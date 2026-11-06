"""Server mode in the dashboard (D-97, D-98): logging in and out, sessions, the forced password change, the header,
identity from the account, the 403 for a role that may not, and the second approval by two accounts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import PENDING, Store, load_sample_setup
from ap_coder.web.app import create_app
from ap_coder.web.identity import SESSION_COOKIE
from tests.webclient import PAGE, add_clean, enrol, known_vendors, sign_in, web_settings

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
    enrol(store, uid)  # its authenticator app set up (D-146)
    return uid


def log_in(client: TestClient, username: str, password: str = GOOD, next: str = "/review"):  # noqa: A002
    """Both steps (D-146): the password, then the authenticator's code."""
    return sign_in(client, username, password, next)


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
    assert answer.status_code == 200 and answer.json()["redirect"] == "/login/code?next=%2Freview"  # (D-146)
    assert store.session_user(planted) is None  # ended at the password already
    for cookie in (answer.headers["set-cookie"], log_in(client, "ann").headers["set-cookie"]):  # half way, then in
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
    assert answer.json()["redirect"].startswith("/login/new-password?next=%2Fsearch")  # no session yet (D-146)
    assert client.get("/review").headers["location"].startswith("/login")
    assert client.post("/review/bulk-approve", data={"ids": "1"}, headers=PAGE).status_code == 401
    assert client.get("/login/new-password").status_code == 200
    mismatch = client.post("/login/new-password", data={"current": GOOD, "new": "a new long password",
                                                         "again": "another one"}, headers=PAGE)  # fmt: skip
    assert "not the same" in mismatch.json()["error"]
    form = {"current": GOOD, "new": "a new long password", "again": "a new long password", "next": "/search"}
    done = client.post("/login/new-password", data=form, headers=PAGE)
    assert done.json()["redirect"].startswith("/login/authenticator?next=%2Fsearch")  # then the app is set up
    assert not store.user(uid)["must_change"] and client.get("/review").status_code == 303


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


def test_a_tab_left_open_logs_out_after_30_idle_minutes_however_often_it_polls(store, monkeypatch):
    from ap_coder.core.store import users

    clock = [users._clock()]
    monkeypatch.setattr(users, "_clock", lambda: clock[0])
    account(store, "boss", "admin")
    iid = add_clean(store)
    client = server_client(store)
    log_in(client, "boss")
    clock[0] += 20 * 60
    assert client.get(f"/review/{iid}").status_code == 200  # a page looked at: the idle time starts again
    polls = (f"/review/{iid}/reader", "/process/status", "/jobs/process", "/settings/reading/self-test")
    for _ in range(3):  # then nothing but the tab's automatic polls (OvisOCR2 reading, a job, the self-test)
        clock[0] += 10 * 60 - 1
        for poll in polls:
            assert client.get(poll).status_code != 303, poll  # still logged in: they are answered
    clock[0] += 4
    assert client.get(f"/review/{iid}").status_code == 303  # 30 minutes without a click: logged out


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


def test_on_the_server_the_data_workbook_is_made_in_the_background_and_the_last_one_downloaded(store):
    """D-156: a year of invoices takes minutes to write as a workbook, past any page's wait: on the company server a
    Clerk makes it in the background (one at a time, for everyone, its progress on the page), and the download is the
    one made last, the same as one made on the spot. Read-only downloads it, and can't start it (it changes nothing)."""
    from ap_coder.erp import spend
    from tests import reports_reference as ref

    account(store, "ann", "clerk")
    account(store, "rita", "readonly")
    add_clean(store)
    app = create_app(web_settings(), store, start_reader=False, server=True)
    clerk, reader = (TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
                     for _ in range(2))  # fmt: skip
    log_in(clerk, "ann")
    log_in(reader, "rita")
    missing = reader.get("/spend/data.xlsx", headers={"Accept": "text/html"})
    assert missing.status_code == 404 and "a Clerk makes it on Spend with Make the Excel workbook" in missing.text
    assert "Make the Excel workbook" in clerk.get("/spend").text
    assert (
        "a Clerk makes it here" in reader.get("/spend").text and 'action="/spend/data"' not in reader.get("/spend").text
    )
    assert reader.post("/spend/data", headers=PAGE).status_code == 403
    answer = clerk.post("/spend/data", headers=PAGE).json()
    assert answer == {"ok": True, "job": "spend-data", "status": "/jobs/spend-data"}
    assert app.state.jobs.get("spend-data").wait(60)
    assert reader.get(answer["status"]).json()["state"] == "done"  # what Read-only may download, it sees made
    got = reader.get("/spend/data.xlsx")
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    assert ref.workbook_contents(got.content) == ref.workbook_contents(spend.workbook(store, store.reference_data()))
    assert (
        "All invoice data (Excel), made " in reader.get("/spend").text and "Make it again" in clerk.get("/spend").text
    )
    assert not list((store.path.parent / ".cache" / "downloads").glob("*.part"))  # put in its place once whole


def _clerk_making_workbooks(store: Store) -> tuple[TestClient, object]:
    account(store, "ann", "clerk")
    add_clean(store)
    app = create_app(web_settings(), store, start_reader=False, server=True)
    clerk = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    log_in(clerk, "ann")
    return clerk, app


def _make_workbook(clerk: TestClient, app) -> dict:
    assert clerk.post("/spend/data", headers=PAGE).status_code == 200
    assert app.state.jobs.get("spend-data").wait(60)
    return clerk.get("/jobs/spend-data").json()


