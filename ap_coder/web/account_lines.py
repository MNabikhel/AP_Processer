"""The review screen's lines for an install with a chart of accounts (D-214): one Account box a line that holds the
whole account string (business unit.object.subsidiary), and under it what the line says about itself.

``in_use`` says whether the screen draws that row (a chart is imported); ``posted`` reads the box as posted (the
line's two stored codes: the string is never replaced by the account the line had); ``account_text`` is the box's
text for a line's two codes; ``row_views`` gives each line of the draft what its row shows: the account's name, its
business unit's name, why it was coded so (``why_words``, from ``LineItem.decided_by``), or why it is left for a
person with the accounts offered (``LineItem.candidates``), and (``taxes``) each tax charged on it with its amount.
The no-chart row (GL account and cost center pickers) uses none of this.
"""

from __future__ import annotations

import re
from typing import Any

from ap_coder.coding.review import is_blank
from ap_coder.coding.taxmath import INSIDE, OWN_LINE, READ, InvoiceTaxes, LineTaxes
from ap_coder.core import chart
from ap_coder.core.memory import pair_lines
from ap_coder.core.reference_data import UNASSIGNED, ReferenceData

LINE_ACCOUNT = "line_account"  # a line's whole account string, one box a row, posted in order
FIX, CHECK = "fix", "check"  # a row's state: Approve is closed until it is fixed; to look at before approving
# The chart's findings on a line's account (``coding/validation.py``): the first of a line is said on its row.
CHART_CODES = ("ACCOUNT_NOT_IN_CHART", "ACCOUNT_NOT_POSTABLE", "BU_INACTIVE", "ACCOUNT_RESERVED")
NO_ACCOUNT = "No account yet: type its number or words, or pick one"
GUESS = "The account is AP Coder's guess, not learned yet: check it"
OFFERED_MOST = 3  # business units offered for an account its line's business unit does not have
_PARTS = re.compile(r"[\d.\s]+")  # an account's parts pasted with spaces or tabs between them
EMPTY: dict[str, Any] = {"text": "", "state": "", "name": "", "unit": "", "why": "", "note": "", "candidates": [],
                         "taxes": None}  # fmt: skip


def in_use(reference: ReferenceData | None) -> bool:
    """Whether a line is coded to one account string on the review screen: a chart of accounts is imported."""
    return reference is not None and reference.pairs is not None and reference.cost_centers is not None


def _code(value: Any) -> str:
    return "" if is_blank(value) or value == UNASSIGNED else str(value).strip()


def account_text(gl: Any, cc: Any) -> str:
    """The Account box's text for a line's GL code and business unit: the whole string, or the part it has."""
    gl, cc = _code(gl), _code(cc)
    return chart.join_account(cc, gl) if gl else cc


def posted(text: Any, pairs: chart.AccountPairs | None = None) -> tuple[str, str]:
    """The Account box as posted, as the line's (GL code, business unit); ("", "") when it is empty. What was typed
    is kept as typed: the parts pasted with spaces or tabs between them are joined with dots, and a text with no
    business unit the chart knows that is one of its GL codes is the code alone."""
    text = " ".join(str(text or "").split())
    if _PARTS.fullmatch(text):
        text = ".".join(part.strip(".") for part in text.split())
    unit, code = chart.split_account(text)
    if pairs is not None and unit and not pairs.has_business_unit(unit) and pairs.has_code(text):
        unit, code = "", chart.code_of(*chart.split_code(text))
    return code, unit


def fill(lines: list[dict[str, Any]], gl: str, cc: str) -> tuple[list[dict[str, Any]], int]:
    """Every line without an account takes the account (its two codes); a line that has one keeps it. The new rows
    and how many lines took it."""
    out, n = [], 0
    for row in lines:
        real = not all(is_blank(row.get(k)) for k in ("description", "amount"))
        if real and not _code(row.get("predicted_gl_code")):
            row, n = {**row, "predicted_gl_code": gl, "predicted_cost_center": cc}, n + 1
        out.append(row)
    return out, n


def said_once(items: list[dict[str, Any]]) -> None:
    """The findings' list (``invoice_view.checks_summary``'s items) as a screen with one Account box a line says
    it: a line with no account is said once ("no account yet"), not once for its GL code and once for its business
    unit: the second stays in the list, not shown (``folded``), so the page still carries its code."""
    uncoded = {n for item in items if item.get("code") == "GL_UNASSIGNED" for n in item.get("lines") or []}
    for item in items:
        if item.get("code") == "GL_UNASSIGNED":
            item["text"] = item["text"].replace("no GL account yet", "no account yet")
        elif item.get("code") == "CC_UNASSIGNED" and item.get("lines") and set(item["lines"]) <= uncoded:
            item["folded"] = True
        elif item.get("code") == "CC_UNASSIGNED":
            item["text"] = item["text"].replace("no cost center yet: pick one",
                                                "no business unit yet: type the whole account")  # fmt: skip


def why_words(decided_by: dict[str, Any] | None) -> str:
    """What decided a line's account (``LineItem.decided_by``), in plain words for under its box; "" when nothing
    is recorded."""
    by = decided_by or {}
    source, lines, of = by.get("source"), by.get("lines"), by.get("of")
    if source == "supplier":
        counted = f": {lines} of {of} {'line' if of == 1 else 'lines'} last year" if lines and of else ""
        return f"this supplier's usual account{counted}"
    if source == "vendor":
        return f"approved this way {lines} {'time' if lines == 1 else 'times'}" if lines else "approved this way before"
    if source == "rule":
        return f"the rule “{by['rule']}”" if by.get("rule") else "a coding rule"
    return {"others": "other suppliers' lines like it went here", "words": "the words match the account's name",
            "model": "AP Coder's guess: check it", "person": "picked by a person"}.get(str(source), "")  # fmt: skip


