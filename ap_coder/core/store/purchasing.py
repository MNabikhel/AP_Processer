"""Purchase orders, ERP export batches, the lines a fixed coding rule set on the review screen (the rules
themselves: ``rules.py``)."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from ...coding.po import CLOSED, OPEN, pick_order, positive_lines
from ..keys import company_code, order_key_of, po_key
from ..memory import vendor_key
from . import dialect
from ._base import (
    _ACTIVE_IN,
    ACTIVE_STATUSES,
    APPROVED,
    REVIEW,
    _clean_code,
    _in_list,
    _now,
)
from .dialect import begin_write
from .learning import one_snapshot
from .reports import _version
from .summary import BIT, fetch_rows

BILLING_BATCH = 500  # the codings ``billing_codings`` reads at a time
# What ``po_invoices`` reads of each invoice's coding (the approved one, else AP Coder's): SQLite writing the coding's
# own text of each value; the coding itself when it is not an object of valid JSON, for Python to read as before.
PO_CODING = ("subtotal", "invoice_date", "line_items")
_PO_INVOICES = f"""
WITH coded AS (SELECT id, status, invoice_number, invoice_date, export_batch,
                      COALESCE(NULLIF(final_output, ''), NULLIF(ai_output, ''), '{{}}') AS doc FROM invoices
               WHERE po_key = ? AND status IN {_ACTIVE_IN}),
read AS (SELECT *, CASE WHEN NOT {dialect.json_valid("doc")} THEN 0 ELSE json_type(doc) = 'object' END AS ok FROM coded)
SELECT id, status, invoice_number, invoice_date, export_batch,
       CASE WHEN ok THEN {dialect.json_values("doc", [f"$.{k}" for k in PO_CODING])} END,
       CASE WHEN NOT ok THEN doc END FROM read ORDER BY id
"""
# Of each of these invoices (a JSON list of ids), what ``po_invoices_since`` reads: its vendor, the columns
# ``po_invoices`` gives, and of one in review or approved its coding as ``po_invoices`` reads it (``active``).
_PO_INVOICES_OF = f"""
WITH coded AS (SELECT id, vendor_key, status, invoice_number, invoice_date, export_batch,
                      status IN {_ACTIVE_IN} AS active,
                      COALESCE(NULLIF(final_output, ''), NULLIF(ai_output, ''), '{{}}') AS doc FROM invoices
               WHERE {_in_list("id")}),
read AS (SELECT *, CASE WHEN NOT active THEN NULL WHEN NOT {dialect.json_valid("doc")} THEN 0
                        ELSE json_type(doc) = 'object' END AS ok FROM coded)
SELECT id, vendor_key, status, invoice_number, invoice_date, export_batch,
       CASE WHEN ok THEN {dialect.json_values("doc", [f"$.{k}" for k in PO_CODING])} END,
       CASE WHEN NOT ok THEN doc END, active FROM read ORDER BY id
"""
# The moment a PO's invoices are brought up to date in: the database's ``summary_id``, the vendors' change log's first
# and last change, and the invoices quoting the PO (any status: a JSON list of ids, by the PO's index).
_PO_MOMENT = (
    "SELECT (SELECT value FROM settings WHERE key = 'summary_id'), (SELECT MIN(seq) FROM vendor_changes), "
    "(SELECT MAX(seq) FROM vendor_changes), (SELECT json_group_array(id) FROM invoices WHERE po_key = ?)"
)
_VENDOR_COUNTS = f"SELECT key, COALESCE(CAST(value AS INTEGER), 0) FROM settings WHERE {_in_list('key')}"
# Of each of these invoices (a JSON list of ids), what billing a PO reads of its coding (the approved one, else AP
# Coder's: ``po_invoices``): [id, [invoice date, lines], null], SQLite writing the coding's own text of each value; [id,
# null, the coding] when it is not an object of valid JSON, for Python to read as before. All in one JSON text.
_BILLING_FACTS = f"""
WITH coded AS (SELECT id, COALESCE(NULLIF(final_output, ''), NULLIF(ai_output, ''), '{{}}') AS doc FROM invoices
               WHERE {_in_list("id")}),
read AS (SELECT id, doc, CASE WHEN NOT json_valid(doc) THEN 0 ELSE json_type(doc) = 'object' END AS ok FROM coded)
SELECT json_group_array(CASE WHEN ok THEN json_array(id, json_extract(doc, '$.invoice_date', '$.line_items'), NULL)
                             ELSE json_array(id, NULL, doc) END) FROM read
