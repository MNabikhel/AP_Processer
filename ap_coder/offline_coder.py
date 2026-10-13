"""Coding an invoice with no AI model: the capture reader's header and lines, coded from memory.

When neither LM Studio nor Azure OpenAI is available, an invoice still gets a full proposal: every header
field and line as the local reader found them (capture, with its own confidence), each line's GL account
from what AP approved before for this vendor and these words (``suggest.suggest_gl``), the vendor master's
default account, and fixed coding rules (applied later by the pipeline). Nothing is guessed: a line with
no history, or only a weak match, is left UNASSIGNED (the review screen suggests accounts for it) and a field
the reader could not find stays empty, so review asks for it.
"""

from __future__ import annotations

import datetime as dt
import functools
import logging
import re
from pathlib import Path
from typing import Any

from .capture import analyze, build_layout
from .capture.bridge import vendor_record
from .capture.reader import _US_ADDRESS
from .capture.types import LIKELY, VERIFIED, CaptureResult, DocLayout
from .capture.workflow import local_evidence, supplier_for
from .extraction import ExtractionResult
from .inference import CodingResult
from .memory import vendor_key
from .reference_data import UNASSIGNED, ReferenceData
from .schema import PROVINCE_VALUES, InvoiceCoding, LineItem, TaxLine
from .suggest import suggest_gl
from .tax import OUTSIDE_CANADA, TaxRateTable

log = logging.getLogger(__name__)

MODEL_NAME = "local reader (no AI model)"
NO_HISTORY = "No earlier approval to learn from: pick the account."
WEAK_MATCH = "A weak match, for a person to confirm"  # a line ``assign_weak`` coded: never approved without a person
_TAXES = (("gst_amount", "GST"), ("hst_amount", "HST"), ("pst_amount", "PST"), ("qst_amount", "QST"))
# The rates a Canadian invoice charges (a rate worked out from rounded amounts snaps to the nearest one), and
# the province a rate names when only one has it (HST 13% Ontario, 14% Nova Scotia from April 2025; PST 6% SK).
_OFFICIAL = {"GST": (0.05,), "HST": (0.13, 0.14, 0.15), "QST": (0.09975,), "PST": (0.06, 0.07, 0.08)}
_RATE_PROVINCE = {("HST", 0.13): "ON", ("HST", 0.14): "NS", ("QST", 0.09975): "QC", ("PST", 0.06): "SK"}
_PROVINCE_AT = re.compile(r"(?:\(|\b)(ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)(?:\)|\b)\s*,?\s*[A-Z]\d[A-Z]\s?\d[A-Z]\d")
# Labels, not any line with the word: "FACTURE" (a French invoice's title), "Customer service" or
# "Date de livraison" in the letterhead are not the customer's address.
_SHIP_LABEL = re.compile(
    r"(?i)\b(?:ship|deliver)(?:ped|ed)?\s*to\b|\b(?:livr|exp[ée]di)(?:é|e|er|ée)\s*(?:à|a)\b"
    r"|\b(?:adresse|lieu)\s+de\s+livraison\b"
)
_CUSTOMER_LABEL = re.compile(
    r"(?i)\b(?:bill|ship|sold|invoice|deliver)(?:ed)?\s*to\b|^\W*(?:client|customer)\s*(?::|$)"
    r"|\b(?:factur(?:é|e|er|ée)|vendue?)\s*(?:à|a)\b|\badresse\s+de\s+facturation\b"
)


def read_invoice(path: str | Path, store: Any = None, layout: DocLayout | None = None,
                 page_text: list[str] | None = None, ai_values: dict[str, Any] | None = None,
                 ) -> tuple[CaptureResult, str, dict[str, Any] | None]:  # fmt: skip
    """(capture, supplier key, supplier profile): a first read, then again with the supplier's template
    and vendor-master record once the first read says who the supplier is. ``layout``: the page, already
    read (a scan is OCR'd once). ``page_text``: the page reader's transcription of each page, when it has
    read them (one more independent reader). ``ai_values``: the chat model's own reading of the header from the page
    images (another one). What AP approved on this computer calibrates the confidence."""
    path = Path(path)
    today = dt.date.today()
    layout = layout or build_layout(path)
    local = local_evidence(store)
    capture = analyze(path, layout=layout, today=today, page_text=page_text, local_evidence=local,
                      ai_values=ai_values or None)  # fmt: skip
    first = _values(capture)
    key, profile = supplier_for(store, first)
    template = (profile or {}).get("template") or None
    vendor = vendor_record(store, first.get("vendor_name"))
    if template or vendor:
        capture = analyze(path, layout=layout, template=template, vendor=vendor, today=today, page_text=page_text,
                          local_evidence=local, ai_values=ai_values or None)  # fmt: skip
    return capture, key, profile


