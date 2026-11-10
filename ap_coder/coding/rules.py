"""Fixed coding rules: lines that always go to the same GL account (and cost center), whatever the AI says.

A rule names a vendor, words a line contains, or both, and the GL account (and optionally the cost
center) to use: "Purolator → 5200", "any line with 'freight' → 5200", "Bell Canada, 'mobile' → 6420 /
CC300". They are applied when an invoice is processed, after the AI; the review screen says which lines
a rule changed, and the reviewer can still change them. The most specific rule wins (vendor and words,
then words, then vendor); among equals, the longer words, then the older rule.

Entry points: ``apply`` (an invoice's lines), ``rule_for``, ``describe`` and ``suggest`` (rules from the history).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..core import chart
from ..core.keys import plain as _plain
from ..core.memory import vendor_key
from ..core.schema import InvoiceCoding, decided


def _has_words(name: str, wanted: str) -> bool:
    """``wanted``'s words appear together, as whole words, in ``name``: "bell" is in "bell canada", not in
    "campbell supplies"."""
    if not name or not wanted:
        return False
    return f" {_plain(wanted)} " in f" {_plain(name)} "


def _starts_word(text: str, wanted: str) -> bool:
    """``wanted`` appears in ``text`` from the start of a word: "rent" is in "office rent" and "rental", not in
    "current"; "monitor" is in "monitors"."""
    return re.search(r"(?<![0-9a-z])" + re.escape(_plain(wanted)), _plain(text)) is not None


@dataclass(frozen=True)
class Rule:
    vendor: str = ""
    contains: str = ""
    gl_code: str = ""
    cost_center: str = ""
    id: int = 0  # the saved rule's number: its own through every change, never given to another (0: not saved yet)
    created_at: str = field(default="", compare=False)  # when it was first added, and by whom
    created_by: str = field(default="", compare=False)

    @property
    def label(self) -> str:
        parts = [self.vendor.strip() or "any vendor"]
        if self.contains.strip():
            parts.append(f"line contains “{self.contains.strip()}”")
        return ", ".join(parts)

    @property
    def target(self) -> str:
        """The account the rule codes to as one string, ``business unit.object.subsidiary`` (the cost center is the
        business unit); the GL code alone for a rule that leaves the line's cost center as it is."""
        return chart.join_account(self.cost_center, self.gl_code)

    @property
    def complete(self) -> bool:
        """Whether it can be a rule at all: a GL account, and a vendor or words."""
        return bool(self.gl_code.strip() and (self.vendor.strip() or self.contains.strip()))

    def matches(self, vendor: str, description: str) -> bool:
        if not (self.vendor.strip() or self.contains.strip()) or not self.gl_code.strip():
            return False
        if self.vendor.strip() and not _has_words(vendor_key(vendor), vendor_key(self.vendor)):
            return False
        if self.contains.strip() and not _starts_word(description, self.contains):
            return False
        return True

    def rank(self) -> tuple[int, int, int]:
        """Sort key: most specific first."""
        both = bool(self.vendor.strip()) and bool(self.contains.strip())
        words = bool(self.contains.strip())
        return (-(2 * both + words), -len(self.contains.strip()), self.id)


def rule_for(rules: list[Rule], vendor: str, description: str) -> Rule | None:
    candidates = [r for r in rules if r.matches(vendor, description)]
    return min(candidates, key=Rule.rank) if candidates else None


RULE_REASON = "Coded by the coding rule"


def decided_by_rule(rule: Rule, was: Any = None, agreed: bool = False) -> dict[str, Any]:
    """What a line a rule coded keeps of it (``LineItem.decided_by``, D-212): the rule's id (0: not saved yet) and
    its words as they were, the source of the proposal it replaced (``was``), and whether it only agreed with it."""
    return decided("rule", rule_id=rule.id, rule=rule.label, was=(was or {}).get("source"), agreed=agreed or None)


