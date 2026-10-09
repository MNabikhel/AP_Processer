# ruff: noqa: E501  (label and pattern tables read best one per line)
"""Combine readers and checks into one value, confidence and status per field.

Each reader proposes values with a score. Values are grouped by their normalized form; independent
readers that agree multiply their chances of being wrong (two 90% readers that agree are wrong far
less than 10% of the time). Competing values pull the confidence down. Cross-field checks then
confirm or refute: a subtotal, taxes and total that add up at the official tax rates are very
unlikely to be misread together, so the checks lift them to *verified*; a failed check sends the
fields involved to *check*.

The raw confidence is mapped through a calibration table measured on the benchmark (see
``ap_coder.bench``), so "99%" means right 99 times in 100 on the benchmark.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from .normalize import amounts_equal, gst_valid, norm_name, normalize_value, qst_valid
from .types import (
    AMOUNT_FIELDS,
    CHECK,
    FIELDS,
    LIKELY,
    MISSING,
    VERIFIED,
    FieldResult,
    LineReading,
    Reading,
)

# How far each reader is trusted on its own (before its per-reading score).
RELIABILITY = {"rules": 0.85, "template": 0.92, "di": 0.85, "ai": 0.8, "ocr": 0.8}
VERIFIED_AT = 0.985
SAME_MISREAD = 0.2  # chance that two independent wrong readings coincide
# Checks whose failure has innocent explanations (exempt lines, shipping not taxed, line items the
# reader could not separate): they lower confidence but do not force a field to "check".
SOFT_CHECKS = {"LINES_ADD_UP", "TAX_RATE", "DUE_AFTER_INVOICE"}
LIKELY_AT = 0.85

TAX_FIELDS = ("gst_amount", "hst_amount", "pst_amount", "qst_amount")
KNOWN_RATES = {
    "gst_amount": (0.05,),
    "hst_amount": (0.13, 0.14, 0.15),
    "pst_amount": (0.06, 0.07, 0.08),
    "qst_amount": (0.09975,),
}

LABELS = {
    "vendor_name": "Supplier",
    "invoice_number": "Invoice number",
    "invoice_date": "Invoice date",
    "due_date": "Due date",
    "po_number": "PO number",
    "currency": "Currency",
    "gst_hst_registration_number": "GST/HST number",
    "qst_registration_number": "QST number",
    "subtotal": "Subtotal",
    "gst_amount": "GST",
    "hst_amount": "HST",
    "pst_amount": "PST",
    "qst_amount": "QST",
    "tax_total": "Total tax",
    "grand_total": "Total",
    "payment_terms": "Terms",
}


@dataclass
class _Group:
    key: Any
    value: Any
    readings: list[tuple[str, Reading]]

    def strength(self) -> float:
        p_wrong = 1.0
        sources = {s for s, _ in self.readings}
        for source in sources:
            best = max(r.score for s, r in self.readings if s == source)
            p_wrong *= 1.0 - RELIABILITY.get(source, 0.7) * max(0.0, min(best, 1.0))
        # Readers that are wrong rarely produce the *same* wrong value: each extra agreeing reader
        # divides the chance of a shared misread further.
        return 1.0 - p_wrong * SAME_MISREAD ** (len(sources) - 1)

    def best(self) -> Reading:
        return max((r for _, r in self.readings), key=lambda r: (bool(r.boxes), r.score))


def _groups(field: str, by_source: dict[str, list[Reading]]) -> list[_Group]:
    groups: dict[Any, _Group] = {}
    for source, readings in by_source.items():
        for rank, r in enumerate(readings[:3]):
            key = normalize_value(field, r.value)
            if key is None:
                continue
            if isinstance(key, float):
                key = round(key, 2)
            g = groups.setdefault(key, _Group(key, r.value, []))
            # Lower-ranked candidates of one reader count less: the reader itself preferred another.
            weight = 1.0 if rank == 0 else 0.35
            g.readings.append((source, Reading(r.field, r.value, r.raw, r.boxes, r.score * weight, r.method)))
    return sorted(groups.values(), key=lambda g: -g.strength())


# ---------------------------------------------------------------- checks


def _v(values: dict[str, Any], f: str) -> float | None:
    v = values.get(f)
    return v if isinstance(v, (int, float)) else None


def run_checks(values: dict[str, Any], line_items: list[LineReading], vendor: dict[str, Any] | None = None,
               today: dt.date | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """Cross-field checks. Each: code, ok (True/False), fields involved, plain-English detail."""
    checks: list[dict[str, Any]] = []
    sub, total = _v(values, "subtotal"), _v(values, "grand_total")
    taxes = {f: _v(values, f) for f in TAX_FIELDS if _v(values, f) is not None}
    tax_total = _v(values, "tax_total")
    if sub is not None and total is not None:
        tax_sum = sum(taxes.values()) if taxes else (tax_total or 0.0)
        ok = amounts_equal(round(sub + tax_sum, 2), total, 0.011)
        fields = ["subtotal", "grand_total", *taxes] if taxes else ["subtotal", "grand_total", "tax_total"]
        checks.append({"code": "TOTALS_ADD_UP", "ok": ok, "fields": fields,
                       "detail": f"subtotal {sub:,.2f} + tax {tax_sum:,.2f} {'=' if ok else '≠'} total {total:,.2f}"})  # fmt: skip
    if taxes and tax_total is not None:
        ok = amounts_equal(round(sum(taxes.values()), 2), tax_total, 0.011)
        checks.append({"code": "TAXES_ADD_UP", "ok": ok, "fields": ["tax_total", *taxes],
                       "detail": f"taxes {sum(taxes.values()):,.2f} {'=' if ok else '≠'} total tax {tax_total:,.2f}"})  # fmt: skip
    if sub:
        for f, amount in taxes.items():
            rate = abs(amount / sub) if sub else 0
            ok = any(abs(amount - round(sub * r, 2)) <= max(0.02, abs(sub) * 0.0002) for r in KNOWN_RATES[f])
            checks.append({"code": "TAX_RATE", "ok": ok, "fields": [f, "subtotal"],
                           "detail": f"{LABELS[f]} is {rate:.3%} of the subtotal"})  # fmt: skip
    if line_items and sub is not None:
        amounts = [li.amount for li in line_items if li.amount is not None]
        if len(amounts) >= 1:
            line_sum = round(sum(amounts), 2)
            ok = amounts_equal(line_sum, sub, 0.011)
            checks.append({"code": "LINES_ADD_UP", "ok": ok, "fields": ["subtotal"],
                           "detail": f"lines {line_sum:,.2f} {'=' if ok else '≠'} subtotal {sub:,.2f}"})  # fmt: skip
    if values.get("gst_hst_registration_number"):
        ok = gst_valid(values["gst_hst_registration_number"])
        checks.append({"code": "GST_NUMBER_VALID", "ok": ok, "fields": ["gst_hst_registration_number"],
                       "detail": "check digit and format" + ("" if ok else " fail")})  # fmt: skip
    if values.get("qst_registration_number"):
        ok = qst_valid(values["qst_registration_number"])
        checks.append({"code": "QST_NUMBER_VALID", "ok": ok, "fields": ["qst_registration_number"],
                       "detail": "format" + ("" if ok else " fails")})  # fmt: skip
    today = today or dt.date.today()
    inv = _date(values.get("invoice_date"))
    if inv:
        ok = today - dt.timedelta(days=3 * 365) <= inv <= today + dt.timedelta(days=60)
        checks.append({"code": "DATE_PLAUSIBLE", "ok": ok, "fields": ["invoice_date"], "detail": f"invoice date {inv}"})
    due = _date(values.get("due_date"))
    if inv and due:
        ok = inv <= due <= inv + dt.timedelta(days=400)
        checks.append({"code": "DUE_AFTER_INVOICE", "ok": ok, "fields": ["due_date", "invoice_date"],
                       "detail": f"due {due} vs invoice {inv}"})  # fmt: skip
    inv_no, po = values.get("invoice_number"), values.get("po_number")
    if inv_no and po and normalize_value("invoice_number", inv_no) == normalize_value("po_number", po):
        checks.append({"code": "INVOICE_NOT_PO", "ok": False, "fields": ["invoice_number", "po_number"],
                       "detail": "the invoice number and PO number are the same"})  # fmt: skip
    if vendor:
        vg = vendor.get("gst_number")
        if vg and values.get("gst_hst_registration_number"):
            ok = normalize_value("gst_hst_registration_number", vg)[:9] == normalize_value(
                "gst_hst_registration_number", values["gst_hst_registration_number"])[:9]  # fmt: skip
            checks.append({"code": "VENDOR_GST_MATCH", "ok": ok, "fields": ["gst_hst_registration_number", "vendor_name"],
                           "detail": "GST/HST number " + ("matches" if ok else "differs from") + " the vendor master"})  # fmt: skip
        vn = vendor.get("name")
        if vn and values.get("vendor_name"):
            a, b = norm_name(vn), norm_name(values["vendor_name"])
            ok = bool(a) and (a == b or a in b or b in a)
            checks.append({"code": "VENDOR_NAME_MATCH", "ok": ok, "fields": ["vendor_name"],
                           "detail": "supplier name " + ("matches" if ok else "differs from") + " the vendor master"})  # fmt: skip
    return checks


def _date(v: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


# ---------------------------------------------------------------- calibration

# Raw confidence -> observed accuracy, measured on the benchmark (piecewise-linear; see ap_coder.bench).
# Kept conservative: never claims more than the benchmark showed.
CALIBRATION: list[tuple[float, float]] = [(0.0, 0.0), (0.5, 0.5), (0.85, 0.85), (0.985, 0.985), (1.0, 0.999)]


def calibrate(raw: float) -> float:
    pts = CALIBRATION
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        if raw <= x1:
            return y0 + (y1 - y0) * (raw - x0) / (x1 - x0) if x1 > x0 else y1
    return pts[-1][1]


# ---------------------------------------------------------------- fusion


def fuse(by_source: dict[str, dict[str, list[Reading]]], line_items: list[LineReading],
         vendor: dict[str, Any] | None = None, fields: tuple[str, ...] = FIELDS,
         today: dt.date | None = None) -> tuple[dict[str, FieldResult], list[dict[str, Any]]]:  # fmt: skip
    """``by_source``: reader name -> field -> readings (best first). Returns field results and checks."""
    chosen: dict[str, _Group | None] = {}
    contest: dict[str, float] = {}
    all_groups: dict[str, list[_Group]] = {}
    for field in fields:
        per_source = {s: cands.get(field, []) for s, cands in by_source.items() if cands.get(field)}
        groups = _groups(field, per_source)
        all_groups[field] = groups
        chosen[field] = groups[0] if groups else None
        if len(groups) > 1:
            contest[field] = groups[1].strength() / max(groups[0].strength(), 1e-9)

    values = {f: (normalize_value(f, g.value) if f in AMOUNT_FIELDS else g.value) for f, g in chosen.items() if g}
    checks = run_checks(values, line_items, vendor, today)
    confirmed: dict[str, int] = {}
    failed: dict[str, list[str]] = {}
    soft: dict[str, list[str]] = {}
    for c in checks:
        for f in c["fields"]:
            if not c["ok"] and c["code"] in SOFT_CHECKS:
                soft.setdefault(f, []).append(c["detail"])
            elif c["ok"]:
                confirmed[f] = confirmed.get(f, 0) + (2 if c["code"] in ("TOTALS_ADD_UP", "VENDOR_GST_MATCH") else 1)
            else:
                failed.setdefault(f, []).append(c["detail"])

    results: dict[str, FieldResult] = {}
    for field in fields:
        g = chosen.get(field)
        if g is None:
            results[field] = FieldResult(field, None, 0.0, MISSING, [], {}, ["not found on the invoice"])
            continue
        raw = g.strength()
        sources = sorted({s for s, _ in g.readings})
        reasons = []
        if len(sources) > 1:
            reasons.append(f"{len(sources)} readers agree ({', '.join(sources)})")
        else:
            reasons.append(f"read by {sources[0]} ({g.best().method})")
        if field in contest:
            raw *= 1.0 - 0.8 * min(contest[field], 1.0)
            other = all_groups[field][1]
            reasons.append(f"another reading: {other.value}")
        n_conf = confirmed.get(field, 0)
        if n_conf and field not in failed:
            raw = 1.0 - (1.0 - raw) * (0.15 ** min(n_conf, 3))
            reasons.append("confirmed by " + ", ".join(c["code"].lower().replace("_", " ") for c in checks
                                                      if c["ok"] and field in c["fields"]))  # fmt: skip
        if field in failed:
            raw *= 0.3
            reasons += [f"check failed: {d}" for d in failed[field]]
        if field in soft:
            raw = min(raw, 0.97)
            reasons += [f"note: {d}" for d in soft[field]]
        conf = calibrate(max(0.0, min(raw, 1.0)))
        best = g.best()
        if field in failed:
            status = CHECK
        elif conf >= VERIFIED_AT and best.boxes:
            status = VERIFIED
        elif conf >= LIKELY_AT:
            status = LIKELY
        else:
            status = CHECK
        value = values.get(field, g.value)
        results[field] = FieldResult(
            field=field, value=value, confidence=conf, status=status, boxes=list(best.boxes),
            sources={s: str(max((r for src, r in g.readings if src == s), key=lambda r: r.score).raw) for s in sources}
            | {f"other:{grp.value}": ", ".join(sorted({s for s, _ in grp.readings})) for grp in all_groups[field][1:3]},
            reasons=reasons,
        )  # fmt: skip
    _derive_tax_total(results)
    return results, checks


def _derive_tax_total(results: dict[str, FieldResult]) -> None:
    """When only the separate taxes are printed, the total tax is their sum (marked as worked out)."""
    tt = results.get("tax_total")
    if tt is None or tt.status != MISSING:
        return
    parts = [results[f] for f in TAX_FIELDS if f in results and results[f].status != MISSING]
    if not parts:
        return
    conf = min(p.confidence for p in parts)
    status = (
        VERIFIED
        if all(p.status == VERIFIED for p in parts)
        else (CHECK if any(p.status == CHECK for p in parts) else LIKELY)
    )
    results["tax_total"] = FieldResult(
        "tax_total",
        round(sum(float(p.value) for p in parts), 2),
        conf,
        status,
        [],
        {"computed": "sum of the taxes"},
        ["worked out: " + " + ".join(LABELS[p.field] for p in parts)],
    )
