"""GL account suggestions for invoice lines, from what AP approved before (no AI model, no Azure call).

Local sources, merged per GL account:

* this vendor's history: how AP coded similar lines from the same vendor. When AP changed the account of a line,
  the latest decision is the one that counts;
* other vendors' history: how similar lines were coded for other vendors;
* the vendor's usual account: every line AP coded for this vendor went to one account;
* the vendor master's default account for this vendor;
* the GL accounts themselves: words the line shares with an account's description and category.

Lines are compared with ``line_match``: abbreviations, plurals, accents, word order and words that go with any
account are allowed for. The best suggestion says whether it is strong enough to code the line with no one picking
it first (``auto``):

* a line of this vendor this alike, and none of its lines nearly as alike (or more recent) coded otherwise;
* a line of another vendor this alike, and no other vendor's similar line coded otherwise, or at least two other
  vendors agreeing on it with three quarters of the evidence (similarity of each vendor's closest line);
* the vendor's usual account once AP has coded enough different lines of it, all to that account;
* never a line AP just recoded against several older approvals, until a second decision confirms it: a slip
  looks the same as a change of mind at first;
* never the vendor master's default or an account's name alone (right 65% and 82% of the time on the benchmark);
* never an account AP recently moved similar lines away from (it may be out of date);
* when two accounts both have strong evidence, this vendor's own lines win over its usual account, which wins
  over other vendors' lines; otherwise a person decides.

The weaker suggestions are shown on the review screen for a person to pick. The thresholds are measured with
``python -m ap_coder.bench coding`` (docs/CODING.md). Suggestions for all the lines of an invoice share one index
of the history, built once (``_memory``): a long history (years of ERP history) costs milliseconds per line.
"""

from __future__ import annotations

import re
import sys
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from itertools import chain, repeat
from typing import Any

from ..core import chart
from ..core.caches import MB
from ..core.memory import (
    HISTORY,
    PAST_BYTES_KEPT,
    PAST_KEPT,
    PAST_ROWS_KEPT,
    Latest,
    PastLines,
    RowsCache,
    WithPast,
    _normalise,
    added_in_front,
    changed_account,
    latest_decision,
    tokens,
    vendor_key,
)
from ..core.reference_data import UNASSIGNED, ReferenceData
from .line_match import Index, keys, similarity
from .supplier_prior import (
    SAID_ALIKE,
    SURE_LINES,
    SURE_SHARE,
    Candidate,
    Prior,
    account_name,
    alike,
    from_book,
    name_words,
    reads_like,
    unit_words_said,
    units_named,
)
from .supplier_prior import rank as rank_prior

VENDOR_MIN_SIMILARITY = 0.34  # a line of this vendor this alike is suggested
OTHERS_MIN_SIMILARITY = 0.5  # a line of another vendor this alike is suggested
KEYWORD_MIN_OVERLAP = 1
# Strong enough to code a line with no one checking it first (measured with ``bench coding``, see docs/CODING.md).
VENDOR_AUTO_SIMILARITY = 0.4  # this vendor's line this alike...
VENDOR_AUTO_MARGIN = 0.15  # ...and no line of it coded to another account nearly as alike
OTHERS_AUTO_SIMILARITY = 0.6  # another vendor's line this alike, and none coded to another account
OTHERS_AUTO_VENDORS = 2  # ...or this many other vendors coded similar lines to it...
OTHERS_AUTO_SHARE = 0.75  # ...with this share of the other vendors' evidence (each vendor's closest line)
USUAL_MIN_LINES = 2  # different lines AP coded for this vendor, all to one account: suggested
USUAL_AUTO_LINES = 4  # ...and coded with it
# A correction that reverses ``UNCONFIRMED_REVERSAL`` approvals in a row of the same line is suggested, not coded
# with, until AP confirms it once: a slip and a deliberate change of account look the same until the next decision
# (``memory.latest_decision``: the one rule the history check and the learned tax codes follow too, D-118).
PRIORITY = {"vendor": 3, "usual": 2, "master": 1, "others": 1, "words": 0}
# A line that reads as a meal or a drink (B-932): on a weak match it goes to the meals account (50% of the tax
# claimable, D-90), never to the 100% one ("Staff events & meals billed to clients") for the word "client".
_MEAL_WORDS = re.compile(
    r"(?i)\b(?:meals?|dinners?|lunch(?:es|eon)?|breakfasts?|brunch|food|restaurant|catering|banquet|beverages?|drinks?"
    r"|wine|beer|coffee|pizzas?|sandwich(?:es)?|snacks?|refreshments?|pastr(?:y|ies)|donuts?|doughnuts?|bagels?"
    r"|muffins?|desserts?|appetizers?|buffet|barbecue|bbq|supper|takeout|repas|d[îi]ners?|d[ée]jeuners?|traiteur"
    r"|boissons?|vin|collations?|rafra[îi]chissements?|go[ûu]ters?|soupers?)\b"
)


@dataclass
class Suggestion:
    gl_code: str
    cost_center: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    source: str = ""  # where the best evidence came from: "vendor", "others", "usual", "master" or "words"
    auto: bool = False  # strong enough to code the line without a person picking it
    check: str = ""  # why a person must check it, said plainly (a recode not confirmed yet), "" otherwise
    strong: int = -1  # the highest ``PRIORITY`` of a source strong enough to code with it on its own (-1: none)
    # The cost centers AP approved this vendor's lines like this one with, for this account, each with how many
    # decisions ("" for none), the most used first: more than one, and the words of the line do not say which
    # (``candidates``).
    units: tuple[tuple[str, int], ...] = field(default=(), compare=False)
    # The past coding has this vendor's lines like this one on several accounts on one day: one voucher split, not
    # a change of mind (``candidates`` leaves such a line for a person).
    split: bool = field(default=False, compare=False)


@dataclass
class _Pattern:
    """The lines AP coded for one vendor with one description (case and punctuation aside)."""

    vendor_key: str
    description: str
    keys: frozenset[str]
    gl_code: str
    cost_center: str
    rank: int  # how recent its latest decision is: lower is newer (its place in the rows, less the memory's ``offset``)
    latest: Latest  # AP's latest decision on the line (``memory.latest_decision``)

    @property
    def decisions(self) -> int:
        """Decisions for ``gl_code`` in a row since the description was last coded otherwise."""
        return self.latest.run

    @property
    def was(self) -> str:
        """The account AP coded it to before (AP changed it), "" if never changed."""
        return self.latest.was

    @property
    def reversed(self) -> int:
        """Decisions for ``was`` among those AP's latest decision reverses."""
        return self.latest.was_count

    @property
    def unconfirmed(self) -> bool:
        """AP has just changed this line's account against several older decisions, and not confirmed it yet."""
        return self.latest.unconfirmed


