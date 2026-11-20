"""The JD Edwards keying sheet: what a person reads while typing vouchers into Standard Voucher Entry (D-210).

For the approved invoices of an export batch, one block per voucher, in the order the screen asks: the header
(supplier, company, invoice number, dates, currency), the pay item (gross amount, payment terms code, remark, due date,
tax explanation code, tax rate/area, tax and taxable amounts; the G/L bank account once bank accounts are entered,
D-220), then its G/L distribution (the account string as it is
typed, ``BU.Object.Subsidiary``; the account's description; the amount, net of the tax JD Edwards posts itself, a
tax left in the expense inside it; the remark), a check line (G/L lines + recovered tax = gross) and, in words, what
a person would otherwise work out: a tax that is not recovered, what the tax amount holds, the meals' half, a
foreign currency, a PO number too long. An invoice the JD Edwards checks hold back is not dropped: it is on the
second sheet, "Not ready", with why.

Every figure is the voucher's own (``jde_voucher._prepare``, which the batch files are made of too), so the sheet and
the Z-files of a batch say the same. Every code is a text cell (a business unit, a PO number or an object account keeps
its leading zeros), amounts are numbers with two decimals and dates are dates. It prints on upright pages, the column
names on each page; a voucher that fits a page is never cut, a longer one only between G/L lines. There is no CSV of it:
a spreadsheet opening one reads an account or a PO number as a number (B-2151).

A PO-matched voucher (Settings → JD Edwards E1, D-264) has no G/L distribution: it is keyed in Voucher Match (P0411),
a row for each invoice line in the screen's order (the order number, type and company, the PO line's own number, the
quantity and amount matched, the unit of measure, the PO line's account, the tax, the remark), each line paired with
its open PO line as the purchase orders loaded have it (``coding.po.voucher_match``), each row's tax and the lines of
its own (a charge not on the PO, a tax its area does not hold) as ``jde_match.keyed_match`` keys them (D-265); its
check line is the amounts matched + the tax JD Edwards works out on them = the gross. One with a line that matches no
PO line is not ready, with why: no PO line is guessed. A subcontract's progress billing (D-273) has each row's
Retained Amount beside its Amount Matched (``jde_retainage``), the retained and the amount due under the gross, and
its check line is the amounts matched - the retained + the tax = the amount due as printed.

Entry points: ``vouchers`` (the values), ``po_matches`` (the voucher matches of a batch's invoices), ``build_xlsx``
and ``build`` (a batch's file), ``keying_batches``.
"""

from __future__ import annotations

import datetime as dt
import io
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.pagebreak import Break

from ..coding import banks
from ..coding import po as po_mod
from ..coding.taxreturn import self_assessed_base
from ..core.safe import neutralise_sheet, xlsx_row
from . import jde_accounts, jde_formats, jde_match, jde_remarks, jde_review, jde_settings, jde_voucher, jde_zfiles
from .exports import _sheet, entry_lines

FORMAT = jde_settings.FORMATS[1]  # "jde_keying": the export format (``exports.FORMATS`` says what it is)
SHEET, NOT_READY = "Keying sheet", "Not ready"
# The G/L distribution's columns, named on the frozen first row and at the top of each printed page.
LINE_COLUMNS = ["Account number", "Account description", "Amount", "Explanation -Remark-"]
WIDTHS = [26, 36, 16, 36]
NOTE_WIDTH = 84  # characters of a note's row: the three columns beside its label
# A G/L line's subledger, its type and its units, after its four columns, when a line of the sheet has any (an account
# whose posting edit code is L or U, D-268).
LEDGER_COLUMNS = ["Subledger", "Sub Type", "Units"]
# Voucher Match's grid (P0411), in its order: a PO-matched voucher's rows (D-264), over the sheet's columns A to L
# (and M and N, the PO line's subledger and its type, for a voucher whose PO lines have one: D-268); the widths of the
# columns after the G/L distribution's four.
MATCH_COLUMNS = ["Order Number", "Or Ty", "Order Co", "Line Number", "Quantity Matched", "UM", "Amount Matched",
                 "Account Number", "Tax Y/N", "Tax Expl", "Tax Area", "Remark"]  # fmt: skip
