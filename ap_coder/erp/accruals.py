"""Month-end accruals: costs incurred by the period end that are not yet in the ERP.

Three sources, none counted twice:

* Received, not invoiced: purchase order lines received (quantity received known) beyond what has been
  invoiced so far, at the PO price, on the PO line's GL account
* Invoices not yet in the ERP: invoices dated on or before the period end that are still in review,
  waiting for a second approval, approved but not exported, or exported after the period end (in the ERP's next
  month, D-133), by GL account (net of recoverable tax); a copy flagged as a duplicate of a bill already exported or
  counted, or as already in the ERP, is left out. One no person has approved yet is "proposed", on the accounts it
  was given (a guessed account counts as none, B-466); one dated more than a year before the period end is accrued
  and flagged to check (a date read wrong, or a bill long overdue: AP decides), one with no date is not accrued but
  listed (``undated``)
* Expected recurring invoices: regular vendors whose next invoice was due by the period end and has not
  arrived, at their usual amount, on the GL account most used for them (an estimate). Not for a vendor whose
  received, not invoiced PO lines are accrued: that invoice would bill them, so it is left out (``left_out``)

Entry points: ``build`` (with ``undated``: the invoices with no date, not accrued; ``left_out``: the expected
recurring invoices left out), ``by_gl_columns``, ``summary`` and ``to_csv``.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import threading
from collections import defaultdict
from dataclasses import dataclass, fields

from ..coding.po import billed_on, po_label, positive_lines
from ..coding.vendors import norm_invoice_number
from ..core.memory import vendor_key
from ..core.safe import csv_row
from ..core.store import APPROVED, PARKED, PENDING, REVIEW, Store
from ..core.store.reports import asked_key
from ..core.store.summary import broken, whole
from . import recurring
from .exports import distribution

RECEIVED, NOT_IN_ERP, RECURRING = "Received, not invoiced", "Invoice not in the ERP yet", "Expected recurring invoice"
SOURCES = (RECEIVED, NOT_IN_ERP, RECURRING)
OLD_DAYS = 365  # an invoice dated more than this before the period end is flagged: a misread date, or long overdue
OLD_NOTE = "dated over a year ago: check"
# Not approved by anyone yet: its coding is what AP Coder proposed, not what a person confirmed.
PROPOSED = "proposed: not approved yet"
# Exported by a batch made after the period end: not in the ERP at the period end, so accrued for it (D-133).
EXPORTED_LATE = "exported after the month end"
# An expected recurring invoice not accrued: its vendor's received, not invoiced PO lines are, and it would bill them.
ON_PO = "left out: its received, not invoiced purchase-order lines are accrued instead"
# The export batches made after a period end (``?``): their invoices were not in the ERP at that period end (D-133).
_LATE_BATCHES = "SELECT id FROM export_batches WHERE substr(created_at, 1, 10) > ?"
# The Month-end page's schedule: under a stream of changes (100 people approving) worked out again at most this often
# in each process, once an invoice changed (``build``, D-166).
SETTLE_SECONDS = 30.0


@dataclass
class Accrual:
    source: str
    vendor: str
    reference: str
    description: str
    gl_code: str
    cost_center: str
    amount: float
    currency: str = "CAD"
    note: str = ""
    old: bool = False  # dated more than OLD_DAYS before the period end: accrued, flagged for AP to check


# Each received PO's accruals as last worked out in this process, by database and period end: {PO: (a digest of what
# they were worked out from, the accruals)}. After a change only the POs whose lines, billing invoices or those
# invoices' vendors' invoices changed (``changes/vendor/<key>``, D-159) are worked out again, not every PO (69,000
# codings decoded at 100,000 invoices: 6.5 s); nothing worked out in a write under way is kept (D-166). The same
# are kept for every process of the company server (``PARTS_ANSWER``, D-169): a process that has none of a PO kept
# takes it from there while its digest is the same, instead of decoding its billing invoices again (3.4 s at 100,000
# invoices the first time in each web worker).
_PARTS: dict[tuple[str, str], dict[str, tuple[bytes | None, list[Accrual]]]] = {}
_PARTS_LOCK = threading.Lock()
PARTS_KEPT = 8  # the databases and period ends kept
PARTS_ANSWER = "received_parts"
# The accruals as they are kept for every process (``Store.shared_answer``, ``keep_answer``): each its fields in
# ``Accrual``'s order. Another format when what is kept changes, so what an older AP Coder kept is worked out again.
ACCRUALS_FORMAT = "accruals-1"
_FIELDS = tuple(f.name for f in fields(Accrual))


def _data(accruals: list[Accrual]) -> list[list[object]]:
    return [[getattr(a, name) for name in _FIELDS] for a in accruals]


def _of(data: list[list[object]]) -> list[Accrual]:
    return [Accrual(*row) for row in data]  # type: ignore[arg-type]


def _received_not_invoiced(store: Store, period_end: dt.date) -> list[Accrual]:
    end = period_end.isoformat()
    orders = store.purchase_orders_by_key(store.received_po_keys())
    # Returns are assumed to be reflected in the ERP's received quantity, so credits do not count here; the vendor's
    # invoices that quote no PO may still bill these lines ("PO-90155" in a description). Invoices dated after the
    # period end do not count: the goods were received, not yet invoiced, by then. Each invoice's lines are read once
    # (``billing_codings``, not once for each PO of its vendor), only those billing a quantity kept.
    vendors = {full["vendor_key"] for full in orders.values()}
    rows, counts, stamp = store.billing_rows(list(orders), list(vendors))
    on_po: dict[str, list] = {}
    without_po: dict[str, list] = {}  # by vendor
    for r in rows:  # (id, invoice date, PO, vendor)
        if r[2] in orders:
            on_po.setdefault(r[2], []).append(r)
        if r[2] == "" and r[3] in vendors:
            without_po.setdefault(r[3], []).append(r)
    key = (str(store.path), end)
    with _PARTS_LOCK:
        kept = _PARTS.get(key, {})
    parts: dict[str, tuple[bytes | None, list[Accrual]]] = {}
    todo: dict[str, tuple[bytes | None, list[tuple]]] = {}
    for po_key, full in orders.items():
        on, without = on_po.get(po_key, []), without_po.get(full["vendor_key"], [])
        made_of = _made_of(stamp, full, on, without, counts)
        hit = kept.get(po_key)
        if made_of is not None and hit is not None and hit[0] == made_of:
            parts[po_key] = hit
        else:
            todo[po_key] = (made_of, on + without)
    if todo and stamp is not None:  # worked out in another process meanwhile, for the same digest (D-169)
        shared = _shared_parts(store, end)
        for po_key in [k for k, (made_of, _) in todo.items() if made_of is not None and k in shared]:
            if shared[po_key][0] == todo[po_key][0]:
                parts[po_key] = shared[po_key]
                del todo[po_key]
    dates = {r[0]: r[1] for _, billing in todo.values() for r in billing}
    lines: dict[int, list] = {}  # each invoice's lines billing a quantity, by id (none: dated after the period end)
    for invoice_id, coding in store.billing_codings(sorted(dates)):
        dated = str(dates[invoice_id] or coding.get("invoice_date") or "")
        lines[invoice_id] = [] if dated > end else positive_lines([{"coding": coding}])
    for po_key, (made_of, billing) in todo.items():
        parts[po_key] = (made_of, _accrued(orders[po_key], [li for r in billing for li in lines.get(r[0], ())]))
    if stamp is not None:
        kept_now = {po_key: part for po_key, part in parts.items() if part[0] is not None}
        with _PARTS_LOCK:
            _PARTS.pop(key, None)  # (the latest asked last: the oldest goes first)
            _PARTS[key] = kept_now
            while len(_PARTS) > PARTS_KEPT:
                _PARTS.pop(next(iter(_PARTS)))
        if any(made_of is not None for made_of, _ in todo.values()):  # some worked out here: for the others too
            store.keep_answer(PARTS_ANSWER, asked_key(end), ACCRUALS_FORMAT, list(stamp),
                              {po_key: [made_of.hex(), _data(found)] for po_key, (made_of, found) in kept_now.items()
                               if made_of is not None})  # fmt: skip
    return [a for po_key in orders for a in parts[po_key][1]]


def _shared_parts(store: Store, end: str) -> dict[str, tuple[bytes, list[Accrual]]]:
    """Each received PO's accruals at ``end`` as a process last kept them for the others: {PO: (the digest of what
    they were worked out from, the accruals)}; none when none were (or by another version)."""
    found = store.kept_answer(PARTS_ANSWER, asked_key(end), ACCRUALS_FORMAT)
    if found is None or not isinstance(found[1], dict):
        return {}
    return {po_key: (bytes.fromhex(made_of), _of(data)) for po_key, (made_of, data) in found[1].items()}


def _made_of(stamp: tuple | None, full: dict, on: list[tuple], without: list[tuple], counts: dict[str, object]
             ) -> bytes | None:  # fmt: skip
    """A digest of what a PO's accruals are worked out from: the database (its ``summary_id``: another after a
    restore), the PO and its lines, its billing invoices (``on`` it, and ``without`` a PO from its vendor: which,
    their dates), and their vendors' counts of changes (any change to one of them, whoever made it). None: not to be
    kept (read in a write under way, or an invoice with no vendor key, whose changes no count follows)."""
    vendors = {r[3] for r in on} | {r[3] for r in without}
    if stamp is None or None in vendors:
        return None
    made = (stamp, full, on, without, [(v, counts.get(v)) for v in sorted(vendors)])
    return hashlib.blake2b(repr(made).encode(), digest_size=16).digest()


def _accrued(full: dict, lines: list) -> list[Accrual]:
    """A PO's lines received and not billed by ``lines`` (``positive_lines`` of its billing invoices, in order)."""
    out = []
    billed = billed_on(full, lines)
    for li in full["lines"]:
        if li["received_quantity"] is None or li.get("amount_only"):
            continue
        qty = li["received_quantity"] - billed.get(li["line_number"], 0.0)
        if qty <= 1e-6:
            continue
        out.append(
            Accrual(
                RECEIVED,
                full["vendor_name"],
                f"{po_label(full['po_number'])} line {li['line_number']}",
                li["description"],
                li["gl_code"],
                li["cost_center"],
                round(qty * li["unit_price"], 2),
                note=f"{qty:g} received and not invoiced at {li['unit_price']:,.2f}",
            )  # fmt: skip
        )
    return out


