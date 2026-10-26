"""The server mode's security review (D-101): lockouts that a stranger can't use against a person, failed logins
that keep no typed text, restores that keep the accounts, guesses in parallel, an Admin's own reset, two session
cookies, /healthz behind a proxy, the Secure cookie and HSTS, caching, the password policy, a new session token at a
password change, and the bundle checked from a private copy."""

from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.audit import describe
from ap_coder.core.store import Store, load_sample_setup, users
from ap_coder.core.store.users import LOCKED, LoginRefused
from ap_coder.web.app import create_app
from ap_coder.web.identity import SESSION_COOKIE

from .webclient import PAGE, add_clean, web_settings

GOOD = "correct horse battery"
WRONG_PASSWORD = "wrong wrong wrong"


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 1_800_000_000.0

        def __call__(self) -> float:
            return self.now

    found = Clock()
    monkeypatch.setattr(users, "_clock", found)
    return found


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


def account(store: Store, username: str, role: str = "clerk", password: str = GOOD) -> int:
    uid = store.add_user(username, password, role=role, display_name=username.title())
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    return uid


def app_for(store: Store, **options):
    return create_app(web_settings(), store, start_reader=False, server=True, **options)


def client_of(app, ip: str = "10.0.0.5", base: str = "https://localhost") -> TestClient:
    return TestClient(app, base_url=base, client=(ip, 50000), follow_redirects=False)


def log_in(client: TestClient, username: str, password: str = GOOD):
    return client.post("/login", data={"username": username, "password": password, "next": "/review"}, headers=PAGE)


def fail(store: Store, username: str, ip: str, times: int = 1, **options) -> str:
    said = ""
    for _ in range(times):
        with pytest.raises(LoginRefused) as refused:
            store.authenticate(username, WRONG_PASSWORD, ip, **options)
        said = str(refused.value)
    return said


# --- 1. Lockout: by username and address, and a higher limit per address ---------------------------------------------


def test_a_stranger_elsewhere_cannot_lock_a_person_out(store, clock):
    account(store, "boss", "admin")
    assert fail(store, "BOSS", "10.0.0.66", 5) == LOCKED  # paused for boss from that address only
    with pytest.raises(LoginRefused, match="paused"):
        store.authenticate("boss", GOOD, "10.0.0.66")
    assert store.authenticate("boss", GOOD, "10.0.0.20")["username"] == "boss"  # the real person, elsewhere


def test_one_address_is_paused_after_30_failures_across_usernames_not_after_5(store, clock):
    account(store, "boss", "admin")
    account(store, "cleo")
    for n in range(5):  # a shared NAT, RDS or proxy address: five people mistyping
        fail(store, f"nobody{n}", "10.1.1.1")
    assert store.authenticate("boss", GOOD, "10.1.1.1")["username"] == "boss"
    for n in range(25):
        fail(store, f"guess{n}", "10.1.1.1")
    with pytest.raises(LoginRefused, match="paused"):  # cleo never logged in from there (boss did: D-109)
        store.authenticate("cleo", GOOD, "10.1.1.1")
    assert store.authenticate("cleo", GOOD, "10.1.1.2")["username"] == "cleo"
    clock.now += 15 * 60 + 1
    assert store.authenticate("cleo", GOOD, "10.1.1.1")["username"] == "cleo"


def test_the_servers_console_is_never_locked_by_address(store, clock):
    account(store, "boss", "admin")
    for n in range(40):
        fail(store, f"guess{n}", "127.0.0.1", console=True)
    assert store.authenticate("boss", GOOD, "127.0.0.1", console=True)["username"] == "boss"


def test_logins_from_the_console_through_the_page_are_not_locked_by_address(store):
    account(store, "boss", "admin")
    console = client_of(app_for(store), ip="127.0.0.1")
    for n in range(31):
        assert log_in(console, f"guess{n}", WRONG_PASSWORD).status_code == 401
    assert log_in(console, "boss").status_code == 200
    behind = app_for(store)
    behind.state.proxy = "127.0.0.1"  # --proxy: a loopback address is the proxy, not the console
    proxied = client_of(behind, ip="127.0.0.1")
    account(store, "ann")  # (boss logged in from there: an address it knows is not paused for it, D-109)
    for n in range(30):
        log_in(proxied, f"other{n}", WRONG_PASSWORD)
    assert "paused" in log_in(proxied, "ann").json()["error"]


