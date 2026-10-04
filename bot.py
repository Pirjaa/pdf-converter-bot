"""Discord bot: file converter.

Slash commands:
  /toword    PDF -> Word (.docx)
  /topdf     Word (.docx/.doc/.odt/.rtf) -> PDF
  /tomd      PDF -> Markdown (.md)
  /summary   PDF/Word/Markdown -> AI summary (via 9router, pick a style with buttons)
  /mergepdf  Merge up to 5 PDFs into one file
  /splitpdf  Extract pages from a PDF (e.g. "1-3,7,10-12")
  /help      How to use this bot

Usage: run a command, upload the file in the `file` parameter, wait,
and the result is sent back in the same channel.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from converter import (
    MODES,
    ConvertError,
    SummarizeError,
    convert_docx_to_pdf,
    convert_pdf_to_docx,
    convert_pdf_to_markdown,
    extract_summary_text,
    merge_pdfs,
    parse_page_spec,
    parse_page_spec_groups,
    pdf_page_count,
    split_pdf,
    summarize_configured,
    summarize_text,
)

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
# Free Discord upload limit per file. Raise if the server is boosted / Nitro.
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(20 * 1024 * 1024)))
# Max file size to process, so the VPS doesn't run out of RAM/disk.
MAX_PROCESS_BYTES = int(os.getenv("MAX_PROCESS_BYTES", str(100 * 1024 * 1024)))

COMMANDS = {
    "toword": {
        "desc": "Convert PDF to Word (.docx)",
        "label": "PDF to Word",
        "exts": {".pdf"},
        "out_ext": ".docx",
    },
    "topdf": {
        "desc": "Convert Word to PDF",
        "label": "Word to PDF",
        "exts": {".docx", ".doc", ".odt", ".rtf"},
        "out_ext": ".pdf",
    },
    "tomd": {
        "desc": "Convert PDF to Markdown (.md)",
        "label": "PDF to Markdown",
        "exts": {".pdf"},
        "out_ext": ".md",
    },
}


class ConverterBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        for name, cfg in COMMANDS.items():
            self.tree.add_command(_make_command(name, cfg))
        self.tree.add_command(_help_cmd)
        self.tree.add_command(_summary_cmd)
        self.tree.add_command(_mergepdf_cmd)
        self.tree.add_command(_splitpdf_cmd)
        await self.tree.sync()


HELP_TEXT = """📄 Converter Bot — how to use

