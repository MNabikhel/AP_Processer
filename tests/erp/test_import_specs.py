"""What each import takes (ap_coder/erp/import_specs.py, D-234): each spec's required columns are exactly what its
reader refuses a file without, its columns are found by its reader in any order, a file is told apart from the other
reports, and each template is a file its reader takes."""

from __future__ import annotations

import csv
import io
import random

import openpyxl
import pytest

from ap_coder.ai import history
from ap_coder.bench import jde_exports as made
from ap_coder.coding import banks, po, vendor_info
from ap_coder.coding.vendors import master_columns, master_rows
from ap_coder.core import chart, chart_file
from ap_coder.erp import import_specs as specs
from ap_coder.erp import jde_accounts, jde_settings, jde_worksheet, journal, registers, statements
from ap_coder.erp.import_specs import SPECS


@pytest.fixture(scope="module")
def exports(tmp_path_factory):
    folder = tmp_path_factory.mktemp("jde")
    made.write_all(folder, size="small")
    return folder


def _xlsx(rows: list[list[str]], sheet: str = "Sheet1") -> bytes:
    book = openpyxl.Workbook()
    book.active.title = sheet
    for row in rows:
        book.active.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


# A value each field reads as a good row's (any other field: "x").
VALUES = {
    "business_unit": "1000", "object": "6010", "subsidiary": "01", "posting_edit": "", "lod": "7", "code": "1000",
    "vendor_name": "ACME LTD", "erp_id": "410001", "tax_id": "123456789RT0001", "account": "0021061010.7510.5107",
    "debit": "10.00", "credit": "", "document": "1001", "pay_item": "001", "an8": "410001", "gross": "10.00",
    "invoice_number": "INV-1", "total": "10.00", "po_number": "PO1", "quantity": "1", "number": "INV-1",
    "amount": "10.00", "gl_code": "6010", "currency": "CAD", "id": "company", "answer": "00210",
}  # fmt: skip


def _row(spec, fields) -> list[str]:
    return [VALUES.get(f, "x") for f in fields]


def _records(spec, fields) -> list[dict[str, str]]:
    return [dict(zip([spec.column(f).header for f in fields], _row(spec, fields), strict=True))]


def _journal_file(spec, fields) -> bytes:
    """A journal with the header of ``fields`` (each at its report position) and a voucher line under it."""
    width = max(journal.POSITIONS.values()) + 1
    head, line = [""] * width, [""] * width
    for f in fields:
        head[journal.POSITIONS[f]] = spec.column(f).header
        line[journal.POSITIONS[f]] = VALUES.get(f, "x")
    line[journal.POSITIONS["doc_type"]] = "PV"
    text = io.StringIO()
    csv.writer(text).writerows([["Transaction Journal"], head, line])
    return text.getvalue().encode("utf-8")


def _chart_takes(spec, fields) -> bool:
    try:
        reader = chart_file.read_chart if spec.key == "chart" else chart_file.read_business_units
        reader(_xlsx([[spec.column(f).header for f in fields], _row(spec, fields)]))
    except chart.ChartError:
        return False
    return True


def _worksheet_takes(spec, fields) -> bool:
    data = _xlsx([[spec.column(f).header for f in fields], _row(spec, fields)], sheet="Questions")
    return bool(jde_worksheet.read(data, jde_settings.JdeSettings()).changes)


def _po_takes(spec, fields) -> bool:
    from ap_coder.web.pages.purchase_orders import missing_columns

    return not missing_columns(po.map_columns([spec.column(f).header for f in fields]))


def _statement_takes(spec, fields) -> bool:
    from ap_coder.web.pages.statements import mapping_ready

    return mapping_ready(statements.map_columns([spec.column(f).header for f in fields]))


def _accounts_take(spec, fields) -> bool:
    """The page refuses an import without a code column (``files.pick_refusal``); its choices start as its spec's."""
    from ap_coder.web.files import pick_refusal
    from ap_coder.web.pages import accounts

    table = accounts.SPECS[spec.key]
    return not pick_refusal(table, table.find([spec.column(f).header for f in fields]))


def _bank_takes(spec, fields) -> bool:
    found = banks.columns_for([spec.column(f).header for f in fields])
    rows = [{f: record[header] for f, header in found.items()} for record in _records(spec, fields)]
    return bool(banks.clean_rows(rows)[0])


