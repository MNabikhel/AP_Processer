"""A made-up company's JD Edwards books for one year, as data: the truth ``jde_exports`` writes its files from.

``build(seed, size)`` makes Maple Ridge Holdings Ltd. (company ``00210``, district ``03``; every name, number and
amount is invented here): its business units, its chart of accounts, its suppliers and a year of vouchers as AP
coded them. A test asks the truth what a line should get (``Voucher.lines[n].account``), an importer is checked
against what was written, and learning is measured against how the suppliers behave:

* 73% of vouchers have one coding line, 9% are split across business units, 6% carry a purchase order;
* CAD 87%, USD 13% (the journal holds the Canadian amounts, the pay item the foreign one);
* 61% carry GST, entered on the pay item (tax explanation code ``V``, one tax area); PST and QST stay inside the
  expense line (``Supplier.provincial_rate``);
* about 27% of coding lines go to balance-sheet accounts (clearing, billbacks, payroll remittances, prepaids,
  relocation advances);
* a supplier goes to one object and subsidiary for about 44% of suppliers, to one business unit for about 58%; for
  the others the remark's words tell the accounts apart most of the time (``Account.phrases``), not always;
* 19% of vouchers pay a person back ("LAST, FIRST M."), 13% an affiliate (the intercompany payable, not trade);
* remarks of 30 characters at most, often ending in an office location (a street form or a building-floor-room
  code), 2% blank; a few vouchers are odd (``Voucher.oddity``).

Everything draws from ``random.Random`` seeded by name, so the same seed and size give the same books."""

from __future__ import annotations

import calendar
import datetime as dt
import random
from collections import Counter
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from ..core.chart import code_of, join_account, split_account, split_code
from .jde_names import (
    AFFILIATES,
    CHART,
    DEPARTMENTS,
    FIRST_NAMES,
    GENERIC_REMARKS,
    IT_UNITS,
    LAST_NAMES,
    PREFIXES,
    REGIONS,
    REMITTANCES,
    STREETS,
    TRADES,
    USERS,
)

CENT = Decimal("0.01")
GST_RATE = Decimal("0.05")
COMPANY_NUMBER, DISTRICT = "00210", "03"
MAIN_UNIT = COMPANY_NUMBER + "61010"  # company + unit 610 + department 10
RELOCATION_UNIT = COMPANY_NUMBER + "22510"
PAYABLE, INTERCOMPANY, GST_CLEARING, PST_CLEARING = "2110", "2150", "2420", "2425"
RECOVERIES, ALLOCATION = "9710", "9720"  # the reserved subsidiaries: postable, never to be suggested
TAX_AREA = "CDNGST"
BALANCE_SHEET_CLASSES = "12"  # an object's first digit: assets, liabilities and equity
OVERHEAD_CLASS, TECHNOLOGY = "7", "75"


def money(x: Decimal | float | int | str) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def account_string(business_unit: str, object_account: str, subsidiary: str = "") -> str:
    """The account as AP types it: ``BusinessUnit.Object.Subsidiary``, the subsidiary left off when there is none."""
    return join_account(business_unit, code_of(object_account, subsidiary))


@dataclass(frozen=True)
class Size:
    """How big the books are (``SIZES``): the counts each file is written to."""

    business_units: int
    it_units: int
    regions: int
    region_departments: int
    used_regional: int
    chart_rows: int
    suppliers: int
    employees: int
    affiliates: int
    remittances: int
    usd_suppliers: int
    unused: int
    vouchers: int
    billbacks: int
    relocations: int
    locations: int


SIZES = {
    "small": Size(business_units=20, it_units=3, regions=3, region_departments=1, used_regional=3, chart_rows=250,
                  suppliers=40, employees=10, affiliates=2, remittances=2, usd_suppliers=3, unused=3, vouchers=120,
                  billbacks=3, relocations=3, locations=4),
    # A quarter of a year's vouchers: enough suppliers of each kind to score coding in seconds (bench coding --quick).
    "tenth": Size(business_units=40, it_units=5, regions=4, region_departments=3, used_regional=8, chart_rows=1500,
                  suppliers=240, employees=60, affiliates=3, remittances=3, usd_suppliers=14, unused=5, vouchers=1400,
                  billbacks=6, relocations=5, locations=8),
    "year": Size(business_units=130, it_units=12, regions=8, region_departments=11, used_regional=39,
                 chart_rows=14000, suppliers=970, employees=330, affiliates=5, remittances=8, usd_suppliers=70,
                 unused=20, vouchers=5500, billbacks=14, relocations=12, locations=14),
}  # fmt: skip


@dataclass(frozen=True)
class Company:
    number: str
    name: str
    group: str
    district: str
    tax_id: str
    year: int
    main_business_unit: str
    payable_object: str  # trade accounts payable: JDE's own line on each pay item
    intercompany_object: str  # the same for an affiliate's voucher (the pay item's G/L offset)
    gst_object: str  # GST clearing: JDE's own line when the pay item carries tax
    pst_object: str  # never used by a voucher
    reserved_subsidiaries: tuple[str, str]  # recoveries, internal allocation
    tax_area: str
    bank_account: str
    payment_terms: str = "N30"


@dataclass(frozen=True)
class BusinessUnit:
    number: str  # unpadded; the exports right-justify it to 12
    name: str
    level: str  # LOD
    kind: str  # BU Ty: "BS" the company's own, "IS" an overhead department
    status: str  # Job Sts: "ACT" open for posting, "INA" not
    related: str
    address_number: int | None = None


