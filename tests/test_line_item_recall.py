"""Line items found on tables the reader used to miss or misread: headings printed in two languages and run together
(by the PDF or by OCR), a heading's longest wording naming its column, a unit-of-measure column between the quantity
and the price, an address or a sentence taken for the heading row, a subtotal's amount set a hair above its label, a
reading that ran on into the totals, a credit's returned quantity printed with its sign, amounts as OCR reads them
("342.18CR", "1530:00") and unit prices to four decimals. Small synthetic pages: one table each."""

from ap_coder.capture import _to_subtotal, layout
from ap_coder.capture.normalize import find_amounts, parse_amount
from ap_coder.capture.reader import _heading_cells, read_line_items
from ap_coder.capture.types import Box, DocLayout, Line, LineReading, PageLayout, Word


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


def _layout(words: list[Word]) -> DocLayout:
    return DocLayout([PageLayout(1, 612, 792, words, layout.group_lines(words, 1))], "text")


def _segment(text: str, x0: float = 0.1) -> Line:
    """One line segment of headings set close together, a word at a time from ``x0``."""
    words, x = [], x0
    for t in text.split():
        words.append(Word(t, Box(1, x, 0.3, x + 0.01 * len(t), 0.31)))
        x += 0.01 * len(t) + 0.005
    return Line(words, Box(1, words[0].box.x0, 0.3, words[-1].box.x1, 0.31), text)


def _names(text: str) -> list[str]:
    return [name for name, _box in _heading_cells(_segment(text), {})]


def _read(words: list[Word]) -> list[tuple]:
    return [(li.description, li.quantity, li.unit_price, li.amount) for li in read_line_items(_layout(words))]


# --- column headings ------------------------------------------------------------------------------------------------


def test_bilingual_headings_run_together_are_one_heading_each():
    """A heading printed "English / French" names one column; two of them set close together are two columns."""
    assert _names("Price / Taux Ext. Price / Prix total") == ["unit_price", "amount"]
    assert _names("Rate / Prix unit. Ext. Price / Prix total") == ["unit_price", "amount"]
    assert _names("Unit Price / Prix unit. Line Total / Total") == ["unit_price", "amount"]
    assert _names("Price/Taux Amount/Montant") == ["unit_price", "amount"]  # the slashes touching the words
    assert _names("Product / Service / Désignation") == ["description"]  # by its parts that are headings
    assert _names("Hrs/Qty / Qté UOM / U.M.") == ["quantity"]
    boxes = _heading_cells(_segment("Price / Taux Ext. Price / Prix total"), {})
    assert boxes[0][1].x1 < boxes[1][1].x0  # each heading boxed on its own words


def test_the_longest_heading_names_the_column():
    assert _names("Unit Cost") == ["unit_price"]  # not a quantity ("Unit")
    assert _names("Prix total") == ["amount"]  # not a unit price ("Prix")
    assert _names("Total Price") == ["amount"]  # one heading, not a total and a price
    assert _names("Prix unit.") == ["unit_price"] and _names("P.U.") == ["unit_price"]


def test_words_after_a_heading_and_a_unit_column_between_headings():
    assert _names("Price Each / Prix unitaire Amount / Prix total") == ["unit_price", "amount"]
    # "Unit / Unité" (the unit of measure) between the quantity and the price is no second quantity column.
    assert _names("Quantity / Quantité Unit / Unité Unit Cost / Taux") == ["quantity", "unit_price"]
    # Two headings the PDF's text runs into one word ("UnitéRate") are read apart.
    cells = _heading_cells(_segment("Qty / Qté U/M / UnitéRate / Prix unitaire"), {})
    assert [name for name, _ in cells] == ["quantity", "unit_price"]
    assert cells[0][1].x1 <= cells[1][1].x0 + 1e-9


def test_headings_ocr_ran_together_are_read_apart():
    """OCR drops the spaces between bilingual headings: they are read apart at the "/" and where a capital starts a
    word ("QuantitePrice"), each boxed on its own share of the word, so the values are looked for under it."""
    assert _names("Hr/Qty/QuantitePriceEach/PrixunitaireLineTotal/Montant") == ["quantity", "unit_price", "amount"]
    assert _names("QTY/Quantiteu/M/u.M.Price Each/TauxExt.Price/Total") == ["quantity", "unit_price", "amount"]
    assert _names("Qty / Qté UOM / U.M. Unit Cost / Prix unitaire Ext. Price / Prix total") == [
        "quantity", "unit_price", "amount"]  # fmt: skip


def test_a_totals_label_in_two_languages_is_no_column_heading():
    assert _names("Balance Due / Total à payer") == []
    assert _names("Subtotal / Sous-total") == []