MATCH_LEDGER = ["Subledger", "Sub Type"]
RETAINED = "Retained Amount"  # a subcontract's row's, after its Amount Matched (D-273)
MATCH_WIDTHS = [16, 6, 10, 26, 10, 9, 10, 32, 10, 8]
NOT_ON_PO = "Not on the PO"  # a voucher match's G/L line of its own, in its Order Number column (D-265)
STANDARD_TYPE = "PV"  # Standard Voucher Entry's own document type: any other is said (B-2621)
CREDIT_TERMS = "none (a credit)"  # a credit memo's Payment terms: none is typed, as the files send none (B-896)
# Rows of a printed page under the column names (upright Letter or A4 at full size; fewer than fit, to be safe): a
# voucher that would run over the page starts on the next one.
ROWS_A_PAGE = 42
MONEY_FORMAT, DATE_FORMAT, TEXT_FORMAT = "#,##0.00", "yyyy-mm-dd", "@"
LABEL, TEXT, MONEY, DATE, TITLE, PLAIN = "label", "text", "money", "date", "title", "plain"
NOT_READY_COLUMNS = [
    ("invoice_id", "AP Coder invoice"), ("supplier_number", "Supplier number"), ("supplier_name", "Supplier name"),
    ("invoice_number", "Invoice number"), ("invoice_date", "Invoice date"), ("gross", "Gross amount"),
    ("currency", "Currency"), ("why", "Why it is not ready"),
]  # fmt: skip


@dataclass
class Line:
    """One G/L distribution line as it is typed."""

    account: str  # BU.Object.Subsidiary, without padding
    description: str  # the account's
    amount: float  # net of the tax JD Edwards posts; a tax left in the expense is inside
    remark: str
    # what an account whose posting edit code is L or U requires (D-268): its subledger and type, its units
    subledger: str = ""
    subledger_type: str = ""
    units: float | None = None

    @property
    def ledger(self) -> bool:
        return bool(self.subledger or self.subledger_type or self.units)


@dataclass
class Voucher:
    """One voucher: its header and pay item fields, its G/L distribution (or, PO-matched, its voucher match rows) and
    what is said under it."""

    invoice_id: int
    supplier_number: str
    supplier_name: str
    company: str
    invoice_number: str
    invoice_date: dt.date | None
    gl_date: dt.date
    currency: str
    gross: float
    terms: str  # the JD Edwards terms code; "" when there is none (the printed terms are in a note, B-2618)
    remark: str
    due: dt.date | None  # None: JD Edwards works it out from the terms
    po_number: str
    tax_code: str
    tax_area: str
    tax: float | None  # None: no tax on the pay item
    taxable: float | None
    recovered: float  # the tax JD Edwards posts itself: not in the lines
    po_matched: bool = False  # no G/L lines: JD Edwards distributes it from the receipts
    bank: tuple[str, str] | None = None  # (the G/L bank account as typed, its name); None: no bank account entered
    doc_type: str = STANDARD_TYPE  # the document type (VLDCT): said on the sheet when it is not PV (B-2621)
    lines: list[Line] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # PO-matched: the voucher match rows (``jde_match.keyed_match``), no G/L lines; the tax JD Edwards works out on
    # them, on top of the amounts matched (D-265)
    matches: list[jde_match.MatchLine] = field(default_factory=list)
    match_tax: float = 0.0
    # A subcontract's progress billing (D-273): the rows' Retained Amounts, and the amount due as printed (None: none)
    retained: float = 0.0
    amount_due: float | None = None

    @property
    def lines_total(self) -> float:
        return round(sum(line.amount for line in self.lines) + sum(m.amount for m in self.matches), 2)

    @property
    def added(self) -> float:
        """What the check line adds to the lines: the tax JD Edwards recovers itself (G/L lines), or every tax it works
        out on the voucher match rows (D-265)."""
        return self.match_tax if self.matches else self.recovered

    @property
    def balanced(self) -> bool:
        due = self.amount_due is None or round(self.lines_total - self.retained + self.added - self.amount_due, 2) == 0
        return round(self.lines_total + self.added - self.gross, 2) == 0 and due


@dataclass
class Held:
    """An invoice the JD Edwards checks hold back, and why."""

    invoice_id: int
    supplier_number: str
    supplier_name: str
    invoice_number: str
    invoice_date: str
    gross: float
    currency: str
    why: list[str]


