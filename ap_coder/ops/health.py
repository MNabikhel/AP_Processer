"""The company server's health, for IT (D-187): what ``GET /readyz`` answers a monitor or a load balancer
(``readiness``), what the doctor adds on the server (``server_rows``, ``store_rows``), the Admin's banner about the
HTTPS certificate (``certificate_notice``) and the business time zone (``business_zone``).

``readiness`` is a dict of checks, each with its ``status`` (``OK``, ``WARN``, ``FAIL``), and the problems in plain
words. AP Coder is **not ready** (503) only when it can't do its work: the database can't be read, the data folder's
disk has less than ``backups.BACKUP_RESERVE`` free (nothing is read or backed up), the HTTPS certificate has expired,
the server's background process gave no heartbeat for ``STALE_SECONDS``, or none of its capture processes runs. The
rest is a warning (200): the certificate within 30 days of its end, a backup failed or old, the second backup folder
missing, unreachable or on the database's disk, a model server's calls on its backup, fewer capture processes than
started, this computer's clock in another time zone than the business's. Nothing in it is a secret, a person's or a
vendor's name, or a path: it may be shown to a monitor on the network (``monitor_allowed``).

Cheap enough for several monitors every 10 seconds: counts through the queues' indexes, a heartbeat row, the
backups folder's listing, the disks' free space, the certificate file; each process keeps its answer
``READY_SECONDS`` (``Kept``), and the second backup folder (often a share, which can take a minute to say it is gone)
is looked at in a thread of its own, its answer kept ``FOLDER_KEEP`` seconds."""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import hmac
import ipaddress
import math
import os
import re
import shutil
import sqlite3
import threading
import time
import zoneinfo
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .. import __version__
from ..core import paths
from . import system_log
from .offline import server_mode

OK, WARN, FAIL = "ok", "warn", "fail"
DOCTOR_STATUS = {OK: "PASS", WARN: "WARN", FAIL: "FAIL"}
READY_SECONDS = 5.0  # a process keeps its readiness answer this long
HEARTBEAT = "background"  # the server's background process's heartbeat (``Store.beat``, ``server.run_background``)
STALE_SECONDS = 120.0  # no heartbeat for this long (it beats every 30 s): stopped, or stuck
CERT_WARN_DAYS = 30  # the certificate's end this close: a warning
CERT_EVERY = 6 * 3600.0  # how often the server's own process looks at it, for the system's log
BACKUP_OLD_HOURS = 36  # the newest backup older than this: a day missed (the daily one is made by 30 hours)
FOLDER_WAIT, FOLDER_KEEP = 2.0, 60.0  # seconds a look at the second backup folder is waited for; kept
# server.json: what a monitor on the network gives (a header), or the addresses it asks from; the business time zone.
MONITOR_TOKEN, MONITOR_ADDRESSES, TIME_ZONE = "monitor_token", "monitor_addresses", "time_zone"
TOKEN_HEADER = "X-AP-Coder-Monitor"
MIN_TOKEN = 24  # characters of a monitor token: a shorter one is not taken
NOT_A_MONITOR = ("On the server, /readyz answers its own console and the monitors server.json names: a token "
                 f"(monitor_token, sent as the {TOKEN_HEADER} header) or their addresses (monitor_addresses). "
                 "docs/SERVER.md, section 12.")  # fmt: skip
SAME_DISK = ("The second backup folder is on the same disk as the database: a failure of that disk loses both. "
             "Choose a folder on another server (a network share).")  # fmt: skip


