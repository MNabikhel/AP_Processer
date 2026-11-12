"""ERP export files for approved invoices.

An export batch is a set of approved invoices handed to the ERP together. Each invoice is exported
once (the store remembers its batch), and a batch's file can be downloaded again at any time: it is
rebuilt from the approved data: the same invoices every time, with today's settings and master data (vendor IDs,
GL names, exchange rates).

Formats
-------
* ``xlsx``: a workbook with an *Invoices* sheet (one row per invoice), a *GL lines* sheet (the posting:
  one row per expense line and per recoverable-tax account) and a *By GL account* summary.
* ``csv``: one row per GL line, the shape most ERP AP-invoice imports accept.
* ``custom``: a CSV with the columns of your ERP's import (``export_layout``).

The two CSV files are for the ERP's loader, like the JD Edwards files: their text is written exactly as it
is, with no spreadsheet apostrophe (an invoice number ``'+A-100`` would miss the ERP's duplicate check).
The xlsx workbook is opened by people, so its formula-looking text is stored as plain text.
* ``jde``: JD Edwards E1 Z-table files (F0411Z1 / F0911Z1), built by ``jde.build_zip``.
* ``jde_keying``: the sheet read while typing vouchers into JD Edwards, built by ``jde_keying.build``.

Entry points: ``build`` (one batch's file in a format), ``distribution`` (an approved invoice's posting lines,
as stored or built again), ``line_rows`` / ``invoice_rows`` and ``entry_lines`` (the invoice line behind a
posting line, which the JD Edwards files use too).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections import defaultdict
from collections.abc import Iterable
from itertools import chain, islice
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..coding.tax import PLACE_OF_SUPPLY, TaxRateTable, TaxSetup, TaxTreatment, build_gl_distribution
from ..coding.vendors import master_vendor_id
from ..core.safe import neutralise_sheet, xlsx_row

FORMATS = {
    "xlsx": "Excel workbook: invoices, GL lines and a GL summary",
    "csv": "CSV: one row per GL line, for the ERP loader (its text exactly as it is: to look at it in Excel, use the "
    ".xlsx export)",
    "custom": "Custom CSV: your ERP's columns (set up below), for the ERP loader (to look at it in Excel, use the "
    ".xlsx export)",
    "jde": "JD Edwards E1 (F0411Z1 / F0911Z1): ZIP of Z-table files (Settings → JD Edwards E1)",
    "jde_keying": "JD Edwards keying sheet: an Excel workbook to read while typing vouchers into JD Edwards by hand, "
    "a block per voucher; only invoices that pass the JD Edwards checks go on it (Settings → JD Edwards E1)",
}

INVOICE_COLUMNS = [
    ("batch", "Batch"), ("invoice_id", "AP Coder #"), ("vendor_id", "Vendor ID"), ("vendor_name", "Vendor"),
    ("gst_hst_registration_number", "Vendor GST/HST #"), ("qst_registration_number", "Vendor QST #"),
    ("invoice_number", "Invoice #"), ("invoice_date", "Invoice date"), ("po_number", "PO #"),
    ("payment_terms", "Terms"), ("due_date", "Due date"),
    ("currency", "Currency"), ("subtotal", "Subtotal"), ("tax_total", "Tax"), ("grand_total", "Total"),
    ("supplier_province", "Supplier province"), ("ship_to_province", "Place of supply"),
    ("reviewer", "Approved by"), ("reviewed_at", "Approved at"), ("second_reviewer", "Second approver"),
    ("file_name", "File"), ("pay_from", "Pay from"), ("pay_from_gl", "Pay from GL"),
]  # fmt: skip
LINE_COLUMNS = [
    ("batch", "Batch"), ("invoice_id", "AP Coder #"), ("vendor_id", "Vendor ID"), ("vendor_name", "Vendor"),
    ("invoice_number", "Invoice #"),
    ("invoice_date", "Invoice date"), ("currency", "Currency"), ("line", "Line"), ("kind", "Type"),
    ("gl_code", "GL account"), ("gl_name", "GL name"), ("cost_center", "Cost center"),
    ("description", "Description"), ("tax_code", "Tax code"), ("net_amount", "Net"),
    ("non_recoverable_tax", "Non-recoverable tax"), ("amount", "Amount"), ("pay_from", "Pay from"),
    ("pay_from_gl", "Pay from GL"),
]  # fmt: skip
MONEY_HEADERS = {"Subtotal", "Tax", "Total", "Net", "Non-recoverable tax", "Amount"}
# Real dates in the workbook (Excel sorts and filters them as dates), not text (B-470); the CSV keeps YYYY-MM-DD.
# The invoice directory's "Approved on" and "Received" (``directory.COLUMNS``) are dates and times too.
DATE_HEADERS = {"Invoice date": "yyyy-mm-dd", "Due date": "yyyy-mm-dd", "Approved at": "yyyy-mm-dd hh:mm",
                "Approved on": "yyyy-mm-dd hh:mm", "Received": "yyyy-mm-dd hh:mm"}  # fmt: skip


def _excel_date(value: Any) -> Any:
    """'2026-10-01' -> date, '2026-10-01 14:05:09' or '2026-10-01T14:05:09' -> datetime (seconds kept); anything
    else as it is."""
    if not isinstance(value, str) or not value.strip():
        return value
    text = value.strip().replace("T", " ")
    try:
        return dt.datetime.fromisoformat(text).replace(tzinfo=None) if " " in text else dt.date.fromisoformat(text)
    except ValueError:
        return value


def _vendor_id(final: dict[str, Any], vendor_ids: dict[str, str]) -> str:
    """The ERP vendor ID: by vendor name, else by GST/HST number (``vendors.master_vendor_id``)."""
    return master_vendor_id(vendor_ids, final.get("vendor_name"), final.get("gst_hst_registration_number"))


def pay_from(final: dict[str, Any], bank_accounts: dict[str, dict[str, Any]] | None) -> dict[str, str]:
    """{"pay_from": the bank account's name, "pay_from_gl": its cash GL account} for the account an invoice is paid
    from (D-66), as the accounts are today (a batch is rebuilt with today's master data, D-29); blank when none."""
    code = str(final.get("pay_from_account") or "")
    account = (bank_accounts or {}).get(code) if code else None
    if account is None:
        return {"pay_from": code, "pay_from_gl": ""}
    return {"pay_from": str(account.get("name") or code), "pay_from_gl": str(account.get("gl_account") or "")}


def invoice_rows(
    invoices: list[dict[str, Any]], batch: int | str = "", vendor_ids: dict[str, str] | None = None,
    bank_accounts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:  # fmt: skip
    """``invoices``: store.get_invoice() dicts of approved invoices; ``vendor_ids``: ``Store.vendor_ids()``;
    ``bank_accounts``: our bank accounts by code (``Store.bank_accounts``), for Pay from."""
    vendor_ids = vendor_ids or {}
    rows = []
    for inv in invoices:
        final = inv.get("final_output") or {}
        rows.append(
            {
                **pay_from(final, bank_accounts),
                **{k: final.get(k) for k, _ in INVOICE_COLUMNS if k in final},
                "batch": batch,
                "invoice_id": inv["id"],
                "vendor_id": _vendor_id(final, vendor_ids),
                # the ship-to printed, else the place of supply worked out when it was coded (pipeline)
                "ship_to_province": final.get("ship_to_province") or final.get(PLACE_OF_SUPPLY) or "",
                # printed, else worked out from the terms / vendor master / default when it was approved
                "due_date": final.get("due_date") or inv.get("due_date") or "",
                "reviewer": inv.get("reviewer"),
                "reviewed_at": (inv.get("reviewed_at") or "").replace("T", " "),
                "second_reviewer": inv.get("second_reviewer") or "",
                "file_name": inv.get("file_name"),
            }  # fmt: skip
        )
    return rows


def distribution(final: dict[str, Any], tax_treatments: dict[str, TaxTreatment] | None = None) -> list[dict[str, Any]]:
    """An approved invoice's posting lines: as stored when it was coded, else (an invoice approved by an older
    version) built again from its coding, with ``tax_treatments`` (``Store.tax_treatments()``; without them the
    default treatments, and a recoverable tax line has no GL account). A cost center left UNASSIGNED is blank: it is
    not a cost center the ERP knows (B-1211)."""
    dist = final.get("gl_distribution")
    if not dist:
        from ..core.schema import InvoiceCoding

        try:
            coding = InvoiceCoding.model_validate({k: v for k, v in final.items() if k != "gl_distribution"})
        except ValueError:
            return []
        dist = build_gl_distribution(coding, TaxSetup(TaxRateTable(()), dict(tax_treatments or {})))
    return [{**e, "cost_center": ""} if _unassigned(e.get("cost_center")) else e for e in dist]


def _unassigned(cost_center: Any) -> bool:
    return str(cost_center or "").strip().upper() == "UNASSIGNED"


def line_rows(
    invoices: list[dict[str, Any]], gl_names: dict[str, str] | None = None, batch: int | str = "",
    vendor_ids: dict[str, str] | None = None, tax_treatments: dict[str, TaxTreatment] | None = None,
    bank_accounts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:  # fmt: skip
    gl_names, vendor_ids = gl_names or {}, vendor_ids or {}
    rows = []
    for inv in invoices:
        final = inv.get("final_output") or {}
        paid_from = pay_from(final, bank_accounts)
        dist = distribution(final, tax_treatments)
        codes = line_tax_codes(dist, final.get("line_items") or [], final)
        for e, code in zip(dist, codes, strict=True):
            rows.append(
                {
                    **paid_from,
                    "batch": batch,
                    "invoice_id": inv["id"],
                    "vendor_id": _vendor_id(final, vendor_ids),
                    "vendor_name": final.get("vendor_name"),
                    "invoice_number": final.get("invoice_number"),
                    "invoice_date": final.get("invoice_date"),
                    "currency": final.get("currency"),
                    "line": e.get("line_number") or "",
                    "kind": "Expense"
                    if e.get("kind") == "expense"
                    else ("Non-recoverable tax" if e.get("non_recoverable_tax") else "Recoverable tax"),
                    "gl_code": e.get("gl_code"),
                    "gl_name": gl_names.get(e.get("gl_code") or "", ""),
                    "cost_center": e.get("cost_center") or "",
                    "description": e.get("description") or "",
                    "tax_code": code,
                    "net_amount": e.get("net_amount"),
                    "non_recoverable_tax": e.get("non_recoverable_tax"),
                    "amount": e.get("amount"),
                }  # fmt: skip
            )
    return rows


def entry_lines(dist: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
    """The invoice line behind each posting line (None for a tax line, or an expense line no invoice line has the
    number of): the expense lines follow the invoice lines in order (``build_gl_distribution``); matched by line
    number when the counts differ."""
    if sum(1 for e in dist if e.get("kind") == "expense") == len(items):
        it = iter(items)
        return [next(it) if e.get("kind") == "expense" else None for e in dist]
    by_number: dict[Any, dict[str, Any]] = {}
    for li in items:
        by_number.setdefault(li.get("line_number"), li)
    return [by_number.get(e.get("line_number")) if e.get("kind") == "expense" else None for e in dist]


def line_tax_codes(dist: list[dict[str, Any]], items: list[dict[str, Any]],
                   final: dict[str, Any] | None = None) -> list[str]:  # fmt: skip
    """The tax code of the invoice line behind each posting line (``entry_lines``) in the export's words ("GST +
    PST", "Exempt", D-64), "" for a tax line."""
    from ..coding.line_tax import export_code, legacy_basis
    from ..coding.tax import stored_supply

    final = final or {}
    province, charged = stored_supply(final), legacy_basis(items, final.get("tax_lines") or [])
    return [export_code(li, province, charged) if li is not None else "" for li in entry_lines(dist, items)]


def gl_summary(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[tuple[str, str, str], float] = defaultdict(float)
    for r in lines:
        totals[(r["gl_code"] or "", r["gl_name"] or "", r["currency"] or "")] += float(r["amount"] or 0)
    return [
        {"gl_code": gl, "gl_name": name, "currency": cur, "amount": round(amount, 2)}
        for (gl, name, cur), amount in sorted(totals.items())
    ]


def build_xlsx(
    invoices: list[dict[str, Any]], gl_names: dict[str, str] | None = None, batch: int | str = "",
    vendor_ids: dict[str, str] | None = None, tax_treatments: dict[str, TaxTreatment] | None = None,
    bank_accounts: dict[str, dict[str, Any]] | None = None,
) -> bytes:  # fmt: skip
    lines = line_rows(invoices, gl_names, batch, vendor_ids, tax_treatments, bank_accounts)
    wb = Workbook()
    _sheet(wb.active, "Invoices", INVOICE_COLUMNS, invoice_rows(invoices, batch, vendor_ids, bank_accounts))
    _sheet(wb.create_sheet(), "GL lines", LINE_COLUMNS, lines)
    _sheet(
        wb.create_sheet(), "By GL account",
        [("gl_code", "GL account"), ("gl_name", "GL name"), ("currency", "Currency"), ("amount", "Amount")],
        gl_summary(lines),
    )  # fmt: skip
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sheet(ws: Any, title: str, columns: list[tuple[str, str]], rows: list[dict[str, Any]]) -> None:
    ws.title = title
    ws.append([label for _, label in columns])
    dated = {key for key, label in columns if label in DATE_HEADERS}
    for r in rows:
        ws.append(xlsx_row([_excel_date(r.get(key)) if key in dated else r.get(key) for key, _ in columns]))
    header_fill = PatternFill("solid", fgColor="E9EEF6")
    for cell in ws[1]:
        cell.font, cell.fill, cell.alignment = Font(bold=True), header_fill, Alignment(vertical="center")
    for i, (_, label) in enumerate(columns, start=1):
        width = max([len(label)] + [len(str(r.get(columns[i - 1][0]) or "")) for r in rows[:500]])
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 60)
        if label in MONEY_HEADERS:
            for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                row[0].number_format = "#,##0.00"
        if label in DATE_HEADERS:
            for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                if isinstance(row[0].value, (dt.date, dt.datetime)):
                    row[0].number_format = DATE_HEADERS[label]
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    neutralise_sheet(ws)  # vendor names and descriptions come from documents: never formulas


# --- A big sheet, written without openpyxl's cells (D-157, D-169) ----------------------------------------------------
# Search to Excel (50,000 rows of 9 columns) spent 7 of its 8.5 s making and writing openpyxl's cells one by one, in
# its write-only workbook. ``sheet_workbook`` writes the workbook's parts itself, a part at a time: what it holds is
# what ``_sheet`` makes, cell by cell (each value and type as openpyxl writes it: numbers "%.16g", dates as Excel's
# serial days, text inline; the same number formats, header style, widths from the first 500 rows, frozen header and
# filter; ``tests/erp/test_directory.py`` reads both back and compares them).
_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PACKAGE = "http://schemas.openxmlformats.org/package/2006"
_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006"
_MONEY_FORMAT = 4  # Excel's own "#,##0.00"
_SHEET_PARTS = {
    "[Content_Types].xml": (
        f'<Types xmlns="{_PACKAGE}/content-types"><Default Extension="rels" ContentType="application/vnd.'
        'openxmlformats-package.relationships+xml" /><Default Extension="xml" ContentType="application/xml" />'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.'
        'styles+xml" /><Override PartName="/xl/theme/theme1.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.theme+xml" /><Override PartName="/docProps/core.xml" ContentType="application/vnd.'
        'openxmlformats-package.core-properties+xml" /><Override PartName="/docProps/app.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.extended-properties+xml" /><Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml" /><Override '
        'PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.'
        'main+xml" /></Types>'
    ),
    "_rels/.rels": (
        f'<Relationships xmlns="{_PACKAGE}/relationships"><Relationship Type="{_OFFICE}/relationships/officeDocument" '
        f'Target="xl/workbook.xml" Id="rId1" /><Relationship Type="{_PACKAGE}/relationships/metadata/core-properties" '
        f'Target="docProps/core.xml" Id="rId2" /><Relationship Type="{_OFFICE}/relationships/extended-properties" '
        'Target="docProps/app.xml" Id="rId3" /></Relationships>'
    ),
    "docProps/app.xml": (
        f'<Properties xmlns="{_OFFICE}/extended-properties"><Application>Microsoft Excel Compatible / AP Coder'
        "</Application></Properties>"
    ),
    "xl/_rels/workbook.xml.rels": (
        f'<Relationships xmlns="{_PACKAGE}/relationships"><Relationship Type="{_OFFICE}/relationships/worksheet" '
        f'Target="/xl/worksheets/sheet1.xml" Id="rId1" /><Relationship Type="{_OFFICE}/relationships/styles" '
        f'Target="styles.xml" Id="rId2" /><Relationship Type="{_OFFICE}/relationships/theme" '
        'Target="theme/theme1.xml" Id="rId3" /></Relationships>'
    ),
}


def sheet_workbook(out: Any, title: str, columns: list[tuple[str, str]], rows: Iterable[dict[str, Any]]) -> None:
    """The workbook of one sheet ``_sheet`` makes of ``rows`` (the same cells, formats, widths, frozen header and
    filter), written to ``out`` (a binary file) a row at a time, without an openpyxl cell for each value."""
    import zipfile
    from math import isinf, isnan
    from xml.sax.saxutils import escape

    from openpyxl.cell.cell import ERROR_CODES
    from openpyxl.utils.datetime import to_excel
    from openpyxl.writer.theme import theme_xml

    from ..core.safe import FORMULA_START

    formats = {fmt: 164 + n for n, fmt in enumerate(dict.fromkeys(DATE_HEADERS.values()))}
    styles: dict[tuple[int, bool], int] = {}  # (number format, quote prefix) -> the cell style (0, 1: the header's)

    def style(number_format: int, quoted: bool) -> int:
        return styles.setdefault((number_format, quoted), len(styles) + 2)

    def text(value: str) -> str:
        value = value[:32767]  # (as openpyxl keeps it)
        stripped = value.strip()
        space = ' xml:space="preserve"' if stripped and stripped != value else ""
        return f"<is><t{space}>{escape(value)}</t></is>"

    def cell(ref: str, value: Any, money: bool, date_format: str) -> str:
        """``value`` as openpyxl writes it at ``ref`` ("" when no cell is written)."""
        dated = bool(date_format) and isinstance(value, (dt.date, dt.datetime))
        quoted = isinstance(value, str) and value.startswith(FORMULA_START)
        styled = money or dated or quoted
        if value is None:
            return f'<c r="{ref}" s="{style(_MONEY_FORMAT, False)}" t="n" />' if styled else ""
        s = f' s="{style(formats[date_format] if dated else _MONEY_FORMAT if money else 0, quoted)}"' if styled else ""
        if isinstance(value, bool):
            return f'<c r="{ref}"{s} t="b"><v>{int(value)}</v></c>'
        if isinstance(value, (int, float)):
            number = "" if isnan(value) or isinf(value) else "%.16g" % value  # noqa: UP031 - as openpyxl writes it
            return f'<c r="{ref}"{s} t="n"><v>{number}</v></c>'
        if isinstance(value, (dt.date, dt.datetime)):
            if not dated:  # (a date openpyxl would give its own format: never in these sheets)
                raise TypeError(f"{ref}: a date outside a date column")
            return f'<c r="{ref}"{s} t="n"><v>{"%.16g" % to_excel(value)}</v></c>'  # noqa: UP031
        if not isinstance(value, str):
            raise TypeError(f"{ref}: {type(value).__name__} can't be written to Excel")
        if value == "":
            return f'<c r="{ref}"{s} t="inlineStr" />'
        if not quoted and value in ERROR_CODES:
            return f'<c r="{ref}"{s} t="e"><v>{escape(value)}</v></c>'
        return f'<c r="{ref}"{s} t="inlineStr">{text(value)}</c>'

    letters = [get_column_letter(i) for i in range(1, len(columns) + 1)]
    kinds = [(label in MONEY_HEADERS, DATE_HEADERS.get(label, "")) for _, label in columns]
    dated = {key for key, label in columns if label in DATE_HEADERS}
    rows = iter(rows)
    first = list(islice(rows, 500))
    widths = [min(max(10, max([len(label)] + [len(str(r.get(key) or "")) for r in first]) + 2), 60)
              for key, label in columns]  # fmt: skip
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        with z.open("xl/worksheets/sheet1.xml", "w", force_zip64=True) as sheet:
            cols = "".join(f'<col width="{w}" customWidth="1" min="{n}" max="{n}" />'
                           for n, w in enumerate(widths, start=1))  # fmt: skip
            header = "".join(f'<c r="{letter}1" s="1" t="inlineStr">{text(label)}</c>'
                             for letter, (_, label) in zip(letters, columns, strict=True))  # fmt: skip
            sheet.write(
                f'<worksheet xmlns="{_MAIN}"><sheetPr><outlinePr summaryBelow="1" summaryRight="1" /><pageSetUpPr />'
                '</sheetPr><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
                'activePane="bottomLeft" state="frozen" /><selection pane="bottomLeft" activeCell="A1" sqref="A1" />'
                '</sheetView></sheetViews><sheetFormatPr baseColWidth="8" defaultRowHeight="15" />'
                f'<cols>{cols}</cols><sheetData><row r="1">{header}</row>'.encode()
            )
            count, part = 0, []
            for r in chain(first, rows):
                count += 1
                values = xlsx_row([_excel_date(r.get(key)) if key in dated else r.get(key) for key, _ in columns])
                part.append(f'<row r="{count + 1}">' + "".join(
                    cell(f"{letter}{count + 1}", value, money, date_format)
                    for letter, value, (money, date_format) in zip(letters, values, kinds, strict=True)
                ) + "</row>")  # fmt: skip
                if len(part) == 1000:
                    sheet.write("".join(part).encode())
                    part = []
            end = f"{letters[-1]}{count + 1}"
            sheet.write(("".join(part) + f'</sheetData><autoFilter ref="A1:{end}" /><pageMargins left="0.75" '
                         'right="0.75" top="1" bottom="1" header="0.5" footer="0.5" /></worksheet>'
                         ).encode())  # fmt: skip
        number_formats = "".join(f'<numFmt numFmtId="{n}" formatCode="{escape(fmt)}" />' for fmt, n in formats.items())
        xfs = "".join(
            f'<xf numFmtId="{fmt}" fontId="0" fillId="0" borderId="0" pivotButton="0" quotePrefix="{int(quoted)}" '
            'xfId="0" />'
            for (fmt, quoted), _ in sorted(styles.items(), key=lambda s: s[1])
        )
        z.writestr("xl/styles.xml", (
            f'<styleSheet xmlns="{_MAIN}"><numFmts count="{len(formats)}">{number_formats}</numFmts><fonts count="2">'
            '<font><name val="Calibri" /><family val="2" /><color theme="1" /><sz val="11" /><scheme val="minor" />'
            '</font><font><b val="1" /></font></fonts><fills count="3"><fill><patternFill /></fill><fill><patternFill '
            'patternType="gray125" /></fill><fill><patternFill patternType="solid"><fgColor rgb="00E9EEF6" />'
            '</patternFill></fill></fills><borders count="1"><border><left /><right /><top /><bottom /><diagonal />'
            '</border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" />'
            f'</cellStyleXfs><cellXfs count="{len(styles) + 2}"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" '
            'pivotButton="0" quotePrefix="0" xfId="0" /><xf numFmtId="0" fontId="1" fillId="2" borderId="0" '
            'applyAlignment="1" pivotButton="0" quotePrefix="0" xfId="0"><alignment vertical="center" /></xf>'
            f'{xfs}</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0" hidden="0" />'
            '</cellStyles><tableStyles count="0" defaultTableStyle="TableStyleMedium9" '
            'defaultPivotStyle="PivotStyleLight16" /></styleSheet>'
        ))  # fmt: skip
        sheet_name = escape(title, {'"': "&quot;"})
        z.writestr("xl/workbook.xml", (
            f'<workbook xmlns:r="{_OFFICE}/relationships" xmlns="{_MAIN}"><workbookPr /><workbookProtection />'
            '<bookViews><workbookView visibility="visible" minimized="0" showHorizontalScroll="1" '
            'showVerticalScroll="1" showSheetTabs="1" tabRatio="600" firstSheet="0" activeTab="0" '
            f'autoFilterDateGrouping="1" /></bookViews><sheets><sheet name="{sheet_name}" sheetId="1" state="visible" '
            'r:id="rId1" /></sheets><definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" '
            f"hidden=\"1\">'{escape(title)}'!$A$1:${letters[-1]}${count + 1}</definedName></definedNames>"
            '<calcPr calcId="124519" fullCalcOnLoad="1" /></workbook>'
        ))  # fmt: skip
        made = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        z.writestr("docProps/core.xml", (
            f'<cp:coreProperties xmlns:cp="{_PACKAGE}/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/'
            '1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:creator>AP Coder</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{made}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{made}</dcterms:modified></cp:coreProperties>'
        ))  # fmt: skip
        z.writestr("xl/theme/theme1.xml", theme_xml)
        for name, part_xml in _SHEET_PARTS.items():
            z.writestr(name, part_xml)


def csv_amount(value: Any) -> Any:
    """An amount as a CSV shows it: two decimals (4350.00, not 4350.0); anything else as it is ("" for none)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.2f}"
    return "" if value is None else value


def build_csv(
    invoices: list[dict[str, Any]], gl_names: dict[str, str] | None = None, batch: int | str = "",
    vendor_ids: dict[str, str] | None = None, tax_treatments: dict[str, TaxTreatment] | None = None,
    bank_accounts: dict[str, dict[str, Any]] | None = None,
) -> bytes:  # fmt: skip
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in LINE_COLUMNS])
    for r in line_rows(invoices, gl_names, batch, vendor_ids, tax_treatments, bank_accounts):
        # for the ERP's loader: text as it is, no spreadsheet apostrophe (see the module docstring)
        writer.writerow([csv_amount(r.get(key)) if label in MONEY_HEADERS else r.get(key) if r.get(key) is not None
                         else "" for key, label in LINE_COLUMNS])  # fmt: skip
    return buf.getvalue().encode("utf-8-sig")  # BOM: Excel shows accents correctly


def custom_rows(
    invoices: list[dict[str, Any]], gl_names: dict[str, str] | None = None, batch: int | str = "",
    vendor_ids: dict[str, str] | None = None, tax_treatments: dict[str, TaxTreatment] | None = None,
    bank_accounts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:  # fmt: skip
    """Posting lines, each with its invoice's fields too (for a custom layout)."""
    heads = {r["invoice_id"]: r for r in invoice_rows(invoices, batch, vendor_ids, bank_accounts)}
    lines = line_rows(invoices, gl_names, batch, vendor_ids, tax_treatments, bank_accounts)
    return [{**heads.get(r["invoice_id"], {}), **r} for r in lines]


def build(
    fmt: str, invoices: list[dict[str, Any]], gl_names: dict[str, str] | None = None, batch: int | str = "",
    vendor_ids: dict[str, str] | None = None, layout: Any = None,
    tax_treatments: dict[str, TaxTreatment] | None = None, bank_accounts: dict[str, dict[str, Any]] | None = None,
) -> tuple[bytes, str, str]:  # fmt: skip
    """(file bytes, file name, mime type). ``tax_treatments``: ``Store.tax_treatments()``, for the posting lines of
    an invoice approved without them stored (``distribution``); ``bank_accounts``: our bank accounts by code, for the
    Pay from columns (D-66)."""
    name = f"ap_coder_export_{batch}" if batch != "" else "ap_coder_export"
    if fmt == "jde":  # needs its settings and checks: see ap_coder.erp.jde.build_zip
        raise ValueError("JD Edwards files are built with ap_coder.erp.jde.build_zip")
    if fmt == "jde_keying":  # the JD Edwards settings and checks too
        raise ValueError("The JD Edwards keying sheet is built with ap_coder.erp.jde_keying.build")
    if fmt == "csv":
        return (
            build_csv(invoices, gl_names, batch, vendor_ids, tax_treatments, bank_accounts),
            f"{name}.csv",
            "text/csv",
        )
    if fmt == "custom":
        from . import export_layout

        rows = custom_rows(invoices, gl_names, batch, vendor_ids, tax_treatments, bank_accounts)
        return (
            export_layout.build(rows, layout or export_layout.Layout(export_layout.default_columns())),
            (f"{name}_custom.csv"),
            "text/csv",
        )
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return build_xlsx(invoices, gl_names, batch, vendor_ids, tax_treatments, bank_accounts), f"{name}.xlsx", mime
