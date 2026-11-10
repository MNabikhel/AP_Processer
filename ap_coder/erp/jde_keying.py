"""The JD Edwards keying sheet: what a person reads while typing vouchers into Standard Voucher Entry (D-210).

For the approved invoices of an export batch, one block per voucher, in the order the screen asks: the header
(supplier, company, invoice number, dates, currency), the pay item (gross amount, payment terms, remark, due date,
tax explanation code, tax rate/area, tax and taxable amounts), then its G/L distribution (the account string as it is
typed, ``BU.Object.Subsidiary``; the account's description; the amount, net of the tax JD Edwards posts itself, a
tax left in the expense inside it; the remark), a check line (G/L lines + recovered tax = gross) and, in words, what
a person would otherwise work out: a tax that is not recovered, what the tax amount holds, the meals' half, a
foreign currency, a PO number too long. An invoice the JD Edwards checks hold back is not dropped: it is on the
second sheet, "Not ready", with why.

Every figure is the voucher's own (``jde._prepare``, which the batch files are made of too), so the sheet and the
Z-files of a batch say the same. Every code is a text cell (a business unit, a PO number or an object account keeps
its leading zeros), amounts are numbers with two decimals and dates are dates. It prints on upright pages, the
column names on each page; a voucher that fits a page is never cut, a longer one only between G/L lines. There is
no CSV of it: a spreadsheet opening one reads an account or a PO number as a number (B-2151).

Entry points: ``vouchers`` (the values), ``build_xlsx`` and ``build`` (a batch's file), ``keying_batches``.
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

from ..core.safe import neutralise_sheet, xlsx_row
from . import jde
from .exports import _sheet

FORMAT = jde.FORMATS[1]  # "jde_keying": the export format (``exports.FORMATS`` says what it is)
SHEET, NOT_READY = "Keying sheet", "Not ready"
# The G/L distribution's columns, named on the frozen first row and at the top of each printed page.
LINE_COLUMNS = ["Account number", "Account description", "Amount", "Explanation -Remark-"]
WIDTHS = [26, 36, 16, 36]
NOTE_WIDTH = 84  # characters of a note's row: the three columns beside its label
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


@dataclass
class Voucher:
    """One voucher: its header and pay item fields, its G/L distribution and what is said under it."""

    invoice_id: int
    supplier_number: str
    supplier_name: str
    company: str
    invoice_number: str
    invoice_date: dt.date | None
    gl_date: dt.date
    currency: str
    gross: float
    terms: str  # the JD Edwards terms code when the terms table has one, else the terms as printed
    remark: str
    due: dt.date | None  # None: JD Edwards works it out from the terms
    po_number: str
    tax_code: str
    tax_area: str
    tax: float | None  # None: no tax on the pay item
    taxable: float | None
    recovered: float  # the tax JD Edwards posts itself: not in the lines
    po_matched: bool = False  # no G/L lines: JD Edwards distributes it from the receipts
    lines: list[Line] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def lines_total(self) -> float:
        return round(sum(line.amount for line in self.lines), 2)

    @property
    def balanced(self) -> bool:
        return round(self.lines_total + self.recovered - self.gross, 2) == 0


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
    invoices: list[dict[str, Any]], settings: jde.JdeSettings, vendor_lookup: jde.VendorLookup = None, *,
    gl_names: Mapping[str, str] | None = None, on: dt.date | None = None,
    left_out: Mapping[int, list[str]] | None = None, fx_rates: Mapping[str, float] | None = None,
) -> tuple[list[Voucher], list[Held]]:  # fmt: skip
    """(the vouchers to type, the invoices not ready) of approved ``invoices``, in their order. ``gl_names``: each GL
    code's description; ``on``: the batch's day (the G/L date, when it is the export day); ``left_out``: {invoice:
    why} from the batch's own checks (a duplicate, a voucher JD Edwards has through the API), beside what each
    voucher's own check says here; ``fx_rates``: AP Coder's exchange rates, said on a foreign voucher."""
    ready: list[Voucher] = []
    held: list[Held] = []
    for inv, (an8, v) in zip(invoices, jde._vouchers(invoices, settings, vendor_lookup), strict=True):
        final = v.final
        why = list((left_out or {}).get(v.inv_id) or []) or (jde._accounts_once(v) if v.problems else [])
        if why:
            held.append(Held(v.inv_id, an8, str(final.get("vendor_name") or ""),
                             str(final.get("invoice_number") or "").strip(), str(final.get("invoice_date") or ""),
                             v.gross / 100, v.currency, why))  # fmt: skip
            continue
        tax, taxable = jde.pay_item_tax(v, settings)
        warnings, notes = _said(v, settings, tax is not None, fx_rates)
        ready.append(Voucher(
            invoice_id=v.inv_id, supplier_number=an8, supplier_name=str(final.get("vendor_name") or ""),
            company=settings.company_code, invoice_number=str(final.get("invoice_number") or "").strip(),
            invoice_date=jde._date(final.get("invoice_date")), gl_date=jde.gl_date_of(final, settings, on)[0],
            currency=v.currency, gross=v.gross / 100,
            terms=jde.terms_code(final, settings)[0] or str(final.get("payment_terms") or "").strip(),
            remark=jde.voucher_remark(inv), due=jde.sent_due(final, inv) if settings.send_due_date else None,
            po_number=str(final.get("po_number") or "").strip(), tax_code=v.tax_code, tax_area=v.tax_area,
            tax=None if tax is None else tax / 100, taxable=None if taxable is None else taxable / 100,
            recovered=v.recoverable / 100, po_matched=v.po_matched,
            lines=[Line(jde.ani(e["account"]), str((gl_names or {}).get(str(e.get("gl_code") or "").strip()) or ""),
                        e["cents"] / 100, jde.line_remark(e)) for e in v.entries],
            warnings=warnings, notes=notes,
        ))  # fmt: skip
    return ready, held


def _said(v: jde._Voucher, settings: jde.JdeSettings, taxed: bool,
          fx_rates: Mapping[str, float] | None) -> tuple[list[str], list[str]]:  # fmt: skip
    """(warnings, notes) under a voucher: what a person typing it would otherwise have to work out, or would not
    know to ask."""

    def of(types: Any) -> str:  # the tax charged of these types
        cents = sum(jde._units(t.get("tax_amount"), v.decimals) for t in v.charged if t["tax_type"] in types)
        return f"{cents / 100:,.2f}"

    def names(types: Any) -> str:
        return " and ".join(sorted(types))

    warnings = [f"{said[0].upper()}{said[1:]}."] if (said := jde.not_recovered(v)) else []
    notes: list[str] = []
    kept = v.expensed - {"GST", "HST"}  # the federal ones are the warning's
    if kept:
        notes.append(f"The {names(kept)} ({of(kept)}) is not recovered: it is inside the G/L lines, not in the tax "
                     "or taxable amount.")  # fmt: skip
    inside = {t["tax_type"] for t in v.charged} - v.recoverable_types - v.expensed
    if taxed and inside:  # code C or B: the tax amount holds a tax JD Edwards does not post as recoverable
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
        home = settings.domestic_currency.strip().upper()
        rate = jde._rate(fx_rates, v.currency, home)
        notes.append(
            f"{v.currency} voucher: type the amounts as shown, in {v.currency} (foreign entry), not in {home}. "
            + (f"AP Coder's rate is {rate:.6g} {home} for 1 {v.currency} (Settings → Review; the batch file sends "
               "it): leave JD Edwards' own rate unless told to type one." if rate
               else "JD Edwards takes the exchange rate from its own table.")
        )  # fmt: skip
    po = str(v.final.get("po_number") or "").strip()
    if len(po) > jde.PO_WIDTH:
        notes.append(f"PO number {po} is longer than the {jde.PO_WIDTH} characters JD Edwards' purchase order field "
                     "holds: it cannot be typed there as it is.")  # fmt: skip
    return warnings, notes


def _check(v: Voucher) -> str:
    """The check line's words: G/L lines + recovered tax = gross."""
    if v.po_matched:
        return "PO-matched voucher: JD Edwards distributes it from the receipts"
    said = f"{v.lines_total:,.2f} + {v.recovered:,.2f} = {v.gross:,.2f}"
    return f"{said}: balanced" if v.balanced else f"{v.lines_total:,.2f} + {v.recovered:,.2f} is not {v.gross:,.2f}"


