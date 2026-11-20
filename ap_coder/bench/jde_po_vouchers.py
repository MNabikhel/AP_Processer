"""Made-up PO-matched invoices for the pilot tester's voucher match test (D-265): one for each Canadian tax case and
each kind of match, from the made-up company's own suppliers (``jde_company``), with the open PO lines each bills as
JD Edwards exports them (``jde_exports.open_po_lines_xlsx``; the PO with no quantities as Work With Order Details'
grid comes, ``order_details_xlsx(short=True)``) and the Voucher Match rows the keying sheet should give it.

The cases (``NAMES``): the GST alone (Alberta), Ontario's HST, the GST and BC's PST, Saskatchewan's PST, Manitoba's
RST, Quebec's QST, the PST not charged (self-assessed), zero-rated and taxed lines mixed, the freight on a PO line of
its own (Nova Scotia's HST), freight and a fuel surcharge the PO has no line for, a PO line with no quantity (a
service), a PO line billed in part (left open), and a credit note against a PO.

The rows are expected under two setups (``SETUPS``): the pilot's (``pilot_settings``: the prototype's, D-210, every
province on code V, the GST-only area ``CDNGST`` for the provinces whose federal tax is the GST, the PST and QST kept
in the expense, so each goes on a G/L line of its own on the PO line's account), and a company with a tax area for
each province (``area_settings``: BC, SK and MB on code C with their PST, Quebec's QST recovered on code V). Both
key a charge not on the PO on a G/L line of its own on ``FREIGHT_IN``.

* ``cases(truth)``: the cases, each with ``expected(setup)`` (its rows) and ``tax(setup)`` (what JD Edwards works out
  on them); ``coding()`` is the invoice as AP Coder should read it.
* ``write(folder, truth)``: each invoice as ``<name>.pdf``, the open PO lines (``open_po_lines.xlsx``,
  ``order_details.xlsx``), ``expected.json`` (each case's rows under each setup) and ``README.txt``: to load into the
  made-up company's data folder (``python -m ap_coder demo --company``) and try.

Beside them, a subcontractor's progress billings on the made-up company's subcontracts (order type OS, each line
holding back 10%, D-273: ``PROGRESS_NAMES``, ``progress_cases``): a first G702-style application for payment, a later
progress invoice (Ontario's HST, work billed before), a billing holding back 5% where its subcontract says 10%, an
application charging the GST on the work less the holdback, and a holdback release; their subcontract lines as report
6 of the build sheet gives them, and the rows with their Retained Amounts each setup should give
(``write_progress``).

The same books give the same files."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from ..capture.reader.progress import PROGRESS_LINE
from ..core.chart import skip_word, split_account
from ..erp.jde_prototype import prototype_settings
from ..erp.jde_settings import AMOUNT_TAX, CHARGES_GL, JdeSettings, default_tax_map
from .jde_company import Supplier, Truth, build, money
from .jde_exports import OpenPoLine, open_po_lines_xlsx, order_details_xlsx, po_lines_report_xlsx
from .jde_invoices import _page, printed_name

NAMES = ("ab_gst", "on_hst", "bc_gst_pst", "sk_gst_pst", "mb_gst_rst", "qc_gst_qst", "bc_self_assessed",
         "bc_zero_rated_mixed", "ns_freight_on_po", "ab_freight_not_on_po", "ab_no_quantity", "ab_partial",
         "ab_credit_note")  # fmt: skip
PILOT, AREAS = "pilot", "areas"
SETUPS = (PILOT, AREAS)
FREIGHT_IN = "0021061010.7420.4207"  # Courier - Domestic: the made-up company's freight in
OPEN_PO_FILE, ORDER_DETAILS_FILE, EXPECTED_FILE = "open_po_lines.xlsx", "order_details.xlsx", "expected.json"
RATES = {("GST", ""): Decimal("0.05"), ("HST", "ON"): Decimal("0.13"), ("HST", "NS"): Decimal("0.14"),
         ("PST", "BC"): Decimal("0.07"), ("PST", "SK"): Decimal("0.06"), ("PST", "MB"): Decimal("0.07"),
         ("QST", "QC"): Decimal("0.09975")}  # fmt: skip
# The company's sites, where the goods are shipped: the province of supply.
SITES = {
    "AB": "410 Elm Street, Edmonton, AB T5J 0N3", "ON": "88 Harbour Street, Toronto, ON M5J 2N1",
    "BC": "1055 Pender Street W, Vancouver, BC V6E 2S9", "SK": "1900 Albert Street, Regina, SK S4P 4K8",
    "MB": "201 Portage Avenue, Winnipeg, MB R3B 3K6", "QC": "1000 rue Sherbrooke O, Montréal, QC H3A 3G4",
    "NS": "1801 Hollis Street, Halifax, NS B3J 3N4",
}  # fmt: skip
# Where each supplier prints itself: the province it supplies from.
FROM = {
    "AB": "2210 Meridian Road NE, Calgary, AB T2A 2N6", "ON": "75 Courtneypark Drive, Mississauga, ON L5T 2Y3",
    "BC": "4400 Still Creek Drive, Burnaby, BC V5C 6C6", "SK": "1150 8th Avenue, Regina, SK S4R 1C9",
    "MB": "1460 Clarence Avenue, Winnipeg, MB R3T 1T6", "QC": "2100 boul. Industriel, Laval, QC H7S 1P4",
}  # fmt: skip
TAX_NAMES = {"MB": "RST"}  # the PST as Manitoba prints it
ORDER_DATE = dt.date(2026, 10, 1)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Item:
    """A printed line: what it bills, the taxes charged on it and the PO line it bills ("": none, a charge the PO
    has no line for)."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    taxes: tuple[str, ...]
    po_line: str  # "1.000"
    status: str = ""  # "zero_rated": printed so, nothing charged on it

    @property
    def amount(self) -> Decimal:
        return money(self.quantity * self.unit_price)


