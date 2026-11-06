"""Tax and money worked out half up on the decimals printed, and one table of what counts as equal (D-183): the tax
checks, the review screen's Tax check, the totals, the GL distribution, the self-assessment and the splits."""

from __future__ import annotations

import datetime as dt
import json
import random
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import pytest

from ap_coder.coding import money, taxreturn
from ap_coder.coding.review import split_line
from ap_coder.coding.tax import build_gl_distribution, check_taxes
from ap_coder.coding.taxmath import math_check
from ap_coder.coding.terms import payment
from ap_coder.coding.validation import validate_coding
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup
from tests.conftest import SAMPLES

YEAR = (dt.date(2020, 1, 1), dt.date(2030, 12, 31))


def _coding(
    lines, taxes, *, province="ON", currency="CAD", subtotal=None, tax_total=None, total=None, date="2026-10-05"
):
    """lines: (amount, [taxes]); taxes: (type, rate, taxable, amount)."""
    subtotal = round(sum(a for a, _ in lines), 2) if subtotal is None else subtotal
    tax_total = round(sum(t[3] for t in taxes), 2) if tax_total is None else tax_total
    return InvoiceCoding.model_validate({
        "vendor_name": "Vendor", "invoice_number": "1", "invoice_date": date, "currency": currency,
        "supplier_province": province, "ship_to_province": province, "subtotal": subtotal,
        "gst_hst_registration_number": "123456782RT0001", "qst_registration_number": "1234567890TQ0001",
        "tax_lines": [{"tax_type": t, "province": "" if t == "GST" else province, "rate": r, "taxable_amount": b,
                       "tax_amount": a} for t, r, b, a in taxes],
        "tax_total": tax_total, "grand_total": round(subtotal + tax_total, 2) if total is None else total,
        "confidence_score": 1.0,
        "line_items": [{"line_number": i, "description": f"Line {i}", "quantity": 1, "unit_price": a, "amount": a,
                        "predicted_gl_code": "6010", "predicted_cost_center": "CC400", "taxes_applied": list(tx),
                        "reasoning_justification": ""} for i, (a, tx) in enumerate(lines, start=1)],
    })  # fmt: skip


def _row(m, what):
    return next(r for r in m["rows"] if r["what"] == what)


def _codes(findings):
    return {f.code for f in findings}


# --- Half up, on the decimals printed (B-1864) ---------------------------------------------------------------------


def test_tax_shares_and_sums_round_half_up_on_the_decimals_printed():
    # R10's two: Python's round on the float gave 0.12 and 0.01.
    assert money.times(2.50, 0.05) == 0.13 and money.times(0.30, 0.05) == 0.02
    assert money.times(1002.50, 0.05) == 50.13 and money.times(1000.50, 0.13) == 130.07
    assert money.times(1000.75, 0.06) == 60.05 and money.times(140, 0.09975) == 13.97
    assert money.times(118.5, 0.07) == 8.3
    assert money.times(-2.50, 0.05) == -0.13  # a credit note rounds as the invoice it reverses
    assert money.times(25, 0.1, 0) == 3 and money.times(15, 0.1, 0) == 2  # whole yen
    assert money.half_up(2.675) == 2.68 and money.half_up(-2.675) == -2.68 and money.half_up(1.005) == 1.01
    assert money.half_up(0.1 + 0.2) == 0.3 and money.half_up(2.5, 0) == 3
    assert money.add_up([1.001, 1.004]) == 2.01 and money.add_up([0.1, 0.2]) == 0.3 and money.add_up([]) == 0
    assert money.in_cents(10.005) == 1001 and money.in_cents(-10.005) == -1001 and money.in_cents(0.07) == 7
    # A split: each share half up, what is left over on the largest weight; the shares add up to the total.
    assert money.split(0.35, [1, 1]) == [0.17, 0.18] and money.split(1.0, [1, 1, 1]) == [0.34, 0.33, 0.33]
    assert money.split(-1.0, [-1, -1, -1]) == [-0.34, -0.33, -0.33] and money.split(5, [1, 2], 0) == [2, 3]
    assert money.split(0.05, [0, 0]) == [0.05, 0] and money.split(1.0, []) == []


def _decimals(x, places):
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)


