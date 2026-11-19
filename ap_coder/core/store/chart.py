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
``chart_hash`` (a random mark written with each change: another chart never passes for the one kept, B-2125, D-272).
A database with no chart imported has none: nothing of this is used.
"""

from __future__ import annotations

import itertools
import json
import secrets
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..chart import (
    ACTIVE,
    COMPANIES_KEY,
    CONTROL_KEYS,
    EXAMPLES,
    SETTING_KEYS,
    VERSION_KEY,
    WITHOUT_KEY,
    BuRow,
    ChartRow,
    ChartSettings,
    code_of,
)
from ..keys import company_code
from . import dialect
from ._base import _in_list, _now
from .chart_index import IndexedPairs

CHART_COLUMNS = ("bu", "object", "subsidiary", "description", "lod", "posting_edit", "category", "company")
BU_COLUMNS = ("code", "name", "company", "type", "status")
STAGED_COLUMNS = (*CHART_COLUMNS, "line")  # a row staged (``chart_load``): its row of the file too (D-272)
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


def _count(conn: sqlite3.Connection) -> int:
    """The chart's rows (0: no chart imported)."""
    return int(conn.execute("SELECT COUNT(*) FROM chart_accounts").fetchone()[0])


def _units(conn: sqlite3.Connection) -> list[BuRow]:
    return [BuRow(*r) for r in conn.execute(f"SELECT {', '.join(BU_COLUMNS)} FROM business_units ORDER BY code")]


def _settings(conn: sqlite3.Connection) -> ChartSettings:
    rows = conn.execute(f"SELECT key, value FROM settings WHERE {_in_list('key')}",
                        (json.dumps([*SETTING_KEYS, *CONTROL_KEYS, COMPANIES_KEY]),))  # fmt: skip
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


def _lists(conn: sqlite3.Connection, settings: ChartSettings | None = None) -> tuple[
        dict[str, tuple[str, str]], dict[str, str]]:  # fmt: skip
    """``chart.account_lists`` of the tables as they are, worked out by SQLite (D-267: a chart of millions of accounts
    is never read whole): the GL accounts {code: (its commonest description, its commonest title)} and the cost centers
    {business unit: name} of the accounts that may be posted to on business units that are not closed; of two texts
    as common, the first met in the chart's order (the lower business unit)."""
    settings = settings or _settings(conn)
    where = (f"c.object != '' AND {_in_list('c.posting_edit')} AND c.bu NOT IN (SELECT code FROM business_units "
             f"WHERE status NOT IN ({', '.join('?' * len(ACTIVE))}))")  # fmt: skip
    params = (json.dumps(list(settings.postable_codes)), *ACTIVE)
    best: dict[str, dict[str, tuple[int, str, str]]] = {"description": {}, "category": {}}
    # (one pass for both: each code's descriptions and titles counted from its distinct pairs of the two, D-272)
    counted: dict[str, dict[tuple[str, str], list[Any]]] = {"description": {}, "category": {}}
    for obj, sub, description, category, n, first in conn.execute(
            f"SELECT c.object, c.subsidiary, c.description, c.category, COUNT(*), MIN(c.bu) FROM chart_accounts c "
            f"WHERE {where} GROUP BY c.object, c.subsidiary, c.description, c.category", params):  # fmt: skip
        code = code_of(obj, sub)
        for column, text in (("description", description), ("category", category)):
            had = counted[column].setdefault((code, text), [0, first])
            had[0] += n
            had[1] = min(had[1], first)
    for column, found in best.items():
        for (code, text), (n, first) in counted[column].items():
            had = found.get(code)
            if text and (had is None or not had[2] or n > had[0] or (n == had[0] and first < had[1])):
                found[code] = (n, first, text)
            elif had is None:
                found[code] = (0, "", "")
    accounts = {code: (best["description"][code][2], best["category"].get(code, (0, "", ""))[2])
                for code in sorted(best["description"])}  # fmt: skip
    centers = {bu: name or "" for bu, name in conn.execute(
        "SELECT u.bu, COALESCE(NULLIF(b.name, ''), h.description, '') FROM (SELECT DISTINCT c.bu FROM "
        f"chart_accounts c WHERE {where}) u LEFT JOIN business_units b ON b.code = u.bu LEFT JOIN chart_accounts h "
        "ON h.bu = u.bu AND h.object = '' AND h.subsidiary = '' ORDER BY u.bu", params)}  # fmt: skip
    return accounts, centers