@dataclass(frozen=True)
class Setup:
    """How a case's taxes are keyed under one setup: the voucher's tax explanation code and area, and the taxes JD
    Edwards does not work out (kept in the expense: on a G/L line of their own)."""

    code: str
    area: str
    apart: tuple[str, ...] = ()


@dataclass(frozen=True)
class Row:
    """A voucher match row as the keying sheet should give it."""

    order_number: str
    order_type: str
    order_company: str
    line: str
    quantity: float | None
    uom: str
    amount: float
    account: str
    taxed: str
    tax_code: str
    tax_area: str
    on_po: bool = True


@dataclass(frozen=True)
class PoCase:
    name: str
    what: str  # what it shows, in a few words
    supplier: Supplier
    supplier_from: str  # its province
    province: str  # of supply: the company's site it is shipped to
    number: str  # the invoice number
    date: dt.date
    po: int
    items: tuple[Item, ...]
    po_lines: tuple[OpenPoLine, ...]
    setups: dict[str, Setup]
    short_grid: bool = False  # its PO comes from Work With Order Details as the grid comes: no quantity, no UM

    @property
    def credit(self) -> bool:
        return self.subtotal < 0

    @property
    def subtotal(self) -> Decimal:
        return sum((item.amount for item in self.items), Decimal(0))

    def taxes(self) -> list[tuple[str, Decimal, Decimal, Decimal]]:
        """The taxes printed: (tax, rate, what it is charged on, the tax), each on the lines that carry it."""
        out = []
        for tax in ("GST", "HST", "QST", "PST"):
            base = sum((item.amount for item in self.items if tax in item.taxes), Decimal(0))
            if base:
                rate = RATES[(tax, "" if tax == "GST" else self.province)]
                out.append((tax, rate, base, (base * rate).quantize(CENT, ROUND_HALF_UP)))
        return out

    @property
    def total(self) -> Decimal:
        return self.subtotal + sum((t[3] for t in self.taxes()), Decimal(0))

    def tax(self, setup: str) -> float:
        """What JD Edwards works out on the rows taxed Y: every tax charged but those kept apart."""
        return float(sum((t[3] for t in self.taxes() if t[0] not in self.setups[setup].apart), Decimal(0)))

    def expected(self, setup: str) -> list[Row]:
        """The voucher match rows under ``setup``: each line on its PO line (a charge not on the PO on a G/L line of
        its own on ``FREIGHT_IN``), Tax Y when it is charged the taxes of the voucher's tax area, else N with the
        exempt code; then the taxes kept apart on a G/L line on the PO line's account."""
        how = self.setups[setup]
        by_line = {f"{pl.line:.3f}": pl for pl in self.po_lines}
        out = []
        for item in self.items:
            taxed = any(t not in how.apart for t in item.taxes)
            code = how.code if taxed else "E"
            pl = by_line.get(item.po_line)
            if pl is None:
                out.append(Row("", "", "", "", None, "", float(item.amount), FREIGHT_IN, "Y" if taxed else "N", code,
                               how.area, on_po=False))  # fmt: skip
                continue
            quantity = float(item.quantity) if item.quantity else (-1.0 if item.amount < 0 else 1.0)
            out.append(Row(str(pl.order), pl.order_type, pl.company, item.po_line, quantity,
                           "" if self.short_grid else pl.uom, float(item.amount), pl.account, "Y" if taxed else "N",
                           code, how.area))  # fmt: skip
        kept = sum((t[3] for t in self.taxes() if t[0] in how.apart), Decimal(0))
        if kept:
            out.append(Row("", "", "", "", None, "", float(kept), self.po_lines[0].account, "N", "E", "", on_po=False))
        return out

    def coding(self) -> dict[str, Any]:
        """The invoice as AP Coder should read it (the fields of ``core.schema.InvoiceCoding``), each line coded to
        its PO line's account (a charge not on the PO to ``FREIGHT_IN``)."""
        accounts = {f"{pl.line:.3f}": pl.account for pl in self.po_lines}
        lines = []
        for n, item in enumerate(self.items, 1):
            unit, code = split_account(accounts.get(item.po_line, FREIGHT_IN))
            lines.append({"line_number": n, "description": item.description, "quantity": float(item.quantity),
                          "unit_price": float(item.unit_price), "amount": float(item.amount),
                          "predicted_gl_code": code, "predicted_cost_center": unit,
                          "taxes_applied": list(item.taxes), "tax_exempt": [], "tax_status": item.status})  # fmt: skip
        taxes = [{"tax_type": tax, "province": "" if tax == "GST" else self.province, "rate": float(rate),
                  "taxable_amount": float(base), "tax_amount": float(amount)}
                 for tax, rate, base, amount in self.taxes()]  # fmt: skip
        return {
            "vendor_name": printed_name(self.supplier.name), "invoice_number": self.number,
            "invoice_date": self.date.isoformat(), "currency": "CAD", "po_number": str(self.po),
            "supplier_province": self.supplier_from, "ship_to_province": self.province,
            "subtotal": float(self.subtotal), "tax_lines": taxes, "tax_total": float(sum(t[3] for t in self.taxes())),
            "grand_total": float(self.total), "confidence_score": 1.0,
            "payment_terms": "" if self.credit else "Net 30", "line_items": lines,
        }  # fmt: skip