@dataclass
class _Memory:
    patterns: list[_Pattern]
    index: Index
    usual: dict[str, Counter[str]]  # vendor key -> {account: different lines coded to it}
    newest: dict[str, int]  # vendor key -> rank of its latest decision
    # ``suggest_gl``'s answers from this memory: (line, vendor key, limit, default) -> (the reference data, the
    # suggestions), the latest used last
    said: dict[tuple[str, str, int, str], tuple[ReferenceData, list[Suggestion]]] = field(default_factory=dict)
    # What an approval's update starts from (``_updated_memory``, D-171): the lessons added in front of the rows built
    # from since (a rank is a row's place less this, so the ranks kept stay as they were); each line's decisions,
    # (vendor key, description normalised) -> [(rank, row)] newest first; and its pattern, for those with words.
    offset: int = 0
    lines: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = field(default_factory=dict)
    pattern_of: dict[tuple[str, str], _Pattern] = field(default_factory=dict)


SAID_KEPT = 1024  # lines whose suggestions each memory keeps
ALL_KEPT = 24  # accounts of a line kept when every one with evidence is asked for (``candidates``), the best
UNIT_SURE_LINES = 3  # approvals of lines like one on one business unit that settle it, for an account used on several
_SAID_LOCK = threading.Lock()


# What ``_extended`` made of one line of a vendor's older lessons (``PastLines``): from its runs and the shared memory's
# decisions of it (``_Memory.lines``' own list, None for none), kept while they are the same: its pattern (None when
# none of its lessons is on an account that counts) and the number of its newest decision.
_Made = tuple[tuple[Any, ...], Any, "_Pattern | None", int]

# The lines of an invoice (and its review screen) share one index of the memory: 50,000 lessons, each of another
# description, hold about 1.9 KB a lesson (95 MB); a second list is kept while both fit (D-195).
_MEMORY = RowsCache(most=104 * MB, row_bytes=2048)
# A vendor's ``learning_rows`` past the whole memory's cap (``WithPast``), by whose they are: (the shared rows and the
# accounts their memory was built for, the vendor's older lessons by line it was extended by, the memory extended, the
# bytes it holds of its own: ``_own_bytes``), the latest used last (``_with_past``).
_PAST: dict[Any, tuple[list[dict[str, Any]], frozenset[str], PastLines, _Memory, int]] = {}
# The patterns the same vendors' older lessons' lines made (the accounts that count, {line: ``_Made``}), kept past a
# change of the shared rows (they hold none of their memory): what the next extended memory takes those of the lines
# that did not change from (D-173), ``PAST_KEPT`` vendors and ``PAST_ROWS_KEPT`` lines at most, the latest used last.
_OLDER: dict[Any, tuple[frozenset[str], dict[tuple[str, str], _Made]]] = {}
_PAST_LOCK = threading.Lock()


def _memory(feedback: list[dict[str, Any]], valid: frozenset[str]) -> _Memory:
    """What AP approved, as patterns and their word index, built once for the same rows. ``feedback`` is newest
    first (``Store.feedback_rows``): the latest decision for a vendor's line wins over older ones that coded it to
    another account. A vendor's rows past the whole memory's cap: the shared index, extended (``_with_past``)."""
    if isinstance(feedback, WithPast):
        return _with_past(feedback, valid)
    return _MEMORY.get(feedback, valid, lambda: _build_memory(feedback, valid),
                       lambda rows, kept: _updated_memory(rows, kept, feedback, valid))  # fmt: skip


def _with_past(feedback: WithPast, valid: frozenset[str]) -> _Memory:
    """The memory of a vendor's ``learning_rows`` past the whole memory's cap: the shared rows' one, built once per
    change of the lessons (``_MEMORY``: the same list while the store's stamp holds, D-154), extended by the vendor's
    older lessons (``_extended``): what building it of all the rows gives, in milliseconds. Kept for ``PAST_KEPT``
    vendors, ``PAST_ROWS_KEPT`` of their lessons and ``PAST_BYTES_KEPT`` of their own (D-171), the latest used always
    (D-159), while the same lists come (the store's, kept while its stamps hold). Once the shared rows are others (the
    lessons changed), the vendors' memories extended from the older ones are let go: each holds all of its shared
    rows' memory (B-1719). The older lessons are read by line (``WithPast.older``: the store's, carried forward as the
    memory moves on, D-178), never row by row."""
    shared, older = feedback.shared, feedback.older
    with _PAST_LOCK:
        hit = _PAST.pop(feedback.key, None)
        if hit is not None and hit[0] is shared and hit[2] is older and hit[1] == valid:
            _PAST[feedback.key] = hit
            return hit[3]
        kept = _OLDER.get(feedback.key)
    base = _MEMORY.get(shared, valid, lambda: _build_memory(shared, valid),
                       lambda rows, kept: _updated_memory(rows, kept, shared, valid))  # fmt: skip
    # The lines the store carried forward unchanged keep their runs (D-173): their patterns are taken from before.
    memory, made = _extended(base, shared, older, valid, kept[1] if kept is not None and kept[0] == valid else {})
    with _PAST_LOCK:
        for key in [k for k, kept in _PAST.items() if kept[0] is not shared]:
            del _PAST[key]
        _PAST.pop(feedback.key, None)
        own = 0 if memory is base else _own_bytes(memory)  # (nothing added: the shared memory itself, B-1781)
        _PAST[feedback.key] = (shared, valid, older, memory, own)
        while len(_PAST) > 1 and (len(_PAST) > PAST_KEPT
                                  or sum(kept[2].n for kept in _PAST.values()) > PAST_ROWS_KEPT
                                  or sum(kept[4] for kept in _PAST.values()) > PAST_BYTES_KEPT):  # fmt: skip
            del _PAST[next(iter(_PAST))]
        _OLDER.pop(feedback.key, None)
        _OLDER[feedback.key] = (valid, made)
        while len(_OLDER) > 1 and (len(_OLDER) > PAST_KEPT
                                   or sum(len(kept[1]) for kept in _OLDER.values()) > PAST_ROWS_KEPT):  # fmt: skip
            del _OLDER[next(iter(_OLDER))]
    return memory


def _own_bytes(memory: _Memory) -> int:
    """About what a memory extended for one vendor holds of its own: the lists and dicts ``_extended`` copied from the
    shared memory (their own size, not the patterns and words they share with it)."""
    index = memory.index
    return sum(sys.getsizeof(x) for x in (memory.patterns, memory.usual, memory.newest, index.entries, index.accounts,
                                          index.by_word, index.weight, index._by_letter))  # fmt: skip


