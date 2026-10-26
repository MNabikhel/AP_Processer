"""Supplier learning: how AP Coder learns each supplier's invoices as AP approves them.

``run_learning(...)`` makes a stream of invoices for each of ``suppliers`` suppliers (same vendor and
layout, new content each time, see ``generator.make_stream_case``) and plays them through the same code
the app runs, in order, one supplier per process:

1. read the invoice with ``capture.analyze`` and the supplier's template as stored so far;
2. decide, as the pipeline does, whether it would be approved without a person: the supplier's state
   from ``autonomy_status`` over its recorded outcomes, then ``should_auto_approve`` and the audit sample
   (``pick_for_audit``). Touchless processing is on, so a supplier goes touchless by itself as soon as it
   meets the bar, and again after a suspension once it has a fresh clean streak (``automatic_transition``,
   exactly as ``Store.sync_autonomy`` applies it);
3. unless it went through untouched, AP "approves" the truth: the outcomes (what capture proposed against
   what was approved, ``outcome_rows``) are recorded in a real ``Store`` and the template learns from the
   approved values (``learn`` on ``confirmed_values``), exactly as ``workflow.learn_from_approval`` does.
   A correction on an autonomous supplier suspends it, as ``Store.record_outcomes`` does.

Each invoice is also scored against its truth (``score_case``), and read a second time without the
template so the gain from learning is measured on the very same invoices. Writes ``report.json``,
``report.md`` and ``invoices.jsonl``.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import tempfile
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from ap_coder.bench.generator import (
    GENERATOR_VERSION,
    make_stream_case,
    stream_scanned,
    supplier_archetype,
    supplier_id,
)
from ap_coder.bench.harness import _map, _pct, _table
from ap_coder.bench.scoring import score_case, values_match
from ap_coder.capture.types import FIELDS, MISSING, VERIFIED, CaptureResult

BUCKETS = ((1, 1), (2, 5), (6, 10), (11, 20), (21, 40), (41, 80), (81, 10**9))
EPOCH = dt.datetime(2026, 1, 5, 9, 0, 0)


def bucket_name(lo: int, hi: int) -> str:
    return str(lo) if lo == hi else f"{lo}+" if hi >= 10**9 else f"{lo}-{hi}"


def approved_values(truth: dict[str, Any], capture: CaptureResult | None) -> dict[str, Any]:
    """What AP approves: every header field printed on the invoice, at its true value. A proposed value
    that already says the same ("Net 60" for "60 jours net") is left as proposed: AP only corrects what
    is wrong. A value the invoice implies without printing it (the currency of a Canadian invoice, a tax
    total) is only part of the comparison when capture proposed it, as the benchmark scores it."""

    def proposed(f: str) -> Any:
        fr = capture.fields.get(f) if capture else None
        return fr.value if fr is not None and fr.status != MISSING and fr.value not in (None, "") else None

    final: dict[str, Any] = {}
    printed = {f: e["value"] for f, e in (truth.get("fields") or {}).items() if f in FIELDS}
    implied = {f: v for f, v in (truth.get("implied") or {}).items() if f in FIELDS and proposed(f) is not None}
    for f, value in {**printed, **implied}.items():
        got = proposed(f)
        final[f] = got if got is not None and values_match(f, value, got) else value
    return final


def _field_counts(recs: list[dict[str, Any]]) -> dict[str, int]:
    present = [r for r in recs if r["present"]]
    spurious = sum(1 for r in recs if r["spurious"])
    return {
        "fields": len(present),
        "correct": sum(1 for r in present if r["correct"]),
        "verified": sum(1 for r in present if r["status"] == VERIFIED),
        "spurious": spurious,
        "fully_correct": all(r["correct"] for r in present) and not spurious,
        "wrong_fields": [r["field"] for r in present if not r["correct"]] + [r["field"] for r in recs if r["spurious"]],
    }


Job = tuple[str, int, int, int, float, dict[str, Any] | None, bool, tuple[str, ...], int]
# ``--wrong-approval N``: on each supplier's invoice N the clerk teaches the invoice number on the wrong words (the
# invoice date's, clicked by mistake) and approves; the approval is then withdrawn (reopened: a second look caught it)
# before the next invoice arrives. How the next invoices read is in the report's "wrong_approval".
WRONG_FIELD, CLICKED_FIELD = "invoice_number", "invoice_date"


def _wrong_click(layout: Any, truth: dict[str, Any]) -> tuple[str, list[Any]] | None:
    """(the text, the boxes) of the words a clerk clicks by mistake for the invoice number: the invoice date's."""
    from ap_coder.capture.supplier import _find_spans

    value = ((truth.get("fields") or {}).get(CLICKED_FIELD) or {}).get("value")
    spans = _find_spans(layout, CLICKED_FIELD, value) if value else []
    if not spans or not spans[0][1]:
        return None
    words = spans[0][1]
    return " ".join(w.text for w in words), [w.box for w in words]


