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
  synced before and no longer open in JDE is closed); vouchers ``Store.import_jde_vouchers`` (F0411, the register of
  the duplicate checks, each voucher by its key). What AP typed that JDE doesn't hold (account categories, meals
  ticks, vendors' notes) is kept. The report lists what was added and what changed, field by field.
* **What changed, not everything every night** (D-181): each part keeps when its last good read began, and the next
  asks JDE for what changed since (``ChangedSince``, from a day before: the two servers' clocks): suppliers, GL
  accounts, open POs (a PO's lines in the answer merged into its lines here) and vouchers; the business units, a short
  list, are read whole. A part is read whole once a week (``FULL_DAYS``), the first time, when what is read changes
  (the company, the PO order type, the vouchers' days) and when asked (``full``: ``jde sync --full``, Settings' *Read
  everything again*): only a whole read can say what JDE no longer holds, so only then is a PO it no longer lists
  closed. The vouchers each night: what changed in the ``voucher_days`` window, and every voucher of the last
  RECENT_DAYS, so one voided this month leaves the register by the next morning; one voided before, at the week's
  whole read. Most or all of what a read would close or remove at once is refused, as a wrong answer (D-105).
* **Every page, as it comes, however many**: each page is written in a transaction of its own (500 rows: a few tens
  of milliseconds of the write lock, never long enough to hold up an approval; each write begun once the lock has
  been free about as long as the last held it, B-2051) with where the read is
  (``PROGRESS_KEY``): a sync interrupted (JDE down at page 300, the computer restarted) goes on from that page the
  next time. Nothing a read would close or remove goes before its last page: a half-read register is never short of
  a voucher. One sync at a time, on every computer of the company server (``LEASE``).
* ``live_findings``: the review screen's checks ask JDE (by supplier number, the vouchers of the sync's window; a
  supplier with too many even then by its number and the invoice number) whether the invoice is already a voucher,
  with a five-minute cache; JDE down or slow (5 seconds at most), it says so in an INFO line and the check stands on
  the last sync's copy, and JDE is not asked again for a minute.
"""

from __future__ import annotations

import atexit
import datetime as dt
import json
import logging
import math
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from ..coding import po as po_mod
from ..coding.vendors import master_rows, norm_invoice_number
from ..core.caches import MB, Bounded
from ..core.memory import vendor_key
from ..core.safe import parse_amount
from ..core.store.register import CLEARED_KEY
from . import jde, jde_ais
from .jde import ERROR, INFO
from .registers import iso_date

log = logging.getLogger(__name__)

STATE_KEY = "jde_ais_sync"  # the last sync: when, whether it worked, its report, each part's last good read, the POs
PROGRESS_KEY = "jde_ais_sync_progress"  # each part's read under way: what it asks and the last row written
PARTS = ("suppliers", "gl_accounts", "business_units", "purchase_orders", "vouchers")
LABELS = {"suppliers": "Suppliers", "gl_accounts": "GL accounts", "business_units": "Business units",
          "purchase_orders": "Open POs", "vouchers": "Vouchers (AP ledger)"}  # fmt: skip
POSTABLE = ("",)  # F0901 / F0006 posting edit codes that take postings (blank); CONFIRM with the JDE team
CLOSED_STATUS = "999"  # F4311 next status of a closed line
FULL, CHANGES = "full", "changes"  # a part read whole, or what changed since its last good read
FULL_DAYS = 7  # each part read whole at least this often (what JDE deleted, closed or voided is seen only so)
RECENT_DAYS = 31  # every voucher of these last days read each night: one voided this month leaves the register
MARGIN_DAYS = 1  # what changed asked from this long before the last good read began (the servers' clocks)
RESUME = dt.timedelta(days=3)  # a read stopped longer ago starts again
WHOLE = ("business_units",)  # read whole every time (a short list; its orchestration takes no ChangedSince)
SCOPE = {"gl_accounts": ("company",), "business_units": ("company",), "purchase_orders": ("po_order_type",),
         "vouchers": ("voucher_days",)}  # what a part reads: changed, it is read whole  # fmt: skip
LEASE, LEASE_SECONDS = "jde_sync", 900.0  # one sync at a time; renewed at each page, let go at the end
KEPT = 50  # names kept in each list of a part's report (its counts count them all)
WRITE_ROWS = 500  # POs closed, vouchers removed, in one transaction
# Vouchers written in one transaction (B-1918): a page can be 1,000 rows or more (the orchestration's page_size),
# and a whole read writing over the register held the write lock 0.4-0.5 s a page on a slow disk.
VOUCHERS_A_WRITE = 250
# Each of a part's writes begins once the write lock has been free about as long as the last one held it, this many
# seconds at least and at most (B-2051; a page coming from JDE meanwhile counts): another writer waiting (an approval)
# retries after 1, 2, 5, 10 ms... then every 100 ms (SQLite's busy timeout), and between writes back to back (a page's
# vouchers a few hundred at a time, the removals) the lock was free too briefly for its retries: up to 0.7 s.
PACE_MIN, PACE_MAX = 0.002, 0.05
_clock, _sleep = time.perf_counter, time.sleep  # (a test's own)
MASS_CLOSE_SHARE = 0.5  # an answer that would close more than this share of the open synced POs, or all, is refused
SUPPLIER_COLUMNS = {k: k for k in ("vendor_name", "erp_id", "gst", "qst", "pst", "terms", "province")}
# Each part's report, besides what all have ("read", "mode", "added", "changed").
REPORTED = {"suppliers": {"duplicates": []}, "gl_accounts": {"not_postable": 0, "mapped": []},
            "business_units": {"not_postable": 0}, "purchase_orders": {"closed": [], "skipped": 0, "refused_close": ""},
            "vouchers": {"new": 0, "removed": 0, "refused_remove": ""}}  # fmt: skip


def state(store: Any) -> dict[str, Any]:
    return store.get_json(STATE_KEY, {})


def _now() -> dt.datetime:
    return dt.datetime.now()


def _when(text: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(text or ""))
    except ValueError:
        return None


def _text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def _code(obj: Any, sub: Any) -> str:
    """AP Coder's GL code of a JDE account: the object, or object.subsidiary."""
    obj, sub = _text(obj), _text(sub)
    return f"{obj}.{sub}" if sub else obj


def _note(report: dict[str, Any], what: str, found: list[Any]) -> None:
    """``found`` counted in the report's ``what`` (``<what>_count``), the first KEPT of them named."""
    report[f"{what}_count"] = report.get(f"{what}_count", 0) + len(found)
    report[what] = [*(report.get(what) or []), *found][:KEPT]


def _count(report: dict[str, Any], what: str) -> int:
    return int(report.get(f"{what}_count", len(report.get(what) or [])))


def _save_state(store: Any, **changes: Any) -> None:
    store.set_setting(STATE_KEY, json.dumps({**state(store), **changes}))


@dataclass
class _Pace:
    """A part's writes one after another, never back to back (B-2051): when the last ended and how long it held the
    write lock."""

    ended: float = -math.inf
    held: float = 0.0

    @contextmanager
    def write(self, store: Any) -> Iterator[None]:
        """A transaction (``store.transaction``), begun once the lock has been free long enough (PACE_MIN, PACE_MAX)."""
        rest = min(max(self.held, PACE_MIN), PACE_MAX) - (_clock() - self.ended)
        if rest > 0:
            _sleep(rest)
        began = _clock()
        try:
            with store.transaction():
                yield
        finally:
            self.ended = _clock()
            self.held = self.ended - began


@dataclass
class _Run:
    """A part's read under way: its plan (``_plan``, kept as the progress), who for, and what its pages share."""

    part: str
    plan: dict[str, Any]
    actor: str | None
    allow_mass_close: bool = False
    date_format: str = "%Y-%m-%d"  # the mapping's, of the dates sent and read
    cache: dict[str, Any] = field(default_factory=dict)
    pace: _Pace = field(default_factory=_Pace)  # its writes

    @property
    def report(self) -> dict[str, Any]:
        return self.plan["report"]

    @property
    def mode(self) -> str:
        return self.plan["mode"]

    def names(self, store: Any) -> dict[str, str]:
        """{ERP ID: name} of the vendor master, for a row JDE gave no supplier name."""
        if "names" not in self.cache:
            self.cache["names"] = {v["erp_id"]: v["display_name"] for v in store.master_vendors() if v.get("erp_id")}
        return self.cache["names"]


# --- The parts --------------------------------------------------------------------------------------------------


VENDOR_FIELDS = {"erp_id": "number", "expected_gst": "GST/HST number", "terms": "terms", "erp_hold": "hold",
                 "qst_number": "QST number", "pst_number": "PST number", "province": "province",
                 "erp_bank": "bank account"}  # fmt: skip


def _suppliers(store: Any, found: list[dict[str, Any]], run: _Run, read: dict[str, Any]) -> None:
    if "terms" not in run.cache:
        run.cache["terms"] = {code: text for text, code in jde.load(store).terms_codes.items()}
    by_code = run.cache["terms"]
    records = []
    banks: dict[str, str] = {}
    # JDE's hold is kept apart (``vendors.erp_hold``): a hold AP put on a vendor here is never lifted by the sync; the
    # vendor is on hold if either is (D-105).
    holds: dict[str, str] = {}
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
            "province": province,
        })  # fmt: skip
        if an8:
            holds[an8] = _text(r.get("hold_code"))
        last4 = _text(r.get("bank_last4"))[-4:]
        if an8 and last4:
            banks[an8] = f"{_text(r.get('bank_transit'))} …{last4}".strip()
    ids = {_text(r.get("an8")) for r in found} - {""}
    named = [(vendor_key(r["vendor_name"]), r["erp_id"]) for r in records]
    keys = {key for name, an8 in named for key in (name, f"{name} #{an8}")}
    before = store.master_vendors_of(keys, ids)
    result = store.import_vendor_master(master_rows(records, SUPPLIER_COLUMNS), actor=run.actor, by_erp_id=True,
                                        log=False)  # fmt: skip
    store.set_erp_bank_and_hold({an8: (banks.get(an8, ""), holds.get(an8, "")) for an8 in sorted(ids)})
    after = store.master_vendors_of(keys, ids)
    _note(run.report, "added", sorted(after[k]["display_name"] for k in after.keys() - before.keys()))
    changed = []
    for key in sorted(after.keys() & before.keys()):
        what = [label for f, label in VENDOR_FIELDS.items() if (before[key].get(f) or "") != (after[key].get(f) or "")]
        if what:
            changed.append(f"{after[key]['display_name']} ({', '.join(what)})")
    _note(run.report, "changed", changed)
    _note(run.report, "duplicates", result["duplicates"])
    run.report["updated"] = run.report.get("updated", 0) + result["updated"]


