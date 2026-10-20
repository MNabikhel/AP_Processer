"""Reading the invoice date and number as real invoices print them (sweep 4): titles that carry the number and the date
("Rechnung Nr. 471102 vom 05.03.2018", "Facture n° 562044 du 2 juillet 2015"), receipts, other languages' months,
a credit note's own number beside the invoice it credits, and day/month order. Every fixture is generated here."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ap_coder.capture import analyze
from ap_coder.capture.normalize import parse_date
from ap_coder.capture.reader import read_fields
from ap_coder.capture.types import VERIFIED, Box, DocLayout, PageLayout, Word

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
    return analyze(path, ocr=False, today=TODAY, tables=False)


def _scan(rows: list[tuple[float, float, str]]) -> DocLayout:
    """A scanned page's layout: (x, y, text) per OCR line, in fractions of the page."""
    from ap_coder.capture.layout import group_lines

    words = []
    for x, y, text in rows:
        at = x
        for w in text.split():
            width = 0.0085 * len(w)
            words.append(Word(w, Box(1, at, y, at + width, y + 0.016), 0.95, "ocr"))
            at += width + 0.006
    return DocLayout([PageLayout(1, 1700, 2200, words, group_lines(words, 1))], "ocr")


def _top(layout: DocLayout, field: str):
    found = read_fields(layout).get(field) or []
    return found[0] if found else None


_TOTALS = [(380, 600, "Nettobetrag"), (500, 600, "1.000,00"), (380, 615, "Umsatzsteuer 19%"), (500, 615, "190,00"),
           (380, 630, "Gesamtbetrag"), (500, 630, "1.190,00")]  # fmt: skip


def test_a_german_title_with_the_number_and_the_date_gives_both(tmp_path):
    # "Handelsrechnung (380) Nr. 471102 vom 05.03.2018": a German invoice's title carries its number and its date (the
    # "Rechnungsnummer" and "Rechnungsdatum" labels are not printed). Neither was read.
    path = _pdf(
        tmp_path,
        [
            (60, 50, "Muster Lieferant GmbH", 14),
            (60, 90, "Handelsrechnung (380) Nr. 471102 vom 05.03.2018", 13),
            (60, 120, "Liefer- und Leistungsdatum:"),
            (250, 120, "04.03.2018"),
            *_TOTALS,
        ],
    )
    f = _read(path).fields
    assert f["invoice_number"].value == "471102"
    assert f["invoice_date"].value == "2018-03-05"
    assert f["invoice_date"].status != VERIFIED  # read from a title, not a label: measured apart, a person sees it


def test_the_date_after_the_number_may_be_the_next_pieces_of_the_row(tmp_path):
    # "Gutschrift (389) | Nr. 471102 | vom | 05.03.2018": the row printed in four pieces.
    path = _pdf(
        tmp_path,
        [
            (60, 50, "Muster Lieferant GmbH", 14),
            (60, 90, "Gutschrift (389)", 13),
            (330, 90, "Nr. 471102", 13),
            (420, 90, "vom", 13),
            (455, 90, "05.03.2018", 13),
            *_TOTALS,
        ],
    )
    f = _read(path).fields
    assert f["invoice_number"].value == "471102"
    assert f["invoice_date"].value == "2018-03-05"


def test_a_french_invoice_number_dated_du_gives_the_invoice_date_but_not_a_period(tmp_path):
    # "Facture n°562044387 du 02 Juillet 2015": the date the invoice was issued, after its number.
    layout = _scan([(0.07, 0.10, "Fibre Services SAS"), (0.07, 0.38, "Facture n°562044387 du 02 Juillet 2015")])
    assert _top(layout, "invoice_date").value == "2015-07-02"
    assert _top(layout, "invoice_number").value == "562044387"
    # "Facture no 4471 du 01/03/2026 au 31/03/2026": the period billed, not the invoice's date.
    period = _scan([(0.07, 0.10, "Fibre Services SAS"), (0.07, 0.38, "Facture no 4471 du 01/03/2026 au 31/03/2026")])
    assert _top(period, "invoice_date") is None


def test_an_english_number_issued_at_a_date_gives_the_date():
    layout = _scan([(0.1, 0.1, "Bei Spiel GmbH"), (0.1, 0.42, "Invoice # RE-20201121/508 issued at 2020-11-21")])
    assert _top(layout, "invoice_number").value == "RE-20201121/508"
    assert _top(layout, "invoice_date").value == "2020-11-21"


def test_a_grid_of_beleg_labels_with_the_colon_beside_the_value(tmp_path):
    # "Beleg-Nr | : RK21012345", "Beleg-Datum | : 16.09.2013": the label's colon printed in the value's column was taken
    # for another label before the value, and the German "document number / date" labels were not known.
    path = _pdf(
        tmp_path,
        [
            (40, 50, "MUSTERLIEFERANT GMBH", 12),
            (370, 50, "RECHNUNGS-KORREKTUR"),
            (370, 80, "Beleg-Nr"),
            (470, 80, ": RK21012345"),
            (370, 92, "Beleg-Datum"),
            (470, 92, ": 16.09.2013"),
            (370, 104, "Bestell-Nr"),
            (470, 104, ": B123456789"),
            *_TOTALS,
        ],
    )
    f = _read(path).fields
    assert f["invoice_number"].value == "RK21012345"
    assert f["invoice_date"].value == "2013-09-16"


