"""Insights: the business case (volume, straight-through rate, time saved, Azure cost), operations, workload.

Everything is computed from data AP Coder already keeps (invoices, their processing metadata with page
counts and token usage, and the audit trail of approvals). Time and price figures are *assumptions* the
AP team can edit on the Insights page; they are stored in the database (setting ``insights_assumptions``).
"""

from __future__ import annotations

import datetime as dt
import html
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields
from typing import Any

from ..coding.terms import parse_terms, payment
from ..core.memory import vendor_key
from ..core.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, Store, _in_list
from ..core.store.summary import _code_rows, computed, summary_facts


@dataclass
class Assumptions:
    manual_minutes: float = 6.0  # keying and coding one invoice by hand today
    clean_minutes: float = 1.0  # reviewing an invoice the AI got right
    changed_minutes: float = 3.0  # reviewing and correcting one it got partly wrong
    hourly_cost: float = 45.0  # loaded cost of an AP clerk hour (CAD)
    di_usd_per_1000_pages: float = 10.0  # Document Intelligence prebuilt-layout / -invoice
    aoai_usd_per_m_input: float = 2.50  # gpt-4o input tokens
    aoai_usd_per_m_cached: float = 1.25  # cached input tokens
    aoai_usd_per_m_output: float = 10.0  # output tokens
    usd_to_cad: float = 1.37
    monthly_volume: int = 500  # invoices per month, for the projection

    @classmethod
    def load(cls, store: Store) -> Assumptions:
        try:
            saved = json.loads(store.get_setting("insights_assumptions") or "{}")
        except ValueError:
            saved = {}
        known = {f.name: f.type for f in fields(cls)}
        values = {}
        for key, value in saved.items():
            if key in known:
                try:
                    values[key] = int(value) if key == "monthly_volume" else float(value)
                except (TypeError, ValueError):
                    continue
        return cls(**values)

    def save(self, store: Store, actor: str | None = None) -> None:
        store.set_setting("insights_assumptions", json.dumps(asdict(self)), actor=actor)


AS_CODED = "Approved as coded"  # the one label of the figure, on Insights, Learning and Activity (D-39)


# Changes that say where the invoice is supplied, not what the coder proposed: the place of supply is the clerk's to
# know (rarely printed, often worked out), so setting it is no correction of the coding (B-1010).
NOT_CODING = frozenset({"place of supply"})


def corrected(event: dict[str, Any]) -> bool:
    """Whether an approval (its ``approved`` event) changed what the coder proposed: a value it gave changed, a line
    added or removed. Filling in what it left blank (a cost center it could not pick, an UNASSIGNED account, an empty
    field) completes the coding: it is in the audit trail, but it is not a change. The one definition of "approved as
    coded" (D-39)."""
    changes = (event.get("detail") or {}).get("changes") or []
    return any(
        str(c.get("what") or "").endswith(" added")
        or str(c.get("before") or "").strip().upper() not in ("", "UNASSIGNED", "NONE", "(BLANK)")
        for c in changes
        if isinstance(c, dict) and c.get("what") not in NOT_CODING
    )


_corrected = corrected  # the name Insights' own tests use


def filled_in(event: dict[str, Any]) -> bool:
    """Whether an approval approved as coded (not ``corrected``) still had blanks filled in by AP: a cost center, an
    UNASSIGNED account. Still "approved as coded" (D-39); the pages say how many of those only had blanks filled in, so
    the figure doesn't read as if AP touched nothing (B-1215)."""
    changes = (event.get("detail") or {}).get("changes") or []
    return not corrected(event) and any(isinstance(c, dict) and c.get("what") not in NOT_CODING for c in changes)


def as_coded_note(clean: int, filled: int, total: int) -> str:
    """The line under the "Approved as coded" figure: how many needed nothing, how many only had blanks filled in."""
    if not filled:
        return f"{clean} of {total} needed no change"
    return f"{clean - filled} of {total} needed no change, {filled} only had blanks filled in by AP"


