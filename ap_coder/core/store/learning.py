"""The learning memory (feedback rows) and metrics."""

from __future__ import annotations

import bisect
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from ..keys import LINE_FIELDS, line_pattern
from ..memory import (
    ACCEPTED,
    BULK,
    CODED,
    CORRECTED,
    HISTORY,
    PAST_KEPT,
    PAST_LINES_BYTES,
    PAST_LINES_KEPT,
    PAST_ROWS_KEPT,
    PastLines,
    Pattern,
    WithPast,
    _aggregate,
    vendor_key,
)
from ..reference_data import UNASSIGNED
from ._base import (
    _FEEDBACK,
    _FEEDBACK_LOCK,
    _METRICS,
    _PAST_LINES,
    _PAST_MEMORY,
    _VENDOR_LESSONS,
    APPROVED,
    FEEDBACK_READ,
    _clean_code,
    _iso_week,
)
from .summary import MEMO_WAIT, fetch_rows

# A lesson as the stamp names it (``_feedback_stamp``): the newest one's, and the one kept as the newest before.
_LESSON_TEXT = ("created_at || '|' || COALESCE(vendor_key, '') || '|' || COALESCE(description, '') || '|' || "
                "COALESCE(final_gl, '')")  # fmt: skip
# The learning memory's order: reviewer decisions first, then imported history, each newest first (a big history import
# never pushes the decisions out).
_ORDER = " ORDER BY outcome = 'history', id DESC"
_READING: dict[tuple[Any, ...], threading.Lock] = {}  # one read of a database's whole memory at a time
_VENDOR_READING = [threading.Lock() for _ in range(64)]  # one read of a vendor's lessons at a time (by its key's hash)
VENDOR_LESSONS_KEPT = 32  # vendors whose lessons are kept worked out in each process (``vendor_lessons``)
# What ``vendor_lessons`` reads of each lesson: what ``memory.aggregate``, the accounts and the cost centers need.
_VENDOR_COLUMNS = "vendor_key, vendor_name, description, final_gl, final_cc, outcome, suggested_gl, created_at"
_DECIDED = (ACCEPTED, CORRECTED, CODED, BULK, "history")  # what a person decided (or the ERP's history)
# What ``PastLines`` reads of a lesson, and the version of what the database keeps of them (``past_lines``, D-178).
_LINE_COLUMNS = "vendor_key, description, final_gl, final_cc, outcome, suggested_gl"
PAST_LINES_FORMAT = "1"
PAST_WRITE_AFTER = 64  # lessons a process carries a vendor's ``_past_lines`` on before it writes the database's copy


def _past_lines_version() -> str:
    from ... import __version__

    return f"{__version__}/{PAST_LINES_FORMAT}"


@dataclass(frozen=True)
class VendorLessons:
    """What one vendor's lessons say (``Store.vendor_lessons``), worked out once from them. Shared: read it, don't
    change it."""

    patterns: list[Pattern]  # ``memory.aggregate`` of them: what the history check reads (``compare_patterns``)
    accounts: list[str]  # the GL accounts its lines were approved to, most used first (the newest first among equals)
    cost_centers: frozenset[str]  # the cost centers its lines were approved with ("" for none)

    @classmethod
    def of(cls, rows: list[dict[str, Any]]) -> VendorLessons:
        """From the vendor's lessons, newest first (``feedback_rows``' order). Only what a person decided (or the ERP's
        history) counts for the accounts, never what the AI proposed; lines left without an account don't."""
        counts: dict[str, int] = {}
        for r in rows:
            code = "" if r["final_gl"] is None else str(r["final_gl"])
            if r["outcome"] in _DECIDED and code and code != UNASSIGNED:
                counts[code] = counts.get(code, 0) + 1
        return cls(patterns=_aggregate(rows), accounts=sorted(counts, key=lambda c: -counts[c]),  # sorted() is stable
                   cost_centers=frozenset(r["final_cc"] or "" for r in rows))  # fmt: skip


def _vendor_stamp(conn: Any, key: str) -> tuple[Any, ...]:
    """``_feedback_stamp`` of one vendor's lessons (their count read from the vendor's index)."""
    return tuple(conn.execute(
        f"SELECT COUNT(*), MAX(id), (SELECT {_LESSON_TEXT} FROM feedback WHERE vendor_key = ?1 ORDER BY id DESC "
        "LIMIT 1) FROM feedback WHERE vendor_key = ?1", (key,)
    ).fetchone())  # fmt: skip


