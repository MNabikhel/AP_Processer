"""Sign-in on the laptop (D-243): a data folder whose ``sign_in`` setting is ``laptop`` opens for its Admin only, after
the password and the authenticator's code, from this computer's own browser. Signing in only guards the door: the
person is this computer's own as before (their saved name on approvals, the computer's login, their drafts, full
folders), and a session lasts 8 hours unused."""

from __future__ import annotations

import getpass

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import Store, users
from ap_coder.core.store.sign_in import LAPTOP, LAPTOP_IDLE_SECONDS, SIGN_IN
from ap_coder.web import core, identity
from ap_coder.web.app import create_app
from tests.webclient import PAGE, authenticator_code, enrol, web_settings

CHOSEN = "correct horse battery"
HERE = ("127.0.0.1", 50000)


def laptop(store: Store, address: str = "127.0.0.1", sign_in: bool | None = None):
    store.set_setting(SIGN_IN, LAPTOP)
    app = create_app(web_settings(), store, address=address, start_reader=False, sign_in=sign_in)

    @app.get("/probe")
    def probe():  # who the pages take the person for
        return {"reviewer": core.reviewer(), "login": core.login(), "person": identity.person() is not None}

    return app


def browser(app, client: tuple[str, int] = HERE) -> TestClient:
    return TestClient(app, base_url="http://localhost", client=client, follow_redirects=False)


def first_sign_in(client: TestClient, name: str | None = None) -> None:
    """admin / admin, the person's own password, then the authenticator's code: signed in."""
    answer = client.post("/login", data={"username": "admin", "password": "admin", "next": "/review"}, headers=PAGE)
    assert answer.json()["redirect"].startswith("/login/first"), answer.text
    form = client.get(answer.json()["redirect"]).text
    shown = form.split('name="name" value="', 1)[1].split('"', 1)[0]
    fields = {"name": name or shown, "new": CHOSEN, "again": CHOSEN, "next": "/review"}
    chosen = client.post("/login/first", data=fields, headers=PAGE)
    assert chosen.json()["redirect"].startswith("/login/authenticator"), chosen.text
    client.get(chosen.json()["redirect"])
    code = authenticator_code(client.app.state.store, "admin")
    done = client.post("/login/authenticator", data={"code": code, "next": "/review"}, headers=PAGE)
    assert done.json()["ok"], done.text


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Pat Lee")  # the name on the laptop's approvals so far


def test_without_the_setting_the_laptop_opens_as_before(store, client):
    assert client.get("/review").status_code == 200
    assert not client.app.state.sign_in


def test_with_it_every_page_waits_for_the_sign_in(store, owner):
    client = browser(laptop(store))
    answer = client.get("/review?show=all")
    assert answer.status_code == 303 and answer.headers["location"] == "/login?next=%2Freview%3Fshow%3Dall"
    refused = client.post("/review/bulk-approve", data={"ids": "1"}, headers=PAGE)
    assert refused.status_code == 401 and refused.json()["redirect"] == "/login"
    assert client.get("/healthz").status_code == 200 and client.get("/login").status_code == 200
    assert "password <b>admin</b>" in client.get("/login").text


def test_the_first_sign_in_keeps_the_name_on_the_approvals_and_the_person_is_this_computers(store, owner):
    client = browser(laptop(store))
    first_sign_in(client)
    assert store.find_user("admin")["name"] == "Pat Lee"  # offered from the laptop's own name
    page = client.get("/review")
    assert page.status_code == 200 and "Log out" in page.text and 'href="/admin/users"' not in page.text
    assert client.get("/probe").json() == {"reviewer": "Pat Lee", "login": getpass.getuser(), "person": False}
    assert client.get("/settings").status_code == 200 and client.get("/account/password").status_code == 200


def test_a_laptop_session_lasts_8_hours_unused_the_servers_30_minutes(store):
    laptop(store)
    assert store.session_idle == LAPTOP_IDLE_SECONDS == 8 * 3600
    other = Store(store.path.with_name("server.db"))
    create_app(web_settings(), other, start_reader=False, server=True)
    assert other.session_idle == users.IDLE_SECONDS


