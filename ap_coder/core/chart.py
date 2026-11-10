"""One JD Edwards account string, ``business unit.object.subsidiary`` (D-209).

A line keeps its two codes as before: the cost center is the business unit and the GL code is the object, or
``object.subsidiary`` when the account has one. This module alone joins them into the string a person types into
JD Edwards ("0880133142.6215.1170") and splits such a string back, so nothing else has to know the order or the dot.

It also knows the chart of accounts as JD Edwards exports it: the rows of the account master and of the business unit
master made clean (``chart_rows``, ``bu_rows``), the chart's settings (``ChartSettings``), and which pairs exist and
may be posted to (``AccountPairs``: what the checks and the suggestions ask). The store keeps the rows
(``core/store/chart.py``); the files are read by ``core/chart_file.py``.

Entry points: ``join_account``, ``split_account``, ``split_code`` and ``clean``; ``chart_rows``, ``bu_rows``,
``account_lists``, ``ChartSettings``, ``AccountPairs``.
"""

from __future__ import annotations

import fnmatch
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .headers import match_columns

SEPARATOR = "."


def clean(value: Any) -> str:
    """A code as the store keeps it: the padding of an export (leading spaces, a lone space for blank) gone."""
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def code_of(obj: Any, subsidiary: Any = "") -> str:
    """The GL code of an object and its subsidiary: "6215.1170", or "3005" when there is no subsidiary."""
    obj, subsidiary = clean(obj), clean(subsidiary)
    return f"{obj}{SEPARATOR}{subsidiary}" if obj and subsidiary else obj


def split_code(code: Any) -> tuple[str, str]:
    """A GL code as (object, subsidiary): "6215.1170" is ("6215", "1170"), "3005" is ("3005", "")."""
    obj, _, subsidiary = clean(code).partition(SEPARATOR)
    return obj.strip(), subsidiary.strip()


def join_account(business_unit: Any, code: Any) -> str:
    """The account string of a business unit and a GL code; the code alone when there is no business unit."""
    business_unit, code = clean(business_unit), code_of(*split_code(code))
    return f"{business_unit}{SEPARATOR}{code}" if business_unit and code else business_unit or code


def split_account(text: Any) -> tuple[str, str]:
    """An account string as (business unit, GL code), split at its first dot: "  0880133142.6215.1170" is
    ("0880133142", "6215.1170"). With no dot the text is the business unit and the code is blank: whether a short
    text is a business unit or an account is for the caller, who knows the chart, to say."""
    business_unit, _, code = clean(text).partition(SEPARATOR)
    return business_unit.strip(), code_of(*split_code(code))


# --- The chart's settings ----------------------------------------------------------------------------------------

# The posting edit codes of an account AP may post to: blank alone (a title, an inactive or a budget-only account, a
# machine-only one or one that wants a subledger has a letter). CONFIRM with the JD Edwards team before adding one.
POSTABLE = ("",)
HEADER_EDIT = "N"  # the posting edit code of a title account: the accounts under it roll up into it
# The business unit master's status of a business unit that takes postings; blank: the file gives no status.
ACTIVE = ("ACT", "")
SKIP_WORDS = ("DO NOT USE", "DNU")  # a business unit (or a supplier) whose name carries one is never offered
LABELS = ("Business unit", "Object", "Subsidiary")  # the three parts' names (job costing: job, cost type, cost code)
# The settings' keys (plain ``settings`` rows: ``ChartSettings.read``), and the count of the chart's imports the
# reference data is kept by (``Store.account_pairs``).
SETTING_KEYS = ("postable_codes", "never_offered", "skip_words", "home_business_unit", "account_labels")
VERSION_KEY = "chart_version"


def _listed(text: Any) -> tuple[str, ...]:
    """A setting's list as typed: one a line, or commas or semicolons between; each once, in order."""
    return tuple(dict.fromkeys(part.strip() for part in re.split(r"[\n,;]+", str(text or "")) if part.strip()))


