"""Roles on the Admin panel (web/pages/admin_roles.py, D-250): the built-in roles shown as they are, a role made with
a name and tick boxes, given to a person, changed and deleted; what it gives is what the server lets through."""

from __future__ import annotations

import re

from ap_coder.core.store.roles import OWN_USERS
from ap_coder.core.store.users import OWN_OFF
from tests.web.test_laptop_sign_in import browser, first_sign_in, laptop
from tests.web.test_server_security import app_for, client_of
from tests.web.test_server_web import GOOD, account, log_in
from tests.webclient import PAGE, add_clean

LEAD = ["view", "activity", "import", "code", "approve", "final_approve", "export", "post", "controls"]
HTML = {"Accept": "text/html"}


def _made(boss, name="AP lead", permissions=LEAD):
    answer = boss.post("/admin/roles/add", data={"name": name, "permissions": permissions}, headers=PAGE)
    assert answer.status_code == 200, answer.text
    return re.match(r"/admin/roles/(role-\d+)", answer.json()["redirect"]).group(1)


def test_the_built_in_roles_are_shown_as_they_are_and_copied_as_a_start(store):
    account(store, "boss", "admin", "Pat Boss")
    boss = client_of(app_for(store), "10.0.0.7")
    log_in(boss, "boss")
    page = boss.get("/admin/roles").text
    assert re.findall(r'<a href="/admin/roles/[^"]+"><b>([^<]+)</b>', page) == [
        "Read-only", "Clerk", "Approver", "Admin", "Manager"]  # fmt: skip
    clerk = boss.get("/admin/roles/clerk").text
    assert 'action="/admin/roles/clerk/save"' not in clerk and "/admin/roles/clerk/delete" not in clerk
    assert clerk.count('name="permissions"') == 14 and clerk.count(" disabled>") >= 14  # every box, none to change
    copy = boss.get("/admin/roles/new?copy=clerk").text
    assert 'value="Clerk (copy)"' in copy and 'action="/admin/roles/add"' in copy
    ticked = re.findall(r'name="permissions" value="(\w+)" checked', copy)
    assert ticked == ["view", "activity", "import", "code", "approve", "export"]
    assert boss.post("/admin/roles/clerk/save", data={"name": "Clerk", "permissions": ["view"]},
                     headers=PAGE).status_code == 400  # fmt: skip
    assert boss.get("/admin/roles/role-9", headers=HTML).status_code == 404


def test_an_ap_lead_approves_but_never_opens_admin(store):
    account(store, "boss", "admin", "Pat Boss")
    ann = account(store, "ann", "clerk", "Ann Lee")
    app = app_for(store)
    boss, lead = client_of(app, "10.0.0.7"), client_of(app, "10.0.0.8")
    log_in(boss, "boss")
    key = _made(boss)
    given = boss.post(f"/admin/users/{ann}/role", data={"role": key, "back": "user"}, headers=PAGE)
    assert given.status_code == 200 and store.user(ann)["role_label"] == "AP lead"
    assert "AP lead" in boss.get(f"/admin/users/{ann}").text
    assert f'<option value="{key}" selected>AP lead</option>' in boss.get(f"/admin/users/{ann}").text
    log_in(lead, "ann")
    queue = lead.get("/review").text
    assert "AP lead · " in queue and 'href="/admin" class="nav-link' not in queue
    invoice = add_clean(store)
    approve = lead.post(f"/review/{invoice}/approve", data={}, headers=PAGE)
    assert approve.status_code not in (401, 403), approve.text  # let through to the page (its own checks there)
    assert lead.post(f"/review/{invoice}/final-approve", data={}, headers=PAGE).status_code != 403
    for url in ("/admin", "/admin/users", f"/admin/users/{ann}", "/admin/roles", "/settings"):
        assert lead.get(url, headers=HTML).status_code == 403, url
    assert (
        lead.post("/admin/roles/add", data={"name": "Mine", "permissions": ["users"]}, headers=PAGE).status_code == 403
    )
    # Changed: from the next click on.
    edited = boss.post(f"/admin/roles/{key}/save", data={"name": "AP lead", "permissions": ["view", "code"]},
                       headers=PAGE)  # fmt: skip
    assert edited.status_code == 200, edited.text
    assert lead.post(f"/review/{invoice}/approve", data={}, headers=PAGE).status_code == 403
    assert "Your role (AP lead) can" in lead.post(f"/review/{invoice}/approve", data={}, headers=PAGE).json()["error"]