def simulate_supplier(job: Job) -> dict[str, Any]:
    """Play one supplier's invoices through capture, review and learning. Returns its invoice rows.

    What-if ``LINES_ADD_UP`` in the ignored checks: capture runs without line items, so the line-items
    check neither fails nor caps the subtotal's confidence (as with a line reader that never adds a
    totals row to the lines)."""
    import ap_coder.capture as capture_pkg

    original = capture_pkg.read_line_items
    if "LINES_ADD_UP" in job[7]:
        capture_pkg.read_line_items = lambda layout: []
    try:
        return _simulate(job)
    finally:
        capture_pkg.read_line_items = original


def _simulate(job: Job) -> dict[str, Any]:
    from ap_coder.capture import analyze, build_layout
    from ap_coder.capture.confidence import apply_supplier_record, reading_confidence
    from ap_coder.capture.supplier import (
        AUTONOMOUS,
        SUPERVISED,
        SUSPENDED,
        AutonomyPolicy,
        automatic_transition,
        autonomy_status,
        confirmed_values,
        learn,
        outcome_rows,
        pick_for_audit,
        should_auto_approve,
    )
    from ap_coder.core.store import Store

    out_dir, seed, supplier, invoices, scanned_fraction, policy_d, vendor_master, ignore, *more = job
    wrong_at = int(more[0]) if more else 0
    policy = AutonomyPolicy.from_dict(policy_d)
    sid = supplier_id(seed, supplier)
    cases_dir = Path(out_dir) / "cases" / sid
    rows: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []  # for `bench calibrate`: what a template adds to the evidence
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ap-learn-") as tmp, contextlib.ExitStack() as held:
        store = Store(Path(tmp) / "learn.db")
        held.enter_context(store.session())  # one connection for the run, as the app holds one for each request
        # The stored state, since when it is touchless and when it was last suspended (as the store keeps them).
        stored, since, suspended_at = SUPERVISED, None, None
        key = ""
        for i in range(invoices):
            case = make_stream_case(cases_dir, seed, supplier, i, stream_scanned(seed, supplier, i, scanned_fraction))
            truth = case.truth
            at = (EPOCH + dt.timedelta(hours=i)).isoformat(timespec="seconds")
            fields = truth["fields"]
            name = fields["vendor_name"]["value"]
            gst = (fields.get("gst_hst_registration_number") or {}).get("value")
            key = key or store.supplier_key_for(name, gst)
            # An established supplier is in the vendor master (as bridge.vendor_record gives it).
            vendor = {"name": name, "gst_number": gst or "", "erp_id": ""} if vendor_master else None
            # The day AP processes it: a few days after the invoice date (the date checks expect that).
            today = dt.date.fromisoformat(fields["invoice_date"]["value"]) + dt.timedelta(days=3)

            # The supplier's state before this invoice, as the pipeline computes it.
            stats = store.supplier_stats(key, policy.window)
            turned_on = False
            move = automatic_transition(stats, policy, stored, since, suspended_at)
            if move is not None:  # touchless processing is on: the supplier's record moves it by itself
                stored = move[0]
                if stored == AUTONOMOUS:
                    since, turned_on = at, True
                elif stored == SUSPENDED:
                    suspended_at = at
            state, _progress, _why = autonomy_status(stats, policy, stored, since, suspended_at=suspended_at)

            layout = build_layout(case.path)
            profile = store.get_supplier_profile(key) or {}
            template = profile.get("template") or None
            capture = analyze(case.path, template=template, vendor=vendor, layout=layout, today=today)
            plain = analyze(case.path, vendor=vendor, layout=layout, today=today) if template else capture
            if template:  # as offline_coder.read_invoice does: the supplier's record of each field (D-89)
                apply_supplier_record(capture.fields, {n: (f.n, f.correct) for n, f in stats.fields.items()})
            recs = score_case(truth, capture)
            evidence.extend(
                {"case": case.id, "scanned": case.scanned, "field": r["field"], "evidence": r["evidence"],
                 "correct": r["correct"]}
                for r in recs
                if r["scored"] and r["reported"] and r["evidence"]
            )  # fmt: skip
            got = _field_counts(recs)
            base = _field_counts(score_case(truth, plain))

            # What-if: checks named in ``ignore`` do not hold an invoice back (e.g. a known reader gap).
            judged = replace(capture, checks=[c for c in capture.checks if c.get("code") not in ignore])
            auto, reason = should_auto_approve(state, judged, checks_ok=True)
            eligible, why_not = should_auto_approve(AUTONOMOUS, judged, checks_ok=True)  # if it were autonomous
            audit = auto and pick_for_audit(case.id, policy.audit_rate)
            touchless = auto and not audit

            final = approved_values(truth, capture)
            taught = None
            invoice_id = 100_000 + i + 1  # the store's own ids stay free for an invoice approved in it (``wrong_at``)
            clicked = _wrong_click(layout, truth) if i + 1 == wrong_at else None
            if clicked is not None:  # the clerk's slip: the wrong words taught for the invoice number
                final = {**final, WRONG_FIELD: clicked[0]}
                taught = {WRONG_FIELD: clicked[1]}
                invoice_id = store.add_invoice(case.path, {"vendor_name": name, "line_items": []}, None)
                store.approve_invoice(invoice_id, {"vendor_name": name, "line_items": [], "grand_total": 0.0},
                                      "AP clerk (benchmark)")  # fmt: skip
            outcomes = outcome_rows(capture, final)
            corrections = [r["field"] for r in outcomes if not r["correct"]]
            if not touchless:  # a person saw it: record what was corrected, and learn from the approval
                store.record_outcomes(key, invoice_id, outcomes, source="audit" if audit else "review",
                                      display_name=name, at=at)  # fmt: skip
                if corrections and stored == AUTONOMOUS:
                    stored, suspended_at = SUSPENDED, at  # one correction suspends autonomy
                learned = learn(template, layout, confirmed_values(final, taught))
                if clicked is not None:
                    store.save_supplier_profile(key, display_name=name, template=learned, invoice_id=invoice_id)
                    # A second look caught it: the approval is withdrawn before the next invoice arrives.
                    store.reopen(invoice_id, "AP clerk (benchmark)", "the wrong words were taught")
                else:
                    store.save_supplier_profile(key, display_name=name, template=learned)

            rows.append(
                {
                    "supplier": sid,
                    "layout": truth["layout"],
                    "index": i + 1,
                    "case": case.id,
                    "scanned": case.scanned,
                    "state": state,
                    "turned_on": turned_on,
                    "template_fields": len((template or {}).get("fields") or {}),
                    **{k: got[k] for k in ("fields", "correct", "verified", "spurious", "fully_correct")},
                    "wrong_fields": got["wrong_fields"],
                    "accuracy": round(got["correct"] / got["fields"], 4) if got["fields"] else None,
                    "reading": reading_confidence(capture.fields),  # the Reading shown (before the checks, D-89)
                    "baseline_reading": reading_confidence(plain.fields),
                    "baseline_correct": base["correct"],
                    "baseline_verified": base["verified"],
                    "baseline_fully_correct": base["fully_correct"],
                    "outcome_fields": len(outcomes),
                    "corrections": corrections,
                    "eligible": eligible,
                    "eligible_reason": "" if eligible else why_not,
                    "auto": auto,
                    "audit": audit,
                    "touchless": touchless,
                    "touchless_error": touchless and (bool(corrections) or not got["fully_correct"]),
                    "reason": reason,
                }
            )
    return {"supplier": sid, "rows": rows, "evidence": evidence, "seconds": round(time.perf_counter() - t0, 2)}


