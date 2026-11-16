"""Azure Document Intelligence over plain HTTPS, with nothing but the standard library (D-245).

The optional reader (``azure_check``) asks the company's own Document Intelligence resource to read an invoice's
pages with ``prebuilt-invoice``, at one pinned API version (``API_VERSION``):

1. ``POST {endpoint}/documentintelligence/documentModels/prebuilt-invoice:analyze?api-version=2024-11-30``, the file
   as ``base64Source``, the key in ``Ocp-Apim-Subscription-Key``: Azure answers 202 with an ``Operation-Location``;
2. ``GET`` that address until the reading has ``succeeded`` (or ``failed``), at most every 2 seconds and never longer
   than ``POLL_SECONDS`` in all (``Retry-After`` honoured);
3. ``DELETE`` the same address, so Azure keeps nothing of the reading (it would otherwise keep it 24 hours).

Every address, the ``Operation-Location`` Azure gives included, is checked against the one host saved
(``endpoint_host``) before a socket is opened: no other host is ever contacted, no redirect is followed, no proxy is
used (``http.client`` uses none), and TLS is 1.2 or later. The key never goes in an error, a log line or a record.

A failure is a ``DiError`` with a ``kind``: ``KEY`` (401/403: the key was refused), ``NOT_FOUND`` (404),
``REFUSED`` (another host, a redirect, a 400), ``UNREACHABLE`` (no connection, Azure busy or failing after the
retries), ``TIMEOUT`` (the reading took too long), ``FAILED`` (Azure could not read the file). ``permanent`` says
which are not worth trying again (the key, the address, the request): the reader pauses its queue for those.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import socket
import ssl
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

log = logging.getLogger(__name__)

API_VERSION = "2024-11-30"
MODEL = "prebuilt-invoice"
CONNECT_SECONDS, REQUEST_SECONDS, POLL_SECONDS = 10.0, 30.0, 120.0
POLL_EVERY = 2.0  # never asked more often than this (Microsoft's advice)
BUSY_TRIES = 3  # a 429: tried at most this many times, after Retry-After
SERVER_PAUSES = (2.0, 5.0)  # a 500/502/503/504 or no connection: tried again after these pauses
LONGEST_WAIT = 60.0  # a Retry-After longer than this is cut to it
MAX_ANSWER = 64 * 1024 * 1024  # an answer longer than this is not read

KEY, NOT_FOUND, REFUSED, UNREACHABLE, TIMEOUT, FAILED = (
    "key", "not found", "refused", "unreachable", "timeout", "failed",
)  # fmt: skip
PERMANENT = (KEY, NOT_FOUND, REFUSED)
SERVER_ERRORS = (500, 502, 503, 504)

# The hosts of Azure Document Intelligence (a resource's own name, or a region's): nothing else is contacted, so
# invoices can't be sent to any https:// address typed in (B-978).
AZURE_HOSTS = (".cognitiveservices.azure.com", ".api.cognitive.microsoft.com")


def endpoint_host(url: str) -> str:
    """The host of an Azure Document Intelligence HTTPS endpoint (``AZURE_HOSTS``), lower case ("" for anything else:
    http, no host, user:password@, another host)."""
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if parts.scheme.lower() != "https" or not parts.hostname or "@" in parts.netloc:
        return ""
    host = parts.hostname.lower().rstrip(".")
    label = host.split(".", 1)[0]
    if not host.endswith(AZURE_HOSTS) or not label or any(host == h[1:] for h in AZURE_HOSTS):
        return ""
    return host


class DiError(RuntimeError):
    """Azure Document Intelligence could not read the file, in plain words (never with the key in them)."""

    def __init__(self, kind: str, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.kind, self.status = kind, status

    @property
    def permanent(self) -> bool:
        """Trying again won't help until someone changes the settings (the key, the address)."""
        return self.kind in PERMANENT


class _Unsent(OSError):
    """No connection was made: nothing was sent, so any request may be tried again."""


