"""Made-up supplier invoices for the made-up company of ``jde_company``: PDFs with ground truth, so a test drops an
invoice and knows the account string, the remark and the tax each line should get.

An ``Invoice`` is what a supplier prints (its lines in its own words, the taxes, the totals) and what AP keys from it:
for each printed line one ``Part`` (the account string, the amount as keyed and the department's remark), or two for
a line split across business units. Only the GST or HST is recovered: it goes on the pay item (tax explanation code
``V``, a tax area, the taxable amount and the tax) and is in no line; a PST or QST stays inside the lines' amounts.

* ``named(truth)``: a fixed set of seven that covers a day (``NAMED``): one line from a supplier that always goes to
  one account; four lines to three accounts, the last worded so that nothing says which (left for a person); fifteen
  lines from the supplier with the most accounts; a line to split across two business units; a Quebec supplier (GST
  recovered, QST in the expense); a USD invoice from a supplier that is not in the supplier master; a credit note.
* ``from_vouchers(truth, n)``: more, each made from a voucher of a trade supplier in the books (its accounts and
  remarks, its amounts to the cent before tax), worded as a supplier words it.
* ``write(folder, truth, invoices)``: each rendered by the capture benchmark's renderer (``generator.Renderer``, its
  layouts and styles; a supplier's invoices all look alike) as ``<name>.pdf`` with ``<name>.truth.json``: the
  benchmark's truth of what is printed where, and under ``coding`` what AP keys.

The same books, invoices and seed give the same files."""

from __future__ import annotations

import calendar
import datetime as dt
import random
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ..coding import money as shares
from ..core import chart
from . import content as C
from .generator import MISREAD_BASES, Case, Inv, Renderer, Style, _header_items, _knobs, _write_case
from .jde_company import MAIN_UNIT, Account, Supplier, Truth, Voucher, money

NAMED = ("one_line", "four_lines", "fifteen_lines", "split_line", "quebec", "usd_new_supplier", "credit_note")
HOME_PROVINCE = "AB"  # head office: a supplier there charges the GST alone
PROVINCES = {Decimal("0.07"): "BC", Decimal("0.09975"): "QC"}  # a supplier's provincial rate: where it supplies
RECOVERED = ("GST", "HST")  # the taxes on the pay item; any other stays in the lines
TAX_CODE = "V"
PLAIN_LAYOUTS = MISREAD_BASES  # the benchmark's plain layouts: a supplier keeps one
# The benchmark's date styles in which a day cannot be taken for a month: the invoices are dated near any day.
DATE_STYLES = ("iso", "mon_d_y", "month_d_y", "d_mon_y", "dd-mon-yyyy", "yyyy/mm/dd")
NEW_SUPPLIER = "Harlow Peak Analytics LLC"  # in no supplier master
TERMS = C.Terms("Net 30", 30)

# What a supplier prints for a purchase AP codes to an account: object, subsidiary ("-": every subsidiary of the
# object) | its phrasings, a semicolon between; ``{n}`` a reference number, ``{month}`` the month billed, ``{who}``
# the person the account's description names. The supplier's own words, not the department's remark
# (``jde_names.CHART``'s phrases). A small table of this module's own, in the layout of the fuller
# ``jde_names.INVOICE_WORDING`` being written beside it: to be replaced by that one.
INVOICE_WORDING = """
1450 - | Household goods move - transferee {who}; Furnished suite 30 nights - guest {who}
1620 - | Annual term paid in advance - contract {n}; 12-month term - renewal notice {n}
7120 - | Candidate search - engagement {n}; Posting package 30 days - order {n}
7140 - | Registration - workshop series {n}; Annual member fee - ID {n}
7160 - | Group event package - booking {n}; Custom embroidered apparel - order {n}
7310 - | Base rent {month} - suite 400; Additional rent - common area {month}
7330 - | Service call - work order {n}; Preventive maintenance visit - {month}
7340 - | Nightly service - {month}; Secure bin exchange - ticket {n}
7350 - | Energy charges - meter {n}; Delivery and consumption - {month}
7360 - | Commercial property coverage - policy {n}; Annual assessment - roll {n}
7410 - | Assorted office products - order {n}; Breakroom restock - order {n}
7420 - | Ground shipment waybill {n}; Express envelope - waybill {n}
7430 - | Colour and mono clicks - {month}; Print job {n} - saddle stitched
7440 - | Annual access - account {n}; Publication renewal - 12 issues
7450 - | Carton storage - {month}; Document imaging - job {n}
7510 - | Wireless service - {month}; Voice lines and features - {month}
7520 - | Dedicated access 500 Mbps - {month}; Managed circuit - site {n}
7530 - | Platform seats - annual term {n}; Maintenance and support - contract {n}
7540 - | Hardware order {n}; Bench service - ticket {n}
7550 - | Monthly managed plan - {month}; Professional services - statement of work {n}
7610 - | Professional services rendered - file {n}; Fees and disbursements - matter {n}
7620 - | Professional services - engagement {n}; Interim billing - engagement {n}
7630 - | Advisory engagement {n} - phase 2; Language services - job {n}
7640 - | Commercial general coverage - policy {n}; Placement fee - policy {n}
7710 - | Room and tax - folio {n}; Ticket {n} - economy fare
7720 - | Group order {n} - delivered; Private dining - booking {n}
7730 - | Delegate pass - order {n}; Boardroom booking {n} - full day
7810 - | Annual campaign gift - receipt {n}; Event partner package - {month}
7830 - | Fleet card purchases - {month}; Service visit - invoice {n}
"""
# The words of a line that does not say what was bought: with a supplier coded to several accounts, a person decides.
VAGUE = "Additional charges per work order {n}"


