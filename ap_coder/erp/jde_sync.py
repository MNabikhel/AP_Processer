"""JD Edwards API step 1 (read only, D-104): suppliers, GL accounts, business units, open POs and the AP ledger's
vouchers read from JDE's orchestrations into AP Coder's own tables, through the same import code as the CSV files,
so the checks and the learning work the same; and the live "already in JD Edwards?" check of the review screen.

* ``sync``: on demand (Settings → JD Edwards E1 → API connection → *Sync now*, ``python -m ap_coder jde sync``) or
  once a night while AP Coder runs (``Background``, after ``AisSettings.sync_hour``). Each part goes through its CSV
  import's path: suppliers ``vendors.master_rows`` + ``Store.import_vendor_master`` (F0101/F0401: the number as ERP
  ID, the tax numbers, the terms, the hold code as on hold, the province; the bank account JDE pays on as "transit
  …last 4"); GL accounts ``Store.import_accounts("gl_accounts")`` (F0901: the object account, or object.subsidiary
  with a JD Edwards account mapping row for it); business units ``Store.import_accounts("cost_centers")`` (F0006);
  open POs ``po.rows_from_records`` + ``Store.import_purchase_orders`` (F4311 with F43121's quantity received; a PO
  synced before and no longer open in JDE is closed); vouchers ``Store.import_erp_register`` (F0411, for the
  duplicate checks). Nothing is deleted, and what AP typed that JDE doesn't hold (account categories, meals ticks,
  vendors' notes) is kept. The report lists what was added and what changed, field by field.
* ``live_findings``: the review screen's checks ask JDE (by supplier number) whether the invoice is already a voucher,
  with a five-minute cache; JDE down or slow (5 seconds at most), it says so in an INFO line and the check stands on
  the last sync's copy, and JDE is not asked again for a minute.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from typing import Any

from ..coding import po as po_mod
from ..coding.vendors import master_rows, norm_invoice_number
from ..core.memory import vendor_key
from ..core.safe import parse_amount
from . import jde, jde_ais
from .jde import ERROR, INFO
from .registers import iso_date

log = logging.getLogger(__name__)

STATE_KEY = "jde_ais_sync"  # the last sync: when, whether it worked, its report, the POs it brought
PARTS = ("suppliers", "gl_accounts", "business_units", "purchase_orders", "vouchers")
LABELS = {"suppliers": "Suppliers", "gl_accounts": "GL accounts", "business_units": "Business units",
          "purchase_orders": "Open POs", "vouchers": "Vouchers (AP ledger)"}  # fmt: skip
POSTABLE = ("",)  # F0901 / F0006 posting edit codes that take postings (blank); CONFIRM with the JDE team
CLOSED_STATUS = "999"  # F4311 next status of a closed line


def state(store: Any) -> dict[str, Any]:
    try:
        found = json.loads(store.get_setting(STATE_KEY) or "{}")
    except ValueError:
        found = {}
    return found if isinstance(found, dict) else {}


def _now() -> dt.datetime:
    return dt.datetime.now()


def _text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def _code(obj: Any, sub: Any) -> str:
    """AP Coder's GL code of a JDE account: the object, or object.subsidiary."""
    obj, sub = _text(obj), _text(sub)
    return f"{obj}.{sub}" if sub else obj


# --- The parts --------------------------------------------------------------------------------------------------


def _vendor_rows(store: Any) -> dict[str, dict[str, Any]]:
    with store._conn() as conn:
        return {r["vendor_key"]: dict(r) for r in conn.execute(
            "SELECT vendor_key, display_name, erp_id, expected_gst, terms, status, qst_number, pst_number, province, "
            "erp_bank FROM vendors WHERE in_master = 1")}  # fmt: skip


VENDOR_FIELDS = {"erp_id": "number", "expected_gst": "GST/HST number", "terms": "terms", "status": "hold",
                 "qst_number": "QST number", "pst_number": "PST number", "province": "province",
                 "erp_bank": "bank account"}  # fmt: skip


