"""The chart of accounts and the business unit master read from JD Edwards' exports as they come (D-209): 110 and 67
columns, every cell text, and a chart of the real size, which no other reader of a workbook opens."""

from __future__ import annotations

import io
import time
import zipfile

import openpyxl
import pytest

from ap_coder.core import chart, chart_file
from ap_coder.intake import triage
from tests import chart_files as cf


def test_the_chart_is_read_from_the_export_as_it_comes(tmp_path):
    path = cf.write_chart(tmp_path / "chart.xlsx")
    sheet = openpyxl.load_workbook(path).worksheets[0]
    assert (sheet.title, sheet.max_column) == ("Sheet1", 110)
    assert sheet["A2"].value == "       07310" and sheet["B2"].value == " " and sheet["D5"].value[:6] == "      "
    read = chart_file.read_chart(path)
    assert read.skipped == {}
    assert [(r.bu, r.object, r.subsidiary, r.description, r.lod, r.posting_edit) for r in read.rows] == [
        (bu, obj, sub, cf.indented(text, lod).strip(), lod, edit) for bu, obj, sub, text, lod, edit in cf.CHART
    ]
    units = chart_file.read_business_units(cf.write_business_units(tmp_path / "bu.xlsx")).rows
    assert chart.account_lists(read.rows, units) == (cf.GL_ACCOUNTS, cf.COST_CENTERS)


def test_the_business_units_are_read_from_the_export_as_it_comes(tmp_path):
    path = cf.write_business_units(tmp_path / "bu.xlsx")
    assert openpyxl.load_workbook(path).worksheets[0].max_column == 67
    read = chart_file.read_business_units(path)
    assert [(u.code, u.name, u.company, u.type, u.status) for u in read.rows] == cf.UNITS and read.skipped == {}
    assert chart_file.read_business_units(path.read_bytes()).rows == read.rows  # an upload's bytes too


def test_only_the_columns_the_import_needs_are_kept_a_row_at_a_time(tmp_path):
    reading = chart_file.read_export(cf.write_chart(tmp_path / "chart.xlsx"), chart.chart_columns)
    assert iter(reading) is reading  # a row at a time: the sheet is never held whole
    records = list(reading)
    assert len(records) == len(cf.CHART)
    assert list(records[0]) == ["Business Unit", "Object Account", "Subsidiary", "Account Description", "Company",
                                "L D", "P E"]  # fmt: skip
    assert records[3]["Business Unit"] == "       07310" and records[0]["Object Account"] == " "  # cleaned later


def _workbook(path, *rows):
    workbook = openpyxl.Workbook()
    for row in rows:
        workbook.active.append(row)
    workbook.save(path)
    return path


HEADERS = ["Business Unit", "Object Account", "Subsidiary", "Account Description", "L D", "P E"]


def test_a_level_stored_as_a_number_and_a_short_row_are_read(tmp_path):
    path = _workbook(tmp_path / "n.xlsx", HEADERS, ["  0731066120", "5410", "2210", "       Coffee", 7.0, None],
                     ["  0731066120", "5430"])  # fmt: skip
    assert chart_file.read_chart(path).rows == [
        chart.ChartRow(cf.HOME, "5410", "2210", "Coffee", 7, ""), chart.ChartRow(cf.HOME, "5430")]  # fmt: skip


def test_a_code_stored_as_a_number_is_refused_and_the_file_named(tmp_path):
    """A file opened and saved in between: 0731066120 became the number 731066120 (B-2126)."""
    path = _workbook(tmp_path / "tidied.xlsx", HEADERS, [731066120, 5410, 2210.0, "Coffee", 7, None],
                     ["  0731066120", "5430"])  # fmt: skip
    with pytest.raises(chart.ChartError) as refused:
        chart_file.read_chart(path)
    said = str(refused.value)
    assert said.startswith("tidied.xlsx: 1 row of this file holds") and "row 2 is the first" in said
    path = _workbook(tmp_path / "units.xlsx", ["Job Number", "Job Name"], [731066120, "Head Office"])
    with pytest.raises(chart.ChartError, match=r"^units\.xlsx: 1 row .*stored as a number"):
        chart_file.read_business_units(path)


