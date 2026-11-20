"""The JD Edwards keying sheet (D-210): a block per voucher to read while typing it into Standard Voucher Entry, its
cells' values and types, the check line and what is said under it, the printed pages, the "Not ready" sheet and the
Exports page's batches of it."""

import csv
import datetime as dt
import io

import openpyxl

from ap_coder.coding.tax import TaxTreatment
from ap_coder.core.memory import vendor_key
from ap_coder.erp import exports, jde, jde_formats, jde_keying, jde_prototype, jde_review, jde_settings, jde_zfiles
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


def test_a_voucher_split_across_two_business_units_has_a_line_each_with_its_remark_cut_at_a_word_end():
    (block,) = blocks(workbook([split()])[jde_keying.SHEET])
    assert lines_and_check(block) == [
        [f"{HQ}.6420.2210", "Data lines", 100, "Courier to the north office"],
        [f"{HQ}.0042", "Office supplies", 200, "Toner"],
        [f"{IT}.6431", "Courier", 300, "Same-day courier"],
        ["Check", "G/L lines + recovered tax = gross", 600, "600.00 + 78.00 = 678.00: balanced"],
    ]
    assert field(block, "Remark").value == "Courier to the north office"  # the first line's, at a word end (B-2617)
    assert (field(block, "Taxable amount").value, field(block, "Tax amount").value) == (600, 78)
    assert (field(block, "Tax explanation code").value, field(block, "Tax rate/area").value) == ("V", "ON-HST")


def test_a_line_without_a_remark_is_keyed_with_its_description_cut_at_a_word_end():
    """B-2617: a line with no remark of its own was keyed with its description cut at 30 characters mid-word
    ("Professional consulting servic"), on the sheet and in the batch files' explanation (VNEXA). It is cut at a word
    end within 30 (``remark.fit``)."""
    final = one_line()["final_output"]
    final["line_items"][0]["description"] = "Professional consulting services in October"
    invoice = coded({k: v for k, v in final.items() if k != "gl_distribution"})
    (block,) = blocks(workbook([invoice])[jde_keying.SHEET])
    assert lines_and_check(block)[0][3] == "Professional consulting"
    files = jde_zfiles.export_batch([invoice], settings(), lookup, batch=7, on=ON_DAY)
    (dist,) = csv.DictReader(io.StringIO(files[jde_zfiles.DIST_FILE].decode()))
    assert dist["VNEXA"] == "Professional consulting"


def test_a_line_with_a_remark_of_its_own_is_on_the_sheet_with_it():
    """D-213: the "Explanation -Remark-" column and the pay item's Remark carry the line's remark, fitted to 30
    characters at a word end; a line without one its description, at a word end too (B-2617)."""
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


def test_a_remark_the_person_cleared_is_keyed_empty_on_the_sheet_and_in_the_files():
    """B-2489: a remark the person emptied on purpose (D-229) was keyed as the line's description cut at 30 (the
    sheet's Explanation -Remark-, VNEXA and VNEXR): the line's remark was copied to its G/L line only when it had
    text, so ``line_remark`` never saw it was cleared."""
    invoice = one_line()
    invoice["final_output"]["line_items"][0].update(remark="", remark_from="person")
    (block,) = blocks(workbook([invoice])[jde_keying.SHEET])
    assert lines_and_check(block)[0][3] in ("", None)
    files = jde_zfiles.export_batch([invoice], settings(remarks=jde_settings.REMARKS_TYPED), lookup, batch=7, on=ON_DAY)
    (dist,) = csv.DictReader(io.StringIO(files[jde_zfiles.DIST_FILE].decode()))
    assert (dist["VNEXA"], dist["VNEXR"]) == ("", "")


def test_a_batch_made_earlier_is_checked_against_the_open_period_on_its_own_day():
    """B-2490: with the export day as the G/L date, a voucher's check asked the open period about today while the
    sheet and the files key the batch's day: a batch of 30 October rebuilt in November was ready with its G/L date
    30 October in a closed period, and with October still the open period it was "not ready" for today's date,
    which it would not key."""
    day = dt.date(2026, 10, 30)
    invoice = one_line()
    closed = settings(gl_date=jde_settings.GL_DATE_EXPORT, open_from="2026-11-01",
                      closed_period=jde_settings.CLOSED_HOLD)  # fmt: skip
    ready, held = jde_keying.vouchers([invoice], closed, lookup, on=day)
    assert not ready and "G/L date 2026-10-30 is in a closed period" in held[0].why[0]
    report = jde.dry_run([invoice], closed, lookup, on=day)
    assert not report["vouchers"] and "G/L date 2026-10-30 is in a closed period" in report["held"][0]["reasons"][0]

    october = settings(gl_date=jde_settings.GL_DATE_EXPORT, open_from="2026-10-01", open_to="2026-10-31")
    ready, held = jde_keying.vouchers([invoice], october, lookup, on=day)
    assert not held and ready[0].gl_date == day
    report = jde.dry_run([invoice], october, lookup, on=day)
    assert not report["held"] and report["vouchers"][0]["pay_items"][0]["dates"].startswith("invoice ")
    files = jde_zfiles.export_batch([invoice], october, lookup, batch=7, on=day)
    (header,) = csv.DictReader(io.StringIO(files[jde_zfiles.HEADER_FILE].decode()))
    assert header["VLDGJ"] == str(jde_formats.julian(day))


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
    check = next(row for row in block if row[0].value == "Check")  # (a note under it: the terms have no code)
    assert check[3].value.endswith(": balanced")
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
    # Another batch (made as Excel) has no keying sheet: its invoices may be in the ERP by then (B-2619).
    follow(client, post(client, "/exports/batches/1/undo"))
    follow(client, post(client, "/exports/create", {"fmt": "xlsx", "ids": [northwind["id"]]}))
    assert client.get(f"/exports/batches/2/download?fmt={jde_keying.FORMAT}").status_code == 409


def test_a_batch_is_downloaded_again_only_in_its_own_format_or_as_the_excel_workbook(
    tmp_path, cost_centers_demo_template
):
    """B-2619: "Download again" offered every format for any batch, so a keying-sheet batch (typed by hand) could be
    downloaded as JD Edwards batch files and loaded too: each voucher in JD Edwards twice. A batch goes again in its
    own format, or as the Excel workbook, which people read and no loader takes; any other is refused."""
    from tests.webclient import PAGE, follow, post

    store, client, northwind, _ = demo(tmp_path, cost_centers_demo_template)
    follow(client, post(client, "/exports/create", {"fmt": jde_keying.FORMAT, "ids": [northwind["id"]]}))
    (batch,) = store.export_batches()
    page = client.get(f"/exports?batch={batch['id']}").text
    offered = page.split('name="bfmt"', 1)[1].split("</select>", 1)[0]
    assert f'value="{jde_keying.FORMAT}"' in offered and 'value="xlsx"' in offered
    assert f'value="{jde_settings.FORMAT}"' not in offered and 'value="csv"' not in offered
    assert 'value="custom"' not in offered
    for fmt in (jde_settings.FORMAT, "csv", "custom"):
        answer = client.post(f"/exports/batches/{batch['id']}/download?fmt={fmt}", headers=PAGE)
        assert answer.status_code == 409 and "twice" in answer.text, fmt
    assert client.get(f"/exports/batches/{batch['id']}/download?fmt=xlsx").status_code == 200
    assert client.get(f"/exports/batches/{batch['id']}/download?fmt={jde_keying.FORMAT}").status_code == 200
    # Asked for another format in the address, the page offers the batch's own
    page = client.get(f"/exports?batch={batch['id']}&bfmt={jde_settings.FORMAT}").text
    assert f'action="/exports/batches/{batch["id"]}/download?fmt={jde_settings.FORMAT}"' not in page


