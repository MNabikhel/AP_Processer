"""Reading terms, PO numbers and due dates on real invoices (sweep 4). What 135 public sample invoices taught about how
suppliers print their payment terms, the customer's order number and the due date. Every fixture is generated here:
small PDFs of a few lines, or a terms text as a label's value."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from ap_coder.capture import analyze
from ap_coder.capture.reader import _terms
from ap_coder.capture.types import VERIFIED
from ap_coder.coding.terms import Terms, parse_terms

TODAY = dt.date(2026, 10, 20)


def _pdf(tmp_path: Path, items: list[tuple], name: str = "inv.pdf") -> Path:
    """A one-page PDF: (x, y, text) or (x, y, text, font size) in points from the top left."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text, *size in items:
        page.insert_text((x, y), text, fontsize=size[0] if size else 10)
    path = tmp_path / name
    doc.save(path)
    return path


def _read(path: Path):
    return analyze(path, ocr=False, today=TODAY).fields


_HEAD = [
    (60, 60, "Northshore Industrial Supply Ltd.", 16), (60, 80, "45 Harbour Rd, Halifax, NS B3H 1A1"),
    (60, 95, "GST/HST No. 123456782 RT0001"), (380, 60, "INVOICE", 14),
    (380, 85, "Invoice No."), (470, 85, "NS-20418"), (380, 100, "Invoice Date"), (470, 100, "2026-09-15"),
]  # fmt: skip
_TABLE = [
    (60, 330, "Description"), (500, 330, "Amount"), (60, 345, "Hydraulic fittings"), (500, 345, "1,000.00"),
    (380, 500, "Subtotal"), (500, 500, "1,000.00"), (380, 515, "HST 15%"), (500, 515, "150.00"),
    (380, 530, "Invoice Total"), (500, 530, "1,150.00"),
]  # fmt: skip


# ---------------------------------------------------------------- terms as the app reads them


@pytest.mark.parametrize(
    "printed, read",
    [
        # The app counts these from the end of the month, a day of the next month, calendar months or weeks: the
        # reader's short form kept only the number ("Net 30", "Net 10", "Net 1", "Net 2"), so the due date was wrong.
        ("Net 30 EOM", "Net 30 EOM"),
        ("Net 30 days end of month", "Net 30 EOM"),
        ("30 jours fin de mois", "Net 30 EOM"),
        ("Net 10th prox", "Net 10 prox"),
        ("Net 1 month", "Net 1 month"),
        ("Net 2 weeks", "Net 14"),
        # A discount's days are not the net days.
        ("2% 10 days", "2% 10 days"),
        ("2% discount if paid within 10 days", "2% 10 days"),
        ("Net 45, 1% 15 days", "1% 15 Net 45"),
        ("2/10 EOM", "2% 10 Net 30 EOM"),
        # As before.
        ("Net 30", "Net 30"),
        ("NET 30", "Net 30"),
        ("Net 30 days", "Net 30"),
        ("30 days", "Net 30"),
        ("2% 10, Net 30", "2% 10 Net 30"),
        ("Net 60 jours", "Net 60"),
        ("30 jours net", "Net 30"),
        ("solde à 30 j", "Net 30"),
        ("15jourssuivant la datedelafacture", "Net 15"),  # OCR's words run together
        ("Due on receipt", "Due on receipt"),
        ("Upon receipt", "Due on receipt"),
        ("Payable sur réception", "Due on receipt"),
        ("Net 30 - 1.5% per month on overdue accounts", "Net 30"),
        ("Visa", None),
    ],
)
def test_terms_are_read_in_the_form_the_app_counts_the_due_date_from(printed, read):
    assert _terms(printed) == read
    if read and parse_terms(printed) != Terms():  # the app reads the short form as it reads the printed terms
        assert parse_terms(read) == parse_terms(printed)