def test_users_reset_clears_the_locks(store, clock):
    uid = account(store, "ann")
    fail(store, "ann", "10.2.2.2", 5)
    for n in range(25):
        fail(store, f"guess{n}", "10.2.2.2")
    temporary = store.reset_password(uid, actor="console")
    assert store.authenticate("ann", temporary, "10.2.2.2")["username"] == "ann"


# --- 2. Failed logins keep no typed text; account events for Admins only ----------------------------------------------


def test_a_failed_login_keeps_no_text_typed_for_an_unknown_username(store, clock):
    account(store, "ann")
    fail(store, "MySecretPassw0rd!", "10.0.0.7")
    fail(store, "ann", "10.0.0.7")
    unknown, known = store.events(actions=["login_failed"])[::-1]
    assert "mysecretpassw0rd" not in str(unknown).lower()
    assert "unknown account" in describe(unknown)
    assert known["detail"]["username"] == "ann" and "ann" in describe(known)
    with sqlite3.connect(store.path) as conn:
        assert "mysecretpassw0rd" not in str(conn.execute("SELECT * FROM login_attempts").fetchall()).lower()
    fail(store, "MySecretPassw0rd!", "10.0.0.8", 5)  # an unknown username pauses the same way
    assert "mysecretpassw0rd" not in str(store.events(actions=["login_locked"])).lower()


def test_login_and_account_events_are_shown_to_admins_only(store):
    account(store, "boss", "admin")
    account(store, "rob", "readonly")
    app = app_for(store)
    log_in(client_of(app, "10.0.0.7"), "ann.typo", WRONG_PASSWORD)
    reader, boss = client_of(app, "10.0.0.8"), client_of(app, "10.0.0.9")
    log_in(reader, "rob")
    log_in(boss, "boss")
    for client, sees in ((reader, False), (boss, True)):
        page = client.get("/activity?view=table").text
        csv = client.get("/activity/events.csv").text
        assert ("Logged in" in page) is sees and ("Logged in" in csv) is sees
        assert ("Login didn" in page) is sees and ("Login didn't match" in csv) is sees
        assert ("Logins" in client.get("/activity").text) is sees  # the filter itself


# --- 3. A restore keeps the accounts and ends every session -----------------------------------------------------------


def test_a_restore_on_the_server_keeps_the_accounts_and_ends_every_session(store, clock):
    boss = account(store, "boss", "admin")
    fired = account(store, "fired")
    backup = store.backup_now("before")
    tok_fired, tok_boss = store.start_session(fired), store.start_session(boss)
    store.deactivate_user(fired, actor="boss")
    store.reset_password(boss, actor="boss", password="brand new secret 1")
    fail(store, "boss", "10.3.3.3", 5)
    store.restore_from(backup, keep_accounts=True)
    assert not store.find_user("fired")["active"]
    assert store.authenticate("boss", "brand new secret 1", "10.3.3.4")["username"] == "boss"
    with pytest.raises(LoginRefused, match="paused"):  # the lockout is kept too
        store.authenticate("boss", "brand new secret 1", "10.3.3.3")
    assert store.session_user(tok_fired) is None and store.session_user(tok_boss) is None
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_a_backup_without_accounts_never_leaves_the_server_with_none(store, clock):
    backup = store.backup_now("before-accounts")
    account(store, "boss", "admin")
    store.restore_from(backup, keep_accounts=True)
    assert [u["username"] for u in store.list_users()] == ["boss"]


def test_a_restore_on_a_laptop_still_ends_every_session(store, clock):
    uid = account(store, "boss", "admin")
    backup = store.backup_now("before")
    token = store.start_session(uid)
    store.restore_from(backup)
    assert store.session_user(token) is None


def test_the_restore_on_the_settings_page_keeps_the_accounts_and_says_so(store):
    boss = account(store, "boss", "admin")
    backup = store.backup_now("before-ann")
    account(store, "ann")
    client = client_of(app_for(store))
    log_in(client, "boss")
    page = client.get("/settings/data").text
    assert "accounts" in page and "logged out" in page
    answer = client.post("/settings/data/restore", data={"name": backup.name, "confirm": "RESTORE"}, headers=PAGE)
    assert answer.status_code == 200, answer.text
    assert answer.json()["redirect"].startswith("/login")
    assert store.find_user("ann") is not None and store.user(boss)["active"]
    assert client.get("/review").status_code == 303  # everyone, the Admin too, logs in again


# --- 4. Guesses in parallel -------------------------------------------------------------------------------------------