@pytest.mark.parametrize("seed", range(4))
def test_the_quick_way_is_the_decimals_answer_always(seed):
    """A float rounded as a float only when it can't be a tie: every answer is what the decimals give, the sign of a
    nothing included (ties, a third decimal, credits, yen, a billion dollars and more)."""
    rng = random.Random(seed)

    def amount():
        kind = rng.random()
        if kind < 0.3:
            return round(rng.uniform(-5000, 5000), 2)
        if kind < 0.5:
            return round(rng.uniform(-50, 50), 3)
        if kind < 0.7:
            return rng.randint(-(10**6), 10**6) / 100 + rng.choice([0.005, -0.005])  # a tie
        if kind < 0.8:
            return round(rng.uniform(-1e12, 1e12), 2)
        return rng.choice([0.0, -0.0, 0.004, -0.004, 0.005, -0.005, 2.675, 1.005, 10.005])

    for _ in range(4000):
        a, places = amount(), rng.choice([0, 2, 2])
        rate = rng.choice([0.05, 0.13, 0.15, 0.06, 0.07, 0.09975, 0.5, 0.02, 0.333, 1.0, 0.0])
        assert repr(money.half_up(a, places)) == repr(float(_decimals(a, places))), (a, places)
        assert repr(money.times(a, rate, places)) == repr(
            float((Decimal(str(a)) * Decimal(str(rate))).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP))
        ), (a, rate, places)
        values = [amount() for _ in range(rng.randint(0, 8))]
        exact = sum((Decimal(str(v)) for v in values), Decimal(0))
        assert repr(money.add_up(values, places)) == repr(float(_decimals(exact, places))), (values, places)
        assert money.in_cents(a) == int(_decimals(a, 2).scaleb(2)), a


@pytest.mark.parametrize("seed", range(3))
def test_split_shares_always_add_up_to_the_total(seed):
    rng = random.Random(seed)
    for _ in range(300):
        weights = [round(rng.uniform(-50, 2000), 2) for _ in range(rng.randint(1, 7))]
        total = round(rng.uniform(-500, 500), rng.choice([0, 2]))
        places = rng.choice([0, 2])
        shares = money.split(total, weights, places)
        assert money.in_cents(sum(shares)) == money.in_cents(total)


def test_the_tax_checks_expect_the_tax_half_up():
    """1,002.50 at 5% is 50.125: the vendor charges 50.13. The Tax check expected 50.12, and a wrong GST's finding
    said so."""
    right = _coding([(1002.50, ["GST"])], [("GST", 0.05, 1002.50, 50.13)], province="AB")
    assert _row(math_check(right), "GST")["expected"] == "50.13"
    wrong = _coding([(1002.50, ["GST"])], [("GST", 0.05, 1002.50, 60.00)], province="AB")
    (finding,) = [f for f in check_taxes(wrong, _setup(), None) if f.code == "TAX_CALC_MISMATCH"]
    assert "1002.50 x 5% = 50.13 but invoice charges 60.00" in finding.message
    qc = _coding([(140, ["GST", "QST"])], [("GST", 0.05, 140, 7.00), ("QST", 0.09975, 140, 13.97)], province="QC")
    assert _row(math_check(qc), "QST")["expected"] == "13.97"


def _setup():
    from ap_coder.coding.tax import TaxRateTable, TaxSetup

    return TaxSetup(TaxRateTable.load(), {}, our_province="ON")


def _sk_doc(amount, currency="CAD", number="X-1"):
    """A Saskatchewan supply charging the GST only: its PST is self-assessed on ``amount``."""
    doc = json.loads((SAMPLES / "ground_truth" / "prairie_SK_GST_PST_PNS-104882.json").read_text())
    line = {**doc["line_items"][0], "quantity": 1, "unit_price": amount, "amount": amount, "taxes_applied": ["GST"]}
    gst = money.times(amount, 0.05, 0 if currency == "JPY" else 2)
    taxes = [{"tax_type": "GST", "province": "", "rate": 0.05, "taxable_amount": amount, "tax_amount": gst}]
    return {**doc, "invoice_number": number, "currency": currency, "subtotal": amount, "line_items": [line],
            "tax_lines": taxes, "tax_total": gst, "grand_total": money.add_up([amount, gst])}  # fmt: skip


def test_the_self_assessment_is_worked_out_half_up(tmp_path):
    """PST due on 1,000.75 at 6% is 60.045: 60.05 to book, as the Tax check says. The report estimated 60.04."""
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    doc = _sk_doc(1000.75)
    store.approve_invoice(store.add_invoice(tmp_path / "1.pdf", doc, {}), doc, "Jane")
    (item,) = taxreturn.self_assessment(store, *YEAR)
    assert (item.base, item.estimate) == (1000.75, 60.05)
    row = _row(math_check(InvoiceCoding.model_validate(doc), store.reference_data().tax), "PST")
    assert row["kind"] == "selfassess" and row["expected"] == "60.05"


