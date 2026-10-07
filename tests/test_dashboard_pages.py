"""Every dashboard page runs headless (Streamlit AppTest) without errors, on an empty and a busy database.

These catch the mistakes unit tests can't: a wrong asset path, a missing import, a widget-key clash.
"""

import json
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder.store import APPROVED, REJECTED, REVIEW, Store, load_sample_setup

from .conftest import ROOT, SAMPLE_STEM, SAMPLES

APP = str(ROOT / "ap_coder" / "dashboard.py")
PAGES = [("process", "page_process"), ("accounts", "page_accounts"), ("learning", "page_learning")]
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
