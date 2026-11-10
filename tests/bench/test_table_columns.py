"""The accuracy bench's line tables with more than the usual columns (B-2206): the rows' numbers, a discount, each
line's tax as an amount or as a code, the line's total with tax, headings printed from the left or centred. A share
of generated invoices print them (``generator.EXTRA_COLUMNS``), so ``bench run`` and ``bench lines`` measure the
reading of the amount before tax and after the discount, under its own heading."""

from __future__ import annotations

from ap_coder.bench import generator
from ap_coder.bench.generator import generate
from ap_coder.capture import analyze


def _amounts(case) -> list[float]:
    return [round(li["amount"], 2) for li in case.truth["line_items"]]


def test_an_invoice_that_draws_no_extra_column_is_generated_as_before(tmp_path, monkeypatch):
    """The extra columns draw from a stream of their own, never the invoice's: with none drawn every invoice is as
    it was (the benches' earlier numbers still compare), and with all of them the invoice says the same things."""
    usual = generate(tmp_path / "share", 24, seed=5, scanned_fraction=0.0)
    monkeypatch.setattr(generator, "EXTRA_COLUMNS", 0.0)
    none = generate(tmp_path / "none", 24, seed=5, scanned_fraction=0.0)
    monkeypatch.setattr(generator, "EXTRA_COLUMNS", 1.0)
    every = generate(tmp_path / "every", 24, seed=5, scanned_fraction=0.0)
    assert not any("table_columns" in case.truth["extras"] for case in none)
    drawn = [case for case in usual if "table_columns" in case.truth["extras"]]
    assert 0 < len(drawn) < len(usual) / 2  # a modest share
    assert [a.truth for a, b in zip(usual, none, strict=True) if a not in drawn] == [
        b.truth for a, b in zip(usual, none, strict=True) if a not in drawn]  # fmt: skip
    for a, b in zip(none, every, strict=True):
        assert {k: v["value"] for k, v in a.truth["fields"].items()} == {
            k: v["value"] for k, v in b.truth["fields"].items()}, a.id  # fmt: skip
        assert _amounts(a) == _amounts(b) and len(a.truth["line_items"]) == len(b.truth["line_items"])


def test_every_extra_column_is_drawn_and_its_table_read_right(tmp_path, monkeypatch):
    """Each kind of column is printed on some invoice, and the rule reader gives every such table's lines their
    amounts: after the discount, before the tax, never the row's number, the tax or the total with it."""
    monkeypatch.setattr(generator, "EXTRA_COLUMNS", 1.0)
    cases = [case for case in generate(tmp_path, 60, seed=7, scanned_fraction=0.0)
             if "table_columns" in case.truth["extras"]]  # fmt: skip
    seen = {column for case in cases for column in case.truth["extras"]["table_columns"]}
    assert seen >= {"number", "before", "discount", "tax0", "tax1", "line_total", "tax_code", "headings left",
                    "headings center"}  # fmt: skip
    for case in cases:
        read = analyze(case.path, second_read=False).line_items
        assert [round(li.amount, 2) for li in read] == _amounts(case), (case.id, case.truth["extras"]["table_columns"])
        numbered = [n for n, (li, want) in enumerate(zip(read, case.truth["line_items"], strict=True), 1)
                    if li.description.startswith(f"{n} ") and not want["description"].startswith(f"{n} ")]  # fmt: skip
        assert not numbered, case.id  # (a row's number under "#" is no part of its description)


def test_a_discounted_line_prints_its_price_before_the_discount_and_its_amount_after(tmp_path, monkeypatch):
    monkeypatch.setattr(generator, "EXTRA_COLUMNS", 1.0)
    cases = [case for case in generate(tmp_path, 60, seed=7, scanned_fraction=0.0)
             if "discount" in case.truth["extras"].get("table_columns", [])]  # fmt: skip
    lines = [li for case in cases for li in case.truth["line_items"] if li["quantity"] and li["unit_price"]]
    off = [li for li in lines if round(li["quantity"] * li["unit_price"], 2) > li["amount"] + 0.005]
    assert cases and off  # (some lines carry a discount: quantity x the price printed is more than the amount)
