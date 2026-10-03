"""Konversi dokumen.

- Word -> PDF : LibreOffice headless (Writer -> PDF, solid).
- PDF -> Word : pdf2docx (LibreOffice tidak bisa: PDF dibuka di Draw dan
  Draw tidak punya export filter ke DOCX).

Catatan jujur: PDF -> Word itu lossy secara inheren (PDF menyimpan posisi
teks per baris, bukan struktur dokumen). Hasilnya cukup buat dokumen teks
rapi, tapi layout kompleks bisa geser. Jangan janjiin pixel-perfect ke user.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

SOFFICE = shutil.which("soffice") or "soffice"

# LibreOffice tidak tahan diparalel -> satu lock global untuk convert via soffice.
_soffice_lock = asyncio.Lock()


class ConvertError(Exception):
    pass


async def _soffice_convert(src: Path, outdir: Path, fmt: str, timeout: int = 180) -> Path:
    """Jalankan `soffice --headless --convert-to fmt`, kembalikan path hasil."""
    async with _soffice_lock:
        proc = await asyncio.create_subprocess_exec(
            SOFFICE, "--headless", "--convert-to", fmt,
            "--outdir", str(outdir), str(src),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ConvertError(f"converting {src.name} timed out ({timeout}s) — the file may be corrupted or too heavy")

        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", "replace")[-500:]
            raise ConvertError(f"LibreOffice failed to convert {src.name}: {err or 'unknown error'}")

    candidates = list(outdir.glob(f"{src.stem}.{fmt}")) or list(outdir.glob(f"{src.stem}.*"))
    outputs = [p for p in candidates if p.suffix.lower() == f".{fmt}" and p != src]
    if not outputs:
        raise ConvertError(f"output file not found after converting {src.name}")
    return outputs[0]


async def convert_docx_to_pdf(src: Path, outdir: Path, timeout: int = 180) -> Path:
    """Word (.docx/.doc/.odt/.rtf) -> PDF via LibreOffice."""
    return await _soffice_convert(src, outdir, "pdf", timeout)


async def convert_pdf_to_docx(src: Path, outdir: Path, timeout: int = 300) -> Path:
    """PDF -> Word (.docx) via pdf2docx. Blocking -> dijalankan di thread."""
    def _run() -> Path:
        from pdf2docx import Converter

        out = outdir / f"{src.stem}.docx"
        cv = Converter(str(src))
        try:
            cv.convert(str(out))
        finally:
            cv.close()
        return out

    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout)
    except asyncio.TimeoutError:
        raise ConvertError(f"converting {src.name} timed out ({timeout}s) — the file may be corrupted or too heavy")
    except ImportError as e:
        raise ConvertError("pdf2docx is not installed: pip install pdf2docx") from e