# --- the report -------------------------------------------------------------------------------


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def _bucket_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n_fields = sum(r["fields"] for r in rows)
    return {
        "invoices": len(rows),
        "fields": n_fields,
        "accuracy": _ratio(sum(r["correct"] for r in rows), n_fields),
        "baseline_accuracy": _ratio(sum(r["baseline_correct"] for r in rows), n_fields),
        "verified": _ratio(sum(r["verified"] for r in rows), n_fields),
        "baseline_verified": _ratio(sum(r["baseline_verified"] for r in rows), n_fields),
        "fully_correct": _ratio(sum(r["fully_correct"] for r in rows), len(rows)),
        "baseline_fully_correct": _ratio(sum(r["baseline_fully_correct"] for r in rows), len(rows)),
        "no_correction": _ratio(sum(not r["corrections"] for r in rows), len(rows)),
        "eligible": _ratio(sum(r["eligible"] for r in rows), len(rows)),
        "eligible_wrong": sum(1 for r in rows if r["eligible"] and (r["corrections"] or not r["fully_correct"])),
    }


WRONG_SPANS = (("the wrong approval's next invoice", 1, 1), ("2-5 invoices after", 2, 5),
               ("6+ invoices after", 6, 10**9))  # fmt: skip


def wrong_approval(rows: list[dict[str, Any]], at: int) -> dict[str, Any]:
    """Accuracy after a wrong approval on invoice ``at`` (withdrawn before the next invoice arrives), on the invoices
    after it, and how many of them had the invoice number (the field taught wrong) wrong."""
    out: dict[str, Any] = {"at": at}
    for label, lo, hi in WRONG_SPANS:
        part = [r for r in rows if at + lo <= r["index"] <= at + hi]
        out[label] = {
            "invoices": len(part),
            "accuracy": _ratio(sum(r["correct"] for r in part), sum(r["fields"] for r in part)),
            "invoice_number_wrong": sum(WRONG_FIELD in r["wrong_fields"] for r in part),
        }
    return out


