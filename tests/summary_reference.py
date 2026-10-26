"""The reports, lists and search as they were computed before the invoices' summary (D-110, schema 18): every
invoice read and decoded on each call. ``tests/test_invoice_summary.py`` checks the summary's results against these,
on realistic data; the bodies are the old code as it was (only renamed, and pointed at the old queries below)."""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter, defaultdict
from typing import Any

from ap_coder.coding.taxreturn import (
    DEFAULT_TREATMENTS,
    FOREIGN,
    GROUPS,
    MEALS,
    NOT_EXPORTED,
    RECOVERABLE,
    Claim,
    Report,
    SelfAssessment,
    TaxRateTable,
    TaxTreatment,
    _charged,
    _jde_accruing,
    _registration_issue,
    charged_in_error,
    document_threshold,
    meals_by_tax_line,
    province,
    regime_on,
    self_assessed_base,
    supply_province,
    supply_setup,
)
from ap_coder.coding.terms import payment
from ap_coder.core.memory import vendor_key
from ap_coder.core.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, Store
from ap_coder.erp.accruals import NOT_IN_ERP, OLD_DAYS, OLD_NOTE, PROPOSED, Accrual, _bill, _flags, _unguessed
from ap_coder.erp.exports import distribution
from ap_coder.erp.insights import (
    AGE_BUCKETS,
    DUPLICATE_CODES,
    Assumptions,
    _day,
    _issues,
    _week,
    corrected,
    filled_in,
    invoice_cost_usd,
)
from ap_coder.erp.search import LIMIT, Hit, _plain, _same_vendor, _score, _tokens, norm_invoice_number
from ap_coder.erp.spend import SpendLine


def _old_list_invoices(store: Store, status: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, file_name, status, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, "
        "model_confidence, adjusted_confidence, requires_review, error, created_at, reviewed_at, reviewer, "
        "second_reviewer FROM invoices"
    )
    args: tuple[Any, ...] = (status,) if status else ()
    sql += " WHERE status = ?" if status else ""
    with store._conn() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY requires_review DESC, adjusted_confidence ASC, id ASC",
                                              args)]  # fmt: skip


def _old_unexported(store: Store) -> list[dict[str, Any]]:
    with store._conn() as conn:
        sql = (
            "SELECT id, vendor_name, invoice_number, invoice_date, currency, grand_total, reviewer, reviewed_at "
            "FROM invoices WHERE status = ? AND export_batch IS NULL ORDER BY reviewed_at, id"
        )
        return [dict(r) for r in conn.execute(sql, (APPROVED,))]


def _old_search_rows(store: Store) -> list[dict[str, Any]]:
    with store._conn() as conn:
        batches = {r["id"]: r["created_at"] for r in conn.execute("SELECT id, created_at FROM export_batches")}
        rows = [dict(r) for r in conn.execute(
            """SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency,
                      grand_total, po_key, created_at, reviewed_at, reviewer, second_reviewer,
                      second_reviewed_at, export_batch, parked_reason, follow_up, error,
                      json_extract(final_output, '$.gst_hst_registration_number') AS gst_hst_registration_number
               FROM invoices ORDER BY id DESC""")]  # fmt: skip
    for r in rows:
        r["exported_at"] = batches.get(r["export_batch"]) if r["export_batch"] else None
    return rows


def old_search_rows(store: Store) -> list[dict[str, Any]]:
    return _old_search_rows(store)


def old_list_invoices(store: Store, status: str | None = None) -> list[dict[str, Any]]:
    return _old_list_invoices(store, status)


def spend_lines(store: Store, start: dt.date, end: dt.date) -> list[SpendLine]:
    """Expense postings of approved invoices dated from ``start`` to ``end`` (inclusive)."""
    first, last = start.isoformat(), end.isoformat()
    treatments = store.tax_treatments()
    out = []
    for r in store.invoice_columns(("id", "final_output"), APPROVED):
        doc = r["final_output"] or {}
        date = str(doc.get("invoice_date") or "")
        if not (first <= date <= last):
            continue
        for e in distribution(doc, treatments):  # an invoice approved by an older version: built again
            # Expense lines, and non-recoverable tax posted to its own GL account (a cost too).
            if e.get("kind") != "expense" and not e.get("non_recoverable_tax"):
                continue
            out.append(
                SpendLine(
                    r["id"],
                    date[:7],
                    str(doc.get("vendor_name") or ""),
                    str(e.get("gl_code") or ""),
                    str(e.get("cost_center") or ""),
                    float(e.get("amount") or 0),
                    str(doc.get("currency") or "CAD"),
                )  # fmt: skip
            )
    return out


