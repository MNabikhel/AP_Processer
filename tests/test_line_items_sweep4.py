"""Line items on real-world and hard invoices (sweep 4): page subtotals and balances carried from page to page, and
the other rows a line table prints that are no lines. Small generated pages: one table each."""

from pathlib import Path

from ap_coder.capture import _page_lines, layout
from ap_coder.capture.reader import listed_items, read_line_items
from ap_coder.capture.types import Box, DocLayout, FieldResult, PageLayout, Word


def _words(*cells: tuple[str, float, float, float]) -> list[Word]:
    """The words of cells (text, x0, x1, y middle), one text line high, spread over the cell by their length."""
    out = []
    for text, x0, x1, y in cells:
        per = (x1 - x0) / len(text)
        at = 0
        for word in text.split(" "):
            box = Box(1, x0 + at * per, y - 0.006, x0 + (at + len(word)) * per, y + 0.006)
            out.append(Word(word, box, 1.0, "text"))
            at += len(word) + 1
    return out


def _layout(words: list[Word], source: str = "text") -> DocLayout:
    return DocLayout([PageLayout(1, 612, 792, words, layout.group_lines(words, 1))], source)


def _read(words: list[Word]) -> list[tuple]:
    return [(li.description, li.quantity, li.unit_price, li.amount) for li in read_line_items(_layout(words))]


HEADINGS = (("Description", 0.10, 0.20, 0.20), ("Qty", 0.55, 0.58, 0.20), ("Unit Price", 0.68, 0.76, 0.20),
            ("Amount", 0.85, 0.90, 0.20))  # fmt: skip


def _row(y: float, desc: str, qty: str, price: str, amount: str) -> list[tuple[str, float, float, float]]:
    return [(desc, 0.10, 0.10 + 0.008 * len(desc), y), (qty, 0.56, 0.56 + 0.008 * len(qty), y),
            (price, 0.70, 0.70 + 0.008 * len(price), y), (amount, 0.90 - 0.008 * len(amount), 0.90, y)]  # fmt: skip


def test_page_subtotals_and_balances_carried_between_pages_are_no_lines():
    """A long table prints its page's subtotal at the foot of each page and the balance brought forward at the top
    of the next ("Balance brought forward / Montant reporté", "Page subtotal", "Sous-total de la page", OCR's
    "Balancebrought forward"): sums of the lines, never lines themselves."""
    for label in ("Balance brought forward", "Balance brought forward / Montant reporté", "Balancebrought forward",
                  "Montant reporté", "Übertrag"):  # fmt: skip
        words = _words(*HEADINGS, (label, 0.10, 0.10 + 0.008 * len(label), 0.22), ("1,234.56", 0.836, 0.90, 0.22),
                       *_row(0.24, "Bearing 6205-2RS", "4", "10.31", "41.24"),
                       *_row(0.26, "Copy paper", "10", "38.65", "386.50"))  # fmt: skip
        assert _read(words) == [("Bearing 6205-2RS", 4.0, 10.31, 41.24), ("Copy paper", 10.0, 38.65, 386.5)], label
    for label in ("Page subtotal", "Subtotal this page", "Sous-total de la page", "Total de la page",
                  "Balance carried forward", "Page total"):  # fmt: skip
        words = _words(*HEADINGS, *_row(0.24, "Bearing 6205-2RS", "4", "10.31", "41.24"),
                       *_row(0.26, "Copy paper", "10", "38.65", "386.50"),
                       (label, 0.10, 0.10 + 0.008 * len(label), 0.28), ("427.74", 0.836, 0.90, 0.28))  # fmt: skip
        assert _read(words) == [("Bearing 6205-2RS", 4.0, 10.31, 41.24), ("Copy paper", 10.0, 38.65, 386.5)], label


# A table whose column headings the reader doesn't know (German: "Bezeichnung", "Menge", "Einzelpreis", "Betrag"),
# found by the table finder, with the invoice's totals inside the box found: rows read by their amount column.
FOREIGN = [("Bezeichnung", 0.10, 0.20, 0.30), ("Menge", 0.50, 0.55, 0.30), ("Einzelpreis", 0.62, 0.72, 0.30),
           ("Betrag", 0.84, 0.90, 0.30),
           ("Trennblätter A4", 0.10, 0.22, 0.33), ("20", 0.53, 0.55, 0.33), ("9,90", 0.68, 0.72, 0.33),
           ("198,00", 0.85, 0.90, 0.33),
           ("Joghurt Banane", 0.10, 0.22, 0.36), ("50", 0.53, 0.55, 0.36), ("5,50", 0.68, 0.72, 0.36),
           ("275,00", 0.85, 0.90, 0.36)]  # fmt: skip
