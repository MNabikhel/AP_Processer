"""Learning from reviewer decisions (no model retraining).

Every approved invoice line is stored as feedback: the AI's suggestion, the
code the reviewer kept, and whether the reviewer *accepted* or *corrected*
it. Before coding a new invoice:

* ``select_examples`` picks the most relevant past decisions (same vendor
  first, then similar line descriptions from any vendor) and they are shown
  to the model, so a correction applies to the very next invoice.

After coding:

* ``compare_with_history`` marks each line as matching or conflicting with an
  established pattern for that vendor. Agreement is shown as reinforcement in
  the dashboard; disagreement is flagged for review.

Both follow AP when it changes its mind with the one rule the GL suggestions and
the learned line tax codes use too (``latest_decision``): the latest decision on a
line is the one that counts, and the older decisions for another value no longer
do, unless that single decision reverses a long run of approvals
(``UNCONFIRMED_REVERSAL``): a slip and a change of mind look the same until AP
confirms it once more.

Accepted lines count as approvals, so patterns the AI gets right grow
stronger over time; a reviewer can delete any bad record.
"""

from __future__ import annotations

import base64
import operator
import re
import sys
import unicodedata
from array import array
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from typing import Any

from .caches import MB

ACCEPTED, CORRECTED = "accepted", "corrected"
# A line whose account the AI only guessed (``offline_coder.guessed``), or left blank, then coded by the reviewer: what
# the reviewer approved is their own coding, learned from like any other, but neither confirmed nor corrected: coding
# accuracy leaves it out (B-475, B-650).
CODED = "coded"
# A line approved in bulk as it was proposed: nobody looked at it, so it is neither confirmed nor corrected, and the
# accuracy figures leave it out (B-955). Its tax code is no vote either (``line_tax.tax_vote``, B-764).
BULK = "bulk"
HISTORY = "history"  # a line of past AP coding imported from the ERP (``ai/history.py``), not an approval here
# The reviewer of an invoice AP Coder approved on its own (touchless, ``capture/workflow.py``): nobody checked its
# coding, so its approval records no feedback (``Store.approve_invoice``).
AUTONOMOUS_REVIEWER = "AP Coder (autonomous)"


def changed_account(row: dict[str, Any]) -> bool:
    """Whether a feedback row set the line's account in place of what was proposed, for the learning memory: a
    correction, or a blank the reviewer filled in (recorded as coded since B-650: for the memory it still moves the
    line to the account picked, as a correction does, B-605)."""
    outcome = row.get("outcome")
    return outcome == CORRECTED or (outcome == CODED and (row.get("suggested_gl") or "") in ("", "UNASSIGNED"))


# Legal-form words dropped from the END of a vendor name ("Mueller GmbH & Co. KG" -> "mueller"), in English and
# French ("Limitée", "Incorporée", "s.e.c." for a société en commandite).
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "incorporee", "ltd", "ltee", "limited", "limitee", "corp", "corporation", "co", "company",
    "cie", "llc", "llp", "lp", "plc", "ulc", "pc", "pllc", "enr", "senc", "sencrl", "sec", "srl", "gmbh", "ag", "kg",
    "sa", "sas", "sarl", "bv", "nv", "pty", "spa", "ltda", "and",
}  # fmt: skip
# Changes whenever ``vendor_key`` gives different keys: the database recomputes the keys it stores (Store._migrate).
VENDOR_KEY_RULES = "2"
_LIGATURES = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss", "ø": "o", "Ø": "O", "ł": "l"})
_STOP_WORDS = {
    "and", "for", "the", "with", "from", "per", "each", "item", "items", "qty", "unit", "units",
    "month", "monthly", "service", "services", "invoice", "total",
}  # fmt: skip

# A pattern is "established" after this many decisions, or after a single explicit correction.
MIN_DECISIONS = 2
DOMINANT_SHARE = 0.8
VENDOR_LINE_SIMILARITY = 0.6
CROSS_VENDOR_SIMILARITY = 0.6


def _fold(text: str) -> str:
    """Lower-case and strip accents: "Hydro-Québec" and "Hydro Quebec" read the same."""
    text = unicodedata.normalize("NFKD", (text or "").translate(_LIGATURES))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


_CACHED_LENGTH = 300  # line descriptions and names come back on every invoice; a page's whole text does not


