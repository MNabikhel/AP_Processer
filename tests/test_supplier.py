"""Supplier learning: templates learned from confirmed values, accuracy statistics and the path to autonomy."""

import json
import sqlite3
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder.capture.supplier import (
    AUTONOMOUS,
    DEFAULT_POLICY,
    HELD,
    LEARNING,
    READY,
    SUPERVISED,
    SUSPENDED,
    AutonomyPolicy,
    Shape,
    SupplierStats,
    Template,
    apply_template,
    autonomy_status,
    clean_fields_needed,
    confirmed_values,
    learn,
    meets_policy,
    outcome_rows,
    parse_amount,
    parse_date,
    pick_for_audit,
    should_auto_approve,
    supplier_key,
    value_shape,
    wilson_lower,
)
from ap_coder.capture.types import Box, CaptureResult, DocLayout, FieldResult, PageLayout, Word
from ap_coder.store import SCHEMA_VERSION, Store

# --- Synthetic layouts --------------------------------------------------------------------------------------------


def _page(items, number=1):
    words = [Word(text, Box(number, x, y, x + 0.008 * len(text), y + 0.012)) for text, x, y in items]
    return PageLayout(number, 612, 792, words, [])


def _invoice(number="NW-2026-0912", date="2026-09-12", total="1,130.00", dy=0.0, label=("Invoice", "No:")):
    items = [
        ("Northwind", 0.06, 0.05 + dy), ("Supplies", 0.15, 0.05 + dy),
        *[(t, 0.6 + 0.06 * i, 0.1 + dy) for i, t in enumerate(label)], (number, 0.72, 0.1 + dy),
        ("Date:", 0.6, 0.12 + dy), (date, 0.72, 0.12 + dy),
        ("Description", 0.06, 0.3 + dy), ("Amount", 0.8, 0.3 + dy),
        ("Toner", 0.06, 0.33 + dy), ("1,000.00", 0.8, 0.33 + dy),
        ("Subtotal", 0.6, 0.8 + dy), ("1,000.00", 0.8, 0.8 + dy),
        ("HST", 0.6, 0.82 + dy), ("130.00", 0.8, 0.82 + dy),
        ("Total", 0.6, 0.84 + dy), ("$", 0.78, 0.84 + dy), (total, 0.8, 0.84 + dy),
    ]  # fmt: skip
    return DocLayout([_page(items)], "text")


CONFIRMED = {
    "vendor_name": ("Northwind Supplies", None), "invoice_number": ("NW-2026-0912", None),
    "invoice_date": ("2026-09-12", None), "subtotal": (1000.0, None), "hst_amount": (130.0, None),
    "grand_total": (1130.0, None),
}  # fmt: skip


def _learned(n=2):
    template = None
    for i in range(n):
        template = learn(template, _invoice(dy=0.002 * i), CONFIRMED)
    return Template.from_dict(json.loads(json.dumps(template.to_dict())))  # JSON round trip


def _top(readings, field):
    return readings[field][0]


# --- Supplier key and normalizing ---------------------------------------------------------------------------------


def test_supplier_key_prefers_the_vendor_master_id_then_the_business_number():
    assert supplier_key("Northwind Supplies Inc.", "123456789 RT 0001", "V1001") == "id:V1001"
    assert supplier_key("Northwind Supplies Inc.", "123456789 RT 0001") == "bn:123456789"
    assert supplier_key("Northwind Supplies Inc.") == supplier_key("NORTHWIND SUPPLIES") == "name:northwind supplies"
    assert supplier_key("", "12-34") == ""


@pytest.mark.parametrize(
    "text, value",
    [("1,234.56", 1234.56), ("1 234,56 $", 1234.56), ("(12.00)", -12.0), ("-12.00", -12.0), ("12.00 CR", -12.0),
     ("$1,234.56", 1234.56), ("CAD 99", 99.0), ("INV-2026", None), ("Total", None), ("1,23,4.00", None)],
)  # fmt: skip
def test_amounts(text, value):
    assert parse_amount(text) == value


def test_dates():
    assert parse_date("2026-09-12") == "2026-09-12"
    assert parse_date("Oct 7, 2026") == "2026-10-07"
    assert parse_date("7 octobre 2026") == "2026-10-07"
    assert parse_date("1er décembre 2026") == "2026-12-01"
    assert parse_date("25/10/2026") == "2026-10-25"  # only one reading possible
    assert parse_date("07/10/2026", "mdy") == "2026-07-10"
    assert parse_date("Toner 7 2026") is None


# --- Shapes -------------------------------------------------------------------------------------------------------


def test_value_shape_and_shape_checks():
    assert value_shape("INV-2026-0912") == "AAA-9999-9999"
    shape = Shape()
    for v in ("NW-2026-0912", "NW-2026-0913"):
        shape.add(v)
    assert shape.patterns == {"AA-9999-9999": 2} and shape.min_len == shape.max_len == 12
    assert shape.score("invoice_number", "NW-2026-1001") == 1.0
    assert shape.score("invoice_number", "NW-2026-10012") == 0.7  # one digit more: same structure
    assert shape.score("invoice_number", "Toner") == 0.0
    assert shape.score("grand_total", "1,130.00") == 1.0 and shape.score("grand_total", "Total") == 0.0
    assert shape.score("invoice_date", "Oct 7, 2026") == 1.0 and shape.score("invoice_date", "NW-1") == 0.0


