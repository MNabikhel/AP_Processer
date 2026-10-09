"""Scoring a capture result against the generator's truth, and the benchmark's summary numbers."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from ap_coder.capture.types import (
    AMOUNT_FIELDS,
    DATE_FIELDS,
    FIELDS,
    LIKELY,
    MISSING,
    STATUSES,
    VERIFIED,
    Box,
    CaptureResult,
)

ID_FIELDS = ("invoice_number", "po_number", "gst_hst_registration_number", "qst_registration_number")
AMOUNT_TOLERANCE = 0.005
N_BINS = 10


# --- value comparison -------------------------------------------------------------------------


def norm_id(v: Any) -> str:
    """Identifiers compare without case, spaces or dashes."""
    return re.sub(r"[\s\-‐-―]", "", str(v)).upper()


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def norm_text(v: Any) -> str:
    """Names: no case, accents or punctuation."""
    return re.sub(r"[^a-z0-9]", "", _ascii(str(v)).lower())


def _amount(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _terms_key(v: Any) -> tuple[tuple[str, ...], bool]:
    s = _ascii(str(v)).lower()
    return tuple(re.findall(r"\d+", s)), bool(re.search(r"receipt|reception", s))


def values_match(field: str, truth: Any, got: Any) -> bool:
    if got is None or got == "":
        return False
    if field in AMOUNT_FIELDS:
        g, t = _amount(got), _amount(truth)
        return g is not None and t is not None and abs(g - t) <= AMOUNT_TOLERANCE + 1e-9
    if field in DATE_FIELDS:
        return str(got)[:10] == str(truth)
    if field == "currency":
        return str(got).strip().upper() == str(truth).upper()
    if field in ID_FIELDS:
        return norm_id(got) == norm_id(truth)
    if field == "payment_terms":
        if norm_text(got) == norm_text(truth):
            return True
        gd, gr = _terms_key(got)
        td, tr = _terms_key(truth)
        return (bool(gd) or gr) and (gd, gr) == (td, tr)
    return norm_text(got) == norm_text(truth)


def overlaps(a: Box, b: Box) -> bool:
    return a.page == b.page and min(a.x1, b.x1) > max(a.x0, b.x0) and min(a.y1, b.y1) > max(a.y0, b.y0)


def truth_boxes(entry: dict[str, Any]) -> list[Box]:
    return [Box.from_list(entry["box"])] + [Box.from_list(a["box"]) for a in entry.get("also") or []]


# --- per-case scoring -------------------------------------------------------------------------


def score_case(truth: dict[str, Any], result: CaptureResult | None) -> list[dict[str, Any]]:
    """One record per field in FIELDS. A field the invoice does not print is scored only if the
    reader reports something: a value equal to the `implied` one is ignored, anything else is
    'spurious' (a wrong answer)."""
    fields = truth.get("fields") or {}
    implied = truth.get("implied") or {}
    out = []
    for f in FIELDS:
        fr = result.fields.get(f) if result else None
        got = fr.value if fr else None
        status = fr.status if fr else MISSING
        conf = float(fr.confidence) if fr else 0.0
        reported = status != MISSING and got not in (None, "")
        rec: dict[str, Any] = {
            "field": f,
            "got": got,
            "status": status,
            "confidence": conf,
            "reported": reported,
            "reasons": list(fr.reasons) if fr else [],
            "evidence": fr.evidence if fr else "",
        }
        if f in fields:
            entry = fields[f]
            correct = reported and values_match(f, entry["value"], got)
            hit = bool(correct and fr and any(overlaps(rb, tb) for rb in fr.boxes for tb in truth_boxes(entry)))
            rec.update(present=True, truth=entry["value"], correct=correct, box_hit=hit, spurious=False, scored=True)
        else:
            ok_implied = reported and f in implied and values_match(f, implied[f], got)
            rec.update(
                present=False,
                truth=implied.get(f),
                correct=False,
                box_hit=False,
                spurious=reported and not ok_implied,
                scored=reported and not ok_implied,
            )
        out.append(rec)
    return out


# --- aggregation ------------------------------------------------------------------------------


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def field_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics over records of one field (or all fields)."""
    present = [r for r in records if r["present"]]
    n = len(present)
    correct = sum(r["correct"] for r in present)
    box_hits = sum(r["box_hit"] for r in present)
    scored = [r for r in records if r["scored"]]  # present, or spurious
    ver = [r for r in scored if r["status"] == VERIFIED]
    vl = [r for r in scored if r["status"] in (VERIFIED, LIKELY)]
    statuses = {s: sum(1 for r in present if r["status"] == s) for s in STATUSES}
    return {
        "n": n,
        "accuracy": _ratio(correct, n),
        "box_hit": _ratio(box_hits, correct),
        "verified_precision": _ratio(sum(r["correct"] for r in ver), len(ver)),
        "verified_coverage": _ratio(sum(1 for r in present if r["status"] == VERIFIED), n),
        "verified_n": len(ver),
        "vl_precision": _ratio(sum(r["correct"] for r in vl), len(vl)),
        "vl_coverage": _ratio(sum(1 for r in present if r["status"] in (VERIFIED, LIKELY)), n),
        "spurious": sum(1 for r in records if r["spurious"]),
        "statuses": statuses,
    }


