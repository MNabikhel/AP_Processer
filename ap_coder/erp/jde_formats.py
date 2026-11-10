"""JD Edwards E1's formats: a date as its "Julian" CYYDDD (``julian``), an amount with implied decimals or a decimal
point (``jde_amount``), a text cut to its column, and the small readers of a value the JD Edwards modules share
(``_date``, ``_norm``, ``_units``). It knows nothing of the settings: every ``jde_*.py`` may import it (D-222).
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..coding import money
from ..coding.tax import ZERO_DECIMAL_CURRENCIES

NO_DECIMAL_CURRENCIES = dict.fromkeys(sorted(ZERO_DECIMAL_CURRENCIES), 0)

E1_YEARS = range(1900, 2900)  # what CYYDDD holds: century digit 0 (1900s) to 9 (2800s)


def julian(value: dt.date | dt.datetime | str) -> int:
    """E1 "Julian" date CYYDDD: C = century (0 = 1900s, 1 = 2000s), YY = year, DDD = day of the year.
    2026-10-09 → 126282; 1999-12-31 → 99365 (099365)."""
    if isinstance(value, str):
        value = dt.date.fromisoformat(value.strip()[:10])
    if isinstance(value, dt.datetime):
        value = value.date()
    if value.year not in E1_YEARS:
        raise ValueError(f"{value} is outside what JD Edwards dates hold (1900 to 2899)")
    return (value.year - 1900) * 1000 + value.timetuple().tm_yday


def jde_amount(x: float | int | str | Decimal, implied: bool = True, decimals: int = 2) -> int | str:
    """An amount the way E1 Z-files take it: implied decimals by default ($5.50 → 550), or with a decimal
    point ("5.50"). Rounded half away from zero, on the decimal value (2.675 → 268)."""
    d = Decimal(str(x)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    if implied:
        return int(d.scaleb(decimals))
    return f"{d:.{decimals}f}"


def _units(x: Any, decimals: int = 2) -> int:
    """An amount in cents, rounded half away from zero to the cent, or to what the currency can hold (``decimals``
    0 for the yen: a multiple of 100); 0 for what is not a number."""
    try:
        return int(Decimal(str(x or 0)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP) * 100)
    except ArithmeticError:
        return 0


def _split(total: int, weights: list[int], unit: int = 1) -> list[int]:
    """``total`` cents split in proportion to ``weights``, in steps of ``unit`` cents (100 for a currency without
    cents): ``money.split``'s rule, each share half up and what is left over on the largest, so a pay item takes the
    share of a tax its line has (``tax.tax_shares``, B-1889). Exact: the shares add up to the total."""
    return [money.in_cents(share) for share in money.split(total / 100, weights, 0 if unit == 100 else 2)]


def from_julian(value: Any) -> dt.date | None:
    """The date of an E1 "Julian" CYYDDD (``julian``'s), None when it isn't one."""
    try:
        n = int(value)
        return dt.date(1900 + n // 1000, 1, 1) + dt.timedelta(days=n % 1000 - 1)
    except (TypeError, ValueError, OverflowError):
        return None


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _text(value: Any, size: int) -> str:
    """One line of text, cut to the column's size."""
    return " ".join(str(value or "").split())[:size].rstrip()


def _plain(value: Any, places: int = 2) -> str:
    try:
        d = Decimal(str(value if value is not None else 0)).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
    except ArithmeticError:
        return ""
    text = f"{d:.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _negative(value: Any) -> bool:
    try:
        return float(value or 0) < 0
    except (TypeError, ValueError):
        return False
