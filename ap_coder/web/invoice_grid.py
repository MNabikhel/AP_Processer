"""The review screen's line grid and tax grid: the rows, the choices of each cell and the actions on the lines.

* How a code reads and what a cell offers: ``gl_display``, ``cc_display``, ``gl_name``; the accounts and cost
  centers a line may use (``gl_options``, ``cc_options``, ``vendor_accounts``: the picker's "Used for this vendor");
  the provinces, the place of supply (``supply_line``) and the lines' tax codes there (``tax_choices``).
* ``grid``: what the two grids draw, a row a line, with what is guessed and what is to check.
* The actions, each returning the new rows: ``split`` a line, ``apply_rules`` (the fixed coding rules), ``po_coding``
  (the PO's coding), ``fill_lines`` (Apply to all lines, ``usual_cost_center``), ``use_suggestion``.

The form's names and ``Draft`` are ``invoice_form.py``'s; the routes, ``pages/invoice_grid.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pandas as pd

from ap_coder.ai.offline_coder import renamed_from
from ap_coder.ai.supplier_prior import prior_for
from ap_coder.coding import rules
from ap_coder.coding.line_tax import (
    all_labels,
    exemption_to_confirm,
    legacy_basis,
    tax_code_of,
    tax_guessed,
    tax_learned,
)
from ap_coder.coding.line_tax import options as tax_code_options
from ap_coder.coding.po import match_invoice
from ap_coder.coding.review import is_blank, split_line, uses_cost_centers
from ap_coder.coding.tax import (
    OUTSIDE_CANADA,
    PROVINCE_NAMES,
    SUPPLY_OURS,
    SUPPLY_PRINTED,
    SUPPLY_SITE,
    SUPPLY_VENDOR,
    TAX_TYPES,
    meals_note,
    province_label,
    supply_source,
)
from ap_coder.core.memory import pair_lines
from ap_coder.core.reference_data import UNASSIGNED, ReferenceData, short_name
from ap_coder.core.schema import PROVINCE_VALUES, InvoiceCoding
from ap_coder.core.store import Store, line_guessed
from ap_coder.core.ui import TAX_LABELS
from ap_coder.web import account_lines
from ap_coder.web.invoice_form import NO_PROVINCE, Draft, _number, _text, lines_frame, lines_records

if TYPE_CHECKING:
    from ap_coder.web.invoice_view import Analysis


# --- How a code reads, and the choices of a cell ------------------------------------------------------------------

NEEDS_GL = "Needs an account"  # what a clerk sees instead of the stored UNASSIGNED code
NEEDS_CC = "Needs a cost center"


def gl_display(code: str | None) -> str:
    """A GL code for display: UNASSIGNED reads as "Needs an account", a blank as an em dash."""
    return NEEDS_GL if code == UNASSIGNED else (code or "—")


def cc_display(code: str | None) -> str:
    return NEEDS_CC if code == UNASSIGNED else (code or "")


def gl_name(reference: ReferenceData | None, code: str) -> str:
    """A GL account's short name ("" for a code not in the list, or before any list is imported)."""
    row = reference.chart_of_accounts.get(code) if reference is not None else None
    return short_name((row or {}).get("description", "")) if row else ""


