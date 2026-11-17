"""The five built-in roles give exactly the access they gave before roles were made of permissions (D-250).

``tests/fixtures/role_access_before.json`` was made by the code before the change (origin/main 2d07def, its
``web/roles.py`` ``allowed`` over every route, ``JOB_NEEDS``, and each check written in the pages), and is never
edited by hand. Here every route of the app, every job and every one of those checks is decided again by the code as
it is now, for each built-in role, and compared."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.permissions import BUILT_IN, CODE, CONFIRM, ROLES, UNDO
from ap_coder.core.store import Store
from ap_coder.web import access, identity, jobs, roles
from ap_coder.web.app import create_app
from ap_coder.web.identity import SESSION_COOKIE
from ap_coder.web.pages import manager
from tests.web.test_roles import app_routes
from tests.webclient import enrol, web_settings

BEFORE = json.loads((Path(__file__).parents[1] / "fixtures" / "role_access_before.json").read_text(encoding="utf-8"))
GOOD = "correct horse battery"
PANEL = {"/admin", "/admin/sign-in", "/admin/users/{user_id:int}", "/admin/users/{user_id:int}/sign-out",
         "/admin/roles", "/admin/roles/new", "/admin/roles/{key}", "/admin/roles/add", "/admin/roles/{key}/save",
         "/admin/roles/{key}/delete"}  # fmt: skip  # the Admin panel's own routes, made since (D-249, D-250)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("permissions") / "ap_coder.db")
    app = create_app(web_settings(), store, start_reader=False, server=True)
    ids = {}
    for role in ROLES:
        ids[role] = store.add_user(f"the.{role}", GOOD, role=role, must_change=False)
        enrol(store, ids[role])
    return app, store, ids


def test_every_route_of_before_is_allowed_to_the_same_built_in_roles(server):
    app, _, _ = server
    routes = set(app_routes(app))
    differ = []
    for route, was in BEFORE["routes"].items():
        method, _, path = route.partition(" ")
        assert (method, path) in routes, f"{route}: gone"
        need = roles.RULES[(method, path)]
        now = [role for role in ROLES if roles.allowed(BUILT_IN[role], need)]
        if now != was["roles"] or (need == roles.OPEN) != was["open"] or (need == roles.SIGNED_IN) != was["signed_in"]:
            differ.append(f"{route}: {was['roles']} before, {now} now")
    assert differ == []
    assert len(BEFORE["routes"]) == 241  # (the fixture is the whole table of before)


def test_the_routes_made_since_are_the_admin_panels_and_an_admins_only(server):
    app, _, _ = server
    added = {f"{m} {p}" for m, p in app_routes(app)} - set(BEFORE["routes"])
    assert {route.partition(" ")[2] for route in added} == PANEL
    for route in added:
        need = roles.RULES[tuple(route.split(" ", 1))]
        assert [role for role in ROLES if roles.allowed(BUILT_IN[role], need)] == ["admin"], route


def test_a_route_without_a_rule_needs_an_admin_as_before():
    assert [role for role in ROLES if roles.allowed(BUILT_IN[role], roles.ADMIN)] == BEFORE["jobs"]["(any other)"]


def test_each_jobs_progress_is_seen_by_the_same_built_in_roles():
    for name, was in BEFORE["jobs"].items():
        need = roles.JOB_NEEDS.get(name, roles.ADMIN)
        assert [role for role in ROLES if roles.allowed(BUILT_IN[role], need)] == was, name


class _Server:
    class app:  # noqa: N801
        class state:  # noqa: N801
            server_mode = True


def _as(store, uid, check):
    user = store.user(uid)
    token = identity.PERSON.set(identity._account_person(user, "10.0.0.5"))
    try:
        return check()
    finally:
        identity.PERSON.reset(token)


def test_each_check_in_the_pages_passes_the_same_built_in_roles(server):
    app, store, ids = server
    client = TestClient(app, base_url="http://localhost", client=("10.0.0.5", 50000), follow_redirects=False)

    def home(role):  # web/app.py "/": a Manager's own requests
        cookie = {"Cookie": f"{SESSION_COOKIE}={store.start_session(ids[role])}"}
        return client.get("/", headers=cookie).headers["location"] == "/manager"

    def counted(role):  # core/store/roles.py: who counts toward the last one, who stays (LAST_ADMIN)
        with store._conn() as conn:
            return store._manages(conn, role)

    now = {
        "is_admin": lambda r: _as(store, ids[r], lambda: access.is_admin(_Server())),
        "may_lift_hold": lambda r: _as(store, ids[r], lambda: access.may_lift_hold(_Server())),
        "bulk_approval_goes_on": lambda r: jobs._may_not(store, {"user_id": ids[r]}) == "",
        "undoes_anyones_approval": lambda r: _as(store, ids[r], lambda: UNDO in identity.person().granted),
        "sees_only_their_own_manager_requests": lambda r: _as(store, ids[r], manager._is_manager),
        "offered_as_a_manager": lambda r: access.confirms(store.user(ids[r])),
        "home_is_the_manager_page": home,
        "makes_the_spend_workbook": lambda r: roles.allowed(BUILT_IN[r], roles.RULES[("POST", "/spend/data")]),
        "counts_as_an_admin": counted,
    }
    assert set(now) == set(BEFORE["checks"])
    for name, was in BEFORE["checks"].items():
        assert [role for role in ROLES if now[name](role)] == was, name


def test_the_permission_sets_of_the_built_in_roles():
    """Spelled out once, so a change to one is a change to this test too."""
    names = {role: sorted(BUILT_IN[role]) for role in ROLES}
    assert names == {
        "readonly": ["activity", "view"],
        "clerk": ["activity", "approve", "code", "export", "import", "view"],
        "approver": ["activity", "approve", "code", "controls", "export", "final_approve", "import", "post", "view"],
        "admin": [
            "activity",
            "approve",
            "code",
            "controls",
            "export",
            "final_approve",
            "import",
            "post",
            "settings",
            "setup",
            "undo",
            "users",
            "view",
        ],  # fmt: skip
        "manager": ["confirm"],
    }
    assert CONFIRM not in BUILT_IN["admin"] and CODE in BUILT_IN["admin"]  # an Admin opens every request as AP does
