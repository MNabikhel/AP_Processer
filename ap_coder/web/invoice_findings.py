"""The checks' findings as the review screen says them, and what keeps Approve closed, in the order to fix it.

* A finding in a clerk's words (``readable``), the same one on several lines said once (``grouped_issues``).
* The confidence (``confidence_pct``, ``confidences``, ``low_confidence``).
* The checks panel: ``checks_summary`` (before you can approve, must be fixed, check before approving) and
  ``checks_details``; which invoice detail a finding is about (``FIELD_FLAGS``, ``CODE_FIELDS``) and which box it goes
  to (``GOTO_BOX``); a detail marked "Not on this invoice" (``settle_absent``).
* What "Approve anyway" never covers (``HARD_CODES``, ``is_hard``, ``hard_reasons``), the key of the errors it does
  cover (``override_key``), and everything that keeps Approve closed from the top of the screen down
  (``ordered_blockers``).

The notes under the invoice details are ``invoice_fields.py``'s; the checks themselves, ``coding/validation.py``'s.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from ap_coder.coding.validation import COUNTED_IN_CODING, INFO, Issue
from ap_coder.ops.help import help_for
from ap_coder.ops.offline import setup_contact
from ap_coder.web import account_lines
from ap_coder.web.invoice_form import ABSENT_FIELDS

# --- A finding in words -------------------------------------------------------------------------------------------

_PLAIN_AMOUNT = re.compile(r"(?<![\d.,])(-?\d{4,})\.(\d{2})(?![\d%])")


# The code's field names in the checks' messages ("but grand_total = 0.00"), as the form names them (B-429).
_PLAIN_FIELDS = (("quantity x unit_price", "quantity × unit price"), ("grand_total", "the total"),
                 ("tax_total", "the tax total"), ("unit_price", "unit price"))  # fmt: skip


def readable(message: str, cap: bool = True) -> str:
    """A check message as a clerk reads it: plain words for field names, a capital first letter, amounts with
    thousands separators."""
    for code, words in _PLAIN_FIELDS:
        message = message.replace(code, words)
    message = _PLAIN_AMOUNT.sub(lambda m: f"{int(m.group(1)):,}.{m.group(2)}", message)
    return message[:1].upper() + message[1:] if cap else message


def grouped_issues(issues: list[Any]) -> list[tuple[bool, str, str, list[int]]]:
    """(is error, text, code, line numbers) per distinct message: the same finding on several lines is one item
    ("Lines 1, 3, 4: …")."""
    order: dict[tuple[str, str, str], list[int]] = {}
    for issue in issues:
        order.setdefault((issue.severity, issue.code, issue.message), []).append(issue.line_number or 0)
    out = []
    for (severity, code, message), lines in order.items():
        numbers = sorted({n for n in lines if n})
        where = (
            (f"Line {numbers[0]}: " if len(numbers) == 1 else f"Lines {', '.join(map(str, numbers))}: ")
            if numbers
            else ""
        )
        out.append((severity == "error", where + readable(message, cap=not where), code, numbers))
    return out


def grouped(issues: list[Any]) -> list[tuple[bool, str]]:
    """(is error, text) per distinct message (``grouped_issues``)."""
    return [(bad, text) for bad, text, _code, _lines in grouped_issues(issues)]


# --- The confidence -----------------------------------------------------------------------------------------------


def confidence_pct(value: float, threshold: float) -> str:
    """The invoice's confidence as a percentage, rounded the usual way, so the screen, the queue and the messages
    agree. Where that rounding would mislead (84.9% shown as the 85% threshold it is below, or 99.6% as certainty) it
    keeps one decimal, rounded down."""
    value = max(0.0, min(1.0, value or 0.0))
    whole = int(f"{value * 100:.0f}")
    if (value < threshold and whole >= int(f"{threshold * 100:.0f}")) or (value < 1 and whole >= 100):
        return f"{math.floor(value * 1000 + 1e-9) / 10:.1f}%"
    return f"{whole}%"


def confidences(report: Any) -> list[tuple[str, float]]:
    """[("Reading", 0.97), ("Coding", 0.75)]: the two numbers of D-89 (a report without them: its one Confidence)."""
    reading = getattr(report, "reading_confidence", None)
    coding = getattr(report, "coding_confidence", None)
    if reading is None or coding is None:
        return [("Confidence", report.adjusted_confidence or 0.0)]
    return [("Reading", reading), ("Coding", coding)]


def low_confidence(report: Any) -> list[str]:
    """What is below the review threshold, as the checks say it ("Reading 80% is below the 85% threshold."). The
    Coding is not said when a line still to code (or an account or cost center to pick) is why: that finding says it
    (D-89)."""
    picks = any(i.code in COUNTED_IN_CODING for i in report.issues)
    return [f"{name} {confidence_pct(value, report.review_threshold)} is below the {report.review_threshold:.0%} "
            "threshold."
            for name, value in confidences(report)
            if value < report.review_threshold and not (name == "Coding" and picks)]  # fmt: skip


# --- The checks panel ---------------------------------------------------------------------------------------------

# The checks that are about one invoice detail: the detail is highlighted in amber with a short note under it (the
# checks panel lists them too). {check: (field, note)}; a note of None is worked out (``field_flags``).
FIELD_FLAGS: dict[str, tuple[str, str | None]] = {
    "VENDOR_TAX_NUMBER_CHANGED": ("gst_hst_registration_number", None),
    "GST_NUMBER_CHECK_DIGIT": ("gst_hst_registration_number", "Fails the CRA check digit"),
    "GST_HST_NUMBER_FORMAT": ("gst_hst_registration_number", "Not in the format 123456789 RT0001"),
    "VENDOR_QST_CHANGED": ("qst_registration_number", None),
    "QST_NUMBER_FORMAT": ("qst_registration_number", "Not in the format 1234567890 TQ0001"),
    "VENDOR_PST_CHANGED": ("pst_registration_number", None),
    "VENDOR_BANK_CHANGED": ("remit_bank_account", "Not the account on this vendor's approved invoices: confirm by "
                            "phone before paying"),
    "VENDOR_BANK_NOT_JDE": ("remit_bank_account", "Not the account JD Edwards pays this vendor on: confirm by phone "
                            "before paying"),
    "PAY_FROM_CURRENCY": ("pay_from_account", "Not an account in the invoice's currency"),
    "PAY_FROM_MISSING": ("pay_from_account", "Pick the account that pays this invoice"),
    "PAY_FROM_NO_ACCOUNT": ("pay_from_account", "No bank account in the invoice's currency"),
    "PAY_FROM_UNKNOWN": ("pay_from_account", "Not one of your bank accounts in use"),
}  # fmt: skip


# The checks shown under the field they are about (their own words), besides FIELD_FLAGS' notes: {code: field}.
CODE_FIELDS = {
    "MISSING_VENDOR": "vendor_name", "VENDOR_NOT_IN_MASTER": "vendor_name", "VENDOR_ADDED_HERE": "vendor_name",
    "VENDOR_ON_HOLD": "vendor_name", "VENDOR_CHANGED_FROM_HELD": "vendor_name", "JDE_NO_AN8": "vendor_name",
    "JDE_BAD_AN8": "vendor_name",
    "MISSING_INVOICE_NUMBER": "invoice_number", "JDE_INVOICE_NUMBER_LONG": "invoice_number",
    "DUPLICATE_INVOICE": "invoice_number", "DUPLICATE_IN_ERP": "invoice_number",
    "INVOICE_DATE_NOT_FOUND": "invoice_date", "OLD_INVOICE_DATE": "invoice_date", "FUTURE_INVOICE_DATE": "invoice_date",
    "DUE_BEFORE_INVOICE": "due_date", "JDE_DATE_RANGE": "invoice_date",
    "CURRENCY_UNKNOWN": "currency", "CURRENCY_FORMAT": "currency", "JDE_CURRENCY": "currency",
    "PO_UNKNOWN": "po_number", "PO_CLOSED": "po_number", "PO_VENDOR_MISMATCH": "po_number", "JDE_PO_LONG": "po_number",
    "SUBTOTAL_MISMATCH": "subtotal", "TOTAL_MISMATCH": "grand_total", "TAX_LINES_TOTAL_MISMATCH": "tax_total",
    "JDE_TAX_AREA": "ship_to_province",
}  # fmt: skip


# A finding about a line's account or cost center: its item in the checks is a button to that box (B-443).
GOTO_BOX = {
    "GL_UNASSIGNED": "line_gl",
    "CC_UNASSIGNED": "line_cc",
    "PST_EXEMPTION_TO_CONFIRM": "line_tax_code",
    "TAX_EXEMPTION_LEARNED": "line_tax_code",
}


# The groups of the checks, in order: what keeps Approve closed (a GL account or cost center to pick: G goes to the
# first), the errors (to fix, or to approve anyway), and the warnings to check (B-465).
BLOCK, FIX, LOOK = "block", "err", "warn"
GROUP_TITLES = {BLOCK: "Before you can approve", FIX: "Must be fixed", LOOK: "Check before approving"}


def checks_summary(report: Any, errors: list[Any], warnings: list[Any], account: bool = False) -> dict[str, Any]:
    """All the checks as one status: passed, or what needs attention (one line each; details below), grouped:
    before you can approve (what keeps Approve closed), must be fixed, check before approving. ``account``: a line
    is one Account box (a chart of accounts), so a line without its account is one finding, not one a code."""
    low = low_confidence(report) if not errors else []
    items: list[dict[str, Any]] = []
    for bad, text, code, lines in grouped_issues(errors + warnings):
        kind = BLOCK if code in GOTO_BOX else FIX if bad else LOOK
        goto = {"box": GOTO_BOX[code], "line": lines[0]} if code in GOTO_BOX and lines else None
        # A possible duplicate: a link to each invoice it may duplicate, to compare them (D-60).
        links = ([{"href": f"/review/{n}", "text": f"Open #{n}"} for n in re.findall(r"#(\d+)", text)]
                 if code == "DUPLICATE_INVOICE" else [])  # fmt: skip
        field = "" if lines else CODE_FIELDS.get(code) or FIELD_FLAGS.get(code, ("", None))[0]
        items.append({"bad": kind != LOOK, "kind": kind, "text": text, "goto": goto, "links": links, "code": code,
                      "field": field, "lines": lines})  # fmt: skip
    for text in low:
        items.append({"bad": False, "kind": LOOK, "text": text})
    if account:
        account_lines.said_once(items)
    if not items:
        return {"tone": "ok", "icon": "check_circle", "title": "All checks passed", "items": [], "groups": [],
                "sub": "Totals reconcile, taxes verified, all codes valid."}  # fmt: skip
    counts = {k: sum(1 for i in items if i["kind"] == k and not i.get("folded")) for k in GROUP_TITLES}
    groups = [{"kind": k, "title": f"{GROUP_TITLES[k]} ({counts[k]})",
               "items": [i for i in items if i["kind"] == k]} for k in GROUP_TITLES if counts[k]]  # fmt: skip
    title = " · ".join(g["title"] for g in groups)
    tone, icon = ("err", "error") if counts[BLOCK] or counts[FIX] else ("warn", "visibility")
    return {"tone": tone, "icon": icon, "title": title, "items": [i for g in groups for i in g["items"]],
            "groups": groups, "sub": "Press G to go to the first one." if counts[BLOCK] else ""}  # fmt: skip


def checks_details(report: Any) -> list[dict[str, str]]:
    """Every finding, with what to do about it (the arguments of ``ui.check``)."""
    items = []
    for issue in report.issues:
        where = f"Line {issue.line_number}: " if issue.line_number else ""
        about = help_for(issue.code)
        hint = setup_contact(about.action) if about and issue.severity != "info" else ""  # an Admin on the server
        kind, title = {"error": ("error", "Must be fixed"), "info": ("info", "Good to know")}.get(
            issue.severity, ("warning", GROUP_TITLES[BLOCK] if issue.code in GOTO_BOX else GROUP_TITLES[LOOK])
        )
        items.append({"kind": kind, "title": title, "message": where + readable(issue.message, cap=not where),
                      "code": issue.code,
                      "hint": "" if kind == "info" else hint})  # fmt: skip
    errors = [i for i in report.issues if i.severity == "error"]
    for text in low_confidence(report) if not errors else []:
        items.append({"kind": "warning", "title": "Low confidence", "code": "", "hint": "",
                      "message": text.rstrip(".") + ", so a person should look it over."})  # fmt: skip
    matches = [h for h in report.checks.get("history") or [] if h["status"] == "match"]
    if matches:
        numbers = ", ".join(str(h["line_number"]) for h in matches)
        items.append({"kind": "info", "title": "Learned", "code": "", "hint": "",
                      "message": f"Line {numbers} matches how reviewers coded this vendor before."})  # fmt: skip
    if not [i for i in report.issues if i.severity != "info"]:
        items.append(
            {
                "kind": "ok",
                "title": "Numbers check out" if report.requires_review else "All clear",
                "message": "Totals reconcile, taxes verified, all codes valid.",
                "code": "",
                "hint": "",
            }
        )
    return items


def settle_absent(issues: list[Any], absent: list[str]) -> list[Any]:
    """The checks once the reviewer said a field is not on the invoice: what asked for it is said as information
    (what it costs), or left out (ABSENT_FIELDS). Errors are never touched."""
    swap: dict[str, Any] = {}
    for name in absent:
        swap.update(ABSENT_FIELDS.get(name) or {})
    out = []
    for issue in issues:
        if issue.code not in swap or issue.severity == "error":
            out.append(issue)
        elif swap[issue.code] is not None:
            code, message = swap[issue.code]
            out.append(Issue(INFO, code, message, issue.line_number))
    return out


# --- What keeps Approve closed ------------------------------------------------------------------------------------

# The findings that make a voucher invalid: "Approve anyway" never covers them (D-75). Every JD Edwards finding
# (JDE_..., erp/jde.voucher_findings) is one too: the batch would hold the voucher back.
HARD_CODES = frozenset({
    "MISSING_VENDOR", "MISSING_INVOICE_NUMBER", "NO_LINE_ITEMS", "SUBTOTAL_MISMATCH", "TOTAL_MISMATCH",
    "POSTING_UNBALANCED", "INVOICE_DATE_INVALID", "DUE_DATE_INVALID", "DATE_AMBIGUOUS", "CURRENCY_UNKNOWN",
    # An account that is wrong or missing would post to the wrong (or a blank) GL (B-926).
    "GL_IS_TAX_ACCOUNT", "GL_UNKNOWN", "CC_UNKNOWN", "TAX_GL_UNMAPPED", "TAX_GL_UNKNOWN",
    # With a chart of accounts imported, an account its business unit does not have or that takes no postings is said
    # in place of the two above (D-209): JD Edwards would refuse the line.
    "ACCOUNT_NOT_IN_CHART", "ACCOUNT_NOT_POSTABLE", "BU_INACTIVE",
    # A vendor on hold: the hold is lifted first (the Vendors page, or JD Edwards), or the invoice rejected (B-1544).
    "VENDOR_ON_HOLD",
})  # fmt: skip


def is_hard(issue: Any) -> bool:
    """An error "Approve anyway" never covers (``HARD_CODES``, and every JD Edwards finding)."""
    return issue.severity == "error" and (issue.code in HARD_CODES or issue.code.startswith("JDE_"))


def override_key(errors: list[Any]) -> str:
    """Keyed on the errors: a new error (an edit after the tick) asks again instead of riding along."""
    seen = ",".join(sorted(f"{i.code}:{i.line_number}" for i in errors))
    return hashlib.sha1(seen.encode()).hexdigest()[:10]


def _short_reason(text: str) -> str:
    # the first sentence ("e.g. CAD" is no sentence's end)
    return re.split(r"(?<=[a-z0-9)])(?<!e\.g)\. (?=[A-Z])|: (?=ask (?:the pilot tester|an Admin))", text, maxsplit=1)[
        0
    ].rstrip(".")


def hard_reasons(hard: list[Any]) -> list[str]:
    """What keeps a voucher from being approved, even anyway: one short line each (D-75)."""
    return [_short_reason(text) for _bad, text in grouped(hard)]


# The review screen from top to bottom (B-797): the invoice details in the form's order, then the JD Edwards block,
# the lines, the tax lines and the posting. The approve bar says the first thing that keeps it closed, as the clerk
# meets it: a currency typed wrong before the bank account that follows it.
SCREEN_ORDER = (
    "vendor_name", "gst_hst_registration_number", "qst_registration_number", "pst_registration_number",
    "supplier_province", "remit_bank_account", "invoice_number", "invoice_date", "po_number", "payment_terms",
    "due_date", "currency", "pay_from_account", "ship_to_province", "original_invoice_number", "subtotal", "tax_total",
    "grand_total", "jde", "lines", "tax_lines", "posting",
)  # fmt: skip
_CODE_PLACES = {"NO_LINE_ITEMS": "lines", "POSTING_UNBALANCED": "posting"}


def ordered_blockers(line_pick: str, bank: str, hard: list[Any], said: str = "") -> list[dict[str, str]]:
    """Everything that keeps Approve closed, in the order of the screen: {"text", "kind"} (``pick``: a line's account
    or cost center, a button to it; ``bank``: the Pay from account; ``hard``: what makes the voucher invalid).
    ``said``: the pick as a screen with one Account box a line says it (``account_lines.bar_words``), its ``said``."""
    place = {name: i for i, name in enumerate(SCREEN_ORDER)}
    found: list[tuple[int, int, dict[str, str]]] = []
    for n, (_bad, text, code, lines) in enumerate(grouped_issues(hard)):
        where = "lines" if lines else CODE_FIELDS.get(code) or _CODE_PLACES.get(code) or "posting"
        found.append((place.get(where, len(place)), n, {"text": _short_reason(text), "kind": "hard"}))
    if line_pick:
        found.append((place["lines"], -1, {"text": line_pick, "kind": "pick", **({"said": said} if said else {})}))
    if bank:
        found.append((place["pay_from_account"], -1, {"text": bank, "kind": "bank"}))
    return [b for _, _, b in sorted(found, key=lambda f: (f[0], f[1]))]
