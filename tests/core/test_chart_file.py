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


def test_only_the_columns_the_import_needs_are_kept(tmp_path):
    records = chart_file.read_export(cf.write_chart(tmp_path / "chart.xlsx"), chart.chart_columns)
    assert len(records) == len(cf.CHART)
    assert list(records[0]) == ["Business Unit", "Object Account", "Subsidiary", "Account Description", "L D", "P E"]
    assert records[3]["Business Unit"] == "       07310" and records[0]["Object Account"] == " "  # cleaned later


def test_a_number_stored_as_a_number_and_a_short_row_are_read(tmp_path):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Business Unit", "Object Account", "Subsidiary", "Account Description", "L D", "P E"])
    sheet.append(["  0731066120", 5410, 2210.0, "       Coffee", 7.0, None])
    sheet.append(["  0731066120", "5430"])
    workbook.save(tmp_path / "numbers.xlsx")
    assert chart_file.read_chart(tmp_path / "numbers.xlsx").rows == [
        chart.ChartRow(cf.HOME, "5410", "2210", "Coffee", 7, ""), chart.ChartRow(cf.HOME, "5430")]  # fmt: skip


def test_a_file_that_is_not_the_export_is_refused_in_plain_words(tmp_path):
    (tmp_path / "chart.csv").write_text("Business Unit,Object Account\n0731066120,5410\n", encoding="utf-8")
    with pytest.raises(chart.ChartError, match=r"chart\.csv is not an Excel workbook"):
        chart_file.read_chart(tmp_path / "chart.csv")
    with pytest.raises(chart.ChartError, match="This file is not an Excel workbook"):
        chart_file.read_chart(b"not a workbook")
    with pytest.raises(chart.ChartError, match="“Business Unit” or “Object Account”"):
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
