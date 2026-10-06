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

from .extraction import ExtractionResult
from .reference_data import UNASSIGNED, ReferenceData
from .schema import InvoiceCoding
from .tax import check_taxes

ERROR, WARNING = "error", "warning"
_ERROR_PENALTY = 0.6
_WARNING_PENALTY = 0.9
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
    adjusted_confidence: float
    review_threshold: float
    requires_review: bool
    issues: list[Issue] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

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


def _norm_id(value: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(value or "").lower())


def validate_coding(
    coding: InvoiceCoding,
    reference: ReferenceData,
    extraction: ExtractionResult | None = None,
    review_threshold: float = 0.85,
    history: list[dict[str, Any]] | None = None,
    duplicate_of: list[int] | None = None,
) -> ValidationReport:
    """``history`` is ``memory.compare_with_history`` output; ``duplicate_of`` lists stored invoice ids
    with the same vendor and invoice number."""
    issues: list[Issue] = []

    def add(severity: str, code: str, message: str, line: int | None = None) -> None:
        issues.append(Issue(severity, code, message, line))

    if duplicate_of:
        ids = ", ".join(f"#{i}" for i in duplicate_of)
        add(ERROR, "DUPLICATE_INVOICE", f"same vendor and invoice number as invoice {ids}: possible duplicate payment")

    # --- Header -----------------------------------------------------------
    if not coding.vendor_name.strip():
        add(ERROR, "MISSING_VENDOR", "vendor_name is empty")
    if not coding.invoice_number.strip():
        add(ERROR, "MISSING_INVOICE_NUMBER", "invoice_number is empty")
    if not re.fullmatch(r"[A-Z]{3}", coding.currency):
        add(WARNING, "CURRENCY_FORMAT", f"currency {coding.currency!r} is not an ISO 4217 code")
    if not coding.line_items:
        add(ERROR, "NO_LINE_ITEMS", "no line items were extracted")

    # --- Line items -------------------------------------------------------
    numbers = [li.line_number for li in coding.line_items]
    if numbers != list(range(1, len(numbers) + 1)):
        add(WARNING, "LINE_NUMBERING", f"line numbers are not sequential from 1: {numbers}")

    tax_gls = reference.tax.tax_gl_codes()
    for li in coding.line_items:
        expected = li.quantity * li.unit_price
        # Unit prices are often printed rounded, so allow 0.5% on the extension.
        if not _close(li.amount, expected, max(_CENT_TOLERANCE, abs(expected) * 0.005)):
            add(
                WARNING,
                "LINE_MATH",
                f"quantity x unit_price = {expected:.2f} but amount = {li.amount:.2f}",
                li.line_number,
            )

        if li.predicted_gl_code == UNASSIGNED:
            add(WARNING, "GL_UNASSIGNED", "model could not determine a GL account", li.line_number)
        elif reference.chart_of_accounts.get(li.predicted_gl_code) is None:
            add(ERROR, "GL_UNKNOWN", f"GL code {li.predicted_gl_code!r} is not in the GL accounts", li.line_number)
        elif li.predicted_gl_code in tax_gls:
            add(ERROR, "GL_IS_TAX_ACCOUNT", f"GL {li.predicted_gl_code} is a sales-tax account", li.line_number)

        if reference.cost_centers is None:
            pass  # cost centers not configured
        elif li.predicted_cost_center == UNASSIGNED:
            add(WARNING, "CC_UNASSIGNED", "model could not determine a cost center", li.line_number)
        elif reference.cost_centers.get(li.predicted_cost_center) is None:
            add(
                ERROR,
                "CC_UNKNOWN",
                f"cost center {li.predicted_cost_center!r} is not in the Cost Center list",
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
        add(ERROR, "SUBTOTAL_MISMATCH", f"line items sum to {line_sum:.2f} but subtotal = {coding.subtotal:.2f}")
    if not _close(computed_total, coding.grand_total):
        add(
            ERROR,
            "TOTAL_MISMATCH",
            f"subtotal + tax = {computed_total:.2f} but grand_total = {coding.grand_total:.2f}",
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
    adjusted = model_conf
    for issue in issues:
        adjusted *= _ERROR_PENALTY if issue.severity == ERROR else _WARNING_PENALTY
    adjusted = round(adjusted, 4)
    requires_review = adjusted < review_threshold or any(i.severity == ERROR for i in issues)

    return ValidationReport(
        source=extraction.source if extraction else "",
        model_confidence=model_conf,
        adjusted_confidence=adjusted,
        review_threshold=review_threshold,
        requires_review=requires_review,
        issues=issues,
        checks=checks,
    )


def _cross_check(coding: InvoiceCoding, extraction: ExtractionResult, add: Any, checks: dict[str, Any]) -> None:
    if extraction.mean_word_confidence is not None:
        checks["mean_ocr_word_confidence"] = round(extraction.mean_word_confidence, 4)
        if extraction.mean_word_confidence < _LOW_OCR_CONFIDENCE:
            add(
                WARNING,
                "LOW_OCR_CONFIDENCE",
                f"mean OCR word confidence {extraction.mean_word_confidence:.2f} < {_LOW_OCR_CONFIDENCE}",
            )

    fields = extraction.invoice_fields
    if not fields:
        return

    di_id = (fields.get("InvoiceId") or {}).get("value")
    if di_id and _norm_id(di_id) != _norm_id(coding.invoice_number):
        add(
            WARNING,
            "DI_INVOICE_ID_DIFFERS",
            f"prebuilt-invoice read InvoiceId {di_id!r}, model output {coding.invoice_number!r}",
        )

    di_date = (fields.get("InvoiceDate") or {}).get("value")
    if di_date and str(di_date)[:10] != coding.invoice_date:
        add(
            WARNING,
            "DI_DATE_DIFFERS",
            f"prebuilt-invoice read InvoiceDate {di_date}, model output {coding.invoice_date}",
        )

    for di_name, attr in (("InvoiceTotal", "grand_total"), ("SubTotal", "subtotal"), ("TotalTax", "tax_total")):
        value = (fields.get(di_name) or {}).get("value")
        amount = value.get("amount") if isinstance(value, dict) else value
        if isinstance(amount, (int, float)):
            ours = getattr(coding, attr)
            checks[f"di_{attr}"] = amount
            if not _close(ours, float(amount)):
                add(WARNING, "DI_AMOUNT_DIFFERS", f"prebuilt-invoice {di_name} = {amount}, model {attr} = {ours}")
