# ruff: noqa: E501  (label and pattern tables read best one per line)
"""The totals solver: subtotal, taxes and total that add up."""

from __future__ import annotations

from itertools import product

from ..types import (
    AMOUNT_FIELDS,
    EITHER_TAX,
    TAKEN_OFF,
    TWO_SUBTOTALS,
    Reading,
)
from ._common import (
    _TAX_FIELDS,
    PREPAID,
)

_RATES = {
    "gst_amount": (0.05,),
    "hst_amount": (0.13, 0.14, 0.15),
    "pst_amount": (0.06, 0.07, 0.08),
    "qst_amount": (0.09975,),
}


def _where(r: Reading) -> tuple:
    b = r.boxes[0] if r.boxes else None
    return (b.page, round(b.x0, 3), round(b.y0, 3)) if b else (id(r),)


def _summed(readings: list[Reading], same_sign: bool = True) -> list[Reading]:
    """Two amounts of one kind on rows of their own, with no total of them printed: a tax charged at two rates
    ("USt. 7% 19,25", "USt. 19% 37,62"), or charges and discounts ("Gesamtbetrag der Zuschläge 5,80", "... der
    Abschläge -9,07"). Their sum, as one more option for the solver to try."""
    out = []
    for i, a in enumerate(readings):
        for b in readings[i + 1 :]:
            if a.boxes and b.boxes and _where(a)[:1] == _where(b)[:1] and abs(a.boxes[0].cy - b.boxes[0].cy) > 0.005:
                if a.value and b.value and (not same_sign or (a.value > 0) == (b.value > 0)):
                    out.append(Reading(a.field, round(a.value + b.value, 2), f"{a.raw} + {b.raw}",
                                       [*a.boxes, *b.boxes], 0.9 * min(a.score, b.score), a.method + "+summed"))  # fmt: skip
    return out[:3]


def _solve_totals(cands: dict[str, list[Reading]], line_sum: float | None) -> dict[str, list[Reading]]:
    """Re-rank amount candidates so that the subtotal, taxes and total that add up come first."""
    top = {f: sorted(cands.get(f, []), key=lambda r: -r.score)[:4] for f in AMOUNT_FIELDS}
    subs = top["subtotal"] or [None]
    totals = top["grand_total"] or [None]
    tax_options = {f: [*top[f], None] for f in _TAX_FIELDS}
    charge_options = [*top["other_charges"][:2], *_summed(top["other_charges"][:3], same_sign=False), None]
    # A sales tax that is no GST/HST/PST/QST ("WA Sales Tax 10.35%"): only its total is printed.
    total_tax_options = [*top["tax_total"][:4], *_summed(top["tax_total"]), None]
    best = None
    any_tax = any(top[f] for f in (*_TAX_FIELDS, "tax_total"))
    balanced: set[float] = set()  # the subtotals some combination adds up with
    for sub, total, charge, total_tax in product(subs, totals, charge_options, total_tax_options):
        if sub is not None and total is not None and sub.boxes and _where(sub) == _where(total):
            continue  # one printed amount read under two labels ("Net Total"): it is not both
        extra = charge.value if charge else 0.0
        for taxes in product(*tax_options.values()):
            present = [t for t in taxes if t is not None]
            if len({_where(t) for t in present}) < len(present):
                continue  # one printed amount ("GST/HST") is one tax
            if present and total_tax is not None:
                continue  # the total tax stands in only when no component tax is read
            if charge is not None and PREPAID in charge.method:
                if present or total_tax is not None:
                    continue  # a deposit taken off a taxed total may be off before or after the tax: not settled here
                if sub is not None and any(
                    t is not None
                    and t.boxes
                    and sub.boxes
                    and t.boxes[0].page == sub.boxes[0].page
                    and abs(sub.value - t.value) <= 0.011
                    for t in totals
                ):
                    continue  # "Total 2,344.90 | Deposit received | Balance due": the invoice's total is printed
            score = (sub.score if sub else 0) + (total.score if total else 0) + sum(t.score for t in present)
            score += (charge.score if charge else 0.0) + (total_tax.score if total_tax else 0.0)
            bonus = 0.0
            if sub and total:
                tax_sum = sum(t.value for t in present) + (total_tax.value if total_tax else 0.0)
                if abs(sub.value + extra + tax_sum - total.value) <= 0.011:
                    # Subtotal = total with no tax is weaker evidence: one number printed twice.
                    taxed = len(present) + (total_tax is not None)
                    if taxed:
                        bonus += 3.0 + 0.5 * taxed
                    elif not any_tax:
                        bonus += 2.5  # one number printed twice: weaker, and no proof at all when a tax is printed
                elif present and abs(sub.value - total.value) <= 0.011:
                    bonus -= 1.0  # a total equal to the subtotal while taxes are printed: wrong total
            if sub and line_sum is not None and abs(sub.value - line_sum) <= 0.011:
                bonus += 1.0
            if sub and sub.value:
                for f, t in zip(tax_options, taxes, strict=True):
                    if t is not None and any(
                        abs(t.value - r * base) <= max(0.02, 0.01 * abs(t.value))
                        for r in _RATES[f]
                        for base in {sub.value, sub.value + extra}  # taxed with or without the freight
                    ):
                        bonus += 0.3
            if sub and bonus >= 3.0:
                balanced.add(round(sub.value, 2))
            if best is None or score + bonus > best[0]:
                chosen_taxes = dict(zip(tax_options, taxes, strict=True))
                best = (
                    score + bonus,
                    sub,
                    total,
                    {**chosen_taxes, "other_charges": charge, "tax_total": total_tax},
                    bonus,
                )
    if best is None:
        return cands
    _, sub, total, taxes, bonus = best
    chosen = {"subtotal": sub, "grand_total": total, **taxes}
    if sub is None:
        for f, other in (("gst_amount", "hst_amount"), ("hst_amount", "gst_amount")):
            r = taxes.get(f)
            if r is not None and any(_where(x) == _where(r) for x in cands.get(other, [])):
                # "GST/HST 16,874.00" with no subtotal read: no rate says which of the two it is.
                r.method += EITHER_TAX
    if total is not None and taxes.get("other_charges") is not None and PREPAID in taxes["other_charges"].method:
        total.method += TAKEN_OFF  # the amount due net of a deposit: whether net or gross is posted is AP's call
    out = dict(cands)
    taken = {_where(r) for f, r in chosen.items() if r is not None and f in _TAX_FIELDS}
    for f, r in chosen.items():
        if r is None:
            if bonus >= 3.0 and (f in _TAX_FIELDS or f == "other_charges"):
                out[f] = []  # the totals add up without this tax: what was read for it is not a tax
            elif f in _TAX_FIELDS:
                out[f] = [x for x in cands.get(f, []) if _where(x) not in taken]
            continue
        if bonus >= 3.0:
            r.score = min(1.0, r.score + 0.15)
            r.method += "+adds-up"
            if f == "subtotal" and len(balanced) > 1:
                # "Zwischensumme 202,76 ... Rechnungssumme Netto 193,83": the totals add up with either, with the
                # discount and freight between them or without. Which one is the subtotal is AP's convention.
                r.method += TWO_SUBTOTALS
        rest = [x for x in cands.get(f, []) if x is not r]
        out[f] = [r, *sorted(rest, key=lambda x: -x.score)]
    return out
