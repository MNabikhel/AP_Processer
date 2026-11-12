"""Last year's coding as JD Edwards printed it: the Transaction Journal's vouchers and lines (report R09321, read by
``erp/journal.read_journal``), kept in tables of their own and never pressed into ``feedback``. It is another kind of
evidence than an approved invoice line: the department's own 30-character remarks, a voucher at a time, keyed by the
SUPPLIER NUMBER, with splits.

* ``journal_files``: a row a file loaded (its name, its hash, who, when, the period, the counts). The same file
  again adds nothing; ``forget_journal`` takes a file out again.
* ``journal_vouchers``: every voucher of every file, also an affiliate's, a credit and one that does not balance,
  each marked. ``supplier_number`` is the text the report printed (leading zeros kept); ``supplier_key`` the same
  without them, which is what a supplier is looked up by.
* ``journal_lines``: every line of each voucher with its kind: ``coding`` (what AP chose), ``payable`` or ``tax``
  (what JD Edwards added).

Two files may cover the same months. A voucher has no document number in the report, so it is known by what it says
(``voucher_identity``: document type, company, G/L date, supplier number and its lines), and the same voucher in two
files is counted once: ``counted`` is 1 on the vouchers of the file that holds the most of them (the earliest loaded
when they hold as many, so a later file replaces nothing), 0 on the others. Every read here is of the counted ones;
the others wait, and take their place when the file that had it is forgotten. A file half written (the computer
stopped) is never read: its vouchers are counted only once it is whole (``journal_files.done``).

A year is about 25,000 lines: written a few hundred vouchers a transaction (``VOUCHERS_A_WRITE``), so no other
writer waits long (as the JD Edwards sync's vouchers, B-1918). ``journal_stamp`` counts the changes, for a caller
that keeps what it worked out from these tables. A database with no journal loaded has the tables empty: nothing of
this is used.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from . import dialect
from ._base import _in_list, _now

CODING, PAYABLE, TAX = "coding", "payable", "tax"  # a line's kind, as erp/journal.py marks it
# The objects of the lines AP did not choose (a company's own account numbers), as plain ``settings`` rows: text, a
# list with commas. What a journal was last loaded with; the next one is read with them.
SETTING_KEYS = {"trade": "journal_payable_trade", "intercompany": "journal_payable_intercompany",
                "tax": "journal_tax_objects"}  # fmt: skip
VERSION_KEY = "journal_version"
VOUCHERS_A_WRITE = 250  # vouchers written in one transaction, with their lines
# A load not finished this long after it began stopped half way (a load takes seconds).
STOPPED_AFTER = dt.timedelta(hours=1)
SAME_FILE = "this file is loaded already"
IN_ANOTHER_FILE = "the same voucher is in a file loaded before"
NO_VOUCHER = "This file has no voucher that could be read: nothing was loaded."
TAKEN_OVER = "This file was being loaded a second time at the same moment: nothing of this load was kept."

_VOUCHER_COLUMNS = ("id", "file_id", "row", "doc_type", "company", "gl_date", "supplier_number", "supplier_key",
                    "supplier_name", "gross", "tax", "affiliate", "credit", "balanced", "both_columns",
                    "coding_lines", "identity")  # fmt: skip
_LINE_COLUMNS = ("voucher_id", "n", "row", "account", "business_unit", "object", "subsidiary", "description",
                 "amount", "remark", "kind")  # fmt: skip
_INSERT_VOUCHER = (f"INSERT INTO journal_vouchers ({', '.join(_VOUCHER_COLUMNS)}) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")  # fmt: skip
_INSERT_LINE = f"INSERT INTO journal_lines ({', '.join(_LINE_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
_FLAGS = ("affiliate", "credit", "balanced", "both_columns")
# Which of a voucher's copies count: those of the finished file that holds the most of them, the earliest on a tie.
_RECOUNT = """UPDATE journal_vouchers SET counted = CASE WHEN file_id = (
        SELECT w.file_id FROM journal_vouchers w JOIN journal_files f ON f.id = w.file_id AND f.done = 1
        WHERE w.identity = journal_vouchers.identity GROUP BY w.file_id ORDER BY COUNT(*) DESC, w.file_id LIMIT 1)
    THEN 1 ELSE 0 END
    WHERE identity IN (SELECT identity FROM journal_vouchers WHERE file_id = ?)"""
_VOUCHERS = """SELECT v.id, v.file_id, v.row, v.doc_type, v.company, v.gl_date, v.supplier_number, v.supplier_name,
    v.gross, v.tax, v.affiliate, v.credit, v.balanced, v.both_columns, v.coding_lines FROM journal_vouchers v
    WHERE v.counted = 1"""
_LINES_FOR = """SELECT v.id AS voucher_id, l.n, v.gl_date, v.company, v.doc_type, v.supplier_number, v.supplier_name,
    l.account, l.business_unit, l.object, l.subsidiary, l.description, l.amount, l.remark, l.kind,
    v.coding_lines AS voucher_coding_lines, v.affiliate, v.credit, v.balanced
    FROM journal_vouchers v JOIN journal_lines l ON l.voucher_id = v.id
    WHERE v.supplier_key = ? AND v.counted = 1 AND """
_SUPPLIERS = """SELECT MAX(v.supplier_number) AS supplier_number, MAX(v.supplier_name) AS supplier_name,
    COUNT(DISTINCT v.id) AS vouchers, SUM(v.affiliate) AS affiliate_vouchers, COUNT(l.n) AS coding_lines,
    COUNT(DISTINCT l.account) AS accounts, MIN(v.gl_date) AS first_date, MAX(v.gl_date) AS last_date
    FROM journal_vouchers v LEFT JOIN journal_lines l
        ON l.voucher_id = v.id AND l.kind = 'coding' AND v.affiliate = 0 AND v.balanced = 1
    WHERE v.counted = 1 GROUP BY v.supplier_key ORDER BY vouchers DESC, supplier_number"""


def supplier_key(number: Any) -> str:
    """A supplier number as it is compared: the text without spaces around it or leading zeros ("00740075" and
    "740075" are one supplier; "000" stays "0")."""
    text = str(number or "").strip()
    return text.lstrip("0") or text[:1]


def voucher_identity(voucher: Any) -> str:
    """What makes a voucher of the journal the same voucher in another file: its document type, company, G/L date,
    supplier number (``supplier_key``) and its lines (account, amount and remark; in any order). The report has no
    document number to go by. A line's kind is left out: the same voucher read with other payable or tax objects is
    still that voucher."""
    lines = sorted((line.account, round(line.amount * 100), line.remark) for line in voucher.lines)
    said = [voucher.doc_type, voucher.company, voucher.gl_date, supplier_key(voucher.supplier_number), lines]
    return hashlib.sha256(json.dumps(said, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]


def _listed(text: Any) -> tuple[str, ...]:
    """A list of objects as saved or typed: commas, semicolons, spaces or lines between; each once, in order."""
    return tuple(dict.fromkeys(str(text or "").replace(",", " ").replace(";", " ").split()))


def _learned(voucher: Any) -> bool:
    """Whether a voucher's coding is learned from: not an affiliate's, and its debits are its credits."""
    return not voucher.affiliate and voucher.balanced


def _counts(journal: Any) -> dict[str, Any]:
    """What a journal as read holds, before anything is written."""
    vouchers, totals = journal.vouchers, journal.totals
    return {
        "first_date": totals.first_date, "last_date": totals.last_date, "vouchers": totals.vouchers,
        "lines": totals.lines, "coding_lines": totals.coding, "payable_lines": totals.payable,
        "tax_lines": totals.tax, "affiliates": sum(1 for v in vouchers if v.affiliate),
        "unbalanced": sum(1 for v in vouchers if not v.balanced), "credits": sum(1 for v in vouchers if v.credit),
        "learned_vouchers": sum(1 for v in vouchers if _learned(v)),
        "learned_lines": sum(1 for v in vouchers if _learned(v) for line in v.lines if line.kind == CODING),
    }  # fmt: skip


def _flags(row: sqlite3.Row) -> dict[str, Any]:
    """A row as a dict, its marks as True or False."""
    return {key: bool(row[key]) if key in _FLAGS else row[key] for key in row.keys()}  # noqa: SIM118 (a Row)


def _file(row: sqlite3.Row) -> dict[str, Any]:
    kept = {key: row[key] for key in row.keys() if key != "detail"}  # noqa: SIM118 (a Row)
    return {**json.loads(row["detail"] or "{}"), **kept}


class JournalMixin:
    def journal_objects(self) -> dict[str, tuple[str, ...]]:
        """The objects of the journal's lines AP did not choose, as saved: {"trade": the trade payable objects,
        "intercompany": the intercompany payable ones, "tax": the tax ones}; each empty until a journal is loaded
        or they are saved (``save_journal_objects``). Settings ``journal_payable_trade``,
        ``journal_payable_intercompany`` and ``journal_tax_objects``."""
        with self._conn() as conn:
            rows = conn.execute(f"SELECT key, value FROM settings WHERE {_in_list('key')}",
                                (json.dumps(list(SETTING_KEYS.values())),))  # fmt: skip
            saved = {r["key"]: r["value"] for r in rows}
        return {name: _listed(saved.get(key)) for name, key in SETTING_KEYS.items()}

    def save_journal_objects(self, trade: Iterable[str], intercompany: Iterable[str] = (),
                             tax: Iterable[str] = (), actor: str | None = None) -> list[str]:  # fmt: skip
        """Save the three lists of objects (``journal_objects``); returns the settings' keys that changed, and
        records them in the activity log when any did. The journals loaded already keep the kinds they were read
        with: forget one and load it again to mark its lines by other objects."""
        given = {"trade": trade, "intercompany": intercompany, "tax": tax}
        wanted = {SETTING_KEYS[name]: ", ".join(_listed(" ".join(given[name]))) for name in SETTING_KEYS}
        before = self.journal_objects()
        changed = [key for name, key in SETTING_KEYS.items() if ", ".join(before[name]) != wanted[key]]
        with self._conn() as conn:
            for key in changed:
                conn.execute(dialect.upsert("settings", ("key", "value"), ("key",)), (key, wanted[key]))
            if changed:
                self._log(conn, "settings_changed", actor=actor, detail={"keys": changed})
        return changed

    def journal_stamp(self) -> int:
        """How many times the journal's tables changed (a file loaded that added a voucher, a file forgotten); 0:
        never. Cheap (one settings row): a caller that keeps what it worked out from the journal compares it."""
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (VERSION_KEY,)).fetchone()
        return int(row["value"]) if row else 0

    @staticmethod
    def _journal_changed(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
            (VERSION_KEY,),
        )

    def import_journal(self, journal: Any, *, source_name: str, source_hash: str, actor: str | None = None,
                       objects: Mapping[str, Sequence[str]] | None = None) -> dict[str, Any]:  # fmt: skip
        """Keep a journal as read (``erp/journal.read_journal``'s ``Journal``): every voucher and every line, marked
        as the reader marked them. ``source_name``: the file's name; ``source_hash``: the SHA-256 of its bytes, by
        which the same file again is known; ``objects``: the payable and tax objects it was read with ({"trade",
        "intercompany", "tax"}), kept with the file for the record.

        Returns {"file_id", "name", "loaded" (False: this very file is loaded already and nothing was written),
        "first_date", "last_date" (the G/L dates covered, YYYY-MM-DD), "vouchers", "lines", "coding_lines",
        "payable_lines", "tax_lines", "affiliates", "unbalanced", "credits" (all of the file), "learned_vouchers" and
        "learned_lines" (the vouchers that are no affiliate's and balance, and their coding lines), "added" (the
        vouchers now counted that were not before), "already" (the file's vouchers that were there from another
        file: kept with this file, not counted twice), "skipped" ({why: how many vouchers})}. One event in the
        activity log (``journal_imported``) when the file was loaded. ``ValueError``, nothing written, for a journal
        with no voucher. Written a few hundred vouchers a transaction, unless the caller holds one open."""
        vouchers = journal.vouchers
        result = {"file_id": None, "name": source_name, "loaded": False, **_counts(journal), "added": 0,
                  "already": len(vouchers), "skipped": {}}  # fmt: skip
        with self.session():
            with self._conn() as conn:
                dialect.begin_write(conn)
                loaded = conn.execute("SELECT id FROM journal_files WHERE hash = ? AND done = 1",
                                      (source_hash,)).fetchone()  # fmt: skip
                if loaded is not None:
                    return {**result, "file_id": loaded["id"], "skipped": {SAME_FILE: len(vouchers)}}
                if not vouchers:
                    raise ValueError(NO_VOUCHER)
                # What a load that stopped half way left: this file's, and any that began long ago.
                long_ago = (dt.datetime.now() - STOPPED_AFTER).isoformat(timespec="seconds")
                stopped = conn.execute("SELECT id FROM journal_files WHERE done = 0 AND (hash = ? OR loaded_at < ?)",
                                       (source_hash, long_ago))  # fmt: skip
                self._forget_files(conn, [r["id"] for r in stopped])
                detail = {key: result[key] for key in ("payable_lines", "tax_lines", "affiliates", "unbalanced",
                                                       "credits", "learned_vouchers", "learned_lines")}  # fmt: skip
                detail["objects"] = {name: list((objects or {}).get(name, ())) for name in SETTING_KEYS}
                detail["control_total_differs"] = bool(journal.totals.differ)
                file_id = int(conn.execute(
                    "INSERT INTO journal_files (name, hash, loaded_at, loaded_by, first_date, last_date, vouchers, "
                    "lines, coding_lines, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (source_name, source_hash, _now(), actor or "", result["first_date"], result["last_date"],
                     result["vouchers"], result["lines"], result["coding_lines"], json.dumps(detail)),
                ).lastrowid)  # fmt: skip
            for start in range(0, len(vouchers), VOUCHERS_A_WRITE):
                with self._conn() as conn:
                    dialect.begin_write(conn)
                    self._write_journal_vouchers(conn, file_id, vouchers[start : start + VOUCHERS_A_WRITE])
            with self._conn() as conn:
                dialect.begin_write(conn)
                added = self._finish_file(conn, file_id)
                if added is not None:
                    result.update(file_id=file_id, loaded=True, added=added, already=len(vouchers) - added)
                    if result["already"]:
                        result["skipped"] = {IN_ANOTHER_FILE: result["already"]}
                    self._log(conn, "journal_imported", actor=actor, detail={
                        "file": source_name, **{key: result[key] for key in (
                            "file_id", "first_date", "last_date", "vouchers", "added", "already", "coding_lines")},
                    })  # fmt: skip
        if added is None:
            raise ValueError(TAKEN_OVER)
        return result

    @staticmethod
    def _write_journal_vouchers(conn: sqlite3.Connection, file_id: int, vouchers: Sequence[Any]) -> None:
        """Some of a file's vouchers and their lines, not counted yet (``_finish_file``). A voucher's ``id`` is the
        next after the highest there is."""
        first = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM journal_vouchers").fetchone()[0]
        rows, lines = [], []
        for voucher_id, v in enumerate(vouchers, start=first):
            coding = sum(1 for line in v.lines if line.kind == CODING)
            rows.append((voucher_id, file_id, v.row, v.doc_type, v.company, v.gl_date, v.supplier_number.strip(),
                         supplier_key(v.supplier_number), v.supplier_name, v.gross, v.tax, int(v.affiliate),
                         int(v.credit), int(v.balanced), int(v.both_columns), coding, voucher_identity(v)))  # fmt: skip
            lines += [(voucher_id, n, line.row, line.account, line.business_unit, line.object, line.subsidiary,
                       line.description, line.amount, line.remark, line.kind)
                      for n, line in enumerate(v.lines, start=1)]  # fmt: skip
        conn.executemany(_INSERT_VOUCHER, rows)
        conn.executemany(_INSERT_LINE, lines)

    def _finish_file(self, conn: sqlite3.Connection, file_id: int) -> int | None:
        """A file whole: marked so, its vouchers counted (those not there from another file), and the journal's
        stamp moved when any was. Returns how many more vouchers are counted than before; None when the file's row
        is gone (the same file was loaded again meanwhile, which removed this load): what it wrote since is removed."""
        count = "SELECT COUNT(*) FROM journal_vouchers WHERE counted = 1"
        before = conn.execute(count).fetchone()[0]
        if not conn.execute("UPDATE journal_files SET done = 1 WHERE id = ? AND done = 0", (file_id,)).rowcount:
            self._forget_files(conn, [file_id])
            return None
        conn.execute(_RECOUNT, (file_id,))
        added = conn.execute(count).fetchone()[0] - before
        if added:
            self._journal_changed(conn)
        return added

    @staticmethod
    def _forget_files(conn: sqlite3.Connection, file_ids: Iterable[int]) -> int:
        """Remove these files with their vouchers and lines; the vouchers another file also has are counted from
        that one now. Returns how many vouchers were removed."""
        removed = 0
        for file_id in file_ids:
            conn.execute("UPDATE journal_files SET done = 0 WHERE id = ?", (file_id,))
            conn.execute(_RECOUNT, (file_id,))  # (the file no longer finished: its copies count from another)
            conn.execute("DELETE FROM journal_lines WHERE voucher_id IN (SELECT id FROM journal_vouchers WHERE "
                         "file_id = ?)", (file_id,))  # fmt: skip
            removed += conn.execute("DELETE FROM journal_vouchers WHERE file_id = ?", (file_id,)).rowcount
            conn.execute("DELETE FROM journal_files WHERE id = ?", (file_id,))
        return removed

    def forget_journal(self, file_id: int, actor: str | None = None) -> int:
        """Take one loaded file out again (``journal_files``' ``id``): its vouchers and lines are removed, and a
        voucher another loaded file also has is counted from that file from now on. Returns how many vouchers were
        removed (0: no such file); one event in the activity log (``journal_forgotten``)."""
        with self._conn() as conn:
            dialect.begin_write(conn)
            row = conn.execute("SELECT name FROM journal_files WHERE id = ? AND done = 1", (file_id,)).fetchone()
            if row is None:
                return 0
            removed = self._forget_files(conn, [file_id])
            self._journal_changed(conn)
            self._log(conn, "journal_forgotten", actor=actor, detail={"files": [row["name"]], "vouchers": removed})
        return removed

    def forget_journals(self, actor: str | None = None) -> int:
        """Take every loaded file out: the three tables emptied. Returns how many vouchers were removed; one event
        in the activity log when there was a file."""
        with self._conn() as conn:
            dialect.begin_write(conn)
            names = [r["name"] for r in conn.execute("SELECT name FROM journal_files WHERE done = 1 ORDER BY id")]
            conn.execute("DELETE FROM journal_lines")
            removed = conn.execute("DELETE FROM journal_vouchers").rowcount
            conn.execute("DELETE FROM journal_files")
            if names:
                self._journal_changed(conn)
                self._log(conn, "journal_forgotten", actor=actor, detail={"files": names, "vouchers": removed})
        return removed

    def journal_files(self) -> list[dict[str, Any]]:
        """The files loaded, the latest first: {"id", "name", "hash", "loaded_at" (ISO, to the second), "loaded_by",
        "first_date", "last_date" (the G/L dates covered), "vouchers", "lines", "coding_lines" (of the file as it
        was read), "counted" (its vouchers counted now: fewer than ``vouchers`` when another file has some of them
        too), "payable_lines", "tax_lines", "affiliates", "unbalanced", "credits", "learned_vouchers",
        "learned_lines", "objects" ({"trade", "intercompany", "tax"}: the objects it was read with),
        "control_total_differs"}."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT f.id, f.name, f.hash, f.loaded_at, f.loaded_by, f.first_date, f.last_date, f.vouchers, "
                "f.lines, f.coding_lines, f.detail, (SELECT COUNT(*) FROM journal_vouchers v WHERE v.file_id = f.id "
                "AND v.counted = 1) AS counted FROM journal_files f WHERE f.done = 1 ORDER BY f.id DESC"
            ).fetchall()
        return [_file(row) for row in rows]

    def journal_already(self, journal: Any) -> int:
        """How many of the vouchers of a journal as read (not loaded) are there already from the files loaded: what
        loading it would keep with its file and not count again. Nothing is written."""
        wanted = Counter(voucher_identity(voucher) for voucher in journal.vouchers)
        with self._conn() as conn:
            rows = conn.execute("SELECT identity, COUNT(*) AS n FROM journal_vouchers WHERE counted = 1 AND "
                                f"{_in_list('identity')} GROUP BY identity", (json.dumps(list(wanted)),))  # fmt: skip
            return sum(min(wanted[r["identity"]], r["n"]) for r in rows)

    def journal_holding(self, identities: Iterable[str]) -> dict[str, int]:
        """The loaded file each of these voucher identities (``voucher_identity``) is counted in, by identity; those
        no file holds are left out. Nothing is written."""
        with self._conn() as conn:
            rows = conn.execute(f"SELECT identity, file_id FROM journal_vouchers WHERE counted = 1 AND "
                                f"{_in_list('identity')}", (json.dumps(list(set(identities))),))  # fmt: skip
            return {r["identity"]: r["file_id"] for r in rows}

    def journal_read_otherwise(self, file_id: int, journal: Any) -> int:
        """How many vouchers of a journal as read (``voucher_identity``) are not as the loaded file ``file_id`` kept
        them, or the other way round: 0 when the same file reads as it was loaded; more when the reader reads it
        better now (the G/L dates of a journal printed day first: B-2543). Nothing is written."""
        wanted = Counter(voucher_identity(voucher) for voucher in journal.vouchers)
        with self._conn() as conn:
            rows = conn.execute("SELECT identity FROM journal_vouchers WHERE file_id = ?", (file_id,))
            kept = Counter(row["identity"] for row in rows)
        return max((wanted - kept).total(), (kept - wanted).total())

    def journal_lines_for(
        self, supplier_number: str, *, kinds: Sequence[str] = (CODING,), include_affiliates: bool = False,
        include_unbalanced: bool = False,
    ) -> list[dict[str, Any]]:  # fmt: skip
        """A supplier's lines in the journal, the oldest first (by G/L date, then voucher, then the line's place in
        it). ``supplier_number``: as text; leading zeros and spaces around it do not matter ("0740075" finds
        "740075"). ``kinds``: which lines ("coding": what AP chose; "payable", "tax": what JD Edwards added). The
        lines of an affiliate's voucher and of one that does not balance are left out unless asked for: their coding
        is not learned from. A voucher two files hold is read once.

        Each line: {"voucher_id", "n" (its place in the voucher, from 1), "gl_date" (YYYY-MM-DD, "" when the report's
        could not be read), "company", "doc_type", "supplier_number" (as printed), "supplier_name" (26 characters at
        most), "account" ("0021061010.7340.2210"), "business_unit", "object", "subsidiary" ("" for none),
        "description" (the account's name as printed, 24 characters at most), "amount" (debit - credit, so a credit
        is negative), "remark" (AP's words, 30 characters at most, may be ""), "kind", "voucher_coding_lines" (1: the
        whole voucher went to this account), "affiliate", "credit", "balanced" (True or False)}."""
        kinds = [kinds] if isinstance(kinds, str) else list(kinds)
        sql = _LINES_FOR + _in_list("l.kind")
        sql += "" if include_affiliates else " AND v.affiliate = 0"
        sql += "" if include_unbalanced else " AND v.balanced = 1"
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY v.gl_date, v.id, l.n",
                                (supplier_key(supplier_number), json.dumps(kinds))).fetchall()  # fmt: skip
        return [_flags(row) for row in rows]

    def journal_suppliers(self) -> list[dict[str, Any]]:
        """Every supplier of the journal, the one with the most vouchers first: {"supplier_number" (as printed),
        "supplier_name", "vouchers" (all of its vouchers), "affiliate_vouchers" (those with an intercompany payable
        line), "coding_lines" and "accounts" (the coding lines that are learned from, see ``journal_lines_for``, and
        how many different account strings they went to), "first_date", "last_date"}. Vouchers with no supplier
        number are one row, its ``supplier_number`` ""."""
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(_SUPPLIERS)]

    def journal_vouchers(self, start: str | None = None, end: str | None = None,
                         supplier_number: str | None = None) -> list[dict[str, Any]]:  # fmt: skip
        """The journal's vouchers, the oldest first (by G/L date), every kind of them: an affiliate's, a credit and
        one that does not balance too, each marked. ``start`` and ``end``: G/L dates YYYY-MM-DD, both included (a
        voucher with no date is found only when neither is given); ``supplier_number``: as ``journal_lines_for``. A
        voucher two files hold is given once.

        Each voucher: {"id", "file_id", "row" (its first row in its file), "doc_type", "company", "gl_date",
        "supplier_number", "supplier_name", "gross" (what the supplier is owed: minus the payable lines), "tax" (the
        tax lines), "affiliate", "credit", "balanced", "both_columns" (True or False), "coding_lines" (how many),
        "lines": every line in the report's order, each {"n", "row", "account", "business_unit", "object",
        "subsidiary", "description", "amount", "remark", "kind"}}."""
        where, args = "", []
        for clause, value in (("v.gl_date >= ?", start), ("v.gl_date <= ?", end),
                              ("v.supplier_key = ?", supplier_number)):  # fmt: skip
            if value is not None:
                where += f" AND {clause}"
                args.append(supplier_key(value) if clause.startswith("v.supplier_key") else value)
        columns = ", ".join(f"l.{name}" for name in _LINE_COLUMNS)
        with self._conn() as conn:
            found = {r["id"]: {**_flags(r), "lines": []}
                     for r in conn.execute(f"{_VOUCHERS}{where} ORDER BY v.gl_date, v.id", args)}  # fmt: skip
            lines = conn.execute(
                f"SELECT {columns} FROM journal_lines l JOIN journal_vouchers v ON v.id = "
                f"l.voucher_id WHERE v.counted = 1{where} ORDER BY l.voucher_id, l.n",
                args,
            )
            for line in lines:
                kept = dict(line)
                found[kept.pop("voucher_id")]["lines"].append(kept)
        return list(found.values())
