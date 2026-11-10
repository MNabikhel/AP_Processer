"""A made-up company's JD Edwards exports, in the layouts EnterpriseOne writes them, for the tests, the benches, a
demo and the docs. The values are invented (``jde_company``: Maple Ridge Holdings Ltd., company ``00210``); the
layouts, with their quirks, are a real E1's:

* every ``.xlsx`` (a grid's export): one sheet ``Sheet1``, the header in row 1, nearly everything text (codes with
  leading zeros, the company, the pay item), a blank field one space (some columns truly empty), numbers and dates
  real, a business unit right-justified to 12 characters;
* ``chart_xlsx``: the account master, 110 columns, sorted by padded business unit, object, subsidiary; the
  description indented by the level of detail and cut at 30 characters in all; a business unit's own header row has
  no object;
* ``business_units_xlsx``: the business-unit master, 67 columns; ``suppliers_xlsx``: the supplier master, 10;
* ``vendor_info_xlsx``: the vendor information list, 22 columns, one row per supplier paid by any company of the
  group (the books' suppliers and a few of another company's), sorted by name as stored (a name starting with a
  space first). Two columns are both named ``Business Unit`` (the number, then its name). Every cell is text
  (number format ``@``; an empty one truly empty) but the supplier number and the payment count (numbers) and the
  last payment's date (``mm-dd-yy``); the last row is a totals row, only its payment count filled. A name can
  start with a space or carry ``(DNU)``, ``DO NOT USE``, ``O/A`` or a run of spaces and an old reference number;
  the tax ID is free text (a business number with or without its program account, an EIN, a word);
* ``journal_csv``: report R09321 "Transaction Journal" (vouchers only) saved as CSV. A printed report, not a table:
  UTF-8 with a BOM, CRLF, 5 title rows, the header over rows 6 and 7, data from row 8 at fixed positions
  (``JOURNAL_POSITIONS``), a voucher's first row starting ``PV``, company, G/L date and its other rows with those
  three blank, no document number, rows as long as their last filled cell, a company total row and ``Grand Total``.
  A voucher is its coding lines, then for each pay item JDE's own payable line and (when taxed) GST line;
* ``voucher_headers_xlsx``: the supplier ledger inquiry grid, one row per pay item, 56 columns, a totals row last;
* ``gl_distribution_xlsx``, ``pay_items_xlsx``, ``payment_history_xlsx``: one voucher's three grids (36, 44 and 24
  columns), the first two ending with the blank row the screen keeps for the next entry.

Only the standard grid and report column names are kept; a company's own category-code labels are ``Cat Code NN``
here. ``write_all(folder)`` writes every file and returns the truth they were written from; the same seed and size
give the same bytes.

    python -m ap_coder.bench.jde_exports OUT [--seed 1] [--size small|year]"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import random
import re
import zipfile
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from ..core.chart import join_account, split_account
from .jde_company import OVERHEAD_CLASS, Account, BusinessUnit, Supplier, Truth, Voucher, build

BLANK = " "  # an empty text field of a grid's export
DATE_FORMAT, THOUSANDS = "MM/dd/yyyy", "#,##0.00"
TEXT_FORMAT, SHORT_DATE = "@", "mm-dd-yy"  # the vendor information list's cells
CHART_FILE, UNITS_FILE, SUPPLIERS_FILE = "chart_of_accounts.xlsx", "business_units.xlsx", "supplier_master.xlsx"
JOURNAL_FILE, HEADERS_FILE = "transaction_journal.csv", "voucher_headers.xlsx"
VENDOR_INFO_FILE = "vendor_info.xlsx"
# Where the journal's fields are in a data row (0-based).
JOURNAL_POSITIONS = {"doc_type": 0, "company": 1, "gl_date": 2, "account": 3, "description": 7, "debit": 9,
                     "credit": 11, "remark": 13, "supplier_number": 15, "supplier_name": 16}  # fmt: skip
JOURNAL_CUTS = {"description": 24, "remark": 30, "supplier_name": 26}
JOURNAL_HEADER = (
    ["Document"] + [""] * 14 + ["Address"],
    ["Number", "Co".rjust(4), "G/L Date".rjust(9), "Account Number".rjust(37), "", "", "", "Description".rjust(26),
     "", "Debit Amount".rjust(18), "", "Credit Amount".rjust(18), "", "Remark".rjust(19), "", "Number".rjust(7),
     "Name".rjust(23)],
)  # fmt: skip
CHART_HEADER = [
    "Business Unit", "Object Account", "Subsidiary", "Account Description", "Company", "L D", "P E", "Currency Code",
    "Account ID", "M C", "Business Unit Description", "Taxable Account", "Tax Rate/Area",
    *(f"Cat Code {n:02d}" for n in range(1, 44)), *(f"BU Cat {n}" for n in range(1, 51)),
    "Adj Entry", "Adjustment Entry Description", "WCI Code", "Rt Cd",
]  # fmt: skip
UNIT_CATEGORIES = [{4: "District", 16: "Job Sts"}.get(n, f"Cat Code {n:02d}") for n in range(1, 51)]
UNITS_HEADER = [
    "Job Number", "Job Name", "Co", "Company Name", "LOD", "BU Ty", *UNIT_CATEGORIES, "Related BU",
    "Related Bu Description", "Address Number", *(f"Address Number{n}" for n in range(1, 7)), "Adj Entry",
    "Adjustment Entry Description",
]  # fmt: skip
SUPPLIERS_HEADER = ["Address Number", "Alpha Name", "Individual Tax ID", "Long Address", "Industry Class", "Sch Typ",
                    "C M", "Status", "Hold Payment Code", "Hold Orders Code"]  # fmt: skip
VENDOR_INFO_HEADER = [
    "Internal Supplier Number", "Company Name", "Complete Address", "Address Line 1", "Address Line 2",
    "Address Line 3", "Address Line 4", "City - CTY1", "State", "Country", "Postal Code", "Tax ID", "Cur Cod",
    "Check/ Itm Date", "JDE email address", "Total Payment Count", "Pmt Trm", "Payment Terms - A/P", "Business Unit",
    "Business Unit", "DUNS Number", "Sch Typ",
]  # fmt: skip
# Another company's suppliers, in the list beside the books' own: (name as stored, city, state, country, postal
# code, tax ID, currency). Invented, each for a shape such a list has.
OTHER_SUPPLIERS = (
    (" HARBOURVIEW MARINE SUPPLY LTD", "Halifax", "NS", "CA", "B3J 1P3", "70412 9930 RT0001", "CAD"),
    ("(DNU) OLD MILL STATIONERS", "Brandon", "MB", "CA", "R7A5Y1", "", "CAD"),
    ("(DO NOT USE)REDWATER TOWING", "Red Deer", "AB", "CA", "T4N 3T2", "R104482261", "CAD"),
    ("TAMARACK SIGNS - DO NOT USE", "Kenora", "ON", "CA", "P9N 1T1", "CHARITABLE", "CAD"),
    ("OKAFOR, DANIEL O/A PRAIRIE SHARPENING", "Estevan", "SK", "CA", "S4A 0W5", "714-20-3381", "CAD"),
    ("C/O NORTHGATE PROPERTY MGMT", "Thunder Bay", "ON", "CA", "P7B-5E1", "88104 2267 RC0001", "CAD"),
    ("WINDROW SEED CLEANING               00417", "Swift Current", "SK", "CA", "S9H 3W4", " 131904417", "CAD"),
    ("TOWN OF COPPER CREEK", "Copper Creek", "BC", "CA", "V0B 1G0", "GOVERNMENT", "CAD"),
    ("ST. BRENDAN'S FOOD BANK SOCIETY", "Corner Brook", "NL", "CA", "A2H 6J8", "CHARITABLE", "CAD"),
    ("CASCADE BEARING & DRIVE INC", "Spokane", "WA", "US", "99201-4410", "91-4402718", "USD"),
    ("LONE PINE CALIBRATION LLC", "Billings", "MT", "US", "59101", "#814402719", "USD"),
    ("KOORA FIELD SYSTEMS PTY LTD", "", "WA", "AU", "6000", "51 824 753 556", "AUD"),
    ("FENWICK INSTRUMENTS LTD", "Leeds", "", "GB", "LS1 4AP", "GB 318 4402 71", "CAD"),
    ("9042 1187 QUEBEC INC", "Sherbrooke", "QC", "CA", "J1H 5N4", "720481193RT0001", "CAD"),
    ("ATELIER  BEAUSÉJOUR", "Moncton", "NB", "CA", "E1C 4Z5", "N/A", "CAD"),
)  # fmt: skip
OTHER_UNIT = ("0031045990", "SHARED SERVICES 00310")  # the business unit the other company's suppliers are paid on
# (code, description) of the payment terms a supplier can have: a code with no description's is blank for some.
VENDOR_TERMS = (("N", "Net 30"), ("F", "Net 45"), ("T", "Net 15"), ("", "Due on receipt"), ("E", "End of next month"),
                ("", ""))  # fmt: skip
MAIL_DOMAINS = ("example.com", "example.org", "example.net")  # reserved for examples: no one's address
HEADERS_HEADER = [
    "Document Number", "Doc Type", "Document Type", "Doc Co", "Invoice Date", "G/L Date", "Due Date", "Gross Amount",
    "Open Amount", "Foreign Amount", "Foreign Open", "Pay Stat", "Pay Status Code", "Supplier Number",
    "Supplier Number Desc", "Invoice Number", "Co", "Batch Number", "Batch Type", "Batch Type Desc", "Batch Date",
    "Discount Available", "Foreign Disc Available", "Purchase Order", "PO Doc Type", "PO Document Type",
    "Doc Co (PO)", "Pay Item", "Pay Ext", "P C", "Curr Code", "No. Pymts", "Recur Freq", "Frequency - Recurring",
    "Adjust Doc Type", "Document Type- Adjusting", "Reg Code", "Business Unit", "G/L Offset", "Pay When Paid",
    "Pay When Paid Group Number", "Netting Transaction ID", "Netting Doc Number", "Netting Line ID",
    "Netting Status", "Netting Status Code", "Contract Code", "Block Code", "Harvest Period", "Harvest Suffix",
    "Contract ID", "Harvest ID", "User ID", "Program ID", "Date - Updated", "Time - Last Updated",
]  # fmt: skip
DISTRIBUTION_HEADER = [
    "Account Number", "Account Description", "Amount", "Explanation -Remark-", "Co", "Sub Type", "Sub- ledger",
    "Subledger Description", "LT", "B C", "P C", "Units", "UM", "PO Doc Co", "PO Doc Type", "Purchase Order",
    "PO Sfx", "Reference 2", "1099 Flag", "Asset ID", "Line Extension", "G/L Date", "R V", "JE Line Number",
    "Pay Itm", "Phase", "Short Item No", "Tax ExpCode", "TaxRate Area", "Tax Item No", "Track Taxes",
    "Alternate Home Business Unit", "Job Typ", "Job Step", "Employee Pool Grouping Code", "Job Pool Grouping Code",
]  # fmt: skip
PAY_ITEMS_HEADER = [
    "Pay Itm", "Gross Amount", "Pymt Terms", "Discount Available", "Remark", "Due Date", "Pay Status", "P C",
    "Open Amount", "G/L Bank Account Number", "Payee Number", "G/L Offset", "Tax Ex", "Tax Rate/Area", "Tax Amount",
    "Taxable Amount", "Non-Taxable Amount", "Non-Recoverable Tax Amount", "Amount To Distribute", "Adjust Doc Ty",
    "Recur Freq", "No. Pym", "Hdl Cod", "Cat Cd 07", "1099 Flag", "Pay Ext", "C/R I/R", "Closed Item",
    "G/L Credit Account ID", "Property Unit No.", "Domestic Gross Amount", "Domestic Discount Available",
    "Domestic Taxable Amount", "Domestic Tax", "Domestic Non-Taxable", "Foreign Gross Amount",
    "Foreign Discount Available", "Foreign Taxable Amount", "Foreign Tax", "Foreign Non-Taxable",
    "Netting Transaction ID", "Netting Doc Number", "Netting Line ID", "Netting Status",
]  # fmt: skip
PAYMENTS_HEADER = [
    "Doc Type", "Document Number", "Company", "Doc Pay Item", "Invoice Number", "Due Date", "Address Number",
    "Open Amount", "Payment Amount", "Discount Available", "Discount Taken", "Curr Code", "Remark", "Doc Type-M",
    "Exchange Rate", "Netting Transaction ID", "Netting Doc Number", "Netting Line ID", "Contract Code", "Block Code",
    "Harvest Period", "Harvest Suffix", "Contract ID", "Harvest ID",
]  # fmt: skip
PAY_STATUS = {"P": "Paid in Full", "A": "Approved for Payment", "H": "Held/Pending Approval"}
_WRITTEN = dt.datetime(2025, 1, 14, 9, 41, 7)  # every file's "written" time, so the same books give the same bytes


# --- cells ---------------------------------------------------------------------------------------


def _text(value: str) -> str:
    return value or BLANK


def _unit(number: str) -> str:
    return number.rjust(12)


def _padded(account: str) -> str:
    """An account string as the exports print it: its business unit right-justified to 12."""
    return " " * (12 - len(split_account(account)[0])) + account


def _number(amount: Decimal | None, always: bool = False) -> Any:
    """An amount's cell: a real number, shown with thousands separators from 1,000 (and on a totals row)."""
    if amount is None:
        return None
    return (float(amount), THOUSANDS) if always or abs(amount) >= 1_000 else float(amount)