CALIBRATION_BUCKETS = ((1, 1), (2, 5), (6, 20), (21, 10**9))


def calibration(rows: list[dict[str, Any]], said: str, right: str, position: str = "index",
                weight: str = "") -> dict[str, dict[str, Any]]:  # fmt: skip
    """Per invoice-position bucket (1, 2-5, 6-20, 21+): the mean confidence the invoices were given (``said``) against
    the share actually right (``right``: a share per invoice; ``weight``: what it is a share of, e.g. its fields, so the
    share is over all of them), to check the confidence is honest (D-89). ``gap``: said minus right, in points."""
    out = {}
    for lo, hi in CALIBRATION_BUCKETS:
        part = [r for r in rows if lo <= r[position] <= hi and r.get(said) is not None and r.get(right) is not None]
        if not part:
            continue
        w = [float(r[weight]) if weight else 1.0 for r in part]
        mean_said = sum(r[said] for r in part) / len(part)
        share = sum(r[right] * x for r, x in zip(part, w, strict=True)) / (sum(w) or 1.0)
        out[bucket_name(lo, hi)] = {"invoices": len(part), "said": round(mean_said, 4), "right": round(share, 4),
                                    "gap": round(100 * (mean_said - share), 1)}  # fmt: skip
    return out


def calibration_table(calib: dict[str, dict[str, Any]], said: str = "mean Reading", right: str = "share right") -> str:
    return _table(["invoice #", "invoices", said, right, "gap (points)"],
                  [[b, m["invoices"], _pct(m["said"]), _pct(m["right"]), f"{m['gap']:+.1f}"]
                   for b, m in calib.items()])  # fmt: skip


