"""Backups and restore."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from ..paths import pid_alive
from . import backup_files, dialect
from ._base import (
    _NOT_FOUND,
    _RESTORING,
    _RESTORING_LOCK,
    _SCHEMA,
    ANSWER_WANTS,
    CHART_TABLES,
    SCHEMA_VERSION,
    _forget_learning,
    _now,
    _store_class,
)
from .backup_files import Retention
from .migrations import _answer_work
from .postings import carry_postings, saved_postings
from .summary import SUMMARY_TRIGGERS, make_triggers
from .users import add_columns, carry_accounts, saved_accounts

# A daily backup, the ones pruned: ap_coder-YYYYMMDD-HHMMSS, and -2, -3 when another was made in the same second.
# A labelled one (-manual, -before-restore, -before-update-...) is kept.
DAILY = re.compile(r"ap_coder-\d{8}-\d{6}(-\d+)?")
# Left free on the backups' disk once a backup is made: a full disk stops every write to the database, for everyone.
BACKUP_RESERVE = 512 * 1024 * 1024
BACKUP_STATUS = "backup_status"  # the last backup that failed: "failed <when>: <why>" ("" once one works again)


@dataclass
class Restored:
    """What a restore did: the backup of the state it replaced (``safety``), and what it did with the files."""

    safety: Path
    files: backup_files.FilesRestored


BACKUP_HOURS_ENV = "AP_BACKUP_HOURS"  # when the daily backup may start ("1-5"); unset: any time (``backup_window``)
MISSED_WINDOW_HOURS = 30  # a daily backup this old is made at once, whatever the hours: a day is never skipped
RESTORE_HOURS = 2  # a restore marker older than this is not believed (a restore takes minutes, not hours)
# The time of day the daily backup is made, "HH:MM" (Settings → Data & backups, D-218): never saved, BACKUP_AT_DEFAULT;
# BACKUP_AT_OFF, no set time: once a day when AP Coder starts, as before.
BACKUP_AT, BACKUP_AT_DEFAULT, BACKUP_AT_OFF = "backup_at", "17:30", "off"
BACKUP_LAST = "backup_last"  # the last backup made at its set time, as JSON: {"at", "file", "why"}
BACKUP_AT_CHANGED = "backup_at_changed"  # when the set time was last saved (this computer's clock): ``_why_then``
ON_TIME = dt.timedelta(minutes=10)  # a backup started this long after its time was made at its time


def restore_marker(db: Path) -> Path:
    """``<database>.restoring``, beside the database (never in it: it is the file being replaced) while a restore
    runs: {"pid", "started", "backup"}. The background workers wait, and on the company server every web worker
    answers the others that AP Coder is being restored (``web/maintenance.py``, D-188)."""
    return db.with_name(db.name + ".restoring")


def restore_under_way(db: Path) -> dict | None:
    """The restore into ``db`` under way, in this process or another ({"pid", "started", "backup"}), None when none
    is: no marker, one older than RESTORE_HOURS, or one whose process ended (it crashed: ``clear_left_restore``)."""
    marker = restore_marker(db)
    try:
        text = marker.read_text(encoding="utf-8")
        fresh = time.time() - marker.stat().st_mtime < RESTORE_HOURS * 3600
        found = json.loads(text or "0")
    except (OSError, ValueError):
        return None
    found = {"pid": found} if isinstance(found, int) else found  # (the process's id alone, as written before D-188)
    if not isinstance(found, dict) or not fresh:
        return None
    try:
        pid = int(found.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    return {**found, "pid": pid} if pid == os.getpid() or pid_alive(pid) else None


def clear_left_restore(db: Path) -> str:
    """A restore marker left by a restore that never ended (AP Coder stopped during it: a crash, a power cut),
    removed as AP Coder starts: what to say in the log ("" when none was left). Kept while a restore still holds
    the backups' lock (``backup_files.exclusive``): one from the console, run as the service starts."""
    marker = restore_marker(db)
    if not marker.exists():
        return ""
    try:
        with backup_files.exclusive(db.parent):
            try:
                found = json.loads(marker.read_text(encoding="utf-8") or "0")
            except (OSError, ValueError):
                found = {}
            marker.unlink(missing_ok=True)
    except backup_files.Busy:
        return ""
    found = found if isinstance(found, dict) else {}
    which = f" of {found['backup']}" if found.get("backup") else ""
    when = f", started {str(found['started'])[:16].replace('T', ' ')}," if found.get("started") else ""
    return (f"A restore{which}{when} did not finish: AP Coder stopped during it. The state before it was backed up "
            "first (backups, ...-before-restore.db): check Settings → Data & backups, and restore again if needed. "
            f"Its marker {marker.name} was removed.")  # fmt: skip


