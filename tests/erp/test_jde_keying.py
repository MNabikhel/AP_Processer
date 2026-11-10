"""The JD Edwards keying sheet (D-210): a block per voucher to read while typing it into Standard Voucher Entry, its
cells' values and types, the check line, the "Not ready" sheet, the CSV twin and the Exports page's download."""

import csv
import datetime as dt
import io

import openpyxl

from ap_coder.coding.tax import TaxTreatment
from ap_coder.core.memory import vendor_key
from ap_coder.erp import exports, jde, jde_keying
from tests.conftest import copy_database
from tests.erp.test_jde import _setup
from tests.erp.test_jde_gl_mode import coded, doc, line, tl

HQ, IT = "0312005561", "07004410"  # two business units, each with its leading zero
NAMES = {"6420.2210": "Data lines", "0042": "Office supplies", "6431": "Courier"}
SUPPLIERS = {"Prairie Office Supply Ltd.": "40417", "Boreal Telecom Inc.": "5512", "Lakehead Courier Inc.": "600233",
             "Cascade Software LLC": "71008"}  # fmt: skip
ON_DAY = dt.date(2026, 10, 9)


def lookup(final):
    return SUPPLIERS.get(final["vendor_name"], "")


def settings(**kw):
    """Tax amounts on the pay item, Quebec on a GST-only tax area: the QST is not recovered."""
    js = jde.JdeSettings(amount_mode=jde.AMOUNT_TAX, terms_codes={"Net 30": "N30"}, **kw)
    js.tax_map["QC"] = jde.TaxArea("GSTONLY", "V")
    return js


def one_line(inv_id=1, **kw):
    """Alberta, one line, GST."""
    d = doc([line(1, 240.0, "6420.2210", ["GST"], desc="Fibre line October", cc=HQ)],
            [tl("GST", "", 0.05, 240.0, 12.0)], prov="AB", vendor_name="Prairie Office Supply Ltd.",
            invoice_number="POS-3301", invoice_date="2026-10-02", payment_terms="Net 30", **kw)  # fmt: skip
    return coded(d, inv_id=inv_id)


def split(inv_id=2):
    """Ontario, three lines over two business units, HST; one object account has leading zeros and no subsidiary."""
    lines = [line(1, 100.0, "6420.2210", ["HST"], desc="Courier to the north office, three parcels overnight", cc=HQ),
             line(2, 200.0, "0042", ["HST"], desc="Toner", cc=HQ),
             line(3, 300.0, "6431", ["HST"], desc="Same-day courier", cc=IT)]  # fmt: skip
    d = doc(lines, [tl("HST", "ON", 0.13, 600.0, 78.0)], prov="ON", vendor_name="Lakehead Courier Inc.",
            invoice_number="LC-00918", invoice_date="2026-10-03")  # fmt: skip
    return coded(d, inv_id=inv_id)


def quebec(inv_id=3):
    """Quebec, the GST recovered and the QST left in the expense, with a PO number."""
    d = doc([line(1, 1240.0, "6420.2210", ["GST", "QST"], desc="Fibre line October", cc=IT)],
            [tl("GST", "", 0.05, 1240.0, 62.0), tl("QST", "QC", 0.09975, 1240.0, 123.69)], prov="QC",
            vendor_name="Boreal Telecom Inc.", invoice_number="BT-20417", invoice_date="2026-10-06",
            po_number="00052316")  # fmt: skip
    return coded(d, _setup(QST=TaxTreatment("QST", "expense_to_line")), inv_id)


def usd(inv_id=4):
    d = doc([line(1, 500.0, "6420.2210", desc="Licences", cc=HQ)], [], prov="ON", currency="USD",
            vendor_name="Cascade Software LLC", invoice_number="CS-77", invoice_date="2026-10-07")  # fmt: skip
    return coded(d, inv_id=inv_id)


