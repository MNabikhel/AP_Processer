"""Logging in and out, and changing one's own password, when AP Coder runs as the company's server (D-97).

* ``/login``: username and password, the first of two steps (D-146). Refusals say the same words whether or not the
  username exists (``core/store/users.py``); the server's own console (127.0.0.1, no ``--proxy``) is never paused by
  address. A right password starts a login half way (``store.start_pending``): its own cookie (``ap_pending``), which
  opens nothing but the steps after it, each checking the login is at its own step:
  * ``/login/new-password``: a temporary password (a new account, a reset) changed first;
  * ``/login/authenticator``: the authenticator app set up, at the first login (or after an Admin reset it): the QR
    code of its ``otpauth://`` address and its key as text, then a code from it to confirm;
  * ``/login/code``: the code at every other login.
  * ``/login/first``: the first sign-in (D-243). While the database has no account, the username admin and the
    password admin, typed in a browser on the computer running AP Coder (``first_sign_in_here``), lead here: the
    person's name and their own password make the first Admin, whose authenticator app is then set up as anyone's.
    admin / admin is never a password kept, and opens nothing once an account exists; from another computer it is
    refused before anything is checked or counted (no one there can pause the first sign-in).
  Only once the code is right does the login get a session, under a new token (one planted before it is worth
  nothing); the cookie ``ap_session`` is HttpOnly, SameSite=Strict, and Secure on the server unless
  ``--insecure-http`` (also behind a proxy that reports plain HTTP, D-101).
* ``/logout``: the session is deleted, the cookie cleared.
* ``/account/password``: a person's own new password; required before anything else while their password is a
  temporary one (a new account, a reset). Their other sessions end, and this one gets a new token.

On a laptop whose Admin signs in (D-243, ``core.signs_in``) the same pages serve them, from this computer only
(web/identity.py), and only an Admin's account; on any other laptop there are no logins: these pages send to the
review queue.
"""

from __future__ import annotations

import hmac
from typing import Any
from urllib.parse import quote

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from markupsafe import Markup

from ap_coder.core import authenticator
from ap_coder.core.store.users import (
    ACCOUNT_PAUSED,
    ADMIN,
    FIRST_HERE_ONLY,
    FIRST_PASSWORD,
    FIRST_USERNAME,
    KEY_MISSING,
    LOCKED,
    MANAGER,
    SETUP_CODE_WHY,
    SETUP_CODE_WRONG,
    TEMPORARY_EXPIRED,
    LoginRefused,
    fold_username,
)
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
    signs_in,
    take_flash,
    templates,
    with_flash,
)
from ap_coder.web.qrcodegen import QrCode

router = page_router()  # each plain handler: one connection a request

HOME = "/review"
NO_LOGINS = "There are no logins on this computer: AP Coder opens for whoever uses it."
TOO_LONG = "Your login took too long, or it ended: log in again."
OUT_OF_STEP = "That isn't the next step of your login: reload the page."
SIX_DIGITS = "Type the six digits your authenticator app shows (spaces don't matter)."
ONLY_ADMIN = ("Your temporary password has expired (it lasts 3 days). You are the only Admin: for a new one, run "
              "python -m ap_coder users reset {username} {where}.")  # fmt: skip
QUIET = 4  # modules of white around a QR code, as the standard asks
# What a reverse proxy adds to the requests it passes on: one on this computer makes every browser arrive from 127.0.0.1
# (D-243). IIS's ARR adds X-ARR-SSL, X-ARR-LOG-ID and the like too (``X_ARR``).
FORWARDING = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host", "x-forwarded-proto", "via",
              "x-original-url", "x-original-host", "x-client-ip", "true-client-ip", "cf-connecting-ip",
              "x-forwarded-server", "x-cluster-client-ip", "fastly-client-ip")  # fmt: skip
X_ARR = "x-arr-"
LAPTOP_ADMIN_ONLY = ("On this laptop AP Coder opens for its Admin only. To let colleagues in with their own accounts, "
                     "its owner runs python -m ap_coder share (docs/SERVER.md, section 16).")  # fmt: skip
