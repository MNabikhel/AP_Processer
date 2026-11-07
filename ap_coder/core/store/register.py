"""The ERP's invoice register (invoices entered in the ERP before or outside AP Coder, for the duplicate checks): a
file imported on the Exports page, or JD Edwards' vouchers, which the API's sync brings a page at a time (D-181).

A JD Edwards voucher is kept by its own key (``erp_vouchers``: KCO-DCT-DOC-SFX) under the bill it is: the vendor, the
invoice number and the total, a row of ``erp_invoices``, what every check reads. A bill counts its ``copies`` by its
vouchers' dates, as the file import counts a bill's dates, and keeps its supplier's number (``an8``), so a check finds
it by that number too (the supplier renamed in JDE, R7). A voucher a complete read of its dates no longer lists
(voided or deleted in JDE) leaves, and its bill with its last voucher; nothing leaves while a read is under way, so a
register half read is never short of a voucher.

The quick search reads the register's rows holding a word through an index of its words (``register_search``, D-194),
kept by the register's own writes.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..keys import register_keys
from ..memory import vendor_key
from ._base import _in_list, _norm_number, _now
from .dialect import ROWID, begin_write, table_names
from .learning import one_snapshot
from .summary import fetch_rows, fts_available

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
# The quick search's index of the register (D-194): FTS5's trigram index of each row's vendor name and invoice number as
# the search compares them (``plain_name``, ``raw_number``), by the row's rowid (the order ``erp_register_rows`` reads
# in), where this SQLite has FTS5 (``summary.fts_available``): a word is looked up in it, not in each of the register's
# rows (600,000 at 300,000 invoices). Kept by the register's own writes in their transaction (a row added: rowids only
# grow; a row removed; the register cleared), and in step while SEARCH_STEP (a setting) is the register's count of
# changes (``changes/register``: its triggers count each row written, whoever writes it). A write that left it as it
# was (an older AP Coder's, plain SQL, a computer whose SQLite has no FTS5) leaves it behind: the search reads the
# register itself, as before, until the next start builds it again (``build_register_search``).
SEARCH_SCHEMA = "CREATE VIRTUAL TABLE register_search USING fts5(plain_name, raw_number, tokenize = 'trigram')"
SEARCH_STEP = "register_search_step"
_COUNT = "COALESCE((SELECT value FROM settings WHERE key = 'changes/register'), '0')"
_LAST_ROWID = 2**63 - 1  # SQLite's largest rowid: past it, a row added gets one at random (not above the others)


def _cleared(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                 "value = excluded.value", (CLEARED_KEY, _now()))  # fmt: skip


def search_in_step(conn: sqlite3.Connection) -> bool:
    """Whether the register's search index is there and in step with the register as ``conn`` reads it (D-194)."""
    if not fts_available():
        return False
    return bool(conn.execute(f"SELECT (SELECT value FROM settings WHERE key = ?) = {_COUNT}", (SEARCH_STEP,))
                .fetchone()[0])  # fmt: skip


def _search_top(conn: sqlite3.Connection) -> int | None:
    """The register's largest rowid before a write (0: none), above which each row it adds is; None when one added
    might not be (the largest there is taken: the index is left behind)."""
    top = int(conn.execute(f"SELECT COALESCE(MAX({ROWID}), 0) FROM erp_invoices").fetchone()[0])
    return None if top >= _LAST_ROWID else top


def _search_kept(conn: sqlite3.Connection, top: int | None, cleared: bool = False) -> None:
    """After a write to the register in step with its search index (``top``: ``_search_top`` before it): the rows it
    added put in (those above ``top``; ``cleared``: the index emptied first, the register was), and the index in step
    with the register again; else (None) it is left behind."""
    if top is None:
        return
    if cleared:  # (made again: FTS5 removes a row at a time)
        conn.execute("DROP TABLE IF EXISTS register_search")
        conn.execute(SEARCH_SCHEMA)
    conn.execute(f"INSERT INTO register_search ({ROWID}, plain_name, raw_number) SELECT {ROWID}, plain_name, "
                 f"raw_number FROM erp_invoices WHERE {ROWID} > ?", (top,))  # fmt: skip
    conn.execute(f"INSERT INTO settings (key, value) VALUES (?, {_COUNT}) ON CONFLICT(key) DO UPDATE SET "
                 "value = excluded.value", (SEARCH_STEP,))  # fmt: skip


