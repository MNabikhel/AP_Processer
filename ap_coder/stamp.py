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
from .store import APPROVED

GREEN, INK, MUTED, LINE = (0.07, 0.55, 0.33), (0.08, 0.13, 0.2), (0.36, 0.39, 0.45), (0.85, 0.88, 0.92)
FONT, BOLD, FALLBACK = "helv", "hebo", "fallback"
PAGE_W, PAGE_H, MARGIN = 612, 792, 48  # US Letter, points


def _when(iso: str | None) -> str:
    return (iso or "")[:16].replace("T", " ")


def _font(text: str, bold: bool = False) -> str:
    """The built-in Helvetica covers Latin-1; anything else (Chinese, Greek, "—"...) uses the bundled fallback."""
    if all(ord(c) < 256 for c in text):
        return BOLD if bold else FONT
    return FALLBACK


def _width(text: str, size: float, font: str) -> float:
    import pymupdf

    return pymupdf.Font("cjk" if font == FALLBACK else font).text_length(text, fontsize=size)


def _fit(text: Any, size: float, max_width: float, bold: bool = False) -> str:
    """``text`` on one line, cut (with "...") to fit ``max_width`` points."""
    text = " ".join(str(text if text is not None else "").split())
    if _width(text, size, _font(text, bold)) <= max_width:
        return text
    while text and _width(text + "...", size, _font(text, bold)) > max_width:
        text = text[:-1]
    return text.rstrip() + "..."


def _put(page: Any, point: tuple[float, float], text: str, size: float, color: tuple = INK, bold: bool = False) -> None:
    """Write ``text`` at ``point`` as the page is seen (also on a rotated page)."""
    import pymupdf

    font = _font(text, bold)
    if font == FALLBACK and FALLBACK not in {f[4] for f in page.get_fonts()}:
        page.insert_font(fontname=FALLBACK, fontbuffer=pymupdf.Font("cjk").buffer)
    where = pymupdf.Point(point) * page.derotation_matrix
    page.insert_text(where, text, fontname=font, fontsize=size, color=color, rotate=page.rotation)


def _source_document(path: Path) -> Any:
    import pymupdf

    suffix = path.suffix.lower()
    try:
        if path.is_file() and suffix == ".pdf":
            doc = pymupdf.open(path)
            if doc.needs_pass and not doc.authenticate(""):  # password-protected: the coding page alone
                doc.close()
                return pymupdf.open()
            return doc
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
    box = page.rect  # as the page is seen, rotation included
    width, height = min(210, box.width - 12), 26 + 11 * len(lines)
    x0 = max(box.x0 + 6, box.x1 - width - 18)
    rect = pymupdf.Rect(x0, box.y0 + 18, x0 + width, box.y0 + 18 + height)
    # No fill: never hides what is printed there.
    page.draw_rect(rect * page.derotation_matrix, color=GREEN, width=1.6, overlay=True)
    _put(page, (rect.x0 + 9, rect.y0 + 17), "APPROVED", 12, GREEN, bold=True)
    for n, text in enumerate(lines):
        _put(page, (rect.x0 + 9, rect.y0 + 31 + 11 * n), _fit(text, 7.5, width - 16), 7.5)


