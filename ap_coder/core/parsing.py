"""Amounts and dates, as printed on a document or typed into a spreadsheet: the one parser of each (D-191).

``parse_amount`` and ``parse_date`` serve every caller; each says how its values come (its rules as parameters):

- printed in a line of a document's text (``within``): the readers (``capture/normalize.py``);
- printed, as one field's whole text: supplier templates (``capture/supplier.py``);
- typed into a spreadsheet cell or a form: the imports (``core/safe.py``, ``core/csvio.py``).

Printed and typed text disagree on what some values mean: "1.234" is 1,234 on a French invoice and 1.234 in a
cell; "03/04/2026" on an invoice is read in the document's order of dates, and refused in an import. So each has
its own grammar here; a field's amount is read by the readers' grammar, the whole text one amount.
"""

from __future__ import annotations

import datetime as dt
import functools
import math
import re
import unicodedata
from collections.abc import Callable
from typing import Any


def parse_amount(value: Any, *, printed: bool = False, within: bool = False) -> float | None:
    """An amount as a float; None when there is none.

    ``printed``: printed on a document and read off it (its text, or OCR's): OCR's slips fixed ("1;864.71",
    "I,396.35"), thousands by commas, points, apostrophes or spaces ("1.234" is 1,234), up to four decimals, a
    credit's minus, parentheses or CR. Else typed into a spreadsheet cell or a form: any decimals, "1.234" is
    1.234, "250.00 DR" a debit, and a number (from openpyxl or JSON) is itself when finite.
    ``within`` (printed only): the first amount in a longer text ("Total due $1,234.56 CAD"); else the whole
    value is one amount or None ("INV-1234", "12/03/2026")."""
    if not printed:
        return _typed_amount(value)
    return _printed_amount(value) if within else _printed_field_amount(value)


def parse_date(text: Any, *, printed: bool = False, within: bool = False, day_first: bool | None = None,
               ) -> dt.date | None:  # fmt: skip
    """A date; None when there is none, or only one that reads two ways and ``day_first`` doesn't choose.

    ``printed``: printed on a document: month names in English, French and (``within``) the other languages
    invoices come in, two-digit years, OCR's slips fixed. Else typed into a spreadsheet or CSV: 2026-01-31,
    2026/01/31, 31/01/2026 or 01/31/2026. ``within`` (printed only): the first date in a longer text; else the
    whole text is one date. ``day_first``: how 03/04/2026 reads (True: 3 April; False: March 4; None: it isn't
    read as either)."""
    if not printed:
        found = _typed_dates(text)
        if len(found) == 1:
            return next(iter(found))
        if day_first is None:
            return None
        return next((d for d, order in found.items() if order == _ORDER[day_first]), None)
    if within:
        for d, _, _, ambiguous in parse_dates(text, prefer_day_first=day_first):
            if not ambiguous:
                return d
        return None
    found_iso = date_candidates(text)
    if not found_iso or day_first is None and len({iso for iso, _ in found_iso}) > 1:
        return None
    order = _ORDER[bool(day_first)]
    return dt.date.fromisoformat(next((iso for iso, o in found_iso if o == order), found_iso[0][0]))


_ORDER = {True: "dmy", False: "mdy"}


# ---------------------------------------------------------------- amounts printed (the readers' grammar)

