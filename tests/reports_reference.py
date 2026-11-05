"""The big reports as they were before they were made light for a year of invoices (D-156): every invoice, PO or
event they needed read and decoded on each call. The tests check the reports' results against these on the same
data; the bodies are the old code as it was (only renamed)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ap_coder.coding.po import CLOSED, billed_by_line, po_label
from ap_coder.core.store import APPROVED, Store
from ap_coder.erp.accruals import RECEIVED, Accrual


def one_person(store: Store, first: str, last: str, limit: float, rates: dict[str, float]) -> list[dict[str, Any]]:
    """``controls.one_person``: every approved invoice's coding decoded and checked."""
    return [i for i in store.invoice_columns(("id", "reviewed_at", "second_reviewer", "final_output"), APPROVED)
            if not i["second_reviewer"] and first <= (i["reviewed_at"] or "") < last
            and store.second_approval_reason(i["final_output"] or {}, limit, rates=rates)]  # fmt: skip


def received_not_invoiced(store: Store, period_end: dt.date) -> list[Accrual]:
    """``accruals._received_not_invoiced``: each PO and its invoices read in turn, each line of the vendor's invoices
    matched again for every PO of the vendor."""
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
