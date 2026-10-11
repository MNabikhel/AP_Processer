# ruff: noqa: E501
"""Capture inside AP Coder's workflow: read every invoice, route touchless invoices, learn on approval.

* ``capture_invoice``: run every reader on a processed invoice (rule reader, the supplier's learned
  template, Document Intelligence, the AI's answer located on the page) and the checks.
* ``autonomy_decision``: approve without a person only when touchless processing is on (one switch for the
  company), the supplier has earned it (one bar for every supplier, reached by itself), nothing on the invoice
  always needs a person (``touchless_gates``: bank account changed, possible duplicate, unusual amount, over the
  touchless or approval limit, credit note, vendor not in the master, not read by every reader), every printed
  header field is verified and every check passes; a deterministic sample still goes to a person (audit).
* ``learn_from_approval``: what AP approved trains the supplier's template and counts towards its
  accuracy; a correction on an autonomous supplier suspends it at once. ``taught_message`` says what was learned.
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
    READY,
    SUPERVISED,
    autonomy_status,
    clean_invoices_to_go,
    confirmed_values,
    judged_stats,
    learn,
    outcome_rows,
    should_auto_approve,
    touchless_gates,
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


WAITING_FOR_PAGE_READER = "waiting for OvisOCR2 to read it"
TOUCHLESS_OFF = "touchless processing is off: every invoice is reviewed"


def _amounts(capture: CaptureResult | None, output: dict[str, Any] | None) -> dict[str, Any]:
    """{"grand_total", "currency"} of the invoice: the coding's (what an approval posts), else what capture read
    (the same values on any invoice that can go touchless: every printed field verified against the coding)."""
    coding = {k: (output or {}).get(k) for k in ("grand_total", "currency")}
    fields = capture.fields if capture is not None else {}
    for name in ("grand_total", "currency"):
        if coding[name] in (None, "") and fields.get(name) is not None:
            coding[name] = fields[name].value
    coding["currency"] = coding["currency"] or "CAD"  # no currency printed: CAD, as the vendor checks take it
    return coding


def invoice_gates(store: Any, capture: CaptureResult | None, report: Any, output: dict[str, Any] | None = None, *,
                  awaiting_page_reader: bool = False) -> list[str]:  # fmt: skip
    """Why this invoice always goes to a person, whatever its supplier's record (``supplier.touchless_gates``):
    its findings, a credit note, a total over the touchless limit (Settings → Automation) or over the approval
    limit (a second approver), and not yet read by the page reader."""
    issues = [{"code": i.code, "severity": i.severity} for i in (report.issues if report else [])]
    coding = _amounts(capture, output)
    over_limit = store.touchless_limit() if store.over_touchless_limit(coding) else None
    return touchless_gates(issues, grand_total=coding["grand_total"], over_limit=over_limit,
                           over_approval_limit=store.over_approval_limit(coding),
                           awaiting_page_reader=awaiting_page_reader)  # fmt: skip


def autonomy_decision(store: Any, key: str, profile: dict[str, Any] | None, capture: CaptureResult | None,
                      report: Any, path: str | Path, *, awaiting_page_reader: bool = False,
                      output: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """{"state", "auto": bool, "audit": bool, "reason"} for one processed invoice. ``awaiting_page_reader``: the
    page reader (OvisOCR2) has not read this invoice yet, so it is never approved without a person now: every
    invoice is read by every reader before anything is decided for it. ``output``: the coding an approval would
    post (its total and currency; what capture read otherwise).

    Nothing is approved without a person while touchless processing is off. With it on, a supplier that meets the
    bar is made touchless first (``Store.sync_autonomy``), then the invoice must pass every gate that always needs
    a person and ``should_auto_approve``; a share of those that pass still goes to a person (the audit sample)."""
    if store is None or not key:
        return {"state": "", "auto": False, "audit": False, "reason": "no supplier"}
    policy = store.autonomy_policy()
    on = store.touchless_enabled()
    if on:
        store.sync_autonomy(key)  # a supplier that reached the bar since its last approval goes touchless now
        profile = store.get_supplier_profile(key) or profile
    stored = (profile or {}).get("state") or SUPERVISED
    state, _, _ = autonomy_status(store.supplier_stats(key, policy.window), policy, stored,
                                  (profile or {}).get("autonomous_since"), touchless_on=on,
                                  suspended_at=(profile or {}).get("suspended_at"))  # fmt: skip
    if not on:
        would = state == READY  # the supplier meets the bar: it would be touchless with the switch on
        return {"state": state, "auto": False, "audit": False, "reason": TOUCHLESS_OFF if would else ""}
    if state != AUTONOMOUS:
        return {"state": state, "auto": False, "audit": False, "reason": ""}
    if awaiting_page_reader:
        return {"state": state, "auto": False, "audit": False, "reason": WAITING_FOR_PAGE_READER}
    gates = invoice_gates(store, capture, report, output)
    if gates:
        return {"state": state, "auto": False, "audit": False, "reason": "always a person: " + "; ".join(gates)}
    issues = [{"code": i.code, "severity": i.severity} for i in (report.issues if report else [])]
    ok, reason = should_auto_approve(state, capture, checks_ok=not (report and report.errors), issues=issues)
    audit = ok and audit_pick(path, policy.audit_rate)  # the same audit share for every supplier
    if audit:
        reason = "picked for the audit sample"
    return {"state": state, "auto": ok and not audit, "audit": audit, "reason": reason}


NOT_READERS = ("computed", "vendor master")  # a field's sources that did not read the page


def reader_outcome_rows(capture: CaptureResult | dict[str, Any] | None, final: dict[str, Any]) -> list[dict[str, Any]]:
    """Each reader against what AP approved: per header field AP approved, one row per reader with what it read
    ({reader, field, read_value, final_value, correct, layout_source}), plus reader "fused": the value AP saw,
    with its evidence key and status. ``correct``: the same value once normalized (``normalize.same_value``).

    A reader whose own value lost to another is scored on that value (the ``other:<value>`` sources); one that
    only offered the winning value as a runner-up counts as having read it. Fields AP left blank are not
    scored: blank may mean not printed, or not needed."""
    from .normalize import same_value
    from .supplier import header_values

    if not capture:
        return []
    if isinstance(capture, dict):
        capture = CaptureResult.from_dict(capture)
    rows: list[dict[str, Any]] = []
    for field, approved in header_values(final).items():
        fr = capture.fields.get(field)
        if approved in (None, "") or fr is None or fr.value in (None, ""):
            continue
        base = {"field": field, "final_value": approved, "layout_source": capture.layout_source}
        scored: set[str] = set()
        for reader, raw in fr.sources.items():  # the readers behind the value AP saw
            if reader.startswith("other:") or reader in NOT_READERS:
                continue
            scored.add(reader)
            # They all read that value; the raw text decides when the vendor master's name replaced theirs.
            right = same_value(field, fr.value, approved) or same_value(field, raw, approved)
            rows.append({**base, "reader": reader, "read_value": raw, "correct": right})
        for other, readers in fr.sources.items():  # readers that read something else
            if not other.startswith("other:"):
                continue
            value = other[len("other:") :]
            for reader in (r.strip() for r in str(readers).split(",")):
                if reader and reader not in scored and reader not in NOT_READERS:
                    scored.add(reader)
                    rows.append({**base, "reader": reader, "read_value": value,
                                 "correct": same_value(field, value, approved)})  # fmt: skip
        rows.append({**base, "reader": "fused", "read_value": fr.value, "correct": same_value(field, fr.value, approved),
                     "evidence": fr.evidence, "status": fr.status})  # fmt: skip
    return rows


def learn_from_approval(store: Any, invoice_id: int, final: dict[str, Any], *, actor: str = "",
                        taught: dict[str, list[Box]] | None = None, bulk: bool = False) -> dict[str, Any]:  # fmt: skip
    """After a person approves: score each reader against what AP approved (the Learning page's Readers tab and
    the local calibration of confidence), count what was corrected, field by field, towards the supplier's accuracy,
    and teach the supplier's template where each confirmed value sits on the page.

    ``bulk``: approved in bulk, without anyone opening it. Its corrections (made earlier, on a reopened or sent-back
    invoice) still count, but an untouched bulk approval does not count as a clean invoice towards touchless
    processing: nobody checked its fields. Returns what was learned ({"supplier", "name", "fields", "corrected":
    [fields], "counted", "template"}), for ``taught_message``."""
    learned: dict[str, Any] = {"supplier": "", "name": "", "fields": 0, "corrected": [], "counted": False,
                               "template": False}  # fmt: skip
    inv = store.get_invoice(invoice_id)
    if inv is None:
        return learned
    try:
        store.record_reader_outcomes(invoice_id, reader_outcome_rows(store.get_capture(invoice_id), final))
    except Exception as exc:  # learning must never block an approval
        log.warning("invoice %s: reader outcomes not recorded (%s)", invoice_id, exc)
    name = final.get("vendor_name") or ""
    key = store.supplier_key_for(name, final.get("gst_hst_registration_number"))
    if not key:
        return learned
    learned.update(supplier=key, name=name)
    source = "audit" if ((inv.get("meta") or {}).get("capture") or {}).get("audit") else "review"
    rows = outcome_rows(inv.get("ai_output") or {}, final)
    learned.update(fields=len(rows), corrected=[r["field"] for r in rows if not r["correct"]])
    if not bulk or learned["corrected"]:
        try:
            store.record_outcomes(key, invoice_id, rows, source=source, display_name=name)
            learned["counted"] = True
        except Exception as exc:  # learning must never block an approval
            log.warning("invoice %s: outcomes not recorded (%s)", invoice_id, exc)
    path = Path(inv.get("source_path") or "")
    if path.suffix.lower() in TEXT_EXTENSIONS or not path.exists():
        return learned
    try:
        layout = build_layout(path)
        profile = store.get_supplier_profile(key) or {}
        template = learn(profile.get("template") or None, layout, confirmed_values(final, taught))
        store.save_supplier_profile(key, display_name=name, template=template, actor=actor or None)
        learned["template"] = True
    except Exception as exc:
        log.warning("invoice %s: supplier template not updated (%s)", invoice_id, exc)
    return learned


FIELD_WORDS = {
    "vendor_name": "vendor", "invoice_number": "invoice number", "invoice_date": "invoice date",
    "due_date": "due date", "po_number": "PO number", "currency": "currency",
    "gst_hst_registration_number": "GST/HST number", "qst_registration_number": "QST number", "subtotal": "subtotal",
    "gst_amount": "GST", "hst_amount": "HST", "pst_amount": "PST", "qst_amount": "QST", "tax_total": "tax total",
    "grand_total": "total", "payment_terms": "terms",
}  # fmt: skip


def supplier_progress(store: Any, key: str) -> tuple[str, int, str]:
    """(state, invoices reviewed, plain English: where it stands on the way to touchless processing)."""
    from .supplier import HELD, LEARNING, SUSPENDED, to_go_text

    policy = store.autonomy_policy()
    profile = store.get_supplier_profile(key) or {}
    stats = store.supplier_stats(key, policy.window)
    on = store.touchless_enabled()
    stored = profile.get("state") or SUPERVISED
    state, _, _ = autonomy_status(stats, policy, stored, profile.get("autonomous_since"), touchless_on=on,
                                  suspended_at=profile.get("suspended_at"))  # fmt: skip
    if state == AUTONOMOUS:
        where = "touchless"
    elif state == HELD:
        where = "kept supervised by a manager"
    elif state == READY:
        where = "meets the bar to go touchless" + ("" if on else " (touchless processing is off)")
    else:
        to_go = clean_invoices_to_go(judged_stats(stats, stored, profile.get("suspended_at")), policy)
        where = to_go_text(to_go) + (" again" if state == SUSPENDED and to_go else "")
        if state == LEARNING and not stats.invoices:
            where = "nothing reviewed yet"
    return state, stats.invoices, where


def taught_message(store: Any, learned: dict[str, Any]) -> str:
    """What an approval taught, in plain words: "Learned from your 2 corrections (invoice number, due date). Acme
    Ltd: 14 invoices reviewed, about 6 more clean invoices to go touchless." "" when no supplier was found."""
    from ..ui import plural

    key = learned.get("supplier")
    if not key:
        return ""
    corrected = [FIELD_WORDS.get(f, f.replace("_", " ")) for f in learned.get("corrected") or []]
    if corrected:
        head = f"Learned from your {plural(len(corrected), 'correction')} ({', '.join(corrected)})."
    elif learned.get("fields"):
        head = "Every header field was read right."
    else:
        head = "Learned this vendor's layout." if learned.get("template") else ""
    try:
        _, invoices, where = supplier_progress(store, key)
    except Exception as exc:  # the message must never block an approval
        log.warning("supplier %s: progress not shown (%s)", key, exc)
        return head
    name = (learned.get("name") or key).rstrip(".")
    return f"{head} {name}: {plural(invoices, 'invoice')} reviewed, {where}.".strip()


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
