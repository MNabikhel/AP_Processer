"""The reader of JD Edwards' report R09321 "Transaction Journal" saved as CSV (erp/journal.py).

The journals are written here, in the report's own layout, with invented values: a made-up company, accounts,
suppliers and amounts."""

import csv
import dataclasses
import io
import json
from decimal import Decimal

import pytest

from ap_coder.erp import journal as jr

COMPANY = "06120"
MAIN, FLOOR, SHORT, EIGHT = "0612038810", "0612038825", "61204", "06120377"  # business units of 10, 5 and 8 digits
TRADE, INTERCO, GST = "2105", "2140", "2310"
OBJECTS = jr.PayableObjects(trade=(TRADE,), intercompany=(INTERCO,))
ACCOUNT_NAMES = {  # object -> the account's name; the report cuts it at 24 characters
    "7410": "Courier & Freight - General",
    "7630": "Legal Fees",
    "7715": "Conferences & Seminars - Staff",
    "1480": "Shared Services Receivable",
    "7905": "Community Sponsorships",
    "7520": "Language Training",
    "7200": "Office Supplies - General",
    "7340": "Computer Equipment under 2500",
    TRADE: "Trade Accounts Payable - Other",
    INTERCO: "Intercompany Payable",
    GST: "GST Clearing Account",
}
PAGE = "page"  # among a voucher's lines: the report starts a new page here

TITLES = [
    ["R09321", *[""] * 7, "NORTHFIELD GROUP OF COMPANIES", *[""] * 9, "9/14/2026", "08:41:07"],
    [*[""] * 8, "Transaction Journal", *[""] * 9, "Page -", "1"],
    ["Company", "", "NORTHFIELD BUILDERS INC."],
    ["Tax ID", "", "700000019RT0001"],
    ["From", "", "", "Thru"],
]
HEADER = [
    ["Document", *[""] * 14, "Address"],
    ["Number", "  Co", " G/L Date", "                       Account Number", "", "", "",
     "               Description", "", "      Debit Amount", "", "     Credit Amount", "",
     "             Remark", "", " Number", "                   Name"],
]  # fmt: skip


def line(account, debit="", credit="", remark="", supplier=True):
    """A voucher's line as the report prints it; ``supplier`` False: the row ends after the remark."""
    return (account, debit, credit, remark, supplier)


def voucher(date, number, name, *lines, doc_type="PV"):
    return {"date": date, "number": number, "name": name, "lines": lines, "doc_type": doc_type}


def vouchers():
    """Seven vouchers, one for each thing the report does."""
    return [
        # two coding lines on two business units, an amount with no leading zero, a date that reads two ways
        voucher("3/7/2025", "300417", "Kestrel Courier & Freight Ltd.",
                line(f"{MAIN}.7410.2260", "118.40", "", "courier Feb"),
                line(f"{FLOOR}.7410.2260", ".26", "", "courier Feb 4th floor"),
                line(f"{MAIN}.{TRADE}", "", "124.59", "courier Feb 4th floor"),
                line(f"{MAIN}.{GST}", "5.93", "", "courier Feb 4th floor")),
        # a name with commas (in quotes), an 8-digit business unit, a subsidiary of letters, a last row that ends
        # before the supplier
        voucher("11/30/2025", "300652", "Hollis, Pryce, Tan & Okafor LLP",
                line(f"{EIGHT}.7630.A14", "2400.00", "", "legal fees file 2291"),
                line(f"{MAIN}.{TRADE}", "", "2520.00", "legal fees file 2291"),
                line(f"{MAIN}.{GST}", "120.00", "", "legal fees file 2291", supplier=False)),
        # a negative voucher: money back from an employee; a 5-digit business unit, an 8-digit subsidiary
        voucher("1/15/2026", "300988", "Okonkwo-Reyes, M.",
                line(f"{SHORT}.7715.00381204", "", "75.00", "refund conference fee"),
                line(f"{MAIN}.{TRADE}", "75.00", "", "refund conference fee")),
        # an affiliate: the payable line is the intercompany one
        voucher("2/2/2026", "301200", "Meridian Holdings (West) Inc.",
                line(f"{MAIN}.1480.6001", "900.00", "", "shared services Jan"),
                line(f"{MAIN}.{INTERCO}", "", "900.00", "shared services Jan")),
        # does not balance: the payable and tax lines have the same amount in both columns
        voucher("2/14/2026", "301355", "Lantern Festival Society",
                line(f"{MAIN}.7905.3300", "500.00", "", "sponsorship table of 8"),
                line(f"{MAIN}.{TRADE}", "525.00", "525.00", "sponsorship table of 8"),
                line(f"{MAIN}.{GST}", "25.00", "25.00", "sponsorship table of 8")),
        # no remarks, no subsidiary, an accented name, and a first row that ends before the supplier
        voucher("12/1/2025", "301478", "École de langues Bélanger",
                line(f"{MAIN}.7520", "310.00", "", "", supplier=False),
                line(f"{MAIN}.{TRADE}", "", "310.00", "")),
        # a new page in the middle of the voucher
        voucher("4/9/2026", "301690", "Tamarack Office Outfitters Corp.",
                line(f"{MAIN}.7200.1105", "61.15", "", "toner 4th floor"),
                PAGE,
                line(f"{EIGHT}.7340.5512", "1289.00", "", "monitor for RJ"),
                line(f"{MAIN}.{TRADE}", "", "1417.66", "monitor for RJ"),
                line(f"{MAIN}.{GST}", "67.51", "", "monitor for RJ")),
    ]  # fmt: skip


