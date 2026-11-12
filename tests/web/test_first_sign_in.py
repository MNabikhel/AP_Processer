"""The first sign-in (D-243): while the database has no account, admin / admin, typed in a browser on the computer
running AP Coder, leads to the first Admin's own password (on the server with its setup code too), then their
authenticator app, then into AP Coder. From another computer, behind a proxy, or once an account exists, it opens
nothing."""

from __future__ import annotations

import os
import stat

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import Store, users
from ap_coder.web.app import create_app
from tests.webclient import PAGE, authenticator_code, web_settings

CHOSEN = "correct horse battery"
CONSOLE = ("127.0.0.1", 50000)
COLLEAGUE = ("10.0.0.5", 50000)


def server(store: Store, client: tuple[str, int] = CONSOLE, proxy: str | None = None) -> TestClient:
    app = create_app(web_settings(), store, start_reader=False, server=True)
    app.state.proxy = proxy
    return TestClient(app, base_url="https://localhost", client=client, follow_redirects=False)


def admin_admin(client: TestClient, password: str = users.FIRST_PASSWORD, **headers: str):
    return client.post("/login", data={"username": "Admin", "password": password, "next": "/review"},
                       headers={**PAGE, **headers})  # fmt: skip


def choose(client: TestClient, new: str = CHOSEN, again: str | None = None, name: str = "Pat Lee",
           code: str | None = None):  # fmt: skip
    """The first sign-in's page sent; on the server with its setup code (read from its file, as its window shows)."""
    if code is None:
        code = client.app.state.store.first_setup_code()
    return client.post("/login/first", data={"name": name, "new": new, "again": new if again is None else again,
                                             "code": code, "next": "/review"}, headers=PAGE)  # fmt: skip


def test_admin_admin_on_this_computer_leads_to_a_password_then_the_app_then_ap_coder(store):
    client = server(store)
    page = client.get("/login").text
    assert "username <b>admin</b>" in page and "password <b>admin</b>" in page
    answer = admin_admin(client)
    assert answer.status_code == 200 and answer.json()["redirect"] == "/login/first?next=%2Freview"
    assert store.user_count() == 0  # nothing made yet: admin / admin is never a password kept
    form = client.get("/login/first?next=%2Freview")
    assert form.status_code == 200 and 'action="/login/first"' in form.text and "12 characters" in form.text
    chosen = choose(client)
    assert chosen.status_code == 200 and chosen.json()["redirect"].startswith("/login/authenticator")
    made = store.find_user("admin")
    assert (made["role"], made["name"], made["must_change"], made["totp_on"]) == ("admin", "Pat Lee", False, False)
    setup = client.get(chosen.json()["redirect"])
    assert setup.status_code == 200 and "apc-qr" in setup.text and "users reset-2fa admin" in setup.text
    code = authenticator_code(store, "admin")
    done = client.post("/login/authenticator", data={"code": code, "next": "/review"}, headers=PAGE)
    assert done.status_code == 200 and done.json()["redirect"].startswith("/review?")
    assert client.get("/review").status_code == 200
    assert store.find_user("admin")["totp_on"]


def test_admin_admin_ends_once_the_password_is_chosen(store):
    client = server(store)
    admin_admin(client)
    choose(client)
    again = server(store)
    refused = admin_admin(again)
    assert refused.status_code == 401 and refused.json()["error"] == users.WRONG
    assert "password <b>admin</b>" not in again.get("/login").text
    # The browser closed before the app was set up: the person's own password leads straight to that step.
    answer = again.post("/login", data={"username": "admin", "password": CHOSEN, "next": "/review"}, headers=PAGE)
    assert answer.json()["redirect"].startswith("/login/authenticator")


def test_from_another_computer_admin_admin_is_refused_unchecked_and_uncounted(store):
    client = server(store, client=COLLEAGUE)
    page = client.get("/login").text
    assert "No one has an account yet" in page and "password <b>admin</b>" not in page
    for _ in range(12):  # never pauses the first sign-in at the computer itself
        refused = admin_admin(client)
        assert refused.status_code == 403 and refused.json()["error"].startswith(users.FIRST_HERE_ONLY)
        assert refused.json()["error"].endswith("open https://127.0.0.1:443.")  # where it is made
    assert admin_admin(client, "anything at all").json()["error"].startswith(users.FIRST_HERE_ONLY)
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0] == 0
    assert client.get("/login/first").status_code == 303
    assert choose(client).status_code in (401, 403) and store.user_count() == 0
    assert admin_admin(server(store)).json()["redirect"].startswith("/login/first")


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Forwarded-For": "127.0.0.1"},
        {"Forwarded": "for=127.0.0.1"},
        {"X-Real-IP": "127.0.0.1"},
        {"Via": "1.1 proxy"},
        {"X-Original-URL": "/login"},
        {"X-Client-IP": "10.0.0.5"},
        {"True-Client-IP": "10.0.0.5"},
        {"CF-Connecting-IP": "10.0.0.5"},
        {"X-Forwarded-Server": "proxy"},
        {"X-Cluster-Client-IP": "10.0.0.5"},
        {"Fastly-Client-IP": "10.0.0.5"},
    ],
)
def test_behind_a_proxy_on_this_computer_admin_admin_is_refused(store, headers):
    """A reverse proxy on this computer makes every browser arrive from 127.0.0.1: what it adds gives it away (one
    forwarding for another address is refused before any page: security.unnamed_proxy)."""
    refused = admin_admin(server(store), **headers)
    assert refused.status_code in (400, 403) and "/login/first" not in refused.text
    assert admin_admin(server(store, proxy="127.0.0.1")).json()["error"].startswith(users.FIRST_HERE_ONLY)