def test_the_keying_sheet_is_offered_only_where_jd_edwards_is_shown(store):
    from ap_coder.web.pages import exports as exports_page

    store.set_setting(jde_settings.SHOW_SETTING, "0")
    assert not {jde_settings.FORMAT, jde_keying.FORMAT} & set(exports_page.shown_formats(store))
    store.set_setting(jde_settings.SHOW_SETTING, "1")
    assert {jde_settings.FORMAT, jde_keying.FORMAT} <= set(exports_page.shown_formats(store))


def test_a_jd_edwards_batch_has_no_keying_sheet_so_no_voucher_is_typed_too(tmp_path, monkeypatch):
    """A JD Edwards batch's vouchers are loaded from its files or posted through the API: it has no keying sheet,
    before its vouchers are posted or after, so none is typed too (B-2619; before, its sheet listed a voucher posted
    through the API as Not ready and the others to type, though the file may have been loaded, D-210)."""
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
        assert client.get(f"/exports/batches/{batch}/download?fmt={jde_keying.FORMAT}").status_code == 409
        for step in ("dry-run", "post"):
            post(client, f"/exports/batches/{batch}/jde-api/{step}")
            assert client.app.state.jobs.get("jde-api").wait(30)
        assert client.get(f"/exports/batches/{batch}/download?fmt={jde_keying.FORMAT}").status_code == 409


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


def test_printed_terms_with_no_jd_edwards_code_are_never_put_in_the_payment_terms_field():
    """B-2618: printed terms the terms table has no code for ("2/10 Net 30") were shown in Payment terms, JD Edwards'
    3-character code, which they can't be typed in, while the batch files send it blank (the supplier's own terms).
    The field is blank, as the files send it, and a note gives the printed terms for the person to pick the code."""
    printed = one_line()
    printed["final_output"]["payment_terms"] = "2/10 Net 30"
    (block,) = blocks(workbook([printed])[jde_keying.SHEET])
    assert field(block, "Payment terms").value is None
    assert jde_zfiles.voucher_rows(printed, "40417", settings())[0][0]["VLPTC"] == ""
    (note,) = [n for n in said(block, "Note") if "2/10 Net 30" in n]
    assert "no JD Edwards code" in note and "blank" in note
    (plain,) = blocks(workbook([one_line()])[jde_keying.SHEET])  # terms with a code: the code, nothing said
    assert field(plain, "Payment terms").value == "N30" and not said(plain, "Note")


def test_a_document_type_other_than_the_standard_voucher_is_on_the_sheet():
    """B-2621: the batch files send a credit note with the credit document type when one is set (Settings → JD Edwards
    E1), but the keying sheet did not say it: the credit was typed as a standard voucher (PV). The sheet gives the
    document type whenever it is not PV, as the files send it."""
    js = settings(credit_document_type="PD")
    ready, _ = jde_keying.vouchers([one_line(), credit()], js, lookup, on=ON_DAY)
    data = jde_keying.build_xlsx(ready, [])
    invoice, minus = blocks(openpyxl.load_workbook(io.BytesIO(data))[jde_keying.SHEET])
    assert field(minus, "Document type").value == "PD"
    assert "Document type" not in [c.value for row in invoice for c in row]  # PV, the standard voucher: not said
    assert jde_zfiles.voucher_rows(credit(), "40417", js)[0][0]["VLDCT"] == "PD"


def test_a_tax_area_of_another_province_than_the_tax_charged_is_said_and_holds_nothing():
    """B-2345: the tax area follows the ship-to, while the tax charged can be another province's: an Alberta supplier
    charging the GST only on goods shipped to Ontario went to ONHST with 5% tax (and an Ontario supplier's HST on a
    supply in Alberta to the GST-only area) with nothing said, where JD Edwards works the tax out at the area's rate.
    A warning on the review screen and a note on the voucher; the voucher is still ready."""
    from ap_coder.ops.help import CHECKS

    js = jde_prototype.prototype_settings(jde_settings.JdeSettings(company="00210"))
    gst = coded(doc([line(1, 1000.0, "6420.2210", ["GST"], cc=HQ)], [tl("GST", "", 0.05, 1000.0, 50.0)],
                    prov="AB", ship_to_province="ON", vendor_name="Prairie Office Supply Ltd."))  # fmt: skip
    hst = coded(doc([line(1, 1000.0, "6420.2210", ["HST"], cc=HQ)], [tl("HST", "ON", 0.13, 1000.0, 130.0)],
                    prov="ON", ship_to_province="AB", vendor_name="Prairie Office Supply Ltd."), inv_id=2)  # fmt: skip
    ready, held = jde_keying.vouchers([gst, hst], js, lookup)
    assert held == [] and [(v.tax_area, v.tax) for v in ready] == [("ONHST", 50.0), ("CDNGST", 130.0)]
    first, second = (v.notes[-1] for v in ready)
    assert first.startswith("Tax area ONHST is for a supply in ON, where HST is charged, but this invoice charges "
                            "GST (50.00)")  # fmt: skip
    assert second.startswith("Tax area CDNGST is for a supply in AB, where GST is charged, but this invoice charges "
                             "HST (130.00)")  # fmt: skip
    for inv in (gst, hst):
        found = [(sev, code) for sev, code, _m, _l in jde_review.voucher_findings(inv["final_output"], js, "40417")]
        assert found == [("warning", "JDE_TAX_AREA_MISMATCH")]
    assert "JDE_TAX_AREA_MISMATCH" in CHECKS
    (plain,) = jde_keying.vouchers([one_line()], js, lookup)[0]  # Alberta's GST on a supply in Alberta: nothing said
    assert not any(n.startswith("Tax area") for n in plain.notes)
    assert jde_review.voucher_findings(one_line()["final_output"], js, "40417") == []


