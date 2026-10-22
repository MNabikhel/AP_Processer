"""The math on an invoice, worked out again from its lines: the review screen shows it as the Tax check under the line
items and on the approve bar, and the AI reviewer (verifier.py) is given it."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..core.schema import InvoiceCoding
from ..core.ui import money, plural
from .line_tax import tax_name
from .tax import PROVINCES, TAX_TYPES, currency_places, supply_source


def _short(text: str, size: int = 28) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= size else text[: size - 1].rstrip() + "…"


def _gap_lines(lines: list[Any], gap: float, tol: float) -> list[Any]:
    return [li for li in lines if abs(li.amount - gap) <= tol + 1e-9 and abs(li.amount) > 0.004]


def math_check(coding: InvoiceCoding, setup: Any = None) -> dict[str, Any]:
    """Every sum on the invoice, worked out again from the lines as they are on screen: the lines add up to the
    subtotal; each tax is its rate on the lines whose tax code carries it (a PST-exempt line is "GST only"; QST on the
    price before GST); subtotal + taxes = total. ``ok`` when all of it does, else each difference. Tax is allowed a
    cent of rounding, or a cent per line when the vendor rounded line by line (a whole yen in a currency without
    cents).

    With ``setup`` (``TaxSetup``), each tax is worked out at the official rate in force on the invoice's date where
    it is supplied (D-61), the Tax check table's "expected"; without it, at the rate printed. Each row of a tax
    (``kind`` "tax") says the lines marked and their base, the rate, what is expected, what is charged and the
    difference, and ``lines``: the line numbers to look at when it does not add up (the line whose amount is the
    difference, else the lines marked with it). ``marks``: {line number: why} for the review screen to highlight."""
    rows: list[dict[str, Any]] = []
    items = coding.line_items
    n = len(items)
    places = currency_places(coding.currency)
    unit = 10.0**-places
    province = supply_source(coding, setup)[0] if setup is not None else coding.ship_to_province
    try:
        on = dt.date.fromisoformat(coding.invoice_date)
    except (TypeError, ValueError):
        on = None
    lines_total = round(sum(li.amount for li in items), 2)
    diff = round(lines_total - coding.subtotal, 2)
    lines_row = {"kind": "lines", "what": "Lines = subtotal", "ok": abs(diff) < 0.015,
                 "detail": f"{plural(n, 'line')} add up to {money(lines_total)}; the subtotal is "
                           f"{money(coding.subtotal)}",
                 "diff": money(diff) if abs(diff) >= 0.015 else "", "lines_total": money(lines_total),
                 "subtotal": money(coding.subtotal), "lines": []}  # fmt: skip
    taxes_total = round(sum(t.tax_amount for t in coding.tax_lines), 2)
    if not lines_row["ok"] and abs(taxes_total) > 0.004 and abs(lines_total - coding.grand_total) < 0.015:
        lines_row["hint"] = (
            "The lines add up to the total: their prices seem to include the tax. Enter them before tax."
        )
    marks: dict[int, str] = {}
    charged_types = [
        t for t in TAX_TYPES if any(x.tax_type == t and abs(x.tax_amount) >= 0.005 for x in coding.tax_lines)
    ]
    marked_types = [t for t in TAX_TYPES if any(t in li.taxes_applied for li in items)]
    tax_rows = []
    for tax_type in [t for t in TAX_TYPES if t in charged_types or t in marked_types]:
        taxes = [t for t in coding.tax_lines if t.tax_type == tax_type]
        charged = round(sum(t.tax_amount for t in taxes), 2)
        label = tax_name(tax_type, province)
        flagged = [li for li in items if tax_type in li.taxes_applied]
        base = round(sum(li.amount for li in flagged), 2)
        which = "every line" if flagged and len(flagged) == n else f"{len(flagged)} of {n} lines"
        row: dict[str, Any] = {"kind": "tax", "what": label, "tax_type": tax_type, "count": len(flagged), "of": n,
                               "which": which, "base": money(base), "charged": money(charged), "lines": []}  # fmt: skip
        rates = {round(t.rate, 6) for t in taxes}
        rate = None
        official = None
        if setup is not None and tax_type != "OTHER":
            where = (taxes[0].province if taxes and taxes[0].province else province) if tax_type != "GST" else ""
            if tax_type == "GST" or where in PROVINCES:
                official = setup.rates.rate_for(tax_type, where, on)
        if official is not None:
            rate = official
        elif len(rates) == 1:
            rate = rates.pop()
        elif len(rates) > 1:  # the same tax at two rates (goods and freight): checked per tax line elsewhere
            continue
        if not flagged and abs(charged) >= 0.005:
            row.update(ok=False, diff=money(charged), rate=f"{rate * 100:.4g}%" if rate else "", expected="—",
                       detail=f"{money(charged)} charged, but no line is marked {label}: pick it as the tax code "
                              "of the lines it applies to")  # fmt: skip
            tax_rows.append(row)
            continue
        if rate is None:  # marked, not charged, no rate known (a foreign tax): nothing to expect
            row.update(ok=abs(charged) < 0.005 and not flagged, diff="", rate="", expected="—",
                       detail=f"{which} marked {label}; nothing charged")  # fmt: skip
            tax_rows.append(row)
            continue
        # Rounded as the tax checks round it (tax.check_taxes): to cents, or to whole yen and the like.
        expected = round(base * rate, places)
        per_line = round(sum(round(li.amount * rate, places) for li in flagged), places)
        off = min(abs(expected - charged), abs(per_line - charged))
        ok = off <= 1.5 * unit or abs(expected - charged) <= unit * (len(flagged) / 2 + 1) + 1e-9
        gap = round(charged - expected, 2)
        row.update(ok=ok, rate=f"{rate * 100:.4g}%", expected=money(expected), diff="" if ok else money(gap),
                   detail=f"{which} ({money(base)}) × {rate * 100:.4g}% = {money(expected)}; charged "
                          f"{money(charged)}")  # fmt: skip
        if not ok:
            tol = 0.01 * n + 0.01
            base_gap = round(gap / rate, 2) if rate else 0.0  # what the lines are short of (or over) the tax's base
            if abs(charged) < 0.005:
                suspects = flagged
                why = f"Marked {label}, but no {label} is charged"
            elif base_gap < 0:
                suspects = _gap_lines(flagged, -base_gap, tol) or flagged
                why = f"May not carry {label}: the {label} charged adds up without it"
            else:
                unmarked = [li for li in items if tax_type not in li.taxes_applied]
                suspects = _gap_lines(unmarked, base_gap, tol) or flagged
                why = f"May carry {label} too: the {label} charged adds up with it"
            row["lines"] = [li.line_number for li in suspects]
            if suspects and len(suspects) == 1 and suspects[0] in items:
                li = suspects[0]
                row["named"] = f"Line {li.line_number} ({_short(li.description)}, {money(li.amount)})"
                row["why"] = (f"may not carry {label}: the {label} charged adds up without it." if base_gap < 0
                              else f"may carry {label} too: the {label} charged adds up with it.")  # fmt: skip
            for li in suspects:
                marks.setdefault(li.line_number, why if len(suspects) < len(flagged) or not flagged else
                                 f"Check the tax code: the {label} does not add up")  # fmt: skip
        tax_rows.append(row)
    rows.append(lines_row)
    rows += tax_rows
    computed = round(coding.subtotal + taxes_total, 2)
    diff = round(coding.grand_total - computed, 2)
    rows.append({"kind": "total", "what": "Subtotal + tax = total", "ok": abs(diff) < 0.015,
                 "diff": money(diff) if abs(diff) >= 0.015 else "", "subtotal": money(coding.subtotal),
                 "tax": money(taxes_total), "computed": money(computed), "total": money(coding.grand_total),
                 "detail": f"{money(coding.subtotal)} + {money(taxes_total)} = {money(computed)}; the total is "
                           f"{money(coding.grand_total)}", "lines": []})  # fmt: skip
    wrong = [r for r in rows if not r["ok"]]
    return {"rows": rows, "ok": not wrong, "marks": marks,
            "text": "All adds up" if not wrong else f"{plural(len(wrong), 'difference')}: "
            + ", ".join(r["what"] for r in wrong)}  # fmt: skip