def rows_of(found, *, header=True, grand=None):
    """The report's rows: five title rows, the header split over two rows (``header`` False: none), each voucher's
    first row and the rows that continue it, the page header again at ``PAGE``, the company's total row (84 cells)
    and ``Grand Total`` (``grand``: (debit, credit) to print instead of the true ones; False: no total rows)."""
    top = TITLES + (HEADER if header else [])
    rows = [list(row) for row in top]
    debit = credit = Decimal(0)
    for v in found:
        head = [v["doc_type"], COMPANY, v["date"]]
        for item in v["lines"]:
            if item == PAGE:
                rows += [list(row) for row in top]
                continue
            account, dr, cr, remark, supplier = item
            unit, _, rest = account.partition(".")
            name = ACCOUNT_NAMES.get(rest.partition(".")[0], "")[:24]
            row = [*head, f"{unit:>12}.{rest}", "", "", "", name, "", dr, "", cr, "", remark[:30]]
            rows.append(row + (["", v["number"], v["name"].upper()[:26]] if supplier else []))
            head = ["", "", ""]
            debit, credit = debit + Decimal(dr or 0), credit + Decimal(cr or 0)
    if grand is not False:
        debit, credit = grand or (debit, credit)
        rows.append([*[""] * 9, f"{debit:.2f}", "", f"{credit:.2f}", *[""] * 71, "NORTHFIELD BUILDERS INC."])
        rows.append([*[""] * 7, "Grand Total", "", f"{debit:.2f}", "", f"{credit:.2f}"])
    return rows


def as_csv(rows, *, shift=0, delimiter=",", encoding="utf-8-sig"):
    """The rows as the file JD Edwards saves: a byte-order mark, CRLF, quotes only where needed. ``shift``: that
    many empty columns in front of every row."""
    text = io.StringIO()
    csv.writer(text, delimiter=delimiter, lineterminator="\r\n").writerows([""] * shift + row for row in rows)
    return text.getvalue().encode(encoding)


def read(data):
    return jr.read_journal(data, payable_objects=OBJECTS, tax_objects=[GST])


@pytest.fixture(scope="module")
def journal():
    return read(as_csv(rows_of(vouchers())))


def test_the_made_up_file_has_the_reports_layout():
    data = as_csv(rows_of(vouchers()))
    assert data.startswith(b"\xef\xbb\xbfR09321,")
    assert data.count(b"\r\n") == data.count(b"\n") == 36
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))
    assert {len(row) for row in rows} == {3, 4, 12, 14, 16, 17, 20, 84}
    assert rows[7][:4] == ["PV", COMPANY, "3/7/2025", "  0612038810.7410.2260"]  # row 8: the first data row
    assert rows[8][:3] == ["", "", ""]  # a row that continues the voucher
    assert b'"HOLLIS, PRYCE, TAN & OKAFO"' in data  # the name, cut at 26 characters, in quotes for its commas
    assert rows[-2][83] == "NORTHFIELD BUILDERS INC." and rows[-1][7] == "Grand Total"


