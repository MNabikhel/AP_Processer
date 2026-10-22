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

Both follow AP when it changes its mind (``current``): once a correction to a
new account is confirmed by one more decision, the older decisions for another
account no longer count. A single correction is not enough on its own: it may
be a slip, and then it still shows as a conflict with what AP did before.

Accepted lines count as approvals, so patterns the AI gets right grow
stronger over time; a reviewer can delete any bad record.
"""

from __future__ import annotations

import operator
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property, lru_cache
from typing import Any

ACCEPTED, CORRECTED = "accepted", "corrected"
# A line whose account the AI only guessed (``offline_coder.guessed``): what the reviewer approved is their own coding,
# learned from like any other, but neither confirmed nor corrected: coding accuracy leaves it out (B-475).
CODED = "coded"

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


_token_set_short = lru_cache(maxsize=131072)(_token_set)


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
    newest: int = 1 << 62  # place of its latest decision in the rows (newest first): 0 for the newest of all
    corrected_at: int | None = None  # place of its latest correction, None if never corrected
    since_fix: int = 0  # its decisions from its latest correction on, that one included

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
    turn would otherwise rebuild the value each time."""

    def __init__(self, slots: int = 2) -> None:
        self._size = max(1, slots)
        self._slots: tuple[tuple[list[dict[str, Any]], int, Any, Any], ...] = ()  # the latest first

    def get(self, rows: list[dict[str, Any]], extra: Any, build: Callable[[], Any]) -> Any:
        slots = self._slots  # one read: another thread may replace them meanwhile
        fingerprint = None
        for slot in slots:
            if slot[2] == extra and len(slot[0]) == len(rows) and all(map(operator.is_, slot[0], rows)):
                return self._keep(slots, slot, slot)  # the same rows (in the same order), in any list
        for slot in slots:
            if slot[2] == extra and len(slot[0]) == len(rows):
                fingerprint = _fingerprint(rows) if fingerprint is None else fingerprint
                if fingerprint == slot[1]:
                    return self._keep(slots, slot, (list(rows), fingerprint, extra, slot[3]))
        value = build()
        fresh = (list(rows), _fingerprint(rows) if fingerprint is None else fingerprint, extra, value)
        return self._keep(slots, None, fresh)

    def _keep(self, slots: tuple, old: Any, new: tuple) -> Any:
        self._slots = (new, *(s for s in slots if s is not old))[: self._size]
        return new[3]


_AGGREGATED = RowsCache()


def aggregate(rows: list[dict[str, Any]]) -> list[Pattern]:
    """The decisions in ``rows`` grouped by vendor, description and coding. Worked out once for the same rows
    (the result is shared: read it, don't change it)."""
    return _AGGREGATED.get(rows, None, lambda: _aggregate(rows))


def _aggregate(rows: list[dict[str, Any]]) -> list[Pattern]:
    groups: dict[tuple[str, str, str, str], Pattern] = {}
    for rank, r in enumerate(rows):  # newest first (``Store.feedback_rows``)
        key = (r["vendor_key"], _normalise(r["description"]), r["final_gl"], r.get("final_cc") or "")
        p = groups.get(key)
        if p is None:
            p = groups[key] = Pattern(
                r["vendor_key"], r["vendor_name"], r["description"], r["final_gl"], r.get("final_cc") or ""
            )
        if r["outcome"] == CORRECTED:
            p.corrections += 1
            if p.corrected_at is None:
                p.corrected_at, p.since_fix = rank, p.decisions
        else:
            p.approvals += 1
        p.newest = min(p.newest, rank)
        p.last_seen = max(p.last_seen, r.get("created_at") or "")
    return list(groups.values())


# A correction counts against older decisions once AP has confirmed it this many times (the correction included).
CONFIRMED_FIX = 2


def current(patterns: list[Pattern]) -> list[Pattern]:
    """``patterns`` (of one line, or of similar lines) without what AP has moved away from: the patterns of another
    account whose decisions are all older than the latest correction, once that correction is confirmed
    (``CONFIRMED_FIX``). Until then they all count: a single correction may be a slip."""
    fixes = [p for p in patterns if p.corrected_at is not None]
    if not fixes:
        return patterns
    latest = min(fixes, key=lambda p: p.corrected_at or 0)
    if latest.since_fix < CONFIRMED_FIX:
        return patterns
    return [p for p in patterns if p.gl_code == latest.gl_code or p.newest < (latest.corrected_at or 0)]


def _current_lines(patterns: list[Pattern]) -> list[Pattern]:
    by_line: dict[tuple[str, str], list[Pattern]] = defaultdict(list)
    for p in patterns:
        by_line[(p.vendor_key, _normalise(p.description))].append(p)
    return [p for same in by_line.values() for p in current(same)]


_CURRENT = RowsCache()


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
    patterns = _CURRENT.get(rows, None, lambda: _current_lines(aggregate(rows)))  # AP moved away: no example
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


def format_examples(examples: list[Pattern], with_cost_center: bool = True) -> str:
    if not examples:
        return ""
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
        lines.append(f"| {p.vendor_name} | {desc} | {p.gl_code} |{cc} {p.approvals} | {p.corrections} |")
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


def compare_with_history(coding: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per line: does the prediction agree with an established pattern for this vendor?"""
    key = vendor_key(coding.vendor_name)
    patterns = [p for p in aggregate(rows) if p.vendor_key == key]
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
        cc_votes: dict[str, int] = defaultdict(int)
        for p in similar:
            if p.gl_code == best_gl:
                cc_votes[p.cost_center] += p.decisions
        results.append(
            {
                "line_number": li.line_number,
                "status": "match" if li.predicted_gl_code == best_gl else "conflict",
                "history_gl": best_gl,
                "history_cost_center": max(cc_votes.items(), key=lambda kv: kv[1])[0],
                "decisions": total,
            }
        )
    return results