def _sentence(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}"


def _offered(pairs: chart.AccountPairs, gl: str) -> list[dict[str, str]]:
    """The business units that do have the account, as candidates for a line whose own business unit does not."""
    units = sorted(pairs.bus_of(gl), key=lambda unit: unit != pairs.settings.home_business_unit)[:OFFERED_MOST]
    return [{"account": chart.join_account(unit, gl), "name": pairs.description(unit, gl),
             "unit": pairs.name_of(unit), "reason": "the same account on this business unit"}
            for unit in units]  # fmt: skip


def _candidates(pairs: chart.AccountPairs, kept: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    return [{"account": str(c.get("account") or ""), "name": str(c.get("name") or ""),
             "unit": pairs.name_of(c.get("business_unit")), "reason": str(c.get("reason") or "")}
            for c in kept or [] if c.get("account")]  # fmt: skip


def _view(reference: ReferenceData, row: dict[str, Any], before: dict[str, Any], issue: Any) -> dict[str, Any]:
    """One line's row: ``before`` is the line as proposed (its candidates, what decided it), ``issue`` the chart's
    finding on it, if any."""
    pairs = reference.pairs
    assert pairs is not None
    gl, cc = _code(row.get("predicted_gl_code")), _code(row.get("predicted_cost_center"))
    view = {**EMPTY, "text": account_text(gl, cc), "candidates": []}
    said = _sentence(issue.message) if issue is not None else ""
    if not (gl and cc):
        proposed_blank = not _code(before.get("predicted_gl_code"))
        reason = str(before.get("reasoning_justification") or "") if proposed_blank else ""
        typed = f"“{view['text']}” is not a whole account: type business unit.object.subsidiary, or pick one"
        return {**view, "state": FIX, "note": typed if view["text"] else reason or NO_ACCOUNT,
                "candidates": _candidates(pairs, before.get("candidates"))}  # fmt: skip
    status = chart.OK if gl in reference.tax.tax_gl_codes() else pairs.status(cc, gl)
    view["name"], view["unit"] = pairs.description(cc, gl), pairs.name_of(cc)
    same = gl == _code(before.get("predicted_gl_code")) and cc == _code(before.get("predicted_cost_center"))
    view["why"] = why_words(before.get("decided_by") if same else {"source": "person"})
    if status in (chart.NOT_IN_CHART, chart.NOT_POSTABLE, chart.BU_INACTIVE):
        view.update(state=FIX, note=said or f"Account {view['text']} cannot be used: pick another", why="",
                    candidates=_offered(pairs, gl) if status != chart.NOT_POSTABLE else [])  # fmt: skip
    elif status == chart.RESERVED:
        view.update(state=CHECK, note=said or f"Account {view['text']} is not offered for invoices: check it")
    elif same and (before.get("decided_by") or {}).get("source") == "model":
        view.update(state=CHECK, note=GUESS)
    return view


def row_views(reference: ReferenceData, lines: list[dict[str, Any]], proposed: list[dict[str, Any]],
              issues: Any = (), taxes: InvoiceTaxes | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """What each line of ``lines`` (the draft's rows) shows in its row. ``proposed``: the lines as AP Coder proposed
    them; ``issues``: the checks' findings (the chart's are said on the line); ``taxes``: ``taxmath.invoice_taxes``
    of the coding on screen, None while it is not worked out (the row then shows no tax amounts)."""
    found: dict[Any, Any] = {}
    for issue in issues or ():
        if issue.code in CHART_CODES and issue.line_number is not None:
            found.setdefault(issue.line_number, issue)
    by_position = {line.position: line for line in taxes.lines} if taxes is not None else {}
    views, position = [], 0
    for before, row in pair_lines(proposed, lines):
        view = _view(reference, row, before or {}, found.get(row.get("line_number")))
        # the coding leaves out a row with no description and no amount (``coding_from_inputs``)
        if not all(is_blank(row.get(k)) for k in ("description", "amount")):
            view["taxes"] = tax_view(by_position[position]) if position in by_position else None
            position += 1
        views.append(view)
    return views


def money(value: float) -> str:
    return f"{value:,.2f}"


def tax_view(line: LineTaxes) -> dict[str, Any]:
    """One line's taxes as its Tax cell shows them: the tax code in words, each tax with its own amount ("read" when
    the invoice prints it on the line), whether the line's tax is not the invoice's usual one, and the amount keyed
    when a tax stays in the line."""
    rows = []
    for tax in line.taxes:
        where = {INSIDE: "in the line", OWN_LINE: "on its own account"}.get(tax.where, "")
        if tax.where != INSIDE and tax.inside:  # a meals line: half the tax recovered stays in it
            where = f"{money(tax.inside)} in the line"
        rows.append({"name": tax.name, "amount": money(tax.amount), "read": tax.source == READ, "where": where})
    for due in line.due:
        rows.append({"name": due.name, "amount": money(due.amount), "read": False, "where": "due, not charged"})
    return {"label": line.label, "unusual": line.unusual, "rows": rows, "notes": list(line.notes),
            "keyed": money(line.keyed) if abs(line.inside) >= 0.005 else ""}  # fmt: skip