def pilot_settings(base: JdeSettings | None = None) -> JdeSettings:
    """The pilot tester's JD Edwards settings for the test: the prototype's (``jde_prototype.prototype_settings``),
    PO-matched vouchers on, a charge not on the PO keyed on ``FREIGHT_IN``."""
    out = prototype_settings(base)
    out.po_matched, out.po_charges, out.po_charge_account = True, CHARGES_GL, FREIGHT_IN
    return out


def area_settings(base: JdeSettings | None = None) -> JdeSettings:
    """A company with a tax area for each province (``jde_settings.default_tax_map``: BC, SK and MB on code C, the
    others on V), tax amounts on the pay item, PO-matched vouchers on, a charge not on the PO on ``FREIGHT_IN``. The
    QST is recovered (GL accounts & tax's default)."""
    out = JdeSettings.from_json(base.to_json()) if base is not None else JdeSettings()
    out.amount_mode, out.tax_map = AMOUNT_TAX, default_tax_map()
    out.po_matched, out.po_charges, out.po_charge_account = True, CHARGES_GL, FREIGHT_IN
    return out


def _supplier(truth: Truth, name: str) -> Supplier:
    """The first supplier whose name starts with ``name`` that is paid: a trade supplier, its payments not held and its
    name not marked retired ("DNU", "DO NOT USE", a star), so its invoices are approved and keyed (B-2947)."""
    return next(s for s in truth.suppliers if s.name.startswith(name) and s.kind == "trade" and s.hold_payment == "N"
                and not skip_word(s.name) and s.name.strip() == s.name.strip("* "))  # fmt: skip


