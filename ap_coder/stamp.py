"""Approval-stamped PDFs: the invoice as received, with an APPROVED stamp on its first page and a coding
page added at the end (GL lines, tax, approvals). Made to attach to the ERP entry or to file.

PDFs keep their pages; an image invoice becomes a one-page PDF; a text invoice (or a missing file) gives
the coding page alone.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from .extraction import DOCUMENT_EXTENSIONS

GREEN, INK, MUTED, LINE = (0.07, 0.55, 0.33), (0.08, 0.13, 0.2), (0.36, 0.39, 0.45), (0.85, 0.88, 0.92)
FONT, BOLD = "helv", "hebo"
PAGE_W, PAGE_H, MARGIN = 612, 792, 48  # US Letter, points


def _when(iso: str | None) -> str:
    return (iso or "")[:16].replace("T", " ")


def _fit(text: Any, chars: int) -> str:
    text = " ".join(str(text if text is not None else "").split())
    return text if len(text) <= chars else text[: chars - 3].rstrip() + "..."  # the built-in font has no "…"


def _source_document(path: Path) -> Any:
    import pymupdf

    suffix = path.suffix.lower()
    try:
        if path.is_file() and suffix == ".pdf":
            return pymupdf.open(path)
        if path.is_file() and suffix in DOCUMENT_EXTENSIONS:
            with pymupdf.open(path) as image:
                return pymupdf.open("pdf", image.convert_to_pdf())
    except Exception:  # a damaged or unreadable file: the coding page alone
        pass
    return pymupdf.open()


def _stamp(page: Any, inv: dict[str, Any]) -> None:
    import pymupdf

    lines = [f"By {inv.get('reviewer') or '?'}  {_when(inv.get('reviewed_at'))}"]
    if inv.get("second_reviewer"):
        lines.append(f"2nd {inv['second_reviewer']}  {_when(inv.get('second_reviewed_at'))}")
    ref = f"AP Coder #{inv['id']}" + (f" · ERP batch {inv['export_batch']}" if inv.get("export_batch") else "")
    lines.append(ref)
    width, height = 210, 26 + 11 * len(lines)
    box = page.rect
    rect = pymupdf.Rect(box.x1 - width - 18, box.y0 + 18, box.x1 - 18, box.y0 + 18 + height)
    page.draw_rect(rect, color=GREEN, width=1.6, overlay=True)  # no fill: never hides what is printed there
    page.insert_text((rect.x0 + 9, rect.y0 + 17), "APPROVED", fontname=BOLD, fontsize=12, color=GREEN)
    for n, text in enumerate(lines):
        page.insert_text((rect.x0 + 9, rect.y0 + 31 + 11 * n), _fit(text, 44), fontname=FONT, fontsize=7.5, color=INK)


def _coding_pages(doc: Any, inv: dict[str, Any], gl_names: dict[str, str]) -> None:
    import pymupdf

    final = inv.get("final_output") or {}
    currency = final.get("currency") or ""
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN

    def text(x: float, value: str, size: float = 9, font: str = FONT, color: tuple = INK) -> None:
        page.insert_text((x, y), value, fontname=font, fontsize=size, color=color)

    def right(value: str, size: float, font: str) -> None:
        width = pymupdf.get_text_length(value, fontname=font, fontsize=size)
        page.insert_text((PAGE_W - MARGIN - width, y), value, fontname=font, fontsize=size, color=INK)

    text(MARGIN, "Coding", 18, BOLD)
    y += 18
    text(MARGIN, _fit(f"{final.get('vendor_name') or ''} · invoice {final.get('invoice_number') or ''}", 90), 11)
    y += 22
    facts = [
        ("Invoice date", final.get("invoice_date")), ("Due date", final.get("due_date") or "—"),
        ("PO", final.get("po_number") or "—"), ("Terms", final.get("payment_terms") or "—"),
        ("Total", f"{float(final.get('grand_total') or 0):,.2f} {currency}"),
        ("Approved", f"{inv.get('reviewer') or ''} {_when(inv.get('reviewed_at'))}"),
    ]  # fmt: skip
    if inv.get("second_reviewer"):
        facts.append(("Second approval", f"{inv['second_reviewer']} {_when(inv.get('second_reviewed_at'))}"))
    for label, value in facts:
        text(MARGIN, label, 8.5, color=MUTED)
        text(MARGIN + 95, _fit(value, 80), 9)
        y += 13
    y += 12
    columns = [(MARGIN, "GL account"), (MARGIN + 190, "Cost center"), (MARGIN + 265, "Description"),
               (PAGE_W - MARGIN - 70, "Amount")]  # fmt: skip

    def header() -> None:
        nonlocal y
        for x, label in columns:
            text(x, label, 8, BOLD, MUTED)
        y += 5
        page.draw_line((MARGIN, y), (PAGE_W - MARGIN, y), color=LINE, width=0.8)
        y += 12

    header()
    total = 0.0
    for e in final.get("gl_distribution") or []:
        if y > PAGE_H - MARGIN - 30:
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            y = MARGIN
            header()
        gl = str(e.get("gl_code") or "")
        amount = float(e.get("amount") or 0)
        total += amount
        text(MARGIN, _fit(f"{gl} {gl_names.get(gl, '')}".strip(), 36), 8.5)
        text(MARGIN + 190, _fit(e.get("cost_center") or "", 14), 8.5)
        text(MARGIN + 265, _fit(e.get("description") or "", 44), 8.5)
        right(f"{amount:,.2f}", 8.5, FONT)
        y += 13
    page.draw_line((MARGIN, y - 6), (PAGE_W - MARGIN, y - 6), color=LINE, width=0.8)
    y += 6
    text(MARGIN, "Total", 9, BOLD)
    right(f"{total:,.2f} {currency}", 9, BOLD)
    y += 26
    text(MARGIN, "Made by AP Coder from the approved coding. Tax lines are posted as shown above.", 7.5, color=MUTED)


def stamped_pdf(inv: dict[str, Any], gl_names: dict[str, str] | None = None) -> bytes:
    """The approved invoice ``inv`` (a ``Store.get_invoice`` row) as a stamped PDF with its coding page."""
    gl_names = gl_names or {}
    doc = _source_document(Path(inv.get("source_path") or ""))
    try:
        if doc.page_count:
            _stamp(doc[0], inv)
        _coding_pages(doc, inv, gl_names)
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def file_name(inv: dict[str, Any]) -> str:
    final = inv.get("final_output") or {}
    raw = f"{final.get('vendor_name') or 'invoice'} {final.get('invoice_number') or inv['id']}"
    safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in raw).strip()[:80]
    return f"{safe or 'invoice'} - approved.pdf"


def batch_zip(invoices: list[dict[str, Any]], gl_names: dict[str, str] | None = None) -> bytes:
    out = io.BytesIO()
    names: set[str] = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for inv in invoices:
            name = file_name(inv)
            if name in names:
                name = name.replace(" - approved.pdf", f" (#{inv['id']}) - approved.pdf")
            names.add(name)
            z.writestr(name, stamped_pdf(inv, gl_names))
    return out.getvalue()
