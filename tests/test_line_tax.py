"""A line's tax code (D-64): the codes and their options for the place of supply, the codes printed beside the lines
and the invoice's legend, the solver that finds which lines make a tax add up, pre-filling the codes, and what the
checks, the GL posting, the self-assessment and the exports make of them."""

from __future__ import annotations

import json
import random
import time

import pytest

from ap_coder.coding import line_tax
from ap_coder.coding.line_tax import (
    apply_tax_code,
    code_for,
    options,
    parse_code,
    parse_legend,
    prefill,
    read_flags,
    solve,
    tax_code_of,
)
from ap_coder.coding.tax import build_gl_distribution, check_taxes
from ap_coder.coding.validation import validate_coding
from ap_coder.core.schema import InvoiceCoding, LineItem, TaxLine, build_json_schema

from .conftest import SAMPLES


def _line(n: int, amount: float, taxes=(), status: str = "", description: str = "") -> LineItem:
    return LineItem(line_number=n, description=description or f"Item {n}", quantity=1, unit_price=amount,
                    amount=amount, predicted_gl_code="6000", taxes_applied=list(taxes), tax_status=status)  # fmt: skip


def _coding(lines, taxes, province="BC", date="2026-08-21") -> InvoiceCoding:
    subtotal = round(sum(li.amount for li in lines), 2)
    tax_total = round(sum(t.tax_amount for t in taxes), 2)
    return InvoiceCoding(vendor_name="Vendor", invoice_number="1", invoice_date=date, currency="CAD",
                         supplier_province=province, ship_to_province=province, subtotal=subtotal, tax_lines=taxes,
                         tax_total=tax_total, grand_total=round(subtotal + tax_total, 2), confidence_score=1.0,
                         gst_hst_registration_number="123456782RT0001", line_items=lines)  # fmt: skip


def _tax(kind: str, rate: float, taxable: float, amount: float | None = None, province: str = "") -> TaxLine:
    amount = round(taxable * rate, 2) if amount is None else amount
    return TaxLine(tax_type=kind, province=province, rate=rate, taxable_amount=taxable, tax_amount=amount)


def _codes(findings) -> set[str]:
    return {f.code for f in findings}


# --- The code of a line ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("taxes", "exempt", "status", "code"),
    [
        (["PST", "GST"], [], "", "GST+PST"), (["HST"], [], "", "HST"), (["QST", "GST"], [], "", "GST+QST"),
        (["OTHER"], [], "", "OTHER"), (["GST", "OTHER"], [], "", "GST+OTHER"), (["GST"], ["PST"], "", "GST~PST"),
        (["PST"], ["GST"], "", "PST~GST"), (["GST"], [], "", "GST"), ([], ["PST"], "", "NONE~PST"),
        ([], [], "exempt", "EXEMPT"), ([], [], "zero_rated", "ZERO"), ([], [], "out_of_scope", "OUT"),
        ([], [], "", "NONE"),
    ],
)  # fmt: skip
def test_a_code_is_the_taxes_charged_and_those_not_due(taxes, exempt, status, code):
    line = _line(1, 10, taxes, status).model_copy(update={"tax_exempt": exempt})
    assert tax_code_of(line) == code == code_for(taxes, status, exempt)
    assert parse_code(code) == (sorted(taxes, key=line_tax.TAX_TYPES.index), exempt, status)
    assert tax_code_of(apply_tax_code(_line(1, 10, ["HST"]), code)) == code
    assert tax_code_of(apply_tax_code({"taxes_applied": ["GST"]}, code)) == code


def test_a_code_that_is_none_changes_nothing():
    assert parse_code("GST+FOO") is None and parse_code("") is None
    assert apply_tax_code(_line(1, 10, ["GST"]), "FOO").taxes_applied == ["GST"]


def test_a_status_means_something_only_without_taxes():
    assert _line(1, 10, ["GST"], "exempt").tax_status == ""  # taxes win: what was charged
    assert _line(1, 10, [], "Zero-rated").tax_status == "zero_rated"
    with pytest.raises(ValueError):
        _line(1, 10, [], "maybe")


def test_the_strict_schema_has_the_status_and_stays_in_its_enum_budget():
    from ap_coder.core import schema

    line = build_json_schema()["properties"]["line_items"]["items"]
    assert line["properties"]["tax_status"]["enum"] == ["", "exempt", "zero_rated", "out_of_scope"]
    assert "tax_status" in line["required"]
    values = [v for prop in _enums(build_json_schema()) for v in prop]
    assert len(values) == schema.FIXED_ENUM_VALUES
    assert sum(len(v) for v in values) == schema.FIXED_ENUM_CHARS


