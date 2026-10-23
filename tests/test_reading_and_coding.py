"""Confidence as two numbers (D-89, B-843): the Reading, how sure the header is, rises with what the supplier's
template has read right; the Coding, how sure the lines' accounts, cost centers and tax codes are, counts a line to code
once (no second penalty). The invoice's confidence is the lower."""

from __future__ import annotations

import copy
import re

import pytest

from ap_coder.capture.confidence import apply_supplier_record, reading_confidence
from ap_coder.capture.types import LIKELY, VERIFIED, FieldResult
from ap_coder.coding.validation import validate_coding
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.schema import InvoiceCoding

from .test_web_invoice import check, form_of
from .test_web_review_signoff import _id, _jde_on, client, demo_db, me, store

__all__ = ["client", "demo_db", "me", "store"]  # the fixtures, shared


# --- The Reading rises with the supplier's record ----------------------------------------------------------------


def _field(name="invoice_number", confidence=0.985, status=VERIFIED, readers="rules+template"):
    return FieldResult(name, "INV-1", confidence, status, evidence=f"{name}|{readers}|label-right|text|confirmed")


def test_a_verified_value_the_template_read_is_as_sure_as_the_suppliers_record():
    """B-843: a supplier's 2nd and 3rd invoices read right through its template, yet confidence stayed where a
    first-time supplier's was: the readers' confidence tops out at 98.5% (verified) whatever the supplier's record."""
    lifted = {"invoice_number": _field()}
    apply_supplier_record(lifted, {"invoice_number": (2, 2)})
    assert lifted["invoice_number"].confidence == pytest.approx((2 + 2 * 0.985) / 4)  # 99.3% after two right
    many = {"invoice_number": _field()}
    apply_supplier_record(many, {"invoice_number": (40, 40)})
    assert many["invoice_number"].confidence >= 0.999 and many["invoice_number"].status == VERIFIED
    assert "read right on 40 of this supplier's last 40 invoices" in many["invoice_number"].reasons


@pytest.mark.parametrize(
    "result, record",
    [
        (_field(), None),  # a first-time supplier: no record
        (_field(), {"invoice_number": (10, 7)}),  # corrections: never lowered, not lifted
        (_field(confidence=0.9, status=LIKELY), {"invoice_number": (10, 10)}),  # not verified: a person looks
        (_field(readers="ocr+rules"), {"invoice_number": (10, 10)}),  # the template did not vouch for it
    ],
)
def test_the_record_lifts_only_a_verified_value_the_template_vouched_for(result, record):
    before = result.confidence
    apply_supplier_record({"invoice_number": result}, record)
    assert result.confidence == before


def test_the_reading_is_the_mean_confidence_of_the_values_found():
    fields = {"a": _field("a", 1.0), "b": _field("b", 0.9), "c": FieldResult("c", None, 0.0, "missing")}
    assert reading_confidence(fields) == 0.95


# --- The Coding counts a line to code once -----------------------------------------------------------------------


def test_a_line_to_code_lowers_the_coding_by_its_share_and_nothing_else(reference, ground_truth):
    """B-843: lines to code halved the confidence (×(0.5 + 0.5 × share coded)), then GL_UNASSIGNED took 10% more."""
    gt = copy.deepcopy(ground_truth)
    n = len(gt["line_items"])
    gt["line_items"][0]["predicted_gl_code"] = UNASSIGNED
    report = validate_coding(InvoiceCoding.model_validate(gt), reference)
    assert report.reading_confidence == 1.0  # the reading is not in doubt
    assert report.coding_confidence == pytest.approx((n - 1) / n, abs=1e-4)
    assert report.adjusted_confidence == min(report.reading_confidence, report.coding_confidence)
    assert "GL_UNASSIGNED" in {i.code for i in report.issues} and report.requires_review  # still a blocker


def test_a_clean_invoice_is_sure_of_both(reference, ground_truth):
    report = validate_coding(InvoiceCoding.model_validate(ground_truth), reference)
    assert (report.reading_confidence, report.coding_confidence, report.adjusted_confidence) == (1.0, 1.0, 1.0)


