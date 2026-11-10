"""A small made-up company for the tests of whole account strings (D-212): a chart of accounts on a few business
units, a journal built by hand, and an invoice as the local reader read it. Every number and name is invented."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from ap_coder.core.chart import BuRow, ChartRow, split_account, split_code
from ap_coder.erp import journal as jr

MAIN, FACILITIES, IT, CLOSED, RETIRED = "0031061010", "0031061020", "04407711", "0031061060", "0031061058"
UNITS = {MAIN: ("General Administration", "ACT"), FACILITIES: ("Facilities", "ACT"), IT: ("IT - Service Desk", "ACT"),
         CLOSED: ("Year 2000 Readiness", "INA"), RETIRED: ("*** DO NOT USE ***", "ACT")}  # fmt: skip
# object.subsidiary: (its description, the business units that have it). "7340" is a title, not posted to.
ACCOUNTS = {
    "7340": ("Cleaning Services", (MAIN, FACILITIES)),
    "7340.3307": ("Janitorial Contract", (MAIN, FACILITIES, CLOSED, RETIRED)),
    "7340.3313": ("Carpet & Window Cleaning", (MAIN, FACILITIES)),
    "7340.9710": ("Recovery-Cleaning Services", (MAIN,)),
    "7420.4207": ("Courier - Domestic", (MAIN, FACILITIES)),
    "7420.4213": ("Courier - Cross Border", (MAIN,)),
    "7510.5107": ("Cell Phones & Plans", (MAIN, FACILITIES, IT)),
    "7550.5507": ("Managed Services", (IT,)),
    "7410.4107": ("Stationery & Desk Supplies", (MAIN, FACILITIES)),
    "2110": ("Trade Payables", (MAIN,)),
}  # fmt: skip
TITLES = {"7340"}


def load_chart(store: Any, home: str = MAIN) -> Any:
    """The chart and the business units in ``store`` (the recovery subsidiary and the payable object never offered,
    ``home`` the home business unit); returns the reference data."""
    store.import_business_units([BuRow(code, name, "00310", "IS", status) for code, (name, status) in UNITS.items()])
    rows = [ChartRow(unit, "", "", name, 3) for unit, (name, _) in UNITS.items()]
    for code, (description, units) in ACCOUNTS.items():
        obj, subsidiary = split_code(code)
        rows += [ChartRow(unit, obj, subsidiary, description, 8, "N" if code in TITLES else "",
                          "Cleaning Services" if obj == "7340" else "") for unit in units]  # fmt: skip
    store.import_chart(rows)
    store.save_chart_settings(replace(store.chart_settings(), never_offered=("*.9710", "2110"),
                                      home_business_unit=home))  # fmt: skip
    return store.reference_data()


def load_journal(store: Any, vouchers: list[tuple[Any, ...]], name: str = "journal.csv") -> dict[str, Any]:
    """A journal of ``vouchers`` in ``store``: each (supplier number, supplier name as printed, G/L date, [(account
    string, amount, remark), ...]); the payable line of each is added. Returns ``Store.import_journal``'s result."""
    made, row = [], 8
    for number, printed, day, lines in vouchers:
        coded = []
        for account, amount, remark in lines:
            unit, code = split_account(account)
            coded.append(jr.Line(row, account, unit, *split_code(code), "", float(amount), remark, jr.CODING))
            row += 1
        gross = round(sum(line.amount for line in coded), 2)
        payable = jr.Line(row, f"{MAIN}.2110", MAIN, "2110", "", "Trade Payables", -gross, "", jr.PAYABLE)
        row += 1
        made.append(jr.Voucher(coded[0].row, "PV", "00310", str(day), str(number), printed, (*coded, payable), gross,
                               0.0, False, gross < 0, True, False))  # fmt: skip
    days = sorted(v.gl_date for v in made)
    coding = sum(len(v.lines) - 1 for v in made)
    totals = jr.Totals(len(made), coding + len(made), coding, len(made), 0, days[0], days[-1], 0.0, 0.0, None, None,
                       False)  # fmt: skip
    digest = hashlib.sha256(repr((name, vouchers)).encode()).hexdigest()
    return store.import_journal(jr.Journal(tuple(made), (), totals), source_name=name, source_hash=digest)


def monthly(number: Any, printed: str, account: str, remark: str, months: int, amount: float = 100.0,
            year: int = 2025) -> list[tuple[Any, ...]]:  # fmt: skip
    """One voucher a month to one account, the remark with its month ("{month} janitorial")."""
    return [(number, printed, dt.date(year, m, 15), [(account, amount, remark.format(month=dt.date(year, m, 1)
                                                                                      .strftime("%b")))])
            for m in range(1, months + 1)]  # fmt: skip


def captured(vendor: str, *lines: str, number: str = "INV-1") -> Any:
    """An invoice of ``vendor`` with these line descriptions, as the local reader read it (every value right)."""
    from ap_coder.bench.coding import _capture

    read = [SimpleNamespace(text=text, quantity=1.0, unit_price=100.0, amount=100.0) for text in lines]
    return _capture(vendor, number, dt.date(2026, 1, 20), read)


def approve(store: Any, tmp_path: Any, coding: Any, accounts: list[str], reviewer: str = "pat") -> int:
    """AP approves ``coding`` with each line on the account string given; returns the invoice's id."""
    output = coding.model_dump(mode="json")
    invoice_id = store.add_invoice(tmp_path / f"{coding.invoice_number}.pdf", output, None)
    final = coding.model_dump(mode="json")
    for li, account in zip(final["line_items"], accounts, strict=True):
        li["predicted_cost_center"], li["predicted_gl_code"] = split_account(account)
    store.approve_invoice(invoice_id, final, reviewer)
    return invoice_id
