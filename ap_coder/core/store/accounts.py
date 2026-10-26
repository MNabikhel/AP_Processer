"""GL accounts, cost centers, the audit trail, our bank accounts, tax setup."""

from __future__ import annotations

import contextvars
import hashlib
import json
import math
import sqlite3
import threading
from typing import Any

from ...coding import tax as _tax
from ...coding.tax import DEFAULT_TREATMENTS, TAX_TYPES, TREATMENTS, TaxRateTable, TaxSetup, TaxTreatment
from ..reference_data import ReferenceData, ReferenceTable, marks_meals, parse_policy_notes
from ._base import (
    ACCOUNT_TABLES,
    APPROVAL_CONTROLS,
    EVENT_ORIGIN,
    _clean_code,
    _in_list,
    _now,
    parse_fx_rates,
)

# The reference data as last built, per database: (what it was built from, it) (``Store.reference_data``).
_REFERENCE: dict[str, tuple[tuple[Any, ...], ReferenceData]] = {}
_REFERENCE_LOCK = threading.Lock()
# Everything ``reference_data`` is built from, in one query: compared, it says whether the one kept still holds.
_REFERENCE_STAMP = """SELECT
    (SELECT group_concat(code || '|' || description || '|' || category || '|' || meals, char(10))
     FROM (SELECT * FROM gl_accounts ORDER BY code)),
    (SELECT group_concat(code || '|' || description || '|' || category, char(10))
     FROM (SELECT * FROM cost_centers ORDER BY code)),
    (SELECT group_concat(tax_type || '|' || treatment || '|' || gl_code, char(10))
     FROM (SELECT * FROM tax_treatments ORDER BY tax_type)),
    (SELECT group_concat(key || '=' || value, char(10))
     FROM (SELECT * FROM settings WHERE key IN ('our_province', 'site_provinces', 'policy_notes') ORDER BY key))"""

# What the checks read besides the reference data, as the queue's re-check compares them (``Store.checks_stamp``,
# B-815): JD Edwards' settings, whether it is in use (its switch, or a batch made), the days to pay, and the count of
# changes to the vendor master and the POs (``masters_version``, B-842), and our bank accounts (the account an invoice
# is paid from, D-66, B-958).
_CHECKS_STAMP = """SELECT
    (SELECT group_concat(key || '=' || value, char(10))
     FROM (SELECT * FROM settings WHERE key IN ('jde_e1', 'show_jde_settings', 'default_terms_days', 'masters_version')
           ORDER BY key)),
    (SELECT COUNT(*) FROM export_batches WHERE format = 'jde'),
    (SELECT group_concat(code || '|' || currency || '|' || gl_account || '|' || is_default || '|' || active, char(10))
     FROM (SELECT * FROM bank_accounts ORDER BY code))"""

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


# Who is signed in on the company server (set by web/identity.py): an event recorded without an actor (an upload, the
# demo, a setup import) records them (B-1106). None on a laptop, whose events are as before.
EVENT_ACTOR: contextvars.ContextVar[str | None] = contextvars.ContextVar("ap_coder_event_actor", default=None)