def test_eom_terms_on_an_invoice_are_kept_and_checked_against_the_due_date(tmp_path):
    # "Net 30 EOM" on an invoice of Sep 15 is due Oct 30 (30 days after Sep 30): read as "Net 30", the due date
    # printed failed the reader's own check (Sep 15 + 30 days is Oct 15), and the app counted the terms from Sep 15.
    lines = [(380, 115, "Terms"), (470, 115, "Net 30 EOM"), (380, 130, "Due Date"), (470, 130, "2026-10-30")]
    got = _read(_pdf(tmp_path, _HEAD + lines + _TABLE))
    assert got["payment_terms"].value == "Net 30 EOM"
    assert got["due_date"].value == "2026-10-30"
    assert not any("≠ due date" in r for r in got["due_date"].reasons)


# ---------------------------------------------------------------- terms with no English label


@pytest.mark.parametrize(
    "lines, terms",
    [
        # Spanish, Italian (the value under its label in a row of labels), German, Dutch.
        ([(60, 140, "CONDICIÓN DE PAGO: 30 DÍAS FECHA FACTURA")], "Net 30"),
        ([(300, 140, "Termini di Pagamento"), (300, 155, "60 GG DF")], "Net 60"),
        ([(60, 140, "Zahlungsbedingungen"), (200, 140, "14 Tage netto")], "Net 14"),
        ([(60, 140, "Betalingsvoorwaarden: 30 dagen")], "Net 30"),
        # Printed with no label at all: a sentence in the footer.
        (
            [(60, 600, "15 days from date of invoice. Late payment fees at 18% per annum will be charged after it.")],
            "Net 15",
        ),
        (
            [(60, 600, "15 jours suivant la date de la facture. Des frais de 18% par an s'appliquent ensuite.")],
            "Net 15",
        ),
        ([(60, 600, "Payment is due within 45 days of the invoice date.")], "Net 45"),
        ([(60, 600, "Please remit payment upon receipt.")], "Due on receipt"),
        ([(60, 600, "Zahlbar innerhalb 30 Tagen netto, 2% Skonto innerhalb 10 Tagen.")], "2% 10 Net 30"),
        ([(60, 600, "Bei Zahlung innerhalb 14 Tagen gewähren wir 2,0% Skonto.")], "2% 14 days"),
        ([(60, 600, "Die Rechnung ist sofort fällig.")], "Due on receipt"),
    ],
)
def test_terms_printed_in_other_words_are_read_for_a_person_to_check(tmp_path, lines, terms):
    got = _read(_pdf(tmp_path, _HEAD + lines + _TABLE))
    assert got["payment_terms"].value == terms
    assert got["payment_terms"].status != VERIFIED  # wording the benchmark never prints: a person checks it


@pytest.mark.parametrize(
    "note",
    [
        # Days in a sentence that is not about when to pay.
        "Invoice discrepancies must be communicated within 90 days of the invoice date.",
        "Returns are accepted within 30 days of the invoice date with the receipt.",
        "Overdue accounts are charged 2% interest per month after 30 days.",
        "Einwände sind schriftlich innerhalb 4 Wochen ab Rechnungsdatum mitzuteilen.",
    ],
)
def test_days_in_a_note_that_is_not_the_terms_are_not_read_as_terms(tmp_path, note):
    got = _read(_pdf(tmp_path, [*_HEAD, (60, 600, note), *_TABLE]))
    assert got["payment_terms"].value is None


# ---------------------------------------------------------------- the customer's order number


