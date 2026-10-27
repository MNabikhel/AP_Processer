"""The CPA's three reviews of the line tax codes (D-64): what a tax professional would object to, each a test.
The first (B-703 to B-713) checks every finding on an invoice and on its credit note; the second (B-714 to B-718)
and the final check (B-719 to B-723) follow."""

from __future__ import annotations

import datetime as dt
import random

import pytest

from ap_coder.ai.offline_coder import tax_codes
from ap_coder.coding.line_tax import (
    DUE,
    GUESS_NOTE,
    NOT_DUE,
    code_label,
    confirm_codes,
    confirm_line,
    exemption_to_confirm,
    inferred_note,
    options,
    prefill,
    solve,
    tax_code_of,
    tax_guessed,
    tax_state,
)
from ap_coder.coding.review import codes_to_pick
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
    assert got.kind == "unsure" and got.count > 1_000_000 and got.lines == set(range(40))
    assert got.doubt == set(range(40))  # every line to check (B-714)


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
    assert not _found(on, reference, "ITC_AT_RISK")  # the GST charged is claimable: HST_NOT_CHARGED (B-717)
    fine = _invoice("ON", [("Software", 1000.0)], [("HST", "ON", 0.13, 1000.0, 130.0)])
    assert not _found(fine, reference, "ITC_AT_RISK")


# --- B-713: approving confirms a tax code worked out to check ------------------------------------------------------


def test_approving_confirms_the_tax_codes_and_drops_the_note_b713():
    from ap_coder.coding.line_tax import GUESS_NOTE

    output = {"line_items": [{"reasoning_justification": f"Office supplies. {GUESS_NOTE}"},
                             {"reasoning_justification": "Freight."}]}  # fmt: skip
    confirm_codes(output)
    assert [li["reasoning_justification"] for li in output["line_items"]] == ["Office supplies.", "Freight."]


# === The second review (B-714 to B-718) ===============================================================================


def _lines(pairs):
    return [LineItem(line_number=i + 1, description=d, amount=a, quantity=1, unit_price=a, predicted_gl_code="6000")
            for i, (d, a) in enumerate(pairs)]  # fmt: skip


def _bc22(taxable_printed: str = "partial"):
    """22 lines in BC: PST rightly charged on the 15 goods, not on the 7 installation lines."""
    rng = random.Random(5)
    amounts = [round(rng.uniform(10, 500), 2) for _ in range(22)]
    descriptions = [f"Hardware item {i}" for i in range(15)] + [f"Installation labour {i}" for i in range(7)]
    pst_base, subtotal = round(sum(amounts[:15]), 2), round(sum(amounts), 2)
    taxable = subtotal if taxable_printed == "subtotal" else pst_base
    return _invoice("BC", list(zip(descriptions, amounts, strict=True)),
                    [("GST", "", 0.05, subtotal, round(subtotal * 0.05, 2)),
                     ("PST", "BC", 0.07, taxable, round(pst_base * 0.07, 2))])  # fmt: skip


# --- B-714: a total right for some of the lines is no vendor error ----------------------------------------------------


def test_a_correct_vendor_is_not_blamed_when_the_lines_can_make_its_tax_b714(reference):
    for printed in ("partial", "subtotal"):
        coding = _bc22(printed)
        assert all(tax_guessed(li) for li in coding.line_items)  # several ways: every line to check
        row = next(r for r in math_check(coding, reference.tax)["rows"] if r.get("tax_type") == "PST")
        assert not row["ok"] and "pick which lines carry PST" in row["why"]
        assert "vendor" not in row["why"] and "claimed" not in row["why"]
        found = [f for f in check_taxes(coding, reference.tax) if f.code in ("TAX_CALC_MISMATCH", "TAX_BASE_MISMATCH")]
        assert found and all(f.severity == "warning" for f in found)
        assert all("pick which lines carry PST" in f.message and "vendor" not in f.message for f in found)