def test_the_gst_printed_as_hst_is_no_other_tax_for_the_tax_area():
    """B-2641: an Alberta supplier's 5% under "HST Tax" on goods and freight delivered in Alberta (656.50 + 32.00,
    34.43 of tax) was "JD Edwards may work the tax out at the area's rate and find it different": the GST area's 5%
    of 688.50 is the 34.43 charged. Nothing to say; the freight is a line of its own, taxed with the goods."""
    js = jde_prototype.prototype_settings(jde_settings.JdeSettings(company="00210"))
    inv = coded(doc([line(1, 656.5, "6420.2210", ["HST"], cc=HQ), line(2, 32.0, "6420.2210", ["HST"], cc=HQ)],
                    [tl("HST", "AB", 0.05, 688.5, 34.43)], prov="AB", ship_to_province="AB",
                    vendor_name="Prairie Office Supply Ltd."))  # fmt: skip
    (v,), held = jde_keying.vouchers([inv], js, lookup)
    assert held == [] and (v.tax_area, v.taxable, v.tax, v.gross) == ("CDNGST", 688.5, 34.43, 722.93)
    assert [li.amount for li in v.lines] == [656.5, 32.0]
    assert not any(n.startswith("Tax area") for n in v.notes)
    assert jde_review.voucher_findings(inv["final_output"], js, "40417") == []


# --- A PO-matched voucher: keyed in Voucher Match (D-264) ----------------------------------------------------------

PROMO = "Prairie Promo Supply Inc."


def po_invoice(inv_id=8, freight="Charges not in the line table", po_number="40917"):
    """The one-row invoice of ``tests.pdfs`` as read: 100 cooler bags, their setup and the freight, GST in Alberta,
    on the made-up JD Edwards PO 40917 (``tests.pdfs.ONE_ROW_PO_LINES``)."""
    bags = {
        **line(1, 599.0, "6420.2210", ["GST"], desc="CB-220 Non-Woven Lunch Cooler Bag, 2 colour", cc=HQ),
        "quantity": 100,
        "unit_price": 5.99,
    }
    lines = [bags, line(2, 57.5, "6420.2210", ["GST"], desc="SETUP Lunch Cooler Setup", cc=HQ),
             line(3, 32.0, "6420.2210", ["GST"], desc=freight, cc=HQ)]  # fmt: skip
    d = doc(lines, [tl("GST", "", 0.05, 688.5, 34.43)], prov="AB", vendor_name=PROMO, invoice_number="118204",
            invoice_date="2026-09-14", payment_terms="Net 30", po_number=po_number)  # fmt: skip
    return coded(d, inv_id=inv_id)


def po_store(tmp_path):
    from ap_coder.core.store import Store
    from tests.coding.test_po import _jde_pos

    store = Store(tmp_path / "ap.db")
    _jde_pos(store)
    return store


def promo_lookup(final):
    return "4471001" if final["vendor_name"] == PROMO else lookup(final)


def test_a_po_matched_voucher_is_keyed_as_voucher_match_rows_in_the_screens_order(tmp_path):
    """D-264: most of the pilot's invoices are matched to a JD Edwards purchase order, keyed in Voucher Match (P0411):
    a row for each PO line matched (the freight too), never a G/L distribution. The keying sheet had none to give: a
    PO-matched voucher said "JD Edwards distributes it from the receipts" and nothing more. Its rows now come in the
    screen's order, each from the PO line its invoice line bills (the order number, type and company, the line's own
    number, the quantity and amount matched, the unit, the PO line's account) with the tax and the remark; the header
    is as before, and an invoice with no PO in the same batch keeps its G/L lines."""
    store = po_store(tmp_path)
    js = settings(po_matched=True)
    invoices = [po_invoice(), one_line()]
    matches = jde_keying.po_matches(store, invoices)
    (matched, coded_to_gl), held = jde_keying.vouchers(invoices, js, promo_lookup, gl_names=NAMES, on=ON_DAY,
                                                       po_matches=matches)  # fmt: skip
    assert held == [] and matched.lines == [] and len(coded_to_gl.lines) == 1 and coded_to_gl.matches == []
    assert (matched.supplier_number, matched.gross, matched.tax, matched.taxable) == ("4471001", 722.93, 34.43, 688.5)
    area = matched.tax_area
    assert [vars(m) for m in matched.matches] == [
        {"order_number": "40917", "order_type": "OP", "order_company": "00210", "line": "12.000", "quantity": 100,
         "uom": "EA", "amount": 599.0, "account": "0312005561.6420.2210", "taxed": "Y", "tax_code": "V",
         "tax_area": area, "remark": "CB-220 Non-Woven Lunch Cooler", "on_po": True, "subledger": "",
         "subledger_type": "", "retainage": None, "retained": 0.0},
        {"order_number": "40917", "order_type": "OP", "order_company": "00210", "line": "13.000", "quantity": 1,
         "uom": "EA", "amount": 57.5, "account": "0312005561.6420.2210", "taxed": "Y", "tax_code": "V",
         "tax_area": area, "remark": "SETUP Lunch Cooler Setup", "on_po": True, "subledger": "",
         "subledger_type": "", "retainage": None, "retained": 0.0},
        {"order_number": "40917", "order_type": "OP", "order_company": "00210", "line": "14.000", "quantity": 1,
         "uom": "EA", "amount": 32.0, "account": "0312005561.6420.2210", "taxed": "Y", "tax_code": "V",
         "tax_area": area, "remark": "Charges not in the line table", "on_po": True, "subledger": "",
         "subledger_type": "", "retainage": None, "retained": 0.0},
    ]  # fmt: skip
    assert area and matched.balanced and jde_keying._check(matched) == "688.50 + 34.43 = 722.93: balanced"

    data, _, _ = jde_keying.build(invoices, js, promo_lookup, gl_names=NAMES, on=ON_DAY, po_matches=matches)
    book = openpyxl.load_workbook(io.BytesIO(data))
    rows = [list(r) for r in book["Keying sheet"].iter_rows(values_only=True)]
    at = rows.index(["Voucher match", *[None] * 11])
    assert rows[at + 1] == jde_keying.MATCH_COLUMNS
    assert rows[at + 2] == ["40917", "OP", "00210", "12.000", 100, "EA", 599, "0312005561.6420.2210", "Y", "V", area,
                            "CB-220 Non-Woven Lunch Cooler"]  # fmt: skip
    assert [r[3] for r in rows[at + 2 : at + 5]] == ["12.000", "13.000", "14.000"]
    assert rows[at + 5][:3] == ["Check", "Amounts matched + tax on the rows = gross", 688.5]
    assert ["Supplier number", "4471001", "Supplier name", PROMO] in [r[:4] for r in rows]
    assert ["G/L distribution", None, None, None] in [r[:4] for r in rows]  # the voucher with no PO: as before
    assert book["Keying sheet"].page_setup.orientation == "landscape"


def test_a_po_matched_voucher_with_a_line_on_no_po_line_is_not_ready_and_says_why(tmp_path):
    """D-264: no PO line is ever guessed. A line that matches none of the PO's, or a PO that is not loaded, holds the
    voucher on the "Not ready" sheet with why; without the PO-matched setting the same invoice is keyed with its G/L
    lines as before."""
    store = po_store(tmp_path)
    js = settings(po_matched=True)
    odd = po_invoice(9)
    odd["final_output"]["line_items"][1].update(description="Artwork proof", unit_price=45.0)
    invoices = [odd, po_invoice(10, po_number="40918")]
    ready, held = jde_keying.vouchers(invoices, js, promo_lookup, on=ON_DAY,
                                      po_matches=jde_keying.po_matches(store, invoices))  # fmt: skip
    assert ready == [] and [(h.invoice_id, h.why) for h in held] == [
        (9, ["line 2 “Artwork proof” matches no line of PO 40917 (none is guessed): correct the line or the PO "
             "number, or match it in JD Edwards by hand"]),
        (10, ["PO 40918 is not in the purchase orders loaded: load its open lines from JD Edwards (Purchase orders), "
              "then make the keying sheet again"])]  # fmt: skip
    (gl,), held = jde_keying.vouchers([odd], settings(), promo_lookup, on=ON_DAY,
                                      po_matches=jde_keying.po_matches(store, [odd]))  # fmt: skip
    assert held == [] and len(gl.lines) == 3 and gl.matches == []


