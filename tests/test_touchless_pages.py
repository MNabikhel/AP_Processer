"""Touchless processing on the dashboard (ap_coder/web): Settings → Automation, the Learning page as a training view,
and what the review screen says an approval taught."""

import html
import re

import pytest

from ap_coder.store import Store, load_sample_setup

from .test_touchless import _ready
from .test_web_invoice import form_of
from .webclient import add_clean, client_for, follow, post


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Lead Lee")
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture
def client(store):
    return client_for(store)


def _switch(page: str) -> str:
    """The form that turns touchless processing on or off."""
    return page.split('action="/settings/automation/touchless"', 1)[1].split("</form>", 1)[0]


def test_automation_tab_turns_touchless_on_with_a_confirmation(client, store):
    _ready(store, name="Price $5 *Wholesale*")
    _ready(store, 12, key="id:V2", name="Chinook")
    page = html.unescape(client.get("/settings/automation").text)
    assert "Off: every invoice is reviewed by a person" in page
    assert "at least 20" in page and "the last 10, with no correction at all" in page and "5% of touchless" in page
    assert "Bank account changed" in page and "Possible duplicate" in page and "5,000.00 CAD" in page
    assert "Credit note" in page and "Not in the vendor master" in page and "Over the approval limit" in page
    assert re.search(r'<button[^>]*type="submit"[^>]*disabled', _switch(page))  # needs "I understand"
    response = post(client, "/settings/automation/touchless", {"turn": "on"})
    assert response.status_code == 400 and not store.touchless_enabled()
    page = follow(client, post(client, "/settings/automation/touchless", {"turn": "on", "sure": "1"}))
    assert store.touchless_enabled()
    assert store.events(actions=["touchless_on"])[0]["actor"] == "Lead Lee"
    assert "Touchless processing is on: 1 vendor touchless now." in page
    assert "On: a vendor that meets the bar" in page
    assert "Vendors touchless" in page and "Touchless, last 30 days" in page


def test_the_touchless_confirmation_is_asked_again_for_the_opposite_switch(client, store):
    """Found driving the dashboard: after turning touchless off, the menu showed "Turn on" with "I understand" still
    ticked from turning it off, so one more click approved invoices without a person with no fresh confirmation."""
    page = follow(client, post(client, "/settings/automation/touchless", {"turn": "on", "sure": "1"}))
    assert store.touchless_enabled()
    form = _switch(page)
    assert 'name="turn" value="off"' in form  # the form now turns it off...
    assert re.search(r'<input type="checkbox" name="sure"[^>]*>', form).group(0).count("checked") == 0  # unticked
    assert re.search(r'<button[^>]*type="submit"[^>]*disabled', form)  # ... and "Turn off" waits for its own tick
    response = post(client, "/settings/automation/touchless", {"turn": "off"})
    assert response.status_code == 400 and store.touchless_enabled()
    # The same "Turn on" sent again (a second click, an old tab) does not turn it off.
    again = follow(client, post(client, "/settings/automation/touchless", {"turn": "on", "sure": "1"}))
    assert store.touchless_enabled() and "already on" in again
    page = follow(client, post(client, "/settings/automation/touchless", {"turn": "off", "sure": "1"}))
    assert not store.touchless_enabled()
    form = _switch(page)
    assert 'name="turn" value="on"' in form and "checked" not in form
    assert re.search(r'<button[^>]*type="submit"[^>]*disabled', form)  # and "Turn on" again for its own


def test_automation_tab_saves_the_limit(client, store):
    page = follow(client, post(client, "/settings/automation/limit", {"limit": "2500"}))
    assert store.touchless_limit() == 2500.0 and "Saved: 2,500.00 CAD." in page
    assert store.events(actions=["settings_changed"])[0]["actor"] == "Lead Lee"


def test_learning_tab_has_no_per_vendor_turn_on_button(client, store):
    _ready(store)
    store.set_touchless(True, "Mia")
    page = client.get("/learning?tab=suppliers").text
    assert "Touchless" in page and "Touchless since" in page
    assert "Turn on autonomy" not in page and "/learning/suppliers/on" not in page
    assert "/learning/suppliers/hold" in page  # keeping a vendor supervised is the one per-vendor control


def test_approving_says_what_the_vendor_learned(client, store):
    load_sample_setup(store)
    invoice_id = add_clean(store)
    page = follow(client, post(client, f"/review/{invoice_id}/approve", form_of(store, invoice_id)))
    found = re.search(r"Approved [^<]*coded as suggested\.", html.unescape(page))
    assert found, page
    text = found.group(0)
    assert "Every header field was read right." in text and ": 1 invoice reviewed, about " in text
    assert "more clean invoices to go touchless." in text
