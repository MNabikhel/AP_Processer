"""GL accounts and cost centers (``import_accounts``, ``save_accounts``), the audit trail (``_log``, ``events``), our
bank accounts, the tax setup, the settings table (``get_setting``, ``set_setting``), the second approval's controls
in force (``approval_controls``, ``approval_reason``) and the reference data the coder and the checks use
(``reference_data``, ``checks_stamp``)."""

from __future__ import annotations

import contextvars
import dataclasses
import hashlib
import json
import math
import sqlite3
import threading
from array import array
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from .. import tax_setup as _tax
from ..reference_data import ReferenceData, ReferenceTable, marks_meals, parse_policy_notes
from ..tax_setup import TAX_TYPES, TREATMENTS, TaxRateTable, TaxSetup, TaxTreatment
from ._base import (
    ACCOUNT_TABLES,
    APPROVAL_CONTROLS,
    CHECKS_STAMP_KEY,
    EVENT_ORIGIN,
    REFERENCE_STAMP_KEY,
    _clean_code,
    _in_list,
    _now,
    parse_fx_rates,
)
from .summary import PAID_FROM, fetch_rows

# How the reason for a second approval starts when it is the currency changed from the one read (D-132).
CURRENCY_CHANGED = "the currency was changed from"
# Our bank accounts' columns (``bank_accounts``, D-66), the import's rows keyed by them (``coding/banks.py``).
BANK_COLUMNS = ("code", "name", "currency", "institution", "transit", "account_number", "gl_account", "is_default",
                "active")  # fmt: skip
# The reference data as last built, per database: (what it was built from, it) (``Store.reference_data``). Of
# REFERENCE_KEPT databases; of WITH_PAIRS_KEPT while one holds a chart of accounts' pairs (about 3 MB for 14,700
# accounts: the bound declared in ``core/caches.py`` holds either way).
_REFERENCE: dict[str, tuple[tuple[Any, ...], ReferenceData]] = {}
_REFERENCE_LOCK = threading.Lock()
REFERENCE_KEPT, WITH_PAIRS_KEPT = 8, 2
# Everything ``reference_data`` is built from, in one query: compared, it says whether the one kept still holds. The
# chart of accounts' pairs (D-209) by the count of its changes, what its tables hold (``chart_hash``, written with
# them: B-2125) and its settings (``chart.SETTING_KEYS``), not by its rows.
_REFERENCE_STAMP = """SELECT
    (SELECT group_concat(code || '|' || description || '|' || category || '|' || meals, char(10))
     FROM (SELECT * FROM gl_accounts ORDER BY code)),
    (SELECT group_concat(code || '|' || description || '|' || category, char(10))
     FROM (SELECT * FROM cost_centers ORDER BY code)),
    (SELECT group_concat(tax_type || '|' || treatment || '|' || gl_code, char(10))
     FROM (SELECT * FROM tax_treatments ORDER BY tax_type)),
    (SELECT group_concat(key || '=' || value, char(10))
     FROM (SELECT * FROM settings WHERE key IN ('our_province', 'site_provinces', 'policy_notes', 'chart_version',
           'chart_hash', 'postable_codes', 'never_offered', 'skip_words', 'home_business_unit', 'account_labels')
           ORDER BY key))"""

# What the checks read besides the reference data, as the queue's re-check compares them (``Store.checks_stamp``,
# B-815): JD Edwards' settings, whether it is in use (its switch, or a batch made: whether there is one, not how many,
# so an export changes no check, D-155), the days to pay, and the count of changes to the vendor master and the POs
# (``masters_version``, B-842), and our bank accounts (the account an invoice is paid from, D-66, B-958).
_CHECKS_STAMP = """SELECT
    (SELECT group_concat(key || '=' || value, char(10))
     FROM (SELECT * FROM settings WHERE key IN ('jde_e1', 'show_jde_settings', 'default_terms_days', 'masters_version')
           ORDER BY key)),
    (SELECT EXISTS (SELECT 1 FROM export_batches WHERE format = 'jde')),
    (SELECT group_concat(code || '|' || currency || '|' || gl_account || '|' || is_default || '|' || active, char(10))
     FROM (SELECT * FROM bank_accounts ORDER BY code))"""


def _file_stamp(path: Path) -> tuple[str, tuple[int, int] | None]:
    """(the tax-rate file, its time and size: None when it can't be read), as the stamps above compare it."""
    try:
        seen = path.stat()
    except OSError:
        return str(path), None
    return str(path), (seen.st_mtime_ns, seen.st_size)


def _short(parts: tuple[Any, ...]) -> str:
    """A stamp's parts as the short fingerprint that is stored and compared."""
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()[:20]


