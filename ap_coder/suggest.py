"""GL account suggestions for lines the AI could not code (or coded to an account that no longer exists).

Three local sources, strongest first, merged per GL account:

* this vendor's history: how reviewers coded similar lines from the same vendor
* other vendors' history: how similar lines were coded for anyone
* the GL accounts themselves: words the line shares with an account's description and category

No Azure call is made; suggestions appear instantly on the review screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .memory import aggregate, similarity, tokens, vendor_key
from .reference_data import UNASSIGNED, ReferenceData

VENDOR_MIN_SIMILARITY = 0.34
OTHERS_MIN_SIMILARITY = 0.5
KEYWORD_MIN_OVERLAP = 1


@dataclass
class Suggestion:
    gl_code: str
    cost_center: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


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
    """Up to ``limit`` GL accounts for one line, best first. ``feedback`` is ``Store.feedback_rows()``;
    ``default_gl`` is the vendor master's default account for this vendor, if any."""
    line = tokens(description)
    if not line and not default_gl:
        return []
    tax_gls = reference.tax.tax_gl_codes()
    valid = {c for c in reference.chart_of_accounts.codes if c not in tax_gls}
    found: dict[str, Suggestion] = {}

    def add(gl: str, score: float, reason: str, cc: str = "") -> None:
        if gl not in valid or gl == UNASSIGNED:
            return
        s = found.setdefault(gl, Suggestion(gl))
        if score > s.score:
            s.score = score
            if cc:
                s.cost_center = cc
        if reason not in s.reasons:
            s.reasons.append(reason)

    if default_gl:
        add(default_gl, 0.5, "the vendor's default GL account in the vendor master")
    key = vendor_key(vendor_name)
    mine: dict[str, list[Any]] = {}
    others: dict[str, list[Any]] = {}
    for p in aggregate(feedback):
        sim = similarity(line, p.tokens)
        same_vendor = p.vendor_key == key
        if sim < (VENDOR_MIN_SIMILARITY if same_vendor else OTHERS_MIN_SIMILARITY):
            continue
        (mine if same_vendor else others).setdefault(p.gl_code, []).append((sim, p))
    for gl, hits in mine.items():
        decisions = sum(p.decisions for _, p in hits)
        best_sim, best = max(hits, key=lambda h: (h[0], h[1].decisions))
        score = 0.6 + 0.3 * best_sim + 0.02 * min(decisions, 5)
        add(gl, score, f"coded this way {_plural(decisions, 'time')} for similar lines from this vendor",
            best.cost_center)  # fmt: skip
    for gl, hits in others.items():
        decisions = sum(p.decisions for _, p in hits)
        vendors = len({p.vendor_key for _, p in hits})
        best_sim, best = max(hits, key=lambda h: (h[0], h[1].decisions))
        score = 0.35 + 0.3 * best_sim + 0.02 * min(decisions, 5)
        add(gl, score, f"used for similar lines from {_plural(vendors, 'other vendor')}", best.cost_center)

    line_stems = _stems(line)
    for row in reference.chart_of_accounts.rows:
        gl = row[reference.chart_of_accounts.key_column]
        words = tokens(" ".join(str(v) for k, v in row.items() if k != reference.chart_of_accounts.key_column))
        shared = sorted(line_stems[stem] for stem in set(line_stems) & set(_stems(words)))
        if len(shared) >= KEYWORD_MIN_OVERLAP:
            score = 0.15 + 0.35 * len(shared) / len(line)
            add(gl, score, "account description mentions " + ", ".join(f"“{w}”" for w in shared[:3]))

    ranked = sorted(found.values(), key=lambda s: -s.score)
    return [s for s in ranked if s.score >= 0.4 * ranked[0].score][:limit] if ranked else []