def test_the_exports_page_keying_sheet_keys_a_po_matched_voucher_from_the_purchase_orders_loaded(tmp_path):
    """D-264: the Exports page's keying sheet (``exports.keying_file``) pairs a batch's PO-matched invoices with the
    purchase orders loaded, and says a quantity matched over what JD Edwards had open."""
    from ap_coder.web.pages.exports import keying_file

    store = po_store(tmp_path)
    js = settings(po_matched=True, an8_overrides={vendor_key(PROMO): "4471001"})
    jde_settings.save(store, js)
    inv = po_invoice()
    inv["final_output"]["line_items"][0].update(quantity=120, amount=718.8)
    final = {**inv["final_output"], "subtotal": 808.3, "tax_total": 40.42, "grand_total": 848.72,
             "tax_lines": [tl("GST", "", 0.05, 808.3, 40.42)]}  # fmt: skip
    invoice_id = store.add_invoice(tmp_path / "118204.pdf", final, {})
    store.approve_invoice(invoice_id, final, "Pat Tester")
    batch = store.create_export_batch([invoice_id], jde_keying.FORMAT, actor="Pat Tester")
    data, _, _ = keying_file(store, batch)
    rows = list(openpyxl.load_workbook(io.BytesIO(data))["Keying sheet"].iter_rows(values_only=True))
    assert ("40917", "OP", "00210", "12.000", 120, "EA", 718.8) in [r[:7] for r in rows]
    assert any(str(r[1] or "").startswith("Order 40917 line 12.000: 120 matched, but JD Edwards had 100 open")
               for r in rows if r[0] == "Warning")  # fmt: skip


def test_a_po_line_with_no_quantity_is_matched_by_its_amount_its_quantity_left_to_check(tmp_path):
    """D-264: JD Edwards' "Work With Order Details" grid gives each open line's amount to receive and no quantity: the
    line is matched by its amount. Quantity Matched is the quantity the invoice bills, or left empty when it bills
    none (the freight read as a charge with no quantity), and a note says to check it."""
    from ap_coder.bench import jde_exports
    from ap_coder.coding import po
    from ap_coder.core.store import Store
    from ap_coder.web.files import read_sheet
    from tests.pdfs import one_row_po_lines

    store = Store(tmp_path / "ap.db")
    sheet, _ = read_sheet("order_details.xlsx", jde_exports.order_details_xlsx(one_row_po_lines(), short=True))
    store.import_purchase_orders(po.rows_from_records(sheet.to_dict("records"), po.map_columns(list(sheet.columns)))[0])
    inv = po_invoice()
    inv["final_output"]["line_items"][2]["quantity"] = 0  # the freight: no quantity printed
    js = settings(po_matched=True)
    (v,), held = jde_keying.vouchers([inv], js, promo_lookup, on=ON_DAY,
                                     po_matches=jde_keying.po_matches(store, [inv]))  # fmt: skip
    assert held == [] and [(m.line, m.quantity, m.uom, m.amount) for m in v.matches] == [
        ("12.000", 100, "", 599.0), ("13.000", 1, "", 57.5), ("14.000", None, "", 32.0)]  # fmt: skip
    assert v.notes[-1] == ("Order 40917 line 14.000 has no quantity in the purchase orders loaded: it is matched by "
                           "its amount (32.00) and Quantity Matched is left empty. Check the quantity JD Edwards "
                           "shows for it.")  # fmt: skip
    assert "Quantity Matched is the invoice's 100" in v.notes[-3]
    data = jde_keying.build_xlsx([v], [])
    rows = [list(r) for r in openpyxl.load_workbook(io.BytesIO(data))["Keying sheet"].iter_rows(values_only=True)]
    assert ["40917", "OP", "00210", "14.000", None, None, 32] == next(r[:7] for r in rows if r[3] == "14.000")
    view = jde_review.voucher_view(inv["final_output"], js, "4471001", match=po.voucher_match(
        po.match_invoice(_coding_of(inv), store, 8), "40917"))  # fmt: skip
    assert [m["quantity_text"] for m in view["match"]] == ["100", "1", "—"]
    from ap_coder.web.invoice_coding import po_section  # the review screen's PO match says how it is matched

    shown = po_section(store, _coding_of(inv), 8)
    assert [r["matched"] for r in shown["rows"]][2] == "32.00 matched (no quantity on the PO)"


def _coding_of(inv):
    from ap_coder.core.schema import InvoiceCoding

    return InvoiceCoding.model_validate({k: x for k, x in inv["final_output"].items() if k != "gl_distribution"})


def test_a_po_lines_own_tax_that_is_not_what_the_invoice_charges_is_said_and_the_row_keeps_the_invoices(tmp_path):
    """D-264, D-265: with the PO file's Tax Y/N, Tax Expl and Tax Rate/Area (the order details grid with its columns
    added), a PO line whose tax is not what the invoice charges on its line is said, with what to type: each row
    carries the invoice's own tax (the GST charged on all three lines, Alberta's area), as JD Edwards works the tax out
    of the rows and the voucher must add up to the gross. It took the PO line's (N on the setup, Ontario's HST area
    on the freight), so JD Edwards would have worked out 2.88 of GST less, and 4.16 of HST more, than charged."""
    from dataclasses import replace

    from ap_coder.bench import jde_exports
    from ap_coder.coding import po
    from ap_coder.core.store import Store
    from ap_coder.web.files import read_sheet
    from tests.pdfs import one_row_po_lines

    bags, setup, freight = one_row_po_lines()
    lines = [bags, replace(setup, tax_flag="N", tax_code="E", tax_area=""), replace(freight, tax_area="ONHST")]
    store = Store(tmp_path / "ap.db")
    sheet, _ = read_sheet("order_details.xlsx", jde_exports.order_details_xlsx(lines))
    store.import_purchase_orders(po.rows_from_records(sheet.to_dict("records"), po.map_columns(list(sheet.columns)))[0])
    inv = po_invoice()
    js = settings(po_matched=True)
    (v,), held = jde_keying.vouchers([inv], js, promo_lookup, on=ON_DAY,
                                     po_matches=jde_keying.po_matches(store, [inv]))  # fmt: skip
    assert held == [] and v.tax_area == "AB-GST" and v.balanced
    assert [(m.quantity, m.uom, m.taxed, m.tax_code, m.tax_area) for m in v.matches] == [
        (100, "EA", "Y", "V", "AB-GST"), (1, "EA", "Y", "V", "AB-GST"), (1, "EA", "Y", "V", "AB-GST")]  # fmt: skip
    said = [n for n in v.notes if n.startswith("Order 40917 line")]
    assert said == [
        "Order 40917 line 12.000: JD Edwards has the PO line's tax as Y, V, CDNGST; type Y, V, AB-GST as on this row, "
        "which is what the invoice charges on it.",
        "Order 40917 line 13.000: JD Edwards has the PO line's tax as N, E, no tax area; type Y, V, AB-GST as on this "
        "row, which is what the invoice charges on it.",
        "Order 40917 line 14.000: JD Edwards has the PO line's tax as Y, V, ONHST; type Y, V, AB-GST as on this row, "
        "which is what the invoice charges on it.",
    ]


