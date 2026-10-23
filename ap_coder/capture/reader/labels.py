# ruff: noqa: E501  (label and pattern tables read best one per line)
"""The labels the reader looks for, and finding them on the page."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from ..types import (
    Line,
)


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
        (r"commande\s*(?:client|no|n\s?°)", 0.8), (r"bill\s*to\s*p\.?\s?o\.?\s*(?:no\b\.?|number|#)", 0.9), (r"order\s*ref(?:erence)?\b\.?", 0.7), (r"votre\s*(?:commande|bon)", 0.95), (r"b\.\s?c\.(?!\s*(?:pst|v\d))\s*(?:#|no\b\.?|n\s?°)?", 0.8),
    ],
    "invoice_date": [
        (r"invoice\s*date", 1.0), (r"date\s*(?:of\s*)?(?:issue|invoice)", 1.0), (r"issue\s*date", 0.95),
        (r"billing\s*date", 0.95), (r"bill\s*date", 0.9), (r"date\s*de\s*(?:la\s*)?factur(?:e|ation)", 1.0),
        (r"date\s*d'?\s*emission", 0.95), (r"statement\s*date", 0.8), (r"date\s*de\s*la\s*note", 0.8),
        (r"inv\.?\s*date", 0.95), (r"date\s*(?:issued|of\s*issue)", 0.95), (r"issued(?:\s*on)?\b", 0.85), (r"issue\s*date", 0.95),
        (r"credit\s*(?:note|memo)\s*date", 0.95), (r"document\s*date", 0.85), (r"credit\s*date", 0.95), (r"date\s*du\s*credit", 0.9), (r"dated?\b(?!\s*(?:due|d'?echeance|de\s*commande|d'?expedition|de\s*livraison|du\s*service))", 0.55),
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
        (r"total\s*partiel", 0.9), (r"merchandise\s*total", 0.8), (r"total\s*services", 0.5), (r"total\s*sales?\b(?!\s*tax)(?!\s*\(?\s*incl)", 0.8),
        (r"total\s*(?:of\s*)?(?:fees|charges|services|goods|merchandise|labou?r|materials)\b", 0.65),
    ],
    "hst_amount": [(rf"(?:total\s*)?(?:[a-z]{{2}}\s*)?(?:{_A('gst')}\s*/\s*)?{_A('hst')}", 1.0), (rf"(?:{_A('tps')}\s*/\s*)?{_A('tvh')}", 1.0), (r"harmoni[sz]ed\s*sales\s*tax", 1.0)],
    "gst_amount": [(rf"(?:total\s*)?(?:federal\s+)?{_A('gst')}(?!\s*/\s*{_A('hst')})", 1.0), (rf"{_A('tps')}(?!\s*/\s*{_A('tvh')})", 1.0), (r"goods\s*and\s*services\s*tax", 1.0)],
    "pst_amount": [(rf"(?:[a-z]{{2}}\s*)?{_A('pst')}", 1.0), (rf"(?:[a-z]{{2}}\s*)?{_A('rst')}", 1.0), (rf"{_A('tvp')}", 1.0), (r"provincial\s*sales\s*tax", 1.0),
                   (r"retail\s*sales\s*tax", 1.0)],
    "qst_amount": [(rf"(?:qc\s+)?{_A('qst')}", 1.0), (rf"{_A('tvq')}", 1.0), (r"quebec\s*sales\s*tax", 1.0)],
    "other_charges": [
        (r"(?:freight|shipping|delivery|handling|transport|livraison|frais\s*de\s*(?:livraison|transport|manutention)|"
         r"environmental\s*(?:fee|levy|handling)|eco\s*-?\s*fees?|ecofrais|fuel\s*surcharge)", 0.9),
        (r"(?:volume\s*|promotional\s*|early\s*payment\s*)?discount|escompte|rabais|remise", 0.9),
        # Paid before the invoice: "Posted Pre-Payments (519.35)", "Less deposit", "Deposit applied".
        (r"(?:less\s*:?\s*)?(?:posted\s*)?pre\s*-?\s*payments?(?:\s*(?:applied|received|posted))?|"
         r"(?:less\s*:?\s*)?deposits?\s*(?:applied|received|paid)|less\s*:?\s*deposits?", 0.9),
    ],
    "tax_total": [
        (r"total\s*(?:sales\s*)?tax(?:es)?\b", 1.0), (r"tax(?:es)?\s*total", 0.95), (r"total\s*des\s*taxes", 1.0),
        (r"taxes\b(?!\s*incl)", 0.6), (r"(?:[a-z]{2,3}\s+)?(?:state\s*|county\s*|city\s*|local\s*)?sales\s*tax\b", 0.85), (r"(?:[a-z]{2,3}\s+)?general\s*excise\s*tax\b", 0.85), (r"tax\s*(?:amount)?\s*$", 0.55), (r"tax\s*:", 0.6), (r"tax\s*[(@]?\s*\d{1,2}(?:[.,]\d{1,3})?\s*%", 0.85),
    ],
    "grand_total": [
        (r"(?:invoice\s*)?total\s*(?:amount\s*)?(?:due|payable)", 1.0), (r"amount\s*due", 0.95), (r"balance\s*due", 0.85),
        (r"total\s*a\s*payer", 1.0), (r"montant\s*(?:total|du|a\s*payer)", 1.0), (r"grand\s*total", 1.0),
        (r"invoice\s*total", 1.0), (r"total\s*de\s*la\s*facture", 1.0), (r"total\s*(?:cad|usd|\$)", 0.95),
        (r"net\s*payable", 0.9), (r"please\s*pay", 0.85), (r"total\s*(?:amount|invoice)", 0.95),
        (r"total\s*this\s*invoice", 1.0), (r"net\s*(?:amount|balance)\s*(?:due|payable)", 1.0),
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
                    rf"total\s*(?:{_A('gst')}|{_A('hst')}|{_A('pst')}|{_A('qst')}|{_A('tps')}|{_A('tvh')}|{_A('tvq')})(?!\s*(?:incl|inclus))",
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
    weak: bool = False  # one of ``WEAK_LABELS``
    fallback: bool = False  # read only when no other label for its field is on the document
    unmeasured: bool = False  # found by a rule the benchmark never exercises: its reading is marked ``UNMEASURED``


# Labels seen on real invoices (sweep 4): an English variant, and the German, Dutch, Italian, Spanish and French
# (France) words for the totals, the invoice's number and its dates. Tried after the labels above. (Plain text:
# accents off, "fällig" is "fallig".)
_RATE_FIRST = r"\d{1,2}(?:[.,]\d{1,3})?\s*%\s*"  # a receipt's "13%HST", "15.00%HST:", "5% GST"

MORE_LABELS: dict[str, list[tuple[str, float]]] = {
    "hst_amount": [(rf"{_RATE_FIRST}(?:{_A('hst')}|{_A('tvh')})", 0.95)],
    "gst_amount": [(rf"{_RATE_FIRST}(?:{_A('gst')}|{_A('tps')})(?!\s*/\s*(?:hst|tvh))", 0.95)],
    "pst_amount": [(rf"{_RATE_FIRST}(?:{_A('pst')}|{_A('rst')}|{_A('tvp')})", 0.95)],
    "qst_amount": [(rf"{_RATE_FIRST}(?:{_A('qst')}|{_A('tvq')})", 0.95)],
    "invoice_number": [
        (r"rechnungs\s*-?\s*(?:nummer|nr\b\.?)", 1.0), (r"factuur\s*-?\s*(?:nummer|nr\b\.?)", 1.0),
        (r"(?:numero|n\.?)\s*(?:de\s*)?factura|factura\s*(?:n\s?[°ºo]\.?|numero)", 0.95),
        (r"fattura\s*(?:n\s?[°ºo]\.?|numero)|numero\s*(?:di\s*)?fattura", 0.95), (r"beleg\s*-?\s*(?:nummer|nr\b\.?)", 0.8),
        # The title with its number: "Rechnung Nr. 42", "Handelsrechnung (380) Nr. 471102 vom ...", "Rechnung # RE-1/499".
        (r"(?:[a-z]*rechnung(?:skorrektur)?|gutschrift|factuur)\s*(?:\(\d{3}\)\s*)?(?:nr\b\.?|#|nummer)", 0.9),
        # A French credit note's title with its number ("Avoir AV-2017-0005", "Avoir n° 12"), as "Note de crédit 051527-CR".
        (r"(?:no|n\s?°|nº|numero)\.?\s*(?:de\s*)?(?:l'?\s*)?avoir|avoir(?=\s*[:#]?\s*(?:n\s?[°º]\.?\s*)?[a-z]{0,4}-?\d)", 0.6),
        (rf"(?:cash|tax|sales|commercial)\s*inv(?:oice)?\.?\s*{_NO}", 0.95),  # "Cash Inv No.: 7803F712", "Tax Invoice No."
    ],
    "invoice_date": [
        (r"rechnungsdatum", 1.0), (r"factuur\s*datum", 1.0), (r"data\s*(?:della\s*)?fattura", 0.95),
        (r"fecha\s*(?:de\s*(?:la\s*)?)?(?:factura|emision)", 0.95), (r"beleg\s*-?\s*datum", 0.8), (r"datum\b", 0.55),
        (r"data\s*(?:da|de)\s*emissao", 0.95),
    ],
    "due_date": [
        (r"fallig(?:keit|keitsdatum)?(?:\s*am)?\b", 0.9), (r"zahlbar\s*bis", 0.9), (r"vervaldatum", 1.0),
        (r"(?:data\s*di\s*)?scadenza", 0.9), (r"(?:fecha\s*de\s*)?vencimiento", 0.9),
    ],
    "subtotal": [
        (r"(?:invoice|order)\s*sub\s*-?\s*total", 1.0), (r"rechnungssumme\s*(?:netto|ohne\s*u\.?\s?st|exkl|excl)", 0.9),
        (r"netto\s*-?\s*summe|summe\s*netto|zwischensumme|gesamt\s*netto|netto\s*gesamt|nettobetrag\s*gesamt", 0.95),
        (r"netto\b", 0.6), (r"subtotaal", 1.0), (r"totaal\s*excl\b\.?(?:\s*btw)?", 0.95), (r"(?:totale\s*)?imponibile", 0.9),
        (r"base\s*imponible", 0.9), (r"montant\s*h\.?\s?t\b\.?", 0.9), (r"total\s*h\.?\s?t\b\.?", 0.9),
        (r"net\s*worth", 0.85), (r"positionssumme|summe\s*(?:der\s*)?positionen", 1.0),
        (r"(?:totaal\s*)?excl(?:\.|usief)?\s*btw", 0.95),
    ],
    "other_charges": [
        (r"(?:gesamtbetrag\s*der\s*)?zuschlage?\b", 0.85), (r"(?:gesamtbetrag\s*der\s*)?abschlage?\b|rabatt\b", 0.85),
        (r"(?:zzgl\.?|zuzuglich)\s*(?:versand|fracht|porto|verpackung)\w*|versandkosten|frachtkosten", 0.85),
        (r"(?:abzgl\.?|abzuglich)\s*\w+|sondernachlass|nachlass\b", 0.85),
    ],
    "tax_total": [
        (r"(?:summe\s*)?(?:mwst|mehrwertsteuer|umsatzsteuer(?!\s*-?\s*(?:aufschl|id))|u\.?\s?st\b\.?(?!\s*[.-]?\s*(?:id|nr)))", 0.85),
        (r"steuerbetrag", 0.8), (r"steuerbasisbetrag", 0.7),
        (r"(?:zzgl\.?|zuzuglich)\s*(?:umsatzsteuer|mehrwertsteuer|mwst|u\.?\s?st\b)", 0.85), (r"btw\b(?!\s*-?\s*(?:nr|nummer|id))", 0.85), (r"iva\b", 0.7), (r"imposta(?:\s*iva)?", 0.9),
        (r"(?:total\s*|montant\s*)?t\.?v\.?a\b\.?(?!\s*intra)", 0.85), (r"(?:total\s*)?vat\b(?:\s*amount)?", 0.85),
    ],
    "grand_total": [
        (r"rechnungssumme\s*(?:brutto|inkl)", 1.0),
        (r"brutto\s*-?\s*summe|summe\s*brutto|bruttobetrag|gesamt\s*brutto|brutto\s*gesamt", 1.0),
        (r"gesamtbetrag(?!\s*der\s*(?:zu|ab)schl)|gesamtsumme|rechnungsbetrag|zahlbetrag|endbetrag", 0.95),
        (r"zu\s*zahlen(?:der\s*betrag)?", 0.9), (r"brutto\b", 0.75),
        (r"totaal(?:\s*(?:incl\b\.?(?:\s*btw)?|bedrag|te\s*betalen))?", 0.9), (r"te\s*betalen", 0.9),
        (r"totale(?:\s*(?:documento|fattura|da\s*pagare))?", 0.9), (r"total\s*a\s*pagar|importe\s*total", 1.0),
        (r"total\s*t\.?\s?t\.?\s?c\b\.?|montant\s*t\.?\s?t\.?\s?c\b\.?|net\s*a\s*payer", 1.0),
        (r"gross\s*(?:worth|total|amount)", 0.9),
    ],
}  # fmt: skip

for _field, _pats in MORE_LABELS.items():
    LABELS[_field] = [*LABELS[_field], *_pats]

# Weak labels (sweep 4): words that name the invoice's number or date on some documents and something else on others.
# A value read after one is its own evidence pattern ("weak-label-..."): never verified or likely on a real label's
# measured record. A fallback one (``True``) is read only when no other label for the field is on the document: on a
# Canadian invoice "Receipt Date" is the day the goods came in, "Receipt No." a payment's receipt.
WEAK_LABELS: dict[str, list[tuple[str, float, bool]]] = {
    "invoice_number": [
        (r"nr\b\.?(?=\s*:?\s*[a-z]{0,3}-?\d)", 0.35, False),  # "Nr. 471102" beside its title, as "No. 4471"
        # A receipt's or a hotel bill's own number: "Receipt Number 334567", "Receipt # No.: 9876", "Folio Number: 12345".
        (rf"receipt\s*(?:{_NO}|id\b)", 0.8, True), (rf"folio\s*{_NO}", 0.7, True),
        (r"factuur\b(?!\s*(?:datum|adres|totaal|bedrag|nummer))", 0.45, False),
        (r"numero\s*:(?!\s*\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4})", 0.45, False),  # "NUMERO: ABC12345", not a phone
    ],
    "invoice_date": [(r"receipt\s*date", 0.9, True), (r"fecha\b", 0.55, False)],
}  # fmt: skip

for _field, _weak in WEAK_LABELS.items():
    LABELS[_field] = [*LABELS[_field], *((rx, st) for rx, st, _fb in _weak)]

# Labels for the terms, the PO and the due date seen on real invoices (sweep 4) that the benchmark never prints: a
# value read by one is marked ``UNMEASURED`` (a person checks it). Tried after all the labels above.
UNMEASURED_LABELS: dict[str, list[tuple[str, float]]] = {
    "payment_terms": [
        (r"condicion(?:es)?\s*de\s*pago", 0.9), (r"(?:termini|condizioni)\s*(?:di\s*)?pagamento", 0.9),
        (r"zahlungs\s*-?\s*(?:bedingungen|konditionen|ziel)", 0.85), (r"betalings\s*-?\s*(?:voorwaarden|termijn|conditie)s?", 0.85),
    ],
    # German: the customer's order ("Bestellung: B123456789", "Bestell-Nr"), not its date ("Bestellung vom 01.03.").
    "po_number": [
        (r"(?:ihre\s*)?bestellung(?:\s*(?:nr\b\.?|nummer))?(?!\s*vom)", 0.85), (r"(?:ihre\s*)?bestell\s*-?\s*(?:nr\b\.?|nummer)", 0.85),
    ],
    # "Invoice Due Date" (a grid's heading), Canada Post's "Your payment is due by", Amazon's "TOTAL AMOUNT DUE ON
    # August 3, 2014", Dutch and German labels ("Zahlungsziel" may be the terms instead: only a date is read).
    "due_date": [
        (r"invoice\s*due\s*date", 0.95), (r"(?:your\s*)?payment\s*is\s*due\s*(?:by|on)\b", 0.9),
        (r"(?:total\s*)?(?:amount|balance)\s*due\s*(?:on|by)\b(?![^|]*(?:deliver|account|as\s*of|discount|after|livraison))", 0.85), (r"vervaldag", 0.95), (r"falligkeitstag", 0.9),
        (r"zahlungsziel", 0.8),
    ],
}  # fmt: skip

for _field, _pats in UNMEASURED_LABELS.items():
    LABELS[_field] = [*LABELS[_field], *_pats]

_UNMEASURED_LABEL = {f: re.compile("|".join(f"(?:{rx})" for rx, _s in pats)) for f, pats in UNMEASURED_LABELS.items()}

DISTRACTORS["tax_total"] += [r"btw\s*[a-z]{2}\s*\d", r"btw\s*prijs", r"u\.?\s?st\.?\s*[.-]?\s*id", r"btw\s*-?\s*(?:nr|nummer|id)", r"tva\s*intra",
                             r"(?:n\s?[°º]|no\b\.?|numero)\s*(?:de\s*)?tva", r"vat\s*(?:no\b|reg|number|id\b|#)"]  # fmt: skip

DISTRACTORS["grand_total"] += [r"totaal\s*excl", r"totale\s*imponibile"]

# "TOTAL HST INCL." is the total with the tax in it, not the tax.
for _field in ("hst_amount", "gst_amount"):
    DISTRACTORS.setdefault(_field, []).append(
        r"total\s*(?:[a-z]{2}\s*)?(?:gst\s*/\s*)?(?:hst|gst)\.?\s*(?:incl|inclus)"
    )

DISTRACTORS["invoice_number"] += [r"invoice\s*sub\s*-?\s*total",  # "Invoice Subtotal 1020.58" is no invoice number
                                  # A credit note's "Facture liée FA-2017-0010" / "Ursprungsbeleg-Nr": the invoice it credits.
                                  r"facture\s*(?:liee|associee|initiale|concernee|corrigee|de\s*reference)",
                                  r"(?:ursprungs|bezugs)\s*(?:rechnung|beleg)"]  # fmt: skip


@functools.cache
def _label_rx(rx: str) -> re.Pattern[str]:
    # A label ends where letters end, even when OCR glued it to its value ("Dated25/05/2025", "No.033"):
    # every word boundary in the table means "no letter follows".
    return re.compile(rx.replace(r"\b", r"(?![a-z])"))


def _compile() -> dict[str, list[tuple[re.Pattern[str], float]]]:
    return {f: [(_label_rx(rx), s) for rx, s in pats] for f, pats in LABELS.items()}


_LABELS = _compile()

_WEAK = {_label_rx(rx) for weak in WEAK_LABELS.values() for rx, _s, _fb in weak}

_FALLBACK = {_label_rx(rx) for weak in WEAK_LABELS.values() for rx, _s, fb in weak if fb}

_DISTRACT = {f: [re.compile(rx) for rx in pats] for f, pats in DISTRACTORS.items()}

_OCR_LABEL_GAP = re.compile(r"(?<=[a-z]{2})[.·_-](?=[a-z]{2})")

_OCR_LABEL_ONE = re.compile(r"(?<=[a-z]{3})1(?![\d.,])")  # a 1 OCR read for the l ending a word: "tota1", "subtota1"

_OCR_LABEL_ZERO = re.compile(r"(?<=[a-z]{2})0(?=[a-z]{2})")  # a 0 OCR read for the o inside a word: "inv0ice"

_OCR_WORD_SPECK = re.compile(r"(?<=[a-z]{2})[.·]\s+(?=[a-z]{2})")

_NEXT_WORD = re.compile(r"\s+(?=[a-z])")

_GLUED_NUMBER = re.compile(r"[A-Z]{1,4}[-/]?\d")

_PAGE_NUMBER = re.compile(r"(?:page|seite|pagina)\s*[\dl]{1,3}\s*(?:/|of|de|sur|von)\s*\d{1,3}\s+(?=[a-z])")

_QST_OR_PST = re.compile(r"\s*(?:q\.?s\.?t|t\.?v\.?q)\.?\s*/\s*(?:p\.?s\.?t|t\.?v\.?p)(?![a-z])")  # "QST/PST"

_PST_OR_QST = re.compile(rf"\s*/\s*(?:{_A('qst')}|{_A('tvq')})")

# The supplier's QST or PST registration number, as printed beside its label: the province's tax it charges.
_QST_REG = re.compile(
    r"(?<![a-z])(?:q\.?s\.?t|t\.?v\.?q)(?![a-z])[\s.:#]*(?:(?:no|n°|reg|registration)\.?[\s:#]*)?\d{10}"
)

_PST_REG = re.compile(r"(?<![a-z])(?:p\.?s\.?t|r\.?s\.?t|t\.?v\.?p)(?![a-z])[\s.:#-]*(?:(?:no|n°|reg|registration)\.?[\s:#]*)?"
                      r"(?:pst-?)?\d[\d-]{5,12}\d(?![\d.,%])")  # fmt: skip


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
                    # Unless OCR glued a title to its number: "AvoirAV-2017-0005" (lower case, then the number's
                    # capitals and a digit).
                    glued = field in ("invoice_number", "po_number") and line.text[m.end() - 1 : m.end()].islower()
                    if not (glued and _GLUED_NUMBER.match(line.text, m.end())):
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
                both = _PST_OR_QST.match(text, end) if field == "pst_amount" else None
                if field == "hst_amount" and "/" in label_text:
                    for hit_field in _combined_tax_fields(text):
                        hits.append(_LabelHit(hit_field, line, strength, end, label_text, box_start))
                elif both:  # "PST/QST", "TVP/TVQ": one column for whichever the province charges
                    end = both.end()
                    for hit_field in ("pst_amount", "qst_amount"):
                        hits.append(_LabelHit(hit_field, line, strength, end, text[st:end], box_start))
                else:
                    hits.append(
                        _LabelHit(field, line, strength, end, label_text, box_start, rx in _WEAK, rx in _FALLBACK)
                    )
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