def _normalise_text(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", _fold(text)).split())


_normalise_short = lru_cache(maxsize=131072)(_normalise_text)


def _normalise(text: str) -> str:
    """Lower case, no accents, letters and digits only, single spaces: "Câble Cat6, 1000'" -> "cable cat6 1000"."""
    return _normalise_short(text) if len(text or "") <= _CACHED_LENGTH else _normalise_text(text)


@lru_cache(maxsize=16384)  # called for every invoice on many pages; the same names come back
def vendor_key(name: str) -> str:
    """Normalised vendor name for matching: accents, punctuation, "&"/"and" and legal forms ignored."""
    text = _fold(name).replace("&", " and ")
    text = re.sub(r"\b((?:[a-z]\.){2,})", lambda m: m.group(1).replace(".", ""), text)  # S.E.N.C. -> senc
    words = _normalise(text).split()
    if words and words[0] == "the":
        words = words[1:]
    return _drop_legal_suffixes(words)


def _drop_legal_suffixes(words: list[str]) -> str:
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def refresh_vendor_key(key: str) -> str:
    """A key ``vendor_key`` gave under older rules, as today's rules give it for the same name (the name already
    normalised: only the legal forms added since are dropped): "energir sec" -> "energir"."""
    return _drop_legal_suffixes(str(key or "").split()) or str(key or "")


def _token_set(text: str) -> frozenset[str]:
    return frozenset(w for w in _normalise(text).split() if len(w) >= 3 and not w.isdigit() and w not in _STOP_WORDS)


_token_set_short = lru_cache(maxsize=65536)(_token_set)  # (0.7 KB a description: 46 MB at most, D-195)


def tokens(text: str) -> set[str]:
    """The words of a description that count for matching (3 letters or more, no plain numbers, no stop words)."""
    return set(_token_set_short(text) if len(text or "") <= _CACHED_LENGTH else _token_set(text))


def similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class Pattern:
    """Aggregated decisions for one vendor + line description + final coding."""

    vendor_key: str
    vendor_name: str
    description: str
    gl_code: str
    cost_center: str
    approvals: int = 0
    corrections: int = 0
    last_seen: str = ""
    superseded: bool = False  # AP has since recoded this line to another account and confirmed it (``current``)

    @property
    def decisions(self) -> int:
        return self.approvals + self.corrections

    @cached_property
    def tokens(self) -> frozenset[str]:
        """Worked out once per pattern: matching reads them for every line of every invoice."""
        return frozenset(tokens(self.description))


_ROW_FIELDS = ("id", "vendor_key", "description", "final_gl", "final_cc", "outcome")


def _fingerprint(rows: list[dict[str, Any]]) -> int:
    """What a list of feedback rows says, in one number: the same rows read again give the same one."""
    return hash(tuple(tuple(r.get(f) for f in _ROW_FIELDS) for r in rows))


class RowsCache:
    """A value worked out from a list of feedback rows, kept while the same rows come back: the lines of an
    invoice, its checks and its review screen all read the same history. The same row objects are recognised at
    once; rows read again from the database by what they say (a few milliseconds for tens of thousands). Rows
    are read, never changed in place. The latest ``slots`` lists are kept: past the memory read cap an old
    vendor's invoice has a list of its own (``Store.learning_rows``), and invoices of it and of other vendors in
    turn would otherwise rebuild the value each time. ``get``'s ``update(rows kept, their value)``: the value for the
    rows asked for, from one kept (an approval's lessons added in front of them, D-171), or None; asked before the
    value is built. Each slot is [rows, their fingerprint (worked out once compared), extra, value]. With ``most``,
    the latest is always kept and the others while the values together are within ``most`` bytes, each counted at
    ``row_bytes`` a row (what the value holds a row at most, measured: every description a different one; the rows
    themselves are the store's, D-195)."""

    def __init__(self, slots: int = 2, most: int = 0, row_bytes: int = 0) -> None:
        self._size = max(1, slots)
        self.most, self.row_bytes = most, row_bytes
        self._slots: tuple[list[Any], ...] = ()  # the latest first

    def get(self, rows: list[dict[str, Any]], extra: Any, build: Callable[[], Any],
            update: Callable[[list[dict[str, Any]], Any], Any] | None = None) -> Any:  # fmt: skip
        slots = self._slots  # one read: another thread may replace them meanwhile
        fingerprint = None
        for slot in slots:
            if slot[2] == extra and len(slot[0]) == len(rows) and all(map(operator.is_, slot[0], rows)):
                return self._keep(slots, slot, slot)  # the same rows (in the same order), in any list
        for slot in slots if update is not None else ():
            if slot[2] == extra and (value := update(slot[0], slot[3])) is not None:
                return self._keep(slots, None, [list(rows), None, extra, value])
        for slot in slots:
            if slot[2] == extra and len(slot[0]) == len(rows):
                fingerprint = _fingerprint(rows) if fingerprint is None else fingerprint
                if slot[1] is None:
                    slot[1] = _fingerprint(slot[0])
                if fingerprint == slot[1]:
                    return self._keep(slots, slot, [list(rows), fingerprint, extra, slot[3]])
        value = build()
        return self._keep(slots, None, [list(rows), fingerprint, extra, value])

    def _keep(self, slots: tuple, old: Any, new: list[Any]) -> Any:
        kept, held = [new], len(new[0]) * self.row_bytes
        for s in slots:
            more = len(s[0]) * self.row_bytes
            if s is not old and len(kept) < self._size and (not self.most or held + more <= self.most):
                kept.append(s)
                held += more
        self._slots = tuple(kept)
        return new[3]


# An approval's lessons are added to what is kept of the memory (the GL suggestions' index, ``suggest._updated_memory``;
# the patterns, ``_updated_aggregate``) instead of working it out again from every row (D-171): at most this many
# lessons added and dropped (the oldest, past the memory's cap) at once; past it, or any other change (lessons
# forgotten, the memory read again), it is worked out from all the rows.
UPDATE_MOST = 1000


def added_in_front(kept: list[dict[str, Any]], rows: list[dict[str, Any]]) -> int | None:
    """How many lessons ``rows`` has before the rows ``kept``, when it is those, then the newest of ``kept`` (the same
    row objects, in the same order: the store's memory after an approval, its oldest past the cap dropped,
    ``Store.feedback_rows``), at most UPDATE_MOST; None when it is anything else."""
    if not kept or not rows:
        return None
    first = kept[0]
    added = next((k for k in range(min(len(rows), UPDATE_MOST + 1)) if rows[k] is first), None)
    if added is None or len(rows) - added > len(kept) or not all(map(operator.is_, rows[added:], kept)):
        return None
    return added if added + len(kept) - (len(rows) - added) <= UPDATE_MOST else None


# The vendors whose lessons go past the whole memory's cap (``Store.learning_rows``): their older lessons (the store's
# ``_PAST_MEMORY``) and the GL suggestions' index extended by them (``suggest._PAST``) are kept for this many vendors
# and this many of their lessons at most in each process, the latest used always (D-159). The indexes extended are
# kept within PAST_BYTES_KEPT too (D-171): each copies the lists and dicts of the shared index it changes, 0.35 MB a
# vendor on the 100k seed (9,850 lines but 167 words), several MB with a real vocabulary.
PAST_KEPT, PAST_ROWS_KEPT, PAST_BYTES_KEPT = 32, 60_000, 32 * 1024 * 1024
# The same lessons by line (``PastLines``, D-178) are kept for this many vendors and about this many bytes of theirs in
# each process (``Store._past_lines``).
PAST_LINES_KEPT, PAST_LINES_BYTES = 4096, 64 * 1024 * 1024
PAST_LINE_BYTES, PAST_RUN_BYTES = 400, 245


Line = tuple[str, str]  # a line: (vendor key, description normalised)
# A run of a line's lessons in a row on one account, newest first: (the account, the lessons, the number of its newest,
# that one's description, its cost center, whether it changed the account: ``changed_account``)
Run = tuple[str, int, int, str, str, bool]


def _run(r: dict[str, Any], n: int, lessons: int = 1) -> Run:
    return (r["final_gl"], lessons, n, r["description"], r.get("final_cc") or "", changed_account(r))


@dataclass(frozen=True, eq=False)
class PastLines:
    """A vendor's lessons past the whole memory's cap (``WithPast.past``) by line, whatever the chart: what the GL
    suggestions read of them (``suggest._extended``), a few thousand lines where the lessons can be tens of thousands
    (D-178). Kept in the database for every process (``Store._past_lines``) and carried forward as the memory moves
    on (``carried``), so they are read whole once, not on every check. Shared: read it, don't change it."""

    n: int  # the lessons
    head: int  # the number of the newest: a lesson's place is its number less this (those put in front: lower)
    lines: dict[Line, tuple[Run, ...]]  # each line's runs of lessons on one account, newest first
    names: tuple[Line, ...]  # the lines ``order`` names (one may have no lessons left)
    order: array  # each lesson's line (its place in ``names``), newest first: which line's oldest goes when one drops

    @classmethod
    def of(cls, rows: list[dict[str, Any]]) -> PastLines:
        """From the lessons, newest first (``Store.learning_rows``' order)."""
        made: dict[Line, list[Run]] = {}
        where: dict[Line, int] = {}
        order = array("I")
        for n, r in enumerate(rows):
            line = (r["vendor_key"], _normalise(r["description"]))
            runs = made.get(line)
            if runs is None:
                where[line] = len(where)
                made[line] = [_run(r, n)]
            elif runs[-1][0] == r["final_gl"]:  # (an older lesson of the run: its newest stays the one it names)
                gl, lessons, *newest = runs[-1]
                runs[-1] = (gl, lessons + 1, *newest)  # type: ignore[assignment]
            else:
                runs.append(_run(r, n))
            order.append(where[line])
        return cls(len(rows), 0, {line: tuple(runs) for line, runs in made.items()}, tuple(where), order)

    def carried(self, front: list[dict[str, Any]], keep: int) -> PastLines:
        """These lessons with ``front`` (newest first, all newer than they are) put in front, then the oldest past
        ``keep`` dropped: ``PastLines.of(front + rows)[:keep]`` (the store's ``_carried``), going over those only.
        The lines they don't change keep their runs (the same tuples: ``suggest._extended`` works their patterns out
        once); itself when nothing changes."""
        if not front and keep >= self.n:
            return self
        changed: dict[Line, list[Run]] = {}
        names, where = list(self.names), None
        head = self.head - len(front)
        lead = array("I")
        for n, r in zip(range(self.head - 1, head - 1, -1), reversed(front), strict=True):  # the oldest first
            line = (r["vendor_key"], _normalise(r["description"]))
            runs = changed.get(line)
            if runs is None:
                runs = changed[line] = list(self.lines.get(line, ()))
            if runs and runs[0][0] == r["final_gl"]:
                runs[0] = _run(r, n, runs[0][1] + 1)
            else:
                runs.insert(0, _run(r, n))
            if where is None:
                where = {name: i for i, name in enumerate(names)}
            if line not in where:
                where[line] = len(names)
                names.append(line)
            lead.append(where[line])
        lead.reverse()
        order = lead + self.order
        for i in reversed(order[keep:]):  # the oldest go: each its line's oldest
            line = names[i]
            runs = changed.get(line)
            if runs is None:
                runs = changed[line] = list(self.lines[line])
            gl, lessons, *newest = runs[-1]
            if lessons > 1:
                runs[-1] = (gl, lessons - 1, *newest)  # type: ignore[assignment]
            else:
                runs.pop()
        del order[keep:]
        lines = dict(self.lines)
        for line, runs in changed.items():
            if runs:
                lines[line] = tuple(runs)
            else:
                lines.pop(line, None)
        if len(names) > 2 * len(lines) + 64:  # the names of lines whose lessons all went, left out (B-1840)
            held = [i for i, name in enumerate(names) if name in lines]
            place = {old: new for new, old in enumerate(held)}
            names = [names[i] for i in held]
            order = array("I", (place[i] for i in order))
        return PastLines(len(order), head, lines, tuple(names), order)

    def encoded(self) -> dict[str, Any]:
        """As JSON (``decoded`` gives it back): the database keeps it for every process."""
        order = array("I", self.order)
        if sys.byteorder != "little":
            order.byteswap()
        where = {line: i for i, line in enumerate(self.names)}
        return {"n": self.n, "head": self.head, "names": [list(line) for line in self.names],
                "lines": [[where[line], [list(run) for run in runs]] for line, runs in self.lines.items()],
                "order": base64.b64encode(order.tobytes()).decode("ascii")}  # fmt: skip

    @classmethod
    def decoded(cls, value: dict[str, Any]) -> PastLines:
        names = tuple((str(v), str(d)) for v, d in value["names"])
        order = array("I")
        order.frombytes(base64.b64decode(value["order"]))
        if sys.byteorder != "little":
            order.byteswap()
        lines = {names[i]: tuple((str(gl), int(k), int(n), str(d), str(cc), bool(ch)) for gl, k, n, d, cc, ch in runs)
                 for i, runs in value["lines"]}  # fmt: skip
        found = cls(int(value["n"]), int(value["head"]), lines, names, order)
        each = Counter(names[i] for i in order)  # (a place past the names: IndexError)
        if found.n != len(order) or each != {line: sum(run[1] for run in runs) for line, runs in lines.items()}:
            raise ValueError("not the lessons it says")
        return found

    @cached_property
    def size(self) -> int:
        """About the bytes it holds (measured: ``PAST_LINE_BYTES`` a line, ``PAST_RUN_BYTES`` a run besides, 4 a
        lesson, and their words: each run's description is a string of its own, B-1841)."""
        runs = sum(map(len, self.lines.values()))
        words = sum(len(line[1]) for line in self.lines) + sum(
            len(run[0]) + len(run[3]) + len(run[4]) for each in self.lines.values() for run in each)  # fmt: skip
        return PAST_LINE_BYTES * len(self.lines) + PAST_RUN_BYTES * runs + 4 * self.n + words


class WithPast(Sequence):
    """``Store.learning_rows`` of a vendor with lessons past the whole memory's cap: the whole memory's rows
    (``shared``, the same list while its lessons are the same), then the vendor's lessons it does not hold (``past``,
    the same list too while they are). ``key``: whose (the database, the vendor). The GL suggestions build the shared
    rows' index once and extend it by the past ones' lines (``older``: ``suggest._with_past``). Not a list: the past
    rows are read only when it is read as one (``past`` may be what reads them, called once), and the GL suggestions
    of the review screen never do (D-178). Read it, don't change it."""

    def __init__(self, shared: list[dict[str, Any]], past: list[dict[str, Any]] | Callable[[], list[dict[str, Any]]],
                 key: Any, older: PastLines | None = None) -> None:  # fmt: skip
        self.shared, self.key = shared, key
        self._past, self._older, self._rows = past, older, None

    @property
    def past(self) -> list[dict[str, Any]]:
        if callable(self._past):
            self._past = self._past()
        return self._past

    @property
    def older(self) -> PastLines:
        """``past`` by line (given, or worked out from them)."""
        if self._older is None:
            self._older = PastLines.of(self.past)
        return self._older

    def _all(self) -> list[dict[str, Any]]:
        if self._rows is None:
            self._rows = self.shared + self.past
        return self._rows

    def __len__(self) -> int:
        return len(self._all())

    def __getitem__(self, i: Any) -> Any:
        return self._all()[i]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._all())

    def __reversed__(self) -> Iterator[dict[str, Any]]:
        return reversed(self._all())

    def __contains__(self, row: Any) -> bool:
        return row in self._all()

    def __eq__(self, other: Any) -> bool:
        return self._all() == (other._all() if isinstance(other, WithPast) else other)

    __hash__ = None  # type: ignore[assignment]

    def __add__(self, other: Any) -> list[dict[str, Any]]:
        return self._all() + list(other)

    def __radd__(self, other: Any) -> list[dict[str, Any]]:
        return list(other) + self._all()

    def __repr__(self) -> str:
        return f"WithPast({self._all()!r})"