def keying_batches(store: Any) -> set[int]:
    """The export batches made as a keying sheet (not undone): an invoice on one is to type into JD Edwards by hand,
    so where its export is said it is "on keying sheet 12", not "exported"."""
    return {int(b["id"]) for b in store.export_batches() if b["format"] == FORMAT and not b["undone_at"]}


def vouchers(
    invoices: list[dict[str, Any]], settings: jde_settings.JdeSettings,
    vendor_lookup: jde_accounts.VendorLookup = None, *,
    gl_names: Mapping[str, str] | None = None, on: dt.date | None = None,
    left_out: Mapping[int, list[str]] | None = None, fx_rates: Mapping[str, float] | None = None,
    warned: Mapping[int, list[str]] | None = None, bank_accounts: Mapping[str, dict[str, Any]] | None = None,
    po_matches: Mapping[int, po_mod.VoucherMatch] | None = None,
) -> tuple[list[Voucher], list[Held]]:  # fmt: skip
    """(the vouchers to type, the invoices not ready) of approved ``invoices``, in their order. ``gl_names``: each GL
    code's description; ``on``: the batch's day (the G/L date, when it is the export day); ``left_out``: {invoice:
    why} from the batch's own checks (a duplicate, a voucher JD Edwards has through the API), beside what each
    voucher's own check says here; ``fx_rates``: AP Coder's exchange rates, said on a foreign voucher; ``warned``:
    {invoice: warnings} said first under its voucher (a tax ID that differs from the supplier list's, D-219);
    ``bank_accounts``: our bank accounts by code, once one is entered: each voucher's header then carries its bank
    account (D-220), and one in a currency none of them is in says so; ``po_matches``: {invoice: its voucher match}
    (``po_matches``), given: a PO-matched voucher is keyed with its match rows, or not ready when it has none."""
    ready: list[Voucher] = []
    held: list[Held] = []
    taken: dict[tuple[str, str, str, str], float] = {}  # the quantity matched on each PO line above on the sheet
    for inv, (an8, v) in zip(invoices, jde_voucher._vouchers(invoices, settings, vendor_lookup, on), strict=True):
        final = v.final
        why = list((left_out or {}).get(v.inv_id) or []) or (jde_voucher._accounts_once(v) if v.problems else [])
        keyed = None
        if v.po_matched and po_matches is not None:
            matched = po_matches.get(v.inv_id) or po_mod.voucher_match(None, str(final.get("po_number") or ""))
            keyed = jde_match.keyed_match(v, matched, settings)
            why = why or list(dict.fromkeys(keyed.held))
        if why:
            held.append(Held(v.inv_id, an8, str(final.get("vendor_name") or ""),
                             str(final.get("invoice_number") or "").strip(), str(final.get("invoice_date") or ""),
                             v.gross / 100, v.currency, why))  # fmt: skip
            continue
        tax, taxable = jde_review.pay_item_tax(v, settings)
        terms, _key, source = jde_voucher.terms_code(final, settings)
        if source == jde_voucher.TERMS_CREDIT:  # a credit memo goes with no terms code, as the files send it (B-896)
            terms = CREDIT_TERMS
        warnings, notes = _said(v, settings, tax is not None, fx_rates, matched=keyed is not None)
        warnings = [*((warned or {}).get(v.inv_id) or []), *warnings]
        if keyed is not None:
            warnings += keyed.warnings
            notes = [*keyed.notes, *notes]
            _match_said(po_matches.get(v.inv_id) if po_matches else None, warnings, notes, taken)
        printed = str(final.get("payment_terms") or "").strip()
        if not terms and printed:  # never the printed terms in JD Edwards' 3-character code (B-2618)
            notes.insert(0, f"Payment terms “{printed}” are printed and have no JD Edwards code (Settings → JD Edwards "
                         "E1): Payment terms is left blank, as the batch files send it, so JD Edwards takes the "
                         "supplier's own terms. Type their code if they differ.")  # fmt: skip
        bank = None
        if bank_accounts:
            code = str(final.get("pay_from_account") or "")
            bank = banks.keyed(code, dict(bank_accounts), jde_zfiles.glba(final, settings))
            if not code:
                warnings.append(f"No bank account is entered for {v.currency}: this voucher has none. JD Edwards "
                                "takes its own default bank unless one is typed.")  # fmt: skip
        ready.append(Voucher(
            invoice_id=v.inv_id, supplier_number=an8, supplier_name=str(final.get("vendor_name") or ""),
            company=jde_settings.company_of(final, settings),
            invoice_number=str(final.get("invoice_number") or "").strip(),
            invoice_date=jde_formats._date(final.get("invoice_date")),
            gl_date=jde_settings.gl_date_of(final, settings, on)[0],
            currency=v.currency, gross=v.gross / 100,
            terms=terms,
            remark=jde_remarks.voucher_remark(inv),
            due=jde_voucher.sent_due(final, inv) if settings.send_due_date else None,
            po_number=str(final.get("po_number") or "").strip(), tax_code=v.tax_code, tax_area=v.tax_area,
            tax=None if tax is None else tax / 100, taxable=None if taxable is None else taxable / 100,
            recovered=v.recoverable / 100, po_matched=v.po_matched, bank=bank,
            doc_type=jde_zfiles._doc_type(v, settings) or STANDARD_TYPE,
            lines=[Line(jde_accounts.ani(e["account"]),
                        str((gl_names or {}).get(str(e.get("gl_code") or "").strip()) or ""),
                        e["cents"] / 100, jde_remarks.line_remark(e), *_ledger(e, li))
                   for e, li in zip(v.entries, entry_lines(v.entries, final.get("line_items") or []),
                                    strict=True)],
            warnings=warnings, notes=notes,
            matches=keyed.rows if keyed is not None else [],
            match_tax=keyed.tax / 100 if keyed is not None else 0.0,
            retained=keyed.retained / 100 if keyed is not None else 0.0,
            amount_due=keyed.due / 100 if keyed is not None and keyed.due is not None and keyed.retained else None,
        ))  # fmt: skip
    return ready, held


