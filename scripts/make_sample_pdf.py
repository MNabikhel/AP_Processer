"""Generate a synthetic two-page invoice PDF for end-to-end testing with Document Intelligence.

Usage: python scripts/make_sample_pdf.py [output.pdf]
Requires PyMuPDF (pip install pymupdf). Content matches
samples/ground_truth/contoso_invoice_INV-2026-04471.json.
"""

import sys

import pymupdf

HEADER = [
    ("Contoso Cloud Solutions Ltd", 16),
    ("1 Canary Wharf, London E14 5AB, United Kingdom", 9),
    ("VAT Reg No: GB 123 4567 89", 9),
    ("", 9),
    ("TAX INVOICE", 18),
    ("Invoice No: INV-2026-04471        Invoice Date: 14/09/2026        Due Date: 14/10/2026", 9),
    ("PO Number: PO-88213", 9),
    ("", 9),
    ("Bill To: Fabrikam Manufacturing UK Ltd, Attn: IT Operations - Accounts Payable", 9),
    ("22 Industrial Way, Coventry CV1 2AA", 9),
]
COLUMNS = [("#", 50), ("Description", 75), ("Qty", 390), ("Unit Price (£)", 430), ("Amount (£)", 510)]
PAGE1_ROWS = [
    ("1", "Azure reserved compute - production customer platform cluster (Sep 2026)", "1", "4,200.00", "4,200.00"),
    ("2", "Microsoft 365 E3 licences - monthly, internal staff", "25", "32.00", "800.00"),
    ("3", "Dell Latitude 7450 laptop, 32GB RAM", "3", "1,450.00", "4,350.00"),
    ("4", "Dell PowerEdge R760 rack server - IT Operations datacentre", "1", "8,900.00", "8,900.00"),
    ("", "Carried forward", "", "", "18,250.00"),
]
PAGE2_ROWS = [
    ("", "Brought forward", "", "", "18,250.00"),
    ("5", "Implementation consulting - endpoint rollout (hours)", "12", "150.00", "1,800.00"),
    ("6", "Premium support contract, annual, 01 Oct 2026 - 30 Sep 2027 (paid in advance)", "1", "3,600.00", "3,600.00"),
]
FOOTER_LINES = [
    "Delivery & handling (hardware): £95.00",
    "",
    "Subtotal:      £23,745.00",
    "VAT @ 20%:     £4,749.00",
    "Total Due:     £28,494.00",
    "",
    "Payment terms: 30 days. Bank: Barclays, Sort 20-00-00, Acc 12345678.",
]


def _table(page: pymupdf.Page, y: float, rows: list[tuple[str, ...]]) -> float:
    for label, x in COLUMNS:
        page.insert_text((x, y), label, fontsize=9, fontname="hebo")
    page.draw_line((45, y + 4), (580, y + 4))
    y += 18
    for row in rows:
        for (_, x), cell in zip(COLUMNS, row, strict=True):
            page.insert_textbox(pymupdf.Rect(x, y - 9, x + (310 if x == 75 else 75), y + 20), cell, fontsize=8)
        y += 26
    page.draw_line((45, y - 10), (580, y - 10))
    return y


def main(out: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 60.0
    for text, size in HEADER:
        page.insert_text((50, y), text, fontsize=size, fontname="hebo" if size > 12 else "helv")
        y += size + 8
    _table(page, y + 10, PAGE1_ROWS)
    page.insert_text((270, 760), "Page 1 of 2", fontsize=8)

    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 50), "Contoso Cloud Solutions Ltd - Invoice INV-2026-04471 (continued)", fontsize=9)
    y = _table(page, 90, PAGE2_ROWS) + 10
    for line in FOOTER_LINES:
        page.insert_text((330 if line.startswith(("Sub", "VAT", "Total")) else 50, y), line, fontsize=10)
        y += 16
    page.insert_text((270, 760), "Page 2 of 2", fontsize=8)
    doc.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples/contoso_invoice_INV-2026-04471.pdf")
