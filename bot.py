"""Discord bot: file converter.

Slash commands:
  /toword  PDF -> Word (.docx)
  /topdf   Word (.docx/.doc/.odt/.rtf) -> PDF
  /tomd    PDF -> Markdown (.md)

Cara pakai: ketik command, upload file di parameter `file`, tunggu,
hasil convert dikirim balik di channel yang sama.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from converter import (
    ConvertError,
    convert_docx_to_pdf,
    convert_pdf_to_docx,
    convert_pdf_to_markdown,
)

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
# Limit upload Discord akun gratis (per file). Naikkan kalau server di-boost / Nitro.
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(20 * 1024 * 1024)))
# Batas ukuran file yang mau diproses, biar VPS tidak kehabisan RAM/disk.
MAX_PROCESS_BYTES = int(os.getenv("MAX_PROCESS_BYTES", str(100 * 1024 * 1024)))

COMMANDS = {
    "toword": {
        "desc": "Convert PDF jadi Word (.docx)",
        "label": "PDF ke Word",
        "target": "Word",
        "exts": {".pdf"},
        "out_ext": ".docx",
    },
    "topdf": {
        "desc": "Convert Word jadi PDF",
        "label": "Word ke PDF",
        "target": "PDF",
        "exts": {".docx", ".doc", ".odt", ".rtf"},
        "out_ext": ".pdf",
    },
    "tomd": {
        "desc": "Convert PDF jadi Markdown (.md)",
        "label": "PDF ke Markdown",
        "target": "Markdown",
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
        await self.tree.sync()


def _make_command(name: str, cfg: dict) -> app_commands.Command:
    @app_commands.command(name=name, description=cfg["desc"])
    @app_commands.describe(file="File yang mau di-convert")
    async def _cmd(interaction: discord.Interaction, file: discord.Attachment) -> None:
        await interaction.response.defer(thinking=True)
        try:
            out_path, out_name = await _handle(file, cfg)
        except ConvertError as e:
            await interaction.followup.send(f"❌ Gagal convert: {e}")
            return
        except Exception:  # noqa: BLE001 - jangan bocorin detail internal ke user
            await interaction.followup.send(
                "❌ sorry ini kayanya yg ngoding bodoh dah, coba lagi"
            )
            return
        await interaction.followup.send(
            content=f"✅ nih udah gua convertin `{file.filename}` dari {cfg['label']}",
            file=discord.File(out_path, filename=out_name),
        )
        # Hapus file hasil setelah terkirim, biar /tmp tidak penuh.
        asyncio.create_task(_cleanup_after_send(out_path))

    return _cmd


async def _handle(file: discord.Attachment, cfg: dict) -> tuple[Path, str]:
    ext = Path(file.filename).suffix.lower()
    if ext not in cfg["exts"]:
        raise ConvertError(
            f"yang bener ajalah, masa mau convert `{ext or '(tanpa ekstensi)'}` "
            f"ke {cfg['target']}"
        )
    if file.size > MAX_FILE_BYTES:
        raise ConvertError(
            f"minimal beliin nitro kalau mau upload "
            f"{file.size / 1024 / 1024:.1f} MB :)"
        )
    if file.size > MAX_PROCESS_BYTES:
        raise ConvertError(
            f"buset {file.size / 1024 / 1024:.1f} MB, server gua kentang — "
            f"maks {MAX_PROCESS_BYTES / 1024 / 1024:.0f} MB ya"
        )

    workdir = Path(tempfile.mkdtemp(prefix="conv_"))
    try:
        src = workdir / f"input{ext}"
        src.write_bytes(await file.read())

        out_name = f"{Path(file.filename).stem}{cfg['out_ext']}"
        if cfg["out_ext"] == ".md":
            # blocking -> lempar ke thread
            text = await asyncio.to_thread(convert_pdf_to_markdown, src)
            final = workdir / out_name
            final.write_text(text, encoding="utf-8")
        elif cfg["out_ext"] == ".docx":
            final = await convert_pdf_to_docx(src, workdir)
        else:
            final = await convert_docx_to_pdf(src, workdir)

        # Pindahkan ke nama final yang rapi di luar workdir (workdir dihapus di finally)
        persist = Path(tempfile.gettempdir()) / f"conv_out_{file.id}_{out_name}"
        if final != persist:
            persist.write_bytes(final.read_bytes())
        return persist, out_name
    finally:
        for p in workdir.rglob("*"):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            workdir.rmdir()
        except OSError:
            pass


async def _cleanup_after_send(path: Path) -> None:
    await asyncio.sleep(10)
    try:
        path.unlink()
    except OSError:
        pass


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "DISCORD_TOKEN belum di-set (isi file .env dulu, contoh di .env.example)"
        )
    ConverterBot().run(TOKEN)


if __name__ == "__main__":
    main()
