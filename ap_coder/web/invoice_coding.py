"""The review screen on the lines' coding: why these codes, what else is suggested, the PO, and what approving teaches.

* Why these codes (``reasons``), one-click accounts for the lines without one (``suggestions``), and the purchase
  order the invoice quotes, line against line (``po_section``).
* What the reviewer changed of what was proposed (``ai_changes``, ``differs_from_stored``; in the approved invoice's
  words: ``coding_changed``, ``remarks_changed``) and what approving teaches, line by line (``learn_text``).

The line grid and its actions are ``invoice_grid.py``'s; what is learned is the store's and the pipeline's own.
"""

from __future__ import annotations

from typing import Any

from ap_coder.ai.offline_coder import supplier_evidence
from ap_coder.ai.suggest import by_pairs, suggest_gl
from ap_coder.coding.po import LineLeft, match_invoice, po_label
from ap_coder.coding.remark import PERSON, one_line
from ap_coder.core.chart import join_account
from ap_coder.core.memory import pair_lines
from ap_coder.core.reference_data import UNASSIGNED, ReferenceData
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, line_guessed, nothing_proposed, suggestion_kept
from ap_coder.core.ui import plural
from ap_coder.web.invoice_form import signed
from ap_coder.web.invoice_grid import gl_name

# --- Why these codes, and what else -------------------------------------------------------------------------------


def reasons(coding: InvoiceCoding, ai: dict[str, Any], report: Any, reference: ReferenceData) -> list[dict[str, Any]]:
    """Why these codes: per line, the AI's reasoning, and whether the reviewer changed it or it matches history."""
    history = {h["line_number"]: h for h in report.checks.get("history") or []}
    rows = []
    for original, li in pair_lines(ai.get("line_items", []), list(coding.line_items)):
        before = original or {}
        badges = []
        if not before:
            badges.append(("Added by you", "info"))
        elif before.get("predicted_gl_code") != li.predicted_gl_code:
            badges.append((f"Changed from {before.get('predicted_gl_code')}", "info"))
        h = history.get(li.line_number)
        if h and h["status"] == "match":
            badges.append((f"Matches {h['decisions']} past decisions", "ok"))
        elif h:
            badges.append((f"Reviewers used {h['history_gl']} before", "warn"))
        rows.append({"number": li.line_number, "description": li.description, "gl": li.predicted_gl_code,
                     "gl_name": gl_name(reference, li.predicted_gl_code),
                     "why": before.get("reasoning_justification") or "Line added by the reviewer.",
                     "badges": badges})  # fmt: skip
    return rows


def suggestions(store: Store, reference: ReferenceData, coding: InvoiceCoding) -> list[dict[str, Any]]:
    """One-click GL accounts for lines without a usable one, from past decisions and the account list. With a chart
    of accounts imported or a journal loaded each is a whole account string that may be posted to (``account``; the
    GL code in ``gl``, its business unit in ``cc``), the supplier's coding last year among them (D-212)."""
    accounts = reference.chart_of_accounts
    lines = [(pos, li) for pos, li in enumerate(coding.line_items)
             if li.predicted_gl_code == UNASSIGNED or accounts.get(li.predicted_gl_code) is None]  # fmt: skip
    if not lines:
        return []
    key = store.vendor_key_for(coding.vendor_name, coding.gst_hst_registration_number)  # its own of two (B-1021)
    feedback = store.learning_rows(coding.vendor_name, key)
    vendor = store.get_vendor(key)
    default_gl = (vendor or {}).get("default_gl") or ""
    prior, units = supplier_evidence(store, vendor or coding.vendor_name, key, reference)
    whole = by_pairs(reference, prior)
    out = []
    for pos, li in lines:
        found = suggest_gl(li.description, coding.vendor_name, feedback, reference, default_gl=default_gl, key=key,
                           prior=prior, units=units)  # fmt: skip
        if found:
            out.append({
                "position": pos, "number": li.line_number, "description": li.description,
                "options": [{"gl": s.gl_code, "label": f"{_shown(s, whole)} · {_name(reference, s) or s.gl_code}",
                             "cc": s.cost_center or "", "why": "; ".join(s.reasons).capitalize(),
                             "first": s.reasons[0] if s.reasons else "",
                             **({"account": join_account(s.cost_center, s.gl_code)} if whole else {})}
                            for s in found],
            })  # fmt: skip
    return out


