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

import ipaddress
import socket
from collections.abc import Iterable
from typing import Any

from starlette.responses import PlainTextResponse

PAGE_HEADER = "x-ap-coder"  # sent by static/js/app.js on every POST, lower case as ASGI gives it
TOKEN_HEADER = "x-ap-coder-token"  # the launcher's POST /shutdown (see web/server.py)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
WILDCARD_ADDRESSES = {"", "0.0.0.0", "::"}  # listening on every network: colleagues use an IP or this PC's name


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
        names = {socket.gethostname(), socket.getfqdn()}
        return name in {n.lower().rstrip(".") for n in names if n}
    return False


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

    def __init__(self, app: Any, address: str = "127.0.0.1", extra: Iterable[str] = ()) -> None:
        self.app = app
        self.address = address
        self.extra = tuple(extra)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
            host = headers.get("host")
            if not host_allowed(host, self.address, self.extra):
                port = (scope.get("server") or (None, 8501))[1]
                await _refusal(
                    f"AP Coder was opened through {host}, which is not this computer's address, so nothing is shown. "
                    f"Open it at http://localhost:{port} (or with the AP Coder shortcut).",
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