def _date(day: dt.date) -> tuple[dt.datetime, str]:
    return dt.datetime(day.year, day.month, day.day), DATE_FORMAT


def _workbook(header: list[str], rows: Iterable[list[Any]]) -> bytes:
    """A grid's export: ``Sheet1``, the header in bold, then the rows. A cell is its value, or (value, number
    format); ``None`` is a truly empty cell."""
    book = Workbook(write_only=True)
    sheet = book.create_sheet("Sheet1")
    bold = Font(bold=True)
    titles = [WriteOnlyCell(sheet, value=name) for name in header]
    for cell in titles:
        cell.font = bold
    sheet.append(titles)
    for row in rows:
        cells = []
        for value in row:
            if isinstance(value, tuple):
                cell = WriteOnlyCell(sheet, value=value[0])
                cell.number_format = value[1]
                cells.append(cell)
            else:
                cells.append(value)
        sheet.append(cells)
    book.properties.creator = "Apache POI"  # what E1's export writes
    book.properties.created = _WRITTEN
    buffer = io.BytesIO()
    book.save(buffer)
    return _steady(buffer.getvalue())


def _steady(data: bytes) -> bytes:
    """The same bytes for the same content: a zip stamps each part with the time it was written, and the workbook's
    properties say when it was saved."""
    out, saved = io.BytesIO(), _WRITTEN.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "docProps/core.xml":
                content = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>" + saved, content)
            target.writestr(zipfile.ZipInfo(info.filename, (1980, 1, 1, 0, 0, 0)), content, zipfile.ZIP_DEFLATED)
    return out.getvalue()


