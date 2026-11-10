"""A made-up chart of accounts and business unit master, written as JD Edwards exports them (D-209): one sheet, the
column names in row 1, 110 and 67 columns, every cell text, an empty field a lone space, a business unit padded to
12 characters, a description indented by its level of detail and cut at 30 characters. Every value is invented here.

``CHART`` and ``UNITS`` are the rows; ``write_chart`` / ``write_business_units`` make the files with openpyxl;
``write_big_chart`` makes one of the real export's size, its cells inline as the export writes them.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import openpyxl

# The columns the import reads (JD Edwards' own names), then filler up to the export's width.
CHART_HEADERS = ["Business Unit", "Object Account", "Subsidiary", "Account Description", "Company", "L D", "P E",
                 *(f"Filler {n:03d}" for n in range(1, 104))]  # fmt: skip
BU_HEADERS = ["Job Number", "Job Name", "Co", "Filler 001", "LOD", "BU Ty", *(f"Filler {n:03d}" for n in range(2, 17)),
              "Job Sts", *(f"Filler {n:03d}" for n in range(17, 62))]  # fmt: skip
HOME, OTHER, CLOSED, NOT_USED, UNLISTED = "0731066120", "0731066135", "0731066199", "0731066150", "0731066177"

# (business unit, object, subsidiary, description, level of detail, posting edit code), in the export's order.
CHART = [
    ("07310", "", "", "Head Office Balance Sheet", 3, ""),
    ("07310", "0042", "", "Objects under 1000 not GL", 5, "N"),
    ("07310", "2100", "", "Amounts Owed", 5, "N"),
    ("07310", "2140", "", "Trade Creditors", 6, ""),
    ("07310", "2140", "QC", "Payable Trade Quebec", 7, ""),
    ("07310", "2165", "60017734", "Deposits Held Lot", 7, ""),
    ("07310", "2190", "7QXR3", "Clearing Account", 7, ""),
    ("07310", "2310", "", "GST Paid on Purchases", 6, ""),
    ("07310K", "", "", "Head Office Annex", 3, "N"),
    ("07310K", "5410", "", "Stationery and Paper", 6, ""),
    ("0550421", "", "", "Fleet Vehicles", 3, "N"),
    ("0550421", "5600", "", "Vehicle Costs", 5, "N"),
    ("0550421", "5610", "", "Fuel", 6, ""),
    ("0550421", "5620", "", "Repairs by the Shop", 6, "M"),
    ("05504218", "", "", "Computer Equipment", 3, "N"),
    ("05504218", "5700", "", "Computer Software", 5, "N"),
    ("05504218", "5710", "", "Software Subscriptions", 6, ""),
    ("05504218", "5710", "3120", "Accounting Package Licences", 7, ""),
    ("05504218", "5720", "", "Software Budget", 6, "B"),
    (HOME, "", "", "Head Office General", 3, "N"),
    (HOME, "5400", "", "Office and General Costs", 5, "N"),
    (HOME, "5410", "", "Stationery and Paper", 6, ""),
    (HOME, "5410", "2210", "Coffee and Refreshments", 7, ""),
    (HOME, "5410", "7701", "Recoveries", 7, ""),
    (HOME, "5430", "", "Courier", 6, "I"),
    (HOME, "5440", "", "Travel by Employee", 6, "L"),
    (HOME, "5700", "", "Computer Software", 5, "N"),
    (HOME, "5710", "", "Software Subscriptions", 6, ""),
    (OTHER, "", "", "People and Culture", 3, "N"),
    (OTHER, "5400", "", "Office and General Costs", 5, "N"),
    (OTHER, "5410", "", "Stationery and Paper", 6, ""),
    (OTHER, "5450", "", "Training and Seminars", 6, ""),
    (NOT_USED, "", "", "Old Annex", 3, "N"),
    (NOT_USED, "5400", "", "Office and General Costs", 5, "N"),
    (NOT_USED, "5410", "", "Stationery and Paper", 6, ""),
    (UNLISTED, "", "", "Facilities", 3, "N"),
    (UNLISTED, "5470", "", "Utilities", 6, ""),
    (CLOSED, "", "", "Closed Branch", 3, "N"),
    (CLOSED, "5400", "", "Office and General Costs", 5, "N"),
    (CLOSED, "5410", "", "Stationery and Paper", 6, ""),
    (CLOSED, "5460", "", "Rent", 6, ""),
]
# (business unit, name, company, type, status): UNLISTED is not here, and 0731066188 has no account in the chart.
UNITS = [
    ("07310", "Head Office Balance Sheet", "07310", "BS", "ACT"),
    ("07310K", "Head Office Annex", "07310", "AD", "ACT"),
    ("0550421", "Fleet Vehicles", "07310", "EQ", "ACT"),
    ("05504218", "Computer Equipment", "07310", "IT", "ACT"),
    (HOME, "Head Office General", "07310", "AD", "ACT"),
    (OTHER, "People and Culture", "07310", "AD", "ACT"),
    (NOT_USED, "****DO NOT USE**** Old Annex", "07310", "AD", "ACT"),
    ("0731066188", "New Department", "07310", "AD", "ACT"),
    (CLOSED, "Closed Branch", "07310", "AD", "INA"),
]
# What the chart and the business units above give: the GL accounts {code: (description, category)} and the cost
# centers {business unit: name}.
GL_ACCOUNTS = {
    "2140": ("Trade Creditors", "Amounts Owed"),
    "2140.QC": ("Payable Trade Quebec", "Amounts Owed"),
    "2165.60017734": ("Deposits Held Lot", "Amounts Owed"),
    "2190.7QXR3": ("Clearing Account", "Amounts Owed"),
    "2310": ("GST Paid on Purchases", "Amounts Owed"),
    "5410": ("Stationery and Paper", "Office and General Costs"),
    "5410.2210": ("Coffee and Refreshments", "Office and General Costs"),
    "5410.7701": ("Recoveries", "Office and General Costs"),
    "5450": ("Training and Seminars", "Office and General Costs"),
    "5470": ("Utilities", ""),
    "5610": ("Fuel", "Vehicle Costs"),
    "5710": ("Software Subscriptions", "Computer Software"),
    "5710.3120": ("Accounting Package Lice", "Computer Software"),
}
COST_CENTERS = {
    "0550421": "Fleet Vehicles",
    "05504218": "Computer Equipment",
    "07310": "Head Office Balance Sheet",
    "07310K": "Head Office Annex",
    HOME: "Head Office General",
    OTHER: "People and Culture",
    NOT_USED: "****DO NOT USE**** Old Annex",
    UNLISTED: "Facilities",
}
BLANK = " "  # an empty field, as the export stores it


def padded(business_unit: str) -> str:
    """A business unit as the export writes it: right-justified to 12 characters."""
    return business_unit.rjust(12)


def indented(description: str, lod: int) -> str:
    """A description as the chart's export writes it: indented by its level, cut at 30 characters in all."""
    return (" " * lod + description)[:30]


