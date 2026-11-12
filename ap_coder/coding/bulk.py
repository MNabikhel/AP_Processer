"""Bulk approval of clean invoices.

The queue says which invoices look clean by today's checks: those made against other settings (GL accounts, cost
centers, tax, JD Edwards) are done again (``issues_today``, B-815). An invoice qualifies only if, re-checked right now
(duplicates and history may have changed since it was processed), it has no error and no warning, no finding that
always goes to a person (``ALWAYS_A_PERSON``: some, like a new vendor name, are only info), it is not a credit note (a
credit is always applied by a person, as the touchless rules say), no line of it was coded from other vendors'
approvals (D-241, as the touchless rules say too) and the AI's confidence is above the review threshold.
Everything else is skipped with the reason, and still needs a person.

Entry points: ``sort_for_bulk`` (the queue's clean invoices, and why each other is left), ``queue_checks`` (what the
queue reads of its invoices, from the invoices' summary), ``recheck_queue`` and ``QueueRecheck`` (the queue checked
again after a change of settings, in the background), ``issues_today`` and ``bulk_approve``, and ``approve_in_job``
(a person's bulk approval as a job, done in the background as them: D-185).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from types import MappingProxyType
from typing import Any

from ..ai.pipeline import finalise_coding
from ..capture.supplier import ALWAYS_A_PERSON, others_coded, others_reason, touchless_gates
from ..capture.workflow import learn_from_approval
from ..core.config import Settings
from ..core.memory import vendor_key
from ..core.reference_data import ReferenceData
from ..core.schema import InvoiceCoding
from ..core.store import REVIEW, Store
from ..core.store._base import CHECKS_STAMP_KEY
from ..core.store.bulk_jobs import CHANGED, GONE
from ..core.store.summary import QUEUE_READ, computed
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
    "PAGES_NOT_READ", "CURRENCY_NOT_FOUND", "NOT_AN_INVOICE", "LINE_AMOUNTS_TO_CHECK",
    "ACCOUNT_ROWS_LEFT_OUT",
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
RECHECK_STOP_SECONDS = 5.0  # how long a stop waits for the background's re-check to end between two batches
QUEUE_COLUMNS = ", ".join(("id", *QUEUE_READ))  # what the summary's ``queue`` topic follows
KEEP_FROM = 50  # from this many invoices asked, their facts are taken from the whole queue's (fewer: read each time)
# Each invoice's findings as the queue reads them (``_found``), by the texts the summary keeps: the queue's invoices
# share a few of them (211 for 2,654 invoices at 100,000), so each is decoded once, not again after each approval or
# park (B-1730). Shared: read-only. Past FOUND_KEPT texts they are all let go.
_FOUND: dict[tuple[str | None, str | None], tuple[MappingProxyType, ...]] = {}
FOUND_KEPT = 4096


def _pairs(issues: list[dict[str, Any]]) -> str:
    """The findings' severity and code, all the queue reads of them, as the summary keeps them."""
    return json.dumps([[i.get("severity"), i.get("code")] for i in issues], separators=(",", ":"))


def _stale(row: dict[str, Any], stamp: str) -> bool:
    """Its stored checks and its queue re-check were both made against other settings than ``stamp``."""
    return row["q_stamp"] is not None and row["q_stamp"] != stamp and row["r_stamp"] != stamp


def _today(rows: list[dict[str, Any]], stamp: str) -> list[list[dict[str, Any]]]:
    """Each invoice's findings by today's checks (severity and code) from its queue facts, as ``issues_today`` gives
    them: the stored ones, or, once its stored checks were made against other settings, the queue's re-check with the
    stored findings made after processing (``_kept``). One not checked again yet (``_stale``): its latest. Each in a
    list of its own, the findings in it read-only (shared by the invoices with the same, ``_found``)."""
    return [list(_found(r["q_found"], r["r_found"] if r["q_stamp"] is not None and r["q_stamp"] != stamp else None))
            for r in rows]  # fmt: skip


def _found(stored: str | None, rechecked: str | None) -> tuple[MappingProxyType, ...]:
    """The findings of the summary's texts of an invoice's stored checks and of its queue re-check (None: none to
    take), decoded once (``_FOUND``)."""
    found = _FOUND.get((stored, rechecked))
    if found is None:
        kept = [MappingProxyType({"severity": severity, "code": code}) for severity, code in json.loads(stored or "[]")]
        again = json.loads(rechecked or "null")
        if again is not None:
            today = [MappingProxyType({"severity": severity, "code": code}) for severity, code in again]
            codes = {t["code"] for t in today}
            kept = today + [i for i in _kept(kept) if i["code"] not in codes]
        # (else no re-check, or its coding can't be read: the stored checks stand)
        if len(_FOUND) >= FOUND_KEPT:
            _FOUND.clear()
        found = _FOUND[(stored, rechecked)] = tuple(kept)
    return found