# --- the masters ---------------------------------------------------------------------------------


def _unit_categories(truth: Truth, unit: BusinessUnit) -> list[str]:
    """A business unit's 50 category codes (the master's columns, repeated on each of its accounts in the chart)."""
    it = len(unit.number) == 8
    codes = {2: "HQ", 3: "ADM", 4: truth.company.district, 6: "ADM", 8: "0", 11: "SVC" if it else "COR", 14: "N",
             15: "NR", 16: unit.status, 17: "ADM", 26: "C1", 32: "ADM", 34: "INH", 36: "ADM"}  # fmt: skip
    if it:
        codes |= {27: "11.02", 28: "11.08", 29: f"11.0{10 + int(unit.number[-1])}", 30: "11.21"}
    return [codes.get(n, BLANK) for n in range(1, 51)]


def _chart_row(truth: Truth, account: Account, unit: BusinessUnit, unit_codes: list[str]) -> list[Any]:
    detail = account.level >= 7 and account.object[:1] == OVERHEAD_CLASS
    codes = [BLANK] * 43
    if detail:
        codes[2] = f"G{account.object[1]}"  # the overhead group it reports under
        codes[9] = "Y" if account.object[:2] == "77" else BLANK  # may be claimed on an expense report
    taxable = "0" if account.level >= 7 and int(account.account_id) % 4 else BLANK
    description = (" " * account.level + account.description)[:30]
    return [
        _unit(account.business_unit), _text(account.object), _text(account.subsidiary), description,
        truth.company.number, str(account.level), _text(account.posting_edit), BLANK, account.account_id, BLANK,
        unit.name, taxable, None, *codes, *unit_codes, "0", "Not Restricted", None, BLANK,
    ]  # fmt: skip