_AGGREGATED = RowsCache(most=28 * MB, row_bytes=512)  # (50,000 lessons: 24 MB at most, and Learning's 2,000)


def aggregate(rows: list[dict[str, Any]]) -> list[Pattern]:
    """The decisions in ``rows`` grouped by vendor, description and coding. Worked out once for the same rows, and
    after an approval from those kept (``_updated_aggregate``; the result is shared: read it, don't change it)."""
    return _AGGREGATED.get(rows, None, lambda: _aggregated(rows),
                           lambda kept_rows, kept: _updated_aggregate(kept_rows, kept, rows)).patterns  # fmt: skip


# AP changes its mind (D-118): one rule for the GL suggestions (``suggest``), the history check (``current``) and the
# learned line tax codes (``line_tax.learned_codes``). The latest decision on a line is the one that counts; a
# correction that reverses this many decisions in a row of the same line, alone, waits for one more before it does: a
# slip and a deliberate change look the same until then.
UNCONFIRMED_REVERSAL = 5


@dataclass(frozen=True)
class Latest:
    """What AP decided last on one line (``latest_decision``)."""

    value: str  # the latest decision's value (an account, a tax code)
    run: int  # decisions in a row for it, the latest included
    was: str = ""  # the value AP changed it from: the commonest of the decisions it reverses, "" if never changed
    was_count: int = 0  # decisions for ``was`` among them
    reversed: int = 0  # decisions in a row for other values before the run, back to the latest one for ``value``
    unconfirmed: bool = False  # a single correction against a long run of decisions: maybe a slip, waits for one more

    @property
    def counts(self) -> bool:
        """Whether the latest value replaces the older ones (everything but an unconfirmed reversal)."""
        return not self.unconfirmed


