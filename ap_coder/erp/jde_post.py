"""JD Edwards API step 2 (D-104): an approved JD Edwards batch posted as vouchers through the voucher orchestration,
next to the batch files, which stay the fallback.

The flow, per export batch made in the JD Edwards format (Exports):

1. **Dry run** (``dry_run``, a Clerk or more): each voucher's rows exactly as the Z-files would hold them
   (``jde.voucher_rows``: the same field mapping), checked here (``jde.validate``) and, when JDE answers, by the
   voucher orchestration in proof mode (``ProcessingMode`` PROOF: nothing is committed). Each invoice gets a row in
   ``jde_postings`` (``_TABLE``): *checked* (with the fingerprint of what was shown, ``payload_hash``) or *refused*
   (the reasons).
2. **Approval** (``post``, an Approver or Admin): only what was checked, unchanged since (the same fingerprint), is
   sent, in FINAL mode, one voucher at a time. The idempotency key is ``APC-<invoice id>-<batch>`` (with the batch
   number VLEDBT and the transaction number VLEDTN = APC + invoice id, the Z-tables' own keys); the orchestration
   keeps one voucher per key and answers EXISTS for a key it has seen.
3. **The result is kept per invoice**: *posted* with JDE's voucher number, type, company and batch (also in the
   invoice's ``meta["jde_voucher"]``: ``{document, doc_type, company, posted_at}``, for the finance hub feed);
   *failed* with JDE's own messages (fix, dry run again, approve again); *queued* when JDE could not be reached (the
   request was never sent), *unknown* when it may have been (a timeout after sending).
4. **The queue** (``retry_queue``: the button on Exports, ``python -m ap_coder jde status --retry``, and every five
   minutes in the background): before anything is sent again, the voucher status orchestration is asked for the key;
   found, it is *posted*; not found, the same voucher (the same fingerprint, else it waits for a new dry run) is sent
   again with the same key. **Never posted twice**: a *posted* row is never sent again, a batch with a voucher
   posted, sending or unknown can't be undone, and its ZIP of batch files leaves the posted vouchers out.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
from collections.abc import Iterator
from typing import Any

from . import jde, jde_ais

CHECKED, REFUSED, QUEUED, SENDING, POSTED, FAILED, UNKNOWN = (
    "checked", "refused", "queued", "sending", "posted", "failed", "unknown",
)  # fmt: skip
LABELS = {CHECKED: "Dry run passed", REFUSED: "Refused in the dry run", QUEUED: "Waiting: JD Edwards not reached",
          SENDING: "Being sent", POSTED: "Posted", FAILED: "Refused by JD Edwards",
          UNKNOWN: "Sent, answer not received: being checked"}  # fmt: skip
TONES = {CHECKED: "ok", REFUSED: "err", QUEUED: "warn", SENDING: "warn", POSTED: "ok", FAILED: "err", UNKNOWN: "warn"}
IN_FLIGHT = (QUEUED, SENDING, UNKNOWN)  # approved and not finished: the queue
LOCKED = (POSTED, SENDING, UNKNOWN)  # the batch can't be undone: JDE may hold the voucher
RETRY_MINUTES = (5, 15, 60)  # the queue's waits after each try that did not reach JDE (then hourly)


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def idem_key(invoice_id: int, batch: int) -> str:
    return f"APC-{int(invoice_id)}-{int(batch)}"


def _hash(payload: dict[str, Any]) -> str:
    body = {k: payload.get(k) for k in ("pay_items", "gl_lines", "matches")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


# --- What is sent ---------------------------------------------------------------------------------------------------


def _batch(store: Any, batch: int) -> dict[str, Any]:
    known = next((b for b in store.export_batches() if b["id"] == batch), None)
    if known is None or known["undone_at"]:
        raise ValueError(f"Batch {batch} is unknown or undone.")
    if known["format"] != jde.FORMAT:
        raise ValueError(f"Batch {batch} was made as {known['format']}: only a JD Edwards batch goes through the API.")
    return known


def payloads(store: Any, batch: int) -> tuple[dict[int, dict[str, Any]], dict[int, list[str]], list[dict[str, Any]]]:
    """({invoice: what the voucher orchestration is sent}, {invoice: why it can't go}, the batch's invoices)."""
    known = _batch(store, batch)
    settings, lookup = jde.load(store), store.vendor_ids()
    invoices = store.get_invoices(store.batch_invoice_ids(batch))
    blocking = jde.settings_problems(settings)
    problems = jde.validate(invoices, settings, lookup)
    on = dt.date.fromisoformat(known["created_at"][:10])
    out: dict[int, dict[str, Any]] = {}
    for inv in invoices:
        if blocking:
            problems.setdefault(inv["id"], []).extend(blocking)
        if inv["id"] in problems:
            continue
        an8 = jde.resolve_an8(inv.get("final_output") or {}, lookup, settings)
        headers, dists, matches = jde.voucher_rows(inv, an8, settings, batch=batch, fx_rates=store.fx_rates(), on=on)
        out[inv["id"]] = {"key": idem_key(inv["id"], batch), "pay_items": headers, "gl_lines": dists,
                          "matches": matches}  # fmt: skip
    return out, problems, invoices


# --- The postings table ----------------------------------------------------------------------------------------------


# One row per voucher sent (or checked) through the API. Made here when first used, so a database stays one an older
# AP Coder opens as before (the schema's version is unchanged).
_TABLE = (
    "CREATE TABLE IF NOT EXISTS jde_postings (idem_key TEXT PRIMARY KEY, invoice_id INTEGER NOT NULL, "
    "batch INTEGER NOT NULL, status TEXT NOT NULL, payload_hash TEXT NOT NULL DEFAULT '', checked_at TEXT, "
    "checked_by TEXT, approved_at TEXT, approved_by TEXT, document TEXT NOT NULL DEFAULT '', "
    "doc_type TEXT NOT NULL DEFAULT '', doc_company TEXT NOT NULL DEFAULT '', jde_batch TEXT NOT NULL DEFAULT '', "
    "error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, next_try TEXT, updated_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS jde_postings_invoice ON jde_postings (invoice_id)",
)


@contextlib.contextmanager
def _conn(store: Any) -> Iterator[Any]:
    with store._conn() as conn:
        for sql in _TABLE:  # (cheap: a database put back from a backup may be without it)
            conn.execute(sql)
        yield conn


def postings(store: Any, batch: int | None = None, statuses: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM jde_postings WHERE 1 = 1", []
    if batch is not None:
        sql += " AND batch = ?"
        args.append(batch)
    if statuses:
        sql += f" AND status IN ({', '.join('?' * len(statuses))})"
        args += list(statuses)
    with _conn(store) as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY batch, invoice_id", args)]


def _put(store: Any, key: str, invoice_id: int, batch: int, **values: Any) -> None:
    values["updated_at"] = _now()
    with _conn(store) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO jde_postings (idem_key, invoice_id, batch, status, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, invoice_id, batch, values.get("status", CHECKED), _now()),
        )
        sets = ", ".join(f"{k} = ?" for k in values)
        conn.execute(f"UPDATE jde_postings SET {sets} WHERE idem_key = ?", (*values.values(), key))


def documents_of(store: Any, invoice_id: int | None) -> list[str]:
    """JDE's voucher numbers AP Coder posted for this invoice (the live duplicate check leaves them out)."""
    with _conn(store) as conn:
        return [r[0] for r in conn.execute("SELECT document FROM jde_postings WHERE invoice_id = ? AND status = ? "
                                           "AND document != ''", (invoice_id, POSTED))]  # fmt: skip


def posted_ids(store: Any, batch: int) -> set[int]:
    return {p["invoice_id"] for p in postings(store, batch, (POSTED,))}


def undo_problem(store: Any, batch: int) -> str:
    """Why the batch can't be undone ("" when it can): JDE has, or may have, one of its vouchers."""
    locked = postings(store, batch, LOCKED)
    if not locked:
        return ""
    return (f"Batch {batch} can't be undone: {len(locked)} of its vouchers {'is' if len(locked) == 1 else 'are'} in "
            "JD Edwards (or being checked). Reverse them in JD Edwards (void the voucher) instead.")  # fmt: skip


def forget_batch(store: Any, batch: int) -> None:
    """The batch was undone: its dry runs and queued vouchers are dropped (none was posted, ``undo_problem``)."""
    with _conn(store) as conn:
        conn.execute(f"DELETE FROM jde_postings WHERE batch = ? AND status NOT IN ({', '.join('?' * len(LOCKED))})",
                     (batch, *LOCKED))  # fmt: skip


def waiting(store: Any, due_only: bool = False) -> list[dict[str, Any]]:
    found = postings(store, statuses=IN_FLIGHT)
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


def _send(client: jde_ais.AisClient, payload: dict[str, Any], mode: str) -> dict[str, Any]:
    spec = jde_ais.spec_of(client.mapping, "voucher_create")
    modes = spec.get("modes") or {}
    answer = client.call("voucher_create", {**payload, "mode": modes.get(mode, mode.upper())}, idempotent=False)
    return _answer(client, answer)


def _status(client: jde_ais.AisClient, key: str) -> dict[str, Any] | None:
    spec = jde_ais.spec_of(client.mapping, "voucher_status")
    found = jde_ais.fields_of(spec, client.call("voucher_status", {"key": key}))
    return found if str(found.get("found") or "").strip().upper() in ("1", "Y", "YES", "TRUE") else None


def dry_run(store: Any, batch: int, actor: str | None = None,
            client: jde_ais.AisClient | None = None) -> dict[str, Any]:  # fmt: skip
    """Check the batch's vouchers (here, then in JDE's proof mode when it answers); each invoice's row says how it went.
    {"batch", "report" (``jde.dry_run``'s), "rows": [...], "jde": "" or why JDE did not check them}."""
    found, problems, invoices = payloads(store, batch)
    settings = jde.load(store)
    known = _batch(store, batch)
    report = jde.dry_run([i for i in invoices if i["id"] in found], settings, store.vendor_ids(), batch=batch,
                         fx_rates=store.fx_rates(), on=dt.date.fromisoformat(known["created_at"][:10]))  # fmt: skip
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
            key = idem_key(inv_id, batch)
            if already.get(inv_id, {}).get("status") in (*LOCKED, QUEUED):
                continue  # posted, or approved and on its way: a dry run changes nothing of it
            if inv_id in problems:
                _put(store, key, inv_id, batch, status=REFUSED, error="; ".join(problems[inv_id]), payload_hash="",
                     checked_at=_now(), checked_by=actor)  # fmt: skip
                continue
            payload, error = found[inv_id], ""
            if client is not None and not jde_note:
                try:
                    answer = _send(client, payload, "proof")
                    if answer["result"] not in ("valid", "exists", "created"):
                        error = "; ".join(answer["messages"]) or f"JD Edwards answered {answer['result'] or 'nothing'}"
                except jde_ais.AisError as exc:
                    if exc.kind == jde_ais.JDE:
                        error = "; ".join(exc.messages) or str(exc)
                    else:
                        jde_note = str(exc)
            _put(store, key, inv_id, batch, status=REFUSED if error else CHECKED, error=error,
                 payload_hash=_hash(payload), checked_at=_now(), checked_by=actor)  # fmt: skip
    finally:
        if own and client is not None:
            client.close()
    store.log_event("jde_api_dry_run", actor=actor, detail={"batch": batch, "jde": not jde_note})
    return {"batch": batch, "report": report, "rows": status_rows(store, batch), "jde": jde_note}


def _record(store: Any, row: dict[str, Any], answer: dict[str, Any], actor: str | None) -> str:
    """A voucher JDE has: posted, with its number."""
    _put(store, row["idem_key"], row["invoice_id"], row["batch"], status=POSTED, error="",
         document=str(answer.get("document") or ""), doc_type=str(answer.get("doc_type") or ""),
         doc_company=str(answer.get("company") or ""), jde_batch=str(answer.get("batch") or ""),
         next_try=None)  # fmt: skip
    # On the invoice too, as the finance hub feed reads it (meta["jde_voucher"]).
    voucher = {"document": str(answer.get("document") or ""), "doc_type": str(answer.get("doc_type") or ""),
               "company": str(answer.get("company") or ""), "posted_at": _now()}  # fmt: skip
    with _conn(store) as conn:
        conn.execute("UPDATE invoices SET meta = json_set(CASE WHEN json_valid(meta) THEN meta ELSE '{}' END, "
                     "'$.jde_voucher', json(?)) WHERE id = ?", (json.dumps(voucher), row["invoice_id"]))  # fmt: skip
    store.log_event("jde_voucher_posted", invoice_id=row["invoice_id"], actor=actor, detail={
        "batch": row["batch"], "voucher": f"{answer.get('document')} {answer.get('doc_type')}".strip(),
        "key": row["idem_key"]})  # fmt: skip
    return POSTED


def _later(attempts: int) -> str:
    wait = RETRY_MINUTES[min(attempts, len(RETRY_MINUTES)) - 1] if attempts else RETRY_MINUTES[0]
    return (dt.datetime.now() + dt.timedelta(minutes=wait)).isoformat(timespec="seconds")


def _deliver(store: Any, client: jde_ais.AisClient | None, row: dict[str, Any], payload: dict[str, Any],
             actor: str | None, look_first: bool) -> tuple[str, jde_ais.AisError | None]:  # fmt: skip
    """Send one approved voucher (asking first whether JDE has it already, when it may have); its new status."""
    key, attempts = row["idem_key"], int(row.get("attempts") or 0) + 1
    if client is None:
        _put(store, key, row["invoice_id"], row["batch"], status=QUEUED, next_try=_later(attempts))
        return QUEUED, None
    try:
        if look_first and (found := _status(client, key)):
            return _record(store, row, found, actor), None
        _put(store, key, row["invoice_id"], row["batch"], status=SENDING, attempts=attempts)
        answer = _send(client, payload, "final")
    except jde_ais.AisError as exc:
        if exc.kind == jde_ais.JDE:
            _put(store, key, row["invoice_id"], row["batch"], status=FAILED, error="; ".join(exc.messages) or str(exc),
                 next_try=None)  # fmt: skip
            store.log_event("jde_voucher_refused", invoice_id=row["invoice_id"], actor=actor,
                            detail={"batch": row["batch"], "key": key})  # fmt: skip
            return FAILED, None
        # Sent and no answer (a timeout, the connection cut, an answer that can't be read): JDE may have it. Refused
        # before the orchestration ran (busy, the sign-in, no permission, no such orchestration): it has not.
        status = UNKNOWN if exc.sent and exc.kind not in (jde_ais.DENIED, jde_ais.NOT_FOUND, jde_ais.AUTH) else QUEUED
        _put(store, key, row["invoice_id"], row["batch"], status=status, error=str(exc), attempts=attempts,
             next_try=_later(attempts))  # fmt: skip
        return status, exc
    if answer["result"] in ("created", "exists") and answer.get("document"):
        return _record(store, row, answer, actor), None
    _put(store, key, row["invoice_id"], row["batch"], status=FAILED, next_try=None,
         error="; ".join(answer["messages"]) or f"JD Edwards answered {answer['result'] or 'nothing'}")  # fmt: skip
    return FAILED, None


def _client(store: Any) -> tuple[jde_ais.AisClient | None, str]:
    try:
        return jde_ais.client(store, need="step2"), ""
    except jde_ais.AisOff:
        raise
    except jde_ais.AisError as exc:
        return None, str(exc)


def post(store: Any, batch: int, actor: str | None = None,
         client: jde_ais.AisClient | None = None) -> dict[str, Any]:  # fmt: skip
    """The approval: send the batch's checked vouchers, unchanged since their dry run. {"posted", "failed", "queued",
    "unknown", "skipped": [why...], "error"}. Raises AisOff when step 2 is off, ValueError when nothing is ready."""
    if not jde_ais.load(store).step2:
        raise jde_ais.AisOff("JD Edwards API step 2 (vouchers) is off: nothing is sent.")
    found, _problems, _invoices = payloads(store, batch)
    rows = postings(store, batch)
    ready, skipped = [], []
    for row in rows:
        if row["status"] != CHECKED:
            continue
        payload = found.get(row["invoice_id"])
        if payload is None or _hash(payload) != row["payload_hash"]:
            skipped.append(f"AP Coder #{row['invoice_id']}: changed since its dry run (run the dry run again)")
            continue
        ready.append((row, payload))
    if not ready:
        raise ValueError("Nothing in this batch is ready to post: run the dry run first (every voucher that passed it "
                         "and has not changed since is posted).")  # fmt: skip
    for row, _ in ready:
        _put(store, row["idem_key"], row["invoice_id"], batch, approved_at=_now(), approved_by=actor)
    store.log_event("jde_api_approved", actor=actor, detail={"batch": batch, "vouchers": len(ready)})
    return _run(store, ready, actor, client, look_first=False) | {"skipped": skipped}


def _run(store: Any, ready: list[tuple[dict[str, Any], dict[str, Any]]], actor: str | None,
         client: jde_ais.AisClient | None, look_first: bool) -> dict[str, Any]:  # fmt: skip
    own, error = client is None, ""
    if client is None:
        client, error = _client(store)
    counts = {POSTED: 0, FAILED: 0, QUEUED: 0, UNKNOWN: 0}
    try:
        for row, payload in ready:
            status, exc = _deliver(store, client, row, payload, actor, look_first or row["status"] in IN_FLIGHT)
            counts[status if status in counts else QUEUED] += 1
            if exc is not None and exc.kind not in (jde_ais.JDE,):
                error = str(exc)
                if client is not None and own:
                    client.close()
                client = None  # JDE can't be reached: the rest wait in the queue, without trying each
    finally:
        if own and client is not None:
            client.close()
    return {"posted": counts[POSTED], "failed": counts[FAILED], "queued": counts[QUEUED],
            "unknown": counts[UNKNOWN], "error": error}  # fmt: skip


def retry_queue(store: Any, actor: str | None = None, client: jde_ais.AisClient | None = None,
                batch: int | None = None) -> dict[str, Any]:  # fmt: skip
    """Send the vouchers waiting (queued, unknown, or left *sending* by a stop), each asked for by its key first."""
    rows = [p for p in waiting(store) if batch is None or p["batch"] == batch]
    ready, skipped = [], []
    built: dict[int, dict[int, dict[str, Any]]] = {}
    for row in rows:
        try:
            found = built.setdefault(row["batch"], payloads(store, row["batch"])[0])
        except ValueError as exc:
            skipped.append(f"AP Coder #{row['invoice_id']}: {exc}")
            continue
        payload = found.get(row["invoice_id"])
        if payload is None or _hash(payload) != row["payload_hash"]:
            # JDE may have it all the same (sent before the change): asked for by its key, never sent changed.
            ready.append((row, None))
            continue
        ready.append((row, payload))
    if not ready:
        return {"posted": 0, "failed": 0, "queued": 0, "unknown": 0, "error": "", "skipped": skipped}
    own, error = client is None, ""
    if client is None:
        client, error = _client(store)
    sendable = []
    try:
        for row, payload in ready:
            if payload is not None:
                sendable.append((row, payload))
                continue
            if client is not None:
                try:
                    if found_voucher := _status(client, row["idem_key"]):
                        _record(store, row, found_voucher, actor)
                        continue
                except jde_ais.AisError as exc:
                    error = str(exc)
                    continue
            _put(store, row["idem_key"], row["invoice_id"], row["batch"], status=FAILED, next_try=None,
                 error="changed in AP Coder since it was approved and not in JD Edwards: dry run and approve "
                 "again")  # fmt: skip
        result = _run(store, sendable, actor, client, look_first=True)
    finally:
        if own and client is not None:
            client.close()
    result["error"] = result["error"] or error
    return result | {"skipped": skipped}


# --- For the pages ----------------------------------------------------------------------------------------------------


def status_rows(store: Any, batch: int) -> list[dict[str, Any]]:
    """The batch's vouchers as the Exports page lists them."""
    invoices = {i["id"]: i for i in store.get_invoices(store.batch_invoice_ids(batch))}
    out = []
    for p in postings(store, batch):
        final = (invoices.get(p["invoice_id"]) or {}).get("final_output") or {}
        out.append({
            **p, "label": LABELS.get(p["status"], p["status"]), "tone": TONES.get(p["status"], "gray"),
            "vendor": final.get("vendor_name") or "", "number": final.get("invoice_number") or "",
            "total": final.get("grand_total"), "currency": final.get("currency") or "",
            "voucher": " ".join(x for x in (p["document"], p["doc_type"], p["doc_company"]) if x),
        })  # fmt: skip
    return out


def summary(store: Any, batch: int) -> dict[str, int]:
    found: dict[str, int] = {}
    for p in postings(store, batch):
        found[p["status"]] = found.get(p["status"], 0) + 1
    return found
