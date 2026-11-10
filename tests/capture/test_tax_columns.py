"""Line tables that print each line's tax in a column of its own, beside its amount before tax and sometimes its
total with the tax (B-2106, B-2107): the line's amount is the one before tax, never the tax nor the total with it,
and a line's own tax cell says whether it is taxed. Every invoice here is made up."""

from __future__ import annotations

import dataclasses as dc

import pytest

from ap_coder.capture import layout
from ap_coder.capture.reader import read_line_items
from ap_coder.capture.types import Box, DocLayout, PageLayout, Word
from tests.pdfs import text_pdf

Y0, STEP = 0.20, 0.03


def _table(headings: list[tuple[str, float]], rows: list[list[str]]) -> DocLayout:
    """A page with a line table: each heading at its x (0..1), each row's cells under them, one text line high."""
    words = []
    for n, cells in enumerate([[h for h, _ in headings], *rows]):
        y = Y0 + STEP * n * (4 / 3 if n else 1)
        for text, (_, x) in zip(cells, headings, strict=True):
            at = x
            for word in text.split():
                words.append(Word(word, Box(1, at, y - 0.006, at + 0.008 * len(word), y + 0.006), 1.0, "text"))
                at += 0.008 * (len(word) + 1)
    return DocLayout([PageLayout(1, 612, 792, words, layout.group_lines(words, 1))], "text")


def _read(headings: list[tuple[str, float]], rows: list[list[str]]) -> list[tuple]:
    return [(li.description, li.amount, li.tax_flags) for li in read_line_items(_table(headings, rows))]


TWO = [["Network cabling", "200.00", "26.00", "226.00"], ["Patch panel", "100.00", "13.00", "113.00"]]


@pytest.mark.parametrize("net, tax, total", [
    ("Amount", "Tax", "Total"), ("Net", "Tax amt", "Total"), ("Excl. tax", "GST/HST", "Incl. tax"),
    ("Before tax", "Tax", "After tax"), ("Subtotal", "Tax", "Total"), ("Ext. price", "Tax amt", "Line total"),
    ("Montant HT", "TPS/TVQ", "Montant TTC"), ("Net amount", "HST", "Gross"), ("Base", "Tax", "Total"),
])  # fmt: skip
def test_the_amount_before_tax_is_the_lines_amount_beside_its_tax_and_its_total(net, tax, total):
    """ "Description | Net | Tax amt | Total" was read with the total as each line's amount (226.00 for 200.00), the
    tax then taken back off as a charge "not in the line table"; "Before tax | Tax | After tax" was no line table at
    all. Whatever the headings say ("Base"), the figure that makes the total with the row's tax is the amount."""
    heads = [("Description", 0.10), (net, 0.55), (tax, 0.68), (total, 0.81)]
    assert [(d, a) for d, a, _ in _read(heads, TWO)] == [("Network cabling", 200.0), ("Patch panel", 100.0)], net
    backwards = [("Description", 0.10), (total, 0.55), (tax, 0.68), (net, 0.81)]
    rows = [[d, with_tax, t, before] for d, before, t, with_tax in TWO]
    assert [a for _, a, _ in _read(backwards, rows)] == [200.0, 100.0], f"{total} | {tax} | {net}"


def test_the_tax_column_before_the_amount_is_not_the_amount_and_one_row_reads_the_same():
    heads = [("Description", 0.10), ("Tax", 0.60), ("Amount", 0.78)]
    rows = [["Network cabling", "26.00", "200.00"], ["Patch panel", "13.00", "100.00"]]
    assert [(d, a) for d, a, _ in _read(heads, rows)] == [("Network cabling", 200.0), ("Patch panel", 100.0)]
    assert _read(heads, rows[:1]) == [("Network cabling", 200.0, "")]
    numbered = [("#", 0.06), *heads]
    assert _read(numbered, [["1", "Network cabling", "39.00", "300.00"]]) == [("Network cabling", 300.0, "")]


