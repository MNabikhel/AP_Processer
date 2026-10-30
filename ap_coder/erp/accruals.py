"""Month-end accruals: costs incurred by the period end that are not yet in the ERP.

Three sources, none counted twice:

* Received, not invoiced: purchase order lines received (quantity received known) beyond what has been
  invoiced so far, at the PO price, on the PO line's GL account
* Invoices not yet in the ERP: invoices dated on or before the period end that are still in review,
  waiting for a second approval, approved but not exported, or exported after the period end (in the ERP's next
  month, D-133), by GL account (net of recoverable tax); a copy flagged as a duplicate of a bill already exported or
  counted, or as already in the ERP, is left out. One no person has approved yet is "proposed", on the accounts it
  was given (a guessed account counts as none, B-466); one dated more than a year before the period end is accrued
  and flagged to check (a date read wrong, or a bill long overdue: AP decides), one with no date is not accrued but
  listed (``undated``)
* Expected recurring invoices: regular vendors whose next invoice was due by the period end and has not
  arrived, at their usual amount, on the GL account most used for them (an estimate). Not for a vendor whose
  received, not invoiced PO lines are accrued: that invoice would bill them, so it is left out (``left_out``)

Entry points: ``build`` (with ``undated``: the invoices with no date, not accrued; ``left_out``: the expected
recurring invoices left out), ``by_gl_columns``, ``summary`` and ``to_csv``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from collections import defaultdict
from dataclasses import dataclass

from ..coding.po import CLOSED, billed_by_line, po_label
from ..coding.vendors import norm_invoice_number
from ..core.memory import vendor_key
from ..core.safe import csv_row
from ..core.store import APPROVED, PARKED, PENDING, REVIEW, Store
from ..core.store.summary import broken, whole
from . import recurring
from .exports import distribution

RECEIVED, NOT_IN_ERP, RECURRING = "Received, not invoiced", "Invoice not in the ERP yet", "Expected recurring invoice"
SOURCES = (RECEIVED, NOT_IN_ERP, RECURRING)
OLD_DAYS = 365  # an invoice dated more than this before the period end is flagged: a misread date, or long overdue
OLD_NOTE = "dated over a year ago: check"
# Not approved by anyone yet: its coding is what AP Coder proposed, not what a person confirmed.
PROPOSED = "proposed: not approved yet"
# Exported by a batch made after the period end: not in the ERP at the period end, so accrued for it (D-133).
EXPORTED_LATE = "exported after the month end"
# An expected recurring invoice not accrued: its vendor's received, not invoiced PO lines are, and it would bill them.
ON_PO = "left out: its received, not invoiced purchase-order lines are accrued instead"
# The export batches made after a period end (``?``): their invoices were not in the ERP at that period end (D-133).
_LATE_BATCHES = "SELECT id FROM export_batches WHERE substr(created_at, 1, 10) > ?"


@dataclass
class Accrual:
    source: str
    vendor: str
    reference: str
    description: str
    gl_code: str
    cost_center: str
    amount: float
    currency: str = "CAD"
    note: str = ""
    old: bool = False  # dated more than OLD_DAYS before the period end: accrued, flagged for AP to check


def _received_not_invoiced(store: Store, period_end: dt.date) -> list[Accrual]:
    out = []
    end = period_end.isoformat()

    def by_end(invoices: list[dict]) -> list[dict]:
        return [i for i in invoices if str(i.get("invoice_date") or i["coding"].get("invoice_date") or "") <= end]

    without_po: dict[str, list[dict]] = {}  # by vendor: read once, not once for each of its POs
    for po in store.purchase_orders():
        if po["status"] == CLOSED or not po["received_lines"]:
            continue
        full = store.purchase_order(po["po_key"])
        if full is None:
            continue
        # Returns are assumed to be reflected in the ERP's received quantity, so credits do not count here;
        # the vendor's invoices that quote no PO may still bill these lines ("PO-90155" in a description).
        # Invoices dated after the period end do not count: the goods were received, not yet invoiced, by then.
        vendor = full["vendor_key"]
        if vendor not in without_po:
            without_po[vendor] = by_end(store.vendor_invoices_without_po(vendor))
        invoices = by_end(store.po_invoices(po["po_key"])) + without_po[vendor]
        billed = billed_by_line(full, invoices, positive_only=True)
        for li in full["lines"]:
            if li["received_quantity"] is None or li.get("amount_only"):
                continue
            qty = li["received_quantity"] - billed.get(li["line_number"], 0.0)
            if qty <= 1e-6:
                continue
            out.append(
                Accrual(
                    RECEIVED,
                    full["vendor_name"],
                    f"{po_label(full['po_number'])} line {li['line_number']}",
                    li["description"],
                    li["gl_code"],
                    li["cost_center"],
                    round(qty * li["unit_price"], 2),
                    note=f"{qty:g} received and not invoiced at {li['unit_price']:,.2f}",
                )  # fmt: skip
            )
    return out


def _bill(doc: dict) -> tuple[str, str, bool]:
    """(vendor, invoice number, is a credit): what makes two invoices the same bill."""
    return (vendor_key(doc.get("vendor_name") or ""), norm_invoice_number(doc.get("invoice_number")),
            float(doc.get("grand_total") or 0) < 0)  # fmt: skip


def _flags(validation: object) -> set[str]:
    issues = validation.get("issues") if isinstance(validation, dict) else None
    return {str(i.get("code")) for i in issues or [] if isinstance(i, dict)}


def _unguessed(doc: dict, ai: dict) -> dict:
    """``doc`` with every guessed account taken out: an account the AI only guessed (``offline_coder.guessed``, the
    line as read in ``ai``) and still the one in ``doc``. An invoice no person has approved accrues only on accounts
    it was really given, never on a guess (B-466); an account the reviewer picked over a guess is theirs."""
    from ..ai.offline_coder import guessed
    from ..core.memory import pair_lines
    from ..core.reference_data import UNASSIGNED

    lines = doc.get("line_items") or []
    guesses = {id(after) for before, after in pair_lines((ai or {}).get("line_items") or [], lines)
               if before and after and guessed(before.get("reasoning_justification"))
               and after.get("predicted_gl_code") == before.get("predicted_gl_code")}  # fmt: skip
    if not guesses:
        return doc
    lines = [{**li, "predicted_gl_code": UNASSIGNED} if id(li) in guesses else li for li in lines]
    return {k: v for k, v in doc.items() if k != "gl_distribution"} | {"line_items": lines}


def _exported_bills(store: Store, rows: list[dict], end: str) -> set[tuple[str, str, bool]]:
    """The bills (``_bill``) of the invoices exported by ``end`` (the period end, D-133), of those that one of
    ``rows`` (invoices not in the ERP yet) may be a copy of: from the invoices' summary (D-110)."""
    flagged = [r for r in rows if "DUPLICATE_INVOICE" in _flags(r["validation"])]
    if not flagged:
        return set()
    wanted = {_bill(r["final_output"] or r["ai_output"] or {}) for r in flagged}
    by_end = f"export_batch IS NOT NULL AND export_batch NOT IN ({_LATE_BATCHES})"
    found = {
        (r["bill_vendor"], r["bill_number"], bool(r["bill_credit"]))
        for r in store.summary_rows(
            f"bill_number IN (SELECT value FROM json_each(?)) AND +status = ? AND {by_end} AND {whole('spend')}",
            (json.dumps(sorted({b[1] for b in wanted})), APPROVED, end),
            "bill_vendor, bill_number, bill_credit",
        )
    }
    for r in store.summary_rows(f"{broken('spend')} AND +status = ? AND {by_end}", (APPROVED, end), "id"):
        (inv,) = store.invoice_columns(("id", "final_output"), ids=[r["id"]])
        found.add(_bill(inv["final_output"] or {}))  # read again: fails as it did when every invoice was read
    return found & wanted


