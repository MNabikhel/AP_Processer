"""The math on an invoice, worked out again from its lines: the review screen shows it as the Tax check under the line
items and on the approve bar, and the AI reviewer (verifier.py) is given it.

Entry points: ``math_check``; ``invoice_taxes`` (``line_taxes``, ``tax_check_lines``): the tax on each line, tax by
tax, and each tax's sum in a sentence, from the same numbers; ``money`` writes an amount as the review screen's boxes
do.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from ..core.schema import InvoiceCoding
from ..core.ui import plural
from .line_tax import code_label, subset_solutions, tax_code_of, tax_name
from .money import (
    CENT,
    EXACT,
    TAX_UNITS,
    add_up,
    cash_rounded,
    half_up,
    in_cents,
    nil,
    per_line,
    split,
    tax_right,
    times,
    within,
)
from .tax import (
    EXPENSE_SEPARATE,
    EXPENSE_TO_LINE,
    MEALS_RESTRICTED,
    PROVINCES,
    RECOVERABLE,
    REGIME,
    TAX_TYPES,
    TaxSetup,
    currency_places,
    gap_lines,
    is_meals,
    is_tip,
    meals_parts,
    short,
    supply_source,
    tax_shares,
)


def money(value: Any) -> str:
    """An amount as the review screen's boxes show it: a credit with a minus sign, -2,050.00, never (2,050.00) beside a
    box holding -2050.00 (B-805)."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"-{-amount:,.2f}" if amount < -0.004 else f"{abs(amount):,.2f}"


@dataclass
class _TaxNumbers:
    """One tax as its row of the Tax check works it out: its tax lines, the lines marked with it and what they add up
    to, what is charged, the rate checked and what it gives (None: no rate to check, nothing to expect)."""

    taxes: list[Any]
    flagged: list[Any]
    base: float
    charged: float
    rate: float | None = None
    expected: float | None = None


@dataclass
class _Sums:
    """One invoice's math as ``math_check`` works it out: what every row reads, and the lines to highlight."""

    coding: InvoiceCoding
    setup: Any
    items: list[Any]
    places: int
    unit: float  # a cent, or a whole yen in a currency without cents
    province: str
    on: dt.date | None
    marks: dict[int, str] = field(default_factory=dict)
    # What the rows say in words, kept as numbers (``invoice_taxes``): each tax's row; the lines a provincial tax is
    # due on and not charged, its rate and amount; whether the lines' prices seem to include the tax.
    numbers: dict[str, _TaxNumbers] = field(default_factory=dict)
    due: dict[str, tuple[list[Any], float, float]] = field(default_factory=dict)
    tax_included: bool = False

    @property
    def n(self) -> int:
        return len(self.items)


def math_check(coding: InvoiceCoding, setup: Any = None) -> dict[str, Any]:
    """Every sum on the invoice, worked out again from the lines as they are on screen: the lines add up to the
    subtotal; each tax is its rate on the lines whose tax code carries it (a PST-exempt line is "GST only"; QST on the
    price before GST); subtotal + the tax total = total. ``ok`` when all of it does, else each difference. Tax is
    allowed a cent of rounding, or a cent per line when the vendor rounded line by line (a whole yen in a currency
    without cents). What counts as equal is the findings' (``money``, D-183): a row is right exactly when
    ``validate_coding`` and ``check_taxes`` say nothing of its sum (the lines row: to the cent when the posting can't
    balance, B-1910).

    With ``setup`` (``TaxSetup``), each tax is worked out at the official rate in force on the invoice's date where
    it is supplied (D-61), the Tax check table's "expected"; without it, at the rate printed. Each row of a tax
    (``kind`` "tax") says the lines marked and their base, the rate, what is expected, what is charged and the
    difference, and ``lines``: the line numbers to look at when it does not add up (the line whose amount is the
    difference, else the lines marked with it). ``marks``: {line number: why} for the review screen to highlight."""
    sums, rows = _check(coding, setup)
    wrong = [r for r in rows if not r["ok"]]
    return {"rows": rows, "ok": not wrong, "marks": sums.marks,
            "text": "All adds up" if not wrong else f"{plural(len(wrong), 'difference')}: "
            + ", ".join(r["what"] for r in wrong)}  # fmt: skip


def _check(coding: InvoiceCoding, setup: Any = None) -> tuple[_Sums, list[dict[str, Any]]]:
    """``math_check``'s sums and its rows."""
    places = currency_places(coding.currency)
    try:
        on = dt.date.fromisoformat(coding.invoice_date)
    except (TypeError, ValueError):
        on = None
    sums = _Sums(coding, setup, coding.line_items, places, 10.0**-places,
                 supply_source(coding, setup)[0] if setup is not None else coding.ship_to_province, on)  # fmt: skip
    taxes_total = add_up(t.tax_amount for t in coding.tax_lines)
    charged_types = [t for t in TAX_TYPES if any(x.tax_type == t and not nil(x.tax_amount) for x in coding.tax_lines)]
    marked_types = [t for t in TAX_TYPES if any(t in li.taxes_applied for li in sums.items)]
    tax_rows = [_tax_row(sums, t) for t in TAX_TYPES if t in charged_types or t in marked_types]
    return sums, [_lines_row(sums, taxes_total), *tax_rows, *_self_assess_rows(sums), _total_row(coding, taxes_total)]


