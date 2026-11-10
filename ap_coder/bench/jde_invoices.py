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
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ..ai.supplier_prior import SURE_LINES
from ..coding import money as shares
from ..core import chart
from . import content as C
from .generator import MISREAD_BASES, Case, Inv, Renderer, Style, _header_items, _knobs, _write_case
from .jde_company import MAIN_UNIT, Account, Supplier, Truth, Voucher, money
from .jde_names import invoice_wording

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


_WORDING = invoice_wording()  # the one table of what a supplier prints for an account (``jde_names``)
QUANTITIES = (2, 3, 4, 6, 12, 24, 40)  # a phrasing's ``{qty}``, by its reference number


def printed_name(name: str) -> str:
    """A supplier's name as its own invoice prints it: the master's capitals in mixed case."""
    return " ".join(word if word in ("LLC", "&") else word.capitalize() for word in name.split())


def _words(account: Account, which: int, number: int, on: dt.date) -> str:
    """What a supplier prints for a purchase coded to ``account``: its ``which``-th phrasing (the account's
    description when the table has none), with reference number ``number``."""
    phrasings = (_WORDING.get((account.object, account.subsidiary)) or _WORDING.get((account.object, ""))
                 or (account.description,))  # fmt: skip
    return phrasings[which % len(phrasings)].format(n=number, qty=QUANTITIES[number % len(QUANTITIES)],
                                                    month=calendar.month_name[on.month],
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
    so the set follows the books. ``LookupError`` when the books have none for one. The one-line invoice's has the
    lines last year that code a line with no one picking (``supplier_prior.SURE_LINES``, all to its one account)."""
    home = [s for s in truth.suppliers if _in_use(s) and s.currency == "CAD" and s.taxed]
    plain = [s for s in home if not s.provincial_rate]  # the GST alone
    most = max((len(s.accounts) for s in plain), default=0)
    history = Counter(v.supplier for v in truth.vouchers if v.oddity != "unbalanced" for _ in v.lines)
    wanted = {
        "fifteen_lines": lambda s: s in plain and len(s.accounts) == most,
        "one_line": lambda s: s in plain and len(s.accounts) == 1 and history[s.number] >= SURE_LINES,
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


# --- the hard cases ------------------------------------------------------------------------------

HARD = ("sixty_lines", "usd", "do_not_use", "on_hold", "bc_pst", "odd_line")
PRINTED = ("exempt_line", "two_provinces", "tax_column", "hash_column")
HOLDS = ("Y", "1", "2")  # JD Edwards' hold payment codes that hold a supplier's payments
_MARK = re.compile(r"[\s*-]*(\*+|DO NOT USE|DNU)[\s*]*$")  # how the master marks a supplier no longer to be used


def hard_cases(truth: Truth, on: dt.date | None = None) -> list[Invoice]:
    """The invoices of ``HARD``, in that order, from suppliers of the books, dated ``on`` (as for
    ``named``): sixty lines from the supplier that always goes to one account; a USD invoice from a US supplier in
    use; one from a supplier the master marks DO NOT USE (printed without the mark); one from a supplier whose
    payments are held; GST and British Columbia's PST (the PST stays in the expense); and a lunch on the invoice of
    the supplier that always goes to one account. ``LookupError`` when the books have no supplier for one."""
    on = on or dt.date(truth.company.year + 1, 1, 20)
    of = _suppliers_of_the_seven(truth)
    trade = [s for s in truth.suppliers if s.kind == "trade" and s.accounts]

    def first(what: str, fits) -> Supplier:
        found = next((s for s in trade if fits(s)), None)
        if found is None:
            raise LookupError(f"These books have no supplier for the invoice {what}.")
        return found

    def words(account: str, which: int = 0) -> str:
        return _words(truth.account(account), which, 40500 + which, on)

    def line(text: str, quantity: str, price: str, account: str):
        return text, Decimal(quantity), Decimal(price), [(account, 1)], False

    def lines(supplier: Supplier, *prices: str):
        return [line(words(supplier.accounts[0], n), "1", price, supplier.accounts[0])
                for n, price in enumerate(prices)]  # fmt: skip

    one = of["one_line"]
    usd = first("usd", lambda s: _in_use(s) and s.currency == "USD")
    retired = first("do_not_use", lambda s: s.hold_payment == "N" and s.currency == "CAD" and _MARK.search(s.name))
    held = first("on_hold", lambda s: s.hold_payment in HOLDS and s.currency == "CAD" and not _MARK.search(s.name))
    coast = first("bc_pst", lambda s: _in_use(s) and PROVINCES.get(s.provincial_rate) == "BC")
    cents = random.Random("jde-invoice:sixty").sample(range(1800, 9600, 5), 60)
    sixty = [line(words(one.accounts[0], n), "1", str(Decimal(c) / 100), one.accounts[0]) for n, c in enumerate(cents)]
    made = {
        "sixty_lines": _invoice(truth, "sixty_lines", one, "60988", on, sixty),
        "usd": _invoice(truth, "usd", usd, "US-44817", on, lines(usd, "1250.00", "380.00"), layout="us_vendor"),
        "do_not_use": _invoice(truth, "do_not_use", retired, "IW-20418", on, lines(retired, "214.60"),
                               supplier_name=printed_name(_MARK.sub("", retired.name))),
        "on_hold": _invoice(truth, "on_hold", held, "AT-7731", on, lines(held, "1900.00")),
        "bc_pst": _invoice(truth, "bc_pst", coast, "BC-30117", on, lines(coast, "400.00", "150.00")),
        "odd_line": _invoice(truth, "odd_line", one, "60991", on, [
            *lines(one, "64.20"), line("Working lunch for the project kickoff, 12 people", "1", "186.40",
                                       one.accounts[0])]),
    }  # fmt: skip
    return [made[name] for name in HARD]


def printed_cases(truth: Truth, on: dt.date | None = None) -> dict[str, bytes]:
    """Invoices set out plainly, where what is printed is the case (the names of ``PRINTED``), as PDFs: three lines
    of which the GST is charged on two (the third says exempt); lines supplied in two provinces, the GST on Alberta's
    and Ontario's HST on the other (each tax printed with what it is charged on); a line table of Description, Tax
    and Amount (the tax of each line in a column of its own: the amounts are before tax); and a table with a "#"
    column and one row. The first two are from the supplier of ``named``'s credit note (one account, too few lines
    last year to code alone), the others from the one that always goes to one account."""
    on = on or dt.date(truth.company.year + 1, 1, 20)
    of = _suppliers_of_the_seven(truth)
    us = printed_name(truth.company.name)

    def head(supplier: Supplier, number: str) -> list[tuple[float, float, str]]:
        return [(50, 60, printed_name(supplier.name)), (50, 76, "2210 Meridian Road NE, Calgary, AB T2A 2N6"),
                (400, 76, f"Invoice No: {number}"), (400, 92, f"Invoice Date: {on.isoformat()}"),
                (400, 108, "Terms: Net 30"), (50, 124, f"Bill to: {us}, 410 Elm Street, Edmonton, AB T5J 0N3"),
                (50, 420, "GST/HST No. 814275533 RT0001")]  # fmt: skip

    def table(*rows: tuple[str, str]) -> list[tuple[float, float, str]]:
        return [(50, 200, "Description"), (500, 200, "Amount")] + [
            cell for n, (text, amount) in enumerate(rows) for cell in ((50, 218 + 16 * n, text),
                                                                      (500, 218 + 16 * n, amount))]  # fmt: skip

    def totals(*rows: tuple[str, str]) -> list[tuple[float, float, str]]:
        return [cell for n, (label, amount) in enumerate(rows) for cell in ((330, 300 + 16 * n, label),
                                                                           (500, 300 + 16 * n, amount))]  # fmt: skip

    plain, one = of["credit_note"], of["one_line"]
    fees = [_words(truth.account(plain.accounts[0]), n, 40600 + n, on) for n in range(3)]
    title = (400, 58, "INVOICE")
    return {
        "exempt_line": _page(
            *head(plain, "KC-5524"),
            *table((fees[0], "1,200.00"), (fees[1], "300.00"),
                   ("Disbursement - government filing fee (exempt)", "250.00")),
            *totals(("Subtotal", "1,750.00"), ("GST 5% on 1,500.00", "75.00"), ("Total", "1,825.00")), title=title),
        "two_provinces": _page(
            *head(plain, "KC-5530"),
            *table((f"{fees[0]} - Calgary office", "1,000.00"), (f"{fees[1]} - Calgary office", "200.00"),
                   (f"{fees[2]} - Toronto office", "500.00")),
            *totals(("Subtotal", "1,700.00"), ("GST 5% on 1,200.00", "60.00"), ("HST 13% on 500.00", "65.00"),
                    ("Total", "1,825.00")), title=title),
        "tax_column": _page(
            *head(one, "QC-77001"), (50, 200, "Description"), (400, 200, "Tax"), (500, 200, "Amount"),
            (50, 218, "Courier service downtown"), (400, 218, "5.00"), (500, 218, "100.00"),
            (50, 234, "Courier service airport"), (400, 234, "10.00"), (500, 234, "200.00"),
            *totals(("Subtotal", "300.00"), ("GST 5%", "15.00"), ("Total", "315.00")), title=title),
        "hash_column": _page(
            *head(one, "QC-77002"), (50, 200, "#"), (90, 200, "Description"), (350, 200, "Qty"),
            (420, 200, "Unit Price"), (500, 200, "Amount"), (50, 218, "1"), (90, 218, "Courier service downtown"),
            (350, 218, "1"), (420, 218, "100.00"), (500, 218, "100.00"),
            *totals(("Subtotal", "100.00"), ("GST 5%", "5.00"), ("Total", "105.00")), title=title),
    }  # fmt: skip


# --- documents that are not invoices -------------------------------------------------------------

OTHER_DOCUMENTS = ("statement", "remittance_advice", "staff_notice", "quote", "packing_slip", "order_confirmation")


def _page(*rows: tuple[float, float, str], title: tuple[float, float, str] | None = None) -> bytes:
    """A letter page with each text at (x, y) points from the top left, the ``title`` larger."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    if title:
        page.insert_text(title[:2], title[2], fontsize=16)
    for x, y, text in rows:
        page.insert_text((x, y), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def other_documents(truth: Truth, on: dt.date | None = None) -> dict[str, bytes]:
    """What arrives in an invoices folder and is not an invoice, as PDFs by the names of ``OTHER_DOCUMENTS``: a
    supplier's statement of account (four invoices with their balances), a remittance advice, a notice to staff, a
    quote, a packing slip and an order confirmation. The supplier is the books' first in use."""
    on = on or dt.date(truth.company.year + 1, 1, 20)
    supplier = next(s for s in truth.suppliers if _in_use(s) and s.currency == "CAD")
    vendor, us = printed_name(supplier.name), printed_name(truth.company.name)
    head = [(50, 60, vendor), (50, 76, "2210 Meridian Road NE, Calgary, AB T2A 2N6")]
    to = [(50, 130, f"To: {us}"), (50, 146, "Accounts Payable")]
    owed = [(f"{71200 + 7 * n}", on - dt.timedelta(days=20 * n + 12), Decimal(412 + 368 * n) + Decimal("0.35"))
            for n in range(4)]  # fmt: skip
    table = [row for n, (number, date, amount) in enumerate(owed) for row in (
        (50, 216 + 16 * n, number), (170, 216 + 16 * n, date.isoformat()), (300, 216 + 16 * n, f"{amount:,.2f}"),
        (450, 216 + 16 * n, f"{amount:,.2f}"))]  # fmt: skip
    total = f"{sum(amount for _, _, amount in owed):,.2f}"
    parts = [row for n, text in enumerate(("Filter cartridge 10 in.", "Gasket kit, nitrile", "Hose clamp 2 in."))
             for row in ((50, 200 + 16 * n, f"{2 + n}"), (120, 200 + 16 * n, text))]  # fmt: skip
    prices = [(450, 200 + 16 * n, f"{48 + 31 * n}.50") for n in range(3)]
    return {
        "statement": _page(
            *head, *to, (400, 92, f"Statement date: {on.isoformat()}"), (400, 108, f"Account: {supplier.number}"),
            (50, 196, "Invoice"), (170, 196, "Date"), (300, 196, "Amount"), (450, 196, "Balance"), *table,
            (300, 296, "Total outstanding"), (450, 296, total), title=(380, 62, "STATEMENT OF ACCOUNT")),
        "remittance_advice": _page(
            (50, 60, us), (50, 76, "Accounts Payable"), (50, 130, f"Paid to: {vendor}"),
            (400, 92, f"Payment date: {on.isoformat()}"), (400, 108, "Payment no: EFT-004417"),
            (50, 196, "Invoice number"), (170, 196, "Invoice date"), (300, 196, "Discount"), (450, 196, "Amount paid"),
            *[(x, y, "0.00" if x == 300 else text) for x, y, text in table], (300, 296, "Total paid"),
            (450, 296, total), title=(400, 62, "REMITTANCE ADVICE")),
        "staff_notice": _page(
            (50, 60, f"{us} - Staff notice"), (50, 110, "The head office will be closed on Monday for the holiday."),
            (50, 126, "Timesheets are due on Friday at noon. Parking passes are renewed at the front desk."),
            (50, 158, "Thank you,"), (50, 174, "Office Services")),
        "quote": _page(
            *head, *to, (400, 92, "Quote no: Q-20931"), (400, 108, f"Valid until: {on + dt.timedelta(days=30)}"),
            (50, 180, "Qty"), (120, 180, "Description"), (450, 180, "Price"), *parts, *prices,
            (380, 270, "Total quoted"), (450, 270, "238.50"), title=(400, 62, "QUOTATION")),
        "packing_slip": _page(
            *head, (50, 130, f"Ship to: {us}"), (400, 92, "Order no: SO-118204"),
            (400, 108, f"Shipped: {on.isoformat()}"), (50, 180, "Qty shipped"), (120, 180, "Description"), *parts,
            (50, 270, "Received by: ____________________"), title=(400, 62, "PACKING SLIP")),
        "order_confirmation": _page(
            *head, *to, (400, 92, "Order no: SO-118204"), (400, 108, f"Order date: {on.isoformat()}"),
            (50, 180, "Qty"), (120, 180, "Description"), (450, 180, "Price"), *parts, *prices,
            (380, 270, "Order total"), (450, 270, "238.50"), (50, 310, "An invoice will follow when the order ships."),
            title=(380, 62, "ORDER CONFIRMATION")),
    }  # fmt: skip