def _extended(base: _Memory, shared: list[dict[str, Any]], older: PastLines, valid: frozenset[str],
              made: dict[tuple[str, str], _Made]) -> tuple[_Memory, dict[tuple[str, str], _Made]]:  # fmt: skip
    """(``_build_memory(shared + past, valid)`` from ``base``, the memory of ``shared``, ``older`` being the ``past``
    lessons by line; what it made of their lines, for the next one): going over the lines of ``older`` only, and
    working out again only the patterns of those whose runs or decisions in the shared rows changed since ``made``
    (the store's carried forward keep theirs; ``base.lines``' list of a line is another once it changed). The older
    lessons rank after every shared row: each line they add decisions to keeps its latest decision (its account, rank
    and cost center) and has its run of decisions worked out again; a line only in them is a new pattern, after all of
    base's, in the order it is first met (ranked again as the shared rows change)."""
    start = len(shared) - base.offset - older.head  # an older lesson's rank: its number plus this
    now: dict[tuple[str, str], _Made] = {}
    found: list[tuple[tuple[str, str], Any, _Pattern, int]] = []  # the lines with a decision that counts
    for line, runs in older.lines.items():
        earlier = base.lines.get(line)  # the line's decisions in the shared rows, (rank, row) newest first
        was = made.get(line)
        if was is not None and was[0] is runs and was[1] is earlier:
            if was[2] is not None and earlier is None and was[2].rank != start + was[3]:
                was = (runs, None, replace(was[2], rank=start + was[3]), was[3])
        else:
            counted = [run for run in runs if run[0] in valid]  # its decisions on the accounts that count
            if not counted:
                was = (runs, earlier, None, 0)
            else:
                _, _, first, description, cost_center, changed = counted[0]
                accounts = list(chain.from_iterable(repeat(run[0], run[1]) for run in counted))
                if earlier is None:
                    p = _pattern_of(start + first, line[0], description, cost_center, changed, accounts)
                else:
                    p = _pattern(*earlier[0], [r["final_gl"] for _, r in earlier] + accounts)
                was = (runs, earlier, p, first)
        now[line] = was
        if was[2] is not None:
            found.append((line, earlier, was[2], was[3]))
    if not found:
        return base, now
    vendors = {line[0] for line, *_ in found}
    patterns = list(base.patterns)
    where = {(p.vendor_key, _normalise(p.description)): n for n, p in enumerate(patterns) if p.vendor_key in vendors}
    new: list[tuple[int, _Pattern]] = []  # the lines only in the older lessons: (their first number, their pattern)
    for line, earlier, p, first in found:
        if earlier is not None:
            if line in where:  # (a line without words is in no memory)
                patterns[where[line]] = p
        elif p.keys:
            new.append((first, p))
    added = [p for _, p in sorted(new, key=lambda x: x[0])]
    patterns += added
    usual, newest = dict(base.usual), dict(base.newest)
    for v in vendors:
        usual.pop(v, None)
        newest.pop(v, None)
    for p in [patterns[n] for n in where.values()] + added:  # the vendors' patterns, in the memory's order
        if not p.unconfirmed:
            usual.setdefault(p.vendor_key, Counter())[p.gl_code] += 1
        newest[p.vendor_key] = min(newest.get(p.vendor_key, p.rank), p.rank)
    index = base.index.extended([p.keys for p in added], [p.gl_code for p in added])
    return _Memory(patterns, index, usual, newest, offset=base.offset), now


def _line_pattern(seq: list[tuple[int, dict[str, Any]]]) -> _Pattern:
    """The pattern of one line from its decisions, (rank, row) newest first."""
    rank, r = seq[0]
    return _pattern(rank, r, [x["final_gl"] for _, x in seq])


def _pattern(rank: int, r: dict[str, Any], accounts: list[str]) -> _Pattern:
    """The pattern of one line whose latest decision is ``r`` (ranked ``rank``), from its decisions' accounts, newest
    first: the latest decision wins over older ones for another account."""
    return _pattern_of(rank, r["vendor_key"], r["description"], r.get("final_cc") or "", changed_account(r), accounts)


def _pattern_of(rank: int, vendor: str, description: str, cost_center: str, changed: bool,
                accounts: list[str]) -> _Pattern:  # fmt: skip
    """``_pattern`` of a latest decision of this vendor's, description and cost center (``changed``: it changed the
    account, ``memory.changed_account``)."""
    latest = latest_decision(accounts, changed)
    return _Pattern(vendor, description, keys(description), latest.value, cost_center, rank, latest)


def _build_memory(feedback: list[dict[str, Any]], valid: frozenset[str]) -> _Memory:
    decisions: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}  # each line's decisions, newest first
    for rank, r in enumerate(feedback):
        if r.get("final_gl") in valid:
            decisions.setdefault((r["vendor_key"], _normalise(r["description"])), []).append((rank, r))
    groups = {line: _line_pattern(seq) for line, seq in decisions.items()}
    patterns = [p for p in groups.values() if p.keys]
    usual, newest = _usual(patterns, None)
    index = Index([p.keys for p in patterns], [p.gl_code for p in patterns])
    return _Memory(patterns, index, usual, newest, lines=decisions,
                   pattern_of={line: p for line, p in groups.items() if p.keys})  # fmt: skip


def _usual(patterns: list[_Pattern], vendors: set[str] | None) -> tuple[dict[str, Counter[str]], dict[str, int]]:
    """Each vendor's (of ``vendors``, None: all) usual accounts and the rank of its latest decision, from the memory's
    patterns in its order."""
    usual: dict[str, Counter[str]] = {}
    newest: dict[str, int] = {}
    for p in patterns:
        if vendors is not None and p.vendor_key not in vendors:
            continue
        if not p.unconfirmed:  # a slip, maybe: no evidence for the vendor's usual account yet
            usual.setdefault(p.vendor_key, Counter())[p.gl_code] += 1
        newest[p.vendor_key] = min(newest.get(p.vendor_key, p.rank), p.rank)
    return usual, newest


def _updated_memory(kept_rows: list[dict[str, Any]], kept: _Memory, rows: list[dict[str, Any]],
                    valid: frozenset[str]) -> _Memory | None:  # fmt: skip
    """``_build_memory(rows, valid)`` from ``kept``, the memory of ``kept_rows``, when ``rows`` is lessons added in
    front of them and the oldest dropped (``memory.added_in_front``): exactly what building it gives, the same
    patterns in the same order and the same index, working out again only the lines the lessons added and dropped are
    of. Ranks are kept: the new lessons' are below them all (``offset``). None when ``rows`` is anything else, or more
    than ``memory.UPDATE_MOST`` lessons changed: then it is built (D-171)."""
    added = added_in_front(kept_rows, rows)
    if added is None:
        return None
    left = len(rows) - added
    offset = kept.offset + added
    new: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}  # the lines of the lessons added, as met
    for place in range(added):
        r = rows[place]
        if r.get("final_gl") in valid:
            new.setdefault((r["vendor_key"], _normalise(r["description"])), []).append((place - offset, r))
    dropped: Counter[tuple[str, str]] = Counter()  # each line's decisions dropped: its oldest
    for r in kept_rows[left:]:
        if r.get("final_gl") in valid:
            dropped[(r["vendor_key"], _normalise(r["description"]))] += 1
    lines, pattern_of = dict(kept.lines), dict(kept.pattern_of)
    changed: dict[tuple[str, str], _Pattern | None] = {}
    for line in [*new, *(line for line in dropped if line not in new)]:
        seq = kept.lines.get(line, [])
        seq = new.get(line, []) + seq[: len(seq) - dropped[line]]
        if seq:
            lines[line] = seq
            changed[line] = _line_pattern(seq)
        else:
            del lines[line]
            changed[line] = None
        pattern_of.pop(line, None)
        if changed[line] is not None and changed[line].keys:
            pattern_of[line] = changed[line]
    # The patterns in a build's order, by their latest decision: those of the lines added first, then the others as
    # they were (a line only dropped from keeps its latest decision, its words and its account).
    front = [p for line in new if (p := changed[line]) is not None and p.keys]
    gone, again = set(), {}  # the patterns kept that go (moved to the front, or no decision left), those changed
    for line, p in changed.items():
        was = kept.pattern_of.get(line)
        if was is not None and (line in new or p is None or not p.keys):
            gone.add(id(was))
        elif was is not None:
            again[id(was)] = p
    patterns, new_of = list(front), []
    for p in kept.patterns:
        if id(p) in gone:
            new_of.append(-1)
        else:
            new_of.append(len(patterns))
            patterns.append(again.get(id(p), p))
    vendors = {line[0] for line in changed}
    usual, newest = _usual(patterns, vendors)
    usual.update((v, c) for v, c in kept.usual.items() if v not in vendors)
    newest.update((v, n) for v, n in kept.newest.items() if v not in vendors)
    index = kept.index.updated([p.keys for p in patterns], [p.gl_code for p in patterns], len(front), new_of)
    return _Memory(patterns, index, usual, newest, offset=offset, lines=lines, pattern_of=pattern_of)