TOTALS_DE = [("Umsatzsteuer 7 %", 0.50, 0.65, 0.40), ("19,25", 0.86, 0.90, 0.40),
             ("Umsatzsteuer 19 %", 0.50, 0.65, 0.43), ("37,62", 0.86, 0.90, 0.43),
             ("Rechnungsbetrag netto", 0.50, 0.70, 0.46), ("473,00", 0.85, 0.90, 0.46),
             ("Bruttosumme", 0.50, 0.62, 0.49), ("529,87", 0.85, 0.90, 0.49)]  # fmt: skip
ITEMS_DE = [("Trennblätter A4", 20.0, 9.9, 198.0), ("Joghurt Banane", 50.0, 5.5, 275.0)]


def _table_rows(cells, box, targets=None) -> list[tuple]:
    doc = _layout(_words(*cells), source="ocr")
    items = read_line_items(doc, {1: [box]}, targets=targets)
    return [(li.description, li.quantity, li.unit_price, li.amount) for li in items]


def test_rows_read_by_their_amounts_end_at_the_row_that_sums_them():
    """With no column headings to go by, the rows of the table found end at the first row that prints the sum of the
    rows above it (a subtotal in a wording the reader doesn't know: "Rechnungsbetrag netto", "Positionssumme"): it,
    the taxes and the totals under it are no lines; nor is a tax's row ("Umsatzsteuer 19 %")."""
    box = Box(1, 0.08, 0.28, 0.92, 0.51)
    assert _table_rows([*FOREIGN, *TOTALS_DE], box) == ITEMS_DE
    # A sum in a wording no rule knows ("Warenwert", the goods' value), then a charge and the total.
    unknown = [("Warenwert", 0.50, 0.60, 0.40), ("473,00", 0.85, 0.90, 0.40),
               ("Fracht pauschal", 0.50, 0.65, 0.43), ("12,00", 0.86, 0.90, 0.43),
               ("Endbetrag", 0.50, 0.60, 0.46), ("485,00", 0.85, 0.90, 0.46)]  # fmt: skip
    assert _table_rows([*FOREIGN, *unknown], box) == ITEMS_DE


def test_rows_read_by_their_amounts_are_kept_only_when_they_add_up():
    """Rows read by their amounts alone are a guess: kept when they add up to a sum printed under them or to the
    invoice's subtotal, else none (a wrong line is worse than a missing one). The totals' block read alone (a
    table of label and amount) is no line table."""
    box = Box(1, 0.08, 0.28, 0.92, 0.38)  # the box ends above the totals
    assert _table_rows(FOREIGN, box) == []  # nothing to say they add up
    assert _table_rows(FOREIGN, box, targets=[473.0]) == ITEMS_DE
    assert _table_rows(FOREIGN, box, targets=[999.0]) == []
    netto = [("Summe netto", 0.50, 0.62, 0.70), ("473,00", 0.85, 0.90, 0.70)]  # printed under the box found
    assert _table_rows([*FOREIGN, *netto], box) == ITEMS_DE
    totals = [("Positionssumme", 0.50, 0.65, 0.40), ("473,00", 0.85, 0.90, 0.40),
              ("Gesamtbetrag der Zuschläge", 0.50, 0.70, 0.43), ("0,00", 0.86, 0.90, 0.43),
              ("Rechnungssumme ohne USt.", 0.50, 0.70, 0.46), ("473,00", 0.85, 0.90, 0.46),
              ("Steuerbetrag", 0.50, 0.62, 0.49), ("56,87", 0.86, 0.90, 0.49),
              ("Bruttosumme", 0.50, 0.62, 0.52), ("529,87", 0.85, 0.90, 0.52)]  # fmt: skip
    assert _table_rows(totals, Box(1, 0.45, 0.37, 0.92, 0.54)) == []