def _not_in_erp(store: Store, period_end: dt.date, no_date: list[Accrual] | None = None) -> list[Accrual]:
    end = period_end.isoformat()
    oldest = (period_end - dt.timedelta(days=OLD_DAYS)).isoformat()
    # Only invoices not in the ERP at the period end are read (the summary says which, D-110): in review, parked,
    # waiting for a second approval, approved and not exported, or exported after the period end (D-133).
    found = store.summary_rows(
        f"status IN (?, ?, ?) OR (status = ? AND (export_batch IS NULL OR export_batch IN ({_LATE_BATCHES})))",
        (REVIEW, PARKED, PENDING, APPROVED, end), "id, export_batch")  # fmt: skip
    late = {r["id"] for r in found if r["export_batch"] is not None}
    rows = store.invoice_columns(("id", "status", "ai_output", "final_output", "validation"),
                                 ids=[r["id"] for r in found])  # fmt: skip
    # A copy flagged as a duplicate of a bill already in the ERP (exported) or already accrued is not accrued
    # again; one flagged as already in the ERP's invoice register is in the ERP.
    counted = _exported_bills(store, rows, end)
    treatments = store.tax_treatments()
    out = []
    for r in rows:
        doc = r["final_output"] or r["ai_output"] or {}
        date = str(doc.get("invoice_date") or "").strip() if doc else ""
        if doc and not date:  # no date: when it was incurred is not known (B-474)
            if no_date is not None:
                no_date.append(Accrual(NOT_IN_ERP, doc.get("vendor_name") or "",
                                       f"Invoice {doc.get('invoice_number') or ''} (#{r['id']})", "no invoice date",
                                       "", "", round(float(doc.get("grand_total") or 0), 2), doc.get("currency") or "",
                                       "no invoice date: can't be accrued until it has one"))  # fmt: skip
            continue
        if not doc or date > end:
            continue
        flags, bill = _flags(r["validation"]), _bill(doc)
        if "DUPLICATE_IN_ERP" in flags or ("DUPLICATE_INVOICE" in flags and bill in counted):
            continue
        counted.add(bill)
        proposed = r["status"] in (REVIEW, PARKED)  # no person has approved it: its coding is a proposal
        if proposed:
            doc = _unguessed(doc, r["ai_output"] or {})
        old = date < oldest  # accrued, flagged: AP decides (B-474)
        by_gl: dict[tuple[str, str], float] = defaultdict(float)
        for e in distribution(doc, treatments):  # an invoice coded by an older version: built again
            if e.get("kind") == "expense" or e.get("non_recoverable_tax"):  # a cost; recoverable tax is not
                by_gl[(e.get("gl_code") or "", e.get("cost_center") or "")] += float(e.get("amount") or 0)
        state = {REVIEW: f"in review, {PROPOSED}", PARKED: f"parked, {PROPOSED}",
                 PENDING: "waiting for a second approval", APPROVED: "approved, not exported"}  # fmt: skip
        for (gl, cc), amount in by_gl.items():
            out.append(
                Accrual(
                    NOT_IN_ERP,
                    doc.get("vendor_name") or "",
                    f"Invoice {doc.get('invoice_number') or ''} (#{r['id']})",
                    f"invoice dated {doc.get('invoice_date')}",
                    gl,
                    cc,
                    round(amount, 2),
                    doc.get("currency") or "",
                    (EXPORTED_LATE if r["id"] in late else state[r["status"]]) + (f"; {OLD_NOTE}" if old else ""),
                    old,
                )  # fmt: skip
            )
    return out


