"""The invoices folder's files as the pick-up last read them (D-155): the import page lists the files not processed
yet without reading any of them.

The processed files stay in the invoices folder (their invoices point at them), so it holds a year of invoices on the
company server. Each pick-up (the Process button, an upload, a scheduled watch) reads every file in it to know whether
AP Coder has it already (``find_by_hash``), and keeps what it read of each in ``seen_files``: its place, size, time
and SHA-256. The import page (GET /process) lists as new each file whose size and time are not those the pick-up read,
or whose content no invoice was made from: a look at each file's size and time, one query, nothing read. A database
from before keeps its invoices' own files as read when they were processed (their size and time not known: NULL).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def read_file(path: Path) -> tuple[str, int, int, str]:
    """(place, size, time in nanoseconds, SHA-256) of a file the pick-up reads, as ``note_files`` keeps it: its size
    and time looked at before it is read (one changed while it was read looks changed afterwards), its place in its
    folder's resolved place (a link, a mapped drive or a relative folder followed, as an invoice's ``source_path`` and
    the import page's list have it). Raises OSError."""
    seen = path.stat()
    place = str(path.parent.resolve() / path.name)
    return place, seen.st_size, seen.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


class FolderMixin:
    # --- What the pick-up read of the invoices folder's files ----------------------------------------------------

    def note_files(self, read: list[tuple[str, int, int, str]]) -> None:
        """Keep what a pick-up read of each file, (place, size, time in nanoseconds, SHA-256): written only for the
        files that changed since, and then one more change counted (``files_stamp``)."""
        if not read:
            return
        with self._conn() as conn:  # type: ignore[attr-defined]
            known = {tuple(r) for r in conn.execute(
                "SELECT path, size, mtime_ns, sha256 FROM seen_files WHERE path IN (SELECT value FROM json_each(?))",
                (json.dumps([r[0] for r in read]),),
            )}  # fmt: skip
            changed = [r for r in read if tuple(r) not in known]
            if not changed:
                return
            conn.executemany(
                "INSERT INTO seen_files (path, size, mtime_ns, sha256) VALUES (?, ?, ?, ?) ON CONFLICT(path) DO "
                "UPDATE SET size = excluded.size, mtime_ns = excluded.mtime_ns, sha256 = excluded.sha256",
                changed,
            )
            conn.execute("INSERT INTO settings (key, value) VALUES ('seen_files', '1') ON CONFLICT(key) DO UPDATE SET "
                         "value = CAST(value AS INTEGER) + 1")  # fmt: skip

    def files_stamp(self) -> tuple[Any, ...]:
        """What the import page's list of new files depends on besides the folder itself: the changes to
        ``seen_files`` (counted), and the invoices (how many, the latest)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            return tuple(conn.execute(
                "SELECT (SELECT value FROM settings WHERE key = 'seen_files'), COUNT(*), MAX(id) FROM invoices"
            ).fetchone())  # fmt: skip

    def unseen_files(self, listed: list[tuple[str, int, int]]) -> set[str]:
        """The places of ``listed`` (place, size, time in nanoseconds) that are not a file an invoice was made from as
        the pick-up last read it (failed attempts count: ``find_by_hash(include_failed=True)``), without reading
        any: one the pick-up has not read yet, or that changed since, is new."""
        if not listed:
            return set()
        with self._conn() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                "SELECT json_extract(j.value, '$[0]') FROM json_each(?) AS j WHERE NOT EXISTS (SELECT 1 FROM "
                "seen_files AS f WHERE f.path = json_extract(j.value, '$[0]') AND (f.size IS NULL OR (f.size = "
                "json_extract(j.value, '$[1]') AND f.mtime_ns = json_extract(j.value, '$[2]'))) AND EXISTS (SELECT 1 "
                "FROM invoices AS i WHERE i.file_sha256 = f.sha256 UNION ALL SELECT 1 FROM invoices AS i WHERE "
                "i.original_sha256 = f.sha256))",
                (json.dumps(listed),),
            ).fetchall()
        return {r[0] for r in rows}


def fill_seen_files(conn: Any, again: bool = False) -> None:
    """An older database's start of ``seen_files``: each invoice's own file as it was processed, its size and time
    unknown (so its place is trusted). Once, whatever the schema version (``_base._migrate``); ``again``: whatever was
    done before (the bench's seed, which writes its invoices without a pick-up)."""
    if not again and conn.execute("SELECT 1 FROM settings WHERE key = 'seen_files'").fetchone():
        return
    conn.execute("INSERT OR IGNORE INTO seen_files (path, sha256) SELECT source_path, file_sha256 FROM invoices "
                 "WHERE COALESCE(source_path, '') != '' AND file_sha256 IS NOT NULL")  # fmt: skip
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('seen_files', '1')")


def add_register_keys(conn: Any, columns: Any) -> None:
    """The ERP register's vendor names and invoice numbers as the quick search compares them (schema 22, D-157), for
    a register made before them: looked for whatever the version (``columns``: the table's, empty when it has none)."""
    if not columns or "plain_name" in columns:
        return
    from ...erp.search import register_keys

    conn.execute("ALTER TABLE erp_invoices ADD COLUMN plain_name TEXT NOT NULL DEFAULT ''")
    conn.execute("ALTER TABLE erp_invoices ADD COLUMN raw_number TEXT NOT NULL DEFAULT ''")
    rows = conn.execute("SELECT rowid, vendor_name, invoice_number FROM erp_invoices").fetchall()
    conn.executemany("UPDATE erp_invoices SET plain_name = ?, raw_number = ? WHERE rowid = ?",
                     [(*register_keys(r[1], r[2]), r[0]) for r in rows])  # fmt: skip
