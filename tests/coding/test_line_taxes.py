"""The tax on each line, tax by tax (``taxmath.invoice_taxes``): which tax was charged on a line and how much, read
off the line or worked out, recovered on the pay item or kept in the line's expense, and the amount keyed into JD
Edwards for it. Every invoice here is made up.

One rounding rule with the rest of the code: every case is held (``_taxes``) to the posting's line amounts
(``tax.build_gl_distribution``), to the tax the JD Edwards pay item takes (``jde_voucher._prepare``) and to the Tax
check's rows (``math_check``)."""

from __future__ import annotations

import random
import time

import pytest

from ap_coder.coding import money
from ap_coder.coding.tax import (
    EXPENSE_SEPARATE,
    EXPENSE_TO_LINE,
    RECOVERABLE,
    TaxRateTable,
    TaxSetup,
    TaxTreatment,
    build_gl_distribution,
    load_tax_mapping,
    tax_shares,
)
from ap_coder.coding.taxmath import (
    INSIDE,
    OWN_LINE,
    RATE,
    READ,
    RECOVERED,
    SHARE,
    invoice_taxes,
    line_taxes,
    math_check,
    tax_check_lines,
)
from ap_coder.coding.taxmath import money as shown
from ap_coder.core.schema import InvoiceCoding
from ap_coder.erp import jde_settings, jde_voucher
from tests.conftest import DATA
from tests.erp.test_jde_gl_mode import coded, doc, line, tl

MEALS = "7810"
RATES = TaxRateTable.load(DATA / "canada_tax_rates.csv")


def _setup(meals=(), **treatments):
    """The department's Tax setup: GST and HST recovered, PST and QST kept in the expense line; ``treatments``
    change one (QST=RECOVERABLE)."""
    mapping = load_tax_mapping(DATA / "tax_gl_mapping.csv")
    mapping["QST"] = TaxTreatment("QST", EXPENSE_TO_LINE)
    for tax_type, treatment in treatments.items():
        mapping[tax_type] = TaxTreatment(tax_type, treatment, "2320" if treatment == RECOVERABLE else "6990")
    return TaxSetup(RATES, mapping, meals_gl_codes=frozenset(meals))


def _keyed_by_hand():
    """JD Edwards as the department keys it: the tax on the pay item under code V, one tax area."""
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX, default_bu="0021061010")
    for province in settings.tax_map:
        settings.tax_map[province] = jde_settings.TaxArea("CDNGST", "V")
    return settings


def _taxes(d, setup=None):
    """``invoice_taxes`` of a made-up invoice, held to the rest of the code: the keyed amounts are the posting's
    line amounts, the recovered taxes are what the pay item takes, each tax's sum is its row of the Tax check, and
    the lines' shares of a tax add up to the printed tax to the cent."""
    setup = setup or _setup()
    coding = InvoiceCoding.model_validate(d)
    found = invoice_taxes(coding, setup)
    posting = build_gl_distribution(coding, setup)
    expense = [e for e in posting if e["kind"] == "expense"]
    assert [ln.keyed for ln in found.lines] == [e["amount"] for e in expense]
    assert [ln.inside for ln in found.lines] == [e["non_recoverable_tax"] for e in expense]
    assert [ln.printed for ln in found.lines] == [e["net_amount"] for e in expense]
    claimed = [e["amount"] for e in posting if e["kind"] == "tax" and not e["non_recoverable_tax"]]
    assert money.add_up(claimed) == found.recovered
    voucher = jde_voucher._prepare(coded(d, setup), _keyed_by_hand(), "10023")
    if voucher.recoverable_types:
        assert voucher.stam == voucher.recoverable == money.in_cents(found.recovered)
    else:  # JD Edwards works out no tax on this voucher (code E): the tax recovered is one of its G/L lines
        own = [e["cents"] for e in voucher.entries if e["kind"] == "tax" and not e["non_recoverable_tax"]]
        assert sum(own) == money.in_cents(found.recovered)
    rows = [r for r in math_check(coding, setup)["rows"] if r["kind"] in ("tax", "selfassess")]
    assert [(c.kind, c.name, c.ok, shown(c.base), c.rate_text) for c in found.check] == [
        (r["kind"], r["what"], r["ok"], r["base"], r["rate"]) for r in rows
    ]
    for check, row in zip(found.check, rows, strict=True):
        assert row["expected"] == ("—" if check.expected is None else shown(check.expected))
        assert row["charged"] == shown(check.printed)
        if check.kind == "tax":
            amounts = [t.worked_out for ln in found.lines for t in ln.taxes if t.tax_type == check.tax_type]
            assert money.add_up(amounts) == check.worked_out == money.half_up(check.printed - check.difference)
            posted = [check.recovered, check.inside, check.own_line]
            assert money.add_up(posted) == check.printed  # every cent of the printed tax goes somewhere
            if check.how == SHARE:
                assert check.difference == 0.0
    return found


def _lines(found):
    """Each line as the screen would say it: (number, label, [(tax, amount, source, where)], keyed)."""
    return [(ln.line_number, ln.label, [(t.name, t.amount, t.source, t.where) for t in ln.taxes], ln.keyed)
            for ln in found.lines]  # fmt: skip


FOUR = [3850.0, 2400.0, 1275.0, 96.4]  # 7,621.40