def _lines_row(sums: _Sums, taxes_total: float) -> dict[str, Any]:
    """The lines add up to the subtotal: within a cent a line and one more, as SUBTOTAL_MISMATCH (B-1866), but to the
    cent when the posting can't balance (POSTING_UNBALANCED: the lines and the tax lines as posted are not the total),
    so that the row points at a line read off (B-1910)."""
    coding = sums.coding
    lines_total = add_up(li.amount for li in sums.items)
    diff = half_up(lines_total - coding.subtotal)
    ok = within(diff, 0, per_line(sums.n))
    if ok and sums.items and not within(diff, 0, EXACT):  # a cent a line: right when the posting balances
        posted = add_up([*(half_up(li.amount) for li in sums.items),
                         *(half_up(t.tax_amount) for t in coding.tax_lines if not nil(t.tax_amount))])  # fmt: skip
        ok = within(posted, coding.grand_total, EXACT)
    row = {"kind": "lines", "what": "Lines = subtotal", "ok": ok,
           "detail": f"{plural(sums.n, 'line')} add up to {money(lines_total)}; the subtotal is "
                     f"{money(coding.subtotal)}",
           "diff": money(diff) if not ok else "", "lines_total": money(lines_total),
           "subtotal": money(coding.subtotal), "lines": []}  # fmt: skip
    if not row["ok"] and not nil(taxes_total) and within(lines_total, coding.grand_total, CENT):
        sums.tax_included = True
        row["hint"] = "The lines add up to the total: their prices seem to include the tax. Enter them before tax."
    return row


def _tax_row(sums: _Sums, tax_type: str) -> dict[str, Any]:
    """One tax: its rate on the lines marked with it, against what is charged."""
    items, n, setup = sums.items, sums.n, sums.setup
    taxes = [t for t in sums.coding.tax_lines if t.tax_type == tax_type]
    # A tax line of nothing beside lines that charge the tax (GST 0.00 on a zero-rated line) is on no line (B-2503)
    taxes = [t for t in taxes if not nil(t.tax_amount)] or taxes
    charged = add_up(t.tax_amount for t in taxes)
    label = tax_name(tax_type, sums.province)
    flagged = [li for li in items if tax_type in li.taxes_applied]
    base = add_up(li.amount for li in flagged)
    which = "every line" if flagged and len(flagged) == n else f"{len(flagged)} of {n} lines"
    found = sums.numbers[tax_type] = _TaxNumbers(taxes, flagged, base, charged)
    row: dict[str, Any] = {"kind": "tax", "what": label, "tax_type": tax_type, "count": len(flagged), "of": n,
                           "which": which, "base": money(base), "charged": money(charged), "lines": []}  # fmt: skip
    rates = sorted({round(t.rate, 6) for t in taxes})
    official = None
    if setup is not None and tax_type != "OTHER":
        where = (taxes[0].province if taxes and taxes[0].province else sums.province) if tax_type != "GST" else ""
        if tax_type == "GST" or where in PROVINCES:
            official = setup.rates.rate_for(tax_type, where, sums.on)
    # The rate printed is the one checked (a lawful other rate, BC PST 10% on liquor, or the same tax at two rates:
    # B-705); whether it is the official one is the rate check's (TAX_RATE_NONSTANDARD). Marked and not charged: the
    # official rate, for what would be owed.
    rate = found.rate = rates[0] if len(rates) == 1 else (official if not rates else None)
    if not flagged and not nil(charged):
        row.update(ok=False, diff=money(charged), rate=" / ".join(f"{r * 100:.4g}%" for r in rates), expected="—",
                   detail=f"{money(charged)} charged, but no line is marked {label}: pick it as the tax code "
                          "of the lines it applies to")  # fmt: skip
        return row
    if len(rates) > 1:
        return _two_rates_row(sums, row, taxes, rates, charged, base, label)
    if rate is None:  # marked, not charged, no rate known (a foreign tax): nothing to expect
        row.update(ok=nil(charged) and not flagged, diff="", rate="", expected="—",
                   detail=f"{which} marked {label}; nothing charged")  # fmt: skip
        return row
    # Rounded as the tax checks round it (tax.check_taxes): to cents, or to whole yen and the like; right within a unit
    # either way, as TAX_CALC_MISMATCH (B-1867).
    places = sums.places
    expected = found.expected = times(base, rate, places)
    line_by_line = add_up((times(li.amount, rate, places) for li in flagged), places)
    ok = tax_right(charged, (expected, line_by_line), places)
    gap = half_up(charged - expected)
    row.update(ok=ok, rate=f"{rate * 100:.4g}%", expected=money(expected), diff="" if ok else money(gap),
               detail=f"{which} ({money(base)}) × {rate * 100:.4g}% = {money(expected)}; charged "
                      f"{money(charged)}")  # fmt: skip
    if not ok:
        _why_it_does_not_add_up(sums, row, tax_type, label, flagged, base, rate, charged, expected, gap)
    return row


