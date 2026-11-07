"""Turn printed text into comparable values: amounts, dates, identifiers, registration numbers.

Every reader normalizes with these functions, so two readers that saw the same printed value always
produce the same normalized value, and agreement can be tested with ``==``.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

from ..core import parsing

# The readers' amounts and dates (D-191): found and read in core/parsing.py, imported from here as before.
from ..core.parsing import _folded, _plain, _unfolded, find_amounts, infer_day_first, parse_dates  # noqa: F401

# ---------------------------------------------------------------- amounts


def parse_amount(text: str | None) -> float | None:
    """'1,234.56' / '1 234,56 $' / '$(12.00)' / '12.00 CR' / '-1.234,56' -> float: the first amount printed in
    ``text`` (``core.parsing.parse_amount``'s rules for printed text). None if no amount."""
    return parsing.parse_amount(text, printed=True, within=True)


def amounts_equal(a: float | None, b: float | None, tol: float = 0.005) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def looks_like_money(text: str) -> bool:
    """A token printed like money: two decimals (".65" too), or a currency sign."""
    s = text.strip()
    return bool(re.search(r"\d[.,]\d{2}\b|(?<![\w.,])\.\d{2}\b", s)) or bool(re.search(r"[$€£]\s*\.?\d", s))


# ---------------------------------------------------------------- dates


def parse_date(text: str, *, prefer_day_first: bool | None = None) -> str | None:
    """The first date in ``text`` as YYYY-MM-DD (None if none, or only an unresolved ambiguous one): ``core.parsing``'s
    rules for printed text."""
    found = parsing.parse_date(text, printed=True, within=True, day_first=prefer_day_first)
    return found.isoformat() if found else None


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
def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


_BN = re.compile(r"(?<![\d-])(\d(?:[ .-]?\d){8})[\s.:-]*(RT|R\s?T)[\s.:-]*(\d{4})(?!\d)", re.I)
_BN9 = re.compile(r"(?<!\d)(\d{3}\s?\d{3}\s?\d{3})(?!\d)")
_QST = re.compile(r"(?<!\d)(\d{10})[\s.:-]*(TQ|T\s?Q)[\s.:-]*(\d{4})(?!\d)", re.I)


def find_gst_numbers(text: str) -> list[tuple[str, int, int, bool]]:
    """GST/HST registration numbers: ('123456782RT0001', start, end, luhn_valid), start and end in ``text``."""
    s, spans = _folded(text, _nfkc)
    out = []
    for m in _BN.finditer(s):
        digits = re.sub(r"[\s.-]", "", m.group(1))
        out.append((f"{digits}RT{m.group(3)}", *_unfolded(spans, *m.span()), luhn_ok(digits)))
    return out


def find_qst_numbers(text: str) -> list[tuple[str, int, int]]:
    s, spans = _folded(text, _nfkc)
    return [(f"{m.group(1)}TQ{m.group(3)}", *_unfolded(spans, *m.span())) for m in _QST.finditer(s)]


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
    s, spans = _folded(text, _nfkc)
    out = []
    for m in _PST.finditer(s):
        if sum(c.isdigit() for c in m.group(2)) < 6:
            continue
        a = m.start(1) if m.group(1) else m.start(2)
        out.append((s[a : m.end()].strip(), *_unfolded(spans, a, m.end())))
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


_CODE = r"(?<![A-Z])(CAD|USD|EUR|GBP)(?![A-Z])"  # also glued by OCR: "TOTALCAD"
# "All amounts in USD", "Prices are quoted in CAD", "Currency: USD", "Montants en dollars CAD", "Devise : CAD".
_CURRENCY_STATEMENT = re.compile(
    r"(?:AMOUNTS?|PRICES?|FIGURES|MONTANTS?|PRIX)\s+(?:ARE\s+|SONT\s+)?(?:(?:STATED|SHOWN|EXPRESSED|QUOTED|BILLED|"
    r"PAYABLE|EXPRIM[EÉ]S?|INDIQU[EÉ]S?|PAYABLES?)\s+)?(?:IN|EN)\s+(?:DOLLARS\s+)?" + _CODE
    + r"|(?:CURRENCY|DEVISE|MONNAIE)\s*[:=]?\s*" + _CODE
)  # fmt: skip
# The same said in words: "All prices in Canadian dollars", "Amounts in US dollars", "Montants en dollars canadiens";
# or as funds: "All prices are in US funds", "Payable in Canadian funds", "Montants en fonds américains" (B-1517).
_WORDS_STATEMENT = re.compile(
    r"(?:IN|EN)\s+(?:(?P<cad>CANADIAN)|(?P<usd>U\.?S\.?|AMERICAN))\s+(?:DOLLARS|FUNDS)"
    r"|(?:DOLLARS|FONDS)\s+(?:(?P<cad2>CANADIENS)|(?P<usd2>AM[EÉ]RICAINS|US)\b)"
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
    if re.search(r"\bCA?\$|\bCDN\b|CANADIAN", s):  # "C$", "CA$ 1,234.00"
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
    return "terms:" + "/".join(str(int(n)) for n in numbers) + (":receipt" if receipt else "") + terms_kind(text)


def terms_kind(text: str) -> str:
    """What the digits of payment terms don't say (B-1519): counted from the end of the month ("Net 30 EOM"), in
    months ("Net 1 month"), or to a day of the next month ("Net 10 prox"); "" for plain terms, whose key stays as it
    was."""
    from ..coding.terms import parse_terms

    terms = parse_terms(text)
    return ((":eom" if terms.eom else "") + (":months" if terms.net_months else "")
            + (f":prox{terms.prox_day}" if terms.prox_day else ""))  # fmt: skip


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