def _bill(doc: dict) -> tuple[str, str, bool]:
    """(vendor, invoice number, is a credit): what makes two invoices the same bill."""
    return (vendor_key(doc.get("vendor_name") or ""), norm_invoice_number(doc.get("invoice_number")),
            float(doc.get("grand_total") or 0) < 0)  # fmt: skip


def _flags(validation: object) -> set[str]:
    issues = validation.get("issues") if isinstance(validation, dict) else None
    return {str(i.get("code")) for i in issues or [] if isinstance(i, dict)}


def _unguessed(doc: dict, ai: dict) -> dict:
    """``doc`` with every guessed account taken out: an account the AI only guessed (``offline_coder.guessed``, the
    line as read in ``ai``) and still the one in ``doc``. An invoice no person has approved accrues only on accounts
    it was really given, never on a guess (B-466); an account the reviewer picked over a guess is theirs."""
    from ..ai.offline_coder import guessed
    from ..core.memory import pair_lines
    from ..core.reference_data import UNASSIGNED

    lines = doc.get("line_items") or []
    guesses = {id(after) for before, after in pair_lines((ai or {}).get("line_items") or [], lines)
               if before and after and guessed(before.get("reasoning_justification"))
               and after.get("predicted_gl_code") == before.get("predicted_gl_code")}  # fmt: skip
    if not guesses:
        return doc
    lines = [{**li, "predicted_gl_code": UNASSIGNED} if id(li) in guesses else li for li in lines]
    return {k: v for k, v in doc.items() if k != "gl_distribution"} | {"line_items": lines}