def sync_suppliers(store: Any, client: jde_ais.AisClient, actor: str | None) -> dict[str, Any]:
    settings = jde.load(store)
    by_code = {code: text for text, code in settings.terms_codes.items()}
    found = client.rows("suppliers")
    records = []
    banks: dict[str, str] = {}
    for r in found:
        name, an8 = _text(r.get("name")), _text(r.get("an8"))
        if not name:
            continue
        second, province = _text(r.get("tax_id_2")), _text(r.get("province")).upper()
        quebec = province == "QC" or "TQ" in second.upper()
        code = _text(r.get("terms_code"))
        records.append({
            "vendor_name": name, "erp_id": an8, "gst": _text(r.get("tax_id")),
            "qst": second if quebec else "", "pst": "" if quebec else second,
            "terms": _text(r.get("terms")) or by_code.get(code.upper(), code),
            "status": "on hold" if _text(r.get("hold_code")) else "active", "province": province,
        })  # fmt: skip
        last4 = _text(r.get("bank_last4"))[-4:]
        if an8 and last4:
            banks[an8] = f"{_text(r.get('bank_transit'))} …{last4}".strip()
    columns = {k: k for k in ("vendor_name", "erp_id", "gst", "qst", "pst", "terms", "status", "province")}
    before = _vendor_rows(store)
    result = store.import_vendor_master(master_rows(records, columns), actor=actor)
    with store._conn() as conn:
        for an8 in {_text(r.get("an8")) for r in found} - {""}:
            conn.execute(
                "UPDATE vendors SET erp_bank = ? WHERE erp_id = ? AND in_master = 1", (banks.get(an8, ""), an8)
            )
    after = _vendor_rows(store)
    added = sorted(after[k]["display_name"] for k in after.keys() - before.keys())
    changed = []
    for key in sorted(after.keys() & before.keys()):
        what = [label for f, label in VENDOR_FIELDS.items() if (before[key].get(f) or "") != (after[key].get(f) or "")]
        if what:
            changed.append(f"{after[key]['display_name']} ({', '.join(what)})")
    return {"read": len(found), "added": added, "changed": changed, "duplicates": result["duplicates"]}


def _accounts(store: Any, table: str) -> dict[str, dict[str, Any]]:
    return {r["code"]: r for r in store.list_accounts(table)}


def _account_changes(before: dict[str, Any], after: dict[str, Any]) -> tuple[list[str], list[str]]:
    added = [f"{c} {after[c].get('description') or ''}".strip() for c in sorted(after.keys() - before.keys())]
    changed = [f"{c} (description)" for c in sorted(after.keys() & before.keys())
               if before[c].get("description") != after[c].get("description")]  # fmt: skip
    return added, changed


def sync_gl_accounts(store: Any, client: jde_ais.AisClient, actor: str | None) -> dict[str, Any]:
    cfg = client.cfg
    found = client.rows("gl_accounts", {"company": cfg.company})
    rows: dict[str, dict[str, Any]] = {}
    subs: dict[str, tuple[str, str]] = {}
    skipped = 0
    for r in found:
        code = _code(r.get("object"), r.get("subsidiary"))
        if not code or _text(r.get("posting_edit")).upper() not in POSTABLE:
            skipped += 1
            continue
        rows.setdefault(code, {"code": code, "description": _text(r.get("description"))})
        if _text(r.get("subsidiary")):
            subs[code] = (_text(r.get("object")), _text(r.get("subsidiary")))
    before = _accounts(store, "gl_accounts")
    store.import_accounts("gl_accounts", list(rows.values()), "code", "description", actor=actor)
    added, changed = _account_changes(before, _accounts(store, "gl_accounts"))
    # An account with a subsidiary is AP Coder's GL code "object.subsidiary": the JD Edwards mapping sends it as that
    # object and subsidiary (the default rule would send "6200.01" as the object).
    settings = jde.load(store)
    mapped = {jde._norm(m.gl_code) for m in settings.gl_map}
    new_maps = [jde.GlMap(code, obj=obj, sub=sub) for code, (obj, sub) in sorted(subs.items())
                if jde._norm(code) not in mapped]  # fmt: skip
    if new_maps:
        settings.gl_map += new_maps
        jde.save(store, settings, actor=actor)
    return {"read": len(found), "added": added, "changed": changed, "not_postable": skipped,
            "mapped": [m.gl_code for m in new_maps]}  # fmt: skip


def sync_business_units(store: Any, client: jde_ais.AisClient, actor: str | None) -> dict[str, Any]:
    found = client.rows("business_units", {"company": client.cfg.company})
    rows: dict[str, dict[str, Any]] = {}
    skipped = 0
    for r in found:
        code = _text(r.get("business_unit"))
        if not code or _text(r.get("posting_edit")).upper() not in POSTABLE:
            skipped += 1
            continue
        rows.setdefault(code, {"code": code, "description": _text(r.get("description"))})
    before = _accounts(store, "cost_centers")
    store.import_accounts("cost_centers", list(rows.values()), "code", "description", actor=actor)
    added, changed = _account_changes(before, _accounts(store, "cost_centers"))
    return {"read": len(found), "added": added, "changed": changed, "not_postable": skipped}


