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

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..core.memory import CORRECTED, RowsCache, _normalise, tokens, vendor_key
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
# A correction that reverses this many approvals in a row of the same line is suggested, not coded with, until
# AP confirms it once: a slip and a deliberate change of account look the same until the next decision.
UNCONFIRMED_REVERSAL = 5
PRIORITY = {"vendor": 3, "usual": 2, "master": 1, "others": 1, "words": 0}


@dataclass
class Suggestion:
    gl_code: str
    cost_center: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    source: str = ""  # where the best evidence came from: "vendor", "others", "usual", "master" or "words"
    auto: bool = False  # strong enough to code the line without a person picking it


@dataclass
class _Pattern:
    """The lines AP coded for one vendor with one description (case and punctuation aside)."""

    vendor_key: str
    description: str
    keys: frozenset[str]
    gl_code: str
    cost_center: str
    rank: int  # how recent its latest decision is: 0 for the newest of all
    decisions: int = 1  # approvals of ``gl_code`` since the description was last coded otherwise
    was: str = ""  # the account AP coded it to before (AP changed it), "" if never changed
    reversed: int = 0  # decisions in a row for ``was`` before AP changed it
    corrected: bool = False  # the latest decision changed the account proposed (or filled in a blank)

    @property
    def unconfirmed(self) -> bool:
        """AP has just changed this line's account against several older decisions, and not confirmed it yet."""
        return self.decisions == 1 and self.corrected and self.reversed >= UNCONFIRMED_REVERSAL


@dataclass
class _Memory:
    patterns: list[_Pattern]
    index: Index
    usual: dict[str, Counter[str]]  # vendor key -> {account: different lines coded to it}
    newest: dict[str, int]  # vendor key -> rank of its latest decision


_MEMORY = RowsCache()  # the lines of an invoice (and its review screen) share one index of the history


def _memory(feedback: list[dict[str, Any]], valid: frozenset[str]) -> _Memory:
    """What AP approved, as patterns and their word index, built once for the same rows. ``feedback`` is newest
    first (``Store.feedback_rows``): the latest decision for a vendor's line wins over older ones that coded it to
    another account."""
    return _MEMORY.get(feedback, valid, lambda: _build_memory(feedback, valid))


def _build_memory(feedback: list[dict[str, Any]], valid: frozenset[str]) -> _Memory:
    decisions: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}  # each line's decisions, newest first
    for rank, r in enumerate(feedback):
        if r.get("final_gl") in valid:
            decisions.setdefault((r["vendor_key"], _normalise(r["description"])), []).append((rank, r))
    groups: dict[tuple[str, str], _Pattern] = {}
    for key, seq in decisions.items():
        rank, r = seq[0]
        gl = r["final_gl"]
        run = next((i for i, (_, x) in enumerate(seq) if x["final_gl"] != gl), len(seq))
        p = _Pattern(r["vendor_key"], r["description"], keys(r["description"]), gl, r.get("final_cc") or "", rank,
                     run, corrected=r.get("outcome") == CORRECTED)  # fmt: skip
        if run < len(seq):  # older decisions for another account: superseded
            p.was = seq[run][1]["final_gl"]
            p.reversed = next((i for i, (_, x) in enumerate(seq[run:]) if x["final_gl"] != p.was), len(seq) - run)
        groups[key] = p
    patterns = [p for p in groups.values() if p.keys]
    usual: dict[str, Counter[str]] = defaultdict(Counter)
    newest: dict[str, int] = {}
    for p in patterns:
        usual[p.vendor_key][p.gl_code] += 1
        newest[p.vendor_key] = min(newest.get(p.vendor_key, p.rank), p.rank)
    index = Index([p.keys for p in patterns], [p.gl_code for p in patterns])
    return _Memory(patterns, index, dict(usual), newest)


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


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def suggest_gl(
    description: str,
    vendor_name: str,
    feedback: list[dict[str, Any]],
    reference: ReferenceData,
    limit: int = 3,
    default_gl: str = "",
) -> list[Suggestion]:
    """Up to ``limit`` GL accounts for one line, best first. ``feedback`` is ``Store.feedback_rows()`` (newest
    first); ``default_gl`` is the vendor master's default account for this vendor, if any. Never a tax account or
    an account that is not in the chart of accounts."""
    line = keys(description)
    if not line and not tokens(description) and not default_gl:
        return []
    tax_gls = reference.tax.tax_gl_codes()
    valid = frozenset(c for c in reference.chart_of_accounts.codes if c not in tax_gls and c != UNASSIGNED)
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

    key = vendor_key(vendor_name)
    memory = _memory(feedback, valid)
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

    # Each other vendor's evidence for an account: its closest line coded to it.
    weight = {gl: sum(max(s for s, p in hits if p.vendor_key == k) for k in {p.vendor_key for _, p in hits})
              for gl, hits in others.items()}  # fmt: skip
    for gl, hits in others.items():
        decisions = sum(p.decisions for _, p in hits)
        vendors = len({p.vendor_key for _, p in hits})
        best_sim, best = max(hits, key=lambda h: (h[0], h[1].decisions))
        # No other vendor coded similar lines to another account, or several agree on this one by far.
        agreed = len(weight) == 1 or (
            vendors >= OTHERS_AUTO_VENDORS and weight[gl] >= OTHERS_AUTO_SHARE * sum(weight.values())
        )  # fmt: skip
        auto = (best_sim >= OTHERS_AUTO_SIMILARITY and agreed and not stale(gl, min(p.rank for _, p in hits))
                and not best.unconfirmed)  # fmt: skip
        add(gl, 0.35 + 0.3 * best_sim + 0.02 * min(decisions, 5),
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
        for row in reference.chart_of_accounts.rows:
            gl = row[reference.chart_of_accounts.key_column]
            words = tokens(" ".join(str(v) for k, v in row.items() if k != reference.chart_of_accounts.key_column))
            shared = sorted(line_stems[stem] for stem in set(line_stems) & set(_stems(words)))
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
    top.auto = level is not None and not any(strong.get(s.gl_code, -1) >= level for s in ranked[1:])
    return [s for s in ranked if s.score >= 0.4 * top.score][:limit]
