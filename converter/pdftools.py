"""Merge and split PDFs (pypdf)."""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter


def pdf_page_count(src: Path) -> int:
    return len(PdfReader(str(src)).pages)


def merge_pdfs(srcs: list[Path], dest: Path) -> Path:
    """Concatenate PDFs in order into dest."""
    writer = PdfWriter()
    for src in srcs:
        for page in PdfReader(str(src)).pages:
            writer.add_page(page)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        writer.write(f)
    return dest


def split_pdf(src: Path, pages: list[int], dest: Path) -> Path:
    """Extract 0-based page indexes into dest."""
    reader = PdfReader(str(src))
    writer = PdfWriter()
    for p in pages:
        writer.add_page(reader.pages[p])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        writer.write(f)
    return dest


def parse_page_spec(spec: str, page_count: int) -> list[int]:
    """Parse "1-3,7,10-12" (1-based, human-friendly) into a deduped list of
    0-based page indexes. Raises ValueError with a user-safe message."""
    if not spec or not spec.strip():
        raise ValueError('page selection is empty — e.g. "1-3,7,10-12"')
    pages: list[int] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            parts = token.split("-")
            if (
                len(parts) != 2
                or not parts[0].strip().isdigit()
                or not parts[1].strip().isdigit()
            ):
                raise ValueError(f'"{token}" is not a valid range — use e.g. "1-3"')
            start, end = int(parts[0]), int(parts[1])
            if start < 1 or end < 1:
                raise ValueError(f'"{token}": page numbers start at 1')
            if start > end:
                raise ValueError(f'"{token}": range start is greater than its end')
            if end > page_count:
                raise ValueError(
                    f"page {end} is out of range — this PDF has {page_count} pages"
                )
            pages.extend(range(start - 1, end))
        else:
            if not token.isdigit():
                raise ValueError(
                    f'"{token}" is not a page number — use e.g. "1-3,7,10-12"'
                )
            n = int(token)
            if n < 1:
                raise ValueError(f'"{token}": page numbers start at 1')
            if n > page_count:
                raise ValueError(
                    f"page {n} is out of range — this PDF has {page_count} pages"
                )
            pages.append(n - 1)
    if not pages:
        raise ValueError('no pages selected — e.g. "1-3,7,10-12"')
    seen: set[int] = set()
    return [p for p in pages if not (p in seen or seen.add(p))]


def parse_page_spec_groups(spec: str, page_count: int) -> list[list[int]]:
    """Split "1-2,3" into per-comma-group page lists: [[0, 1], [2]].

    Used for "separate files" split mode — each comma group becomes its
    own output PDF. Raises ValueError with a user-safe message.
    """
    groups: list[list[int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        groups.append(parse_page_spec(part, page_count))
    if not groups:
        raise ValueError('no pages selected — e.g. "1-3,7,10-12"')
    return groups
