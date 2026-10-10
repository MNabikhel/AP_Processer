# ruff: noqa: E501
"""Capture inside AP Coder's workflow: read every invoice, route touchless invoices, learn on approval.

* ``capture_invoice``: run every reader on a processed invoice (rule reader, the supplier's learned
  template, Document Intelligence, the AI's answer located on the page) and the checks.
* ``route``: approve without a person only when the supplier is autonomous, every printed header
  field is verified and every check passes; a deterministic sample still goes to a person (audit).
* ``learn_from_approval``: what AP approved trains the supplier's template and counts towards its
  accuracy; a correction on an autonomous supplier suspends it at once.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path
from typing import Any

from ..extraction import TEXT_EXTENSIONS
from . import analyze, build_layout
from .bridge import ai_values, vendor_record
from .supplier import (
    AUTONOMOUS,
    SUPERVISED,
    autonomy_status,
    confirmed_values,
    learn,
    outcome_rows,
    should_auto_approve,
)
from .types import Box, CaptureResult

log = logging.getLogger(__name__)

AUTONOMOUS_REVIEWER = "AP Coder (autonomous)"


def supplier_for(store: Any, output: dict[str, Any] | None) -> tuple[str, dict[str, Any] | None]:
    if store is None or not output:
        return "", None
    name = output.get("vendor_name") or ""
    key = store.supplier_key_for(name, output.get("gst_hst_registration_number"))
    return key, (store.get_supplier_profile(key) if key else None)


def local_evidence(store: Any) -> dict[str, tuple[int, int]] | None:
    """{evidence: (cases, right)}: how often each evidence pattern was right on the invoices AP approved here
    (``store.evidence_counts``), for the local calibration of confidence. None without a store, or with a store
    that does not count them."""
    counts = getattr(store, "evidence_counts", None) if store is not None else None
    if counts is None:
        return None
    try:
        return dict(counts() or {})
    except Exception as exc:  # the benchmark's calibration stands alone
        log.warning("local calibration not used (%s)", exc)
        return None


def capture_invoice(path: str | Path, output: dict[str, Any] | None, *, store: Any = None,
                    di_raw: dict[str, Any] | None = None,
                    page_text: list[str] | None = None) -> tuple[CaptureResult | None, str, dict[str, Any] | None]:  # fmt: skip
    """(capture, supplier key, supplier profile). None for text files or when the file cannot be read.
    ``page_text``: the page reader's transcription of each page, when it has read them."""
    path = Path(path)
    if path.suffix.lower() in TEXT_EXTENSIONS or not path.exists():
        return None, "", None
    key, profile = supplier_for(store, output)
    template = (profile or {}).get("template") or None
    vendor = vendor_record(store, (output or {}).get("vendor_name"))
    try:
        # Processed the day it arrives: that date also settles a 03/04/2026 the page leaves open.
        capture = analyze(path, di_raw=di_raw, ai_values=ai_values(output), template=template, vendor=vendor,
                          today=dt.date.today(), page_text=page_text, local_evidence=local_evidence(store))  # fmt: skip
    except Exception as exc:  # capture is an extra check: never lose an invoice over it
        log.warning("%s: capture failed (%s)", path.name, exc)
        return None, key, profile
    return capture, key, profile


def audit_pick(path: str | Path, rate: float) -> bool:
    """Whether a person audits this autonomous invoice (deterministic on the file's content)."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    try:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        digest = hashlib.sha256(str(path).encode()).hexdigest()
    return int(digest[:15], 16) / float(16**15) < rate


def autonomy_decision(store: Any, key: str, profile: dict[str, Any] | None, capture: CaptureResult | None,
                      report: Any, path: str | Path) -> dict[str, Any]:  # fmt: skip
    """{"state", "auto": bool, "audit": bool, "reason"} for one processed invoice."""
    if store is None or not key:
        return {"state": "", "auto": False, "audit": False, "reason": "no supplier"}
    policy = store.autonomy_policy()
    stored = (profile or {}).get("state") or SUPERVISED
    state, _, _ = autonomy_status(store.supplier_stats(key), policy, stored, (profile or {}).get("autonomous_since"))
    if state != AUTONOMOUS:
        return {"state": state, "auto": False, "audit": False, "reason": ""}
    issues = [{"code": i.code, "severity": i.severity} for i in (report.issues if report else [])]
    ok, reason = should_auto_approve(state, capture, checks_ok=not (report and report.errors), issues=issues)
    rate = (profile or {}).get("audit_rate")
    audit = ok and audit_pick(path, policy.audit_rate if rate is None else float(rate))
    if audit:
        reason = "picked for the audit sample"
    return {"state": state, "auto": ok and not audit, "audit": audit, "reason": reason}


def learn_from_approval(store: Any, invoice_id: int, final: dict[str, Any], *, actor: str = "",
                        taught: dict[str, list[Box]] | None = None) -> None:  # fmt: skip
    """After a person approves: count what was corrected towards the supplier's accuracy, and teach the
    supplier's template where each confirmed value sits on the page."""
    inv = store.get_invoice(invoice_id)
    if inv is None:
        return
    name = final.get("vendor_name") or ""
    key = store.supplier_key_for(name, final.get("gst_hst_registration_number"))
    if not key:
        return
    source = "audit" if ((inv.get("meta") or {}).get("capture") or {}).get("audit") else "review"
    proposed = inv.get("ai_output") or {}
    try:
        store.record_outcomes(key, invoice_id, outcome_rows(proposed, final), source=source, display_name=name)
    except Exception as exc:  # learning must never block an approval
        log.warning("invoice %s: outcomes not recorded (%s)", invoice_id, exc)
    path = Path(inv.get("source_path") or "")
    if path.suffix.lower() in TEXT_EXTENSIONS or not path.exists():
        return
    try:
        layout = build_layout(path)
        profile = store.get_supplier_profile(key) or {}
        template = learn(profile.get("template") or None, layout, confirmed_values(final, taught))
        store.save_supplier_profile(key, display_name=name, template=template, actor=actor or None)
    except Exception as exc:
        log.warning("invoice %s: supplier template not updated (%s)", invoice_id, exc)


def on_reopen(store: Any, invoice_id: int, actor: str, reason: str = "") -> None:
    """Reopening an invoice approved without a person means the autonomous run got something wrong:
    the supplier goes back to supervised review."""
    inv = store.get_invoice(invoice_id)
    if not inv or inv.get("reviewer") != AUTONOMOUS_REVIEWER:
        return
    final = inv.get("final_output") or {}
    key = store.supplier_key_for(final.get("vendor_name") or "", final.get("gst_hst_registration_number"))
    if key:
        store.set_supplier_state(key, "suspended", actor or "AP Coder",
                                 reason=f"an invoice approved without review was reopened: {reason}".strip(": "))  # fmt: skip
