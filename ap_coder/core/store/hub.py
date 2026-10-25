"""What the finance hub feed reads (``ap_coder/erp/hub``, D-103), and the record of each import run.

Only reads, apart from ``record_import_run``: the feed never changes an invoice, a lesson or an event.
"""

from __future__ import annotations

import json
from typing import Any

from ._base import _now

# The audit trail's record of one import of invoices (the dashboard's Import invoices, ``process``, ``watch``): what the
# finance hub's ``workflow_runs`` table lists, with the hub's own runs (``hub_export``) and the ERP export batches.
IMPORT_RUN, HUB_RUN = "import_run", "hub_export"
MAX_RUN_ERRORS = 20  # error lines kept with a run (the first ones; the count says how many there were)


class HubMixin:
    def hub_invoice_rows(self) -> list[dict[str, Any]]:
        """Every invoice with what the feed's invoices and invoice_lines tables need (the JSON columns read)."""
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, status, file_name, source_path, vendor_name, vendor_key, invoice_number, invoice_date, "
                    "due_date, currency, grand_total, adjusted_confidence, requires_review, ai_output, final_output, "
                    "meta, created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_batch, "
                    "parked_reason, error FROM invoices ORDER BY id"
                )
            ]
        for r in rows:
            for column in ("ai_output", "final_output", "meta"):
                try:
                    r[column] = json.loads(r[column]) if r[column] else None
                except ValueError:
                    r[column] = None
        return rows

    def hub_events(self, after_id: int = 0, actions: list[str] | None = None) -> list[dict[str, Any]]:
        """Audit events after ``after_id``, oldest first, their detail read (``actions``: only these)."""
        sql, args = "SELECT * FROM events WHERE id > ?", [int(after_id)]
        if actions:
            sql += " AND action IN (SELECT value FROM json_each(?))"
            args.append(json.dumps(list(actions)))
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]
        for r in rows:
            try:
                r["detail"] = json.loads(r["detail"]) if r["detail"] else {}
            except ValueError:
                r["detail"] = {}
        return rows

    def hub_last_event(self) -> tuple[int, str]:
        """(the newest event's id, its stamp): a feed that saw a higher id is looking at a database restored from a
        backup since, whose next events reuse ids it already sent."""
        with self._conn() as conn:
            row = conn.execute("SELECT id, created_at, action FROM events ORDER BY id DESC LIMIT 1").fetchone()
        return (int(row["id"]), f"{row['created_at']}|{row['action']}") if row else (0, "")

    def hub_event_stamp(self, event_id: int) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT created_at, action FROM events WHERE id = ?", (int(event_id),)).fetchone()
        return f"{row['created_at']}|{row['action']}" if row else ""

    def hub_feedback_rows(self) -> list[dict[str, Any]]:
        """The lessons of approved invoices (not imported ERP history), oldest first."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT id, invoice_id, line_number, vendor_key, suggested_gl, final_gl, suggested_cc, final_cc, "
                    "outcome, reviewer, created_at FROM feedback WHERE invoice_id IS NOT NULL ORDER BY id"
                )
            ]

    def hub_outcome_rows(self) -> list[dict[str, Any]]:
        """What AP Coder proposed and AP approved, per header field of each approved invoice (``record_outcomes``)."""
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, supplier_key, invoice_id, field, ai_value, final_value, correct, source, at "
                    "FROM supplier_outcomes WHERE invoice_id IS NOT NULL ORDER BY id"
                )
            ]
        for r in rows:
            for column in ("ai_value", "final_value"):
                try:
                    r[column] = json.loads(r[column]) if r[column] else None
                except ValueError:
                    pass
        return rows

    def hub_user_roles(self) -> dict[int, dict[str, str]]:
        """{account id: {username, role}} (server mode's accounts; none on a laptop)."""
        with self._conn() as conn:
            return {int(r["id"]): {"username": r["username"], "role": r["role"]}
                    for r in conn.execute("SELECT id, username, role FROM users")}  # fmt: skip

    def record_import_run(
        self, source: str, *, files: int, ok: int, failed: int, skipped: int = 0, started_at: str = "",
        seconds: float = 0.0, errors: list[str] | None = None, actor: str | None = None,
    ) -> None:  # fmt: skip
        """One import of invoices in the audit trail (``import_run``): where from ("dashboard", "process", "watch"),
        how many files were read, coded, failed or skipped as already in AP Coder, when and how long."""
        errors = [str(e) for e in errors or [] if str(e).strip()]
        self.log_event(IMPORT_RUN, actor=actor, detail={
            "source": source, "files": int(files), "ok": int(ok), "failed": int(failed), "skipped": int(skipped),
            "started_at": started_at or _now(), "seconds": round(float(seconds), 1),
            "errors": errors[:MAX_RUN_ERRORS], "error_count": len(errors),
        })  # fmt: skip