def summarize_learning(rows: list[dict[str, Any]], wrong_at: int = 0) -> dict[str, Any]:
    by_supplier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_supplier[r["supplier"]].append(r)
    buckets = {}
    for lo, hi in BUCKETS:
        part = [r for r in rows if lo <= r["index"] <= hi]
        if part:
            buckets[bucket_name(lo, hi)] = _bucket_stats(part)
    suppliers = []
    after_all: list[dict[str, Any]] = []
    for sid in sorted(by_supplier):
        rs = sorted(by_supplier[sid], key=lambda r: r["index"])
        on = next((r["index"] for r in rs if r["turned_on"]), None)
        after = [r for r in rs if on is not None and r["index"] >= on]
        after_all += after
        suppliers.append(
            {
                "supplier": sid,
                "layout": rs[0]["layout"],
                "invoices": len(rs),
                "scanned": sum(r["scanned"] for r in rs),
                "accuracy_first": rs[0]["accuracy"],
                "accuracy_rest": _ratio(sum(r["correct"] for r in rs[1:]), sum(r["fields"] for r in rs[1:])),
                "reviewed_with_correction": sum(1 for r in rs if r["corrections"] and not r["touchless"]),
                # Autonomy is switched on before this invoice: the bar was met after the ones before it.
                "ready_at": on,
                "after_ready": len(after),
                "touchless": sum(r["touchless"] for r in after),
                "audited": sum(r["audit"] for r in after),
                "touchless_errors": sum(r["touchless_error"] for r in after),
                "suspensions": sum(
                    1
                    for a, b in zip(rs, rs[1:], strict=False)
                    if a["state"] == "autonomous" and b["state"] == "suspended"
                ),
                "touchless_rate": _ratio(sum(r["touchless"] for r in after), len(after)),
            }
        )
    touchless = [r for r in after_all if r["touchless"]]
    touchless_errors = sum(r["touchless_error"] for r in touchless)
    ready = [s for s in suppliers if s["ready_at"] is not None]
    by_layout: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in suppliers:
        by_layout[s["layout"]].append(s)
    return {
        "invoices": len(rows),
        "suppliers": len(suppliers),
        "buckets": buckets,
        "overall": _bucket_stats(rows),
        "per_supplier": suppliers,
        "autonomy": {
            "suppliers_ready": len(ready),
            "ready_at_median": sorted(s["ready_at"] for s in ready)[len(ready) // 2] if ready else None,
            "ready_at_min": min((s["ready_at"] for s in ready), default=None),
            "ready_at_max": max((s["ready_at"] for s in ready), default=None),
            "invoices_after_ready": len(after_all),
            "touchless": len(touchless),
            "audited": sum(r["audit"] for r in after_all),
            "touchless_rate": _ratio(len(touchless), len(after_all)),
            "touchless_errors": touchless_errors,
            "touchless_precision": _ratio(len(touchless) - touchless_errors, len(touchless)),
            "suspensions": sum(s["suspensions"] for s in suppliers),
            "share_of_all_invoices_touchless": _ratio(len(touchless), len(rows)),
        },
        "layouts": {
            lay: {
                "suppliers": len(ss),
                "ready": sum(1 for s in ss if s["ready_at"] is not None),
                "accuracy_first": _ratio(sum(s["accuracy_first"] or 0 for s in ss), len(ss)),
                "accuracy_rest": _ratio(sum(s["accuracy_rest"] or 0 for s in ss), len(ss)),
            }
            for lay, ss in sorted(by_layout.items())
        },
        "wrong_approval": wrong_approval(rows, wrong_at) if wrong_at else None,
        # Is the Reading honest? Its mean against the share of the printed fields read right (D-89); and the same
        # invoices read without the template (a first-time supplier's Reading). The Coding: see the coding bench.
        "calibration": {
            "reading": calibration(rows, "reading", "accuracy", weight="fields"),
            "reading_without_template": calibration(
                [{**r, "accuracy": _ratio(r["baseline_correct"], r["fields"])} for r in rows],
                "baseline_reading",
                "accuracy",
                weight="fields",
            ),
        },
        "why_not_touchless": _reasons([r for r in after_all if not r["auto"]], "reason"),
        "why_not_eligible": _reasons([r for r in rows if r["index"] > 1 and not r["eligible"]], "eligible_reason"),
    }


def _reasons(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in rows:
        out[r[key]] += 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:12])


