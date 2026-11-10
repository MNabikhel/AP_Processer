"""The chart of accounts as JD Edwards exports it (D-209): the account master's rows (``chart_accounts``: an account
of a business unit a row, the ones that may not be posted to as well, so a check can say why) and the business unit
master's (``business_units``), each replaced by its import (``import_chart``, ``import_business_units``).

The GL accounts and the cost centers every other part reads are worked out from the two at each import
(``_rebuild_account_lists``, ``core/chart.account_lists``): a code keeps its meals tick and a category typed in, a
cost center its manager; what the chart no longer has leaves the lists. Which pairs exist and may be posted to
(``account_pairs``) is built once for a chart and its settings, and kept with the reference data
(``accounts._REFERENCE_STAMP`` reads ``chart_version``, counted here). A database with no chart imported has none:
nothing of this is used.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from ..chart import SETTING_KEYS, VERSION_KEY, AccountPairs, BuRow, ChartRow, ChartSettings, account_lists
from . import dialect
from ._base import _in_list, _now

CHART_COLUMNS = ("bu", "object", "subsidiary", "description", "lod", "posting_edit", "category")
BU_COLUMNS = ("code", "name", "company", "type", "status")
NO_ACCOUNT = ("No account of the chart may be posted to on a business unit that is active, so there would be no GL "
              "account to code to: nothing was changed.")  # fmt: skip


def _chart(conn: sqlite3.Connection) -> list[ChartRow]:
    """The chart's rows, by business unit, object and subsidiary."""
    return [ChartRow(*r) for r in conn.execute(
        f"SELECT {', '.join(CHART_COLUMNS)} FROM chart_accounts ORDER BY bu, object, subsidiary")]  # fmt: skip


def _units(conn: sqlite3.Connection) -> list[BuRow]:
    return [BuRow(*r) for r in conn.execute(f"SELECT {', '.join(BU_COLUMNS)} FROM business_units ORDER BY code")]


def _settings(conn: sqlite3.Connection) -> ChartSettings:
    rows = conn.execute(f"SELECT key, value FROM settings WHERE {_in_list('key')}", (json.dumps(SETTING_KEYS),))
    return ChartSettings.read({r["key"]: r["value"] for r in rows})