_CURRENCY_WORDS = re.compile(r"\b(CAD|USD|EUR|GBP|CDN|CA\$|US\$|C\$)\b", re.I)
# A minus sign touches its number or currency sign ("-113.00", "-$113.00", "$-113.00"); a dash with a space
# after it ("Total - $113.00", "Amount Due – $1,234.56") or a run of dashes ("Total ------ 113.00") is a
# separator. Thousands: commas, dots, apostrophes or spaces ("1,234.56", "1.234,56", "1 234,56", "1 234.56",
# "1 234 567"). One space group may also be a quantity set close to its price ("10 100.00" in a line item):
# ``find_amounts`` tells them apart. "0.125" is a unit price with three decimals, never thousands, and "1.3023" one
# with four ("1234.5678" and "12,345.6789" too: four decimals are never thousands). A credit's "CR" may follow a
# stray point on a scan ("392.99.CR"). Cents may be printed without the leading zero ("$.65", "-.50"), never after a
# word or another number ("No.12", "1.234.56").
_AMOUNT = re.compile(
    r"""(?P<neg1>\((?=\s*(?:[$€£]\s*)?\.?\d)|(?<![-−–])[-−–](?=(?:[$€£]\s*)?\.?\d))?
        \s*(?:[$€£]\s*)?
        (?P<num>\d{1,6}[.,]\d{4}(?!\d)(?![.,']\d)
          |(?!0[,.']\d)\d{1,3}(?P<g>[,.'])\d{3}(?:(?P=g)\d{3})*(?!(?P=g))[.,]\d{4}(?!\d)(?![.,']\d)
          |\d{1,3}(?:[   ]\d{3})+[.,]\d{4}(?!\d)(?![.,']\d)
          |(?!0[,.']\d)\d{1,3}(?:[,.']\d{3})+(?:[.,]\d{1,2})?
          |\d{1,3}(?:[ \u00a0\u202f]\d{3})+,\d{1,2}(?!\d)
          |\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:\.\d{1,2})?(?![.,]?\d)
          |0[.,]\d{1,4}
          |(?<![\w.,])\.\d{2}(?![\d.,])
          |\d+(?:[.,]\d{1,2})?)
        \s*(?:[$€£])?\s*(?P<neg2>\)|-(?!\s*\w)|\.?(?i:cr)\b)?""",
    re.X,
)
# One space group ("1 050.00", "1 234"): 1,050.00, or a quantity 1 set close to a price 50.00.
_ONE_SPACE_GROUP = re.compile(r"(\d{1,3})[   ](\d{3}(?:\.\d{1,2}|\.\d{4})?)")
# A header or totals label: an amount after it is one amount ("Total Due 1 050.00", "GST 1 250.00").
_TOTALS_LABEL = re.compile(
    r"\b(?:sub[\s-]?total|sous[\s-]?total|total|amount\s+due|balance\s+due|montant|tax(?:es)?|taxe|gst|hst|pst|"
    r"qst|rst|tps|tvh|tvq)\b",
    re.I,
)


_OCR_THOUSANDS = re.compile(r"(?<=\d)[;:](?=\d{3}(?!\d))")
_OCR_DECIMAL = re.compile(r"(?<=\d)[;:](?=\d{2}(?!\d))")
_OCR_ONE = re.compile(r"(?<![A-Za-z\d])[Il](?=[,.]?\d{3}[.,]\d{2}(?!\d))")
# A speck a scanner left after a number, read as a period: "9,975.%" (a rate), "$442.91.CR" (a credit).
_OCR_SPECK = re.compile(r"(?<=\d)\.(?=\s?(?:%|(?i:cr)\b))")


def _ocr_amount_text(s: str) -> str:
    """OCR slips in amounts, fixed in place (same length, so spans stay valid): the thousands comma or
    decimal point printed as ";" or ":" ("1;864.71", "2447:70"), the digit 1 as "I" or "l" ("I,396.35"), and a
    speck read as a period before a percent sign or "CR" ("9,975.%", "442.91.CR")."""
    s = _OCR_THOUSANDS.sub(",", s)
    s = _OCR_DECIMAL.sub(".", s)
    s = _OCR_SPECK.sub(" ", s)
    return _OCR_ONE.sub("1", s)


def _printed_amount(text: str | None) -> float | None:
    """'1,234.56' / '1 234,56 $' / '$(12.00)' / '12.00 CR' / '-1.234,56' -> float. None if no amount."""
    if text is None:
        return None
    s = _ocr_amount_text(unicodedata.normalize("NFKC", str(text)).strip())
    s = _CURRENCY_WORDS.sub(" ", s)
    m = _AMOUNT.search(s)
    if not m:
        return None
    num = m.group("num")
    neg = bool(m.group("neg1")) or bool(m.group("neg2"))
    if m.group("neg1") == "(" and m.group("neg2") != ")":
        neg = False  # an opening parenthesis without a closing one is punctuation
    value = _number(num)
    if value is None:
        return None
    return -value if neg else value