@dataclass(frozen=True)
class ChartSettings:
    """How the chart is used. ``postable_codes``: the posting edit codes AP may post to (blank always is; the setting
    names any others). ``never_offered``: patterns of accounts that exist and may be posted to but are never
    suggested or listed (``*``: any characters): an object alone ("4110": that object, whatever its subsidiary),
    ``object.subsidiary`` ("*.8801": that subsidiary of any object), or a whole string ("0880133142.*.*"). A line
    coded to one by hand is a warning. ``skip_words``: a business unit whose name carries one is not offered either.
    ``home_business_unit``: where a line is suggested to go when nothing says otherwise. ``account_labels``: what
    the three parts are called."""

    postable_codes: tuple[str, ...] = POSTABLE
    never_offered: tuple[str, ...] = ()
    skip_words: tuple[str, ...] = SKIP_WORDS
    home_business_unit: str = ""
    account_labels: tuple[str, ...] = LABELS

    @classmethod
    def read(cls, saved: Mapping[str, str]) -> ChartSettings:
        """The settings as saved (``SETTING_KEYS``: text, a list one a line or with commas); one not saved, or left
        blank, is the default."""
        labels = _listed(saved.get("account_labels"))
        return cls(
            postable_codes=tuple(dict.fromkeys(("", *(c.upper() for c in _listed(saved.get("postable_codes")))))),
            never_offered=tuple(p.upper() for p in _listed(saved.get("never_offered"))),
            skip_words=_listed(saved.get("skip_words")) or SKIP_WORDS,
            home_business_unit=clean(saved.get("home_business_unit")),
            account_labels=labels if len(labels) == len(LABELS) else LABELS,
        )

    def saved(self) -> dict[str, str]:
        """The settings as text, as ``read`` takes them back."""
        return {
            "postable_codes": ", ".join(c for c in self.postable_codes if c),
            "never_offered": ", ".join(self.never_offered),
            "skip_words": ", ".join(self.skip_words),
            "home_business_unit": self.home_business_unit,
            "account_labels": ", ".join(self.account_labels),
        }


def skip_word(name: Any, words: Iterable[str] = SKIP_WORDS) -> str:
    """The first of ``words`` the name carries as words of its own ("****DO NOT USE****", "Old yard DNU"), whatever
    the case and the spaces; "" when it carries none."""
    text = str(name or "")
    for word in words:
        pattern = r"\s+".join(re.escape(part) for part in word.split())
        if pattern and re.search(rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])", text, re.IGNORECASE):
            return word
    return ""


def _never_offered(patterns: Iterable[str], business_unit: str, code: str) -> str:
    """The first of the never-offered ``patterns`` the account matches ("" when none): see ``ChartSettings``."""
    obj, subsidiary = split_code(code.upper())
    told = (obj, f"{obj}.{subsidiary}", f"{business_unit.upper()}.{obj}.{subsidiary}")
    for pattern in patterns:
        if fnmatch.fnmatchcase(told[min(pattern.count(SEPARATOR), 2)], pattern):
            return pattern
    return ""


# --- The two exports' rows ---------------------------------------------------------------------------------------

# The columns of the account master's export (JD Edwards' own headers first), for ``headers.match_columns``.
CHART_ALIASES: dict[str, tuple[str, ...]] = {
    "business_unit": ("Business Unit", "BU", "Cost Center", "MCU"),
    "object": ("Object Account", "Object", "Obj Acct", "OBJ"),
    "subsidiary": ("Subsidiary", "Sub"),
    "description": ("Account Description", "Description", "DL01"),
    "lod": ("L D", "LOD", "Level of Detail", "LDA"),
    "posting_edit": ("P E", "Posting Edit", "Posting Edit Code", "PEC"),
}
# The columns of the business unit master's export.
BU_ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("Job Number", "Business Unit", "BU", "Cost Center", "MCU"),
    "name": ("Job Name", "Business Unit Description", "Description", "Name", "DL01"),
    "company": ("Co", "Company"),
    "type": ("BU Ty", "BU Type", "Business Unit Type"),
    "status": ("Job Sts", "Job Status", "Status"),
}
CHART_NEEDS = ("business_unit", "object")  # the columns a chart cannot be read without
BU_NEEDS = ("code",)


class ChartError(ValueError):
    """An export that cannot be read as the chart or the business unit master; the message says why, in plain
    words."""


@dataclass(frozen=True)
class ChartRow:
    """One row of the chart: an account of a business unit (``code``: its GL code), or the business unit's own header
    row, which has no object. ``category``: the title account it sits under."""

    bu: str
    object: str
    subsidiary: str = ""
    description: str = ""
    lod: int = 0
    posting_edit: str = ""
    category: str = ""

    @property
    def code(self) -> str:
        return code_of(self.object, self.subsidiary)

    @property
    def is_header(self) -> bool:
        return not self.object