def test_a_credit_note_against_a_po_line_is_never_said_to_match_more_than_is_open(tmp_path):
    """B-2647: a credit note against a PO line JD Edwards had vouchered in full (0 open) was said to match "4 matched,
    but JD Edwards had 0 open": the quantity a credit gives back was held against what is open. A credit reduces what
    is matched; only an invoice can match more than is open."""
    from dataclasses import replace
    from decimal import Decimal

    from ap_coder.core.store import Store
    from tests.coding.test_po import _jde_pos
    from tests.pdfs import one_row_po_lines

    store = Store(tmp_path / "ap.db")
    bags, setup, freight = one_row_po_lines()
    _jde_pos(store, [replace(bags, vouchered=Decimal(100)), setup, freight])
    inv = po_invoice()
    final = inv["final_output"]
    final["line_items"] = [{**final["line_items"][0], "quantity": -4, "amount": -23.96}]
    final.update(subtotal=-23.96, tax_lines=[tl("GST", "", 0.05, -23.96, -1.2)], tax_total=-1.2, grand_total=-25.16)
    (v,), held = jde_keying.vouchers([inv], settings(po_matched=True), promo_lookup, on=ON_DAY,
                                     po_matches=jde_keying.po_matches(store, [inv]))  # fmt: skip
    assert held == [] and [(m.line, m.quantity, m.amount) for m in v.matches] == [("12.000", -4, -23.96)]
    assert v.balanced and not any("open" in w for w in v.warnings), v.warnings


# --- The tax on a voucher match, and charges not on the PO (D-265) ------------------------------------------------


def case_vouchers(tmp_path, name, js, setup=None, lines=None, items=None, grid=False, changes=None):
    """The keying sheet's (vouchers, held) of the test set's case ``name`` (``bench.jde_po_vouchers``) as AP Coder
    reads it, its PO loaded (``lines``: other open lines; ``grid``: from the order details grid with its tax columns),
    coded with ``setup`` (the QST recovered by default); ``items``: {line number: changes to it}; ``changes``: to the
    invoice's fields."""
    from ap_coder.bench import jde_company, jde_exports, jde_po_vouchers
    from ap_coder.coding import po
    from ap_coder.core.schema import InvoiceCoding
    from ap_coder.core.store import Store
    from ap_coder.web.files import read_sheet

    case = next(c for c in jde_po_vouchers.cases(jde_company.build(1, "small", 2025)) if c.name == name)
    store = Store(tmp_path / "ap.db")
    lines = list(case.po_lines if lines is None else lines)
    data = jde_exports.order_details_xlsx(lines) if grid else jde_exports.open_po_lines_xlsx(lines)
    sheet, _ = read_sheet("po.xlsx", data)
    store.import_purchase_orders(po.rows_from_records(sheet.to_dict("records"), po.map_columns(list(sheet.columns)))[0])
    final = {**case.coding(), **(changes or {})}
    for n, change in (items or {}).items():
        final["line_items"][n - 1].update(change)
    final["gl_distribution"] = exports.build_gl_distribution(InvoiceCoding.model_validate(final), setup or _setup())
    inv = {"id": 1, "final_output": final}
    return jde_keying.vouchers([inv], js, lambda f: str(case.supplier.number), on=ON_DAY,
                               po_matches=jde_keying.po_matches(store, [inv], js.charge_account))  # fmt: skip


def test_a_charge_not_on_the_po_holds_the_voucher_unless_an_account_is_chosen_for_it(tmp_path):
    """D-265: freight and a fuel surcharge the PO has no line for hold the voucher, as any line on no PO line (no PO
    line is guessed), until Settings → JD Edwards E1's "Charges not on the purchase order" adds them: then each is a
    line of its own on the account chosen, with its tax, marked "Not on the PO" on the sheet, and the voucher adds up
    to the gross."""
    from ap_coder.bench.jde_po_vouchers import FREIGHT_IN, pilot_settings

    held_js = pilot_settings()
    held_js.po_charges = jde_settings.CHARGES_HOLD
    ready, held = case_vouchers(tmp_path / "a", "ab_freight_not_on_po", held_js)
    assert ready == [] and [w.split(" (none")[0] for w in held[0].why] == [
        "line 2 “Freight” matches no line of PO 41110",
        "line 3 “Fuel surcharge” matches no line of PO 41110",
    ]
    (v,), held = case_vouchers(tmp_path / "b", "ab_freight_not_on_po", pilot_settings())
    assert held == [] and v.balanced
    assert [(m.on_po, m.line, m.amount, m.account, m.taxed, m.tax_code, m.tax_area) for m in v.matches] == [
        (True, "1.000", 945.0, "0021061010.7410.4107", "Y", "V", "CDNGST"),
        (False, "", 38.0, FREIGHT_IN, "Y", "V", "CDNGST"),
        (False, "", 6.84, FREIGHT_IN, "Y", "V", "CDNGST"),
    ]
    assert jde_keying._check(v) == "989.84 + 49.49 = 1,039.33: balanced"
    assert any(n.startswith("Line 3 “Fuel surcharge” (6.84) is not on PO 41110: it is keyed as a G/L line of its own "
                            f"on {FREIGHT_IN}") for n in v.notes)  # fmt: skip
    rows = [list(r) for r in openpyxl.load_workbook(io.BytesIO(jde_keying.build_xlsx([v], [])))["Keying sheet"]
            .iter_rows(values_only=True)]  # fmt: skip
    assert [r[:8] for r in rows if r[0] == jde_keying.NOT_ON_PO] == [
        [jde_keying.NOT_ON_PO, None, None, None, None, None, 38, FREIGHT_IN],
        [jde_keying.NOT_ON_PO, None, None, None, None, None, 6.84, FREIGHT_IN],
    ]
    assert next(r for r in rows if r[0] == "Check")[1:3] == ["Amounts matched + tax on the rows = gross", 989.84]