def chart_xlsx(truth: Truth) -> bytes:
    """The chart of accounts (the account master's export), 110 columns."""
    units = {unit.number: unit for unit in truth.business_units}
    codes = {number: _unit_categories(truth, unit) for number, unit in units.items()}
    rows = (_chart_row(truth, a, units[a.business_unit], codes[a.business_unit]) for a in truth.accounts)
    return _workbook(CHART_HEADER, rows)


def business_units_xlsx(truth: Truth) -> bytes:
    """The business-unit master's export, 67 columns."""
    names = {unit.number: unit.name for unit in truth.business_units}
    rows = [
        [_unit(unit.number), unit.name, truth.company.number, truth.company.name, unit.level, unit.kind,
         *_unit_categories(truth, unit), _unit(unit.related), names[unit.related], unit.address_number,
         *[None] * 6, "0", "Not Restricted"]
        for unit in truth.business_units
    ]  # fmt: skip
    return _workbook(UNITS_HEADER, rows)


def suppliers_xlsx(truth: Truth) -> bytes:
    """The supplier master's export, 10 columns, by address number."""
    rows = [
        [s.number, s.name, _text(s.tax_id), _text(s.long_address), s.industry_class.rjust(9) if s.industry_class
         else BLANK, "V", _text(s.credit_message), BLANK, s.hold_payment, BLANK]
        for s in truth.suppliers
    ]  # fmt: skip
    return _workbook(SUPPLIERS_HEADER, rows)


