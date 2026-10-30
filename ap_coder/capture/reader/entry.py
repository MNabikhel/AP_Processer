# ruff: noqa: E501  (label and pattern tables read best one per line)
"""The entry point: read_fields and the reading it returns."""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from collections import Counter

from ..normalize import (
    currency_evidence,
    find_currency,
    find_gst_numbers,
    find_qst_numbers,
    infer_day_first,
    parse_dates,
)
from ..types import (
    AMOUNT_FIELDS,
    EITHER_TAX,
    PART_OF_TERMS,
    TAKEN_OFF,
    UNMEASURED,
    DocLayout,
    Line,
    PageLayout,
    Reading,
)
from ._common import (
    _AS_PRINTED,
    _TAX_FIELDS,
    _US_ADDRESS,
    PREPAID,
    _table_header_line,
    plain,
    plain_with_offsets,
    us_address,
)
from .labels import (
    _DISTRACT,
    _PST_OR_QST,
    _PST_REG,
    _QST_OR_PST,
    _QST_REG,
    _UNMEASURED_LABEL,
    _LabelHit,
)
from .line_items import (
    STRAIGHTEN_FROM,
    _credit_titled,
    _on_tilted_page,
    credit_printed_positive,
    credit_titled,
    read_line_items,
    straightened,
)
from .patterns import (
    _CITIES,
    _POSTAL_CODE,
    _PROVINCES,
    _REMIT,
    _in_bill_to,
    _registration_numbers,
    _vendor_names,
)
from .spatial import (
    _aligned,
    _below,
    _labelled,
    _right_of,
    labels_under_values,
)
from .totals import (
    _RATES,
    _solve_totals,
)
from .values import (
    _DAY_FIRST,
    _DEPOSIT,
    _DISCOUNT_SENTENCE,
    _ON_RECEIPT,
    _amounts_in,
    _boxes,
    _id_from,
    _label_hits,
    _looks_like_phone_or_postal,
    _min_conf,
    _span_words,
    _terms,
    _value_reading,
)


def _us_vendor(layout: DocLayout) -> bool:
    """The supplier's address (top of the first page, outside the customer's block) is in the US."""
    if not layout.pages:
        return False
    for line in layout.pages[0].lines:
        if line.box.cy < 0.3 and not _in_bill_to(layout, line) and us_address(line.text):
            return True
    return False


def _us_supplier(layout: DocLayout) -> bool:
    """The first page prints a US address (a state and its ZIP code) outside the customer's block, and nothing
    Canadian outside it: no GST/HST, PST or QST (a word or a number), no postal code or province. A Canadian customer
    named under "Bill to" does not count against it; an unlabelled Canadian address does."""
    if not layout.pages:
        return False
    text = layout.text()
    if _CANADIAN_TAX.search(text) or find_gst_numbers(text) or find_qst_numbers(text):
        return False
    if re.search(r"\bCA?\$|\bCDN\b|\bCAD\b|canadian\s+dollar", text, re.I):
        return False
    page = layout.pages[0].lines
    lines = [ln for ln in page if not _in_bill_to(layout, ln)]
    if any(_POSTAL_CODE.search(ln.text) or _PROVINCE_NAME.search(plain(ln.text)) or _CA_CITY.search(plain(ln.text))
           for ln in lines):  # fmt: skip
        return False
    # A remit-to or lockbox address is where the money goes (a Canadian supplier may bank in the US), not the supplier's.
    remit = {id(o) for ln in page if _REMIT.search(plain(ln.text)) or _LOCKBOX.search(ln.text)
             for o in [ln, *_below(page, ln, ln.box)[:3]]}  # fmt: skip
    return any(us_address(_STATE_COMMA.sub("", ln.text)) for ln in lines if id(ln) not in remit)


_CA_CITY = re.compile(rf"(?<![a-z])(?:{_CITIES})(?![a-z])")

_LOCKBOX = re.compile(r"lock\s*box", re.I)

_PROVINCE_NAME = re.compile(rf"(?<![a-z])(?:{_PROVINCES})(?![a-z])")


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
    in (it settles a 03/04/2026 the page itself does not: invoices arrive days after their date).
    A tilted scan is read straightened, as its line items are (``straightened``): at 2.5 degrees a value two inches
    right of its label sits a row lower, level with the next label's. The boxes are given on the page as scanned."""
    skews = {page.number: page.skew for page in layout.pages if abs(page.skew) >= STRAIGHTEN_FROM}
    if not skews:
        return _read_fields_on(layout, received)
    level = straightened(layout)
    level = DocLayout([dataclasses.replace(p, skew=0.0, turn=page.turn, dpi=page.dpi) for p, page in
                       zip(level.pages, layout.pages, strict=True)], layout.source, layout.pages_left_out,
                      layout.pages_unread_locally)  # fmt: skip
    cands = _read_fields_on(level, received)
    for readings in cands.values():
        for r in readings:
            r.boxes = [_on_tilted_page(b, skews[b.page]) if b.page in skews else b for b in r.boxes]
    return cands


