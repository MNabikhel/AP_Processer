# ruff: noqa: E501  (label and pattern tables read best one per line)
"""Fields read from their shape alone (tax numbers, dates, the vendor's name...)."""

from __future__ import annotations

import re
from contextvars import ContextVar

from ..normalize import (
    find_gst_numbers,
    find_pst_numbers,
    find_qst_numbers,
    looks_like_money,
    luhn_ok,
)
from ..types import (
    BILL_TO_READING,
    RIVAL_NAME,
    UNMEASURED,
    Box,
    DocLayout,
    Line,
    PageLayout,
    Reading,
    Word,
    union_all,
)
from ._common import (
    _AS_PRINTED,
    _HEAD,
    _LABEL_SHAPE,
    _table_header_line,
    plain,
    us_address,
)
from .spatial import (
    _below,
    _right_of,
)
from .values import (
    _boxes,
    _label_hits,
    _min_conf,
    _ocr_name_fix,
    _span_words,
)

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
    if not gst:
        gst = _bare_business_numbers(layout)
    return {"gst_hst_registration_number": gst, "qst_registration_number": qst, "pst_registration_number": pst}


# A GST/HST number's label: "GST/HST Registration No.:", "HST #", "HST Number:", "GST No.", "TPS/TVH no".
_GST_REG_LABEL = re.compile(
    r"(?<![a-z])(?:gst|hst|tps|tvh)(?:\s*/\s*(?:gst|hst|tps|tvh))?(?:\s*\(\s*[a-z]{2}\s*\))?\s*(?:reg(?:istration|\.)?|registration)?\s*"
    r"(?:no\b\.?|nos?\.|number|num\b\.?|#|n\s?°)\s*:?\s*"
    r"|(?<![a-z])(?:gst|hst|tps|tvh)(?:\s*/\s*(?:gst|hst|tps|tvh))?\s*reg(?:istration|\.)?\s*:?\s*"
)

_OTHER_PARTY = re.compile(
    r"purchaser|buyer|carrier|sub\s*-?\s*contractor|exempt|recipient|agent|broker|shipper|consignee|customer|client"
)

_NINE_DIGITS = re.compile(r"^(\d{9}|\d{5}\s\d{4}|\d{3}\s\d{3}\s\d{3})(?![\d.,%])")


def _bare_business_numbers(layout: DocLayout) -> list[Reading]:
    """The supplier's business number printed without its RT program account, after its GST/HST label: "HST #
    108162333", or on the line under "GST/HST Registration No.:". Read as the nine digits, "label-bn", marked
    ``UNMEASURED`` (the benchmark prints the whole number); not for a customer's number (bill-to block, "Client")."""
    out: list[Reading] = []
    for page in layout.pages:
        for line in page.lines:
            p = plain(line.text)
            for m in _GST_REG_LABEL.finditer(p):
                if _CUSTOMER_WORD.search(p[max(0, m.start() - 40) : m.start()]) or _OTHER_PARTY.search(
                    p[max(0, m.start() - 40) : m.start()]
                ):
                    continue  # "Client HST #", "Purchaser's HST #", "Carrier HST #": another party's number
                bill_to = _in_bill_to(layout, line)
                spots = [(line, m.end())]
                if not p[m.end() :].strip():
                    # The number beside the label, or under it.
                    spots += [(o, 0) for o in _right_of(page.lines, line)[:1]]
                    spots += [(o, 0) for o in _below(page.lines, line, line.box)[:1]]
                for where, at in spots:
                    rest = where.text[at:].strip()
                    got = _NINE_DIGITS.match(rest)
                    if not got or re.match(r"[\s.:-]*[RT]", rest[got.end() :], re.I):
                        continue  # "232672980-RT-0001" misread: the whole number is printed, not read here
                    digits = re.sub(r"\s", "", got.group(1))
                    a = where.text.find(got.group(1), at)
                    words = _span_words(where, a, a + len(got.group(1)))
                    score = (0.9 if luhn_ok(digits) else 0.5) * _min_conf(words) * (0.35 if bill_to else 1.0)
                    out.append(Reading("gst_hst_registration_number", digits, got.group(1), _boxes(words), score,
                                       "label-bn" + UNMEASURED + (BILL_TO_READING if bill_to else "")))  # fmt: skip
                    break
    return out


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
                      r"s\.?e\.?n\.?c|s\.e\.c|products|gmbh|ag|kg|b\.v|n\.v|s\.a\.?s?|sarl|eurl|sas|s\.r\.l|s\.p\.a|sdn\.?\s*bhd|bhd|pvt|pte|"
                      r"group|groupe|services|solutions|supply|supplies|industries|technologies|consulting|"
                      r"distribution|holdings|partners|associates|enterprises|entreprises)\b\.?", re.I)  # fmt: skip

