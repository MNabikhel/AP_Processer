# ruff: noqa: E501  (label and pattern tables read best one per line)
"""Reading the value printed beside or under a label."""

from __future__ import annotations

import re
import unicodedata
from contextvars import ContextVar

from ..normalize import (
    find_gst_numbers,
    find_pst_numbers,
    find_qst_numbers,
    looks_like_money,
    norm_id,
    parse_dates,
)
from ..types import (
    AMOUNT_FIELDS,
    PART_OF_TERMS,
    Box,
    Line,
    Reading,
    Word,
    union_all,
)
from ._common import (
    _AS_PRINTED,
    _TAX_BASE,
    _TAX_FIELDS,
    plain,
    plain_with_offsets,
)
from .labels import (
    _NEXT_WORD,
    _OCR_LABEL_GAP,
    _OCR_LABEL_ONE,
    _OCR_LABEL_ZERO,
    _PAGE_NUMBER,
    _PERCENT,
    _REG_WORDS,
    _SPLIT_BN,
    _hits_at,
    _LabelHit,
)


def _label_hits(line: Line) -> list[_LabelHit]:
    """Labels that start a line or follow a separator inside it ("... | Date: ...")."""
    # OCR puts dots, dashes or middots between label words ("Total.before tax", "Bon·de commande").
    text = _OCR_LABEL_ONE.sub("l", _OCR_LABEL_GAP.sub(" ", plain(line.text)))  # OCR's "Tota1" (same length)
    text = _OCR_LABEL_ZERO.sub("o", text)  # OCR's "INV0ICE" (same length)
    starts = [0] + [m.end() for m in re.finditer(r"(?:\s{2,}|\s\|\s|[,;]\s)", text)]
    page_no = _PAGE_NUMBER.match(text)
    if page_no:
        starts.append(page_no.end())  # "Page 1/2 Invoice date 2025-06-21": OCR ran the page number into the label
    first = list(starts)
    hits = _hits_at(line, text, starts)
    # A header row of bilingual labels set one space apart ("Date of issue / Date de facturation Payment Terms /
    # Conditions"): a label may also start where such a label ends, when no value follows it on the line. (Only after
    # a bilingual label: "Delivery Date" is one label, not "Delivery" and a "Date".)
    for _ in range(3):
        more = set()
        for hit in hits:
            m = _NEXT_WORD.match(text, hit.end)
            if (m and "/" in hit.label_text and m.end() not in starts
                    and not _value_reading(hit.field, line, hit.end, 1.0, "x")):  # fmt: skip
                more.add(m.end())
        if not more:
            break
        starts += sorted(more)
        hits = _hits_at(line, text, starts, chained=set(starts) - set(first))
    # "Service Address / Adresse de livraison: Payment Terms / Conditions de paiement:" and "Fax / Téléc.: 905.555.2439
    # Issued / Date de facturation:": a bilingual label may also start after another label's colon or value.
    after_colon = [m.end() for m in re.finditer(r"(?::|\d)\s+(?=[a-z])", text) if m.end() not in starts]
    if after_colon:
        seen = {h.field for h in hits}
        hits += [h for h in _hits_at(line, text, after_colon, chained=set(after_colon))
                 if "/" in h.label_text and h.field not in seen]  # fmt: skip
    if len(text) != len(line.text):
        # "Fittings ½ in., PO # 4500123456": "½" is three characters in plain text; the value is read from the line.
        at = plain_with_offsets(line.text)[1]
        for h in hits:
            h.start, h.end = at[min(h.start, len(at) - 1)], at[min(h.end, len(at) - 1)]
    return hits


def _span_words(line: Line, start: int, end: int) -> list[Word]:
    """Words of ``line`` covering characters [start, end) of ``line.text``."""
    out, pos = [], 0
    for w in line.words:
        a, b = pos, pos + len(w.text)
        if b > start and a < end:
            out.append(w)
        pos = b + 1
    return out


def _boxes(words: list[Word]) -> list[Box]:
    b = union_all([w.box for w in words])
    return [b] if b else []


def _min_conf(words: list[Word]) -> float:
    return min((w.conf for w in words), default=1.0)