def test_a_line_charged_part_of_what_its_tax_area_holds_is_held_unless_its_po_line_has_an_area_of_its_own(tmp_path):
    """D-265: JD Edwards works out every tax its area holds on a row taxed Y: on BC's GST + PST area (code C), a line
    charged the GST and not the PST can't be keyed on it, and no area is guessed: the voucher is held with why. When
    the PO line has its own tax rate/area (the order details grid with its tax columns), its row takes that one, and
    it is said."""
    from dataclasses import replace

    from ap_coder.bench import jde_company, jde_po_vouchers

    js = jde_po_vouchers.area_settings()
    exempt = {2: {"taxes_applied": ["GST"], "tax_exempt": ["PST"]}}
    case = next(c for c in jde_po_vouchers.cases(jde_company.build(1, "small", 2025)) if c.name == "bc_gst_pst")
    vests = case.coding()["line_items"][1]["amount"]
    pst = round(480.0 * 0.07, 2)
    taxes = {"tax_lines": [tl("GST", "", 0.05, 915.0, 45.75), tl("PST", "BC", 0.07, 480.0, pst)],
             "tax_total": round(45.75 + pst, 2), "grand_total": round(915.0 + 45.75 + pst, 2)}  # fmt: skip

    def vouchers(where, lines=None, grid=False):
        return case_vouchers(tmp_path / where, "bc_gst_pst", js, lines=lines, items=exempt, grid=grid, changes=taxes)

    ready, held = vouchers("a")
    assert ready == [] and held[0].why == [
        "line 2 “Hi-vis safety vest, class 2” is charged the GST and not the PST, but tax area BC-GSTPST (code C) has "
        "JD Edwards work out both on its row: give its PO line a tax rate/area for the GST alone in JD Edwards and "
        "load the purchase orders again (with their Tax Rate/Area column), or key the voucher by hand"
    ]
    hats, own = case.po_lines
    (v,), held = vouchers("b", [replace(hats, tax_code="C", tax_area="BC-GSTPST"), replace(own, tax_area="BCGST")],
                          grid=True)  # fmt: skip
    assert held == [] and v.balanced and vests == 435.0
    assert [(m.taxed, m.tax_code, m.tax_area) for m in v.matches] == [("Y", "C", "BC-GSTPST"), ("Y", "V", "BCGST")]
    assert ("Order 41103 line 2.000 is charged the GST and not the PST: its row takes the PO line's own tax rate/area "
            "BCGST (code V). Check JD Edwards works out the GST alone on it.") in v.notes  # fmt: skip


def test_a_provincial_tax_jd_edwards_would_treat_otherwise_than_the_tax_setup_holds_the_voucher(tmp_path):
    """D-265: on a voucher match JD Edwards posts the tax its area holds as the code says: a QST the Tax setup recovers
    on a GST-only area (it would go nowhere), or one it keeps in the expense on Quebec's own area under code V (JD
    Edwards would recover it), holds the voucher with why and what to set; the QST kept in the expense on the GST-only
    area goes on a line of its own on the PO line's account (the test set's case)."""
    from ap_coder.bench.jde_po_vouchers import area_settings, pilot_settings

    ready, held = case_vouchers(tmp_path / "a", "qc_gst_qst", pilot_settings())  # the QST recovered
    assert ready == [] and held[0].why == [
        "the QST is recovered (GL accounts & tax), but tax area CDNGST (code V) has JD Edwards work out GST on a "
        "voucher match, so the QST would go nowhere: give QC a tax area and code that hold it (Settings → JD Edwards "
        "E1 → tax areas), or key the voucher by hand"
    ]
    expensed = _setup(QST=TaxTreatment("QST", "expense_to_line"))
    ready, held = case_vouchers(tmp_path / "b", "qc_gst_qst", area_settings(), expensed)
    assert ready == [] and held[0].why == [
        "the QST is kept in the expense (GL accounts & tax), but tax area QC-GSTQST with code V has JD Edwards recover "
        "it on a voucher match: set it to recoverable, or give QC a code that keeps it in the expense (C), in "
        "Settings → JD Edwards E1 → tax areas"
    ]
    (v,), held = case_vouchers(tmp_path / "c", "qc_gst_qst", pilot_settings(), expensed)
    assert held == [] and v.balanced and [(m.on_po, m.amount, m.taxed) for m in v.matches][-1] == (False, 66.63, "N")


def test_jd_edwards_working_out_another_tax_than_the_invoice_charges_is_a_warning(tmp_path):
    """D-265: JD Edwards works the tax out of the rows at the area's rate: when the invoice charges another (here a
    dollar more GST than 5% of the lines), the sheet warns to check it, the voucher still adds up to the gross."""
    from ap_coder.bench.jde_po_vouchers import pilot_settings

    more = {"tax_lines": [tl("GST", "", 0.05, 785.8, 40.29)], "tax_total": 40.29, "grand_total": 826.09}
    (v,), held = case_vouchers(tmp_path, "ab_gst", pilot_settings(), changes=more)
    assert held == [] and v.balanced
    assert (
        "At tax area CDNGST's 5% (GST), JD Edwards works out 39.29 on the rows taxed Y, but the invoice charges "
        "40.29: check the tax JD Edwards shows against the invoice before saving the voucher."
    ) in v.warnings


def test_a_tax_keyed_on_a_line_of_its_own_carries_the_subledger_of_the_po_line_it_goes_with(tmp_path):
    """B-2661: BC's PST kept in the expense on the pilot's GST-only tax area is keyed on a G/L line of its own on the
    PO line's account (D-265). A PO line coded to an account whose posting edit code is L carries its subledger and
    type (D-268), and its rows had them, but the PST's line on that account had none: JD Edwards refuses a line on an
    L account without its subledger. It carries the PO line's, a line for each subledger."""
    from ap_coder.bench import jde_company, jde_exports, jde_po_vouchers
    from ap_coder.coding import po
    from ap_coder.core.schema import InvoiceCoding
    from ap_coder.core.store import Store
    from ap_coder.web.files import read_sheet

    case = next(c for c in jde_po_vouchers.cases(jde_company.build(1, "small", 2025)) if c.name == "bc_gst_pst")
    store = Store(tmp_path / "ap.db")
    sheet, _ = read_sheet("po.xlsx", jde_exports.open_po_lines_xlsx(case.po_lines))
    rows = po.rows_from_records(sheet.to_dict("records"), po.map_columns(list(sheet.columns)))[0]
    for row, subledger in zip(rows, ("00012345", "00067890"), strict=True):
        row.update(subledger=subledger, subledger_type="A")
    store.import_purchase_orders(rows)
    final = case.coding()
    final["gl_distribution"] = exports.build_gl_distribution(InvoiceCoding.model_validate(final), _setup())
    inv = {"id": 1, "final_output": final}
    js = jde_po_vouchers.pilot_settings()
    (v,), held = jde_keying.vouchers([inv], js, lambda f: str(case.supplier.number), on=ON_DAY,
                                     po_matches=jde_keying.po_matches(store, [inv], js.charge_account))  # fmt: skip
    assert held == [] and v.balanced
    assert [(m.on_po, m.amount, m.subledger, m.subledger_type) for m in v.matches] == [
        (True, 480.0, "00012345", "A"), (True, 435.0, "00067890", "A"),
        (False, 33.6, "00012345", "A"), (False, 30.45, "00067890", "A")]  # fmt: skip