# Each spec's reader, as its page or the command line calls it: whether it takes a file of these fields' columns.
READERS = {
    "chart": _chart_takes,
    "units": _chart_takes,
    # (the page's own rule since B-2275: the number is the supplier's identity; master_rows needs the name)
    "supplier_master": lambda s, f: bool(master_rows(_records(s, f), master_columns([s.column(x).header for x in f])))
    and "erp_id" in master_columns([s.column(x).header for x in f]),
    "vendor_info": lambda s, f: vendor_info.read(_xlsx([[s.column(x).header for x in f], _row(s, f)]), "v.xlsx")
    is not None,
    "journal": lambda s, f: journal.looks_like_journal(_journal_file(s, f)),
    "vouchers": lambda s, f: bool(registers.voucher_columns([s.column(x).header for x in f])),
    "gl_accounts": _accounts_take,
    "cost_centers": _accounts_take,
    "bank_accounts": _bank_takes,
    "vendor_master": lambda s, f: bool(master_rows(_records(s, f), master_columns([s.column(x).header for x in f]))),
    "purchase_orders": _po_takes,
    "statements": _statement_takes,
    "erp_register": lambda s, f: bool(registers.rows_from_records(
        _records(s, f), registers.map_columns([s.column(x).header for x in f]))[0]),
    "past_coding": lambda s, f: bool(history.rows_from_records(
        _records(s, f), history.map_columns([s.column(x).header for x in f]))[0]),
    "gl_map": lambda s, f: bool(jde_accounts.gl_map_from_records(_records(s, f))),
    "jde_worksheet": _worksheet_takes,
}  # fmt: skip


def test_every_import_has_a_spec_and_a_reader_to_prove_it_by():
    assert set(READERS) == set(SPECS)
    for spec in SPECS.values():
        assert spec.title and spec.purpose and spec.source and spec.report and spec.file_types and spec.required


@pytest.mark.parametrize("key", list(SPECS))
def test_the_required_columns_are_exactly_what_the_reader_refuses_a_file_without(key):
    spec, takes = SPECS[key], READERS[key]
    needed = [c.field for c in spec.required] + list(spec.one_of[:1])
    assert takes(spec, needed), "a file with only the required columns is taken"
    assert specs.check(spec, [spec.column(f).header for f in needed]).ok
    for left_out in needed:
        if left_out in spec.one_of:
            continue
        fewer = [f for f in needed if f != left_out]
        assert not takes(spec, fewer), f"a file without {left_out} is refused"
        missing = specs.check(spec, [spec.column(f).header for f in fewer]).missing
        assert spec.column(left_out) in missing and (key == "journal" or len(missing) == 1)  # (journal: its header)
    if spec.one_of:  # none of them: refused
        fewer = [c.field for c in spec.required]
        assert not takes(spec, fewer) and specs.check(spec, [spec.column(f).header for f in fewer]).none_of


@pytest.mark.parametrize("key", list(SPECS))
def test_every_column_is_found_by_its_header_and_each_other_name_the_reader_reads(key):
    """(The journal's columns are told by where they are on its header: its template's test.)"""
    spec = SPECS[key]
    if key == "journal":
        return
    found = specs.check(spec, [c.header for c in spec.columns]).found
    assert set(found) == {c.field for c in spec.columns}
    for c in spec.columns:
        others = [o.header for o in spec.required if o is not c]
        for name in c.others if spec.key not in ("gl_accounts", "cost_centers") else ():
            assert c.field in spec.find([name]) or c.field in spec.find([name, *others]), (c.field, name)


def test_the_specs_are_the_readers_own_tables():
    assert {c.field for c in SPECS["chart"].required} == {*chart.CHART_NEEDS, *chart.CHART_WANTS}
    assert [c.field for c in SPECS["chart"].columns if not c.required] == ["description", "lod"]
    assert {c.field for c in SPECS["vouchers"].required} == set(registers.VOUCHER_REQUIRED)
    assert {c.field for c in SPECS["vendor_info"].required} == set(vendor_info.NEEDS)
    assert {c.field for c in SPECS["purchase_orders"].required} == set(po.REQUIRED_COLUMNS)
    assert {c.field for c in SPECS["past_coding"].required} == set(history.REQUIRED)
    assert {c.field for c in SPECS["erp_register"].required} == set(registers.REQUIRED)
    for key, aliases in (("chart", chart.CHART_ALIASES), ("units", chart.BU_ALIASES), ("vouchers",
                         registers.VOUCHER_ALIASES), ("bank_accounts", banks.ALIASES), ("gl_map",
                         jde_accounts.GL_MAP_ALIASES)):  # fmt: skip
        assert {c.field: c.names for c in SPECS[key].columns} == {f: tuple(n) for f, n in aliases.items()}, key


