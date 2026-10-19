# ruff: noqa: E501  (label and pattern tables read best one per line)
"""Rule reader: finds invoice header fields from labels, patterns and positions on the page.

For every field it returns candidate readings, best first, each with the box of the words it read.
It knows English and French labels, glued OCR text ("InvoiceNo:"), label-then-value on one line,
value to the right of the label, value below it (also header grids), and it avoids the usual traps
(order and ship dates, PO Box, customer numbers, previous balances, registration numbers next to tax
amounts). Totals are chosen together: the subtotal, taxes and total that add up win.
"""

from __future__ import annotations

import datetime as dt
import functools
import re
import statistics
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from itertools import product

from .normalize import (
    CREDIT_TITLES,
    currency_evidence,
    find_currency,
    find_gst_numbers,
    find_pst_numbers,
    find_qst_numbers,
    infer_day_first,
    looks_like_money,
    norm_id,
    parse_dates,
)
from .types import (
    AMOUNT_FIELDS,
    BILL_TO_READING,
    Box,
    DocLayout,
    Line,
    LineReading,
    PageLayout,
    Reading,
    Word,
    union_all,
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


# ---------------------------------------------------------------- labels


# (regex on plain lowercase text, strength). Regexes match at the label's start.
def _A(letters: str) -> str:
    """An abbreviation printed with or without dots: "gst" matches GST, G.S.T., G. S. T."""
    return r"\.?\s?".join(letters) + r"(?![a-z])\.?"  # may touch a number: OCR prints "GST5%"


_NO = r"(?:no\b\.?|nos?\.|n\s?°|nº|n o\b|#|number|num\b\.?|numero)"
LABELS: dict[str, list[tuple[str, float]]] = {
    "invoice_number": [
        (rf"invoice\s*{_NO}", 1.0), (r"invoice\s*id\b", 0.95), (rf"inv\.?\s*{_NO}", 0.95),
        (rf"facture\s*{_NO}", 1.0), (r"(?:no|n\s?°|nº|numero|n)\.?\s*(?:de\s*)?(?:la\s*)?facture", 1.0),
        (rf"bill\s*{_NO}", 0.9), (rf"document\s*{_NO}", 0.85), (r"invoice\s*ref(?:erence)?\b", 0.85),
(rf"credit\s*(?:note|memo)?\s*{_NO}", 0.95), (r"(?:sales|tax|commercial)\s*invoice\b", 0.6), (r"inv(?![a-z])(?![.\s]*(?:date|total|amount))\.?(?![-\d])", 0.55), (rf"note\s*de\s*credit\s*{_NO}", 0.95),
        (rf"statement\s*{_NO}", 0.6), (r"invoice\s*:", 0.7), (r"facture\s*:", 0.7), (r"invoice\b(?!\s*(?:date|total|amount|to\b|period))", 0.45),
        (r"facture\b(?!\s*(?:a\s*:?\s*[a-z]|date|de\s*la|totale))", 0.45),
        (r"(?:no|n\s?°|nº|n|numero)\.?\s*(?:de\s*)?(?:la\s*)?note\s*de\s*credit", 1.0), (r"credit\s*(?:note|memo)\b(?!\s*(?:date|total))", 0.6), (rf"ref(?:erence)?\.?\s*{_NO}?", 0.3), (r"(?:no\b\.?|n\s?°|nº|#)(?=\s*:?\s*[a-z]{0,3}-?\d)", 0.35),
        # A credit note's title with its number: "Note de crédit 051527-CR", "Avis de crédit AC-104".
        (r"(?:note|avis)\s*de\s*credit(?=\s*[:#]?\s*[a-z]{0,4}-?\d)", 0.6),
    ],
    "po_number": [
        (r"p\.?\s?o\.?(?![-\d])\s*(?!box\b|b\.?\s?p)(?:no\b\.?|#|number|n\s?°)?", 0.9), (r"purchase\s*order(?:\s*" + _NO + ")?", 1.0),
        (r"(?:your|customer|cust\.?|client)\s*(?:order|p\.?\s?o\.?)(?:\s*" + _NO + ")?", 0.95),
        (r"(?:votre\s*)?bon\s*de\s*commande(?:\s*" + _NO + ")?", 1.0), (r"(?:no|n\s?°|nº|n)\.?\s*(?:de\s*)?(?:bon\s*de\s*)?commande", 0.95),
        (r"commande\s*(?:client|no|n\s?°)", 0.8), (r"order\s*ref(?:erence)?\b\.?", 0.7), (r"votre\s*(?:commande|bon)", 0.95), (r"b\.\s?c\.(?!\s*(?:pst|v\d))\s*(?:#|no\b\.?|n\s?°)?", 0.8),
    ],
    "invoice_date": [
        (r"invoice\s*date", 1.0), (r"date\s*(?:of\s*)?(?:issue|invoice)", 1.0), (r"issue\s*date", 0.95),
        (r"billing\s*date", 0.95), (r"bill\s*date", 0.9), (r"date\s*de\s*(?:la\s*)?factur(?:e|ation)", 1.0),
        (r"date\s*d'?\s*emission", 0.95), (r"statement\s*date", 0.8), (r"date\s*de\s*la\s*note", 0.8),
        (r"inv\.?\s*date", 0.95), (r"date\s*(?:issued|of\s*issue)", 0.95), (r"issued(?:\s*on)?\b", 0.85), (r"issue\s*date", 0.95),
        (r"credit\s*(?:note|memo)\s*date", 0.95), (r"credit\s*date", 0.95), (r"date\s*du\s*credit", 0.9), (r"dated?\b(?!\s*(?:due|d'?echeance|de\s*commande|d'?expedition|de\s*livraison|du\s*service))", 0.55),
    ],
    "due_date": [
        (r"due\s*date", 1.0), (r"date\s*due", 0.95), (r"payment\s*due(?:\s*date)?", 0.9), (r"pay(?:able)?\s*by", 0.8),
        (r"(?:date\s*d'?\s*)?echeance", 1.0), (r"date\s*limite(?:\s*de\s*paiement)?", 0.9), (r"due\s*on\b(?!\s*(?:receipt|demand))", 0.8),
        (r"a\s*payer\s*avant\s*le", 0.85), (r"payer\s*avant", 0.8), (r"please\s*pay\s*by", 0.95),
        (r"payable\s*avant(?:\s*le)?", 0.9), (r"due\b(?!\s*(?:to|from|upon|on\s*receipt))", 0.7),
    ],
    "subtotal": [
        (r"sub\s*-?\s*total", 1.0), (r"sous\s*-?\s*total", 1.0), (r"net\s*amount", 0.9), (r"montant\s*net", 0.9),
        (r"total\s*(?:before|excl\.?|excluding|hors)\s*(?:tax(?:es)?|taxes)", 0.95), (r"amount\s*before\s*tax", 0.95),
        (r"net\s*total", 0.85), (r"total\s*ht\b", 0.9), (r"total\s*(?:avant|sans)\s*taxes?", 0.95),
        (r"total\s*partiel", 0.9), (r"merchandise\s*total", 0.8), (r"total\s*services", 0.5),
        (r"total\s*(?:of\s*)?(?:fees|charges|services|goods|merchandise|labou?r|materials)\b", 0.65),
    ],
    "hst_amount": [(rf"(?:[a-z]{{2}}\s*)?(?:{_A('gst')}\s*/\s*)?{_A('hst')}", 1.0), (rf"(?:{_A('tps')}\s*/\s*)?{_A('tvh')}", 1.0), (r"harmoni[sz]ed\s*sales\s*tax", 1.0)],
    "gst_amount": [(rf"(?:federal\s+)?{_A('gst')}(?!\s*/\s*{_A('hst')})", 1.0), (rf"{_A('tps')}(?!\s*/\s*{_A('tvh')})", 1.0), (r"goods\s*and\s*services\s*tax", 1.0)],
    "pst_amount": [(rf"(?:[a-z]{{2}}\s*)?{_A('pst')}", 1.0), (rf"(?:[a-z]{{2}}\s*)?{_A('rst')}", 1.0), (rf"{_A('tvp')}", 1.0), (r"provincial\s*sales\s*tax", 1.0),
                   (r"retail\s*sales\s*tax", 1.0)],
    "qst_amount": [(rf"(?:qc\s+)?{_A('qst')}", 1.0), (rf"{_A('tvq')}", 1.0), (r"quebec\s*sales\s*tax", 1.0)],
    "other_charges": [
        (r"(?:freight|shipping|delivery|handling|transport|livraison|frais\s*de\s*(?:livraison|transport|manutention)|"
         r"environmental\s*(?:fee|levy|handling)|eco\s*-?\s*fees?|ecofrais|fuel\s*surcharge)", 0.9),
        (r"(?:volume\s*|promotional\s*|early\s*payment\s*)?discount|escompte|rabais|remise", 0.9),
    ],
    "tax_total": [
        (r"total\s*(?:sales\s*)?tax(?:es)?\b", 1.0), (r"tax(?:es)?\s*total", 0.95), (r"total\s*des\s*taxes", 1.0),
        (r"taxes\b(?!\s*incl)", 0.6), (r"(?:[a-z]{2,3}\s+)?(?:state\s*|county\s*|city\s*|local\s*)?sales\s*tax\b", 0.85), (r"tax\s*(?:amount)?\s*$", 0.55), (r"tax\s*:", 0.6), (r"tax\s*[(@]?\s*\d{1,2}(?:[.,]\d{1,3})?\s*%", 0.85),
    ],
    "grand_total": [
        (r"(?:invoice\s*)?total\s*(?:amount\s*)?(?:due|payable)", 1.0), (r"amount\s*due", 0.95), (r"balance\s*due", 0.85),
        (r"total\s*a\s*payer", 1.0), (r"montant\s*(?:total|du|a\s*payer)", 1.0), (r"grand\s*total", 1.0),
        (r"invoice\s*total", 1.0), (r"total\s*de\s*la\s*facture", 1.0), (r"total\s*(?:cad|usd|\$)", 0.95),
        (r"net\s*payable", 0.9), (r"please\s*pay", 0.85), (r"total\s*(?:amount|invoice)", 0.95),
        (r"credit\s*total", 0.9), (r"amount\s*credited", 1.0), (r"total\s*credited", 1.0), (r"montant\s*credite", 1.0), (r"total\s*(?:du\s*)?credit", 0.9), (r"total\b", 0.75),
    ],
    "payment_terms": [
        (r"payment\s*terms", 1.0), (r"terms(?:\s*of\s*payment)?\b", 0.9), (r"conditions\s*(?:de\s*)?(?:paiement|reglement)", 1.0),
        (r"modalites\s*(?:de\s*)?paiement", 1.0), (r"conditions\b", 0.6), (r"termes\b", 0.8),
        (r"modalites(?!\s*(?:(?:de|du)(?![a-z])|d\s*'))", 0.8),  # "Modalités : Net 30", not "Modalités de livraison"
    ],
}  # fmt: skip

# Labels whose value is NOT the field: a reading next to one of these loses the field's label match.
DISTRACTORS: dict[str, list[str]] = {
    "invoice_date": [r"order\s*date", r"p\.?\s?o\.?\s*date", r"purchase\s*order\s*date", r"date\s*(?:shipped|ordered|delivered|received|paid|printed)",
                     r"date\s*of\s*(?:order|shipment|delivery|service|supply)", r"invoice\s*period", r"billing\s*period", r"ship(?:ping|ment)?\s*date", r"due\s*date", r"date\s*due", r"delivery\s*date",
                     r"date\s*de\s*commande", r"date\s*d'?\s*expedition", r"date\s*de\s*livraison", r"echeance",
                     r"service\s*(?:date|period)", r"period", r"periode", r"date\s*limite", r"payment\s*due",
                     r"date\s*de\s*la\s*commande", r"quote\s*date", r"date\s*de\s*soumission", r"printed", r"imprime"],
    "invoice_number": [r"inv\s?-\s?\d",  # "INV -4471": OCR's space in the number "INV-4471", not a label
                       r"invoice\s*date", r"invoice\s*total", r"invoice\s*amount", r"customer\s*(?:no|#|number|id)",
                       r"account\s*(?:no|#|number)", r"no\s*de\s*client", r"no\s*de\s*compte", r"client\s*(?:no|#)",
                       r"quote", r"soumission", r"order", r"commande", r"p\.?o\.?\b", r"phone", r"tel", r"fax",
                       r"date",
                       # A credit note's reference to the invoice it credits is not its own number.
                       r"facture\s*(?:originale|d'?\s*origine)", r"original\s*invoice", r"applies\s*to", r"against\s*invoice",
                       r"re(?:f\.?|:)?\s*invoice", r"ref\.?\s*(?:de\s*(?:la\s*)?)?facture"],
    "po_number": [r"po\s?-\s?\d", r"p\.?\s?o\.?\s*box", r"p\.?\s?o\.?\s*date", r"date\s*(?:du\s*)?(?:b\.?\s?c\.?|bon)", r"our\s*(?:order|ref)", r"notre\s*(?:commande|reference)", r"packing\s*slip", r"bon\s*de\s*livraison", r"c\.?\s?p\.?\s*\d", r"case\s*postale", r"sales\s*order", r"order\s*date",
                  r"date\s*de\s*commande"],
    "grand_total": [r"sub\s*-?\s*total", r"sous\s*-?\s*total", r"total\s*(?:tax|taxes|des\s*taxes)", r"previous",
                    r"solde\s*(?:precedent|anterieur)", r"payments?\s*(?:received|recu)", r"paiements?\s*recus?",
                    r"total\s*(?:qty|quantity|quantite|items|articles|hours|heures|weight|poids)", r"discount",
                    r"escompte", r"remise", r"credit\s*applied", r"total\s*ht\b", r"total\s*(?:before|avant|hors|excl)",
                    r"net\s*total", r"total\s*partiel", r"total\s*(?:of\s*)?(?:fees|charges|services|goods|merchandise|labou?r|materials)\b"],
    "subtotal": [],
    "tax_total": [r"tax\s*(?:id|reg|registration|no|number|#)", r"taxable", r"taxe?s?\s*incl", r"before\s*tax",
                  r"avant\s*taxes", r"hors\s*taxes", r"excl", r"exempt"],
}  # fmt: skip

_REG_WORDS = re.compile(r"\b(reg|registration|regist|no|number|num|#|n\s?°|nº|inscription|bn|business)\b|#", re.I)
_SPLIT_BN = re.compile(r"\d{3,}[\d\s.-]*\s*R\s*[TP]\b", re.I)
_PERCENT = re.compile(
    r"\d+(?:[.,]\d+)?\.?\s*%|@\s*\d{1,2}(?:[.,]\d{1,3})?\s*[%8]?(?![\d.,])|[(（]\s*\d{1,2}(?:\s*%|8|[.,]\d{1,3}\s*%|[.,]\d{3}8)\s*[)）]"
)  # 13%, @ 13%, (13%), OCR's "@138" / "(138)" / "（9,9758）", and a speck read as a period before the sign ("9,975.%")


@dataclass
class _LabelHit:
    field: str
    line: Line
    strength: float
    end: int  # character offset in the line text where the value may start
    label_text: str
    start: int = 0  # where the label's box starts: a label after another label on the line starts there


def _compile() -> dict[str, list[tuple[re.Pattern[str], float]]]:
    # A label ends where letters end, even when OCR glued it to its value ("Dated25/05/2025", "No.033"):
    # every word boundary in the table means "no letter follows".
    return {f: [(re.compile(rx.replace(r"\b", r"(?![a-z])")), s) for rx, s in pats] for f, pats in LABELS.items()}


_LABELS = _compile()
_DISTRACT = {f: [re.compile(rx) for rx in pats] for f, pats in DISTRACTORS.items()}


_OCR_LABEL_GAP = re.compile(r"(?<=[a-z]{2})[.·_-](?=[a-z]{2})")
_OCR_LABEL_ONE = re.compile(r"(?<=[a-z]{3})1(?![\d.,])")  # a 1 OCR read for the l ending a word: "tota1", "subtota1"
_OCR_WORD_SPECK = re.compile(r"(?<=[a-z]{2})[.·]\s+(?=[a-z]{2})")


def _label_hits(line: Line) -> list[_LabelHit]:
    """Labels that start a line or follow a separator inside it ("... | Date: ...")."""
    # OCR puts dots, dashes or middots between label words ("Total.before tax", "Bon·de commande").
    text = _OCR_LABEL_ONE.sub("l", _OCR_LABEL_GAP.sub(" ", plain(line.text)))  # OCR's "Tota1" (same length)
    starts = [0] + [m.end() for m in re.finditer(r"(?:\s{2,}|\s\|\s|[,;]\s)", text)]
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
    return hits


_NEXT_WORD = re.compile(r"\s+(?=[a-z])")


def _hits_at(line: Line, text: str, starts: list[int], chained: set[int] | None = None) -> list[_LabelHit]:
    hits: list[_LabelHit] = []
    for field, pats in _LABELS.items():
        for rx, strength in pats:
            for st in starts:
                m = rx.match(text, st)
                if not m or m.end() == st:
                    continue
                # A label must end on a word boundary (avoids "Totalement", "Dated").
                # (A label glued to a number is fine: OCR drops spaces, "GST5%", "Net30".)
                if m.end() < len(text) and text[m.end() - 1].isalpha() and text[m.end()].isalpha():
                    continue
                end = m.end()
                # "Invoice No. / No facture: 123": the value follows the second (translated) label.
                for _ in range(2):
                    sep = re.match(r"\s*[/-]\s*", text[end:])
                    if not sep:
                        break
                    nxt = next(
                        (
                            m2
                            for rx2, _s in pats
                            if (m2 := rx2.match(text, end + sep.end())) and m2.end() > end + sep.end()
                        ),
                        None,
                    )
                    if nxt is None:
                        break
                    end = nxt.end()
                label_text = text[st:end]
                box_start = st if chained and st in chained else 0
                if _distracted(field, text[st:]):
                    continue
                if field == "hst_amount" and "/" in label_text:
                    for hit_field in _combined_tax_fields(text):
                        hits.append(_LabelHit(hit_field, line, strength, end, label_text, box_start))
                else:
                    hits.append(_LabelHit(field, line, strength, end, label_text, box_start))
                break
            else:
                continue
            break
    return hits


def _combined_tax_fields(text: str) -> tuple[str, ...]:
    """A "GST/HST" label holds GST at 5% and HST at 13-15%. The printed rate tells which; without one
    both are proposed and the totals solver picks the one whose rate fits the subtotal."""
    m = _PERCENT.search(text)
    if m:
        num = re.search(r"\d+(?:[.,]\d+)?", m.group(0)).group(0)
        if "%" not in m.group(0) and num in ("58", "138", "148", "158"):
            num = num[:-1]  # OCR's "8" for the percent sign: "@58" / "(138)" for 5% / 13%
        rate = float(num.replace(",", "."))
        return ("gst_amount",) if abs(rate - 5.0) < 0.01 else ("hst_amount",)
    return ("hst_amount", "gst_amount")


def _distracted(field: str, text_from_label: str) -> bool:
    text_from_label = _OCR_WORD_SPECK.sub(" ", text_from_label)  # OCR's "Date. limite de paiement"
    for rx in _DISTRACT.get(field, []):
        if rx.match(text_from_label):
            return True
    return False


# ---------------------------------------------------------------- value extraction


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


_SEP = re.compile(r"^[\s:#.\-–—=|]*(?:(?:no|n°|nº|#|number)\b\.?\s*[:#]?\s*)?")
_ID_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/.]*[A-Za-z0-9]|[A-Za-z0-9]")


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
    elif re.fullmatch(r"\d{1,3}", value) and re.fullmatch(r"(?: \d{3})+\s*", rest[b:]):
        # "No de facture 123 456": a number printed in groups of three, alone after its label, is one number.
        value = rest[a:].rstrip()
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