def _feedback_stamp(conn: Any) -> tuple[Any, ...]:
    """What tells whether the lessons kept are still the ones in the database (``feedback_rows``, ``metrics``): the
    highest id, their count as the summary keeps it (``feedback_counts``, D-110: not counted again each time) and the
    newest lesson itself."""
    return tuple(conn.execute(
        f"SELECT (SELECT MAX(id) FROM feedback), (SELECT TOTAL(n) FROM feedback_counts), (SELECT {_LESSON_TEXT} FROM "
        "feedback ORDER BY id DESC LIMIT 1)"
    ).fetchone())  # fmt: skip


@contextmanager
def one_snapshot(conn: Any) -> Iterator[None]:
    """The reads inside see the database as it was at the first of them (one read transaction), so that a stamp and
    the rows read with it agree; inside the caller's own transaction, they do already."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.rollback()  # a write inside, undone with the error as a transaction's is (B-1966)
        raise
    conn.commit()  # it ends the read


def _with_added(conn: Any, kept: tuple[tuple[Any, ...], list[dict[str, Any]]], stamp: tuple[Any, ...],
                limit: int) -> list[dict[str, Any]] | None:  # fmt: skip
    """The whole memory ``kept`` (its stamp, its rows) with the lessons added since put in their place: what reading
    it all again gives, reading only those (``id >`` the newest kept). None when anything but adding happened since
    (a lesson forgotten, a database restored), or when more were added than the memory holds: then it is read again.
    Lessons are changed in place only by an upgrade, which drops what is kept (``_forget_learning``)."""
    (last, count, newest), rows = kept
    added_n = stamp[1] - count  # the count kept by the summary: lower than the rows added when any was removed
    if last is None or stamp[0] is None or not 0 < added_n <= limit:
        return None
    still = conn.execute(f"SELECT {_LESSON_TEXT} FROM feedback WHERE id = ?", (last,)).fetchone()
    if still is None or still[0] != newest:  # the newest kept is gone, or is another one (a database restored)
        return None
    added = fetch_rows(conn, "SELECT * FROM feedback WHERE id > ?" + _ORDER, (last,))
    if len(added) != added_n:
        return None
    # Each new lesson has a higher id than every one kept: the new decisions go before the decisions kept, the new
    # history before the history kept.
    history = lambda r: r["outcome"] == "history"  # noqa: E731
    new, old = bisect.bisect_left(added, True, key=history), bisect.bisect_left(rows, True, key=history)
    return (added[:new] + rows[:old] + added[new:] + rows[old:])[:limit]


def _carried(conn: Any, key: str, kept: tuple[tuple[Any, ...], list[dict[str, Any]]],
             stamp: tuple[Any, ...]) -> list[dict[str, Any]] | None:  # fmt: skip
    """``_past_memory``'s rows ``kept`` (their stamp, the rows) carried forward to ``stamp``: what reading them again
    gives, reading only the vendor's lessons the whole memory just let go (``_moved``). None when they are read
    again."""
    moved = _moved(conn, key, kept[0], len(kept[1]), stamp)
    if moved is None:
        return None
    (left, keep), rows = moved, kept[1]
    return rows if not left and keep == len(rows) else (left + rows)[:keep]


def _moved(conn: Any, key: str, kept: tuple[Any, ...], had: int, stamp: tuple[Any, ...],
           columns: str = "*") -> tuple[list[dict[str, Any]], int] | None:  # fmt: skip
    """What a vendor's lessons past the memory, ``had`` of them under the stamp ``kept``, became under ``stamp``: (the
    lessons to put in front of them, newest first, with these ``columns``; how many of them all to keep). In the
    memory's order (decisions, then history, each newest first) the rows past its last are a vendor's tail: once the
    last moved forward and the vendor only gained decisions (ahead of all its lessons in that order), the lessons the
    whole memory let go (from its last before to its new one) go in front of those kept (with a decision just added,
    when more were added than the memory holds), and of the vendor's newest ``FEEDBACK_READ`` as many of the oldest
    drop as it gained. None when anything else happened: one of its lessons removed (or moved to another vendor),
    history of it imported, the memory's last moved back (lessons forgotten), a database restored; then they are read
    again."""
    count, newest, text, last, history = kept
    count_now, _, _, last_now, history_now = stamp
    gained = count_now - count
    if newest is None or gained < 0 or (history_now, -last_now) > (history, -last):
        return None
    still = conn.execute(f"SELECT {_LESSON_TEXT} FROM feedback WHERE id = ?", (newest,)).fetchone()
    if still is None or still[0] != text:  # its newest kept is gone, or is another one (a database restored)
        return None
    added = conn.execute("SELECT outcome = 'history' FROM feedback WHERE vendor_key = ? AND id > ?",
                         (key, newest)).fetchall()  # fmt: skip
    if len(added) != gained or any(h for (h,) in added):  # one of its lessons removed, or history imported
        return None
    if (history_now, last_now) == (history, last):
        left = []
    elif history_now == history:
        left = fetch_rows(conn, f"SELECT {columns} FROM feedback WHERE vendor_key = ? AND (outcome = 'history') = ? "
                          "AND id >= ? AND id < ?" + _ORDER, (key, history, last, last_now))  # fmt: skip
    else:  # the last moved from the history to the decisions: the decisions after it, then the history up to the last
        left = fetch_rows(conn, f"SELECT {columns} FROM feedback WHERE vendor_key = ? AND (((outcome = 'history') = 0 "
                          "AND id < ?) OR ((outcome = 'history') = 1 AND id >= ?))" + _ORDER,
                          (key, last_now, last))  # fmt: skip
    # The vendor's newest FEEDBACK_READ: the decisions it gained, those it had that the memory still holds, then those
    # past the memory's last, as many as are left.
    held = min(FEEDBACK_READ, count) - had - len(left)  # those it had that the memory still holds
    return left, min(FEEDBACK_READ - gained, count) - held


def forget_past_lines(conn: Any, invoice_id: int) -> None:
    """The copy every process reads of the older lessons by line (``past_lines``, D-178) of each vendor whose lessons
    an invoice is about to forget: no longer what the lessons say, and none of their words left behind (B-1842)."""
    conn.execute("DELETE FROM past_lines WHERE vendor_key IN (SELECT DISTINCT vendor_key FROM feedback WHERE "
                 "invoice_id = ?)", (invoice_id,))  # fmt: skip


class LearningMixin:
    # --- Learning memory ------------------------------------------------------------------------------

    def feedback_rows(
        self, limit: int | None = None, vendor_name: str | None = None, key: str | None = None
    ) -> list[dict[str, Any]]:
        """Recorded reviewer decisions, newest first (only one vendor's when ``vendor_name`` is given), at most
        ``limit`` (``FEEDBACK_READ``). Read once and kept: every invoice read asks for them, and years of imported ERP
        history take a third of a second to load on a laptop. Once lessons were added only those are read, once one
        was removed all again. The list is shared: read it, don't change it."""
        limit = FEEDBACK_READ if limit is None else limit
        order = _ORDER + " LIMIT ?"
        if vendor_name is not None:
            # One vendor's lessons: read once a page (``_session_memo``), not kept past it (what the review screen
            # and the checks read of them is: ``vendor_lessons``). ``key``: the vendor's own, of two whose names read
            # alike (``vendor_key_for``, B-1021).
            args = (key or vendor_key(vendor_name), limit)

            def read() -> list[dict[str, Any]]:
                with self._conn() as conn:
                    return fetch_rows(conn, "SELECT * FROM feedback WHERE vendor_key = ?" + order, args)

            return self._session_memo(("feedback_rows", *args), read, shared=True)
        # Lessons are only ever added or deleted, never changed (but by an upgrade, ``_forget_learning``): the highest
        # id, the count and the newest lesson itself tell whether the ones kept are still the ones in the database.
        # (The id alone is not enough: a database restored from a backup, or made again where one was deleted, gives
        # its next lesson an id a lesson it no longer has once had.)
        kept_key = (str(self.path.resolve()), limit, None, ())
        with self._conn() as conn:
            stamp = _feedback_stamp(conn)
        with _FEEDBACK_LOCK:
            kept = _FEEDBACK.get(kept_key)
            reading = _READING.setdefault(kept_key, threading.Lock())
        if kept is not None and kept[0] == stamp:
            return kept[1]
        # One page reads them, the others asking meanwhile wait for it (the whole memory is 50,000 lessons, 130 MB
        # while read: not one copy per page at once after each approval); when lessons were only added since, only
        # those are read.
        got = reading.acquire(timeout=MEMO_WAIT)
        try:
            with self._conn() as conn, one_snapshot(conn):
                stamp = _feedback_stamp(conn)
                with _FEEDBACK_LOCK:
                    kept = _FEEDBACK.get(kept_key)
                if kept is not None and kept[0] == stamp:
                    return kept[1]
                rows = _with_added(conn, kept, stamp, limit) if kept is not None else None
                if rows is None:
                    rows = fetch_rows(conn, "SELECT * FROM feedback" + order, (limit,))
            with _FEEDBACK_LOCK:
                _FEEDBACK[kept_key] = (stamp, rows)
                while len(_FEEDBACK) > 8:
                    _FEEDBACK.pop(next(iter(_FEEDBACK)))
            return rows
        finally:
            if got:
                reading.release()

    def vendor_lessons(self, vendor_name: str, key: str | None = None) -> VendorLessons:
        """What this vendor's lessons say (the ones ``feedback_rows(vendor_name=...)`` reads), kept in each process
        until one of them is added or removed: the history check, the account picker's "Used for this vendor" and the
        usual cost center ask on every review screen and every check of its edits, and the biggest vendor can have
        50,000 (half a second and 120 MB to read). The latest ``VENDOR_LESSONS_KEPT`` vendors are kept; one page
        works a vendor's out at a time. ``key``: the vendor's own, of two whose names read alike (B-1021)."""
        key = key or vendor_key(vendor_name)
        kept_key = (str(self.path.resolve()), key, FEEDBACK_READ)
        with self._conn() as conn:
            writing = conn.in_transaction
            stamp = self._lessons_stamp(conn, key, writing)
        with _FEEDBACK_LOCK:
            kept = _VENDOR_LESSONS.get(kept_key)  # (the latest used, kept the longest)
        if kept is not None and kept[0] == stamp:
            return kept[1]
        reading = _VENDOR_READING[hash(kept_key) % len(_VENDOR_READING)]
        got = reading.acquire(timeout=MEMO_WAIT)
        try:
            with self._conn() as conn, one_snapshot(conn):
                stamp = self._lessons_stamp(conn, key, writing)
                with _FEEDBACK_LOCK:
                    kept = _VENDOR_LESSONS.get(kept_key)
                if kept is not None and kept[0] == stamp:
                    return kept[1]
                rows = fetch_rows(conn, f"SELECT {_VENDOR_COLUMNS} FROM feedback WHERE vendor_key = ?" + _ORDER +
                                  " LIMIT ?", (key, FEEDBACK_READ)) if stamp[0] else []  # fmt: skip
            found = VendorLessons.of(rows)
            with _FEEDBACK_LOCK:
                _VENDOR_LESSONS.pop(kept_key, None)
                _VENDOR_LESSONS[kept_key] = (stamp, found)
                while len(_VENDOR_LESSONS) > VENDOR_LESSONS_KEPT:
                    _VENDOR_LESSONS.pop(next(iter(_VENDOR_LESSONS)))
            return found
        finally:
            if got:
                reading.release()

    def _lessons_stamp(self, conn: Any, key: str, writing: bool) -> tuple[Any, ...]:
        """``_vendor_stamp``, read once a page while the database does not change (``_session_memo``): a check asks it
        three or four times (the history check, the accounts and cost centers used for the vendor, its older lessons),
        and it counts all of the vendor's lessons (8 ms for the biggest at 300,000 invoices, 179,000 lessons). Inside a
        write under way (``writing``: it may yet be undone, which moves neither of the session's counts) read afresh
        and not kept (B-1810)."""
        if writing:
            return _vendor_stamp(conn, key)
        return self._session_memo(("vendor_stamp", key), lambda: _vendor_stamp(conn, key), shared=True)

    def learning_rows(self, vendor_name: str | None, key: str | None = None) -> list[dict[str, Any]]:
        """``feedback_rows()`` with every lesson of this vendor in it: what an invoice from it is coded and checked
        with. Past ``FEEDBACK_READ`` lessons the whole memory holds only the newest, so a vendor whose history was
        imported first would have none of its own; its older lessons then follow the shared list (``WithPast``, which
        says which rows are which, the rows themselves shared), by line at once (``_past_lines``: what the GL
        suggestions read), the rows themselves read only when they are read (``_past_memory``: the examples, the
        history check and the tax codes of an invoice read; never a check of the review screen, D-178). The shared
        list itself while it holds them all."""
        rows = self.feedback_rows()
        if not vendor_name or len(rows) < FEEDBACK_READ:
            return rows
        key, db = key or vendor_key(vendor_name), str(self.path.resolve())
        with _FEEDBACK_LOCK:  # (the stamp the whole memory was read with, B-1809)
            whole = _FEEDBACK.get((db, FEEDBACK_READ, None, ()))
        read_with, last = whole[0] if whole is not None and whole[1] is rows else None, rows[-1]
        older = self._past_lines(key, last, read_with)
        if not older.n:
            return rows
        return WithPast(rows, lambda: self._past_memory(key, last, read_with), (db, key, FEEDBACK_READ), older)

    def _past_memory(
        self, key: str, last: dict[str, Any], read_with: tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        """This vendor's lessons that the whole memory, whose last lesson is ``last``, does not hold (the ones after
        it in its order, of the vendor's newest ``FEEDBACK_READ``), in that order. Once the memory's last moved on (an
        approval, past the memory's cap) and the vendor only gained decisions, those kept are carried forward
        (``_carried``, D-173); read again whole once anything else changed. The GL suggestions' index extended by them
        (``suggest._PAST``) knows the same list at once, and the list carried forward by what moved in front of it.
        Kept for ``PAST_KEPT`` vendors and ``PAST_ROWS_KEPT`` of their lessons, as that index is, the latest used
        always (D-159). ``read_with``: the stamp the whole memory was read with (``_feedback_stamp``); when the
        database moved on since (a lesson added between the two reads), or it isn't known, they are read for this
        once, not kept nor carried forward: kept, they would be carried forward from a moment they were not read at
        (B-1809)."""
        kept_key = (str(self.path.resolve()), key, FEEDBACK_READ)
        history = last["outcome"] == "history"
        with self._conn() as conn:
            writing = conn.in_transaction
            with one_snapshot(conn):
                if read_with is None or _feedback_stamp(conn) != read_with:
                    return self._past_read(conn, key, last)
                return self._past_kept(conn, key, last, history, writing, kept_key)

    def _past_read(self, conn: Any, key: str, last: dict[str, Any], columns: str = "*") -> list[dict[str, Any]]:
        """The vendor's lessons the whole memory, whose last lesson is ``last``, does not hold, read whole (these
        ``columns`` of them)."""
        history = last["outcome"] == "history"
        return fetch_rows(
            conn, f"SELECT {columns} FROM (SELECT * FROM feedback WHERE vendor_key = ?" + _ORDER + " LIMIT ?) WHERE "
            "(outcome = 'history') > ? OR ((outcome = 'history') = ? AND id < ?)" + _ORDER,
            (key, FEEDBACK_READ, history, history, last["id"]),
        )  # fmt: skip

    def _past_lines(self, key: str, last: dict[str, Any], read_with: tuple[Any, ...] | None) -> PastLines:
        """This vendor's lessons the whole memory, whose last lesson is ``last``, does not hold (``_past_memory``'s
        rows), by line (``PastLines``): what the GL suggestions read of them, on every check of the review screen. At
        300,000 invoices most vendors had thousands of them, read whole on nearly every check (about 115 ms of CPU
        each), as each process kept 32 vendors' and the review queue's invoices came from a thousand (D-178).
        Kept in each process (``PAST_LINES_KEPT`` vendors, ``PAST_LINES_BYTES``) and for every process in the database
        (``past_lines``: as JSON, with the stamp it was worked out under), and carried forward as the memory moves on
        (``_moved``, ``PastLines.carried``: only the lessons it let go are read), read whole only when nothing kept
        can be carried forward (a lesson of the vendor removed, history of it imported, an upgrade). The database's
        copy is written again once a process carried it ``PAST_WRITE_AFTER`` lessons on (or read them whole), never
        inside a write under way. ``read_with``: as ``_past_memory``'s (B-1809)."""
        kept_key = (str(self.path.resolve()), key, FEEDBACK_READ)
        history = last["outcome"] == "history"
        with self._conn() as conn:
            writing = conn.in_transaction
            with one_snapshot(conn):
                if read_with is None or _feedback_stamp(conn) != read_with:
                    return PastLines.of(self._past_read(conn, key, last, _LINE_COLUMNS))  # for this once, not kept
                stamp = (*self._lessons_stamp(conn, key, writing), last["id"], history)
                with _FEEDBACK_LOCK:
                    kept = _PAST_LINES.pop(kept_key, None)
                    if kept is not None:
                        _PAST_LINES[kept_key] = kept  # the latest used, kept the longest
                if kept is not None and kept[0] == stamp:
                    return kept[1]

                def carried(was: tuple[Any, ...], lines: PastLines, behind: int) -> tuple[PastLines, int] | None:
                    """(``lines``, under the stamp ``was``, carried forward; the lessons it moved by since the
                    database's copy, ``behind`` before), or None."""
                    moved = _moved(conn, key, was, lines.n, stamp, _LINE_COLUMNS)
                    if moved is None:
                        return None
                    found, front = lines.carried(*moved), len(moved[0])
                    return found, behind + front + (lines.n + front - found.n)  # (put in front, and dropped)

                found = carried(*kept) if kept is not None else None
                if found is None and (stored := self._stored_past_lines(conn, key)) is not None:
                    found = (stored[1], 0) if stored[0] == stamp else carried(*stored, 0)
                if found is None:
                    found = (PastLines.of(self._past_read(conn, key, last, _LINE_COLUMNS)), PAST_WRITE_AFTER)
        lines, behind = found
        if behind >= PAST_WRITE_AFTER and not writing and self._keep_past_lines(key, stamp, lines):
            behind = 0
        with _FEEDBACK_LOCK:
            _PAST_LINES.pop(kept_key, None)
            _PAST_LINES[kept_key] = (stamp, lines, behind)
            held = sum(kept[1].size for kept in _PAST_LINES.values())
            while len(_PAST_LINES) > 1 and (len(_PAST_LINES) > PAST_LINES_KEPT or held > PAST_LINES_BYTES):
                held -= _PAST_LINES.pop(next(iter(_PAST_LINES)))[1].size
        return lines

    def _stored_past_lines(self, conn: Any, key: str) -> tuple[tuple[Any, ...], PastLines] | None:
        """(the stamp, the lines) of the vendor's ``_past_lines`` kept in the database by any process; None when there
        are none, or of another version (or ``FEEDBACK_READ``), or they can't be read."""
        try:
            row = conn.execute("SELECT stamp, body FROM past_lines WHERE vendor_key = ?", (key,)).fetchone()
        except sqlite3.OperationalError:  # (a backup of an older version restored, before its upgrade: none yet)
            return None
        if row is None:
            return None
        try:
            stamp = json.loads(row[0])
            if stamp[:2] != [_past_lines_version(), FEEDBACK_READ]:
                return None
            count, newest, text, last, history = stamp[2:]
            return (count, newest, text, last, bool(history)), PastLines.decoded(json.loads(row[1]))
        except (ValueError, TypeError, KeyError, IndexError):
            return None

    def _keep_past_lines(self, key: str, stamp: tuple[Any, ...], lines: PastLines) -> bool:
        """The vendor's ``_past_lines`` kept in the database for every process, with the stamp they were worked out
        under; False when the database was busy just now (they are written again later)."""
        body = json.dumps(lines.encoded(), separators=(",", ":"))
        stamped = json.dumps([_past_lines_version(), FEEDBACK_READ, *stamp], separators=(",", ":"))

        def write() -> bool:
            with self._conn() as conn:
                conn.execute("INSERT INTO past_lines (vendor_key, stamp, body) VALUES (?, ?, ?) ON CONFLICT "
                             "(vendor_key) DO UPDATE SET stamp = excluded.stamp, body = excluded.body",
                             (key, stamped, body))  # fmt: skip
            return True

        return bool(self._briefly_or(write, False))  # type: ignore[attr-defined]

    def _past_kept(self, conn: Any, key: str, last: dict[str, Any], history: bool, writing: bool,
                   kept_key: tuple[Any, ...]) -> list[dict[str, Any]]:  # fmt: skip
        """``_past_memory``'s rows from what is kept (as it is, or carried forward), else read whole; kept."""
        stamp = (*self._lessons_stamp(conn, key, writing), last["id"], history)
        with _FEEDBACK_LOCK:
            kept = _PAST_MEMORY.get(kept_key)  # (the latest used, kept the longest)
        if kept is not None and kept[0] == stamp:
            return kept[1]
        missing = _carried(conn, key, kept, stamp) if kept is not None else None
        if missing is None:
            missing = self._past_read(conn, key, last)
        with _FEEDBACK_LOCK:
            _PAST_MEMORY.pop(kept_key, None)
            _PAST_MEMORY[kept_key] = (stamp, missing)
            while len(_PAST_MEMORY) > 1 and (len(_PAST_MEMORY) > PAST_KEPT or sum(
                    len(rows) for _, rows in _PAST_MEMORY.values()) > PAST_ROWS_KEPT):  # fmt: skip
                _PAST_MEMORY.pop(next(iter(_PAST_MEMORY)))
        return missing

    def import_history(self, rows: list[dict[str, Any]], actor: str | None = None) -> dict[str, int]:
        """Past AP coding from the ERP (``history.rows_from_records``) into the learning memory, as history.
        Rows already imported (same vendor, description, GL, cost center, amount and date) are skipped."""
        added = skipped = unknown = 0
        with self._conn() as conn:
            known_gls = {r[0] for r in conn.execute("SELECT code FROM gl_accounts")}
            seen = {
                (r[0], r[1], r[2], r[3] or "", r[4], r[5])
                for r in conn.execute(
                    "SELECT vendor_key, description, final_gl, final_cc, amount, created_at FROM feedback "
                    "WHERE outcome = ?",
                    (HISTORY,),
                )
            }
            batch = []
            for row in rows:
                key = vendor_key(row["vendor_name"])
                gl = _clean_code(row["gl_code"])
                if known_gls and gl not in known_gls:  # a retired or mistyped account would only mislead
                    unknown += 1
                    continue
                when = row.get("date") or ""  # undated rows: the same key every day, so no re-import doubles
                ident = (key, row["description"], gl, row.get("cost_center") or "", row.get("amount"), when)
                if not key or ident in seen:
                    skipped += 1
                    continue
                seen.add(ident)
                batch.append((key, row["vendor_name"], row["description"], row.get("amount"), gl,
                              row.get("cost_center") or "", HISTORY, "ERP history", when))  # fmt: skip
            conn.executemany(
                """INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, amount,
                   suggested_gl, final_gl, suggested_cc, final_cc, outcome, reviewer, created_at)
                   VALUES (NULL, NULL, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?)""",
                batch,
            )
            added = len(batch)
            self._log(conn, "history_imported", actor=actor, detail={
                "added": added, "skipped": skipped, "unknown_gl": unknown,
            })  # fmt: skip
        return {"added": added, "skipped": skipped, "unknown_gl": unknown}

    def history_count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM feedback WHERE outcome = 'history'").fetchone()[0])

    def forget_history(self, actor: str | None = None) -> int:
        with self._conn() as conn:
            conn.execute("DELETE FROM past_lines")  # (what is kept of the lessons forgotten goes with them)
            count = conn.execute("DELETE FROM feedback WHERE outcome = 'history'").rowcount
            if count:
                self._log(conn, "lessons_forgotten", actor=actor, detail={"count": count, "history": True})
            return count

    def delete_feedback(self, ids: list[int], actor: str | None = None) -> int:
        """Forget lines of the coding memory (Learning → Coding memory). What each line's approval taught the lessons
        for lines like it (its GL account and cost center lessons, ``lessons.LINE_FIELDS``) is withdrawn with it, once
        no line of that kind is left on that invoice (B-1422): a forgotten line is no hint and reaches no AI."""
        with self._conn() as conn:
            gone = conn.execute(
                "SELECT invoice_id, description FROM feedback WHERE invoice_id IS NOT NULL AND id IN "
                "(SELECT value FROM json_each(?))", (json.dumps([int(i) for i in ids]),)
            ).fetchall()  # fmt: skip
            conn.execute("DELETE FROM past_lines WHERE vendor_key IN (SELECT vendor_key FROM feedback WHERE id IN "
                         "(SELECT value FROM json_each(?)))", (json.dumps([int(i) for i in ids]),))  # fmt: skip
            cur = conn.executemany("DELETE FROM feedback WHERE id = ?", [(i,) for i in ids])
            withdrawn = 0
            for invoice_id, pattern in {(r["invoice_id"], line_pattern(r["description"])) for r in gone}:
                left = conn.execute("SELECT description FROM feedback WHERE invoice_id = ?", (invoice_id,)).fetchall()
                if not pattern or any(line_pattern(r["description"]) == pattern for r in left):
                    continue  # another line like it is still in the memory: its lesson still stands
                situations = conn.execute(
                    "SELECT a.situation FROM lesson_approvals a JOIN lessons l ON l.situation = a.situation "
                    "WHERE a.invoice_id = ? AND l.field IN (?, ?) AND l.pattern = ?",
                    (invoice_id, *LINE_FIELDS, pattern),
                ).fetchall()  # fmt: skip
                for (situation,) in situations:
                    self._withdraw_lessons(conn, invoice_id, situation)
                    withdrawn += 1
            if ids:
                self._log(conn, "lessons_forgotten", actor=actor, detail={"count": len(ids), "lessons": withdrawn})
            return cur.rowcount

    def metrics(self) -> dict[str, Any]:
        """The learning memory's counts (kept until a lesson is added or removed, like ``feedback_rows``: four scans
        of the whole memory) and the invoices' (counted each time)."""
        with self._conn() as conn:
            stamp, key = _feedback_stamp(conn), str(self.path.resolve())
            with _FEEDBACK_LOCK:
                kept = _METRICS.get(key)
            if kept is not None and kept[0] == stamp:
                learned = kept[1]
            else:
                learned = self._learning_metrics(conn)
                with _FEEDBACK_LOCK:
                    _METRICS[key] = (stamp, learned)
                    while len(_METRICS) > 8:
                        _METRICS.pop(next(iter(_METRICS)))
            self._summary_fresh(conn)  # the invoices' summary (D-110): its status and edits
            statuses = conn.execute("SELECT status, COUNT(*) n FROM invoice_summary GROUP BY status").fetchall()
            untouched = conn.execute(
                "SELECT COUNT(*) FROM invoice_summary WHERE status = ? AND edits = '[]'", (APPROVED,)
            ).fetchone()[0]
            coded_by_hand = conn.execute(
                "SELECT COUNT(*) FROM invoice_summary WHERE status = ? AND edits LIKE '%\"line_coded\"%'", (APPROVED,)
            ).fetchone()[0]
        return {
            **{k: v for k, v in learned.items() if k not in ("weekly", "by_vendor", "top_corrections")},
            "invoices_by_status": {r["status"]: r["n"] for r in statuses},
            "invoices_approved_without_edits": untouched,
            "invoices_coded_by_hand": coded_by_hand,
            "weekly": [dict(w) for w in learned["weekly"]],
            "by_vendor": [dict(r) for r in learned["by_vendor"]],
            "top_corrections": [dict(r) for r in learned["top_corrections"]],
        }

    def invoice_count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0])

    def run_summary_rows(self, latest: int | None = None) -> list[dict[str, Any]]:
        """What the redacted run summary (``ops/share_report.py``) reads of every invoice, or of the ``latest`` ones,
        oldest first, in one query: its id, file, status, error, meta and validation (their JSON as stored)."""
        newest = "" if latest is None else "WHERE id IN (SELECT id FROM invoices ORDER BY id DESC LIMIT ?) "
        sql = f"SELECT id, source_path, status, error, meta, validation FROM invoices {newest}ORDER BY id"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, () if latest is None else (latest,))]

    def line_metrics(self) -> dict[str, Any]:
        """``metrics()``'s counts of the lines AP reviewed (``lines_*``, ``line_accuracy``) alone: what the review queue
        shows, from the counts the summary keeps (``feedback_counts``) instead of four scans of the learning memory."""
        with self._conn() as conn:
            return self._line_counts(conn)

    @staticmethod
    def _keep_approved_coding(conn: Any, invoice_id: int) -> None:
        """An approved invoice reopened: keep its coding as approved, line by line (``meta.reopened_coding``, from its
        lessons, before they are withdrawn), which a line changed at the next approval corrects (B-1299, D-112). A
        rejected invoice has none."""
        coding = {str(r[0]): [r[1], r[2] or ""] for r in conn.execute(
            "SELECT line_number, final_gl, final_cc FROM feedback WHERE invoice_id = ?", (invoice_id,))}  # fmt: skip
        conn.execute("UPDATE invoices SET meta = json_set(CASE WHEN json_valid(meta) THEN meta ELSE '{}' END, "
                     "'$.reopened_coding', json(?)) WHERE id = ?", (json.dumps(coding), invoice_id))  # fmt: skip

    @staticmethod
    def _line_counts(conn: Any) -> dict[str, Any]:
        # Imported ERP history is memory, not a reviewer decision: it never counts towards accuracy.
        lines = conn.execute(  # counted as lessons are added and removed (the summary's ``feedback_counts``, D-110)
            "SELECT outcome, n FROM feedback_counts WHERE outcome != 'history' AND n > 0"
        ).fetchall()
        outcome = {r["outcome"]: r["n"] for r in lines}
        # Every line AP reviewed (and AP Coder learns from), the ones it coded over a guess too (B-479); the accuracy
        # measures only what the AI proposed: confirmed or corrected (B-475).
        measured = outcome.get(ACCEPTED, 0) + outcome.get(CORRECTED, 0)
        return {
            "lines_reviewed": sum(outcome.values()),
            "lines_measured": measured,
            "lines_coded": outcome.get(CODED, 0),
            "lines_bulk": outcome.get(BULK, 0),  # approved in bulk as proposed: not measured (B-955)
            "lines_accepted": outcome.get(ACCEPTED, 0),
            "lines_corrected": outcome.get(CORRECTED, 0),
            "line_accuracy": round(outcome.get(ACCEPTED, 0) / measured, 4) if measured else None,
        }

    @classmethod
    def _learning_metrics(cls, conn: Any) -> dict[str, Any]:
        daily = conn.execute(
            """SELECT substr(created_at, 1, 10) day,
                      SUM(outcome = 'accepted') accepted, SUM(outcome = 'corrected') corrected
               FROM feedback WHERE outcome NOT IN ('history', 'coded', 'bulk') GROUP BY day"""
        ).fetchall()
        by_vendor = conn.execute(
            """SELECT vendor_name, COUNT(*) lines, SUM(outcome = 'accepted') accepted,
                      SUM(outcome = 'corrected') corrected, MAX(created_at) last_seen
               FROM feedback WHERE outcome NOT IN ('history', 'coded', 'bulk') GROUP BY vendor_key
               ORDER BY lines DESC"""
        ).fetchall()
        corrections = conn.execute(
            """SELECT COALESCE(suggested_gl, '(new line)') suggested_gl, final_gl, COUNT(*) n
               FROM feedback WHERE outcome = 'corrected' AND COALESCE(suggested_gl, '') != final_gl
               GROUP BY suggested_gl, final_gl ORDER BY n DESC LIMIT 25"""
        ).fetchall()
        # ISO weeks (Monday to Sunday, "2026-W41"), as on the Insights page: SQLite's %W numbers them differently.
        weeks: dict[str, dict[str, Any]] = {}
        for r in daily:
            week = weeks.setdefault(_iso_week(r["day"]), {"week": _iso_week(r["day"]), "accepted": 0, "corrected": 0})
            week["accepted"] += r["accepted"] or 0
            week["corrected"] += r["corrected"] or 0
        return {
            **cls._line_counts(conn),
            "weekly": [weeks[w] for w in sorted(weeks)],
            "by_vendor": [dict(r) for r in by_vendor],
            "top_corrections": [dict(r) for r in corrections],
        }
