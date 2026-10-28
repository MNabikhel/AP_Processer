"""Purchase orders, ERP export batches, fixed coding rules."""

from __future__ import annotations

import json
from typing import Any

from ...coding.po import OPEN, po_key
from ...coding.rules import Rule
from ..memory import vendor_key
from ._base import (
    _ACTIVE_IN,
    ACTIVE_STATUSES,
    APPROVED,
    REVIEW,
    _clean_code,
    _in_list,
    _now,
)
from .summary import BIT, fetch_rows


class PurchasingMixin:
    # --- Purchase orders ---------------------------------------------------------------------------

    def import_purchase_orders(
        self, rows: list[dict[str, Any]], replace_all: bool = False, actor: str | None = None
    ) -> dict[str, int]:
        """Import PO lines (``po.rows_from_records`` output). A PO in the file replaces that PO's lines,
        so re-importing an ERP export updates quantities received; other POs are kept unless ``replace_all``."""
        orders: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = po_key(row["po_number"])
            if not key:
                continue
            order = orders.setdefault(key, {"row": row, "lines": []})
            order["lines"].append(row)
            if row.get("vendor_name") and not order["row"].get("vendor_name"):
                order["row"] = row
        now = _now()
        with self._conn() as conn:
            if replace_all:
                conn.execute("DELETE FROM po_lines")
                conn.execute("DELETE FROM purchase_orders")
            existing = {r["po_key"] for r in conn.execute("SELECT po_key FROM purchase_orders")}
            for key, order in orders.items():
                head = order["row"]
                status = "closed" if all(r.get("status") == "closed" for r in order["lines"]) else OPEN
                conn.execute(
                    """INSERT INTO purchase_orders (po_key, po_number, vendor_name, vendor_key, status, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(po_key) DO UPDATE SET po_number = excluded.po_number,
                       vendor_name = excluded.vendor_name, vendor_key = excluded.vendor_key,
                       status = excluded.status, updated_at = excluded.updated_at""",
                    (key, head["po_number"], head.get("vendor_name") or "", vendor_key(head.get("vendor_name") or ""),
                     status, now),
                )  # fmt: skip
                conn.execute("DELETE FROM po_lines WHERE po_key = ?", (key,))
                used: set[int] = set()
                for row in order["lines"]:
                    number = row.get("line_number")
                    if not number or number in used:
                        number = max(used, default=0) + 1
                    used.add(number)
                    conn.execute(
                        """INSERT INTO po_lines (po_key, line_number, description, quantity, unit_price, amount,
                           received_quantity, gl_code, cost_center, amount_only)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (key, number, row["description"], row["quantity"], row["unit_price"], row["amount"],
                         row.get("received_quantity"), _clean_code(row.get("gl_code")),
                         _clean_code(row.get("cost_center")), int(bool(row.get("amount_only")))),
                    )  # fmt: skip
            counts = {
                "orders": len(orders),
                "added": len(set(orders) - existing),
                "updated": len(set(orders) & existing),
                "lines": sum(len(o["lines"]) for o in orders.values()),
            }
            self._log(conn, "pos_imported", actor=actor, detail={**counts, "replace_all": replace_all})
        return counts

    def has_purchase_orders(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM purchase_orders LIMIT 1").fetchone() is not None

    def purchase_order(self, key: str) -> dict[str, Any] | None:
        """One PO with its lines and total (``key`` is ``po.po_key``)."""
        with self._conn() as conn:
            head = conn.execute("SELECT * FROM purchase_orders WHERE po_key = ?", (key,)).fetchone()
            if head is None:
                return None
            lines = [
                dict(r) for r in conn.execute("SELECT * FROM po_lines WHERE po_key = ? ORDER BY line_number", (key,))
            ]
        return {**dict(head), "lines": lines, "total": round(sum(li["amount"] for li in lines), 2)}

    def purchase_orders(self) -> list[dict[str, Any]]:
        """Every PO with its total and how much has been invoiced against it (invoices in review included)."""
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT p.*, COUNT(l.line_number) lines, COALESCE(SUM(l.amount), 0) total,
                       SUM(l.received_quantity IS NOT NULL) received_lines
                       FROM purchase_orders p LEFT JOIN po_lines l ON l.po_key = p.po_key
                       GROUP BY p.po_key ORDER BY p.po_number"""
                )
            ]
            billed: dict[str, dict[str, Any]] = {}
            self._summary_fresh(conn)  # each invoice's subtotal billed, from the invoices' summary (D-110)
            for r in conn.execute(
                "SELECT id, po_key, status, po_billed, broken FROM invoice_summary WHERE po_key != '' AND status IN "
                + _ACTIVE_IN
                + " AND po_key IN (SELECT po_key FROM purchase_orders) ORDER BY id",  # a PO we have
                ACTIVE_STATUSES,
            ):
                amount = r["po_billed"]
                if r["broken"] & BIT["po"]:  # what the summary could not read: read again, failing as it did before
                    (inv,) = conn.execute("SELECT ai_output, final_output FROM invoices WHERE id = ?", (r["id"],))
                    amount = float(json.loads(inv["final_output"] or inv["ai_output"] or "{}").get("subtotal") or 0)
                entry = billed.setdefault(r["po_key"], {"billed": 0.0, "invoices": 0, "in_review": 0})
                entry["billed"] += amount
                entry["invoices"] += 1
                entry["in_review"] += r["status"] == REVIEW
        for r in rows:
            r.update(billed.get(r["po_key"], {"billed": 0.0, "invoices": 0, "in_review": 0}))
            r["remaining"] = r["total"] - r["billed"]
        return rows

    def open_pos_for_vendor(self, key: str) -> list[str]:
        if not key:
            return []
        with self._conn() as conn:
            return [
                r["po_number"]
                for r in conn.execute(
                    "SELECT po_number FROM purchase_orders WHERE vendor_key = ? AND status = ? ORDER BY po_number",
                    (key, OPEN),
                )
            ]

    def po_invoices(self, key: str, exclude_id: int | None = None) -> list[dict[str, Any]]:
        """Invoices quoting this PO (in review or approved), with their current coding."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, status, invoice_number, invoice_date, ai_output, final_output FROM invoices "
                "WHERE po_key = ? AND status IN " + _ACTIVE_IN + " ORDER BY id",
                (key, *ACTIVE_STATUSES),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "status": r["status"],
                "invoice_number": r["invoice_number"],
                "invoice_date": r["invoice_date"],
                "coding": json.loads(r["final_output"] or r["ai_output"] or "{}"),
            }  # fmt: skip
            for r in rows
            if r["id"] != exclude_id
        ]

    def vendor_invoices_without_po(self, key: str) -> list[dict[str, Any]]:
        """This vendor's active invoices that quote no PO, with their coding (they may still bill a PO line)."""
        if not key:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, status, invoice_number, invoice_date, ai_output, final_output FROM invoices "
                "WHERE vendor_key = ? AND po_key = '' AND status IN " + _ACTIVE_IN + " ORDER BY id",
                (key, *ACTIVE_STATUSES),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "status": r["status"],
                "invoice_number": r["invoice_number"],
                "invoice_date": r["invoice_date"],
                "coding": json.loads(r["final_output"] or r["ai_output"] or "{}"),
            }  # fmt: skip
            for r in rows
        ]

    def set_po_status(self, key: str, status: str, actor: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE purchase_orders SET status = ?, updated_at = ? WHERE po_key = ?", (status, _now(), key)
            )
            number = conn.execute("SELECT po_number FROM purchase_orders WHERE po_key = ?", (key,)).fetchone()
            self._log(conn, "po_status", actor=actor, detail={"po": number[0] if number else key, "status": status})

    def delete_purchase_orders(self, keys: list[str], actor: str | None = None) -> int:
        with self._conn() as conn:
            listed = (json.dumps(list(keys)),)
            numbers = [
                r[0] for r in conn.execute(f"SELECT po_number FROM purchase_orders WHERE {_in_list('po_key')}", listed)
            ]
            conn.execute(f"DELETE FROM po_lines WHERE {_in_list('po_key')}", listed)
            conn.execute(f"DELETE FROM purchase_orders WHERE {_in_list('po_key')}", listed)
            if numbers:
                self._log(conn, "pos_deleted", actor=actor, detail={"pos": numbers})
        return len(numbers)

    # --- ERP export batches -------------------------------------------------------------------------

    def unexported_approved(self) -> list[dict[str, Any]]:
        """Approved invoices that have not been in an export batch yet (oldest approval first)."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            return fetch_rows(
                conn,
                """SELECT id, vendor_name, invoice_number, invoice_date, currency, grand_total, reviewer,
                          reviewed_at, second_reviewer FROM invoice_summary WHERE status = ? AND export_batch IS NULL
                   ORDER BY reviewed_at, id""",
                (APPROVED,),
            )

    def ready_to_export_count(self) -> int:
        """How many invoices ``unexported_approved`` lists (counted on the summary's index)."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            return conn.execute("SELECT COUNT(*) FROM invoice_summary WHERE status = ? AND export_batch IS NULL",
                                (APPROVED,)).fetchone()[0]  # fmt: skip

    def create_export_batch(self, invoice_ids: list[int], fmt: str, actor: str | None = None) -> int:
        """Mark approved, not yet exported invoices as one batch. Returns the batch number. Two people exporting
        at once never put the same invoice in two batches: the write lock is taken before the invoices are
        read, and each is taken only if it is still approved and not exported."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")  # the write lock from the read to the marks
            # The ids as one JSON list: one '?' however many are ticked (SQLite takes only so many bound variables).
            eligible = conn.execute(
                "SELECT id, grand_total, currency FROM invoices WHERE id IN (SELECT value FROM json_each(?)) "
                "AND status = ? AND export_batch IS NULL ORDER BY id",
                (json.dumps([int(i) for i in invoice_ids]), APPROVED),
            ).fetchall()
            if not eligible:
                raise ValueError("none of these invoices can be exported (not approved, or already exported)")
            cur = conn.execute(
                "INSERT INTO export_batches (created_at, actor, format, invoices, total) VALUES (?, ?, ?, ?, ?)",
                (_now(), actor, fmt, 0, 0.0),
            )
            batch = int(cur.lastrowid)
            taken = []
            for r in eligible:
                cur = conn.execute(
                    "UPDATE invoices SET export_batch = ? WHERE id = ? AND status = ? AND export_batch IS NULL",
                    (batch, r["id"], APPROVED),
                )
                if cur.rowcount:
                    taken.append(r)
            if not taken:
                raise ValueError("none of these invoices can be exported (not approved, or already exported)")
            currencies = {(r["currency"] or "").strip().upper() or "CAD" for r in taken}
            total = round(sum(r["grand_total"] or 0 for r in taken), 2) if len(currencies) == 1 else None
            conn.execute(  # no total across currencies: dollars and euros do not add up
                "UPDATE export_batches SET invoices = ?, total = ? WHERE id = ?", (len(taken), total, batch)
            )
            for r in taken:  # the amount and currency, so an undone batch still shows what went out
                self._log(conn, "exported", r["id"], actor, {
                    "batch": batch, "format": fmt, "total": r["grand_total"],
                    "currency": (r["currency"] or "").strip().upper() or "CAD",
                })  # fmt: skip
        return batch

    def export_batches(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM export_batches ORDER BY id DESC")]

    def export_batch(self, batch_id: int) -> dict[str, Any] | None:
        """Export batch ``batch_id`` as ``export_batches`` lists it, or None when there is none."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM export_batches WHERE id = ?", (batch_id,)).fetchone()
        return dict(row) if row else None

    # --- Fixed coding rules ---------------------------------------------------------------------------

    def coding_rules(self) -> list[Rule]:
        with self._conn() as conn:
            return [
                Rule(r["vendor"], r["contains"], r["gl_code"], r["cost_center"], r["id"])
                for r in conn.execute("SELECT * FROM coding_rules ORDER BY id")
            ]

    def save_coding_rules(self, rules: list[Rule], actor: str | None = None) -> int:
        """Replace the rules (rules without a GL account, or without a vendor and words, are left out).
        Unchanged rules keep their id, so their age (the tie-breaker) is kept."""
        keep = [r for r in rules if r.gl_code.strip() and (r.vendor.strip() or r.contains.strip())]
        with self._conn() as conn:
            old = {(r["vendor"], r["contains"], r["gl_code"], r["cost_center"]): r["id"]
                   for r in conn.execute("SELECT * FROM coding_rules")}  # fmt: skip
            conn.execute("DELETE FROM coding_rules")
            now = _now()
            seen = set()
            for r in keep:
                values = (r.vendor.strip(), r.contains.strip(), _clean_code(r.gl_code), _clean_code(r.cost_center))
                if values in seen:  # the same rule twice: kept once
                    continue
                seen.add(values)
                conn.execute(
                    "INSERT INTO coding_rules (id, vendor, contains, gl_code, cost_center, created_at, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (old.get(values), *values, now, actor),
                )
            self._log(conn, "rules_changed", actor=actor, detail={"rules": len(keep)})
        return len(keep)

    def add_rules_applied(self, invoice_id: int, changes: list[dict[str, Any]]) -> None:
        """Remember lines a fixed coding rule set on the review screen (as rules applied at processing are), so
        the AI's accuracy is measured on its own answer for them."""
        with self._conn() as conn:
            # The write lock from the read to the write: a background check's notes saved in between are kept (B-658).
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT meta FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
            if row is None:
                return
            meta = json.loads(row["meta"] or "{}") or {}
            known = {c["line_number"]: c for c in meta.get("rules_applied") or []}
            for c in changes:
                first = known.get(c["line_number"])
                # The AI's own answer stays the one recorded first.
                known[c["line_number"]] = (
                    {**c, "gl_from": first["gl_from"], "cc_from": first["cc_from"]} if first else c
                )
            meta["rules_applied"] = sorted(known.values(), key=lambda c: c["line_number"])
            conn.execute("UPDATE invoices SET meta = ? WHERE id = ?", (json.dumps(meta), invoice_id))

    def delete_empty_batches(self, batches: set[int]) -> int:
        """Delete these export batches if no invoice belongs to them any more (e.g. demo invoices removed)."""
        removed = 0
        with self._conn() as conn:
            for batch in batches:
                if not conn.execute("SELECT 1 FROM invoices WHERE export_batch = ? LIMIT 1", (batch,)).fetchone():
                    removed += conn.execute("DELETE FROM export_batches WHERE id = ?", (batch,)).rowcount
        return removed

    def batch_totals(self) -> dict[int, dict[str, float]]:
        """{batch: {currency: total}} of the invoices in each batch (an undone batch has none any more): read again once
        an invoice changed (``summary_memo``). Shared: read it, don't change it."""
        return self.summary_memo("batch_totals", self._batch_totals, "batches")

    def _batch_totals(self) -> dict[int, dict[str, float]]:
        with self._conn() as conn:
            self._summary_fresh(conn)
            rows = conn.execute(
                "SELECT export_batch, COALESCE(NULLIF(currency, ''), 'CAD') AS cur, SUM(grand_total) AS total "
                "FROM invoice_summary WHERE export_batch IS NOT NULL GROUP BY export_batch, cur"
            ).fetchall()
        out: dict[int, dict[str, float]] = {}
        for r in rows:
            out.setdefault(r["export_batch"], {})[r["cur"]] = round(r["total"] or 0, 2)
        return out

    def exported_totals(self) -> dict[int, dict[str, float]]:
        """{batch: {currency: total}} of what each batch held when it was exported, from the audit trail: an
        undone batch's invoices are back in the ready list, but what went out is still shown per currency. An
        export recorded by an older version (without the amount) counts the invoice's amount as it is now; a
        batch with such an invoice since deleted is left out (its total is not known). Read again once an invoice or
        the audit trail changed (``summary_memo``). Shared: read it, don't change it."""
        return self.summary_memo("exported_totals", self._exported_totals, "batches", "exported")

    def _exported_totals(self) -> dict[int, dict[str, float]]:
        with self._conn() as conn:
            self._summary_fresh(conn)
            rows = conn.execute(
                "SELECT e.detail, i.id, i.currency, i.grand_total FROM events e "
                "LEFT JOIN invoice_summary i ON i.id = e.invoice_id WHERE e.action = 'exported'"
            ).fetchall()
        out: dict[int, dict[str, float]] = {}
        unknown: set[int] = set()
        for r in rows:
            detail = json.loads(r["detail"] or "{}") or {}
            if detail.get("batch") is None:
                continue
            batch = int(detail["batch"])
            if "currency" in detail:
                currency, amount = detail["currency"], detail.get("total")
            elif r["id"] is not None:
                currency, amount = (r["currency"] or "").strip().upper() or "CAD", r["grand_total"]
            else:
                unknown.add(batch)
                continue
            totals = out.setdefault(batch, {})
            totals[currency] = round(totals.get(currency, 0.0) + float(amount or 0), 2)
        return {batch: totals for batch, totals in out.items() if batch not in unknown}

    def batch_invoice_ids(self, batch: int) -> list[int]:
        with self._conn() as conn:
            self._summary_fresh(conn)
            return [
                r["id"]
                for r in conn.execute("SELECT id FROM invoice_summary WHERE export_batch = ? ORDER BY id", (batch,))
            ]

    def undo_export_batch(self, batch: int, actor: str | None = None, guard: Any = None) -> int:
        """The ERP import failed: put the batch's invoices back in the ready-to-export list. ValueError when the
        batch was undone already (two clicks at once: the second must not log a second undo). ``guard(conn)`` runs
        first in the same write transaction (``BEGIN IMMEDIATE``) and may refuse with ValueError: the JD Edwards
        vouchers' check that none of the batch's is, or may be, in JDE (D-105)."""
        with self._conn() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            if guard is not None:
                guard(conn)
            claimed = conn.execute(
                "UPDATE export_batches SET undone_at = ?, undone_by = ? WHERE id = ? AND undone_at IS NULL",
                (_now(), actor, batch),
            )
            if claimed.rowcount == 0:
                raise ValueError(f"export batch {batch} is unknown or already undone")
            ids = [r["id"] for r in conn.execute("SELECT id FROM invoices WHERE export_batch = ?", (batch,))]
            conn.execute("UPDATE invoices SET export_batch = NULL WHERE export_batch = ?", (batch,))
            self._log(conn, "export_undone", actor=actor, detail={"batch": batch, "invoices": len(ids)})
        return len(ids)
