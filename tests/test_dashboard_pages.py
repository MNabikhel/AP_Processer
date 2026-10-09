"""Every dashboard page runs headless (Streamlit AppTest) without errors, on an empty and a busy database.

These catch the mistakes unit tests can't: a wrong asset path, a missing import, a widget-key clash.
"""

import datetime as dt
import json
import os
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder.store import APPROVED, REJECTED, REVIEW, Store, load_sample_setup

from .conftest import ROOT, SAMPLE_STEM, SAMPLES

APP = str(ROOT / "ap_coder" / "dashboard.py")
PAGES = [
    ("process", "page_process"),
    ("accounts", "page_accounts"),
    ("learning", "page_learning"),
    ("settings", "page_settings"),
    ("activity", "page_activity"),
    ("vendors", "page_vendors"),
    ("exports", "page_exports"),
    ("insights", "page_insights"),
    ("purchase_orders", "page_purchase_orders"),
    ("help", "page_help"),
    ("statements", "page_statements"),
    ("month_end", "page_month_end"),
    ("sales_tax", "page_sales_tax"),
    ("spend", "page_spend"),
    ("search", "page_search"),
]
TIMEOUT = 90


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    # The page modules read the database location when first imported: start fresh for each test.
    for name in [m for m in sys.modules if m.startswith(("ap_coder.webapp", "ap_coder.dashboard"))]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()  # the cached Store is process-wide, not per module
    st.cache_data.clear()
    return path


@pytest.fixture
def busy_db(db):
    store = Store(db)
    load_sample_setup(store)
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    invoice_id = store.add_invoice(
        SAMPLES / f"{SAMPLE_STEM}.pdf", gt, {"requires_review": False, "adjusted_confidence": 0.95}
    )
    return store, invoice_id


def _page(module, function):
    return AppTest.from_string(
        f"from ap_coder.webapp.{module} import {function}\n{function}()", default_timeout=TIMEOUT
    )


def _ok(at):
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_every_page_runs_on_an_empty_database(db):
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # onboarding on the review page
    for module, function in PAGES:
        _ok(_page(module, function).run())


def test_every_page_runs_with_invoices(busy_db):
    store, invoice_id = busy_db
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    assert at.button(key=f"qopen_{invoice_id}")  # the invoice is in the queue
    for module, function in PAGES:
        _ok(_page(module, function).run())


def test_open_and_approve_teaches_the_memory(busy_db):
    store, invoice_id = busy_db
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    _ok(at.run())
    _ok(at.button(key=f"inv{invoice_id}_approve").click().run())
    assert store.get_invoice(invoice_id)["status"] == APPROVED
    assert len(store.feedback_rows()) == 5
    assert not at.exception


def test_reject_from_the_more_menu(busy_db):
    store, invoice_id = busy_db
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    _ok(at.run())
    at.text_input(key=f"inv{invoice_id}_reason").input("not our invoice")
    _ok(at.button(key=f"inv{invoice_id}_reject").click().run())
    inv = store.get_invoice(invoice_id)
    assert inv["status"] == REJECTED


def test_an_invalid_date_shows_a_message_not_an_error(busy_db):
    store, invoice_id = busy_db
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    _ok(at.run())
    at.text_input(key=f"inv{invoice_id}_invoice_date").input("2026-02-30")
    _ok(at.run())
    keys = {b.key for b in at.button}
    assert f"inv{invoice_id}_approve" not in keys  # no approving until it is fixed
    assert {f"inv{invoice_id}_reject", f"inv{invoice_id}_delete"} <= keys  # but reject / delete stay available


def test_demo_button_on_the_welcome_screen(db):
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    _ok(at.button(key="demo_load_welcome").click().run())
    store = Store(db)
    assert len(store.list_invoices(REVIEW)) == 8
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    assert len([b for b in at.button if (b.key or "").startswith("qopen_")]) == 8


def test_settings_page_saves_to_the_env_file(db, monkeypatch):
    from ap_coder.envfile import read_env

    env = db.parent / ".env"
    monkeypatch.setenv("AP_ENV_FILE", str(env))
    for key in ("AP_REVIEWER", "AP_REVIEW_THRESHOLD", "AP_VISION", "AP_CONSTRAIN_CODES"):
        monkeypatch.setenv(key, "")  # restored after the test (the page writes os.environ)
    at = _ok(_page("settings", "page_settings").run())
    _ok(at.button(key="backup_now").click().run())
    assert list((db.parent / "backups").glob("ap_coder-*-manual.db"))
    reviewer = next(t for t in at.text_input if t.label == "Your name")
    reviewer.input("Jane Doe")
    review_form_submit = next(b for b in at.button if b.label == "Save review settings" and b.proto.is_form_submitter)
    _ok(review_form_submit.click().run())
    from ap_coder.paths import read_user_settings

    assert read_user_settings()["reviewer"] == "Jane Doe"  # per Windows user, not in the shared .env
    assert "AP_REVIEWER" not in read_env(env)
    assert "AP_REVIEW_THRESHOLD" not in read_env(env)  # only the name changed: the defaults are not written