def _four(taxes, tax_lines, prov="AB", **kw):
    return doc([line(n, a, "7620", taxes) for n, a in enumerate(FOUR, 1)], tax_lines, prov=prov, **kw)


# --- Every line the same tax -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prov", "tax", "rate", "printed", "each"),
    [
        ("AB", "GST", 0.05, 381.07, [192.50, 120.00, 63.75, 4.82]),
        ("ON", "HST", 0.13, 990.78, [500.50, 312.00, 165.75, 12.53]),
        ("NB", "HST", 0.15, 1143.21, [577.50, 360.00, 191.25, 14.46]),
    ],
)
def test_every_line_the_same_tax_shows_its_share_of_the_one_tax_printed(prov, tax, rate, printed, each):
    found = _taxes(_four([tax], [tl(tax, "" if tax == "GST" else prov, rate, 7621.4, printed)], prov))
    assert _lines(found) == [
        (n + 1, tax, [(tax, each[n], SHARE, RECOVERED)], FOUR[n]) for n in range(4)
    ]  # fmt: skip
    assert not any(ln.unusual for ln in found.lines) and found.usual_label == tax
    assert all(t.rate == rate and t.read is None and t.recovered == t.amount for ln in found.lines for t in ln.taxes)
    [check] = found.check
    assert check.text == f"{tax}: lines taxed 7,621.40 × {rate * 100:g}% = {printed:,.2f} · printed {printed:,.2f} ✓"
    assert check.goes == "recovered: on the pay item" and check.recovered == found.recovered == printed
    assert not found.two_tax_areas and not found.tax_included and found.notes == ()


def test_the_two_short_ways_in_give_the_same_lines_and_the_same_sums():
    d = _four(["GST"], [tl("GST", "", 0.05, 7621.4, 381.07)])
    coding, setup = InvoiceCoding.model_validate(d), _setup()
    assert line_taxes(coding, setup) == invoice_taxes(coding, setup).lines
    assert tax_check_lines(coding, setup) == invoice_taxes(coding, setup).check


# --- A provincial tax kept in the expense, or recovered ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("prov", "rate", "name", "pst", "keyed"),
    [("BC", 0.07, "PST", [70.0, 10.5], [1070.0, 160.5]), ("SK", 0.06, "PST", [60.0, 9.0], [1060.0, 159.0]),
     ("MB", 0.07, "RST", [70.0, 10.5], [1070.0, 160.5])],
)  # fmt: skip
def test_gst_and_pst_are_two_amounts_and_the_pst_is_keyed_inside_the_line(prov, rate, name, pst, keyed):
    d = doc([line(1, 1000.0, "7410", ["GST", "PST"]), line(2, 150.0, "7410", ["GST", "PST"])],
            [tl("GST", "", 0.05, 1150.0, 57.5), tl("PST", prov, rate, 1150.0, sum(pst))], prov=prov)  # fmt: skip
    found = _taxes(d)
    assert _lines(found) == [
        (1, f"GST + {name}", [("GST", 50.0, SHARE, RECOVERED), (name, pst[0], SHARE, INSIDE)], keyed[0]),
        (2, f"GST + {name}", [("GST", 7.5, SHARE, RECOVERED), (name, pst[1], SHARE, INSIDE)], keyed[1]),
    ]
    one = found.lines[0]
    assert (one.printed, one.inside, one.keyed) == (1000.0, pst[0], keyed[0])
    assert [(t.recovered, t.inside) for t in one.taxes] == [(50.0, 0.0), (0.0, pst[0])]
    assert (found.recovered, found.inside) == (57.5, sum(pst))
    assert [c.goes for c in found.check] == ["recovered: on the pay item", "not recovered: inside the lines"]


def test_gst_and_qst_with_the_qst_kept_in_the_expense():
    d = doc([line(1, 1000.0, "7410", ["GST", "QST"]), line(2, 240.0, "7410", ["GST", "QST"])],
            [tl("GST", "", 0.05, 1240.0, 62.0), tl("QST", "QC", 0.09975, 1240.0, 123.69)], prov="QC")  # fmt: skip
    found = _taxes(d)
    assert _lines(found) == [
        (1, "GST + QST", [("GST", 50.0, SHARE, RECOVERED), ("QST", 99.75, SHARE, INSIDE)], 1099.75),
        (2, "GST + QST", [("GST", 12.0, SHARE, RECOVERED), ("QST", 23.94, SHARE, INSIDE)], 263.94),
    ]
    assert (found.recovered, found.inside) == (62.0, 123.69)
    assert found.check[1].text == "QST: lines taxed 1,240.00 × 9.975% = 123.69 · printed 123.69 ✓"


def test_gst_and_qst_with_the_qst_recovered_by_the_setup():
    d = doc([line(1, 1000.0, "7410", ["GST", "QST"]), line(2, 240.0, "7410", ["GST", "QST"])],
            [tl("GST", "", 0.05, 1240.0, 62.0), tl("QST", "QC", 0.09975, 1240.0, 123.69)], prov="QC")  # fmt: skip
    found = _taxes(d, _setup(QST=RECOVERABLE))
    assert _lines(found) == [
        (1, "GST + QST", [("GST", 50.0, SHARE, RECOVERED), ("QST", 99.75, SHARE, RECOVERED)], 1000.0),
        (2, "GST + QST", [("GST", 12.0, SHARE, RECOVERED), ("QST", 23.94, SHARE, RECOVERED)], 240.0),
    ]
    assert (found.recovered, found.inside) == (185.69, 0.0)


