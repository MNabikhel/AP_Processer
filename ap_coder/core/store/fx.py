"""Exchange rates as AP sets them in Settings → Review (CAD for one unit), for the second-approval limit."""

from __future__ import annotations

import re

_FX_RATE = re.compile(r"([A-Za-z]{3})\s*[=:]?\s*([0-9]+(?:[.,][0-9]+)?)")


# The settings a second approval depends on: their changes are logged with the old and new values (B-970).
APPROVAL_CONTROLS = ("approval_limit", "fx_rates")
# The smallest CAD for one unit taken as a rate: below it a rate is a typing mistake, or a way round the second
# approval (USD=0.0001 makes every USD invoice "under" the limit, B-971). A few currencies' units are worth less than
# a cent: theirs is about a tenth of the real rate.
MIN_FX_RATE = 0.01
LOW_UNIT_FX_RATES = {"JPY": 0.001, "KRW": 0.0001, "HUF": 0.0003, "CLP": 0.0001, "ISK": 0.001, "COP": 0.00003,
                     "IDR": 0.00001, "VND": 0.000005, "PYG": 0.00001, "UZS": 0.00001, "IRR": 0.000003,
                     "LAK": 0.000005, "MNT": 0.00003, "UGX": 0.00003, "TZS": 0.00005, "KHR": 0.00003,
                     "NGN": 0.00008, "PKR": 0.0005, "LKR": 0.0005, "XOF": 0.0002, "XAF": 0.0002, "AMD": 0.0003,
                     "KZT": 0.0002, "MMK": 0.00006, "RWF": 0.0001, "MGA": 0.00003, "GNF": 0.00001}  # fmt: skip


def min_fx_rate(code: str) -> float:
    return LOW_UNIT_FX_RATES.get(code.upper(), MIN_FX_RATE)


def tiny_fx_rates(text: str) -> list[str]:
    """The rates of ``text`` at 0 or below the smallest believable one ("USD=0", "USD=0.0001"), as typed."""
    return [f"{code.upper()}={value}" for code, value in _FX_RATE.findall(text or "")
            if code.upper() != "CAD" and float(value.replace(",", ".")) < min_fx_rate(code)]  # fmt: skip


def parse_fx_rates(text: str) -> dict[str, float]:
    """ "USD=1.37, EUR 1,50; GBP: 1.85" -> {"USD": 1.37, "EUR": 1.5, "GBP": 1.85} (CAD is always 1). A rate at 0 or
    below ``min_fx_rate`` is no rate: the invoice then needs the second approval (B-971)."""
    rates = {"CAD": 1.0}
    for code, value in _FX_RATE.findall(text or ""):
        rate = float(value.replace(",", "."))
        if rate >= min_fx_rate(code) and code.upper() != "CAD":  # a CAD rate typed in would scale every limit check
            rates[code.upper()] = rate
    return rates


def unread_fx_text(text: str) -> str:
    """What of ``text`` is not a rate ``parse_fx_rates`` reads ("" when all of it is): "1.37 USD" -> "1.37 USD"."""
    return re.sub(r"[\s,;]+", " ", _FX_RATE.sub(" ", text or "")).strip()