def test_a_reading_doubt_lowers_the_reading_only(reference, ground_truth):
    gt = copy.deepcopy(ground_truth)
    gt["grand_total"] += 1.0  # the total no longer adds up: the reading is in doubt, the accounts are not
    report = validate_coding(InvoiceCoding.model_validate(gt), reference)
    assert report.reading_confidence < 1.0 and report.coding_confidence == 1.0


def test_the_offline_coder_gives_the_reading_without_halving_it_for_lines_to_code(reference):
    """B-843: a first invoice (no history: every line to code) was given half its reading's confidence."""
    import datetime as dt

    from ap_coder.ai.offline_coder import code_from_capture
    from ap_coder.bench.coding import BILL_TO, Line, _capture

    lines = [Line("Copy paper 8.5x11, case of 10", "Copy paper 8.5x11, case of 10", "en", 2.0, 40.0, 80.0)]
    capture = _capture("Never Seen Ltd.", "N-1", dt.date(2026, 3, 2), lines)
    coding = code_from_capture(capture, reference, [], None, text=BILL_TO).coding
    assert coding.line_items[0].predicted_gl_code == UNASSIGNED
    assert coding.confidence_score == pytest.approx(0.99)  # the values' own, not halved
    assert validate_coding(coding, reference).coding_confidence == 0.0


def test_the_suppliers_record_is_its_field_accuracy(tmp_path):
    from ap_coder.capture.workflow import supplier_record
    from ap_coder.core.store import Store

    store = Store(tmp_path / "ap.db")
    rows = [{"field": "invoice_number", "ai_value": "A", "final_value": "A", "correct": True},
            {"field": "grand_total", "ai_value": "1", "final_value": "2", "correct": False}]  # fmt: skip
    store.record_outcomes("acme", 1, rows, display_name="Acme")
    assert supplier_record(store, "acme") == {"invoice_number": (1, 1), "grand_total": (1, 0)}
    assert supplier_record(None, "acme") is None and supplier_record(store, "") is None


# --- On the review screen ----------------------------------------------------------------------------------------


def _meters(html: str) -> dict[str, str]:
    """{"Reading": "97%", "Coding": "75%"}: the header's two numbers, as shown."""
    return dict(re.findall(r'rq-conf-label">([^<]+)</span>.*?<b>([^<]*)</b>', html, re.S))


def test_the_header_shows_reading_and_coding_and_an_uncoded_line_lowers_only_coding(client, store):
    """D-89: one Confidence mixed how sure the reading was with the lines left to code (counted twice: halved, then
    taken 10% off again by "no GL account yet"). The header now shows the two."""
    invoice_id = _id(store, "Pacific")
    before = _meters(check(client, invoice_id, form_of(store, invoice_id))["sections"]["summary"])
    assert set(before) == {"Reading", "Coding"}, before
    form = form_of(store, invoice_id)
    form["line_gl"] = ["UNASSIGNED"] + list(form["line_gl"][1:])
    after = check(client, invoice_id, form)
    shown = _meters(after["sections"]["summary"])
    assert shown["Reading"] == before["Reading"]  # the reading is as sure as it was
    n = len(form["line_gl"])
    assert shown["Coding"] == f"{round(100 * (n - 1) / n)}%", (shown, n)  # one line of n to code: no more off
    assert "Not ready" in after["sections"]["bar"] or not after["allow"]  # still a blocker
    assert "Coding" not in after["sections"]["checks"]  # the blocker says it: no "Coding … below the threshold"


def test_jd_edwards_not_set_up_takes_nothing_off_reading_or_coding(client, store):
    """B-799 still holds with the two numbers: a setup blocker is a blocker, not doubt."""
    invoice_id = _id(store, "Pacific")
    before = _meters(check(client, invoice_id, form_of(store, invoice_id))["sections"]["summary"])
    _jde_on(store)
    after = _meters(check(client, invoice_id, form_of(store, invoice_id))["sections"]["summary"])
    assert after == before


