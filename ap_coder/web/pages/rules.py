"""Coding rules (D-217): lines that always go to the same account, the history of the rules' changes with Undo, and
how many lines each rule coded.

``GET /rules`` (``?rule=<number>``: the history of one rule), ``POST /rules`` (the table saved), ``POST /rules/add``
(one suggested rule), ``POST /rules/undo`` (an entry of the history). Anyone who reviews invoices may change the
rules (server mode: a Clerk and above, web/roles.py), so every change is kept with who made it and when
(``Store.coding_rule_history``) and any of them can be put back.

Several people change one list, and a save writes all of it: the page posts what it was drawn from (``version``: the
history's last entry, ``Store.rules_seen``) with a save and with an undo. When the rules changed since, nothing is
written: the person is sent back to the page (``?kept=``), told who changed what, with the rules as they are now and
their own changes still in the form (``form_again``, B-2292).

With a chart of accounts loaded a rule's account is one string, ``business unit.object.subsidiary``, in one box
(read as the review screen's Account box is, ``account_lines.posted``), and a rule added or changed is refused when
that account is not one of the chart's that may be posted to, in the words the line's check has for it
(``validation.account_problem``). A rule saved before, left as it is, is not checked again: the page says so on its
row.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from typing import Any

from fastapi import Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from ap_coder.coding.rules import Rule, RuleChange, change_words, rule_words
from ap_coder.coding.rules import suggest as suggest_rules
from ap_coder.coding.validation import account_problem
from ap_coder.core import ui
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.store import Store
from ap_coder.core.store.rules import CHANGED_SINCE, SAME, TWICE, UNDONE, USE_SINCE, RulesChanged
from ap_coder.web import account_lines
from ap_coder.web.core import (
    done,
    flash_message,
    get_store,
    one_connection,
    page_router,
    posted_form,
    reference_or_none,
    refuse,
    render,
    reviewer,
    with_flash,
)

router = page_router()  # each plain handler: one connection a request

BLANK_ROWS = 3  # empty rows under the rules, to add more
SUGGESTED = 8  # rules suggested from past coding, at most
NEEDS_GL = "Import your GL accounts first."
KEPT_FORM = "rules_form"  # a person's rows that were not saved, kept for the page they are sent back to
KEPT_FOR = 3600  # seconds
CHANGES_SAID = 6  # the changes a person had not seen that the message names, at most
NO_PAGE = ("Nothing was changed: this request does not say which rules it was made on. Open the rules page again "
           "and make your change there.")  # fmt: skip


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


def _use(rule: Rule, use: dict[int, tuple[int, int]], before: dict[int, tuple[int, int]], store: Store,
         whole: bool) -> dict[str, Any]:  # fmt: skip
    """What a rule's row says it coded: ``lines`` on ``invoices`` as it is now, and, when it coded lines while it
    said something else, ``before`` (lines, invoices) with when it was changed and what it was (from its history's
    last change; neither when the history does not have it)."""
    lines, invoices = use.get(rule.id, (0, 0))
    said = {"lines": lines, "invoices": invoices, "before": before.get(rule.id), "changed": "", "was": ""}
    if said["before"]:
        last = next((c for c in store.coding_rule_history(rule.id) if c.before and c.after), None)
        if last is not None:
            said.update(changed=_when(last.at), was=rule_words(last.before, whole))
    return said


def form_again(posted: list[Rule], unseen: list[RuleChange], current: list[Rule]) -> list[Rule]:
    """The rows of the form for a person whose save met rules changed since their page was drawn (B-2292): the rules
    as they are now with this person's own changes on them. A row they left as their page showed it follows the
    change they had not seen (theirs was none); a row they changed, cleared or added stays as they had it. ``unseen``:
    the history's entries since their page."""
    showed: dict[int, Rule | None] = {}  # each rule changed since, as their page showed it
    for change in sorted(unseen, key=lambda c: c.id):
        showed.setdefault(change.rule_id, change.before)
    mine = {r.id: r for r in posted if r.id}
    rows = []
    for rule in current:
        own = mine.get(rule.id)
        rows.append(rule if own is None or (rule.id in showed and own == showed[rule.id]) else own)
    saved = {r.id for r in current}
    for r in posted:
        said = any((r.vendor, r.contains, r.gl_code, r.cost_center))
        if said and r.id not in saved and (not r.id or r != showed.get(r.id)):
            rows.append(replace(r, id=0))  # a new row, or their change to a rule removed since: theirs to save again
    return rows


