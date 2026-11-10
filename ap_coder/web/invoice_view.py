"""What the invoice review screen shows, worked out from the invoice and the reviewer's edits: ``analyse``. No web code
here or in its siblings: ``pages/invoice.py`` has the routes, ``templates/invoice.html`` and ``_invoice_*.html``
draw it.

``analyse`` rebuilds the coding from a draft (``review.coding_from_inputs``) and runs the checks approval runs
(``pipeline.finalise_coding``); ``Analysis.live`` holds plain values for the templates: the summary, the checks,
why these codes, GL suggestions, the PO match, the tax check, the posting preview and the approve bar.

The parts it puts together, one subject a module (D-223: a new piece of the screen goes in the module of its
subject, here only what ``analyse`` itself is made of):

* ``invoice_form.py``: the form's fields, ``Draft`` (what is on the screen), what the server fills in
  (``settle_header``), the words taught on the page.
* ``invoice_grid.py``: the line and tax grids (``grid``), the choices of their cells, the actions on the lines.
* ``invoice_reading.py``: what was read from the page (``capture_of``, ``shown_sure``), OvisOCR2 and the AI reviewer
  for this invoice (``reader_state``, ``reviewer_state``).
* ``invoice_findings.py``: the checks as the screen says them (``checks_summary``), what is never approved anyway
  (``HARD_CODES``) and what to fix first (``ordered_blockers``).
* ``invoice_fields.py``: the note under each invoice detail, the dates and currency typed (``settle_dates``).
* ``invoice_totals.py``: the summary, the Tax check, the GL posting preview (``tax_check``, ``distribution``).
* ``invoice_coding.py``: why these codes, suggestions, the PO match, what approving teaches (``learn_text``).

The store calls and the rules for what may be approved and what is learned are the store's and the pipeline's own.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from ap_coder.ai import verifier
from ap_coder.ai.extraction import ExtractionResult
from ap_coder.ai.pipeline import finalise_coding
from ap_coder.capture.bridge import review_issues
from ap_coder.capture.types import DATE_FIELDS, CaptureResult
from ap_coder.coding import banks, rules, vendor_mail
from ap_coder.coding.claim import totals_follow
from ap_coder.coding.review import codes_to_pick, coding_from_inputs, to_number, uses_cost_centers
from ap_coder.coding.tax import PLACE_OF_SUPPLY
from ap_coder.coding.taxmath import invoice_taxes, math_check
from ap_coder.coding.validation import INFO, Issue
from ap_coder.coding.vendors import MOVED_OFF_HOLD, moved_off_hold
from ap_coder.core.config import Settings
from ap_coder.core.memory import pair_lines, vendor_key
from ap_coder.core.reference_data import UNASSIGNED, ReferenceData
from ap_coder.core.schema import FROM_VENDOR_LIST, InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.core.store.accounts import CURRENCY_CHANGED
from ap_coder.core.store.invoices import TAX_ID_DIFFERS
from ap_coder.web import account_lines, identity
from ap_coder.web.access import may_lift_hold
from ap_coder.web.invoice_coding import ai_changes, differs_from_stored, learn_text, po_section, reasons, suggestions
from ap_coder.web.invoice_fields import (
    DateCheck,
    code_flags,
    currency_issues,
    field_flags,
    field_help,
    flag_errors,
    merge_flags,
    not_printed,
    plain_problem,
    settle_dates,
)
from ap_coder.web.invoice_findings import (
    checks_details,
    checks_summary,
    hard_reasons,
    is_hard,
    ordered_blockers,
    override_key,
    settle_absent,
)
from ap_coder.web.invoice_form import (
    AMOUNT_FIELDS,
    Draft,
    _date_or_none,
    _number,
    header_values,
    lines_frame,
    settle_header,
    signed,
    tax_frame,
)
from ap_coder.web.invoice_reading import capture_of
from ap_coder.web.invoice_totals import (
    distribution,
    posting_note,
    spend_split,
    stored_summary,
    summary,
    tax_check,
    tax_marks,
)


@dataclass
class Analysis:
    """The coding rebuilt from the screen, its checks, and what the templates show (``live``)."""

    coding: InvoiceCoding | None
    problems: list[str]
    output: dict[str, Any] = field(default_factory=dict)
    report: Any = None
    errors: list[Any] = field(default_factory=list)
    warnings: list[Any] = field(default_factory=list)
    allow: bool = False
    changed: bool = False  # differs from the stored coding (an edit not approved yet)
    live: dict[str, Any] = field(default_factory=dict)
    filled: dict[str, str] = field(default_factory=dict)  # details the server filled in (``settle_header``)
    taxes: Any = None  # each line's taxes (``taxmath.invoice_taxes``), worked out once, with a chart of accounts


def keep_taxes_read(coding: InvoiceCoding, stored: list[dict[str, Any]]) -> None:
    """A line's tax amounts as the invoice prints them on it (``LineItem.taxes_read``) stay with the line through
    the review screen, whose form posts no such field: each line takes those of the stored line it pairs with, while
    its amount is the one printed (a part of a split line, or a line whose amount was corrected, has no printed tax
    of its own: its tax is worked out)."""
    for before, li in pair_lines(stored, list(coding.line_items)):
        read = (before or {}).get("taxes_read")
        if read and li.taxes_read is None and abs(_number(before.get("amount")) - li.amount) < 0.005:
            li.taxes_read = dict(read)


def totals_settled(coding: InvoiceCoding, ai: dict[str, Any]) -> bool:
    """Whether the reviewer changed the amounts as read and they now add up (subtotal + tax = total, the tax lines
    = the tax): capture's "as printed, … ≠ total" no longer says anything about the invoice on screen (B-662)."""

    def cents(value: Any) -> int:
        try:
            return round(float(value or 0) * 100)
        except (TypeError, ValueError):
            return 0

    changed = any(cents(ai.get(f)) != cents(getattr(coding, f)) for f in AMOUNT_FIELDS)
    changed = changed or sorted(cents(t.get("tax_amount")) for t in ai.get("tax_lines") or []) != sorted(
        cents(t.tax_amount) for t in coding.tax_lines
    )
    adds_up = abs(cents(coding.subtotal) + cents(coding.tax_total) - cents(coding.grand_total)) <= 1
    taxes = abs(sum(cents(t.tax_amount) for t in coding.tax_lines) - cents(coding.tax_total)) <= 1
    return changed and adds_up and (taxes or not coding.tax_lines)


