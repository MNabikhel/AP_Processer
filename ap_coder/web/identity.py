"""Who is using the dashboard, when it is shared on the office network (W2-02, docs/decisions.md D-34).

On this computer (the default, 127.0.0.1) the person is the one logged in here, as always: ``core.reviewer()`` and
``core.login()`` (also when its browser opens AP Coder at this computer's own network address: ``from_this_computer``).
When AP Coder listens on the network (``AP_DASHBOARD_ADDRESS=0.0.0.0``), a browser on another computer gives its
name once, on the "Who are you?" page (``pages/who.py``). The name is kept in a cookie the server signs with a secret
made when it starts, so it cannot be made up or changed in the browser; after a restart each colleague gives their
name again. For each request, ``Identity`` (ASGI middleware) puts the person in ``PERSON``:

* ``core.reviewer()`` is then their name, and ``core.login()`` ``network <their address>``, so a second approval
  from the same computer as the first is refused, whatever name it gives;
* every event the store records names them (``actor``) and their address (``detail["address"]``,
  ``store.EVENT_ORIGIN``);
* the review screen keeps their unsaved edits apart (``pages/invoice_drafts.py``).

A browser on another computer without a valid cookie sees only the "Who are you?" page (a page asked for is sent
there; an action is refused), the styles and scripts, and ``/healthz``.

**Server mode** (``AP_SERVER_MODE=1``, D-97) replaces all of this: everyone logs in with their own account
(``pages/login.py``), whichever computer they use, the server's own included (no address is "this computer" for
what may be done). The session cookie (``session_cookie``) holds a token whose SHA-256 the store keeps
(``core/store/users.py``); ``PERSON`` is then the account (``user_id``, ``role``), so approvals name the person and
record ``user:<id>`` as their login, and what each request may do is the role's (``web/roles.py``, D-98): no
session, the login page (a page) or a 401 (an action); a password to change first, the password page; a role that
may not, a 403 page (or JSON for an action). A login half way (the password right, the authenticator's code still to
come, D-146) is no session: its own cookie (``pending_cookie``) is read only by the login's next steps.

**Sign-in on a laptop** (D-243, its database's ``sign_in`` setting): its Admin signs in as on the server, and only
from this computer's own browser; another computer gets neither the "Who are you?" page nor the login (sharing with
colleagues is ``share``). Signing in only guards the door: ``PERSON`` stays None, so the person is this computer's
own as before (their saved name, the computer's login, their drafts); the account is ``LAPTOP_ACCOUNT``
(``signed_in``), for the login's own pages and the rail's foot.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import contextvars
import hashlib
import hmac
import ipaddress
import re
import secrets
import unicodedata
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from ap_coder.core.store import EVENT_ACTOR, EVENT_ORIGIN
from ap_coder.web import security

COOKIE = "ap_coder_person"
MAX_NAME = 60  # characters kept of a name
WHO_PATH = "/who"
OPEN_PATHS = ("/healthz", "/readyz", WHO_PATH)  # answered to a browser that has not said who it is
OPEN_PREFIXES = ("/static/", "/fonts/", "/assets/")
# Server mode (D-97): the session cookie, the login page and the page where a password is changed.
SESSION_COOKIE = "__Host-ap_session"  # Secure (HTTPS): a browser takes it only from this host (D-135)
HTTP_SESSION_COOKIE = "ap_session"  # over --insecure-http, where the __Host- prefix isn't allowed
LOGIN_PATH = "/login"
PASSWORD_PATH = "/account/password"
# A login half way (D-146): the password was right, its next steps (a temporary password changed, the authenticator
# app set up, its code) still to come. Its own cookie, never a session: it opens only those steps (pages/login.py).
PENDING_COOKIE = "__Host-ap_pending"
HTTP_PENDING_COOKIE = "ap_pending"  # over --insecure-http
NEW_PASSWORD_PATH = "/login/new-password"
SET_UP_PATH = "/login/authenticator"
CODE_PATH = "/login/code"
FIRST_PATH = "/login/first"  # the first sign-in's own password, after admin / admin (D-243)
LOGIN_FORMS = (LOGIN_PATH, NEW_PASSWORD_PATH, SET_UP_PATH, CODE_PATH, FIRST_PATH)  # the forms sent before a session
# What a page asks by itself while it waits (a job's progress, OvisOCR2 reading the invoice, the self-test, a download
# being made, a bulk approval): no one using it, so these don't keep a session from ending after its idle minutes
# (D-109, B-1580, B-1927).
POLLS = re.compile(r"/(process/status|jobs/[^/]+|review/\d+/reader|settings/reading/self-test|downloads/\d+|"
                   r"review/bulk/status)")  # fmt: skip
SESSION_ENDED = "You are not logged in (or your session ended): log in again."
LAPTOP_HERE_ONLY = (
    "AP Coder on this laptop is used at the laptop itself: its Admin signs in there. To let colleagues in from their "
    "computers, its owner runs python -m ap_coder share (docs/SERVER.md, section 16)."
)
LAPTOP_OPEN = ("/healthz", "/readyz")  # answered to another computer all the same (the launcher, a monitor)
CHANGE_FIRST = "Choose a new password first (Your password), then carry on."
THIS_COMPUTER = {"127.0.0.1", "localhost", "::1"}
# Settings (all of it: the approval limit and rates, Data's backups and restore, the Azure check) are changed only on
# the computer running AP Coder when it is shared on the office network (B-972); on the server, by an Admin's role.
THIS_COMPUTER_ONLY = ("/settings",)
# The setup and its imports, changed only from this computer too (B-1030, B-2429): the account lists, the tax setup,
# the policy notes (under /accounts), the coding rules (/rules), the vendor master's import, JD Edwards' files, the
# purchase orders, the ERP's invoice list (Exports' register) and the past coding (Learning's history and journal):
# what an Admin's role changes on the company server (web/roles.py). Looking at them stays open, and so do the
# statements a colleague compares (a Clerk's on the server). (On the company server the rules are changed by anyone
# who reviews invoices: web/roles.py, D-217.)
SETUP_CHANGES_HERE_ONLY = ("/accounts", "/rules", "/vendors/master", "/jde-files", "/purchase-orders",
                           "/exports/register", "/learning/history", "/learning/journal")  # fmt: skip
SETUP_HERE_ONLY = (
    "The account lists, the tax setup, the coding rules, the vendor master, JD Edwards' files, the purchase orders, "
    "the ERP's invoice list and the past coding are changed only on the computer running AP Coder. Ask the person "
    "at that computer, or the pilot tester."
)
SETTINGS_HERE_ONLY = (
    "Settings are changed only on the computer running AP Coder. Ask the person at that computer, or the pilot "
    "tester. To change your own name, use the Who are you? page (/who)."
)


@dataclass(frozen=True)
class Person:
    """A colleague on another computer: the name they gave, the address their browser came from and the browser's
    own id (kept in its name cookie: the same browser after its computer got a new address). In server mode, the
    person logged in: their account's id, username and role, the name it shows."""

    name: str
    address: str
    browser: str = ""
    user_id: int = 0
    username: str = ""
    role: str = ""


