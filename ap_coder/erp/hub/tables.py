"""The finance hub's six tables, built from the Store (D-103; the data dictionary is docs/FINANCE_HUB.md).

Each table is a ``Table``: its name, its key columns and its columns, each with a stable name, a type and a meaning.
``build(store, name, ...)`` gives its rows as dicts with exactly those columns, in that order. Names and types never
change within a ``HUB_SCHEMA_VERSION``: a column added is a new version (``tests/test_finance_hub.py`` holds the
columns of this one, and checks the data dictionary describes every one of them).

Types (``TYPES``): string, int64, decimal(18,2) (money), float64, date (YYYY-MM-DD), timestamp (local time, to the
second, as AP Coder records it), boolean. A value AP Coder doesn't have is null (None), never "" or 0.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

HUB_SCHEMA_VERSION = 1

STRING, INT, MONEY, FLOAT, DATE, TIMESTAMP, BOOL = (
    "string", "int64", "decimal(18,2)", "float64", "date", "timestamp", "boolean")  # fmt: skip
TYPES = (STRING, INT, MONEY, FLOAT, DATE, TIMESTAMP, BOOL)

# How an invoice came in, by what AP Coder recorded about its file (``source_channel``).
DEMO, PICTURE, FILE = "demo", "picture", "file"
# The actions that withdraw an invoice's approval (``approvals.withdrawn``).
WITHDRAWN_BY = ("reopened", "sent_back")
# Sign-in events (logins, failed logins, passwords, unlocked addresses) stay out of ``activity``: they are the server's
# security record, not the work on invoices (docs/FINANCE_HUB.md).
SIGN_IN_ACTIONS = ("login", "logout", "login_failed", "login_locked", "password_changed", "password_reset",
                   "address_unlocked", "downloaded")  # fmt: skip
# Accounts and settings stay out too (docs/FINANCE_HUB.md "What is left out"): who has an account and what role, a
# backup put back, and settings changed (the tax setup and the policy notes too, B-1206) are the company server's
# administration, not the work on invoices.
ADMIN_ACTIONS = ("user_added", "role_changed", "user_activated", "user_deactivated", "backup_restored",
                 "settings_changed", "tax_setup_changed", "policy_changed")  # fmt: skip


def left_out_of_activity(action: str) -> bool:
    return action in SIGN_IN_ACTIONS or action in ADMIN_ACTIONS or action.startswith(("login", "logout"))


HIDDEN = "(hidden)"  # a vendor's bank details in an event's detail: never sent to the hub


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    meaning: str


@dataclass(frozen=True)
class Table:
    name: str
    meaning: str
    key: tuple[str, ...]
    columns: tuple[Column, ...]
    # "rows": every row is read and compared with what was sent (a fingerprint per key); "events": rows are only
    # ever added, read after the last event id sent (the watermark).
    changes: str = "rows"

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.columns]


def _c(name: str, type_: str, meaning: str) -> Column:
    return Column(name, type_, meaning)


INVOICES = Table(
    "invoices",
    "One row per invoice in AP Coder, whatever its status. The amounts and coding are the approved version once "
    "approved, else what AP Coder proposed.",
    ("invoice_id",),
    (
        _c("invoice_id", INT, "AP Coder's number for the invoice (the AP Coder # on screen). Key."),
        _c(
            "status",
            STRING,
            "review, parked, pending_approval (waiting for a second approval), approved, rejected "
            "or failed (the file could not be read).",
        ),
        _c(
            "coding_source",
            STRING,
            "approved: the values below are what AP approved; proposed: what AP Coder read "
            "and coded, not approved yet.",
        ),
        _c("vendor_name", STRING, "The supplier's name."),
        _c(
            "vendor_key",
            STRING,
            "The name as AP Coder matches vendors (no case, accents or legal form): the same "
            "supplier always has the same key.",
        ),
        _c(
            "vendor_erp_id",
            STRING,
            "The supplier's number in the ERP vendor master (JD Edwards address number), when known.",
        ),
        _c("invoice_number", STRING, "The supplier's invoice number, as printed."),
        _c("invoice_date", DATE, "The invoice date."),
        _c("due_date", DATE, "When it is due, from the terms (none for a credit note)."),
        _c("po_number", STRING, "The purchase order it quotes."),
        _c("payment_terms", STRING, "The payment terms, as printed."),
        _c("currency", STRING, "ISO currency code (CAD, USD...)."),
        _c("subtotal", MONEY, "Total before tax."),
        _c("gst_amount", MONEY, "GST charged (5%)."),
        _c("hst_amount", MONEY, "HST charged."),
        _c("pst_amount", MONEY, "Provincial sales tax charged (BC PST, Manitoba RST, Saskatchewan PST)."),
        _c("qst_amount", MONEY, "Quebec sales tax charged."),
        _c("other_tax_amount", MONEY, "Any other tax charged (a non-Canadian tax)."),
        _c("tax_total", MONEY, "All tax charged."),
        _c("grand_total", MONEY, "Amount due, tax included (below zero for a credit note)."),
        _c("is_credit_note", BOOL, "A credit note (a total below zero)."),
        _c("original_invoice_number", STRING, "For a credit note: the invoice it credits."),
        _c("supplier_province", STRING, "Province of the supplier's address (OUTSIDE_CANADA for a foreign one)."),
        _c("ship_to_province", STRING, "Province delivered to (the place of supply), when printed."),
        _c("line_count", INT, "How many lines (rows of invoice_lines)."),
        _c("confidence", FLOAT, "AP Coder's confidence in its reading and coding, 0 to 1."),
        _c("needs_review", BOOL, "AP Coder asked for a person to check it."),
        _c("source_file", STRING, "The file's name (the PDF or picture itself is not in the hub)."),
        _c(
            "source_channel",
            STRING,
            "file (a PDF or file brought in), picture (a photo or scan made into a PDF), demo (a sample invoice).",
        ),
        _c("received_at", TIMESTAMP, "When AP Coder read it."),
        _c("first_approved_by", STRING, "Who gave the (first) approval."),
        _c("first_approved_at", TIMESTAMP, "When."),
        _c("second_approved_by", STRING, "Who gave the second approval (over the approval limit)."),
        _c("second_approved_at", TIMESTAMP, "When."),
        _c("touchless", BOOL, "Approved by AP Coder without a person (touchless processing)."),
        _c("parked_reason", STRING, "Why it is parked, while parked."),
        _c("export_batch", INT, "The ERP export batch it went out in (none until exported, or once undone)."),
        _c("exported_at", TIMESTAMP, "When that batch was made."),
        _c("jde_voucher", STRING, "The JD Edwards voucher (document number) once posted."),
        _c("jde_doc_type", STRING, "Its document type (PV...)."),
        _c("jde_company", STRING, "Its document company."),
        _c("jde_posted_at", TIMESTAMP, "When it was posted."),
        _c("invoice_url", STRING, "A link to the invoice's page in AP Coder (the PDF stays there)."),
    ),
)

INVOICE_LINES = Table(
    "invoice_lines",
    "One row per line of each invoice, coded: the approved coding once approved, else AP Coder's proposal.",
    ("invoice_id", "line_position"),
    (
        _c("invoice_id", INT, "The invoice (invoices.invoice_id). Key, with line_position."),
        _c("line_position", INT, "The line's place on the invoice, 1 to line_count. Key, with invoice_id."),
        _c("line_number", INT, "The line number as AP Coder read it (two lines may share one)."),
        _c("description", STRING, "The line's description, as printed."),
        _c("quantity", FLOAT, "Quantity billed."),
        _c("unit_price", FLOAT, "Price per unit, before tax."),
        _c("amount", MONEY, "The line's amount, before tax."),
        _c("gl_account", STRING, "The GL account it is coded to (none while unassigned)."),
        _c("cost_center", STRING, "The cost center it is coded to."),
        _c(
            "tax_code",
            STRING,
            "The line's tax code as AP Coder shows it: the taxes charged (GST, GST+PST...), "
            "EXEMPT, ZERO or OUT, and what it is exempt from.",
        ),
        _c("taxes_applied", STRING, "The taxes charged on the line, joined with +."),
        _c("coding_source", STRING, "approved or proposed, as on invoices."),
    ),
)

APPROVALS = Table(
    "approvals",
    "One row per approval given: the first (by a clerk or AP Coder on its own) and the second (over the approval "
    "limit). An approval later withdrawn (the invoice reopened or sent back) stays, marked withdrawn.",
    ("approval_id",),
    (
        _c("approval_id", INT, "The approval's event in the audit trail (activity.event_id). Key."),
        _c("invoice_id", INT, "The invoice approved."),
        _c("step", STRING, "first or second."),
        _c("approver_name", STRING, "The name on the approval."),
        _c(
            "approver_account",
            STRING,
            "The account that approved (the username, on the company server; the computer login on a laptop).",
        ),
        _c("approver_role", STRING, "The account's role on the company server (clerk, approver, admin...)."),
        _c("approved_at", TIMESTAMP, "When."),
        _c("amount", MONEY, "The invoice total approved."),
        _c("currency", STRING, "Its currency."),
        _c("needs_second", BOOL, "A first approval that called for a second one."),
        _c("second_reason", STRING, "Why a second approval was needed."),
        _c("bulk", BOOL, "Approved in bulk from the review queue."),
        _c("touchless", BOOL, "Approved by AP Coder without a person."),
        _c("withdrawn", BOOL, "The approval was withdrawn since (the invoice reopened or sent back)."),
        _c("withdrawn_at", TIMESTAMP, "When."),
    ),
)

ACTIVITY = Table(
    "activity",
    "The audit trail: every event AP Coder records (read, approved, exported...), oldest first. "
    "Sign-in, account and settings events are left out.",
    ("event_id",),
    (
        _c("event_id", INT, "The event's number, in the order recorded. Key."),
        _c("invoice_id", INT, "The invoice it is about, if any."),
        _c(
            "action",
            STRING,
            "What happened (processed, approved, final_approved, exported, parked, rejected, "
            "import_run, hub_export...).",
        ),
        _c("actor", STRING, "Who did it (a person, or AP Coder)."),
        _c("occurred_at", TIMESTAMP, "When."),
        _c("detail_json", STRING, "What AP Coder recorded with it, as JSON (a vendor's bank details hidden)."),
    ),
    changes="events",
)

ACCURACY = Table(
    "accuracy",
    "For each approved invoice, field by field, what AP Coder proposed against what AP approved: the header "
    "fields (as the supplier learning measures them) and each line's GL account and cost center (as the learning "
    "memory records them).",
    ("accuracy_id",),
    (
        _c("accuracy_id", STRING, "header:<invoice>:<field>:<source>, or line:<lesson>:<field>. Key."),
        _c("invoice_id", INT, "The invoice."),
        _c("vendor_key", STRING, "Its supplier (invoices.vendor_key)."),
        _c("line_number", INT, "The line, for a line's field (none for a header field)."),
        _c("field", STRING, "The field: invoice_number, invoice_date, grand_total..., or gl_account, cost_center."),
        _c("proposed", STRING, "What AP Coder proposed."),
        _c("approved", STRING, "What AP approved."),
        _c(
            "correct",
            BOOL,
            "AP Coder had it right. None when it proposed nothing, or nobody looked (a bulk "
            "approval): left out of the accuracy, as on Learning & accuracy.",
        ),
        _c(
            "source",
            STRING,
            "review (checked by a person), audit (a touchless invoice checked by a person), "
            "computed (worked out from the invoice for one approved without that record), lines (a line's coding).",
        ),
        _c("recorded_at", TIMESTAMP, "When it was recorded (the approval)."),
    ),
)

WORKFLOW_RUNS = Table(
    "workflow_runs",
    "One row per run: each import of invoices, each ERP export batch, each finance hub feed.",
    ("run_id",),
    (
        _c("run_id", STRING, "import-<event>, erp_export-<batch> or hub_export-<event>. Key."),
        _c("kind", STRING, "import, erp_export or hub_export."),
        _c(
            "source",
            STRING,
            "For an import: dashboard, process or watch; for an export: its format; for the hub: folder or onelake.",
        ),
        _c("actor", STRING, "Who ran it (or AP Coder, on its schedule)."),
        _c("started_at", TIMESTAMP, "When it started."),
        _c("finished_at", TIMESTAMP, "When it finished."),
        _c("seconds", FLOAT, "How long it took."),
        _c("items", INT, "Files read (import), invoices (export) or rows sent (hub)."),
        _c("succeeded", INT, "Read and coded (import), exported (export), rows written (hub)."),
        _c("failed", INT, "Files that could not be read (import)."),
        _c("skipped", INT, "Files already in AP Coder (import)."),
        _c("total_amount", MONEY, "The batch's total (export)."),
        _c("status", STRING, "ok, failed (some or all failed), undone (an export batch undone)."),
        _c("errors", STRING, "The errors, one per line (the first 20)."),
    ),
)

TABLES: dict[str, Table] = {t.name: t for t in (INVOICES, INVOICE_LINES, APPROVALS, ACTIVITY, ACCURACY,
                                                 WORKFLOW_RUNS)}  # fmt: skip


# --- Values ---------------------------------------------------------------------------------------------------------


def money(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if math.isfinite(number) else None


def number(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def whole(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
    value = value.strip()
    return value or None


def day(value: Any) -> str | None:
    """YYYY-MM-DD, or None for a date that can't be read."""
    raw = str(value or "").strip()[:10]
    try:
        return dt.date.fromisoformat(raw).isoformat() if len(raw) == 10 else None
    except ValueError:
        return None