def credit(inv_id=5):
    d = doc([line(1, -240.0, "6420.2210", ["GST"], desc="Credit for September", cc=HQ)],
            [tl("GST", "", 0.05, -240.0, -12.0)], prov="AB", vendor_name="Prairie Office Supply Ltd.",
            invoice_number="POS-CN-12", invoice_date="2026-10-08")  # fmt: skip
    return coded(d, inv_id=inv_id)


def unknown_supplier(inv_id=6):
    d = doc([line(1, 80.0, "6431", ["GST"], cc=HQ)], [tl("GST", "", 0.05, 80.0, 4.0)], prov="AB",
            vendor_name="Nobody Knows Ltd.", invoice_number="NK-1", invoice_date="2026-10-08")  # fmt: skip
    return coded(d, inv_id=inv_id)


def everything():
    return [one_line(), split(), quebec(), usd(), credit(), unknown_supplier()]


def workbook(invoices, js=None, **kw):
    data, name, mime = jde_keying.build(jde_keying.FORMAT, invoices, js or settings(), lookup, batch=7,
                                        gl_names=NAMES, on=ON_DAY, **kw)  # fmt: skip
    assert name == "ap_coder_jde_keying_7.xlsx" and mime.endswith("spreadsheetml.sheet")
    return openpyxl.load_workbook(io.BytesIO(data))


def blocks(ws):
    """The sheet's vouchers: each a list of rows of cells, split at the blank rows under the column names."""
    out, block = [], []
    for row in ws.iter_rows(min_row=2, max_col=4):
        if all(c.value is None for c in row):
            if block:
                out.append(block)
            block = []
        else:
            block.append(row)
    return out + ([block] if block else [])


def values(block):
    return [[c.value for c in row] for row in block]


def field(block, label):
    """The cell beside a pay item's label."""
    for row in block:
        for cell, beside in zip(row, row[1:], strict=False):
            if cell.value == label:
                return beside
    raise AssertionError(label)


def test_a_one_line_gst_voucher_is_a_block_of_the_pay_item_its_line_and_a_check():
    wb = workbook([one_line()])
    assert wb.sheetnames == [jde_keying.SHEET, jde_keying.NOT_READY]
    ws = wb[jde_keying.SHEET]
    assert [c.value for c in ws[1]] == ["Account number", "Account description", "Amount", "Explanation -Remark-"]
    (block,) = blocks(ws)
    assert values(block) == [
        ["Voucher 1 of 1", "AP Coder invoice 1", None, None],
        ["Supplier number", "40417", "Supplier name", "Prairie Office Supply Ltd."],
        ["Invoice number", "POS-3301", "PO number", None],
        ["Invoice date", dt.datetime(2026, 10, 2), "G/L date", dt.datetime(2026, 10, 2)],
        ["Gross amount", 252, "Currency", "CAD"],
        ["Payment terms", "N30", "Remark", "Fibre line October"],
        ["Tax explanation code", "V", "Tax rate/area", "AB-GST"],
        ["Taxable amount", 240, "Tax amount", 12],
        ["G/L distribution", None, None, None],
        [f"{HQ}.6420.2210", "Data lines", 240, "Fibre line October"],
        ["Check", "G/L lines + recovered tax = gross", 240, "240.00 + 12.00 = 252.00: balanced"],
    ]


def test_codes_are_text_cells_amounts_are_numbers_and_dates_are_dates():
    """A business unit, an object account, a PO number and a supplier number keep their leading zeros: each is a text
    cell (read back by its type), formatted as text so that typing in the cell keeps it one."""
    ws = workbook([one_line(), split(), quebec()])[jde_keying.SHEET]
    first, second, third = blocks(ws)
    po = field(third, "PO number")
    assert (po.value, po.data_type, po.number_format) == ("00052316", "s", "@")
    supplier = field(third, "Supplier number")
    assert (supplier.value, supplier.data_type, supplier.number_format) == ("5512", "s", "@")
    accounts = [row[0] for row in second[9:12]]
    assert [a.value for a in accounts] == [f"{HQ}.6420.2210", f"{HQ}.0042", f"{IT}.6431"]
    assert all(a.data_type == "s" and a.number_format == "@" for a in accounts)
    assert field(third, "Tax explanation code").data_type == field(third, "Tax rate/area").data_type == "s"
    for label in ("Gross amount", "Taxable amount", "Tax amount"):
        cell = field(first, label)
        assert cell.data_type == "n" and cell.number_format == "#,##0.00", label
    amount = second[9][2]
    assert (amount.value, amount.data_type, amount.number_format) == (100, "n", "#,##0.00")
    for label in ("Invoice date", "G/L date"):
        cell = field(first, label)
        assert cell.is_date and cell.value == dt.datetime(2026, 10, 2) and cell.number_format == "yyyy-mm-dd"