# A till receipt: no column headings, each item's amount at the right with its tax flag ("J"), a product code between.
RECEIPT = [("Walmart Supercentre", 0.37, 0.60, 0.04),
           ("GR MN 6PK", 0.13, 0.29, 0.19), ("085430100956", 0.36, 0.58, 0.19), ("$12.97", 0.66, 0.76, 0.19),
           ("BATH TOWEL", 0.12, 0.30, 0.21), ("002215445066", 0.36, 0.58, 0.21), ("$7.97J", 0.68, 0.79, 0.21),
           ("OPP BT 896400119106", 0.12, 0.58, 0.23), ("$4.00 J", 0.68, 0.79, 0.23),
           ("OPP BT", 0.12, 0.24, 0.25), ("896400119105", 0.36, 0.58, 0.25), ("$4.00J", 0.68, 0.78, 0.25),
           ("COUPON", 0.12, 0.24, 0.27), ("1.00-", 0.70, 0.77, 0.27),
           ("$27.94", 0.66, 0.77, 0.30), ("SUBTOTAL", 0.46, 0.60, 0.302),
           ("GST 5%", 0.42, 0.53, 0.32), ("$1.40", 0.68, 0.77, 0.32),
           ("TOTAL", 0.51, 0.60, 0.34), ("$29.34", 0.66, 0.77, 0.34),
           ("VISA TEND", 0.42, 0.60, 0.36), ("$29.34", 0.66, 0.77, 0.36),
           ("#ITEMS SOLD 4", 0.22, 0.77, 0.40)]  # fmt: skip
RECEIPT_ITEMS = [("GR MN 6PK", 12.97), ("BATH TOWEL", 7.97), ("OPP BT", 4.0), ("OPP BT", 4.0), ("COUPON", -1.0)]


def test_a_receipts_items_are_the_amounts_above_its_subtotal_that_add_up_to_it():
    """A receipt prints no column headings: its items are the amounts in the column right above the subtotal, each
    with the words on its row (the product code left out), read up from the subtotal until they add up to it (a
    coupon taken off too). Kept only when they add up: to the subtotal, or the total less the tax."""
    doc = _layout(_words(*RECEIPT), source="ocr")
    assert read_line_items(doc) == []  # no headings: the reader under the headings reads nothing
    items = listed_items(doc, [27.94])
    assert [(li.description, li.amount) for li in items] == RECEIPT_ITEMS
    assert listed_items(doc, [999.0]) == [] and listed_items(doc, []) == []
    fields = {"subtotal": FieldResult("subtotal", 27.94, 0.9, "likely")}
    chosen, _readings = _page_lines(Path("receipt.png"), doc, fields, [], False)
    assert [(li.description, li.amount) for li in chosen] == RECEIPT_ITEMS


def test_a_bills_totals_above_its_total_are_not_listed_items():
    """Amounts above the total that add up to it but name the bill's totals ("Montant HT", "Remise totale", "TVA"),
    or the subtotal printed once more with no words, are no items."""
    summary = _words(("Montant HT", 0.50, 0.65, 0.60), ("1 000,00", 0.80, 0.90, 0.60),
                     ("TVA 20 %", 0.50, 0.62, 0.62), ("200,00", 0.84, 0.90, 0.62),
                     ("TOTAL", 0.50, 0.58, 0.66), ("1 200,00", 0.80, 0.90, 0.66))  # fmt: skip
    assert listed_items(_layout(summary, source="ocr"), [1200.0]) == []
    again = _words(("1,200.00", 0.80, 0.90, 0.62), ("TOTAL", 0.50, 0.58, 0.66), ("1,200.00", 0.80, 0.90, 0.66))
    assert listed_items(_layout(again, source="ocr"), [1200.0]) == []


def test_net_price_and_net_worth_headings_name_the_price_and_the_amount():
    """ "Net price" is the unit price and "Net worth" the line's amount, its "VAT [%]" set close beside it another
    column, and "No. Description" the description; the "Gross worth" (with VAT) is not the line's amount: the lines
    add up to the subtotal before tax."""
    words = _words(
        ("No. Description", 0.09, 0.23, 0.36), ("Qty", 0.41, 0.44, 0.36), ("UM", 0.47, 0.50, 0.36),
        ("Net price Net worth VAT[%]", 0.54, 0.81, 0.36), ("Gross worth", 0.84, 0.91, 0.36),
        ("1.", 0.10, 0.12, 0.40), ("Marble coffee table", 0.15, 0.35, 0.40), ("5,00", 0.41, 0.44, 0.40),
        ("each", 0.46, 0.50, 0.40), ("271,01", 0.57, 0.62, 0.40), ("1 355,05", 0.65, 0.72, 0.40),
        ("10%", 0.77, 0.81, 0.40), ("1 490,55", 0.84, 0.91, 0.40),
        ("with malachite inlay", 0.15, 0.33, 0.413), ("Home Decor H5372", 0.15, 0.30, 0.426),
        ("2.", 0.10, 0.12, 0.45), ("Black marble top", 0.15, 0.35, 0.45), ("1,00", 0.41, 0.44, 0.45),
        ("each", 0.46, 0.50, 0.45), ("280,50", 0.57, 0.62, 0.45), ("280,50", 0.67, 0.72, 0.45),
        ("10%", 0.77, 0.81, 0.45), ("308,55", 0.86, 0.91, 0.45),
    )  # fmt: skip
    assert [(q, p, a) for _d, q, p, a in _read(words)] == [(5.0, 271.01, 1355.05), (1.0, 280.5, 280.5)]