def _number(num: str) -> float | None:
    num = num.replace(" ", " ").replace(" ", " ").replace("'", " ")
    if re.fullmatch(r"0[.,]\d{3,4}", num):
        return float("0." + num[2:])  # a unit price to three or four decimals ("0.125"), not thousands
    if re.fullmatch(r"\d{1,6}[.,]\d{4}", num):
        return float(re.sub(r"[.,]", ".", num))  # four decimals ("1.3023" a cubic metre): never thousands
    last_sep = max(num.rfind("."), num.rfind(","))
    # The last separator is the decimal point when it has one or two digits after it, or when the separators
    # before it are all the other kind ("1,234.567": a unit price to three decimals, not 1,234,567).
    other = "," if last_sep >= 0 and num[last_sep] == "." else "."
    mixed = last_sep >= 0 and other in num[:last_sep] and num[last_sep] not in num[:last_sep]
    # Four digits after a separator not used before it are decimals ("1 234.5678", "12,345.6789"): never thousands.
    four = last_sep >= 0 and len(num) - last_sep - 1 == 4 and num[last_sep] not in num[:last_sep]
    if last_sep >= 0 and (len(num) - last_sep - 1 in (1, 2) or mixed or four):
        whole, frac = num[:last_sep], num[last_sep + 1 :]
    else:
        whole, frac = num, ""
    whole = re.sub(r"[ ,.]", "", whole) or ("0" if last_sep == 0 and num[0] == "." else "")  # ".65": 0.65
    if not whole.isdigit() or (frac and not frac.isdigit()):
        return None
    return float(f"{whole}.{frac or '0'}")


def find_amounts(text: str) -> list[tuple[float, int, int]]:
    """Every amount in ``text`` with its character span (a sign or parentheses included).

    One space group ("1 050.00") is one amount after a totals label ("Total Due 1 050.00") or when nothing on the
    line says otherwise; it is a quantity and a price when their product is another amount on the line ("Bolts
    3 250.00 750.00"). A whole number in one space group ("10 100") is two numbers unless a totals label is before
    it. A row whose quantity and price stand apart from the amount is split by the line-item reader, which checks
    quantity x price = amount itself."""
    found = []
    # Character by character, so the spans index ``text`` itself: "…" becomes "..." under NFKC, which
    # would move every span after it.
    s = _ocr_amount_text("".join(n if len(n := unicodedata.normalize("NFKC", c)) == 1 else c for c in text))
    for m in _AMOUNT.finditer(s):
        num = m.group("num")
        if not num or not any(c.isdigit() for c in num):
            continue
        value = _number(num)
        if value is None:
            continue
        neg = bool(m.group("neg1") and m.group("neg1") != "(") or bool(m.group("neg2") and m.group("neg2") != ")")
        if m.group("neg1") == "(" and m.group("neg2") == ")":
            neg = True
        found.append((-value if neg else value, m))
    out = []
    for i, (value, m) in enumerate(found):
        pair = _ONE_SPACE_GROUP.fullmatch(m.group("num"))
        if pair and value > 0 and not _TOTALS_LABEL.search(s, 0, m.start()):
            qty, price = float(pair.group(1)), float(pair.group(2))
            others = [abs(v) for j, (v, _) in enumerate(found) if j != i]
            if "." not in pair.group(2) or any(abs(qty * price - v) <= 0.011 for v in others):
                cut = m.start("num") + len(pair.group(1))
                out += [(qty, m.start(), cut), (price, cut + 1, m.end())]
                continue
        out.append((value, m.start(), m.end()))
    return out


# ---------------------------------------------------------------- an amount printed as a field's whole text

_FIELD_WORDS = {"cad", "usd", "eur", "gbp", "ca", "us", "cdn", "dr", "cr"}
# An amount whose thousands are set off by a space ("11 211,29", "$ 1 118.33 CR"): one value, not its last group.
SPACED_THOUSANDS = re.compile(
    r"[-(]?(?:\$\s?)?\d{1,3}(?:\s\d{3})+(?:[.,]\d{1,2})?(?:\s?\$)?(?:\s?(?:cr|dr)\.?)?\)?", re.I
)