def _value_reading(field: str, line: Line, start: int, base: float, method: str) -> Reading | None:
    """Read ``field`` from ``line.text[start:]``."""
    text = line.text
    rest = text[start:]
    if field in ("invoice_number", "po_number"):
        got = _id_from(rest, start)
        if not got:
            return None
        value, a, b = got
        if _looks_like_phone_or_postal(value) or parse_dates(value) or _MONTH_DAY.match(plain(value)):
            return None
        if any(da <= a - start < db for _d, da, db, _amb in parse_dates(rest)):
            return None  # the day of a date ("30 Jun 2025"), not a number
        if value.isdigit() and _STREET.search(text[b:]):
            return None  # a street number ("4833 Barrington St", "9777, boul. des Laurentides"), not an ID
        if re.fullmatch(r"(?i)(?:unit|suite|ste|bureau|apt|room|local|piece|box)\s*#?\s*\d+[a-z]?", value):
            return None  # part of an address ("Unit 7")
        if value.translate(_DIGIT_LOOKALIKES).strip("#:.-").casefold() in _LABEL_WORDS:
            return None  # a label OCR misread ("INVO1CE"), not its value
        if re.fullmatch(r"(?i)page-?\d{1,3}", value):
            return None  # "Page - 1" (OCR's spaces beside the hyphen joined), not a number
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
            value = _ocr_id_fix(_unglue_label(value))
        return Reading(field, value, value, _boxes(words), base * _min_conf(words), method)
    if field in ("invoice_date", "due_date"):
        found = parse_dates(rest, prefer_day_first=_DAY_FIRST.get())
        if not found:
            return None
        d, a, b, ambiguous = found[0]
        if a > 30:  # the date must be close to its label
            return None
        words = _span_words(line, start + a, start + b)
        raw = rest[a:b]
        return Reading(field, d.isoformat(), raw, _boxes(words), base * (0.6 if ambiguous else 1.0) * _min_conf(words),
                       method + ("-ambiguous" if ambiguous else ""))  # fmt: skip
    if field in AMOUNT_FIELDS:
        cleaned = _PERCENT.sub(lambda m: " " * len(m.group(0)), rest)
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
        return Reading(field, value, value, _boxes(words), base * _min_conf(words), method)
    return None


