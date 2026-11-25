"""What the supplier prior reads (``ai/supplier_prior.py``, D-212): how a supplier's lines were coded, counted. The
journal's own lines come from ``journal.journal_lines_for``; here are the reads it has no method for: the names the
journal prints its suppliers under (to find a vendor with no supplier number), the vendor list's rows that go by one
name, a vendor's approved lines counted by account (never every row: the biggest vendor has 50,000), and the
journal's remarks counted by supplier and account (what a supplier it does not have is offered from, D-226).

``prior_stamp`` tells whether what was worked out from these is still right. Nothing here writes.
"""

from __future__ import annotations

from collections import Counter
from itertools import groupby
from operator import itemgetter
from typing import Any

from .journal import VERSION_KEY, without_voids
from .summary import fetch_rows

DESCRIPTIONS_READ = 400  # of a vendor's approved lines, the latest different ones: what its accounts are told apart by
_APPROVED = """SELECT final_gl, COALESCE(final_cc, '') AS final_cc, outcome, COUNT(*) AS lines,
    COUNT(DISTINCT invoice_id) AS invoices, MAX(created_at) AS last_at, MAX(id) AS newest
    FROM feedback WHERE vendor_key = ? AND final_gl NOT IN ('', 'UNASSIGNED')
    GROUP BY final_gl, COALESCE(final_cc, ''), outcome"""
_DESCRIPTIONS = """SELECT final_gl, COALESCE(final_cc, '') AS final_cc, description, outcome, COUNT(*) AS lines,
    MAX(id) AS newest FROM feedback WHERE vendor_key = ? AND final_gl NOT IN ('', 'UNASSIGNED')
    GROUP BY final_gl, COALESCE(final_cc, ''), description, outcome ORDER BY newest DESC LIMIT ?"""
_REMARKS = """SELECT final_gl, COALESCE(final_cc, '') AS final_cc, final_remark, remark_from, outcome,
    COUNT(*) AS lines, MAX(id) AS newest FROM feedback WHERE vendor_key = ? AND final_gl NOT IN ('', 'UNASSIGNED')
    AND final_remark != '' GROUP BY final_gl, COALESCE(final_cc, ''), final_remark, remark_from, outcome
    ORDER BY newest DESC LIMIT ?"""
_LEDGERS = """SELECT final_gl, COALESCE(final_cc, '') AS final_cc, final_subledger, final_subledger_type, final_units,
    ledger_from, outcome, COUNT(*) AS lines, MAX(id) AS newest FROM feedback WHERE vendor_key = ?
    AND final_gl NOT IN ('', 'UNASSIGNED') AND (final_subledger != '' OR final_units IS NOT NULL)
    GROUP BY final_gl, COALESCE(final_cc, ''), final_subledger, final_subledger_type, final_units, ledger_from, outcome
    ORDER BY newest DESC LIMIT ?"""
_NAMES = """SELECT supplier_key, MAX(supplier_number) AS supplier_number, supplier_name, COUNT(*) AS vouchers
    FROM journal_vouchers WHERE counted = 1 AND supplier_key != '' GROUP BY supplier_key, supplier_name
    ORDER BY supplier_key, supplier_name"""
_LAST_DATE = "SELECT MAX(gl_date) FROM journal_vouchers WHERE counted = 1 AND gl_date != ''"
_REMARKED = """SELECT v.supplier_key, l.business_unit, l.object, l.subsidiary, l.remark, l.account, l.amount
    FROM journal_vouchers v JOIN journal_lines l ON l.voucher_id = v.id
    WHERE v.counted = 1 AND v.affiliate = 0 AND v.balanced = 1 AND l.kind = 'coding'
    ORDER BY v.supplier_key, v.gl_date, v.id, l.n"""
_REMARK_KEY = ("supplier_key", "business_unit", "object", "subsidiary", "remark")


