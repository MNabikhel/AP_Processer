"""Regression tests for bugs found by the reading-and-decisions bug sweep: each one reproduces a bug that was
real (a wrong value shown as right, a wrong message, an invoice approved without a person when it should not be),
so it is never made again."""

from dataclasses import replace
from pathlib import Path

import pymupdf

from ap_coder.config import Settings
from ap_coder.pipeline import InvoicePipeline
from ap_coder.store import Store


def _pdf(path: Path, pages: list[list[str | tuple[float, str]]]) -> Path:
    """A digital PDF: each page's lines, one under the other (a (x, text) pair is set at that x)."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page(width=612, height=792)
        y = 50.0
        for line in lines:
            x, text = line if isinstance(line, tuple) else (50.0, line)
            if text:
                page.insert_text((x, y), text, fontsize=10)
            y += 14
    doc.save(path)
    return path


def _offline(tmp_path: Path, reference) -> tuple[InvoicePipeline, Store]:
    s = Settings()
    store = Store(tmp_path / "ap.db")
    return InvoicePipeline(replace(s, llm=replace(s.llm, provider="off")), reference, store=store), store


def test_a_currency_assumed_from_a_us_address_is_said_as_assumed_not_as_cad(tmp_path, reference):
    """A US supplier's invoice that prints no currency is coded in US dollars (``default_currency``), and the review
    screen said "no currency printed: taken as CAD" (info), the opposite of what an approval posts."""
    pdf = _pdf(tmp_path / "us.pdf", [[
        "Acme Widgets Inc.", "100 Main Street, Buffalo, NY 14201", "", "INVOICE", "Invoice No: AW-5521",
        "Invoice Date: 2026-09-15", "Bill To: Northern Foods Ltd, 12 King St W, Toronto, ON M5H 1A1", "",
        "Description            Qty   Unit Price    Amount", "Widgets                 10       25.00      250.00", "",
        (300, "Subtotal  250.00"), (300, "Total  250.00"),
    ]])  # fmt: skip
    pipe, _store = _offline(tmp_path, reference)
    result = pipe.process(pdf)
    assert result.ok, result.error
    assert result.output["currency"] == "USD"
    (issue,) = [i for i in result.report.issues if i.code == "CURRENCY_NOT_FOUND"]
    assert issue.severity == "warning" and "USD" in issue.message and "CAD" not in issue.message


def test_a_credit_lines_quantity_and_unit_price_are_read_with_the_credits_sign(tmp_path):
    """A credit note's row "Returned pallets 4 50.00 200.00 CR" was read as 4 at 4.00 (the quantity taken for the
    unit price too): quantity x price was only ever matched against a positive amount, so a negative line never
    found its price. The coded line then posted a unit price of 4.00 and the review warned LINE_MATH."""
    from ap_coder.capture import build_layout
    from ap_coder.capture.reader import read_line_items

    pdf = _pdf(tmp_path / "credit.pdf", [[
        "Harbour Supply Ltd.", "CREDIT NOTE", "", "Description            Qty   Unit Price    Amount",
        "Returned pallets          4       50.00      200.00 CR", "Returned crates           2       25.00      (50.00)",
        "Damaged drums             3      -10.00      -30.00", "", (300, "Subtotal  280.00 CR"),
    ]])  # fmt: skip
    items = [(li.description, li.quantity, li.unit_price, li.amount) for li in read_line_items(build_layout(pdf))]
    assert items == [("Returned pallets", 4.0, -50.0, -200.0), ("Returned crates", 2.0, -25.0, -50.0),
                     ("Damaged drums", 3.0, -10.0, -30.0)]  # fmt: skip

