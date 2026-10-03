"""Ringkasan dokumen via 9router (API OpenAI-compatible).

Config via env (isi di .env):
  NINE_ROUTER_BASE_URL  default https://router.joysi.my.id/v1
  NINE_ROUTER_API_KEY   wajib diisi
  NINE_ROUTER_MODEL     default kr/claude-sonnet-4.5

Env dibaca lazy (di dalam fungsi), karena modul ini di-import sebelum
load_dotenv() jalan di bot.py.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

MAX_INPUT_CHARS = 12_000
TIMEOUT_S = 120

SYSTEM_PROMPT = (
    "Kamu meringkas dokumen untuk user Indonesia. Balas HANYA dengan ringkasan "
    "dalam Bahasa Indonesia yang santai dan jelas: 3-8 bullet point berisi "
    "poin-poin penting, lalu satu paragraf kesimpulan maksimal 2 kalimat. "
    "Total maksimal 1500 karakter. Jangan mengarang fakta yang tidak ada di dokumen. "
    "Jika dokumen bukan bahasa Indonesia, tetap ringkas dalam Bahasa Indonesia."
)


class SummarizeError(Exception):
    """Error ringkasan yang aman ditampilkan ke user."""


def _cfg() -> tuple[str, str, str]:
    base = os.getenv("NINE_ROUTER_BASE_URL", "https://router.joysi.my.id/v1").rstrip("/")
    key = os.getenv("NINE_ROUTER_API_KEY", "")
    model = os.getenv("NINE_ROUTER_MODEL", "kr/claude-sonnet-4.5")
    return base, key, model


def summarize_configured() -> bool:
    return bool(_cfg()[1])


def extract_summary_text(src: Path, ext: str) -> str:
    """Ambil teks mentah dari pdf/docx/md buat bahan ringkasan."""
    if ext == ".md":
        text = src.read_text(encoding="utf-8", errors="replace")
    else:
        from markitdown import MarkItDown

        result = MarkItDown().convert(str(src))
        text = result.text_content or ""
    text = text.strip()
    if not text:
        raise SummarizeError(
            "teksnya kosong — PDF hasil scan butuh OCR dulu, belum gua pasang, masih malas"
        )
    return text


def summarize_text(text: str) -> str:
    """Kirim teks ke 9router, kembalikan ringkasan (blocking, lempar ke thread)."""
    base_url, api_key, model = _cfg()
    if not api_key:
        raise SummarizeError("not_configured")

    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS] + "\n\n[dokumen dipotong karena kepanjangan]"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Ringkas dokumen berikut:\n\n{text}"},
        ],
        "temperature": 0.3,
        "max_tokens": 800,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SummarizeError("API key 9router salah / tidak valid") from e
        raise SummarizeError(f"9router error {e.code}") from e
    except urllib.error.URLError as e:
        raise SummarizeError("9router nggak bisa dihubungi") from e
    except TimeoutError as e:
        raise SummarizeError("9router lama banget responnya") from e

    try:
        summary = payload["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError, TypeError) as e:
        raise SummarizeError("9router ngasih respon kosong") from e
    if not summary:
        raise SummarizeError("9router ngasih respon kosong")
    return summary