# After the label, its "No." / "No:" too: "Receipt # No.: 9876" (capitals, so with its point or colon: "NO-4471" is a number).
_SEP = re.compile(r"^[\s:#.\-–—=|]*(?:(?:no|n°|nº|#|number)\b\.?\s*[:#]?\s*|(?i:no)(?:\.\s*:?|\s*:)\s*)?")

_ID_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/.]*[A-Za-z0-9]|[A-Za-z0-9]")

# Groups of three digits after the first one ("123 456"), with nothing after them on the line or a word next.
_DIGIT_GROUPS = re.compile(r"(?: \d{3})+(?=\s*$|\s+[^\W\d_])")


def _id_from(text: str, offset: int) -> tuple[str, int, int] | None:
    """An identifier at the start of ``text`` (after separators): letters/digits/dashes, with a digit."""
    m = _SEP.match(text)
    pos = m.end() if m else 0
    rest = text[pos:]
    tokens = list(_ID_TOKEN.finditer(rest))
    if not tokens:
        return None
    first = tokens[0]
    value, a, b = first.group(0), first.start(), first.end()
    end = None
    if not any(c.isdigit() for c in value) and len(tokens) > 1:
        nxt = tokens[1]
        gap = rest[b : nxt.start()]
        if len(gap) <= 1 and any(c.isdigit() for c in nxt.group(0)) and len(value) <= 4:
            value, b = rest[a : nxt.end()], nxt.end()
        elif re.fullmatch(r" ?- ?", gap) and nxt.group(0)[:1].isdigit() and len(value) <= 4 and value.isupper():
            # OCR's space beside the hyphen of "PO-20203": "PO -20203" is the one number.
            value, end = value + "-" + nxt.group(0), nxt.end()
    elif re.fullmatch(r"\d{1,3}", value) and (groups := _DIGIT_GROUPS.match(rest, b)):
        # "No de facture 123 456": a number printed in groups of three, alone after its label or before the next
        # words on its line ("123 456 Date ..."), is one number.
        value = rest[a : groups.end()]
    if not any(c.isdigit() for c in value):
        return None
    value = value.strip(".-/")
    return value, offset + pos + a, offset + pos + (end if end is not None else a + len(value))


# A label OCR glued to the number after it: "No.066", "Inv321", "N°factureFA2627633", "INVOICEA-2026-1".
# An all-capitals "INV2026..." is left alone: suppliers print that as part of the number.
_GLUED_LABEL = re.compile(
    r"^(?:(?:(?i:tax|sales)\s*)?(?i:invoice|facture)(?:\s*(?i:number|no)\.?)?|(?i:n°\s*facture)|(?i:no)\.+|(?i:n°|nº)|Inv\.?|#)\s*(?=[A-Za-z]{0,4}-?\d)"
)


def _unglue_label(value: str) -> str:
    rest = _GLUED_LABEL.sub("", value, count=1)
    return rest if rest != value and len(rest) >= 1 and any(c.isdigit() for c in rest) else value


def _unglue_next_word(value: str) -> str:
    """OCR ran the number into the next word on its line: "1128461Cashier" (from "1128461 Cashier: Thandar")."""
    m = re.fullmatch(r"(.*\d)([A-Z][a-z]{3,}\S*)", value)
    return m.group(1) if m and len(norm_id(m.group(1))) >= 2 else value


def _ocr_id_fix(value: str) -> str:
    """OCR reads the letter O in a prefix as a zero ("P0-40059" -> "PO-40059"), and a zero among
    digits as the letter o ("Fo8005" -> "F08005")."""
    value = re.sub(r"(?<=[A-Z])o(?=\d)|(?<=\d)o(?=\d)", "0", value)
    return re.sub(r"^([A-Za-z]+)0([A-Za-z]*)(?=-)", lambda m: m.group(1) + "O" + m.group(2), value)


_OCR_NAME_WORDS = {"itee": "ltée", "ltee": "ltée", "itée": "ltée", "ltée": "ltée", "lnc": "Inc", "lnc.": "Inc.", "ine": "Inc",
                   "ine.": "Inc.", "ltd": "Ltd", "ltd.": "Ltd.", "limitee": "limitée"}  # fmt: skip