NAME_FIRST = "Type your name, as it is written on your approvals: AP Coder shows it on each one."


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


def _loopback_name(host: str) -> bool:
    """Whether a Host header names this computer: localhost or a loopback address, with or without its port."""
    name = host.strip().lower()
    if name.startswith("["):
        name = name[1:].split("]", 1)[0]
    elif name.count(":") == 1:
        name = name.split(":", 1)[0]
    ip = identity._ip(name)
    return name == "localhost" or (ip is not None and ip.is_loopback)


def _here_only(request: Request) -> str:
    """``FIRST_HERE_ONLY``, with the address to open on the computer running AP Coder."""
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return f"{FIRST_HERE_ONLY} On that computer, open {request.url.scheme}://127.0.0.1:{port}."


def first_sign_in_here(request: Request) -> bool:
    """Whether admin / admin may start the first sign-in from this request (D-243): a browser on the computer running
    AP Coder, opened at its own loopback name, with no reverse proxy between (no ``--proxy``, nothing a proxy adds).
    On a laptop listening on its own network address only (``AP_DASHBOARD_ADDRESS``), also its own browser there: the
    connection from that same address, opened at it (``identity.from_this_computer``)."""
    if getattr(request.app.state, "proxy", None):
        return False
    if any(k in FORWARDING or k.startswith(X_ARR) for k in (key.lower() for key in request.headers.keys())):
        return False
    if from_console(request) and _loopback_name(request.headers.get("host", "")):
        return True
    own = (request.scope.get("server") or ("", 0))[0] or ""
    host = request.headers.get("host", "").strip().lower()
    named = host.rsplit(":", 1)[0].strip("[]") if host.count(":") <= 1 or host.startswith("[") else host
    same = identity._ip(named) is not None and identity._ip(named) == identity._ip(own)  # (``::ffff:`` on dual stack)
    return not server_mode(request) and identity.from_this_computer(request.scope) and (same or named == own.lower())


def _pending(request: Request) -> str:
    """The cookie of this browser's login half way (D-146), read as the session cookie is."""
    return identity.session_token(request.scope.get("headers") or [], identity.pending_cookie(secure_cookie(request)))


def set_pending_cookie(request: Request, response: Response, token: str) -> None:
    secure = secure_cookie(request)
    response.set_cookie(identity.pending_cookie(secure), token, httponly=True, samesite="strict", secure=secure,
                        path="/")  # fmt: skip


def clear_pending_cookie(request: Request, response: Response) -> None:
    secure = secure_cookie(request)
    response.delete_cookie(identity.pending_cookie(secure), path="/", httponly=True, samesite="strict", secure=secure)


def _step(user: dict) -> str:
    """The next step of a login half way: a temporary password changed, the authenticator app set up, or its code."""
    if user["must_change"]:
        return identity.NEW_PASSWORD_PATH
    return identity.CODE_PATH if user["totp_on"] else identity.SET_UP_PATH


def _step_url(user: dict, wanted: str) -> str:
    return f"{_step(user)}?next={quote(wanted, safe='')}"


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    # A browser-session cookie (no lifetime): closing the browser ends it; the server ends the session anyway after
    # its idle time or its hours (D-109).
    secure = secure_cookie(request)
    response.set_cookie(identity.session_cookie(secure), token, httponly=True, samesite="strict", secure=secure,
                        path="/")  # fmt: skip


@router.api_route(identity.LOGIN_PATH, methods=["GET", "HEAD"], response_class=HTMLResponse)  # HEAD: monitors
def login_page(request: Request, next: str = HOME) -> Response:  # noqa: A002 - the address's own word
    if not signs_in(request):
        return RedirectResponse(HOME, status_code=303)
    if identity.signed_in() is not None:  # logged in already
        return RedirectResponse(_next(next), status_code=303)
    store = get_store(request)
    first = store.user_count() == 0
    context = {"next": _next(next), "first_admin": first, "first_here": first and first_sign_in_here(request),
               "server_mode": server_mode(request),
               "flashes": take_flash(request.query_params.get("flash")), "title": "Log in"}  # fmt: skip
    response = templates.TemplateResponse(request, "login.html", context)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post(identity.LOGIN_PATH)
