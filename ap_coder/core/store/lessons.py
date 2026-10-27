"""Lessons (AP's corrections per situation: ``record_lesson``, ``lessons_for``), the AI reviewer's queue
(``queue_verification``, ``next_verification``), the Azure check's figures, and what changes an open invoice after it
was processed: a check folded in (``update_after_check``), the duplicates checked again (``_recheck_duplicates``) and
the page reader's new reading (``replace_proposal``)."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ...coding.po import po_key
from ..audit import diff_coding
from ..memory import vendor_key
from ._base import (
    _VERIFY_IN_LINE,
    AZURE_CHECKING,
    AZURE_DONE,
    AZURE_FAILED,
    AZURE_SKIPPED,
    AZURE_WAITING,
    CHECKS_STAMP_KEY,
    PARKED,
    REVIEW,
    VERIFY_DONE,
    VERIFY_FAILED,
    VERIFY_RUNNING,
    VERIFY_SKIPPED,
    VERIFY_WAITING,
    _due,
    _now,
    _same_row,
    _stale_before,
)

# A lesson with how many of the approvals still standing corrected it to its value (``agreeing``): a lesson recorded
# without an invoice (none kept beside it) counts its corrections.
# The corrections to the lesson's value, over the approvals still standing (``lesson_approvals``), plus those counted
# before each approval's part was kept (the lesson's count less the approvals'), taken as agreeing as they were before:
# an upgrade never demotes a lesson (B-785).
_LESSON = (
    "SELECT lessons.*, (SELECT COUNT(*) FROM lesson_approvals a WHERE a.situation = lessons.situation "
    "AND a.corrected = 1 AND a.value = lessons.value) + MAX(lessons.corrected - (SELECT COUNT(*) FROM "
    "lesson_approvals a WHERE a.situation = lessons.situation AND a.corrected = 1), 0) AS agreeing FROM lessons"
)


class LessonsMixin:
    # --- Lessons: AP's corrections per situation (ap_coder/ai/lessons.py) ---------------------------------------------

    def record_lesson(self, row: dict[str, Any], *, invoice_id: int | None = None, actor: str | None = None) -> None:
        """One approval's word on a situation (``lessons.outcomes``): seen, and corrected (the value AP chose, what
        was proposed) or confirmed. A situation never corrected is only counted, never shown. What ``invoice_id``'s
        approval added is kept beside it (``lesson_approvals``), to be taken back when the approval is withdrawn."""
        now = _now()
        corrected = 1 if row.get("corrected") else 0
        value, proposed = str(row.get("value") or "")[:300], str(row.get("proposed") or "")[:300]
        with self._conn() as conn:
            if invoice_id is not None:  # the same invoice's word on it counts once, the latest
                self._withdraw_lessons(conn, invoice_id, row["key"])
            before = conn.execute("SELECT corrected, value FROM lessons WHERE situation = ?", (row["key"],)).fetchone()
            # A confirmation keeps the lesson's own value: another (a blank line coded elsewhere) is none (B-955).
            confirmed = 1 if (not corrected and before is not None and before["corrected"] > 0
                              and before["value"] == value) else 0  # fmt: skip
            conn.execute(
                """INSERT INTO lessons (situation, vendor_key, vendor_name, kind, field, pattern, value, proposed, seen,
                   corrected, since_why, confirmed, last_invoice, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, ?, ?, ?)
                   ON CONFLICT(situation) DO UPDATE SET seen = lessons.seen + 1, vendor_name = excluded.vendor_name,
                   corrected = lessons.corrected + excluded.corrected,
                   since_why = lessons.since_why + excluded.since_why,
                   confirmed = lessons.confirmed + CASE WHEN excluded.corrected = 0 AND lessons.corrected > 0
                                                   AND excluded.value = lessons.value THEN 1 ELSE 0 END,
                   value = CASE WHEN excluded.corrected = 1 THEN excluded.value ELSE lessons.value END,
                   proposed = CASE WHEN excluded.corrected = 1 THEN excluded.proposed ELSE lessons.proposed END,
                   last_invoice = excluded.last_invoice, updated_at = excluded.updated_at""",
                (row["key"], vendor_key(row.get("vendor") or ""), row.get("vendor") or "", row.get("kind") or "invoice",
                 row["field"], row.get("pattern") or "", value, proposed, corrected, corrected, invoice_id, now, now),
            )  # fmt: skip
            if invoice_id is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO lesson_approvals (invoice_id, situation, corrected, confirmed, value, "
                    "proposed, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (invoice_id, row["key"], corrected, confirmed, value, proposed, now),
                )

    @staticmethod
    def _withdraw_lessons(conn: sqlite3.Connection, invoice_id: int, situation: str | None = None) -> None:
        """Take back what ``invoice_id``'s approval added to the lessons (one situation's, or all): seen, corrected,
        confirmed, and the count towards the next "why" (unless AP gave a reason since). A withdrawn correction's value
        gives way to the latest correction still standing; with none left the lesson is no longer shown."""
        where, args = "invoice_id = ?" + (" AND situation = ?" if situation else ""), (invoice_id,)
        args += (situation,) if situation else ()
        for c in conn.execute(f"SELECT * FROM lesson_approvals WHERE {where}", args).fetchall():
            lesson = conn.execute("SELECT * FROM lessons WHERE situation = ?", (c["situation"],)).fetchone()
            if lesson is None:
                continue
            value, proposed = lesson["value"], lesson["proposed"]
            if c["corrected"]:
                last = conn.execute(
                    "SELECT value, proposed FROM lesson_approvals WHERE situation = ? AND invoice_id != ? "
                    "AND corrected = 1 ORDER BY at DESC, rowid DESC LIMIT 1",
                    (c["situation"], invoice_id),
                ).fetchone()
                if last is not None:
                    value, proposed = last["value"], last["proposed"]
            since_why = c["corrected"] if not lesson["explained_at"] or c["at"] > lesson["explained_at"] else 0
            conn.execute(
                "UPDATE lessons SET seen = MAX(seen - 1, 0), corrected = MAX(corrected - ?, 0), "
                "since_why = MAX(since_why - ?, 0), confirmed = MAX(confirmed - ?, 0), value = ?, proposed = ? "
                "WHERE id = ?",
                (c["corrected"], since_why, c["confirmed"], value, proposed, lesson["id"]),
            )
        conn.execute(f"DELETE FROM lesson_approvals WHERE {where}", args)

    def lesson(self, situation: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(f"{_LESSON} WHERE situation = ?", (situation,)).fetchone()
        return dict(row) if row else None

    def lessons_for(self, key: str, kind: str | None = None) -> list[dict[str, Any]]:
        """A vendor's lessons (of one kind of document), the ones with a reason first, then the newest."""
        sql = f"{_LESSON} WHERE vendor_key = ?" + (" AND kind = ?" if kind else "")
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY explanation = '', updated_at DESC, id DESC",
                                (key, kind) if kind else (key,)).fetchall()  # fmt: skip
        return [dict(r) for r in rows]

    def lesson_history(self, situations: list[str]) -> dict[str, list[dict[str, Any]]]:
        """{situation: the approvals still standing that saw it, oldest first ({invoice_id, corrected, value, at})}:
        what ``lessons.effect`` measures a lesson with."""
        out: dict[str, list[dict[str, Any]]] = {s: [] for s in situations}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT situation, invoice_id, corrected, value, at FROM lesson_approvals WHERE "
                "situation IN (SELECT value FROM json_each(?)) ORDER BY at, rowid",
                (json.dumps(list(situations)),),
            ).fetchall()
        for r in rows:
            out[r["situation"]].append(dict(r))
        return out

    def list_lessons(self, *, include_retired: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        """Every lesson AP corrected at least once, the most corrected since its last reason first."""
        where = "corrected > 0" + ("" if include_retired else " AND retired = 0")
        with self._conn() as conn:
            rows = conn.execute(f"{_LESSON} WHERE {where} ORDER BY since_why DESC, updated_at DESC "
                                "LIMIT ?", (limit,)).fetchall()  # fmt: skip
        return [dict(r) for r in rows]

    def explain_lesson(self, situation: str, explanation: str, actor: str | None = None,
                       invoice_id: int | None = None) -> bool:  # fmt: skip
        """AP's reason for a situation (the "why" screen, or the Learning page): kept, read by the AI, and the count
        towards the next question starts again."""
        text = " ".join(str(explanation or "").split())[:600]
        if not text:
            return False
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE lessons SET explanation = ?, explained_by = ?, explained_at = ?, since_why = 0, retired = 0, "
                "updated_at = ? WHERE situation = ?",
                (text, actor or "", _now(), _now(), situation),
            )
            if cur.rowcount:
                self._log(conn, "lesson_explained", invoice_id, actor, {"situation": situation, "reason": text})
            return cur.rowcount > 0

    def retire_lesson(self, lesson_id: int, actor: str | None = None, retired: bool = True) -> bool:
        """A lesson no longer true (the vendor changed its invoices): not shown to the AI or the reviewer again."""
        with self._conn() as conn:
            cur = conn.execute("UPDATE lessons SET retired = ?, updated_at = ? WHERE id = ?",
                               (int(retired), _now(), lesson_id))  # fmt: skip
            if cur.rowcount:
                self._log(conn, "lesson_retired" if retired else "lesson_restored", actor=actor,
                          detail={"lesson": lesson_id})  # fmt: skip
            return cur.rowcount > 0

    # --- The AI reviewer's queue (ap_coder/ai/verifier.py) -----------------------------------------------------------

    def queue_verification(self, invoice_id: int, reason: str = "") -> bool:
        """Put an invoice in line for the AI reviewer: a new reading of it (the page reader's) is reviewed again, so
        one reviewed before goes back in line; one being reviewed now is left to finish (False)."""
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO verifications (invoice_id, reason, status, created_at, updated_at)
                   VALUES (?, ?, 'waiting', ?, ?) ON CONFLICT(invoice_id) DO UPDATE SET reason = excluded.reason,
                   status = 'waiting', error = '', tries = 0, updated_at = excluded.updated_at,
                   created_at = CASE WHEN verifications.status = 'waiting' THEN verifications.created_at
                                ELSE excluded.created_at END
                   WHERE verifications.status != 'verifying' OR verifications.updated_at < ?""",
                (invoice_id, reason or "", now, now, _stale_before()),
            )
            return cur.rowcount > 0

    def next_verification(self, *, after_page_reader: bool = True, reader: str = "") -> dict[str, Any] | None:
        """The invoice the AI reviewer takes next (the one waiting longest), marked as being reviewed (by ``reader``,
        the process: ``page_worker.READER_ID``); None when nothing waits. ``after_page_reader``: leave invoices still
        waiting for the page reader (it reads them first, then the reviewer reviews them as they then stand)."""
        now, stale = _now(), _stale_before()
        later = (
            " AND NOT EXISTS (SELECT 1 FROM page_reads p WHERE p.invoice_id = verifications.invoice_id "
            "AND p.status IN ('waiting', 'reading'))"
            if after_page_reader
            else ""
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT invoice_id FROM verifications WHERE {_VERIFY_IN_LINE}{later} "
                "ORDER BY created_at, invoice_id LIMIT 1",
                (stale,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE verifications SET status = 'verifying', error = '', reader = ?, updated_at = ? "
                "WHERE invoice_id = ?",
                (reader or "", now, row[0]),
            )
            taken = conn.execute("SELECT * FROM verifications WHERE invoice_id = ?", (row[0],)).fetchone()
        return dict(taken)

    def finish_verification(
        self, invoice_id: int, status: str, *, result: dict[str, Any] | None = None, rounds: int = 0,
        seconds: float = 0.0, error: str = "", tries: int | None = None, reader: str | None = None,
    ) -> bool:  # fmt: skip
        """How a review ended: ``done`` (``result``), ``failed`` / ``skipped`` (``error`` says why) or ``waiting``
        (back in line: the chat model isn't answering now). ``tries``: reviews the model server cut off before any
        answer (None keeps the count; a review done starts it over). Only a review still under way ends: one put
        back in line meanwhile (``release_verifications``, a new reading) is left there, and with ``reader`` (the
        process that took it, ``next_verification``) one another process has taken since too (B-787). False when
        nothing ended."""
        if status not in (VERIFY_WAITING, VERIFY_DONE, VERIFY_FAILED, VERIFY_SKIPPED):
            raise ValueError(f"unknown review status {status!r}")
        if tries is None and status == VERIFY_DONE:
            tries = 0
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE verifications SET status = ?, result_json = COALESCE(?, result_json), rounds = ?,
                   seconds = ?, error = ?, updated_at = ?, tries = COALESCE(?, tries), reader = ''
                   WHERE invoice_id = ? AND status = 'verifying'""" + (" AND reader = ?" if reader is not None else ""),
                (status, json.dumps(result, default=str) if result is not None else None,
                 int(rounds or 0), round(float(seconds or 0), 1), (error or "")[:2000], _now(), tries, invoice_id,
                 *(() if reader is None else (reader,))),
            )  # fmt: skip
            return cur.rowcount > 0

    def release_verifications(self, alive: Callable[[str], bool], why: str = "") -> int:
        """Put back in line every invoice marked as being reviewed by a process that isn't reviewing any more
        (``alive(reader)`` is False: closed or killed part way), at once rather than after STALE_READING_HOURS. How
        many were put back (``release_page_reads``, for the AI reviewer's queue)."""
        why = why or "interrupted (AP Coder was closed while it was reviewed): it is reviewed again"
        with self._conn() as conn:
            rows = conn.execute("SELECT invoice_id, reader FROM verifications WHERE status = 'verifying'").fetchall()
            gone = [(r["invoice_id"], r["reader"]) for r in rows if not alive(r["reader"] or "")]
            for invoice_id, reader in gone:
                conn.execute(
                    "UPDATE verifications SET status = 'waiting', reader = '', error = ?, updated_at = ? "
                    "WHERE invoice_id = ? AND status = 'verifying' AND reader = ?",
                    (why, _now(), invoice_id, reader),
                )
        return len(gone)

    def verification(self, invoice_id: int) -> dict[str, Any] | None:
        """The invoice's review: {invoice_id, reason, status, rounds, seconds, result, error, created_at,
        updated_at}, or None when it was never put in line."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM verifications WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if row is None:
            return None
        found = dict(row)
        found["result"] = json.loads(found.pop("result_json") or "null")
        if found["status"] == VERIFY_RUNNING and found["updated_at"] < _stale_before():
            found["status"] = VERIFY_WAITING
        return found

    def verification_counts(self) -> dict[str, int]:
        """How many reviews are in each state, and how the ones done ended: {waiting, verifying, done, failed,
        skipped, agreed, disagrees, unsure}."""
        with self._conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) n FROM verifications GROUP BY status").fetchall()
            ends = conn.execute(
                "SELECT json_extract(result_json, '$.status') s, COUNT(*) n FROM verifications "
                "WHERE status = 'done' AND json_valid(result_json) GROUP BY s"
            ).fetchall()
        counts = dict.fromkeys((VERIFY_WAITING, VERIFY_RUNNING, VERIFY_DONE, VERIFY_FAILED, VERIFY_SKIPPED,
                                "agreed", "disagrees", "unsure"), 0)  # fmt: skip
        counts.update({r["status"]: int(r["n"]) for r in rows})
        counts.update({r["s"]: int(r["n"]) for r in ends if r["s"]})
        return counts

    def azure_pages_on(self, day: str | None = None) -> int:
        """The pages sent to Azure on ``day`` (YYYY-MM-DD, default today): what the daily cap counts."""
        day = day or dt.date.today().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pages), 0) FROM azure_checks WHERE status = 'done' "
                "AND substr(compared_at, 1, 10) = ?",
                (day,),
            ).fetchone()
        return int(row[0] or 0)

    def azure_checks_since(self, since_iso: str) -> list[dict[str, Any]]:
        """Every check done since ``since_iso``, newest first, with the invoice's vendor, number and status:
        {invoice_id, reason, pages, compared, agreed, result, compared_at, vendor_name, invoice_number,
        invoice_status}."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT a.invoice_id, a.reason, a.pages, a.compared, a.agreed, a.result_json, a.compared_at,
                   i.vendor_name, i.invoice_number, i.status invoice_status FROM azure_checks a
                   LEFT JOIN invoices i ON i.id = a.invoice_id WHERE a.status = 'done' AND a.compared_at >= ?
                   ORDER BY a.compared_at DESC, a.invoice_id DESC""",
                (since_iso,),
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["result"] = json.loads(item.pop("result_json") or "null")
            out.append(item)
        return out

    def azure_check_counts(self) -> dict[str, int]:
        """How many checks are in each state: {waiting, checking, done, failed, skipped}."""
        with self._conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) n FROM azure_checks GROUP BY status").fetchall()
        counts = dict.fromkeys((AZURE_WAITING, AZURE_CHECKING, AZURE_DONE, AZURE_FAILED, AZURE_SKIPPED), 0)
        counts.update({r["status"]: int(r["n"]) for r in rows})
        return counts

    def update_after_check(
        self, invoice_id: int, capture: dict[str, Any] | None, issue: dict[str, Any] | None, codes: tuple[str, ...],
        meta: dict[str, Any] | None = None, *, expect: tuple[Any, ...] | None = None,
    ) -> bool:  # fmt: skip
        """Fold a check made after processing (the Azure check, the AI reviewer) into an invoice still open (in review
        or parked): the capture (fields read differently marked Check), the review issues ``codes`` replaced by
        ``issue`` (None removes them) and ``meta`` merged into the processing notes. Nothing changes on an invoice
        approved, rejected or deleted (False), nor on one that is no longer ``expect`` (``row_identity``: a backup
        was restored meanwhile)."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status, validation, meta, file_sha256, created_at FROM invoices WHERE id = ?",
                               (invoice_id,)).fetchone()  # fmt: skip
            if row is None or row["status"] not in (REVIEW, PARKED) or not _same_row(row, expect):
                return False
            validation = json.loads(row["validation"] or "null") or {}
            issues = [i for i in validation.get("issues") or [] if i.get("code") not in codes]
            if issue:
                issues.append(issue)
                validation["requires_review"] = True
            if validation:
                validation["issues"] = issues
            merged = {**(json.loads(row["meta"] or "null") or {}), **(meta or {})}
            conn.execute(
                "UPDATE invoices SET validation = ?, meta = ?, requires_review = CASE WHEN ? THEN 1 "
                "ELSE requires_review END WHERE id = ?",
                (json.dumps(validation) if validation else None, json.dumps(merged, default=str), int(bool(issue)),
                 invoice_id),
            )  # fmt: skip
            if capture is not None:
                self._save_capture(conn, invoice_id, capture)
        return True

    def _recheck_duplicates(self, vendor_name: str, invoice_number: str, key: str | None = None) -> None:
        """The open invoices (in review or parked) with this vendor and invoice number, after one of them was rejected
        or deleted, checked again for duplicates: DUPLICATE_INVOICE dropped, or said with the invoices left, and
        whether a person must look worked out again; the rest of their checks stand (B-1182)."""
        from ...coding.validation import ERROR, duplicate_message, needs_review

        for other in self.find_duplicates(vendor_name, invoice_number, key=key):
            with self._conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT status, validation, grand_total, adjusted_confidence FROM invoices "
                                   "WHERE id = ?", (other,)).fetchone()  # fmt: skip
                if row is None or row["status"] not in (REVIEW, PARKED) or not row["validation"]:
                    continue
                validation = json.loads(row["validation"])
                issues = validation.get("issues") or []
                kept = [i for i in issues if i.get("code") != "DUPLICATE_INVOICE"]
                if len(kept) == len(issues):
                    continue  # never flagged a duplicate
                still = self.find_duplicates(
                    vendor_name, invoice_number, exclude_id=other, grand_total=row["grand_total"], key=key
                )
                if still:
                    kept.insert(0, {"severity": ERROR, "code": "DUPLICATE_INVOICE", "line_number": None,
                                    "message": duplicate_message(still)})  # fmt: skip
                validation["issues"] = kept
                validation.pop("recheck", None)  # the queue's re-check said the duplicate too
                threshold = float(validation.get("review_threshold") or 0.85)
                validation["requires_review"] = needs_review(float(row["adjusted_confidence"] or 0.0),
                                                             [(i.get("severity"), i.get("code")) for i in kept],
                                                             threshold)  # fmt: skip
                conn.execute(
                    "UPDATE invoices SET validation = ?, requires_review = ? WHERE id = ?",
                    (json.dumps(validation, default=str), int(validation["requires_review"]), other),
                )

    def replace_proposal(
        self, invoice_id: int, output: dict[str, Any], report: Any, capture: Any, meta: dict[str, Any] | None = None,
        actor: str = "AP Coder", *, expect: tuple[Any, ...] | None = None,
    ) -> bool:  # fmt: skip
        """Replace what AP Coder proposes for an invoice nobody has worked on yet (the page reader read it after
        it was processed): the coding, its checks and confidence, the columns the queue shows, the capture
        (``CaptureResult`` or its dict; None keeps the stored one), and ``meta`` merged into the processing
        notes. ``report``: the ``ValidationReport`` or its dict.

        Only while the invoice waits in the review queue untouched: no edits recorded and never approved (a
        reopened invoice keeps the reviewer's coding), and still ``expect`` (``row_identity``, taken before the page
        reader read it: a backup restored meanwhile may hold another invoice under the same number). Otherwise
        nothing changes and it returns False."""
        if not output:
            return False
        validation = report.to_dict() if hasattr(report, "to_dict") else dict(report or {})
        if validation:  # checked now, against the settings as they are (B-815)
            validation[CHECKS_STAMP_KEY] = self.checks_stamp()
        if capture is not None and hasattr(capture, "to_dict"):
            capture = capture.to_dict()
        # The vendor's own key, of two whose names read alike (B-1021): as ``add_invoice`` keeps it (B-1311).
        key = self.vendor_key_for(output.get("vendor_name", ""), output.get("gst_hst_registration_number"))
        due = _due(output, self.default_terms_days(), self.vendor_terms(key))
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")  # checked and changed in one go: an approval cannot slip in between
            row = conn.execute(
                "SELECT status, edits, final_output, reviewer, ai_output, meta, file_sha256, created_at FROM invoices "
                "WHERE id = ?",
                (invoice_id,),
            ).fetchone()
            if (row is None or row["status"] != REVIEW or row["final_output"] or row["reviewer"]
                    or json.loads(row["edits"] or "null") or not _same_row(row, expect)):  # fmt: skip
                return False
            merged = {**(json.loads(row["meta"] or "null") or {}), **(meta or {})}
            conn.execute(
                """UPDATE invoices SET ai_output = ?, validation = ?, model_confidence = ?, adjusted_confidence = ?,
                   requires_review = ?, vendor_name = ?, vendor_key = ?, invoice_number = ?, invoice_date = ?,
                   currency = ?, grand_total = ?, po_key = ?, due_date = ?, meta = ? WHERE id = ?""",
                (json.dumps(output), json.dumps(validation) if validation else None,
                 validation.get("model_confidence"), validation.get("adjusted_confidence"),
                 int(bool(validation.get("requires_review", True))), output.get("vendor_name"), key,
                 output.get("invoice_number"), output.get("invoice_date"), output.get("currency"),
                 output.get("grand_total"), po_key(output.get("po_number") or ""), due,
                 json.dumps(merged, default=str), invoice_id),
            )  # fmt: skip
            if capture is not None:
                self._save_capture(conn, invoice_id, capture)
            self._log(conn, "proposal_updated", invoice_id, actor, {
                "by": "page reader", "changes": diff_coding(json.loads(row["ai_output"] or "null") or {}, output),
                "confidence": validation.get("adjusted_confidence"),
                "requires_review": bool(validation.get("requires_review", True)),
            })  # fmt: skip
        return True