def stamp(value: Any) -> str | None:
    """YYYY-MM-DDTHH:MM:SS, or None."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw).replace(tzinfo=None, microsecond=0).isoformat()
    except ValueError:
        return None


def flag(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _hide_bank(value: Any) -> Any:
    """``value`` with every bank detail hidden, at any depth: a key naming a bank account, and a change of one
    (``{"what": "Remit bank account", "before": ..., "after": ...}``, already masked by ``core/audit``)."""
    if isinstance(value, dict):
        if "bank" in str(value.get("what") or "").lower():
            return {k: (HIDDEN if k in ("before", "after") and v else v) for k, v in value.items()}
        return {k: (HIDDEN if "bank" in str(k).lower() and v not in (None, "", [], {}) else _hide_bank(v))
                for k, v in value.items()}  # fmt: skip
    if isinstance(value, list):
        return [_hide_bank(v) for v in value]
    return value


# --- Building ---------------------------------------------------------------------------------------------------------


@dataclass
class Context:
    """What the builders share: the link base for invoice_url, and lookups read once."""

    link_base: str = "http://127.0.0.1:8501"
    vendor_ids: dict[str, str] | None = None
    batches: dict[int, dict[str, Any]] | None = None
    currencies: dict[int, str] | None = None  # {invoice: its currency}, for approvals

    def invoice_url(self, invoice_id: int) -> str:
        return f"{self.link_base.rstrip('/')}/review/{int(invoice_id)}"


def _coding(row: dict[str, Any]) -> tuple[dict[str, Any], str]:
    final = row.get("final_output")
    if isinstance(final, dict):
        return final, "approved"
    ai = row.get("ai_output")
    return (ai if isinstance(ai, dict) else {}), "proposed"


def _taxes(coding: dict[str, Any]) -> dict[str, float | None]:
    sums: dict[str, float] = {}
    for t in coding.get("tax_lines") or []:
        if isinstance(t, dict):
            kind = str(t.get("tax_type") or "OTHER").upper()
            kind = kind if kind in ("GST", "HST", "PST", "QST") else "OTHER"
            sums[kind] = sums.get(kind, 0.0) + (money(t.get("tax_amount")) or 0.0)
    return {k: (round(sums[k], 2) if k in sums else None) for k in ("GST", "HST", "PST", "QST", "OTHER")}


def _voucher(meta: dict[str, Any]) -> dict[str, Any]:
    """The JD Edwards voucher an invoice was posted as, from its record (``meta["jde_voucher"]``: the document
    number, or {document, doc_type, company, posted_at}); {} until posted."""
    found = meta.get("jde_voucher") if isinstance(meta, dict) else None
    if isinstance(found, dict):
        return found
    return {"document": found} if found not in (None, "") else {}


def _channel(row: dict[str, Any]) -> str:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    if meta.get("demo"):
        return DEMO
    if meta.get("original"):
        return PICTURE
    return FILE


def invoices(store: Any, ctx: Context, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    from ...capture.workflow import AUTONOMOUS_REVIEWER

    rows = store.hub_invoice_rows() if rows is None else rows
    vendor_ids = ctx.vendor_ids if ctx.vendor_ids is not None else store.vendor_ids()
    batches = ctx.batches if ctx.batches is not None else {int(b["id"]): b for b in store.export_batches()}
    out = []
    for r in rows:
        coding, source = _coding(r)
        meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
        taxes = _taxes(coding)
        total = money(coding.get("grand_total", r.get("grand_total")))
        batch = whole(r.get("export_batch"))
        made = batches.get(batch) if batch is not None else None
        voucher = _voucher(meta)
        out.append({
            "invoice_id": int(r["id"]), "status": text(r.get("status")), "coding_source": source,
            "vendor_name": text(coding.get("vendor_name") or r.get("vendor_name")),
            "vendor_key": text(r.get("vendor_key")), "vendor_erp_id": text(vendor_ids.get(r.get("vendor_key") or "")),
            "invoice_number": text(coding.get("invoice_number") or r.get("invoice_number")),
            "invoice_date": day(coding.get("invoice_date") or r.get("invoice_date")),
            "due_date": day(r.get("due_date")), "po_number": text(coding.get("po_number")),
            "payment_terms": text(coding.get("payment_terms")),
            "currency": text(str(coding.get("currency") or r.get("currency") or "").upper()),
            "subtotal": money(coding.get("subtotal")), "gst_amount": taxes["GST"], "hst_amount": taxes["HST"],
            "pst_amount": taxes["PST"], "qst_amount": taxes["QST"], "other_tax_amount": taxes["OTHER"],
            "tax_total": money(coding.get("tax_total")), "grand_total": total,
            "is_credit_note": None if total is None else total < 0,
            "original_invoice_number": text(coding.get("original_invoice_number")),
            "supplier_province": text(coding.get("supplier_province")),
            "ship_to_province": text(coding.get("ship_to_province")),
            "line_count": len(coding.get("line_items") or []),
            "confidence": number(r.get("adjusted_confidence")), "needs_review": flag(r.get("requires_review")),
            "source_file": text(r.get("file_name")), "source_channel": _channel(r),
            "received_at": stamp(r.get("created_at")),
            "first_approved_by": text(r.get("reviewer")), "first_approved_at": stamp(r.get("reviewed_at")),
            "second_approved_by": text(r.get("second_reviewer")),
            "second_approved_at": stamp(r.get("second_reviewed_at")),
            "touchless": r.get("reviewer") == AUTONOMOUS_REVIEWER if r.get("reviewer") else None,
            "parked_reason": text(r.get("parked_reason")) if r.get("status") == "parked" else None,
            "export_batch": batch, "exported_at": stamp(made.get("created_at")) if made else None,
            "jde_voucher": text(voucher.get("document") or voucher.get("number")),
            "jde_doc_type": text(voucher.get("doc_type")), "jde_company": text(voucher.get("company")),
            "jde_posted_at": stamp(voucher.get("posted_at")),
            "invoice_url": ctx.invoice_url(r["id"]),
        })  # fmt: skip
    return out


def invoice_lines(store: Any, ctx: Context, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    from ...coding.line_tax import legacy_basis, tax_code_of

    rows = store.hub_invoice_rows() if rows is None else rows
    out = []
    for r in rows:
        coding, source = _coding(r)
        items = coding.get("line_items") or []
        try:  # a line stored before ``tax_exempt`` is read as the review screen and the exports read it (B-1373)
            basis = legacy_basis([li for li in items if isinstance(li, dict)],
                                 [t for t in coding.get("tax_lines") or [] if isinstance(t, dict)])  # fmt: skip
        except Exception:  # noqa: BLE001 - a tax line stored oddly: its old lines go out without their codes
            basis = None
        for position, li in enumerate(items, start=1):
            if not isinstance(li, dict):
                continue
            gl = text(li.get("predicted_gl_code"))
            try:
                code = tax_code_of(li, basis or ()) if basis is not None or li.get("tax_exempt") is not None else None
            except Exception:  # noqa: BLE001 - a line stored oddly still goes out, without its code
                code = None
            out.append({
                "invoice_id": int(r["id"]), "line_position": position, "line_number": whole(li.get("line_number")),
                "description": text(li.get("description")), "quantity": number(li.get("quantity")),
                "unit_price": number(li.get("unit_price")), "amount": money(li.get("amount")),
                "gl_account": None if gl == "UNASSIGNED" else gl,
                "cost_center": None if li.get("predicted_cost_center") == "UNASSIGNED"
                else text(li.get("predicted_cost_center")),
                "tax_code": text(code), "taxes_applied": text("+".join(li.get("taxes_applied") or [])),
                "coding_source": source,
            })  # fmt: skip
    return out


def approvals(store: Any, ctx: Context, events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    from ...capture.workflow import AUTONOMOUS_REVIEWER

    events = store.hub_events(0, ["approved", "final_approved", *WITHDRAWN_BY]) if events is None else events
    roles = store.hub_user_roles()
    withdrawn: dict[int, list[tuple[int, str]]] = {}
    for e in events:
        if e["action"] in WITHDRAWN_BY and e.get("invoice_id") is not None:
            withdrawn.setdefault(int(e["invoice_id"]), []).append((int(e["id"]), e["created_at"]))
    out = []
    for e in events:
        if e["action"] not in ("approved", "final_approved"):
            continue
        d = e.get("detail") or {}
        invoice = whole(e.get("invoice_id"))
        later = [w for w in withdrawn.get(invoice, []) if w[0] > int(e["id"])] if invoice is not None else []
        account = roles.get(whole(d.get("user_id")) or -1, {})
        out.append({
            "approval_id": int(e["id"]), "invoice_id": invoice,
            "step": "first" if e["action"] == "approved" else "second",
            "approver_name": text(e.get("actor")), "approver_account": text(account.get("username") or d.get("login")),
            "approver_role": text(account.get("role")), "approved_at": stamp(e.get("created_at")),
            "amount": money(d.get("total")),
            "currency": text(d.get("currency") or (ctx.currencies or {}).get(invoice)),
            "needs_second": bool(d.get("needs_second")) if e["action"] == "approved" else None,
            "second_reason": text(d.get("second_reason")), "bulk": bool(d.get("bulk")),
            "touchless": e.get("actor") == AUTONOMOUS_REVIEWER, "withdrawn": bool(later),
            "withdrawn_at": stamp(later[0][1]) if later else None,
        })  # fmt: skip
    return out


# The learning memory's lessons stay out of the hub (FINANCE_HUB.md): an explained lesson's event is sent without
# the situation and AP's reason, who and when only (B-1216).
LESSON_DETAIL = {"lesson_explained": ("situation", "reason")}


def activity(store: Any, ctx: Context, after_id: int = 0) -> list[dict[str, Any]]:
    out = []
    for e in store.hub_events(after_id):
        action = str(e["action"] or "")
        if left_out_of_activity(action):
            continue
        # the computer it was done from stays out, as the sign-in events do (B-1206)
        hidden = {"address", *LESSON_DETAIL.get(action, ())}
        out.append({
            "event_id": int(e["id"]), "invoice_id": whole(e.get("invoice_id")), "action": text(e["action"]),
            "actor": text(e.get("actor")), "occurred_at": stamp(e.get("created_at")),
            "detail_json": json.dumps(_hide_bank({k: v for k, v in (e.get("detail") or {}).items() if k not in hidden}),
                                      ensure_ascii=False, sort_keys=True, default=str),
        })  # fmt: skip
    return out


def _shown(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value) if abs(value) < 1e15 else value
    return text(value)


def accuracy(store: Any, ctx: Context, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The header fields from the supplier learning's record (``supplier_outcomes``), or worked out the same way
    (``capture.supplier.outcome_rows``) for an approved invoice without one; the lines from the learning memory."""
    from ...capture.supplier import outcome_rows
    from ...core.memory import ACCEPTED, CORRECTED
    from ...core.schema import as_read

    rows = store.hub_invoice_rows() if rows is None else rows
    keys = {int(r["id"]): r.get("vendor_key") for r in rows}
    out: list[dict[str, Any]] = []
    recorded: set[int] = set()
    for o in store.hub_outcome_rows():
        invoice = int(o["invoice_id"])
        if invoice not in keys:
            continue  # its invoice was deleted
        recorded.add(invoice)
        out.append({
            "accuracy_id": f"header:{invoice}:{o['field']}:{o['source']}", "invoice_id": invoice,
            "vendor_key": text(keys.get(invoice)), "line_number": None, "field": o["field"],
            "proposed": _shown(o.get("ai_value")), "approved": _shown(o.get("final_value")),
            "correct": bool(o["correct"]), "source": o["source"], "recorded_at": stamp(o.get("at")),
        })  # fmt: skip
    for r in rows:
        if r["status"] not in ("approved", "pending_approval") or int(r["id"]) in recorded:
            continue
        ai, final = r.get("ai_output"), r.get("final_output")
        if not isinstance(ai, dict) or not isinstance(final, dict):
            continue
        for o in outcome_rows(ai, as_read(final, ai)):
            out.append({
                "accuracy_id": f"header:{r['id']}:{o['field']}:computed", "invoice_id": int(r["id"]),
                "vendor_key": text(r.get("vendor_key")), "line_number": None, "field": o["field"],
                "proposed": _shown(o.get("ai_value")), "approved": _shown(o.get("final_value")),
                "correct": bool(o["correct"]), "source": "computed", "recorded_at": stamp(r.get("reviewed_at")),
            })  # fmt: skip
    for f in store.hub_feedback_rows():
        invoice = int(f["invoice_id"])
        if invoice not in keys:
            continue
        for field, proposed, approved in (("gl_account", f["suggested_gl"], f["final_gl"]),
                                          ("cost_center", f["suggested_cc"], f["final_cc"])):  # fmt: skip
            proposed = None if proposed in (None, "", "UNASSIGNED") else proposed
            approved = None if approved in (None, "", "UNASSIGNED") else approved
            if proposed is None and approved is None:
                continue
            correct = None
            if proposed is not None and f["outcome"] in (ACCEPTED, CORRECTED):
                correct = proposed == approved
            out.append({
                "accuracy_id": f"line:{f['id']}:{field}", "invoice_id": invoice, "vendor_key": text(f["vendor_key"]),
                "line_number": whole(f["line_number"]), "field": field, "proposed": text(proposed),
                "approved": text(approved), "correct": correct, "source": "lines",
                "recorded_at": stamp(f["created_at"]),
            })  # fmt: skip
    return out