def _read_fields_on(layout: DocLayout, received: dt.date | None) -> dict[str, list[Reading]]:
    text = layout.text()
    order = infer_day_first(text)
    # US invoices print month first. A Canadian supplier need not, whatever its currency or a US line on its letterhead
    # (a warehouse, a label's number read as a ZIP code): its Canadian postal code or GST/HST number tells.
    canadian = _canadian_vendor(layout) or any(valid for *_, valid in find_gst_numbers(text))
    # Dates printed with points (05.03.2018) are Europe's, day first: the US does not print them so ("DE 80333
    # München" reads as a Delaware ZIP code, a German supplier's invoice may be in US dollars).
    dotted = _dotted_dates(text)
    if order is None and not canadian and not dotted and (_us_vendor(layout) or find_currency(text) == "USD"):
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
    if not canadian and not dotted and layout.pages and _us_page(layout.pages[0]):
        # Nothing settles 9/10/2020, but the page prints a US address (the customer's, or the supplier's where the
        # reader does not take it for the supplier's) and nothing Canadian: US invoices print the month first. Shown
        # month first, still as ambiguous (a person checks it).
        out = options[False]
        for f in ("invoice_date", "due_date"):
            for r in out.get(f, []):
                if any(amb for *_, amb in parse_dates(str(r.raw))[:1]) and "ambiguous" not in r.method:
                    r.method += "-ambiguous"
        return out
    return cands


def _dotted_dates(text: str) -> bool:
    """The page's numeric dates (day, month and year) are printed with points: 05.03.2018."""
    seps = [m.group(0)[len(m.group(1))] for m in _NUMERIC_DATE.finditer(text)]
    return bool(seps) and seps.count(".") > len(seps) / 2


_NUMERIC_DATE = re.compile(r"(?<!\d)(?<!\d[.,/-])(\d{1,2})[-/.]\d{1,2}[-/.](?:\d{4}|\d{2})(?!\d)(?![.,/-]\d)")


def _us_page(page: PageLayout) -> bool:
    """The page prints a US address (a state and ZIP code, not "CA 12345" alone: that may be Canada) and nothing Canadian
    anywhere: no postal code or province, no GST/HST, PST, QST, TPS or TVQ."""
    text = "\n".join(ln.text for ln in page.lines)
    if _POSTAL_CODE.search(text) or _CANADIAN_TAX.search(text):
        return False
    for ln in page.lines:
        found = _US_ADDRESS.search(_STATE_COMMA.sub("", ln.text))
        if found and not re.search(r"(?<![A-Za-z])CA\s+\d{5}", found.group(0)):
            return True
    return False


_CANADIAN_TAX = re.compile(r"(?<![A-Za-z])(?:GST|HST|PST|QST|TPS|TVQ|TVH|TVP)(?![A-Za-z])")

_STATE_COMMA = re.compile(r"(?<=\b[A-Z]{2}),(?=\s*\d{5}\b)")  # "Walnut, WY, 98432": the comma before the ZIP code


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


_PREPAYMENT = re.compile(r".*(pre\s*-?\s*payment|deposit)")

_DISCOUNT = re.compile(
    r".*(discount|escompte|rabais|remise|abschlag|rabatt|abzgl|abzuglich|nachlass|pre\s*-?\s*payment|deposit)"
)

_GLUED_TITLE = re.compile(
    r"^(?:(?i:tax|sales)\s*)?(?i:invoice|facture)\s*(?:(?i:n°|no)\.?\s*|#\s*)?([A-Z]{0,4}[-/]?\d[\w/-]*)$"
)


def _glued_title_numbers(layout: DocLayout) -> list[Reading]:
    """OCR glued the title to the number beside it: "INVOICEA-2026-29266", "TAXINVOICEA-90595"; and read the number
    a second time on its own ("TAX INVOICE82-78605 82-78605"): once."""
    out = []
    for line in layout.lines():
        if all(w.source in _AS_PRINTED for w in line.words):
            continue
        tokens = line.text.split()
        if len(tokens) >= 2 and len(tokens[-1]) >= 3 and tokens[-2].endswith(tokens[-1]) and tokens[-2] != tokens[-1]:
            tokens = tokens[:-1]
        m = _GLUED_TITLE.match("".join(tokens))
        if m and len(m.group(1)) >= 3:
            out.append(Reading("invoice_number", m.group(1), m.group(1), [line.box], 0.75 * _min_conf(line.words),
                               "glued-title"))  # fmt: skip
    return out


# "Facture n° 562044387 du 02 juillet 2015", "Rechnung Nr. 471102 vom 05.03.2018", "Invoice # RE-508 issued at
# 2020-11-21": the word that dates the document, right after its number.
_DATED_WORD = re.compile(r"\s*[,;]?\s*(?:vom|von|du|dated|issued(?:\s*(?:on|at))?|del|dd\.?)(?![a-z])\s*:?")

_PERIOD_AFTER = re.compile(r"\s*(?:-|–|au|to|bis|al|through|thru)(?![a-z])")


def _dated_number(hit: _LabelHit, lines: list[Line]) -> Reading | None:
    """The invoice date printed right after the invoice number its label reads on the line ("Nr. 471102 vom
    05.03.2018"), or in the next pieces of the row ("Nr. 471102 | vom | 05.03.2018"). Not a period's first day
    ("du 01/03/2026 au 31/03/2026"). Its own method ("number-dated"): it is measured apart from labelled dates."""
    line = hit.line
    got = _id_from(line.text[hit.end :], hit.end)
    if not got:
        return None
    pieces = [(line, got[2])] + [(other, 0) for other in _right_of(lines, line)[:2]]
    dated = False
    for piece, start in pieces:
        rest = piece.text[start:]
        if not dated:
            m = _DATED_WORD.match(plain(rest))
            if not m:
                if rest.strip():
                    return None
                continue  # the number ends its piece of the row: the word may be in the next one
            dated, start = True, start + m.end()
            rest = piece.text[start:]
            if not rest.strip():
                continue
        found = parse_dates(rest, prefer_day_first=_DAY_FIRST.get())
        if not found or rest[: found[0][1]].strip(" :,"):
            return None
        d, a, b, ambiguous = found[0]
        if _PERIOD_AFTER.match(plain(rest[b:])) and parse_dates(rest[b:]):
            return None  # "du 01/03/2026 au 31/03/2026": a period, not the invoice's date
        words = _span_words(piece, start + a, start + b)
        method = "number-dated" + ("-ambiguous" if ambiguous else "")
        return Reading("invoice_date", d.isoformat(), rest[a:b], _boxes(words),
                       0.85 * hit.strength * (0.6 if ambiguous else 1.0) * _min_conf(words), method)  # fmt: skip
    return None