PERSON: contextvars.ContextVar[Person | None] = contextvars.ContextVar("ap_coder_person", default=None)
# The Admin signed in on a laptop (D-243): never ``PERSON``, which would make them a colleague.
LAPTOP_ACCOUNT: contextvars.ContextVar[Person | None] = contextvars.ContextVar("ap_coder_laptop", default=None)


def person() -> Person | None:
    """The colleague behind the request being answered; None on this computer (or outside a request)."""
    return PERSON.get()


def signed_in() -> Person | None:
    """The account signed in for the request being answered: the server's person, or a laptop's Admin (D-243)."""
    found = person()
    return found if found is not None and found.user_id else LAPTOP_ACCOUNT.get()


def kept_person() -> dict[str, Any]:
    """The colleague behind the request (``person``) as work started for them keeps them, to be done as them later
    (``acting_as``); {} on this computer."""
    found = person()
    return {} if found is None else asdict(found)


@contextlib.contextmanager
def acting_as(kept: dict[str, Any]) -> Iterator[None]:
    """Work done outside their request, as the person it was started for (``kept_person``): ``person()`` is them, and
    every event the store records names them and their address, as their requests' do, whether or not their session
    has ended since (a bulk approval, D-185)."""
    found = Person(**kept) if kept else None
    tokens = (
        PERSON.set(found),
        EVENT_ORIGIN.set((found.address or None) if found is not None else None),
        EVENT_ACTOR.set(found.name if found is not None and found.user_id else None),  # (as ``_server`` sets it)
    )
    try:
        yield
    finally:
        PERSON.reset(tokens[0])
        EVENT_ORIGIN.reset(tokens[1])
        EVENT_ACTOR.reset(tokens[2])