def cases(truth: Truth) -> list[PoCase]:
    """The cases of ``NAMES``, in that order."""
    company = truth.company.number
    office, safety, kitchen = "0021061010.7410.4107", "0021061010.7410.4141", "0021061010.7410.4113"
    janitorial, repairs, equipment = "0021061010.7340.3307", "0021061022.7330.3207", "0021061010.7540.5421"
    for account in (office, safety, kitchen, janitorial, repairs, equipment, FREIGHT_IN):
        truth.account(account)  # (KeyError: the books have no such account)
    prairie, alder, elkhorn = (_supplier(truth, n) for n in ("PRAIRIE SKY", "ALDERBROOK", "ELKHORN"))
    westmeadow, oak, silvertip = (_supplier(truth, n) for n in ("WESTMEADOW", "OAK & EMBER JANITORIAL", "SILVERTIP"))

    def po(order: int, supplier: Supplier, *lines: tuple[str, str, str, str, str, str], vouchered: str = "0"):
        """The open lines of PO ``order``: each (line, description, quantity, unit cost, unit, account)."""
        return tuple(OpenPoLine(order, Decimal(line), supplier.number, words, Decimal(quantity), Decimal(cost), unit,
                                account, company, vouchered=Decimal(vouchered), supplier_name=supplier.name,
                                order_date=ORDER_DATE)
                     for line, words, quantity, cost, unit, account in lines)  # fmt: skip

    def item(words: str, quantity: str, price: str, taxes: tuple[str, ...], line: str, status: str = "") -> Item:
        return Item(words, Decimal(quantity), Decimal(price), taxes, line, status)

    gst, hst, both, qst = ("GST",), ("HST",), ("GST", "PST"), ("GST", "QST")
    v_gst = Setup("V", "CDNGST")
    pst_apart = Setup("V", "CDNGST", ("PST",))
    day = dt.date(2026, 10, 20)
    made = [
        PoCase("ab_gst", "the GST alone (Alberta)", prairie, "AB", "AB", "PS-50211", day, 41101,
               (item("Copy paper, 8.5 x 11, case of 10", "12", "52.40", gst, "1.000"),
                item("Ballpoint pens, blue, box of 12", "20", "7.85", gst, "2.000")),
               po(41101, prairie, ("1", "COPY PAPER 8.5X11 CASE", "12", "52.40", "CS", office),
                  ("2", "PENS BALLPOINT BLUE BX12", "20", "7.85", "BX", office)),
               {PILOT: v_gst, AREAS: Setup("V", "AB-GST")}),
        PoCase("on_hst", "Ontario's HST", alder, "ON", "ON", "AT-7745", day, 41102,
               (item("Toner cartridge TN-760, black", "6", "89.00", hst, "1.000"),
                item("Drum unit DR-730", "2", "112.50", hst, "2.000")),
               po(41102, alder, ("1", "TONER TN-760 BLACK", "6", "89.00", "EA", office),
                  ("2", "DRUM UNIT DR-730", "2", "112.50", "EA", office)),
               {PILOT: Setup("V", "ONHST"), AREAS: Setup("V", "ON-HST")}),
        PoCase("bc_gst_pst", "the GST and BC's PST", elkhorn, "BC", "BC", "EWC-30918", day, 41103,
               (item("Hard hat, type 1, white", "20", "24.00", both, "1.000"),
                item("Hi-vis safety vest, class 2", "30", "14.50", both, "2.000")),
               po(41103, elkhorn, ("1", "HARD HAT TYPE 1 WHITE", "20", "24.00", "EA", safety),
                  ("2", "HI-VIS VEST CLASS 2", "30", "14.50", "EA", safety)),
               {PILOT: pst_apart, AREAS: Setup("C", "BC-GSTPST")}),
        PoCase("sk_gst_pst", "the GST and Saskatchewan's PST", westmeadow, "SK", "SK", "WBS-1187", day, 41104,
               (item("Pallet wrap, 18 in x 1500 ft", "12", "32.00", both, "1.000"),),
               po(41104, westmeadow, ("1", "PALLET WRAP 18IN", "12", "32.00", "RL", repairs)),
               {PILOT: pst_apart, AREAS: Setup("C", "SK-GSTPST")}),
        PoCase("mb_gst_rst", "the GST and Manitoba's RST", oak, "MB", "MB", "OEJ-6602", day, 41105,
               (item("Shop towels, case of 12 rolls", "8", "46.25", both, "1.000"),),
               po(41105, oak, ("1", "SHOP TOWELS CASE", "8", "46.25", "CS", janitorial)),
               {PILOT: pst_apart, AREAS: Setup("C", "MB-GSTPST")}),
        PoCase("qc_gst_qst", "the GST and Quebec's QST", silvertip, "QC", "QC", "SS-20488", day, 41106,
               (item("Licence renewal, inventory module, 12 months", "5", "114.00", qst, "1.000"),
                item("Support plan, 12 months", "1", "98.00", qst, "2.000")),
               po(41106, silvertip, ("1", "LICENCE RENEWAL INVENTORY", "5", "114.00", "EA", equipment),
                  ("2", "SUPPORT PLAN 12 MONTHS", "1", "98.00", "EA", equipment)),
               {PILOT: Setup("V", "CDNGST", ("QST",)), AREAS: Setup("V", "QC-GSTQST")}),
        PoCase("bc_self_assessed", "the PST not charged: self-assessed (an Alberta supplier ships to BC)", alder,
               "AB", "BC", "AT-7790", day, 41107,
               (item("Conveyor belt, 10 m, rubber", "1", "1450.00", gst, "1.000"),),
               po(41107, alder, ("1", "CONVEYOR BELT 10M RUBBER", "1", "1450.00", "EA", equipment)),
               {PILOT: Setup("B", "CDNGST"), AREAS: Setup("B", "BC-GSTPST")}),
        PoCase("bc_zero_rated_mixed", "a zero-rated line beside taxed ones (BC)", elkhorn, "BC", "BC", "EWC-30940",
               day, 41108,
               (item("Coffee, dark roast, 2 lb bag (zero-rated)", "6", "18.00", (), "1.000", "zero_rated"),
                item("Paper cups, 12 oz, sleeve of 50", "10", "6.50", both, "2.000"),
                item("Water cooler rental, October", "1", "30.00", both, "3.000")),
               po(41108, elkhorn, ("1", "COFFEE DARK ROAST 2LB", "6", "18.00", "EA", kitchen),
                  ("2", "PAPER CUPS 12OZ SLEEVE", "10", "6.50", "EA", kitchen),
                  ("3", "WATER COOLER RENTAL", "1", "30.00", "EA", kitchen)),
               {PILOT: pst_apart, AREAS: Setup("C", "BC-GSTPST")}),
        PoCase("ns_freight_on_po", "the freight on a PO line of its own (Nova Scotia's HST)", prairie, "AB", "NS",
               "PS-50260", day, 41109,
               (item("Fire extinguisher, 10 lb ABC", "6", "79.00", hst, "1.000"),
                item("Freight", "1", "45.00", hst, "2.000")),
               po(41109, prairie, ("1", "FIRE EXTINGUISHER 10LB ABC", "6", "79.00", "EA", safety),
                  ("2", "FREIGHT", "1", "45.00", "EA", safety)),
               {PILOT: Setup("V", "NSHST"), AREAS: Setup("V", "NS-HST")}),
        PoCase("ab_freight_not_on_po", "freight and a fuel surcharge the PO has no line for", alder, "AB", "AB",
               "AT-7802", day, 41110,
               (item("USB-C docking station, dual monitor", "5", "189.00", gst, "1.000"),
                item("Freight", "1", "38.00", gst, ""),
                item("Fuel surcharge", "1", "6.84", gst, "")),
               po(41110, alder, ("1", "USB-C DOCKING STATION DUAL", "5", "189.00", "EA", office)),
               {PILOT: v_gst, AREAS: Setup("V", "AB-GST")}),
        PoCase("ab_no_quantity", "a PO line with no quantity (a service; the order details grid as it comes)",
               westmeadow, "AB", "AB", "WBS-1203", day, 41111,
               (item("Building maintenance, October", "1", "1280.00", gst, "1.000"),),
               po(41111, westmeadow, ("1", "BUILDING MAINTENANCE OCT", "1", "1280.00", "EA", repairs)),
               {PILOT: v_gst, AREAS: Setup("V", "AB-GST")}, short_grid=True),
        PoCase("ab_partial", "a PO line billed in part: 60 of 100, left open", oak, "AB", "AB", "OEJ-6640", day,
               41112,
               (item("Paper towel, multifold, case", "60", "38.50", gst, "1.000"),),
               po(41112, oak, ("1", "PAPER TOWEL MULTIFOLD CASE", "100", "38.50", "CS", janitorial)),
               {PILOT: v_gst, AREAS: Setup("V", "AB-GST")}),
        PoCase("ab_credit_note", "a credit note against a PO (4 cases returned)", prairie, "AB", "AB", "CN-50277",
               day, 41113,
               (item("Return - copy paper, 8.5 x 11, case of 10", "-4", "52.40", gst, "1.000"),),
               po(41113, prairie, ("1", "COPY PAPER 8.5X11 CASE", "10", "52.40", "CS", office), vouchered="10"),
               {PILOT: v_gst, AREAS: Setup("V", "AB-GST")}),
    ]  # fmt: skip
    assert tuple(c.name for c in made) == NAMES
    return made


def _amount(value: Decimal) -> str:
    return f"{value:,.2f}"


def _rate(rate: Decimal) -> str:
    return f"{rate * 100:.3f}".rstrip("0").rstrip(".") + "%"