_NOT_NAME = re.compile(r"^(invoice|facture|credit\s*memo|debit\s*(?:note|memo)|avis\s*de\s*credit|(?:sales|tax|commercial|proforma|pro\s*forma)\s*invoice|please\s*pay|your\b|votre\b|credit\s*note|note\s*de\s*credit|statement|page\b|date|bill|"
                       r"ship|sold|remit|total|amount|description|qty|www\.|http|tel|phone|fax|email|courriel|"
                       r"gst|hst|tps|tvq|qst|po\b|p\.o|account|terms|due|original|copy|duplicata|paid|"
                       r"customer|client|attention|attn|from|de\s*:|to\s*:|a\s*:|bon\s*de)", re.I)  # fmt: skip

_REMIT = re.compile(r"(?:make\s*(?:all\s*)?cheques?\s*payable\s*to|payable\s*(?:to|a\s*l'ordre\s*de)|"
                    r"remit\s*(?:payment\s*)?to|pay\s*to\s*the\s*order\s*of|libeller\s*(?:le|les)?\s*cheques?\s*a|"
                    r"remit(?:tance)?\s*address|send\s*(?:all\s*)?payments?\s*to|"
                    r"mail\s*(?:all\s*)?(?:payments?\s*|remittances?\s*)?to(?![a-z]))\s*:?\s*",
                    re.I)  # fmt: skip

# A column's label OCR or the PDF's text ran onto the end of a name: "REMIT TO Sinclair Broadcast G Advertiser".
_REMIT_WHERE = re.compile(r"(?:the\s*)?(?:bank|address|account|following|below)\s*:?")

_ACCOUNT_NAME = re.compile(r"^(?:a/c|account|beneficiary)\s*name\s*:\s*", re.I)  # "A/C Name: MICROSOFT CANADA INC"

_TRAILING_LABEL = re.compile(
    r"\s+(?:advertiser|agency|buyer|product|customer|client|salesperson|station|brand)\s*:?$", re.I
)

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

_COLUMN_WORD = re.compile(r"^(?:net\s*\d+|code|item\s*code|part\s*(?:#|no\.?)|sku|uom|unit|ref\.?|#)$")

_POSTAL_CODE = re.compile(r"[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d|,\s*(?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)(?![a-z])")

_TITLE_PREFIX = re.compile(
    r"^(?:sales\s*|tax\s*|commercial\s*)?(?:invoice|facture)\s*(?:#|no\.?|n°)?\s*[a-z]{0,3}[\d/-]*\d\S*\s+"
)

_ATTENTION = re.compile(r"^(?:attn|attention|a\s*l'?\s*attention\s*de|c\s*/\s*o)\b|^accounts\s*payable\b")


def _over_attention(lines: list[Line], line: Line) -> bool:
    """``line`` is printed just over an "Attn: Accounts Payable" or "c/o ..." line starting where it starts: a
    customer's name in its address block."""
    h = max(line.box.height, 0.008)
    return any(
        other is not line
        and abs(other.box.x0 - line.box.x0) < 0.02
        and 0 < other.box.cy - line.box.cy < 2.2 * h
        and _ATTENTION.match(plain(other.text).strip())
        for other in lines
    )