def test_a_payment_discount_and_a_line_split_by_percent_round_half_up():
    """2% of 1,000.25 is 20.005: a discount of 20.01 (it said 20.00). Half of 1,234.55 is 617.275: the first half
    617.28, the rest 617.27 (they were the other way round)."""
    terms = {"invoice_date": "2026-10-01", "payment_terms": "2/10 Net 30", "due_date": "", "subtotal": 1000.25,
             "grand_total": 1130.28}  # fmt: skip
    assert payment(terms).discount_amount == 20.01
    line = {"line_number": 1, "description": "Toner", "quantity": 1, "unit_price": 1234.55, "amount": 1234.55,
            "predicted_gl_code": "6010", "predicted_cost_center": "CC400"}  # fmt: skip
    lines = pd.DataFrame([line])
    parts = split_line(lines, 1, [("6010", "CC400", 50), ("6020", "CC400", 50)])
    assert list(parts["amount"]) == [617.28, 617.27]


def test_a_line_with_a_third_decimal_posts_half_up(reference):
    """A line of 2.675 (a fuel price to the tenth of a cent) posts 2.68: the float 2.675 is a hair below, and posted
    2.67."""
    coding = _coding([(2.675, ["HST"]), (100, ["HST"])], [("HST", 0.13, 102.68, 13.35)], subtotal=102.68)
    dist = build_gl_distribution(coding, reference.tax)
    assert [e["amount"] for e in dist if e["kind"] == "expense"] == [2.68, 100.0]
    assert "POSTING_UNBALANCED" not in _codes(validate_coding(coding, reference).issues)


# --- One table of what counts as equal: the Tax check and the findings agree ---------------------------------------


def test_a_total_two_cents_off_is_a_difference_in_the_tax_check_and_a_finding(reference):
    """Subtotal + tax = total within a cent (one sum the vendor may round once), in the Tax check and in the findings
    alike (B-1865): the Tax check said 0.02 off while TOTAL_MISMATCH allowed it. The tax lines against the tax total
    the same."""
    lines, taxes = [(100, ["HST"])], [("HST", 0.13, 100, 13.00)]
    for off, flagged in ((0.01, False), (0.02, True), (-0.02, True)):
        coding = _coding(lines, taxes, total=round(113 + off, 2))
        assert _row(math_check(coding), "Subtotal + tax = total")["ok"] is not flagged
        assert ("TOTAL_MISMATCH" in _codes(validate_coding(coding, reference).issues)) is flagged
        tax_total = _coding(lines, taxes, tax_total=round(13 + off, 2), total=113)
        assert ("TAX_LINES_TOTAL_MISMATCH" in _codes(check_taxes(tax_total, reference.tax))) is flagged


def test_the_tax_checks_lines_row_allows_the_cent_a_line_the_subtotal_check_does(reference):
    """Three lines a vendor rounded one by one may be cents from a subtotal worked out before rounding: no
    SUBTOTAL_MISMATCH up to a cent a line and one more, and the Tax check said "Lines = subtotal" was off (B-1866,
    N-40). Five cents is beyond both."""
    lines = [(33.33, ["HST"]), (33.33, ["HST"]), (33.33, ["HST"])]
    for subtotal, flagged in ((100.0, False), (100.02, False), (100.03, False), (100.04, True)):
        coding = _coding(lines, [("HST", 0.13, subtotal, money.times(subtotal, 0.13))], subtotal=subtotal)
        assert _row(math_check(coding), "Lines = subtotal")["ok"] is not flagged
        assert ("SUBTOTAL_MISMATCH" in _codes(validate_coding(coding, reference).issues)) is flagged


def test_a_tax_off_by_more_than_its_rounding_is_a_difference_in_the_tax_check_as_in_the_findings(reference):
    """GST 2 cents over 5% of three lines: check_taxes warns (TAX_CALC_MISMATCH, within the band of rounding per
    line), and the Tax check said the GST added up (B-1867). A cent is rounding for both."""
    lines = [(100, ["GST"]), (200, ["GST"]), (300, ["GST"])]
    for charged, flagged in ((30.01, False), (30.02, True), (29.98, True)):
        coding = _coding(lines, [("GST", 0.05, 600, charged)], province="AB")
        findings = [f for f in check_taxes(coding, reference.tax) if f.code == "TAX_CALC_MISMATCH"]
        assert bool(findings) is flagged and all(f.severity == "warning" for f in findings)
        assert _row(math_check(coding, reference.tax), "GST")["ok"] is not flagged