def _amounts_in(text: str) -> list[tuple[float, int, int]]:
    from .normalize import find_amounts

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
    (re.compile(r"(\d{1,2})\s*%?[\s.]*(\d{1,2})[\s,.;]*net[\s.:]*(\d{1,3})(?!\d)", re.I), "{0}% {1} Net {2}"),
    (re.compile(r"\bnet[\s.:]*(\d{1,3})(?!\d)", re.I), "Net {0}"),
    (re.compile(r"\bn\s*/?\s*(\d{1,3})\b", re.I), "Net {0}"),
    (re.compile(r"(?<!\d)(\d{1,3})\s*(?:days|jours|j\b)", re.I), "Net {0}"),
    (re.compile(r"\bdue[\s.]*(?:up)?on[\s.]*receipt\b|payable[\s.]*(?:a|à|sur|des|dès)[\s.]*(?:la[\s.]*)?r[ée]ception|on[\s.]*receipt", re.I), "Due on receipt"),  # a speck between words: "on.receipt"
]  # fmt: skip


def _terms(text: str) -> str | None:
    # OCR's full-width comma ("2%10，Net30") and a speck read as a middle dot ("Net·30")
    text = unicodedata.normalize("NFKC", text).replace("·", " ")
    for rx, fmt in _TERMS:
        m = rx.search(text)
        if m:
            return fmt.format(*m.groups())
    return None


# ---------------------------------------------------------------- spatial search

MAX_RIGHT = 0.5  # fraction of page width
BELOW_LINES = 3.2


def _right_of(lines: list[Line], line: Line) -> list[Line]:
    out = []
    for other in lines:
        if other is line or other.box.page != line.box.page or other.box.x0 < line.box.x1 - 0.002:
            continue
        overlap = min(line.box.y1, other.box.y1) - max(line.box.y0, other.box.y0)
        if overlap > 0.5 * min(line.box.height, other.box.height) and other.box.x0 - line.box.x1 < MAX_RIGHT:
            out.append(other)
    return sorted(out, key=lambda ln: ln.box.x0)


def _below(lines: list[Line], line: Line, label_box: Box) -> list[Line]:
    out = []
    h = max(line.box.height, 0.008)
    for other in lines:
        if other is line or other.box.page != line.box.page or other.box.y0 < line.box.y1 - 0.2 * h:
            continue
        if other.box.y0 - line.box.y1 > BELOW_LINES * h:
            continue
        overlap = min(label_box.x1, other.box.x1) - max(label_box.x0, other.box.x0)
        aligned = abs(other.box.x0 - label_box.x0) < 0.03 or abs(other.box.cx - label_box.cx) < 0.04
        if overlap > 0 or aligned:
            out.append(other)
    return sorted(out, key=lambda ln: ln.box.y0)


def _under_another_label(lines: list[Line], value: Line, label: Line) -> bool:
    """``value`` sits right under a label of its own (a header grid: a row of labels over a row of values), not
    ``label``: on a scan, where the rows are a little off, it can look level with the label on its left."""
    h = max(value.box.height, 0.008)
    for other in lines:
        if other is value or other is label or other.box.page != value.box.page:
            continue
        gap = value.box.y0 - other.box.y1
        if not -0.3 * h <= gap <= 1.2 * h or other.box.cy >= value.box.cy:
            continue
        # In the same row of labels as ``label``, printed as small (a title or a heading higher up is not a grid's
        # label)...
        if abs(other.box.cy - label.box.cy) > 0.8 * label.box.height or other.box.height > 1.5 * label.box.height:
            continue
        # ...and over the value, as a grid prints them: left edges or centres lined up.
        if abs(other.box.x0 - value.box.x0) >= 0.03 and abs(other.box.cx - value.box.cx) >= 0.04:
            continue
        if _label_hits(other) or _LABEL_SHAPE.search(plain(other.text).strip()):
            return True
    return False


def _label_box(hit: _LabelHit) -> Box:
    words = _span_words(hit.line, hit.start, hit.end)
    return union_all([w.box for w in words]) or hit.line.box


def _registration_line(line: Line, hit: _LabelHit) -> bool:
    after = line.text[hit.end :]
    return bool(
        _REG_WORDS.search(plain(after[:12]))
        or find_gst_numbers(after)
        or find_qst_numbers(after)
        or find_pst_numbers(f"PST {after[:40]}")
    )


def _table_row(lines: list[Line], line: Line) -> bool:
    """A line items row: two or more numbers to its right (quantity, price, amount), where a charge in the totals
    prints one."""
    numbers = 0
    for other in lines:
        if other is line or other.box.page != line.box.page or other.box.x0 < line.box.x1 - 0.002:
            continue
        overlap = min(line.box.y1, other.box.y1) - max(line.box.y0, other.box.y0)
        if overlap > 0.5 * min(line.box.height, other.box.height) and _value_reading("subtotal", other, 0, 1.0, "x"):
            numbers += 1
    return numbers >= 2


def _own_label(line: Line, field: str) -> bool:
    return any(h.field == field for h in _label_hits(line))


def _after_another_label(line: Line, got: Reading) -> bool:
    """``got``, read from ``line`` for a label on another line, comes after a label of its own on ``line``
    ("Delivery Date: Sep 20, 2026"): it is that label's value."""
    at = line.text.find(str(got.raw))
    before = plain(line.text[: max(at, 0)]).strip()
    if at <= 0 or not before:
        return False
    return bool(_LABEL_SHAPE.search(before) or before.endswith(":") or any(
        h.field != got.field and h.end <= at for h in _label_hits(line)))  # fmt: skip


def _labelled(field_hits: list[_LabelHit], lines: list[Line]) -> list[Reading]:
    """For each label: the value after it on the same line, else just right of it, else just below it
    (header grids print a row of labels over a row of values)."""
    out: list[Reading] = []
    for hit in field_hits:
        field, line = hit.field, hit.line
        inline = _value_reading(field, line, hit.end, 0.95 * hit.strength, "label-inline")
        if inline:
            out.append(inline)
            continue
        if field in AMOUNT_FIELDS and _registration_line(line, hit):
            continue  # "GST/HST Reg. No.: 988130969 RT0003": the number's label, not the tax amount's
        if field == "other_charges" and _table_row(lines, line):
            continue  # "Shipping cartons 18x12x12 | 100 | 47.04 | 4,704.00": a product, not the freight
        got = None
        for other in _right_of(lines, line)[:2]:
            if _own_label(other, field):
                break  # "Inv 7" beside or under a title: that line's own label reads its value
            if (_label_hits(other) or _LABEL_SHAPE.search(plain(other.text).strip())) and not _value_reading(
                field, other, 0, 1.0, "x"
            ):
                break  # the next thing to the right is another label ("Ship Date"): look below instead
            if _under_another_label(lines, other, line):
                break  # a header grid's value, under its own label ("Order No." over "SO-28797"): look below
            got = _value_reading(field, other, 0, 0.9 * hit.strength, "label-right")
            if got and _after_another_label(other, got):
                got = None
                break  # "Delivery Date: Sep 20, 2026": that label's value, not this one's
            if got:
                got.score *= 1.0 - min(other.box.x0 - line.box.x1, 0.4) * 0.5
                break
        if got is None:
            lb = _label_box(hit)
            for other in _below(lines, line, lb)[:3]:
                if _own_label(other, field):
                    break
                got = _value_reading(field, other, 0, 0.85 * hit.strength, "label-below")
                if got and _after_another_label(other, got):
                    got = None
                    break  # "Due Date: Upon Receipt" over "Delivery Date: Sep 20, 2026": not the due date
                if got:
                    break
                if _label_hits(other) or _LABEL_SHAPE.search(plain(other.text).strip()):
                    break  # the next row down is another label ("Account #"): this label has no value under it
        if got is not None:
            out.append(got)
    return out


# ---------------------------------------------------------------- pattern-only fields


# The customer's own numbers, also as OCR prints the words ("C1ient", "Cust0mer").
_CUSTOMER_WORD = re.compile(r"c[l1i|]ient|cust[o0]mer|y[o0]ur|v[o0]tre|acheteur|buyer")


