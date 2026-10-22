"""Payment terms: when an invoice is due and whether an early-payment discount is on offer.

Terms are read as printed ("Net 30", "2/10 Net 30", "1% 15 days, net 45", "Due on receipt",
"Payable dans les 30 jours"...). The due date is the one printed on the invoice when there is one,
otherwise the invoice date plus the net days of the terms, otherwise the organisation's default
(Settings, 30 days unless changed).
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass, replace
from typing import Any

DEFAULT_TERMS_DAYS = 30
WARNING, INFO = "warning", "info"
DUE_SOON_DAYS = 7

_RECEIPT = re.compile(
    r"(on|upon|due on|payable on|at)\s+receipt|sur\s+r[ée]ception|[àa]\s+r[ée]ception|comptant|cash on delivery|"
    r"\bcod\b|immediate(ly)?|due now",
    re.IGNORECASE,
)
_EOM = re.compile(r"\b(eom|end of (the )?month|fin de mois)\b", re.I)
# A discount: 2/10 net 30, 2/10 EOM net 30, 2/10 EOM, 1.5/15 n60 (a net part or EOM is required, and the rate
# must look like a discount, so "Due 10/15 net 45" stays a date), 2% 10 net 30, 2% discount if paid within 10 days,
# "Net 30, 2% 10" (the word "days" may be left out).
_SLASH = re.compile(
    r"(?<![\d/.,])(\d(?:[.,]\d+)?)\s*%?\s*/\s*(\d+)\s*(?:days?)?\s*(?:eom\b)?\s*[,;]?\s*(?:n(?:et)?\s*[:/\-–]?\s*(\d+)|(?<=eom))",
    re.I,
)
_DISCOUNT = [
    _SLASH,
    re.compile(r"(?<![\d/.,])(\d+(?:[.,]\d+)?)\s*%\s*(\d+)\s*[,;]?\s*n(?:et)?\s*[:/\-–]?\s*(\d+)", re.I),
    re.compile(
        r"(?<![\d/.,])(\d+(?:[.,]\d+)?)\s*%\s*(?:discount|escompte)?\s*(?:if paid|si pay[ée]e?)?"
        r"\s*(?:within|in|dans les|sous)?\s*(\d+)\b(?!\s*%|[.,]\d)"
        r"(?!\s*(?:months?|mois|years?|yrs?|ans?|ann[ée]es?|weeks?|wks?|semaines?)\b)\s*(?:days?|jours?)?",
        re.I,
    ),
]
# A fraction of a percent: "1/2% 10 net 30" is a 0.5% discount (never 2%).
_FRACTION_PCT = re.compile(r"(?<![\d/.,])(\d)\s*/\s*(\d)\s*%")
# A rate charged on late payment, not a discount: "1.5% 1 month interest", "2% 30 days past due".
_INTEREST = re.compile(
    r"interest|int[ée]r[êe]ts?|per\s+month|par\s+mois|monthly|\blate\b|overdue|past\s+due|retard|arrears|penalt|"
    r"p[ée]nalit",
    re.I,
)
# Where one clause of the terms ends: "," ";" a full stop (not a decimal point) or a spaced dash.
_CLAUSE_END = re.compile(r"[,;]|\.(?!\d)|\s[-–—/]\s")


def _clause(text: str, start: int, end: int) -> str:
    """The clause of ``text`` around ``text[start:end]`` (the whole of "Interest of 2% 30 days after due date")."""
    before = [m.end() for m in _CLAUSE_END.finditer(text, 0, start)]
    after = _CLAUSE_END.search(text, end)
    return text[before[-1] if before else 0 : after.start() if after else len(text)]


def _discount_clause(text: str, start: int, end: int) -> str:
    """The clause around a discount, for the interest words that would make it a late-payment rate. When another
    rate follows in the same clause ("2/10 net 30 interest 1.5% per month"), the words after the discount are that
    rate's, so the clause stops at the discount."""
    before = [m.end() for m in _CLAUSE_END.finditer(text, 0, start)]
    after = _CLAUSE_END.search(text, end)
    first, last = before[-1] if before else 0, after.start() if after else len(text)
    return text[first : end if "%" in text[end:last] else last]