def test_a_pst_posted_to_its_own_account_is_neither_on_the_pay_item_nor_in_the_line():
    d = doc([line(1, 1000.0, "7410", ["GST", "PST"])],
            [tl("GST", "", 0.05, 1000.0, 50.0), tl("PST", "BC", 0.07, 1000.0, 70.0)], prov="BC")  # fmt: skip
    found = _taxes(d, _setup(PST=EXPENSE_SEPARATE))
    assert _lines(found) == [
        (1, "GST + PST", [("GST", 50.0, SHARE, RECOVERED), ("PST", 70.0, SHARE, OWN_LINE)], 1000.0)
    ]
    pst = found.lines[0].taxes[1]
    assert (pst.recovered, pst.inside, pst.own_line) == (0.0, 0.0, 70.0)
    assert found.check[1].goes == "not recovered: on its own account"


# --- Lines that do not carry the invoice's usual tax ------------------------------------------------------------------


def test_lines_exempt_or_zero_rated_among_taxed_lines_show_no_tax_and_are_marked_unusual():
    d = doc([line(1, 1000.0, "7410", ["GST"], desc="Shelving"),
             line(2, 200.0, "7410", status="zero_rated", desc="Basic groceries"),
             line(3, 300.0, "7410", status="exempt", desc="Course fees")],
            [tl("GST", "", 0.05, 1000.0, 50.0)], prov="AB")  # fmt: skip
    found = _taxes(d)
    assert _lines(found) == [
        (1, "GST", [("GST", 50.0, SHARE, RECOVERED)], 1000.0), (2, "Zero-rated", [], 200.0), (3, "Exempt", [], 300.0),
    ]  # fmt: skip
    assert [ln.unusual for ln in found.lines] == [False, True, True] and found.usual_code == "GST"
    assert found.check[0].text == "GST: lines taxed 1,000.00 (1 of 3 lines) × 5% = 50.00 · printed 50.00 ✓"
    assert (found.check[0].count, found.check[0].of) == (1, 3)


def test_a_line_that_is_gst_only_pst_exempt_carries_the_gst_alone():
    d = doc([line(1, 1000.0, "7410", ["GST", "PST"], desc="Desk chairs"),
             line(2, 150.0, "7410", ["GST"], exempt=["PST"], desc="Delivery and assembly")],
            [tl("GST", "", 0.05, 1150.0, 57.5), tl("PST", "BC", 0.07, 1000.0, 70.0)], prov="BC")  # fmt: skip
    found = _taxes(d)
    assert _lines(found) == [
        (1, "GST + PST", [("GST", 50.0, SHARE, RECOVERED), ("PST", 70.0, SHARE, INSIDE)], 1070.0),
        (2, "GST only · PST exempt", [("GST", 7.5, SHARE, RECOVERED)], 150.0),
    ]
    two = found.lines[1]
    assert two.unusual and two.code == "GST~PST" and two.due == () and two.inside == 0.0
    assert found.check[1].text == "PST: lines taxed 1,000.00 (1 of 2 lines) × 7% = 70.00 · printed 70.00 ✓"
    assert found.check[1].goes == "not recovered: inside line 1"


def test_a_pst_due_and_not_charged_is_shown_as_due_and_is_in_no_amount_keyed():
    """British Columbia, a supplier in Alberta: GST charged, the PST to self-assess. The line says so, with the
    amount at the official rate; nothing of it is on the invoice, so nothing of it is keyed for the line."""
    d = doc([line(1, 500.0, "7410", ["GST"]), line(2, 100.1, "7410", ["GST"])],
            [tl("GST", "", 0.05, 600.1, 30.01)], prov="BC", supplier_province="AB")  # fmt: skip
    found = _taxes(d)
    assert _lines(found) == [
        (1, "GST only · PST not charged (self-assess)", [("GST", 25.0, SHARE, RECOVERED)], 500.0),
        (2, "GST only · PST not charged (self-assess)", [("GST", 5.01, SHARE, RECOVERED)], 100.1),
    ]
    assert [[(t.name, t.rate, t.amount, t.self_assess) for t in ln.due] for ln in found.lines] == [
        [("PST", 0.07, 35.0, True)], [("PST", 0.07, 7.01, True)],
    ]  # fmt: skip
    due = found.check[1]
    assert (due.kind, due.expected, due.printed, due.lines, due.goes) == (
        "selfassess",
        42.01,
        0.0,
        (1, 2),
        "self-assess",
    )
    assert due.text == "PST due on 600.10 and not charged: self-assess 42.01"


def test_a_tax_charged_that_no_line_is_marked_with_is_shared_and_said():
    d = doc([line(1, 600.0, "7410"), line(2, 400.0, "7410")], [tl("GST", "", 0.05, 1000.0, 50.0)], prov="AB")
    found = _taxes(d)
    assert [[(t.amount, t.marked, t.source) for t in ln.taxes] for ln in found.lines] == [
        [(30.0, False, SHARE)], [(20.0, False, SHARE)],
    ]  # fmt: skip
    assert not found.check[0].ok and found.check[0].text == "GST: 50.00 printed, but no line is marked GST"