def _registration_numbers(layout: DocLayout) -> dict[str, list[Reading]]:
    """The registration numbers printed. One printed for the customer (in the bill-to block, after "Client:") is
    read "+bill-to" (``BILL_TO_READING``): it may be the customer's, and is never verified on its own."""
    gst: list[Reading] = []
    qst: list[Reading] = []
    pst: list[Reading] = []
    for line in layout.lines():
        text = line.text
        lower = plain(text)
        bill_to = _in_bill_to(layout, line)
        for value, a, b, valid in find_gst_numbers(text):
            words = _span_words(line, a, b)
            score = (0.95 if valid else 0.55) * _min_conf(words)
            near = lower[max(0, a - 40) : a]
            if re.search(r"gst|hst|tps|tvh|bn\b|business|entreprise|reg", near):
                score = min(1.0, score + 0.03)
            if _CUSTOMER_WORD.search(near):
                continue  # the customer's own number, printed for them
            if bill_to:
                score *= 0.35
            if line.box.page == 1 and line.box.cy < 0.3:
                score = min(1.0, score + 0.02)
            method = "pattern" + (BILL_TO_READING if bill_to else "")
            gst.append(Reading("gst_hst_registration_number", value, text[a:b], _boxes(words), score, method))
        for value, a, b in find_qst_numbers(text):
            words = _span_words(line, a, b)
            score = 0.95 * _min_conf(words)
            near = lower[max(0, a - 40) : a]
            customer = bool(_CUSTOMER_WORD.search(near)) or bill_to
            if customer:
                score *= 0.35
            method = "pattern" + (BILL_TO_READING if customer else "")
            qst.append(Reading("qst_registration_number", value, text[a:b], _boxes(words), score, method))
        for value, a, b in find_pst_numbers(text):
            words = _span_words(line, a, b)
            score = 0.9 * _min_conf(words)
            customer = bool(_CUSTOMER_WORD.search(lower[max(0, a - 40) : a])) or bill_to
            if customer:
                score *= 0.35
            method = "pattern" + (BILL_TO_READING if customer else "")
            pst.append(Reading("pst_registration_number", value, text[a:b], _boxes(words), score, method))
    return {"gst_hst_registration_number": gst, "qst_registration_number": qst, "pst_registration_number": pst}


_BILL_TO = re.compile(r"^(bill(?:ed)?\s*to|sold\s*to|ship\s*to|invoice\s*to|deliver\s*to|for\s*:|to\s*:|service\s*address|"
                      r"account\s*(?:holder|name)|customer|client|factur[ée]\s*a|vendu\s*a|livr[ée]?\s*a|"
                      r"expedier\s*a|adresse\s*de\s*livraison|attention|attn)(?:\b|(?<=:))")  # fmt: skip


def _bill_to_regions(layout: DocLayout) -> list[Box]:
    """Rough areas holding the customer's address (below a 'Bill to'-style label)."""
    regions = []
    for line in layout.lines():
        if _BILL_TO.match(plain(line.text)):
            b = line.box
            regions.append(Box(b.page, b.x0 - 0.01, b.y0 - 0.002, min(1.0, b.x0 + 0.42), b.y1 + 0.12))
    return regions


_REGIONS: ContextVar[tuple[DocLayout, list[Box]] | None] = ContextVar("bill_to_regions", default=None)


def _in_bill_to(layout: DocLayout, line: Line) -> bool:
    cached = _REGIONS.get()
    if cached is None or cached[0] is not layout:  # identity, not id(): ids are reused after a layout is freed
        cached = (layout, _bill_to_regions(layout))
        _REGIONS.set(cached)
    for r in cached[1]:
        b = line.box
        if b.page == r.page and r.x0 <= b.cx <= r.x1 and r.y0 <= b.cy <= r.y1:
            return True
    return False


_COMPANY = re.compile(r"\b(inc|lnc|ltd|ltee|itee|limited|limitee|llc|llp|lp|l\.p|ulc|plc|corp|corporation|co\.|company|cie|enr|"
                      r"s\.?e\.?n\.?c|products|"
                      r"group|groupe|services|solutions|supply|supplies|industries|technologies|consulting|"
                      r"distribution|holdings|partners|associates|enterprises|entreprises)\b\.?", re.I)  # fmt: skip
_NOT_NAME = re.compile(r"^(invoice|facture|credit\s*memo|debit\s*(?:note|memo)|avis\s*de\s*credit|(?:sales|tax|commercial|proforma|pro\s*forma)\s*invoice|please\s*pay|your\b|votre\b|credit\s*note|note\s*de\s*credit|statement|page\b|date|bill|"
                       r"ship|sold|remit|total|amount|description|qty|www\.|http|tel|phone|fax|email|courriel|"
                       r"gst|hst|tps|tvq|qst|po\b|p\.o|account|terms|due|original|copy|duplicata|paid|"
                       r"customer|client|attention|attn|from|de\s*:|to\s*:|a\s*:|bon\s*de)", re.I)  # fmt: skip
_REMIT = re.compile(r"(?:make\s*(?:all\s*)?cheques?\s*payable\s*to|payable\s*(?:to|a\s*l'ordre\s*de)|"
                    r"remit\s*(?:payment\s*)?to|pay\s*to\s*the\s*order\s*of|libeller\s*(?:le|les)?\s*cheques?\s*a)\s*:?\s*",
                    re.I)  # fmt: skip


_LEGAL_FORMS = {"LP", "LLP", "LLC", "INC", "LTD", "CO", "PLC", "SA", "SAS", "ULC", "SRL"}


_LOGO_MARKS = "■□▪▫◆◇◊●○•·►▶★☆|*~"  # a logo's mark as OCR reads it, at the start of the name's line


def _without_logo_initials(words: list[Word]) -> list[Word]:
    """Drop a logo's initials printed on the name's line: "BT Bluewater Telecom" -> "Bluewater Telecom"; and a mark
    with no letter or digit in it, OCR's reading of a logo: "■ Birchmount Logistics Limited", "•Acme Supply"."""
    words = list(words)
    while words and not any(ch.isalnum() for ch in words[0].text):
        words.pop(0)
    while words and not any(ch.isalnum() for ch in words[-1].text):
        words.pop()
    if words and words[0].text[0] in _LOGO_MARKS:
        words[0] = Word(words[0].text.lstrip(_LOGO_MARKS + " "), words[0].box, words[0].conf, words[0].source)

    def initials_of(ws: list[Word], n: int) -> str:
        # Word starts, and capitals inside words OCR glued together ("BirchmountHydraulics").
        caps = [
            c
            for w in ws
            for i, c in enumerate(w.text)
            if c.isalpha() and (i == 0 or (c.isupper() and w.text[i - 1].islower()))
        ]
        return "".join(caps[:n]).upper()

    out = list(words)
    if len(out) > 2 and re.fullmatch(r"[A-Z]", out[-1].text) and out[-2].text.strip(".,").upper() in _LEGAL_FORMS:
        out = out[:-1]  # a logo's initial OCR read after the legal form: "Prairie Landscaping LP P"
    for i in (0, -1):
        if len(out) < 2:
            break
        tok = out[i].text.strip(".")
        # "LP" after "Lakeshore Plumbing" is the legal form, not the logo
        if 2 <= len(tok) <= 3 and tok.isalpha() and tok.isupper() and not (i == -1 and tok in _LEGAL_FORMS):
            rest = out[1:] if i == 0 else out[:-1]
            if initials_of(rest, len(tok)) == tok:
                out = rest
    return out


_CONTACT = re.compile(
    r"@|www|https?:|\.(?:com|ca|net|org|qc\.ca)\b|^(?:bureau|suite|unit|local|apt|room|piece)\s*\d|"
    r"^(?:issued|dated?|due|terms|conditions|page|tel|phone|fax|ph(?=\s*[.:#(\d])|p\.?\s?o\.?\s*date|podate|payment|"
    r"echeance|modalites|reference|ref|c/o|attn|attention|payments?\s+received|balance\s+(?:forward|due|brought))\b|"
    r":\s*$|^\d+(?:\s*,\s*|\s+)(?:[a-z]+\.?\s+)*(?:rue|boul|blvd|ave|av|st|rd|road|street|chemin|ch|route|hwy|dr|way|cres|pkwy)\b|"
    r"\s-\s(?:jan|feb|fev|mar|apr|avr|may|mai|jun|juin|jul|juil|aug|aou|sep|oct|nov|dec)"  # "Consulting services - September": a line item
)
# A field's label: "Order No.", "Quotation No.", "Delivery Date", "Numero de compte"; a table header.
_LABEL_SHAPE = re.compile(
    r"^(?:[a-z.'/]+\s*){1,3}(?:no\.?|n°|#|date|id|number)\s*:?$|^(?:numero|no|n°)\s+d[e']|"
    r"\b(?:description|designation|qty|quantite|quantity)\b"
)
_COLUMN_WORD = re.compile(r"^(?:net\s*\d+|code|item\s*code|part\s*(?:#|no\.?)|sku|uom|unit|ref\.?|#)$")
_POSTAL_CODE = re.compile(r"[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d|,\s*(?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)(?![a-z])")
_TITLE_PREFIX = re.compile(
    r"^(?:sales\s*|tax\s*|commercial\s*)?(?:invoice|facture)\s*(?:#|no\.?|n°)?\s*[a-z]{0,3}[\d/-]*\d\S*\s+"
)