def _kept_form(store: Store, token: str) -> dict[str, Any] | None:
    """The form kept for a person sent back to the page (``_changed_since``): {seen, rows}; None when there is none
    (any more)."""
    kept = store.ephemeral(KEPT_FORM, token) if token else None
    try:
        form = json.loads(kept) if kept else None
        rows = [Rule(*(str(v) for v in row[1:5]), int(row[0])) for row in form["rows"]] if form else []
        return {"seen": int(form["seen"]), "rows": rows} if form else None
    except (ValueError, TypeError, KeyError, IndexError):
        return None


def rules_context(store: Store, reference: ReferenceData | None, rule_id: int = 0, kept: str = "") -> dict[str, Any]:
    form = _kept_form(store, kept)
    seen = form["seen"] if form else store.rules_seen()  # before the rules: a change in between is not seen
    current = store.coding_rules()
    shown = form["rows"] if form else current
    whole = account_lines.in_use(reference)
    gl = store.list_accounts("gl_accounts")
    gl_codes = [a["code"] for a in gl]
    names = {a["code"]: f"{a['code']} · {a['description']}" for a in gl}
    use, before = store.rule_use(), store.rule_use(before=True)
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
                **_use(r, use, before, store, whole),
                "problem": target_problem(r, reference) if r.complete else "",
                "name": pairs.description(r.cost_center, r.gl_code) if pairs and r.complete else "",
            }
            for r in shown
        ],
        "blank_rows": BLANK_ROWS,
        "rules_version": seen,
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
def rules_page(request: Request, rule: int = 0, kept: str = "") -> HTMLResponse:
    store = get_store(request)
    return render(request, "rules.html", "rules", **rules_context(store, reference_or_none(store), rule, kept))


def _form_rows(form: Any, reference: ReferenceData | None) -> list[tuple[Rule, str]]:
    """Each row of the form, in its order: (what it says, with the saved rule's number when it has one; what is typed
    for an account that names none, else ""). A row with an ``account`` (the one box of an install with a chart of
    accounts) is read as the review screen's is."""
    names = ("id", "vendor", "contains", "gl_code", "cost_center", "account")
    columns = [form.getlist(k) for k in names]
    pairs = reference.pairs if account_lines.in_use(reference) and reference else None
    rows = []
    for n in range(len(columns[1])):
        number, vendor, words, gl, cc, account = (str(x[n] if n < len(x) else "").strip() for x in columns)
        unread = ""
        if account:
            gl, cc = account_lines.posted(account, pairs)
            unread = "" if gl else account  # (no business unit: ``target_problem`` says so of a rule added or changed)
        rows.append((Rule(vendor, words, gl, cc, int(number) if number.isdigit() else 0), unread))
    return rows


def rules_from_form(form: Any, reference: ReferenceData | None = None) -> tuple[list[Rule], list[int], list[str]]:
    """(the rules in the form, the numbers of incomplete rows, what is typed for an account that names none).
    Wholly empty rows are not rules at all. A row with a saved rule's number (``id``) is that rule."""
    rules, incomplete, unread = [], [], []
    for n, (rule, bad) in enumerate(_form_rows(form, reference), start=1):
        if bad:
            unread.append(bad)
        if not any((rule.vendor, rule.contains, rule.gl_code, rule.cost_center)):
            continue
        if rule.complete:
            rules.append(rule)
        else:
            incomplete.append(n)
    return rules, incomplete, unread


def _seen(posted: Any) -> int | None:
    """The rules a request was made on, as the page posted it (``version``); None when it does not say."""
    text = str(posted or "").strip()
    return int(text) if text.isdigit() and len(text) <= 18 else None


def _unseen_words(unseen: list[RuleChange], whole: bool) -> list[str]:
    """Who changed what since a person's page was drawn, oldest first, as lines for the message."""
    said = [f"{change_words(c, whole)}, {_when(c.at)}" for c in unseen[:CHANGES_SAID]]
    more = len(unseen) - CHANGES_SAID
    return said + ([f"and {ui.plural(more, 'more change')}"] if more > 0 else [])