def _changes(before: dict[Any, Any], after: dict[Any, Any]) -> dict[str, int]:
    """How a table's rows, by key, differ once replaced: added, changed and removed."""
    return {
        "added": sum(1 for key in after if key not in before),
        "updated": sum(1 for key, row in after.items() if key in before and before[key] != row),
        "removed": sum(1 for key in before if key not in after),
    }


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

    def save_chart_settings(self, settings: ChartSettings, actor: str | None = None) -> list[str]:
        """Save the chart's settings (plain ``settings`` rows, ``chart.SETTING_KEYS``); returns the keys that
        changed. The pairs and the checks follow at once: the reference data is kept by these too."""
        with self._conn() as conn:
            before = _settings(conn).saved()
            changed = [key for key, value in settings.saved().items() if before[key] != value]
            for key in changed:
                conn.execute(dialect.upsert("settings", ("key", "value"), ("key",)), (key, settings.saved()[key]))
            if changed:
                self._log(conn, "settings_changed", actor=actor, detail={"keys": changed})
        return changed

    def chart_version(self) -> int:
        """How many times the chart or the business units changed by an import (0: never imported)."""
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (VERSION_KEY,)).fetchone()
        return int(row["value"]) if row else 0

    def import_chart(self, rows: Iterable[ChartRow], actor: str | None = None) -> dict[str, Any]:
        """Replace the chart with ``rows`` (``core/chart.chart_rows``) and work the GL accounts and cost centers out
        again. Returns {"rows", "added", "updated", "removed", "gl_accounts", "cost_centers"} (the two lists: {"now",
        "added", "updated", "removed"}). The same rows again change nothing and record nothing. ``ValueError``,
        nothing changed, for rows with no account at all (the wrong file) or none that may be posted to."""
        after = {(r.bu, r.object, r.subsidiary): r for r in rows}
        if not any(r.object for r in after.values()):
            raise ValueError("No row of this file has an object account, so it is not a chart of accounts: nothing "
                             "was changed.")  # fmt: skip
        with self._conn() as conn:
            dialect.begin_write(conn)
            old = _chart(conn)
            result = _changes({(r.bu, r.object, r.subsidiary): r for r in old}, after)
            result["rows"] = len(after)
            if result["added"] or result["updated"] or result["removed"]:
                was = account_lists(old, _units(conn), _settings(conn))[0]
                conn.execute("DELETE FROM chart_accounts")
                conn.executemany(
                    f"INSERT INTO chart_accounts ({', '.join(CHART_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(r.bu, r.object, r.subsidiary, r.description, r.lod, r.posting_edit, r.category)
                     for r in after.values()],
                )  # fmt: skip
                result.update(self._rebuild_account_lists(conn, was))
                self._chart_changed(conn, "chart_accounts", result, actor)
            else:
                result.update(self._list_sizes(conn))
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
            if result["added"] or result["updated"] or result["removed"]:
                chart = _chart(conn)
                was = account_lists(chart, old, _settings(conn))[0]
                conn.execute("DELETE FROM business_units")
                conn.executemany(
                    f"INSERT INTO business_units ({', '.join(BU_COLUMNS)}) VALUES (?, ?, ?, ?, ?)",
                    [(r.code, r.name, r.company, r.type, r.status) for r in after.values()],
                )
                result.update(self._rebuild_account_lists(conn, was) if chart else self._list_sizes(conn))
                self._chart_changed(conn, "business_units", result, actor)
            else:
                result.update(self._list_sizes(conn))
        return result

    def _chart_changed(self, conn: sqlite3.Connection, table: str, result: dict[str, Any], actor: str | None) -> None:
        """One more change to the chart (``chart_version``: the reference data and the checks are worked out again),
        and its event."""
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
            (VERSION_KEY,),
        )
        self._log(conn, "accounts_imported", actor=actor, detail={"table": table, **result, "replace_all": True})

    @staticmethod
    def _list_sizes(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
        """The two lists left as they are: how many each holds."""
        return {table: {"now": conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], "added": 0, "updated": 0,
                        "removed": 0} for table in ("gl_accounts", "cost_centers")}  # fmt: skip

    def _rebuild_account_lists(self, conn: sqlite3.Connection,
                               was: dict[str, tuple[str, str]]) -> dict[str, dict[str, int]]:  # fmt: skip
        """The GL accounts and the cost centers made what the chart and the business units now give
        (``chart.account_lists``), in this transaction. A code already listed keeps its meals tick, and its category
        when a person typed one (``was``: the GL accounts the chart gave before, so a category that came from the
        chart follows it); a cost center keeps its category and its manager. What the chart no longer gives is
        removed. ``ValueError`` when no GL account would be left."""
        accounts, centers = account_lists(_chart(conn), _units(conn), _settings(conn))
        if not accounts:
            raise ValueError(NO_ACCOUNT)
        now = _now()
        counts = {name: {"now": len(new), "added": 0, "updated": 0, "removed": 0}
                  for name, new in (("gl_accounts", accounts), ("cost_centers", centers))}  # fmt: skip
        listed = {r["code"]: r for r in conn.execute("SELECT code, description, category FROM gl_accounts")}
        for code, (description, category) in accounts.items():
            old = listed.get(code)
            if old is None:
                conn.execute("INSERT INTO gl_accounts (code, description, category, created_at) VALUES (?, ?, ?, ?)",
                             (code, description, category, now))  # fmt: skip
                counts["gl_accounts"]["added"] += 1
                continue
            if old["category"] not in ("", was.get(code, ("", ""))[1]):
                category = old["category"]  # typed in by a person: kept
            if (old["description"], old["category"]) != (description, category):
                conn.execute("UPDATE gl_accounts SET description = ?, category = ? WHERE code = ?",
                             (description, category, code))  # fmt: skip
                counts["gl_accounts"]["updated"] += 1
        held = {r["code"]: r["description"] for r in conn.execute("SELECT code, description FROM cost_centers")}
        for code, name in centers.items():
            if code not in held:
                conn.execute("INSERT INTO cost_centers (code, description, created_at) VALUES (?, ?, ?)",
                             (code, name, now))  # fmt: skip
                counts["cost_centers"]["added"] += 1
            elif held[code] != name:
                conn.execute("UPDATE cost_centers SET description = ? WHERE code = ?", (name, code))
                counts["cost_centers"]["updated"] += 1
        for table, old_codes, new in (("gl_accounts", listed, accounts), ("cost_centers", held, centers)):
            gone = [code for code in old_codes if code not in new]
            conn.execute(f"DELETE FROM {table} WHERE {_in_list('code')}", (json.dumps(gone),))
            counts[table]["removed"] = len(gone)
        return counts

    def _account_pairs(self) -> AccountPairs | None:
        """Which pairs exist, built from the tables (None with no chart imported)."""
        with self._conn() as conn:
            if not conn.execute("SELECT EXISTS (SELECT 1 FROM chart_accounts)").fetchone()[0]:
                return None
            return AccountPairs(_chart(conn), _units(conn), _settings(conn))

    def account_pairs(self) -> AccountPairs | None:
        """Which GL code exists on which business unit and may be posted to (``core/chart.AccountPairs``), or None
        when no chart was imported. Built once for a chart and its settings: it is the reference data's ``pairs``,
        kept with it."""
        with self._conn() as conn:
            if not conn.execute("SELECT EXISTS (SELECT 1 FROM chart_accounts)").fetchone()[0]:
                return None
        try:
            return self.reference_data().pairs
        except ValueError:  # every GL account deleted by hand since: no reference data to keep it with
            return self._account_pairs()
