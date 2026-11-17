"""Roles (/admin/roles, D-250), on the Admin panel: what each role may do. The five built-in roles are shown as they
are (fixed: a copy of one is a starting point); an Admin makes a role with a name and tick boxes, changes it, and
deletes it while no account has it. A person's role is given on Users or their own page. The rules are the store's
(``core/store/roles.py``): an Admin never takes Manage users and roles from themselves, and there is always an active
account that has it. Every change is in the Activity log.
"""

from __future__ import annotations

from typing import Any

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ap_coder.core.permissions import PERMISSIONS
from ap_coder.web import identity
from ap_coder.web.core import done, get_store, page_router, refuse, render, reviewer
from ap_coder.web.pages import admin, users

router = page_router()  # each plain handler: one connection a request

PATH = "/admin/roles"


def _role(request: Request, key: str) -> dict[str, Any]:
    found = get_store(request).role_info(key)
    if found is None:
        raise HTTPException(404, "There is no such role.")
    return found


@router.get(PATH, response_class=HTMLResponse)
def roles_page(request: Request) -> HTMLResponse:
    users.server_only(request)
    return render(request, "admin_roles.html", "admin", title="Roles", **admin.common(request),
                  roles=get_store(request).all_roles(), permissions=PERMISSIONS)  # fmt: skip


@router.get(PATH + "/new", response_class=HTMLResponse)
def new_role(request: Request, copy: str = "") -> HTMLResponse:
    """A new role's form, its boxes ticked as the role ``copy`` has them (a starting point), else none."""
    users.server_only(request)
    start = get_store(request).role_info(copy) if copy else None
    role = {"key": "", "name": f"{start['name']} (copy)" if start else "", "built_in": False, "people": 0,
            "permissions": start["permissions"] if start else []}  # fmt: skip
    return render(request, "admin_role.html", "admin", title="New role", **admin.common(request), role=role,
                  permissions=PERMISSIONS, copied=start["name"] if start else "")  # fmt: skip


@router.get(PATH + "/{key}", response_class=HTMLResponse)
def role_page(request: Request, key: str) -> HTMLResponse:
    users.server_only(request)
    role = _role(request, key)
    holders = [u for u in get_store(request).list_users() if u["role"] == key]
    return render(request, "admin_role.html", "admin", title=role["name"], **admin.common(request), role=role,
                  permissions=PERMISSIONS, holders=holders, copied="")  # fmt: skip


@router.post(PATH + "/add")
def add_role(request: Request, name: str = Form(""), permissions: list[str] = Form([])) -> JSONResponse:  # noqa: B008
    users.server_only(request)
    try:
        me = identity.person()
        key = get_store(request).add_role(name, permissions, actor=reviewer(), by_user=me.user_id if me else None)
    except ValueError as exc:
        return refuse(str(exc))
    return done(request, f"{PATH}/{key}", "Role made: give it to people on Users, or on their own page.")


@router.post(PATH + "/{key}/save")
def save_role(request: Request, key: str, name: str = Form(""),
              permissions: list[str] = Form([])) -> JSONResponse:  # noqa: B008  # fmt: skip
    users.server_only(request)
    _role(request, key)
    me = identity.person()
    try:
        get_store(request).edit_role(key, name, permissions, actor=reviewer(), by_user=me.user_id if me else None)
    except ValueError as exc:
        return refuse(str(exc))
    return done(request, f"{PATH}/{key}", "Role saved: it applies to everyone who has it, from their next click.")


@router.post(PATH + "/{key}/delete")
def delete_role(request: Request, key: str) -> JSONResponse:
    users.server_only(request)
    role = _role(request, key)
    me = identity.person()
    try:
        get_store(request).delete_role(key, actor=reviewer(), by_user=me.user_id if me else None)
    except ValueError as exc:
        return refuse(str(exc))
    return done(request, PATH, f"Role {role['name']} deleted.")