def test_vendor_detail_and_hold(busy_db):
    store, invoice_id = busy_db
    at = _page("vendors", "page_vendors")
    _ok(at.run())
    at.selectbox(key="vendor_open").select("northwind it solutions")
    _ok(at.run())
    at.radio[0].set_value("on_hold")
    _ok(next(b for b in at.button if b.label == "Save vendor").click().run())
    assert store.get_vendor("northwind it solutions")["status"] == "on_hold"


def test_exports_page_creates_a_batch(db):
    from ap_coder.demo import load_demo

    store = Store(db)
    load_demo(store)
    at = _ok(_page("exports", "page_exports").run())
    _ok(at.button(key="export_create").click().run())
    assert store.unexported_approved() == [] and len(store.export_batches()) == 1


def test_insights_with_the_demo(db):
    from ap_coder.demo import load_demo

    load_demo(Store(db))
    _ok(_page("insights", "page_insights").run())


def test_purchase_orders_page_and_po_coding_on_the_review_screen(db):
    from ap_coder.demo import load_demo

    store = Store(db)
    at = _ok(_page("purchase_orders", "page_purchase_orders").run())
    _ok(at.button(key="po_samples").click().run())
    assert len(store.purchase_orders()) == 4
    load_demo(store)
    _ok(_page("purchase_orders", "page_purchase_orders").run())

    redriver = next(i for i in store.list_invoices() if i["vendor_name"].startswith("Red River"))
    key = f"inv{redriver['id']}"
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = redriver["id"]
    _ok(at.run())
    _ok(at.button(key=f"{key}_po_coding").click().run())
    _ok(at.run())
    assert f"{key}_po_coding" not in {b.key for b in at.button}  # nothing left to apply
    _ok(at.button(key=f"{key}_approve").click().run())
    final = store.get_invoice(redriver["id"])["final_output"]
    assert final["line_items"][0]["predicted_gl_code"] == "1510"  # the PO's GL, not the AI's 6900


def test_sales_tax_page_with_the_demo(db):
    from ap_coder.demo import load_demo

    load_demo(Store(db))
    at = _page("sales_tax", "page_sales_tax")
    at.session_state["tax_start"] = dt.date(2026, 1, 1)
    at.session_state["tax_end"] = dt.date(2026, 12, 31)
    _ok(at.run())
    _ok(at.toggle(key="tax_risky").set_value(False).run())


def test_ask_the_vendor_on_the_review_screen(db):
    store = Store(db)
    load_sample_setup(store)
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", {**gt, "gst_hst_registration_number": ""}, {})
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    _ok(at.run())
    bodies = [c.value for c in at.code if c.value.startswith("Hello")]
    assert bodies and "GST/HST registration number" in bodies[0]


def test_today_strip_on_the_queue(db):
    from ap_coder.demo import load_demo

    load_demo(Store(db))
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    today = [h.proto.body for h in at.get("html") if "<div class='rq-today'><b>Today</b>" in h.proto.body]
    assert today and "ready to export" in today[0]  # the demo's approved invoices are not exported yet


def test_spend_page_and_data_download(db):
    from ap_coder.demo import load_demo

    load_demo(Store(db))
    at = _page("spend", "page_spend")
    at.session_state["spend_start"] = dt.date(2020, 1, 1)
    at.session_state["spend_end"] = dt.date(2030, 12, 31)
    _ok(at.run())
    _ok(at.button(key="spend_prepare").click().run())
    assert at.session_state["spend_xlsx"][0][:2] == b"PK"  # an xlsx (zip) file, ready to download


def test_saved_emails_in_the_invoices_folder_are_unpacked(db):
    from email.message import EmailMessage

    m = EmailMessage()
    m["Subject"] = "Invoice"
    m.set_content("attached")
    m.add_attachment(b"%PDF-1.4 x", maintype="application", subtype="pdf", filename="INV-9.pdf")
    folder = db.parent / "invoices"
    folder.mkdir(parents=True)
    (folder / "vendor mail.eml").write_bytes(m.as_bytes())
    os.utime(folder / "vendor mail.eml", (1, 1))  # copied a while ago
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages the setup steps link to
    at = _ok(_page("process", "page_process").run())
    assert (folder / "vendor mail - INV-9.pdf").exists() and (folder / "emails" / "vendor mail.eml").exists()
    assert any("attachment taken out" in c.value for c in at.caption)