def _exported_bills(store: Store, rows: list[dict], end: str) -> set[tuple[str, str, bool]]:
    """The bills (``_bill``) of the invoices exported by ``end`` (the period end, D-133), of those that one of
    ``rows`` (invoices not in the ERP yet) may be a copy of: from the invoices' summary (D-110)."""
    flagged = [r for r in rows if "DUPLICATE_INVOICE" in _flags(r["validation"])]
    if not flagged:
        return set()
    wanted = {_bill(r["final_output"] or r["ai_output"] or {}) for r in flagged}
    by_end = f"export_batch IS NOT NULL AND export_batch NOT IN ({_LATE_BATCHES})"
    found = {
        (r["bill_vendor"], r["bill_number"], bool(r["bill_credit"]))
        for r in store.summary_rows(
            f"bill_number IN (SELECT value FROM json_each(?)) AND +status = ? AND {by_end} AND {whole('spend')}",
            (json.dumps(sorted({b[1] for b in wanted})), APPROVED, end),
            "bill_vendor, bill_number, bill_credit",
        )
    }
    for r in store.summary_rows(f"{broken('spend')} AND +status = ? AND {by_end}", (APPROVED, end), "id"):
        (inv,) = store.invoice_columns(("id", "final_output"), ids=[r["id"]])
        found.add(_bill(inv["final_output"] or {}))  # read again: fails as it did when every invoice was read
    return found & wanted


