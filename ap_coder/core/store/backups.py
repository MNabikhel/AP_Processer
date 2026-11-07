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
from typing import Any

from . import backup_files
from ._base import (
    _NOT_FOUND,
    _RESTORING,
    _RESTORING_LOCK,
    _SCHEMA,
    SCHEMA_VERSION,
    _forget_learning,
    _now,
    _store_class,
)
from .backup_files import Retention
from .postings import carry_postings, saved_postings
from .summary import SUMMARY_TRIGGERS, make_triggers
from .users import carry_accounts, saved_accounts

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
    from ap_coder.ai.page_worker import _pid_alive

    return {**found, "pid": pid} if pid == os.getpid() or _pid_alive(pid) else None


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
        from ap_coder.ai.page_worker import _pid_alive

        for marker in self.backup_dir().glob(".making-*"):
            try:
                pid, started = int(marker.name.split("-")[1]), marker.read_text(encoding="utf-8").strip()
            except (OSError, ValueError, IndexError):
                continue
            if pid == os.getpid() or _pid_alive(pid):
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

    def auto_backup(self, keep: Retention | None = None, min_hours: float = 20, window: tuple[int, int] | None = None,
                    now: dt.datetime | None = None) -> Path | None:  # fmt: skip
        """Back up at most once a day (call it at start-up) and keep the daily copies ``keep`` says
        (``backup_files.drop_old``; by default the newest 14, and on the company server also one a week for 8 weeks
        and one a month for a year, D-187). Only a
        complete backup (with its list of files) is the day's: one cut short is made again at the next check (B-1557).
        None while another backup or a restore runs (B-1559). ``window`` (``backup_window``): it starts only in those
        hours of the business time zone (server.json's ``time_zone``, else the server's clock), unless the last daily
        backup is ``MISSED_WINDOW_HOURS`` old or there is none (B-1671)."""
        try:
            with backup_files.exclusive(self.path.parent):
                return self._auto_backup(keep, min_hours, window, now or dt.datetime.now())
        except backup_files.Busy:
            return None

    def _auto_backup(self, keep: Retention | None, min_hours: float, window: tuple[int, int] | None,
                     now: dt.datetime) -> Path | None:  # fmt: skip
        daily = [p for p in self.list_backups() if _daily(p) and backup_files.list_path(p).is_file()]
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
        made = self.backup_now(keep=keep)
        backup_files.drop_old([p for p in self.list_backups() if _daily(p)], keep, self.path.parent)
        backup_files.prune(self.backup_dir())  # the files only the backups dropped needed
        return made

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
            restored_dir = conn.execute("SELECT value FROM settings WHERE key = 'backup_copy_dir'").fetchone()
            conn.execute(
                "DELETE FROM settings WHERE key IN ('backup_copy_dir', 'backup_copy_status', ?)", (BACKUP_STATUS,)
            )
            # What the pages share (``summary_memo``) was worked out on the database as it was: the summary gets a new
            # id, so every process works it out again, whatever the restored generations' numbers (B-1310).
            conn.execute("INSERT INTO settings (key, value) VALUES ('summary_id', ?) ON CONFLICT(key) DO UPDATE SET "
                         "value = excluded.value", (uuid.uuid4().hex,))  # fmt: skip
            detail = {"file": backup.name, "safety_copy": safety.name, "files": files.words()}
            if copy_dir or (restored_dir and str(restored_dir[0]).strip()):
                detail["note"] = "second backup folder cleared: set it again in Settings (Data & backups)"
            self._log(conn, "backup_restored", detail=detail)
        _forget_learning(self.path)  # the lessons and learning counts kept for the database as it was (B-786)
        return Restored(safety, files)
