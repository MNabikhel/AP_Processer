"""The web dashboard used as a clerk would: stale pages, double actions, odd input, and the guards in front of it."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.config import Settings
from ap_coder.demo import load_demo
from ap_coder.store import REVIEW, Store
from ap_coder.web.app import create_app

PAGE = {"X-AP-Coder": "1"}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture(autouse=True)
def reviewer_name(monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Jordan Lee")


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


def test_approving_in_a_second_tab_says_plainly_it_was_approved_meanwhile(client, store):
    """The same invoice open in two tabs: approved in the first, the second's Approve says what happened."""
    from tests.test_web_invoice import clean_invoice, flash_text, form_of

    load_demo(store, Settings())
    inv_id = clean_invoice(client, store)
    data = form_of(store, inv_id)
    assert client.post(f"/review/{inv_id}/approve", data=data, headers=PAGE).json()["ok"]
    answer = client.post(f"/review/{inv_id}/approve", data=data, headers=PAGE).json()
    assert answer["redirect"].startswith(f"/review/{inv_id}?")
    text = flash_text(client, answer)
    assert "changed meanwhile, in another tab or by someone else. It is approved by Jordan Lee." in text
    assert "is already approved)" not in text


def test_acting_on_an_invoice_deleted_meanwhile_goes_back_to_the_queue(client, store):
    """Parked, then deleted in another tab: "Back to the queue" on the stale page leads to the queue with a plain
    message, not to a "Not found" page."""
    from tests.test_web_invoice import flash_text

    load_demo(store, Settings())
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    store.park_invoice(inv_id, "Jordan Lee", "waiting for the buyer")
    store.delete_invoice(inv_id, actor="Jordan Lee")
    answer = client.post(f"/review/{inv_id}/resume", headers=PAGE).json()
    assert answer["redirect"].startswith("/review?")
    assert "this invoice was deleted meanwhile" in flash_text(client, answer)


@pytest.mark.parametrize("typed", ["1e309", "-1e309", "abc"])
@pytest.mark.parametrize("field", ["line_amount", "line_quantity", "line_unit_price", "tax_rate_pct", "tax_amount"])
def test_a_number_cell_that_is_not_a_finite_number_never_breaks_the_review_screen(client, store, field, typed):
    """A cell posted as "1e309" (infinite once read) or as text: checking, splitting and approving answered 500
    ("That didn't work"). Such a cell counts as empty and the checks say what is off."""
    from tests.test_web_invoice import form_of

    load_demo(store, Settings())
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    data = form_of(store, inv_id)
    data[field] = [typed] * len(data[field])
    check = client.post(f"/review/{inv_id}/check", data=data, headers=PAGE)
    assert check.status_code == 200, check.text
    split = {**data, "split_line": "1", "part_gl": ["6000", "6010"], "part_pct": ["50", "50"]}
    assert client.post(f"/review/{inv_id}/split", data=split, headers=PAGE).status_code < 500
    assert client.post(f"/review/{inv_id}/approve", data=data, headers=PAGE).status_code < 500


def test_clearing_your_name_in_settings_goes_back_to_the_default_name(client, store, monkeypatch):
    """A name saved by mistake on a shared computer could not be removed: clearing the box said "Nothing changed."
    and the name stayed on every approval."""
    from ap_coder import paths
    from ap_coder.web import core

    monkeypatch.delenv("AP_REVIEWER")
    form = {"threshold": "80", "constrain": "1"}
    assert client.post("/settings/review", data={**form, "reviewer_name": "Sam Mistake"}, headers=PAGE).json()["ok"]
    assert core.reviewer() == "Sam Mistake"
    answer = client.post("/settings/review", data={**form, "reviewer_name": ""}, headers=PAGE).json()
    assert core.reviewer() == core.login() and not paths.read_user_settings().get("reviewer")
    assert "Nothing changed" not in client.get(answer["redirect"]).text


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


def test_every_page_starts_with_a_skip_link_past_the_rail(client):
    """Keyboard only: 18 rail links came before the first invoice of the queue on every page; the first Tab now
    offers to skip to the page itself."""
    for path in ("/review", "/process", "/settings/data", "/help"):
        text = client.get(path).text
        assert 'class="skip" href="#main"' in text, path
        assert text.index('href="#main"') < text.index('class="brand"'), path
        assert '<main class="main" id="main" tabindex="-1">' in text, path


def test_pages_are_never_kept_by_the_browser_so_back_shows_where_things_stand(client, store):
    """Parked, then Back: the browser showed the review screen it had kept, as if nothing had happened (its first
    check then said "no longer in the review queue: reload the page"). Pages are drawn afresh each time."""
    for path in ("/review", "/settings/data", "/no-such-page"):
        response = client.get(path, headers={"Accept": "text/html"})
        assert "no-store" in response.headers.get("cache-control", ""), path


@pytest.mark.parametrize(
    "folder", ["é" * 300, "x" * 5000, "C:\\Users\\me\\" + "a" * 400], ids=["accents", "long", "windows"]
)
def test_a_backup_folder_name_the_computer_refuses_is_said_plainly(client, store, folder):
    """A pasted name too long for the computer answered 500 ("That didn't work") instead of saying so."""
    response = client.post("/settings/data/backup-copy", data={"folder": folder}, headers=PAGE)
    assert response.status_code == 400 and "does not exist" in response.json()["error"]
    assert store.get_setting("backup_copy_dir") == ""


@pytest.mark.parametrize(
    "path",
    [
        "/settings/data/backups/..%2Fap_coder.db",
        "/settings/data/backups/ap_coder.db",
        "/settings/data/backups/..%2F..%2F..%2Fetc%2Fpasswd",
        "/static/..%2F..%2Fstore.py",
        "/static/%2e%2e/%2e%2e/app.py",
        "/fonts/..%2Fweb%2Fapp.py",
        "/assets/..%2F..%2Fpyproject.toml",
        "/review/1/page/..%2F..%2Fap_coder.db",
        "/exports/batches/1/download?fmt=..%2F..%2Fx",
        "/accounts/..%2F..%2Fx.csv",
    ],
)
def test_no_file_route_serves_anything_outside_its_own_files(client, store, path):
    """Backups, page pictures, exports and static files: a name with ../ in it is not found, never another file."""
    store.backup_now("manual")
    response = client.get(path)
    assert response.status_code in (400, 404, 422), (path, response.status_code)
    assert b"SQLite format" not in response.content and b"root:" not in response.content