def test_the_rows_are_read_whatever_size_the_sheet_says_it_has(tmp_path):
    table = [cf.CHART_HEADERS, *cf.chart_cells()]
    path = cf.write_inline(tmp_path / "chart.xlsx", table, dimension="A1")
    assert len(chart_file.read_chart(path).rows) == len(cf.CHART)
    assert chart_file.read_chart(path).rows == chart_file.read_chart(cf.write_chart(tmp_path / "same.xlsx")).rows


def _word_file(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')  # fmt: skip
        z.writestr("word/document.xml", "<document/>")
    return path


@pytest.mark.parametrize("kind", ["a Word file renamed", "a sheet cut short", "no sheet"])
def test_a_workbook_that_cannot_be_read_is_refused_in_plain_words_not_with_a_traceback(kind, tmp_path):
    table = [cf.CHART_HEADERS, *cf.chart_cells()]
    path = tmp_path / "chart.xlsx"
    if kind == "a Word file renamed":
        _word_file(path)
    else:
        cf.write_inline(path, table, cut=40 if kind == "a sheet cut short" else 0, sheets=kind != "no sheet")
    with pytest.raises(chart.ChartError) as refused:
        chart_file.read_chart(path)
    said = str(refused.value)
    assert said.startswith("chart.xlsx could not be read as an Excel workbook") and "save it again as .xlsx" in said


def test_a_protected_or_older_excel_file_says_what_to_do(tmp_path):
    (tmp_path / "chart.xlsx").write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 600)
    with pytest.raises(chart.ChartError) as refused:
        chart_file.read_chart(tmp_path / "chart.xlsx")
    said = str(refused.value)
    assert "protected with a password" in said and ".xls" in said and "save it as .xlsx" in said


def test_title_rows_above_the_column_names_are_no_rows_to_delete(tmp_path):
    """A grid exported with its title above the column names was refused ("Delete the rows above them"), though the
    supplier and voucher lists load so: the column names are found within the first rows (B-2273), and a row is
    still named by its number in the file."""
    titles = (["Account master"], [None], ["Printed 2026-11-10"])
    path = _workbook(tmp_path / "titled.xlsx", *titles, HEADERS, ["  0731066120", "5410", " ", "Coffee", "7", " "])
    assert [(r.bu, r.object, r.description) for r in chart_file.read_chart(path).rows] == [
        ("0731066120", "5410", "Coffee")
    ]
    units = _workbook(tmp_path / "bu.xlsx", ["Business units"], ["Job Number", "Job Name"], ["  0731066120", "Hull"])
    assert [(u.code, u.name) for u in chart_file.read_business_units(units).rows] == [("0731066120", "Hull")]
    numbers = _workbook(tmp_path / "numbers.xlsx", *titles, HEADERS, ["  0731066120", "5410"], ["  0731066120", 5420])
    with pytest.raises(chart.ChartError, match=r"stored as a number or a date, not as text \(row 6 is the first\)"):
        chart_file.read_chart(numbers)
    low = _workbook(tmp_path / "low.xlsx", *([["Title"]] * chart_file.HEADER_ROWS), HEADERS, ["  0731066120", "5410"])
    with pytest.raises(chart.ChartError, match=r"^low\.xlsx has no columns named “Business Unit” and “Object Account”"):
        chart_file.read_chart(low)


def _grid_without(path, *columns):
    return cf.write_chart(path, without=columns)