def pdf(case: PoCase, truth: Truth, holdback: Decimal | None = None) -> bytes:
    """The invoice set out plainly, as ``jde_invoices.printed_cases``: the supplier, the numbers and the PO, who it
    is billed and shipped to, the lines (description, quantity, unit price, amount) and the totals, each tax with
    what it is charged on when some lines carry none. ``holdback``: a percent of the subtotal taken off under the
    total, then the balance due (B-2700)."""
    us = printed_name(truth.company.name)
    rows = [(50, 60, printed_name(case.supplier.name)), (50, 76, FROM[case.supplier_from]),
            (400, 76, f"{'Credit Note' if case.credit else 'Invoice'} No: {case.number}"),
            (400, 92, f"Date: {case.date.isoformat()}"), (400, 108, f"PO Number: {case.po}"),
            (50, 124, f"Bill to: {us}, {SITES['AB']}"), (50, 140, f"Ship to: {us}, {SITES[case.province]}"),
            (50, 200, "Description"), (330, 200, "Qty"), (400, 200, "Unit Price"), (500, 200, "Amount")]  # fmt: skip
    if not case.credit:
        rows.append((400, 124, "Terms: Net 30"))
    for n, item in enumerate(case.items):
        y = 218 + 16 * n
        rows += [(50, y, item.description), (330, y, f"{item.quantity:g}"), (400, y, _amount(item.unit_price)),
                 (500, y, _amount(item.amount))]  # fmt: skip
    y = 218 + 16 * len(case.items) + 40
    totals = [("Subtotal", case.subtotal)]
    for tax, rate, base, amount in case.taxes():
        label = f"{TAX_NAMES.get(case.province, 'PST') if tax == 'PST' else tax} {_rate(rate)}"
        totals.append((label if base == case.subtotal else f"{label} on {_amount(base)}", amount))
    totals.append(("Total", case.total))
    if holdback:
        held = money(case.subtotal * holdback / 100)
        totals += [(f"Less {holdback:g}% holdback", -held), ("Balance due", case.total - held)]
    for n, (label, value) in enumerate(totals):
        rows += [(330, y + 16 * n, label), (500, y + 16 * n, f"({_amount(-value)})" if value < 0
                                                             else _amount(value))]  # fmt: skip
    rows.append((50, y + 16 * len(totals) + 40, "GST/HST No. 814275533 RT0001"))
    return _page(*rows, title=(400, 58, "CREDIT NOTE" if case.credit else "INVOICE"))


def po_files(made: list[PoCase]) -> dict[str, bytes]:
    """The open PO lines of ``made`` as JD Edwards exports them: Open Order Inquiry's grid, and the order details
    grid as it comes for the PO with no quantities."""
    lines = [pl for case in made if not case.short_grid for pl in case.po_lines]
    short = [pl for case in made if case.short_grid for pl in case.po_lines]
    out = {OPEN_PO_FILE: open_po_lines_xlsx(lines)}
    if short:
        out[ORDER_DETAILS_FILE] = order_details_xlsx(short, short=True)
    return out


def expected_json(made: list[PoCase]) -> dict[str, Any]:
    """{case: what it shows, its gross, and under each setup its rows and the tax on them}."""
    return {case.name: {"what": case.what, "invoice": f"{case.name}.pdf", "po": str(case.po),
                        "gross": float(case.total),
                        **{setup: {"tax": case.tax(setup), "rows": [vars(row) for row in case.expected(setup)]}
                           for setup in SETUPS}}
            for case in made}  # fmt: skip


README = """Made-up PO-matched invoices for trying JD Edwards' Voucher Match on the keying sheet (AP Coder, D-265).

All names, numbers and amounts are made up: the made-up company's suppliers and accounts.

To try them:
1. Make the made-up company's data folder: set AP_PRIVATE_DIR to an empty folder, then
   python -m ap_coder demo --company (or use one made already).
2. Settings -> JD Edwards E1: tick "PO-matched vouchers"; under "Charges not on the purchase order" pick
   "Add each as its own G/L line" with the account {freight_in}.
3. Purchase orders: load open_po_lines.xlsx, then order_details.xlsx.
4. Drop the PDFs in, approve them (each line on its PO line's account; on bc_zero_rated_mixed, confirm the coffee
   line's PST exemption, as the screen asks), and make the keying sheet (Exports, JD Edwards keying sheet).
5. Compare each voucher's Voucher Match rows with expected.json ("pilot": the prototype's tax areas; "areas": a tax
   area for each province, BC, SK and MB on code C).

The invoices:
{cases}

Subcontracts' progress billings (D-273): to try them, also load subcontract_lines.xlsx (report 6 of the build
sheet: the subcontracts' lines, order type OS, each with its Retainage %), and in Settings -> JD Edwards E1 tick
"Subcontract progress billings". Compare each voucher's rows and its Retained Amounts with progress_billings.json
("pilot_whole": the prototype's tax areas, Voucher Match working the tax out on the whole amount matched; "_net": on
it less the retained; no rows: held, with why, on the sheet "Not ready").
{progress}
"""


def write(folder: str | Path, truth: Truth) -> list[PoCase]:
    """The set in ``folder`` (made if missing): each invoice's PDF, the PO files, ``expected.json`` and
    ``README.txt`` (the progress billings beside them: ``write_progress``). Returns the cases."""
    made = cases(truth)
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    for case in made:
        (out / f"{case.name}.pdf").write_bytes(pdf(case, truth))
    for name, data in po_files(made).items():
        (out / name).write_bytes(data)
    (out / EXPECTED_FILE).write_text(json.dumps(expected_json(made), indent=1), encoding="utf-8")
    listed = "\n".join(f"- {case.name}.pdf: {case.what} (PO {case.po}, gross {case.total:,.2f})" for case in made)
    progress = "\n".join(f"- {case.name}.pdf: {case.what} (subcontract {case.po}, amount due {case.due:,.2f})"
                         for case in write_progress(out, truth))  # fmt: skip
    (out / "README.txt").write_text(README.format(freight_in=FREIGHT_IN, cases=listed, progress=progress),
                                    encoding="utf-8")  # fmt: skip
    return made