def _po_lines(store: Any, key: str) -> list[tuple[Any, ...]]:
    found = store.purchase_order(key)
    if not found:
        return []
    return [(li["line_number"], li["description"], li["quantity"], li["unit_price"], li["amount"],
             li["received_quantity"], li["gl_code"], li["cost_center"]) for li in found["lines"]]  # fmt: skip


def sync_purchase_orders(store: Any, client: jde_ais.AisClient, actor: str | None) -> dict[str, Any]:
    names = {v["erp_id"]: v["display_name"] for v in store.master_vendors() if v.get("erp_id")}
    found = client.rows("purchase_orders", {"order_type": client.cfg.po_order_type})
    records = []
    for r in found:
        try:
            line: Any = int(float(_text(r.get("line_number")) or 0)) or None
        except ValueError:
            line = None
        records.append({
            "po_number": _text(r.get("order_number")),
            "vendor_name": _text(r.get("supplier_name")) or names.get(_text(r.get("an8")), ""),
            "line_number": line, "description": _text(r.get("description")), "quantity": r.get("quantity"),
            "unit_price": r.get("unit_cost"), "amount": r.get("amount"), "received_quantity": r.get("received"),
            "gl_code": _code(r.get("object"), r.get("subsidiary")), "cost_center": _text(r.get("business_unit")),
            "status": "closed" if _text(r.get("next_status")) == CLOSED_STATUS else "open",
        })  # fmt: skip
    columns = {k: k for k in records[0]} if records else {}
    rows, skipped = po_mod.rows_from_records(records, columns)
    keys = sorted({po_mod.po_key(r["po_number"]) for r in rows} - {""})
    before = {k: _po_lines(store, k) for k in keys}
    store.import_purchase_orders(rows, actor=actor)
    added = [store.purchase_order(k)["po_number"] for k in keys if not before[k]]
    changed = [store.purchase_order(k)["po_number"] for k in keys if before[k] and before[k] != _po_lines(store, k)]
    closed = []
    for key in sorted(set(state(store).get("po_keys") or []) - set(keys)):
        known = store.purchase_order(key)
        if known and known["status"] == po_mod.OPEN:
            store.set_po_status(key, po_mod.CLOSED, actor=actor)
            closed.append(known["po_number"])
    return {"read": len(found), "added": added, "changed": changed, "closed": closed, "skipped": skipped,
            "po_keys": keys}  # fmt: skip