def test_a_voucher_is_a_first_row_and_the_rows_that_continue_it(journal):
    assert [v.row for v in journal.vouchers] == [8, 12, 15, 17, 19, 22, 24]  # its id: the row it starts on
    assert [len(v.lines) for v in journal.vouchers] == [4, 3, 2, 2, 3, 2, 4]
    first = journal.vouchers[0]
    assert (first.doc_type, first.company, first.gl_date) == ("PV", COMPANY, "2025-03-07")  # m/d/yyyy: March 7
    assert (first.supplier_number, first.supplier_name) == ("300417", "KESTREL COURIER & FREIGHT")
    assert [line.row for line in first.lines] == [8, 9, 10, 11]
    assert [v.gl_date for v in journal.vouchers[1:]] == [
        "2025-11-30", "2026-01-15", "2026-02-02", "2026-02-14", "2025-12-01", "2026-04-09",
    ]  # fmt: skip


def test_an_account_is_split_into_business_unit_object_and_subsidiary(journal):
    def parts(v, n):
        found = journal.vouchers[v].lines[n]
        return found.account, found.business_unit, found.object, found.subsidiary

    assert parts(0, 0) == ("0612038810.7410.2260", MAIN, "7410", "2260")  # the padding to 12 is gone
    assert parts(1, 0) == ("06120377.7630.A14", EIGHT, "7630", "A14")  # 8 digits; letters
    assert parts(2, 0) == ("61204.7715.00381204", SHORT, "7715", "00381204")  # 5 digits; 8 digits
    assert parts(5, 0) == ("0612038810.7520", MAIN, "7520", "")  # no subsidiary
    assert parts(0, 2) == ("0612038810.2105", MAIN, TRADE, "")


def test_a_line_has_its_amount_remark_description_and_kind(journal):
    first = journal.vouchers[0]
    assert [line.amount for line in first.lines] == [118.40, 0.26, -124.59, 5.93]  # debit - credit; ".26"
    assert [line.kind for line in first.lines] == [jr.CODING, jr.CODING, jr.PAYABLE, jr.TAX]
    assert first.lines[1].remark == "courier Feb 4th floor"
    assert first.lines[0].description == "Courier & Freight - Gene"  # the account's name, 24 characters
    assert journal.vouchers[3].lines[1].kind == jr.PAYABLE  # the intercompany payable line
    assert [line.remark for line in journal.vouchers[5].lines] == ["", ""]  # a voucher with no remarks


def test_gross_and_tax_come_from_the_payable_and_tax_lines(journal):
    assert [(v.gross, v.tax) for v in journal.vouchers] == [
        (124.59, 5.93), (2520.0, 120.0), (-75.0, 0.0), (900.0, 0.0), (0.0, 0.0), (310.0, 0.0), (1417.66, 67.51),
    ]  # fmt: skip
    assert [v.credit for v in journal.vouchers] == [False, False, True, False, False, False, False]
    assert [v.affiliate for v in journal.vouchers] == [False, False, False, True, False, False, False]


def test_a_voucher_that_does_not_balance_is_kept_and_marked(journal):
    assert [v.balanced for v in journal.vouchers] == [True, True, True, True, False, True, True]
    odd = journal.vouchers[4]
    assert odd.both_columns and [v.both_columns for v in journal.vouchers].count(True) == 1
    assert [(line.kind, line.amount) for line in odd.lines] == [
        (jr.CODING, 500.0), (jr.PAYABLE, 0.0), (jr.TAX, 0.0),
    ]  # fmt: skip
    assert (
        "1 voucher where the debits and the credits are not the same amount (kept and marked): "
        "row 19 (301355 LANTERN FESTIVAL SOCIETY)." in journal.notes
    )
    assert (
        "1 voucher with a line that has an amount in both the debit and the credit column (kept and marked): row 19."
        in journal.notes
    )


def test_balanced_means_within_a_cent():
    def balanced(credit):
        one = voucher("5/5/2026", "302001", "Any Supplier", line(f"{MAIN}.7200.1105", "10.00"),
                      line(f"{MAIN}.{TRADE}", "", credit))  # fmt: skip
        return read(as_csv(rows_of([one]))).vouchers[0].balanced

    assert balanced("10.00") and balanced("10.01") and balanced("9.99")
    assert not balanced("10.02") and not balanced("9.98")


