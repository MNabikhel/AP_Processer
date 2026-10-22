"""The CPA review of the line tax codes (D-64): what a tax professional would object to, each a test (B-703 to
B-713). Every finding is checked on an invoice and on its credit note."""

from __future__ import annotations

import random

import pytest

from ap_coder.ai.offline_coder import tax_codes
from ap_coder.coding.line_tax import (
    DUE,
    NOT_DUE,
    code_label,
    confirm_codes,
    options,
    prefill,
    solve,
    tax_code_of,
    tax_guessed,
    tax_state,
)
from ap_coder.coding.tax import build_gl_distribution, check_taxes
from ap_coder.coding.taxmath import math_check
from ap_coder.core.schema import InvoiceCoding, LineItem, TaxLine, build_json_schema


def _invoice(prov, lines, taxes, date="2026-06-01", prefilled=True, supplier=None) -> InvoiceCoding:
    """An invoice of ``lines`` [(description, amount) or (description, amount, code)] and ``taxes`` [(type, province,
    rate, taxable, tax)], its codes worked out as the offline reading does (or set from the code given)."""
    items = []
    for i, line in enumerate(lines):
        item = LineItem(line_number=i + 1, description=line[0], amount=line[1], quantity=1, unit_price=line[1],
                        predicted_gl_code="6000")  # fmt: skip
        items.append(item)
    tl = [TaxLine(tax_type=t, province=p, rate=r, taxable_amount=b, tax_amount=x) for t, p, r, b, x in taxes]
    if prefilled:
        items = tax_codes(items, tl, [], "", prov, date)
    for item, line in zip(items, lines, strict=True):
        if len(line) > 2:
            from ap_coder.coding.line_tax import apply_tax_code

            items[items.index(item)] = apply_tax_code(item, line[2])
    sub = round(sum(line[1] for line in lines), 2)
    tax = round(sum(t[4] for t in taxes), 2)
    return InvoiceCoding(vendor_name="V", invoice_number="1", invoice_date=date, currency="CAD", confidence_score=0.9,
                         subtotal=sub, tax_total=tax, grand_total=round(sub + tax, 2), ship_to_province=prov,
                         supplier_province=supplier or prov, gst_hst_registration_number="123456782RT0001",
                         line_items=items, tax_lines=tl)  # fmt: skip


def _credit(invoice: InvoiceCoding) -> InvoiceCoding:
    """The credit note of an invoice: every amount the other way."""
    data = invoice.model_dump()
    for li in data["line_items"]:
        li["amount"], li["unit_price"] = -li["amount"], -li["unit_price"]
    for t in data["tax_lines"]:
        t["taxable_amount"], t["tax_amount"] = -t["taxable_amount"], -t["tax_amount"]
    for k in ("subtotal", "tax_total", "grand_total"):
        data[k] = -data[k]
    return InvoiceCoding.model_validate(data)


def _found(coding, reference, code):
    return [f for f in check_taxes(coding, reference.tax) if f.code == code]


def _row(coding, reference, tax_type):
    return next(r for r in math_check(coding, reference.tax)["rows"] if r.get("tax_type") == tax_type)


# --- B-703: the Tax check names a line only when one line explains the difference ------------------------------------


@pytest.mark.parametrize("charged", [140.0, 120.0])
def test_a_vendors_wrong_tax_is_said_as_such_not_blamed_on_a_line_b703(reference, charged):
    invoice = _invoice("ON", [("Consulting", 1000.0)], [("HST", "ON", 0.13, 1000.0, charged)])
    for coding in (invoice, _credit(invoice)):
        m = math_check(coding, reference.tax)
        row = next(r for r in m["rows"] if r.get("tax_type") == "HST")
        assert not row["ok"] and "named" not in row and m["marks"] == {}
        assert "the vendor's tax looks wrong, ask for a corrected invoice" in row["why"]
        assert "isn't 13% of the lines marked HST" in row["why"]
        calc = _found(coding, reference, "TAX_CALC_MISMATCH")
        assert calc and "ask the vendor for a corrected invoice" in calc[0].message and "line 1" not in calc[0].message


# --- B-704: a credit note is checked in the size of its amounts ----------------------------------------------------