# Lines at the top of a page that are no supplier's name, however they are printed (sweep 4, real invoices): a
# document's title or a section's heading ("SAMPLE INVOICE WITH REQUIRED INFORMATION", "Summary of charges",
# "Sommaire des frais"), a form's placeholder ("Company Name", "Address Line1"), a receipt's staff or table line
# ("Server: Bhomik", "Table 23"), a field's label with its value ("Guest Name: Sanjay"), and a city or province
# printed alone ("VAUGHAN ON", "Montreal,Quebec", "Toronto").
_TITLE_WORDS = re.compile(
    r"(?<![a-z])(?:invoices?|invoicing|facture|facturation|receipt|recu|statement|summary|sommaire|details|"
    r"requirements|rechnung(?:en)?|gutschrift|factura|fattura|factuur|bon\s+de\s+commande|purchase\s+order|quotation|"
    r"soumission|estimate|devis|charges|frais|contact(?:ez)?|renseignements|information|inquiries|questions|"
    r"thank\s+you|merci|important|message|notice)(?![a-z])"
)

_PLACEHOLDER = re.compile(
    r"^(?:(?:registered\s+)?(?:company|business|vendor|supplier|legal)\s+name|address(?:\s*line\s*\d*)?|"
    r"postal\s+code|city|province|phone|logo|your\s+(?:company|logo|name))\b"
)

_STAFF = re.compile(
    r"^(?:server|cashier|caissiere?|serveu(?:r|se)|table|tbl|guests?|host|clerk|operator|register|till|"
    r"trans(?:action)?|chk|check\s*#|check\s*(?:in|out)|cover|station|terminal|store\s*#|order\s*#)(?![a-z])"
)

_FIELD_AND_VALUE = re.compile(r"^[a-z][a-z .'/#-]{1,24}:\s*\S")

# A receipt's other words: "SEE BACK", "SALE", "PROMO****", "Welcome", "Customer copy"; a slogan ("always more for
# less!"); a line item ("+MASHED POTATO").
_RECEIPT_WORDS = re.compile(
    r"^(?:see\s+back|sale|promo|welcome|bienvenue|thank|merci|customer\s+copy|copie\s+client|duplicate|reprint|"
    r"approved|approuvee?|void|annule)(?![a-z])|!\s*$|^[+=]"
)

_PROVINCES = (
    r"ontario|quebec|british\s+columbia|alberta|manitoba|saskatchewan|nova\s+scotia|new\s+brunswick|"
    r"newfoundland(?:\s+and\s+labrador)?|prince\s+edward\s+island|yukon|nunavut|northwest\s+territories"
)

_CITIES = (
    r"toronto|montreal|vancouver|calgary|edmonton|ottawa|winnipeg|quebec(?:\s+city)?|hamilton|kitchener|london|"
    r"victoria|halifax|oshawa|windsor|saskatoon|regina|st\.?\s+john'?s|barrie|kelowna|abbotsford|sherbrooke|"
    r"guelph|kingston|moncton|trois-rivieres|saguenay|levis|laval|gatineau|longueuil|mississauga|brampton|"
    r"markham|vaughan|richmond(?:\s+hill)?|burnaby|surrey|oakville|burlington|scarborough|north\s+york|"
    r"etobicoke|waterloo|whitby|ajax|pickering|thornhill|concord|woodbridge|brossard|terrebonne|"
    r"repentigny|boucherville|dorval|pointe-claire|saint-laurent|anjou|lachine|verdun|montreal-nord"
)

_COUNTRIES = (
    r"canada|usa|u\.s\.a\.?|united\s+states(?:\s+of\s+america)?|united\s+kingdom|deutschland|germany|france|"
    r"espana|spain|italia|italy|nederland|netherlands|belgique|belgie|osterreich|schweiz|suisse|portugal|mexico"
)

_PLACE = re.compile(
    rf"^(?:(?:{_CITIES})(?:\s*,?\s*(?:{_PROVINCES}|on|qc|bc|ab|mb|sk|ns|nb|nl|pe|yt|nt|nu|{_COUNTRIES}))?|"
    rf"(?:[a-z.'-]+\s*){{1,3}},?\s*(?<![a-z])(?:{_PROVINCES}|on|qc|bc|ab|mb|sk|ns|nb|nl|pe|yt|nt|nu)|"
    rf"(?:[a-z.'-]+\s*){{1,3}},\s*(?:{_COUNTRIES})|"
    rf"(?:{_PROVINCES}|{_COUNTRIES}))"
    r"\s*[.,]?$"
)

