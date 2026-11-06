"""The ERP's invoice register (invoices entered in the ERP before or outside AP Coder, for the duplicate checks): a
file imported on the Exports page, or JD Edwards' vouchers, which the API's sync brings a page at a time (D-181).

A JD Edwards voucher is kept by its own key (``erp_vouchers``: KCO-DCT-DOC-SFX) under the bill it is: the vendor, the
invoice number and the total, a row of ``erp_invoices``, what every check reads. A bill counts its ``copies`` by its
vouchers' dates, as the file import counts a bill's dates, and keeps its supplier's number (``an8``), so a check finds
it by that number too (the supplier renamed in JDE, R7). A voucher a complete read of its dates no longer lists
(voided or deleted in JDE) leaves, and its bill with its last voucher; nothing leaves while a read is under way, so a
register half read is never short of a voucher.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..memory import vendor_key
from ._base import _in_list, _norm_number, _now
from .summary import fetch_rows

# JD Edwards' vouchers by their key, under the bills they are; made at every start (an older AP Coder leaves the table
# and the column be: no version of their own).
JDE_REGISTER = (
    "CREATE TABLE IF NOT EXISTS erp_vouchers (erp_key TEXT PRIMARY KEY, vendor_key TEXT NOT NULL, number_key TEXT "
    "NOT NULL, total REAL NOT NULL, invoice_date TEXT NOT NULL DEFAULT '', seen INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS erp_vouchers_bill ON erp_vouchers (vendor_key, number_key, total)",
    "CREATE INDEX IF NOT EXISTS erp_vouchers_date ON erp_vouchers (invoice_date, seen)",
)
WRITE_ROWS = 500  # vouchers removed in one transaction: each a moment, never long enough to hold up an approval
# When the register was last cleared or replaced (a setting): what the JD Edwards sync's reads before then wrote is
# gone, so its next read of the vouchers is whole.
CLEARED_KEY = "erp_register_cleared"
ISO_DAY = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"  # a voucher's date as the sweep compares it (GLOB)


def _cleared(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                 "value = excluded.value", (CLEARED_KEY, _now()))  # fmt: skip


def add_jde_register(conn: sqlite3.Connection, columns: Any) -> None:
    """The register's supplier numbers and JD Edwards' vouchers (D-181), whatever the version (``columns``: the
    register's)."""
    if "an8" not in columns:
        conn.execute("ALTER TABLE erp_invoices ADD COLUMN an8 TEXT NOT NULL DEFAULT ''")
    for sql in JDE_REGISTER:
        conn.execute(sql)


class RegisterMixin:
    # --- The ERP's invoice register (invoices entered before or outside AP Coder) ---------------------

    def import_erp_register(
        self, rows: list[dict[str, Any]], replace_all: bool = False, actor: str | None = None
    ) -> int:
        """Invoices already in the ERP (``registers.rows_from_records`` output). Returns how many are stored. A bill
        listed more than once in the file with another date (the same vendor, number and amount: entered twice in the
        ERP) is one row that counts its ``copies``; rows alike in their date too are one bill (a register listing a
        bill once per GL line, or JD Edwards pay items), and a register imported again, overlapping the last, does not
        add to them."""
        now = _now()
        keyed = [
            ((vendor_key(r["vendor_name"]), _norm_number(r["invoice_number"]), round(float(r["total"]), 2)), r)
            for r in rows
            if vendor_key(r["vendor_name"]) and _norm_number(r["invoice_number"]) and r.get("total") is not None
        ]
        dates: dict[tuple[str, str, float], set[str]] = {}
        for key, r in keyed:
            dates.setdefault(key, set()).add(r.get("invoice_date") or "")
        copies = {key: len(found - {""}) or 1 for key, found in dates.items()}
        from ...erp.search import register_keys

        with self._conn() as conn:
            if replace_all:
                conn.execute("DELETE FROM erp_invoices")
                conn.execute("DELETE FROM erp_vouchers")
                _cleared(conn)
            conn.executemany(
                """INSERT INTO erp_invoices (vendor_key, number_key, total, vendor_name, invoice_number, invoice_date,
                   imported_at, copies, plain_name, raw_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(vendor_key, number_key, total)
                   DO UPDATE SET invoice_date = excluded.invoice_date, imported_at = excluded.imported_at,
                   copies = MAX(erp_invoices.copies, excluded.copies)""",
                [(*key, r["vendor_name"], r["invoice_number"], r.get("invoice_date") or "", now, copies[key],
                  *register_keys(r["vendor_name"], r["invoice_number"])) for key, r in keyed],
            )  # fmt: skip
            count = int(conn.execute("SELECT COUNT(*) FROM erp_invoices").fetchone()[0])
            detail: dict[str, Any] = {"rows": len(rows), "total": count}
            if repeats := sum(1 for n in copies.values() if n > 1):
                detail["repeated"] = repeats  # bills the file lists more than once
            self._log(conn, "erp_register_imported", actor=actor, detail=detail)
        return count

    def erp_register_repeats(self) -> int:
        """How many bills the ERP register lists more than once (entered twice in the ERP: the duplicate audit)."""
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM erp_invoices WHERE copies > 1").fetchone()[0])

    def erp_register_count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM erp_invoices").fetchone()[0])

    def clear_erp_register(self, actor: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM erp_invoices")
            conn.execute("DELETE FROM erp_vouchers")
            _cleared(conn)
            self._log(conn, "erp_register_imported", actor=actor, detail={"rows": 0, "total": 0, "cleared": True})

    def erp_register(self, numbers: list[str] | None = None) -> list[dict[str, Any]]:
        """Every invoice in the imported ERP register; with ``numbers``, those with one of these invoice numbers (as
        compared: ``norm_invoice_number``), in the same order."""
        sql = (
            "SELECT vendor_key, vendor_name, invoice_number, number_key, invoice_date, total, copies FROM erp_invoices"
        )
        with self._conn() as conn:
            if numbers is None:
                return [dict(r) for r in conn.execute(sql)]
            return fetch_rows(conn, f"{sql} WHERE {_in_list('number_key')} ORDER BY rowid", (json.dumps(numbers),))

    def erp_register_rows(self, where: str, params: tuple[Any, ...] | list[Any] = ()) -> list[dict[str, Any]]:
        """The ERP register's rows ``where`` keeps (the caller's SQL over its columns, with its vendor name and
        invoice number as the quick search compares them: ``plain_name``, ``raw_number``), in ``erp_register``'s
        order, read in one go."""
        with self._conn() as conn:
            sql = ("SELECT vendor_key, vendor_name, invoice_number, number_key, invoice_date, total, copies "
                   f"FROM erp_invoices WHERE {where} ORDER BY rowid")  # fmt: skip
            return fetch_rows(conn, sql, tuple(params))

    def in_erp(self, vendor_name: str, invoice_number: str, grand_total: float | None,
               an8: str | None = None) -> list[dict[str, Any]]:  # fmt: skip
        """This vendor's invoices with the same number already in the ERP (a credit note is not a duplicate
        of the invoice it reverses): filed under its name, or synced from JD Edwards under its supplier number
        (``an8``; None: the vendor list's ERP ID of the vendor) whatever name they were filed under (R7)."""
        key, number = vendor_key(vendor_name), _norm_number(invoice_number)
        if not key or not number:
            return []
        an8 = str(self.vendor_ids().get(key, "") if an8 is None else an8).strip()
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT invoice_number, invoice_date, total FROM erp_invoices "
                    "WHERE number_key = ? AND (vendor_key = ? OR (? != '' AND an8 = ?))",
                    (number, key, an8, an8),
                )
            ]
        if grand_total is None:
            return rows
        return [r for r in rows if (r["total"] < 0) == (grand_total < 0)]

    # --- JD Edwards' vouchers, from the API's sync (D-181) ----------------------------------------------

    def import_jde_vouchers(self, rows: list[dict[str, Any]], seen: int) -> int:
        """JD Edwards' vouchers (``jde_sync.voucher_rows``: its key, the supplier's name and number, the invoice
        number, date and gross) listed by the sync's read ``seen``, into the register: each kept by its key under its
        bill; one that moved (its supplier renamed in JDE, its gross corrected) leaves its old bill, which goes with
        its last voucher. One transaction (the caller's: a page of the read). How many bills are new."""
        vouchers: dict[str, tuple[str, str, float, dict[str, Any]]] = {}
        for r in rows:
            key, name, number = str(r.get("key") or ""), vendor_key(r["vendor_name"]), _norm_number(r["invoice_number"])
            if key and name and number and r.get("total") is not None:
                vouchers[key] = (name, number, round(float(r["total"]), 2), r)
        if not vouchers:
            return 0
        from ...erp.search import register_keys

        bills = {v[:3]: v[3] for v in vouchers.values()}  # (named by its last voucher)
        with self._conn() as conn:
            before = {r[0]: tuple(r[1:]) for r in conn.execute(
                f"SELECT erp_key, vendor_key, number_key, total FROM erp_vouchers WHERE {_in_list('erp_key')}",
                (json.dumps(sorted(vouchers)),))}  # fmt: skip
            known = {tuple(r) for r in conn.execute(
                f"SELECT vendor_key, number_key, total FROM erp_invoices WHERE {_in_list('number_key')}",
                (json.dumps(sorted({bill[1] for bill in bills})),))}  # fmt: skip
            conn.executemany(
                "INSERT INTO erp_vouchers (erp_key, vendor_key, number_key, total, invoice_date, seen) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(erp_key) DO UPDATE SET vendor_key = excluded.vendor_key, "
                "number_key = excluded.number_key, total = excluded.total, invoice_date = excluded.invoice_date, "
                "seen = excluded.seen",
                [(key, *v[:3], v[3].get("invoice_date") or "", seen) for key, v in vouchers.items()],
            )
            conn.executemany(
                """INSERT INTO erp_invoices (vendor_key, number_key, total, vendor_name, invoice_number, invoice_date,
                   imported_at, copies, plain_name, raw_number, an8) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(vendor_key, number_key, total) DO UPDATE SET invoice_date = excluded.invoice_date,
                   imported_at = excluded.imported_at,
                   an8 = CASE WHEN excluded.an8 != '' THEN excluded.an8 ELSE erp_invoices.an8 END""",
                [(*bill, r["vendor_name"], r["invoice_number"], r.get("invoice_date") or "", _now(),
                  *register_keys(r["vendor_name"], r["invoice_number"]), str(r.get("an8") or ""))
                 for bill, r in bills.items()],
            )  # fmt: skip
            moved = {bill for key, bill in before.items() if bill != vouchers[key][:3]}
            self._recount(conn, set(bills) | moved)
        return len(set(bills) - known)

    def jde_vouchers_unseen(self, since: str, seen: int, undated: bool = False) -> tuple[list[str], int]:
        """(the JD Edwards vouchers dated from ``since`` that the sync's read ``seen`` did not list, how many are dated
        from then): read only. ``undated``: with those without a date YYYY-MM-DD (none, or one that couldn't be read),
        for a read of every voucher of the window."""
        where = "(invoice_date >= ?1 AND invoice_date GLOB ?2" + (" OR NOT invoice_date GLOB ?2)" if undated else ")")
        with self._conn() as conn:
            dated = conn.execute(f"SELECT COUNT(*) FROM erp_vouchers WHERE {where}", (since, ISO_DAY))
            window = int(dated.fetchone()[0])
            gone = [r[0] for r in conn.execute(f"SELECT erp_key FROM erp_vouchers WHERE {where} AND seen < ?3",
                                               (since, ISO_DAY, seen))]  # fmt: skip
        return gone, window

    def remove_jde_vouchers(self, keys: list[str], seen: int) -> int:
        """The vouchers ``keys`` out of the register (``jde_vouchers_unseen``'s; one a read from ``seen`` on listed
        again meanwhile stays), WRITE_ROWS to a transaction; a bill left with no voucher goes with them. How many
        bills went."""
        gone = 0
        for start in range(0, len(keys), WRITE_ROWS):
            listed = (json.dumps(keys[start : start + WRITE_ROWS]), seen)
            with self._conn() as conn:
                bills = {tuple(r) for r in conn.execute(
                    f"SELECT vendor_key, number_key, total FROM erp_vouchers WHERE {_in_list('erp_key')} AND seen < ?",
                    listed)}  # fmt: skip
                conn.execute(f"DELETE FROM erp_vouchers WHERE {_in_list('erp_key')} AND seen < ?", listed)
                gone += self._recount(conn, bills)
        return gone

    @staticmethod
    def _recount(conn: sqlite3.Connection, bills: set[tuple[Any, ...]]) -> int:
        """Each bill's copies counted again from its vouchers' dates; a bill left without a voucher removed. How many
        were removed."""
        gone = 0
        for bill in bills:
            count, dates = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT NULLIF(invoice_date, '')) FROM erp_vouchers WHERE vendor_key = ? AND "
                "number_key = ? AND total = ?", bill).fetchone()  # fmt: skip
            if count:
                copies = max(int(dates), 1)
                conn.execute("UPDATE erp_invoices SET copies = ? WHERE vendor_key = ? AND number_key = ? AND total = ? "
                             "AND copies != ?", (copies, *bill, copies))  # fmt: skip
            else:
                gone += conn.execute("DELETE FROM erp_invoices WHERE vendor_key = ? AND number_key = ? AND total = ?",
                                     bill).rowcount  # fmt: skip
        return gone
