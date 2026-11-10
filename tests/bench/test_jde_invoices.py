"""The made-up company's supplier invoices (``bench/jde_invoices``): the seven that cover a day and what AP should
key from each, more made from the books' vouchers, and the files (a PDF with its ground truth)."""

import json
from decimal import Decimal

import pytest

from ap_coder.bench import jde_company, jde_invoices
from ap_coder.bench.generator import load_cases
from ap_coder.core import chart


@pytest.fixture(scope="module")
def truth():
    return jde_company.build()


@pytest.fixture(scope="module")
def seven(truth):
    return dict(zip(jde_invoices.NAMED, jde_invoices.named(truth), strict=True))


def test_the_seven_are_named_in_order_and_each_adds_up(truth, seven):
    assert [invoice.name for invoice in seven.values()] == list(jde_invoices.NAMED)
    offered = {account.string for account in truth.suggestable()}
    for invoice in seven.values():
        keyed = sum((part.amount for part in invoice.parts), Decimal(0))
        assert keyed + invoice.recovered == invoice.total, invoice.name  # G/L lines + recovered tax = gross
        assert {part.account for part in invoice.parts} <= offered, invoice.name
        assert all(0 < len(part.remark) <= 30 for part in invoice.parts), invoice.name
    assert len({invoice.supplier_name for invoice in seven.values()}) == 7  # a supplier each


def test_each_of_the_seven_is_the_case_it_is_named_for(truth, seven):
    one = seven["one_line"]
    assert len(one.lines) == 1 and one.supplier.accounts == (one.parts[0].account,)
    assert [(tax.code, tax.amount) for tax in one.taxes] == [("GST", one.subtotal * 5 / 100)]
    four = seven["four_lines"]
    assert len(four.lines) == 4 and len({part.account for part in four.parts}) == 3
    assert [line.for_a_person for line in four.lines] == [False, False, False, True]
    assert len(seven["fifteen_lines"].lines) == 15
    whole, split = seven["split_line"].lines
    assert len(whole.parts) == 1 and len(split.parts) == 2
    assert len({chart.split_account(part.account)[0] for part in split.parts}) == 2  # two business units
    assert len({chart.split_account(part.account)[1] for part in split.parts}) == 1  # one object.subsidiary
    assert sum(part.amount for part in split.parts) == split.amount
    usd = seven["usd_new_supplier"]
    assert usd.supplier is None and usd.currency == "USD" and not usd.taxes
    assert chart.skip_word(usd.supplier_name) == "" and all(
        usd.supplier_name.upper() != supplier.name for supplier in truth.suppliers
    )
    credit = seven["credit_note"]
    assert credit.total < 0 and credit.recovered < 0 and all(part.amount < 0 for part in credit.parts)


def test_the_quebec_invoice_recovers_the_gst_and_keeps_the_qst_in_its_lines(seven):
    quebec = seven["quebec"]
    taxes = {tax.code: tax.amount for tax in quebec.taxes}
    assert set(taxes) == {"GST", "QST"} and quebec.province == "QC"
    assert quebec.recovered == taxes["GST"]
    assert sum(part.amount for part in quebec.parts) == quebec.subtotal + taxes["QST"]
    coding = quebec.coding()
    assert (coding["tax_code"], coding["tax_area"]) == ("V", "CDNGST")
    assert (coding["taxable"], coding["tax"]) == (float(quebec.subtotal), float(taxes["GST"]))
    assert coding["in_the_expense"] == {"QST": float(taxes["QST"])}


def test_the_seven_follow_the_date_given_and_the_same_books_give_the_same_files(truth, tmp_path):
    import datetime as dt

    on = dt.date(2026, 3, 12)
    dated = jde_invoices.named(truth, on)
    assert all(on - dt.timedelta(days=9) <= invoice.date < on for invoice in dated)
    first = jde_invoices.write(tmp_path / "a", truth, dated[:2])
    again = jde_invoices.write(tmp_path / "b", truth, dated[:2])
    assert [case.path.read_bytes() for case in first] == [case.path.read_bytes() for case in again]
    assert [case.truth for case in first] == [case.truth for case in again]


def test_an_invoice_is_written_as_a_pdf_with_what_is_printed_and_what_ap_keys(truth, seven, tmp_path):
    import pymupdf

    four = seven["four_lines"]
    (case,) = jde_invoices.write(tmp_path, truth, [four])
    assert (case.path.name, case.truth_path.name) == ("four_lines.pdf", "four_lines.truth.json")
    with pymupdf.open(case.path) as pdf:
        text = "\n".join(page.get_text() for page in pdf)
    assert four.supplier_name in text and four.number in text and "Maple Ridge Holdings Ltd." in text
    assert all(line.description in text for line in four.lines)
    kept = json.loads(case.truth_path.read_text(encoding="utf-8"))
    assert kept["fields"]["invoice_number"]["value"] == four.number
    assert kept["fields"]["grand_total"]["value"] == float(four.total)
    assert [line["amount"] for line in kept["line_items"]] == [float(line.amount) for line in four.lines]
    coding = kept["coding"]
    assert coding["supplier_number"] == four.supplier.number and coding["gross"] == float(four.total)
    assert [line["parts"][0]["account"] for line in coding["lines"]] == [line.parts[0].account for line in four.lines]
    assert [line["for_a_person"] for line in coding["lines"]] == [False, False, False, True]
    assert [loaded.id for loaded in load_cases(tmp_path)] == ["four_lines"]


def test_more_invoices_are_made_from_the_books_vouchers(truth):
    more = jde_invoices.from_vouchers(truth, 30)
    assert len(more) == 30 and len({invoice.name for invoice in more}) == 30
    by_document = {voucher.document: voucher for voucher in truth.vouchers}
    for invoice in more:
        voucher = by_document[invoice.document]
        supplier = truth.supplier(voucher.supplier)
        assert supplier.kind == "trade" and invoice.supplier is supplier and invoice.number == voucher.invoice_number
        assert [part.account for part in invoice.parts] == [line.account for line in voucher.lines]
        # the voucher's own remarks; one left blank in the books gets the account's words
        assert all(part.remark == (line.remark or part.remark) and part.remark
                   for line, part in zip(voucher.lines, invoice.parts, strict=True))  # fmt: skip
        assert sum((part.amount for part in invoice.parts), Decimal(0)) + invoice.recovered == invoice.total
        assert bool(invoice.recovered) == supplier.taxed and invoice.currency == supplier.currency
    assert {invoice.currency for invoice in more} == {"CAD", "USD"}
    assert any(invoice.total < 0 for invoice in more) and any(len(invoice.lines) > 1 for invoice in more)