@pytest.mark.parametrize(
    "lines, po",
    [
        # European invoices give the customer's order number as "your reference", and German ones as the order.
        (
            [(60, 140, "Your Reference"), (200, 140, "COMPRA0832"), (60, 155, "Agreement Ref."), (200, 155, "FR2017")],
            "COMPRA0832",
        ),
        ([(60, 140, "Votre référence"), (200, 140, "PO445")], "PO445"),
        ([(60, 140, "Bestellung:"), (200, 140, "B123456789")], "B123456789"),
        (
            [
                (60, 140, "Bestell-Nr"),
                (160, 140, ": B123456789"),
                (60, 152, "Bestell-Datum"),
                (160, 152, ": 01.08.2026"),
            ],
            "B123456789",
        ),
        # The label in the middle of a line, after the customer's name or in a sentence.
        ([(60, 140, "York University Purchase Order Number: PO12345")], "PO12345"),
        ([(60, 140, "Rechnung gemäß Bestellung Nr. 2026-471331 vom 01.09.2026.")], "2026-471331"),
    ],
)
def test_the_customers_order_number_under_other_labels_is_read_for_a_person_to_check(tmp_path, lines, po):
    got = _read(_pdf(tmp_path, _HEAD + lines + _TABLE))
    assert got["po_number"].value == po
    assert got["po_number"].status != VERIFIED


@pytest.mark.parametrize(
    "lines",
    [
        [(60, 140, "Customer#: 10045  Contract/Purchase Order Number:___________")],
        [(60, 140, "Your reference: John Smith")],
        [(60, 140, "Rechnung gemäß Bestellung vom 01.09.2026.")],
        # Two orders named in the lines' details: neither is the invoice's.
        [(60, 140, "* Bestellung Nr. ORDER84359 Zeile 1"), (60, 155, "* Bestellung Nr. ORDER84753 Zeile 7")],
    ],
)
def test_no_order_number_is_made_up_from_a_label_without_one(tmp_path, lines):
    assert _read(_pdf(tmp_path, _HEAD + lines + _TABLE))["po_number"].value is None


def test_a_po_label_wins_over_your_reference(tmp_path):
    lines = [(60, 140, "Your Reference"), (200, 140, "J-SMITH-7"), (60, 155, "PO Number"), (200, 155, "4500012345")]
    got = _read(_pdf(tmp_path, _HEAD + lines + _TABLE))["po_number"]
    assert got.value == "4500012345"
    assert not any("J-SMITH-7" in r for r in got.reasons)  # not even a second reading to cast doubt on it


# ---------------------------------------------------------------- due dates


@pytest.mark.parametrize(
    "lines, due",
    [
        # A header grid: the labels over their values.
        (
            [(60, 140, "Invoice Number"), (160, 140, "Invoice Due Date"), (60, 155, "88120"), (160, 155, "10/15/2026")],
            "2026-10-15",
        ),
        ([(60, 140, "TOTAL AMOUNT DUE ON October 15, 2026"), (60, 155, "$1,150.00")], "2026-10-15"),
        ([(60, 140, "Your payment is due by (Y-M-D)"), (60, 155, "2026-10-15")], "2026-10-15"),
        ([(60, 140, "Vervaldag: 15.10.2026")], "2026-10-15"),
        ([(60, 140, "Fälligkeitstag: 15.10.2026")], "2026-10-15"),
        ([(60, 140, "Zahlungsziel"), (200, 140, "15.10.26")], "2026-10-15"),
        # In a sentence: the net amount's date, not the discount's.
        ([(60, 600, "Bitte überweisen Sie bis zum 15.10.2026.")], "2026-10-15"),
        (
            [(60, 600, "Zahlbar innerhalb 30 Tagen netto bis 15.10.2026, 2% Skonto innerhalb 10 Tagen bis 25.09.2026")],
            "2026-10-15",
        ),
        ([(60, 600, "Thank you for your business. Payment is due by October 15, 2026.")], "2026-10-15"),
    ],
)
def test_due_dates_under_other_labels_or_in_a_sentence_are_read_for_a_person_to_check(tmp_path, lines, due):
    got = _read(_pdf(tmp_path, _HEAD + lines + _TABLE))
    assert got["due_date"].value == due
    assert got["due_date"].status != VERIFIED


