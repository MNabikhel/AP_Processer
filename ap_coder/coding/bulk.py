"""Bulk approval of clean invoices.

An invoice qualifies only if, re-checked right now (duplicates and history may have changed since it was
processed), it has no error and no warning, no finding that always goes to a person (``ALWAYS_A_PERSON``: some,
like a new vendor name, are only info), it is not a credit note (a credit is always applied by a person, as the
touchless rules say) and the AI's confidence is above the review threshold.
Everything else is skipped with the reason, and still needs a person.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..ai.pipeline import finalise_coding
from ..capture.supplier import ALWAYS_A_PERSON, touchless_gates
from ..capture.workflow import learn_from_approval
from ..core.config import Settings
from ..core.reference_data import ReferenceData
from ..core.schema import InvoiceCoding
from ..core.store import REVIEW, Store


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


def _needs_a_person(severity: Any, code: Any) -> bool:
    """An error, a warning, or a finding that always goes to a person whatever its severity."""
    return severity in ("error", "warning") or str(code or "") in ALWAYS_A_PERSON


def _credit_note(titled: bool, grand_total: Any) -> bool:
    """A total below zero, or titled a credit note by what capture read (whatever the sign of its total): the same
    notion as the touchless gates (``supplier.touchless_gates``)."""
    return bool(touchless_gates(grand_total=grand_total, credit_note=titled))


def clean_candidates(store: Store) -> list[dict[str, Any]]:
    """Invoices in the queue that were clean when processed (no review flag, no errors or warnings, nothing that
    always goes to a person, not a credit note)."""
    return sort_for_bulk(store)[0]


def sort_for_bulk(store: Store) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """(``clean_candidates``, why each other invoice of the queue not flagged when processed is left for a person:
    "credit note", or "warning" for an error, a warning or a finding that always goes to a person)."""
    validations = {r["id"]: r["validation"] or {} for r in store.invoice_columns(("id", "validation"), REVIEW)}
    titled = store.credit_note_ids(REVIEW)
    candidates, left = [], {}
    for inv in store.list_invoices(REVIEW):
        if inv["requires_review"]:
            continue
        issues = validations.get(inv["id"], {}).get("issues") or []
        if _credit_note(inv["id"] in titled, inv["grand_total"]):
            left[inv["id"]] = "credit note"
        elif any(_needs_a_person(i.get("severity"), i.get("code")) for i in issues):
            left[inv["id"]] = "warning"
        else:
            candidates.append(inv)
    return candidates, left


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
    for invoice_id in invoice_ids:
        inv = store.get_invoice(invoice_id)
        if inv is None or inv["status"] != REVIEW or not inv.get("ai_output"):
            skipped.append((invoice_id, "no longer in the queue"))
            continue
        # What was found after processing and is kept only with the invoice (the Azure check's disagreement, the page
        # reader's line items or text layer read differently): finalise_coding below does not raise it again.
        stored = [
            i
            for i in (inv.get("validation") or {}).get("issues") or []
            if _needs_a_person(i.get("severity"), i.get("code"))
        ]
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
