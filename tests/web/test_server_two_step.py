"""Two-step sign-in on the server (D-146): the password, then a code from an authenticator app; set up at the first
login (after the password the Admin gave is changed), asked at every login after it. Until both steps pass there is
no session: the login half way reaches no page but its own next step."""

from __future__ import annotations

import re
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from ap_coder.core import authenticator
from ap_coder.core.store import Store, load_sample_setup, users
from ap_coder.web import roles
from ap_coder.web.app import create_app
from ap_coder.web.identity import PENDING_COOKIE, SESSION_COOKIE
from ap_coder.web.pages import login
from tests.web.test_roles import Probe, app_routes, concrete
from tests.webclient import PAGE, authenticator_code, client_for, enrol, sign_in, web_settings

GOOD = "correct horse battery"
TEMPORARY = "abcd-efgh-jkmn-pqrs"
STEPS = ("/login/new-password", "/login/authenticator", "/login/code")


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


def server_client(store: Store) -> TestClient:
    app = create_app(web_settings(), store, start_reader=False, server=True)
    return TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)


def account(store: Store, username: str, role: str = "clerk") -> int:
    """An account with its own password and its authenticator app set up."""
    uid = store.add_user(username, GOOD, role=role, display_name=username.title())
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    enrol(store, uid)
    return uid


def redirect(answer) -> str:
    assert answer.status_code == 200, answer.text
    return answer.json()["redirect"]


def shown_secret(page: str) -> str:
    return re.search(r'data-secret="([A-Z2-7 ]+)"', page).group(1).replace(" ", "")


# --- The first login: the temporary password, a new one, the authenticator app, then AP Coder -------------------------


def test_the_first_login_changes_the_password_then_sets_up_the_app_then_opens_ap_coder(store):
    uid = store.add_user("ann", TEMPORARY, role="clerk", display_name="Ann Lee")  # as an Admin adds her
    client = server_client(store)
    answer = client.post("/login", data={"username": "ann", "password": TEMPORARY, "next": "/review"}, headers=PAGE)
    assert redirect(answer).startswith("/login/new-password?next=%2Freview")
    assert SESSION_COOKIE not in client.cookies and PENDING_COOKIE in client.cookies
    assert not store.events(actions=["login"])  # the password alone is no login
    assert client.get("/review").headers["location"].startswith("/login?next=")  # nothing opens half way
    page = client.get("/login/new-password?next=%2Freview")
    assert page.status_code == 200 and "Choose your own password" in page.text
    answer = client.post("/login/new-password", data={"current": TEMPORARY, "new": "my own good password",
                                                      "again": "my own good password", "next": "/review"},
                         headers=PAGE)  # fmt: skip
    assert redirect(answer).startswith("/login/authenticator?next=%2Freview")
    assert not store.user(uid)["must_change"] and SESSION_COOKIE not in client.cookies
    page = client.get("/login/authenticator?next=%2Freview")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    for words in ("Microsoft Authenticator", "Google Authenticator", "Any authenticator app works", "<svg"):
        assert words in page.text, words
    secret = shown_secret(page.text)
    assert f"otpauth://totp/AP%20Coder:ann?secret={secret}&amp;issuer=AP%20Coder" in page.text
    refused = client.post("/login/authenticator", data={"code": "12345", "next": "/review"}, headers=PAGE)
    assert refused.status_code == 400 and "six digits" in refused.json()["error"]  # a slip: not a try counted
    code = authenticator.code(secret, users._clock())
    answer = client.post("/login/authenticator", data={"code": code, "next": "/review"}, headers=PAGE)
    assert unquote(redirect(answer)).split("?")[0] == "/review"
    assert PENDING_COOKIE not in client.cookies and client.cookies[SESSION_COOKIE]
    assert client.get("/review").status_code == 200
    assert store.user(uid)["totp_on"]
    assert [e["action"] for e in store.events()][:3] == ["login", "authenticator_enrolled", "password_changed"]


def test_every_later_login_asks_the_code_and_the_session_is_a_new_token(store):
    account(store, "ann")
    client = server_client(store)
    answer = client.post("/login", data={"username": "ann", "password": GOOD, "next": "/exports"}, headers=PAGE)
    assert redirect(answer).startswith("/login/code?next=%2Fexports")
    pending = client.cookies[PENDING_COOKIE]
    page = client.get("/login/code?next=%2Fexports")
    assert page.status_code == 200 and 'autocomplete="one-time-code"' in page.text and "data-secret" not in page.text
    answer = client.post("/login/code", data={"code": authenticator_code(store, "ann"), "next": "/exports"},
                         headers=PAGE)  # fmt: skip
    assert redirect(answer) == "/exports"
    assert client.cookies[SESSION_COOKIE] != pending and store.pending_user(pending) is None
    assert store.session_user(pending) is None
    assert client.get("/exports").status_code == 200


def test_a_wrong_code_is_refused_and_the_fifth_ends_the_login_paused(store):
    account(store, "ann")
    client = server_client(store)
    for _ in range(4):
        client.post("/login", data={"username": "ann", "password": GOOD}, headers=PAGE)
        right = authenticator_code(store, "ann")
        refused = client.post("/login/code", data={"code": "000000" if right != "000000" else "111111"}, headers=PAGE)
        assert refused.status_code == 401 and refused.json()["error"] == users.WRONG_CODE
    client.post("/login", data={"username": "ann", "password": GOOD}, headers=PAGE)
    right = authenticator_code(store, "ann")
    refused = client.post("/login/code", data={"code": "000000" if right != "000000" else "111111"}, headers=PAGE)
    assert refused.status_code == 401 and refused.json()["redirect"].startswith("/login")
    assert PENDING_COOKIE not in client.cookies
    again = client.post("/login", data={"username": "ann", "password": GOOD}, headers=PAGE)
    assert again.status_code == 401 and "paused" in again.json()["error"]