def _changes(before: dict[Any, Any], after: dict[Any, Any]) -> dict[str, Any]:
    """How a table's rows, by key, differ once replaced: added, changed and removed."""
    return {
        "added": sum(1 for key in after if key not in before),
        "updated": sum(1 for key, row in after.items() if key in before and before[key] != row),
        "removed": sum(1 for key in before if key not in after),
    }


LOAD_BATCH = 50_000  # the rows of a chart written to ``chart_load`` at a time
# A loaded row ``l`` and the chart's ``c``: the same account.
_SAME = "l.bu = chart_accounts.bu AND l.object = chart_accounts.object AND l.subsidiary = chart_accounts.subsidiary"


def _batches(rows: Iterable[Any], size: int) -> Iterator[list[Any]]:
    it = iter(rows)
    while batch := list(itertools.islice(it, size)):
        yield batch


def _scope(conn: sqlite3.Connection, whole: bool, named: Iterable[str] | None = None) -> tuple[str, list[str]]:
    """(the chart's rows a load replaces, as SQL of ``chart_accounts``; the companies said: those of the file, when
    another company's rows are kept): the file's companies' accounts, and those kept without a company on its
    business units; ``whole``, or a file naming no company, every row (D-267). ``named``: the file's companies, when
    the caller counted them (``Staged.by_company``)."""
    companies = sorted(c for c in named if c) if named is not None else [r[0] for r in conn.execute(
        "SELECT DISTINCT company FROM temp.chart_load WHERE company != '' ORDER BY company")]  # fmt: skip
    if whole or not companies:
        return "1", []
    listed = ", ".join(f"'{c}'" for c in companies if "'" not in c)
    scope = f"(company IN ({listed}) OR (company = '' AND bu IN (SELECT DISTINCT bu FROM temp.chart_load)))"
    others = conn.execute(f"SELECT EXISTS (SELECT 1 FROM chart_accounts WHERE NOT {scope})").fetchone()[0]
    return scope, companies if others else []


def _staged_changes(conn: sqlite3.Connection, scope: str) -> dict[str, int]:
    """How the chart's rows in ``scope`` differ from the rows loaded (``chart_load``): added, changed and removed."""
    differs = " OR ".join(f"c.{col} IS NOT l.{col}" for col in CHART_COLUMNS[3:])
    added, updated = conn.execute(  # (one pass over the rows loaded: D-272)
        f"SELECT COALESCE(SUM(c.bu IS NULL), 0), COALESCE(SUM(c.bu IS NOT NULL AND ({differs})), 0) FROM "
        "temp.chart_load l LEFT JOIN chart_accounts c ON c.bu = l.bu AND c.object = l.object AND c.subsidiary = "
        "l.subsidiary").fetchone()  # fmt: skip
    removed = conn.execute(f"SELECT COUNT(*) FROM chart_accounts WHERE {scope} AND NOT EXISTS (SELECT 1 FROM "
                           f"temp.chart_load l WHERE {_SAME})").fetchone()[0]  # fmt: skip
    return {"added": int(added), "updated": int(updated), "removed": int(removed)}