def latest_approvals(store: Store, rows: dict[int, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The latest approval of each invoice still approved (or waiting for its second approval): one sent back and
    approved again counts once. ``rows``: the invoices by id, when already read."""
    rows = rows if rows is not None else {r["id"]: r for r in _old_list_invoices(store)}
    latest: dict[int, dict[str, Any]] = {}
    for e in sorted(store.events(actions=["approved"], limit=100_000), key=lambda e: (e["created_at"], e["id"])):
        latest[e["invoice_id"]] = e
    return [e for i, e in latest.items() if i in rows and rows[i]["status"] in (APPROVED, PENDING)]


def approved_as_coded(store: Store) -> tuple[int, int]:
    """(approved as coded, approved): the invoices of ``latest_approvals`` whose approval changed nothing the coder
    proposed (``corrected``). Insights and Learning show this pair."""
    approvals = latest_approvals(store)
    return sum(1 for e in approvals if not corrected(e)), len(approvals)


def filled_in_count(store: Store) -> int:
    """Of ``approved_as_coded``'s, the approvals that only had blanks filled in by AP (``filled_in``)."""
    return sum(1 for e in latest_approvals(store) if filled_in(e))


def insights_compute(store: Store, a: Assumptions | None = None) -> dict[str, Any]:
    a = a or Assumptions.load(store)
    rows = {r["id"]: r for r in _old_list_invoices(store)}
    light = store.invoice_columns(("id", "meta", "validation"))
    metas = {r["id"]: r["meta"] or {} for r in light}
    processed = [r for r in rows.values() if r["status"] != FAILED]
    failed = [r for r in rows.values() if r["status"] == FAILED]
    approvals = latest_approvals(store, rows)  # as approved_as_coded counts them (Learning shows the same pair)
    clean = [e for e in approvals if not corrected(e)]
    changed = [e for e in approvals if corrected(e)]

    # Only a build that may use the internet can send anything to Azure. Offline, the tokens recorded are LM Studio's
    # (or the demo's) on this computer: no cloud cost, whatever Azure would have charged for them.
    from ap_coder.ops.offline import internet_allowed

    costs = [invoice_cost_usd(metas.get(r["id"]) or {}, a) for r in processed] if internet_allowed() else []
    measured = [c for c, recorded in costs if recorded]
    cost_per_invoice_cad = (sum(measured) / len(measured) * a.usd_to_cad) if measured else None
    seconds = [
        sum(((metas.get(r["id"]) or {}).get("timings_seconds") or {}).values())
        for r in processed
        if (metas.get(r["id"]) or {}).get("timings_seconds")
    ]

    minutes_saved = len(clean) * (a.manual_minutes - a.clean_minutes) + len(changed) * (
        a.manual_minutes - a.changed_minutes
    )
    stp = len(clean) / len(approvals) if approvals else None
    per_invoice_minutes = (
        (stp * (a.manual_minutes - a.clean_minutes) + (1 - stp) * (a.manual_minutes - a.changed_minutes))
        if stp is not None
        else None
    )

    issue_counts: Counter[str] = Counter()
    stopped: dict[str, float] = defaultdict(float)  # duplicates caught and rejected: {currency: total}
    stopped_count = 0
    for r in light:
        codes = set()
        for issue in _issues(r["validation"]):
            if issue.get("severity") in ("error", "warning"):
                issue_counts[issue["code"]] += 1
                codes.add(issue["code"])
        inv = rows.get(r["id"])
        if inv and inv["status"] == REJECTED and codes & DUPLICATE_CODES and (inv["grand_total"] or 0) > 0:
            stopped_count += 1
            stopped[inv["currency"] or "CAD"] += inv["grand_total"]

    weekly: dict[str, dict[str, int]] = defaultdict(lambda: {"processed": 0, "clean": 0, "changed": 0})
    for r in processed:
        weekly[_week(r["created_at"])]["processed"] += 1
    for e in approvals:
        weekly[_week(e["created_at"])]["changed" if corrected(e) else "clean"] += 1

    projection = None
    if per_invoice_minutes is not None:
        hours = a.monthly_volume * per_invoice_minutes / 60
        projection = {
            "hours_saved": hours,
            "value_saved": hours * a.hourly_cost,
            "azure_cost": (cost_per_invoice_cad or 0) * a.monthly_volume,
        }
    return {
        "assumptions": a,
        "processed": len(processed),
        "failed": len(failed),
        "approved": len(approvals),
        "approved_clean": len(clean),
        "approved_filled": sum(1 for e in clean if filled_in(e)),
        "approved_changed": len(changed),
        "bulk_approved": sum(1 for e in approvals if (e["detail"] or {}).get("bulk")),
        "needs_attention_rate": (sum(1 for r in processed if r["requires_review"]) / len(processed))
        if processed
        else None,
        "straight_through": stp,
        "hours_saved": minutes_saved / 60,
        "value_saved": minutes_saved / 60 * a.hourly_cost,
        "cost_per_invoice_cad": cost_per_invoice_cad,
        "cost_measured_invoices": len(measured),
        "avg_seconds": (sum(seconds) / len(seconds)) if seconds else None,
        "top_issues": issue_counts.most_common(8),
        "weekly": [{"week": w, **v} for w, v in sorted(weekly.items())],
        "projection": projection,
        "approved_status": APPROVED,
        "duplicates_stopped": stopped_count,
        "duplicates_stopped_total": {c: round(t, 2) for c, t in sorted(stopped.items())},
    }


def insights_operations(store: Store, today: dt.date | None = None) -> dict[str, Any]:
    """AP operations: how old the queue is, how long approval takes, approvals after the due date, and
    early-payment discounts approved in time to take them (or not)."""
    today = today or dt.date.today()
    rows = _old_list_invoices(store)
    waiting = [r for r in rows if r["status"] in (REVIEW, PARKED)]
    ageing = {label: 0 for label, _, _ in AGE_BUCKETS}
    for r in waiting:
        received = _day(r["created_at"])
        age = (today - received).days if received else 0
        for label, low, high in AGE_BUCKETS:
            if low <= age <= high:
                ageing[label] += 1
    approved = [r for r in rows if r["status"] in (APPROVED, PENDING) and r["reviewed_at"]]
    detail = {
        r["id"]: r
        for status in (APPROVED, PENDING)
        for r in store.invoice_columns(("id", "second_reviewed_at", "final_output"), status)
    }

    def done(r: dict[str, Any]) -> str:  # fully approved: after the second approval when one was needed
        return ((detail.get(r["id"]) or {}).get("second_reviewed_at") or r["reviewed_at"] or "")[:10]

    cycle = [
        (_day(r["reviewed_at"]) - _day(r["created_at"])).days
        for r in approved
        if _day(r["reviewed_at"]) and _day(r["created_at"])
    ]
    late = sum(1 for r in approved if r["status"] == APPROVED and r["due_date"] and done(r) > r["due_date"])
    finals = {i: (r["final_output"] or {}) for i, r in detail.items()}
    taken = missed = 0
    taken_amount: Counter[str] = Counter()  # per currency: a yen discount is not dollars
    missed_amount: Counter[str] = Counter()
    default_days, vendor_terms = store.default_terms_days(), store.all_vendor_terms()
    for r in approved:
        final = finals.get(r["id"]) or {}
        p = payment(final, default_days, vendor_terms.get(vendor_key(final.get("vendor_name") or ""), ""))
        if p.discount_by is None:
            continue
        if r["status"] == PENDING and today <= p.discount_by:
            continue  # still waiting for the second approval, and the discount is still open
        currency = str(final.get("currency") or "CAD").strip().upper()
        if r["status"] == APPROVED and done(r) <= p.discount_by.isoformat():
            taken, taken_amount[currency] = taken + 1, taken_amount[currency] + p.discount_amount
        else:
            missed, missed_amount[currency] = missed + 1, missed_amount[currency] + p.discount_amount
    return {
        "waiting": len(waiting),
        "ageing": ageing,
        "oldest_days": max(((today - _day(r["created_at"])).days for r in waiting if _day(r["created_at"])), default=0),
        "median_days_to_approve": statistics.median(cycle) if cycle else None,
        "approved": len(approved),
        "approved_after_due": late,
        # (count, {currency: amount}), like duplicates_stopped_total
        "discounts_in_time": (taken, {c: round(t, 2) for c, t in sorted(taken_amount.items())}),
        "discounts_missed": (missed, {c: round(t, 2) for c, t in sorted(missed_amount.items())}),
    }


def insights_vendor_workload(store: Store, min_invoices: int = 2) -> list[dict[str, Any]]:
    """Vendors whose invoices make work: how often an invoice arrived with a problem only the vendor can fix
    (amounts that don't add up, no GST/HST number, PO issues...: the checks *Ask the vendor* covers), and how
    often AP had to correct the coding. Worst first; vendors without problems are left out. A rejected invoice is left
    out, and an approved one counts what was still open when it was approved: a finding the reviewer cleared by
    correcting what AP Coder read was AP Coder's misread, not the vendor's doing (B-1183)."""
    from ap_coder.coding.vendor_mail import ASKABLE
    from ap_coder.core.memory import vendor_key

    stats: dict[str, dict[str, Any]] = {}
    rows = [r for r in store.invoice_columns(("id", "status", "validation", "edits", "ai_output", "final_output"))
            if r["status"] not in (FAILED, REJECTED)]  # fmt: skip
    approvals = store.latest_events("approved", [r["id"] for r in rows if r["status"] in (APPROVED, PENDING)])
    for r in rows:
        doc = r["final_output"] or r["ai_output"] or {}
        name = str(doc.get("vendor_name") or "")
        key = vendor_key(name)
        if not key:
            continue
        s = stats.setdefault(key, {"vendor_name": name, "invoices": 0, "with_problems": 0, "corrected": 0,
                                   "approved": 0, "codes": Counter()})  # fmt: skip
        s["invoices"] += 1
        codes = {i.get("code") for i in _issues(r["validation"])} & ASKABLE
        if r["id"] in approvals:
            codes &= {i.get("code") for i in (approvals[r["id"]]["detail"] or {}).get("open_issues") or []}
        if codes:
            s["with_problems"] += 1
            s["codes"].update(codes)
        if r["status"] in (APPROVED, PENDING):
            s["approved"] += 1
            s["corrected"] += bool({"line_coding", "line_count"} & set(r["edits"] or []))  # the GL coding, not a date
    out = []
    for key, s in stats.items():
        if s["invoices"] < min_invoices or not (s["with_problems"] or s["corrected"]):
            continue
        s["vendor_key"] = key
        s["problem_rate"] = s["with_problems"] / s["invoices"]
        s["top_codes"] = [c for c, _ in s.pop("codes").most_common(3)]
        out.append(s)
    return sorted(out, key=lambda s: (-(s["with_problems"] + s["corrected"]), -s["problem_rate"], s["vendor_name"]))


def tax_build(store: Store, start: dt.date, end: dt.date, treatments: dict[str, TaxTreatment] | None = None) -> Report:
    """The claimable tax on approved invoices dated from ``start`` to ``end`` (inclusive)."""
    treatments = treatments if treatments is not None else store.tax_treatments()

    def recoverable(tax_type: str) -> bool:
        t = treatments.get(tax_type)
        return (t.treatment if t else DEFAULT_TREATMENTS[tax_type]) == RECOVERABLE

    setup = supply_setup(store)
    first, last = start.isoformat(), end.isoformat()
    claims, not_approved = [], 0
    rows = store.invoice_columns(("id", "status", "invoice_date", "export_batch", "ai_output", "final_output",
                                  "validation"))  # fmt: skip
    approvals = store.latest_events("approved", [r["id"] for r in rows if r["status"] == APPROVED])
    for r in rows:
        doc = r["final_output"] or r["ai_output"] or {}
        date = str(doc.get("invoice_date") or r["invoice_date"] or "")
        if not (first <= date <= last):
            continue
        if r["status"] in (REVIEW, PARKED, PENDING):
            not_approved += 1
            continue
        if r["status"] != APPROVED or not r["final_output"]:
            continue
        doc = r["final_output"]
        currency = str(doc.get("currency") or "CAD").upper()
        total = float(doc.get("grand_total") or 0)
        meals = meals_by_tax_line(doc)
        in_error = charged_in_error(r, approvals.get(r["id"]))
        for position, tl in enumerate(doc.get("tax_lines") or []):
            tax_type = str(tl.get("tax_type") or "").upper()
            amount = round(float(tl.get("tax_amount") or 0), 2)
            if tax_type not in GROUPS or abs(amount) < 0.005 or not recoverable(tax_type):
                continue
            not_claimed = meals.get(position, 0.0)
            amount = round(amount - not_claimed, 2)
            number, issue = _registration_issue(tax_type, doc)
            issues = [issue] if issue and abs(total) >= document_threshold(date) else []
            issues += in_error
            if not_claimed:
                issues.append(MEALS)
            if currency != "CAD":
                issues.append(FOREIGN)
            if r["export_batch"] is None:
                issues.append(NOT_EXPORTED)
            claims.append(
                Claim(
                    r["id"],
                    str(doc.get("vendor_name") or ""),
                    str(doc.get("invoice_number") or ""),
                    date,
                    currency,
                    total,
                    tax_type,
                    province(tax_type, tl, doc, setup),
                    float(tl.get("rate") or 0),
                    amount,
                    number,
                    issues,
                    not_claimed,
                )  # fmt: skip
            )
    claims.sort(key=lambda c: (c.invoice_date, c.invoice_id))
    not_recoverable = [t for t in GROUPS if not recoverable(t)]
    return Report(start, end, claims, not_approved, not_recoverable)


def tax_self_assessment(
    store: Store, start: dt.date, end: dt.date, rates: TaxRateTable | None = None
) -> list[SelfAssessment]:
    """Approved invoices for a supply in a PST or QST province with lines the PST / QST is due on and not charged (an
    out-of-province or unregistered vendor, or a line the vendor missed): the buyer self-assesses it (BC PSTA s.49 and
    the SK, MB and QST acts alike), at the official rate on those lines (``self_assessed_base``, line by line, D-64)."""
    rates = rates or TaxRateTable.load()
    setup = supply_setup(store, rates)
    first, last = start.isoformat(), end.isoformat()
    out = []
    accrued = _jde_accruing(store)
    for r in store.invoice_columns(("id", "final_output", "export_batch"), APPROVED):
        doc = r["final_output"] or {}
        date = str(doc.get("invoice_date") or "")
        if not (first <= date <= last):
            continue
        province = supply_province(doc, setup)
        # A printed "PST 0.00" is not a charge (as in the tax checks): that PST may still be owed.
        charged = {str(tl.get("tax_type") or "").upper() for tl in doc.get("tax_lines") or [] if _charged(tl)}
        items = doc.get("line_items") or []
        try:
            on = dt.date.fromisoformat(date)
        except ValueError:
            on = None
        for tax_type in sorted(regime_on(province, on, rates) & {"PST", "QST"}):
            base = self_assessed_base(doc, items, tax_type, charged)
            if abs(base) < 0.005:
                continue
            rate = rates.rate_for(tax_type, province, on) if on else None
            if not rate:
                continue
            out.append(
                SelfAssessment(
                    r["id"],
                    str(doc.get("vendor_name") or ""),
                    str(doc.get("invoice_number") or ""),
                    date,
                    province,
                    tax_type,
                    round(base, 2),
                    rate,
                    round(base * rate, 2),
                    str(doc.get("currency") or "CAD").upper(),
                    accrued(r["export_batch"], lambda doc=doc: doc) if tax_type == "PST" else "",
                )  # fmt: skip
            )
    return sorted(out, key=lambda s: (s.province, s.invoice_date, s.invoice_id))


def _not_in_erp(store: Store, period_end: dt.date, no_date: list[Accrual] | None = None) -> list[Accrual]:
    end = period_end.isoformat()
    oldest = (period_end - dt.timedelta(days=OLD_DAYS)).isoformat()
    rows = store.invoice_columns(("id", "status", "ai_output", "final_output", "validation"))
    exported = {r["id"] for r in _old_list_invoices(store, APPROVED)} - {r["id"] for r in _old_unexported(store)}
    # A copy flagged as a duplicate of a bill already in the ERP (exported) or already accrued is not accrued
    # again; one flagged as already in the ERP's invoice register is in the ERP.
    counted = {_bill(r["final_output"] or {}) for r in rows if r["id"] in exported}
    treatments = store.tax_treatments()
    out = []
    for r in rows:
        if r["status"] not in (REVIEW, PARKED, PENDING, APPROVED) or r["id"] in exported:
            continue
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
                    state[r["status"]] + (f"; {OLD_NOTE}" if old else ""),
                    old,
                )  # fmt: skip
            )
    return out