def _eom(text: str) -> bool:
    """EOM terms ("Net 30 EOM", "fin de mois"). End of month in a late-payment clause ("interest charged at end of
    month on overdue accounts") says when interest is charged, not where the terms count from."""
    for m in _EOM.finditer(text):
        before = [c.end() for c in _CLAUSE_END.finditer(text, 0, m.start())]
        if not _INTEREST.search(text, before[-1] if before else 0, m.start()):
            return True
    return False


# "2/10 EOM" prints no net days: the usual reading is that the net amount is due 20 days after the discount date.
EOM_NET_AFTER_DISCOUNT = 20
_MONTHS = r"(?:months?|mois)\b"
_WEEKS = r"(?:weeks?|wks?|semaines?)\b"
# (pattern, days in each unit it counts). "Net 1 month", "Net 2 weeks" and "Net 10 prox" are not 1, 2 or 10 days.
# "Net 45", "Net: 45", "Net - 45", "NET-45", "N45"; then "30 EOM" and "EOM 30" (days after the end of the month: the
# EOM itself is read by ``_eom``, D-62), days, "45 net" and weeks.
_NET = [
    (re.compile(r"\bn(?:et)?\s*[:/\-–]?\s*(\d{1,3})\b(?!\s*(?:" + _MONTHS + "|" + _WEEKS + r"|prox|%))", re.I), 1),
    (re.compile(r"(?<![\d/.,%])\b(\d{1,3})\s*(?:days?\s*)?eom\b|\beom\s*[:\-–]?\s*(\d{1,3})\b(?!\s*(?:%|/))", re.I), 1),
    (re.compile(r"(\d{1,3})\s*(?:days?|jours?)\b", re.I), 1),
    (re.compile(r"(?<![\d/.,%])\b(\d{1,3})\s*net\b", re.I), 1),
    (re.compile(r"(\d{1,2})\s*" + _WEEKS, re.I), 7),
]
# "Net 10 prox" (proximo): due on the 10th of the month after the invoice's. Not a discount's day ("2/10th prox").
_PROX = re.compile(r"(?<![\d/.,])\b(?:n(?:et)?\s*/?\s*)?(\d{1,2})(?:st|nd|rd|th)?\s*prox(?:imo)?\b", re.I)
# A discount by a day of the next month: "2/10th prox net 30th", "2% 10th prox", "1/10 prox net 25 prox".
_PROX_DISCOUNT = re.compile(
    r"(?<![\d/.,])(\d(?:[.,]\d+)?)\s*(?:%\s*/?|/)\s*(\d{1,2})(?:st|nd|rd|th)?\s*prox(?:imo)?\b", re.I
)
# After a prox discount, "net 30th" is the 30th of the next month (a plain "net 30" stays 30 days).
_NET_ORDINAL = re.compile(r"\bn(?:et)?\s*/?\s*(\d{1,2})(?:st|nd|rd|th)\b", re.I)
# Weeks, counted as days before anything else is read: "2% 2 weeks net 30" is a 14-day discount.
_IN_WEEKS = re.compile(r"(?<![\d/.,])\b(\d{1,2})\s*" + _WEEKS, re.I)
# Net months ("Net 1 month", "net 2 mois"): calendar months from the invoice date.
_NET_MONTHS = re.compile(r"\bn(?:et)?\s*/?\s*(\d{1,2})\s*" + _MONTHS, re.I)


def _net_months(text: str) -> int | None:
    for m in _NET_MONTHS.finditer(text):
        if not _INTEREST.search(_clause(text, m.start(), m.end())) and 0 < int(m.group(1)) <= 6:
            return int(m.group(1))
    return None