# A time after a date: "6/10/2019 13:59", "2015-08-23 20:45:57", OCR's "Ju1/15/2017_12:25".
_TIME_AFTER = re.compile(r"[\s_,.@-]*(?:a\s+|at\s+|à\s+)?\d{1,2}\s?:\s?\d(?:\d(?::\d\d)?(?:\s?[ap]\.?m\.?)?)?", re.I)

# A dated line that says what else happened then: a service call's times, a payment, a delivery, a period.
_NOT_ISSUED = re.compile(
    r"(?<![a-z])(?:received|recu|paid|payee?|payment|paiement|completed|complete|termine|time\s*(?:in|out)|"
    r"arrival|arrive|depart(?:ure)?|call|appel|delivered|delivery|livre|livraison|shipped|ship|expedie|period|"
    r"periode|ending|ended|from|to|du|au|thru|through|start|end|labou?r|main\s*d'?\s*oeuvre|generated|genere|created|"
    r"cree|run|report|rapport|log|journal|printed|imprime)(?![a-z])"
)

STAMP_MARGIN = (
    0.04  # a stamp printed this close to the top of the page (fraction of its height) is a printer's or a fax's
)

_FAX_HEADER = re.compile(r"(?<![a-z])(?:from|to|fax|de|a)\s*:|(?<![a-z])p\.\s?\d{1,3}(?!\d)|page\s*\d")


def _stamped_dates(layout: DocLayout) -> list[Reading]:
    """A receipt's date and time, printed with no label ("6/10/2019 13:59", "27Mar2018 18:46"): when it was issued.
    The topmost one on each page, outranked by any labelled date ("date-time": measured apart, never verified on a
    labelled date's record). Not after another date's label ("Printed", "Due", "Ship date")."""
    out: dict[int, Reading] = {}
    lines = sorted(layout.lines(), key=lambda ln: (ln.box.page, ln.box.y0))
    for line in lines:
        page = line.box.page
        if (page in out or line.box.y0 < STAMP_MARGIN or line.box.y1 > 1 - STAMP_MARGIN
                or _FAX_HEADER.search(plain(line.text))):  # fmt: skip
            continue  # a printer's or a fax's stamp in the page's top margin ("SEP-13-2025 11:26 FROM: ..."), not the date
        found = parse_dates(line.text, prefer_day_first=_DAY_FIRST.get())
        if not found:
            continue
        d, a, b, ambiguous = found[0]
        if not _TIME_AFTER.match(line.text, b) and not (
            not line.text[b:].strip()
            and any(_TIME_AFTER.fullmatch(o.text.split()[0]) for o in _right_of(lines, line)[:1] if o.text.split())
        ):
            continue  # no time after the date, on its line or as the next piece of its row ("12/22/17 | 14:40:29")
        before = plain(line.text[:a])
        if (
            _NOT_ISSUED.search(plain(line.text))
            or any(rx.search(before) for rx in _DISTRACT["invoice_date"])
            or any(h.field in ("due_date", "invoice_number", "po_number") for h in _label_hits(line))
        ):
            continue
        words = _span_words(line, a, b)
        method = "date-time" + ("-ambiguous" if ambiguous else "")
        out[page] = Reading("invoice_date", d.isoformat(), line.text[a:b], _boxes(words),
                            0.45 * (0.6 if ambiguous else 1.0) * _min_conf(words), method)  # fmt: skip
    return list(out.values())


_DOC_TITLE = re.compile(
    r"(?:[a-z]*rechnung|(?:tax\s*|sales\s*|commercial\s*)?invoice|facture|factuur|fattura|factura|gutschrift|"
    r"credit\s*note|note\s*de\s*credit|avoir)(?![a-z])"
    r"(?!\s*(?:due|period|periode|total|totale|amount|montant|date|datum|balance|solde|no\b|number|nr|#|ref|to\b|a\b))"
)


def _title_dates(layout: DocLayout) -> list[Reading]:
    """The date printed alone beside the document's title, on its row: "Beitragsrechnung | 24. Januar 2014",
    "INVOICE | March 3, 2026". The first page's first such date; its own method ("title-dated"), weaker than a
    labelled date."""
    if not layout.pages:
        return []
    lines = layout.pages[0].lines
    for line in sorted(lines, key=lambda ln: ln.box.y0):
        title = plain(line.text).strip()
        if line.box.cy > 0.5 or not _DOC_TITLE.match(title) or parse_dates(line.text) or _NOT_ISSUED.search(title):
            continue
        right = _right_of(lines, line)[:1]
        found = parse_dates(right[0].text, prefer_day_first=_DAY_FIRST.get()) if right else []
        if not found or right[0].text[: found[0][1]].strip() or right[0].text[found[0][2] :].strip(" ."):
            continue  # nothing beside the title, or more than a date
        d, a, b, ambiguous = found[0]
        words = _span_words(right[0], a, b)
        method = "title-dated" + ("-ambiguous" if ambiguous else "")
        return [Reading("invoice_date", d.isoformat(), right[0].text[a:b], _boxes(words),
                        0.5 * (0.6 if ambiguous else 1.0) * _min_conf(words), method)]  # fmt: skip
    return []