def _queue_rows(store: Store, ids: list[int]) -> dict[int, dict[str, Any]]:
    rows = store.queue_rows(ids)  # each findings text read once (B-1741)
    return {r["id"]: computed(store, r, "queue") for r in rows}  # one the summary could not read: read again


def _kept_rows(store: Store, ids: list[int]) -> dict[int, dict[str, Any]]:
    """``_queue_rows`` of ``ids``, taken from the facts of the whole queue each process keeps (``Store.queue_facts``):
    shared, read-only."""
    facts = store.queue_facts()
    return {i: facts[i] for i in sorted({int(i) for i in ids}) if i in facts}


def queue_checks(
    store: Store, ids: list[int], settings: Settings | None = None, recheck: bool | None = None
) -> tuple[dict[int, dict[str, Any]], int]:
    """({id: what the queue reads of it} for the invoices of ``ids`` in review: its facts from the invoices' summary
    (``queue_facts``), and ``found``, its findings by today's checks (severity and code); how many of them still show
    earlier checks). The checks are today's (B-815): with ``recheck`` (the laptop: by default unless AP Coder runs as
    the company server), an invoice whose checks were made against other settings is checked again first, waiting
    for a re-check under way; without it (the server's background process does it, ``QueueRecheck``), it shows its
    earlier checks meanwhile. From ``KEEP_FROM`` invoices asked, their facts are taken from those of the whole queue
    each process keeps, followed by the summary's log of the queue's changes (``Store.queue_facts``, D-193): the queue
    and each of its tabs ask them. Each caller gets rows and lists of its own (the findings in them are read-only)."""
    if not ids:
        return {}, 0
    if recheck is None:
        from ..ops.offline import server_mode

        recheck = not server_mode()
    return _queue_checks(store, ids, settings, recheck, _queue_rows if len(ids) < KEEP_FROM else _kept_rows)


def _queue_checks(
    store: Store, ids: list[int], settings: Settings | None, recheck: bool,
    rows_of: Callable[[Store, list[int]], dict[int, dict[str, Any]]] = _queue_rows,
) -> tuple[dict[int, dict[str, Any]], int]:  # fmt: skip
    rows = rows_of(store, ids)
    stamp = store.checks_stamp()
    if recheck and any(_stale(r, stamp) for r in rows.values()):
        recheck_queue(store, settings, ids=[i for i, r in rows.items() if _stale(r, stamp)], wait=RECHECK_WAIT)
        rows, stamp = rows_of(store, ids), store.checks_stamp()
    found = _today(list(rows.values()), stamp)
    checks = {i: {**r, "found": f} for (i, r), f in zip(rows.items(), found, strict=True)}
    return checks, sum(_stale(r, stamp) for r in rows.values())


def recheck_queue(store: Store, settings: Settings | None = None, *, ids: list[int] | None = None,
                  wait: float = 0.0, stopping: Callable[[], bool] | None = None) -> int | None:  # fmt: skip
    """Check again each invoice in review (only ``ids``' when given) whose checks were made against other settings
    than today's, as ``issues_today`` does, keeping each batch of ``RECHECK_BATCH`` in one short write. One process at
    a time: a lease in the database, waited for up to ``wait`` seconds. How many were checked; None when another
    process held the lease all that time. ``stopping``: True once AP Coder stops, which ends it between two batches
    (the rest: the next start)."""
    owner = f"{os.getpid()}:{threading.get_ident()}"
    deadline = time.monotonic() + wait
    while not store.take_lease(RECHECK_LEASE, owner, LEASE_SECONDS):
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.2)
    try:
        with store.session():  # one connection for every invoice checked again
            return _recheck_queue(store, settings or Settings(), ids, owner, stopping or (lambda: False))
    finally:
        store.drop_lease(RECHECK_LEASE, owner)