def voucher_rows(found: list[dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    """F0411 rows as the ERP invoice register's rows (``registers.rows_from_records`` output)."""
    rows = []
    for r in found:
        name = _text(r.get("supplier_name")) or names.get(_text(r.get("an8")), "")
        number, total = _text(r.get("invoice_number")), parse_amount(r.get("gross"))
        if name and number and total is not None:
            rows.append({"vendor_name": name, "invoice_number": number,
                         "invoice_date": iso_date(_text(r.get("invoice_date"))), "total": total})  # fmt: skip
    return rows


def sync_vouchers(store: Any, client: jde_ais.AisClient, actor: str | None) -> dict[str, Any]:
    fmt = client.ais.get("date_format") or "%Y-%m-%d"
    since = (_now().date() - dt.timedelta(days=client.cfg.voucher_days)).strftime(fmt)
    names = {v["erp_id"]: v["display_name"] for v in store.master_vendors() if v.get("erp_id")}
    found = client.rows("vouchers", {"since": since})
    before = store.erp_register_count()
    total = store.import_erp_register(voucher_rows(found, names), actor=actor)
    return {"read": len(found), "added": [f"{total - before} new"] if total > before else [], "changed": [],
            "since": since}  # fmt: skip


SYNCS = {"suppliers": sync_suppliers, "gl_accounts": sync_gl_accounts, "business_units": sync_business_units,
         "purchase_orders": sync_purchase_orders, "vouchers": sync_vouchers}  # fmt: skip


def sync(store: Any, *, parts: tuple[str, ...] = PARTS, client: jde_ais.AisClient | None = None,
         actor: str | None = None) -> dict[str, Any]:  # fmt: skip
    """Read each part from JD Edwards into the store. {"ok", "at", "parts": {part: report}, "errors": {part: why}}.
    Step 1 must be on (else ``AisOff``, nothing sent). JDE unreachable: the parts left are not tried."""
    own = client is None
    client = client or jde_ais.client(store, need="step1")
    report: dict[str, Any] = {"at": _now().isoformat(timespec="seconds"), "parts": {}, "errors": {}}
    previous = state(store)
    try:
        for part in parts:
            try:
                report["parts"][part] = SYNCS[part](store, client, actor)
            except jde_ais.AisError as exc:
                report["errors"][part] = str(exc)
                if exc.kind in jde_ais.DOWN or exc.kind in (jde_ais.AUTH, jde_ais.CERTIFICATE, jde_ais.ADDRESS):
                    for rest in parts[parts.index(part) + 1 :]:
                        report["errors"][rest] = "not tried: " + str(exc)
                    break
    finally:
        if own:
            client.close()
    report["ok"] = not report["errors"]
    po_keys = (report["parts"].get("purchase_orders") or {}).pop("po_keys", None)
    saved = {**previous, "tried": report["at"], "report": report,
             "po_keys": po_keys if po_keys is not None else previous.get("po_keys", [])}  # fmt: skip
    if report["ok"]:
        saved["at"] = report["at"]
    store.set_setting(STATE_KEY, json.dumps(saved))
    store.log_event("jde_synced", actor=actor, detail={
        "ok": report["ok"], **{p: {k: len(v) if isinstance(v, list) else v for k, v in r.items()}
                               for p, r in report["parts"].items()}, "errors": sorted(report["errors"])})  # fmt: skip
    return report


def report_lines(report: dict[str, Any], shown: int = 10) -> list[str]:
    """The sync's report as plain lines (the settings page's message, ``jde sync``)."""
    lines = []
    for part, r in (report.get("parts") or {}).items():
        bits = [f"{len(r.get('added') or [])} added" if part != "vouchers" else
                f"{r.get('read', 0)} read" + (f", {r['added'][0]}" if r.get("added") else "")]  # fmt: skip
        if part != "vouchers":
            bits.append(f"{len(r.get('changed') or [])} changed")
        if r.get("closed"):
            bits.append(f"{len(r['closed'])} closed in JDE")
        if r.get("not_postable"):
            bits.append(f"{r['not_postable']} not postable, left out")
        line = f"{LABELS[part]}: " + ", ".join(bits)
        detail = [*(r.get("added") or []), *(r.get("changed") or [])] if part != "vouchers" else []
        if detail:
            line += ": " + "; ".join(detail[:shown]) + ("…" if len(detail) > shown else "")
        lines.append(line)
    for part, why in (report.get("errors") or {}).items():
        lines.append(f"{LABELS[part]}: not synced: {why}")
    return lines


def due(store: Any, now: dt.datetime | None = None) -> bool:
    """Whether the nightly sync is due: step 1 on, past the sync hour, not synced today, not tried in the last hour."""
    cfg = jde_ais.load(store)
    now = now or _now()
    if not cfg.step1 or now.hour < cfg.sync_hour:
        return False
    found = state(store)
    if str(found.get("at") or "")[:10] == now.date().isoformat():
        return False
    try:
        tried = dt.datetime.fromisoformat(str(found.get("tried") or ""))
    except ValueError:
        return True
    return now - tried >= dt.timedelta(hours=1)


# --- The live duplicate check -------------------------------------------------------------------------------------

CACHE_SECONDS = 300.0
DOWN_SECONDS = 60.0
LIVE_TIMEOUT = 5.0
_CACHE: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}
_DOWN: dict[str, tuple[float, str]] = {}
_LOCK = threading.Lock()
PAID = {"P": "paid", "A": "approved for payment, not paid yet", "H": "on hold in JDE"}


def forget_cache() -> None:
    with _LOCK:
        _CACHE.clear()
        _DOWN.clear()


def _ask(store: Any, an8: str, number: str) -> list[dict[str, Any]]:
    cfg = jde_ais.load(store)
    cfg.timeout = min(cfg.timeout, LIVE_TIMEOUT)
    with jde_ais.AisClient(cfg) as c:
        c.retries = 0
        spec = jde_ais.spec_of(c.mapping, "vouchers")
        inputs = {"supplier": an8} if an8 else {"invoice": number}
        answer = c.call("vouchers", {**inputs, "max_rows": int(c.ais.get("page_size") or 500)})
        return [jde_ais.fields_of(spec, r) for r in answer.get(spec.get("rows") or "rows") or [] if isinstance(r, dict)]


def live_findings(store: Any, coding: Any, exclude_invoice_id: int | None = None) -> list[tuple[str, str, str]]:
    """(severity, code, message) of the live check: DUPLICATE_IN_ERP for a voucher already in JDE with this supplier
    and invoice number (not the one AP Coder posted for this invoice), or JDE_NOT_REACHED when JDE can't be asked.
    Nothing when step 1 is off."""
    cfg = jde_ais.load(store)
    number = norm_invoice_number(getattr(coding, "invoice_number", ""))
    if not cfg.step1 or not number:
        return []
    final = {"vendor_name": coding.vendor_name, "gst_hst_registration_number": coding.gst_hst_registration_number}
    an8 = jde.resolve_an8(final, store.vendor_ids(), jde.load(store))
    where, key = str(store.path), (str(store.path), an8, number if not an8 else "")
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        down = _DOWN.get(where)
    rows: list[dict[str, Any]] | None = cached[1] if cached and now - cached[0] < CACHE_SECONDS else None
    if rows is None:
        if down and now - down[0] < DOWN_SECONDS:
            return [_not_reached(store, down[1])]
        try:
            rows = _ask(store, an8, number)
        except jde_ais.AisError as exc:
            with _LOCK:
                _DOWN[where] = (now, str(exc))
            return [_not_reached(store, str(exc))]
        with _LOCK:
            _CACHE[key] = (now, rows)
            _DOWN.pop(where, None)
    from . import jde_post

    own = set(jde_post.documents_of(store, exclude_invoice_id)) if exclude_invoice_id else set()
    credit = float(coding.grand_total or 0) < 0
    said = {norm_invoice_number(r["invoice_number"]) for r in store.in_erp(coding.vendor_name, coding.invoice_number,
                                                                            coding.grand_total)}  # fmt: skip
    out = []
    for r in rows:
        if norm_invoice_number(r.get("invoice_number")) != number or _text(r.get("document")) in own:
            continue
        if not an8 and vendor_key(_text(r.get("supplier_name"))) != vendor_key(coding.vendor_name or ""):
            continue
        gross = parse_amount(r.get("gross"))
        if gross is not None and (gross < 0) != credit:
            continue
        if number in said and not own:
            continue  # the register (the last sync) said so already
        paid = PAID.get(_text(r.get("pay_status")).upper(), "")
        if parse_amount(r.get("open_amount")) == 0 and gross:
            paid = "paid"
        date = iso_date(_text(r.get("invoice_date")))
        out.append((ERROR, "DUPLICATE_IN_ERP",
                    f"invoice {_text(r.get('invoice_number'))} from this vendor is already in JD Edwards: voucher "
                    f"{_text(r.get('document'))} {_text(r.get('doc_type'))}{' dated ' + date if date else ''}"
                    + (f", total {gross:,.2f}" if gross is not None else "") + (f", {paid}" if paid else "")
                    + ": possible duplicate payment"))  # fmt: skip
        if len(out) >= 2:
            break
    return out


def _not_reached(store: Any, why: str) -> tuple[str, str, str]:
    when = str(state(store).get("at") or "")[:16].replace("T", " ")
    since = f"the vouchers of the last sync ({when})" if when else "the ERP invoice register only (no sync yet)"
    return (INFO, "JDE_NOT_REACHED", f"JD Edwards could not be asked whether this invoice is already there "
            f"({why.rstrip('.')}); it was checked against {since}")  # fmt: skip


# --- In the background --------------------------------------------------------------------------------------------


def tick(store: Any, now: dt.datetime | None = None) -> list[str]:
    """One look: the nightly sync when it is due, then the vouchers waiting in the queue. What it did."""
    done: list[str] = []
    cfg = jde_ais.load(store)
    if cfg.step1 and due(store, now):
        try:
            report = sync(store, actor="the nightly JD Edwards sync")
            done.append("synced" if report["ok"] else "sync failed")
        except jde_ais.AisError as exc:
            log.warning("the nightly JD Edwards sync did not run: %s", exc)
    if cfg.step2:
        from . import jde_post

        if jde_post.waiting(store, due_only=True):
            found = jde_post.retry_queue(store, actor="the JD Edwards queue")
            done.append(f"queue: {found['posted']} posted")
    return done


class Background:
    """The nightly sync and the queue, while the dashboard runs: a look every ``every`` seconds (nothing while both
    steps are off)."""

    def __init__(self, store_factory: Any, every: float = 300.0) -> None:
        self.store_factory, self.every = store_factory, every
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="ap-coder-jde", daemon=True)

    def start(self) -> Background:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stopping.set()

    def _run(self) -> None:
        while not self.stopping.wait(self.every):
            try:
                store = self.store_factory()
                if not store.restoring():
                    tick(store)
            except Exception:  # noqa: BLE001 - never stop the dashboard
                log.warning("the JD Edwards background work failed", exc_info=True)
