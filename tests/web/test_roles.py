"""Every route of the dashboard × every role, in server mode (web/roles.py, D-98).

The routes are walked from the app itself, so a route added without a rule in ``roles.RULES`` fails here (and is
closed to all but an Admin until it gets one). Each request goes through the real ``Identity`` middleware with a real
session; what it lets through reaches a stub instead of the page, so nothing is changed, deleted or started."""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

from ap_coder.core.store import Store
from ap_coder.core.store.users import ROLES
from ap_coder.web import roles
from ap_coder.web.app import create_app
from ap_coder.web.identity import SESSION_COOKIE, Identity
from tests.webclient import PAGE, web_settings

GOOD = "correct horse battery"


def app_routes(app) -> list[tuple[str, str]]:
    found = []
    for route in roles.leaf_routes(app.routes):
        if isinstance(route, APIRoute):
            found += [(method, route.path) for method in sorted(route.methods)]
    return found


def concrete(path: str) -> str:
    """An address the route answers: each {parameter} as 1."""
    return re.sub(r"\{[^}]+\}", "1", path)


class Probe:
    """The Identity middleware of server mode in front of a stub: "passed" when it lets the request through."""

    def __init__(self, app) -> None:
        self.app = app

        async def passed(scope, receive, send):
            await PlainTextResponse("passed")(scope, receive, send)

        self.guard = Identity(passed, secret=b"test", server=True)

    async def __call__(self, scope, receive, send):
        scope["app"] = self.app
        await self.guard(scope, receive, send)


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("roles") / "ap_coder.db")
    app = create_app(web_settings(), store, start_reader=False, server=True)
    tokens = {}
    for role in ROLES:
        uid = store.add_user(f"the.{role}", GOOD, role=role)
        with store._conn() as conn:
            conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
        tokens[role] = store.start_session(uid)
    client = TestClient(Probe(app), base_url="http://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    return app, tokens, client


def test_every_route_has_a_rule_and_every_rule_a_route(setup):
    app, _, _ = setup
    routes = set(app_routes(app))
    assert sorted(routes - set(roles.RULES)) == [], "a route without a rule in web/roles.py RULES"
    assert sorted(set(roles.RULES) - routes) == [], "a rule in web/roles.py RULES for a route that is gone"


def test_a_route_without_a_rule_needs_an_admin(setup, tmp_path):
    app = create_app(web_settings(), Store(tmp_path / "a.db"), start_reader=False, server=True)
    app.add_api_route("/brand-new", lambda: {"ok": True}, methods=["GET", "POST"])
    for method in ("GET", "POST"):
        scope = {"type": "http", "method": method, "path": "/brand-new", "root_path": "", "query_string": b"",
                 "headers": []}  # fmt: skip
        assert roles.need_for(app, scope) == roles.ADMIN
    assert roles.need_for(app, {**scope, "path": "/no/such/page"}) == roles.ADMIN
    assert not roles.allowed("approver", roles.ADMIN) and roles.allowed("admin", roles.ADMIN)


@pytest.mark.parametrize("role", [None, *ROLES])
def test_every_route_for_every_role(setup, role):
    app, tokens, client = setup
    wrong: list[str] = []
    for method, path in app_routes(app):
        need = roles.RULES[(method, path)]
        url = concrete(path)
        scope = {"type": "http", "method": method, "path": url, "root_path": "", "query_string": b"", "headers": []}
        if roles.need_for(app, scope) != need:  # the address must land on its own route's rule
            wrong.append(f"{method} {url}: lands on another route's rule")
            continue
        headers = {**PAGE, "Accept": "text/html"}
        if role is not None:
            headers["Cookie"] = f"{SESSION_COOKIE}={tokens[role]}"
        answer = client.request(method, url, headers=headers)
        if need == roles.OPEN or (role is not None and roles.allowed(role, need)):
            ok = answer.status_code == 200 and answer.text == ("" if method == "HEAD" else "passed")
        elif role is None:
            ok = answer.status_code == (303 if method == "GET" else 401)
            ok = ok and (method != "GET" or answer.headers["location"].startswith("/login?next="))
        else:
            ok = answer.status_code == 403
        if not ok:
            wrong.append(f"{method} {url} ({need}): {answer.status_code}")
    assert wrong == []


def test_what_each_role_may_do(setup):
    """The table as the plan put it, independently of how RULES is written."""
    app, _, _ = setup

    def may(role, route):
        method, _, path = route.partition(" ")
        return roles.can(app, role, method, path)

    for route in app_routes(app):  # Read-only changes nothing (its own password and logout aside)
        if route[0] != "GET" and route[1] not in (
            "/logout",
            "/account/password",
            "/account/sessions/end-others",
            "/login",
            "/who",
            "/shutdown",
        ):
            assert not may("readonly", f"{route[0]} {concrete(route[1])}"), route
        assert may("admin", f"{route[0]} {concrete(route[1])}"), route
    for route in (
        "GET /review",
        "GET /review/1",
        "GET /review/1/page/1.png",
        "GET /exports",
        "GET /help",
        "GET /insights",
        "GET /spend",
        "GET /activity",
        "GET /month-end",
        "GET /account/password",
    ):
        assert may("readonly", route), route
    for route in (
        "POST /process/upload",
        "POST /review/1/approve",
        "POST /review/1/check",
        "POST /review/1/park",
        "POST /review/1/reject",
        "POST /review/1/reopen",
        "POST /review/1/undo-approve",
        "POST /review/bulk-approve",
        "POST /exports/create",
        "GET /exports/batches/1/download",
        "POST /vendors/save",
        "GET /process",
    ):
        assert may("clerk", route) and not may("readonly", route), route
    for route in ("POST /review/1/final-approve",):
        assert may("approver", route) and not may("clerk", route), route
    for route in (
        "GET /settings",
        "POST /settings/review",
        "GET /admin/users",
        "POST /admin/users/add",
        "POST /settings/jde",
        "POST /accounts/gl_accounts/import",
        "POST /vendors/master/import",
        "POST /accounts/tax",
        "POST /settings/data/backup",
        "POST /settings/data/restore",
        "POST /review/1/delete",
        "POST /settings/azure-check",
        "POST /learning/memory/forget",
        "POST /learning/history/forget",
        "GET /settings/data/backups/x.db",
    ):
        assert may("admin", route) and not may("approver", route), route