def _enums(node):
    if isinstance(node, dict):
        if "enum" in node:
            yield node["enum"]
        for value in node.values():
            yield from _enums(value)
    elif isinstance(node, list):
        for value in node:
            yield from _enums(value)


def test_an_invoice_stored_before_has_no_status_and_reads_as_before():
    stored = json.loads((SAMPLES / "ground_truth" / "redriver_MB_GST_RST_RRO-55821.json").read_text("utf-8"))
    for li in stored["line_items"]:
        li.pop("tax_status"), li.pop("tax_exempt")
    coding = InvoiceCoding.model_validate(stored)
    # the design service: RST charged on the other lines and not on it: exempt from RST, not RST to self-assess
    assert [tax_code_of(li) for li in coding.line_items] == ["GST+PST", "GST+PST", "GST~PST", "GST+PST"]
    # a tax charged on no line at all is due: the PST an Alberta vendor did not charge in BC is self-assessed
    gst_only = [{**li, "taxes_applied": ["GST"]} for li in stored["line_items"]]
    alberta = InvoiceCoding.model_validate({**stored, "tax_lines": stored["tax_lines"][:1], "line_items": gst_only})
    assert [tax_code_of(li) for li in alberta.line_items] == ["GST", "GST", "GST", "GST"]


# --- The choices where the invoice is supplied ------------------------------------------------------------------------


NO_TAX = ["Zero-rated", "No tax (out of scope)"]
TWO = ["{p} + {q}", "{p} only · {q} exempt", "{p} only · {q} not charged (self-assess)", "{q} only · {p} exempt",
       "Exempt from both"]  # fmt: skip


def _two(q: str) -> list[str]:
    return [label.format(p="GST", q=q) for label in TWO]


@pytest.mark.parametrize(
    ("province", "charged", "labels"),
    [
        ("ON", ["HST"], ["HST", "Exempt"]),
        ("NS", ["HST"], ["HST", "Exempt"]),
        ("BC", ["GST", "PST"], _two("PST")),
        ("SK", ["GST", "PST"], _two("PST")),
        ("MB", ["GST", "PST"], _two("RST")),
        # QST not charged is not "self-assess": a registrant generally owes nothing net (B-718)
        ("QC", ["GST", "QST"], [x.replace("QST not charged (self-assess)", "QST not charged") for x in _two("QST")]),
        ("AB", ["GST"], ["GST", "Exempt"]),
        ("YT", ["GST"], ["GST", "Exempt"]),
        ("NT", [], ["GST", "Exempt"]),
        ("NU", ["GST"], ["GST", "Exempt"]),
        # an out-of-province vendor that charges GST where HST applies: the line can say what was charged
        ("ON", ["GST"], ["HST", "GST only", "Exempt"]),
        # a foreign tax charged: its own choice
        ("AB", ["GST", "OTHER"], ["GST", "Foreign tax", "GST + Foreign tax", "Exempt"]),
        # HST charged in BC (the vendor's province): listed so the line can carry it; the regime check flags it
        ("BC", ["HST"], [*_two("PST")[:4], "HST", "Exempt from both"]),
    ],
)
def test_each_province_offers_its_own_codes(province, charged, labels):
    assert [label for _, label in options(province, charged)] == labels + NO_TAX


def test_a_lines_own_code_is_always_offered():
    got = options("ON", ["HST"], current=["GST+PST", "HST", "EXEMPT"])
    assert got[-1] == ("GST+PST", "GST + PST") and [c for c, _ in got].count("HST") == 1
    assert options("BC", ["GST"], current=["NONE"])[-1] == ("NONE", "No tax charged (self-assess PST)")


def test_an_unknown_province_offers_every_canadian_combination():
    assert [c for c, _ in options("", [])][:4] == ["HST", "GST+PST", "GST+QST", "GST"]


# --- Codes printed beside the lines, and the invoice's legend ---------------------------------------------------------