def apply(coding: InvoiceCoding, rules: list[Rule]) -> tuple[InvoiceCoding, list[dict[str, Any]]]:
    """The coding with the rules applied, and what each rule changed:
    [{line_number, rule, gl_from, gl_to, cc_from, cc_to}]. Every line a rule matches says so (``decided_by``), also
    one the rule only agrees with: such a line is in no change, and keeps its reasoning."""
    if not rules:
        return coding, []
    changes = []
    lines = []
    matched = False
    for li in coding.line_items:
        rule = rule_for(rules, coding.vendor_name, li.description)
        if rule is None:
            lines.append(li)
            continue
        matched = True
        gl = rule.gl_code.strip()
        cc = rule.cost_center.strip() or li.predicted_cost_center
        if gl == li.predicted_gl_code and cc == li.predicted_cost_center:
            if (li.decided_by or {}).get("source") != "rule":
                li = li.model_copy(update={"decided_by": decided_by_rule(rule, li.decided_by, agreed=True)})
        else:
            from ..ai.offline_coder import guessed

            changes.append(
                {
                    "line_number": li.line_number,
                    "rule": rule.label,
                    "gl_from": li.predicted_gl_code,
                    "gl_to": gl,
                    "cc_from": li.predicted_cost_center,
                    "cc_to": cc,
                    # the AI's answer the rule replaced was only a guess: no measure of its accuracy (B-475)
                    "from_guess": guessed(li.reasoning_justification),
                }  # fmt: skip
            )
            # The rule's coding, said so: no longer the chat model's guess on the review screen (B-475).
            li = li.model_copy(update={
                "predicted_gl_code": gl, "predicted_cost_center": cc, "candidates": [],
                "reasoning_justification": f"{RULE_REASON} “{rule.label}”",
                "decided_by": decided_by_rule(rule, li.decided_by),
            })  # fmt: skip
        lines.append(li)
    if not matched:
        return coding, []
    return coding.model_copy(update={"line_items": lines}), changes


def describe(change: dict[str, Any]) -> str:
    to = change["gl_to"] + (f" · {change['cc_to']}" if change.get("cc_to") else "")
    before = change["gl_from"] + (f" · {change['cc_from']}" if change.get("cc_from") else "")
    return f"Line {change['line_number']}: {to} by the rule “{change['rule']}” (the AI chose {before or 'nothing'})"


def suggest(feedback: list[dict[str, Any]], existing: list[Rule], min_lines: int = 5) -> list[tuple[Rule, int]]:
    """Vendors whose lines reviewers (or the ERP history) always coded to one GL account: candidate rules,
    with the number of lines behind each. Vendors already covered by a vendor-only rule are left out."""
    covered = {_plain(vendor_key(r.vendor)) for r in existing if r.vendor.strip() and not r.contains.strip()}
    by_vendor: dict[str, list[dict[str, Any]]] = {}
    for row in feedback:
        if row.get("vendor_key") and row.get("final_gl"):
            by_vendor.setdefault(row["vendor_key"], []).append(row)
    out = []
    for key, rows in by_vendor.items():
        gls = {r["final_gl"] for r in rows}
        if _plain(key) in covered or len(rows) < min_lines or len(gls) != 1:
            continue
        ccs = {r.get("final_cc") or "" for r in rows}
        cc = ccs.pop() if len(ccs) == 1 else ""
        out.append((Rule(rows[0]["vendor_name"], "", gls.pop(), cc), len(rows)))
    return sorted(out, key=lambda x: -x[1])


# --- The history of the rules -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleChange:
    """One entry of the rules' history (``Store.coding_rule_history``): a rule added (no ``before``), removed (no
    ``after``) or changed, by whom and when. ``undo_of``: the entry it undid, when it is an undo."""

    id: int
    rule_id: int
    at: str
    actor: str
    before: Rule | None
    after: Rule | None
    undo_of: int | None = None


def _account(rule: Rule, whole: bool) -> str:
    """Where a rule codes to, named: one account string with a chart of accounts loaded (``whole``), else the GL
    account and the cost center."""
    if whole:
        return f"account {rule.target}"
    return f"GL account {rule.gl_code}" + (f", cost center {rule.cost_center}" if rule.cost_center else "")


def _parts_changed(before: Rule, after: Rule, whole: bool) -> list[str]:
    parts = []
    if before.label != after.label:
        parts.append(f"now ‘{after.label}’")
    if whole:
        if before.target != after.target:
            parts.append(f"account {before.target} → {after.target}")
        return parts
    if before.gl_code != after.gl_code:
        parts.append(f"GL account {before.gl_code} → {after.gl_code}")
    if before.cost_center != after.cost_center:
        parts.append(f"cost center {before.cost_center or 'none'} → {after.cost_center or 'none'}")
    return parts


def change_words(change: RuleChange, whole: bool = False) -> str:
    """An entry of the history in plain words: "Dana changed ‘Courier’: account 0021061010.7340 → 0021061010.7350",
    "Lee added ‘any vendor, line contains “freight”’: GL account 5200", "Dana removed ‘Courier’ (account ...)"; an
    undo says it is one. ``whole``: a chart of accounts is loaded, the account is said as one string."""
    who = (change.actor or "").strip() or "Someone"
    before, after = change.before, change.after
    if before is None and after is None:
        return f"{who} changed a rule"
    if before is None:
        said = f"{who} added ‘{after.label}’: {_account(after, whole)}"
    elif after is None:
        said = f"{who} removed ‘{before.label}’ ({_account(before, whole)})"
    else:
        said = f"{who} changed ‘{before.label}’: {'; '.join(_parts_changed(before, after, whole))}"
    return f"{said} (an undo)" if change.undo_of else said
