"""Supplier learning: how AP Coder learns each supplier's invoices as AP approves them.

``run_learning(...)`` makes a stream of invoices for each of ``suppliers`` suppliers (same vendor and
layout, new content each time, see ``generator.make_stream_case``) and plays them through the same code
the app runs, in order, one supplier per process:

1. read the invoice as the app reads it (``offline_coder.read_invoice``): a first read says who the supplier is,
   then the supplier's template as stored so far, its vendor master record and its record of each field;
2. decide, as the pipeline does, whether it would be approved without a person: the supplier's state
   from ``autonomy_status`` over its recorded outcomes, then ``should_auto_approve`` and the audit sample
   (``pick_for_audit``). Touchless processing is on, so a supplier goes touchless by itself as soon as it
   meets the bar, and again after a suspension once it has a fresh clean streak (``automatic_transition``,
   exactly as ``Store.sync_autonomy`` applies it);
3. unless it went through untouched, AP "approves" the truth (``--bulk``: some invoices are approved in bulk as
   proposed, nobody looking) in a real ``Store`` (``add_invoice``, ``approve_invoice``) and the app learns from it
   with ``workflow.learn_from_approval``: the outcomes, the readers' scores, the template, the lessons, a vendor name
   AP corrected. A correction on an autonomous supplier suspends it, as ``Store.record_outcomes`` does.

Each invoice is also scored against its truth (``score_case``), and read a second time without the
template so the gain from learning is measured on the very same invoices, and a field AP corrected is looked at
again on the supplier's next invoice ("right next time"). Writes ``report.json``, ``report.md`` and
``invoices.jsonl``.
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
from ap_coder.capture.supplier import compare_key
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
# before the next invoice arrives, or with ``--withdraw-after K`` once K more invoices were approved (the template has
# learned from them meanwhile). How the next invoices read is in the report's "wrong_approval".
WRONG_FIELD, CLICKED_FIELD = "invoice_number", "invoice_date"
CLERK = "AP clerk (benchmark)"


def _wrong_click(layout: Any, truth: dict[str, Any]) -> tuple[str, list[Any]] | None:
    """(the text, the boxes) of the words a clerk clicks by mistake for the invoice number: the invoice date's."""
    from ap_coder.capture.supplier import _find_spans

    value = ((truth.get("fields") or {}).get(CLICKED_FIELD) or {}).get("value")
    spans = _find_spans(layout, CLICKED_FIELD, value) if value else []
    if not spans or not spans[0][1]:
        return None
    words = spans[0][1]
    return " ".join(w.text for w in words), [w.box for w in words]


# What AP knows of a supplier that its invoices do not print (``--ap-knows``): its payment terms, and for some, that it
# bills in US dollars (a Canadian address, amounts printed with "$" alone).
KNOWN_TERMS = ("Net 45", "Net 60", "Net 15", "2% 10 Net 30", "Due on receipt")


def known_fields(recs: list[dict[str, Any]], known: dict[str, str]) -> list[dict[str, Any]]:
    """``recs`` (``score_case``) with each value AP knows and the invoice does not print scored as a field of the
    invoice: right only when proposed so (posted without it, or otherwise, it is wrong)."""
    out = []
    for r in recs:
        if r["field"] in known:
            right = bool(r["reported"]) and values_match(r["field"], known[r["field"]], r["got"])
            r = {**r, "present": True, "truth": known[r["field"]], "correct": right, "spurious": False, "scored": True}
        out.append(r)
    return out


def ap_knows(seed: int, supplier: int, share: float) -> dict[str, str]:
    """{field: value} AP sets on each of this supplier's invoices that does not print it ({}: none), for a ``share`` of
    the suppliers, at random."""
    import random

    rng = random.Random(f"ap-bench-knows:{seed}:{supplier}")
    if share <= 0 or rng.random() >= share:
        return {}
    known = {"payment_terms": rng.choice(KNOWN_TERMS)}
    if rng.random() < 0.5:
        known["currency"] = "USD"
    return known


