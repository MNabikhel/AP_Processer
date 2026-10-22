"""Problems found by using AP Coder end to end: approval stamps, GST/HST check digits, what counts as an AI
correction, how confidence and weeks are shown, per-reviewer counts, vendor spend, exports, set-up steps."""

import datetime as dt
import io
import json
import re
import zipfile

import pytest

from ap_coder.coding import taxreturn
from ap_coder.coding.tax import check_taxes, gst_check_digit_ok
from ap_coder.core.memory import ACCEPTED, CODED, CORRECTED
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import APPROVED, PENDING, Store, load_sample_cost_centers, load_sample_setup
from ap_coder.erp import stamp
from ap_coder.erp.insights import _week
from ap_coder.ops import help as help_catalog

from .conftest import SAMPLE_STEM, SAMPLES
from .webclient import client_for, follow, post


def _gt(stem=SAMPLE_STEM):
    return json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text())  # Northwind: 18,017.85 CAD


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Sam")
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


# --- 1. No APPROVED stamp before the second approval ---------------------------------------------------------------


def test_an_invoice_waiting_for_its_second_approver_gets_no_approved_stamp(tmp_path):
    store = Store(tmp_path / "s.db")
    store.set_setting("approval_limit", "10000")
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")
    waiting = store.get_invoice(invoice_id)
    assert waiting["status"] == PENDING and not stamp.can_stamp(waiting)
    with pytest.raises(ValueError, match="not fully approved"):
        stamp.stamped_pdf(waiting)
    with zipfile.ZipFile(io.BytesIO(stamp.batch_zip([waiting]))) as z:
        assert z.namelist() == []  # left out of a ZIP too
    store.final_approve(invoice_id, "Sam")
    approved = store.get_invoice(invoice_id)
    assert approved["status"] == APPROVED and stamp.stamped_pdf(approved)[:4] == b"%PDF"


def test_find_does_not_offer_the_approved_pdf_while_it_waits(db):
    db.set_setting("approval_limit", "10000")
    invoice_id = db.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", _gt(), {})
    db.approve_invoice(invoice_id, _gt(), "Jane")
    page = client_for(db).get("/search", params={"q": "northwind"}).text
    assert f"/search/{invoice_id}/approved.pdf" not in page
    assert "once the second approver has approved it" in page


# --- 2. GST/HST check digit ---------------------------------------------------------------------------------------


def test_a_gst_number_failing_the_check_digit_is_a_warning(reference):
    gt = _gt()
    gt["gst_hst_registration_number"] = "123456789 RT0001"  # right format, wrong check digit
    codes = {f.code for f in check_taxes(InvoiceCoding.model_validate(gt), reference.tax, None)}
    assert "GST_NUMBER_CHECK_DIGIT" in codes and "GST_HST_NUMBER_FORMAT" not in codes
    assert help_catalog.help_for("GST_NUMBER_CHECK_DIGIT") is not None
    gt["gst_hst_registration_number"] = "123456782 RT0001"
    codes = {f.code for f in check_taxes(InvoiceCoding.model_validate(gt), reference.tax, None)}
    assert "GST_NUMBER_CHECK_DIGIT" not in codes


def test_sales_tax_lists_a_claim_whose_gst_number_fails_the_check(tmp_path):
    store = Store(tmp_path / "t.db")
    doc = {**_gt(), "gst_hst_registration_number": "123456789 RT0001"}
    invoice_id = store.add_invoice(tmp_path / "a.pdf", doc, {})
    store.approve_invoice(invoice_id, doc, "Jane")
    report = taxreturn.build(store, dt.date(2020, 1, 1), dt.date(2030, 12, 31))
    assert [c.invoice_id for c in report.at_risk()] == [invoice_id]
    assert taxreturn.GST_CHECK_DIGIT in report.at_risk()[0].issues


def test_the_sample_invoices_use_numbers_that_pass_the_check_digit():
    for path in (SAMPLES / "ground_truth").glob("*.json"):
        number = json.loads(path.read_text())["gst_hst_registration_number"]
        assert not number or gst_check_digit_ok(number), path.name
        assert not number or number in (SAMPLES / f"{path.stem}.md").read_text(encoding="utf-8")


# --- 3. Filling in a blank is not a correction --------------------------------------------------------------------


def test_filling_in_a_blank_cost_center_confirms_the_ai(tmp_path):
    store = Store(tmp_path / "s.db")
    load_sample_cost_centers(store)  # a cost center to pick needs a list (without one only the GL counts: B-562)
    ai = _gt()
    for li in ai["line_items"]:
        li["predicted_cost_center"] = "UNASSIGNED"  # the AI left the cost center to pick
    ai["line_items"][1].update(predicted_gl_code="UNASSIGNED", predicted_cost_center="CC400")  # a GL to pick
    final = _gt()
    final["line_items"][0]["predicted_gl_code"] = "6020"  # a real correction
    invoice_id = store.add_invoice(tmp_path / "a.pdf", ai, {})
    counts = store.approve_invoice(invoice_id, final, "Jane")
    assert counts == {ACCEPTED: 4, CORRECTED: 1, CODED: 0}
    assert store.metrics()["line_accuracy"] == pytest.approx(0.8)


def test_only_filling_in_blanks_is_not_a_coding_change(tmp_path):
    store = Store(tmp_path / "s.db")
    ai = _gt()
    for li in ai["line_items"]:
        li["predicted_cost_center"] = ""
    invoice_id = store.add_invoice(tmp_path / "a.pdf", ai, {})
    counts = store.approve_invoice(invoice_id, _gt(), "Jane")
    assert counts[CORRECTED] == 0
    assert "line_coding" not in store.get_invoice(invoice_id)["edits"]