def _check_digit(digits: str) -> str:
    """The digit that makes ``digits`` and it pass the Luhn formula: a business number's ninth."""
    total = 0
    for n, ch in enumerate(reversed(digits)):
        doubled = int(ch) * (1 if n % 2 else 2)
        total += doubled - 9 if doubled > 9 else doubled
    return str(-total % 10)


def vendor_tax_id(supplier: Supplier) -> str:
    """The tax ID the vendor information list holds for a supplier of the books, in the shapes such a list has: a
    business number with its program account for most that charge the GST (with spaces for some, the nine digits
    alone or another account of the same business for others), an EIN for a foreign one, a personal number for some
    people, a word or nothing for the rest. Invented from the supplier's number; a business number passes its check
    digit."""
    rng = random.Random(f"tax-id-{supplier.number}")
    eight = "7" + "".join(rng.choice("0123456789") for _ in range(7))
    number = eight + _check_digit(eight)
    if supplier.currency != "CAD":
        return f"{number[:2]}-{number[2:]}"
    if supplier.kind == "employee":
        return f"{number[:3]}-{number[3:5]}-{number[5:]}" if supplier.number % 3 == 0 else ""
    if supplier.kind == "remittance":
        return "GOVERNMENT"
    if not supplier.taxed:
        return ("", "N/A", number)[supplier.number % 3]
    shapes = (f"{number}RT0001", f"{number}RT0001", f"{number[:5]} {number[5:]} RT0001", number,
              f"{number} RT 0001", f"{number}RT0002")  # fmt: skip
    return shapes[rng.randrange(len(shapes))]


def _vendor_row(truth: Truth, rng: random.Random, number: int, name: str, lines: list[str], place: tuple[str, ...],
                tax_id: str, currency: str, unit: tuple[str, str]) -> list[Any]:  # fmt: skip
    """One row of the vendor information list; ``place``: (city, state, country, postal code)."""
    city, state, country, postal = place
    lines = (lines + [""] * 4)[:4]
    where = f"{city}, {state} {postal}".strip() if country else ",  "
    complete = "\n".join([name.strip(), *[text for text in lines if text], where, country, ""])
    mail = re.sub(r"[^a-z]+", "", name.lower())[:14] or "accounts"
    email = ("", f"ap@{mail}.{rng.choice(MAIL_DOMAINS)}", f"AR@{mail.upper()}.{rng.choice(MAIL_DOMAINS).upper()}",
             f"{mail} billing <billing@{mail}.{rng.choice(MAIL_DOMAINS)}>")[rng.randrange(4)]  # fmt: skip
    code, terms = VENDOR_TERMS[rng.randrange(len(VENDOR_TERMS))]
    paid = dt.datetime(truth.company.year, rng.randint(1, 12), rng.randint(1, 28))
    duns = "" if rng.randrange(3) else str(rng.randrange(10**8, 10**9))

    def text(*values: str) -> list[Any]:
        return [(value, TEXT_FORMAT) if value else None for value in values]

    return [number, *text(name, complete, *lines, city, state, country, postal, tax_id, currency),
            (paid, SHORT_DATE), *text(email), rng.choice((1, 2, 3, 7, 12, 31, 118, 1204)),
            *text(code, terms, *unit, duns, "V")]  # fmt: skip


def _vendor_rows(truth: Truth) -> list[list[Any]]:
    """The vendor information list's rows before they are sorted: the books' suppliers under their names as the
    supplier master has them (one in eleven stored with a space before it), then the other company's."""
    rows: list[list[Any]] = []
    units = [unit for unit in truth.business_units if unit.status == "ACT"] or truth.business_units
    cities = {"AB": "Edmonton", "BC": "Kamloops", "QC": "Trois-Rivieres", "WA": "Yakima", "MT": "Helena",
              "MN": "Duluth"}  # fmt: skip
    for n, s in enumerate(truth.suppliers):
        rng = random.Random(f"vendor-info-{s.number}")
        foreign = s.currency != "CAD"
        state = rng.choice(("WA", "MT", "MN")) if foreign else {"0.07": "BC", "0.09975": "QC"}.get(
            str(s.provincial_rate), "AB")  # fmt: skip
        postal = str(rng.randrange(10000, 99999)) if foreign else (
            f"{'TVG'[('AB', 'BC', 'QC').index(state)]}{rng.randrange(1, 9)}{rng.choice('ABCEGHJ')} "
            f"{rng.randrange(1, 9)}{rng.choice('KLMNPRS')}{rng.randrange(1, 9)}")  # fmt: skip
        street = f"{rng.randrange(100, 9900)} {rng.choice(('Alder St', 'Quarry Rd', '104 St NW', 'Ferry Rd'))}"
        lines = ([street], [f"PO BOX {rng.randrange(100, 999)}", street], ["", street], [])[rng.randrange(4)]
        unit = units[n % len(units)]
        name = f" {s.name}"[:40] if n % 11 == 3 else s.name
        rows.append(_vendor_row(truth, rng, s.number, name, lines, (cities[state], state, "US" if foreign else "CA",
                                                                    postal), vendor_tax_id(s), s.currency,
                                (unit.number, unit.name)))  # fmt: skip
    for n, (name, city, state, country, postal, tax_id, currency) in enumerate(OTHER_SUPPLIERS):
        rng = random.Random(f"vendor-info-other-{n}")
        lines = [f"{rng.randrange(10, 990)} {rng.choice(('Harbour Rd', 'Mill Lane', 'Station St'))}"] if n % 4 else []
        rows.append(_vendor_row(truth, rng, 900_001 + 7 * n, name, lines, (city, state, country, postal), tax_id,
                                currency, OTHER_UNIT))  # fmt: skip
    return rows


