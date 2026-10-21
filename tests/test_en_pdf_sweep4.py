"""Reading English vendor PDFs (sweep 4): what 48 real digital invoices from Canadian and US suppliers taught (cloud
and consulting firms' invoices to a federal agency, utility bills, TV stations' advertising invoices, a university's
sample invoices). Every fixture is generated here: small PDFs of a few lines."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ap_coder.capture import analyze, build_layout, read_fields
from ap_coder.capture.normalize import parse_dates
from ap_coder.capture.types import MISSING, VERIFIED

TODAY = dt.date(2026, 10, 20)


def _pdf(tmp_path: Path, items: list[tuple], name: str = "inv.pdf", pages: int = 1) -> Path:
    """A PDF: (x, y, text), (x, y, text, font size) or (x, y, text, font size, page) in points from the top left."""
    import pymupdf

    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    for x, y, text, *more in items:
        size = more[0] if more else 10
        page = doc[more[1] - 1] if len(more) > 1 else doc[0]
        page.insert_text((x, y), text, fontsize=size)
    path = tmp_path / name
    doc.save(path)
    return path


def _read(path: Path):
    return analyze(path, ocr=False, today=TODAY).fields


# ---------------------------------------------------------------- dates


def test_a_date_run_into_the_next_label_or_printed_with_soft_hyphens_is_read():
    # "Invoice Date: 08-01-2016Contract ID: 1272709": the PDF's text has no space between the year and the next label.
    assert [d for d, *_ in parse_dates("08-01-2016Contract ID: 1272709", prefer_day_first=False)] == [
        dt.date(2016, 8, 1)
    ]
    # "10­31­2016" with soft hyphens (U+00AD) as the separators.
    assert [d for d, *_ in parse_dates("10­31­2016")] == [dt.date(2016, 10, 31)]


def test_the_invoice_date_run_into_the_next_label_is_not_lost_to_a_date_in_the_schedule(tmp_path):
    path = _pdf(tmp_path, [
        (40, 40, "Bayside Media LLC", 14), (40, 56, "PO Box 105353"), (40, 70, "Atlanta, GA 30348"),
        (40, 200, "Invoice ID: 1354259"), (160, 200, "Invoice Date: 08-01-2016Contract ID: 1272709"),
        (40, 250, "Description"), (480, 250, "Amount"), (40, 265, "Advertising Fee"), (480, 265, "$611.00"),
        (400, 300, "Total"), (480, 300, "$611.00"),
        (40, 400, "Date"), (120, 400, "Program"), (480, 400, "Cost"),
        (40, 415, "06/27/16"), (120, 415, "Morning News"), (480, 415, "45.00"),
    ])  # fmt: skip
    assert _read(path)["invoice_date"].value == "2016-08-01"


# ---------------------------------------------------------------- credit memos


def test_a_credit_memo_that_says_so_only_in_its_body_is_read_as_a_credit(tmp_path):
    # Titled "EXERCISE 9", numbered CR4597, "DO NOT PAY – THIS IS A CREDIT MEMO" across the body, amounts unsigned.
    path = _pdf(tmp_path, [
        (440, 40, "EXERCISE 9", 14), (50, 55, "Grand & Work", 14), (50, 72, "P.O. Box 5500"),
        (50, 84, "Don Mills, Ontario M3C 3L5"), (470, 100, "INVOICE NO: CR4597"), (470, 112, "DATE: July 22, 2019"),
        (50, 150, "To:"), (80, 162, "University of Toronto"),
        (60, 270, "QUANTITY"), (250, 270, "DESCRIPTION"), (450, 270, "UNIT PRICE"), (530, 270, "AMOUNT"),
        (80, 288, "1"), (120, 288, "Stats 520 laser pointer"), (480, 288, "23.99"), (550, 288, "23.99"),
        (120, 410, "DO NOT PAY - THIS IS A CREDIT MEMO", 14),
        (460, 600, "SUBTOTAL"), (550, 600, "$23.99"), (420, 622, "13% HST SALES TAX"), (558, 622, "3.12"),
        (455, 646, "TOTAL DUE"), (550, 646, "$27.11"),
    ])  # fmt: skip
    f = _read(path)
    assert f["grand_total"].value == -27.11
    assert f["subtotal"].value == -23.99
    assert f["hst_amount"].value == -3.12


# ---------------------------------------------------------------- amounts taken off the total


def test_the_amount_due_after_a_prepayment_is_the_total_and_not_verified_on_the_subtotal(tmp_path):
    # "Sub Total 611.00 | Posted Pre-Payments (519.35) | Total This Invoice 91.65", and a schedule's "Grand Total
    # 611.00" on the next page: the total is 91.65, never 611.00 verified because it equals the subtotal.
    path = _pdf(tmp_path, [
        (40, 40, "Bayside Media LLC", 14), (40, 56, "PO Box 105353"), (40, 70, "Atlanta, GA 30348"),
        (40, 200, "Invoice ID: 1354259"), (300, 200, "Invoice Date: 08/01/2016"),
        (40, 250, "Description"), (480, 250, "Amount"), (40, 270, "Gross Advertising Fee"), (480, 270, "$611.00"),
        (350, 300, "Sub Total"), (480, 300, "$611.00"), (100, 315, "Posted Pre-Payments"), (475, 315, "($519.35)"),
        (320, 345, "Total This Invoice"), (485, 345, "$91.65"),
        (40, 100, "Affidavit of Performance", 12, 2), (200, 300, "Grand Total", 10, 2), (300, 300, "24", 10, 2),
        (340, 300, "$611.00", 10, 2),
    ], pages=2)  # fmt: skip
    f = _read(path)
    assert f["grand_total"].value == 91.65
    assert f["grand_total"].status != VERIFIED  # a total net of a payment made before: the benchmark prints none


def test_an_agency_commission_is_taken_off_the_gross_the_net_is_the_total(tmp_path):
    # A TV station's invoice to an agency: "Gross Total 17,000.00 | Commission (2,550.00) | Net Total 14,450.00".
    path = _pdf(tmp_path, [
        (40, 40, "WTTE FOX 28", 14), (40, 56, "PO Box 206270"), (40, 70, "Dallas, TX 75320-6270"),
        (420, 40, "Invoice 4828683"), (420, 55, "Inv Date 11/6/2016"),
        (40, 200, "Program"), (480, 200, "Amount"), (40, 215, "FOX NFL Early Game"), (480, 215, "$17,000.00"),
        (380, 600, "Gross Total"), (480, 600, "$17,000.00"), (380, 615, "Commission"), (475, 615, "($2,550.00)"),
        (380, 630, "Net Total"), (480, 630, "$14,450.00"),
    ])  # fmt: skip
    f = _read(path)
    assert f["grand_total"].value == 14450.0
    assert f["subtotal"].value is None  # the gross is no subtotal, and the net is the total, not a subtotal


def test_a_tax_charged_on_the_net_after_the_commission_still_adds_up(tmp_path):
    # "Gross Total 35,250.00" on one page; "Agency Commission 5,287.50 | Net Amount Due 29,962.50 | HI General Excise
    # Tax 1,411.83 | Amount Due 31,374.33" on the next.
    path = _pdf(tmp_path, [
        (40, 40, "KHON", 14), (40, 56, "PO Box 844304"), (40, 70, "Dallas, TX 75284"),
        (420, 40, "Invoice # 500734-1"), (420, 55, "Invoice Date 10/26/14"),
        (300, 700, "Gross Total"), (420, 700, "$35,250.00"),
        (300, 100, "Agency Commission", 10, 2), (420, 100, "$5,287.50", 10, 2),
        (300, 115, "Net Amount Due", 10, 2), (420, 115, "$29,962.50", 10, 2),
        (260, 130, "HI General Excise Tax", 10, 2), (360, 130, "4.712%", 10, 2), (420, 130, "$1,411.83", 10, 2),
        (300, 145, "Amount Due", 10, 2), (420, 145, "$31,374.33", 10, 2),
    ], pages=2)  # fmt: skip
    f = _read(path)
    assert (f["subtotal"].value, f["tax_total"].value, f["grand_total"].value) == (29962.5, 1411.83, 31374.33)


# ---------------------------------------------------------------- the supplier's name

_CUSTOMER = [(40, 200, "CANADA BORDER SERVICES AGENCY (CLOUD)"), (40, 214, "333 NORTH RIVER RD"),
             (40, 228, "OTTAWA ON K1L 8B9")]  # fmt: skip
_AMOUNTS = [(40, 330, "Description"), (480, 330, "Amount"), (40, 345, "Consulting"), (480, 345, "1,000.00"),
            (380, 500, "Subtotal"), (480, 500, "1,000.00"), (380, 515, "HST 13%"), (480, 515, "130.00"),
            (380, 530, "Total"), (480, 530, "1,130.00")]  # fmt: skip


def test_the_name_under_a_remit_to_label_is_the_supplier_not_the_customer_above_it(tmp_path):
    path = _pdf(tmp_path, [
        (250, 50, "INVOICE", 14), (200, 80, "PLEASE REMIT TO:", 8), (200, 92, "TEKSYSTEMS CANADA CORP", 8),
        (200, 104, "PO BOX 15520", 8), (200, 116, "TORONTO ON M5W 1C1", 8), (420, 80, "Invoice No:", 8),
        (500, 80, "MAR21KAVA", 8), (420, 95, "Invoice Date:", 8), (500, 95, "2021-03-24", 8),
        (40, 186, "CANADA BORDER SERVICES AGENCY (CLOUD)", 10), (40, 200, "BTID PROCUREMENT"), *_AMOUNTS,
    ])  # fmt: skip
    assert _read(path)["vendor_name"].value == "TEKSYSTEMS CANADA CORP"


def test_a_bilingual_remit_label_is_no_name_and_a_column_label_run_onto_a_name_is_dropped(tmp_path):
    path = _pdf(tmp_path, [
        (60, 90, "Please Remit To/S'il vous plait remettre a:"), (70, 105, "Modis Canada Inc."),
        (70, 118, "20 Bay Street, Suite 1500, Toronto, ON M5J 2N8"), (330, 90, "Invoice No:"), (470, 90, "11311308"),
        (330, 105, "Invoice Date:"), (470, 105, "2020-05-03"), *_CUSTOMER, *_AMOUNTS,
    ])  # fmt: skip
    assert _read(path)["vendor_name"].value == "Modis Canada Inc."
    assert not any(str(r.value).startswith("/") for r in read_fields(build_layout(path, ocr=False))["vendor_name"])
    path = _pdf(tmp_path, [
        (130, 30, "REMIT TO Sinclair Broadcast G"), (282, 30, "Advertiser"), (340, 30, "NRA-National Rifle Assoc"),
        (130, 45, "PO Box 206270"), (130, 58, "Dallas, TX 75320-6270"), (480, 30, "Invoice 4828683"), *_AMOUNTS,
    ], name="two.pdf")  # fmt: skip
    assert _read(path)["vendor_name"].value == "Sinclair Broadcast G"


def test_the_payee_beside_a_mail_to_label_at_the_foot_is_the_supplier(tmp_path):
    path = _pdf(tmp_path, [
        (330, 45, "INVOICE NO."), (440, 45, "INVOICE DATE"), (330, 60, "EN5579496"), (440, 60, "10/30/2016"),
        (40, 140, "GREER MARGOUS MITCHELL BURN"), (40, 152, "1010 WISCONSIN AVENUE, NW"),
        (40, 164, "Washington, DC 20007"),
        (40, 250, "AGENCY"), (180, 250, "AGENCY NO."), (300, 250, "CUSTOMER"),
        (300, 600, "Net Advertising Total"), (480, 600, "$1,035.30"),
        (40, 650, "Mail to:"), (110, 650, "Comcast Spotlight"), (110, 662, "P.O. Box 415949"),
        (110, 674, "Boston, MA 02241-5949"),
    ])  # fmt: skip
    assert _read(path)["vendor_name"].value == "Comcast Spotlight"


def test_lines_that_name_no_supplier_are_not_read_as_its_name(tmp_path):
    # A letter's subject line and salutation, opening hours, a usage chart's month, a box's label printed alone: none
    # is the supplier, so the name is missing (or the right one), never one of these shown as likely.
    cases = [
        [(150, 100, "February 08, 2024"), (150, 115, "Mr. Scott Surette"), (150, 128, "Municipality of Argyle"),
         (150, 141, "PO Box 10"), (150, 154, "Tusket, NS, B0W 3M0"),
         (150, 205, "Subject: Ongoing Planning Services - Municipality of Argyle"), (150, 245, "Dear Scott,")],
        [(360, 80, "Monday to Friday 8:30 am - 4:30 pm."), (360, 92, "Closed Saturday, Sunday, and Stat. Holidays")],
        [(110, 220, "Dec"), (160, 220, "Nov"), (210, 220, "Dec")],
        [(280, 70, "Advertiser"), (340, 70, "POL/NEA Advocacy Fund")],
        [(330, 30, "A quick overview of your bill.", 16)],
    ]  # fmt: skip
    bad = ("Subject", "Mr.", "Dear", "Closed", "Monday", "Dec", "Nov", "Advertiser", "A quick")
    for i, lines in enumerate(cases):
        f = _read(_pdf(tmp_path, [*lines, (420, 40, "Invoice # 1001"), *_AMOUNTS], name=f"c{i}.pdf"))
        got = f["vendor_name"]
        assert not (got.value and str(got.value).startswith(bad) and got.status in (VERIFIED, "likely")), got


def test_a_name_ending_in_a_province_s_letters_is_still_a_name(tmp_path):
    # "KHON" (a TV station) ends in "ON", Ontario's code: it was taken for a place ("Toronto ON") and never read.
    from ap_coder.capture.reader import _not_a_name

    assert not _not_a_name("KHON")
    assert not _not_a_name("Johnson")
    assert _not_a_name("Toronto ON")
    assert _not_a_name("Tusket, NS")


def test_a_large_line_over_the_supplier_is_not_taken_for_its_letterhead(tmp_path):
    # A customer's or a project's name printed large over the supplier's letterhead: the size says nothing about whose
    # name it is (the review's probes). Two names at the top: never verified, and the supplier's is not lost to it.
    for i, big in enumerate(["Kestrel Foods", "Riverside Condominium Project", "Fall Promotion"]):
        path = _pdf(tmp_path, [
            (50, 25, big, 20), (50, 60, "Northwind Supply Ltd.", 12), (50, 72, "123 King Street West"),
            (50, 84, "Toronto, ON M5H 1A1"), (50, 96, "HST Reg. No. 123456782 RT0001"), (420, 40, "INVOICE", 18),
            (380, 90, "Invoice #:"), (470, 90, "INV-1001"), *_AMOUNTS,
        ], name=f"big{i}.pdf")  # fmt: skip
        assert _read(path)["vendor_name"].value == "Northwind Supply Ltd.", big


def test_a_line_item_with_dashes_is_not_an_envelope_s_sender_line(tmp_path):
    # "Implementation Services - CA - GC" in the line items is no "Name • Street • Town" sender line.
    path = _pdf(tmp_path, [
        (320, 40, "Amazon Web Services Canada, Inc."), (320, 52, "PO Box 4290"), (320, 64, "Toronto, Ont M5W 0E1"),
        (420, 120, "Invoice Number:"), (520, 120, "10002100"),
        (60, 245, "Description"), (480, 245, "Amount"), (90, 260, "Implementation Services - CA - GC"),
        (480, 260, "1,000.00"), *_AMOUNTS[4:],
    ])  # fmt: skip
    assert _read(path)["vendor_name"].value == "Amazon Web Services Canada, Inc."
    assert "Implementation Services" not in [r.value for r in read_fields(build_layout(path, ocr=False))["vendor_name"]]


# ---------------------------------------------------------------- the HST and the GST/HST number

_SUPPLIER = [(40, 40, "Distill Mobile Inc.", 14), (40, 56, "561 Golden Avenue"), (40, 68, "Ottawa ON K2A 2E4"),
             (400, 40, "Invoice Number"), (500, 40, "INV-799"), (400, 55, "Invoice Date"),
             (500, 55, "2022-04-30")]  # fmt: skip


def test_total_hst_with_its_rate_and_a_bilingual_total_hst_label_are_the_hst(tmp_path):
    path = _pdf(tmp_path, [
        *_SUPPLIER, (40, 330, "Description"), (480, 330, "Amount"), (40, 345, "Consulting - June"),
        (480, 345, "279,400.00"),
        (380, 500, "Subtotal"), (480, 500, "279,400.00"), (380, 515, "TOTAL  HST 13%"), (480, 515, "36,322.00"),
        (380, 530, "TOTAL CAD"), (480, 530, "315,722.00"),
    ])  # fmt: skip
    f = _read(path)
    assert (f["hst_amount"].value, f["tax_total"].value) == (36322.0, 36322.0)
    path = _pdf(tmp_path, [
        *_SUPPLIER, (40, 330, "Description"), (480, 330, "Amount"), (40, 345, "Services"), (480, 345, "23,889.31"),
        (130, 500, "SUBTOTAL/Totale:"), (480, 500, "23,889.31"), (130, 515, "Total HST/Montant TVH Total"),
        (290, 515, "871072369RT:"), (480, 515, "3,105.60"), (130, 530, "TOTAL AMOUNT DUE/Montant Total Du :"),
        (480, 530, "26,994.91"),
    ], name="two.pdf")  # fmt: skip
    assert _read(path)["hst_amount"].value == 3105.6


def test_a_tax_code_column_does_not_crowd_out_the_hst_in_the_totals(tmp_path):
    # "Services | 20 | 1,792.20 | HST ON | 35,844.00" on every row: the tax's code in its column, the line's amount.
    rows = [(40, 300 + 15 * i, "Services against task authorization") for i in range(9)]
    rows += [(300, 300 + 15 * i, "10") for i in range(9)] + [(340, 300 + 15 * i, "100.00") for i in range(9)]
    rows += [(420, 300 + 15 * i, "HST ON") for i in range(9)] + [(480, 300 + 15 * i, "1,000.00") for i in range(9)]
    path = _pdf(tmp_path, [
        *_SUPPLIER, (40, 285, "ACTIVITY"), (300, 285, "QTY"), (340, 285, "RATE"), (420, 285, "TAX"),
        (480, 285, "AMOUNT"), *rows,
        (330, 500, "SUBTOTAL"), (480, 500, "9,000.00"), (330, 515, "HST (ON) @ 13%"), (480, 515, "1,170.00"),
        (330, 530, "TOTAL"), (480, 530, "10,170.00"),
        (40, 580, "TAX SUMMARY"), (200, 600, "RATE"), (330, 600, "TAX"), (420, 600, "NET"),
        (110, 615, "HST (ON) @ 13%"), (320, 615, "1,170.00"), (410, 615, "9,000.00"),
    ])  # fmt: skip
    assert _read(path)["hst_amount"].value == 1170.0


def test_a_tax_printed_as_tax_13_percent_is_the_hst_the_page_names(tmp_path):
    path = _pdf(tmp_path, [
        *_SUPPLIER, (40, 330, "Description"), (480, 330, "Amount"), (40, 345, "Consulting"), (480, 345, "$200,000.00"),
        (380, 500, "Subtotal"), (480, 500, "$200,000.00"), (380, 515, "Tax (13%)"), (480, 515, "$26,000.00"),
        (380, 530, "Amount Due"), (480, 530, "$226,000.00"), (40, 600, "Notes"), (40, 615, "HST Number: 835582081"),
    ])  # fmt: skip
    f = _read(path)
    assert f["hst_amount"].value == 26000.0
    assert f["hst_amount"].status != VERIFIED
    # A US invoice's "Tax (8%)" names no Canadian tax: no HST.
    path = _pdf(tmp_path, [
        (40, 40, "Pacific Supply LLC", 14), (40, 56, "14368 Pipeline Ave"), (40, 68, "Chino, CA 91710"),
        (400, 40, "Invoice # 1001"), (380, 500, "Subtotal"), (480, 500, "$1,000.00"), (380, 515, "Tax (8%)"),
        (480, 515, "$80.00"), (380, 530, "Amount Due"), (480, 530, "$1,080.00"),
    ], name="us.pdf")  # fmt: skip
    f = _read(path)
    assert f["hst_amount"].status == MISSING and f["gst_amount"].status == MISSING


def test_a_business_number_after_its_gst_hst_label_is_read_alone(tmp_path):
    for i, lines in enumerate([
        [(40, 80, "GST/HST Registration No.:"), (40, 93, "121628366")],
        [(40, 80, "HST (ON) Registration No.: 713892313")],
        [(40, 80, "HST # 835582081")],
    ]):  # fmt: skip
        f = _read(_pdf(tmp_path, [(40, 40, "GC Strategies Inc.", 14), *lines, (400, 40, "Invoice # 2101"), *_AMOUNTS],
                       name=f"bn{i}.pdf"))  # fmt: skip
        got = f["gst_hst_registration_number"]
        assert got.value in ("121628366", "713892313", "835582081"), got
        assert got.status != VERIFIED  # the benchmark prints the whole number: a person checks or it is likely
    # The customer's number is not the supplier's.
    f = _read(_pdf(tmp_path, [(40, 40, "GC Strategies Inc.", 14), (40, 80, "Client GST/HST No.: 121628366"),
                              (400, 40, "Invoice # 2101"), *_AMOUNTS], name="client.pdf"))  # fmt: skip
    assert f["gst_hst_registration_number"].status == MISSING


# ---------------------------------------------------------------- currency


def test_dollars_from_a_us_supplier_are_us_dollars_never_more_than_checked(tmp_path):
    us = [(40, 40, "Bayside Media LLC", 14), (40, 56, "PO Box 105353"), (40, 70, "Atlanta, GA 30348"),
          (400, 40, "Invoice # 1001"), (380, 500, "Subtotal"), (480, 500, "$1,000.00"), (380, 530, "Total"),
          (480, 530, "$1,000.00")]  # fmt: skip
    capture = analyze(_pdf(tmp_path, us), ocr=False, today=TODAY)
    f = capture.fields
    assert f["currency"].value == "USD" and f["currency"].status not in (VERIFIED, "likely")
    from ap_coder.capture.bridge import review_issues

    # Not printed: the review still says the currency was assumed.
    assert "CURRENCY_NOT_FOUND" in [code for _, code, _ in review_issues(capture)]
    # A US supplier billing a Canadian customer named under "Bill To": still US dollars.
    customer = [(40, 150, "Bill To:"), (40, 163, "Northshore Ltd."), (40, 176, "Halifax, NS B3H 1A1")]
    f = _read(_pdf(tmp_path, [*us, *customer], name="ca_customer.pdf"))
    assert f["currency"].value == "USD"
    # A Canadian supplier's "$" (its postal code, its GST/HST) is not US dollars.
    f = _read(_pdf(tmp_path, [(40, 40, "Northshore Ltd.", 14), (40, 56, "45 Harbour Rd, Halifax, NS B3H 1A1"),
                              (40, 150, "Ship To:"), (40, 163, "Bayside Media LLC"), (40, 176, "Atlanta, GA 30348"),
                              *us[3:]], name="ca.pdf"))  # fmt: skip
    assert f["currency"].value != "USD"


# ---------------------------------------------------------------- PO numbers, totals' rows, dates


def test_a_po_run_into_the_next_word_after_a_slash_is_cut_there(tmp_path):
    path = _pdf(tmp_path, [*_SUPPLIER, (440, 120, "Customer PO #"), (440, 133, "PO#474192803B/Contract"), *_AMOUNTS])
    assert _read(path)["po_number"].value == "474192803B"


def test_a_subtotal_row_with_the_hours_first_is_read_from_its_amount_column(tmp_path):
    path = _pdf(tmp_path, [
        *_SUPPLIER, (40, 300, "Contractor"), (400, 300, "Qty"), (500, 300, "Total"), (40, 315, "Consultant"),
        (400, 315, "23.00"), (500, 315, "23,115.00"), (310, 400, "SUBTOTAL:"), (390, 400, "23.00"),
        (490, 400, "23,115.00"), (310, 415, "HST 13%"), (495, 415, "3,004.95"), (310, 430, "TOTAL"),
        (490, 430, "26,119.95"),
    ])  # fmt: skip
    assert _read(path)["subtotal"].value == 23115.0


def test_an_exchange_rate_or_a_date_s_year_on_the_total_s_row_is_no_amount(tmp_path):
    path = _pdf(tmp_path, [
        (40, 40, "Amazon Web Services Canada, Inc.", 12), (240, 130, "AMOUNT DUE ON"), (355, 130, "April 1, 2022"),
        (540, 130, "CAD 4,000,000"), (30, 300, "Total Amount in CAD (1 USD = 1.235 CAD)"), (540, 300, "CAD 4,000,000"),
    ])  # fmt: skip
    assert _read(path)["grand_total"].value == 4000000.0


def test_a_letter_s_date_alone_under_the_letterhead_is_the_invoice_date_checked(tmp_path):
    path = _pdf(tmp_path, [
        (40, 40, "BDO Canada LLP", 14), (40, 56, "180 Kent Street"), (40, 68, "Ottawa, ON K1P 0B6"),
        (40, 100, "June 30, 2021"), (40, 130, "Canada Border Services Agency"), (40, 160, "Invoice CINV1271478"),
        *_AMOUNTS,
    ])  # fmt: skip
    f = _read(path)
    assert f["invoice_date"].value == "2021-06-30" and f["invoice_date"].status not in (VERIFIED, "likely")


def test_a_date_in_another_sentence_over_a_due_date_label_is_not_the_due_date(tmp_path):
    # A utility bill: "Mailed 11/14/22 for Account # 0000" over the box "AMOUNT DUE | DUE DATE", "$117.89 | 12/12/22".
    path = _pdf(tmp_path, [
        (160, 40, "Mailed 11/14/22 for Account # 0000-0000-0000", 6), (160, 48, "AMOUNT DUE", 6),
        (220, 48, "DUE DATE", 6),
        (160, 56, "$117.89", 9), (220, 56, "12/12/22", 9), (330, 40, "Old Dominion Power", 14), *_AMOUNTS,
    ])  # fmt: skip
    assert _read(path)["due_date"].value != "2022-11-14"


def test_a_page_whose_text_runs_up_the_page_is_read_turned(tmp_path):
    # Microsoft's landscape invoices are set on portrait pages with their text drawn sideways: almost nothing was read.
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    lines = [(40, "Microsoft Canada Inc., 1950 Meadowvale Blvd., Mississauga, ON, L5N8L9"),
             (80, "Document No: 9988434410"), (100, "Document Date: 06-AUG-2021"),
             (120, "Bill to PO Number: 2B3035175"), (400, "Total Sale  540,600.00"),
             (420, "GST/HST (13.000%)  70,278.00"), (440, "Total Amount  610,878.00")]  # fmt: skip
    for x, text in lines:
        # Text running up the page: read upright once the page is turned a quarter turn clockwise.
        page.insert_text((60 + x * 1.1, 700), text, fontsize=9, rotate=90)
    path = tmp_path / "turned.pdf"
    doc.save(path)
    layout = build_layout(path, ocr=False)
    assert layout.pages[0].turn == 90
    f = analyze(path, ocr=False, today=TODAY, layout=layout).fields
    assert f["invoice_number"].value == "9988434410"
    assert f["invoice_date"].value == "2021-08-06"
    assert f["po_number"].value == "2B3035175"
    assert (f["subtotal"].value, f["hst_amount"].value, f["grand_total"].value) == (540600.0, 70278.0, 610878.0)


def test_a_gst_hst_amount_with_no_subtotal_to_tell_its_rate_is_not_verified_as_either_tax(tmp_path):
    path = _pdf(tmp_path, [
        *_SUPPLIER, (380, 515, "GST/HST"), (480, 515, "16,874.00"), (380, 530, "QST/PST"), (480, 530, "0.00"),
        (380, 545, "Total Amount"), (480, 545, "146,674.00"),
    ])  # fmt: skip
    f = _read(path)
    for field in ("gst_amount", "hst_amount", "qst_amount", "pst_amount"):
        assert f[field].status != VERIFIED, field


def test_remit_to_bank_is_no_name_the_account_s_name_under_it_is(tmp_path):
    path = _pdf(tmp_path, [
        (20, 85, "REMIT TO BANK"), (20, 97, "A/C Name: MICROSOFT CANADA INC"), (20, 109, "Bank Name: CITIBANK NA"),
        (420, 40, "Invoice", 18), (400, 85, "Document No:"), (480, 85, "9988434410"), *_AMOUNTS,
    ])  # fmt: skip
    assert _read(path)["vendor_name"].value == "MICROSOFT CANADA INC"


def test_a_deposit_under_a_printed_total_leaves_the_total_as_printed(tmp_path):
    # "Subtotal 2,344.90 | Total 2,344.90 | Deposit received (586.23) | Balance due 1,758.67": the invoice's total is
    # printed; the balance after the deposit is not read as it.
    path = _pdf(tmp_path, [
        *_SUPPLIER, (40, 330, "Description"), (480, 330, "Amount"), (40, 345, "Furniture"), (480, 345, "2,344.90"),
        (380, 500, "Subtotal"), (480, 500, "2,344.90"), (380, 515, "Total"), (480, 515, "2,344.90"),
        (380, 530, "Deposit received"), (480, 530, "(586.23)"), (380, 545, "Balance due"), (480, 545, "1,758.67"),
    ])  # fmt: skip
    assert _read(path)["grand_total"].value == 2344.9


def test_a_whole_business_number_misread_is_not_read_as_its_nine_digits(tmp_path):
    from ap_coder.capture.reader import _bare_business_numbers

    path = _pdf(
        tmp_path, [(40, 40, "GC Strategies Inc.", 14), (40, 80, "GST/HST Reg. No. 232672980-RT-O001"), *_AMOUNTS]
    )
    assert _bare_business_numbers(build_layout(path, ocr=False)) == []


# ---------------------------------------------------------------- review of this sweep: ordinary invoices stay right

_NORTHWIND = [(50, 60, "Northwind Supply Ltd.", 16), (50, 80, "123 King Street West"), (50, 93, "Toronto, ON M5H 1A1"),
              (50, 106, "HST Reg. No. 123456782 RT0001"), (430, 60, "INVOICE", 22), (380, 95, "Invoice #:"),
              (470, 95, "INV-3001"), (380, 109, "Invoice Date:"), (470, 109, "2026-10-01"), (50, 160, "Bill To:"),
              (50, 173, "Acme Corp"), (50, 186, "45 Queen Street"), (50, 199, "Ottawa, ON K1P 1J9"),
              (50, 240, "Description"), (500, 240, "Amount"), (50, 258, "Consulting services"),
              (500, 258, "550.00")]  # fmt: skip


def _totals(tmp_path, rows: list, name: str = "inv.pdf", head: list | None = None, extra: list | None = None):
    items = list(head if head is not None else _NORTHWIND)
    for i, row in enumerate(rows):
        items += (
            [(330, 300 + 15 * i, row[0]), (500, 300 + 15 * i, row[1])]
            if isinstance(row, tuple)
            else [(330, 300 + 15 * i, row)]
        )
    return _read(_pdf(tmp_path, items + list(extra or []), name=name))


_PLAIN = [("Subtotal", "550.00"), ("HST 13%", "71.50"), ("Total Due", "621.50")]


def test_a_sentence_about_credit_memos_does_not_make_an_invoice_a_credit(tmp_path):
    for i, note in enumerate([
        "If this document is a credit memo, the amount will be applied to your account.",
        "Where this is a credit note, no payment is required.",
        "Unless this is a credit memo, payment is due within 30 days.",
        "If this is a credit memo, do not pay.",
        "Note: this is a credit memo only if marked CREDIT above.",
    ]):  # fmt: skip
        f = _totals(tmp_path, _PLAIN, name=f"n{i}.pdf", extra=[(50, 700, note, 8)])
        assert f["grand_total"].value == 621.5, note
    # The statement on a line of its own is a credit, but only likely: the title does not say it.
    f = _totals(tmp_path, _PLAIN, name="stated.pdf", extra=[(50, 700, "THIS IS A CREDIT MEMO", 8)])
    assert f["grand_total"].value == -621.5 and f["grand_total"].status != VERIFIED


def test_an_unlabelled_customer_block_lower_down_is_not_the_supplier(tmp_path):
    # The supplier's second line is "Suite 200, 123 King Street West"; the customer's block (a window envelope's,
    # no "Bill To") is over its own street address lower down: the supplier stays the one at the top.
    for i, second in enumerate(["Suite 200, 123 King Street West", "Unit 4 - 88 Industrial Rd"]):
        for j, street in enumerate(["45 Queen Street", "PO Box 455"]):
            path = _pdf(tmp_path, [
                (50, 50, "Northwind Supply Ltd.", 12), (50, 65, second, 9), (50, 76, "Toronto, ON M5H 1A1", 9),
                (50, 87, "HST Reg. No. 123456782 RT0001", 9), (50, 110, "Acme Industries Inc."), (50, 123, street),
                (50, 136, "Ottawa, ON K1P 1J9"), (380, 110, "Invoice #:"), (470, 110, "INV-1001"),
                (50, 220, "Description"), (500, 220, "Amount"), (50, 238, "Consulting"), (500, 238, "550.00"),
                (380, 280, "Subtotal"), (500, 280, "550.00"), (380, 295, "HST 13%"), (500, 295, "71.50"),
                (380, 310, "Total"), (500, 310, "621.50"),
            ], name=f"w{i}{j}.pdf")  # fmt: skip
            assert _read(path)["vendor_name"].value == "Northwind Supply Ltd.", (second, street)


def test_sideways_text_in_the_margin_does_not_turn_an_upright_page(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text, *size in _NORTHWIND + [
        (330, 300, "Subtotal"),
        (500, 300, "550.00"),
        (330, 315, "HST 13%"),
        (500, 315, "71.50"),
        (330, 330, "Total Due"),
        (500, 330, "621.50"),
    ]:
        page.insert_text((x, y), text, fontsize=size[0] if size else 10)
    for i in range(4):  # the terms in small print up the right margin
        page.insert_text((580 + 7 * i, 760), "Terms and conditions: goods remain the property of Northwind Supply " * 2,
                         fontsize=5.5, rotate=90)  # fmt: skip
    path = tmp_path / "margin.pdf"
    doc.save(path)
    layout = build_layout(path, ocr=False)
    assert layout.pages[0].turn == 0
    f = analyze(path, ocr=False, today=TODAY, layout=layout).fields
    assert f["vendor_name"].value == "Northwind Supply Ltd." and f["grand_total"].value == 621.5


def test_total_sale_under_the_tax_is_the_total_and_the_hst_is_kept(tmp_path):
    for i, rows in enumerate([
        [("HST 13%", "71.50"), ("Total Sale", "621.50"), ("Balance Due", "621.50")],
        [("Net Amount", "550.00"), ("HST 13%", "71.50"), ("Amount Due", "621.50")],
    ]):  # fmt: skip
        f = _totals(tmp_path, rows, name=f"k{i}.pdf")
        assert f["hst_amount"].value == 71.5, rows
        assert not (f["subtotal"].value == 621.5 and f["subtotal"].status == VERIFIED), rows
    f = _totals(tmp_path, [("Total Sales (incl. HST)", "621.50"), ("HST 13% included", "71.50")], name="incl.pdf")
    assert f["subtotal"].value != 621.5


def test_arithmetic_on_a_totals_line_is_not_an_exchange_rate(tmp_path):
    f = _totals(tmp_path, ["Subtotal 550.00", "HST 13% of 550.00 = 71.50", "Total 621.50"], name="a.pdf")
    assert f["hst_amount"].value == 71.5
    f = _totals(tmp_path, [("Subtotal", "550.00"), ("HST 13%", "71.50"), "Total: 10 x 62.15 = 621.50"], name="b.pdf")
    assert f["grand_total"].value in (621.5, None)
    assert f["grand_total"].value != 10.0


def test_total_hst_incl_is_the_invoice_total(tmp_path):
    f = _totals(tmp_path, [("Subtotal", "550.00"), ("HST 13%", "71.50"), ("TOTAL HST INCL.", "621.50")])
    assert (f["grand_total"].value, f["hst_amount"].value) == (621.5, 71.5)


def test_the_supplier_s_name_with_an_amount_due_label_across_its_row_is_still_read(tmp_path):
    head = [x for x in _NORTHWIND if x[2] != "INVOICE"] + [(380, 60, "Amount Due:", 11), (500, 60, "$621.50", 11)]
    f = _totals(tmp_path, _PLAIN, head=head)
    assert f["vendor_name"].value == "Northwind Supply Ltd."


def test_a_canadian_supplier_banking_in_the_us_is_not_billing_in_us_dollars(tmp_path):
    head = [(50, 60, "Northwind Supply Ltd.", 16), (50, 80, "123 King Street West"), (50, 93, "Toronto"),
            (380, 95, "Invoice #:"), (470, 95, "INV-3001"), (50, 240, "Description"), (500, 240, "Amount")]  # fmt: skip
    f = _totals(tmp_path, [("Subtotal", "$550.00"), ("Total Due", "$550.00")], head=head,
                extra=[(50, 700, "Remit to: Lockbox 774, Chicago, IL 60693", 8)])  # fmt: skip
    assert f["currency"].value != "USD"
    us = [(50, 60, "Pacific Supply LLC", 16), (50, 80, "14368 Pipeline Ave"), (50, 93, "Chino, CA 91710"),
          (380, 95, "Invoice #:"), (470, 95, "INV-3001")]  # fmt: skip
    f = _totals(tmp_path, [("Subtotal", "CA$550.00"), ("Total Due", "CA$550.00")], head=us, name="ca.pdf")
    assert f["currency"].value != "USD"


def test_an_excise_tax_in_a_fuel_price_is_no_sales_tax(tmp_path):
    f = _totals(tmp_path, [("Fuel", "510.00"), ("Excise Tax", "40.00"), ("GST 5%", "27.50"), ("Total", "577.50")])
    assert f["tax_total"].value in (None, 27.5)


def test_another_party_s_business_number_is_not_the_supplier_s(tmp_path):
    head = [x for x in _NORTHWIND if not x[2].startswith("HST Reg")]
    for i, who in enumerate(["Purchaser's", "Exempt purchaser", "Carrier", "Subcontractor"]):
        f = _totals(tmp_path, _PLAIN, head=head, extra=[(50, 700, f"{who} HST # 876543216", 8)], name=f"b{i}.pdf")
        assert f["gst_hst_registration_number"].status == MISSING, who
    f = _totals(tmp_path, _PLAIN, head=head, extra=[(50, 700, "HST # 123456782", 8)], name="ours.pdf")
    assert f["gst_hst_registration_number"].value == "123456782"
    assert f["gst_hst_registration_number"].status == "check"  # the check digit alone confirms nothing (D-47)


def test_a_deposit_on_a_taxed_invoice_leaves_the_total_for_a_person_to_check(tmp_path):
    f = _totals(tmp_path, [("Subtotal", "550.00"), ("HST 13%", "71.50"), ("Deposit received", "(200.00)"),
                           ("Balance Due", "421.50")])  # fmt: skip
    assert f["grand_total"].status not in (VERIFIED, "likely")
    assert f["other_charges"].status not in (VERIFIED, "likely")


def test_an_invoice_number_ending_in_a_word_after_a_slash_is_kept_whole(tmp_path):
    head = [x for x in _NORTHWIND if x[2] != "INV-3001"] + [(470, 95, "1234/Revised")]
    assert _totals(tmp_path, _PLAIN, head=head)["invoice_number"].value == "1234/Revised"


def test_the_net_after_an_agency_commission_is_checked(tmp_path):
    f = _totals(tmp_path, [("Gross Total", "$1,400.00"), ("Agency Commission", "($210.00)"),
                           ("Net Amount Due", "$1,190.00")])  # fmt: skip
    assert f["grand_total"].value == 1190.0 and f["grand_total"].status == "check"


def test_a_customer_block_level_with_a_right_aligned_supplier_is_not_the_supplier(tmp_path):
    # The supplier right-aligned at the top right, the customer's block at the top left at about the same height
    # (the review's sweep of layouts): the customer won by its "name over a street address" bonus (B-419).
    import pymupdf

    for i, (vy, cy) in enumerate([(50, 60), (60, 60), (50, 50)]):
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        for text, y, size in [("Northwind Supply Ltd.", vy, 11), ("123 King Street West", vy + 13, 9),
                              ("Toronto, ON M5H 1A1", vy + 24, 9)]:  # fmt: skip
            w = pymupdf.get_text_length(text, fontname="hebo" if size == 11 else "helv", fontsize=size)
            page.insert_text((560 - w, y), text, fontsize=size, fontname="hebo" if size == 11 else "helv")
        for x, y, text in [(50, cy, "Acme Industries Inc."), (50, cy + 13, "45 Queen Street"),
                           (50, cy + 26, "Ottawa, ON K1P 1J9"), (380, 130, "Invoice #:"), (470, 130, "INV-1001"),
                           (50, 220, "Description"), (500, 220, "Amount"), (50, 238, "Consulting"),
                           (500, 238, "550.00"),
                           (380, 280, "Subtotal"), (500, 280, "550.00"), (380, 295, "HST 13%"), (500, 295, "71.50"),
                           (380, 310, "Total"), (500, 310, "621.50")]:  # fmt: skip
            page.insert_text((x, y), text, fontsize=10)
        path = tmp_path / f"tr{i}.pdf"
        doc.save(path)
        got = _read(path)["vendor_name"]
        assert not (got.value == "Acme Industries Inc." and got.status in (VERIFIED, "likely")), (vy, cy, got)