def test_a_description_wrapped_onto_the_lines_under_its_row_is_read_whole():
    """A long description wraps onto one or two more lines under its row, in the description's column: they are the
    rest of it, not dropped. A line under the table that reaches other columns, or a total, is not."""
    words = _words(*HEADINGS, *_row(0.24, "Marble coffee table", "5", "271.01", "1,355.05"),
                   ("with malachite inlay", 0.10, 0.26, 0.253), ("Home Decor H5372", 0.10, 0.23, 0.266),
                   *_row(0.29, "Black marble top", "1", "280.50", "280.50"),
                   ("Thank you for your business, payment due in 30 days", 0.10, 0.80, 0.303))  # fmt: skip
    assert [d for d, *_ in _read(words)] == ["Marble coffee table with malachite inlay Home Decor H5372",
                                            "Black marble top"]  # fmt: skip


def test_a_summary_row_with_no_words_under_the_lines_is_no_line():
    """Under the lines, a summary row with no words prints the rate, the lines' sum, the tax and the gross ("10%
    1 635,55 163,56 1 799,11"): it prints the sum of the rows above it, so it is no line."""
    words = _words(*HEADINGS, *_row(0.24, "Marble coffee table", "5", "271.01", "1,355.05"),
                   *_row(0.27, "Black marble top", "1", "280.50", "280.50"),
                   ("10%", 0.50, 0.53, 0.31), ("1,635.55", 0.62, 0.70, 0.31), ("163.56", 0.74, 0.80, 0.31),
                   ("1,799.11", 0.84, 0.90, 0.31))  # fmt: skip
    assert [a for *_r, a in _read(words)] == [1355.05, 280.5]


def test_a_scan_tilted_five_degrees_is_straightened():
    """A page scanned 5 degrees off (a hand-fed scanner, a phone held askew) is a tilt to straighten, not a turn: at
    5 degrees a row's description sits more than three rows above or below its amount at the other end of the table.
    A page turned a quarter (text running down the page) is not straightened."""
    import math

    from ap_coder.capture.layout import _skew

    slope = math.tan(math.radians(5))
    lines = [([[0, 100 + 40 * i], [400, 100 + 40 * i + 400 * slope], [400, 120 + 40 * i + 400 * slope],
               [0, 120 + 40 * i]], "some text here", 0.9) for i in range(8)]  # fmt: skip
    assert abs(_skew(lines, 1000, 1300) - slope * 1000 / 1300) < 1e-6
    turned = [([[0, 0], [100, 50], [100, 70], [0, 20]], "sideways text", 0.9)] * 8
    assert _skew(turned, 1000, 1300) == 0.0


def test_a_discount_printed_without_its_sign_is_taken_off_when_that_adds_up():
    """ "Discount 5%  2,689.02" with its minus lost (OCR, or printed in a column of its own): taken off, when the
    lines then add up to the subtotal; left as printed otherwise."""
    from ap_coder.capture import _discounts_taken_off
    from ap_coder.capture.types import LineReading

    lines = [LineReading("Bolts", 2.0, 50.0, 100.0), LineReading("Nuts", 5.0, 10.0, 50.0),
             LineReading("Discount 5%", None, None, 7.5)]  # fmt: skip
    assert [li.amount for li in _discounts_taken_off(lines, [142.5])] == [100.0, 50.0, -7.5]
    assert [li.amount for li in _discounts_taken_off(lines, [157.5])] == [100.0, 50.0, 7.5]  # it adds up as printed
    assert [li.amount for li in _discounts_taken_off(lines, [999.0])] == [100.0, 50.0, 7.5]


def test_a_table_with_no_amount_printed_as_money_is_no_line_table():
    """Rows whose "amounts" are none of them printed as money ("774320", "-9692": a customer number and a phone
    number under headings read off a page upside down) are no line items, unless they add up to the subtotal."""
    words = _words(*HEADINGS, ("Cust. ID: Billed To", 0.10, 0.30, 0.24), ("774320", 0.85, 0.90, 0.24),
                   ("Ordered: www.liberty.com", 0.10, 0.34, 0.27), ("-9692", 0.86, 0.90, 0.27))  # fmt: skip
    assert _read(words) == []
    doc = _layout(words)
    assert len(read_line_items(doc, {1: [Box(1, 0.05, 0.15, 0.95, 0.30)]}, targets=[764628.0])) == 2