def test_a_chart_without_its_posting_edit_column_is_refused_unless_asked(tmp_path):
    """It loaded with EVERY account postable and no word (B-2271)."""
    path = _grid_without(tmp_path / "grid.xlsx", "P E")
    with pytest.raises(chart.ColumnsMissing) as refused:
        chart_file.read_chart(path)
    assert str(refused.value) == (
        "grid.xlsx has no posting edit column (“P E”): without it AP Coder cannot tell a title from an account that "
        "can be posted to. Add the column to the grid in JD Edwards and export again."
    ) and refused.value.fields == ("posting_edit",)
    read = chart_file.read_chart(path, anyway=True)
    assert read.without == ("posting_edit",) and len(read.rows) == len(cf.CHART)
    assert not any(r.posting_edit for r in read.rows)
    assert chart_file.read_chart(cf.write_chart(tmp_path / "chart.xlsx")).without == ()


def test_a_chart_without_its_subsidiary_column_is_refused_unless_asked(tmp_path):
    """It loaded with "Left out (rows repeating an account already read)" and no word that the column was missing
    (B-2272)."""
    with pytest.raises(chart.ColumnsMissing) as refused:
        chart_file.read_chart(_grid_without(tmp_path / "grid.xlsx", "Subsidiary"))
    assert str(refused.value) == (
        "grid.xlsx has no “Subsidiary” column: without it the accounts of one object that differ only by their "
        "subsidiary are read as one account and the others are left out. Add the column to the grid in JD Edwards "
        "and export again."
    )
    read = chart_file.read_chart(tmp_path / "grid.xlsx", anyway=True)
    assert read.without == ("subsidiary",) and not any(r.subsidiary for r in read.rows)
    with pytest.raises(chart.ColumnsMissing) as refused:
        chart_file.read_chart(_grid_without(tmp_path / "both.xlsx", "Subsidiary", "P E"))
    assert refused.value.fields == ("posting_edit", "subsidiary")
    assert "It has no “Subsidiary” column either: " in str(refused.value) and "Add the columns to" in str(refused.value)


def test_a_grids_total_row_is_no_business_unit_and_no_row_of_the_chart(tmp_path):
    """ "Read 21 business units" of a file of 20 and its total row (B-2274)."""
    table = [cf.BU_HEADERS, *cf.bu_cells()]
    read = chart_file.read_business_units(_workbook(tmp_path / "bu.xlsx", *table, ["Total", len(cf.UNITS)]))
    assert [u.code for u in read.rows] == [u[0] for u in cf.UNITS] and read.skipped == {"total rows": 1}
    named = _workbook(tmp_path / "named.xlsx", ["Job Number", "Job Name", "Co", "Job Sts"],
                      ["       TOTAL", "Total Energy site", "00001", "ACT"])  # fmt: skip
    assert [u.code for u in chart_file.read_business_units(named).rows] == ["TOTAL"]  # (a business unit so named)
    table = [cf.CHART_HEADERS, *cf.chart_cells()]
    read = chart_file.read_chart(_workbook(tmp_path / "chart.xlsx", *table, ["Grand Total", len(cf.CHART)]))
    assert len(read.rows) == len(cf.CHART) and read.skipped == {"total rows": 1}


def test_an_empty_first_sheet_or_another_file_is_said(tmp_path):
    workbook = openpyxl.Workbook()
    workbook.active.title = "Notes"
    workbook.create_sheet("Chart").append(HEADERS)
    workbook.save(tmp_path / "second.xlsx")
    with pytest.raises(chart.ChartError) as refused:
        chart_file.read_chart(tmp_path / "second.xlsx")
    assert str(refused.value).startswith("second.xlsx: its first sheet (“Notes”) is empty")
    assert "must be the first sheet" in str(refused.value)
    with pytest.raises(chart.ChartError, match=r"^bu\.xlsx has no column named “Job Number” in its first row"):
        chart_file.read_business_units(_workbook(tmp_path / "bu.xlsx", ["Name", "Status"], ["Head Office", "ACT"]))


