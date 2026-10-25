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
     only). A *sending* row is someone's live post; after ``SENDING_MINUTES`` it is looked up, never sent blind.
4. **The queue** (``retry_queue``: the button on Exports, ``python -m ap_coder jde status --retry``, and every five
   minutes in the background): each row is looked up by its key first; found, it is *posted* (or *failed*, another
   voucher); not found, the same voucher (the same fingerprint, else *failed*: dry run again) is sent with the same
   key. **Never posted twice**: a locked row (``postings.LOCKED_SQL``) is never sent blind, its batch can't be undone
   (``undo_batch``, in the same kind of transaction as the claim), and its ZIP of batch files leaves it out.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
from collections.abc import Iterator
from typing import Any

from ..coding.vendors import norm_invoice_number
from ..core.safe import parse_amount
from ..core.store import postings as ledger
from . import jde, jde_ais

CHECKED, REFUSED, QUEUED, SENDING, POSTED, FAILED, UNKNOWN = (
    "checked", "refused", "queued", "sending", "posted", "failed", "unknown",
)  # fmt: skip
LABELS = {CHECKED: "Dry run passed", REFUSED: "Refused in the dry run", QUEUED: "Waiting: JD Edwards not reached",
          SENDING: "Being sent", POSTED: "Posted", FAILED: "Refused by JD Edwards",
          UNKNOWN: "Sent, answer not received: being checked"}  # fmt: skip
TONES = {CHECKED: "ok", REFUSED: "err", QUEUED: "warn", SENDING: "warn", POSTED: "ok", FAILED: "err", UNKNOWN: "warn"}
IN_FLIGHT = (QUEUED, SENDING, UNKNOWN)  # approved and not finished: the queue
LOCKED = ledger.LOCKED  # by status; any row with ``maybe_sent`` too (``ledger.LOCKED_SQL``)
LOCKED_SQL = ledger.LOCKED_SQL
SENDABLE = (CHECKED, QUEUED, UNKNOWN, SENDING)  # claimed only with maybe_sent = 0: never sent while JDE may have it
DRY_RUN_OVER = (CHECKED, REFUSED, FAILED)  # what a dry run may write over (and only with maybe_sent = 0)
RETRY_MINUTES = (5, 15, 60)  # the queue's waits after each try that did not reach JDE (then hourly)
SENDING_MINUTES = 15  # a row *sending* longer than this was left by a stop: looked up by its key, never resent blind
YES, NO = ("1", "Y", "YES", "TRUE"), ("0", "N", "NO", "FALSE")


class Locked(ValueError):
    """The batch can't be undone: JDE has, or may have, one of its vouchers."""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


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
    known = next((b for b in store.export_batches() if b["id"] == batch), None)
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
    for foreign in (False, True):
        try:
            raw = sum(float(jde._gross_of(h, foreign) or 0) for h in headers)
        except (TypeError, ValueError):
            continue
        if raw:
            gross.append(round(raw / 100 if settings.decimals == jde.IMPLIED else raw, 2))
    return {"an8": str(head.get("VLAN8") or ""), "invoice_number": str(head.get("VLVINV") or ""),
            "gross": sorted(set(gross))}  # fmt: skip


def payloads(store: Any, batch: int) -> tuple[dict[int, dict[str, Any]], dict[int, list[str]], list[dict[str, Any]]]:
    """({invoice: what the voucher orchestration is sent}, {invoice: why it can't go}, the batch's invoices)."""
    known = _batch(store, batch)
    settings, lookup = jde.load(store), store.vendor_ids()
    invoices = store.get_invoices(store.batch_invoice_ids(batch))
    blocking = jde.settings_problems(settings)
    problems = jde.validate(invoices, settings, lookup)
    on = dt.date.fromisoformat(known["created_at"][:10])
    installation = installation_id(store)
    out: dict[int, dict[str, Any]] = {}
    for inv in invoices:
        if blocking:
            problems.setdefault(inv["id"], []).extend(blocking)
        if inv["id"] in problems:
            continue
        an8 = jde.resolve_an8(inv.get("final_output") or {}, lookup, settings)
        headers, dists, matches = jde.voucher_rows(inv, an8, settings, batch=batch, fx_rates=store.fx_rates(), on=on)
        out[inv["id"]] = {"key": idem_key(inv["id"], batch, installation), "pay_items": headers, "gl_lines": dists,
                          "matches": matches, "_expect": _expect(inv, headers, settings)}  # fmt: skip
    return out, problems, invoices


# --- The postings table ----------------------------------------------------------------------------------------------


@contextlib.contextmanager
def _conn(store: Any) -> Iterator[Any]:
    with store._conn() as conn:
        ledger.ensure(conn)
        yield conn