# --- Meals: half the recoverable tax stays in the line ----------------------------------------------------------------


def test_a_meals_line_keeps_half_its_gst_inside_and_the_other_half_is_recovered():
    d = doc([line(1, 200.0, MEALS, ["GST"], desc="Team lunch"), line(2, 100.0, "7410", ["GST"], desc="Room")],
            [tl("GST", "", 0.05, 300.0, 15.0)], prov="AB")  # fmt: skip
    found = _taxes(d, _setup(meals=(MEALS,)))
    lunch, room = found.lines
    assert lunch.meals and not room.meals
    assert [(t.amount, t.where, t.recovered, t.inside) for t in lunch.taxes] == [(10.0, RECOVERED, 5.0, 5.0)]
    assert [(t.amount, t.where, t.recovered, t.inside) for t in room.taxes] == [(5.0, RECOVERED, 5.0, 0.0)]
    assert (lunch.keyed, room.keyed, found.recovered, found.inside) == (205.0, 100.0, 10.0, 5.0)
    assert found.check[0].goes == "recovered: on the pay item, less 5.00 on meals (50%) kept inside line 1"


@pytest.mark.parametrize(
    ("qst", "parts", "keyed", "recovered"),
    [(RECOVERABLE, [(4.37, 2.18, 2.19), (8.72, 4.36, 4.36)], 93.98, 6.54),
     (EXPENSE_TO_LINE, [(4.37, 2.18, 2.19), (8.72, 0.0, 8.72)], 98.34, 2.18)],
)  # fmt: skip
def test_a_quebec_meal_halves_each_tax_that_is_recovered_tax_by_tax(qst, parts, keyed, recovered):
    """A 87.43 dinner in Quebec, GST 4.37 and QST 8.72: the claim is half of each, never rounded up (2.18 of 4.37);
    a QST the setup does not recover is all in the line."""
    d = doc([line(1, 87.43, MEALS, ["GST", "QST"], desc="Client dinner")],
            [tl("GST", "", 0.05, 87.43, 4.37), tl("QST", "QC", 0.09975, 87.43, 8.72)], prov="QC")  # fmt: skip
    found = _taxes(d, _setup(meals=(MEALS,), QST=qst))
    assert [(t.amount, t.recovered, t.inside) for t in found.lines[0].taxes] == parts
    assert (found.lines[0].keyed, found.recovered) == (keyed, recovered)


def test_a_tip_takes_none_of_the_tax_whatever_its_code():
    d = doc([line(1, 100.0, MEALS, ["GST", "PST"], desc="Dinner for 4"),
             line(2, 15.0, "7820", ["GST", "PST"], desc="Tip")],
            [tl("GST", "", 0.05, 100.0, 5.0), tl("PST", "SK", 0.06, 100.0, 6.0)], prov="SK")  # fmt: skip
    found = _taxes(d, _setup(meals=(MEALS,)))
    dinner, tip = found.lines
    assert [(t.amount, t.recovered, t.inside) for t in dinner.taxes] == [(5.0, 2.5, 2.5), (6.0, 0.0, 6.0)]
    assert tip.tip and [(t.amount, t.marked) for t in tip.taxes] == [(0.0, True), (0.0, True)]
    assert (dinner.keyed, tip.keyed) == (108.5, 15.0)
    assert tip.notes == ("A tip: none of the tax printed is put on it.",)


# --- Signs, freight, currencies ---------------------------------------------------------------------------------------


def test_a_discount_line_takes_its_share_below_zero():
    d = doc([line(1, 1000.0, "7410", ["GST", "PST"]), line(2, -100.0, "7410", ["GST", "PST"], desc="Discount 10%")],
            [tl("GST", "", 0.05, 900.0, 45.0), tl("PST", "BC", 0.07, 900.0, 63.0)], prov="BC")  # fmt: skip
    assert _lines(_taxes(d)) == [
        (1, "GST + PST", [("GST", 50.0, SHARE, RECOVERED), ("PST", 70.0, SHARE, INSIDE)], 1070.0),
        (2, "GST + PST", [("GST", -5.0, SHARE, RECOVERED), ("PST", -7.0, SHARE, INSIDE)], -107.0),
    ]


def test_freight_taxed_differently_from_the_goods_and_the_gst_printed_twice():
    """Saskatchewan: goods with GST and PST, freight the supplier charged GST only on (as the invoice shows it:
    whether freight carries PST is the supplier's and the reviewer's to say, not worked out here), the GST printed
    once for the goods and once for the freight: each line ends with the GST of its own amount."""
    d = doc([line(1, 1200.0, "7410", ["GST", "PST"], desc="Filing cabinets"),
             line(2, 85.0, "7440", ["GST"], exempt=["PST"], desc="Freight")],
            [tl("GST", "", 0.05, 1200.0, 60.0), tl("GST", "", 0.05, 85.0, 4.25), tl("PST", "SK", 0.06, 1200.0, 72.0)],
            prov="SK")  # fmt: skip
    found = _taxes(d)
    assert _lines(found) == [
        (1, "GST + PST", [("GST", 60.0, SHARE, RECOVERED), ("PST", 72.0, SHARE, INSIDE)], 1272.0),
        (2, "GST only · PST exempt", [("GST", 4.25, SHARE, RECOVERED)], 85.0),
    ]
    assert found.check[0].text == "GST: lines taxed 1,285.00 × 5% = 64.25 · printed 64.25 ✓"
    assert len(found.areas) == 1 and found.areas[0].tax == 64.25  # one tax area: the GST, printed twice