def login_save(request: Request, username: str = Form(""), password: str = Form(""),
               next: str = Form(HOME)) -> JSONResponse:  # noqa: A002  # fmt: skip
    if not signs_in(request):
        return refuse(NO_LOGINS)
    if not username.strip() or not password:
        return refuse("Type your username and your password.")
    store = get_store(request)
    if fold_username(username) == FIRST_USERNAME and store.user_count() == 0:
        first = _first_sign_in(request, password, next)
        if first is not None:
            return first
    ip = request.client.host if request.client else ""
    try:
        user = store.authenticate(username, password, ip, console=from_console(request))
    except LoginRefused as exc:
        return refuse(_refusal(store, str(exc), username, server_mode(request)), 401)
    if not server_mode(request) and user["role"] != ADMIN:  # a laptop opens for its Admin (D-243)
        return refuse(LAPTOP_ADMIN_ONLY, 403)
    store.end_session(_token(request))  # whatever session this browser had before: never carried over
    store.end_pending(_pending(request))  # nor a login half way
    pending = store.start_pending(user["id"])  # the password alone is no session (D-146)
    wanted = _next(next)
    if user.get("role") == MANAGER and wanted == HOME:
        wanted = "/manager"  # a Manager's own page: the review queue is not theirs (D-138)
    if user["must_change"]:
        answer = done(request, _step_url(user, wanted),
                      flash_message("Choose your own password before you carry on: the one you were given is "
                                    "temporary.", "info", icon="key"))  # fmt: skip
    else:
        answer = done(request, _step_url(user, wanted))
    set_pending_cookie(request, answer, pending)
    return answer


def _first_sign_in(request: Request, password: str, next: str) -> JSONResponse | None:  # noqa: A002
    """admin / admin while there is no account (D-243): from this computer, the first sign-in starts (its own cookie,
    the login half way's, which opens only ``FIRST_PATH``); from anywhere else, refused before the password is looked
    at or the try counted. None: another password typed here, an ordinary login that doesn't match."""
    if not first_sign_in_here(request):
        return refuse(_here_only(request), 403)
    if not hmac.compare_digest(password.encode("utf-8"), FIRST_PASSWORD.encode("utf-8")):
        return None
    store = get_store(request)
    store.end_session(_token(request))
    store.end_pending(_pending(request))
    answer = done(request, f"{identity.FIRST_PATH}?next={quote(_next(next), safe='')}")
    set_pending_cookie(request, answer, store.start_first_sign_in())
    return answer


# --- The steps after the password (D-146) -----------------------------------------------------------------------------


def _refusal(store: Any, said: str, username: str, server: bool = True) -> str:
    """What a login refused says; to the only active Admin, whose temporary password expired, the command that gives
    a new one: there is no other Admin to ask (B-2525). (Said only once the password matched.)"""
    if said != TEMPORARY_EXPIRED:
        return said
    account = store.find_user(username) or {}
    admins = [u["id"] for u in store.list_users() if u["role"] == ADMIN and u["active"]]
    where = "on the server" if server else "in scripts\\terminal.bat on this computer"
    return ONLY_ADMIN.format(username=account["username"], where=where) if admins == [account.get("id")] else said


def qr_svg(text: str) -> str:
    """``text`` as a QR code (error correction M) in an SVG: one path of black modules on white, with its quiet zone
    (``QUIET`` modules), black on white whatever the page's colours, so a phone reads it."""
    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    size = qr.get_size()
    full = size + 2 * QUIET
    modules = "".join(f"M{x + QUIET},{y + QUIET}h1v1h-1z" for y in range(size) for x in range(size)
                      if qr.get_module(x, y))  # fmt: skip
    return (f'<svg class="apc-qr" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {full} {full}" width="232" '
            f'height="232" role="img" aria-label="QR code to scan with your authenticator app" '
            f'shape-rendering="crispEdges"><rect width="{full}" height="{full}" fill="#fff"/>'
            f'<path d="{modules}" fill="#000"/></svg>')  # fmt: skip