def _ocr_name_fix(name: str) -> str:
    """Common OCR slips in company names: "Itee" for "ltée", "lronwood" for "Ironwood"."""
    out = []
    name = re.sub(r"(?<=[a-z])(?=(?:LP|LLP|LLC|Inc|Ltd|Corp)\b)", " ", name)
    name = re.sub(r"\.{2,}$", ".", name)  # a speck after the suffix: "Ltd.."  # "ServicesLP" -> "Services LP"
    name = re.sub(r"(?<!\bMc)(?<!\bMac)(?<=[a-z])(?=[A-Z][a-z])", " ", name)  # "TrueNorth" -> "True North"
    for word in name.split():
        key = word.lower()
        if key in _OCR_NAME_WORDS:  # a known word (spelled right or as OCR slips): never "repaired" below
            if key not in ("ltd", "ltd."):
                word = _OCR_NAME_WORDS[key]
        elif re.match(r"^l[bcdfghjkmnpqrstvwxz]", word):  # English words do not start "lr", "ln", ...
            word = "I" + word[1:]
        out.append(word)
    return " ".join(out)


def _looks_like_phone_or_postal(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if re.fullmatch(r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4}", value) or (
        len(digits) == 10 and "-" in value and value.count("-") == 2 and len(value.split("-")[0]) == 3
    ):
        return True
    return bool(re.fullmatch(r"[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d", value))


_MONTH_DAY = re.compile(
    r"^(?:jan|feb|fev|mar|apr|avr|may|mai|jun|juin|jul|juil|aug|aou|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}\b"
)

# The document's numeric date order while it is read (None: 03/04/2026 stays ambiguous).
_DAY_FIRST: ContextVar[bool | None] = ContextVar("day_first", default=None)

_STREET = re.compile(
    # "4833 Barrington St", "12 Main Street": a street's name, then its kind (English);
    r"^\s*,?\s*(?:[A-ZÀ-Ý0-9][\w'.-]*\s+){1,3}(?i:st|street|ave?|avenue|rd|road|blvd|boulevard|dr|drive|way|cres|"
    r"crescent|hwy|highway|pkwy|ln|lane|ct|court|pl|place)\b\.?(?:\s|,|$)|"
    # "2565 av. du Parc", "9777, boul. des Laurentides", "1234 rue Sainte-Catherine", "4099 Hwy 7": its kind first.
    r"^\s*,?\s*(?i:rue|boul(?:evard)?|av(?:enue)?|ch(?:emin)?|route|rang|mont[ée]e|hwy|highway)\b\.?\s+\S",
)

_DIGIT_LOOKALIKES = str.maketrans("01358", "OISSB")  # OCR's digits for letters

_LABEL_WORDS = {"invoice", "facture", "number", "numero", "date", "total", "amount", "page", "order", "bill",
                "account", "customer", "client", "reference", "receipt", "statement", "credit"}  # fmt: skip

# A due date's label and its date are in one sentence; a discount's deadline is no due date.
_SENTENCE_BREAK = re.compile(r"[.;!?]\s+[A-Z]")

_DUE_DISCOUNT = re.compile(r"discount|escompte|skonto")

_RATE_CLAUSE = re.compile(
    r"[,;]\s*\d{1,2}(?:[.,]\d{1,2})?\s*%"
)  # "..., 2% Skonto innerhalb 10 Tagen": a clause of its own

# An exchange rate for one unit of a currency: "(1 USD = 1.235 CAD)", "USD 1 = CAD 1.3521". Not arithmetic ("10 x 62.15 =
# 621.50", "13% of 550.00 = 71.50") nor a converted total ("CAD 621.50 = USD 460.37").
_FX_RATE = re.compile(
    r"\(?\s*(?:1(?:[.,]0+)?\s*[A-Z]{3}|[A-Z]{3}\s*1(?:[.,]0+)?)\s*=\s*(?:[A-Z]{3}\s*)?\d+(?:[.,]\d+)?\s*(?:[A-Z]{3})?\s*\)?"
)

_GLUED_WORD = re.compile(r"/[A-Z][a-z]{3,}$")  # a capitalised word after a slash ending an ID: "/Contract"