@pytest.mark.parametrize(
    "note",
    [
        "Wichtige Information: Bei Bestellungen bis zum 19.12.2026 ist die Auslieferung bis 23.12.2026 garantiert.",
        "Prices are valid until 2026-12-31.",
        "Bei Zahlung innerhalb 10 Tagen bis 25.09.2026 gewähren wir 2% Skonto.",
    ],
)
def test_a_date_in_a_note_that_is_not_about_the_due_date_is_not_read_as_it(tmp_path, note):
    assert _read(_pdf(tmp_path, [*_HEAD, (60, 600, note), *_TABLE]))["due_date"].value is None


# ---------------------------------------------------------------- review of B-372 to B-375: what else shares the line


@pytest.mark.parametrize(
    "printed, read",
    [
        # Only the terms phrase is the terms: not a sentence after it, nor another label's value on the same line.
        ("Net 30 days. Statements are issued at the end of the month.", "Net 30"),
        ("Net 30   Statement date: end of month", "Net 30"),
        ("Net 30 Statement date: end of month", "Net 30"),
        ("Net 30, payment by end of month", "Net 30"),
        ("Net 30 days from end of month", "Net 30 EOM"),
        ("30 days end of month", "Net 30 EOM"),
        ("Net 30   Ship Via: UPS 2 Day", "Net 30"),
        ("Net 30  Sales Rep: John 12", "Net 30"),
        # Due on receipt (or prepaid) stays so, whatever days another clause or label prints.
        ("Due on receipt. Delivery within 2 weeks.", "Due on receipt"),
        ("Due on receipt  Ship Via: UPS 2 Day", "Due on receipt"),
        ("Due on receipt Ship Via: UPS 2 Day", "Due on receipt"),
        ("Upon receipt   Lead time: 2 weeks", "Due on receipt"),
        ("Upon receipt Lead time 2 weeks", "Due on receipt"),
        ("Prepaid  Freight: 2 Day", None),
        ("Prepaid Freight 2 Day", None),
        ("Due on receipt, 5% discount if paid in 7 days", "Due on receipt"),
        ("Net 30 days upon receipt of invoice", "Net 30"),
        # A discount with net months or a day of the next month: the net is not the months' or the day's number.
        ("1% 15 days, net 1 month", "1% 15 days, Net 1 month"),
        ("2% 10 days, Net 10th prox", "Net 10 prox"),  # the app has no form for both: the due date is kept
        ("2% 10 net 1 month", "2% 10 days, Net 1 month"),
        ("Net 15th following", "Net 15 prox"),
        ("EOM. Interest 1.5% per month after 30 days", "Due EOM"),
        # A discount in the next sentence still belongs to the terms.
        ("Payable in 30 days. 2% discount if paid within 10 days", "2% 10 Net 30"),
    ],
)
def test_only_the_terms_phrase_is_read_as_the_terms(printed, read):
    assert _terms(printed) == read


@pytest.mark.parametrize(
    "line",
    [
        "Terms: Net 30 days. Statements are issued at the end of the month.",
        "Terms: Due on receipt. Delivery within 2 weeks.",
    ],
)
def test_a_terms_label_line_on_an_invoice_reads_only_its_terms(tmp_path, line):
    got = _read(_pdf(tmp_path, [*_HEAD, (60, 140, line), *_TABLE]))["payment_terms"]
    assert got.value in ("Net 30", "Due on receipt")


def test_eom_terms_matching_the_due_date_do_not_confirm_the_invoice_date(tmp_path):
    # Net 30 EOM gives Nov 30 from any day of October: it says nothing about which day the invoice is dated.
    lines = [(380, 115, "Terms"), (470, 115, "Net 30 EOM"), (380, 130, "Due Date"), (470, 130, "2026-10-30")]
    got = _read(_pdf(tmp_path, _HEAD + lines + _TABLE))
    assert not any("terms" in r for r in got["invoice_date"].reasons if r.startswith("confirmed"))


