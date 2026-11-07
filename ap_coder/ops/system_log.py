"""What goes wrong on the company server, told to the computer's own log as well as AP Coder's (D-187), where IT's
monitoring already looks (the Event Viewer, SCOM, journald): a backup that failed or was refused for a full disk, the
background process or a capture process that stopped, the HTTPS certificate near its end or past it, a model
server's calls moved to its backup.

* **Windows**: the Application log, through ``eventcreate`` (part of Windows: no package), source "AP Coder", one
  event number for each kind (``BACKUP_FAILED``...), so a monitoring rule can pick one. Windows lets only an
  administrator make a new source: IT runs ``eventcreate`` once as one (docs/SERVER.md, section 6); until then the
  line is in AP Coder's own logs only.
* **macOS and Linux**: syslog (``/var/run/syslog``, ``/dev/log``) as ``ap-coder``; nothing when neither is there.

The same message is told at most once an hour by each process (``EVERY``); on a laptop, nothing is told."""

from __future__ import annotations

import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time

from .offline import server_mode

SOURCE = "AP Coder"  # the Event Log's source
TAG = "ap-coder"  # syslog's
EVERY = 3600.0  # seconds before the same message is told again by this process
MOST = 900  # characters of a message (eventcreate takes a line, not a page)
# The event numbers (eventcreate takes 1 to 1000).
BACKUP_FAILED, DISK_FULL, PROCESS_STOPPED, CERTIFICATE, MODEL_BACKUP = 101, 102, 103, 104, 105
SOCKET = "/var/run/syslog" if sys.platform == "darwin" else "/dev/log"

log = logging.getLogger(__name__)
_told: dict[str, float] = {}  # what was told in the last hour (EVERY), and when (time.monotonic)
_lock = threading.Lock()


def report(kind: int, message: str, *, warning: bool = False, key: str = "") -> bool:
    """Tell the system's log ``message`` (event ``kind``; an error, or a ``warning``): on the company server only,
    the same ``key`` (default: the message) at most once an hour by this process. Never raises. True when told."""
    if not server_mode():
        return False
    text = " ".join(str(message).split())[:MOST]
    key = f"{kind}:{key or text}"
    now = time.monotonic()
    with _lock:
        last = _told.get(key)
        if last is not None and now - last < EVERY:
            return False
        for old in [k for k, at in _told.items() if now - at >= EVERY]:  # told again anyway: forgotten (B-2027)
            del _told[old]
        _told[key] = now
    try:
        _send(kind, text, warning)
    except Exception:  # noqa: BLE001 - the system's log refusing never stops AP Coder
        log.debug("the system's log was not told", exc_info=True)
    return True


def forget() -> None:
    """What was told forgotten (tests)."""
    with _lock:
        _told.clear()


def _send(kind: int, text: str, warning: bool) -> None:
    if os.name == "nt":
        _event_log(kind, text, warning)
    else:
        _syslog(kind, text, warning)


def _event_log(kind: int, text: str, warning: bool) -> None:
    """The Windows Application log (``eventcreate``; its quotes taken out: the command line is its one argument)."""
    command = ["eventcreate", "/L", "APPLICATION", "/T", "WARNING" if warning else "ERROR", "/SO", SOURCE,
               "/ID", str(kind), "/D", text.replace('"', "'")]  # fmt: skip
    subprocess.run(command, capture_output=True, timeout=10, check=False,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # fmt: skip


def _syslog(kind: int, text: str, warning: bool) -> None:
    """syslog's socket, when this computer has one (a container may not: then nothing)."""
    if not os.path.exists(SOCKET):
        return
    handler = logging.handlers.SysLogHandler(address=SOCKET)
    handler.handleError = lambda record: None  # type: ignore[method-assign]  # (quiet, as its socket is gone)
    handler.ident = f"{TAG}[{os.getpid()}]: "
    try:
        level = logging.WARNING if warning else logging.ERROR
        handler.emit(logging.LogRecord(TAG, level, __file__, 0, f"[{kind}] {text}", None, None))
    finally:
        handler.close()
