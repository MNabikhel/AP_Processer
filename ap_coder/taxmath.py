"""The math on an invoice, worked out again from its lines: the review screen shows it under the tax lines and on the
approve bar, and the AI reviewer (verifier.py) is given it."""

from __future__ import annotations

from typing import Any

from .schema import InvoiceCoding
from .ui import TAX_LABELS, money, plural


def math_check(coding: InvoiceCoding) -> dict[str, Any]:
    """Every sum on the invoice, worked out again from the lines as they are on screen: the lines add up to the
    subtotal; each tax is its rate on the lines marked with it (GST/HST/PST/QST ticked per line, so a PST-exempt line
    is simply not ticked; QST on the price before GST); subtotal + taxes = total. ``ok`` when all of it does, else
    each difference. Tax is allowed a cent of rounding, or a cent per line when the vendor rounded line by line."""
    rows: list[dict[str, Any]] = []
    items = coding.line_items
    n = len(items)
    lines_total = round(sum(li.amount for li in items), 2)
    diff = round(lines_total - coding.subtotal, 2)
    rows.append({"what": "Lines = subtotal", "ok": abs(diff) < 0.015,
                 "detail": f"{plural(n, 'line')} add up to {money(lines_total)}; the subtotal is "
                           f"{money(coding.subtotal)}",
                 "diff": money(diff) if abs(diff) >= 0.015 else ""})  # fmt: skip
    for tax_type in dict.fromkeys(t.tax_type for t in coding.tax_lines):
        taxes = [t for t in coding.tax_lines if t.tax_type == tax_type]
        charged = round(sum(t.tax_amount for t in taxes), 2)
        label = TAX_LABELS.get(tax_type, tax_type)
        if abs(charged) < 0.005:
            continue  # "GST 0.00" printed: nothing charged
        flagged = [li for li in items if tax_type in li.taxes_applied]
        if not flagged:
            rows.append({"what": label, "ok": False, "diff": money(charged),
                         "detail": f"{money(charged)} charged, but no line is marked {label}: tick it on the lines it "
                                   "applies to"})  # fmt: skip
            continue
        rates = {round(t.rate, 6) for t in taxes}
        if len(rates) != 1:  # the same tax at two rates (e.g. goods and freight): checked per tax line elsewhere
            continue
        rate = rates.pop()
        base = round(sum(li.amount for li in flagged), 2)
        expected = round(base * rate, 2)
        per_line = round(sum(round(li.amount * rate, 2) for li in flagged), 2)
        off = min(abs(expected - charged), abs(per_line - charged))
        ok = off <= 0.015 or abs(expected - charged) <= 0.01 * (len(flagged) / 2 + 1) + 1e-9
        which = "every line" if len(flagged) == n else f"{len(flagged)} of {n} lines"
        rows.append({"what": label, "ok": ok, "diff": "" if ok else money(round(charged - expected, 2)),
                     "detail": f"{which} ({money(base)}) × {rate * 100:.4g}% = {money(expected)}; charged "
                               f"{money(charged)}"})  # fmt: skip
    taxes_total = round(sum(t.tax_amount for t in coding.tax_lines), 2)
    computed = round(coding.subtotal + taxes_total, 2)
    diff = round(coding.grand_total - computed, 2)
    rows.append({"what": "Subtotal + tax = total", "ok": abs(diff) < 0.015,
                 "diff": money(diff) if abs(diff) >= 0.015 else "",
                 "detail": f"{money(coding.subtotal)} + {money(taxes_total)} = {money(computed)}; the total is "
                           f"{money(coding.grand_total)}"})  # fmt: skip
    wrong = [r for r in rows if not r["ok"]]
    return {"rows": rows, "ok": not wrong,
            "text": "All adds up" if not wrong else f"{plural(len(wrong), 'difference')}: "
            + ", ".join(r["what"] for r in wrong)}  # fmt: skip