def _expected_recurring(store: Store, period_end: dt.date, received: list[Accrual],
                        left_out: list[Accrual] | None = None) -> list[Accrual]:  # fmt: skip
    # A vendor's received, not invoiced PO lines (``received``) are what its next invoice bills: accrued once, on them.
    on_po = {vendor_key(a.vendor) for a in received}
    out = []
    for r in recurring.detected(store, today=period_end):
        if r.next_date > period_end:
            continue
        if r.vendor_key in on_po:
            if left_out is not None:
                left_out.append(Accrual(RECURRING, r.vendor_name, f"expected {r.next_date.isoformat()}",
                                        f"{r.cadence.lower()} invoice", "", "", round(r.typical_total, 2), r.currency,
                                        ON_PO))  # fmt: skip
            continue
        usage = store.vendor_gl_usage(r.vendor_key)
        out.append(
            Accrual(
                RECURRING,
                r.vendor_name,
                f"expected {r.next_date.isoformat()}",
                f"{r.cadence.lower()} invoice",
                usage[0]["gl_code"] if usage else "",
                "",
                round(r.typical_total, 2),
                r.currency,
                "estimate: the usual amount, including tax",
            )  # fmt: skip
        )
    return out


def build(store: Store, period_end: dt.date, undated: list[Accrual] | None = None,
          left_out: list[Accrual] | None = None) -> list[Accrual]:  # fmt: skip
    """The accruals at ``period_end``; ``undated``: filled with the invoices not in the ERP yet that have no invoice
    date, not accrued until they have one (B-474), read in the same go; ``left_out``: with the expected recurring
    invoices not accrued, as their vendor's received, not invoiced PO lines are (``ON_PO``)."""
    received = _received_not_invoiced(store, period_end)
    expected = _expected_recurring(store, period_end, received, left_out)
    return received + _not_in_erp(store, period_end, undated) + expected