def _add_months(day: dt.date, months: int) -> dt.date:
    """The same day ``months`` calendar months later, or the last day of a shorter month (Jan 31 + 1 -> Feb 28)."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    if year > dt.MAXYEAR:
        raise OverflowError("date value out of range")
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _net_days(text: str, *, outside_interest: bool = False) -> int | None:
    """``outside_interest``: days in a late-payment clause ("2% per month on accounts over 30 days") are left out:
    they say when interest starts, which is the net days only when nothing else says when it is due."""
    for pattern, unit in _NET:
        for m in pattern.finditer(text):
            if outside_interest and pattern is not _NET[0][0] and _INTEREST.search(_clause(text, m.start(), m.end())):
                continue
            n = int(next(g for g in m.groups() if g is not None))
            if 0 <= n * unit <= 180:
                return n * unit
    return None


def _prox_discount(text: str) -> Terms | None:
    """A discount by a day of the next month, with the net due on a later prox day, in net days or (none printed)
    on the discount's day."""
    for m in _PROX_DISCOUNT.finditer(text):
        if _INTEREST.search(_discount_clause(text, m.start(), m.end())):
            continue
        pct, day = float(m.group(1).replace(",", ".")), int(m.group(2))
        if not (0 < pct < 20 and 1 <= day <= 31):
            continue
        rest = text[m.end() :]
        ordinal = _NET_ORDINAL.search(rest)
        net_day = _prox_day(rest) or (int(ordinal.group(1)) if ordinal and 1 <= int(ordinal.group(1)) <= 31 else None)
        if net_day:
            return Terms(net_days=30, discount_pct=pct, discount_prox_day=day, prox_day=net_day)
        net = _net_days(rest, outside_interest=True)
        if net is not None:
            return Terms(net_days=net, discount_pct=pct, discount_prox_day=day)
        return Terms(net_days=30, discount_pct=pct, discount_prox_day=day, prox_day=day)
    return None


def _prox_day(text: str) -> int | None:
    for m in _PROX.finditer(text):
        if not _INTEREST.search(_clause(text, m.start(), m.end())) and 1 <= int(m.group(1)) <= 31:
            return int(m.group(1))
    return None


@dataclass(frozen=True)
class Terms:
    net_days: int | None = None
    discount_pct: float | None = None  # 2.0 for 2%
    discount_days: int | None = None
    on_receipt: bool = False
    eom: bool = False  # counted from the end of the invoice's month ("Net 30 EOM")
    net_months: int | None = None  # "Net 1 month": calendar months (net_days then holds about 30 a month)
    prox_day: int | None = None  # "Net 10 prox": that day of the next month (net_days then holds about 30)
    discount_prox_day: int | None = None  # "2/10th prox": the discount runs to that day of the next month


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
    eom = _eom(text)
    text = _FRACTION_PCT.sub(
        lambda m: f"{int(m.group(1)) / int(m.group(2)):g}%" if int(m.group(2)) else m.group(0), text
    )
    text = _IN_WEEKS.sub(lambda m: f"{int(m.group(1)) * 7} days", text)
    if prox_discount := _prox_discount(text):
        return prox_discount
    months = _net_months(text)
    for pattern in _DISCOUNT:
        for m in pattern.finditer(text):
            if _INTEREST.search(_discount_clause(text, m.start(), m.end())):
                continue  # a late-payment rate ("1.5% per month on overdue accounts"), not a discount
            pct = float(m.group(1).replace(",", "."))
            days = int(m.group(2))
            net = int(m.group(3)) if m.lastindex and m.lastindex >= 3 and m.group(3) else None
            if net is None and months:  # "2% 10 days, net 1 month"
                net = 30 * months
            if net is None:  # "Net 45, 1% 15 days" or "2% 10 days, net 30": the net days are elsewhere
                net = _net_days(text[: m.start()] + " " + text[m.end() :])
            if net is None and eom:  # "2/10 EOM": 2% by the 10th after month end, the net 20 days later
                net = days + EOM_NET_AFTER_DISCOUNT
            if 0 < pct < 20 and 0 < days <= 60:
                in_months = months if months and net == 30 * months else None
                return Terms(net_days=net, discount_pct=pct, discount_days=days, eom=eom, net_months=in_months)
    # Before "on receipt": "Net 30 days upon receipt of invoice" is net 30. "Due on receipt, 2% per month on accounts
    # over 30 days" is due on receipt: the days of an interest clause count only when nothing else gives the terms.
    if months:
        return Terms(net_days=30 * months, eom=eom, net_months=months)
    net = _net_days(text, outside_interest=True)
    if net is not None:
        return Terms(net_days=net, eom=eom)
    if prox := _prox_day(text):
        return Terms(net_days=30, prox_day=prox)
    if _RECEIPT.search(text):
        return Terms(net_days=0, on_receipt=True)
    net = _net_days(text)
    if net is not None:
        return Terms(net_days=net, eom=eom)
    # "Net EOM", "Due EOM": no days printed, so due at the end of the invoice's month.
    return Terms(net_days=0, eom=True) if eom else Terms()


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10]) if value else None
    except ValueError:
        return None