Hits = dict[str, list[tuple[float, _Pattern]]]  # account -> [(similarity, pattern)]


def _similar_lines(line: frozenset[str], key: str, memory: _Memory) -> tuple[Hits, Hits]:
    """(this vendor's, other vendors') lines like ``line``, by account. A newer decision of this vendor on a line
    as alike replaces older ones that went to another account (AP changed its mind)."""
    mine: Hits = defaultdict(list)
    others: Hits = defaultdict(list)
    if not line:
        return mine, others
    patterns = memory.patterns
    lowest = min(VENDOR_MIN_SIMILARITY, OTHERS_MIN_SIMILARITY)
    for i, sim in memory.index.similar(line, lowest, own_codes=lambda i: patterns[i].vendor_key != key):
        p = patterns[i]
        if p.vendor_key == key:
            if sim >= VENDOR_MIN_SIMILARITY:
                mine[p.gl_code].append((sim, p))
        elif sim >= OTHERS_MIN_SIMILARITY:
            others[p.gl_code].append((sim, p))
    every = [(gl, s, p) for gl, hits in mine.items() for s, p in hits]
    for gl in list(mine):
        kept = [
            (s, p) for s, p in mine[gl]
            if not any(g != gl and q.rank < p.rank and t >= max(s - VENDOR_AUTO_MARGIN, VENDOR_AUTO_SIMILARITY)
                       for g, t, q in every)
        ]  # fmt: skip
        if kept:
            mine[gl] = kept
        else:
            del mine[gl]
    return mine, others


def _stems(words: set[str]) -> dict[str, str]:
    """{stem: word}: "monitors" and "monitor" meet (plural -s / -es only; kept deliberately simple)."""
    out = {}
    for w in words:
        stem = w[:-2] if w.endswith("es") and len(w) > 5 else w[:-1] if w.endswith("s") and len(w) > 4 else w
        out.setdefault(stem, w)
    return out


# Each chart of accounts' accounts and the stems of the words of their other columns (``_account_stems``), the latest
# two charts: every line suggested reads them all (1,500 accounts, each line of each check of an invoice).
_ACCOUNT_STEMS: dict[int, tuple[Any, list[tuple[str, frozenset[str]]]]] = {}


def _account_stems(chart: Any) -> list[tuple[str, frozenset[str]]]:
    """(account, the stems of the words its description and category have), in the chart's order: worked out once for
    the chart (``Store.reference_data`` keeps it, shared and never changed, until the accounts change)."""
    with _SAID_LOCK:
        kept = _ACCOUNT_STEMS.get(id(chart))
    if kept is not None and kept[0] is chart:
        return kept[1]
    found = [(row[chart.key_column], frozenset(_stems(tokens(" ".join(str(v) for k, v in row.items()
                                                                       if k != chart.key_column)))))
             for row in chart.rows]  # fmt: skip
    with _SAID_LOCK:
        _ACCOUNT_STEMS.pop(id(chart), None)
        _ACCOUNT_STEMS[id(chart)] = (chart, found)
        while len(_ACCOUNT_STEMS) > 2:  # (the chart in use and the one before a change: room for D-212's priors)
            del _ACCOUNT_STEMS[next(iter(_ACCOUNT_STEMS))]
    return found


def _units_of(memory: _Memory, gl: str,
              hits: list[tuple[float, _Pattern]]) -> tuple[tuple[tuple[str, int], ...], bool]:  # fmt: skip
    """(the cost centers AP approved a vendor's lines like one with, for the account ``gl``, each with its
    decisions, the most used first: every decision of the lines found (``hits``) that went to it, not only each
    line's latest; whether the imported past coding has one of those lines on several accounts or cost centers on
    one day: a voucher split across them, which the latest-decision rule would take for a change of mind)."""
    units: Counter[str] = Counter()
    split = False
    for _, p in hits:
        rows = [row for _, row in memory.lines.get((p.vendor_key, _normalise(p.description)), ())]
        decided = [row for row in rows if row.get("final_gl") == gl]
        for row in decided:
            units[row.get("final_cc") or ""] += 1
        if not decided:  # (a memory extended for one vendor keeps no decisions: the line's latest)
            units[p.cost_center] += p.decisions
        days: dict[str, set[tuple[str, str]]] = {}
        for row in rows:
            if row.get("outcome") == HISTORY:
                days.setdefault(str(row.get("created_at") or ""), set()).add(
                    (row.get("final_gl") or "", row.get("final_cc") or ""))  # fmt: skip
        split = split or any(len(coded) > 1 for coded in days.values())
    return tuple(units.most_common()), split


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _meals_first(
    description: str, ranked: list[Suggestion], reference: ReferenceData, valid: frozenset[str]
) -> list[Suggestion]:
    """``ranked`` with a meals account first when the line reads as a meal and the best is a weak suggestion of
    another account (B-932): a guess, with a note naming the other account. Strong evidence (AP's own coding) stays."""
    meals = frozenset(getattr(getattr(reference, "tax", None), "meals_gl_codes", None) or ()) & valid
    if not meals or not ranked or ranked[0].auto or ranked[0].gl_code in meals or not _MEAL_WORDS.search(description):
        return ranked
    first = ranked[0]
    meal = next((x for x in ranked if x.gl_code in meals), None) or Suggestion(sorted(meals)[0], source="words")
    meal.score = max(meal.score, first.score + 0.001)
    meal.auto = False
    meal.reasons.insert(0, f"reads as a meal: the meals account (50% of the tax claimable); {first.gl_code} only if "
                           "it is billed to a client or is a staff event")  # fmt: skip
    return [meal, *(x for x in ranked if x is not meal)]


