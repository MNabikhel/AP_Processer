"""The fixed coding rules (``coding/rules.py``, D-217): the rules, the history of their changes and the lines each
rule coded.

A rule's number (``coding_rules.id``) is its own for good: saving the rules changes a rule in place (``created_at``
and ``created_by`` stay what they were), a number is never given to another rule, and a removed rule brought back by
an undo has its number again. Every add, change and removal is one row of ``coding_rule_history`` (which rule, when,
who, the rule before and after as JSON); ``undo_rule_change`` puts a rule back as it was before an entry, and is an
entry itself.

``summary_rules`` (one row per invoice and rule: how many of the invoice's lines the rule coded, from each line's
``decided_by``, D-212) is kept with the invoices' summary (``summary.py`` fills it by ``rule_rows``): the rules page
counts off it and the invoice directory filters on it (``RULE_FILTER``), both off its index.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any

from ...coding.rules import Rule, RuleChange
from ._base import _clean_code, _now
from .dialect import begin_write

KEPT = ("vendor", "contains", "gl_code", "cost_center")  # what a rule says: what the history keeps of it
HISTORY_SHOWN = 100  # the history's entries a page reads at most, newest first
# The day this database began to keep which lines each rule coded (a setting, written once: ``migrations``).
USE_SINCE = "rule_use_since"
UNDONE, GONE, SAME, CHANGED_SINCE = "undone", "gone", "same", "changed since"  # what ``undo_rule_change`` did
# The invoices with a line coded by one rule (bound: its number), as a condition on the summary's ``id``.
RULE_FILTER = "id IN (SELECT id FROM summary_rules WHERE rule_id = ?)"
RULES_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS summary_rules (id INTEGER NOT NULL, rule_id INTEGER NOT NULL, n INTEGER NOT NULL, "
    "PRIMARY KEY (id, rule_id))",
    "CREATE INDEX IF NOT EXISTS summary_rules_rule ON summary_rules (rule_id, id, n)",
)


def rule_rows(invoice_id: int, coding: str | None) -> list[tuple[int, int, int]]:
    """(invoice, rule, lines) for each saved rule that coded a line of ``coding`` (the invoice's coding as its JSON
    text): the lines whose ``decided_by`` names the rule's number. A coding from before lines kept it gives none."""
    if not coding or '"rule_id"' not in coding:
        return []
    try:
        lines = json.loads(coding).get("line_items") or []
    except (ValueError, AttributeError):
        return []
    found: Counter[int] = Counter()
    for line in lines:
        by = line.get("decided_by") if isinstance(line, dict) else None
        rule = by.get("rule_id") if isinstance(by, dict) and by.get("source") == "rule" else None
        if type(rule) is int and rule > 0:
            found[rule] += 1
    return [(invoice_id, rule, n) for rule, n in found.items()]


def _values(rule: Rule) -> tuple[str, str, str, str]:
    return rule.vendor.strip(), rule.contains.strip(), _clean_code(rule.gl_code), _clean_code(rule.cost_center)


def _rule(row: Any) -> Rule:
    return Rule(row["vendor"], row["contains"], row["gl_code"], row["cost_center"], row["id"], row["created_at"],
                row["created_by"] or "")  # fmt: skip


def _kept(rule: Rule | None) -> str | None:
    """A rule as the history keeps it (JSON), with who added it and when: an undo of its removal has them again."""
    if rule is None:
        return None
    return json.dumps({**dict(zip(KEPT, _values(rule), strict=True)), "created_at": rule.created_at,
                       "created_by": rule.created_by})  # fmt: skip


def _read(text: str | None, rule_id: int) -> Rule | None:
    if not text:
        return None
    kept = json.loads(text)
    return Rule(*(str(kept.get(k) or "") for k in KEPT), rule_id, str(kept.get("created_at") or ""),
                str(kept.get("created_by") or ""))  # fmt: skip


def _change(row: Any) -> RuleChange:
    return RuleChange(row["id"], row["rule_id"], row["at"], row["actor"] or "", _read(row["before"], row["rule_id"]),
                      _read(row["after"], row["rule_id"]), row["undo_of"])  # fmt: skip