def test_the_legend_an_invoice_prints():
    legend = parse_legend("Tax codes: G = GST 5%, R = Manitoba RST 7%. Design services are not subject to RST.")
    assert legend == {"G": ("GST",), "R": ("PST",)}
    assert parse_legend("E - Exempt; Z - Zero-rated; T = taxable item") == {"E": "exempt", "Z": "zero_rated",
                                                                            "T": "TAXABLE"}  # fmt: skip
    assert parse_legend("H: HST 13%, P: PST") == {"H": ("HST",), "P": ("PST",)}
    assert parse_legend("Tax code: * = GST/HST applicable") == {"*": ("HST",)}
    # Not a legend: words that only look like one
    assert parse_legend("PO: 1234\nRe: your order of May 4\nUnit 9 - 45 Main St") == {}


@pytest.mark.parametrize(
    ("cell", "legend", "charged", "read"),
    [
        ("G R", "MB", ["GST", "PST"], (["GST", "PST"], "")),
        ("G", "MB", ["GST", "PST"], (["GST"], "")),
        ("GR", "MB", ["GST", "PST"], (["GST", "PST"], "")),
        ("GP", "MB", ["GST", "PST"], None),  # P is not one of the invoice's codes: a misread
        ("E", "MB", ["GST", "PST"], ([], "exempt")),  # a status letter needs no legend
        ("GP", None, ["GST", "PST"], (["GST", "PST"], "")),
        ("G, P", None, ["GST", "PST"], (["GST", "PST"], "")),
        ("H", None, ["HST"], (["HST"], "")),
        ("Q", None, ["GST", "QST"], (["QST"], "")),
        ("P", None, ["HST"], None),  # no PST charged: not sure what P means without a legend
        ("*", None, ["GST", "PST"], (["GST", "PST"], "")),  # taxable: the taxes charged
        ("T", None, ["HST"], (["HST"], "")),
        ("Z", None, ["GST"], ([], "zero_rated")),
        ("exempt", None, ["GST"], ([], "exempt")),
        ("Zero-rated", None, ["GST"], ([], "zero_rated")),
        ("HST ON", None, ["HST"], (["HST"], "")),
        ("GST/PST", None, ["GST", "PST"], (["GST", "PST"], "")),
        ("N/T", None, ["GST"], ([], "exempt")),
        ("X", None, ["GST"], None),
        ("12.50", None, ["GST"], None),
    ],
)
def test_a_printed_tax_cell(cell, legend, charged, read):
    codes = parse_legend("Tax codes: G = GST 5%, R = Manitoba RST 7%") if legend == "MB" else None
    assert read_flags(cell, codes, charged) == read


def test_codes_at_the_end_of_a_description_with_a_legend():
    legend = parse_legend("Tax codes: G = GST 5%, P = BC PST 7%")
    assert line_tax.codes_in_description("HP 26X toner cartridges (G, P)", legend) == "G, P"
    assert line_tax.codes_in_description("Cable (5 m)", legend) == ""
    assert line_tax.codes_in_description("HP 26X toner cartridges (G, P)", {}) == ""


# --- The solver ------------------------------------------------------------------------------------------------------


def test_every_line_when_they_add_up_to_the_base():
    assert solve([100.0, 50.0], 150.0, 0.05).kind == "all"
    # within a cent a line, and the base's own rounding (half a cent of tax is 10 cents of base at 5%)
    assert solve([100.0, 50.0], 150.09, 0.05).kind == "all"
    assert solve([100.0, 50.0], 150.20, 0.05).kind != "all"


def test_one_set_of_lines_makes_the_tax_add_up():
    """Red River: RST 941.15 at 7% is charged on 13,445.00: the desk, the chairs and the delivery, not the design."""
    got = solve([9200, 3960, 1200, 285], 941.15 / 0.07, 0.07)
    assert got.kind == "unique" and got.lines == {0, 1, 3}


def test_several_sets_the_likeliest_and_its_doubtful_lines():
    # 100 + 50 or 150 alone: leaving the freight out is the likelier
    got = solve([100.0, 50.0, 150.0, 50.0], 300.0, 0.07, ["Chairs", "Desk", "Shelf", "Bottle deposit"])
    assert got.kind == "several" and got.lines == {0, 1, 2} and got.doubt == {1, 3}
    # two lines of the same amount: either could be the exempt one, both are to check
    same = solve([100.0, 100.0, 50.0], 150.0, 0.07)
    assert same.kind == "several" and same.doubt == {0, 1}


def test_no_set_of_lines_makes_it_add_up():
    got = solve([100.0, 50.0], 70.0, 0.05)
    assert got.kind == "none" and got.lines == {0, 1}


