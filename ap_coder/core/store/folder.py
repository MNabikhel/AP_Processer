"""The invoices folder's files as the pick-up last read them (D-155): the import page lists the files not processed
yet without reading any of them.

The processed files stay in the invoices folder (their invoices point at them), so it holds a year of invoices on the
company server. Each pick-up (the Process button, an upload, a scheduled watch) reads every file in it to know whether
AP Coder has it already (``find_by_hash``), and keeps what it read of each in ``seen_files``: its place, size, time
and SHA-256. The import page (GET /process) lists as new each file whose size and time are not those the pick-up read,
or whose content no invoice was made from: a look at each file's size and time, one query, nothing read. A database
from before keeps its invoices' own files as read when they were processed (their size and time not known: NULL).

On the company server the folder is listed once for every process (D-176): ``folder_listings`` keeps the latest
listing of each folder (its new files, saved emails and files of a kind not read), which every web worker's import
page shows, the files a pick-up has put in line or read since left out at once.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..keys import register_keys
from .joined import PART_SEEN

# A file whose content AP Coder has (``seen_files`` as ``f``): an invoice was made from it (or from a voucher it is one
# of the files of, D-216), or it is in line.
_KNOWN = ("EXISTS (SELECT 1 FROM invoices AS i WHERE i.file_sha256 = f.sha256 UNION ALL SELECT 1 FROM invoices AS i "
          "WHERE i.original_sha256 = f.sha256 UNION ALL SELECT 1 FROM import_files AS q WHERE q.status IN ('waiting', "
          f"'reading') AND q.sha256 = f.sha256 UNION ALL {PART_SEEN})")  # fmt: skip
_AT_ONCE_FROM = 1000  # files of a folder's listing from which what the pick-up read there is read at once
# The latest listing of each folder (D-176): its resolved place, when it began (``time.time``), the folder's time then
# (nanoseconds) and what it found (JSON: {"files": [[name, size, time], ...] the invoice files new then, "emails":
# [[name, size], ...], "others": [name, ...], "took": seconds}; one that failed: {..., "error": why}).
LISTINGS = ("CREATE TABLE IF NOT EXISTS folder_listings (place TEXT PRIMARY KEY, began REAL NOT NULL, mtime_ns INTEGER "
            "NOT NULL, found TEXT NOT NULL)")  # fmt: skip


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
        ``seen_files`` (counted), the invoices (how many, the latest) and the import queue (its latest batch, D-168)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            return tuple(conn.execute(
                "SELECT (SELECT value FROM settings WHERE key = 'seen_files'), COUNT(*), MAX(id), (SELECT MAX(id) FROM "
                "import_batches) FROM invoices"
            ).fetchone())  # fmt: skip

    def unseen_files(self, listed: list[tuple[str, int, int]], folder: str = "", since: int | None = None) -> set[str]:
        """The places of ``listed`` (place, size, time in nanoseconds) that are not a file an invoice was made from as
        the pick-up last read it (failed attempts count: ``find_by_hash(include_failed=True)``), nor one in line to
        be read (the import queue, D-168), without reading any: one the pick-up has not read yet, or that changed
        since, is new. ``folder``: the place of the folder they are all in, when they are its listing: what the
        pick-up read of that folder is then read at once (0.6 s for 300,000 files, against 0.8 s looked up one by
        one), unless it read far more files there than are listed now (files removed since). ``since``: when the
        listing of ``listed`` began (nanoseconds), for a listing kept a while (D-176): a file the pick-up read with a
        time later than both the one listed and ``since`` was written again after the listing looked at it, and
        counts as the pick-up read it (B-1814; one written over by an older copy keeps an earlier time: new)."""
        if not listed:
            return set()
        with self._conn() as conn:  # type: ignore[attr-defined]
            known = _known_in(conn, folder, len(listed)) if folder and len(listed) >= _AT_ONCE_FROM else None
            if known is not None:  # (a size of None: its size and time not known, its place trusted)
                return {place for place, size, at in listed
                        if (was := known.get(place)) is None or was[0] is not None and was != (size, at)
                        and not (since is not None and was[1] > max(at, since))}  # fmt: skip
            rows = conn.execute(  # (``since`` None: NULL, never later)
                "SELECT json_extract(j.value, '$[0]') FROM json_each(?) AS j WHERE NOT EXISTS (SELECT 1 FROM "
                "seen_files AS f WHERE f.path = json_extract(j.value, '$[0]') AND (f.size IS NULL OR (f.size = "
                "json_extract(j.value, '$[1]') AND f.mtime_ns = json_extract(j.value, '$[2]')) OR (f.mtime_ns > "
                f"json_extract(j.value, '$[2]') AND f.mtime_ns > ?)) AND {_KNOWN})",
                (json.dumps(listed), since),
            ).fetchall()
        return {r[0] for r in rows}

    # --- The folder listed once for every process (D-176) ----------------------------------------------------------

    def keep_listing(self, place: str, began: float, mtime_ns: int, found: dict[str, Any]) -> None:
        """Keep a listing of the folder at ``place`` (resolved) for every process, in place of the one kept: begun at
        ``began`` (``time.time``), the folder's time ``mtime_ns`` read before it, what it ``found`` (``LISTINGS``)."""
        text = json.dumps(found, separators=(",", ":"))
        with self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute("INSERT INTO folder_listings (place, began, mtime_ns, found) VALUES (?, ?, ?, ?) ON "
                         "CONFLICT(place) DO UPDATE SET began = excluded.began, mtime_ns = excluded.mtime_ns, found = "
                         "excluded.found", (place, began, mtime_ns, text))  # fmt: skip

    def kept_listing(self, place: str) -> tuple[float, int, dict[str, Any]] | None:
        """(when it began, the folder's time then, what it found) of the listing kept of the folder at ``place``; None
        when there is none."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT began, mtime_ns, found FROM folder_listings WHERE place = ?",
                               (place,)).fetchone()  # fmt: skip
        return None if row is None else (row[0], row[1], json.loads(row[2]))


def _known_in(conn: Any, folder: str, listed: int) -> dict[str, tuple[Any, Any]] | None:
    """What the pick-up read in ``folder`` of the files whose content AP Coder has, read at once: place -> (size,
    time in nanoseconds), (None, None) when trusted; None when it read more than twice ``listed`` files there (files
    removed since): then looking each listed file up is quicker."""
    inside = (folder.rstrip(os.sep) + os.sep, folder.rstrip(os.sep) + chr(ord(os.sep) + 1))  # (its files, as text)
    noted = conn.execute("SELECT COUNT(*) FROM (SELECT 1 FROM seen_files WHERE path >= ? AND path < ? LIMIT ?)",
                         (*inside, 2 * listed + 1)).fetchone()[0]  # fmt: skip
    if noted > 2 * listed:
        return None
    rows = conn.execute(f"SELECT path, size, mtime_ns FROM seen_files AS f WHERE path >= ? AND path < ? AND {_KNOWN}",
                        inside)  # fmt: skip
    return {r[0]: (r[1], r[2]) for r in rows}


def fill_seen_files(conn: Any, again: bool = False) -> None:
    """An older database's start of ``seen_files``: each invoice's own file as it was processed, its size and time
    unknown (so its place is trusted). Once, whatever the schema version (``_base._migrate``); ``again``: whatever was
    done before (the bench's seed, which writes its invoices without a pick-up). This part's table of the folders'
    listings (``LISTINGS``, D-176) is made here too, whatever the version."""
    conn.execute(LISTINGS)
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
    conn.execute("ALTER TABLE erp_invoices ADD COLUMN plain_name TEXT NOT NULL DEFAULT ''")
    conn.execute("ALTER TABLE erp_invoices ADD COLUMN raw_number TEXT NOT NULL DEFAULT ''")
    rows = conn.execute("SELECT rowid, vendor_name, invoice_number FROM erp_invoices").fetchall()
    conn.executemany("UPDATE erp_invoices SET plain_name = ?, raw_number = ? WHERE rowid = ?",
                     [(*register_keys(r[1], r[2]), r[0]) for r in rows])  # fmt: skip