def postings(store: Any, batch: int | None = None, statuses: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM jde_postings WHERE 1 = 1", []
    if batch is not None:
        sql += " AND batch = ?"
        args.append(batch)
    if statuses:
        sql += " AND status IN (SELECT value FROM json_each(?))"
        args.append(json.dumps(list(statuses)))
    with _conn(store) as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY batch, invoice_id", args)]


def _row(store: Any, key: str) -> dict[str, Any] | None:
    with _conn(store) as conn:
        found = conn.execute("SELECT * FROM jde_postings WHERE idem_key = ?", (key,)).fetchone()
    return dict(found) if found else None


def _keep(store: Any, keys: list[str]) -> None:
    """The rows as they now are, in the ledger outside the database (``core/store/postings.py``)."""
    if not keys:
        return
    with _conn(store) as conn:
        rows = ledger.rows_with_ident(conn, "p.idem_key IN (SELECT value FROM json_each(?))", (json.dumps(keys),))
    ledger.append(store.path, rows)


def _put(store: Any, key: str, invoice_id: int, batch: int, *, only_from: tuple[str, ...] | None = None,
         rev: int | None = None, **values: Any) -> bool:  # fmt: skip
    """Write the row's values. A new row only for a batch not undone. ``only_from``: only over a row in one of these
    statuses that JDE can't hold (maybe_sent 0); ``rev``: only if the row is still that version. Whether it changed."""
    values["updated_at"] = _now()
    with _conn(store) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO jde_postings (idem_key, invoice_id, batch, status, updated_at) SELECT ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM export_batches WHERE id = ? AND undone_at IS NOT NULL)",
            (key, invoice_id, batch, values.get("status", CHECKED), _now(), batch),
        )
        sets = ", ".join(f"{k} = ?" for k in values)
        where, args = "idem_key = ?", [key]
        if only_from is not None:
            where += " AND status IN (SELECT value FROM json_each(?)) AND maybe_sent = 0"
            args.append(json.dumps(list(only_from)))
        if rev is not None:
            where += " AND rev = ?"
            args.append(rev)
        changed = conn.execute(f"UPDATE jde_postings SET {sets}, rev = rev + 1 WHERE {where}",
                               (*values.values(), *args)).rowcount  # fmt: skip
    if changed:
        _keep(store, [key])
    return bool(changed)


def _claim(store: Any, row: dict[str, Any], attempts: int) -> dict[str, Any] | None:
    """Compare-and-set before a send: *sending* only if the row is the version read, sendable, JDE can't hold it and
    its batch is not undone; the row as claimed, or None (someone else has it, or the batch was undone)."""
    with _conn(store) as conn:
        changed = conn.execute(
            "UPDATE jde_postings SET status = ?, sending_at = ?, maybe_sent = 1, attempts = ?, next_try = NULL, "
            "rev = rev + 1, updated_at = ? WHERE idem_key = ? AND rev = ? AND maybe_sent = 0 AND verify = 0 AND "
            "status IN (SELECT value FROM json_each(?)) AND batch NOT IN "
            "(SELECT id FROM export_batches WHERE undone_at IS NOT NULL)",
            (SENDING, _now(), attempts, _now(), row["idem_key"], row["rev"], json.dumps(SENDABLE)),
        ).rowcount
    if changed != 1:
        return None
    _keep(store, [row["idem_key"]])
    return _row(store, row["idem_key"])


def documents_of(store: Any, invoice_id: int | None) -> list[str]:
    """JDE's voucher numbers AP Coder posted for this invoice (the live duplicate check leaves them out)."""
    with _conn(store) as conn:
        return [r[0] for r in conn.execute("SELECT document FROM jde_postings WHERE invoice_id = ? AND status = ? "
                                           "AND document != ''", (invoice_id, POSTED))]  # fmt: skip


def posted_ids(store: Any, batch: int) -> set[int]:
    return {p["invoice_id"] for p in postings(store, batch, (POSTED,))}


def locked(store: Any, batch: int) -> list[dict[str, Any]]:
    """The batch's rows JDE has, or may have (``LOCKED_SQL``)."""
    with _conn(store) as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM jde_postings WHERE batch = ? AND {LOCKED_SQL} "
                                              "ORDER BY invoice_id", (batch,))]  # fmt: skip


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


def undo_problem(store: Any, batch: int) -> str:
    """Why the batch can't be undone ("" when it can): JDE has, or may have, one of its vouchers."""
    found = locked(store, batch)
    return _locked_message(batch, [p["status"] for p in found]) if found else ""


