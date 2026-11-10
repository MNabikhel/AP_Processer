"""What the supplier prior reads (``ai/supplier_prior.py``, D-212): how a supplier's lines were coded, counted. The
journal's own lines come from ``journal.journal_lines_for``; here are the reads it has no method for: the names the
journal prints its suppliers under (to find a vendor with no supplier number), the vendor list's rows that go by one
name, and a vendor's approved lines counted by account (never every row: the biggest vendor has 50,000).

``prior_stamp`` tells whether what was worked out from these is still right. Nothing here writes.
"""

from __future__ import annotations

from typing import Any

from ..memory import ACCEPTED, CODED, CORRECTED
from .journal import VERSION_KEY
from .summary import fetch_rows

PERSON_DECIDED = (ACCEPTED, CORRECTED, CODED)  # an approval a person gave here, line by line (not bulk, not history)
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
_NAMES = """SELECT supplier_key, MAX(supplier_number) AS supplier_number, supplier_name, COUNT(*) AS vouchers
    FROM journal_vouchers WHERE counted = 1 AND supplier_key != '' GROUP BY supplier_key, supplier_name
    ORDER BY supplier_key, supplier_name"""


class PriorMixin:
    def prior_stamp(self, key: str) -> tuple[Any, ...]:
        """What tells whether a supplier's prior worked out before is still right: the journal's stamp
        (``journal_stamp``), the vendor's lessons (their count, the highest id, the newest one: ``_lessons_stamp``)
        and the count of the vendor list's changes (a supplier number given or changed). ``key``: the vendor's."""
        with self._conn() as conn:
            row = conn.execute("SELECT (SELECT value FROM settings WHERE key = ?), (SELECT value FROM settings WHERE "
                               "key = 'changes/vendors')", (VERSION_KEY,)).fetchone()  # fmt: skip
            lessons = self._lessons_stamp(conn, key, conn.in_transaction)
        return (int(row[0] or 0), str(row[1] or ""), *lessons)

    def journal_supplier_names(self) -> list[dict[str, Any]]:
        """The suppliers of the journal by the names it prints them under (26 characters at most, as printed):
        {"supplier_key" (the number as it is compared), "supplier_number" (as printed), "supplier_name", "vouchers"},
        a row for each name a number was printed with. Vouchers with no supplier number are left out."""
        with self._conn() as conn:
            return fetch_rows(conn, _NAMES)

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
        "newest"}, the newest first; D-213)}. Lines left without an account are not counted."""
        with self._conn() as conn:
            return {"accounts": fetch_rows(conn, _APPROVED, (key,)),
                    "descriptions": fetch_rows(conn, _DESCRIPTIONS, (key, DESCRIPTIONS_READ)),
                    "remarks": fetch_rows(conn, _REMARKS, (key, DESCRIPTIONS_READ))}  # fmt: skip
