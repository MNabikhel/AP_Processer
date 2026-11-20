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


# A made-up supplier's invoice with its totals as a table of one row: a row of labels over a row of amounts, a cell
# left empty where there is none. Tax "HST" at 5% (GST charged in Alberta under an HST label), on the goods and the
# freight: 688.50 taxed, 34.43 of tax, 722.93 due.
ONE_ROW_LABELS = ("Line-Item Total", "Freight Amount", "GST Tax", "HST Tax", "Sub Total", "Deposits",
                  "Credits/Discounts", "Amount Due:")  # fmt: skip
ONE_ROW_VALUES = ("656.50", "32.00", "", "34.43", "722.93", "", "", "722.93 CAD")
ONE_ROW_SUPPLIER, ONE_ROW_CUSTOMER = "Prairie Promo Supply Inc.", "Maple Ridge Holdings Ltd."
ONE_ROW_GST = "123456782RT0001"
ONE_ROW_ADDRESS = "200 Ridge Road SW, Calgary AB T2P 1J9"
ONE_ROW_ITEMS = (("CB-220", "Non-Woven Lunch Cooler Bag, 2 colour", "100", "100", "0", "5.9900", "Each", "", "599.00"),
                 ("SETUP", "Lunch Cooler Setup", "1", "1", "0", "57.5000", "Each", "", "57.50"))  # fmt: skip


# The made-up JD Edwards purchase order that invoice bills (D-264): its three open lines, the goods, the setup and the
# freight, each its own line, the freight's words not the invoice's ("Freight Amount"). For
# ``jde_exports.open_po_lines_xlsx``; the supplier's address number is ONE_ROW_SUPPLIER_NUMBER.
ONE_ROW_PO, ONE_ROW_SUPPLIER_NUMBER, ONE_ROW_COMPANY = "40917", 4471001, "00210"
ONE_ROW_ACCOUNT = "0312005561.6420.2210"
ONE_ROW_PO_LINES = (("12", "LUNCH COOLER BAG NON-WOVEN 2C", "100", "5.99", "EA"),
                    ("13", "COOLER BAG SETUP", "1", "57.50", "EA"),
                    ("14", "DELIVERY CHARGE", "1", "32.00", "EA"))  # fmt: skip


def one_row_po_lines() -> list:
    """ONE_ROW_PO's open lines (``jde_exports.OpenPoLine``), nothing received or vouchered yet."""
    from decimal import Decimal

    from ap_coder.bench.jde_exports import OpenPoLine

    return [OpenPoLine(int(ONE_ROW_PO), Decimal(line), ONE_ROW_SUPPLIER_NUMBER, words, Decimal(quantity),
                       Decimal(cost), unit, ONE_ROW_ACCOUNT, ONE_ROW_COMPANY)
            for line, words, quantity, cost, unit in ONE_ROW_PO_LINES]  # fmt: skip