def test_the_self_assessment_of_an_invoice_in_yen_is_in_whole_yen_as_the_tax_check_says(tmp_path):
    """PST due on 10,001 yen at 6% is 600 yen, as the Tax check says: the report estimated 600.06 (B-1868)."""
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    doc = _sk_doc(10001, currency="JPY")
    store.approve_invoice(store.add_invoice(tmp_path / "1.pdf", doc, {}), doc, "Jane")
    (item,) = taxreturn.self_assessment(store, *YEAR)
    assert item.estimate == 600
    row = _row(math_check(InvoiceCoding.model_validate(doc), store.reference_data().tax), "PST")
    assert row["expected"] == "600.00"


def _random_coding(rng):
    """An invoice whose printed taxable amounts are its lines marked with each tax (one tax line a tax), a credit
    note now and then, in yen now and then, its tax and totals right, a cent or a few off, or more."""
    province = rng.choice(["ON", "BC", "SK", "QC", "AB", "NS"])
    kinds = {"ON": ["HST"], "NS": ["HST"], "BC": ["GST", "PST"], "SK": ["GST", "PST"], "QC": ["GST", "QST"],
             "AB": ["GST"]}[province]  # fmt: skip
    currency = rng.choice(["CAD", "CAD", "CAD", "JPY"])
    places = 0 if currency == "JPY" else 2
    sign = -1 if rng.random() < 0.15 else 1
    lines = []
    for _ in range(rng.randint(1, 6)):
        amount = sign * round(rng.uniform(1, 3000), places)
        lines.append((amount, sorted(t for t in kinds if rng.random() < 0.85)))
    rates = {"GST": 0.05, "HST": 0.13 if province == "ON" else 0.14, "QST": 0.09975,
             "PST": 0.07 if province == "BC" else 0.06}  # fmt: skip
    off = rng.choice([0, 0, 0, 1, -1, 2, 3, -5, 40])
    taxes = []
    for t in kinds:
        base = money.add_up(a for a, applied in lines if t in applied)
        if not base:
            continue
        charged = money.times(base, rates[t], places) + (off * 10**-places if rng.random() < 0.4 else 0)
        taxes.append((t, rates[t], base, round(charged, places)))
    subtotal = round(sum(a for a, _ in lines) + (rng.choice([0, 0, 0.01, -0.02, 0.03, 0.07]) if rng.random() < 0.3
                                                  else 0), 2)  # fmt: skip
    tax_total = round(sum(t[3] for t in taxes), 2)
    total = round(subtotal + tax_total + (rng.choice([0.01, -0.01, 0.02, 0.05]) if rng.random() < 0.2 else 0), 2)
    return _coding(lines, taxes, province=province, currency=currency, subtotal=subtotal, tax_total=tax_total,
                   total=total)  # fmt: skip


TAX_ROW_FINDINGS = {"TAX_CALC_MISMATCH", "TAX_BASE_MISMATCH", "TAX_NOT_ALLOCATED"}


def test_the_tax_check_and_the_findings_agree_on_every_sum(reference):
    """Generated invoices (credit notes, yen, cents off, more off): each row of the Tax check is right exactly when its
    finding is not there: the lines and SUBTOTAL_MISMATCH, the total and TOTAL_MISMATCH, each tax and its findings;
    the PST or QST to self-assess is the report's arithmetic on the same lines."""
    rng = random.Random(183)
    for _ in range(400):
        coding = _random_coding(rng)
        codes = {i.code for i in validate_coding(coding, reference).issues}
        m = math_check(coding, reference.tax)
        assert _row(m, "Lines = subtotal")["ok"] is ("SUBTOTAL_MISMATCH" not in codes), coding
        assert _row(m, "Subtotal + tax = total")["ok"] is ("TOTAL_MISMATCH" not in codes), coding
        findings = check_taxes(coding, reference.tax)
        for row in (r for r in m["rows"] if r["kind"] == "tax"):
            said = {f.code for f in findings if f.code in TAX_ROW_FINDINGS and f.message.startswith(row["tax_type"])}
            assert row["ok"] is not said, (row, said, coding)
        places = 0 if coding.currency == "JPY" else 2
        doc = coding.model_dump()
        for row in (r for r in m["rows"] if r["kind"] == "selfassess"):
            base = taxreturn.self_assessed_base(doc, doc["line_items"], row["tax_type"])
            rate = float(row["rate"].rstrip("%")) / 100
            assert row["expected"] == f"{money.times(base, rate, places):,.2f}", row