def _size(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


# --- The HTTPS certificate's end, read from its PEM file with the standard library ----------------------------------
# A DER walk to the validity's notAfter (RFC 5280: Certificate ::= SEQUENCE { tbsCertificate SEQUENCE { [0] version
# OPTIONAL, serialNumber INTEGER, signature SEQUENCE, issuer SEQUENCE, validity SEQUENCE { notBefore, notAfter } ...
# } ... }); ``cryptography`` is not a dependency, and ``ssl`` decodes a certificate only through a private function.

_PEM = re.compile(rb"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", re.S)
SEQUENCE, INTEGER, VERSION, UTC_TIME, GENERALIZED_TIME = 0x30, 0x02, 0xA0, 0x17, 0x18


def _element(der: bytes, at: int) -> tuple[int, int, int]:
    """(tag, where its value starts, where it ends) of the DER element at ``at``."""
    if at + 2 > len(der):
        raise ValueError("cut short")
    tag, size, start = der[at], der[at + 1], at + 2
    if size & 0x80:
        count = size & 0x7F
        if not 1 <= count <= 4 or start + count > len(der):
            raise ValueError("not a DER length")
        size, start = int.from_bytes(der[start : start + count], "big"), start + count
    if start + size > len(der):
        raise ValueError("cut short")
    return tag, start, start + size


def _expect(tag: int, wanted: int) -> None:
    if tag != wanted:
        raise ValueError("not an X.509 certificate")


def _time(tag: int, raw: bytes) -> dt.datetime:
    text = raw.decode("ascii")
    if tag == UTC_TIME and re.fullmatch(r"\d{12}Z", text):  # YYMMDDHHMMSSZ: 1950 to 2049
        text = f"{19 if int(text[:2]) >= 50 else 20}{text}"
    elif not (tag == GENERALIZED_TIME and re.fullmatch(r"\d{14}Z", text)):  # YYYYMMDDHHMMSSZ: from 2050
        raise ValueError("not a certificate's time")
    return dt.datetime.strptime(text, "%Y%m%d%H%M%SZ").replace(tzinfo=dt.UTC)


def not_after(der: bytes) -> dt.datetime:
    """When the X.509 certificate ``der`` ends (its notAfter, UTC). ValueError when it isn't one."""
    tag, at, _ = _element(der, 0)  # Certificate
    _expect(tag, SEQUENCE)
    tag, at, _ = _element(der, at)  # tbsCertificate
    _expect(tag, SEQUENCE)
    tag, _, end = _element(der, at)
    if tag == VERSION:  # (absent in a version 1 certificate)
        at = end
    for wanted in (INTEGER, SEQUENCE, SEQUENCE):  # serialNumber, signature, issuer
        tag, _, at = _element(der, at)
        _expect(tag, wanted)
    tag, at, _ = _element(der, at)  # validity
    _expect(tag, SEQUENCE)
    _, _, at = _element(der, at)  # notBefore
    tag, start, end = _element(der, at)
    return _time(tag, der[start:end])


def certificate_ends(path: str | Path) -> tuple[dt.datetime, int]:
    """When the first of the PEM file's certificates to end ends, and which it is (0: the first, the server's own; the
    CA's follow it). OSError when the file can't be read, ValueError when it holds no certificate."""
    blocks = _PEM.findall(Path(path).read_bytes())
    if not blocks:
        raise ValueError("no certificate")
    ends = [not_after(base64.b64decode(b"".join(block.split()), validate=True)) for block in blocks]
    first = min(range(len(ends)), key=ends.__getitem__)
    return ends[first], first


def certificate_check(path: str | Path, now: dt.datetime | None = None) -> dict[str, Any]:
    """{"status", "days_left", "expires"} of the certificate file ``path``, and the ``problem`` in words."""
    now = now or dt.datetime.now(dt.UTC)
    try:
        ends, which = certificate_ends(path)
    except OSError as exc:
        return {"status": WARN, "problem": "The HTTPS certificate file can't be read "
                f"({exc.strerror or type(exc).__name__}): when it expires isn't known."}  # fmt: skip
    except ValueError:
        return {"status": WARN, "problem": "AP Coder can't read when the HTTPS certificate expires: the file holds no "
                "certificate it can read."}  # fmt: skip
    left = ends - now
    found = {"days_left": math.floor(left.total_seconds() / 86400), "expires": ends.isoformat()}
    whose = "The server's HTTPS certificate" if which == 0 else "A CA certificate in the server's certificate file"
    if left <= dt.timedelta(0):
        return {**found, "status": FAIL, "problem": f"{whose} expired on {ends:%Y-%m-%d}: browsers warn everyone. "
                "Install a new one (docs/SERVER.md, section 3)."}  # fmt: skip
    if left <= dt.timedelta(days=CERT_WARN_DAYS):
        days = found["days_left"]
        when = "within a day" if days == 0 else f"in {days} day{'' if days == 1 else 's'}"
        return {**found, "status": WARN, "problem": f"{whose} expires on {ends:%Y-%m-%d}, {when}: ask for a new one "
                "and install it (docs/SERVER.md, section 3)."}  # fmt: skip
    return {**found, "status": OK}


def tell_certificate(found: dict[str, Any]) -> None:
    """The system's log told of a certificate near its end (a warning) or past it (an error)."""
    if found.get("status") in (WARN, FAIL) and "days_left" in found:
        system_log.report(system_log.CERTIFICATE, found["problem"], warning=found["status"] == WARN,
                          key="certificate")  # fmt: skip


def watch_certificate(certfile: str | None, every: float = CERT_EVERY) -> threading.Thread | None:
    """On the company server, its certificate looked at now and every ``every`` seconds, for the system's log
    (``tell_certificate``); the server's own process runs it (``web/server.py``). None elsewhere."""
    if not certfile or not server_mode():
        return None

    def watch() -> None:
        while True:
            tell_certificate(certificate_check(certfile))
            time.sleep(every)

    thread = threading.Thread(target=watch, name="ap-coder-certificate", daemon=True)
    thread.start()
    return thread


_NOTICES: dict[str, tuple[float, Any, dict[str, str] | None]] = {}
_NOTICES_LOCK = threading.Lock()
NOTICE_SECONDS = 600.0


def certificate_notice(certfile: str | None) -> dict[str, str] | None:
    """The Admin's banner about the server's certificate: {"text", "tone"} from 30 days before it expires, None
    otherwise (or without one). Looked at again after NOTICE_SECONDS, or as soon as its file changes."""
    if not certfile:
        return None
    try:
        stamp: Any = os.stat(certfile).st_mtime_ns
    except OSError:
        stamp = None
    with _NOTICES_LOCK:
        kept = _NOTICES.get(certfile)
        if kept is not None and kept[1] == stamp and time.monotonic() - kept[0] < NOTICE_SECONDS:
            return kept[2]
    found = certificate_check(certfile)
    notice = None
    if found["status"] != OK and "days_left" in found:
        notice = {"text": found["problem"], "tone": "err" if found["status"] == FAIL else "warn"}
    with _NOTICES_LOCK:
        _NOTICES[certfile] = (time.monotonic(), stamp, notice)
    return notice


# --- The business time zone (server.json ``time_zone``) ---------------------------------------------------------------


def business_zone_name(data: Path | None = None) -> str:
    """server.json's ``time_zone`` as given ("" when there is none)."""
    from .service import SettingsError, read_config

    try:
        value = read_config(data or paths.private_dir()).get(TIME_ZONE)
    except SettingsError:
        return ""
    return value.strip() if isinstance(value, str) else ""


def business_zone(data: Path | None = None) -> zoneinfo.ZoneInfo | None:
    """The business time zone (an IANA name, ``zoneinfo``: on Windows from the tzdata package pandas brings); None
    without one, or with a name that is not one."""
    name = business_zone_name(data)
    try:
        return zoneinfo.ZoneInfo(name) if name else None
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return None


def _offset(delta: dt.timedelta | None) -> str:
    minutes = round((delta or dt.timedelta(0)).total_seconds() / 60)
    return f"{'+' if minutes >= 0 else '-'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _machine_offset(at: dt.datetime) -> dt.timedelta | None:
    """This computer's offset from UTC at the instant ``at``, by its own time zone."""
    return at.astimezone().utcoffset()


def zone_check(data: Path | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    """{"business", "machine", "status"}: a warning when this computer's clock keeps another time than the business
    time zone, now or at another time of the year (a VM left on UTC: "today", the backup hours and the day's counts
    would follow it)."""
    name = business_zone_name(data)
    now = now or dt.datetime.now(dt.UTC)
    here = now.astimezone()
    found: dict[str, Any] = {"business": name or None,
                             "machine": f"{here.tzname()} (UTC{_offset(_machine_offset(now))})"}  # fmt: skip
    if not name:
        return {**found, "status": OK}
    try:
        zone = zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return {**found, "status": WARN, "problem": f"server.json's time_zone {name!r} is not a time zone AP Coder "
                "knows: give its IANA name, such as America/Toronto or America/Vancouver."}  # fmt: skip
    instants = [now, *(dt.datetime(now.year, month, 15, 12, tzinfo=dt.UTC) for month in (1, 7))]
    if all(_machine_offset(at) == at.astimezone(zone).utcoffset() for at in instants):
        return {**found, "status": OK}
    return {**found, "status": WARN, "problem": f"This computer's clock is on {found['machine']}, not {name} "
            f"(UTC{_offset(now.astimezone(zone).utcoffset())} now), the business time zone in server.json: \"today\", "
            "the day's counts and due dates follow this computer's clock. Set its time zone to the business's "
            "(Windows: Settings → Time & language → Date & time)."}  # fmt: skip


# --- Disks and backups ---------------------------------------------------------------------------------------------


def _device(path: Path) -> int:
    return os.stat(path).st_dev


def _database_size(store: Any) -> int:
    path = store.path
    return sum(p.stat().st_size for p in (path, path.with_name(path.name + "-wal")) if p.exists())


def data_disk(store: Any) -> dict[str, Any]:
    """The data folder's disk: a failure under ``BACKUP_RESERVE`` free (no invoice is read, no backup made)."""
    from ..core.store import backups

    usage, reserve = shutil.disk_usage(store.path.parent), backups.BACKUP_RESERVE
    found = {"free_bytes": usage.free, "total_bytes": usage.total, "reserve_bytes": reserve}
    if usage.free < reserve:
        return {**found, "status": FAIL, "kind": system_log.DISK_FULL, "problem": "The disk of the data folder has "
                f"{_size(usage.free)} free, less than the {_size(reserve)} AP Coder keeps: it reads no more invoices "
                "and makes no backup until there is room."}  # fmt: skip
    return {**found, "status": OK}


def backups_disk(store: Any) -> dict[str, Any]:
    """The backups folder's disk: a warning when the next backup won't fit (the database and the reserve)."""
    from ..core.store import backups

    folder = store.backup_dir()
    place = folder if folder.is_dir() else store.path.parent
    usage = shutil.disk_usage(place)
    need = _database_size(store) + backups.BACKUP_RESERVE
    found = {"same_disk_as_data": _device(place) == _device(store.path.parent), "free_bytes": usage.free,
             "needed_bytes": need}  # fmt: skip
    if usage.free < need:
        return {**found, "status": WARN, "problem": f"The next backup won't fit: the backups folder's disk has "
                f"{_size(usage.free)} free, and a backup needs {_size(need)} (the database and the room AP Coder "
                "keeps)."}  # fmt: skip
    return {**found, "status": OK}


def last_backup(store: Any, now: float | None = None) -> dict[str, Any]:
    """The newest complete backup's age, and whether the last one failed (when, never why: a reason may name a file)."""
    from ..core.store import backup_files
    from ..core.store.backups import BACKUP_STATUS

    newest = next((b for b in store.list_backups() if backup_files.list_path(b).is_file()), None)
    made = newest.stat().st_mtime if newest is not None else None
    age = max(0.0, ((now or time.time()) - made) / 3600) if made is not None else None
    failed = store.get_setting(BACKUP_STATUS)
    when = failed.partition(" ")[2].partition(": ")[0].replace("T", " ") if failed else ""
    found = {"newest": dt.datetime.fromtimestamp(made).astimezone().isoformat(timespec="seconds") if made else None,
             "age_hours": round(age, 1) if age is not None else None, "last_failed": when or None,
             "being_made": bool(store.backup_in_progress())}  # fmt: skip
    if failed:
        return {**found, "status": WARN, "problem": f"The last backup failed ({when}): Settings → Data & backups says "
                "why."}  # fmt: skip
    if age is None:
        return {**found, "status": WARN, "problem": "There is no backup yet."}
    if age > BACKUP_OLD_HOURS:
        return {**found, "status": WARN, "problem": f"The newest backup is {age:.0f} hours old."}
    return {**found, "status": OK}


_LOOKED: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_LOOKING: dict[tuple[str, str], threading.Thread] = {}
_LOOK_LOCK = threading.Lock()


def _look(folder: str, data: Path) -> dict[str, Any]:
    try:
        target = Path(folder).expanduser()
        if not target.is_dir():
            return {"reachable": False}
        usage = shutil.disk_usage(target)
        return {"reachable": True, "free_bytes": usage.free, "same_disk_as_data": _device(target) == _device(data)}
    except (OSError, ValueError):
        return {"reachable": False}


def look_at_folder(folder: str, data: Path, wait: float = FOLDER_WAIT) -> dict[str, Any]:
    """{"reachable", "free_bytes", "same_disk_as_data"} of ``folder``, looked at on a thread of its own, one at a time,
    waited for ``wait`` seconds and kept FOLDER_KEEP: a share gone can take a minute to say so. "reachable" None: it
    did not answer in time."""
    key = (folder, str(data))
    with _LOOK_LOCK:
        kept = _LOOKED.get(key)
        if kept is not None and time.monotonic() - kept[0] < FOLDER_KEEP:
            return kept[1]
        thread = _LOOKING.get(key)
        if thread is None or not thread.is_alive():

            def look() -> None:
                found = _look(folder, data)
                with _LOOK_LOCK:
                    _LOOKED[key] = (time.monotonic(), found)

            thread = _LOOKING[key] = threading.Thread(target=look, name="ap-coder-folder-look", daemon=True)
            thread.start()
    thread.join(wait)
    with _LOOK_LOCK:
        kept = _LOOKED.get(key)
    return kept[1] if kept is not None and not thread.is_alive() else {"reachable": None}


def forget() -> None:
    """What was looked at forgotten (tests)."""
    with _LOOK_LOCK:
        _LOOKED.clear()
    with _NOTICES_LOCK:
        _NOTICES.clear()


def second_folder(store: Any, wait: float = FOLDER_WAIT) -> dict[str, Any]:
    """The second backup folder on the server: a warning when there is none, it can't be reached, or it is on the
    database's disk (it would not survive that disk's failure)."""
    folder = store.get_setting("backup_copy_dir").strip()
    if not folder:
        return {"set": False, "status": WARN, "problem": "There is no second backup folder: the backups are on the "
                "data folder's disk, so a failure of that disk loses them with the database. An Admin sets one in "
                "Settings → Data & backups."}  # fmt: skip
    found = {"set": True, **look_at_folder(folder, store.path.parent, wait)}
    if found["reachable"] is None:
        return {**found, "status": WARN, "problem": f"The second backup folder did not answer within {wait:g} s."}
    if not found["reachable"]:
        return {**found, "status": WARN, "problem": "The second backup folder can't be reached from the server."}
    if found["same_disk_as_data"]:
        return {**found, "status": WARN, "problem": SAME_DISK}
    return {**found, "status": OK}


# --- The server's processes, queues and model servers --------------------------------------------------------------


def beat(store: Any) -> None:
    """The background process's heartbeat (``server.run_background``, every 30 s); a busy database: the next one."""
    with contextlib.suppress(Exception):  # (well within STALE_SECONDS)
        store.beat(HEARTBEAT)


def background(store: Any, processes: int) -> dict[str, Any]:
    """The server's background process (several web workers): a failure when its heartbeat is STALE_SECONDS old."""
    if processes <= 1:
        return {"in_this_process": True, "status": OK}
    age = store.heartbeat_age(HEARTBEAT)
    found = {"heartbeat_age_seconds": round(age, 1) if age is not None else None}
    if age is not None and age <= STALE_SECONDS:
        return {**found, "status": OK}
    since = f"for {age / 60:.0f} minutes" if age is not None else "within the last day"
    return {**found, "status": FAIL, "kind": system_log.PROCESS_STOPPED, "problem": "The server's background process "
            f"has not said it is alive {since}: the page reader, the AI reviewer, JD Edwards and the daily backup "
            "wait. The service log says why (docs/SERVER.md, section 6)."}  # fmt: skip


def capture(store: Any, processes: int) -> dict[str, Any]:
    """The server's capture processes: a failure when none runs, a warning when fewer than it starts."""
    if processes <= 1:
        return {"in_this_process": True, "status": OK}
    from ..web.server import capture_processes

    alive, started = store.capture_processes(), capture_processes()
    found = {"alive": alive, "started": started}
    if alive == 0:
        return {**found, "status": FAIL, "kind": system_log.PROCESS_STOPPED, "problem": "No capture process is "
                "running: the invoices brought in wait in the import queue, unread."}  # fmt: skip
    if alive < started:
        return {**found, "status": WARN, "problem": f"{alive} of the server's {started} capture processes are running."}
    return {**found, "status": OK}


def _waited(depth: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    """A queue's depth with how long its oldest has waited (seconds), from when it came in (local time)."""
    oldest = depth.pop("oldest", None)
    try:
        wait = max(0, round((now - dt.datetime.fromisoformat(oldest)).total_seconds())) if oldest else None
    except (TypeError, ValueError):
        wait = None
    return {**depth, "oldest_wait_seconds": wait}


def imports(store: Any) -> dict[str, Any]:
    return _waited(store.import_queue_depth(), dt.datetime.now())


def page_reader(store: Any) -> dict[str, Any]:
    return _waited(store.page_queue_depth(), dt.datetime.now())


def model_servers(settings: Any) -> dict[str, Any]:
    """Each model server with a backup (D-180): where its breaker sends the calls (a warning while on the backup)."""
    from ..ai import local_llm
    from ..ai.page_reader import reader_failover

    chat = local_llm.chat_failover(settings.llm) if local_llm.resolve_provider(settings) == "local" else None
    pages = reader_failover(settings)
    named = {"model_server": chat} if chat is not None and chat is pages else {"chat_model": chat, "page_reader": pages}
    found: dict[str, Any] = {"status": OK}
    for name, breaker in named.items():
        if breaker is None:
            continue
        state = breaker.state()
        since = dt.datetime.fromtimestamp(state["since"]).astimezone() if state.get("since") else None
        found[name] = {"on": state["on"], "since": since.isoformat(timespec="seconds") if since else None,
                       "moves": int(state.get("moves") or 0)}  # fmt: skip
        if state["on"] == local_llm.BACKUP:
            when = f" since {since:%Y-%m-%d %H:%M}" if since else ""
            found["status"] = WARN
            found.setdefault("problem", f"The calls to {breaker.title} go to its backup{when}: the main one didn't "
                             "answer (Settings → Reading → For IT).")  # fmt: skip
    return found


# --- What /readyz answers --------------------------------------------------------------------------------------------


def database(store: Any) -> dict[str, Any]:
    store.get_setting("schema_version")  # (raises sqlite3.Error when the database can't be read)
    return {"status": OK}


def readiness(store: Any, settings: Any, *, server: bool, processes: int = 1, certfile: str | None = None,
              insecure_http: bool = False) -> dict[str, Any]:  # fmt: skip
    """What ``GET /readyz`` answers (module docstring): ``ready`` (no check failed), the overall ``status``, the
    ``problems`` in words, and each check. ``server``: the company server's checks too (the certificate, the second
    backup folder, the time zone); ``processes``: its web workers (more than one: a background process and capture
    processes beside them); ``certfile``: the HTTPS certificate it serves. On the server, each failure (a full disk,
    a process stopped, the certificate) is told to the system's log too (``system_log``, once an hour)."""
    checks: dict[str, Callable[[], dict[str, Any]]] = {
        "database": lambda: database(store),
        "data_disk": lambda: data_disk(store),
        "backups_disk": lambda: backups_disk(store),
        "backup": lambda: last_backup(store),
    }
    if server:
        checks["second_backup_folder"] = lambda: second_folder(store)
        if certfile:
            checks["certificate"] = lambda: certificate_check(certfile)
        elif insecure_http:
            checks["certificate"] = lambda: {"https": False, "status": WARN, "problem": "AP Coder runs without HTTPS "
                                             "(--insecure-http): passwords cross the network unencrypted."}  # fmt: skip
        checks["time_zone"] = lambda: zone_check()
    checks.update({
        "background": lambda: background(store, processes),
        "capture": lambda: capture(store, processes),
        "imports": lambda: imports(store),
        "page_reader": lambda: page_reader(store),
        "model_servers": lambda: model_servers(settings),
    })  # fmt: skip
    body: dict[str, Any] = {}
    problems: list[dict[str, str]] = []
    for name, check in checks.items():
        try:
            found = check()
        except sqlite3.Error:
            found = {"status": FAIL, "problem": "The database can't be read just now."}
        except Exception as exc:  # noqa: BLE001 - a check that can't be made is said, never a 500
            found = {"status": WARN, "problem": f"The {name.replace('_', ' ')} check could not be made "
                     f"({type(exc).__name__})."}  # fmt: skip
        kind, problem = found.pop("kind", 0), found.pop("problem", "")
        if problem:
            problems.append({"check": name, "status": found["status"], "text": problem})
            if server and name == "certificate":
                tell_certificate({**found, "problem": problem})
            elif server and kind and found["status"] == FAIL:
                system_log.report(kind, problem, key=f"readyz {name}")
        body[name] = found
    statuses = {found.get("status") for found in body.values()}
    status = FAIL if FAIL in statuses else WARN if WARN in statuses else OK
    return {"ready": status != FAIL, "status": status, "app": "ap-coder", "version": __version__, "server": server,
            "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "problems": problems,
            **body}  # fmt: skip


class Kept:
    """An answer worked out at most once every ``every`` seconds: callers meanwhile wait for it, then share it."""

    def __init__(self, every: float = READY_SECONDS) -> None:
        self.every = every
        self._lock = threading.Lock()
        self._value: Any = None
        self._at = float("-inf")

    def get(self, make: Callable[[], Any]) -> Any:
        with self._lock:
            if self._value is None or time.monotonic() - self._at >= self.every:
                self._value, self._at = make(), time.monotonic()
            return self._value


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(str(host or "").strip().strip("[]"))
    except ValueError:
        return None
    return getattr(ip, "ipv4_mapped", None) or ip


def monitor_config(data: Path | None = None) -> tuple[str, list[Any]]:
    """server.json's monitor token ("" when none, or shorter than MIN_TOKEN) and monitor addresses (each an address
    or a network, "10.1.2.0/24"; one that is neither is left out)."""
    from .service import SettingsError, read_config

    try:
        found = read_config(data or paths.private_dir())
    except SettingsError:
        return "", []
    token = found.get(MONITOR_TOKEN)
    token = token.strip() if isinstance(token, str) and len(token.strip()) >= MIN_TOKEN else ""
    given = found.get(MONITOR_ADDRESSES) or []
    listed = re.split(r"[,;\s]+", given) if isinstance(given, str) else given if isinstance(given, list) else []
    networks = []
    for entry in listed:
        try:
            networks.append(ipaddress.ip_network(str(entry).strip(), strict=False))
        except ValueError:
            continue
    return token, networks


def monitor_allowed(client: str, headers: Mapping[str, str], *, proxy: str | None, data: Path | None = None) -> bool:
    """Whether the server answers /readyz to ``client``: its own console (a loopback address with no ``--proxy``, as
    the login page has it), a monitor giving server.json's ``monitor_token`` (``TOKEN_HEADER``, or ``Authorization:
    Bearer``), or one at an address of its ``monitor_addresses``."""
    ip = _ip(client)
    if ip is not None and ip.is_loopback and not proxy:
        return True
    token, networks = monitor_config(data)
    given = (headers.get(TOKEN_HEADER) or "").strip()
    if not given and (headers.get("authorization") or "").lower().startswith("bearer "):
        given = headers["authorization"][7:].strip()
    if token and given and hmac.compare_digest(given.encode(), token.encode()):
        return True
    return ip is not None and any(ip.version == net.version and ip in net for net in networks)


# --- The doctor on the server ---------------------------------------------------------------------------------------


def _row(area: str, found: dict[str, Any], fine: str) -> tuple[str, str, str]:
    return area, DOCTOR_STATUS[found["status"]], found.get("problem") or fine


def server_rows(data: Path) -> list[tuple[str, str, str]]:
    """The doctor's lines on the server (D-187): when its HTTPS certificate expires (when the file is readable: the
    service's own line says otherwise), and its time zone against the business's."""
    from .service import SettingsError, settings

    rows = []
    try:
        s = settings(data)
    except SettingsError:  # (its own line says why)
        s = None
    if s is not None and s.https and Path(s.certfile).is_file():
        found = certificate_check(s.certfile)
        fine = f"expires on {found.get('expires', '')[:10]}, in {found.get('days_left')} days"
        rows.append(_row("server HTTPS certificate expiry", found, fine))
    found = zone_check(data)
    if found["business"] is None:
        unset = (f'not set: add "{TIME_ZONE}" (the business\'s, such as "America/Toronto") to server.json, so this '
                 f"computer's clock, {found['machine']}, is checked against it")  # fmt: skip
        rows.append(("server time zone", "SKIP", unset))
    else:
        rows.append(_row("server time zone", found, f"{found['business']}: this computer's clock keeps its time"))
    return rows


def store_rows(store: Any) -> list[tuple[str, str, str]]:
    """The doctor's lines on the server about its data (D-187): the data folder's disk, the newest backup, the second
    backup folder (waited for longer than /readyz does)."""
    disk = data_disk(store)
    backup = last_backup(store)
    copy = second_folder(store, wait=10.0)
    newest = f"the newest {backup['age_hours']} hours old" if backup.get("age_hours") is not None else ""
    return [
        _row("data folder disk", disk, f"{_size(disk['free_bytes'])} free of {_size(disk['total_bytes'])}"),
        _row("backups", backup, newest),
        _row("second backup folder", copy, f"reachable, on another disk, {_size(copy.get('free_bytes') or 0)} free"),
    ]