def latest_approvals(store: Store) -> list[dict[str, Any]]:
    """The latest approval of each invoice still approved (or waiting for its second approval): one sent back and
    approved again counts once. From the invoices' summary (``approval_facts``, D-110): {invoice_id, created_at,
    corrected, filled, bulk}, by invoice."""
    out = []
    for r in store.summary_rows("+status IN (?, ?)", (APPROVED, PENDING), "id, broken, a_at, a_corrected, a_filled, "
                                "a_bulk"):  # fmt: skip
        a = computed(store, r)
        if a["a_at"] is not None:
            out.append({"invoice_id": r["id"], "created_at": a["a_at"], "corrected": bool(a["a_corrected"]),
                        "filled": bool(a["a_filled"]), "bulk": bool(a["a_bulk"])})  # fmt: skip
    return out


def approval_facts(created_at: str, detail: dict[str, Any]) -> dict[str, Any]:
    """What Insights and Learning count of an invoice's latest approval (its ``approved`` event's time and detail):
    kept in the invoices' summary (D-110)."""
    event = {"detail": detail}
    return {"a_at": created_at, "a_corrected": int(corrected(event)), "a_filled": int(filled_in(event)),
            "a_bulk": int(bool((detail or {}).get("bulk")))}  # fmt: skip


def approved_as_coded(store: Store) -> tuple[int, int]:
    """(approved as coded, approved): the invoices of ``latest_approvals`` whose approval changed nothing the coder
    proposed (``corrected``). Insights and Learning show this pair."""
    approvals = latest_approvals(store)
    return sum(1 for e in approvals if not e["corrected"]), len(approvals)


def filled_in_count(store: Store) -> int:
    """Of ``approved_as_coded``'s, the approvals that only had blanks filled in by AP (``filled_in``)."""
    return sum(1 for e in latest_approvals(store) if e["filled"])


def _issues(validation: Any) -> list[dict[str, Any]]:
    """The stored checks of an invoice, leaving out anything that isn't one (a damaged or hand-edited database)."""
    issues = (validation or {}).get("issues") if isinstance(validation, dict) else None
    return [i for i in issues or [] if isinstance(i, dict) and i.get("code")]


def invoice_cost_usd(meta: dict[str, Any], a: Assumptions) -> tuple[float, bool]:
    """(Azure cost in USD, whether usage was recorded) for one processed invoice."""
    pages = ((meta.get("extraction") or {}).get("page_count")) or 1
    usage = (meta.get("inference") or {}).get("usage") or {}
    prompt = usage.get("prompt_tokens", 0)
    cached = min(usage.get("cached_prompt_tokens", 0), prompt)
    completion = usage.get("completion_tokens", 0)
    cost = pages * a.di_usd_per_1000_pages / 1000
    cost += (prompt - cached) * a.aoai_usd_per_m_input / 1e6 + cached * a.aoai_usd_per_m_cached / 1e6
    cost += completion * a.aoai_usd_per_m_output / 1e6
    return cost, bool(usage)


def _cost(facts: dict[str, Any], a: Assumptions) -> tuple[float, bool]:
    """``invoice_cost_usd`` of an invoice's ``invoice_facts``."""
    meta = {"extraction": {"page_count": facts["pages"]}, "inference": {"usage": facts["usage"]}}
    return invoice_cost_usd(meta, a)[0], facts["used"]


DUPLICATE_CODES = {"DUPLICATE_INVOICE", "DUPLICATE_IN_ERP", "DUPLICATE_OTHER_VENDOR", "POSSIBLE_DUPLICATE_AMOUNT"}


