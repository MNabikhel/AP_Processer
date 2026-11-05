"""Bulk approval of clean invoices.

The queue says which invoices look clean by today's checks: those made against other settings (GL accounts, cost
centers, tax, JD Edwards) are done again (``issues_today``, B-815). An invoice qualifies only if, re-checked right now
(duplicates and history may have changed since it was processed), it has no error and no warning, no finding that
always goes to a person (``ALWAYS_A_PERSON``: some, like a new vendor name, are only info), it is not a credit note (a
credit is always applied by a person, as the touchless rules say) and the AI's confidence is above the review
threshold.
Everything else is skipped with the reason, and still needs a person.

Entry points: ``sort_for_bulk`` (the queue's clean invoices, and why each other is left), ``queue_checks`` (what the
queue reads of its invoices, from the invoices' summary), ``recheck_queue`` and ``QueueRecheck`` (the queue checked
again after a change of settings, in the background), ``issues_today`` and ``bulk_approve``.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any

from ..ai.pipeline import finalise_coding
from ..capture.supplier import ALWAYS_A_PERSON, touchless_gates
from ..capture.workflow import learn_from_approval
from ..core.config import Settings
from ..core.memory import vendor_key
from ..core.reference_data import ReferenceData
from ..core.schema import InvoiceCoding
from ..core.store import REVIEW, Store
from ..core.store._base import CHECKS_STAMP_KEY, _in_list
from ..core.store.summary import computed
from .review import codes_to_pick
from .vendors import moved_off_hold

log = logging.getLogger(__name__)


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
    found = []  # the re-checks made here, kept in one write
    for row in rows:
        seen = row["validation"]
        validation = json.loads(seen) if seen else {}
        stored = validation.get("issues") or []
        recheck = validation.get("recheck") or {}
        if recheck.get("stamp") == stamp:
            today = recheck.get("issues")  # None: its coding can't be read, the stored checks stand
        else:
            if reference is None:
                try:
                    reference = store.reference_data()
                except Exception:  # noqa: BLE001 - no reference data yet: the stored checks stand
                    break
            today = _recheck(store, reference, settings or Settings(), _decoded(row))
            found.append((row["id"], stamp, today, seen))
        if today is not None:
            out[row["id"]] = today + [i for i in _kept(stored) if i.get("code") not in {t.get("code") for t in today}]
    store.save_rechecks(found)
    return out


def _decoded(row: dict[str, Any]) -> dict[str, Any]:
    """An invoice's columns as read (``invoice_columns(..., decode=False)``), its coding decoded."""
    return {**row, "ai_output": json.loads(row["ai_output"]) if row["ai_output"] else None,
            "final_output": json.loads(row["final_output"]) if row["final_output"] else None}  # fmt: skip


# --- The queue's checks, from the invoices' summary (D-155) ----------------------------------------------------------
# The queue read the coding and checks of every invoice waiting (their JSON text, twice: about 86 MB a load with 2,700
# waiting), and the first load after a change of settings checked every one of them again (26 s with 2,700). The
# summary keeps what the queue reads of each (``queue_facts``); the re-check is done in the background, in batches,
# one process at a time (``recheck_queue``, ``QueueRecheck``).

RECHECK_LEASE = "queue-recheck"  # the lease (``Store.take_lease``) of the one process checking the queue again
LEASE_SECONDS = 120.0  # a lease not renewed for this long was left by a process that stopped
RECHECK_BATCH = 50  # invoices checked again, then kept in one short write
RECHECK_WAIT = 300.0  # how long a laptop's queue waits for the re-check under way (its own background thread's)
QUEUE_COLUMNS = "id, broken, q_audit, q_stamp, q_found, r_stamp, r_found, q_pick, q_pick_cc, q_pay"


def _pairs(issues: list[dict[str, Any]]) -> str:
    """The findings' severity and code, all the queue reads of them, as the summary keeps them."""
    return json.dumps([[i.get("severity"), i.get("code")] for i in issues], separators=(",", ":"))