def suggest_gl(
    description: str,
    vendor_name: str,
    feedback: list[dict[str, Any]],
    reference: ReferenceData,
    limit: int = 3,
    default_gl: str = "",
    key: str | None = None,
    prior: Prior | None = None,
    units: Iterable[str] = (),
) -> list[Suggestion]:
    """Up to ``limit`` GL accounts for one line, best first. ``feedback`` is ``Store.feedback_rows()`` (newest
    first); ``default_gl`` is the vendor master's default account for this vendor, if any. Never a tax account or
    an account that is not in the chart of accounts. Worked out once for the same line, vendor and default, kept with
    the memory and the reference data they came from (``_Memory.said``, ``SAID_KEPT`` lines, D-159): every check of
    the review screen asks it again of each line. Each caller gets copies.

    With a chart of accounts imported, or a journal loaded (``prior``: the supplier's, ``supplier_prior.prior_for``;
    ``units``: the cost centers the vendor's approved lines used), these are the first of ``candidates``: whole
    account strings, each a pair that may be posted to (D-212)."""
    if by_pairs(reference, prior):
        found = candidates(description, vendor_name, feedback, reference, limit, default_gl, key, prior, units)
        return [Suggestion(c.code, c.business_unit, c.score, list(c.reasons), c.source, c.auto, c.check)
                for c in found]  # fmt: skip
    return _from_memory(description, vendor_name, feedback, reference, limit, default_gl, key)


def _from_memory(description: str, vendor_name: str, feedback: list[dict[str, Any]], reference: ReferenceData,
                 limit: int, default_gl: str, key: str | None) -> list[Suggestion]:  # fmt: skip
    """``suggest_gl`` from the approval memory and the accounts' names, as it was before a line had a whole account
    string: the GL code, and the cost center of the past line it came from. ``limit`` 0: every account with any
    evidence, the weak ones too (what ``candidates`` completes and orders)."""
    line = keys(description)
    if not line and not tokens(description) and not default_gl:
        return []
    valid = frozenset(c for c in reference.line_accounts() if c != UNASSIGNED)
    key = key or vendor_key(vendor_name)  # the vendor's own key, when two vendors' names read alike (B-1021)
    memory = _memory(feedback, valid)
    asked = (description, key, limit, default_gl)
    with _SAID_LOCK:
        kept = memory.said.pop(asked, None)
        if kept is not None and kept[0] is reference:
            memory.said[asked] = kept  # the latest used, kept the longest
    if kept is None or kept[0] is not reference:
        kept = (reference, _suggested(description, line, key, memory, reference, valid, limit, default_gl))
        with _SAID_LOCK:
            memory.said.pop(asked, None)
            memory.said[asked] = kept
            while len(memory.said) > SAID_KEPT:
                del memory.said[next(iter(memory.said))]
    return [replace(s, reasons=list(s.reasons)) for s in kept[1]]


def _suggested(description: str, line: frozenset[str], key: str, memory: _Memory, reference: ReferenceData,
               valid: frozenset[str], limit: int, default_gl: str) -> list[Suggestion]:  # fmt: skip
    """``suggest_gl`` worked out."""
    found: dict[str, Suggestion] = {}
    strong: dict[str, int] = {}  # account -> the highest priority of a source strong enough to code with it

    def add(gl: str, score: float, reason: str, source: str, auto: bool = False, cc: str = "") -> None:
        if gl not in valid:
            return
        s = found.setdefault(gl, Suggestion(gl))
        if score > s.score:
            s.score, s.source = score, source
            if cc:
                s.cost_center = cc
            if reason in s.reasons:
                s.reasons.remove(reason)
            s.reasons.insert(0, reason)  # the strongest reason first
        elif reason not in s.reasons:
            s.reasons.append(reason)
        if auto:
            strong[gl] = max(strong.get(gl, -1), PRIORITY[source])

    if default_gl:
        add(default_gl, 0.5, "the vendor's default GL account in the vendor master", "master")
    mine, others = _similar_lines(line, key, memory)

    # Changes of mind: similar lines AP recoded away from an account, and when (the most recent).
    moved_away: dict[str, int] = {}
    for hits in (*mine.values(), *others.values()):
        for _, p in hits:
            if p.was:
                moved_away[p.was] = min(moved_away.get(p.was, p.rank), p.rank)

    def stale(gl: str, newest: int) -> bool:
        """AP moved similar lines away from ``gl`` after the newest decision behind it: it may be out of date."""
        return moved_away.get(gl, newest) < newest

    best_of = {gl: max(hits, key=lambda h: h[0]) for gl, hits in mine.items()}
    # This vendor's closest line was just recoded against several approvals, unconfirmed: nothing codes the line
    # until AP confirms it (a slip and a change of mind look the same), and both accounts are offered.
    closest = max(best_of.values(), key=lambda h: (h[0], -h[1].rank), default=None)
    held = closest[1] if closest and closest[0] >= VENDOR_AUTO_SIMILARITY and closest[1].unconfirmed else None
    for gl, hits in mine.items():
        decisions = sum(p.decisions for _, p in hits)
        best_sim, best = max(hits, key=lambda h: (h[0], -h[1].rank))
        newest = min(p.rank for s, p in hits if s >= best_sim - VENDOR_AUTO_MARGIN)
        rival = max((s for g, (s, _) in best_of.items() if g != gl), default=0.0)
        # A similar line of this vendor coded to another account more recently: AP may have changed its mind.
        newer = any(s >= VENDOR_AUTO_SIMILARITY and p.rank < newest for g, (s, p) in best_of.items() if g != gl)
        auto = (best_sim >= VENDOR_AUTO_SIMILARITY and best_sim - rival >= VENDOR_AUTO_MARGIN and not newer
                and not stale(gl, newest) and not best.unconfirmed)  # fmt: skip
        add(gl, 0.6 + 0.3 * best_sim + 0.02 * min(decisions, 5),
            f"coded this way {_plural(decisions, 'time')} for similar lines from this vendor", "vendor", auto,
            best.cost_center)  # fmt: skip
        found[gl].units, found[gl].split = _units_of(memory, gl, hits)

    # The line AP changed: this one, or a similar line of this vendor (then named, B-607).
    held_line = "this line" if held is None or _normalise(held.description) == _normalise(description) else (
        f"a similar line, “{held.description}”,")  # fmt: skip
    if held is not None:
        add(held.was, found[held.gl_code].score - 0.01,
            f"approved {_plural(held.reversed, 'time')} for {held_line.rstrip(',')} before AP changed it",
            "vendor")  # fmt: skip

    # Each other vendor's evidence for an account: its closest line coded to it (one pass over the lines).
    weight: dict[str, float] = {}
    for gl, hits in others.items():
        closest_of: dict[str, float] = {}
        for s, p in hits:
            closest_of[p.vendor_key] = max(s, closest_of.get(p.vendor_key, 0.0))
        weight[gl] = sum(closest_of.values())
    evidence = sum(weight.values())
    for gl, hits in others.items():
        decisions = sum(p.decisions for _, p in hits)
        vendors = len({p.vendor_key for _, p in hits})
        best_sim, best = max(hits, key=lambda h: (h[0], h[1].decisions))
        # No other vendor coded similar lines to another account, or several agree on this one by far.
        agreed = len(weight) == 1 or (vendors >= OTHERS_AUTO_VENDORS and weight[gl] >= OTHERS_AUTO_SHARE * evidence)
        auto = (best_sim >= OTHERS_AUTO_SIMILARITY and agreed and not stale(gl, min(p.rank for _, p in hits))
                and not best.unconfirmed)  # fmt: skip
        # As alike and as often: the account more other vendors agree on comes first.
        add(gl, 0.35 + 0.3 * best_sim + 0.02 * min(decisions, 5) + 0.01 * weight[gl] / evidence,
            f"used for similar lines from {_plural(vendors, 'other vendor')}", "others", auto,
            best.cost_center)  # fmt: skip

    usual = memory.usual.get(key)
    if usual:
        gl, n = usual.most_common(1)[0]
        if n == sum(usual.values()) and n >= USUAL_MIN_LINES:
            add(gl, 0.45 + 0.01 * min(n, 5), f"all {n} different lines AP coded for this vendor went to it", "usual",
                n >= USUAL_AUTO_LINES and not stale(gl, memory.newest[key]))  # fmt: skip

    words_of_line = tokens(description)
    if words_of_line:
        line_stems = _stems(words_of_line)
        for gl, stems in _account_stems(reference.chart_of_accounts):
            shared = sorted(line_stems[stem] for stem in stems.intersection(line_stems))
            if len(shared) >= KEYWORD_MIN_OVERLAP:
                score = 0.15 + 0.35 * len(shared) / len(words_of_line)
                add(gl, score, "account description mentions " + ", ".join(f"“{w}”" for w in shared[:3]), "words")

    ranked = sorted(found.values(), key=lambda s: -s.score)
    if not ranked:
        return []
    # Strong enough on its own, and no other account has evidence as strong (then a person decides): this vendor's
    # own lines outrank its usual account, which outranks other vendors' lines.
    top = ranked[0]
    level = strong.get(top.gl_code)
    top.auto = held is None and level is not None and not any(strong.get(s.gl_code, -1) >= level for s in ranked[1:])
    for s in ranked:
        s.strong = strong.get(s.gl_code, -1)
    ranked = _meals_first(description, ranked, reference, valid)
    top = ranked[0]
    if held is not None:
        found[held.gl_code].check = (
            f"AP changed {held_line} from {held.was} (approved {_plural(held.reversed, 'time')}) to {held.gl_code} "
            "once: check which is right"
        )  # fmt: skip
    kept = [s for s in ranked if s.score >= 0.4 * top.score] if limit > 0 else ranked  # (0: the weak ones too)
    return kept[: limit if limit > 0 else ALL_KEPT]