def undo_batch(store: Any, batch: int, actor: str | None = None) -> int:
    """Undo the batch (``Store.undo_export_batch``) in one write transaction with the check that none of its vouchers
    is, or may be, in JDE: Locked (a ValueError) when one is; ValueError when it was undone already. Its dry runs and
    queued vouchers are dropped."""

    def guard(conn: Any) -> None:
        ledger.ensure(conn)
        found = [r[0] for r in conn.execute(f"SELECT status FROM jde_postings WHERE batch = ? AND {LOCKED_SQL}",
                                            (batch,))]  # fmt: skip
        if found:
            raise Locked(_locked_message(batch, found))

    count = store.undo_export_batch(batch, actor=actor, guard=guard)
    forget_batch(store, batch)
    return count


def forget_batch(store: Any, batch: int) -> None:
    """The batch was undone: its dry runs and queued vouchers are dropped (never a row JDE may hold)."""
    with _conn(store) as conn:
        keys = [r[0] for r in conn.execute(f"SELECT idem_key FROM jde_postings WHERE batch = ? AND NOT {LOCKED_SQL}",
                                           (batch,))]  # fmt: skip
        conn.execute(f"DELETE FROM jde_postings WHERE batch = ? AND NOT {LOCKED_SQL}", (batch,))
    ledger.append(store.path, [{"idem_key": k, "deleted": True, "updated_at": _now()} for k in keys])


def _fresh_sending(row: dict[str, Any]) -> bool:
    """A row someone is sending now (not left by a stop): nobody else touches it."""
    if row["status"] != SENDING or not row.get("sending_at"):
        return False
    since = dt.datetime.now() - dt.timedelta(minutes=SENDING_MINUTES)
    return str(row["sending_at"]) > since.isoformat(timespec="seconds")


def waiting(store: Any, due_only: bool = False) -> list[dict[str, Any]]:
    """The queue: approved and not finished, without the rows being sent right now."""
    found = [p for p in postings(store, statuses=IN_FLIGHT) if not _fresh_sending(p)]
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
    answer = client.call("voucher_create", sent, idempotent=False)
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
            row = already.get(inv_id)
            if row and (row["status"] not in DRY_RUN_OVER or row.get("maybe_sent")):
                continue  # posted, on its way, or JDE may have it: a dry run changes nothing of it
            key = row["idem_key"] if row else (found[inv_id]["key"] if inv_id in found else
                                               idem_key(inv_id, batch, installation_id(store)))  # fmt: skip
            if inv_id in problems:
                _put(store, key, inv_id, batch, only_from=DRY_RUN_OVER, status=REFUSED, payload_hash="",
                     error="; ".join(problems[inv_id]), checked_at=_now(), checked_by=actor)  # fmt: skip
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
            _put(store, key, inv_id, batch, only_from=DRY_RUN_OVER, status=REFUSED if error else CHECKED,
                 error=error, payload_hash=_hash(payload), expect=json.dumps(payload["_expect"]), checked_at=_now(),
                 checked_by=actor, maybe_sent=held)  # fmt: skip
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
    with _conn(store) as conn:
        conn.execute("UPDATE invoices SET meta = json_set(CASE WHEN json_valid(meta) THEN meta ELSE '{}' END, "
                     "'$.jde_voucher', json(?)) WHERE id = ?", (json.dumps(voucher), row["invoice_id"]))  # fmt: skip
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


def _wait(store: Any, row: dict[str, Any], attempts: int) -> str:
    """JDE can't be asked now: an approved voucher never sent is queued; any other row stays as it is (its next try
    only)."""
    later = _later(attempts, bool(row.get("maybe_sent")))
    if row["status"] == CHECKED and not row.get("maybe_sent"):
        _put(store, row["idem_key"], row["invoice_id"], row["batch"], only_from=(CHECKED,), status=QUEUED,
             attempts=attempts, next_try=later)  # fmt: skip
        return QUEUED
    _put(store, row["idem_key"], row["invoice_id"], row["batch"], attempts=attempts, next_try=later)
    return row["status"]


def _lookup(store: Any, client: jde_ais.AisClient, row: dict[str, Any], actor: str | None
            ) -> tuple[str | None, dict[str, Any] | None]:  # fmt: skip
    """Ask JDE for the row's key: (the status it now has, None) when JDE has a voucher under it; (None, the row cleared
    of maybe_sent) when JDE says it has none. AisError when JDE can't say."""
    found = _status(client, row["idem_key"])
    if found is not None:
        return _take(store, row, found, actor), None
    if not _put(store, row["idem_key"], row["invoice_id"], row["batch"], rev=row["rev"], maybe_sent=0, verify=0):
        return None, None  # changed meanwhile (another queue run, an undo): left to it
    return None, _row(store, row["idem_key"])