# A street address: "15Wertheim Court", "70 La Gauchetiere Ouest, 2etage", "Jalan SS 15/4B".
_ADDRESS_LINE = re.compile(
    r"^(?:\d+[a-z]?[\s,-]*(?:[a-z.'-]+\s+){0,4}(?:rue|boul|blvd|ave|av|avenue|st|street|rd|road|chemin|ch|route|hwy|"
    r"dr|drive|way|cres|crescent|pkwy|court|ct|lane|ln|place|pl|ouest|est|nord|sud|west|east|north|south|square|sq)"
    r"(?![a-z])|(?:jalan|jln|lorong)(?![a-z]))"
)

_GLUED_TITLE_WORDS = re.compile(r"invoice|facture|renseignements|summary|sommaire|statement|account|question")

_CURRENCY_CODE = re.compile(r"^(?:eur|usd|cad|gbp|chf|aud|jpy|inr|myr|idr|sgd|nzd|mxn)$")

_LIST_ITEM = re.compile(r"^\(?\d{1,2}[.)]\s")  # "2. Vendor's FULL LEGAL NAME, remit": a numbered note

# A legal form OCR glued to the name: "CONTOSOLTD.", "AcmeInc".
_GLUED_FORM = re.compile(r"[a-z]{3}(?:ltd|inc|llc|gmbh|ltee|corp)\b\.?", re.I)

# Lines at the top of English vendor PDFs that name no supplier (sweep 4): a letter's subject line and salutation
# ("Subject: Ongoing Planning Services", "Dear Scott,", "Mr. Scott Surette"), a column's or a box's label printed alone
# ("Advertiser", "AGENCY", "Sales Office", "Product"), a month of a usage chart ("Dec", "Nov 2012"), opening hours
# ("Monday to Friday 8:30 am - 4:30 pm.", "Closed Saturday, Sunday, and Stat. Holidays").
_MONTH_WORDS = r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"

_WEEKDAYS = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"

_NOT_A_PARTY = re.compile(
    r"^(?:subject|re|regarding|reference|object)\s*:|^(?:dear|mr|mrs|ms|miss|messrs)\b\.?\s|"
    r"^(?:advertiser|agency|buyer|product|brand|station|salesperson|sales\s*(?:office|region|rep(?:resentative)?)|"
    r"account\s*executive|contractor|actual|estimated?|program|campaign|market|demo|revision|comments?|remarks?)"
    r"(?:\s*(?:no\b\.?|#|code|name|id\b))?\s*:?$|"
    rf"^(?:{_MONTH_WORDS})[a-z]*\.?(?:[\s-]*\d{{2,4}})?$|"
    rf"^(?:closed|open|hours)\b.*\b(?:{_WEEKDAYS}|holidays?)\b|^(?:{_WEEKDAYS})\b.*\d\s*(?:am|pm|a\.m|p\.m)\b"
)


def _not_a_name(text: str) -> bool:
    """``text`` is a title, heading, placeholder, staff line, field label with its value, a sentence or a place."""
    p = plain(text).strip()
    if _PLACEHOLDER.match(p) or _STAFF.match(p) or _PLACE.match(p) or _LIST_ITEM.match(p) or _CURRENCY_CODE.match(p):
        return True
    if _RECEIPT_WORDS.search(p) or _ADDRESS_LINE.match(p):
        return True
    if any(len(w) > 14 for w in p.split()) and _GLUED_TITLE_WORDS.search(p):
        return True  # OCR's words run together: "Questionsaboutyourinvoice/account", "Renseignementsgeneraux"
    words = p.split()
    if len(words) > 8:
        return True  # a sentence of the page's notes, not a name
    if _NOT_A_PARTY.search(p):
        return True  # a subject line, a salutation, a column's label, a month, opening hours
    if _COMPANY.search(p):
        return False  # "Chinook Courier & Office Services Ltd."
    if len(words) >= 4 and p.endswith(".") and len(words[-1].strip(".")) >= 3:
        return True  # a sentence: "A quick overview of your bill."
    if len(words) >= 4 and (text.strip()[:1].islower() or (p.endswith(".") and len(words[-1].strip(".")) > 4)):
        return True  # a sentence ("Thank you for your business."), not "Bluewater Equipment Rentals Co."
    return bool(_TITLE_WORDS.search(p) or _FIELD_AND_VALUE.match(p))