def _merged(before: dict[Any, Any], new: dict[Any, Any], whole: bool,
            unit: Callable[[Any], str]) -> tuple[dict[Any, Any], list[str]]:  # fmt: skip
    """(the rows a load leaves, the companies it replaced) of the rows kept (``before``) and the file's (``new``,
    ``ChartRow`` or ``BuRow``): the file's companies' rows replaced, and the rows kept without a company on its
    business units (``unit``: a row's); the others kept (D-267). ``whole``, a file naming no company, or none of the
    rows another company's: the file's alone, no company said."""
    companies = sorted({company_code(r.company) for r in new.values() if r.company})
    if whole or not companies:
        return new, []
    named, units = set(companies), {unit(r) for r in new.values()}
    kept = {key: r for key, r in before.items()
            if not (company_code(r.company) in named or (not r.company and unit(r) in units))}  # fmt: skip
    return ({**kept, **new}, companies) if kept else (new, [])  # (none kept: as a whole file, nothing more to say)


def _write(conn: sqlite3.Connection, table: str, key: tuple[str, ...], columns: tuple[str, ...],
           before: dict[Any, Any], after: dict[Any, Any]) -> None:  # fmt: skip
    """The rows of ``table`` changed from ``before`` to ``after`` (by ``key``): those gone deleted, the new and changed
    ones written, the others left as they are: a company's file of a chart of a million accounts writes its own."""
    where = " AND ".join(f"{k} = ?" for k in key)
    gone = [k if isinstance(k, tuple) else (k,) for k in before if k not in after]
    conn.executemany(f"DELETE FROM {table} WHERE {where}", gone)
    conn.executemany(
        dialect.upsert(table, columns, key),
        [tuple(getattr(r, c) for c in columns) for k, r in after.items() if before.get(k) != r],
    )  # (a row's fields are its table's columns)


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


SHRINK_MOST = 0.5  # a refresh with less than this share of a company's accounts now is refused unless asked (D-272)
CHART_PARTS = "chart_parts"  # {company: [the files its accounts came from at its last load]} (settings, JSON)


class ChartRefused(ValueError):
    """A chart that is not loaded, the live one kept as it is; the message says why and what to do, ``said`` what was
    read of its files (``chart_cmd.read_lines``)."""

    said: list[str] = []  # noqa: RUF012  (each refusal's own, set where it is raised again)


@dataclass
class Staged:
    """A chart read into ``chart_load`` (``Store.stage_chart``): the files (``parts``), the rows given (``given``),
    those repeating an account already given (``repeats``, the first ``chart.EXAMPLES`` of their rows in
    ``repeat_rows``), the accounts and title rows kept, the accounts by company and by posting edit code, the business
    units, and of each company the files it is in (``company_parts``)."""

    parts: list[str] = field(default_factory=list)
    given: int = 0
    repeats: int = 0
    repeat_rows: list[int] = field(default_factory=list)
    accounts: int = 0
    titles: int = 0
    by_company: Counter[str] = field(default_factory=Counter)
    edits: Counter[str] = field(default_factory=Counter)
    units: int = 0
    company_parts: dict[str, set[str]] = field(default_factory=dict)

    @property
    def kept(self) -> int:
        """The rows staged: the accounts and the business units' title rows."""
        return self.accounts + self.titles

    def repeated(self, conn: sqlite3.Connection, values: list[tuple[Any, ...]], left: int) -> None:
        """Of a batch ``left`` rows of which were not staged: rows repeating an account given before (in the batch or
        one before; the first given kept), counted, the first of them named by their row of the file."""
        self.repeats += left
        for value in values:
            if len(self.repeat_rows) >= EXAMPLES or not value[-1]:
                break
            found = conn.execute("SELECT line FROM temp.chart_load WHERE bu = ? AND object = ? AND subsidiary = ?",
                                 value[:3]).fetchone()  # fmt: skip
            if found is not None and found[0] != value[-1]:
                self.repeat_rows.append(value[-1])

    def counted(self, part: str, values: list[tuple[Any, ...]]) -> None:
        """A batch given (``values``: as written, a row's company eighth): of each company, the files it is in."""
        self.given += len(values)
        for company in {value[7] for value in values}:
            if company:
                self.company_parts.setdefault(company, set()).add(part)

    def tallied(self, conn: sqlite3.Connection) -> None:
        """What was staged, counted by SQLite once the rows are in: the accounts by company and posting edit code, the
        title rows, the business units."""
        for company, edit, account, n in conn.execute(
                "SELECT company, posting_edit, object != '', COUNT(*) FROM temp.chart_load GROUP BY company, "
                "posting_edit, object != ''"):  # fmt: skip
            if account:
                self.accounts += n
                self.by_company[company] += n
                self.edits[edit] += n
            else:
                self.titles += n
        self.units = int(conn.execute("SELECT COUNT(DISTINCT bu) FROM temp.chart_load").fetchone()[0])