def vendor_info_xlsx(truth: Truth) -> bytes:
    """The vendor information list, 22 columns, by name as stored, a totals row last (only its payment count)."""
    rows = sorted(_vendor_rows(truth), key=lambda row: row[1][0])
    count = VENDOR_INFO_HEADER.index("Total Payment Count")
    totals: list[Any] = [None] * len(VENDOR_INFO_HEADER)
    totals[count] = sum(row[count] for row in rows)
    return _workbook(VENDOR_INFO_HEADER, [*rows, totals])


# --- the journal ---------------------------------------------------------------------------------


def _amount(amount: Decimal) -> str:
    """An amount as the report prints it: two decimals, no thousands separator, no zero before the point."""
    text = f"{amount:.2f}"
    return text[1:] if text.startswith("0.") else text


def _sides(amount: Decimal, both: bool = False) -> tuple[Decimal | None, Decimal | None]:
    """(debit, credit) of a signed amount; ``both``: the same amount on each side (a voucher that does not balance)."""
    if both:
        return abs(amount), abs(amount)
    return (amount, None) if amount > 0 else (None, -amount)


def _journal_lines(truth: Truth, voucher: Voucher) -> list[tuple]:
    """A voucher's lines in the journal: (account, description, debit, credit, remark, with the supplier or not).
    The coding lines, then for each pay item JDE's payable line and, when taxed, its GST line. A voucher that does
    not balance prints its coding lines as credits and the same amount on both sides of JDE's own lines."""
    company, odd = truth.company, voucher.oddity == "unbalanced"
    lines = []
    for line in voucher.lines:
        debit, credit = _sides(-line.amount if odd else line.amount)
        description = truth.account(line.account).description
        lines.append((line.account, description, debit, credit, line.remark, voucher.oddity != "no_address"))
    remark = voucher.lines[-1].remark
    payable = truth.account(join_account(company.main_business_unit, voucher.payable_object))
    clearing = truth.account(join_account(company.main_business_unit, company.gst_object))
    for item in voucher.pay_items:
        lines.append((payable.string, payable.description, *_sides(-item.gross, odd), remark, True))
        if item.tax:
            lines.append((clearing.string, clearing.description, *_sides(item.tax, odd), remark, True))
    return lines


def _trimmed(row: list[str]) -> list[str]:
    """A printed row ends at its last filled cell."""
    while row and not row[-1]:
        row = row[:-1]
    return row


def journal_rows(truth: Truth) -> list[list[str]]:
    """The Transaction Journal's rows, as the CSV holds them (titles, header, vouchers by G/L date, totals)."""
    company, at, cut = truth.company, JOURNAL_POSITIONS, JOURNAL_CUTS
    rows = [
        ["R09321"] + [""] * 7 + [company.group] + [""] * 9 + [f"1/14/{company.year + 1}", "09:41:07"],
        [""] * 8 + ["Transaction Journal"] + [""] * 9 + ["Page -", "1"],
        ["Company", "", company.name],
        ["Tax ID", "", company.tax_id],
        ["From", "", "", "Thru"],
        *JOURNAL_HEADER,
    ]
    debits = credits = Decimal(0)
    for voucher in truth.vouchers:
        supplier = truth.supplier(voucher.supplier)
        day = voucher.gl_date
        first = ["PV", company.number, f"{day.month}/{day.day}/{day.year}"]
        for n, (account, description, debit, credit, remark, named) in enumerate(_journal_lines(truth, voucher)):
            row = (first if n == 0 else ["", "", ""]) + [""] * 14
            row[at["account"]] = _padded(account)
            row[at["description"]] = description[: cut["description"]]
            row[at["debit"]] = _amount(debit) if debit is not None else ""
            row[at["credit"]] = _amount(credit) if credit is not None else ""
            row[at["remark"]] = remark[: cut["remark"]]
            if named:
                row[at["supplier_number"]] = str(supplier.number)
                row[at["supplier_name"]] = supplier.name[: cut["supplier_name"]]
            debits, credits = debits + (debit or 0), credits + (credit or 0)
            rows.append(_trimmed(row))
    totals = [_amount(debits), "", _amount(credits)]
    rows.append([""] * 9 + totals + [""] * 71 + [company.name])
    rows.append([""] * 7 + ["Grand Total", ""] + totals)
    return rows


