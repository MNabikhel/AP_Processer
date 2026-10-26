"""Bulk approval of clean invoices.

The queue says which invoices look clean by today's checks: those made against other settings (GL accounts, cost
centers, tax, JD Edwards) are done again (``issues_today``, B-815). An invoice qualifies only if, re-checked right now
(duplicates and history may have changed since it was processed), it has no error and no warning, no finding that
always goes to a person (``ALWAYS_A_PERSON``: some, like a new vendor name, are only info), it is not a credit note (a
credit is always applied by a person, as the touchless rules say) and the AI's confidence is above the review
threshold.
Everything else is skipped with the reason, and still needs a person.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from ..ai.pipeline import finalise_coding
from ..capture.supplier import ALWAYS_A_PERSON, touchless_gates
from ..capture.workflow import learn_from_approval
from ..core.config import Settings
from ..core.reference_data import ReferenceData
from ..core.schema import InvoiceCoding
from ..core.store import REVIEW, Store
from ..core.store._base import CHECKS_STAMP_KEY


def _received_on(inv: dict[str, Any]) -> dt.date | None:
    """The day the invoice came in (its ``created_at``), None when it can't be read."""
    try:
        return dt.date.fromisoformat(str(inv.get("created_at") or "")[:10])
    except ValueError:
        return None


def _plain(message: Any, code: Any) -> str:
    """Why an invoice was not approved, as the reviewer reads it: the check's message (its code only when it has none:
    the code is for the problem report, B-477)."""
    text = str(message or "").strip()
    if not text:
        from ..ops.help import help_for

        about = help_for(str(code or ""))
        text = about.title if about else str(code or "a check needs a person")
    return text[:1].upper() + text[1:]


def needs_a_person(severity: Any, code: Any) -> bool:
    """An error, a warning, or a finding that always goes to a person whatever its severity: bulk approval leaves the
    invoice for a person, and the queue does not call it Ready (B-1027)."""
    return severity in ("error", "warning") or str(code or "") in ALWAYS_A_PERSON


_needs_a_person = needs_a_person


AUDIT_PICK = "picked for the audit sample: a person checks it"


def _audit_pick(meta: Any) -> bool:
    """Picked for the touchless audit sample (``meta.capture.audit``): a person must check it, never bulk (B-952)."""
    return bool(((meta or {}).get("capture") or {}).get("audit")) if isinstance(meta, dict) else False


def _credit_note(titled: bool, grand_total: Any) -> bool:
    """A total below zero, or titled a credit note by what capture read (whatever the sign of its total): the same
    notion as the touchless gates (``supplier.touchless_gates``)."""
    return bool(touchless_gates(grand_total=grand_total, credit_note=titled))


# Findings made after the coding's checks, from the page and the readers: kept from the stored checks as they are, when
# today's checks are worked out again (``issues_today``); the coding's own checks are re-done. A code no check
# describes (``ops.help.CHECKS``) is kept too: never dropped unseen.
AFTER_PROCESSING = frozenset({
    "FILE_CUT_OFF", "AZURE_DI_DISAGREES", "AZURE_DI_NOT_COMPARED", "VERIFIER_DISAGREES", "VERIFIER_UNSURE",
    "CAPTURE_TOTALS", "CAPTURE_CHECK_FIELDS", "TEXT_LAYER_MATCHES_PAGE", "LINE_ITEMS_AGREE", "INVOICE_DATE_NOT_FOUND",
    "PAGES_NOT_READ", "CURRENCY_NOT_FOUND",
})  # fmt: skip