class DiClient:
    """One Document Intelligence resource: its endpoint and key, and only its host. ``connect_to``: where a socket
    for (host, port) really goes (the tests' stand-in on 127.0.0.1); ``ca_file``: a certificate authority to trust
    besides the system's (the tests' own); ``sleep``: how the client waits (the tests count the pauses)."""

    def __init__(self, endpoint: str, key: str, *, ca_file: str | None = None,
                 connect_to: Callable[[str, int], tuple[str, int]] | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 poll_seconds: float = POLL_SECONDS) -> None:  # fmt: skip
        self.host = endpoint_host(endpoint)
        if not self.host:
            raise DiError(REFUSED, "The endpoint must be an Azure Document Intelligence https:// address.")
        if not key:
            raise DiError(KEY, "No key is saved for Azure Document Intelligence.")
        self.base = endpoint.strip().rstrip("/")
        self._key = key
        self._ca_file, self._connect_to = ca_file, connect_to
        self._sleep, self._clock, self.poll_seconds = sleep, clock, poll_seconds
        self.region = ""  # the region Azure's answer names (``x-ms-region``), when it names one
        self.deleted = False  # the last reading was deleted from Azure

    # -- the readings
    def analyze(self, data: bytes, pages: str = "") -> dict[str, Any]:
        """Azure's reading of ``data`` (a PDF or a picture) with ``prebuilt-invoice``: its ``analyzeResult``.
        ``pages``: which pages ("1-4,9"), all when empty. The reading is deleted from Azure once it is taken."""
        query = {"api-version": API_VERSION, "stringIndexType": "unicodeCodePoint"}
        if pages:
            query["pages"] = pages
        url = f"{self.base}/documentintelligence/documentModels/{MODEL}:analyze?{urlencode(query)}"
        body = json.dumps({"base64Source": base64.b64encode(data).decode("ascii")}).encode("ascii")
        status, headers, answer = self._send("POST", url, body, repeat=False)
        if status != 202:
            raise self._refusal(status, answer, "the reading was not accepted")
        location = headers.get("operation-location", "")
        if endpoint_host(location) != self.host:
            raise DiError(REFUSED, f"Azure gave a result address on another host ({_host_of(location)}): it is not "
                          f"asked; AP Coder only contacts {self.host}.")  # fmt: skip
        self.deleted = False
        try:
            return self._result(location, _seconds(headers.get("retry-after")))
        finally:
            self._delete(location)

    def model_info(self) -> dict[str, Any]:
        """The model's description (no invoice is sent): whether the endpoint, the key and the model answer."""
        url = f"{self.base}/documentintelligence/documentModels/{MODEL}?{urlencode({'api-version': API_VERSION})}"
        status, _, answer = self._send("GET", url)
        if status != 200:
            raise self._refusal(status, answer, "the model was not found")
        return answer if isinstance(answer, dict) else {}

    def _result(self, location: str, wait: float) -> dict[str, Any]:
        deadline = self._clock() + self.poll_seconds
        while True:
            self._sleep(min(max(wait, POLL_EVERY), LONGEST_WAIT))
            status, headers, answer = self._send("GET", location)
            if status != 200 or not isinstance(answer, dict):
                raise self._refusal(status, answer, "the reading could not be fetched")
            state = str(answer.get("status") or "").lower()
            if state == "succeeded":
                return answer.get("analyzeResult") or {}
            if state in ("failed", "canceled"):
                why = ((answer.get("error") or {}).get("message") or state) if isinstance(answer, dict) else state
                raise DiError(FAILED, f"Azure could not read the file ({self._clean(str(why))}).")
            if self._clock() >= deadline:
                raise DiError(TIMEOUT, f"Azure had not finished reading after {self.poll_seconds:g} seconds.")
            wait = _seconds(headers.get("retry-after"))

    def _delete(self, location: str) -> None:
        """The reading deleted from Azure (best effort: Azure deletes it after 24 hours anyway)."""
        if endpoint_host(location) != self.host:
            return
        try:
            status, _, _ = self._send("DELETE", location)
            self.deleted = status in (200, 202, 204)
        except DiError as exc:
            log.info("Azure Document Intelligence: the reading was not deleted (%s)", exc.kind)

    # -- one request, tried again where that is safe
    def _send(self, method: str, url: str, body: bytes | None = None, *,
              repeat: bool = True) -> tuple[int, dict[str, str], Any]:  # fmt: skip
        """(status, headers in lower case, the JSON answer). ``repeat``: the request may be sent twice (a POST that
        reached Azure is never sent again: it would be read, and billed, twice)."""
        if endpoint_host(url) != self.host:
            raise DiError(REFUSED, f"refused: AP Coder only contacts {self.host} (not {_host_of(url)})")
        busy, failing = 0, 0
        while True:
            try:
                status, headers, answer = self._once(method, url, body)
            except _Unsent as exc:
                failing += 1
                if failing > len(SERVER_PAUSES):
                    raise DiError(UNREACHABLE, f"{self.host} can't be reached ({exc}).") from None
                self._sleep(SERVER_PAUSES[failing - 1])
                continue
            except (OSError, http.client.HTTPException) as exc:
                if not repeat:
                    raise DiError(UNREACHABLE, f"The connection to {self.host} broke off "
                                  f"({type(exc).__name__}).") from None  # fmt: skip
                failing += 1
                if failing > len(SERVER_PAUSES):
                    raise DiError(UNREACHABLE, f"The connection to {self.host} broke off "
                                  f"({type(exc).__name__}).") from None  # fmt: skip
                self._sleep(SERVER_PAUSES[failing - 1])
                continue
            region = headers.get("x-ms-region", "").strip()
            if region:
                self.region = region
            if status == 429 and busy + 1 < BUSY_TRIES:
                busy += 1
                self._sleep(min(_seconds(headers.get("retry-after")) or POLL_EVERY, LONGEST_WAIT))
                continue
            if status in SERVER_ERRORS and failing < len(SERVER_PAUSES):
                failing += 1
                self._sleep(SERVER_PAUSES[failing - 1])
                continue
            return status, headers, answer

    def _once(self, method: str, url: str, body: bytes | None) -> tuple[int, dict[str, str], Any]:
        parts = urlsplit(url)
        conn = http.client.HTTPSConnection(self.host, parts.port or 443, timeout=CONNECT_SECONDS,
                                           context=self._context())  # fmt: skip
        if self._connect_to is not None:
            where = self._connect_to(self.host, parts.port or 443)

            def pinned(_: Any, *args: Any, **kwargs: Any) -> socket.socket:
                return socket.create_connection(where, *args, **kwargs)

            conn._create_connection = pinned  # type: ignore[attr-defined]
        try:
            try:
                conn.connect()
            except ssl.SSLCertVerificationError as exc:
                raise DiError(REFUSED, f"{self.host}'s certificate isn't trusted ({exc.verify_message}).") from None
            except OSError as exc:
                raise _Unsent(type(exc).__name__) from None
            if conn.sock is not None:
                conn.sock.settimeout(REQUEST_SECONDS)
            path = parts.path + (f"?{parts.query}" if parts.query else "")
            headers = {"Ocp-Apim-Subscription-Key": self._key, "Accept": "application/json",
                       "User-Agent": "AP-Coder"}  # fmt: skip
            if body is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=body, headers=headers)
            answer = conn.getresponse()
            raw = answer.read(MAX_ANSWER + 1)
            found = {k.lower(): v for k, v in answer.getheaders()}
        finally:
            conn.close()
        if 300 <= answer.status < 400:
            raise DiError(REFUSED, f"{self.host} answered with a redirect ({answer.status}), which AP Coder never "
                          "follows: check the endpoint.", status=answer.status)  # fmt: skip
        if len(raw) > MAX_ANSWER:
            raise DiError(REFUSED, "Azure's answer was too long to read.", status=answer.status)
        try:
            parsed: Any = json.loads(raw) if raw.strip() else {}
        except ValueError:
            parsed = raw.decode("utf-8", "replace")
        return answer.status, found, parsed

    def _context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if self._ca_file:
            ctx.load_verify_locations(self._ca_file)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    def _refusal(self, status: int, answer: Any, what: str) -> DiError:
        error = answer.get("error") if isinstance(answer, dict) else None
        detail = self._clean(str((error or {}).get("message") or (error or {}).get("code") or ""))
        said = f": {detail}" if detail else ""
        if status in (401, 403):
            return DiError(KEY, f"Azure refused the key (HTTP {status}){said}. Paste it again from the Azure portal "
                           "(Keys and Endpoint).", status)  # fmt: skip
        if status == 404:
            return DiError(NOT_FOUND, f"Azure answered 'not found' (HTTP 404){said}: check the endpoint.", status)
        if status == 429:
            return DiError(UNREACHABLE, f"Azure is busy (HTTP 429){said}: tried again later.", status)
        if status in SERVER_ERRORS:
            return DiError(UNREACHABLE, f"Azure failed (HTTP {status}){said}: tried again later.", status)
        return DiError(REFUSED, f"Azure answered HTTP {status}, {what}{said}.", status)

    def _clean(self, text: str) -> str:
        """``text`` with the key taken out, short."""
        if self._key and len(self._key) >= 4:
            text = text.replace(self._key, "••••")
        return " ".join(text.split())[:300]


def _host_of(url: str) -> str:
    try:
        return urlsplit(str(url or "")).hostname or "no host"
    except ValueError:
        return "no host"


def _seconds(value: Any) -> float:
    try:
        return max(0.0, float(str(value).strip()))
    except (TypeError, ValueError):
        return 0.0
