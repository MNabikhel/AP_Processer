"""The totals solver works out a few of the combinations it once tried one by one (480,000 at most, 1.3 s), and gives
the same answers: the same readings first, the same scores and methods, on every kind of totals a reading can give
(D-170). ``_solve_totals_as_before`` is the solver as it was (fd5d166), kept here to compare with."""

import copy
import random
import time
from itertools import product

import pytest

from ap_coder.capture.reader._common import _TAX_FIELDS, PREPAID
from ap_coder.capture.reader.totals import _RATES, _solve_totals, _summed, _where
from ap_coder.capture.types import AMOUNT_FIELDS, EITHER_TAX, TAKEN_OFF, TWO_SUBTOTALS, Box, Reading


def _solve_totals_as_before(cands: dict[str, list[Reading]], line_sum: float | None) -> dict[str, list[Reading]]:
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


def _case(rng: random.Random) -> tuple[dict[str, list[Reading]], float | None]:
    """Amount readings as a page gives them: a subtotal, taxes at their rates (some read twice, under GST and HST, from
    one printed amount), a charge or a deposit, a total tax, totals that add up or don't, and wrong readings beside."""
    y = [0.0]

    def at(page: int = 1) -> list[Box]:
        y[0] += 0.013
        return [Box(page, 0.6, y[0], 0.8, y[0] + 0.01)]

    def reading(field: str, value: float, method: str = "label-right", boxes: list[Box] | None = None) -> Reading:
        return Reading(field, round(value, 2), f"{value:.2f}", at() if boxes is None else boxes,
                       rng.choice([0.4, 0.55, 0.7, 0.85, 0.9, rng.uniform(0.2, 0.99)]), method)  # fmt: skip

    sub = round(rng.choice([rng.uniform(5, 500), rng.uniform(500, 20000)]), 2)
    charge = round(rng.choice([0.0, 0.0, rng.uniform(5, 80), -rng.uniform(5, 50)]), 2)
    taxes = {f: round((sub + charge * rng.choice([0, 1])) * rng.choice(_RATES[f]), 2) for f in _TAX_FIELDS}
    kept = [f for f in _TAX_FIELDS if rng.random() < 0.4]
    total = round(sub + charge + sum(taxes[f] for f in kept) + rng.choice([0.0, 0.0, 0.0, 0.01, rng.uniform(-50, 50)]),
                  2)  # fmt: skip
    cands: dict[str, list[Reading]] = {f: [] for f in AMOUNT_FIELDS}
    if rng.random() < 0.85:  # (else no subtotal read: GST and HST from one amount stay either)
        cands["subtotal"].append(reading("subtotal", sub))
    cands["grand_total"].append(reading("grand_total", total))
    if cands["subtotal"] and rng.random() < 0.2:  # one printed amount under two labels ("Net Total")
        cands["grand_total"].append(reading("grand_total", sub, boxes=cands["subtotal"][0].boxes))
    for f in kept:
        cands[f].append(reading(f, taxes[f]))
    if "gst_amount" in kept and rng.random() < 0.3:  # "GST/HST 16,874.00": read under both
        cands["hst_amount"].append(reading("hst_amount", taxes["gst_amount"], boxes=cands["gst_amount"][0].boxes))
    if charge:
        method = "label-right" + (PREPAID if rng.random() < 0.3 else "")
        cands["other_charges"].append(reading("other_charges", charge, method))
    if kept and rng.random() < 0.3:
        cands["tax_total"].append(reading("tax_total", sum(taxes[f] for f in kept)))
    for f in AMOUNT_FIELDS:  # the other amounts on the page, some read under each label
        for _ in range(rng.choice([0, 0, 1, 2, 3, 5])):
            value = rng.choice([sub, total, rng.uniform(1, 30000), rng.choice(list(taxes.values())), charge or 9.99])
            cands[f].append(reading(f, value))
    for f in list(cands):
        if not cands[f]:
            del cands[f]
    line_sum = rng.choice([None, sub, round(sub + 1.5, 2), round(sub - charge, 2)])
    return cands, line_sum


def _shown(out: dict[str, list[Reading]]) -> dict[str, list[tuple]]:
    return {f: [(r.field, r.value, r.raw, r.boxes, r.score, r.method) for r in rs] for f, rs in out.items()}


@pytest.mark.parametrize("seed", range(2))
def test_the_pruned_solver_answers_as_every_combination_tried_did(seed):
    rng = random.Random(seed)
    for _ in range(150):
        cands, line_sum = _case(rng)
        before = _solve_totals_as_before(copy.deepcopy(cands), line_sum)
        after = _solve_totals(copy.deepcopy(cands), line_sum)
        assert _shown(after) == _shown(before)


def test_the_solvers_worst_case_takes_milliseconds_not_a_second():
    """Every amount field with all the readings it keeps (four each, three charges and sums): 480,000 combinations,
    1.3 s each time a page was read; pruned, 12 ms on a laptop (0.25 s allowed here)."""
    rng = random.Random(7)
    y = 0.0
    cands = {}
    for f in AMOUNT_FIELDS:
        cands[f] = []
        for _ in range(5):
            y += 0.011
            value = round(rng.uniform(1, 6000), 2)
            cands[f].append(Reading(f, value, str(value), [Box(1, 0.6, y, 0.8, y + 0.01)], rng.uniform(0.3, 0.95),
                                    "label-right"))  # fmt: skip
    sub = cands["subtotal"][0].value
    cands["gst_amount"][0].value = round(sub * 0.05, 2)
    cands["grand_total"][0].value = round(sub * 1.05, 2)
    before = _solve_totals_as_before(copy.deepcopy(cands), 1000.0)
    started = time.perf_counter()
    after = _solve_totals(copy.deepcopy(cands), 1000.0)
    took = time.perf_counter() - started
    assert _shown(after) == _shown(before)
    assert took < 0.25