def _fold(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _printed_field_amount(text: Any) -> float | None:
    """ "1,234.56", "1 234,56 $", "(12.00)", "$-113.00", "12.00 CR", "0.125" -> float, read as the reader reads it
    (``normalize.parse_amount``); None if the text is not one amount (an id, a date, a word)."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    s = str(text or "").strip()
    if not s or not re.search(r"\d", s):
        return None
    if any(w not in _FIELD_WORDS for w in re.findall(r"[a-z]+", _fold(s))):
        return None  # letters other than a currency code or CR: an id, not an amount
    found = find_amounts(s)
    # "1 234 $", "CAD 1 234": one amount, which find_amounts splits (a quantity and a price)
    spaced = SPACED_THOUSANDS.fullmatch(" ".join(re.sub(r"^[a-z]+\.?|[a-z]+\.?$", "", _fold(s)).split()))
    if not spaced and (len(found) != 1 or re.search(r"\d", s[: found[0][1]] + s[found[0][2] :])):
        return None  # two numbers ("1,23,4.00", a date) or digits beside the amount
    value = _printed_amount(s)
    if value and value > 0 and re.match(r"\s*(?:cr(?![a-z])|[-−–])", _fold(s)):
        return -value  # a credit written first ("CR 12.00", "- 12.00"), which normalize's parser leaves positive
    return value


# ---------------------------------------------------------------- amounts typed (a spreadsheet's cells)

_CURRENCY = re.compile(r"(?i)(ca\$|c\$|us\$|cdn|cad|usd|eur|\$|€|£)")  # "CA$" before "$": no "CA" left over


def _thousands(text: str, sep: str) -> bool:
    """Whether ``text`` is digits in real thousands groups: 1-3 digits (not 0) first, then threes ("1,234,567";
    not "0,125", "1,2345" or "12.50.00")."""
    return re.fullmatch(rf"[1-9]\d{{0,2}}(?:{re.escape(sep)}\d{{3}})+", text) is not None


def _typed_amount(value: Any) -> float | None:
    """A number from a spreadsheet cell as people write it: "1,234.50", "1 234,56 $", "(150.00)",
    "150.00-", "99.00 CR", "250.00 DR", "-$150", "−150.00" (a typeset minus), "CA$1,234.50", "150,00". A credit
    (CR) is negative, a debit (DR) positive, as a register or statement shows them. None when it is not a number
    (or NaN / infinite)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = (
        _CURRENCY.sub("", str(value)).replace(" ", "").replace(" ", "").replace(" ", "").strip().replace("\u2212", "-")
    )
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.upper().endswith("CR"):
        negative, text = True, text[:-2]
    elif text.upper().endswith("DR"):
        text = text[:-2]
    if text.endswith("-"):
        negative, text = True, text[:-1]
    if text.startswith("-"):
        negative, text = True, text[1:]
    elif text.startswith("+"):
        text = text[1:]
    if "," in text and "." in text:  # the last separator is the decimal mark, the other the thousands one
        mark = "," if text.rfind(",") > text.rfind(".") else "."
        whole, _, decimals = text.rpartition(mark)
        group = "." if mark == "," else ","
        if group in whole and not _thousands(whole, group):
            return None  # "12,34.50": not real thousands
        text = f"{whole.replace(group, '')}.{decimals}"
    elif "," in text or "." in text:
        sep = "," if "," in text else "."
        if _thousands(text, sep) and (sep == "," or text.count(".") > 1):  # "1,234", "1,234,567", "1.234.567"
            text = text.replace(sep, "")
        elif text.count(sep) == 1:  # "150,00", "0,125", "1,2345": a decimal comma ("1.5" as it is)
            text = text.replace(",", ".")
        else:  # "12.50.00", "01.02.2026": not a number
            return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return -number if negative else number


# ---------------------------------------------------------------- dates printed (the readers' grammar)

MONTHS = {
    "jan": 1, "january": 1, "janv": 1, "janvier": 1,
    "feb": 2, "february": 2, "fev": 2, "fevr": 2, "fevrier": 2,
    "mar": 3, "march": 3, "mars": 3,
    "apr": 4, "april": 4, "avr": 4, "avril": 4,
    "may": 5, "mai": 5,
    "jun": 6, "june": 6, "juin": 6,
    "jul": 7, "july": 7, "juil": 7, "juillet": 7,
    "aug": 8, "august": 8, "aou": 8, "aout": 8,
    "sep": 9, "sept": 9, "september": 9, "septembre": 9,
    "oct": 10, "october": 10, "octobre": 10,
    "nov": 11, "november": 11, "novembre": 11,
    "dec": 12, "december": 12, "decembre": 12,
}  # fmt: skip
# The months as German, Dutch, Spanish, Italian and Portuguese invoices print them ("24. Januar 2014", "29 maart
# 2014", "10 de febrero de 2022"), accents off. Not their short forms that are English words ("set", "out", "ago").
MONTHS.update({
    "januar": 1, "januari": 1, "enero": 1, "ene": 1, "gennaio": 1, "janeiro": 1,
    "februar": 2, "februari": 2, "febrero": 2, "febbraio": 2, "fevereiro": 2,
    "marz": 3, "maerz": 3, "maart": 3, "mrt": 3, "marzo": 3,
    "abril": 4, "abr": 4, "aprile": 4,
    "mei": 5, "mayo": 5, "maggio": 5, "maio": 5,
    "juni": 6, "junio": 6, "giugno": 6, "junho": 6,
    "juli": 7, "julio": 7, "luglio": 7, "julho": 7,
    "augustus": 8, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "settembre": 9, "setembro": 9,
    "oktober": 10, "okt": 10, "octubre": 10, "ottobre": 10, "outubro": 10,
    "noviembre": 11, "novembro": 11,
    "dezember": 12, "dez": 12, "diciembre": 12, "dic": 12, "dicembre": 12, "dezembro": 12,
})  # fmt: skip


# Letters Unicode does not decompose: dropped with the accents, "Œuvres" became "uvres".
_LIGATURES = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss"})


def _plain(text: str) -> str:
    """Lower case, accents folded to plain letters ("Ltée" -> "ltee", "Cœur" -> "coeur")."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_LIGATURES))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


# A receipt's date and time as OCR runs them together: "6/10/201913:59", "2018-02-1514:33:37", "27Mar201818:46",
# "2/25/125:21PM" (the year 12, then 5:21). The date ends where a time begins.
_TIME_NEXT = r"(?=_?\d{1,2}:\d)"
# ISO 8601's T before the time: "2026-10-20T00:00:00" (an export's date, B-1540).
_ISO = re.compile(rf"\b(20\d{{2}}|19\d{{2}})[-/.](\d{{1,2}})[-/.](\d{{1,2}})(?:\b|{_TIME_NEXT}|(?=t\d{{1,2}}:\d))")
# The year may run into the next label's word in a PDF's text: "Invoice Date: 08-01-2016Contract ID: 1272709".
_NUMERIC = re.compile(
    rf"\b(\d{{1,2}})[-/.](\d{{1,2}})[-/.](\d{{4}}{_TIME_NEXT}|\d{{2}}{_TIME_NEXT}|\d{{2}}\b|\d{{4}}\b|\d{{4}}(?=[a-z]{{3}}))"
)
# Spaces optional: OCR often drops them ("May14,2026", "20Jul2026"); the month name is looked up (three to ten
# letters: "septiembre" has ten, B-1343).
# Between the day and the year, a comma and a speck OCR read as a point: "October 23, . 2026"; a receipt's slashes:
# "Jun14/2017", "Jul/15/2017".
_WORDY_MDY = re.compile(
    rf"\b([a-z]{{3,10}})\.?[\s/-]*(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*[,./]){{0,2}}\s*(\d{{4}})(?:\b|{_TIME_NEXT})"
)
# Spanish and Portuguese put "de" between the parts: "10 de febrero de 2022".
_WORDY_DMY = re.compile(
    r"\b(\d{1,2})(?:er|st|nd|rd|th)?[\s.]*(?:de\s+)?([a-z]{3,10})(?:\s*[,.]){0,2}\s*(?:del?\s+)?(\d{4})(?:\b|"
    + _TIME_NEXT
    + ")"
)
# OCR reads letters of a month as digits ("0ct", "Ju1", "N0v"); fixed in place, same length.
_OCR_MONTHS = [
    (re.compile(r"0ct"), "oct"),
    (re.compile(r"ju1"), "jul"),
    (re.compile(r"n0v"), "nov"),
    (re.compile(r"(?<![a-z])1an"), "jan"),
    (re.compile(r"ap1"), "apr"),
    (re.compile(r"apri1"), "april"),
    (re.compile(r"ju1y"), "july"),
    (re.compile(r"(?<![a-z])eeb(?=[\s.,]*\d)"), "feb"),  # OCR's E for F: "15Eeb.2025"
    # French "1er" (the first) as OCR reads it, "ler" or "ier": "ler octobre 2026" (same length, so offsets hold)
    (re.compile(r"(?<![a-z\d])[li]er(?=\s+(?:janv|fevr|mars|avr|mai|juin|juil|aout|sept|oct|nov|dec))"), "1er"),
    # "le 9 septembre 2025" as OCR glues it: "le9 septembre" (same length: the e becomes the space)
    (re.compile(r"(?<![a-z])le(?=\d{1,2}\s*[a-z]{3})"), "l "),
]


def _ocr_date_text(s: str) -> str:
    for rx, fixed in _OCR_MONTHS:
        s = rx.sub(fixed, s)
    # a speck read as a point inside the day: "0.1/08/2026" (same length, so offsets hold)
    s = _SPECK_IN_DAY.sub(r" \1\2", s)
    # ... or inside the year after a month: "28 Apr 2.025"
    s = _SPECK_IN_YEAR.sub(r"\1\2\3 ", s)
    # ... or beside a separator: "2025/12/.07" -> "2025/12/07 "
    return _SPECK_BY_SEP.sub(lambda m: re.sub(r"[.,]", "", m.group()) + " " * len(re.findall(r"[.,]", m.group())), s)


_SPECK_IN_YEAR = re.compile(r"([a-z]{3,9}\.?,?\s*)(2)[.,](0\d{2})\b")
_SPECK_IN_DAY = re.compile(r"(?<![\d.,])(\d)[.,](\d)(?=[/-]\d{1,2}[/-]\d{2,4}\b)")
_SPECK_BY_SEP = re.compile(
    r"(?<![\d.,])\d{1,4}(?=[.,]?[/-][.,]?\d)(?:[.,][/-]|[/-][.,]|[/-])\d{1,2}(?:[.,][/-]|[/-][.,]|[/-])\d{2,4}\b"
)


_WORDY_DMY_DASH = re.compile(r"\b(\d{1,2})[-\s]([a-z]{3,10})[-\s](\d{2,4})\b")
# Compact forms: "Oct-15-26" (the month, day and year by dashes) and "15OCT26" (all run together, a two-digit year).
# Not inside a number ("INV-12MAR26", "PO-OCT-15-26").
_WORDY_MDY_DASH = re.compile(r"(?<![\w/-])([a-z]{3,10})\.?-(\d{1,2})-(\d{2}|\d{4})(?![\w/-])")
_GLUED_DMY = re.compile(r"(?<![\w/-])(\d{1,2})([a-z]{3,4})(\d{2})(?![\w/-])")


def _make(y: int, m: int, d: int) -> dt.date | None:
    if y < 100:
        y += 2000
    if not 1900 <= y <= 2099:
        return None  # no invoice is dated 1011 ("02/15/1011:10AM" is the year 10 run into the time 11:10)
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def _folded(text: str, fold: Callable[[str], str]) -> tuple[str, list[tuple[int, int]] | None]:
    """``fold(text)`` and, for each of its characters, the span of ``text`` it comes from: "…", "™", "œ" and "ß" are
    one character printed and several folded, so a match's place in the folded text is not its place in ``text``
    (B-1340). Folded a character at a time (with the accents after it); None for plain ASCII, where nothing moves."""
    if text.isascii() or all(_one_for_one(c, fold) for c in text if not c.isascii()):
        return fold(text), None
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(text[j]):
            j += 1
        piece = fold(text[i:j])
        out.append(piece)
        spans += [(i, j)] * len(piece)
        i = j
    return "".join(out), spans


@functools.lru_cache(maxsize=4096)
def _one_for_one(char: str, fold: Callable[[str], str]) -> bool:
    """The character folds to one character, and no accent folds into the one before it."""
    return len(fold(char)) == 1 and not unicodedata.combining(char)


def _unfolded(spans: list[tuple[int, int]] | None, a: int, b: int) -> tuple[int, int]:
    """A match's span (never empty) in the folded text (``_folded``) as a span of the text itself."""
    return (a, b) if spans is None else (spans[a][0], spans[b - 1][1])


def _date_fold(text: str) -> str:
    return _plain(unicodedata.normalize("NFKC", text))


def parse_dates(text: str, *, prefer_day_first: bool | None = None) -> list[tuple[dt.date, int, int, bool]]:
    """Every date in ``text``: (date, start, end, ambiguous), start and end where it is printed in ``text``.
    ``ambiguous`` is True for 03/04/2026 when both day-first and month-first are valid and no preference resolves
    it."""
    folded, spans = _folded(text, _date_fold)
    # A soft hyphen printed as the date's separator: "10\u00ad31\u00ad2016" (same length, so offsets hold).
    s = _ocr_date_text(folded.replace("\u00ad", "-"))
    found: list[tuple[dt.date, int, int, bool]] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    for m in _ISO.finditer(s):
        d = _make(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((d, m.start(), m.end(), False))
            taken.append(m.span())
    for rx, order in ((_WORDY_MDY, "mdy"), (_WORDY_DMY, "dmy"), (_WORDY_DMY_DASH, "dmy"), (_WORDY_MDY_DASH, "mdy"),
                      (_GLUED_DMY, "dmy")):  # fmt: skip
        for m in rx.finditer(s):
            if not free(*m.span()):
                continue
            if order == "mdy":
                mon, day, year = MONTHS.get(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                day, mon, year = int(m.group(1)), MONTHS.get(m.group(2)), int(m.group(3))
                if m.group(2) == "marco" and " de " in m.group(0):
                    mon = 3  # Portuguese "12 de março de 2020"; "Marco 12, 2026" is a name, not March
            if not mon:
                continue
            d = _make(year, mon, day)
            if d:
                found.append((d, m.start(), m.end(), False))
                taken.append(m.span())
    pos = 0
    while m := _NUMERIC.search(s, pos):
        pos = m.end()
        if not free(*m.span()):
            continue
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        dmy, mdy = _make(y, b, a), _make(y, a, b)
        if dmy and mdy and dmy != mdy:
            if prefer_day_first is None:
                found.append((dmy, m.start(), m.end(), True))
            else:
                found.append((dmy if prefer_day_first else mdy, m.start(), m.end(), False))
        elif dmy or mdy:
            found.append((dmy or mdy, m.start(), m.end(), False))  # type: ignore[arg-type]
        else:
            # No date: "45/13/12/2026" in a reference is no date either. A date may start at the next word only.
            nxt = re.search(r"\s\S", s[m.start() : m.end()])
            pos = m.start() + nxt.end() - 1 if nxt else m.end()
            continue
        taken.append(m.span())
    return sorted(((d, *_unfolded(spans, a, b), ambiguous) for d, a, b, ambiguous in found), key=lambda x: x[1])


def infer_day_first(text: str) -> bool | None:
    """The document's numeric date order, from its unambiguous dates: 25/03/2026 means day first,
    03/25/2026 month first. None when nothing tells (or the document mixes both)."""
    s = _plain(unicodedata.normalize("NFKC", text)).replace("\u00ad", "-")
    day = month = 0
    for m in _NUMERIC.finditer(s):
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and 1 <= b <= 12:
            day += 1
        elif b > 12 and 1 <= a <= 12:
            month += 1
    if day and not month:
        return True
    if month and not day:
        return False
    return None


# ---------------------------------------------------------------- a date printed as a field's whole text

_FIELD_MONTHS = {
    "jan": 1, "janv": 1, "january": 1, "janvier": 1, "feb": 2, "fev": 2, "fevr": 2, "february": 2, "fevrier": 2,
    "mar": 3, "march": 3, "mars": 3, "apr": 4, "avr": 4, "april": 4, "avril": 4, "may": 5, "mai": 5,
    "jun": 6, "june": 6, "juin": 6, "jul": 7, "july": 7, "juil": 7, "juillet": 7, "aug": 8, "august": 8, "aou": 8,
    "aout": 8, "sep": 9, "sept": 9, "september": 9, "septembre": 9, "oct": 10, "october": 10, "octobre": 10,
    "nov": 11, "november": 11, "novembre": 11, "dec": 12, "december": 12, "decembre": 12,
}  # fmt: skip


def _iso(y: int, m: int, d: int) -> str | None:
    if y < 100:
        y += 2000
    try:
        return dt.date(y, m, d).isoformat()
    except ValueError:
        return None


def date_candidates(text: Any) -> list[tuple[str, str]]:
    """Every reading of a printed date as (ISO date, order) with order "ymd", "dmy", "mdy" or "text"."""
    s = _fold(text).strip()
    m = re.fullmatch(r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", s)
    if m:
        iso = _iso(int(m[1]), int(m[2]), int(m[3]))
        return [(iso, "ymd")] if iso else []
    m = re.fullmatch(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{2}|\d{4})", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        out = [(iso, order) for iso, order in ((_iso(y, b, a), "dmy"), (_iso(y, a, b), "mdy")) if iso]
        return out[:1] if a == b else out
    words = re.findall(r"[a-z]+|\d+", s)
    if len(words) > 6:
        return []
    month = next((_FIELD_MONTHS[w] for w in words if w in _FIELD_MONTHS), None)
    numbers = [w for w in words if w.isdigit()]
    others = [
        w for w in words if not w.isdigit() and w not in _FIELD_MONTHS and w not in {"er", "st", "nd", "rd", "th", "le"}
    ]
    if month is None or others or len(numbers) != 2:
        return []
    # Printed in a date's order: month day year, day month year, or year month day. Not the end of one date and the
    # start of the next printed beside it ("10, 2025 October", "2025 March 15,").
    at = {w: i for i, w in enumerate(words)}
    month_at = next(i for i, w in enumerate(words) if w in _FIELD_MONTHS)
    if len(numbers[0]) == 4 and (at[numbers[0]] > month_at or s.endswith(",")):
        return []
    if len(numbers[-1]) == 4 and at[numbers[-1]] < month_at:
        return []
    year = next((int(n) for n in numbers if len(n) == 4), None)
    day = next((int(n) for n in numbers if len(n) <= 2), None)
    if year is None and words[-1] == numbers[-1] and len(numbers[-1]) == 2 and len(numbers[0]) <= 2:
        day, year = int(numbers[0]), int(numbers[-1])  # "15OCT26", "Oct-15-26": a two-digit year last
    iso = _iso(year, month, day) if year and day else None
    return [(iso, "text")] if iso else []


# ---------------------------------------------------------------- dates typed (a CSV's cells)


def _typed_dates(text: Any) -> dict[dt.date, str]:
    """Each reading of a date typed in a CSV and its order ("ymd", "dmy", "mdy"): one for 2026-01-31 or 31/01/2026,
    two for 03/04/2026."""
    text = (text or "").strip()
    try:
        return {dt.date.fromisoformat(text): "ymd"}
    except ValueError:
        pass
    found: dict[dt.date, str] = {}
    for fmt, order in (("%Y/%m/%d", "ymd"), ("%d/%m/%Y", "dmy"), ("%m/%d/%Y", "mdy"), ("%d-%m-%Y", "dmy"),
                       ("%m-%d-%Y", "mdy")):  # fmt: skip
        try:
            found.setdefault(dt.datetime.strptime(text, fmt).date(), order)
        except ValueError:
            continue
    return found