def _deliver(store: Any, client: jde_ais.AisClient | None, row: dict[str, Any], payload: dict[str, Any] | None,
             actor: str | None, look_first: bool) -> tuple[str, jde_ais.AisError | None]:  # fmt: skip
    """Send one approved voucher (asking first whether JDE has it already, when it may have); its new status."""
    attempts = int(row.get("attempts") or 0) + 1
    if client is None:  # the run never reached it (JDE not asked): its tries and its place stay (B-1198)
        return _wait(store, row, attempts - 1), None
    if look_first or row.get("maybe_sent") or row.get("verify") or row["status"] != CHECKED:
        try:
            status, cleared = _lookup(store, client, row, actor)
        except jde_ais.AisError as exc:
            return _wait(store, row, attempts), exc
        if status is not None:
            return status, None
        if cleared is None:
            return "skipped", None
        row = cleared
    if payload is None:  # changed since it was approved, and JDE has none under its key
        _put(store, row["idem_key"], row["invoice_id"], row["batch"], rev=row["rev"], status=FAILED, next_try=None,
             error="changed in AP Coder since it was approved and not in JD Edwards: dry run and approve "
             "again")  # fmt: skip
        return FAILED, None
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
                 f"made it: {lost})", next_try=_later(row["attempts"]))  # fmt: skip
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
    _put(store, key, inv, batch, status=UNKNOWN, error=str(exc), next_try=_later(row["attempts"]))
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
    _put(store, key, inv, batch, status=UNKNOWN, error=f"{said}: being checked", next_try=_later(row["attempts"]))
    if result in ("created", "exists"):
        current = _row(store, key)
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
    approved = []
    for row, payload in ready:
        if _put(store, row["idem_key"], row["invoice_id"], batch, rev=row["rev"], approved_at=_now(),
                approved_by=actor):  # fmt: skip
            approved.append((_row(store, row["idem_key"]) or row, payload))
    store.log_event("jde_api_approved", actor=actor, detail={"batch": batch, "vouchers": len(approved)})
    return _run(store, approved, actor, client, look_first=False, skipped=skipped)


def _run(store: Any, ready: list[tuple[dict[str, Any], dict[str, Any] | None]], actor: str | None,
         client: jde_ais.AisClient | None, look_first: bool, skipped: list[str] | None = None,
         error: str = "") -> dict[str, Any]:  # fmt: skip
    own = client is None
    if client is None:
        client, error = _client(store)
    skipped = list(skipped or [])
    counts = {POSTED: 0, FAILED: 0, QUEUED: 0, UNKNOWN: 0}
    try:
        for row, payload in ready:
            status, exc = _deliver(store, client, row, payload, actor, look_first)
            if status == "skipped":
                skipped.append(f"AP Coder #{row['invoice_id']}: not sent: it changed meanwhile (sent from elsewhere, "
                               "or its batch undone)")  # fmt: skip
            else:
                counts[status if status in counts else UNKNOWN if status == SENDING else QUEUED] += 1
            if exc is not None and exc.kind not in (jde_ais.JDE,):
                error = str(exc)
                if client is not None and own:
                    client.close()
                client = None  # JDE can't be reached: the rest wait in the queue, without trying each
    finally:
        if own and client is not None:
            client.close()
    return {"posted": counts[POSTED], "failed": counts[FAILED], "queued": counts[QUEUED],
            "unknown": counts[UNKNOWN], "error": error, "skipped": skipped}  # fmt: skip


def retry_queue(store: Any, actor: str | None = None, client: jde_ais.AisClient | None = None,
                batch: int | None = None) -> dict[str, Any]:  # fmt: skip
    """Send the vouchers waiting (queued, unknown, or left *sending* by a stop), each asked for by its key first."""
    rows = [p for p in waiting(store) if batch is None or p["batch"] == batch]
    ready: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    skipped: list[str] = []
    built: dict[int, dict[int, dict[str, Any]]] = {}
    for row in rows:
        try:
            found = built.setdefault(row["batch"], payloads(store, row["batch"])[0])
        except ValueError as exc:
            skipped.append(f"AP Coder #{row['invoice_id']}: {exc}")
            continue
        payload = found.get(row["invoice_id"])
        # Changed since it was approved: JDE may have it all the same (sent before the change): asked for by its key,
        # never sent changed.
        ready.append((row, payload if payload is not None and _hash(payload) == row["payload_hash"] else None))
    if not ready:
        return {"posted": 0, "failed": 0, "queued": 0, "unknown": 0, "error": "", "skipped": skipped}
    return _run(store, ready, actor, client, look_first=True, skipped=skipped)


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
            "locked": p["status"] in LOCKED or bool(p.get("maybe_sent")),
        })  # fmt: skip
    return out


def summary(store: Any, batch: int) -> dict[str, int]:
    found: dict[str, int] = {}
    for p in postings(store, batch):
        found[p["status"]] = found.get(p["status"], 0) + 1
    return found
