"""The learning memory (feedback rows) and metrics."""

from __future__ import annotations

import bisect
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from ..memory import (
    ACCEPTED,
    BULK,
    CODED,
    CORRECTED,
    PAST_KEPT,
    PAST_ROWS_KEPT,
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
def _one_snapshot(conn: Any) -> Iterator[None]:
    """The reads inside see the database as it was at the first of them (one read transaction), so that a stamp and
    the rows read with it agree; inside the caller's own transaction, they do already."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN")
    try:
        yield
    finally:
        conn.commit()  # nothing written: it ends the read


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
    gives, reading only the vendor's lessons the whole memory just let go (from its last before to its new one). In
    the memory's order (decisions, then history, each newest first) the rows past its last are a vendor's tail: once
    the last moved forward and the vendor only gained decisions (ahead of all its lessons in that order), the lessons
    let go go in front of those kept (with a decision just added, when more were added than the memory holds), and of
    the vendor's newest ``FEEDBACK_READ`` as many of the oldest drop as it gained. None when anything else happened:
    one of its lessons removed (or moved to another vendor), history of it imported, the memory's last moved back
    (lessons forgotten), a database restored; then they are read again."""
    (count, newest, text, last, history), rows = kept
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
        left = fetch_rows(conn, "SELECT * FROM feedback WHERE vendor_key = ? AND (outcome = 'history') = ? AND "
                          "id >= ? AND id < ?" + _ORDER, (key, history, last, last_now))  # fmt: skip
    else:  # the last moved from the history to the decisions: the decisions after it, then the history up to the last
        left = fetch_rows(conn, "SELECT * FROM feedback WHERE vendor_key = ? AND (((outcome = 'history') = 0 AND id "
                          "< ?) OR ((outcome = 'history') = 1 AND id >= ?))" + _ORDER,
                          (key, last_now, last))  # fmt: skip
    # The vendor's newest FEEDBACK_READ: the decisions it gained, those it had that the memory still holds, then those
    # past the memory's last, as many as are left.
    held = min(FEEDBACK_READ, count) - len(rows) - len(left)  # those it had that the memory still holds
    keep = min(FEEDBACK_READ - gained, count) - held
    return rows if not left and keep == len(rows) else (left + rows)[:keep]


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
            with self._conn() as conn, _one_snapshot(conn):
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
            stamp = _vendor_stamp(conn, key)
        with _FEEDBACK_LOCK:
            kept = _VENDOR_LESSONS.pop(kept_key, None)
            if kept is not None:
                _VENDOR_LESSONS[kept_key] = kept  # the latest used, kept the longest
        if kept is not None and kept[0] == stamp:
            return kept[1]
        reading = _VENDOR_READING[hash(kept_key) % len(_VENDOR_READING)]
        got = reading.acquire(timeout=MEMO_WAIT)
        try:
            with self._conn() as conn, _one_snapshot(conn):
                stamp = _vendor_stamp(conn, key)
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

    def learning_rows(self, vendor_name: str | None, key: str | None = None) -> list[dict[str, Any]]:
        """``feedback_rows()`` with every lesson of this vendor in it: what an invoice from it is coded and checked
        with. Past ``FEEDBACK_READ`` lessons the whole memory holds only the newest, so a vendor whose history was
        imported first would have none of its own; its older lessons then follow the shared list (in a new list,
        the rows themselves shared: ``WithPast``, which says which rows are which). The shared list itself while it
        holds them all."""
        rows = self.feedback_rows()
        if not vendor_name or len(rows) < FEEDBACK_READ:
            return rows
        key = key or vendor_key(vendor_name)
        missing = self._past_memory(key, rows[-1])
        return WithPast(rows, missing, (str(self.path.resolve()), key, FEEDBACK_READ)) if missing else rows

    def _past_memory(self, key: str, last: dict[str, Any]) -> list[dict[str, Any]]:
        """This vendor's lessons that the whole memory, whose last lesson is ``last``, does not hold (the ones after
        it in its order, of the vendor's newest ``FEEDBACK_READ``), in that order. Once the memory's last moved on (an
        approval, past the memory's cap) and the vendor only gained lessons it holds, those kept are carried forward
        (``_carried``, D-173); read again whole once anything else changed. The GL suggestions' index extended by them
        (``suggest._PAST``) knows the same list at once, and the list carried forward by what moved in front of it.
        Kept for ``PAST_KEPT`` vendors and ``PAST_ROWS_KEPT`` of their lessons, as that index is, the latest used
        always (D-159)."""
        kept_key = (str(self.path.resolve()), key, FEEDBACK_READ)
        history = last["outcome"] == "history"
        with self._conn() as conn, _one_snapshot(conn):
            stamp = (*_vendor_stamp(conn, key), last["id"], history)
            with _FEEDBACK_LOCK:
                kept = _PAST_MEMORY.pop(kept_key, None)
                if kept is not None:
                    _PAST_MEMORY[kept_key] = kept  # the latest used, kept the longest
            if kept is not None and kept[0] == stamp:
                return kept[1]
            missing = _carried(conn, key, kept, stamp) if kept is not None else None
            if missing is None:
                missing = fetch_rows(
                    conn, "SELECT * FROM (SELECT * FROM feedback WHERE vendor_key = ?" + _ORDER + " LIMIT ?) WHERE "
                    "(outcome = 'history') > ? OR ((outcome = 'history') = ? AND id < ?)" + _ORDER,
                    (key, FEEDBACK_READ, history, history, last["id"]),
                )  # fmt: skip
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
        from ...ai.history import HISTORY

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
            count = conn.execute("DELETE FROM feedback WHERE outcome = 'history'").rowcount
            if count:
                self._log(conn, "lessons_forgotten", actor=actor, detail={"count": count, "history": True})
            return count

    def delete_feedback(self, ids: list[int], actor: str | None = None) -> int:
        """Forget lines of the coding memory (Learning → Coding memory). What each line's approval taught the lessons
        for lines like it (its GL account and cost center lessons, ``lessons.LINE_FIELDS``) is withdrawn with it, once
        no line of that kind is left on that invoice (B-1422): a forgotten line is no hint and reaches no AI."""
        from ...ai.lessons import LINE_FIELDS, line_pattern

        with self._conn() as conn:
            gone = conn.execute(
                "SELECT invoice_id, description FROM feedback WHERE invoice_id IS NOT NULL AND id IN "
                "(SELECT value FROM json_each(?))", (json.dumps([int(i) for i in ids]),)
            ).fetchall()  # fmt: skip
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