def _prox(invoice_date: dt.date, day: int) -> dt.date:
    """That day of the month after the invoice's, or the last day of a shorter one (Net 30 prox in January: Feb 28)."""
    next_month = _add_months(invoice_date.replace(day=1), 1)
    return next_month.replace(day=min(day, calendar.monthrange(next_month.year, next_month.month)[1]))


def _start(invoice_date: dt.date, terms: Terms) -> dt.date:
    """Where the terms count from: the invoice date, or the end of its month for EOM terms."""
    if not terms.eom:
        return invoice_date
    return (invoice_date.replace(day=1) + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)


def payment(coding: dict[str, Any], default_days: int = DEFAULT_TERMS_DAYS, vendor_terms: str = "") -> Payment:
    """Due date and discount for an invoice (``coding`` as stored: ai_output / final_output). Terms printed on
    the invoice come first, then ``vendor_terms`` (the vendor master's), then ``default_days``."""
    printed_terms = (coding.get("payment_terms") or "").strip()
    terms = parse_terms(printed_terms)
    from_vendor = False
    if vendor_terms and terms.net_days is None and not terms.on_receipt:  # none printed, or unreadable ("As agreed")
        vendor = parse_terms(vendor_terms)
        if vendor.net_days is not None or vendor.on_receipt:
            if terms.discount_pct:  # "2% 10 days" printed: the vendor master gives the net days, not the discount
                vendor = replace(
                    vendor, discount_pct=terms.discount_pct, discount_days=terms.discount_days,
                    discount_prox_day=terms.discount_prox_day,
                )  # fmt: skip
            terms, from_vendor = vendor, True
    invoice_date = _date(coding.get("invoice_date"))
    try:
        if invoice_date:  # a placeholder 9999-12-31: nothing can be counted from it
            _start(invoice_date, terms) + dt.timedelta(days=max(terms.net_days or 0, terms.discount_days or 0,
                                                                default_days, 0))  # fmt: skip
            if terms.net_months:
                _add_months(_start(invoice_date, terms), terms.net_months)
            for day in (terms.prox_day, terms.discount_prox_day):
                if day:
                    _prox(invoice_date, day)
    except OverflowError:
        invoice_date = None
    printed = _date(coding.get("due_date"))
    if printed:
        due, source = printed, "printed"
    elif invoice_date and terms.net_days is not None:
        start = _start(invoice_date, terms)
        if terms.prox_day:
            due = _prox(invoice_date, terms.prox_day)
        elif terms.net_months:
            due = _add_months(start, terms.net_months)
        else:
            due = start + dt.timedelta(days=terms.net_days)
        source = "vendor" if from_vendor else "terms"
    elif invoice_date:
        due, source = invoice_date + dt.timedelta(days=default_days), "default"
    else:
        due, source = None, ""
    discount_by, amount = None, 0.0
    total = float(coding.get("grand_total") or 0)
    if terms.discount_pct and terms.discount_prox_day and invoice_date and total > 0:
        discount_by = _prox(invoice_date, terms.discount_prox_day)
        amount = round(float(coding.get("subtotal") or total) * terms.discount_pct / 100, 2)
    elif terms.discount_pct and terms.discount_days is not None and invoice_date and total > 0:
        discount_by = _start(invoice_date, terms) + dt.timedelta(days=terms.discount_days)
        amount = round(float(coding.get("subtotal") or total) * terms.discount_pct / 100, 2)
    return Payment(due, source, terms, discount_by, amount)


