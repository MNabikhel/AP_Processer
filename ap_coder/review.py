"""Turning the dashboard's edited header fields and grids back into an ``InvoiceCoding``.

Kept free of Streamlit so it can be unit-tested.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
from pydantic import ValidationError

from .reference_data import UNASSIGNED
from .schema import InvoiceCoding


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or value == ""


def to_number(value: Any, default: float = 0.0) -> float:
    if is_blank(value):
        return default
    return float(str(value).replace(",", "").replace("$", "").strip())


def coding_from_inputs(
    header: dict[str, Any], lines: pd.DataFrame, taxes: pd.DataFrame, ai: dict[str, Any]
) -> tuple[InvoiceCoding | None, list[str]]:
    problems: list[str] = []
    line_items = []
    used = {int(n) for n in lines.get("line_number", []) if not is_blank(n)}
    next_no = max(used, default=0) + 1
    for row in lines.to_dict("records"):
        if all(is_blank(row.get(k)) for k in ("description", "amount")):
            continue
        number = row.get("line_number")
        if is_blank(number):
            number, next_no = next_no, next_no + 1
        taxes_applied = row.get("taxes_applied")
        amount = to_number(row.get("amount"))

        def text(field: str, default: str = "", row: dict[str, Any] = row) -> str:
            return default if is_blank(row.get(field)) else str(row[field])

        line_items.append(
            {
                "line_number": int(number),
                "description": text("description"),
                "quantity": to_number(row.get("quantity"), 1.0),
                "unit_price": to_number(row.get("unit_price"), amount),
                "amount": amount,
                "predicted_gl_code": text("predicted_gl_code", UNASSIGNED),
                "predicted_cost_center": text("predicted_cost_center"),
                "taxes_applied": []
                if taxes_applied is None or isinstance(taxes_applied, float)
                else list(taxes_applied),
                "reasoning_justification": text("reasoning_justification"),
            }
        )
    tax_lines = []
    for row in taxes.to_dict("records"):
        if is_blank(row.get("tax_type")):
            continue
        tax_lines.append(
            {
                "tax_type": row["tax_type"],
                "province": "" if is_blank(row.get("province")) or row["province"] == "—" else row["province"],
                # The dashboard edits rates as percentages (13 = 13%); the schema stores fractions.
                "rate": round(to_number(row["rate_pct"]) / 100, 6) if "rate_pct" in row else to_number(row.get("rate")),
                "taxable_amount": to_number(row.get("taxable_amount")),
                "tax_amount": to_number(row.get("tax_amount")),
            }
        )
    data = {**header, "tax_lines": tax_lines, "line_items": line_items,
            "confidence_score": ai.get("confidence_score", 0.0)}  # fmt: skip
    try:
        return InvoiceCoding.model_validate(data), []
    except ValidationError as exc:
        for err in exc.errors():
            problems.append(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}")
        return None, problems
