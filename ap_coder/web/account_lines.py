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

from ap_coder.ai.supplier_prior import prior_for
from ap_coder.coding.remark import PERSON, REMARK_MOST, one_line
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
NO_REMARK: dict[str, Any] = {"text": "", "typed": False, "why": "", "note": "", "count": 0, "over": False,
                             "printed": ""}  # fmt: skip
EMPTY: dict[str, Any] = {"text": "", "state": "", "name": "", "unit": "", "why": "", "note": "", "candidates": [],
                         "listed": [], "taxes": None, "remark": NO_REMARK}  # fmt: skip
# Where a suggested remark comes from (``LineItem.remark_from``), said on its box.
REMARK_WHY = {"supplier": "Suggested: this supplier's usual remark for this account",
              "invoice": "Suggested from the line as the invoice prints it",
              "account": "Suggested from the account's name"}  # fmt: skip


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


_PICK = re.compile(r"^Pick (?:a GL account for (?P<gl>.+?))?(?: and )?(?:a cost center for (?P<cc>.+?))? to approve"
                   r"(?: \(or use Apply to all lines…\))?(?P<rest>.*)$")  # fmt: skip


def pick_words(text: str) -> str:
    """What approval still needs on the lines (``review.codes_to_pick``'s sentence, which names a GL account and a
    cost center), as a screen with one Account box a line says it: "an account for line 4", "an account for 4
    lines"; a line that has its GL code and no business unit: "the business unit of line 2's account". What the
    sentence says after (a tax exemption to confirm) is kept; any other sentence comes back as it is."""
    found = _PICK.match(text or "")
    if not found or not (found["gl"] or found["cc"]):
        return text
    parts = [
        f"an account for {found['gl']}" if found["gl"] else "",
        f"the business unit of the account of {found['cc']}" if found["cc"] not in (None, found["gl"]) else "",
    ]
    return f"{' and '.join(filter(None, parts))}{found['rest']}"


SUPPLIER_MOST = 8  # of a supplier's accounts last year, offered first in an Account box's list


def supplier_accounts(store: Any, reference: ReferenceData, vendor_name: str) -> list[list[Any]]:
    """The accounts this supplier's lines went to last year and since, the most used first, each [the account
    string, its lines, its name, its business unit's name]: only those the chart still offers, ``SUPPLIER_MOST`` at
    most. From the supplier's prior (``supplier_prior.prior_for``, kept in this process): asked once a page or a
    check, never per key typed."""
    if not in_use(reference) or not (vendor_name or "").strip():
        return []
    pairs = reference.pairs
    assert pairs is not None
    used = prior_for(store, vendor_name, always=True).accounts
    return [
        [use.account, use.lines, pairs.description(use.business_unit, use.code), pairs.name_of(use.business_unit)]
        for use in used
        if use.business_unit and pairs.offered(use.business_unit, use.code)
    ][:SUPPLIER_MOST]


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
    view["candidates"] = _candidates(pairs, before.get("candidates"))  # still in the box's list
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


def remark_view(row: dict[str, Any], coded: dict[str, Any] | None, issue: Any = None) -> dict[str, Any]:
    """A line's Remark box (D-213): the remark of the coding on screen (``coded``: the suggestion follows the line's
    account), else the draft row's; ``typed``: a person's, which no suggestion replaces; ``note``: the finding's
    words when it is longer than JD Edwards keeps (``issue``, REMARK_LONG); ``printed``: the line as the invoice
    prints it, for under the box."""
    line = coded if coded is not None else row
    text = one_line(line.get("remark"))
    source = str(line.get("remark_from") or "")
    return {"text": text, "typed": source == PERSON, "why": REMARK_WHY.get(source, ""),
            "note": _sentence(issue.message) if issue is not None else "", "count": len(text),
            "over": len(text) > REMARK_MOST, "printed": one_line(row.get("description"))}  # fmt: skip