@dataclass(frozen=True)
class BuRow:
    """One business unit of the business unit master."""

    code: str
    name: str = ""
    company: str = ""
    type: str = ""
    status: str = ""


@dataclass
class Rows:
    """An export's rows as cleaned (``ChartRow`` or ``BuRow``), and the rows left out: {why: how many}."""

    rows: list[Any] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1


def columns_of(headers: Iterable[Any], aliases: dict[str, tuple[str, ...]], needs: Iterable[str]) -> dict[str, str]:
    """{field: the file's column} of an export's ``headers``; ``ChartError`` when a column of ``needs`` is not
    there, naming the headers looked for and the first of those found."""
    headers = [str(h) for h in headers if h is not None and str(h).strip()]
    found = match_columns(headers, aliases)
    missing = [aliases[name][0] for name in needs if name not in found]
    if missing:
        shown = ", ".join(headers[:8]) + (" …" if len(headers) > 8 else "")
        raise ChartError(
            f"This file has no column named {' or '.join(f'“{m}”' for m in missing)}, so it was not read"
            + (f" (its columns: {shown})." if headers else " (its first row has no column names).")
            + " Export it from JD Edwards again and use the file as it comes."
        )
    return found


def chart_columns(headers: Iterable[Any]) -> dict[str, str]:
    """{field: the file's column} of the account master's export (``CHART_ALIASES``)."""
    return columns_of(headers, CHART_ALIASES, CHART_NEEDS)


def bu_columns(headers: Iterable[Any]) -> dict[str, str]:
    """{field: the file's column} of the business unit master's export (``BU_ALIASES``). The chart itself, which
    has a business unit column too, is refused: read as the master it would call every business unit active."""
    headers = [str(h) for h in headers if h is not None]
    if "object" in match_columns(headers, CHART_ALIASES):
        raise ChartError(
            "This file has an object account column: it is the chart of accounts, not the business unit master, so "
            "it was not read as the business units."
        )
    return columns_of(headers, BU_ALIASES, BU_NEEDS)


def _whole(value: Any) -> int:
    """A level of detail as a number (0 when the cell holds none)."""
    try:
        return int(float(clean(value) or 0))
    except ValueError:
        return 0


def _words(value: Any) -> str:
    """A description without its indentation, one space between its words."""
    return " ".join(clean(value).split())


def chart_rows(records: Iterable[Mapping[str, Any]], columns: Mapping[str, str] | None = None) -> Rows:
    """The account master's export as clean rows, in the file's order. ``records``: each row as {header: cell};
    ``columns``: {field: header} (found from the first row's headers when not given, ``CHART_ALIASES``). A business
    unit loses its padding, a lone space is a blank, codes stay text (leading zeros, letters), a description loses
    its indentation. A row with no object is its business unit's header. Each row's ``category`` is the title
    account above it (posting edit code N, a lower level of detail), which says what the description, cut at 30
    characters, no longer does."""
    out = Rows()
    seen: set[tuple[str, str, str]] = set()
    titles: list[tuple[int, str]] = []  # the title accounts above this row in its business unit: (level, description)
    last_bu = None
    for record in records:
        if columns is None:
            columns = chart_columns(record)
        cell = {name: record.get(column) for name, column in columns.items()}
        bu, obj, subsidiary = clean(cell["business_unit"]), clean(cell["object"]), clean(cell.get("subsidiary"))
        if not bu:
            out.skip("blank rows" if not obj and not subsidiary else "rows with no business unit")
            continue
        if subsidiary and not obj:
            out.skip("rows with a subsidiary but no object account")
            continue
        if (bu, obj, subsidiary) in seen:
            out.skip("rows repeating an account already read")
            continue
        seen.add((bu, obj, subsidiary))
        if bu != last_bu:
            titles, last_bu = [], bu
        lod, edit = _whole(cell.get("lod")), clean(cell.get("posting_edit")).upper()
        description = _words(cell.get("description"))
        while lod and titles and titles[-1][0] >= lod:
            titles.pop()
        category = titles[-1][1] if lod and titles else ""
        if lod and obj and edit == HEADER_EDIT:
            titles.append((lod, description))
        out.rows.append(ChartRow(bu, obj, subsidiary, description, lod, edit, category))
    return out


