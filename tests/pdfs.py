"""Small invoices made for a test: a digital PDF with text set where the test says (the rule reader's fixtures)."""

from __future__ import annotations

from pathlib import Path


def text_pdf(where: Path, items: list[tuple], name: str = "inv.pdf", size: float = 10, pages: int = 1) -> Path:
    """A letter-size PDF with each item's text set at (x, y) points from the top left: ``(x, y, text)``,
    ``(x, y, text, font size)`` or ``(x, y, text, font size, page)`` (pages from 1; ``pages`` of them in all).
    ``where`` is a folder (the PDF is ``where / name``) or the PDF's own path (ending in .pdf)."""
    import pymupdf

    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    for x, y, text, *more in items:
        page = doc[more[1] - 1] if len(more) > 1 else doc[0]
        page.insert_text((x, y), text, fontsize=more[0] if more else size)
    path = where if where.suffix.lower() == ".pdf" else where / name
    doc.save(path)
    doc.close()
    return path


def pdf_bytes(*lines: str, size: float = 10) -> bytes:
    """A one-page PDF of these lines, one under the other, as bytes (an email attachment)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    for n, line in enumerate(lines):
        page.insert_text((50, 60 + 16 * n), line, fontsize=size)
    out = doc.tobytes()
    doc.close()
    return out