LONG_OVERDUE_DAYS = 60  # past this, "Overdue since Dec 2012", not "Overdue by 5042 days" (B-469)
OLD_INVOICE_DAYS = 365  # an invoice dated more than this before today is flagged: a misread date, or a very late bill


def overdue_since(due: dt.date) -> str:
    """'Dec 2012': the month a long-overdue bill fell due."""
    return f"{due:%b %Y}"


def describe(p: Payment, today: dt.date | None = None) -> str:
    """'Due in 5 days (Oct 14)', 'Overdue by 3 days (Oct 10)', 'Overdue since Dec 2012', 'Due today'..."""
    left = p.days_left(today)
    if left is None:
        return "No due date"
    when = f"{p.due:%b} {p.due.day}"
    if left < -LONG_OVERDUE_DAYS:
        return f"Overdue since {overdue_since(p.due)}"
    if left < 0:
        return f"Overdue by {-left} day{'s' if left != -1 else ''} ({when})"
    if left == 0:
        return f"Due today ({when})"
    return f"Due in {left} day{'s' if left != 1 else ''} ({when})"


def payment_findings(
    coding: dict[str, Any], default_days: int = DEFAULT_TERMS_DAYS, today: dt.date | None = None,
    vendor_terms: str = "", received: dt.date | None = None,
) -> list[tuple[str, str, str]]:  # fmt: skip
    """(severity, code, message) for ``validate_coding``: discounts on offer, due dates passed or odd.
    ``received``: the day AP Coder read the invoice (default ``today``): an invoice dated more than a year before it
    is noted (OLD_INVOICE_DATE, information only, B-476)."""
    today = today or dt.date.today()
    p = payment(coding, default_days, vendor_terms)
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
    if p.source == "default" and float(coding.get("grand_total") or 0) > 0:  # never silent (B-663)
        printed = (coding.get("payment_terms") or "").strip()
        what = f"Payment terms “{printed}” not understood" if printed else "Payment terms not found"
        findings.append((INFO, "TERMS_NOT_FOUND", f"{what}: the due date assumes {default_days} days (Settings → "
                         "Review, Days to pay). Check the invoice."))  # fmt: skip
    left = p.days_left(today)
    if left is not None and left < 0 and float(coding.get("grand_total") or 0) > 0:
        ago = f"overdue since {overdue_since(p.due)}" if left < -LONG_OVERDUE_DAYS else f"{-left} days ago"
        findings.append((INFO, "PAYMENT_OVERDUE", f"due {p.due} ({ago}): late fees may apply"))
    # Information only: it blocks no approval, bulk or touchless; a credit note is often old on purpose (B-476).
    received = received or today
    if invoice_date and (received - invoice_date).days > OLD_INVOICE_DAYS and float(coding.get("grand_total") or 0) > 0:
        findings.append((INFO, "OLD_INVOICE_DATE",
                         f"dated {invoice_date}, more than a year before it was received: check the date on the "
                         "page"))  # fmt: skip
    return findings
