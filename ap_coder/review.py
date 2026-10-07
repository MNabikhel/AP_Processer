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
    header: dict[str, Any],
    lines: pd.DataFrame,
    taxes: pd.DataFrame,
    ai: dict[str, Any],
    default_cost_center: str = "",
) -> tuple[InvoiceCoding | None, list[str]]:
    """Rebuild the coding from the dashboard's form and grids.

    Blank GL cells become ``UNASSIGNED``; blank cost centers become ``default_cost_center``
    (``UNASSIGNED`` when cost centers are configured, so the check says what is missing).
    """
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
                "predicted_cost_center": text("predicted_cost_center", default_cost_center),
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


def split_line(lines: pd.DataFrame, line_number: int, parts: list[tuple[str, str, float]]) -> pd.DataFrame:
    """Replace one line by several, one per (GL, cost center, percent); percents must add up to 100.

    Quantities are split in proportion at the same unit price (so PO matching still adds up), the last
    part takes the rounding, and each part keeps the line's description and taxes. The first part keeps
    the line number and its place; the others are added at the end, numbered after the last line.
    """
    if any(not isinstance(p, (int, float)) or not math.isfinite(p) for _, _, p in parts):
        raise ValueError("every part needs a percentage")
    if not parts or abs(sum(p for _, _, p in parts) - 100) > 0.01:
        raise ValueError("the percentages must add up to 100")
    if any(p <= 0 for _, _, p in parts):
        raise ValueError("every part needs a percentage above 0")
    numbers = pd.to_numeric(lines["line_number"], errors="coerce")
    matches = lines.index[numbers == line_number].tolist()
    if not matches:
        raise ValueError(f"there is no line {line_number}")
    row = lines.loc[matches[0]].to_dict()
    amount = to_number(row.get("amount"))
    quantity = to_number(row.get("quantity"), 1.0)
    next_no = int(numbers.max()) + 1
    new_rows, allocated = [], 0.0
    for i, (gl, cc, pct) in enumerate(parts):
        last = i == len(parts) - 1
        part_amount = round(amount - allocated, 2) if last else round(amount * pct / 100, 2)
        allocated += part_amount
        new = dict(row)
        new.update(
            line_number=line_number if i == 0 else next_no + i - 1,
            quantity=round(quantity * pct / 100, 6),
            amount=part_amount,
            predicted_gl_code=gl or row.get("predicted_gl_code"),
            predicted_cost_center=cc if cc is not None else row.get("predicted_cost_center"),
            description=f"{row.get('description') or ''} ({pct:g}%)",
            reasoning_justification=f"Split from line {line_number} ({pct:g}%).",
        )
        if quantity and new["quantity"]:
            new["unit_price"] = round(part_amount / new["quantity"], 6)
        new_rows.append(new)
    # The first part replaces the line where it is; the others go last, keeping every other line's number
    # (renumbering would pair lines wrongly with the AI's when learning from the approval).
    position = lines.index.get_loc(matches[0])
    first = pd.DataFrame([new_rows[0]])
    return pd.concat(
        [lines.iloc[:position], first, lines.iloc[position + 1 :], pd.DataFrame(new_rows[1:])], ignore_index=True
    )