def test_a_voucher_split_across_two_business_units_has_a_line_each_with_its_remark_cut_at_30():
    (block,) = blocks(workbook([split()])[jde_keying.SHEET])
    assert values(block)[9:] == [
        [f"{HQ}.6420.2210", "Data lines", 100, "Courier to the north office, t"],
        [f"{HQ}.0042", "Office supplies", 200, "Toner"],
        [f"{IT}.6431", "Courier", 300, "Same-day courier"],
        ["Check", "G/L lines + recovered tax = gross", 600, "600.00 + 78.00 = 678.00: balanced"],
    ]
    assert field(block, "Remark").value == "Courier to the north office, t"  # the first line's
    assert (field(block, "Taxable amount").value, field(block, "Tax amount").value) == (600, 78)
    assert (field(block, "Tax explanation code").value, field(block, "Tax rate/area").value) == ("V", "ON-HST")


def test_quebec_with_the_qst_in_the_expense_shows_the_gst_on_the_pay_item_and_the_qst_in_the_line():
    (block,) = blocks(workbook([quebec()])[jde_keying.SHEET])
    assert (field(block, "Gross amount").value, field(block, "Taxable amount").value) == (1425.69, 1240)
    assert (field(block, "Tax amount").value, field(block, "Tax rate/area").value) == (62, "GSTONLY")
    assert values(block)[9:] == [
        [f"{IT}.6420.2210", "Data lines", 1363.69, "Fibre line October"],  # 1,240.00 and its QST 123.69
        ["Check", "G/L lines + recovered tax = gross", 1363.69, "1,363.69 + 62.00 = 1,425.69: balanced"],
    ]


def test_a_usd_voucher_and_a_credit_note():
    foreign, minus = blocks(workbook([usd(), credit()])[jde_keying.SHEET])
    assert (field(foreign, "Currency").value, field(foreign, "Gross amount").value) == ("USD", 500)
    assert field(foreign, "Taxable amount").value is None and field(foreign, "Tax amount").value is None  # no tax
    assert field(foreign, "Tax explanation code").value == "E"
    assert values(foreign)[-1][2:] == [500, "500.00 + 0.00 = 500.00: balanced"]
    assert (field(minus, "Gross amount").value, field(minus, "Taxable amount").value) == (-252, -240)
    assert field(minus, "Tax amount").value == -12 and field(minus, "Payment terms").value is None  # a credit: no terms
    assert values(minus)[-2:] == [
        [f"{HQ}.6420.2210", "Data lines", -240, "Credit for September"],
        ["Check", "G/L lines + recovered tax = gross", -240, "-240.00 + -12.00 = -252.00: balanced"],
    ]


def test_the_tax_coded_to_gl_accounts_is_a_line_of_its_own_and_no_tax_on_the_pay_item():
    """With JD Edwards' Amounts set to the tax on G/L lines (D-93), the sheet says the same as the batch files."""
    js = jde.JdeSettings(default_bu="0000900")  # the tax account's business unit
    (block,) = blocks(workbook([one_line()], js)[jde_keying.SHEET])
    assert field(block, "Tax explanation code").value == "E" and field(block, "Tax rate/area").value is None
    assert field(block, "Taxable amount").value is None
    assert values(block)[9:] == [
        [f"{HQ}.6420.2210", "Data lines", 240, "Fibre line October"],
        ["0000900.2310", None, 12, "GST 5% (recoverable)"],
        ["Check", "G/L lines + recovered tax = gross", 252, "252.00 + 0.00 = 252.00: balanced"],
    ]