def workflow_runs(store: Any, ctx: Context) -> list[dict[str, Any]]:
    from ...core.store.hub import HUB_RUN, IMPORT_RUN

    out = []
    for e in store.hub_events(0, [IMPORT_RUN, HUB_RUN]):
        d = e.get("detail") or {}
        errors = [str(x) for x in d.get("errors") or []]
        if e["action"] == IMPORT_RUN:
            failed = whole(d.get("failed")) or 0
            out.append({
                "run_id": f"import-{e['id']}", "kind": "import", "source": text(d.get("source")),
                "actor": text(e.get("actor")), "started_at": stamp(d.get("started_at")),
                "finished_at": stamp(e.get("created_at")), "seconds": number(d.get("seconds")),
                "items": whole(d.get("files")), "succeeded": whole(d.get("ok")), "failed": failed,
                "skipped": whole(d.get("skipped")), "total_amount": None, "status": "failed" if failed else "ok",
                "errors": text("\n".join(errors)),
            })  # fmt: skip
        else:
            out.append({
                "run_id": f"hub_export-{e['id']}", "kind": "hub_export", "source": text(d.get("mode")),
                "actor": text(e.get("actor")), "started_at": stamp(d.get("started_at")),
                "finished_at": stamp(e.get("created_at")), "seconds": number(d.get("seconds")),
                "items": whole(d.get("rows")), "succeeded": whole(d.get("rows")) if d.get("ok") else 0,
                "failed": None, "skipped": None, "total_amount": None, "status": "ok" if d.get("ok") else "failed",
                "errors": text("\n".join(errors) or d.get("error")),
            })  # fmt: skip
    for b in sorted(store.export_batches(), key=lambda b: b["id"]):
        out.append({
            "run_id": f"erp_export-{b['id']}", "kind": "erp_export", "source": text(b.get("format")),
            "actor": text(b.get("actor")), "started_at": stamp(b.get("created_at")),
            "finished_at": stamp(b.get("created_at")), "seconds": None, "items": whole(b.get("invoices")),
            "succeeded": whole(b.get("invoices")), "failed": None, "skipped": None,
            "total_amount": money(b.get("total")), "status": "undone" if b.get("undone_at") else "ok",
            "errors": None,
        })  # fmt: skip
    return out


BUILDERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "invoices": invoices, "invoice_lines": invoice_lines, "approvals": approvals, "activity": activity,
    "accuracy": accuracy, "workflow_runs": workflow_runs,
}  # fmt: skip


def build(store: Any, name: str, ctx: Context | None = None, **kwargs: Any) -> list[dict[str, Any]]:
    """The rows of table ``name``, each with exactly its columns, in order."""
    table = TABLES[name]
    rows = BUILDERS[name](store, ctx or Context(), **kwargs)
    return [{c: r.get(c) for c in table.names} for r in rows]
