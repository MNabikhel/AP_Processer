"""A pilot day (``python -m ap_coder.bench pilotday [--quick]``, D-275): what the pilot tester will do, end to end
through the real app, on a made-up construction group of seven JD Edwards companies (``jde_group.construction_books``:
three Canadian ones in Alberta, British Columbia and Ontario, two US ones, a USD company whose address says CA and one
with no country; five of them in the pilot), scored against the truth each invoice should end at.

1. **Loading**, as the guide says (docs/PROTOTYPE.md, section 3), in a new laptop data folder (``web.core.first_run``:
   the simple screens, JD Edwards keying): ONE master workbook of the build sheet's reports (``jde_group.
   group_workbook_xlsx``: report 9 titled as ReportsNow titles it by default, long titles wrapped over two lines, the
   payment terms by JD Edwards' field names) uploaded on JD Edwards files and loaded with *Load all*; the whole chart
   as ReportsNow's Unicode text (UTF-16 with its byte order mark, tab-delimited, quotation marks in descriptions, the
   business units right-aligned, posting edit codes JD Edwards' own list does not have) loaded with ``python -m
   ap_coder chart import --chart FILE.txt`` in a process of its own; then the settings a tester makes on the pages:
   the province and the companies' names (Settings → Review), PO-matched vouchers (Settings → JD Edwards E1) and the
   companies in the pilot (Accounts AP posts to).
2. **The day's invoices** (``CATEGORIES``, 200 in a full run, about 35 with ``--quick``; ``--scanned`` of them, 10% by
   default, as scans): PDFs of the benchmark's renderer in each supplier's own style, and a few set out by hand (the
   one-row totals table, the company's name printed where a supplier's goes); uploaded on Add invoices, read and coded
   in the background, the chat model answered by the benches' stand-in LM Studio (``fake_model``; it proposes no
   account: what is proposed is AP Coder's own, from the past coding and the POs).
3. **The score**, per category and per measure (``MEASURES``): the company decided, the supplier (its JD Edwards
   number), the PO lines each invoice line was matched to, the accounts proposed (a wrong one, or none where the
   history is sure, is a miss; none where a person picks is not), the taxes read, the findings that must appear and
   those that must not, whether Approve is open as it arrives; then a person's work on each invoice to approve
   (blank accounts and the company picked as the truth says, checked, approved through the routes), the export to the
   keying sheet and its rows (the supplier, the gross, the tax area and amount, each G/L line or each PO line
   matched). Every miss is listed with the invoice and what was expected and found.

Everything is made from ``--seed`` and the day (today), so a run is the same each time; the report is written to
``--out`` (``pilot_day.json``, ``pilot_day.md``)."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import content as C
from . import jde_group as G
from .fake_model import CHAT, READER, FakeModel

# The day's invoices: (category, how many in a full run, what it is).
CATEGORIES = (
    ("po", 38, "every line of an order billed, and its freight line when it has one"),
    ("po_freight_off", 8, "an order's lines and a freight charge the order does not have"),
    ("po_partial", 14, "some lines of an order, one of them in part"),
    ("po_credit", 4, "a credit for goods returned on an order"),
    ("po_subledger", 8, "an order of equipment rental: an L account, the subledger on the PO line"),
    ("office", 33, "no PO: the supplier's one account at the company's office"),
    ("credit", 3, "no PO: a credit note for goods returned"),
    ("units", 4, "no PO: fuel to a U account, its units the quantity"),
    ("subledger", 4, "no PO: equipment rental to an L account, the subledger of the past coding"),
    ("jobs", 12, "no PO: the job named on the invoice; the supplier's history on five jobs, two closed"),
    ("two_companies", 8, "a supplier billing two companies: the one named on the invoice"),
    ("undecided", 4, "a supplier billing two companies, neither named, no PO: a person picks"),
    ("intercompany", 2, "a company of the group billing another"),
    ("us", 14, "a US company in the pilot: USD, no Canadian tax"),
    ("usd_ca", 4, "a USD company whose address says CA"),
    ("outside_pilot", 8, "a company outside the pilot"),
    ("dup_erp", 6, "already in JD Edwards under the same company"),
    ("dup_other_company", 4, "its number already in JD Edwards under the supplier's other company"),
    ("dup_day_other", 2, "the number of an invoice of the day, from the same supplier to its other company"),
    ("dup_resent", 3, "an invoice of the day sent again, scanned"),
    ("multipage", 6, "lines over several pages"),
    ("several", 3, "two or three invoices in one file"),
    ("one_row", 3, "the totals printed as a table of one row"),
    ("buyer_trap", 5, "the company's name printed large at the top, the supplier's small at the foot"),
)
QUICK_SHARE = 0.15  # --quick: this share of each category, one at least
MEASURES = ("company", "supplier", "po_lines", "accounts", "tax", "findings", "held", "keying")
# Findings no clean invoice of a Canadian company in the pilot should have.
NEVER_CLEAN = ("COMPANY_UNKNOWN", "COMPANY_NOT_IN_PILOT", "US_TAX_NOT_CHECKED", "DUPLICATE_IN_ERP",
               "DUPLICATE_INVOICE", "DUPLICATE_OTHER_COMPANY", "SEVERAL_INVOICES", "JDE_SUBLEDGER_NEEDED",
               "JDE_UNITS_NEEDED", "PO_AMBIGUOUS", "PO_UNKNOWN", "PO_VENDOR_MISMATCH")  # fmt: skip
CANADIAN = ("00210", "00220", "00230")
CENT = Decimal("0.01")
DAY_LAYOUTS = ("classic_left", "vendor_right", "header_grid", "boxed_fields", "totals_left", "table_footer")
DATE_STYLES = ("iso", "mon_d_y", "month_d_y", "d_mon_y", "dd-mon-yyyy", "yyyy/mm/dd")
TERMS = {"N": C.Terms("Net 30", 30), "F": C.Terms("Net 45", 45), "T": C.Terms("2% 10, Net 15", 15)}


@dataclass(frozen=True)
class DayLine:
    """A printed line and what AP codes it to."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    uom: str = "EA"
    account: str = ""  # BusinessUnit.Object.Subsidiary
    po_line: str = ""  # the order and line it bills, "47012-2"
    sure: bool = True  # the past coding or the PO says the account: AP Coder should propose it itself
    subledger: tuple[str, str] = ("", "")
    units: Decimal | None = None

    @property
    def amount(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(CENT)


@dataclass
class DayInvoice:
    """One invoice of the day, with the truth it should end at."""

    name: str
    category: str
    supplier: int
    company: str
    number: str
    date: dt.date
    lines: list[DayLine]
    po: str = ""
    named: bool = True  # the company's name printed on it (billed to)
    layout: str = ""  # the renderer's archetype; "one_row", "buyer_trap" or "several" set out here
    scanned: bool = False
    must: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    held: bool | None = False  # Approve closed as it arrives (None: either way)
    approve: bool = True  # a person approves it today, after picking what is left to pick
    reject: str = ""  # a person rejects it, saying why
    parts: list[DayInvoice] = field(default_factory=list)  # the invoices a "several" file holds
    copy_of: str = ""  # sent again: the invoice of the day it is a scan of
    file: str = ""

    @property
    def credit(self) -> bool:
        return self.subtotal < 0

    @property
    def province(self) -> str:
        """Where it is supplied, for its taxes: the company's site; "" for a company outside Canada."""
        return G.SITES[self.company][0] if self.company in CANADIAN else ""

    @property
    def subtotal(self) -> Decimal:
        return sum((li.amount for li in self.lines), Decimal(0))

    @property
    def taxes(self) -> list[C.Tax]:
        return C.compute_taxes(self.province, self.subtotal) if self.province else []

    @property
    def total(self) -> Decimal:
        return self.subtotal + sum((t.amount for t in self.taxes), Decimal(0))

    @property
    def recovered(self) -> Decimal:
        return sum((t.amount for t in self.taxes if t.code in ("GST", "HST")), Decimal(0))

    @property
    def tax_keyed(self) -> Decimal:
        """The pay item's tax amount: the GST or HST; with British Columbia's code C, the PST too (it stays in the
        G/L lines)."""
        return sum((t.amount for t in self.taxes), Decimal(0)) if self.province == "BC" else self.recovered

    def keyed(self) -> dict[str, Decimal]:
        """{account: the amount keyed on it}: each line's amount with its share of a provincial tax (in the
        expense)."""
        provincial = sum((t.amount for t in self.taxes if t.code not in ("GST", "HST")), Decimal(0))
        out: dict[str, Decimal] = {}
        for li in self.lines:
            share = (provincial * li.amount / self.subtotal).quantize(CENT) if self.subtotal else Decimal(0)
            out[li.account] = out.get(li.account, Decimal(0)) + li.amount + share
        return out


# --- the plan of the day -------------------------------------------------------------------------------------------


class Planner:
    """The day's invoices of each category, from the books (``jde_group.GroupBooks``) and ``seed``: each order and each
    past voucher used once."""

    def __init__(self, books: G.GroupBooks, seed: int) -> None:
        self.books, self.rng = books, random.Random(f"pilot-day:{seed}")
        self.numbers: Counter[int] = Counter()
        self.used_orders: set[int] = set()
        self.used_vouchers: set[int] = set()
        self.count: Counter[str] = Counter()

    # (helpers)
    def supplier(self, number: int) -> G.GroupSupplier:
        return self.books.supplier(number)

    def of_kind(self, *kinds: str, companies: tuple[str, ...] = (), two: bool | None = None) -> list[G.GroupSupplier]:
        return [s for s in self.books.suppliers if s.kind in kinds
                and (not companies or set(s.companies) & set(companies))
                and (two is None or (len(s.companies) > 1) == two)]  # fmt: skip

    def number(self, s: G.GroupSupplier) -> str:
        self.numbers[s.number] += 1
        return f"{s.name[:2]}-{30000 + (s.number % 1000) * 37 + self.numbers[s.number]}"

    def name(self, category: str) -> str:
        self.count[category] += 1
        return f"{category}_{self.count[category]:02d}"

    def day(self) -> dt.date:
        return self.books.on - dt.timedelta(days=1 + self.rng.randrange(9))

    def words(self, s: G.GroupSupplier, k: int) -> str:
        return G.WORDS[s.code][k % 3]

    def line(self, s: G.GroupSupplier, company: str, unit: str, k: int, sure: bool = True) -> DayLine:
        obj, _, sub = s.code.partition(".")
        account = f"{unit}.{obj}.{sub}" if sub else f"{unit}.{obj}"
        quantity = Decimal(self.rng.choice((1, 2, 3, 5, 10, 12)))
        price = Decimal(str(round(self.rng.uniform(25, 900), 2)))
        ledger = ("EQ1042", "E") if s.code == "5210" else ("", "")
        units = quantity if s.code == "5230" else None
        return DayLine(self.words(s, k), quantity, price, "EA", account, sure=sure, subledger=ledger, units=units)

    def unit_of(self, s: G.GroupSupplier, company: str) -> str:
        """Where a supplier's invoices without a PO go: the office for overhead, else its one open job."""
        return G.office_of(company) if s.code[0] in "67" else G.open_jobs(company)[0]

    def order(self, s: G.GroupSupplier, company: str, freight: bool | None = None) -> tuple[int, list[G.GroupPoLine]]:
        """An open order of ``s`` in ``company`` not billed yet today (with a freight line or not, when asked)."""
        for number, lines in sorted(self.books.orders(s.number, company).items()):
            has = any(li.description == "FREIGHT" for li in lines)
            if number not in self.used_orders and (freight is None or has == freight):
                self.used_orders.add(number)
                return number, lines
        raise LookupError(f"no open order of {s.name} in {company} left")

    def invoice(self, category: str, s: G.GroupSupplier, company: str, lines: list[DayLine], **more: Any) -> DayInvoice:
        return DayInvoice(self.name(category), category, s.number, company, more.pop("number", None) or self.number(s),
                          more.pop("date", None) or self.day(), lines, **more)  # fmt: skip

    # (the categories)
    def po(self, category: str, k: int) -> DayInvoice:
        buyers = [s for s in self.of_kind("po") if s.currency == "CAD" and (category == "po_subledger") ==
                  (s.code == "5210")]  # fmt: skip
        freight = {"po_freight_off": False, "po": None}.get(category)
        for attempt in range(len(buyers) * 2):
            s = buyers[(k + attempt) % len(buyers)]
            company = s.companies[(k // len(buyers)) % len(s.companies)]
            try:
                number, ordered = self.order(s, company, freight)
                break
            except LookupError:
                continue
        else:
            raise LookupError(f"no order left for {category}")
        lines = []
        for li in ordered:
            quantity = li.ordered
            if category == "po_partial":
                if li.description == "FREIGHT" or li.line > 2:
                    continue
                quantity = (li.ordered / 2).quantize(Decimal(1)) if li.line == 1 else li.ordered
            if category == "po_credit":
                if li.line != 1:
                    continue
                quantity = -max(Decimal(1), (li.ordered / 4).quantize(Decimal(1)))
            words = li.description.title() if li.description != "FREIGHT" else "Freight"
            lines.append(DayLine(words, quantity, li.unit_cost, li.uom, li.account, f"{number}-{int(li.line)}",
                                 subledger=(li.subledger, li.subledger_type)))  # fmt: skip
        must, must_not, held, approve = ("PO_MATCHED",), NEVER_CLEAN, False, True
        if category == "po_freight_off":
            unit = ordered[0].account.split(".")[0]
            lines.append(DayLine("Freight", Decimal(1), Decimal(str(round(self.rng.uniform(45, 160), 2))), "EA",
                                 f"{unit}.5510"))  # fmt: skip
            must, held, approve = ("PO_LINE_NOT_ON_PO",), True, False
        if category in ("po_partial", "po_credit"):
            must = ()
        return self.invoice(category, s, company, lines, po=str(number), must=must,
                            must_not=(*must_not, "PO_QTY_OVER", "PO_PRICE_OVER") if category != "po_freight_off"
                            else must_not, held=held, approve=approve)  # fmt: skip

    def office(self, category: str, k: int) -> DayInvoice:
        code = {"units": ("5230",), "subledger": ("5210",)}.get(category)
        pool = [s for s in self.of_kind("office", companies=CANADIAN, two=False)
                if (s.code in code if code else s.code[0] in "67") and s.number != 4410900]  # fmt: skip
        s = pool[k % len(pool)]
        company = s.companies[0]
        unit = self.unit_of(s, company)
        count = 1 + self.rng.randrange(3)
        lines = [self.line(s, company, unit, k + n) for n in range(count)]
        return self.invoice(category, s, company, lines, must_not=NEVER_CLEAN)

    def jobs(self, k: int) -> DayInvoice:
        pool = self.of_kind("jobs")
        s = pool[k % len(pool)]
        company = s.companies[0]
        unit = G.open_jobs(company)[k % len(G.open_jobs(company))]
        lines = [self.line(s, company, unit, k + n, sure=False) for n in range(1 + self.rng.randrange(2))]
        lines = [DayLine(f"{li.description} - {G.job_name(unit)}", li.quantity, li.unit_price, li.uom, li.account,
                         sure=False) for li in lines]  # fmt: skip
        return self.invoice("jobs", s, company, lines, must_not=NEVER_CLEAN, held=None)

    def two_companies(self, category: str, k: int) -> DayInvoice:
        pool = self.of_kind("office", two=True)
        s = pool[k % len(pool)]
        company = s.companies[(k // len(pool)) % 2]
        lines = [self.line(s, company, self.unit_of(s, company), k + n) for n in range(1 + self.rng.randrange(2))]
        if category == "undecided":  # (no company: no account of one proposed)
            lines = [
                DayLine(li.description, li.quantity, li.unit_price, li.uom, li.account, sure=False) for li in lines
            ]
            quiet = tuple(c for c in NEVER_CLEAN if c != "COMPANY_UNKNOWN")
            return self.invoice(category, s, company, lines, named=False, must=("COMPANY_UNKNOWN",), must_not=quiet,
                                held=True)  # fmt: skip
        return self.invoice(category, s, company, lines, must_not=NEVER_CLEAN)

    def intercompany(self, k: int) -> DayInvoice:
        s = self.supplier(4410900)
        company = s.companies[0]
        return self.invoice("intercompany", s, company, [self.line(s, company, G.office_of(company), k)],
                            must_not=NEVER_CLEAN)  # fmt: skip

    def foreign(self, category: str, k: int) -> DayInvoice:
        companies = {"us": ("00310",), "usd_ca": ("00320",), "outside_pilot": ("00330", "00410")}[category]
        pool = [s for s in self.books.suppliers if set(s.companies) & set(companies)]
        s = pool[k % len(pool)]
        company = next(c for c in s.companies if c in companies)
        quiet = tuple(c for c in NEVER_CLEAN if c not in ("US_TAX_NOT_CHECKED", "COMPANY_NOT_IN_PILOT"))
        if s.kind == "po" and k % 2 == 0:
            number, ordered = self.order(s, company)
            lines = [
                DayLine(
                    li.description.title() if li.description != "FREIGHT" else "Freight",
                    li.ordered,
                    li.unit_cost,
                    li.uom,
                    li.account,
                    f"{number}-{int(li.line)}",
                )
                for li in ordered
            ]
            invoice = self.invoice(category, s, company, lines, po=str(number))
        else:  # (a PO supplier's job costs without a PO: its history is on several jobs, a person picks)
            sure = s.kind == "office"
            lines = [self.line(s, company, self.unit_of(s, company), k + n, sure)
                     for n in range(1 + self.rng.randrange(2))]  # fmt: skip
            invoice = self.invoice(category, s, company, lines, held=None if not sure else False)
        invoice.layout = "us_vendor"
        if category == "outside_pilot":
            invoice.must, invoice.must_not, invoice.held, invoice.approve = (
                ("COMPANY_NOT_IN_PILOT",),
                quiet,
                True,
                False,
            )
        else:
            invoice.must, invoice.must_not = ("US_TAX_NOT_CHECKED",), quiet
        return invoice

    def duplicate(self, category: str, k: int) -> DayInvoice:
        """An invoice of a past voucher: its number and lines (the same company, or the supplier's other one); one in
        three already in JD Edwards is a US company's (its voucher in US dollars)."""
        other = category == "dup_other_company"
        companies = ("00310",) if not other and k % 3 == 2 else CANADIAN
        pool = [s for s in self.of_kind("office", companies=companies, two=other) if s.code[0] in "67"
                and s.number != 4410900]  # fmt: skip
        s = pool[k % len(pool)]
        past = [v for v in self.books.vouchers if v.supplier == s.number and v.company == s.companies[0]
                and v.document not in self.used_vouchers]  # fmt: skip
        v = past[-1 - (k // len(pool))]
        self.used_vouchers.add(v.document)
        company = s.companies[1] if other else v.company
        rate = 1 + G.provincial_rate(v.company)
        lines = [DayLine(self.words(s, n), Decimal(1), (li.amount / rate).quantize(CENT), "EA",
                         li.account if not other else f"{self.unit_of(s, company)}.{li.account.split('.', 1)[1]}")
                 for n, li in enumerate(v.lines)]  # fmt: skip
        if other:
            return self.invoice(category, s, company, lines, number=v.invoice_number, date=v.invoice_date,
                                must=("DUPLICATE_OTHER_COMPANY",),
                                must_not=tuple(c for c in NEVER_CLEAN if c != "DUPLICATE_OTHER_COMPANY"))  # fmt: skip
        return self.invoice(category, s, company, lines, number=v.invoice_number, date=v.invoice_date,
                            must=("DUPLICATE_IN_ERP",), must_not=("COMPANY_UNKNOWN", "COMPANY_NOT_IN_PILOT"),
                            held=True, approve=False, reject="Already in JD Edwards")  # fmt: skip

    def multipage(self, k: int) -> DayInvoice:
        pool = [s for s in self.of_kind("office", companies=CANADIAN, two=False) if s.code in ("6110", "7140")]
        s = pool[k % len(pool)]
        company = s.companies[0]
        unit = self.unit_of(s, company)
        lines = [self.line(s, company, unit, n) for n in range(34 + self.rng.randrange(14))]
        lines = [DayLine(f"{li.description} #{4100 + n}", li.quantity, li.unit_price, li.uom, li.account)
                 for n, li in enumerate(lines)]  # fmt: skip
        return self.invoice("multipage", s, company, lines, layout="multipage", must_not=NEVER_CLEAN)

    def plan(self, counts: dict[str, int], scanned: float) -> list[DayInvoice]:
        """Every category's invoices, ``counts`` of each, in the order they arrive (the invoices sent again last);
        ``scanned`` of them as scans."""
        out: list[DayInvoice] = []
        later: list[DayInvoice] = []
        for category, _n, _words in CATEGORIES:
            for k in range(counts.get(category, 0)):
                if category.startswith("po"):
                    out.append(self.po(category, k))
                elif category in ("office", "units", "subledger"):
                    out.append(self.office(category, k))
                elif category == "jobs":
                    out.append(self.jobs(k))
                elif category in ("two_companies", "undecided"):
                    out.append(self.two_companies(category, k))
                elif category == "intercompany":
                    out.append(self.intercompany(k))
                elif category in ("us", "usd_ca", "outside_pilot"):
                    out.append(self.foreign(category, k))
                elif category == "credit":
                    made = self.office("credit", k)
                    made.lines = [DayLine(f"Returned - {li.description}", -li.quantity, li.unit_price, li.uom,
                                          li.account) for li in made.lines[:1]]  # fmt: skip
                    out.append(made)
                elif category == "dup_day_other":  # (both are bills of their own: a person approves each)
                    first = [i for i in out if i.category == "two_companies"][k]
                    s = self.supplier(first.supplier)
                    company = next(c for c in s.companies if c != first.company)
                    first.must_not = tuple(c for c in first.must_not if c != "DUPLICATE_INVOICE_OTHER_COMPANY")
                    lines = [self.line(s, company, self.unit_of(s, company), k + 7)]
                    must = ("DUPLICATE_INVOICE_OTHER_COMPANY",)
                    out.append(self.invoice(category, s, company, lines, number=first.number, must=must,
                                            must_not=NEVER_CLEAN))  # fmt: skip
                elif category in ("dup_erp", "dup_other_company"):
                    out.append(self.duplicate(category, k))
                elif category == "multipage":
                    out.append(self.multipage(k))
                elif category == "several":
                    parts = [self.office("office", k * 3 + n) for n in range(2 + k % 2)]
                    first = parts[0]
                    out.append(DayInvoice(self.name("several"), "several", first.supplier, first.company,
                                          first.number, first.date, first.lines, layout="several", parts=parts,
                                          must=("SEVERAL_INVOICES",), held=None, approve=False,
                                          reject="Several invoices in one file: to split"))  # fmt: skip
                elif category in ("one_row", "buyer_trap"):
                    pool = {"one_row": [self.supplier(4410115)],
                            "buyer_trap": [s for s in self.of_kind("office", companies=CANADIAN, two=False)
                                           if s.code[0] in "67" and s.number != 4410900]}[category]  # fmt: skip
                    s = pool[k % len(pool)]
                    company = s.companies[0]
                    unit = self.unit_of(s, company)
                    made = self.invoice(category, s, company, [self.line(s, company, unit, k + n) for n in range(2)],
                                        layout=category, must_not=NEVER_CLEAN)  # fmt: skip
                    out.append(made)
                elif category == "dup_resent":  # the k-th office invoice, its scan sent again at the end
                    first = [i for i in out if i.category == "office"][k]
                    first.must, first.held = ("DUPLICATE_INVOICE",), True  # (approved once the copy is rejected)
                    first.must_not = tuple(c for c in first.must_not if c != "DUPLICATE_INVOICE")
                    later.append(DayInvoice(self.name("dup_resent"), "dup_resent", first.supplier, first.company,
                                            first.number, first.date, first.lines, scanned=True, copy_of=first.name,
                                            must=("DUPLICATE_INVOICE",),
                                            must_not=("COMPANY_UNKNOWN", "COMPANY_NOT_IN_PILOT"), held=True,
                                            approve=False, reject="Sent again: a copy"))  # fmt: skip
        pick = random.Random(f"pilot-day-scans:{len(out)}")
        copied = {i.copy_of for i in later}
        plain = [i for i in out if i.layout not in ("one_row", "buyer_trap", "several", "multipage")
                 and i.name not in copied]  # fmt: skip
        for invoice in pick.sample(plain, min(len(plain), round(len(out) * scanned))):
            invoice.scanned = True
        return out + later


def counts_for(quick: bool) -> dict[str, int]:
    return {c: max(1, round(n * QUICK_SHARE)) if quick else n for c, n, _ in CATEGORIES}


# --- the files -----------------------------------------------------------------------------------------------------


def _party_lines(address: str) -> list[str]:
    street, _, rest = address.partition(", ")
    return [street, rest]


def _rendered(invoice: DayInvoice, books: G.GroupBooks, seed: int) -> bytes:
    """The invoice laid out by the benchmark's renderer in its supplier's own style (``generator.Renderer``), billed
    to its company at its site (or to Accounts Payable alone, when no company is named)."""
    from .generator import Inv, Renderer, Style, _header_items, _knobs

    s = books.supplier(invoice.supplier)
    rng = random.Random(f"pilot-day:{seed}:{invoice.name}")
    style = Style(rng, key=f"pilot-supplier:{seed}:{s.number}")
    foreign = s.country != "CA"
    layout = invoice.layout if invoice.layout not in ("", "several") else style("layout").choice(DAY_LAYOUTS)
    if invoice.credit:
        layout = "credit_note"
    knobs = _knobs(style("knobs"), layout)
    knobs["stub"] = False
    if s.currency != "CAD":
        knobs["currency_where"] = "header"
    vendor = C.make_vendor(style("vendor"), "us" if foreign else "en", s.province if not foreign else "ON")
    street, city, postal = s.address
    vendor.name, vendor.province, vendor.country = s.printed, s.province, s.country
    vendor.lines = [street, f"{city}, {s.province} {postal}".replace(",  ", ", ")]
    vendor.bn = s.tax_id[:9] if s.country == "CA" else ""
    vendor.qst = ""
    province = invoice.province or G.SITES[invoice.company][0] or "ON"
    customer = C.make_customer(style("customer"), province if province in C.CITIES else "ON")
    customer.name = books.company_name(invoice.company) if invoice.named else "Accounts Payable"
    customer.lines = _party_lines(G.SITES[invoice.company][1])
    if not invoice.named:
        customer.lines = ["PO Box 4410", customer.lines[-1]]
    customer.province, customer.country = G.SITES[invoice.company][0], "US" if foreign else "CA"
    lines = [C.LineItem(li.description, li.quantity, li.uom, li.unit_price, li.amount) for li in invoice.lines]
    terms = TERMS.get(s.terms, TERMS["N"])
    inv = Inv(
        layout, "credit" if invoice.credit else "invoice", "en", "US" if foreign else "CA",
        invoice.province or s.province or "WA", vendor, customer, s.currency, invoice.number, invoice.date,
        None if invoice.credit else invoice.date + dt.timedelta(days=terms.days or 30), invoice.po or None,
        None if invoice.credit else terms, style("date_style").choice(DATE_STYLES),
        C.money_style(style("money"), "en", s.currency), lines, None, invoice.taxes, invoice.subtotal,
        invoice.total, knobs, sty=style)  # fmt: skip
    inv.header = _header_items(rng, inv)
    pdf, _printed = Renderer(inv, rng).render()
    return pdf


def _page_pdf(rows: list[tuple[float, float, str, float]]) -> bytes:
    """A letter page with each text at (x, y) points from the top left, in its size."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text, size in rows:
        page.insert_text((x, y), text, fontsize=size)
    data = doc.tobytes()
    doc.close()
    return data


def _money_text(amount: Decimal) -> str:
    return f"{amount:,.2f}"


def _one_row(invoice: DayInvoice, books: G.GroupBooks) -> bytes:
    """The supplier's invoice whose totals are a table of one row (as ``tests/pdfs.one_row_totals``): a row of labels
    over a row of amounts, a cell left empty where there is none, the GST under "HST Tax" on one in two."""
    import pymupdf

    s = books.supplier(invoice.supplier)
    street, city, postal = s.address
    rows: list[tuple[float, float, str, float]] = [
        (50, 50, s.printed, 16), (50, 68, f"{street}, {city} {s.province} {postal}", 10), (400, 50, "INVOICE", 14),
        (400, 70, f"Invoice #: {invoice.number}", 10), (400, 84, f"Invoice Date: {invoice.date.isoformat()}", 10),
        (400, 98, "Terms: Net 30", 10), (50, 120, "Bill To:", 10), (50, 134, books.company_name(invoice.company), 10),
        (50, 148, G.SITES[invoice.company][1], 10)]  # fmt: skip
    heads = ((50, "Item #"), (95, "Item Description"), (275, "QTY Billed"), (318, "QTY Ordered"), (368, "Back Order"),
             (412, "Unit Price"), (455, "Per"), (480, "Credit"), (530, "Amount"))  # fmt: skip
    rows += [(x, 200, text, 8) for x, text in heads]
    for n, li in enumerate(invoice.lines):
        cells = (f"IT-{220 + n}", li.description, str(li.quantity), str(li.quantity), "0", f"{li.unit_price:.4f}",
                 "Each", "", _money_text(li.amount))  # fmt: skip
        rows += [(x + (10 if 2 <= k <= 4 else 0), 216 + 14 * n, text, 8)
                 for k, ((x, _), text) in enumerate(zip(heads, cells, strict=True)) if text]  # fmt: skip
    tax = _money_text(invoice.recovered)
    under_hst = int(invoice.name[-2:]) % 2 == 0
    labels = ("Line-Item Total", "Freight Amount", "GST Tax", "HST Tax", "Sub Total", "Deposits", "Credits/Discounts",
              "Amount Due:")  # fmt: skip
    values = (_money_text(invoice.subtotal), "", "" if under_hst else tax, tax if under_hst else "",
              _money_text(invoice.total), "", "", f"{_money_text(invoice.total)} CAD")  # fmt: skip
    width = 540 / len(labels)
    for n, cells in enumerate(zip(labels, values, strict=True)):
        for text, y in zip(cells, (300, 314), strict=True):
            if text:
                left, room = 36 + n * width + 2, width - 4 - pymupdf.get_text_length(text, fontsize=8)
                rows.append((left + (0 if y == 300 else room), y, text, 8))
    rows.append((50, 360, f"GST/HST Registration No: {s.tax_id[:9]} {s.tax_id[9:]}", 8))
    return _page_pdf(rows)


def _buyer_trap(invoice: DayInvoice, books: G.GroupBooks) -> bytes:
    """An invoice whose top holds the company's name large, as an envelope's window shows it (no label), and the
    supplier's name only in the remit-to line at the foot, beside its GST/HST number."""
    s = books.supplier(invoice.supplier)
    street, city, postal = s.address
    company = books.company_name(invoice.company)
    rows: list[tuple[float, float, str, float]] = [
        (50, 60, company.upper(), 16), (50, 80, G.SITES[invoice.company][1], 10),
        (50, 94, "Attn: Accounts Payable", 10), (400, 60, "INVOICE", 16),
        (400, 80, f"Invoice No: {invoice.number}", 10), (400, 94, f"Date: {invoice.date.isoformat()}", 10),
        (400, 108, "Terms: Net 30", 10),
        (50, 180, "Description", 10), (330, 180, "Qty", 10), (400, 180, "Price", 10),
        (500, 180, "Amount", 10)]  # fmt: skip
    for n, li in enumerate(invoice.lines):
        y = 198 + 16 * n
        rows += [(50, y, li.description, 10), (330, y, str(li.quantity), 10), (400, y, f"{li.unit_price:,.2f}", 10),
                 (500, y, _money_text(li.amount), 10)]  # fmt: skip
    y = 198 + 16 * len(invoice.lines) + 30
    rows += [(400, y, "Subtotal", 10), (500, y, _money_text(invoice.subtotal), 10)]
    for k, tax in enumerate(invoice.taxes, 1):
        rows += [(400, y + 16 * k, f"{tax.code} {tax.rate * 100:g}%", 10), (500, y + 16 * k, _money_text(tax.amount),
                                                                         10)]  # fmt: skip
    k = len(invoice.taxes) + 1
    rows += [(400, y + 16 * k, "Total", 10), (500, y + 16 * k, _money_text(invoice.total), 10)]
    rows += [(50, 700, f"Please remit to: {s.printed}, {street}, {city} {s.province} {postal}", 8),
             (50, 712, f"GST/HST No. {s.tax_id[:9]} {s.tax_id[9:]}", 8)]  # fmt: skip
    return _page_pdf(rows)


def _joined(pdfs: list[bytes]) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for data in pdfs:
        with pymupdf.open(stream=data, filetype="pdf") as part:
            doc.insert_pdf(part)
    out = doc.tobytes()
    doc.close()
    return out


def write_files(folder: Path, invoices: list[DayInvoice], books: G.GroupBooks, seed: int) -> None:
    """Each invoice's file in ``folder`` (``<name>.pdf``; a scan as an image-only PDF), its name kept on it."""
    from .generator import scan

    folder.mkdir(parents=True, exist_ok=True)
    made: dict[str, bytes] = {}
    for invoice in invoices:
        if invoice.copy_of:
            pdf = made[invoice.copy_of]
        elif invoice.layout == "one_row":
            pdf = _one_row(invoice, books)
        elif invoice.layout == "buyer_trap":
            pdf = _buyer_trap(invoice, books)
        elif invoice.layout == "several":
            pdf = _joined([_rendered(part, books, seed) for part in invoice.parts])
        else:
            pdf = _rendered(invoice, books, seed)
        made[invoice.name] = pdf
        if invoice.scanned:
            pdf, _meta = scan(pdf, random.Random(f"pilot-day-scan:{seed}:{invoice.name}"), False)
        invoice.file = f"{invoice.name}.pdf"
        (folder / invoice.file).write_bytes(pdf)


# --- the app -------------------------------------------------------------------------------------------------------

PAGE = {"X-AP-Coder": "1"}  # what AP Coder's own page sends with every action
BATCH = 25  # invoices chosen at once on Add invoices
READ_WAIT = 900.0  # seconds a batch has to be read and coded


class Failed(Exception):
    """A step of the day did not go as the guide says."""


class StandIn(FakeModel):
    """LM Studio with OvisOCR2 and a chat model that proposes no account (what is proposed is AP Coder's own: the
    past coding, the purchase orders) and, asked as the AI reviewer, agrees."""

    def __init__(self) -> None:
        super().__init__(api="v1", slots=4, models=[
            {"id": READER, "type": "llm", "vision": True, "reasoning": False, "ctx": 20480},
            {"id": CHAT, "type": "llm", "vision": True, "reasoning": False, "ctx": 32768, "max_context": 131072},
        ])  # fmt: skip

    def answer_suggest(self, body: dict[str, Any]) -> Any:
        return {"lines": []}


def plain(page: str) -> str:
    """A page's words, as a person reads them."""
    page = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def said_on(page: str) -> str:
    """What the page says of the action that led to it: its messages' words."""
    return " ".join(plain(text) for text in re.findall(r'<div class="flash-text">(.*?)</div>', page, re.S))


class App:
    """The dashboard on a new laptop data folder (``web.core.first_run``: the simple screens, JD Edwards keying), as
    the browser on this computer opens it, its chat model the stand-in at ``model_url``."""

    def __init__(self, data: Path, model_url: str) -> None:
        from dataclasses import replace

        from fastapi.testclient import TestClient

        from ..core.config import Settings
        from ..web.app import create_app
        from ..web.core import first_run

        data.mkdir(parents=True, exist_ok=True)
        self.data = data
        self.store = first_run(data / "ap_coder.db", server=False)
        settings = Settings()
        self.settings = replace(settings, llm=replace(settings.llm, provider="local", base_url=model_url))
        self.app = create_app(self.settings, self.store, start_reader=False)
        self.client = TestClient(self.app, base_url="http://localhost")

    def get(self, url: str) -> str:
        answer = self.client.get(url)
        if answer.status_code != 200:
            raise Failed(f"GET {url}: {answer.status_code} {answer.text[:400]}")
        return answer.text

    def post(self, url: str, fields: list[tuple[str, str]] | dict[str, Any] | None = None, **more: Any) -> dict:
        data: dict[str, Any] = dict(fields) if isinstance(fields, dict) else {}
        for name, value in fields if isinstance(fields, list) else []:  # (a form's fields, a name repeated)
            data.setdefault(name, []).append(value)
        answer = self.client.post(url, data=data, headers=PAGE, **more)
        try:
            said = answer.json()
        except ValueError:
            said = {"ok": False, "error": answer.text[:400]}
        if answer.status_code != 200 or not said.get("ok", True):
            raise Failed(f"POST {url}: {answer.status_code} {said.get('error') or said}")
        return said

    def follow(self, said: dict) -> str:
        return self.get(said["redirect"])

    def submit(self, url: str, action: str, ticked: dict[str, bool] | None = None,
               rows: tuple[str, dict[str, dict[str, str]]] | None = None, **values: str) -> dict:  # fmt: skip
        """The form of page ``url`` that posts to ``action``, sent as a person sends it: the text boxes of ``values``
        typed in, the boxes of ``ticked`` ticked or not, a table's boxes typed in by row (``rows``)."""
        from .load import Forms, form_data

        parsed = Forms()
        parsed.feed(self.get(url))
        owner = next((k for k, f in parsed.forms.items() if f["action"] == action), None)
        if owner is None:
            raise Failed(f"{url} has no form that sends to {action}")
        for f in parsed.fields:
            if f.owner == owner and f.checkable and ticked and f.name in ticked:
                f.checked = ticked[f.name]
        if rows is not None:  # a table's boxes by row: {the row's key (its ``rows[0]`` box): {box: value}}
            key, typed = rows
            row = ""
            for f in parsed.fields:
                if f.owner == owner and f.name == key:
                    row = f.values[0] if f.values else ""
                elif f.owner == owner and f.name in typed.get(row, {}):
                    f.values = [typed[row][f.name]]
        return self.post(action, form_data(parsed.fields, owner, **values))

    def wait_read(self) -> dict:
        job = self.app.state.jobs.get("process")
        if job is None or not job.wait(READ_WAIT):
            raise Failed(f"the invoices were not read within {READ_WAIT:g} s")
        snap = self.client.get("/process/status").json()
        if snap.get("state") != "done":
            raise Failed(f"the reading did not finish: {snap}")
        return snap.get("result") or {}


def load_files(app: App, books: G.GroupBooks, folder: Path) -> list[str]:
    """Setup → JD Edwards files as the guide has it: the master workbook uploaded and every sheet loaded with Load all,
    then the whole chart from its text file on the command line. What each said."""
    said = []
    workbook = G.group_workbook_xlsx(books)
    (folder / "jde_reports.xlsx").write_bytes(workbook)
    up = app.post("/jde-files/workbook/upload", files={"file": ("jde_reports.xlsx", workbook)})
    token = re.search(r"workbook=([^&#]+)", up.get("redirect", ""))
    if not token:
        raise Failed(f"the workbook was not taken: {up}")
    shown = plain(app.get(f"/jde-files?workbook={token.group(1)}"))
    said.append(shown[shown.find("jde_reports.xlsx ·") :][:160])
    loaded = plain(app.follow(app.post("/jde-files/workbook/load", {"token": token.group(1)})))
    said.append(loaded[loaded.find("Loaded ") :][:3000])
    chart = folder / "F0901 all companies.txt"
    G.write_chart_text(chart, books)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2]), "AP_PRIVATE_DIR": str(app.data)}
    done = subprocess.run([sys.executable, "-m", "ap_coder", "--db", str(app.store.path), "chart", "import",
                           "--chart", str(chart)], capture_output=True, text=True, env=env, check=False,
                          timeout=600)  # fmt: skip
    if done.returncode:
        raise Failed(f"the chart was not loaded: {done.stderr[-800:] or done.stdout[-800:]}")
    said.append(" ".join(line.strip() for line in done.stdout.splitlines() if "rows read..." not in line)[:1500])
    return said


def make_settings(app: App, books: G.GroupBooks) -> list[str]:
    """The settings a pilot tester makes: the province and the companies' names (Settings → Review), PO-matched
    vouchers (Settings → JD Edwards E1) and the companies in the pilot (its Accounts AP posts to)."""
    said = []
    names = "\n".join(name for _code, name, _cur, _country in books.companies)
    answer = app.submit("/settings/review", "/settings/review", our_province="AB", our_names=names)
    said.append(said_on(app.follow(answer)))
    answer = app.submit("/settings/jde", "/settings/jde", ticked={"po_matched": True})
    said.append(said_on(app.follow(answer)))
    answer = app.submit("/settings/jde", "/settings/jde/posting", pilot_companies=", ".join(G.PILOT))
    said.append(said_on(app.follow(answer)))
    areas = {
        province: {"tax_area": area, "code": "C" if province == "BC" else "V"} for province, area in G.AREAS.items()
    }
    answer = app.submit("/settings/jde", "/settings/jde/tax-map", rows=("province", areas))
    said.append(said_on(app.follow(answer)))
    return said


def process(app: App, folder: Path, invoices: list[DayInvoice]) -> list[dict]:
    """Add invoices: the day's files chosen ``BATCH`` at a time, each batch read before the next (the ones sent again
    last), as Process uploaded invoices reads them."""
    results = []
    for start in range(0, len(invoices), BATCH):
        chosen = invoices[start : start + BATCH]
        files = [("files", (i.file, (folder / i.file).read_bytes(), "application/pdf")) for i in chosen]
        app.post("/process/upload", files=files)
        results.append(app.wait_read())
    return results


# --- what came of each invoice -------------------------------------------------------------------------------------

CODE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")  # a finding's code, as the check's sections carry it


def _form(fields: list[tuple[str, str]]) -> dict[str, list[str]]:
    data: dict[str, list[str]] = {}
    for name, value in fields:
        data.setdefault(name, []).append(value)
    return data


def _opened(app: App, invoice_id: int) -> tuple[Any, dict]:
    """The review screen opened and checked as it is: (the screen, the check's answer)."""
    from .load import Screen

    screen = Screen(invoice_id, app.get(f"/review/{invoice_id}"))
    if not screen.editable:
        return screen, {}
    answer = app.post(f"/review/{invoice_id}/check", screen.data("inv"))
    screen.apply(answer)
    return screen, answer


def _account(line: dict[str, Any]) -> str:
    from ..core.chart import join_account
    from ..core.reference_data import UNASSIGNED

    gl, cc = (str(line.get(k) or "").strip() for k in ("predicted_gl_code", "predicted_cost_center"))
    return join_account("" if cc == UNASSIGNED else cc, "" if gl == UNASSIGNED else gl)


def observe(app: App, invoices: list[DayInvoice]) -> dict[str, dict[str, Any]]:
    """Each invoice as it arrived in the review queue: its company, supplier number, lines' accounts and PO lines,
    taxes, findings (stored and as its check says them) and whether Approve is open."""
    from ..coding import po as po_mod
    from ..core.schema import InvoiceCoding

    ids = {Path(r["source_path"]).name: r["id"] for r in app.store.list_invoices_full()}
    got: dict[str, dict[str, Any]] = {}
    for invoice in invoices:
        invoice_id = ids.get(invoice.file)
        if invoice_id is None:
            got[invoice.name] = {"missing": True}
            continue
        inv = app.store.get_invoice(invoice_id) or {}
        output = inv.get("ai_output") or {}
        screen, answer = _opened(app, invoice_id)
        codes = {str(i.get("code")) for i in (inv.get("validation") or {}).get("issues") or []}
        codes |= set(CODE.findall(json.dumps(answer.get("sections") or {})))
        lines = output.get("line_items") or []
        matched: dict[int, str] = {}
        if output.get("po_number") and lines:
            coding = InvoiceCoding.model_validate({k: v for k, v in output.items() if k != "gl_distribution"})
            match = po_mod.match_invoice(coding, app.store, invoice_id)
            for m in match.lines if match is not None else []:
                if m.po_erp_line and match.po:
                    matched[m.invoice_line] = f"{match.po.get('po_number')}-{int(float(m.po_erp_line))}"
        taxes: dict[str, float] = {}
        for t in output.get("tax_lines") or []:
            taxes[t["tax_type"]] = round(taxes.get(t["tax_type"], 0.0) + float(t["tax_amount"] or 0), 2)
        got[invoice.name] = {
            "id": invoice_id, "status": inv.get("status"), "company": output.get("company") or "",
            "supplier": str((answer.get("header") or {}).get("an8") or ""), "vendor": output.get("vendor_name"),
            "number": output.get("invoice_number"), "total": output.get("grand_total"),
            "accounts": [_account(li) for li in lines],
            "po_lines": [matched.get(int(li.get("line_number") or 0), "") for li in lines],
            "ledgers": [[li.get("subledger") or "", li.get("subledger_type") or "", li.get("units")] for li in lines],
            "taxes": taxes, "codes": sorted(codes), "allow": bool(screen.allow),
        }  # fmt: skip
    return got


def approve(app: App, invoices: list[DayInvoice], got: dict[str, dict[str, Any]]) -> dict[str, str]:
    """A person's work on each invoice to approve today: each line left without an account given the one the truth
    says (as a person picks it, or clicks "Use the PO's coding"), the company picked when none was decided, a
    subledger or units typed where the screen asks, checked, then approved. {invoice: "approved", or why not}."""
    from .load import Screen

    out: dict[str, str] = {}
    for invoice in invoices:
        seen = got.get(invoice.name) or {}
        if not invoice.approve or seen.get("missing"):
            continue
        invoice_id = seen["id"]
        screen = Screen(invoice_id, app.get(f"/review/{invoice_id}"))
        if not screen.editable:
            out[invoice.name] = "the review screen is read-only"
            continue
        boxes = [f for f in screen.fields if f.owner == "inv" and f.name == "line_account"]
        for box, line in zip(boxes, invoice.lines, strict=False):
            if not (box.values and box.values[0].strip()) and line.account:
                box.values = [line.account]
        company = screen.field("company")
        if company is not None and not (company.values and company.values[0].strip()):
            company.values = [invoice.company]
        screen.apply(app.post(f"/review/{invoice_id}/check", screen.data("inv")))
        typed = {"line_subledger": [li.subledger[0] for li in invoice.lines],
                 "line_subledger_type": [li.subledger[1] for li in invoice.lines],
                 "line_units": [str(li.units or li.quantity) for li in invoice.lines]}  # fmt: skip
        for name, values in typed.items():
            for box, value in zip([f for f in screen.fields if f.owner == "inv" and f.name == name], values,
                                  strict=False):  # fmt: skip
                if not (box.values and box.values[0].strip()) and value:
                    box.values = [value]
        screen.apply(app.post(f"/review/{invoice_id}/check", screen.data("inv")))
        url, fields = screen.forms["inv"]["action"], screen.data("inv")
        answer = app.client.post(url, data=_form(fields), headers=PAGE).json()
        if answer.get("ask_why"):
            answer = app.client.post(url, data=_form([*fields, ("why_done", "1")]), headers=PAGE).json()
        out[invoice.name] = "approved" if answer.get("ok", True) and answer.get("redirect") else \
            f"not approved: {answer.get('error') or answer}"  # fmt: skip
    return out


def keying_sheet(app: App, folder: Path) -> list[list[Any]]:
    """To key into JDE: Export, as the page sends it (every approved invoice ticked, the keying sheet), the batch's
    file downloaded; the sheet's rows (the first sheet's; the second, "Not ready", after a row ["Not ready"])."""
    import openpyxl

    from .load import export_form

    form = export_form(app.get("/exports"))
    if not any(name == "ids" for name, _ in form):
        return []
    made = app.follow(app.post("/exports/create", form))
    link = re.search(r'href="(/exports/batches/(\d+)/download[^"]*)"', made)
    if not link:
        raise Failed(f"the export made no batch to download: {plain(made)[:600]}")
    sheet = app.client.get(html.unescape(link.group(1)))
    (folder / "keying_sheet.xlsx").write_bytes(sheet.content)
    book = openpyxl.load_workbook(io.BytesIO(sheet.content), read_only=True, data_only=True)
    rows: list[list[Any]] = []
    for n, ws in enumerate(book.worksheets):
        if n:
            rows.append(["Not ready"])
        rows += [list(r) for r in ws.iter_rows(values_only=True)]
    return rows


def reject(app: App, invoices: list[DayInvoice], got: dict[str, dict[str, Any]]) -> dict[str, str]:
    """What a person rejects first: a bill already in JD Edwards, an invoice sent again, a file of several invoices
    (to split). {invoice: "rejected", or why not}."""
    from .load import Screen

    out = {}
    for invoice in invoices:
        seen = got.get(invoice.name) or {}
        if not invoice.reject or seen.get("missing"):
            continue
        screen = Screen(seen["id"], app.get(f"/review/{seen['id']}"))
        rev = screen.field("rev")
        fields = [("reason", invoice.reject), *([("rev", rev.values[0])] if rev and rev.values else [])]
        try:
            app.post(f"/review/{seen['id']}/reject", fields)
            out[invoice.name] = "rejected"
        except Failed as exc:
            out[invoice.name] = f"not rejected: {exc}"
    return out


# --- the keying sheet ----------------------------------------------------------------------------------------------

VOUCHER = re.compile(r"Voucher \d+ of \d+")


def vouchers_keyed(rows: list[list[Any]]) -> dict[int, dict[str, Any]]:
    """{AP Coder's invoice number: its voucher on the keying sheet}: the header's label and value pairs, and its rows
    (each G/L line: account, amount; each voucher match row: order and line, amount)."""
    out: dict[int, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    matching = False
    for row in rows:
        cells = [c for c in row if c is not None]
        if not cells:
            continue
        if cells[0] == "Not ready":
            break
        if isinstance(cells[0], str) and VOUCHER.fullmatch(cells[0]):
            number = int(re.sub(r"\D", "", str(cells[1] if len(cells) > 1 else "")) or 0)
            current = out.setdefault(number, {"header": {}, "gl": [], "match": []})
            matching = False
            continue
        if current is None:
            continue
        if cells[0] == "Voucher match":
            matching = True
            continue
        if cells[0] in ("Order Number", "Account number", "Check", "Note") or len(cells) < 2:
            continue
        if matching and re.fullmatch(r"\d+", str(cells[0])) and len(cells) >= 7:
            current["match"].append((f"{cells[0]}-{int(float(cells[3]))}", Decimal(str(cells[6])).quantize(CENT)))
        elif re.fullmatch(r"[\w ]+(\.\w+){1,2}", str(cells[0])) and len(cells) >= 3 and isinstance(row[2], int | float):
            current["gl"].append((str(cells[0]).strip(), Decimal(str(row[2])).quantize(CENT)))
        elif isinstance(cells[0], str):
            for label, value in zip(cells[0::2], cells[1::2], strict=False):
                current["header"].setdefault(str(label), value)
    return out


# --- the score -----------------------------------------------------------------------------------------------------


@dataclass
class Miss:
    invoice: str
    category: str
    measure: str
    expected: str
    got: str
    file: str = ""


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT)


def _taxes(amounts: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for code, amount in amounts.items():
        key = "GST/HST" if code in ("GST", "HST") else code
        out[key] = round(out.get(key, 0.0) + amount, 2)
    return {k: v for k, v in out.items() if v}


def _closed(account: str) -> bool:
    unit = account.split(".")[0]
    return any(unit in G.jobs_of(c[0])[: G.CLOSED_JOBS] for c in G.CONSTRUCTION)


def score_one(invoice: DayInvoice, seen: dict[str, Any], done: str, keyed: dict[str, Any] | None) -> dict[str, Any]:
    """{measure: (right, expected, got)} for one invoice; a measure that does not apply is left out."""
    out: dict[str, tuple[bool, str, str]] = {}
    if seen.get("missing"):
        return {m: (False, "in the review queue", "not there") for m in MEASURES[:2]}
    expected = invoice.company if invoice.named else ""
    out["company"] = (seen["company"] == expected, expected or "none decided", seen["company"] or "none decided")
    out["supplier"] = (seen["supplier"] == str(invoice.supplier), str(invoice.supplier), seen["supplier"] or "none")
    accounts = seen["accounts"]
    if invoice.category != "several":
        if len(accounts) != len(invoice.lines):
            out["lines"] = (False, f"{len(invoice.lines)} lines", f"{len(accounts)} lines")
        wanted = [li.po_line for li in invoice.lines]
        if any(wanted):
            out["po_lines"] = (sorted(wanted) == sorted(seen["po_lines"]), " ".join(p or "-" for p in wanted),
                               " ".join(p or "-" for p in seen["po_lines"]))  # fmt: skip
    if invoice.category not in ("several", "outside_pilot", "dup_erp", "dup_resent") and len(accounts) == len(
            invoice.lines):  # fmt: skip
        pairs = list(enumerate(zip(invoice.lines, accounts, strict=True), 1))
        wrong = [f"line {n}: {a} (not {li.account})" for n, (li, a) in pairs if a and a != li.account]
        blank = [f"line {n}: none (sure: {li.account})" for n, (li, a) in pairs if not a and li.sure and li.account]
        closed = [f"line {n}: {a} is a closed job" for n, a in enumerate(accounts, 1) if a and _closed(a)]
        problems = closed + wrong + blank
        out["accounts"] = (not problems, " ".join(li.account or "-" for li in invoice.lines),
                           "; ".join(problems) or " ".join(a or "-" for a in accounts))  # fmt: skip
    if invoice.category != "several":  # (the GST and the HST alike: what is recovered, whatever its label says)
        taxes = _taxes({t.code: float(t.amount) for t in invoice.taxes})
        read = _taxes(seen["taxes"])
        same = set(taxes) == set(read) and all(abs(taxes[k] - read[k]) < 0.015 for k in taxes)
        out["tax"] = (same, json.dumps(taxes), json.dumps(read))
    codes = set(seen["codes"])
    missing = [c for c in invoice.must if c not in codes]
    extra = [c for c in invoice.must_not if c in codes]
    out["findings"] = (
        not missing and not extra,
        f"must: {', '.join(invoice.must) or '-'}",
        "; ".join([*(f"no {c}" for c in missing), *(f"{c} said" for c in extra)]) or "as expected",
    )
    if invoice.held is not None:
        out["held"] = (seen["allow"] != invoice.held, "held" if invoice.held else "Approve open",
                       "Approve open" if seen["allow"] else "held")  # fmt: skip
    if invoice.approve:
        out["keying"] = _keying(invoice, seen, done, keyed)
    return out


def _keying(invoice: DayInvoice, seen: dict[str, Any], done: str, keyed: dict[str, Any] | None) -> tuple:
    if done != "approved":
        return False, "approved and on the keying sheet", done or "not approved"
    if keyed is None:
        return False, "on the keying sheet", "not on it (Not ready, or not exported)"
    header, problems = keyed["header"], []
    if str(header.get("Supplier number", "")) != str(invoice.supplier):
        problems.append(f"supplier {header.get('Supplier number')}")
    if str(header.get("Company", "")) != invoice.company:
        problems.append(f"company {header.get('Company')}")
    if _money(header.get("Gross amount")) != invoice.total:
        problems.append(f"gross {header.get('Gross amount')} (not {invoice.total})")
    area = G.area_of(invoice.company)  # (a US company's voucher: the PO line's area, or the supplier's default)
    if str(header.get("Tax rate/area", "") or "") != area:
        problems.append(f"tax area {header.get('Tax rate/area')} (not {area})")
    if invoice.province and _money(header.get("Tax amount")) != invoice.tax_keyed:
        problems.append(f"tax {header.get('Tax amount')} (not {invoice.tax_keyed})")
    if any(li.po_line for li in invoice.lines):
        want = sorted((li.po_line, li.amount) for li in invoice.lines if li.po_line)
        if sorted(keyed["match"]) != want:
            problems.append(f"matched {sorted(keyed['match'])} (not {want})")
    else:
        lines: dict[str, Decimal] = {}
        for account, amount in keyed["gl"]:
            lines[account] = lines.get(account, Decimal(0)) + amount
        want_lines = invoice.keyed()
        if set(lines) != set(want_lines) or any(abs(lines[a] - want_lines[a]) > Decimal("0.02") for a in lines):
            problems.append(f"G/L lines {sorted((a, str(v)) for a, v in lines.items())} (not "
                            f"{sorted((a, str(v)) for a, v in want_lines.items())})")  # fmt: skip
    return not problems, "the voucher as the truth keys it", "; ".join(problems) or "as expected"


def score(invoices: list[DayInvoice], got: dict[str, dict[str, Any]], done: dict[str, str],
          keyed: dict[int, dict[str, Any]]) -> tuple[dict[str, dict[str, list[int]]], list[Miss]]:  # fmt: skip
    """The scorecard ({category: {measure: [right, scored]}}, with "all") and every miss."""
    card: dict[str, dict[str, list[int]]] = {}
    misses: list[Miss] = []
    for invoice in invoices:
        seen = got.get(invoice.name) or {"missing": True}
        voucher = keyed.get(seen.get("id") or -1)
        for measure, (right, expected, found) in score_one(invoice, seen, done.get(invoice.name, ""), voucher).items():
            for row in (invoice.category, "all"):
                cell = card.setdefault(row, {}).setdefault(measure, [0, 0])
                cell[0] += bool(right)
                cell[1] += 1
            if not right:
                misses.append(Miss(invoice.name, invoice.category, measure, expected, found, invoice.file))
    return card, misses


# --- the run -------------------------------------------------------------------------------------------------------


def run(out: Path, seed: int = 1, quick: bool = False, scanned: float = 0.1, keep: bool = False,
        on: dt.date | None = None, counts: dict[str, int] | None = None) -> dict[str, Any]:  # fmt: skip
    """The pilot day in ``out`` (its data folder ``out/data`` removed after, unless ``keep``): the report. ``counts``:
    how many of each category (default ``CATEGORIES``', or ``QUICK_SHARE`` of them)."""
    started = time.perf_counter()
    out.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(out / "data", ignore_errors=True)
    books = G.construction_books(seed, on)
    invoices = Planner(books, seed).plan(counts or counts_for(quick), scanned)
    write_files(out / "invoices", invoices, books, seed)
    took: dict[str, float] = {"files": round(time.perf_counter() - started, 1)}
    before = os.environ.get("AP_PRIVATE_DIR")
    os.environ["AP_PRIVATE_DIR"] = str(out / "data")  # (the chat model's answers are kept there)
    try:
        with StandIn() as fake:
            app = App(out / "data", fake.base_url)
            step = time.perf_counter()
            loaded = load_files(app, books, out)
            settings = make_settings(app, books)
            took["loading"] = round(time.perf_counter() - step, 1)
            step = time.perf_counter()
            read = process(app, out / "invoices", invoices)
            took["reading"] = round(time.perf_counter() - step, 1)
            step = time.perf_counter()
            got = observe(app, invoices)
            rejected = reject(app, invoices, got)
            done = approve(app, invoices, got)
            rows = keying_sheet(app, out)
            took["review"] = round(time.perf_counter() - step, 1)
            asked = fake.stats()
    finally:
        if before is None:
            os.environ.pop("AP_PRIVATE_DIR", None)
        else:
            os.environ["AP_PRIVATE_DIR"] = before
    if not keep:
        shutil.rmtree(out / "data", ignore_errors=True)
    keyed = vouchers_keyed(rows)
    card, misses = score(invoices, got, done, keyed)
    took["all"] = round(time.perf_counter() - started, 1)
    report = {
        "config": {"seed": seed, "quick": quick, "scanned": scanned, "day": books.on.isoformat(),
                   "invoices": len(invoices), "scans": sum(i.scanned for i in invoices)},
        "seconds": took, "loaded": loaded, "settings": settings,
        "read": [r.get("label") for r in read], "rejected": rejected, "approved": done,
        "model": {k: asked.get(k) for k in ("requests", "by_kind") if k in asked},
        "scorecard": card, "misses": [vars(m) for m in misses],
        "invoices": {i.name: {"category": i.category, "file": i.file, "scanned": i.scanned, **(got.get(i.name) or {})}
                     for i in invoices},
    }  # fmt: skip
    (out / "pilot_day.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    (out / "pilot_day.md").write_text(markdown(report), encoding="utf-8")
    return report


def scorecard_lines(card: dict[str, dict[str, list[int]]]) -> list[str]:
    measures = [m for m in (*MEASURES, "lines") if any(m in row for row in card.values())]
    lines = ["| category | n | " + " | ".join(measures) + " |", "|---|---:|" + "---:|" * len(measures)]
    for category in [c for c, _n, _w in CATEGORIES if c in card] + ["all"]:
        row = card[category]
        n = max(cell[1] for cell in row.values())
        cells = [f"{row[m][0]}/{row[m][1]}" if m in row else "" for m in measures]
        lines.append(f"| {category} | {n} | " + " | ".join(cells) + " |")
    return lines


def markdown(report: dict[str, Any]) -> str:
    c, s = report["config"], report["seconds"]
    lines = ["# A pilot day (D-275)", "",
             f"{c['invoices']} invoices ({c['scans']} scans) of the made-up construction group, day {c['day']}, seed "
             f"{c['seed']}{', quick' if c['quick'] else ''}: {s['all']:g} s in all (files {s['files']:g}, loading "
             f"{s.get('loading', 0):g}, reading {s.get('reading', 0):g}, review and approvals {s.get('review', 0):g}).",
             "", "Right of scored, per category and measure:", "", *scorecard_lines(report["scorecard"]), "",
             f"## Every miss ({len(report['misses'])})", ""]  # fmt: skip
    for m in report["misses"]:
        lines.append(f"- {m['invoice']} ({m['file']}) · {m['measure']}: expected {m['expected']}; got {m['got']}")
    lines += ["", "## Loading", "", *(f"- {text}" for text in report["loaded"] + report["settings"])]
    return "\n".join(lines) + "\n"


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """``pilotday``'s command line, under ``python -m ap_coder.bench``."""
    p = sub.add_parser("pilotday", help="the pilot tester's day on a made-up construction group, end to end through "
                       "the app, scored against the truth (D-275)")  # fmt: skip
    p.add_argument("--quick", action="store_true", help=f"{QUICK_SHARE:.0%} of each category (about 35 invoices)")
    p.add_argument("--seed", type=int, default=1, help="random seed; the same seed gives the same day")
    p.add_argument("--scanned", type=float, default=0.1, help="share of invoices made as scans (default 0.1)")
    p.add_argument("--out", default="bench_pilotday", help="output folder (default bench_pilotday)")
    p.add_argument("--keep", action="store_true", help="keep the data folder (OUT/data) to open it after")
    p.set_defaults(run=main)
    return p


def main(a: argparse.Namespace) -> int:
    report = run(Path(a.out), a.seed, a.quick, a.scanned, a.keep)
    print("\n".join(scorecard_lines(report["scorecard"])))
    print(f"{len(report['misses'])} misses; report: {Path(a.out) / 'pilot_day.md'}")
    return 0