def test_sales_tax_page_self_assessment_card(db):
    store = Store(db)
    load_sample_setup(store)
    doc = json.loads((SAMPLES / "ground_truth" / "prairie_SK_GST_PST_PNS-104882.json").read_text())
    doc["tax_lines"] = [t for t in doc["tax_lines"] if t["tax_type"] != "PST"]
    invoice_id = store.add_invoice(db.parent / "x.pdf", doc, {})
    store.approve_invoice(invoice_id, doc, "Jane")
    at = _page("sales_tax", "page_sales_tax")
    at.session_state["tax_start"] = dt.date(2020, 1, 1)
    at.session_state["tax_end"] = dt.date(2030, 12, 31)
    _ok(at.run())
    assert any("self-assess" in m.value for m in at.markdown)


def test_coding_rules_tab_suggests_and_adds_a_rule(db):
    store = Store(db)
    load_sample_setup(store)
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    same = {**gt, "line_items": [{**li, "predicted_gl_code": "6010"} for li in gt["line_items"]]}
    store.approve_invoice(store.add_invoice(db.parent / "a.pdf", same, {}), same, "Jane")
    at = _ok(_page("accounts", "page_accounts").run())
    _ok(at.button(key="rule_add_0").click().run())
    (rule,) = store.coding_rules()
    assert (rule.vendor, rule.gl_code) == (gt["vendor_name"], "6010")
    assert not [b for b in at.button if b.key == "rule_add_0"]  # covered now: no longer suggested
    _ok(at.button(key="rules_save").click().run())  # saving right after keeps the rule just added
    assert len(store.coding_rules()) == 1


def test_review_screen_shows_the_rules_applied(db):
    store = Store(db)
    load_sample_setup(store)
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    change = {"line_number": 5, "rule": "any vendor, line contains “delivery”", "gl_from": "6800", "gl_to": "6900",
              "cc_from": "CC400", "cc_to": "CC400"}  # fmt: skip
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", gt, {}, meta={"rules_applied": [change]})
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    _ok(at.run())
    assert any("by the rule" in h.proto.body for h in at.get("html"))


def test_apply_a_rule_added_after_processing(busy_db):
    from ap_coder.rules import Rule

    store, invoice_id = busy_db
    store.save_coding_rules([Rule("", "delivery", "6900")])
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    _ok(at.run())
    _ok(at.button(key=f"inv{invoice_id}_apply_rules").click().run())
    assert f"inv{invoice_id}_apply_rules" not in {b.key for b in at.button}  # nothing left to apply
    _ok(at.button(key=f"inv{invoice_id}_approve").click().run())
    lines = store.get_invoice(invoice_id)["final_output"]["line_items"]
    assert next(li for li in lines if li["line_number"] == 5)["predicted_gl_code"] == "6900"
    assert "line_coding" not in store.get_invoice(invoice_id)["edits"]  # keeping a rule's coding is not a change
    line5 = next(r for r in store.feedback_rows() if r["line_number"] == 5)
    assert (line5["suggested_gl"], line5["outcome"]) == ("6800", "corrected")  # the AI's own answer was 6800


def test_duplicate_audit_on_the_activity_page(db):
    store = Store(db)
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    for number in ("NW-2026-0912", "NW-2026-0921"):
        doc = {**gt, "invoice_number": number}
        store.approve_invoice(store.add_invoice(db.parent / f"{number}.pdf", doc, {}), doc, "Jane")
    at = _ok(_page("activity", "page_activity").run())
    _ok(at.button(key="dupaudit_run").click().run())
    assert len(at.session_state["dupaudit"]) == 1


def test_approved_pdfs_from_the_exports_page(db):
    from ap_coder.demo import load_demo

    store = Store(db)
    load_demo(store)
    at = _ok(_page("exports", "page_exports").run())
    _ok(at.button(key="export_create").click().run())
    _ok(at.button(key="export_pdfs_make").click().run())
    batch, data = at.session_state["export_pdfs"]
    assert batch == 1 and data[:2] == b"PK"


def test_approved_invoice_view_offers_the_stamped_pdf(busy_db):
    store, invoice_id = busy_db
    gt = store.get_invoice(invoice_id)["ai_output"]
    store.approve_invoice(invoice_id, gt, "Jane")
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    _ok(at.run())
    keys = [getattr(b, "key", "") or b.proto.id for b in at.get("download_button")]  # in the Approved tab
    assert any(f"approved_pdf_{invoice_id}" in k for k in keys)


