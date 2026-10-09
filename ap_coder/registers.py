"""The ERP's AP invoice register: invoices entered in the ERP before (or outside) AP Coder.

AP Coder only knows the invoices it has processed. Importing the ERP's invoice list (vendor, invoice
number, date, total) lets the duplicate check also catch a bill that was already entered or paid in the
ERP: DUPLICATE_IN_ERP.
"""

from __future__ import annotations

import re
from typing import Any

from .safe import parse_amount
from .statements import day_first_order, read_date

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "vendor_name": ("vendorname", "vendor", "supplier", "suppliername", "name", "fournisseur"),
    "invoice_number": ("invoicenumber", "invoice", "invoiceno", "invno", "documentnumber", "document", "docno",
                       "reference", "facture", "vendorinvoice", "supplierinvoice"),
    "invoice_date": ("invoicedate", "date", "documentdate", "postingdate"),
    "total": ("total", "invoicetotal", "amount", "grossamount", "totalamount", "montant", "invoiceamount"),
}  # fmt: skip
REQUIRED = ("vendor_name", "invoice_number", "total")


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def map_columns(headers: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    normalised = {_norm(h): h for h in headers}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in (_norm(target), *aliases):
            if alias in normalised and normalised[alias] not in found.values():
                found[target] = normalised[alias]
                break
    return found


def iso_date(text: str, day_first: bool | None = None) -> str:
    """YYYY-MM-DD when the date can be read ("2026-09-11", "2026-09-11 00:00:00", "25/09/2026", "1/5/2026 0:00"),
    else the text as it is. ``day_first``: the order the file's other dates show, for an ambiguous one."""
    return read_date(text, day_first)


def rows_from_records(records: list[dict[str, Any]], columns: dict[str, str]) -> tuple[list[dict[str, Any]], int]:
    rows, skipped = [], 0
    column = columns.get("invoice_date")
    order = day_first_order([str(rec.get(column) or "") for rec in records]) if column else None
    for rec in records:

        def get(target: str, rec: dict[str, Any] = rec) -> str:
            value = rec.get(columns[target]) if target in columns else None
            text = "" if value is None else str(value).strip()
            return "" if text.lower() in ("nan", "none") else text

        row = {"vendor_name": get("vendor_name"), "invoice_number": get("invoice_number"),
               "invoice_date": iso_date(get("invoice_date"), order), "total": parse_amount(get("total"))}  # fmt: skip
        if not row["vendor_name"] or not row["invoice_number"] or row["total"] is None:
            skipped += 1
            continue
        rows.append(row)
    return rows, skipped