# --- Whole account strings: every source, each a pair that may be posted to (D-212) --------------------------------

# Where a candidate's evidence comes from, the strongest first: this vendor's own approved lines, the supplier's
# coding (last year's journal and its approvals), then other vendors' lines and the vendor master's default, then
# the words of the accounts' names.
TIER = {"vendor": 3, "supplier": 2, "others": 1, "usual": 1, "master": 1, "words": 0}
# A line of a supplier whose lines all go to one account, this alike to the NAME of another account offered (and not
# alike to anything known of the supplier's own), reads like the other account: left for a person (B-2228).
ANOTHER_ALIKE = 0.5
MEALS_OFFERED = 3  # meals accounts offered for a line that reads as a meal on such a supplier's invoice
BOOK_SCORE = 0.1  # a new supplier's account from the journal as a whole: its score (``from_book``, 0 to 6.5) times this


def by_pairs(reference: ReferenceData, prior: Prior | None = None) -> bool:
    """Whether a line is suggested whole account strings (``candidates``): a chart of accounts was imported, or a
    journal is loaded. An install with neither suggests GL codes as before (``_from_memory``)."""
    return reference.pairs is not None or (prior is not None and prior.journal)


def status_said(reference: ReferenceData, business_unit: str, code: str) -> str:
    """Why an account string may not be offered, in plain words ("" when it may, or no chart was imported)."""
    pairs = reference.pairs
    if pairs is None:
        return ""
    status = pairs.status(business_unit, code)
    if status == chart.OK:
        return ""
    if status == chart.BU_INACTIVE:
        return f"business unit {business_unit} is no longer active"
    if status == chart.NOT_IN_CHART:
        if not pairs.has_code(code):
            return f"the chart has no account {code}"
        return f"business unit {business_unit} has no account {code}"
    if status == chart.NOT_POSTABLE:
        return f"{chart.join_account(business_unit, code)} may not be posted to"
    return pairs.why_reserved(business_unit, code)


def complete_pair(reference: ReferenceData, code: str, unit: str = "",
                  units: Iterable[str] = ()) -> tuple[str, bool] | None:  # fmt: skip
    """(the business unit a GL code is offered with, whether it is only a guess), or None when no pair of it may be
    offered. In this order: ``unit`` (the business unit of the past line the code came from) when the pair may be
    posted to; the only one of ``units`` (the business units the supplier's lines used, the most used first) that
    has the account; the only business unit of the chart that has it; then a guess, for a person to confirm: the
    supplier's most used business unit that has it, else the home business unit (``ChartSettings``). A past line on
    a business unit since closed so gives its account on another one, or nothing. With no chart imported the code
    goes with ``unit`` as it is."""
    pairs = reference.pairs
    if pairs is None:
        return unit, False
    if unit and pairs.offered(unit, code):
        return unit, False
    known = [u for u in dict.fromkeys(units) if u and pairs.offered(u, code)]
    if len(known) == 1:
        return known[0], False
    on = pairs.bus_of(code)
    if len(on) == 1:
        return on[0], False
    if known:
        return known[0], True
    home = pairs.settings.home_business_unit
    return (home, True) if home and pairs.offered(home, code) else None


def _placed(reference: ReferenceData, code: str, unit: str, units: list[str], source: str, score: float,
            reasons: list[str], **more: Any) -> Candidate | None:  # fmt: skip
    """A candidate for ``code``, completed as a pair that may be offered (None when there is none). When the
    business unit is not the one the evidence had, the reason says so."""
    found = complete_pair(reference, code, unit, units)
    if found is None:
        return None
    placed, guess = found
    reasons = list(reasons)
    if unit and placed != unit:
        reasons.append(f"{status_said(reference, unit, code)}: offered on {placed}")
    elif guess:
        reasons.append(f"nothing says which business unit: {placed} is a guess")
    return Candidate(account=chart.join_account(placed, code), business_unit=placed, code=code, score=score,
                     reason="; ".join(reasons), source=source, guess=guess, reasons=reasons,
                     name=account_name(reference, placed, code), **more)  # fmt: skip