def backup_window(text: str | None) -> tuple[int, int] | None:
    """AP_BACKUP_HOURS as (start, end) hours of the business time zone (``_business_hour``; without one, of the
    server's clock): "1-5" from 1 to 5 in the morning, "22-4" across midnight. None when it is unset or can't be
    read: the daily backup starts at any time, as before."""
    found = re.fullmatch(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*", text or "")
    if not found:
        return None
    start, end = int(found.group(1)), int(found.group(2))
    if start > 23 or end > 24 or start == end % 24:
        return None
    return start, end


def _in_window(hour: int, window: tuple[int, int]) -> bool:
    start, end = window
    return start <= hour < end if start < end else hour >= start or hour < end


def backup_time(text: str | None) -> tuple[int, int] | None:
    """A time of day as written ("17:30", "7:05") as (hour, minute); None when it is not one."""
    found = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text or "")
    if not found or int(found.group(1)) > 23 or int(found.group(2)) > 59:
        return None
    return int(found.group(1)), int(found.group(2))


def backup_due(now: dt.datetime, at: tuple[int, int]) -> dt.datetime:
    """The last moment the clock showed ``at`` (hour, minute), at ``now`` or before, on this computer's clock as
    ``now`` is: the time is the business time zone's (server.json's ``time_zone``, D-187) where there is one, so a
    server left on UTC backs up at the end of the business's day. The next one is a day after it."""
    from ...ops.health import business_zone

    zone = business_zone()
    there = now.astimezone(zone) if zone is not None else now
    due = there.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    if due > there:
        due -= dt.timedelta(days=1)
    return due.astimezone().replace(tzinfo=None) if zone is not None else due


def next_backup_due(now: dt.datetime, at: tuple[int, int]) -> dt.datetime:
    """The next moment the clock shows ``at``, after ``now`` (``backup_due``'s clock and time zone)."""
    return backup_due(now + dt.timedelta(days=1), at)


def _why_then(now: dt.datetime, due: dt.datetime, at: tuple[int, int], started: bool, first: bool,
              changed: dt.datetime | None = None) -> str:  # fmt: skip
    """Why a backup with a set time was made when it was, for Settings → Data & backups. ``changed``: when the time
    was last saved; after the clock last showed it, the change is why (a time moved to one already passed, B-2295),
    said on the clock the time itself is on (the business time zone's, where there is one)."""
    said = f"{at[0]:02d}:{at[1]:02d}"
    if changed is not None and due < changed <= now:
        from ...ops.health import business_zone

        zone = business_zone()
        there = changed.astimezone(zone) if zone is not None else changed
        return f"made after the time was changed to {said} at {there:%H:%M}"
    if now - due <= ON_TIME:
        return f"made at its time, {said}"
    if first:
        return "made at start: there was no backup yet" if started else "the first backup"
    if started:
        return f"made at start: AP Coder was not running at {said}"
    return f"made late: this computer was asleep or busy at {said}"


def _business_hour(now: dt.datetime) -> int:
    """``now`` (the server's clock)'s hour in the business time zone, server.json's ``time_zone`` (D-187): a server
    left on UTC still backs up in the night of the business. Its own hour without one (a laptop)."""
    from ...ops.health import business_zone

    zone = business_zone()
    return now.astimezone(zone).hour if zone is not None else now.hour