def one_row_totals(where: Path, name: str = "one_row.pdf", labels: str = "left", values: str = "right",
                   supplier: bool = True, customer: str = "below", po: str = "") -> Path:  # fmt: skip
    """That invoice as ``where / name``: each label set at the ``labels`` side of its cell ("left", "center",
    "right"), each amount at the ``values`` side. ``supplier``: the supplier's name printed at the top (else only in
    its logo, a picture). ``customer``: "below" (the customer's name under "Bill To:"), "beside" ("Customer:" at
    the left margin, the name in a column far to its right) or "unlabelled" (its address block with no label, as in
    an envelope's window). ``po``: a PO number printed under the terms (ONE_ROW_PO: the order it bills)."""
    import pymupdf

    items: list[tuple] = [(50, 68, "4410 Industrial Way NE, Calgary AB T2E 7H1"), (400, 50, "INVOICE", 14),
                          (400, 70, "Invoice #: 118204"), (400, 84, "Invoice Date: 2026-09-14"),
                          (400, 98, "Terms: Net 30")]  # fmt: skip
    if po:
        items.append((400, 112, f"PO Number: {po}"))
    if supplier:
        items.append((50, 50, ONE_ROW_SUPPLIER, 16))
    if customer == "below":
        items += [(50, 120, "Bill To:"), (50, 134, ONE_ROW_CUSTOMER), (50, 148, ONE_ROW_ADDRESS)]
    elif customer == "unlabelled":
        items += [(50, 120, ONE_ROW_CUSTOMER), (50, 134, ONE_ROW_ADDRESS)]
    else:
        items += [(36, 120, "Customer:"), (300, 120, ONE_ROW_CUSTOMER), (300, 134, ONE_ROW_ADDRESS)]
    heads = ((50, "Item #"), (95, "Item Description"), (275, "QTY Billed"), (318, "QTY Ordered"), (368, "Back Order"),
             (412, "Unit Price"), (455, "Per"), (480, "Credit"), (530, "Amount"))  # fmt: skip
    items += [(x, 200, text, 8) for x, text in heads]
    for y, row in zip((216, 230), ONE_ROW_ITEMS, strict=True):
        items += [(x + (10 if 2 <= n <= 4 else 0), y, text, 8)
                  for n, ((x, _), text) in enumerate(zip(heads, row, strict=True)) if text]  # fmt: skip
    width = 540 / len(ONE_ROW_LABELS)
    for n, cells in enumerate(zip(ONE_ROW_LABELS, ONE_ROW_VALUES, strict=True)):
        for text, side, y in zip(cells, (labels, values), (300, 314), strict=True):
            if text:
                left, room = 36 + n * width + 2, width - 4 - pymupdf.get_text_length(text, fontsize=8)
                items.append((left + {"left": 0, "center": room / 2, "right": room}[side], y, text, 8))
    items.append((50, 360, f"GST/HST Registration No: {ONE_ROW_GST[:9]} {ONE_ROW_GST[9:]}", 8))
    return text_pdf(where / name, items)


def supplier_beside_bill_to(where: Path, name: str, label: str = "Bill To", heading: str = "Remit To",
                            top: bool = True, named: bool = True, ship_to: bool = False) -> Path:  # fmt: skip
    """A made-up invoice as ``where / name``: a bare ``label`` ("Bill To", "Sold To", "Customer") with the customer's
    block under it, and the supplier's own block level with the label to its right, under ``heading`` ("Remit To",
    "Vendor", "From"...; "" for its letterhead there, its name printed larger), its GST/HST number last. ``top``: the
    supplier's letterhead also at the top left (else its name is only in a logo, a picture, or in that block).
    ``named``: its name in the block. ``ship_to``: a "Ship To" block between the two."""
    items: list[tuple] = [(400, 50, "INVOICE", 14), (400, 70, "Invoice #: 118204"),
                          (400, 84, "Invoice Date: 2026-09-14"), (400, 98, "Terms: Net 30")]  # fmt: skip
    if top:
        items += [(50, 50, ONE_ROW_SUPPLIER, 14), (50, 68, "4410 Industrial Way NE, Calgary AB T2E 7H1")]
    items += [(50, 130, label), (50, 144, ONE_ROW_CUSTOMER), (50, 158, "200 Ridge Road SW"),
              (50, 172, "Calgary AB T2P 1J9")]  # fmt: skip
    if ship_to:
        items += [(210, 130, "Ship To"), (210, 144, "Site 14, Ridge Road"), (210, 158, "Calgary AB T2P 1J9")]
    block = [heading] * bool(heading) + [ONE_ROW_SUPPLIER] * named
    block += ["PO Box 2210 Stn M", "Calgary AB T2P 2M4", f"GST/HST No. {ONE_ROW_GST[:9]} {ONE_ROW_GST[9:]}"]
    items += [(400 if ship_to else 330, 130 + 14 * n, text, 10 if heading or n else 12) for n, text in enumerate(block)]
    items += [(50, 260, "Description"), (400, 260, "Qty"), (500, 260, "Amount"), (50, 276, "Widgets"), (400, 276, "1"),
              (500, 276, "100.00"), (400, 340, "Subtotal"), (500, 340, "100.00"), (400, 354, "GST 5%"),
              (500, 354, "5.00"), (400, 368, "Total"), (500, 368, "105.00")]  # fmt: skip
    return text_pdf(where / name, items)


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