def bulk_pick(sid: str, index: int, share: float) -> bool:
    """Whether this invoice is one AP approves in bulk, without opening it (``--bulk``: a share, at random)."""
    if share <= 0:
        return False
    import random

    return random.Random(f"ap-bench-bulk:{sid}:{index}").random() < share


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


@contextlib.contextmanager
def _app_clock(folder: str) -> Any:
    """The app's data folder in ``folder`` (its OCR cache, as ``learn_from_approval`` reads the pages again) and the
    store's clock set by the simulation (``clock[0]``): an invoice is reviewed an hour after the one before it, so the
    supplier's record (its clean streak since a suspension) reads in the stream's order."""
    import os
    import sys

    from ap_coder.core.store import _base

    clock = [_base._now()]
    real = _base._now
    patched = [m for name, m in list(sys.modules.items())
               if name.startswith("ap_coder.core.store") and getattr(m, "_now", None) is real]  # fmt: skip
    before = os.environ.get("AP_PRIVATE_DIR")
    os.environ["AP_PRIVATE_DIR"] = folder
    for m in patched:
        m._now = lambda: clock[0]
    try:
        yield clock
    finally:
        for m in patched:
            m._now = real
        if before is None:
            os.environ.pop("AP_PRIVATE_DIR", None)
        else:
            os.environ["AP_PRIVATE_DIR"] = before