def _size(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def _room_refusal(database: Path, folder: Path) -> str:
    """Why a backup of ``database`` into ``folder`` must not start, "" when it may: the copy (the database with its
    write-ahead log) and ``BACKUP_RESERVE`` more must fit in that disk's free space (a year's database on the company
    server is 7-9 GB). "" when the space can't be read: the backup is tried, as before, and one cut short by a full
    disk leaves nothing behind (``backup_to``)."""
    try:
        size = sum(p.stat().st_size for p in (database, database.with_name(database.name + "-wal")) if p.exists())
        folder.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(folder).free
    except OSError:
        return ""
    need = size + BACKUP_RESERVE
    if free >= need:
        return ""
    return (f"not enough free space for a backup ({_size(free)} free, {_size(need)} needed: the copy and "
            f"{_size(BACKUP_RESERVE)} to spare); free space on the disk of the data folder")  # fmt: skip


def _daily(path: Path) -> bool:
    return DAILY.fullmatch(path.stem) is not None


# --- A database file copied or read without a Store opened on it (server-update's, D-100, D-189: a Store would
# migrate it, and it may be a newer version's database, a copy being migrated or a safety copy) ---------------------


def copy_database(source: Path, dest: Path) -> Path:
    """A consistent copy of ``source`` into ``dest`` (a new file, or the database itself to put a backup back), with
    SQLite's backup: no AP Coder code opens it, so nothing is migrated."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dest, timeout=30)
        try:
            src.backup(target)
        finally:
            target.close()
    finally:
        src.close()
    return dest


def one_file(path: Path) -> None:
    """The database file ``path`` made one self-contained file (no write-ahead log beside it)."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()


def accounts_in(db: Path) -> dict:
    """The user accounts and paused logins of the database file ``db`` (``users.saved_accounts``), read-only."""
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        return saved_accounts(conn)
    finally:
        conn.close()


def carry_accounts_into(db: Path, saved: dict) -> None:
    """``saved`` (``accounts_in``) put into the database file ``db`` (``users.carry_accounts``), in one transaction."""
    conn = sqlite3.connect(db, timeout=30)
    try:
        with conn:
            carry_accounts(conn, saved)
    finally:
        conn.close()


def setting_in(db: Path, key: str) -> str | None:
    """A setting of the database file ``db``, read-only: None when it has none or can't be read."""
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


# --- server-update's safety copy, made while AP Coder serves and brought up to date once it is stopped (D-196) --------

# In a database over 1 GiB, SQLite's lock-byte page starts at this byte: it never holds data, and on Windows bytes of
# it are locked by whoever opens the database (a read of them fails), so it is never read.
LOCK_PAGE = 1 << 30
HOLD_WAIT = 60.0  # seconds to find a moment when the whole write-ahead log is in the database file
CHUNK = 1 << 20


def _beside(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


class CopyAhead:
    """A copy of the database file ``db`` as ``part``, made while AP Coder works on it and brought up to date in a
    moment once it has stopped: server-update's safety copy, whose downtime then no longer grows with the database
    (D-196). ``start`` takes the backups' lock (``backup_files.exclusive``) and holds the file still: a read
    transaction begun once the whole write-ahead log is in the file, no writer meanwhile (the write lock held for that
    moment). While it lasts SQLite writes nothing into the file (a checkpoint stops at the oldest reader's snapshot,
    and this one's is the file itself): what is written stays in the log. The file is then copied as it is.
    ``finish``, once nothing writes: the log copied beside the copy and applied to it by SQLite's own recovery, the
    copy made one file. ``close``: the hold and the lock let go, what is left of an unfinished copy removed."""

    def __init__(self, db: Path, part: Path) -> None:
        self.db, self.part = db, part
        self.held: sqlite3.Connection | None = None
        # The database file, opened once for the copy and closed only once the hold is let go: on Linux and macOS
        # closing any handle on a file lets go of every lock this process holds on it, the hold's own (B-2036).
        self._file: BinaryIO | None = None
        self.seen: tuple[int, int] = (0, 0)
        self.caught_up = False  # (``finish`` brought it up to date: else the database was copied whole)
        self._lock = contextlib.ExitStack()

    def start(self) -> str:
        """Nothing ("") once the file is held and copied; else why it can't be held still (nothing done: the caller
        copies it whole once AP Coder is stopped). Raises OSError when the copy can't be made (a full disk), and
        ``backup_files.Busy`` while another backup or a restore runs."""
        try:
            self._lock.enter_context(backup_files.exclusive(self.db.parent))
            self.held, why = _hold_still(self.db, HOLD_WAIT)
            if self.held is None:
                self.close()
                return why
            page = dialect.page_size(self.held)
            found = self.db.stat()
            self.seen = (found.st_size, found.st_mtime_ns)
            self._remove_part()
            self.part.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self.db, "rb")  # noqa: SIM115 - closed by _let_go, after the hold (above)
            _copy_as_is(self._file, self.part, found.st_size, page)
        except BaseException:
            self.close()
            raise
        return ""

    def finish(self) -> bool:
        """The copy brought up to date with what was written since ``start``, one file on the disk (``part``). False
        when it can't be (the hold was lost, or the file was written meanwhile after all): the copy is removed and the
        database is to be copied whole, the backups' lock still held."""
        found = self.db.stat()
        if self.held is None or not self.held.in_transaction or (found.st_size, found.st_mtime_ns) != self.seen:
            self._let_go()
            self._remove_part()
            return False
        log = _beside(self.db, "-wal")
        if log.exists():
            shutil.copyfile(log, _beside(self.part, "-wal"))
        self._let_go()
        conn = sqlite3.connect(self.part)
        try:
            dialect.checkpoint(conn, "TRUNCATE")  # (SQLite's recovery reads the log as it opens)
            dialect.journal_mode(conn, "delete")
        finally:
            conn.close()
        self.caught_up = True
        return True

    def close(self) -> None:
        self._let_go()
        self._remove_part()
        self._lock.close()

    def _let_go(self) -> None:
        if self.held is not None:
            self.held.close()
            self.held = None
        if self._file is not None:  # (after the hold: B-2036)
            self._file.close()
            self._file = None

    def _remove_part(self) -> None:
        """The copy not finished, SQLite's files beside it, and the list of files made with it (server-update's)."""
        for leftover in (self.part, *(_beside(self.part, s) for s in ("-wal", "-shm", "-journal"))):
            leftover.unlink(missing_ok=True)
        backup_files.list_path(self.part).unlink(missing_ok=True)


def _hold_still(db: Path, wait: float) -> tuple[sqlite3.Connection | None, str]:
    """A read transaction on ``db`` begun once its whole write-ahead log is in the file (``CopyAhead``), and ""; or
    None and why there is none: not in WAL mode, or no such moment in ``wait`` seconds (a long write, or readers that
    kept the log from being all written into the file)."""
    held = sqlite3.connect(db, timeout=30, isolation_level=None)
    writer = sqlite3.connect(db, timeout=5, isolation_level=None)
    try:
        if dialect.journal_mode(held) != "wal":
            held.close()
            return None, "the database is not in WAL mode"
        deadline = time.monotonic() + wait
        while True:
            with contextlib.suppress(sqlite3.OperationalError):  # (a write under way for 5 s: tried again)
                dialect.begin_write(writer)  # nothing is added to the log meanwhile
                try:
                    busy, logged, written = dialect.checkpoint(held)
                    if busy == 0 and logged == written >= 0:  # the whole log is in the file
                        held.execute("BEGIN")
                        dialect.table_names(held)  # (a read: its snapshot, the file as it is now)
                        return held, ""
                finally:
                    writer.execute("ROLLBACK")
            if time.monotonic() > deadline:
                held.close()
                return None, f"it was never still for a moment in {wait:g} s"
            time.sleep(0.2)
    except BaseException:
        held.close()
        raise
    finally:
        writer.close()


def _copy_as_is(src: BinaryIO, dest: Path, size: int, page: int) -> None:
    """The first ``size`` bytes of the open file ``src`` (left open: B-2036) into the new file ``dest``, flushed to
    the disk; SQLite's lock-byte page (``LOCK_PAGE``, ``page`` bytes) written as zeros, never read."""
    src.seek(0)
    with open(dest, "xb") as out:
        done = 0
        while done < size:
            if done == LOCK_PAGE:
                out.write(bytes(page))
                done += page
                src.seek(done)
                continue
            block = src.read(min(CHUNK, size - done, LOCK_PAGE - done if done < LOCK_PAGE else CHUNK))
            if not block:
                raise OSError(f"{Path(src.name).name} ends at {done:,} bytes, not {size:,}")
            out.write(block)
            done += len(block)
        out.flush()
        os.fsync(out.fileno())


class BackupsMixin:
    # --- Backups -----------------------------------------------------------------------------------------

    def backup_to(self, dest: str | Path) -> Path:
        """A consistent copy of the database (safe while the dashboard is running). Written under a temporary name
        and renamed once complete: a copy that fails (a full disk, a folder that cannot be written) leaves nothing
        that looks like a backup, and raises OSError, as a file that cannot be written does (B-453)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        part.unlink(missing_ok=True)
        try:
            source = sqlite3.connect(self.path)
            try:
                target = sqlite3.connect(part)
                try:
                    source.backup(target)
                    # One self-contained file (not WAL, which needs -wal / -shm files beside it): it opens and
                    # restores from anywhere, also a read-only network share or USB stick.
                    target.execute("PRAGMA journal_mode=DELETE")
                finally:
                    target.close()
            finally:
                source.close()
            part.replace(dest)
        except (sqlite3.Error, OSError) as exc:
            for leftover in (part, part.with_name(part.name + "-journal")):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass
            if isinstance(exc, OSError):
                raise
            raise OSError(f"the backup {dest.name} could not be written ({exc})") from exc
        return dest

    def backup_dir(self) -> Path:
        return self.path.parent / "backups"

    def list_backups(self) -> list[Path]:
        folder = self.backup_dir()
        return sorted(folder.glob("ap_coder-*.db"), reverse=True) if folder.exists() else []

    def backup_now(self, label: str = "", actor: str | None = None, keep: Retention | None = None) -> Path:
        """The database and the files of the data folder (``backup_files``, D-143): a state that restores whole.
        A backup that fails raises OSError and is recorded (``backup_status``, shown on Settings → Data & backups), and
        on the server told to the system's log (D-187). ``keep``: the daily backups kept in the second backup folder
        (``copy_backup``). While it is made,
        ``backups/.making-<pid>-...`` says so (``backup_in_progress``: the page shows it, B-1553). Never while another
        backup or a restore runs (``backup_files.exclusive``: raises ``backup_files.Busy``, B-1559)."""
        with backup_files.exclusive(self.path.parent):
            marker = self.backup_dir() / f".making-{os.getpid()}-{uuid.uuid4().hex[:8]}"
            with contextlib.suppress(OSError):
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(_now()[:16].replace("T", " "), encoding="utf-8")
            try:
                return self._backup_now(label, actor, keep)
            finally:
                with contextlib.suppress(OSError):
                    marker.unlink()

    def backup_in_progress(self) -> str:
        """When the backup being made now started ("" when none is), in this process or another (the server's
        background process makes the daily one while a web worker draws the page)."""
        for marker in self.backup_dir().glob(".making-*"):
            try:
                pid, started = int(marker.name.split("-")[1]), marker.read_text(encoding="utf-8").strip()
            except (OSError, ValueError, IndexError):
                continue
            if pid == os.getpid() or pid_alive(pid):
                return started
            with contextlib.suppress(OSError):
                marker.unlink()  # left by a process that ended while making it
        return ""

    def _backup_now(self, label: str, actor: str | None, keep: Retention | None) -> Path:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = f"-{label}" if label else ""
        dest = self.backup_dir() / f"ap_coder-{stamp}{suffix}.db"
        n = 2
        while dest.exists():  # never overwrite a backup made in the same second
            dest = self.backup_dir() / f"ap_coder-{stamp}{suffix or '-'}{n}.db"
            n += 1
        for stale in self.backup_dir().glob("ap_coder-*.db.part"):  # left by a crash (B-453): gone after a day
            try:
                if time.time() - stale.stat().st_mtime > 86400:
                    stale.unlink()
            except OSError:
                pass
        try:
            short = _room_refusal(self.path, self.backup_dir())
            if short:
                raise OSError(short)
            made = self.backup_to(dest)
            try:
                backup_files.snapshot(made, self.path.parent)
            except OSError as exc:  # no backup without its files: not one taken for the day's (B-1557)
                made.unlink(missing_ok=True)
                backup_files.list_path(made).unlink(missing_ok=True)
                raise OSError(f"the invoice files could not be saved ({exc}), so no backup was made") from exc
        except OSError as exc:
            with contextlib.suppress(OSError, sqlite3.Error):
                self.set_setting(BACKUP_STATUS, f"failed {_now()[:16]}: {exc}")
            from ...ops import system_log

            full = bool(short) or exc.errno in backup_files.NO_ROOM  # refused before it starts, or cut short by it
            system_log.report(system_log.DISK_FULL if full else system_log.BACKUP_FAILED,
                              f"AP Coder's backup {dest.name} was not made: {exc}", key="backup")  # fmt: skip
            raise
        with contextlib.suppress(OSError, sqlite3.Error):  # (a busy database: the backup is made all the same, B-1564)
            if self.get_setting(BACKUP_STATUS):
                self.set_setting(BACKUP_STATUS, "")
        if label == "manual":
            self.log_event("backup_made", actor=actor, detail={"file": made.name})
        if label in ("", "manual") or label.startswith("before-update"):
            self.copy_backup(made, keep=keep)
        return made

    def copy_backup(self, made: Path, keep: Retention | None = None) -> Path | None:
        """Copy a backup to the second location set in Settings (e.g. a OneDrive or network folder), with the files
        it needs that are not there yet (``backup_files.copy_files``), keeping the daily copies ``keep`` says (by
        default this mode's, ``backup_files.retention``) and the files they need. A copy that fails (folder offline)
        is recorded (and on the server told to the system's log), never raised: the local backup is there either
        way."""
        folder = self.get_setting("backup_copy_dir").strip()
        if not folder:
            return None
        from ...ops.service import folder_refusal

        try:
            refused = folder_refusal(folder)  # a folder saved before IT's list, or brought back by a restore (D-109)
            if refused:
                raise OSError(refused)
            target = Path(folder).expanduser()
            if not target.is_dir():
                raise OSError(f"folder not found: {target}")
            if target.resolve() == made.parent.resolve():
                raise OSError("that is the local backups folder; choose another one")
            for stale in target.glob("ap_coder-*.db.part"):  # a copy cut short a day ago or more
                with contextlib.suppress(OSError):
                    if time.time() - stale.stat().st_mtime > 86400:
                        stale.unlink()
            # The database under a temporary name until its files and list are there: a copy cut short (the share
            # gone meanwhile) leaves no backup there that restores no file (B-1558).
            copied = target / made.name
            part = copied.with_name(copied.name + ".part")
            try:
                shutil.copy2(made, part)
                backup_files.copy_files(made, target)
                os.replace(part, copied)
            except BaseException:
                with contextlib.suppress(OSError):
                    part.unlink()
                raise
            backup_files.drop_old([p for p in target.glob("ap_coder-*.db") if _daily(p)], keep, self.path.parent)
            backup_files.prune(target)
        except OSError as exc:
            self.set_setting("backup_copy_status", f"failed {_now()[:16]}: {exc}")
            backup_files.record_copy(made, False, _now()[:16], str(exc))
            from ...ops import system_log

            system_log.report(system_log.BACKUP_FAILED, f"AP Coder's backup {made.name} was not copied to the second "
                              f"backup folder: {exc}", warning=True, key="backup copy")  # fmt: skip
            return None
        self.set_setting("backup_copy_status", f"ok {_now()[:16]}: {copied.name}")
        backup_files.record_copy(made, True, _now()[:16], str(target))
        return copied

    def backup_at(self) -> tuple[int, int] | None:
        """The time of day the daily backup is made (Settings → Data & backups; BACKUP_AT_DEFAULT until one is
        saved); None with no set time: once a day when AP Coder starts."""
        return backup_time(self.get_setting(BACKUP_AT) or BACKUP_AT_DEFAULT)

    def save_backup_at(self, time: tuple[int, int] | None, actor: str | None = None,
                       now: dt.datetime | None = None) -> None:  # fmt: skip
        """The daily backup's time of day as Settings saves it (None: no set time), and when it was saved: a backup
        the change itself makes due says so (``_why_then``)."""
        self.set_setting(BACKUP_AT, f"{time[0]:02d}:{time[1]:02d}" if time else BACKUP_AT_OFF, actor=actor)
        self.set_setting(BACKUP_AT_CHANGED, (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S"))

    def last_timed_backup(self) -> dict[str, str]:
        """The last backup made at its set time: {"at": when, "file": its name, "why": why then}; {} when none
        was."""
        found = self.get_json(BACKUP_LAST, {})
        return {k: str(v) for k, v in found.items()} if found.get("at") else {}

    def auto_backup(self, keep: Retention | None = None, min_hours: float = 20, window: tuple[int, int] | None = None,
                    now: dt.datetime | None = None, at: tuple[int, int] | None = None,
                    started: bool = False) -> Path | None:  # fmt: skip
        """Back up at most once a day (call it at start-up) and keep the daily copies ``keep`` says
        (``backup_files.drop_old``; by default the newest 14, and on the company server also one a week for 8 weeks
        and one a month for a year, D-187). Only a
        complete backup (with its list of files) is the day's: one cut short is made again at the next check (B-1557).
        None while another backup or a restore runs (B-1559). ``window`` (``backup_window``): it starts only in those
        hours of the business time zone (server.json's ``time_zone``, else the server's clock), unless the last daily
        backup is ``MISSED_WINDOW_HOURS`` old or there is none (B-1671). ``at`` (``backup_at``; without ``window``,
        whose hours IT chose): the day's backup is the one made since the clock last showed that time
        (``backup_due``), so one is made when the time comes, and at the next check when AP Coder was not running
        then (``started``: this is the check at start); why it was made then is kept (``last_timed_backup``,
        D-218)."""
        try:
            with backup_files.exclusive(self.path.parent):
                return self._auto_backup(keep, min_hours, window, now or dt.datetime.now(), at, started)
        except backup_files.Busy:
            return None

    def _timed_backup(self, keep: Retention | None, daily: list[Path], now: dt.datetime, at: tuple[int, int],
                      started: bool) -> Path | None:  # fmt: skip
        """The day's backup with a set time (``auto_backup``'s ``at``): none while one was made since that time."""
        due, first = backup_due(now, at), True
        for backup in daily:
            try:
                made = dt.datetime.fromtimestamp(backup.stat().st_mtime)
            except OSError:
                continue
            if due <= made <= now:
                return None
            first = first and made > now  # (one made in the future, the clock was ahead, is no backup yet)
        if not self.path.exists():
            return None
        made_now = self._daily_backup(keep)
        with contextlib.suppress(OSError, sqlite3.Error):  # (a busy database: the backup is made all the same)
            changed = None
            with contextlib.suppress(ValueError):  # (never saved, or not a time: no change to say)
                changed = dt.datetime.fromisoformat(self.get_setting(BACKUP_AT_CHANGED) or "")
            why = _why_then(now, due, at, started, first, changed)
            self.set_setting(BACKUP_LAST, json.dumps({"at": now.strftime("%Y-%m-%d %H:%M"), "file": made_now.name,
                                                      "why": why}))  # fmt: skip
        return made_now

    def _daily_backup(self, keep: Retention | None) -> Path:
        made = self.backup_now(keep=keep)
        backup_files.drop_old([p for p in self.list_backups() if _daily(p)], keep, self.path.parent)
        backup_files.prune(self.backup_dir())  # the files only the backups dropped needed
        return made

    def _auto_backup(self, keep: Retention | None, min_hours: float, window: tuple[int, int] | None,
                     now: dt.datetime, at: tuple[int, int] | None, started: bool) -> Path | None:  # fmt: skip
        daily = [p for p in self.list_backups() if _daily(p) and backup_files.list_path(p).is_file()]
        if at is not None and not window:
            return self._timed_backup(keep, daily, now, at, started)
        newest: dt.timedelta | None = None
        for backup in daily:  # one made in the future (the clock was ahead) is old: it never stops the daily backups
            try:
                age = now - dt.datetime.fromtimestamp(backup.stat().st_mtime)
            except OSError:
                continue
            if dt.timedelta(0) <= age < dt.timedelta(hours=min_hours):
                return None
            if age >= dt.timedelta(0) and (newest is None or age < newest):
                newest = age
        if window and not _in_window(_business_hour(now), window) and newest is not None:
            if newest < dt.timedelta(hours=MISSED_WINDOW_HOURS):
                return None  # its hours come before a day is missed
        if not self.path.exists():
            return None
        return self._daily_backup(keep)

    @staticmethod
    @contextmanager
    def _local_copy(backup: str | Path) -> Iterator[Path]:
        """A copy of ``backup`` in a temporary folder on this computer, to open: nothing is ever written next to the
        backup itself (SQLite would, for a WAL-format one), which may be on a read-only share or a UNC path."""
        import tempfile

        path = Path(backup)
        with tempfile.TemporaryDirectory(prefix="ap_coder-backup-", ignore_cleanup_errors=True) as folder:
            copy = Path(folder) / "backup.db"
            try:
                shutil.copyfile(path, copy)
                wal = path.with_name(path.name + "-wal")  # a database still open elsewhere: its latest changes
                if wal.is_file():
                    shutil.copyfile(wal, copy.with_name(copy.name + "-wal"))
            except OSError as exc:
                raise ValueError(f"{path.name} could not be opened ({exc.strerror or exc})") from None
            yield copy

    @staticmethod
    def check_backup(backup: str | Path) -> None:
        """Raise ValueError (in plain words) unless ``backup`` is an AP Coder database this version can open:
        a ``schema_version`` setting no newer than this version's, and no views or triggers but the summary's own
        (``summary.SUMMARY_TRIGGERS``, D-110, D-159; another one in a planted file would run on every later change).
        Those are known by their names, whatever SQL the version that made them gave them: a restore makes them again
        as this version does (``_own_triggers``)."""
        with _store_class()._local_copy(backup) as copy:
            _store_class()._check_backup_file(copy, Path(backup).name)

    @staticmethod
    def _check_backup_file(path: Path, name: str) -> None:
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise ValueError(f"{name} could not be opened ({exc})") from None
        try:
            # The summary's own triggers (D-110, D-159), by their names, are AP Coder's; any other is not.
            kinds = {
                r[0]
                for r in conn.execute("SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view')")
                if r[0] == "view" or r[1] not in SUMMARY_TRIGGERS
            }
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            row = (
                conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
                if "settings" in tables
                else None
            )
            columns = {r[1] for r in conn.execute("PRAGMA table_info(invoices)")}
        except sqlite3.DatabaseError:
            raise ValueError(f"{name} is not an AP Coder database (or it is damaged)") from None
        finally:
            conn.close()
        if kinds:
            raise ValueError(f"{name} contains {' and '.join(sorted(kinds))}s, which AP Coder never makes")
        # No schema_version row: a database older than the setting (the update's own safety copy of one, made
        # before migrating) is version 1 and has its shape (invoices without export_batch, added in version 3);
        # a newer one without the row is not one AP Coder made.
        first = row is None and "export_batch" not in columns
        if not {"invoices", "feedback"} <= tables or (row is None and not first):
            raise ValueError(f"{name} is not an AP Coder database")
        try:
            version = 1 if first else int(row[0])
        except (TypeError, ValueError):
            raise ValueError(f"{name} is not an AP Coder database") from None
        if version > SCHEMA_VERSION:
            raise ValueError(f"{name} was made by a newer version of AP Coder: update AP Coder first")

    @staticmethod
    def _own_triggers(path: Path, name: str) -> None:
        """The summary's triggers in the restore's own copy of a backup (``_local_copy``, checked) made again as this
        version makes them (``summary.make_triggers``), before the copy goes into the database: the SQL the backup
        gave them never runs here. Raises ValueError when one of them cannot be made (its table is not there).
        The copy is the database for minutes before it is migrated (the data folder's files are copied first), the web
        workers answering meanwhile. So it gets the counts' triggers its tables allow (an older backup may have none):
        another worker's change is counted; and a new ``summary_id``: nothing kept on the database as it was is taken
        when a count comes back to a number kept then (B-1720). The migration gives it another."""
        conn = sqlite3.connect(path)
        try:
            make_triggers(conn, missing=False, counts=True)
            if "report_answers" in dialect.table_names(
                conn
            ):  # (the kept answers read as this version reads them: B-2045)
                _answer_work(None, conn)
            conn.execute(ANSWER_WANTS)  # (the settled answers asked, D-198: a backup from before has none)
            for table in CHART_TABLES:  # (the chart of accounts, D-209: the reference data asks them)
                conn.execute(table)
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settings'").fetchone():
                conn.execute("INSERT INTO settings (key, value) VALUES ('summary_id', ?) ON CONFLICT(key) DO UPDATE "
                             "SET value = excluded.value", (uuid.uuid4().hex,))  # fmt: skip
            conn.commit()
        except sqlite3.Error as exc:
            raise ValueError(f"{name} is not an AP Coder database ({exc})") from None
        finally:
            conn.close()

    def row_identity(self, invoice_id: int) -> tuple[Any, ...] | None:
        """What tells this invoice from another one under the same number (its file's SHA-256 and when it came in), or
        None when there is none: a background worker takes it before its long work and writes its result only onto
        the same invoice (``expect``), not onto whatever a restored backup holds under that number."""
        with self._conn() as conn:
            row = conn.execute("SELECT file_sha256, created_at FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        return (row["file_sha256"], row["created_at"]) if row else None

    def restoring(self) -> bool:
        """A backup is being restored into this database: the background workers wait. In this process, or in
        another one (``<database>.restoring`` beside it, with that process's id: the company server's web worker
        restoring while its background process reads, D-111)."""
        with _RESTORING_LOCK:
            if str(self.path.resolve()) in _RESTORING:
                return True
        found = restore_under_way(self.path)
        return found is not None and found["pid"] != os.getpid()

    def restore_from(self, backup: str | Path, *, keep_accounts: bool = False) -> Path:
        """``restore_state``; the safety backup of the state replaced."""
        return self.restore_state(backup, keep_accounts=keep_accounts).safety

    def restore_state(self, backup: str | Path, *, keep_accounts: bool = False) -> Restored:
        """``_restore_from`` with the background workers paused meanwhile (``restoring``), never while a backup or
        another restore runs (``backup_files.exclusive``: raises ``backup_files.Busy``, with nothing changed, B-1559;
        so no pruning removes the backup being restored either)."""
        with backup_files.exclusive(self.path.parent):
            return self._restore_state(backup, keep_accounts=keep_accounts)

    def _restore_state(self, backup: str | Path, *, keep_accounts: bool) -> Restored:
        key = str(self.path.resolve())
        marker = restore_marker(self.path)
        with _RESTORING_LOCK:
            _RESTORING.add(key)
        try:
            with contextlib.suppress(OSError):  # (also what every web worker answers others meanwhile: D-188)
                marker.write_text(json.dumps({"pid": os.getpid(), "started": _now(), "backup": Path(backup).name}),
                                  encoding="utf-8")  # fmt: skip
            restored = self._restore_from(backup, keep_accounts=keep_accounts)
            # What was made from the replaced invoices goes with them: the company server's Spend workbook
            # (web/pages/spend.py, workbook_path).
            shutil.rmtree(self.path.parent / ".cache" / "downloads", ignore_errors=True)
            return restored
        finally:
            with contextlib.suppress(OSError):
                marker.unlink()
            with _RESTORING_LOCK:
                _RESTORING.discard(key)

    @staticmethod
    def _rules_first(copy: Path, accounts: dict, keep_accounts: bool) -> str:
        """A restore's rules for the accounts, the sessions and the second backup folder (``_restore_from``) applied
        to its own copy of the backup, before the copy becomes the database: the files are copied for minutes after
        that, and a restore stopped meanwhile (the power, a kill) must not leave an account turned off since the
        backup on again, a session the backup knew, or its folder. The second backup folder the backup named."""
        conn = sqlite3.connect(copy)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                tables = dialect.table_names(conn)
                if keep_accounts:
                    add_columns(conn)  # (an older backup's accounts table: every column of the accounts kept)
                    carry_accounts(conn, accounts)
                elif "sessions" in tables:
                    conn.execute("DELETE FROM sessions")
                if "settings" not in tables:
                    return ""
                named = conn.execute("SELECT value FROM settings WHERE key = 'backup_copy_dir'").fetchone()
                conn.execute(
                    "DELETE FROM settings WHERE key IN ('backup_copy_dir', 'backup_copy_status', ?)", (BACKUP_STATUS,)
                )
        finally:
            conn.close()
        return str(named[0]).strip() if named else ""

    def _restore_from(self, backup: str | Path, *, keep_accounts: bool = False) -> Restored:
        """Put AP Coder back as it was at a backup: the database, and the files of the data folder as the backup lists
        them (``backup_files.restore_files``; a file there now and not then is moved aside). The current state is
        backed up first (``safety``). Every session ends (everyone logs in again); ``keep_accounts`` (the server,
        D-101): the accounts and the lockouts stay as they are now, not as the backup has them
        (``users.carry_accounts``).
        Raises ValueError, with nothing changed, when the file is not an AP Coder backup (``check_backup``).
        The second backup folder is cleared after a restore: a backup file must not choose where later
        backups are copied (set it again in Settings)."""
        backup = Path(backup)
        # The files here now: one written after (an upload meanwhile) is never moved aside (B-1559, B-1566).
        present = set(backup_files.data_files(self.path.parent))
        with self._local_copy(backup) as copy:  # read from this computer, never written next to the backup
            self._check_backup_file(copy, backup.name)
            self._own_triggers(copy, backup.name)  # (only the copy is written to)
            copy_dir = self.get_setting("backup_copy_dir").strip()
            safety = self.backup_now("before-restore")
            with self._conn() as conn:
                accounts = saved_accounts(conn) if keep_accounts else {}
                postings = saved_postings(conn)  # the JD Edwards vouchers: never forgotten by a restore (D-105)
            restored_dir = self._rules_first(copy, accounts, keep_accounts)
            source = sqlite3.connect(copy)
            target = sqlite3.connect(self.path)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        from ...ops.service import folder_refusal

        # A file missing or damaged in the backup's folder is taken from the second one, when it has it.
        also = [Path(copy_dir).expanduser()] if copy_dir and not folder_refusal(copy_dir) else []
        try:  # (before the ledger is merged into the database below: on a new computer it comes from the backup)
            files = backup_files.restore_files(backup, self.path.parent, also=also, present=present)
        except OSError as exc:  # said, never a database restored without the accounts and sessions rules below
            files = backup_files.FilesRestored(error=str(exc))
        _NOT_FOUND.clear()  # a file looked for a moment ago may be back
        with self._conn() as conn:
            conn.executescript(_SCHEMA)  # an older backup may lack newer tables...
            self._migrate(conn)  # ...and columns
            if keep_accounts:
                carry_accounts(conn, accounts)
            else:
                conn.execute("DELETE FROM sessions")  # no one stays logged in by a session the backup knew
            carry_postings(conn, self.path, postings)
            backup_files.repoint_invoices(conn, self.path.parent)  # restored onto a data folder at another path
            conn.execute(
                "DELETE FROM settings WHERE key IN ('backup_copy_dir', 'backup_copy_status', ?)", (BACKUP_STATUS,)
            )
            # What the pages share (``summary_memo``) was worked out on the database as it was: the summary gets a new
            # id, so every process works it out again, whatever the restored generations' numbers (B-1310).
            conn.execute("INSERT INTO settings (key, value) VALUES ('summary_id', ?) ON CONFLICT(key) DO UPDATE SET "
                         "value = excluded.value", (uuid.uuid4().hex,))  # fmt: skip
            detail = {"file": backup.name, "safety_copy": safety.name, "files": files.words()}
            if copy_dir or restored_dir:
                detail["note"] = "second backup folder cleared: set it again in Settings (Data & backups)"
            self._log(conn, "backup_restored", detail=detail)
        _forget_learning(self.path)  # the lessons and learning counts kept for the database as it was (B-786)
        return Restored(safety, files)
