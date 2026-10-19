"""Sweep 3, the ai folder: credit notes, learning past the history cap, banking lines, the page reader's test mark,
unlisted charges and the files a folder of invoices holds."""

from ap_coder.ai.offline_coder import code_from_capture
from ap_coder.capture.types import VERIFIED, CaptureResult, FieldResult, LineReading
from ap_coder.coding.validation import validate_coding

HEADER = {"vendor_name": "Northwind", "invoice_number": "CN-1", "invoice_date": "2026-09-12", "currency": "CAD"}


def _capture(lines=(), **values):
    return CaptureResult(
        fields={k: FieldResult(k, v, 0.95, VERIFIED) for k, v in {**HEADER, **values}.items()},
        line_items=[LineReading(d, q, u, a) for d, q, u, a in lines],
    )


def test_a_credit_note_with_positive_lines_and_negative_totals_codes_the_lines_as_credits(reference):
    # The lines print 1,450 and 600; the subtotal -2,050: the lines are the credit, not a -4,100 charge beside them.
    capture = _capture([("Returned laptop", 1, 1450.0, 1450.0), ("Consulting hours cancelled", 4, 150.0, 600.0)],
                       subtotal=-2050.0, hst_amount=-266.5, tax_total=-266.5, grand_total=-2316.5)  # fmt: skip
    text = "Northwind\nToronto ON M5H 1A1\nCREDIT NOTE\nBill to:\nToronto ON M5J 2J5\n"
    coding = code_from_capture(capture, reference, [], text=text).coding
    assert [(li.quantity, li.unit_price, li.amount) for li in coding.line_items] == [
        (1, -1450.0, -1450.0),
        (4, -150.0, -600.0),
    ]
    assert coding.subtotal == -2050.0
    assert [i.code for i in validate_coding(coding, reference).issues if i.severity == "error"] == []
