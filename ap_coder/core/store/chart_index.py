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
without holding them): ``Store.account_pairs`` gives one, kept with the reference data. Each thread asks on a
connection of its own, kept open; ``close`` closes them all (the reference data built again, a restore: Windows
refuses to replace or remove a database file a connection holds, B-2653).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import weakref
from collections import Counter, OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..chart import (
    ACTIVE,
    BU_INACTIVE,
    BU_UNKNOWN,
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
from . import dialect
from ._base import _held_connections, _in_list

ACCOUNTS_KEPT = 20_000  # the accounts asked lately, each kept with its description and posting edit code
SEARCH_MOST = 50  # an Account box's search: the accounts it draws at most (more are found by typing more)
CODE_INDEX = "CREATE INDEX IF NOT EXISTS chart_accounts_code ON chart_accounts (object, subsidiary)"
UNIT_COMPANY_INDEX = "CREATE INDEX IF NOT EXISTS business_units_company ON business_units (company)"


def _globbed(patterns: tuple[str, ...]) -> tuple[str, list[str]] | None:
    """The accounts never offered (``chart._never_offered``'s patterns) as SQL: (the condition, its values), each
    pattern matched against the object, the ``object.subsidiary`` or the whole string as its dots say, by GLOB
    (``*`` and ``?`` as ``fnmatch``'s; ``[!...]`` as GLOB's ``[^...]``); None for no pattern."""
    if not patterns:
        return None
    told = ("object", "object || '.' || subsidiary", "bu || '.' || object || '.' || subsidiary")
    return (" OR ".join(f"{told[min(p.count('.'), 2)]} GLOB ?" for p in patterns),
            [p.replace("[!", "[^") for p in patterns])  # fmt: skip


class _Own:
    """A thread's own connection to the chart (``IndexedPairs._reader``): how many of its questions are asked on it
    now, and whether ``close`` was asked meanwhile (then it is closed once they are answered)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn: sqlite3.Connection | None = conn
        self.asking = 1
        self.closing = False


class IndexedPairs:
    """The chart of ``store`` as the checks and the suggestions ask it (``AccountPairs``' answers), with its settings
    ``settings``. Shared by the threads of a process: read it, don't change it."""

    def __init__(self, store: Any, settings: ChartSettings | None = None) -> None:
        self.settings = settings or ChartSettings()
        self._store = store
        self._lock = threading.Lock()
        self._local = threading.local()  # (each thread's own connection, when it holds no session)
        # Every thread's own connection open now, for ``close`` (one of a thread that has ended is closed with it).
        self._own: weakref.WeakSet[_Own] = weakref.WeakSet()
        self._kept: OrderedDict[tuple[str, str], tuple[str, str, str] | None] = OrderedDict()
        self._units: tuple[dict[str, str], set[str], dict[str, str], dict[str, str], set[str]] | None = None
        self._offered: tuple[frozenset[str], tuple[str, ...]] | None = None
        self._of_company: dict[str, frozenset[str]] | None = None
        self._searched: tuple[frozenset[str], frozenset[str]] | None = None
        self._tally: Counter[str] | None = None

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
        with self._lock:
            own = getattr(self._local, "own", None)
            conn = own.conn if own is not None else None
            if own is not None and conn is not None:
                own.asking += 1
        if own is None or conn is None:  # this thread's first question, or the first since ``close``
            conn = self._store._open(any_thread=True)  # (``close`` closes it from whichever thread)
            with self._lock:
                own = self._local.own = _Own(conn)
                self._own.add(own)
        try:
            yield conn
        finally:
            with self._lock:
                own.asking -= 1
                done = own.conn if own.closing and not own.asking else None
                if done is not None:
                    own.conn, own.closing = None, False
                    self._own.discard(own)
            if done is not None:
                done.close()

    def close(self) -> None:
        """Close the connection each thread opened to ask the chart; one a question is asked on now is closed by its
        thread once the question is answered. Then nothing of it holds the database file open. A question asked
        after opens one again (an invoice being checked with the reference data this belonged to, replaced
        meanwhile, still finishes); what was read once (the business units, the codes offered) is kept."""
        idle: list[sqlite3.Connection] = []
        with self._lock:
            for own in list(self._own):
                if own.asking:
                    own.closing = True
                elif own.conn is not None:
                    idle.append(own.conn)
                    own.conn = None
                    self._own.discard(own)
        for conn in idle:
            conn.close()

    # --- What is read once: the business units, and the codes and units offered ------------------------------------

    def _unit_facts(self) -> tuple[dict[str, str], set[str], dict[str, str], dict[str, str], set[str]]:
        """({business unit: name}, the closed ones, {business unit: company}, {business unit: skip word}, the business
        units of the business units report): the master's and each business unit's title row in the chart, read
        once."""
        if self._units is None:
            names: dict[str, str] = {}
            closed: set[str] = set()
            companies: dict[str, str] = {}
            listed: set[str] = set()
            with self._reader() as conn:
                for bu, description in conn.execute("SELECT bu, description FROM chart_accounts WHERE object = '' "
                                                     "AND subsidiary = '' ORDER BY bu"):  # fmt: skip
                    names.setdefault(bu, description)
                for code, name, company, status in conn.execute("SELECT code, name, company, status FROM "
                                                                "business_units"):  # fmt: skip
                    listed.add(code)
                    if name:
                        names[code] = name
                    if company:
                        companies[code] = company_code(company)
                    if status not in ACTIVE:
                        closed.add(code)
            skipped = {bu: word for bu, name in names.items() if (word := skip_word(name, self.settings.skip_words))}
            self._units = (names, closed, companies, skipped, listed)
        return self._units

    def _unit_held(self, bu: str, company: str) -> bool:
        """Whether nothing on business unit ``bu`` is offered, whatever the account (its own ``company`` given): it is
        closed, named to be skipped, not in the business units report, or of a company held back (D-272)."""
        _names, closed, companies, skipped, listed = self._unit_facts()
        return (bu in closed or bu in skipped or bool(listed and bu not in listed)
                or bool(self.settings.company_held(companies.get(bu) or company)))  # fmt: skip

    def _offered_once(self) -> tuple[frozenset[str], tuple[str, ...]]:
        """(the GL codes offered on a business unit at least, the business units with an account offered), of one
        pass over the accounts that may be posted to, holding the two sets alone."""
        if self._offered is None:
            patterns = self.settings.held_back
            codes: set[str] = set()
            units: set[str] = set()
            sql = (f"SELECT bu, object, subsidiary, company FROM chart_accounts WHERE object != '' AND "
                   f"{_in_list('posting_edit')}")  # fmt: skip
            held: dict[tuple[str, str], bool] = {}
            with self._reader() as conn:
                for bu, obj, sub, company in conn.execute(sql, (json.dumps(list(self.settings.postable_codes)),)):
                    if (bu, company) not in held:
                        held[(bu, company)] = self._unit_held(bu, company)
                    if held[(bu, company)]:
                        continue
                    code = code_of(obj, sub)
                    if patterns and _never_offered(patterns, bu, code):
                        continue
                    codes.add(code)
                    units.add(bu)
            self._offered = (frozenset(codes), tuple(sorted(units)))
        return self._offered

    # --- One account ------------------------------------------------------------------------------------------------

    def _found(self, business_unit: Any, code: Any) -> tuple[str, str, tuple[str, str, str] | None]:
        """(the business unit, the GL code, (its description, its posting edit code, its company) or None: no such
        account)."""
        bu, code = _code(business_unit), _gl(code)
        key = (bu, code)
        with self._lock:
            if key in self._kept:
                self._kept.move_to_end(key)
                return bu, code, self._kept[key]
        obj, sub = split_code(code)
        with self._reader() as conn:
            row = conn.execute("SELECT description, posting_edit, company FROM chart_accounts WHERE bu = ? AND "
                               "object = ? AND subsidiary = ?", (bu, obj, sub)).fetchone() if obj else None  # fmt: skip
        found = (row[0], row[1], row[2]) if row is not None else None
        with self._lock:
            self._kept[key] = found
            while len(self._kept) > ACCOUNTS_KEPT:
                self._kept.popitem(last=False)
        return bu, code, found

    def _status(self, bu: str, code: str, found: tuple[str, str, str] | None) -> str:
        facts = self._unit_facts()
        if bu in facts[1]:
            return BU_INACTIVE
        if found is None:
            return NOT_IN_CHART
        if found[1] not in self.settings.postable_codes:
            return NOT_POSTABLE
        if facts[4] and bu not in facts[4]:
            return BU_UNKNOWN
        return RESERVED if self._why(bu, code, found[2]) else OK

    def _why(self, bu: str, code: str, company: str) -> str:
        pattern = _never_offered(self.settings.held_back, bu, code)
        if pattern:
            return self.settings.why_held(pattern)
        word = self._unit_facts()[3].get(bu)
        if word:
            return f"the name of business unit {bu} says {word}"
        held = self.settings.company_held(self._unit_facts()[2].get(bu) or company)
        return f"business unit {bu}: {held}" if held else ""

    def status(self, business_unit: Any, code: Any) -> str:
        """``AccountPairs.status``: ``OK``, ``BU_INACTIVE``, ``NOT_IN_CHART``, ``NOT_POSTABLE``, ``BU_UNKNOWN`` or
        ``RESERVED``."""
        return self._status(*self._found(business_unit, code))

    def why_reserved(self, business_unit: Any, code: Any) -> str:
        bu, code, found = self._found(business_unit, code)
        return self._why(bu, code, found[2]) if found is not None else ""

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
            rows = conn.execute("SELECT object, subsidiary, description, posting_edit, company FROM chart_accounts "
                                "WHERE bu = ? AND object != ''", (bu,)).fetchall()  # fmt: skip
        out = []
        for obj, sub, description, edit, company in rows:
            code = code_of(obj, sub)
            if self._status(bu, code, (description, edit, company)) == OK:
                out.append(code)
        return sorted(out)

    def bus_of(self, code: Any) -> tuple[str, ...]:
        """The business units a GL code is offered on, in order (``chart_accounts_code``; what may be posted to and is
        not closed told by SQLite, the units held back for their name and the accounts never offered here)."""
        code = _gl(code)
        obj, sub = split_code(code)
        if not obj:
            return ()
        patterns = self.settings.held_back
        closed = f"SELECT code FROM business_units WHERE status NOT IN ({', '.join('?' * len(ACTIVE))})"
        sql = (f"SELECT bu, company FROM chart_accounts WHERE object = ? AND subsidiary = ? AND "
               f"{_in_list('posting_edit')} AND bu NOT IN ({closed}) ORDER BY bu")  # fmt: skip
        postable = json.dumps(list(self.settings.postable_codes))
        with self._reader() as conn:
            rows = conn.execute(sql, (obj, sub, postable, *ACTIVE)).fetchall()
        return tuple(
            bu
            for bu, company in rows
            if not self._unit_held(bu, company) and not (patterns and _never_offered(patterns, bu, code))
        )

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
        """``AccountPairs.units_of``: the companies as they compare (``company_code``: report 2 read through Excel
        gives 100 for 00100, B-2743), read once."""
        if self._of_company is None:
            found: dict[str, set[str]] = {}
            for bu, co in self._unit_facts()[2].items():
                found.setdefault(co, set()).add(bu)
            self._of_company = {co: frozenset(units) for co, units in found.items()}
        return self._of_company.get(company_code(company), frozenset())

    def closed_units(self) -> list[str]:
        return sorted(self._unit_facts()[1])

    def skipped_units(self) -> dict[str, str]:
        return dict(sorted(self._unit_facts()[3].items()))

    def unlisted_units(self, most: int) -> list[str]:
        if not self._unit_facts()[4]:
            return []
        with self._reader() as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT bu FROM chart_accounts WHERE object != '' AND bu NOT IN "
                    "(SELECT code FROM business_units) ORDER BY bu LIMIT ?",
                    (most,),
                )
            ]

    def __len__(self) -> int:
        with self._reader() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM chart_accounts WHERE object != ''").fetchone()[0])

    def tally(self) -> Counter[str]:
        """How many of the chart's accounts are of each status: counted by SQLite by business unit, company and
        whether the posting edit code is one AP posts to, the accounts never offered by their patterns as SQLite's
        GLOB (``fnmatch``'s own wildcards), never a row at a time (D-272: 3,500,000 accounts in 3 s), once for the
        chart and its settings (a page shows it at each view)."""
        if self._tally is not None:
            return Counter(self._tally)
        out: Counter[str] = Counter()
        _names, closed, companies, skipped, listed = self._unit_facts()
        postable = json.dumps(list(self.settings.postable_codes))
        open_units: Counter[tuple[str, str]] = Counter()  # (business unit, company): accounts that may be offered
        with self._reader() as conn:
            for bu, company, edit_ok, n in conn.execute(
                f"SELECT bu, company, {_in_list('posting_edit')}, COUNT(*) FROM chart_accounts WHERE object != '' "
                "GROUP BY bu, company, 3", (postable,)):  # fmt: skip
                if bu in closed:
                    out[BU_INACTIVE] += n
                elif not edit_ok:
                    out[NOT_POSTABLE] += n
                elif listed and bu not in listed:
                    out[BU_UNKNOWN] += n
                elif bu in skipped or self.settings.company_held(companies.get(bu) or company):
                    out[RESERVED] += n
                else:
                    open_units[(bu, company)] += n
            matched = _globbed(self.settings.held_back)
            if matched:
                for bu, company, n in conn.execute(
                    f"SELECT bu, company, COUNT(*) FROM chart_accounts WHERE object != '' AND "
                    f"{_in_list('posting_edit')} AND ({matched[0]}) GROUP BY bu, company",
                    (postable, *matched[1]),
                ):
                    if (bu, company) in open_units:
                        open_units[(bu, company)] -= n
                        out[RESERVED] += n
        out[OK] += sum(open_units.values())
        self._tally = +out
        return Counter(self._tally)

    def _search_units(self) -> tuple[frozenset[str], frozenset[str]]:
        """The business units of the business units report an account may be offered on, for the search (read once):
        (those whose company it gives, held back or not by it, those it gives none: their account's own company
        decides). Both empty without the report."""
        if self._searched is None:
            _names, closed, companies, skipped, listed = self._unit_facts()
            open_units = [bu for bu in listed if bu not in closed and bu not in skipped]
            self._searched = (
                frozenset(bu for bu in open_units if bu in companies and not self.settings.company_held(companies[bu])),
                frozenset(bu for bu in open_units if bu not in companies),
            )
        return self._searched

    def _company_clause(self) -> tuple[str, list[str]] | None:
        """``ChartSettings.company_held`` as SQL of an account's own company (its business unit's not known), and its
        values; None when no company is held back."""
        if self.settings.pilot:
            return _in_list("company"), [json.dumps(list(self.settings.pilot))]
        if self.settings.companies:
            return f"(company = '' OR {_in_list('company')})", [json.dumps(list(self.settings.companies))]
        return None

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
        skipped, listed = self._unit_facts()[3], self._unit_facts()[4]
        # Every rule of what is offered asked of SQLite with the words (D-272, B-2741): the accounts held back are
        # half a group's chart and more, so none is read to be dropped. With the business units report, the business
        # units offered on (``_search_units``); without it, those named to be skipped left out. Then the account's
        # own company, its posting edit code and the accounts never offered.
        company = self._company_clause()
        clauses, params = [], []
        searched: frozenset[str] | None = None
        if listed:
            known, unknown = self._search_units()
            searched = known | unknown if units is None else (known | unknown) & units
            clauses.append(_in_list("bu"))
            params.append(json.dumps(sorted(searched)))
            if company is not None and unknown:
                clauses.append(f"(NOT {_in_list('bu')} OR {company[0]})")
                params += [json.dumps(sorted(unknown & searched)), *company[1]]
        else:
            if units is not None:  # (one company's: its business units', by the chart's key)
                searched = units - skipped.keys()
                clauses.append(_in_list("bu"))
                params.append(json.dumps(sorted(searched)))
            elif skipped:
                clauses.append(f"NOT {_in_list('bu')}")
                params.append(json.dumps(sorted(skipped)))
            if company is not None:
                clauses.append(company[0])
                params += company[1]
        for word in words:
            if any(c.isdigit() for c in word):
                # (the last two: typed without its dots, "14509309")
                starts = ("bu", "object", "subsidiary", "object || subsidiary", "bu || object || subsidiary")
                clauses.append(f"({' OR '.join(dialect.like(s) for s in starts)})")
                params += [f"{word}%"] * len(starts)
            else:  # (of the business units searched alone, every one so named: B-2742)
                named = sorted(bu for bu, name in names.items() if word in name.upper()
                               and (searched is None or bu in searched))  # fmt: skip
                clauses.append(f"({dialect.like('description')} OR {_in_list('bu')})")
                params += [f"%{word}%", json.dumps(named)]
        clauses.append(_in_list("posting_edit"))
        params.append(json.dumps(list(self.settings.postable_codes)))
        matched = _globbed(self.settings.held_back)
        if matched:
            clauses.append(f"NOT ({matched[0]})")
            params += matched[1]
        out: list[list[str]] = []
        sql = (f"SELECT bu, object, subsidiary, description, posting_edit, company FROM chart_accounts WHERE object != "
               f"'' AND {' AND '.join(clauses)} ORDER BY bu, object, subsidiary LIMIT ?")  # fmt: skip
        with self._reader() as conn:
            rows = conn.execute(sql, (*params, most)).fetchall()
        for bu, obj, sub, description, edit, company_of in rows:
            code = code_of(obj, sub)
            if self._status(bu, code, (description, edit, company_of)) == OK:
                out.append([f"{bu}.{code}", description, names.get(bu, "")])
        return out