def test_a_lines_own_tax_cells_say_which_taxes_it_carries():
    """A tax printed line by line is that line's: a column that names its tax ("GST", "PST") gives the line that
    tax where its cell holds an amount; a blank or 0.00 cell is a line not taxed, among lines that are; a discount
    carries its (negative) tax. A column that names no tax ("Tax") leaves which tax to the invoice's own taxes."""
    heads = [("Description", 0.08), ("Qty", 0.36), ("Unit Price", 0.44), ("Amount", 0.56), ("GST", 0.66),
             ("PST", 0.74), ("Total", 0.84)]  # fmt: skip
    rows = [["Copy paper", "4", "50.00", "200.00", "10.00", "14.00", "224.00"],
            ["Courier service", "1", "100.00", "100.00", "5.00", "0.00", "105.00"],
            ["Gift card", "1", "50.00", "50.00", "", "", "50.00"],
            ["Loyalty discount", "1", "-20.00", "-20.00", "-1.00", "-1.40", "-22.40"]]  # fmt: skip
    assert _read(heads, rows) == [("Copy paper", 200.0, "GST/PST"), ("Courier service", 100.0, "GST"),
                                  ("Gift card", 50.0, "Exempt"), ("Loyalty discount", -20.0, "GST/PST")]  # fmt: skip
    one = [("Description", 0.10), ("Tax", 0.60), ("Amount", 0.78)]
    rows = [["Network cabling", "13.00", "100.00"], ["Training day", "", "100.00"], ["Postage", "0.00", "100.00"]]
    assert _read(one, rows) == [("Network cabling", 100.0, ""), ("Training day", 100.0, "Exempt"),
                                ("Postage", 100.0, "Exempt")]  # fmt: skip


def test_a_column_of_tax_codes_or_of_no_tax_at_all_is_read_as_before():
    """Letters under "Tax" are the line's code (D-64), as before; a tax column with no amount on any line says
    nothing (the tax is in the totals alone): no line is called exempt for it."""
    heads = [("Description", 0.10), ("Amount", 0.60), ("Tax", 0.78)]
    assert _read(heads, [["Copy paper", "200.00", "G P"], ["Courier", "100.00", "G"]]) == [
        ("Copy paper", 200.0, "G P"), ("Courier", 100.0, "G")]  # fmt: skip
    assert _read(heads, [["Copy paper", "200.00", "0.00"], ["Courier", "100.00", ""]]) == [
        ("Copy paper", 200.0, ""), ("Courier", 100.0, "")]  # fmt: skip


# --- Whole invoices, through the pipeline ----------------------------------------------------------------------------


@pytest.fixture
def pipe(tmp_path):
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.core.config import Settings
    from ap_coder.core.store import Store, load_sample_setup

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    settings = Settings()
    settings = dc.replace(settings, llm=dc.replace(settings.llm, provider="off"))
    return InvoicePipeline(settings, store.reference_data(), store=store)


def _invoice(tmp_path, number: str, supplier: list[str], heads: list[tuple[str, int]], rows: list[list[str]],
             totals: list[tuple[str, str]]):  # fmt: skip
    items = [(50, 60 + 14 * i, text) for i, text in enumerate(supplier)]
    ours = supplier[1].replace(supplier[1].split(",")[0], "9 Elm Road")  # (delivered in the vendor's province)
    items += [(50, 120, "INVOICE"), (50, 140, f"Invoice No: {number}"), (50, 154, "Invoice Date: 2026-10-20"),
              (50, 168, "Bill To: Fabrikam Canada Ltd."), (50, 182, ours)]  # fmt: skip
    items += [(x, 230, head) for head, x in heads]
    for n, row in enumerate(rows):
        items += [(x, 254 + 22 * n, cell) for cell, (_, x) in zip(row, heads, strict=True) if cell]
    at = 254 + 22 * len(rows) + 20
    for n, (label, value) in enumerate(totals):
        items += [(380, at + 16 * n, label), (500, at + 16 * n, value)]
    return text_pdf(tmp_path, items, name=f"{number}.pdf")


ON = ["Northwind IT Solutions Inc.", "200 Bay Street, Toronto, ON M5J 2J2", "GST/HST Reg. No: 123456782 RT0001"]
BC = ["Pacific Office Supply Ltd", "1055 West Georgia Street, Vancouver, BC V6E 3P3", "GST Reg. No: 555666775 RT0001"]
HST = [("Subtotal", "$300.00"), ("HST 13%", "$39.00"), ("Total", "$339.00")]