def test_a_discount_and_a_credit_line_are_lines_too():
    # 1,000 of goods less a 100 discount, and 200 of exempt freight: GST on 900
    got = solve([1000.0, -100.0, 200.0], 900.0, 0.05, ["Goods", "Discount 10%", "Freight"])
    assert got.kind == "unique" and got.lines == {0, 1}
    credit = solve([-500.0, -120.0, -80.0], -580.0, 0.05)  # a credit note: the same, in the negative
    assert credit.kind == "unique" and credit.lines == {0, 2}


def test_forty_lines_are_solved_quickly_and_more_are_not_searched():
    rng = random.Random(7)
    amounts = [round(rng.uniform(5, 900), 2) for _ in range(40)]
    base = round(sum(amounts) - amounts[5] - amounts[17], 2)
    started = time.perf_counter()
    got = solve(amounts, base, 0.07)
    assert time.perf_counter() - started < 5
    # more than a few ways among 40 lines: no pick presented as a reading, every line carries it and is to check
    assert got.kind in ("unique", "unsure") and (got.kind == "unique" or got.lines == set(range(40)))
    assert got.kind == "unique" or got.doubt == set(range(40))  # (B-706, B-714)
    many = solve([10.0] * 41 + [5.0], 410.0, 0.05)
    assert many.kind == "none" and len(many.lines) == 42


def test_the_tolerance_grows_with_the_lines_and_the_rate():
    assert line_tax.tolerance_cents(1, 0.05) == 12  # 2 cents, and 10 cents of base for half a cent of GST
    assert line_tax.tolerance_cents(30, 0.13) == 34  # a cent a line, and 4 cents at 13%
    # Just inside, just outside: 5 lines at 7% allow 5 + 7 = 12 cents
    amounts = [100.0, 200.0, 300.0, 400.0, 57.0]
    assert solve(amounts, 1000.12, 0.07).lines == {0, 1, 2, 3}
    assert solve(amounts, 1000.13, 0.07).kind == "none"


# --- Pre-filling the codes -------------------------------------------------------------------------------------------


def _red_river():
    lines = [_line(1, 9200, description="Sit-stand desk"), _line(2, 3960, description="Task chair"),
             _line(3, 1200, description="Design consultation"),
             _line(4, 285, description="Delivery charge")]  # fmt: skip
    taxes = [_tax("GST", 0.05, 14645), _tax("PST", 0.07, 14645, 941.15, "MB")]
    return lines, taxes


def test_prefill_reads_the_printed_codes_with_the_legend():
    lines, taxes = _red_river()
    legend = parse_legend("Tax codes: G = GST 5%, R = Manitoba RST 7%")
    got = prefill(lines, taxes, ["G R", "G R", "G", "G R"], legend)
    # the design service: RST charged on the others, "G" only on it: exempt from RST (not RST to self-assess)
    assert [tax_code_of(li) for li in got.lines] == ["GST+PST", "GST+PST", "GST~PST", "GST+PST"]
    assert got.how == ["printed"] * 4 and not got.check
    assert [li.taxes_applied for li in lines] == [[], [], [], []]  # the lines given are left as they were


def test_prefill_without_codes_finds_the_lines_that_add_up():
    lines, taxes = _red_river()
    got = prefill(lines, taxes)
    assert [tax_code_of(li) for li in got.lines] == ["GST+PST", "GST+PST", "GST~PST", "GST+PST"]
    # one set of lines, leaving out a line that does not read as untaxed: to check (B-707)
    assert got.how == ["solver"] * 4 and got.check == {2}
    one = prefill([_line(1, 100), _line(2, 50)], [_tax("HST", 0.13, 150)])
    assert one.how == ["all", "all"] and [tax_code_of(li) for li in one.lines] == ["HST", "HST"]


def test_prefill_uses_the_official_rate_where_it_is_supplied():
    """A PST line read at the wrong rate (the rate worked out from rounded amounts): the official one finds the
    base."""
    lines, taxes = _red_river()
    taxes[1] = _tax("PST", 0.0643, 14645, 941.15, "MB")
    got = prefill(lines, taxes, rate_for=lambda kind, province: 0.07 if kind == "PST" else 0.05)
    assert [tax_code_of(li) for li in got.lines] == ["GST+PST", "GST+PST", "GST~PST", "GST+PST"]