def invoice_facts(row: dict[str, Any]) -> dict[str, Any]:
    """What ``compute`` reads of an invoice (``row``: its columns, the JSON ones decoded), whatever the assumptions:
    kept in the invoices' summary (D-110). Its pages and token usage (``invoice_cost_usd``), its processing seconds,
    the codes of its errors and warnings (one per finding)."""
    meta = row["meta"] or {}
    usage = (meta.get("inference") or {}).get("usage") or {}
    timings = meta.get("timings_seconds")
    cost = {
        "pages": ((meta.get("extraction") or {}).get("page_count")) or 1,
        "usage": {k: usage[k] for k in ("prompt_tokens", "cached_prompt_tokens", "completion_tokens") if k in usage},
        "used": bool(usage),
    }
    return {
        "i_seconds": sum((timings or {}).values()) if timings else None,
        "i_codes": json.dumps(
            [i["code"] for i in _issues(row["validation"]) if i.get("severity") in ("error", "warning")]
        ),
        "i_cost": json.dumps(cost),
    }


def workload_facts(row: dict[str, Any], approval: dict[str, Any] | None) -> dict[str, Any]:
    """What ``vendor_workload`` reads of an invoice not failed nor rejected (``approval``: the detail of its latest
    ``approved`` event, when it is approved or waiting for its second approval): kept in the invoices' summary."""
    from ..coding.vendor_mail import ASKABLE

    doc = row["final_output"] or row["ai_output"] or {}
    name = str(doc.get("vendor_name") or "")
    codes = {i.get("code") for i in _issues(row["validation"])} & ASKABLE
    if approval is not None:
        codes &= {i.get("code") for i in approval.get("open_issues") or []}
    return {"w_name": name, "w_key": vendor_key(name), "w_codes": json.dumps(sorted(codes)),
            "w_corrected": int(bool({"line_coding", "line_count"} & set(row["edits"] or [])))}  # fmt: skip