def _changed_since(store: Store, reference: ReferenceData | None, unseen: list[RuleChange], form: Any) -> JSONResponse:
    """The answer to a save made on rules that changed since (nothing was written): back to the page, which says who
    changed what and has this person's own changes in its form (``form_again``), on the rules they have now seen."""
    rows = form_again([rule for rule, _ in _form_rows(form, reference)], unseen, store.coding_rules())
    kept = {"seen": max(c.id for c in unseen),
            "rows": [[r.id, r.vendor, r.contains, r.gl_code, r.cost_center] for r in rows]}  # fmt: skip
    token = store.keep_ephemeral(KEPT_FORM, json.dumps(kept).encode("utf-8"), KEPT_FOR)
    text = "Nothing was saved: the rules were changed since you opened this page."
    words = _unseen_words(unseen, account_lines.in_use(reference))
    said = flash_message(f"{text} Your own changes are still in the form, on the rules as they are now: check them, "
                         "then save again.", "warn", details=words)  # fmt: skip
    return refuse(f"{text} Open the rules page again.", 409, redirect=with_flash(f"/rules?kept={token}", [said]))


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
    seen = _seen(form.get("version"))  # a rule added or changed meanwhile would be lost (B-655, B-2292)
    if seen is None:
        return refuse(NO_PAGE, 409)
    reference = reference_or_none(store)
    rules, incomplete, unread = rules_from_form(form, reference)
    if unread:
        return refuse(f"Nothing was saved: “{unread[0]}” is not an account. Type the whole account, business "
                      "unit.object.subsidiary, or clear the whole row to remove the rule.")  # fmt: skip
    problems = _refused(rules, store, reference)
    if problems:
        return refuse("Nothing was saved. " + " ".join(problems))
    try:
        saved = store.save_coding_rules(rules, actor=reviewer(), seen=seen)
    except RulesChanged as newer:
        return _changed_since(store, reference, newer.changes, form)
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
    try:  # the rules as they are and this one: not written over a change made at the same moment (B-2292)
        seen = store.rules_seen()
        store.save_coding_rules([*store.coding_rules(), rule], actor=reviewer(), seen=seen)
    except RulesChanged:
        return refuse("Not added: the rules were changed at the same moment. Add it again.", 409)
    target = rule.target if account_lines.in_use(reference) else rule.gl_code
    return done(request, "/rules", flash_message(f"Rule added: {rule.vendor} → {target}.", "ok", icon="rule_settings"))


@router.post("/rules/undo")
def undo_rule(request: Request, change: int = Form(0), overwrite: str = Form(""),
              version: str = Form("")) -> JSONResponse:  # fmt: skip
    """An entry of the history undone: its rule put back as it was before it. When the rule changed again since,
    only with ``overwrite`` (the page asks first, saying what the newer change is). As a save, an undo is made on
    the rules its page showed (``version``): changed since, it is not done and the page says who changed what."""
    store = get_store(request)
    seen = _seen(version)
    if seen is None:
        return refuse(NO_PAGE, 409)
    entry = store.coding_rule_change(change)
    if entry is None:
        return refuse("That entry is not in the history: open the rules page again.", 404)
    reference = reference_or_none(store)
    if entry.before is not None and (problem := target_problem(entry.before, reference)):
        return refuse(f"Not undone: the rule as it was can't be saved any more. {problem}")
    whole = account_lines.in_use(reference)
    try:
        did, undo = store.undo_rule_change(change, actor=reviewer(), overwrite=overwrite == "1", seen=seen)
    except RulesChanged as newer:
        text = "Not undone: the rules were changed since you opened this page."
        said = flash_message(f"{text} The history shows it: undo again here if you still want to.", "warn",
                             details=_unseen_words(newer.changes, whole))  # fmt: skip
        return refuse(f"{text} Open the rules page again.", 409, redirect=with_flash("/rules#history", [said]))
    if did == TWICE:
        return refuse(f"Not undone: the rule would then say {rule_words(entry.before, whole)}, which another rule "
                      "says already. Change or remove that rule first, then undo.", 409)  # fmt: skip
    if did == CHANGED_SINCE:
        return refuse("Not undone: this rule changed again since that entry. Open the rules page again: Undo says "
                      "there what the newer change is, and asks before it overwrites it.", 409)  # fmt: skip
    if did == SAME:
        return done(request, "/rules#history", flash_message(
            "Nothing to undo: the rule is already as it was before that entry.", "info"))  # fmt: skip
    if did != UNDONE or undo is None:
        return refuse("That entry is not in the history: open the rules page again.", 404)
    words = change_words(undo, whole).removesuffix(" (an undo)")
    return done(request, "/rules#history", flash_message(f"Undone. {words}.", "ok", icon="undo"))