def _ledger(entry: dict[str, Any], line: dict[str, Any] | None) -> tuple[str, str, float | None]:
    """A G/L line's subledger, its type and its units (D-268): the invoice line's own (its account's L or U), else
    the account mapping's subledger."""
    line = line or {}
    if line.get("subledger") or line.get("units"):
        return str(line.get("subledger") or ""), str(line.get("subledger_type") or ""), line.get("units") or None
    account = entry.get("account")
    return (account.sbl, account.sblt, None) if account is not None else ("", "", None)


def po_matches(store: Any, invoices: list[dict[str, Any]], charge_account: str = "") -> dict[int, po_mod.VoucherMatch]:
    """{invoice: its voucher match} of the ``invoices`` that quote a PO: the one kept with it at its approval
    (``po.kept_voucher_match``, D-284), else each line paired with its PO line as the purchase orders loaded have it
    (``po.match_invoice``, the invoice's own lines left out of what was billed before); ``charge_account``: where a
    charge the PO has no line for goes (``JdeSettings.charge_account``, D-265)."""
    from ..core.schema import InvoiceCoding

    out: dict[int, po_mod.VoucherMatch] = {}
    for inv in invoices:
        final = inv.get("final_output") or {}
        number = str(final.get("po_number") or "").strip()
        if number and (kept := po_mod.kept_voucher_match(final)) is not None:
            out[int(inv.get("id") or 0)] = kept
        elif number:
            coding = InvoiceCoding.model_validate({k: x for k, x in final.items() if k != "gl_distribution"})
            invoice_id = int(inv.get("id") or 0)
            out[invoice_id] = po_mod.voucher_match(po_mod.match_invoice(coding, store, invoice_id), number,
                                                   charge_account)  # fmt: skip
    return out