def _value_reading(field: str, line: Line, start: int, base: float, method: str) -> Reading | None:
    """Read ``field`` from ``line.text[start:]``."""
    text = line.text
    rest = text[start:]
    if field in ("invoice_number", "po_number"):
        got = _id_from(rest, start)
        if not got:
            return None
        value, a, b = got
        glued = _GLUED_WORD.search(value)
        if glued and field == "po_number" and any(c.isdigit() for c in value[: glued.start()]):
            # "PO#474192803B/Contract": the next label's word run onto the number after a slash, in the PDF's text.
            value, b = value[: glued.start()], b - (len(value) - glued.start())
        if _looks_like_phone_or_postal(value) or parse_dates(value) or _MONTH_DAY.match(plain(value)):
            return None
        if any(da <= a - start < db for _d, da, db, _amb in parse_dates(rest)):
            return None  # the day of a date ("30 Jun 2025"), not a number
        if any(ga == a for _v, ga, _gb, _ok in find_gst_numbers(text)):
            return None  # "782311542 RT 0001", "No. 123456782 RT0001": a business number, not an invoice or a PO
        if value.isdigit() and _STREET.search(text[b:]):
            return None  # a street number ("4833 Barrington St", "9777, boul. des Laurentides"), not an ID
        if re.fullmatch(r"(?i)(?:unit|suite|ste|bureau|apt|room|local|piece|box)\s*#?\s*\d+[a-z]?", value):
            return None  # part of an address ("Unit 7")
        if value.translate(_DIGIT_LOOKALIKES).strip("#:.-").casefold() in _LABEL_WORDS:
            return None  # a label OCR misread ("INVO1CE"), not its value
        if re.fullmatch(r"(?i)net-?\d{1,3}(?:days?|jours?)?", value):
            return None  # "Net30days": the terms (OCR ran them together), not a number
        if field == "po_number" and re.fullmatch(r"(?i)net\s?-?\d{1,3}(?:\s?(?:days?|jours?))?", value):
            return None  # "Net 30" under the next column of a tight grid: the terms, not a purchase order
        if re.fullmatch(r"(?i)page-?\d{1,3}", value):
            return None  # "Page - 1" (OCR's spaces beside the hyphen joined), not a number
        if re.fullmatch(r"\d{1,4}[.)][A-Za-z]{3,}.*", value):
            return None  # "15.Dumplings": a receipt's item number run into its description, not a number
        if re.fullmatch(r"[A-Za-z]{1,4}-?\d+[.,]\d{2}", value):
            base *= 0.5  # "PO-636.19": a dot OCR put inside the number, or a price; not to be trusted alone
        if field == "po_number" and (
            re.fullmatch(r"\$?\d{1,3}(?:[.,]\d{2})?", value) or re.fullmatch(r"\d{1,2}", value)
        ):
            return None  # a quantity, a price or a day of the month, not a purchase order
        if len(norm_id(value)) < 2 or len(value) > 30:
            return None
        words = _span_words(line, a, b)
        if any(w.source not in _AS_PRINTED for w in words):
            value = _ocr_id_fix(_unglue_label(_unglue_next_word(value)))
        return Reading(field, value, value, _boxes(words), base * _min_conf(words), method)
    if field in ("invoice_date", "due_date"):
        found = parse_dates(rest, prefer_day_first=_DAY_FIRST.get())
        if not found:
            return None
        d, a, b, ambiguous = found[0]
        if a > 30:  # the date must be close to its label
            return None
        if field == "due_date" and (
            _SENTENCE_BREAK.search(rest[:a]) or _DUE_DISCOUNT.search(_RATE_CLAUSE.split(plain(rest))[0])
        ):
            return None  # "Payment is due by the 15th. Next service 2026-12-01", "Please pay by ... to take the 2% discount"
        words = _span_words(line, start + a, start + b)
        raw = rest[a:b]
        return Reading(field, d.isoformat(), raw, _boxes(words), base * (0.6 if ambiguous else 1.0) * _min_conf(words),
                       method + ("-ambiguous" if ambiguous else ""))  # fmt: skip
    if field in AMOUNT_FIELDS:
        cleaned = _PERCENT.sub(lambda m: " " * len(m.group(0)), rest)
        cleaned = _FX_RATE.sub(lambda m: " " * len(m.group(0)), cleaned)  # "(1 USD = 1.235 CAD)": a rate, no amount
        for _d, da, db, _amb in parse_dates(cleaned):
            if re.search(
                r"[a-z]{3}", plain(cleaned[da:db])
            ):  # "AMOUNT DUE ON April 1, 2022": the date's year is no amount
                cleaned = cleaned[:da] + " " * (db - da) + cleaned[db:]
        if field.endswith("_amount") or field == "tax_total":
            # "GST/HST Reg. No. 123456789RT0001" is a registration number, not an amount.
            if (
                (_REG_WORDS.search(plain(rest[:12])) and not looks_like_money(rest))
                or find_gst_numbers(rest)
                or find_qst_numbers(rest)
                or find_pst_numbers(f"PST {rest[:40]}")  # "PST No. 1234567": rest is what follows the label
                or _SPLIT_BN.search(rest)  # OCR broke the number: "HST # 18337 5716 RT 00.01"
            ):
                return None
        amounts = _amounts_in(cleaned)
        if field in _TAX_FIELDS or field == "tax_total":
            # "HST 13% on $1,000.00": the amount the tax is charged on, not the tax (further right, or the next column)
            amounts = [x for x in amounts if not _TAX_BASE.search(cleaned[: x[1]])]
        if not amounts:
            return None
        money = [a for a in amounts if looks_like_money(cleaned[a[1] : a[2]])]
        if not money and field in _TAX_FIELDS and all(abs(v) < 200 and float(v).is_integer() for v, _, _ in amounts):
            return None  # "HST 138": the rate (13% read with the % as an 8), the amount is further right
        if not money and field == "other_charges":
            return None  # "Shipping - UPS Ground   1": a line's quantity, not a charge (charges are money: 45.00)
        money = money or amounts
        value, a, b = money[-1]  # the rightmost amount on a totals row is the amount column
        words = _span_words(line, start + a, start + b)
        moneyish = looks_like_money(cleaned[a:b])
        return Reading(field, round(value, 2), rest[a:b].strip(), _boxes(words),
                       base * (1.0 if moneyish else 0.7) * _min_conf(words), method)  # fmt: skip
    if field == "payment_terms":
        value = _terms(rest)
        if not value:
            return None
        a = plain(rest).find(plain(value)[:6]) if value else 0
        words = _span_words(line, start + max(a, 0), start + max(a, 0) + len(value))
        if _DEPOSIT.search(plain(rest)):
            method += PART_OF_TERMS  # "30% d'acompte, solde à 30 j": the net days are only the balance's
        return Reading(field, value, value, _boxes(words), base * _min_conf(words), method)
    return None