def test_an_invoice_read_before_gets_its_reading_once(tmp_path):
    """D-89: an invoice still to approve, read locally by an older version (its confidence multiplied by the share of
    lines coded), shows the Reading of its stored capture; one read by a chat model keeps its own."""
    import json

    from ap_coder.ai.offline_coder import MODEL_NAME
    from ap_coder.core.store import Store

    store = Store(tmp_path / "ap.db")
    coding = {"vendor_name": "Acme", "invoice_number": "A-1", "confidence_score": 0.47, "line_items": []}
    ids = [store.add_invoice(tmp_path / f"{m}.pdf", coding, {}, meta={"inference": {"model": m}})
           for m in (MODEL_NAME, "gpt-4o")]  # fmt: skip
    for invoice_id in ids:
        store.save_capture(invoice_id, {"fields": {"vendor_name": _field("vendor_name", 0.98).to_dict(),
                                                   "invoice_number": _field(confidence=0.96).to_dict()}})  # fmt: skip
    with store._conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'reading_d89'")
    store = Store(tmp_path / "ap.db")  # opened by this version
    assert [store.get_invoice(i)["ai_output"]["confidence_score"] for i in ids] == [0.97, 0.47]
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", (json.dumps(coding), ids[0]))
    assert Store(tmp_path / "ap.db").get_invoice(ids[0])["ai_output"]["confidence_score"] == 0.47  # once only


def test_an_invoice_read_before_is_sorted_and_flagged_by_its_new_reading(tmp_path, ground_truth):
    """The one-time Reading (D-89) refreshed the confidence alone: the queue still sorted and flagged the invoice by
    its old figure (0.47: needs review) until it was checked again. Its confidence for the queue and its review flag
    follow too, from its stored findings, and its checks are done again in the queue (B-895)."""
    import json

    from ap_coder.ai.offline_coder import MODEL_NAME
    from ap_coder.core.store import Store

    store = Store(tmp_path / "ap.db")
    coding = dict(copy.deepcopy(ground_truth), confidence_score=0.47)
    old = {"model_confidence": 0.47, "adjusted_confidence": 0.47, "requires_review": True, "review_threshold": 0.85,
           "issues": [{"severity": "warning", "code": "HISTORY_CONFLICT", "message": "?", "line_number": 1}],
           "checks_stamp": "before"}  # fmt: skip
    clean = {**old, "issues": []}
    ids = [store.add_invoice(tmp_path / f"{n}.pdf", coding, validation, meta={"inference": {"model": MODEL_NAME}})
           for n, validation in enumerate((clean, old))]  # fmt: skip
    for invoice_id in ids:
        store.save_capture(invoice_id, {"fields": {"vendor_name": _field("vendor_name", 0.98).to_dict(),
                                                   "invoice_number": _field(confidence=0.96).to_dict()}})  # fmt: skip
    with store._conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'reading_d89'")
    store = Store(tmp_path / "ap.db")  # opened by this version
    rows = {r["id"]: r for r in store.list_invoices("review")}
    assert (rows[ids[0]]["adjusted_confidence"], bool(rows[ids[0]]["requires_review"])) == (0.97, False)
    assert (rows[ids[1]]["adjusted_confidence"], bool(rows[ids[1]]["requires_review"])) == (0.9, True)  # B-892
    validation = store.get_invoice(ids[0])["validation"]
    assert (validation["adjusted_confidence"], validation["reading_confidence"]) == (0.97, 0.97)
    assert validation["requires_review"] is False and validation["coding_confidence"] == 1.0
    assert store.get_invoice(ids[1])["validation"]["reading_confidence"] == 0.97  # the coding doubt is the Coding's
    assert store.get_invoice(ids[1])["validation"]["coding_confidence"] == 0.9
    assert all(stamp == "" for stamp, _ in store.check_stamps(ids).values())  # checked again in the queue
    assert json.loads(json.dumps(validation["issues"])) == []


def test_a_business_finding_keeps_the_invoice_back_but_takes_nothing_off(tmp_path, reference, ground_truth):
    """D-89: a vendor not in the vendor master read every value right: an error to fix, not a doubt about the reading
    (it made a supplier's Reading 54% with every field at 99.9%)."""
    from ap_coder.coding.validation import ERROR

    report = validate_coding(
        InvoiceCoding.model_validate(ground_truth), reference,
        vendor_findings=[(ERROR, "VENDOR_NOT_IN_MASTER", "not in the vendor list"), ("warning", "PO_UNKNOWN", "?")],
    )  # fmt: skip
    assert (report.reading_confidence, report.coding_confidence) == (1.0, 1.0)
    assert report.requires_review and "VENDOR_NOT_IN_MASTER" in {i.code for i in report.errors}
