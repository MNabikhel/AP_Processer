"""Every supplier's record at once, for the pages that list them all (Learning → Supplier learning, Settings →
Automation, D-157): worked out by SQLite in a few queries, not three a supplier (``capture.py`` keeps one supplier's:
``supplier_stats``, ``supplier_outcomes``)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ._base import _in_list, _now


def _ranked(where: str = "1") -> str:
    """Each supplier's reviewed invoices (of the outcomes ``where`` keeps), newest first (``n``), as ``supplier_stats``
    picks its window: by the time of each invoice's latest outcome, then its id, the outcomes recorded without an
    invoice taking a place in it too; with what ``SupplierStats.from_rows`` works out of each, and ``total``: the
    supplier's invoices all time."""
    return f"""WITH inv AS (
  SELECT supplier_key, invoice_id, MAX(at) AS at, COUNT(*) AS fields, SUM(correct = 0) AS corrections,
         json_group_array(DISTINCT COALESCE(NULLIF(source, ''), 'review')) AS sources
  FROM supplier_outcomes WHERE {where} GROUP BY supplier_key, invoice_id),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY supplier_key ORDER BY at DESC, invoice_id DESC) AS n,
         COUNT(invoice_id) OVER (PARTITION BY supplier_key) AS total
  FROM inv)"""