def test_a_tax_no_set_of_lines_makes_is_the_vendors_and_pst_is_never_said_claimable_b714(reference):
    taxes = [("GST", "", 0.05, 1000.0, 50.0), ("PST", "BC", 0.07, 1000.0, 90.0)]
    pst = _invoice("BC", [("Desk", 1000.0, "GST+PST")], taxes, prefilled=False)
    row = next(r for r in math_check(pst, reference.tax)["rows"] if r.get("tax_type") == "PST")
    assert "the vendor's tax looks wrong" in row["why"] and "claimed" not in row["why"]
    hst = _invoice("ON", [("Desk", 1000.0)], [("HST", "ON", 0.13, 1000.0, 140.0)])
    row = next(r for r in math_check(hst, reference.tax)["rows"] if r.get("tax_type") == "HST")
    assert "the vendor's tax looks wrong" in row["why"] and "can't be claimed" in row["why"]


def test_the_posting_preview_says_pst_is_spread_over_lines_still_to_check_b714(reference):
    from ap_coder.web.invoice_view import posting_note

    assert "still to check" in posting_note(_bc22(), reference)
    taxes = [("GST", "", 0.05, 1000.0, 50.0), ("PST", "BC", 0.07, 1000.0, 70.0)]
    clear = _invoice("BC", [("Desk", 1000.0, "GST+PST")], taxes, prefilled=False)
    assert posting_note(clear, reference) == ""


# --- B-715: an inferred PST exemption is confirmed by a person -------------------------------------------------------


def _bc_partial(**prefill_args):
    lines = _lines([("Laptop", 1000.0), ("Bookkeeping setup", 300.0)])
    taxes = [TaxLine(tax_type="GST", rate=0.05, taxable_amount=1300.0, tax_amount=65.0),
             TaxLine(tax_type="PST", province="BC", rate=0.07, taxable_amount=1000.0, tax_amount=70.0)]  # fmt: skip
    return prefill(lines, taxes, **prefill_args)


def test_an_inferred_pst_exemption_must_be_confirmed_before_approval_b715():
    got = _bc_partial()
    line = got.lines[1]
    assert tax_code_of(line) == "GST~PST" and exemption_to_confirm(line) == "PST"
    pick = codes_to_pick(got.lines, cost_centers=False)
    assert pick == "Confirm the PST on line 2: exempt, or not charged (self-assess)?"
    # approving does not confirm it (B-713 confirms only the codes worked out to check)
    from ap_coder.coding.line_tax import confirm_codes

    output = {"line_items": [li.model_dump() for li in got.lines]}
    confirm_codes(output)
    assert exemption_to_confirm(output["line_items"][1]) == "PST"
    # confirmed by the reviewer: recorded in the line's reasoning, and the approval may go on
    confirmed = line.model_copy(
        update={"reasoning_justification": confirm_line(line.reasoning_justification, "GST~PST")}
    )
    assert "PST exemption confirmed on review" in confirmed.reasoning_justification
    assert exemption_to_confirm(confirmed) == "" and codes_to_pick([got.lines[0], confirmed], False) == ""
    # picking "not charged (self-assess)" resolves it too
    reason = confirm_line(line.reasoning_justification, "GST")
    due = line.model_copy(update={"taxes_applied": ["GST"], "tax_exempt": [], "reasoning_justification": reason})
    assert exemption_to_confirm(due) == "" and "confirmed" not in due.reasoning_justification


def test_printed_codes_need_no_confirmation_b715():
    legend_text = {"G": ("GST",), "P": ("PST",)}
    got = prefill(_lines([("Laptop", 1000.0), ("Bookkeeping setup", 300.0)]),
                  [TaxLine(tax_type="GST", rate=0.05, taxable_amount=1300.0, tax_amount=65.0),
                   TaxLine(tax_type="PST", province="BC", rate=0.07, taxable_amount=1000.0, tax_amount=70.0)],
                  ["GP", "G"], legend_text)  # fmt: skip
    assert tax_code_of(got.lines[1]) == "GST~PST" and exemption_to_confirm(got.lines[1]) == ""


