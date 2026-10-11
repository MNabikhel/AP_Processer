"""The web dashboard used as a clerk would: stale pages, double actions, odd input, and the guards in front of it."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.config import Settings
from ap_coder.store import Store
from ap_coder.web.app import create_app

PAGE = {"X-AP-Coder": "1"}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture
def client(store):
    s = Settings()
    return TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), store), base_url="http://localhost")


def test_the_welcome_pages_sample_accounts_never_go_into_an_imported_chart(client, store):
    """A welcome page left open while the clerk imported their own chart of accounts: its "Use the sample GL
    accounts" button must not add the sample codes to it, nor replace their coding policy and tax setup."""
    store.import_accounts("gl_accounts", [{"code": "1000", "d": "Our cash"}], "code", "d")
    store.set_setting("policy_notes", "Our own policy.")
    response = client.post("/review/sample-gl", headers=PAGE)
    assert response.status_code == 400 and "already have GL accounts" in response.json()["error"]
    assert [a["code"] for a in store.list_accounts("gl_accounts")] == ["1000"]
    assert store.get_setting("policy_notes") == "Our own policy."


@pytest.mark.parametrize("url", ["/\t/evil.example", "/\n/evil.example", "/\r\n/evil.example"])
def test_a_redirect_never_leaves_this_server_through_a_control_character(url):
    """Browsers drop tabs and line breaks from an address, so "/<tab>/evil.example" is "//evil.example" to them:
    another site."""
    from ap_coder.web import core

    assert core.local_url(url, "/x") == "/x"
    assert core.local_url("/review?tab=parked&q=a%20b") == "/review?tab=parked&q=a%20b"


@pytest.mark.parametrize("typed", ["dollars", "1.37 USD", "US$=1.37", "USD=1.37, EUR"])
def test_exchange_rates_that_cannot_be_read_are_refused_not_saved(client, store, typed):
    """A rate typed the wrong way round ("1.37 USD") used to be saved as if understood: USD invoices were then
    compared with the approval limit at 1 CAD per USD."""
    store.set_setting("fx_rates", "USD=1.37")
    data = {"approval_limit": "1000", "terms_days": "30", "fx_rates": typed}
    response = client.post("/settings/review/approval", data=data, headers=PAGE)
    assert response.status_code == 400 and "USD=1.37" in response.json()["error"]
    assert store.get_setting("fx_rates") == "USD=1.37" and store.approval_limit() == 0


def test_exchange_rates_written_as_the_page_shows_are_saved(client, store):
    data = {"approval_limit": "0", "terms_days": "30", "fx_rates": "USD=1.37, EUR 1,50; gbp: 1.85"}
    assert client.post("/settings/review/approval", data=data, headers=PAGE).json()["ok"]
    assert store.fx_rates() == {"CAD": 1.0, "USD": 1.37, "EUR": 1.5, "GBP": 1.85}


def test_the_activity_search_finds_what_the_log_shows_in_its_what_column(client, store):
    """The clerk types the word the log shows for an event ("Approved", "Rejected"): it is found."""
    from ap_coder.audit import ACTIONS

    store.log_event("settings_changed", actor="Jordan Lee", detail={"keys": ["approval_limit"]})
    label = ACTIONS["settings_changed"][2]
    text = client.get("/activity", params={"q": label.lower()}).text
    assert "1 of 1 event" in text and "No events match" not in text