def candidates(description: str, vendor_name: str, feedback: list[dict[str, Any]], reference: ReferenceData,
               limit: int = 20, default_gl: str = "", key: str | None = None, prior: Prior | None = None,
               units: Iterable[str] = (), addressed: str = "") -> list[Candidate]:  # fmt: skip
    """Up to ``limit`` account strings for one invoice line, the likeliest first, from every source: this vendor's
    approved lines like it, the supplier's coding (``prior``: ``supplier_prior.prior_for``), other vendors' lines
    like it, the vendor master's default (``default_gl``) and the words of the accounts' names. Each is a pair
    completed by ``complete_pair`` (``units``: the cost centers the vendor's approved lines used) and may be posted
    to: never a reserved subsidiary, a business unit that is closed or marked DO NOT USE, or an account that takes
    no postings. The first says whether it may code the line with no one picking (``Candidate.auto``): this vendor's
    own approved lines as before, or the supplier's one account (``Prior.sure``) when no approved line of the vendor
    like this one went elsewhere; never a guess of the business unit. A supplier last year's journal does not have
    is offered, with a chart imported, the accounts the journal as a whole has for the line's words and the vendor's
    name (``supplier_prior.from_book``, D-226), as other vendors' lines: never coded by them. A line left for a
    person has the supplier's accounts on the business unit it (or else the invoice's ship-to and attention lines,
    ``addressed``) names first (``_named_unit_first``).

    With no chart imported and no journal loaded these are ``suggest_gl``'s own, uncut: the GL code and the past
    line's cost center."""
    found = _from_memory(description, vendor_name, feedback, reference, 0, default_gl, key)
    if not by_pairs(reference, prior):
        return [Candidate(account=chart.join_account(_unit(s.cost_center), s.gl_code),
                          business_unit=_unit(s.cost_center), code=s.gl_code, score=s.score,
                          reason="; ".join(s.reasons), source=s.source, auto=s.auto, check=s.check,
                          reasons=list(s.reasons)) for s in found][:limit]  # fmt: skip
    valid = frozenset(reference.line_accounts())
    ranked = [c for c in rank_prior(prior, description, reference) if c.code in valid] if prior else []
    used = [u.business_unit for u in prior.accounts] if prior else []
    used = list(dict.fromkeys(u for u in [*used, *units] if u and u != UNASSIGNED))
    offered: dict[str, tuple[int, Candidate]] = {}

    def offer(tier: int, c: Candidate | None) -> None:
        if c is None:
            return
        there = offered.get(c.account)
        if there is None:
            offered[c.account] = (tier, c)
            return
        best, kept = (tier, c) if tier > there[0] else there
        other = there[1] if kept is c else c
        kept.reasons += [r for r in other.reasons if r not in kept.reasons]
        kept.reason = "; ".join(kept.reasons)
        kept.check = kept.check or other.check
        if not kept.of and other.of:
            kept.lines, kept.of, kept.share, kept.example = other.lines, other.of, other.share, other.example
        offered[c.account] = (best, kept)

    for c in ranked:
        offer(TIER["supplier"], _placed(reference, c.code, c.business_unit, [*prior.units_of.get(c.code, ()), *used],
                                        "supplier", c.score, c.reasons, lines=c.lines, of=c.of, share=c.share,
                                        example=c.example))  # fmt: skip
    for s in found:
        offer(TIER.get(s.source, 0), _placed(reference, s.gl_code, _unit(s.cost_center), used, s.source, s.score,
                                             s.reasons, check=s.check))  # fmt: skip
    if prior is not None and prior.book is not None and reference.pairs is not None:  # a new supplier (D-226)
        for b in from_book(prior.book, description, vendor_name, reference):
            offer(TIER["others"], _placed(reference, b.code, "", list(b.units), "others", BOOK_SCORE * b.score,
                                          b.reasons))  # fmt: skip
    ordered = [c for _, c in sorted(offered.values(), key=lambda found: (-found[0], -found[1].score))]
    if ordered:
        ordered[0].auto = _sure(ordered[0], offered, found, prior, description)
        if ordered[0].auto and offered[ordered[0].account][0] == TIER["supplier"]:
            ordered = _unless_another_kind(description, ordered, prior, reference, used)
        if not ordered[0].auto and not ordered[0].check:
            own = {c.account for tier, c in offered.values() if tier >= TIER["supplier"]}
            ordered = _named_unit_first(description, ordered, own, reference, prior, addressed)
    return ordered[:limit]


def _unit(cost_center: str) -> str:
    return "" if cost_center in ("", UNASSIGNED) else cost_center


def _named_unit_first(description: str, ordered: list[Candidate], own: set[str], reference: ReferenceData,
                      prior: Prior | None = None, addressed: str = "") -> list[Candidate]:  # fmt: skip
    """``ordered``, the accounts offered for a line left for a person, with the supplier's (``own``: those of this
    vendor's approved lines and of the supplier's coding) on a business unit the line names first, each saying so
    (B-2253): "Smartphone device balance - IT - Data Centre" is the supplier's telephone account on IT - Data
    Centre, whichever of its thirteen business units had most lines. A line names a unit by its name or its number
    (``supplier_prior.units_named``), or by a word only AP's remarks for the supplier's lines on that unit have
    ("4410 Alder", ``supplier_prior.unit_words_said``, D-226); a line that names none of the supplier's units goes by
    what the invoice is ``addressed`` to (its ship-to and attention lines). The unit named most strongly first; the
    rest as they were. It only orders: no line is coded by it."""
    for text, said in ((description, "the line"), (addressed, "the invoice's ship-to or attention")):
        named = {unit: (n, f"{said} names the business unit {reference.pairs.name_of(unit)}")
                 for unit, n in units_named(reference, text).items()} if text else {}  # fmt: skip
        for unit, words in unit_words_said(prior, reference, text).items() if text else ():
            quoted = ", ".join(f"“{w}”" for w in words)
            named.setdefault(unit, (len(words), f"{said} says {quoted}, as AP's remarks for this supplier's lines on "
                                                f"{reference.pairs.name_of(unit) or unit} alone did"))  # fmt: skip
        first = [c for c in ordered if c.account in own and c.business_unit in named]
        if first:
            break
    if not first:
        return ordered
    for c in first:
        c.reasons.insert(0, named[c.business_unit][1])
        c.reason = "; ".join(c.reasons)
    first.sort(key=lambda c: -named[c.business_unit][0])
    return [*first, *(c for c in ordered if not any(c is f for f in first))]


def _sure(top: Candidate, offered: dict[str, tuple[int, Candidate]], found: list[Suggestion],
          prior: Prior | None, description: str) -> bool:  # fmt: skip
    """Whether the first candidate may code the line ``description`` with no one picking (``candidates``)."""
    if top.guess or any(s.check for s in found):
        return False
    first = found[0] if found else None
    if first is not None and first.source == "vendor" and top.code == first.gl_code:
        # One description on several accounts of one voucher of the past coding is a split, not a change of mind.
        # The same words on several business units: who ordered decides, not the words. So does a supplier whose
        # lines to this account went to several, until lines like this one were approved here on one of them
        # ``UNIT_SURE_LINES`` times.
        units = [(u, n) for u, n in first.units if _unit(u)]
        used = prior.units_of.get(first.gl_code, ()) if prior else ()
        unsettled = ""
        if first.split:
            unsettled = "the past coding has lines like this one on several accounts on one day: a split"
        elif len(units) > 1:
            unsettled = f"lines like this one went to {len(units)} business units: the words do not say which"
        elif len(used) > 1 and (not units or units[0][1] < UNIT_SURE_LINES):
            unsettled = (f"this supplier's lines to {first.gl_code} went to {len(used)} business units: the words "
                         "do not say which")  # fmt: skip
        if unsettled:
            top.reasons.insert(0, unsettled)
            top.reason = "; ".join(top.reasons)
            return False
    if first is not None and first.auto and first.strong >= PRIORITY["vendor"]:
        return offered[top.account][0] == TIER["vendor"] and top.code == first.gl_code
    sure = prior.sure(description) if prior else None
    if sure is None or top.account != sure.account:
        return False
    return not any(tier == TIER["vendor"] and c.account != sure.account for tier, c in offered.values())


