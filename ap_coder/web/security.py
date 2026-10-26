"""Only this computer, and only AP Coder's own pages.

Two guards sit in front of every page, as in CloseDesk:

* **Only this computer's addresses (DNS rebinding).** A web page on the internet whose name is made to point at
  127.0.0.1 could otherwise open the dashboard in the clerk's browser and read it. Browsers send the name they used
  in the Host header, so a request addressed to any other name is refused (``host_allowed``).
* **Only AP Coder's own pages change anything.** Every request that changes something is a POST that carries the
  header ``X-AP-Coder: 1``. AP Coder's own scripts add it (``static/js/app.js``); a form or a script on another
  site cannot, because the browser would first have to ask this server, which never says yes. A request whose
  Origin (or Sec-Fetch-Site) says it came from another site is refused too.

GET requests never change anything, so they need neither. The one exception to the header is the launcher's
``POST /shutdown``, which carries a secret token instead (checked by the route, from this computer only).
"""

from __future__ import annotations

import functools
import ipaddress
import os
import socket
from collections.abc import Iterable
from typing import Any

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, PlainTextResponse

from ap_coder.ops.offline import server_mode

PAGE_HEADER = "x-ap-coder"  # sent by static/js/app.js on every POST, lower case as ASGI gives it
TOKEN_HEADER = "x-ap-coder-token"  # the launcher's POST /shutdown (see web/server.py)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
WILDCARD_ADDRESSES = {"", "0.0.0.0", "::"}  # listening on every network: colleagues use an IP or this PC's name
ALLOWED_HOSTS_ENV = "AP_ALLOWED_HOSTS"  # the server's other names, e.g. "apcoder.company.local,10.1.2.3" (D-99)


def allowed_hosts() -> tuple[str, ...]:
    """The names the company gave the server (``AP_ALLOWED_HOSTS``, comma-separated): a DNS alias or an address
    browsers open it by, besides this computer's own."""
    raw = os.environ.get(ALLOWED_HOSTS_ENV) or ""
    return tuple(h for h in (host_name(part) for part in raw.split(",")) if h)


@functools.cache
def computer_names() -> frozenset[str]:
    """This computer's own names (its host name and full DNS name), looked up once: ``getfqdn`` asks the DNS, and a
    slow DNS asked on every request froze every page (N-58)."""
    names = set()
    for lookup in (socket.gethostname, socket.getfqdn):
        try:
            name = lookup()
        except OSError:
            continue
        if name:
            names.add(name.lower().rstrip("."))
    return frozenset(names)


def host_name(host: str) -> str:
    """The name part of a Host header ("localhost:8501" → "localhost", "[::1]:8501" → "::1")."""
    host = host.strip().lower()
    if host.startswith("["):
        host = host[1 : host.find("]")] if "]" in host else ""  # "[::1" without "]" is not an address
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return host.rstrip(".")


def host_allowed(host: str | None, address: str | None, extra: Iterable[str | None] = ()) -> bool:
    """Whether a request whose Host header is ``host`` may use the dashboard listening on ``address``: this
    computer (localhost, 127.0.0.1, ::1) or the configured address. When it listens on every network
    (``0.0.0.0``), also an IP address or this computer's own name: a rebinding attack needs a domain name.
    No Host header at all is not a browser, so it is allowed."""
    if host is None:
        return True
    name = host_name(host)
    if not name:
        return False
    allowed = LOCAL_HOSTS | {host_name(a) for a in (address, *extra) if a}
    if name in allowed:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_loopback or (getattr(ip, "ipv4_mapped", None) or ip).is_loopback):
        return True  # also ::ffff:127.0.0.1
    if host_name(address or "") in WILDCARD_ADDRESSES:
        if ip is not None:
            return True
        return name in computer_names()
    return False


def unnamed_proxy(client: Any, headers: dict[str, str]) -> bool:
    """A request from this computer that says it was forwarded for another one: a reverse proxy on the server whose
    address was not given with ``--proxy`` (then uvicorn would have put the forwarded address in its place). Without
    this, everyone behind the proxy would look like the server's own console."""
    host = (client or ("", 0))[0] or ""
    try:
        here = ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
    forwarded = headers.get("x-forwarded-for", "").split(",")[0].strip() or ("for=" in headers.get("forwarded", ""))
    if not here or not forwarded:
        return False
    try:
        return not ipaddress.ip_address(str(forwarded)).is_loopback
    except ValueError:
        return True  # "for=..." or a name: forwarded all the same


def same_origin(headers: dict[str, str], host: str | None) -> bool:
    """Whether a POST came from a page of this server: its Origin, when the browser sends one, names this server,
    and Sec-Fetch-Site does not say another site."""
    origin = (headers.get("origin") or "").strip().lower()
    if origin and origin != "null":
        if origin.split("://", 1)[-1].rstrip("/") != (host or "").strip().lower():
            return False
    elif origin == "null":
        return False  # a sandboxed frame or a file opened from disk
    return (headers.get("sec-fetch-site") or "").lower() not in {"cross-site", "same-site"}


def _refusal(text: str, status: int) -> PlainTextResponse:
    return PlainTextResponse(text, status_code=status, headers={"Cache-Control": "no-store"})