def test_a_us_companys_po_matched_voucher_keys_the_sales_tax_charged_on_lines_of_their_own(tmp_path):
    """B-2662: a company not checked for Canadian tax (a US one, D-267) has its voucher keyed with the exempt code,
    JD Edwards working out no tax, the tax kept as charged. Its voucher match rows are the lines before tax, so with
    the sales tax charged they never added up to the gross and every such voucher was held ("check the lines and the
    taxes against the invoice"). The tax charged goes on a G/L line of its own on each PO line's account, Tax N, as
    a tax JD Edwards does not work out does (D-265), and the voucher adds up to the gross."""
    from ap_coder.core.schema import COMPANY_TAX
    from ap_coder.core.store import Store
    from tests.erp.test_jde import USD, _invoice

    area = {COMPANY_TAX: {"tax_area": "WA-KING", "from": "PO 30981"}}
    inv = _invoice(USD, company="00310", po_number="30981", **area)
    final = inv["final_output"]
    store = Store(tmp_path / "ap.db")
    store.import_purchase_orders([
        {"po_number": "30981", "vendor_name": final["vendor_name"], "supplier_number": "71008", "order_type": "OP",
         "order_company": "00310", "line_number": li["line_number"], "erp_line": f"{li['line_number']}.000",
         "description": li["description"], "quantity": li["quantity"], "unit_price": li["unit_price"],
         "amount": li["amount"], "open_quantity": li["quantity"], "uom": "EA", "gl_code": "6420" if n < 2 else "1450",
         "cost_center": "3100", "status": "open", "currency": "USD"}
        for n, li in enumerate(final["line_items"])])  # fmt: skip
    js = settings(po_matched=True)
    js.company_currencies = {"00310": "USD"}
    (v,), held = jde_keying.vouchers([inv], js, lambda f: "71008", on=ON_DAY,
                                     po_matches=jde_keying.po_matches(store, [inv]))  # fmt: skip
    assert held == [] and v.balanced, held
    assert [(m.on_po, m.amount, m.account, m.taxed, m.tax_code, m.tax_area) for m in v.matches] == [
        (True, 735.0, "3100.6420", "N", "E", "WA-KING"), (True, 3600.0, "3100.6420", "N", "E", "WA-KING"),
        (True, 2480.0, "3100.1450", "N", "E", "WA-KING"), (True, 45.0, "3100.1450", "N", "E", "WA-KING"),
        (False, 448.67, "3100.6420", "N", "E", ""), (False, 261.34, "3100.1450", "N", "E", "")]  # fmt: skip
    assert jde_keying._check(v) == "7,570.01 + 0.00 = 7,570.01: balanced"


def test_what_a_keying_sheet_matches_on_one_po_line_is_held_against_what_is_open_all_together(tmp_path):
    """B-2663: the quantity matched over what JD Edwards had open on a PO line was looked for row by row: an invoice
    billing a line's bags on two lines (60 and 60 of the 100 open) said twice "60 of the 100 open matched; the line
    stays open for 40" and warned of nothing, and so did two invoices of 60 each on one keying sheet; JD Edwards takes
    the first 60 and has 40 left for the rest. What a sheet matches on a PO line is added up, in its order."""
    store = po_store(tmp_path)
    js = settings(po_matched=True)

    def bags(inv_id, *quantities):
        inv = po_invoice(inv_id)
        final = inv["final_output"]
        first = final["line_items"][0]
        final["line_items"] = [{**first, "line_number": n, "quantity": q, "amount": round(q * 5.99, 2)}
                               for n, q in enumerate(quantities, 1)]  # fmt: skip
        subtotal = round(sum(li["amount"] for li in final["line_items"]), 2)
        tax = round(subtotal * 0.05, 2)
        final.update(invoice_number=f"1182{inv_id}", subtotal=subtotal, tax_lines=[tl("GST", "", 0.05, subtotal, tax)],
                     tax_total=tax, grand_total=round(subtotal + tax, 2))  # fmt: skip
        return inv

    def said(invoices):
        ready, held = jde_keying.vouchers(invoices, js, promo_lookup, on=ON_DAY,
                                          po_matches=jde_keying.po_matches(store, invoices))  # fmt: skip
        assert held == []
        return [([w for w in v.warnings if "open" in w], [n for n in v.notes if "open" in n]) for v in ready]

    assert said([bags(1, 60, 60)]) == [(["Order 40917 line 12.000: 120 matched, but JD Edwards had 100 open at "
                                         "the purchase orders' last load: it matches no more than is open."],
                                        [])]  # fmt: skip
    assert said([bags(1, 60), bags(2, 60)]) == [
        ([], ["Order 40917 line 12.000: 60 of the 100 open matched; the line stays open for 40 (do not close it in "
              "Voucher Match)."]),
        (["Order 40917 line 12.000: 60 matched, 120 with the 60 above on this sheet, but JD Edwards had 100 open at "
          "the purchase orders' last load: it matches no more than is open."], []),
    ]  # fmt: skip
    assert said([bags(1, 30), bags(2, 30)])[1] == ([], [
        "Order 40917 line 12.000: 30 of the 100 open matched, 60 with the 30 above on this sheet; the line stays open "
        "for 40 (do not close it in Voucher Match)."])  # fmt: skip


def test_a_voucher_match_the_keying_sheet_would_hold_is_a_check_before_approval(tmp_path):
    """B-2667: a PO-matched voucher's rows the keying sheet can't make for a reason no PO check gives (here a BC line
    charged the GST and not the PST on BC's GST + PST area, its PO line with no area of its own, D-265) were said only
    in the voucher block on the review screen: no check, so the invoice was approved, touchless too, and was then on
    the keying sheet's "Not ready" sheet (what D-75 says never happens). It is the check PO_VOUCHER_MATCH, a warning
    (keyed by hand, it is approved anyway); what a PO check says already (a line on no PO line) is not said twice."""
    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.bench import jde_company, jde_exports, jde_po_vouchers
    from ap_coder.coding import po
    from ap_coder.core.config import Settings
    from ap_coder.core.schema import InvoiceCoding
    from ap_coder.core.store import Store
    from ap_coder.web.files import read_sheet

    case = next(c for c in jde_po_vouchers.cases(jde_company.build(1, "small", 2025)) if c.name == "bc_gst_pst")
    store = Store(tmp_path / "ap.db")
    sheet, _ = read_sheet("po.xlsx", jde_exports.open_po_lines_xlsx(case.po_lines))
    store.import_purchase_orders(po.rows_from_records(sheet.to_dict("records"), po.map_columns(list(sheet.columns)))[0])
    codes = {li["predicted_gl_code"] for li in case.coding()["line_items"]} | {"2310", "2320"}
    store.import_accounts("gl_accounts", [{"code": code} for code in sorted(codes)], "code", None)
    store.set_setting(jde_settings.SHOW_SETTING, "1")
    jde_settings.save(store, jde_po_vouchers.area_settings())
    final = case.coding()
    final["line_items"][1].update(taxes_applied=["GST"], tax_exempt=["PST"])
    pst = round(480.0 * 0.07, 2)
    final.update(tax_lines=[tl("GST", "", 0.05, 915.0, 45.75), tl("PST", "BC", 0.07, 480.0, pst)],
                 tax_total=round(45.75 + pst, 2), grand_total=round(915.0 + 45.75 + pst, 2))  # fmt: skip

    def issues(coding):
        _, report = finalise_coding(InvoiceCoding.model_validate(coding), store.reference_data(), Settings(),
                                    store=store)  # fmt: skip
        return {i.code: (i.severity, i.message) for i in report.issues if i.code.startswith("PO_")}

    found = issues(final)
    assert found["PO_VOUCHER_MATCH"] == ("warning", (
        "JD Edwards' voucher match can't be keyed from the keying sheet: line 2 “Hi-vis safety vest, class 2” is "
        "charged the GST and not the PST, but tax area BC-GSTPST (code C) has JD Edwards work out both on its row: "
        "give its PO line a tax rate/area for the GST alone in JD Edwards and load the purchase orders again (with "
        "their Tax Rate/Area column), or key the voucher by hand"))  # fmt: skip
    odd = {**final, "line_items": [final["line_items"][0], {**final["line_items"][1], "description": "Artwork proof"}]}
    assert "PO_VOUCHER_MATCH" not in issues(odd) and "PO_LINE_NOT_ON_PO" in issues(odd)  # (said by the PO check)
    assert "PO_VOUCHER_MATCH" not in issues(case.coding())  # (keyed: nothing to say)