def test_prefill_marks_doubtful_lines_to_check():
    lines = [_line(1, 100), _line(2, 100), _line(3, 50)]
    got = prefill(lines, [_tax("GST", 0.05, 250), _tax("PST", 0.07, 150, 10.50, "BC")])
    assert got.check == {0, 1}
    assert all(line_tax.tax_guessed(got.lines[k]) for k in got.check) and not line_tax.tax_guessed(got.lines[2])
    assert tax_code_of(got.lines[2]) == "GST+PST"


def test_prefill_never_overwrites_a_clerks_choice():
    lines, taxes = _red_river()
    lines[3] = _line(4, 285, [], "exempt", "Delivery charge")  # the clerk marked the delivery exempt
    got = prefill(lines, taxes, ["G R", "G R", "G", "G R"], parse_legend("G = GST, R = RST"), keep=[3])
    assert tax_code_of(got.lines[3]) == "EXEMPT" and got.how[3] == "kept"
    # without codes printed: the others are worked out around it (GST then cannot add up: every other line)
    again = prefill(lines, taxes, keep=[3])
    assert tax_code_of(again.lines[3]) == "EXEMPT"


def test_prefill_lines_in_no_tax_charged_and_exempt_only_when_printed():
    lines = [_line(1, 100), _line(2, 40, description="Deposit")]
    got = prefill(lines, [_tax("GST", 0.05, 100)])
    # the vendor charges GST and not on the deposit: the deposit is exempt (not GST to collect)
    assert [tax_code_of(li) for li in got.lines] == ["GST", "EXEMPT"] and not got.check
    printed = prefill(lines, [_tax("GST", 0.05, 100)], ["G", "E"])
    assert [tax_code_of(li) for li in printed.lines] == ["GST", "EXEMPT"]
    nothing = prefill(lines, [])
    assert [tax_code_of(li) for li in nothing.lines] == ["NONE", "NONE"]


def test_prefill_some_codes_printed_the_rest_solved():
    lines = [_line(1, 1000), _line(2, 300, description="Installation"), _line(3, 200, description="Freight")]
    taxes = [_tax("GST", 0.05, 1500), _tax("PST", 0.07, 1000, 70.00, "BC")]
    got = prefill(lines, taxes, ["GP", "", ""])
    assert [tax_code_of(li) for li in got.lines] == ["GST+PST", "GST~PST", "GST~PST"]


def test_prefill_a_foreign_tax():
    lines = [_line(1, 735), _line(2, 3600)]
    got = prefill(lines, [_tax("OTHER", 0.1035, 4335)])
    assert [tax_code_of(li) for li in got.lines] == ["OTHER", "OTHER"]


# --- The checks -------------------------------------------------------------------------------------------------------


def test_a_base_that_does_not_add_up_names_the_line_that_is_the_difference(reference):
    lines = [_line(1, 9200, ["GST", "PST"]), _line(2, 3960, ["GST", "PST"]), _line(3, 1200, ["GST"]),
             _line(4, 285, ["GST", "PST"], description="Delivery charge")]  # fmt: skip
    coding = _coding(lines, [_tax("GST", 0.05, 14645), _tax("PST", 0.07, 13160, 921.20, "MB")], "MB")
    found = [f for f in check_taxes(coding, reference.tax) if f.code == "TAX_BASE_MISMATCH"]
    assert len(found) == 1 and "line 4 (Delivery charge, 285.00) may be exempt from RST" in found[0].message
    # a line left out (RST due, not exempt) whose amount is the difference: it may carry the tax too
    lines[2] = _line(3, 1200, ["GST"], description="Design").model_copy(update={"tax_exempt": []})
    coding = _coding(lines, [_tax("GST", 0.05, 14645), _tax("PST", 0.07, 14645, 1025.15, "MB")], "MB")
    found = [f for f in check_taxes(coding, reference.tax) if f.code == "TAX_BASE_MISMATCH"]
    assert "line 3 (Design, 1,200.00) may carry RST too" in found[0].message
    # the same line marked exempt from RST: the RST charged needs it (EXEMPT_LINE_TAXED), said once
    lines[2] = lines[2].model_copy(update={"tax_exempt": ["PST"]})
    coding = _coding(lines, [_tax("GST", 0.05, 14645), _tax("PST", 0.07, 14645, 1025.15, "MB")], "MB")
    found = check_taxes(coding, reference.tax)
    assert [f.line_number for f in found if f.code == "EXEMPT_LINE_TAXED"] == [3]
    assert "TAX_BASE_MISMATCH" not in _codes(found)