def _match_said(matched: po_mod.VoucherMatch | None, warnings: list[str], notes: list[str],
                taken: dict[tuple[str, str, str, str], float] | None = None) -> None:  # fmt: skip
    """What a voucher match's rows need said: a quantity matched over what JD Edwards had open on its PO line
    (``warnings``: a credit gives back, never over, B-2647); a PO line with no quantity in the purchase orders loaded,
    matched by amount, and one matched in part, left open for the rest (``notes``). What the rows match on a PO line
    is added up, and to what the vouchers above on the sheet match on it (``taken``, added to: B-2663)."""
    taken = {} if taken is None else taken
    rows = [row for row in (matched.rows if matched is not None else []) if row.on_po]
    mine: dict[tuple[str, str, str, str], float] = {}
    for row in rows:
        if not row.no_quantity and row.quantity is not None:
            key = (row.order_number, row.order_type, row.order_company, row.line)
            mine[key] = mine.get(key, 0.0) + row.quantity
    said: set[tuple[str, str, str, str]] = set()
    for row in rows:
        where = f"Order {row.order_number} line {row.line}"
        key = (row.order_number, row.order_type, row.order_company, row.line)
        if row.no_quantity and (row.retainage or row.order_type.upper() == po_mod.SUBCONTRACT):
            continue  # (a subcontract's lump sum: matched by its amount alone, D-273)
        count, cost = po_mod._qty(row.quantity or 0), f"{row.unit_cost or 0:,.2f}"
        if row.quantity_from == "amount":  # a line printed with no quantity (D-283)
            notes.append(f"{where}: the invoice prints no quantity; Quantity Matched is {count}, its amount "
                         f"({row.amount:,.2f}) at the PO line's unit cost {cost}.")  # fmt: skip
        elif row.quantity_from == "one":
            notes.append(f"{where}: the invoice prints no quantity and its amount ({row.amount:,.2f}) is no whole "
                         f"number of the PO line's unit cost {cost}: Quantity Matched is {count}. Check the quantity "
                         "JD Edwards shows for it.")  # fmt: skip
        if row.no_quantity:
            typed = (f"the invoice's {po_mod._qty(row.quantity)}" if row.quantity is not None
                     else "left empty")  # fmt: skip
            notes.append(f"{where} has no quantity in the purchase orders loaded: it is matched by its amount "
                         f"({row.amount:,.2f}) and Quantity Matched is {typed}. Check the quantity JD Edwards "
                         "shows for it.")  # fmt: skip
            continue
        if key in said or key not in mine or row.open_quantity is None:
            continue
        said.add(key)
        here, before, opened = mine[key], taken.get(key, 0.0), row.open_quantity
        total = here + before
        with_above = f", {po_mod._qty(total)} with the {po_mod._qty(before)} above on this sheet" if before else ""
        if here > 0 and total > opened + 1e-6:
            warnings.append(f"{where}: {po_mod._qty(here)} matched{with_above}, but JD Edwards had "
                            f"{po_mod._qty(opened)} open at the purchase orders' last load: it matches no more than is "
                            "open.")  # fmt: skip
        elif here > 0 and total < opened - 1e-6:
            notes.append(f"{where}: {po_mod._qty(here)} of the {po_mod._qty(opened)} open matched{with_above}; the "
                         f"line stays open for {po_mod._qty(opened - total)} (do not close it in Voucher "
                         "Match).")  # fmt: skip
    for key, quantity in mine.items():
        taken[key] = taken.get(key, 0.0) + quantity


