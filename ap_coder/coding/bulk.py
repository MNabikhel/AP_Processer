"""Bulk approval of clean invoices.

An invoice qualifies only if, re-checked right now (duplicates and history may have changed since it was
processed), it has no error and no warning, no finding that always goes to a person (``ALWAYS_A_PERSON``: some,
like a new vendor name, are only info), it is not a credit note (a credit is always applied by a person, as the
touchless rules say) and the AI's confidence is above the review threshold.
Everything else is skipped with the reason, and still needs a person.
"""

from __future__ import annotations

from typing import Any

from ..ai.pipeline import finalise_coding
from ..capture.supplier import ALWAYS_A_PERSON, touchless_gates
from ..capture.workflow import learn_from_approval
from ..core.config import Settings
from ..core.reference_data import ReferenceData
from ..core.schema import InvoiceCoding
from ..core.store import REVIEW, Store


def _needs_a_person(severity: Any, code: Any) -> bool:
    """An error, a warning, or a finding that always goes to a person whatever its severity."""
    return severity in ("error", "warning") or str(code or "") in ALWAYS_A_PERSON


def _credit_note(store: Store, invoice_id: int, grand_total: Any) -> bool:
    """A total below zero, or titled a credit note by what capture read (whatever the sign of its total): the same
    notion as the touchless gates (``supplier.touchless_gates``)."""
    titled = bool((store.get_capture(invoice_id) or {}).get("credit_note"))
    return bool(touchless_gates(grand_total=grand_total, credit_note=titled))


def clean_candidates(store: Store) -> list[dict[str, Any]]:
    """Invoices in the queue that were clean when processed (no review flag, no errors or warnings, nothing that
    always goes to a person, not a credit note)."""
    validations = {r["id"]: r["validation"] or {} for r in store.invoice_columns(("id", "validation"), REVIEW)}
    candidates = []
    for inv in store.list_invoices(REVIEW):
        if inv["requires_review"]:
            continue
        issues = validations.get(inv["id"], {}).get("issues") or []
        if any(_needs_a_person(i.get("severity"), i.get("code")) for i in issues):
            continue
        if not _credit_note(store, inv["id"], inv["grand_total"]):
            candidates.append(inv)
    return candidates


def bulk_approve(
    store: Store, reference: ReferenceData, settings: Settings, invoice_ids: list[int], reviewer: str, login: str = ""
) -> dict[str, Any]:
    """Approve each invoice exactly as it is coded, if it is still clean. Returns approved / skipped.

    "As coded" is what the review screen shows: an earlier approver's corrections (a reopened or sent-back
    invoice) when there are some, otherwise the AI's coding."""
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
            skipped.append((invoice_id, f"{stored[0].get('code')}: {stored[0].get('message')}"))
            continue
        coded = inv.get("final_output") or inv["ai_output"]
        coded = {k: v for k, v in coded.items() if k != "gl_distribution"}
        try:
            coding = InvoiceCoding.model_validate(coded)
        except ValueError:
            skipped.append((invoice_id, "the coding needs fixing by hand"))
            continue
        if _credit_note(store, invoice_id, coding.grand_total):
            skipped.append((invoice_id, "it is a credit note: a credit is always applied by a person"))
            continue
        output, report = finalise_coding(coding, reference, settings, store=store, exclude_invoice_id=invoice_id)
        blocking = [i for i in report.issues if _needs_a_person(i.severity, i.code)]
        if blocking:
            skipped.append((invoice_id, f"{blocking[0].code}: {blocking[0].message}"))
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
