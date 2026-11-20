"""What JD Edwards requires of a line coded to an account whose posting edit code is L or U (D-268).

An account of the chart with posting edit code L takes a posting only with a subledger (8 characters at most) and its
subledger type (1 character); one with U only with units. Both may be posted to (``chart.POSTABLE``, Settings → JD
Edwards E1 → Accounts AP posts to), so a line coded to one keeps what JD Edwards will ask for on the line itself
(``LineItem.subledger``, ``subledger_type``, ``units``), and the voucher carries it (the keying sheet, the Z-files).

Where it comes from (``fill``), a person's own always first (the review screen shows the boxes for such a line only):

* the subledger and its type: the PO line the invoice line bills (the purchase orders' Subledger and Sub Type
  columns), else the supplier's past coding of that account (the account ledger's Subledger and Sub Type, and the
  lines approved here, a typed one counting as a correction does, B-2830: its commonest,
  ``supplier_prior.AccountUse.subledger``);
* the units: the invoice line's quantity, else the units the supplier's past lines to the account all had (the units
  a person typed here for it, when there are, all the same).

A line whose account asks for neither keeps none (what is not shown is never sent). ``findings`` says what is still
missing, an error each (``JDE_SUBLEDGER_NEEDED``, ``JDE_UNITS_NEEDED``): JD Edwards would refuse the line.

Entry points: ``needed``, ``fill``, ``findings``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.chart import join_account
from ..core.reference_data import UNASSIGNED, ReferenceData

SUBLEDGER, UNITS = "L", "U"  # the posting edit codes: a subledger is required; units are required
SUBLEDGER_MOST, TYPE_MOST = 8, 1  # characters JD Edwards keeps (GLSBL, GLSBLT)
ERROR = "error"


def _code(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text == UNASSIGNED else text


def _get(line: Any, name: str) -> Any:
    return line.get(name) if isinstance(line, Mapping) else getattr(line, name, None)


def needed(reference: ReferenceData | None, line: Any) -> str:
    """``SUBLEDGER`` or ``UNITS`` when the line's account (its business unit and GL code; a line item or a dict) has
    that posting edit code and AP may post to it; "" otherwise, with no chart, and for a line not coded to a whole
    account or coded to a tax account."""
    pairs = reference.pairs if reference is not None else None
    gl, cc = _code(_get(line, "predicted_gl_code")), _code(_get(line, "predicted_cost_center"))
    if pairs is None or not gl or not cc or gl in reference.tax.tax_gl_codes():
        return ""
    edit = pairs.posting_edit(cc, gl)
    return edit if edit in (SUBLEDGER, UNITS) and edit in pairs.settings.postable_codes else ""


def _po_ledgers(coding: Any, store: Any, invoice_id: int | None) -> dict[int, tuple[str, str]]:
    """{invoice line number: the (subledger, type) of the PO line it bills}, for the lines whose PO line has one."""
    from .po import match_invoice

    match = match_invoice(coding, store, invoice_id) if coding.po_number and store is not None else None
    return {m.invoice_line: m.po_subledger for m in (match.lines if match is not None else []) if m.po_subledger[0]}


def fill(coding: Any, store: Any, reference: ReferenceData, invoice_id: int | None = None) -> None:
    """Give each line of ``coding`` what its account's posting edit code requires and it has not got yet (see the
    module); a line whose account requires neither keeps none. Nothing changes with no chart of accounts."""
    if reference.pairs is None:
        return
    wants = [needed(reference, li) for li in coding.line_items]
    for li, want in zip(coding.line_items, wants, strict=True):
        if want != SUBLEDGER:
            li.subledger = li.subledger_type = ""
        if want != UNITS:
            li.units = None
    paired = list(zip(coding.line_items, wants, strict=True))
    ledgerless = [li for li, want in paired if want == SUBLEDGER and not li.subledger]
    unitless = [li for li, want in paired if want == UNITS and not li.units]
    for li in unitless:
        li.units = li.quantity or None
    unitless = [li for li in unitless if not li.units]
    if not (ledgerless or unitless) or store is None:
        return
    from ..ai.supplier_prior import prior_for

    on_po = _po_ledgers(coding, store, invoice_id) if ledgerless else {}
    prior = prior_for(store, coding.vendor_name, always=True) if hasattr(store, "prior_stamp") else None
    for li in ledgerless:
        use = prior.use(join_account(li.predicted_cost_center, li.predicted_gl_code)) if prior else None
        li.subledger, li.subledger_type = on_po.get(li.line_number) or (
            (use.subledger, use.subledger_type) if use is not None else ("", ""))  # fmt: skip
    for li in unitless:
        use = prior.use(join_account(li.predicted_cost_center, li.predicted_gl_code)) if prior else None
        li.units = use.units if use is not None else None


def findings(coding: Any, reference: ReferenceData) -> list[tuple[str, str, str, int]]:
    """(severity, code, message, line number) for each line whose account's posting edit code requires what the line
    has not got, or has too long for JD Edwards."""
    out = []
    for li in coding.line_items:
        want = needed(reference, li)
        account = join_account(li.predicted_cost_center, li.predicted_gl_code)
        if want == SUBLEDGER and not (li.subledger and li.subledger_type):
            out.append((ERROR, "JDE_SUBLEDGER_NEEDED", f"account {account} takes a posting only with a subledger and "
                        "its type (its posting edit code is L): type them under the line", li.line_number))  # fmt: skip
        elif want == SUBLEDGER and (len(li.subledger) > SUBLEDGER_MOST or len(li.subledger_type) > TYPE_MOST):
            out.append((ERROR, "JDE_SUBLEDGER_NEEDED", f"the subledger {li.subledger} (type {li.subledger_type}) is "
                        f"longer than JD Edwards keeps: {SUBLEDGER_MOST} characters, and 1 for its type",
                        li.line_number))  # fmt: skip
        elif want == UNITS and not li.units:
            out.append((ERROR, "JDE_UNITS_NEEDED", f"account {account} takes a posting only with units (its posting "
                        "edit code is U): type them under the line", li.line_number))  # fmt: skip
    return out
