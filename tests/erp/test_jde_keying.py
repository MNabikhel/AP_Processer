"""The JD Edwards keying sheet (D-210): a block per voucher to read while typing it into Standard Voucher Entry, its
cells' values and types, the check line and what is said under it, the printed pages, the "Not ready" sheet and the
Exports page's batches of it."""

import csv
import datetime as dt
import io

import openpyxl

from ap_coder.coding.tax import TaxTreatment
from ap_coder.core.memory import vendor_key
from ap_coder.erp import exports, jde_formats, jde_keying, jde_prototype, jde_settings, jde_zfiles
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
    js = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX, terms_codes={"Net 30": "N30"}, **kw)
    js.tax_map["QC"] = jde_settings.TaxArea("GSTONLY", "V")
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
    data, name, mime = jde_keying.build(invoices, js or settings(), lookup, batch=7, gl_names=NAMES, on=ON_DAY, **kw)
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


LINES = 10  # a block's rows above its G/L lines: the title, eight rows of fields and "G/L distribution"


def lines_and_check(block):
    """The G/L lines and the check line of a block, as values."""
    rows = values(block)[LINES:]
    return rows[: [r[0] for r in rows].index("Check") + 1]


def said(block, label):
    """The warnings or notes under a block's check line, each as one text."""
    out: list[str] = []
    for row in values(block):
        if row[0] == label:
            out.append(row[1])
        elif out and row[0] is None and row[1]:
            out[-1] += " " + row[1]
    return out


