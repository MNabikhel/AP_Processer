"""Payment terms: when an invoice is due and whether an early-payment discount is on offer.

Terms are read as printed ("Net 30", "2/10 Net 30", "1% 15 days, net 45", "Due on receipt",
"Payable dans les 30 jours"...). The due date is the one printed on the invoice when there is one,
otherwise the invoice date plus the net days of the terms, otherwise the organisation's default
(Settings, 30 days unless changed).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

DEFAULT_TERMS_DAYS = 30
WARNING, INFO = "warning", "info"
DUE_SOON_DAYS = 7

_RECEIPT = re.compile(
    r"(on|upon|due on|payable on|at)\s+receipt|sur\s+r[ée]ception|[àa]\s+r[ée]ception|comptant|cash on delivery|"
    r"\bcod\b|immediate(ly)?|due now",
    re.IGNORECASE,
)
_DISCOUNT = [
    # 2/10 net 30, 2/10 n30, 1.5/15 net 60 (the net part is required: "10/31" alone is a date)
    re.compile(r"(\d+(?:[.,]\d+)?)\s*%?\s*/\s*(\d+)\s*(?:days?)?\s*[,;]?\s*n(?:et)?\s*/?\s*(\d+)", re.I),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*%\s*(\d+)\s*[,;]?\s*n(?:et)?\s*/?\s*(\d+)", re.I),  # 2% 10 net 30
    # 2% 10 days, 2% discount if paid within 10 days, 2 % escompte 10 jours
    re.compile(
        r"(\d+(?:[.,]\d+)?)\s*%\s*(?:discount|escompte)?\s*(?:if paid|si pay[ée]e?)?\s*(?:within|in|dans les|sous)?"
        r"\s*(\d+)\s*(?:days?|jours?)",
        re.I,
    ),
]
_NET = [
    re.compile(r"\bn(?:et)?\s*/?\s*(\d{1,3})\b", re.I),
    re.compile(r"(\d{1,3})\s*(?:days?|jours?)\b", re.I),
]


@dataclass(frozen=True)
class Terms:
    net_days: int | None = None
    discount_pct: float | None = None  # 2.0 for 2%
    discount_days: int | None = None
    on_receipt: bool = False


@dataclass(frozen=True)
class Payment:
    due: dt.date | None
    source: str  # "printed", "terms", "default" or ""
    terms: Terms
    discount_by: dt.date | None = None
    discount_amount: float = 0.0

    def days_left(self, today: dt.date | None = None) -> int | None:
        return None if self.due is None else (self.due - (today or dt.date.today())).days

    def discount_open(self, today: dt.date | None = None) -> bool:
        return self.discount_by is not None and self.discount_by >= (today or dt.date.today())


def parse_terms(text: str | None) -> Terms:
    text = (text or "").strip()
    if not text:
        return Terms()
    for pattern in _DISCOUNT:
        m = pattern.search(text)
        if m:
            pct = float(m.group(1).replace(",", "."))
            days = int(m.group(2))
            net = int(m.group(3)) if m.lastindex and m.lastindex >= 3 and m.group(3) else None
            if net is None:  # "2% 10 days, net 30" written the other way round
                rest = text[m.end() :]
                n = _NET[0].search(rest) or _NET[1].search(rest)
                net = int(n.group(1)) if n else None
            if 0 < pct < 20 and 0 < days <= 60:
                return Terms(net_days=net, discount_pct=pct, discount_days=days)
    if _RECEIPT.search(text):
        return Terms(net_days=0, on_receipt=True)
    for pattern in _NET:
        m = pattern.search(text)
        if m and 0 <= int(m.group(1)) <= 180:
            return Terms(net_days=int(m.group(1)))
    return Terms()


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10]) if value else None
    except ValueError:
        return None


def payment(coding: dict[str, Any], default_days: int = DEFAULT_TERMS_DAYS) -> Payment:
    """Due date and discount for an invoice (``coding`` as stored: ai_output / final_output)."""
    terms = parse_terms(coding.get("payment_terms"))
    invoice_date = _date(coding.get("invoice_date"))
    printed = _date(coding.get("due_date"))
    if printed:
        due, source = printed, "printed"
    elif invoice_date and terms.net_days is not None:
        due, source = invoice_date + dt.timedelta(days=terms.net_days), "terms"
    elif invoice_date:
        due, source = invoice_date + dt.timedelta(days=default_days), "default"
    else:
        due, source = None, ""
    discount_by, amount = None, 0.0
    total = float(coding.get("grand_total") or 0)
    if terms.discount_pct and terms.discount_days is not None and invoice_date and total > 0:
        discount_by = invoice_date + dt.timedelta(days=terms.discount_days)
        amount = round(float(coding.get("subtotal") or total) * terms.discount_pct / 100, 2)
    return Payment(due, source, terms, discount_by, amount)


def describe(p: Payment, today: dt.date | None = None) -> str:
    """'Due in 5 days (Oct 14)', 'Overdue by 3 days', 'Due today'..."""
    left = p.days_left(today)
    if left is None:
        return "No due date"
    when = f"{p.due:%b} {p.due.day}"
    if left < 0:
        return f"Overdue by {-left} day{'s' if left != -1 else ''} ({when})"
    if left == 0:
        return f"Due today ({when})"
    return f"Due in {left} day{'s' if left != 1 else ''} ({when})"


def payment_findings(
    coding: dict[str, Any], default_days: int = DEFAULT_TERMS_DAYS, today: dt.date | None = None
) -> list[tuple[str, str, str]]:
    """(severity, code, message) for ``validate_coding``: discounts on offer, due dates passed or odd."""
    today = today or dt.date.today()
    p = payment(coding, default_days)
    findings: list[tuple[str, str, str]] = []
    invoice_date = _date(coding.get("invoice_date"))
    if p.source == "printed" and invoice_date and p.due and p.due < invoice_date:
        findings.append((WARNING, "DUE_BEFORE_INVOICE", f"due date {p.due} is before the invoice date {invoice_date}"))
    if p.discount_open(today) and p.discount_by is not None:
        findings.append(
            (INFO, "DISCOUNT_AVAILABLE",
             f"{p.terms.discount_pct:g}% early-payment discount (about {p.discount_amount:,.2f}) if paid by "
             f"{p.discount_by:%b} {p.discount_by.day}")
        )  # fmt: skip
    left = p.days_left(today)
    if left is not None and left < 0 and float(coding.get("grand_total") or 0) > 0:
        findings.append((INFO, "PAYMENT_OVERDUE", f"due {p.due} ({-left} days ago): late fees may apply"))
    return findings