def test_far_more_rows_columns_or_a_longer_cell_than_the_export_has_are_refused(tmp_path, monkeypatch):
    table = [cf.CHART_HEADERS, *cf.chart_cells()]
    long = [list(row) for row in table]
    long[5][3] = "x" * (2 * chart_file.CELL_MOST)
    with pytest.raises(chart.ChartError, match=r"^long\.xlsx holds a cell far longer than any of the export's"):
        chart_file.read_chart(cf.write_inline(tmp_path / "long.xlsx", long))
    wide = [[*cf.CHART_HEADERS, *(f"More {n}" for n in range(chart_file.COLUMNS_MOST))], *cf.chart_cells()]
    with pytest.raises(chart.ChartError, match=r"^wide\.xlsx has .* columns"):
        chart_file.read_chart(cf.write_inline(tmp_path / "wide.xlsx", wide))
    monkeypatch.setattr(chart, "ROWS_MOST", 10)
    with pytest.raises(chart.ChartError, match=r"^rows\.xlsx: This file has more than 10 rows"):
        chart_file.read_chart(cf.write_inline(tmp_path / "rows.xlsx", table))
    assert chart_file.SHEET_MOST == 250_000_000 and chart.ROWS_MOST == 10


def test_a_file_that_is_not_the_export_is_refused_in_plain_words(tmp_path):
    (tmp_path / "chart.csv").write_text("Business Unit,Object Account\n0731066120,5410\n", encoding="utf-8")
    with pytest.raises(chart.ChartError, match=r"chart\.csv is not an Excel workbook"):
        chart_file.read_chart(tmp_path / "chart.csv")
    with pytest.raises(chart.ChartError, match="This file is not an Excel workbook"):
        chart_file.read_chart(b"not a workbook")
    with pytest.raises(chart.ChartError, match="“Business Unit” and “Object Account”"):
        chart_file.read_chart(cf.write_business_units(tmp_path / "bu.xlsx"))
    # The chart has a business unit column too: given as the business unit master, it is refused by name.
    with pytest.raises(chart.ChartError, match="it is the chart of accounts, not the business unit master"):
        chart_file.read_business_units(cf.write_chart(tmp_path / "chart.xlsx"))


def test_a_workbook_that_unzips_to_more_than_a_chart_does_is_not_opened(tmp_path, monkeypatch):
    path = cf.write_chart(tmp_path / "chart.xlsx")
    monkeypatch.setattr(chart_file, "SHEET_MOST", 1000)
    with pytest.raises(chart.ChartError, match="unzips to more than a chart of accounts does"):
        chart_file.read_chart(path)
    monkeypatch.undo()
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as z:  # another part: held to the limit of any workbook
        z.writestr("xl/media/filler.bin", b"\0" * (chart_file.PART_MOST + 1))
    with pytest.raises(chart.ChartError, match="was not opened"):
        chart_file.read_chart(path)
    assert chart_file.PART_MOST == triage.MAX_SHEET_PART_BYTES < chart_file.SHEET_MOST


def test_a_chart_of_the_real_size_is_read_in_well_under_a_minute(tmp_path):
    """14,740 rows of 110 inline-text columns unzip to over 100 MB: every other reader refuses the workbook (finding
    E1 of the audit behind D-209); this one reads it a row at a time."""
    rows = cf.big_chart_rows()
    path = cf.write_big_chart(tmp_path / "big.xlsx", rows)
    with zipfile.ZipFile(path) as z:
        assert z.getinfo("xl/worksheets/sheet1.xml").file_size > 100_000_000
    assert triage.sheet_unzips_too_big(path.read_bytes())
    started = time.monotonic()
    read = chart_file.read_chart(path)
    took = time.monotonic() - started
    assert len(read.rows) == len(rows) == 14_740 and read.skipped == {}
    assert len({r.bu for r in read.rows}) == 134 and sum(r.is_header for r in read.rows) == 134
    assert read.rows[-1] == chart.ChartRow("0731066133", "5027", "1003", "Expense Account 107", 7, "",
                                           "Operating Expenses", "07310")  # fmt: skip
    assert took < 45, f"a chart of the real size took {took:.0f} s to read"


