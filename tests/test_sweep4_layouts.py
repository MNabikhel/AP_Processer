"""Reading bugs found by testing on a hard corpus of Canadian invoices (sweep 4): page layouts and printed formats the
rule reader read wrong or not at all. Each fixture is a small generated PDF; the corpus itself is not in the repo."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ap_coder.capture import analyze
from ap_coder.capture.types import VERIFIED

TODAY = dt.date(2026, 10, 20)


def _pdf(tmp_path: Path, items: list[tuple], name: str = "inv.pdf") -> Path:
    """A one-page PDF: (x, y, text) or (x, y, text, font size) in points from the top left."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text, *size in items:
        page.insert_text((x, y), text, fontsize=size[0] if size else 10)
    path = tmp_path / name
    doc.save(path)
    return path


def _read(path: Path, today: dt.date = TODAY):
    return analyze(path, ocr=False, today=today)


_VENDOR = [
    (60, 60, "Distributions Gagnon Ltd.", 16), (60, 80, "9235 Main St, Halifax, NS B3H 1A1"),
    (60, 95, "GST/HST No. 123456782 RT0001"),
]  # fmt: skip
_TABLE = [
    (60, 330, "Description"), (400, 330, "Qty"), (500, 330, "Amount"),
    (60, 345, "Forklift rental (weekly)"), (400, 345, "2"), (500, 345, "1,000.00"),
    (380, 500, "Subtotal"), (500, 500, "1,000.00"), (380, 515, "HST 15%"), (500, 515, "150.00"),
    (380, 530, "Invoice Total"), (500, 530, "1,150.00"),
]  # fmt: skip


def test_a_grid_that_prints_each_label_under_its_value(tmp_path):
    """A header grid of values, each with its small label printed UNDER it, two rows deep: the value under "Invoice
    Number" is the next row's PO number (one digit off), and the invoice date, the due date and the PO were missed."""
    grid = [
        (60, 200, "5874088"), (160, 200, "25/10/2025"), (260, 200, "8574088"), (360, 200, "09/12/2025"),
        (60, 209, "Invoice Number", 7), (160, 209, "Date", 7), (260, 209, "Sales Order", 7),
        (360, 209, "Payment Due", 7),
        (60, 232, "5874082"), (160, 232, "5847088"), (260, 232, "146890"),
        (60, 241, "PO #", 7), (160, 241, "Reference", 7), (260, 241, "Client ID", 7),
    ]  # fmt: skip
    got = _read(_pdf(tmp_path, _VENDOR + grid + _TABLE)).fields
    assert got["invoice_number"].value == "5874088"
    assert got["invoice_date"].value == "2025-10-25"
    assert got["due_date"].value == "2025-12-09"
    assert got["po_number"].value == "5874082"
    assert got["invoice_number"].status != VERIFIED  # a layout the benchmark has not measured


def test_a_grid_of_labels_over_values_is_still_read_below(tmp_path):
    """The usual grid (labels over values, and an address line just over the labels) is read as before."""
    grid = [
        (60, 189, "Bill To: Kestrel Foods, 12 Bay St"),
        (60, 200, "Invoice Number", 7), (160, 200, "Invoice Date", 7), (260, 200, "PO #", 7),
        (60, 213, "INV-7781"), (160, 213, "2026-09-14"), (260, 213, "PO-5521"),
    ]  # fmt: skip
    got = _read(_pdf(tmp_path, _VENDOR + grid + _TABLE)).fields
    assert (got["invoice_number"].value, got["invoice_date"].value, got["po_number"].value) == (
        "INV-7781",
        "2026-09-14",
        "PO-5521",
    )


def test_one_label_under_its_value_with_a_quote_number_under_the_next(tmp_path):
    """A scan's tilt runs most of a labels-under grid's labels into one line, so the page is not seen to print its
    labels under their values; "Inv # / No de facture" still has its number just over it, and the number under it is
    the quote's, printed right over "Quote # / Soumission". The quote number was verified as the invoice number (the
    page reader, reading the same words, agreed)."""
    import pymupdf

    grid = [
        (60, 200, "075037"), (60, 209, "Inv # / No de facture", 7),
        (160, 200, "4 Mar 2026"), (260, 200, "3 Apr 2026"),
        (160, 209, "Billing Date / Date de facturation Payment Due / Payable avant le", 7),
        (60, 232, "Q-2026-625"), (60, 241, "Quote # / Soumission", 7),
    ]  # fmt: skip
    path = _pdf(tmp_path, _VENDOR + grid + _TABLE)
    with pymupdf.open(path) as doc:
        text = [page.get_text() for page in doc]
    got = analyze(path, ocr=False, today=TODAY, page_text=text).fields["invoice_number"]
    assert got.value == "075037"
    assert got.status != VERIFIED