@dataclass(frozen=True)
class Account:
    """One row of the chart. A business unit's own header row has no object."""

    business_unit: str
    object: str
    subsidiary: str
    description: str  # in full (30 characters at most); the chart indents and cuts it, the journal cuts it at 24
    level: int  # L D, 3 to 9
    posting_edit: str  # P E: "" postable; N, M, I, B, L not for AP
    account_id: str
    phrases: tuple[str, ...] = ()  # the words AP's remarks use for it

    @property
    def string(self) -> str:
        return account_string(self.business_unit, self.object, self.subsidiary)

    @property
    def postable(self) -> bool:
        return bool(self.object) and not self.posting_edit

    @property
    def reserved(self) -> bool:
        return self.subsidiary in (RECOVERIES, ALLOCATION)

    @property
    def balance_sheet(self) -> bool:
        return self.object[:1] in BALANCE_SHEET_CLASSES


@dataclass(frozen=True)
class Supplier:
    number: int
    name: str  # Alpha Name, 40 characters at most
    kind: str  # trade, employee, affiliate, remittance (payroll-type, no tax), unused (in the master only)
    currency: str = "CAD"
    taxed: bool = False  # its invoices carry GST
    provincial_rate: Decimal = Decimal(0)  # PST or QST on its invoices, left in the expense line
    accounts: tuple[str, ...] = ()  # every account string AP codes it to
    purchase_orders: bool = False
    location: str = ""  # the office its purchases are usually for
    share: float = 0.0  # its weight among the suppliers of its kind
    tax_id: str = ""  # Individual Tax ID: free text
    long_address: str = ""
    hold_payment: str = "N"
    industry_class: str = ""
    credit_message: str = ""


@dataclass(frozen=True)
class CodingLine:
    account: str
    amount: Decimal  # net, in Canadian dollars (what the G/L distribution and the journal hold)
    remark: str
    foreign_amount: Decimal | None = None  # the invoice's own amount when it is not in CAD


@dataclass(frozen=True)
class PayItem:
    number: str  # "001"
    gross: Decimal
    taxable: Decimal
    tax: Decimal
    tax_code: str  # tax explanation code: "V" when taxed
    tax_area: str
    due_date: dt.date
    status: str  # P paid, A approved, H held
    foreign_gross: Decimal | None = None


@dataclass(frozen=True)
class Voucher:
    document: int
    batch: int
    batch_date: dt.date
    supplier: int
    invoice_number: str
    invoice_date: dt.date
    gl_date: dt.date
    currency: str
    exchange_rate: Decimal | None
    purchase_order: str  # 8 characters with leading zeros, or ""
    business_unit: str  # the header's
    payable_object: str
    lines: tuple[CodingLine, ...]
    pay_items: tuple[PayItem, ...]
    provincial_tax: Decimal  # the PST or QST inside the lines
    user: str
    updated: dt.datetime
    oddity: str = ""  # negative, unbalanced, no_remarks, no_address, nine_lines, many_lines, under_a_dollar

    @property
    def net(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal(0))

    @property
    def tax(self) -> Decimal:
        return sum((item.tax for item in self.pay_items), Decimal(0))

    @property
    def gross(self) -> Decimal:
        return sum((item.gross for item in self.pay_items), Decimal(0))

    @property
    def business_units(self) -> set[str]:
        return {split_account(line.account)[0] for line in self.lines}


@dataclass
class Truth:
    """The books: the chart sorted as its export, the suppliers by number, the vouchers by G/L date."""

    company: Company
    business_units: list[BusinessUnit]
    accounts: list[Account]
    suppliers: list[Supplier]
    vouchers: list[Voucher]
    _accounts: dict[str, Account] = field(init=False, repr=False)
    _suppliers: dict[int, Supplier] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._accounts = {a.string: a for a in self.accounts if a.object}
        self._suppliers = {s.number: s for s in self.suppliers}

    def account(self, string: str) -> Account:
        return self._accounts[string]

    def supplier(self, number: int) -> Supplier:
        return self._suppliers[number]

    def suggestable(self) -> list[Account]:
        """The accounts a coding line may get: postable, not a reserved subsidiary, on an active business unit."""
        active = {u.number for u in self.business_units if u.status == "ACT"}
        return [a for a in self.accounts if a.postable and not a.reserved and a.business_unit in active]


_EMPLOYEE_OBJECTS = ("7710", "7720", "7730", "7140", "7510")
_MONTHLY_VOUCHERS = (300, 420, 470, 500, 530, 480, 430, 460, 510, 490, 450, 460)  # of a 5,500-voucher year
# kind: its share of the vouchers, and how many coding lines a voucher has (1, 2 to 5, 6 to 9).
_KINDS = {
    "employee": (0.19, (0.80, 0.19, 0.01)),
    "affiliate": (0.13, (0.55, 0.37, 0.08)),
    "remittance": (0.03, (0.10, 0.60, 0.30)),
    "usd": (0.09, (0.78, 0.21, 0.01)),
    "trade": (0.56, (0.78, 0.21, 0.01)),
}
_AMOUNTS = {"employee": (75, 1.0), "affiliate": (2400, 1.2), "remittance": (18000, 1.0), "trade": (320, 1.1)}


class _Row(NamedTuple):
    object: str
    subsidiary: str
    level: int
    edit: str
    description: str
    phrases: tuple[str, ...]