def _shown(suggestion: Any, whole: bool) -> str:
    """A suggestion's account as its button says it: the whole account string, or the GL code as before."""
    return join_account(suggestion.cost_center, suggestion.gl_code) if whole else suggestion.gl_code


def _name(reference: ReferenceData, suggestion: Any) -> str:
    """A suggested account's name: its own on that business unit when a chart of accounts was imported."""
    if reference.pairs is not None and suggestion.cost_center:
        return reference.pairs.description(suggestion.cost_center, suggestion.gl_code) or gl_name(
            reference, suggestion.gl_code)  # fmt: skip
    return gl_name(reference, suggestion.gl_code)


# --- The purchase order -------------------------------------------------------------------------------------------


def _fmt_qty(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def po_section(store: Store, coding: InvoiceCoding, invoice_id: int) -> dict[str, Any] | None:
    """How the invoice lines compare with the purchase order it quotes (2- or 3-way match); None without one."""
    if not coding.po_number.strip() or not store.has_purchase_orders():
        return None
    match = match_invoice(coding, store, invoice_id)
    if match is None:
        return None
    label = po_label(match.po_number)
    if not match.found:
        return {"found": False, "label": label}
    po = match.po or {}
    problems = sum(1 for m in match.lines if set(m.problems) - {"coding"})
    received = any(m.received is not None for m in match.lines)
    flags = {"price": "Price", "quantity": "Over ordered", "received": "Not received", "coding": "Coding"}
    rows = []
    for m in match.lines:
        if m.po_line is None:
            rows.append({"line": m.invoice_line, "description": m.description, "po_line": "", "coding": "", "qty": "",
                         "price": "", "price_po": "", "left": "", "over": False, "left_how": "",
                         "pills": [("Not on PO", "warn")]})  # fmt: skip
            continue
        if m.amount_only:  # a PO line with only an amount: compare amounts
            qty = f"{signed(m.billed_before_amount + m.amount)} / {signed(m.po_amount)}"
        else:
            qty = f"{_fmt_qty(m.billed_before + m.quantity)} / {_fmt_qty(m.po_quantity)}"
            if received:
                qty += f" / {_fmt_qty(m.received)}"
        pills = [(flags[p], "info" if p == "coding" else "warn") for p in m.problems if p in flags] or [("OK", "ok")]
        left = match.left[m.po_line]
        rows.append({"line": m.invoice_line, "description": m.description,
                     "po_line": f"{m.po_line} · {m.po_description}",
                     "coding": " · ".join(x for x in (f"GL {m.po_gl}" if m.po_gl else "", m.po_cc) if x),
                     "qty": qty, "price": signed(m.unit_price),
                     "price_po": signed(m.po_unit_price) if "price" in m.problems else "",
                     "left": _left_text(left.left), "over": left.left < 0, "left_how": _left_how(left),
                     "pills": pills})  # fmt: skip
    notes = [f"PO total {signed(po.get('total'))}"]
    if match.other_invoices:
        notes.append(f"{signed(match.billed_before)} already invoiced on "
                     + ", ".join(f"#{o['id']}" for o in match.other_invoices[:5]))  # fmt: skip
    notes.append(f"{'closed' if po.get('status') == 'closed' else 'open'}, for "
                 f"{(po.get('vendor_name') or '?').rstrip('.')}")  # fmt: skip
    opened = any(line.open_amount is not None for line in match.left.values())
    how = ("Left to expense: each PO line's amount less what this and the other invoices bill on it"
           + ("; where the PO import gave an open amount (as the ERP had it at the last PO import), that amount "
              "less this invoice and the ones not exported yet" if opened else "") + ".")  # fmt: skip
    return {
        "found": True, "label": label, "rows": rows, "received": received,
        "status": (f"{plural(problems, 'line')} to check", "warn") if problems else ("Matches the PO", "ok"),
        "notes": " · ".join(notes) + ". Billed quantities include earlier invoices on this PO. " + how,
        "differs": len(match.coding_differs),
        "total_left": _left_text(match.total_left), "total_over": match.total_left < 0,
    }  # fmt: skip


def _left_text(left: float) -> str:
    """What is left to expense as the PO match shows it: below zero, "over by" that much (amber, never an error)."""
    return f"over by {signed(-left)}" if left < 0 else signed(left)


def _left_how(line: LineLeft) -> str:
    """How a PO line's left to expense was worked out (its cell's tooltip)."""
    if line.open_amount is None:
        return (
            f"{signed(line.amount)} on the PO line, less {signed(line.others)} on other invoices and "
            f"{signed(line.this_invoice)} on this one"
        )
    return (
        f"{signed(line.open_amount)} open in the ERP when the PO was imported, less {signed(line.others)} on "
        f"invoices not exported yet and {signed(line.this_invoice)} on this one"
    )


# --- What the reviewer changed, and what approving teaches --------------------------------------------------------

EDIT_LABELS = {
    "line_coding": "GL coding", "line_coded": "lines the AI left blank coded by AP",
    "line_count": "lines added or removed", "tax_lines": "sales tax",
    "vendor_name": "vendor", "invoice_number": "invoice #", "invoice_date": "date", "po_number": "PO #",
    "payment_terms": "terms", "due_date": "due date", "currency": "currency",
    "supplier_province": "supplier province", "ship_to_province": "place of supply",
    "gst_hst_registration_number": "GST/HST #", "qst_registration_number": "QST #", "pst_registration_number": "PST #",
    "original_invoice_number": "invoice credited", "remit_bank_account": "bank account", "subtotal": "subtotal",
    "tax_total": "tax total", "grand_total": "total",
}  # fmt: skip


def coding_changed(ai: dict[str, Any], final: dict[str, Any]) -> str:
    """What an approval changed of the lines' coding proposed, as the approved invoice says it ("changed: …"): the
    GL accounts, the cost centers, or both, where it said "GL coding" of a cost center alone (B-2104). "GL coding"
    still when neither differs from what was proposed (a line changed after a reopen)."""
    gl = cc = False
    for before, li in pair_lines(ai.get("line_items") or [], final.get("line_items") or []):
        s_gl, s_cc = ((before.get("predicted_gl_code"), before.get("predicted_cost_center") or "") if before
                      else (None, ""))  # fmt: skip
        gl = gl or (s_gl not in (None, "", UNASSIGNED) and li.get("predicted_gl_code") != s_gl)
        cc = cc or (bool(s_cc) and (li.get("predicted_cost_center") or "") != s_cc)
    return {(True, True): "GL accounts and cost centers", (True, False): "GL accounts",
            (False, True): "cost centers"}.get((gl, cc), EDIT_LABELS["line_coding"])  # fmt: skip


def remarks_changed(ai: dict[str, Any], final: dict[str, Any]) -> str:
    """The lines whose remark a person typed over the one suggested, as the approved invoice says it: "remark changed
    on line 1", "remarks changed on lines 1, 3"; "" when none was."""
    paired = pair_lines(ai.get("line_items") or [], final.get("line_items") or [])
    numbers = [str(li.get("line_number")) for before, li in paired if li.get("remark_from") == PERSON
               and one_line(li.get("remark")) != one_line((before or {}).get("remark"))]  # fmt: skip
    if not numbers:
        return ""
    s = "s" if len(numbers) > 1 else ""
    return f"remark{s} changed on line{s} {', '.join(numbers)}"


def ai_changes(coding: InvoiceCoding, ai: dict[str, Any], cost_centers: bool = True) -> int:
    """The lines whose suggested coding the reviewer changed, counted as the approval counts them: a GL account or
    cost center the AI left blank and the reviewer picked changes nothing; a line still without a GL isn't taught.
    Without a cost center list (``cost_centers`` False) the page has no cost center to keep: only the GL counts."""
    changed = 0
    for before, li in pair_lines(ai.get("line_items", []), list(coding.line_items)):
        if li.predicted_gl_code == UNASSIGNED:
            continue
        s_gl = before.get("predicted_gl_code") if before else None
        s_cc = (before.get("predicted_cost_center", "") if cost_centers else "") if before else None
        if nothing_proposed(s_gl, s_cc):
            continue  # the reviewer's coding, not a change (B-650)
        if not suggestion_kept(s_gl, s_cc, li.predicted_gl_code, li.predicted_cost_center):
            changed += 1
    return changed


def learn_text(coding: InvoiceCoding, ai: dict[str, Any], cost_centers: bool = False,
               rules_applied: list[dict[str, Any]] | None = None) -> str:  # fmt: skip
    """What approving teaches, line by line, as the approval counts it (B-473, B-475): "1 line to code" (no GL account
    yet, or no cost center where they are used: nothing taught from it), then "Teaches from 3 lines: 1 you coded (the
    AI proposed nothing, or only a guess, and you picked it), 1 you changed, 1 as suggested (an account proposed and
    kept), 1 by a coding rule, 1 guessed account to check (the chat model's guess, still on screen: B-463)".
    ``rules_applied``: the invoice's (its ``meta``), the lines a fixed coding rule coded when it was read."""
    by_rule = {c.get("line_number"): c for c in rules_applied or []}
    todo = coded = changed = kept = guesses = ruled = 0
    for before, li in pair_lines(ai.get("line_items", []), list(coding.line_items)):
        gl, cc = li.predicted_gl_code, li.predicted_cost_center or ""
        if gl in (UNASSIGNED, "") or (cost_centers and cc in (UNASSIGNED, "")):
            todo += 1
            continue
        rule = by_rule.get(before.get("line_number")) if before else None
        s_gl = before.get("predicted_gl_code") if before else None
        s_cc = (before.get("predicted_cost_center", "") if cost_centers else "") if before else None  # B-562
        if rule is not None and rule.get("gl_to") == gl and (rule.get("cc_to") or "") == cc:
            ruled += 1
        elif line_guessed(before, rule):
            if gl == s_gl:
                guesses += 1
            else:
                coded += 1
        elif s_gl is None or nothing_proposed(s_gl, s_cc):  # nothing proposed: theirs
            coded += 1
        elif suggestion_kept(s_gl, s_cc, gl, cc):
            kept += 1
        else:
            changed += 1
    parts = [f"{coded} you coded" if coded else "", f"{changed} you changed" if changed else "",
             f"{kept} as suggested" if kept else "", f"{ruled} by a coding rule" if ruled else "",
             f"{plural(guesses, 'guessed account')} to check" if guesses else ""]  # fmt: skip
    taught = coded + changed + kept + guesses + ruled
    said = [f"{plural(todo, 'line')} to code" if todo else "",
            f"Teaches from {plural(taught, 'line')}: {', '.join(p for p in parts if p)}" if taught else ""]  # fmt: skip
    return " · ".join(s for s in said if s)


def differs_from_stored(
    coding: InvoiceCoding,
    stored: dict[str, Any],
    *,
    cost_centers: bool = True,
    filled: frozenset[str] | set[str] = frozenset(),
) -> bool:
    """Whether the coding on screen is not the one stored for the invoice (the reviewer changed something). Not an
    edit (B-801): an account or cost center blank either way ("" / "UNASSIGNED"), a cost center where none are in use
    (the screen has no box for it), and ``filled``, the details the screen fills itself on every check (the default
    Pay from account, the vendor list's tax numbers) while the invoice stored has none."""
    try:
        before = InvoiceCoding.model_validate({k: v for k, v in stored.items() if k != "gl_distribution"})
    except ValueError:
        return True
    ignore = {"confidence_score"} | {k for k in filled if not stored.get(k)}
    shown, kept = coding.to_output(), before.to_output()
    return _comparable(shown, ignore, cost_centers) != _comparable(kept, ignore, cost_centers)


# A line's account or cost center not given, however it is written: the screen posts "UNASSIGNED" for a cost center
# stored blank, and that is no edit (B-801).
_BLANK_CODES = {None, "", UNASSIGNED}


def _comparable(output: dict[str, Any], ignore: set[str], cost_centers: bool = True) -> dict[str, Any]:
    out = {k: v for k, v in output.items() if k not in ignore}
    blank = ("predicted_gl_code", "predicted_cost_center")
    # A remark only a person's: one suggested again (the account changed, the supplier's usual one did) is no edit.
    remark = ("remark", "remark_from")
    out["line_items"] = [
        {k: ("" if k in blank and (v in _BLANK_CODES or (k == blank[1] and not cost_centers)) else v)
         for k, v in li.items() if k not in remark or li.get("remark_from") == "person"}
        for li in out.get("line_items") or []
    ]  # fmt: skip
    return out