def latest_decision(values: list[str], corrected: bool, reversal: int = UNCONFIRMED_REVERSAL) -> Latest:
    """AP's latest decision on a line, from its decisions' values, newest first (not empty). ``corrected``: the latest
    changed what was proposed (a correction, a blank filled in: ``changed_account``; a tax code picked). It is
    unconfirmed when it is the only one for its value and reverses ``reversal`` decisions or more: those in a row for
    other values before it, back to the last one for its own (two slips in a row against ten approvals reverse
    eleven, a slip put right reverses one)."""
    value = values[0]
    run = next((i for i, v in enumerate(values) if v != value), len(values))
    if run == len(values):
        return Latest(value, run)
    others = values[run:]
    span = others[: next((i for i, v in enumerate(others) if v == value), len(others))]
    tally = Counter(span)
    was = max(tally, key=lambda v: (tally[v], -span.index(v)))  # the commonest, then the most recent
    return Latest(value, run, was, tally[was], len(span), run == 1 and corrected and len(span) >= reversal)


def _aggregate(rows: list[dict[str, Any]]) -> list[Pattern]:
    return _aggregated(rows).patterns


@dataclass
class _Aggregated:
    """``aggregate`` of some rows (``patterns``), and what an approval's update starts from (``_updated_aggregate``):
    each line's rows, (vendor key, description normalised) -> [row] newest first, and each group's pattern."""

    patterns: list[Pattern]
    lines: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    groups: dict[tuple[str, str, str, str], Pattern] = field(default_factory=dict)


