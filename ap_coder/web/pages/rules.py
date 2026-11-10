"""Coding rules (D-217): lines that always go to the same account, the history of the rules' changes with Undo, and
how many lines each rule coded.

``GET /rules`` (``?rule=<number>``: the history of one rule), ``POST /rules`` (the table saved), ``POST /rules/add``
(one suggested rule), ``POST /rules/undo`` (an entry of the history). Anyone who reviews invoices may change the
rules (server mode: a Clerk and above, web/roles.py), so every change is kept with who made it and when
(``Store.coding_rule_history``) and any of them can be put back.

With a chart of accounts loaded a rule's account is one string, ``business unit.object.subsidiary``, in one box
(read as the review screen's Account box is, ``account_lines.posted``), and a rule added or changed is refused when
that account is not one of the chart's that may be posted to, in the words the line's check has for it
(``validation.account_problem``). A rule saved before, left as it is, is not checked again: the page says so on its
row.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from ap_coder.coding.rules import Rule, RuleChange, change_words
from ap_coder.coding.rules import suggest as suggest_rules
from ap_coder.coding.validation import account_problem
from ap_coder.core import ui
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.store import Store
from ap_coder.core.store.rules import CHANGED_SINCE, SAME, UNDONE, USE_SINCE
from ap_coder.web import account_lines
from ap_coder.web.core import (
    done,
    flash_message,
    get_store,
    one_connection,
    page_router,
    page_version,
    posted_form,
    reference_or_none,
    refuse,
    render,
    reviewer,
    stale_page,
)

router = page_router()  # each plain handler: one connection a request

BLANK_ROWS = 3  # empty rows under the rules, to add more
SUGGESTED = 8  # rules suggested from past coding, at most
NEEDS_GL = "Import your GL accounts first."


def rules_shown(store: Store) -> list[tuple[Any, ...]]:
    """The coding rules as the page shows them (its ``page_version`` is posted with the form)."""
    return [(r.id, r.vendor, r.contains, r.gl_code, r.cost_center) for r in store.coding_rules()]


def target_problem(rule: Rule, reference: ReferenceData | None) -> str:
    """Why a rule can't code to its account with a chart of accounts loaded, as a sentence; "" when it can, and
    without a chart (the GL account is picked from the list then)."""
    if not account_lines.in_use(reference):
        return ""
    if not rule.cost_center.strip():
        return (f"The rule ‘{rule.label}’ needs the whole account, business unit.object.subsidiary: "
                f"{rule.gl_code} names no business unit.")  # fmt: skip
    problem = account_problem(reference, rule.gl_code, rule.cost_center)
    return f"The rule ‘{rule.label}’: {problem}." if problem else ""


def _when(at: str) -> str:
    try:
        return ui.day_and_time(dt.datetime.fromisoformat(at))
    except ValueError:
        return at


def _entry(change: RuleChange, history: list[RuleChange], current: dict[int, Rule], whole: bool) -> dict[str, Any]:
    """An entry of the history as the page draws it. ``undo``: "yes" (the rule is as the entry left it), "ask" (it
    changed again since: ``newer`` says how, and Undo asks before it overwrites that) or "" (the rule is as it was
    before the entry already: nothing to undo)."""
    now = current.get(change.rule_id)
    undo = "" if now == change.before else "yes" if now == change.after else "ask"
    newer = next((c for c in history if c.rule_id == change.rule_id and c.id > change.id), None)
    return {
        "id": change.id,
        "rule_id": change.rule_id,
        "when": _when(change.at),
        "words": change_words(change, whole),
        "undo": undo,
        "newer": f"{change_words(newer, whole)}, {_when(newer.at)}" if undo == "ask" and newer else "",
    }


def rules_context(store: Store, reference: ReferenceData | None, rule_id: int = 0) -> dict[str, Any]:
    current = store.coding_rules()
    whole = account_lines.in_use(reference)
    gl = store.list_accounts("gl_accounts")
    gl_codes = [a["code"] for a in gl]
    names = {a["code"]: f"{a['code']} · {a['description']}" for a in gl}
    use = store.rule_use()
    history = store.coding_rule_history(rule_id)
    by_id = {r.id: r for r in current}
    suggested = [(r, n) for r, n in suggest_rules(store.feedback_rows(), current)
                 if (not target_problem(r, reference) if whole else r.gl_code in gl_codes)][:SUGGESTED]  # fmt: skip
    pairs = reference.pairs if whole and reference else None
    return {
        "has_gl": bool(gl),
        "whole": whole,
        "rows": [
            {
                "rule": r,
                "lines": use.get(r.id, (0, 0))[0],
                "invoices": use.get(r.id, (0, 0))[1],
                "problem": target_problem(r, reference),
                "name": pairs.description(r.cost_center, r.gl_code) if pairs else "",
            }
            for r in current
        ],
        "blank_rows": BLANK_ROWS,
        "rules_version": page_version(rules_shown(store)),
        "gl_names": names,
        "cc_codes": [a["code"] for a in store.list_accounts("cost_centers")],
        "unknown": [] if whole else sorted({r.gl_code for r in current if r.gl_code and r.gl_code not in gl_codes}),
        "counted_since": _when(store.get_setting(USE_SINCE)).split(" at ")[0],
        "suggestions": [
            {
                "rule": r,
                "lines": n,
                "target": r.target
                if whole
                else names.get(r.gl_code, r.gl_code) + (f" · {r.cost_center}" if r.cost_center else ""),
            }
            for r, n in suggested
        ],
        "history": [_entry(c, history, by_id, whole) for c in history],
        "history_of": by_id.get(rule_id) or next((c.before or c.after for c in history), None) if rule_id else None,
        "history_rule": rule_id,
    }


@router.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, rule: int = 0) -> HTMLResponse:
    store = get_store(request)
    return render(request, "rules.html", "rules", **rules_context(store, reference_or_none(store), rule))


def rules_from_form(form: Any, reference: ReferenceData | None = None) -> tuple[list[Rule], list[int], list[str]]:
    """(the rules in the form, the numbers of incomplete rows, what is typed for an account that names none).
    Wholly empty rows are not rules at all. A row with a saved rule's number (``id``) is that rule; one with
    an ``account`` (the one box of an install with a chart of accounts) is read as the review screen's is."""
    names = ("id", "vendor", "contains", "gl_code", "cost_center", "account")
    columns = [form.getlist(k) for k in names]
    pairs = reference.pairs if account_lines.in_use(reference) and reference else None
    rules, incomplete, unread = [], [], []
    for n in range(len(columns[1])):
        number, vendor, words, gl, cc, account = (str(x[n] if n < len(x) else "").strip() for x in columns)
        if account:
            gl, cc = account_lines.posted(account, pairs)
            if not gl:  # (one with no business unit: ``target_problem`` says so of a rule added or changed)
                unread.append(account)
        if not any((vendor, words, gl, cc)):
            continue
        rule = Rule(vendor, words, gl, cc, int(number) if number.isdigit() else 0)
        if rule.complete:
            rules.append(rule)
        else:
            incomplete.append(n + 1)
    return rules, incomplete, unread