def bu_rows(records: Iterable[Mapping[str, Any]], columns: Mapping[str, str] | None = None) -> Rows:
    """The business unit master's export as clean rows (``BU_ALIASES``): the business unit without its padding, its
    name, company, type and status (upper case: ACT, INA)."""
    out = Rows()
    seen: set[str] = set()
    for record in records:
        if columns is None:
            columns = bu_columns(record)
        cell = {name: record.get(column) for name, column in columns.items()}
        code = clean(cell["code"])
        if not code:
            out.skip("rows with no business unit" if _words(cell.get("name")) else "blank rows")
            continue
        if code in seen:
            out.skip("rows repeating a business unit already read")
            continue
        seen.add(code)
        out.rows.append(BuRow(code, _words(cell.get("name")), clean(cell.get("company")),
                              clean(cell.get("type")).upper(), clean(cell.get("status")).upper()))  # fmt: skip
    return out


def _commonest(counts: Counter[str]) -> str:
    """The text counted most often, a blank one only when there is no other (of two as common, the first met)."""
    filled = [(text, n) for text, n in counts.items() if text]
    return max(filled, key=lambda found: found[1])[0] if filled else ""


def account_lists(
    rows: Iterable[ChartRow], units: Iterable[BuRow] = (), settings: ChartSettings | None = None
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """The two lists every other part of AP Coder reads, worked out from the chart: the GL accounts {code:
    (description, category)} and the cost centers {business unit: name}. A cost center is a business unit that is
    active (or not in the business unit master) and has an account that may be posted to; a GL account is the
    ``object.subsidiary`` of such an account, with the description and the title account it has on most business
    units. A never-offered account and a business unit named DO NOT USE stay in the lists: they exist, and a line
    coded to one by hand is checked (``AccountPairs.status``)."""
    settings = settings or ChartSettings()
    names: dict[str, str] = {}
    closed: set[str] = set()
    for unit in units:
        names[unit.code] = unit.name
        if unit.status not in ACTIVE:
            closed.add(unit.code)
    described: dict[str, Counter[str]] = {}
    titled: dict[str, Counter[str]] = {}
    headers: dict[str, str] = {}
    centers: set[str] = set()
    for row in rows:
        if row.is_header:
            headers.setdefault(row.bu, row.description)
        elif row.posting_edit in settings.postable_codes and row.bu not in closed:
            centers.add(row.bu)
            described.setdefault(row.code, Counter())[row.description] += 1
            titled.setdefault(row.code, Counter())[row.category] += 1
    accounts = {code: (_commonest(described[code]), _commonest(titled[code])) for code in sorted(described)}
    return accounts, {bu: names.get(bu) or headers.get(bu, "") for bu in sorted(centers)}


# --- Which pairs exist and may be posted to ----------------------------------------------------------------------

OK, NOT_IN_CHART, NOT_POSTABLE, BU_INACTIVE, RESERVED = "ok", "not_in_chart", "not_postable", "bu_inactive", "reserved"


class AccountPairs:
    """The chart as the checks and the suggestions ask it: which GL code exists on which business unit, and whether a
    line may be coded to the pair (``status``). Built from the chart's rows, the business unit master's and the
    settings; read it, don't change it."""

    def __init__(self, rows: Iterable[ChartRow], units: Iterable[BuRow] = (),
                 settings: ChartSettings | None = None) -> None:  # fmt: skip
        self.settings = settings or ChartSettings()
        self._accounts: dict[str, dict[str, tuple[str, str]]] = {}  # business unit: {code: (description, edit code)}
        self._names: dict[str, str] = {}
        self._closed: set[str] = set()  # the business units the master says take no postings
        for row in rows:
            if row.is_header:
                self._names.setdefault(row.bu, row.description)
            else:
                self._accounts.setdefault(row.bu, {})[row.code] = (row.description, row.posting_edit)
        for unit in units:
            if unit.name:
                self._names[unit.code] = unit.name
            if unit.status not in ACTIVE:
                self._closed.add(unit.code)
        # The business units held back for their name, with the word; then, of each code, the business units it is
        # offered on.
        self._skipped = {bu: word for bu, name in self._names.items()
                         if (word := skip_word(name, self.settings.skip_words))}  # fmt: skip
        offered: dict[str, list[str]] = {}
        for bu in sorted(self._accounts):
            for code in self._accounts[bu]:
                if self.status(bu, code) == OK:
                    offered.setdefault(code, []).append(bu)
        self._offered = {code: tuple(found) for code, found in offered.items()}
        self._codes = frozenset(self._offered)

    def __len__(self) -> int:
        return sum(len(codes) for codes in self._accounts.values())

    def tally(self) -> Counter[str]:
        """How many of the chart's accounts are of each status (``OK``, ``RESERVED``...)."""
        return Counter(self.status(bu, code) for bu, codes in self._accounts.items() for code in codes)

    def closed_units(self) -> list[str]:
        """The business units the business unit master says are not active, in order."""
        return sorted(self._closed)

    def skipped_units(self) -> dict[str, str]:
        """The business units never offered for their name: {business unit: the word its name carries}."""
        return dict(sorted(self._skipped.items()))

    def _found(self, business_unit: Any, code: Any) -> tuple[str, str, tuple[str, str] | None]:
        bu, code = clean(business_unit), code_of(*split_code(code))
        return bu, code, self._accounts.get(bu, {}).get(code)

    def status(self, business_unit: Any, code: Any) -> str:
        """What a line coded to the pair is: ``OK``; ``BU_INACTIVE`` (the business unit master says the business unit
        takes no postings); ``NOT_IN_CHART`` (that business unit has no such account); ``NOT_POSTABLE`` (a title, an
        inactive or a machine-only account: its posting edit code); ``RESERVED`` (it may be posted to but is never
        offered: ``why_reserved``)."""
        bu, code, found = self._found(business_unit, code)
        if bu in self._closed:
            return BU_INACTIVE
        if found is None:
            return NOT_IN_CHART
        if found[1] not in self.settings.postable_codes:
            return NOT_POSTABLE
        return RESERVED if self.why_reserved(bu, code) else OK

    def why_reserved(self, business_unit: Any, code: Any) -> str:
        """Why the pair is never offered, in words for a finding ("" when nothing holds it back)."""
        bu, code, _ = self._found(business_unit, code)
        pattern = _never_offered(self.settings.never_offered, bu, code)
        if pattern:
            return f"it is one of the accounts never offered ({pattern})"
        word = self._skipped.get(bu)
        return f"the name of business unit {bu} says {word}" if word else ""

    def offered(self, business_unit: Any, code: Any) -> bool:
        """Whether the pair may be suggested or listed: it exists, may be posted to and nothing holds it back."""
        return self.status(business_unit, code) == OK

    def accounts_of(self, business_unit: Any) -> list[str]:
        """The GL codes offered on a business unit, in order."""
        bu = clean(business_unit)
        return sorted(code for code in self._accounts.get(bu, ()) if bu in self._offered.get(code, ()))

    def bus_of(self, code: Any) -> tuple[str, ...]:
        """The business units a GL code is offered on, in order."""
        return self._offered.get(code_of(*split_code(code)), ())

    def offered_codes(self) -> frozenset[str]:
        """The GL codes offered on at least one business unit."""
        return self._codes

    def business_units(self) -> list[str]:
        """The business units with an account offered, in order."""
        return sorted({bu for found in self._offered.values() for bu in found})

    def has_business_unit(self, business_unit: Any) -> bool:
        """Whether the chart or the business unit master knows the business unit."""
        bu = clean(business_unit)
        return bu in self._accounts or bu in self._names or bu in self._closed

    def has_code(self, code: Any) -> bool:
        """Whether any business unit has the GL code, one that may be posted to or not."""
        code = code_of(*split_code(code))
        return any(code in codes for codes in self._accounts.values())

    def description(self, business_unit: Any, code: Any) -> str:
        """The account's description on that business unit ("" when it has no such account)."""
        found = self._found(business_unit, code)[2]
        return found[0] if found else ""

    def posting_edit(self, business_unit: Any, code: Any) -> str:
        """The account's posting edit code on that business unit ("" for blank, or when it has no such account)."""
        found = self._found(business_unit, code)[2]
        return found[1] if found else ""

    def name_of(self, business_unit: Any) -> str:
        """The business unit's name: the master's, else its header row's in the chart."""
        return self._names.get(clean(business_unit), "")
