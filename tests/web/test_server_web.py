"""Server mode in the dashboard (D-97, D-98): logging in and out, sessions, the forced password change, the header,
identity from the account, the 403 for a role that may not, and the second approval by two accounts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import PENDING, Store, load_sample_setup
from ap_coder.web.app import create_app
from ap_coder.web.identity import SESSION_COOKIE
from tests.webclient import PAGE, add_clean, known_vendors, web_settings

GOOD = "correct horse battery"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


def server_client(store: Store, base: str = "https://localhost", client: tuple[str, int] = ("10.0.0.5", 50000)):
    app = create_app(web_settings(), store, start_reader=False, server=True)
    return TestClient(app, base_url=base, client=client, follow_redirects=False)


def account(store: Store, username: str, role: str, name: str = "", password: str = GOOD) -> int:
    """An account that has chosen its own password already."""
    uid = store.add_user(username, password, role=role, display_name=name or username.title())
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    return uid


def log_in(client: TestClient, username: str, password: str = GOOD, next: str = "/review"):  # noqa: A002
    return client.post("/login", data={"username": username, "password": password, "next": next}, headers=PAGE)


# --- Nothing without a login ---------------------------------------------------------------------------------------


def test_without_a_session_a_page_sends_to_the_login_and_an_action_is_refused(store):
    client = server_client(store)
    answer = client.get("/review?show=all")
    assert answer.status_code == 303 and answer.headers["location"] == "/login?next=%2Freview%3Fshow%3Dall"
    refused = client.post("/review/bulk-approve", data={"ids": "1"}, headers=PAGE)
    assert refused.status_code == 401 and refused.json()["redirect"] == "/login"
    assert client.get("/login").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/who").headers["location"] == "/login"  # no names given in server mode
    assert client.post("/who", data={"name": "Ann"}, headers=PAGE).status_code in (401, 403)


def test_the_servers_own_browser_gets_no_identity_either(store):
    """In server mode no address is "this computer": the console logs in like everyone else."""
    client = server_client(store, client=("127.0.0.1", 50000))
    assert client.get("/review").status_code == 303
    assert client.get("/settings").status_code == 303


def test_the_login_page_says_how_to_add_the_first_admin_until_there_is_one(store):
    client = server_client(store)
    assert "users add" in client.get("/login").text
    account(store, "boss", "admin")
    assert "users add" not in client.get("/login").text


# --- Logging in ----------------------------------------------------------------------------------------------------


def test_a_wrong_password_and_an_unknown_name_get_the_same_refusal(store):
    account(store, "ann", "clerk")
    client = server_client(store)
    wrong, unknown = log_in(client, "ann", "not the password"), log_in(client, "nobody", "not the password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"] == unknown.json()["error"] and "don't match" in wrong.json()["error"]
    assert SESSION_COOKIE not in client.cookies


def test_login_sets_a_strict_http_only_cookie_and_a_new_token_each_time(store):
    account(store, "ann", "clerk", "Ann Lee")
    client = server_client(store)
    planted = store.start_session(store.find_user("ann")["id"])  # a token someone else knows (fixation)
    answer = client.post("/login", data={"username": "ann", "password": GOOD, "next": "/review"},
                         headers={**PAGE, "Cookie": f"{SESSION_COOKIE}={planted}"})  # fmt: skip
    assert answer.status_code == 200 and answer.json()["redirect"] == "/review"
    cookie = answer.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict") and "Secure" in cookie
    token = client.cookies.get(SESSION_COOKIE)
    assert token and token != planted and store.session_user(planted) is None
    page = client.get("/review")
    assert page.status_code == 200
    assert "Ann Lee" in page.text and "Clerk" in page.text and 'action="/logout"' in page.text


def test_over_https_the_cookie_is_secure_and_over_insecure_http_not(store):
    account(store, "ann", "clerk")
    client = server_client(store, base="https://localhost")
    assert "Secure" in log_in(client, "ann").headers["set-cookie"]
    app = create_app(web_settings(), store, start_reader=False, server=True, insecure_http=True)
    plain = TestClient(app, base_url="http://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    assert "Secure" not in log_in(plain, "ann").headers["set-cookie"]


def test_a_new_account_must_choose_its_own_password_before_anything_else(store):
    uid = store.add_user("ann", GOOD, role="clerk", display_name="Ann Lee")  # must_change, as made
    client = server_client(store)
    answer = log_in(client, "ann", next="/search")
    assert answer.json()["redirect"].startswith("/account/password?next=%2Fsearch")
    assert client.get("/review").headers["location"].startswith("/account/password")
    assert client.post("/review/bulk-approve", data={"ids": "1"}, headers=PAGE).status_code == 403
    assert client.get("/account/password").status_code == 200
    mismatch = client.post("/account/password", data={"current": GOOD, "new": "a new long password",
                                                      "again": "another one"}, headers=PAGE)  # fmt: skip
    assert "not the same" in mismatch.json()["error"]
    form = {"current": GOOD, "new": "a new long password", "again": "a new long password", "next": "/search"}
    done = client.post("/account/password", data=form, headers=PAGE)
    assert done.json()["redirect"].startswith("/search")
    assert not store.user(uid)["must_change"] and client.get("/review").status_code == 200


def test_logout_ends_the_session(store):
    account(store, "ann", "clerk")
    client = server_client(store)
    log_in(client, "ann")
    token = client.cookies.get(SESSION_COOKIE)
    assert client.get("/logout").status_code == 200  # a page asking first
    answer = client.post("/logout", headers=PAGE)
    assert answer.json()["redirect"].startswith("/login")
    assert store.session_user(token) is None
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/review").status_code == 303
    assert store.events(actions=["logout"])[0]["detail"]["username"] == "ann"


def test_a_password_change_logs_the_other_browsers_out(store):
    account(store, "ann", "clerk")
    here, there = server_client(store), server_client(store)
    log_in(here, "ann")
    log_in(there, "ann")
    here.post("/account/password", data={"current": GOOD, "new": "a new long password",
                                         "again": "a new long password"}, headers=PAGE)  # fmt: skip
    assert here.get("/review").status_code == 200
    assert there.get("/review").status_code == 303


def test_a_restart_keeps_everyone_logged_in(store):
    account(store, "ann", "clerk")
    client = server_client(store)
    log_in(client, "ann")
    again = server_client(store)  # a new app on the same database: AP Coder started again
    again.cookies.set(SESSION_COOKIE, client.cookies.get(SESSION_COOKIE))
    assert again.get("/review").status_code == 200


def test_the_events_name_the_account_and_its_address(store):
    uid = account(store, "ann", "clerk", "Ann Lee")
    client = server_client(store)
    log_in(client, "ann")
    known_vendors(store)
    invoice_id = add_clean(store)
    assert client.post("/review/bulk-approve", data={"ids": invoice_id}, headers=PAGE).status_code == 200
    approved = store.events(invoice_id, ["approved"])[0]
    assert approved["actor"] == "Ann Lee"
    assert approved["detail"]["login"] == f"user:{uid}" and approved["detail"]["user_id"] == uid
    assert approved["detail"]["address"] == "10.0.0.5"
    login = store.events(actions=["login"])[0]
    assert login["actor"] == "Ann Lee" and login["detail"]["address"] == "10.0.0.5"


# --- Roles in the pages --------------------------------------------------------------------------------------------


def test_a_role_that_may_not_gets_a_403_page_or_a_json_refusal(store):
    account(store, "ann", "clerk")
    client = server_client(store)
    log_in(client, "ann")
    page = client.get("/settings", headers={"Accept": "text/html"})
    assert page.status_code == 403 and "Not allowed" in page.text and "Your role (Clerk)" in page.text
    action = client.post("/settings/review", data={}, headers=PAGE)
    assert action.status_code == 403 and "Your role (Clerk) can't do this" in action.json()["error"]


def test_the_menu_shows_only_what_the_role_may_open(store):
    account(store, "reader", "readonly")
    account(store, "boss", "admin")
    reader, boss = server_client(store), server_client(store)
    log_in(reader, "reader")
    log_in(boss, "boss")
    shown, everything = reader.get("/review").text, boss.get("/review").text
    for path in ("/settings", "/process", "/accounts", "/learning"):
        link = f'href="{path}" class="nav-link'
        assert link not in shown and link in everything
    assert 'href="/admin/users"' not in shown and 'href="/admin/users"' in everything
    for path in ("/review", "/search", "/spend", "/activity", "/help"):
        assert f'href="{path}" class="nav-link' in shown


def test_an_admin_adds_a_person_whose_temporary_password_is_shown_once(store):
    account(store, "boss", "admin", "Pat Tester")
    client = server_client(store)
    log_in(client, "boss")
    answer = client.post("/admin/users/add", data={"username": "Ann.Lee", "name": "Ann Lee", "role": "approver"},
                         headers=PAGE)  # fmt: skip
    shown = client.get(answer.json()["redirect"]).text
    import re

    password = re.search(r'<code class="big">([^<]+)</code>', shown).group(1)
    assert store.find_user("ann.lee")["role"] == "approver"
    assert password not in client.get(answer.json()["redirect"]).text  # once
    newcomer = server_client(store)
    assert log_in(newcomer, "ann.lee", password).json()["redirect"].startswith("/account/password")
    assert store.events(actions=["user_added"])[0]["actor"] == "Pat Tester"


def test_the_last_admin_cannot_be_demoted_or_turned_off_from_the_page(store):
    uid = account(store, "boss", "admin")
    client = server_client(store)
    log_in(client, "boss")
    refused = client.post(f"/admin/users/{uid}/role", data={"role": "clerk"}, headers=PAGE)
    assert refused.status_code == 400 and "at least one active Admin" in refused.json()["error"]
    refused = client.post(f"/admin/users/{uid}/deactivate", headers=PAGE)
    assert "at least one active Admin" in refused.json()["error"]
    assert store.user(uid)["role"] == "admin" and store.user(uid)["active"]


def test_an_admin_resets_a_password_and_turns_an_account_off(store):
    account(store, "boss", "admin")
    ann = account(store, "ann", "clerk")
    ann_client, boss = server_client(store), server_client(store)
    log_in(ann_client, "ann")
    log_in(boss, "boss")
    boss.post(f"/admin/users/{ann}/reset", headers=PAGE)
    assert ann_client.get("/review").status_code == 303  # logged out by the reset
    assert store.user(ann)["must_change"]
    boss.post(f"/admin/users/{ann}/deactivate", headers=PAGE)
    assert not store.user(ann)["active"]
    boss.post(f"/admin/users/{ann}/role", data={"role": "approver"}, headers=PAGE)
    assert store.user(ann)["role"] == "approver"
    assert {"password_reset", "user_deactivated", "role_changed"} <= {e["action"] for e in store.events()}


# --- The second approval needs two accounts ------------------------------------------------------------------------


def test_the_second_approval_needs_a_second_account(store):
    store.set_setting("approval_limit", "5000.00")
    known_vendors(store)
    invoice_id = add_clean(store)  # 18,017.85 CAD: over the limit
    account(store, "ann", "approver", "Ann Lee")
    account(store, "ann2", "approver", "Ann Lee")  # another person with the same name
    first, second = server_client(store), server_client(store)
    log_in(first, "ann")
    log_in(second, "ann2")
    first.post("/review/bulk-approve", data={"ids": invoice_id}, headers=PAGE)
    assert store.get_invoice(invoice_id)["status"] == PENDING
    shown = store.events(invoice_id, ["approved"])[0]["id"]
    refused = first.post(f"/review/{invoice_id}/final-approve", data={"approval": shown}, headers=PAGE)
    assert refused.status_code == 409 and "another account" in refused.json()["error"]  # (B-1113)
    assert store.get_invoice(invoice_id)["status"] == PENDING
    second.post(f"/review/{invoice_id}/final-approve", data={"approval": shown}, headers=PAGE)
    assert store.get_invoice(invoice_id)["status"] == "approved"


@pytest.mark.parametrize("server", [False, True])
def test_changing_the_currency_on_the_review_screen_does_not_skip_the_second_approver(store, server, monkeypatch):
    """B-1482 (D-132), on a laptop and on the company server: an invoice read as USD and changed to CAD on the review
    screen still needs a second approver above the limit in USD, and the approve bar says why before approving."""
    from tests.conftest import next_second
    from tests.web.test_web_invoice import form_of

    store.set_setting("approval_limit", "20000.00")
    store.set_setting("fx_rates", "USD=1.37")
    next_second()  # the invoice comes in after the settings (B-970)
    known_vendors(store)
    invoice_id = add_clean(store, currency="USD")  # 18,017.85 USD: about 24,684 CAD, 18,017.85 as CAD
    if server:
        account(store, "ann", "approver", "Ann Lee")
        client = server_client(store)
        log_in(client, "ann")
    else:
        monkeypatch.delenv("AP_SERVER_MODE", raising=False)
        monkeypatch.setenv("AP_REVIEWER", "Ann Lee")
        client = TestClient(create_app(web_settings(), store, start_reader=False), base_url="http://localhost")

    def bar(data):
        return client.post(f"/review/{invoice_id}/check", data=data, headers=PAGE).json()["sections"]["bar"]

    assert "currency was changed" not in bar(form_of(store, invoice_id))  # as read: the bar as before
    changed = {**form_of(store, invoice_id, currency="CAD"), "why_done": "1"}
    assert "The currency was changed from USD" in bar(changed) and "a second approver is needed" in bar(changed)
    assert client.post(f"/review/{invoice_id}/approve", data=changed, headers=PAGE).json()["ok"]
    inv = store.get_invoice(invoice_id)
    assert inv["status"] == PENDING and inv["currency"] == "CAD"


def test_a_clerk_cannot_give_the_second_approval(store):
    store.set_setting("approval_limit", "5000.00")
    known_vendors(store)
    invoice_id = add_clean(store)  # 18,017.85 CAD: over the limit
    account(store, "ann", "clerk")
    account(store, "bob", "clerk")
    first, second = server_client(store), server_client(store)
    log_in(first, "ann")
    log_in(second, "bob")
    first.post("/review/bulk-approve", data={"ids": invoice_id}, headers=PAGE)
    assert second.post(f"/review/{invoice_id}/final-approve", headers=PAGE).status_code == 403
    assert store.get_invoice(invoice_id)["status"] == PENDING


def test_only_the_approver_or_an_admin_undoes_an_approval(store):
    known_vendors(store)
    invoice_id = add_clean(store)
    account(store, "ann", "clerk")
    account(store, "bob", "clerk")
    ann, bob = server_client(store), server_client(store)
    log_in(ann, "ann")
    log_in(bob, "bob")
    ann.post("/review/bulk-approve", data={"ids": invoice_id}, headers=PAGE)
    refused = bob.post(f"/review/{invoice_id}/undo-approve", headers=PAGE)
    assert refused.status_code == 403 and "Only the person who gave this approval" in refused.json()["error"]
    assert ann.post(f"/review/{invoice_id}/undo-approve", headers=PAGE).json()["ok"]
    assert store.get_invoice(invoice_id)["status"] == "review"


# --- Laptop mode ---------------------------------------------------------------------------------------------------


def test_on_a_laptop_there_is_no_login_and_nothing_changes(store, monkeypatch):
    monkeypatch.delenv("AP_SERVER_MODE", raising=False)
    app = create_app(web_settings(), store, start_reader=False)
    client = TestClient(app, base_url="http://localhost", follow_redirects=False)
    assert app.state.server_mode is False
    page = client.get("/review")
    assert page.status_code == 200 and 'action="/logout"' not in page.text and 'href="/settings"' in page.text
    assert client.get("/login").headers["location"] == "/review"
    assert client.get("/admin/users").status_code == 404
    assert client.get("/settings").status_code == 200


def test_the_switch_comes_from_the_environment(store, monkeypatch):
    monkeypatch.setenv("AP_SERVER_MODE", "1")
    assert create_app(web_settings(), store, start_reader=False).state.server_mode is True


def test_the_review_screen_hides_the_buttons_a_role_cannot_use(store):
    known_vendors(store)
    invoice_id = add_clean(store)
    shown = {}
    for role in ("readonly", "clerk", "admin"):
        account(store, f"the.{role}", role)
        client = server_client(store)
        log_in(client, f"the.{role}")
        shown[role] = client.get(f"/review/{invoice_id}").text
    assert "data-approve " not in shown["readonly"] and "data-park" not in shown["readonly"]
    assert "data-approve " in shown["clerk"] and f"/review/{invoice_id}/delete" not in shown["clerk"]
    assert f"/review/{invoice_id}/delete" in shown["admin"]