def _vendor_names(layout: DocLayout) -> list[Reading]:
    if not layout.pages:
        return []
    page = layout.pages[0]
    out: list[Reading] = []
    heights = sorted((ln.box.height for ln in page.lines), reverse=True)
    tall = heights[min(2, len(heights) - 1)] if heights else 0.0
    customer_names = {plain(ln.text.strip()) for ln in layout.lines() if _in_bill_to(layout, ln)}
    # The line-items table starts below the header: nothing from it is the supplier's name.
    table_top = min((ln.box.y0 for ln in page.lines if _table_header_line(ln, page.lines)), default=1.0)
    for line in page.lines:
        text = line.text.strip()
        p = plain(text)
        title = _TITLE_PREFIX.match(p)
        if title and title.end() < len(text):  # "SALES INVOICE #26/41538 Fraser Valley Fasteners Co."
            line = Line(_span_words(line, title.end(), len(text)), line.box, text[title.end() :].strip())
            line = Line(line.words, union_all([w.box for w in line.words]) or line.box, line.text)
            text, p = line.text, plain(line.text)
        m = _REMIT.search(p)
        if m:
            name = text[m.end() :].strip(" :.,")
            if 2 < len(name) < 70 and not _NOT_NAME.match(plain(name)):
                words = _span_words(line, m.end(), len(text))
                out.append(Reading("vendor_name", name, name, _boxes(words), 0.9 * _min_conf(words), "remit-to"))
            continue
        if line.box.cy > 0.33 or line.box.y0 >= table_top - 0.002 or _in_bill_to(layout, line):
            continue
        if len(text) < 3 or len(text) > 70 or _NOT_NAME.match(p) or sum(c.isdigit() for c in text) > 3:
            continue
        if _POSTAL_CODE.search(text) or us_address(text) or _CONTACT.search(p):
            continue  # an address, e-mail or web line
        heading = (
            len(p.split()) <= 2
            and not _COMPANY.search(p)
            and (any(rx.match(p.strip()) for rx in _HEAD.values()) or _COLUMN_WORD.match(p.strip()))
        )
        if heading or ((_label_hits(line) or _LABEL_SHAPE.search(p)) and not _COMPANY.search(p)):
            continue  # a field's label ("Cust. P.O.#") or a column heading ("Code") is not a name
        letters = sum(c.isalpha() for c in text)
        if letters < 3:
            continue
        score = 0.35
        if _COMPANY.search(p):
            score += 0.35
        if line.box.height >= tall * 0.95:
            score += 0.15
        score += 0.12 * (1 - min(line.box.cy / 0.33, 1))
        if text.isupper() and len(text.split()) == 1:
            score -= 0.1
        if plain(text) in customer_names:
            score *= 0.3
        words = _without_logo_initials(line.words)
        if words != line.words:
            text = " ".join(w.text for w in words)
        if any(w.source not in _AS_PRINTED for w in words):
            text = _ocr_name_fix(text)
        out.append(Reading("vendor_name", text, text, [union_all([w.box for w in words])] if words else [line.box],
                           min(score, 0.95) * _min_conf(line.words), "top-of-page"))  # fmt: skip
    out.sort(key=lambda r: -r.score)
    return out


# ---------------------------------------------------------------- totals solver

_TAX_FIELDS = ("gst_amount", "hst_amount", "pst_amount", "qst_amount")
_TAX_BASE = re.compile(r"(?i)\b(?:on|sur)\s*\$?\s*$")  # "13% on $1,000.00", "5 % sur 1 000,00 $"
_RATES = {
    "gst_amount": (0.05,),
    "hst_amount": (0.13, 0.14, 0.15),
    "pst_amount": (0.06, 0.07, 0.08),
    "qst_amount": (0.09975,),
}


def _where(r: Reading) -> tuple:
    b = r.boxes[0] if r.boxes else None
    return (b.page, round(b.x0, 3), round(b.y0, 3)) if b else (id(r),)


def _solve_totals(cands: dict[str, list[Reading]], line_sum: float | None) -> dict[str, list[Reading]]:
    """Re-rank amount candidates so that the subtotal, taxes and total that add up come first."""
    top = {f: sorted(cands.get(f, []), key=lambda r: -r.score)[:4] for f in AMOUNT_FIELDS}
    subs = top["subtotal"] or [None]
    totals = top["grand_total"] or [None]
    tax_options = {f: [*top[f], None] for f in _TAX_FIELDS}
    charge_options = [*top["other_charges"][:2], None]
    # A sales tax that is no GST/HST/PST/QST ("WA Sales Tax 10.35%"): only its total is printed.
    total_tax_options = [*top["tax_total"][:2], None]
    best = None
    for sub, total, charge, total_tax in product(subs, totals, charge_options, total_tax_options):
        extra = charge.value if charge else 0.0
        for taxes in product(*tax_options.values()):
            present = [t for t in taxes if t is not None]
            if len({_where(t) for t in present}) < len(present):
                continue  # one printed amount ("GST/HST") is one tax
            if present and total_tax is not None:
                continue  # the total tax stands in only when no component tax is read
            score = (sub.score if sub else 0) + (total.score if total else 0) + sum(t.score for t in present)
            score += (charge.score if charge else 0.0) + (total_tax.score if total_tax else 0.0)
            bonus = 0.0
            if sub and total:
                tax_sum = sum(t.value for t in present) + (total_tax.value if total_tax else 0.0)
                if abs(sub.value + extra + tax_sum - total.value) <= 0.011:
                    # Subtotal = total with no tax is weaker evidence: one number printed twice.
                    taxed = len(present) + (total_tax is not None)
                    bonus += 3.0 + 0.5 * taxed if taxed else 2.5
                elif present and abs(sub.value - total.value) <= 0.011:
                    bonus -= 1.0  # a total equal to the subtotal while taxes are printed: wrong total
            if sub and line_sum is not None and abs(sub.value - line_sum) <= 0.011:
                bonus += 1.0
            if sub and sub.value:
                for f, t in zip(tax_options, taxes, strict=True):
                    if t is not None and any(
                        abs(t.value - r * base) <= max(0.02, 0.01 * abs(t.value))
                        for r in _RATES[f]
                        for base in {sub.value, sub.value + extra}  # taxed with or without the freight
                    ):
                        bonus += 0.3
            if best is None or score + bonus > best[0]:
                chosen_taxes = dict(zip(tax_options, taxes, strict=True))
                best = (score + bonus, sub, total, {**chosen_taxes, "other_charges": charge}, bonus)
    if best is None:
        return cands
    _, sub, total, taxes, bonus = best
    chosen = {"subtotal": sub, "grand_total": total, **taxes}
    out = dict(cands)
    taken = {_where(r) for f, r in chosen.items() if r is not None and f in _TAX_FIELDS}
    for f, r in chosen.items():
        if r is None:
            if bonus >= 3.0 and (f in _TAX_FIELDS or f == "other_charges"):
                out[f] = []  # the totals add up without this tax: what was read for it is not a tax
            elif f in _TAX_FIELDS:
                out[f] = [x for x in cands.get(f, []) if _where(x) not in taken]
            continue
        if bonus >= 3.0:
            r.score = min(1.0, r.score + 0.15)
            r.method += "+adds-up"
        rest = [x for x in cands.get(f, []) if x is not r]
        out[f] = [r, *sorted(rest, key=lambda x: -x.score)]
    return out


# ---------------------------------------------------------------- line items

_HEAD = {
    "description": re.compile(r"^(description|desc\.?|item|article|details|designation|produit|service|libelle|particulars)"),
    "quantity": re.compile(r"^(qty|quantity|qte|quantite|qte\.|units?|hrs?|hours|heures)\b"),
    "unit_price": re.compile(r"^(unit\s*price|price|rate|unit\s*cost|prix(\s*unit(?:aire|\.)?)?|taux|p\.?u\.?|cost)(?![a-z0-9])"),
    "amount": re.compile(r"^(amount|total(?:\s*price)?|montant|line\s*total|ext(?:ended)?\.?\s*(?:price|amount)?|value|valeur|prix\s*total|fees|honoraires|frais)\b"),
}  # fmt: skip
_TOTAL_LABELS = ("subtotal", "grand_total", "tax_total")  # the line table ends at these
_FIGURE = re.compile(r"[#(]?[\d.,:/-]*\d[\d.,:/-]*[)%]?")  # a word that is a number: "9", "(2)", "5%", "2026-01"


_NUMBER_CELL = re.compile(r"^[\s$€£¢()+\-–.,'%x×@#]*\d[\d\s$€£¢()+\-–.,'%x×@]*(?:[a-z]{1,3}\.?)?\s*$", re.I)
_CARRIED = re.compile(r"^(a\s*reporter|report\b|reporte|carried\s*forward|brought\s*forward|balance\s*forward|"
                      r"continued|suite|sub\s*-?\s*total|sous\s*-?\s*total|page\s*total)")  # fmt: skip
_CREDIT_MARK = re.compile(r"\d[.,]\d{2}cr", re.I)  # "342.18CR": a credit's mark run into its amount


def _printed_as_money(token: str) -> bool:
    """A line's amount printed as money, as OCR reads it too: "1530:00" (the point read as a colon) and "342.18CR"
    (a credit's mark run into it) are money; "5%" and "12" are not."""
    from .normalize import _ocr_amount_text

    fixed = _ocr_amount_text(token)
    return looks_like_money(fixed) or bool(_CREDIT_MARK.search(fixed))


_CREDIT_TITLE = re.compile(r"^(?:" + "|".join(CREDIT_TITLES) + r")(?![a-z])")  # as email triage reads them


def credit_titled(layout: DocLayout) -> bool:
    """The document calls itself a credit note ("CREDIT MEMO", "Note de crédit", "AVOIR" ...) at the top of its
    first page."""
    if not layout.pages:
        return False
    return any(ln.box.cy < 0.35 and _CREDIT_TITLE.match(plain(ln.text).strip()) for ln in layout.pages[0].lines)


def credit_printed_positive(layout: DocLayout) -> bool:
    """A credit memo (titled so at the top of its first page) whose subtotal and total are printed without a sign:
    its amounts are credits however they are printed, and AP posts credits as negative amounts. The subtotal's and
    total's own signs decide: one adjustment line printed negative ("Restocking fee -50.00") is that line's sign,
    not the document's. With no subtotal or total found, any amount printed with a sign means the signs are printed.
    A zero has no sign ("Balance Due $0.00": nothing left to pay), and a line that only looks like a total
    ("Previous Balance", "Payments received") is not the credit's own: neither votes."""
    if not credit_titled(layout):
        return False
    from .normalize import find_amounts

    def money(line: Line) -> list[float]:
        return [v for v, a, b in find_amounts(line.text) if looks_like_money(line.text[a:b])]

    totals: list[float] = []
    for page in layout.pages:
        for line in page.lines:
            p = plain(line.text).strip()
            if not any(rx.match(p) for f in ("subtotal", "grand_total") for rx, _ in _LABELS[f]):
                continue
            if any(rx.match(p) for rx in _DISTRACT["grand_total"]) and not any(
                rx.match(p) for rx, _ in _LABELS["subtotal"]
            ):
                continue  # "Previous Balance": a total's label, not this credit's total (a subtotal is one of them)
            # The amount on the label's line, or in the cells beside it on its row.
            row = [line] + sorted((o for o in page.lines if o is not line and abs(o.box.cy - line.box.cy) < 0.006
                                   and o.box.x0 >= line.box.x0), key=lambda o: o.box.x0)  # fmt: skip
            totals += [v for v in next((m for m in map(money, row) if m), [])[-1:] if v]
    if totals:
        return all(v > 0 for v in totals)
    return not any(v < 0 for line in layout.lines() for v in money(line))  # the signs are printed: read as they are