# --- Learn and apply ----------------------------------------------------------------------------------------------


def test_learn_records_anchor_offset_shape_and_counts():
    template = _learned(2)
    assert template.invoices == 2
    inv = template.fields["invoice_number"]
    assert len(inv) == 1 and inv[0].count == 2 and inv[0].page == 1
    assert inv[0].anchor.text == "Invoice No:" and inv[0].anchor.where == "left"
    assert inv[0].anchor.offset == pytest.approx([0.12, 0.0], abs=1e-6)
    assert inv[0].shape.patterns == {"AA-9999-9999": 2} and inv[0].last_seen
    total = template.fields["grand_total"][0]
    assert total.anchor.text == "Total" and total.box[0] == pytest.approx(0.8)  # the total, not the line amount
    assert template.fields["subtotal"][0].anchor.text == "Subtotal"  # 1,000.00 is printed twice: the labelled one


def test_apply_reads_a_new_invoice_where_the_page_moved():
    readings = apply_template(_learned(), _invoice("NW-2026-0999", "2026-10-01", "2,260.00", dy=0.03))
    assert _top(readings, "invoice_number").value == "NW-2026-0999"
    assert _top(readings, "invoice_date").value == "2026-10-01"
    assert _top(readings, "grand_total").value == 2260.0 and _top(readings, "grand_total").raw == "2,260.00"
    assert _top(readings, "subtotal").value == 1000.0 and _top(readings, "hst_amount").value == 130.0
    for field in ("invoice_number", "grand_total"):
        r = _top(readings, field)
        assert r.method == "template" and r.score >= 0.9 and r.boxes


def test_anchor_matching_is_tolerant_of_case_accents_and_ocr_glue():
    template = _learned()
    shouted = apply_template(template, _invoice("NW-2026-0999", label=("INVOICE", "NO")))
    assert _top(shouted, "invoice_number").value == "NW-2026-0999" and _top(shouted, "invoice_number").score >= 0.9
    glued = DocLayout(
        [_page([("InvoiceNo:NW-2026-0777", 0.6, 0.1), ("Total", 0.6, 0.84), ("99.00", 0.8, 0.84)])], "ocr"
    )
    reading = _top(apply_template(template, glued), "invoice_number")
    assert reading.value == "NW-2026-0777" and reading.score >= 0.9
    french = learn(None, DocLayout([_page([("Facture", 0.6, 0.1), ("n°", 0.66, 0.1), ("F-12", 0.72, 0.1)])], "text"),
                   {"invoice_number": ("F-12", None)})  # fmt: skip
    plain = DocLayout([_page([("FACTURE", 0.6, 0.2), ("N°", 0.66, 0.2), ("F-13", 0.72, 0.2)])], "text")
    assert _top(apply_template(french, plain), "invoice_number").value == "F-13"


def test_without_the_anchor_the_position_is_used_with_a_lower_score():
    template = _learned()
    anchored = _top(apply_template(template, _invoice("NW-2026-0999")), "invoice_number")
    no_label = _invoice("NW-2026-0999", label=("Ref",))  # the label is not printed
    position = _top(apply_template(template, no_label), "invoice_number")
    assert position.value == "NW-2026-0999"
    assert position.score < anchored.score and position.score <= 0.65


def test_shape_check_rejects_a_wrong_value_in_the_right_place():
    template = _learned()
    wrong = _invoice("Toner")  # something else printed where the invoice number goes
    assert "invoice_number" not in apply_template(template, wrong)


def test_teach_by_click_boxes_are_authoritative():
    layout = _invoice()
    clicked = Box(1, 0.06, 0.05, 0.214, 0.062)  # the reviewer clicked the vendor name in the letterhead
    template = learn(None, layout, {"vendor_name": ("Northwind Supplies Inc.", [clicked])})
    variant = template.fields["vendor_name"][0]
    assert variant.box == pytest.approx([0.06, 0.05, 0.214, 0.062])
    assert variant.shape.patterns == {"AAAAAAAAA AAAAAAAA": 1}  # the printed words, not the typed value


def test_a_value_not_on_the_page_teaches_nothing():
    template = learn(None, _invoice(), {"po_number": ("PO-1", None), "invoice_number": ("NW-2026-0912", None)})
    assert "po_number" not in template.learned_fields() and template.invoices == 1
    assert learn(None, _invoice(), {"po_number": ("PO-1", None)}).invoices == 0


