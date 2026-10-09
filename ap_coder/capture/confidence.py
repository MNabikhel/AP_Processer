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
import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
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
RELIABILITY = {"rules": 0.85, "template": 0.92, "di": 0.85, "ai": 0.8, "ocr": 0.8, "ocr2": 0.8}
VERIFIED_AT = 0.985
SAME_MISREAD = 0.2  # chance that two independent wrong readings coincide
# Checks whose failure has innocent explanations (exempt lines, shipping not taxed, line items the
# reader could not separate): they lower confidence but do not force a field to "check".
SOFT_CHECKS = {"LINES_ADD_UP", "TAX_RATE", "DUE_AFTER_INVOICE", "DUE_MATCHES_TERMS"}
# Checks that only show a value is reasonable, not that it is the printed one: passing them confirms nothing.
WEAK_CHECKS = {"DATE_PLAUSIBLE", "DUE_AFTER_INVOICE"}
LIKELY_AT = 0.85
SINGLE_READER_CAP = 0.97  # highest confidence for a value only one reader found and no check confirms

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
    "other_charges": "Freight / other charges",
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
            if field == "vendor_name" and len(str(r.value)) > len(str(g.value)):
                g.value = r.value  # names that agree once legal suffixes are ignored: the fuller one as printed
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
    charges = _v(values, "other_charges")
    if sub is not None and total is not None:
        tax_sum = sum(taxes.values()) if taxes else (tax_total or 0.0)
        ok = amounts_equal(round(sub + (charges or 0.0) + tax_sum, 2), total, 0.011)
        fields = ["subtotal", "grand_total", *taxes] if taxes else ["subtotal", "grand_total", "tax_total"]
        if charges is not None:
            fields.append("other_charges")
        extra = f" + charges {charges:,.2f}" if charges is not None else ""
        checks.append({"code": "TOTALS_ADD_UP", "ok": ok, "fields": fields,
                       "detail": f"subtotal {sub:,.2f}{extra} + tax {tax_sum:,.2f} {'=' if ok else '≠'} total {total:,.2f}"})  # fmt: skip
    if taxes and tax_total is not None:
        ok = amounts_equal(round(sum(taxes.values()), 2), tax_total, 0.011)
        checks.append({"code": "TAXES_ADD_UP", "ok": ok, "fields": ["tax_total", *taxes],
                       "detail": f"taxes {sum(taxes.values()):,.2f} {'=' if ok else '≠'} total tax {tax_total:,.2f}"})  # fmt: skip
    if sub:
        for f, amount in taxes.items():
            rate = abs(amount / sub) if sub else 0
            bases = {sub, sub + (charges or 0.0)}  # taxed with or without the freight
            ok = any(abs(amount - round(b * r, 2)) <= max(0.02, abs(b) * 0.0002) for r in KNOWN_RATES[f] for b in bases)
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
        net = re.search(r"net\s*(\d{1,3})", str(values.get("payment_terms") or ""), re.I)
        if net:
            ok = (due - inv).days == int(net.group(1))
            checks.append({"code": "DUE_MATCHES_TERMS", "ok": ok, "fields": ["due_date", "invoice_date", "payment_terms"],
                           "detail": f"invoice date + {net.group(1)} days {'=' if ok else '≠'} due date"})  # fmt: skip
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
CALIBRATION_FILE = Path(__file__).with_name("calibration.json")
MIN_EVIDENCE = 40  # cases of one evidence pattern before its measured accuracy replaces the formula


def evidence_key(
    field: str,
    sources: list[str],
    method: str,
    *,
    layout_source: str = "text",
    contested: bool = False,
    confirmed: bool = False,
    soft: bool = False,
) -> str:
    """field|readers|how it was read|flags: the unit the calibration measures. Coarse on purpose, so
    each pattern is seen often enough on the benchmark to measure."""
    family = re.sub(r"[-+](ambiguous|adds-up|terms|dates|received)", "", method).split("+")[0]
    flags = [f for f, on in (("adds-up", "adds-up" in method), ("confirmed", confirmed), ("contested", contested),
                             ("soft", soft), ("ambiguous", "ambiguous" in method),
                             ("received", "+received" in method)) if on]  # fmt: skip
    page = "scan" if layout_source in ("ocr", "di", "mixed") else "text"
    return "|".join([field, "+".join(sorted(sources)), family, page, ",".join(flags)])


@lru_cache(maxsize=1)
def calibration_table() -> dict[str, tuple[int, int]]:
    """{evidence: (cases, correct)} measured by ``python -m ap_coder.bench calibrate``."""
    try:
        data = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: (int(v[0]), int(v[1])) for k, v in (data.get("evidence") or {}).items()}


def wilson_lower(correct: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = correct / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / den)


def calibrate(raw: float, evidence: str = "") -> float:
    """The confidence a value deserves. When the benchmark has measured this evidence pattern often
    enough, it is the lower 95% bound of that pattern's accuracy (so "verified" at 98.5% means the
    pattern was right at least 98.5% of the time, with room for chance); otherwise the readers' own
    combined score."""
    seen = calibration_table().get(evidence) if evidence else None
    if seen and seen[0] >= MIN_EVIDENCE:
        return round(wilson_lower(seen[1], seen[0]), 4)
    return raw


# ---------------------------------------------------------------- fusion