def _heading_name(text: str) -> str | None:
    """The column a heading names, by the longest heading it starts with ("Unit Cost" is the unit price, not a
    quantity in "Unit"; "Prix total" the amount, not a price); a heading printed in two languages ("Product /
    Service / Désignation") by its parts, when they name one column. None when it names none, or two."""
    longest = {name: m.end() for name, rx in _HEAD.items() if (m := rx.match(text))}
    if longest:
        top = max(longest.values())
        named = [name for name, end in longest.items() if end == top]
        return named[0] if len(named) == 1 else None
    if "/" not in text or any(rx.match(text) for f in _TOTAL_LABELS for rx, _ in _LABELS[f]):
        return None  # "Balance Due / Total à payer" is the invoice's total, not a column
    parts = {_heading_name(part.strip()) for part in text.split("/") if part.strip()} - {None}
    return parts.pop() if len(parts) == 1 else None


def _heading_cells(line: Line, taken: dict[str, Box]) -> list[tuple[str, Box]]:
    """The column headings a line segment holds, each with its box. Headings set close together read as one
    segment ("Qty Unit Price"): when the segment is nothing but two or more different headings, each gets the
    box of its own words, so a column's values are looked for under its heading and not the segment's middle. A
    heading printed in two languages is one heading ("Price / Taux Ext. Price / Prix total": the unit price, then
    the amount); words after a heading are more of it ("Price Each"); a second heading naming a column already named
    is another column without a name ("Quantity Unit Unit Cost": the unit of measure between the quantity and the
    price). A segment with a number in it is no heading ("Unit 9" is an address; "Rate per m3" can be)."""
    p = plain(line.text).strip()
    if any(_FIGURE.fullmatch(word) for word in p.split()):
        return []
    name = _heading_name(p)
    whole = [(name, line.box)] if name and name not in taken else []
    # (word or "/", its box): "Hrs/Qty" is "hrs", "/", "qty"; "UnitéRate" (two headings a PDF's text or OCR runs
    # together) "unite", "rate", each with its share of the word's box.
    tokens: list[tuple[str, Box]] = []
    for w in line.words:
        at = 0
        for piece in re.split(r"(?<=[a-zà-ÿ])(?=[A-Z])", w.text):
            share = w.box.width / max(len(w.text), 1)  # a piece's box: its share of the word's
            box = Box(w.box.page, w.box.x0 + at * share, w.box.y0, w.box.x0 + (at + len(piece)) * share, w.box.y1)
            tokens += [(part, box) for part in re.split(r"(/)", plain(piece)) if part.strip()]
            at += len(piece)
    words = [t for t, _ in tokens]
    if not 2 <= sum(t != "/" for t in words) <= 16 or "/" in (words[0], words[-1]):
        return whole
    cells: list[tuple[str, int, int]] = []  # (heading, first token, last token + 1); "" another column's heading
    extra = 0  # words of the last heading that are no heading themselves, not counting its other language's
    noise = 0  # such words, and headings naming a column already named, in the whole segment
    i = 0
    while i < len(words):
        joined = words[i] == "/"  # a heading's other language follows
        i += joined
        rest = " ".join(words[i:])
        ends = [len(" ".join(words[i : j + 1])) for j in range(i, len(words))]
        best = None
        for heading, rx in _HEAD.items():
            m = rx.match(rest)
            if m and m.end() in ends and (best is None or m.end() > best[1]):
                best = (heading, m.end())  # the longest heading here: "Unit Price", not "Unit"
        if best is None and cells:  # more words of the heading before: "Price Each", "Qty Shipped"
            extra += not joined
            noise += not joined
            if extra > 2:
                return whole  # a sentence that starts with a heading's word, not a run of headings
            cells[-1] = (cells[-1][0], cells[-1][1], i + 1)
            i += 1
            continue
        if best is None:
            return whole  # a segment that starts with no heading
        extra = 0
        n = ends.index(best[1]) + 1
        seen = any(c[0] == best[0] for c in cells)
        if joined and (cells[-1][0] == best[0] or (cells[-1][0] == "" and seen)):  # the other language
            cells[-1] = (cells[-1][0], cells[-1][1], i + n)
        else:  # (after a "/", another column: OCR runs "Qty/Qté Price Each/Taux" together)
            cells.append(("" if seen else best[0], i, i + n))
            noise += seen
        i += n
    columns = sum(1 for c in cells if c[0])
    if columns < 2 or noise > columns:  # (more words that are no heading than headings: a sentence)
        return whole
    out = []
    for heading, a, b in cells:
        box = union_all([box for _, box in tokens[a:b]])
        if heading and heading not in taken and box is not None:
            out.append((heading, box))
    return out


def heading_row(page: PageLayout) -> tuple[Line, list[Line], dict[str, Box]] | None:
    """The line table's column headings on the page: (the first line of their row, the row's lines, {column: heading
    box}). A row of headings names the amount and the description or the quantity; of several, the one naming the
    most columns (an address's "Unit 9" set level with "Amount Due" at the top of a bill names two). None when there
    is none."""
    named = {id(line) for line in page.lines if _heading_cells(line, {})}  # the lines that hold a heading at all
    best = None
    for line in page.lines:
        row = [other for other in page.lines if abs(other.box.cy - line.box.cy) < 0.006]
        cols: dict[str, Box] = {}
        for other in row:
            if id(other) in named:
                for name, box in _heading_cells(other, cols):
                    cols[name] = box
        if "amount" in cols and len(cols) >= 2 and ("description" in cols or "quantity" in cols):
            if best is None or len(cols) > len(best[2]):
                best = (line, row, cols)
    return best


_THREE_PLACES = re.compile(r"-?\d{1,3}([.,])\d{3}")  # "1.459": a price to three decimals, or 1,459
_SPACED_PAIR = re.compile(r"(\d{1,3})[   ](\d{3}[.,]\d{1,2})")  # "3 250,00": quantity 3 at 250,00
_SPACED_WHOLE = re.compile(r"(\d{1,3})[   ](\d{3})")  # "2 500": 2500 (French thousands), or 2 and 500


def spaced_whole(text: str) -> float | None:
    """A cell that is one whole number in one space group ("2 500", "6 000"): the number kept whole (2500).
    ``find_amounts`` reads it as two numbers (a quantity and a price); the line's amount tells which it is."""
    m = _SPACED_WHOLE.fullmatch(text.strip())
    return float(m.group(1) + m.group(2)) if m else None


def _qty_times_price(segs: list[Line], nums: list[tuple[Line, list[tuple[float, int, int]]]], amt_seg: Line | None,
                     amount: float) -> tuple[float, float] | None:  # fmt: skip
    """The quantity and unit price of a row whose columns did not give two that make its amount: two numbers
    left of the amount, in order, whose product is the amount. A number printed "1.459" may be a price to three
    decimals as well as 1459, and "3 250,00" a quantity and a price set close together; among readings that fit,
    those that read each separator as the amount's own decimal mark (or not) the way the amount does win."""
    amt_text = (amt_seg.text if amt_seg else "").strip()
    decimal = "," if re.search(r",\d{2}(?!\d)", amt_text) and not re.search(r"\.\d{2}(?!\d)", amt_text) else "."
    # (position, value, how unnatural the reading is)
    options: list[tuple[float, float, int]] = []
    k = 0
    for s in sorted(segs, key=lambda seg: seg.box.x0):
        found = next((f for seg, f in nums if seg is s), [])
        if s is amt_seg:
            found = found[:-1]  # the amount itself
        whole = spaced_whole(s.text) if s is not amt_seg and len(found) == 2 else None
        if whole is not None:
            options.append((k + 1, whole, 0))  # "2 500" kept whole, in the place of its first part
        for value, a, b in found:
            token = s.text[a:b].strip()
            k += 1
            options.append((k, value, 0))
            three = _THREE_PLACES.fullmatch(token)
            if three:
                as_decimal = round(value / 1000, 4)
                natural = three.group(1) == decimal
                options[-1] = (k, value, 1 if natural else 0)
                options.append((k, as_decimal, 0 if natural else 1))
            pair = _SPACED_PAIR.fullmatch(token)
            if pair:
                second = re.sub(r"[.,]", ".", pair.group(2))
                options.append((k, float(pair.group(1)), 1))
                options.append((k + 0.5, float(second), 1))
    # A credit's line (-200.00, "200.00 CR") prints its price with or without the sign: the price is a credit too,
    # unless the quantity carries the sign (-4 returned at 45.13).
    credit = amount < 0
    best = None
    for k1, q, cost1 in options:
        for k2, p, cost2 in options:
            signed = credit and q < 0 < p  # (a "-2" in "6205-2RS" is no quantity: a reading without one first)
            price = p if signed else -abs(p) if credit else p
            if k2 <= k1 or (q <= 0 and not signed) or abs(q * price - amount) > 0.011:
                continue
            if best is None or cost1 + cost2 + signed < best[0]:
                best = (cost1 + cost2 + signed, q, price)
    return (best[1], best[2]) if best else None


# How far around a table the table finder found its words are still taken: its box can sit a hair inside the first and
# last column, or just under the column headings (fractions of the page).
TABLE_MARGIN_X = 0.03
TABLE_MARGIN_TOP = 0.02
TABLE_MARGIN_BOTTOM = 0.01


def inside_tables(layout: DocLayout, tables: dict[int, list[Box]]) -> DocLayout:
    """``layout`` with, on each page where the table finder found tables, only the words inside them (their middle
    within ``TABLE_MARGIN_*`` of a table), put back into lines: text printed beside or between the rows is left out.
    A page where it found none keeps all its words."""
    from .layout import group_lines
    from .types import PageLayout

    pages = []
    for page in layout.pages:
        boxes = tables.get(page.number) or []
        if not boxes:
            pages.append(page)
            continue

        words = [w for w in page.words if _in_table(w, boxes)]
        pages.append(PageLayout(page.number, page.width, page.height, words, group_lines(words, page.number),
                                page.rotation, page.skew))  # fmt: skip
    return DocLayout(pages, layout.source)


