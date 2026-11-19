"""The chart of accounts asked in the database, never held whole (D-267): a group's chart is millions of accounts.

``IndexedPairs`` answers what ``core/chart.AccountPairs`` answers (whether a line may be coded to a business unit and a
GL code: ``status``; an account's description and posting edit code; the business units a code is offered on...), each
by an indexed query of ``chart_accounts`` (its key: business unit, object, subsidiary; ``chart_accounts_code``: object,
subsidiary), the accounts asked lately kept (``ACCOUNTS_KEPT``). What every check asks of the whole chart, the GL codes
and the business units offered (``offered_codes``, ``business_units``: the GL accounts and cost centers a line may
take), is read once in one pass, holding their sets only; the business units' names, status and company (report 2,
and each business unit's title row) are read once too: a few per business unit, never per account.

``account_search`` is the review screen's Account box (``GET /review/accounts.json?q=``): the offered accounts whose
string, name or business unit's name has every word typed, found by the business unit, object and description, at most
``SEARCH_MOST``.

It is the chart for every size (measured, D-267: 10,000 accounts as fast as in memory for a line's check, 3,000,000
without holding them): ``Store.account_pairs`` gives one, kept with the reference data.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter, OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..chart import (
    ACTIVE,
    BU_INACTIVE,
    NOT_IN_CHART,
    NOT_POSTABLE,
    OK,
    RESERVED,
    ChartSettings,
    _code,
    _gl,
    _never_offered,
    code_of,
    skip_word,
    split_code,
)
from ..keys import company_code
from ._base import _held_connections

ACCOUNTS_KEPT = 20_000  # the accounts asked lately, each kept with its description and posting edit code
SEARCH_MOST = 50  # an Account box's search: the accounts it draws at most (more are found by typing more)
CODE_INDEX = "CREATE INDEX IF NOT EXISTS chart_accounts_code ON chart_accounts (object, subsidiary)"
UNIT_COMPANY_INDEX = "CREATE INDEX IF NOT EXISTS business_units_company ON business_units (company)"


class IndexedPairs:
    """The chart of ``store`` as the checks and the suggestions ask it (``AccountPairs``' answers), with its settings
    ``settings``. Shared by the threads of a process: read it, don't change it."""

    def __init__(self, store: Any, settings: ChartSettings | None = None) -> None:
        self.settings = settings or ChartSettings()
        self._store = store
        self._lock = threading.Lock()
        self._local = threading.local()  # (each thread's own connection, when it holds no session)
        self._kept: OrderedDict[tuple[str, str], tuple[str, str] | None] = OrderedDict()
        self._units: tuple[dict[str, str], set[str], dict[str, str], dict[str, str]] | None = None
        self._offered: tuple[frozenset[str], tuple[str, ...]] | None = None

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        """The connection a question is asked on: this thread's session's when it holds one (a request's, a write
        under way: what it changed is seen), else this thread's own, kept open (a connection opened for each question
        took a millisecond, a question 20 microseconds)."""
        held = _held_connections().get(str(self._store.path))
        if held is not None:
            with self._store._conn() as conn:
                yield conn
            return
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = self._store._open()
        yield conn

    # --- What is read once: the business units, and the codes and units offered ------------------------------------

    def _unit_facts(self) -> tuple[dict[str, str], set[str], dict[str, str], dict[str, str]]:
        """({business unit: name}, the closed ones, {business unit: company}, {business unit: skip word}): the master's
        and each business unit's title row in the chart, read once."""
        if self._units is None:
            names: dict[str, str] = {}
            closed: set[str] = set()
            companies: dict[str, str] = {}
            with self._reader() as conn:
                for bu, description in conn.execute("SELECT bu, description FROM chart_accounts WHERE object = '' "
                                                     "AND subsidiary = '' ORDER BY bu"):  # fmt: skip
                    names.setdefault(bu, description)
                for code, name, company, status in conn.execute("SELECT code, name, company, status FROM "
                                                                "business_units"):  # fmt: skip
                    if name:
                        names[code] = name
                    if company:
                        companies[code] = company_code(company)
                    if status not in ACTIVE:
                        closed.add(code)
            skipped = {bu: word for bu, name in names.items() if (word := skip_word(name, self.settings.skip_words))}
            self._units = (names, closed, companies, skipped)
        return self._units

    def _offered_once(self) -> tuple[frozenset[str], tuple[str, ...]]:
        """(the GL codes offered on a business unit at least, the business units with an account offered), of one
        pass over the accounts that may be posted to, holding the two sets alone."""
        if self._offered is None:
            _names, closed, _companies, skipped = self._unit_facts()
            patterns = self.settings.never_offered
            codes: set[str] = set()
            units: set[str] = set()
            postable = list(self.settings.postable_codes)
            sql = (f"SELECT bu, object, subsidiary FROM chart_accounts WHERE object != '' AND posting_edit IN "
                   f"({', '.join('?' * len(postable))})")  # fmt: skip
            with self._reader() as conn:
                for bu, obj, sub in conn.execute(sql, postable):
                    if bu in closed or bu in skipped:
                        continue
                    code = code_of(obj, sub)
                    if patterns and _never_offered(patterns, bu, code):
                        continue
                    codes.add(code)
                    units.add(bu)
            self._offered = (frozenset(codes), tuple(sorted(units)))
        return self._offered

    # --- One account ------------------------------------------------------------------------------------------------

    def _found(self, business_unit: Any, code: Any) -> tuple[str, str, tuple[str, str] | None]:
        """(the business unit, the GL code, (its description, its posting edit code) or None: no such account)."""
        bu, code = _code(business_unit), _gl(code)
        key = (bu, code)
        with self._lock:
            if key in self._kept:
                self._kept.move_to_end(key)
                return bu, code, self._kept[key]
        obj, sub = split_code(code)
        with self._reader() as conn:
            row = conn.execute("SELECT description, posting_edit FROM chart_accounts WHERE bu = ? AND object = ? AND "
                               "subsidiary = ?", (bu, obj, sub)).fetchone() if obj else None  # fmt: skip
        found = (row[0], row[1]) if row is not None else None
        with self._lock:
            self._kept[key] = found
            while len(self._kept) > ACCOUNTS_KEPT:
                self._kept.popitem(last=False)
        return bu, code, found

    def _status(self, bu: str, code: str, found: tuple[str, str] | None) -> str:
        if bu in self._unit_facts()[1]:
            return BU_INACTIVE
        if found is None:
            return NOT_IN_CHART
        if found[1] not in self.settings.postable_codes:
            return NOT_POSTABLE
        return RESERVED if self._why(bu, code) else OK

    def _why(self, bu: str, code: str) -> str:
        pattern = _never_offered(self.settings.never_offered, bu, code)
        if pattern:
            return f"it is one of the accounts never offered ({pattern})"
        word = self._unit_facts()[3].get(bu)
        return f"the name of business unit {bu} says {word}" if word else ""

    def status(self, business_unit: Any, code: Any) -> str:
        """``AccountPairs.status``: ``OK``, ``BU_INACTIVE``, ``NOT_IN_CHART``, ``NOT_POSTABLE`` or ``RESERVED``."""
        return self._status(*self._found(business_unit, code))

    def why_reserved(self, business_unit: Any, code: Any) -> str:
        bu, code, found = self._found(business_unit, code)
        return self._why(bu, code) if found is not None else ""

    def offered(self, business_unit: Any, code: Any) -> bool:
        return self.status(business_unit, code) == OK

    def description(self, business_unit: Any, code: Any) -> str:
        found = self._found(business_unit, code)[2]
        return found[0] if found else ""

    def posting_edit(self, business_unit: Any, code: Any) -> str:
        found = self._found(business_unit, code)[2]
        return found[1] if found else ""

    # --- Of a business unit, of a code -------------------------------------------------------------------------

    def accounts_of(self, business_unit: Any) -> list[str]:
        """The GL codes offered on a business unit, in order (its accounts, by the chart's key)."""
        bu = _code(business_unit)
        with self._reader() as conn:
            rows = conn.execute("SELECT object, subsidiary, description, posting_edit FROM chart_accounts WHERE bu = ? "
                                "AND object != ''", (bu,)).fetchall()  # fmt: skip
        out = []
        for obj, sub, description, edit in rows:
            code = code_of(obj, sub)
            if self._status(bu, code, (description, edit)) == OK:
                out.append(code)
        return sorted(out)

    def bus_of(self, code: Any) -> tuple[str, ...]:
        """The business units a GL code is offered on, in order (``chart_accounts_code``; what may be posted to and is
        not closed told by SQLite, the units held back for their name and the accounts never offered here)."""
        code = _gl(code)
        obj, sub = split_code(code)
        if not obj:
            return ()
        postable = list(self.settings.postable_codes)
        skipped, patterns = self._unit_facts()[3], self.settings.never_offered
        closed = f"SELECT code FROM business_units WHERE status NOT IN ({', '.join('?' * len(ACTIVE))})"
        sql = (f"SELECT bu FROM chart_accounts WHERE object = ? AND subsidiary = ? AND posting_edit IN "
               f"({', '.join('?' * len(postable))}) AND bu NOT IN ({closed}) ORDER BY bu")  # fmt: skip
        with self._reader() as conn:
            rows = conn.execute(sql, (obj, sub, *postable, *ACTIVE)).fetchall()
        return tuple(bu for (bu,) in rows
                     if bu not in skipped and not (patterns and _never_offered(patterns, bu, code)))  # fmt: skip

    def offered_codes(self) -> frozenset[str]:
        return self._offered_once()[0]

    def business_units(self) -> list[str]:
        return list(self._offered_once()[1])

    def has_business_unit(self, business_unit: Any) -> bool:
        bu = _code(business_unit)
        return self.has_accounts(bu) or bu in self._unit_facts()[0] or bu in self._unit_facts()[1]

    def has_accounts(self, business_unit: Any) -> bool:
        with self._reader() as conn:
            return conn.execute("SELECT 1 FROM chart_accounts WHERE bu = ? AND object != '' LIMIT 1",
                                (_code(business_unit),)).fetchone() is not None  # fmt: skip

    def has_code(self, code: Any) -> bool:
        obj, sub = split_code(_gl(code))
        with self._reader() as conn:
            return bool(obj) and conn.execute("SELECT 1 FROM chart_accounts WHERE object = ? AND subsidiary = ? LIMIT "
                                              "1", (obj, sub)).fetchone() is not None  # fmt: skip

    def name_of(self, business_unit: Any) -> str:
        return self._unit_facts()[0].get(_code(business_unit), "")

    def company_of(self, business_unit: Any) -> str:
        return self._unit_facts()[2].get(str(business_unit or "").strip(), "")

    def units_of(self, company: str) -> frozenset[str]:
        with self._reader() as conn:
            return frozenset(
                r[0]
                for r in conn.execute(
                    "SELECT code FROM business_units WHERE company IN (?, ?)",
                    (company_code(company), str(company or "").strip()),
                )
            )

    def closed_units(self) -> list[str]:
        return sorted(self._unit_facts()[1])

    def skipped_units(self) -> dict[str, str]:
        return dict(sorted(self._unit_facts()[3].items()))

    def __len__(self) -> int:
        with self._reader() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM chart_accounts WHERE object != ''").fetchone()[0])

    def tally(self) -> Counter[str]:
        """How many of the chart's accounts are of each status, in one pass."""
        out: Counter[str] = Counter()
        with self._reader() as conn:
            for bu, obj, sub, description, edit in conn.execute(
                "SELECT bu, object, subsidiary, description, posting_edit FROM chart_accounts WHERE object != ''"
            ):
                out[self._status(bu, code_of(obj, sub), (description, edit))] += 1
        return out

    # --- The Account box's search ---------------------------------------------------------------------------------

    def search(self, typed: str, units: frozenset[str] | None = None, most: int = SEARCH_MOST) -> list[list[str]]:
        """The offered accounts whose account string, description or business unit's name has every word ``typed``
        ([[account string, description, business unit's name], ...], at most ``most``): a word of digits is found at
        the start of the business unit, the object or the subsidiary (or the string with its dots); another, in the
        description or the business unit's name. ``units``: one company's business units alone (D-267)."""
        words = [w for w in typed.replace(".", " ").upper().split() if w][:6]
        if not words:
            return []
        names = self._unit_facts()[0]
        clauses, params = [], []
        for word in words:
            if any(c.isdigit() for c in word):
                clauses.append(
                    "(bu LIKE ? OR object LIKE ? OR subsidiary LIKE ? OR object || subsidiary LIKE ? OR "
                    "bu || object || subsidiary LIKE ?)"
                )  # (typed without its dots: "14509309")
                params += [f"{word}%"] * 5
            else:
                named = sorted(bu for bu, name in names.items() if word in name.upper())[:500]
                marks = ", ".join("?" * len(named)) or "''"
                clauses.append(f"(UPPER(description) LIKE ? OR bu IN ({marks}))")
                params += [f"%{word}%", *named]
        if units is not None:  # (one company's: its business units', by the chart's key)
            clauses.append("bu IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(sorted(units)))
        out: list[list[str]] = []
        sql = (f"SELECT bu, object, subsidiary, description, posting_edit FROM chart_accounts WHERE object != '' AND "
               f"{' AND '.join(clauses)} ORDER BY bu, object, subsidiary LIMIT ?")  # fmt: skip
        with self._reader() as conn:
            rows = conn.execute(sql, (*params, most * 20)).fetchall()
        for bu, obj, sub, description, edit in rows:
            code = code_of(obj, sub)
            if self._status(bu, code, (description, edit)) == OK:
                out.append([f"{bu}.{code}", description, names.get(bu, "")])
                if len(out) >= most:
                    break
        return out