def _accounts(store: Any, table: str) -> dict[str, dict[str, Any]]:
    return {r["code"]: r for r in store.list_accounts(table)}


def _account_changes(before: dict[str, Any], after: dict[str, Any]) -> tuple[list[str], list[str]]:
    added = [f"{c} {after[c].get('description') or ''}".strip() for c in sorted(after.keys() - before.keys())]
    changed = [f"{c} (description)" for c in sorted(after.keys() & before.keys())
               if before[c].get("description") != after[c].get("description")]  # fmt: skip
    return added, changed


def _import_accounts(store: Any, table: str, rows: dict[str, dict[str, Any]], run: _Run) -> None:
    before = _accounts(store, table)
    result = store.import_accounts(table, list(rows.values()), "code", "description", actor=run.actor, log=False)
    added, changed = _account_changes(before, _accounts(store, table))
    _note(run.report, "added", added)
    _note(run.report, "changed", changed)
    run.report["updated"] = run.report.get("updated", 0) + result["updated"]


def _gl_accounts(store: Any, found: list[dict[str, Any]], run: _Run, read: dict[str, Any]) -> None:
    rows: dict[str, dict[str, Any]] = {}
    subs: dict[str, tuple[str, str]] = {}
    done = run.cache.setdefault("codes", set())  # an object.subsidiary is read once a business unit: the first kept
    for r in found:
        code = _code(r.get("object"), r.get("subsidiary"))
        if not code or _text(r.get("posting_edit")).upper() not in POSTABLE:
            run.report["not_postable"] = run.report.get("not_postable", 0) + 1
            continue
        if code in done:
            continue
        rows.setdefault(code, {"code": code, "description": _text(r.get("description"))})
        if _text(r.get("subsidiary")):
            subs[code] = (_text(r.get("object")), _text(r.get("subsidiary")))
    done |= set(rows)
    _import_accounts(store, "gl_accounts", rows, run)
    # An account with a subsidiary is AP Coder's GL code "object.subsidiary": the JD Edwards mapping sends it as that
    # object and subsidiary (the default rule would send "6200.01" as the object). Saved with the page's accounts (the
    # change logged once for the part).
    settings = jde.load(store)
    mapped = {jde._norm(m.gl_code) for m in settings.gl_map}
    new_maps = [jde.GlMap(code, obj=obj, sub=sub) for code, (obj, sub) in sorted(subs.items())
                if jde._norm(code) not in mapped]  # fmt: skip
    if new_maps:
        settings.gl_map += new_maps
        store.set_setting(jde.SETTING_KEY, settings.to_json(), actor=run.actor)
        _note(run.report, "mapped", [m.gl_code for m in new_maps])