def _in_table(w: Word, boxes: list[Box]) -> bool:
    return any(b.x0 - TABLE_MARGIN_X <= w.box.cx <= b.x1 + TABLE_MARGIN_X
               and b.y0 - TABLE_MARGIN_TOP <= w.box.cy <= b.y1 + TABLE_MARGIN_BOTTOM for b in boxes)  # fmt: skip


STRAIGHTEN_FROM = 0.002  # a scan tilted more than this (down per across: about a tenth of a degree) is straightened


def straightened(layout: DocLayout) -> DocLayout:
    """``layout`` with each tilted scan's words moved up or down to where they would be on a straight page
    (``PageLayout.skew``), then put back into lines: on a page scanned at 1.4 degrees, a row's description sits a
    whole row higher or lower than its amount at the other end of the table. Pages that aren't tilted are kept."""
    from .layout import group_lines
    from .types import PageLayout

    pages = []
    for page in layout.pages:
        if abs(page.skew) < STRAIGHTEN_FROM:
            pages.append(page)
            continue
        words = []
        for w in page.words:
            dy = -page.skew * (w.box.cx - 0.5)
            words.append(Word(w.text, Box(w.box.page, w.box.x0, w.box.y0 + dy, w.box.x1, w.box.y1 + dy), w.conf,
                              w.source))  # fmt: skip
        pages.append(PageLayout(page.number, page.width, page.height, words, group_lines(words, page.number),
                                page.rotation, page.skew))  # fmt: skip
    return DocLayout(pages, layout.source)


def _on_tilted_page(box: Box, skew: float) -> Box:
    """A box on the straightened page, back on the tilted one: the box around where it is printed."""
    a, b = skew * (box.x0 - 0.5), skew * (box.x1 - 0.5)
    return Box(box.page, box.x0, box.y0 + min(a, b), box.x1, box.y1 + max(a, b))


AMOUNT_COLUMN = 0.04  # a row's amount ends this close to the amount column's right edge (fraction of the page)


def _headerless_rows(page: PageLayout) -> list[LineReading]:
    """The rows of one table the table finder found whose column headings can't be read (OCR runs bilingual headings
    together): each row whose last cell is an amount, in the column where most rows end, down to the subtotal. The
    numbers before it are the quantity and unit price when they make the amount; the words are the description."""
    rows: dict[float, list[Line]] = {}
    for line in page.lines:
        key = next((k for k in rows if abs(k - line.box.cy) < 0.006), line.box.cy)
        rows.setdefault(key, []).append(line)
    ordered = [sorted(segs, key=lambda s: s.box.x0) for _, segs in sorted(rows.items())]

    def money(seg: Line) -> list[tuple[float, int, int]]:  # amounts printed as money: "1,250.00", "$12"
        return [(v, a, b) for v, a, b in _amounts_in(seg.text) if _printed_as_money(seg.text[a:b])]

    ends = [segs[-1].box.x1 for segs in ordered if len(segs) >= 2 and money(segs[-1])]
    if len(ends) < 2:
        return []
    right = statistics.median(ends)
    items: list[LineReading] = []
    for segs in ordered:
        p = plain(" ".join(s.text for s in segs))
        if items and any(rx.match(plain(s.text)) for s in segs for f in ("subtotal", "grand_total", "tax_total")
                         for rx, _ in _LABELS[f]):  # fmt: skip
            break  # the table's totals: its rows end above
        last = segs[-1]
        found = money(last)
        if not found or abs(last.box.x1 - right) > AMOUNT_COLUMN or len(segs) < 2 or _CARRIED.match(p.strip()):
            continue
        amount = found[-1][0]
        desc = " ".join(s.text for s in segs if s is not last and not _NUMBER_CELL.match(s.text)).strip()
        if not desc:
            continue
        fit = _qty_times_price(segs, [(s, _amounts_in(s.text)) for s in segs], last, amount)
        qty, price = fit if fit else (None, None)
        row_box = union_all([s.box for s in segs])
        items.append(LineReading(desc, qty, price, amount, [row_box] if row_box else [], 0.6))
    return items if len(items) >= 2 else []


def read_line_items(layout: DocLayout, tables: dict[int, list[Box]] | None = None) -> list[LineReading]:
    """The line items under the line table's column headings, read on the page straightened (``straightened``).
    ``tables``: the tables the table finder found on each page (``table_finder.find_tables``); the rows are then read
    from the words inside them only (``inside_tables``)."""
    skews = {page.number: page.skew for page in layout.pages if abs(page.skew) >= STRAIGHTEN_FROM}
    one_table = {n: [straightened(inside_tables(layout, {n: [box]})) for box in boxes]
                 for n, boxes in (tables or {}).items() if boxes}  # fmt: skip
    items = _read_line_items(straightened(inside_tables(layout, tables) if tables else layout), one_table)
    for li in items if skews else []:
        li.boxes = [_on_tilted_page(b, skews[b.page]) if b.page in skews else b for b in li.boxes]
    if items and credit_printed_positive(layout):
        for li in items:
            li.amount = -li.amount if li.amount else li.amount
            li.unit_price = -li.unit_price if li.unit_price else li.unit_price
    return items


def _read_line_items(layout: DocLayout, one_table: dict[int, list[DocLayout]] | None = None) -> list[LineReading]:
    """``one_table``: for each page where the table finder found tables, the words of each table on its own; on a
    page whose column headings can't be read, the table whose rows read best by their amounts is read
    (``_headerless_rows``)."""
    items: list[LineReading] = []
    money: list[bool] = []  # each line's amount is printed as money ("1,450.00", "$12"), not "5%" or "12"
    for page in layout.pages:
        found = heading_row(page)
        header = (found[0].box.y1, found[2]) if found else None
        if header is None:
            tables = [t.pages[page.number - 1] for t in (one_table or {}).get(page.number, [])]
            rows_read = max((_headerless_rows(t) for t in tables), key=len, default=[])
            items += rows_read
            money += [True] * len(rows_read)
            continue
        y_top, cols = header
        rows: dict[float, list[Line]] = {}
        stop_y = stop_cy = 1.0
        for line in page.lines:
            if line.box.y0 <= y_top:
                continue
            p = plain(line.text)
            if any(rx.match(p) for f in _TOTAL_LABELS for rx, _ in _LABELS[f]):
                stop_y, stop_cy = min(stop_y, line.box.y0), min(stop_cy, line.box.cy)
        for line in page.lines:
            # The table ends at its subtotal or total: its amount, printed a hair higher than its label, too.
            if not (y_top < line.box.y0 < stop_y) or line.box.cy > stop_cy - 0.006:
                continue
            key = next((k for k in rows if abs(k - line.box.cy) < 0.006), line.box.cy)
            rows.setdefault(key, []).append(line)
        amount_x = cols["amount"].cx
        for _, segs in sorted(rows.items()):
            if any(_CARRIED.match(plain(s.text).strip()) for s in segs):
                continue
            nums = [(s, _amounts_in(s.text)) for s in segs]
            amount = None
            amt_seg = None
            amt_token = ""
            for s, found in nums:
                if found and abs(s.box.x1 - cols["amount"].x1) < 0.08 or (found and abs(s.box.cx - amount_x) < 0.08):
                    amount, amt_seg = found[-1][0], s
                    amt_token = s.text[found[-1][1] : found[-1][2]]
            if amount is None:
                continue
            # A number cell (qty, price, amount, row number) is not part of the description; a description
            # with a number in it ("Meraki MR46", "Cat6 cables", "1 year licence") is.
            desc = " ".join(s.text for s in segs if s is not amt_seg and not _NUMBER_CELL.match(s.text) or
                            ("description" in cols and abs(s.box.x0 - cols["description"].x0) < 0.05))  # fmt: skip
            qty = price = None
            if "quantity" in cols:
                q = next((f for s, f in nums if f and abs(s.box.cx - cols["quantity"].cx) < 0.05), None)
                qty = q[0][0] if q else None
            if "unit_price" in cols:
                u = next(
                    (f for s, f in nums if f and s is not amt_seg and abs(s.box.cx - cols["unit_price"].cx) < 0.07),
                    None,
                )
                price = u[-1][0] if u else None
            # The quantity or the price not read under its heading (OCR lost a heading, or a digit): the two numbers
            # left of the amount whose product it is, when the table has a quantity or a price column.
            if {"quantity", "unit_price"} & cols.keys() and not (
                qty is not None and price is not None and abs(qty * price - amount) <= 0.011
            ):
                fit = _qty_times_price(segs, nums, amt_seg, amount)
                if fit:
                    qty, price = fit
            row_box = union_all([s.box for s in segs])
            score = 0.8 if (qty is None or price is None or abs(qty * price - amount) <= 0.011) else 0.5
            items.append(LineReading(desc.strip(), qty, price, amount, [row_box] if row_box else [], score))
            money.append(_printed_as_money(amt_token))
    # Where the lines' amounts are printed as money, a "line" whose amount isn't is a sentence under the table ("All
    # services subject to GST 5%", "4 of 12 hours cancelled") that reaches the amount column.
    if sum(money) >= 2 and not all(money):
        items = [li for li, m in zip(items, money, strict=True) if m]
    # A last row with no words of its own whose amount is the sum of the rows above: the table's total row, its label
    # read as no label (OCR runs "Total before tax" together).
    if len(items) >= 3 and not items[-1].description.strip() and items[-1].amount is not None:
        above = [li.amount for li in items[:-1] if li.amount is not None]
        if abs(round(sum(above), 2) - items[-1].amount) <= 0.011:
            items.pop()
    # A "#" column read into the description: "1 Observability…", "2 Premium…". Only when every row starts
    # with its own number, so "6 Dell monitors" on its own is left alone.
    numbered = [re.match(rf"^{i}\s+(?=\S)", li.description) for i, li in enumerate(items, 1)]
    if len(items) >= 2 and all(numbered):
        for li, m in zip(items, numbered, strict=True):
            li.description = li.description[m.end() :]
    return items


# ---------------------------------------------------------------- entry point