def test_an_out_of_province_vendor_or_one_without_a_pst_number_is_due_not_exempt_b715():
    for args in ({"vendor_collects": False},):
        got = _bc_partial(**args)
        assert tax_code_of(got.lines[1]) == "GST" and exemption_to_confirm(got.lines[1]) == ""
    from ap_coder.ai.offline_coder import tax_codes

    lines = _lines([("Laptop", 1000.0), ("Bookkeeping setup", 300.0)])
    taxes = [TaxLine(tax_type="GST", rate=0.05, taxable_amount=1300.0, tax_amount=65.0),
             TaxLine(tax_type="PST", province="BC", rate=0.07, taxable_amount=1000.0, tax_amount=70.0)]  # fmt: skip
    away = tax_codes(lines, taxes, [], "", "BC", "2026-06-01", supplier_province="AB", pst_number="PST-1234-5678")
    nonum = tax_codes(lines, taxes, [], "", "BC", "2026-06-01", supplier_province="BC", pst_number="")
    home = tax_codes(lines, taxes, [], "", "BC", "2026-06-01", supplier_province="BC", pst_number="PST-1234-5678")
    assert [tax_code_of(li) for li in away] == ["GST+PST", "GST"]
    # no PST number printed, but PST charged on the laptop: the vendor collects it, so it is asked (B-719)
    assert [tax_code_of(li) for li in nonum] == ["GST+PST", "GST~PST"] and exemption_to_confirm(nonum[1]) == "PST"
    assert [tax_code_of(li) for li in home] == ["GST+PST", "GST~PST"]


def test_the_review_screen_blocks_approval_until_the_pst_is_confirmed_b715(reference):
    coding = InvoiceCoding.model_validate({
        "vendor_name": "V", "invoice_number": "1", "invoice_date": "2026-06-01", "currency": "CAD",
        "confidence_score": 0.9, "subtotal": 1300.0, "tax_total": 135.0, "grand_total": 1435.0,
        "ship_to_province": "BC", "supplier_province": "BC",
        "line_items": [li.model_dump() for li in _bc_partial().lines],
        "tax_lines": [{"tax_type": "GST", "rate": 0.05, "taxable_amount": 1300.0, "tax_amount": 65.0},
                      {"tax_type": "PST", "province": "BC", "rate": 0.07, "taxable_amount": 1000.0,
                       "tax_amount": 70.0}],
    })  # fmt: skip
    found = [f for f in check_taxes(coding, reference.tax) if f.code == "PST_EXEMPTION_TO_CONFIRM"]
    assert [f.line_number for f in found] == [2]
    from ap_coder.capture.supplier import ALWAYS_A_PERSON

    assert "PST_EXEMPTION_TO_CONFIRM" in ALWAYS_A_PERSON  # never approved without a person


def test_the_audit_trail_records_the_confirmed_exemption_b715():
    from ap_coder.core.audit import diff_coding

    line = _bc_partial().lines[1].model_dump()
    final = {**line, "reasoning_justification": confirm_line(line["reasoning_justification"], "GST~PST")}
    change = next(c for c in diff_coding({"line_items": [line]}, {"line_items": [final]})
                  if c["what"] == "line 2 PST exemption")  # fmt: skip
    assert (change["before"], change["after"]) == ("inferred", "confirmed on review")


# --- B-716: the self-assessment and JD Edwards' use-tax code -------------------------------------------------------