@pytest.mark.parametrize("headers, found", [
    (["GL Code", "Account Name", "Group"], {"code": "GL Code", "description": "Account Name", "category": "Group"}),
    (["Object Account", "Account Description", "Account Type", "Meals"],
     {"code": "Object Account", "description": "Account Description", "category": "Account Type", "meals": "Meals"}),
])  # fmt: skip
def test_the_accounts_guess_is_the_same_whatever_the_order_of_the_columns(headers, found):
    """("Account Name" came first: it was taken for the code, the description left to guess.)"""
    for order in (headers, headers[::-1], [*headers[1:], headers[0]]):
        assert SPECS["gl_accounts"].find(order) == found, order
        assert SPECS["cost_centers"].find(order) == {f: h for f, h in found.items() if f != "meals"}, order


def _sheet(path) -> list[list[str]]:
    return [["" if v is None else str(v) for v in row] for row in chart_file.read_rows(path.read_bytes())]


@pytest.mark.parametrize("name, key", [(made.CHART_FILE, "chart"), (made.UNITS_FILE, "units"),
                                       (made.SUPPLIERS_FILE, "supplier_master"), (made.VENDOR_INFO_FILE, "vendor_info"),
                                       (made.HEADERS_FILE, "vouchers")])  # fmt: skip
def test_each_export_of_jd_edwards_is_told_by_its_columns(name, key, exports):
    rows = _sheet(exports / name)
    assert specs.identify(rows[0]).key == key and specs.check(SPECS[key], rows[0]).ok
    titled = [["Account Master"], [], *rows[:8]]  # a grid's title rows above its names (B-2273)
    assert specs.header_row(titled, [SPECS[k] for k in specs.JDE_REPORTS]) == 2


def test_a_chart_with_its_columns_in_another_order_is_read_the_same(exports, tmp_path):
    """The bench's chart export with its 110 columns shuffled: the same columns found, the same accounts read."""
    rows = _sheet(exports / made.CHART_FILE)
    order = list(range(len(rows[0])))
    random.Random(7).shuffle(order)
    shuffled = [[row[i] if i < len(row) else "" for i in order] for row in rows]
    first, again = specs.check(SPECS["chart"], rows[0]), specs.check(SPECS["chart"], shuffled[0])
    assert again.ok and again.found == first.found and set(again.unused) == set(first.unused)
    upper = [h.upper() for h in shuffled[0]]  # (and in capitals)
    assert specs.check(SPECS["chart"], upper).found == {f: h.upper() for f, h in first.found.items()}
    assert chart_file.read_chart(_xlsx(shuffled)).rows == chart_file.read_chart(exports / made.CHART_FILE).rows


def test_a_missing_column_is_named_with_its_report_and_another_report_with_its_card():
    chart_spec = SPECS["chart"]
    without = [c.header for c in chart_spec.columns if c.field != "posting_edit"]
    said = specs.check(chart_spec, without).problem()
    assert said.startswith("Not loaded: this file has no “P E” (posting edit code) column, which JD Edwards' account")
    assert "export it to Excel again" in said
    vouchers = [c.header for c in SPECS["vouchers"].columns]
    other = specs.identify(vouchers)
    assert other.key == "vouchers"
    said = specs.check(chart_spec, vouchers).problem(other, "5. Vouchers")
    assert "columns, which JD Edwards' account master" in said and said.endswith(
        "It looks like JD Edwards' voucher list (the supplier ledger inquiry's grid): load it under 5. Vouchers."
    )
    assert specs.identify(without) is None  # a chart missing a column, not the business units (which it has)
    units = specs.check(SPECS["units"], [c.header for c in chart_spec.columns])
    assert units.ok and units.problem(SPECS["chart"], "1. Chart").startswith("Not loaded: this file is JD Edwards' "
                                                                             "account master")  # fmt: skip