def _letter_dates(layout: DocLayout) -> list[Reading]:
    """A letter-style invoice's date: a line that is a date written out and nothing else ("June 30, 2021"), in the top
    part of the first page, the only such line there. Read only when no date has a label; its own method
    ("letter-dated"): a person checks it."""
    if not layout.pages:
        return []
    found = []
    for line in layout.pages[0].lines:
        text = line.text.strip()
        dates = parse_dates(text, prefer_day_first=_DAY_FIRST.get()) if line.box.cy < 0.45 else []
        if (
            len(dates) == 1
            and dates[0][1] == 0
            and dates[0][2] >= len(text.rstrip("."))
            and re.search(r"[A-Za-z]{3}", text)
            # alone on its row: not a label's value ("Invoice Due Date | Nov 1, 2026")
            and not any(o is not line and abs(o.box.cy - line.box.cy) < 0.006 for o in layout.pages[0].lines)
        ):
            found.append((line, dates[0]))
    if len(found) != 1:
        return []
    line, (d, a, b, _amb) = found[0]
    words = _span_words(line, a, b)
    return [
        Reading("invoice_date", d.isoformat(), line.text[a:b], _boxes(words), 0.45 * _min_conf(words), "letter-dated")
    ]


_CUSTOMER_HEADING = re.compile(
    r"^(?:client|customer|cust\.?|compte|account|acheteur|buyer|bill(?:ed)?\s*to|sold\s*to|ship\s*to|"
    r"facturee?\s*a|vendu\s*a)(?![a-z])"
)


def _customer_line(layout: DocLayout, line: Line, by_page: dict[int, list[Line]]) -> bool:
    """``line`` is in the customer's block: a bill-to region, or a customer heading one or two lines above it."""
    if _in_bill_to(layout, line) or _CUSTOMER_HEADING.match(plain(line.text).strip()):
        return True
    h = max(line.box.height, 0.008)
    return any(
        o is not line
        and o.box.cy < line.box.cy
        and line.box.y0 - o.box.y1 <= 2.5 * h
        and _aligned(line.box, o.box)
        and _CUSTOMER_HEADING.match(plain(o.text).strip())
        for o in by_page.get(line.box.page, [])
    )


# An advertising invoice's agency commission (sweep 4, TV stations' invoices to agencies): "Gross Total 17,000.00 |
# Commission (2,550.00) | Net Total 14,450.00", "Less: Agency Fee", "Rep. Firm Commission". The agency pays the net.
_TOTAL_SALE = re.compile(r"total\s*sales?\b")

_COMMISSION = re.compile(
    r"^(?:less\s*:?\s*)?(?:(?:agency|rep\.?(?:\s*firm)?|representative|[a-z]{2,4}\s+rep\.?)\s*(?:commission|fee)s?|"
    r"commissions?)(?![a-z])"
)

_GROSS = re.compile(r"^(?:total\s*)?gross(?![a-z])")

_NET = re.compile(r"^(?:total\s*)?net(?![a-z])(?!\s*\d)")  # "Net Total", "Net Amount Due"; not "Net 30"


def _net_of_commission(hits: dict[str, list[_LabelHit]], by_page: dict[int, list[Line]]) -> None:
    """On a page that takes an agency's commission off the gross ("Gross Total / Commission / Net Total"), the gross
    (on that page or another: "Gross Total" at the foot of the schedule, the commission on the next page) is neither
    the invoice's total nor its subtotal, and a net amount printed under the commission is the total to
    pay ("Net Total" is a subtotal's label elsewhere). A net amount stays a subtotal too when a total is printed
    under it (a tax charged on the net: "Net Amount Due 29,962.50 | Excise Tax 1,411.83 | Amount Due 31,374.33")."""
    below: dict[int, float] = {}  # page -> the top of its first commission line with an amount
    for page, page_lines in by_page.items():
        for ln in page_lines:
            p = plain(ln.text).strip()
            if _COMMISSION.match(p) and (
                _amounts_in(ln.text)
                or any(
                    _amounts_in(o.text)
                    for o in page_lines
                    if o.box.x0 > ln.box.x1 and abs(o.box.cy - ln.box.cy) < 0.006
                )
            ):
                below[page] = min(below.get(page, 1.0), ln.box.y0)
    if not below:
        return
    grossed = any(_GROSS.match(plain(h.label_text)) for f in ("subtotal", "grand_total") for h in hits.get(f, []))
    for field in ("subtotal", "grand_total"):
        hits[field] = [h for h in hits.get(field, []) if not _GROSS.match(plain(h.label_text))]
    totals = hits.get("grand_total", [])
    for hit in list(hits.get("subtotal", [])):
        page = hit.line.box.page
        if page not in below or hit.line.box.y0 < below[page] or not _NET.match(plain(hit.label_text)):
            continue
        if not any(t.line is hit.line for t in totals):
            totals.append(_LabelHit("grand_total", hit.line, 0.9, hit.end, hit.label_text, hit.start, unmeasured=True))
        if not any(t.line.box.page == page and t.line.box.y0 > hit.line.box.y1 and not _NET.match(plain(t.label_text))
                   for t in totals):  # fmt: skip
            hits["subtotal"].remove(hit)  # nothing charged on it: the net is the total, not a subtotal
    for t in totals:
        if grossed or (
            _NET.match(plain(t.label_text)) and t.line.box.page in below and t.line.box.y0 >= below[t.line.box.page]
        ):
            t.unmeasured = True  # a total on a document that takes a commission off the gross: AP's call
    hits["grand_total"] = totals


