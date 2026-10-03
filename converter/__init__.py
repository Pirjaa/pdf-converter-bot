"""Core: PDF <-> Word, PDF -> Markdown, AI summary via 9router, PDF merge/split.

Kept separate from the bot (Discord/Telegram/WA) so the transport can
change without touching the conversion logic.
"""
from .office import convert_docx_to_pdf, convert_pdf_to_docx, ConvertError
from .markdown import convert_pdf_to_markdown
from .pdftools import merge_pdfs, parse_page_spec, pdf_page_count, split_pdf
from .summarize import (
    MODES,
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
    "MODES",
    "SummarizeError",
    "extract_summary_text",
    "merge_pdfs",
    "parse_page_spec",
    "pdf_page_count",
    "split_pdf",
    "summarize_configured",
    "summarize_text",
]