def search_find(store: Store, query: str, limit: int = LIMIT) -> list[Hit]:
    tokens = [t.lower() for t in _tokens(query)]
    if not tokens:
        return []
    numbers = [w for w in tokens if any(c.isdigit() for c in w)]
    words = [_plain(w.strip(",.")) for w in tokens if w not in numbers and len(w.strip(",.")) > 1]
    rows = _old_search_rows(store)
    hits = [Hit(s, r) for r in rows if (s := _score(r, words, numbers))]
    # An exported invoice's own row in the ERP register is the same bill: shown once, as the AP Coder invoice. Its
    # own row: the same number and amount (with its sign: a credit is another document), and the same vendor.
    ours: dict[tuple[str, float], list[str]] = {}
    for r in rows:
        if r["export_batch"]:
            key = (norm_invoice_number(r["invoice_number"] or ""), round(r["grand_total"] or 0, 2))
            ours.setdefault(key, []).append(vendor_key(r["vendor_name"] or ""))
    for r in store.erp_register():
        vendors = ours.get((r["number_key"], round(r["total"] or 0, 2)), [])
        if any(_same_vendor(r["vendor_key"], v) for v in vendors):
            continue
        row = {"vendor_name": r["vendor_name"], "invoice_number": r["invoice_number"], "grand_total": r["total"],
               "invoice_date": r["invoice_date"], "po_key": ""}  # fmt: skip
        if s := _score(row, words, numbers):
            hits.append(Hit(s, row, "ERP"))
    hits.sort(key=lambda h: (-h.score, h.source != "AP Coder", -(h.row.get("id") or 0)))
    return hits[:limit]