_TAX_WORDS = re.compile(r"(?<![a-z])(gst|hst|pst|qst|rst|tps|tvh|tvq|tvp)(?![a-z])")


def _tax_named_by_page(cands: dict[str, list[Reading]], text: str) -> None:
    """A Canadian invoice that prints its tax only as "Tax (13%)" or "Total Taxes" and names one tax elsewhere ("HST
    Number: 835582081", "HST-14015 0269" over the tax table): the tax is that one when its rate fits the subtotal
    (13 to 15% for the HST, 5% for the GST). Marked ``UNMEASURED``: the benchmark always names its taxes."""
    if any(cands.get(f) for f in _TAX_FIELDS) or not cands.get("tax_total") or not cands.get("subtotal"):
        return
    tax, sub = cands["tax_total"][0], cands["subtotal"][0]
    if not sub.value or not tax.value:
        return
    named = set(_TAX_WORDS.findall(text))
    if named & {"pst", "qst", "rst", "tvq", "tvp"}:
        return  # a provincial tax may be in it too
    rate = tax.value / sub.value
    if named & {"hst", "tvh"} and any(abs(rate - r) <= 0.0005 for r in _RATES["hst_amount"]):
        field = "hst_amount"
    elif named & {"gst", "tps"} and not named & {"hst", "tvh"} and abs(rate - 0.05) <= 0.0005:
        field = "gst_amount"
    elif named == {"gst", "hst"} and abs(rate - 0.05) <= 0.0005:
        field = "gst_amount"  # "GST/HST No." printed, taxed at 5%: the GST
    else:
        return
    cands[field] = [Reading(field, tax.value, tax.raw, list(tax.boxes), 0.9 * tax.score,
                            tax.method.split("+")[0] + "+named-by-page" + UNMEASURED)]  # fmt: skip