def test_vendors_page_with_the_demo(db):
    from ap_coder.demo import load_demo
    from ap_coder.insights import vendor_workload

    load_demo(Store(db))
    at = _ok(_page("vendors", "page_vendors").run())
    if vendor_workload(Store(db)):
        assert any("make work" in m.value for m in at.markdown)


def test_settings_backup_copy_folder(db, tmp_path):
    second = tmp_path / "onedrive"
    second.mkdir()
    at = _ok(_page("settings", "page_settings").run())
    field = next(t for t in at.text_input if t.label.startswith("Also copy each backup"))
    field.input(f'"{second}"')  # pasted with quotes, as Windows "Copy as path" gives it
    submit = next(b for b in at.button if b.proto.is_form_submitter and b.proto.form_id.endswith("backup_copy_form"))
    _ok(submit.click().run())
    assert Store(db).get_setting("backup_copy_dir") == str(second) and list(second.glob("ap_coder-*.db"))


def test_find_an_invoice_and_open_it(db):
    from ap_coder.demo import load_demo

    store = Store(db)
    load_demo(store)
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages
    at = _page("search", "page_search")
    _ok(at.run())
    at.text_input(key="search_query").input("red river")
    _ok(at.run())
    target = next(i for i in store.list_invoices() if i["vendor_name"].startswith("Red River"))
    assert at.button(key=f"search_open_{target['id']}")


def test_reopen_from_the_approved_view(busy_db):
    store, invoice_id = busy_db
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Jane")
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    at.text_input(key=f"reopen_reason_{invoice_id}").input("wrong cost center")
    _ok(at.run())
    _ok(at.button(key=f"reopen_{invoice_id}").click().run())
    assert store.get_invoice(invoice_id)["status"] == "review"
    assert at.button(key=f"inv{invoice_id}_approve")  # opened straight away for the correction
    assert any("Reopened by" in h.proto.body and "wrong cost center" in h.proto.body for h in at.get("html"))


def test_spend_all_currencies_in_cad(db):
    from ap_coder.demo import load_demo

    store = Store(db)
    load_demo(store)
    cascade = next(i for i in store.list_invoices() if i["currency"] == "USD")
    store.approve_invoice(cascade["id"], store.get_invoice(cascade["id"])["ai_output"], "Jane")
    store.set_setting("fx_rates", "USD=1.37")
    at = _page("spend", "page_spend")
    at.session_state["spend_start"] = dt.date(2020, 1, 1)
    at.session_state["spend_end"] = dt.date(2030, 12, 31)
    _ok(at.run())
    assert at.selectbox(key="spend_currency").options[0] == "All, in CAD"
    _ok(at.selectbox(key="spend_currency").select("All, in CAD").run())
    at = _page("sales_tax", "page_sales_tax")
    at.session_state["tax_start"] = dt.date(2020, 1, 1)
    at.session_state["tax_end"] = dt.date(2030, 12, 31)
    _ok(at.run())


def test_a_credit_note_is_not_shown_as_due(db):
    from ap_coder.demo import load_demo

    store = Store(db)
    load_demo(store)
    credit = next(i for i in store.list_invoices() if (i["grand_total"] or 0) < 0)
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = credit["id"]
    _ok(at.run())
    hero = next(h.proto.body for h in at.get("html") if "rvw-head" in h.proto.body)
    assert "<span>Credit</span>" in hero and "Due in" not in hero and "Overdue" not in hero


def test_find_brings_back_a_parked_invoice(busy_db):
    store, invoice_id = busy_db
    store.park_invoice(invoice_id, "Jane", "waiting for the buyer")
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages
    at = _page("search", "page_search")
    _ok(at.run())
    at.text_input(key="search_query").input("northwind")
    _ok(at.run())
    at.button(key=f"search_unpark_{invoice_id}").click().run()
    assert store.get_invoice(invoice_id)["status"] == "review"


def test_find_offers_the_approved_pdf(busy_db):
    store, invoice_id = busy_db
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Jane")
    at = _page("search", "page_search")
    _ok(at.run())
    at.text_input(key="search_query").input("northwind")
    _ok(at.run())
    _ok(at.button(key=f"search_pdf_make_{invoice_id}").click().run())
    name, data, _ = at.session_state[f"search_pdf_{invoice_id}"]
    assert name.endswith("approved.pdf") and data[:4] == b"%PDF"