def test_through_another_name_than_this_computers_admin_admin_is_refused(store, monkeypatch):
    monkeypatch.setenv("AP_ALLOWED_HOSTS", "apcoder.company.local")
    client = server(store)
    refused = client.post("/login", data={"username": "admin", "password": "admin"},
                          headers={**PAGE, "Host": "apcoder.company.local"})  # fmt: skip
    assert refused.json()["error"].startswith(users.FIRST_HERE_ONLY)


def test_another_password_for_admin_is_an_ordinary_wrong_one(store):
    refused = admin_admin(server(store), "not the password")
    assert refused.status_code == 401 and refused.json()["error"] == users.WRONG


def test_the_password_page_needs_admin_admin_first_and_follows_the_policy(store):
    client = server(store)
    assert client.get("/login/first").headers["location"].startswith("/login")
    assert choose(client).status_code == 401 and store.user_count() == 0
    admin_admin(client)
    assert "not the same" in choose(client, again="something else entirely").json()["error"]
    assert "12 characters" in choose(client, "short").json()["error"]
    assert "easy to guess" in choose(client, "admin and more admin").json()["error"]
    assert "your name" in choose(client, name=" ").json()["error"]
    assert client.get("/login/authenticator").status_code == 303  # the first sign-in is no login half way
    assert store.user_count() == 0


def test_two_browsers_at_the_first_sign_in_make_one_account(store):
    first, second = server(store), server(store)
    admin_admin(first), admin_admin(second)
    assert choose(first).status_code == 200
    late = choose(second, "another long password", name="Sam Roe")
    assert late.status_code == 400 and late.json()["error"] == users.FIRST_DONE
    assert store.user_count() == 1 and store.find_user("admin")["name"] == "Pat Lee"


def test_once_an_account_exists_admin_admin_is_an_ordinary_login(store):
    store.add_user("boss", CHOSEN, role="admin")
    refused = admin_admin(server(store))
    assert refused.status_code == 401 and refused.json()["error"] == users.WRONG


# --- The server's setup code: a forwarder on the server can't prove who is at it -------------------------------------


def test_on_the_server_the_first_sign_in_also_needs_the_setup_code_from_the_console(store):
    """A plain TCP forwarder on the server (netsh portproxy, ssh -L, socat, nginx's default proxy_pass) brings any
    browser in from 127.0.0.1 with a Host it writes itself: admin / admin alone would give the first account away."""
    client = server(store)
    admin_admin(client)
    form = client.get("/login/first").text
    assert 'name="code"' in form and "python -m ap_coder doctor" in form and "service.log" in form
    code = store.first_setup_code()
    assert len(code) == 19 and code == store.first_setup_code()  # made once, the same until it is used
    for wrong in ("", "abcd-efgh-jkmn-pqrs", code[:-1]):
        refused = choose(client, code=wrong)
        assert refused.status_code == 403 and refused.json()["error"] == users.SETUP_CODE_WRONG
    assert store.user_count() == 0
    assert choose(client, code=f"  {code.upper().replace('-', ' ')} ").status_code == 200  # as typed from a screen
    assert store.find_user("admin") is not None
    assert store.first_setup_code() == "" and not (store.path.parent / users.SETUP_CODE_FILE).exists()


@pytest.mark.skipif(os.name == "nt", reason="on Windows the data folder's own permissions cover the file")
def test_the_setup_code_is_readable_by_the_service_account_only(store):
    store.first_setup_code()
    assert stat.S_IMODE((store.path.parent / users.SETUP_CODE_FILE).stat().st_mode) == 0o600


def test_the_laptop_needs_no_setup_code(store):
    store.set_setting("sign_in", "laptop")
    client = TestClient(create_app(web_settings(), store, start_reader=False), base_url="http://localhost",
                        client=CONSOLE, follow_redirects=False)  # fmt: skip
    admin_admin(client)
    assert 'name="code"' not in client.get("/login/first").text
    assert choose(client, code="").status_code == 200 and not (store.path.parent / users.SETUP_CODE_FILE).exists()
