"""Money and tax arithmetic, one way for the tax checks, the Tax check, the GL distribution and the reports (D-183).

An amount is the decimal it is printed as: a float is taken as the shortest decimal that is it (2.675, not the binary
2.67499999999999982...), and a tax, a share or a sum is worked out on those decimals and rounded half up (half away
from zero: a credit note rounds as the invoice it reverses) to the cent, or to the whole unit of a currency without
cents (``places`` 0: the yen). Python's ``round`` on a float is half-even on its binary value: 2.50 x 5% gave 0.12 and
0.30 x 5% gave 0.01, where the vendor's arithmetic and JD Edwards' (``erp.jde_formats.jde_amount``) give 0.13 and 0.02
(B-1864).

What counts as equal is named once, below: the tax checks (``tax.check_taxes``), the Tax check
(``taxmath.math_check``), the totals and the posting (``validation.validate_coding``) and the line tax codes
(``line_tax``) all take it from here.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Context, Decimal

_UNITS = {places: Decimal(1).scaleb(-places) for places in range(7)}
_SCALE = {places: 10.0**places for places in range(7)}
# The quick way, the same answer: a float rounds as its decimal does unless that decimal is a tie, and a float of
# fewer than _FAST units (a billion dollars) is off its decimal, after a product or a sum, by under 1e-5 of a unit.
# So a float more than _NEAR_A_TIE from a tie is rounded as a float (a quarter of the time of the decimals, at every
# check); a tie, or what could be one, is worked out in decimals (tests/coding/test_money.py holds them equal).
_FAST = 1e11
_NEAR_A_TIE = 0.4999
# Rounding to the cent needs every digit of the number: the decimals' own 28 ran out at 27 digits typed in a field
# (decimal.InvalidOperation, B-2096). This holds any float, and a product or a share of two.
_ALL_DIGITS = Context(prec=1000)
# The largest amount a field takes (ten trillion): beyond it a float no longer holds the cents, and no invoice, tax or
# quantity is that large. What is typed past it is not an amount (``web.invoice_form``).
LARGEST = 1e13


def _dec(amount: float) -> Decimal:
    return Decimal(str(amount))


def _rounded(exact: Decimal, places: int) -> Decimal:
    return exact.quantize(_UNITS[places], ROUND_HALF_UP, context=_ALL_DIGITS)


def _nearest(scaled: float) -> int | None:
    """The whole units ``scaled`` rounds to when the float can tell, None when it is to be worked out in decimals."""
    if -_FAST < scaled < _FAST:
        nearest = round(scaled)
        if abs(scaled - nearest) < _NEAR_A_TIE:
            return nearest
    return None


def half_up(amount: float, places: int = 2) -> float:
    """``amount`` rounded half up to ``places`` decimals: 2.675 is 2.68, -0.125 is -0.13."""
    nearest = _nearest(amount * _SCALE[places])
    if nearest is not None:
        return nearest / _SCALE[places] if nearest else math.copysign(0.0, amount)
    return float(_rounded(_dec(amount), places))


def times(amount: float, factor: float, places: int = 2) -> float:
    """``amount`` x ``factor`` (a rate, a share), worked out on their decimals and rounded half up: a tax on its base
    (2.50 x 5% is 0.13), a part of a line."""
    product = amount * factor
    nearest = _nearest(product * _SCALE[places])
    if nearest is not None:
        return nearest / _SCALE[places] if nearest else math.copysign(0.0, product)
    return float(_rounded(_dec(amount) * _dec(factor), places))


def add_up(amounts: Iterable[float], places: int = 2) -> float:
    """The amounts added up on their decimals, rounded half up."""
    amounts = amounts if isinstance(amounts, list) else list(amounts)
    scale = _SCALE[places]
    total = math.fsum(amounts)  # the float nearest the sum of the floats: off the decimals' by their own errors only
    if sum(map(abs, amounts)) * scale < _FAST:
        nearest = round(total * scale)
        if abs(total * scale - nearest) < _NEAR_A_TIE:
            return nearest / scale if nearest else math.copysign(0.0, total) if total else 0.0
    return float(_rounded(sum(map(_dec, amounts), Decimal(0)), places))


def split(total: float, weights: list[float], places: int = 2) -> list[float]:
    """``total`` in proportion to ``weights``, each share rounded half up to ``places`` (whole units when 0); what is
    left over goes to the largest weight, so the shares always add up to the total as read, to the cent. No weight
    at all (they add up to nothing): the first takes it all."""
    if not weights:
        return []
    exact = [_dec(w) for w in weights]
    base = sum(exact, Decimal(0))
    if not base:
        return [half_up(total), *[0.0] * (len(weights) - 1)]
    whole = _dec(total)
    shares = [_rounded(whole * w / base, places) for w in exact]
    biggest = max(range(len(weights)), key=lambda i: abs(weights[i]))
    shares[biggest] += _rounded(whole - sum(shares, Decimal(0)), 2)
    return [float(s) for s in shares]


def in_cents(amount: float) -> int:
    """``amount`` in whole cents, half up: 10.005 is 1001."""
    nearest = _nearest(amount * 100)
    return nearest if nearest is not None else int(_rounded(_dec(amount).scaleb(2), 0))


# --- What counts as equal ------------------------------------------------------------------------------------------
# Amounts, in a currency's unit (a cent, or a whole yen without cents). "within" allows only the float error of a
# difference beyond the tolerance, at the size of the amounts compared.

NIL = 0.005  # under half a cent an amount is nothing: a printed "GST 0.00" charges no GST
# The GL posting against the invoice total, and the parts of a split against what is split: equal to the cent.
EXACT = 0.005
# One sum of amounts printed rounded, the vendor having added before rounding: a cent. Subtotal + tax = total (the
# validation's TOTAL_MISMATCH, the Tax check's total row), the tax lines = the tax total (TAX_LINES_TOTAL_MISMATCH),
# the lines = the total when the prices include the tax.
CENT = 0.01
# A total paid in cash is rounded to the nickel (Canada since 2013: 1 or 2 cents down, 3 or 4 up), the rounding
# entered as a line of its own so that the posting balances: subtotal + tax 59.18, paid 59.20 (``cash_rounded``).
CASH_ROUNDING = 0.02
# Another reader's amount (Azure's, DI_AMOUNT_DIFFERS), and the least quantity x unit price may be off (LINE_MATH).
READ_AGAIN = 0.02
LINE_MATH_SHARE = 0.005  # quantity x unit price: unit prices are printed rounded, 0.5% of the extension
# A printed taxable amount is the base of its tax line when it makes the tax within this (line_tax._tax_base).
PRINTED_BASE = 0.05
# A tax against its base x rate, worked out once or line by line: within this many units (cents, or yen) either way
# is right (TAX_CALC_MISMATCH, the Tax check's tax rows: a unit per tax line when one tax is printed at two rates;
# the review screen's tick on a tax line: ``tax_right``).
TAX_UNITS = 1


def per_line(lines: int) -> float:
    """The lines against what they add up to: the subtotal (SUBTOTAL_MISMATCH, the Tax check's lines row), a tax's
    taxable amount (TAX_BASE_MISMATCH, QST_ON_GST_INCLUSIVE, the Tax check's tax rows), the line that is the
    difference: a cent a line (the vendor rounds line by line), and a cent more."""
    return 0.01 * max(1, lines) + 0.01


def tax_band(unit: float, lines: int) -> float:
    """Beyond ``TAX_UNITS``, a tax still within half a unit of rounding a line (tax rounded per unit, a line marked
    wrongly) and one more: a warning, not an error (TAX_CALC_MISMATCH)."""
    return unit * (max(1, lines) / 2 + 1)


def solver_cents(lines: int, rate: float) -> int:
    """How far the lines may be from a tax's base and still be its base, for the line tax codes' solver: a cent a
    line (at least two), and the base's own uncertainty, half a cent of tax over the rate (10 cents at 5%), in whole
    cents half up."""
    rounding = 0.5 / rate if rate > 0 else 0.0
    return int(Decimal(str(max(2.0, 1.0 * lines) + rounding)).quantize(_UNITS[0], ROUND_HALF_UP))


def cash_rounded(computed: float, total: float, lines: Iterable[float]) -> bool:
    """Whether ``total`` is ``computed`` (subtotal + tax) rounded to the nickel for cash, its rounding one of
    ``lines`` (the line amounts): TOTAL_MISMATCH and the Tax check's total row (B-1907)."""
    rounding = half_up(total - computed)
    return (not nil(rounding) and within(rounding, 0, CASH_ROUNDING) and in_cents(total) % 5 == 0
            and any(within(amount, rounding, EXACT) for amount in lines))  # fmt: skip


def tax_right(charged: float, ways: Iterable[float], places: int = 2) -> bool:
    """Whether a tax charged is one of the ``ways`` it is worked out (once on its base, line by line) within
    ``TAX_UNITS`` of the currency's unit: TAX_CALC_MISMATCH, the Tax check's tax rows and the review screen's tick on
    a tax line (B-1867, B-1908)."""
    return any(within(way, charged, TAX_UNITS * 10.0**-places) for way in ways)


def nil(amount: float) -> bool:
    """Whether ``amount`` is nothing (under half a cent)."""
    return abs(amount) < NIL


def within(a: float, b: float, tolerance: float) -> bool:
    """Whether ``a`` and ``b`` are equal within ``tolerance``, and the float error of their difference: a billionth
    (a tolerance worked out in floats, 0.01 x 7 + 0.01) and a few parts in 10^16 of the amounts, as that error grows
    with them (two amounts a cent apart differ by a cent and up to 2e-9 from 2**23 dollars on, B-1906)."""
    return abs(a - b) <= tolerance + 1e-9 + 2e-15 * max(abs(a), abs(b))