def _recheck_queue(store: Store, settings: Settings, ids: list[int] | None, owner: str,
                   stopping: Callable[[], bool]) -> int:  # fmt: skip
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
            if stopping():
                return done
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
                if (
                    not store.restoring()
                    and store.stale_checks(store.checks_stamp())
                    and store.list_accounts("gl_accounts")
                ):  # (no GL accounts: nothing is checked, the stored checks stand)
                    recheck_queue(store, self.settings_factory(), stopping=self.stopping.is_set)
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
    "credit note", "audit" for one picked for the touchless audit sample, "others" for a line coded from other
    vendors' approvals (D-241), or "warning" for an error, a warning or a finding that always goes to a person).
    ``queue``: the invoices in review as ``store.list_invoices`` gives them, when the caller has them already.
    ``checks``: ``queue_checks`` of the queue, when the caller has it already. The checks are today's: an invoice
    whose stored checks were made against other settings is checked again (B-815)."""
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
        elif facts.get("q_others"):
            left[inv["id"]] = "others"
        elif any(needs_a_person(i["severity"], i["code"]) for i in facts["found"]):
            left[inv["id"]] = "warning"
        else:
            candidates.append(inv)
    return candidates, left


def queue_facts(row: Any) -> dict[str, Any]:
    """What the queue reads of an invoice (its columns, the JSON ones decoded), kept in the invoices' summary (D-110,
    D-155): picked for the audit sample; of one in review, also the stamps of its stored checks ("", None without
    stored checks to check again) and of its queue re-check, their findings (``_pairs``; None for a re-check whose
    coding could not be read), what its lines still need (``codes_to_pick``, without and with cost centers), what
    its opening Pay from account is chosen from (``banks.opening_choice``: the account, approved before or not, the
    currency, and the vendor whose last account it is) and why its lines coded from other vendors' approvals leave it
    for a person (``supplier.others_reason``, None: none is, D-241)."""
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
    stamp = str(validation.get(CHECKS_STAMP_KEY) or "") if isinstance(row["validation"], dict) else None
    return {**facts, "q_stamp": stamp,
            "q_found": _pairs(issues), "r_stamp": str(recheck.get("stamp") or ""),
            "r_found": None if recheck.get("issues") is None else _pairs(recheck["issues"]),
            "q_pick": codes_to_pick(lines, False, hint=False).removesuffix(" to approve"),
            "q_pick_cc": codes_to_pick(lines, True, hint=False).removesuffix(" to approve"),
            "q_pay": json.dumps(pay, separators=(",", ":")),
            "q_others": others_reason(others_coded(lines)) or None}  # fmt: skip


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
        output, why = _still_clean(store, reference, settings, invoice_id, stamp, server)
        if output is None:
            skipped.append((invoice_id, why))
            continue
        try:
            store.approve_invoice(invoice_id, output, reviewer, bulk=True, login=login)
        except (KeyError, ValueError):
            # A colleague parked, rejected or approved it while the loop checked the others: skip this one only.
            skipped.append((invoice_id, GONE))
            continue
        # Nobody opened it: it teaches the template, but only a correction counts towards touchless processing.
        learn_from_approval(store, invoice_id, output, actor=reviewer, bulk=True)
        approved.append(invoice_id)
    return {"approved": approved, "skipped": skipped}


def _still_clean(store: Store, reference: ReferenceData, settings: Settings, invoice_id: int, stamp: str,
                 server: bool) -> tuple[dict[str, Any] | None, str]:  # fmt: skip
    """(the coding to approve, "") when invoice ``invoice_id`` is still clean, checked again now as it is coded;
    (None, why it is left for a person) otherwise."""
    inv = store.get_invoice(invoice_id)
    if inv is None or inv["status"] != REVIEW or not inv.get("ai_output"):
        return None, "no longer in the queue"
    if _audit_pick(inv.get("meta")):
        return None, AUDIT_PICK[:1].upper() + AUDIT_PICK[1:]
    # What was found after processing and is kept only with the invoice (the Azure check's disagreement, the page
    # reader's line items or text layer read differently): finalise_coding below does not raise it again.
    validation = inv.get("validation") or {}
    found = validation.get("issues") or []
    if validation.get(CHECKS_STAMP_KEY) != stamp:  # checked against other settings (B-815)
        found = _kept(found)  # the coding's own checks are done again below, as they come out today
    stored = [i for i in found if needs_a_person(i.get("severity"), i.get("code"))]
    if stored:
        return None, _plain(stored[0].get("message"), stored[0].get("code"))
    coded = inv.get("final_output") or inv["ai_output"]
    coded = {k: v for k, v in coded.items() if k != "gl_distribution"}
    try:
        coding = InvoiceCoding.model_validate(coded)
    except ValueError:
        return None, "the coding needs fixing by hand"
    if _credit_note(bool((store.get_capture(invoice_id) or {}).get("credit_note")), coding.grand_total):
        return None, "it is a credit note: a credit is always applied by a person"
    others = others_reason(others_coded(coded.get("line_items")))
    if others:  # a line coded from other vendors' approvals: a person looks at it (D-241)
        return None, _plain(others, "")
    moved = moved_off_hold(store, inv["ai_output"], coding) if server else []
    if moved:  # a held vendor's, by the GST/HST number or bank account it shows (B-1551)
        return None, _plain(moved[0][2], moved[0][1])
    output, report = finalise_coding(coding, reference, settings, store=store, exclude_invoice_id=invoice_id,
                                     received=_received_on(inv))  # fmt: skip
    blocking = [i for i in report.issues if needs_a_person(i.severity, i.code)]
    if blocking:
        return None, _plain(blocking[0].message, blocking[0].code)
    if report.requires_review:
        return None, f"confidence {report.adjusted_confidence:.0%} is below the threshold"
    return output, ""


# --- Bulk approval as a job (D-185) ---------------------------------------------------------------------------------
# A person's bulk approval is a job kept in the database (``Store.start_bulk_job``), approved by a background thread
# (``web/jobs.py`` ``BulkRunner``) while their page shows how far it got: the web worker asked holds no thread for it.

JOB_FORMAT = "bulk-2"  # what a job's record is (another: a job an older AP Coder left ends, and says so)


class Stopping(Exception):
    """The job stops for now: AP Coder is stopping (it is put back in line), or it is no longer this runner's."""