def _read_fields(layout: DocLayout) -> dict[str, list[Reading]]:
    lines = list(layout.lines())
    by_page: dict[int, list[Line]] = {}
    for ln in lines:
        by_page.setdefault(ln.box.page, []).append(ln)
    under = {page for page, page_lines in by_page.items() if labels_under_values(page_lines)}
    hits: dict[str, list[_LabelHit]] = {}
    discounts: list[Reading] = []
    for ln in lines:
        in_table_head = None
        for hit in _label_hits(ln):
            if hit.field in AMOUNT_FIELDS:
                if in_table_head is None:
                    in_table_head = _table_header_line(ln, by_page.get(ln.box.page, []))
                if in_table_head:
                    continue  # "Total" over the line-items' amount column is not the invoice total
            hits.setdefault(hit.field, []).append(hit)
    if any(h.credit_only for h in hits.get("invoice_number", [])) and not credit_titled(layout):
        hits["invoice_number"] = [h for h in hits["invoice_number"] if not h.credit_only]  # "Memo No" on an invoice
    _net_of_commission(hits, by_page)
    taxes_at = [h.line for f in (*_TAX_FIELDS, "tax_total") for h in hits.get(f, [])]
    hits["subtotal"] = [
        h
        for h in hits.get("subtotal", [])
        if not _TOTAL_SALE.match(plain(h.label_text))
        or any(t.box.page == h.line.box.page and t.box.y0 > h.line.box.y1 - 0.002 for t in taxes_at)
    ]  # "Total Sale" is the subtotal only over the taxes; under them ("HST 71.50 | Total Sale 621.50") it is the total
    for field in _TAX_FIELDS:
        # A tax's code in a column of the line items ("HST ON" on every row, the line's amount beside it): a tax is
        # printed once, or once a rate, not on three rows and more with the same words.
        rows = Counter((h.line.box.page, plain(h.line.text).strip()) for h in hits.get(field, []))
        if any(n >= 3 for n in rows.values()):
            hits[field] = [h for h in hits[field] if rows[(h.line.box.page, plain(h.line.text).strip())] < 3]
    cands: dict[str, list[Reading]] = {}
    number_dates: list[Reading] = []
    for field, fhits in hits.items():
        page_lines = by_page
        readings: list[Reading] = []
        fallback: list[Reading] = []
        for hit in fhits:
            if hit.weak and field == "invoice_number" and _customer_line(layout, hit.line, by_page):
                continue  # "Client / Numéro : C-4471": the customer's number, under the customer's heading
            got = _labelled([hit], page_lines.get(hit.line.box.page, []), hit.line.box.page in under)
            if hit.unmeasured or (field in _UNMEASURED_LABEL and _UNMEASURED_LABEL[field].match(hit.label_text)):
                for r in got:
                    r.method += UNMEASURED
            if hit.unmeasured:
                for r in got:
                    r.method += TAKEN_OFF  # the net after a commission (``_net_of_commission``)
            if hit.weak:
                for r in got:
                    r.method = "weak-" + r.method  # measured apart: never on a real label's record
            if hit.fallback:
                fallback += got
                continue
            if field == "other_charges" and _DISCOUNT.match(plain(hit.label_text)):
                for r in got:
                    r.value = -abs(r.value)  # a discount lowers the total, however it is printed
                    discounts.append(r)
                    if _PREPAYMENT.match(plain(hit.label_text)):
                        r.method += PREPAID
            if field in ("pst_amount", "qst_amount") and (
                _PST_OR_QST.search(hit.label_text) or _QST_OR_PST.match(plain(hit.line.text)[hit.start :])
            ):
                for r in got:
                    r.method += EITHER_TAX
            if field == "invoice_number" and any(r.method.endswith("label-inline") for r in got):
                dated = _dated_number(hit, page_lines.get(hit.line.box.page, []))
                if dated:
                    number_dates.append(dated)
            readings += got
        cands[field] = readings or fallback  # "Receipt Date" only when no other date label reads a value
    unlabelled = number_dates + _stamped_dates(layout) + _title_dates(layout)
    if not cands.get("invoice_date") and not unlabelled:
        unlabelled = _letter_dates(layout)
    labelled = cands.get("invoice_date") or []
    if labelled and unlabelled:
        # A date read with no label of its own ranks below every labelled one: "Invoice # 20417 dated 2026-08-15" in
        # a credit note's body, a receipt's stamp, a date beside the title are not over "Date: 2026-10-01".
        below = 0.5 * min(r.score for r in labelled)
        for r in unlabelled:
            r.score = min(r.score, below)
    cands.setdefault("invoice_date", []).extend(unlabelled)
    if not cands.get("payment_terms"):
        # "Due Date: Upon Receipt" (no date): the terms, printed where the due date goes.
        for hit in hits.get("due_date", []):
            terms = _value_reading("payment_terms", hit.line, hit.end, 0.95 * hit.strength, "label-inline")
            if terms and terms.value == "Due on receipt":
                cands.setdefault("payment_terms", []).append(terms)
    if not cands.get("payment_terms"):
        # Terms printed only in a note ("... Payable on receipt."): weaker than a labelled value, so a person confirms.
        for line in (ln for page in by_page.values() for ln in page):
            m = _ON_RECEIPT.search(line.text)
            terms = _value_reading("payment_terms", line, m.start(), 0.6, "label-inline") if m else None
            if terms:
                cands.setdefault("payment_terms", []).append(terms)
                break
    if not cands.get("payment_terms"):
        cands["payment_terms"] = _terms_sentences(lines)
    if not cands.get("due_date"):
        cands["due_date"] = _due_sentences(lines)
    if not cands.get("po_number") and by_page:
        cands["po_number"] = _po_mentions(by_page[min(by_page)]) or _po_as_your_reference(by_page, under)
    if credit_printed_positive(layout):
        stated = not _credit_titled(layout)  # a credit only by a sentence of its body: a person confirms the sign
        for field in AMOUNT_FIELDS:
            for r in cands.get(field, []):
                r.value = -r.value if r.value else r.value
                if stated:
                    r.method += UNMEASURED
    elif discounts and _negative_totals(cands):
        # A credit note printed with negative totals ("Positionssumme -7,90") and its discount as printed (0,23): the
        # discount lowers the credit, it is added to it.
        for r in discounts:
            r.value = abs(r.value)
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
    _settle_pst_or_qst(cands, plain(layout.text()))
    cands["vendor_name"] = _vendor_names(layout)
    currency = currency_evidence(layout.text())
    if currency:
        # Several codes and none clearly the invoice's: its own evidence pattern, so it is not calibrated as sure.
        method = "pattern" if currency[1] >= 0.8 else "pattern-several-codes"
        cands["currency"] = [Reading("currency", currency[0], currency[0], [], currency[1], method)]
    elif "$" in layout.text() and _us_supplier(layout):
        # Dollars from a supplier in the US, with nothing Canadian about it: US dollars. Never printed so, so a person
        # checks it (``UNMEASURED``): a Canadian supplier's "$" is Canadian dollars.
        cands["currency"] = [Reading("currency", "USD", "$", [], 0.8, "us-supplier" + UNMEASURED)]
    items = read_line_items(layout)
    line_sum = round(sum(i.amount for i in items if i.amount is not None), 2) if items else None
    cands = _solve_totals(cands, line_sum)
    if any(TAKEN_OFF in r.method for r in cands.get("other_charges", [])):
        for r in cands.get("grand_total", []):
            if TAKEN_OFF not in r.method:
                r.method += TAKEN_OFF  # a deposit printed: whether the total before or after it is posted is AP's call
    _tax_named_by_page(cands, plain(layout.text()))
    for field, readings in cands.items():
        readings.sort(key=lambda r: -r.score) if field not in AMOUNT_FIELDS else None
        cands[field] = _dedupe(readings)
    return cands