def _live_accounts(conn: sqlite3.Connection) -> Counter[str]:
    """The live chart's accounts by company."""
    return Counter({company: int(n) for company, n in conn.execute(
        "SELECT company, COUNT(*) FROM chart_accounts WHERE object != '' GROUP BY company")})  # fmt: skip


def _parts(conn: sqlite3.Connection) -> dict[str, list[str]]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (CHART_PARTS,)).fetchone()
    try:
        kept = json.loads(row[0]) if row else {}
    except ValueError:
        kept = {}
    return {str(k): [str(p) for p in v] for k, v in kept.items() if isinstance(v, list)} if isinstance(kept, dict) \
        else {}  # fmt: skip


def _refuse_cut_short(conn: sqlite3.Connection, staged: Staged, by_company: bool) -> None:
    """``ChartRefused`` for a refresh that looks cut short (D-272): of each company it loads (``by_company``; else the
    whole chart), less than ``SHRINK_MOST`` of the accounts it has now; a company loaded last time from several files
    (``CHART_PARTS``) found in fewer of this load's."""
    live = _live_accounts(conn)
    anyway = " If that is what JD Edwards has now, import it again with --anyway (python -m ap_coder chart import)."
    if by_company:
        short = [f"{co} ({staged.by_company[co]:,} accounts, {live[co]:,} now)" for co in sorted(staged.by_company)
                 if co and live[co] and staged.by_company[co] < live[co] * SHRINK_MOST]  # fmt: skip
        if short:
            raise ChartRefused(f"Not loaded: the file has less than half the accounts loaded now of company "
                               f"{', '.join(short)}, so it looks cut short or filtered, and the chart was left as it "
                               f"was.{anyway}")  # fmt: skip
    elif sum(live.values()) and staged.accounts < sum(live.values()) * SHRINK_MOST:
        raise ChartRefused(f"Not loaded: the file has {staged.accounts:,} accounts, less than half the "
                           f"{sum(live.values()):,} loaded now, so it looks cut short or filtered, and the chart was "
                           f"left as it was.{anyway}")  # fmt: skip
    before = _parts(conn)
    split = []
    for company, now in sorted(staged.company_parts.items()):
        had = before.get(company) or []
        if len(had) > 1 and len(now) < len(had):
            split.append(f"company {company} came from {len(had)} files last time ({', '.join(had)}); this import "
                         f"has it in {len(now)} ({', '.join(sorted(now))})")  # fmt: skip
    if split:
        raise ChartRefused(f"Not loaded: {'; '.join(split)}. A company split across files is loaded with all of them "
                           "in one import (python -m ap_coder chart import --chart a.txt --chart b.txt), so that "
                           "no part of it is lost; the chart was left as it was. If it now comes in fewer files, "
                           "import it with --anyway.")  # fmt: skip


def _keep_parts(conn: sqlite3.Connection, staged: Staged, whole: bool) -> None:
    """Remember which files each company loaded came from (``CHART_PARTS``): all of them for a whole chart."""
    kept = {} if whole else _parts(conn)
    kept.update({company: sorted(parts) for company, parts in staged.company_parts.items()})
    _put(conn, CHART_PARTS, json.dumps(kept, sort_keys=True))