def by_gl_columns(accruals: list[Accrual]) -> tuple[list[str], list[tuple[str, dict[str, float]]]]:
    """(currencies, [(GL account, {currency: total})]): one row per account, a column per currency (CAD first),
    largest first by its biggest amount. Currencies are never added together."""
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for a in accruals:
        totals[a.gl_code or "(no GL)"][a.currency or "CAD"] += a.amount
    currencies = sorted({c for t in totals.values() for c in t}, key=lambda c: (c != "CAD", c))
    rows = [(gl, {c: round(v, 2) for c, v in t.items()}) for gl, t in totals.items()]
    return currencies, sorted(rows, key=lambda r: -max(r[1].values()))


def to_csv(accruals: list[Accrual], period_end: dt.date) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([f"Accruals at {period_end.isoformat()}"])
    w.writerow(["Source", "Vendor", "Reference", "Description", "GL account", "Cost center", "Amount", "Currency",
                "Note"])  # fmt: skip
    for a in accruals:
        w.writerow(csv_row([a.source, a.vendor, a.reference, a.description, a.gl_code, a.cost_center,
                            f"{a.amount:.2f}", a.currency, a.note]))  # fmt: skip
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def default_period_end(today: dt.date | None = None) -> dt.date:
    """The last day of the previous month in the first week of a month (closing it), else of this month."""
    today = today or dt.date.today()
    first = today.replace(day=1)
    if today.day <= 7:
        return first - dt.timedelta(days=1)
    nxt = (first + dt.timedelta(days=32)).replace(day=1)
    return nxt - dt.timedelta(days=1)


def summary(accruals: list[Accrual]) -> dict[str, tuple[int, dict[str, float]]]:
    """{source: (lines, {currency: total})}: currencies are never added together."""
    out: dict[str, tuple[int, dict[str, float]]] = {}
    for s in SOURCES:
        totals: dict[str, float] = defaultdict(float)
        for a in accruals:
            if a.source == s:
                totals[a.currency or "CAD"] += a.amount
        out[s] = (sum(1 for a in accruals if a.source == s), {c: round(t, 2) for c, t in totals.items()})
    return out