Commands:
/toword — Convert PDF to Word (.docx)
/topdf — Convert Word to PDF
/tomd — Convert PDF to Markdown (.md)
/summary — Summarize a PDF/Word document with AI (you'll pick a style: Paragraph / Key points / Full)
/mergepdf — Merge up to 5 PDFs into one file
/splitpdf — Extract pages from a PDF, e.g. pages "1-3,7,10-12" (one file or separate files)

How to use: run a command, upload your file in the file parameter, wait a moment, and the result will be sent back in this channel.

Notes:
• Max 20MB per file (Discord's free upload limit)
• PDF to Word conversion isn't pixel-perfect — complex layouts may shift slightly
• Scanned/image PDFs contain no readable text — OCR is not supported yet
• Conversions run one at a time, so please be patient if there's a queue

Found a bug? Please DM the developer."""


@app_commands.command(name="help", description="How to use this bot")
async def _help_cmd(interaction: discord.Interaction) -> None:
    # Ephemeral: only the requester sees it, keeps the channel clean.
    await interaction.response.send_message(HELP_TEXT, ephemeral=True)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _validate_upload(filename: str, size: int, allowed_exts: set[str]) -> str:
    """Return the lowercase extension, or raise ConvertError."""
    ext = Path(filename).suffix.lower()
    if ext not in allowed_exts:
        want = ", ".join(sorted(allowed_exts))
        raise ConvertError(
            f"unsupported format `{ext or '(no extension)'}` — accepted formats: {want}"
        )
    if size > MAX_FILE_BYTES:
        raise ConvertError(
            f"file is {size / 1024 / 1024:.1f} MB — exceeds Discord's "
            f"{MAX_FILE_BYTES / 1024 / 1024:.0f} MB per-file limit"
        )
    if size > MAX_PROCESS_BYTES:
        raise ConvertError(
            "file is too large to process on this server "
            f"(max {MAX_PROCESS_BYTES / 1024 / 1024:.0f} MB)"
        )
    return ext


def _check_output_size(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ConvertError(
            f"the result is {size / 1024 / 1024:.1f} MB — exceeds Discord's "
            f"{MAX_FILE_BYTES / 1024 / 1024:.0f} MB upload limit"
        )


def _rmtree(workdir: Path) -> None:
    for p in workdir.rglob("*"):
        try:
            p.unlink()
        except OSError:
            pass
    try:
        workdir.rmdir()
    except OSError:
        pass


async def _download(attachment: discord.Attachment, dest: Path) -> None:
    dest.write_bytes(await attachment.read())


async def _cleanup_after_send(path: Path) -> None:
    await asyncio.sleep(10)
    try:
        path.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Convert commands (/toword, /topdf, /tomd)
# ---------------------------------------------------------------------------

def _make_command(name: str, cfg: dict) -> app_commands.Command:
    @app_commands.command(name=name, description=cfg["desc"])
    @app_commands.describe(file="File to convert")
    async def _cmd(interaction: discord.Interaction, file: discord.Attachment) -> None:
        await interaction.response.defer(thinking=True)
        try:
            out_path, out_name = await _handle(file, cfg)
            _check_output_size(out_path)
        except ConvertError as e:
            await interaction.followup.send(f"❌ Conversion failed: {e}")
            return
        except Exception:  # noqa: BLE001 - don't leak internals to the user
            await interaction.followup.send(
                "❌ An unexpected error occurred while converting. Please try again."
            )
            return
        await interaction.followup.send(
            content=f"✅ Converted `{file.filename}` ({cfg['label']})",
            file=discord.File(out_path, filename=out_name),
        )
        # Delete the result after sending so /tmp doesn't fill up.
        asyncio.create_task(_cleanup_after_send(out_path))

    return _cmd


async def _handle(file: discord.Attachment, cfg: dict) -> tuple[Path, str]:
    ext = _validate_upload(file.filename, file.size, cfg["exts"])

    workdir = Path(tempfile.mkdtemp(prefix="conv_"))
    try:
        src = workdir / f"input{ext}"
        await _download(file, src)

        out_name = f"{Path(file.filename).stem}{cfg['out_ext']}"
        if cfg["out_ext"] == ".md":
            # blocking -> run in a thread
            text = await asyncio.to_thread(convert_pdf_to_markdown, src)
            final = workdir / out_name
            final.write_text(text, encoding="utf-8")
        elif cfg["out_ext"] == ".docx":
            final = await convert_pdf_to_docx(src, workdir)
        else:
            final = await convert_docx_to_pdf(src, workdir)

        # Move to a clean final name outside workdir (workdir is deleted in finally)
        persist = Path(tempfile.gettempdir()) / f"conv_out_{file.id}_{out_name}"
        if final != persist:
            persist.write_bytes(final.read_bytes())
        return persist, out_name
    finally:
        _rmtree(workdir)


# ---------------------------------------------------------------------------
# /summary
# ---------------------------------------------------------------------------

SUMMARY_EXTS = {".pdf", ".docx", ".md"}

# Document text waiting for a style to be picked: nonce -> text.
# Removed after a button is clicked / the view times out (5 minutes).
_PENDING_SUMMARY: dict[str, str] = {}


class _SummaryView(discord.ui.View):
    """3 summary-style buttons. One click disables all of them
    (guardrail: 1 upload = 1 summary)."""

    def __init__(self, nonce: str, filename: str) -> None:
        super().__init__(timeout=300)
        self.nonce = nonce
        self.filename = filename

    async def on_timeout(self) -> None:
        _PENDING_SUMMARY.pop(self.nonce, None)

    async def _run(self, interaction: discord.Interaction, mode: str) -> None:
        label = MODES[mode]["label"]
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"⏳ Summarizing `{self.filename}` ({label})...",
            view=self,
        )
        text = _PENDING_SUMMARY.pop(self.nonce, None)
        if text is None:
            await interaction.followup.send(
                "❌ This summary session has expired. Please upload the file again."
            )
            return
        try:
            summary, _ = await asyncio.to_thread(summarize_text, text, mode)
        except SummarizeError as e:
            await interaction.followup.send(f"❌ Summarization failed: {e}")
            return
        except Exception:  # noqa: BLE001 - don't leak internals to the user
            await interaction.followup.send(
                "❌ An unexpected error occurred. Please try again."
            )
            return

        # Always send as a .txt file: it previews inline in Discord and
        # sidesteps the 2000-character chat message limit.
        header = f"📝 {label} summary of `{self.filename}`:"
        out_name = f"summary_{Path(self.filename).stem}.txt"
        out = Path(tempfile.gettempdir()) / f"summary_{interaction.id}.txt"
        out.write_text(summary, encoding="utf-8")
        await interaction.followup.send(
            content=header,
            file=discord.File(out, filename=out_name),
        )
        asyncio.create_task(_cleanup_after_send(out))
        self.stop()

    @discord.ui.button(label="Paragraph", emoji="📝", style=discord.ButtonStyle.primary)
    async def _btn_para(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._run(interaction, "paragraph")

    @discord.ui.button(label="Key points", emoji="📌", style=discord.ButtonStyle.primary)
    async def _btn_points(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._run(interaction, "keypoints")

    @discord.ui.button(label="Full", emoji="📋", style=discord.ButtonStyle.primary)
    async def _btn_full(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._run(interaction, "full")


@app_commands.command(name="summary", description="Summarize a document with AI (via 9router)")
@app_commands.describe(file="PDF/Word/Markdown file to summarize")
async def _summary_cmd(interaction: discord.Interaction, file: discord.Attachment) -> None:
    await interaction.response.defer(thinking=True)
    if not summarize_configured():
        await interaction.followup.send(
            "❌ The /summary feature is not configured yet (missing NINE_ROUTER_API_KEY)."
        )
        return
    try:
        text = await _extract_for_summary(file)
    except SummarizeError as e:
        await interaction.followup.send(f"❌ Summarization failed: {e}")
        return
    except Exception:  # noqa: BLE001 - don't leak internals to the user
        await interaction.followup.send(
            "❌ An unexpected error occurred. Please try again."
        )
        return

    nonce = secrets.token_hex(8)
    _PENDING_SUMMARY[nonce] = text
    await interaction.followup.send(
        content=f"📄 `{file.filename}` is ready. Choose a summary style:",
        view=_SummaryView(nonce, file.filename),
    )


async def _extract_for_summary(file: discord.Attachment) -> str:
    ext = _validate_upload(file.filename, file.size, SUMMARY_EXTS)
    workdir = Path(tempfile.mkdtemp(prefix="summary_"))
    try:
        src = workdir / f"input{ext}"
        await _download(file, src)
        # blocking (markitdown) -> run in a thread
        return await asyncio.to_thread(extract_summary_text, src, ext)
    finally:
        _rmtree(workdir)


# ---------------------------------------------------------------------------
# /mergepdf and /splitpdf
# ---------------------------------------------------------------------------

@app_commands.command(name="mergepdf", description="Merge up to 5 PDFs into one file")
@app_commands.describe(
    file1="First PDF",
    file2="Second PDF (optional)",
    file3="Third PDF (optional)",
    file4="Fourth PDF (optional)",
    file5="Fifth PDF (optional)",
)
async def _mergepdf_cmd(
    interaction: discord.Interaction,
    file1: discord.Attachment,
    file2: discord.Attachment | None = None,
    file3: discord.Attachment | None = None,
    file4: discord.Attachment | None = None,
    file5: discord.Attachment | None = None,
) -> None:
    await interaction.response.defer(thinking=True)
    files = [f for f in (file1, file2, file3, file4, file5) if f is not None]
    try:
        for f in files:
            _validate_upload(f.filename, f.size, {".pdf"})
        workdir = Path(tempfile.mkdtemp(prefix="merge_"))
        try:
            srcs = []
            for i, f in enumerate(files):
                src = workdir / f"input{i}.pdf"
                await _download(f, src)
                srcs.append(src)
            out_name = f"merged_{Path(file1.filename).stem}.pdf"
            out = await asyncio.to_thread(merge_pdfs, srcs, workdir / out_name)
            persist = Path(tempfile.gettempdir()) / f"merge_out_{interaction.id}_{out_name}"
            shutil.copy(out, persist)
            _check_output_size(persist)
        finally:
            _rmtree(workdir)
    except ConvertError as e:
        await interaction.followup.send(f"❌ Merge failed: {e}")
        return
    except Exception:  # noqa: BLE001 - don't leak internals to the user
        await interaction.followup.send(
            "❌ An unexpected error occurred. Please try again."
        )
        return
    await interaction.followup.send(
        content=f"✅ Merged {len(files)} PDF(s) into `{out_name}`",
        file=discord.File(persist, filename=out_name),
    )
    asyncio.create_task(_cleanup_after_send(persist))


@app_commands.command(name="splitpdf", description="Extract pages from a PDF")
@app_commands.describe(
    file="PDF file",
    pages='Pages to extract, e.g. "1-3,7,10-12"',
    mode="Single file merges everything into one PDF; Separate files creates one PDF per comma-group",
)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="Single file", value="single"),
        app_commands.Choice(name="Separate files", value="separate"),
    ]
)
async def _splitpdf_cmd(
    interaction: discord.Interaction,
    file: discord.Attachment,
    pages: str,
    mode: str = "single",
) -> None:
    await interaction.response.defer(thinking=True)
    try:
        _validate_upload(file.filename, file.size, {".pdf"})
        workdir = Path(tempfile.mkdtemp(prefix="split_"))
        jobs: list[tuple[list[int], str]] = []
        sent: list[tuple[Path, str]] = []
        try:
            src = workdir / "input.pdf"
            await _download(file, src)
            page_count = await asyncio.to_thread(pdf_page_count, src)
            stem = Path(file.filename).stem
            try:
                if mode == "separate":
                    groups = parse_page_spec_groups(pages, page_count)
                    raw_groups = [g.strip() for g in pages.split(",") if g.strip()]
                    jobs = [
                        (sel, f"{stem}_pages_{raw.replace(' ', '')}.pdf")
                        for sel, raw in zip(groups, raw_groups)
                    ]
                else:
                    selected = parse_page_spec(pages, page_count)
                    safe = pages.replace(" ", "").replace(",", "_")
                    jobs = [(selected, f"{stem}_pages_{safe}.pdf")]
            except ValueError as e:
                raise ConvertError(f"invalid page selection: {e}")
            for selected, out_name in jobs:
                out = await asyncio.to_thread(split_pdf, src, selected, workdir / out_name)
                persist = Path(tempfile.gettempdir()) / f"split_out_{interaction.id}_{out_name}"
                shutil.copy(out, persist)
                _check_output_size(persist)
                sent.append((persist, out_name))
        finally:
            _rmtree(workdir)
    except ConvertError as e:
        await interaction.followup.send(f"❌ Split failed: {e}")
        return
    except Exception:  # noqa: BLE001 - don't leak internals to the user
        await interaction.followup.send(
            "❌ An unexpected error occurred. Please try again."
        )
        return
    total = sum(len(sel) for sel, _ in jobs)
    if len(sent) == 1:
        content = f"✅ Extracted {total} page(s) from `{file.filename}`"
    else:
        content = f"✅ Split `{file.filename}` into {len(sent)} PDFs ({total} pages total)"
    await interaction.followup.send(
        content=content,
        files=[discord.File(path, filename=name) for path, name in sent],
    )
    for path, _ in sent:
        asyncio.create_task(_cleanup_after_send(path))


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "DISCORD_TOKEN is not set (fill in .env first, see .env.example)"
        )
    ConverterBot().run(TOKEN)


if __name__ == "__main__":
    main()