def _group_key(r: dict[str, Any], line: tuple[str, str]) -> tuple[str, str, str, str]:
    return (*line, r["final_gl"], r.get("final_cc") or "")


def _counted(groups: dict[tuple[str, str, str, str], Pattern], r: dict[str, Any], key: tuple[str, str, str, str],
             changed: bool) -> None:  # fmt: skip
    """Row ``r`` (``changed``: ``changed_account(r)``) counted in its group's pattern, made by its first row."""
    p = groups.get(key)
    if p is None:
        p = groups[key] = Pattern(r["vendor_key"], r["vendor_name"], r["description"], r["final_gl"], key[3])
    if changed:
        p.corrections += 1
    else:
        p.approvals += 1
    p.last_seen = max(p.last_seen, r.get("created_at") or "")


def _superseded(groups: dict[tuple[str, str, str, str], Pattern], lines: dict[tuple[str, str], list[dict[str, Any]]]
                ) -> None:  # fmt: skip
    """Each group's ``superseded``, by its line's latest decision (``latest_decision`` of the line's rows)."""
    latest = {line: latest_decision([r["final_gl"] for r in rs], changed_account(rs[0])) for line, rs in lines.items()}
    for key, p in groups.items():
        last = latest[key[:2]]
        p.superseded = last.counts and p.gl_code != last.value