def test_a_role_is_deleted_only_when_no_one_has_it(store):
    account(store, "boss", "admin", "Pat Boss")
    ann = account(store, "ann", "clerk", "Ann Lee")
    boss = client_of(app_for(store), "10.0.0.7")
    log_in(boss, "boss")
    key = _made(boss)
    boss.post(f"/admin/users/{ann}/role", data={"role": key}, headers=PAGE)
    page = boss.get(f"/admin/roles/{key}").text
    assert f"/admin/roles/{key}/delete" not in page and "another role first" in page and "Ann Lee" in page
    refused = boss.post(f"/admin/roles/{key}/delete", headers=PAGE)
    assert refused.status_code == 400 and "AP lead is the role of 1 account" in refused.json()["error"]
    boss.post(f"/admin/users/{ann}/role", data={"role": "clerk"}, headers=PAGE)
    assert f'action="/admin/roles/{key}/delete"' in boss.get(f"/admin/roles/{key}").text
    assert boss.post(f"/admin/roles/{key}/delete", headers=PAGE).json()["redirect"].startswith("/admin/roles?")
    assert store.custom_roles() == []
    assert [e["action"] for e in store.events(actions=["role_created", "role_deleted"])] == [
        "role_deleted",
        "role_created",
    ]


def test_an_admin_keeps_their_own_manage_users(store):
    boss = account(store, "boss", "admin", "Pat Boss")
    account(store, "sam", "admin", "Sam Other")
    client = client_of(app_for(store), "10.0.0.7")
    log_in(client, "boss")
    refused = client.post(f"/admin/users/{boss}/role", data={"role": "clerk"}, headers=PAGE)
    assert refused.status_code == 400 and refused.json()["error"] == OWN_USERS
    off = client.post(f"/admin/users/{boss}/deactivate", headers=PAGE)
    assert off.status_code == 400 and off.json()["error"] == OWN_OFF
    key = _made(client, "Keeper", ["view", "users"])
    assert client.post(f"/admin/users/{boss}/role", data={"role": key}, headers=PAGE).status_code == 200
    assert client.get("/admin/roles").status_code == 200  # still manages the people, as a Keeper
    refused = client.post(f"/admin/roles/{key}/save", data={"name": "Keeper", "permissions": ["view"]}, headers=PAGE)
    assert refused.status_code == 400 and refused.json()["error"] == OWN_USERS
    assert store.user(boss)["role"] == key and store.role_info(key)["permissions"] == ["view", "users"]


def test_the_users_page_offers_the_roles_made_here(store):
    account(store, "boss", "admin", "Pat Boss")
    boss = client_of(app_for(store), "10.0.0.7")
    log_in(boss, "boss")
    key = _made(boss)
    page = boss.get("/admin/users").text
    assert f'<option value="{key}">AP lead</option>' in page
    listed = page.split("<h4>The roles</h4>", 1)[1]
    assert re.findall(r"<li><b>([^<]+)</b>", listed) == ["Read-only", "Clerk", "Approver", "Admin", "Manager",
                                                        "AP lead"]  # fmt: skip
    added = boss.post("/admin/users/add", data={"username": "cy", "name": "Cy", "role": key}, headers=PAGE)
    assert added.status_code == 200 and store.find_user("cy")["role"] == key