"""
# What month-end reads of each invoice billing a received PO (``billing_lines``), kept for every process in the
# database by invoice (``billing_facts``: [date, lines], about 600 bytes, by its vendor) under its vendor's stamp
# (``billing_vendors``: the database's ``summary_id`` (another after a restore), this AP Coder's version and
# BILLING_FORMAT as ``kept``; the last change of the vendors' change log its rows are as of, ``logged``; the vendor's
# count of changes then, ``counted``, B-1811) and brought up to date from the log (``vendor_changes``, D-174): after a
# change only the invoices changed since are decoded again, not every invoice of the vendor (23,000 at 300,000
# invoices, D-177), and only their rows are written, with the vendor's stamp, not a row of every invoice of the vendor
# (7 MB, D-194).
BILLING_FORMAT = "billing-2"
# The rows of each of these vendors (a JSON list), each vendor's together: (vendor, id, [date, lines] as JSON, all
# decoded at once).
_KEPT_FACTS = f"SELECT vendor_key, id, facts FROM billing_facts WHERE {dialect.in_list('vendor_key')}"
_STAMP = "SELECT kept, logged, counted FROM billing_vendors WHERE vendor_key = ?"


# What ``po.pick_order`` reads of each order of a number (D-267).
_ORDERS = (
    "SELECT po_key, number_key, po_number, vendor_name, vendor_key, supplier_number, order_type, "
    "order_company, status, currency FROM purchase_orders"
)
# The company each of these invoices (a JSON list of ids) is coded to (its coding's, the approved one else AP Coder's).
_COMPANIES = f"""
SELECT id, CASE WHEN {dialect.json_valid("doc")} THEN json_extract(doc, '$.company') END FROM
(SELECT id, COALESCE(NULLIF(final_output, ''), NULLIF(ai_output, ''), '{{}}') AS doc FROM invoices
 WHERE {_in_list("id")})
"""


@dataclass
class BillingMoment:
    """What ``billing_moment`` yields: whether its reads are inside a write under way (which may yet be undone:
    nothing read in it is kept), and the invoices' billing lines to keep once the read is over (``billing_lines``)."""

    writing: bool
    # (vendor, its stamp as read, its stamp now, its invoices decoded: [(id, what was read)], the ids whose rows go:
    # None, all)
    keep: list[tuple[str, Any, tuple[str, int, int], list[tuple[int, Any]], Any]] = field(default_factory=list)


@dataclass
class PoChanges:
    """What ``po_invoices_since`` read of a PO's invoices, in one moment: its ``stamp`` (the database's ``summary_id``,
    the last change of the vendors' change log, and each of the invoices' vendors' count of changes then, B-1811),
    the invoices quoting the PO (``quoting``: any status, in order), and those of them read (``read``: {id: (its
    vendor, its row as ``po_invoices`` gives it; None when it is not in review or approved)})."""

    stamp: tuple[Any, int, dict[str, int]]
    quoting: list[int]
    read: dict[int, tuple[Any, dict[str, Any] | None]]


def _vendor_counts(conn: Any, vendors: Any) -> dict[str, int]:
    """Each of ``vendors``' count of changes on ``conn`` (``changes/vendor/<key>``, which every version's triggers
    keep, D-159; 0 before its first)."""
    keys = {vendor: f"changes/vendor/{vendor}" for vendor in vendors}
    found = dict(conn.execute(_VENDOR_COUNTS, (json.dumps(sorted(keys.values())),)).fetchall())
    return {vendor: found.get(key, 0) for vendor, key in keys.items()}


def _po_row(n: int, status: str, number: Any, date: Any, batch: Any, facts: Any, doc: Any) -> dict[str, Any]:
    """An invoice as ``po_invoices`` gives it: its coding read out of SQLite's values of PO_CODING (``facts``,
    decoded), else (not an object of valid JSON) read as it was, failing as it did."""
    coding = dict(zip(PO_CODING, facts, strict=True)) if doc is None else json.loads(doc)
    return {"id": n, "status": status, "invoice_number": number, "invoice_date": date, "coding": coding,
            "export_batch": batch}  # fmt: skip


def _billing(coding: Any) -> Any:
    """What month-end reads of an invoice's coding (``billing_codings``): (its invoice date, its lines billing a
    positive quantity: ``positive_lines``); the coding itself when it can't be read so (not an object, its lines not
    lines), for the caller to read as before at each use, failing where it did (an invoice dated after the period end
    is not read further)."""
    if not isinstance(coding, dict):
        return coding
    try:
        lines = positive_lines([{"coding": coding}])
    except (AttributeError, TypeError, ValueError, OverflowError):
        return coding
    return coding.get("invoice_date"), lines


def _plain(value: Any) -> bool:
    return value is None or isinstance(value, (str, int)) or (isinstance(value, float) and math.isfinite(value))


def _keepable(facts: Any) -> bool:
    """Whether what ``_billing`` read of an invoice is kept for every process: read so, and each value plain JSON (a
    coding Python reads and SQLite doesn't may carry a NaN), so that its row is always written, and read back as it
    was."""
    return isinstance(facts, tuple) and _plain(facts[0]) and all(
        _plain(description) and math.isfinite(price) and math.isfinite(quantity)
        for description, price, quantity in facts[1])  # fmt: skip


