"""Turn printed text into comparable values: amounts, dates, identifiers, registration numbers.

Every reader normalizes with these functions, so two readers that saw the same printed value always
produce the same normalized value, and agreement can be tested with ``==``.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

# ---------------------------------------------------------------- amounts

_CURRENCY_WORDS = re.compile(r"\b(CAD|USD|EUR|GBP|CDN|CA\$|US\$|C\$)\b", re.I)
# A minus sign touches its number or currency sign ("-113.00", "-$113.00", "$-113.00"); a dash with a space
# after it ("Total - $113.00", "Amount Due – $1,234.56") or a run of dashes ("Total ------ 113.00") is a
# separator. Thousands: commas, dots, apostrophes or spaces ("1,234.56", "1.234,56", "1 234,56", "1 234.56",
# "1 234 567"). One space group may also be a quantity set close to its price ("10 100.00" in a line item):
# ``find_amounts`` tells them apart. "0.125" is a unit price with three decimals, never thousands, and "1.3023" one
# with four ("1234.5678" and "12,345.6789" too: four decimals are never thousands). A credit's "CR" may follow a
# stray point on a scan ("392.99.CR").
_AMOUNT = re.compile(
    r"""(?P<neg1>\((?=\s*(?:[$€£]\s*)?\d)|(?<![-−–])[-−–](?=(?:[$€£]\s*)?\d))?
        \s*(?:[$€£]\s*)?
        (?P<num>\d{1,6}[.,]\d{4}(?!\d)(?![.,']\d)
          |(?!0[,.']\d)\d{1,3}(?P<g>[,.'])\d{3}(?:(?P=g)\d{3})*(?!(?P=g))[.,]\d{4}(?!\d)(?![.,']\d)
          |\d{1,3}(?:[   ]\d{3})+[.,]\d{4}(?!\d)(?![.,']\d)
          |(?!0[,.']\d)\d{1,3}(?:[,.']\d{3})+(?:[.,]\d{1,2})?
          |\d{1,3}(?:[ \u00a0\u202f]\d{3})+,\d{1,2}(?!\d)
          |\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:\.\d{1,2})?(?![.,]?\d)
          |0[.,]\d{1,4}
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


def parse_amount(text: str | None) -> float | None:
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
    whole = re.sub(r"[ ,.]", "", whole)
    if not whole.isdigit() or (frac and not frac.isdigit()):
        return None
    return float(f"{whole}.{frac or '0'}")


def amounts_equal(a: float | None, b: float | None, tol: float = 0.005) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


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


def looks_like_money(text: str) -> bool:
    """A token printed like money: two decimals, or a currency sign."""
    s = text.strip()
    return bool(re.search(r"\d[.,]\d{2}\b", s)) or bool(re.search(r"[$€£]\s*\d", s))


# ---------------------------------------------------------------- dates

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
    "marz": 3, "maerz": 3, "maart": 3, "mrt": 3, "marzo": 3, "marco": 3,
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
_ISO = re.compile(rf"\b(20\d{{2}}|19\d{{2}})[-/.](\d{{1,2}})[-/.](\d{{1,2}})(?:\b|{_TIME_NEXT})")
_NUMERIC = re.compile(
    rf"\b(\d{{1,2}})[-/.](\d{{1,2}})[-/.](\d{{4}}{_TIME_NEXT}|\d{{2}}{_TIME_NEXT}|\d{{2}}\b|\d{{4}}\b)"
)
# Spaces optional: OCR often drops them ("May14,2026", "20Jul2026"); the month name is looked up.
# Between the day and the year, a comma and a speck OCR read as a point: "October 23, . 2026"; a receipt's slashes:
# "Jun14/2017", "Jul/15/2017".
_WORDY_MDY = re.compile(
    rf"\b([a-z]{{3,9}})\.?[\s/-]*(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*[,./]){{0,2}}\s*(\d{{4}})(?:\b|{_TIME_NEXT})"
)
# Spanish and Portuguese put "de" between the parts: "10 de febrero de 2022".
_WORDY_DMY = re.compile(
    r"\b(\d{1,2})(?:er|st|nd|rd|th)?[\s.]*(?:de\s+)?([a-z]{3,9})(?:\s*[,.]){0,2}\s*(?:del?\s+)?(\d{4})(?:\b|"
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


_WORDY_DMY_DASH = re.compile(r"\b(\d{1,2})[-\s]([a-z]{3,9})[-\s](\d{2,4})\b")
# Compact forms: "Oct-15-26" (the month, day and year by dashes) and "15OCT26" (all run together, a two-digit year).
# Not inside a number ("INV-12MAR26", "PO-OCT-15-26").
_WORDY_MDY_DASH = re.compile(r"(?<![\w/-])([a-z]{3,9})\.?-(\d{1,2})-(\d{2}|\d{4})(?![\w/-])")
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


def parse_dates(text: str, *, prefer_day_first: bool | None = None) -> list[tuple[dt.date, int, int, bool]]:
    """Every date in ``text``: (date, start, end, ambiguous). ``ambiguous`` is True for 03/04/2026
    when both day-first and month-first are valid and no preference resolves it."""
    s = _ocr_date_text(_plain(unicodedata.normalize("NFKC", text)))
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
            # No date: "REG#19-21/05/2018" read "19-21/05" first. The date may start at the next number.
            pos = m.start() + 1
            continue
        taken.append(m.span())
    return sorted(found, key=lambda x: x[1])


def infer_day_first(text: str) -> bool | None:
    """The document's numeric date order, from its unambiguous dates: 25/03/2026 means day first,
    03/25/2026 month first. None when nothing tells (or the document mixes both)."""
    s = _plain(unicodedata.normalize("NFKC", text))
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


def parse_date(text: str, *, prefer_day_first: bool | None = None) -> str | None:
    """The first date in ``text`` as YYYY-MM-DD (None if none, or only an unresolved ambiguous one)."""
    for d, _, _, ambiguous in parse_dates(text, prefer_day_first=prefer_day_first):
        if not ambiguous:
            return d.isoformat()
    return None


# ---------------------------------------------------------------- identifiers


def norm_id(text: str | None) -> str:
    """Invoice/PO numbers compared without case, spaces, dashes, dots, slashes or '#'."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).upper()
    return re.sub(r"[\s\-_./#:]", "", s)


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# The nine digits may be grouped by spaces or dashes, or a period where a scan left a speck ("57165.3898 RT 0001").
_BN = re.compile(r"(?<![\d-])(\d(?:[ .-]?\d){8})[\s.:-]*(RT|R\s?T)[\s.:-]*(\d{4})(?!\d)", re.I)
_BN9 = re.compile(r"(?<!\d)(\d{3}\s?\d{3}\s?\d{3})(?!\d)")
_QST = re.compile(r"(?<!\d)(\d{10})[\s.:-]*(TQ|T\s?Q)[\s.:-]*(\d{4})(?!\d)", re.I)


def find_gst_numbers(text: str) -> list[tuple[str, int, int, bool]]:
    """GST/HST registration numbers: ('123456782RT0001', start, end, luhn_valid)."""
    s = unicodedata.normalize("NFKC", text)
    out = []
    for m in _BN.finditer(s):
        digits = re.sub(r"[\s.-]", "", m.group(1))
        out.append((f"{digits}RT{m.group(3)}", m.start(), m.end(), luhn_ok(digits)))
    return out


def find_qst_numbers(text: str) -> list[tuple[str, int, int]]:
    s = unicodedata.normalize("NFKC", text)
    return [(f"{m.group(1)}TQ{m.group(3)}", m.start(), m.end()) for m in _QST.finditer(s)]


# A provincial sales tax number (BC "PST-1234-5678", Manitoba "RST 123456-7", Saskatchewan "PST 1234567") has no
# fixed shape, so it is read only after its own label: "PST No.", "RST #", "PST Reg.", "TVP". The rate line
# ("PST 7%  190.82") is not a number: no percent, decimals or thousands separators.
_PST = re.compile(
    r"(?<![A-Za-z])(?:P\.?S\.?T\.?|R\.?S\.?T\.?|TVP)(?![A-Za-z])[\s:]*(?:(?:reg(?:istration)?|no|nr|number|num[ée]ro|"
    r"n°|#)\.?[\s:#]*)*((?:PST|RST)[\s-]?)?(\d[\d-]{4,13}\d)(?![\d.,%])",
    re.I,
)


def find_pst_numbers(text: str) -> list[tuple[str, int, int]]:
    """Provincial sales tax numbers after their label: ('PST-1234-5678', start, end) with the number as printed
    (its own "PST-" prefix kept); start..end covers the number, not the label."""
    s = unicodedata.normalize("NFKC", text)
    out = []
    for m in _PST.finditer(s):
        if sum(c.isdigit() for c in m.group(2)) < 6:
            continue
        a = m.start(1) if m.group(1) else m.start(2)
        out.append((s[a : m.end()].strip(), a, m.end()))
    return out


def norm_pst(text: str | None) -> str:
    """A PST number compared by its digits: "PST-1234-5678", "1234-5678" and "PST 12345678" are the same."""
    return re.sub(r"\D", "", str(text or ""))


def norm_gst(text: str | None) -> str:
    """'123 456 782 RT 0001' -> '123456782RT0001'; a bare 9-digit BN -> '123456782'."""
    if not text:
        return ""
    found = find_gst_numbers(str(text))
    if found:
        return found[0][0]
    m = _BN9.search(str(text))
    return re.sub(r"\s", "", m.group(1)) if m else norm_id(text)


def norm_qst(text: str | None) -> str:
    if not text:
        return ""
    found = find_qst_numbers(str(text))
    return found[0][0] if found else norm_id(text)


def gst_valid(value: str | None) -> bool:
    v = norm_gst(value)
    return bool(re.fullmatch(r"\d{9}RT\d{4}", v)) and luhn_ok(v[:9])


def qst_valid(value: str | None) -> bool:
    return bool(re.fullmatch(r"\d{10}TQ\d{4}", norm_qst(value)))


# ---------------------------------------------------------------- names and currency

_LEGAL_SUFFIX = re.compile(
    r"\b(inc|incorporated|ltd|ltee|limited|limitee|llc|llp|lp|corp|corporation|co|company|cie|"
    r"enr|senc|sa|plc|gmbh|ulc)\b\.?",
    re.I,
)


def norm_name(text: str | None) -> str:
    """Vendor names compared without accents, case, punctuation or legal suffixes."""
    if not text:
        return ""
    s = _plain(str(text))
    s = re.sub(r"[&+]", " and ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = _LEGAL_SUFFIX.sub(" ", s)
    return " ".join(s.split())


CURRENCIES = ("CAD", "USD", "EUR", "GBP")


_CODE = r"(?<![A-Z])(CAD|USD|EUR|GBP)(?![A-Z])"  # also glued by OCR: "TOTALCAD"
# "All amounts in USD", "Prices are quoted in CAD", "Currency: USD", "Montants en dollars CAD", "Devise : CAD".
_CURRENCY_STATEMENT = re.compile(
    r"(?:AMOUNTS?|PRICES?|FIGURES|MONTANTS?|PRIX)\s+(?:ARE\s+|SONT\s+)?(?:(?:STATED|SHOWN|EXPRESSED|QUOTED|BILLED|"
    r"PAYABLE|EXPRIM[EÉ]S?|INDIQU[EÉ]S?|PAYABLES?)\s+)?(?:IN|EN)\s+(?:DOLLARS\s+)?" + _CODE
    + r"|(?:CURRENCY|DEVISE|MONNAIE)\s*[:=]?\s*" + _CODE
)  # fmt: skip
# The same said in words: "All prices in Canadian dollars", "Amounts in US dollars", "Montants en dollars canadiens".
_WORDS_STATEMENT = re.compile(
    r"(?:IN|EN)\s+(?:(?P<cad>CANADIAN)|(?P<usd>U\.?S\.?|AMERICAN))\s+DOLLARS"
    r"|DOLLARS\s+(?:(?P<cad2>CANADIENS)|(?P<usd2>AM[EÉ]RICAINS|US)\b)"
)
# A line on how the supplier may be paid ("Payments in USD accepted", "Wire: ..., USD account 4455"): a code on it
# is not the currency the invoice is billed in.
_PAYMENT_NOTE = re.compile(r"ACCEPT|PAYMENTS?\s+(?:IN|EN)\b|PAIEMENTS?\s+EN\b|REMIT|\bWIRE|VIREMENT|ACCOUNT|COMPTE")
# A code printed on an amount: "CAD 1,050.00", "$1,050.00 USD", "TOTALCAD1,050.00".
_CODE_ON_AMOUNT = re.compile(_CODE + r"\s*\$?\s*\d|\d[.,]\d{2}\s*\$?\s*" + _CODE)
_EXCHANGE = re.compile(r"EXCHANGE|\bRATE\b|\bTAUX\b|CONVERSION|[EÉ]QUIVALEN|=")
# The label of the invoice's total, printed with its code ("Total USD", "Amount Due (CAD)", "Montant dû CAD"): the
# code is on the total's amount, though the layout may read the label and the amount in two columns.
_TOTAL_LABEL = re.compile(
    r"^\W*(?:GRAND\s+|INVOICE\s+)?TOTAL\b|AMOUNT\s+DUE|BALANCE\s+DUE|TOTAL\s+(?:DUE|PAYABLE)|MONTANT\s+(?:TOTAL|D[UÛ]|[AÀ]\s+PAYER)"
    r"|TOTAL\s+[AÀ]\s+PAYER"
)


def find_currency(text: str) -> str | None:
    """The invoice's currency code (see ``currency_evidence``)."""
    found = currency_evidence(text)
    return found[0] if found else None


def _in_payment_note(s: str, m: re.Match[str]) -> bool:
    """Whether the sentence ``m`` is in is a note on how the supplier may be paid ("Payments in US dollars
    accepted"). The sentence, not the line: "All prices in Canadian dollars. Payments in USD accepted." states CAD."""
    start = s.rfind("\n", 0, m.start()) + 1
    end = s.find("\n", m.end())
    before = re.split(r"[.;!?]\s", s[start : m.start()])[-1]
    after = re.split(r"[.;!?](?:\s|$)", s[m.end() : end if end >= 0 else len(s)])[0]
    return bool(_PAYMENT_NOTE.search(before + m.group(0) + after))


def _codes_in_order(text: str) -> list[str]:
    """The currency codes in ``text``, each once, in the order they are printed."""
    return list(dict.fromkeys(m.group(1) for m in re.finditer(_CODE, text)))


def currency_evidence(text: str) -> tuple[str, float] | None:
    """(currency, how sure, 0-1). A statement ("All amounts in USD", "Currency: CAD", "All prices in Canadian
    dollars") or a code printed on the total's label ("Total USD", its amount in the next column) or on the amounts
    settles it; a code anywhere else counts when it is the only one, and not when it is only in a note on how the
    supplier may be paid ("Payments in USD accepted"). A statement in such a note ("Payments in US dollars accepted")
    is no statement either, and a code on an exchange or equivalent note ("CAD equivalent: CAD 1,438.50") is not on the
    invoice's amounts. Several codes and nothing to tell which the invoice is billed in ("1 USD = 1.37 CAD" in a note):
    the first printed is given, but not as sure."""
    s = text.upper()
    stated = {m.group(1) or m.group(2) for m in _CURRENCY_STATEMENT.finditer(s) if not _in_payment_note(s, m)}
    stated |= {
        "CAD" if m.group("cad") or m.group("cad2") else "USD"
        for m in _WORDS_STATEMENT.finditer(s)
        if not _in_payment_note(s, m)
    }
    if len(stated) == 1:
        return stated.pop(), 0.8
    lines = [ln for ln in s.splitlines() if not _EXCHANGE.search(ln)]
    on_total = {m.group(1) for ln in lines if _TOTAL_LABEL.search(ln) and not _PAYMENT_NOTE.search(ln)
                for m in re.finditer(_CODE, ln)}  # fmt: skip
    if len(on_total) == 1:
        return on_total.pop(), 0.8
    on_amounts = {m.group(1) or m.group(2) for ln in lines for m in _CODE_ON_AMOUNT.finditer(ln)}
    if len(on_amounts) == 1:
        return on_amounts.pop(), 0.8
    # A code anywhere else, the first printed first; one only in a note on how the supplier may be paid is a guess,
    # not as sure.
    rest = "\n".join(ln for ln in s.splitlines() if not _PAYMENT_NOTE.search(ln))
    codes = _codes_in_order(rest)
    if codes:
        return codes[0], 0.8 if len(codes) == 1 else 0.5
    codes = _codes_in_order(s)
    if codes:
        return codes[0], 0.5
    if re.search(r"\bUS\$|\bUS\s?DOLLARS?\b|\$\s?US\b", s):
        return "USD", 0.8
    if re.search(r"\bC\$|\bCDN\b|CANADIAN", s):
        return "CAD", 0.8
    # A euro or pound sign is that currency (a plain "$" is not: Canadian or US dollars), unless dollar signs
    # outnumber it: one "£66" among a scan's dollar amounts is OCR's reading of a "$".
    for sign, code in (("€", "EUR"), ("£", "GBP")):
        if sign in s and s.count(sign) >= s.count("$"):
            return code, 0.8
    return None


def normalize_value(field: str, value: object) -> object:
    """The comparable form of ``value`` for ``field`` (used to test agreement between readers)."""
    from .types import AMOUNT_FIELDS, DATE_FIELDS

    if value is None or value == "":
        return None
    if field in AMOUNT_FIELDS:
        if isinstance(value, (int, float)):
            return round(float(value), 2)
        v = parse_amount(str(value))
        return None if v is None else round(v, 2)
    if field in DATE_FIELDS:
        if isinstance(value, dt.date):
            return value.isoformat()
        return parse_date(str(value), prefer_day_first=None) or str(value)
    if field == "gst_hst_registration_number":
        return norm_gst(str(value))
    if field == "qst_registration_number":
        return norm_qst(str(value))
    if field == "pst_registration_number":
        return norm_pst(str(value))
    if field in ("invoice_number", "po_number"):
        return norm_id(str(value))
    if field == "vendor_name":
        return norm_name(str(value))
    if field == "currency":
        return str(value).upper().strip()
    if field == "payment_terms":
        return terms_key(str(value))
    return " ".join(str(value).split()).lower()


def terms_key(text: str) -> str:
    """Payment terms by what they mean: "30 days", "Net 30" and "NET 30 jours" are the same terms;
    "2% 10, Net 30" is not; "Due on receipt" and "Payable à réception" are."""
    s = _plain(unicodedata.normalize("NFKC", text))
    numbers = re.findall(r"\d+", s)
    receipt = bool(re.search(r"receipt|reception", s))
    if not numbers and not receipt:
        return " ".join(s.split())
    return "terms:" + "/".join(str(int(n)) for n in numbers) + (":receipt" if receipt else "")


def same_value(field: str, a: object, b: object) -> bool:
    na, nb = normalize_value(field, a), normalize_value(field, b)
    if na is None or nb is None:
        return False
    if isinstance(na, float) and isinstance(nb, float):
        return amounts_equal(na, nb)
    return na == nb


# ---------------------------------------------------------------- document titles

# What a credit note calls itself, in lower case without accents ("Note de crédit" -> "note de credit"). Email triage
# (``triage``) and capture (``reader.credit_printed_positive``) read the same list: a document triage sends to the
# queue as a credit note is a credit for capture too.
CREDIT_TITLES = (
    r"credit\s*(?:note|memo(?:randum)?|invoice)", r"note\s*de\s*credit", r"avis\s*de\s*credit",
    r"facture\s*d\s*['’]?\s*avoir", r"avoir",
)  # fmt: skip