def _counted_as_read(data: bytes, title: str, header: int, company: int | None):
    """The rows under the header with a cell filled, and of each company, as every row read gives them (the survey's
    way before D-271)."""
    import itertools

    rows, companies = 0, {}
    for row in itertools.islice(chart_file.read_rows(data, "book.xlsx", title), header, None):
        cells = ["" if cell is None else str(cell).strip() for cell in row]
        if any(cells):
            rows += 1
            if company is not None and company < len(cells) and cells[company]:
                companies[cells[company]] = companies.get(cells[company], 0) + 1
    return rows, companies


@pytest.mark.parametrize("write_only", [False, True], ids=["shared strings", "inline strings"])
def test_a_sheets_rows_and_companies_are_counted_in_its_xml_as_reading_every_row_counts_them(tmp_path, write_only):
    """D-271: the survey's counts without a cell made of the rows: shared strings (Excel's way) and inline ones, a
    title row over the column names, rows of lone spaces and gaps, a company as a number and as text, a short row."""
    book = openpyxl.Workbook(write_only=write_only)
    sheet = book.create_sheet("GL lines") if write_only else book.active
    if not write_only:
        sheet.title = "GL lines"
    rows = [["G/L lines of vouchers"], ["Document", "Doc Co", "Amount", "Remark"]]
    rows += [[5000 + n, 210 if n % 3 else "00310", 12.5 * n, "AT&T <cell>" if n % 2 else " "] for n in range(1, 40)]
    rows += [[" ", " ", None, " "], [None, None, None, " "], [6001], [None, "00420"], [" ", None, None, "x"]]
    for row in rows:
        sheet.append(row)
    if not write_only:
        sheet.cell(row=60, column=1, value=7000)  # (a gap of rows before it)
    other = book.create_sheet("Notes")
    other.append(["Note"])
    path = tmp_path / "book.xlsx"
    book.save(path)
    data = path.read_bytes()
    heads = {}

    def pick(title, head):
        heads[title] = head
        return (2, 1) if title == "GL lines" else None

    found = {title: (rows, companies) for title, rows, companies in chart_file.sheet_survey(data, pick, "book.xlsx")}
    assert heads["GL lines"][:3] == [["G/L lines of vouchers"], ["Document", "Doc Co", "Amount", "Remark"],
                                     ["5001", "210", "12.5", "AT&T <cell>"]]  # fmt: skip
    assert found["GL lines"] == _counted_as_read(data, "GL lines", 2, 1)
    assert found["GL lines"][0] == 39 + 3 + (0 if write_only else 1)
    assert found["GL lines"][1] == {"210": 26, "00310": 13, "00420": 1}
    assert found["Notes"] == (0, {}) and heads["Notes"] == [["Note"]]


def test_a_sheet_whose_xml_names_its_namespace_is_counted_too(tmp_path):
    """A workbook another program wrote, its elements prefixed ("x:row"), counts the same."""
    import re

    path = tmp_path / "book.xlsx"
    _workbook(path, ["Business Unit", "Company"], ["  0880133142", "00210"], ["  0880133150", "00210"], [" ", " "])
    data = path.read_bytes()
    prefixed = tmp_path / "prefixed.xlsx"
    with zipfile.ZipFile(path) as z, zipfile.ZipFile(prefixed, "w") as out:
        for item in z.infolist():
            part = z.read(item)
            if item.filename.startswith("xl/worksheets/"):
                part = re.sub(rb"<(/?)(?!\?)(\w+)", rb"<\1x:\2", part).replace(
                    b"<x:worksheet xmlns=", b"<x:worksheet xmlns:x="
                )
            out.writestr(item, part)
    expected = chart_file.sheet_survey(data, lambda title, head: (1, 1))
    assert [found[1:] for found in expected] == [(2, {"00210": 2})]
    assert chart_file.sheet_survey(prefixed.read_bytes(), lambda title, head: (1, 1)) == expected


