# ruff: noqa: E501  (label tables read best one per line)
"""A progress billing (D-274): a contractor's application for payment on a subcontract, read from the page's lines by
their labels: the work completed to date, the work billed before, the work this period, the holdback taken off it
(and its percent), the taxes, the total before the holdback and the amount due; and whether it releases a holdback
held before. Each amount is the last printed on its label's row of the page ("6. Less holdback 10%   (7,500.00)",
``layout_rows``); a label must be the row's own words, so a line item named for one ("Retainage billing - Phase 2")
is none.

``read_progress`` gives what is read as a plain dict (kept beside the coding, ``schema.PROGRESS_BILLING``), or None
for an invoice that is no progress billing: one holds something back, releases a holdback, or bills the work this
period beside what was billed before or to date. ``adds_up``: the work this period, less the holdback, plus the taxes
is the amount due as printed (and the work to date less the work before is the work this period, when both are
printed): only then are its amounts used for the coding (``ai/offline_coder``) and the voucher (``erp/jde_retainage``).

Its own part of the rule reader, the last: it uses ``_common`` only.
"""

from __future__ import annotations

import re
from typing import Any

from ...core.parsing import find_amounts
from ..types import DocLayout
from ._common import plain

# The line a progress billing printed without a line table is coded as (a G702-style application: ``ai/offline_coder``)
PROGRESS_LINE = "Work completed this period"
# Before a label: a G702's line number ("5.", "6 a)").
_NUMBERED = r"\W*(?:\d{1,2}\s*[.)]\s*)?(?:[a-h]\s*[.)]\s*)?"
_PCT = r"\(?\s*\d{1,2}(?:[.,]\d{1,2})?\s*%\s*\)?"
_HELD = r"(?:holdback|hold\s*back|retainage|retention|retenue(?:\s*de\s*garantie)?)"
# Each amount's labels, the line's words before its amount (accents off, lower case); in this order, the first that
# fits a line is its.
LABELS: dict[str, str] = {
    "holdback": rf"(?:less\s*:?\s*|moins\s*:?\s*)?(?:{_PCT}\s*)?{_HELD}(?:\s*(?:of|on|at|de|a)?\s*{_PCT})?"
    r"(?:\s*\(?(?:of|on|sur)\s*(?:the\s*)?(?:completed\s*)?(?:work|travaux)(?:\s*this\s*period)?\)?)?",
    "net": rf"net\s*(?:amount\s*)?(?:this\s*(?:period|billing|application|claim)|of\s*{_HELD}|after\s*{_HELD}|billing)"
    rf"|(?:amount|total)\s*(?:less|net\s*of|after)\s*{_HELD}|total\s*earned\s*less\s*{_HELD}|montant\s*net",
    "previous": r"(?:less\s*:?\s*|moins\s*:?\s*)?(?:previous(?:ly)?\s*(?:billed|billings?|applications?|certificates?(?:\s*for\s*payment)?"
    r"|claims?|invoiced|progress\s*billings?|work)|(?:billed|invoiced)\s*previously|(?:travaux\s*)?factur[ea]s?\s*(?:precedemment|anterieurement))",
    "to_date": r"(?:total\s*)?(?:work\s*)?(?:completed|done|performed)(?:\s*(?:and|&)\s*stored)?\s*to\s*date|(?:total\s*)?(?:billed|work)\s*to\s*date"
    r"|travaux\s*(?:executes|realises)\s*a\s*ce\s*jour|facture\s*a\s*ce\s*jour",
    "amount_due": r"(?:current\s*|net\s*)?(?:payment|amount|balance|total)\s*(?:now\s*)?(?:due|payable|owing)(?:\s*this\s*(?:invoice|billing|application|period))?"
    r"|net\s*payable|please\s*pay|montant\s*(?:du|a\s*payer)|solde\s*(?:du|a\s*payer)|total\s*a\s*payer",
    "total": r"(?:invoice\s*)?total(?:\s*(?:amount|invoice|this\s*invoice|before\s*holdback))?|total\s*de\s*la\s*facture",
    "subtotal": r"sub\s*-?\s*total|sous\s*-?\s*total|total\s*(?:before\s*tax(?:es)?|work|this\s*period|avant\s*taxes)",
}  # fmt: skip
_LABELS = {name: re.compile(rf"^{_NUMBERED}(?:{pattern})\W*$") for name, pattern in LABELS.items()}
# The work this period, anywhere in a line of its own words ("Mechanical rough-in - work this period"), never a
# holdback's, a tax's or a net's
_THIS_PERIOD = re.compile(r"\bthis\s*(?:period|application|billing|claim)\b|\bcurrent\s*(?:period|billing|application|claim)\b"
                          r"|travaux\s*de\s*la\s*periode")  # fmt: skip