class PriorMixin:
    def prior_stamp(self, key: str) -> tuple[Any, ...]:
        """What tells whether a supplier's prior worked out before is still right: the journal's stamp
        (``journal_stamp``) and the database's ``summary_id`` (another after a restore, which puts the counts back to
        the backup's: a journal loaded after it is never taken for the one it took out, B-2995), its first two, which
        say what the journal is; the count of the vendor list's changes (a supplier number given or changed) and the
        vendor's lessons (their count, the highest id, the newest one: ``_lessons_stamp``). ``key``: the vendor's."""
        with self._conn() as conn:
            row = conn.execute("SELECT (SELECT value FROM settings WHERE key = ?), (SELECT value FROM settings WHERE "
                               "key = 'summary_id'), (SELECT value FROM settings WHERE key = 'changes/vendors')",
                               (VERSION_KEY,)).fetchone()  # fmt: skip
            lessons = self._lessons_stamp(conn, key, conn.in_transaction)
        return (int(row[0] or 0), str(row[1] or ""), str(row[2] or ""), *lessons)

    def journal_supplier_names(self) -> list[dict[str, Any]]:
        """The suppliers of the journal by the names it prints them under (26 characters at most, as printed):
        {"supplier_key" (the number as it is compared), "supplier_number" (as printed), "supplier_name", "vouchers"},
        a row for each name a number was printed with. Vouchers with no supplier number are left out."""
        with self._conn() as conn:
            return fetch_rows(conn, _NAMES)

    def journal_last_date(self) -> str:
        """The latest G/L date of the journal's vouchers counted (YYYY-MM-DD; "" when none has one): a line's age is
        measured from it (``supplier_prior.age_weight``, D-251), not from today."""
        with self._conn() as conn:
            row = conn.execute(_LAST_DATE).fetchone()
        return str(row[0] or "") if row else ""

    def journal_remarks(self) -> list[dict[str, Any]]:
        """The journal's coding lines counted by supplier, account and remark, as ``journal_lines_for`` reads them
        (an affiliate's voucher and one that does not balance left out): {"supplier_key", "business_unit",
        "object", "subsidiary", "remark", "lines"}, in that order; a voucher voided and its void left out
        (``without_voids``). A year of 25,000 lines is a few thousand rows."""
        with self._conn() as conn:
            rows = fetch_rows(conn, _REMARKED)
        counted: Counter[tuple[Any, ...]] = Counter()
        for _, lines in groupby(rows, key=itemgetter("supplier_key")):
            counted.update(tuple(line[k] for k in _REMARK_KEY) for line in without_voids(list(lines)))
        return [{**dict(zip(_REMARK_KEY, key, strict=True)), "lines": n} for key, n in sorted(counted.items())]

    def vendors_named(self, key: str) -> list[dict[str, Any]]:
        """The vendor list's rows that go by one name: the vendor under ``key`` and those kept under "<key> #<ERP
        ID>" (two suppliers whose names read alike, B-930), in the order they were kept: {"vendor_key",
        "display_name", "erp_id"}."""
        with self._conn() as conn:
            return fetch_rows(
                conn,
                "SELECT vendor_key, display_name, erp_id FROM vendors WHERE vendor_key = ? OR (vendor_key >= ? AND "
                "vendor_key < ?) ORDER BY rowid",
                (key, f"{key} #", f"{key} $"),
            )

    def approved_coding(self, key: str) -> dict[str, Any]:
        """A vendor's lessons counted, for the supplier prior: {"accounts": a row for each GL code, cost center and
        outcome ({"final_gl", "final_cc" ("" for none), "outcome", "lines", "invoices", "last_at", "newest": the
        highest lesson id}), "descriptions": the latest ``DESCRIPTIONS_READ`` different lines of them ({"final_gl",
        "final_cc", "description", "outcome", "lines", "newest"}, the newest first), "remarks": the latest different
        remarks approved with them ({"final_gl", "final_cc", "final_remark", "remark_from", "outcome", "lines",
        "newest"}, the newest first; D-213), "ledgers": the subledgers and units approved with them ({"final_gl",
        "final_cc", "final_subledger", "final_subledger_type", "final_units", "ledger_from" ("person": typed),
        "outcome", "lines", "newest"}, the newest first; B-2830)}. Lines left without an account are not counted."""
        with self._conn() as conn:
            return {"accounts": fetch_rows(conn, _APPROVED, (key,)),
                    "descriptions": fetch_rows(conn, _DESCRIPTIONS, (key, DESCRIPTIONS_READ)),
                    "remarks": fetch_rows(conn, _REMARKS, (key, DESCRIPTIONS_READ)),
                    "ledgers": fetch_rows(conn, _LEDGERS, (key, DESCRIPTIONS_READ))}  # fmt: skip