def _catalogue() -> list[_Row]:
    rows = []
    for line in CHART.strip().splitlines():
        head, _, phrases = line.partition("|")
        obj, sub, level, edit, description = head.split(None, 4)
        words = tuple(p.strip() for p in phrases.split(",") if p.strip())
        rows.append(_Row(obj, sub.strip("-"), int(level), edit.strip("-"), description.strip(), words))
    return rows


def _generated_rows(rng: random.Random, plan: Size) -> list[_Row]:
    """The subsidiaries that name a region, an affiliate or a person: billbacks (6 digits, on other clearing) and
    relocation advances (4 digits, on the relocation business unit), and an 8-digit one for the IT units."""
    rows = [_Row("2190", "", 7, "B", "Shared Asset Clearing", ())]
    rows.append(_Row("2190", "64020118", 9, "", "IT Equipment - Shared Pool", ("shared hardware", "pool equipment")))
    names = [f"{region} Region" for region in REGIONS] + [f"Affiliate {letter}" for letter in "NPRSTW"]
    for code, name in zip(sorted(rng.sample(range(710001, 739999), plan.billbacks)), names, strict=False):
        short = name.lower().replace(" region", "")
        rows.append(_Row("2480", str(code), 8, "", f"Billback - {name}", (f"billback {short}", f"recharge {short}")))
    people = rng.sample([(first, last) for last in LAST_NAMES for first in FIRST_NAMES], plan.relocations)
    for code, (first, last) in zip(sorted(rng.sample(range(9301, 9699), plan.relocations)), people, strict=True):
        who = f"{first[0]}. {last.title()}"
        rows.append(_Row("1450", str(code), 8, "", f"Move Advance - {who}"[:30], (f"{who} move", f"{who} housing")))
    return rows


# --- business units ------------------------------------------------------------------------------


def _business_units(rng: random.Random, plan: Size) -> list[BusinessUnit]:
    """The business-unit master: 5 characters (the company), 6 with a letter, 7, 8 (the IT cost centres) and 10
    (company + unit + department), one marked DO NOT USE that is still active, inactive ones, and one whose related
    business unit is another."""
    co = COMPANY_NUMBER
    units = [
        (co, "Head Office", "9", "BS", "ACT"),
        (co + "A", "Head Office - Adjustments", "9", "IS", "ACT"),
        ("0440118", "EQUIPMENT - RETIRED", "9", "IS", "INA"),
        ("0440126", "EQUIPMENT - IN TRANSFER", "7", "IS", "ACT"),
        (MAIN_UNIT, "General Administration", "9", "IS", "ACT"),
        (RELOCATION_UNIT, "Staff Moves & Transfers", "9", "IS", "ACT"),
        (co + "61058", "*** DO NOT USE ***", "9", "IS", "ACT"),
        (co + "61060", "Year 2000 Readiness", "9", "IS", "INA"),
    ]
    for number, name in zip(sorted(rng.sample(range(1200, 9900), plan.it_units)), IT_UNITS, strict=False):
        units.append((f"0440{number}", f"IT - {name}", "9", "IS", "ACT"))
    departments = plan.business_units - len(units) - plan.regions * plan.region_departments
    for n, name in enumerate(DEPARTMENTS[:departments]):
        units.append((f"{co}610{12 + 2 * n}", name, "7" if n % 6 == 4 else "9", "IS", "ACT"))
    for r, region in enumerate(REGIONS[: plan.regions]):
        for n, name in enumerate(["Administration", *DEPARTMENTS][: plan.region_departments]):
            units.append((f"{co}{621 + r}{10 + 2 * n}", f"{region} {name}"[:30], "9", "IS", "ACT"))
    elsewhere = units[-1][0]  # one regional unit rolls up to the main one
    return [
        BusinessUnit(number, name, level, kind, status, MAIN_UNIT if number == elsewhere else number,
                     rng.randrange(390000, 436000) if number == MAIN_UNIT else None)
        for number, name, level, kind, status in sorted(units, key=lambda u: u[0].rjust(12))
    ]  # fmt: skip


class _Groups(NamedTuple):
    """The business units vouchers are coded to (active, not marked DO NOT USE), by where they sit."""

    departments: list[str]  # the main unit's other departments
    regional: list[str]  # the regional units' departments really used
    it: list[str]  # the 8-digit IT cost centres

    @property
    def used(self) -> list[str]:
        return [MAIN_UNIT, RELOCATION_UNIT, *self.departments, *self.regional, *self.it]


def _groups(rng: random.Random, plan: Size, units: list[BusinessUnit]) -> _Groups:
    open_ = [u.number for u in units if u.status == "ACT" and "DO NOT USE" not in u.name and len(u.number) >= 8]
    departments = [n for n in open_ if n.startswith(COMPANY_NUMBER + "610") and n != MAIN_UNIT]
    regional = [n for n in open_ if len(n) == 10 and n[:8] not in (MAIN_UNIT[:8], RELOCATION_UNIT[:8])]
    return _Groups(departments, sorted(rng.sample(regional, min(plan.used_regional, len(regional)))),
                   [n for n in open_ if len(n) == 8])  # fmt: skip


# --- suppliers -----------------------------------------------------------------------------------


