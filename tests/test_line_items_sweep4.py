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