@dataclass(frozen=True)
class Part:
    """What AP keys for a printed line, or for one share of a line split across business units."""

    account: str  # BusinessUnit.Object.Subsidiary
    amount: Decimal  # as keyed: without the GST or HST, a provincial tax inside
    remark: str  # the department's words, 30 characters at most


@dataclass(frozen=True)
class Line:
    description: str  # as the supplier prints it
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal  # as printed, before tax
    taxes: tuple[tuple[str, Decimal], ...]  # its share of each tax charged: ("GST", 12.00)
    parts: tuple[Part, ...]
    for_a_person: bool = False  # nothing printed says which of the supplier's accounts: no account is to be put in


@dataclass(frozen=True)
class Invoice:
    name: str  # the file's name: one of ``NAMED``, or "voucher_<document>"
    supplier: Supplier | None  # None: not in the supplier master
    supplier_name: str  # as printed
    number: str
    date: dt.date
    currency: str
    province: str  # where it is supplied: whose taxes it charges ("": outside Canada)
    layout: str  # the benchmark's archetype
    lines: tuple[Line, ...]
    taxes: tuple[C.Tax, ...]  # as printed
    tax_area: str  # the pay item's, when a tax is recovered
    purchase_order: str = ""
    document: int | None = None  # the voucher of the books it was made from

    @property
    def subtotal(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal(0))

    @property
    def total(self) -> Decimal:
        return self.subtotal + sum((tax.amount for tax in self.taxes), Decimal(0))

    @property
    def recovered(self) -> Decimal:
        """The tax on the pay item: the GST or HST."""
        return sum((tax.amount for tax in self.taxes if tax.code in RECOVERED), Decimal(0))

    @property
    def parts(self) -> list[Part]:
        """The G/L distribution as keyed, in the lines' order: with ``recovered`` it adds up to ``total``."""
        return [part for line in self.lines for part in line.parts]

    def coding(self) -> dict:
        """What AP keys, as the truth file holds it: the pay item's fields, then each printed line with its parts."""
        taxed = bool(self.recovered)
        return {
            "name": self.name, "document": self.document,
            "supplier_number": self.supplier.number if self.supplier else None,
            "supplier_name": self.supplier.name if self.supplier else "",
            "currency": self.currency, "gross": float(self.total),
            "tax_code": TAX_CODE if taxed else "", "tax_area": self.tax_area if taxed else "",
            "taxable": float(self.subtotal) if taxed else None, "tax": float(self.recovered) if taxed else None,
            "in_the_expense": {tax.code: float(tax.amount) for tax in self.taxes if tax.code not in RECOVERED},
            "lines": [
                {"description": line.description, "amount": float(line.amount), "for_a_person": line.for_a_person,
                 "taxes": {code: float(amount) for code, amount in line.taxes},
                 "parts": [{"account": part.account, "amount": float(part.amount), "remark": part.remark}
                           for part in line.parts]}
                for line in self.lines
            ],
        }  # fmt: skip


# --- wording -------------------------------------------------------------------------------------


def invoice_wording() -> dict[tuple[str, str], tuple[str, ...]]:
    """{(object, subsidiary): what a supplier prints for it}; a subsidiary "" stands for all of the object's."""
    found = {}
    for line in INVOICE_WORDING.strip().splitlines():
        head, _, phrasings = line.partition("|")
        obj, subsidiary = head.split()
        found[(obj, subsidiary.strip("-"))] = tuple(p.strip() for p in phrasings.split(";") if p.strip())
    return found