def _two_rates_row(sums: _Sums, row: dict[str, Any], taxes: list[Any], rates: list[float], charged: float,
                   base: float, label: str) -> dict[str, Any]:  # fmt: skip
    """The same tax at two rates: each tax line at its rate, their bases against the lines."""
    expected = sums.numbers[row["tax_type"]].expected = add_up(
        (times(t.taxable_amount, t.rate, sums.places) for t in taxes), sums.places
    )
    printed_base = add_up(t.taxable_amount for t in taxes)
    ok = (within(expected, charged, TAX_UNITS * sums.unit * len(taxes))
          and within(printed_base, base, per_line(sums.n)))  # fmt: skip
    parts = " + ".join(f"{money(t.taxable_amount)} × {t.rate * 100:.4g}%" for t in taxes)
    row.update(ok=ok, rate=" / ".join(f"{r * 100:.4g}%" for r in rates), expected=money(expected),
               diff="" if ok else money(half_up(charged - expected)),
               detail=f"{parts} = {money(expected)} on {row['which']} ({money(base)}); charged "
                      f"{money(charged)}")  # fmt: skip
    if not ok:
        row["why"] = (f"The {label} lines are charged on {money(printed_base)}, the lines marked {label} "
                      f"add up to {money(base)}: check the tax codes, or the vendor's {label}.")  # fmt: skip
    return row


def _why_it_does_not_add_up(sums: _Sums, row: dict[str, Any], tax_type: str, label: str, flagged: list[Any],
                            base: float, rate: float, charged: float, expected: float, gap: float) -> None:  # fmt: skip
    """A tax that does not add up: the line whose amount is the difference (``named``), else why (``why``), and the
    lines to look at, highlighted on the review screen (``marks``)."""
    items = sums.items
    tol = per_line(sums.n)
    # In the size of the amounts: a credit note reverses an invoice line for line (ETA s.232, B-704).
    sign = -1 if (charged or base) < 0 else 1
    base_gap = sign * half_up(gap / rate) if rate else 0.0  # what the lines are short of (or over)
    if nil(charged):
        suspects = flagged
        why = f"Marked {label}, but no {label} is charged"
    elif base_gap < 0:
        suspects = gap_lines(flagged, -base_gap, tol, sign)
        why = f"May not carry {label}: the {label} charged adds up without it"
    else:
        unmarked = [li for li in items if tax_type not in li.taxes_applied]
        suspects = gap_lines(unmarked, base_gap, tol, sign)
        why = f"May carry {label} too: the {label} charged adds up with it"
    if len(suspects) == 1 and not nil(charged):
        li = suspects[0]
        row["named"] = f"Line {li.line_number} ({short(li.description, 28)}, {money(li.amount)})"
        row["why"] = (f"may not carry {label}: the {label} charged adds up without it." if base_gap < 0
                      else f"may carry {label} too: the {label} charged adds up with it.")  # fmt: skip
    elif not nil(charged):
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
        sums.marks.setdefault(li.line_number, why)


def _self_assess_rows(sums: _Sums) -> list[dict[str, Any]]:
    """A provincial tax due on lines and not charged on them (an out-of-province or unregistered vendor, a missed
    line): what is to self-assess, at the official rate (not a difference: nothing was charged, B-709)."""
    province, items, n = sums.province, sums.items, sums.n
    if sums.setup is None or province not in PROVINCES:
        return []
    from .line_tax import DUE, tax_state

    rows = []
    for tax_type in [t for t in ("PST", "QST") if t in REGIME.get(province, ())]:
        due = [li for li in items if tax_state(li, tax_type) == DUE]
        rate = sums.setup.rates.rate_for(tax_type, province, sums.on)
        if not due or not rate:
            continue
        label = tax_name(tax_type, province)
        base = add_up(li.amount for li in due)
        sums.due[tax_type] = (due, rate, times(base, rate, sums.places))
        amount = money(sums.due[tax_type][2])
        if tax_type == "QST":  # a registrant generally owes nothing net: not "self-assess" (B-718, B-720)
            from .tax import QST_NOT_CHARGED

            pill, detail = "QST not charged", f"{label} not charged on {money(base)} ({amount}): {QST_NOT_CHARGED}"
        else:
            pill, detail = "self-assess", f"{label} due on {money(base)} and not charged: self-assess {amount}"
        rows.append({"kind": "selfassess", "what": label, "tax_type": tax_type, "ok": True, "count": len(due),
                     "of": n, "which": f"{len(due)} of {n} lines not charged" if len(due) < n
                     else "every line, not charged", "base": money(base), "rate": f"{rate * 100:.4g}%",
                     "expected": amount, "charged": money(0), "diff": "",
                     "pill": pill, "detail": detail, "lines": []})  # fmt: skip
    return rows


