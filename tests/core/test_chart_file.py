"""The chart of accounts and the business unit master read from JD Edwards' exports as they come (D-209): 110 and 67
columns, every cell text, and a chart of the real size, which no other reader of a workbook opens."""

from __future__ import annotations

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
    assert list(records[0]) == ["Business Unit", "Object Account", "Subsidiary", "Account Description", "L D", "P E"]
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


def test_column_names_not_in_row_1_or_an_empty_first_sheet_are_said(tmp_path):
    path = _workbook(tmp_path / "titled.xlsx", ["Account master"], [None], HEADERS, ["  0731066120", "5410"])
    with pytest.raises(chart.ChartError) as refused:
        chart_file.read_chart(path)
    assert str(refused.value).startswith("titled.xlsx: the column names are in row 3, not in row 1")
    assert "Delete the rows above them" in str(refused.value)
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
                                           "Operating Expenses")  # fmt: skip
    assert took < 45, f"a chart of the real size took {took:.0f} s to read"
