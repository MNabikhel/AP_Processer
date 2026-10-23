"""Deterministic post-inference controls.

The LLM's self-reported confidence is not enough on its own to decide
straight-through processing. These checks reconcile the arithmetic, verify
every code against the reference data, run the Canadian sales-tax checks,
compare against reviewer history and cross-check prebuilt-invoice fields,
then derive an adjusted confidence and a human-review flag that drives the
dashboard review queue.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..ai.extraction import ExtractionResult
from ..core.reference_data import UNASSIGNED, ReferenceData
from ..core.schema import InvoiceCoding
from .tax import build_gl_distribution, check_taxes
from .vendors import norm_tax_number  # letters and digits, lower case

ERROR, WARNING, INFO = "error", "warning", "info"  # info: worth knowing, no penalty
_ERROR_PENALTY = 0.6
_WARNING_PENALTY = 0.9
# What the setup or a choice still to make holds back (JD Edwards not set up for it, the bank account that pays), not
# a doubt about the reading or the coding: a blocker, never lower confidence (D-86).
SETUP_PREFIXES = ("JDE_", "PAY_FROM_")
# Confidence is two numbers (D-89): the Reading (how sure the header is: the coding's ``confidence_score``) and the
# Coding (how sure the lines' accounts, cost centers and tax codes are: ``coding_confidence``); the invoice's is the
# lower. A line to code, or coded to an account or cost center not in the lists, already counts 0 in the Coding: its
# finding keeps Approve closed but takes nothing more off (as setup findings, D-86).
COUNTED_IN_CODING = frozenset({"GL_UNASSIGNED", "CC_UNASSIGNED", "GL_UNKNOWN", "CC_UNKNOWN", "GL_IS_TAX_ACCOUNT",
                               "PST_EXEMPTION_TO_CONFIRM"})  # fmt: skip
# Findings about the business, not about how the invoice was read or coded (the vendor master, a purchase order, a
# possible duplicate, an unusual amount): like setup findings they keep Approve closed (an error), go to a person and
# are said, but take nothing off the Reading or the Coding: every value can be read right and still not be in the
# vendor master (D-89).
BUSINESS_PREFIXES = ("PO_", "DUPLICATE_")
BUSINESS_CODES = frozenset({"VENDOR_NOT_IN_MASTER", "VENDOR_ON_HOLD", "VENDOR_ADDED_HERE", "VENDOR_BANK_CHANGED",
                            "POSSIBLE_DUPLICATE_AMOUNT", "AMOUNT_UNUSUAL"})  # fmt: skip
# Findings that doubt the lines' coding rather than the reading: they take their share off the Coding.
ABOUT_CODING = frozenset({"HISTORY_CONFLICT", "TAX_EXEMPTION_LEARNED", "EXEMPT_LINE_TAXED", "TAX_NOT_ALLOCATED",
                          "LINE_TAX_NOT_CHARGED", "TAX_BASE_MISMATCH", "MEALS_TAX_LINES"})  # fmt: skip
# A line's account the chat model chose or a weak match (``offline_coder.GUESSED``), and a tax code worked out from
# several possible sets of lines or an exemption to confirm: about one chance in two (the coding bench measures it).
GUESS_SURE = 0.5
_LOW_OCR_CONFIDENCE = 0.90


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    line_number: int | None = None


@dataclass
class ValidationReport:
    source: str
    model_confidence: float
    adjusted_confidence: float  # the lower of the two below: what the queue sorts on and the threshold is held to
    review_threshold: float
    requires_review: bool
    issues: list[Issue] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    reading_confidence: float = 0.0  # how sure the header is (D-89)
    coding_confidence: float = 0.0  # how sure the lines' accounts, cost centers and tax codes are (D-89)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_CENT_TOLERANCE = 0.02


def _close(a: float, b: float, tolerance: float = _CENT_TOLERANCE) -> bool:
    return abs(a - b) <= tolerance + 1e-9


def _rounding_tolerance(line_count: int) -> float:
    """Totals may differ from the line sum by up to a cent of rounding per line."""
    return max(_CENT_TOLERANCE, 0.01 * line_count)


def validate_coding(
    coding: InvoiceCoding,
    reference: ReferenceData,
    extraction: ExtractionResult | None = None,
    review_threshold: float = 0.85,
    history: list[dict[str, Any]] | None = None,
    duplicate_of: list[int] | None = None,
    vendor_findings: list[tuple[str, str, str]] | None = None,
    po_findings: list[tuple[str, str, str, int | None]] | None = None,
    erp_findings: list[tuple[str, str, str, int | None]] | None = None,
) -> ValidationReport:
    """``history`` is ``memory.compare_with_history`` output; ``duplicate_of`` lists stored invoice ids
    with the same vendor and invoice number."""
    issues: list[Issue] = []

    def add(severity: str, code: str, message: str, line: int | None = None) -> None:
        issues.append(Issue(severity, code, message, line))

    if duplicate_of:
        ids = ", ".join(f"#{i}" for i in duplicate_of)
        add(ERROR, "DUPLICATE_INVOICE", f"same vendor and invoice number as invoice {ids}: possible duplicate payment")
    for severity, code, message in vendor_findings or []:  # vendor master / fraud signals (vendors.py)
        add(severity, code, message)
    for severity, code, message, line in po_findings or []:  # purchase order match (po.py)
        add(severity, code, message, line)
    for severity, code, message, line in erp_findings or []:  # what the ERP's batch would refuse (erp/jde.py)
        add(severity, code, message, line)

    # --- Header -----------------------------------------------------------
    if not coding.vendor_name.strip():
        add(ERROR, "MISSING_VENDOR", "No vendor name: type it from the page")
    if not coding.invoice_number.strip():
        add(ERROR, "MISSING_INVOICE_NUMBER", "No invoice number: type it from the page")
    if not re.fullmatch(r"[A-Z]{3}", coding.currency):
        add(WARNING, "CURRENCY_FORMAT", f"The currency “{coding.currency}” is not a 3-letter code such as CAD or USD")
    if not coding.line_items:
        add(ERROR, "NO_LINE_ITEMS", "No line items were read: add a line for what the invoice charges")

    # --- Line items -------------------------------------------------------
    numbers = [li.line_number for li in coding.line_items]
    if numbers != list(range(1, len(numbers) + 1)):
        add(WARNING, "LINE_NUMBERING", f"The line numbers don't run 1, 2, 3…: {', '.join(map(str, numbers))}")

    tax_gls = reference.tax.tax_gl_codes()
    for li in coding.line_items:
        expected = li.quantity * li.unit_price
        # Unit prices are often printed rounded, so allow 0.5% on the extension.
        if not _close(li.amount, expected, max(_CENT_TOLERANCE, abs(expected) * 0.005)):
            add(
                WARNING,
                "LINE_MATH",
                f"quantity × unit price = {expected:.2f} but the amount is {li.amount:.2f}",
                li.line_number,
            )

        if li.predicted_gl_code == UNASSIGNED:
            add(WARNING, "GL_UNASSIGNED", "no GL account yet: pick one", li.line_number)
        elif reference.chart_of_accounts.get(li.predicted_gl_code) is None:
            add(
                ERROR,
                "GL_UNKNOWN",
                f"GL account {li.predicted_gl_code} is not in your GL accounts: pick one from the list",
                li.line_number,
            )
        elif li.predicted_gl_code in tax_gls:
            add(
                ERROR,
                "GL_IS_TAX_ACCOUNT",
                f"GL account {li.predicted_gl_code} is a sales-tax account: pick the expense account",
                li.line_number,
            )

        if reference.cost_centers is None:
            pass  # cost centers not configured
        elif li.predicted_cost_center == UNASSIGNED:
            add(WARNING, "CC_UNASSIGNED", "no cost center yet: pick one", li.line_number)
        elif reference.cost_centers.get(li.predicted_cost_center) is None:
            add(
                ERROR,
                "CC_UNKNOWN",
                f"cost center {li.predicted_cost_center} is not in your cost centers: pick one from the list",
                li.line_number,
            )

    # --- Totals reconciliation ---------------------------------------------
    line_sum = round(sum(li.amount for li in coding.line_items), 2)
    computed_total = round(coding.subtotal + coding.tax_total, 2)
    checks: dict[str, Any] = {
        "line_count": len(coding.line_items),
        "line_sum": line_sum,
        "subtotal": coding.subtotal,
        "subtotal_plus_tax": computed_total,
        "grand_total": coding.grand_total,
    }
    line_tolerance = _rounding_tolerance(len(coding.line_items))
    if coding.line_items and not _close(line_sum, coding.subtotal, line_tolerance):
        add(ERROR, "SUBTOTAL_MISMATCH", f"the lines add up to {line_sum:.2f} but the subtotal is {coding.subtotal:.2f}"
            + (TAX_INCLUDED_HINT if prices_include_tax(coding) else ""))  # fmt: skip
    if not _close(computed_total, coding.grand_total):
        add(
            ERROR,
            "TOTAL_MISMATCH",
            f"subtotal + tax = {computed_total:.2f} but the total is {coding.grand_total:.2f}",
        )
    # The GL posting (line amounts + tax lines) must equal the amount payable to the cent. The checks
    # above each allow a little rounding; this catches small differences that add up (e.g. a misread line).
    # Added up from the posting itself, which rounds each line to the cent: two lines of 10.005 post as
    # 10.00 + 10.00, so summing first (20.01) would pass an invoice whose export is a cent off.
    posting_total = round(sum(e["amount"] for e in build_gl_distribution(coding, reference.tax)), 2)
    checks["posting_total"] = posting_total
    if coding.line_items and abs(posting_total - coding.grand_total) > 0.005:
        add(
            ERROR,
            "POSTING_UNBALANCED",
            f"the GL posting adds up to {posting_total:.2f} but the invoice total is {coding.grand_total:.2f} "
            f"({off_text(posting_total, coding.grand_total)}): correct a line amount or a tax line",
        )
    # --- Canadian sales tax ------------------------------------------------------
    known = set(reference.chart_of_accounts.codes)
    for f in check_taxes(coding, reference.tax, known):
        add(f.severity, f.code, f.message, f.line_number)
    checks["tax_lines"] = [f"{t.tax_type} {t.province} {t.rate:g}".strip() for t in coding.tax_lines]

    # --- Reviewer history -------------------------------------------------------------
    if history:
        checks["history"] = history
        for h in history:
            if h["status"] == "conflict":
                add(
                    WARNING,
                    "HISTORY_CONFLICT",
                    f"reviewers coded similar lines from this vendor to GL {h['history_gl']} "
                    f"({h['decisions']} past decisions)",
                    h["line_number"],
                )
        checks["history_matches"] = sum(1 for h in history if h["status"] == "match")

    # --- Cross-checks against Document Intelligence ---------------------------
    if extraction is not None:
        _cross_check(coding, extraction, add, checks)

    model_conf = coding.confidence_score
    reading, coded = model_conf, coding_confidence(coding, reference)
    # Each kind of finding counts once: eight lines without an account are one thing to do, not eight. Setup and
    # business findings (D-86) and what the Coding already counts take nothing off (``doubtless``).
    for severity, code in {(i.severity, i.code) for i in issues if i.severity != INFO and not doubtless(i.code)}:
        penalty = _ERROR_PENALTY if severity == ERROR else _WARNING_PENALTY
        if code in ABOUT_CODING:
            coded *= penalty
        else:
            reading *= penalty
    reading, coded = round(reading, 4), round(coded, 4)
    adjusted = min(reading, coded)
    # A line to code, and a business finding (a person looks at it, whatever the numbers), keep it for a person.
    requires_review = (adjusted < review_threshold or any(i.severity == ERROR for i in issues)
                       or any(i.code in COUNTED_IN_CODING or (i.severity != INFO and business(i.code))
                              for i in issues))  # fmt: skip

    return ValidationReport(
        source=extraction.source if extraction else "",
        model_confidence=model_conf,
        adjusted_confidence=adjusted,
        review_threshold=review_threshold,
        requires_review=requires_review,
        reading_confidence=reading,
        coding_confidence=coded,
        issues=issues,
        checks=checks,
    )


def business(code: str) -> bool:
    """Whether a finding is about the business (the vendor master, a PO, a possible duplicate, an unusual amount)."""
    return code.startswith(BUSINESS_PREFIXES) or code in BUSINESS_CODES


def doubtless(code: str) -> bool:
    """Whether a finding takes nothing off the Reading or the Coding (D-86, D-89): a setup finding, one about the
    business (``BUSINESS_CODES``), or a line to code, already counted in the Coding (``COUNTED_IN_CODING``)."""
    return code.startswith(SETUP_PREFIXES) or business(code) or code in COUNTED_IN_CODING


def line_coding_confidence(line: Any, cost_centers: bool, taxed: bool, known: Any = None, cc_known: Any = None,
                           tax_gls: Any = ()) -> float:  # fmt: skip
    """How sure one line's coding is: its account (none, or one not in the GL accounts or a tax account: 0; a guess:
    ``GUESS_SURE``; else sure), its cost center where they are used (none or unknown: 0), and its tax code when the
    invoice charges a tax (worked out from several possible sets of lines, an exemption to confirm, or none:
    ``GUESS_SURE``), multiplied. ``known``/``cc_known``: the GL accounts and cost centers (a ``ReferenceTable``)."""
    from ..ai.offline_coder import guessed
    from .line_tax import CODE_NONE, exemption_to_confirm, tax_code_of, tax_guessed

    gl = str(line.predicted_gl_code or "")
    if gl in ("", UNASSIGNED) or (known is not None and known.get(gl) is None) or gl in tax_gls:
        return 0.0
    sure = GUESS_SURE if guessed(line.reasoning_justification) else 1.0
    if cost_centers:
        cc = str(line.predicted_cost_center or "")
        if cc in ("", UNASSIGNED) or (cc_known is not None and cc_known.get(cc) is None):
            return 0.0
    if taxed and (tax_code_of(line) == CODE_NONE or tax_guessed(line) or exemption_to_confirm(line)):
        sure *= GUESS_SURE
    return sure


def coding_confidence(coding: InvoiceCoding, reference: ReferenceData) -> float:
    """The Coding (D-89): how sure the lines' coding is, the mean of ``line_coding_confidence`` over the lines (no line:
    0). A line to code counts 0, so three lines of four coded is 75%, never less: the finding that asks for the
    account is a blocker, not a second penalty."""
    if not coding.line_items:
        return 0.0
    taxed = any(t.tax_amount for t in coding.tax_lines)
    tax_gls = reference.tax.tax_gl_codes()
    ccs = reference.cost_centers
    sure = [line_coding_confidence(li, ccs is not None, taxed, reference.chart_of_accounts, ccs, tax_gls)
            for li in coding.line_items]  # fmt: skip
    return round(sum(sure) / len(sure), 4)


def off_text(posted: float, total: float) -> str:
    """How the invoice total differs from what its lines post, in words: "the total is 10.00 more than the lines"
    (never "off by -10.00", whose sign reads either way, B-805)."""
    gap = round(total - posted, 2)
    return f"the total is {abs(gap):,.2f} {'more' if gap > 0 else 'less'} than the lines"


_DI_WORDS = {"InvoiceTotal": "total", "SubTotal": "subtotal", "TotalTax": "tax total"}


# Prices that include the tax (a receipt): the lines add up to the total, not the subtotal. Said, never converted
# (D-64): which tax is in which price is not printed.
TAX_INCLUDED_HINT = (
    ": they add up to the total, so the prices seem to include the tax (as on a receipt). Enter each line's amount "
    "before tax, or one line for the subtotal"
)


def prices_include_tax(coding: Any) -> bool:
    """Whether the lines add up to the invoice's total (tax included) rather than its subtotal, with tax charged."""
    lines = round(sum(li.amount for li in coding.line_items), 2)
    tax = round(sum(t.tax_amount for t in coding.tax_lines), 2) or coding.tax_total
    return (bool(coding.line_items) and abs(tax) > 0.004 and abs(lines - coding.grand_total) <= 0.011
            and abs(lines - coding.subtotal) > 0.011)  # fmt: skip