class AccountsMixin:
    # --- GL accounts / cost centers ----------------------------------------------------------

    # --- Audit trail ---------------------------------------------------------------------------------

    @staticmethod
    def _log(
        conn: sqlite3.Connection, action: str, invoice_id: int | None = None, actor: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:  # fmt: skip
        origin = EVENT_ORIGIN.get()
        actor = actor if actor else EVENT_ACTOR.get()  # the person signed in on the server (B-1106)
        if origin:
            detail = {**(detail or {}), "address": origin}  # never "from": events use it (B-129)
        conn.execute(
            "INSERT INTO events (invoice_id, action, actor, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (invoice_id, action, actor, json.dumps(detail or {}, default=str), _now()),
        )
        if action in MASTERS_CHANGES:
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
        sql = (  # with the invoice's vendor and number, so a list of events says which invoice each is about
            "SELECT e.*, i.vendor_name AS invoice_vendor, i.invoice_number AS invoice_number FROM events e "
            "LEFT JOIN invoices i ON i.id = e.invoice_id WHERE 1 = 1"
        )
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
        from ...coding.banks import COLUMNS

        before = {r["code"]: r for r in self.bank_accounts()}
        added = updated = 0
        now = _now()
        upsert = (
            f"INSERT INTO bank_accounts ({', '.join(COLUMNS)}, updated_at) VALUES ({', '.join('?' * len(COLUMNS))}, ?) "
            "ON CONFLICT(code) DO UPDATE SET "
            + ", ".join(f"{c} = excluded.{c}" for c in COLUMNS[1:])
            + ", updated_at = excluded.updated_at"
        )
        with self._conn() as conn:
            for row in rows:
                values = [row[c] if c not in ("is_default", "active") else int(bool(row[c])) for c in COLUMNS]
                if row["code"] in before:
                    old = before[row["code"]]
                    if all(old[c] == row[c] for c in COLUMNS):
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

    def approvals_stamp(self) -> tuple[Any, ...]:
        """(approvals, the latest) of invoices approved or waiting for a second approval: changes when one is, so what
        depends on past approvals (a vendor's last Pay from account) is worked out again (the queue, B-690)."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            row = conn.execute(
                "SELECT COUNT(*), MAX(reviewed_at), MAX(id) FROM invoice_summary "
                "WHERE status IN ('approved', 'pending_approval')"
            ).fetchone()
        return tuple(row)

    def last_pay_from(self, key: str) -> str:
        """The bank account this vendor's last approved invoice was paid from ("" when none recorded)."""
        if not key:
            return ""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT json_extract(final_output, '$.pay_from_account') AS code FROM invoices WHERE vendor_key = ? "
                "AND status IN ('approved', 'pending_approval') AND json_valid(final_output) "
                "AND COALESCE(json_extract(final_output, '$.pay_from_account'), '') != '' "
                "ORDER BY reviewed_at DESC, id DESC LIMIT 1",
                (key,),
            ).fetchone()
        return str(row["code"]) if row else ""

    # --- Tax setup and policy ---------------------------------------------------------------------

    def tax_treatments(self) -> dict[str, TaxTreatment]:
        with self._conn() as conn:
            stored = {r["tax_type"]: r for r in conn.execute("SELECT * FROM tax_treatments")}
        result = {}
        for tax_type in TAX_TYPES:
            r = stored.get(tax_type)
            result[tax_type] = (
                TaxTreatment(tax_type, r["treatment"], r["gl_code"])
                if r
                else TaxTreatment(tax_type, DEFAULT_TREATMENTS[tax_type])
            )
        return result

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
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

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

    def has_reference(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM gl_accounts").fetchone()[0] > 0

    def reference_data(self) -> ReferenceData:
        """The GL accounts, cost centers, tax setup and policy notes, as the coder and the checks use them: kept and
        shared until one of them, or the tax-rate file, changes (every page and every invoice read asks for it, and the
        rate file was parsed each time). Read it, don't change it."""
        path = _tax.rates_path()
        try:
            seen = path.stat()
            mtime: tuple[int, int] | None = (seen.st_mtime_ns, seen.st_size)
        except OSError:
            mtime = None
        with self._conn() as conn:
            stamp = (str(path), mtime, *conn.execute(_REFERENCE_STAMP).fetchone())
        key = str(self.path.resolve())
        with _REFERENCE_LOCK:
            kept = _REFERENCE.get(key)
            if kept is not None and kept[0] == stamp:
                return kept[1]
        built = self._reference_data(TaxRateTable.load(path))
        with _REFERENCE_LOCK:
            _REFERENCE[key] = (stamp, built)
            while len(_REFERENCE) > 8:
                _REFERENCE.pop(next(iter(_REFERENCE)))
        return built

    def checks_stamp(self) -> str:
        """A short fingerprint of everything an invoice's checks read that a person changes in Settings: the GL
        accounts, cost centers, tax setup and rate file, our province and sites, the policy notes, JD Edwards, the
        days to pay, and any change to the vendor master or the POs. A validation stored with another one may say
        what today's checks would not (B-815)."""
        path = _tax.rates_path()
        try:
            seen = path.stat()
            mtime: tuple[int, int] | None = (seen.st_mtime_ns, seen.st_size)
        except OSError:
            mtime = None
        with self._conn() as conn:
            parts = (str(path), mtime, *conn.execute(_REFERENCE_STAMP).fetchone(),
                     *conn.execute(_CHECKS_STAMP).fetchone())  # fmt: skip
        return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()[:20]

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
        )