@pytest.mark.parametrize(
    "line, po",
    [
        ("Bolts ½ in. per PO # 4500123456", "4500123456"),
        ("Shipped with FastTrack™ against your PO # 4500123456", "4500123456"),
        ("Order received… shipped per Purchase Order No. 4500123456", "4500123456"),
    ],
)
def test_a_po_after_a_character_written_as_several_is_read_whole(tmp_path, line, po):
    # "½", "™" and "…" are one character printed, several once accents and ligatures are taken apart: the PO read
    # from the line was cut short ("00123456").
    assert _read(_pdf(tmp_path, [*_HEAD, (60, 300, line), *_TABLE]))["po_number"].value == po


def test_a_terms_sentence_after_a_ligature_is_read(tmp_path):
    got = _read(
        _pdf(tmp_path, [*_HEAD, (60, 600, "Thank you ½ off next order. Payment is due within 45 days."), *_TABLE])
    )
    assert got["payment_terms"].value == "Net 45"


@pytest.mark.parametrize(
    "note",
    [
        "Amount due on account as of 2026-10-01: 0.00",
        "Amount due by October 25, 2026 with discount: 1,107.40",
        "Payment is due by the 15th. Next service 2026-12-01.",
        "Balance due on delivery October 30, 2026.",
        "Please pay by October 25, 2026 to take the 2% discount.",
        "Balance due on or after 2026-11-14 is subject to 2% interest.",
    ],
)
def test_a_date_that_is_not_the_due_date_in_a_due_sentence_is_not_read(tmp_path, note):
    assert _read(_pdf(tmp_path, [*_HEAD, (60, 600, note), *_TABLE]))["due_date"].value is None


def test_a_balance_due_on_delivery_label_is_not_a_due_date(tmp_path):
    lines = [(360, 345, "Balance due on delivery: 2026-10-30")]
    assert _read(_pdf(tmp_path, _HEAD + lines + _TABLE))["due_date"].value is None


@pytest.mark.parametrize(
    "note",
    [
        "90 days from date of invoice: warranty on all workmanship.",
        "5 days after invoice: no claims for shortages accepted.",
        "30 days from the invoice date for returns; restocking fee 15%.",
        "Holdback of 10% is payable within 60 days of substantial completion.",
        "30 days from date of invoice, interest at 2% per month is charged on overdue accounts.",
        "Payable within 10 days of statement date.",
    ],
)
def test_a_days_sentence_about_warranty_claims_returns_or_holdback_is_not_the_terms(tmp_path, note):
    assert _read(_pdf(tmp_path, [*_HEAD, (60, 600, note), *_TABLE]))["payment_terms"].value is None


@pytest.mark.parametrize(
    "note, terms",
    [
        ("Payment is due within 30 days of month end.", "Net 30 EOM"),
        ("Payment due within 30 days. 2% discount if paid within 10 days.", "2% 10 Net 30"),
        ("2% discount if paid within 10 days. Payment due within 30 days.", "2% 10 Net 30"),
        ("Payment due within 30 days; overdue accounts subject to 1.5% per month.", "Net 30"),
    ],
)
def test_a_terms_sentence_keeps_its_end_of_month_and_its_discount(tmp_path, note, terms):
    assert _read(_pdf(tmp_path, [*_HEAD, (60, 600, note), *_TABLE]))["payment_terms"].value == terms


@pytest.mark.parametrize("line", ["Your ref: call 416-555-1212", "Your reference: J. Smith 12"])
def test_your_reference_that_is_a_phone_number_or_words_is_no_po(tmp_path, line):
    assert _read(_pdf(tmp_path, [*_HEAD, (60, 140, line), *_TABLE]))["po_number"].value is None


@pytest.mark.parametrize(
    "line, field, value",
    [
        ("Fittings ½ in., PO # 4500123456", "po_number", "4500123456"),
        ("Bolts ½ in. | Due Date: 2026-11-30", "due_date", "2026-11-30"),
    ],
)
def test_a_label_after_a_character_written_as_several_reads_its_whole_value(tmp_path, line, field, value):
    assert _read(_pdf(tmp_path, [*_HEAD, (60, 300, line), *_TABLE]))[field].value == value