def test_the_journal_is_told_by_its_header_and_its_first_lines_are_shown(exports):
    rows, at = specs.journal_head((exports / made.JOURNAL_FILE).read_bytes())
    result = specs.check(SPECS["journal"], rows[at])
    assert result.ok and set(result.found) == set(journal.POSITIONS)
    shown = specs.journal_lines(rows, at)
    assert shown["headers"][:4] == ["Document type", "Company", "G/L date", "Account number"]
    assert len(shown["rows"]) == specs.PREVIEW_ROWS and all("." in row[3] for row in shown["rows"])


@pytest.mark.parametrize("key", [k for k, s in SPECS.items() if not s.no_template])
def test_each_template_is_a_file_its_reader_takes(key):
    spec = SPECS[key]
    data, name, kind = specs.template(spec)
    if key == "journal":
        assert name.endswith(".csv") and kind == "text/csv" and journal.looks_like_journal(data)
        rows, at = specs.journal_head(data)
        assert journal._positions(rows)[0] == journal.POSITIONS and specs.check(spec, rows[at]).ok
        return
    assert name.endswith("_template.xlsx")
    (header,) = _sheet_of(data)
    assert header == [c.header for c in spec.columns] and specs.check(spec, header).ok
    if key in ("chart", "units"):
        _chart_takes(spec, [c.field for c in spec.columns])
    if key == "vendor_info":
        assert vendor_info.read(data, name) is not None


def test_the_worksheet_has_no_blank_template_and_says_why():
    with pytest.raises(ValueError, match="download it on this page"):
        specs.template(SPECS["jde_worksheet"])


def _sheet_of(data: bytes) -> list[list[str]]:
    return [["" if v is None else str(v) for v in row] for row in chart_file.read_rows(data)]


def test_a_needed_column_whose_header_is_there_twice_is_refused_naming_it(exports):
    """B-2408: the first of two columns named as a needed one was read, whichever it was (a junk "Business Unit"
    before the chart's own: every account on business unit "ZZZ")."""
    headers = _sheet(exports / made.CHART_FILE)[0]
    doubled = specs.check(SPECS["chart"], ["Business Unit", *headers])
    assert not doubled.ok and [c.field for c in doubled.twice] == ["business_unit"]
    assert "more than one column named “Business Unit”" in doubled.problem()
    master = _sheet(exports / made.SUPPLIERS_FILE)[0]
    mangled = specs.check(SPECS["supplier_master"], [*master, "Alpha Name.1"])  # (as pandas names the second)
    assert [c.field for c in mangled.twice] == ["vendor_name"] and "“Alpha Name”" in mangled.problem()
    info = specs.check(SPECS["vendor_info"], _sheet(exports / made.VENDOR_INFO_FILE)[0])
    assert info.ok and not info.twice  # (the list's own two "Business Unit" columns, both read)


def test_jd_edwards_reports_are_told_by_their_own_headers_and_other_lists_are_not(exports):
    """B-2404: what a page of any ERP's lists refuses as JD Edwards' (``recognise``), with a column missing too;
    never a list of another system with a name, a number, a code or a business unit."""
    for name, key in ((made.CHART_FILE, "chart"), (made.UNITS_FILE, "units"), (made.SUPPLIERS_FILE, "supplier_master"),
                      (made.VENDOR_INFO_FILE, "vendor_info"), (made.HEADERS_FILE, "vouchers"),
                      ("voucher_split_gl_distribution.xlsx", "gl_distribution"),
                      ("voucher_split_pay_items.xlsx", "pay_items"),
                      ("voucher_split_payment_history.xlsx", "payment_history")):  # fmt: skip
        headers = _sheet(exports / name)[0]
        assert specs.recognise(headers) == key, name
        needed = SPECS[key].required[0].header if key in SPECS else headers[0]
        assert specs.recognise([h for h in headers if h != needed]) == key, (name, needed)
    for key in ("gl_accounts", "cost_centers", "bank_accounts", "vendor_master", "purchase_orders", "statements",
                "erp_register", "past_coding", "gl_map"):  # fmt: skip
        assert specs.recognise([c.header for c in SPECS[key].columns]) is None, key
    for headers in (["Code", "Name"], ["Cost Center", "Description"], ["Vendor Name", "Vendor ID", "Tax ID"],
                    ["Business Unit", "Object Account", "Subsidiary", "GL Code"],
                    ["Document Number", "Supplier Number", "Gross Amount", "Invoice Number"]):  # fmt: skip
        assert specs.recognise(headers) is None, headers
    assert specs.recognise(["Alpha Name", "Address Number"]) == "supplier_master"  # (every column it needs)