def _step_page(request: Request, step: str, next: str) -> Response:  # noqa: A002
    """A step's page, for the login half way at that step; any other goes to its own step, or to the login page."""
    if not signs_in(request):
        return RedirectResponse(HOME, status_code=303)
    store, wanted = get_store(request), _next(next)
    user = store.pending_user(_pending(request))
    if user is None:
        said = [flash_message(TOO_LONG, "info", icon="schedule")] if _pending(request) else []
        return RedirectResponse(with_flash(f"{identity.LOGIN_PATH}?next={quote(wanted, safe='')}", said), 303)
    if _step(user) != step:
        return RedirectResponse(_step_url(user, wanted), status_code=303)
    context = {"step": step, "next": wanted, "username": user["username"], "name": user["name"],
               "flashes": take_flash(request.query_params.get("flash")), "error": "",
               "title": {identity.NEW_PASSWORD_PATH: "Your password", identity.SET_UP_PATH: "Set up your "
                         "authenticator app"}.get(step, "Your code")}  # fmt: skip
    admins = [u["id"] for u in store.list_users() if u["role"] == ADMIN and u["active"]]
    context["only_admin"] = admins == [user["id"]]  # their lost phone: the console's command, no Admin to ask
    if step == identity.SET_UP_PATH:
        try:
            secret = store.authenticator_secret(user["id"])
        except ValueError as exc:  # the key file damaged: never made again over it
            context["error"] = str(exc)
        else:
            uri = authenticator.otpauth_uri(user["username"], secret)
            context.update(uri=uri, qr=Markup(qr_svg(uri)), secret=" ".join(secret[i : i + 4] for i in range(0, 32, 4)))
    response = templates.TemplateResponse(request, "login_step.html", context)
    response.headers["Cache-Control"] = "no-store"  # the secret above all
    return response


def _again(wanted: str) -> str:
    """The login page, coming back to ``wanted`` after it (a login half way that ended, B-1925)."""
    return f"{identity.LOGIN_PATH}?next={quote(_next(wanted), safe='')}"


def _pending_at(request: Request, step: str, wanted: str) -> tuple[dict | None, Response | None]:
    """The account of the login half way at ``step``; else the refusal to send (one that ended: to the login page,
    coming back to ``wanted`` after it)."""
    if not signs_in(request):
        return None, refuse(NO_LOGINS)
    user = get_store(request).pending_user(_pending(request))
    if user is None:
        again = with_flash(_again(wanted), [flash_message(TOO_LONG, "info", icon="schedule")])
        return None, refuse(TOO_LONG, 401, redirect=again)
    if _step(user) != step:
        return None, refuse(OUT_OF_STEP)
    return user, None


def _back_to_login(request: Request, text: str, wanted: str) -> JSONResponse:
    """The login half way ended (logins paused, the server's key missing): said on the login page, which comes back to
    ``wanted`` after it."""
    get_store(request).end_pending(_pending(request))
    answer = refuse(text, 401, redirect=with_flash(_again(wanted), [flash_message(text, "err", icon="lock")]))
    clear_pending_cookie(request, answer)
    return answer


@router.get(identity.NEW_PASSWORD_PATH, response_class=HTMLResponse)
def new_password_page(request: Request, next: str = HOME) -> Response:  # noqa: A002
    return _step_page(request, identity.NEW_PASSWORD_PATH, next)