def test_the_steps_open_only_in_their_order_and_the_secret_is_never_shown_once_set_up(store):
    client = server_client(store)
    for path in STEPS:  # no login under way
        assert client.get(path).headers["location"].startswith("/login")
        assert client.post(path, data={"code": "123456"}, headers=PAGE).status_code == 401
        assert client.post(path, files={"code": (None, "123456")}, headers=PAGE).status_code == 415  # as /login
    store.add_user("new", TEMPORARY)
    client.post("/login", data={"username": "new", "password": TEMPORARY}, headers=PAGE)
    for path in STEPS[1:]:  # the password first
        assert client.get(path).headers["location"].startswith("/login/new-password")
        assert client.post(path, data={"code": "123456"}, headers=PAGE).status_code == 400
    account(store, "ann")
    client.post("/login", data={"username": "ann", "password": GOOD}, headers=PAGE)
    assert client.get("/login/authenticator").headers["location"].startswith("/login/code")
    assert client.post("/login/authenticator", data={"code": "123456"}, headers=PAGE).status_code == 400
    assert client.post("/login/new-password", data={"current": GOOD, "new": "x" * 20, "again": "x" * 20},
                       headers=PAGE).status_code == 400  # fmt: skip


# --- Half way, nothing opens ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["new password", "set up", "code"])
def test_a_login_half_way_reaches_no_page_of_the_app(store, stage):
    """Every route of the app, walked as the role tests walk them, with the cookie of a login half way (and the same
    token sent as a session's): only what is open to anyone answers."""
    uid = store.add_user("ann", GOOD, role="admin")  # an Admin's: every route would be theirs
    if stage != "new password":
        with store._conn() as conn:
            conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    if stage == "code":
        enrol(store, uid)
    pending = store.start_pending(uid)
    app = create_app(web_settings(), store, start_reader=False, server=True)
    client = TestClient(Probe(app), base_url="http://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    wrong = []
    for method, path in app_routes(app):
        need, url = roles.RULES[(method, path)], concrete(path)
        for cookie in (f"{PENDING_COOKIE}={pending}", f"{SESSION_COOKIE}={pending}", f"ap_session={pending}"):
            answer = client.request(method, url, headers={**PAGE, "Accept": "text/html", "Cookie": cookie})
            if need == roles.OPEN:
                ok = answer.status_code == 200
            else:
                ok = answer.status_code == (303 if method == "GET" else 401)
                ok = ok and (method != "GET" or answer.headers["location"].startswith("/login?next="))
            if not ok:
                wrong.append(f"{method} {url} ({need}, {cookie.split('=')[0]}): {answer.status_code}")
    assert wrong == []


# --- An Admin resets a person's authenticator ---------------------------------------------------------------------


def test_an_admin_resets_a_persons_authenticator_on_the_users_page_and_they_set_it_up_again(store):
    account(store, "boss", "admin")
    ann = account(store, "ann")
    theirs = server_client(store)
    assert redirect(sign_in(theirs, "ann", GOOD)) == "/review"
    admin = server_client(store)
    sign_in(admin, "boss", GOOD)
    page = admin.get("/admin/users").text
    assert f"/admin/users/{ann}/reset-authenticator" in page and "Codes set up" in page
    answer = admin.post(f"/admin/users/{ann}/reset-authenticator", headers=PAGE)
    assert "set it up again" in admin.get(redirect(answer)).text
    assert theirs.get("/review").status_code == 303  # logged out
    assert store.events(actions=["authenticator_reset"])[0]["actor"] == "Boss"
    answer = theirs.post("/login", data={"username": "ann", "password": GOOD}, headers=PAGE)
    assert redirect(answer).startswith("/login/authenticator")
    clerk = server_client(store)
    sign_in(clerk, "ann", GOOD)
    assert clerk.post(f"/admin/users/{ann}/reset-authenticator", headers=PAGE).status_code in (401, 403)


# --- On a laptop: no logins, as before ----------------------------------------------------------------------------


def test_on_a_laptop_nothing_asks_for_a_code_and_the_steps_send_to_the_queue(store):
    client = client_for(store)
    client.follow_redirects = False
    assert client.get("/review").status_code == 200  # no login, no cookie
    for path in ("/login", *STEPS):
        assert client.get(path).headers["location"] == "/review"
    for path in STEPS:
        assert client.post(path, data={"code": "123456"}, headers=PAGE).status_code == 400
    assert client.post("/admin/users/1/reset-authenticator", headers=PAGE).status_code == 404
    assert not (store.path.parent / authenticator.KEY_FILE).exists()


def test_the_qr_code_is_an_svg_of_the_address_with_its_quiet_zone():
    uri = authenticator.otpauth_uri("a" * 64, authenticator.new_secret())  # the longest username there can be
    svg = login.qr_svg(uri)
    size = int(re.search(r'viewBox="0 0 (\d+) \1"', svg).group(1)) - 2 * login.QUIET
    assert svg.startswith("<svg") and "<path" in svg and "script" not in svg
    assert size % 4 == 1 and size >= 21  # a QR code is 17 + 4 x its version modules a side