def _stale(row: dict[str, Any], stamp: str) -> bool:
    """Its stored checks and its queue re-check were both made against other settings than ``stamp``."""
    return row["q_stamp"] is not None and row["q_stamp"] != stamp and row["r_stamp"] != stamp


def _today(rows: list[dict[str, Any]], stamp: str) -> list[list[dict[str, Any]]]:
    """Each invoice's findings by today's checks (severity and code) from its queue facts, as ``issues_today`` gives
    them: the stored ones, or the queue's re-check with the stored findings made after processing (``_kept``). One
    not checked again yet (``_stale``): its latest. The findings of all are decoded in one go."""
    stored = json.loads("[" + ",".join(r["q_found"] or "[]" for r in rows) + "]")
    rechecked = json.loads("[" + ",".join(r["r_found"] or "null" for r in rows) + "]")
    out = []
    for row, kept, again in zip(rows, stored, rechecked, strict=True):
        found = [{"severity": severity, "code": code} for severity, code in kept]
        if row["q_stamp"] is not None and row["q_stamp"] != stamp and again is not None:
            today = [{"severity": severity, "code": code} for severity, code in again]
            codes = {t["code"] for t in today}
            found = today + [i for i in _kept(found) if i["code"] not in codes]
        out.append(found)  # (else no re-check, or its coding can't be read: the stored checks stand)
    return out


def _queue_rows(store: Store, ids: list[int]) -> dict[int, dict[str, Any]]:
    rows = store.summary_rows(f"status = ? AND {_in_list('id')}", (REVIEW, json.dumps([int(i) for i in ids])),
                              QUEUE_COLUMNS)  # fmt: skip
    return {r["id"]: computed(store, r, "queue") for r in rows}  # one the summary could not read: read again


def queue_checks(
    store: Store, ids: list[int], settings: Settings | None = None, recheck: bool | None = None
) -> tuple[dict[int, dict[str, Any]], int]:
    """({id: what the queue reads of it} for the invoices of ``ids`` in review: its facts from the invoices' summary
    (``queue_facts``), and ``found``, its findings by today's checks (severity and code); how many of them still show
    earlier checks). The checks are today's (B-815): with ``recheck`` (the laptop: by default unless AP Coder runs as
    the company server), an invoice whose checks were made against other settings is checked again first, waiting
    for a re-check under way; without it (the server's background process does it, ``QueueRecheck``), it shows its
    earlier checks meanwhile."""
    if not ids:
        return {}, 0
    rows = _queue_rows(store, ids)
    stamp = store.checks_stamp()
    if recheck is None:
        from ..ops.offline import server_mode

        recheck = not server_mode()
    if recheck and any(_stale(r, stamp) for r in rows.values()):
        recheck_queue(store, settings, ids=[i for i, r in rows.items() if _stale(r, stamp)], wait=RECHECK_WAIT)
        rows, stamp = _queue_rows(store, ids), store.checks_stamp()
    found = _today(list(rows.values()), stamp)
    checks = {i: {**r, "found": f} for (i, r), f in zip(rows.items(), found, strict=True)}
    return checks, sum(_stale(r, stamp) for r in rows.values())


def recheck_queue(store: Store, settings: Settings | None = None, *, ids: list[int] | None = None,
                  wait: float = 0.0) -> int | None:  # fmt: skip
    """Check again each invoice in review (only ``ids``' when given) whose checks were made against other settings
    than today's, as ``issues_today`` does, keeping each batch of ``RECHECK_BATCH`` in one short write. One process at
    a time: a lease in the database, waited for up to ``wait`` seconds. How many were checked; None when another
    process held the lease all that time."""
    owner = f"{os.getpid()}:{threading.get_ident()}"
    deadline = time.monotonic() + wait
    while not store.take_lease(RECHECK_LEASE, owner, LEASE_SECONDS):
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.2)
    try:
        with store.session():  # one connection for every invoice checked again
            return _recheck_queue(store, settings or Settings(), ids, owner)
    finally:
        store.drop_lease(RECHECK_LEASE, owner)