def _kept(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stored findings today's checks do not make again (``AFTER_PROCESSING``)."""
    from ..ops.help import CHECKS

    return [i for i in issues if str(i.get("code") or "") in AFTER_PROCESSING or str(i.get("code") or "") not in CHECKS]


def _recheck(
    store: Store, reference: ReferenceData, settings: Settings, row: dict[str, Any]
) -> list[dict[str, Any]] | None:
    """The coding's checks of an invoice in the queue as they come out today (what ``bulk_approve`` checks), as
    stored findings; None when its coding can't be read (the stored checks stand)."""
    coded = row.get("final_output") or row.get("ai_output")
    if not coded:
        return None
    coded = {k: v for k, v in coded.items() if k != "gl_distribution"}
    try:
        coding = InvoiceCoding.model_validate(coded)
    except ValueError:
        return None
    _, report = finalise_coding(coding, reference, settings, store=store, exclude_invoice_id=row["id"],
                                received=_received_on(row))  # fmt: skip
    return [{"severity": i.severity, "code": i.code, "message": i.message, "line_number": i.line_number}
            for i in report.issues]  # fmt: skip


def issues_today(
    store: Store, ids: list[int], reference: ReferenceData | None = None, settings: Settings | None = None
) -> dict[int, list[dict[str, Any]]]:
    """{id: its findings by today's checks} for the invoices of ``ids`` whose stored checks were made against other
    settings than today's (``Store.checks_stamp``: the GL accounts, cost centers, tax setup, JD Edwards...): the
    coding's checks done again (kept beside the stored ones, so once per change of settings), plus the stored
    findings made after them (``_kept``). An invoice not listed: its stored checks are today's (B-815). About 5 ms
    an invoice, once per change of settings: the queue asks it of the invoices not flagged only."""
    if not ids:
        return {}
    with store.session():  # one connection for every invoice checked again
        return _issues_today(store, ids, reference, settings)


def _issues_today(
    store: Store, ids: list[int], reference: ReferenceData | None, settings: Settings | None
) -> dict[int, list[dict[str, Any]]]:
    stamp = store.checks_stamp()
    stamps = store.check_stamps(ids)
    stale = [i for i in ids if i in stamps and stamps[i][0] != stamp]
    if not stale:
        return {}
    rows = store.invoice_columns(("id", "validation", "ai_output", "final_output", "created_at"), ids=stale,
                                 decode=False)  # fmt: skip
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        seen = row["validation"]
        validation = json.loads(seen) if seen else {}
        stored = validation.get("issues") or []
        recheck = validation.get("recheck") or {}
        if recheck.get("stamp") == stamp:
            today = recheck.get("issues") or []
        else:
            if reference is None:
                try:
                    reference = store.reference_data()
                except Exception:  # noqa: BLE001 - no reference data yet: the stored checks stand
                    return out
            decoded = {**row, "ai_output": json.loads(row["ai_output"]) if row["ai_output"] else None,
                       "final_output": json.loads(row["final_output"]) if row["final_output"] else None}  # fmt: skip
            today = _recheck(store, reference, settings or Settings(), decoded)
            if today is None:
                continue
            store.save_recheck(row["id"], stamp, today, seen)
        out[row["id"]] = today + [i for i in _kept(stored) if i.get("code") not in {t.get("code") for t in today}]
    return out


def clean_candidates(store: Store) -> list[dict[str, Any]]:
    """Invoices in the queue that were clean when processed (no review flag, no errors or warnings, nothing that
    always goes to a person, not a credit note)."""
    return sort_for_bulk(store)[0]


def sort_for_bulk(
    store: Store, queue: list[dict[str, Any]] | None = None, today: dict[int, list[dict[str, Any]]] | None = None
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """(``clean_candidates``, why each other invoice of the queue not flagged when processed is left for a person:
    "credit note", "audit" for one picked for the touchless audit sample, or "warning" for an error, a warning or a
    finding that always goes to a person). ``queue``: the
    invoices in review as ``store.list_invoices`` gives them, when the caller has them already. ``today``:
    ``issues_today`` of the queue, when the caller has it already. The checks are today's: an invoice whose stored
    checks were made against other settings is checked again (B-815)."""
    # What the stored checks and metadata say, from the invoices' summary (``queue_facts``, D-110); one the summary
    # could not read is read again here.
    facts = {r["id"]: r for r in store.summary_rows("status = ?", (REVIEW,), "id, broken, q_audit, q_person")}
    broken = [i for i, r in facts.items() if r["broken"]]
    columns = store.invoice_columns(("id", "validation", "meta"), ids=broken) if broken else []
    validations = {r["id"]: r["validation"] or {} for r in columns}
    audited = {i for i, r in facts.items() if r["q_audit"]} | {r["id"] for r in columns if _audit_pick(r["meta"])}
    titled = store.credit_note_ids(REVIEW)
    queue = store.list_invoices(REVIEW) if queue is None else queue
    if today is None:
        today = issues_today(store, [i["id"] for i in queue if not i["requires_review"]])
    candidates, left = [], {}
    for inv in queue:
        if inv["requires_review"]:
            continue
        if inv["id"] in today or inv["id"] in validations:
            issues = today[inv["id"]] if inv["id"] in today else validations[inv["id"]].get("issues") or []
            person = any(_needs_a_person(i.get("severity"), i.get("code")) for i in issues)
        else:
            person = bool((facts.get(inv["id"]) or {"q_person": False})["q_person"])
        if _credit_note(inv["id"] in titled, inv["grand_total"]):
            left[inv["id"]] = "credit note"
        elif inv["id"] in audited:
            left[inv["id"]] = "audit"
        elif person:
            left[inv["id"]] = "warning"
        else:
            candidates.append(inv)
    return candidates, left


def queue_facts(row: dict[str, Any]) -> dict[str, int]:
    """What ``sort_for_bulk`` reads of an invoice (its columns, the JSON ones decoded): picked for the audit sample,
    and a stored finding that needs a person. Kept in the invoices' summary (D-110)."""
    issues = (row["validation"] or {}).get("issues") or []
    return {"q_audit": int(_audit_pick(row["meta"])),
            "q_person": int(any(_needs_a_person(i.get("severity"), i.get("code")) for i in issues))}  # fmt: skip


def bulk_approve(
    store: Store, reference: ReferenceData, settings: Settings, invoice_ids: list[int], reviewer: str, login: str = ""
) -> dict[str, Any]:
    """Approve each invoice exactly as it is coded, if it is still clean. Returns approved / skipped.

    "As coded" is what the review screen shows: an earlier approver's corrections (a reopened or sent-back
    invoice) when there are some, otherwise the AI's coding."""
    with store.session():  # one connection for every invoice (a no-op inside the caller's own session)
        return _bulk_approve(store, reference, settings, invoice_ids, reviewer, login)


def _bulk_approve(
    store: Store, reference: ReferenceData, settings: Settings, invoice_ids: list[int], reviewer: str, login: str
) -> dict[str, Any]:
    approved: list[int] = []
    skipped: list[tuple[int, str]] = []
    stamp = store.checks_stamp()
    for invoice_id in invoice_ids:
        inv = store.get_invoice(invoice_id)
        if inv is None or inv["status"] != REVIEW or not inv.get("ai_output"):
            skipped.append((invoice_id, "no longer in the queue"))
            continue
        if _audit_pick(inv.get("meta")):
            skipped.append((invoice_id, AUDIT_PICK[:1].upper() + AUDIT_PICK[1:]))
            continue
        # What was found after processing and is kept only with the invoice (the Azure check's disagreement, the page
        # reader's line items or text layer read differently): finalise_coding below does not raise it again.
        validation = inv.get("validation") or {}
        found = validation.get("issues") or []
        if validation.get(CHECKS_STAMP_KEY) != stamp:  # checked against other settings (B-815)
            found = _kept(found)  # the coding's own checks are done again below, as they come out today
        stored = [i for i in found if _needs_a_person(i.get("severity"), i.get("code"))]
        if stored:
            skipped.append((invoice_id, _plain(stored[0].get("message"), stored[0].get("code"))))
            continue
        coded = inv.get("final_output") or inv["ai_output"]
        coded = {k: v for k, v in coded.items() if k != "gl_distribution"}
        try:
            coding = InvoiceCoding.model_validate(coded)
        except ValueError:
            skipped.append((invoice_id, "the coding needs fixing by hand"))
            continue
        if _credit_note(bool((store.get_capture(invoice_id) or {}).get("credit_note")), coding.grand_total):
            skipped.append((invoice_id, "it is a credit note: a credit is always applied by a person"))
            continue
        output, report = finalise_coding(coding, reference, settings, store=store, exclude_invoice_id=invoice_id,
                                         received=_received_on(inv))  # fmt: skip
        blocking = [i for i in report.issues if _needs_a_person(i.severity, i.code)]
        if blocking:
            skipped.append((invoice_id, _plain(blocking[0].message, blocking[0].code)))
            continue
        if report.requires_review:
            skipped.append((invoice_id, f"confidence {report.adjusted_confidence:.0%} is below the threshold"))
            continue
        try:
            store.approve_invoice(invoice_id, output, reviewer, bulk=True, login=login)
        except (KeyError, ValueError):
            # A colleague parked, rejected or approved it while the loop checked the others: skip this one only.
            skipped.append((invoice_id, "no longer in the queue (someone else acted on it meanwhile)"))
            continue
        # Nobody opened it: it teaches the template, but only a correction counts towards touchless processing.
        learn_from_approval(store, invoice_id, output, actor=reviewer, bulk=True)
        approved.append(invoice_id)
    return {"approved": approved, "skipped": skipped}
