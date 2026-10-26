"""The math on an invoice, worked out again from its lines: the review screen shows it as the Tax check under the line
items and on the approve bar, and the AI reviewer (verifier.py) is given it."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..core.schema import InvoiceCoding
from ..core.ui import plural
from .line_tax import tax_name
from .tax import PROVINCES, REGIME, TAX_TYPES, currency_places, supply_source


def money(value: Any) -> str:
    """An amount as the review screen's boxes show it: a credit with a minus sign, -2,050.00, never (2,050.00) beside a
    box holding -2050.00 (B-805)."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"-{-amount:,.2f}" if amount < -0.004 else f"{abs(amount):,.2f}"


def _short(text: str, size: int = 28) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= size else text[: size - 1].rstrip() + "…"


def _gap_lines(lines: list[Any], gap: float, tol: float, sign: int = 1) -> list[Any]:
    return [li for li in lines if abs(sign * li.amount - gap) <= tol + 1e-9 and abs(li.amount) > 0.004]


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
        rates = sorted({round(t.rate, 6) for t in taxes})
        official = None
        if setup is not None and tax_type != "OTHER":
            where = (taxes[0].province if taxes and taxes[0].province else province) if tax_type != "GST" else ""
            if tax_type == "GST" or where in PROVINCES:
                official = setup.rates.rate_for(tax_type, where, on)
        # The rate printed is the one checked (a lawful other rate, BC PST 10% on liquor, or the same tax at two
        # rates: B-705); whether it is the official one is the rate check's (TAX_RATE_NONSTANDARD). Marked and not
        # charged: the official rate, for what would be owed.
        rate = rates[0] if len(rates) == 1 else (official if not rates else None)
        if not flagged and abs(charged) >= 0.005:
            row.update(ok=False, diff=money(charged), rate=" / ".join(f"{r * 100:.4g}%" for r in rates), expected="—",
                       detail=f"{money(charged)} charged, but no line is marked {label}: pick it as the tax code "
                              "of the lines it applies to")  # fmt: skip
            tax_rows.append(row)
            continue
        if len(rates) > 1:  # the same tax at two rates: each tax line at its rate, their bases against the lines
            expected = round(sum(round(t.taxable_amount * t.rate, places) for t in taxes), places)
            printed_base = round(sum(t.taxable_amount for t in taxes), 2)
            ok = abs(expected - charged) <= unit * (len(taxes) + 0.5) and abs(printed_base - base) <= 0.01 * n + 0.01
            parts = " + ".join(f"{money(t.taxable_amount)} × {t.rate * 100:.4g}%" for t in taxes)
            row.update(ok=ok, rate=" / ".join(f"{r * 100:.4g}%" for r in rates), expected=money(expected),
                       diff="" if ok else money(round(charged - expected, 2)),
                       detail=f"{parts} = {money(expected)} on {which} ({money(base)}); charged "
                              f"{money(charged)}")  # fmt: skip
            if not ok:
                row["why"] = (f"The {label} lines are charged on {money(printed_base)}, the lines marked {label} "
                              f"add up to {money(base)}: check the tax codes, or the vendor's {label}.")  # fmt: skip
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
            # In the size of the amounts: a credit note reverses an invoice line for line (ETA s.232, B-704).
            sign = -1 if (charged or base) < 0 else 1
            base_gap = sign * round(gap / rate, 2) if rate else 0.0  # what the lines are short of (or over)
            if abs(charged) < 0.005:
                suspects = flagged
                why = f"Marked {label}, but no {label} is charged"
            elif base_gap < 0:
                suspects = _gap_lines(flagged, -base_gap, tol, sign)
                why = f"May not carry {label}: the {label} charged adds up without it"
            else:
                unmarked = [li for li in items if tax_type not in li.taxes_applied]
                suspects = _gap_lines(unmarked, base_gap, tol, sign)
                why = f"May carry {label} too: the {label} charged adds up with it"
            if len(suspects) == 1 and abs(charged) >= 0.005:
                li = suspects[0]
                row["named"] = f"Line {li.line_number} ({_short(li.description)}, {money(li.amount)})"
                row["why"] = (f"may not carry {label}: the {label} charged adds up without it." if base_gap < 0
                              else f"may carry {label} too: the {label} charged adds up with it.")  # fmt: skip
            elif abs(charged) >= 0.005:
                suspects = []
                from .line_tax import can_explain

                explained = can_explain([li.amount for li in items], charged, rate)
                if explained:  # a choice of the lines makes it: the marking, not the vendor (B-714)
                    row["why"] = (f"The lines marked don't make the {label} charged ({money(charged)}): pick which "
                                  f"lines carry {label}.")  # fmt: skip
                elif explained is None:
                    row["why"] = (f"The lines marked don't make the {label} charged ({money(charged)}): check which "
                                  f"lines carry {label}, or the vendor's {label}.")  # fmt: skip
                else:  # no set of lines makes it: the vendor's arithmetic (B-703)
                    claim = " (tax charged in error can't be claimed)" if tax_type in ("GST", "HST", "QST") else ""
                    row["why"] = (f"The {label} charged ({money(charged)}) isn't {rate * 100:.4g}% of the lines "
                                  f"marked {label} ({money(base)} → {money(expected)}): the vendor's tax looks wrong, "
                                  f"ask for a corrected invoice{claim}.")  # fmt: skip
            row["lines"] = [li.line_number for li in suspects]
            for li in suspects:
                marks.setdefault(li.line_number, why)
        tax_rows.append(row)
    # A provincial tax due on lines and not charged on them (an out-of-province or unregistered vendor, a missed
    # line): what is to self-assess, at the official rate (not a difference: nothing was charged, B-709).
    if setup is not None and province in PROVINCES:
        from .line_tax import DUE, tax_state

        for tax_type in [t for t in ("PST", "QST") if t in REGIME.get(province, ())]:
            due = [li for li in items if tax_state(li, tax_type) == DUE]
            rate = setup.rates.rate_for(tax_type, province, on)
            if not due or not rate:
                continue
            label = tax_name(tax_type, province)
            base = round(sum(li.amount for li in due), 2)
            amount = money(round(base * rate, places))
            if tax_type == "QST":  # a registrant generally owes nothing net: not "self-assess" (B-718, B-720)
                from .tax import QST_NOT_CHARGED

                pill, detail = "QST not charged", f"{label} not charged on {money(base)} ({amount}): {QST_NOT_CHARGED}"
            else:
                pill, detail = "self-assess", f"{label} due on {money(base)} and not charged: self-assess {amount}"
            tax_rows.append({"kind": "selfassess", "what": label, "tax_type": tax_type, "ok": True, "count": len(due),
                             "of": n, "which": f"{len(due)} of {n} lines not charged" if len(due) < n
                             else "every line, not charged", "base": money(base), "rate": f"{rate * 100:.4g}%",
                             "expected": amount, "charged": money(0), "diff": "",
                             "pill": pill, "detail": detail, "lines": []})  # fmt: skip
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