def lines_settled(coding: InvoiceCoding, ai: dict[str, Any]) -> bool:
    """Whether a person changed the lines' amounts as read and they now add up to the subtotal: the reading's "the
    lines add up to …, the invoice says …" no longer says anything about the lines on screen (B-2210)."""

    def cents(value: Any) -> int:
        try:
            return round(float(value or 0) * 100)
        except (TypeError, ValueError):
            return 0

    now = [cents(li.amount) for li in coding.line_items]
    changed = now != [cents(li.get("amount")) for li in ai.get("line_items") or []]
    return changed and abs(sum(now) - cents(coding.subtotal)) <= 1


def received_on(inv: dict[str, Any]) -> dt.date | None:
    """The day the invoice came in (its ``created_at``), None when it can't be read."""
    return _date_or_none(inv.get("created_at"))


def tax_id_second(store: Store, output: dict[str, Any]) -> str:
    """Why approving this coding needs a second approver for its tax ID ("" when it does not): it differs from the
    supplier list's and people sign in, so approved anyway, someone else confirms it (``Store.approve_invoice``,
    D-219). Said on the approve bar before approving, as a currency changed is."""
    person = identity.person()
    return TAX_ID_DIFFERS if person is not None and person.user_id and store.tax_id_differs(output) else ""


_UNREAD: Any = object()  # analyse without a capture given: it reads the invoice's own