def late_after(store: Store, period_end: str) -> str:
    """The day after which an export batch leaves its invoices out of the ERP at ``period_end`` (D-133): the period
    end when JD Edwards dates a voucher by the day it is exported; with the invoice date as its G/L date (the
    default), a voucher exported later still lands in its own month, so no batch is late."""
    from . import jde

    return period_end if jde.load(store).gl_date == jde.GL_DATE_EXPORT else "9999-12-31"


def _not_in_erp(store: Store, period_end: dt.date, no_date: list[Accrual] | None = None) -> list[Accrual]:
    end = period_end.isoformat()
    cutoff = late_after(store, end)
    oldest = (period_end - dt.timedelta(days=OLD_DAYS)).isoformat()
    # Only invoices not in the ERP at the period end are read (the summary says which, D-110): in review, parked,
    # waiting for a second approval, approved and not exported, or exported after the period end (D-133).
    found = store.summary_rows(
        f"status IN (?, ?, ?) OR (status = ? AND (export_batch IS NULL OR export_batch IN ({_LATE_BATCHES})))",
        (REVIEW, PARKED, PENDING, APPROVED, cutoff), "id, export_batch")  # fmt: skip
    late = {r["id"] for r in found if r["export_batch"] is not None}
    rows = store.invoice_columns(("id", "status", "ai_output", "final_output", "validation"),
                                 ids=[r["id"] for r in found])  # fmt: skip
    # A copy flagged as a duplicate of a bill already in the ERP (exported) or already accrued is not accrued
    # again; one flagged as already in the ERP's invoice register is in the ERP.
    counted = _exported_bills(store, rows, cutoff)
    treatments = store.tax_treatments()
    out = []
    for r in rows:
        doc = r["final_output"] or r["ai_output"] or {}
        date = str(doc.get("invoice_date") or "").strip() if doc else ""
        if doc and not date:  # no date: when it was incurred is not known (B-474)
            if no_date is not None:
                no_date.append(Accrual(NOT_IN_ERP, doc.get("vendor_name") or "",
                                       f"Invoice {doc.get('invoice_number') or ''} (#{r['id']})", "no invoice date",
                                       "", "", round(float(doc.get("grand_total") or 0), 2), doc.get("currency") or "",
                                       "no invoice date: can't be accrued until it has one"))  # fmt: skip
            continue
        if not doc or date > end:
            continue
        flags, bill = _flags(r["validation"]), _bill(doc)
        if "DUPLICATE_IN_ERP" in flags or ("DUPLICATE_INVOICE" in flags and bill in counted):
            continue
        counted.add(bill)
        proposed = r["status"] in (REVIEW, PARKED)  # no person has approved it: its coding is a proposal
        if proposed:
            doc = _unguessed(doc, r["ai_output"] or {})
        old = date < oldest  # accrued, flagged: AP decides (B-474)
        by_gl: dict[tuple[str, str], float] = defaultdict(float)
        for e in distribution(doc, treatments):  # an invoice coded by an older version: built again
            if e.get("kind") == "expense" or e.get("non_recoverable_tax"):  # a cost; recoverable tax is not
                by_gl[(e.get("gl_code") or "", e.get("cost_center") or "")] += float(e.get("amount") or 0)
        state = {REVIEW: f"in review, {PROPOSED}", PARKED: f"parked, {PROPOSED}",
                 PENDING: "waiting for a second approval", APPROVED: "approved, not exported"}  # fmt: skip
        for (gl, cc), amount in by_gl.items():
            out.append(
                Accrual(
                    NOT_IN_ERP,
                    doc.get("vendor_name") or "",
                    f"Invoice {doc.get('invoice_number') or ''} (#{r['id']})",
                    f"invoice dated {doc.get('invoice_date')}",
                    gl,
                    cc,
                    round(amount, 2),
                    doc.get("currency") or "",
                    (EXPORTED_LATE if r["id"] in late else state[r["status"]]) + (f"; {OLD_NOTE}" if old else ""),
                    old,
                )  # fmt: skip
            )
    return out