def journal_csv(truth: Truth) -> bytes:
    """Report R09321 "Transaction Journal" (document type PV) saved as CSV: UTF-8 with a BOM, CRLF."""
    text = io.StringIO(newline="")
    csv.writer(text, lineterminator="\r\n").writerows(journal_rows(truth))
    return text.getvalue().encode("utf-8-sig")


# --- vouchers ------------------------------------------------------------------------------------


def voucher_headers_xlsx(truth: Truth, month: int | None = None) -> bytes:
    """The supplier ledger inquiry grid: one row per pay item, 56 columns, then a totals row with only the gross
    and open amounts. ``month``: only the vouchers with a G/L date in it (the screen is usually asked for one)."""
    company, rows = truth.company, []
    gross = open_ = Decimal(0)
    for voucher in truth.vouchers:
        if month and voucher.gl_date.month != month:
            continue
        supplier, order = truth.supplier(voucher.supplier), voucher.purchase_order
        offset = voucher.payable_object if voucher.payable_object == company.intercompany_object else ""
        clock = voucher.updated
        for item in voucher.pay_items:
            unpaid = item.gross if item.status != "P" else None
            gross, open_ = gross + item.gross, open_ + (unpaid or 0)
            rows.append([
                voucher.document, "PV", "Voucher", company.number, _date(voucher.invoice_date),
                _date(voucher.gl_date), _date(item.due_date), _number(item.gross), _number(unpaid),
                _number(item.foreign_gross), _number(item.foreign_gross if unpaid else None), item.status,
                PAY_STATUS[item.status], supplier.number, supplier.name[:30], voucher.invoice_number, company.number,
                voucher.batch, "V", "Voucher Entry", _date(voucher.batch_date), None, None, _text(order),
                "OP" if order else BLANK, "Purchase Order" if order else None, company.number if order else BLANK,
                item.number, None, "D", voucher.currency, None, BLANK, None, BLANK, None, "M" if order else BLANK,
                _unit(voucher.business_unit), _text(offset), BLANK, None, 0, None, None, "0",
                "Ineligible for Netting", BLANK, BLANK, BLANK, BLANK, None, 0, voucher.user,
                "EP0413M" if item.status == "P" else "R09801", _date(clock.date()),
                clock.hour * 10000 + clock.minute * 100 + clock.second,
            ])  # fmt: skip
    totals: list[Any] = [None] * len(HEADERS_HEADER)
    totals[7], totals[8] = _number(gross, always=True), _number(open_, always=True)
    return _workbook(HEADERS_HEADER, [*rows, totals])


def gl_distribution_xlsx(truth: Truth, voucher: Voucher) -> bytes:
    """A voucher's G/L distribution grid, 36 columns: its coding lines (net amounts; the purchase order and the tax
    fields only when it was matched to an order), then the blank entry row, which has only the G/L date (and the
    order)."""
    company, order, day = truth.company, voucher.purchase_order, _date(voucher.gl_date)
    matched = [company.number, "OP", order] if order else [BLANK, BLANK, BLANK]
    taxed = bool(order and voucher.tax)
    rows = [
        [_padded(line.account), truth.account(line.account).description, _number(line.amount), _text(line.remark),
         company.number, BLANK, BLANK, None, "AA", BLANK, "P", 1 if order else None, BLANK, *matched,
         "000" if order else BLANK, BLANK, BLANK, None, BLANK, day, BLANK, n, "001" if order else BLANK, BLANK, None,
         "V" if taxed else BLANK, company.tax_area if taxed else BLANK, 0, "1" if taxed else "0", " " * 12, BLANK,
         BLANK, BLANK, BLANK]
        for n, line in enumerate(voucher.lines, start=1)
    ]  # fmt: skip
    entry = [None] * 13 + [*matched, BLANK] + [None] * 4 + [day] + [None] * 8 + [BLANK] + [None] * 5
    return _workbook(DISTRIBUTION_HEADER, [*rows, entry])


