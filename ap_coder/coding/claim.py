"""The receipts of one expense claim as ONE voucher (D-216): no reading, no database.

Each receipt is read on its own, as an invoice is (``InvoicePipeline``); ``claim_coding`` makes the voucher of them:

* **Lines:** the receipts' lines in order, each named with its merchant. A receipt whose lines add up to its total
  gives its lines; one whose lines do not (or that printed none the reader could split) gives one line, the merchant
  and the date, for its amount before tax.
* **Tax:** each line carries the tax its own receipt charged on it (``LineItem.taxes_read``: the receipt's own
  ``taxmath.invoice_taxes``, so a tip takes none), and the voucher's tax lines are the receipts' added up by tax,
  province and rate.
* **Totals:** the receipts' totals added up. A receipt in another currency than most, or whose total could not be
  read, adds nothing: it gets one line of 0.00 and a note naming it (``CLAIM_RECEIPT_NOT_ADDED``), never an amount
  nobody saw.
* **Supplier and reference:** left empty. The supplier is the employee being repaid, never a merchant, and a claim
  has no invoice number: the two findings say so in a claim's words (``claim_findings``).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..core.reference_data import UNASSIGNED
from ..core.schema import InvoiceCoding, LineItem, TaxLine
from ..core.store.joined import CLAIM
from ..core.tax_setup import TaxSetup
from ..intake.joined import pages_said
from .money import CENT, add_up, half_up, nil, within
from .taxmath import invoice_taxes
from .validation import WARNING, Issue

NOT_ADDED = "CLAIM_RECEIPT_NOT_ADDED"
NO_SUPPLIER = ("The receipts of one expense claim: the supplier is the employee being repaid, not a merchant on a "
               "receipt. Choose the employee here")  # fmt: skip
NO_REFERENCE = ("An expense claim has no invoice number: type a reference of your own here (the employee and the "
                "month, or the claim's number)")  # fmt: skip


@dataclass
class Receipt:
    """One receipt of a claim as it was read on its own: the file as it came, its coding (None: it could not be
    read) and why not, in plain words."""

    name: str
    coding: InvoiceCoding | None = None
    said: str = ""


def _lines_of(coding: InvoiceCoding, setup: TaxSetup) -> list[LineItem]:
    """The receipt's lines for the voucher, each with the tax its receipt charged on it: its own lines when they add
    up to its total, else one line for its amount before tax."""
    merchant = coding.vendor_name.strip() or "Receipt"
    lines = coding.line_items
    whole = len(lines) == 1 and lines[0].description.strip() == f"Invoice {coding.invoice_number}".strip()
    adds_up = (bool(lines) and within(add_up(li.amount for li in lines), coding.subtotal, CENT)
               and within(half_up(coding.subtotal + coding.tax_total), coding.grand_total, CENT))  # fmt: skip
    if adds_up and not whole:
        taxes = invoice_taxes(coding, setup).lines
        return [li.model_copy(update={
            "description": f"{merchant}: {li.description}",
            "taxes_read": {t.tax_type: t.amount for t in of_line.taxes if not nil(t.amount)} or None,
        }) for li, of_line in zip(lines, taxes, strict=True)]  # fmt: skip
    amount = half_up(coding.grand_total - coding.tax_total)
    accounts = {(li.predicted_gl_code, li.predicted_cost_center) for li in lines}
    account, cost_center = accounts.pop() if len(accounts) == 1 else (UNASSIGNED, "")
    read: dict[str, float] = {}
    for tax in coding.tax_lines:
        read[tax.tax_type] = half_up(read.get(tax.tax_type, 0.0) + tax.tax_amount)
    one = lines[0] if len(lines) == 1 else None  # the reader's own one line keeps what decided its account
    return [LineItem(
        line_number=1, description=f"{merchant} {coding.invoice_date}", quantity=1.0, unit_price=amount, amount=amount,
        predicted_gl_code=account, predicted_cost_center=cost_center, taxes_applied=sorted(read),
        taxes_read={k: v for k, v in read.items() if not nil(v)} or None,
        reasoning_justification=one.reasoning_justification if one else "One line for the whole receipt: its lines "
        "did not add up to its total.", candidates=one.candidates if one else [],
        decided_by=one.decided_by if one else {},
    )]  # fmt: skip


def _not_added(receipt: Receipt, why: str, shown: str = "") -> LineItem:
    """The 0.00 line of a receipt whose amounts are not in the voucher: a person types what it comes to."""
    return LineItem(line_number=1, description=f"{receipt.name}: {shown or 'not read'}", quantity=1.0, unit_price=0.0,
                    amount=0.0, predicted_gl_code=UNASSIGNED, reasoning_justification=why)  # fmt: skip


def claim_coding(receipts: list[Receipt], setup: TaxSetup) -> tuple[InvoiceCoding, list[str]]:
    """The voucher of a claim's ``receipts`` (in order), and of each receipt why its amounts are not in it ("" when
    they are). The claim's currency is the one most receipts read are in (CAD when none was read); its date, the
    latest receipt's."""
    read = [r.coding for r in receipts if r.coding is not None and not nil(r.coding.grand_total)]
    currency = Counter(c.currency for c in read).most_common(1)[0][0] if read else "CAD"
    lines: list[LineItem] = []
    notes: list[str] = []
    taxes: dict[tuple[str, str, float], list[float]] = {}
    tax_total = grand_total = 0.0
    for receipt in receipts:
        coding, note = receipt.coding, ""
        if coding is None or nil(coding.grand_total):
            note = receipt.said or "its total could not be read"
            mine = [_not_added(receipt, f"Not in the claim's total: {note}.")]
        elif coding.currency != currency:
            note = f"it is in {coding.currency} ({coding.grand_total:,.2f}) and the claim is in {currency}"
            shown = f"{coding.vendor_name.strip() or 'receipt'} {coding.invoice_date}, {coding.grand_total:,.2f} " \
                    f"{coding.currency}"  # fmt: skip
            mine = [_not_added(receipt, f"Not in the claim's total: {note}.", shown)]
        else:
            mine = _lines_of(coding, setup)
            tax_total, grand_total = half_up(tax_total + coding.tax_total), half_up(grand_total + coding.grand_total)
            for tax in coding.tax_lines:
                sums = taxes.setdefault((tax.tax_type, tax.province, tax.rate), [0.0, 0.0])
                sums[0], sums[1] = half_up(sums[0] + tax.taxable_amount), half_up(sums[1] + tax.tax_amount)
        notes.append(note)
        lines += [li.model_copy(update={"line_number": len(lines) + n}) for n, li in enumerate(mine, start=1)]
    dates = [c.invoice_date for c in read]
    return InvoiceCoding(
        vendor_name="", invoice_number="", invoice_date=max(dates) if dates else dt.date.today().isoformat(),
        currency=currency, subtotal=add_up(li.amount for li in lines),
        tax_lines=[TaxLine(tax_type=kind, province=province, rate=rate, taxable_amount=taxable, tax_amount=amount)
                   for (kind, province, rate), (taxable, amount) in taxes.items()],
        tax_total=tax_total, grand_total=grand_total,
        confidence_score=min((c.confidence_score for c in read), default=0.0) if len(read) == len(receipts) else 0.0,
        line_items=lines,
    ), notes  # fmt: skip


def claim_findings(report: Any, files: list[dict[str, Any]]) -> None:
    """What the checks say of an invoice made of ``files`` (``Store.joined_files``, each with its ``note``) when
    they are the receipts of one claim; any other invoice's ``report`` is left as it is. The missing supplier and
    invoice number are said in a claim's words, and each receipt whose amounts are not in the total is named."""
    if not files or files[0]["kind"] != CLAIM:
        return
    words = {"MISSING_VENDOR": NO_SUPPLIER, "MISSING_INVOICE_NUMBER": NO_REFERENCE}
    for issue in report.issues:
        issue.message = words.get(issue.code, issue.message)
    for f in files:
        if f.get("note"):
            said = (f"{f['name']} ({pages_said(f)}) is not in the claim's total: {f['note']}. Type its amount on its "
                    "line, or leave it at 0.00.")  # fmt: skip
            report.issues.append(Issue(WARNING, NOT_ADDED, said))