def _recheck_queue(store: Store, settings: Settings, ids: list[int] | None, owner: str) -> int:
    done, stamp, tried = 0, "", set()
    while True:
        now = store.checks_stamp()
        if now != stamp:  # (changed again meanwhile: what is left is checked against the new settings)
            stamp, tried = now, set()
        todo = [i for i in store.stale_checks(stamp, ids) if i not in tried]
        if not todo:
            return done
        try:
            reference = store.reference_data()
        except ValueError:  # no GL accounts any more: the stored checks stand
            return done
        for start in range(0, len(todo), RECHECK_BATCH):
            if start and store.checks_stamp() != stamp:
                break
            batch = todo[start : start + RECHECK_BATCH]
            done += _recheck_batch(store, batch, stamp, reference, settings)
            tried.update(batch)
            if not store.renew_lease(RECHECK_LEASE, owner, LEASE_SECONDS):
                return done  # taken over (this one was taken for stopped): the other one goes on


def _recheck_batch(store: Store, ids: list[int], stamp: str, reference: ReferenceData, settings: Settings) -> int:
    """Check the invoices ``ids`` again, those still in review and not checked against ``stamp`` meanwhile, and keep
    what was found in one write: how many."""
    found = []
    for row in store.invoice_columns(("id", "status", "validation", "ai_output", "final_output", "created_at"),
                                     ids=ids, decode=False):  # fmt: skip
        seen = row["validation"]
        try:
            validation = json.loads(seen) if seen and row["status"] == REVIEW else None
            if not isinstance(validation, dict) or stamp in (
                validation.get(CHECKS_STAMP_KEY),
                (validation.get("recheck") or {}).get("stamp"),
            ):
                continue
            today = _recheck(store, reference, settings, _decoded(row))
        except sqlite3.OperationalError:  # the database busy: the next look
            continue
        except Exception:  # noqa: BLE001 - kept as its coding can't be read: its stored checks stand
            log.warning("invoice %s could not be checked again: its stored checks stand", row["id"], exc_info=True)
            today = None
        found.append((row["id"], stamp, today, seen))
    store.save_rechecks(found)
    return len(found)


class QueueRecheck:
    """The queue checked again after a change of settings (``recheck_queue``), in the background: a look every
    ``every`` seconds (a few small queries when nothing changed). In the server's background process, or the
    laptop's dashboard (``web.app.start_background_work``)."""

    def __init__(self, settings_factory: Any, store_factory: Any, every: float = 5.0) -> None:
        self.settings_factory, self.store_factory, self.every = settings_factory, store_factory, every
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="ap-coder-queue-recheck", daemon=True)

    def start(self) -> QueueRecheck:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stopping.set()

    def _run(self) -> None:
        while not self.stopping.wait(self.every):
            try:
                store = self.store_factory()
                if not store.restoring() and store.stale_checks(store.checks_stamp()):
                    recheck_queue(store, self.settings_factory())
            except Exception:  # noqa: BLE001 - never stop the dashboard
                log.warning("the queue's re-check failed", exc_info=True)


def clean_candidates(store: Store) -> list[dict[str, Any]]:
    """Invoices in the queue that were clean when processed (no review flag, no errors or warnings, nothing that
    always goes to a person, not a credit note)."""
    return sort_for_bulk(store)[0]


