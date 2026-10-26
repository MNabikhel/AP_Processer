"""The learning memory (feedback rows) and metrics."""

from __future__ import annotations

import json
from typing import Any

from ..memory import ACCEPTED, BULK, CODED, CORRECTED, vendor_key
from ..paths import PROJECT_DIR, default_db_path, find_moved_file, private_dir  # noqa: F401  (re-exported)
from ._base import (
    _FEEDBACK,
    _FEEDBACK_LOCK,
    _METRICS,
    APPROVED,
    FEEDBACK_READ,
    _clean_code,
    _iso_week,
)
from .summary import fetch_rows


def _feedback_stamp(conn: Any) -> tuple[Any, ...]:
    """What tells whether the lessons kept are still the ones in the database (``feedback_rows``, ``metrics``): their
    count as the summary keeps it (``feedback_counts``, D-110), not counted again each time."""
    return tuple(conn.execute(
        "SELECT (SELECT MAX(id) FROM feedback), (SELECT TOTAL(n) FROM feedback_counts), (SELECT created_at || '|' || "
        "COALESCE(vendor_key, '') || '|' || COALESCE(description, '') || '|' || COALESCE(final_gl, '') FROM feedback "
        "ORDER BY id DESC LIMIT 1)"
    ).fetchone())  # fmt: skip


class LearningMixin:
    # --- Learning memory ------------------------------------------------------------------------------

    def feedback_rows(
        self, limit: int | None = None, vendor_name: str | None = None, key: str | None = None
    ) -> list[dict[str, Any]]:
        """Recorded reviewer decisions, newest first (only one vendor's when ``vendor_name`` is given), at most
        ``limit`` (``FEEDBACK_READ``). Read once and kept until a lesson is added or removed: every invoice read asks
        for them, and years of imported ERP history take a third of a second to load on a laptop. The list is
        shared: read it, don't change it."""
        limit = FEEDBACK_READ if limit is None else limit
        sql, args = "SELECT * FROM feedback", ()
        if vendor_name is not None:
            # ``key``: the vendor's own, of two whose names read alike (``vendor_key_for``, B-1021)
            sql, args = sql + " WHERE vendor_key = ?", (key or vendor_key(vendor_name),)
        with self._conn() as conn:
            # Lessons are only ever added or deleted, never changed: the highest id, the count and the newest lesson
            # itself tell whether the ones kept are still the ones in the database. (The id alone is not enough: a
            # database restored from a backup, or made again where one was deleted, gives its next lesson an id a
            # lesson it no longer has once had.)
            stamp = _feedback_stamp(conn)
            key = (str(self.path.resolve()), limit, vendor_name, args)
            with _FEEDBACK_LOCK:
                kept = _FEEDBACK.get(key)
                if kept is not None and kept[0] == stamp:
                    return kept[1]
            # Reviewer decisions first, then imported history: a big history import never pushes them out.
            rows = fetch_rows(conn, sql + " ORDER BY outcome = 'history', id DESC LIMIT ?", (*args, limit))
        if vendor_name is None:  # one vendor's lessons are quick to read: only the whole memory is kept
            with _FEEDBACK_LOCK:
                _FEEDBACK[key] = (stamp, rows)
                while len(_FEEDBACK) > 8:
                    _FEEDBACK.pop(next(iter(_FEEDBACK)))
        return rows

    def learning_rows(self, vendor_name: str | None, key: str | None = None) -> list[dict[str, Any]]:
        """``feedback_rows()`` with every lesson of this vendor in it: what an invoice from it is coded and checked
        with. Past ``FEEDBACK_READ`` lessons the whole memory holds only the newest, so a vendor whose history was
        imported first would have none of its own; its older lessons then follow the shared list (in a new list,
        the rows themselves shared). The shared list itself while it holds them all."""
        rows = self.feedback_rows()
        if not vendor_name or len(rows) < FEEDBACK_READ:
            return rows
        key = key or vendor_key(vendor_name)
        have = {r["id"] for r in rows if r["vendor_key"] == key}
        missing = [r for r in self.feedback_rows(vendor_name=vendor_name, key=key) if r["id"] not in have]
        return rows + missing if missing else rows

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
        with self._conn() as conn:
            cur = conn.executemany("DELETE FROM feedback WHERE id = ?", [(i,) for i in ids])
            if ids:
                self._log(conn, "lessons_forgotten", actor=actor, detail={"count": len(ids)})
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
