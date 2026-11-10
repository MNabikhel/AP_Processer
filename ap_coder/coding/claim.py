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
  nobody saw. As many receipts in each of two currencies: the claim is put in CAD (else the first by its letters),
  whatever the order of the files, and the note names the receipts of both. Once a person types the receipt's amount
  on its line the note goes, and the claim's totals follow (``totals_follow``).
* **Supplier and reference:** left empty. The supplier is the employee being repaid, never a merchant, and a claim
  has no invoice number: the two findings say so in a claim's words (``claim_findings``).
* **Tax areas:** JD Edwards takes one tax area a voucher. Receipts of two (the GST on some, Ontario's HST on
  another) are held by ``JDE_TAX_AREA``, said in a claim's words with which files are of which (``two_areas``): the
  way forward is one voucher a tax area.
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
from .tax import PROVINCE_NAMES
from .taxmath import invoice_taxes
from .validation import WARNING, Issue

NOT_ADDED = "CLAIM_RECEIPT_NOT_ADDED"
NO_SUPPLIER = ("The receipts of one expense claim: the supplier is the employee being repaid, not a merchant on a "
               "receipt. Choose the employee here")  # fmt: skip
NO_REFERENCE = ("An expense claim has no invoice number: type a reference of your own here (the employee and the "
                "month, or the claim's number)")  # fmt: skip
SEPARATE_THEN_JOIN = ("Use “Make these separate invoices again” under the document, then choose each tax area's "
                      "receipts again on Add invoices, as the receipts of one claim (a receipt alone in its tax area "
                      "stays an invoice of its own)")  # fmt: skip


@dataclass
class Receipt:
    """One receipt of a claim as it was read on its own: the file as it came, its coding (None: it could not be
    read) and why not, in plain words."""

    name: str
    coding: InvoiceCoding | None = None
    said: str = ""


def tax_area(tax_lines: list[Any]) -> str:
    """The tax area a receipt is of, in words, from its tax lines (``TaxLine``s, or as stored): "GST", "Ontario HST"
    (the province's; its rate when no province was read: "HST 13%"); "" when it charges neither."""
    found: list[str] = []
    for tax in tax_lines:
        t = tax if isinstance(tax, dict) else tax.model_dump()
        kind, rate = str(t.get("tax_type") or ""), float(t.get("rate") or 0)
        if kind not in ("GST", "HST") or nil(float(t.get("tax_amount") or 0)):
            continue
        province = PROVINCE_NAMES.get(str(t.get("province") or ""))
        label = kind if kind == "GST" else f"{province} HST" if province else f"HST {rate * 100:.6g}%" if rate else kind
        if label not in found:
            found.append(label)
    return " and ".join(found)


def two_areas(files: list[dict[str, Any]], joined: bool = False) -> str:
    """What to do with receipts of two tax areas where JD Edwards takes one a voucher, with which files are of
    which; "" when ``files`` (each its ``name`` and ``tax``: ``tax_area``) are of one area or none. ``joined``: they
    are one voucher already, to separate first. No full stop at its end."""
    areas: dict[str, list[str]] = {}
    for f in files:
        if f.get("tax"):
            areas.setdefault(f["tax"], []).append(f["name"])
    if len(areas) < 2:
        return ""
    count = {2: "two", 3: "three"}.get(len(areas), str(len(areas)))
    whose = ". ".join(f"{area}: {', '.join(names)}" for area, names in areas.items())
    return (f"These receipts are of {count} tax areas ({'; '.join(areas)}), and JD Edwards takes one tax area a "
            f"voucher: {'separate them and ' if joined else ''}join each tax area's receipts as its own voucher. "
            f"{whose}")  # fmt: skip


def totals_follow(header: dict[str, str], lines: list[dict[str, Any]], files: list[dict[str, Any]],
                  number: Any) -> dict[str, str]:  # fmt: skip
    """On the review screen of a claim (``files``: ``Store.joined_files``), once a person has typed the amount of a
    receipt that was not in the total (its line, named after its file): the subtotal and the total take it, in
    ``header`` (the screen's, as typed) and returned ({} when nothing changed). Only while the totals are still the
    ones without it: totals a person typed are theirs. ``number``: reads an amount as typed (ValueError)."""
    names = [f["name"] for f in files if f.get("note")] if files and files[0]["kind"] == CLAIM else []
    try:
        amounts = [(str(row.get("description") or ""), number(str(row.get("amount") or "0"))) for row in lines]
        subtotal, tax, total = (number(str(header.get(k) or "0")) for k in ("subtotal", "tax_total", "grand_total"))
    except ValueError:
        return {}
    typed = add_up(amount for text, amount in amounts if any(text.startswith(f"{name}:") for name in names))
    whole = add_up(amount for _, amount in amounts)
    if nil(typed) or not within(subtotal, whole - typed, CENT) or not within(total, subtotal + tax, CENT):
        return {}
    followed = {"subtotal": f"{whole:.2f}", "grand_total": f"{half_up(whole + tax):.2f}"}
    header.update(followed)
    return followed