def compute(store: Store, a: Assumptions | None = None) -> dict[str, Any]:
    """The business case, from the invoices' summary (``invoice_facts``, ``approval_facts``, D-110): counted by
    SQLite, by day, then by week here; an invoice the summary could not read is read again (``computed``)."""
    a = a or Assumptions.load(store)
    from ..ops.offline import internet_allowed

    whole = "broken = 0"
    tally = store.summary_query(
        f"SELECT status = '{FAILED}', COUNT(*), SUM(CASE WHEN requires_review THEN 1 ELSE 0 END) FROM invoice_summary "
        f"WHERE {whole} GROUP BY 1"
    )
    counts = {bool(r[0]): (r[1], r[2]) for r in tally}
    processed, flagged = counts.get(False, (0, 0))
    failed = counts.get(True, (0, 0))[0]
    days = store.summary_query(
        f"SELECT substr(created_at, 1, 10), COUNT(*) FROM invoice_summary WHERE +status != '{FAILED}' AND {whole} "
        "GROUP BY 1"
    )
    # (day, corrected, filled, bulk, how many) of the latest approvals (``latest_approvals``)
    signed = store.summary_query(
        "SELECT substr(a_at, 1, 10), a_corrected, a_filled, a_bulk, COUNT(*) FROM invoice_summary WHERE +status IN "
        f"('{APPROVED}', '{PENDING}') AND a_at IS NOT NULL AND {whole} GROUP BY 1, 2, 3, 4"
    )
    approvals = [(r[0], bool(r[1]), bool(r[2]), bool(r[3]), r[4]) for r in signed]
    seconds = [r[0] for r in store.summary_query(
        f"SELECT i_seconds FROM invoice_summary WHERE +status != '{FAILED}' AND i_seconds IS NOT NULL AND {whole} "
        "ORDER BY id")]  # fmt: skip
    costs = []
    if internet_allowed():  # only a build that may use the internet can send anything to Azure
        costs = [_cost(json.loads(r[0]), a) for r in store.summary_query(
            f"SELECT i_cost FROM invoice_summary JOIN summary_facts USING (id) WHERE +status != '{FAILED}' AND "
            f"{whole} ORDER BY id")]  # fmt: skip
    # {code: [times, where it first appears]}: most_common breaks a tie by the order the codes came in
    codes = {r[0]: [r[1], (r[2], r[3])] for r in store.summary_query(
        "SELECT c.code, SUM(c.n), MIN(c.id), MIN(c.pos) FROM summary_codes c JOIN (SELECT code, MIN(id) first FROM "
        "summary_codes GROUP BY code) f ON f.code = c.code AND c.id = f.first GROUP BY c.code")}  # fmt: skip
    for r in store.summary_query("SELECT code, SUM(n) FROM summary_codes GROUP BY code"):
        codes[r[0]][0] = r[1]
    duplicates = ", ".join(f"'{c}'" for c in sorted(DUPLICATE_CODES))
    stopped_rows = list(store.summary_query(
        f"SELECT id, grand_total, currency FROM invoice_summary s WHERE status = '{REJECTED}' AND {whole} AND EXISTS "
        f"(SELECT 1 FROM summary_codes c WHERE c.id = s.id AND c.code IN ({duplicates})) ORDER BY id"))  # fmt: skip
    # What the summary could not read, read again from the invoice (failing as it did when every invoice was read).
    for r in store.summary_rows("broken = 1", (), "id, status, grand_total, currency, created_at, requires_review, "
                                "broken"):  # fmt: skip
        f = computed(store, r)
        if r["status"] == FAILED:
            failed += 1
        else:
            processed, flagged = processed + 1, flagged + bool(r["requires_review"])
            days.append(((r["created_at"] or "")[:10] if r["created_at"] is not None else None, 1))
            seconds += [f["i_seconds"]] if f["i_seconds"] is not None else []
            costs += [_cost(json.loads(f["i_cost"]), a)] if internet_allowed() else []
        if r["status"] in (APPROVED, PENDING) and f["a_at"] is not None:
            approvals.append((f["a_at"][:10], bool(f["a_corrected"]), bool(f["a_filled"]), bool(f["a_bulk"]), 1))
        for invoice_id, code, n, position in _code_rows(r["id"], f["i_codes"]):
            seen = codes.setdefault(code, [0, (invoice_id, position)])
            seen[0] += n
            seen[1] = min(seen[1], (invoice_id, position))
            if code in DUPLICATE_CODES and r["status"] == REJECTED and (r["id"], r["grand_total"], r["currency"]) \
                    not in stopped_rows:  # fmt: skip
                stopped_rows.append((r["id"], r["grand_total"], r["currency"]))
    approved = sum(n for *_, n in approvals)
    clean = sum(n for _, c, *_, n in approvals if not c)
    changed = approved - clean

    measured = [c for c, recorded in costs if recorded]
    cost_per_invoice_cad = (sum(measured) / len(measured) * a.usd_to_cad) if measured else None

    minutes_saved = clean * (a.manual_minutes - a.clean_minutes) + changed * (a.manual_minutes - a.changed_minutes)
    stp = clean / approved if approved else None
    per_invoice_minutes = (
        (stp * (a.manual_minutes - a.clean_minutes) + (1 - stp) * (a.manual_minutes - a.changed_minutes))
        if stp is not None
        else None
    )

    issue_counts: Counter[str] = Counter({code: n for code, (n, _) in sorted(codes.items(), key=lambda c: c[1][1])})
    stopped: dict[str, float] = defaultdict(float)  # duplicates caught and rejected: {currency: total}
    stopped_count = 0
    for _, total, currency in sorted(stopped_rows):
        if (total or 0) > 0:
            stopped_count += 1
            stopped[currency or "CAD"] += total

    weekly: dict[str, dict[str, int]] = defaultdict(lambda: {"processed": 0, "clean": 0, "changed": 0})
    for day, n in days:
        weekly[_week(day)]["processed"] += n
    for day, corrected_, _, _, n in approvals:
        weekly[_week(day)]["changed" if corrected_ else "clean"] += n

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
        "processed": processed,
        "failed": failed,
        "approved": approved,
        "approved_clean": clean,
        "approved_filled": sum(n for _, c, f, _, n in approvals if f and not c),
        "approved_changed": changed,
        "bulk_approved": sum(n for *_, b, n in approvals if b),
        "needs_attention_rate": (flagged / processed) if processed else None,
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