def _cross_check(coding: InvoiceCoding, extraction: ExtractionResult, add: Any, checks: dict[str, Any]) -> None:
    if extraction.mean_word_confidence is not None:
        checks["mean_ocr_word_confidence"] = round(extraction.mean_word_confidence, 4)
        if extraction.mean_word_confidence < _LOW_OCR_CONFIDENCE:
            add(
                WARNING,
                "LOW_OCR_CONFIDENCE",
                f"the scan is hard to read ({extraction.mean_word_confidence:.0%} sure of its words): check the values "
                "against the page",
            )

    fields = extraction.invoice_fields
    if not fields:
        return

    di_id = (fields.get("InvoiceId") or {}).get("value")
    if di_id and norm_tax_number(di_id) != norm_tax_number(coding.invoice_number):
        add(
            WARNING,
            "DI_INVOICE_ID_DIFFERS",
            f"Azure read the invoice number as “{di_id}”, AP Coder as “{coding.invoice_number}”",
        )

    di_date = (fields.get("InvoiceDate") or {}).get("value")
    if di_date and str(di_date)[:10] != coding.invoice_date:
        add(
            WARNING,
            "DI_DATE_DIFFERS",
            f"Azure read the invoice date as {str(di_date)[:10]}, AP Coder as {coding.invoice_date}",
        )

    for di_name, attr in (("InvoiceTotal", "grand_total"), ("SubTotal", "subtotal"), ("TotalTax", "tax_total")):
        value = (fields.get(di_name) or {}).get("value")
        amount = value.get("amount") if isinstance(value, dict) else value
        if isinstance(amount, (int, float)):
            ours = getattr(coding, attr)
            checks[f"di_{attr}"] = amount
            if not _close(ours, float(amount)):
                add(
                    WARNING,
                    "DI_AMOUNT_DIFFERS",
                    f"Azure read the {_DI_WORDS[di_name]} as {float(amount):,.2f}, AP Coder as {ours:,.2f}",
                )


def currency_not_found(assumed: str | None = None) -> Issue:
    """The invoice prints no currency a reader of the page could read (no code such as CAD or USD, no € or £):
    the coding's currency is assumed (CAD, or USD from a supplier with a US address), so AP checks it. Raised
    from what capture read (``capture.bridge.review_issues``). Assumed CAD is how most Canadian invoices are
    printed, so that is worth knowing only; any other assumed currency is a warning."""
    if (assumed or "CAD").upper() == "CAD":
        return Issue(INFO, "CURRENCY_NOT_FOUND", "no currency printed: taken as CAD")
    return Issue(WARNING, "CURRENCY_NOT_FOUND", f"the currency isn't printed clearly: taken as {assumed}, check it")