def test_the_supplier_comes_from_any_line_of_the_voucher(journal):
    school = journal.vouchers[5]  # its first row ends after the remark; the second voucher's last row does
    assert journal.vouchers[1].supplier_number == "300652"
    assert (school.supplier_number, school.supplier_name) == ("301478", "ÉCOLE DE LANGUES BÉLANGER")
    assert journal.vouchers[1].supplier_name == "HOLLIS, PRYCE, TAN & OKAFO"  # the commas were inside quotes
    assert all(len(v.supplier_name) <= 26 for v in journal.vouchers)


def test_a_page_header_in_the_middle_of_a_voucher_is_skipped(journal):
    last = journal.vouchers[6]
    assert [line.row for line in last.lines] == [24, 32, 33, 34]  # rows 25 to 31 are the next page's header
    assert last.balanced and last.gross == 1417.66
    assert "Skipped: 14 title or page header rows and 2 total rows." in journal.notes


def test_the_totals_and_the_control_total(journal):
    totals = journal.totals
    assert (totals.vouchers, totals.lines, totals.coding, totals.payable, totals.tax) == (7, 20, 9, 7, 4)
    assert (totals.first_date, totals.last_date) == ("2025-03-07", "2026-04-09")
    assert (totals.debit, totals.credit) == (6397.25, 5897.25)  # every line read, both columns of the odd ones
    assert (totals.file_debit, totals.file_credit) == (6397.25, 5897.25)  # the Grand Total row
    assert not totals.differ
    assert journal.notes[-1] == (
        "No row is missing: the lines read match the report's Grand Total row (debits 6,397.25 and credits "
        "5,897.25, as the report prints them)."
    )


def test_the_notes_say_what_was_read(journal):
    assert journal.notes[:2] == (
        "Read: 7 vouchers with 20 lines, G/L dates 2025-03-07 to 2026-04-09.",
        "The lines by kind: 9 coding (what AP chose), 7 payable and 4 tax (what JD Edwards added).",
    )
    assert "1 voucher with an intercompany payable line (an affiliate's)." in journal.notes
    assert "1 voucher with a negative amount (a credit)." in journal.notes
    assert not any("could not be read" in note or "No header" in note for note in journal.notes)


def test_a_grand_total_that_differs_is_said_and_flagged():
    wrong = read(as_csv(rows_of(vouchers(), grand=(Decimal("6397.25"), Decimal("5897.24")))))
    assert wrong.totals.differ and (wrong.totals.file_debit, wrong.totals.file_credit) == (6397.25, 5897.24)
    assert wrong.notes[-1] == (
        "Warning: the lines read do not add up to the report's Grand Total. The lines read: debits 6,397.25 and "
        "credits 5,897.25. The Grand Total row: debits 6,397.25 and credits 5,897.24. Rows are missing from this "
        "file or could not be read."
    )
    assert len(wrong.vouchers) == 7  # nothing is refused here: the caller decides


def test_a_file_with_rows_missing_does_not_add_up():
    rows = rows_of(vouchers())
    del rows[12:14]  # two rows of the second voucher were lost on the way
    cut = read(as_csv(rows))
    assert cut.totals.differ and cut.totals.lines == 18
    assert not cut.vouchers[1].balanced


def test_a_file_with_no_grand_total_row_says_it_could_not_be_checked():
    cut = read(as_csv(rows_of(vouchers(), grand=False)))
    assert (cut.totals.file_debit, cut.totals.file_credit, cut.totals.differ) == (None, None, False)
    assert cut.totals.debit == 6397.25
    assert cut.notes[-1].startswith("The file has no Grand Total row") and "cut short" in cut.notes[-1]


def test_rows_that_cannot_be_read_are_left_out_and_named():
    rows = rows_of(vouchers()[:1], grand=(Decimal("124.59"), Decimal("124.59")))
    account = f"{MAIN:>12}.7200.1105"
    rows.insert(7, ["", "", "", account, "", "", "", "", "", "40.00", "", "", "", "before any voucher"])  # row 8
    rows.insert(10, ["", "", "", account, "", "", "", "", "", "n/a", "", "", "", "not an amount"])  # row 11
    rows.insert(11, ["", "", "", account, "", "", "", "", "", "", "", "", "", "no amount"])  # row 12
    rows.insert(12, ["", COMPANY, "3/7/2025", account, "", "", "", "", "", "9.00", "", "", "", "no type"])  # row 13
    rows.insert(15, ["PV", COMPANY, "3/9/2025", account, "", "", "", "", "", "n/a", "", "", "", "alone"])  # row 16
    found = read(as_csv(rows))
    assert len(found.vouchers) == 1 and [line.row for line in found.vouchers[0].lines] == [9, 10, 14, 15]
    assert (
        "5 rows left out, because the account, the amount or the voucher it belongs to could not be read: "
        "row 8, 11, 12, 13, 16." in found.notes
    )
    assert not found.totals.differ  # the rows left out are not in the lines' total either