def test_variants_per_field_are_capped_at_three():
    template = None
    for i, label in enumerate(("Invoice", "Number", "Reference", "Document", "Bill")):  # five layouts
        layout = DocLayout([_page([(label, 0.1, 0.1 + 0.15 * i), (f"A-{i}", 0.25, 0.1 + 0.15 * i)])], "text")
        template = learn(template, layout, {"invoice_number": (f"A-{i}", None)})
        if i == 1:
            template = learn(template, layout, {"invoice_number": (f"A-{i}", None)})  # this one seen twice
    variants = template.fields["invoice_number"]
    assert len(variants) == 3 and variants[0].anchor.text == "Number" and variants[0].count == 2
    assert {v.anchor.text for v in variants[1:]} == {"Document", "Bill"}  # the least used, oldest went first
    # The same label at another place on the page is the same variant (the offset from the label holds).
    moved = DocLayout([_page([("Bill", 0.3, 0.5), ("A-9", 0.45, 0.5)])], "text")
    assert len(learn(template, moved, {"invoice_number": ("A-9", None)}).fields["invoice_number"]) == 3


def test_totals_on_the_last_page_of_a_multi_page_invoice():
    def two_pages(total):
        first = _page([("Invoice", 0.6, 0.1), ("No:", 0.66, 0.1), ("NW-1", 0.72, 0.1)], 1)
        last = _page([("Total", 0.6, 0.84), (total, 0.8, 0.84)], 2)
        return DocLayout([first, last], "text")

    template = learn(None, two_pages("50.00"), {"grand_total": (50.0, None), "invoice_number": ("NW-1", None)})
    assert template.fields["grand_total"][0].page == "last" and template.fields["invoice_number"][0].page == 1
    three = two_pages("75.00")
    three.pages.insert(1, _page([("Toner", 0.1, 0.1)], 2))
    three.pages[-1] = _page([("Total", 0.6, 0.84), ("75.00", 0.8, 0.84)], 3)
    assert _top(apply_template(template, three), "grand_total").value == 75.0


def test_the_supplier_date_order_is_learned():
    layout = DocLayout([_page([("Date:", 0.6, 0.12), ("25/10/2026", 0.72, 0.12)])], "text")
    template = learn(None, layout, {"invoice_date": ("2026-10-25", None)})
    assert template.fields["invoice_date"][0].date_order == "dmy"
    ambiguous = DocLayout([_page([("Date:", 0.6, 0.12), ("03/11/2026", 0.72, 0.12)])], "text")
    assert _top(apply_template(template, ambiguous), "invoice_date").value == "2026-11-03"


def test_learning_continues_from_the_template_as_the_store_keeps_it():
    stored = json.loads(json.dumps(_learned(1).to_dict()))  # a dict, as get_supplier_profile returns it
    template = learn(stored, _invoice(dy=0.002), CONFIRMED)
    assert isinstance(template, Template) and template.invoices == 2
    assert template.fields["invoice_number"][0].count == 2


def _totals(lines: int, gst: str, total: str) -> DocLayout:
    """Totals that move down the page with the number of lines, the tax rate printed by its label and a
    "Line Total" column heading over the amounts."""
    items = [("Description", 0.06, 0.25), ("Line", 0.8, 0.25), ("Total", 0.836, 0.25)]
    items += [(t, x, 0.28 + 0.03 * i) for i in range(lines) for t, x in ((f"Item{i}", 0.06), (f"{11 + i}.00", 0.8))]
    y = 0.3 + 0.03 * lines
    items += [("Sub", 0.6, y), ("Total:", 0.627, y), ("1,150.00", 0.8, y),
              ("GST", 0.6, y + 0.02), ("5%", 0.63, y + 0.02), (gst, 0.8, y + 0.02),
              ("Total:", 0.6, y + 0.04), (total, 0.8, y + 0.04)]  # fmt: skip
    return DocLayout([_page(items)], "text")


def test_totals_are_read_by_their_own_label_when_the_lines_push_them_down():
    template = None
    for _ in range(2):
        template = learn(template, _totals(2, "57.50", "1,207.50"), {"gst_amount": (57.5, None),
                                                                     "grand_total": (1207.5, None)})  # fmt: skip
    gst = template.fields["gst_amount"][0]
    assert gst.anchor.text == "GST" and gst.anchor.where == "left"  # past the rate, not the column heading above
    readings = apply_template(template, _totals(6, "60.00", "1,260.00"))
    assert [r.value for r in readings["gst_amount"]] == [60.0]
    # "Total:" is the whole label of the total, not the end of "Sub Total:" nor the "Line Total" heading.
    assert [r.value for r in readings["grand_total"]] == [1260.0]