def test_a_one_line_gst_voucher_is_a_block_of_the_pay_item_its_line_and_a_check():
    wb = workbook([one_line()])
    assert wb.sheetnames == [jde_keying.SHEET, jde_keying.NOT_READY]
    ws = wb[jde_keying.SHEET]
    assert [c.value for c in ws[1]] == ["Account number", "Account description", "Amount", "Explanation -Remark-"]
    (block,) = blocks(ws)
    assert values(block) == [
        ["Voucher 1 of 1", "AP Coder invoice 1", None, None],
        ["Supplier number", "40417", "Supplier name", "Prairie Office Supply Ltd."],
        ["Company", "00001", "Invoice number", "POS-3301"],
        ["Invoice date", dt.datetime(2026, 10, 2), "G/L date", dt.datetime(2026, 10, 2)],
        ["Currency", "CAD", "Gross amount", 252],
        ["Payment terms", "N30", "Remark", "Fibre line October"],
        ["Due date", dt.datetime(2026, 11, 1), "PO number", None],
        ["Tax explanation code", "V", "Tax rate/area", "AB-GST"],
        ["Tax amount", 12, "Taxable amount", 240],
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
    accounts = [row[0] for row in second[LINES : LINES + 3]]
    assert [a.value for a in accounts] == [f"{HQ}.6420.2210", f"{HQ}.0042", f"{IT}.6431"]
    assert all(a.data_type == "s" and a.number_format == "@" for a in accounts)
    assert field(third, "Tax explanation code").data_type == field(third, "Tax rate/area").data_type == "s"
    for label in ("Gross amount", "Taxable amount", "Tax amount"):
        cell = field(first, label)
        assert cell.data_type == "n" and cell.number_format == "#,##0.00", label
    amount = second[LINES][2]
    assert (amount.value, amount.data_type, amount.number_format) == (100, "n", "#,##0.00")
    for label in ("Invoice date", "G/L date"):
        cell = field(first, label)
        assert cell.is_date and cell.value == dt.datetime(2026, 10, 2) and cell.number_format == "yyyy-mm-dd"
    company = field(first, "Company")
    assert (company.value, company.data_type, company.number_format) == ("00001", "s", "@")


def test_a_voucher_split_across_two_business_units_has_a_line_each_with_its_remark_cut_at_30():
    (block,) = blocks(workbook([split()])[jde_keying.SHEET])
    assert lines_and_check(block) == [
        [f"{HQ}.6420.2210", "Data lines", 100, "Courier to the north office, t"],
        [f"{HQ}.0042", "Office supplies", 200, "Toner"],
        [f"{IT}.6431", "Courier", 300, "Same-day courier"],
        ["Check", "G/L lines + recovered tax = gross", 600, "600.00 + 78.00 = 678.00: balanced"],
    ]
    assert field(block, "Remark").value == "Courier to the north office, t"  # the first line's
    assert (field(block, "Taxable amount").value, field(block, "Tax amount").value) == (600, 78)
    assert (field(block, "Tax explanation code").value, field(block, "Tax rate/area").value) == ("V", "ON-HST")


def test_a_line_with_a_remark_of_its_own_is_on_the_sheet_with_it():
    """D-213: the "Explanation -Remark-" column and the pay item's Remark carry the line's remark, fitted to 30
    characters at a word end; a line without one keeps its description cut at 30."""
    invoice = split()
    lines = invoice["final_output"]["line_items"]
    lines[0].update(remark="KT Oct.parcels 3-1-204", remark_from="person")
    lines[2].update(remark="same-day courier for the north office", remark_from="person")
    (block,) = blocks(workbook([invoice])[jde_keying.SHEET])
    assert [row[3] for row in lines_and_check(block)[:3]] == [
        "KT Oct.parcels 3-1-204", "Toner", "same-day courier for the north"]  # fmt: skip
    assert field(block, "Remark").value == "KT Oct.parcels 3-1-204"  # the first line's
    files = jde_zfiles.export_batch([invoice], settings(remarks=jde_settings.REMARKS_TYPED), lookup, batch=7, on=ON_DAY)
    dist = list(csv.DictReader(io.StringIO(files[jde_zfiles.DIST_FILE].decode())))
    assert [r["VNEXR"] for r in dist] == [row[3] for row in lines_and_check(block)[:3]]


def test_quebec_with_the_qst_in_the_expense_shows_the_gst_on_the_pay_item_and_the_qst_in_the_line():
    (block,) = blocks(workbook([quebec()])[jde_keying.SHEET])
    assert (field(block, "Gross amount").value, field(block, "Taxable amount").value) == (1425.69, 1240)
    assert (field(block, "Tax amount").value, field(block, "Tax rate/area").value) == (62, "GSTONLY")
    assert lines_and_check(block) == [
        [f"{IT}.6420.2210", "Data lines", 1363.69, "Fibre line October"],  # 1,240.00 and its QST 123.69
        ["Check", "G/L lines + recovered tax = gross", 1363.69, "1,363.69 + 62.00 = 1,425.69: balanced"],
    ]


def test_a_usd_voucher_and_a_credit_note():
    foreign, minus = blocks(workbook([usd(), credit()])[jde_keying.SHEET])
    assert (field(foreign, "Currency").value, field(foreign, "Gross amount").value) == ("USD", 500)
    assert field(foreign, "Taxable amount").value is None and field(foreign, "Tax amount").value is None  # no tax
    assert field(foreign, "Tax explanation code").value == "E"
    assert lines_and_check(foreign)[-1][2:] == [500, "500.00 + 0.00 = 500.00: balanced"]
    assert (field(minus, "Gross amount").value, field(minus, "Taxable amount").value) == (-252, -240)
    assert field(minus, "Tax amount").value == -12 and field(minus, "Payment terms").value == "none (a credit)"
    assert field(minus, "Due date").value == dt.datetime(2026, 10, 8)  # a credit: its own date
    assert lines_and_check(minus) == [
        [f"{HQ}.6420.2210", "Data lines", -240, "Credit for September"],
        ["Check", "G/L lines + recovered tax = gross", -240, "-240.00 + -12.00 = -252.00: balanced"],
    ]


def meal(inv_id=7):
    """Alberta: a meal and a room, GST on both; half the GST on the meal is not claimed (D-90)."""
    from tests.erp.test_jde_gl_mode import _setup as with_meals

    lines = [line(1, 100.0, "6300", ["GST"], desc="Team lunch", cc=HQ),
             line(2, 100.0, "6431", ["GST"], desc="Room", cc=HQ)]  # fmt: skip
    d = doc(lines, [tl("GST", "", 0.05, 200.0, 10.0)], prov="AB", vendor_name="Prairie Office Supply Ltd.",
            invoice_number="POS-3340", invoice_date="2026-10-07")  # fmt: skip
    return coded(d, with_meals(meals=["6300"]), inv_id)


def with_pst(inv_id=8):
    """British Columbia: GST recovered, the PST in the expense line (code C)."""
    d = doc([line(1, 100.0, "6431", ["GST", "PST"], desc="Parcels", cc=HQ)],
            [tl("GST", "", 0.05, 100.0, 5.0), tl("PST", "BC", 0.07, 100.0, 7.0)], prov="BC",
            vendor_name="Lakehead Courier Inc.", invoice_number="LC-00950", invoice_date="2026-10-07",
            po_number="4500078813")  # fmt: skip
    return coded(d, inv_id=inv_id)


def test_what_a_person_would_have_to_work_out_is_said_under_the_voucher():
    """The meals' half and where it is, what the tax amount holds under code C, a tax that is not recovered, the
    currency the amounts are typed in and the rate, a PO number JD Edwards' field cannot hold."""
    rates = {"USD": 1.37}
    eaten, sales_tax, qc, foreign = blocks(
        workbook([meal(), with_pst(), quebec(), usd()], fx_rates=rates)[jde_keying.SHEET])  # fmt: skip
    assert (field(eaten, "Tax amount").value, field(eaten, "Taxable amount").value) == (7.5, 200)
    assert lines_and_check(eaten)[0][2] == 102.5 and said(eaten, "Warning") == []
    assert said(eaten, "Note") == [
        "Meals: half the tax on meals (2.50) is not claimed. It is inside G/L line 1, and the tax amount is the "
        "claimed part only: less than the area's rate on the taxable amount."
    ]
    assert (field(sales_tax, "Tax explanation code").value, field(sales_tax, "Tax amount").value) == ("C", 12)
    assert lines_and_check(sales_tax)[-1][3] == "107.00 + 5.00 = 112.00: balanced"
    assert said(sales_tax, "Note") == [
        "The tax amount 12.00 is 5.00 that JD Edwards recovers and 7.00 of PST that stays inside the G/L lines (code "
        "C: the tax area's sales tax).",
        "PO number 4500078813 is longer than the 8 characters JD Edwards' purchase order field holds: it cannot be "
        "typed there as it is.",
    ]
    assert said(qc, "Note") == [
        "The QST (123.69) is not recovered: it is inside the G/L lines, not in the tax or taxable amount."
    ]
    assert said(foreign, "Note") == [
        "USD voucher: type the amounts as shown, in USD (foreign entry), not in CAD. AP Coder's rate is 1.37 CAD for "
        "1 USD (Settings → Review; the batch file sends it): leave JD Edwards' own rate unless told to type one."
    ]
    (no_rate,) = blocks(workbook([usd()])[jde_keying.SHEET])
    assert said(no_rate, "Note")[0].endswith("not in CAD. JD Edwards takes the exchange rate from its own table.")
    # No row of a note is wider than the three columns beside its label, and a plain voucher has none.
    assert all(len(row[1]) <= jde_keying.NOTE_WIDTH for b in (eaten, sales_tax, qc, foreign) for row in values(b)
               if row[0] in ("Note", None) and row[1])  # fmt: skip
    (plain,) = blocks(workbook([one_line()])[jde_keying.SHEET])
    assert plain[-1][0].value == "Check"


def test_a_gst_the_tax_setup_does_not_recover_is_warned_of_on_the_voucher():
    """B-2150 on the sheet: the GST kept in the expense under code V goes with the exempt code and a warning."""
    d = {k: v for k, v in one_line()["final_output"].items() if k != "gl_distribution"}
    (block,) = blocks(workbook([coded(d, _setup(GST=TaxTreatment("GST", "expense_to_line")))])[jde_keying.SHEET])
    assert (field(block, "Tax explanation code").value, field(block, "Tax rate/area").value) == ("E", "AB-GST")
    assert field(block, "Tax amount").value is None and field(block, "Taxable amount").value is None
    assert lines_and_check(block)[-1][2:] == [252, "252.00 + 0.00 = 252.00: balanced"]
    assert said(block, "Warning") == [
        "The GST (12.00) is not recovered: GL accounts & tax keeps it in the expense, so the voucher claims no input "
        "tax credit for it. If that is not meant, set it to recoverable in GL accounts & tax (or ask the pilot "
        "tester), then save the invoice again."
    ]
    assert [row[0].font.bold for row in block if row[0].value == "Warning"] == [True]


def test_the_tax_coded_to_gl_accounts_is_a_line_of_its_own_and_no_tax_on_the_pay_item():
    """With JD Edwards' Amounts set to the tax on G/L lines (D-93), the sheet says the same as the batch files."""
    js = jde_settings.JdeSettings(default_bu="0000900")  # the tax account's business unit
    (block,) = blocks(workbook([one_line()], js)[jde_keying.SHEET])
    assert field(block, "Tax explanation code").value == "E" and field(block, "Tax rate/area").value is None
    assert field(block, "Taxable amount").value is None
    assert lines_and_check(block) == [
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
    data, _, _ = jde_keying.build([inv], settings(), lambda final: "40417", gl_names=NAMES)
    (block,) = blocks(openpyxl.load_workbook(io.BytesIO(data))[jde_keying.SHEET])
    name, remark = field(block, "Supplier name"), block[LINES][3]
    assert (name.value, name.data_type, name.quotePrefix) == ("=HYPERLINK(1)", "s", True)
    assert (remark.value, remark.data_type, remark.quotePrefix) == ("+1 800 555 0100", "s", True)


def test_the_same_invoices_give_the_same_sheet_every_time():
    def cells(wb):
        return [[(c.value, c.data_type, c.number_format) for c in row] for ws in wb for row in ws.iter_rows()]

    assert cells(workbook(everything())) == cells(workbook(everything()))
    # The G/L date of the export day is the batch's day, not the day the sheet is downloaded again.
    (block,) = blocks(workbook([one_line()], settings(gl_date=jde_settings.GL_DATE_EXPORT))[jde_keying.SHEET])
    assert field(block, "G/L date").value == dt.datetime(2026, 10, 9)


def test_the_sheet_and_the_batch_files_say_the_same():
    """The sheet's figures are the voucher's own: what the Z-files of the same invoices hold."""
    js = settings()
    invoices = [one_line(), split(), quebec(), usd(), credit()]
    files = jde_zfiles.export_batch(invoices, js, lookup, batch=7, on=ON_DAY)
    heads = list(csv.DictReader(io.StringIO(files[jde_zfiles.HEADER_FILE].decode())))
    dist = list(csv.DictReader(io.StringIO(files[jde_zfiles.DIST_FILE].decode())))
    ready, held = jde_keying.vouchers(invoices, js, lookup, gl_names=NAMES, on=ON_DAY)
    assert not held
    for v, head in zip(ready, heads, strict=True):
        cents = lambda x: str(round((x or 0) * 100))  # noqa: E731
        foreign = v.currency != "CAD"
        tax = (head["VLCTXA"], head["VLCTAM"]) if foreign else (head["VLATXA"], head["VLSTAM"])
        assert tax == (cents(v.taxable), cents(v.tax))
        assert (head["VLAN8"], head["VLVINV"], head["VLEXR1"], head["VLTXA1"]) == (
            v.supplier_number, v.invoice_number, v.tax_code, v.tax_area)  # fmt: skip
        assert head["VLDGJ"] == str(jde_formats.julian(v.gl_date)) and head["VLPO"] == v.po_number
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
    js = jde_settings.load(store)
    js.default_bu = "HQ"  # the tax account's business unit (the tax coded to G/L accounts: D-93)
    js.an8_overrides = {vendor_key(chinook["vendor_name"]): "C-41"}
    jde_settings.save(store, js)
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
    assert [values(b) for b in blocks(wb[jde_keying.SHEET])] == [
        [["No voucher to type: see the sheet “Not ready”.", None, None, None]]
    ]
    assert "already in the ERP" in wb[jde_keying.NOT_READY]["H2"].value


def test_the_keying_sheet_is_offered_only_where_jd_edwards_is_shown(store):
    from ap_coder.web.pages import exports as exports_page

    store.set_setting(jde_settings.SHOW_SETTING, "0")
    assert not {jde_settings.FORMAT, jde_keying.FORMAT} & set(exports_page.shown_formats(store))
    store.set_setting(jde_settings.SHOW_SETTING, "1")
    assert {jde_settings.FORMAT, jde_keying.FORMAT} <= set(exports_page.shown_formats(store))


def test_a_voucher_jd_edwards_has_through_the_api_is_on_not_ready_never_to_be_typed_too(tmp_path, monkeypatch):
    """A JD Edwards batch's keying sheet: a voucher posted through the API is not one to type (D-210)."""
    from ap_coder.erp import jde_ais, jde_sync
    from tests.erp.test_jde_post import batch_of
    from tests.fake_ais import PASSWORD, FakeAis
    from tests.webclient import client_for, post

    monkeypatch.setattr(jde_ais, "RETRY_PAUSE", 0)
    monkeypatch.setattr(jde_ais, "env_path", lambda: tmp_path / ".env")
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)
    jde_sync.forget_cache()
    with FakeAis() as ais:
        store, batch = batch_of(tmp_path, ais)
        client = client_for(store)

        def sheet():
            answer = client.get(f"/exports/batches/{batch}/download?fmt={jde_keying.FORMAT}")
            wb = openpyxl.load_workbook(io.BytesIO(answer.content))
            return ([b[0][0].value for b in blocks(wb[jde_keying.SHEET])],
                    list(wb[jde_keying.NOT_READY].iter_rows(min_row=2, values_only=True)))  # fmt: skip

        assert sheet() == (["Voucher 1 of 1"], [])
        for step in ("dry-run", "post"):
            post(client, f"/exports/batches/{batch}/jde-api/{step}")
            assert client.app.state.jobs.get("jde-api").wait(30)
        titles, held = sheet()
        assert titles == ["No voucher to type: see the sheet “Not ready”."]
        assert [(row[0], row[7]) for row in held] == [(1, "already posted to JD Edwards through the API")]


def test_a_keying_sheet_batch_typed_with_code_b_counts_as_jd_edwards_accruing_the_pst(tmp_path):
    """B-2153: the Self-assessment report knew only a batch made as Z-files for one whose PST JD Edwards accrues
    itself (code B): a voucher typed from a keying sheet with code B was listed to book again by journal entry."""
    from ap_coder.coding import taxreturn
    from ap_coder.core.store import Store
    from tests.conftest import SAMPLES
    from tests.erp.test_jde import SK
    from tests.erp.test_jde import _invoice as sample

    store = Store(tmp_path / "private" / "ap_coder.db")
    final = sample(SK)["final_output"]  # Saskatchewan, the PST not charged: self-assessed
    final["tax_lines"] = [t for t in final["tax_lines"] if t["tax_type"] != "PST"]
    final["tax_total"] = sum(t["tax_amount"] for t in final["tax_lines"])
    final["grand_total"] = round(final["subtotal"] + final["tax_total"], 2)
    for li in final["line_items"]:
        li["taxes_applied"], li["tax_exempt"] = ["GST"], []
    ids = []
    for _ in range(2):
        ids.append(store.add_invoice(SAMPLES / f"{SK}.pdf", final, {"requires_review": True}))
        store.approve_invoice(ids[-1], final, "Pat Tester")
    jde_settings.save(store, jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX))
    day = dt.date.fromisoformat(final["invoice_date"])
    before = taxreturn.setup_key(store)
    store.create_export_batch([ids[0]], jde_keying.FORMAT, actor="Pat Tester")
    assert taxreturn.setup_key(store) != before  # an answer kept for the page is worked out again
    notes = {s.invoice_id: s.note for s in taxreturn.self_assessment(store, day, day)}
    assert notes == {ids[0]: taxreturn.JDE_ACCRUES, ids[1]: taxreturn.JDE_WILL_ACCRUE}
    store.create_export_batch([ids[1]], "xlsx", actor="Pat Tester")  # an Excel batch: not in JD Edwards
    assert {s.invoice_id: s.note for s in taxreturn.self_assessment(store, day, day)}[ids[1]] == ""