def _total_row(coding: InvoiceCoding, taxes_total: float) -> dict[str, Any]:
    """Subtotal + tax = total: the tax total, as TOTAL_MISMATCH adds it, and the tax lines, as
    TAX_LINES_TOTAL_MISMATCH holds them to it, the row saying which is off (B-1911). Within a cent, as TOTAL_MISMATCH
    (B-1865), or a total rounded to the nickel for cash with its rounding a line (B-1907)."""
    computed = half_up(coding.subtotal + coding.tax_total)
    diff = half_up(coding.grand_total - computed)
    cash = not within(diff, 0, CENT) and cash_rounded(computed, coding.grand_total,
                                                      [li.amount for li in coding.line_items])  # fmt: skip
    adds_up = within(diff, 0, CENT) or cash
    taxes_agree = within(taxes_total, coding.tax_total, CENT)
    shown = diff if not adds_up else half_up(coding.grand_total - half_up(coding.subtotal + taxes_total))
    row = {"kind": "total", "what": "Subtotal + tax = total", "ok": adds_up and taxes_agree,
           "diff": "" if adds_up and taxes_agree else money(shown), "subtotal": money(coding.subtotal),
           "tax": money(coding.tax_total), "computed": money(computed), "total": money(coding.grand_total),
           "detail": f"{money(coding.subtotal)} + {money(coding.tax_total)} = {money(computed)}; the total is "
                     f"{money(coding.grand_total)}", "lines": []}  # fmt: skip
    hints = [f"Rounded to the nickel for cash: the rounding, {money(diff)}, is a line of its own."] if cash else []
    if not taxes_agree:
        hints.append(f"The tax lines add up to {money(taxes_total)}, not the tax total {money(coding.tax_total)}.")
    if hints:
        row["hint"] = " ".join(hints)
    return row


# --- The tax on each line (the review screen's Tax column and the line under the lines) ----------------------------

# How a line's tax amount was got: READ off the line itself; worked out as the line's SHARE of the printed tax, or,
# when the printed tax is not right for the lines taxed, as the line x the RATE.
READ, SHARE, RATE = "read", "share", "rate"
# Where a tax goes, by the Tax setup's treatment: RECOVERED (on the JD Edwards pay item under code V, D-210; a G/L
# line of its own where JD Edwards works out no tax: ``erp.jde_review`` says which), kept INSIDE the line's expense, or
# on the tax's own expense account (OWN_LINE).
RECOVERED, INSIDE, OWN_LINE = "recovered", "inside", "own_line"
_WHERE = {RECOVERABLE: RECOVERED, EXPENSE_TO_LINE: INSIDE, EXPENSE_SEPARATE: OWN_LINE}
_PARTS = (RECOVERED, INSIDE, OWN_LINE)  # the order of a line's parts of a tax (``_posted``)
FEDERAL = ("GST", "HST")  # the taxes of a JD Edwards tax area: two of them, or one at two rates, are two areas
# The lines of each rate, when one tax is printed at two, are told apart by their amounts (every subset counted,
# ``line_tax.subset_solutions``) among at most this many lines: the review screen asks at every edit.
MAX_RATE_LINES = 24
_Worked = tuple[float, str, float | None, str]  # a line's tax worked out: the amount, SHARE or RATE, rate, province
_WorkedOut = dict[str, dict[int, _Worked]]  # {tax: {line position: worked out}}
_Posted = dict[str, dict[int, list[float]]]  # {tax: {line position: [recovered, in the line, on its own account]}}


@dataclass(frozen=True)
class LineTax:
    """One tax on one line, in the invoice's currency. ``amount`` is what the screen shows: read off the line when
    the invoice prints it there, else worked out. ``recovered`` + ``inside`` + ``own_line`` is the line's share of the
    PRINTED tax as it is posted (by amount over the lines marked with the tax): it is ``worked_out``, unless the
    printed tax is not right (``how`` RATE) or the line is one of two rates of the tax told apart."""

    tax_type: str
    name: str  # as it is said where the invoice is supplied: RST in Manitoba
    province: str  # of its tax line ("" for the GST, and when the lines of two rates can't be told apart)
    rate: float | None  # the rate on this line; None when it can't be told (two rates, a foreign tax not charged)
    marked: bool  # the line's tax code carries it (False: no line is marked with it, or it is only printed here)
    worked_out: float
    how: str  # SHARE or RATE: how ``worked_out`` was got
    read: float | None  # printed on this line (``LineItem.taxes_read``); None: the invoice prints none here
    difference: float  # read less worked out when they differ by more than a cent, else 0.0
    where: str  # RECOVERED, INSIDE or OWN_LINE: the Tax setup's treatment of this tax
    recovered: float  # of the printed tax: on the pay item
    inside: float  # kept in this line's expense (a tax not recovered; the half not claimable on a meals line)
    own_line: float  # posted to the tax's own expense account

    @property
    def amount(self) -> float:
        return self.worked_out if self.read is None else self.read

    @property
    def source(self) -> str:
        """READ, SHARE or RATE: where ``amount`` comes from."""
        return self.how if self.read is None else READ