# The audit events of a change to the vendor master or the POs: each one counts (``count_masters_change``).
MASTERS_CHANGES = frozenset({"vendor_updated", "vendors_imported", "vendor_added", "vendors_deleted", "pos_imported",
                             "po_status", "pos_deleted"})  # fmt: skip


def count_masters_change(conn: sqlite3.Connection) -> None:
    """One more change to the vendor master or the POs (``masters_version``): the queue's checks read them, and
    ``Store.checks_stamp`` names this count rather than reading every vendor and PO line (B-842)."""
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('masters_version', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
    )


# The audit events with the invoice each is about (its vendor and number), as every list of events shows them.
_EVENTS = (
    "SELECT e.*, i.vendor_name AS invoice_vendor, i.invoice_number AS invoice_number FROM events e "
    "LEFT JOIN invoices i ON i.id = e.invoice_id"
)
# The same, of many events at a time (``find_events``): each invoice's vendor and number off the invoices' summary,
# which keeps them as they are (``summary.MIRROR``) in its narrow rows, not off each invoice's whole one (its coding,
# checks and text: a year's 600,000 events, 18 s at 300,000 invoices, B-2076). Read with the summary brought up to date.
_EVENTS_LISTED = _EVENTS.replace("LEFT JOIN invoices i", "LEFT JOIN invoice_summary i")
EVENTS_BATCH = 2000  # the events ``find_events`` reads at a time: memory stays flat however many there are


# Of a page of events of some actions, newest first (``find_events``), a twentieth of the events or fewer are read off
# their index by action and put in order; of more, the events are walked newest first, a page of them found sooner
# than sorting them all (the Invoices group of Activity: 9 events in 10, re-sorted for each batch of the download).
FEW_EVENTS = 20


def _event_filter(actions: Iterable[str] | None = None, hidden: Iterable[str] = (), actor: str | None = None,
                  since: str | None = None, until: str | None = None, *,
                  by_action: bool = True) -> tuple[str, list[Any]]:  # fmt: skip
    """The SQL condition on ``events e`` (and its arguments) of ``find_events`` and ``count_events``; ``by_action``
    False: ``actions`` not looked up by their index (``FEW_EVENTS``)."""
    where, args = ["1 = 1"], []
    if actions is not None:
        where.append(_in_list("e.action" if by_action else "+e.action"))
        args.append(json.dumps(list(actions)))
    if hidden:
        where.append(f"NOT {_in_list('e.action')}")
        args.append(json.dumps(list(hidden)))
    if actor is not None:
        where.append("e.actor = ?")
        args.append(actor)
    if since:
        where.append("e.created_at >= ?")
        args.append(since)
    if until:
        where.append("e.created_at < ?")
        args.append(until)
    return " AND ".join(where), args


# Who is signed in on the company server (set by web/identity.py): an event recorded without an actor (an upload, the
# demo, a setup import) records them (B-1106). None on a laptop, whose events are as before.
EVENT_ACTOR: contextvars.ContextVar[str | None] = contextvars.ContextVar("ap_coder_event_actor", default=None)