def test_a_value_printed_in_several_words_is_one_reading():
    def page(terms: list[str], total: list[str]) -> DocLayout:
        items = [("Terms:", 0.1, 0.5), *[(w, 0.2 + 0.05 * i, 0.5) for i, w in enumerate(terms)]]
        items += [("TOTAL:", 0.6, 0.8), (total[0], 0.75, 0.8), (total[1], 0.762, 0.8)]
        return DocLayout([_page(items)], "text")

    template = None
    for _ in range(2):
        template = learn(template, page(["NET", "60"], ["2", "292,84"]), {"payment_terms": ("NET 60", None),
                                                                         "grand_total": (2292.84, None)})  # fmt: skip
    readings = apply_template(template, page(["NET", "45"], ["1", "868,10"]))
    assert [r.raw for r in readings["payment_terms"]] == ["NET 45"]  # not "NET" and "45" as rival values
    assert [r.value for r in readings["grand_total"]] == [1868.1]  # "1 868,10", not "1"
    # Once "868,10" (one word) has also been confirmed, the tail of "2 074,06" fits as well: still the whole.
    small = DocLayout([_page([("TOTAL:", 0.6, 0.8), ("868,10", 0.762, 0.8)])], "text")
    template = learn(template, small, {"grand_total": (868.1, None)})
    readings = apply_template(template, page(["NET", "45"], ["2", "074,06"]))
    assert [r.value for r in readings["grand_total"]] == [2074.06]


def test_payment_terms_compare_by_meaning_and_are_learned_as_printed():
    from ap_coder.capture.normalize import normalize_value
    from ap_coder.capture.supplier import compare_key

    assert compare_key("payment_terms", "30 days") == compare_key("payment_terms", "Net 30")
    assert compare_key("payment_terms", "2% 10, Net 30") != compare_key("payment_terms", "Net 30")
    assert normalize_value("payment_terms", "60 jours net") == normalize_value("payment_terms", "Net 60")
    assert normalize_value("payment_terms", "Payable à réception") == normalize_value("payment_terms", "Due on receipt")
    assert outcome_rows({"payment_terms": "Net 30"}, {"payment_terms": "30 days"})[0]["correct"]
    layout = DocLayout([_page([("Terms:", 0.1, 0.5), ("30", 0.2, 0.5), ("days", 0.225, 0.5)])], "text")
    template = learn(None, layout, {"payment_terms": ("Net 30", None)})  # the reader's wording, as AP approved it
    variant = template.fields["payment_terms"][0]
    assert variant.anchor.text == "Terms:" and variant.shape.patterns == {"99 AAAA": 1}  # "30 days" as printed


def test_the_label_on_the_value_row_beats_another_label_above():
    layout = DocLayout(
        [_page([("PO", 0.1, 0.2), ("Votre", 0.44, 0.2), ("commande:", 0.49, 0.2), ("BC-1", 0.6, 0.2),
                ("Terms:", 0.1, 0.215), ("Net", 0.5, 0.215), ("30", 0.53, 0.215)])],
        "text",
    )  # fmt: skip
    template = learn(None, layout, {"payment_terms": ("Net 30", None)})
    assert template.fields["payment_terms"][0].anchor.text == "Terms:"


def test_an_optional_field_whose_label_is_missing_is_not_read_by_position():
    def page(po_label: bool) -> DocLayout:
        items = [("Invoice", 0.1, 0.1), ("No:", 0.156, 0.1), ("A-1", 0.25, 0.1)]
        items += (
            [("PO:", 0.1, 0.12), ("PO-12345", 0.25, 0.12)]
            if po_label
            else [("SO:", 0.1, 0.12), ("SO-55555", 0.25, 0.12)]
        )
        return DocLayout([_page(items)], "text")

    template = learn(None, page(True), {"po_number": ("PO-12345", None), "invoice_number": ("A-1", None)})
    readings = apply_template(template, page(False))  # no PO on this one: a sales order sits in its place
    assert "po_number" not in readings and _top(readings, "invoice_number").value == "A-1"


def test_each_confirmation_at_the_same_label_adds_trust():
    new = _invoice("NW-2026-0999", dy=0.03)
    scores = [_top(apply_template(_learned(n), new), "invoice_number").score for n in (1, 2, 6)]
    assert scores[0] < scores[1] < scores[2] <= 0.99


def test_confirmed_values_and_outcome_rows_from_codings():
    final = {"vendor_name": "Northwind Supplies", "invoice_number": "NW-1", "grand_total": 113.0,
             "tax_lines": [{"tax_type": "HST", "tax_amount": 13.0}]}  # fmt: skip
    confirmed = confirmed_values(final, {"grand_total": [Box(1, 0.8, 0.84, 0.86, 0.85)]})
    assert confirmed["hst_amount"] == (13.0, None) and confirmed["grand_total"][1]
    proposed = {"vendor_name": "Northwind Supplies Inc.", "invoice_number": "nw-1", "grand_total": "113.00",
                "tax_lines": [{"tax_type": "HST", "tax_amount": 12.0}]}  # fmt: skip
    rows = {r["field"]: r["correct"] for r in outcome_rows(proposed, final)}
    assert rows == {"vendor_name": True, "invoice_number": True, "grand_total": True, "hst_amount": False}
    capture = CaptureResult({"invoice_number": FieldResult("invoice_number", "NW-1", 0.99, "verified")})
    assert outcome_rows(capture, {"invoice_number": "NW-1"}) == [
        {"field": "invoice_number", "ai_value": "NW-1", "final_value": "NW-1", "correct": True}
    ]


# --- Statistics and the state machine -----------------------------------------------------------------------------


