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


def long_invoice(where: Path, pages: int, per_page: int = 20, name: str = "long.pdf", number: str = "RW-88213",
                 vendor: str = "Rapidway Courier Canada Ltd.", date: str = "2026-05-04"
                 ) -> tuple[Path, float, float]:  # fmt: skip
    """A made-up invoice of ``pages`` pages with ``per_page`` lines on each and its subtotal, GST and total under
    the lines of the LAST page: (the PDF, its subtotal, its total)."""
    items: list[tuple] = [
        (50, 60, vendor), (50, 76, "500 Dock Road, Calgary, AB T2P 1J9"), (400, 60, "INVOICE"),
        (400, 76, f"Invoice No: {number}"), (400, 92, f"Invoice Date: {date}"),
        (50, 110, "Bill to: Madeup Constructors Inc., 410 Elm Street, Edmonton, AB T5J 0N3"),
    ]  # fmt: skip
    cents, n = 0, 0
    for page in range(1, pages + 1):
        y = 150 if page == 1 else 60
        items += [(50, y, "Description", 10, page), (350, y, "Qty", 10, page), (420, y, "Unit Price", 10, page),
                  (500, y, "Amount", 10, page)]  # fmt: skip
        for i in range(per_page):
            n += 1
            amount = 1025 + (n * 7) % 90 * 100
            cents += amount
            row = y + 18 + 16 * i
            price = f"{amount / 100:.2f}"
            items += [(50, row, f"Ground shipment waybill {70000 + n}", 10, page), (350, row, "1", 10, page),
                      (420, row, price, 10, page), (500, row, price, 10, page)]  # fmt: skip
    gst = round(cents * 0.05)
    y = (150 if pages == 1 else 60) + 18 + 16 * per_page + 30
    items += [(400, y, "Subtotal", 10, pages), (500, y, f"{cents / 100:,.2f}", 10, pages),
              (400, y + 16, "GST 5%", 10, pages), (500, y + 16, f"{gst / 100:,.2f}", 10, pages),
              (400, y + 32, "Total Due", 10, pages), (500, y + 32, f"{(cents + gst) / 100:,.2f}", 10, pages),
              (50, y + 32, "GST/HST No. 814275533 RT0001", 10, pages)]  # fmt: skip
    return text_pdf(where, items, name=name, pages=pages), cents / 100, (cents + gst) / 100