# --- subcontracts' progress billings (D-273) -----------------------------------------------------------------------

PROGRESS_NAMES = ("first_billing", "later_billing", "holdback_differs", "gst_on_net", "holdback_release")
SUBCONTRACT_FILE, PROGRESS_FILE = "subcontract_lines.xlsx", "progress_billings.json"
APPLICATION, PROGRESS_INVOICE = "application", "invoice"  # the two layouts: a G702-style application, an invoice
WHOLE, NET = "whole", "net"  # the tax on the work with the holdback in it, or on it less the holdback


@dataclass(frozen=True)
class Billed:
    """A progress billing's line: the work billed this period on a subcontract line ("1.000")."""

    description: str
    amount: Decimal
    po_line: str


@dataclass(frozen=True)
class ProgressRow:
    """A subcontract's voucher match row as the keying sheet should give it: its Retained Amount beside its amount
    matched, no quantity (a lump sum's, matched by its amount)."""

    order_number: str
    order_type: str
    order_company: str
    line: str
    quantity: float | None
    amount: float
    retained: float
    account: str
    taxed: str
    tax_code: str
    tax_area: str


@dataclass(frozen=True)
class ProgressCase:
    """A made-up progress billing of the subcontractor on one of the made-up company's subcontracts (order type OS),
    in one of the two layouts; ``holdback_pct``: the holdback it prints (its subcontract's ``retainage`` may differ);
    ``tax_on``: the base its tax is charged on; ``release``: it pays out a holdback held before."""

    name: str
    what: str
    supplier: Supplier
    province: str  # the site: the province of supply
    number: str
    application: int  # its billing's number
    date: dt.date
    po: int
    layout: str
    items: tuple[Billed, ...]
    po_lines: tuple[OpenPoLine, ...]
    holdback_pct: Decimal
    tax_on: str = WHOLE
    previous: Decimal | None = None  # the work billed before, as printed (None: not printed)
    release: bool = False

    @property
    def tax_type(self) -> str:
        return "HST" if self.province in ("ON", "NS") else "GST"

    @property
    def rate(self) -> Decimal:
        return RATES[(self.tax_type, "" if self.tax_type == "GST" else self.province)]

    @property
    def this_period(self) -> Decimal:
        return sum((item.amount for item in self.items), Decimal(0))

    @property
    def holdback(self) -> Decimal:
        return Decimal(0) if self.release else money(self.this_period * self.holdback_pct / 100)

    @property
    def tax_base(self) -> Decimal:
        return self.this_period - (self.holdback if self.tax_on == NET else Decimal(0))

    @property
    def tax(self) -> Decimal:
        return (self.tax_base * self.rate).quantize(CENT, ROUND_HALF_UP)

    @property
    def due(self) -> Decimal:
        return self.this_period - self.holdback + self.tax

    def held(self, retainage_tax: str) -> str:
        """Why the keying sheet holds it ("": it is keyed), Voucher Match working the tax out as ``retainage_tax``."""
        if self.release:
            return "a holdback release"
        if self.tax_on != retainage_tax:
            return "the invoice charges its tax on the work"
        return ""

    def expected(self, setup: str) -> list[ProgressRow]:
        """The voucher match rows under ``setup`` (``SETUPS``): each line on its subcontract line, its holdback the
        invoice's (shared as the lines' percents would share it), taxed under the province's code and area."""
        area = {PILOT: {"AB": "CDNGST", "ON": "ONHST"}, AREAS: {"AB": "AB-GST", "ON": "ON-HST"}}[setup][self.province]
        by_line = {f"{pl.line:.3f}": pl for pl in self.po_lines}
        due = [money(item.amount * (by_line[item.po_line].retainage or 0) / 100) for item in self.items]
        weights = due if sum(due) else [item.amount for item in self.items]
        shares = [money(self.holdback * w / sum(weights)) for w in weights]
        shares[max(range(len(shares)), key=lambda i: shares[i])] += self.holdback - sum(shares)
        return [ProgressRow(str(self.po), "OS", self.po_lines[0].company, item.po_line, None, float(item.amount),
                            float(share), by_line[item.po_line].account, "Y", "V", area)
                for item, share in zip(self.items, shares, strict=True)]  # fmt: skip

    def progress(self) -> dict[str, Any]:
        """What AP Coder should read of it as a progress billing (``capture.reader.progress``), kept beside its
        coding."""
        before = self.previous if self.layout == APPLICATION or self.previous is not None else None
        return {"this_period": float(self.this_period), "previous": None if before is None else float(before),
                "to_date": None if before is None else float(before + self.this_period),
                "holdback": float(self.holdback), "holdback_rate": None if self.release else float(self.holdback_pct),
                "taxes": {self.tax_type: float(self.tax)}, "amount_due": float(self.due), "release": self.release,
                "adds_up": True}  # fmt: skip

    def coding(self) -> dict[str, Any]:
        """The invoice as AP Coder should read it: the work this period its subtotal (a line of each, or the one line
        of an application), the tax as printed on its base, the total before the holdback."""
        accounts = {f"{pl.line:.3f}": pl.account for pl in self.po_lines}
        items = self.items if self.layout == PROGRESS_INVOICE else (
            Billed(PROGRESS_LINE, self.this_period, self.items[0].po_line),)  # fmt: skip
        lines = []
        for n, item in enumerate(items, 1):
            unit, code = split_account(accounts[item.po_line])
            lines.append({"line_number": n, "description": item.description, "quantity": 1.0,
                          "unit_price": float(item.amount), "amount": float(item.amount), "predicted_gl_code": code,
                          "predicted_cost_center": unit, "taxes_applied": [self.tax_type], "tax_exempt": [],
                          "tax_status": ""})  # fmt: skip
        return {
            "vendor_name": printed_name(self.supplier.name), "invoice_number": self.number,
            "invoice_date": self.date.isoformat(), "currency": "CAD", "po_number": str(self.po),
            "supplier_province": "AB", "ship_to_province": self.province, "subtotal": float(self.this_period),
            "tax_lines": [{"tax_type": self.tax_type, "province": "" if self.tax_type == "GST" else self.province,
                           "rate": float(self.rate), "taxable_amount": float(self.tax_base),
                           "tax_amount": float(self.tax)}],
            "tax_total": float(self.tax), "grand_total": float(self.this_period + self.tax), "confidence_score": 1.0,
            "payment_terms": "Net 30", "line_items": lines,
        }  # fmt: skip