class _Pairs:
    """The postable object and subsidiary pairs of the catalogue, by object, and where each may be coded."""

    def __init__(self, rows: list[_Row], groups: _Groups) -> None:
        self.groups = groups
        self.by_object: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            if not row.edit and row.phrases:
                self.by_object.setdefault(row.object, []).append((row.object, row.subsidiary))

    def of(self, objects: tuple[str, ...] | list[str]) -> list[tuple[str, str]]:
        return [pair for obj in objects for pair in self.by_object.get(obj, [])]

    def home(self, rng: random.Random, pair: tuple[str, str]) -> str:
        """A business unit for an expense: the main one, one of its departments, a regional one, or (technology
        only) an IT cost centre."""
        u, g = rng.random(), self.groups
        if pair[0].startswith(TECHNOLOGY) and rng.random() < 0.75:
            return rng.choice(g.it)
        if u < 0.45:
            return MAIN_UNIT
        return rng.choice(g.regional) if u > 0.95 and g.regional else rng.choice(g.departments)

    def place(self, unit: str, pair: tuple[str, str]) -> str:
        """The account string of ``pair`` on ``unit``; a balance-sheet account sits where the chart has it."""
        if pair[0] == "1450":
            unit = RELOCATION_UNIT
        elif pair[0] == "2190":
            unit = self.groups.it[0]
        elif pair[0][0] in BALANCE_SHEET_CLASSES or (len(unit) == 8 and not pair[0].startswith(TECHNOLOGY)):
            unit = MAIN_UNIT
        return account_string(unit, *pair)


def _trade_accounts(rng: random.Random, pairs: _Pairs, objects: list[str]) -> list[str]:
    """Where AP codes a trade supplier: one pair or several (2 to 4), on one business unit or several (2 to 5)."""
    candidates = pairs.of(objects)
    one_pair = rng.random() < 0.41 or len(candidates) == 1
    chosen = [rng.choice(candidates)] if one_pair else rng.sample(candidates, rng.randint(2, min(4, len(candidates))))
    one_unit = rng.random() < (0.64 if one_pair else 0.22)
    homes = [pairs.home(rng, chosen[0])]
    while not one_unit and len(homes) < rng.randint(2, 5):
        homes.append(pairs.home(rng, chosen[0]))
    homes = list(dict.fromkeys(homes))
    if one_pair:
        accounts = [pairs.place(home, chosen[0]) for home in homes]
    else:
        spread = max(len(chosen), len(homes))
        accounts = [pairs.place(homes[i % len(homes)], chosen[i % len(chosen)]) for i in range(spread)]
    if rng.random() < 0.15:  # some of its invoices are billed back
        accounts.append(pairs.place(MAIN_UNIT, rng.choice([p for p in pairs.by_object["2480"] if len(p[1]) == 6])))
    return list(dict.fromkeys(accounts))


def _employee_accounts(rng: random.Random, pairs: _Pairs) -> list[str]:
    """A person's claims: a clearing account on the main unit, an expense of their department, or a mix."""
    clearing = [("2480", "320"), ("2480", "330"), ("2480", "310")]
    department = rng.choice([MAIN_UNIT, *pairs.groups.departments, *pairs.groups.regional])
    expenses = pairs.of(_EMPLOYEE_OBJECTS)
    u = rng.random()
    if u < 0.50:
        return [pairs.place(MAIN_UNIT, rng.choice(clearing[:2]))]
    if u < 0.64:
        return [pairs.place(department, rng.choice(expenses))]
    chosen = rng.sample(expenses, rng.randint(2, 4)) + ([rng.choice(clearing)] if rng.random() < 0.6 else [])
    return list(dict.fromkeys(pairs.place(department, pair) for pair in chosen))


def _affiliate_accounts(rng: random.Random, pairs: _Pairs) -> list[str]:
    """An affiliate's charges: shared premises and services on a department or two, and billbacks."""
    homes = rng.sample([MAIN_UNIT, *pairs.groups.departments], 2)
    shared = rng.sample(pairs.of(["7310", "7330", "7340", "7350", "7360", "7550", "7640"]), rng.randint(5, 9))
    accounts = [pairs.place(homes[0 if i % 3 else 1], pair) for i, pair in enumerate(shared)]
    billbacks = [p for p in pairs.by_object["2480"] if len(p[1]) == 6]
    billed = [pairs.place(MAIN_UNIT, pair) for pair in rng.sample(billbacks, min(3, len(billbacks)))]
    return list(dict.fromkeys([accounts[0], billed[0], *accounts[1:], *billed[1:]]))


class _Names:
    """Made-up supplier names, each given once."""

    def __init__(self, rng: random.Random) -> None:
        self.rng, self.given = rng, set()
        self.trades = []
        for line in TRADES.strip().splitlines():
            words, _, objects = line.partition("|")
            self.trades.append(([w.strip() for w in words.split(",")], objects.split()))

    def _once(self, make) -> str:
        while True:
            name = make()
            if name not in self.given and len(name) <= 40:
                self.given.add(name)
                return name

    def trade(self, foreign: bool = False, telecom: bool = False) -> tuple[str, list[str]]:
        """A trade supplier's name and the objects it is coded to (a foreign one sells technology)."""
        words, objects = self.rng.choice(self.trades[4:5] if telecom else self.trades[4:7] if foreign else self.trades)
        endings = ("LLC", "INC.", "CORP.") if foreign else ("LTD.", "INC.", "CORP.", "& CO.", "")
        pick = self.rng.choice
        return self._once(lambda: f"{pick(PREFIXES)} {pick(words)} {pick(endings)}".strip()), objects

    def person(self) -> str:
        rng = self.rng
        return self._once(lambda: f"{rng.choice(LAST_NAMES)}, {rng.choice(FIRST_NAMES)} {rng.choice('ABCDEJKLMRST')}.")