def _lines_of(coding: InvoiceCoding, setup: TaxSetup, unassigned: str = "") -> list[LineItem]:
    """The receipt's lines for the voucher, each with the tax its receipt charged on it: its own lines when they add
    up to its total, else one line for its amount before tax (``unassigned``: its cost center when its lines had
    several accounts)."""
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
    account, cost_center = accounts.pop() if len(accounts) == 1 else (UNASSIGNED, unassigned)
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


def _not_added(receipt: Receipt, why: str, shown: str = "", unassigned: str = "") -> LineItem:
    """The 0.00 line of a receipt whose amounts are not in the voucher: a person types what it comes to."""
    return LineItem(line_number=1, description=f"{receipt.name}: {shown or 'not read'}", quantity=1.0, unit_price=0.0,
                    amount=0.0, predicted_gl_code=UNASSIGNED, predicted_cost_center=unassigned,
                    reasoning_justification=why)  # fmt: skip


def claim_coding(receipts: list[Receipt], setup: TaxSetup, cost_centers: bool = False
                 ) -> tuple[InvoiceCoding, list[str]]:  # fmt: skip
    """The voucher of a claim's ``receipts`` (in order), and of each receipt why its amounts are not in it ("" when
    they are). The claim's currency is the one most receipts read are in (CAD when none was read, and when as many
    are in CAD as in another: never the first file's); its date, the latest receipt's. ``cost_centers``: the
    company uses them, so a line made here has none yet (unassigned), as any line coded has."""
    unassigned = UNASSIGNED if cost_centers else ""
    read = [r for r in receipts if r.coding is not None and not nil(r.coding.grand_total)]
    counts = Counter(r.coding.currency for r in read)
    tied = sorted(c for c, n in counts.items() if n == max(counts.values()))  # the currencies most receipts are in
    currency = "CAD" if "CAD" in tied or not tied else tied[0]
    read = [r.coding for r in read]
    lines: list[LineItem] = []
    notes: list[str] = []
    taxes: dict[tuple[str, str, float], list[float]] = {}
    tax_total = grand_total = 0.0
    for receipt in receipts:
        coding, note = receipt.coding, ""
        if coding is None or nil(coding.grand_total):
            note = receipt.said or "its total could not be read"
            mine = [_not_added(receipt, f"Not in the claim's total: {note}.", unassigned=unassigned)]
        elif coding.currency != currency:
            note = f"it is in {coding.currency} ({coding.grand_total:,.2f}) and the claim is in {currency}"
            if coding.currency in tied:  # as many receipts in each: nothing read says which the claim is in
                theirs = ", ".join(r.name for r in receipts if r.coding in read and r.coding.currency == currency)
                note = (
                    f"it is in {coding.currency} ({coding.grand_total:,.2f}) and {theirs} in {currency}, as many "
                    f"receipts in each: the claim was put in {currency}, and its currency is yours to set"
                )
            shown = f"{coding.vendor_name.strip() or 'receipt'} {coding.invoice_date}, {coding.grand_total:,.2f} " \
                    f"{coding.currency}"  # fmt: skip
            mine = [_not_added(receipt, f"Not in the claim's total: {note}.", shown, unassigned)]
        else:
            mine = _lines_of(coding, setup, unassigned)
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


def claim_findings(report: Any, files: list[dict[str, Any]], coding: InvoiceCoding | None = None) -> None:
    """What the checks say of an invoice made of ``files`` (``Store.joined_files``, each with its ``note`` and its
    ``tax``) when they are the receipts of one claim; any other invoice's ``report`` is left as it is. The missing
    supplier and invoice number are said in a claim's words; a claim JD Edwards holds for its two tax areas is told
    what to do, with which files are of which; and each receipt whose amounts are not in the total is named, while
    its line in ``coding`` (the claim as it is now) is still at 0.00."""
    if not files or files[0]["kind"] != CLAIM:
        return
    words = {"MISSING_VENDOR": NO_SUPPLIER, "MISSING_INVOICE_NUMBER": NO_REFERENCE}
    if coding is None or len({area for area in (tax_area([t]) for t in coding.tax_lines) if area}) > 1:
        if held := two_areas(files, joined=True):
            words["JDE_TAX_AREA"] = f"{held}. {SEPARATE_THEN_JOIN}"
    for issue in report.issues:
        issue.message = words.get(issue.code, issue.message)
    for f in files:
        typed = coding is not None and not any(
            li.description.startswith(f"{f['name']}:") and nil(li.amount) for li in coding.line_items)  # fmt: skip
        if f.get("note") and not typed:
            said = (f"{f['name']} ({pages_said(f)}) is not in the claim's total: {f['note']}. Type its amount on its "
                    "line, or leave it at 0.00.")  # fmt: skip
            report.issues.append(Issue(WARNING, NOT_ADDED, said))