class PurchasingMixin:
    # --- Purchase orders ---------------------------------------------------------------------------

    def import_purchase_orders(
        self, rows: list[dict[str, Any]], replace_all: bool = False, actor: str | None = None, log: bool = True
    ) -> dict[str, int]:
        """Import PO lines (``po.rows_from_records`` output). A PO in the file replaces that PO's lines,
        so re-importing an ERP export updates quantities received; other POs are kept unless ``replace_all``.
        ``log``: the import's event (the JD Edwards sync logs one for all its pages, D-181)."""
        orders: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = order_key_of(row)  # (the same number in two companies: two orders, D-267)
            if not key:
                continue
            order = orders.setdefault(key, {"row": row, "lines": []})
            order["lines"].append(row)
            if row.get("vendor_name") and not order["row"].get("vendor_name"):
                order["row"] = row
        # A PO file that names the supplier by its number alone (JD Edwards' address number): its name in the vendor
        # list, so the PO is the supplier's (D-264).
        numbers = {str(r.get("supplier_number")) for r in rows if r.get("supplier_number") and not r.get("vendor_name")}
        named = {v["erp_id"]: v["display_name"] for v in self.master_vendors_of(set(), numbers).values()} if numbers \
            else {}  # fmt: skip
        now = _now()
        with self._conn() as conn:
            if replace_all:
                conn.execute("DELETE FROM po_lines")
                conn.execute("DELETE FROM purchase_orders")
            existing = {r["po_key"] for r in conn.execute("SELECT po_key FROM purchase_orders")}
            replaced: set[str] = set()
            for key, order in orders.items():
                head = order["row"]
                status = "closed" if all(r.get("status") == "closed" for r in order["lines"]) else OPEN
                # The PO's currency: its lines' first ('' when the file gives none: taken as CAD, B-2612); so its
                # JD Edwards order type, company and supplier number (D-264).
                currency, order_type, company, supplier = (
                    next((r[f] for r in order["lines"] if r.get(f)), "")
                    for f in ("currency", "order_type", "order_company", "supplier_number"))  # fmt: skip
                name = head.get("vendor_name") or named.get(supplier) or ""
                numbered = po_key(head["po_number"])
                if key != numbered and self._replaces_numbered(conn, numbered, vendor_key(name), supplier):
                    replaced.add(key)  # (the order kept by its number alone, now with its company and type: B-2665)
                conn.execute(
                    """INSERT INTO purchase_orders (po_key, po_number, vendor_name, vendor_key, status, updated_at,
                       currency, order_type, order_company, supplier_number, number_key)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(po_key) DO UPDATE SET
                       po_number = excluded.po_number, vendor_name = excluded.vendor_name,
                       vendor_key = excluded.vendor_key, status = excluded.status, updated_at = excluded.updated_at,
                       currency = excluded.currency, order_type = excluded.order_type,
                       order_company = excluded.order_company, supplier_number = excluded.supplier_number,
                       number_key = excluded.number_key""",
                    (key, head["po_number"], name, vendor_key(name), status, now, currency, order_type,
                     company_code(company), supplier, po_key(head["po_number"])),
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
                           received_quantity, gl_code, cost_center, amount_only, open_amount, erp_line, uom,
                           open_quantity, tax_flag, tax_code, tax_area, subledger, subledger_type, retainage)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (key, number, row["description"], row["quantity"], row["unit_price"], row["amount"],
                         row.get("received_quantity"), _clean_code(row.get("gl_code")),
                         _clean_code(row.get("cost_center")), int(bool(row.get("amount_only"))),
                         row.get("open_amount"), row.get("erp_line") or "", row.get("uom") or "",
                         row.get("open_quantity"), row.get("tax_flag") or "", row.get("tax_code") or "",
                         row.get("tax_area") or "", row.get("subledger") or "", row.get("subledger_type") or "",
                         row.get("retainage")),
                    )  # fmt: skip
            counts = {
                "orders": len(orders),
                "added": len(set(orders) - existing - replaced),
                "updated": len((set(orders) & existing) | replaced),
                "lines": sum(len(o["lines"]) for o in orders.values()),
            }
            if log:
                self._log(conn, "pos_imported", actor=actor, detail={**counts, "replace_all": replace_all})
        return counts

    @staticmethod
    def _replaces_numbered(conn: Any, number: str, vendor: str, supplier: Any) -> bool:
        """Whether an order of a file naming its company or type (``keys.order_key``; ``vendor``: its vendor key,
        ``supplier``: its supplier number) is the one kept by its number alone (loaded from a file that named neither,
        D-267), of its supplier or of none: then that one is deleted, its lines too, and this one takes its place
        (B-2665), not a second order of the number beside it."""
        kept = conn.execute("SELECT vendor_key, supplier_number FROM purchase_orders WHERE po_key = ? AND "
                            "order_type = '' AND order_company = ''", (number,)).fetchone()  # fmt: skip
        if kept is None:
            return False
        theirs, number_of = kept["vendor_key"] or "", str(kept["supplier_number"] or "").lstrip("0")
        supplier = str(supplier or "").lstrip("0")
        if (theirs or number_of) and not ((vendor and theirs == vendor) or (supplier and number_of == supplier)):
            return False  # (another supplier's: another order of that number)
        conn.execute("DELETE FROM po_lines WHERE po_key = ?", (number,))
        conn.execute("DELETE FROM purchase_orders WHERE po_key = ?", (number,))
        return True

    def has_purchase_orders(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM purchase_orders LIMIT 1").fetchone() is not None

    def open_po_count(self) -> int:
        """How many purchase orders are open (JD Edwards files' card 6, D-266)."""
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE status = 'open'").fetchone()[0])

    def purchase_order(self, key: str) -> dict[str, Any] | None:
        """One PO with its lines and total (``key``: its ``keys.order_key``, or its number as people mean it when that
        is one order's alone, D-267)."""
        with self._conn() as conn:
            head = conn.execute("SELECT * FROM purchase_orders WHERE po_key = ?", (key,)).fetchone()
            if head is None:
                found = conn.execute("SELECT * FROM purchase_orders WHERE number_key = ? LIMIT 2", (key,)).fetchall()
                if len(found) != 1:
                    return None
                head, key = found[0], found[0]["po_key"]
            lines = [
                dict(r) for r in conn.execute("SELECT * FROM po_lines WHERE po_key = ? ORDER BY line_number", (key,))
            ]
        return {**dict(head), "lines": lines, "total": round(sum(li["amount"] for li in lines), 2)}

    def purchase_orders_by_key(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        """``purchase_order`` of each of ``keys`` that exists, by key: in two queries, not two a PO."""
        listed = (json.dumps(list(keys)),)
        with self._conn() as conn:
            heads = {r["po_key"]: dict(r) for r in conn.execute(
                f"SELECT * FROM purchase_orders WHERE {_in_list('po_key')}", listed)}  # fmt: skip
            lines: dict[str, list[dict[str, Any]]] = {}
            for r in conn.execute(f"SELECT * FROM po_lines WHERE {_in_list('po_key')} ORDER BY po_key, line_number",
                                  listed):  # fmt: skip
                lines.setdefault(r["po_key"], []).append(dict(r))
        found = {}
        for key in keys:
            if key in heads:
                rows = lines.get(key, [])
                found[key] = {**heads[key], "lines": rows, "total": round(sum(li["amount"] for li in rows), 2)}
        return found

    def orders_numbered(self, number: str) -> list[dict[str, Any]]:
        """The purchase orders (their heads) with this number as people mean it (``keys.po_key``), by key: one, or
        several when two companies or order types share it (D-267)."""
        with self._conn() as conn:
            return fetch_rows(conn, f"{_ORDERS} WHERE number_key = ? ORDER BY po_key", (number,))

    def _order_owners(self, conn: Any, rows: list[tuple[int, str, str]]) -> dict[int, str | None]:
        """{invoice id: the order it bills (its key), None when its number is several orders' and none is the one
        (``po.pick_order``: by its supplier, then its company)} of ``rows`` ((id, the number it quotes, its vendor
        key)), those whose number is an order's; on ``conn``. One query for all the orders, and one for the companies
        of the invoices whose number is shared."""
        numbers = sorted({number for _, number, _ in rows if number})
        if not numbers:
            return {}
        orders: dict[str, list[dict[str, Any]]] = {}
        for order in fetch_rows(conn, f"{_ORDERS} WHERE {_in_list('number_key')} ORDER BY po_key",
                                (json.dumps(numbers),)):  # fmt: skip
            orders.setdefault(order["number_key"], []).append(order)
        shared = [n for n, number, _ in rows if len(orders.get(number, ())) > 1]
        companies = dict(conn.execute(_COMPANIES, (json.dumps(shared),)).fetchall()) if shared else {}
        ids = self.vendor_ids() if shared else {}
        owners: dict[int, str | None] = {}
        for n, number, vendor in rows:
            found = orders.get(number)
            if found:
                picked = pick_order(found, vendor, ids.get(vendor, ""), companies.get(n) or "")
                owners[n] = picked["po_key"] if picked else None
        return owners

    def _billing_number(self, conn: Any, key: str) -> tuple[str, set[int] | None]:
        """(the number invoices quote the order ``key`` by, the ids of those of them that bill it: None for all, its
        number no other order's), on ``conn``."""
        head = conn.execute("SELECT number_key FROM purchase_orders WHERE po_key = ?", (key,)).fetchone()
        number = (head[0] if head else "") or key
        if not head or conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE number_key = ?",
                                    (number,)).fetchone()[0] < 2:  # fmt: skip
            return number, None
        quoting = [tuple(r) for r in conn.execute("SELECT id, po_key, vendor_key FROM invoices WHERE po_key = ?",
                                                  (number,))]  # fmt: skip
        return number, {n for n, owner in self._order_owners(conn, quoting).items() if owner == key}

    def received_po_keys(self) -> list[str]:
        """The POs month-end accrues lines of (not closed, a line received), in ``purchase_orders``' order."""
        with self._conn() as conn:
            return [r[0] for r in conn.execute(
                "SELECT po_key FROM purchase_orders p WHERE status != ? AND EXISTS (SELECT 1 FROM po_lines l WHERE "
                "l.po_key = p.po_key AND l.received_quantity IS NOT NULL) ORDER BY po_number", (CLOSED,),
            )]  # fmt: skip

    @contextmanager
    def billing_moment(self) -> Iterator[BillingMoment]:
        """Month-end's reads of the received POs and the invoices billing them inside (``billing_rows``,
        ``billing_lines``), in this thread, all of one moment of the database (one read: the invoices billing each PO
        and their codings agree, B-1818, and the vendors' change log and the invoices it names, B-1812), the invoices'
        summary brought up to date first. What ``billing_lines`` read to keep for every process is written once the
        read is over; never in a write under way, which may yet be undone (``BillingMoment.writing``: not this
        bringing up to date)."""
        with self.session():
            with self._conn() as conn:
                moment = BillingMoment(conn.in_transaction)
                self._summary_fresh(conn)
                if not moment.writing:
                    conn.commit()  # (the summary's own update: done before the read, not held through it)
                with one_snapshot(conn):
                    yield moment
            for keep in moment.keep:  # (the database too busy just now: kept at a later read)
                self._briefly_or(lambda keep=keep: self._keep_billing(*keep), None)  # type: ignore[attr-defined]

    def _keep_billing(self, vendor: str, was: Any, now: tuple[str, int, int], rows: list[tuple[int, Any]],
                      gone: Any) -> None:  # fmt: skip
        """A vendor's billing lines as ``billing_lines`` read them under its stamp ``was`` (as it was read, None: none)
        kept with its stamp ``now``: the rows of ``gone`` removed (the invoices changed since: None, all of its rows),
        ``rows`` written (an invoice's older row replaced; made JSON here, a vendor at a time). Only while its stamp is
        still the one read (in one write): once another process kept its rows since, theirs are as new, and these are
        not written."""
        with self._conn() as conn:
            begin_write(conn)
            found = conn.execute(_STAMP, (vendor,)).fetchone()
            if (tuple(found) if found else None) != was:
                return
            if gone is None:
                conn.execute("DELETE FROM billing_facts WHERE vendor_key = ?", (vendor,))
            elif gone:
                conn.execute(f"DELETE FROM billing_facts WHERE vendor_key = ? AND {_in_list('id')}",
                             (vendor, json.dumps(sorted(gone))))  # fmt: skip
            conn.executemany(dialect.upsert("billing_facts", ("vendor_key", "id", "facts"), ("vendor_key", "id")),
                             [(vendor, n, json.dumps(facts, separators=(",", ":"), allow_nan=False))
                              for n, facts in rows])  # fmt: skip
            conn.execute(dialect.upsert("billing_vendors", ("vendor_key", "kept", "logged", "counted"),
                                        ("vendor_key",)), (vendor, *now))  # fmt: skip

    def billing_rows(self, po_keys: list[str], vendor_keys: list[str]
                     ) -> tuple[list[tuple[Any, ...]], dict[str, Any], Any]:  # fmt: skip
        """(the invoices ``po_invoices`` gives for each of ``po_keys`` and ``vendor_invoices_without_po`` for each of
        ``vendor_keys``, by id, each once: (id, invoice date, ``po_key``, ``vendor_key``); the count of changes of each
        of their vendors, an invoice of its added, deleted or changed: ``changes/vendor/<key>``, D-159; the database's
        ``summary_id``), from the invoices' summary in one read: most invoices, read in order, not by an index. Inside
        ``billing_moment``, which brings the summary up to date. What month-end reads of each: ``billing_lines``."""
        with self._conn() as conn:
            numbers = [r[0] for r in conn.execute(f"SELECT DISTINCT number_key FROM purchase_orders WHERE "
                                                  f"{_in_list('po_key')}", (json.dumps(po_keys),))]  # fmt: skip
            rows = [tuple(r) for r in conn.execute(
                f"SELECT id, invoice_date, po_key, vendor_key FROM invoice_summary WHERE +status IN {_ACTIVE_IN} AND "
                "(+po_key IN (SELECT value FROM json_each(?)) OR (+po_key = '' AND +vendor_key IN (SELECT value FROM "
                "json_each(?)))) ORDER BY id",
                (*ACTIVE_STATUSES, json.dumps(numbers), json.dumps([k for k in vendor_keys if k])),
            )]  # fmt: skip
            # each by the order it bills (``po_key``: its key; None: its number several orders' and none the one)
            owners = self._order_owners(conn, [(r[0], r[2], r[3]) for r in rows if r[2]])
            rows = [(*r[:2], owners.get(r[0]), r[3]) if r[2] else r for r in rows]  # fmt: skip
            vendors = sorted({r[3] for r in rows if r[3] is not None})
            counts = dict(conn.execute(
                "SELECT substr(key, 16), value FROM settings WHERE key IN (SELECT 'changes/vendor/' || value FROM "
                "json_each(?))", (json.dumps(vendors),),
            ).fetchall())  # fmt: skip
            found = conn.execute("SELECT value FROM settings WHERE key = 'summary_id'").fetchone()
        return rows, counts, found[0] if found else None

    def billing_lines(self, rows: list[tuple[Any, ...]], moment: BillingMoment) -> dict[int, Any]:
        """{id: what month-end reads of its coding (``_billing``: its invoice date and its lines billing a positive
        quantity)} of each of ``rows`` (``billing_rows``') still there, inside ``moment`` (``billing_moment``). Kept
        for every process by invoice (``billing_facts``, D-194) and brought up to date from the vendors' change log,
        read in the same moment: only the invoices changed since are decoded again (``billing_codings``, D-177), and
        kept with their vendor's stamp; all of a vendor's once its stamp no longer holds (``_kept_billing``). An
        invoice with no vendor, whose changes no log follows, is decoded each time. In a write under way each is
        decoded, and nothing kept is read or kept."""
        by_vendor: dict[str, list[int]] = {}
        loose: list[int] = []
        for r in rows:
            if r[3] is None:
                loose.append(r[0])
            else:
                by_vendor.setdefault(r[3], []).append(r[0])
        read: dict[int, Any] = {}
        held: dict[str, tuple[Any, Any]] = {}
        with self._conn() as conn:
            summary_id, first, last = conn.execute(
                "SELECT (SELECT value FROM settings WHERE key = 'summary_id'), (SELECT MIN(seq) FROM vendor_changes), "
                "(SELECT MAX(seq) FROM vendor_changes)").fetchone()  # fmt: skip
            counts = dict(conn.execute(
                "SELECT substr(key, 16), COALESCE(CAST(value AS INTEGER), 0) FROM settings WHERE key IN (SELECT "
                "'changes/vendor/' || value FROM json_each(?))", (json.dumps(sorted(by_vendor)),),
            ).fetchall())  # fmt: skip
            kept = f"{summary_id}/{_version(BILLING_FORMAT)}"
            if by_vendor and not moment.writing:
                held = self._kept_billing(conn, by_vendor, counts, first, kept, read)
        todo = loose + [n for ids in by_vendor.values() for n in ids if n not in read]
        vendors, decoded = {r[0]: r[3] for r in rows}, {}
        for n, coding in self.billing_codings(todo):
            read[n] = _billing(coding)
            if vendors[n] is not None and _keepable(read[n]):
                decoded.setdefault(vendors[n], []).append((n, read[n]))
        for vendor in by_vendor if not moment.writing else ():
            was, gone = held.get(vendor, (None, None))
            if vendor in decoded or gone != set():  # (anything to write: its stamp held, nothing changed since)
                moment.keep.append((vendor, was, (kept, last or 0, counts.get(vendor, 0)), decoded.get(vendor, []),
                                    gone))  # fmt: skip
        return read

    @staticmethod
    def _kept_billing(conn: Any, by_vendor: dict[str, list[int]], counts: dict[str, int], first: int | None,
                      kept: str, read: dict[int, Any]) -> dict[str, tuple[Any, Any]]:  # fmt: skip
        """{vendor: (its stamp as read, the invoices changed since it, whose rows are not taken: None, every one)} of
        each of ``by_vendor`` ({vendor: the ids of its invoices}), and ``read`` filled with the rows taken: those of
        a vendor whose stamp holds, under it still and not changed since. It holds when kept by this database and
        version (``kept``) and its vendor's count of changes (``counts``) is the one it was kept with (nothing changed
        since), or else when the vendors' change log, read in the same moment, still holds every change since
        (``first``: its first) and the count moved by as many as the log says (an older AP Coder's triggers count,
        writing no log: B-1811); the invoices it names are the ones changed."""
        stamps = {r[0]: tuple(r[1:]) for r in conn.execute(
            f"SELECT vendor_key, kept, logged, counted FROM billing_vendors WHERE {_in_list('vendor_key')}",
            (json.dumps(sorted(by_vendor)),))}  # fmt: skip
        oldest = (first or 1) - 1  # (a stamp older: a change since may be gone from the log)
        moved = {v: s[1] for v, s in stamps.items() if s[0] == kept and counts.get(v, 0) != s[2] and s[1] >= oldest}
        changed: dict[str, set[int]] = {v: set() for v in moved}
        logged = dict.fromkeys(moved, 0)
        if moved:
            for seq, vendor, invoice_id in conn.execute(
                "SELECT seq, vendor_key, invoice_id FROM vendor_changes WHERE seq > ? AND vendor_key IN (SELECT value "
                "FROM json_each(?))", (min(moved.values()), json.dumps(sorted(moved))),
            ):  # fmt: skip
                if seq > moved[vendor]:
                    changed[vendor].add(invoice_id)
                    logged[vendor] += 1
        held: dict[str, tuple[Any, Any]] = {}
        for vendor in by_vendor:
            stamp = stamps.get(vendor)
            if stamp is not None and stamp[0] == kept and counts.get(vendor, 0) == stamp[2]:
                held[vendor] = (stamp, set())
            elif vendor in moved and counts.get(vendor, 0) - stamp[2] == logged[vendor]:  # type: ignore[index]
                held[vendor] = (stamp, changed[vendor])
            else:
                held[vendor] = (stamp, None)
        holding = sorted(v for v, (_, gone) in held.items() if gone is not None)
        wanted = {n: v for v in holding for n in by_vendor[v]}  # (a row of an invoice under another vendor since: not)
        found = conn.execute(_KEPT_FACTS, (json.dumps(holding),)).fetchall() if holding else []
        found = [r for r in found if wanted.get(r[1]) == r[0] and r[1] not in held[r[0]][1]]
        read.update(zip([r[1] for r in found], map(tuple, json.loads(f"[{','.join(r[2] for r in found)}]")),
                        strict=True))  # fmt: skip
        return held

    def billing_codings(self, ids: list[int]) -> Iterator[tuple[int, dict[str, Any]]]:
        """(id, what billing a PO reads of its coding: its invoice date and lines, read out by SQLite, the rest of it
        not decoded) of each of ``ids`` still there, in their order, a batch of codings at a time (the lines of every
        invoice of a vendor are never held at once)."""
        for start in range(0, len(ids), BILLING_BATCH):
            batch = ids[start : start + BILLING_BATCH]
            with self._conn() as conn:
                found = conn.execute(_BILLING_FACTS, (json.dumps(batch),)).fetchone()[0]
            read = {n: (facts, doc) for n, facts, doc in json.loads(found)}
            for n in batch:
                if n not in read:  # deleted since
                    continue
                facts, doc = read[n]
                # Not an object of valid JSON: the coding read as it was, failing as it did.
                yield n, json.loads(doc) if facts is None else {"invoice_date": facts[0], "line_items": facts[1]}

    def purchase_orders(self) -> list[dict[str, Any]]:
        """Every PO with its total and how much has been invoiced against it (invoices in review included), in its
        currency: an invoice in another (a PO with none taken as CAD) is counted in ``invoices`` and
        ``other_currency``, never added to ``billed`` (B-2613). A PO billed by amount whose lines all have what the ERP
        has open on them (``by_open``: JD Edwards' order details grid as it comes, D-264) totals that, and only the
        invoices not exported yet are added to ``billed``: the ERP's open amounts have counted the others (D-280)."""
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT p.*, COUNT(l.line_number) lines, COALESCE(SUM(l.amount), 0) total,
                       SUM(l.received_quantity IS NOT NULL) received_lines, MIN(l.amount_only) by_amount,
                       SUM(CASE WHEN l.open_amount IS NULL THEN 1 ELSE 0 END) no_open, SUM(l.open_amount) open_total
                       FROM purchase_orders p LEFT JOIN po_lines l ON l.po_key = p.po_key
                       GROUP BY p.po_key ORDER BY p.po_number"""
                )
            ]
            for r in rows:
                by_amount, no_open, open_total = r.pop("by_amount"), r.pop("no_open"), r.pop("open_total")
                r["by_open"] = bool(r["lines"] and by_amount and not no_open)
                if r["by_open"]:
                    r["total"] = round(open_total, 2)
            by_open = {r["po_key"] for r in rows if r["by_open"]}
            billed: dict[str, dict[str, Any]] = {}
            ordered = {r["po_key"]: r["currency"] or "CAD" for r in rows}  # each PO's currency (none: CAD)
            self._summary_fresh(conn)  # each invoice's subtotal billed, from the invoices' summary (D-110)
            quoting = fetch_rows(
                conn,
                "SELECT id, po_key, vendor_key, status, po_billed, broken, currency, export_batch FROM invoice_summary "
                "WHERE po_key != '' AND status IN "
                + _ACTIVE_IN
                + " AND po_key IN (SELECT number_key FROM purchase_orders) ORDER BY id",  # a PO we have
                ACTIVE_STATUSES,
            )
            # The order each bills: its number's, or of a number several orders share, the one of its supplier and
            # company (none: not counted on any, D-267).
            owners = self._order_owners(conn, [(r["id"], r["po_key"], r["vendor_key"]) for r in quoting])
            for r in quoting:
                owner = owners.get(r["id"])
                if owner not in ordered:
                    continue
                entry = billed.setdefault(owner, {"billed": 0.0, "invoices": 0, "in_review": 0,
                                                  "other_currency": 0})  # fmt: skip
                entry["invoices"] += 1
                entry["in_review"] += r["status"] == REVIEW
                if ((r["currency"] or "").strip().upper() or "CAD") != ordered[owner]:
                    entry["other_currency"] += 1  # in another currency: never added to this one (B-2613)
                    continue
                if owner in by_open and r["export_batch"] is not None:  # in the ERP's open amounts already (D-280)
                    continue
                amount = r["po_billed"]
                if r["broken"] & BIT["po"]:  # what the summary could not read: read again, failing as it did before
                    (inv,) = conn.execute("SELECT ai_output, final_output FROM invoices WHERE id = ?", (r["id"],))
                    amount = float(json.loads(inv["final_output"] or inv["ai_output"] or "{}").get("subtotal") or 0)
                entry["billed"] += amount
        for r in rows:
            r.update(billed.get(r["po_key"], {"billed": 0.0, "invoices": 0, "in_review": 0, "other_currency": 0}))
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
        """Invoices quoting this PO (in review or approved), with what a PO's match and page read of their current
        coding (``coding``: its subtotal, invoice date and lines, ``PO_CODING``) and export batch (None: not exported
        yet). SQLite reads those out of each coding, the rest of it not decoded: a PO billed by every invoice of a big
        vendor has thousands (3,381 at 300,000 invoices), whose whole codings took 1.5 s to decode at each open and
        check of one of them (D-199). The match and the PO page keep a big PO's in each process (``po.po_billing``,
        D-202), brought up to date by ``po_invoices_since``."""
        with self._conn() as conn:
            quoted, owned = self._billing_number(conn, key)  # (its number another order's too: those billing it)
            rows = conn.execute(_PO_INVOICES, (quoted, *ACTIVE_STATUSES)).fetchall()
        out = []
        for n, status, number, date, batch, facts, doc in rows:
            if n == exclude_id or (owned is not None and n not in owned):
                continue
            out.append(_po_row(n, status, number, date, batch, json.loads(facts) if doc is None else None, doc))
        return out

    def po_invoices_since(self, key: str, since: tuple[Any, int, dict[str, int]] | None = None,
                          kept: dict[int, Any] | None = None) -> PoChanges | None:  # fmt: skip
        """The invoices quoting the PO ``key`` now, and those of them to read again, read (``PoChanges``), for a
        process keeping them (``po.po_billing``, D-202): every one without ``since`` (an earlier read's stamp) or once
        it no longer holds (another database or summary since, a restore; a change since gone from the vendors' change
        log); else those not in ``kept`` ({id: its vendor, as read then}), those the log names since (D-174), those of
        a vendor whose count of changes moved by more than the log says (an older AP Coder's triggers count, writing
        no log: B-1811), and those with no vendor, whose changes no log follows. Which invoices quote the PO is read
        each time, off its index (a PO number changed by plain SQL alone is no change the log follows). The log and
        the invoices it names are read in one moment (B-1812). None inside a write under way, which may yet be undone:
        nothing read in it is kept (``po_invoices`` reads them afresh)."""
        kept = kept or {}
        with self._conn() as conn:
            if conn.in_transaction:
                return None
            with one_snapshot(conn):
                quoted, owned = self._billing_number(conn, key)  # (its number another order's too, D-267)
                summary_id, first, last, quoting = conn.execute(_PO_MOMENT, (quoted,)).fetchone()
                quoting = sorted(n for n in json.loads(quoting) if owned is None or n in owned)
                todo = quoting
                if since is not None and since[0] == summary_id and since[1] >= (first or 1) - 1:
                    named, logged = set(), dict.fromkeys(since[2], 0)
                    for vendor, n in json.loads(conn.execute(
                            "SELECT json_group_array(json_array(vendor_key, invoice_id)) FROM vendor_changes WHERE "
                            "seq > ?", (since[1],)).fetchone()[0]):  # fmt: skip
                        named.add(n)
                        if vendor in logged:
                            logged[vendor] += 1
                    counts = _vendor_counts(conn, since[2])
                    moved = {v for v, count in since[2].items() if counts[v] - count != logged[v]}
                    todo = [n for n in quoting if n not in kept or n in named or kept[n] is None or kept[n] in moved]
                read = self._po_invoices_of(conn, todo)
                vendors = {kept[n] for n in quoting if n not in read} | {vendor for vendor, _ in read.values()}
                vendors.discard(None)
                counts = _vendor_counts(conn, vendors)
        return PoChanges((summary_id, last or 0, counts), quoting, read)

    @staticmethod
    def _po_invoices_of(conn: Any, ids: list[int]) -> dict[int, tuple[Any, dict[str, Any] | None]]:
        """{id: (its vendor, its ``po_invoices`` row; None when not in review or approved)} of each of ``ids``, on
        ``conn``; SQLite's values of the codings decoded at once (each key one object for all, as in one coding)."""
        if not ids:
            return {}
        rows = conn.execute(_PO_INVOICES_OF, (*ACTIVE_STATUSES, json.dumps(ids))).fetchall()
        facts = iter(json.loads(f"[{','.join(r[6] for r in rows if r[6] is not None)}]"))
        return {n: (vendor, _po_row(n, status, number, date, batch, next(facts) if values is not None else None, doc)
                    if active else None)
                for n, vendor, status, number, date, batch, values, doc, active in rows}  # fmt: skip

    def vendor_invoices_without_po(self, key: str) -> list[dict[str, Any]]:
        """This vendor's active invoices that quote no PO, with their coding (they may still bill a PO line)."""
        if not key:
            return []
        with self._conn() as conn:
            # The vendor's invoices, by its index ("+": not by the PO's, where no PO is nearly every invoice, B-1583).
            rows = conn.execute(
                "SELECT id, status, invoice_number, invoice_date, ai_output, final_output FROM invoices "
                "WHERE vendor_key = ? AND +po_key = '' AND +status IN " + _ACTIVE_IN + " ORDER BY id",
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

    def po_keys_written_since(self, stamp: str) -> set[str]:
        """The POs written (imported, changed) since ``stamp``: what a JD Edwards read wrote before it stopped."""
        with self._conn() as conn:
            return {r[0] for r in conn.execute("SELECT po_key FROM purchase_orders WHERE updated_at >= ?", (stamp,))}

    def open_po_numbers(self, keys: list[str]) -> dict[str, str]:
        """{key: PO number} of those of these POs that are open, by key."""
        with self._conn() as conn:
            return {r[0]: r[1] for r in conn.execute(
                "SELECT po_key, po_number FROM purchase_orders WHERE status = ? AND po_key IN (SELECT value FROM "
                "json_each(?)) ORDER BY po_key", (OPEN, json.dumps(keys)))}  # fmt: skip

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
            begin_write(conn)  # the write lock from the read to the marks
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

    # --- The lines a fixed coding rule set (the rules: rules.py) ----------------------------------------

    def add_rules_applied(self, invoice_id: int, changes: list[dict[str, Any]]) -> None:
        """Remember lines a fixed coding rule set on the review screen (as rules applied at processing are), so
        the AI's accuracy is measured on its own answer for them."""
        with self._conn() as conn:
            # The write lock from the read to the write: a background check's notes saved in between are kept (B-658).
            begin_write(conn)
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
            begin_write(conn)
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
