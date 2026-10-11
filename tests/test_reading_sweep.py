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
        "Returned pallets          4       50.00      200.00 CR",
        "Returned crates           2       25.00      (50.00)",
        "Damaged drums             3      -10.00      -30.00", "", (300, "Subtotal  280.00 CR"),
    ]])  # fmt: skip
    items = [(li.description, li.quantity, li.unit_price, li.amount) for li in read_line_items(build_layout(pdf))]
    assert items == [("Returned pallets", 4.0, -50.0, -200.0), ("Returned crates", 2.0, -25.0, -50.0),
                     ("Damaged drums", 3.0, -10.0, -30.0)]  # fmt: skip


_FRENCH = ["FACTURE FL-2026-0042", "Fournitures Laval Inc.", "Papier A4 12 42,25 $ 507,00 $",
           "Toner 4 155,15 $ 620,60 $", "Bureau 2 1 234,56 $ 2 469,12 $", "Livraison 1 125,00 $ 125,00 $",
           "Sous-total 3 721,72 $", "TPS (5 %) 186,09 $", "TVQ (9,975 %) 371,24 $", "Total 4 279,05 $"]  # fmt: skip


def test_figures_are_read_in_french_and_european_formats():
    """ "1 234,56 $" was counted as the figure 234 (and "42,25" not at all): the figures of a French invoice were
    fragments, so the page reader's figure-by-figure comparison measured nothing on them."""
    from decimal import Decimal

    from ap_coder.figures import compare_figures, figures

    found = figures("\n".join(_FRENCH) + "\nNetto 1.234,56 EUR\nTotal 1 050.00")
    for amount in ("42.25", "507.00", "1234.56", "2469.12", "3721.72", "186.09", "4279.05", "1050.00"):
        assert Decimal(amount) in found, amount
    assert Decimal("234") not in found and Decimal("279") not in found
    assert figures("Total 1,234.56 and 3,907.81") == {
        Decimal("1234.56"): ["1,234.56"],
        Decimal("3907.81"): ["3,907.81"],
    }
    misread = compare_figures("Total 4 279,05 $", "Total 4 279,65 $")
    assert misread.confirmed == 0 and misread.differ == [("4 279,65", "4 279,05")]


def test_a_french_pdf_whose_hidden_text_is_not_the_page_fails_the_check(tmp_path):
    """The edited-PDF check (``TEXT_LAYER_MATCHES_PAGE``) passed a French invoice whose hidden subtotal and total
    differ from the page ("the page as printed shows 13 of the 13 figures"): its figures were never compared."""
    import datetime as dt

    from ap_coder.capture import analyze

    pdf = _pdf(tmp_path / "fr.pdf", [list(_FRENCH)])
    today = dt.date(2026, 10, 1)
    same = analyze(pdf, ocr=False, page_text=["\n".join(_FRENCH)], today=today)
    check = next(c for c in same.checks if c["code"] == "TEXT_LAYER_MATCHES_PAGE")
    assert check["ok"] and check["confirmed"] == check["figures"] >= 8, check
    printed = "\n".join(_FRENCH).replace("3 721,72", "2 721,72").replace("4 279,05", "3 279,05")
    tampered = analyze(pdf, ocr=False, page_text=[printed], today=today)
    check = next(c for c in tampered.checks if c["code"] == "TEXT_LAYER_MATCHES_PAGE")
    assert not check["ok"] and set(check["fields"]) >= {"subtotal", "grand_total"}, check