def network_login(address: str) -> str:
    """What stands for a computer login for a colleague: their computer's address."""
    return f"network {address}"


def clean_name(text: str, most: int = MAX_NAME) -> str:
    """A name as given, without control or format characters or runs of spaces, at most ``most`` characters (a
    person's: ``MAX_NAME``)."""
    kept = "".join(" " if unicodedata.category(c).startswith(("C", "Z")) else c for c in str(text or ""))
    return " ".join(kept.split())[:most].strip()


def encode_name(name: str) -> str:
    return base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii").rstrip("=")


def _signature(secret: bytes, encoded: str) -> str:
    return hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()


def new_browser() -> str:
    """An id for a browser that gives its name for the first time."""
    return secrets.token_hex(8)


def cookie_value(secret: bytes, name: str, browser: str = "") -> str:
    """``<name, base64>.<browser id>.<HMAC-SHA256 of both>``: only this start of the server could have made it."""
    signed = f"{encode_name(name)}.{browser}" if browser else encode_name(name)
    return f"{signed}.{_signature(secret, signed)}"


def from_cookie(secret: bytes, value: str | None) -> tuple[str, str] | None:
    """(name, browser id) a cookie holds (the id "" in one without), or None when it was not made by this start of
    the server (made up, changed, old)."""
    signed, _, signature = str(value or "").rpartition(".")
    if not signed or not signature or not hmac.compare_digest(signature, _signature(secret, signed)):
        return None
    encoded, _, browser = signed.partition(".")
    try:
        name = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return (clean_name(name), browser) if clean_name(name) else None


_QUOTED_PATH = re.compile(r"""(['"])[^'"]*[\\/]([^'"\\/]*)\1""")  # 'C:\\Users\\x\\a.db', "/home/x/a.db"


def shown_path(path: Any) -> str:
    """A file's path as the browser asking is shown it: in full on this computer, its name alone to a colleague on
    another computer (this computer's folders and user name are not theirs to see, B-979)."""
    text = str(path or "")
    return text if person() is None else text.replace("\\", "/").rsplit("/", 1)[-1]


def without_folders(text: str) -> str:
    """``text`` (an error's message) with each path quoted in it cut to its file name, for a colleague on another
    computer; as it is on this computer. Python's errors quote the file they failed on: the name says which."""
    return text if person() is None else _QUOTED_PATH.sub(lambda m: m.group(1) + m.group(2) + m.group(1), text)


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """An address as an IP, an IPv4-mapped IPv6 one (``::ffff:10.0.0.5``) as its IPv4 address; None for a name."""
    try:
        ip = ipaddress.ip_address(str(host or "").strip().strip("[]"))
    except ValueError:
        return None
    return getattr(ip, "ipv4_mapped", None) or ip


def is_this_computer(host: str, server: str = "") -> bool:
    """Whether a request came from this computer's own browser: a loopback address (also ``::ffff:127.0.0.1``), or,
    given ``server`` (the address the connection arrived on), that same address: the browser of the computer running
    AP Coder opened at its own network address (``http://<its address>:8501``) comes from it, not from a colleague."""
    ip = _ip(host)
    if ip is None:
        return host == "localhost"
    return ip.is_loopback or (bool(server) and ip == _ip(server))


def from_this_computer(scope: dict[str, Any]) -> bool:
    """``is_this_computer`` for an ASGI request: its client against the address it arrived on (``scope["server"]``)."""
    client = (scope.get("client") or ("", 0))[0] or ""
    return is_this_computer(client, (scope.get("server") or ("", 0))[0] or "")