def test_every_line_finding_is_the_same_on_a_credit_note_b704(reference):
    invoice = _invoice("ON", [("Software", 1000.0, "HST"), ("Training", 200.0, "EXEMPT")],
                       [("HST", "ON", 0.13, 1200.0, 156.0)],
                      prefilled=False)  # fmt: skip
    credit = _credit(invoice)
    for coding in (invoice, credit):
        taxed = _found(coding, reference, "EXEMPT_LINE_TAXED")
        assert [f.line_number for f in taxed] == [2] and not _found(coding, reference, "TAX_BASE_MISMATCH")
        row = _row(coding, reference, "HST")
        assert row["named"].startswith("Line 2 (Training") and "may carry HST too" in row["why"]
    # a line marked that the tax was not charged on: "more on the lines" both ways, the same line named
    over = _invoice("ON", [("Software", 1000.0, "HST"), ("Training", 200.0, "HST")],
                    [("HST", "ON", 0.13, 1000.0, 130.0)],
                      prefilled=False)  # fmt: skip
    for coding in (over, _credit(over)):
        base = _found(coding, reference, "TAX_BASE_MISMATCH")
        assert "(200.00 more on the lines)" in base[0].message and "line 2 (Training" in base[0].message
        assert _row(coding, reference, "HST")["named"].startswith("Line 2 (Training")


def test_the_solver_reads_a_credit_note_as_its_invoice_b704():
    assert solve([-9200, -3960, -1200, -285], -13445.0, 0.07).lines == {0, 1, 3}


# --- B-705: a lawful rate other than the general one ------------------------------------------------------------------


def test_bc_pst_on_liquor_at_10_percent_adds_up_b705(reference):
    catering = _invoice("BC", [("Catering food", 1000.0), ("Wine", 300.0)],
                        [("GST", "", 0.05, 1300.0, 65.0), ("PST", "BC", 0.07, 1000.0, 70.0),
                         ("PST", "BC", 0.10, 300.0, 30.0)])  # fmt: skip
    m = math_check(catering, reference.tax)
    assert m["ok"], m["text"]
    assert not _found(catering, reference, "TAX_RATE_NONSTANDARD")
    liquor = _invoice("BC", [("Wine, 12 cases", 1800.0)], [("GST", "", 0.05, 1800.0, 90.0),
                                                          ("PST", "BC", 0.10, 1800.0, 180.0)])  # fmt: skip
    assert math_check(liquor, reference.tax)["ok"] and not _found(liquor, reference, "TAX_RATE_NONSTANDARD")
    assert [tax_code_of(li) for li in liquor.line_items] == ["GST+PST"]


# --- B-706: no pick presented as a reading when the amounts can't say ------------------------------------------------


def test_forty_lines_with_millions_of_ways_are_not_guessed_b706():
    rng = random.Random(3)
    amounts = [round(rng.uniform(10, 500), 2) for _ in range(40)]
    got = solve(amounts, round(sum(amounts[:25]), 2), 0.07, [f"Item {i}" for i in range(40)])
    assert got.kind == "unsure" and got.count > 1_000_000 and got.lines == set(range(40)) and not got.doubt


def test_a_few_ways_among_many_lines_are_not_guessed_either_b706():
    amounts = [100.0, 100.0] + [float(17 + 31 * i) for i in range(22)]
    got = solve(amounts, sum(amounts) - 100.0, 0.05)
    assert got.kind == "unsure" and got.lines == set(range(24))


def test_the_count_is_exact_without_listing_the_ways_b706():
    got = solve([100.0, 100.0, 100.0, 50.0], 250.0, 0.05)
    assert got.kind == "several" and got.count == 3 and got.doubt == {0, 1, 2}


# --- B-707: a unique set can hide a vendor's error ----------------------------------------------------------------


def test_a_discount_is_never_left_out_of_the_tax_b707(reference):
    """GST charged before a 10% volume discount: the discount lowers the taxable price (ETA s.165), so the vendor
    overcharged; the discount line is not quietly made untaxed."""
    coding = _invoice("AB", [("Widgets", 1000.0), ("Install", 200.0), ("Volume discount 10%", -120.0)],
                      [("GST", "", 0.05, 1080.0, 60.0)])  # fmt: skip
    assert [tax_code_of(li) for li in coding.line_items] == ["GST", "GST", "GST"]
    assert not math_check(coding, reference.tax)["ok"]


def test_a_line_left_out_that_does_not_read_as_untaxed_is_to_check_b707(reference):
    coding = _invoice("AB", [("Service A", 512.37), ("Service B", 233.10), ("Service C", 1045.00), ("Courier", 88.88)],
                      [("GST", "", 0.05, 1879.35, 89.53)])  # fmt: skip
    courier = coding.line_items[3]
    assert "GST" not in courier.taxes_applied and tax_guessed(courier)
    # the taxable printed (the subtotal) says the courier is taxed: the finding names it
    assert [f.line_number for f in _found(coding, reference, "EXEMPT_LINE_TAXED")] == [4]


