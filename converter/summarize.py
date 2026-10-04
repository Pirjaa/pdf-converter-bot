"""Document summarization via 9router (OpenAI-compatible API).

Config via env (set in .env):
  NINE_ROUTER_BASE_URL  default https://router.joysi.my.id/v1
  NINE_ROUTER_API_KEY   required
  NINE_ROUTER_MODEL     default ag/gemini-3.8-flash-high (Antigravity provider)
  SUMMARY_MAX_INPUT_CHARS  default 60000

Env vars are read lazily (inside functions) because this module is
imported before load_dotenv() runs in bot.py.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT_S = 120


def _max_input_chars() -> int:
    return int(os.getenv("SUMMARY_MAX_INPUT_CHARS", "60000"))


MODES = {
    "paragraph": {
        "label": "Paragraph",
        "max_tokens": 1000,
        "system": (
            "You are summarizing a document. Reply ONLY with ONE dense, "
            "comprehensive summary paragraph in the SAME language as the document, "
            "about 1000-2000 characters. Cover: the main topic, the key points, and "
            "the conclusion. Do not invent facts that are not in the document."
        ),
    },
    "keypoints": {
        "label": "Key points",
        "max_tokens": 1500,
        "system": (
            "You are summarizing a document. Write a DETAILED and thorough "
            "summary, NOT a brief one. Reply ONLY with 12-20 bullet points "
            "(use •) in the SAME language as the document. Each point should be 2-3 "
            "substantive sentences — explain the reasoning, examples, or "
            "implications, not just dry one-liners. Follow the document's flow. "
            "Do not invent facts that are not in the document."
        ),
    },
    "full": {
        "label": "Full (key points + paragraph)",
        "max_tokens": 3000,
        "system": (
            "You are summarizing a document. Write a DETAILED and thorough "
            "summary, NOT a brief one — the user wants to truly understand the "
            "document without reading it. Write the summary in the SAME language "
            "as the document.\n"
            "Follow the document's section structure. For each important section, "
            "write a sub-heading (### Section Name format), then 4-8 bullet points "
            "(use •), each 2-3 substantive sentences: explain the reasoning, "
            "examples, or implications.\n"
            "End with ### Conclusion containing one overall assessment paragraph "
            "(4-6 sentences).\n"
            "Do not invent facts that are not in the document."
        ),
    },
}


class SummarizeError(Exception):
    """Summarization error that is safe to show to the user."""


def _cfg() -> tuple[str, str, str]:
    base = os.getenv("NINE_ROUTER_BASE_URL", "https://router.joysi.my.id/v1").rstrip("/")
    key = os.getenv("NINE_ROUTER_API_KEY", "")
    model = os.getenv("NINE_ROUTER_MODEL", "ag/gemini-3.8-flash-high")
    return base, key, model


def summarize_configured() -> bool:
    return bool(_cfg()[1])


def extract_summary_text(src: Path, ext: str) -> str:
    """Extract raw text from pdf/docx/md as summarization input."""
    if ext == ".md":
        text = src.read_text(encoding="utf-8", errors="replace")
    else:
        from markitdown import MarkItDown

        result = MarkItDown().convert(str(src))
        text = result.text_content or ""
    text = text.strip()
    if not text:
        raise SummarizeError(
            "no readable text found — scanned PDFs require OCR, which is not supported yet"
        )
    return text


def summarize_text(text: str, mode: str = "full") -> tuple[str, dict]:
    """Send text to 9router.

    Returns (summary, usage) with usage = {"prompt": int, "completion": int,
    "total": int}. Blocking — run in a thread.
    """
    if mode not in MODES:
        raise SummarizeError(f"unknown mode `{mode}`")
    base_url, api_key, model = _cfg()
    if not api_key:
        raise SummarizeError("not_configured")

    max_chars = _max_input_chars()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[document truncated: too long]"

    cfg = MODES[mode]
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": cfg["system"]},
            {"role": "user", "content": f"Summarize the following document:\n\n{text}"},
        ],
        "temperature": 0.3,
        "max_tokens": cfg["max_tokens"],
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            # Cloudflare in front of 9router blocks the default
            # python-urllib User-Agent (error 1010), so send our own.
            "User-Agent": "converter-bot/1.0",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:200].strip()
        if e.code == 401:
            raise SummarizeError("invalid 9router API key") from e
        if e.code == 403:
            raise SummarizeError(
                f"request rejected by 9router/Cloudflare (403){': ' + detail if detail else ''}"
            ) from e
        if e.code == 404:
            raise SummarizeError(
                "9router model not found — check NINE_ROUTER_MODEL and the provider"
            ) from e
        raise SummarizeError(
            f"9router error {e.code}{': ' + detail if detail else ''}"
        ) from e
    except urllib.error.URLError as e:
        raise SummarizeError("could not reach 9router") from e
    except TimeoutError as e:
        raise SummarizeError("9router took too long to respond") from e

    try:
        summary = payload["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError, TypeError) as e:
        raise SummarizeError("9router returned an empty response") from e
    if not summary:
        raise SummarizeError("9router returned an empty response")

    usage = payload.get("usage") or {}
    usage_info = {
        "prompt": int(usage.get("prompt_tokens") or 0),
        "completion": int(usage.get("completion_tokens") or 0),
        "total": int(usage.get("total_tokens") or 0),
    }
    return summary, usage_info
