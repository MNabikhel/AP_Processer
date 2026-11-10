"""The chart of accounts as JD Edwards exports it (D-209): the account master's rows (``chart_accounts``: an account
of a business unit a row, the ones that may not be posted to as well, so a check can say why) and the business unit
master's (``business_units``), each replaced by its import (``import_chart``, ``import_business_units``).

The GL accounts and the cost centers every other part reads are what the chart's rows, the business units and the
chart's settings give (``core/chart.account_lists``), nothing else: they are worked out again whenever one of the
three changes (an import, a setting saved) and at every import, even of the same file, so a list changed by hand or
by another import is put right (``_rebuild_account_lists``; on demand: ``rebuild_account_lists``; B-2091). A code
keeps its meals tick and a category typed in, a cost center its manager; what a person typed on a code that leaves
the lists is set aside (``chart_set_aside``) and put back when the code is in the chart again (B-2090).

Which pairs exist and may be posted to (``account_pairs``) is built once for a chart and its settings, and kept with
the reference data: ``accounts._REFERENCE_STAMP`` reads the settings, ``chart_version`` (the count of changes) and
``chart_hash`` (what the two tables hold, written with them: another chart never passes for the one kept, B-2125).
A database with no chart imported has none: nothing of this is used.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from ..chart import SETTING_KEYS, VERSION_KEY, AccountPairs, BuRow, ChartRow, ChartSettings, account_lists
from . import dialect
from ._base import _in_list, _now

CHART_COLUMNS = ("bu", "object", "subsidiary", "description", "lod", "posting_edit", "category")
BU_COLUMNS = ("code", "name", "company", "type", "status")
HASH_KEY = "chart_hash"  # what the two tables hold (settings: read by the reference data's stamp)
ASIDE_KEY = "chart_set_aside"  # what a person typed on the codes that left the lists (settings, JSON)
NAMED = 50  # the codes an import's result and its event name of each kind (the counts say them all)
NO_ACCOUNT = ("No account of the chart may be posted to on a business unit that is active, so there would be no GL "
              "account to code to: nothing was changed.")  # fmt: skip
NO_CHART = "No chart of accounts is imported yet (python -m ap_coder chart import --chart <file>)."
LISTS = ("gl_accounts", "cost_centers")
KINDS = ("meals", "categories", "managers")  # what is set aside and put back, as a result names it


def _chart(conn: sqlite3.Connection) -> list[ChartRow]:
    """The chart's rows, by business unit, object and subsidiary."""
    return [ChartRow(*r) for r in conn.execute(
        f"SELECT {', '.join(CHART_COLUMNS)} FROM chart_accounts ORDER BY bu, object, subsidiary")]  # fmt: skip


def _units(conn: sqlite3.Connection) -> list[BuRow]:
    return [BuRow(*r) for r in conn.execute(f"SELECT {', '.join(BU_COLUMNS)} FROM business_units ORDER BY code")]


def _settings(conn: sqlite3.Connection) -> ChartSettings:
    rows = conn.execute(f"SELECT key, value FROM settings WHERE {_in_list('key')}", (json.dumps(SETTING_KEYS),))
    return ChartSettings.read({r["key"]: r["value"] for r in rows})