def test_logging_out_closes_the_door_again(store, owner):
    client = browser(laptop(store))
    first_sign_in(client)
    assert client.post("/logout", headers=PAGE).json()["ok"]
    assert client.get("/review").status_code == 303


def test_only_an_admin_signs_in_on_the_laptop(store, owner):
    uid = store.add_user("clerk", CHOSEN, role="clerk", must_change=False)
    enrol(store, uid)
    client = browser(laptop(store))
    refused = client.post("/login", data={"username": "clerk", "password": CHOSEN}, headers=PAGE)
    assert refused.status_code == 403 and "share" in refused.json()["error"]


def test_another_computer_gets_no_names_page_and_no_sign_in(store, owner):
    app = laptop(store, address="0.0.0.0")
    colleague = browser(app, ("10.0.0.5", 50000))
    for path in ("/review", "/who", "/login"):
        page = colleague.get(path)
        assert page.status_code == 403 and "python -m ap_coder share" in page.text, path
    refused = colleague.post("/login", data={"username": "admin", "password": "admin"}, headers=PAGE)
    assert refused.status_code == 403 and "share" in refused.json()["error"]
    assert colleague.get("/healthz").status_code == 200
    first_sign_in(browser(app))  # this computer's own browser, as before


def test_without_sign_in_on_purpose_the_setting_is_left_and_the_laptop_opens(store, owner):
    client = browser(laptop(store, sign_in=False))
    assert client.get("/review").status_code == 200 and store.get_setting(SIGN_IN) == LAPTOP


def test_a_new_name_in_settings_is_the_accounts_too(store, owner):
    """The account keeps the name on the approvals, so a later ``share`` still knows them for the owner's."""
    client = browser(laptop(store))
    first_sign_in(client)
    answer = client.post("/settings/review", data={"reviewer_name": "Pat A. Lee", "threshold": "80"}, headers=PAGE)
    assert answer.json()["ok"], answer.text
    assert store.find_user("admin")["name"] == "Pat A. Lee" and client.get("/probe").json()["reviewer"] == "Pat A. Lee"


def test_a_restore_on_settings_keeps_the_laptops_admin_and_its_sign_in(store, owner):
    backup = store.backup_now()  # from before the sign-in
    client = browser(laptop(store))
    first_sign_in(client)
    answer = client.post("/settings/data/restore", data={"name": backup.name, "confirm": "RESTORE"}, headers=PAGE)
    assert answer.json()["ok"], answer.text
    assert store.get_setting(SIGN_IN) == LAPTOP and store.find_user("admin")["name"] == "Pat Lee"


def test_the_who_are_you_page_shows_nothing_before_the_sign_in(store, owner):
    """Open to anyone (roles.OPEN), the "Who are you?" page showed the owner's name and the menu without a session."""
    client = browser(laptop(store))
    page = client.get("/who")
    assert page.status_code == 303 and page.headers["location"] == "/login" and "Pat Lee" not in page.text
    assert client.post("/who", data={"name": "Someone"}, headers=PAGE).status_code == 403


def test_a_laptop_listening_on_its_own_address_signs_in_from_its_own_browser(store, owner):
    """AP_DASHBOARD_ADDRESS set to the laptop's own address: 127.0.0.1 doesn't answer, its own browser comes from that
    address (identity.from_this_computer), and the first sign-in is made there."""
    app = laptop(store, address="192.168.1.20")
    own = TestClient(app, base_url="http://192.168.1.20:8501", client=("192.168.1.20", 50000), follow_redirects=False)
    first_sign_in(own)
    assert own.get("/review").status_code == 200


def test_the_laptops_words_name_the_console_not_an_admin_to_ask(store, owner):
    client = browser(laptop(store))
    first_sign_in(client)
    client.post("/logout", headers=PAGE)
    page = client.get("/login").text
    assert "Ask an Admin" not in page and "users reset-2fa admin" in page
    answer = client.post("/login", data={"username": "admin", "password": CHOSEN, "next": "/review"}, headers=PAGE)
    code = client.get(answer.json()["redirect"]).text
    assert "Ask an Admin" not in code and "users reset-2fa admin" in code
    refused = client.post("/login/code", data={"code": "000000", "next": "/review"}, headers=PAGE).json()["error"]
    assert "clock" in refused
