"""JD Edwards API step 2 (D-104, its safety D-105): an approved JD Edwards batch posted as vouchers through the voucher
orchestration, next to the batch files, which stay the fallback.

The flow, per export batch made in the JD Edwards format (Exports):

1. **Dry run** (``dry_run``, a Clerk or more): each voucher's rows exactly as the Z-files would hold them
   (``jde.voucher_rows``: the same field mapping), checked here (``jde.validate``) and, when JDE answers, by the
   voucher orchestration in proof mode (``ProcessingMode`` PROOF: nothing is committed). Each invoice gets a row in
   ``jde_postings``: *checked* (with the fingerprint of what was shown, ``payload_hash``, and who the voucher is for,
   ``expect``) or *refused* (the reasons). Only VALID passes: EXISTS is "already in JD Edwards" (locked for a person).
2. **Approval** (``post``, an Approver or Admin): only what was checked, unchanged since (the same fingerprint), is
   sent, in FINAL mode, one voucher at a time. The idempotency key is ``APC-<installation id>-<invoice id>-<batch>``
   (``core/store/postings.py``: the installation id is random and kept in the data folder, outside the database).
3. **The result is kept per invoice** (the state machine, D-105):

   * *checked* → *sending* only by a compare-and-set (``_claim``: the row unchanged since read, nothing in JDE may
     hold it, its batch not undone), so the queue, the background tick and an undo never race a live post.
   * *sending* → *posted* (CREATED with a document number; EXISTS only when JDE's record is this voucher: the same
     fingerprint, else the same supplier, invoice number and gross); *failed* when JDE's record is another voucher
     (locked for a person), or when JDE refused it and a lookup by the key says it holds nothing; *unknown* for
     anything else that may have reached JDE (a timeout, 502/504, an answer without a document number or not
     understood, a lookup that could not be made); *queued* only when the request certainly never reached JDE
     (the connection refused, AIS busy with 503, the sign-in or permission refused before the orchestration ran).
   * *unknown* / *sending* never go back to *queued* or *failed* unless a lookup by the key says JDE holds nothing
     (``maybe_sent`` is then cleared): JDE down, no client, a lookup that fails leave the row as it is (its next try
     only). A *sending* row is someone's live post for as long as a send can take (``SENDING_MINUTES`` at least,
     ``send_seconds``: its retries included); after that it is looked up, never sent blind. A
     "not there" for a send whose answer was lost is believed only ``resend_minutes`` after the answer was lost, and
     never sooner than a voucher's wait and a margin (``_in_flight``, ``JdeSettings.resend_seconds``, D-182): JDE
     may still be making it.
   * **A run** (``_run``, D-182): the lookups first, then the sends, one after another, going on past a late answer
     (*unknown*), then the run's late ones looked up; it stops (the rest queued, saying why) when JDE can't be
     reached, after ``LATE_STOP`` late answers in a row, or after its time. Just before each voucher its supplier's
     hold (queued while held) and its G/L date's period (back to *checked*, for a new dry run, once closed) are read.
4. **The queue** (``retry_queue``: the button on Exports, ``python -m ap_coder jde status --retry``, and every five
   minutes in the background): each row is looked up by its key first; found, it is *posted* (or *failed*, another
   voucher); not found, the same voucher (the same fingerprint, else *failed*: dry run again) is sent with the same
   key. **Never posted twice**: a locked row (``postings.LOCKED_SQL``) is never sent blind, its batch can't be undone
   (``undo_batch``, in the same kind of transaction as the claim), and its ZIP of batch files leaves it out.
5. **The batch's ZIP downloaded** (D-134): each download is an event (``file_downloaded``: who, when). From then on the
   go-ahead asks for a person's word that the file was not loaded into JDE (``post``'s ``not_loaded``, kept in the
   ``jde_api_approved`` event), and the vouchers waiting in the queue (JDE never reached: the file holds them) are
   not sent by the queue any more: they wait for that go-ahead.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import Any

from ..coding.vendors import norm_invoice_number, voucher_hold
from ..core.safe import parse_amount
from ..core.store import postings as ledger
from ..core.store.postings import (  # the rows' statuses, kept with the table (core/store/postings.py, D-189)
    CHECKED,
    FAILED,
    FILE_DOWNLOADED,
    POSTED,
    QUEUED,
    REFUSED,
    SENDING,
    UNKNOWN,
)
from . import jde, jde_ais

LABELS = {CHECKED: "Dry run passed", REFUSED: "Refused in the dry run", QUEUED: "Waiting: JD Edwards not reached",
          SENDING: "Being sent", POSTED: "Posted", FAILED: "Refused by JD Edwards",
          UNKNOWN: "Sent, answer not received: being checked"}  # fmt: skip
TONES = {CHECKED: "ok", REFUSED: "err", QUEUED: "warn", SENDING: "warn", POSTED: "ok", FAILED: "err", UNKNOWN: "warn"}
HELD_BACK = "Not sent: dry run again"  # *checked* after its go-ahead, held back by its run (``status_rows``)
LOCKED = ledger.LOCKED  # by status; any row with ``maybe_sent`` too (``ledger.LOCKED_SQL``)
DRY_RUN_OVER = (CHECKED, REFUSED, FAILED)  # what a dry run may write over (and only with maybe_sent = 0)
RETRY_MINUTES = (5, 15, 60)  # the queue's waits after each try that did not reach JDE (then hourly)
# A row *sending* longer than this, and than a send can take (``_live_seconds``), was left by a stop: looked up by its
# key, never resent blind.
SENDING_MINUTES = 15
LATE_STOP = 3  # answers lost one after another: JDE is overloaded, the rest of the run waits in the queue (D-182)
LOOK_AFTER = 30.0  # seconds after a lost answer before the run looks the voucher up (a younger one: the queue does)
# JDE's words for a G/L date in a closed period (PBCO: "post before cut-off"): CONFIRM R04110ZA's error IDs (D-182)
PERIOD_REFUSED = re.compile(r"\bPBCO\b|closed (fiscal )?(period|year)|period (is )?closed|before (the )?cut[- ]?off|"
                            r"prior (fiscal )?(period|year)", re.IGNORECASE)  # fmt: skip
YES, NO = ("1", "Y", "YES", "TRUE"), ("0", "N", "NO", "FALSE")


class Locked(ValueError):
    """The batch can't be undone: JDE has, or may have, one of its vouchers."""


class FileDownloaded(ValueError):
    """The batch's JD Edwards ZIP was downloaded: posting it needs a person's word that it was not loaded (D-134)."""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# The safety waits (a live send's, a lost answer's) are kept and compared in UTC, which a change of the clocks doesn't
# move (B-1887): ``sending_at`` and ``lost_at`` with their offset; one an older AP Coder kept is local time.
def _clock() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _stamp() -> str:
    return _clock().isoformat(timespec="seconds")


def _at(text: str) -> dt.datetime:
    """A time kept on a row, in UTC (a time without its offset is local: kept by an older AP Coder)."""
    return dt.datetime.fromisoformat(text).astimezone(dt.UTC)


def idem_key(invoice_id: int, batch: int, installation: str = "") -> str:
    head = f"APC-{installation}" if installation else "APC"
    return f"{head}-{int(invoice_id)}-{int(batch)}"