def test_a_long_list_in_a_note_is_cut_short():
    one = voucher("5/5/2026", "302001", "Any Supplier", line(f"{MAIN}.7200.1105", "10.00"))
    found = read(as_csv(rows_of([one] * 8)))
    note = next(note for note in found.notes if "not the same amount" in note)
    assert note.startswith("8 vouchers where") and note.count("302001 ANY SUPPLIER") == 5
    assert note.endswith("row 12 (302001 ANY SUPPLIER) and 3 more.")


def test_a_voucher_whose_date_cannot_be_read_is_kept_and_said():
    one = voucher("sometime", "302001", "Any Supplier", line(f"{MAIN}.7200.1105", "10.00"),
                  line(f"{MAIN}.{TRADE}", "", "10.00"))  # fmt: skip
    found = read(as_csv(rows_of([one])))
    assert found.vouchers[0].gl_date == "" and found.totals.first_date == ""
    assert "1 voucher with no G/L date that could be read: row 8." in found.notes
    assert found.notes[0] == "Read: 1 voucher with 2 lines."


def test_the_columns_are_found_by_the_headers_words(journal):
    shifted = as_csv(rows_of(vouchers()), shift=1)  # an empty column in front: every position is one further
    assert read(shifted).vouchers == journal.vouchers
    assert read(shifted).totals == journal.totals
    # ... and it is the header that does it: the same shifted file without one is read at the standard positions,
    # where the G/L date is in the account's place, so nothing is found
    blind = read(as_csv(rows_of(vouchers(), header=False), shift=1))
    assert blind.vouchers == () and blind.notes[0].startswith("No header row was found")


def test_a_header_in_one_row_and_a_column_more_are_read():
    rows = rows_of(vouchers())
    for row in rows:
        if len(row) > 9:
            row.insert(8, "")  # one more spacer before the amounts
        if row and row[0] == "Number":
            row[0], row[16] = "Document Number", "Address Number"  # someone tidied the header up
    found = read(as_csv([row for row in rows if row[:1] != ["Document"]]))
    assert [(v.supplier_number, v.gross, v.balanced) for v in found.vouchers][:2] == [
        ("300417", 124.59, True), ("300652", 2520.0, True),
    ]  # fmt: skip
    assert found.totals.lines == 20 and found.vouchers[0].row == 7


def test_a_file_with_no_header_is_read_at_the_standard_positions(journal):
    found = read(as_csv(rows_of(vouchers(), header=False)))
    assert found.notes[0] == "No header row was found, so the report's standard column positions were used."

    def facts(vs):
        return [(v.supplier_number, v.supplier_name, v.gl_date, v.gross, [x.account for x in v.lines]) for v in vs]

    assert facts(found.vouchers) == facts(journal.vouchers)
    assert found.vouchers[0].row == 6 and not found.totals.differ
    assert jr.POSITIONS == {"doc_type": 0, "company": 1, "gl_date": 2, "account": 3, "description": 7, "debit": 9,
                            "credit": 11, "remark": 13, "supplier": 15, "name": 16}  # fmt: skip


def test_a_file_saved_as_windows_text_keeps_its_accents(journal):
    data = as_csv(rows_of(vouchers()), encoding="cp1252")
    assert "É".encode("cp1252") in data and not data.startswith(b"\xef\xbb\xbf")
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    assert read(data) == journal


def test_a_file_saved_with_semicolons_is_read(journal):
    data = as_csv(rows_of(vouchers()), delimiter=";")
    assert b"PV;06120;3/7/2025;" in data and b";300652;HOLLIS, PRYCE, TAN & OKAFO\r\n" in data  # no quotes needed
    assert read(data) == journal


