"""Users (/admin/users), for an Admin when AP Coder runs as the company's server (D-97): who can log in, with which
role; add a person (with a temporary password shown once), reset a password, change a role, turn an account off or
back on. The last active Admin can't be demoted or turned off (``core/store/users.py``). Every change is in the
Activity log.

A temporary password is never put in a message kept for the next page or in the database: it waits in this
process's memory under a one-off key, for the Admin who made it, and is shown once.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ap_coder.core.store.users import ROLE_LABELS, ROLES
from ap_coder.web import identity
from ap_coder.web.core import done, flash_message, get_store, refuse, render, reviewer, server_mode

router = APIRouter()

PATH = "/admin/users"
SHOWN_FOR = 600  # seconds a temporary password waits to be shown
OWN_RESET = (
    "This is your own account: change your password on the Your password page (/account/password). A reset here "
    "would log you out before its temporary password is shown."
)
_waiting: dict[str, tuple[float, int, str, str]] = {}  # key -> (when, the Admin's id, username, password)
_lock = threading.Lock()


def _keep(username: str, password: str) -> str:
    found = identity.person()
    key = secrets.token_urlsafe(12)
    now = time.monotonic()
    with _lock:
        for old in [k for k, (at, *_rest) in _waiting.items() if now - at > SHOWN_FOR]:
            del _waiting[old]
        _waiting[key] = (now, found.user_id if found else 0, username, password)
    return key


def _take(key: str | None) -> dict[str, str] | None:
    found = identity.person()
    if not key or found is None:
        return None
    with _lock:
        kept = _waiting.get(key)
        if kept is None or kept[1] != found.user_id or time.monotonic() - kept[0] > SHOWN_FOR:
            return None
        del _waiting[key]
    return {"username": kept[2], "password": kept[3]}


def _server_only(request: Request) -> None:
    if not server_mode(request):
        raise HTTPException(404, "Users and logins are only for AP Coder on a company server (server mode).")


def _user(request: Request, user_id: int) -> dict[str, Any]:
    found = get_store(request).user(user_id)
    if found is None:
        raise HTTPException(404, "There is no such account.")
    return found


@router.get(PATH, response_class=HTMLResponse)
def users_page(request: Request, shown: str = "") -> HTMLResponse:
    _server_only(request)
    me = identity.person()
    return render(request, "admin_users.html", "", title="Users", users=get_store(request).list_users(),
                  roles=[(r, ROLE_LABELS[r]) for r in ROLES], temporary=_take(shown),
                  paused=[{**p, "until": _clock_text(p["until"])} for p in get_store(request).paused_addresses()],
                  me=me.user_id if me else 0)  # fmt: skip


@router.post(f"{PATH}/add")
def add_user(
    request: Request, username: str = Form(""), name: str = Form(""), role: str = Form("clerk")
) -> JSONResponse:
    _server_only(request)
    store = get_store(request)
    from ap_coder.core.store.users import fold_username, temporary_password

    password = temporary_password()
    try:
        store.add_user(username, password, role=role, display_name=name, actor=reviewer())
    except ValueError as exc:
        return refuse(str(exc))
    key = _keep(fold_username(username), password)
    return done(request, f"{PATH}?shown={key}", flash_message(f"Account {fold_username(username)} added.", "ok"))


@router.post(PATH + "/{user_id:int}/reset")
def reset(request: Request, user_id: int) -> JSONResponse:
    _server_only(request)
    found = _user(request, user_id)
    me = identity.person()
    if me is not None and me.user_id == found["id"]:  # it would log you out before the password is shown (D-101)
        return refuse(OWN_RESET)
    password = get_store(request).reset_password(user_id, actor=reviewer())
    key = _keep(found["username"], password)
    said = f"Password of {found['username']} reset: they are logged out everywhere."
    return done(request, f"{PATH}?shown={key}", flash_message(said, "ok"))


@router.post(PATH + "/addresses/unlock")
def unlock_address(request: Request, address: str = Form("")) -> JSONResponse:
    """Lift the pause of an address (30 tries that didn't match from it: a shared NAT, RDS or proxy address)."""
    _server_only(request)
    if not get_store(request).unlock_address(address, actor=reviewer()):
        return refuse(f"Logins from {address or 'that address'} are not paused.")
    return done(request, PATH, f"Logins from {address} are no longer paused.")


def _clock_text(epoch: float) -> str:
    return time.strftime("%H:%M", time.localtime(epoch))


@router.post(PATH + "/{user_id:int}/role")
def change_role(request: Request, user_id: int, role: str = Form("")) -> JSONResponse:
    _server_only(request)
    found = _user(request, user_id)
    try:
        get_store(request).set_role(user_id, role, actor=reviewer())
    except ValueError as exc:
        return refuse(str(exc))
    return done(request, PATH, f"{found['username']} is now {ROLE_LABELS.get(role, role)}.")


@router.post(PATH + "/{user_id:int}/deactivate")
def deactivate(request: Request, user_id: int) -> JSONResponse:
    _server_only(request)
    found = _user(request, user_id)
    try:
        get_store(request).deactivate_user(user_id, actor=reviewer())
    except ValueError as exc:
        return refuse(str(exc))
    return done(request, PATH, f"{found['username']} can no longer log in (logged out everywhere).")


@router.post(PATH + "/{user_id:int}/activate")
def activate(request: Request, user_id: int) -> JSONResponse:
    _server_only(request)
    found = _user(request, user_id)
    get_store(request).activate_user(user_id, actor=reviewer())
    return done(request, PATH, f"{found['username']} can log in again.")