@dataclass(frozen=True)
class TaxDue:
    """A provincial tax due on a line and not charged on it (the Tax check's "self-assess" row): the amount at the
    official rate. Not on the invoice, so in no amount keyed for the line."""

    tax_type: str
    name: str
    rate: float
    amount: float
    self_assess: bool  # PST; False for the QST (a registrant generally owes nothing net)


@dataclass(frozen=True)
class LineTaxes:
    """One line: its tax code, each tax on it, and the amount keyed into JD Edwards for it."""

    position: int  # in ``coding.line_items`` (line numbers may repeat)
    line_number: int
    code: str  # ``line_tax.tax_code_of``
    label: str  # in plain words: "GST only · PST exempt"
    unusual: bool  # not the invoice's usual tax code (``InvoiceTaxes.usual_code``)
    printed: float  # the line's amount as printed
    taxes: tuple[LineTax, ...]
    due: tuple[TaxDue, ...]
    inside: float  # the taxes kept in the line's expense, added up
    keyed: float  # printed + inside: the G/L line's amount (``tax.build_gl_distribution``'s)
    meals: bool  # coded to a meals account: half its GST/HST (and QST) is not claimable
    tip: bool  # a tip: none of the tax printed is put on it, whatever its code
    notes: tuple[str, ...]  # in plain words: a tax read on the line that is not the one worked out


@dataclass(frozen=True)
class TaxCheckLine:
    """One tax's sum for under the lines, from its row of the Tax check (``math_check``): the lines taxed x the rate
    against what is printed, in numbers and as a sentence (``text``). ``kind`` "selfassess": a provincial tax due and
    not charged (``expected`` is what is due, nothing is printed)."""

    kind: str  # "tax" or "selfassess"
    tax_type: str
    name: str
    count: int  # the lines taxed, of ``of``
    of: int
    base: float  # what they add up to
    rate: float | None
    rate_text: str  # "5%"; "13% / 15%" at two rates
    expected: float | None
    printed: float
    ok: bool  # the Tax check's row is right
    text: str
    how: str = ""  # SHARE or RATE: how each line's amount of this tax was worked out
    worked_out: float = 0.0  # the lines' worked-out amounts, added up
    difference: float = 0.0  # printed less ``worked_out``: 0.0 when the lines add up to the printed tax
    lines: tuple[int, ...] = ()  # the line numbers to look at when it is not right; () when on no line in particular
    why: str = ""
    read: float | None = None  # the amounts printed on the lines, added up; None when no line prints one
    where: str = ""
    goes: str = ""  # where it goes, in words: "recovered: on the pay item"
    recovered: float = 0.0
    inside: float = 0.0
    own_line: float = 0.0


@dataclass(frozen=True)
class FederalTax:
    """The GST, or the HST of one province at one rate, as the invoice charges it: two on one invoice are two JD
    Edwards tax areas. ``line_numbers`` is empty when the amounts can't say which lines are its own."""

    tax_type: str
    province: str
    rate: float
    taxable: float  # as printed on its tax lines
    tax: float
    line_numbers: tuple[int, ...]


@dataclass(frozen=True)
class InvoiceTaxes:
    """``invoice_taxes``'s answer. Every amount is in the invoice's currency (``currency``): what JD Edwards holds in
    the domestic one is the exchange rate's, not worked out here."""

    lines: list[LineTaxes]
    check: list[TaxCheckLine]
    currency: str
    province: str  # the place of supply the labels are worded for
    usual_code: str  # the tax code of the most lines, by amount
    usual_label: str
    recovered: float  # on the pay item: the taxes recovered, less the half not claimable on meals
    inside: float  # kept in the lines' expense
    areas: tuple[FederalTax, ...]
    tax_included: bool  # the lines' prices seem to include the tax (the Tax check's hint)
    notes: tuple[str, ...]

    @property
    def two_tax_areas(self) -> bool:
        return len(self.areas) > 1


def line_taxes(coding: InvoiceCoding, setup: TaxSetup) -> list[LineTaxes]:
    """Each line's taxes (``invoice_taxes``'s ``lines``)."""
    return invoice_taxes(coding, setup).lines


def tax_check_lines(coding: InvoiceCoding, setup: TaxSetup) -> list[TaxCheckLine]:
    """Each tax's sum for under the lines (``invoice_taxes``'s ``check``)."""
    return invoice_taxes(coding, setup).check