def _business_units(store: Any, found: list[dict[str, Any]], run: _Run, read: dict[str, Any]) -> None:
    rows: dict[str, dict[str, Any]] = {}
    for r in found:
        code = _text(r.get("business_unit"))
        if not code or _text(r.get("posting_edit")).upper() not in POSTABLE:
            run.report["not_postable"] = run.report.get("not_postable", 0) + 1
            continue
        rows.setdefault(code, {"code": code, "description": _text(r.get("description"))})
    _import_accounts(store, "cost_centers", rows, run)


def _po_lines(po: dict[str, Any] | None) -> list[tuple[Any, ...]]:
    return [(li["line_number"], li["description"], li["quantity"], li["unit_price"], li["amount"],
             li["received_quantity"], li["gl_code"], li["cost_center"])
            for li in (po or {}).get("lines") or []]  # fmt: skip


def line_number(value: Any) -> int | None:
    """A PO line's number from JDE's (LNID, "1.000"): a whole one as it is (1); a split line's (1.500) in JDE's own
    thousandths (1500), so no two lines of a PO share a number and no line after it is renumbered (R9)."""
    try:
        number = float(_text(value) or 0)
    except ValueError:
        return None
    if not number or not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else round(number * 1000)


def _whole_pos(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(the rows of the POs complete in ``rows``, the last PO's, which the next page may go on with)."""
    last = po_mod.po_key(_text(rows[-1].get("order_number"))) if rows else ""
    cut = len(rows)
    while cut and po_mod.po_key(_text(rows[cut - 1].get("order_number"))) == last:
        cut -= 1
    return rows[:cut], rows[cut:]


def _with_lines_kept(rows: list[dict[str, Any]], before: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """What changed of a PO merged into its lines here: each line of the answer replaces the line of its number, the
    others are kept (an answer of a PO's changed lines alone, or all of them: the same). A PO in the answer with a line
    without a number is taken as it is."""
    out: list[dict[str, Any]] = []
    by_po: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_po.setdefault(po_mod.po_key(r["po_number"]), []).append(r)
    for key, lines in by_po.items():
        given = {r.get("line_number") for r in lines}
        had = before.get(key)
        if had and None not in given:
            status = po_mod.CLOSED if had["status"] == po_mod.CLOSED else po_mod.OPEN
            name = lines[0].get("vendor_name") or had["vendor_name"]
            out += [{"po_number": lines[0]["po_number"], "vendor_name": name, "line_number": li["line_number"],
                     "description": li["description"], "quantity": li["quantity"], "unit_price": li["unit_price"],
                     "amount": li["amount"], "amount_only": bool(li.get("amount_only")),
                     "open_amount": li.get("open_amount"), "received_quantity": li["received_quantity"],
                     "gl_code": li["gl_code"], "cost_center": li["cost_center"], "status": status}
                    for li in had["lines"] if li["line_number"] not in given]  # fmt: skip
        out += lines
    return out


def _open_lines(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each PO's open lines, as a whole read lists them: a line JDE closed (999) leaves while another line of its PO
    is open (a line here has no status of its own: one kept by a later read of what changed is taken as open); with
    none open, every line stays and the PO is closed."""
    open_pos = {po_mod.po_key(r["po_number"]) for r in rows if r.get("status") != po_mod.CLOSED}
    return [r for r in rows if r.get("status") != po_mod.CLOSED or po_mod.po_key(r["po_number"]) not in open_pos]


def _purchase_orders(store: Any, found: list[dict[str, Any]], run: _Run, read: dict[str, Any]) -> None:
    names = run.names(store)
    records = []
    for r in found:
        records.append({
            "po_number": _text(r.get("order_number")),
            "vendor_name": _text(r.get("supplier_name")) or names.get(_text(r.get("an8")), ""),
            "line_number": line_number(r.get("line_number")), "description": _text(r.get("description")),
            "quantity": r.get("quantity"), "unit_price": r.get("unit_cost"), "amount": r.get("amount"),
            "received_quantity": r.get("received"), "gl_code": _code(r.get("object"), r.get("subsidiary")),
            "cost_center": _text(r.get("business_unit")),
            "status": "closed" if _text(r.get("next_status")) == CLOSED_STATUS else "open",
        })  # fmt: skip
    rows, skipped = po_mod.rows_from_records(records, {k: k for k in records[0]} if records else {})
    run.report["skipped"] = run.report.get("skipped", 0) + skipped
    keys = sorted({po_mod.po_key(r["po_number"]) for r in rows} - {""})
    before = store.purchase_orders_by_key(keys)
    if run.mode == CHANGES:
        rows = _with_lines_kept(rows, before)
    result = store.import_purchase_orders(_open_lines(rows), actor=run.actor, log=False)
    after = store.purchase_orders_by_key(keys)
    _note(run.report, "added", [after[k]["po_number"] for k in keys if k in after and not _po_lines(before.get(k))])
    _note(run.report, "changed", [after[k]["po_number"] for k in keys if k in after and _po_lines(before.get(k))
                                  and _po_lines(before[k]) != _po_lines(after[k])])  # fmt: skip
    for counted in ("orders", "updated", "lines"):
        run.report[counted] = run.report.get(counted, 0) + result[counted]
    run.cache.setdefault("seen", set()).update(keys)


def _date(value: Any, fmt: str) -> str:
    """A date AIS gave as YYYY-MM-DD: in the mapping's ``date_format`` (its inputs' and outputs': "10/07/2026" in
    %m/%d/%Y is 7 October), else as ``iso_date`` reads it ("" for none, the text when it can't be read)."""
    text = _text(value)
    try:
        return dt.datetime.strptime(text, fmt).date().isoformat()
    except ValueError:
        return iso_date(text)


def voucher_rows(found: list[dict[str, Any]], names: dict[str, str], fmt: str = "%Y-%m-%d") -> list[dict[str, Any]]:
    """F0411 rows as the ERP invoice register's rows (``registers.rows_from_records`` output), with the voucher's key
    and its supplier's number; ``fmt``: the mapping's ``date_format``."""
    rows = []
    for r in found:
        name = _text(r.get("supplier_name")) or names.get(_text(r.get("an8")), "")
        number, total = _text(r.get("invoice_number")), parse_amount(r.get("gross"))
        key = _text(r.get("key")) or "-".join(_text(r.get(f)) for f in ("company", "doc_type", "document"))
        if name and number and total is not None:
            rows.append({"vendor_name": name, "invoice_number": number, "key": key, "an8": _text(r.get("an8")),
                         "invoice_date": _date(r.get("invoice_date"), fmt), "total": total})  # fmt: skip
    return rows


def _vouchers(store: Any, found: list[dict[str, Any]], run: _Run, read: dict[str, Any]) -> None:
    rows = voucher_rows(found, run.names(store), run.date_format)
    run.report["new"] = run.report.get("new", 0) + store.import_jde_vouchers(rows, read["seen"])


WRITE = {"suppliers": _suppliers, "gl_accounts": _gl_accounts, "business_units": _business_units,
         "purchase_orders": _purchase_orders, "vouchers": _vouchers}  # fmt: skip


def _too_many(gone: int, there: int) -> bool:
    """Most or all of what there is gone at once: more likely a wrong answer (a filter, a page lost, another order
    type, row security) than a month's work."""
    return bool(gone) and (gone == there or gone > MASS_CLOSE_SHARE * there)


def _removes(part: str, plan: dict[str, Any], read: dict[str, Any]) -> bool:
    """Whether ``read`` decides what leaves (the POs a whole read no longer lists closed, the vouchers swept): it is
    read to the end and must end complete."""
    return bool(read.get("sweep")) or (part == "purchase_orders" and plan["mode"] == FULL)


def _cut_short(run: _Run, read: dict[str, Any], what: str, done: str) -> bool:
    """Whether ``read`` ended without being known complete (on a page as long as the longest before it: its last row
    without its key, nothing to ask the next page after): then nothing is closed or removed, said in the report."""
    if read.get("complete"):
        return False
    run.report[f"refused_{done}"] = (
        f"JD Edwards' answer stopped at a {what} without its key, so the rest could not be asked for: none was "
        f"{done}d. The JDE team should check that every row of the orchestration has its key."
    )  # fmt: skip
    log.warning("JD Edwards %s sync: %s", what, run.report[f"refused_{done}"])
    return True


def _close_pos(store: Any, run: _Run, read: dict[str, Any]) -> None:
    """The open POs synced before that a whole read no longer lists are closed (most or all at once, or the read not
    known complete: refused, and the next whole read looks again); the POs known as synced kept in the state."""
    before = set(state(store).get("po_keys") or [])
    seen = set(run.cache.get("seen") or ())
    if run.report.get("resumed"):  # the pages read before it stopped: the POs written since it began
        seen |= store.po_keys_written_since(run.plan["started"])
    if run.mode != FULL or _cut_short(run, read, "PO line", "close"):
        _save_state(store, po_keys=sorted(before | seen))
        return
    open_synced = store.open_po_numbers(sorted(before))
    gone = [k for k in open_synced if k not in seen]
    if not run.allow_mass_close and _too_many(len(gone), len(open_synced)):
        run.report["refused_close"] = (
            f"JD Edwards' answer would close {len(gone)} of the {len(open_synced)} open POs synced before: none "
            "was closed. If that is right, sync again with --allow-mass-close (python -m ap_coder jde sync)."
        )
        log.warning("JD Edwards PO sync: %s", run.report["refused_close"])
        seen |= set(gone)  # still known as synced, so a later sync can close them
    else:
        for start in range(0, len(gone), WRITE_ROWS):
            with run.pace.write(store):
                for key in gone[start : start + WRITE_ROWS]:
                    store.set_po_status(key, po_mod.CLOSED, actor=run.actor)
        _note(run.report, "closed", [open_synced[k] for k in gone])
    _save_state(store, po_keys=sorted(seen))


def _remove_vouchers(store: Any, run: _Run, read: dict[str, Any]) -> None:
    """After a read of every voucher from a date: those of the register from then it no longer lists (voided or
    deleted in JDE) leave it, and after the whole read of the window those without a date too (most or all at once:
    refused, as a wrong answer; the read not known complete: none)."""
    if not read.get("sweep") or _cut_short(run, read, "voucher", "remove"):
        return
    gone, there = store.jde_vouchers_unseen(read["sweep"], read["seen"], undated=bool(read.get("undated")))
    if not run.allow_mass_close and _too_many(len(gone), there):
        run.report["refused_remove"] = (
            f"JD Edwards' answer would remove {len(gone)} of the {there} vouchers dated after {read['sweep']} from the "
            "register: none was removed. If that is right, sync again with --allow-mass-close (python -m ap_coder "
            "jde sync)."
        )
        log.warning("JD Edwards voucher sync: %s", run.report["refused_remove"])
        return
    for start in range(0, len(gone), WRITE_ROWS):
        with run.pace.write(store):
            store.remove_jde_vouchers(gone[start : start + WRITE_ROWS], read["seen"])
    run.report["removed"] = run.report.get("removed", 0) + len(gone)


AFTER_READ = {"purchase_orders": _close_pos, "vouchers": _remove_vouchers}


class _LeaseLost(Exception):
    """Another sync took this one's lease (it waited on JD Edwards longer than LEASE_SECONDS): it stops there."""


def _hold(store: Any, lease: str) -> None:
    """The sync's lease renewed, else _LeaseLost: nothing more written (the caller's transaction undone)."""
    if lease and not store.renew_lease(LEASE, lease, LEASE_SECONDS):
        raise _LeaseLost("stopped: another sync took over while this one waited on JD Edwards; it reads everything "
                         "this one would.")  # fmt: skip


def _log_part(store: Any, run: _Run) -> None:
    """The part's import, logged once for all its pages, as its CSV import logs it (nothing read: nothing logged)."""
    r = run.report
    if not r["read"]:
        return
    if run.part == "suppliers":
        detail: dict[str, Any] = {"added": _count(r, "added"), "updated": r.get("updated", 0),
                                  "duplicates": _count(r, "duplicates")}  # fmt: skip
        store.log_event("vendors_imported", actor=run.actor, detail=detail)
    elif run.part in ("gl_accounts", "business_units"):
        table = "gl_accounts" if run.part == "gl_accounts" else "cost_centers"
        store.log_event("accounts_imported", actor=run.actor, detail={
            "table": table, "added": _count(r, "added"), "updated": r.get("updated", 0),
            "skipped": r.get("not_postable", 0), "replace_all": False})  # fmt: skip
        if _count(r, "mapped"):
            store.log_event("settings_changed", actor=run.actor, detail={"keys": [jde.SETTING_KEY]})
    elif run.part == "purchase_orders":
        store.log_event("pos_imported", actor=run.actor, detail={
            "orders": r.get("orders", 0), "added": _count(r, "added"), "updated": r.get("updated", 0),
            "lines": r.get("lines", 0), "closed": _count(r, "closed"), "replace_all": False})  # fmt: skip
    else:
        store.log_event("erp_register_imported", actor=run.actor, detail={
            "rows": r["read"], "total": store.erp_register_count(), "removed": r.get("removed", 0)})  # fmt: skip


# --- A part's read: what it asks, page after page, from where it stopped ---------------------------------------


def _plan(store: Any, client: jde_ais.AisClient, part: str, full: bool) -> dict[str, Any]:
    """What to read of ``part``: the read left unfinished (its scope the same, begun less than RESUME ago) when it is
    the kind due or a whole one (which reads the changes too), else a new one: whole (``full``, never read whole, a
    week since, its scope changed) or what changed since the last good read. The register cleared or replaced since a
    read of the vouchers began (Exports): what it wrote is gone, so the next is a new whole one."""
    cfg = client.cfg
    scope = {k: getattr(cfg, k) for k in SCOPE.get(part, ())}
    done = (state(store).get("parts") or {}).get(part) or {}
    left = store.get_json(PROGRESS_KEY, {}).get(part)
    if part == "vouchers" and (cleared := store.get_setting(CLEARED_KEY)):
        done = {} if cleared >= str(done.get("at") or "") else done
        left = None if cleared >= str((left or {}).get("started") or "") else left
    last, whole_at = _when(done.get("at")), _when(done.get("full"))
    whole = (full or part in WHOLE or last is None or whole_at is None or done.get("scope") != scope
             or _now() - whole_at >= dt.timedelta(days=FULL_DAYS))  # fmt: skip
    mode = FULL if whole else CHANGES
    begun = _when((left or {}).get("started"))
    if left and left.get("mode") in (mode, FULL) and left.get("scope") == scope and begun and _now() - begun < RESUME:
        left["report"]["resumed"] = True
        return left
    fmt = client.ais.get("date_format") or "%Y-%m-%d"
    today = _now().date()
    changed = "" if last is None or whole else (last.date() - dt.timedelta(days=MARGIN_DAYS)).strftime(fmt)
    report: dict[str, Any] = {"read": 0, "mode": mode, "added": [], "changed": [], **REPORTED.get(part, {})}
    if changed:
        report["changed_since"] = changed
    if part == "vouchers":
        window = today - dt.timedelta(days=cfg.voucher_days)
        report["since"] = window.strftime(fmt)
        reads = [{"inputs": {"since": window.strftime(fmt), "changed_since": changed},
                  **({} if changed else {"sweep": window.isoformat(), "undated": True})}]  # fmt: skip
        if changed:  # and every voucher of the last weeks, whole: one voided then leaves
            recent = today - dt.timedelta(days=min(RECENT_DAYS, cfg.voucher_days))
            reads.append({"inputs": {"since": recent.strftime(fmt), "changed_since": ""}, "sweep": recent.isoformat()})
    else:
        inputs: dict[str, Any] = {"suppliers": {}, "gl_accounts": {"company": cfg.company},
                                  "business_units": {"company": cfg.company},
                                  "purchase_orders": {"order_type": cfg.po_order_type}}[part]  # fmt: skip
        if part not in WHOLE:
            inputs["changed_since"] = changed
        reads = [{"inputs": inputs}]
    return {"mode": mode, "scope": scope, "started": _now().isoformat(timespec="seconds"), "reads": reads, "step": 0,
            "after": "", "report": report}  # fmt: skip


def _sync_part(store: Any, client: jde_ais.AisClient, part: str, actor: str | None, *, full: bool = False,
               allow_mass_close: bool = False, lease: str = "") -> dict[str, Any]:  # fmt: skip
    """Read ``part`` from JD Edwards page after page, each page written as it comes in its own transaction with the
    read's progress; what a read closes or removes, after its last page. The part's report. One connection to the
    database for it all (a connection opened and closed a page would write the database's log out each time)."""
    with store.session():
        plan = _plan(store, client, part, full)
        run = _Run(part, plan, actor, allow_mass_close, client.ais.get("date_format") or "%Y-%m-%d")
        spec = jde_ais.spec_of(client.mapping, part)
        progress = store.get_json(PROGRESS_KEY, {})
        for step in range(plan["step"], len(plan["reads"])):
            read = plan["reads"][step]
            read.setdefault("seen", time.time_ns() // 1_000_000)  # the read's stamp on what it lists (a resumed one's)
            plan["step"] = step
            held: list[dict[str, Any]] = []
            to_the_end = _removes(part, plan, read)
            for page, following in client.pages(part, read["inputs"], after=plan["after"], to_the_end=to_the_end):
                rows, held = _whole_pos(held + page) if part == "purchase_orders" and following else (held + page, [])
                run.report["read"] += len(page)
                # complete when it ends on an empty page or one shorter than the longest before it (AIS gives that)
                read["complete"] = not page or len(page) < read.get("longest", 0)
                read["longest"] = max(read.get("longest", 0), len(page))
                if part == "vouchers":  # a long page a few hundred at a time, each with the lease (B-1918)
                    while len(rows) > VOUCHERS_A_WRITE:
                        with run.pace.write(store):
                            WRITE[part](store, rows[:VOUCHERS_A_WRITE], run, read)
                            _hold(store, lease)
                        rows = rows[VOUCHERS_A_WRITE:]
                # the page (its last part), where the read is and the lease: one short transaction
                with run.pace.write(store):
                    if rows:
                        WRITE[part](store, rows, run, read)
                        plan["after"] = jde_ais.row_key(spec, rows[-1])
                    progress[part] = plan
                    store.set_setting(PROGRESS_KEY, json.dumps(progress))
                    _hold(store, lease)  # last (the write lock taken at the page's first write); lost: all undone
            if part in AFTER_READ:
                _hold(store, lease)
                AFTER_READ[part](store, run, read)
            plan["step"], plan["after"] = step + 1, ""
            store.set_setting(PROGRESS_KEY, json.dumps(progress))
        _log_part(store, run)
        progress.pop(part, None)
        with store.transaction():
            store.set_setting(PROGRESS_KEY, json.dumps(progress))
            parts = state(store).get("parts") or {}
            whole_at = plan["started"] if plan["mode"] == FULL else (parts.get(part) or {}).get("full")
            _save_state(store, parts={**parts, part: {"at": plan["started"], "full": whole_at, "scope": plan["scope"]}})
    return run.report


def sync(store: Any, *, parts: tuple[str, ...] = PARTS, client: jde_ais.AisClient | None = None,
         actor: str | None = None, allow_mass_close: bool = False, full: bool = False) -> dict[str, Any]:  # fmt: skip
    """Read each part from JD Edwards into the store. {"ok", "at", "parts": {part: report}, "errors": {part: why}}.
    Step 1 must be on (else ``AisOff``, nothing sent). JDE unreachable: the parts left are not tried. Another sync
    under way (here or on another computer of the company server): nothing is tried, nothing saved; one that took
    over meanwhile (this one waited on JDE longer than its lease): this one stops there, nothing more saved.
    ``allow_mass_close``: close the POs (remove the vouchers) JDE no longer lists even when that is most or all of
    them. ``full``: every part read whole (what JDE deleted, closed or voided is seen only so)."""
    own = client is None
    client = client or jde_ais.client(store, need="step1")
    report: dict[str, Any] = {"at": _now().isoformat(timespec="seconds"), "parts": {}, "errors": {}}
    lease = f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    if not store.take_lease(LEASE, lease, LEASE_SECONDS):
        if own:
            client.close()
        busy = "not tried: another sync is under way; it reads everything this one would."
        report["errors"] = dict.fromkeys(parts, busy)
        report["ok"] = False
        return report
    lost = False
    try:
        for part in parts:
            try:
                report["parts"][part] = _sync_part(store, client, part, actor, full=full,
                                                   allow_mass_close=allow_mass_close, lease=lease)  # fmt: skip
            except _LeaseLost as exc:
                lost = True
                report["errors"].update(dict.fromkeys(parts[parts.index(part) :], str(exc)))
                break
            except jde_ais.AisError as exc:
                report["errors"][part] = str(exc)
                if exc.kind in jde_ais.DOWN or exc.kind in (jde_ais.AUTH, jde_ais.CERTIFICATE, jde_ais.ADDRESS):
                    for rest in parts[parts.index(part) + 1 :]:
                        report["errors"][rest] = "not tried: " + str(exc)
                    break
    finally:
        store.drop_lease(LEASE, lease)
        if own:
            client.close()
    report["ok"] = not report["errors"]
    saved = {**state(store), "tried": report["at"], "report": report}
    if report["ok"]:
        saved["at"] = report["at"]
    if not lost:  # (else the sync that took over says how it went)
        store.set_setting(STATE_KEY, json.dumps(saved))
    store.log_event("jde_synced", actor=actor, detail={
        "ok": report["ok"], **{p: {k: _count(r, k) if isinstance(v, list) else v for k, v in r.items()}
                               for p, r in report["parts"].items()}, "errors": sorted(report["errors"])})  # fmt: skip
    return report


def report_lines(report: dict[str, Any], shown: int = 10) -> list[str]:
    """The sync's report as plain lines (the settings page's message, ``jde sync``)."""
    lines = []
    for part, r in (report.get("parts") or {}).items():
        if part == "vouchers":
            bits = [f"{r.get('read', 0)} read"]
            if r.get("new"):
                bits.append(f"{r['new']} new")
            if r.get("removed"):
                bits.append(f"{r['removed']} no longer in JD Edwards (voided or deleted), out of the register")
            if r.get("refused_remove"):
                bits.append(r["refused_remove"])
        else:
            bits = [f"{_count(r, 'added')} added", f"{_count(r, 'changed')} changed"]
        if r.get("closed"):
            bits.append(f"{_count(r, 'closed')} closed in JDE")
        if r.get("refused_close"):
            bits.append(r["refused_close"])
        if part == "gl_accounts" and r.get("changed"):  # JDE's words over AP Coder's: said (B-1210)
            n = _count(r, "changed")
            bits.append(f"{n} description{'' if n == 1 else 's'} taken from JD Edwards")
        if r.get("not_postable"):
            bits.append(f"{r['not_postable']} not postable, left out")
        what = f" (what changed since {r['changed_since']})" if r.get("changed_since") else ""
        line = f"{LABELS[part]}{what}: " + ", ".join(bits)
        detail = [*(r.get("added") or []), *(r.get("changed") or [])] if part != "vouchers" else []
        if detail:
            more = _count(r, "added") + _count(r, "changed") > min(len(detail), shown)
            line += ": " + "; ".join(detail[:shown]) + ("…" if more else "")
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
LIVE_PAGES = 40  # at most this many pages (of 500) of one supplier's vouchers
TOO_WIDE = (jde_ais.TooManyPages, jde_ais.PageRepeated)  # the live check's question too wide: JDE is not down
# What JDE answered, by (database, AN8, the invoice number when asked by it): (when, its vouchers), within CACHE_MOST
# bytes, the ones used longest ago going first; one CACHE_SECONDS old goes as another is kept (B-2025).
CACHE_MOST = 32 * MB
_CACHE: Bounded = Bounded("JD Edwards' live check", CACHE_MOST)
_DOWN: dict[str, tuple[float, str]] = {}
_BIG: set[tuple[str, str]] = set()  # (database, AN8): more vouchers in the window than LIVE_PAGES, asked by number
_CLIENTS: dict[str, jde_ais.AisClient] = {}  # database -> this process's client for the live check, signed in once
# JDE not reached at the address the client resolved when it was made: the next check makes another, its name looked up
# again (JDE moved to another server) and jde_orchestrations.json read again (B-1609).
_NEW_CLIENT = frozenset({jde_ais.UNREACHABLE, jde_ais.TIMEOUT, jde_ais.CERTIFICATE})
_LOCK = threading.Lock()
PAID = {"P": "paid", "A": "approved for payment, not paid yet", "H": "on hold in JDE"}


def forget_cache() -> None:
    """Forget what JDE said, and log the live check's clients out (the settings saved; the process stopping)."""
    with _LOCK:
        _CACHE.clear()
        _DOWN.clear()
        _BIG.clear()
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for c in clients:
        c.close()


atexit.register(forget_cache)


def _client(store: Any) -> jde_ais.AisClient:
    """The live check's client for this database: one token for every check (and every thread), until the settings
    change."""
    cfg = jde_ais.load(store)
    cfg.timeout = min(cfg.timeout, LIVE_TIMEOUT)
    where = str(store.path)
    with _LOCK:
        c = _CLIENTS.get(where)
        if c is not None and c.cfg == cfg and c.cfg.password == cfg.password:
            return c
        old = _CLIENTS.pop(where, None)
    if old is not None:
        old.close()
    made = jde_ais.AisClient(cfg)
    made.retries = 0
    with _LOCK:
        return _CLIENTS.setdefault(where, made)  # not signed in yet: one made at the same time is simply dropped


def _ask(store: Any, an8: str, printed: str, big: bool) -> tuple[list[dict[str, Any]], bool]:
    """The vouchers in JDE that may be this invoice, and whether they were asked by its number (as printed: JDE
    matches it exactly)."""
    c = _client(store)
    since = (_now().date() - dt.timedelta(days=c.cfg.voucher_days)).strftime(c.ais.get("date_format") or "%Y-%m-%d")
    if not an8:
        return c.rows("vouchers", {"invoice": printed, "since": since}, max_pages=LIVE_PAGES), True
    if not big:
        # Every page of the supplier's vouchers of the sync's window (the invoice numbers are compared here, written
        # any way): a supplier with more vouchers than a page is checked to the last one.
        try:
            return c.rows("vouchers", {"supplier": an8, "since": since}, max_pages=LIVE_PAGES), False
        except TOO_WIDE as exc:
            if isinstance(exc, jde_ais.PageRepeated):
                log.warning("JD Edwards live check: %s Asked by the invoice number instead.", exc)
    # More than LIVE_PAGES even so: by the supplier and the invoice number as printed (JDE matches it exactly)
    return c.rows("vouchers", {"supplier": an8, "invoice": printed}, max_pages=LIVE_PAGES), True


def live_findings(store: Any, coding: Any, exclude_invoice_id: int | None = None) -> list[tuple[str, str, str]]:
    """(severity, code, message) of the live check: DUPLICATE_IN_ERP for a voucher already in JDE with this supplier
    and invoice number (not the one AP Coder posted for this invoice), or JDE_NOT_REACHED when JDE can't be asked
    (with the register's copies, by the supplier's AN8 as well as its name). Nothing when step 1 is off."""
    cfg = jde_ais.load(store)
    number = norm_invoice_number(getattr(coding, "invoice_number", ""))
    if not cfg.step1 or not number:
        return []
    final = {"vendor_name": coding.vendor_name, "gst_hst_registration_number": coding.gst_hst_registration_number}
    ids = store.vendor_ids()
    an8 = jde.resolve_an8(final, ids, jde.load(store))
    where = str(store.path)
    now = time.monotonic()
    with _LOCK:
        big = (where, an8) in _BIG
        cached = _CACHE.get((where, an8, number if not an8 or big else ""))
        down = _DOWN.get(where)
    rows: list[dict[str, Any]] | None = cached[1] if cached and now - cached[0] < CACHE_SECONDS else None
    if rows is None:
        if down and now - down[0] < DOWN_SECONDS:
            return [_not_reached(store, down[1]), *_in_register(store, coding, an8, ids)]
        try:
            rows, by_number = _ask(store, an8, str(coding.invoice_number or "").strip(), big)
        except jde_ais.AisError as exc:
            dropped = None
            if not isinstance(exc, TOO_WIDE):  # too wide a question is this invoice's, not JDE down
                with _LOCK:
                    _DOWN[where] = (now, str(exc))
                    if exc.kind in _NEW_CLIENT:
                        dropped = _CLIENTS.pop(where, None)
            if dropped is not None:  # logged out aside: the person's check doesn't wait on a JDE that didn't answer
                threading.Thread(target=dropped.close, daemon=True).start()
            return [_not_reached(store, str(exc)), *_in_register(store, coding, an8, ids)]
        with _LOCK:
            if an8 and by_number:
                _BIG.add((where, an8))
            for old in [k for k, (at, _) in _CACHE.items() if now - at >= CACHE_SECONDS]:
                del _CACHE[old]
            _CACHE[(where, an8, number if by_number else "")] = (now, rows)
            _DOWN.pop(where, None)
    from . import jde_post

    own = set(jde_post.documents_of(store, exclude_invoice_id)) if exclude_invoice_id else set()
    credit = float(coding.grand_total or 0) < 0
    out = []
    for r in rows:
        if norm_invoice_number(r.get("invoice_number")) != number or _text(r.get("document")) in own:
            continue
        if not an8 and vendor_key(_text(r.get("supplier_name"))) != vendor_key(coding.vendor_name or ""):
            continue
        gross = parse_amount(r.get("gross"))
        if gross is not None and (gross < 0) != credit:
            continue
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


def _in_register(store: Any, coding: Any, an8: str, ids: dict[str, str]) -> list[tuple[str, str, str]]:
    """JDE not reached: DUPLICATE_IN_ERP for the register's copies of the invoice, under its vendor name and every
    vendor of its AN8, and by the AN8 itself (the sync files a voucher under JDE's supplier name, which may not be the
    invoice's, with its supplier number). Without an AN8 nothing: the review screen's vendor checks look the register
    up by name."""
    if not an8:
        return []
    keys = {vendor_key(coding.vendor_name or "")} | {k for k, v in ids.items() if v == an8 and ":" not in k}
    out, said = [], set()
    for key in sorted(keys - {""}):
        for posted in store.in_erp(key, coding.invoice_number, coding.grand_total, an8=an8):
            if (bill := (posted["invoice_number"], posted["invoice_date"], posted["total"])) in said:
                continue
            said.add(bill)
            out.append((ERROR, "DUPLICATE_IN_ERP",
                        f"invoice {posted['invoice_number']} from this vendor is already in the ERP"
                        f"{' (dated ' + posted['invoice_date'] + ')' if posted['invoice_date'] else ''}, total "
                        f"{posted['total']:,.2f}: possible duplicate payment"))  # fmt: skip
    return out[:2]


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