def _open_elsewhere(store: Store, invoice_id: int, owner: str) -> str:
    """Why invoice ``invoice_id`` is left when someone other than ``owner`` (whose job it is) has it open on their
    review screen now (``Store.open_by``, the server's, D-184): "Ann Lee has it open on the review screen"; "" when
    nobody else has."""
    people = store.open_by(invoice_id).get(invoice_id, {})
    # (each as the screens show it to the others: never an email)
    names = list(dict.fromkeys(str(name).split("@")[0] for who, name in people.items() if who != owner))
    if not names:
        return ""
    who = f"{', '.join(names[:-1])} and {names[-1]}" if len(names) > 1 else names[0]
    return f"{who} {'has' if len(names) == 1 else 'have'} it open on the review screen"


def approve_in_job(
    store: Store, settings: Settings, job: dict[str, Any], runner: str, *, stopping: Callable[[], bool] = lambda: False,
    refused: Callable[[], str] = lambda: "",
) -> str:  # fmt: skip
    """Approve the invoices of bulk job ``job`` (claimed by ``runner``) not done yet, in the order asked, each exactly
    as ``bulk_approve`` does and as the person who asked (``job["person"]``: their name and login on each approval;
    the caller makes them who the other events name). Each outcome is kept with its approval (``Store.bulk_step``).
    An invoice is left for a person, with why, when it is no longer the one asked (``CHANGED``: another
    ``row_identity``, or its stamp moved since the queue listed it, ``Store.bulk_stamps``: approved and undone,
    parked, read again; ``bulk_step`` looks again in the approval's own transaction), when it is no longer clean, or
    when someone else has it open on their review screen (``Store.open_by``) or edits of it kept there (``bulk_step``
    looks there too). Raises ``Stopping`` once ``stopping()`` is true, or the job is no longer ``runner``'s. Returns ""
    once every invoice is done, else why it stopped: what ``refused()`` says (the person may no longer approve)."""
    person = job["person"]
    taken = set(job["approved"]) | {s[0] for s in job["skipped"]}
    reference = store.reference_data()
    stamp = store.checks_stamp()
    for invoice_id, identity, listed in job["ids"]:
        if invoice_id in taken:
            continue
        # Its progress noted at each invoice's start too, not only at each approval: the last one's learning taking
        # long never lets another runner take the job meanwhile (one that did goes on with it).
        if stopping() or not store.renew_bulk_job(job["id"], runner):
            raise Stopping
        why = refused()
        if why:
            return why
        output = None
        now = store.row_identity(invoice_id)
        if now is None:
            why = "no longer in the queue"
        elif list(now) != identity:
            why = CHANGED
        else:
            output, why = _still_clean(store, reference, settings, invoice_id, stamp, bool(person.get("server")))
        if output is not None and (why := _open_elsewhere(store, invoice_id, job["owner"])):
            output = None  # (theirs to approve as they see it, though nothing of theirs is checked yet)

        def approve(invoice_id: int = invoice_id, output: dict[str, Any] | None = output) -> None:
            store.approve_invoice(invoice_id, output or {}, person["name"], bulk=True, login=person.get("login") or "")

        outcome = store.bulk_step(job["id"], runner, invoice_id, why, approve if output is not None else None,
                                  expect=(identity, listed))  # fmt: skip
        if outcome is None:
            raise Stopping  # (taken again by another runner: it goes on with it)
        if outcome == "approved":
            # Nobody opened it: it teaches the template, but only a correction counts towards touchless processing.
            learn_from_approval(store, invoice_id, output or {}, actor=person["name"], bulk=True)
    return ""