def _expected_recurring(store: Store, period_end: dt.date, received: list[Accrual],
                        left_out: list[Accrual] | None = None, settle: float = 0.0) -> list[Accrual]:  # fmt: skip
    # A vendor's received, not invoiced PO lines (``received``) are what its next invoice bills: accrued once, on them.
    on_po = {vendor_key(a.vendor) for a in received}
    out = []
    for r in recurring.detected(store, today=period_end, settle=settle):
        if r.next_date > period_end:
            continue
        if r.vendor_key in on_po:
            if left_out is not None:
                left_out.append(Accrual(RECURRING, r.vendor_name, f"expected {r.next_date.isoformat()}",
                                        f"{r.cadence.lower()} invoice", "", "", round(r.typical_total, 2), r.currency,
                                        ON_PO))  # fmt: skip
            continue
        usage = store.vendor_gl_usage(r.vendor_key)
        out.append(
            Accrual(
                RECURRING,
                r.vendor_name,
                f"expected {r.next_date.isoformat()}",
                f"{r.cadence.lower()} invoice",
                usage[0]["gl_code"] if usage else "",
                "",
                round(r.typical_total, 2),
                r.currency,
                "estimate: the usual amount, including tax",
            )  # fmt: skip
        )
    return out


def build(store: Store, period_end: dt.date, undated: list[Accrual] | None = None,
          left_out: list[Accrual] | None = None, settle: float = 0.0) -> list[Accrual]:  # fmt: skip
    """The accruals at ``period_end``; ``undated``: filled with the invoices not in the ERP yet that have no invoice
    date, not accrued until they have one (B-474), read in the same go; ``left_out``: with the expected recurring
    invoices not accrued, as their vendor's received, not invoiced PO lines are (``ON_PO``). Kept until what they are
    worked out from changes (D-162); ``settle``, for the page, which only shows them: once an invoice changed, worked
    out again at most every that many seconds (``SETTLE_SECONDS``, D-166); a change of the POs, the tax setup, JD
    Edwards' G/L date or the batches made after the period end is another question, worked out at once. The CSV, which
    is booked from, is never settled. Shared: read them, don't change them."""
    # Each PO's lines worked out again only once its billing changed (``_received_not_invoiced``). Both answers are
    # worked out once for every process of the company server (D-169).
    pos = store.get_setting("changes/purchase_orders")
    received = store.shared_answer("received_not_invoiced", lambda: _received_not_invoiced(store, period_end),
                                   version=ACCRUALS_FORMAT, encode=_data, decode=_of, asked=(period_end, pos),
                                   settle=settle)  # fmt: skip

    def not_in_erp() -> tuple[list[Accrual], list[Accrual]]:
        no_date: list[Accrual] = []
        return _not_in_erp(store, period_end, no_date), no_date

    # The invoices not in the ERP yet: 0.9 s at 100,000 invoices, at every visit before.
    cutoff = late_after(store, period_end.isoformat())
    late = [r["id"] for r in store.summary_fetch(_LATE_BATCHES, (cutoff,))]
    found, no_date = store.shared_answer("not_in_erp", not_in_erp, version=ACCRUALS_FORMAT,
                                         encode=lambda v: [_data(v[0]), _data(v[1])],
                                         decode=lambda v: (_of(v[0]), _of(v[1])),
                                         asked=(period_end, cutoff, late, store.tax_treatments()),
                                         settle=settle)  # fmt: skip
    if undated is not None:
        undated.extend(no_date)
    expected = _expected_recurring(store, period_end, received, left_out, settle)
    return received + found + expected


