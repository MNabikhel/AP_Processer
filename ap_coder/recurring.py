"""Recurring vendors: who bills on a regular rhythm, when the next invoice is due, and which are late.

A vendor is recurring once it has billed at least three times at a steady interval (weekly to
quarterly). A late invoice is worth chasing (a lost invoice means a late payment) and worth
accruing at month-end, so the Vendors page lists them with the usual amount.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass
from typing import Any

MIN_INVOICES = 3
MIN_DAYS, MAX_DAYS = 5, 120
STEADY_SHARE = 0.75  # at least 3 in 4 gaps close to the usual one
DUE_SOON_DAYS = 5

ON_TRACK, DUE_SOON, LATE = "on_track", "due_soon", "late"


@dataclass
class Recurring:
    vendor_key: str
    vendor_name: str
    cadence: str
    interval_days: int
    invoices: int
    last_date: dt.date
    next_date: dt.date
    typical_total: float
    currency: str
    status: str
    days_late: int = 0


def cadence_label(days: float) -> str:
    for label, low, high in (
        ("Weekly", 6, 8), ("Every 2 weeks", 13, 16), ("Monthly", 26, 35), ("Every 2 months", 55, 66),
        ("Quarterly", 84, 98),
    ):  # fmt: skip
        if low <= days <= high:
            return label
    return f"Every {round(days)} days"


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def detect(rows: list[dict[str, Any]], today: dt.date | None = None) -> list[Recurring]:
    """``rows``: one per invoice with vendor_key, vendor_name, invoice_date, grand_total, currency.
    Credit notes are ignored. Late vendors first, then by next expected date."""
    today = today or dt.date.today()
    by_vendor: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("vendor_key") and (r.get("grand_total") or 0) > 0 and _date(r.get("invoice_date")):
            by_vendor.setdefault(r["vendor_key"], []).append(r)
    found = []
    for key, invoices in by_vendor.items():
        dates = sorted({_date(r["invoice_date"]) for r in invoices})
        if len(dates) < MIN_INVOICES:
            continue
        gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
        usual = statistics.median(gaps)
        if not MIN_DAYS <= usual <= MAX_DAYS:
            continue
        steady = sum(1 for g in gaps if 0.6 * usual <= g <= 1.5 * usual)
        if steady / len(gaps) < STEADY_SHARE:
            continue
        last = dates[-1]
        next_date = last + dt.timedelta(days=round(usual))
        grace = max(5, round(usual * 0.25))
        late_by = (today - next_date).days
        status = LATE if late_by > grace else DUE_SOON if late_by >= -DUE_SOON_DAYS else ON_TRACK
        latest = max(invoices, key=lambda r: r["invoice_date"])
        found.append(
            Recurring(
                vendor_key=key,
                vendor_name=latest.get("vendor_name") or key,
                cadence=cadence_label(usual),
                interval_days=round(usual),
                invoices=len(dates),
                last_date=last,
                next_date=next_date,
                typical_total=statistics.median(r["grand_total"] for r in invoices),
                currency=latest.get("currency") or "",
                status=status,
                days_late=max(late_by, 0) if status == LATE else 0,
            )
        )
    order = {LATE: 0, DUE_SOON: 1, ON_TRACK: 2}
    return sorted(found, key=lambda r: (order[r.status], r.next_date))