def test_wilson_lower_bound():
    assert wilson_lower(0, 0) == 0.0
    assert wilson_lower(95, 100) == pytest.approx(0.88825, abs=1e-4)
    assert wilson_lower(10, 10) == pytest.approx(0.72246, abs=1e-4)
    assert wilson_lower(380, 380) < 0.99 < wilson_lower(381, 381)  # a 99% bound needs ~381 clean fields
    assert clean_fields_needed(0, 0, 0.99) == 381 and clean_fields_needed(400, 400, 0.99) == 0


def _rows(invoices, fields=13, bad=(), start=0):
    """Outcome rows: ``invoices`` invoices of ``fields`` fields each; invoice numbers in ``bad`` have one error."""
    out = []
    for i in range(start, start + invoices):
        for f in range(fields):
            out.append({"invoice_id": i, "field": f"f{f}", "correct": not (i in bad and f == 0),
                        "at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}", "source": "review"})  # fmt: skip
    return out


def test_stats_from_rows():
    stats = SupplierStats.from_rows(_rows(12, bad={3}), key="k")
    assert stats.invoices == 12 and stats.n == 156 and stats.correct == 155
    assert stats.clean_streak == 8 and stats.touchless_rate == pytest.approx(11 / 12)
    assert stats.fields["f0"].n == 12 and stats.fields["f0"].correct == 11
    window = SupplierStats.from_rows(_rows(12, bad={3}), window=5)
    assert window.window_invoices == 5 and window.n == 65 and window.invoices == 12


def test_state_machine_learning_supervised_ready_autonomous_suspended():
    state, progress, why = autonomy_status(SupplierStats.from_rows(_rows(12, bad={5})))
    assert state == LEARNING and why.startswith("12 of 20 invoices; 4 more clean in a row needed")
    assert 0 < progress < 1
    state, _, why = autonomy_status(SupplierStats.from_rows(_rows(25, bad={20})))
    assert state == SUPERVISED and "6 more clean in a row needed" in why
    clean = SupplierStats.from_rows(_rows(30))
    state, progress, why = autonomy_status(clean)
    assert state == READY and progress == 1.0 and "Goes touchless on its next invoice" in why
    assert meets_policy(clean)
    # with touchless processing off, ready says so (and nothing is approved without a person)
    assert "once touchless processing is on" in autonomy_status(clean, touchless_on=False)[2]
    state, progress, _ = autonomy_status(clean, DEFAULT_POLICY, AUTONOMOUS, "2026-01-01T00:00:30")
    assert state == AUTONOMOUS and progress == 1.0
    # a touchless supplier shows as ready while the switch is off
    assert autonomy_status(clean, DEFAULT_POLICY, AUTONOMOUS, "2026-01-01T00:00:30", touchless_on=False)[0] == READY
    one_error = SupplierStats.from_rows(_rows(31, bad={30}))  # an audit after autonomy found one correction
    state, _, why = autonomy_status(one_error, DEFAULT_POLICY, AUTONOMOUS, "2026-01-01T00:00:30")
    assert state == SUSPENDED and why.startswith("Suspended")
    assert autonomy_status(one_error, DEFAULT_POLICY, SUSPENDED)[0] == SUSPENDED


def test_policy_is_configurable():
    lenient = AutonomyPolicy(min_invoices=5, min_lower_bound=0.9, clean_streak=3)
    assert autonomy_status(SupplierStats.from_rows(_rows(5)), lenient)[0] == READY
    assert AutonomyPolicy.from_dict(lenient.to_dict()) == lenient
    stats = SupplierStats.from_rows(_rows(22))  # 286 clean fields: the 99% bound is the last thing missing
    state, _, why = autonomy_status(stats)
    assert state == SUPERVISED and "more clean invoices for a 99% accuracy bound" in why


def _capture(**statuses):
    fields = {name: FieldResult(name, "x", 0.99, "verified") for name in
              ("vendor_name", "invoice_number", "invoice_date", "grand_total", "subtotal")}  # fmt: skip
    fields["po_number"] = FieldResult("po_number", None, 0.0, "missing")  # not printed: fine
    for name, status in statuses.items():
        fields[name] = FieldResult(name, "x", 0.5, status)
    return CaptureResult(fields, checks=[{"code": "TOTALS_ADD_UP", "ok": True, "detail": ""}])