def chart_cells(rows: list[tuple] = CHART) -> list[list[str]]:
    """The chart's rows as the export's cells: 110 a row, all text, a lone space where a field is empty."""
    return [[padded(bu), obj or BLANK, sub or BLANK, indented(text, lod), "07310", str(lod), edit or BLANK,
             *[BLANK] * (len(CHART_HEADERS) - 7)] for bu, obj, sub, text, lod, edit in rows]  # fmt: skip


def bu_cells(rows: list[tuple] = UNITS) -> list[list[str]]:
    """The business unit master's rows as the export's cells: 67 a row."""
    cells = []
    for code, name, company, kind, status in rows:
        row = [BLANK] * len(BU_HEADERS)
        row[0], row[1], row[2], row[4], row[5] = padded(code), name, company, "9", kind
        row[BU_HEADERS.index("Job Sts")] = status
        cells.append(row)
    return cells


def _write(path: Path, headers: list[str], rows: list[list[str]]) -> Path:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def write_chart(path: Path, rows: list[tuple] = CHART, without: tuple[str, ...] = ()) -> Path:
    """``without``: the columns the grid does not show (JD Edwards exports the visible ones only)."""
    shown = [n for n, header in enumerate(CHART_HEADERS) if header not in without]
    return _write(path, [CHART_HEADERS[n] for n in shown], [[row[n] for n in shown] for row in chart_cells(rows)])