def test_with_the_prototype_set_up_and_the_made_up_chart_a_one_line_gst_invoice_is_on_the_keying_sheet(tmp_path):
    """The department's way in one step (`jde_prototype.use_prototype`): JD Edwards shown, the tax on the pay item with
    code V and a tax rate/area, the QST not recovered, the account the line's own string. With a new data folder's
    defaults (tax coded to G/L lines, JD Edwards hidden) the same invoice was held back from the sheet."""
    from ap_coder.bench import jde_company, jde_exports
    from ap_coder.core.store import Store
    from ap_coder.ops import chart_cmd
    from ap_coder.web.pages import exports as exports_page

    store = Store(tmp_path / "private" / "ap_coder.db")
    books = tmp_path / "books"
    jde_exports.write_all(books)
    chart_cmd.import_files(store, books / jde_exports.CHART_FILE, books / jde_exports.UNITS_FILE)
    pairs = store.account_pairs()
    unit = pairs.business_units()[0]
    code = next(c for c in pairs.accounts_of(unit) if pairs.offered(unit, c) and "." in c)
    final = coded(doc([line(1, 240.0, code, ["GST"], desc="Fibre line October", cc=unit)],
                      [tl("GST", "", 0.05, 240.0, 12.0)], prov="AB", vendor_name="Prairie Office Supply Ltd.",
                      invoice_number="POS-3301", invoice_date="2026-10-02"))["final_output"]  # fmt: skip
    store.import_vendor_master([{"vendor_name": final["vendor_name"], "erp_id": "40417"}])
    inv = store.add_invoice(tmp_path / "a.pdf", final, {})
    store.approve_invoice(inv, final, "Jane")

    def sheet():
        batch = store.create_export_batch([inv], jde_keying.FORMAT)
        wb = openpyxl.load_workbook(io.BytesIO(exports_page.batch_file(store, batch, jde_keying.FORMAT)[0]))
        store.undo_export_batch(batch)
        return blocks(wb[jde_keying.SHEET]), list(wb[jde_keying.NOT_READY].iter_rows(min_row=2, values_only=True))

    assert not jde_settings.in_use(store) and not exports_page.shown_formats(store).count(jde_keying.FORMAT)
    ready, held = sheet()  # a new data folder: the tax on G/L lines, whose tax account has no business unit
    assert len(held) == 1 and "has no JDE account" in held[0][7]

    js = jde_prototype.use_prototype(store, actor="Setup")
    # the page that hands vouchers over opens on the keying sheet (B-2279), unless a format was chosen before
    assert store.get_setting("export_format") == jde_keying.FORMAT
    store.set_setting("export_format", "csv")
    jde_prototype.use_prototype(store, actor="Setup")
    assert store.get_setting("export_format") == "csv"
    assert jde_settings.load(store) == js == jde_prototype.prototype_settings() and jde_settings.in_use(store)
    assert jde_keying.FORMAT in exports_page.shown_formats(store)
    assert store.tax_treatments()["QST"].treatment == "expense_to_line"
    (block,), held = sheet()
    assert held == []
    assert (field(block, "Tax explanation code").value, field(block, "Tax rate/area").value) == (
        "V", jde_company.TAX_AREA)  # fmt: skip
    assert (field(block, "Taxable amount").value, field(block, "Tax amount").value) == (240, 12)
    assert lines_and_check(block) == [
        [f"{unit}.{code}", pairs.description(unit, code) or None, 240, "Fibre line October"],
        ["Check", "G/L lines + recovered tax = gross", 240, "240.00 + 12.00 = 252.00: balanced"],
    ]
    jde_settings.show(store, False)
    assert not jde_settings.in_use(store)