def _lines(result) -> list[tuple]:
    return [(li["description"], li["amount"], li["taxes_applied"], li["tax_status"])
            for li in result.output["line_items"]]  # fmt: skip


def _codes(result) -> set[str]:
    return {i.code for i in result.report.issues}


def test_an_invoice_with_before_tax_tax_and_after_tax_columns_is_read_whole(pipe, tmp_path):
    """Its "Tax" heading was read as the invoice's tax, with the first line's 26.00 under it: tax 26.00 against a
    total of 339.00 (TOTAL_MISMATCH), and no line read."""
    heads = [("Description", 50), ("Before tax", 340), ("Tax", 420), ("After tax", 500)]
    rows = [["Network cabling", "200.00", "26.00", "226.00"], ["Patch panel", "100.00", "13.00", "113.00"]]
    result = pipe.process(_invoice(tmp_path, "TX-3101", ON, heads, rows, HST), save=False)
    assert (result.output["subtotal"], result.output["tax_total"], result.output["grand_total"]) == (300.0, 39.0, 339.0)
    assert _lines(result) == [("Network cabling", 200.0, ["HST"], ""), ("Patch panel", 100.0, ["HST"], "")]
    assert not _codes(result) & {"TOTAL_MISMATCH", "SUBTOTAL_MISMATCH", "TAX_LINES_TOTAL_MISMATCH"}


def test_each_lines_printed_tax_decides_its_taxes_where_the_amounts_cannot(pipe, tmp_path):
    """Three lines of 100.00 and HST on two of them: the amounts cannot say which two, the tax column does. And a
    PST of 0.00 printed on a line is that line's exemption as the vendor printed it, not one to confirm."""
    heads = [("Description", 50), ("Tax", 400), ("Amount", 490)]
    rows = [["Network cabling", "13.00", "100.00"], ["Training day", "", "100.00"], ["Patch panel", "13.00", "100.00"]]
    totals = [("Subtotal", "$300.00"), ("HST 13%", "$26.00"), ("Total", "$326.00")]
    result = pipe.process(_invoice(tmp_path, "TX-3102", ON, heads, rows, totals), save=False)
    assert _lines(result) == [("Network cabling", 100.0, ["HST"], ""), ("Training day", 100.0, [], "exempt"),
                              ("Patch panel", 100.0, ["HST"], "")]  # fmt: skip
    heads = [("Description", 50), ("Qty", 250), ("Unit Price", 290), ("Amount", 350), ("GST", 410), ("PST", 460),
             ("Total", 510)]  # fmt: skip
    rows = [["Copy paper, case", "4", "50.00", "200.00", "10.00", "14.00", "224.00"],
            ["Courier service", "1", "100.00", "100.00", "5.00", "0.00", "105.00"]]  # fmt: skip
    totals = [("Subtotal", "$300.00"), ("GST 5%", "$15.00"), ("PST 7%", "$14.00"), ("Total", "$329.00")]
    result = pipe.process(_invoice(tmp_path, "TX-3103", BC, heads, rows, totals), save=False)
    assert _lines(result) == [("Copy paper, case", 200.0, ["GST", "PST"], ""), ("Courier service", 100.0, ["GST"], "")]
    assert result.output["line_items"][1]["tax_exempt"] == ["PST"] and "PST_EXEMPTION_TO_CONFIRM" not in _codes(result)


def test_lines_printed_with_their_tax_in_them_are_said_not_balanced_by_a_made_up_charge(pipe, tmp_path):
    """ "Description | Tax | Total", the total with the tax: nothing on the page gives each line's amount before tax
    for sure, so the lines stay as printed and the check says they include the tax (a person enters them), where a
    line "Charges not in the line table" of minus the tax made everything add up unseen (B-2107)."""
    heads = [("Description", 50), ("Tax", 400), ("Total", 490)]
    rows = [["Network cabling", "26.00", "226.00"], ["Patch panel", "13.00", "113.00"]]
    result = pipe.process(_invoice(tmp_path, "TX-3104", ON, heads, rows, HST), save=False)
    assert [(d, a) for d, a, *_ in _lines(result)] == [("Network cabling", 226.0), ("Patch panel", 113.0)]
    said = [i.message for i in result.report.issues if i.code == "SUBTOTAL_MISMATCH"]
    assert said and "the prices seem to include the tax" in said[0]