def _aggregated(rows: list[dict[str, Any]]) -> _Aggregated:
    groups: dict[tuple[str, str, str, str], Pattern] = {}
    lines: dict[tuple[str, str], list[dict[str, Any]]] = {}  # each line's rows, newest first
    for r in rows:  # newest first (``Store.feedback_rows``)
        line = (r["vendor_key"], _normalise(r["description"]))
        _counted(groups, r, _group_key(r, line), changed_account(r))
        lines.setdefault(line, []).append(r)
    _superseded(groups, lines)
    return _Aggregated(list(groups.values()), lines, groups)


def _updated_aggregate(kept_rows: list[dict[str, Any]], kept: _Aggregated,
                       rows: list[dict[str, Any]]) -> _Aggregated | None:  # fmt: skip
    """``_aggregated(rows)`` from ``kept``, that of ``kept_rows``, when ``rows`` is lessons added in front of them and
    the oldest dropped (``added_in_front``): the same patterns in the same order, working out again only the lines
    the lessons added and dropped are of (their patterns new, the others kept). None when ``rows`` is anything else
    (D-171)."""
    added = added_in_front(kept_rows, rows)
    if added is None:
        return None
    left = len(rows) - added
    new: dict[tuple[str, str], list[dict[str, Any]]] = {}  # the lines of the lessons added, their rows
    for r in rows[:added]:
        new.setdefault((r["vendor_key"], _normalise(r["description"])), []).append(r)
    dropped: Counter[tuple[str, str]] = Counter(
        (r["vendor_key"], _normalise(r["description"])) for r in kept_rows[left:]
    )
    lines, groups = dict(kept.lines), dict(kept.groups)
    fresh: dict[tuple[str, str, str, str], Pattern] = {}
    was: list[tuple[tuple[str, str, str, str], Pattern]] = []  # the changed lines' patterns kept before
    for line in [*new, *(line for line in dropped if line not in new)]:
        before = kept.lines.get(line, [])
        for key in dict.fromkeys(_group_key(r, line) for r in before):
            was.append((key, groups.pop(key)))
        rs = new.get(line, []) + before[: len(before) - dropped[line]]
        if not rs:
            del lines[line]
            continue
        lines[line] = rs
        made: dict[tuple[str, str, str, str], Pattern] = {}
        for r in rs:
            _counted(made, r, _group_key(r, line), changed_account(r))
        _superseded(made, {line: rs})
        groups.update(made)
        fresh.update(made)
    # In the order first met: the groups first met in the lessons added, then the others as they were.
    front: dict[tuple[str, str, str, str], Pattern] = {}
    for r in rows[:added]:
        key = _group_key(r, (r["vendor_key"], _normalise(r["description"])))
        front.setdefault(key, fresh[key])
    gone = {id(p) for key, p in was if key in front or key not in fresh}
    again = {id(p): fresh[key] for key, p in was if key not in front and key in fresh}
    patterns = [*front.values(), *(again.get(id(p), p) for p in kept.patterns if id(p) not in gone)]
    return _Aggregated(patterns, lines, groups)