def test_a_warning_given_for_a_voucher_is_said_first_under_it_and_under_no_other():
    """A tax ID that differs from the supplier list's, approved by one person alone (D-219): said on that voucher."""
    note = "Tax ID differs from the supplier list: confirm before paying."
    ws = workbook([one_line(), split()], warned={1: [note]})[jde_keying.SHEET]
    first, second = blocks(ws)
    assert said(first, "Warning")[0] == note and note not in said(second, "Warning")


def test_the_header_block_carries_the_bank_account_once_bank_accounts_are_entered():
    """D-220: no bank account entered, the block is as it was; with one, each voucher says its G/L bank account as
    typed (the account's cash GL account, else its code), and a voucher with none says so."""
    plain = blocks(workbook([one_line()])[jde_keying.SHEET])[0]
    assert len(lines_and_check(plain)) and "G/L bank account" not in [row[0] for row in values(plain)]
    paid, other = one_line(), split()
    paid["final_output"]["pay_from_account"] = "CAD-OP"
    accounts = {"CAD-OP": {"code": "CAD-OP", "name": "Operating", "gl_account": "100.1110"},
                "100.1112.PAY": {"code": "100.1112.PAY", "name": "Payroll", "gl_account": ""}}  # fmt: skip
    first, second = blocks(workbook([paid, other], bank_accounts=accounts)[jde_keying.SHEET])
    assert (field(first, "G/L bank account").value, field(first, "Bank account").value) == ("100.1110", "Operating")
    assert field(first, "G/L bank account").number_format == "@"  # a text cell: typed as it is
    assert field(second, "G/L bank account").value is None
    assert any("No bank account is entered for CAD" in text for text in said(second, "Warning"))
    other["final_output"]["pay_from_account"] = "100.1112.PAY"
    _, second = blocks(workbook([paid, other], bank_accounts=accounts)[jde_keying.SHEET])
    assert field(second, "G/L bank account").value == "100.1112.PAY"  # no cash GL account: the code as keyed