def installation_id(store: Any) -> str:
    return ledger.installation_id(store.path)


def _hash(payload: dict[str, Any]) -> str:
    body = {k: payload.get(k) for k in ("pay_items", "gl_lines", "matches")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


# --- What is sent ---------------------------------------------------------------------------------------------------


def _batch(store: Any, batch: int) -> dict[str, Any]:
    known = store.export_batch(batch)
    if known is None or known["undone_at"]:
        raise ValueError(f"Batch {batch} is unknown or undone.")
    if known["format"] != jde.FORMAT:
        raise ValueError(f"Batch {batch} was made as {known['format']}: only a JD Edwards batch goes through the API.")
    return known


def _expect(inv: dict[str, Any], headers: list[dict[str, Any]], settings: jde.JdeSettings) -> dict[str, Any]:
    """Who the voucher is for, to check JDE's record of a key against (supplier, invoice number, gross)."""
    head = headers[0] if headers else {}
    gross: list[float] = []
    total = parse_amount((inv.get("final_output") or {}).get("grand_total"))
    if total is not None:
        gross.append(round(total, 2))
    # Implied decimals are the currency's own: whole yen are written as they are (B-1372).
    places = jde.NO_DECIMAL_CURRENCIES.get(str(head.get("VLCRCD") or "").upper(), 2)
    for foreign in (False, True):
        try:
            raw = sum(float(jde._gross_of(h, foreign) or 0) for h in headers)
        except (TypeError, ValueError):
            continue
        if raw:
            gross.append(round(raw / 10**places if settings.decimals == jde.IMPLIED else raw, 2))
    return {"an8": str(head.get("VLAN8") or ""), "invoice_number": str(head.get("VLVINV") or ""),
            "gross": sorted(set(gross))}  # fmt: skip


def payloads(store: Any, batch: int) -> tuple[dict[int, dict[str, Any]], dict[int, list[str]], list[dict[str, Any]]]:
    """({invoice: what the voucher orchestration is sent}, {invoice: why it can't go}, the batch's invoices)."""
    return _payloads(store, batch)[:3]


def _payloads(store: Any, batch: int) -> tuple[dict[int, dict[str, Any]], dict[int, list[str]],
                                               list[dict[str, Any]], dict[str, Any]]:  # fmt: skip
    """``payloads``, and what they were made with (for the dry run's report: not read or prepared again): {"settings",
    "lookup", "on", "fx_rates", "site", "vouchers": {invoice: (AN8, voucher)}}."""
    known = _batch(store, batch)
    settings, lookup, fx_rates = jde.load(store), store.vendor_ids(), store.fx_rates()
    invoices = store.get_invoices(store.batch_invoice_ids(batch))
    blocking = jde.settings_problems(settings)
    prepared = jde._vouchers(invoices, settings, lookup)
    problems = jde.validate(invoices, settings, lookup, prepared=prepared)
    on = dt.date.fromisoformat(known["created_at"][:10])
    installation, site = installation_id(store), jde.batch_site(known, invoices)
    out: dict[int, dict[str, Any]] = {}
    for inv, (an8, v) in zip(invoices, prepared, strict=True):
        if blocking:
            problems.setdefault(inv["id"], []).extend(blocking)
        day, moved = jde.gl_date_of(inv.get("final_output") or {}, settings, on)  # the batch's day, when that is it
        if inv["id"] not in problems and (closed := jde.period_problem(day, settings)):
            problems[inv["id"]] = [closed]
        if inv["id"] in problems:
            continue
        headers, dists, matches = jde.voucher_rows(inv, an8, settings, batch=batch, fx_rates=fx_rates, on=on,
                                                   prepared=v, site=site)  # fmt: skip
        out[inv["id"]] = {"key": idem_key(inv["id"], batch, installation), "pay_items": headers, "gl_lines": dists,
                          "matches": matches, "_expect": _expect(inv, headers, settings),
                          "_timeout": settings.voucher_timeout, "_vendor": inv.get("vendor_key") or "",
                          "_moved": jde.moved_note(day, moved) if moved else ""}  # fmt: skip
    made = {"settings": settings, "lookup": lookup, "on": on, "fx_rates": fx_rates, "site": site,
            "vouchers": {inv["id"]: pv for inv, pv in zip(invoices, prepared, strict=True)}}  # fmt: skip
    return out, problems, invoices, made


# --- The postings table (its statements: core/store/postings.py, ``PostingsMixin``) ----------------------------------


def postings(store: Any, batch: int | None = None, statuses: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    return store.jde_postings(batch, statuses)


def _put(store: Any, key: str, invoice_id: int, batch: int, *, only_from: tuple[str, ...] | None = None,
         rev: int | None = None, **values: Any) -> bool:  # fmt: skip
    """Write the row's values. A new row only for a batch not undone. ``only_from``: only over a row in one of these
    statuses that JDE can't hold (maybe_sent 0); ``rev``: only if the row is still that version. Whether it changed."""
    values["updated_at"] = _now()
    return store.put_jde_posting(key, invoice_id, batch, values, status=values.get("status", CHECKED),
                                 only_from=only_from, rev=rev)  # fmt: skip


def _claim(store: Any, row: dict[str, Any], attempts: int) -> dict[str, Any] | None:
    """Compare-and-set before a send: *sending* only if the row is the version read, sendable, JDE can't hold it, its
    batch is not undone and its ZIP was not downloaded since the go-ahead (D-134: the file holds it); the row as
    claimed, or None (someone else has it, the batch was undone or its file downloaded)."""
    return store.claim_jde_posting(row, attempts, sending_at=_stamp(), now=_now())


def documents_of(store: Any, invoice_id: int | None) -> list[str]:
    """JDE's voucher numbers AP Coder posted for this invoice (the live duplicate check leaves them out)."""
    return store.jde_documents(invoice_id)


def not_posted(store: Any) -> dict[int, str]:
    """{invoice: what its voucher is} of the exported invoices whose voucher went to JD Edwards through the API (its
    go-ahead given) and is not posted there yet: waiting in the queue, being sent, sent with its answer lost, held back
    for a new dry run, or refused, by JDE or by a dry run since (B-1885), unless the batch's files were downloaded
    since: then loaded from them, as any batch (``file_downloaded`` takes their go-ahead off, B-1882). Month end
    takes them as not in the ERP (R4, B-1862)."""
    return store.jde_not_posted()


def locked(store: Any, batch: int) -> list[dict[str, Any]]:
    """The batch's rows JDE has, or may have (``ledger.LOCKED_SQL``)."""
    return store.jde_locked(batch)


def left_out_of_files(store: Any, batch: int) -> dict[int, list[str]]:
    """{invoice: why} for the batch files: every voucher JDE has or may have, never loaded a second time."""
    out = {}
    for p in locked(store, batch):
        if p["status"] == POSTED:
            why = "already posted to JD Edwards through the API"
        elif p["status"] in (UNKNOWN, SENDING):
            why = ("sent to JD Edwards through the API, answer not received: check in JD Edwards before loading it "
                   "from these files")  # fmt: skip
        else:
            why = f"sent to JD Edwards through the API ({p['error'] or LABELS.get(p['status'], p['status'])})"
        out[p["invoice_id"]] = [why]
    return out


def _locked_message(batch: int, found: list[str]) -> str:
    """Why batch ``batch`` can't be undone, from the statuses of its rows JDE has or may have, each said as it is: a
    voucher posted is voided in JD Edwards; one being sent or checked waits; any other may be there (B-1202)."""
    posted = found.count(POSTED)
    checked = sum(1 for status in found if status in (SENDING, UNKNOWN))
    other = len(found) - posted - checked

    def vouchers(n: int) -> str:
        return f"{n} voucher{'' if n == 1 else 's'}"

    parts = ([f"{vouchers(posted)} posted to JD Edwards: reverse {'it' if posted == 1 else 'them'} there (void the "
              "voucher)"] if posted else [])  # fmt: skip
    if checked:
        parts.append(f"{vouchers(checked)} sent, answer not received: being checked in JD Edwards (the queue does it)")
    if other:
        parts.append(f"{vouchers(other)} may be in JD Edwards (it refused or didn't answer): check there first")
    return f"Batch {batch} can't be undone: {'; '.join(parts)}."


def release(store: Any, batch: int, invoice_id: int, actor: str | None, note: str) -> None:
    """An Admin checked in JD Edwards that a voucher locked here isn't there (JDE answered EXISTS for another
    invoice, say): the row is marked not sent, so the invoice goes in the batch files again or the batch can be
    undone, and an event says who released it and why (D-107). ValueError, in plain words, when it can't be: no
    note, not locked, being sent, or posted (that one is voided in JD Edwards, never released)."""
    note = " ".join((note or "").split())
    if not note:
        raise ValueError("Say what you checked in JD Edwards.")
    row = next((p for p in postings(store, batch) if p["invoice_id"] == invoice_id), None)
    if row is None or not (row["status"] in LOCKED or row.get("maybe_sent")):
        raise ValueError(f"AP Coder #{invoice_id} isn't held for JD Edwards in batch {batch}: nothing to release.")
    if row["status"] == POSTED:
        raise ValueError(f"AP Coder #{invoice_id} is posted to JD Edwards: reverse it there (void the voucher).")
    if row["status"] == SENDING:
        raise ValueError(f"AP Coder #{invoice_id} is being sent to JD Edwards: wait for its answer.")
    error = f"Released by {actor or 'an Admin'} after checking in JD Edwards: {note}"[:500]
    if not _put(store, row["idem_key"], invoice_id, batch, rev=row["rev"], status=FAILED, maybe_sent=0, verify=0,
                next_try=None, error=error):  # fmt: skip
        raise ValueError(f"AP Coder #{invoice_id} changed meanwhile: look at it again.")
    store.log_event("jde_voucher_released", invoice_id=invoice_id, actor=actor,
                    detail={"batch": batch, "key": row["idem_key"], "was": row["status"], "note": note})  # fmt: skip


def undo_problem(store: Any, batch: int) -> str:
    """Why the batch can't be undone ("" when it can): JDE has, or may have, one of its vouchers."""
    found = locked(store, batch)
    return _locked_message(batch, [p["status"] for p in found]) if found else ""


def undo_batch(store: Any, batch: int, actor: str | None = None) -> int:
    """Undo the batch (``Store.undo_export_batch``) in one write transaction with the check that none of its vouchers
    is, or may be, in JDE: Locked (a ValueError) when one is; ValueError when it was undone already. Its dry runs and
    queued vouchers are dropped."""
    count = store.undo_jde_batch(batch, actor, lambda found: Locked(_locked_message(batch, found)))
    forget_batch(store, batch)
    return count


def forget_batch(store: Any, batch: int) -> None:
    """The batch was undone: its dry runs and queued vouchers are dropped (never a row JDE may hold)."""
    store.forget_jde_batch(batch, _now())


# --- The batch's ZIP downloaded (D-134) -------------------------------------------------------------------------------


def file_downloaded(store: Any, batch: int, actor: str | None) -> None:
    """The batch's JD Edwards ZIP is being downloaded: who and when, in the audit trail. Its vouchers waiting in the
    queue (approved, JDE never reached: the file holds them) go back to waiting for a go-ahead, which asks whether the
    file was loaded; every other voucher the file holds loses its go-ahead too (held back for a new dry run, or
    refused: loaded from the file, it is in JDE for month end, B-1882). Called before the file is made: a voucher
    the queue claims meanwhile is left out of it."""
    why = "Not sent: the batch's JD Edwards file was downloaded. Post it only if the file was not loaded"
    for row in postings(store, batch, statuses=(QUEUED, CHECKED, FAILED, REFUSED)):
        if row["status"] == QUEUED:
            _put(store, row["idem_key"], row["invoice_id"], batch, only_from=(QUEUED,), rev=row["rev"],
                 status=CHECKED, approved_at=None, approved_by=None, next_try=None, error=why)  # fmt: skip
        elif row["approved_at"]:
            _put(store, row["idem_key"], row["invoice_id"], batch, only_from=(row["status"],), rev=row["rev"],
                 approved_at=None, approved_by=None, next_try=None)  # fmt: skip
    store.log_event(FILE_DOWNLOADED, actor=actor, detail={"batch": int(batch)})


def downloads(store: Any, batch: int) -> list[dict[str, str]]:
    """Each download of the batch's JD Edwards ZIP, newest first: {"actor", "at"}."""
    return store.jde_downloads(batch)


def downloaded_message(batch: int, found: list[dict[str, str]]) -> str:
    last = found[0]
    more = f" ({len(found)} downloads)" if len(found) > 1 else ""
    return (f"Not posted: the JD Edwards file of batch {batch} was downloaded by {last['actor'] or 'someone'} on "
            f"{last['at'].replace('T', ' ')[:16]}{more}. If it was loaded into JD Edwards, these vouchers would be "
            "there twice: tick that it was not loaded and say what you checked.")  # fmt: skip


def send_seconds(settings: jde.JdeSettings, cfg: jde_ais.AisSettings) -> float:
    """The longest one voucher's send can take (B-1881): each try of its request a voucher's wait, AIS busy (503)
    answered at the end of it and tried again after its pause (``jde_ais``), a sign-in before it (each try the
    connection's wait), and all of it twice over when the sign-in token expired meanwhile."""
    tries = jde_ais.DEFAULT_RETRIES + 1
    pauses = sum(jde_ais.RETRY_PAUSE * 2**i for i in range(jde_ais.DEFAULT_RETRIES))
    return 2 * (tries * (settings.voucher_timeout + cfg.timeout) + 2 * pauses)


def _live_seconds(store: Any) -> float:
    """How long a row stays someone's live send: ``SENDING_MINUTES``, or the longest a send can take and a margin."""
    cfg = jde_ais.AisSettings.from_json(store.get_setting(jde_ais.SETTING_KEY))
    return max(SENDING_MINUTES * 60, send_seconds(jde.load(store), cfg) + jde.RESEND_MARGIN)


def _run_over(store: Any) -> str:
    """When a go-ahead's run is over at the latest (its time, the last send it began, a margin), as a next try: a
    voucher it approved and had not sent by then was left by a run cut short, and the queue sends it (B-1886)."""
    settings = jde.load(store)
    cfg = jde_ais.AisSettings.from_json(store.get_setting(jde_ais.SETTING_KEY))
    seconds = settings.post_minutes * 60 + send_seconds(settings, cfg) + jde.RESEND_MARGIN
    return (dt.datetime.now() + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _fresh_sending(row: dict[str, Any], live: float) -> bool:
    """A row someone is sending now (not left by a stop: claimed less than ``live`` seconds ago): nobody else touches
    it."""
    if row["status"] != SENDING or not row.get("sending_at"):
        return False
    return _at(str(row["sending_at"])) > _clock() - dt.timedelta(seconds=live)


def waiting(store: Any, due_only: bool = False) -> list[dict[str, Any]]:
    """The queue: approved and not finished, without the rows being sent right now; with the vouchers of a go-ahead
    whose run is over without sending them (cut short: AP Coder stopped, ``_run_over``)."""
    live = _live_seconds(store)
    found = [p for p in store.jde_waiting(_now()) if not _fresh_sending(p, live)]
    if due_only:
        now = _now()
        found = [p for p in found if not p.get("next_try") or p["next_try"] <= now]
    return found


# --- Talking to JDE ---------------------------------------------------------------------------------------------------


def _answer(client: jde_ais.AisClient, answer: dict[str, Any]) -> dict[str, Any]:
    spec = jde_ais.spec_of(client.mapping, "voucher_create")
    out = jde_ais.fields_of(spec, answer)
    results = {v: k for k, v in (spec.get("results") or {}).items()}
    out["result"] = results.get(str(out.get("result") or "").upper(), str(out.get("result") or "").lower())
    out["messages"] = jde_ais.jde_messages({"Messages": out.get("messages") or []})
    return out


def _send(client: jde_ais.AisClient, payload: dict[str, Any], mode: str, key: str = "") -> dict[str, Any]:
    spec = jde_ais.spec_of(client.mapping, "voucher_create")
    modes = spec.get("modes") or {}
    sent = {"key": key or payload["key"], "mode": modes.get(mode, mode.upper()), "pay_items": payload["pay_items"],
            "gl_lines": payload["gl_lines"], "matches": payload["matches"], "fingerprint": _hash(payload)}  # fmt: skip
    if "fingerprint" not in (spec.get("inputs") or {}):
        sent.pop("fingerprint")
    # R04110ZA runs in the request: its own wait (Settings → JD Edwards E1), not the connection's 30 seconds (D-182)
    answer = client.call("voucher_create", sent, idempotent=False, timeout=payload.get("_timeout"))
    return _answer(client, answer)


def _status(client: jde_ais.AisClient, key: str) -> dict[str, Any] | None:
    """JDE's voucher under ``key``, None when JDE says it has none; AisError when that can't be said for certain."""
    spec = jde_ais.spec_of(client.mapping, "voucher_status")
    found = jde_ais.fields_of(spec, client.call("voucher_status", {"key": key}))
    said = str(found.get("found") or "").strip().upper()
    if said in YES:
        return found
    if said in NO:
        return None
    raise jde_ais.AisError(jde_ais.ANSWER, f"JD Edwards' voucher status for {key} can't be read (Found: "
                           f"{said or 'blank'}).", sent=True)  # fmt: skip


def _digits(value: Any) -> str:
    return str(value or "").strip().lstrip("0")


def mismatch(found: dict[str, Any], row: dict[str, Any]) -> str:
    """Why JDE's record of the key is not this voucher ("" when it is): the fingerprint when JDE gives one, else the
    supplier, the invoice number and the gross, all three."""
    theirs = str(found.get("fingerprint") or "").strip()
    if theirs:
        return "" if theirs == row.get("payload_hash") else "its fingerprint is another voucher's"
    try:
        expect = json.loads(row.get("expect") or "{}")
    except ValueError:
        expect = {}
    an8, number, gross = found.get("an8"), found.get("invoice_number"), parse_amount(found.get("gross"))
    if not expect or an8 in (None, "") or number in (None, "") or gross is None:
        return "JD Edwards did not say whose voucher it is (supplier, invoice number and gross amount)"
    problems = []
    if _digits(an8) != _digits(expect.get("an8")):
        problems.append(f"supplier {an8}, not {expect.get('an8')}")
    if norm_invoice_number(number) != norm_invoice_number(expect.get("invoice_number")):
        problems.append(f"invoice {number}, not {expect.get('invoice_number')}")
    if all(abs(gross - float(g)) > 0.005 for g in expect.get("gross") or []):
        problems.append(f"gross {gross:,.2f}, not {', '.join(f'{float(g):,.2f}' for g in expect.get('gross') or [])}")
    return "; ".join(problems)


def dry_run(store: Any, batch: int, actor: str | None = None,
            client: jde_ais.AisClient | None = None) -> dict[str, Any]:  # fmt: skip
    """Check the batch's vouchers (here, then in JDE's proof mode when it answers); each invoice's row says how it went.
    {"batch", "report" (``jde.dry_run``'s), "rows": [...], "jde": "" or why JDE did not check them}. A row JDE has or
    may have, or one approved and queued, is never written over."""
    found, problems, invoices, made = _payloads(store, batch)
    going = [i for i in invoices if i["id"] in found]
    rows = {i: (p["pay_items"], p["gl_lines"], p["matches"]) for i, p in found.items()}
    report = jde.dry_run(going, made["settings"], made["lookup"], batch=batch, fx_rates=made["fx_rates"],
                         on=made["on"], prepared=[made["vouchers"][i["id"]] for i in going], rows=rows,
                         site=made["site"])  # fmt: skip
    already = {p["invoice_id"]: p for p in postings(store, batch)}
    jde_note = ""
    own = client is None
    try:
        if client is None:
            client = jde_ais.client(store, need="step2")
    except jde_ais.AisError as exc:
        jde_note, client = str(exc), None
    try:
        for inv in invoices:
            inv_id = inv["id"]
            row = already.get(inv_id)
            if row and (row["status"] not in DRY_RUN_OVER or row.get("maybe_sent")):
                continue  # posted, on its way, or JDE may have it: a dry run changes nothing of it
            key = row["idem_key"] if row else (found[inv_id]["key"] if inv_id in found else
                                               idem_key(inv_id, batch, installation_id(store)))  # fmt: skip
            if inv_id not in problems and (on_hold := _held(store, found[inv_id])):  # (R3, B-1859)
                problems[inv_id] = [f"{on_hold}: dry run again once the hold is lifted"]
            if inv_id in problems:
                _put(store, key, inv_id, batch, only_from=DRY_RUN_OVER, status=REFUSED, payload_hash="",
                     error="; ".join(problems[inv_id]), checked_at=_now(), checked_by=actor, next_try=None)  # fmt: skip
                continue
            payload, error, held = found[inv_id], "", 0
            if client is not None and not jde_note:
                try:
                    answer = _send(client, payload, "proof", key)
                    if answer["result"] == "exists":
                        held = 1  # JDE holds a voucher under this key: never a pass, locked for a person
                        error = (f"already in JD Edwards (voucher {answer.get('document') or 'number not given'}): "
                                 "check it there; it is not sent again")  # fmt: skip
                    elif answer["result"] == "created":
                        held = 1
                        error = "JD Edwards answered CREATED to a dry run: check in JD Edwards before anything else"
                    elif answer["result"] != "valid":
                        error = "; ".join(answer["messages"]) or f"JD Edwards answered {answer['result'] or 'nothing'}"
                except jde_ais.AisError as exc:
                    if exc.kind == jde_ais.JDE:
                        error = "; ".join(exc.messages) or str(exc)
                    else:
                        jde_note = str(exc)
            # passed with its G/L date moved into the open period: said, and the go-ahead confirms it (D-182); a
            # go-ahead given before it is for the voucher it checked then: the queue doesn't send it (B-1886)
            _put(store, key, inv_id, batch, only_from=DRY_RUN_OVER, status=REFUSED if error else CHECKED,
                 error=error or payload["_moved"], payload_hash=_hash(payload), expect=json.dumps(payload["_expect"]),
                 checked_at=_now(), checked_by=actor, maybe_sent=held, next_try=None)  # fmt: skip
    finally:
        if own and client is not None:
            client.close()
    store.log_event("jde_api_dry_run", actor=actor, detail={"batch": batch, "jde": not jde_note})
    return {"batch": batch, "report": report, "rows": status_rows(store, batch), "jde": jde_note}


def _record(store: Any, row: dict[str, Any], answer: dict[str, Any], actor: str | None) -> str:
    """A voucher JDE has: posted, with its number."""
    _put(store, row["idem_key"], row["invoice_id"], row["batch"], status=POSTED, error="", maybe_sent=1,
         document=str(answer.get("document") or ""), doc_type=str(answer.get("doc_type") or ""),
         doc_company=str(answer.get("company") or ""), jde_batch=str(answer.get("batch") or ""),
         next_try=None, verify=0)  # fmt: skip
    # On the invoice too, as the finance hub feed reads it (meta["jde_voucher"]).
    voucher = {"document": str(answer.get("document") or ""), "doc_type": str(answer.get("doc_type") or ""),
               "company": str(answer.get("company") or ""), "posted_at": _now()}  # fmt: skip
    store.set_jde_voucher(row["invoice_id"], voucher)
    store.log_event("jde_voucher_posted", invoice_id=row["invoice_id"], actor=actor, detail={
        "batch": row["batch"], "voucher": f"{answer.get('document')} {answer.get('doc_type')}".strip(),
        "key": row["idem_key"]})  # fmt: skip
    return POSTED


def _take(store: Any, row: dict[str, Any], found: dict[str, Any], actor: str | None) -> str:
    """JDE has a voucher under the row's key (EXISTS, or the status lookup): posted if it is this one; failed and locked
    for a person if it is another; unknown while JDE gives no document number."""
    if not str(found.get("document") or "").strip():
        _put(store, row["idem_key"], row["invoice_id"], row["batch"], status=UNKNOWN, maybe_sent=1,
             error="JD Edwards has this key but gave no voucher number: being checked")  # fmt: skip
        return UNKNOWN
    if why := mismatch(found, row):
        _put(store, row["idem_key"], row["invoice_id"], row["batch"], status=FAILED, maybe_sent=1, next_try=None,
             error=f"JD Edwards holds voucher {found.get('document')} under this voucher's key, but it is not this "
             f"invoice ({why}): check in JD Edwards. AP Coder sends nothing more for it.")  # fmt: skip
        store.log_event(
            "jde_voucher_mismatch",
            invoice_id=row["invoice_id"],
            actor=actor,
            detail={"batch": row["batch"], "key": row["idem_key"], "document": found.get("document")},
        )
        return FAILED
    return _record(store, row, found, actor)


def _later(attempts: int, sent: bool = True) -> str:
    """When to try again after ``attempts`` tries: a voucher never sent stays on the short waits (5, then 15
    minutes), one that may be in JD Edwards goes on to hourly (B-1198)."""
    steps = RETRY_MINUTES if sent else RETRY_MINUTES[:2]
    wait = steps[min(max(attempts, 1), len(steps)) - 1]
    return (dt.datetime.now() + dt.timedelta(minutes=wait)).isoformat(timespec="seconds")


def _wait(store: Any, row: dict[str, Any], attempts: int, why: str = "") -> str:
    """JDE can't be asked now: an approved voucher never sent is queued (``why``, when said); any other row stays as
    it is (its next try only)."""
    later = _later(attempts, bool(row.get("maybe_sent")))
    if row["status"] == CHECKED and not row.get("maybe_sent"):
        _put(store, row["idem_key"], row["invoice_id"], row["batch"], only_from=(CHECKED,), status=QUEUED,
             attempts=attempts, next_try=later, **({"error": why} if why else {}))  # fmt: skip
        return QUEUED
    _put(store, row["idem_key"], row["invoice_id"], row["batch"], attempts=attempts, next_try=later)
    return row["status"]


def _in_flight(row: dict[str, Any], wait_for: float, longest: float) -> str:
    """When a "not there" from JDE is believed for this row ("" now): a send whose answer never came may still be
    running in JDE (R04110ZA), its key not kept yet, until ``wait_for`` seconds after its answer was lost
    (``lost_at``); when that is not known (left *sending* by a stop, or lost before AP Coder kept it), after the
    longest the send could take (``longest`` seconds from ``sending_at``) and ``wait_for`` (D-182). Compared in
    UTC (B-1887); said in local time, as the row's next try."""
    if row["status"] not in (UNKNOWN, SENDING) or wait_for <= 0:
        return ""
    if row["status"] == UNKNOWN and row.get("lost_at"):
        ended = _at(str(row["lost_at"]))
    elif row.get("sending_at"):
        ended = _at(str(row["sending_at"])) + dt.timedelta(seconds=longest)
    else:
        return ""
    after = ended + dt.timedelta(seconds=wait_for)
    return after.astimezone().replace(tzinfo=None).isoformat(timespec="seconds") if after > _clock() else ""


def _lookup(store: Any, client: jde_ais.AisClient, row: dict[str, Any], actor: str | None, wait_for: float = 0.0,
            longest: float = 0.0) -> tuple[str | None, dict[str, Any] | None]:  # fmt: skip
    """Ask JDE for the row's key: (the status it now has, None) when JDE has a voucher under it, or says it has none
    while the row's send may still be running there (``_in_flight``: left as it is, asked again then); (None, the row
    cleared of maybe_sent) when JDE says it has none. AisError when JDE can't say."""
    found = _status(client, row["idem_key"])
    if found is not None:
        return _take(store, row, found, actor), None
    if after := _in_flight(row, wait_for, longest):  # never sent again, nor unlocked, before JDE could have finished it
        _put(store, row["idem_key"], row["invoice_id"], row["batch"], rev=row["rev"], next_try=after)
        return row["status"], None
    if not _put(store, row["idem_key"], row["invoice_id"], row["batch"], rev=row["rev"], maybe_sent=0, verify=0):
        return None, None  # changed meanwhile (another queue run, an undo): left to it
    return None, store.jde_posting(row["idem_key"])


def _look(store: Any, client: jde_ais.AisClient, row: dict[str, Any], actor: str | None,
          run: _Run) -> tuple[str, dict[str, Any] | None, jde_ais.AisError | None]:  # fmt: skip
    """A row asked for by its key before anything is sent: (its status, None, None) when that settles it for now (JDE
    has it, or may still be making it); ("", the row cleared of maybe_sent, None) when JDE has none, so it may be
    sent; ("skipped", None, None) when it changed meanwhile; (its status, None, the error) when JDE can't say (only its
    next try is set)."""
    try:
        status, cleared = _lookup(store, client, row, actor, run.resend, run.longest)
    except jde_ais.AisError as exc:
        return _wait(store, row, int(row.get("attempts") or 0) + 1), None, exc
    if status is not None:
        return status, None, None
    return ("skipped", None, None) if cleared is None else ("", cleared, None)


def _held(store: Any, payload: dict[str, Any]) -> str:
    """Why the voucher's vendor is on hold now, here or in JD Edwards ("" when not, ``vendors.voucher_hold``)."""
    return voucher_hold(store, payload.get("_vendor"), (payload["pay_items"] or [{}])[0].get("VLAN8"))


def _deliver(store: Any, client: jde_ais.AisClient, row: dict[str, Any], payload: dict[str, Any] | None,
             actor: str | None) -> tuple[str, jde_ais.AisError | None]:  # fmt: skip
    """Send one approved voucher JDE has none of (never sent, or asked for by its key first); its new status."""
    attempts = int(row.get("attempts") or 0) + 1
    if payload is None:  # changed since it was approved, and JDE has none under its key: held back, its go-ahead kept
        # for month end (B-1888: an upgrade's new VLEDBT / VLEDTN, a settings change; not "refused by JD Edwards")
        if not _put(store, row["idem_key"], row["invoice_id"], row["batch"], rev=row["rev"], status=CHECKED,
                    next_try=None, error="Not sent: it changed in AP Coder since its go-ahead (an update of AP Coder "
                    "or of its JD Edwards settings), and JD Edwards hasn't it: dry run again, then give the "
                    "go-ahead"):  # fmt: skip
            return "skipped", None
        return CHECKED, None
    claimed = _claim(store, row, attempts)
    if claimed is None:
        return "skipped", None
    try:
        answer = _send(client, payload, "final", claimed["idem_key"])
    except jde_ais.AisError as exc:
        return _failed_send(store, client, claimed, row, exc, actor), exc
    return _answered(store, client, claimed, answer, actor), None


def _failed_send(store: Any, client: jde_ais.AisClient, row: dict[str, Any], before: dict[str, Any],
                 exc: jde_ais.AisError, actor: str | None) -> str:  # fmt: skip
    """A send that did not come back with an answer: what the row now is."""
    key, inv, batch = row["idem_key"], row["invoice_id"], row["batch"]
    if exc.kind == jde_ais.JDE:  # JDE refused it (HTTP 500, its error shape): did it make nothing? Ask by the key.
        try:
            status, cleared = _lookup(store, client, row, actor)
        except jde_ais.AisError as lost:
            _put(store, key, inv, batch, status=UNKNOWN, error=f"{exc} (and JD Edwards could not be asked whether it "
                 f"made it: {lost})", next_try=_later(row["attempts"]), lost_at=_stamp())  # fmt: skip
            return UNKNOWN
        if status is not None:
            return status
        _put(store, key, inv, batch, status=FAILED, error="; ".join(exc.messages) or str(exc), next_try=None)
        store.log_event("jde_voucher_refused", invoice_id=inv, actor=actor, detail={"batch": batch, "key": key})
        return FAILED
    if not exc.sent or exc.kind in (jde_ais.DENIED, jde_ais.NOT_FOUND, jde_ais.AUTH):
        # Certainly never processed: not connected, AIS busy (503, before processing), the sign-in or the permission
        # refused before the orchestration ran. As it was before the try, waiting in the queue.
        _put(store, key, inv, batch, status=QUEUED, maybe_sent=int(before.get("maybe_sent") or 0), error=str(exc),
             next_try=_later(row["attempts"]))  # fmt: skip
        return QUEUED
    # Sent and no answer (a timeout, the connection cut, 502 / 504, an answer that can't be read): JDE may have it.
    _put(store, key, inv, batch, status=UNKNOWN, error=str(exc), next_try=_later(row["attempts"]), lost_at=_stamp())
    return UNKNOWN


def _answered(store: Any, client: jde_ais.AisClient, row: dict[str, Any], answer: dict[str, Any],
              actor: str | None) -> str:  # fmt: skip
    key, inv, batch = row["idem_key"], row["invoice_id"], row["batch"]
    result = answer["result"]
    if result == "created" and answer.get("document"):
        return _record(store, row, answer, actor)
    if result == "exists" and answer.get("document"):
        return _take(store, row, answer, actor)
    if result == "error":  # the orchestration's own answer: nothing made
        _put(store, key, inv, batch, status=FAILED, maybe_sent=0, next_try=None,
             error="; ".join(answer["messages"]) or "JD Edwards answered ERROR")  # fmt: skip
        store.log_event("jde_voucher_refused", invoice_id=inv, actor=actor, detail={"batch": batch, "key": key})
        return FAILED
    said = f"JD Edwards answered {result.upper() or 'nothing'}" + (" without a voucher number" if result in (
        "created", "exists") else "")  # fmt: skip
    _put(store, key, inv, batch, status=UNKNOWN, error=f"{said}: being checked", next_try=_later(row["attempts"]),
         lost_at=_stamp())  # fmt: skip
    if result in ("created", "exists"):
        current = store.jde_posting(key)
        if current is not None:
            with contextlib.suppress(jde_ais.AisError):
                found = _status(client, key)
                if found is not None and str(found.get("document") or "").strip():
                    return _take(store, current, found, actor)
    return UNKNOWN


def _client(store: Any) -> tuple[jde_ais.AisClient | None, str]:
    try:
        return jde_ais.client(store, need="step2"), ""
    except jde_ais.AisOff:
        raise
    except jde_ais.AisError as exc:
        return None, str(exc)


def post(store: Any, batch: int, actor: str | None = None, client: jde_ais.AisClient | None = None,
         not_loaded: str = "", progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:  # fmt: skip
    """The approval: send the batch's checked vouchers, unchanged since their dry run. {"posted", "failed", "queued",
    "unknown", "skipped": [why...], "error"}. Raises AisOff when step 2 is off, ValueError when nothing is ready, and
    FileDownloaded when the batch's ZIP was downloaded and ``not_loaded`` (what the person checked) is blank.
    ``progress``: called after each voucher with {"done", "total", "posted", "failed", "queued", "unknown", "note"}."""
    if not jde_ais.load(store).step2:
        raise jde_ais.AisOff("JD Edwards API step 2 (vouchers) is off: nothing is sent.")
    downloaded, not_loaded = downloads(store, batch), " ".join((not_loaded or "").split())
    if downloaded and not not_loaded:
        raise FileDownloaded(downloaded_message(batch, downloaded))
    word = {"not_loaded": not_loaded, "downloaded": f"{downloaded[0]['actor']} {downloaded[0]['at']}"} if (
        downloaded) else {}  # fmt: skip
    found, _problems, _invoices = payloads(store, batch)
    rows = postings(store, batch)
    ready, skipped = [], []
    for row in rows:
        if row["status"] != CHECKED or row.get("maybe_sent"):
            continue
        payload = found.get(row["invoice_id"])
        if payload is None or _hash(payload) != row["payload_hash"]:
            skipped.append(f"AP Coder #{row['invoice_id']}: changed since its dry run (run the dry run again)")
            continue
        ready.append((row, payload))
    if not ready:
        raise ValueError("Nothing in this batch is ready to post: run the dry run first (every voucher that passed it "
                         "and has not changed since is posted).")  # fmt: skip
    approved, over = [], _run_over(store)  # (the queue's once the run is over without sending it, B-1886)
    for row, payload in ready:
        if _put(store, row["idem_key"], row["invoice_id"], batch, rev=row["rev"], approved_at=_now(),
                approved_by=actor, next_try=over):  # fmt: skip
            approved.append((store.jde_posting(row["idem_key"]) or row, payload))
    # the vouchers whose G/L date the dry run moved into the open period, confirmed by this go-ahead (D-182)
    moved = [row["invoice_id"] for row, payload in approved if payload["_moved"]]
    store.log_event("jde_api_approved", actor=actor, detail={"batch": batch, "vouchers": len(approved), **word,
                                                             **({"gl_moved": moved} if moved else {})})  # fmt: skip
    return _run(store, approved, actor, client, look_first=False, skipped=skipped, progress=progress)


def _late(exc: jde_ais.AisError) -> bool:
    """The request reached JDE and its answer did not come back (a timeout, the connection cut, 502 / 504, an answer
    that can't be read): JDE is slow, not down, so the run goes on (B-1857). Not reached at all, AIS busy (503), the
    sign-in or the permission refused: JDE can't be asked now, so the rest wait in the queue."""
    return exc.sent and exc.kind not in (jde_ais.JDE, jde_ais.DENIED, jde_ais.NOT_FOUND, jde_ais.AUTH)


class _Run:
    """One run of sends (``post``, ``retry_queue``, D-182): its settings, its time, each voucher's outcome so far (for
    the job's progress), the late ones to look up, and why it stopped sending, if it did (the rest wait in the
    queue)."""

    def __init__(self, store: Any, total: int, progress: Callable[[dict[str, Any]], None] | None) -> None:
        self.text = store.get_setting(jde.SETTING_KEY)
        self.settings = settings = jde.JdeSettings.from_json(self.text)
        self.refused: tuple[dt.date, int, dt.date, str] | None = None  # (through, invoice, its G/L date, JDE's words)
        # a lost answer's "not there" believed only this long after it was lost (or after the send's longest), B-1880
        cfg = jde_ais.AisSettings.from_json(store.get_setting(jde_ais.SETTING_KEY))
        self.resend, self.longest = settings.resend_seconds, send_seconds(settings, cfg)
        self.minutes, self.began = settings.post_minutes, time.monotonic()
        self.total, self.progress = total, progress
        self.outcome: dict[str, str] = {}  # key -> its status now
        self.skipped: set[str] = set()  # the keys it left as they changed meanwhile (done too, B-1891)
        self.late: dict[str, float] = {}  # key -> when its answer was given up on (time.monotonic)
        self.in_a_row = 0
        self.why = ""
        self.note = "Looking the vouchers up in JD Edwards…"  # what the run is doing (the job's progress)

    def over(self) -> bool:
        """Whether the run sends nothing more (JDE can't be reached, too many late answers, or its time is out)."""
        if not self.why and time.monotonic() - self.began > self.minutes * 60:
            self.why = (f"The run's time ({self.minutes:g} minutes, Settings → JD Edwards E1) ran out: the rest wait "
                        "in the queue, sent in the background every few minutes.")  # fmt: skip
        return bool(self.why)

    def counts(self) -> dict[str, int]:
        found = {POSTED: 0, FAILED: 0, QUEUED: 0, UNKNOWN: 0, CHECKED: 0}  # checked: held back, for a new dry run
        for status in self.outcome.values():
            found[status if status in found else UNKNOWN if status == SENDING else QUEUED] += 1
        return found

    def closed(self, store: Any, payload: dict[str, Any]) -> str:
        """Why the voucher's G/L date can't be posted now ("" when it can): its period closed since its dry run (the
        open period moved on in Settings: read again when it changes), or JD Edwards refused one of this run's vouchers
        as in a closed period, dated in the same month or later (D-182)."""
        text = store.get_setting(jde.SETTING_KEY)
        if text != self.text:
            self.text, self.settings = text, jde.JdeSettings.from_json(text)
        day = jde.from_julian((payload["pay_items"] or [{}])[0].get("VLDGJ"))
        if day is None:
            return ""
        if self.refused and day <= self.refused[0]:
            _, invoice, refused, words = self.refused
            return (f"JD Edwards refused AP Coder #{invoice}'s G/L date {refused} as in a closed period ({words}): "
                    f"this one, dated {day}, waits. Set JD Edwards' open period in Settings → JD Edwards E1, then dry "
                    "run again")  # fmt: skip
        if jde.period_problem(day, self.settings):
            return (
                f"the period of its G/L date {day} was closed since its dry run (Settings → JD Edwards E1's open "
                f"period: from {self.settings.open_from or '…'} to {self.settings.open_to or '…'}): dry run again"
            )
        return ""

    def refusal(self, row: dict[str, Any] | None, payload: dict[str, Any]) -> None:
        """JD Edwards refused this voucher: as in a closed period, the rest of its month and before it wait."""
        words = str((row or {}).get("error") or "")
        day = jde.from_julian((payload["pay_items"] or [{}])[0].get("VLDGJ"))
        if day is None or not PERIOD_REFUSED.search(words):
            return
        through = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)  # its month's end
        if self.refused is None or through > self.refused[0]:
            self.refused = (through, row["invoice_id"], day, words[:200])  # type: ignore[index]

    def done(self, row: dict[str, Any], status: str) -> None:
        if status != "skipped":
            self.outcome[row["idem_key"]] = status
        else:
            self.skipped.add(row["idem_key"])
        if self.progress is not None:
            done = len(self.outcome.keys() | self.skipped)
            self.progress({"done": done, "total": self.total, **self.counts(), "note": self.note})

    def after(self, row: dict[str, Any], exc: jde_ais.AisError | None, sent: bool) -> bool:
        """Whether JDE may still be asked after this voucher's call (``sent``: a send, not a lookup)."""
        if exc is None or exc.kind == jde_ais.JDE:
            self.in_a_row = 0
            return True
        if not _late(exc):
            self.why = str(exc)
            return False
        if sent:
            self.late[row["idem_key"]] = time.monotonic()
        self.in_a_row += 1
        if self.in_a_row < LATE_STOP:
            return True
        self.why = (f"JD Edwards answered {LATE_STOP} vouchers in a row late ({exc}): the rest wait in the queue, "
                    "sent in the background every few minutes.")  # fmt: skip
        return False


def _run(store: Any, ready: list[tuple[dict[str, Any], dict[str, Any] | None]], actor: str | None,
         client: jde_ais.AisClient | None, look_first: bool, skipped: list[str] | None = None, error: str = "",
         progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:  # fmt: skip
    """Each voucher its own outcome (D-182): the lookups first, all of them (each row JDE has or may have, asked for by
    its key), then the sends, one after another, going on past a late answer; then the late ones looked up once their
    answer is ``LOOK_AFTER`` seconds old (a younger one: the queue). A row the run doesn't reach waits in the queue."""
    own = client is None
    if client is None:
        client, error = _client(store)
    run = _Run(store, len(ready), progress)
    if client is None:
        run.why = error
    skipped = list(skipped or [])
    payloads_of = {row["idem_key"]: payload for row, payload in ready}

    def changed(row: dict[str, Any]) -> None:
        skipped.append(f"AP Coder #{row['invoice_id']}: not sent: it changed meanwhile (sent from elsewhere, its batch "
                       "undone, or its JD Edwards file downloaded)")  # fmt: skip
        run.done(row, "skipped")

    def send(row: dict[str, Any], payload: dict[str, Any] | None) -> None:
        """One voucher JDE has none of, its G/L date's period and its vendor's hold read just before: its period
        closed since (B-1861), it waits for a new dry run; put on hold since its approval (R3, B-1859), it waits in
        the queue; each saying why."""
        if payload is not None and (closed := run.closed(store, payload)):
            if _put(store, row["idem_key"], row["invoice_id"], row["batch"], rev=row["rev"], status=CHECKED,
                    next_try=None, error=f"Not sent: {closed}"):  # fmt: skip
                skipped.append(f"AP Coder #{row['invoice_id']}: not sent: {closed}")
                run.done(row, CHECKED)
            else:
                changed(row)
            return
        if payload is not None and (held := _held(store, payload)):
            if _put(store, row["idem_key"], row["invoice_id"], row["batch"], rev=row["rev"], status=QUEUED,
                    next_try=_later(int(row.get("attempts") or 0) + 1, sent=False),
                    error=f"Not sent: {held}: it waits in the queue until the hold is lifted"):  # fmt: skip
                skipped.append(f"AP Coder #{row['invoice_id']}: not sent: {held}")
                run.done(row, QUEUED)
            else:
                changed(row)
            return
        status, exc = _deliver(store, client, row, payload, actor)
        run.after(row, exc, sent=True)
        if status == FAILED and payload is not None:
            run.refusal(store.jde_posting(row["idem_key"]), payload)
        if status == "skipped":
            changed(row)
        else:
            run.done(row, status)

    try:
        sends = []
        for row, payload in ready:
            if not (look_first or row.get("maybe_sent") or row.get("verify") or row["status"] != CHECKED):
                sends.append((row, payload))
            elif run.over():  # never asked: its tries and its place stay (B-1198)
                run.done(row, _wait(store, row, int(row.get("attempts") or 0), run.why))
            else:
                status, cleared, exc = _look(store, client, row, actor, run)
                run.after(row, exc, sent=False)
                if status == "skipped":
                    changed(row)
                elif cleared is not None:
                    sends.append((cleared, payload))
                else:
                    run.done(row, status)
        run.note = "Posting the vouchers to JD Edwards…"
        for row, payload in sends:
            if run.over():
                run.done(row, _wait(store, row, int(row.get("attempts") or 0), run.why))
            else:
                send(row, payload)
        run.note = "Looking up the vouchers JD Edwards answered late…"
        for key, at in list(run.late.items()):  # looked up, and sent again only once JDE could have finished it
            if run.over() or time.monotonic() - at < LOOK_AFTER:
                continue
            row = store.jde_posting(key)
            if row is None or row["status"] != UNKNOWN:
                continue
            status, cleared, exc = _look(store, client, row, actor, run)
            run.after(row, exc, sent=False)
            if cleared is not None:
                send(cleared, payloads_of.get(key))
            elif status == "skipped":
                changed(row)
            else:
                run.done(row, status)
    except BaseException:  # cut short (an error, AP Coder stopping): what it had not reached waits in the queue now
        for row, _payload in ready:  # (B-1886)
            if row["status"] == CHECKED and row["idem_key"] not in run.outcome:
                _put(store, row["idem_key"], row["invoice_id"], row["batch"], only_from=(CHECKED,), rev=row["rev"],
                     status=QUEUED, next_try=None, error="Not sent: the posting run stopped before it: it waits in "
                     "the queue")  # fmt: skip
        raise
    finally:
        if own and client is not None:
            client.close()
    counts = run.counts()
    return {"posted": counts[POSTED], "failed": counts[FAILED], "queued": counts[QUEUED],
            "unknown": counts[UNKNOWN], "held": counts[CHECKED], "error": run.why or error,
            "skipped": skipped}  # fmt: skip


def retry_queue(store: Any, actor: str | None = None, client: jde_ais.AisClient | None = None,
                batch: int | None = None,
                progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:  # fmt: skip
    """Send the vouchers waiting (queued, unknown, or left *sending* by a stop), each asked for by its key first
    (``progress``: as ``post``'s)."""
    rows = [p for p in waiting(store) if batch is None or p["batch"] == batch]
    ready: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    skipped: list[str] = []
    built: dict[int, dict[int, dict[str, Any]] | ValueError] = {}
    for row in rows:
        if row["batch"] not in built:  # once per batch, not once per voucher waiting (B-1884)
            try:
                built[row["batch"]] = payloads(store, row["batch"])[0]
            except ValueError as exc:
                built[row["batch"]] = exc
        found = built[row["batch"]]
        if isinstance(found, ValueError):
            skipped.append(f"AP Coder #{row['invoice_id']}: {found}")
            continue
        payload = found.get(row["invoice_id"])
        # Changed since it was approved: JDE may have it all the same (sent before the change): asked for by its key,
        # never sent changed.
        ready.append((row, payload if payload is not None and _hash(payload) == row["payload_hash"] else None))
    if not ready:
        return {"posted": 0, "failed": 0, "queued": 0, "unknown": 0, "held": 0, "error": "", "skipped": skipped}
    return _run(store, ready, actor, client, look_first=True, skipped=skipped, progress=progress)


# --- For the pages ----------------------------------------------------------------------------------------------------


def status_rows(store: Any, batch: int) -> list[dict[str, Any]]:
    """The batch's vouchers as the Exports page lists them (one its go-ahead's run held back, for a new dry run: said
    so, B-1888)."""
    invoices = {i["id"]: i for i in store.get_invoices(store.batch_invoice_ids(batch))}
    out = []
    for p in postings(store, batch):
        final = (invoices.get(p["invoice_id"]) or {}).get("final_output") or {}
        approved = p["approved_at"] or ""  # held back: approved since its dry run, and its go-ahead's run over
        held = p["status"] == CHECKED and bool(approved) and approved >= (p["checked_at"] or "") and not p["next_try"]
        out.append({
            **p, "label": HELD_BACK if held else LABELS.get(p["status"], p["status"]),
            "tone": "warn" if held else TONES.get(p["status"], "gray"),
            "vendor": final.get("vendor_name") or "", "number": final.get("invoice_number") or "",
            "total": final.get("grand_total"), "currency": final.get("currency") or "",
            "voucher": " ".join(x for x in (p["document"], p["doc_type"], p["doc_company"]) if x),
            "locked": p["status"] in LOCKED or bool(p.get("maybe_sent")),
        })  # fmt: skip
    return out


def summary(store: Any, batch: int) -> dict[str, int]:
    found: dict[str, int] = {}
    for p in postings(store, batch):
        found[p["status"]] = found.get(p["status"], 0) + 1
    return found