class LocalOnly:
    """ASGI middleware: refuses a foreign Host header (400) and a POST that is not from AP Coder's own page (403)."""

    def __init__(
        self, app: Any, address: str = "127.0.0.1", extra: Iterable[str] = (), server: bool | None = None
    ) -> None:
        self.app = app
        self.server = server_mode() if server is None else server
        self.address = address
        self.extra = tuple(extra)
        if host_name(address or "") in WILDCARD_ADDRESSES:
            computer_names()  # looked up now, at the start, never while a request waits (N-58)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
            host = headers.get("host")
            if not host_allowed(host, self.address, self.extra):
                port = (scope.get("server") or (None, 8501))[1]
                if self.server:
                    text = (f"AP Coder was opened through {host}, which is not one of the server's names, so nothing "
                            f"is shown. IT adds the name with service install --allowed-hosts (which restarts AP "
                            f"Coder), or to {ALLOWED_HOSTS_ENV} and restarts it; several names: comma-separated "
                            "(docs/SERVER.md, section 4).")  # fmt: skip
                else:
                    text = (
                        f"AP Coder was opened through {host}, which is not this computer's address, so nothing "
                        f"is shown. Open it at http://localhost:{port} (or with the AP Coder shortcut)."
                    )
                await _refusal(text, 400)(scope, receive, send)
                return
            if self.server and unnamed_proxy(scope.get("client"), headers):
                await _refusal(
                    "This request came through a reverse proxy, but AP Coder was not told the proxy's address, so "
                    "nothing is shown (everyone would look like the server itself). Start AP Coder with --proxy "
                    "<the proxy's address>.",
                    400,
                )(scope, receive, send)
                return
            # The launcher stops the server with its token instead of the page's header (the route checks it).
            launcher = scope.get("path") == "/shutdown" and TOKEN_HEADER in headers
            if scope.get("method", "GET") not in SAFE_METHODS and (
                (headers.get(PAGE_HEADER) != "1" and not launcher) or not same_origin(headers, host)
            ):
                await _refusal("Use the AP Coder page for this.", 403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


# --- Security headers (D-99) --------------------------------------------------------------------------------------

# Scripts only from AP Coder's own files (no inline script runs: tests/test_server_https.py checks the templates);
# inline styles are allowed, as the pages set a bar's width or a column's size in style="...". Nothing is loaded from
# another site, and no other site may show AP Coder in a frame.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'"
)
HSTS = "max-age=31536000"  # a year; without includeSubDomains, the company's other sites are not this server's


class SecurityHeaders:
    """ASGI middleware: the headers that tell the browser what a page may do. On the laptop the harmless two (no
    frames, no guessed file types); on the server also the referrer and content policies, and HSTS (``hsts``: always,
    unless ``--insecure-http``, whatever scheme a request reports: a proxy in front may end HTTPS, D-101)."""

    def __init__(self, app: Any, server: bool = False, hsts: bool | None = None) -> None:
        self.app = app
        self.server = server
        self.hsts = server if hsts is None else hsts

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        added = [(b"x-frame-options", b"DENY"), (b"x-content-type-options", b"nosniff")]
        if self.server:
            added += [(b"referrer-policy", b"same-origin"),
                      (b"content-security-policy", CONTENT_SECURITY_POLICY.encode())]  # fmt: skip
            if self.hsts:
                added.append((b"strict-transport-security", HSTS.encode()))

        async def sending(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message.get("headers") or []}
                message["headers"] = [*(message.get("headers") or []), *(h for h in added if h[0] not in names)]
            await send(message)

        await self.app(scope, receive, sending)


# --- Request sizes on the server (D-109) ------------------------------------------------------------------------------

OPEN_BODY_LIMIT = 64 * 1024  # a request anyone may send (the login, before a session): a form of a few fields
# Any other request, from a person logged in: the largest upload AP Coder takes (Import invoices: 250 MB in all,
# process.MAX_REQUEST_BYTES), with room for the form around it.
BODY_LIMIT = 260 * 1024 * 1024
TOO_LARGE = "This is more than AP Coder takes in one go: send fewer or smaller files."
NO_MULTIPART = "Log in from AP Coder's login page."


class BodyTooLarge(HTTPException):
    """The request's body went past its limit while it was read (FastAPI passes an HTTPException on as it is)."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail=TOO_LARGE)


def declared_length(headers: list[tuple[bytes, bytes]]) -> int | None:
    for name, value in headers:
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return -1  # not a number: refused
    return None


def _too_large() -> JSONResponse:
    headers = {"Cache-Control": "no-store", "Connection": "close"}
    return JSONResponse({"ok": False, "error": TOO_LARGE}, status_code=413, headers=headers)


async def limit_body(app: Any, scope: dict[str, Any], receive: Any, send: Any, limit: int) -> None:
    """Run ``app`` with at most ``limit`` bytes of request body: a larger Content-Length is refused before anything
    is read (413), and a body that goes past it while it streams in (chunked, or a false length) stops being read
    there, so it is never spooled to disk in full."""
    length = declared_length(scope.get("headers") or [])
    if length is not None and not 0 <= length <= limit:
        await _too_large()(scope, receive, send)
        return
    seen, started = 0, False

    async def receiving() -> dict[str, Any]:
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body") or b"")
            if seen > limit:
                raise BodyTooLarge()
        return message

    async def sending(message: dict[str, Any]) -> None:
        nonlocal started
        started = started or message["type"] == "http.response.start"
        await send(message)

    try:
        await app(scope, receiving, sending)
    except BodyTooLarge:
        if started:
            raise
        await _too_large()(scope, receive, send)


def multipart(scope: dict[str, Any]) -> bool:
    return any(
        k.lower() == b"content-type" and v.lower().startswith(b"multipart/") for k, v in scope.get("headers") or []
    )
