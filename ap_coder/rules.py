"""Fixed coding rules: lines that always go to the same GL account (and cost center), whatever the AI says.

A rule names a vendor, words a line contains, or both, and the GL account (and optionally the cost
center) to use: "Purolator → 5200", "any line with 'freight' → 5200", "Bell Canada, 'mobile' → 6420 /
CC300". They are applied when an invoice is processed, after the AI; the review screen says which lines
a rule changed, and the reviewer can still change them. The most specific rule wins (vendor and words,
then words, then vendor); among equals, the longer words, then the older rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .memory import vendor_key
from .schema import InvoiceCoding


@dataclass(frozen=True)
class Rule:
    vendor: str = ""
    contains: str = ""
    gl_code: str = ""
    cost_center: str = ""
    id: int = 0

    @property
    def label(self) -> str:
        parts = [self.vendor.strip() or "any vendor"]
        if self.contains.strip():
            parts.append(f"line contains “{self.contains.strip()}”")
        return ", ".join(parts)

    def matches(self, vendor: str, description: str) -> bool:
        if not (self.vendor.strip() or self.contains.strip()) or not self.gl_code.strip():
            return False
        if self.vendor.strip():
            wanted, actual = vendor_key(self.vendor), vendor_key(vendor)
            if not wanted or not actual or (wanted != actual and wanted not in actual):
                return False
        if self.contains.strip() and self.contains.strip().lower() not in " ".join(description.lower().split()):
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


def apply(coding: InvoiceCoding, rules: list[Rule]) -> tuple[InvoiceCoding, list[dict[str, Any]]]:
    """The coding with the rules applied, and what each rule changed:
    [{line_number, rule, gl_from, gl_to, cc_from, cc_to}]."""
    if not rules:
        return coding, []
    changes = []
    lines = []
    for li in coding.line_items:
        rule = rule_for(rules, coding.vendor_name, li.description)
        if rule is None:
            lines.append(li)
            continue
        gl = rule.gl_code.strip()
        cc = rule.cost_center.strip() or li.predicted_cost_center
        if gl != li.predicted_gl_code or cc != li.predicted_cost_center:
            changes.append(
                {
                    "line_number": li.line_number,
                    "rule": rule.label,
                    "gl_from": li.predicted_gl_code,
                    "gl_to": gl,
                    "cc_from": li.predicted_cost_center,
                    "cc_to": cc,
                }  # fmt: skip
            )
            li = li.model_copy(update={"predicted_gl_code": gl, "predicted_cost_center": cc})
        lines.append(li)
    if not changes:
        return coding, []
    return coding.model_copy(update={"line_items": lines}), changes


def describe(change: dict[str, Any]) -> str:
    to = change["gl_to"] + (f" · {change['cc_to']}" if change.get("cc_to") else "")
    before = change["gl_from"] + (f" · {change['cc_from']}" if change.get("cc_from") else "")
    return f"Line {change['line_number']}: {to} by the rule “{change['rule']}” (the AI chose {before or 'nothing'})"


def suggest(feedback: list[dict[str, Any]], existing: list[Rule], min_lines: int = 5) -> list[tuple[Rule, int]]:
    """Vendors whose lines reviewers (or the ERP history) always coded to one GL account: candidate rules,
    with the number of lines behind each. Vendors already covered by a vendor-only rule are left out."""
    covered = {vendor_key(r.vendor) for r in existing if r.vendor.strip() and not r.contains.strip()}
    by_vendor: dict[str, list[dict[str, Any]]] = {}
    for row in feedback:
        if row.get("vendor_key") and row.get("final_gl"):
            by_vendor.setdefault(row["vendor_key"], []).append(row)
    out = []
    for key, rows in by_vendor.items():
        gls = {r["final_gl"] for r in rows}
        if key in covered or len(rows) < min_lines or len(gls) != 1:
            continue
        ccs = {r.get("final_cc") or "" for r in rows}
        cc = ccs.pop() if len(ccs) == 1 else ""
        out.append((Rule(rows[0]["vendor_name"], "", gls.pop(), cc), len(rows)))
    return sorted(out, key=lambda x: -x[1])