def fuse(by_source: dict[str, dict[str, list[Reading]]], line_items: list[LineReading],
         vendor: dict[str, Any] | None = None, fields: tuple[str, ...] = FIELDS,
         today: dt.date | None = None, layout_source: str = "text") -> tuple[dict[str, FieldResult], list[dict[str, Any]]]:  # fmt: skip
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

    _totals_that_add_up(chosen, all_groups, contest)
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
                if c["code"] not in WEAK_CHECKS:
                    confirmed[f] = confirmed.get(f, 0) + (
                        2 if c["code"] in ("TOTALS_ADD_UP", "VENDOR_GST_MATCH") else 1
                    )
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
                                                      if c["ok"] and c["code"] not in WEAK_CHECKS and field in c["fields"]))  # fmt: skip
        if field in failed:
            raw *= 0.3
            reasons += [f"check failed: {d}" for d in failed[field]]
        if field in soft:
            raw = min(raw, 0.97)
            reasons += [f"note: {d}" for d in soft[field]]
        best = g.best()
        evidence = evidence_key(field, sources, best.method, layout_source=layout_source,
                                contested=contest.get(field, 0.0) > 0.25, confirmed=bool(n_conf) and field not in failed,
                                soft=field in soft)  # fmt: skip
        conf = calibrate(max(0.0, min(raw, 1.0)), evidence)
        # A lone reader is never verified on its own measured record (the benchmark is synthetic):
        # verified also needs a second reader or a check to agree. The second OCR read of a scan
        # shares the first one's blind spots (a word OCR never saw), so with the rule reader it
        # counts as one reader.
        independent = {"rules" if s == "ocr" else s for s in sources}
        texts = {" ".join(str(r.value).split()).casefold() for _, r in g.readings}
        if field == "vendor_name" and len(texts) > 1:
            independent = {"one"}  # they agree on the supplier, not on how its name is printed
            reasons.append(
                "readers differ on the exact name: " + " / ".join(sorted({str(r.value) for _, r in g.readings}))
            )
        if len(independent) < 2 and not (n_conf and field not in failed):
            conf = min(conf, SINGLE_READER_CAP)
        ambiguous = all("ambiguous" in r.method for _, r in g.readings)
        if ambiguous:
            conf = min(conf, 0.6)
            reasons.append("the date reads both day/month and month/day")
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
            reasons=reasons, evidence=evidence,
        )  # fmt: skip
    _derive_tax_total(results)
    _vendor_master_name(results, checks, vendor)
    return results, checks


def _vendor_master_name(
    results: dict[str, FieldResult], checks: list[dict[str, Any]], vendor: dict[str, Any] | None
) -> None:
    """Once the vendor master confirms the supplier by both its GST/HST number and its name, the
    supplier is the master record: show its name as the master spells it (what the ERP posts to),
    not the page's text with OCR's glued words or a logo's initials."""
    fr = results.get("vendor_name")
    if fr is None or not vendor or not vendor.get("name") or fr.status == MISSING:
        return
    passed = {c["code"] for c in checks if c["ok"]}
    if {"VENDOR_NAME_MATCH", "VENDOR_GST_MATCH"} <= passed and fr.value != vendor["name"]:
        fr.sources = {"vendor master": vendor["name"], **fr.sources}
        fr.reasons.append(f"name as in the vendor master (printed: {fr.value})")
        fr.value = vendor["name"]


_TOTAL_PARTS = ("subtotal", "other_charges", *TAX_FIELDS)


def _totals_that_add_up(chosen: dict[str, Any], all_groups: dict[str, list[Any]], contest: dict[str, float]) -> None:
    """When the readers' favourite amounts do not add up but a runner-up does (one reader read the
    row above, a template after the totals block moved), take the combination that adds up: a sum
    that balances is far stronger evidence than one reader's preference. Changes ``chosen`` in place."""
    from itertools import product

    def amount(g: Any) -> float:
        v = normalize_value("subtotal", g.value) if g is not None else None
        return float(v) if v is not None else 0.0

    total_groups = all_groups.get("grand_total") or []
    if not total_groups or not all_groups.get("subtotal"):
        return
    current = [chosen.get(f) for f in _TOTAL_PARTS]
    if abs(sum(amount(g) for g in current) - amount(chosen.get("grand_total"))) <= 0.011:
        return
    options = [(all_groups.get(f) or [])[:2] + ([None] if f != "subtotal" else []) for f in _TOTAL_PARTS]
    best, best_strength = None, -1.0
    for total in total_groups[:2]:
        for parts in product(*options):
            if abs(sum(amount(g) for g in parts) - amount(total)) > 0.011:
                continue
            if any(
                g is None and chosen.get(f) is not None and f in TAX_FIELDS
                for f, g in zip(_TOTAL_PARTS, parts, strict=True)
            ):
                continue  # dropping a tax the readers agreed on is not "adding up"
            strength = total.strength() + sum(g.strength() for g in parts if g is not None)
            if strength > best_strength:
                best, best_strength = (total, parts), strength
    if best is None:
        return
    total, parts = best
    for f, g in (("grand_total", total), *zip(_TOTAL_PARTS, parts, strict=True)):
        if g is not None and g is not chosen.get(f):
            others = [o for o in all_groups[f] if o is not g]
            all_groups[f] = [g, *others]
            chosen[f] = g
            contest[f] = max(contest.get(f, 0.0), others[0].strength() / max(g.strength(), 1e-9) if others else 0.0)


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