AGE_BUCKETS = (("0–2 days", 0, 2), ("3–7 days", 3, 7), ("8–14 days", 8, 14), ("15+ days", 15, 10**6))


def _day(iso: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat((iso or "")[:10])
    except ValueError:
        return None


def operations(store: Store, today: dt.date | None = None) -> dict[str, Any]:
    """AP operations: how old the queue is, how long approval takes, approvals after the due date, and
    early-payment discounts approved in time to take them (or not)."""
    today = today or dt.date.today()
    rows = store.summary_rows(
        "+status IN (?, ?, ?, ?)",  # (most invoices: read in order, not by the index)
        (REVIEW, PARKED, APPROVED, PENDING),
        "id, status, created_at, reviewed_at, second_reviewed_at, due_date, broken, pay_terms, pay_vendor",
    )
    waiting = [r for r in rows if r["status"] in (REVIEW, PARKED)]
    ageing = {label: 0 for label, _, _ in AGE_BUCKETS}
    for r in waiting:
        received = _day(r["created_at"])
        age = (today - received).days if received else 0
        for label, low, high in AGE_BUCKETS:
            if low <= age <= high:
                ageing[label] += 1
    approved = [r for r in rows if r["status"] in (APPROVED, PENDING) and r["reviewed_at"]]

    def done(r: Any) -> str:  # fully approved: after the second approval when one was needed
        return (r["second_reviewed_at"] or r["reviewed_at"] or "")[:10]

    cycle = [
        (_day(r["reviewed_at"]) - _day(r["created_at"])).days
        for r in approved
        if _day(r["reviewed_at"]) and _day(r["created_at"])
    ]
    late = sum(1 for r in approved if r["status"] == APPROVED and r["due_date"] and done(r) > r["due_date"])
    taken = missed = 0
    taken_amount: Counter[str] = Counter()  # per currency: a yen discount is not dollars
    missed_amount: Counter[str] = Counter()
    default_days, vendor_terms = store.default_terms_days(), store.all_vendor_terms()
    # The coding's payment fields of those whose terms (or their vendor's) may give a discount: ``payment`` gives the
    # others none.
    found = {r["id"]: computed(store, r) for r in approved}
    maybe = [
        i
        for i, f in found.items()
        if f["pay_terms"] is None or _may_discount(f["pay_terms"], vendor_terms.get(f["pay_vendor"], ""))
    ]
    pays = {r["id"]: (summary_facts(store, r["id"]) if r["broken"] else r)["pay"]
            for r in store.summary_rows(_in_list("id"), (json.dumps(maybe),), "id, broken, pay")}  # fmt: skip
    for r in approved:
        if r["id"] not in pays:
            continue  # no discount in its terms nor in its vendor's
        final = json.loads(pays[r["id"]]) if pays[r["id"]] is not None else {}
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


PAYMENT_FIELDS = ("payment_terms", "invoice_date", "due_date", "grand_total", "subtotal", "vendor_name", "currency")


def payment_facts(final: dict[str, Any]) -> dict[str, Any]:
    """What ``operations`` reads of an approved invoice's coding (``terms.payment``, its vendor and currency), its
    printed terms and its vendor: kept in the invoices' summary (D-110)."""
    return {"pay": json.dumps({k: final[k] for k in PAYMENT_FIELDS if k in final}),
            "pay_terms": (final.get("payment_terms") or "").strip(),
            "pay_vendor": vendor_key(final.get("vendor_name") or "")}  # fmt: skip


def _may_discount(printed: str, vendor_terms: str) -> bool:
    """Whether ``terms.payment`` can find an early-payment discount: in the printed terms, or in the vendor master's
    when they take their place (none printed, or none understood)."""
    terms = parse_terms(printed)
    if terms.discount_pct:
        return True
    if vendor_terms and terms.net_days is None and not terms.on_receipt:
        vendor = parse_terms(vendor_terms)
        return bool((vendor.net_days is not None or vendor.on_receipt) and vendor.discount_pct)
    return False


def _week(iso: str | None) -> str:
    try:
        d = dt.date.fromisoformat((iso or "")[:10])
    except ValueError:
        return "?"
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0%}"