def _locations(rng: random.Random, plan: Size) -> list[str]:
    """The offices a purchase is for, as AP ends a remark: a street form and a building-floor-room code."""
    streets = [f"{rng.randrange(40, 9900)} {rng.choice(STREETS)}" for _ in range(plan.locations // 2)]
    rooms = [f"{rng.randint(1, 4)}-{rng.randint(1, 6)}-{rng.randrange(10, 640):03d}" for _ in streets]
    return streets + rooms


def _suppliers(rng: random.Random, plan: Size, pairs: _Pairs, places: list[str]) -> list[Supplier]:
    """The supplier master: affiliates, payroll-type remittances, people, trade suppliers (the first ones foreign)
    and a few that are in the master only, then the master's quirks (``_quirks``)."""
    names = _Names(rng)
    numbers = (10 * n + rng.choice((5, 6)) for n in rng.sample(range(2100, 89900), plan.suppliers))  # made up
    made: list[Supplier] = []
    for n, (name, currency, share) in enumerate(AFFILIATES[: plan.affiliates]):
        made.append(Supplier(3110 + 17 * n, name, "affiliate", currency, currency == "CAD" and n != 2,
                             accounts=tuple(_affiliate_accounts(rng, pairs)), share=share))  # fmt: skip
    for name, rest, objects in REMITTANCES[: plan.remittances]:
        accounts = tuple(pairs.place(MAIN_UNIT, pair) for pair in pairs.of(objects.split()))
        made.append(Supplier(next(numbers), name, "remittance", accounts=accounts, long_address=rest, share=1.0))
    for n in range(plan.employees):
        made.append(Supplier(next(numbers), names.person(), "employee", accounts=tuple(_employee_accounts(rng, pairs)),
                             location=rng.choice(places), share=(n + 1) ** -0.5))  # fmt: skip
    trade = plan.suppliers - plan.unused - len(made)
    for n in range(trade):
        foreign = n < plan.usd_suppliers
        rank = n + 1 if foreign else n + 1 - plan.usd_suppliers
        busiest = rank == 1 and not foreign  # a telephone company: the many-line voucher's supplier
        name, objects = names.trade(foreign, telecom=busiest)
        accounts = _everywhere(pairs) if busiest else _trade_accounts(rng, pairs, objects)
        provincial = "0" if foreign else {5: "0.07", 9: "0.07", 14: "0.09975"}.get(rank % 20, "0")  # PST, or QST
        made.append(Supplier(
            next(numbers), name, "trade", "USD" if foreign else "CAD", taxed=not foreign and rank % 25 != 13,
            provincial_rate=Decimal(provincial), accounts=tuple(accounts),
            purchase_orders=not foreign and rank % 5 == 4, location=rng.choice(places), share=rank**-0.9))  # fmt: skip
    for _ in range(plan.unused):
        made.append(Supplier(next(numbers), names.trade()[0], "unused"))
    return sorted(_quirks(rng, made), key=lambda s: s.number)


def _everywhere(pairs: _Pairs) -> list[str]:
    """The busiest Canadian trade supplier bills every department's cell phones on one invoice: one object and
    subsidiary, on every business unit in use."""
    return [pairs.place(unit, ("7510", "5107")) for unit in pairs.groups.used if unit != RELOCATION_UNIT]


def _quirks(rng: random.Random, made: list[Supplier]) -> list[Supplier]:
    """What a real supplier master carries: names marked DNU, DO NOT USE or ``*``, one with a double space, a tax-ID
    field that is mostly blank and otherwise notes or number-like strings, hold codes, a class, a credit message."""
    trade = [i for i, s in enumerate(made) if s.kind == "trade"]
    marks = (lambda n: n[:36] + " DNU", lambda n: n[:38] + " *", lambda n: n.replace(" ", "  ", 1)[:40],
             lambda n: n[:26] + "**DO NOT USE**")  # fmt: skip
    for i, mark in zip(rng.sample(trade, len(marks)), marks, strict=True):
        made[i] = replace(made[i], name=mark(made[i].name))
    for n, i in enumerate(i for i, s in enumerate(made) if s.kind == "unused"):
        ending = (" - DO NOT USE", " DNU")[n % 2]
        made[i] = replace(made[i], name=made[i].name[: 40 - len(ending)] + ending, hold_payment="Y")

    def digits(count: int) -> str:
        return "".join(rng.choice("0123456789") for _ in range(count))

    def checked() -> str:
        return f"VERIFIED {calendar.month_abbr[rng.randint(1, 12)].upper()} {rng.randint(1, 28)}/{rng.randint(12, 23)}"

    notes = (checked, lambda: f"{digits(9)}RT0001", lambda: "PER FORM ON FILE", lambda: digits(9),
             lambda: "NONE ON FILE", lambda: f"{digits(2)}-{digits(7)}", checked)  # fmt: skip
    for n, i in enumerate(rng.sample(range(len(made)), max(7, len(made) // 20))):
        made[i] = replace(made[i], tax_id=notes[n % len(notes)]())
    for n, i in enumerate(rng.sample(range(len(made)), max(4, len(made) // 12))):
        made[i] = replace(made[i], hold_payment="Y012"[n % 4] if n < 8 else "Y")
    for n, i in enumerate(rng.sample(range(len(made)), max(3, len(made) // 10))):
        made[i] = replace(made[i], credit_message="B25"[n % 3],
                          industry_class=str(rng.randrange(1100, 9300)) if n % 4 == 0 else "")  # fmt: skip
    return made


# --- the chart -----------------------------------------------------------------------------------


def _chart(rng: random.Random, plan: Size, units: list[BusinessUnit], rows: list[_Row], groups: _Groups,
           suppliers: list[Supplier]) -> list[Account]:  # fmt: skip
    """The account master: each business unit's header row, the whole structure on the main unit, every account a
    supplier is coded to, the reserved subsidiaries (recoveries on the main unit's overhead objects, internal
    allocation on the other departments'), then overhead accounts on the departments until ``plan.chart_rows``."""
    by_pair = {(row.object, row.subsidiary): row for row in rows}
    chosen: dict[tuple[str, str, str], _Row] = {(u.number, "", ""): _Row("", "", 3, "", u.name, ()) for u in units}
    for unit in (u for u in units if u.status == "INA" or "DO NOT USE" in u.name):
        chosen[(unit.number, "", "")] = _Row("", "", 3, "N", unit.name, ())
    chosen[(COMPANY_NUMBER, "2205", "")] = _Row("2205", "", 9, "I", "Payroll Clearing - Salaried", ())
    overhead = [row for row in rows if row.object[0] == OVERHEAD_CLASS and row.level >= 7]
    for row in rows:
        if row.object == "1450":
            chosen[(RELOCATION_UNIT, row.object, row.subsidiary)] = row
        if row.object == "1450" and row.subsidiary:
            continue
        if row.object == "2190":
            chosen[(groups.it[0], row.object, row.subsidiary)] = row
        elif row not in overhead:
            chosen[(MAIN_UNIT, row.object, row.subsidiary)] = row
    for supplier in suppliers:
        for string in supplier.accounts:
            unit, code = split_account(string)
            obj, sub = split_code(code)
            chosen.setdefault((unit, obj, sub), by_pair[(obj, sub)])
    objects = [row for row in overhead if not row.subsidiary]
    departments = [u.number for u in units if len(u.number) == 10 and u.number not in (MAIN_UNIT, RELOCATION_UNIT)]
    reserved = [(MAIN_UNIT, row.object, RECOVERIES) for row in objects]
    reserved += [(unit, row.object, ALLOCATION) for unit in departments for row in objects]
    candidates = reserved + [
        (unit, row.object, row.subsidiary) for unit in [MAIN_UNIT, *departments] for row in overhead
    ]
    technology = [row for row in overhead if row.object.startswith(TECHNOLOGY)]
    candidates += [(unit, row.object, row.subsidiary) for unit in groups.it for row in technology]
    rng.shuffle(candidates)
    if len(candidates) > 4 * plan.chart_rows:  # a small chart: the main unit's own accounts first
        candidates.sort(key=lambda key: key[0] != MAIN_UNIT)
    candidates = reserved[:3] + reserved[len(objects) : len(objects) + 2] + candidates
    for key in candidates:
        if len(chosen) >= plan.chart_rows:
            break
        unit, obj, sub = key
        label = {RECOVERIES: "Recovery-", ALLOCATION: "Int Alloc-"}.get(sub)
        row = _Row(obj, sub, 8, "", (label + by_pair[(obj, "")].description)[:30], ()) if label else by_pair[(obj, sub)]
        chosen.setdefault(key, row)
    ids = rng.sample(range(41000000, 48999999), len(chosen))
    ordered = sorted(chosen.items(), key=lambda item: (item[0][0].rjust(12), item[0][1], item[0][2]))
    return [Account(unit, obj, sub, row.description, row.level, row.edit, str(account_id), row.phrases)
            for ((unit, obj, sub), row), account_id in zip(ordered, ids, strict=True)]  # fmt: skip


# --- vouchers ------------------------------------------------------------------------------------


def _gl_dates(rng: random.Random, count: int, year: int) -> list[dt.date]:
    """A G/L date for each voucher: the months' shares of ``_MONTHLY_VOUCHERS``, weekdays only, in order."""
    total, dates = sum(_MONTHLY_VOUCHERS), []
    for month in range(1, 13):
        wanted = round(count * sum(_MONTHLY_VOUCHERS[:month]) / total) - len(dates)
        days = [dt.date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)]
        dates += sorted(rng.choices([d for d in days if d.weekday() < 5], k=wanted))
    return dates


def _draws(rng: random.Random, plan: Size, suppliers: list[Supplier]) -> list[Supplier]:
    """The supplier of each voucher: each kind its share of the vouchers, every supplier at least once, the busy
    ones much more often (``Supplier.share``)."""
    kinds: dict[str, list[Supplier]] = {kind: [] for kind in _KINDS}
    for supplier in suppliers:
        if supplier.kind != "unused":
            kinds["usd" if supplier.kind == "trade" and supplier.currency == "USD" else supplier.kind].append(supplier)
    order: list[Supplier] = []
    for kind, members in kinds.items():
        wanted = plan.vouchers - len(order) if kind == "trade" else round(plan.vouchers * _KINDS[kind][0])
        more = max(0, wanted - len(members))
        order += members + rng.choices(members, weights=[s.share for s in members], k=more)
    rng.shuffle(order)
    return order


def _oddities(rng: random.Random, plan: Size, order: list[Supplier]) -> dict[int, str]:
    """Which vouchers are odd, and how. The rates are a real year's; a small set has one of each (two with no
    remarks) so a test meets them all, and the 130-line voucher is a year's only."""
    wanted = {"negative": 0.006, "unbalanced": 0.006, "no_remarks": 0.02, "no_address": 0.005,
              "nine_lines": 0.0, "under_a_dollar": 0.0, "many_lines": 0.0}  # fmt: skip
    busiest = max(order, key=lambda s: len(s.accounts) if s.kind == "trade" else 0)
    fits = {"unbalanced": lambda s: s.taxed and s.kind == "trade", "no_address": lambda s: s.kind == "trade",
            "nine_lines": lambda s: s.kind == "affiliate", "under_a_dollar": lambda s: s.kind == "employee",
            "many_lines": lambda s: s is busiest, "negative": lambda s: s.kind in ("trade", "employee")}  # fmt: skip
    odd: dict[int, str] = {}
    for oddity, rate in wanted.items():
        if oddity == "many_lines" and plan.vouchers < 500:  # too small a set for a 130-line voucher
            continue
        free = [i for i, s in enumerate(order) if i not in odd and fits.get(oddity, lambda s: True)(s)]
        for i in rng.sample(free, min(len(free), max(1, round(rate * plan.vouchers)))):
            odd[i] = oddity
    return odd


class _Entry:
    """What entering the vouchers needs to remember: the chart, how many lines each supplier has had (its accounts
    are met in turn first), the document and batch numbers."""

    STAYS = 0.45  # how often a voucher of several lines keeps to its first line's business unit

    def __init__(self, rng: random.Random, company: Company, accounts: list[Account], places: list[str]) -> None:
        self.rng, self.company, self.places = rng, company, places
        self.accounts = {a.string: a for a in accounts}
        self.lines_so_far: Counter[int] = Counter()
        self.document, self.batch = rng.randrange(205000, 240000), rng.randrange(7100000, 7200000)

    def targets(self, supplier: Supplier, count: int) -> list[str]:
        """The account of each coding line. A supplier's first lines go through its accounts in turn; after that
        the first ones are the usual ones, and a voucher of several lines mostly stays in one business unit."""
        rng, pool, start = self.rng, list(supplier.accounts), self.lines_so_far[supplier.number]
        self.lines_so_far[supplier.number] += count
        if count >= 9:
            return [pool[i % len(pool)] for i in range(count)]
        picks = pool[start : start + count] or rng.choices(pool, weights=[1 / (i + 1) for i in range(len(pool))])
        first = picks[0]
        unit = split_account(first)[0]
        ring = [a for a in pool if split_account(a)[0] == unit] if rng.random() < self.STAYS else pool
        while len(picks) < count:
            picks.append(ring[(ring.index(first) + len(picks)) % len(ring)])
        return picks

    def remark(self, supplier: Supplier, account: str, date: dt.date, place: str) -> str:
        """A remark of 30 characters at most: the account's words (or, now and then, words that say nothing),
        sometimes the month or initials, often the office it is for."""
        rng, phrases = self.rng, self.accounts[account].phrases
        telling = rng.random() < (0.85 if len(supplier.accounts) > 1 else 0.7)
        words = rng.choice(phrases) if phrases and telling else rng.choice(GENERIC_REMARKS)
        month, style = calendar.month_abbr[date.month], rng.random()
        if style < 0.22:
            words = f"{month} {words}"
        elif style < 0.30:
            words = f"{rng.choice('ADJKLMRST')}{rng.choice('BCDGHLMPW')} {month}.{words}"
        return f"{words} {place}"[:30].strip()


def _line_count(rng: random.Random, kind: str, oddity: str) -> int:
    if oddity in ("nine_lines", "many_lines"):
        return 9 if oddity == "nine_lines" else 130
    one, some, _ = _KINDS[kind][1]
    u = rng.random()
    if u < one or oddity == "under_a_dollar":
        return 1
    return rng.choices((2, 3, 4, 5), weights=(50, 25, 15, 10))[0] if u < one + some else rng.randint(6, 9)


def _pay_items(rng: random.Random, voucher_gross: Decimal, tax: Decimal, foreign: Decimal | None, count: int,
               due: dt.date, status: str) -> tuple[PayItem, ...]:  # fmt: skip
    """The voucher's pay items: the gross split in ``count`` parts, the tax with it (the last takes the cents)."""
    cuts = sorted(rng.uniform(0.15, 0.85) for _ in range(count - 1))
    shares = [b - a for a, b in zip([0.0, *cuts], [*cuts, 1.0], strict=True)]
    items, gross_left, tax_left = [], voucher_gross, tax
    for n, share in enumerate(shares, start=1):
        last = n == count
        gross = gross_left if last else money(voucher_gross * Decimal(str(share)))
        item_tax = tax_left if last else money(tax * Decimal(str(share)))
        gross_left, tax_left = gross_left - gross, tax_left - item_tax
        items.append(PayItem(f"{n:03d}", gross, gross - item_tax, item_tax, "V" if tax else "", TAX_AREA if tax else "",
                             due, status, foreign))  # fmt: skip
    return tuple(items)


def _voucher(entry: _Entry, supplier: Supplier, gl_date: dt.date, oddity: str) -> Voucher:
    """One voucher as AP entered it: the coding lines (net; PST or QST inside them), then the pay items with the
    GST. A foreign invoice's lines are converted line by line, so the journal balances in Canadian dollars."""
    rng, company = entry.rng, entry.company
    kind = supplier.kind
    count = _line_count(rng, kind, oddity)
    place = (supplier.location if rng.random() < 0.8 else rng.choice(entry.places)) if rng.random() < 0.65 else ""
    median, spread = _AMOUNTS[kind]
    sign = -1 if oddity == "negative" else 1
    rate = Decimal(str(round(rng.uniform(1.34, 1.40), 4))) if supplier.currency != "CAD" else None
    lines = []
    for account in entry.targets(supplier, count):
        amount = sign * money(max(1.0, rng.lognormvariate(0, spread) * median / (1 + 0.15 * count)))
        if oddity == "under_a_dollar":
            amount = money(rng.uniform(0.11, 0.94))
        remark = "" if oddity == "no_remarks" else entry.remark(supplier, account, gl_date, place)
        lines.append(CodingLine(account, money(amount * rate) if rate else amount, remark, amount if rate else None))
    net = sum((line.amount for line in lines), Decimal(0))
    before_provincial = net / (1 + supplier.provincial_rate)
    tax = money(before_provincial * GST_RATE) if supplier.taxed else Decimal(0)
    order = f"{rng.randrange(52000, 58999):08d}" if supplier.purchase_orders and rng.random() < 0.6 else ""
    several = rng.random() < (0.35 if order else 0.04) and not rate and abs(net) > 20
    invoice_date = gl_date - dt.timedelta(days=rng.randint(0, 20))
    status = "P" if gl_date < dt.date(company.year, 12, 8) else rng.choice("AAH")
    foreign = sum((line.foreign_amount for line in lines), Decimal(0)) if rate else None
    items = _pay_items(rng, net + tax, tax, foreign, rng.randint(2, 4) if several else 1,
                       invoice_date + dt.timedelta(days=30), status)  # fmt: skip
    entry.document += rng.randint(1, 3)
    entry.batch += rng.randint(40, 900) if rng.random() < 0.6 else 0
    batch_date = gl_date + dt.timedelta(days=rng.randint(0, 5))
    updated = dt.datetime.combine(batch_date + dt.timedelta(days=rng.randint(0, 7)), dt.time(rng.randint(8, 17)))
    return Voucher(
        document=entry.document, batch=entry.batch, batch_date=batch_date, supplier=supplier.number,
        invoice_number=_invoice_number(rng, supplier, gl_date), invoice_date=invoice_date, gl_date=gl_date,
        currency=supplier.currency, exchange_rate=rate, purchase_order=order, business_unit=company.main_business_unit,
        payable_object=company.intercompany_object if kind == "affiliate" else company.payable_object,
        lines=tuple(lines), pay_items=items, provincial_tax=money(net - before_provincial), user=rng.choice(USERS),
        updated=updated.replace(minute=rng.randrange(60), second=rng.randrange(60)), oddity=oddity)  # fmt: skip


def _invoice_number(rng: random.Random, supplier: Supplier, date: dt.date) -> str:
    month = calendar.month_abbr[date.month].upper()
    if supplier.kind == "employee":
        return rng.choice((f"EXP {month} {date.year % 100}", f"{month} VISA", f"CLAIM {rng.randrange(10000, 99999)}"))
    if supplier.kind == "remittance":
        return f"REMIT {month} {date.day:02d} {date.year}"
    return rng.choice((f"{rng.randrange(10000, 9999999)}", f"INV-{rng.randrange(10000, 99999)}",
                       f"{rng.choice('ABCKMRT')}{rng.randrange(100000, 9999999)}"))  # fmt: skip


def build(seed: int = 1, size: str = "small", year: int = 2024) -> Truth:
    """The made-up company's books: ``size`` is "small" (tests: about 20 business units, 250 chart rows, 40
    suppliers, 120 vouchers) or "year" (about 130, 14,000, 970 and 5,500). The same arguments give the same books."""
    plan = SIZES[size]
    rng = {part: random.Random(f"{seed}:{size}:{part}") for part in ("units", "rows", "suppliers", "chart", "entry")}
    company = Company(COMPANY_NUMBER, "MAPLE RIDGE HOLDINGS LTD.", "MAPLE RIDGE GROUP OF COMPANIES", DISTRICT,
                      "700100200RT0001", year, MAIN_UNIT, PAYABLE, INTERCOMPANY, GST_CLEARING, PST_CLEARING,
                      (RECOVERIES, ALLOCATION), TAX_AREA, account_string(MAIN_UNIT, "1120", "CDOPS"))  # fmt: skip
    units = _business_units(rng["units"], plan)
    groups = _groups(rng["units"], plan, units)
    rows = _catalogue() + _generated_rows(rng["rows"], plan)
    places = _locations(rng["rows"], plan)
    suppliers = _suppliers(rng["suppliers"], plan, _Pairs(rows, groups), places)
    accounts = _chart(rng["chart"], plan, units, rows, groups, suppliers)
    entry = _Entry(rng["entry"], company, accounts, places)
    order = _draws(rng["entry"], plan, suppliers)
    odd = _oddities(rng["entry"], plan, order)
    dates = _gl_dates(rng["entry"], plan.vouchers, year)
    vouchers = [_voucher(entry, supplier, date, odd.get(i, "")) for i, (supplier, date) in
                enumerate(zip(order, dates, strict=True))]  # fmt: skip
    return Truth(company, units, accounts, suppliers, vouchers)