class RulesMixin:
    # --- Fixed coding rules ---------------------------------------------------------------------------

    def coding_rules(self) -> list[Rule]:
        with self._conn() as conn:
            return [_rule(r) for r in conn.execute("SELECT * FROM coding_rules ORDER BY id")]

    @staticmethod
    def _put_rule(conn: sqlite3.Connection, before: Rule | None, after: Rule | None, actor: str | None, now: str,
                  undo_of: int | None = None) -> int:  # fmt: skip
        """One rule added (no ``before``; with ``after``'s number when it has one: a removed rule brought back),
        removed (no ``after``) or changed in place, and the entry of the history that says so. Returns the entry's
        number."""
        if after is None:
            rule_id = before.id
            conn.execute("DELETE FROM coding_rules WHERE id = ?", (rule_id,))
        elif before is None:
            after = Rule(*_values(after), after.id, after.created_at or now, after.created_by or actor or "")
            rule_id = conn.execute(
                "INSERT INTO coding_rules (id, vendor, contains, gl_code, cost_center, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (after.id or None, *_values(after), after.created_at, after.created_by or None),
            ).lastrowid
        else:
            rule_id = before.id
            after = Rule(*_values(after), rule_id, before.created_at, before.created_by)
            conn.execute("UPDATE coding_rules SET vendor = ?, contains = ?, gl_code = ?, cost_center = ? WHERE id = ?",
                         (*_values(after), rule_id))  # fmt: skip
        return conn.execute(
            "INSERT INTO coding_rule_history (rule_id, at, actor, before, after, undo_of) VALUES (?, ?, ?, ?, ?, ?)",
            (rule_id, now, actor, _kept(before), _kept(after), undo_of),
        ).lastrowid

    def save_coding_rules(self, rules: list[Rule], actor: str | None = None) -> int:
        """The rules as they are from now on (rules without a GL account, or without a vendor and words, are left
        out; the same rule twice is kept once). A rule with the number of a saved one is that rule, changed in place
        when it says something else; one without a number is the saved rule that says the same, else a new rule. A
        saved rule that is none of them is removed. An unchanged rule is left as it is: its number (its age, the
        tie-breaker), who added it and when (B-2231). Each add, change and removal is an entry of the history."""
        keep = [r for r in rules if r.complete]
        with self._conn() as conn:
            begin_write(conn)
            old = {r["id"]: _rule(r) for r in conn.execute("SELECT * FROM coding_rules ORDER BY id")}
            same: dict[tuple[str, ...], int] = {}
            for rule_id, rule in old.items():
                same.setdefault(_values(rule), rule_id)
            said: dict[tuple[str, ...], Rule] = {}  # what each rule to keep says: once
            for r in keep:
                said.setdefault(_values(r), r)
            # Which saved rule each one is: the one of its number first, then the one that says the same.
            which: dict[tuple[str, ...], int] = {}
            for values, r in said.items():
                if r.id in old and r.id not in which.values():
                    which[values] = r.id
            for values in said:
                if values not in which:
                    which[values] = same[values] if same.get(values, 0) not in (0, *which.values()) else 0
            taken = set(which.values())
            now, counts = _now(), Counter()
            for values in said:
                before = old.get(which[values])
                if before is not None and _values(before) == values:
                    continue
                counts["changed" if before else "added"] += 1
                self._put_rule(conn, before, Rule(*values), actor, now)
            for rule_id, rule in old.items():
                if rule_id not in taken:
                    counts["removed"] += 1
                    self._put_rule(conn, rule, None, actor, now)
            self._log(conn, "rules_changed", actor=actor, detail={"rules": len(said), **counts})
        return len(keep)

    def coding_rule_history(self, rule_id: int = 0, limit: int = HISTORY_SHOWN) -> list[RuleChange]:
        """The rules' history, newest first, ``limit`` entries at most; of one rule with ``rule_id``."""
        where, params = ("WHERE rule_id = ?", [rule_id]) if rule_id else ("", [])
        with self._conn() as conn:
            rows = conn.execute(f"SELECT * FROM coding_rule_history {where} ORDER BY id DESC LIMIT ?",
                                [*params, limit])  # fmt: skip
            return [_change(r) for r in rows]

    def coding_rule_change(self, change_id: int) -> RuleChange | None:
        """One entry of the rules' history, or None."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM coding_rule_history WHERE id = ?", (change_id,)).fetchone()
        return _change(row) if row else None

    def undo_rule_change(self, change_id: int, actor: str | None = None, overwrite: bool = False
                         ) -> tuple[str, RuleChange | None]:  # fmt: skip
        """Put a rule back as it was before entry ``change_id`` of the history: an added rule is removed, a removed
        one comes back with its number, a changed one says what it said. The undo is an entry itself (returned with
        ``UNDONE``), so it can be undone. ``GONE``: no such entry. ``SAME``: the rule is as it was before the entry
        already. ``CHANGED_SINCE``: the rule is not as the entry left it (it changed again since) and ``overwrite``
        is not set: nothing is done, for the caller to ask first."""
        with self._conn() as conn:
            begin_write(conn)
            row = conn.execute("SELECT * FROM coding_rule_history WHERE id = ?", (change_id,)).fetchone()
            if row is None:
                return GONE, None
            change = _change(row)
            now = conn.execute("SELECT * FROM coding_rules WHERE id = ?", (change.rule_id,)).fetchone()
            current = _rule(now) if now else None
            if current == change.before:
                return SAME, None
            if current != change.after and not overwrite:
                return CHANGED_SINCE, None
            entry = self._put_rule(conn, current, change.before, actor, _now(), undo_of=change.id)
            self._log(conn, "rules_changed", actor=actor,
                      detail={"rule": change.rule_id, "undone": change.id})  # fmt: skip
            done = conn.execute("SELECT * FROM coding_rule_history WHERE id = ?", (entry,)).fetchone()
            return UNDONE, _change(done)

    def rule_use(self) -> dict[int, tuple[int, int]]:
        """{a rule's number: (the lines it coded, on how many invoices)}, from the invoices' summary: the lines
        whose coding names the rule (as approved, else as AP Coder coded them)."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            counted = conn.execute("SELECT rule_id, SUM(n), COUNT(*) FROM summary_rules GROUP BY rule_id")
            return {r[0]: (r[1], r[2]) for r in counted}
