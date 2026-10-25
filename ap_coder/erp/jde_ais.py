"""JD Edwards EnterpriseOne through its AIS Server: the connection settings and the REST client (D-104).

AP Coder signs in to the company's AIS Server as its own JDE service user and calls named orchestrations with JSON
in and JSON out; it never writes JDE tables itself. What each orchestration takes and gives is in
``jde_orchestrations.json`` (the one mapping file: the AIS paths and versions, every orchestration's name, inputs and
outputs) and is specified for the JDE team in ``docs/JDE_ORCHESTRATIONS.md``.

* **Off by default.** Settings → JD Edwards E1 → API connection (an Admin): the AIS address, environment, role, user,
  and two switches, *step 1* (read suppliers, accounts, business units, POs and vouchers) and *step 2* (post approved
  vouchers). With both off nothing here opens a connection (``AisOff``).
* **Only that address.** The client connects to the one host of the AIS address saved, never through a proxy, never
  following a redirect; an address on the internet is refused before a byte is sent unless ``AP_ALLOW_INTERNET=1``
  (an IP address must be a private one, a name must resolve to private addresses only). HTTPS with the certificate
  checked against the computer's trusted certificates, or a company CA file named in the settings; plain http only to
  this computer (the stand-in AIS server of the tests and of a first trial).
* **Sign-in.** ``token`` (the default): ``POST <token_path>`` with the user, password, environment, role and device
  name; the token goes in the ``jde-AIS-Auth`` header of each call; an expired token (HTTP 444 on AIS, CONFIRM) is
  asked again once; ``close`` logs it out. ``basic``: HTTP Basic on each orchestration call, with the environment, role
  and device in their ``jde-AIS-Auth-*`` headers (AIS 9.2.1 and later accept it on the v3 orchestrator, CONFIRM).
* **Errors in plain English** (``AisError``, ``kind``): the address refused, not reachable, a timeout, the user or
  password refused, no permission, no such orchestration, JDE's own error messages (its error shape, ``jde_messages``),
  an answer that isn't JSON. ``sent`` says whether the request may have reached JDE: a voucher whose request timed out
  after it was sent is *unknown*, looked up before it is ever sent again (``jde_post``).
* **Limited retries** of what is safe to repeat: the connection refused or reset before the request was sent, AIS busy
  (502/503/504), and a timeout of a read (``idempotent``); a voucher's request is never repeated by the client after
  it may have reached JDE: 502 and 504 (a gateway that passed the request on and lost the answer) raise ``sent``. 503
  is taken as refused before processing (AIS or its load balancer turning the request away): retried, not sent.
* **The address checked is the address used**: the name is resolved once, each address must be on the company
  network, and the connection goes to that address (with the name for the certificate and the Host header), so a
  DNS answer that changes between the check and the connection (DNS rebinding) can't lead it elsewhere. 6to4
  (2002::/16) and documentation (2001:db8::/32) addresses are not internal; 100.64.0.0/10 (carrier-grade NAT, some
  VPNs) only when an Admin ticks "this address is on our network" (``shared_network``).
* **No secrets in logs**: the password is kept in the data folder's .env (``JDE_AIS_PASSWORD``, as the Azure key is),
  never in the database or the repository; it, the token and the Basic header are cut out of any error text
  (``scrub``, whatever their length), and log lines name the orchestration and the HTTP status only. A new AIS
  address forgets the saved password (it is typed again), so it is never sent to a host it was not given for.
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

log = logging.getLogger(__name__)

SETTING_KEY = "jde_ais"
PASSWORD_ENV = "JDE_AIS_PASSWORD"
MAPPING_FILE = "jde_orchestrations.json"  # in this folder, and optionally a copy in the data folder over it
TOKEN, BASIC = "token", "basic"
AUTH_MODES = {
    TOKEN: "Token (AIS token request, then the token on each call)",
    BASIC: "Basic authentication on each call",
}
DEFAULT_TIMEOUT = 30.0  # seconds, each request
DEFAULT_RETRIES = 2  # a request that is safe to repeat, at most this many times again
RETRY_PAUSE = 1.0  # seconds before the first retry, doubled each time (tests set it to 0)
BUSY = (502, 503, 504)

# What went wrong (``AisError.kind``).
OFF, ADDRESS, UNREACHABLE, TIMEOUT, CERTIFICATE, AUTH, DENIED, NOT_FOUND, JDE, ANSWER = (
    "off", "address", "unreachable", "timeout", "certificate", "auth", "denied", "not found", "jde", "answer",
)  # fmt: skip
DOWN = frozenset({UNREACHABLE, TIMEOUT})  # JD Edwards can't be reached just now: queued, asked again later


class AisError(Exception):
    """A call that did not work, in plain English (``str``), with what kind of failure (``kind``), the HTTP status,
    JDE's own messages and whether the request may have reached JDE (``sent``)."""

    def __init__(self, kind: str, message: str, *, status: int | None = None, messages: list[str] | None = None,
                 sent: bool = False) -> None:  # fmt: skip
        super().__init__(message)
        self.kind, self.status, self.messages, self.sent = kind, status, list(messages or []), sent