def pay_items_xlsx(truth: Truth, voucher: Voucher) -> bytes:
    """A voucher's pay-item grid, 44 columns: where AP enters the tax (tax explanation code, tax area, taxable and
    tax amounts), with the bank account and the terms; then the empty next pay item."""
    company, rows = truth.company, []
    offset = voucher.payable_object if voucher.payable_object == company.intercompany_object else ""
    for item in voucher.pay_items:
        foreign = item.foreign_gross
        tax, taxable = (_number(item.tax), _number(item.taxable)) if item.tax else (None, None)
        rows.append([
            item.number, _number(foreign or item.gross), company.payment_terms, None, _text(voucher.lines[-1].remark),
            _date(item.due_date), item.status, "D", _number(None if item.status == "P" else foreign or item.gross),
            _padded(company.bank_account), voucher.supplier, _text(offset), _text(item.tax_code),
            _text(item.tax_area), tax, taxable, None, None, _number(foreign or item.taxable), BLANK, BLANK, None,
            BLANK, BLANK, BLANK, None, BLANK, BLANK, BLANK, BLANK, _number(item.gross), None, taxable, tax, None,
            _number(foreign), None, _number(foreign), None, None, 0, None, None, "0",
        ])  # fmt: skip
    following = [f"{len(voucher.pay_items) + 1:03d}", None, None, None, BLANK] + [None] * 39
    return _workbook(PAY_ITEMS_HEADER, [*rows, following])


def payment_history_xlsx(truth: Truth, voucher: Voucher) -> bytes:
    """A voucher's payment-history grid, 24 columns: one row per pay item that was paid."""
    rate = float(voucher.exchange_rate) if voucher.exchange_rate else None
    rows = [
        ["PV", voucher.document, truth.company.number, item.number, voucher.invoice_number, _date(item.due_date),
         voucher.supplier, None, _number(item.foreign_gross or item.gross), None, None, voucher.currency,
         _text(voucher.lines[-1].remark), "PT", rate, None, None, None, BLANK, BLANK, BLANK, BLANK, None, 0]
        for item in voucher.pay_items if item.status == "P"
    ]  # fmt: skip
    return _workbook(PAYMENTS_HEADER, rows)


def example_vouchers(truth: Truth) -> dict[str, Voucher]:
    """The vouchers whose three grids ``write_all`` writes, all paid and with GST: one split across business units,
    one of a single line with GST only, one matched to a purchase order (with PST or QST in the expense when there
    is one)."""
    plain = [v for v in truth.vouchers if not v.oddity and v.currency == "CAD" and v.pay_items[0].status == "P"]
    wanted = (
        ("split", lambda v: len(v.business_units) > 1 and len(v.lines) <= 6 and v.tax),
        ("gst_only", lambda v: len(v.lines) == 1 and v.tax and not v.provincial_tax and not v.purchase_order),
        ("purchase_order", lambda v: v.purchase_order and v.tax and v.provincial_tax),
        ("purchase_order", lambda v: v.purchase_order and v.tax),
    )
    found: dict[str, Voucher] = {}
    for label, fits in wanted:
        match = next((v for v in plain if fits(v)), None)
        if match and label not in found:
            found[label] = match
    return found


def write_all(folder: str | Path, seed: int = 1, size: str = "small", year: int = 2024) -> Truth:
    """Every export of the made-up company in ``folder`` (made if missing): the chart, the business units, the
    suppliers, the vendor information list, the journal, the voucher headers, and the three grids of each example
    voucher (``voucher_<which>_gl_distribution.xlsx``, ``..._pay_items.xlsx``, ``..._payment_history.xlsx``). Returns
    the truth the files were written from."""
    truth = build(seed, size, year)
    files = {CHART_FILE: chart_xlsx(truth), UNITS_FILE: business_units_xlsx(truth),
             SUPPLIERS_FILE: suppliers_xlsx(truth), VENDOR_INFO_FILE: vendor_info_xlsx(truth),
             JOURNAL_FILE: journal_csv(truth), HEADERS_FILE: voucher_headers_xlsx(truth)}  # fmt: skip
    for label, voucher in example_vouchers(truth).items():
        files[f"voucher_{label}_gl_distribution.xlsx"] = gl_distribution_xlsx(truth, voucher)
        files[f"voucher_{label}_pay_items.xlsx"] = pay_items_xlsx(truth, voucher)
        files[f"voucher_{label}_payment_history.xlsx"] = payment_history_xlsx(truth, voucher)
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out / name).write_bytes(data)
    return truth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a made-up company's JD Edwards exports into a folder.")
    parser.add_argument("out", help="the folder to write into")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--size", choices=("small", "year"), default="small")
    args = parser.parse_args(argv)
    truth = write_all(args.out, args.seed, args.size)
    print(f"{len(truth.accounts)} accounts, {len(truth.business_units)} business units, {len(truth.suppliers)} "
          f"suppliers, {len(truth.vouchers)} vouchers written to {args.out}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
