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


# Three made-up receipts of one expense claim (D-216): fuel with GST, a lunch with GST and a tip, a room with HST.
# Each row is its text, with the amount at the right after a "|". Together: 273.00 before tax, 27.80 of tax, 300.80.
RECEIPTS = {
    "IMG_0411": ["Prairie Fuel Stop", "88 Range Road, Red Deer, AB T4N 2B2", "GST No. 123456789 RT0001", "",
                 "Receipt No: 48213", "Date: 2026-05-04", "", "Regular fuel|60.00", "",
                 "Subtotal|60.00", "GST 5%|3.00", "Total|63.00"],
    "IMG_0412": ["Riverbend Diner", "14 Bow Street, Calgary, AB T2P 3K1", "GST No. 987654321 RT0001", "",
                 "Receipt No: 7731", "Date: 2026-05-05", "", "LUNCH SPECIAL|24.00", "COFFEE|4.00", "",
                 "SUBTOTAL|28.00", "GST 5%|1.40", "TIP|5.00", "TOTAL|34.40"],
    "IMG_0413": ["Lakeshore Inn", "200 Front Street, Toronto, ON M5V 2Y3", "HST No. 555666777 RT0001", "",
                 "Receipt No: F-20931", "Date: 2026-05-06", "", "Room one night|180.00", "",
                 "Subtotal|180.00", "HST 13%|23.40", "Total|203.40"],
}  # fmt: skip


def receipt(where: Path, name: str, rows: list[str]) -> Path:
    """A made-up till receipt as ``where / name``: a narrow PDF of text (``name`` ending in .pdf), or a grey JPEG of
    it as a phone takes one, stating no resolution (.jpg)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=280, height=60 + 18 * len(rows))
    for n, row in enumerate(rows):
        text, _, amount = row.partition("|")
        page.insert_text((24, 40 + 18 * n), text, fontsize=11)
        if amount:
            page.insert_text((200, 40 + 18 * n), amount, fontsize=11)
    path = where / name
    if path.suffix.lower() == ".pdf":
        doc.save(path)
    else:
        pymupdf.Pixmap(pymupdf.csGRAY, page.get_pixmap(dpi=220)).save(path, jpg_quality=80)
    doc.close()
    return path