def test_a_real_size_file_with_a_page_header_every_page():
    found, lines, pages = [], 0, 0
    for n in range(7000):
        coding = [line(f"{MAIN}.7200.{1100 + n % 40}", "20.00", "", f"supplies {n}") for _ in range(1 + n % 4)]
        items = [*coding, line(f"{MAIN}.{TRADE}", "", f"{21 * len(coding)}.00", f"supplies {n}"),
                 line(f"{MAIN}.{GST}", f"{len(coding)}.00", "", f"supplies {n}")]  # fmt: skip
        if n % 12 == 5:
            items.insert(1, PAGE)  # a new page inside the voucher
            pages += 1
        lines += len(items) - items.count(PAGE)
        found.append(voucher(f"{1 + n % 12}/{1 + n % 28}/2025", str(400000 + n % 900), f"Supplier {n % 900}", *items))
    data = as_csv(rows_of(found))
    assert data.count(b"\r\n") > 25000
    big = read(data)
    assert (big.totals.vouchers, big.totals.lines, big.totals.coding) == (7000, lines, lines - 14000)
    assert lines == 31500 and all(v.balanced for v in big.vouchers)
    assert not big.totals.differ and big.totals.debit == big.totals.credit == big.totals.file_debit == 367500.0
    assert f"Skipped: {7 * (pages + 1):,} title or page header rows and 2 total rows." in big.notes
    assert (big.totals.first_date, big.totals.last_date) == ("2025-01-01", "2025-12-28")


def test_a_journal_is_recognised():
    rows = rows_of(vouchers())
    assert jr.looks_like_journal(as_csv(rows))
    assert jr.looks_like_journal(as_csv(rows, shift=1))  # by the header's words
    assert jr.looks_like_journal(as_csv(rows_of(vouchers(), header=False)))  # by the report's number
    assert jr.looks_like_journal(as_csv(rows, delimiter=";", encoding="cp1252"))
    assert jr.looks_like_journal(as_csv(rows[5:], encoding="utf-8"))  # no title rows, no byte-order mark
    assert not jr.looks_like_journal(b"")
    assert not jr.looks_like_journal(b"Invoice Number,Supplier,Amount,Remark\r\n1001,Any Supplier,10.00,paper\r\n")
    assert not jr.looks_like_journal(b'PK\x03\x04\x14\x00\x06\x00\x08\x00\x00\x00!\x00\x81\x8d\x90\xff"\x00')


def test_the_lines_to_learn_from(journal):
    learned = jr.coding_lines(journal)
    assert [c.voucher_row for c in learned] == [8, 8, 12, 15, 22, 24, 24]  # not the affiliate's, not the odd one
    assert all(c.line.kind == jr.CODING for c in learned)
    first = learned[1]
    assert (first.supplier_number, first.supplier_name, first.gl_date) == (
        "300417", "KESTREL COURIER & FREIGHT", "2025-03-07",
    )  # fmt: skip
    assert (first.line.account, first.line.remark, first.line.amount, first.line.row) == (
        "0612038825.7410.2260", "courier Feb 4th floor", 0.26, 9,
    )  # fmt: skip
    assert [c.voucher_coding_lines for c in learned] == [2, 2, 1, 1, 1, 2, 2]
    assert [c.voucher_row for c in jr.coding_lines(journal, include_affiliates=True)] == [8, 8, 12, 15, 17, 22, 24, 24]
    assert [c.voucher_row for c in jr.coding_lines(journal, include_unbalanced=True)] == [8, 8, 12, 15, 19, 22, 24, 24]
    everything = jr.coding_lines(journal, include_affiliates=True, include_unbalanced=True)
    assert len(everything) == journal.totals.coding == 9


def test_the_objects_are_the_callers(journal):
    """Which lines are payable or tax lines is a company's own account numbers: nothing is built in."""
    data = as_csv(rows_of(vouchers()))
    none = jr.read_journal(data, payable_objects=jr.PayableObjects(trade=()), tax_objects=())
    assert (none.totals.coding, none.totals.payable, none.totals.tax) == (20, 0, 0)
    assert not any(v.affiliate or v.gross for v in none.vouchers)
    spaced = jr.PayableObjects(trade=(f" {TRADE}",), intercompany=(f"{INTERCO} ",))
    assert jr.read_journal(data, payable_objects=spaced, tax_objects={f"{GST} "}) == journal