# The supplier's own label: "From:", "Seller", "Verkäufer", "Lieferant", "Fournisseur". Its value (after it, or on the
# line below) is the supplier's name.
_SELLER_LABEL = re.compile(
    r"^(?:from|seller|vendor|supplier|sold\s+by|issued\s+by|bill(?:ed)?\s+from|verkaufer|lieferant|"
    r"rechnungsersteller|leistungserbringer|fournisseur|vendeur|emetteur|leverancier|proveedor|fornitore)"
    r"(?![a-z])\s*:?\s*"
)

_SENDER_SPLIT = re.compile(r"\s+[•·|–-]\s+")

_SELLER_NOT = re.compile(r"^(?:#|no\b|nr\b|n°|number|id\b|code\b|num)|^\d")  # "Vendor # 4471", "Supplier ID"

# The supplier named with its registration numbers: "Canada Post registration nos.: HST/GST 119321495", "Numéros
# d'enregistrement de Postes Canada TVH/TPS 119321495".
_REG_NAMED_EN = re.compile(
    r"^(?P<name>[A-Z][\w&'’.\- ]{1,60}?)\s*(?:'s\s+)?(?:(?:gst|hst|tps|tvh|qst|tvq)[\s/]*)*"
    r"(?:registration|reg\.)\s*(?:nos?\.?|numbers?|#)?\s*:?\s*(?:gst|hst|tps|tvh|qst|tvq|bn|business)",
    re.I,
)

_REG_NAMED_FR = re.compile(
    r"^num[ée]ros?\s+d['’]\s*(?:enregistrement|inscription)\s+(?:de\s+la\s+|de\s+l['’]|du\s+|des\s+|de\s+)"
    r"(?P<name>[A-Z][\w&'’.\- ]{1,60}?)\s*:?\s*(?:gst|hst|tps|tvh|qst|tvq)",
    re.I,
)


def _seller_named(page: PageLayout) -> list[Reading]:
    """The supplier's name where the page gives it as such: after a seller's label ("From: Lions and Co.", or on
    the line under "Verkäufer"), or with its registration numbers ("Canada Post registration nos.: HST/GST ...")."""
    out: list[Reading] = []
    for line in page.lines:
        text = line.text.strip()
        p = plain(text)
        for rx in (_REG_NAMED_EN, _REG_NAMED_FR):
            m = rx.match(text)
            if m:
                name = m.group("name").strip(" .,:;")
                a = line.text.find(name)
                if _name_like(name) and a >= 0:
                    words = _span_words(line, a, a + len(name))
                    out.append(Reading("vendor_name", name, name, _boxes(words), 0.85 * _min_conf(words),
                                       "registration-line"))  # fmt: skip
                break
        parts = _SENDER_SPLIT.split(text)
        if len(parts) >= 3 and page.number == 1 and line.box.cy < 0.4:
            # The sender's line over the window of the envelope: "Hetzner Online GmbH • Industriestr. 25 • 91710
            # Gunzenhausen". Its first part is the supplier when it is a company's name.
            name = parts[0].strip()
            # The rest is an address ("Industriestr. 25", "91710 Gunzenhausen"), not a line item's description
            # ("Implementation Services - CA - GC").
            if (
                _name_like(name)
                and _COMPANY.search(plain(name))
                and any(any(c.isdigit() for c in x) for x in parts[1:])
            ):
                words = _span_words(line, 0, len(line.text.split(name, 1)[0]) + len(name))
                out.append(Reading("vendor_name", name, name, _boxes(words), 0.9 * _min_conf(words), "sender-line"))
            continue
        m = _SELLER_LABEL.match(p)
        if not m or _SELLER_NOT.match(p[m.end() :]) or (m.end() < len(p) and ":" not in p[: m.end()]):
            continue  # "Vendor # 4471"; "Lieferant GmbH" (a name that starts with the word), not "Lieferant: ..."
        rest = text[m.end() :].strip(" :")
        if rest:
            if _name_like(rest):
                words = _span_words(line, len(text) - len(rest), len(text))
                out.append(Reading("vendor_name", rest, rest, _boxes(words), 0.85 * _min_conf(words), "seller-label"))
            continue
        got = _under_seller_label(page, line)
        if got is not None:
            name, box, conf = got
            out.append(Reading("vendor_name", name, name, [box], 0.85 * conf, "seller-label"))
    return out


