"""Supplier learning, touchless processing, reader scores, the Azure check's queue (the page reader's is
``page_queue``)."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import zlib
from typing import Any

from ..keys import master_vendor_id, supplier_key
from ..memory import AUTONOMOUS_REVIEWER
from ._base import (
    _AZURE_IN_LINE,
    APPROVED,
    AZURE_CHECKING,
    AZURE_DONE,
    AZURE_FAILED,
    AZURE_SKIPPED,
    AZURE_WAITING,
    DEFAULT_TOUCHLESS_LIMIT,
    KEEP_SNAPSHOTS,
    PENDING,
    TOUCHLESS_LIMIT_SETTING,
    TOUCHLESS_SETTING,
    _in_list,
    _now,
    _stale_before,
)
from .dialect import begin_write


def capture_value(stored: str | bytes) -> Any:
    """A stored capture (``invoice_capture.capture_json``): compressed JSON, or the JSON text an older version kept."""
    return json.loads(zlib.decompress(stored) if isinstance(stored, bytes) else stored)


# A stored capture's value at a JSON path, whether it is kept compressed or as text (``capture_value``).
def capture_lookup(path: str, column: str) -> str:
    return f"CASE WHEN typeof(capture_json) = 'text' THEN json_extract(capture_json, '{path}') ELSE {column} END"


class CaptureMixin:
    # --- Capture and supplier learning (see capture/supplier.py) --------------------------------------

    def save_capture(self, invoice_id: int, capture: dict[str, Any]) -> None:
        """What the capture readers found on an invoice (``CaptureResult.to_dict()``), kept with it."""
        with self._conn() as conn:
            self._save_capture(conn, invoice_id, capture)

    @staticmethod
    def _save_capture(conn: sqlite3.Connection, invoice_id: int, capture: dict[str, Any]) -> None:
        """Kept compressed (D-110: about 7 KB of JSON an invoice, read whole and only on its own), with the two
        values the queue looks up in it (``name_status``, ``credit_note``) beside it, as SQLite's JSON reads them."""
        text = json.dumps(capture, default=str)
        conn.execute(
            """INSERT INTO invoice_capture (invoice_id, capture_json, layout_source, created_at, name_status,
               credit_note) VALUES (?, ?, ?, ?, json_extract(?, '$.fields.vendor_name.status'),
               json_extract(?, '$.credit_note')) ON CONFLICT(invoice_id) DO UPDATE SET
               capture_json = excluded.capture_json, layout_source = excluded.layout_source,
               created_at = excluded.created_at, name_status = excluded.name_status,
               credit_note = excluded.credit_note""",
            (invoice_id, zlib.compress(text.encode("utf-8")), str(capture.get("layout_source") or ""), _now(), text,
             text),
        )  # fmt: skip

    @staticmethod
    def _capture_migrate(conn: sqlite3.Connection) -> None:
        """The columns a compressed capture keeps beside it (``_save_capture``), on a database made before them."""
        columns = {r[1] for r in conn.execute("PRAGMA table_info(invoice_capture)")}
        for column in ("name_status", "credit_note"):
            if column not in columns:
                conn.execute(f"ALTER TABLE invoice_capture ADD COLUMN {column}")

    def get_capture(self, invoice_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT capture_json FROM invoice_capture WHERE invoice_id = ?", (invoice_id,)
            ).fetchone()
        return capture_value(row[0]) if row else None

    def layout_sources(self, ids: list[int]) -> dict[int, str]:
        """{invoice id: how its pages were read} ("text", "ocr", "di", "mixed"), in one query; an invoice with no
        capture is left out."""
        if not ids:
            return {}
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT invoice_id, layout_source FROM invoice_capture WHERE {_in_list('invoice_id')}",
                (json.dumps([int(i) for i in ids]),),
            ).fetchall()
        return {int(r[0]): str(r[1] or "") for r in rows}

    def credit_note_ids(self, status: str) -> set[int]:
        """The invoices with ``status`` that capture read as titled a credit note, in one query (bulk approval)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT c.invoice_id FROM invoice_capture c JOIN invoices i ON i.id = c.invoice_id
                   WHERE i.status = ? AND CASE WHEN typeof(c.capture_json) = 'text' THEN
                   json_extract(c.capture_json, '$.credit_note') ELSE c.credit_note END""",
                (status,),
            ).fetchall()
        return {r[0] for r in rows}

    def supplier_key_for(self, vendor_name: str, gst_number: str | None = None) -> str:
        """The supplier key for an invoice, with the vendor master's ERP id when the master knows it."""
        vendor_name = self._aliased(vendor_name, gst_number) or vendor_name  # a name AP corrected (D-119)
        return supplier_key(vendor_name, gst_number, master_vendor_id(self.vendor_ids(), vendor_name, gst_number))

    @staticmethod
    def _profile(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        profile = dict(row)
        profile["template"] = json.loads(profile.pop("template_json")) if profile.get("template_json") else None
        return profile

    def get_supplier_profile(self, key: str) -> dict[str, Any] | None:
        """{key, display_name, vendor_id, state, autonomous_since, audit_rate, template (dict), updated_*}."""
        with self._conn() as conn:
            return self._profile(conn.execute("SELECT * FROM supplier_profiles WHERE key = ?", (key,)).fetchone())

    def list_supplier_profiles(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM supplier_profiles ORDER BY display_name COLLATE NOCASE, key").fetchall()
        return [p for p in (self._profile(r) for r in rows) if p is not None]

    def save_supplier_profile(
        self, key: str, display_name: str | None = None, vendor_id: str | None = None, template: Any = None,
        actor: str | None = None, invoice_id: int | None = None,
    ) -> bool:  # fmt: skip
        """Create or update a supplier's profile; only what is given changes (``template``: a ``Template``
        or its dict). The autonomy state is changed with ``set_supplier_state`` only. ``invoice_id``: the approval
        the template learned from; the template as it was before is kept (``template_snapshots``, the last
        ``KEEP_SNAPSHOTS`` of the supplier, in the order of approval), to be put back if that approval is withdrawn
        (D-71). False, with nothing changed, when that invoice is no longer approved (withdrawn since: B-782)."""
        if not key:
            raise ValueError("a supplier profile needs a key")
        if template is not None and hasattr(template, "to_dict"):
            template = template.to_dict()
        snapshot = invoice_id is not None and template is not None
        with self._conn() as conn:
            before = None
            if snapshot:
                begin_write(conn)  # checked and saved in one go: a withdrawal cannot slip in between
                status = conn.execute("SELECT status FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
                if status is None or status["status"] not in (APPROVED, PENDING):
                    return False
                row = conn.execute("SELECT template_json FROM supplier_profiles WHERE key = ?", (key,)).fetchone()
                before = row["template_json"] if row else None
            self._save_profile(conn, key, display_name, vendor_id, template, actor)
            if snapshot:
                after = conn.execute("SELECT template_json FROM supplier_profiles WHERE key = ?", (key,)).fetchone()
                conn.execute(
                    "INSERT OR REPLACE INTO template_snapshots (invoice_id, supplier_key, before_json, after_json, at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (invoice_id, key, before, after["template_json"], _now()),
                )
                # The newest by the order of approval (``seq``: the time is to the second), never the one just made.
                conn.execute(
                    "DELETE FROM template_snapshots WHERE supplier_key = ? AND invoice_id != ? AND seq NOT IN "
                    "(SELECT seq FROM template_snapshots WHERE supplier_key = ? ORDER BY seq DESC LIMIT ?)",
                    (key, invoice_id, key, KEEP_SNAPSHOTS),
                )
        return True

    @staticmethod
    def _withdraw_template(conn: sqlite3.Connection, invoice_id: int) -> dict[str, Any]:
        """The supplier's template as it was before ``invoice_id``'s approval taught it (``template_snapshots``),
        put back. A later approval changed it since: learned again from the later approvals without this one
        (``_replay_without``, D-119), else kept, and said (D-71)."""
        snap = conn.execute("SELECT * FROM template_snapshots WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if snap is None:
            return {}
        conn.execute("DELETE FROM template_snapshots WHERE invoice_id = ?", (invoice_id,))
        now = conn.execute("SELECT template_json FROM supplier_profiles WHERE key = ?",
                           (snap["supplier_key"],)).fetchone()  # fmt: skip
        if now is None:
            return {}
        if now["template_json"] != snap["after_json"]:
            replayed = CaptureMixin._replay_without(conn, snap)  # learned again without it, when it can be (D-119)
            if replayed:
                return replayed
            # The later approval's snapshot started from what this one taught: it now starts from what was there
            # before this one, so withdrawing it too takes back both (B-781).
            conn.execute("UPDATE template_snapshots SET before_json = ? WHERE supplier_key = ? AND before_json IS ?",
                         (snap["before_json"], snap["supplier_key"], snap["after_json"]))  # fmt: skip
            return {"layout": "kept: a later approval changed the vendor's layout since, so it was not put back"}
        conn.execute("UPDATE supplier_profiles SET template_json = ?, updated_at = ? WHERE key = ?",
                     (snap["before_json"], _now(), snap["supplier_key"]))  # fmt: skip
        # A later snapshot of this supplier started from what this approval taught: it now starts from what is back.
        conn.execute("UPDATE template_snapshots SET before_json = ? WHERE supplier_key = ? AND before_json IS ?",
                     (snap["before_json"], snap["supplier_key"], snap["after_json"]))  # fmt: skip
        return {"layout": "put back as it was before this approval"}

    @staticmethod
    def _replay_without(conn: sqlite3.Connection, snap: sqlite3.Row) -> dict[str, Any]:
        """A withdrawn approval's teaching taken back after later approvals changed the supplier's template since: the
        template learned again from what it was before the withdrawn approval, through each later approval in order
        (``workflow.replay_template``), and their snapshots made again on the way, so that withdrawing one of them later
        puts back what it found (D-119). Only when the later approvals' snapshots follow on from the withdrawn one's,
        each later invoice is still approved and its pages have a text layer (a scan is not OCR'd again here); {} when
        not, and the template is kept as it is (D-71)."""
        from ...capture.workflow import replay_template

        later = conn.execute("SELECT * FROM template_snapshots WHERE supplier_key = ? AND seq > ? ORDER BY seq",
                             (snap["supplier_key"], snap["seq"])).fetchall()  # fmt: skip
        now = conn.execute("SELECT template_json FROM supplier_profiles WHERE key = ?",
                           (snap["supplier_key"],)).fetchone()  # fmt: skip
        if not later or now is None or later[-1]["after_json"] != now["template_json"]:
            return {}
        if any(b["before_json"] != a["after_json"] for a, b in zip([snap, *later], later, strict=False)):
            return {}  # changed by something else in between (reset on the Learning page): not followed
        approvals = []
        for s in later:
            inv = conn.execute("SELECT status, source_path, file_sha256, final_output, ai_output FROM invoices "
                               "WHERE id = ?", (s["invoice_id"],)).fetchone()  # fmt: skip
            if inv is None or inv["status"] not in (APPROVED, PENDING) or not inv["final_output"]:
                return {}
            row = conn.execute("SELECT capture_json FROM invoice_capture WHERE invoice_id = ?",
                               (s["invoice_id"],)).fetchone()  # fmt: skip
            capture = capture_value(row[0]) if row else {}
            event = conn.execute("SELECT detail FROM events WHERE invoice_id = ? AND action = 'approved' "
                                 "ORDER BY id DESC LIMIT 1", (s["invoice_id"],)).fetchone()  # fmt: skip
            approvals.append({
                "source_path": inv["source_path"], "file_sha256": inv["file_sha256"],
                "final": json.loads(inv["final_output"]), "ai": json.loads(inv["ai_output"] or "{}"),
                "taught": capture.get("taught") or {}, "taught_values": capture.get("taught_values") or {},
                "layout_source": capture.get("layout_source") or "",
                "bulk": bool(json.loads(event["detail"] or "{}").get("bulk")) if event else False,
            })  # fmt: skip
        try:
            afters = replay_template(json.loads(snap["before_json"]) if snap["before_json"] else None, approvals)
        except Exception:  # learning must never block a withdrawal: the template is kept as it is
            return {}
        if afters is None:
            return {}
        before = snap["before_json"]
        for s, after in zip(later, afters, strict=True):
            text = json.dumps(after)
            conn.execute("UPDATE template_snapshots SET before_json = ?, after_json = ? WHERE seq = ?",
                         (before, text, s["seq"]))  # fmt: skip
            before = text
        conn.execute("UPDATE supplier_profiles SET template_json = ?, updated_at = ? WHERE key = ?",
                     (before, _now(), snap["supplier_key"]))  # fmt: skip
        return {"layout": "taken back: the vendor's layout learned again from the approvals made since"}

    def approval_generation(self, invoice_id: int) -> int:
        """How many times the invoice was approved (its ``approved`` events): which approval learning learns from, so
        one withdrawn and made again meanwhile is told apart from the one still standing (B-960)."""
        with self._conn() as conn:
            return self._generation(conn, invoice_id)

    @staticmethod
    def _generation(conn: sqlite3.Connection, invoice_id: int) -> int:
        return int(conn.execute("SELECT COUNT(*) FROM events WHERE invoice_id = ? AND action = 'approved'",
                                (invoice_id,)).fetchone()[0])  # fmt: skip

    def forget_if_withdrawn(self, invoice_id: int, generation: int | None = None) -> bool:
        """After learning from an approval: if the invoice is no longer approved (withdrawn while it was learned
        from), or approved again since (``generation``: ``approval_generation`` when learning began, B-960), what was
        learned is taken back (``_forget_learning``), in the same transaction as the check (B-782). True when it
        was. A deleted invoice is left to ``delete_invoice``."""
        with self._conn() as conn:
            begin_write(conn)
            row = conn.execute("SELECT status FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
            if row is None:
                return False
            standing = row["status"] in (APPROVED, PENDING)
            if standing and (generation is None or self._generation(conn, invoice_id) == generation):
                return False
            self._forget_learning(conn, invoice_id)
        return True

    def reset_supplier_layout(self, key: str, actor: str | None = None) -> bool:
        """Forget what a supplier's template learned of its layout (the Learning page's "Reset layout"): its next
        invoices are read as a new vendor's, and the template learns again from the next approvals. Logged
        (``layout_reset``). False when there is no such supplier."""
        with self._conn() as conn:
            row = conn.execute("SELECT display_name, template_json FROM supplier_profiles WHERE key = ?",
                               (key,)).fetchone()  # fmt: skip
            if row is None:
                return False
            conn.execute("UPDATE supplier_profiles SET template_json = NULL, updated_at = ?, updated_by = ? "
                         "WHERE key = ?", (_now(), actor, key))  # fmt: skip
            conn.execute("DELETE FROM template_snapshots WHERE supplier_key = ?", (key,))
            self._log(conn, "layout_reset", actor=actor, detail={
                "key": key, "vendor": row["display_name"] or key, "had_layout": row["template_json"] is not None,
            })  # fmt: skip
        return True

    @staticmethod
    def _save_profile(
        conn: sqlite3.Connection, key: str, display_name: str | None, vendor_id: str | None,
        template: dict[str, Any] | None, actor: str | None,
    ) -> None:  # fmt: skip
        conn.execute(
            """INSERT INTO supplier_profiles (key, display_name, vendor_id, template_json, updated_at, updated_by)
               VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET
               display_name = COALESCE(NULLIF(?, ''), display_name), vendor_id = COALESCE(NULLIF(?, ''), vendor_id),
               template_json = COALESCE(?, template_json), updated_at = excluded.updated_at,
               updated_by = COALESCE(excluded.updated_by, updated_by)""",
            (
                key, display_name or "", vendor_id or "", json.dumps(template) if template is not None else None,
                _now(), actor, display_name or "", vendor_id or "",
                json.dumps(template) if template is not None else None,
            ),
        )  # fmt: skip

    # --- Touchless processing: one switch and one bar for every supplier ------------------------------------------

    def autonomy_policy(self) -> Any:
        """The bar every supplier must meet to go touchless (``AutonomyPolicy``): fixed standard values, the same
        for every supplier and every installation. An ``autonomy_policy`` setting left by an older version is
        ignored."""
        from ...capture.supplier import AutonomyPolicy

        return AutonomyPolicy()

    def touchless_enabled(self) -> bool:
        """Touchless processing (Settings → Automation), off until an AP lead turns it on: while it is off, every
        invoice is approved by a person, whatever its supplier's record."""
        return self.get_setting(TOUCHLESS_SETTING) == "on"

    def set_touchless(self, on: bool, actor: str | None) -> list[dict[str, Any]]:
        """Turn touchless processing on or off for the whole company (recorded in the audit trail with who did it).
        Turned on, every supplier that meets the bar goes touchless at once and any supplier made touchless by hand
        under another bar that does not meet it goes back to review (``sync_autonomy``); returns those changes.
        Turned off, suppliers keep their record and state, and nothing is approved without a person."""
        if on == self.touchless_enabled():
            return []
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (TOUCHLESS_SETTING, "on" if on else "off"),
            )
            self._log(conn, "touchless_on" if on else "touchless_off", actor=actor, detail={
                "policy": self.autonomy_policy().to_dict(), "limit": self.touchless_limit(),
            })  # fmt: skip
        return self.sync_autonomy() if on else []

    def touchless_limit(self) -> float:
        """The largest invoice total approved without a person (Settings → Automation), in CAD: a foreign-currency
        total is converted at the exchange rates in Settings → Review (without a rate it goes to a person:
        ``capture.workflow.invoice_gates``)."""
        try:
            value = float(self.get_setting(TOUCHLESS_LIMIT_SETTING) or DEFAULT_TOUCHLESS_LIMIT)
        except ValueError:
            return DEFAULT_TOUCHLESS_LIMIT
        return value if value > 0 else DEFAULT_TOUCHLESS_LIMIT

    def set_touchless_limit(self, amount: float, actor: str | None) -> bool:
        """Set the touchless limit (must be above zero). Returns whether it changed (recorded in the audit trail)."""
        amount = round(float(amount), 2)
        if amount <= 0:
            raise ValueError("the touchless limit must be above zero")
        before = self.touchless_limit()
        if amount == before:
            return False
        self.set_setting(TOUCHLESS_LIMIT_SETTING, f"{amount:.2f}", actor=actor)
        self.log_event("settings_changed", actor=actor,
                       detail={"keys": [TOUCHLESS_LIMIT_SETTING], "from": before, "to": amount})  # fmt: skip
        return True

    def over_touchless_limit(self, coding: dict[str, Any]) -> bool:
        """Is this invoice's total above the largest amount approved without a person?"""
        # A currency with no rate is its own touchless gate (``capture.workflow.invoice_gates``), said as such.
        return self.over_approval_limit(coding, limit=self.touchless_limit(), unknown_rate=False)

    def sync_autonomy(self, key: str | None = None) -> list[dict[str, Any]]:
        """With touchless processing on, move each supplier (or the one ``key``) to the state its record earns
        (``supplier.automatic_transition``): it goes touchless by itself once it meets the bar, and back to review
        when it no longer may. Each change is recorded in the audit trail by "AP Coder". Returns the changes."""
        from ...capture.supplier import automatic_transition

        if not self.touchless_enabled():
            return []
        policy = self.autonomy_policy()
        profiles = [p for p in [self.get_supplier_profile(key)] if p] if key else self.list_supplier_profiles()
        changes = []
        for p in profiles:
            stats = self.supplier_stats(p["key"], policy.window)
            move = automatic_transition(stats, policy, p["state"] or "supervised", p["autonomous_since"],
                                        p.get("suspended_at"))  # fmt: skip
            if move is None:
                continue
            state, reason = move
            with self._conn() as conn:
                self._set_state(conn, p, state, "AP Coder", reason, policy)
            changes.append({"key": p["key"], "supplier": p["display_name"] or p["key"], "from": p["state"],
                            "to": state, "reason": reason})  # fmt: skip
        return changes

    def set_supplier_state(self, key: str, state: str, by: str | None, reason: str = "") -> None:
        """Set a supplier's state: ``held`` (an AP lead keeps it supervised, whatever its record), ``supervised``
        (back to the automatic bar, e.g. "Allow again" after ``held``), ``suspended`` (a person found an error in a
        touchless invoice) or ``autonomous`` (only when it meets the bar; touchless processing does this by itself).
        Recorded in the audit trail with who did it and why."""
        from ...capture.supplier import AUTONOMOUS, STORED_STATES, SUSPENDED, meets_policy

        if state not in STORED_STATES:
            raise ValueError(f"unknown supplier state {state!r}")
        profile = self.get_supplier_profile(key)
        if profile is None:
            raise KeyError(key)
        policy = self.autonomy_policy()
        if state == AUTONOMOUS and profile["state"] != AUTONOMOUS:
            if not meets_policy(self.supplier_stats(key, policy.window), policy):
                raise ValueError(f"{profile['display_name'] or key} does not meet the autonomy policy yet")
        if state == SUSPENDED and profile["state"] != AUTONOMOUS:
            # Not touchless now (kept supervised by an AP lead, or suspended already), but one of its touchless
            # invoices was found wrong: its state stays, its clean streak starts again from now (B-959).
            with self._conn() as conn:
                conn.execute("UPDATE supplier_profiles SET suspended_at = ?, updated_at = ?, updated_by = ? "
                             "WHERE key = ?", (_now(), _now(), by, key))  # fmt: skip
                self._log(conn, "autonomy_suspended", actor=by, detail={
                    "supplier": profile["display_name"] or key, "key": key, "from": profile["state"],
                    "to": profile["state"], **({"reason": reason} if reason else {})})  # fmt: skip
            return
        if state == profile["state"]:
            return
        with self._conn() as conn:
            self._set_state(conn, profile, state, by, reason, policy)
        if state != SUSPENDED:  # "Allow again": it may go touchless straight away
            self.sync_autonomy(key)

    def _set_state(
        self, conn: sqlite3.Connection, profile: dict[str, Any], state: str, by: str | None, reason: str,
        policy: Any,
    ) -> None:  # fmt: skip
        from ...capture.supplier import AUTONOMOUS, HELD, SUPERVISED, SUSPENDED

        key = profile["key"]
        if state == HELD:
            action = "autonomy_held"
        elif state == SUPERVISED and profile["state"] == HELD:
            action = "autonomy_allowed"
        else:
            action = {AUTONOMOUS: "autonomy_on", SUSPENDED: "autonomy_suspended"}.get(state, "autonomy_off")
        rate = profile["audit_rate"]
        if state == AUTONOMOUS and rate is None:
            rate = policy.audit_rate
        # Since when it is touchless (kept while suspended, to show; cleared otherwise), and when it was suspended
        # (its fresh clean streak counts from then: kept while an AP lead keeps it supervised and once allowed again,
        # cleared when it is touchless again).
        since = _now() if state == AUTONOMOUS else profile["autonomous_since"] if state == SUSPENDED else None
        suspended = _now() if state == SUSPENDED else None if state == AUTONOMOUS else profile.get("suspended_at")
        conn.execute(
            "UPDATE supplier_profiles SET state = ?, autonomous_since = ?, suspended_at = ?, audit_rate = ?, "
            "updated_at = ?, updated_by = ? WHERE key = ?",
            (state, since, suspended, rate, _now(), by, key),
        )
        self._log(conn, action, actor=by, detail={
            "supplier": profile["display_name"] or key, "key": key, "from": profile["state"], "to": state,
            **({"reason": reason} if reason else {}),
            **({"audit_rate": rate, "policy": policy.to_dict()} if state == AUTONOMOUS else {}),
        })  # fmt: skip

    def touchless_summary(self, days: int = 30) -> dict[str, Any]:
        """The touchless numbers over the last ``days`` days: invoices processed, approved without a person,
        audited (a touchless candidate picked for a person to check) and how many of those a person corrected, and
        touchless invoices reopened (a person found something wrong after the fact)."""
        since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
        with self._conn() as conn:
            processed = conn.execute(
                "SELECT COUNT(DISTINCT invoice_id) FROM events WHERE action = 'processed' AND created_at >= ?", (since,)
            ).fetchone()[0]
            touchless = conn.execute(
                "SELECT COUNT(DISTINCT invoice_id) FROM events WHERE action = 'approved' AND actor = ? "
                "AND created_at >= ?",
                (AUTONOMOUS_REVIEWER, since),
            ).fetchone()[0]
            audits = conn.execute(  # (off the audits' own index, ``supplier_outcomes_audit``: grouped after, D-206)
                "SELECT invoice_id, MIN(correct) AS clean FROM supplier_outcomes WHERE source = 'audit' AND at >= ? "
                "GROUP BY +invoice_id",
                (since,),
            ).fetchall()
            reopened = conn.execute(
                "SELECT COUNT(DISTINCT r.invoice_id) FROM events r WHERE r.action = 'reopened' AND r.created_at >= ? "
                "AND EXISTS (SELECT 1 FROM events a WHERE a.invoice_id = r.invoice_id AND a.action = 'approved' "
                "AND a.actor = ?)",
                (since, AUTONOMOUS_REVIEWER),
            ).fetchone()[0]
        return {
            "days": days, "processed": processed, "touchless": touchless,
            "touchless_rate": touchless / processed if processed else None,
            "audited": len(audits), "audit_errors": sum(1 for r in audits if not r["clean"]), "reopened": reopened,
        }  # fmt: skip

    def record_outcomes(
        self, supplier_key: str, invoice_id: int | None, rows: list[dict[str, Any]], source: str = "review",
        display_name: str = "", at: str | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        """Record, per header field, what AP Coder proposed and what AP approved (``outcome_rows``).

        Recording the same invoice and source again replaces its rows (an invoice approved again after
        a reopen). A correction on an autonomous supplier suspends it at once; with touchless processing on, a
        supplier this invoice brings up to the bar goes touchless (``sync_autonomy``)."""
        from ...capture.supplier import AUTONOMOUS, SUSPENDED

        if source not in ("review", "audit"):
            raise ValueError(f"unknown outcome source {source!r}")
        if not supplier_key:
            return {"fields": 0, "corrections": 0, "suspended": False, "touchless": False}
        at = at or _now()
        corrections = [r["field"] for r in rows if not r.get("correct")]
        with self._conn() as conn:
            self._save_profile(conn, supplier_key, display_name, "", None, None)
            if invoice_id is not None:
                conn.execute("DELETE FROM supplier_outcomes WHERE invoice_id = ? AND source = ?", (invoice_id, source))
            conn.executemany(
                """INSERT INTO supplier_outcomes (supplier_key, invoice_id, field, ai_value, final_value, correct,
                   source, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (supplier_key, invoice_id, r["field"], json.dumps(r.get("ai_value"), default=str),
                     json.dumps(r.get("final_value"), default=str), 1 if r.get("correct") else 0, source, at)
                    for r in rows
                ],
            )  # fmt: skip
            state = conn.execute("SELECT state, display_name FROM supplier_profiles WHERE key = ?", (supplier_key,))
            profile = state.fetchone()
            suspended = bool(corrections) and profile["state"] == AUTONOMOUS
            if suspended:
                conn.execute(
                    "UPDATE supplier_profiles SET state = ?, suspended_at = ?, updated_at = ?, updated_by = ? "
                    "WHERE key = ?",
                    (SUSPENDED, at, _now(), "AP Coder", supplier_key),
                )
                self._log(conn, "autonomy_suspended", invoice_id, "AP Coder", {
                    "supplier": profile["display_name"] or supplier_key, "key": supplier_key, "from": AUTONOMOUS,
                    "to": SUSPENDED, "reason": f"{source} found a correction: {', '.join(corrections)}",
                })  # fmt: skip
        promoted = self.sync_autonomy(supplier_key)
        return {"fields": len(rows), "corrections": len(corrections), "suspended": suspended,
                "touchless": any(c["to"] == AUTONOMOUS for c in promoted)}  # fmt: skip

    def supplier_outcomes(self, key: str, limit: int = 5000) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM supplier_outcomes WHERE supplier_key = ? ORDER BY at DESC, id DESC LIMIT ?", (key, limit)
            )]  # fmt: skip
        for r in rows:
            for column in ("ai_value", "final_value"):
                r[column] = json.loads(r[column]) if r[column] else None
            r["correct"] = bool(r["correct"])
        return rows

    def supplier_stats(self, key: str, window: int = 200) -> Any:
        """``SupplierStats`` for a supplier: field accuracy over its last ``window`` reviewed invoices."""
        from ...capture.supplier import SupplierStats

        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(DISTINCT invoice_id) FROM supplier_outcomes WHERE supplier_key = ?", (key,)
            ).fetchone()[0]
            rows = conn.execute(
                """SELECT invoice_id, field, correct, source, at FROM supplier_outcomes WHERE supplier_key = ?
                   AND invoice_id IN (SELECT invoice_id FROM supplier_outcomes WHERE supplier_key = ?
                   GROUP BY invoice_id ORDER BY MAX(at) DESC, invoice_id DESC LIMIT ?)""",
                (key, key, window),
            ).fetchall()
        return SupplierStats.from_rows(
            [dict(r) | {"correct": bool(r["correct"])} for r in rows], key=key, window=window, total_invoices=total
        )

    def supplier_keys_for_invoices(self, ids: list[int]) -> list[str]:
        if not ids:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT supplier_key FROM supplier_outcomes WHERE {_in_list('invoice_id')}",
                (json.dumps([int(i) for i in ids]),),
            ).fetchall()
        return [r[0] for r in rows]

    def prune_supplier_profiles(self, keys: list[str]) -> int:
        """Delete these supplier profiles when nothing is recorded for them any more (e.g. after the demo)."""
        if not keys:
            return 0
        with self._conn() as conn:
            return conn.execute(
                f"DELETE FROM supplier_profiles WHERE {_in_list('key')} AND state != 'autonomous' AND key NOT IN "
                "(SELECT DISTINCT supplier_key FROM supplier_outcomes)",
                (json.dumps(list(keys)),),
            ).rowcount

    # --- A supplier's line descriptions as AP approved them (D-120) ---------------------------------------------

    def remember_line_texts(self, key: str, rows: list[dict[str, Any]], *, invoice_id: int | None = None) -> int:
        """The lines of an approval of supplier ``key`` (``line_memory.observations``: {"read_key", "printed",
        "description", "corrected"}), kept with the approval (``invoice_id``): withdrawn with it, and replaced when it
        is learned from again. Returns how many were corrected."""
        if not key:
            return 0
        at = _now()
        with self._conn() as conn:
            if invoice_id is not None:
                conn.execute("DELETE FROM line_texts WHERE invoice_id = ?", (invoice_id,))
            conn.executemany(
                "INSERT INTO line_texts (supplier_key, read_key, printed, description, corrected, invoice_id, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        key,
                        r["read_key"],
                        r.get("printed") or "",
                        r["description"],
                        int(bool(r.get("corrected"))),
                        invoice_id,
                        at,
                    )
                    for r in rows
                    if r.get("read_key") and r.get("description")
                ],
            )
        return sum(1 for r in rows if r.get("corrected"))

    def line_texts_stamp(self) -> tuple[Any, ...]:
        """What tells whether the line descriptions AP corrected changed (any added, replaced or withdrawn): their
        newest id and their count. No count of changes follows the table (B-1839)."""
        with self._conn() as conn:
            return tuple(conn.execute("SELECT MAX(id), COUNT(*) FROM line_texts").fetchone())

    def line_texts(self, key: str, limit: int = 2000) -> list[dict[str, Any]]:
        """Supplier ``key``'s remembered lines, newest first."""
        if not key:
            return []
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT read_key, printed, description, corrected, invoice_id, at FROM line_texts "
                "WHERE supplier_key = ? ORDER BY id DESC LIMIT ?", (key, int(limit)))]  # fmt: skip

    # --- Each reader against what AP approved; the page reader's queue -------------------------------------------

    def record_reader_outcomes(
        self, invoice_id: int, rows: list[dict[str, Any]], at: str | None = None, reader: str | None = None
    ) -> int:
        """What each capture reader read on an approved invoice, against what AP approved
        (``capture.workflow.reader_outcome_rows``): one row per reader and field; reader "fused" is the value
        AP saw, with its evidence key and status. Recording an invoice again replaces its rows (an invoice
        approved again after a reopen). ``reader``: only that reader's rows are replaced (the Azure check, run on an
        invoice already approved, adds reader "di" beside the others). Returns how many rows were kept."""
        at = at or _now()
        if reader:
            rows = [r for r in rows if r.get("reader") == reader]

        def text(value: Any) -> str | None:
            return None if value is None else str(value)

        keep = [
            (invoice_id, str(r["reader"]), str(r["field"]), text(r.get("read_value")), text(r.get("final_value")),
             1 if r.get("correct") else 0, str(r.get("evidence") or ""), str(r.get("layout_source") or ""),
             str(r.get("status") or ""), str(r.get("at") or at))
            for r in rows
            if r.get("reader") and r.get("field")
        ]  # fmt: skip
        with self._conn() as conn:
            if reader:
                conn.execute("DELETE FROM reader_outcomes WHERE invoice_id = ? AND reader = ?", (invoice_id, reader))
            else:
                conn.execute("DELETE FROM reader_outcomes WHERE invoice_id = ?", (invoice_id,))
            conn.executemany(
                """INSERT INTO reader_outcomes (invoice_id, reader, field, read_value, final_value, correct, evidence,
                   layout_source, status, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                keep,
            )
        return len(keep)

    @staticmethod
    def _score(row: sqlite3.Row) -> dict[str, Any]:
        fields, agreed = int(row["fields"]), int(row["agreed"] or 0)
        return {"fields": fields, "agreed": agreed, "rate": agreed / fields if fields else None,
                "invoices": int(row["invoices"])}  # fmt: skip

    def reader_scorecard(self) -> list[dict[str, Any]]:
        """Each reader's record against AP's approvals: {reader, fields (compared), agreed (with AP), rate,
        invoices}. Reader "fused" is the combined value AP saw on the review screen. Kept until a reader outcome
        changes (D-162)."""
        kept = self.summary_memo("reader_scorecard", self._reader_scorecard, topic="", changes="readers")
        return [dict(r) for r in kept]

    def _reader_scorecard(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT reader, COUNT(*) fields, SUM(correct) agreed, COUNT(DISTINCT invoice_id) invoices
                   FROM reader_outcomes GROUP BY reader ORDER BY reader"""
            ).fetchall()
        return [{"reader": r["reader"], **self._score(r)} for r in rows]

    def reader_field_scorecard(self, reader: str) -> list[dict[str, Any]]:
        """One reader's record field by field, the weakest first: {field, fields, agreed, rate, invoices,
        last_read, last_final}, the last two being its latest disagreement with AP (None if it always agreed)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT field, COUNT(*) fields, SUM(correct) agreed, COUNT(DISTINCT invoice_id) invoices
                   FROM reader_outcomes WHERE reader = ? GROUP BY field""",
                (reader,),
            ).fetchall()
            # With MAX(id), SQLite takes the other columns from that row: the latest disagreement of each field.
            latest = {
                r["field"]: r
                for r in conn.execute(
                    """SELECT field, MAX(id) id, read_value, final_value FROM reader_outcomes
                       WHERE reader = ? AND correct = 0 GROUP BY field""",
                    (reader,),
                )
            }
        out = []
        for r in rows:
            wrong = latest.get(r["field"])
            out.append({"field": r["field"], **self._score(r), "last_read": wrong["read_value"] if wrong else None,
                        "last_final": wrong["final_value"] if wrong else None})  # fmt: skip
        return sorted(out, key=lambda r: (r["rate"], r["field"]))

    def fused_status_scorecard(self) -> list[dict[str, Any]]:
        """How often the value AP saw was the one AP approved, by the status it showed (verified first):
        {status, fields, agreed, rate, invoices}."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT status, COUNT(*) fields, SUM(correct) agreed, COUNT(DISTINCT invoice_id) invoices
                   FROM reader_outcomes WHERE reader = 'fused' AND status != '' GROUP BY status"""
            ).fetchall()
        order = {"verified": 0, "likely": 1, "check": 2}
        scores = [{"status": r["status"], **self._score(r)} for r in rows]
        return sorted(scores, key=lambda r: order.get(r["status"], 9))

    def fused_kind_scorecard(self) -> dict[str, dict[str, Any]]:
        """How often the value AP saw was the one AP approved, by how the invoice came in: {"text": vendor PDFs (their
        text inside), "scan": scans and photos (read with OCR)} -> {fields, agreed, rate, invoices}."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT CASE WHEN layout_source = 'text' THEN 'text' ELSE 'scan' END kind, COUNT(*) fields,
                          SUM(correct) agreed, COUNT(DISTINCT invoice_id) invoices
                   FROM reader_outcomes WHERE reader = 'fused' AND layout_source IN ('text', 'ocr', 'di', 'mixed')
                   GROUP BY kind"""
            ).fetchall()
        return {r["kind"]: self._score(r) for r in rows}

    def evidence_counts(self) -> dict[str, tuple[int, int]]:
        """{evidence key: (values, right)}: how often each pattern of readers and checks
        (``capture.confidence.evidence_key``) gave the value AP approved on this company's own invoices (the
        values AP saw, reader "fused"). The local calibration of confidence counts them."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT evidence, COUNT(*) n, SUM(correct) ok FROM reader_outcomes
                   WHERE reader = 'fused' AND evidence != '' GROUP BY evidence"""
            ).fetchall()
        return {r["evidence"]: (int(r["n"]), int(r["ok"] or 0)) for r in rows}

    def supplier_templates_count(self) -> int:
        """How many suppliers have a learned template (for "How AP Coder reads every invoice"), without reading
        them: off the partial index of those (``supplier_profiles_template``, B-1736)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM supplier_profiles WHERE template_json IS NOT NULL "
                "AND template_json NOT IN ('', 'null', '{}')"
            ).fetchone()
        return int(row[0]) if row else 0

    # --- The Azure check's queue (ap_coder/ai/azure_check.py) -----------------------------------------------------

    def queue_azure_check(self, invoice_id: int, reason: str) -> bool:
        """Put an invoice in line for the Azure check (``reason``: "low-confidence" or "spot-check"). One already in
        line or checked stays as it is, except that a low-confidence reason replaces a spot-check still waiting, and
        one skipped before is put back in line. False when nothing changed."""
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO azure_checks (invoice_id, reason, status, created_at, updated_at)
                   VALUES (?, ?, 'waiting', ?, ?) ON CONFLICT(invoice_id) DO UPDATE SET reason = excluded.reason,
                   status = 'waiting', error = '', updated_at = excluded.updated_at
                   WHERE azure_checks.status = 'skipped' OR (azure_checks.status = 'waiting'
                   AND azure_checks.reason != excluded.reason AND excluded.reason = 'low-confidence')""",
                (invoice_id, reason, now, now),
            )
            return cur.rowcount > 0

    def next_azure_check(self, *, after_page_reader: bool = False) -> dict[str, Any] | None:
        """The invoice the Azure check takes next (the one waiting longest), marked as being checked; None when
        nothing waits. ``after_page_reader``: leave invoices still waiting for the page reader (OvisOCR2 is running
        and will read them): they are checked once it has, on the invoice as it then stands."""
        now, stale = _now(), _stale_before()
        later = (
            " AND NOT EXISTS (SELECT 1 FROM page_reads p WHERE p.invoice_id = azure_checks.invoice_id "
            "AND p.status IN ('waiting', 'reading'))"
            if after_page_reader
            else ""
        )
        with self._conn() as conn:
            # Its own: the claim commits before the check starts (Azure's minutes, outside any transaction): never the
            # write lock held, and every other writer waiting, all that time.
            self._begin_alone(conn, "next_azure_check")
            row = conn.execute(
                f"SELECT invoice_id FROM azure_checks WHERE {_AZURE_IN_LINE}{later} "
                "ORDER BY created_at, invoice_id LIMIT 1",
                (stale,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE azure_checks SET status = 'checking', error = '', updated_at = ? WHERE invoice_id = ?",
                (now, row[0]),
            )
            taken = conn.execute("SELECT * FROM azure_checks WHERE invoice_id = ?", (row[0],)).fetchone()
        return dict(taken)

    def finish_azure_check(
        self, invoice_id: int, status: str, *, result: dict[str, Any] | None = None, pages: int = 0,
        compared: int = 0, agreed: int = 0, error: str = "", reason: str | None = None,
    ) -> bool:  # fmt: skip
        """How a check ended: ``done`` (``result``: Azure's values and the comparison), ``failed`` (``error``),
        ``skipped`` (nothing to check), or ``waiting`` (back in line, e.g. the day's page cap is reached)."""
        if status not in (AZURE_WAITING, AZURE_DONE, AZURE_FAILED, AZURE_SKIPPED):
            raise ValueError(f"unknown Azure check status {status!r}")
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE azure_checks SET status = ?, result_json = COALESCE(?, result_json), pages = ?,
                   compared = ?, agreed = ?, error = ?, updated_at = ?, reason = COALESCE(?, reason),
                   compared_at = CASE WHEN ? = 'done' THEN ? ELSE compared_at END WHERE invoice_id = ?""",
                (status, json.dumps(result, default=str) if result is not None else None, int(pages or 0),
                 int(compared or 0), int(agreed or 0), (error or "")[:2000], now, reason, status, now, invoice_id),
            )  # fmt: skip
            return cur.rowcount > 0

    def azure_check(self, invoice_id: int) -> dict[str, Any] | None:
        """The invoice's Azure check: {invoice_id, reason, status, pages, compared, agreed, result (dict or None),
        error, created_at, updated_at, compared_at}, or None when it was never put in line."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM azure_checks WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if row is None:
            return None
        check = dict(row)
        check["result"] = json.loads(check.pop("result_json") or "null")
        if check["status"] == AZURE_CHECKING and check["updated_at"] < _stale_before():
            check["status"] = AZURE_WAITING
        return check
