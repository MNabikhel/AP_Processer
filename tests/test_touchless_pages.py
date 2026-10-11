"""Touchless processing on the dashboard: Settings → Automation, the Learning page as a training view, and what the
Review page says an approval taught."""

import json
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder.store import Store, load_sample_setup

from .conftest import ROOT, SAMPLE_STEM, SAMPLES
from .test_touchless import _ready

# --- The dashboard -------------------------------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    monkeypatch.setenv("AP_REVIEWER", "Manager Mia")
    for name in [m for m in sys.modules if m.startswith(("ap_coder.webapp", "ap_coder.dashboard"))]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()
    st.cache_data.clear()
    return path


def _ok(at):
    assert not at.exception, [e.value for e in at.exception]
    return at


def _html(at) -> str:
    return " ".join(str(h.proto.body) for h in at.get("html"))


def _automation():
    return AppTest.from_string(
        "from ap_coder.webapp.automation_settings import automation_tab\n"
        "from ap_coder.webapp.common import get_store, show_toast\n"
        "show_toast()\nautomation_tab(get_store())",
        default_timeout=90,
    )


def test_automation_tab_turns_touchless_on_with_a_confirmation(db):
    store = Store(db)
    _ready(store, name="Price $5 *Wholesale*")
    _ready(store, 12, key="id:V2", name="Chinook")
    at = _ok(_automation().run())
    html = _html(at)
    assert "Off: every invoice is reviewed by a person" in html
    assert "at least 20" in html and "the last 10, with no correction at all" in html and "5% of touchless" in html
    assert "Bank account changed" in html and "Possible duplicate" in html and "5,000.00 CAD" in html
    assert "Credit note" in html and "Not in the vendor master" in html and "Over the approval limit" in html
    assert at.button(key="touchless_go").disabled  # needs "I understand"
    _ok(at.checkbox(key="touchless_sure").check().run())
    _ok(at.button(key="touchless_go").click().run())
    assert store.touchless_enabled()
    assert store.events(actions=["touchless_on"])[0]["actor"] == "Manager Mia"
    assert [t.value for t in at.toast] == ["Touchless processing is on: 1 vendor touchless now."]
    assert "On: a vendor that meets the bar" in _html(at)
    tiles = _html(at)
    assert "Vendors touchless" in tiles and "Touchless, last 30 days" in tiles


def test_automation_tab_saves_the_limit(db):
    store = Store(db)
    at = _ok(_automation().run())
    at.number_input[0].set_value(2500.0)
    _ok(next(b for b in at.button if b.label == "Save the limit").click().run())
    assert store.touchless_limit() == 2500.0
    assert store.events(actions=["settings_changed"])[0]["actor"] == "Manager Mia"


def test_learning_tab_has_no_per_vendor_turn_on_button(db):
    store = Store(db)
    _ready(store)
    store.set_touchless(True, "Mia")
    at = _ok(AppTest.from_string("from ap_coder.webapp.learning import page_learning\npage_learning()",
                                 default_timeout=90).run())  # fmt: skip
    html = _html(at)
    assert "Touchless" in html and "Touchless since" in html
    assert not any("Turn on autonomy" in str(b.proto) for b in at.get("button"))
    assert any((b.key or "").startswith("sup_hold_") for b in at.button)


def test_approving_says_what_the_vendor_learned(db):
    store = Store(db)
    load_sample_setup(store)
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", gt, {"requires_review": False,
                                                                         "adjusted_confidence": 0.95})  # fmt: skip
    at = AppTest.from_file(str(ROOT / "ap_coder" / "dashboard.py"), default_timeout=90)
    at.session_state["open_invoice"] = invoice_id
    _ok(at.run())
    _ok(at.button(key=f"inv{invoice_id}_approve").click().run())
    toasts = [t.value for t in at.toast]
    assert any(
        t.startswith("Approved ")
        and "Every header field was read right." in t
        and ": 1 invoice reviewed, about " in t
        and "more clean invoices to go touchless." in t
        and "coded as suggested." in t
        for t in toasts
    ), toasts