def _values(capture: CaptureResult) -> dict[str, Any]:
    return {f: r.value for f, r in capture.fields.items() if r.value not in (None, "")}


def _amount(values: dict[str, Any], field: str) -> float:
    try:
        return round(float(values.get(field) or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


@functools.lru_cache(maxsize=1)
def _rate_table() -> Any:
    try:
        return TaxRateTable.load()
    except Exception:  # no rate table: rates are worked out from the amounts
        return None


def provinces(text: str) -> tuple[str, str]:
    """(supplier province, ship-to province) from the addresses on the page: the first address is the
    supplier's (its letterhead), the first after a "Bill to" / "Client" label the customer's. A US address
    is OUTSIDE_CANADA. Each labelled address goes to the labels in the order they were read, so "Bill to" and
    "Ship to" side by side (labels on one row, then each column's address) are told apart."""
    supplier = bill_to = ship_to = ""
    after_label = False
    pending: list[str] = []  # labels read whose address has not come yet
    for line in text.splitlines():
        if _SHIP_LABEL.search(line):
            after_label = True
            pending.append("ship")
        elif _CUSTOMER_LABEL.search(line):
            after_label = True
            pending.append("bill")
        m = _PROVINCE_AT.search(line)
        where = m.group(1) if m else (OUTSIDE_CANADA if _US_ADDRESS.search(line) else "")
        if not where:
            continue
        if not supplier and not after_label:
            supplier = where
        elif pending:
            if pending.pop(0) == "ship":
                ship_to = ship_to or where  # the place of supply: where the goods or services go
            else:
                bill_to = bill_to or where
    return supplier, ship_to or bill_to or supplier


def default_currency(supplier_province: str) -> str:
    """The currency of an invoice that prints none: US dollars from a supplier with a US address, Canadian dollars
    otherwise. Never silent: the review report warns that the currency was not read (``bridge.review_issues``)."""
    return "USD" if supplier_province == OUTSIDE_CANADA else "CAD"


def _tax_lines(values: dict[str, Any], subtotal: float, province: str, on: dt.date | None = None) -> list[TaxLine]:
    out = []
    table = _rate_table()
    for field, tax_type in _TAXES:
        amount = _amount(values, field)
        if not amount:
            continue
        official = table.rate_for(tax_type, province, on) if table and province in PROVINCE_VALUES else None
        charged = abs(amount / subtotal) if subtotal else 0.0
        # Less tax than the province's rate on the whole subtotal: charged on part of it (delivery exempt from
        # Manitoba RST), the province's rate on its base. More than that (14% Nova Scotia HST with the province
        # read as Ontario) is no partial base: the rate the amounts show is kept, with the province it names,
        # and the tax check flags the place of supply that differs (TAX_PROVINCE_DIFFERS).
        partial = bool(official) and (not subtotal or abs(amount) < official * abs(subtotal))
        if official and tax_type != "GST" and abs(charged - official) > 0.002 and partial:
            base = round(amount / official, 2)
            out.append(
                TaxLine(tax_type=tax_type, province=province, rate=official, taxable_amount=base, tax_amount=amount)
            )
            continue
        rate = abs(amount / subtotal) if subtotal else 0.0
        rate = (
            min(_OFFICIAL[tax_type], key=lambda r: abs(r - rate))
            if any(abs(r - rate) <= 0.002 for r in _OFFICIAL[tax_type])
            else round(min(rate, 1.0), 5)
        )
        where = "" if tax_type == "GST" else (_RATE_PROVINCE.get((tax_type, rate)) or province)
        if where not in PROVINCE_VALUES or where == OUTSIDE_CANADA:
            where = ""
        out.append(TaxLine(tax_type=tax_type, province=where, rate=rate, taxable_amount=subtotal, tax_amount=amount))
    total_tax = _amount(values, "tax_total")
    if not out and total_tax and subtotal:  # a sales tax that is none of these (a US state's): its total only
        rate = round(min(abs(total_tax / subtotal), 1.0), 5)
        out.append(TaxLine(tax_type="OTHER", province="", rate=rate, taxable_amount=subtotal, tax_amount=total_tax))
    return out


# The supplier's banking details: words only banking details use ("Transit", "Institution", "IBAN", "SWIFT", "bank
# account", "direct deposit", "virement"...), on a line with an account's digits (amounts don't count: "Deposit
# 500.00" is a line item). "Remit by EFT within 30 days" has none; "100 Bank Street" is an address.
_BANKING = re.compile(
    r"(?i)\b(?:transit|institution|iban|swift|bic|routing|aba|bank\s+(?:account|acct)|banking|banque|bancaires?|"
    r"direct\s+deposit|d[ée]p[ôo]t\s+direct|virement|wire\s+transfer|eft)\b"
)
_BANK_WORDS = re.compile(
    r"(?i)\b(?:transit|institution|inst\b|iban|swift|bic|routing|aba|bank(?!\s+(?:st|street|ave|avenue|rd|road|"
    r"blvd)\b)|banque|account|acct|compte|virement|eft|wire|direct)"
)
_MONEY = re.compile(r"\d[\d,. ]*[.,]\d{2}(?!\d)")


def bank_details(text: str) -> str:
    """The bank account the invoice asks to be paid into, as printed ("Transit 12345 Institution 004 Account
    1234567", "IBAN GB29 NWBK 6016 1331 9268 19"), or "". Read so that a change of account from one invoice to the
    next is found (``vendors.VENDOR_BANK_CHANGED``, a payment-fraud signal that always needs a person): lines that
    name banking details with at least 5 digits on them, and the account lines under a "Banking details" heading."""

    def digits(s: str) -> int:
        return sum(c.isdigit() for c in _MONEY.sub(" ", s))

    found: list[str] = []
    lines = [ln.strip() for ln in (text or "").splitlines()]
    for i, line in enumerate(lines):
        if not _BANKING.search(line):
            continue
        heading = digits(line) < 5 and (line.endswith(":") or len(line.split()) <= 4)  # "Banking details:"
        for row in [line] if not heading else lines[i + 1 : i + 4]:  # under a heading: the account lines
            m = _BANK_WORDS.search(row)
            if m and digits(row[m.start() :]) >= 5 and row[m.start() :] not in found:
                found.append(row[m.start() :])
    return "; ".join(found)[:200]


def _unlisted_charge(text: str, amount: float) -> str:
    """The label printed beside an amount outside the line table ("Delivery & handling: $95.00")."""
    figure = f"{abs(amount):,.2f}"
    for line in text.splitlines():
        if figure in line:
            label = line.split(figure)[0].strip(" :$-–\t")
            if 2 < len(label) < 60 and not re.search(r"(?i)sub\s*-?total|total|tax|gst|hst|pst|qst|tps|tvq", label):
                return label
    return "Charges not in the line table"


def not_sure(top: Any) -> str:
    """Why a line with a suggestion was left without an account."""
    return f"Not sure enough to code it (maybe {top.gl_code}: {'; '.join(top.reasons[:2])}): pick the account."


def code_from_capture(capture: CaptureResult, reference: ReferenceData, feedback: list[dict[str, Any]],
                      store: Any = None, text: str = "", assign_weak: bool = False) -> CodingResult:  # fmt: skip
    """``text``: the page text (labels a charge printed outside the line table). A line gets an account only when
    the evidence is strong enough (``Suggestion.auto``); the others stay UNASSIGNED and the review screen suggests
    accounts for them. ``assign_weak``: code those with the best suggestion anyway (the local chat model is asked
    only about lines with no suggestion at all: an account name match beat a small model on the samples)."""
    values = _values(capture)
    vendor = str(values.get("vendor_name") or "")
    readings = [li for li in capture.line_items if li.amount is not None]
    # No subtotal found: the lines read are the subtotal (not a charge that cancels them all).
    subtotal = _amount(values, "subtotal") if "subtotal" in values or not readings else (
        round(sum(float(li.amount) for li in readings), 2)
    )  # fmt: skip
    # Freight read beside the subtotal is part of the coded subtotal (lines + charges, before tax) and taxed.
    charges = capture.fields.get("other_charges")
    freight = _amount(values, "other_charges") if charges is not None and charges.status in (VERIFIED, LIKELY) else 0.0
    supplier_province, ship_to_province = provinces(text)
    invoice_on = _iso_or_empty(values.get("invoice_date"))
    tax_lines = _tax_lines(
        values, round(subtotal + freight, 2), ship_to_province,
        dt.date.fromisoformat(invoice_on) if invoice_on else None,
    )  # fmt: skip
    taxes = [t.tax_type for t in tax_lines]
    default_gl = ""
    if store is not None and vendor:
        try:
            default_gl = (store.get_vendor(vendor_key(vendor)) or {}).get("default_gl") or ""
        except Exception:  # an older store without the vendor table
            default_gl = ""

    if not readings:  # no table the reader could split: one line for the whole subtotal
        readings_data = [("Invoice " + str(values.get("invoice_number") or ""), 1.0, subtotal, subtotal)]
    else:
        readings_data = [
            (li.description or f"Line {i}", li.quantity if li.quantity is not None else 1.0,
             li.unit_price if li.unit_price is not None
             else float(li.amount) / li.quantity if li.quantity else float(li.amount), float(li.amount))
            for i, li in enumerate(readings, 1)
        ]  # fmt: skip
    # Lines that fall short of the subtotal (plus freight read beside it): a charge printed outside the table.
    short = round(subtotal + freight - sum(r[3] for r in readings_data), 2)
    if (readings or freight) and abs(short) >= 0.01:
        readings_data.append((_unlisted_charge(text, short), 1.0, short, short))
    lines = []
    for number, (description, quantity, unit_price, amount) in enumerate(readings_data, 1):
        top = next(iter(suggest_gl(description, vendor, feedback, reference, limit=1, default_gl=default_gl)), None)
        # Only a strong match codes the line; a weaker one is left for AP, suggested on the review screen.
        best = top if top is not None and (top.auto or assign_weak) else None
        why = "; ".join(best.reasons) if best else not_sure(top) if top is not None else NO_HISTORY
        if best is not None and not best.auto:
            why = f"{WEAK_MATCH}: {why}" if why else WEAK_MATCH
        lines.append(
            LineItem(
                line_number=number,
                description=description.strip() or f"Line {number}",
                quantity=quantity,
                unit_price=round(unit_price, 4),
                amount=round(amount, 2),
                predicted_gl_code=best.gl_code if best else UNASSIGNED,
                predicted_cost_center=(best.cost_center if best else "")
                or (UNASSIGNED if reference.cost_centers is not None else ""),
                taxes_applied=taxes,
                reasoning_justification=why,
            )
        )

    found = [r.confidence for f, r in capture.fields.items() if r.value not in (None, "")]
    gl_known = sum(1 for li in lines if li.predicted_gl_code != UNASSIGNED) / len(lines)
    confidence = round(min(sum(found) / len(found) if found else 0.0, 1.0) * (0.5 + 0.5 * gl_known), 3)
    invoice_date = str(values.get("invoice_date") or "")
    try:
        dt.date.fromisoformat(invoice_date)
    except ValueError:
        invoice_date = dt.date.today().isoformat()  # the day it arrived; capture already flags the date as missing
    coding = InvoiceCoding(
        vendor_name=vendor,
        invoice_number=str(values.get("invoice_number") or ""),
        invoice_date=invoice_date,
        po_number=str(values.get("po_number") or ""),
        payment_terms=str(values.get("payment_terms") or ""),
        due_date=_iso_or_empty(values.get("due_date")),
        currency=str(values.get("currency") or default_currency(supplier_province)),
        supplier_province=supplier_province,
        ship_to_province=ship_to_province,
        gst_hst_registration_number=str(values.get("gst_hst_registration_number") or ""),
        qst_registration_number=str(values.get("qst_registration_number") or ""),
        remit_bank_account=bank_details(text),
        subtotal=round(subtotal + freight, 2),
        tax_lines=tax_lines,
        tax_total=_amount(values, "tax_total") or round(sum(t.tax_amount for t in tax_lines), 2),
        grand_total=_amount(values, "grand_total"),
        confidence_score=confidence,
        line_items=lines,
    )
    return CodingResult(coding=coding, raw_response="", model=MODEL_NAME, attempts=0)


def _iso_or_empty(value: Any) -> str:
    try:
        return dt.date.fromisoformat(str(value)).isoformat() if value else ""
    except ValueError:
        return ""


def text_layout(text: str) -> DocLayout:
    """A text invoice (.md, .txt: an invoice already typed out, tables in markdown or HTML) as a layout the rule
    reader reads, like a PDF's text layer: its characters are exact. Pages are split where the text marks a page
    break."""
    from .capture.transcript import layout_from_transcript
    from .figures import PAGE_BREAK

    pages = [page for page in (text or "").replace("\f", PAGE_BREAK).split(PAGE_BREAK) if page.strip()] or [""]
    layout = layout_from_transcript(pages)
    return DocLayout(layout.pages, "text")


def local_extraction(path: str | Path, layout: DocLayout | None = None) -> ExtractionResult:
    """The page text as the local reader saw it (text layer, or OCR for scans), for search and the review page."""
    path = Path(path)
    layout = layout or build_layout(path)
    words = list(layout.words())
    ocr_conf = [w.conf for w in words if w.source == "ocr"]
    content = "\n\n<!-- PageBreak -->\n\n".join("\n".join(line.text for line in page.lines) for page in layout.pages)
    return ExtractionResult(
        source=str(path),
        model_id=f"local-{layout.source}",
        content=content,
        page_count=len(layout.pages),
        mean_word_confidence=(sum(ocr_conf) / len(ocr_conf)) if ocr_conf else None,
        low_confidence_word_count=sum(1 for c in ocr_conf if c < 0.8),
    )