_NAME_LABEL = re.compile(r"^(?:name|firma|firmenname|company(?:\s*name)?|raison\s*sociale|nom)\s*:?$")


def _under_seller_label(page: PageLayout, label: Line) -> tuple[str, Box, float] | None:
    """The name under a seller's label: the first line below it (left edges lined up) that could be a name, past the
    seller's own fields printed first ("Nummer : 12345676"); "Name:" gives the value beside it."""
    h = max(label.box.height, 0.008)
    below = sorted((ln for ln in page.lines if ln is not label and 0 <= ln.box.y0 - label.box.y1 + 0.2 * h <= 6 * h
                    and abs(ln.box.x0 - label.box.x0) < 0.03), key=lambda ln: ln.box.y0)  # fmt: skip
    for nxt in below[:4]:
        p = plain(nxt.text).strip()
        beside = _right_of(page.lines, nxt)
        if _NAME_LABEL.match(p):
            if beside and _name_like(beside[0].text.strip(" :")):
                return beside[0].text.strip(" :"), beside[0].box, _min_conf(beside[0].words)
            return None
        if p.endswith(":") or (beside and beside[0].text.lstrip().startswith(":")) or _label_hits(nxt):
            continue  # one of the seller's fields: "Nummer : 12345676", "Globale Nummer:"
        name = nxt.text.strip()
        return (name, nxt.box, _min_conf(nxt.words)) if _name_like(name) else None
    return None


_STREET_START = re.compile(
    r"^(?:\d{1,6}[a-z]?(?:-\d{1,6})?[\s,]+[a-z]|p\.?\s?o\.?\s*box\b|c\.?p\.?\s*\d|"
    r"(?:suite|unit|ste|apt|bureau|floor|room)\.?\s*#?\s*\d)"  # "Suite 200, 123 King Street West", "Unit 4 - 88 ..."
)


def _amount_beside(lines: list[Line], line: Line) -> bool:
    """The next cell on ``line``'s row is an amount (not another label's: "Amount Due: $587.50" across the page)."""
    for o in _right_of(lines, line)[:2]:
        if _label_hits(o) or o.text.rstrip().endswith(":"):
            return False
        if any(looks_like_money(t) for t in o.text.split()):
            return True
    return False


def _over_address(lines: list[Line], line: Line, under: bool = False) -> bool:
    """``line`` is printed just over a street address or a PO box starting where it starts ("Distill Mobile" over
    "301-311 Richmond Road"); ``under``: just under one."""
    h = max(line.box.height, 0.008)
    return any(
        o is not line and abs(o.box.x0 - line.box.x0) < 0.02
        and 0 < ((line.box.y0 - o.box.y1) if under else (o.box.y0 - line.box.y1)) + 0.3 * h < 1.5 * h
        and _STREET_START.match(plain(o.text).strip())
        for o in lines
    )  # fmt: skip