def test_a_business_number_is_not_an_invoice_number(tmp_path):
    """ "GST/HST No. 123456782 RT0001" (its "No." a label of the invoice number's shape) and "782311542 RT 0001" under
    a grid's label were read as the invoice number."""
    from ap_coder.capture.reader import _value_reading
    from ap_coder.capture.types import Box, Line, Word

    def line(text: str) -> Line:
        box = Box(1, 0.1, 0.1, 0.5, 0.12)
        return Line([Word(w, box) for w in text.split()], box, text)

    assert _value_reading("invoice_number", line("782311542 RT 0001"), 0, 1.0, "x") is None
    assert _value_reading("invoice_number", line("No. 123456782 RT0001"), 4, 1.0, "x") is None
    assert _value_reading("invoice_number", line("No. 123456782"), 4, 1.0, "x").value == "123456782"


def test_a_credit_whose_title_is_lost_is_known_by_its_credit_total(tmp_path):
    """A faxed credit memo printed with positive amounts lost its "CREDIT MEMO" title (and the supplier's name) to the
    fax: the amounts were verified as a bill's, positive, and the customer's name over "Attn: Accounts Payable" was
    verified as the supplier."""
    lines = [
        (300, 120, "6484 Main St, Vancouver, BC V9Y 9N3"), (300, 135, "GST No. 123456782 RT0001"),
        (60, 205, "Groupe Boisvert ltee", 12), (60, 220, "Attn: Accounts Payable"), (60, 235, "2818 Albert St"),
        (60, 280, "Credit No: CM2025-59874"), (300, 280, "Date: 2026-04-21"),
        (60, 330, "Description"), (400, 330, "Qty"), (500, 330, "Amount"),
        (60, 345, "Forklift rental (weekly)"), (400, 345, "2"), (500, 345, "1,000.00"),
        (380, 500, "Sub Total:"), (500, 500, "1,000.00"), (380, 515, "G.S.T. (5%):"), (500, 515, "50.00"),
        (380, 530, "CREDIT TOTAL:"), (500, 530, "1,050.00"),
    ]  # fmt: skip
    got = _read(_pdf(tmp_path, lines))
    assert (got.fields["subtotal"].value, got.fields["gst_amount"].value, got.fields["grand_total"].value) == (
        -1000.0,
        -50.0,
        -1050.0,
    )
    assert got.line_items[0].amount == -1000.0
    assert got.fields["vendor_name"].value != "Groupe Boisvert ltee"


def test_an_invoices_credit_line_does_not_make_it_a_credit(tmp_path):
    """ "Credit total" on an invoice (a line smaller than its total) is not the document's total."""
    lines = _VENDOR + [(380, 150, "Invoice No:"), (460, 150, "INV-7781")] + _TABLE + [
        (380, 560, "Credit total"), (500, 560, "25.00"),
    ]  # fmt: skip
    assert _read(_pdf(tmp_path, lines)).fields["grand_total"].value == 1150.0


def test_an_amount_nothing_confirms_on_a_scan_is_not_verified():
    """A photo's tilt put the shipping line's amount on the "Sub Total" label; both OCR reads agreed, no total was read
    and nothing added up, and the wrong subtotal was verified on the measured record of its evidence alone."""
    from ap_coder.capture.confidence import fuse
    from ap_coder.capture.types import Box, Reading

    box = [Box(1, 0.8, 0.57, 0.95, 0.6)]
    by_source = {
        s: {"subtotal": [Reading("subtotal", 171.03, "171,03", box, 0.95, "label-inline")]} for s in ("rules", "ocr2")
    }
    scan = fuse(by_source, [], layout_source="ocr", fields=("subtotal",))[0]["subtotal"]
    assert scan.value == 171.03 and scan.status != VERIFIED