_WORDING = invoice_wording()


def printed_name(name: str) -> str:
    """A supplier's name as its own invoice prints it: the master's capitals in mixed case."""
    return " ".join(word if word in ("LLC", "&") else word.capitalize() for word in name.split())


def _words(account: Account, which: int, number: int, on: dt.date) -> str:
    """What a supplier prints for a purchase coded to ``account``: its ``which``-th phrasing (the account's
    description when the table has none), with reference number ``number``."""
    phrasings = (_WORDING.get((account.object, account.subsidiary)) or _WORDING.get((account.object, ""))
                 or (account.description,))  # fmt: skip
    return phrasings[which % len(phrasings)].format(n=number, month=calendar.month_name[on.month],
                                                    who=account.description.rpartition(" - ")[2])  # fmt: skip


def _remark(truth: Truth, supplier: Supplier | None, account: str) -> str:
    """The department's remark for a line coded to ``account``: the account's words, then the office it is for."""
    row = truth.account(account)
    words = row.phrases[0] if row.phrases else row.description
    return f"{words} {supplier.location if supplier else ''}"[:30].strip()


# --- an invoice from its lines -------------------------------------------------------------------


def _taxes(supplier: Supplier | None, subtotal: Decimal) -> tuple[str, tuple[C.Tax, ...]]:
    """(where the invoice is supplied, the taxes it charges): the GST when the supplier charges it, with the PST or
    QST of the supplier's province; none from a supplier that is not registered or is outside Canada."""
    if supplier is None or supplier.currency != "CAD":
        return "", ()
    province = PROVINCES.get(supplier.provincial_rate, HOME_PROVINCE)
    return province, tuple(C.compute_taxes(province, subtotal)) if supplier.taxed else ()


def _invoice(truth: Truth, name: str, supplier: Supplier | None, number: str, date: dt.date,
             printed: list[tuple[str, Decimal, Decimal, list[tuple[str, int]], bool]], *, layout: str = "",
             supplier_name: str = "", currency: str = "", purchase_order: str = "", document: int | None = None,
             remarks: list[str] | None = None) -> Invoice:  # fmt: skip
    """The invoice of ``printed`` lines, each (description, quantity, unit price, [(account, weight)], left for a
    person): the taxes worked out on the subtotal, each line's share of each tax (``money.split``: what is left over
    on the largest), and each part's amount as keyed. ``remarks``: the parts' remarks, when the books give them."""
    amounts = [money(quantity * price) for _, quantity, price, _, _ in printed]
    province, taxes = _taxes(supplier, sum(amounts, Decimal(0)))
    weights = [float(amount) for amount in amounts]
    spread = {tax.code: [money(s) for s in shares.split(float(tax.amount), weights)] for tax in taxes}
    told = iter(remarks or [])
    lines = []
    for n, ((description, quantity, price, split, for_a_person), amount) in enumerate(zip(printed, amounts,
                                                                                         strict=True)):  # fmt: skip
        mine = tuple((code, spread[code][n]) for code in spread)
        keyed = amount + sum((share for code, share in mine if code not in RECOVERED), Decimal(0))
        cut = [money(s) for s in shares.split(float(keyed), [float(weight) for _, weight in split])]
        parts = tuple(Part(account, part, next(told, None) or _remark(truth, supplier, account))
                      for (account, _), part in zip(split, cut, strict=True))  # fmt: skip
        lines.append(Line(description, quantity, price, amount, mine, parts, for_a_person))
    style = random.Random(f"jde-invoice-layout:{supplier.number if supplier else supplier_name}")
    return Invoice(
        name, supplier, supplier_name or printed_name(supplier.name), number, date,
        currency or supplier.currency, province, layout or style.choice(PLAIN_LAYOUTS), tuple(lines), taxes,
        truth.company.tax_area, purchase_order, document)  # fmt: skip


def _in_use(supplier: Supplier) -> bool:
    """A trade supplier AP pays: not on hold, its name not marked as retired in the master."""
    return (supplier.kind == "trade" and supplier.hold_payment == "N" and "*" not in supplier.name
            and not chart.skip_word(supplier.name))  # fmt: skip


def _codes(supplier: Supplier) -> list[str]:
    """The supplier's object.subsidiary codes, one for each of its accounts."""
    return [chart.split_account(account)[1] for account in supplier.accounts]