def test_a_credit_note_reverses_every_amount():
    d = doc([line(1, -1000.0, "7410", ["GST", "PST"]), line(2, -150.0, "7410", ["GST"], exempt=["PST"])],
            [tl("GST", "", 0.05, -1150.0, -57.5), tl("PST", "BC", 0.07, -1000.0, -70.0)], prov="BC")  # fmt: skip
    found = _taxes(d)
    assert _lines(found) == [
        (1, "GST + PST", [("GST", -50.0, SHARE, RECOVERED), ("PST", -70.0, SHARE, INSIDE)], -1070.0),
        (2, "GST only · PST exempt", [("GST", -7.5, SHARE, RECOVERED)], -150.0),
    ]
    assert (found.recovered, found.inside) == (-57.5, -70.0)
    assert found.check[0].text == "GST: lines taxed -1,150.00 × 5% = -57.50 · printed -57.50 ✓"


def test_a_credit_note_whose_tax_matches_no_rate_says_more_or_less_in_the_size_of_its_amounts():
    d = doc([line(1, -1000.0, "7410", ["GST"])], [tl("GST", "", 0.05, -1000.0, -55.0)], prov="AB")
    found = _taxes(d)
    assert found.check[0].text == (
        "GST: lines taxed -1,000.00 × 5% = -50.00 · printed -55.00 · 5.00 more than the rate gives, "
        "on no line in particular"
    )
    assert found.lines[0].taxes[0].amount == -50.0 and found.check[0].difference == -5.0


def test_a_us_dollar_invoice_is_worked_out_in_us_dollars():
    """Every amount is in the invoice's currency. What JD Edwards holds in Canadian dollars is the exchange rate's:
    not worked out here."""
    found = _taxes(_four(["GST"], [tl("GST", "", 0.05, 7621.4, 381.07)], currency="USD"))
    assert found.currency == "USD"
    assert [ln.taxes[0].amount for ln in found.lines] == [192.50, 120.00, 63.75, 4.82]
    assert [ln.keyed for ln in found.lines] == FOUR and found.recovered == 381.07


def test_an_invoice_with_no_tax_at_all():
    d = doc([line(1, 420.0, "7410"), line(2, 80.0, "7410")], [], prov="AB", supplier_province="OUTSIDE_CANADA")
    found = _taxes(d)
    assert _lines(found) == [(1, "No tax charged", [], 420.0), (2, "No tax charged", [], 80.0)]
    assert found.check == [] and found.areas == () and (found.recovered, found.inside) == (0.0, 0.0)
    assert not any(ln.unusual for ln in found.lines)


def test_recovered_is_the_tax_setups_word_where_jd_edwards_works_out_no_tax_it_is_a_gl_line_not_the_pay_item():
    """British Columbia, GST only, every line exempt from the PST: JD Edwards takes the voucher with code E and the
    GST as a G/L line of its own (``jde_voucher._tax_area_code``). The line still says the GST is recovered: which box
    it is keyed in is the voucher's to say (``jde_review.voucher_view``), not worked out here."""
    d = doc([line(1, 609.2, "7410", ["GST"], exempt=["PST"], desc="Legal fees")], [tl("GST", "", 0.05, 609.2, 30.46)],
            prov="BC")  # fmt: skip
    found = _taxes(d)
    assert _lines(found) == [(1, "GST only · PST exempt", [("GST", 30.46, SHARE, RECOVERED)], 609.2)]
    voucher = jde_voucher._prepare(coded(d, _setup()), _keyed_by_hand(), "10023")
    assert voucher.tax_code == "E" and voucher.recoverable == 0 and found.recovered == 30.46


# --- The odd cents, and a printed tax that is not right ---------------------------------------------------------------


def test_thirty_lines_and_one_tax_total_add_up_to_the_cent_with_the_odd_cents_where_the_posting_puts_them():
    """30 lines of 33.33 and GST 50.00: 1.67 a line would be 50.10. The ten cents come off one line, the one
    ``money.split`` gives them to: there is one rule for the odd cents, the posting's."""
    d = doc([line(n, 33.33, "7410", ["GST"]) for n in range(1, 31)], [tl("GST", "", 0.05, 999.9, 50.0)], prov="AB")
    found = _taxes(d)
    amounts = [ln.taxes[0].amount for ln in found.lines]
    assert amounts == money.split(50.0, [33.33] * 30) == [1.57, *[1.67] * 29]
    assert money.add_up(amounts) == 50.0 and found.check[0].ok and found.check[0].difference == 0.0

    uneven = [round(7.77 + 3.31 * n + (n % 4) * 0.13, 2) for n in range(30)]
    base = money.add_up(uneven)
    printed = money.times(base, 0.05)
    d = doc(
        [line(n, a, "7410", ["GST"]) for n, a in enumerate(uneven, 1)], [tl("GST", "", 0.05, base, printed)], prov="AB"
    )
    found = _taxes(d)
    coding = InvoiceCoding.model_validate(d)
    assert [ln.taxes[0].amount for ln in found.lines] == [
        s for _, s in tax_shares(coding.line_items, coding.tax_lines[0])
    ]
    assert money.add_up(ln.taxes[0].amount for ln in found.lines) == printed


