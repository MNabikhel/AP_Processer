"""Line items on real-world and hard invoices (sweep 4): page subtotals and balances carried from page to page, and
the other rows a line table prints that are no lines. Small generated pages: one table each."""

from ap_coder.capture import layout
from ap_coder.capture.reader import read_line_items
from ap_coder.capture.types import Box, DocLayout, PageLayout, Word


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