def invoice_taxes(coding: InvoiceCoding, setup: TaxSetup) -> InvoiceTaxes:
    """Which tax was charged on each line and how much, each tax its own amount; no reading, no database.

    A line's amount of a tax is the one printed on that line when the invoice prints it (``LineItem.taxes_read``).
    Else it is worked out: the line's share of the printed tax (``tax.tax_shares``: by amount over the lines that
    carry it, the odd cents where ``money.split`` puts them) when the Tax check finds the printed tax right for the
    lines taxed, so the lines add up to it to the cent; the line x the rate, shared the same way out of what the rate
    gives, when it does not, and then the tax's ``TaxCheckLine.difference`` is what the lines are short of the printed
    tax. An amount read is held against the one worked out: over a cent apart is said on the line (``notes``).

    Where the printed tax goes is the posting's (``tax.build_gl_distribution``; the JD Edwards pay item, D-210): a tax
    the Tax setup recovers is on the pay item, one it does not is kept in the line's expense (or on its own account),
    and half the recoverable tax of a meals line stays in the line. ``keyed`` is the line with what is kept in it. A
    tip line takes none of a tax, whatever its code. A provincial tax due and not charged is the line's ``due``: at
    the official rate, in no amount keyed.

    Not shown as certain: lines whose prices include the tax show their share of the printed tax, which is in the
    amount printed (``tax_included``); the lines of one tax at two rates take their own rate when their amounts tell
    which is which, else a share at no rate; two federal taxes or rates are reported as two tax areas (``areas``)."""
    sums, rows = _check(coding, setup)
    items, places, province = sums.items, sums.places, sums.province
    posted = _posted(items, coding.tax_lines, setup, places)
    worked = {r["tax_type"]: _worked_out(sums, r, posted.get(r["tax_type"], {})) for r in rows if r["kind"] == "tax"}
    due = _due(sums)
    codes = [tax_code_of(li) for li in items]
    by_amount: dict[str, float] = {}
    for li, code in zip(items, codes, strict=True):
        by_amount[code] = by_amount.get(code, 0.0) + abs(li.amount)
    usual = max(by_amount, key=lambda code: by_amount[code], default="")
    labels = {code: code_label(code, province) for code in by_amount}
    lines = []
    for i, li in enumerate(items):
        taxes, notes = _taxes_of(i, li, worked, posted, setup, province)
        tip = is_tip(li)
        if tip and any(t.marked and nil(t.worked_out) for t in taxes):
            notes.append("A tip: none of the tax printed is put on it.")
        inside = add_up(t.inside for t in taxes)
        lines.append(LineTaxes(i, li.line_number, codes[i], labels[codes[i]], codes[i] != usual, half_up(li.amount),
                               tuple(taxes), tuple(due.get(i, ())), inside, half_up(half_up(li.amount) + inside),
                               is_meals(li.predicted_gl_code, setup.meals_gl_codes), tip, tuple(notes)))  # fmt: skip
    check = [_check_line(sums, r, worked, posted, lines, setup) for r in rows if r["kind"] in ("tax", "selfassess")]
    areas = _areas(coding, worked)
    notes = []
    if sums.tax_included:
        notes.append("The lines' prices seem to include the tax: each line's tax is its share of the printed tax, "
                     "and is in the amount printed. Enter each line before tax.")  # fmt: skip
    if len(areas) > 1:
        notes.append("Two tax areas on one invoice: "
                     + ", ".join(f"{' '.join(filter(None, (a.tax_type, a.province)))} {a.rate * 100:.4g}% "
                                 f"{money(a.tax)} on {money(a.taxable)}" for a in areas) + ".")  # fmt: skip
    return InvoiceTaxes(lines, check, coding.currency, province, usual, labels.get(usual, ""),
                        add_up(t.recovered for line in lines for t in line.taxes),
                        add_up(line.inside for line in lines), areas, sums.tax_included, tuple(notes))  # fmt: skip


def _posted(items: list[Any], tax_lines: list[Any], setup: TaxSetup, places: int) -> _Posted:
    """{tax: {line position: [recovered, kept in the line, on the tax's own account]}}: each line's share of each
    printed tax (``tax.tax_shares``) by where the Tax setup sends it, half a meals line's recoverable tax kept in the
    line (``tax.meals_parts``): the rules ``build_gl_distribution`` posts by."""
    out: _Posted = {}
    for tl in tax_lines:
        if nil(tl.tax_amount):
            continue
        part = _PARTS.index(_WHERE[setup.treatment(tl.tax_type).treatment])
        meals: dict[int, float] = {}
        if part == 0 and tl.tax_type in MEALS_RESTRICTED and setup.meals_gl_codes:
            meals = dict(meals_parts(items, tl, setup.meals_gl_codes, places, tax_lines))
        of_tax = out.setdefault(tl.tax_type, {})
        for idx, share in tax_shares(items, tl, places, tax_lines):
            parts = of_tax.setdefault(idx, [0.0, 0.0, 0.0])
            kept = meals.get(idx, 0.0)
            parts[part] = half_up(parts[part] + share - kept)
            parts[1] = half_up(parts[1] + kept)
    return out


def _tax_province(tax_line: Any) -> str:
    return "" if tax_line.tax_type == "GST" else tax_line.province


def _rate_groups(taxes: list[Any]) -> list[list[Any]]:
    """The tax lines of one tax that charge something, those of one province and rate together."""
    groups: dict[tuple[str, float], list[Any]] = {}
    for t in taxes:
        if not nil(t.tax_amount):
            groups.setdefault((_tax_province(t), round(t.rate, 6)), []).append(t)
    return list(groups.values())


