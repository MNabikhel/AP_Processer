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
* the review screen keeps their unsaved edits apart (``pages/invoice.py``).

A browser on another computer without a valid cookie sees only the "Who are you?" page (a page asked for is sent
there; an action is refused), the styles and scripts, and ``/healthz``.

**Server mode** (``AP_SERVER_MODE=1``, D-97) replaces all of this: everyone logs in with their own account
(``pages/login.py``), whichever computer they use, the server's own included (no address is "this computer" for
what may be done). The session cookie (``SESSION_COOKIE``) holds a token whose SHA-256 the store keeps
(``core/store/users.py``); ``PERSON`` is then the account (``user_id``, ``role``), so approvals name the person and
record ``user:<id>`` as their login, and what each request may do is the role's (``web/roles.py``, D-98): no
session, the login page (a page) or a 401 (an action); a password to change first, the password page; a role that
may not, a 403 page (or JSON for an action).
"""

from __future__ import annotations

import base64
import binascii
import contextvars
import hashlib
import hmac
import ipaddress
import re
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from ap_coder.core.store import EVENT_ACTOR, EVENT_ORIGIN

COOKIE = "ap_coder_person"
MAX_NAME = 60  # characters kept of a name
WHO_PATH = "/who"
OPEN_PATHS = ("/healthz", WHO_PATH)  # answered to a browser that has not said who it is
OPEN_PREFIXES = ("/static/", "/fonts/", "/assets/")
# Server mode (D-97): the session cookie, the login page and the page where a password is changed.
SESSION_COOKIE = "ap_session"
LOGIN_PATH = "/login"
PASSWORD_PATH = "/account/password"
SESSION_ENDED = "You are not logged in (or your session ended): log in again."
CHANGE_FIRST = "Choose a new password first (Your password), then carry on."
THIS_COMPUTER = {"127.0.0.1", "localhost", "::1"}
# Settings (all of it: the approval limit and rates, Data's backups and restore, the Azure check) are changed only on
# the computer running AP Coder when it is shared on the office network (B-972); logins come later.
THIS_COMPUTER_ONLY = ("/settings",)
# The setup and its imports, changed only from this computer too (B-1030): the account lists, the tax setup, the
# policy notes and coding rules (all under /accounts), and the vendor master's import. Looking at them stays open.
SETUP_CHANGES_HERE_ONLY = ("/accounts", "/vendors/master")
SETUP_HERE_ONLY = (
    "The account lists, the tax setup, the coding rules and the vendor master are changed only on the computer "
    "running AP Coder. Ask the person at that computer, or the pilot tester."
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


def person() -> Person | None:
    """The colleague behind the request being answered; None on this computer (or outside a request)."""
    return PERSON.get()


def network_login(address: str) -> str:
    """What stands for a computer login for a colleague: their computer's address."""
    return f"network {address}"


def clean_name(text: str) -> str:
    """A name as given, without control characters or runs of spaces, at most ``MAX_NAME`` characters."""
    kept = "".join(" " if unicodedata.category(c).startswith(("C", "Z")) else c for c in str(text or ""))
    return " ".join(kept.split())[:MAX_NAME].strip()


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


def name_from_cookie(secret: bytes, value: str | None) -> str | None:
    """The name a cookie holds, or None when it was not made by this start of the server (made up, changed, old)."""
    found = from_cookie(secret, value)
    return found[0] if found else None


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


def _cookies(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in headers:
        if key.lower() == b"cookie":
            for part in value.decode("latin-1").split(";"):
                name, _, kept = part.strip().partition("=")
                found.setdefault(name, kept)
    return found


class Identity:
    """ASGI middleware: puts the colleague of each request in ``PERSON`` (and their address in the store's
    ``EVENT_ORIGIN``); a browser on another computer that has not said who it is goes to the "Who are you?" page."""

    def __init__(self, app: Any, secret: bytes, address: str = "127.0.0.1", server: bool = False) -> None:
        self.app = app
        self.secret = secret
        self.network = str(address or "").strip().strip("[]").lower() not in THIS_COMPUTER
        self.server = bool(server)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if self.server and scope["type"] == "http":
            await self._server(scope, receive, send)
            return
        client = (scope.get("client") or ("", 0))[0] or ""
        if scope["type"] != "http" or not self.network or from_this_computer(scope):
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if any(path == p or path.startswith(p + "/") for p in THIS_COMPUTER_ONLY):
            if scope.get("method", "GET") in ("GET", "HEAD"):
                page = f"<!doctype html><meta charset=utf-8><title>Settings</title><p>{SETTINGS_HERE_ONLY}</p>"
                refusal: Any = HTMLResponse(page + '<p><a href="/">Back</a></p>', 403)
            else:
                refusal = JSONResponse({"ok": False, "error": SETTINGS_HERE_ONLY}, status_code=403)
            refusal.headers["Cache-Control"] = "no-store"
            await refusal(scope, receive, send)
            return
        if scope.get("method", "GET") not in ("GET", "HEAD") and any(
            path == p or path.startswith(p + "/") for p in SETUP_CHANGES_HERE_ONLY
        ):
            refusal = JSONResponse({"ok": False, "error": SETUP_HERE_ONLY}, status_code=403)
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

    async def _server(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Server mode: the account behind the session cookie, and what its role may do (web/roles.py)."""
        from ap_coder.web import roles

        client = (scope.get("client") or ("", 0))[0] or ""
        path, method = scope.get("path") or "", str(scope.get("method") or "GET").upper()
        need = roles.need_for(scope.get("app"), scope)
        user = None
        if not path.startswith(OPEN_PREFIXES):
            token = _cookies(scope.get("headers") or []).get(SESSION_COOKIE)
            store = getattr(getattr(scope.get("app"), "state", None), "store", None)
            if token and store is not None:
                user = await run_in_threadpool(store.session_user, token)
        page = method in ("GET", "HEAD")
        refusal: Any = None
        if user is None and need != roles.OPEN:
            if page:
                refusal = RedirectResponse(f"{LOGIN_PATH}?next={quote(_wanted(scope), safe='')}", status_code=303)
            else:
                refusal = JSONResponse({"ok": False, "error": SESSION_ENDED, "redirect": LOGIN_PATH}, status_code=401)
        elif user is not None and user["must_change"] and need not in (roles.OPEN, roles.SIGNED_IN):
            if page:
                refusal = RedirectResponse(f"{PASSWORD_PATH}?next={quote(_wanted(scope), safe='')}", status_code=303)
            else:
                refusal = JSONResponse({"ok": False, "error": CHANGE_FIRST}, status_code=403)
        signed_in = _account_person(user, client) if user is not None else None
        tokens = (
            PERSON.set(signed_in),
            EVENT_ORIGIN.set(client or None),
            EVENT_ACTOR.set(signed_in.name if signed_in is not None else None),  # events record who (B-1106)
        )
        try:
            if refusal is None and user is not None and not roles.allowed(user["role"], need):
                refusal = await run_in_threadpool(_not_allowed, scope, receive, user, page)
            if refusal is not None:
                refusal.headers["Cache-Control"] = "no-store"
                await refusal(scope, receive, send)
                return
            await self.app(scope, receive, send)
        finally:
            PERSON.reset(tokens[0])
            EVENT_ORIGIN.reset(tokens[1])
            EVENT_ACTOR.reset(tokens[2])


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