def test_a_line_left_out_against_a_printed_taxable_is_to_check_b707():
    lines = [LineItem(line_number=i + 1, description=d, amount=a, quantity=1, unit_price=a, predicted_gl_code="6000")
             for i, (d, a) in enumerate([("Laptop", 1500.0), ("Monitor", 400.0), ("Gift card", 85.0)])]  # fmt: skip
    taxes = [TaxLine(tax_type="GST", rate=0.05, taxable_amount=1985.0, tax_amount=99.25),
             TaxLine(tax_type="PST", province="BC", rate=0.07, taxable_amount=1985.0, tax_amount=133.0)]  # fmt: skip
    got = prefill(lines, taxes)
    assert tax_code_of(got.lines[2]) == "GST~PST" and 2 in got.check  # untaxed-looking, but the PST taxable says not


# --- B-708, B-709, B-710: tax by tax, and what is due --------------------------------------------------------------


def test_a_line_says_its_state_for_each_tax_b710():
    line = LineItem(line_number=1, description="Bookkeeping", amount=100, quantity=1, unit_price=100,
                    predicted_gl_code="6000", taxes_applied=["GST"], tax_exempt=["PST"])  # fmt: skip
    assert tax_code_of(line) == "GST~PST" and code_label("GST~PST", "BC") == "GST only · PST exempt"
    assert tax_state(line, "GST") == "charged" and tax_state(line, "PST") == NOT_DUE
    due = line.model_copy(update={"tax_exempt": []})
    assert tax_code_of(due) == "GST" and tax_state(due, "PST") == DUE
    assert code_label("GST", "BC") == "GST only · PST not charged (self-assess)"
    assert code_label("PST~GST", "BC") == "PST only · GST exempt"
    assert [label for _, label in options("MB", ["GST", "PST"])][:5] == [
        "GST + RST", "GST only · RST exempt", "GST only · RST not charged (self-assess)", "RST only · GST exempt",
        "Exempt from both",
    ]  # fmt: skip
    assert [label for _, label in options("ON", ["HST"])] == ["HST", "Exempt", "Zero-rated", "No tax (out of scope)"]
    schema = build_json_schema()["properties"]["line_items"]["items"]
    assert schema["properties"]["tax_exempt"]["items"]["enum"] == ["GST", "HST", "PST", "QST", "OTHER"]


def test_an_alberta_vendor_not_charging_pst_in_bc_is_self_assessed_b708(reference):
    """BC laptop from an Alberta vendor with no PST: the PST is due and self-assessed (BC PSTA s.49); "exempt" would
    have taken it out of the self-assessment."""
    coding = _invoice("BC", [("Laptop", 1000.0), ("Dock", 300.0), ("Cables", 200.0)],
                      [("GST", "", 0.05, 1500.0, 75.0)], supplier="AB")  # fmt: skip
    assert [tax_code_of(li) for li in coding.line_items] == ["GST", "GST", "GST"]
    found = _found(coding, reference, "PROVINCIAL_TAX_NOT_CHARGED")
    assert len(found) == 1 and "every line (1,500.00)" in found[0].message and "BC PSTA s.49" in found[0].message
    row = next(r for r in math_check(coding, reference.tax)["rows"] if r["kind"] == "selfassess")
    assert row["expected"] == "105.00" and row["ok"]


def test_a_pst_missed_on_one_line_is_self_assessed_for_that_line_b709(reference):
    coding = _invoice("BC", [("Laptop", 1000.0, "GST+PST"), ("Monitor", 300.0, "GST")],
                      [("GST", "", 0.05, 1300.0, 65.0), ("PST", "BC", 0.07, 1000.0, 70.0)],
                      prefilled=False)  # fmt: skip
    found = _found(coding, reference, "PROVINCIAL_TAX_NOT_CHARGED")
    assert len(found) == 1 and "line 2 (300.00)" in found[0].message
    exempt = _invoice("BC", [("Laptop", 1000.0, "GST+PST"), ("Bookkeeping", 300.0, "GST~PST")],
                      [("GST", "", 0.05, 1300.0, 65.0), ("PST", "BC", 0.07, 1000.0, 70.0)],
                      prefilled=False)  # fmt: skip
    assert not _found(exempt, reference, "PROVINCIAL_TAX_NOT_CHARGED")