def _said(v: jde_voucher._Voucher, settings: jde_settings.JdeSettings, taxed: bool,
          fx_rates: Mapping[str, float] | None, matched: bool = False) -> tuple[list[str], list[str]]:  # fmt: skip
    """(warnings, notes) under a voucher: what a person typing it would otherwise have to work out, or would not
    know to ask. ``matched``: keyed as voucher match rows, whose tax ``jde_match`` says (no G/L lines to keep a tax
    inside)."""

    def of(types: Any) -> str:  # the tax charged of these types
        cents = sum(jde_formats._units(t.get("tax_amount"), v.decimals) for t in v.charged if t["tax_type"] in types)
        return f"{cents / 100:,.2f}"

    def names(types: Any) -> str:
        return " and ".join(sorted(types))

    warnings = [f"{said[0].upper()}{said[1:]}."] if (said := jde_review.not_recovered(v)) else []
    notes: list[str] = []
    kept = v.expensed - {"GST", "HST"}  # the federal ones are the warning's
    if kept and not matched:
        notes.append(f"The {names(kept)} ({of(kept)}) is not recovered: it is inside the G/L lines, not in the tax "
                     "or taxable amount.")  # fmt: skip
    inside = {t["tax_type"] for t in v.charged} - v.recoverable_types - v.expensed
    # code C or B: the tax amount holds a tax JD Edwards does not post as recoverable
    if taxed and inside and not matched:
        notes.append(f"The tax amount {v.stam / 100:,.2f} is {v.recoverable / 100:,.2f} that JD Edwards recovers and "
                     f"{of(inside)} of {names(inside)} that stays inside the G/L lines (code {v.tax_code}: the tax "
                     "area's sales tax).")  # fmt: skip
    if v.meals:
        lines = [str(n) for n, e in enumerate(v.entries, 1) if e.get("meals_tax") and e.get("kind") == "expense"]
        where = f"G/L line{'s' if len(lines) > 1 else ''} {', '.join(lines)}"
        notes.append(
            f"Meals: half the tax on meals ({v.meals / 100:,.2f}) is not claimed. It is inside {where}, and the tax "
            "amount is the claimed part only: less than the area's rate on the taxable amount."
        )
    if v.foreign:
        home = jde_settings.domestic_of(v.final, settings)
        rate = jde_zfiles._rate(fx_rates, v.currency, home)
        notes.append(
            f"{v.currency} voucher: type the amounts as shown, in {v.currency} (foreign entry), not in {home}. "
            + (f"AP Coder's rate is {rate:.6g} {home} for 1 {v.currency} (Settings → Review; the batch file sends "
               "it): leave JD Edwards' own rate unless told to type one." if rate
               else "JD Edwards takes the exchange rate from its own table.")
        )  # fmt: skip
    po = str(v.final.get("po_number") or "").strip()
    if len(po) > jde_voucher.PO_WIDTH:
        notes.append(f"PO number {po} is longer than the {jde_voucher.PO_WIDTH} characters JD Edwards' purchase order "
                     "field holds: it cannot be typed there as it is.")  # fmt: skip
    if jde_voucher.self_assessed_pst(v.final, settings):  # code B on an area with no PST: E1 accrues none (B-2343)
        base = self_assessed_base(v.final, v.final.get("line_items") or [], "PST")
        notes.append(f"PST to self-assess: {v.province} PST on {base:,.2f}. Tax area {v.tax_area} holds no PST, so "
                     f"code {v.tax_code} has JD Edwards accrue none: book it from AP Coder's Self-assessment report, "
                     "which lists it.")  # fmt: skip
    if said := jde_review.area_mismatch(v, settings):  # the ship-to's area, another province's tax (B-2345)
        notes.append(f"{said[0].upper()}{said[1:]}.")
    return warnings, notes


def _check(v: Voucher) -> str:
    """The check line's words: G/L lines + recovered tax = gross; amounts matched + the tax JD Edwards works out on
    them = gross (D-265)."""
    if v.po_matched and not v.matches:
        return "PO-matched voucher: JD Edwards distributes it from the receipts"
    if v.amount_due is not None:  # a subcontract's progress billing (D-273): what is paid now
        said = f"{v.lines_total:,.2f} - {v.retained:,.2f} + {v.added:,.2f}"
        return f"{said} = {v.amount_due:,.2f}: balanced" if v.balanced else f"{said} is not {v.amount_due:,.2f}"
    said = f"{v.lines_total:,.2f} + {v.added:,.2f} = {v.gross:,.2f}"
    return f"{said}: balanced" if v.balanced else f"{v.lines_total:,.2f} + {v.added:,.2f} is not {v.gross:,.2f}"


def match_columns(v: Voucher) -> list[str]:
    """A voucher match's columns on the sheet: Voucher Match's, a subcontract's Retained Amount after Amount Matched
    (D-273), the subledger's two when its PO lines have one (D-268)."""
    columns = list(MATCH_COLUMNS)
    if v.amount_due is not None:
        columns.insert(columns.index("Amount Matched") + 1, RETAINED)
    return columns + (MATCH_LEDGER if _match_ledger(v) else [])


Row = list[tuple[Any, str]]  # a sheet row: four (value, kind) cells