# --- the seven -----------------------------------------------------------------------------------


def _suppliers_of_the_seven(truth: Truth) -> dict[str, Supplier]:
    """The supplier of each invoice of ``NAMED`` that is from one of the books': the first that fits, none twice,
    so the set follows the books. ``LookupError`` when the books have none for one."""
    home = [s for s in truth.suppliers if _in_use(s) and s.currency == "CAD" and s.taxed]
    plain = [s for s in home if not s.provincial_rate]  # the GST alone
    most = max((len(s.accounts) for s in plain), default=0)
    wanted = {
        "fifteen_lines": lambda s: s in plain and len(s.accounts) == most,
        "one_line": lambda s: s in plain and len(s.accounts) == 1,
        "four_lines": lambda s: s in plain and len(set(_codes(s))) >= 3,
        "split_line": lambda s: s in plain and len(s.accounts) >= 2 and len(set(_codes(s))) == 1,
        "quebec": lambda s: PROVINCES.get(s.provincial_rate) == "QC",
        "credit_note": lambda s: s in plain and len(s.accounts) == 1,
    }
    found: dict[str, Supplier] = {}
    for name, fits in wanted.items():
        supplier = next((s for s in home if s not in found.values() and fits(s)), None)
        if supplier is None:
            raise LookupError(f"These books have no supplier for the invoice {name}.")
        found[name] = supplier
    return found


def named(truth: Truth, on: dt.date | None = None) -> list[Invoice]:
    """The seven invoices of ``NAMED``, in that order, dated in the nine days before ``on`` (default: 20 January of
    the year after the books')."""
    on = on or dt.date(truth.company.year + 1, 1, 20)
    of = _suppliers_of_the_seven(truth)
    units = {unit.number: unit.name for unit in truth.business_units}

    def words(account: str, which: int = 0) -> str:
        return _words(truth.account(account), which, 30418 + which, on)

    def line(text: str, quantity: str, price: str, *accounts: tuple[str, int], for_a_person: bool = False):
        return text, Decimal(quantity), Decimal(price), list(accounts), for_a_person

    one, quebec, credited = (of[name].accounts[0] for name in ("one_line", "quebec", "credit_note"))
    a, b, c = of["four_lines"].accounts[:3]
    here, there = of["split_line"].accounts[:2]
    many = of["fifteen_lines"].accounts
    software = next(x for x in truth.suggestable() if x.business_unit == MAIN_UNIT and x.object == "7530")
    billed = len(set(_codes(of["fifteen_lines"]))) < len(many)  # one code on several business units: a line each
    fifteen = []
    for i, cents in enumerate(random.Random("jde-invoice:fifteen").sample(range(3800, 26000, 25), 15)):
        account = many[i % len(many)]
        group = f" - {units[chart.split_account(account)[0]]}" if billed else ""
        fifteen.append(line(words(account, i) + group, "1", str(Decimal(cents) / 100), (account, 1)))
    printed = {
        "one_line": [line(words(one), "1", "4800.00", (one, 1))],
        "four_lines": [
            line(words(a), "1", "640.00", (a, 1)),
            line(words(a, 1), "2", "85.50", (a, 1)),
            line(words(b), "1", "420.00", (b, 1)),
            line(VAGUE.format(n=30422), "1", "96.25", (c, 1), for_a_person=True),
        ],
        "fifteen_lines": fifteen,
        "split_line": [
            line(words(here), "1", "310.00", (here, 1)),
            line(words(here, 1), "1", "480.00", (here, 60), (there, 40)),
        ],
        "quebec": [
            line(words(quebec), "1", "1000.00", (quebec, 1)),
            line(words(quebec, 1), "2", "120.00", (quebec, 1)),
        ],
        "usd_new_supplier": [line(_words(software, 0, 77120, on), "25", "96.00", (software.string, 1))],
        "credit_note": [line(words(credited), "-2", "289.00", (credited, 1))],
    }
    numbers = ("60417", "INV-21873", "8841207", "INV-07755", "F-55102", "HP-10093", "CN-00318")
    more = {
        "usd_new_supplier": {"layout": "us_vendor", "supplier_name": NEW_SUPPLIER, "currency": "USD"},
        "credit_note": {"layout": "credit_note"},
    }
    return [
        _invoice(truth, name, of.get(name), number, on - dt.timedelta(days=9 - n), printed[name], **more.get(name, {}))
        for n, (name, number) in enumerate(zip(NAMED, numbers, strict=True))
    ]


# --- more, from the books' vouchers --------------------------------------------------------------