# Terms that ask for part of the amount first (a deposit): "Net 30" is only the balance's terms.
_DEPOSIT = re.compile(
    r"acompte|deposit|down\s*payment|advance\s*payment|anzahlung|\d\s*%\s*(?:a|à|on|upon|at)\s*(?:la\s*)?(?:commande|order|signature)"
)


def _amounts_in(text: str) -> list[tuple[float, int, int]]:
    from ..normalize import find_amounts

    out = []
    for value, a, b in find_amounts(text):
        token = text[a:b].strip()
        digits = re.sub(r"\D", "", token)
        if not digits:
            continue
        if len(digits) >= 9 and not looks_like_money(token):  # registration and account numbers
            continue
        out.append((value, a, b))
    return out


_TERMS = [
    # "2% 10 Net 30", "2/10 Net 30", "2/10, n/30", "2% 10 days, Net 30": the rate, the discount days, the net days
    (re.compile(r"(?<![\d.])(\d{1,2})(?:\s*%\s*/?\s*|\s*/\s*|[\s.]+)(\d{1,2})(?!\d)(?:\s*(?:days?|jours?))?[\s,.;]*(?:net|n\s*/)[\s.:]*(\d{1,3})(?!\d)", re.I),
     "{0}% {1} Net {2}"),
    (re.compile(r"\bnet[\s.:]*(\d{1,3})(?!\d)", re.I), "Net {0}"),
    (re.compile(r"\bn\s*/?\s*(\d{1,3})\b", re.I), "Net {0}"),
    (re.compile(r"(?<!\d)(\d{1,3})\s*(?:days|jours|j\b)", re.I), "Net {0}"),
    (re.compile(r"\bdue[\s.]*(?:up)?on[\s.]*receipt\b|payable[\s.]*(?:a|à|sur|des|dès)[\s.]*(?:la[\s.]*)?r[ée]ception|on[\s.]*receipt", re.I), "Due on receipt"),  # a speck between words: "on.receipt"
]  # fmt: skip

