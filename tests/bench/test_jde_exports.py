"""The made-up company's JD Edwards exports (``ap_coder.bench.jde_exports``): each file's layout as a real E1 writes
it (what the importers will be tested against), the files agreeing with each other and with the truth they are
written from, the same bytes for the same seed, and a year's coding mix as measured on a real head-office overhead
department."""

import csv
import datetime as dt
import io
import os
import re
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from ap_coder.bench import jde_company, jde_exports, jde_names
from ap_coder.bench.jde_company import ALLOCATION, MAIN_UNIT, RECOVERIES

COLUMNS = {"chart_of_accounts": 110, "business_units": 67, "supplier_master": 10, "voucher_headers": 56,
           "gl_distribution": 36, "pay_items": 44, "payment_history": 24}  # fmt: skip
AMOUNT = re.compile(r"\d*\.\d\d")  # two decimals, no thousands separator; ".26", never "0.26"
DATE = re.compile(r"[1-9]\d?/[1-9]\d?/\d{4}")  # m/d/yyyy, not zero-padded


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    folder = tmp_path_factory.mktemp("jde_small")
    return jde_exports.write_all(folder), folder


def _cells(path: Path) -> list[tuple]:
    """A grid's rows as cells (the header first)."""
    book = openpyxl.load_workbook(path)
    assert book.sheetnames == ["Sheet1"]
    return list(book["Sheet1"].iter_rows())


def _grid(path: Path) -> tuple[list[str], list[dict]]:
    rows = _cells(path)
    header = [cell.value for cell in rows[0]]
    return header, [dict(zip(header, (cell.value for cell in row), strict=True)) for row in rows[1:]]


def _journal(folder: Path) -> tuple[bytes, list[list[str]]]:
    raw = (folder / jde_exports.JOURNAL_FILE).read_bytes()
    return raw, list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))


def _journal_vouchers(rows: list[list[str]]) -> list[list[list[str]]]:
    """The journal's data rows grouped as the report means them: a ``PV`` row and the rows under it."""
    vouchers: list[list[list[str]]] = []
    for row in rows[7:-2]:
        if row[0] == "PV":
            vouchers.append([])
        vouchers[-1].append(row + [""] * (17 - len(row)))
    return vouchers


# --- every grid export -------------------------------------------------------------------------


def test_each_grid_is_one_sheet_with_its_header_in_row_1_and_its_number_of_columns(small):
    _, folder = small
    seen = set()
    for path in sorted(folder.glob("*.xlsx")):
        kind = next(kind for kind in COLUMNS if path.stem.endswith(kind))
        rows = _cells(path)
        assert len(rows[0]) == COLUMNS[kind], path.name
        assert all(isinstance(cell.value, str) and cell.value.strip() for cell in rows[0]), path.name
        assert len(set(cell.value for cell in rows[0])) == len(rows[0]), path.name
        assert openpyxl.load_workbook(path).properties.creator == "Apache POI"
        seen.add(kind)
        for row in rows[1:]:  # a blank field is one space or a truly empty cell, never an empty string
            assert all(cell.value != "" for cell in row), path.name
    assert seen == set(COLUMNS)


def test_only_standard_column_names_are_kept_and_the_rest_are_generic():
    assert jde_exports.CHART_HEADER[:13] == [
        "Business Unit", "Object Account", "Subsidiary", "Account Description", "Company", "L D", "P E",
        "Currency Code", "Account ID", "M C", "Business Unit Description", "Taxable Account", "Tax Rate/Area",
    ]  # fmt: skip
    assert jde_exports.CHART_HEADER[13:56] == [f"Cat Code {n:02d}" for n in range(1, 44)]
    assert jde_exports.CHART_HEADER[56:106] == [f"BU Cat {n}" for n in range(1, 51)]
    units = jde_exports.UNITS_HEADER
    assert units[:6] == ["Job Number", "Job Name", "Co", "Company Name", "LOD", "BU Ty"]
    assert units[9] == "District" and units[21] == "Job Sts" and units[56] == "Related BU"
    generic = [name for n, name in enumerate(units[6:56], start=1) if n not in (4, 16)]
    assert all(re.fullmatch(r"Cat Code \d\d", name) for name in generic)


# --- the chart of accounts ---------------------------------------------------------------------