class SuppliersMixin:
    def supplier_profiles_brief(self) -> list[dict[str, Any]]:
        """``list_supplier_profiles`` without each learned template: whether it has one (``has_layout``, as
        ``supplier_templates_count`` counts them)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            return [dict(r) for r in conn.execute(
                "SELECT key, display_name, state, autonomous_since, suspended_at, COALESCE(template_json NOT IN "
                "('', 'null', '{}'), 0) AS has_layout FROM supplier_profiles ORDER BY display_name COLLATE NOCASE, key"
            )]  # fmt: skip

    def all_supplier_stats(self, keys: list[str], window: int = 200, fields: bool = True) -> dict[str, Any]:
        """{key: ``supplier_stats(key, window)``} of these suppliers, in two queries; without ``fields``, in one, each
        header field's figures (``SupplierStats.fields``, ``supplier_fields``) left empty: every other figure is the
        same, worked out of each invoice's. Without ``fields``, kept until a supplier outcome changes (D-162): read
        it, don't change it."""
        if not fields:
            return self.summary_memo(f"supplier_stats/{window}",  # type: ignore[attr-defined]
                                     lambda: self._all_supplier_stats(keys, window, False), topic="",
                                     changes="suppliers", asked=tuple(keys))  # fmt: skip
        return self._all_supplier_stats(keys, window, True)

    def _all_supplier_stats(self, keys: list[str], window: int, fields: bool) -> dict[str, Any]:
        from ...capture.supplier import SupplierStats

        invoices: dict[str, list[dict[str, Any]]] = {key: [] for key in keys}
        totals: dict[str, int] = {}
        with self._conn() as conn:  # type: ignore[attr-defined]
            for r in conn.execute(f"{_ranked()} SELECT * FROM ranked WHERE n <= ?", (window,)):
                totals[r["supplier_key"]] = r["total"]
                if r["invoice_id"] is not None and r["supplier_key"] in invoices:
                    invoices[r["supplier_key"]].append({
                        "invoice_id": r["invoice_id"], "at": r["at"] or "", "fields": r["fields"],
                        "corrections": r["corrections"], "sources": json.loads(r["sources"]),
                    })  # fmt: skip
        each = self.supplier_fields(keys, window) if fields else {}
        return {key: SupplierStats.from_invoices(invoices[key], each.get(key), key=key, window=window,
                                                 total_invoices=totals.get(key, 0)) for key in keys}  # fmt: skip

    def supplier_fields(self, keys: list[str], window: int = 200) -> dict[str, dict[str, Any]]:
        """{key: {field: ``FieldStat``}}: each header field read and read right over the supplier's window, as
        ``supplier_stats(key, window).fields``, for these suppliers in one query; kept until a supplier outcome
        changes (D-162): read it, don't change it."""
        return self.summary_memo(f"supplier_fields/{window}",  # type: ignore[attr-defined]
                                 lambda: self._supplier_fields(keys, window), topic="", changes="suppliers",
                                 asked=tuple(keys))  # fmt: skip

    def _supplier_fields(self, keys: list[str], window: int) -> dict[str, dict[str, Any]]:
        from ...capture.supplier import FieldStat

        found: dict[str, dict[str, FieldStat]] = {key: {} for key in keys}
        with self._conn() as conn:  # type: ignore[attr-defined]
            for r in conn.execute(
                f"{_ranked(_in_list('supplier_key'))} SELECT o.supplier_key, o.field, COUNT(*) AS n, "
                "SUM(o.correct != 0) AS correct FROM ranked r JOIN supplier_outcomes o ON o.invoice_id = r.invoice_id "
                "AND +o.supplier_key = r.supplier_key WHERE r.n <= ? GROUP BY o.supplier_key, o.field",
                (json.dumps(list(keys)), window),
            ):  # fmt: skip
                found[r["supplier_key"]][r["field"]] = FieldStat(r["n"], r["correct"])
        return found

    def latest_corrections(self, keys: list[str], among: int = 500, most: int = 5) -> dict[str, list[dict[str, Any]]]:
        """{key: the latest ``most`` fields AP corrected among the supplier's latest ``among`` recorded} of these
        suppliers (``supplier_outcomes(key, among)``' rows not read right, newest first), in one query."""
        found: dict[str, list[dict[str, Any]]] = {key: [] for key in keys}
        with self._conn() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(  # each supplier's latest ``among`` read back along its index, then the corrected
                "SELECT o.* FROM json_each(?) AS k JOIN supplier_outcomes o ON o.id IN (SELECT id FROM "
                "supplier_outcomes WHERE supplier_key = k.value ORDER BY at DESC, id DESC LIMIT ?) WHERE NOT o.correct "
                "ORDER BY o.supplier_key, o.at DESC, o.id DESC", (json.dumps(list(keys)), among),
            ).fetchall()  # fmt: skip
        for r in rows:
            if len(found[r["supplier_key"]]) < most:
                row = dict(r)
                for column in ("ai_value", "final_value"):
                    row[column] = json.loads(row[column]) if row[column] else None
                found[r["supplier_key"]].append({**row, "correct": bool(row["correct"])})
        return found

    @staticmethod
    def _merge_supplier_profiles(conn: sqlite3.Connection, old: str, key: str) -> None:
        """The supplier profile under ``old`` folded into the one under ``key`` (the same supplier under today's
        rules), then removed: the stricter state wins (held, then suspended, supervised, touchless), with its dates
        and audit rate, so a held supplier never goes touchless by a key change; a field blank in one is filled from
        the other."""
        from ...capture.supplier import AUTONOMOUS, HELD, SUPERVISED, SUSPENDED

        strict = {HELD: 3, SUSPENDED: 2, SUPERVISED: 1, AUTONOMOUS: 0}
        kept = conn.execute("SELECT * FROM supplier_profiles WHERE key = ?", (key,)).fetchone()
        gone = conn.execute("SELECT * FROM supplier_profiles WHERE key = ?", (old,)).fetchone()
        if kept is None or gone is None:
            return
        state = gone if strict.get(gone["state"], 1) > strict.get(kept["state"], 1) else kept
        conn.execute(
            "UPDATE supplier_profiles SET state = ?, autonomous_since = ?, suspended_at = ?, audit_rate = ?, "
            "display_name = ?, vendor_id = ?, template_json = ?, updated_at = ? WHERE key = ?",
            (
                state["state"], state["autonomous_since"], state["suspended_at"], state["audit_rate"],
                kept["display_name"] or gone["display_name"], kept["vendor_id"] or gone["vendor_id"],
                kept["template_json"] or gone["template_json"], _now(), key,
            ),
        )  # fmt: skip
        conn.execute("DELETE FROM supplier_profiles WHERE key = ?", (old,))
