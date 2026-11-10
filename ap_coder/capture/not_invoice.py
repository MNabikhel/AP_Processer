"""A document that is not an invoice (a statement of account, a remittance advice, a quote, a packing slip, an order
confirmation, a notice): said from plain evidence on the page, for a person to reject (B-2217). Never a rejection of
its own, and silent on an invoice: a title counts only standing alone at the top of the first page, above any title
that says invoice.

``not_an_invoice(layout, fields, items)`` gives the reason in words, or "".
"""

from __future__ import annotations

import re
from typing import Any

from .reader._common import plain
from .types import MISSING, DocLayout, Line, PageLayout

# What a document calls itself, and how the finding names it. Not "Statement" alone: a utility's or a telephone
# company's bill is titled so, and is an invoice.
TITLES = {
    "statement of account": "Statement of account", "customer statement": "Customer statement",
    "releve de compte": "Relevé de compte", "remittance advice": "Remittance advice",
    "payment advice": "Payment advice",
    "avis de paiement": "Avis de paiement", "quote": "Quote", "quotation": "Quotation", "estimate": "Estimate",
    "soumission": "Soumission", "packing slip": "Packing slip", "packing list": "Packing list",
    "delivery note": "Delivery note", "bon de livraison": "Bon de livraison",
    "order confirmation": "Order confirmation", "order acknowledgement": "Order acknowledgement",
    "order acknowledgment": "Order acknowledgment", "sales order confirmation": "Sales order confirmation",
    "confirmation de commande": "Confirmation de commande", "staff notice": "Staff notice", "notice": "Notice",
    "memo": "Memo", "memorandum": "Memorandum",
}  # fmt: skip
# A title that says it is one after all (a credit note is an invoice here: ``credit_titled``).
_INVOICE = re.compile(r"\b(invoice|facture|credit (note|memo)|note de credit|avoir|debit (note|memo)|bill)\b")
TOP = 0.4  # a title is in this part of the first page
GAP = 0.03  # words farther apart than this (of the page's width) are two pieces of a row
_SEPARATORS = {"-", "–", "—", "|", "·", ":"}
_AMOUNT_AT_END = re.compile(r"\(?-?\$?\d[\d,. ]*[.,]\d{2}\)?(\s*(cr|cad|usd))?$")
_LISTED = re.compile(r"\b(balance|outstanding|paid|payment|solde)\b")
SEVERAL = 2  # rows under such a heading
LEVEL = 0.006  # words whose middles are this close (of the page's height) are on one row


def _pieces(line: Line) -> list[str]:
    """A row's pieces, each read on its own: words set apart by a wide gap or a dash are not one phrase."""
    pieces: list[list[str]] = [[]]
    last = None
    for word in line.words:
        if word.text in _SEPARATORS or (last is not None and word.box.x0 - last.box.x1 > GAP):
            pieces.append([])
        if word.text not in _SEPARATORS:
            pieces[-1].append(word.text)
        last = word
    return [plain(" ".join(piece)).strip(" .:*-") for piece in pieces if piece]


def _title(layout: DocLayout) -> str:
    """What the first page's title calls the document when that is not an invoice ("" otherwise): a piece of a row
    that is one of ``TITLES`` and nothing else, in the top of the page, with no title saying invoice above it."""
    first = layout.pages[0]
    found = [(line.box.cy, TITLES[piece]) for line in first.lines if line.box.cy < TOP for piece in _pieces(line)
             if piece in TITLES]  # fmt: skip
    if not found:
        return ""
    at, title = min(found)
    said = any(_INVOICE.search(piece) for line in first.lines if line.box.cy <= at + 0.01 for piece in _pieces(line))
    return "" if said else title


def _rows(page: PageLayout) -> list[str]:
    """The page's rows of words, top to bottom, each read left to right."""
    rows: list[list[Any]] = []
    for word in sorted(page.words, key=lambda w: w.box.cy):
        if rows and abs(word.box.cy - rows[-1][-1].box.cy) <= LEVEL:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [" ".join(w.text for w in sorted(row, key=lambda w: w.box.x0)) for row in rows]


def _listed(layout: DocLayout) -> int:
    """How many rows stand under a heading that names invoices beside their balances or payments (0: none, or one
    row, as under an invoice's own boxes): a statement's or a remittance advice's table."""
    most = 0
    for page in layout.pages:
        rows = 0
        counting = False
        for row in _rows(page):
            text = plain(row).strip()
            if re.search(r"\b(invoice|facture)\b", text) and _LISTED.search(text) and not _AMOUNT_AT_END.search(text):
                counting, rows = True, 0
            elif counting and _AMOUNT_AT_END.search(text):
                rows += 1
                most = max(most, rows)
            else:
                counting = False
    return most if most >= SEVERAL else 0


def not_an_invoice(layout: DocLayout, fields: dict[str, Any], items: list[Any]) -> str:
    """Why this may not be an invoice, in words a person can check on the page; "" for an invoice. ``fields`` and
    ``items``: what was read off it (``FieldResult`` by name, the line items)."""
    if not layout.pages or not any(page.words for page in layout.pages):
        return ""
    title = _title(layout)
    if title:
        return f'its title is "{title}"'
    rows = _listed(layout)
    if rows:
        return f"it lists {rows} invoices with their balances or payments, as a statement or a remittance advice does"

    def read(name: str) -> Any:
        found = fields.get(name)
        return None if found is None or found.status == MISSING else found.value

    if not read("invoice_number") and not any(read(name) for name in ("subtotal", "grand_total")) and not any(
            item.amount for item in items):  # fmt: skip
        return "no invoice number and no amounts were found on it"
    return ""
