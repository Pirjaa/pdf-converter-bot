"""PDF -> Markdown.

Utama: MarkItDown (Microsoft). Fallback: PyMuPDF ekstraksi teks mentah
kalau MarkItDown tidak terinstall.
"""
from __future__ import annotations

from pathlib import Path


def convert_pdf_to_markdown(src: Path) -> str:
    """Kembalikan isi markdown sebagai string (bukan file)."""
    try:
        from markitdown import MarkItDown

        result = MarkItDown().convert(str(src))
        text = result.text_content or ""
        if text.strip():
            return text
        # MarkItDown tidak menemukan teks -> kemungkinan PDF hasil scan
        raise ValueError("tidak ada teks terdeteksi (PDF hasil scan butuh OCR dulu)")
    except ImportError:
        pass

    # Fallback: PyMuPDF
    try:
        import fitz
    except ImportError as e:
        raise ImportError("install salah satu: pip install markitdown  ATAU  pip install pymupdf") from e

    parts: list[str] = []
    with fitz.open(src) as doc:
        for i, page in enumerate(doc, start=1):
            t = page.get_text().strip()
            if t:
                parts.append(f"<!-- halaman {i} -->\n\n{t}")
    text = "\n\n".join(parts).strip()
    if not text:
        raise ValueError("tidak ada teks terdeteksi (PDF hasil scan butuh OCR dulu)")
    return text