def test_the_chart_is_text_with_blanks_as_one_space_and_some_columns_truly_empty(small):
    truth, folder = small
    rows = _cells(folder / jde_exports.CHART_FILE)
    header = [cell.value for cell in rows[0]]
    empty = {header.index("Tax Rate/Area"), header.index("WCI Code")}
    assert len(rows) - 1 == len(truth.accounts) == jde_company.SIZES["small"].chart_rows
    for row in rows[1:]:
        for at, cell in enumerate(row):
            if at in empty:
                assert cell.value is None
            else:
                assert cell.data_type == "s" and cell.value, (header[at], cell.value)
    _, chart = _grid(folder / jde_exports.CHART_FILE)
    assert all(row["Currency Code"] == " " for row in chart)
    assert {row["Company"] for row in chart} == {"00210"}
    assert all(re.fullmatch(r"\d{8}", row["Account ID"]) for row in chart)
    assert len({row["Account ID"] for row in chart}) == len(chart)


def test_the_charts_business_units_objects_and_subsidiaries_have_their_real_shapes(small):
    truth, folder = small
    _, chart = _grid(folder / jde_exports.CHART_FILE)
    for row in chart:
        unit = row["Business Unit"]
        assert len(unit) == 12 and unit == unit.strip().rjust(12)
        assert row["Object Account"] == " " or re.fullmatch(r"\d{4}", row["Object Account"])
        assert row["Subsidiary"] == " " or re.fullmatch(r"[A-Z0-9]{1,8}", row["Subsidiary"])
    assert {len(row["Business Unit"].strip()) for row in chart} == {5, 6, 7, 8, 10}
    objects = {row["Object Account"] for row in chart}
    assert any(obj.startswith("0") for obj in objects - {" "})  # below 1,000: 4 digits as text
    shapes = {re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", row["Subsidiary"])) for row in chart}
    assert {" ", "999", "9999", "999999", "99999999", "AA", "AAAAA"} <= shapes
    assert any(re.search(r"[A-Z]", s) and re.search(r"\d", s) for s in (row["Subsidiary"] for row in chart))
    keys = [(row["Business Unit"], row["Object Account"], row["Subsidiary"]) for row in chart]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


def test_the_charts_descriptions_are_indented_by_level_and_cut_at_30_in_all(small):
    truth, folder = small
    _, chart = _grid(folder / jde_exports.CHART_FILE)
    cut = 0
    for row, account in zip(chart, truth.accounts, strict=True):
        level = int(row["L D"])
        assert 3 <= level <= 9 and level == account.level
        assert len(account.description) <= 30
        assert row["Account Description"] == (" " * level + account.description)[:30]
        assert row["Account Description"].startswith(" " * level) and row["Account Description"][level] != " "
        cut += level + len(account.description) > 30
    assert cut > 10  # deeper accounts lose more of their text
    assert {row["L D"] for row in chart} == set("3456789")
    assert {row["P E"] for row in chart} == {" ", "N", "M", "I", "B", "L"}
    assert {row["Taxable Account"] for row in chart} == {" ", "0"}


def test_each_business_unit_has_a_header_row_and_the_reserved_subsidiaries_are_postable(small):
    truth, folder = small
    _, chart = _grid(folder / jde_exports.CHART_FILE)
    headers = [row for row in chart if row["Object Account"] == " "]
    assert all(row["Subsidiary"] == " " and row["L D"] == "3" for row in headers)
    assert [row["Business Unit"].strip() for row in headers] == [unit.number for unit in truth.business_units]
    names = {unit.number: unit.name for unit in truth.business_units}
    assert all(row["Business Unit Description"] == names[row["Business Unit"].strip()] for row in chart)
    recoveries = [row for row in chart if row["Subsidiary"] == RECOVERIES]
    allocation = [row for row in chart if row["Subsidiary"] == ALLOCATION]
    assert truth.company.reserved_subsidiaries == (RECOVERIES, ALLOCATION)
    assert len(recoveries) >= 3 and len(allocation) >= 2
    assert all(row["Business Unit"].strip() == MAIN_UNIT and row["Object Account"][0] == "7" for row in recoveries)
    assert all(len(row["Business Unit"].strip()) == 10 and row["Business Unit"].strip() != MAIN_UNIT
               for row in allocation)  # fmt: skip
    assert all(row["P E"] == " " and row["L D"] == "8" for row in recoveries + allocation)
    assert all(account.postable and account.reserved for account in truth.accounts
               if account.subsidiary in (RECOVERIES, ALLOCATION))  # fmt: skip
    suggestable = truth.suggestable()
    assert suggestable and not any(account.reserved or account.posting_edit for account in suggestable)


# --- the business units and the suppliers -------------------------------------------------------


def test_the_business_unit_master_has_every_shape_and_its_odd_ones(small):
    truth, folder = small
    header, units = _grid(folder / jde_exports.UNITS_FILE)
    numbers = [row["Job Number"] for row in units]
    assert all(len(n) == 12 and n == n.strip().rjust(12) for n in numbers) and numbers == sorted(numbers)
    shapes = Counter(len(n.strip()) for n in numbers)
    assert set(shapes) == {5, 6, 7, 8, 10} and shapes[10] > shapes[8] > 1
    assert any(re.fullmatch(r"\d{5}[A-Z]", n.strip()) for n in numbers)
    assert {row["Job Sts"] for row in units} == {"ACT", "INA"}
    assert [row["Job Sts"] for row in units if "DO NOT USE" in row["Job Name"]] == ["ACT"]
    assert {row["Co"] for row in units} == {"00210"} and {row["District"] for row in units} == {"03"}
    elsewhere = [row for row in units if row["Related BU"] != row["Job Number"]]
    assert len(elsewhere) == 1 and elsewhere[0]["Related BU"].strip() == MAIN_UNIT
    assert elsewhere[0]["Related Bu Description"] != elsewhere[0]["Job Name"]
    assert sum(isinstance(row["Address Number"], int) for row in units) == 1
    assert all(row[f"Address Number{n}"] is None for row in units for n in range(1, 7))
    _, chart = _grid(folder / jde_exports.CHART_FILE)
    assert {row["Business Unit"] for row in chart} == set(numbers)
    for row in chart[:40]:  # the chart repeats its business unit's category codes
        unit = units[numbers.index(row["Business Unit"])]
        assert [row[f"BU Cat {n}"] for n in range(1, 51)] == [unit[name] for name in header[6:56]]


def test_the_supplier_master_has_its_quirks(small):
    truth, folder = small
    header, suppliers = _grid(folder / jde_exports.SUPPLIERS_FILE)
    assert header == jde_exports.SUPPLIERS_HEADER
    numbers = [row["Address Number"] for row in suppliers]
    assert all(isinstance(n, int) for n in numbers) and numbers == sorted(numbers) and len(numbers) == 40
    names = [row["Alpha Name"] for row in suppliers]
    assert all(0 < len(name) <= 40 for name in names) and len(set(names)) == len(names)
    assert any(name.endswith(" DNU") for name in names) and any("DO NOT USE" in name for name in names)
    assert any(name.endswith("*") for name in names) and any("  " in name for name in names)
    people = [name for name in names if re.fullmatch(r"[A-Z]+, [A-Z]+ [A-Z]\.", name)]
    assert len(people) == sum(s.kind == "employee" for s in truth.suppliers) == 10
    assert sum(s.kind == "affiliate" for s in truth.suppliers) == 2
    tax_ids = [row["Individual Tax ID"] for row in suppliers]
    assert tax_ids.count(" ") > len(tax_ids) * 0.75  # mostly blank
    filled = [value for value in tax_ids if value != " "]
    numeric = [value for value in filled if re.fullmatch(r"\d{9}RT0001|\d{9}|\d\d-\d{7}", value)]
    assert numeric and len(numeric) < len(filled)  # a few look like a tax number, the others are notes
    assert {row["Hold Payment Code"] for row in suppliers} == {"N", "Y", "0", "1", "2"}
    assert {row["Sch Typ"] for row in suppliers} == {"V"} and {row["Status"] for row in suppliers} == {" "}
    assert any(row["Long Address"] != " " for row in suppliers)  # the rest of a long name
    assert {v.supplier for v in truth.vouchers} <= set(numbers)


# --- the transaction journal -------------------------------------------------------------------


def test_the_journal_is_utf8_with_a_bom_and_crlf_and_starts_with_its_title_rows(small):
    truth, folder = small
    raw, rows = _journal(folder)
    assert raw.startswith(b"\xef\xbb\xbf") and raw.endswith(b"\r\n")
    assert raw.count(b"\n") == raw.count(b"\r\n") == len(rows)
    assert rows[0][0] == "R09321" and rows[0][8] == truth.company.group and len(rows[0]) == 20
    assert DATE.fullmatch(rows[0][18]) and re.fullmatch(r"\d\d:\d\d:\d\d", rows[0][19])
    assert rows[1][8] == "Transaction Journal" and rows[1][18:] == ["Page -", "1"]
    assert rows[2] == ["Company", "", truth.company.name]
    assert rows[3] == ["Tax ID", "", truth.company.tax_id]
    assert rows[4] == ["From", "", "", "Thru"]


def test_the_journals_header_is_split_over_two_rows_with_padded_names_and_spacer_columns(small):
    _, rows = _journal(small[1])
    assert rows[5] == ["Document"] + [""] * 14 + ["Address"]
    assert rows[6] == [
        "Number", "  Co", " G/L Date", " " * 23 + "Account Number", "", "", "", " " * 15 + "Description", "",
        " " * 6 + "Debit Amount", "", " " * 5 + "Credit Amount", "", " " * 13 + "Remark", "", " Number",
        " " * 19 + "Name",
    ]  # fmt: skip
    assert [len(name) for name in rows[6] if name] == [6, 4, 9, 37, 26, 18, 18, 19, 7, 23]
    at = jde_exports.JOURNAL_POSITIONS
    assert sorted(at.values()) == [0, 1, 2, 3, 7, 9, 11, 13, 15, 16]
    assert [n for n, name in enumerate(rows[6]) if name] == sorted(at.values())


def test_the_journals_data_starts_at_row_8_at_fixed_positions(small):
    truth, folder = small
    _, rows = _journal(folder)
    data = rows[7:-2]
    assert data[0][:2] == ["PV", "00210"]
    spacers = [n for n in range(17) if n not in jde_exports.JOURNAL_POSITIONS.values()]
    for row in data:
        assert row[-1] != "" and len(row) <= 17  # a printed row ends at its last filled cell
        assert all(row[n] == "" for n in spacers if n < len(row))
        assert row[:3] == ["", "", ""] or (row[0] == "PV" and row[1] == "00210" and DATE.fullmatch(row[2]))
        unit, _, rest = row[3].partition(".")
        assert len(unit) == 12 and unit == unit.strip().rjust(12) and re.fullmatch(r"\d{4}(\.[A-Z0-9]{1,8})?", rest)
        amounts = [row[n] for n in (9, 11) if n < len(row) and row[n]]
        assert amounts and all(AMOUNT.fullmatch(amount) for amount in amounts)
    assert len({len(row) for row in data}) > 1  # rows of different lengths
    assert sum(row[0] == "PV" for row in data) == len(truth.vouchers)
    printed = [row[n] for row in data for n in (9, 11) if n < len(row) and row[n]]
    assert any(amount.startswith(".") for amount in printed) and not any(a.startswith("0") for a in printed)
    documents = {str(v.document) for v in truth.vouchers}
    assert not any(cell in documents for row in data for cell in row[:15])  # no document number is printed
    days = {row[2] for row in data if row[0]}
    assert days == {f"{v.gl_date.month}/{v.gl_date.day}/{v.gl_date.year}" for v in truth.vouchers}
    assert [dt.datetime.strptime(row[2], "%m/%d/%Y") for row in data if row[0]] == sorted(
        dt.datetime.strptime(row[2], "%m/%d/%Y") for row in data if row[0]
    )


def test_the_journal_cuts_the_description_at_24_the_remark_at_30_and_the_name_at_26(small):
    truth, folder = small
    vouchers = _journal_vouchers(_journal(folder)[1])
    cut = Counter()
    for rows, voucher in zip(vouchers, truth.vouchers, strict=True):
        supplier = truth.supplier(voucher.supplier)
        for row, line in zip(rows, voucher.lines, strict=False):
            description = truth.account(line.account).description
            assert row[7] == description[:24] and row[13] == line.remark[:30] and len(line.remark) <= 30
            cut["description"] += len(description) > 24
            cut["remark"] += len(line.remark) == 30
        assert rows[-1][16] == supplier.name[:26] and rows[-1][15] == str(supplier.number)
        cut["name"] += len(supplier.name) > 26
    assert min(cut["description"], cut["remark"], cut["name"]) > 0


def test_a_journal_voucher_is_its_coding_lines_then_a_payable_and_a_gst_line_per_pay_item(small):
    truth, folder = small
    company = truth.company
    vouchers = _journal_vouchers(_journal(folder)[1])
    payable, intercompany = f"{MAIN_UNIT}.{company.payable_object}", f"{MAIN_UNIT}.{company.intercompany_object}"
    clearing = f"{MAIN_UNIT}.{company.gst_object}"
    assert truth.account(payable).posting_edit == truth.account(intercompany).posting_edit == "M"
    offsets = Counter()
    for rows, voucher in zip(vouchers, truth.vouchers, strict=True):
        accounts = [row[3].strip() for row in rows]
        coding = len(voucher.lines)
        assert accounts[:coding] == [line.account for line in voucher.lines]
        expected = []
        offset = intercompany if truth.supplier(voucher.supplier).kind == "affiliate" else payable
        for item in voucher.pay_items:
            expected += [offset, clearing] if item.tax else [offset]
        assert accounts[coding:] == expected
        offsets[offset] += 1
        if voucher.oddity == "unbalanced":
            continue
        debit = sum(Decimal(row[9] or "0") for row in rows)
        credit = sum(Decimal(row[11] or "0") for row in rows)
        assert debit == credit, voucher.document
        net = sum(Decimal(row[9] or "0") - Decimal(row[11] or "0") for row in rows[:coding])
        assert net == voucher.net and voucher.net + voucher.tax == voucher.gross
    assert offsets[payable] > offsets[intercompany] > 0
    assert not any(line.account.split(".")[1] == company.pst_object for v in truth.vouchers for line in v.lines)


def test_the_journal_has_a_few_odd_vouchers(small):
    truth, folder = small
    vouchers = dict(zip((v.document for v in truth.vouchers), _journal_vouchers(_journal(folder)[1]), strict=True))
    odd = defaultdict(list)
    for voucher in truth.vouchers:
        odd[voucher.oddity].append(voucher)
    assert set(odd) == {"", "negative", "unbalanced", "no_remarks", "no_address", "nine_lines", "under_a_dollar"}
    assert len(odd[""]) > 110
    unbalanced = vouchers[odd["unbalanced"][0].document]
    assert sum(Decimal(r[9] or "0") for r in unbalanced) != sum(Decimal(r[11] or "0") for r in unbalanced)
    assert all(row[9] == row[11] != "" for row in unbalanced[len(odd["unbalanced"][0].lines) :])
    negative = odd["negative"][0]
    assert negative.gross < 0 and all(row[9] == "" and row[11] for row in vouchers[negative.document][:1])
    assert vouchers[negative.document][len(negative.lines)][9] != ""  # the payable line is a debit
    assert all(row[13] == "" for v in odd["no_remarks"] for row in vouchers[v.document])
    assert len(odd["nine_lines"][0].lines) == 9
    assert vouchers[odd["under_a_dollar"][0].document][0][9].startswith(".")
    printed = _journal(folder)[1][7:-2]  # a line with no supplier number ends after its remark
    assert Counter(len(row) for row in printed).keys() == {14, 17}
    assert sum(len(row) == 14 for row in printed) == len(odd["no_address"][0].lines)


def test_the_journal_ends_with_the_company_total_and_the_grand_total(small):
    truth, folder = small
    _, rows = _journal(folder)
    data, company_total, grand_total = rows[7:-2], rows[-2], rows[-1]
    debit = sum(Decimal(row[9]) for row in data if row[9])
    credit = sum(Decimal(row[11]) for row in data if len(row) > 11 and row[11])
    assert len(company_total) == 84 and company_total[83] == truth.company.name
    assert [n for n, cell in enumerate(company_total) if cell] == [9, 11, 83]
    assert grand_total == [""] * 7 + ["Grand Total", "", company_total[9], "", company_total[11]]
    assert (Decimal(company_total[9]), Decimal(company_total[11])) == (debit, credit)
    assert debit != credit  # one voucher does not balance


# --- the voucher grids -------------------------------------------------------------------------


def test_the_voucher_headers_have_a_row_per_pay_item_and_a_totals_row(small):
    truth, folder = small
    cells = _cells(folder / jde_exports.HEADERS_FILE)
    header, rows = _grid(folder / jde_exports.HEADERS_FILE)
    assert header == jde_exports.HEADERS_HEADER
    items = [(voucher, item) for voucher in truth.vouchers for item in voucher.pay_items]
    assert len(rows) == len(items) + 1 > len(truth.vouchers) + 1
    totals = rows[-1]
    assert [name for name, value in totals.items() if value is not None] == ["Gross Amount", "Open Amount"]
    assert round(totals["Gross Amount"], 2) == float(sum(item.gross for _, item in items))
    assert round(totals["Open Amount"], 2) == round(sum(row["Open Amount"] or 0 for row in rows[:-1]), 2) > 0
    for row, (voucher, item) in zip(rows, items, strict=False):
        assert (row["Document Number"], row["Pay Item"], row["Doc Type"]) == (voucher.document, item.number, "PV")
        assert row["Doc Co"] == row["Co"] == "00210" and row["Batch Type"] == "V"
        assert isinstance(row["Supplier Number"], int) and isinstance(row["Batch Number"], int)
        assert re.fullmatch(r"00\d", row["Pay Item"]) and row["Business Unit"] == MAIN_UNIT.rjust(12)
        assert all(isinstance(row[name], dt.datetime) for name in ("Invoice Date", "G/L Date", "Due Date"))
        assert row["G/L Date"].date() == voucher.gl_date
        assert round(row["Gross Amount"], 2) == float(item.gross)
        assert row["Purchase Order"] == (voucher.purchase_order or " ")
        assert (row["PO Doc Type"], row["PO Document Type"]) in ((" ", None), ("OP", "Purchase Order"))
        assert (row["Foreign Amount"] is not None) == (row["Curr Code"] == "USD")
        assert (row["Open Amount"] is None) == (row["Pay Stat"] == "P")
        assert len(row["Supplier Number Desc"]) <= 30
    orders = {row["Purchase Order"] for row in rows[:-1]} - {" "}
    assert orders and all(re.fullmatch(r"0\d{7}", order) for order in orders)
    assert {row["Curr Code"] for row in rows[:-1]} == {"CAD", "USD"}
    assert {row["G/L Offset"] for row in rows[:-1]} == {" ", truth.company.intercompany_object}
    gross = header.index("Gross Amount")
    formats = {(abs(row[gross].value) >= 1_000, row[gross].number_format) for row in cells[1:-1]}
    assert formats == {(False, "General"), (True, "#,##0.00")} and cells[-1][gross].number_format == "#,##0.00"
    assert cells[1][header.index("G/L Date")].number_format == "MM/dd/yyyy"
    assert cells[1][header.index("Doc Co")].data_type == "s" and cells[1][0].data_type == "n"


def test_the_headers_gross_is_the_journals_payable_lines(small):
    truth, folder = small
    _, rows = _grid(folder / jde_exports.HEADERS_FILE)
    gross = defaultdict(Decimal)
    for row in rows[:-1]:
        gross[row["Document Number"]] += Decimal(str(row["Gross Amount"]))
    payables = (f"{MAIN_UNIT}.{truth.company.payable_object}", f"{MAIN_UNIT}.{truth.company.intercompany_object}")
    for journal, voucher in zip(_journal_vouchers(_journal(folder)[1]), truth.vouchers, strict=True):
        if voucher.oddity == "unbalanced":
            continue
        payable = sum(Decimal(r[11] or "0") - Decimal(r[9] or "0") for r in journal if r[3].strip() in payables)
        assert payable == gross[voucher.document] == voucher.gross


def test_a_vouchers_three_grids(small):
    truth, folder = small
    examples = jde_exports.example_vouchers(truth)
    assert set(examples) == {"split", "gst_only", "purchase_order"}
    assert len(examples["split"].business_units) > 1 and examples["purchase_order"].purchase_order
    for label, voucher in examples.items():
        header, lines = _grid(folder / f"voucher_{label}_gl_distribution.xlsx")
        assert header == jde_exports.DISTRIBUTION_HEADER
        entry = lines.pop()  # the blank entry row: only the G/L date (and the order)
        order = voucher.purchase_order
        filled = {name: value for name, value in entry.items() if value not in (None, " ")}
        assert set(filled) == {"G/L Date"} | ({"PO Doc Co", "PO Doc Type", "Purchase Order"} if order else set())
        assert [line["Account Number"] for line in lines] == [
            a.split(".")[0].rjust(12) + "." + a.split(".", 1)[1] for a in (x.account for x in voucher.lines)
        ]
        assert [Decimal(str(line["Amount"])) for line in lines] == [x.amount for x in voucher.lines]  # net
        assert [line["Explanation -Remark-"] for line in lines] == [x.remark or " " for x in voucher.lines]
        assert [line["JE Line Number"] for line in lines] == list(range(1, len(lines) + 1))
        assert all(line["Account Description"] == truth.account(x.account).description
                   for line, x in zip(lines, voucher.lines, strict=True))  # fmt: skip
        assert all(line["Alternate Home Business Unit"] == " " * 12 and line["LT"] == "AA" for line in lines)
        assert {line["Tax ExpCode"] for line in lines} == ({"V"} if order else {" "})

        header, items = _grid(folder / f"voucher_{label}_pay_items.xlsx")
        assert header == jde_exports.PAY_ITEMS_HEADER
        following = items.pop()  # the empty next pay item
        assert following["Pay Itm"] == f"{len(voucher.pay_items) + 1:03d}" and following["Gross Amount"] is None
        for row, item in zip(items, voucher.pay_items, strict=True):
            assert (row["Pay Itm"], row["Tax Ex"], row["Tax Rate/Area"]) == (item.number, "V", "CDNGST")
            assert Decimal(str(row["Gross Amount"])) == item.gross == item.taxable + item.tax
            assert Decimal(str(row["Taxable Amount"])) == item.taxable == Decimal(str(row["Amount To Distribute"]))
            assert Decimal(str(row["Tax Amount"])) == item.tax
            assert row["G/L Bank Account Number"].strip() == truth.company.bank_account
            assert isinstance(row["Payee Number"], int) and isinstance(row["Due Date"], dt.datetime)

        header, payments = _grid(folder / f"voucher_{label}_payment_history.xlsx")
        assert header == jde_exports.PAYMENTS_HEADER and len(payments) == len(voucher.pay_items)
        assert {row["Doc Type-M"] for row in payments} == {"PT"} and {row["Doc Type"] for row in payments} == {"PV"}
        assert [Decimal(str(row["Payment Amount"])) for row in payments] == [i.gross for i in voucher.pay_items]
    quebec = examples["purchase_order"]  # PST or QST stays inside the expense line; only the GST is on the pay item
    assert quebec.provincial_tax > 0 and quebec.tax < quebec.net * Decimal("0.05")


# --- the truth ---------------------------------------------------------------------------------


def test_the_truth_is_consistent(small):
    truth, _ = small
    plan = jde_company.SIZES["small"]
    assert (len(truth.business_units), len(truth.suppliers), len(truth.vouchers)) == (20, 40, 120)
    assert (plan.business_units, plan.chart_rows, plan.suppliers, plan.vouchers) == (20, 250, 40, 120)
    units = {unit.number: unit for unit in truth.business_units}
    suggestable = {account.string for account in truth.suggestable()}
    for voucher in truth.vouchers:
        supplier = truth.supplier(voucher.supplier)
        assert supplier.kind != "unused" and voucher.currency == supplier.currency
        assert bool(voucher.tax) == supplier.taxed and voucher.gross == voucher.net + voucher.tax
        assert [item.number for item in voucher.pay_items] == [f"{n:03d}" for n in range(1, len(voucher.pay_items) + 1)]
        assert all((item.tax_code, item.tax_area) == (("V", "CDNGST") if item.tax else ("", ""))
                   for item in voucher.pay_items)  # fmt: skip
        for line in voucher.lines:
            account = truth.account(line.account)
            assert account.postable and line.account in suggestable and line.account in supplier.accounts
            assert units[account.business_unit].status == "ACT" and len(line.remark) <= 30
            assert (line.foreign_amount is not None) == (voucher.currency == "USD")
    assert all(len(a.description) <= 30 for a in truth.accounts)
    assert jde_company.account_string(MAIN_UNIT, "2480", "320") in suggestable
    assert jde_company.account_string(MAIN_UNIT, truth.company.payable_object) not in suggestable


def test_the_same_seed_gives_the_same_bytes_and_another_seed_other_books(small, tmp_path):
    truth, folder = small
    again = jde_exports.write_all(tmp_path / "again")
    assert again == truth and truth == jde_company.build(1, "small")
    names = sorted(path.name for path in folder.iterdir())
    assert names == sorted(path.name for path in (tmp_path / "again").iterdir()) and len(names) == 14
    for name in names:
        assert (folder / name).read_bytes() == (tmp_path / "again" / name).read_bytes(), name
    other = jde_company.build(2, "small")
    assert other.vouchers != truth.vouchers and other.suppliers != truth.suppliers
    assert jde_exports.journal_csv(other) != (folder / jde_exports.JOURNAL_FILE).read_bytes()
    assert jde_exports.main([str(tmp_path / "by_hand"), "--seed", "2"]) == 0
    assert (tmp_path / "by_hand" / jde_exports.JOURNAL_FILE).read_bytes() == jde_exports.journal_csv(other)


def test_the_remarks_end_in_an_office_location_in_two_forms(small):
    truth, _ = small
    remarks = [line.remark for voucher in truth.vouchers for line in voucher.lines]
    rooms = [r for r in remarks if re.search(r" \d-\d-\d{3}$", r)]  # building-floor-room
    streets = [r for r in remarks if re.search(r" \d{2,4} [A-Z0-9][\w ]*$", r) and r not in rooms]  # a street address
    assert len(rooms) > 10 and len(streets) > 10 and len(rooms) + len(streets) < len(remarks)


def _shares(truth) -> dict[str, float]:
    """The coding mix of a set of books, as shares."""
    vouchers = truth.vouchers
    lines = [line for voucher in vouchers for line in voucher.lines]
    kinds = Counter(truth.supplier(voucher.supplier).kind for voucher in vouchers)
    by_supplier = defaultdict(list)
    for voucher in vouchers:
        by_supplier[voucher.supplier] += [line.account for line in voucher.lines]
    repeat = [accounts for accounts in by_supplier.values() if len(accounts) >= 2]
    several = [(line, truth.account(line.account)) for voucher in vouchers for line in voucher.lines
               if line.remark and len(truth.supplier(voucher.supplier).accounts) > 1]  # fmt: skip
    return {
        "one_line": sum(len(v.lines) == 1 for v in vouchers) / len(vouchers),
        "split": sum(len(v.business_units) > 1 for v in vouchers) / len(vouchers),
        "purchase_order": sum(bool(v.purchase_order) for v in vouchers) / len(vouchers),
        "usd": sum(v.currency == "USD" for v in vouchers) / len(vouchers),
        "gst": sum(bool(v.tax) for v in vouchers) / len(vouchers),
        "employee": kinds["employee"] / len(vouchers),
        "affiliate": kinds["affiliate"] / len(vouchers),
        "negative": sum(v.gross < 0 for v in vouchers) / len(vouchers),
        "balance_sheet": sum(truth.account(line.account).balance_sheet for line in lines) / len(lines),
        "blank_remark": sum(not line.remark for line in lines) / len(lines),
        "one_account": sum(len({a.split(".", 1)[1] for a in accounts}) == 1 for accounts in repeat) / len(repeat),
        "one_unit": sum(len({a.split(".")[0] for a in accounts}) == 1 for accounts in repeat) / len(repeat),
        "remark_tells": sum(any(p in line.remark for p in account.phrases) for line, account in several) / len(several),
    }


def test_a_years_books_have_the_measured_size_and_coding_mix():
    truth = jde_company.build(1, "year")
    assert (len(truth.business_units), len(truth.accounts), len(truth.suppliers), len(truth.vouchers)) == (
        130, 14000, 970, 5500)  # fmt: skip
    wanted = {"one_line": (0.73, 0.03), "split": (0.09, 0.025), "purchase_order": (0.06, 0.02), "usd": (0.13, 0.02),
              "gst": (0.61, 0.03), "employee": (0.19, 0.02), "affiliate": (0.13, 0.02), "negative": (0.006, 0.004),
              "balance_sheet": (0.27, 0.05), "blank_remark": (0.02, 0.01), "one_account": (0.44, 0.05),
              "one_unit": (0.58, 0.05), "remark_tells": (0.83, 0.08)}  # fmt: skip
    shares = _shares(truth)
    off = {name: round(shares[name], 3) for name, (share, margin) in wanted.items()
           if abs(shares[name] - share) > margin}  # fmt: skip
    assert not off, off
    months = Counter(voucher.gl_date.month for voucher in truth.vouchers)
    assert len(months) == 12 and 300 <= min(months.values()) and max(months.values()) <= 530
    assert max(len(voucher.lines) for voucher in truth.vouchers) == 130
    assert sum(v.oddity == "unbalanced" for v in truth.vouchers) == 33
    used = {line.account.split(".")[0] for voucher in truth.vouchers for line in voucher.lines}
    assert 65 <= len(used) <= 85 and {len(unit) for unit in used} == {8, 10}
    assert len({v.supplier for v in truth.vouchers}) == 950
    assert all(truth.account(line.account).postable for voucher in truth.vouchers for line in voucher.lines)


@pytest.mark.slow
def test_a_years_files_are_written_in_the_same_layouts(tmp_path):
    truth = jde_exports.write_all(tmp_path, size="year")
    chart = openpyxl.load_workbook(tmp_path / jde_exports.CHART_FILE, read_only=True)["Sheet1"]
    assert [len(row) for row in chart.iter_rows(values_only=True)] == [110] * 14001
    _, rows = _journal(tmp_path)
    assert sum(row[0] == "PV" for row in rows) == 5500 and rows[-1][7] == "Grand Total"
    big = next(v for v in truth.vouchers if v.oddity == "many_lines")
    assert len(big.lines) == 130 and len(big.business_units) > 50


# --- no value of a real company ----------------------------------------------------------------


def _deny_list() -> list[str]:
    """Values that must never be in the made-up books: read from a local file outside the repository, one value a
    line (``#`` starts a comment), named by ``AP_JDE_DENY_LIST`` or kept as ``~/ap_coder_jde_deny_list.txt``."""
    path = Path(os.environ.get("AP_JDE_DENY_LIST") or Path.home() / "ap_coder_jde_deny_list.txt")
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def test_no_denied_value_is_in_the_generated_files_or_the_modules(small):
    denied = _deny_list()
    if not denied:
        pytest.skip("no local deny list (AP_JDE_DENY_LIST or ~/ap_coder_jde_deny_list.txt)")
    _, folder = small
    texts = {}
    for path in sorted(folder.iterdir()):
        if path.suffix == ".csv":
            texts[path.name] = path.read_bytes().decode("utf-8-sig")
        else:
            texts[path.name] = "\n".join(
                str(cell.value) for row in _cells(path) for cell in row if cell.value is not None
            )
    for module in (jde_company, jde_exports, jde_names):
        texts[Path(module.__file__).name] = Path(module.__file__).read_text(encoding="utf-8")
    texts[Path(__file__).name] = Path(__file__).read_text(encoding="utf-8")
    found = set()
    for value in denied:  # as a whole word or number: "1234" is not found inside "41234567"
        whole = re.compile(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])")
        found |= {(name, value) for name, text in texts.items() if whole.search(text)}
    assert not found, sorted(found)