def test_a_journal_is_plain_frozen_data(journal):
    again = json.loads(json.dumps(dataclasses.asdict(journal)))
    assert again["vouchers"][0]["lines"][1]["amount"] == 0.26 and again["totals"]["differ"] is False
    assert again["notes"] == list(journal.notes)
    for item in (journal, journal.totals, journal.vouchers[0], journal.vouchers[0].lines[0], OBJECTS,
                 jr.coding_lines(journal)[0]):  # fmt: skip
        assert not hasattr(item, "__dict__")  # slots
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(item, dataclasses.fields(item)[0].name, None)


def test_a_file_that_is_no_csv_is_refused_in_plain_words():
    with pytest.raises(ValueError, match="could not be read as a CSV file"):
        read(b'R09321,"' + b"x" * 200_000)


# --- Which objects are the payable and tax ones (suggest_objects) ----------------------------------------------------


def _made_up(size, seed=1):
    """A made-up company's journal for a year (``ap_coder.bench``): (its truth's company, the file's bytes)."""
    from ap_coder.bench import jde_company, jde_exports

    truth = jde_company.build(seed, size)
    return truth.company, jde_exports.journal_csv(truth)


def test_the_payable_and_tax_objects_are_worked_out_from_the_file():
    company, data = _made_up("small")
    objects, tax, why = jr.suggest_objects(data)
    assert objects == jr.PayableObjects((company.payable_object,), (company.intercompany_object,))
    assert tax == (company.gst_object,)
    assert why == (
        "Trade payable: object 2110 closes 103 of 119 vouchers that balance (its lines alone are the other side of "
        "the coding lines).",
        "Intercompany payable: object 2150 closes 16 of the 16 vouchers that 2110 is not on, for 2 suppliers with no "
        "voucher that 2110 closes. An affiliate's vouchers are kept but not learned from; if this is a second trade "
        "payable object, move it.",
        "Tax: object 2420 is on 71 vouchers and is 5% of the rest of the voucher on 71 of them.",
    )
    for seed in (2, 3):
        company, data = _made_up("small", seed)
        assert jr.suggest_objects(data)[:2] == (jr.PayableObjects(("2110",), ("2150",)), ("2420",))


def test_the_objects_of_a_year_of_vouchers_are_found():
    company, data = _made_up("year")
    objects, tax, why = jr.suggest_objects(data)
    assert objects == jr.PayableObjects((company.payable_object,), (company.intercompany_object,))
    assert tax == (company.gst_object,)
    assert why[0].startswith("Trade payable: object 2110 closes 4,752 of 5,467 vouchers that balance")
    assert "object 2150 closes 715 of the 715 vouchers that 2110 is not on, for 5 suppliers" in why[1]


def test_the_tax_setups_objects_are_the_tax_objects_when_the_file_has_them():
    _, data = _made_up("small")
    objects, tax, why = jr.suggest_objects(data, ["2420 ", "2425", ""])  # (2425: in the setup, on no line)
    assert objects.trade == ("2110",) and tax == ("2420",)
    assert why[-1] == "Tax: object 2420 is a GL account of the tax setup; it is on 81 lines."
    # A tax setup of another chart: none of its objects is in the file, so they are worked out from it.
    assert jr.suggest_objects(data, ["1300", "1310"])[1:] == jr.suggest_objects(data)[1:]


def test_a_guess_that_cannot_be_made_is_left_empty_and_said():
    """Seven vouchers: the trade payable object is plain; one affiliate's voucher and three tax lines are too few
    to go by."""
    objects, tax, why = jr.suggest_objects(as_csv(rows_of(vouchers())))
    assert objects == jr.PayableObjects((TRADE,)) and tax == ()
    assert why == (
        f"Trade payable: object {TRADE} closes 5 of 6 vouchers that balance (its lines alone are the other side of "
        "the coding lines).",
        f"Intercompany payable: none found among the 1 voucher that {TRADE} is not on.",
        "Tax: no object is the same small share of its vouchers time after time, so none is taken. If the journal "
        "has a tax line (GST clearing), name its object.",
    )
    only_odd = [v for v in vouchers() if v["number"] == "301355"]  # the one that does not balance
    objects, tax, why = jr.suggest_objects(as_csv(rows_of(only_odd)))
    assert objects == jr.PayableObjects(()) and tax == ()
    assert why == ("Trade payable: no object closes most of the vouchers (its lines alone on one side, every other "
                   "line on the other), so none is taken. Name the trade payable object.",)  # fmt: skip