def test_thirty_wrong_guesses_in_parallel_check_at_most_five_passwords(store, monkeypatch):
    account(store, "vic")
    checked, lock = [], threading.Lock()
    real = users.verify_password

    def counting(password, stored):
        with lock:
            checked.append(1)
        return real(password, stored)

    monkeypatch.setattr(users, "verify_password", counting)
    start = threading.Barrier(30)

    def guess():
        start.wait()
        try:
            store.authenticate("vic", "bad guess here!", "10.9.9.9")
        except LoginRefused:
            pass

    threads = [threading.Thread(target=guess) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(checked) <= 5
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM login_attempts WHERE ok = 0").fetchone()[0] <= 5


# --- 5. An Admin's own reset ------------------------------------------------------------------------------------------


def test_an_admin_cannot_reset_their_own_password_on_the_users_page(store):
    boss = account(store, "boss", "admin")
    account(store, "boss2", "admin")
    client = client_of(app_for(store))
    log_in(client, "boss")
    assert f"/admin/users/{boss}/reset" not in client.get("/admin/users").text
    refused = client.post(f"/admin/users/{boss}/reset", headers=PAGE)
    assert refused.status_code == 400 and "/account/password" in refused.json()["error"]
    assert "Your password" in refused.json()["error"]
    assert client.get("/review").status_code == 200 and not store.user(boss)["must_change"]


# --- 6. Two session cookies -------------------------------------------------------------------------------------------


def _raw(app, method: str, path: str, cookie: str) -> dict:
    async def run() -> dict:
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                 "scheme": "https", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"host", b"localhost"), (b"cookie", cookie.encode()), (b"x-ap-coder", b"1")],
                 "client": ("10.0.0.5", 1), "server": ("localhost", 443)}  # fmt: skip
        out: dict = {"headers": []}
        sent = [False]

        async def receive() -> dict:
            if sent[0]:
                return {"type": "http.disconnect"}
            sent[0] = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                out["status"], out["headers"] = message["status"], message["headers"]

        await app(scope, receive, send)
        return out

    return asyncio.run(run())


def test_two_session_cookies_are_not_a_login_and_logout_still_clears_it(store):
    account(store, "rob", "readonly")
    app = app_for(store)
    client = client_of(app)
    log_in(client, "rob")
    good = client.cookies.get(SESSION_COOKIE)
    with client:  # the app's lifespan once
        pass
    both = f"{SESSION_COOKIE}=bogus; {SESSION_COOKIE}={good}"
    assert _raw(app, "GET", "/review", both)["status"] == 303
    assert _raw(app, "GET", "/review", f"{SESSION_COOKIE}={good}; {SESSION_COOKIE}=bogus")["status"] == 303
    out = _raw(app, "POST", "/logout", both)
    cleared = [v.decode() for k, v in out["headers"] if k.lower() == b"set-cookie"]
    assert any(c.startswith(f'{SESSION_COOKIE}=""') or c.startswith(f"{SESSION_COOKIE}=;") for c in cleared), cleared
    assert any("Max-Age=0" in c for c in cleared)
    assert store.session_user(good) is None  # the sessions it named are ended too


# --- 7. /healthz behind a proxy on the same server --------------------------------------------------------------------


def test_healthz_tells_only_the_console_the_servers_folder(store):
    app = app_for(store, address="127.0.0.1")  # listening on 127.0.0.1 behind IIS (--proxy 127.0.0.1)
    outside = client_of(app, "10.1.2.40").get("/healthz").json()
    assert outside == {"ok": True, "app": "ap-coder"}
    console = client_of(app, "127.0.0.1").get("/healthz").json()
    assert console["folder"] and console["version"] and "pid" in console


# --- 8. The Secure flag and HSTS --------------------------------------------------------------------------------------


def test_on_the_server_the_cookie_is_secure_and_hsts_sent_whatever_the_scheme(store):
    account(store, "ann")
    plain = client_of(app_for(store), base="http://localhost")  # behind a proxy that ends HTTPS
    answer = log_in(plain, "ann")
    assert "Secure" in answer.headers["set-cookie"]
    assert answer.headers["strict-transport-security"].startswith("max-age=")
    first_test = client_of(app_for(store, insecure_http=True), base="http://localhost")
    answer = log_in(first_test, "ann")
    assert "Secure" not in answer.headers["set-cookie"]
    assert "strict-transport-security" not in answer.headers


# --- 9. Caching -------------------------------------------------------------------------------------------------------