def progress_cases(truth: Truth) -> list[ProgressCase]:
    """The cases of ``PROGRESS_NAMES``, in that order: the made-up subcontractor's billings on five subcontracts of
    the made-up company (each line holding back 10%), the last one done and its holdback released."""
    company = truth.company.number
    repairs, equipment = "0021061022.7330.3207", "0021061010.7540.5421"
    for account in (repairs, equipment):
        truth.account(account)
    builder = _supplier(truth, "WESTMEADOW")

    def contract(order: int, *lines: tuple[str, str, str, str]) -> tuple[OpenPoLine, ...]:
        """Subcontract ``order``'s lines: each (line, description, lump sum, vouchered before), 10% held back."""
        return tuple(OpenPoLine(order, Decimal(line), builder.number, words, Decimal(0), Decimal(0), "", repairs
                                if "ELECTRICAL" not in words else equipment, company, vouchered=Decimal(before),
                                order_type="OS", supplier_name=builder.name, order_date=ORDER_DATE,
                                lump_sum=Decimal(amount), retainage=Decimal(10), tax_area="")
                     for line, words, amount, before in lines)  # fmt: skip

    day = dt.date(2026, 10, 31)
    renovation = contract(41201, ("1", "OFFICE RENOVATION PHASE 1", "250000", "0"))
    mechanical = contract(41202, ("1", "MECHANICAL ROUGH-IN", "400000", "120000"))
    site = contract(41203, ("1", "SITE WORK", "100000", "0"), ("2", "CONCRETE FOUNDATIONS", "150000", "0"))
    electrical = contract(41204, ("1", "ELECTRICAL ROUGH-IN", "180000", "40000"))
    roofing = contract(41205, ("1", "ROOFING REPLACEMENT", "120000", "120000"))  # (done: all of it vouchered)
    ten, five = Decimal(10), Decimal(5)
    made = [
        ProgressCase("first_billing", "a first application for payment (G702-style), 10% held back", builder, "AB",
                     "WBS-P-0101", 1, day, 41201, APPLICATION,
                     (Billed("Office renovation, phase 1", Decimal(45000), "1.000"),), renovation, ten,
                     previous=Decimal(0)),
        ProgressCase("later_billing", "a third progress invoice (Ontario's HST), 120,000.00 billed before", builder,
                     "ON", "WBS-P-0102", 3, day, 41202, PROGRESS_INVOICE,
                     (Billed("Mechanical rough-in - work this period", Decimal(80000), "1.000"),), mechanical, ten,
                     previous=Decimal(120000)),
        ProgressCase("holdback_differs", "a billing of two lines holding back 5%, its subcontract 10%", builder, "AB",
                     "WBS-P-0103", 2, day, 41203, PROGRESS_INVOICE,
                     (Billed("Site work - work this period", Decimal(20000), "1.000"),
                      Billed("Concrete foundations - work this period", Decimal(30000), "2.000")), site, five),
        ProgressCase("gst_on_net", "an application charging the GST on the work less the holdback", builder, "AB",
                     "WBS-P-0104", 2, day, 41204, APPLICATION,
                     (Billed("Electrical rough-in", Decimal(60000), "1.000"),), electrical, ten, tax_on=NET,
                     previous=Decimal(40000)),
        ProgressCase("holdback_release", "a holdback release: the roofing's 10% paid out (held for a person)",
                     builder, "AB", "WBS-P-0105", 5, day, 41205, PROGRESS_INVOICE,
                     (Billed("Holdback release - roofing replacement", Decimal(12000), "1.000"),), roofing, ten,
                     release=True),
    ]  # fmt: skip
    assert tuple(c.name for c in made) == PROGRESS_NAMES
    return made


def _application_rows(case: ProgressCase, y: int) -> list[tuple[str, Decimal | None, str]]:
    """A G702-style application's numbered lines from ``y``: the contract, the work to date, before and this period,
    the holdback, the net, the tax and the payment due."""
    contract = sum((pl.ordered_amount for pl in case.po_lines), Decimal(0))
    before = case.previous or Decimal(0)
    rows = [("Original contract sum", contract), ("Net change by change orders", Decimal(0)),
            ("Contract sum to date", contract), ("Total completed to date", before + case.this_period),
            ("Less previous applications", before), ("Work completed this period", case.this_period),
            (f"Less holdback {case.holdback_pct:g}%", -case.holdback),
            ("Net amount this period", case.this_period - case.holdback),
            (f"{case.tax_type} {_rate(case.rate)} on {_amount(case.tax_base)}", case.tax),
            ("Current payment due", case.due)]  # fmt: skip
    return [(f"{n}. {label}", value, "") for n, (label, value) in enumerate(rows, 1)]