def test_a_printed_tax_a_cent_off_is_rounding_and_the_lines_add_up_to_what_is_printed():
    found = _taxes(_four(["GST"], [tl("GST", "", 0.05, 7621.4, 381.08)]))
    assert [ln.taxes[0].amount for ln in found.lines] == [192.51, 120.00, 63.75, 4.82]
    [check] = found.check
    assert check.ok and check.how == SHARE and check.worked_out == 381.08
    assert check.text == "GST: lines taxed 7,621.40 × 5% = 381.07 · printed 381.08 ✓ (0.01 of rounding)"


def test_a_printed_tax_that_matches_no_rate_shows_the_rate_on_each_line_and_says_the_difference():
    """385.00 printed where 5% gives 381.07: no line is given a share of the 3.93. Each line shows the line x the
    rate, and the tax's sum says the difference, on no line in particular. What is keyed is still the printed tax."""
    found = _taxes(_four(["GST"], [tl("GST", "", 0.05, 7621.4, 385.0)]))
    assert [(ln.taxes[0].amount, ln.taxes[0].source) for ln in found.lines] == [
        (192.50, RATE), (120.00, RATE), (63.75, RATE), (4.82, RATE),
    ]  # fmt: skip
    [check] = found.check
    assert (check.ok, check.how, check.expected, check.printed) == (False, RATE, 381.07, 385.0)
    assert (check.worked_out, check.difference, check.lines) == (381.07, 3.93, ())
    assert check.text == ("GST: lines taxed 7,621.40 × 5% = 381.07 · printed 385.00 · 3.93 more than the rate gives, "
                          "on no line in particular")  # fmt: skip
    assert "ask for a corrected invoice" in check.why
    assert found.recovered == 385.0 == money.add_up(ln.taxes[0].recovered for ln in found.lines)


def test_a_tax_short_by_one_lines_amount_names_the_line():
    """GST 50.00 printed, every line marked: it adds up without the 200.00 line, which the sum points at."""
    d = doc([line(1, 1000.0, "7410", ["GST"]), line(2, 200.0, "7410", ["GST"], desc="Basic groceries")],
            [tl("GST", "", 0.05, 1000.0, 50.0)], prov="AB")  # fmt: skip
    [check] = _taxes(d).check
    assert (check.ok, check.difference, check.lines) == (False, -10.0, (2,))
    assert check.text == (
        "GST: lines taxed 1,200.00 × 5% = 60.00 · printed 50.00 · 10.00 less than the rate gives · "
        "Line 2 (Basic groceries, 200.00) may not carry GST: the GST charged adds up without it."
    )


def test_lines_whose_prices_include_the_tax_show_their_share_and_say_it_is_in_the_amount():
    """Lines of 105.00 and 210.00 that add up to the total, GST 15.00 printed: the line x the rate would be a tax on
    a tax. What can be said honestly is each line's share of the printed tax, and that it is in the amount printed."""
    d = doc([line(1, 105.0, "7410", ["GST"]), line(2, 210.0, "7410", ["GST"])], [tl("GST", "", 0.05, 300.0, 15.0)],
            prov="AB", subtotal=300.0, grand_total=315.0)  # fmt: skip
    found = _taxes(d)
    assert found.tax_included and "seem to include the tax" in found.notes[0]
    assert [(ln.taxes[0].amount, ln.taxes[0].source) for ln in found.lines] == [(5.0, SHARE), (10.0, SHARE)]
    [check] = found.check
    assert not check.ok and check.difference == 0.0
    assert check.text.endswith("0.75 less than the rate gives: the lines' prices seem to include the tax")


# --- A tax amount read on the line ------------------------------------------------------------------------------------


def _read(*printed):
    lines = [{**line(n, a, "7410", ["HST"] if hst else []), **({"taxes_read": read} if read is not None else {})}
             for n, (a, hst, read) in enumerate(printed, 1)]  # fmt: skip
    base = sum(a for a, hst, _ in printed if hst)
    return doc(lines, [tl("HST", "ON", 0.13, base, round(base * 0.13, 2))], prov="ON")


def test_a_tax_read_on_each_line_that_agrees_is_the_amount_shown():
    found = _taxes(_read((200.0, True, {"HST": 26.0}), (100.0, True, {"HST": 13.01})))
    taxes = [t for ln in found.lines for t in ln.taxes]
    assert [(t.amount, t.source, t.read, t.worked_out, t.how, t.difference) for t in taxes] == [
        (26.0, READ, 26.0, 26.0, SHARE, 0.0), (13.01, READ, 13.01, 13.0, SHARE, 0.0),  # a cent apart is rounding
    ]  # fmt: skip
    assert all(ln.notes == () for ln in found.lines)
    assert found.check[0].read == 39.01 and found.check[0].worked_out == 39.0