def test_a_goods_line_printed_with_no_quantity_matches_its_amount_at_the_po_lines_unit_cost(tmp_path):
    """B-2669 (D-283): an invoice line of goods printed with no quantity was matched with Quantity Matched 1 whatever
    its PO line, so 599.00 of the 100 bags at 5.99 open was keyed as 1 bag, and JD Edwards would leave 99 open on a
    line billed in full. Its amount at the PO line's unit cost is the quantity when that comes out whole (within the
    unit cost's tolerance, to the cent), said under the voucher; else it stays 1, and the sheet says to check it."""
    store = po_store(tmp_path)
    inv = po_invoice()
    final = inv["final_output"]
    final["line_items"][0]["quantity"] = 0  # 599.00 of bags, no quantity printed
    final["line_items"][1].update(quantity=0, amount=86.25)  # 1.5 setups' worth: no whole number of 57.50
    final.update(subtotal=717.25, tax_lines=[tl("GST", "", 0.05, 717.25, 35.86)], tax_total=35.86,
                 grand_total=753.11)  # fmt: skip
    (v,), held = jde_keying.vouchers([inv], settings(po_matched=True), promo_lookup, on=ON_DAY,
                                     po_matches=jde_keying.po_matches(store, [inv]))  # fmt: skip
    assert held == [] and v.balanced
    assert [(m.line, m.quantity, m.amount) for m in v.matches] == [("12.000", 100, 599.0), ("13.000", 1, 86.25),
                                                                   ("14.000", 1, 32.0)]  # fmt: skip
    assert ("Order 40917 line 12.000: the invoice prints no quantity; Quantity Matched is 100, its amount (599.00) at "
            "the PO line's unit cost 5.99.") in v.notes  # fmt: skip
    assert ("Order 40917 line 13.000: the invoice prints no quantity and its amount (86.25) is no whole number of the "
            "PO line's unit cost 57.50: Quantity Matched is 1. Check the quantity JD Edwards shows for "
            "it.") in v.notes  # fmt: skip


def _approved_po_invoice(store, tmp_path, js):
    """``po_invoice`` approved as the review screen approves it (its output from ``finalise_coding``, JD Edwards in
    use with ``js``), on a keying sheet batch: (its id, the batch)."""
    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.core.config import Settings

    store.set_setting(jde_settings.SHOW_SETTING, "1")
    jde_settings.save(store, js)
    store.import_accounts("gl_accounts", [{"code": "6420.2210"}], "code", None)
    inv = po_invoice()
    invoice_id = store.add_invoice(tmp_path / "118204.pdf", inv["final_output"], {})
    output, _ = finalise_coding(_coding_of(inv), store.reference_data(), Settings(), store=store,
                                exclude_invoice_id=invoice_id)  # fmt: skip
    store.approve_invoice(invoice_id, output, "Pat Tester")
    return invoice_id, store.create_export_batch([invoice_id], jde_keying.FORMAT, actor="Pat Tester")


def test_a_keying_sheet_made_again_after_the_pos_are_loaded_again_keeps_the_rows_matched_at_approval(tmp_path):
    """B-2671 (D-284): a keying sheet downloaded again after the purchase orders were loaded again (the vouchers keyed,
    JD Edwards closed the bags' line and left it out of its open lines) made the approved invoice's voucher match
    afresh: its bags matched another line or none, the voucher "Not ready". What was matched at approval is kept with
    the invoice, and the sheet keys that each time."""
    from dataclasses import replace
    from decimal import Decimal

    from ap_coder.web.pages.exports import keying_file
    from tests.coding.test_po import _jde_pos
    from tests.pdfs import one_row_po_lines

    store = po_store(tmp_path)
    js = settings(po_matched=True, an8_overrides={vendor_key(PROMO): "4471001"})
    _, batch = _approved_po_invoice(store, tmp_path, js)

    def sheet():
        book = openpyxl.load_workbook(io.BytesIO(keying_file(store, batch)[0]))
        return [[list(r) for r in book[name].iter_rows(values_only=True)] for name in book.sheetnames]

    first = sheet()
    assert [r[3] for r in first[0] if r[0] == "40917"] == ["12.000", "13.000", "14.000"] and len(first[1]) == 1
    bags, setup, freight = one_row_po_lines()
    _jde_pos(store, [replace(bags, vouchered=Decimal(100), closed=True), setup, freight])
    assert sheet() == first


def test_a_canadian_companys_po_matched_voucher_charged_another_tax_says_why_it_is_held(tmp_path):
    """B-2673: a Canadian company's PO-matched invoice charged a tax that is none of the GST, HST, PST and QST (a tax
    line of type OTHER: a foreign sales tax, a levy read as a tax) was held by the gross check, "the amounts matched
    (688.50) and the tax JD Edwards works out on them (34.43) add up to 722.93, not the gross 727.93: check the lines
    and the taxes against the invoice", though both were right: that tax is on no row and JD Edwards works out none of
    it. It is held for that, said."""
    store = po_store(tmp_path)
    inv = po_invoice()
    final = inv["final_output"]
    final["tax_lines"].append(tl("OTHER", "", 0.0, 688.5, 5.0))
    final.update(tax_total=39.43, grand_total=727.93)
    ready, held = jde_keying.vouchers([inv], settings(po_matched=True), promo_lookup, on=ON_DAY,
                                      po_matches=jde_keying.po_matches(store, [inv]))  # fmt: skip
    assert ready == [] and held[0].why == [
        "the invoice charges 5.00 of a tax that is none of the GST, HST, PST and QST (a tax line of type OTHER): JD "
        "Edwards works out none of it on a voucher match and it is on no row. Correct the tax line if it is one of "
        "them, or key the voucher by hand"]  # fmt: skip