# Terms printed as a sentence with no label (plain text): "15 days from date of invoice.", "15 jours suivant la date de
# la facture.", "Payment is due within 45 days", "Please remit payment upon receipt", "Zahlbar innerhalb 30 Tagen
# netto", "14 Tage netto", "2% Skonto", "Die Rechnung ist sofort fällig". Days in a sentence about something else
# ("discrepancies must be reported within 90 days of the invoice date") are not: the sentence starts with the days,
# or says the payment is due within them.
_TERMS_SENTENCE = re.compile(
    r"^\W*(?:net\s*)?\d{1,3}\s*(?:days?|jours?)\s*(?:nets?\s*)?(?:from|after|of|suivant|apres|a\s*compter\s*de)\s*(?:the\s*|la\s*)?"
    r"(?:date\s*(?:of|de)\s*(?:the\s*|la\s*)?)?(?:invoice|factur)"
    r"|(?:payable|due|payment\s*(?:is\s*)?(?:due|required)|pay\s*in\s*full|paiement)\s*(?:in\s*full\s*)?(?:within|dans\s*les|sous)"
    r"\s*\d{1,3}\s*(?:days?|jours?)|payable\s*a\s*\d{1,3}\s*jours"
    r"|remit\s*payment\s*(?:up)?on\s*receipt"
    r"|zahlbar\s*(?:innerhalb|binnen)\s*(?:von\s*)?\d{1,3}\s*tag|(?<![a-z\d])\d{1,3}\s*tag(?:en|e)\s*(?:rein\s*)?netto"
    r"|\d(?:[.,]\d{1,2})?\s*%\s*skonto|skonto\w*\s*:?\s*\d(?:[.,]\d{1,2})?\s*%"
    r"|zahlbar\s*sofort|sofort\s*(?:zahlbar|fallig)|ist\s*sofort\s*fallig"
)  # fmt: skip

# Where a sentence ends: ".", "!", "?" or ";" (a full stop between two digits is a date's or an amount's).
_SENTENCE_END = re.compile(r"[!?;]|\.(?!\d)|(?<!\d)\.")

# A sentence about something other than paying the invoice: its days are a warranty's, a claim's, a return's, a
# holdback's, a statement's or an interest charge's (plain text).
_NOT_TERMS = re.compile(
    r"warrant|guarant|garanti|return|retour|claim|reclamation|holdback|retenue|completion|achevement|dispute|"
    r"discrepanc|restocking|statement|releve|valid|einwand|beanstand"
)

# Interest or late charges: a sentence that starts with the days and speaks of them counts the days to the charge.
_LATE_WORDS = re.compile(r"interest|interet|zinsen|per\s*month|par\s*mois|overdue|late|retard|souffrance|past\s*due")


def _sentences(text: str) -> list[tuple[int, int]]:
    starts = [0] + [m.end() for m in _SENTENCE_END.finditer(text)]
    return list(zip(starts, [*starts[1:], len(text)], strict=True))


def _terms_sentences(lines: list[Line]) -> list[Reading]:
    """The terms in the first sentence on the invoice that states them, read for a person to check: one reading, or
    none when two sentences state different terms. A discount stated in the sentence before or after belongs to
    them."""
    from ..normalize import normalize_value

    found: list[Reading] = []
    for line in lines:
        text, at = plain_with_offsets(line.text)
        spans = _sentences(text)
        for k, (a, b) in enumerate(spans):
            sentence = text[a:b].strip()
            m = _TERMS_SENTENCE.search(sentence)
            if not m or _NOT_TERMS.search(sentence):
                continue  # "90 days from date of invoice: warranty", "Holdback of 10% is payable within 60 days..."
            if m.start() == 0 and sentence[:1].isdigit() and _LATE_WORDS.search(sentence):
                continue  # "30 days from date of invoice, interest at 2% per month is charged"
            near = [text[x:y].strip() for x, y in spans[max(k - 1, 0) : k] + spans[k + 1 : k + 2]]
            discount = next((x for x in near if _DISCOUNT_SENTENCE.match(x)), "")
            value = _terms(f"{sentence.rstrip('.')}. {discount}" if discount else sentence)
            if value:
                words = _span_words(line, at[a], at[b])
                method = "sentence" + UNMEASURED + (PART_OF_TERMS if _DEPOSIT.search(sentence) else "")
                found.append(Reading("payment_terms", value, line.text[at[a] : at[b]].strip(), _boxes(words),
                                     0.6 * _min_conf(words), method))  # fmt: skip
    values = {normalize_value("payment_terms", r.value) for r in found}
    return found[:1] if len(values) == 1 else []


# A PO label inside a line, after other words (plain text): "York University Purchase Order Number: PO12345" (OCR may
# glue it: "UniversityPurchaseOrderNumber:PO12345"), "Rechnung gemäß Bestellung Nr. 2013-471331 vom 01.03.2013". With
# its "number", so "PO Box" and a "purchase order" in a sentence are not.
_PO_MENTION = re.compile(
    r"(?:purchase\s*order|(?<![a-z])p\.?\s?o\.?)\s*(?:number|no(?![a-z])\.?|#|n\s?°)\s*[:#]?"
    r"|(?<![a-z])bestell(?:ung)?\s*-?\s*(?:nr(?![a-z])\.?|nummer)\s*[:#]?"
    r"|(?<![a-z])bon\s*de\s*commande\s*(?:n\s?[o°]|numero)(?![a-z])\.?\s*[:#]?"
)

# "Your reference" / "Votre référence": European invoices give the customer's order number so, but it may be another
# reference of the customer's. Read only when no PO label gives one.
_YOUR_REFERENCE = re.compile(r"(?:your|votre)\s*ref(?:erence)?(?![a-z])\.?")

_ASKS_FOR_PO = re.compile(
    r"(?:quote|reference|indicate|include|mention|show|rappeler|indiquer)\s*(?:the\s*|your\s*|our\s*|this\s*|le\s*|votre\s*)?$"
)

_PHONE_SHAPE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}")


def _a_plain_po(r: Reading) -> bool:
    """A number read for the PO without a PO label: one token, not a phone number ("call 416-555-1212")."""
    value = str(r.value)
    return not re.search(r"\s", value) and not _PHONE_SHAPE.search(value) and not _looks_like_phone_or_postal(value)