def test_the_dry_run_warns_that_a_use_tax_code_and_the_self_assessment_could_accrue_twice_b716():
    from ap_coder.erp import jde
    from tests.erp.test_jde import AN8, SK
    from tests.erp.test_jde import _invoice as jde_invoice

    doc_inv = jde_invoice(SK)
    final = doc_inv["final_output"]
    final["tax_lines"] = [t for t in final["tax_lines"] if t["tax_type"] != "PST"]
    final["tax_total"] = sum(t["tax_amount"] for t in final["tax_lines"])
    final["grand_total"] = round(final["subtotal"] + final["tax_total"], 2)
    for li in final["line_items"]:
        li["taxes_applied"], li["tax_exempt"] = ["GST"], []
    from ap_coder.coding.tax import TaxRateTable, TaxSetup, build_gl_distribution, load_tax_mapping
    from ap_coder.core.schema import InvoiceCoding as IC
    from tests.conftest import DATA

    setup = TaxSetup(TaxRateTable.load(DATA / "canada_tax_rates.csv"), load_tax_mapping(DATA / "tax_gl_mapping.csv"))
    final["gl_distribution"] = build_gl_distribution(
        IC.model_validate({k: v for k, v in final.items() if k != "gl_distribution"}), setup
    )
    report = jde.dry_run([doc_inv], jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS), AN8)
    voucher = report["vouchers"][0]
    assert voucher["tax_code"] == "B"
    assert any("accrued twice" in w for w in voucher["warnings"])
    assert "accrued twice" in jde.dry_run_text(report)


# --- B-717: GST charged where HST applies is no ITC risk ------------------------------------------------------------


def test_gst_charged_where_hst_applies_is_hst_not_charged_not_an_itc_risk_b717(reference):
    on = _invoice("ON", [("Software", 1000.0)], [("GST", "", 0.05, 1000.0, 50.0)], supplier="AB")
    codes = {f.code: f for f in check_taxes(on, reference.tax)}
    assert "ITC_AT_RISK" not in codes and "HST_NOT_CHARGED" in codes
    message = codes["HST_NOT_CHARGED"].message
    assert "the GST is claimable" in message and "ETA s.165" in message and "13%" in message
    from ap_coder.ops.help import CHECKS

    assert "HST_NOT_CHARGED" in CHECKS


# --- B-718: QST not charged is not "self-assess" -----------------------------------------------------------------


def test_qst_not_charged_is_said_without_self_assess_b718(reference):
    assert code_label("GST", "QC") == "GST only · QST not charged"
    assert "GST only · QST not charged" in [label for _, label in options("QC", ["GST", "QST"])]
    assert code_label("GST", "BC") == "GST only · PST not charged (self-assess)"
    coding = _invoice("QC", [("Software", 1000.0, "GST")], [("GST", "", 0.05, 1000.0, 50.0)], prefilled=False)
    found = [f for f in check_taxes(coding, reference.tax) if f.code == "PROVINCIAL_TAX_NOT_CHARGED"]
    assert found and "generally owes nothing net" in found[0].message and "it is due" not in found[0].message
    assert GUESS_NOTE not in found[0].message


# === The final check (B-719 to B-723) =================================================================================


def _bc():
    lines = [LineItem(line_number=i + 1, description=d, amount=a, quantity=1, unit_price=a, predicted_gl_code="6000")
             for i, (d, a) in enumerate([("Laptop", 1000.0), ("Bookkeeping setup", 300.0)])]  # fmt: skip
    taxes = [TaxLine(tax_type="GST", rate=0.05, taxable_amount=1300.0, tax_amount=65.0),
             TaxLine(tax_type="PST", province="BC", rate=0.07, taxable_amount=1000.0, tax_amount=70.0)]  # fmt: skip
    return lines, taxes


# --- B-719: no PST number printed, but PST charged: the vendor collects it ------------------------------------------


def test_a_missing_pst_number_does_not_make_a_line_due_when_pst_is_charged_b719():
    lines, taxes = _bc()
    got = tax_codes(lines, taxes, [], "", "BC", "2026-06-01", supplier_province="BC", pst_number="")
    assert [tax_code_of(li) for li in got] == ["GST+PST", "GST~PST"]
    assert exemption_to_confirm(got[1]) == "PST"  # asked, as B-715 asks it


# --- B-720: QST not charged is not "self-assess" in the Tax check either -------------------------------------------