def test_should_auto_approve_only_when_everything_is_verified():
    assert should_auto_approve(AUTONOMOUS, _capture(), True) == (True, should_auto_approve(AUTONOMOUS, _capture(),
                                                                                            True)[1])  # fmt: skip
    ok, reason = should_auto_approve(SUPERVISED, _capture(), True)
    assert not ok and "not autonomous" in reason
    assert not should_auto_approve(READY, _capture(), True)[0]
    ok, reason = should_auto_approve(AUTONOMOUS, _capture(subtotal="likely"), True)
    assert not ok and "subtotal is likely" in reason
    assert not should_auto_approve(AUTONOMOUS, _capture(invoice_date="check"), True)[0]
    ok, reason = should_auto_approve(AUTONOMOUS, _capture(), False)
    assert not ok and "check failed" in reason
    failed = _capture()
    failed.checks.append({"code": "TAX_RATE", "ok": False, "detail": "13% expected"})
    assert should_auto_approve(AUTONOMOUS, failed.to_dict(), True) == (False, "check failed: TAX_RATE")
    issues = [{"severity": "warning", "code": "W"}, {"severity": "error", "code": "GL_UNKNOWN"}]
    assert should_auto_approve(AUTONOMOUS, _capture(), True, issues) == (False, "validation error: GL_UNKNOWN")
    duplicate = [{"severity": "error", "code": "DUPLICATE_INVOICE"}]
    assert should_auto_approve(AUTONOMOUS, _capture(), True, duplicate) == (
        False,
        "always a person: it may be a duplicate",
    )
    no_number = _capture()
    no_number.fields["invoice_number"] = FieldResult("invoice_number", None, 0, "missing")
    assert not should_auto_approve(AUTONOMOUS, no_number, True)[0]
    assert not should_auto_approve(AUTONOMOUS, None, True)[0]


def test_lines_not_adding_up_do_not_block_once_the_totals_add_up():
    lines = _capture()  # its totals add up
    lines.checks.append({"code": "LINES_ADD_UP", "ok": False, "detail": "lines 90.00 ≠ subtotal 100.00"})
    assert should_auto_approve(AUTONOMOUS, lines, True)[0]
    lines.checks = [c for c in lines.checks if c["code"] != "TOTALS_ADD_UP"]
    assert should_auto_approve(AUTONOMOUS, lines, True) == (False, "check failed: LINES_ADD_UP")


def test_audit_sampling_is_deterministic_and_close_to_the_rate():
    picks = [pick_for_audit(i, 0.05) for i in range(20_000)]
    assert picks == [pick_for_audit(i, 0.05) for i in range(20_000)]
    assert 0.04 < sum(picks) / len(picks) < 0.06
    assert not any(pick_for_audit(i, 0) for i in range(100)) and all(pick_for_audit(i, 1) for i in range(100))


# --- Store -------------------------------------------------------------------------------------------------------


def _outcomes(n_fields=13, bad=False):
    return [{"field": f"f{f}", "ai_value": "a", "final_value": "a" if not (bad and f == 0) else "b",
             "correct": not (bad and f == 0)} for f in range(n_fields)]  # fmt: skip


def _invoice_ids(store, ground_truth, tmp_path, n):
    return [store.add_invoice(tmp_path / f"inv{i}.pdf", ground_truth, {"requires_review": False}) for i in range(n)]