def _findings(store: Store, inv: dict[str, Any], draft: Draft, coding: InvoiceCoding, output: dict[str, Any],
              report: Any, dates: DateCheck, capture: CaptureResult | None) -> list[str]:  # fmt: skip
    """What ``analyse`` adds to the checks of ``finalise_coding`` (``report.issues``), in the order the screen lists
    them: the AI reviewer's, capture's, the fields said absent, the vendor list's, the dates and currency as typed
    (first), no Pay from account. Returns the fields the vendor list filled (``FROM_VENDOR_LIST``)."""
    ai = inv["ai_output"] or {}
    # The AI reviewer's findings, on what is on screen now: a field it disputed that the reviewer changed is settled.
    review = store.verification(inv["id"])
    for severity, code, message in verifier.issues((review or {}).get("result"), output):
        report.issues.append(Issue(severity, code, message))
    # What capture found about the page (pages not read, a currency or a date not printed, totals that don't add up as
    # printed...): kept with the invoice when it was read, and shown here too (B-458). A date not found is assumed
    # only while the date is still the one put in for it.
    if capture is not None:
        # Assumed while the date is still the one put in, unless a person approved it so (reopened, sent back) or
        # showed it on the page.
        confirmed = bool(inv.get("final_output")) or "invoice_date" in draft.taught
        assumed = coding.invoice_date if coding.invoice_date == ai.get("invoice_date") and not confirmed else None
        settled = totals_settled(coding, ai)
        for severity, code, message in review_issues(capture, coding.currency, assumed_date=assumed):
            if code == "CAPTURE_CHECK_FIELDS":  # the fields to check are highlighted in amber: no need to list them
                continue
            if code == "CAPTURE_TOTALS" and settled:  # the reviewer fixed the amounts and they add up now (B-662)
                continue
            if code == "LINE_AMOUNTS_TO_CHECK" and lines_settled(coding, ai):  # the lines were made to add up
                continue
            report.issues.append(Issue(severity, code, message))
    if draft.absent:  # fields the reviewer said are not on this invoice: nothing asks for them (D-60)
        report.issues = settle_absent(report.issues, draft.absent)
    from_list = sorted((draft.from_vendor.get("fields") or {}).keys())
    report.issues += not_printed(coding, from_list)
    found = identity.person()
    if found is not None and found.user_id:  # the company server: a held vendor's invoice moved (D-142); not a laptop
        report.issues += [Issue(*f) for f in moved_off_hold(store, ai, coding)]
    typed = dates.issues + currency_issues(coding.currency)  # the dates and the currency as typed (D-75)
    if typed:
        report.issues = typed + report.issues
        report.requires_review = True
    if from_list:
        output[FROM_VENDOR_LIST] = from_list  # kept with the coding: the tax return's claims at risk read it
    # No bank account in use: nothing asks for a Pay from account, and the screen says where to add them (D-66).
    if not store.bank_accounts(active_only=True):
        report.issues.append(Issue(INFO, "PAY_FROM_NO_ACCOUNTS", banks.NONE_YET))
    return from_list


def _vendor_mail(doc: dict[str, Any], issues: list[Any], lang: str, signature: str) -> dict[str, Any] | None:
    """The email to the vendor about what its invoice is missing, in each language (the one picked, or suggested,
    first shown), or None when nothing is the vendor's to fix."""
    if not vendor_mail.points(doc, issues):
        return None
    chosen = lang if lang in vendor_mail.LANGUAGES else vendor_mail.suggested_language(doc)
    drafts = []
    for code, name in vendor_mail.LANGUAGES.items():
        m = vendor_mail.draft(doc, issues, code, signature)
        drafts.append({"lang": code, "name": name, "subject": m.subject, "body": m.body, "mailto": m.mailto()})
    return {"chosen": chosen, "drafts": drafts}


def _rebuilt(header: dict[str, Any], problems: list[str], draft: Draft, ai: dict[str, Any], start: dict[str, Any],
             has_cc: bool, coding_rules: Any) -> tuple[InvoiceCoding | None, list[str]]:  # fmt: skip
    """The coding rebuilt from the screen's header (with ``problems``, what its fields already have wrong) and its
    grids, each line keeping the tax amounts the invoice prints on it; None with what keeps it from being built, in
    plain words."""
    coding = None
    if not problems:
        coding, problems = coding_from_inputs(header, lines_frame(draft.lines), tax_frame(draft.taxes), ai,
                                              UNASSIGNED if has_cc else "", coding_rules)  # fmt: skip
    if coding is not None:
        keep_taxes_read(coding, start.get("line_items") or [])
    return coding, [plain_problem(p) for p in problems]


def _settled(store: Store, inv: dict[str, Any], draft: Draft, ai: dict[str, Any]) -> dict[str, str]:
    """What the server fills in the screen's details before they are checked ({field: value}, for the screen to
    show): ``settle_header``'s, and a claim's totals once the amount of a receipt that was not in them is typed on
    its line (``claim.totals_follow``, B-2286)."""
    filled = settle_header(store, draft, ai)
    filled.update(totals_follow(draft.header, draft.lines, store.joined_files(invoice_id=inv["id"]), to_number))
    return filled