_ON_RECEIPT = re.compile(
    r"(?i)\b(?:payable|due)\s+(?:up)?on\s+receipt\b|payable\s+(?:à|a|dès|des)\s+(?:la\s+)?r[ée]ception"
)

# Terms as printed, put in the words the terms parser (``coding.terms.parse_terms``) reads, on plain text (accents off):
# OCR's specks and glued words, and other languages' days, discount and "at once" ("14 Tage netto", "30 días",
# "50 gg DF", "3% Skonto innerhalb 10 Tagen", "zahlbar sofort").
_TERMS_WORDS = [
    (re.compile(r"(?<=\d)\s*(?:tag(?:en|e)(?=netto)|(?:tag(?:en|e)|dias|giorni|gg|dagen)(?![a-z]))"), " days "),
    (re.compile(r"(?<=\d)\s*j(?![a-z])"), " jours"),  # "solde à 30 j"
    (re.compile(r"(?<=\d)\s*(?=(days?|jours?))\1(?=[a-z])"), r" \1 "),  # OCR's "15jourssuivant la datedelafacture"
    (re.compile(r"(?<![a-z])skonto(?![a-z])"), "discount"),
    (re.compile(r"(?<![a-z])(?:innerhalb|binnen)(?:\s*von)?(?![a-z])"), "within"),
    (re.compile(r"(?<![a-z])bei\s*zahlung(?![a-z])"), "if paid"),
    (re.compile(r"(?<![a-z])(?:df\s*fm|dffm|fine\s*mese|fin\s*de\s*mes)(?![a-z])"), " eom "),
    (
        re.compile(r"zahlbar\s*sofort|sofort\s*(?:zahlbar|fallig)|paiement\s*immediat|payable\s*immediatement"),
        "due on receipt",
    ),  # fmt: skip
    (re.compile(r"(?<![a-z])net[\s.:]+(?=\d)"), "net "),  # "Net: 30", "Net.30"
    (re.compile(r"(?<=on)[\s.]*(?=receipt)"), " "),  # "on.receipt", "uponreceipt"
    (
        re.compile(r"(?:(?<![a-z])a|des|sur)[\s.]*(?=reception)"),
        " a ",
    ),  # also OCR's "Payablesurréception"  # "à.réception", "dès réception"
]

# "Bei Zahlung innerhalb 14 Tagen gewähren wir 2,0% Skonto": the discount's days printed before its rate.
_DAYS_THEN_RATE = re.compile(
    r"within\s*(\d{1,2})\s*days\b[^%\d]{0,40}?(\d{1,2}(?:[.,]\d{1,2})?)\s*%\s*discount(?!\s*(?:if\s*paid\s*)?(?:within\s*)?\d)"
)  # but not "zahlbar innerhalb 30 Tagen netto, 2% Skonto innerhalb 10 Tagen": that rate has its own days

# Where the terms phrase ends on its line: a sentence's end ("Net 30 days. Statements are issued at the end of the
# month."), a wide gap, or another label ("Ship Via: UPS 2 Day", "Lead time 2 weeks", "Freight: 2 Day").
_TERMS_SENTENCE_END = re.compile(r"(?<=[\w)%])[.;!?](?=\s+[A-Z])(?!\s+(?i:net|n\s*/)(?![a-z]))")

_TERMS_NEXT_LABEL = re.compile(
    r"\s{2,}(?!\s|(?i:net|n\s*/|\d))|(?<=\S)\s+(?=[A-Z][A-Za-z.#/'-]*(?: [A-Za-z#][A-Za-z.#/'-]*)?\s?:)"
    r"|(?<=\S)\s+(?=(?i:ship(?:ping)?\s*(?:via|date)|lead\s*time|freight|(?<!on )deliver(?:y|ed)|livraison|statements?|"
    r"f\.?o\.?b|(?:sales\s*)?rep|carrier|due\s*date|customer|account|p\.?o\.?\s*(?:#|no|number))(?![a-z]))"
)