def _head(v: Voucher, title: str) -> list[Row]:
    """A voucher's title and its fields, two a row, in the order Standard Voucher Entry asks for them: the header,
    the pay item, then the G/L distribution's own (the purchase order)."""
    bank = [] if v.bank is None else [[("G/L bank account", LABEL), (v.bank[0], TEXT), ("Bank account", LABEL),
                                       (v.bank[1], PLAIN)]]  # fmt: skip
    kind = [] if v.doc_type == STANDARD_TYPE else [[("Document type", LABEL), (v.doc_type, TEXT), ("", PLAIN),
                                                    ("", PLAIN)]]  # fmt: skip
    return [
        [(title, TITLE), (f"AP Coder invoice {v.invoice_id}", TITLE), ("", TITLE), ("", TITLE)],
        [("Supplier number", LABEL), (v.supplier_number, TEXT), ("Supplier name", LABEL), (v.supplier_name, PLAIN)],
        [("Company", LABEL), (v.company, TEXT), ("Invoice number", LABEL), (v.invoice_number, TEXT)],
        *kind,
        [("Invoice date", LABEL), (v.invoice_date, DATE), ("G/L date", LABEL), (v.gl_date, DATE)],
        [("Currency", LABEL), (v.currency, TEXT), ("Gross amount", LABEL), (v.gross, MONEY)],
        [("Payment terms", LABEL), (v.terms, TEXT), ("Remark", LABEL), (v.remark, TEXT)],
        [("Due date", LABEL), (v.due, DATE), ("PO number", LABEL), (v.po_number, TEXT)],
        [("Tax explanation code", LABEL), (v.tax_code, TEXT), ("Tax rate/area", LABEL), (v.tax_area, TEXT)],
        [("Tax amount", LABEL), (v.tax, MONEY), ("Taxable amount", LABEL), (v.taxable, MONEY)],
        *bank,
        *([[("Retained (held back)", LABEL), (v.retained, MONEY), ("Amount due now", LABEL), (v.amount_due, MONEY)]]
          if v.amount_due is not None else []),
        *([[("Voucher match", LABEL), ("", PLAIN), ("", PLAIN), ("", PLAIN)],
           [(name, LABEL) for name in match_columns(v)]] if v.matches
          else [[("G/L distribution", LABEL), ("", PLAIN), ("", PLAIN), ("", PLAIN)]]),
    ]  # fmt: skip


def _tail(v: Voucher) -> list[Row]:
    """The check line, then the warnings and notes, each wrapped over as many rows as it needs."""
    said = "Amounts matched + tax on the rows = gross" if v.matches else "G/L lines + recovered tax = gross"
    if v.amount_due is not None:
        said = "Amounts matched - retained + tax on the rows = amount due"
    rows: list[Row] = [[("Check", LABEL), (said, PLAIN), (v.lines_total, MONEY), (_check(v), PLAIN)]]
    for label, texts in (("Warning", v.warnings), ("Note", v.notes)):
        for text in texts:
            for n, part in enumerate(textwrap.wrap(text, NOTE_WIDTH)):
                rows.append([("" if n else label, LABEL), (part, PLAIN), ("", PLAIN), ("", PLAIN)])
    return rows


def _match_ledger(v: Voucher) -> bool:
    """Whether a voucher match's rows carry a subledger (its PO lines have one, D-268): its two columns are shown."""
    return any(m.subledger or m.subledger_type for m in v.matches)


def _parts(v: Voucher, n: int, count: int, ledger: bool = False) -> list[list[Row]]:
    """A voucher's rows (its title and fields, the G/L lines, the check line and what is said under it) as the parts
    that each fit a printed page: one, unless it is longer than a page. Then it is cut between G/L lines, each
    further part under the title and fields again ("Voucher 7 of 31, continued"), the check line with the last
    lines."""
    title = f"Voucher {n} of {count}"
    lines: list[Row] = [[(li.account, TEXT), (li.description, PLAIN), (li.amount, MONEY), (li.remark, TEXT),
                         *([(li.subledger, TEXT), (li.subledger_type, TEXT), (li.units, PLAIN)] if ledger else [])]
                        for li in v.lines]  # fmt: skip
    with_ledger = _match_ledger(v)
    lines += [[(m.order_number, TEXT) if m.on_po else (NOT_ON_PO, LABEL), (m.order_type, TEXT),
               (m.order_company, TEXT), (m.line, TEXT),
               (m.quantity, PLAIN), (m.uom, TEXT), (m.amount, MONEY),
               *([(m.retained, MONEY)] if v.amount_due is not None else []), (m.account, TEXT), (m.taxed, TEXT),
               (m.tax_code, TEXT), (m.tax_area, TEXT), (m.remark, TEXT),
               *([(m.subledger, TEXT), (m.subledger_type, TEXT)] if with_ledger else [])]
              for m in v.matches]  # fmt: skip
    head, tail = _head(v, title), _tail(v)
    room = ROWS_A_PAGE - len(head) - 1  # G/L lines (and the tail) a page holds under the fields, a blank row left
    parts: list[list[Row]] = []
    while len(lines) + len(tail) > room and len(lines) > 1:
        cut = max(1, min(room, len(lines) - 1))  # the last part keeps a line above the check line
        parts.append([*head, *lines[:cut]])
        head, lines = _head(v, f"{title}, continued"), lines[cut:]
    return [*parts, [*head, *lines, *tail]]