# The invoice directory's rows, as ``erp/directory.py`` worked them out of every invoice's coding.
_DOC = "CASE WHEN json_valid(COALESCE(final_output, ai_output)) THEN COALESCE(final_output, ai_output) END"
_GL = (
    "(SELECT group_concat(g, ', ') FROM (SELECT DISTINCT json_extract(l.value, '$.predicted_gl_code') AS g "
    "FROM json_each(doc, '$.line_items') AS l WHERE g IS NOT NULL AND g NOT IN ('', 'UNASSIGNED') ORDER BY g))"
)
OLD_FIRST_GL = (
    "(SELECT MIN(json_extract(l.value, '$.predicted_gl_code')) FROM json_each(doc, '$.line_items') AS l "
    "WHERE json_extract(l.value, '$.predicted_gl_code') NOT IN ('', 'UNASSIGNED'))"
)
OLD_ROWS_SQL = f"""
WITH coded AS (SELECT *, {_DOC} AS doc FROM invoices),
rows AS (
  SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total,
         created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_batch, parked_reason,
         follow_up, error,
         NULLIF(TRIM(CAST(json_extract(doc, '$.po_number') AS TEXT)), '') AS po_number,
         CAST(json_extract(doc, '$.subtotal') AS REAL) AS subtotal,
         CAST(json_extract(doc, '$.tax_total') AS REAL) AS tax_total,
         {_GL} AS gl_codes, doc
  FROM coded)
"""
