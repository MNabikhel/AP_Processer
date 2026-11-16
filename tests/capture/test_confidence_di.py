"""Azure Document Intelligence's reading among AP Coder's own (capture/confidence.py, D-245): the local readers come
first, a disagreement goes to a person, agreeing raises the confidence, and a value only Azure read is at most
likely."""

import datetime as dt

from ap_coder.capture.confidence import fuse
from ap_coder.capture.types import CHECK, LIKELY, VERIFIED, Box, Reading

TODAY = dt.date(2026, 10, 20)
RULES_BOX, AZURE_BOX = Box(1, 0.6, 0.1, 0.8, 0.12), Box(1, 0.61, 0.1, 0.81, 0.121)
AZURE = "Azure Document Intelligence"


def _rules(field, value, score=0.95, method="label-right", raw=None):
    return Reading(field, value, raw or str(value), [RULES_BOX], score, method)


def _azure(field, value, score=0.95, raw=None, boxed=True):
    return Reading(field, value, raw or str(value), [AZURE_BOX] if boxed else [], score, "di")


def _fuse(by_source, fields):
    return fuse(by_source, [], fields=fields, today=TODAY)[0]


def test_the_local_reading_comes_first_and_a_disagreement_goes_to_a_person():
    for rules_score in (0.95, 0.4):  # however much surer Azure is
        by_source = {"rules": {"invoice_number": [_rules("invoice_number", "INV-1001", rules_score)]},
                     "di": {"invoice_number": [_azure("invoice_number", "INV-1007", 0.99)]}}  # fmt: skip
        fr = _fuse(by_source, ("invoice_number",))["invoice_number"]
        assert fr.value == "INV-1001" and fr.status == CHECK
        assert f"{AZURE} read INV-1007, the rule reader read INV-1001" in fr.reasons
        assert fr.sources.get("other:INV-1007") == "di"


def test_agreeing_lifts_a_lone_readers_likely_to_verified_and_shows_the_pages_own_box():
    alone = _fuse({"rules": {"invoice_number": [_rules("invoice_number", "INV-1001")]}}, ("invoice_number",))
    assert alone["invoice_number"].status == LIKELY  # one reader, no check: never verified on its own
    by_source = {"rules": {"invoice_number": [_rules("invoice_number", "INV-1001")]},
                 "di": {"invoice_number": [_azure("invoice_number", "INV-1001", 0.99)]}}  # fmt: skip
    fr = _fuse(by_source, ("invoice_number",))["invoice_number"]
    assert fr.status == VERIFIED and fr.confidence >= alone["invoice_number"].confidence
    assert fr.boxes == [RULES_BOX]  # the rule reader's exact word, not Azure's box
    assert fr.sources == {"rules": "INV-1001", "di": "INV-1001"}


def test_agreeing_never_lowers_the_confidence():
    total = {"subtotal": [_rules("subtotal", 100.0)], "gst_amount": [_rules("gst_amount", 5.0)],
             "grand_total": [_rules("grand_total", 105.0)]}  # fmt: skip
    fields = ("subtotal", "gst_amount", "grand_total")
    alone = _fuse({"rules": total}, fields)
    both = _fuse({"rules": total, "di": {f: [_azure(f, r[0].value)] for f, r in total.items()}}, fields)
    for f in fields:
        assert both[f].confidence >= alone[f].confidence and both[f].status == VERIFIED


def test_a_value_only_azure_read_fills_the_field_but_is_at_most_likely():
    fr = _fuse({"rules": {}, "di": {"po_number": [_azure("po_number", "PO-88213", 0.99)]}}, ("po_number",))["po_number"]
    assert fr.value == "PO-88213" and fr.status != VERIFIED and fr.confidence < 0.985
    assert f"read only by {AZURE}" in fr.reasons
    # Even confirmed by the totals adding up: Azure's total, the page's subtotal and tax.
    by_source = {"rules": {"subtotal": [_rules("subtotal", 100.0)], "gst_amount": [_rules("gst_amount", 5.0)]},
                 "di": {"grand_total": [_azure("grand_total", 105.0, 0.99)]}}  # fmt: skip
    total = _fuse(by_source, ("subtotal", "gst_amount", "grand_total"))["grand_total"]
    assert total.value == 105.0 and any("confirmed" in r for r in total.reasons) and total.status == LIKELY


def test_azures_date_printed_either_way_round_is_for_a_person():
    by_source = {"di": {"invoice_date": [_azure("invoice_date", "2026-04-03", 0.99, raw="03/04/2026")]}}
    fr = _fuse(by_source, ("invoice_date",))["invoice_date"]
    assert fr.status == CHECK and any("day/month" in r for r in fr.reasons)


def test_the_page_readers_and_azures_disagreement_is_said_once_each():
    by_source = {"rules": {"invoice_number": [_rules("invoice_number", "INV-1001")]},
                 "vlm": {"invoice_number": [Reading("invoice_number", "INV-1001", "INV-1001", [RULES_BOX], 0.9,
                                                   "label-right")]},
                 "di": {"invoice_number": [_azure("invoice_number", "INV-1007")]}}  # fmt: skip
    fr = _fuse(by_source, ("invoice_number",))["invoice_number"]
    assert fr.value == "INV-1001" and fr.status == CHECK
    assert sum(AZURE in r for r in fr.reasons) == 1


# --- Never below the readers on this computer alone (the review of D-245) ------------------------------------------


def test_the_total_tax_the_page_works_out_stays_verified_when_azure_reads_it_too():
    from ap_coder.capture import analyze
    from tests.conftest import SAMPLES
    from tests.fake_di import recorded

    stem = "northwind_ON_HST_NW-2026-0912"
    alone = analyze(SAMPLES / f"{stem}.pdf")
    both = analyze(SAMPLES / f"{stem}.pdf", di_raw=recorded(stem))
    local, merged = alone.fields["tax_total"], both.fields["tax_total"]
    assert local.status == VERIFIED and "computed" in local.sources
    assert merged.status == VERIFIED and merged.confidence >= local.confidence and merged.value == local.value
    assert merged.sources.get("di") and "computed" in merged.sources


def test_a_total_tax_only_azure_read_that_differs_from_the_taxes_goes_to_a_person():
    by_source = {"rules": {"subtotal": [_rules("subtotal", 100.0)], "hst_amount": [_rules("hst_amount", 13.0)],
                           "grand_total": [_rules("grand_total", 113.0)]},
                 "di": {"tax_total": [_azure("tax_total", 31.0)]}}  # fmt: skip
    fr = _fuse(by_source, ("subtotal", "hst_amount", "tax_total", "grand_total"))["tax_total"]
    assert fr.value == 13.0 and fr.status == CHECK
    assert any(r.startswith(f"{AZURE} read 31.00") for r in fr.reasons) and fr.sources.get("other:31.0") == "di"