def calibration(records: list[dict[str, Any]], bins: int = N_BINS) -> dict[str, Any]:
    """Stated confidence against observed accuracy, over every reported value that is scored."""
    rows = [{"lo": i / bins, "hi": (i + 1) / bins, "n": 0, "conf": 0.0, "acc": 0.0} for i in range(bins)]
    pts = [
        (min(max(r["confidence"], 0.0), 1.0), 1.0 if r["correct"] else 0.0)
        for r in records
        if r["scored"] and r["reported"]
    ]
    for c, a in pts:
        row = rows[min(int(c * bins), bins - 1)]
        row["n"] += 1
        row["conf"] += c
        row["acc"] += a
    total = len(pts)
    ece = 0.0
    for row in rows:
        if row["n"]:
            row["conf"] = round(row["conf"] / row["n"], 4)
            row["acc"] = round(row["acc"] / row["n"], 4)
            ece += row["n"] / total * abs(row["acc"] - row["conf"])
        else:
            row["conf"] = row["acc"] = None
    return {"bins": rows, "ece": round(ece, 4) if total else None, "n": total}


def invoice_metrics(per_case: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> dict[str, Any]:
    """Whole-invoice numbers: all fields right; touchless (every printed field verified, nothing
    spurious) and how many of those touchless invoices had a wrong field."""
    n = len(per_case)
    fully = touchless = touchless_wrong = no_silent = 0
    for _truth, recs in per_case:
        present = [r for r in recs if r["present"]]
        spurious = [r for r in recs if r["spurious"]]
        ok = all(r["correct"] for r in present) and not spurious
        fully += ok
        # Every value is right, or the field that is not is flagged for a person (check / missing):
        # nothing wrong is presented as verified or likely.
        wrong = [r for r in present if not r["correct"]] + spurious
        no_silent += all(r["status"] not in (VERIFIED, LIKELY) for r in wrong)
        if (
            present
            and all(r["status"] == VERIFIED for r in present)
            and not any(r["status"] == VERIFIED for r in spurious)
        ):
            touchless += 1
            touchless_wrong += not ok
    return {
        "n": n,
        "fully_correct": _ratio(fully, n),
        "no_silent_error": _ratio(no_silent, n),
        "touchless": _ratio(touchless, n),
        "touchless_n": touchless,
        "touchless_wrong": touchless_wrong,
        "touchless_precision": _ratio(touchless - touchless_wrong, touchless),
    }


def line_item_metrics(truth: dict[str, Any], result: CaptureResult | None) -> tuple[int, int, int]:
    """(truth lines, lines matched by amount and description, lines the reader returned)."""
    want = list(truth.get("line_items") or [])
    got = list(result.line_items) if result else []
    used: set[int] = set()
    matched = 0
    for t in want:
        for i, g in enumerate(got):
            if i in used or g.amount is None:
                continue
            if abs(float(g.amount) - t["amount"]) <= AMOUNT_TOLERANCE and (
                norm_text(t["description"])[:12] in norm_text(g.description)
                or norm_text(g.description)[:12] in norm_text(t["description"])
            ):
                used.add(i)
                matched += 1
                break
    return len(want), matched, len(got)


def summarize(per_case: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> dict[str, Any]:
    by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_recs = []
    for _t, recs in per_case:
        for r in recs:
            by_field[r["field"]].append(r)
            all_recs.append(r)
    return {
        "fields": {f: field_metrics(by_field[f]) for f in FIELDS},
        "overall": field_metrics(all_recs),
        "invoices": invoice_metrics(per_case),
        "calibration": calibration(all_recs),
    }
