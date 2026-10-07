"""Every dashboard page runs headless (Streamlit AppTest) without errors, on an empty and a busy database.

These catch the mistakes unit tests can't: a wrong asset path, a missing import, a widget-key clash.
"""

import datetime as dt
import json
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
    review_form_submit = next(b for b in at.button if b.label == "Save" and b.proto.is_form_submitter)
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
    today = [
        h.proto.body for h in at.get("html") if "<b style='color:#142033;margin-right:.2rem'>Today</b>" in h.proto.body
    ]
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
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages the setup steps link to
    at = _ok(_page("process", "page_process").run())
    assert (folder / "vendor mail - INV-9.pdf").exists() and (folder / "emails" / "vendor mail.eml").exists()
    assert any("saved email" in c.value for c in at.caption)