def build_register_search(conn: sqlite3.Connection) -> None:
    """The register's search index built again when it is not in step with the register (at every start: made
    first, or left behind by a write that did not keep it, D-194), where this SQLite has FTS5."""

    def ready() -> bool:
        return "register_search" in table_names(conn) and search_in_step(conn)

    if not fts_available() or ready():
        return
    begin_write(conn)
    if not ready():  # (another process may have built it meanwhile)
        _search_kept(conn, 0, cleared=True)


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
        with self._conn() as conn:
            begin_write(conn)
            top = _search_top(conn) if search_in_step(conn) else None  # (the search's index kept with it, D-194)
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
            _search_kept(conn, None if top is None else 0 if replace_all else top, cleared=replace_all)
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
            begin_write(conn)
            top = _search_top(conn) if search_in_step(conn) else None  # (the search's index kept with it, D-194)
            conn.execute("DELETE FROM erp_invoices")
            conn.execute("DELETE FROM erp_vouchers")
            _cleared(conn)
            _search_kept(conn, None if top is None else 0, cleared=True)
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

    def erp_register_rows(self, where: str, params: tuple[Any, ...] | list[Any] = (),
                          indexed: tuple[str, list[Any]] | None = None) -> list[dict[str, Any]]:  # fmt: skip
        """The ERP register's rows ``where`` keeps (the caller's SQL over its columns, with its vendor name and
        invoice number as the quick search compares them: ``plain_name``, ``raw_number``), in ``erp_register``'s
        order, read in one go. ``indexed``: the same rows' condition (and its parameters) narrowed first by the
        register's search index (``register_search``, D-194), read instead while the index is in step with the
        register, in the same moment."""
        with self._conn() as conn, one_snapshot(conn):
            if indexed is not None and search_in_step(conn):
                where, params = indexed
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
        bill, with the newest read that listed it; one that moved (its supplier renamed in JDE, its gross corrected)
        leaves its old bill, which goes with its last voucher. One transaction (the caller's: a page of the read). How
        many bills are new."""
        vouchers: dict[str, tuple[str, str, float, dict[str, Any]]] = {}
        for r in rows:
            key, name, number = str(r.get("key") or ""), vendor_key(r["vendor_name"]), _norm_number(r["invoice_number"])
            if key and name and number and r.get("total") is not None:
                vouchers[key] = (name, number, round(float(r["total"]), 2), r)
        if not vouchers:
            return 0
        bills = {v[:3]: v[3] for v in vouchers.values()}  # (named by its last voucher)
        with self._conn() as conn:
            begin_write(conn)
            top = _search_top(conn) if search_in_step(conn) else None  # (the search's index kept with it, D-194)
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
                "seen = MAX(erp_vouchers.seen, excluded.seen)",  # (an older read writing after a newer one)
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
            self._recount(conn, set(bills) | moved, top is not None)
            _search_kept(conn, top)
        return len(set(bills) - known)

    def jde_vouchers_unseen(self, since: str, seen: int, undated: bool = False) -> tuple[list[str], int]:
        """(the JD Edwards vouchers dated after ``since`` that the sync's read ``seen`` did not list, how many are dated
        after then): read only. The day ``since`` itself is left alone, whether JDE's FromInvoiceDate lists it or not.
        ``undated``: with those without a date YYYY-MM-DD (none, or one that couldn't be read), for a read of every
        voucher of the window."""
        where = "(invoice_date > ?1 AND invoice_date GLOB ?2" + (" OR NOT invoice_date GLOB ?2)" if undated else ")")
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
                begin_write(conn)
                top = _search_top(conn) if search_in_step(conn) else None  # (the search's index kept, D-194)
                bills = {tuple(r) for r in conn.execute(
                    f"SELECT vendor_key, number_key, total FROM erp_vouchers WHERE {_in_list('erp_key')} AND seen < ?",
                    listed)}  # fmt: skip
                conn.execute(f"DELETE FROM erp_vouchers WHERE {_in_list('erp_key')} AND seen < ?", listed)
                gone += self._recount(conn, bills, top is not None)
                _search_kept(conn, top)
        return gone

    @staticmethod
    def _recount(conn: sqlite3.Connection, bills: set[tuple[Any, ...]], indexed: bool = False) -> int:
        """Each bill's copies counted again: its vouchers (each its own, by its key: one invoice entered twice in JDE
        is two, whatever their dates); a bill left without a voucher removed (``indexed``: from the search's index
        too, D-194). How many were removed."""
        gone = 0
        for bill in bills:
            count = conn.execute("SELECT COUNT(*) FROM erp_vouchers WHERE vendor_key = ? AND number_key = ? AND "
                                 "total = ?", bill).fetchone()[0]  # fmt: skip
            if count:
                copies = int(count)
                conn.execute("UPDATE erp_invoices SET copies = ? WHERE vendor_key = ? AND number_key = ? AND total = ? "
                             "AND copies != ?", (copies, *bill, copies))  # fmt: skip
                continue
            found = conn.execute(f"SELECT {ROWID} FROM erp_invoices WHERE vendor_key = ? AND number_key = ? AND "
                                 "total = ?", bill).fetchone()  # fmt: skip
            if found is not None:
                gone += conn.execute(f"DELETE FROM erp_invoices WHERE {ROWID} = ?", (found[0],)).rowcount
                if indexed:
                    conn.execute(f"DELETE FROM register_search WHERE {ROWID} = ?", (found[0],))
        return gone