def _put(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(dialect.upsert("settings", ("key", "value"), ("key",)), (key, value))


def _aside(conn: sqlite3.Connection) -> dict[str, dict[str, dict[str, Any]]]:
    """What was set aside: {"gl_accounts": {code: {"meals", "category"}}, "cost_centers": {code: {"category",
    "manager_name", "manager_email"}}}, each with only what the person had set."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (ASIDE_KEY,)).fetchone()
    try:
        kept = json.loads(row["value"]) if row else {}
    except ValueError:
        kept = {}
    return {table: dict(kept.get(table) or {}) if isinstance(kept, dict) else {} for table in LISTS}


def _changes(before: dict[Any, Any], after: dict[Any, Any]) -> dict[str, Any]:
    """How a table's rows, by key, differ once replaced: added, changed and removed."""
    return {
        "added": sum(1 for key in after if key not in before),
        "updated": sum(1 for key, row in after.items() if key in before and before[key] != row),
        "removed": sum(1 for key in before if key not in after),
    }


def _moved(result: dict[str, Any]) -> bool:
    """Whether an import or a rebuild changed a table or a list."""
    return bool(result.get("added") or result.get("updated") or result.get("removed")
                or any(result[table][what] for table in LISTS for what in ("added", "updated", "removed")))  # fmt: skip


class _Lists:
    """One rebuild of the two lists: what it counts, and what it sets aside and puts back."""

    def __init__(self, conn: sqlite3.Connection, aside: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.conn, self.aside, self.now = conn, aside, _now()
        self.counts = {table: {"now": 0, "added": 0, "updated": 0, "removed": 0} for table in LISTS}
        self.left: dict[str, list[str]] = {table: [] for table in LISTS}
        self.set_aside: dict[str, list[str]] = {kind: [] for kind in KINDS}
        self.brought_back: dict[str, list[str]] = {kind: [] for kind in KINDS}

    def note(self, into: dict[str, list[str]], code: str, typed: dict[str, Any]) -> None:
        for kind, has in (("meals", typed.get("meals")), ("categories", typed.get("category")),
                          ("managers", typed.get("manager_name") or typed.get("manager_email"))):  # fmt: skip
            if has:
                into[kind].append(code)

    def accounts(self, accounts: dict[str, tuple[str, str]], was: dict[str, tuple[str, str]]) -> None:
        """The GL accounts made ``accounts``: a code listed keeps its meals tick and a category a person typed (one
        that is not what the chart gave before, ``was``); a code that leaves has them set aside; a new code gets
        back what was set aside for it."""
        listed = {r["code"]: r for r in self.conn.execute("SELECT code, description, category, meals FROM gl_accounts")}
        kept = self.aside["gl_accounts"]
        for code, (description, category) in accounts.items():
            old = listed.get(code)
            if old is None:
                typed = kept.pop(code, {})
                self.note(self.brought_back, code, typed)
                self.conn.execute(
                    "INSERT INTO gl_accounts (code, description, category, created_at, meals) VALUES (?, ?, ?, ?, ?)",
                    (code, description, typed.get("category") or category, self.now, int(bool(typed.get("meals")))),
                )
                self.counts["gl_accounts"]["added"] += 1
                continue
            if old["category"] not in ("", was.get(code, ("", ""))[1]):
                category = old["category"]  # typed in by a person: kept
            if (old["description"], old["category"]) != (description, category):
                self.conn.execute("UPDATE gl_accounts SET description = ?, category = ? WHERE code = ?",
                                  (description, category, code))  # fmt: skip
                self.counts["gl_accounts"]["updated"] += 1
        for code, old in listed.items():
            if code not in accounts:
                typed = {
                    "meals": int(bool(old["meals"])),
                    "category": "" if old["category"] in ("", was.get(code, ("", ""))[1]) else old["category"],
                }
                self.leave("gl_accounts", code, {k: v for k, v in typed.items() if v})
        self.end("gl_accounts", accounts)

    def centers(self, centers: dict[str, str]) -> None:
        """The cost centers made ``centers``: one listed keeps its category and its manager; one that leaves has
        them set aside; a new one gets back what was set aside for it."""
        held = {r["code"]: r for r in self.conn.execute(
            "SELECT code, description, category, manager_name, manager_email FROM cost_centers")}  # fmt: skip
        kept = self.aside["cost_centers"]
        for code, name in centers.items():
            if code not in held:
                typed = kept.pop(code, {})
                self.note(self.brought_back, code, typed)
                self.conn.execute(
                    "INSERT INTO cost_centers (code, description, category, created_at, manager_name, manager_email) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (code, name, typed.get("category", ""), self.now, typed.get("manager_name", ""),
                     typed.get("manager_email", "")),
                )  # fmt: skip
                self.counts["cost_centers"]["added"] += 1
            elif held[code]["description"] != name:
                self.conn.execute("UPDATE cost_centers SET description = ? WHERE code = ?", (name, code))
                self.counts["cost_centers"]["updated"] += 1
        for code, old in held.items():
            if code not in centers:
                typed = {key: old[key] for key in ("category", "manager_name", "manager_email") if old[key]}
                self.leave("cost_centers", code, typed)
        self.end("cost_centers", centers)

    def leave(self, table: str, code: str, typed: dict[str, Any]) -> None:
        self.left[table].append(code)
        if typed:
            self.aside[table][code] = typed
            self.note(self.set_aside, code, typed)

    def end(self, table: str, new: dict[str, Any]) -> None:
        self.conn.execute(f"DELETE FROM {table} WHERE {_in_list('code')}", (json.dumps(self.left[table]),))
        self.counts[table]["now"], self.counts[table]["removed"] = len(new), len(self.left[table])

    def result(self) -> dict[str, Any]:
        """What the rebuild did, as an import's result and its event say it (``NAMED`` codes of each kind)."""
        return {**self.counts, "left": {table: sorted(codes)[:NAMED] for table, codes in self.left.items()},
                "set_aside": {kind: codes[:NAMED] for kind, codes in self.set_aside.items()},
                "brought_back": {kind: codes[:NAMED] for kind, codes in self.brought_back.items()}}  # fmt: skip


class ChartMixin:
    def chart_accounts(self) -> list[ChartRow]:
        """Every row of the chart as imported (``core/chart.ChartRow``; a business unit's header row has no
        object)."""
        with self._conn() as conn:
            return _chart(conn)

    def business_units(self) -> list[BuRow]:
        """Every business unit of the business unit master as imported."""
        with self._conn() as conn:
            return _units(conn)

    def chart_settings(self) -> ChartSettings:
        """How the chart is used (``core/chart.ChartSettings``): the defaults for what is not saved."""
        with self._conn() as conn:
            return _settings(conn)

    def chart_set_aside(self) -> dict[str, dict[str, dict[str, Any]]]:
        """What a person typed on the GL accounts and cost centers that left the lists, kept for when they are in the
        chart again: {"gl_accounts": {code: {"meals", "category"}}, "cost_centers": {code: {"category",
        "manager_name", "manager_email"}}}."""
        with self._conn() as conn:
            return _aside(conn)

    def has_chart(self) -> bool:
        """Whether a chart of accounts is imported: the GL accounts and cost centers are then worked out from it."""
        with self._conn() as conn:
            return bool(conn.execute("SELECT EXISTS (SELECT 1 FROM chart_accounts)").fetchone()[0])

    def save_chart_settings(self, settings: ChartSettings, actor: str | None = None) -> list[str]:
        """Save the chart's settings (plain ``settings`` rows, ``chart.SETTING_KEYS``); returns the keys that
        changed. The two lists, the pairs and the checks follow at once (``set_setting``). ``ValueError``, nothing
        changed, when the settings would leave no account to code to."""
        with self._conn() as conn:
            dialect.begin_write(conn)
            before = _settings(conn).saved()
            changed = [key for key, value in settings.saved().items() if before[key] != value]
            was = self._chart_setting_changing(conn, SETTING_KEYS[0])
            for key in changed:
                _put(conn, key, settings.saved()[key])
            if changed:
                self._log(conn, "settings_changed", actor=actor, detail={"keys": changed})
            if changed and was is not None:
                self._chart_setting_changed(conn, was, actor)
        return changed

    def chart_version(self) -> int:
        """How many times the chart, the business units or the lists they give changed (0: never imported)."""
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (VERSION_KEY,)).fetchone()
        return int(row["value"]) if row else 0

    def import_chart(self, rows: Iterable[ChartRow], actor: str | None = None) -> dict[str, Any]:
        """Replace the chart with ``rows`` (``core/chart.chart_rows``) and work the GL accounts and cost centers out
        again, even when the rows are those already imported. Returns {"rows", "added", "updated", "removed"} of the
        chart and what the lists' rebuild did (``_rebuild_account_lists``). With nothing to change, nothing is
        written or recorded. ``ValueError``, nothing changed, for rows with no account at all (the wrong file) or
        none that may be posted to."""
        after = {(r.bu, r.object, r.subsidiary): r for r in rows}
        if not any(r.object for r in after.values()):
            raise ValueError("No row of this file has an object account, so it is not a chart of accounts: nothing "
                             "was changed.")  # fmt: skip
        with self._conn() as conn:
            dialect.begin_write(conn)
            old = _chart(conn)
            result = _changes({(r.bu, r.object, r.subsidiary): r for r in old}, after)
            result["rows"] = len(after)
            was = account_lists(old, _units(conn), _settings(conn))[0]
            if result["added"] or result["updated"] or result["removed"]:
                conn.execute("DELETE FROM chart_accounts")
                conn.executemany(
                    f"INSERT INTO chart_accounts ({', '.join(CHART_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(r.bu, r.object, r.subsidiary, r.description, r.lod, r.posting_edit, r.category)
                     for r in after.values()],
                )  # fmt: skip
            result.update(self._rebuild_account_lists(conn, was))
            self._chart_changed(conn, "chart_accounts", result, actor)
        return result

    def import_business_units(self, rows: Iterable[BuRow], actor: str | None = None) -> dict[str, Any]:
        """Replace the business units with ``rows`` (``core/chart.bu_rows``); with a chart imported, the GL accounts
        and cost centers are worked out again (a business unit closed since leaves them). Returns what
        ``import_chart`` does. ``ValueError``, nothing changed, for no row at all, or when no account would be left
        to code to."""
        after = {r.code: r for r in rows}
        if not after:
            raise ValueError("This file has no business unit: nothing was changed.")
        with self._conn() as conn:
            dialect.begin_write(conn)
            old = _units(conn)
            result = _changes({r.code: r for r in old}, after)
            result["rows"] = len(after)
            was = account_lists(_chart(conn), old, _settings(conn))[0]
            if result["added"] or result["updated"] or result["removed"]:
                conn.execute("DELETE FROM business_units")
                conn.executemany(
                    f"INSERT INTO business_units ({', '.join(BU_COLUMNS)}) VALUES (?, ?, ?, ?, ?)",
                    [(r.code, r.name, r.company, r.type, r.status) for r in after.values()],
                )
            result.update(self._rebuild_account_lists(conn, was))
            self._chart_changed(conn, "business_units", result, actor)
        return result

    def rebuild_account_lists(self, actor: str | None = None) -> dict[str, Any]:
        """Work the GL accounts and the cost centers out again from the chart, the business units and the chart's
        settings as they are: the repair for lists changed by hand or by another import since. Returns what the
        rebuild did (``_rebuild_account_lists``); recorded when it changed something. ``ValueError`` with no chart
        imported."""
        with self._conn() as conn:
            dialect.begin_write(conn)
            rows = _chart(conn)
            if not rows:
                raise ValueError(NO_CHART)
            result = self._rebuild_account_lists(conn, account_lists(rows, _units(conn), _settings(conn))[0])
            self._chart_changed(conn, "chart_accounts", {"rows": len(rows), **result}, actor)
        return result

    def _chart_setting_changing(self, conn: sqlite3.Connection, key: str) -> dict[str, tuple[str, str]] | None:
        """Before a setting is saved: the GL accounts the chart gives now, when ``key`` is one of the chart's
        settings and a chart is imported (else None: nothing follows the setting)."""
        if key not in SETTING_KEYS:
            return None
        rows = _chart(conn)
        return account_lists(rows, _units(conn), _settings(conn))[0] if rows else None

    def _chart_setting_changed(self, conn: sqlite3.Connection, was: dict[str, tuple[str, str]],
                               actor: str | None) -> None:  # fmt: skip
        """After one of the chart's settings is saved (``Store.set_setting``): the lists follow it, in the same
        transaction. ``ValueError`` (the setting is not saved) when no account would be left."""
        result = self._rebuild_account_lists(conn, was)
        self._chart_changed(conn, "chart_accounts", {"rows": len(_chart(conn)), **result}, actor)

    def _chart_changed(self, conn: sqlite3.Connection, table: str, result: dict[str, Any], actor: str | None) -> None:
        """When an import or a rebuild changed a table or a list: one more change to the chart (``chart_version``),
        what the tables now hold (``chart_hash``), so the reference data and the checks are worked out again, and
        its event with everything the result says."""
        if not _moved(result):
            return
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
            (VERSION_KEY,),
        )
        held = hashlib.sha256(repr((_chart(conn), _units(conn))).encode("utf-8")).hexdigest()[:20]
        _put(conn, HASH_KEY, held)
        self._log(conn, "accounts_imported", actor=actor, detail={"table": table, **result, "replace_all": True})

    def _rebuild_account_lists(self, conn: sqlite3.Connection, was: dict[str, tuple[str, str]]) -> dict[str, Any]:
        """The GL accounts and the cost centers made what the chart, the business units and the chart's settings
        give (``chart.account_lists``), in this transaction; with no chart imported they are left as they are.
        ``was``: the GL accounts the chart gave before, so a category that came from the chart follows it and one a
        person typed stays. Returns {"gl_accounts", "cost_centers"} ({"now", "added", "updated", "removed"}),
        "left" (the codes that left each list), "set_aside" and "brought_back" (the codes whose meals tick,
        category or manager was kept aside or put back), "unmapped" (what else points at a GL account no longer
        listed). ``ValueError`` when no GL account would be left."""
        rows = _chart(conn)
        lists = _Lists(conn, _aside(conn))
        if not rows:
            for table in LISTS:
                lists.counts[table]["now"] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            return {**lists.result(), "unmapped": []}
        accounts, centers = account_lists(rows, _units(conn), _settings(conn))
        if not accounts:
            raise ValueError(NO_ACCOUNT)
        before = json.dumps(lists.aside, sort_keys=True)
        lists.accounts(accounts, was)
        lists.centers(centers)
        if json.dumps(lists.aside, sort_keys=True) != before:
            _put(conn, ASIDE_KEY, json.dumps(lists.aside, sort_keys=True))
        return {**lists.result(), "unmapped": self._unmapped(conn)}

    @staticmethod
    def _unmapped(conn: sqlite3.Connection) -> list[str]:
        """What points at a GL account that is not listed: a tax type's account, a bank account's, in words."""
        taxes = conn.execute("SELECT tax_type, gl_code FROM tax_treatments WHERE gl_code != '' AND gl_code NOT IN "
                             "(SELECT code FROM gl_accounts) ORDER BY tax_type")  # fmt: skip
        said = [f"the tax set-up posts {r['tax_type']} to GL account {r['gl_code']}" for r in taxes]
        banks = conn.execute("SELECT code, gl_account FROM bank_accounts WHERE gl_account != '' AND gl_account NOT IN "
                             "(SELECT code FROM gl_accounts) ORDER BY code")  # fmt: skip
        return said + [f"the bank account {r['code']} is on GL account {r['gl_account']}" for r in banks]

    def _account_pairs(self) -> AccountPairs | None:
        """Which pairs exist, built from the tables (None with no chart imported)."""
        with self._conn() as conn:
            rows = _chart(conn)
            return AccountPairs(rows, _units(conn), _settings(conn)) if rows else None

    def account_pairs(self) -> AccountPairs | None:
        """Which GL code exists on which business unit and may be posted to (``core/chart.AccountPairs``), or None
        when no chart was imported. Built once for a chart and its settings: it is the reference data's ``pairs``,
        kept with it."""
        if not self.has_chart():
            return None
        try:
            return self.reference_data().pairs
        except ValueError:  # every GL account deleted by hand since: no reference data to keep it with
            return self._account_pairs()