def test_every_response_behind_the_login_is_not_stored_by_the_browser(store):
    account(store, "boss", "admin")
    invoice_id = add_clean(store)
    client = client_of(app_for(store))
    log_in(client, "boss")
    for path in (f"/review/{invoice_id}/page/1.png", "/review", "/activity/events.csv", "/exports/duplicates.csv",
                 "/activity/controls-report", "/spend/data.xlsx", "/insights/business-case",
                 "/settings/jde/worksheet.xlsx", f"/review/{invoice_id}/words"):  # fmt: skip
        answer = client.get(path)
        assert answer.status_code == 200, path
        assert answer.headers.get("cache-control") == "no-store", path
    assert client.get("/static/app.css").headers["cache-control"] == "no-cache"  # still checked, not refused


def test_on_a_laptop_the_page_images_are_cached_as_before(store, monkeypatch):
    monkeypatch.delenv("AP_SERVER_MODE", raising=False)
    invoice_id = add_clean(store)
    client = TestClient(create_app(web_settings(), store, start_reader=False), base_url="http://localhost")
    assert client.get(f"/review/{invoice_id}/page/1.png").headers["cache-control"] == "private, max-age=86400"


# --- 10. The password policy ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [" " * 12, "a" * 12, "cleocleocleo", "abcabcabcabc", "121212121212", "xcleo-is-here", "CLEOcleo2026!",
     "password1234", "qwertyuiop12", "welcome12345", "changeme1234", "Password1234"],
)  # fmt: skip
def test_the_password_policy_refuses_easy_passwords_in_one_message(password):
    said = users.password_problem(password, "cleo", "Cleo Tester")
    assert said == users.EASY_PASSWORD


@pytest.mark.parametrize("password", [GOOD, "plum river 7 lantern", "Tr0ub4dor&3xyz"])
def test_the_password_policy_still_takes_good_passwords(password):
    assert users.password_problem(password, "cleo", "Cleo Tester") == ""


# --- 11. A new session token at a password change ---------------------------------------------------------------------


def test_a_password_change_gives_this_browser_a_new_session_and_ends_the_others(store):
    account(store, "ann")
    app = app_for(store)
    here, there = client_of(app), client_of(app, "10.0.0.6")
    log_in(here, "ann")
    log_in(there, "ann")
    before = here.cookies.get(SESSION_COOKIE)
    form = {"current": GOOD, "new": "a new long password", "again": "a new long password"}
    answer = here.post("/account/password", data=form, headers=PAGE)
    assert answer.status_code == 200 and SESSION_COOKIE in answer.headers.get("set-cookie", "")
    after = here.cookies.get(SESSION_COOKIE)
    assert after and after != before
    assert store.session_user(before) is None and store.session_user(after)["username"] == "ann"
    assert here.get("/review").status_code == 200 and there.get("/review").status_code == 303


def test_resetting_a_user_lifts_the_pause_on_their_address(store, clock):
    uid = account(store, "ann")
    fail(store, "ann", "10.4.4.4")  # ann tried from there (a shared NAT), but never logged in from it
    for n in range(30):
        fail(store, f"guess{n}", "10.4.4.4")
    with pytest.raises(LoginRefused, match="paused"):
        store.authenticate("ann", GOOD, "10.4.4.4")
    temporary = store.reset_password(uid, actor="console")
    assert store.authenticate("ann", temporary, "10.4.4.4")["username"] == "ann"
    fail(store, "guess-again", "10.4.4.4")  # the count starts again: one more try doesn't pause it at once
    assert store.authenticate("ann", temporary, "10.4.4.4")["username"] == "ann"


def test_an_admin_lifts_a_paused_address_on_the_users_page(store):
    account(store, "boss", "admin")
    account(store, "ann")
    account(store, "cleo")
    app = app_for(store)
    for n in range(30):
        log_in(client_of(app, "10.5.5.5"), f"guess{n}", WRONG_PASSWORD)
    assert "paused" in log_in(client_of(app, "10.5.5.5"), "ann").json()["error"]
    clerk = client_of(app, "10.0.0.6")
    log_in(clerk, "cleo")
    assert clerk.post("/admin/users/addresses/unlock", data={"address": "10.5.5.5"}, headers=PAGE).status_code == 403
    boss = client_of(app, "10.0.0.7")
    log_in(boss, "boss")
    page = boss.get("/admin/users").text
    assert "10.5.5.5" in page and "Paused addresses" in page
    lifted = boss.post("/admin/users/addresses/unlock", data={"address": "10.5.5.5"}, headers=PAGE)
    assert lifted.status_code == 200, lifted.text
    assert log_in(client_of(app, "10.5.5.5"), "ann").status_code == 200
    assert "10.5.5.5" not in boss.get("/admin/users").text
    assert store.events(actions=["address_unlocked"])[0]["detail"]["unpaused"] == "10.5.5.5"