# A state and ZIP code after a city, a comma or at the start of a line ("Seattle, WA 98101", "Boise ID 83702", "WA
# 98101"); not after a label: "Order ID 77812", "Store IN 46204" are a label's numbers, not Idaho or Indiana.
_ID_LABEL_WORDS = (
    "order|vendor|supplier|store|job|ticket|customer|cust|client|account|acct|member|employee|tax|ref|reference|batch|"
    "lot|work|purchase|transaction|tracking|user|invoice|inv|document|doc|file|case|project|contract|site|location|"
    "unit|item|product|part|serial|payment|po|no|number|box|route|shipment|delivery|quote|receipt|confirmation"
)
_US_ADDRESS = re.compile(
    r"(?:^\s*|,\s*|(?<![A-Za-z])(?!(?i:" + _ID_LABEL_WORDS + r")\s)[A-Za-z][A-Za-z.'-]*\s+)"
    r"(?:A[LKZR]|C[AOT]|DE|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])"
    r"\s+\d{5}(?:-\d{4})?\b",
    re.M,
)


def us_address(text: str) -> bool:
    """``text`` holds a US address's state and ZIP code (``offline_coder.provinces`` reads the supplier's with it)."""
    return bool(_US_ADDRESS.search(text))


def _us_vendor(layout: DocLayout) -> bool:
    """The supplier's address (top of the first page, outside the customer's block) is in the US."""
    if not layout.pages:
        return False
    for line in layout.pages[0].lines:
        if line.box.cy < 0.3 and not _in_bill_to(layout, line) and us_address(line.text):
            return True
    return False


def _canadian_vendor(layout: DocLayout) -> bool:
    """The supplier's address (top of the first page, outside the customer's block) has a Canadian postal code."""
    if not layout.pages:
        return False
    return any(
        ln.box.cy < 0.3 and not _in_bill_to(layout, ln) and re.search(r"\b[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d\b", ln.text)
        for ln in layout.pages[0].lines
    )


def read_fields(layout: DocLayout, received: dt.date | None = None) -> dict[str, list[Reading]]:
    """Candidate readings for every header field, best first. ``received``: the day the invoice came
    in (it settles a 03/04/2026 the page itself does not: invoices arrive days after their date)."""
    text = layout.text()
    order = infer_day_first(text)
    # US invoices print month first. A Canadian supplier need not, whatever its currency or a US line on its letterhead
    # (a warehouse, a label's number read as a ZIP code): its Canadian postal code or GST/HST number tells.
    canadian = _canadian_vendor(layout) or any(valid for *_, valid in find_gst_numbers(text))
    if order is None and not canadian and (_us_vendor(layout) or find_currency(text) == "USD"):
        order = False
    if order is not None:
        return _read_with_order(layout, order)
    cands = _read_with_order(layout, None)
    if not any("ambiguous" in r.method for f in ("invoice_date", "due_date") for r in cands.get(f, [])[:1]):
        return cands
    # 03/04/2026: the order that makes the dates agree with each other and with the terms wins.
    options = {o: _read_with_order(layout, o) for o in (True, False)}
    fit = {o: _date_fit(c) for o, c in options.items()}
    if fit[True] != fit[False] and max(fit.values()) >= 2:
        best = max(fit, key=fit.get)
        out = options[best]
        for f in ("invoice_date", "due_date"):
            for r in out.get(f, []):
                r.method += "+terms" if fit[best] >= 3 else "+dates"
        return out
    if received is not None:
        near = {o: _received_fit(c, received) for o, c in options.items()}
        if near[True] != near[False] and max(near.values()) > 0:
            out = options[max(near, key=near.get)]
            for f in ("invoice_date", "due_date"):
                for r in out.get(f, []):
                    r.method += "+received"
            return out
    return cands


def _received_fit(cands: dict[str, list[Reading]], received: dt.date) -> float:
    """How plausible the invoice date is for an invoice received on ``received``: not after it,
    and the closer before it the better (most invoices arrive within weeks of their date)."""
    inv = cands.get("invoice_date")
    if not inv:
        return 0.0
    days = (received - dt.date.fromisoformat(inv[0].value)).days
    if days < -3:
        return -1.0
    if days < 0:
        return 0.01  # dated a day or three after it came in: possible (a clock, a post-dated invoice), never preferred
    return max(0.0, 1.0 - days / 120)


def _read_with_order(layout: DocLayout, order: bool | None) -> dict[str, list[Reading]]:
    token = _DAY_FIRST.set(order)
    try:
        return _read_fields(layout)
    finally:
        _DAY_FIRST.reset(token)


def _date_fit(cands: dict[str, list[Reading]]) -> int:
    """How well the invoice date, due date and terms agree (higher is better)."""

    inv, due, terms = (
        cands.get(f, [None])[0] if cands.get(f) else None for f in ("invoice_date", "due_date", "payment_terms")
    )
    if inv is None or due is None:
        return 0
    days = (dt.date.fromisoformat(due.value) - dt.date.fromisoformat(inv.value)).days
    if days < 0:
        return -2
    m = re.search(r"net\s*(\d+)", str(terms.value), re.I) if terms else None
    if m and days == int(m.group(1)):
        return 3
    if m and abs(days - int(m.group(1))) <= 1:
        return 2
    if m:
        return 1
    return 2 if days in (0, 7, 10, 14, 15, 20, 21, 30, 45, 60, 90) else 1


_DISCOUNT = re.compile(r".*(discount|escompte|rabais|remise)")


_GLUED_TITLE = re.compile(
    r"^(?:(?i:tax|sales)\s*)?(?i:invoice|facture)\s*(?:(?i:n°|no)\.?\s*|#\s*)?([A-Z]{0,4}-?\d[\w/-]*)$"
)


def _glued_title_numbers(layout: DocLayout) -> list[Reading]:
    """OCR glued the title to the number beside it: "INVOICEA-2026-29266", "TAXINVOICEA-90595"."""
    out = []
    for line in layout.lines():
        if all(w.source in _AS_PRINTED for w in line.words):
            continue
        m = _GLUED_TITLE.match(line.text.replace(" ", ""))
        if m and len(m.group(1)) >= 3:
            out.append(Reading("invoice_number", m.group(1), m.group(1), [line.box], 0.75 * _min_conf(line.words),
                               "glued-title"))  # fmt: skip
    return out


def _table_header_line(line: Line, page_lines: list[Line]) -> bool:
    """The line is a column heading of a line-item table (two or more other headings beside it)."""
    others = 0
    for other in page_lines:
        if other is not line and abs(other.box.cy - line.box.cy) < 0.006:
            p = plain(other.text).strip()
            if any(rx.match(p) for rx in _HEAD.values()):
                others += 1
    return others >= 2


def _read_fields(layout: DocLayout) -> dict[str, list[Reading]]:
    lines = list(layout.lines())
    by_page: dict[int, list[Line]] = {}
    for ln in lines:
        by_page.setdefault(ln.box.page, []).append(ln)
    hits: dict[str, list[_LabelHit]] = {}
    for ln in lines:
        in_table_head = None
        for hit in _label_hits(ln):
            if hit.field in AMOUNT_FIELDS:
                if in_table_head is None:
                    in_table_head = _table_header_line(ln, by_page.get(ln.box.page, []))
                if in_table_head:
                    continue  # "Total" over the line-items' amount column is not the invoice total
            hits.setdefault(hit.field, []).append(hit)
    cands: dict[str, list[Reading]] = {}
    for field, fhits in hits.items():
        page_lines = by_page
        readings: list[Reading] = []
        for hit in fhits:
            got = _labelled([hit], page_lines.get(hit.line.box.page, []))
            if field == "other_charges" and _DISCOUNT.match(plain(hit.label_text)):
                for r in got:
                    r.value = -abs(r.value)  # a discount lowers the total, however it is printed
            readings += got
        cands[field] = readings
    if not cands.get("payment_terms"):
        # "Due Date: Upon Receipt" (no date): the terms, printed where the due date goes.
        for hit in hits.get("due_date", []):
            terms = _value_reading("payment_terms", hit.line, hit.end, 0.95 * hit.strength, "label-inline")
            if terms and terms.value == "Due on receipt":
                cands.setdefault("payment_terms", []).append(terms)
    if credit_printed_positive(layout):
        for field in AMOUNT_FIELDS:
            for r in cands.get(field, []):
                r.value = -r.value if r.value else r.value
    # Totals: the lowest "total" on the last page with one is usually the invoice total.
    if cands.get("grand_total"):
        last_page = max(r.boxes[0].page for r in cands["grand_total"] if r.boxes)
        for r in cands["grand_total"]:
            if r.boxes and r.boxes[0].page == last_page:
                r.score = min(1.0, r.score + 0.02 * r.boxes[0].cy)
    # Reading order breaks ties: earlier pages and higher on the page first for header fields.
    for field in ("invoice_number", "invoice_date", "po_number", "due_date"):
        for r in cands.get(field, []):
            if r.boxes:
                r.score -= 0.03 * (r.boxes[0].page - 1) + 0.01 * r.boxes[0].cy
    cands.setdefault("invoice_number", []).extend(_glued_title_numbers(layout))
    cands.update(_registration_numbers(layout))
    cands["vendor_name"] = _vendor_names(layout)
    currency = currency_evidence(layout.text())
    if currency:
        # Several codes and none clearly the invoice's: its own evidence pattern, so it is not calibrated as sure.
        method = "pattern" if currency[1] >= 0.8 else "pattern-several-codes"
        cands["currency"] = [Reading("currency", currency[0], currency[0], [], currency[1], method)]
    items = read_line_items(layout)
    line_sum = round(sum(i.amount for i in items if i.amount is not None), 2) if items else None
    cands = _solve_totals(cands, line_sum)
    for field, readings in cands.items():
        readings.sort(key=lambda r: -r.score) if field not in AMOUNT_FIELDS else None
        cands[field] = _dedupe(readings)
    return cands


def _dedupe(readings: list[Reading]) -> list[Reading]:
    """One reading per distinct value (the best one), keeping order."""
    from .normalize import normalize_value

    seen: dict[object, Reading] = {}
    out = []
    for r in readings:
        key = normalize_value(r.field, r.value)
        if key in seen:
            seen[key].score = min(1.0, max(seen[key].score, r.score) + 0.02)  # printed twice, read twice
            continue
        seen[key] = r
        out.append(r)
    return out