def current(patterns: list[Pattern]) -> list[Pattern]:
    """``patterns`` without what AP has moved away from: on each line, the decisions for another account than the
    latest one (``latest_decision``), unless the latest is a single change against ``UNCONFIRMED_REVERSAL`` decisions
    or more (it may be a slip: then they all count). Each line is its own: a recode of the laptops leaves the
    batteries' history."""
    return [p for p in patterns if not p.superseded]


def detect_vendors(patterns: list[Pattern], document_text: str, vendor_hint: str | None = None) -> set[str]:
    """Known vendors whose (normalised) name appears in the document."""
    doc = f" {_normalise(document_text)} "
    found = {k for k in {p.vendor_key for p in patterns} if len(k) >= 4 and f" {k} " in doc}
    if vendor_hint:
        found.add(vendor_key(vendor_hint))
    return found


def select_examples(
    rows: list[dict[str, Any]],
    document_text: str,
    vendor_hint: str | None = None,
    max_vendor: int = 25,
    max_similar: int = 15,
) -> list[Pattern]:
    patterns = current(aggregate(rows))  # what AP moved away from is no example
    if not patterns:
        return []
    vendors = detect_vendors(patterns, document_text, vendor_hint)
    doc_tokens = tokens(document_text)

    def relevance(p: Pattern) -> float:  # share of the past line's words that appear on this invoice
        return len(p.tokens & doc_tokens) / len(p.tokens) if p.tokens else 0.0

    # A big vendor can have hundreds of decisions: show the ones about lines on THIS invoice first,
    # corrections before confirmations, then the most-used and most recent.
    vendor_patterns = sorted(
        (p for p in patterns if p.vendor_key in vendors),
        key=lambda p: (relevance(p) >= 0.5, p.corrections > 0, relevance(p), p.decisions, p.last_seen),
        reverse=True,
    )[:max_vendor]
    scored = []
    for p in patterns:
        if p.vendor_key in vendors or len(p.tokens) < 2:
            continue
        coverage = len(p.tokens & doc_tokens) / len(p.tokens)  # how much of the past line appears here
        if coverage >= CROSS_VENDOR_SIMILARITY:
            scored.append((coverage, p.decisions, p))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return vendor_patterns + [p for _, _, p in scored[:max_similar]]