_TERMS_LABEL_WORDS = re.compile(r"(?i)\s*(?:payment\s*)?terms?(?:\s*of\s*payment)?\s*:|\s*conditions?\b[^:]{0,30}:")

# A discount stated in a sentence of its own, after or before the net terms: "2% discount if paid within 10 days".
_DISCOUNT_SENTENCE = re.compile(r"(?i)^\W*\d{1,2}(?:[.,]\d{1,2})?\s*%\s*(?:discount|escompte|skonto)")

# End of month, said of the net days themselves ("Net 30 EOM", "30 days end of month", "Net 30 days from end of
# month", "within 30 days of month end", "2/10 EOM", "Net EOM"): not a month's end said of something else
# ("Statements are issued at the end of the month").
_EOM_WORDS = r"(?:eom|e\.o\.m\.?|end\s*of\s*(?:the\s*)?month|month\s*end|fin\s*de\s*mois)"

_EOM_ATTACHED = re.compile(
    r"\d{1,3}(?:st|nd|rd|th)?\s*(?:days?|jours?|months?|mois)?\s*(?:net\s*)?"
    r"(?:(?:from|after|of|following)\s*(?:the\s*)?(?:invoice\s*date\s*)?)?"
    + _EOM_WORDS
    + r"|(?:^|\bnet|\bdue)\W*"
    + _EOM_WORDS
    + r"|"
    + _EOM_WORDS
    + r"\s*(?:net\s*)?\d"
)

_EOM_ANY = re.compile(_EOM_WORDS)

# Due on receipt (or paid ahead): days elsewhere on the line are not its net days, unless the net days are printed
# with it ("Net 30 days upon receipt of invoice").
_AT_ONCE = re.compile(
    r"(?:up)?on\s*receipt|a\s*reception|\bc\.?o\.?d\b|cash\s*on\s*delivery|comptant|immediate|due\s*now"
)

_PREPAID = re.compile(r"(?<![a-z])pre-?paid(?![a-z])")

_NET_PRINTED = re.compile(r"\bn(?:et)?\s*/?\s*\d|\d{1,3}\s*(?:days?|jours?)\s*net")

# A net day of the next month ("Net 10th prox"), or net months: not the net days of OCR's run-together discount.
# "2% 10 net 1 month": the discount's days stated as days, so the net is read as months, not as 1 day.
_RATE_THEN_MONTHS = re.compile(
    r"(\d{1,2}(?:[.,]\d{1,2})?\s*%\s*\d{1,2})(?!\s*(?:days?|jours?))\s*[,;]?\s*(?=n(?:et)?\s*/?\s*\d{1,2}\s*(?:months?|mois))"
)

# "Net 15th following (month)": the 15th of the next month, as "15 prox".
_FOLLOWING = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s*(?:of\s*(?:the\s*)?)?following(?:\s*month)?(?![a-z])")

_AFTER_NET_NUMBER = re.compile(r"\s*(?:st|nd|rd|th|months?|mois|weeks?|wks?|semaines?|prox)(?![a-z])")

# OCR runs a row's words together ("Net30Freight:2Day", "Net30ShipVia:UPS2Day", "Net10thprox", "Net 1Oth prox"): the
# words are set apart again where figures and letters meet (an ordinal's "th" stays with its number).
_UNGLUE = [
    (re.compile(r"(?<=\d)[Oo](?=\d|th)"), "0"),
    (re.compile(r"(\d(?:st|nd|rd|th))(?=[A-Za-z])"), r"\1 "),
    (re.compile(r"(?<=\d)(?=[A-Za-z])(?!(?:st|nd|rd|th)(?![A-Za-z]))"), " "),
    (re.compile(r"(?<=[A-Za-z])(?=\d)"), " "),
]

# "Net 30th" with no month after it: OCR's or the printer's "th" on the net days.
_NET_ORDINAL_DAYS = re.compile(r"\bnet\s*(\d{1,3})(?:st|nd|rd|th)(?!\s*(?:prox|following|of|month))")


