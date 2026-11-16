"""The Admin panel (web/pages/admin.py, D-249): one menu entry for whoever may manage the people, where people sign in;
a page per person (their account, where they are signed in, their sign-ins, what they did, the Users page's actions);
Sign-in (the sessions now, the pauses). The Users page keeps its address."""

from __future__ import annotations

import re

from ap_coder.core.store.users import token_hash
from ap_coder.web.pages.admin import EVENTS_SHOWN, browser_name
from tests.web.test_laptop_sign_in import browser, first_sign_in, laptop
from tests.web.test_server_security import WRONG_PASSWORD, app_for, client_of
from tests.web.test_server_web import account, log_in, server_client
from tests.webclient import PAGE, add_clean

EDGE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 "
    "Edg/120.0"
)
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 "
    "Safari/604.1"
)
ENTRY = 'href="/admin" class="nav-link'


def test_the_browser_is_said_in_words():
    assert browser_name(EDGE) == "Edge on Windows"
    assert browser_name(IPHONE) == "Safari on iPhone"
    assert browser_name("Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0") == "Firefox on Linux"
    assert browser_name("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Chrome/120.0 Safari/537.36") == "Chrome on Mac"
    assert browser_name("") == "An unknown browser" and browser_name("curl/8.0") == "An unknown browser"


def test_one_admin_entry_opens_the_panel_and_only_for_an_admin(store):
    account(store, "boss", "admin", "Pat Boss")
    account(store, "ann", "clerk", "Ann Lee")
    boss, ann = server_client(store), server_client(store)
    log_in(boss, "boss")
    log_in(ann, "ann")
    page = boss.get("/review").text
    assert page.count(ENTRY) == 1 and 'href="/admin/users"' not in page  # the foot's Users link is the entry now
    panel = boss.get("/admin")
    assert panel.status_code == 200
    tabs = re.findall(r'<nav class="tabs" aria-label="Admin">(.*?)</nav>', panel.text, re.S)[0]
    assert re.findall(r">([^<]+)</a>", tabs) == ["Overview", "Users", "Sign-in", "Activity"]
    for link in (
        'href="/admin/users"',
        'href="/admin/sign-in"',
        'href="/activity?group=Logins#log"',
        'href="/settings/reading#azure-check"',
        'href="/settings/jde#api"',
        'href="/settings/data#backups"',
    ):
        assert link in panel.text, link
    assert ENTRY not in ann.get("/review").text
    for url in ("/admin", "/admin/sign-in", "/admin/users/1"):
        assert ann.get(url, headers={"Accept": "text/html"}).status_code == 403, url
    users = boss.get("/admin/users").text  # the old address, as before, with the panel's tabs
    assert 'aria-label="Admin"' in users and re.search(r'href="/admin/users/\d+"[^>]*>ann</a>', users)


def test_a_persons_page_says_who_they_are_where_they_are_signed_in_and_what_they_did(store):
    account(store, "boss", "admin", "Pat Boss")
    ann = account(store, "ann", "clerk", "Ann Lee")
    app = app_for(store)
    boss = client_of(app, "10.0.0.7")
    log_in(boss, "boss")
    log_in(client_of(app, "10.6.6.6"), "ann", WRONG_PASSWORD)
    phone = client_of(app, "10.0.0.9")
    phone.headers["User-Agent"] = IPHONE
    log_in(phone, "ann")
    invoice = add_clean(store)
    for n in range(EVENTS_SHOWN + 5):  # more than a page of what she did, the newest about an invoice
        store.log_event("note", invoice if n == EVENTS_SHOWN + 4 else None, actor="Ann Lee", detail={"text": f"n{n}"})
    store.log_event("note", None, actor="Someone Else", detail={"text": "not hers"})
    page = boss.get(f"/admin/users/{ann}").text
    for said in (
        "Ann Lee",
        "ann",
        "Clerk",
        "Active",
        "Authenticator set up",
        "Last login",
        "Safari on iPhone",
        "10.0.0.9",
        "Logged in",
        "Login didn&#x27;t match",
        "10.6.6.6",
    ):
        assert said in page, said
    with store._conn() as conn:
        tokens = [r[0] for r in conn.execute("SELECT token_hash FROM sessions")]
    assert tokens and not any(t in page for t in tokens)  # never a session's token, nor its hash
    activity = page.split('id="activity"', 1)[1]
    assert f'href="/review/{invoice}"' in activity and "not hers" not in activity
    notes = re.findall(r"n(\d+)</td>", activity)
    assert len(notes) == EVENTS_SHOWN and notes[0] == str(EVENTS_SHOWN + 4)  # newest first, a page at a time
    older = re.search(r'href="(/admin/users/\d+\?before=\d+#activity)"', activity).group(1)
    rest = boss.get(older).text.split('id="activity"', 1)[1]
    assert re.findall(r"n(\d+)</td>", rest)[:5] == ["4", "3", "2", "1", "0"]
    for action in ("role", "reset", "reset-authenticator", "sign-out", "deactivate"):
        assert f'action="/admin/users/{ann}/{action}"' in page, action