def test_a_supplier_put_on_hold_after_approval_is_not_ready_for_jd_edwards(tmp_path, cost_centers_demo_template):
    """B-2340: an approved invoice whose supplier was put on hold since (in AP Coder's vendor list, or by a hold code
    in JD Edwards' supplier master) still went on a keying sheet or a JD Edwards batch as ready: only the API looked
    at the hold. It is now not ready, with the hold's reason, in both formats."""
    from ap_coder.web.pages.exports import jde_check
    from tests.webclient import post

    store, client, northwind, _chinook = demo(tmp_path, cost_centers_demo_template)
    vendor = store.get_vendor(store.get_invoice(northwind["id"])["vendor_key"])
    store.save_vendor(vendor["vendor_key"], vendor["display_name"], "on_hold", notes="bank details changed")
    for files in (True, False):
        why = jde_check(store, [northwind["id"]], files=files)[1][northwind["id"]]
        assert "is on hold in AP Coder's vendor list (bank details changed)" in " ".join(why)
    assert "on hold in AP Coder&#39;s vendor list" in client.get(f"/exports?fmt={jde_keying.FORMAT}").text
    answer = post(client, "/exports/create", {"fmt": jde_keying.FORMAT, "ids": [northwind["id"]]})
    assert answer.status_code == 400 and store.export_batches() == []
    # Off hold here, held by its hold payment code in JD Edwards' supplier master (by its supplier number).
    store.save_vendor(vendor["vendor_key"], vendor["display_name"], "active")
    store.import_vendor_master([{"vendor_name": vendor["display_name"], "erp_id": "10023", "hold": "Y"}],
                               by_erp_id=True)  # fmt: skip
    why = jde_check(store, [northwind["id"]])[1][northwind["id"]]
    assert "is on hold in JD Edwards (hold code Y" in " ".join(why)