def _simulate(job: Job) -> dict[str, Any]:
    from ap_coder.ai.offline_coder import read_invoice
    from ap_coder.capture import analyze, build_layout
    from ap_coder.capture.confidence import reading_confidence
    from ap_coder.capture.layout import ocr_cache
    from ap_coder.capture.supplier import (
        AUTONOMOUS,
        SUPERVISED,
        SUSPENDED,
        AutonomyPolicy,
        automatic_transition,
        autonomy_status,
        pick_for_audit,
        should_auto_approve,
    )
    from ap_coder.capture.workflow import learn_from_approval
    from ap_coder.core.store import Store

    out_dir, seed, supplier, invoices, scanned_fraction, policy_d, vendor_master, ignore, *more = job
    wrong_at = int(more[0]) if more else 0
    withdraw_after = int(more[1]) if len(more) > 1 else 0
    bulk_share = float(more[2]) if len(more) > 2 else 0.0
    known = ap_knows(seed, supplier, float(more[3]) if len(more) > 3 else 0.0)
    policy = AutonomyPolicy.from_dict(policy_d)
    sid = supplier_id(seed, supplier)
    cases_dir = Path(out_dir) / "cases" / sid
    rows: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []  # for `bench calibrate`: what a template adds to the evidence
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ap-learn-") as tmp, contextlib.ExitStack() as held:
        clock = held.enter_context(_app_clock(tmp))
        store = Store(Path(tmp) / "learn.db")
        held.enter_context(store.session())  # one connection for the run, as the app holds one for each request
        held.enter_context(ocr_cache(Path(tmp) / ".cache" / "ocr"))  # each page OCR'd once, as on arrival (B-1294)
        # The stored state, since when it is touchless and when it was last suspended (as the store keeps them).
        stored, since, suspended_at = SUPERVISED, None, None
        key = ""
        withdraw: tuple[int, int] | None = None  # (the wrong approval's invoice id, withdrawn after this invoice)
        for i in range(invoices):
            case = make_stream_case(cases_dir, seed, supplier, i, stream_scanned(seed, supplier, i, scanned_fraction))
            truth = case.truth
            at = (EPOCH + dt.timedelta(hours=i)).isoformat(timespec="seconds")
            clock[0] = at
            fields = truth["fields"]
            name = fields["vendor_name"]["value"]
            gst = (fields.get("gst_hst_registration_number") or {}).get("value")
            if i == 0 and vendor_master:  # an established supplier is in the vendor master, as AP imported it
                store.import_vendor_master([{"vendor_name": name, "gst": gst or "", "erp_id": None,
                                             "status": "active"}])  # fmt: skip
            key = key or store.supplier_key_for(name, gst)  # where AP's approvals of this supplier are counted
            # The vendor master's record, as the rule reader alone (without learning) is given it.
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
            # Read as the app reads it: a first read says who the supplier is, then its template, its vendor master
            # record and its record of each field (``offline_coder.read_invoice``).
            capture, _read_key, profile = read_invoice(case.path, store, layout=layout, today=today)
            template = (profile or {}).get("template") or None
            plain = analyze(case.path, vendor=vendor, layout=layout, today=today)  # without anything learned
            # What AP knows of the supplier and the invoice does not print is part of what is right (``--ap-knows``):
            # proposed as AP knows it, it is no error; proposed otherwise, it is one.
            known_now = {f: v for f, v in known.items() if f not in (truth.get("fields") or {})}
            recs = known_fields(score_case(truth, capture), known_now)
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
            # What AP knows of this supplier that its invoices do not print (``--ap-knows``): set on each approval.
            final.update(known_now)
            taught = None
            clicked = _wrong_click(layout, truth) if i + 1 == wrong_at else None
            if clicked is not None:  # the clerk's slip: the wrong words taught for the invoice number
                final = {**final, WRONG_FIELD: clicked[0]}
                taught = {WRONG_FIELD: clicked[1]}
            # Approved in bulk: nobody opened it, what was proposed is approved as it is (right or wrong). Only an
            # invoice with every check passed, not a credit note, as bulk approval takes.
            bulk = (clicked is None and i > 0 and not touchless and not audit and not capture.credit_note
                    and all(c.get("ok", True) for c in capture.checks) and bulk_pick(sid, i, bulk_share))  # fmt: skip
            if bulk:
                final = kept_values(truth, capture)
            proposed = {f: fr.value for f, fr in capture.fields.items() if fr.value not in (None, "")}
            corrections: list[str] = []
            if not touchless:  # a person saw it (or approved it in bulk): approved and learned from, as in the app
                invoice_id = store.add_invoice(case.path, {**proposed, "line_items": []}, None,
                                               meta={"capture": {"audit": audit}})  # fmt: skip
                store.save_capture(invoice_id, capture.to_dict())
                coding = {**final, "line_items": []}
                store.approve_invoice(invoice_id, coding, CLERK, bulk=bulk)
                learned = learn_from_approval(store, invoice_id, coding, actor=CLERK, taught=taught, bulk=bulk)
                corrections = list(learned.get("corrected") or [])
                if corrections and stored == AUTONOMOUS:
                    stored, suspended_at = SUSPENDED, at  # one correction suspends autonomy
                if clicked is not None:
                    withdraw = (invoice_id, i + 1 + withdraw_after)
            if withdraw is not None and i + 1 >= withdraw[1]:
                # A second look caught it: the approval is withdrawn (reopened), and stays reopened.
                store.reopen(withdraw[0], CLERK, "the wrong words were taught")
                withdraw = None
            # The clerk's slip is no reading error: it is not counted as one.
            misread = [f for f in corrections if not (clicked is not None and f == WRONG_FIELD)]

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
                    "present_fields": [r["field"] for r in recs if r["present"]],
                    "accuracy": round(got["correct"] / got["fields"], 4) if got["fields"] else None,
                    "reading": reading_confidence(capture.fields),  # the Reading shown (before the checks, D-89)
                    "baseline_reading": reading_confidence(plain.fields),
                    "baseline_correct": base["correct"],
                    "baseline_verified": base["verified"],
                    "baseline_fully_correct": base["fully_correct"],
                    "baseline_wrong_fields": base["wrong_fields"],
                    "corrections": misread,
                    # Each value AP knows and the invoice does not print: whether it was proposed so (D-121).
                    "known": {
                        f: compare_key(f, (capture.fields.get(f).value if capture.fields.get(f) else None))
                        == compare_key(f, v)
                        for f, v in known_now.items()
                    },
                    "slip": clicked is not None,
                    "bulk": bulk,
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


def kept_values(truth: dict[str, Any], capture: CaptureResult) -> dict[str, Any]:
    """What a bulk approval approves: what capture proposed for each field the invoice prints (or implies and capture
    proposed), as it is: nobody looked."""
    fields = {f for f in (truth.get("fields") or {}) if f in FIELDS} | {f for f in (truth.get("implied") or {})
                                                                          if f in FIELDS}  # fmt: skip
    out = {}
    for f in sorted(fields):
        fr = capture.fields.get(f)
        if fr is not None and fr.status != MISSING and fr.value not in (None, ""):
            out[f] = fr.value
    return out


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


