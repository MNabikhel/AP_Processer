"""Option B, "AP Coder pushes": the finance hub's files written straight to OneLake (D-103).

OneLake speaks the ADLS Gen2 DFS API (``https://onelake.dfs.fabric.microsoft.com/<workspace>/<item>/Files/...``) and
takes an Entra ID token got with an app registration's client credentials (tenant, client id, client secret; scope
``https://storage.azure.com/.default``). The Admin gives the folder's URL as Fabric shows it, e.g. a mirrored
database's landing zone ``https://onelake.dfs.fabric.microsoft.com/<workspace>/<database>/Files/LandingZone``.

Each file is written as ``FolderDestination`` writes one: created under ``_staging/``, appended, flushed, then renamed
into place (``x-ms-rename-source``), so Fabric never picks up half a file.

Network: nothing is sent unless the internet is allowed on this computer (``AP_ALLOW_INTERNET=1``) **and** an Admin
turned the push on (Settings → Finance hub), as for the Azure check. Only two hosts are ever contacted, the sign-in
host and the OneLake host of the URL given, over HTTPS (plain HTTP only to this computer, for tests), and no
redirect is followed. The client secret is kept in the data folder's ``.env`` (``AP_HUB_CLIENT_SECRET``), never in
the database, never shown back or logged.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from ...ops import offline
from .landing import STAGING, HubError

DEFAULT_AUTHORITY = "https://login.microsoftonline.com"
SCOPE = "https://storage.azure.com/.default"
DFS_VERSION = "2023-11-03"
ONELAKE_HOSTS = (".dfs.fabric.microsoft.com", ".dfs.core.windows.net")
AUTHORITY_HOSTS = ("login.microsoftonline.com", "login.microsoftonline.us", "login.partner.microsoftonline.cn")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
TIMEOUT = 60.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise HubError(f"OneLake answered with a redirect to {urllib.parse.urlsplit(newurl).hostname}: refused.")


def _check_url(url: str, hosts: tuple[str, ...], what: str, exact: bool = False) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host in LOOPBACK and parts.scheme in ("http", "https"):
        return parts  # a stand-in on this computer (tests)
    known = host in hosts if exact else any(host.endswith(h) for h in hosts)
    if parts.scheme != "https" or not known:
        raise HubError(f"The {what} must be an https:// address on {' or '.join(h.lstrip('.') for h in hosts)}: "
                       f"“{url}” is not.")  # fmt: skip
    return parts


def push_allowed(opted_in: bool) -> str:
    """Why the push can't be used now ("" when it can)."""
    if not offline.internet_allowed():
        return ("This computer is offline (AP_ALLOW_INTERNET is not 1): AP Coder pushes nothing to OneLake. Use "
                "“Fabric pulls” with a shared folder, or ask IT to allow the internet for AP Coder.")  # fmt: skip
    if not opted_in:
        return "The push to OneLake is off: an Admin ticks “Send the files to OneLake” first."
    return ""