def row_views(reference: ReferenceData, lines: list[dict[str, Any]], proposed: list[dict[str, Any]],
              issues: Any = (), taxes: InvoiceTaxes | None = None,
              coded: list[dict[str, Any]] | None = None, meals_note: str = "") -> list[dict[str, Any]]:  # fmt: skip
    """What each line of ``lines`` (the draft's rows) shows in its row. ``proposed``: the lines as AP Coder proposed
    them; ``issues``: the checks' findings (the chart's and the remark's are said on the line); ``taxes``:
    ``taxmath.invoice_taxes`` of the coding on screen, None while it is not worked out (the row then shows no tax
    amounts); ``coded``: that coding's lines as checked (each with its remark), None likewise; ``meals_note``: what a
    line coded to a meals account says under its tax (``tax.meals_note``)."""
    found: dict[Any, Any] = {}
    long: dict[Any, Any] = {}
    for issue in issues or ():
        if issue.code in CHART_CODES and issue.line_number is not None:
            found.setdefault(issue.line_number, issue)
        elif issue.code == "REMARK_LONG" and issue.line_number is not None:
            long.setdefault(issue.line_number, issue)
    by_position = {line.position: line for line in taxes.lines} if taxes is not None else {}
    views, position = [], 0
    for before, row in pair_lines(proposed, lines):
        view = _view(reference, row, before or {}, found.get(row.get("line_number")))
        # the coding leaves out a row with no description and no amount (``coding_from_inputs``)
        mine = None
        if not all(is_blank(row.get(k)) for k in ("description", "amount")):
            view["taxes"] = tax_view(by_position[position], meals_note) if position in by_position else None
            mine = coded[position] if coded is not None and position < len(coded) else None
            position += 1
        view["remark"] = remark_view(row, mine, long.get(row.get("line_number")))
        # what the Account box's list offers first: [account, its name, its business unit's name, why]
        view["listed"] = [[c["account"], c["name"], c["unit"], c["reason"]] for c in view["candidates"]]
        views.append(view)
    return views


TWO_AREAS = ("This invoice charges two federal taxes or rates: its lines are in two tax areas, and one voucher's pay "
             "item takes one. Check which lines carry each tax")  # fmt: skip


def tax_said(taxes: InvoiceTaxes, issues: Any = ()) -> dict[str, Any]:
    """The tax check for under the lines, one sentence a tax (``TaxCheckLine.text``, with where the tax goes), and,
    when the invoice's lines are in two tax areas, why the voucher is held (the JD Edwards check's own words)."""
    hold = ""
    if taxes.two_tax_areas:
        hold = next((_sentence(i.message) for i in issues or () if i.code == "JDE_TAX_AREA"), TWO_AREAS)
    return {"lines": [{"ok": c.ok, "text": c.text, "goes": c.goes} for c in taxes.check], "hold": hold,
            "notes": list(taxes.notes)}  # fmt: skip


def money(value: float) -> str:
    return f"{value:,.2f}"


def tax_view(line: LineTaxes, meals_note: str = "") -> dict[str, Any]:
    """One line's taxes as its Tax cell shows them: the tax code in words, each tax with its own amount ("read" when
    the invoice prints it on the line), whether the line's tax is not the invoice's usual one, and the amount keyed
    when a tax stays in the line. A line coded to a meals account says so (``meals_note``, D-90)."""
    rows = []
    for tax in line.taxes:
        where = {INSIDE: "in the line", OWN_LINE: "on its own account"}.get(tax.where, "")
        if tax.where != INSIDE and tax.inside:  # a meals line: half the tax recovered stays in it
            where = f"{money(tax.inside)} in the line"
        rows.append({"name": tax.name, "amount": money(tax.amount), "read": tax.source == READ, "where": where})
    for due in line.due:
        rows.append({"name": due.name, "amount": money(due.amount), "read": False, "where": "due, not charged"})
    notes = [*line.notes, *([meals_note] if line.meals and meals_note else [])]
    return {"label": line.label, "unusual": line.unusual, "rows": rows, "notes": notes,
            "keyed": money(line.keyed) if abs(line.inside) >= 0.005 else ""}  # fmt: skip