def test_a_tax_read_on_a_line_that_disagrees_is_said_on_that_line():
    found = _taxes(_read((200.0, True, {"HST": 26.0}), (100.0, True, {"HST": 12.0}), (50.0, False, {"HST": 6.5}),
                         (40.0, False, {"HST": 0.0}), (60.0, True, None)))  # fmt: skip
    one, two, three, four, five = found.lines
    assert (one.taxes[0].difference, one.notes) == (0.0, ())
    assert (two.taxes[0].amount, two.taxes[0].worked_out, two.taxes[0].difference) == (12.0, 13.0, -1.0)
    assert two.notes == ("HST printed on this line is 12.00; worked out, 13.00: a difference of -1.00.",)
    assert [(t.amount, t.marked, t.worked_out, t.difference) for t in three.taxes] == [(6.5, False, 0.0, 6.5)]
    assert three.notes == ("HST 6.50 is printed on this line, but its tax code does not carry HST.",)
    assert four.taxes == () and four.notes == ()  # "0.00" printed on a line that carries none: nothing to say
    assert (five.taxes[0].source, five.taxes[0].read, five.taxes[0].amount) == (SHARE, None, 7.8)
    assert found.check[0].read == 44.5


# --- Two tax areas on one invoice -------------------------------------------------------------------------------------


def test_gst_lines_and_hst_lines_on_one_invoice_are_two_tax_areas_each_line_at_its_own_rate():
    d = doc([line(1, 2400.0, "7430", ["GST"], desc="Printed binders - Calgary office"),
             line(2, 600.0, "7430", ["GST"], desc="Business cards - Calgary office"),
             line(3, 1000.0, "7430", ["HST"], desc="Banners and signs - Toronto event")],
            [tl("GST", "", 0.05, 3000.0, 150.0), tl("HST", "ON", 0.13, 1000.0, 130.0)], prov="AB")  # fmt: skip
    found = _taxes(d)
    assert [[(t.name, t.province, t.rate, t.amount) for t in ln.taxes] for ln in found.lines] == [
        [("GST", "", 0.05, 120.0)], [("GST", "", 0.05, 30.0)], [("HST", "ON", 0.13, 130.0)],
    ]  # fmt: skip
    assert found.two_tax_areas and [ln.unusual for ln in found.lines] == [False, False, True]
    assert [(a.tax_type, a.province, a.rate, a.taxable, a.tax, a.line_numbers) for a in found.areas] == [
        ("GST", "", 0.05, 3000.0, 150.0, (1, 2)), ("HST", "ON", 0.13, 1000.0, 130.0, (3,)),
    ]  # fmt: skip
    assert found.notes == ("Two tax areas on one invoice: GST 5% 150.00 on 3,000.00, HST ON 13% 130.00 on 1,000.00.",)


def test_hst_at_two_rates_gives_each_line_its_own_rate_when_the_amounts_tell_which():
    d = doc([line(1, 1000.0, "7430", ["HST"]), line(2, 500.0, "7430", ["HST"]), line(3, 250.0, "7430", ["HST"])],
            [tl("HST", "ON", 0.13, 1250.0, 162.5), tl("HST", "NB", 0.15, 500.0, 75.0)], prov="ON")  # fmt: skip
    found = _taxes(d)
    assert [[(t.province, t.rate, t.amount, t.source) for t in ln.taxes] for ln in found.lines] == [
        [("ON", 0.13, 130.0, SHARE)], [("NB", 0.15, 75.0, SHARE)], [("ON", 0.13, 32.5, SHARE)],
    ]  # fmt: skip
    assert found.two_tax_areas
    assert [(a.province, a.rate, a.tax, a.line_numbers) for a in found.areas] == [
        ("ON", 0.13, 162.5, (1, 3)), ("NB", 0.15, 75.0, (2,)),
    ]  # fmt: skip
    assert found.check[0].text == "HST: 1,250.00 × 13% + 500.00 × 15% = 237.50 · printed 237.50 ✓"


def test_hst_at_two_rates_one_of_them_wrong_shows_that_rate_on_its_lines():
    d = doc([line(1, 1000.0, "7430", ["HST"]), line(2, 500.0, "7430", ["HST"])],
            [tl("HST", "ON", 0.13, 1000.0, 130.0), tl("HST", "NB", 0.15, 500.0, 80.0)], prov="ON")  # fmt: skip
    found = _taxes(d)
    assert [[(t.rate, t.amount, t.source) for t in ln.taxes] for ln in found.lines] == [
        [(0.13, 130.0, SHARE)], [(0.15, 75.0, RATE)],
    ]  # fmt: skip
    assert (found.check[0].ok, found.check[0].how, found.check[0].difference) == (False, RATE, 5.0)