def test_actions_on_a_persons_page_come_back_to_it(store):
    boss_id = account(store, "boss", "admin", "Pat Boss")
    ann = account(store, "ann", "clerk", "Ann Lee")
    boss = server_client(store)
    log_in(boss, "boss")
    reset = boss.post(f"/admin/users/{ann}/reset", data={"back": "user"}, headers=PAGE).json()
    assert reset["redirect"].startswith(f"/admin/users/{ann}?shown=")
    assert 'id="temporary"' in boss.get(reset["redirect"]).text  # shown once, there
    role = boss.post(f"/admin/users/{ann}/role", data={"role": "approver", "back": "user"}, headers=PAGE).json()
    assert role["redirect"].startswith(f"/admin/users/{ann}?") and store.user(ann)["role"] == "approver"
    from_users = boss.post(f"/admin/users/{ann}/role", data={"role": "clerk"}, headers=PAGE).json()
    assert from_users["redirect"].startswith("/admin/users?")  # from Users: back to Users, as before
    own = boss.get(f"/admin/users/{boss_id}").text
    assert f'action="/admin/users/{boss_id}/sign-out"' not in own and 'href="/account/password"' in own


def test_an_admin_signs_someone_out_everywhere_but_not_themselves(store):
    boss_id = account(store, "boss", "admin", "Pat Boss")
    ann = account(store, "ann", "clerk", "Ann Lee")
    app = app_for(store)
    boss, office, phone = client_of(app, "10.0.0.7"), client_of(app, "10.0.0.8"), client_of(app, "10.0.0.9")
    log_in(boss, "boss")
    log_in(office, "ann")
    log_in(phone, "ann")
    assert office.get("/review").status_code == 200
    sign_in_page = boss.get("/admin/sign-in").text
    assert sign_in_page.count(f'action="/admin/users/{ann}/sign-out"') == 2  # one per session of hers
    assert f'action="/admin/users/{boss_id}/sign-out"' not in sign_in_page
    ended = boss.post(f"/admin/users/{ann}/sign-out", data={"back": "sign-in"}, headers=PAGE).json()
    assert ended["redirect"].startswith("/admin/sign-in?")
    assert office.get("/review").status_code == 303 and phone.get("/review").status_code == 303  # to the login
    event = store.events(actions=["sessions_ended"])[0]
    assert event["detail"]["username"] == "ann" and event["detail"]["count"] == 2 and event["actor"] == "Pat Boss"
    refused = boss.post(f"/admin/users/{boss_id}/sign-out", headers=PAGE)
    assert refused.status_code == 400 and "Log out everywhere else" in refused.json()["error"]
    assert boss.get("/review").status_code == 200
    clerk = client_of(app, "10.0.0.8")
    log_in(clerk, "ann")
    assert clerk.post(f"/admin/users/{boss_id}/sign-out", headers=PAGE).status_code == 403


def test_a_pause_lifted_on_sign_in_comes_back_there(store):
    account(store, "boss", "admin")
    account(store, "ann", "clerk")
    app = app_for(store)
    for _ in range(5):
        log_in(client_of(app, "10.6.6.6"), "ann", WRONG_PASSWORD)
    boss = client_of(app, "10.0.0.7")
    log_in(boss, "boss")
    page = boss.get("/admin/sign-in").text
    assert "Paused usernames" in page and "10.6.6.6" in page and 'name="back" value="sign-in"' in page
    lifted = boss.post("/admin/users/usernames/unlock", data={"username": "ann", "address": "10.6.6.6",
                                                              "back": "sign-in"}, headers=PAGE)  # fmt: skip
    assert lifted.json()["redirect"].startswith("/admin/sign-in?")
    assert "Nothing is paused now" in boss.get("/admin/sign-in").text


def test_a_laptops_own_sign_in_has_the_panel_without_the_people(store, monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Pat Lee")
    client = browser(laptop(store))
    first_sign_in(client)
    me = store.find_user("admin")["id"]
    page = client.get("/review").text
    assert page.count(ENTRY) == 1
    panel = client.get("/admin").text
    tabs = re.findall(r'<nav class="tabs" aria-label="Admin">(.*?)</nav>', panel, re.S)[0]
    assert re.findall(r">([^<]+)</a>", tabs) == ["Overview", "Sign-in", "Activity"]
    assert f'href="/admin/users/{me}"' in panel and "python -m ap_coder share" in panel
    own = client.get(f"/admin/users/{me}").text
    assert "Pat Lee" in own and "Signed in now" in own and f'action="/admin/users/{me}/role"' not in own
    assert client.get("/admin/sign-in").status_code == 200
    assert client.get("/admin/users").status_code == 404  # the people are the server's, as before


def test_without_sign_in_there_is_no_panel(client):
    assert ENTRY not in client.get("/review").text
    assert client.get("/admin").status_code == 404 and client.get("/admin/users/1").status_code == 404


def test_a_session_kept_by_its_hash_only(store):
    """What the pages read of a session (``live_sessions``) never holds its token or its hash."""
    uid = account(store, "ann", "clerk")
    token = store.start_session(uid, "10.0.0.9", IPHONE)
    (found,) = store.live_sessions(uid)
    assert token not in str(found) and token_hash(token) not in str(found)
    assert (found["address"], found["browser"], found["username"]) == ("10.0.0.9", IPHONE, "ann")