def test_an_old_invoice_printing_pst_0_is_one_finding_not_one_a_line_b708(reference):
    old = InvoiceCoding.model_validate({
        "vendor_name": "V", "invoice_number": "1", "invoice_date": "2026-06-01", "currency": "CAD",
        "confidence_score": 0.9, "subtotal": 1500.0, "tax_total": 75.0, "grand_total": 1575.0,
        "ship_to_province": "BC", "supplier_province": "AB",
        "line_items": [{"line_number": n, "description": d, "quantity": 1, "unit_price": a, "amount": a,
                        "predicted_gl_code": "6000", "taxes_applied": ["GST", "PST"]}
                       for n, d, a in ((1, "Laptop", 1000.0), (2, "Dock", 300.0), (3, "Cables", 200.0))],
        "tax_lines": [{"tax_type": "GST", "rate": 0.05, "taxable_amount": 1500.0, "tax_amount": 75.0},
                      {"tax_type": "PST", "province": "BC", "rate": 0.07, "taxable_amount": 1500.0, "tax_amount": 0}],
    })  # fmt: skip
    found = check_taxes(old, reference.tax)
    assert len([f for f in found if f.code == "LINE_TAX_NOT_CHARGED"]) == 1
    assert "TAX_CALC_MISMATCH" not in {f.code for f in found}


def test_pst_is_expensed_onto_the_lines_it_is_charged_on_never_one_exempt_from_it_b710(reference):
    coding = _invoice("BC", [("Desk", 1000.0, "GST+PST"), ("Bookkeeping", 200.0, "GST~PST")],
                      [("GST", "", 0.05, 1200.0, 60.0), ("PST", "BC", 0.07, 1000.0, 70.0)],
                      prefilled=False)  # fmt: skip
    nonrec = {e["line_number"]: e["non_recoverable_tax"] for e in build_gl_distribution(coding, reference.tax)
              if e["kind"] == "expense"}  # fmt: skip
    assert nonrec == {1: 70.0, 2: 0.0}


# --- B-711: freight and eco fees follow the goods ----------------------------------------------------------------


def test_delivery_and_an_eco_fee_are_not_preferred_untaxed_b711():
    """A tie between the delivery and the design consultation: the delivery (taxed like the goods in MB) is not the
    one left out; and an SK eco fee is part of the price."""
    lines = [LineItem(line_number=i + 1, description=d, amount=a, quantity=1, unit_price=a, predicted_gl_code="6000")
             for i, (d, a) in enumerate([("Standing desk", 1000.0), ("Delivery charge", 150.0),
                                         ("Design consultation", 150.0)])]  # fmt: skip
    taxes = [TaxLine(tax_type="GST", rate=0.05, taxable_amount=1300.0, tax_amount=65.0),
             TaxLine(tax_type="PST", province="MB", rate=0.07, taxable_amount=1150.0, tax_amount=80.5)]  # fmt: skip
    got = prefill(lines, taxes)
    assert got.check == {1, 2}  # the amounts can't tell which: both to check, neither a confident pick
    from ap_coder.coding.line_tax import LIKELY_UNTAXED

    for word in ("Delivery charge", "Freight", "Shipping", "Environmental handling fee", "Eco fee"):
        assert not LIKELY_UNTAXED.search(word), word
    for word in ("Bottle deposit", "Gift card", "Gratuity", "Interest", "Late payment fee", "Exempt item"):
        assert LIKELY_UNTAXED.search(word), word


# --- B-712: an input tax credit at risk ------------------------------------------------------------------------


def test_hst_charged_where_gst_applies_and_gst_where_hst_applies_put_the_itc_at_risk_b712(reference):
    bc = _invoice("BC", [("Software", 1000.0)], [("HST", "ON", 0.13, 1000.0, 130.0)], supplier="ON")
    assert "ETA s.169" in _found(bc, reference, "ITC_AT_RISK")[0].message
    on = _invoice("ON", [("Software", 1000.0)], [("GST", "", 0.05, 1000.0, 50.0)], supplier="AB")
    assert _found(on, reference, "ITC_AT_RISK")
    fine = _invoice("ON", [("Software", 1000.0)], [("HST", "ON", 0.13, 1000.0, 130.0)])
    assert not _found(fine, reference, "ITC_AT_RISK")


# --- B-713: approving confirms a tax code worked out to check ------------------------------------------------------


def test_approving_confirms_the_tax_codes_and_drops_the_note_b713():
    from ap_coder.coding.line_tax import GUESS_NOTE

    output = {"line_items": [{"reasoning_justification": f"Office supplies. {GUESS_NOTE}"},
                             {"reasoning_justification": "Freight."}]}  # fmt: skip
    confirm_codes(output)
    assert [li["reasoning_justification"] for li in output["line_items"]] == ["Office supplies.", "Freight."]