def _wrong_section(w: dict[str, Any] | None) -> list[str]:
    if not w:
        return []
    return [
        "## A wrong approval, then its withdrawal\n",
        f"On each supplier's invoice {w['at']} the clerk taught the invoice number on the invoice date's words and "
        "approved; the approval was withdrawn (reopened) before the next invoice arrived.\n",
        _table(
            ["invoices", "count", "accuracy", "invoice numbers wrong"],
            [
                [k, v["invoices"], _pct(v["accuracy"]), v["invoice_number_wrong"]]
                for k, v in w.items()
                if isinstance(v, dict)
            ],
        ),  # fmt: skip
    ]


def render_markdown(report: dict[str, Any]) -> str:
    cfg, s = report["config"], report["summary"]
    a = s["autonomy"]
    pol = cfg["policy"]
    out = [
        "# Supplier learning\n",
        f"{s['suppliers']} suppliers x {cfg['invoices']} invoices (seed {cfg['seed']}, scanned share "
        f"{cfg['scanned']}, generator v{cfg['generator_version']}). Each supplier's invoices share the vendor, "
        "layout, wording and formats; number, dates, PO, lines and amounts change. They are read in order "
        "with the supplier's template as learned so far, AP approves the true values, and the template "
        "learns from every approval.\n",
        f"Autonomy policy: at least {pol['min_invoices']} reviewed invoices, a {pol['min_lower_bound']:.0%} "
        f"lower bound on header-field accuracy, the last {pol['clean_streak']} without a correction; "
        f"{pol['audit_rate']:.0%} audit sample. Touchless processing is on: a supplier goes touchless by itself as "
        "soon as it meets the bar.\n",
        ("Suppliers are in the vendor master (name and GST/HST number checked). " if cfg["vendor_master"] else "")
        + (
            f"**What-if:** a failed {', '.join(cfg['ignore_checks'])} check does not hold an invoice back.\n"
            if cfg["ignore_checks"]
            else "\n"
        ),
        *_wrong_section(s.get("wrong_approval")),
        "## Learning curve\n",
        "Accuracy over the printed header fields, by the invoice's position in its supplier's stream; "
        "*without template* reads the same invoices with the rule reader only. *No correction*: AP changed "
        "nothing. *Eligible*: every printed field verified and every check passed (would go touchless if "
        "the supplier were autonomous); *eligible wrong*: of those, invoices with a wrong field.\n",
        _table(
            [
                "invoice #",
                "invoices",
                "accuracy",
                "without template",
                "verified",
                "fully correct",
                "no correction",
                "eligible",
                "eligible wrong",
            ],  # fmt: skip
            [
                [
                    b,
                    m["invoices"],
                    _pct(m["accuracy"]),
                    _pct(m["baseline_accuracy"]),
                    _pct(m["verified"]),
                    _pct(m["fully_correct"]),
                    _pct(m["no_correction"]),
                    _pct(m["eligible"]),
                    m["eligible_wrong"],
                ]
                for b, m in s["buckets"].items()
            ],
        ),
        "## Calibration of the Reading\n",
        "The Reading the review screen shows (before its checks take anything off: D-89), the mean over the invoices "
        "of each position, against the share of their printed header fields actually read right. The Coding is "
        "measured by the coding bench (this one reads headers only).\n",
        calibration_table((s.get("calibration") or {}).get("reading") or {}),
        "\nThe same invoices read without the supplier's template (as a first-time supplier's):\n",
        calibration_table((s.get("calibration") or {}).get("reading_without_template") or {}),
        "",
        "## Autonomy\n",
        f"- Suppliers that reached the policy: **{a['suppliers_ready']} of {s['suppliers']}**"
        + (
            f", switched on before invoice {a['ready_at_min']} at the earliest, {a['ready_at_median']} "
            f"(median), {a['ready_at_max']} at the latest."
            if a["suppliers_ready"]
            else "."
        ),
        f"- After that point: {a['invoices_after_ready']} invoices, **{a['touchless']} touchless "
        f"({_pct(a['touchless_rate'])})**, {a['audited']} audited.",
        f"- Touchless invoices with any wrong field: **{a['touchless_errors']}** (precision "
        f"{_pct(a['touchless_precision'])}). Suspensions: {a['suspensions']}.",
        f"- Touchless share of all {s['invoices']} invoices: {_pct(a['share_of_all_invoices_touchless'])}.\n",
    ]
    if s["why_not_touchless"]:
        out.append("Why an invoice of an autonomous supplier still went to a person:\n")
        out.append(_table(["reason", "invoices"], [[k or "-", v] for k, v in s["why_not_touchless"].items()]))
    if s["why_not_eligible"]:
        out.append(
            "\nWhy an invoice (after a supplier's first) would not go touchless even for an autonomous supplier:\n"
        )
        out.append(_table(["reason", "invoices"], [[k or "-", v] for k, v in s["why_not_eligible"].items()]))
    out.append("\n## Suppliers\n")
    out.append(
        _table(
            [
                "supplier",
                "layout",
                "first invoice acc.",
                "later acc.",
                "reviewed with a correction",
                "autonomous from #",
                "after",
                "touchless",
                "audited",
                "touchless errors",
                "suspensions",
            ],  # fmt: skip
            [
                [
                    p["supplier"],
                    p["layout"],
                    _pct(p["accuracy_first"]),
                    _pct(p["accuracy_rest"]),
                    p["reviewed_with_correction"],
                    p["ready_at"] or "never",
                    p["after_ready"],
                    p["touchless"],
                    p["audited"],
                    p["touchless_errors"],
                    p["suspensions"],
                ]
                for p in s["per_supplier"]
            ],
        )
    )
    out.append("\n## By layout\n")
    out.append(
        _table(
            ["layout", "suppliers", "ready", "first invoice acc.", "later acc."],
            [
                [lay, m["suppliers"], m["ready"], _pct(m["accuracy_first"]), _pct(m["accuracy_rest"])]
                for lay, m in s["layouts"].items()
            ],
        )
    )
    t = report["timing"]
    out.append(f"\nTime: {t['seconds']} s ({t['per_invoice_s']} s per invoice, {cfg['workers']} worker(s)).\n")
    return "\n".join(out)


