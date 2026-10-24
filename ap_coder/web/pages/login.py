"""Logging in and out, and changing one's own password, when AP Coder runs as the company's server (D-97).

* ``/login``: username and password. A new session token at every login (one planted before it is worth nothing);
  the cookie ``ap_session`` is HttpOnly, SameSite=Strict, and Secure over HTTPS. Refusals say the same words whether
  or not the username exists (``core/store/users.py``).
* ``/logout``: the session is deleted, the cookie cleared.
* ``/account/password``: a person's own new password; required before anything else while their password is a
  temporary one (a new account, a reset). Their other sessions end.

On a laptop (server mode off) there are no logins: these pages send to the review queue.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ap_coder.core.store.users import ABSOLUTE_SECONDS, LoginRefused
from ap_coder.web import identity
from ap_coder.web.core import (
    done,
    flash_message,
    get_store,
    local_url,
    refuse,
    render,
    reviewer,
    server_mode,
    take_flash,
    templates,
)

router = APIRouter()

HOME = "/review"
NO_LOGINS = "There are no logins on this computer: AP Coder opens for whoever uses it."


def _next(url: str | None) -> str:
    wanted = local_url(url or HOME, HOME)
    return HOME if wanted.split("?", 1)[0] in (identity.LOGIN_PATH, "/logout") else wanted


def _token(request: Request) -> str:
    return request.cookies.get(identity.SESSION_COOKIE) or ""


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(identity.SESSION_COOKIE, token, max_age=ABSOLUTE_SECONDS, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/")  # fmt: skip


@router.api_route(identity.LOGIN_PATH, methods=["GET", "HEAD"], response_class=HTMLResponse)  # HEAD: monitors
def login_page(request: Request, next: str = HOME) -> Response:  # noqa: A002 - the address's own word
    if not server_mode(request):
        return RedirectResponse(HOME, status_code=303)
    if identity.person() is not None:  # logged in already
        return RedirectResponse(_next(next), status_code=303)
    store = get_store(request)
    context = {"next": _next(next), "first_admin": store.user_count() == 0,
               "flashes": take_flash(request.query_params.get("flash")), "title": "Log in"}  # fmt: skip
    response = templates.TemplateResponse(request, "login.html", context)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post(identity.LOGIN_PATH)
def login_save(request: Request, username: str = Form(""), password: str = Form(""),
               next: str = Form(HOME)) -> JSONResponse:  # noqa: A002  # fmt: skip
    if not server_mode(request):
        return refuse(NO_LOGINS)
    if not username.strip() or not password:
        return refuse("Type your username and your password.")
    store = get_store(request)
    ip = request.client.host if request.client else ""
    try:
        user = store.authenticate(username, password, ip)
    except LoginRefused as exc:
        return refuse(str(exc), 401)
    store.end_session(_token(request))  # whatever session this browser had before: never carried over
    token = store.start_session(user["id"], ip, request.headers.get("user-agent", ""))
    wanted = _next(next)
    if user["must_change"]:
        answer = done(request, f"{identity.PASSWORD_PATH}?next={quote(wanted, safe='')}",
                      flash_message("Choose your own password before you carry on: the one you were given is "
                                    "temporary.", "info", icon="key"))  # fmt: skip
    else:
        answer = done(request, wanted)
    set_session_cookie(request, answer, token)
    return answer


@router.get("/logout", response_class=HTMLResponse)
def logout_page(request: Request) -> Response:
    if not server_mode(request):
        return RedirectResponse(HOME, status_code=303)
    return render(request, "logout.html", "", title="Log out")


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    if not server_mode(request):
        return refuse(NO_LOGINS)
    get_store(request).end_session(_token(request), actor=reviewer())
    answer = done(request, identity.LOGIN_PATH, flash_message("You are logged out.", "ok", icon="logout"))
    answer.delete_cookie(identity.SESSION_COOKIE, path="/", httponly=True, samesite="strict",
                         secure=request.url.scheme == "https")  # fmt: skip
    return answer


@router.get(identity.PASSWORD_PATH, response_class=HTMLResponse)
def password_page(request: Request, next: str = HOME) -> Response:  # noqa: A002
    found = identity.person()
    if not server_mode(request) or found is None:
        return RedirectResponse(HOME, status_code=303)
    user = get_store(request).user(found.user_id) or {}
    return render(request, "account_password.html", "", title="Your password", next=_next(next),
                  must_change=bool(user.get("must_change")), username=found.username)  # fmt: skip


@router.post(identity.PASSWORD_PATH)
def password_save(request: Request, current: str = Form(""), new: str = Form(""), again: str = Form(""),
                  next: str = Form(HOME)) -> JSONResponse:  # noqa: A002  # fmt: skip
    found = identity.person()
    if not server_mode(request) or found is None:
        return refuse(NO_LOGINS)
    if new != again:
        return refuse("The two new passwords are not the same: type it twice the same way.")
    try:
        get_store(request).change_password(found.user_id, current, new, keep=_token(request))
    except ValueError as exc:
        return refuse(str(exc))
    return done(
        request,
        _next(next),
        flash_message(
            "Password changed. If you were logged in anywhere else, you are logged out there.", "ok", icon="key"
        ),
    )