def write_business_units(path: Path, rows: list[tuple] = UNITS) -> Path:
    return _write(path, BU_HEADERS, bu_cells(rows))


def big_chart_rows(business_units: int = 134, accounts: int = 110) -> list[tuple]:
    """A chart of the real export's size: ``business_units`` x ``accounts`` rows, a header and a title account in
    each business unit."""
    rows = []
    for b in range(business_units):
        bu = f"07310{66000 + b:05d}"
        rows.append((bu, "", "", f"Department {b}", 3, "N"))
        rows.append((bu, "5000", "", "Operating Expenses", 5, "N"))
        for a in range(accounts - 2):
            rows.append((bu, f"{5001 + a // 4}", f"{1000 + a % 4}" if a % 4 else "", f"Expense Account {a}", 7, ""))
    return rows


def _column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def write_big_chart(path: Path, rows: list[tuple]) -> Path:
    """The chart written as JD Edwards writes it: every cell an inline text (no shared texts), about 7 KB a row, so
    14,700 rows unzip to about 100 MB: more than any other workbook AP Coder opens may."""
    return write_inline(path, [CHART_HEADERS, *chart_cells(rows)])


def write_inline(path: Path, table: list[list[str]], dimension: str = "", cut: int = 0, sheets: bool = True) -> Path:
    """A workbook of one sheet, every cell an inline text, its first row first. ``dimension``: the size the sheet
    says it has (its real one when not given); ``cut``: the sheet's XML stops that many bytes short of its end;
    ``sheets`` False: the workbook lists no sheet at all."""
    names = [_column(i) for i in range(max(len(row) for row in table))]

    def row_xml(number: int, cells: list[str]) -> str:
        text = '<c r="{}{}" t="inlineStr"><is><t xml:space="preserve">{}</t></is></c>'
        made = "".join(text.format(names[i], number, escape(cell)) for i, cell in enumerate(cells))
        return f'<row r="{number}">{made}</row>'

    last = dimension or f"A1:{names[-1]}{len(table)}"
    listed = '<sheet name="Sheet1" sheetId="1" r:id="rId1"/>' if sheets else ""
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rels = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package = "http://schemas.openxmlformats.org/package/2006"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr("[Content_Types].xml", f'<Types xmlns="{package}/content-types"><Default Extension="rels" '
                   'ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" '
                   'ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/'
                   'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/'
                   'worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.'
                   'worksheet+xml"/></Types>')  # fmt: skip
        z.writestr("_rels/.rels", f'<Relationships xmlns="{package}/relationships"><Relationship Id="rId1" '
                   f'Type="{rels}/officeDocument" Target="xl/workbook.xml"/></Relationships>')  # fmt: skip
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{main}" xmlns:r="{rels}"><sheets>{listed}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="{package}/relationships"><Relationship '
                   f'Id="rId1" Type="{rels}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')  # fmt: skip
        with z.open("xl/worksheets/sheet1.xml", "w", force_zip64=True) as sheet:
            sheet.write(f'<worksheet xmlns="{main}"><dimension ref="{last}"/><sheetData>'.encode())
            for number, cells in enumerate(table[:-1], 1):
                sheet.write(row_xml(number, cells).encode())
            end = (row_xml(len(table), table[-1]) + "</sheetData></worksheet>").encode()
            sheet.write(end[: len(end) - cut])
    return path
