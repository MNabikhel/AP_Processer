"""The checks of one coded invoice, and what they make of its confidence.

The reader's or the model's own confidence is not enough to let an invoice through. ``validate_coding`` reconciles
the arithmetic (lines, subtotal, tax, total, the GL posting to the cent), checks every GL account and cost center
against the reference data, runs the Canadian sales-tax checks (``tax.check_taxes``), compares the lines with what
reviewers coded before and with Azure's reading when there is one, and gathers the vendor, PO and JD Edwards findings
made elsewhere. ``score`` then gives the two confidences (D-89): the Reading (the header) and the Coding (the lines'
accounts, cost centers and tax codes, ``coding_confidence``), the invoice's being the lower, and whether it needs a
person (``needs_review``): what the review queue sorts and flags on.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..ai.extraction import ExtractionResult
from ..core import chart
from ..core.reference_data import UNASSIGNED, ReferenceData
from ..core.schema import InvoiceCoding
from .money import CENT, EXACT, LINE_MATH_SHARE, READ_AGAIN, add_up, cash_rounded, half_up, nil, per_line, within
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
# finding keeps Approve closed but takes nothing more off (as setup findings, D-86). So does a line coded to an account
# its business unit does not have, or that is not to be posted to there, with a chart of accounts imported (D-209).
COUNTED_IN_CODING = frozenset({"GL_UNASSIGNED", "CC_UNASSIGNED", "GL_UNKNOWN", "CC_UNKNOWN", "GL_IS_TAX_ACCOUNT",
                               "PST_EXEMPTION_TO_CONFIRM", "ACCOUNT_NOT_IN_CHART", "ACCOUNT_NOT_POSTABLE",
                               "BU_INACTIVE", "ACCOUNT_RESERVED"})  # fmt: skip
# Findings about the business, not about how the invoice was read or coded (the vendor master, a purchase order, a
# possible duplicate, an unusual amount): like setup findings they keep Approve closed (an error), go to a person and
# are said, but take nothing off the Reading or the Coding: every value can be read right and still not be in the
# vendor master (D-89).
BUSINESS_PREFIXES = ("PO_", "DUPLICATE_")
BUSINESS_CODES = frozenset({"VENDOR_NOT_IN_MASTER", "VENDOR_ON_HOLD", "VENDOR_CHANGED_FROM_HELD", "VENDOR_ADDED_HERE",
                            "VENDOR_BANK_CHANGED", "VENDOR_BANK_NOT_JDE", "POSSIBLE_DUPLICATE_AMOUNT",
                            "VENDOR_PAYMENTS_HELD",
                            "AMOUNT_UNUSUAL"})  # fmt: skip
# Findings that doubt the lines' coding rather than the reading: they take their share off the Coding.
ABOUT_CODING = frozenset({"HISTORY_CONFLICT", "TAX_EXEMPTION_LEARNED", "EXEMPT_LINE_TAXED", "TAX_NOT_ALLOCATED",
                          "LINE_TAX_NOT_CHARGED", "TAX_BASE_MISMATCH", "MEALS_TAX_LINES"})  # fmt: skip
# A line's account the chat model chose or a weak match (``offline_coder.unsure``), and a tax code worked out from
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
    # Which reference data the checks were made with (``ReferenceData.stamp``; "" when it was read from files): the
    # store reads it to stamp what the invoice was checked against, and does not keep it (B-2094).
    reference_stamp: str = ""

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        if not out["reference_stamp"]:
            del out["reference_stamp"]
        return out


_DUPLICATE_STANDS = {"review": "waiting for review", "parked": "set aside", "approved": "approved",
                     "pending_approval": "waiting for a second approval"}  # fmt: skip


def duplicate_message(duplicate_of: list[Any]) -> str:
    """DUPLICATE_INVOICE's words: the other invoices with the same vendor and invoice number. Each is its number, or
    what is known of it (``pipeline.duplicates_known``: {"id", "first": received before this one, "status",
    "approved": when, "exported"}): the message then says which was received first and where the other stands, and
    which of the two to reject, where both said "reject this one" (B-2101)."""
    import datetime as dt

    known = [d for d in duplicate_of if isinstance(d, dict)]
    if len(known) != len(duplicate_of) or not known:
        ids = ", ".join(f"#{d['id'] if isinstance(d, dict) else d}" for d in duplicate_of)
        return f"same vendor and invoice number as invoice {ids}: possible duplicate payment"
    said = []
    for other in known:
        stands = _DUPLICATE_STANDS.get(other.get("status") or "", "")
        if other.get("status") == "approved" and other.get("approved"):
            day = dt.datetime.fromisoformat(str(other["approved"]))
            year = f", {day.year}" if day.year != dt.date.today().year else ""
            stands = f"approved on {day:%b} {day.day}{year}"
        stands += " and exported" if stands and other.get("exported") else ""
        order = "received first" if other.get("first") else "received after this one"
        said.append(f"#{other['id']} ({', '.join(filter(None, [order, stands]))})")
    if any(o.get("first") for o in known):
        advice = "If it is the same bill, reject this one"
    elif any(o.get("status") == "approved" for o in known):
        advice = "It is approved already: if it is the same bill, reject this one"
    else:
        others = "the other" if len(known) == 1 else "the others"
        advice = f"This one came first: if it is the same bill, reject {others}"
    return f"same vendor and invoice number as invoice {', '.join(said)}: possible duplicate payment. {advice}"


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
        add(ERROR, "DUPLICATE_INVOICE", duplicate_message(duplicate_of))
    for severity, code, message in vendor_findings or []:  # vendor master / fraud signals (vendors.py)
        add(severity, code, message)
    for severity, code, message, line in po_findings or []:  # purchase order match (po.py)
        add(severity, code, message, line)
    for severity, code, message, line in erp_findings or []:  # what the ERP's batch would refuse (erp/jde.py)
        add(severity, code, message, line)

    # --- Header -----------------------------------------------------------
    _header_issues(coding, add)
    # --- Line items -------------------------------------------------------
    _line_issues(coding, reference, add)
    # --- Totals reconciliation (what counts as equal: ``money``, D-183) ---------
    checks = _totals_checks(coding, reference, add)
    # --- Canadian sales tax ------------------------------------------------------
    known = set(reference.chart_of_accounts.codes)
    for f in check_taxes(coding, reference.tax, known):
        add(f.severity, f.code, f.message, f.line_number)
    checks["tax_lines"] = [f"{t.tax_type} {t.province} {t.rate:g}".strip() for t in coding.tax_lines]

    # --- Reviewer history -------------------------------------------------------------
    _history_issues(history, add, checks)

    # --- Cross-checks against Document Intelligence ---------------------------
    if extraction is not None:
        _cross_check(coding, extraction, add, checks)

    model_conf = coding.confidence_score
    reading, coded, adjusted, requires_review = score(
        model_conf, coding_confidence(coding, reference), [(i.severity, i.code) for i in issues], review_threshold
    )

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
        reference_stamp=getattr(reference, "stamp", ""),
    )


def _header_issues(coding: InvoiceCoding, add: Any) -> None:
    """The vendor, invoice number, currency and lines an invoice must have."""
    if not coding.vendor_name.strip():
        add(ERROR, "MISSING_VENDOR", "No vendor name: type it from the page")
    if not coding.invoice_number.strip():
        add(ERROR, "MISSING_INVOICE_NUMBER", "No invoice number: type it from the page")
    if not re.fullmatch(r"[A-Z]{3}", coding.currency):
        add(WARNING, "CURRENCY_FORMAT", f"The currency “{coding.currency}” is not a 3-letter code such as CAD or USD")
    if not coding.line_items:
        add(ERROR, "NO_LINE_ITEMS", "No line items were read: add a line for what the invoice charges")


def _named(business_units: tuple[str, ...], first: str = "") -> str:
    """Up to three business units by code, the home one first: "A, B and C", or "A, B, C and 12 more"."""
    found = sorted(business_units, key=lambda bu: bu != first)
    if len(found) > 3:
        return f"{', '.join(found[:3])} and {len(found) - 3} more"
    return " and ".join(filter(None, (", ".join(found[:-1]), found[-1])))


def _pair_issue(reference: ReferenceData, li: Any, tax_gls: Any) -> tuple[str, str, str] | None:
    """With a chart of accounts imported (D-209), what is wrong with a line's account on its business unit (its cost
    center), as (severity, code, message). None with no chart, for a line not coded to both yet or coded to a tax
    account (said on its own), and for a pair that exists and may be posted to."""
    pairs, gl, cc = reference.pairs, str(li.predicted_gl_code or ""), str(li.predicted_cost_center or "")
    if pairs is None or reference.cost_centers is None or gl in ("", UNASSIGNED) or cc in ("", UNASSIGNED):
        return None
    status = chart.OK if gl in tax_gls else pairs.status(cc, gl)
    account = chart.join_account(cc, gl)
    if status == chart.RESERVED:
        return (WARNING, "ACCOUNT_RESERVED", f"account {account} exists but is not offered for invoices: "
                f"{pairs.why_reserved(cc, gl)}. Check it is the one meant")  # fmt: skip
    if status == chart.BU_INACTIVE:
        return (ERROR, "BU_INACTIVE", f"business unit {cc} is not active in JD Edwards, so nothing can be posted to "
                "it: pick another business unit")  # fmt: skip
    if status == chart.NOT_POSTABLE:
        return (ERROR, "ACCOUNT_NOT_POSTABLE", f"account {account} cannot be posted to (its posting edit code is "
                f"{pairs.posting_edit(cc, gl)}): pick an account that takes postings")  # fmt: skip
    if status != chart.NOT_IN_CHART:
        return None
    others = pairs.bus_of(gl)
    if not pairs.has_business_unit(cc):
        why = f"the chart has no business unit {cc} (pick one from the list)"
    elif others:
        why = f"business unit {cc} has no account {gl}; it is on {_named(others, pairs.settings.home_business_unit)}"
    elif pairs.has_code(gl):
        why = f"business unit {cc} has no account {gl}, and no business unit has one that may be posted to"
    else:
        why = f"no business unit has an account {gl}: pick one from the list"
    return ERROR, "ACCOUNT_NOT_IN_CHART", f"account {account} is not in the chart of accounts: {why}"


def _line_issues(coding: InvoiceCoding, reference: ReferenceData, add: Any) -> None:
    """Each line's numbering, arithmetic, GL account and cost center; with a chart of accounts imported, the two
    together (``_pair_issue``)."""
    numbers = [li.line_number for li in coding.line_items]
    if sorted(numbers) != list(range(1, len(numbers) + 1)):  # the parts of a split line sit under it (B-1188)
        add(WARNING, "LINE_NUMBERING", f"The line numbers don't run 1, 2, 3…: {', '.join(map(str, numbers))}")

    tax_gls = reference.tax.tax_gl_codes()
    credit = coding.grand_total < 0
    for li in coding.line_items:
        expected = li.quantity * li.unit_price
        # Unit prices are often printed rounded, so allow 0.5% on the extension. A credit note puts its minus on the
        # quantity, the price or the amount alone (2 × 50.00 = -100.00): its lines are compared in size (B-1484).
        amount, size = (abs(li.amount), abs(expected)) if credit else (li.amount, expected)
        if not within(amount, size, max(READ_AGAIN, abs(expected) * LINE_MATH_SHARE)):
            add(
                WARNING,
                "LINE_MATH",
                f"quantity × unit price = {expected:.2f} but the amount is {li.amount:.2f}",
                li.line_number,
            )

        pair = _pair_issue(reference, li, tax_gls)
        if pair is not None:
            add(*pair, li.line_number)
        told = pair is not None and pair[0] == ERROR  # the pair's error says why: not one more for each of its codes
        if li.predicted_gl_code == UNASSIGNED:
            add(WARNING, "GL_UNASSIGNED", "no GL account yet: pick one", li.line_number)
        elif not told and reference.chart_of_accounts.get(li.predicted_gl_code) is None:
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
        elif not told and reference.cost_centers.get(li.predicted_cost_center) is None:
            add(
                ERROR,
                "CC_UNKNOWN",
                f"cost center {li.predicted_cost_center} is not in your cost centers: pick one from the list",
                li.line_number,
            )


def _totals_checks(coding: InvoiceCoding, reference: ReferenceData, add: Any) -> dict[str, Any]:
    """The lines, subtotal, tax, total and GL posting reconciled; the figures compared (the report's checks)."""
    line_sum = add_up(li.amount for li in coding.line_items)
    computed_total = half_up(coding.subtotal + coding.tax_total)
    checks: dict[str, Any] = {
        "line_count": len(coding.line_items),
        "line_sum": line_sum,
        "subtotal": coding.subtotal,
        "subtotal_plus_tax": computed_total,
        "grand_total": coding.grand_total,
    }
    if coding.line_items and not within(line_sum, coding.subtotal, per_line(len(coding.line_items))):
        add(ERROR, "SUBTOTAL_MISMATCH", f"the lines add up to {line_sum:.2f} but the subtotal is {coding.subtotal:.2f}"
            + (TAX_INCLUDED_HINT if prices_include_tax(coding) else ""))  # fmt: skip
    # A cent, or a total rounded to the nickel for cash with its rounding a line (B-1907).
    if not within(computed_total, coding.grand_total, CENT) and not cash_rounded(
        computed_total, coding.grand_total, (li.amount for li in coding.line_items)
    ):
        add(
            ERROR,
            "TOTAL_MISMATCH",
            f"subtotal + tax = {computed_total:.2f} but the total is {coding.grand_total:.2f}",
        )
    # The GL posting (line amounts + tax lines) must equal the amount payable to the cent. The checks
    # above each allow a little rounding; this catches small differences that add up (e.g. a misread line).
    # Added up from the posting itself, which rounds each line to the cent: two lines of 10.005 post as
    # 10.01 + 10.01, so summing first (20.01) would pass an invoice whose export is a cent off.
    posting_total = add_up(e["amount"] for e in build_gl_distribution(coding, reference.tax))
    checks["posting_total"] = posting_total
    if coding.line_items and not within(posting_total, coding.grand_total, EXACT):
        add(
            ERROR,
            "POSTING_UNBALANCED",
            f"the GL posting adds up to {posting_total:.2f} but the invoice total is {coding.grand_total:.2f} "
            f"({off_text(posting_total, coding.grand_total)}): correct a line amount or a tax line",
        )
    return checks


def _history_issues(history: list[dict[str, Any]] | None, add: Any, checks: dict[str, Any]) -> None:
    """Lines coded otherwise than reviewers coded the vendor's similar lines (``memory.compare_with_history``)."""
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


def score(
    reading: float, coded: float, findings: list[tuple[str, str]], review_threshold: float
) -> tuple[float, float, float, bool]:
    """(the Reading, the Coding, the invoice's confidence: the lower, whether it needs a person) from the reading's
    and the coding's own figures and the findings ``(severity, code)`` (D-89)."""
    # Each kind of finding counts once: eight lines without an account are one thing to do, not eight. Setup and
    # business findings (D-86) and what the Coding already counts take nothing off (``doubtless``).
    for severity, code in {(sev, code) for sev, code in findings if sev != INFO and not doubtless(code)}:
        penalty = _ERROR_PENALTY if severity == ERROR else _WARNING_PENALTY
        if code in ABOUT_CODING:
            coded *= penalty
        else:
            reading *= penalty
    reading, coded = round(reading, 4), round(coded, 4)
    adjusted = min(reading, coded)
    return reading, coded, adjusted, needs_review(adjusted, findings, review_threshold)


def needs_review(adjusted: float, findings: list[tuple[str, str]], review_threshold: float) -> bool:
    """Whether an invoice of confidence ``adjusted`` with these findings ``(severity, code)`` needs a person."""
    # A line to code, a business finding and a doubt about the coding (a person looks at it, whatever the numbers:
    # taken off the Coding alone, a warning no longer brings a well-read invoice under the threshold, B-892) keep it
    # for a person.
    return (adjusted < review_threshold or any(sev == ERROR for sev, _ in findings)
            or any(code in COUNTED_IN_CODING or (sev != INFO and (business(code) or code in ABOUT_CODING))
                   for sev, code in findings))  # fmt: skip


def business(code: str) -> bool:
    """Whether a finding is about the business (the vendor master, a PO, a possible duplicate, an unusual amount)."""
    return code.startswith(BUSINESS_PREFIXES) or code in BUSINESS_CODES


def doubtless(code: str) -> bool:
    """Whether a finding takes nothing off the Reading or the Coding (D-86, D-89): a setup finding, one about the
    business (``BUSINESS_CODES``), or a line to code, already counted in the Coding (``COUNTED_IN_CODING``)."""
    return code.startswith(SETUP_PREFIXES) or business(code) or code in COUNTED_IN_CODING


def line_coding_confidence(line: Any, cost_centers: bool, taxed: bool, known: Any = None, cc_known: Any = None,
                           tax_gls: Any = (), pairs: Any = None) -> float:  # fmt: skip
    """How sure one line's coding is: its account (none, or one not in the GL accounts or a tax account: 0; a guess:
    ``GUESS_SURE``; else sure), its cost center where they are used (none or unknown: 0), and its tax code when the
    invoice charges a tax (worked out from several possible sets of lines, an exemption to confirm, or none:
    ``GUESS_SURE``), multiplied. ``known``/``cc_known``: the GL accounts and cost centers (a ``ReferenceTable``);
    ``pairs``: the chart of accounts' (``chart.AccountPairs``, D-209): an account its cost center, a business unit,
    does not have, or that is not to be posted to there, is 0 as an unknown code is."""
    from ..ai.offline_coder import unsure
    from .line_tax import CODE_NONE, exemption_to_confirm, tax_code_of, tax_guessed

    gl = str(line.predicted_gl_code or "")
    if gl in ("", UNASSIGNED) or (known is not None and known.get(gl) is None) or gl in tax_gls:
        return 0.0
    sure = GUESS_SURE if unsure(line.reasoning_justification) else 1.0
    if cost_centers:
        cc = str(line.predicted_cost_center or "")
        if cc in ("", UNASSIGNED) or (cc_known is not None and cc_known.get(cc) is None):
            return 0.0
        if pairs is not None and pairs.status(cc, gl) != chart.OK:
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
    sure = [line_coding_confidence(li, ccs is not None, taxed, reference.chart_of_accounts, ccs, tax_gls,
                                   reference.pairs) for li in coding.line_items]  # fmt: skip
    return round(sum(sure) / len(sure), 4)


def off_text(posted: float, total: float) -> str:
    """How the invoice total differs from what its lines post, in words: "the total is 10.00 more than the lines"
    (never "off by -10.00", whose sign reads either way, B-805)."""
    gap = half_up(total - posted)
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
    lines = add_up(li.amount for li in coding.line_items)
    tax = add_up(t.tax_amount for t in coding.tax_lines) or coding.tax_total
    return (bool(coding.line_items) and not nil(tax) and within(lines, coding.grand_total, CENT)
            and not within(lines, coding.subtotal, CENT))  # fmt: skip


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
            if not within(ours, float(amount), READ_AGAIN):
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