def test_a_tax_on_no_line(reference):
    coding = _coding([_line(1, 100, ["GST"])], [_tax("GST", 0.05, 100), _tax("PST", 0.07, 100, 7.0, "BC")])
    found = check_taxes(coding, reference.tax)
    assert any(f.code == "TAX_NOT_ALLOCATED" and "PST is charged" in f.message for f in found)


def test_lines_marked_with_a_tax_not_charged(reference):
    lines = [_line(1, 100, ["GST"]), _line(2, 50, ["GST", "PST"]), _line(3, 50, ["GST", "PST"])]
    coding = _coding(lines, [_tax("GST", 0.05, 200)])
    found = [f for f in check_taxes(coding, reference.tax) if f.code == "LINE_TAX_NOT_CHARGED"]
    # once, the lines listed (an invoice of before printing "PST 0.00" is one finding, not one a line: B-708)
    assert len(found) == 1 and found[0].message.startswith("lines 2, 3 marked PST, but no PST is charged")
    # the advice keeps the PST in the self-assessment: "not charged (self-assess)", never "exempt" for taxable lines
    assert "choose “GST only · PST not charged (self-assess)” and self-assess it (BC PSTA s.49" in found[0].message
    assert "if they are exempt, choose “GST only · PST exempt”" in found[0].message
    # said once: the province's own "no PST charged" is not said again
    assert "PROVINCIAL_TAX_NOT_CHARGED" not in _codes(check_taxes(coding, reference.tax))
    unmarked = _coding([_line(1, 200, ["GST"])], [_tax("GST", 0.05, 200)])
    assert "PROVINCIAL_TAX_NOT_CHARGED" in _codes(check_taxes(unmarked, reference.tax))


def test_an_exempt_line_the_tax_adds_up_with(reference):
    lines = [_line(1, 1000, ["GST"]), _line(2, 200, [], "exempt", "Freight")]
    coding = _coding(lines, [_tax("GST", 0.05, 1200)], "AB")
    found = check_taxes(coding, reference.tax)
    taxed = [f for f in found if f.code == "EXEMPT_LINE_TAXED"]
    assert len(taxed) == 1 and taxed[0].line_number == 2 and "TAX_BASE_MISMATCH" not in _codes(found)


def test_every_line_exempt_no_tax_charged_says_so(reference):
    coding = _coding([_line(1, 100, [], "exempt"), _line(2, 50, [], "zero_rated")], [], "BC")
    found = [f for f in check_taxes(coding, reference.tax) if f.code == "NO_TAX_CHARGED"]
    assert len(found) == 1 and "every line is marked exempt, zero-rated or out of scope" in found[0].message


def test_prices_that_include_the_tax_are_said(reference):
    coding = _coding([_line(1, 105.0, ["GST"])], [_tax("GST", 0.05, 100)], "AB")
    coding.subtotal = 100.0
    coding.grand_total = 105.0
    report = validate_coding(coding, reference)
    found = [i for i in report.issues if i.code == "SUBTOTAL_MISMATCH"]
    assert found and "seem to include the tax" in found[0].message


def test_the_findings_have_help_and_the_vendor_is_asked_only_about_a_taxed_exempt_line():
    from ap_coder.coding.vendor_mail import ASKABLE
    from ap_coder.ops.help import CHECKS

    assert {"LINE_TAX_NOT_CHARGED", "EXEMPT_LINE_TAXED"} <= set(CHECKS)
    assert "EXEMPT_LINE_TAXED" in ASKABLE and "LINE_TAX_NOT_CHARGED" not in ASKABLE


# --- The Tax check ---------------------------------------------------------------------------------------------------


def test_the_tax_check_at_the_official_rate_with_the_lines_to_look_at(reference):
    from ap_coder.coding.taxmath import math_check

    lines, _ = _red_river()
    lines = [li.model_copy(update={"taxes_applied": ["GST", "PST"]}) for li in lines]
    coding = _coding(lines, [_tax("GST", 0.05, 14645), _tax("PST", 0.07, 13445, 941.15, "MB")], "MB")
    m = math_check(coding, reference.tax)
    rst = next(r for r in m["rows"] if r.get("tax_type") == "PST")
    assert rst["what"] == "RST" and not rst["ok"] and rst["expected"] == "1,025.15" and rst["diff"] == "(84.00)"
    assert rst["named"] == "Line 3 (Design consultation, 1,200.00)" and list(m["marks"]) == [3]
    gst = next(r for r in m["rows"] if r.get("tax_type") == "GST")
    assert gst["ok"] and gst["which"] == "every line" and gst["rate"] == "5%"