def _terms_phrase(text: str) -> str:
    """The terms phrase at the start of ``text`` (a terms label's value, or a sentence): up to the next sentence, wide
    gap or label, with a discount stated in the next sentence."""
    text = unicodedata.normalize("NFKC", text)  # a speck read as a middle dot is a space later, not a gap
    for rx, sub in _UNGLUE:
        text = rx.sub(sub, text)
    label = _TERMS_LABEL_WORDS.match(text)
    start = label.end() if label else 0
    sentences = []
    pos = start
    for m in _TERMS_SENTENCE_END.finditer(text, start):
        sentences.append(text[pos : m.start()])
        pos = m.end()
    sentences.append(text[pos:])
    first = _clip_at_label(sentences[0])
    rest = [_clip_at_label(x) for x in sentences[1:] if _DISCOUNT_SENTENCE.match(x)]
    return ". ".join([first, *rest[:1]]) if first.strip() else first


def _clip_at_label(text: str) -> str:
    m = _TERMS_NEXT_LABEL.search(text.strip())
    return text.strip()[: m.start()] if m else text.strip()


def _terms(text: str) -> str | None:
    """The terms printed in ``text``, in the short form the app counts the due date from ("Net 30", "2% 10 Net 30",
    "Net 30 EOM", "Net 10 prox", "Net 1 month", "2% 10 days", "Due on receipt"): what ``parse_terms`` reads in the
    terms phrase, written so that it reads the same in the short form."""
    from ...coding.terms import parse_terms

    # OCR's full-width comma ("2%10，Net30") and a speck read as a middle dot ("Net·30")
    text = plain(_terms_phrase(text))
    # "2%10,.Net45", "2.10 net 30": a rate, its days and the net days as OCR runs them together.
    text = _TERMS[0][0].sub(
        lambda m: (
            m.group(0) if _AFTER_NET_NUMBER.match(text, m.end()) else f" {m.group(1)}% {m.group(2)} net {m.group(3)} "
        ),
        text,
    )
    for rx, words in _TERMS_WORDS:
        text = rx.sub(words, text)
    text = _DAYS_THEN_RATE.sub(lambda m: f"{m.group(2)}% discount within {m.group(1)} days", text)
    text = _RATE_THEN_MONTHS.sub(r"\1 days, ", text)
    text = _FOLLOWING.sub(r"\1 prox", text)
    if "prox" not in text:
        text = _NET_ORDINAL_DAYS.sub(r"net \1", text)
    if not _NET_PRINTED.search(text):
        if _AT_ONCE.search(text):
            return "Due on receipt"
        if _PREPAID.search(text):
            return None
    text = _EOM_ANY.sub(" eom " if _EOM_ATTACHED.search(text) else " ", text)  # "month end" too, as "EOM"
    t = parse_terms(text)
    if t.discount_days is not None and not t.prox_day and not t.discount_prox_day:
        from ...coding.terms import _prox_day

        if day := _prox_day(text):  # "2% 10 days, Net 10th prox": the app counts no discount with prox net days
            return f"Net {day} prox"
    return _terms_form(t)


def _terms_form(t) -> str | None:  # noqa: ANN001 - a coding.terms.Terms
    """``t`` written as short terms that ``parse_terms`` reads back as ``t``."""
    if t.on_receipt:
        return "Due on receipt"
    eom = " EOM" if t.eom else ""
    rate = f"{t.discount_pct:g}%" if t.discount_pct else ""
    if rate and t.discount_prox_day:
        if t.prox_day == t.discount_prox_day:
            return f"{rate} {t.discount_prox_day} prox"
        return f"{rate} {t.discount_prox_day} prox, Net " + (f"{t.prox_day} prox" if t.prox_day else f"{t.net_days}")
    if t.prox_day:
        return f"Net {t.prox_day} prox"
    if t.net_months:
        net = f"Net {t.net_months} month{'s' if t.net_months > 1 else ''}{eom}"
        return f"{rate} {t.discount_days} days, {net}" if rate and t.discount_days is not None else net
    if rate and t.discount_days is not None:
        return (
            f"{rate} {t.discount_days} days"
            if t.net_days is None
            else f"{rate} {t.discount_days} Net {t.net_days}{eom}"
        )
    if t.net_days is not None:
        return "Due EOM" if t.net_days == 0 and t.eom else f"Net {t.net_days}{eom}"
    return None