# --- 4. Confidence shown one way ----------------------------------------------------------------------------------


def test_confidence_rounds_like_the_messages_without_misleading():
    from ap_coder.web.pages.review import confidence_pct

    assert confidence_pct(0.837, 0.85) == "84%"  # rounded the usual way, as the bulk approval says it
    assert confidence_pct(0.876, 0.85) == "88%"
    assert confidence_pct(0.849, 0.85) == "84.9%"  # never shown as the threshold it is below
    assert confidence_pct(0.996, 0.85) == "99.6%"  # never shown as certainty
    assert confidence_pct(1.0, 0.85) == "100%"


# --- 5. Approved today: this reviewer's -----------------------------------------------------------------------------


def test_approved_today_counts_this_reviewers_approvals(db):
    from ap_coder.web.core import approved_today

    for n, name in enumerate(("Jane", "Jane", "sam ")):
        invoice_id = db.add_invoice(db.path.parent / f"{n}.pdf", {**_gt(), "invoice_number": f"A-{n}"}, {})
        db.approve_invoice(invoice_id, {**_gt(), "invoice_number": f"A-{n}"}, name)
    today = dt.date.today().isoformat()
    assert db.approved_since(today) == 3
    assert db.approved_since(today, "Jane") == 2
    assert approved_today(db) == 1  # AP_REVIEWER is Sam


def test_approved_today_matches_accented_names_without_case(db):
    invoice_id = db.add_invoice(db.path.parent / "e.pdf", {**_gt(), "invoice_number": "E-1"}, {})
    db.approve_invoice(invoice_id, {**_gt(), "invoice_number": "E-1"}, "Élise Tremblay")
    today = dt.date.today().isoformat()
    assert db.approved_since(today, "élise tremblay") == 1
    assert db.approved_since(today, "Élise Tremblay ") == 1


# --- 6. ISO weeks ---------------------------------------------------------------------------------------------------


def test_accuracy_by_week_uses_iso_weeks_like_insights(tmp_path):
    store = Store(tmp_path / "s.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")
    with store._conn() as conn:
        conn.execute("UPDATE feedback SET created_at = '2026-10-10T10:00:00'")
    (week,) = store.metrics()["weekly"]
    assert week["week"] == _week("2026-10-10T10:00:00") == "2026-W41"
    assert (week["accepted"], week["corrected"]) == (5, 0)


# --- 7. Vendor spend per currency -----------------------------------------------------------------------------------


def test_a_usd_vendor_shows_its_usd_spend_not_zero_cad(db):
    doc = _gt("cascade_US_SalesTax_INV-30981")
    invoice_id = db.add_invoice(SAMPLES / "cascade.pdf", doc, {})
    db.approve_invoice(invoice_id, doc, "Jane")
    (row,) = db.vendor_summaries()
    assert row["spend"] == {"USD": pytest.approx(doc["grand_total"])}
    page = client_for(db).get("/vendors").text
    assert "Spend (CAD)" not in page and '<span class="muted">USD</span>' in page


# --- 8. Download again ------------------------------------------------------------------------------------------------


def test_download_again_defaults_to_the_batchs_format_and_warns_about_jde_left_outs(db):
    invoice_id = db.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", _gt(), {})
    db.approve_invoice(invoice_id, _gt(), "Jane")
    batch = db.create_export_batch([invoice_id], "csv")
    client = client_for(db)
    page = client.get("/exports", params={"batch": batch}).text
    pick = page.split('name="bfmt"', 1)[1].split("</select>", 1)[0]
    assert '<option value="csv" selected>' in pick
    assert "cannot go to JD Edwards and" not in page
    warning = re.sub(r"\s+", " ", client.get("/exports", params={"batch": batch, "bfmt": "jde"}).text)
    assert f"1 of 1 invoice in batch {batch} cannot go to JD Edwards" in warning and "holds no invoice" in warning


# --- 9. Page reader set-up steps ------------------------------------------------------------------------------------


def test_setup_steps_never_claim_the_self_test_passed_before_it_has(tmp_path):
    from ap_coder.ai.reading import ReadingStatus
    from ap_coder.web.pages.settings_reading import setup_steps

    found = ReadingStatus(page_reader_model="ovis", self_test="pending")
    steps = {label: (state, detail) for state, label, detail in setup_steps(found, True, (False, ""))}
    assert steps["Self-test passed (automatic)"] == ("wait", "starts by itself in a moment")
    down = ReadingStatus(page_reader_model="", self_test="no model")
    steps = {label: (state, detail) for state, label, detail in setup_steps(down, False, (False, ""))}
    assert "start LM Studio first" in steps["OvisOCR2 is in LM Studio"][1]
    assert "copy" not in steps["OvisOCR2 is in LM Studio"][1]


# --- 10. Over the limit: first approval, not "Approved" ---------------------------------------------------------------


def test_over_the_limit_message_says_first_approval_recorded(db):
    from .test_web_invoice import form_of

    db.set_setting("approval_limit", "10000")
    invoice_id = db.add_invoice(
        SAMPLES / f"{SAMPLE_STEM}.pdf", _gt(), {"requires_review": False, "adjusted_confidence": 0.95}
    )
    client = client_for(db)
    page = follow(client, post(client, f"/review/{invoice_id}/approve", form_of(db, invoice_id)))
    assert db.get_invoice(invoice_id)["status"] == PENDING
    assert "First approval recorded for" in page and "second approver" in page
    assert "Approved Northwind" not in page