def test_an_invoice_a_jde_check_holds_is_on_the_not_ready_sheet_with_why():
    wb = workbook(everything(), left_out={5: ["already posted to JD Edwards through the API"]})
    ready = blocks(wb[jde_keying.SHEET])
    assert [b[0][0].value for b in ready] == [f"Voucher {n} of 4" for n in (1, 2, 3, 4)]
    assert [b[0][1].value for b in ready] == [f"AP Coder invoice {n}" for n in (1, 2, 3, 4)]
    held = [[c.value for c in row] for row in wb[jde_keying.NOT_READY].iter_rows()]
    assert held[0] == ["AP Coder invoice", "Supplier number", "Supplier name", "Invoice number", "Invoice date",
                       "Gross amount", "Currency", "Why it is not ready"]  # fmt: skip
    assert held[1] == [5, "40417", "Prairie Office Supply Ltd.", "POS-CN-12", dt.datetime(2026, 10, 8), -252, "CAD",
                       "already posted to JD Edwards through the API"]  # fmt: skip
    assert held[2][:7] == [6, None, "Nobody Knows Ltd.", "NK-1", dt.datetime(2026, 10, 8), 84, "CAD"]
    assert "no JDE supplier number" in held[2][7]
    assert wb[jde_keying.NOT_READY]["B2"].data_type == "s"
    # Every invoice is on one sheet or the other: none is dropped.
    assert len(ready) + len(held) - 1 == len(everything())


def test_the_sheet_prints_with_its_column_names_on_each_page_and_no_voucher_cut_by_a_page():
    many = [one_line(n) for n in range(1, 9)] + [split(9), one_line(10)]
    ws = workbook(many)[jde_keying.SHEET]
    assert ws.freeze_panes == "A2" and ws.print_title_rows == "$1:$1"
    assert ws.page_setup.orientation == "portrait" and ws.sheet_properties.pageSetUpPr.fitToPage
    assert (ws.page_setup.fitToWidth, ws.page_setup.fitToHeight) == (1, 0)
    assert [ws.column_dimensions[c].width for c in "ABCD"] == jde_keying.WIDTHS
    breaks = [b.id for b in ws.row_breaks.brk]
    assert breaks  # ten vouchers do not fit a page
    starts = [block[0][0].row for block in blocks(ws)]
    assert all(row + 1 in starts for row in breaks)  # a page ends on the blank row above a voucher
    page_ends = [1, *breaks, ws.max_row]
    assert all(end - start <= jde_keying.ROWS_A_PAGE for start, end in zip(page_ends, page_ends[1:], strict=False))


def long_voucher(inv_id, count=130):
    """Alberta, 130 lines: more than a printed page holds."""
    lines = [line(n, 10.0, "6431", ["GST"], desc=f"Parcel {n}", cc=HQ) for n in range(1, count + 1)]
    taxes = [tl("GST", "", 0.05, 10.0 * count, 0.5 * count)]
    d = doc(lines, taxes, prov="AB", vendor_name="Lakehead Courier Inc.", invoice_number=f"LC-{inv_id:05}",
            invoice_date="2026-10-05")  # fmt: skip
    return coded(d, inv_id=inv_id)