class AccountsMixin:
    # --- GL accounts / cost centers ----------------------------------------------------------

    # --- Audit trail ---------------------------------------------------------------------------------

    @staticmethod
    def _log(
        conn: sqlite3.Connection, action: str, invoice_id: int | None = None, actor: str | None = None,
        detail: dict[str, Any] | None = None, *, checked: bool = True,
    ) -> None:  # fmt: skip
        """Record an event. ``checked``: False for a change to the vendor master that no check reads (a note,
        ``save_vendor``): it leaves the checks stamp as it is (D-155)."""
        origin = EVENT_ORIGIN.get()
        actor = actor if actor else EVENT_ACTOR.get()  # the person signed in on the server (B-1106)
        if origin:
            detail = {**(detail or {}), "address": origin}  # never "from": events use it (B-129)
        conn.execute(
            "INSERT INTO events (invoice_id, action, actor, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (invoice_id, action, actor, json.dumps(detail or {}, default=str), _now()),
        )
        if action in MASTERS_CHANGES and checked:
            count_masters_change(conn)

    def log_event(
        self, action: str, invoice_id: int | None = None, actor: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:  # fmt: skip
        with self._conn() as conn:
            self._log(conn, action, invoice_id, actor, detail)

    def events(
        self, invoice_id: int | None = None, actions: list[str] | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Audit events, newest first (optionally for one invoice or some actions)."""
        sql = _EVENTS + " WHERE 1 = 1"  # with the invoice's vendor and number: which invoice each is about
        args: list[Any] = []
        if invoice_id is not None:
            sql += " AND e.invoice_id = ?"
            args.append(invoice_id)
        if actions:
            sql += f" AND e.action IN ({', '.join('?' for _ in actions)})"
            args += actions
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY e.id DESC LIMIT ?", (*args, limit))]
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else {}
        return rows

    def find_events(
        self, *, actions: Iterable[str] | None = None, hidden: Iterable[str] = (), actor: str | None = None,
        since: str | None = None, until: str | None = None, before: int | None = None,
        matches: Callable[[dict[str, Any]], bool] | None = None, limit: int | None = None,
        detail_if: str | None = None,
    ) -> Iterator[dict[str, Any]]:  # fmt: skip
        """Every audit event that matches, newest first and as ``events`` gives them, read a batch at a time (a year of
        them is never held at once, only a period's ids, 8 bytes each; nor a read kept open while the caller works): of
        ``actions`` (None: any) and none of ``hidden``, by ``actor``, made from ``since`` up to, not including,
        ``until`` (ISO days or times), older than event ``before``, and for which ``matches(event)`` holds (what SQL
        can't ask: the words an event is shown with); ``limit``: that many at most. ``detail_if``: an SQL condition on
        ``events e``; an event for which it doesn't hold comes with a detail of None, not read nor decoded (a year's
        report needs few of them, D-156)."""
        actions = None if actions is None else list(actions)
        # Without a period, a page of a person's events comes off their index in order (D-156), and those of common
        # actions off the events newest first (``FEW_EVENTS``), not every one of them read and sorted for each batch.
        by_action = since or until or actions is None or (actor is None and self._few_events(actions))
        where, args = _event_filter(actions, hidden, actor, since, until, by_action=bool(by_action))
        select = _EVENTS_LISTED if detail_if is None else _EVENTS_LISTED.replace(
            "e.*", f"e.id, e.invoice_id, e.action, e.actor, CASE WHEN {detail_if} THEN COALESCE(e.detail, '') END "
            "AS detail, e.created_at")  # fmt: skip
        sql = f"{select} WHERE {where} AND e.id < ? ORDER BY e.id DESC LIMIT ?"
        below, found = before if before is not None else 2**63 - 1, 0
        if limit is not None and limit <= 0:
            return  # none asked for (a batch of none would end the loop on no rows: B-1676)

        def taken(rows: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
            nonlocal found
            for r in rows:
                if r["detail"] is not None or detail_if is None:
                    r["detail"] = json.loads(r["detail"]) if r["detail"] else {}
                if matches is None or matches(r):
                    yield r
                    found += 1
                    if limit is not None and found >= limit:
                        return

        if since or until:
            # A period's events come off an index by day, not in their order: their ids are read in one go off it, then
            # the events a batch at a time by id, not all of the period's read, joined and sorted again for each batch
            # (a year's controls report: 313 s at 300,000 invoices, B-2076).
            most = -1 if matches is not None or limit is None else limit
            with self._conn() as conn:
                ids = array("q", (r[0] for r in conn.execute(
                    f"SELECT e.id FROM events e WHERE {where} AND e.id < ? ORDER BY e.id DESC LIMIT ?",
                    (*args, below, most))))  # fmt: skip
            by_id = f"{select} WHERE {_in_list('e.id')} ORDER BY e.id DESC"
            for start in range(0, len(ids), EVENTS_BATCH):
                chunk = json.dumps(ids[start : start + EVENTS_BATCH].tolist())
                with self._conn() as conn:
                    self._summary_fresh(conn)
                    rows = [dict(r) for r in conn.execute(by_id, (chunk,))]
                yield from taken(rows)
                if limit is not None and found >= limit:
                    return
            return
        while True:
            batch = EVENTS_BATCH if matches is not None or limit is None else min(EVENTS_BATCH, limit - found)
            with self._conn() as conn:
                self._summary_fresh(conn)
                rows = [dict(r) for r in conn.execute(sql, (*args, below, batch))]
            yield from taken(rows)
            if (limit is not None and found >= limit) or len(rows) < batch:
                return
            below = rows[-1]["id"]

    def _few_events(self, actions: list[str]) -> bool:
        """Whether a twentieth of the events or fewer are of ``actions`` (``FEW_EVENTS``), counted off their index up to
        that many at most."""
        with self._conn() as conn:
            most = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]) // FEW_EVENTS + 1
            found = conn.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM events e WHERE {_in_list('e.action')} LIMIT ?)",
                                 (json.dumps(actions), most)).fetchone()[0]  # fmt: skip
        return int(found) < most

    def count_events(
        self, *, actions: Iterable[str] | None = None, hidden: Iterable[str] = (), actor: str | None = None,
        since: str | None = None, until: str | None = None,
    ) -> int:  # fmt: skip
        """How many audit events ``find_events`` gives for these (counted by SQLite, none read)."""
        hidden = list(hidden)
        if actions is None and actor is None and not since and not until:
            # Every event but those of ``hidden`` actions: all of them counted (no row read), less those of ``hidden``
            # off their index, not each event's action looked at (1.2 million at 300,000 invoices, D-206; an
            # event's action is never NULL).
            with self._conn() as conn:
                if not hidden:
                    return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
                return int(conn.execute(f"SELECT (SELECT COUNT(*) FROM events) - (SELECT COUNT(*) FROM events e WHERE "
                                        f"{_in_list('e.action')})", (json.dumps(hidden),)).fetchone()[0])  # fmt: skip
        where, args = _event_filter(actions, hidden, actor, since, until)
        with self._conn() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM events e WHERE {where}", args).fetchone()[0])

    def events_stamp(self) -> str:
        """How many audit events there are and the newest one's id ("count/id"): another once one is recorded or
        deleted, so what is worked out from them is kept until then (``summary_memo``'s ``asked``, D-206). Read off the
        table's size and its last row, not each event."""
        with self._conn() as conn:
            return str(conn.execute("SELECT (SELECT COUNT(*) FROM events) || '/' || "
                                    "(SELECT COALESCE(MAX(id), '') FROM events)").fetchone()[0])  # fmt: skip

    def event_actors(self, hidden: Iterable[str] = ()) -> list[str]:
        """Everyone the audit events are by (but those of ``hidden`` actions only), by name, each once."""
        where, args = _event_filter(hidden=hidden)
        # Each name off the index of the events by person (D-156), the next one after another looked up (not every
        # event's name read: 1.2 million at 300,000 invoices, D-206), kept when one of its events is not of ``hidden``.
        with self._conn() as conn:
            rows = conn.execute(
                "WITH RECURSIVE a(actor) AS (SELECT MIN(actor) FROM events WHERE actor > '' UNION ALL SELECT (SELECT "
                "MIN(actor) FROM events WHERE actor > a.actor) FROM a WHERE a.actor IS NOT NULL) SELECT a.actor FROM a "
                f"WHERE a.actor IS NOT NULL AND EXISTS (SELECT 1 FROM events e WHERE e.actor = a.actor AND {where})",
                args)  # fmt: skip
            return sorted(str(r[0]) for r in rows)

    def event_values(self, action: str, field: str) -> set[str]:
        """The ``field`` of every ``action`` event's detail, as text (the emails marked done: their ids), read by
        SQLite in one query rather than every event decoded (``events``)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT CAST(json_extract(detail, ?) AS TEXT) FROM events WHERE action = ? AND "
                "json_valid(detail)",
                (f"$.{field}", action),
            )
            return {str(r[0]) for r in rows}

    def latest_events(self, action: str, invoice_ids: list[int]) -> dict[int, dict[str, Any]]:
        """{invoice: its newest ``action`` event} for each of ``invoice_ids`` that has one, in one query (what
        ``events(invoice_id, [action])[0]`` gives for each, without a query per invoice)."""
        ids = list(dict.fromkeys(int(i) for i in invoice_ids))
        if not ids:
            return {}
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM events WHERE action = ? AND {_in_list('invoice_id')} ORDER BY id DESC",
                (action, json.dumps(ids)),
            ).fetchall()
        latest: dict[int, dict[str, Any]] = {}
        for r in rows:
            if r["invoice_id"] not in latest:
                latest[r["invoice_id"]] = {**dict(r), "detail": json.loads(r["detail"]) if r["detail"] else {}}
        return latest

    def import_accounts(
        self,
        table: str,
        rows: list[dict[str, Any]],
        code_column: str,
        description_column: str | None,
        category_column: str | None = None,
        replace_all: bool = False,
        actor: str | None = None,
        log: bool = True,
        meals_column: str | None = None,
    ) -> dict[str, int]:
        """Import rows using the columns the user picked.

        Existing codes are updated, but only in the columns picked: importing codes without a category column keeps
        the categories already typed in. ``meals_column`` (GL accounts only): the column that marks the meals and
        entertainment accounts (D-90), "yes", "x", "M&E", "50%"..., blank or "no" for the others; without it (or in
        a row without that column) each code keeps its tick, even through ``replace_all`` (B-890). ``replace_all``
        with rows none of which has a code (the wrong column picked) is refused, nothing deleted: it would leave no
        account to code to.
        """
        if table not in ACCOUNT_TABLES:
            raise ValueError(f"unknown table {table!r}")
        if replace_all and rows and not any(_clean_code(row.get(code_column)) for row in rows):
            raise ValueError(
                f"No row has a code in the column {code_column!r}: nothing was replaced. Pick the column that "
                "holds the codes and import again."
            )
        added = updated = skipped = 0
        gl = table == "gl_accounts"
        with self._conn() as conn:
            # The meals ticks are usually set only here, not in the ERP: a replace whose file doesn't say (no meals
            # column, or a row without it) keeps the tick of each code still in the list (B-890).
            ticked = {r["code"] for r in conn.execute("SELECT code FROM gl_accounts WHERE meals = 1")} if gl else set()
            if replace_all:
                conn.execute(f"DELETE FROM {table}")
            existing = {r["code"] for r in conn.execute(f"SELECT code FROM {table}")}
            seen: set[str] = set()
            for row in rows:
                code = _clean_code(row.get(code_column))
                if not code or code in seen:
                    skipped += 1
                    continue
                seen.add(code)
                description = _clean_code(row.get(description_column)) if description_column else ""
                category = _clean_code(row.get(category_column)) if category_column else ""
                says_meals = bool(gl and meals_column and meals_column in row)
                meals = int(marks_meals(row.get(meals_column))) if says_meals else int(code in ticked)
                if code in existing:
                    changes: dict[str, Any] = {"description": description} if description_column else {}
                    if category_column:
                        changes["category"] = category
                    if says_meals:
                        changes["meals"] = meals
                    if changes:
                        sets = ", ".join(f"{column} = ?" for column in changes)
                        conn.execute(f"UPDATE {table} SET {sets} WHERE code = ?", (*changes.values(), code))
                    updated += 1
                elif gl:
                    conn.execute(
                        "INSERT INTO gl_accounts (code, description, category, created_at, meals) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (code, description, category, _now(), meals),
                    )
                    added += 1
                else:
                    conn.execute(
                        f"INSERT INTO {table} (code, description, category, created_at) VALUES (?, ?, ?, ?)",
                        (code, description, category, _now()),
                    )
                    added += 1
            if log:
                self._log(conn, "accounts_imported", actor=actor, detail={
                    "table": table, "added": added, "updated": updated, "skipped": skipped,
                    "replace_all": replace_all,
                })  # fmt: skip
        return {"added": added, "updated": updated, "skipped": skipped}

    def list_accounts(self, table: str) -> list[dict[str, str]]:
        if table not in ACCOUNT_TABLES:
            raise ValueError(f"unknown table {table!r}")
        with self._conn() as conn:
            if table == "gl_accounts":  # with the meals and entertainment flag (D-90)
                rows = conn.execute("SELECT code, description, category, meals FROM gl_accounts ORDER BY code")
                return [{**dict(r), "meals": bool(r["meals"])} for r in rows]
            return [dict(r) for r in conn.execute(f"SELECT code, description, category FROM {table} ORDER BY code")]

    def mark_meals_accounts(self, codes: Iterable[str], actor: str | None = None) -> list[str]:
        """Tick these GL accounts as meals and entertainment accounts (D-90: half the GST/HST on them is
        claimable). Returns the codes newly ticked, recorded as an edit of the list. ``ValueError``, nothing ticked,
        when one of them is not in the list."""
        wanted = sorted({str(code).strip() for code in codes} - {""})
        with self._conn() as conn:
            rows = conn.execute(f"SELECT code, meals FROM gl_accounts WHERE {_in_list('code')}", (json.dumps(wanted),))
            held = {r["code"]: r["meals"] for r in rows}
            missing = [code for code in wanted if code not in held]
            if missing or not wanted:
                raise ValueError(f"No GL account {', '.join(missing[:5])} is in the list: nothing was ticked."
                                 if missing else "No account was named: nothing was ticked.")  # fmt: skip
            new = [code for code in wanted if not held[code]]
            if new:
                conn.execute(f"UPDATE gl_accounts SET meals = 1 WHERE {_in_list('code')}", (json.dumps(new),))
                self._log(conn, "accounts_edited", actor=actor, detail={"table": "gl_accounts", "codes": new})
        return new

    def save_accounts(self, table: str, rows: list[dict[str, Any]], actor: str | None = None) -> None:
        """Replace the table with ``rows`` (used by the dashboard's editable grid)."""
        before = {r["code"]: r for r in self.list_accounts(table)}
        meals = "meals" if table == "gl_accounts" else None
        self.import_accounts(table, rows, "code", "description", "category", replace_all=True, log=False,
                             meals_column=meals)  # fmt: skip
        after = {r["code"]: r for r in self.list_accounts(table)}
        changed = sorted(c for c in before.keys() | after.keys() if before.get(c) != after.get(c))
        if changed:
            self.log_event("accounts_edited", actor=actor, detail={"table": table, "codes": changed})

    # --- Our bank accounts ("Pay from", D-66) -------------------------------------------------------------

    def bank_accounts(self, active_only: bool = False) -> list[dict[str, Any]]:
        """Our company's bank accounts (coding/banks.py), by currency then name; ``active_only``: those in use."""
        where = " WHERE active = 1" if active_only else ""
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM bank_accounts{where} ORDER BY currency, name, code")]
        for r in rows:
            r["is_default"], r["active"] = bool(r["is_default"]), bool(r["active"])
        return rows

    def save_bank_accounts(
        self, rows: list[dict[str, Any]], actor: str | None = None, imported: bool = False
    ) -> dict[str, int]:
        """Add or change accounts (``banks.clean_rows`` output), by code; an account not in ``rows`` is kept. An
        account made the default of its currency is the only one. Returns {"added", "updated"}."""
        before = {r["code"]: r for r in self.bank_accounts()}
        added = updated = 0
        now = _now()
        upsert = (
            f"INSERT INTO bank_accounts ({', '.join(BANK_COLUMNS)}, updated_at) VALUES "
            f"({', '.join('?' * len(BANK_COLUMNS))}, ?) "
            "ON CONFLICT(code) DO UPDATE SET "
            + ", ".join(f"{c} = excluded.{c}" for c in BANK_COLUMNS[1:])
            + ", updated_at = excluded.updated_at"
        )
        with self._conn() as conn:
            for row in rows:
                values = [row[c] if c not in ("is_default", "active") else int(bool(row[c])) for c in BANK_COLUMNS]
                if row["code"] in before:
                    old = before[row["code"]]
                    if all(old[c] == row[c] for c in BANK_COLUMNS):
                        continue
                    updated += 1
                else:
                    added += 1
                conn.execute(upsert, (*values, now))
                if row["is_default"] and row["active"]:
                    conn.execute("UPDATE bank_accounts SET is_default = 0 WHERE currency = ? AND code != ?",
                                 (row["currency"], row["code"]))  # fmt: skip
            if added or updated:
                self._log(conn, "accounts_imported" if imported else "accounts_edited", actor=actor, detail={
                    "table": "bank_accounts", "added": added, "updated": updated,
                    "codes": sorted(r["code"] for r in rows if before.get(r["code"]) != r)[:50]})  # fmt: skip
        return {"added": added, "updated": updated}

    def last_pay_from(self, key: str) -> str:
        """The bank account this vendor's last approved invoice was paid from ("" when none recorded)."""
        return self.last_pay_froms([key]).get(key, "") if key else ""

    def last_pay_froms(self, keys: list[str]) -> dict[str, str]:
        """``last_pay_from`` of each vendor key of ``keys`` that has one, in one query of the invoices' summary (its
        approved coding's ``pay_from``): the review queue asks it of every vendor waiting (D-155). Each vendor's
        latest is sought on the index ``summary_pay_from`` (B-1739), read in one step (``fetch_rows``)."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            rows = fetch_rows(
                conn,
                "SELECT k.value AS vendor_key, (SELECT pay_from FROM invoice_summary WHERE vendor_key = k.value AND "
                f"{PAID_FROM} ORDER BY reviewed_at DESC, id DESC LIMIT 1) AS code FROM json_each(?) AS k",
                (json.dumps(sorted({k for k in keys if k})),),
            )
        return {r["vendor_key"]: str(r["code"]) for r in rows if r["code"] is not None}

    # --- Tax setup and policy ---------------------------------------------------------------------

    def tax_treatments(self) -> dict[str, TaxTreatment]:
        """How each tax type is posted (the default treatment for one not set up): ``tax_treatments_in``."""
        with self._conn() as conn:
            return self.tax_treatments_in(conn)

    def set_tax_treatment(self, tax_type: str, treatment: str, gl_code: str = "", actor: str | None = None) -> None:
        if tax_type not in TAX_TYPES or treatment not in TREATMENTS:
            raise ValueError("invalid tax type or treatment")
        current = self.tax_treatments()[tax_type]
        with self._conn() as conn:
            if (current.treatment, current.gl_code) != (treatment, gl_code.strip()):
                self._log(conn, "tax_setup_changed", actor=actor, detail={
                    "tax_type": tax_type, "treatment": treatment, "gl_code": gl_code.strip(),
                })  # fmt: skip
            conn.execute(
                "INSERT INTO tax_treatments (tax_type, treatment, gl_code) VALUES (?, ?, ?) "
                "ON CONFLICT(tax_type) DO UPDATE SET treatment = excluded.treatment, gl_code = excluded.gl_code",
                (tax_type, treatment, gl_code.strip()),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def get_json(self, key: str, default: Any = None) -> Any:
        """Setting ``key`` saved as JSON, or ``default`` when it is unset or unreadable, or (a ``default`` given) not
        of its kind: a dict for ``{}``, a list for ``[]``."""
        raw = self.get_setting(key)
        if not raw:
            return default
        try:
            value = json.loads(raw)
        except ValueError:
            return default
        return default if default is not None and not isinstance(value, type(default)) else value

    def set_setting(self, key: str, value: str, actor: str | None = None) -> None:
        with self._conn() as conn:
            if key == "policy_notes" and self.get_setting(key) != value:
                self._log(conn, "policy_changed", actor=actor, detail={"rules": len(parse_policy_notes(value))})
            row = None
            if key in APPROVAL_CONTROLS:  # read on this connection: one call, one connection
                row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            before = row["value"] if row else ""
            if key in APPROVAL_CONTROLS and before != value:
                # The second-approval controls: their old and new values are kept, in the audit trail and for
                # ``approval_controls`` (the strictest in force since an invoice came in counts, B-970).
                self._log(conn, "settings_changed", actor=actor,
                          detail={"keys": [key], "changes": {key: {"from": before, "to": value}}})  # fmt: skip
            # One of the chart of accounts' settings: the GL accounts and cost centers follow it (chart.py, B-2091).
            chart_gave = self._chart_setting_changing(conn, key)
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            if chart_gave is not None:
                self._chart_setting_changed(conn, chart_gave, actor)

    def approval_controls(self, currency: str, since: str) -> tuple[float, float | None]:
        """The approval limit and the CAD rate of ``currency`` that decide an invoice that came in at ``since``: the
        strictest of those in force since then (the lowest limit above 0; the highest rate, or none when it had
        none). So a limit turned off, raised, or a rate lowered just before approving can't skip a second approval
        that was due when the invoice came in (B-970); an invoice that came in after the change follows it."""
        currency = (currency or "CAD").strip().upper()
        limits, rates = [self.approval_limit()], [self.fx_rates().get(currency)]
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT detail FROM events WHERE action = 'settings_changed' AND created_at >= ? "
                "AND detail LIKE '%\"changes\"%'",
                (since or "",),
            ).fetchall()
        for row in rows:
            changes = (json.loads(row["detail"] or "{}").get("changes")) or {}
            if "approval_limit" in changes:
                try:
                    limits.append(max(float(changes["approval_limit"].get("from") or 0), 0.0))
                except (TypeError, ValueError):
                    pass
            if "fx_rates" in changes:
                rates.append(parse_fx_rates(str(changes["fx_rates"].get("from") or "")).get(currency))
        above = [limit for limit in limits if limit > 0 and math.isfinite(limit)]
        return (min(above) if above else 0.0), (None if None in rates else max(r for r in rates if r is not None))

    def approval_reason(self, inv: dict[str, Any], coding: dict[str, Any]) -> tuple[str, float, float | None]:
        """Why approving ``coding`` for the invoice ``inv`` needs a second approver ("" when it doesn't), with the
        limit and the CAD rate of its currency that decide it (``approval_controls``). A currency changed from the one
        read (``ai_output``) is held to the limit in both (D-132): a USD invoice approved as CAD can't go under the
        limit, and the reason says so (it starts with ``CURRENCY_CHANGED``)."""
        since = inv.get("created_at") or ""
        currency = str(coding.get("currency") or "CAD").strip().upper()
        limit, rate = self.approval_controls(currency, since)
        reason = self.second_approval_reason(coding, limit, rates={} if rate is None else {currency: rate})
        read = str((inv.get("ai_output") or {}).get("currency") or "CAD").strip().upper()
        if reason or read == currency:
            return reason, limit, rate
        read_rate = self.approval_controls(read, since)[1]
        if self.second_approval_reason({**coding, "currency": read}, limit,
                                       rates={} if read_rate is None else {read: read_rate}):  # fmt: skip
            reason = f"{CURRENCY_CHANGED} {read}, " + (
                "which has no CAD exchange rate set (Settings → Review)" if read_rate is None
                else f"and in {read} it is over the approval limit of {limit:,.2f} CAD"
            )  # fmt: skip
        return reason, limit, rate

    def has_reference(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM gl_accounts").fetchone()[0] > 0

    def reference_data(self) -> ReferenceData:
        """The GL accounts, cost centers, tax setup and policy notes, as the coder and the checks use them: kept and
        shared until one of them, or the tax-rate file, changes (every page and every invoice read asks for it, and the
        rate file was parsed each time). Read it, don't change it."""
        path = _tax.rates_path()
        with self._conn() as conn:
            stamp = (*_file_stamp(path), *conn.execute(_REFERENCE_STAMP).fetchone())
        key = str(self.path.resolve())
        with _REFERENCE_LOCK:
            kept = _REFERENCE.get(key)
            if kept is not None and kept[0] == stamp:
                return kept[1]
        built = dataclasses.replace(self._reference_data(TaxRateTable.load(path)), stamp=_short(stamp))
        with _REFERENCE_LOCK:
            _REFERENCE.pop(key, None)  # (put last: the one used now)
            _REFERENCE[key] = (stamp, built)
            with_pairs = any(kept.pairs is not None for _, kept in _REFERENCE.values())
            while len(_REFERENCE) > (WITH_PAIRS_KEPT if with_pairs else REFERENCE_KEPT):
                _REFERENCE.pop(next(iter(_REFERENCE)))
        return built

    def reference_stamp(self) -> str:
        """A short fingerprint of what ``reference_data`` is built from now: the ``stamp`` of the reference data it
        would give. Checks made with reference data of another stamp were made against lists changed since."""
        with self._conn() as conn:
            return _short((*_file_stamp(_tax.rates_path()), *conn.execute(_REFERENCE_STAMP).fetchone()))

    def checks_stamp(self) -> str:
        """A short fingerprint of everything an invoice's checks read that a person changes in Settings: the GL
        accounts, cost centers, tax setup and rate file, our province and sites, the policy notes, JD Edwards, the
        days to pay, and any change to the vendor master or the POs. A validation stored with another one may say
        what today's checks would not (B-815)."""
        with self._conn() as conn:
            parts = (*_file_stamp(_tax.rates_path()), *conn.execute(_REFERENCE_STAMP).fetchone(),
                     *conn.execute(_CHECKS_STAMP).fetchone())  # fmt: skip
        return _short(parts)

    def checked_stamp(self, validation: dict[str, Any]) -> dict[str, Any]:
        """``validation`` (a report's dict) as it is stored: stamped with what it was checked against
        (``CHECKS_STAMP_KEY``). That is today's ``checks_stamp``, unless the checks say they were made with reference
        data that is no longer the one in force (``reference_stamp``: a reader keeps its own for a whole run of
        files, and a list changed meanwhile): then the stamp names those older lists, never today's, so the queue
        checks the invoice again (B-2094). The stamp was taken when the invoice was saved, whatever it had been
        checked against. A stamp the caller gives is kept."""
        stored = {k: v for k, v in validation.items() if k != REFERENCE_STAMP_KEY}
        used = validation.get(REFERENCE_STAMP_KEY)
        stale = bool(used) and used != self.reference_stamp()
        return {CHECKS_STAMP_KEY: f"lists {used}" if stale else self.checks_stamp(), **stored}

    @staticmethod
    def rates_stamp() -> tuple[str, tuple[int, int] | None]:
        """The tax-rate file in use (the data folder's copy, else the bundled one) with its time and size, as
        ``reference_data`` and ``checks_stamp`` compare it: another once a rate file is copied in, changed or taken
        away (an answer worked out from the rates is kept for it, B-2074)."""
        return _file_stamp(_tax.rates_path())

    def _reference_data(self, rates: TaxRateTable) -> ReferenceData:
        gl = self.list_accounts("gl_accounts")
        if not gl:
            raise ValueError("No GL accounts imported yet: add them on the GL Accounts page")
        cc = self.list_accounts("cost_centers")

        def table(rows: list[dict[str, str]], kind: str, key: str) -> ReferenceTable:
            return ReferenceTable(
                kind, key, [{key: r["code"], "description": r["description"], "category": r["category"]} for r in rows]
            )

        return ReferenceData(
            chart_of_accounts=table(gl, "GL account", "gl_code"),
            cost_centers=table(cc, "cost center", "cost_center") if cc else None,
            tax=TaxSetup(
                rates,
                self.tax_treatments(),
                our_province=self.our_province(),
                site_provinces=self.site_provinces(),
                meals_gl_codes=frozenset(r["code"] for r in gl if r.get("meals")),
            ),
            notes=parse_policy_notes(self.get_setting("policy_notes")),
            pairs=self._account_pairs(),  # None with no chart imported (chart.py, D-209)
        )