def _coding_pages(doc: Any, inv: dict[str, Any], gl_names: dict[str, str]) -> None:
    final = inv.get("final_output") or {}
    currency = final.get("currency") or ""
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN

    def text(x: float, value: str, size: float = 9, bold: bool = False, color: tuple = INK, room: float = 0) -> None:
        _put(page, (x, y), _fit(value, size, room or PAGE_W - MARGIN - x, bold), size, color, bold)

    def right(value: str, size: float, bold: bool = False) -> None:
        _put(page, (PAGE_W - MARGIN - _width(value, size, _font(value, bold)), y), value, size, INK, bold)

    text(MARGIN, "Coding", 18, True)
    y += 18
    text(MARGIN, f"{final.get('vendor_name') or ''} · invoice {final.get('invoice_number') or ''}", 11)
    y += 22
    facts = [
        ("Invoice date", final.get("invoice_date") or "-"),
        ("Due date", final.get("due_date") or inv.get("due_date") or "-"),
        ("PO", final.get("po_number") or "-"), ("Terms", final.get("payment_terms") or "-"),
        ("Total", f"{float(final.get('grand_total') or 0):,.2f} {currency}"),
        ("Approved", f"{inv.get('reviewer') or ''} {_when(inv.get('reviewed_at'))}"),
    ]  # fmt: skip
    if final.get("original_invoice_number"):
        facts.insert(3, ("Credits invoice", final["original_invoice_number"]))
    if inv.get("second_reviewer"):
        facts.append(("Second approval", f"{inv['second_reviewer']} {_when(inv.get('second_reviewed_at'))}"))
    for label, value in facts:
        text(MARGIN, label, 8.5, color=MUTED)
        text(MARGIN + 95, str(value), 9)
        y += 13
    y += 12
    amount_x = PAGE_W - MARGIN - 70
    columns = [(MARGIN, "GL account", 182), (MARGIN + 190, "Cost center", 68), (MARGIN + 265, "Description",
               amount_x - MARGIN - 265 - 8), (amount_x, "Amount", 70)]  # fmt: skip

    def header() -> None:
        nonlocal y
        for x, label, _ in columns:
            text(x, label, 8, True, MUTED)
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
        text(MARGIN, f"{gl} {gl_names.get(gl, '')}".strip(), 8.5, room=columns[0][2])
        text(MARGIN + 190, str(e.get("cost_center") or ""), 8.5, room=columns[1][2])
        text(MARGIN + 265, str(e.get("description") or ""), 8.5, room=columns[2][2])
        right(f"{amount:,.2f}", 8.5)
        y += 13
    page.draw_line((MARGIN, y - 6), (PAGE_W - MARGIN, y - 6), color=LINE, width=0.8)
    y += 6
    text(MARGIN, "Total", 9, True)
    right(f"{total:,.2f} {currency}", 9, True)
    y += 26
    text(MARGIN, "Made by AP Coder from the approved coding. Tax lines are posted as shown above.", 7.5, color=MUTED)


def can_stamp(inv: dict[str, Any] | None) -> bool:
    """Whether ``inv`` is fully approved. An invoice waiting for its second approver (or reopened, rejected...)
    must never leave with an APPROVED stamp on it."""
    return bool(inv) and inv.get("status") == APPROVED


def stamped_pdf(inv: dict[str, Any], gl_names: dict[str, str] | None = None) -> bytes:
    """The approved invoice ``inv`` (a ``Store.get_invoice`` row) as a stamped PDF with its coding page.

    Raises ``ValueError`` when the invoice is not fully approved (see ``can_stamp``).
    """
    if not can_stamp(inv):
        raise ValueError(f"Invoice {inv.get('id')} is not fully approved ({inv.get('status')}): no APPROVED stamp.")
    gl_names = gl_names or {}
    doc = _source_document(Path(inv.get("source_path") or ""))
    try:
        if doc.page_count:
            _stamp(doc[0], inv)
        _coding_pages(doc, inv, gl_names)
        if any(FALLBACK in f[4] for page in doc for f in page.get_fonts()):
            doc.subset_fonts()  # only the characters used: keeps the file small
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def file_name(inv: dict[str, Any]) -> str:
    final = inv.get("final_output") or {}
    raw = f"{final.get('vendor_name') or 'invoice'} {final.get('invoice_number') or inv['id']}"
    safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in raw).strip()[:80]
    return f"{safe or 'invoice'} - approved.pdf"


def batch_zip(invoices: list[dict[str, Any]], gl_names: dict[str, str] | None = None) -> bytes:
    """The stamped PDFs of ``invoices`` in one ZIP. Invoices not fully approved are left out (see ``can_stamp``)."""
    out = io.BytesIO()
    names: set[str] = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for inv in filter(can_stamp, invoices):
            name = file_name(inv)
            if name.casefold() in names:  # Windows: "ACME" and "Acme" are the same file
                name = name.replace(" - approved.pdf", f" (#{inv['id']}) - approved.pdf")
            names.add(name.casefold())
            z.writestr(name, stamped_pdf(inv, gl_names))
    return out.getvalue()