def test_no_stretch_between_page_breaks_is_longer_than_a_page_whatever_the_vouchers():
    """B-2147: the count of rows on the printed page wrapped back to nothing without a page break being written
    (a voucher ending on the page's last rows), so the next vouchers ran on and a page ended inside one. A voucher
    that fits a page is never cut; one longer than a page is cut between G/L lines only, its fields said again."""
    many = [split(n) if n % 3 == 0 else one_line(n) for n in range(1, 40)]
    many += [long_voucher(40), one_line(41), split(42)]
    ws = workbook(many)[jde_keying.SHEET]
    breaks = sorted(b.id for b in ws.row_breaks.brk)
    page_ends = [1, *breaks, ws.max_row]
    assert max(end - start for start, end in zip(page_ends, page_ends[1:], strict=False)) <= jde_keying.ROWS_A_PAGE
    found = blocks(ws)
    assert len(found) == 42
    for block in found:
        first, last = block[0][0].row, block[-1][0].row
        inside = [b for b in breaks if first <= b < last]
        if block is not found[39]:
            assert not inside, block[0][0].value  # a voucher that fits a page is on one page
            continue
        assert len(inside) >= 3  # 130 lines: four pages or more
        rows = {row[0].row: row for row in block}
        for b in inside:  # each page ends on a G/L line and the next begins with the voucher's fields again
            assert rows[b][0].value == f"{HQ}.6431"
            assert [c.value for c in rows[b + 1][:2]] == ["Voucher 40 of 42, continued", "AP Coder invoice 40"]
            assert [c.value for c in rows[b + 2][:2]] == ["Supplier number", "600233"]
        assert [row[3].value for row in block if row[0].value == f"{HQ}.6431"] == [f"Parcel {n}" for n in range(1, 131)]
        assert [row[0].value for row in block].count("Check") == 1 and block[-1][0].value == "Check"
        assert [row[0].value for row in block].count("Voucher 40 of 42") == 1


def test_text_that_looks_like_a_formula_is_kept_as_text():
    final = {k: v for k, v in one_line()["final_output"].items() if k != "gl_distribution"}
    final["line_items"][0]["description"] = "+1 800 555 0100"
    inv = coded({**final, "vendor_name": "=HYPERLINK(1)"})
    data, _, _ = jde_keying.build(jde_keying.FORMAT, [inv], settings(), lambda final: "40417", gl_names=NAMES)
    (block,) = blocks(openpyxl.load_workbook(io.BytesIO(data))[jde_keying.SHEET])
    name, remark = field(block, "Supplier name"), block[9][3]
    assert (name.value, name.data_type, name.quotePrefix) == ("=HYPERLINK(1)", "s", True)
    assert (remark.value, remark.data_type, remark.quotePrefix) == ("+1 800 555 0100", "s", True)


def test_the_csv_twin_has_a_row_per_gl_line_with_its_vouchers_fields():
    data, name, mime = jde_keying.build(jde_keying.CSV_FORMAT, everything(), settings(), lookup, batch=7,
                                        gl_names=NAMES, on=ON_DAY)  # fmt: skip
    assert (name, mime) == ("ap_coder_jde_keying_7.csv", "text/csv") and data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert list(rows[0]) == jde_keying.CSV_COLUMNS
    assert [(r["Voucher"], r["Line"], r["Account number"], r["Amount"]) for r in rows[:5]] == [
        ("1", "1", f"{HQ}.6420.2210", "240.00"),
        ("2", "1", f"{HQ}.6420.2210", "100.00"), ("2", "2", f"{HQ}.0042", "200.00"), ("2", "3", f"{IT}.6431", "300.00"),
        ("3", "1", f"{IT}.6420.2210", "1363.69"),
    ]  # fmt: skip
    third = rows[4]
    assert (third["Supplier number"], third["PO number"], third["Invoice date"], third["G/L date"]) == (
        "5512", "00052316", "2026-10-06", "2026-10-06")  # fmt: skip
    assert (third["Gross amount"], third["Taxable amount"], third["Tax amount"], third["Recovered tax"]) == (
        "1425.69", "1240.00", "62.00", "62.00")  # fmt: skip
    assert (third["Tax explanation code"], third["Tax rate/area"]) == ("V", "GSTONLY")
    assert third["Remark"] == "Fibre line October"
    assert third["Explanation -Remark-"] == "Fibre line October" and third["Not ready"] == ""
    (held,) = [r for r in rows if r["Not ready"]]
    assert (held["AP Coder invoice"], held["Voucher"], held["Account number"]) == ("6", "", "")
    assert "no JDE supplier number" in held["Not ready"]