def test_migration_from_schema_13(tmp_path, ground_truth):
    path = tmp_path / "old.db"
    store = Store(path)
    invoice_id = store.add_invoice(tmp_path / "a.pdf", ground_truth, {"requires_review": False})
    with sqlite3.connect(path) as conn:  # what a version-13 database looks like
        for table in ("invoice_capture", "supplier_profiles", "supplier_outcomes"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("UPDATE settings SET value = '13' WHERE key = 'schema_version'")
    upgraded = Store(path)
    assert upgraded.get_setting("schema_version") == str(SCHEMA_VERSION)
    with sqlite3.connect(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
    assert {"invoice_capture", "supplier_profiles", "supplier_outcomes", "supplier_outcomes_key_at"} <= tables
    assert upgraded.get_invoice(invoice_id)["vendor_name"] == ground_truth["vendor_name"]  # data kept
    upgraded.save_capture(invoice_id, {"fields": {}, "layout_source": "text"})
    assert upgraded.get_capture(invoice_id) == {"fields": {}, "layout_source": "text"}


def test_fresh_database_has_the_new_tables(tmp_path):
    store = Store(tmp_path / "fresh.db")
    assert store.get_setting("schema_version") == str(SCHEMA_VERSION)
    assert store.list_supplier_profiles() == [] and store.get_capture(1) is None


def test_capture_and_profile_round_trips(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    invoice_id = _invoice_ids(store, ground_truth, tmp_path, 1)[0]
    capture = CaptureResult(
        {"grand_total": FieldResult("grand_total", 113.0, 0.97, "verified", [Box(1, 0.8, 0.84, 0.86, 0.85)])},
        layout_source="ocr", page_count=1,
    )  # fmt: skip
    store.save_capture(invoice_id, capture.to_dict())
    assert CaptureResult.from_dict(store.get_capture(invoice_id)) == capture
    store.save_capture(invoice_id, {**capture.to_dict(), "page_count": 2})  # saved again: replaced
    assert store.get_capture(invoice_id)["page_count"] == 2

    template = _learned()
    store.save_supplier_profile("name:northwind supplies", "Northwind Supplies", template=template, actor="Ann")
    profile = store.get_supplier_profile("name:northwind supplies")
    assert profile["state"] == SUPERVISED and profile["updated_by"] == "Ann"
    assert Template.from_dict(profile["template"]) == template
    store.save_supplier_profile("name:northwind supplies", vendor_id="V1")  # only what is given changes
    profile = store.get_supplier_profile("name:northwind supplies")
    assert profile["display_name"] == "Northwind Supplies" and profile["vendor_id"] == "V1" and profile["template"]
    assert [p["key"] for p in store.list_supplier_profiles()] == ["name:northwind supplies"]


def test_outcomes_stats_autonomy_and_suspension(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    key = "bn:123456789"
    ids = _invoice_ids(store, ground_truth, tmp_path, 32)
    for i, invoice_id in enumerate(ids[:30]):
        store.record_outcomes(key, invoice_id, _outcomes(), display_name="Northwind", at=f"2026-01-01T00:00:{i:02d}")
    store.record_outcomes(key, ids[0], _outcomes(), at="2026-01-01T00:00:00")  # recorded again: replaced
    stats = store.supplier_stats(key)
    assert stats.invoices == 30 and stats.n == 390 and stats.clean_streak == 30
    assert store.supplier_stats(key, window=10).n == 130
    assert autonomy_status(stats)[0] == READY

    with pytest.raises(ValueError):
        store.set_supplier_state(key, "ready", "Ann")  # ready is computed, never stored
    store.set_supplier_state(key, AUTONOMOUS, "Ann")
    profile = store.get_supplier_profile(key)
    assert profile["state"] == AUTONOMOUS and profile["autonomous_since"] and profile["audit_rate"] == 0.05
    event = store.events(actions=["autonomy_on"])[0]
    assert event["actor"] == "Ann" and event["detail"]["supplier"] == "Northwind"
    assert event["detail"]["policy"]["min_invoices"] == 20

    result = store.record_outcomes(key, ids[30], _outcomes(bad=True), source="audit", at="2099-01-01T00:00:00")
    assert result == {"fields": 13, "corrections": 1, "suspended": True, "touchless": False}
    assert store.get_supplier_profile(key)["state"] == SUSPENDED
    suspended = store.events(actions=["autonomy_suspended"])[0]
    assert suspended["actor"] == "AP Coder" and "audit found a correction: f0" in suspended["detail"]["reason"]
    assert autonomy_status(store.supplier_stats(key), DEFAULT_POLICY, SUSPENDED)[0] == SUSPENDED
    with pytest.raises(ValueError):  # not clean in a row any more
        store.set_supplier_state(key, AUTONOMOUS, "Ann")
    store.set_supplier_state(key, SUPERVISED, "Bob")
    assert store.get_supplier_profile(key)["autonomous_since"] is None
    assert store.events(actions=["autonomy_off"])[0]["actor"] == "Bob"
    store.set_supplier_state(key, SUSPENDED, "AP Coder")  # nothing to suspend when not autonomous
    assert store.get_supplier_profile(key)["state"] == SUPERVISED

    store.delete_invoice(ids[30])  # deleting an invoice removes what was measured on it
    assert store.supplier_stats(key).invoices == 30
    store.save_capture(ids[1], {"fields": {}})
    store.delete_invoice(ids[1])
    assert store.get_capture(ids[1]) is None and store.supplier_stats(key).invoices == 29


def test_reopening_withdraws_the_review_outcomes(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    invoice_id = _invoice_ids(store, ground_truth, tmp_path, 1)[0]
    store.approve_invoice(invoice_id, ground_truth, "Ann")
    store.record_outcomes("name:x", invoice_id, _outcomes())
    store.reopen(invoice_id, "Ann", "wrong total")
    assert store.supplier_stats("name:x").invoices == 0


def test_supplier_key_for_uses_the_vendor_master(tmp_path):
    store = Store(tmp_path / "s.db")
    store.import_vendor_master([{"vendor_name": "Northwind Supplies", "erp_id": "V1001",
                                 "gst": "123456789RT0001"}])  # fmt: skip
    assert store.supplier_key_for("Northwind Supplies Inc.") == "id:V1001"
    assert store.supplier_key_for("Northwind (renamed)", "123456789 RT 0001") == "id:V1001"
    assert store.supplier_key_for("Someone Else", "987654321RT0001") == "bn:987654321"


def test_demo_records_and_removes_supplier_outcomes(tmp_path):
    from ap_coder.demo import load_demo, remove_demo

    store = Store(tmp_path / "ap.db")
    load_demo(store)
    profiles = store.list_supplier_profiles()
    assert len(profiles) == 2  # the two pre-approved demo invoices
    stats = store.supplier_stats(profiles[0]["key"])
    assert stats.invoices == 1 and stats.n and stats.correct == stats.n
    remove_demo(store)
    assert store.list_supplier_profiles() == []


# --- The Learning page --------------------------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    for name in [m for m in sys.modules if m.startswith(("ap_coder.webapp", "ap_coder.dashboard"))]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()
    st.cache_data.clear()
    return path


def _learning_page():
    return AppTest.from_string(
        "from ap_coder.webapp.learning import page_learning\npage_learning()", default_timeout=90
    )


def _ok(at):
    assert not at.exception, [e.value for e in at.exception]
    return at


def _html(at):
    return " ".join(str(h.proto.body) for h in at.get("html"))


def test_learning_page_supplier_tab_empty(db):
    at = _ok(_learning_page().run())
    assert [t.label for t in at.tabs] == ["Coding accuracy", "Supplier learning", "Readers"]
    assert "No supplier learned yet" in _html(at)


def test_learning_page_is_a_training_view_with_keep_supervised(db, tmp_path, ground_truth, monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Manager Mia")
    store = Store(db)
    ids = _invoice_ids(store, ground_truth, tmp_path, 42)
    for i, invoice_id in enumerate(ids[:30]):
        store.record_outcomes(
            "id:V1", invoice_id, _outcomes(), display_name="Northwind", at=f"2026-01-01T00:00:{i:02d}"
        )
    for i, invoice_id in enumerate(ids[30:]):
        store.record_outcomes("id:V2", invoice_id, _outcomes(bad=i == 7), display_name="Chinook",
                              at=f"2026-01-02T00:00:{i:02d}")  # fmt: skip
    at = _ok(_learning_page().run())
    html = _html(at)
    assert "Northwind" in html and "Ready" in html and "Chinook" in html and "Learning" in html
    assert "12 of 20 invoices; 6 more clean in a row needed" in html
    assert "12 invoices reviewed · 1 correction in the last 12" in html
    assert "about 32 more clean invoices to go touchless" in html  # the accuracy bound needs the most
    assert "touchless processing is on (Settings → Automation)" in html  # Northwind waits for the switch
    assert not [b for b in at.button if (b.key or "").startswith(("sup_on_", "sup_off_"))]  # no per-vendor switch
    assert any("Touchless processing is **off**" in c.value for c in at.caption)
    hold = next(b.key for b in at.button if (b.key or "").startswith("sup_hold_"))
    at.text_input(key="sup_why_" + hold[len("sup_hold_") :]).set_value("prices changing")
    _ok(at.button(key=hold).click().run())
    held = [k for k in ("id:V1", "id:V2") if store.get_supplier_profile(k)["state"] == HELD]
    assert len(held) == 1
    event = store.events(actions=["autonomy_held"])[0]
    assert event["actor"] == "Manager Mia" and event["detail"]["reason"] == "prices changing"
    assert "Kept supervised" in _html(at)
    allow = next(b.key for b in at.button if (b.key or "").startswith("sup_allow_"))
    _ok(at.button(key=allow).click().run())
    assert store.get_supplier_profile(held[0])["state"] == SUPERVISED
    assert store.events(actions=["autonomy_allowed"])[0]["actor"] == "Manager Mia"


def test_template_learns_the_whole_name_with_its_legal_suffix(tmp_path):
    import pymupdf

    from ap_coder.capture import build_layout
    from ap_coder.capture.supplier import apply_template, learn

    paths = []
    for n in range(2):
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((40, 50), "Facture", fontsize=14)
        page.insert_text((330, 50), "Fournitures Laval S.E.N.C.", fontsize=12)
        page.insert_text((40, 90), f"N° facture {1000 + n}", fontsize=10)
        paths.append(tmp_path / f"f{n}.pdf")
        doc.save(paths[-1])
    template = learn(None, build_layout(paths[0], ocr=False), {"vendor_name": "Fournitures Laval S.E.N.C."})
    got = apply_template(template, build_layout(paths[1], ocr=False))["vendor_name"][0]
    assert got.value == "Fournitures Laval S.E.N.C."


def test_template_keeps_the_credit_mark_of_an_amount(tmp_path):
    import pymupdf

    from ap_coder.capture import build_layout
    from ap_coder.capture.supplier import apply_template, learn

    paths = []
    for n, amount in enumerate(("256.28", "1,370.34")):
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((40, 50), "Acme Supply Ltd.", fontsize=14)
        page.insert_text((380, 400), "Credit Total", fontsize=10)
        page.insert_text((480, 400), f"${amount}", fontsize=10)
        page.insert_text((540, 400), "CR", fontsize=10)
        paths.append(tmp_path / f"c{n}.pdf")
        doc.save(paths[-1])
    template = learn(None, build_layout(paths[0], ocr=False), {"grand_total": -256.28})
    got = apply_template(template, build_layout(paths[1], ocr=False))["grand_total"][0]
    assert got.value == -1370.34


def test_supplier_key_for_matches_a_business_number_only_master(tmp_path):
    """The vendor master keeps only the 9-digit Business Number; the invoice prints the full GST/HST account (or the
    other way round): the same registration, so the ERP vendor ID is found."""
    store = Store(tmp_path / "s.db")
    store.import_vendor_master(
        [
            {"vendor_name": "Northwind Supplies", "erp_id": "V1001", "gst": "123456789"},
            {"vendor_name": "Pacific Paper", "erp_id": "V2002", "gst": "555555555RT0001"},
        ]
    )
    assert store.supplier_key_for("Northwind (renamed)", "123456789 RT0001") == "id:V1001"
    assert store.supplier_key_for("Pacific (renamed)", "555555555") == "id:V2002"
    assert store.supplier_key_for("Pacific (renamed)", "555555555RT0002") != "id:V2002"  # another account