def _lines_of_each_rate(items: list[Any], marked: list[int], groups: list[list[Any]]) -> list[list[int]] | None:
    """The positions of the lines each group of tax lines is charged on, when one tax is printed at two rates (or
    for two provinces): the one set of lines that adds up to each printed taxable amount, to the cent. None when the
    amounts can't say (no set, or several: two lines of the same amount at different rates)."""
    if len(marked) > MAX_RATE_LINES:
        return None
    left, out = list(marked), []
    for taxes in groups[:-1]:
        base = in_cents(add_up(t.taxable_amount for t in taxes))
        found, total = subset_solutions([in_cents(items[i].amount) for i in left], base, 1, cap=2)
        if total != 1 or not found[0].lines:
            return None
        out.append([left[k] for k in sorted(found[0].lines)])
        left = [i for i in left if i not in out[-1]]
    rest = in_cents(add_up(t.taxable_amount for t in groups[-1]))
    if not left or abs(sum(in_cents(items[i].amount) for i in left) - rest) > 1:
        return None
    return [*out, left]


def _worked_out(sums: _Sums, row: dict[str, Any], posted: dict[int, list[float]]) -> dict[int, _Worked]:
    """{line position: its amount of one tax, worked out} from the tax's row of the Tax check."""
    tax_type, items, places = row["tax_type"], sums.items, sums.places
    found = sums.numbers[tax_type]
    marked = [i for i, li in enumerate(items) if tax_type in li.taxes_applied]
    groups = _rate_groups(found.taxes)
    told = _lines_of_each_rate(items, marked, groups) if len(groups) > 1 else None
    if told is not None:  # each line at its own rate: its share of that rate's tax, or the line x that rate
        out: dict[int, _Worked] = {}
        for taxes, mine in zip(groups, told, strict=True):
            amounts = [items[i].amount for i in mine]
            rate, charged = taxes[0].rate, add_up(t.tax_amount for t in taxes)
            expected = times(add_up(amounts), rate, places)
            line_by_line = add_up((times(a, rate, places) for a in amounts), places)
            right = tax_right(charged, (expected, line_by_line), places)
            shares = split(charged if right else expected, amounts, places)
            for i, share in zip(mine, shares, strict=True):
                out[i] = (share, SHARE if right else RATE, rate, _tax_province(taxes[0]))
        return out
    where = _tax_province(groups[0][0]) if len(groups) == 1 else ""
    # The printed tax is right for the lines marked: each line's share of it. So too when there is no telling what
    # the rate gives on a line: no line marked, prices that include the tax, two rates not told apart, no rate.
    by_share = row["ok"] or not marked or sums.tax_included or len(groups) > 1 or found.expected is None
    if by_share:
        return {i: (add_up(posted.get(i, ())), SHARE, found.rate, where) for i in sorted({*marked, *posted})}
    # The line x the rate, out of what the rate gives on the lines marked. A tip takes none, as it takes no share.
    taxed = [i for i in marked if not is_tip(items[i])] or marked
    amounts = [items[i].amount for i in taxed]
    gives = found.expected if len(taxed) == len(marked) else times(add_up(amounts), found.rate, places)
    by_rate = dict(zip(taxed, split(gives, amounts, places), strict=True))
    return {i: (by_rate.get(i, 0.0), RATE, found.rate, where) for i in marked}


def _taxes_of(i: int, li: Any, worked: _WorkedOut, posted: _Posted, setup: TaxSetup,
              province: str) -> tuple[list[LineTax], list[str]]:  # fmt: skip
    """The taxes of one line, and what is to say of the amounts read on it."""
    read = li.taxes_read or {}
    taxes, notes = [], []
    for tax_type in TAX_TYPES:
        got = worked.get(tax_type, {}).get(i)
        printed = read.get(tax_type)
        if got is None and (printed is None or nil(printed)):
            continue
        amount, how, rate, of_province = got or (0.0, SHARE, None, "")
        name = tax_name(tax_type, province)
        differs = printed is not None and not within(printed, amount, CENT)
        if got is None:
            notes.append(f"{name} {money(printed)} is printed on this line, but its tax code does not carry {name}.")
        elif differs:
            notes.append(f"{name} printed on this line is {money(printed)}; worked out, {money(amount)}: a "
                         f"difference of {money(half_up(printed - amount))}.")  # fmt: skip
        parts = posted.get(tax_type, {}).get(i, (0.0, 0.0, 0.0))
        taxes.append(LineTax(tax_type, name, of_province, rate, tax_type in li.taxes_applied, amount, how, printed,
                             half_up(printed - amount) if differs else 0.0,
                             _WHERE[setup.treatment(tax_type).treatment], *parts))  # fmt: skip
    return taxes, notes


def _due(sums: _Sums) -> dict[int, list[TaxDue]]:
    """{line position: the provincial taxes due on it and not charged}: each line's share (``money.split``) of what
    the Tax check's self-assess row gives."""
    position = {id(li): i for i, li in enumerate(sums.items)}
    out: dict[int, list[TaxDue]] = {}
    for tax_type, (lines, rate, amount) in sums.due.items():
        shares = split(amount, [li.amount for li in lines], sums.places)
        for li, share in zip(lines, shares, strict=True):
            out.setdefault(position[id(li)], []).append(
                TaxDue(tax_type, tax_name(tax_type, sums.province), rate, share, tax_type == "PST")
            )
    return out


