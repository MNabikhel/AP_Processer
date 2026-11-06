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
from dataclasses import dataclass, field, replace
from typing import Any

from ..core.memory import (
    PAST_BYTES_KEPT,
    PAST_KEPT,
    PAST_ROWS_KEPT,
    Latest,
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
from .line_match import Index, keys

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
    r"|wine|beer|coffee|repas|d[îi]ners?|d[ée]jeuners?|traiteur|boissons?|vin)\b"
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
_SAID_LOCK = threading.Lock()


@dataclass
class _Older:
    """A vendor's lessons past the whole memory's cap (``WithPast.past``) by line, as the memory extended by them reads
    them (``_extended``), with the patterns of their lines as last made: carried forward when the memory moves on
    (``_carried``, D-173) instead of gone over again."""

    past: list[dict[str, Any]]  # the lessons, newest first
    valid: frozenset[str]  # the accounts whose decisions count
    head: int  # the number of past[0]: a lesson's place in ``past`` is its number less this (those put in front: lower)
    lines: dict[tuple[str, str], list[dict[str, Any]]]  # each line's decisions on those accounts, newest first
    first: dict[tuple[str, str], int]  # the number of each line's newest decision
    # each line's pattern, with the shared memory's decisions of the line it was made of (``_Memory.lines``' own list,
    # None for none): kept while they are the same list
    made: dict[tuple[str, str], tuple[Any, _Pattern]] = field(default_factory=dict)


_MEMORY = RowsCache()  # the lines of an invoice (and its review screen) share one index of the history
# A vendor's ``learning_rows`` past the whole memory's cap (``WithPast``), by whose they are: (the shared rows and the
# accounts their memory was built for, the vendor's older lessons it was extended by, the memory extended, the bytes it
# holds of its own: ``_own_bytes``), the latest used last (``_with_past``).
_PAST: dict[Any, tuple[list[dict[str, Any]], frozenset[str], list[dict[str, Any]], _Memory, int]] = {}
# The same vendors' older lessons by line (``_Older``), kept past a change of the shared rows (it holds none of their
# memory): what the next one's extended memory is carried forward from, ``PAST_KEPT`` vendors and ``PAST_ROWS_KEPT``
# lessons at most, the latest used last.
_OLDER: dict[Any, _Older] = {}
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
    rows' memory (B-1719)."""
    shared, past = feedback.shared, feedback.past
    with _PAST_LOCK:
        hit = _PAST.pop(feedback.key, None)
        if hit is not None and hit[0] is shared and hit[2] is past and hit[1] == valid:
            _PAST[feedback.key] = hit
            return hit[3]
        kept = _OLDER.get(feedback.key)
    base = _MEMORY.get(shared, valid, lambda: _build_memory(shared, valid),
                       lambda rows, kept: _updated_memory(rows, kept, shared, valid))  # fmt: skip
    # After an approval the store carries the vendor's older lessons forward (D-173): so are their lines here.
    older = _carried(kept, past) if kept is not None and kept.valid == valid else None
    if older is None:
        older = _grouped(past, valid)
    memory = _extended(base, shared, older)
    with _PAST_LOCK:
        for key in [k for k, kept in _PAST.items() if kept[0] is not shared]:
            del _PAST[key]
        _PAST.pop(feedback.key, None)
        own = 0 if memory is base else _own_bytes(memory)  # (nothing added: the shared memory itself, B-1781)
        _PAST[feedback.key] = (shared, valid, past, memory, own)
        while len(_PAST) > 1 and (len(_PAST) > PAST_KEPT
                                  or sum(len(kept[2]) for kept in _PAST.values()) > PAST_ROWS_KEPT
                                  or sum(kept[4] for kept in _PAST.values()) > PAST_BYTES_KEPT):  # fmt: skip
            del _PAST[next(iter(_PAST))]
        _OLDER.pop(feedback.key, None)
        _OLDER[feedback.key] = older
        while len(_OLDER) > 1 and (len(_OLDER) > PAST_KEPT
                                   or sum(len(kept.past) for kept in _OLDER.values()) > PAST_ROWS_KEPT):  # fmt: skip
            del _OLDER[next(iter(_OLDER))]
    return memory


def _own_bytes(memory: _Memory) -> int:
    """About what a memory extended for one vendor holds of its own: the lists and dicts ``_extended`` copied from the
    shared memory (their own size, not the patterns and words they share with it)."""
    index = memory.index
    return sum(sys.getsizeof(x) for x in (memory.patterns, memory.usual, memory.newest, index.entries, index.accounts,
                                          index.by_word, index.weight, index._by_letter))  # fmt: skip


def _grouped(past: list[dict[str, Any]], valid: frozenset[str]) -> _Older:
    """A vendor's older lessons by line: their decisions on ``valid`` accounts, newest first."""
    lines: dict[tuple[str, str], list[dict[str, Any]]] = {}
    first: dict[tuple[str, str], int] = {}
    for n, r in enumerate(past):
        if r.get("final_gl") in valid:
            line = (r["vendor_key"], _normalise(r["description"]))
            lines.setdefault(line, []).append(r)
            first.setdefault(line, n)
    return _Older(past, valid, 0, lines, first)


def _carried(kept: _Older, past: list[dict[str, Any]]) -> _Older | None:
    """``_grouped(past, kept.valid)`` from ``kept``, when ``past`` is lessons put in front of those kept, their oldest
    dropped (``memory.added_in_front``: what the store's ``_past_memory`` carries forward, D-173), going over those
    lessons only; the lines they are of lose their patterns (made again by ``_extended``). None when ``past`` is
    anything else (the store read them again), or more than ``memory.UPDATE_MOST`` lessons came and went."""
    moved = added_in_front(kept.past, past)
    if moved is None:
        return None
    head, valid = kept.head - moved, kept.valid
    first = dict(kept.first)
    front: dict[tuple[str, str], list[dict[str, Any]]] = {}  # the lines of the lessons put in front, their decisions
    for n, r in enumerate(past[:moved], start=head):
        if r.get("final_gl") in valid:
            line = (r["vendor_key"], _normalise(r["description"]))
            if line not in front:
                front[line], first[line] = [], n
            front[line].append(r)
    dropped = Counter((r["vendor_key"], _normalise(r["description"])) for r in kept.past[len(past) - moved:]
                      if r.get("final_gl") in valid)  # fmt: skip
    lines, made = dict(kept.lines), dict(kept.made)
    for line in [*front, *(line for line in dropped if line not in front)]:
        rows = kept.lines.get(line, [])
        rows = front.get(line, []) + rows[: len(rows) - dropped[line]]
        made.pop(line, None)
        if rows:
            lines[line] = rows
        else:
            del lines[line], first[line]
    return _Older(past, valid, head, lines, first, made)


def _extended(base: _Memory, shared: list[dict[str, Any]], older: _Older) -> _Memory:
    """``_build_memory(shared + older.past, older.valid)`` from ``base``, the memory of ``shared``, going over the
    lines of ``older`` only, and working out again only the patterns of those whose decisions changed since they were
    made (in ``older.past``, carried forward, or in the shared rows: ``base.lines``' list of the line is another). The
    older lessons rank after every shared row: each line they add decisions to keeps its latest decision (its account,
    rank and cost center) and has its run of decisions worked out again; a line only in them is a new pattern, after
    all of base's, in the order it is first met (ranked again as the shared rows change)."""
    if not older.lines:
        return base
    start = len(shared) - base.offset - older.head  # an older lesson's rank: its number plus this
    vendors = {line[0] for line in older.lines}
    patterns = list(base.patterns)
    where = {(p.vendor_key, _normalise(p.description)): n for n, p in enumerate(patterns) if p.vendor_key in vendors}
    new: list[tuple[int, _Pattern]] = []  # the lines only in the older lessons: (their first number, their pattern)
    for line, rows in older.lines.items():
        earlier = base.lines.get(line)  # the line's decisions in the shared rows, (rank, row) newest first
        made = older.made.get(line)
        if made is not None and made[0] is earlier:
            p = made[1]
            if earlier is None and p.rank != start + older.first[line]:
                p = replace(p, rank=start + older.first[line])
                older.made[line] = (None, p)
        else:
            if earlier is None:
                p = _pattern(start + older.first[line], rows[0], [r["final_gl"] for r in rows])
            else:
                p = _pattern(*earlier[0], [r["final_gl"] for _, r in earlier] + [r["final_gl"] for r in rows])
            older.made[line] = (earlier, p)
        if earlier is not None:
            if line in where:  # (a line without words is in no memory)
                patterns[where[line]] = p
        elif p.keys:
            new.append((older.first[line], p))
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
    return _Memory(patterns, index, usual, newest, offset=base.offset)


def _line_pattern(seq: list[tuple[int, dict[str, Any]]]) -> _Pattern:
    """The pattern of one line from its decisions, (rank, row) newest first."""
    rank, r = seq[0]
    return _pattern(rank, r, [x["final_gl"] for _, x in seq])


def _pattern(rank: int, r: dict[str, Any], accounts: list[str]) -> _Pattern:
    """The pattern of one line whose latest decision is ``r`` (ranked ``rank``), from its decisions' accounts, newest
    first: the latest decision wins over older ones for another account."""
    latest = latest_decision(accounts, changed_account(r))
    return _Pattern(r["vendor_key"], r["description"], keys(r["description"]), latest.value, r.get("final_cc") or "",
                    rank, latest)  # fmt: skip


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
# few charts: every line suggested reads them all (1,500 accounts, each line of each check of an invoice).
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
        while len(_ACCOUNT_STEMS) > 4:
            del _ACCOUNT_STEMS[next(iter(_ACCOUNT_STEMS))]
    return found


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
) -> list[Suggestion]:
    """Up to ``limit`` GL accounts for one line, best first. ``feedback`` is ``Store.feedback_rows()`` (newest
    first); ``default_gl`` is the vendor master's default account for this vendor, if any. Never a tax account or
    an account that is not in the chart of accounts. Worked out once for the same line, vendor and default, kept with
    the memory and the reference data they came from (``_Memory.said``, ``SAID_KEPT`` lines, D-159): every check of
    the review screen asks it again of each line. Each caller gets copies."""
    line = keys(description)
    if not line and not tokens(description) and not default_gl:
        return []
    tax_gls = reference.tax.tax_gl_codes()
    valid = frozenset(c for c in reference.chart_of_accounts.codes if c not in tax_gls and c != UNASSIGNED)
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
    ranked = _meals_first(description, ranked, reference, valid)
    top = ranked[0]
    if held is not None:
        found[held.gl_code].check = (
            f"AP changed {held_line} from {held.was} (approved {_plural(held.reversed, 'time')}) to {held.gl_code} "
            "once: check which is right"
        )  # fmt: skip
    return [s for s in ranked if s.score >= 0.4 * top.score][:limit]