def _po_mentions(page_lines: list[Line]) -> list[Reading]:
    """The PO named mid-line on the first page, for a person to check; none when two different ones are (the order
    of each line item: "* Bestellung Nr. ORDER84359 Zeile 1")."""
    from ..normalize import normalize_value

    found = []
    for line in page_lines:
        text, at = plain_with_offsets(line.text)
        for m in _PO_MENTION.finditer(text):
            if m.start() == 0 or _ASKS_FOR_PO.search(text[: m.start()]):
                continue  # a label starting its line (read as one), or "Please quote PO number ... with payment"
            got = _value_reading("po_number", line, at[m.end()], 0.55, "mention" + UNMEASURED)
            if got and _a_plain_po(got):
                found.append(got)
    return found[:1] if len({normalize_value("po_number", r.value) for r in found}) == 1 else []


def _po_as_your_reference(by_page: dict[int, list[Line]], under: set[int]) -> list[Reading]:
    """The customer's reference read as the PO, for a person to check; none when two different ones are printed."""
    from ..normalize import normalize_value

    found = []
    for page, page_lines in by_page.items():
        for line in page_lines:
            text, at = plain_with_offsets(line.text)
            m = _YOUR_REFERENCE.match(text)
            if m:
                hit = _LabelHit("po_number", line, 0.6, at[m.end()], text[: m.end()])
                for r in _labelled([hit], page_lines, page in under):
                    if _a_plain_po(r):
                        r.method += UNMEASURED
                        found.append(r)
    return found[:1] if len({normalize_value("po_number", r.value) for r in found}) == 1 else []


# A due date stated in a sentence (plain text): "Bitte überweisen Sie bis zum 09.06.2014", "Zahlbar innerhalb 30 Tagen
# netto bis 04.04.2013" (the net amount's date: not the discount's "3% Skonto ... bis 15.03.2013"), "Payment is due by
# October 15, 2026", "Payable avant le 15 octobre 2026". The date follows the words, in the same sentence.
_DUE_SENTENCE = re.compile(
    r"(?:netto|ohne\s*(?:abzug|abschlag))\s*bis\s*(?:zum\s*)?"
    r"|(?:uberweisen|zahlen|begleichen)\s*sie\s*(?:[a-z]+\s*){0,3}?bis\s*(?:zum\s*)?"
    r"|(?:payment|amount|balance|total)\s*(?:is\s*)?due\s*(?:on|by)\s*|please\s*pay\s*(?:on\s*or\s*)?(?:by|before)\s*"
    r"|(?:payable|a\s*payer)\s*(?:avant|au\s*plus\s*tard)\s*(?:le\s*)?"
)  # fmt: skip

# A date in such a sentence that is not the one to pay by: a balance as of a day, a discount's deadline, a delivery,
# a later charge.
_NOT_DUE = re.compile(
    r"as\s*of|on\s*account|discount|escompte|rabatt|deliver|livraison|interest|interet|after|apres|through|"
    r"until|valid|service|quote"
)


def _due_sentences(lines: list[Line]) -> list[Reading]:
    """The due date a sentence on the invoice states, for a person to check; none when two sentences state different
    ones."""
    found = []
    for line in lines:
        text, at = plain_with_offsets(line.text)
        for a, b in _sentences(text):
            sentence = text[a:b]
            if _NOT_DUE.search(sentence):
                continue
            for m in _DUE_SENTENCE.finditer(sentence):
                dates = parse_dates(line.text[at[a + m.end()] : at[b]], prefer_day_first=_DAY_FIRST.get())
                if not dates or dates[0][1] > 2:
                    continue  # the date must follow the words, before the sentence ends ("due by the 15th. Next...")
                got = _value_reading("due_date", line, at[a + m.end()], 0.6, "sentence" + UNMEASURED)
                if got:
                    found.append(got)
    return found[:1] if len({r.value for r in found}) == 1 else []


def _negative_totals(cands: dict[str, list[Reading]]) -> bool:
    """The subtotal and total read are printed negative (a credit note's)."""
    tops = [r.value for f in ("subtotal", "grand_total") for r in cands.get(f, [])[:2] if r.value]
    return bool(tops) and all(v < 0 for v in tops)


def _settle_pst_or_qst(cands: dict[str, list[Reading]], text: str) -> None:
    """An amount under a "PST/QST" (or "TVP/TVQ") label is the QST when the supplier prints a QST registration number
    and no PST one, and the PST the other way round. Otherwise both stay proposed, marked ``EITHER_TAX``: the totals
    solver picks one by its rate, and it is never verified."""
    either = {f: [r for r in cands.get(f, []) if EITHER_TAX in r.method] for f in ("pst_amount", "qst_amount")}
    if not either["pst_amount"] and not either["qst_amount"]:
        return
    qst = bool(cands.get("qst_registration_number")) or bool(_QST_REG.search(text))
    pst = bool(cands.get("pst_registration_number")) or bool(_PST_REG.search(text))
    if qst == pst:
        return
    keep, drop = ("qst_amount", "pst_amount") if qst else ("pst_amount", "qst_amount")
    cands[drop] = [r for r in cands.get(drop, []) if EITHER_TAX not in r.method]
    for r in either[keep]:
        r.method = r.method.replace(EITHER_TAX, "")


def _dedupe(readings: list[Reading]) -> list[Reading]:
    """One reading per distinct value (the best one), keeping order."""
    from ..normalize import normalize_value

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