def test_the_same_invoices_give_the_same_sheet_every_time():
    def cells(wb):
        return [[(c.value, c.data_type, c.number_format) for c in row] for ws in wb for row in ws.iter_rows()]

    assert cells(workbook(everything())) == cells(workbook(everything()))
    twice = [jde_keying.build(jde_keying.CSV_FORMAT, everything(), settings(), lookup, batch=7, gl_names=NAMES,
                              on=ON_DAY)[0] for _ in range(2)]  # fmt: skip
    assert twice[0] == twice[1]
    # The G/L date of the export day is the batch's day, not the day the sheet is downloaded again.
    (block,) = blocks(workbook([one_line()], settings(gl_date=jde.GL_DATE_EXPORT))[jde_keying.SHEET])
    assert field(block, "G/L date").value == dt.datetime(2026, 10, 9)


def test_the_sheet_and_the_batch_files_say_the_same():
    """The sheet's figures are the voucher's own: what the Z-files of the same invoices hold."""
    js = settings()
    invoices = [one_line(), split(), quebec(), usd(), credit()]
    files = jde.export_batch(invoices, js, lookup, batch=7, on=ON_DAY)
    heads = list(csv.DictReader(io.StringIO(files[jde.HEADER_FILE].decode())))
    dist = list(csv.DictReader(io.StringIO(files[jde.DIST_FILE].decode())))
    ready, held = jde_keying.vouchers(invoices, js, lookup, gl_names=NAMES, on=ON_DAY)
    assert not held
    for v, head in zip(ready, heads, strict=True):
        cents = lambda x: str(round((x or 0) * 100))  # noqa: E731
        foreign = v.currency != "CAD"
        tax = (head["VLCTXA"], head["VLCTAM"]) if foreign else (head["VLATXA"], head["VLSTAM"])
        assert tax == (cents(v.taxable), cents(v.tax))
        assert (head["VLAN8"], head["VLVINV"], head["VLEXR1"], head["VLTXA1"]) == (
            v.supplier_number, v.invoice_number, v.tax_code, v.tax_area)  # fmt: skip
        assert head["VLDGJ"] == str(jde.julian(v.gl_date)) and head["VLPO"] == v.po_number
    typed = [(li.account, cents(li.amount), li.remark) for v in ready for li in v.lines]
    assert typed == [(r["VNANI"], r["VNAA"] or r["VNACR"], r["VNEXA"]) for r in dist]


def demo(tmp_path, template):
    """The demo's two approved invoices on a copy of ``cost_centers_demo_template``, the first ready for JD Edwards
    and the second held back (its supplier number is not an address number); the dashboard on it."""
    from tests.webclient import client_for

    store = copy_database(template, tmp_path / "private" / "ap_coder.db")
    ready = store.unexported_approved()
    northwind, chinook = (next(i for i in ready if i["vendor_name"].startswith(name)) for name in ("North", "Chinook"))
    assert len(ready) == 2
    js = jde.load(store)
    js.default_bu = "HQ"  # the tax account's business unit (the tax coded to G/L accounts: D-93)
    js.an8_overrides = {vendor_key(chinook["vendor_name"]): "C-41"}
    jde.save(store, js)
    return store, client_for(store), northwind, chinook


