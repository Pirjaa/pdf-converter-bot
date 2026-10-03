"""Core converter: PDF <-> Word, PDF -> Markdown, ringkasan via 9router.

Sengaja dipisah dari bot (Discord/Telegram/WA) supaya transport bisa
ganti-ganti tanpa nyentuh logika convert.
"""
from .office import convert_docx_to_pdf, convert_pdf_to_docx, ConvertError
from .markdown import convert_pdf_to_markdown
from .summarize import (
    SummarizeError,
    extract_summary_text,
    summarize_configured,
    summarize_text,
)

__all__ = [
    "convert_docx_to_pdf",
    "convert_pdf_to_docx",
    "convert_pdf_to_markdown",
    "ConvertError",
    "SummarizeError",
    "extract_summary_text",
    "summarize_configured",
    "summarize_text",
]