Row = list[tuple[Any, str]]  # a sheet row: four (value, kind) cells


def _head(v: Voucher, title: str) -> list[Row]:
    """A voucher's title and its fields, two a row, in the order Standard Voucher Entry asks for them: the header,
    the pay item, then the G/L distribution's own (the purchase order)."""
    return [
        [(title, TITLE), (f"AP Coder invoice {v.invoice_id}", TITLE), ("", TITLE), ("", TITLE)],
        [("Supplier number", LABEL), (v.supplier_number, TEXT), ("Supplier name", LABEL), (v.supplier_name, PLAIN)],
        [("Company", LABEL), (v.company, TEXT), ("Invoice number", LABEL), (v.invoice_number, TEXT)],
        [("Invoice date", LABEL), (v.invoice_date, DATE), ("G/L date", LABEL), (v.gl_date, DATE)],
        [("Currency", LABEL), (v.currency, TEXT), ("Gross amount", LABEL), (v.gross, MONEY)],
        [("Payment terms", LABEL), (v.terms, TEXT), ("Remark", LABEL), (v.remark, TEXT)],
        [("Due date", LABEL), (v.due, DATE), ("PO number", LABEL), (v.po_number, TEXT)],
        [("Tax explanation code", LABEL), (v.tax_code, TEXT), ("Tax rate/area", LABEL), (v.tax_area, TEXT)],
        [("Tax amount", LABEL), (v.tax, MONEY), ("Taxable amount", LABEL), (v.taxable, MONEY)],
        [("G/L distribution", LABEL), ("", PLAIN), ("", PLAIN), ("", PLAIN)],
    ]  # fmt: skip


def _tail(v: Voucher) -> list[Row]:
    """The check line, then the warnings and notes, each wrapped over as many rows as it needs."""
    rows: list[Row] = [[("Check", LABEL), ("G/L lines + recovered tax = gross", PLAIN), (v.lines_total, MONEY),
                        (_check(v), PLAIN)]]  # fmt: skip
    for label, texts in (("Warning", v.warnings), ("Note", v.notes)):
        for text in texts:
            for n, part in enumerate(textwrap.wrap(text, NOTE_WIDTH)):
                rows.append([("" if n else label, LABEL), (part, PLAIN), ("", PLAIN), ("", PLAIN)])
    return rows


def _parts(v: Voucher, n: int, count: int) -> list[list[Row]]:
    """A voucher's rows (its title and fields, the G/L lines, the check line and what is said under it) as the parts
    that each fit a printed page: one, unless it is longer than a page. Then it is cut between G/L lines, each
    further part under the title and fields again ("Voucher 7 of 31, continued"), the check line with the last
    lines."""
    title = f"Voucher {n} of {count}"
    lines: list[Row] = [[(li.account, TEXT), (li.description, PLAIN), (li.amount, MONEY), (li.remark, TEXT)]
                        for li in v.lines]  # fmt: skip
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
    ws.append(LINE_COLUMNS)
    fill = PatternFill("solid", fgColor="E9EEF6")
    for cell in ws[1]:
        cell.font, cell.fill, cell.alignment = Font(bold=True), fill, Alignment(vertical="center")
    left = Alignment(horizontal="left")
    at, on_page = 1, 0  # the last row written; rows on the printed page so far
    for n, v in enumerate(ready, 1):
        for i, part in enumerate(_parts(v, n, len(ready))):
            # A part that would run over the page starts the next one, as each further part of a long voucher does
            # (B-2147: a break is written every time, so no stretch between two is longer than a page).
            if on_page and (i or on_page + len(part) + 1 > ROWS_A_PAGE):
                ws.row_breaks.append(Break(id=at))
                on_page = 0
            for row in part:
                ws.append(xlsx_row([None if value in ("", None) else value for value, _ in row]))
                at += 1
                pair = row[0][1] == LABEL and row[2][1] == LABEL  # the voucher's fields: read left to right
                for cell, (_, kind) in zip(ws[at], row, strict=True):
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
    for i, width in enumerate(WIDTHS, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    ws.print_title_rows = "1:1"
    ws.page_setup.orientation = "portrait"
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
    invoices: list[dict[str, Any]], settings: jde.JdeSettings, vendor_lookup: jde.VendorLookup = None, *,
    batch: int | str = "", gl_names: Mapping[str, str] | None = None, on: dt.date | None = None,
    left_out: Mapping[int, list[str]] | None = None, fx_rates: Mapping[str, float] | None = None,
) -> tuple[bytes, str, str]:  # fmt: skip
    """(file bytes, file name, mime type) of a batch's keying sheet."""
    ready, held = vouchers(invoices, settings, vendor_lookup, gl_names=gl_names, on=on, left_out=left_out,
                           fx_rates=fx_rates)  # fmt: skip
    name = f"ap_coder_jde_keying_{batch}.xlsx" if batch != "" else "ap_coder_jde_keying.xlsx"
    return build_xlsx(ready, held), name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