def gl_options(reference: ReferenceData, lines: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(code, label) of the GL accounts a line may use (not the tax accounts); an unknown code a line has is kept,
    so it shows and can be fixed."""
    labels = {UNASSIGNED: NEEDS_GL}
    for row in reference.chart_of_accounts.rows:
        labels[row["gl_code"]] = f"{row['gl_code']} · {short_name(row.get('description', ''))[:44]}"
    codes = [UNASSIGNED, *reference.line_accounts()]
    for li in lines:
        code = _text(li.get("predicted_gl_code"))
        if code and code not in codes:
            codes.append(code)
    return [(c, labels.get(c, f"{c} · unknown code")) for c in codes]


def cc_options(reference: ReferenceData, lines: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(code, label) of the cost centers a line may be offered ([] when the organisation has none:
    ``ReferenceData.line_cost_centers``); one a line has that is not offered, or not in the list, is kept, so it
    shows and can be fixed."""
    if reference.cost_centers is None:
        return []
    labels = {row["cost_center"]: f"{row['cost_center']} · {row.get('description', '')[:30]}"
              for row in reference.cost_centers.rows}  # fmt: skip
    out = [(UNASSIGNED, NEEDS_CC)] + [(code, labels[code]) for code in reference.line_cost_centers() or ()]
    known = {c for c, _ in out}
    for li in lines:
        code = _text(li.get("predicted_cost_center"))
        if code and code not in known:
            out.append((code, labels.get(code, code)))
            known.add(code)
    return out


def province_options() -> list[tuple[str, str]]:
    return [(p, f"{p} · {PROVINCE_NAMES[p]}" if p in PROVINCE_NAMES else province_label(p, "Unknown"))
            for p in PROVINCE_VALUES]  # fmt: skip


def supply_line(header: dict[str, Any], lines: list[dict[str, Any]], setup: Any) -> dict[str, Any]:
    """The place of supply as the review screen says it: {province, label, why, open}: open when it is not known or
    is only the vendor's province (a guess), so the reviewer sees the choice."""
    coding = SimpleNamespace(
        ship_to_province=str(header.get("ship_to_province") or ""),
        supplier_province=str(header.get("supplier_province") or ""),
        line_items=[SimpleNamespace(predicted_cost_center=str(r.get("predicted_cost_center") or "")) for r in lines],
    )
    province, source = supply_source(coding, setup)
    if not province and coding.ship_to_province == OUTSIDE_CANADA:
        return {"province": OUTSIDE_CANADA, "label": "Outside Canada", "why": "delivered outside Canada: no Canadian "
                "sales tax", "open": False}  # fmt: skip
    why = {  # the ship-to field holds only what is printed or set by the reviewer (pipeline.finalise_coding)
        # A bill-to read under a customer label is ours, used when no ship-to is printed (D-61): said as either.
        SUPPLY_PRINTED: "the invoice's ship-to, else its bill-to (or as you set it)",
        SUPPLY_SITE: "the province of the cost center the lines are coded to",
        SUPPLY_OURS: "your province (Settings): no ship-to or bill-to printed",
        SUPPLY_VENDOR: "only the vendor's province: set where it is delivered to you",
    }.get(source, "unknown: choose where it is delivered to you")
    if coding.ship_to_province == OUTSIDE_CANADA and source in (SUPPLY_OURS, SUPPLY_VENDOR):  # as the tax check says
        why = "the ship-to printed is outside Canada: checked against " + (
            "your province (Settings)" if source == SUPPLY_OURS else "only the vendor's province")  # fmt: skip
    label = f"{province} · {PROVINCE_NAMES[province]}" if province in PROVINCE_NAMES else "Not known"
    return {"province": province, "label": label, "why": why, "open": source in ("", SUPPLY_VENDOR)}


def tax_province_options() -> list[tuple[str, str]]:
    return [(p, province_label(p) if p == OUTSIDE_CANADA else (p or NO_PROVINCE)) for p in PROVINCE_VALUES]


def vendor_accounts(store: Store, vendor_name: str, limit: int = 6) -> list[str]:
    """The GL accounts this vendor's lines were approved to, most used first (the newest first among equals), at most
    ``limit``: the account picker's "Used for this vendor" (D-56). Only what a person decided (or the ERP's history),
    never what the AI proposed; lines left without an account are not counted."""
    if not (vendor_name or "").strip():
        return []
    return store.vendor_lessons(vendor_name).accounts[:limit]


# --- The rows -----------------------------------------------------------------------------------------------------


def grid(reference: ReferenceData, draft: Draft, ai: dict[str, Any] | None = None,
         vendor_gls: list[str] | None = None, checked: Analysis | None = None) -> dict[str, Any]:  # fmt: skip
    """What the line and tax grids need: the rows, with a stable key each, and the choices of every cell. ``ai``: the
    invoice as read: a row whose account is still the one the AI only guessed for its line is marked (B-463), not one
    whose account the reviewer, a coding rule or the PO coding put there (B-475). ``vendor_gls``: the accounts the
    vendor's lines went to (``vendor_accounts``), offered first by the account picker; those still in the list.
    With a chart of accounts imported (``account``) each row is one Account box: what it shows is its ``acct``
    (``account_lines.row_views``), with the findings and the taxes of ``checked``, the draft's analysis."""
    paired = list(pair_lines((ai or {}).get("line_items") or [], draft.lines))
    guesses = {
        id(row)
        for before, row in paired
        if before and row and line_guessed(before) and row.get("predicted_gl_code") == before.get("predicted_gl_code")
    }
    # A tax code worked out from several possible sets of lines, still as it was worked out: to check (D-64).
    charged = _charged(draft)
    tax_guesses = {
        id(row)
        for before, row in paired
        if before and row and tax_guessed(row) and tax_code_of(row, charged) == tax_code_of(before, charged)
    }
    # A PST exemption inferred when the invoice was read: picking it again later is still confirmed on review (B-723).
    inferred = {id(row): exemption_to_confirm(before) for before, row in paired if before and row}
    # A line read as AP named it on an earlier invoice, still so: the text printed, said under it (D-120).
    printed = {id(row): renamed_from(before.get("reasoning_justification")) for before, row in paired
               if before and row and before.get("description") == row.get("description")}  # fmt: skip
    codes = tax_choices(draft, reference.tax)
    account = account_lines.in_use(reference)
    views = account_lines.row_views(
        reference, draft.lines, (ai or {}).get("line_items") or [],
        checked.report.issues if checked is not None and checked.report is not None else (),
        checked.taxes if checked is not None else None,
        (checked.output.get("line_items") if checked is not None and checked.coding is not None else None),
        meals_note(codes["province"]),
    ) if account else []  # fmt: skip
    for shown, row in zip(views, draft.lines, strict=False):  # still the model's own guess: to check (B-463)
        if id(row) in guesses and not shown["state"] and shown["name"]:
            shown.update(state=account_lines.CHECK, note=account_lines.GUESS)
    return {  # "guessed": an account no approval backs, marked for the reviewer to check (B-463)
        "account": account,
        "acct_empty": account_lines.EMPTY,
        "lines": [
            {
                **row,
                "key": f"r{i}",
                "guessed": id(row) in guesses and row.get("predicted_gl_code") not in (None, "", UNASSIGNED),
                "tax_code": tax_code_of(row, charged),
                "tax_guess": id(row) in tax_guesses,
                "tax_learned": tax_learned(row),  # taken from what AP approved on the vendor's similar lines (D-70)
                "tax_inferred": inferred.get(id(row), ""),
                "read_as": printed.get(id(row), ""),
                **({"acct": views[i]} if account else {}),
            }
            for i, row in enumerate(draft.lines)
        ],
        "tax_codes": codes["options"],
        # a line added on screen: the code most lines have, else the province's usual taxes
        "tax_default": max((c for c, _ in codes["options"]), key=[tax_code_of(r, charged) for r in draft.lines].count)
        if draft.lines
        else codes["options"][0][0],
        "tax_code_labels": codes["labels"],
        "tax_marks": {},  # {line number: why}: the lines the Tax check points at (``analyse``), drawn yellow
        "show_qty": shows_quantities(draft.lines),
        "taxes": draft.taxes,
        "gl_options": (gls := gl_options(reference, draft.lines)),
        "cc_options": (ccs := cc_options(reference, draft.lines)),
        "gl_labels": dict(gls),  # a line's own choice, printed in its cell (the picker has the whole list)
        "cc_labels": dict(ccs),
        "vendor_accounts": [c for c in vendor_gls or [] if c in {code for code, _ in gls}],
        "tax_types": list(TAX_TYPES),
        "tax_labels": {t: TAX_LABELS.get(t, t) for t in TAX_TYPES},
        "tax_provinces": tax_province_options(),
        "has_cc": reference.cost_centers is not None,
        # The GL accounts marked meals and entertainment, and the note on a line coded to one (D-90).
        "meals_gls": " ".join(sorted(getattr(reference.tax, "meals_gl_codes", ()) or ())),
        "meals_note": meals_note(codes["province"]),
    }


def shows_quantities(lines: list[dict[str, Any]]) -> bool:
    """Whether the line grid shows Qty and Unit price as columns: a line with a quantity other than 1, or a unit price
    other than its amount (otherwise they say nothing the amount does not, and the toggle shows them)."""

    def num(value: Any) -> float | None:
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    for li in lines:
        qty, price, amount = num(li.get("quantity")), num(li.get("unit_price")), num(li.get("amount"))
        if (qty is not None and abs(qty - 1) > 1e-9) or (
            price is not None and amount is not None and abs(price - amount) > 0.005
        ):
            return True
    return False


def tax_choices(draft: Draft, setup: Any = None) -> dict[str, Any]:
    """The choices of the lines' tax-code dropdowns where the invoice on screen is supplied (D-61, D-64): {options:
    [(code, label)], labels: {code: label} for every code, province}. The lines' own codes are always among them."""
    header, lines = getattr(draft, "header", None) or {}, list(getattr(draft, "lines", None) or [])
    province = supply_line(header, lines, setup)["province"]
    charged = _charged(draft)
    current = [tax_code_of(row, charged) for row in lines]
    return {"options": tax_code_options(province, charged, current), "labels": all_labels(province),
            "province": province}  # fmt: skip


def _charged(draft: Any) -> list[str]:
    """The taxes the tax grid on screen charges and some line is marked with (``line_tax.legacy_basis``)."""
    return legacy_basis(getattr(draft, "lines", None) or [],
                        [{"tax_type": t.get("tax_type"), "tax_amount": _number(t.get("tax_amount"))}
                         for t in getattr(draft, "taxes", None) or []])  # fmt: skip


# --- The actions on the lines -------------------------------------------------------------------------------------


def numbered_lines(lines: pd.DataFrame) -> pd.DataFrame:
    """A copy of the line grid with the lines just added numbered as the checks number them (after the last line,
    in order), so an action on "line 6" finds the row the reviewer added."""
    out = lines.copy()
    numbers = pd.to_numeric(out["line_number"], errors="coerce")
    next_no = int(numbers.max()) + 1 if numbers.notna().any() else 1
    for index, row in out.iterrows():
        if pd.notna(numbers[index]) or all(is_blank(row.get(k)) for k in ("description", "amount")):
            continue
        out.at[index, "line_number"] = next_no
        next_no += 1
    out["line_number"] = pd.to_numeric(out["line_number"], errors="coerce")
    return out


def grid_row(lines: pd.DataFrame, coding: InvoiceCoding, position: int) -> Any:
    """The grid rows of the ``position``-th line of the coding: the first, second… row with its number, as it comes in
    the coding (lines that share a number are told apart by their order). Every row with the number if unsure."""
    li = coding.line_items[position]
    same = lines["line_number"] == li.line_number
    nth = sum(1 for x in coding.line_items[:position] if x.line_number == li.line_number)
    rows = lines.index[same]
    return [rows[nth]] if nth < len(rows) else same


def split(draft: Draft, number: int, parts: list[tuple[str, str, float]], has_cc: bool,
          by: str = "percent") -> list[dict[str, Any]]:  # fmt: skip
    """Split line ``number`` across several GL accounts / cost centers by percentage or by dollar amount
    (``review.split_line``); a part left at 0 is left out. ValueError when the parts don't add up (to 100%, or to the
    line's amount) or there is no such line."""
    frame = lines_frame(draft.lines)
    rows = frame[pd.to_numeric(frame["line_number"], errors="coerce") == number]
    if rows.empty:
        raise ValueError(f"there is no line {number}")
    row = rows.iloc[0]
    gl, cc = row.get("predicted_gl_code") or UNASSIGNED, row.get("predicted_cost_center") or ""
    chosen = [(g or gl, (c or cc) if has_cc else "", p) for g, c, p in parts if (p != 0 if by == "amount" else p > 0)]
    return lines_records(split_line(frame, number, chosen, by=by))


def apply_rules(store: Store, coding: InvoiceCoding, draft: Draft, invoice_id: int,
                ai: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:  # fmt: skip
    """Code the lines a fixed coding rule would code differently; records the rule's change for the AI's own lines,
    so its accuracy is measured on its own answer there. Returns the new rows and how many lines changed."""
    _, changes = rules.apply(coding, store.coding_rules())
    frame = lines_frame(draft.lines)
    numbers = pd.to_numeric(frame["line_number"], errors="coerce")
    done = []
    for c in changes:
        at = numbers == c["line_number"]
        if not at.any():  # a line just added (not numbered yet): left for the reviewer to code
            continue
        frame.loc[at, "predicted_gl_code"] = c["gl_to"]
        frame.loc[at, "predicted_cost_center"] = c["cc_to"]
        done.append(c)
    ai_lines = {li.get("line_number"): li for li in ai.get("line_items") or []}
    store.add_rules_applied(invoice_id, [
        {**c, "gl_from": ai_lines[c["line_number"]].get("predicted_gl_code", ""),
         "cc_from": ai_lines[c["line_number"]].get("predicted_cost_center", "")}
        for c in done
        if c["line_number"] in ai_lines
    ])  # fmt: skip
    return lines_records(frame), len(done)


def po_coding(store: Store, coding: InvoiceCoding, draft: Draft, invoice_id: int) -> tuple[list[dict[str, Any]], int]:
    """Set the GL account and cost center of the lines whose coding differs from the PO's to the PO's."""
    match = match_invoice(coding, store, invoice_id)
    differs = match.coding_differs if match is not None else []
    frame = numbered_lines(lines_frame(draft.lines))
    for m in differs:
        at = frame["line_number"] == m.invoice_line
        if m.po_gl:
            frame.loc[at, "predicted_gl_code"] = m.po_gl
        if m.po_cc:
            frame.loc[at, "predicted_cost_center"] = m.po_cc
    return lines_records(frame), len(differs)


def fill_lines(draft: Draft, gl: str, cc: str) -> tuple[list[dict[str, Any]], int, int]:
    """Give every line without a GL account ``gl`` and every line without a cost center ``cc`` (either may be "":
    left as it is); a line already coded keeps its code. Returns the new rows and how many lines got each."""
    frame = lines_frame(draft.lines)
    real = ~frame.apply(lambda row: all(is_blank(row.get(k)) for k in ("description", "amount")), axis=1)
    counts = []
    for column, code in (("predicted_gl_code", gl), ("predicted_cost_center", cc)):
        missing = real & frame[column].apply(lambda v: is_blank(v) or v == UNASSIGNED)
        if code:
            frame.loc[missing, column] = code
        counts.append(int(missing.sum()) if code else 0)
    return lines_records(frame), counts[0], counts[1]


def usual_cost_center(store: Store, reference: ReferenceData, vendor_name: str) -> str:
    """The one cost center every past decision on this vendor's lines used (still in the list), else "": offered
    for its lines without one. Two cost centers, or a line without one, and there is no usual one."""
    if not uses_cost_centers(reference) or not (vendor_name or "").strip():
        return ""
    used = store.vendor_lessons(vendor_name).cost_centers
    if not used:  # nothing approved here yet: the one business unit the supplier's lines went to last year (D-212)
        used = frozenset(u.business_unit for u in prior_for(store, vendor_name).accounts)
    only = next(iter(used)) if len(used) == 1 else ""
    if reference.pairs is not None and only not in reference.pairs.business_units():
        return ""  # a business unit since closed, or one no account is offered on
    return only if only != UNASSIGNED and reference.cost_centers.get(only) is not None else ""


def use_suggestion(reference: ReferenceData, coding: InvoiceCoding, draft: Draft, position: int, gl: str,
                   cc: str) -> list[dict[str, Any]]:  # fmt: skip
    """Code the ``position``-th line with a suggested GL account (and its cost center, when the line has none).
    With a chart of accounts imported a suggestion is a whole account string: the line takes its business unit too,
    since the account may not exist on the one the line had (D-212)."""
    if not 0 <= position < len(coding.line_items):
        raise ValueError("that line is no longer on the invoice")
    li = coding.line_items[position]
    frame = numbered_lines(lines_frame(draft.lines))
    at = grid_row(frame, coding, position)
    frame.loc[at, "predicted_gl_code"] = gl
    takes_unit = reference.pairs is not None or li.predicted_cost_center in ("", UNASSIGNED)
    if cc and takes_unit and reference.cost_centers is not None:
        frame.loc[at, "predicted_cost_center"] = cc
    return lines_records(frame)