class AisOff(AisError):
    def __init__(self, message: str = "") -> None:
        super().__init__(OFF, message or "The JD Edwards API connection is off (Settings → JD Edwards E1 → API "
                         "connection): nothing is sent.")  # fmt: skip


# --- The mapping file ----------------------------------------------------------------------------------------------


def _merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        out[key] = _merge(out[key], value) if isinstance(value, Mapping) and isinstance(out.get(key), dict) else value
    return out


def mapping_paths() -> tuple[Path, Path | None]:
    """(the mapping that comes with AP Coder, the data folder's copy over it or None)."""
    from ..core import paths

    own = Path(__file__).with_name(MAPPING_FILE)
    try:
        copy = paths.private_dir() / MAPPING_FILE
    except OSError:
        copy = None
    return own, copy if copy is not None and copy.is_file() else None


def load_mapping() -> dict[str, Any]:
    """The AIS paths and the orchestrations: this folder's file, with the data folder's copy over it key by key (the
    JDE team renames an orchestration, an input or an output there). A copy that can't be read is reported, not
    guessed: ValueError."""
    own, copy = mapping_paths()
    data = json.loads(own.read_text(encoding="utf-8"))
    if copy is not None:
        try:
            over = json.loads(copy.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{copy} can't be read as JSON ({exc}): fix it or remove it.") from None
        if not isinstance(over, dict):
            raise ValueError(f"{copy} must hold one JSON object, like {own.name}.")
        data = _merge(data, over)
    return data


# --- Settings ------------------------------------------------------------------------------------------------------


@dataclass
class AisSettings:
    """The connection (the database's settings, ``SETTING_KEY``); the password is in the .env (``PASSWORD_ENV``)."""

    base_url: str = ""  # https://<server>:<port>, e.g. port 9300
    environment: str = ""  # JPD920
    role: str = "*ALL"
    username: str = ""
    device_name: str = "APCoder"
    auth_mode: str = TOKEN
    ca_file: str = ""  # a company CA certificate (PEM); blank: the computer's trusted certificates
    timeout: float = DEFAULT_TIMEOUT
    step1: bool = False  # read: suppliers, accounts, business units, POs, vouchers
    step2: bool = False  # post approved vouchers
    sync_hour: int = 2  # the nightly sync runs after this hour, once a day, while AP Coder runs
    company: str = ""  # the company whose accounts and business units are read; blank: every company
    po_order_type: str = "OP"
    voucher_days: int = 730  # how far back the AP ledger is read for the duplicate check
    shared_network: bool = False  # an Admin's tick: the AIS address in 100.64.0.0/10 is on the company network
    password: str = field(default="", repr=False, compare=False)  # from the .env, never stored with the rest

    @property
    def on(self) -> bool:
        return self.step1 or self.step2

    @property
    def host(self) -> str:
        return (urlsplit(self.base_url).hostname or "").lower()

    @property
    def password_hint(self) -> str:
        return "saved" if self.password else ""

    def to_json(self) -> str:
        data = asdict(self)
        data.pop("password", None)
        return json.dumps(data, sort_keys=True)

    @classmethod
    def from_json(cls, text: str | None) -> AisSettings:
        try:
            data = json.loads(text or "{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        base = cls()
        known = {k for k in asdict(base) if k != "password"}
        out = cls(**{k: v for k, v in data.items() if k in known})
        for name in ("base_url", "environment", "role", "username", "device_name", "ca_file", "company",
                     "po_order_type"):  # fmt: skip
            setattr(out, name, str(getattr(out, name) or "").strip())
        out.auth_mode = out.auth_mode if out.auth_mode in AUTH_MODES else TOKEN
        out.step1, out.step2, out.shared_network = bool(out.step1), bool(out.step2), bool(out.shared_network)
        try:
            out.timeout = min(300.0, max(0.1, float(out.timeout)))
        except (TypeError, ValueError):
            out.timeout = DEFAULT_TIMEOUT
        try:
            out.sync_hour = min(23, max(0, int(out.sync_hour)))
        except (TypeError, ValueError):
            out.sync_hour = 2
        try:
            out.voucher_days = min(3650, max(1, int(out.voucher_days)))
        except (TypeError, ValueError):
            out.voucher_days = 730
        return out


def env_path() -> Path:
    """The .env in use, or where a new one goes (the data folder): where the password is kept, as the Azure key is."""
    from ..core import paths

    return paths.env_file() or paths.private_dir() / ".env"


def _password() -> str:
    from ..core.config import _env

    return _env(PASSWORD_ENV) or ""


def load(store: Any) -> AisSettings:
    cfg = AisSettings.from_json(store.get_setting(SETTING_KEY))
    cfg.password = _password()
    return cfg


def clean_base_url(value: str) -> str:
    value = str(value or "").strip()
    if value and "://" not in value:
        value = "https://" + value
    return value.rstrip("/")


def address_problem(url: str) -> str:
    """Why this AIS address can't be used ("" when it can): https:// (http only to this computer), a host, no
    user:password in it (where it is on the network is ``internal_problem``'s, at connection time)."""
    if not url:
        return "No AIS address yet: add the address of your AIS Server (https://<server>:<port>)."
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - a port that isn't a number raises here
    except ValueError:
        return "The AIS address can't be read: type it as https://<server>:<port>."
    host = (parts.hostname or "").lower()
    if not host or "@" in parts.netloc or parts.query or parts.fragment:
        return "The AIS address must be https://<server>:<port> (no user name, password or ? in it)."
    if parts.scheme == "http" and not _loopback(host):
        return "The AIS address must start with https:// (plain http only to this computer, for a test)."
    if parts.scheme not in ("http", "https"):
        return "The AIS address must start with https:// (https://<server>:<port>)."
    return ""


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


NOT_INTERNAL = (ipaddress.ip_network("2002::/16"), ipaddress.ip_network("2001:db8::/32"))  # 6to4, documentation
SHARED = ipaddress.ip_network("100.64.0.0/10")  # carrier-grade NAT (some VPNs): only with the Admin's tick


def _private(address: str, shared: bool = False) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if any(ip in net for net in NOT_INTERNAL):
        return False
    if ip in SHARED:
        return shared
    return ip.is_private or ip.is_loopback or ip.is_link_local


def internal_address(host: str, resolve: Callable[[str], list[str]] | None = None,
                     shared: bool = False) -> tuple[str, str]:  # fmt: skip
    """(why ``host`` is not on the company's network or "", the one address to connect to). An IP address must be a
    private one; a name is resolved once and judged by every address it resolves to, all private. With
    ``AP_ALLOW_INTERNET=1`` (developers) any address goes, still resolved once and pinned."""
    from ..ops.offline import internet_allowed

    anything = internet_allowed()
    bare = host.strip("[]")
    try:
        ipaddress.ip_address(bare)
        found = [bare]
    except ValueError:
        found = (resolve or _resolve)(host)
    if not found:
        return f"{host} can't be found on the network (check the name, or the VPN).", ""
    public = [a for a in found if not _private(a, shared)]
    if public and not anything:
        where = "" if found == [bare] else f" ({public[0]})"
        cgnat = (" (100.64.0.0/10: an Admin ticks \u201cthis address is on our network\u201d if it is)"
                 if any(_private(a, True) for a in public) else "")  # fmt: skip
        return (f"{host} is an address on the internet{where}: AP Coder connects only to an AIS Server on your "
                f"company's network{cgnat}."), ""  # fmt: skip
    return "", found[0]


def internal_problem(host: str, resolve: Callable[[str], list[str]] | None = None, shared: bool = False) -> str:
    """Why ``host`` is not on the company's network ("" when it is): ``internal_address``'s answer."""
    return internal_address(host, resolve, shared)[0]


def _resolve(host: str) -> list[str]:
    try:
        return sorted({str(i[4][0]) for i in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})
    except (OSError, UnicodeError):
        return []


def save(store: Any, *, base_url: str, environment: str, role: str, username: str, password: str = "",
         forget_password: bool = False, device_name: str = "APCoder", auth_mode: str = TOKEN, ca_file: str = "",
         timeout: float = DEFAULT_TIMEOUT, step1: bool = False, step2: bool = False, sync_hour: int = 2,
         company: str = "", po_order_type: str = "OP", voucher_days: int = 730, shared_network: bool = False,
         actor: str | None = None) -> list[str]:  # fmt: skip
    """Save the connection; the names of what changed (never the values of the password). An empty ``password`` keeps
    the one saved, unless the AIS address moved to another host: the saved password is then forgotten and asked for
    again. Raises ValueError, in plain words, for what can't be used. Switching a step on needs the address,
    environment, user and password."""
    from ..core.envfile import one_line, write_env

    before = load(store)
    new = AisSettings.from_json(json.dumps({
        "base_url": clean_base_url(base_url), "environment": environment, "role": role or "*ALL",
        "username": username, "device_name": device_name or "APCoder", "auth_mode": auth_mode, "ca_file": ca_file,
        "timeout": timeout, "step1": step1, "step2": step2, "sync_hour": sync_hour, "company": company,
        "po_order_type": po_order_type or "OP", "voucher_days": voucher_days, "shared_network": shared_network,
    }))  # fmt: skip
    if auth_mode not in AUTH_MODES:
        raise ValueError("Choose how AP Coder signs in to AIS.")
    if new.base_url and (problem := address_problem(new.base_url)):
        raise ValueError(problem)
    # The connection's own check (B-1200): an address on the internet is refused now. A name this computer can't look
    # up now (the VPN off) is saved: the connection checks it when it is used.
    found = [] if not new.base_url else _resolve(new.host)
    if found and (problem := internal_problem(new.host, lambda _host: found, new.shared_network)):
        raise ValueError(problem)
    if new.ca_file and not Path(new.ca_file).is_file():
        raise ValueError(f"The CA certificate file {new.ca_file} isn't there: give the full path of the .pem / .crt "
                         "file IT gave you, or leave it blank.")  # fmt: skip
    password = one_line(password or "")
    moved = bool(before.base_url and new.base_url and _where(new.base_url) != _where(before.base_url))
    if moved and not password and before.password:
        forget_password = True  # never send the password saved for one host to another
    if new.on:
        missing = [label for label, value in (("the AIS address", new.base_url), ("the environment", new.environment),
                                              ("the user", new.username)) if not value]  # fmt: skip
        if not password and (not before.password or forget_password):
            missing.append("the password (type it again for the new AIS address)" if moved else "the password")
        if missing:
            raise ValueError("To switch the connection on, fill in " + ", ".join(missing) + ".")
    changed = [name for name in ("base_url", "environment", "role", "username", "device_name", "auth_mode", "ca_file",
                                 "timeout", "step1", "step2", "sync_hour", "company", "po_order_type", "voucher_days",
                                 "shared_network")
               if getattr(new, name) != getattr(before, name)]  # fmt: skip
    if new.to_json() != store.get_setting(SETTING_KEY):
        store.set_setting(SETTING_KEY, new.to_json(), actor=actor)
    env: dict[str, str] = {}
    if forget_password and before.password and not password:
        env[PASSWORD_ENV] = ""
    elif password and password != before.password:
        env[PASSWORD_ENV] = password
    if env:
        write_env(env_path(), env)
        os.environ.update(env)
        changed.append("password")
    if changed:
        store.log_event("settings_changed", actor=actor, detail={"jde_api": changed})
    return changed


def _where(url: str) -> tuple[str, int | None]:
    """The host and port an AIS address goes to."""
    try:
        parts = urlsplit(url)
        return (parts.hostname or "").lower(), parts.port
    except ValueError:
        return url, None


def status(cfg: AisSettings) -> tuple[bool, str]:
    """(usable, plain-English state) for the settings page and ``jde status``."""
    if not cfg.on:
        return False, "Off: AP Coder connects to nothing. JD Edwards gets the batch files (Exports), as before."
    for problem in (address_problem(cfg.base_url), "" if cfg.password else "No password saved."):
        if problem:
            return False, problem
    steps = " and ".join(s for s, on in (("step 1 (reading)", cfg.step1), ("step 2 (vouchers)", cfg.step2)) if on)
    return True, f"On, {steps}: only {cfg.host} is contacted."


# --- The client ------------------------------------------------------------------------------------------------------


# Where a secret is cut out whatever comes after it: the value of password= / token: / an Authorization header.
_SECRET_AFTER = r"""((?:password|passwd|pwd|token|secret|authorization)["']?\s*[:=]\s*["']?(?:(?:Basic|Bearer)\s+)?)"""


def scrub(text: Any, *secrets: str) -> str:
    """``text`` with each secret (the password, the token, a Basic header) cut out, at most 500 characters. Whatever
    its length, as a whole word or as the value of password= / token: / Authorization, never inside another word: a
    password "x" leaves "example.com" as it is (B-1199)."""
    out = str(text or "")
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):  # whatever its length
        found = re.escape(secret)
        out = re.sub(_SECRET_AFTER + found, lambda m: m.group(1) + "••••", out, flags=re.IGNORECASE)
        out = re.sub(rf"(?<![A-Za-z0-9]){found}(?![A-Za-z0-9])", "••••", out)
    return out[:500]


def jde_messages(body: Any) -> list[str]:
    """JDE's own messages in an AIS error answer: ``userDefinedErrorText``, ``message`` (text, or the form request's
    ``errors`` / ``warnings`` with CODE, TITLE, DESC), an orchestration's ``Messages`` / ``errors``, ``exception``."""
    found: list[str] = []

    def add(value: Any) -> None:
        text = " ".join(str(value or "").split())
        if text and text not in found:
            found.append(text[:300])

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(value, str):
            add(value)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
        elif isinstance(value, dict):
            if any(k in value for k in ("TITLE", "DESC", "CODE")):
                code = str(value.get("CODE") or "").strip()
                title = str(value.get("TITLE") or value.get("DESC") or "").strip()
                add(f"{title} ({code})" if code and title else title or code)
                return
            for key in ("errors", "Errors", "Messages", "messages", "warnings"):
                if key in value:
                    walk(value[key], depth + 1)
            for key, inner in value.items():
                if isinstance(inner, dict) and key not in ("errors", "Errors", "Messages", "messages", "warnings"):
                    walk(inner, depth + 1)

    if isinstance(body, dict):
        add(body.get("userDefinedErrorText"))
        walk(body.get("message"))
        for key in ("Messages", "errors", "Errors"):
            walk(body.get(key))
        if not found:
            add(body.get("exception"))
    elif isinstance(body, str):
        add(body)
    return found


class AisClient:
    """One sign-in to AIS (token mode: one token, logged out by ``close``; use it as a context manager)."""

    def __init__(self, cfg: AisSettings, mapping: Mapping[str, Any] | None = None, *,
                 resolve: Callable[[str], list[str]] | None = None, allow_off: bool = False) -> None:  # fmt: skip
        if not cfg.on and not allow_off:
            raise AisOff()
        problem, address = address_problem(cfg.base_url), ""
        if not problem:
            problem, address = internal_address(cfg.host, resolve, cfg.shared_network)
        if not problem and urlsplit(cfg.base_url).scheme == "http" and not _loopback(address):
            problem = "The AIS address must start with https:// (plain http only to this computer, for a test)."
        if problem:
            raise AisError(ADDRESS, problem)
        self.address = address  # resolved once, checked, and the only address connected to
        if not cfg.username or not cfg.password:
            raise AisError(AUTH, "No JDE user or password saved (Settings → JD Edwards E1 → API connection).")
        self.cfg = cfg
        self.mapping = dict(mapping or load_mapping())
        self.ais = dict(self.mapping.get("ais") or {})
        parts = urlsplit(cfg.base_url)
        self.scheme, self.netloc = parts.scheme, parts.netloc
        self.prefix = parts.path.rstrip("/")
        self.token = ""
        self.retries = DEFAULT_RETRIES
        self._context: ssl.SSLContext | None = None

    # -- the secrets to keep out of any text
    def _secrets(self) -> tuple[str, ...]:
        basic = self._basic().split(" ", 1)[-1]
        return (self.cfg.password, self.token, basic)

    def _basic(self) -> str:
        raw = f"{self.cfg.username}:{self.cfg.password}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _ssl(self) -> ssl.SSLContext:
        if self._context is None:
            try:
                ctx = ssl.create_default_context(cafile=self.cfg.ca_file or None)
            except (OSError, ssl.SSLError) as exc:
                raise AisError(CERTIFICATE, f"The CA certificate file {self.cfg.ca_file} can't be read "
                               f"({type(exc).__name__}): give the .pem / .crt file IT gave you.") from None  # fmt: skip
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            self._context = ctx
        return self._context

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        # http.client uses no proxy: the AIS Server is asked directly, on the company network. The socket goes to the
        # address checked (``self.address``); the name stays the certificate's (SNI) and the Host header's.
        if self.scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(self.netloc, timeout=timeout,
                                                                           context=self._ssl())  # fmt: skip
        else:
            conn = http.client.HTTPConnection(self.netloc, timeout=timeout)
        address = self.address

        def pinned(where: tuple[str, int], *args: Any, **kwargs: Any) -> socket.socket:
            return socket.create_connection((address, where[1]), *args, **kwargs)

        conn._create_connection = pinned  # type: ignore[attr-defined]
        return conn

    def _once(self, method: str, path: str, body: Any, headers: dict[str, str],
              timeout: float) -> tuple[int, Any]:  # fmt: skip
        """One request: (HTTP status, the JSON answer or its text). Raises AisError for the connection's failures."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        conn = self._connection(timeout)
        host = self.cfg.host
        try:
            try:
                conn.connect()
            except ssl.SSLCertVerificationError as exc:
                raise AisError(CERTIFICATE, f"{host}'s certificate isn't trusted ({exc.verify_message}): name your "
                               "company's CA certificate file in the settings, or ask IT.") from None  # fmt: skip
            except ssl.SSLError as exc:
                raise AisError(CERTIFICATE, f"The secure connection to {host} failed ({exc.reason or exc}).") from None
            except TimeoutError:  # no connection opened, nothing sent (Windows waits ~2 s on a closed port)
                raise AisError(
                    UNREACHABLE,
                    f"{host} can't be reached (no answer within {timeout:g} seconds): is the "
                    "AIS Server running, and is this computer on the company network or VPN?",
                ) from None
            except OSError as exc:
                raise AisError(UNREACHABLE, f"{host} can't be reached ({type(exc).__name__}): is the AIS Server "
                               "running, and is this computer on the company network or VPN?") from None  # fmt: skip
            try:
                conn.request(
                    method,
                    self.prefix + path,
                    body=data,
                    headers={
                        "Accept": "application/json",
                        **({"Content-Type": "application/json"} if data else {}),
                        **headers,
                    },
                )
                answer = conn.getresponse()
                raw = answer.read()
            except TimeoutError:
                raise AisError(TIMEOUT, f"{host} did not answer within {timeout:g} seconds.", sent=True) from None
            except (ConnectionError, http.client.HTTPException, OSError) as exc:
                raise AisError(UNREACHABLE, f"The connection to {host} broke off ({type(exc).__name__}).",
                               sent=True) from None  # fmt: skip
        finally:
            conn.close()
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(text) if text.strip() else {}
        except ValueError:
            parsed = text
        if 300 <= answer.status < 400:
            raise AisError(ANSWER, f"{host} answered with a redirect ({answer.status}), which AP Coder never follows: "
                           "check the AIS address.", status=answer.status, sent=True)  # fmt: skip
        return answer.status, parsed

    def _request(self, method: str, path: str, body: Any, headers: dict[str, str], *, idempotent: bool,
                 timeout: float | None = None) -> tuple[int, Any]:  # fmt: skip
        timeout = timeout or self.cfg.timeout
        attempt = 0
        while True:
            try:
                status, parsed = self._once(method, path, body, headers, timeout)
            except AisError as exc:
                again = exc.kind in DOWN and (idempotent or not exc.sent)
                if not again or attempt >= self.retries:
                    exc.args = (scrub(exc.args[0], *self._secrets()),)
                    raise
            else:
                if status in (502, 504) and not idempotent:
                    # A gateway passed the request on and lost the answer: the voucher may be made. Never repeated.
                    raise AisError(UNREACHABLE, f"AIS answered HTTP {status} (a gateway that lost JD Edwards' "
                                   "answer): it may have been made, so it is looked up by its key before anything "
                                   "else.", status=status, sent=True)  # fmt: skip
                if status not in BUSY or attempt >= self.retries:
                    return status, parsed
            attempt += 1
            time.sleep(RETRY_PAUSE * 2 ** (attempt - 1))

    # -- signing in
    def login(self) -> str:
        """A token for the service user (token mode)."""
        body = {"username": self.cfg.username, "password": self.cfg.password, "deviceName": self.cfg.device_name,
                "environment": self.cfg.environment, "role": self.cfg.role or "*ALL"}  # fmt: skip
        status, parsed = self._request("POST", self.ais.get("token_path", "/jderest/v2/tokenrequest"), body, {},
                                       idempotent=True)  # fmt: skip
        if status in (401, 403):
            raise AisError(AUTH, scrub(f"AIS refused the JDE user {self.cfg.username} in {self.cfg.environment}: "
                           + self._why(parsed, "the user, password, environment or role is wrong"),
                           *self._secrets()), status=status)  # fmt: skip
        if status != 200:
            raise self._failure(status, parsed, "the token request")
        token = ((parsed.get("userInfo") or {}).get("token") if isinstance(parsed, dict) else None) or ""
        if not token:
            raise AisError(ANSWER, "AIS answered the token request without a token.", status=status)
        self.token = str(token)
        log.info("AIS: signed in to %s as %s", self.cfg.environment, self.cfg.username)
        return self.token

    def close(self) -> None:
        """Log the token out (token mode); never raises."""
        if not self.token:
            return
        token, self.token = self.token, ""
        try:
            self._request("POST", self.ais.get("logout_path", "/jderest/v2/tokenrequest/logout"), {"token": token},
                          {}, idempotent=True, timeout=min(self.cfg.timeout, 10))  # fmt: skip
        except AisError:
            pass

    def __enter__(self) -> AisClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _auth_headers(self) -> dict[str, str]:
        extra = {self.ais.get("environment_header", "jde-AIS-Auth-Environment"): self.cfg.environment,
                 self.ais.get("role_header", "jde-AIS-Auth-Role"): self.cfg.role or "*ALL",
                 self.ais.get("device_header", "jde-AIS-Auth-Device"): self.cfg.device_name}  # fmt: skip
        if self.cfg.auth_mode == BASIC:
            return {"Authorization": self._basic(), **extra}
        if not self.token:
            self.login()
        return {self.ais.get("token_header", "jde-AIS-Auth"): self.token,
                self.ais.get("device_header", "jde-AIS-Auth-Device"): self.cfg.device_name}  # fmt: skip

    # -- answers
    @staticmethod
    def _why(parsed: Any, default: str) -> str:
        found = jde_messages(parsed)
        return "; ".join(found[:3]) if found else default

    def _failure(self, status: int, parsed: Any, what: str) -> AisError:
        found = [scrub(m, *self._secrets()) for m in jde_messages(parsed)]
        said = "; ".join(found[:3])
        if status in (401, 403):
            return AisError(DENIED, f"The JDE user {self.cfg.username} may not run {what}: " + (said or "ask the JDE "
                            "team to add it to the AP Coder security role."), status=status, messages=found,
                            sent=True)  # fmt: skip
        if status == 404:
            return AisError(NOT_FOUND, f"AIS has no {what} (HTTP 404): the JDE team must build and share it, or rename "
                            "it in jde_orchestrations.json.", status=status, messages=found, sent=True)  # fmt: skip
        if status in BUSY:
            return AisError(UNREACHABLE, f"AIS is busy or down (HTTP {status}) for {what}.", status=status,
                            messages=found, sent=False)  # fmt: skip
        if not isinstance(parsed, dict):
            return AisError(ANSWER, f"AIS answered {what} with something that isn't JSON (HTTP {status}).",
                            status=status, sent=True)  # fmt: skip
        return AisError(JDE, f"JD Edwards refused {what}: " + (said or f"HTTP {status}, no message."), status=status,
                        messages=found, sent=True)  # fmt: skip

    def orchestration(self, name: str, inputs: Mapping[str, Any], *, idempotent: bool = True,
                      timeout: float | None = None) -> dict[str, Any]:  # fmt: skip
        """Run orchestration ``name`` with these inputs; its outputs. An expired token is asked again, once."""
        path = self.ais.get("orchestrator_path", "/jderest/v3/orchestrator/{name}").replace("{name}", quote(name))
        expired = set(self.ais.get("expired_token_statuses") or [444])
        for again in (False, True):
            status, parsed = self._request("POST", path, dict(inputs), self._auth_headers(), idempotent=idempotent,
                                           timeout=timeout)  # fmt: skip
            if status in expired and self.cfg.auth_mode == TOKEN and not again:
                log.info("AIS: the token expired; signing in again")
                self.token = ""
                continue
            break
        log.info("AIS: %s answered HTTP %s", name, status)
        if status in expired:
            raise AisError(AUTH, "AIS refused the sign-in token even after signing in again.", status=status, sent=True)
        if status != 200:
            raise self._failure(status, parsed, f"the orchestration {name}")
        if not isinstance(parsed, dict):
            raise AisError(ANSWER, f"AIS answered {name} with something that isn't JSON.", status=status, sent=True)
        if str(parsed.get("jde__status") or "").upper() == "ERROR":
            raise self._failure(500, parsed, f"the orchestration {name}")
        return parsed

    def call(self, key: str, inputs: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        """Run the orchestration the mapping names for ``key`` ("suppliers", "voucher_create"...), with AP Coder's
        input names (``inputs``) turned into the orchestration's."""
        spec = spec_of(self.mapping, key)
        names = spec.get("inputs") or {}
        sent = {names.get(k, k): v for k, v in inputs.items()}
        return self.orchestration(str(spec["name"]), sent, **kwargs)

    def rows(self, key: str, inputs: Mapping[str, Any] | None = None, *, max_pages: int = 200) -> list[dict[str, Any]]:
        """Every row a read orchestration gives, page after page (``after`` = the last row's key, ``max_rows`` = the
        page size), with AP Coder's field names."""
        spec = spec_of(self.mapping, key)
        size = int(self.ais.get("page_size") or 500)
        out: list[dict[str, Any]] = []
        after = ""
        for _ in range(max_pages):
            answer = self.call(key, {**dict(inputs or {}), "after": after, "max_rows": size})
            page = [fields_of(spec, r) for r in answer.get(spec.get("rows") or "rows") or [] if isinstance(r, dict)]
            out += page
            if len(page) < size:
                return out
            last = page[-1]
            after = str(last.get("key") or last.get(next(iter(spec.get("fields") or {"key": ""}))) or "")
            if not after:
                return out
        raise AisError(ANSWER, f"{spec['name']} gave more than {max_pages} pages: narrow it, or raise the page size.")

    def test(self) -> dict[str, Any]:
        """Test connection: AIS's version (``defaultconfig``, when it answers), a sign-in and logout. Never raises."""
        found: dict[str, Any] = {"ok": False, "host": self.cfg.host}
        try:
            status, parsed = self._request("GET", self.ais.get("defaultconfig_path", "/jderest/v2/defaultconfig"),
                                           None, {}, idempotent=True)  # fmt: skip
            if status == 200 and isinstance(parsed, dict):
                found["version"] = str(parsed.get("aisVersion") or "")
            if self.cfg.auth_mode == TOKEN:
                self.login()
                self.close()
            found["ok"] = True
            version = f" (AIS {found['version']})" if found.get("version") else ""
            found["summary"] = f"Connected to {self.cfg.host}{version}: signed in to {self.cfg.environment} as " + (
                self.cfg.username + "."
            )
        except AisError as exc:
            found["error"], found["kind"] = scrub(str(exc), *self._secrets()), exc.kind
        return found


def spec_of(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    spec = (mapping.get("orchestrations") or {}).get(key)
    if not isinstance(spec, dict) or not spec.get("name"):
        raise AisError(ANSWER, f"jde_orchestrations.json has no orchestration for {key!r}.")
    return spec


def fields_of(spec: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """A row of an orchestration's answer with AP Coder's field names (``fields``: ours → theirs)."""
    return {ours: row.get(theirs) for ours, theirs in (spec.get("fields") or {}).items()}


def client(store: Any, *, need: str = "", allow_off: bool = False) -> AisClient:
    """The client for this database's settings. ``need`` "step1" / "step2": that step must be on (else AisOff)."""
    cfg = load(store)
    if need and not getattr(cfg, need) and not allow_off:
        raise AisOff(f"JD Edwards API {'step 1 (reading)' if need == 'step1' else 'step 2 (vouchers)'} is off "
                     "(Settings → JD Edwards E1 → API connection): nothing is sent.")  # fmt: skip
    return AisClient(cfg, allow_off=allow_off)


def connection_test(store: Any) -> dict[str, Any]:
    """Test connection (Settings → JD Edwards E1 → API connection) and ``jde test``: only once the connection is on
    (an Admin switched a step on)."""
    cfg = load(store)
    if not cfg.on:
        return {"ok": False, "error": str(AisOff()), "kind": OFF}
    try:
        with AisClient(cfg) as c:
            found = c.test()
    except AisError as exc:
        found = {"ok": False, "error": scrub(str(exc), cfg.password), "kind": exc.kind}
    store.set_setting(SETTING_KEY + "_tested", json.dumps({**found, "at": _now()}))
    store.log_event("jde_api_tested", detail={"ok": found.get("ok"), "kind": found.get("kind", "")})
    return found


def _now() -> str:
    import datetime as dt

    return dt.datetime.now().isoformat(timespec="seconds")


def last_test(store: Any) -> dict[str, Any]:
    try:
        found = json.loads(store.get_setting(SETTING_KEY + "_tested") or "{}")
    except ValueError:
        found = {}
    return found if isinstance(found, dict) else {}