@router.post(identity.NEW_PASSWORD_PATH)
def new_password_save(request: Request, current: str = Form(""), new: str = Form(""), again: str = Form(""),
                      next: str = Form(HOME)) -> Response:  # noqa: A002  # fmt: skip
    user, refusal = _pending_at(request, identity.NEW_PASSWORD_PATH, next)
    if user is None:
        return refusal
    if new != again:
        return refuse("The two new passwords are not the same: type it twice the same way.")
    store = get_store(request)
    try:
        store.change_password(user["id"], current, new, ip=request.client.host if request.client else "")
    except ValueError as exc:
        if store.pending_user(_pending(request)) is None:  # the 5th that didn't match: logins paused
            return _back_to_login(request, str(exc), next)
        return refuse(str(exc))
    after = store.pending_user(_pending(request)) or user
    return done(request, _step_url(after, _next(next)), flash_message("Password changed.", "ok", icon="key"))


@router.get(identity.SET_UP_PATH, response_class=HTMLResponse)
def set_up_page(request: Request, next: str = HOME) -> Response:  # noqa: A002
    return _step_page(request, identity.SET_UP_PATH, next)


@router.post(identity.SET_UP_PATH)
def set_up_save(request: Request, code: str = Form(""), next: str = Form(HOME)) -> Response:  # noqa: A002
    return _second_step(request, identity.SET_UP_PATH, code, next)


@router.get(identity.CODE_PATH, response_class=HTMLResponse)
def code_page(request: Request, next: str = HOME) -> Response:  # noqa: A002
    return _step_page(request, identity.CODE_PATH, next)


@router.post(identity.CODE_PATH)
def code_save(request: Request, code: str = Form(""), next: str = Form(HOME)) -> Response:  # noqa: A002
    return _second_step(request, identity.CODE_PATH, code, next)


@router.get(identity.FIRST_PATH, response_class=HTMLResponse)
def first_page(request: Request, next: str = HOME) -> Response:  # noqa: A002
    """The first sign-in's page (D-243): the first Admin's name and own password, after admin / admin."""
    if not signs_in(request):
        return RedirectResponse(HOME, status_code=303)
    store, wanted = get_store(request), _next(next)
    if not (first_sign_in_here(request) and store.user_count() == 0 and store.first_sign_in_open(_pending(request))):
        return RedirectResponse(_again(wanted), status_code=303)
    name = "" if server_mode(request) else reviewer()  # a laptop's: the name on its approvals so far
    error = ""
    if server_mode(request):
        store.first_setup_code()  # (made at the server's start; here too, should it not be there)
        error = store.setup_code_problem()
    context = {"step": identity.FIRST_PATH, "next": wanted, "username": FIRST_USERNAME, "name": name, "error": error,
               "needs_code": server_mode(request),
               "flashes": take_flash(request.query_params.get("flash")),
               "title": "Make this account yours"}  # fmt: skip
    response = templates.TemplateResponse(request, "login_step.html", context)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post(identity.FIRST_PATH)
def first_save(request: Request, name: str = Form(""), new: str = Form(""), again: str = Form(""),
               code: str = Form(""), next: str = Form(HOME)) -> JSONResponse:  # noqa: A002  # fmt: skip
    """The first Admin made, with the name and password typed (``add_first_admin``: only while there is no account);
    the first sign-in goes on as a login half way, to the authenticator app's set-up. On the server, with its setup
    code too (``users.SETUP_CODE_FILE``): no address proves the person is at the server (a forwarder on it)."""
    if not signs_in(request):
        return refuse(NO_LOGINS)
    if not first_sign_in_here(request):
        return refuse(_here_only(request), 403)
    store, token = get_store(request), _pending(request)
    if not store.first_sign_in_open(token):
        again = with_flash(_again(next), [flash_message(TOO_LONG, "info", icon="schedule")])
        return refuse(TOO_LONG, 401, redirect=again)
    # (an account made meanwhile: refused below, by ``add_first_admin``'s FIRST_DONE)
    if server_mode(request) and store.user_count() == 0 and not store.setup_code_matches(code):
        store.log_event("login_failed", detail={"username": FIRST_USERNAME, "why": SETUP_CODE_WHY})
        return refuse(store.setup_code_problem() or SETUP_CODE_WRONG, 403)
    if new != again:
        return refuse("The two new passwords are not the same: type it twice the same way.")
    shown = identity.clean_name(name)
    if not shown:
        return refuse(NAME_FIRST)
    try:
        user_id = store.add_first_admin(new, shown)
    except ValueError as exc:
        return refuse(str(exc))
    store.end_first_sign_in(token)
    answer = done(request, f"{identity.SET_UP_PATH}?next={quote(_next(next), safe='')}",
                  flash_message("Password saved. Now set up your authenticator app.", "ok", icon="key"))  # fmt: skip
    set_pending_cookie(request, answer, store.start_pending(user_id))
    return answer