def test_an_address_or_a_sentence_is_no_heading_row():
    """An address ("Unit 9") has a number in it; a sentence that starts with a heading's word has more other words
    than headings."""
    assert _names("Unit 9") == []
    assert _names("Total amount includes service fees and price adjustments") == ["amount"]  # one heading at most
    words = _words(
        ("Ironwood Gas Inc.", 0.08, 0.30, 0.07), ("Unit 1", 0.08, 0.11, 0.115), ("Amount Due", 0.59, 0.68, 0.117),
        ("$1,570.51", 0.76, 0.91, 0.145),
        ("Description", 0.08, 0.16, 0.42), ("QTY", 0.64, 0.67, 0.42), ("Price", 0.76, 0.79, 0.42),
        ("Total", 0.88, 0.92, 0.42),
        ("Delivery charge", 0.08, 0.17, 0.44), ("102.47", 0.877, 0.917, 0.44),
        ("Water consumption", 0.08, 0.20, 0.46), ("132", 0.65, 0.67, 0.46), ("3.3810", 0.75, 0.79, 0.46),
        ("446.29", 0.877, 0.917, 0.46),
        ("SUBTOTAL", 0.11, 0.18, 0.56), ("548.76", 0.36, 0.41, 0.56),
    )  # fmt: skip
    assert _read(words) == [("Delivery charge", None, None, 102.47), ("Water consumption", 132.0, 3.381, 446.29)]


# --- tables read ----------------------------------------------------------------------------------------------------


BILINGUAL = [
    ("Code", 0.07, 0.11, 0.32), ("Product / Service / Désignation", 0.18, 0.40, 0.32),
    ("Hrs/Qty / Qté UOM / U.M.", 0.56, 0.68, 0.32), ("Price / Taux Ext. Price / Prix total", 0.72, 0.93, 0.32),
    ("699124", 0.07, 0.12, 0.34), ("Disinfectant 4 L", 0.18, 0.28, 0.34), ("10 ea", 0.60, 0.64, 0.34),
    ("16,92", 0.76, 0.80, 0.34), ("169,20", 0.885, 0.93, 0.34),
    ("Hydraulic hose assembly 1/2 in.", 0.18, 0.39, 0.36), ("2 ea", 0.61, 0.64, 0.36), ("128,82", 0.755, 0.80, 0.36),
    ("257,64", 0.885, 0.93, 0.36),
    ("Net amount / Montant net:", 0.68, 0.85, 0.40), ("426,84", 0.885, 0.93, 0.40),
]  # fmt: skip


def test_a_bilingual_table_is_read():
    """Its headings in two languages, the price and the amount's run into one segment: before, no amount heading was
    found and no line was read."""
    assert _read(_words(*BILINGUAL)) == [("Disinfectant 4 L", 10.0, 16.92, 169.2),
                                         ("Hydraulic hose assembly 1/2 in.", 2.0, 128.82, 257.64)]  # fmt: skip


def test_the_quantity_is_read_under_its_own_heading_not_the_unit_cost():
    """The quantity's heading is "Qty Shipped": "Unit Cost" (read first, "Qty Shipped" printed a hair lower) used to be
    taken for it too."""
    words = _words(
        ("Details", 0.10, 0.15, 0.209), ("Qty Shipped", 0.55, 0.61, 0.2105), ("Unit Cost", 0.71, 0.78, 0.209),
        ("Line Total", 0.825, 0.90, 0.209),
        ("LED panel 2x4 ft, 40W", 0.09, 0.26, 0.23), ("-4", 0.60, 0.61, 0.23), ("76.25", 0.74, 0.78, 0.23),
        ("-305.00", 0.85, 0.906, 0.23),
    )  # fmt: skip
    assert _read(words) == [("LED panel 2x4 ft, 40W", -4.0, 76.25, -305.0)]


def test_a_subtotal_amount_set_above_its_label_is_not_a_line():
    """The subtotal's amount sits a hair higher than its label (a hand-set invoice): it is the table's end, not a line
    with no description."""
    words = _words(
        ("Description", 0.10, 0.18, 0.21), ("Qty", 0.55, 0.58, 0.21), ("Price", 0.72, 0.76, 0.21),
        ("Amount", 0.85, 0.90, 0.21),
        ("LED panel", 0.09, 0.16, 0.23), ("4", 0.60, 0.61, 0.23), ("76.25", 0.74, 0.78, 0.23),
        ("305.00", 0.86, 0.906, 0.23),
        ("305.00", 0.86, 0.906, 0.2615), ("SUBTOTAL", 0.757, 0.82, 0.263),
    )  # fmt: skip
    assert _read(words) == [("LED panel", 4.0, 76.25, 305.0)]


