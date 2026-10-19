"""Capture bugs found by the third sweep: each test reproduces a wrong value the reader once verified. The page
reader takes part (``page_text``: the page's own text as its transcription), as on a real invoice."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ap_coder.capture import analyze
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