def analyse(store: Store, settings: Settings, reference: ReferenceData, inv: dict[str, Any], draft: Draft,
            signature: str = "", capture: CaptureResult | None = _UNREAD) -> Analysis:  # fmt: skip
    """The screen's coding, checked as approval checks it. ``signature``: the reviewer's name, for the email to the
    vendor; ``capture``: the invoice's capture (``capture_of``) when the caller has it already."""
    ai = inv["ai_output"] or {}
    start = inv.get("final_output") or ai
    has_cc = reference.cost_centers is not None
    filled = _settled(store, inv, draft, ai)  # the vendor list's tax numbers, the default Pay from account
    header, problems = header_values(draft.header)
    # A date typed another way, unclear or not a date: said at its field, the checks meanwhile on the date as read.
    dates = settle_dates(header, {**ai, **{k: v for k, v in start.items() if k in DATE_FIELDS}})
    header = dates.header
    filled.update(dates.filled)
    coding_rules = store.coding_rules()
    coding, problems = _rebuilt(header, problems, draft, ai, start, has_cc, coding_rules)
    if coding is None:
        live = {"ok": False, "problems": problems, "amount_line": _amount_line(header),
                "fallback": stored_summary(inv, start)}  # fmt: skip
        return Analysis(coding=None, problems=problems, live=live, filled=filled)

    # What the screen fills itself on every check (not an edit, B-801): the default Pay from account (none picked for
    # this currency) and the tax numbers the vendor list filled, while they are as filled.
    auto = {"pay_from_account"} if not draft.pay_from_for else set()
    auto |= {n for n, v in (draft.from_vendor.get("fields") or {}).items() if draft.header.get(n) == v}
    meta = inv.get("meta") or {}
    extraction = ExtractionResult(
        source=inv["source_path"],
        model_id=(meta.get("extraction") or {}).get("model_id", ""),
        content="",
        mean_word_confidence=(meta.get("extraction") or {}).get("mean_word_confidence"),
        invoice_fields=(meta.get("extraction") or {}).get("invoice_fields") or {},
    )
    output, report = finalise_coding(coding, reference, settings, extraction, store=store,
                                     exclude_invoice_id=inv["id"], received=received_on(inv))  # fmt: skip
    if capture is _UNREAD:
        capture = capture_of(store, inv["id"])
    from_list = _findings(store, inv, draft, coding, output, report, dates, capture)
    errors = [i for i in report.issues if i.severity == "error"]
    warnings = [i for i in report.issues if i.severity == "warning"]
    default_days = store.default_terms_days()
    terms = store.vendor_terms(vendor_key(coding.vendor_name))

    # The approve bar: never post to UNASSIGNED; where cost centers are used, a line without one isn't posted either.
    # The queue asks the same question (pages/review.py attention), so its Ready means Approve is open here.
    cc = uses_cost_centers(reference)
    line_pick = pick = codes_to_pick(coding.line_items, cc)
    # never approved without one of our accounts in use to pay from, whatever is ticked (D-66, B-683)
    bank = banks.blocking(coding.pay_from_account, store.bank_accounts(), coding.currency)
    if bank:
        pick = f"{pick}; {bank[0].lower()}{bank[1:]}" if pick else bank
    # What makes the voucher invalid is never approved anyway; the rest of the errors only with a reason (D-75).
    hard = [i for i in errors if is_hard(i)]
    soft = [i for i in errors if not is_hard(i)]
    if not may_lift_hold():  # a Clerk on the server: no "Approve anyway" for a held vendor's invoice moved (D-142)
        hard += [Issue(i.severity, i.code, f"Only an Approver can approve it. {i.message[:1].upper()}{i.message[1:]}")
                 for i in soft if i.code == MOVED_OFF_HOLD]  # fmt: skip
        soft = [i for i in soft if i.code != MOVED_OFF_HOLD]
    blockers = ordered_blockers(line_pick, bank, hard, account_lines.bar_words(coding.line_items, reference))
    key = override_key(soft) if soft else ""
    reason = draft.override_reason.strip()
    allow = not pick and not hard and (not soft or (draft.override == key and bool(reason)))
    # A currency changed from the one read: when the limit in that one is why a second approver is needed, the bar
    # says so before approving (D-132).
    second = store.approval_reason(inv, output)[0] if coding.currency != (ai.get("currency") or "CAD") else ""
    changed = ai_changes(coding, ai, cc)
    lines_n = len(coding.line_items)
    _, rule_changes = rules.apply(coding, coding_rules)
    doc = coding.model_dump()
    if PLACE_OF_SUPPLY in output:  # worked out, no ship-to printed: the vendor is told where it is delivered
        doc[PLACE_OF_SUPPLY] = output[PLACE_OF_SUPPLY]
    mail = _vendor_mail(doc, report.issues, draft.mail_lang, signature)

    from ap_coder.erp import jde_accounts, jde_review, jde_settings

    js = jde_settings.load(store) if jde_settings.in_use(store) else None  # JD Edwards is the export in use (D-75)
    taxes = invoice_taxes(coding, reference.tax) if account_lines.in_use(reference) else None  # once a draw (D-214)
    an8 = jde_accounts.resolve_an8(output, store.vendor_ids(), js) if js is not None else ""
    live = {
        "ok": True,
        "account": taxes is not None,  # a chart of accounts: the review screen of D-214
        "tax_said": account_lines.tax_said(taxes, report.issues) if taxes is not None else None,
        "problems": [],
        "amount_line": _amount_line(header),
        "summary": summary(coding, report, errors, warnings, default_days, terms, reference.tax),
        "checks": checks_summary(report, errors, warnings, taxes is not None),
        "details": checks_details(report),
        "rules_applied": [rules.describe(c) for c in meta.get("rules_applied") or []],
        "rule_changes": {
            "numbers": ", ".join(str(c["line_number"]) for c in rule_changes),
            "help": "; ".join(rules.describe(c) for c in rule_changes).replace("the AI chose", "now"),
        } if rule_changes else None,
        "mail": mail,
        "reasons": reasons(coding, ai, report, reference),
        "suggestions": suggestions(store, reference, coding),
        "po": po_section(store, coding, inv["id"]),
        "tax_check": tax_check(coding, reference),
        "math": (math := math_check(coding, reference.tax)),
        "tax_marks": tax_marks(math, report.issues),
        "split": spend_split(output, reference),
        "posting": {**distribution(output, reference, js), "note": posting_note(coding, reference)},
        "flags": merge_flags(field_flags(report.issues, store, coding, inv["id"]), code_flags(report.issues),
                             dates.notes),
        "flag_errors": flag_errors(errors, dates.notes),
        "help": field_help(store, coding, draft, capture, dates, report.issues),
        "jde": jde_review.voucher_view(output, js, an8) if js is not None else None,
        "an8": an8 if js is not None else None,
        "from_vendor": from_list,
        "bar": {
            "amount": signed(coding.grand_total), "currency": coding.currency, "lines": lines_n, "changed": changed,
            "learn": learn_text(coding, ai, cc, meta.get("rules_applied")),
            "pick": pick, "errors": len(soft), "override_key": key, "override": bool(key) and draft.override == key,
            "override_reason": draft.override_reason if bool(key) and draft.override == key else "",
            "hard": hard_reasons(hard), "allow": allow, "credit": coding.grand_total < 0,
            "first": blockers[0] if blockers else None, "blockers": blockers,
            "second": second if second.startswith(CURRENCY_CHANGED) else tax_id_second(store, output),
        },
    }  # fmt: skip
    return Analysis(coding=coding, problems=[], output=output, report=report, errors=errors, warnings=warnings,
                    allow=allow, changed=differs_from_stored(coding, start, cost_centers=has_cc, filled=auto),
                    live=live, filled=filled, taxes=taxes)  # fmt: skip


def _amount_line(header: dict[str, Any]) -> dict[str, Any] | None:
    """ "1,000.00 + 130.00 tax = 1,130.00 CAD", and whether it adds up (the inputs can't show separators)."""
    try:
        sub, tax, total = (float(header.get(f) or 0) for f in AMOUNT_FIELDS)
    except (TypeError, ValueError):
        return None
    return {"subtotal": signed(sub), "tax": signed(tax), "total": signed(total),
            "currency": header.get("currency") or "", "adds_up": abs(sub + tax - total) < 0.015}  # fmt: skip