def test_a_returned_quantity_printed_with_its_sign():
    """A credit's line "-4 x 45.13 = -180.52", its quantity printed away from its heading (found by quantity x price
    = amount): the quantity carries the sign, the price is as printed. A "-2" in a part number ("6205-2RS") is no
    quantity."""
    words = _words(
        ("Description", 0.08, 0.16, 0.33), ("Qty", 0.45, 0.48, 0.33), ("Unit Price", 0.73, 0.79, 0.33),
        ("Amount", 0.87, 0.92, 0.33),
        ("Hydraulic hose assembly", 0.08, 0.25, 0.35), ("-4", 0.66, 0.674, 0.35), ("45.13", 0.757, 0.794, 0.35),
        ("-180.52", 0.872, 0.922, 0.35),
        ("Bearing 6205-2RS", 0.08, 0.20, 0.37), ("2", 0.666, 0.674, 0.37), ("20.70", 0.757, 0.794, 0.37),
        ("(41.40)", 0.88, 0.922, 0.37),
    )  # fmt: skip
    items = read_line_items(_layout(words))
    assert [(li.quantity, li.unit_price, li.amount) for li in items] == [(-4.0, 45.13, -180.52), (2.0, -20.7, -41.4)]


def test_a_scanned_credits_amounts_as_ocr_reads_them():
    """OCR's "342.18CR" (the credit's mark run into the amount) and "47:41CR" (the point read as a colon) are amounts
    printed as money: those lines were dropped as sentences under the table. "392.99.CR" is a credit too. With the
    quantity's heading lost, the quantity is the number that times the price makes the amount."""
    words = _words(
        ("Description", 0.19, 0.29, 0.38), ("Unit Price", 0.70, 0.79, 0.38), ("Extended", 0.85, 0.92, 0.38),
        ("Consulting services", 0.19, 0.40, 0.40), ("2", 0.66, 0.67, 0.40), ("171.09", 0.74, 0.79, 0.40),
        ("342.18CR", 0.84, 0.92, 0.40),
        ("Electrician - labour", 0.19, 0.37, 0.42), ("0.50", 0.64, 0.67, 0.42), ("94.81", 0.745, 0.79, 0.42),
        ("47:41CR", 0.846, 0.92, 0.42),
        ("Pallet wrap", 0.19, 0.30, 0.44), ("50", 0.65, 0.67, 0.44), ("20.80", 0.745, 0.79, 0.44),
        ("1040.00 CR", 0.83, 0.92, 0.44),
        ("Janitorial service", 0.19, 0.36, 0.46), ("1", 0.66, 0.67, 0.46), ("392.99", 0.736, 0.79, 0.46),
        ("392.99.CR", 0.836, 0.92, 0.46),
    )  # fmt: skip
    assert _read(words) == [
        ("Consulting services", 2.0, -171.09, -342.18), ("Electrician - labour", 0.5, -94.81, -47.41),
        ("Pallet wrap", 50.0, -20.8, -1040.0), ("Janitorial service", 1.0, -392.99, -392.99)]  # fmt: skip


def test_a_reading_that_ran_into_the_totals_is_cut_at_its_subtotal():
    """OCR ran the totals' labels together ("Totalbefore.tax", "SalesTaxTotal"): read as lines. The first row that
    prints the sum of the rows above it, when they add up to the subtotal, is the subtotal: it and the rows after it
    are no lines. A row with a quantity is a line, and nothing is cut when the lines already add up."""

    def line(desc, amount, qty=None):
        return LineReading(desc, qty, None, amount, [], 0.8)

    lines = [line("Bolts", 100.0, 2), line("Nuts", 50.0, 5), line("Discount", -15.0)]
    read = [*lines, line("Totalbefore.tax", 135.0), line("GST/HST@14%", 18.9), line("SalesTaxTotal", 18.9)]
    assert _to_subtotal(read, [135.0]) == lines
    assert _to_subtotal(read, [999.0]) == read  # the subtotal says otherwise: nothing is cut
    assert _to_subtotal(lines, [135.0]) == lines
    with_qty = [*lines, line("Washers", 135.0, 3)]
    assert _to_subtotal(with_qty, [135.0]) == with_qty


# --- amounts --------------------------------------------------------------------------------------------------------


def test_a_unit_price_to_four_decimals():
    """ "1.3023" (water by the cubic metre) is 1.3023, never 1,302 and a 3: thousands have three digits."""
    assert parse_amount("1.3023") == 1.3023 and parse_amount("1,3023") == 1.3023
    assert [v for v, _a, _b in find_amounts("91 1.3023 118.51")] == [91.0, 1.3023, 118.51]
    assert parse_amount("1,234.567") == 1234.567 and parse_amount("1.459") == 1459.0  # unchanged
    assert parse_amount("1.234.567,89") == 1234567.89
    words = _words(
        ("Item Description", 0.09, 0.21, 0.42), ("Quantity", 0.61, 0.67, 0.42), ("Price Each", 0.715, 0.786, 0.42),
        ("Amount", 0.858, 0.91, 0.42),
        ("Water consumption", 0.09, 0.21, 0.44), ("91", 0.654, 0.669, 0.44), ("1.3023", 0.743, 0.786, 0.44),
        ("118.51", 0.868, 0.91, 0.44),
    )  # fmt: skip
    assert _read(words) == [("Water consumption", 91.0, 1.3023, 118.51)]
