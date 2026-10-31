"""Logging in and out, and changing one's own password, when AP Coder runs as the company's server (D-97).

* ``/login``: username and password. A new session token at every login (one planted before it is worth nothing);
  the cookie ``ap_session`` is HttpOnly, SameSite=Strict, and Secure on the server unless ``--insecure-http`` (also
  behind a proxy that reports plain HTTP, D-101). Refusals say the same words whether or not the username exists
  (``core/store/users.py``); the server's own console (127.0.0.1, no ``--proxy``) is never paused by address.
* ``/logout``: the session is deleted, the cookie cleared.
* ``/account/password``: a person's own new password; required before anything else while their password is a
  temporary one (a new account, a reset). Their other sessions end, and this one gets a new token.

On a laptop (server mode off) there are no logins: these pages send to the review queue.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ap_coder.core.store.users import LoginRefused
from ap_coder.web import identity
from ap_coder.web.core import (
    done,
    flash_message,
    get_store,
    local_url,
    page_router,
    refuse,
    render,
    reviewer,
    server_mode,
    take_flash,
    templates,
)

router = page_router()  # each plain handler: one connection a request

HOME = "/review"
NO_LOGINS = "There are no logins on this computer: AP Coder opens for whoever uses it."


def _next(url: str | None) -> str:
    wanted = local_url(url or HOME, HOME)
    return HOME if wanted.split("?", 1)[0] in (identity.LOGIN_PATH, "/logout") else wanted


def _token(request: Request) -> str:
    """The session cookie, read as the login middleware reads it (two of them: none, D-101)."""
    return identity.session_token(request.scope.get("headers") or [], identity.session_cookie(secure_cookie(request)))


def secure_cookie(request: Request) -> bool:
    """Whether the session cookie is sent over HTTPS only: always on the server, unless ``--insecure-http`` (a proxy
    in front may end HTTPS and pass the request on as plain HTTP); on a laptop, over HTTPS."""
    if server_mode(request):
        return not getattr(request.app.state, "insecure_http", False)
    return request.url.scheme == "https"


def from_console(request: Request) -> bool:
    """The server's own console: a loopback address with no ``--proxy`` (behind a proxy on this server, everyone
    arrives from a loopback address)."""
    host = request.client.host if request.client else ""
    ip = identity._ip(host)
    return ip is not None and ip.is_loopback and not getattr(request.app.state, "proxy", None)


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    # A browser-session cookie (no lifetime): closing the browser ends it; the server ends the session anyway after
    # its idle time or its hours (D-109).
    secure = secure_cookie(request)
    response.set_cookie(identity.session_cookie(secure), token, httponly=True, samesite="strict", secure=secure,
                        path="/")  # fmt: skip


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
        user = store.authenticate(username, password, ip, console=from_console(request))
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
    secure = secure_cookie(request)
    answer.delete_cookie(identity.session_cookie(secure), path="/", httponly=True, samesite="strict", secure=secure)
    return answer


SESSIONS_PATH = "/account/sessions/end-others"


@router.get(identity.PASSWORD_PATH, response_class=HTMLResponse)
def password_page(request: Request, next: str = HOME) -> Response:  # noqa: A002
    found = identity.person()
    if not server_mode(request) or found is None:
        return RedirectResponse(HOME, status_code=303)
    store = get_store(request)
    user = store.user(found.user_id) or {}
    return render(request, "account_password.html", "", title="Your password", next=_next(next),
                  must_change=bool(user.get("must_change")), username=found.username,
                  others=store.other_sessions(found.user_id, _token(request)))  # fmt: skip


@router.post(identity.PASSWORD_PATH)
def password_save(request: Request, current: str = Form(""), new: str = Form(""), again: str = Form(""),
                  next: str = Form(HOME)) -> JSONResponse:  # noqa: A002  # fmt: skip
    found = identity.person()
    if not server_mode(request) or found is None:
        return refuse(NO_LOGINS)
    if new != again:
        return refuse("The two new passwords are not the same: type it twice the same way.")
    try:
        ip = request.client.host if request.client else ""
        fresh = get_store(request).change_password(found.user_id, current, new, keep=_token(request), ip=ip)
    except ValueError as exc:
        return refuse(str(exc))
    answer = done(
        request,
        _next(next),
        flash_message(
            "Password changed. If you were logged in anywhere else, you are logged out there.", "ok", icon="key"
        ),
    )
    if fresh:  # this browser goes on under a new token (D-101)
        set_session_cookie(request, answer, fresh)
    return answer


@router.post(SESSIONS_PATH)
def end_other_sessions(request: Request) -> JSONResponse:
    """Log out everywhere else: every other session of this account ends; this browser's goes on (D-109)."""
    found = identity.person()
    if not server_mode(request) or found is None:
        return refuse(NO_LOGINS)
    ended = get_store(request).end_sessions(found.user_id, keep=_token(request))
    said = f"Logged out on {ended} other computer{'s' if ended != 1 else ''} or browser{'s' if ended != 1 else ''}."
    return done(request, identity.PASSWORD_PATH, flash_message(said if ended else "You were logged in nowhere else.",
                                                               "ok", icon="logout"))  # fmt: skip
