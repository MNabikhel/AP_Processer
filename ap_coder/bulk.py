"""Bulk approval of clean invoices.

An invoice qualifies only if, re-checked right now (duplicates and history may have changed since it was
processed), it has no error and no warning and the AI's confidence is above the review threshold.
Everything else is skipped with the reason, and still needs a person.
"""

from __future__ import annotations

from typing import Any

from .config import Settings
from .pipeline import finalise_coding
from .reference_data import ReferenceData
from .schema import InvoiceCoding
from .store import REVIEW, Store


def clean_candidates(store: Store) -> list[dict[str, Any]]:
    """Invoices in the queue that were clean when processed (no review flag, no errors or warnings)."""
    candidates = []
    for inv in store.list_invoices(REVIEW):
        if inv["requires_review"]:
            continue
        full = store.get_invoice(inv["id"]) or {}
        issues = (full.get("validation") or {}).get("issues") or []
        if not any(i.get("severity") in ("error", "warning") for i in issues):
            candidates.append(inv)
    return candidates


def bulk_approve(
    store: Store, reference: ReferenceData, settings: Settings, invoice_ids: list[int], reviewer: str
) -> dict[str, Any]:
    """Approve each invoice exactly as the AI coded it, if it is still clean. Returns approved / skipped."""
    approved: list[int] = []
    skipped: list[tuple[int, str]] = []
    for invoice_id in invoice_ids:
        inv = store.get_invoice(invoice_id)
        if inv is None or inv["status"] != REVIEW or not inv.get("ai_output"):
            skipped.append((invoice_id, "no longer in the queue"))
            continue
        ai = {k: v for k, v in inv["ai_output"].items() if k != "gl_distribution"}
        try:
            coding = InvoiceCoding.model_validate(ai)
        except ValueError:
            skipped.append((invoice_id, "the AI output needs fixing by hand"))
            continue
        output, report = finalise_coding(coding, reference, settings, store=store, exclude_invoice_id=invoice_id)
        blocking = [i for i in report.issues if i.severity in ("error", "warning")]
        if blocking:
            skipped.append((invoice_id, f"{blocking[0].code}: {blocking[0].message}"))
            continue
        if report.requires_review:
            skipped.append((invoice_id, f"confidence {report.adjusted_confidence:.0%} is below the threshold"))
            continue
        store.approve_invoice(invoice_id, output, reviewer, bulk=True)
        approved.append(invoice_id)
    return {"approved": approved, "skipped": skipped}