def test_the_exports_page_offers_the_keying_sheet_and_downloads_it_for_a_batch(tmp_path, cost_centers_demo_template):
    from tests.webclient import follow, post

    store, client, northwind, chinook = demo(tmp_path, cost_centers_demo_template)
    page = client.get(f"/exports?fmt={jde_keying.FORMAT}").text
    assert "JD Edwards keying sheet</a>" in page and exports.FORMATS[jde_keying.FORMAT].split(":")[0] in page
    text = follow(client, post(client, "/exports/create", {"fmt": jde_keying.FORMAT, "ids": [northwind["id"]]}))
    (batch,) = store.export_batches()
    assert batch["format"] == jde_keying.FORMAT and store.batch_invoice_ids(batch["id"]) == [northwind["id"]]
    assert "Batch 1 created with 1 invoice. Download it below." in text and "not ready for JD Edwards" not in text
    assert "while typing each voucher into JD Edwards" in text and "Import this file into your ERP" not in text
    assert f"/exports/batches/{batch['id']}/download?fmt={jde_keying.FORMAT}" in text
    answer = client.get(f"/exports/batches/{batch['id']}/download?fmt={jde_keying.FORMAT}")
    assert answer.status_code == 200 and f"ap_coder_jde_keying_{batch['id']}.xlsx" in answer.headers[
        "content-disposition"]  # fmt: skip
    wb = openpyxl.load_workbook(io.BytesIO(answer.content))
    (block,) = blocks(wb[jde_keying.SHEET])
    assert field(block, "Supplier number").value == "10023"
    assert field(block, "Supplier name").value == northwind["vendor_name"]
    assert any(str(row[0].value).startswith("CC400.") and row[1].value for row in block)  # an account, described
    assert block[-1][0].value == "Check" and block[-1][3].value.endswith(": balanced")
    assert list(wb[jde_keying.NOT_READY].iter_rows(min_row=2, values_only=True)) == []


def test_only_invoices_ready_for_jd_edwards_go_on_a_keying_batch_and_the_page_says_which_are_not(
    tmp_path, cost_centers_demo_template
):
    """B-2148: a keying batch took every ticked invoice, one that cannot be typed too (it left Ready to export,
    marked exported, with only the workbook's second sheet to say so), and a batch with nothing to type could be
    made. As for a JD Edwards batch, the page lists what is not ready before the batch is made, each with why and
    its invoice to open; only the ready ones go on the batch, and a batch of nothing is refused."""
    from tests.webclient import follow, post

    store, client, northwind, chinook = demo(tmp_path, cost_centers_demo_template)
    page = client.get(f"/exports?fmt={jde_keying.FORMAT}").text
    assert "1 invoice cannot go to JD Edwards yet and is left out of the" in page
    assert "is not a JDE address number" in page and f'<a href="/review/{chinook["id"]}"' in page
    held_box = page.split(f'name="ids" value="{chinook["id"]}"', 1)[1].split(">", 1)[0]
    assert "disabled" in held_box and "Export 1 invoice" in page
    # The held one alone: no batch of nothing.
    answer = post(client, "/exports/create", {"fmt": jde_keying.FORMAT, "ids": [chinook["id"]]})
    assert (
        answer.status_code == 400 and "None of the ticked invoices can go to JD Edwards yet" in answer.json()["error"]
    )
    assert store.export_batches() == []
    # Both ticked (a page drawn before the supplier number went wrong): the ready one goes, the other stays.
    both = {"fmt": jde_keying.FORMAT, "ids": [northwind["id"], chinook["id"]]}
    text = follow(client, post(client, "/exports/create", both))
    (batch,) = store.export_batches()
    assert store.batch_invoice_ids(batch["id"]) == [northwind["id"]]
    assert [i["id"] for i in store.unexported_approved()] == [chinook["id"]]
    assert "Batch 1 created with 1 invoice." in text
    assert "1 ticked invoice is not ready for JD Edwards and stays in Ready to export" in text
    wb = openpyxl.load_workbook(io.BytesIO(client.get(f"/exports/batches/{batch['id']}/download").content))
    assert len(blocks(wb[jde_keying.SHEET])) == 1 and wb[jde_keying.NOT_READY].max_row == 1