def test_on_the_server_a_data_workbook_that_could_not_be_made_says_why_on_spend(store, monkeypatch):
    """The job's error was never shown: the page offered "Make the Excel workbook" again as if nothing had happened."""
    from ap_coder.erp import spend

    clerk, app = _clerk_making_workbooks(store)

    def full(*args, **kwargs):
        raise OSError("No space left on the disk")

    monkeypatch.setattr(spend, "write_workbook", full)
    assert _make_workbook(clerk, app)["state"] == "error"
    page = clerk.get("/spend").text
    assert "The workbook could not be made: No space left on the disk" in page and "Make the Excel workbook" in page
    monkeypatch.undo()
    assert _make_workbook(clerk, app)["state"] == "done"
    assert "could not be made" not in clerk.get("/spend").text


def test_on_the_server_a_data_workbook_being_downloaded_does_not_stop_the_next_one(store, monkeypatch):
    """On Windows a file open for a download can be neither replaced nor deleted: the next workbook failed while one
    was being downloaded. Each is made under a name of its own, the newest downloaded, the older ones removed once
    they can be."""
    import os
    from pathlib import Path

    from ap_coder.web.pages import spend as spend_page

    clerk, app = _clerk_making_workbooks(store)
    assert _make_workbook(clerk, app)["state"] == "done"
    first = spend_page.workbook_path(store)
    first_bytes = first.read_bytes()
    replace, unlink = os.replace, Path.unlink

    def windows_replace(src, dst):
        if Path(dst) == first:
            raise PermissionError(13, "The process cannot access the file because it is being used", str(dst))
        return replace(src, dst)

    def windows_unlink(path, missing_ok=False):
        if path == first:
            raise PermissionError(13, "The process cannot access the file because it is being used", str(path))
        return unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(os, "replace", windows_replace)
    monkeypatch.setattr(Path, "unlink", windows_unlink)
    add_clean(store, invoice_number="INV-NEXT-1")
    assert _make_workbook(clerk, app)["state"] == "done"
    newest = spend_page.workbook_path(store)
    assert newest != first and first.exists()  # (still being downloaded: removed next time)
    got = clerk.get("/spend/data.xlsx")
    assert got.status_code == 200 and got.content == newest.read_bytes() != first_bytes
    monkeypatch.undo()
    assert _make_workbook(clerk, app)["state"] == "done"
    assert list(first.parent.glob("*.xlsx")) == [spend_page.workbook_path(store)]


def test_on_the_server_a_restore_removes_the_data_workbook_of_the_replaced_invoices(store):
    from ap_coder.web.pages import spend as spend_page

    clerk, app = _clerk_making_workbooks(store)
    backup = store.backup_now("manual")
    assert _make_workbook(clerk, app)["state"] == "done"
    assert spend_page.workbook_path(store) is not None
    store.restore_state(backup, keep_accounts=True)
    assert spend_page.workbook_path(store) is None
    log_in(clerk, "ann")  # (a restore logs everyone out)
    assert clerk.get("/spend/data.xlsx", headers={"Accept": "text/html"}).status_code == 404


def test_on_the_server_a_page_picture_is_drawn_once_for_all_the_web_workers(store, monkeypatch):
    """D-160: each worker drew the pictures it was asked for into its own memory. On the server they are put in the
    data folder's .cache/pages, and another worker reads them back; the browser still keeps none (B-1070)."""
    from ap_coder.web import document

    account(store, "ann", "clerk")
    invoice_id = add_clean(store)
    one, two = server_client(store), server_client(store)  # two web workers
    in_memory = document._png_cached.cache_info().currsize
    for client in (one, two):
        log_in(client, "ann")
    first = one.get(f"/review/{invoice_id}/page/1.png?scale=2")
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    (kept,) = (store.path.parent / ".cache" / "pages").glob("*/*.png")
    assert kept.read_bytes() == first.content
    monkeypatch.setattr(document, "_draw_png", lambda *a: pytest.fail("drawn again"))
    assert two.get(f"/review/{invoice_id}/page/1.png?scale=2").content == first.content
    assert document._png_cached.cache_info().currsize == in_memory  # none kept in a worker's memory


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
    assert log_in(newcomer, "ann.lee", password).json()["redirect"].startswith("/login/new-password")
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


def test_the_progress_of_a_download_or_a_bulk_approval_keeps_no_session_going(store, monkeypatch):
    """B-1927: the progress of a download the server's background process makes (D-169) is looked at by its link
    every half second, up to every 1.5 s, for as long as it waits, and each look counted as use: a tab waiting on one
    (the background process stopped: it waits for good) stayed logged in past the idle limit, as B-1580's polls did.
    A bulk approval's page looks the same way (D-185). Neither renews the session."""
    import json

    from ap_coder.coding import bulk
    from ap_coder.core.store import users
    from ap_coder.web.pages import downloads

    clock = [users._clock()]
    monkeypatch.setattr(users, "_clock", lambda: clock[0])
    monkeypatch.setattr(downloads, "_left", lambda request, job: None)  # (waiting: no process makes it)
    uid = account(store, "boss", "admin")
    client = server_client(store)
    log_in(client, "boss")
    made, _ = store.start_download(f"user:{uid}", "/exports/duplicates.csv", {}, "d.csv", 3, downloads.FORMAT)
    person = {"name": "Boss", "login": f"user:{uid}", "server": True, "as": {}}
    approving, _ = store.start_bulk_job(json.dumps(["", f"user:{uid}"]), person, [], bulk.JOB_FORMAT)
    polls = (f"/downloads/{made}", f"/review/bulk/status?job={approving}")
    for _ in range(3):  # nothing but the tab's automatic looks
        clock[0] += 10 * 60 - 1
        for poll in polls:
            assert client.get(poll).status_code == 200, poll  # still logged in: they are answered
    clock[0] += 4
    assert client.get("/review").status_code == 303  # 30 minutes without a click: logged out
