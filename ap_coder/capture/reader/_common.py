# ruff: noqa: E501  (label and pattern tables read best one per line)
"""What every part of the rule reader shares: words, number shapes and small helpers."""

from __future__ import annotations

import functools
import re
import unicodedata

from ..types import (
    TAKEN_OFF,
    Line,
)


def plain(text: str) -> str:
    """Lowercase, accents off, OCR's "·" a space. Called thousands of times per invoice on the same few hundred lines:
    plain ASCII (most of an invoice) is only lowercased, the rest remembered."""
    if text.isascii():
        return text.lower()
    return _plain(text)


@functools.lru_cache(maxsize=16384)
def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text).replace("·", " ")  # OCR reads a space as "·"
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


# Words spelled as printed: a PDF's text layer, and a page reader's transcription (it writes words apart and
# whole). OCR's slips (a label glued to its value, O read as 0) are undone only in the other words.
_AS_PRINTED = ("text", "vlm")

# A field's label: "Order No.", "Quotation No.", "Delivery Date", "Numero de compte"; a table header.
_LABEL_SHAPE = re.compile(
    r"^(?:[a-z.'/]+\s*){1,3}(?:no\.?|n°|#|date|id|number)\s*:?$|^(?:numero|no|n°)\s+d[e']|"
    r"\b(?:description|designation|qty|quantite|quantity)\b"
)

_TAX_FIELDS = ("gst_amount", "hst_amount", "pst_amount", "qst_amount")

_TAX_BASE = re.compile(r"(?i)\b(?:on|sur)\s*\$?\s*$")  # "13% on $1,000.00", "5 % sur 1 000,00 $"

_HEAD = {
    "description": re.compile(r"^(description|desc\.?|item|article|details|designation|produit|service|libelle|particulars)"),
    "quantity": re.compile(r"^(qty|quantity|qte|quantite|qte\.|units?|hrs?|hours|heures)\b"),
    "unit_price": re.compile(r"^(unit\s*price|price|rate|unit\s*cost|net\s*price|prix(\s*unit(?:aire|\.)?)?|taux|p\.?u\.?|cost)(?![a-z0-9])"),
    "amount": re.compile(r"^(amount|total(?:\s*price)?|montant|line\s*total|ext(?:ended)?\.?\s*(?:price|amount)?|extension|net\s*(?:worth|amount|value)|value|valeur|prix\s*total|fees|honoraires|frais)\b"),
}  # fmt: skip

# A state and ZIP code after a city, a comma or at the start of a line ("Seattle, WA 98101", "Boise ID 83702", "WA
# 98101"); not after a label: "Order ID 77812", "Store IN 46204" are a label's numbers, not Idaho or Indiana.
_ID_LABEL_WORDS = (
    "order|vendor|supplier|store|job|ticket|customer|cust|client|account|acct|member|employee|tax|ref|reference|batch|"
    "lot|work|purchase|transaction|tracking|user|invoice|inv|document|doc|file|case|project|contract|site|location|"
    "unit|item|product|part|serial|payment|po|no|number|box|route|shipment|delivery|quote|receipt|confirmation"
)

_US_ADDRESS = re.compile(
    r"(?:^\s*|,\s*|(?<![A-Za-z])(?!(?i:" + _ID_LABEL_WORDS + r")\s)[A-Za-z][A-Za-z.'-]*\s+)"
    r"(?:A[LKZR]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])"
    r"\s+\d{5}(?:-\d{4})?\b",
    re.M,
)


def us_address(text: str) -> bool:
    """``text`` holds a US address's state and ZIP code (``offline_coder.provinces`` reads the supplier's with it)."""
    return bool(_US_ADDRESS.search(text))


PREPAID = TAKEN_OFF  # a payment made before the invoice, taken off its total


def _table_header_line(line: Line, page_lines: list[Line]) -> bool:
    """The line is a column heading of a line-item table (two or more other headings beside it)."""
    others = 0
    for other in page_lines:
        if other is not line and abs(other.box.cy - line.box.cy) < 0.006:
            p = plain(other.text).strip()
            if any(rx.match(p) for rx in _HEAD.values()):
                others += 1
    return others >= 2


def plain_with_offsets(text: str) -> tuple[str, list[int]]:
    """``plain(text)`` and, for each of its characters, where it comes from in ``text`` (one more for the end): "½",
    "™" and "…" are one character in ``text`` and several in ``plain(text)``."""
    out, index = [], []
    for i, ch in enumerate(text):
        p = plain(ch)
        out.append(p)
        index += [i] * len(p)
    return "".join(out), [*index, len(text)]