_NOT_THIS_PERIOD = re.compile(
    rf"{_HELD}|\bnet\b|\b(?:gst|hst|pst|qst|rst|tps|tvq|tvh|tax(?:es)?)\b|\bdue\b|\bpayable\b"
)
# A holdback paid out: a line that bills it ("Holdback release - Phase 1", "Release of retainage")
_RELEASE = re.compile(rf"{_HELD}\s*(?:release|released|payout|paid\s*out)|release\s*(?:of\s*)?(?:the\s*)?(?:\d{{1,2}}\s*%\s*)?{_HELD}"
                      r"|liberation\s*(?:de\s*)?(?:la\s*)?retenue|retenue\s*liberee")  # fmt: skip
# (its rate may follow with no space: a scan's "GST5%on45,000.00")
_TAX = re.compile(rf"^{_NUMBERED}(?:total\s*)?(gst\s*/\s*hst|hst|gst|qst|pst|rst|tps|tvq|tvh)(?![a-z])")
_TAX_TYPES = {
    "gst": "GST",
    "tps": "GST",
    "hst": "HST",
    "tvh": "HST",
    "qst": "QST",
    "tvq": "QST",
    "pst": "PST",
    "rst": "PST",
}
_MONEY = re.compile(r"\d[.,]\d{2}$")
_RATE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")


def _last_money(text: str) -> tuple[float, int] | None:
    """The last amount printed as money on a line (two decimals) and where it starts; None when there is none."""
    found = [(value, start) for value, start, end in find_amounts(text)
             if _MONEY.search(text[start:end].rstrip(")-CRcr "))]  # fmt: skip
    return found[-1] if found else None


def _rate(label: str) -> float | None:
    m = _RATE.search(label)
    return float(m.group(1).replace(",", ".")) if m else None


def layout_rows(layout: DocLayout) -> list[str]:
    """The pages' rows, top to bottom, each its lines read left to right: a label and its amount printed apart are
    one row ("Less holdback 10%" ... "(4,500.00)")."""
    out: list[str] = []
    for page in layout.pages:
        rows: list[list[Any]] = []
        for line in sorted(page.lines, key=lambda li: li.box.cy):
            if rows and abs(line.box.cy - rows[-1][0].box.cy) <= max(line.box.height, rows[-1][0].box.height) / 2:
                rows[-1].append(line)
            else:
                rows.append([line])
        out += ["   ".join(li.text for li in sorted(row, key=lambda li: li.box.x0)) for row in rows]
    return out


def _lines_read(rows_text: list[str]) -> tuple[dict[str, Any], list[float]]:
    """{label: its amount} of the page's rows (the last of each label), the taxes by type added up, whether a row
    releases a holdback; and the amounts of the rows billing the work this period."""
    read: dict[str, Any] = {"taxes": {}, "release": False}
    rows: list[float] = []
    for line in rows_text:
        money = _last_money(line)
        if money is None:
            continue
        value, at = money
        words = plain(line[:at]).strip()
        if _RELEASE.search(words):
            read["release"] = True
            continue
        tax = _TAX.match(words)
        if tax:
            kind = tax.group(1).replace(" ", "")
            rate = _rate(words)
            name = "HST" if kind == "gst/hst" and rate and rate > 5 else _TAX_TYPES.get(kind, "GST")
            read["taxes"][name] = round(read["taxes"].get(name, 0.0) + value, 2)
            continue
        label = next((name for name, pattern in _LABELS.items() if pattern.match(words)), None)
        if label == "holdback":
            read["holdback"], read["holdback_rate"] = abs(value), _rate(words)
        elif label is not None:
            read[label] = value
        elif _THIS_PERIOD.search(words) and not _NOT_THIS_PERIOD.search(words):
            rows.append(value)
    return read, rows


def read_progress(rows_text: list[str]) -> dict[str, Any] | None:
    """What a progress billing's rows (``layout_rows``) say (the module's first lines), or None when it is none."""
    read, rows = _lines_read(rows_text)
    to_date, previous = read.get("to_date"), read.get("previous")
    holdback = round(read.get("holdback") or 0.0, 2)
    if not holdback and not read["release"] and not (rows and (to_date is not None or previous is not None)):
        return None
    this_period = read.get("subtotal")
    if this_period is None:
        this_period = round(sum(rows), 2) if rows else None
    if this_period is None and to_date is not None and previous is not None:
        this_period = round(to_date - abs(previous), 2)
    taxes = read["taxes"]
    due = read.get("amount_due")
    adds_up = (
        this_period is not None and due is not None and abs(this_period - holdback + sum(taxes.values()) - due) < 0.005
    )
    if adds_up and to_date is not None and previous is not None:
        adds_up = abs(to_date - abs(previous) - this_period) < 0.005
    return {
        "this_period": this_period, "previous": abs(previous) if previous is not None else None,
        "to_date": to_date, "holdback": holdback, "holdback_rate": read.get("holdback_rate"), "taxes": taxes,
        "amount_due": due, "release": read["release"], "adds_up": adds_up,
    }  # fmt: skip