def test_an_invoice_on_a_keying_sheet_says_so_and_undoing_the_batch_puts_it_back(tmp_path, cost_centers_demo_template):
    """An invoice on a keying sheet is not in JD Edwards until someone types it: where its export is said, it is "on
    keying sheet 1", not "exported"; the batch is undone like any other."""
    from ap_coder.web.pages.review import exported_reason
    from ap_coder.web.pages.statements import ap_status
    from tests.webclient import follow, post

    store, client, northwind, chinook = demo(tmp_path, cost_centers_demo_template)
    follow(client, post(client, "/exports/create", {"fmt": jde_keying.FORMAT, "ids": [northwind["id"]]}))
    assert jde_keying.keying_batches(store) == {1}
    page = client.get(f"/review/{northwind['id']}").text
    assert "On keying sheet 1 (to type into JD Edwards)" in page and "Exported in batch" not in page
    inv = store.get_invoice(northwind["id"])
    assert exported_reason(store, inv) == "it is on keying sheet 1: undo the batch on the Exports page first"
    answer = post(client, f"/review/{northwind['id']}/undo-approve")
    assert answer.status_code == 400 and "it is on keying sheet 1" in answer.json()["error"]
    line = type("Line", (), {"invoice_id": inv["id"], "ap_status": "approved"})()
    assert ap_status(line, {inv["id"]: inv}, {1}) == "On keying sheet 1"
    assert ap_status(line, {inv["id"]: inv}) == "Exported (batch 1)"
    exports_page = client.get("/exports?batch=1").text
    assert "On keying sheet" in exports_page
    # Undone: back in Ready to export, and no longer on a sheet.
    text = follow(client, post(client, "/exports/batches/1/undo"))
    assert "Batch 1 undone" in text
    assert northwind["id"] in [i["id"] for i in store.unexported_approved()]
    assert jde_keying.keying_batches(store) == set()
    assert "On keying sheet" not in client.get(f"/review/{northwind['id']}").text


def test_a_keying_batchs_sheet_downloaded_again_keeps_its_vouchers_once_they_are_in_the_erp_register(
    tmp_path, cost_centers_demo_template
):
    """B-2149: the vouchers typed from a keying sheet come back in the ERP's invoice register (the voucher list
    imported): the same batch's sheet then listed each as "Not ready: already in the ERP", with nothing to type.
    What was keyed is the batch's record: its own vouchers are not checked against the register, as a JD Edwards
    batch's are not."""
    from tests.webclient import follow, post

    store, client, northwind, chinook = demo(tmp_path, cost_centers_demo_template)
    follow(client, post(client, "/exports/create", {"fmt": jde_keying.FORMAT, "ids": [northwind["id"]]}))

    def sheet():
        wb = openpyxl.load_workbook(io.BytesIO(client.get("/exports/batches/1/download").content))
        return ([values(b) for b in blocks(wb[jde_keying.SHEET])],
                list(wb[jde_keying.NOT_READY].iter_rows(min_row=2, values_only=True)))  # fmt: skip

    first = sheet()
    assert len(first[0]) == 1 and first[1] == []
    final = store.get_invoice(northwind["id"])["final_output"]
    store.import_erp_register([{"vendor_name": final["vendor_name"], "invoice_number": final["invoice_number"],
                                "total": final["grand_total"]}])  # fmt: skip
    assert sheet() == first
    # Another batch's sheet (made as Excel) is still checked against the register: that one is in the ERP already.
    follow(client, post(client, "/exports/batches/1/undo"))
    follow(client, post(client, "/exports/create", {"fmt": "xlsx", "ids": [northwind["id"]]}))
    wb = openpyxl.load_workbook(
        io.BytesIO(client.get(f"/exports/batches/2/download?fmt={jde_keying.FORMAT}").content))  # fmt: skip
    assert blocks(wb[jde_keying.SHEET]) == []
    assert "already in the ERP" in wb[jde_keying.NOT_READY]["H2"].value


def test_the_keying_sheet_is_offered_only_where_jd_edwards_is_shown(store):
    from ap_coder.web.pages import exports as exports_page

    store.set_setting(jde.SHOW_SETTING, "0")
    assert not {jde.FORMAT, jde_keying.FORMAT, jde_keying.CSV_FORMAT} & set(exports_page.shown_formats(store))
    store.set_setting(jde.SHOW_SETTING, "1")
    assert {jde.FORMAT, jde_keying.FORMAT, jde_keying.CSV_FORMAT} <= set(exports_page.shown_formats(store))
