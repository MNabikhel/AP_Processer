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
_AMOUNT = re.compile(
    r"""(?P<neg1>[-−–(])?\s*(?:[$€£]\s*)?
        (?P<num>\d{1,3}(?:[ ,.  ']\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)
        \s*(?:[$€£])?\s*(?P<neg2>\)|-|CR\b|cr\b)?""",
    re.X,
)


_OCR_THOUSANDS = re.compile(r"(?<=\d)[;:](?=\d{3}(?!\d))")
_OCR_DECIMAL = re.compile(r"(?<=\d)[;:](?=\d{2}(?!\d))")
_OCR_ONE = re.compile(r"(?<![A-Za-z\d])[Il](?=[,.]?\d{3}[.,]\d{2}(?!\d))")


def _ocr_amount_text(s: str) -> str:
    """OCR slips in amounts, fixed in place (same length, so spans stay valid): the thousands comma or
    decimal point printed as ";" or ":" ("1;864.71", "2447:70"), and the digit 1 as "I" or "l"
    ("I,396.35")."""
    s = _OCR_THOUSANDS.sub(",", s)
    s = _OCR_DECIMAL.sub(".", s)
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
    last_sep = max(num.rfind("."), num.rfind(","))
    if last_sep >= 0 and len(num) - last_sep - 1 in (1, 2):
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
    """Every amount in ``text`` with its character span (a sign or parentheses included)."""
    out = []
    s = _ocr_amount_text(unicodedata.normalize("NFKC", text))
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
        out.append((-value if neg else value, m.start(), m.end()))
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


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


_ISO = re.compile(r"\b(20\d{2}|19\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})\b")
# Spaces optional: OCR often drops them ("May14,2026", "20Jul2026"); the month name is looked up.
_WORDY_MDY = re.compile(r"\b([a-z]{3,9})\.?[\s-]*(\d{1,2})(?:st|nd|rd|th)?[,.]?\s*(\d{4})\b")
_WORDY_DMY = re.compile(r"\b(\d{1,2})(?:er|st|nd|rd|th)?[\s.]*([a-z]{3,9})\.?,?\s*(\d{4})\b")
# OCR reads letters of a month as digits ("0ct", "Ju1", "N0v"); fixed in place, same length.
_OCR_MONTHS = [
    (re.compile(r"0ct"), "oct"),
    (re.compile(r"ju1"), "jul"),
    (re.compile(r"n0v"), "nov"),
    (re.compile(r"(?<![a-z])1an"), "jan"),
    (re.compile(r"ap1"), "apr"),
]


def _ocr_date_text(s: str) -> str:
    for rx, fixed in _OCR_MONTHS:
        s = rx.sub(fixed, s)
    return s


_WORDY_DMY_DASH = re.compile(r"\b(\d{1,2})[-\s]([a-z]{3,9})[-\s](\d{2,4})\b")


def _make(y: int, m: int, d: int) -> dt.date | None:
    if y < 100:
        y += 2000
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
    for rx, order in ((_WORDY_MDY, "mdy"), (_WORDY_DMY, "dmy"), (_WORDY_DMY_DASH, "dmy")):
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
    for m in _NUMERIC.finditer(s):
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


def clean_id(text: str) -> str:
    """The identifier as printed, trimmed of label punctuation around it."""
    return str(text).strip().strip(":#.,;").strip()


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


_BN = re.compile(r"(?<![\d-])(\d(?:[ -]?\d){8})[\s.:-]*(RT|R\s?T)[\s.:-]*(\d{4})(?!\d)", re.I)
_BN9 = re.compile(r"(?<!\d)(\d{3}\s?\d{3}\s?\d{3})(?!\d)")
_QST = re.compile(r"(?<!\d)(\d{10})[\s.:-]*(TQ|T\s?Q)[\s.:-]*(\d{4})(?!\d)", re.I)


def find_gst_numbers(text: str) -> list[tuple[str, int, int, bool]]:
    """GST/HST registration numbers: ('123456782RT0001', start, end, luhn_valid)."""
    s = unicodedata.normalize("NFKC", text)
    out = []
    for m in _BN.finditer(s):
        digits = re.sub(r"[\s-]", "", m.group(1))
        out.append((f"{digits}RT{m.group(3)}", m.start(), m.end(), luhn_ok(digits)))
    return out


def find_qst_numbers(text: str) -> list[tuple[str, int, int]]:
    s = unicodedata.normalize("NFKC", text)
    return [(f"{m.group(1)}TQ{m.group(3)}", m.start(), m.end()) for m in _QST.finditer(s)]


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


def find_currency(text: str) -> str | None:
    s = text.upper()
    for code in CURRENCIES:
        if re.search(rf"(?<![A-Z]){code}(?![A-Z])", s):  # also glued by OCR: "TOTALCAD"
            return code
    if re.search(r"\bUS\$|\bUS\s?DOLLARS?\b|\$\s?US\b", s):
        return "USD"
    if re.search(r"\bC\$|\bCDN\b|CANADIAN", s):
        return "CAD"
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