def test_the_tax_check_rate_is_the_one_in_force_on_the_invoice_date(reference):
    """Nova Scotia: HST 15% before April 1, 2025, 14% from it: an invoice of March 2025 charged 15% adds up."""
    from ap_coder.coding.taxmath import math_check

    coding = _coding([_line(1, 1000, ["HST"])], [_tax("HST", 0.15, 1000, province="NS")], "NS", date="2025-03-15")
    assert math_check(coding, reference.tax)["ok"]
    later = _coding([_line(1, 1000, ["HST"])], [_tax("HST", 0.15, 1000, province="NS")], "NS", date="2025-06-15")
    # the Tax check works the rate printed (B-705); the rate check says it is not the official one
    row = next(r for r in math_check(later, reference.tax)["rows"] if r.get("tax_type") == "HST")
    assert row["ok"] and row["rate"] == "15%"
    assert "TAX_RATE_NONSTANDARD" in _codes(check_taxes(later, reference.tax))


def test_the_tax_check_qst_is_on_the_price_before_gst(reference):
    from ap_coder.coding.taxmath import math_check

    coding = _coding([_line(1, 1000, ["GST", "QST"])], [_tax("GST", 0.05, 1000), _tax("QST", 0.09975, 1000, 99.75,
                                                                                       "QC")], "QC")  # fmt: skip
    assert math_check(coding, reference.tax)["ok"]


# --- Posting, self-assessment and exports ---------------------------------------------------------------------------


def test_pst_expensed_onto_the_lines_that_carry_it_never_an_exempt_one(reference):
    lines = [_line(1, 1000, ["GST", "PST"]), _line(2, 200, ["GST"], description="Design"), _line(3, 100, [], "exempt")]
    coding = _coding(lines, [_tax("GST", 0.05, 1200), _tax("PST", 0.07, 1000, 70.0, "BC")])
    dist = {e["line_number"]: e["non_recoverable_tax"] for e in build_gl_distribution(coding, reference.tax)
            if e["kind"] == "expense"}  # fmt: skip
    assert dist == {1: 70.0, 2: 0.0, 3: 0.0}
    # a PST charged on no line marked with it: spread over the lines that do not say they carry no tax
    unmarked = _coding([_line(1, 1000, ["GST"]), _line(2, 100, [], "exempt")],
                       [_tax("GST", 0.05, 1000), _tax("PST", 0.07, 1000, 70.0, "BC")])  # fmt: skip
    dist = {e["line_number"]: e["non_recoverable_tax"] for e in build_gl_distribution(unmarked, reference.tax)
            if e["kind"] == "expense"}  # fmt: skip
    assert dist == {1: 70.0, 2: 0.0}


def test_the_self_assessed_base_is_the_lines_marked_taxable_but_not_charged():
    from ap_coder.coding.taxreturn import self_assessed_base

    doc = {"subtotal": 1300.0, "tax_lines": [{"tax_type": "GST", "tax_amount": 65.0}]}
    # line by line: marked PST on an invoice charging none, and a line PST is due on (neither charged nor exempt)
    marked = [{"amount": 1000, "taxes_applied": ["GST", "PST"]}, {"amount": 300, "taxes_applied": ["GST"]}]
    assert self_assessed_base(doc, marked, "PST") == 1300.0
    exempt = [{"amount": 1000, "taxes_applied": ["GST"], "tax_exempt": []},
              {"amount": 300, "taxes_applied": ["GST"], "tax_exempt": ["PST"]}]  # fmt: skip
    assert self_assessed_base(doc, exempt, "PST") == 1000.0
    stated = [{"amount": 1000, "taxes_applied": ["GST"]}, {"amount": 300, "taxes_applied": [], "tax_status": "exempt"}]
    assert self_assessed_base(doc, stated, "PST") == 1000.0
    assert self_assessed_base(doc, [], "PST") == 1300.0
    # PST charged on the other lines and missed on one (marked due): that one line is owed (B-709)
    charged = {"subtotal": 1300.0, "tax_lines": [{"tax_type": "PST", "tax_amount": 70.0}]}
    missed = [{"amount": 1000, "taxes_applied": ["GST", "PST"], "tax_exempt": []},
              {"amount": 300, "taxes_applied": ["GST"], "tax_exempt": []}]  # fmt: skip
    assert self_assessed_base(charged, missed, "PST") == 300.0