def test_a_tax_kept_in_the_expense_at_two_rates_posts_each_rate_on_the_lines_it_was_charged_on():
    """B-2481: each tax line of a tax kept in the expense was spread by amount over every line marked with that tax:
    BC food 1,000.00 (PST 7%, 70.00) and wine 200.00 (PST 10%, 20.00) posted 1,075.00 and 215.00. The lines' amounts
    tell which rate is on which line, so each line takes its own rate's tax: 1,070.00 and 220.00."""
    d = doc([line(1, 1000.0, "5100", ["GST", "PST"], desc="Catering - food"),
             line(2, 200.0, "5300", ["GST", "PST"], desc="Wine")],
            [tl("GST", "", 0.05, 1200.0, 60.0), tl("PST", "BC", 0.07, 1000.0, 70.0),
             tl("PST", "BC", 0.10, 200.0, 20.0)], prov="BC")  # fmt: skip
    found = _taxes(d)
    posting = build_gl_distribution(InvoiceCoding.model_validate(d), _setup())
    assert [(e["gl_code"], e["non_recoverable_tax"], e["amount"]) for e in posting if e["kind"] == "expense"] == [
        ("5100", 70.0, 1070.0), ("5300", 20.0, 220.0),
    ]  # fmt: skip
    assert [ln.keyed for ln in found.lines] == [1070.0, 220.0]
    # Lines of the same amount at the two rates can't be told apart: shared by amount over both, as before
    same = doc([line(1, 500.0, "5100", ["GST", "PST"]), line(2, 500.0, "5300", ["GST", "PST"])],
               [tl("GST", "", 0.05, 1000.0, 50.0), tl("PST", "BC", 0.07, 500.0, 35.0),
                tl("PST", "BC", 0.10, 500.0, 50.0)], prov="BC")  # fmt: skip
    assert [ln.keyed for ln in _taxes(same).lines] == [542.5, 542.5]


def test_hst_at_two_rates_on_lines_of_the_same_amount_is_reported_without_guessing_which_is_which():
    d = doc([line(1, 500.0, "7430", ["HST"]), line(2, 500.0, "7430", ["HST"])],
            [tl("HST", "ON", 0.13, 500.0, 65.0), tl("HST", "NB", 0.15, 500.0, 75.0)], prov="ON")  # fmt: skip
    found = _taxes(d)
    assert [[(t.province, t.rate, t.amount, t.source) for t in ln.taxes] for ln in found.lines] == [
        [("", None, 70.0, SHARE)], [("", None, 70.0, SHARE)],
    ]  # fmt: skip
    assert found.two_tax_areas and [a.line_numbers for a in found.areas] == [(), ()]


# --- Held to the posting on invoices of every kind, and fast ----------------------------------------------------------

REGIMES = {"AB": {"GST": 0.05}, "ON": {"HST": 0.13}, "NB": {"HST": 0.15}, "BC": {"GST": 0.05, "PST": 0.07},
           "SK": {"GST": 0.05, "PST": 0.06}, "MB": {"GST": 0.05, "PST": 0.07},
           "QC": {"GST": 0.05, "QST": 0.09975}}  # fmt: skip


def _made_up(rng):
    prov = rng.choice(sorted(REGIMES))
    regime = REGIMES[prov]
    sign = -1 if rng.random() < 0.1 else 1
    lines = []
    for n in range(1, rng.choice([1, 2, 3, 5, 9, 30]) + 1):
        amount = sign * round(rng.uniform(1, 2500), 2) * (-1 if rng.random() < 0.05 else 1)
        kind = rng.random()
        taxes = [] if kind < 0.1 else [t for t in regime if not (t in ("PST", "QST") and kind < 0.3)]
        lines.append(line(n, amount, rng.choice(["7410", "7620", MEALS]), taxes, status="exempt" if not taxes else "",
                          exempt=[t for t in regime if t not in taxes] if taxes and rng.random() < 0.5 else [],
                          desc=rng.choice(["Paper", "Licences", "Dinner", "Tip", "Freight"])))  # fmt: skip
    tax_lines = []
    for tax, rate in regime.items():
        base = round(sum(li["amount"] for li in lines if tax in li["taxes_applied"]), 2)
        off = rng.choice([0, 0, 0, 0, 0.01, -0.01, 3.93, -12.0])
        if base:
            tax_lines.append(tl(tax, "" if tax == "GST" else prov, rate, base, round(base * rate + off, 2)))
    return doc(lines, tax_lines, prov=prov, currency=rng.choice(["CAD", "CAD", "USD"]))


@pytest.mark.parametrize("qst", [EXPENSE_TO_LINE, RECOVERABLE])
def test_on_made_up_invoices_of_every_kind_the_lines_agree_with_the_posting_and_the_pay_item(qst):
    rng = random.Random(2169)
    setup = _setup(meals=(MEALS,), QST=qst)
    for _ in range(300):
        _taxes(_made_up(rng), setup)


def test_130_lines_are_worked_out_in_well_under_50_ms_without_reading_anything(monkeypatch):
    def no_reading(*args, **kwargs):
        raise AssertionError(f"invoice_taxes opened {args[:1]}")

    rng = random.Random(7)
    lines = [line(n, round(rng.uniform(5, 900), 2), rng.choice(["7410", MEALS]), ["GST", "PST"]) for n in range(1, 131)]
    base = round(sum(li["amount"] for li in lines), 2)
    setup = _setup(meals=(MEALS,))
    for off in (0.0, 3.93):  # the printed tax right, and not
        taxes = [
            tl("GST", "", 0.05, base, round(base * 0.05 + off, 2)),
            tl("PST", "BC", 0.07, base, round(base * 0.07, 2)),
        ]
        coding = InvoiceCoding.model_validate(doc(lines, taxes, prov="BC"))
        took = []
        with monkeypatch.context() as patched:
            patched.setattr("builtins.open", no_reading)
            patched.setattr("sqlite3.connect", no_reading)
            for _ in range(5):
                start = time.perf_counter()
                found = invoice_taxes(coding, setup)
                took.append(time.perf_counter() - start)
        assert len(found.lines) == 130 and min(took) < 0.025, f"{min(took) * 1000:.1f} ms"
