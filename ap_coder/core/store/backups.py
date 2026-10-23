"""Backups and restore."""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..paths import PROJECT_DIR, default_db_path, find_moved_file, private_dir  # noqa: F401  (re-exported)
from ._base import (
    _RESTORING,
    _RESTORING_LOCK,
    _SCHEMA,
    SCHEMA_VERSION,
    _forget_learning,
    _now,
    _store_class,
)


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

    def backup_now(self, label: str = "", actor: str | None = None) -> Path:
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
        made = self.backup_to(dest)
        if label == "manual":
            self.log_event("backup_made", actor=actor, detail={"file": made.name})
        if label in ("", "manual"):
            self.copy_backup(made)
        return made

    def copy_backup(self, made: Path, keep: int = 14) -> Path | None:
        """Copy a backup to the second location set in Settings (e.g. a OneDrive or network folder), keeping
        its newest ``keep`` daily copies. A copy that fails (folder offline) is recorded, never raised: the
        local backup is there either way."""
        folder = self.get_setting("backup_copy_dir").strip()
        if not folder:
            return None
        try:
            target = Path(folder).expanduser()
            if not target.is_dir():
                raise OSError(f"folder not found: {target}")
            if target.resolve() == made.parent.resolve():
                raise OSError("that is the local backups folder; choose another one")
            copied = Path(shutil.copy2(made, target / made.name))
            for old in sorted((p for p in target.glob("ap_coder-*.db") if p.stem.count("-") == 2), reverse=True)[keep:]:
                old.unlink(missing_ok=True)
        except OSError as exc:
            self.set_setting("backup_copy_status", f"failed {_now()[:16]}: {exc}")
            return None
        self.set_setting("backup_copy_status", f"ok {_now()[:16]}: {copied.name}")
        return copied

    def auto_backup(self, keep: int = 14, min_hours: float = 20) -> Path | None:
        """Back up at most once a day (call it at start-up) and keep the newest ``keep`` daily copies."""
        daily = [p for p in self.list_backups() if p.stem.count("-") == 2]  # ap_coder-YYYYMMDD-HHMMSS
        now = dt.datetime.now()
        for backup in daily:  # one made in the future (the clock was ahead) is old: it never stops the daily backups
            try:
                age = now - dt.datetime.fromtimestamp(backup.stat().st_mtime)
            except OSError:
                continue
            if dt.timedelta(0) <= age < dt.timedelta(hours=min_hours):
                return None
        if not self.path.exists():
            return None
        made = self.backup_now()
        for old in [p for p in self.list_backups() if p.stem.count("-") == 2][keep:]:
            old.unlink(missing_ok=True)
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
        a ``schema_version`` setting no newer than this version's, and no triggers or views (AP Coder makes
        none; one in a planted file would run on every later change)."""
        with _store_class()._local_copy(backup) as copy:
            _store_class()._check_backup_file(copy, Path(backup).name)

    @staticmethod
    def _check_backup_file(path: Path, name: str) -> None:
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise ValueError(f"{name} could not be opened ({exc})") from None
        try:
            kinds = {r[0] for r in conn.execute("SELECT type FROM sqlite_master WHERE type IN ('trigger', 'view')")}
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

    def row_identity(self, invoice_id: int) -> tuple[Any, ...] | None:
        """What tells this invoice from another one under the same number (its file's SHA-256 and when it came in), or
        None when there is none: a background worker takes it before its long work and writes its result only onto
        the same invoice (``expect``), not onto whatever a restored backup holds under that number."""
        with self._conn() as conn:
            row = conn.execute("SELECT file_sha256, created_at FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        return (row["file_sha256"], row["created_at"]) if row else None

    def restoring(self) -> bool:
        """A backup is being restored into this database (in this process): the background workers wait."""
        with _RESTORING_LOCK:
            return str(self.path.resolve()) in _RESTORING

    def restore_from(self, backup: str | Path) -> Path:
        """``_restore_from`` with the background workers paused meanwhile (``restoring``)."""
        key = str(self.path.resolve())
        with _RESTORING_LOCK:
            _RESTORING.add(key)
        try:
            return self._restore_from(backup)
        finally:
            with _RESTORING_LOCK:
                _RESTORING.discard(key)

    def _restore_from(self, backup: str | Path) -> Path:
        """Replace the database with a backup. The current one is backed up first (returned).
        Raises ValueError, with nothing changed, when the file is not an AP Coder backup (``check_backup``).
        The second backup folder is cleared after a restore: a backup file must not choose where later
        backups are copied (set it again in Settings)."""
        with self._local_copy(backup) as copy:  # read from this computer, never written next to the backup
            self._check_backup_file(copy, Path(backup).name)
            copy_dir = self.get_setting("backup_copy_dir").strip()
            safety = self.backup_now("before-restore")
            source = sqlite3.connect(copy)
            target = sqlite3.connect(self.path)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)  # an older backup may lack newer tables...
            self._migrate(conn)  # ...and columns
            restored_dir = conn.execute("SELECT value FROM settings WHERE key = 'backup_copy_dir'").fetchone()
            conn.execute("DELETE FROM settings WHERE key IN ('backup_copy_dir', 'backup_copy_status')")
            detail = {"file": Path(backup).name, "safety_copy": safety.name}
            if copy_dir or (restored_dir and str(restored_dir[0]).strip()):
                detail["note"] = "second backup folder cleared: set it again in Settings (Data & backups)"
            self._log(conn, "backup_restored", detail=detail)
        _forget_learning(self.path)  # the lessons and learning counts kept for the database as it was (B-786)
        return safety
