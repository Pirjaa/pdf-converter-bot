"""Discord bot: file converter.

Slash commands:
  /toword  PDF -> Word (.docx)
  /topdf   Word (.docx/.doc/.odt/.rtf) -> PDF
  /tomd    PDF -> Markdown (.md)
  /summary PDF/Word/Markdown -> ringkasan AI (via 9router, pilih mode via tombol)
  /help    bantuan cara pakai

Cara pakai: ketik command, upload file di parameter `file`, tunggu,
hasil convert dikirim balik di channel yang sama.
"""
from __future__ import annotations

import asyncio
import os
import secrets
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
    summarize_configured,
    summarize_text,
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
        self.tree.add_command(_help_cmd)
        self.tree.add_command(_summary_cmd)
        await self.tree.sync()


HELP_TEXT = """🤖 Gua bisa convert file, ini command-nya:

/toword — PDF ke Word (.docx)
/topdf — Word ke PDF
/tomd — PDF ke Markdown (.md)
/summary — ringkas isi PDF/Word pakai AI (ntar pilih: paragraf / keypoints / lengkap)

Cara pakai: ketik salah satu command di atas, upload file-nya,
tunggu bentar, ntar gua kirim balik hasilnya di sini.

Catatan:
• Maks 20MB per file — minimal beliin nitro kalau mau upload lebih gede :)
• PDF ke Word nggak pixel-perfect, layout rumit bisa geser dikit
• PDF hasil scan (foto) nggak kebaca teksnya — butuh OCR, belum gua pasang, masih malas
• Convert jalan satu-satu, kalau antre sabar ya

Kalau ngebug dm aja yang ngoding."""


@app_commands.command(name="help", description="Bantuan cara pakai bot ini")
async def _help_cmd(interaction: discord.Interaction) -> None:
    # ephemeral: cuma yang ngetik yang bisa liat, biar nggak ngespam channel
    await interaction.response.send_message(HELP_TEXT, ephemeral=True)


SUMMARY_EXTS = {".pdf", ".docx", ".md"}

# Teks dokumen yang nunggu dipilih modenya: nonce -> teks.
# Dihapus setelah tombol diklik / view timeout (5 menit).
_PENDING_SUMMARY: dict[str, str] = {}


class _SummaryView(discord.ui.View):
    """3 tombol mode ringkasan. Sekali klik langsung disable semua
    (guardrail: 1x upload = 1x ringkas)."""

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
            content=f"⏳ lagi ngeringkas `{self.filename}` ({label})...",
            view=self,
        )
        text = _PENDING_SUMMARY.pop(self.nonce, None)
        if text is None:
            await interaction.followup.send(
                "❌ sesi ringkasannya udah kedaluwarsa, upload ulang gih"
            )
            return
        try:
            summary, _ = await asyncio.to_thread(summarize_text, text, mode)
        except SummarizeError as e:
            await interaction.followup.send(f"❌ Gagal meringkas: {e}")
            return
        except Exception:  # noqa: BLE001 - jangan bocorin detail internal ke user
            await interaction.followup.send(
                "❌ sorry ini kayanya yg ngoding bodoh dah, coba lagi"
            )
            return

        header = f"📝 Ringkasan {label} `{self.filename}`:"
        if len(header) + len(summary) + 2 <= 2000:
            await interaction.followup.send(f"{header}\n\n{summary}")
            return
        # Kepanjangan buat chat -> kirim sebagai file .md
        out = Path(tempfile.gettempdir()) / f"summary_{interaction.id}.md"
        out.write_text(
            f"# Ringkasan {label} — {self.filename}\n\n{summary}",
            encoding="utf-8",
        )
        await interaction.followup.send(
            content=f"{header} kepanjangan buat chat, nih file-nya:",
            file=discord.File(out, filename=out.name),
        )
        asyncio.create_task(_cleanup_after_send(out))
        self.stop()

    @discord.ui.button(label="Paragraf", emoji="📝", style=discord.ButtonStyle.primary)
    async def _btn_para(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._run(interaction, "paragraf")

    @discord.ui.button(label="Keypoints", emoji="📌", style=discord.ButtonStyle.primary)
    async def _btn_points(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._run(interaction, "keypoints")

    @discord.ui.button(label="Lengkap", emoji="📋", style=discord.ButtonStyle.primary)
    async def _btn_full(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._run(interaction, "lengkap")


@app_commands.command(name="summary", description="Ringkas isi dokumen pakai AI (via 9router)")
@app_commands.describe(file="File PDF/Word/Markdown yang mau diringkas")
async def _summary_cmd(interaction: discord.Interaction, file: discord.Attachment) -> None:
    await interaction.response.defer(thinking=True)
    if not summarize_configured():
        await interaction.followup.send(
            "❌ fitur /summary belum disetting — suruh yang ngoding isi NINE_ROUTER_API_KEY dulu gih"
        )
        return
    try:
        text = await _extract_for_summary(file)
    except SummarizeError as e:
        await interaction.followup.send(f"❌ Gagal meringkas: {e}")
        return
    except Exception:  # noqa: BLE001 - jangan bocorin detail internal ke user
        await interaction.followup.send(
            "❌ sorry ini kayanya yg ngoding bodoh dah, coba lagi"
        )
        return

    nonce = secrets.token_hex(8)
    _PENDING_SUMMARY[nonce] = text
    await interaction.followup.send(
        content=f"📄 `{file.filename}` siap diringkas. Mau yang model gimana?",
        view=_SummaryView(nonce, file.filename),
    )


async def _extract_for_summary(file: discord.Attachment) -> str:
    ext = Path(file.filename).suffix.lower()
    if ext not in SUMMARY_EXTS:
        raise SummarizeError(
            f"yang bener ajalah, masa mau meringkas `{ext or '(tanpa ekstensi)'}`"
        )
    if file.size > MAX_FILE_BYTES:
        raise SummarizeError(
            f"minimal beliin nitro kalau mau upload "
            f"{file.size / 1024 / 1024:.1f} MB :)"
        )
    if file.size > MAX_PROCESS_BYTES:
        raise SummarizeError(
            f"buset {file.size / 1024 / 1024:.1f} MB, server gua kentang — "
            f"maks {MAX_PROCESS_BYTES / 1024 / 1024:.0f} MB ya"
        )

    workdir = Path(tempfile.mkdtemp(prefix="ringkas_"))
    try:
        src = workdir / f"input{ext}"
        src.write_bytes(await file.read())
        # blocking (markitdown) -> lempar ke thread
        return await asyncio.to_thread(extract_summary_text, src, ext)
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