def fixed_next_time(by_supplier: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Wrong this time, right next time: of the fields AP corrected on a supplier's invoice (reviewed, not the clerk's
    slip), how many the supplier's next invoice read right (where it prints the field too). Per field as well."""
    fields: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for rs in by_supplier.values():
        rs = sorted(rs, key=lambda r: r["index"])
        for now, nxt in zip(rs, rs[1:], strict=False):
            for f in now["corrections"]:
                if f in nxt.get("present_fields", []):
                    fields[f][0] += 1
                    fields[f][1] += f not in nxt["wrong_fields"]
    n, fixed = sum(v[0] for v in fields.values()), sum(v[1] for v in fields.values())
    return {"corrected": n, "fixed": fixed, "share": _ratio(fixed, n),
            "by_field": {f: {"corrected": v[0], "fixed": v[1]} for f, v in sorted(fields.items())}}  # fmt: skip


def known_values(by_supplier: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    """What AP knows of a supplier and its invoices do not print (``--ap-knows``): how often AP had to set it, by
    position, and how many of those suppliers went touchless. None when no supplier had any."""
    rows = [r for rs in by_supplier.values() for r in rs if r.get("known")]
    if not rows:
        return None
    by_position = {}
    for lo, hi in BUCKETS:
        part = [v for r in rows if lo <= r["index"] <= hi for v in r["known"].values()]
        if part:
            by_position[bucket_name(lo, hi)] = {"values": len(part), "right": _ratio(sum(part), len(part))}
    suppliers = {r["supplier"] for r in rows}
    return {
        "suppliers": len(suppliers),
        "values": sum(len(r["known"]) for r in rows),
        "set_by_ap": sum(1 for r in rows for v in r["known"].values() if not v),
        "right": _ratio(sum(v for r in rows for v in r["known"].values()), sum(len(r["known"]) for r in rows)),
        "by_position": by_position,
        "touchless_suppliers": sum(1 for sid in suppliers if any(r["turned_on"] for r in by_supplier[sid])),
        "touchless_invoices": sum(1 for sid in suppliers for r in by_supplier[sid] if r["touchless"]),
    }


def eligible_wrong_by_position(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Invoices every reader and check would have let through touchless (eligible) with a wrong field, by position,
    with the fields: the errors a touchless supplier would post."""
    out = {}
    for lo, hi in BUCKETS:
        part = [r for r in rows if lo <= r["index"] <= hi and r["eligible"] and not r["fully_correct"]]
        if part:
            fields = _count(f for r in part for f in r["wrong_fields"])
            out[bucket_name(lo, hi)] = {"invoices": len(part), "fields": dict(sorted(fields.items()))}
    return out


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for x in items:
        out[x] += 1
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
        "fixed_next_time": fixed_next_time(by_supplier),
        "known": known_values(by_supplier),
        "eligible_wrong": eligible_wrong_by_position(rows),
        "bulk_approved": sum(1 for r in rows if r.get("bulk")),
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


def _wrong_section(w: dict[str, Any] | None, after: int = 0) -> list[str]:
    if not w:
        return []
    when = (f"once {after} more invoice{'s' if after > 1 else ''} had been approved (and learned from)" if after
            else "before the next invoice arrived")  # fmt: skip
    return [
        "## A wrong approval, then its withdrawal\n",
        f"On each supplier's invoice {w['at']} the clerk taught the invoice number on the invoice date's words and "
        f"approved; the approval was withdrawn (reopened) {when}.\n",
        _table(
            ["invoices", "count", "accuracy", "invoice numbers wrong"],
            [
                [k, v["invoices"], _pct(v["accuracy"]), v["invoice_number_wrong"]]
                for k, v in w.items()
                if isinstance(v, dict)
            ],
        ),  # fmt: skip
    ]


def _known_section(k: dict[str, Any] | None) -> list[str]:
    if not k:
        return []
    return [
        "## What AP knows and the invoice does not print\n",
        f"{k['suppliers']} suppliers whose terms (and for some, US dollars) AP sets: of {k['values']} values, AP had "
        f"to set **{k['set_by_ap']}**; proposed right {_pct(k['right'])}. Of those suppliers, "
        f"{k['touchless_suppliers']} went touchless ({k['touchless_invoices']} invoices).\n",
        _table(["invoice #", "values", "proposed right"],
               [[b, v["values"], _pct(v["right"])] for b, v in k["by_position"].items()]),
        "",
    ]  # fmt: skip


def _fixed_section(f: dict[str, Any] | None) -> list[str]:
    if not f:
        return []
    return [
        "## Wrong this time, right next time\n",
        f"Of the fields AP corrected on an invoice, **{f['fixed']} of {f['corrected']} ({_pct(f['share'])})** were "
        "read right on the supplier's next invoice.\n",
        _table(["field", "corrected", "right next time"],
               [[k, v["corrected"], v["fixed"]] for k, v in f["by_field"].items()]) if f["by_field"] else "",
        "",
    ]  # fmt: skip


def _eligible_wrong_section(e: dict[str, Any] | None) -> list[str]:
    head = ["## Eligible but wrong\n", "Invoices that would have gone touchless (every printed field verified, every "
            "check passed) with a field read wrong, by position:\n"]  # fmt: skip
    if not e:
        return [*head, "None.\n"]
    return [*head, _table(["invoice #", "invoices", "fields wrong"],
                          [[b, m["invoices"], ", ".join(f"{k} {v}" for k, v in m["fields"].items())]
                           for b, m in e.items()]), ""]  # fmt: skip


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
        f"lower bound on header-field accuracy since the last correction "
        f"({pol.get('window_lower_bound', pol['min_lower_bound']):.0%} over the last {pol['window']}), the last "
        f"{pol['clean_streak']} without a correction; "
        f"{pol['audit_rate']:.0%} audit sample. Touchless processing is on: a supplier goes touchless by itself as "
        "soon as it meets the bar.\n",
        ("Suppliers are in the vendor master (name and GST/HST number checked). " if cfg["vendor_master"] else "")
        + (
            f"**What-if:** a failed {', '.join(cfg['ignore_checks'])} check does not hold an invoice back.\n"
            if cfg["ignore_checks"]
            else "\n"
        ),
        *_wrong_section(s.get("wrong_approval"), cfg.get("withdraw_after") or 0),
        *(
            _fixed_section(s.get("fixed_next_time"))
            + _known_section(s.get("known"))
            + _eligible_wrong_section(s.get("eligible_wrong"))
        ),
        *(
            [
                f"{s['bulk_approved']} invoices were approved in bulk (nobody opened them; what was proposed was "
                "approved, right or wrong).\n"
            ]
            if cfg.get("bulk")
            else []
        ),
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
    withdraw_after: int = 0,
    bulk: float = 0.0,
    ap_knows_share: float = 0.0,
) -> dict[str, Any]:
    """``ap_knows_share``: the share of suppliers whose payment terms (and for some, US dollars) AP knows and sets,
    their invoices not printing them (D-121). ``wrong_at``: the invoice of each supplier approved with the wrong words
    taught, then withdrawn (0: none), once
    ``withdraw_after`` more invoices were approved (0: before the next one arrives). ``bulk``: the share of invoices
    (after a supplier's first) approved in bulk, untouched, when bulk approval would take them."""
    from ap_coder.capture.supplier import AutonomyPolicy

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    pol = AutonomyPolicy.from_dict(policy)
    t0 = time.perf_counter()
    jobs = [
        (
            str(out),
            seed,
            s,
            invoices,
            scanned,
            pol.to_dict(),
            vendor_master,
            tuple(ignore_checks),
            wrong_at,
            withdraw_after,
            bulk,
            ap_knows_share,
        )
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
            **({"wrong_approval_at": wrong_at, "withdraw_after": withdraw_after} if wrong_at else {}),
            **({"bulk": bulk} if bulk else {}),
            **({"ap_knows": ap_knows_share} if ap_knows_share else {}),
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