def test_the_exports_have_a_tax_code_column():
    from ap_coder.erp import export_layout, exports

    final = json.loads((SAMPLES / "ground_truth" / "redriver_MB_GST_RST_RRO-55821.json").read_text("utf-8"))
    final["line_items"][3]["taxes_applied"], final["line_items"][3]["tax_status"] = [], "exempt"
    rows = exports.line_rows([{"id": 1, "final_output": final}])
    assert [r["tax_code"] for r in rows if r["kind"] == "Expense"] == [
        "GST + RST", "GST + RST", "GST only · RST exempt", "Exempt from both"
    ]  # fmt: skip
    assert all(r["tax_code"] == "" for r in rows if r["kind"] != "Expense")
    assert ("tax_code", "Tax code") in exports.LINE_COLUMNS and "tax_code" in export_layout.FIELDS
    csv_text = exports.build_csv([{"id": 1, "final_output": final}]).decode("utf-8-sig")
    assert "Tax code" in csv_text.splitlines()[0] and ",Exempt from both," in csv_text


# --- The chat model's lines, the audit trail --------------------------------------------------------------------------


def test_the_chat_models_lines_without_taxes_are_worked_out_and_its_own_kept():
    from ap_coder.ai.pipeline import _model_line_taxes

    lines = [_line(1, 9200, description="Desk"), _line(2, 3960, description="Chair"),
             _line(3, 1200, description="Design"), _line(4, 285, description="Delivery")]  # fmt: skip
    coding = _coding(lines, [_tax("GST", 0.05, 14645), _tax("PST", 0.07, 13445, 941.15, "MB")], "MB")
    coding.pst_registration_number = "160482-7"  # a vendor that collects the RST (B-715)
    _model_line_taxes(coding, "")
    assert [tax_code_of(li) for li in coding.line_items] == ["GST+PST", "GST+PST", "GST~PST", "GST+PST"]
    said = _coding([_line(1, 100, ["GST"]), _line(2, 50)], [_tax("GST", 0.05, 100)], "AB")
    _model_line_taxes(said, "")
    # the model said something: kept, the line it left out read as the vendor meant it (exempt)
    assert [tax_code_of(li) for li in said.line_items] == ["GST", "EXEMPT"]


def test_the_audit_trail_says_a_lines_tax_code_changed():
    from ap_coder.core.audit import diff_coding

    ai = {"line_items": [{"line_number": 1, "amount": 100, "taxes_applied": ["GST", "PST"]}]}
    final = {"line_items": [{"line_number": 1, "amount": 100, "taxes_applied": [], "tax_status": "exempt"}]}
    change = next(c for c in diff_coding(ai, final) if c["what"] == "line 1 tax code")
    assert (change["before"], change["after"]) == ("GST + PST", "Exempt")


def test_a_taxable_that_followed_the_lines_names_the_line_instead_of_an_arithmetic_error(reference):
    """The review screen moves a tax line's taxable with the lines marked (B-610), never its amount (B-700): a line
    marked wrongly then shows as taxable × rate ≠ tax. When one line's amount is the difference, that is a warning
    naming the line, not an error in the vendor's arithmetic."""
    lines = [_line(1, 9200, ["GST", "PST"]), _line(2, 3960, ["GST", "PST"]),
             _line(3, 1200, ["GST", "PST"], description="Design"), _line(4, 285, ["GST", "PST"])]  # fmt: skip
    coding = _coding(lines, [_tax("GST", 0.05, 14645), _tax("PST", 0.07, 14645, 941.15, "MB")], "MB")
    found = [f for f in check_taxes(coding, reference.tax) if f.code == "TAX_CALC_MISMATCH"]
    assert len(found) == 1 and found[0].severity == "warning"
    assert "line 3 (Design, 1,200.00) may be exempt from RST" in found[0].message
    wrong = _coding(lines, [_tax("GST", 0.05, 14645), _tax("PST", 0.07, 14645, 900.00, "MB")], "MB")
    assert [f.severity for f in check_taxes(wrong, reference.tax) if f.code == "TAX_CALC_MISMATCH"] == ["error"]
