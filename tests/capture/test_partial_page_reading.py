"""A page the page reader read only in part (it got stuck repeating itself twice: what it read before the loop is kept)
is not compared with the PDF's hidden text: the totals under where it stopped were never read, which is not the PDF
showing other figures than its page (B-1518)."""

from __future__ import annotations

import datetime as dt

from ap_coder.capture import TEXT_LAYER_CHECK, analyze
from ap_coder.capture.transcript import PARTIAL_MARK
from ap_coder.capture.types import CHECK
from tests.pdfs import text_pdf

ROWS = [
    ("Toner cartridge", "2", "61.25", "122.50"),
    ("Copy paper", "5", "9.75", "48.75"),
    ("Stapler", "3", "14.40", "43.20"),
    ("Binder clips", "4", "3.15", "12.60"),
    ("Pens box", "6", "7.35", "44.10"),
]
HEAD = ["Acme Supply Ltd.", "123 Main St, Toronto, ON M5V 2T6", "GST/HST No. 123456782 RT0001", "INVOICE",
        "Invoice No: INV-5001", "Invoice Date: 2026-10-20"]  # fmt: skip


def _pdf(tmp_path):
    items = [(50, 50 + 16 * i, t) for i, t in enumerate(HEAD)]
    items += [(50, 200, "Description"), (300, 200, "Qty"), (380, 200, "Price"), (480, 200, "Amount")]
    for n, (d, q, p, a) in enumerate(ROWS):
        y = 216 + 16 * n
        items += [(50, y, d), (300, y, q), (380, y, p), (480, y, a)]
    items += [(380, 400, "Subtotal"), (480, 400, "271.15"), (380, 416, "HST 13%"), (480, 416, "35.25"),
              (380, 432, "Total"), (480, 432, "306.40")]  # fmt: skip
    return text_pdf(tmp_path, items)


def _reading(stopped: bool) -> str:
    table = (
        "<table><tr><td>Description</td><td>Qty</td><td>Price</td><td>Amount</td></tr>"
        + "".join(f"<tr><td>{d}</td><td>{q}</td><td>{p}</td><td>{a}</td></tr>" for d, q, p, a in ROWS)
        + "</table>"
    )
    return "\n".join([*HEAD, table]) + ("\n" + PARTIAL_MARK if stopped else "")


def test_a_page_read_in_part_raises_no_hidden_text_warning(tmp_path):
    result = analyze(_pdf(tmp_path), today=dt.date(2026, 11, 2), page_text=[_reading(True)], tables=False)
    check = next(c for c in result.checks if c["code"] == TEXT_LAYER_CHECK)
    assert check["ok"] and "only part" in check["detail"]
    assert all(result.fields[f].status != CHECK for f in ("subtotal", "grand_total"))


def test_the_same_reading_unmarked_still_raises_it(tmp_path):
    """The check itself is unchanged: totals the page reader read the rest of the page without are a warning."""
    result = analyze(_pdf(tmp_path), today=dt.date(2026, 11, 2), page_text=[_reading(False)], tables=False)
    check = next(c for c in result.checks if c["code"] == TEXT_LAYER_CHECK)
    assert not check["ok"]