def test_an_expense_beside_others_is_not_taken_for_tax():
    """Freight is on every voucher beside the goods, a different share each time: no tax object."""
    found = [voucher(f"3/{day}/2025", "300417", "Kestrel Courier & Freight Ltd.",
                     line(f"{MAIN}.7200.1105", f"{100 + 37 * day}.00", "", "paper"),
                     line(f"{MAIN}.7410.2260", f"{3 + (day * 7) % 11}.00", "", "freight"),
                     line(f"{MAIN}.{TRADE}", "", f"{103 + 37 * day + (day * 7) % 11}.00", "freight"))
             for day in range(1, 13)]  # fmt: skip
    objects, tax, why = jr.suggest_objects(as_csv(rows_of(found)))
    assert objects == jr.PayableObjects((TRADE,)) and tax == ()
    assert why[0].startswith(f"Trade payable: object {TRADE} closes 12 of 12 vouchers") and len(why) == 2


def test_a_header_row_without_the_remark_column_is_refused_naming_it_never_read_by_position():
    """B-2407: a header naming Account Number but not Remark was no header at all: the file was read by the report's
    positions, the supplier numbers taken for names and no remark kept. Read by position only with no header."""
    rows = rows_of(vouchers())
    at = next(i for i, row in enumerate(rows) if "             Remark" in row)
    remark = rows[at].index("             Remark")
    cut = [[cell for i, cell in enumerate(row) if i != remark] for row in rows]
    assert jr.looks_like_journal(as_csv(cut))
    with pytest.raises(ValueError, match="no “Remark” column"):
        read(as_csv(cut))
    assert read(as_csv(rows_of(vouchers(), header=False))).vouchers  # (no header row: the report's positions)


def test_a_journal_printed_day_first_reads_its_dates_that_read_two_ways_day_first():
    """25/03/2026 in the file says it prints day first: 03/04/2026 is 3 April, not 4 March."""
    found = read(as_csv(rows_of([
        voucher(date, number, "Any Supplier", line(f"{MAIN}.7200.1105", "10.00"), line(f"{MAIN}.{TRADE}", "", "10.00"))
        for date, number in (("25/03/2026", "302001"), ("03/04/2026", "302002"), ("04/04/2026", "302003"))
    ])))  # fmt: skip
    assert [v.gl_date for v in found.vouchers] == ["2026-03-25", "2026-04-03", "2026-04-04"]
    assert (found.totals.first_date, found.totals.last_date) == ("2026-03-25", "2026-04-04")


def test_a_journal_printed_month_first_still_reads_them_month_first(journal):
    assert journal.vouchers[0].gl_date == "2025-03-07"  # 3/7/2025: 11/30/2025 says the file prints month first


def _dated(*dates):
    """A journal of one voucher a G/L date, as printed."""
    return read(as_csv(rows_of([
        voucher(date, f"30200{n}", "Supplier", line(f"{MAIN}.7200.1105", "10.00"), line(f"{MAIN}.{TRADE}", "", "10"))
        for n, date in enumerate(dates)
    ])))  # fmt: skip


@pytest.mark.parametrize(("dates", "said"), [
    (("25/03/2026", "03/04/2026"), "read day first (03/04/2026: 3 April 2026), as 25/03/2026 in the file says."),
    (("03/04/2026",), "read month first (03/04/2026: 4 March 2026): no date in the file tells the order, as a day "
                      "after the 12th would."),
    (("25/03/2026", "03/25/2026", "03/04/2026"), "read month first (03/04/2026: 4 March 2026): the file has dates "
                                                 "printed both ways (25/03/2026 and 03/25/2026)."),
])  # fmt: skip
def test_the_notes_say_which_way_the_dates_that_read_two_ways_were_read(dates, said):
    """B-2543: a wrong guess (an extract with no day after the 12th, of a journal printed day first) can be seen."""
    assert f"Dates that read two ways were {said}" in _dated(*dates).notes


def test_no_note_on_the_order_when_no_date_reads_two_ways(journal):
    """B-2543: nor when the file says it prints month first, as AP Coder always read a journal: 3/7/2025 reads one
    way only beside 11/30/2025, and an ordinary journal is not given a note of it."""
    assert not any("two ways" in note for note in _dated("25/03/2026", "2026-04-03").notes)
    assert not any("two ways" in note for note in _dated("03/25/2026", "03/04/2026").notes)
    assert journal.vouchers[0].gl_date == "2025-03-07" and not any("two ways" in note for note in journal.notes)