def _money(v: float | None, decimals: int = 0) -> str:
    return "—" if v is None else f"${v:,.{decimals}f}"


REPORT_CSS = """
body {font-family: Segoe UI, Inter, Arial, sans-serif; color: #142033; max-width: 860px; margin: 2rem auto;
      padding: 0 1rem;}
h1 {font-size: 1.6rem; margin: .2rem 0;} h2 {font-size: 1.1rem; margin-top: 1.6rem;} .muted {color: #5b6474;}
.grid {display: grid; grid-template-columns: repeat(4, 1fr); gap: .8rem; margin: 1.2rem 0;}
.k {border: 1px solid #e4e8ef; border-radius: 12px; padding: .8rem;}
.k b {display: block; font-size: 1.5rem; margin-top: .2rem;}
table {border-collapse: collapse; width: 100%; margin: .5rem 0 1.2rem;}
td, th {border-bottom: 1px solid #e4e8ef; padding: .4rem .3rem; text-align: left;} .n {text-align: right;}
@media (max-width: 640px) {.grid {grid-template-columns: repeat(2, 1fr);}}
"""


def report_html(s: dict[str, Any], organisation: str = "") -> str:
    """A one-page business case to share with management (no invoice data: only totals)."""
    a: Assumptions = s["assumptions"]
    p = s["projection"] or {}
    e = html.escape

    def row(label: str, value: str, tag: str = "td") -> str:
        return f"<tr><{tag}>{e(label)}</{tag}><{tag} class='n'>{e(value)}</{tag}></tr>"

    def tile(label: str, value: str) -> str:
        return f"<div class='k'>{e(label)}<b>{e(value)}</b></div>"

    net = (p.get("value_saved") or 0) - (p.get("azure_cost") or 0)
    avg = "—" if s["avg_seconds"] is None else f"{s['avg_seconds']:.0f} s"
    issues = "".join(row(code, str(n)) for code, n in s["top_issues"]) or row("No issues recorded", "")
    title = "AP Coder pilot: business case" + (f" · {organisation}" if organisation else "")
    assumptions = (
        f"Manual processing {a.manual_minutes:g} min per invoice; review when the AI is right "
        f"{a.clean_minutes:g} min, when it needs corrections {a.changed_minutes:g} min; loaded hourly cost "
        f"{_money(a.hourly_cost)} CAD. Azure list prices (USD): Document Intelligence "
        f"{_money(a.di_usd_per_1000_pages, 2)} per 1,000 pages; Azure OpenAI {_money(a.aoai_usd_per_m_input, 2)} "
        f"input, {_money(a.aoai_usd_per_m_cached, 2)} cached input and {_money(a.aoai_usd_per_m_output, 2)} output "
        f"per million tokens; USD to CAD {a.usd_to_cad:g}. Costs use the token usage recorded for "
        f"{s['cost_measured_invoices']} invoice(s)."
    )
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{e(title)}</title><style>{REPORT_CSS}</style></head><body>",
        f"<div class='muted'>Prepared {dt.date.today().isoformat()} from AP Coder's own records · "
        "no invoice details included</div>",
        f"<h1>{e(title)}</h1><div class='grid'>",
        tile("Invoices processed", str(s["processed"])),
        tile(AS_CODED, _pct(s["straight_through"])),
        tile("Hours saved so far", f"{s['hours_saved']:,.1f}"),
        tile("Azure cost per invoice", _money(s["cost_per_invoice_cad"], 3)),
        f"</div><h2>At {a.monthly_volume:,} invoices a month</h2><table>",
        row("AP time saved", f"{p.get('hours_saved', 0):,.0f} hours / month"),
        row(f"Value of that time (at {_money(a.hourly_cost)} / hour)", f"{_money(p.get('value_saved'))} / month"),
        row("Azure cost (Document Intelligence + Azure OpenAI)", f"{_money(p.get('azure_cost'))} / month"),
        row("Net benefit", f"{_money(net)} / month", "th"),
        "</table><h2>Pilot so far</h2><table>",
        row("Approved", f"{s['approved']} ({s['approved_clean']} as coded, {s['approved_changed']} corrected)"),
        row("Flagged by the checks when processed", _pct(s["needs_attention_rate"])),
        row("Could not be read", str(s["failed"])),
        row(
            "Duplicate invoices stopped (flagged, then rejected)",
            f"{s['duplicates_stopped']}"
            + "".join(f" · {_money(t)} {c}" for c, t in s["duplicates_stopped_total"].items()),
        ),
        row("Average processing time", avg),
        "</table><h2>Most common findings</h2><table>",
        issues,
        f"</table><h2>Assumptions</h2><p class='muted'>{e(assumptions)}</p></body></html>",
    ]
    return "\n".join(parts)