def _check_line(sums: _Sums, row: dict[str, Any], worked: _WorkedOut, posted: _Posted,
                lines: list[LineTaxes], setup: TaxSetup) -> TaxCheckLine:  # fmt: skip
    """One tax's row of the Tax check as a ``TaxCheckLine``."""
    tax_type = row["tax_type"]
    if row["kind"] == "selfassess":
        due, rate, amount = sums.due[tax_type]
        return TaxCheckLine("selfassess", tax_type, row["what"], row["count"], row["of"],
                            add_up(li.amount for li in due), rate, row["rate"], amount, 0.0, True, row["detail"],
                            lines=tuple(li.line_number for li in due), goes=row["pill"])  # fmt: skip
    found = sums.numbers[tax_type]
    of_tax = worked[tax_type]
    worked_out = add_up(amount for amount, *_ in of_tax.values())
    read = [t.read for line in lines for t in line.taxes if t.tax_type == tax_type and t.read is not None]
    parts = [add_up(p[k] for p in posted.get(tax_type, {}).values()) for k in range(3)]
    where = _WHERE[setup.treatment(tax_type).treatment]
    kept = [lines[i].line_number for i, p in posted.get(tax_type, {}).items() if not nil(p[1])]
    goes = {RECOVERED: "recovered: on the pay item", OWN_LINE: "not recovered: on its own account",
            INSIDE: f"not recovered: inside {_which_lines(kept)}"}[where]  # fmt: skip
    if where == RECOVERED and kept:
        goes += f", less {money(parts[1])} on meals (50%) kept inside {_which_lines(kept)}"
    if nil(found.charged):
        goes = ""
    why = row["detail"] if not found.flagged else " ".join(filter(None, (row.get("named"), row.get("why"))))
    return TaxCheckLine("tax", tax_type, row["what"], row["count"], row["of"], found.base, found.rate, row["rate"],
                        found.expected, found.charged, row["ok"], _sentence(sums, row, found),
                        RATE if any(how == RATE for _, how, *_ in of_tax.values()) else SHARE, worked_out,
                        half_up(found.charged - worked_out), tuple(row["lines"]), "" if row["ok"] else why,
                        add_up(read) if read else None, where, goes, *parts)  # fmt: skip


def _which_lines(numbers: list[int]) -> str:
    return f"line {numbers[0]}" if len(numbers) == 1 else "the lines"


def _sentence(sums: _Sums, row: dict[str, Any], found: _TaxNumbers) -> str:
    """A tax's sum in one sentence: "GST: lines taxed 7,621.40 × 5% = 381.07 · printed 381.07 ✓", or the difference
    and the lines that carry it."""
    name, charged, expected = row["what"], found.charged, found.expected
    if not found.flagged and not nil(charged):
        return f"{name}: {money(charged)} printed, but no line is marked {name}"
    if expected is None:
        return f"{name}: {row['detail']}"
    if found.rate is None:  # one tax at two rates: each tax line at its own
        product = " + ".join(f"{money(t.taxable_amount)} × {t.rate * 100:.4g}%" for t in found.taxes)
    else:
        some = "" if row["count"] == row["of"] else f" ({row['which']})"
        product = f"lines taxed {money(found.base)}{some} × {row['rate']}"
    text = f"{name}: {product} = {money(expected)}"
    if nil(charged):
        return f"{text} · none printed"
    text += f" · printed {money(charged)}"
    gap = half_up(charged - expected)
    if row["ok"]:
        return f"{text} ✓" + ("" if nil(gap) else f" ({money(abs(gap))} of rounding)")
    more = (gap > 0) == (charged > 0)  # in the size of the amounts: a credit note's are below zero
    text += f" · {money(abs(gap))} {'more' if more else 'less'} than the rate gives"
    if sums.tax_included:
        return f"{text}: the lines' prices seem to include the tax"
    if row.get("named"):
        return f"{text} · {row['named']} {row['why']}"
    if row["lines"]:
        return f"{text} · look at {'line' if len(row['lines']) == 1 else 'lines'} " + ", ".join(map(str, row["lines"]))
    return f"{text}, on no line in particular"


def _areas(coding: InvoiceCoding, worked: _WorkedOut) -> tuple[FederalTax, ...]:
    """The federal taxes the invoice charges, one for each of GST and the HST of a province at a rate, with the lines
    that carry each."""
    found: dict[tuple[str, str, float], list[Any]] = {}
    for t in coding.tax_lines:
        if t.tax_type in FEDERAL and not nil(t.tax_amount):
            found.setdefault((t.tax_type, _tax_province(t), round(t.rate, 6)), []).append(t)
    out = []
    for (tax_type, province, rate), taxes in found.items():
        mine = [coding.line_items[i].line_number for i, (_, _, at, where) in worked.get(tax_type, {}).items()
                if at is not None and (where, round(at, 6)) == (province, rate)]  # fmt: skip
        out.append(FederalTax(tax_type, province, rate, add_up(t.taxable_amount for t in taxes),
                           add_up(t.tax_amount for t in taxes), tuple(mine)))  # fmt: skip
    return tuple(out)