def test_months_of_other_languages_are_read():
    assert parse_date("24. Januar 2014") == "2014-01-24"
    assert parse_date("11. März 2014") == "2014-03-11"
    assert parse_date("29 maart 2014") == "2014-03-29"
    assert parse_date("10 de febrero de 2022") == "2022-02-10"
    assert parse_date("12 de março de 2020") == "2020-03-12"
    assert parse_date("3 dicembre 2021") == "2021-12-03"
    # Short forms that are English words are not months: "1 set 2500" is a quantity and a price.
    assert parse_date("1 set 2500") is None


def test_a_page_number_run_into_the_label_still_reads_the_date():
    # OCR ran "Page 1/2" into the next words: "Page1/2 Invoice date(Y-M-D) 2025-06-21".
    layout = _scan([(0.06, 0.07, "POSTES CANADA"), (0.61, 0.07, "Page1/2 Invoice date(Y-M-D) 2025-06-21")])
    assert _top(layout, "invoice_date").value == "2025-06-21"


def test_a_receipts_number_and_date_are_read():
    # "ReceiptNumber334567", "ReceiptDate01/18/2024" (OCR glued the words), and "Receipt # No.: 9876".
    layout = _scan(
        [(0.27, 0.19, "Contoso"), (0.27, 0.46, "ReceiptNumber334567"), (0.27, 0.49, "ReceiptDate01/18/2024")]
    )
    assert _top(layout, "invoice_number").value == "334567"
    assert _top(layout, "invoice_date").value == "2024-01-18"
    other = _scan([(0.6, 0.03, "Project Statement"), (0.77, 0.11, "Receipt # No.: 9876")])
    assert _top(other, "invoice_number").value == "9876"


def test_a_credit_notes_own_number_not_the_invoice_it_credits(tmp_path):
    # A French credit note: "Avoir AV-2017-0005" is its number, "Facture liée FA-2017-0010" the invoice it credits.
    # The credit's number was not read and the invoice's was taken for it (likely).
    path = _pdf(
        tmp_path,
        [(40, 40, "Moulin Rouge SARL", 12), (40, 90, "Avoir AV-2017-0005", 16), (40, 210, "Date de facture"),
         (190, 210, "16/11/2017"), (40, 226, "Facture liée"), (190, 226, "FA-2017-0010"), (380, 600, "Total HT"),
         (500, 600, "-100,00"), (380, 615, "TVA 20%"), (500, 615, "-20,00"), (380, 630, "Total TTC"),
         (500, 630, "-120,00")],
    )  # fmt: skip
    assert _read(path).fields["invoice_number"].value == "AV-2017-0005"
    # Its scan, OCR glued the title to the number: "AvoirAV-2017-0005".
    scan = _scan([(0.07, 0.11, "AvoirAV-2017-0005"), (0.07, 0.36, "Facture liee"), (0.27, 0.36, "FA-2017-0010")])
    assert _top(scan, "invoice_number").value == "AV-2017-0005"


def test_a_us_customers_invoice_shows_an_unsettled_date_month_first_for_a_person_to_check(tmp_path):
    # "Date: 9/10/2020" on a statement to a customer in Walnut, WY, with no Canadian address or GST number and no other
    # date to settle it: it was shown day first (October 9). US invoices print the month first; shown so, still checked.
    path = _pdf(
        tmp_path,
        [(40, 40, "Contoso Training", 14), (450, 70, "Date: 9/10/2020"), (450, 82, "Statement # 9876"),
         (100, 110, "Sold To"), (150, 110, "Wingtip Toys"), (150, 122, "554 Magnolia Way"),
         (150, 134, "Walnut, WY, 98432"), (380, 600, "Subtotal"), (500, 600, "$1,000.00"), (380, 615, "Sales Tax"),
         (500, 615, "$30.00"), (380, 630, "Total"), (500, 630, "$1,030.00")],
    )  # fmt: skip
    f = _read(path).fields
    assert f["invoice_date"].value == "2020-09-10"
    assert f["invoice_date"].status == "check"


def test_a_date_printed_with_points_is_not_turned_month_first_by_a_german_postcode(tmp_path):
    # "DE 80333 München" reads as a Delaware ZIP code; a date printed 05.03.2018 is a European one: day first.
    path = _pdf(
        tmp_path,
        [(60, 50, "Lieferant GmbH", 14), (60, 70, "DE 80333 München"), (60, 90, "Rechnungsdatum: 05.03.2018"),
         *_TOTALS],
    )  # fmt: skip
    assert _read(path).fields["invoice_date"].value == "2018-03-05"


def test_a_year_run_into_the_time_is_not_read_as_the_year():
    # A receipt's "02/15/10 11:10AM" as OCR read it: "02/15/1011:10AM". It was read as 1011-02-15 (check).
    assert parse_date("02/15/1011:10AM") == "2010-02-15"
    assert parse_date("Date 15/02/1011") is None  # no invoice is dated 1011
