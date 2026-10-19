"""Capture bugs found by the third sweep: each test reproduces a wrong value the reader once verified. The page
reader takes part (``page_text``: the page's own text as its transcription), as on a real invoice."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ap_coder.capture import analyze
from ap_coder.capture.types import VERIFIED
from tests.test_capture_edge_cases import _ACME, TODAY, _pdf


def _read(path: Path, today: dt.date = TODAY):
    import pymupdf

    with pymupdf.open(path) as doc:
        text = [page.get_text() for page in doc]
    return analyze(path, ocr=False, today=today, page_text=text)


_NO_NUMBER = [x for x in _ACME if x[2] not in ("Invoice No:", "AC-1001", "Invoice Date:", "2026-09-14")]
_NO_DATE = [x for x in _ACME if x[2] not in ("Invoice Date:", "2026-09-14")]


def test_the_code_on_the_total_line_is_the_currency_not_one_in_an_equivalent_note(tmp_path):
    """ "Total USD" with its amount in the next column, and a note "CAD equivalent: CAD 1,438.50": the invoice is in
    US dollars. The code on the total's label is on the amount, though the layout reads them apart."""
    lines = [x for x in _ACME if x[1] < 500 and "Calgary" not in x[2]] + [
        (60, 75, "400 Pine St, Seattle, WA 98101"),
        (380, 500, "Subtotal"), (500, 500, "1,000.00"), (380, 515, "GST 5%"), (500, 515, "50.00"),
        (380, 530, "Total USD"), (500, 530, "1,050.00"), (300, 560, "CAD equivalent: CAD 1,438.50"),
    ]  # fmt: skip
    assert _read(_pdf(tmp_path, lines)).fields["currency"].value == "USD"


def test_several_currency_codes_are_given_in_page_order():
    """Nothing tells which of several codes is the invoice's: the first printed is given (not as sure), not the first
    in the list of currencies."""
    from ap_coder.capture.normalize import currency_evidence

    assert currency_evidence("We accept USD and CAD") == ("USD", 0.5)


def test_an_order_id_is_not_a_us_address_and_a_calgary_suppliers_date_is_not_read_month_first(tmp_path):
    """ "Order ID 77812" is no Idaho ZIP code: a Calgary supplier's 03/04/2026 is not verified as March 4."""
    lines = _NO_DATE + [(380, 105, "Order ID 77812"), (380, 135, "Invoice Date:"), (460, 135, "03/04/2026"),
                        (380, 150, "Due Date:"), (460, 150, "03/05/2026")]  # fmt: skip
    capture = _read(_pdf(tmp_path, lines), today=dt.date(2026, 4, 10))
    date = capture.fields["invoice_date"]
    assert not (date.value == "2026-03-04" and date.status == VERIFIED), date


def test_a_label_with_an_id_is_not_a_us_address():
    from ap_coder.ai.offline_coder import OUTSIDE_CANADA, provinces
    from ap_coder.capture.reader import us_address

    for text in ("Order ID 77812", "Vendor ID 12345", "Store IN 46204", "Job ID 83702", "Ticket OR 97201"):
        assert not us_address(text), text
    for text in ("400 Pine St, Seattle, WA 98101", "Boise ID 83702", "WA 98101-1234", "Austin, TX 78701"):
        assert us_address(text), text
    assert provinces("Order ID 77812\nAcme Supply Ltd.\n12 Main St, Calgary, AB T2P 1A1")[0] == "AB"
    assert provinces("Acme Inc.\n400 Pine St, Seattle, WA 98101")[0] == OUTSIDE_CANADA


def test_a_canadian_supplier_billing_in_us_dollars_is_not_read_month_first(tmp_path):
    """A US-looking line on a Canadian supplier's letterhead (its US warehouse) does not make its dates month first."""
    lines = _NO_DATE + [(60, 105, "Warehouse: 400 Pine St, Seattle, WA 98101"), (380, 135, "Invoice Date:"),
                        (460, 135, "03/04/2026"), (380, 150, "Due Date:"), (460, 150, "03/05/2026")]  # fmt: skip
    capture = _read(_pdf(tmp_path, lines), today=dt.date(2026, 4, 10))
    date = capture.fields["invoice_date"]
    assert not (date.value == "2026-03-04" and date.status == VERIFIED), date


def test_due_upon_receipt_does_not_take_the_delivery_date_below_it(tmp_path):
    """ "Due Date: Upon Receipt" over "Delivery Date: Sep 20, 2026": the delivery date is not the due date; "Upon
    Receipt" is the terms."""
    lines = _NO_NUMBER + [(380, 110, "Invoice #: AC-1001"), (380, 122, "Invoice Date: Sep 14, 2026"),
                          (380, 134, "Due Date: Upon Receipt"), (380, 146, "Delivery Date: Sep 20, 2026")]  # fmt: skip
    capture = _read(_pdf(tmp_path, lines))
    assert capture.fields["due_date"].value != "2026-09-20"
    assert capture.fields["payment_terms"].value == "Due on receipt"
    na = _NO_NUMBER + [(380, 110, "Invoice #: AC-1001"), (380, 122, "Invoice Date: Sep 14, 2026"),
                       (380, 134, "Due Date: N/A"), (380, 146, "Delivery Date: Sep 20, 2026")]  # fmt: skip
    assert _read(_pdf(tmp_path, na, "na.pdf")).fields["due_date"].value != "2026-09-20"


def test_an_invoice_number_in_french_digit_groups_is_read_whole(tmp_path):
    """ "No de facture 123 456": the number is 123 456, not 123."""
    lines = _NO_NUMBER + [(380, 120, "No de facture"), (470, 120, "123 456")]
    fr = _read(_pdf(tmp_path, lines)).fields["invoice_number"]
    assert fr.value.replace(" ", "") == "123456", fr


def test_text_drawn_twice_for_bold_is_read_once(tmp_path):
    """Some PDF writers make bold by drawing the text twice a hair apart: each word is read once."""
    lines = []
    for x, y, t in _ACME:
        lines += [(x, y, t)] + ([(x + 0.3, y, t)] if t in ("Acme Supply Ltd.", "Invoice Date:") else [])
    capture = analyze(_pdf(tmp_path, lines), ocr=False, today=TODAY)
    assert capture.fields["vendor_name"].value == "Acme Supply Ltd."
    assert capture.fields["invoice_date"].value == "2026-09-14"