def test_a_laptops_own_sign_in_has_no_roles_page(store, monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Pat Lee")
    client = browser(laptop(store))
    first_sign_in(client)
    assert client.get("/admin/roles").status_code == 404


def test_a_role_that_only_manages_people_never_makes_itself_admin(store):
    """An HR role (Manage users and roles alone) through the pages: it can't tick more for itself, make a role with
    more, give more, nor demote or turn off the Admin (B-2561). The Roles page says so."""
    boss = account(store, "boss", "admin", "Pat Boss")
    ann = account(store, "ann", "clerk", "Ann Lee")
    app = app_for(store)
    admin, hr = client_of(app, "10.0.0.7"), client_of(app, "10.0.0.8")
    log_in(admin, "boss")
    key = _made(admin, "HR", ["users"])
    assert admin.post(f"/admin/users/{ann}/role", data={"role": key}, headers=PAGE).status_code == 200
    log_in(hr, "ann", next="/admin")
    everything = ["view", "activity", "import", "code", "approve", "final_approve", "export", "post", "controls",
                  "setup", "undo", "settings", "users"]  # fmt: skip
    for url, data in (
        (f"/admin/roles/{key}/save", {"name": "HR", "permissions": everything}),
        ("/admin/roles/add", {"name": "Mine", "permissions": everything}),
        (f"/admin/users/{ann}/role", {"role": "admin"}),
        (f"/admin/users/{boss}/role", {"role": "readonly"}),
        (f"/admin/users/{boss}/deactivate", {}),
        (f"/admin/users/{boss}/reset", {}),
        (f"/admin/users/{boss}/sign-out", {}),
    ):
        refused = hr.post(url, data=data, headers=PAGE)
        assert refused.status_code == 400, (url, refused.text)
        assert "more than" in refused.json()["error"], url
    assert store.role_info(key)["permissions"] == ["users"] and store.user(ann)["role"] == key
    assert store.user(boss)["role"] == "admin" and store.user(boss)["active"]
    assert "never gives more than their own role may do" in admin.get("/admin/roles").text


def test_the_plain_refusal_page_shows_a_roles_name_as_text(store, monkeypatch):
    from ap_coder.web import core

    account(store, "boss", "admin", "Pat Boss")
    ann = account(store, "ann", "clerk", "Ann Lee")
    admin = client_of(app_for(store), "10.0.0.7")
    log_in(admin, "boss")
    key = _made(admin, "AP <i>lead</i>", ["view"])
    store.set_role(ann, key)
    lead = client_of(app_for(store), "10.0.0.8")
    log_in(lead, "ann")

    def broken(*args, **kwargs):
        raise RuntimeError("the page frame can't be drawn")

    monkeypatch.setattr(core, "render", broken)
    page = lead.get("/settings", headers=HTML)
    assert page.status_code == 403 and "AP &lt;i&gt;lead&lt;/i&gt;" in page.text and "<i>" not in page.text


def test_a_role_that_sees_no_invoices_lands_on_a_page_it_may_open(store):
    """B-2578: a role without See invoices and reports (people's accounts only, or bringing invoices in) signed in to
    the review queue and "Not allowed", and the menu's AP Coder and the refusal's button led back there."""
    account(store, "boss", "admin", "Pat Boss")
    app = app_for(store)
    boss = client_of(app, "10.0.0.7")
    log_in(boss, "boss")
    desk = _made(boss, "People desk", ["users"])
    importer = _made(boss, "Importer", ["import"])
    account(store, "ivy", desk, "Ivy Tan")
    account(store, "ike", importer, "Ike Low")
    for username, lands in (("ivy", "/admin"), ("ike", "/process")):
        person = client_of(app, "10.0.0.8")
        password = person.post("/login", data={"username": username, "password": GOOD, "next": "/review"},
                               headers=PAGE)  # fmt: skip  # the login page's own next: the review queue
        assert password.json()["redirect"] == "/login/code?next=%2F", password.text  # the code's step, then /
        log_in(person, username, next="/")
        home = person.get("/", headers=HTML)
        assert home.status_code == 307 and home.headers["location"] == lands
        assert person.get(lands, headers=HTML).status_code == 200
        refused = person.get("/review", headers=HTML)
        assert refused.status_code == 403 and 'href="/review"' not in refused.text and 'href="/"' in refused.text
    assert boss.get("/", headers=HTML).headers["location"] == "/review"


def test_a_copy_of_a_role_with_a_long_name_is_named_in_full(store):
    """B-2580: "(copy)" put after a long name went past the 40 characters a name keeps: saved as it was offered, the
    copy of "Accounts payable team lead for Ontario" was named "Accounts payable team lead for Ontario (", and the
    copy of a 40-character name was refused as a role there already is."""
    account(store, "boss", "admin", "Pat Boss")
    boss = client_of(app_for(store), "10.0.0.7")
    log_in(boss, "boss")
    for name in ("Accounts payable team lead for Ontario", "Accounts payable team leads for Ontario A"[:40]):
        key = _made(boss, name, ["view"])
        offered = re.search(r'name="name" value="([^"]*)"', boss.get(f"/admin/roles/new?copy={key}").text).group(1)
        assert len(offered) <= 40 and offered.endswith(" (copy)"), offered
        copy = _made(boss, offered, ["view"])
        assert store.role_info(copy)["name"] == offered