def _refused(rules: list[Rule], store: Store, reference: ReferenceData | None) -> list[str]:
    """Why each rule added or changed can't be saved (``target_problem``); a saved rule left as it is is not asked."""
    saved = {(r.vendor, r.contains, r.gl_code, r.cost_center) for r in store.coding_rules()}
    return [problem for r in rules if (r.vendor, r.contains, r.gl_code, r.cost_center) not in saved
            and (problem := target_problem(r, reference))]  # fmt: skip


@router.post("/rules")
async def save_rules(request: Request) -> JSONResponse:
    """The rules saved: always this account for a vendor and/or words in a line."""
    form = await posted_form(request)
    return await run_in_threadpool(_save_rules, request, form)


@one_connection
def _save_rules(request: Request, form: Any) -> JSONResponse:
    store = get_store(request)
    if not store.list_accounts("gl_accounts"):
        return refuse(NEEDS_GL)
    stale = stale_page(form, rules_shown(store))  # a rule added or changed meanwhile would be lost (B-655)
    if stale is not None:
        return stale
    reference = reference_or_none(store)
    rules, incomplete, unread = rules_from_form(form, reference)
    if unread:
        return refuse(f"Nothing was saved: “{unread[0]}” is not an account. Type the whole account, business "
                      "unit.object.subsidiary, or clear the whole row to remove the rule.")  # fmt: skip
    problems = _refused(rules, store, reference)
    if problems:
        return refuse("Nothing was saved. " + " ".join(problems))
    saved = store.save_coding_rules(rules, actor=reviewer())
    left_out = f"; {ui.plural(len(incomplete), 'incomplete row')} left out (no account, or no vendor or words)"
    return done(
        request,
        "/rules",
        flash_message(
            f"{ui.plural(saved, 'coding rule')} saved{left_out if incomplete else ''}.", "ok", icon="rule_settings"
        ),
    )


@router.post("/rules/add")
def add_rule(
    request: Request,
    vendor: str = Form(""),
    contains: str = Form(""),
    gl_code: str = Form(""),
    cost_center: str = Form(""),
) -> JSONResponse:
    """One suggested rule added to the rules."""
    store = get_store(request)
    rule = Rule(vendor.strip(), contains.strip(), gl_code.strip(), cost_center.strip())
    if not rule.complete:
        return refuse("A rule needs a GL account and a vendor or words.")
    reference = reference_or_none(store)
    problems = _refused([rule], store, reference)
    if problems:
        return refuse(problems[0])
    store.save_coding_rules([*store.coding_rules(), rule], actor=reviewer())
    target = rule.target if account_lines.in_use(reference) else rule.gl_code
    return done(request, "/rules", flash_message(f"Rule added: {rule.vendor} → {target}.", "ok", icon="rule_settings"))


@router.post("/rules/undo")
def undo_rule(request: Request, change: int = Form(0), overwrite: str = Form("")) -> JSONResponse:
    """An entry of the history undone: its rule put back as it was before it. When the rule changed again since,
    only with ``overwrite`` (the page asks first, saying what the newer change is)."""
    store = get_store(request)
    entry = store.coding_rule_change(change)
    if entry is None:
        return refuse("That entry is not in the history: open the rules page again.", 404)
    reference = reference_or_none(store)
    if entry.before is not None and (problem := target_problem(entry.before, reference)):
        return refuse(f"Not undone: the rule as it was can't be saved any more. {problem}")
    did, undo = store.undo_rule_change(change, actor=reviewer(), overwrite=overwrite == "1")
    if did == CHANGED_SINCE:
        return refuse("Not undone: this rule changed again since that entry. Open the rules page again: Undo says "
                      "there what the newer change is, and asks before it overwrites it.", 409)  # fmt: skip
    if did == SAME:
        return done(request, "/rules#history", flash_message(
            "Nothing to undo: the rule is already as it was before that entry.", "info"))  # fmt: skip
    if did != UNDONE or undo is None:
        return refuse("That entry is not in the history: open the rules page again.", 404)
    words = change_words(undo, account_lines.in_use(reference)).removesuffix(" (an undo)")
    return done(request, "/rules#history", flash_message(f"Undone. {words}.", "ok", icon="undo"))