def build_xlsx(ready: list[Voucher], held: list[Held]) -> bytes:
    """The workbook: the vouchers a block each on the first sheet, the invoices not ready on the second."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ledger = any(li.ledger for v in ready for li in v.lines)  # (D-268)
    ws.append(LINE_COLUMNS + (LEDGER_COLUMNS if ledger else []))
    fill = PatternFill("solid", fgColor="E9EEF6")
    for cell in ws[1]:
        cell.font, cell.fill, cell.alignment = Font(bold=True), fill, Alignment(vertical="center")
    left = Alignment(horizontal="left")
    at, on_page = 1, 0  # the last row written; rows on the printed page so far
    for n, v in enumerate(ready, 1):
        for i, part in enumerate(_parts(v, n, len(ready), ledger)):
            # A part that would run over the page starts the next one, as each further part of a long voucher does
            # (B-2147: a break is written every time, so no stretch between two is longer than a page).
            if on_page and (i or on_page + len(part) + 1 > ROWS_A_PAGE):
                ws.row_breaks.append(Break(id=at))
                on_page = 0
            for row in part:
                ws.append(xlsx_row([None if value in ("", None) else value for value, _ in row]))
                at += 1
                pair = row[0][1] == LABEL and row[2][1] == LABEL  # the voucher's fields: read left to right
                for column, (_, kind) in enumerate(row, 1):
                    cell = ws.cell(row=at, column=column)
                    if kind in (LABEL, TITLE):
                        cell.font = Font(bold=True)
                    if kind == TITLE:
                        cell.fill = fill
                    if kind in (TEXT, MONEY, DATE):
                        cell.number_format = {TEXT: TEXT_FORMAT, MONEY: MONEY_FORMAT, DATE: DATE_FORMAT}[kind]
                    if pair:
                        cell.alignment = left
            on_page += len(part)
        ws.append([])
        at += 1
        on_page += 1
    if not ready:
        ws.append(["No voucher to type: see the sheet “Not ready”." if held else "No voucher to type."])
    for i, width in enumerate(WIDTHS + MATCH_WIDTHS, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    ws.print_title_rows = "1:1"
    # A voucher match's twelve columns print across the page (D-264); the G/L distribution's four upright
    ws.page_setup.orientation = "landscape" if any(v.matches for v in ready) else "portrait"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.oddFooter.center.text = "Page &P of &N"
    neutralise_sheet(ws)  # vendor names and remarks come from documents: never formulas

    other = wb.create_sheet()
    _sheet(other, NOT_READY, NOT_READY_COLUMNS, [{**vars(h), "why": "; ".join(h.why)} for h in held])
    for row in other.iter_rows(min_row=2):
        row[1].number_format, row[5].number_format = TEXT_FORMAT, MONEY_FORMAT
        row[-1].alignment = Alignment(wrap_text=True, vertical="top")
    other.page_setup.orientation = "landscape"  # one page wide, the reasons wrapped, not a column alone on a page
    other.page_setup.fitToWidth, other.page_setup.fitToHeight = 1, 0
    other.sheet_properties.pageSetUpPr.fitToPage = True
    other.print_title_rows = "1:1"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build(
    invoices: list[dict[str, Any]], settings: jde_settings.JdeSettings,
    vendor_lookup: jde_accounts.VendorLookup = None, *,
    batch: int | str = "", gl_names: Mapping[str, str] | None = None, on: dt.date | None = None,
    left_out: Mapping[int, list[str]] | None = None, fx_rates: Mapping[str, float] | None = None,
    warned: Mapping[int, list[str]] | None = None, bank_accounts: Mapping[str, dict[str, Any]] | None = None,
    po_matches: Mapping[int, po_mod.VoucherMatch] | None = None,
) -> tuple[bytes, str, str]:  # fmt: skip
    """(file bytes, file name, mime type) of a batch's keying sheet."""
    ready, held = vouchers(invoices, settings, vendor_lookup, gl_names=gl_names, on=on, left_out=left_out,
                           fx_rates=fx_rates, warned=warned, bank_accounts=bank_accounts,
                           po_matches=po_matches)  # fmt: skip
    name = f"ap_coder_jde_keying_{batch}.xlsx" if batch != "" else "ap_coder_jde_keying.xlsx"
    return build_xlsx(ready, held), name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
