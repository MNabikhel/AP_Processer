"""A network guard for the offline proof: every connection, name lookup or datagram to anything but this computer
(127.0.0.0/8, ::1, localhost) is refused and recorded.

AP Coder is an offline build (``ap_coder/offline.py``): it may talk to its own web server and to LM Studio on
127.0.0.1, and to the Azure check's endpoint only when AP switched that on in Settings. ``install()`` patches the
socket module so any other attempt raises ``OfflineViolation`` (a ``ConnectionRefusedError``, so the code under test
sees what an offline computer would show it) and is appended to ``attempts`` and, when ``AP_OFFLINE_GUARD_LOG`` names
a file, to that file (one line each): a server or pip started in a subprocess reports there.

Subprocesses get the guard through :func:`subprocess_env`: its ``sitecustomize.py`` (``tests/offline_guard_site``)
installs it before anything else runs. Standard library only.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

LOG_ENV = "AP_OFFLINE_GUARD_LOG"
ALLOW_ENV = "AP_OFFLINE_GUARD_ALLOW"  # extra host names allowed, comma separated (none by default)
SITE_DIR = Path(__file__).resolve().parent / "offline_guard_site"
LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
# Addresses no traffic should go to, used to tempt the code under test: a proxy, a package index, a model server.
BAIT_HOST = "203.0.113.7"  # TEST-NET-3 (RFC 5737): never routed
BAIT_ENV = {
    "HTTP_PROXY": f"http://{BAIT_HOST}:3128",
    "HTTPS_PROXY": f"http://{BAIT_HOST}:3128",
    "ALL_PROXY": f"http://{BAIT_HOST}:3128",
    "http_proxy": f"http://{BAIT_HOST}:3128",
    "https_proxy": f"http://{BAIT_HOST}:3128",
    "all_proxy": f"http://{BAIT_HOST}:3128",
    "NO_PROXY": "",
    "no_proxy": "",
    "PIP_INDEX_URL": f"https://{BAIT_HOST}/simple",
    "PIP_EXTRA_INDEX_URL": f"https://{BAIT_HOST}/extra",
    "PIP_FIND_LINKS": f"https://{BAIT_HOST}/links",
    "BROWSER": "none",
}


class OfflineViolation(ConnectionRefusedError):
    """A connection to somewhere other than this computer."""


attempts: list[str] = []
_lock = threading.Lock()
_saved: dict[str, Any] = {}


def is_local(host: Any) -> bool:
    """127.0.0.0/8, ::1 (also ::ffff:127.x), localhost, an empty host (the wildcard a server binds to), or a Unix
    socket path."""
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    name = str(host).strip().lower().rstrip(".")
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    name = name.split("%", 1)[0]  # fe80::1%lo
    allowed = {n.strip().lower() for n in (os.environ.get(ALLOW_ENV) or "").split(",") if n.strip()}
    if name in ("", "0.0.0.0", "::") or name in LOCAL_NAMES or name in allowed:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


def _record(what: str) -> None:
    line = f"{what} (pid {os.getpid()})"
    with _lock:
        attempts.append(line)
        log = os.environ.get(LOG_ENV)
        if log:
            try:
                with open(log, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass


def _refuse(what: str) -> OfflineViolation:
    _record(what)
    return OfflineViolation(111, f"offline guard: {what} refused (not this computer)")


def _address_host(address: Any) -> Any:
    if isinstance(address, (tuple, list)) and address:
        return address[0]
    return None  # AF_UNIX path or something else local


def install() -> None:
    """Patch the socket module (once; :func:`uninstall` undoes it)."""
    if _saved:
        return
    real_socket = socket.socket
    _saved.update(
        connect=real_socket.connect,
        connect_ex=real_socket.connect_ex,
        sendto=real_socket.sendto,
        getaddrinfo=socket.getaddrinfo,
        gethostbyname=socket.gethostbyname,
        gethostbyname_ex=socket.gethostbyname_ex,
        gethostbyaddr=socket.gethostbyaddr,
    )

    def connect(self: socket.socket, address: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6) and not is_local(_address_host(address)):
            raise _refuse(f"connect to {address!r}")
        return _saved["connect"](self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6) and not is_local(_address_host(address)):
            raise _refuse(f"connect_ex to {address!r}")
        return _saved["connect_ex"](self, address)

    def sendto(self: socket.socket, data: Any, *args: Any) -> Any:
        address = args[-1] if args else None
        if self.family in (socket.AF_INET, socket.AF_INET6) and not is_local(_address_host(address)):
            raise _refuse(f"sendto {address!r}")
        return _saved["sendto"](self, data, *args)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not is_local(host):
            raise _refuse(f"name lookup of {host!r}")
        return _saved["getaddrinfo"](host, *args, **kwargs)

    def gethostbyname(host: Any) -> Any:
        if not is_local(host):
            raise _refuse(f"name lookup of {host!r}")
        return _saved["gethostbyname"](host)

    def gethostbyname_ex(host: Any) -> Any:
        if not is_local(host):
            raise _refuse(f"name lookup of {host!r}")
        return _saved["gethostbyname_ex"](host)

    def gethostbyaddr(host: Any) -> Any:
        if not is_local(host):
            raise _refuse(f"reverse lookup of {host!r}")
        return _saved["gethostbyaddr"](host)

    real_socket.connect = connect  # type: ignore[method-assign]
    real_socket.connect_ex = connect_ex  # type: ignore[method-assign]
    real_socket.sendto = sendto  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
    socket.gethostbyname_ex = gethostbyname_ex
    socket.gethostbyaddr = gethostbyaddr


def uninstall() -> None:
    if not _saved:
        return
    socket.socket.connect = _saved["connect"]  # type: ignore[method-assign]
    socket.socket.connect_ex = _saved["connect_ex"]  # type: ignore[method-assign]
    socket.socket.sendto = _saved["sendto"]  # type: ignore[method-assign]
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        setattr(socket, name, _saved[name])
    _saved.clear()


def subprocess_env(log: Path, base: dict[str, str] | None = None, bait: bool = True,
                   extra: Iterable[tuple[str, str]] = ()) -> dict[str, str]:  # fmt: skip
    """An environment for a Python subprocess that runs under the guard (reporting to ``log``), with the bait proxy
    and package-index settings unless ``bait`` is False."""
    env = dict(os.environ if base is None else base)
    env.pop("AP_ALLOW_INTERNET", None)
    if bait:
        env.update(BAIT_ENV)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SITE_DIR), env.get("PYTHONPATH", "")) if p)
    env[LOG_ENV] = str(log)
    env.update(extra)
    return env


def read_log(log: Path) -> list[str]:
    try:
        return [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []
