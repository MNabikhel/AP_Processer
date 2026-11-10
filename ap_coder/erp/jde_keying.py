"""The JD Edwards keying sheet: what a person reads while typing vouchers into Standard Voucher Entry (D-210).

For the approved invoices of an export batch, one block per voucher: the pay item's fields (supplier, invoice
number, dates, gross amount, currency, payment terms, PO number, tax explanation code, tax rate/area, taxable and tax
amounts, remark), under them its G/L distribution (the account string as it is typed, ``BU.Object.Subsidiary``; the
account's description; the amount, net of the tax JD Edwards posts itself, a tax left in the expense inside it; the
remark) and a check line: G/L lines + recovered tax = gross. An invoice the JD Edwards checks hold back is not
dropped: it is on the second sheet, "Not ready", with why.

Every figure is the voucher's own (``jde._prepare``, which the batch files are made of too), so the sheet and the
Z-files of a batch say the same. In the workbook every code is a text cell (a business unit, a PO number or an object
account keeps its leading zeros), amounts are numbers with two decimals and dates are dates; it prints on upright
pages, the column names on each page, a voucher kept on one page when it fits. The CSV twin holds the same values,
one row per G/L line.

Entry points: ``vouchers`` (the values), ``build_xlsx``, ``build_csv`` and ``build`` (a batch's file by format).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.pagebreak import Break

from ..core.safe import csv_row, neutralise_sheet, xlsx_row
from . import jde
from .exports import _sheet, csv_amount

FORMAT, CSV_FORMAT = "jde_keying", "jde_keying_csv"  # export formats (``exports.FORMATS`` says what each is)
SHEET, NOT_READY = "Keying sheet", "Not ready"
# The G/L distribution's columns, named on the frozen first row and at the top of each printed page.
LINE_COLUMNS = ["Account number", "Account description", "Amount", "Explanation -Remark-"]
WIDTHS = [26, 36, 16, 36]
# Rows of a printed page under the column names (upright Letter or A4 at full size; fewer than fit, to be safe): a
# voucher that would run over the page starts on the next one.
ROWS_A_PAGE = 42
MONEY_FORMAT, DATE_FORMAT, TEXT_FORMAT = "#,##0.00", "yyyy-mm-dd", "@"
LABEL, TEXT, MONEY, DATE, TITLE, PLAIN = "label", "text", "money", "date", "title", "plain"
CSV_COLUMNS = [
    "Voucher", "AP Coder invoice", "Supplier number", "Supplier name", "Invoice number", "Invoice date", "G/L date",
    "Gross amount", "Currency", "Payment terms", "PO number", "Tax explanation code", "Tax rate/area",
    "Taxable amount", "Tax amount", "Remark", "Line", *LINE_COLUMNS, "Recovered tax", "Not ready",
]  # fmt: skip
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
    """One voucher: its pay item's fields and its G/L distribution."""

    invoice_id: int
    supplier_number: str
    supplier_name: str
    invoice_number: str
    invoice_date: dt.date | None
    gl_date: dt.date
    gross: float
    currency: str
    terms: str  # the JD Edwards terms code when the terms table has one, else the terms as printed
    po_number: str
    tax_code: str
    tax_area: str
    taxable: float | None  # None: no tax on the pay item
    tax: float | None
    remark: str
    recovered: float  # the tax JD Edwards posts itself: not in the lines
    po_matched: bool = False  # no G/L lines: JD Edwards distributes it from the receipts
    lines: list[Line] = field(default_factory=list)

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
    left_out: Mapping[int, list[str]] | None = None,
) -> tuple[list[Voucher], list[Held]]:  # fmt: skip
    """(the vouchers to type, the invoices not ready) of approved ``invoices``, in their order. ``gl_names``: each GL
    code's description; ``on``: the batch's day (the G/L date, when it is the export day); ``left_out``: {invoice:
    why} from the batch's own checks (a duplicate, a voucher JD Edwards has through the API), beside what each
    voucher's own check says here."""
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
        # The pay item's tax as ``voucher_rows`` sends it: none with the tax on G/L lines, the exempt code or no tax.
        exempt = ("", "E", settings.exempt_code.strip().upper())
        taxed = not settings.tax_on_gl and v.tax_code not in exempt and bool(v.stam)
        ready.append(Voucher(
            invoice_id=v.inv_id, supplier_number=an8, supplier_name=str(final.get("vendor_name") or ""),
            invoice_number=str(final.get("invoice_number") or "").strip(),
            invoice_date=jde._date(final.get("invoice_date")), gl_date=jde.gl_date_of(final, settings, on)[0],
            gross=v.gross / 100, currency=v.currency,
            terms=jde.terms_code(final, settings)[0] or str(final.get("payment_terms") or "").strip(),
            po_number=str(final.get("po_number") or "").strip(), tax_code=v.tax_code, tax_area=v.tax_area,
            taxable=v.atxa / 100 if taxed else None, tax=v.stam / 100 if taxed else None,
            remark=jde.voucher_remark(inv), recovered=v.recoverable / 100, po_matched=v.po_matched,
            lines=[Line(jde.ani(e["account"]), str((gl_names or {}).get(str(e.get("gl_code") or "").strip()) or ""),
                        e["cents"] / 100, jde.line_remark(e)) for e in v.entries],
        ))  # fmt: skip
    return ready, held


def _check(v: Voucher) -> str:
    """The check line's words: G/L lines + recovered tax = gross."""
    if v.po_matched:
        return "PO-matched voucher: JD Edwards distributes it from the receipts"
    said = f"{v.lines_total:,.2f} + {v.recovered:,.2f} = {v.gross:,.2f}"
    return f"{said}: balanced" if v.balanced else f"{v.lines_total:,.2f} + {v.recovered:,.2f} is not {v.gross:,.2f}"


Row = list[tuple[Any, str]]  # a sheet row: four (value, kind) cells


def _head(v: Voucher, title: str) -> list[Row]:
    """A voucher's title and the pay item's fields, two a row."""
    return [
        [(title, TITLE), (f"AP Coder invoice {v.invoice_id}", TITLE), ("", TITLE), ("", TITLE)],
        [("Supplier number", LABEL), (v.supplier_number, TEXT), ("Supplier name", LABEL), (v.supplier_name, PLAIN)],
        [("Invoice number", LABEL), (v.invoice_number, TEXT), ("PO number", LABEL), (v.po_number, TEXT)],
        [("Invoice date", LABEL), (v.invoice_date, DATE), ("G/L date", LABEL), (v.gl_date, DATE)],
        [("Gross amount", LABEL), (v.gross, MONEY), ("Currency", LABEL), (v.currency, TEXT)],
        [("Payment terms", LABEL), (v.terms, TEXT), ("Remark", LABEL), (v.remark, TEXT)],
        [("Tax explanation code", LABEL), (v.tax_code, TEXT), ("Tax rate/area", LABEL), (v.tax_area, TEXT)],
        [("Taxable amount", LABEL), (v.taxable, MONEY), ("Tax amount", LABEL), (v.tax, MONEY)],
        [("G/L distribution", LABEL), ("", PLAIN), ("", PLAIN), ("", PLAIN)],
    ]  # fmt: skip


def _parts(v: Voucher, n: int, count: int) -> list[list[Row]]:
    """A voucher's rows (its title and fields, the G/L lines, the check line) as the parts that each fit a printed
    page: one, unless it is longer than a page. Then it is cut between G/L lines, each further part under the
    title and fields again ("Voucher 7 of 31, continued"), the check line with the last lines."""
    title = f"Voucher {n} of {count}"
    lines: list[Row] = [[(li.account, TEXT), (li.description, PLAIN), (li.amount, MONEY), (li.remark, TEXT)]
                        for li in v.lines]  # fmt: skip
    tail: list[Row] = [[("Check", LABEL), ("G/L lines + recovered tax = gross", PLAIN), (v.lines_total, MONEY),
                        (_check(v), PLAIN)]]  # fmt: skip
    head = _head(v, title)
    room = ROWS_A_PAGE - len(head) - 1  # G/L lines (and the check line) a page holds under the fields, a blank row left
    parts: list[list[Row]] = []
    while len(lines) + len(tail) > room:
        cut = min(room, len(lines) - 1)  # the last part keeps a line above the check line
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
                pair = row[0][1] == LABEL and row[2][1] == LABEL  # the pay item's fields: read left to right
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


def build_csv(ready: list[Voucher], held: list[Held]) -> bytes:
    """The same values flat: one row per G/L line with its voucher's fields, then a row per invoice not ready."""

    def day(value: dt.date | None) -> str:
        return value.isoformat() if value else ""

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for n, v in enumerate(ready, 1):
        head = [n, v.invoice_id, v.supplier_number, v.supplier_name, v.invoice_number, day(v.invoice_date),
                day(v.gl_date), csv_amount(v.gross), v.currency, v.terms, v.po_number, v.tax_code, v.tax_area,
                csv_amount(v.taxable), csv_amount(v.tax), v.remark]  # fmt: skip
        for number, li in enumerate(v.lines, 1):
            writer.writerow(csv_row([*head, number, li.account, li.description, csv_amount(li.amount), li.remark,
                                     csv_amount(v.recovered), ""]))  # fmt: skip
        if not v.lines:
            writer.writerow(csv_row([*head, "", "", "", "", "", csv_amount(v.recovered), ""]))
    for h in held:
        head = ["", h.invoice_id, h.supplier_number, h.supplier_name, h.invoice_number, h.invoice_date, "",
                csv_amount(h.gross), h.currency]  # fmt: skip
        writer.writerow(csv_row([*head, *[""] * (len(CSV_COLUMNS) - len(head) - 1), "; ".join(h.why)]))
    return buf.getvalue().encode("utf-8-sig")  # BOM: Excel shows accents correctly


def build(
    fmt: str, invoices: list[dict[str, Any]], settings: jde.JdeSettings, vendor_lookup: jde.VendorLookup = None, *,
    batch: int | str = "", gl_names: Mapping[str, str] | None = None, on: dt.date | None = None,
    left_out: Mapping[int, list[str]] | None = None,
) -> tuple[bytes, str, str]:  # fmt: skip
    """(file bytes, file name, mime type) of a batch's keying sheet: the workbook, or with ``CSV_FORMAT`` its CSV."""
    ready, held = vouchers(invoices, settings, vendor_lookup, gl_names=gl_names, on=on, left_out=left_out)
    name = f"ap_coder_jde_keying_{batch}" if batch != "" else "ap_coder_jde_keying"
    if fmt == CSV_FORMAT:
        return build_csv(ready, held), f"{name}.csv", "text/csv"
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return build_xlsx(ready, held), f"{name}.xlsx", mime