def _unless_another_kind(description: str, ordered: list[Candidate], prior: Prior, reference: ReferenceData,
                         units: list[str]) -> list[Candidate]:  # fmt: skip
    """``ordered``, whose first is the supplier's one account about to code the line with no one picking: as it is,
    unless the line plainly says another kind of thing (a lunch on a courier's invoice, B-2228). Then the first is
    left for a person, its ``check`` saying why, and the accounts of that other kind come next.

    Plainly: the line is not alike to anything known of the supplier's account (``supplier_prior.alike`` under
    ``SAID_ALIKE``: AP's remarks for it, the lines approved to it here, its name), and either it reads as a meal
    (``_MEAL_WORDS``) while the supplier's account is no meals account and neither its name nor AP's remarks for it
    read as one, or it is ``ANOTHER_ALIKE`` to the name of another account offered, or it says the name of another
    account of the chart, offered or not (``supplier_prior.reads_like``: a laptop, a hotel, consulting on a
    courier's invoice, B-2251). A supplier's own words that say nothing of any account ("waybill 77812") are no
    other kind: the line is coded."""
    top, usual = ordered[0], prior.accounts[0]
    if alike(prior, usual, description, reference) >= SAID_ALIKE:
        return ordered
    line, rest = keys(description), ordered[1:]
    said, others = "", []
    meals = frozenset(getattr(getattr(reference, "tax", None), "meals_gl_codes", None) or ())
    known = " ".join([top.name or account_name(reference, top.business_unit, top.code),
                      *(r.example for r in usual.remarks)])  # fmt: skip
    if _MEAL_WORDS.search(description) and top.code not in meals and not _MEAL_WORDS.search(known):
        said, others = "a meal", _meal_accounts(line, reference, meals, rest, units)
    else:
        other = max(rest, key=lambda c: similarity(line, name_words(reference, c.business_unit, c.code)), default=None)
        if other is None or similarity(line, name_words(reference, other.business_unit, other.code)) < ANOTHER_ALIKE:
            other = _named_account(description, top, rest, reference, units)
        if other is not None:
            said, others = f"{other.name or 'account'} ({other.account})", [other]
    if not said:
        return ordered
    top.auto = False
    top.check = f"This supplier's lines usually go to {top.account}, but this line reads like {said}: check it"
    return [top, *others, *(c for c in rest if not any(c is o for o in others))]


def _named_account(description: str, top: Candidate, offered: list[Candidate], reference: ReferenceData,
                   units: list[str]) -> Candidate | None:  # fmt: skip
    """The account of the chart, other than ``top``'s, whose name the line says (``supplier_prior.reads_like``: the
    first that may be offered), or None: the candidate already ``offered`` for it, else the account completed as a
    pair (``_placed``)."""
    codes = [code for code in reads_like(reference, description) if code != top.code]
    valid = frozenset(reference.line_accounts()) if codes else frozenset()
    for code in codes:
        there = next((c for c in offered if c.code == code), None)
        there = there or _placed(reference, code, "", units, "words", 0.0, ["the line says the account's name"])
        if code in valid and there is not None:
            return there
    return None


def _meal_accounts(line: frozenset[str], reference: ReferenceData, meals: frozenset[str],
                   offered: list[Candidate], units: list[str]) -> list[Candidate]:  # fmt: skip
    """The accounts a line that reads as a meal may go to, ``MEALS_OFFERED`` at most: the meals accounts (Settings),
    else the accounts whose name reads as a meal; those this supplier's lines went to first, then the name most
    alike to the line; those already ``offered`` as they are."""
    valid = reference.line_accounts()
    codes = sorted(meals & set(valid)) or [c for c in valid if _MEAL_WORDS.search(account_name(reference, "", c))]
    used = {c.code for c in offered if c.of}
    codes.sort(key=lambda code: (code not in used, -similarity(line, name_words(reference, "", code))))
    out = []
    for code in codes[:MEALS_OFFERED]:
        there = next((c for c in offered if c.code == code), None)
        there = there or _placed(reference, code, "", units, "words", 0.0, ["the line reads as a meal"])
        if there is not None:
            out.append(there)
    return out


def why_left(prior: Prior | None, ranked: list[Candidate], reference: ReferenceData,
             description: str | None = None) -> str:  # fmt: skip
    """Why a line was left for a person to code, said specifically (with a journal loaded: D-212): the supplier is
    new, its lines went to several accounts (with their shares), its usual account is no longer in the chart, too
    few lines, or AP's own approvals went elsewhere. ``ranked``: the line's ``candidates``; ``description``: the
    line, for which a line approved elsewhere that is not alike to it is no reason (``Prior.sure``)."""
    top = ranked[0] if ranked else None
    if top is not None and top.check:
        return f"{top.check}."
    if prior is None or (not prior.journal and not prior):
        if top is None:
            return "No earlier approval to learn from: pick the account."
        return f"Not sure enough to code it (maybe {top.account}: {'; '.join(top.reasons[:2])}): pick the account."
    if top is not None and top.source == "vendor" and not top.guess:  # this vendor's own lines like it
        said = f"Not sure enough to code it (maybe {top.account}: {'; '.join(top.reasons[:3])})"
    elif not prior:
        said = "A new supplier: not in last year's journal" + (f" ({prior.note})" if prior.note else "")
    else:
        use = prior.accounts[0]
        went = f"{use.lines} of this supplier's {prior.total_said()} went to {use.account}"
        gone = status_said(reference, use.business_unit, use.code) if use.business_unit else ""
        elsewhere = next((c for c in ranked if c.source == "vendor" and c.account != use.account), None)
        if gone and use.share >= SURE_SHARE:
            said = f"The supplier's usual account {use.account} is no longer in the chart ({gone})"
        elif len(prior.accounts) > 1 and use.share < SURE_SHARE:
            more = " / ..." if len(prior.accounts) > 4 else ""
            said = (f"This supplier's {prior.total_said()} went to {len(prior.accounts)} accounts: "
                    f"{prior.shares()}{more}")  # fmt: skip
        elif prior.lines < SURE_LINES:
            said = f"Only {went}: too few to code it alone"
        elif prior.latest_approved and prior.latest_approved != use.account and prior.sure(description) is None:
            said = (f"{went}, but the last line AP approved here ({prior.latest_approved_on}) went to "
                    f"{prior.latest_approved}")  # fmt: skip
        elif elsewhere is not None:
            said = f"{went}, but AP approved a line like this one to {elsewhere.account}"
        elif top is not None and top.guess:
            said = f"{went}; which business unit is not known ({top.business_unit} is a guess)"
        else:
            said = went
    return f"{said}: pick the account."
