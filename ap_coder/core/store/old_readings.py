"""The invoices read before D-89, scored again once per database (``reading_as_d89``, run by ``_base._migrate``):
kept apart from the foundation so it stays short enough to read in one go."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ._base import CHECKS_STAMP_KEY, PARKED, REVIEW


def reading_as_d89(conn: sqlite3.Connection) -> None:
    """Once per database (``settings.reading_d89``): the invoices still to approve that the local readers read
    before D-89 had a confidence multiplied by the share of lines coded; their ``confidence_score`` becomes the
    Reading of their stored capture. The queue's confidence and review flag follow, from their stored findings and a
    Coding worked out from their lines (``validation.score``; the accounts' lists are checked again in the queue:
    their checks stamp is cleared for ``bulk.issues_today``, D-88), so the queue is sorted and flagged by the new
    figures (B-895). One read by a chat model (or without a stored capture) keeps its own confidence as its Reading,
    its queue figure worked out again the same way (it showed the old one beside a lower Coding, B-956)."""
    if conn.execute("SELECT 1 FROM settings WHERE key = 'reading_d89'").fetchone():
        return
    from ...ai.offline_coder import MODEL_NAME
    from ...capture.confidence import reading_confidence
    from ...capture.types import FieldResult
    from .capture import capture_value

    rows = conn.execute(
        """SELECT i.id, i.ai_output, i.meta, i.validation, c.capture_json FROM invoices i LEFT JOIN
           invoice_capture c ON c.invoice_id = i.id WHERE i.status IN (?, ?) AND i.ai_output IS NOT NULL""",
        (REVIEW, PARKED),
    ).fetchall()
    for r in rows:
        try:
            output = json.loads(r["ai_output"])
            local = (json.loads(r["meta"] or "{}").get("inference") or {}).get("model") == MODEL_NAME
            if local and r["capture_json"]:
                fields = (capture_value(r["capture_json"]) or {}).get("fields") or {}
                reading = reading_confidence({k: FieldResult.from_dict(v) for k, v in fields.items()})
            else:  # read by a chat model or the demo: its own confidence
                reading, local = float(output.get("confidence_score") or 0.0), False
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        if local:
            output["confidence_score"] = reading
            conn.execute("UPDATE invoices SET ai_output = ?, model_confidence = ? WHERE id = ?",
                         (json.dumps(output), reading, r["id"]))  # fmt: skip
        validation = _rescored(output, r["validation"], reading)
        if validation is not None:
            conn.execute("UPDATE invoices SET validation = ?, adjusted_confidence = ?, requires_review = ? "
                         "WHERE id = ?", (json.dumps(validation, default=str), validation["adjusted_confidence"],
                                          int(validation["requires_review"]), r["id"]))  # fmt: skip
    conn.execute("INSERT INTO settings (key, value) VALUES ('reading_d89', '1') ON CONFLICT(key) DO NOTHING")


def _rescored(output: dict[str, Any], stored: str | None, reading: float) -> dict[str, Any] | None:
    """The stored checks ``stored`` (JSON) of an invoice coded ``output``, scored again with ``reading`` as its
    Reading (``reading_as_d89``): its findings as they were, the Coding from its lines without the accounts'
    lists (a line to an unknown account has its finding, which keeps it for a person anyway), and no checks stamp, so
    the queue checks it again. None when there are none, or they can't be read."""
    from ...coding.validation import line_coding_confidence, score
    from ...core.schema import InvoiceCoding

    try:
        validation = json.loads(stored) if stored else None
        if not isinstance(validation, dict):
            return None
        coding = InvoiceCoding.model_validate({k: v for k, v in output.items() if k != "gl_distribution"})
        findings = [(str(i.get("severity") or ""), str(i.get("code") or "")) for i in validation.get("issues") or []]
        threshold = float(validation.get("review_threshold") or 0.85)
    except (ValueError, TypeError, AttributeError):
        return None
    taxed = any(t.tax_amount for t in coding.tax_lines)
    sure = [line_coding_confidence(li, False, taxed) for li in coding.line_items]
    coded = round(sum(sure) / len(sure), 4) if sure else 0.0
    read, coded, adjusted, review = score(reading, coded, findings, threshold)
    # The AI reviewer's or the Azure check's disagreement keeps it for a person whatever the scores (B-956).
    review = review or any(code.startswith(("VERIFIER_", "AZURE_DI_")) for _, code in findings)
    validation.pop(CHECKS_STAMP_KEY, None)
    validation.pop("recheck", None)
    return {**validation, "model_confidence": reading, "reading_confidence": read, "coding_confidence": coded,
            "adjusted_confidence": adjusted, "requires_review": review}  # fmt: skip