def format_examples(examples: list[Pattern], with_cost_center: bool = True,
                    names: Mapping[str, str] | None = None) -> str:  # fmt: skip
    """The examples as the chat model reads them. ``names``: {GL code: the account's name}, shown beside each
    code (a code alone says nothing to a model that was not shown that account, B-2143)."""
    if not examples:
        return ""
    names = names or {}
    header = "| vendor | past line description | GL | " + ("cost center | " if with_cost_center else "")
    header += "approved | corrected-to |"
    sep = "|" + " --- |" * (5 + int(with_cost_center))
    lines = [
        "## Approved coding history from your AP team",
        "These are decisions reviewers made on past invoices. When a line here is from the same vendor and has a "
        "similar description, use the same coding unless this invoice clearly shows different spend. "
        "'corrected-to' counts are explicit reviewer overrides of an earlier AI suggestion: follow them.",
        "",
        header,
        sep,
    ]
    for p in examples:
        desc = p.description.replace("|", "/")[:120]
        cc = f" {p.cost_center or '-'} |" if with_cost_center else ""
        name = str(names.get(p.gl_code) or "").replace("|", "/")
        gl = f"{p.gl_code} {name}".strip()
        lines.append(f"| {p.vendor_name} | {desc} | {gl} |{cc} {p.approvals} | {p.corrections} |")
    return "\n".join(lines)


def pair_lines(before: list[Any], after: list[Any]) -> list[tuple[Any | None, Any]]:
    """Match each final line to the AI's original line.

    Lines are matched on (line_number, occurrence), so invoices that repeat a line number
    (e.g. 1, 2, 2) still pair the second "2" with the AI's second "2". Works for dicts and
    objects with a ``line_number`` attribute. Lines the reviewer added pair with ``None``.
    """

    def number(li: Any) -> Any:
        return li.get("line_number") if isinstance(li, dict) else li.line_number

    def keyed(lines: list[Any]) -> list[tuple[tuple[Any, int], Any]]:
        seen: dict[Any, int] = defaultdict(int)
        out = []
        for li in lines:
            n = number(li)
            out.append(((n, seen[n]), li))
            seen[n] += 1
        return out

    originals = dict(keyed(before))
    return [(originals.get(k), li) for k, li in keyed(after)]


def compare_with_history(coding: Any, rows: list[dict[str, Any]], key: str | None = None,
                         usable: Collection[str] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """Per line: does the prediction agree with an established pattern for this vendor? ``key``: the vendor's key
    (``Store.vendor_key_for``: the second of two vendors by one name has its own, B-1021), else the name's. ``usable``:
    as ``compare_patterns``."""
    key = key or vendor_key(coding.vendor_name)
    return compare_patterns(coding, [p for p in aggregate(rows) if p.vendor_key == key], usable)


def compare_patterns(coding: Any, patterns: list[Pattern],
                     usable: Collection[str] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """``compare_with_history`` on one vendor's patterns (``aggregate`` of its lessons: ``Store.vendor_lessons``).
    ``usable``: the GL codes a line may still be coded to (``ReferenceData.line_accounts``); the lines coded to
    another (closed in the chart, taken out of the list) are no history to follow (B-2521)."""
    if usable is not None:
        patterns = [p for p in patterns if p.gl_code in usable]
    results = []
    for li in coding.line_items:
        line_tokens = tokens(li.description)
        similar = current([p for p in patterns if similarity(line_tokens, p.tokens) >= VENDOR_LINE_SIMILARITY])
        if not similar:
            continue
        votes: dict[str, int] = defaultdict(int)
        for p in similar:
            votes[p.gl_code] += p.decisions
        total = sum(votes.values())
        best_gl, best_votes = max(votes.items(), key=lambda kv: kv[1])
        corrected = any(p.corrections for p in similar if p.gl_code == best_gl)
        if not ((total >= MIN_DECISIONS or corrected) and best_votes / total >= DOMINANT_SHARE):
            continue
        results.append(
            {
                "line_number": li.line_number,
                "status": "match" if li.predicted_gl_code == best_gl else "conflict",
                "history_gl": best_gl,
                "decisions": total,
            }
        )
    return results
