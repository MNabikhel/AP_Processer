"""Two readings of a page compared figure by figure (OCR or the PDF's text against the page reader)."""

from decimal import Decimal

from ap_coder.figures import compare_figures, figures


def test_amounts_are_found_however_they_are_written():
    found = figures("Subtotal $1,150.00 · credit (2,750.00) · fee -45.5 · HST 13% · <td>49.00</td>")
    assert set(found) == {Decimal("1150.00"), Decimal("-2750.00"), Decimal("-45.5"), Decimal("13"), Decimal("49.00")}
    assert found[Decimal("-2750.00")] == ["(2,750.00)"]


def test_dates_years_and_small_numbers_are_not_figures():
    assert figures("Invoice date 2026-03-14, due 14/04/2026, page 2 of 3, line 12, since 2019") == {}
    assert Decimal("150") in figures("Qty 150")  # three digits: a figure


def test_the_same_figures_are_confirmed():
    ocr = "Subtotal 1,150.00\nHST 149.50\nTotal 1,299.50"
    reader = "| Subtotal | 1,150.00 |\n| HST (13%) | 149.50 |\n| Total | $1,299.50 |"
    result = compare_figures(ocr, reader)
    assert (result.confirmed, result.figures, result.first_figures) == (3, 4, 3)  # 13% only in the reader's
    assert result.only_reader == ["13%"]
    assert result.differ == [] and result.only_first == []


def test_a_figure_read_two_ways_is_paired():
    result = compare_figures("Total 1,150.00  Freight 75.00", "Total 1,105.00  Freight 75.00  Ref 88231")
    assert result.differ == [("1,105.00", "1,150.00")]  # (page reader, first reading)
    assert result.only_reader == ["88231"] and result.only_first == []
    assert result.confirmed == 1 and result.share == 1 / 3


def test_nothing_to_compare():
    result = compare_figures("", "")
    assert result.share is None and result.to_dict()["figures"] == 0