def sort_for_bulk(
    store: Store, queue: list[dict[str, Any]] | None = None, checks: dict[int, dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """(``clean_candidates``, why each other invoice of the queue not flagged when processed is left for a person:
    "credit note", "audit" for one picked for the touchless audit sample, or "warning" for an error, a warning or a
    finding that always goes to a person). ``queue``: the
    invoices in review as ``store.list_invoices`` gives them, when the caller has them already. ``checks``:
    ``queue_checks`` of the queue, when the caller has it already. The checks are today's: an invoice whose stored
    checks were made against other settings is checked again (B-815)."""
    queue = store.list_invoices(REVIEW) if queue is None else queue
    if checks is None:
        checks = queue_checks(store, [i["id"] for i in queue if not i["requires_review"]])[0]
    titled = store.credit_note_ids(REVIEW)
    candidates, left = [], {}
    for inv in queue:
        if inv["requires_review"]:
            continue
        facts = checks.get(inv["id"]) or {"q_audit": 0, "found": []}
        if _credit_note(inv["id"] in titled, inv["grand_total"]):
            left[inv["id"]] = "credit note"
        elif facts["q_audit"]:
            left[inv["id"]] = "audit"
        elif any(needs_a_person(i["severity"], i["code"]) for i in facts["found"]):
            left[inv["id"]] = "warning"
        else:
            candidates.append(inv)
    return candidates, left


def queue_facts(row: Any) -> dict[str, Any]:
    """What the queue reads of an invoice (its columns, the JSON ones decoded), kept in the invoices' summary (D-110,
    D-155): picked for the audit sample; of one in review, also the stamps of its stored checks ("", None without
    stored checks) and of its queue re-check, their findings (``_pairs``; None for a re-check whose coding could not
    be read), what its lines still need (``codes_to_pick``, without and with cost centers) and what its opening Pay
    from account is chosen from (``banks.opening_choice``: the account, approved before or not, the currency, and the
    vendor whose last account it is)."""
    validation = row["validation"] or {}
    issues = validation.get("issues") or []
    facts: dict[str, Any] = {"q_audit": int(_audit_pick(row["meta"]))}
    if row["status"] != REVIEW:
        return facts
    recheck = validation.get("recheck") or {}
    doc = row["final_output"] or row["ai_output"] or {}
    lines = doc.get("line_items")
    pay = [str(doc.get("pay_from_account") or ""), bool(row["final_output"]), str(doc.get("currency") or "CAD"),
           vendor_key(doc.get("vendor_name") or row["vendor_name"] or "")]  # fmt: skip
    return {**facts, "q_stamp": None if row["validation"] is None else str(validation.get(CHECKS_STAMP_KEY) or ""),
            "q_found": _pairs(issues), "r_stamp": str(recheck.get("stamp") or ""),
            "r_found": None if recheck.get("issues") is None else _pairs(recheck["issues"]),
            "q_pick": codes_to_pick(lines, False, hint=False).removesuffix(" to approve"),
            "q_pick_cc": codes_to_pick(lines, True, hint=False).removesuffix(" to approve"),
            "q_pay": json.dumps(pay, separators=(",", ":"))}  # fmt: skip


def bulk_approve(
    store: Store, reference: ReferenceData, settings: Settings, invoice_ids: list[int], reviewer: str, login: str = "",
    server: bool = False,
) -> dict[str, Any]:  # fmt: skip
    """Approve each invoice exactly as it is coded, if it is still clean. Returns approved / skipped.

    "As coded" is what the review screen shows: an earlier approver's corrections (a reopened or sent-back
    invoice) when there are some, otherwise the AI's coding. ``server``: the company server, where a held vendor's
    invoice coded to another vendor is the review screen's error (``VENDOR_CHANGED_FROM_HELD``, D-142)."""
    with store.session():  # one connection for every invoice (a no-op inside the caller's own session)
        return _bulk_approve(store, reference, settings, invoice_ids, reviewer, login, server)


def _bulk_approve(
    store: Store, reference: ReferenceData, settings: Settings, invoice_ids: list[int], reviewer: str, login: str,
    server: bool,
) -> dict[str, Any]:  # fmt: skip
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
        stored = [i for i in found if needs_a_person(i.get("severity"), i.get("code"))]
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
        moved = moved_off_hold(store, inv["ai_output"], coding) if server else []
        if moved:  # a held vendor's, by the GST/HST number or bank account it shows (B-1551)
            skipped.append((invoice_id, _plain(moved[0][2], moved[0][1])))
            continue
        output, report = finalise_coding(coding, reference, settings, store=store, exclude_invoice_id=invoice_id,
                                         received=_received_on(inv))  # fmt: skip
        blocking = [i for i in report.issues if needs_a_person(i.severity, i.code)]
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