def _under_remit_label(page: PageLayout, label: Line) -> tuple[str, Box, float] | None:
    """The name on the first line under a remit-to label on its own ("PLEASE REMIT TO:" over "TEKSYSTEMS CANADA
    CORP"), its left edge lined up with the label's (or indented a little)."""
    h = max(label.box.height, 0.008)
    beside = _right_of(page.lines, label)[:1]
    below = sorted((ln for ln in page.lines if ln is not label and 0 <= ln.box.y0 - label.box.y1 + 0.3 * h <= 3 * h
                    and -0.01 < ln.box.x0 - label.box.x0 < 0.04), key=lambda ln: ln.box.y0)  # fmt: skip
    # "Mail to:  Comcast Spotlight" in a cell beside the label, close to it; else the line under it.
    for nxt in [b for b in beside if b.box.x0 - label.box.x1 < 0.1] + below[:1]:
        name = _TRAILING_LABEL.sub("", _ACCOUNT_NAME.sub("", nxt.text.strip())).strip(" :.,")
        if _name_like(name):
            return name, nxt.box, _min_conf(nxt.words)
    return None


def _name_like(name: str) -> bool:
    """``name`` could be a supplier's name: a few words, letters, no address, contact or label."""
    p = plain(name)
    return (
        2 < len(name) < 70
        and sum(c.isalpha() for c in name) >= 3
        and sum(c.isdigit() for c in name) <= 3
        and not _NOT_NAME.match(p)
        and not _not_a_name(name)
        and not _CONTACT.search(p)
        and not _POSTAL_CODE.search(name)
        and not us_address(name)
    )


