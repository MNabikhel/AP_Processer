"""Teach the AI from the ERP's past AP coding before the pilot starts.

An export of posted AP invoice lines (vendor, line description, GL account, optional cost center,
amount and date) is stored in the learning memory as *history*: the AI sees how each vendor's lines were
coded before, from the very first invoice. History is not a reviewer decision, so it never counts in the
accuracy figures, and it can be forgotten in one click.
"""

from __future__ import annotations

import re
from typing import Any

from .safe import parse_amount

HISTORY = "history"
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "vendor_name": ("vendorname", "vendor", "supplier", "suppliername", "fournisseur", "name"),
    "description": ("description", "linedescription", "itemdescription", "memo", "text", "lineitem", "details"),
    "gl_code": ("glcode", "gl", "glaccount", "account", "accountcode", "expenseaccount", "costcode"),
    "cost_center": ("costcenter", "costcentre", "cc", "department", "dept"),
    "amount": ("amount", "netamount", "lineamount", "debit", "total", "montant"),
    "date": ("date", "invoicedate", "postingdate", "glDate", "documentdate"),
}
REQUIRED = ("vendor_name", "description", "gl_code")


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def map_columns(headers: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    normalised = {_norm(h): h for h in headers}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in (_norm(target), *(_norm(a) for a in aliases)):
            if alias in normalised and normalised[alias] not in found.values():
                found[target] = normalised[alias]
                break
    return found


def rows_from_records(records: list[dict[str, Any]], columns: dict[str, str]) -> tuple[list[dict[str, Any]], int]:
    """History rows (vendor_name, description, gl_code, cost_center, amount, date). Returns (rows, skipped)."""
    rows, skipped = [], 0
    for rec in records:

        def get(target: str, rec: dict[str, Any] = rec) -> str:
            value = rec.get(columns[target]) if target in columns else None
            text = "" if value is None else str(value).strip()
            return "" if text.lower() in ("nan", "none") else text

        row = {k: get(k) for k in COLUMN_ALIASES}
        if not all(row[k] for k in REQUIRED):
            skipped += 1
            continue
        if re.fullmatch(r"\d+\.0", row["gl_code"]):
            row["gl_code"] = row["gl_code"][:-2]
        row["amount"] = parse_amount(row["amount"])
        row["date"] = row["date"][:10]
        rows.append(row)
    return rows, skipped