_ROW = b'<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
_RELS = (b'<Relationship Id="rId1" Type="x/worksheet" Target="worksheets/sheet1.xml"/>'
         b'<Relationship Id="rId2" Type="x/sharedStrings" Target="sharedStrings.xml"/>')  # fmt: skip


def _crafted(rows: bytes = _ROW, shared: bytes = b"<sst><si><t>x</t></si></sst>",
             sheets: bytes = b'<sheet name="S" r:id="rId1"/>', rels: bytes = _RELS) -> bytes:  # fmt: skip
    """A workbook of the XML given, as no program writes it: its sheet's rows, its shared strings, the sheets its
    workbook lists and its relationships."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", b'<workbook xmlns:r="r"><sheets>' + sheets + b"</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", b"<Relationships>" + rels + b"</Relationships>")
        z.writestr("xl/sharedStrings.xml", shared)
        z.writestr("xl/worksheets/sheet1.xml", b"<worksheet><sheetData>" + rows + b"</sheetData></worksheet>")
    return out.getvalue()


def test_a_tiny_crafted_workbook_is_surveyed_at_once_never_holding_the_server():
    """B-2760: rows, cells or shared strings never closed, 20,000 of them in an upload of 1 KB, took the survey's
    patterns a search to the end of the XML from each (16,000 rows: 12 s; a sheet of 250 MB: days), the server held
    meanwhile; so did the workbook's relationships never closed, or a sheet's attribute of 20,000 words. A sheet
    listed again was read again (any number of times). Each is surveyed at once, those that are no workbook's XML
    refused in plain words."""
    shapes = {
        "rows never closed": _crafted(b"<row>" * 20_000),
        "cells never closed": _crafted(b"<row>" + b"<c>" * 20_000 + b"</row>"),
        "shared strings never closed": _crafted(shared=b"<sst>" + b"<si>" * 20_000 + b"</sst>"),
        "one sheet listed twice": _crafted(sheets=b'<sheet name="S" r:id="rId1"/><sheet name="T" r:id="rId1"/>'),
        "relationships never closed": _crafted(rels=b"<Relationship " * 20_000),
        "an attribute of 20,000 words": _crafted(sheets=b"<sheet " + b"a:" * 20_000 + b' r:id="rId1"/>'),
    }
    said = {}
    for shape, data in shapes.items():
        assert len(data) < 2_000, shape
        started = time.monotonic()
        try:
            said[shape] = str(chart_file.sheet_survey(data, lambda title, head: (1, 0), "crafted.xlsx"))
        except chart.ChartError as exc:
            said[shape] = str(exc)
        took = time.monotonic() - started
        assert took < 2, f"{shape}: {took:.1f} s"
    refused = [shape for shape, words in said.items() if words.startswith("crafted.xlsx could not be read as an Excel")]
    assert refused == list(shapes)[:4], said


def test_a_cell_past_excels_last_column_or_rows_numbered_1_again_and_again_are_never_held():
    """B-2761: the survey's first rows were padded out to a cell's column before being cut to ``COLUMNS_MOST``: one cell
    "ZZZZZ1" made a list of 12 million cells (190 MB), "ZZZZZZ1" 2.6 GB, and a reference of 20 letters ended in an
    error the page could not say (OverflowError); rows numbered 1 again and again were each kept. A column past those
    kept is never padded to, a reference of any length is past Excel's last column (XFD), and ``most`` rows are
    kept."""
    import tracemalloc

    first = (b'<row r="1"><c r="A1" t="inlineStr"><is><t>Company</t></is></c><c r="ZZZZZ1"><v>1</v></c>'
             b'<c r="' + b"Z" * 20 + b'1"><v>2</v></c></row>')  # fmt: skip
    data = _crafted(first + b'<row r="1"><c r="A1"><v>3</v></c></row>' * 20_000)
    heads = []
    tracemalloc.start()
    try:
        chart_file.sheet_survey(data, lambda title, head: heads.append(head), "crafted.xlsx")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert heads == [[["Company", *[""] * (chart_file.COLUMNS_MOST - 1)], *[["3"]] * (chart_file.HEADER_ROWS - 1)]]
    assert peak < 5_000_000, f"{peak / 1e6:.0f} MB"
    assert chart_file.EXCEL_COLUMNS == 16_384 == chart_file._column(b"XFD") + 1 == chart_file._column(b"Z" * 20)


def test_a_sheet_read_in_blocks_smaller_than_a_row_counts_the_same_and_a_row_longer_than_any_exports_is_refused(
    tmp_path, monkeypatch
):
    """B-2760: a block of a sheet's XML is cut before a row's start (a row closed in itself too: an empty row given a
    height) and ends with the rows (what follows them, such as merged cells, never read); a row that does not end
    within ``ROW_MOST`` is refused, never held to the end of the sheet."""
    import re

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Document", "Doc Co"])
    for n in range(1, 60):
        sheet.append([5000 + n, "00210" if n % 3 else "00310"])
    for n in range(1, 60):
        sheet.row_dimensions[100 + n].height = 30
        sheet.cell(row=300, column=1 + n % 2, value=" ")
        sheet.merge_cells(start_row=400 + n, start_column=1, end_row=400 + n, end_column=2)
    sheet.cell(row=200, column=2, value="00420")
    book.save(tmp_path / "saved.xlsx")
    path = tmp_path / "book.xlsx"
    with zipfile.ZipFile(tmp_path / "saved.xlsx") as z, zipfile.ZipFile(path, "w") as out:
        for item in z.infolist():  # (the empty rows closed in themselves, as Excel writes them)
            out.writestr(item, re.sub(rb'(<row [^>]*customHeight="1")></row>', rb"\1/>", z.read(item)))
    data = path.read_bytes()
    assert zipfile.ZipFile(path).read("xl/worksheets/sheet1.xml").count(b'customHeight="1"/>') == 59
    whole = chart_file.sheet_survey(data, lambda title, head: (1, 1))
    counted = (60, {"00210": 40, "00310": 19, "00420": 1})
    assert [found[1:] for found in whole] == [_counted_as_read(data, "Sheet", 1, 1)] == [counted]
    monkeypatch.setattr(chart_file, "BLOCK", 150)
    monkeypatch.setattr(chart_file, "ROW_MOST", 1000)
    assert chart_file.sheet_survey(data, lambda title, head: (1, 1)) == whole
    monkeypatch.setattr(chart_file, "ROW_MOST", 100)
    with pytest.raises(chart.ChartError, match="could not be read as an Excel workbook"):
        chart_file.sheet_survey(data, lambda title, head: (1, 1))


# --- The chart as a query saves it as text, streamed from disk (D-272) ----------------------------------------------

TAB_HEADER = ("Company", "Business Unit", "Object Account", "Subsidiary", "Account Description", "L D", "P E")
TAB_ROWS = [
    ("00210", "  0731066120", "", "", "Head Office General", "3", "N"),
    ("00210", "  0731066120", "0042", "", "Objects under 1000", "5", "N"),
    ("00210", "  0731066120", "5410", "", 'Pipe 1/2" fittings', "6", " "),
    ("00210", "  0731066120", "5410", "0010", '"Quoted" supplies, and a comma', "7", "L"),
    ("00210", "        0731", "5420", "", "Short unit", "6", ""),
]


def _tab_file(path, rows, encoding="utf-16", newline="\r\n", extra=""):
    lines = ["\t".join(TAB_HEADER), *("\t".join(row) for row in rows)]
    path.write_bytes((newline.join(lines) + newline + extra).encode(encoding))
    return path


def _streamed(path):
    read, rows = chart_file.stream_chart_text(path)
    return read, list(rows)


@pytest.mark.parametrize(("encoding", "newline"), [("utf-16", "\r\n"), ("utf-16-le", "\r\n"), ("utf-16-be", "\n"),
                                                   ("utf-8-sig", "\r\n"), ("utf-8", "\n")])  # fmt: skip
def test_a_tab_delimited_chart_is_read_whatever_its_unicode_and_line_ends(tmp_path, encoding, newline):
    """ReportsNow's "Unicode text" is UTF-16 with its byte-order mark and CRLF; without the mark, or UTF-8, the
    same. Quotation marks are text (a tab file is never quoted), the codes' leading zeros kept, a business unit's
    padding of 12 characters gone as JD Edwards compares it."""
    path = _tab_file(tmp_path / "chart.txt", TAB_ROWS, encoding, newline)
    assert chart_file.text_encoding(path) == encoding  # (told by the byte-order mark, or the noughts of UTF-16)
    read, rows = _streamed(path)
    assert (read.parsed, read.skipped, read.without) == (5, {}, ())
    assert [(r.bu, r.object, r.subsidiary, r.description, r.posting_edit, r.company, r.line) for r in rows] == [
        ("0731066120", "", "", "Head Office General", "N", "00210", 2),
        ("0731066120", "0042", "", "Objects under 1000", "N", "00210", 3),
        ("0731066120", "5410", "", 'Pipe 1/2" fittings', "", "00210", 4),
        ("0731066120", "5410", "0010", '"Quoted" supplies, and a comma', "L", "00210", 5),
        ("0731", "5420", "", "Short unit", "", "00210", 6),
    ]


def test_a_tab_row_whose_cells_do_not_line_up_is_rejected_with_its_row_never_read_shifted(tmp_path):
    """A tab inside a description (one cell more), a line break inside one (the row in two), the file cut short: each
    left out and counted, the first rows named, the others read."""
    rows = [*TAB_ROWS[:3], ("00210", "  0731066120", "5430", "", "Rent\tand more", "6", ""),
            ("00210", "  0731066120", "5440", "", "Two"), ("lines", "6", ""), TAB_ROWS[4]]  # fmt: skip
    path = _tab_file(tmp_path / "chart.tsv", rows, "utf-8", "\r\n", extra="00210\t  0731066120\t54")
    read, kept = _streamed(path)
    assert [r.object for r in kept] == ["", "0042", "5410", "5420"]
    assert read.parsed == 8 and read.skipped == {chart.MISALIGNED: 4}
    assert read.examples == {chart.MISALIGNED: [5, 6, 7, 9]}


def test_a_comma_file_is_read_as_before_and_text_that_is_neither_utf_is_read_as_windows_text(tmp_path):
    path = tmp_path / "chart.csv"
    path.write_bytes(("Business Unit,Object Account,Subsidiary,Account Description,P E\r\n"
                      '  0731066120,5410,,"Café, ""the"" lounge",\r\n').encode("cp1252"))  # fmt: skip
    assert chart_file.text_encoding(path) == "cp1252"
    read, rows = chart_file.stream_chart_text(path)
    assert [r.description for r in rows] == ['Café, "the" lounge'] and read.parsed == 1


@pytest.mark.parametrize("encoding", ["cp1252", "utf-8"])
def test_text_whose_first_accent_comes_after_its_first_megabyte_is_read_in_its_own_encoding(tmp_path, encoding):
    """B-2763: a chart saved as Windows' own text (cp1252, D-272) whose first megabyte was plain English (the first
    companies' accounts) was told UTF-8 by that megabyte alone, and refused at its first accent, a Quebec company's
    description far into the load. The whole file tells it, a block at a time; UTF-8 accented after it stays UTF-8."""
    rows = [("00210", "  0731066120", f"{1000 + n}", "", "Head office general expense", "6", "") for n in range(20_000)]
    rows.append(("00220", "  0731066130", "5100", "", "Matériaux coût", "6", ""))
    path = _tab_file(tmp_path / "chart.txt", rows, encoding)
    assert path.read_bytes().index(b"Mat") > chart_file.SNIFF
    assert chart_file.text_encoding(path) == encoding
    read, given = _streamed(path)
    assert (read.parsed, read.skipped) == (20_001, {}) and given[-1].description == "Matériaux coût"