# ---------------------------------------------------------------- re-review: scans (OCR joins a row's words)


@pytest.mark.parametrize(
    "printed, read",
    [
        ("Net30Freight:2Day", "Net 30"),
        ("Net30Lead time2weeks", "Net 30"),
        ("Net45Rep:12", "Net 45"),
        ("Net30ShipVia:UPS2Day", "Net 30"),
        ("Net 30 Ship Via: UPS 2 Day", "Net 30"),
        ("Payablesurréception", "Due on receipt"),
        ("Dueonreceipt", "Due on receipt"),
        ("Dueuponreceipt", "Due on receipt"),
        ("Net30th", "Net 30"),
        ("2%10 days,Net10thprox", "Net 10 prox"),
        ("2% 10 days, Net 1Oth prox", "Net 10 prox"),  # OCR's O for a zero
        ("EOM   Net 30", "Net 30 EOM"),
    ],
)
def test_terms_ocr_ran_together_are_read_as_printed(printed, read):
    assert _terms(printed) == read


def test_please_quote_the_po_number_is_an_instruction_not_a_po(tmp_path):
    line = (60, 600, "Please quote PO number 4500 and invoice NS-20418 with payment.")
    assert _read(_pdf(tmp_path, [*_HEAD, line, *_TABLE]))["po_number"].value is None


def _reading(field, value, method, score=0.95):
    from ap_coder.capture.types import Box, Reading

    return Reading(field, value, str(value), [Box(1, 0.1, 0.1, 0.2, 0.12)], score, method)


def test_two_ocr_reads_of_unmeasured_wording_stay_checked():
    # On a scan the rule reader reads both OCR passes: two "readers" of the same pixels agreeing on "ACCT-1015" after
    # "Your Reference" made it likely. Wording the benchmark never prints stays checked however many reads agree.
    from ap_coder.capture.confidence import fuse
    from ap_coder.capture.types import CHECK, UNMEASURED

    po = {"po_number": [_reading("po_number", "ACCT-1015", "label-right" + UNMEASURED)]}
    got, _ = fuse({"rules": po, "ocr2": po}, [], layout_source="ocr", today=TODAY)
    assert got["po_number"].status == CHECK and got["po_number"].confidence <= 0.8


def test_unmeasured_terms_confirmed_by_the_due_date_may_be_likely():
    from ap_coder.capture.confidence import fuse
    from ap_coder.capture.types import LIKELY, UNMEASURED

    cands = {
        "invoice_date": [_reading("invoice_date", "2026-09-15", "label-right")],
        "due_date": [_reading("due_date", "2026-10-15", "label-right")],
        "payment_terms": [_reading("payment_terms", "Net 30", "sentence" + UNMEASURED, 0.6)],
    }
    got, _ = fuse({"rules": cands, "ocr2": cands}, [], layout_source="ocr", today=TODAY)
    terms = got["payment_terms"]
    assert terms.status == LIKELY
    assert not any("a person checks it" in r for r in terms.reasons)


def test_terms_in_the_next_column_of_a_tight_grid_are_no_po(tmp_path):
    # A header grid whose columns are as wide as their text plus 10 points: the PO label's value was taken from the
    # terms column ("Net 30", likely).
    import pymupdf

    heads = ["Customer ID", "Terms", "Ship Via", "PO#", "Due Date"]
    vals = ["C-1015", "Net 30", "UPS 2 Day", "4500123456", "2026-11-14"]
    grid, x = [], 60.0
    for h, v in zip(heads, vals, strict=True):
        grid += [(x, 190, h, 9), (x, 203, v, 9)]
        x += max(pymupdf.get_text_length(h, fontsize=9), pymupdf.get_text_length(v, fontsize=9)) + 10
    assert _read(_pdf(tmp_path, [*_HEAD, *grid, *_TABLE]))["po_number"].value != "Net 30"