def vendor_workload(store: Store, min_invoices: int = 2) -> list[dict[str, Any]]:
    """Vendors whose invoices make work: how often an invoice arrived with a problem only the vendor can fix
    (amounts that don't add up, no GST/HST number, PO issues...: the checks *Ask the vendor* covers), and how
    often AP had to correct the coding. Worst first; vendors without problems are left out. A rejected invoice is left
    out, and an approved one counts what was still open when it was approved: a finding the reviewer cleared by
    correcting what AP Coder read was AP Coder's misread, not the vendor's doing (B-1183). Worked out again once an
    invoice or the audit trail changed (``summary_memo``, D-110)."""
    found = store.summary_memo(f"vendor_workload/{min_invoices}", lambda: _vendor_workload(store, min_invoices),
                               "workload")  # fmt: skip
    return [{**v, "top_codes": list(v["top_codes"])} for v in found]


def _vendor_workload(store: Store, min_invoices: int) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    rows = store.summary_rows("status NOT IN (?, ?)", (FAILED, REJECTED), "id, status, broken, w_name, w_key, w_codes, "
                              "w_corrected")  # fmt: skip
    for r in rows:
        f = computed(store, r)
        key = f["w_key"]
        if not key:
            continue
        s = stats.setdefault(key, {"vendor_name": f["w_name"], "invoices": 0, "with_problems": 0, "corrected": 0,
                                   "approved": 0, "codes": Counter()})  # fmt: skip
        s["invoices"] += 1
        if f["w_codes"] != "[]":
            s["with_problems"] += 1
            s["codes"].update(json.loads(f["w_codes"]))
        if r["status"] in (APPROVED, PENDING):
            s["approved"] += 1
            s["corrected"] += bool(f["w_corrected"])  # the GL coding, not a date
    out = []
    for key, s in stats.items():
        if s["invoices"] < min_invoices or not (s["with_problems"] or s["corrected"]):
            continue
        s["vendor_key"] = key
        s["problem_rate"] = s["with_problems"] / s["invoices"]
        s["top_codes"] = [c for c, _ in s.pop("codes").most_common(3)]
        out.append(s)
    return sorted(out, key=lambda s: (-(s["with_problems"] + s["corrected"]), -s["problem_rate"], s["vendor_name"]))