def _second_step(request: Request, step: str, code: str, next: str) -> Response:  # noqa: A002
    """The code (setting the app up, or at a login): right, the login gets its session, under a new token."""
    user, refusal = _pending_at(request, step, next)
    if user is None:
        return refusal
    typed = "".join(code.split())
    if len(typed) != authenticator.DIGITS or not typed.isascii() or not typed.isdigit():
        return refuse(SIX_DIGITS)  # a slip of the finger: not a try
    store = get_store(request)
    ip = request.client.host if request.client else ""
    try:
        found = store.check_code(user["id"], typed, ip, console=from_console(request))
    except LoginRefused as exc:
        if str(exc) in (LOCKED, ACCOUNT_PAUSED, KEY_MISSING):
            return _back_to_login(request, str(exc), next)
        return refuse(str(exc), 401)
    store.end_pending(_pending(request))
    token = store.start_session(found["id"], ip, request.headers.get("user-agent", ""))
    said = []
    if step == identity.SET_UP_PATH:
        said = [flash_message("Your authenticator app is set up: AP Coder asks for a code from it each time you log "
                              "in.", "ok", icon="verified_user")]  # fmt: skip
    answer = done(request, _next(next), *said)
    set_session_cookie(request, answer, token)
    clear_pending_cookie(request, answer)
    return answer


@router.get("/logout", response_class=HTMLResponse)
def logout_page(request: Request) -> Response:
    if not signs_in(request):
        return RedirectResponse(HOME, status_code=303)
    return render(request, "logout.html", "", title="Log out")


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    if not signs_in(request):
        return refuse(NO_LOGINS)
    get_store(request).end_session(_token(request), actor=reviewer())
    answer = done(request, identity.LOGIN_PATH, flash_message("You are logged out.", "ok", icon="logout"))
    secure = secure_cookie(request)
    answer.delete_cookie(identity.session_cookie(secure), path="/", httponly=True, samesite="strict", secure=secure)
    return answer


SESSIONS_PATH = "/account/sessions/end-others"


@router.get(identity.PASSWORD_PATH, response_class=HTMLResponse)
def password_page(request: Request, next: str = HOME) -> Response:  # noqa: A002
    found = identity.signed_in()
    if not signs_in(request) or found is None:
        return RedirectResponse(HOME, status_code=303)
    store = get_store(request)
    user = store.user(found.user_id) or {}
    return render(request, "account_password.html", "", title="Your password", next=_next(next),
                  must_change=bool(user.get("must_change")), username=found.username,
                  others=store.other_sessions(found.user_id, _token(request)))  # fmt: skip


@router.post(identity.PASSWORD_PATH)
def password_save(request: Request, current: str = Form(""), new: str = Form(""), again: str = Form(""),
                  next: str = Form(HOME)) -> JSONResponse:  # noqa: A002  # fmt: skip
    found = identity.signed_in()
    if not signs_in(request) or found is None:
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
    found = identity.signed_in()
    if not signs_in(request) or found is None:
        return refuse(NO_LOGINS)
    ended = get_store(request).end_sessions(found.user_id, keep=_token(request))
    said = f"Logged out on {ended} other computer{'s' if ended != 1 else ''} or browser{'s' if ended != 1 else ''}."
    return done(request, identity.PASSWORD_PATH, flash_message(said if ended else "You were logged in nowhere else.",
                                                               "ok", icon="logout"))  # fmt: skip