def run_learning(
    suppliers: int = 20,
    invoices: int = 40,
    seed: int = 1,
    scanned: float = 0.0,
    out: str | Path = "bench_learn",
    workers: int = 1,
    policy: dict[str, Any] | None = None,
    vendor_master: bool = True,
    ignore_checks: tuple[str, ...] = (),
    wrong_at: int = 0,
) -> dict[str, Any]:
    """``wrong_at``: the invoice of each supplier approved with the wrong words taught, then withdrawn (0: none)."""
    from ap_coder.capture.supplier import AutonomyPolicy

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    pol = AutonomyPolicy.from_dict(policy)
    t0 = time.perf_counter()
    jobs = [
        (str(out), seed, s, invoices, scanned, pol.to_dict(), vendor_master, tuple(ignore_checks), wrong_at)
        for s in range(suppliers)
    ]
    results = _map(simulate_supplier, jobs, workers, "suppliers")
    rows = [r for res in results for r in res["rows"]]
    seconds = time.perf_counter() - t0
    report = {
        "config": {
            "suppliers": suppliers,
            "invoices": invoices,
            "seed": seed,
            "scanned": scanned,
            "workers": workers,
            "generator_version": GENERATOR_VERSION,
            "policy": pol.to_dict(),
            "vendor_master": vendor_master,
            "ignore_checks": list(ignore_checks),
            **({"wrong_approval_at": wrong_at} if wrong_at else {}),
            "layouts": {supplier_id(seed, s): supplier_archetype(seed, s) for s in range(suppliers)},
        },
        "summary": summarize_learning(rows, wrong_at),
        "timing": {"seconds": round(seconds, 1), "per_invoice_s": round(seconds * workers / max(1, len(rows)), 2)},
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    with (out / "invoices.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    with (out / "evidence.jsonl").open("w", encoding="utf-8") as fh:
        for res in results:
            for r in res.get("evidence") or []:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return report