def test_the_tax_check_says_qst_not_charged_without_self_assess_b720(reference):
    coding = _invoice("QC", [("Software", 1000.0, "GST")], [("GST", "", 0.05, 1000.0, 50.0)], prefilled=False)
    row = next(r for r in math_check(coding, reference.tax)["rows"] if r["kind"] == "selfassess")
    assert row["pill"] == "QST not charged" and "not charged: self-assess" not in row["detail"]
    assert "generally owes nothing net" in row["detail"]  # the B-718 note
    pst = _invoice("BC", [("Laptop", 1000.0, "GST")], [("GST", "", 0.05, 1000.0, 50.0)], prefilled=False)
    assert next(r for r in math_check(pst, reference.tax)["rows"] if r["kind"] == "selfassess")["pill"] == "self-assess"


# --- B-721: HST not charged: s.165, not s.218.1 ------------------------------------------------------------------


def test_hst_not_charged_cites_the_recipients_liability_under_s165_b721(reference):
    on = _invoice("ON", [("Software", 1000.0)], [("GST", "", 0.05, 1000.0, 50.0)], supplier="AB")
    message = next(f.message for f in check_taxes(on, reference.tax) if f.code == "HST_NOT_CHARGED")
    assert "You remain liable for the provincial part (ETA s.165): ask for a corrected invoice" in message
    assert "218.1" not in message and "the GST is claimable" in message
    from ap_coder.ops.help import CHECKS

    assert "218.1" not in CHECKS["HST_NOT_CHARGED"].action and "s.165" in CHECKS["HST_NOT_CHARGED"].action


# --- B-722: the Self-assessment report says what JD Edwards accrues itself -------------------------------------


def test_the_self_assessment_report_marks_invoices_jd_edwards_accrues_with_code_b_b722(tmp_path):
    from ap_coder.coding import taxreturn
    from ap_coder.core.store import Store
    from tests.erp.test_jde import SK
    from tests.erp.test_jde import _invoice as jde_invoice

    store = Store(tmp_path / "private" / "ap_coder.db")
    final = jde_invoice(SK)["final_output"]
    final["tax_lines"] = [t for t in final["tax_lines"] if t["tax_type"] != "PST"]
    final["tax_total"] = sum(t["tax_amount"] for t in final["tax_lines"])
    final["grand_total"] = round(final["subtotal"] + final["tax_total"], 2)
    for li in final["line_items"]:
        li["taxes_applied"], li["tax_exempt"] = ["GST"], []
    from tests.conftest import SAMPLES

    ids = []
    for _ in range(2):
        iid = store.add_invoice(SAMPLES / f"{SK}.pdf", final, {"requires_review": True})
        store.approve_invoice(iid, final, "Pat Tester")
        ids.append(iid)
    store.create_export_batch([ids[0]], "jde", actor="Pat Tester")
    from ap_coder.erp import jde

    jde.save(store, jde.JdeSettings(amount_mode=jde.AMOUNT_TAX))  # code B: not with the tax coded to G/L (D-93)
    day = dt.date.fromisoformat(final["invoice_date"])
    items = {s.invoice_id: s for s in taxreturn.self_assessment(store, day, day)}
    assert "JD Edwards accrues this one (code B): don't book it again from this report" == items[ids[0]].note
    # approved, not exported yet: JD Edwards will accrue it once it is (B-928)
    assert items[ids[1]].note == taxreturn.JDE_WILL_ACCRUE
    assert b"JD Edwards accrues this one" in taxreturn.self_assessment_csv(list(items.values()), day, day)


# --- B-723: an inferred exemption picked again is confirmed --------------------------------------------------------


def test_picking_the_inferred_exemption_again_records_it_as_confirmed_b723():
    reason = f"Bookkeeping. {inferred_note('PST')}"
    away = confirm_line(reason, "GST")  # changed to "not charged (self-assess)"
    back = confirm_line(away, "GST~PST", inferred=reason)  # and back to "PST exempt": an explicit pick
    assert "PST exemption confirmed on review" in back and "inferred" not in back
    assert "confirmed" not in confirm_line(away, "GST", inferred=reason)