def progress_pdf(case: ProgressCase, truth: Truth) -> bytes:
    """The billing set out as its layout has it: an application's numbered summary, or an invoice's line table (what
    was billed to date and before above it) with the holdback taken off under its subtotal."""
    us = printed_name(truth.company.name)
    title = "APPLICATION FOR PAYMENT" if case.layout == APPLICATION else "INVOICE"
    rows = [(50, 60, printed_name(case.supplier.name)), (50, 76, FROM["AB"]),
            (400, 76, f"Invoice No: {case.number}"), (400, 92, f"Date: {case.date.isoformat()}"),
            (400, 108, f"PO Number: {case.po}"), (400, 124, "Terms: Net 30"),
            (50, 124, f"Bill to: {us}, {SITES['AB']}"), (50, 140, f"Ship to: {us}, {SITES[case.province]}"),
            (50, 170, f"{'Application for Payment' if case.layout == APPLICATION else 'Progress billing'} No. "
                      f"{case.application}, period to {case.date.isoformat()}")]  # fmt: skip
    if case.layout == APPLICATION:
        rows.append((50, 186, f"Project: {case.items[0].description}"))
        for n, (label, value, _) in enumerate(_application_rows(case, 220)):
            rows += [(50, 220 + 16 * n, label), (450, 220 + 16 * n, f"({_amount(-value)})" if value < 0
                                                                     else _amount(value))]  # fmt: skip
    else:
        y = 186
        if case.previous is not None:
            rows += [(50, 186, "Billed to date"), (250, 186, _amount(case.previous + case.this_period)),
                     (50, 202, "Previously billed"), (250, 202, _amount(case.previous))]  # fmt: skip
            y = 218
        rows += [(50, y + 24, "Description"), (500, y + 24, "Amount")]
        for n, item in enumerate(case.items):
            rows += [(50, y + 42 + 16 * n, item.description), (500, y + 42 + 16 * n, _amount(item.amount))]
        at = y + 42 + 16 * len(case.items) + 30
        totals = [("Subtotal", _amount(case.this_period))]
        if case.holdback:
            totals.append((f"Less holdback {case.holdback_pct:g}%", f"({_amount(case.holdback)})"))
        totals += [(f"{case.tax_type} {_rate(case.rate)} on {_amount(case.tax_base)}", _amount(case.tax)),
                   ("Amount due", _amount(case.due))]  # fmt: skip
        for n, (label, value) in enumerate(totals):
            rows += [(330, at + 16 * n, label), (500, at + 16 * n, value)]
    rows.append((50, 700, "GST/HST No. 814275533 RT0001"))
    return _page(*rows, title=(400, 58, title))


def subcontract_settings(base: JdeSettings | None = None, retainage_tax: str = WHOLE,
                         setup: str = PILOT) -> JdeSettings:  # fmt: skip
    """``setup``'s settings (``pilot_settings``, ``area_settings``) with subcontract progress billings on, Voucher
    Match working a retaining row's tax out on ``retainage_tax``."""
    out = (pilot_settings if setup == PILOT else area_settings)(base)
    out.po_subcontracts, out.retainage_tax = True, retainage_tax
    return out


def progress_json(made: list[ProgressCase]) -> dict[str, Any]:
    """{case: what it shows, its amounts, and under each setup its rows (none when held), by the tax setting}."""
    return {case.name: {"what": case.what, "invoice": f"{case.name}.pdf", "po": str(case.po),
                        "this_period": float(case.this_period), "holdback": float(case.holdback),
                        "tax": float(case.tax), "amount_due": float(case.due),
                        **{f"{setup}_{tax}": [] if case.held(tax) else [vars(r) for r in case.expected(setup)]
                           for setup in SETUPS for tax in (WHOLE, NET)}}
            for case in made}  # fmt: skip


def write_progress(folder: str | Path, truth: Truth) -> list[ProgressCase]:
    """The progress billings in ``folder`` (made if missing): each PDF, their subcontracts' lines as report 6 of the
    build sheet gives them (``jde_exports.po_lines_report_xlsx``) and ``PROGRESS_FILE``. Returns the cases."""
    made = progress_cases(truth)
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    for case in made:
        (out / f"{case.name}.pdf").write_bytes(progress_pdf(case, truth))
    lines = list({(pl.order, pl.line): pl for case in made for pl in case.po_lines}.values())
    (out / SUBCONTRACT_FILE).write_bytes(po_lines_report_xlsx(lines))
    (out / PROGRESS_FILE).write_text(json.dumps(progress_json(made), indent=1), encoding="utf-8")
    return made


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """``po-vouchers``' command line, under ``python -m ap_coder.bench``."""
    p = sub.add_parser("po-vouchers", help="made-up PO-matched invoices, their open PO lines and the voucher match "
                       "rows expected, to try JD Edwards' Voucher Match (D-265)")  # fmt: skip
    p.add_argument("--out", required=True, help="the folder to write the set into (made if missing)")
    p.add_argument("--seed", type=int, default=1, help="the made-up company's seed (the demo company's: 1)")
    p.set_defaults(run=main)
    return p


def main(a: argparse.Namespace) -> int:
    made = write(a.out, build(a.seed, "small", dt.date.today().year - 1))
    print(f"{len(made)} PO-matched invoices and {len(PROGRESS_NAMES)} subcontract progress billings, their open PO "
          f"lines, {EXPECTED_FILE} and {PROGRESS_FILE} written to {a.out}: see "
          f"{Path(a.out) / 'README.txt'}")  # fmt: skip
    return 0