def from_vouchers(truth: Truth, n: int, seed: int = 1) -> list[Invoice]:
    """``n`` invoices (fewer when the books have fewer), each from a voucher of a trade supplier in use, the books'
    last first: the voucher's accounts, remarks and invoice number, its lines' amounts before tax to the cent (the
    foreign amount of a USD one), the taxes worked out again on what is printed. A billback is worded as the purchase
    it bills back (the invoice does not say who pays in the end). A voucher that does not balance is not one."""
    fit = [v for v in reversed(truth.vouchers) if _in_use(truth.supplier(v.supplier)) and v.oddity != "unbalanced"]
    return [_from_voucher(truth, voucher, random.Random(f"jde-invoice:{seed}:{voucher.document}")) for voucher in
            fit[:n]]  # fmt: skip


def _from_voucher(truth: Truth, voucher: Voucher, rng: random.Random) -> Invoice:
    supplier = truth.supplier(voucher.supplier)
    bought = [a for a in supplier.accounts if truth.account(a).object != "2480"]
    printed = []
    for coded in voucher.lines:
        row = truth.account(coded.account)
        if row.object == "2480" and len(row.subsidiary) == 6 and bought:
            row = truth.account(bought[0])
        amount = money((coded.foreign_amount or coded.amount) / (1 + supplier.provincial_rate))
        text = _words(row, rng.randrange(2), rng.randrange(10000, 99999), voucher.invoice_date)
        printed.append((text, Decimal(-1 if amount < 0 else 1), abs(amount), [(coded.account, 1)], False))
    layout = {"USD": "us_vendor"}.get(supplier.currency, "credit_note" if voucher.net < 0 else "")
    return _invoice(truth, f"voucher_{voucher.document}", supplier, voucher.invoice_number, voucher.invoice_date,
                    printed, layout=layout, purchase_order=voucher.purchase_order, document=voucher.document,
                    remarks=[coded.remark for coded in voucher.lines])  # fmt: skip


# --- the files -----------------------------------------------------------------------------------


def render(invoice: Invoice, truth: Truth, seed: int = 1) -> tuple[bytes, dict]:
    """(the PDF, its truth): the invoice laid out by the benchmark's renderer in its supplier's style, billed to the
    books' company at its site in the province supplied (head office for an invoice from outside Canada)."""
    rng = random.Random(f"jde-invoice:{seed}:{invoice.name}")
    style = Style(rng, key=f"jde-supplier:{seed}:{invoice.supplier_name}")
    foreign = not invoice.province
    knobs = _knobs(style("knobs"), invoice.layout)
    knobs["stub"] = False  # a payment stub keeps an invoice to a few lines
    if invoice.currency != "CAD":
        knobs["currency_where"] = "header"  # a foreign invoice says its currency
    vendor = C.make_vendor(style("vendor"), "us" if foreign else "en", invoice.province)
    domain = C.slug(invoice.supplier_name)
    vendor.email, vendor.web = (text.replace(C.slug(vendor.name), domain) for text in (vendor.email, vendor.web))
    vendor.name = invoice.supplier_name
    customer = C.make_customer(style("customer"), invoice.province or HOME_PROVINCE)
    customer.name = printed_name(truth.company.name)
    credit = invoice.layout == "credit_note"
    lines = [C.LineItem(line.description, line.quantity, "", line.unit_price, line.amount) for line in invoice.lines]
    inv = Inv(
        invoice.layout, "credit" if credit else "invoice", "en", "US" if foreign else "CA", invoice.province, vendor,
        customer, invoice.currency, invoice.number, invoice.date,
        None if credit else invoice.date + dt.timedelta(days=TERMS.days), invoice.purchase_order or None,
        None if credit else TERMS, style("date_style").choice(DATE_STYLES),
        C.money_style(style("money"), "en", invoice.currency), lines, None, list(invoice.taxes), invoice.subtotal,
        invoice.total, knobs, sty=style)  # fmt: skip
    inv.header = _header_items(rng, inv)
    pdf, printed = Renderer(inv, rng).render()
    return pdf, {**printed, "coding": invoice.coding()}


def write(folder: str | Path, truth: Truth, invoices: list[Invoice], seed: int = 1) -> list[Case]:
    """Each invoice as ``<name>.pdf`` with ``<name>.truth.json`` in ``folder`` (made if missing): the benchmark's
    cases (``generator.load_cases`` reads them back)."""
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    return [_write_case(out, invoice.name, *render(invoice, truth, seed), False, "") for invoice in invoices]