class ChartMixin:
    def chart_accounts(self) -> list[ChartRow]:
        """Every row of the chart as imported (``core/chart.ChartRow``; a business unit's header row has no
        object)."""
        with self._conn() as conn:
            return _chart(conn)

    def chart_counts(self) -> tuple[int, int]:
        """(the chart's accounts, the business units they are on), counted in one query: a page that only says how
        many reads no row (B-2436: 20,000 rows read one at a time gave way, each, to a load reading its workbook)."""
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(CASE WHEN object != '' THEN 1 END), COUNT(DISTINCT bu) FROM "
                               "chart_accounts").fetchone()  # fmt: skip
        return int(row[0]), int(row[1])

    def chart_edit_counts(self) -> dict[str, int]:
        """{posting edit code: the chart's accounts with it}, counted once a change of the chart (D-272: what the
        Accounts AP posts to card lists as not yet decided)."""

        def count() -> dict[str, int]:
            with self._conn() as conn:
                rows = conn.execute("SELECT posting_edit, COUNT(*) FROM chart_accounts WHERE object != '' GROUP BY "
                                    "posting_edit")  # fmt: skip
                return {code: int(n) for code, n in rows}

        return dict(self.summary_memo("chart_edit_counts", count, topic="", asked=self.chart_version()))

    def chart_lists(self) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
        """The GL accounts and cost centers the chart gives (``chart.account_lists``), worked out by SQLite."""
        with self._conn() as conn:
            return _lists(conn)

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

    def chart_without(self) -> tuple[str, ...]:
        """The columns the chart loaded came without (``chart.CHART_WANTS``: a person asked for it anyway), for
        ``chart.without_lines`` to say each time the chart is shown; none for a whole export."""
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (WITHOUT_KEY,)).fetchone()
        return tuple(part for part in (row["value"] if row else "").split(",") if part)

    def import_chart(self, rows: Iterable[ChartRow], actor: str | None = None, without: Iterable[str] = (),
                     whole: bool = False, progress: Callable[[int], None] | None = None,
                     anyway: bool = False) -> dict[str, Any]:  # fmt: skip
        """Load the chart's ``rows`` (``core/chart.chart_rows``, or ``iter_chart_rows`` streamed from a file): staged
        (``stage_chart``), then swapped in (``swap_chart``), on one connection. ``progress``: told the rows read so far
        after each batch."""
        with self.session():
            staged = self.stage_chart([("", rows)], progress)
            return self.swap_chart(staged, actor=actor, without=without, whole=whole, anyway=anyway)

    def stage_chart(self, parts: Iterable[tuple[str, Iterable[ChartRow]]],
                    progress: Callable[[int], None] | None = None) -> Staged:  # fmt: skip
        """Read the chart's rows into a table of their own (``chart_load``, LOAD_BATCH at a time), the live chart not
        touched and the write lock not taken (D-272): the files of one import (``parts``: (its name, its rows) each; a
        company split across files is loaded with all of them), an account given twice kept once (the first;
        counted, its rows named). Within ``session()``: ``swap_chart`` swaps it in, on the same connection. What was
        staged, counted (``Staged``). ``progress``: told the rows read so far after each batch."""
        staged = Staged()
        with self._conn() as conn:
            conn.execute("DROP TABLE IF EXISTS temp.chart_load")
            conn.execute(f"CREATE TEMP TABLE chart_load AS SELECT {', '.join(CHART_COLUMNS)}, 0 AS line FROM "
                         "chart_accounts WHERE 0")  # fmt: skip
            conn.execute("CREATE UNIQUE INDEX temp.chart_load_key ON chart_load (bu, object, subsidiary)")
            insert = dialect.insert_or_ignore(f"INSERT INTO temp.chart_load ({', '.join(STAGED_COLUMNS)}) VALUES "
                                              f"({', '.join('?' * len(STAGED_COLUMNS))})")  # fmt: skip
            for name, rows in parts:
                staged.parts.append(name)
                for batch in _batches(rows, LOAD_BATCH):
                    values = [(r.bu, r.object, r.subsidiary, r.description, r.lod, r.posting_edit, r.category,
                               company_code(r.company), r.line) for r in batch]  # fmt: skip
                    before = conn.total_changes
                    conn.executemany(insert, values)
                    if conn.total_changes - before < len(values):
                        staged.repeated(conn, values, len(values) - (conn.total_changes - before))
                    staged.counted(name, values)
                    if progress is not None:
                        progress(staged.given)
            staged.tallied(conn)
        return staged

    def swap_chart(self, staged: Staged, actor: str | None = None, without: Iterable[str] = (), whole: bool = False,
                   anyway: bool = False, summary: Iterable[str] = ()) -> dict[str, Any]:  # fmt: skip
        """Put the chart staged (``stage_chart``, on this session's connection) in place of the live one's rows it
        replaces, in one transaction, and work the GL accounts and cost centers out again, even when the rows are those
        already imported. Rows naming their company replace the accounts of those companies alone (and the accounts
        kept without one on their business units), the other companies' kept: a chart split by company loads a file
        at a time, or its files together, the files adding up (D-267). ``whole``, or rows naming none, replace the
        whole chart. Refused (``ChartRefused``, a ``ValueError``; nothing changed) for no account at all (the wrong
        file) or none that may be posted to; and, unless ``anyway``, for a load that looks cut short (D-272): a company
        (or the whole chart) with less than ``SHRINK_MOST`` of the accounts it has now, or a company loaded before from
        several files with fewer of them now (``CHART_PARTS``). Returns {"rows", "added", "updated", "removed"} of the
        chart, "companies" (those replaced, when another company's accounts were kept) and what the lists' rebuild did
        (``_rebuild_account_lists``). ``without``: the columns the export did not have (``Rows.without``), kept for
        ``chart_without``; ``summary``: what the import said of its files, kept with its event for the page."""
        with self._conn() as conn:
            dialect.begin_write(conn)
            if not staged.accounts:
                conn.execute("DROP TABLE IF EXISTS temp.chart_load")
                raise ChartRefused("No row of this file has an object account, so it is not a chart of accounts: "
                                   "nothing was changed.")  # fmt: skip
            by_hand = self._loaded_by_hand(conn)  # (D-221: kept across a load)
            scope, companies = _scope(conn, whole, staged.by_company)
            if not anyway:
                _refuse_cut_short(conn, staged, scope != "1")
            result = {**_staged_changes(conn, scope), **({"companies": companies} if companies else {})}
            if by_hand:
                result["by_hand"] = by_hand[:NAMED]
            lists = _lists(conn)
            was, same = lists[0], lists  # (the chart as it was: its lists are the ones it gives again, D-272)
            if result["added"] or result["updated"] or result["removed"]:
                same = None
                conn.execute(f"DELETE FROM chart_accounts WHERE {scope} AND NOT EXISTS (SELECT 1 FROM temp.chart_load "
                             f"l WHERE {_SAME})")  # fmt: skip
                sets = ", ".join(f"{c} = excluded.{c}" for c in CHART_COLUMNS[3:])
                differs = " OR ".join(f"c.{col} IS NOT l.{col}" for col in CHART_COLUMNS[3:])
                conn.execute(f"INSERT INTO chart_accounts ({', '.join(CHART_COLUMNS)}) SELECT "  # (the new and changed)
                             f"{', '.join('l.' + c for c in CHART_COLUMNS)} FROM temp.chart_load l LEFT JOIN "
                             "chart_accounts c ON c.bu = l.bu AND c.object = l.object AND c.subsidiary = l.subsidiary "
                             f"WHERE c.bu IS NULL OR {differs} ON CONFLICT (bu, object, subsidiary) DO UPDATE SET "
                             f"{sets}")  # fmt: skip
            conn.execute("DROP TABLE temp.chart_load")
            _keep_parts(conn, staged, scope == "1")
            result["rows"] = _count(conn)
            result.update(self._rebuild_account_lists(conn, was, same))
            missing = list(without)
            if missing:
                result["without"] = missing
            _put(conn, WITHOUT_KEY, ",".join(missing))
            said = list(summary)
            if said:
                result["summary"] = said
            self._chart_changed(conn, "chart_accounts", result, actor, loaded=True)
        return result

    def _loaded_by_hand(self, conn: sqlite3.Connection) -> list[str]:
        """What a person changed by hand put back on the rows being loaded (``chart_load``) that have it: the few
        rows kept with a change read, ``_hand_edits_put_back``, and written back."""
        from .by_hand import chart_parts, held

        texts = list(held(conn).get("chart_accounts", {}))
        if not texts:
            return []
        keys = [chart_parts(text) for text in texts]
        rows = {}
        for key in keys:
            found = conn.execute(f"SELECT {', '.join(CHART_COLUMNS)} FROM temp.chart_load WHERE bu = ? AND "
                                 "object = ? AND subsidiary = ?", key).fetchone()  # fmt: skip
            if found is not None:
                rows[key] = ChartRow(*found)
        put = self._hand_edits_put_back(conn, "chart_accounts", rows)
        conn.executemany(
            "UPDATE temp.chart_load SET description = ?, posting_edit = ? WHERE bu = ? AND object = ? "
            "AND subsidiary = ?",
            [(r.description, r.posting_edit, *key) for key, r in rows.items()],
        )
        return put

    def import_business_units(self, rows: Iterable[BuRow], actor: str | None = None,
                              whole: bool = False) -> dict[str, Any]:  # fmt: skip
        """Load the business units ``rows`` (``core/chart.bu_rows``): those of the companies they name replace those
        companies' (and the ones kept without a company of their codes), the others kept, as ``import_chart``
        loads the chart; ``whole``, or rows naming no company, replace them all. With a chart imported, the GL
        accounts and cost centers are worked out again (a business unit closed since leaves them). Returns what
        ``import_chart`` does. ``ValueError``, nothing changed, for no row at all, or when no account would be left
        to code to."""
        new = {r.code: r for r in rows}
        if not new:
            raise ValueError("This file has no business unit: nothing was changed.")
        with self._conn() as conn:
            dialect.begin_write(conn)
            old = _units(conn)
            by_hand = self._hand_edits_put_back(conn, "business_units", new)  # (D-221: kept across a load)
            before = {r.code: r for r in old}
            after, companies = _merged(before, new, whole, lambda r: r.code)
            result = {**_changes(before, after), "rows": len(after), **({"companies": companies} if companies else {})}
            if by_hand:
                result["by_hand"] = by_hand[:NAMED]
            was = _lists(conn)[0]
            _write(conn, "business_units", ("code",), BU_COLUMNS, before, after)
            result.update(self._rebuild_account_lists(conn, was))
            self._chart_changed(conn, "business_units", result, actor, loaded=True)
        return result

    def rebuild_account_lists(self, actor: str | None = None) -> dict[str, Any]:
        """Work the GL accounts and the cost centers out again from the chart, the business units and the chart's
        settings as they are: the repair for lists changed by hand or by another import since. Returns what the
        rebuild did (``_rebuild_account_lists``); recorded when it changed something. ``ValueError`` with no chart
        imported."""
        with self._conn() as conn:
            dialect.begin_write(conn)
            rows = _count(conn)
            if not rows:
                raise ValueError(NO_CHART)
            result = self._rebuild_account_lists(conn, _lists(conn)[0])
            self._chart_changed(conn, "chart_accounts", {"rows": rows, **result}, actor)
        return result

    def _chart_setting_changing(self, conn: sqlite3.Connection, key: str) -> dict[str, tuple[str, str]] | None:
        """Before a setting is saved: the GL accounts the chart gives now, when ``key`` is one of the chart's
        settings and a chart is imported (else None: nothing follows the setting)."""
        if key not in SETTING_KEYS:
            return None
        return _lists(conn)[0] if _count(conn) else None

    def _chart_setting_changed(self, conn: sqlite3.Connection, was: dict[str, tuple[str, str]],
                               actor: str | None) -> None:  # fmt: skip
        """After one of the chart's settings is saved (``Store.set_setting``): the lists follow it, in the same
        transaction. ``ValueError`` (the setting is not saved) when no account would be left."""
        result = self._rebuild_account_lists(conn, was)
        self._chart_changed(conn, "chart_accounts", {"rows": _count(conn), **result}, actor)

    def _chart_changed(self, conn: sqlite3.Connection, table: str, result: dict[str, Any], actor: str | None,
                       loaded: bool = False) -> None:  # fmt: skip
        """When an import or a rebuild changed a table or a list: one more change to the chart (``chart_version``),
        what the tables now hold (``chart_hash``), so the reference data and the checks are worked out again, and
        its event with everything the result says. ``loaded``: a file loaded, recorded even when it changed nothing
        (``unchanged``: "last loaded" and Activity say it, B-2439), with nothing worked out again."""
        if not _moved(result):
            if loaded:
                self._log(conn, "accounts_imported", actor=actor,
                          detail={"table": table, **result, "replace_all": True, "unchanged": True})  # fmt: skip
            return
        self._chart_stamped(conn)
        self._log(conn, "accounts_imported", actor=actor, detail={"table": table, **result, "replace_all": True})

    @staticmethod
    def _chart_stamped(conn: sqlite3.Connection) -> None:
        """One more change to the chart (``chart_version``) and a mark of it never written before (``chart_hash``):
        the reference data and the checks are worked out again. An import's, a rebuild's, and a row changed by hand
        (``by_hand``, which records its own event). The mark is random, not a hash of every row (D-272: 9 s of 3,500,000
        accounts under the write lock): another chart, after a backup restored and the same count of changes, still
        never passes for the one a process kept (B-2125)."""
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
            (VERSION_KEY,),
        )
        _put(conn, HASH_KEY, secrets.token_hex(10))

    def _rebuild_account_lists(self, conn: sqlite3.Connection, was: dict[str, tuple[str, str]],
                               given: tuple[dict[str, tuple[str, str]], dict[str, str]] | None = None
                               ) -> dict[str, Any]:  # fmt: skip
        """The GL accounts and the cost centers made what the chart, the business units and the chart's settings
        give (``chart.account_lists``), in this transaction; with no chart imported they are left as they are.
        ``was``: the GL accounts the chart gave before, so a category that came from the chart follows it and one a
        person typed stays. Returns {"gl_accounts", "cost_centers"} ({"now", "added", "updated", "removed"}),
        "left" (the codes that left each list), "set_aside" and "brought_back" (the codes whose meals tick,
        category or manager was kept aside or put back), "unmapped" (what else points at a GL account no longer
        listed). ``ValueError`` when no GL account would be left. ``given``: what ``_lists`` gives now, when the
        caller has it already (a chart loaded again unchanged)."""
        lists = _Lists(conn, _aside(conn))
        if not _count(conn):
            for table in LISTS:
                lists.counts[table]["now"] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            return {**lists.result(), "unmapped": []}
        accounts, centers = given if given is not None else _lists(conn)
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

    def _account_pairs(self) -> IndexedPairs | None:
        """Which pairs exist, asked of the tables as they are (``chart_index``, D-267: never the chart held whole;
        None with no chart imported)."""
        with self._conn() as conn:
            has = conn.execute("SELECT EXISTS (SELECT 1 FROM chart_accounts)").fetchone()[0]
            return IndexedPairs(self, _settings(conn)) if has else None

    def account_pairs(self) -> IndexedPairs | None:
        """Which GL code exists on which business unit and may be posted to (``core/chart.AccountPairs``), or None
        when no chart was imported. Built once for a chart and its settings: it is the reference data's ``pairs``,
        kept with it."""
        if not self.has_chart():
            return None
        try:
            return self.reference_data().pairs
        except ValueError:  # every GL account deleted by hand since: no reference data to keep it with
            return self._account_pairs()