def test_pst_to_self_assess_on_a_gst_only_tax_area_is_listed_and_said_on_the_voucher(tmp_path):
    """B-2343: a Saskatchewan supply charged the GST only is keyed with code B, which has JD Edwards accrue the PST
    only on a tax area that carries it; on the prototype's GST-only area (shared with Alberta) it accrues nothing, yet
    the Self-assessment report said "JD Edwards accrues this one (code B)", so nobody booked the PST. The report lists
    it to self-assess and the voucher says so; what is keyed is the same."""
    from ap_coder.coding import taxreturn
    from ap_coder.core.store import Store
    from tests.conftest import SAMPLES
    from tests.erp.test_jde import SK
    from tests.erp.test_jde import _invoice as jde_invoice

    store = Store(tmp_path / "private" / "ap_coder.db")
    final = jde_invoice(SK)["final_output"]
    final["tax_lines"] = [t for t in final["tax_lines"] if t["tax_type"] != "PST"]
    final["tax_total"] = sum(t["tax_amount"] for t in final["tax_lines"])
    final["grand_total"] = round(final["subtotal"] + final["tax_total"], 2)
    for li in final["line_items"]:
        li["taxes_applied"], li["tax_exempt"] = ["GST"], []
    final = coded({k: v for k, v in final.items() if k != "gl_distribution"})["final_output"]
    ids = []
    for _ in range(2):
        ids.append(store.add_invoice(SAMPLES / f"{SK}.pdf", final, {"requires_review": True}))
        store.approve_invoice(ids[-1], final, "Pat Tester")
    store.create_export_batch([ids[0]], jde_keying.FORMAT, actor="Pat Tester")
    js = jde_prototype.prototype_settings(jde_settings.JdeSettings(company="00210"))
    jde_settings.save(store, js)
    day = dt.date.fromisoformat(final["invoice_date"])
    items = {s.invoice_id: s for s in taxreturn.self_assessment(store, day, day)}
    assert [(items[i].tax_type, items[i].note) for i in ids] == [("PST", ""), ("PST", "")]
    (voucher,), held = jde_keying.vouchers([{"id": ids[0], "final_output": final}], js, lambda final: "40417")
    assert held == [] and (voucher.tax_code, voucher.tax_area) == ("B", "CDNGST")
    assert voucher.notes[-1].startswith(f"PST to self-assess: SK PST on {items[ids[0]].base:,.2f}. ")
    assert "CDNGST" in voucher.notes[-1] and "Self-assessment report" in voucher.notes[-1]


def test_a_credit_note_that_prints_payment_terms_is_keyed_with_none():
    """B-2344: the batch files send a credit memo with no terms code (B-896), but the keying sheet showed the terms it
    prints ("Net 30") to type. It says none, as the files send."""
    printed = credit()
    printed["final_output"]["payment_terms"] = "Net 30"
    (block,) = blocks(workbook([printed])[jde_keying.SHEET])
    assert field(block, "Payment terms").value == "none (a credit)"
    head = jde_zfiles.voucher_rows(printed, "40417", settings())[0][0]
    assert head["VLPTC"] == ""