def by_gl_columns(accruals: list[Accrual]) -> tuple[list[str], list[tuple[str, dict[str, float]]]]:
    """(currencies, [(GL account, {currency: total})]): one row per account, a column per currency (CAD first),
    largest first by its biggest amount. Currencies are never added together."""
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for a in accruals:
        totals[a.gl_code or "(no GL)"][a.currency or "CAD"] += a.amount
    currencies = sorted({c for t in totals.values() for c in t}, key=lambda c: (c != "CAD", c))
    rows = [(gl, {c: round(v, 2) for c, v in t.items()}) for gl, t in totals.items()]
    return currencies, sorted(rows, key=lambda r: -max(r[1].values()))


def to_csv(accruals: list[Accrual], period_end: dt.date) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([f"Accruals at {period_end.isoformat()}"])
    w.writerow(["Source", "Vendor", "Reference", "Description", "GL account", "Cost center", "Amount", "Currency",
                "Note"])  # fmt: skip
    for a in accruals:
        w.writerow(csv_row([a.source, a.vendor, a.reference, a.description, a.gl_code, a.cost_center,
                            f"{a.amount:.2f}", a.currency, a.note]))  # fmt: skip
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def default_period_end(today: dt.date | None = None) -> dt.date:
    """The last day of the previous month in the first week of a month (closing it), else of this month."""
    today = today or dt.date.today()
    first = today.replace(day=1)
    if today.day <= 7:
        return first - dt.timedelta(days=1)
    nxt = (first + dt.timedelta(days=32)).replace(day=1)
    return nxt - dt.timedelta(days=1)


def summary(accruals: list[Accrual]) -> dict[str, tuple[int, dict[str, float]]]:
    """{source: (lines, {currency: total})}: currencies are never added together."""
    out: dict[str, tuple[int, dict[str, float]]] = {}
    for s in SOURCES:
        totals: dict[str, float] = defaultdict(float)
        for a in accruals:
            if a.source == s:
                totals[a.currency or "CAD"] += a.amount
        out[s] = (sum(1 for a in accruals if a.source == s), {c: round(t, 2) for c, t in totals.items()})
    return out