def here_only(path: str, method: str = "GET") -> str:
    """Why a colleague on the office network may not send ``method`` to ``path`` ("" when they may): Settings, all
    of it, and the setup's changes are this computer's (``THIS_COMPUTER_ONLY``, ``SETUP_CHANGES_HERE_ONLY``)."""
    if any(path == p or path.startswith(p + "/") for p in THIS_COMPUTER_ONLY):
        return SETTINGS_HERE_ONLY
    if method not in ("GET", "HEAD") and any(path == p or path.startswith(p + "/") for p in SETUP_CHANGES_HERE_ONLY):
        return SETUP_HERE_ONLY
    return ""


def _cookies(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in headers:
        if key.lower() == b"cookie":
            for part in value.decode("latin-1").split(";"):
                name, _, kept = part.strip().partition("=")
                found.setdefault(name, kept)
    return found


def session_cookie(secure: bool) -> str:
    """The session cookie's name: Secure (the server over HTTPS), ``__Host-ap_session``, which a browser takes only
    from this host, for the path /, never for a Domain, so another host of the company's domain can't plant one
    beside it (D-135); not Secure (``--insecure-http``), ``ap_session``."""
    return SESSION_COOKIE if secure else HTTP_SESSION_COOKIE


def pending_cookie(secure: bool) -> str:
    """The cookie of a login half way (D-146), named as ``session_cookie`` is."""
    return PENDING_COOKIE if secure else HTTP_PENDING_COOKIE


def session_tokens(headers: list[tuple[bytes, bytes]], name: str) -> list[str]:
    """Every session cookie called ``name`` the request carries, in order."""
    found = []
    for key, value in headers:
        if key.lower() == b"cookie":
            for part in value.decode("latin-1").split(";"):
                called, _, kept = part.strip().partition("=")
                if called == name:
                    found.append(kept)
    return found


def session_token(headers: list[tuple[bytes, bytes]], name: str) -> str:
    """The session token of a request, read the same way by the login middleware and the pages: its one session
    cookie called ``name`` (``session_cookie``). Two or more (one planted by another site of the company for a path or
    a parent domain, or left over) are no login at all, never one of them picked (D-101)."""
    found = session_tokens(headers, name)
    return found[0] if len(found) == 1 else ""


def _no_store(send: Any) -> Any:
    """``send`` with every answer marked ``Cache-Control: no-store`` (whatever the page said): on the server, what
    needs a login is never kept by the browser or a proxy, the downloads and the invoices' page images too."""

    async def sending(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            kept = [(k, v) for k, v in message.get("headers") or [] if k.lower() not in (b"cache-control", b"pragma")]
            message["headers"] = [*kept, (b"cache-control", b"no-store")]
        await send(message)

    return sending


class Identity:
    """ASGI middleware: puts the colleague of each request in ``PERSON`` (and their address in the store's
    ``EVENT_ORIGIN``); a browser on another computer that has not said who it is goes to the "Who are you?" page."""

    def __init__(self, app: Any, secret: bytes, address: str = "127.0.0.1", server: bool = False,
                 laptop: bool = False) -> None:  # fmt: skip
        self.app = app
        self.secret = secret
        self.network = str(address or "").strip().strip("[]").lower() not in THIS_COMPUTER
        self.server = bool(server)
        self.laptop = bool(laptop) and not self.server  # its Admin signs in (D-243)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if self.server and scope["type"] == "http":
            await self._server(scope, receive, send)
            return
        if self.laptop and scope["type"] == "http":
            await self._laptop(scope, receive, send)
            return
        client = (scope.get("client") or ("", 0))[0] or ""
        if scope["type"] != "http" or not self.network or from_this_computer(scope):
            await self.app(scope, receive, send)
            return
        path, method = scope.get("path") or "", scope.get("method", "GET")
        if said := here_only(path, method):
            if method in ("GET", "HEAD"):
                page = f"<!doctype html><meta charset=utf-8><title>Settings</title><p>{said}</p>"
                refusal: Any = HTMLResponse(page + '<p><a href="/">Back</a></p>', 403)
            else:
                refusal = JSONResponse({"ok": False, "error": said}, status_code=403)
            refusal.headers["Cache-Control"] = "no-store"
            await refusal(scope, receive, send)
            return
        found = from_cookie(self.secret, _cookies(scope.get("headers") or []).get(COOKIE))
        if found is None:
            if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
                await self.app(scope, receive, send)
                return
            if scope.get("method", "GET") in ("GET", "HEAD"):
                query = (scope.get("query_string") or b"").decode("latin-1")
                wanted = path + (f"?{query}" if query else "")
                response: Any = RedirectResponse(f"{WHO_PATH}?next={quote(wanted, safe='')}", status_code=303)
            else:
                response = JSONResponse(
                    {"ok": False, "error": "Tell AP Coder who you are first (the Who are you? page, /who)."},
                    status_code=403,
                )
            response.headers["Cache-Control"] = "no-store"
            await response(scope, receive, send)
            return
        tokens = PERSON.set(Person(found[0], client, found[1])), EVENT_ORIGIN.set(client)
        try:
            await self.app(scope, receive, send)
        finally:
            PERSON.reset(tokens[0])
            EVENT_ORIGIN.reset(tokens[1])

    async def _laptop(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """A laptop whose Admin signs in (D-243): another computer gets nothing but the styles and the health checks;
        this computer's browser needs the Admin's session for all but the login's own pages (``roles.OPEN``)."""
        from ap_coder.core.store.users import ADMIN
        from ap_coder.web import roles

        path, method = scope.get("path") or "", str(scope.get("method") or "GET").upper()
        page = method in ("GET", "HEAD")
        if path.startswith(OPEN_PREFIXES) or path in LAPTOP_OPEN:
            await self.app(scope, receive, send)
            return
        refusal: Any = None
        user = None
        if self.network and not from_this_computer(scope):
            refusal = _here_only_page() if page else JSONResponse({"ok": False, "error": LAPTOP_HERE_ONLY}, 403)
        else:
            store = getattr(getattr(scope.get("app"), "state", None), "store", None)
            token = session_token(scope.get("headers") or [], session_cookie(scope.get("scheme") == "https"))
            if token and store is not None:
                polled = page and POLLS.fullmatch(path) is not None
                user = await run_in_threadpool(store.session_user, token, renew=not polled)
            user = user if user is not None and user["role"] == ADMIN else None
            if user is None and roles.need_for(scope.get("app"), scope) != roles.OPEN:
                refusal = _to_login(scope, page)
        if refusal is not None:
            refusal.headers["Cache-Control"] = "no-store"
            await refusal(scope, receive, send)
            return
        client = (scope.get("client") or ("", 0))[0] or ""
        token = LAPTOP_ACCOUNT.set(_account_person(user, client) if user is not None else None)
        try:
            await self.app(scope, receive, send)
        finally:
            LAPTOP_ACCOUNT.reset(token)

    async def _server(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Server mode: the account behind the session cookie, and what its role may do (web/roles.py)."""
        from ap_coder.web import roles

        client = (scope.get("client") or ("", 0))[0] or ""
        path, method = scope.get("path") or "", str(scope.get("method") or "GET").upper()
        need = roles.need_for(scope.get("app"), scope)
        user = None
        store = getattr(getattr(scope.get("app"), "state", None), "store", None)
        secure = not getattr(getattr(scope.get("app"), "state", None), "insecure_http", False)  # as login.secure_cookie
        cookie = session_cookie(secure)
        page = method in ("GET", "HEAD")
        if not path.startswith(OPEN_PREFIXES):
            token = session_token(scope.get("headers") or [], cookie)
            if token and store is not None:
                polled = page and POLLS.fullmatch(path) is not None
                user = await run_in_threadpool(store.session_user, token, renew=not polled)
        refusal: Any = None
        if user is None and need != roles.OPEN:
            refusal = _to_login(scope, page)
            if not page and path == "/logout":  # (two cookies, say): the sessions they name end, the cookie cleared
                for named in session_tokens(scope.get("headers") or [], cookie):
                    if named and store is not None:
                        await run_in_threadpool(store.end_session, named)
                refusal.delete_cookie(cookie, path="/", httponly=True, samesite="strict", secure=secure)
        elif user is not None and user["must_change"] and need not in (roles.OPEN, roles.SIGNED_IN):
            if page:
                refusal = RedirectResponse(f"{PASSWORD_PATH}?next={quote(_wanted(scope), safe='')}", status_code=303)
            else:
                refusal = JSONResponse({"ok": False, "error": CHANGE_FIRST}, status_code=403)
        signed_in = _account_person(user, client) if user is not None else None
        if user is not None:  # for the access log, outside this middleware (D-109)
            scope.setdefault("state", {})[security.USER_KEY] = user["username"]
            scope["state"][security.NAME_KEY] = signed_in.name
        tokens = (
            PERSON.set(signed_in),
            EVENT_ORIGIN.set(client or None),
            EVENT_ACTOR.set(signed_in.name if signed_in is not None else None),  # events record who (B-1106)
        )
        try:
            if refusal is None and user is not None and not roles.allowed(user["role"], need):
                refusal = await run_in_threadpool(_not_allowed, scope, receive, user, page)
            if refusal is None and path in LOGIN_FORMS and not page and security.multipart(scope):
                refusal = JSONResponse({"ok": False, "error": security.NO_MULTIPART}, status_code=415)
            if refusal is not None:
                refusal.headers["Cache-Control"] = "no-store"
                await refusal(scope, receive, send)
                return
            # Before a login, a few fields at most; after it, the largest upload (D-109).
            limit = security.OPEN_BODY_LIMIT if user is None else security.BODY_LIMIT
            await security.limit_body(self.app, scope, receive, send if need == roles.OPEN else _no_store(send), limit)
        finally:
            PERSON.reset(tokens[0])
            EVENT_ORIGIN.reset(tokens[1])
            EVENT_ACTOR.reset(tokens[2])


def _to_login(scope: dict[str, Any], page: bool) -> Any:
    """No session: a page goes to the login, coming back after it; an action is refused (``session_ended``: the
    page's script stays on the page and says so, its edits kept, D-185)."""
    if page:
        return RedirectResponse(f"{LOGIN_PATH}?next={quote(_wanted(scope), safe='')}", status_code=303)
    return JSONResponse({"ok": False, "error": SESSION_ENDED, "redirect": LOGIN_PATH, "session_ended": True},
                        status_code=401)  # fmt: skip


def _here_only_page() -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>AP Coder</title><p>{LAPTOP_HERE_ONLY}</p>", 403)


def _wanted(scope: dict[str, Any]) -> str:
    query = (scope.get("query_string") or b"").decode("latin-1")
    return (scope.get("path") or "/") + (f"?{query}" if query else "")


def _account_person(user: dict[str, Any], client: str) -> Person:
    return Person(user.get("name") or user["username"], client, f"user:{user['id']}", int(user["id"]),
                  user["username"], user["role"])  # fmt: skip


def not_allowed_text(role_label: str, page: bool) -> str:
    what = "open this page" if page else "do this"
    return f"Your role ({role_label}) can't {what}. Ask an Admin if you need it."


def _not_allowed(scope: dict[str, Any], receive: Any, user: dict[str, Any], page: bool) -> Any:
    """The 403: AP Coder's own page with the menu (a page asked for), else ``{"ok": false, "error": ...}``."""
    from starlette.requests import Request

    text = not_allowed_text(user.get("role_label") or user["role"], page)
    request = Request(scope, receive)
    if not page or "text/html" not in request.headers.get("accept", ""):
        return JSONResponse({"ok": False, "error": text}, status_code=403)
    try:
        from ap_coder.web import core

        return core.render(request, "error.html", "", status_code=403, title="Not allowed", code=403, detail=text)
    except Exception:  # noqa: BLE001 - the page frame reads the database: a plain page then
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Not allowed</title><p>{text}</p>"
                            '<p><a href="/">Back</a></p>', 403)  # fmt: skip