class OneLakeDestination:
    kind = "onelake"

    def __init__(self, url: str, tenant: str, client_id: str, secret: str, *, authority: str = DEFAULT_AUTHORITY,
                 opted_in: bool = False) -> None:  # fmt: skip
        why = push_allowed(opted_in)
        if why:
            raise HubError(why)
        if not (url.strip() and tenant.strip() and client_id.strip() and secret):
            raise HubError("The OneLake push needs the folder URL, the tenant ID, the client ID and the client "
                           "secret (Settings → Finance hub).")  # fmt: skip
        self.parts = _check_url(url, ONELAKE_HOSTS, "OneLake folder URL")
        self.auth = _check_url(authority, AUTHORITY_HOSTS, "sign-in address", exact=True)
        segments = [s for s in self.parts.path.split("/") if s]
        if len(segments) < 2:
            example = "https://onelake.dfs.fabric.microsoft.com/<workspace>/<item>/Files/LandingZone"
            raise HubError(f"The OneLake folder URL needs the workspace and the item, e.g. {example}.")
        self.filesystem, self.folder = segments[0], "/".join(segments[1:])
        self.tenant, self.client_id, self._secret = tenant.strip(), client_id.strip(), secret
        self._token: tuple[str, float] | None = None
        loopback = (self.parts.hostname or "") in LOOPBACK
        handlers: list[Any] = [_NoRedirect()]
        if loopback:
            handlers.append(urllib.request.ProxyHandler({}))
        self._opener = urllib.request.build_opener(*handlers)

    # --- What the feed calls -----------------------------------------------------------------------------------

    def ident(self) -> str:
        return f"onelake:{self.parts.hostname}/{self.filesystem}/{self.folder}"

    def describe(self) -> str:
        return f"{self.parts.scheme}://{self.parts.netloc}/{self.filesystem}/{self.folder}"

    def list(self, folder: str) -> list[str]:
        directory = f"{self.folder}/{folder}".strip("/")
        query = urllib.parse.urlencode({"resource": "filesystem", "directory": directory, "recursive": "false"})
        url = f"{self._base()}/{urllib.parse.quote(self.filesystem)}?{query}"
        try:
            answer = self._call("GET", url)
        except _NotFound:
            return []
        found = json.loads(answer or b"{}").get("paths") or []
        return sorted(str(p.get("name", "")).rsplit("/", 1)[-1] for p in found
                      if str(p.get("isDirectory", "false")).lower() != "true")  # fmt: skip

    def write(self, path: str, data: bytes) -> None:
        name = path.rsplit("/", 1)[-1]
        staged = f"{self.folder}/{STAGING}/{uuid.uuid4().hex}-{name}"
        target = f"{self.folder}/{path}"
        self._call("PUT", self._url(staged, resource="file"))
        if data:
            self._call("PATCH", self._url(staged, action="append", position="0"), data)
        self._call("PATCH", self._url(staged, action="flush", position=str(len(data))))
        source = "/" + urllib.parse.quote(f"{self.filesystem}/{staged}")
        try:
            self._call("PUT", self._url(target), headers={"x-ms-rename-source": source})
        except HubError:
            try:
                self._call("DELETE", self._url(staged))
            except HubError:
                pass
            raise

    # --- HTTP -------------------------------------------------------------------------------------------------

    def _base(self) -> str:
        return f"{self.parts.scheme}://{self.parts.netloc}"

    def _url(self, path: str, **query: str) -> str:
        quoted = urllib.parse.quote(f"{self.filesystem}/{path}")
        return f"{self._base()}/{quoted}" + (f"?{urllib.parse.urlencode(query)}" if query else "")

    def token(self) -> str:
        if self._token and self._token[1] > time.time():
            return self._token[0]
        url = f"{self.auth.scheme}://{self.auth.netloc}/{urllib.parse.quote(self.tenant)}/oauth2/v2.0/token"
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                                       "client_secret": self._secret, "scope": SCOPE}).encode()  # fmt: skip
        request = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})  # fmt: skip
        try:
            with self._open(request) as answer:
                found = json.loads(answer.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise HubError(f"Signing in to Microsoft Entra ID was refused ({exc.code}): check the tenant ID, the "
                           f"client ID and the client secret.{_reason(exc)}") from exc  # fmt: skip
        except (urllib.error.URLError, OSError) as exc:
            raise HubError(f"The sign-in address {self.auth.hostname} can't be reached ({_why(exc)}).") from exc
        token = str(found.get("access_token") or "")
        if not token:
            raise HubError("Microsoft Entra ID gave no access token.")
        self._token = (token, time.time() + max(60, int(found.get("expires_in") or 3600)) - 60)
        return token

    def _open(self, request: urllib.request.Request) -> Any:
        return self._opener.open(request, timeout=TIMEOUT)

    def _call(self, method: str, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
        sent = {"Authorization": f"Bearer {self.token()}", "x-ms-version": DFS_VERSION,
                "Content-Length": str(len(data or b"")), **(headers or {})}  # fmt: skip
        request = urllib.request.Request(url, data=data if data is not None else b"", method=method, headers=sent)
        try:
            with self._open(request) as answer:
                return answer.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                raise _NotFound() from exc
            if exc.code in (401, 403):
                raise HubError(f"OneLake refused the files ({exc.code}): give the app registration access to the "
                               f"workspace (Contributor) and check the folder URL.{_reason(exc)}") from exc  # fmt: skip
            raise HubError(f"OneLake answered {exc.code} to {method} {urllib.parse.urlsplit(url).path}."
                           f"{_reason(exc)}") from exc  # fmt: skip
        except (urllib.error.URLError, OSError) as exc:
            raise HubError(f"OneLake ({self.parts.hostname}) can't be reached ({_why(exc)}).") from exc


class _NotFound(Exception):
    pass


def _why(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None) or exc
    return str(getattr(reason, "strerror", None) or reason)


def _reason(exc: urllib.error.HTTPError) -> str:
    """The service's own short reason (its error code), never the request."""
    try:
        found = json.loads(exc.read() or b"{}")
    except (ValueError, OSError):
        return ""
    error = found.get("error") if isinstance(found, dict) else None
    code = (error.get("code") if isinstance(error, dict) else error) or ""
    return f" ({str(code)[:80]})" if code else ""