def _tilted_scan(rows: list[tuple[float, float, str]], skew: float):
    """A scanned page's layout, tilted by ``skew`` (down per across): (x, y on the straight page, text) per segment."""
    from ap_coder.capture.layout import group_lines
    from ap_coder.capture.types import Box, DocLayout, PageLayout, Word

    words = []
    for x, y, text in rows:
        at = x
        for w in text.split():
            width = 0.0085 * len(w)
            dy = skew * (at + width / 2 - 0.5)
            words.append(Word(w, Box(1, at, y + dy, at + width, y + 0.016 + dy), 0.95, "ocr"))
            at += width + 0.006
    page = PageLayout(1, 1700, 2200, words, group_lines(words, 1), skew=skew)
    return DocLayout([page], "ocr")


def test_a_tilted_scans_label_is_read_level_with_its_value():
    """A scan tilted 2.5 degrees: the value two inches right of "Invoice Number" sits a row lower, level with "Bill of
    lading No."'s value, and the next row's number (one digit off) was read as the invoice number. The line items were
    read straightened; the header was not."""
    from ap_coder.capture.reader import read_fields

    rows = [
        (0.08, 0.237, "B/L No."), (0.44, 0.237, "1803062"),
        (0.08, 0.258, "Invoice Number"), (0.44, 0.258, "1803462"),
        (0.08, 0.279, "Ship Via"), (0.44, 0.279, "UPS Ground"),
    ]  # fmt: skip
    got = read_fields(_tilted_scan(rows, 0.045))["invoice_number"][0]
    assert got.value == "1803462"
    printed = 0.258 + 0.045 * (0.44 + 0.0085 * 7 / 2 - 0.5)  # where the number is printed on the tilted page
    assert got.boxes[0].y0 <= printed + 0.001 and got.boxes[0].y1 >= printed + 0.015


def test_labels_under_values_the_value_to_the_right_is_the_next_labels():
    """Labels under their values, on a scan whose rows are a little off: the next column's value ("A-26-54597", over
    "Applies to Invoice") sits level with "Credit Note #" and was read as the credit's number."""
    from ap_coder.capture.reader import read_fields

    rows = [
        (0.08, 0.282, "2025-09-11"), (0.08, 0.300, "Credit Date"),
        (0.25, 0.291, "CM2026-77832"), (0.25, 0.308, "Credit Note #"),
        (0.43, 0.302, "A-26-54597"), (0.43, 0.319, "Applies to Invoice"),
        (0.60, 0.305, "4500070761"), (0.60, 0.322, "Customer PO"),
    ]  # fmt: skip
    got = read_fields(_tilted_scan(rows, 0.0))
    assert got["invoice_number"][0].value == "CM2026-77832"
    assert got["po_number"][0].value == "4500070761"


def test_compact_dates_with_a_month_name(tmp_path):
    """ "12APR26" (day, month and two-digit year run together) and "Oct-15-26" (month, day and year by dashes) were
    not read as dates at all; a number with such a date inside it is still a number."""
    from ap_coder.capture.normalize import parse_date, parse_dates
    from ap_coder.capture.supplier import parse_date as template_date

    assert parse_date("12APR26") == "2026-04-12"
    assert parse_date("Billing Date: 24NOV25") == "2025-11-24"
    assert parse_date("Oct-15-26") == "2026-10-15"
    assert parse_date("Sept-05-2026") == "2026-09-05"
    assert parse_dates("INV-12MAR26") == [] and parse_dates("SKU 12ABC26") == []
    assert (template_date("12APR26"), template_date("Oct-15-26")) == ("2026-04-12", "2026-10-15")
    lines = _VENDOR + [
        (380, 150, "Invoice No:"), (460, 150, "INV-12MAR26"), (380, 165, "Invoice Date:"), (460, 165, "12APR26"),
        (380, 180, "Due Date:"), (460, 180, "Oct-15-26"),
    ] + _TABLE  # fmt: skip
    got = _read(_pdf(tmp_path, lines)).fields
    assert (got["invoice_number"].value, got["invoice_date"].value, got["due_date"].value) == (
        "INV-12MAR26",
        "2026-04-12",
        "2026-10-15",
    )