def _vendor_names(layout: DocLayout) -> list[Reading]:
    if not layout.pages:
        return []
    page = layout.pages[0]
    out: list[Reading] = [r for pg in layout.pages[:2] for r in _seller_named(pg)]
    heights = sorted((ln.box.height for ln in page.lines), reverse=True)
    tall = heights[min(2, len(heights) - 1)] if heights else 0.0
    customer_names = {plain(ln.text.strip()) for ln in layout.lines() if _in_bill_to(layout, ln)}
    # The line-items table starts below the header: nothing from it is the supplier's name.
    table_top = min((ln.box.y0 for ln in page.lines if _table_header_line(ln, page.lines)), default=1.0)
    for line in page.lines:
        text = line.text.strip()
        p = plain(text)
        title = _TITLE_PREFIX.match(p) or _ACCOUNT_NAME.match(text)  # "A/C Name: MICROSOFT CANADA INC": the name
        if title and title.end() < len(text):  # "SALES INVOICE #26/41538 Fraser Valley Fasteners Co."
            line = Line(_span_words(line, title.end(), len(text)), line.box, text[title.end() :].strip())
            line = Line(line.words, union_all([w.box for w in line.words]) or line.box, line.text)
            text, p = line.text, plain(line.text)
        m = _REMIT.search(p)
        if m:
            name = _TRAILING_LABEL.sub("", text[m.end() :].strip(" :.,")).strip(" :.,")
            if name[:1] in "/(" or _REMIT_WHERE.fullmatch(plain(name)):
                name = ""  # "Please Remit To/S'il vous plaît remettre à:", "REMIT TO BANK": no name
            if 2 < len(name) < 70 and not _NOT_NAME.match(plain(name)) and _name_like(name):
                words = _span_words(line, m.end(), m.end() + len(name))
                out.append(Reading("vendor_name", name, name, _boxes(words), 0.9 * _min_conf(words), "remit-to"))
            elif not name or name[:1] in "/(" or not _name_like(name):
                # "PLEASE REMIT TO:", "Please Remit To/S'il vous plaît remettre à:", "Send Payment To:": the name is on
                # the line under the label.
                got = _under_remit_label(page, line)
                if got is not None:
                    out.append(Reading("vendor_name", got[0], got[0], [got[1]], 0.9 * got[2], "remit-to" + UNMEASURED))
            continue
        if line.box.cy > 0.33 or line.box.y0 >= table_top - 0.002 or _in_bill_to(layout, line):
            continue
        if _over_attention(page.lines, line):
            continue  # "Groupe Boisvert ltée" over "Attn: Accounts Payable": the customer, its "Bill To" unread
        if len(text) < 3 or len(text) > 70 or _NOT_NAME.match(p) or sum(c.isdigit() for c in text) > 3:
            continue
        if _not_a_name(text) or _SELLER_LABEL.fullmatch(p):
            continue  # a title, heading, placeholder, staff line, place, or the seller's label itself
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
        if not _COMPANY.search(p) and _amount_beside(page.lines, line):
            continue  # a label with its amount on the row ("Previous Balance   111.54"), not a name
        if not _COMPANY.search(p) and _over_address(page.lines, line, under=True):
            continue  # the town under a street address ("845 Hwy. 401" over "Port Hope"): a name is over its address
        score = 0.35
        if _COMPANY.search(p) or (_GLUED_FORM.search(p) and any(w.source not in _AS_PRINTED for w in line.words)):
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
    for sender in [r for r in out if r.method == "sender-line" and r.boxes]:
        # The address block under the sender's line is the customer's, shown through the envelope's window.
        sb = sender.boxes[0]
        for r in out:
            b = r.boxes[0] if r.boxes else None
            if r.method == "top-of-page" and b and b.page == sb.page and 0 < b.y0 - sb.y1 < 0.12 and b.x0 < sb.x1:
                r.score *= 0.5
    for pg in layout.pages[1:2]:
        # "... cheque or money order payable to Canada Post Corporation;" in the payment notes on the back: a
        # company's name only (notes say "payable to your bank" too).
        for line in pg.lines:
            m = _REMIT.search(plain(line.text))
            name = line.text[m.end() :].strip(" :.,;") if m else ""
            if m and _name_like(name) and _COMPANY.search(plain(name)):
                words = _span_words(line, m.end(), len(line.text))
                out.append(Reading("vendor_name", name, name, _boxes(words), 0.8 * _min_conf(words), "remit-to"))
    for r in out:
        if UNMEASURED in r.method and any(o is not r and UNMEASURED not in o.method and _same_name(o.value, r.value)
                                          for o in out):  # fmt: skip
            r.method = r.method.replace(UNMEASURED, "")  # printed again elsewhere on the page: two places agree
        elif r.method.startswith("remit-to") and UNMEASURED in r.method:
            first = plain(str(r.value)).split()[:1]
            for o in out:
                if o.method == "top-of-page" and o.score >= 0.6 and plain(str(o.value)).split()[:1] == first:
                    # "Contoso Billing" under "Remit to:", "CONTOSO LTD." at the top: one company, its legal name
                    r.score = min(r.score, 0.95 * o.score)
    out.sort(key=lambda r: -r.score)
    if out and out[0].method == "top-of-page" and out[0].score < WEAK_NAME:
        # No company's form, not printed larger, low on the page ("SOMETHING JOHN" over a street address, "Previous
        # Balance" on a utility bill's summary): a guess the benchmark's names never test. A person checks it.
        out[0].method += UNMEASURED
    # A rival: another name at the top of the page (a letterhead's brand and the operator's legal name).
    rivals = [r for r in out[1:] if not _same_name(r.value, out[0].value) and r.method == "top-of-page"]
    if out and out[0].method == "top-of-page" and rivals and rivals[0].score >= RIVAL_SHARE * out[0].score:
        # "CANADIAN TIRE 235" over "Gestions Daniel Picard Inc": a store's brand and its operator's legal name, or a
        # name and another line of the letterhead. Which one is the supplier the page does not say.
        out[0].method += RIVAL_NAME
    return out


RIVAL_SHARE = 0.6  # a second name scored this close to the first one is its rival

WEAK_NAME = 0.5  # a name from the top of the page scored below this has nothing of a supplier's name about it


def _same_name(a: str, b: str) -> bool:
    """Two readings of one name: one inside the other, or an OCR slip apart ("ANADIANTIRE235", "CANADIAN TIRE235")."""
    from difflib import SequenceMatcher

    x, y = re.sub(r"[^a-z0-9]", "", plain(str(a))), re.sub(r"[^a-z0-9]", "", plain(str(b)))
    return bool(x and y) and (x in y or y in x or SequenceMatcher(None, x, y).ratio() >= 0.85)
