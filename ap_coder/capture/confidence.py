# ruff: noqa: E501  (label and pattern tables read best one per line)
"""Combine readers and checks into one value, confidence and status per field.

Each reader proposes values with a score. Values are grouped by their normalized form; independent
readers that agree multiply their chances of being wrong (two 90% readers that agree are wrong far
less than 10% of the time). Competing values pull the confidence down. Cross-field checks then
confirm or refute: a subtotal, taxes and total that add up at the official tax rates are very
unlikely to be misread together, so the checks lift them to *verified*; a failed check sends the
fields involved to *check*.

The raw confidence is mapped through a calibration table measured on the benchmark (see
``ap_coder.bench``), so "99%" means right 99 times in 100 on the benchmark; what AP approved on this
computer counts too (``local_evidence``), so real invoices refine the benchmark's measure.

The page reader ("vlm": a vision model's transcription of the page, read by the rule reader) is one more
independent reader: agreeing with the page's own text it can make a value *verified*; disagreeing with it,
it sends the field to *check*.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass
from dataclasses import field as _field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .normalize import amounts_equal, gst_valid, luhn_ok, norm_name, normalize_value, parse_dates, qst_valid
from .types import (
    AMOUNT_FIELDS,
    BILL_TO_READING,
    CHECK,
    DATE_FIELDS,
    EITHER_TAX,
    FIELDS,
    LIKELY,
    MISSING,
    PART_OF_TERMS,
    RIVAL_NAME,
    TAKEN_OFF,
    TWO_SUBTOTALS,
    UNMEASURED,
    UNSETTLED,
    VERIFIED,
    FieldResult,
    LineReading,
    Reading,
)

# How far each reader is trusted on its own (before its per-reading score). "vlm": the page reader.
RELIABILITY = {"rules": 0.85, "template": 0.92, "di": 0.85, "ai": 0.8, "ocr": 0.8, "ocr2": 0.8, "vlm": 0.85}
# Readers of the page itself, whose first choice the page reader's is held against.
PAGE_READERS = ("rules", "ocr", "ocr2", "template", "di")
VERIFIED_AT = 0.985
SAME_MISREAD = 0.2  # chance that two independent wrong readings coincide
# Checks whose failure has innocent explanations (exempt lines, shipping not taxed, line items the
# reader could not separate): they lower confidence but do not force a field to "check".
SOFT_CHECKS = {"LINES_ADD_UP", "TAX_RATE", "DUE_AFTER_INVOICE", "DUE_MATCHES_TERMS"}
# Checks that only show a value is reasonable, not that it is the printed one: passing them confirms nothing.
# "BN_CHECK_DIGIT": a business number printed without its RT program account passes its check digit; nine digits
# pass one time in ten by chance, so it confirms nothing (D-47) though a failure marks it down.
WEAK_CHECKS = {"DATE_PLAUSIBLE", "DUE_AFTER_INVOICE", "BN_CHECK_DIGIT", "DATE_RECENT"}
# Checks whose failure only asks for a look: the value is marked "check" (amber), never "failed" (red). An invoice
# dated more than 3 years ago is often right (a late bill, an old statement): "check the year" (B-664).
LOOK_CHECKS = {"DATE_RECENT"}
LOOK_CAP = 0.8
LIKELY_AT = 0.85
UNMEASURED_CAP = 0.8  # a value read only by wording the benchmark never prints (``UNMEASURED``), no check confirming
REGISTRATION_FIELDS = ("gst_hst_registration_number", "qst_registration_number", "pst_registration_number")
SINGLE_READER_CAP = 0.97  # highest confidence for a value only one reader found and no check confirms
# Values read with no label of their own (``reader``: a receipt's date and time, a date beside the title): checked.
UNLABELLED_METHODS = ("date-time", "title-dated", "letter-dated")
UNLABELLED_CAP = 0.8
WEAK_LABEL_METHOD = "weak-"  # a value after a weak label (``reader.WEAK_LABELS``): checked as well

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
    "pst_registration_number": "PST number",
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
    top: set[str] = _field(default_factory=set)  # sources for which this is their first choice

    def strength(self, vouched: bool = False) -> float:
        """How likely this value is right, by its readers. ``vouched``: leaving out the template's readings it does
        not vouch for (``UNSETTLED``), which agree but never decide."""
        readings = [(s, r) for s, r in self.readings if not (vouched and UNSETTLED in r.method)]
        if not readings:
            return 0.0
        p_wrong = 1.0
        sources = {s for s, _ in readings}
        for source in sources:
            best = max(r.score for s, r in readings if s == source)
            p_wrong *= 1.0 - RELIABILITY.get(source, 0.7) * _clamp(best)
        # Readers that are wrong rarely produce the *same* wrong value: each extra agreeing reader
        # divides the chance of a shared misread further. A reader that only listed the value as a
        # runner-up agrees by half: two readers' second choices (a label, the terms) often coincide.
        top = len(self.top & sources)
        extra = max(top - 1, 0) + 0.5 * (len(sources) - top) - (0.5 if top == 0 else 0.0)
        return 1.0 - p_wrong * SAME_MISREAD ** max(extra, 0.0)

    def best(self) -> Reading:
        # The page reader's value is found back on the page; another reader's reading of the same value comes
        # first, so the evidence pattern names how the page's own text was read.
        return max(self.readings, key=lambda sr: (bool(sr[1].boxes), sr[0] != "vlm", sr[1].score))[1]


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
            if rank == 0:
                g.top.add(source)
            g.readings.append((source, Reading(r.field, r.value, r.raw, r.boxes, r.score * weight, r.method)))
    return sorted(groups.values(), key=lambda g: -g.strength())


# ---------------------------------------------------------------- checks


def _v(values: dict[str, Any], f: str) -> float | None:
    v = values.get(f)
    return v if isinstance(v, (int, float)) else None


def run_checks(values: dict[str, Any], line_items: list[LineReading], vendor: dict[str, Any] | None = None,
               today: dt.date | None = None, text: str = "") -> list[dict[str, Any]]:  # fmt: skip
    """Cross-field checks. Each: code, ok (True/False), fields involved, plain-English detail. ``text``: the page's
    text, where a tip printed after the tax is looked for (it is in the total, not in the subtotal: B-1026)."""
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
        if not ok and text:  # the gap is a tip printed after the tax, untaxed (B-931): the subtotal is as printed
            from ..ai.offline_coder import _tip_after_tax

            tip = _tip_after_tax(text, round(total - sub - (charges or 0.0) - tax_sum, 2))
            if tip:
                ok, extra = True, f"{extra} + {tip[0].lower()} {tip[1]:,.2f}"
        checks.append({"code": "TOTALS_ADD_UP", "ok": ok, "fields": fields,
                       "detail": f"subtotal {sub:,.2f}{extra} + tax {tax_sum:,.2f} {'=' if ok else '≠'} total {total:,.2f}"})  # fmt: skip
    elif sub is None and total is not None and line_items:
        # No subtotal printed: lines (+ charges + taxes) that make the total prove the money. Lines that don't prove
        # nothing either way (a line missed or read wrong is common: LINES_ADD_UP is soft), so the header's total
        # and taxes are not failed for it; with nothing proven the invoice still goes to a person.
        amounts = [li.amount for li in line_items if li.amount is not None]
        if amounts:
            tax_sum = sum(taxes.values()) if taxes else (tax_total or 0.0)
            line_sum = round(sum(amounts), 2)
            if amounts_equal(round(line_sum + (charges or 0.0) + tax_sum, 2), total, 0.011):
                fields = ["grand_total", *taxes] if taxes else ["grand_total"]
                extra = f" + charges {charges:,.2f}" if charges is not None else ""
                checks.append({"code": "TOTALS_ADD_UP", "ok": True, "fields": fields,
                               "detail": f"lines {line_sum:,.2f}{extra} + tax {tax_sum:,.2f} = total {total:,.2f}"})  # fmt: skip
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
        number = values["gst_hst_registration_number"]
        if re.fullmatch(r"\d{9}", str(number)):
            # The business number printed alone ("HST # 108162333"), without its RT program account: its check digit.
            ok = luhn_ok(str(number))
            detail = "check digit (printed without its RT program number)" + ("" if ok else " fails")
        else:
            ok = gst_valid(number)
            detail = "check digit and format" + ("" if ok else " fail")
        code = "BN_CHECK_DIGIT" if re.fullmatch(r"\d{9}", str(number)) else "GST_NUMBER_VALID"
        checks.append({"code": code, "ok": ok, "fields": ["gst_hst_registration_number"], "detail": detail})
    if values.get("qst_registration_number"):
        ok = qst_valid(values["qst_registration_number"])
        checks.append({"code": "QST_NUMBER_VALID", "ok": ok, "fields": ["qst_registration_number"],
                       "detail": "format" + ("" if ok else " fails")})  # fmt: skip
    today = today or dt.date.today()
    inv = _date(values.get("invoice_date"))
    if inv:
        ok = inv <= today + dt.timedelta(days=60)  # far ahead: misread
        checks.append({"code": "DATE_PLAUSIBLE", "ok": ok, "fields": ["invoice_date"], "detail": f"invoice date {inv}"})
        if ok:  # long ago: often right, a look (LOOK_CHECKS)
            recent = today - dt.timedelta(days=3 * 365) <= inv
            checks.append({"code": "DATE_RECENT", "ok": recent, "fields": ["invoice_date"],
                           "detail": "dated more than 3 years ago: check the year"})  # fmt: skip
    due = _date(values.get("due_date"))
    if inv and due:
        ok = inv <= due <= inv + dt.timedelta(days=400)
        checks.append({"code": "DUE_AFTER_INVOICE", "ok": ok, "fields": ["due_date", "invoice_date"],
                       "detail": f"due {due} vs invoice {inv}"})  # fmt: skip
        by_terms = _due_by_terms(inv, values.get("payment_terms"))
        if by_terms:
            ok = by_terms == due
            # "Net 30 EOM", "Net 10 prox": the same due date from any day of the invoice's month, so the invoice's own
            # day is not confirmed by it.
            month_only = _counts_from_month(values.get("payment_terms"))
            fields = ["due_date", "payment_terms"] if month_only else ["due_date", "invoice_date", "payment_terms"]
            checks.append({"code": "DUE_MATCHES_TERMS", "ok": ok, "fields": fields,
                           "detail": f"invoice date + {(by_terms - inv).days} days ({values['payment_terms']}) "
                                     f"{'=' if ok else '≠'} due date"})  # fmt: skip
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
            b = norm_name(values["vendor_name"])
            # The master's name, or a name AP corrected to it that the vendor's invoices print (D-119).
            ok = any(bool(a) and (a == b or a in b or b in a)
                     for a in (norm_name(n) for n in [vn, *(vendor.get("aliases") or [])]))  # fmt: skip
            checks.append({"code": "VENDOR_NAME_MATCH", "ok": ok, "fields": ["vendor_name"],
                           "detail": "supplier name " + ("matches" if ok else "differs from") + " the vendor master"})  # fmt: skip
    return checks


def _due_by_terms(invoice_date: dt.date, terms: Any) -> dt.date | None:
    """The due date the terms give, counted as the app counts it ("Net 30 EOM" from the end of the month, "Net 10
    prox" on the 10th of the next month); None for terms with no net days, or due on receipt."""
    from ..coding.terms import parse_terms, payment

    t = parse_terms(str(terms or ""))
    if t.net_days is None or t.on_receipt:
        return None
    p = payment({"invoice_date": invoice_date.isoformat(), "payment_terms": str(terms)})
    return p.due if p.source == "terms" else None


def _counts_from_month(terms: Any) -> bool:
    from ..coding.terms import parse_terms

    t = parse_terms(str(terms or ""))
    return bool(t.eom or t.prox_day)


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


def _clamp(value: float, top: float = 1.0) -> float:
    """``value`` kept between 0 and ``top``."""
    return max(0.0, min(value, top))


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


def calibrate(raw: float, evidence: str = "", local: dict[str, tuple[int, int]] | None = None) -> float:
    """The confidence a value deserves. When the benchmark has measured this evidence pattern often
    enough, it is the lower 95% bound of that pattern's accuracy (so "verified" at 98.5% means the
    pattern was right at least 98.5% of the time, with room for chance); otherwise the readers' own
    combined score. ``local``: {evidence: (cases, right)} from what AP approved on this computer. Real
    invoices are added to the benchmark's cases of the same pattern (the synthetic ones keep their weight),
    and the bound is taken over both once they are ``MIN_EVIDENCE`` together."""
    if not evidence:
        return raw
    n, right = calibration_table().get(evidence) or (0, 0)
    mine = _counts((local or {}).get(evidence))
    if mine[0] and n + mine[0] >= MIN_EVIDENCE:
        return round(wilson_lower(right + mine[1], n + mine[0]), 4)
    if n >= MIN_EVIDENCE:
        return round(wilson_lower(right, n), 4)
    return raw


# ---------------------------------------------------------------- the supplier's record (D-89)

# How many of the supplier's approvals the readers' own confidence counts as, when its record is added to it: two, so a
# first approval moves a verified field from 98.5% to 99%, ten to 99.7%, and a record with corrections lifts nothing.
LEARNED_PRIOR = 2.0


def template_vouched(result: FieldResult) -> bool:
    """Whether the supplier's template is one of the readers behind the value, where it vouches for it (the readers
    of its evidence pattern: ``evidence_key`` leaves out the template's readings it does not vouch for)."""
    parts = result.evidence.split("|")
    return len(parts) > 1 and "template" in parts[1].split("+")


def apply_supplier_record(fields: dict[str, FieldResult], record: dict[str, Any] | None) -> None:
    """In place: each verified value the supplier's template vouched for, with how often this supplier's value of the
    field was read right (``record``: {field: (cases, right)}, its approvals as ``SupplierStats`` counts them) added to
    the readers' confidence, as ``LEARNED_PRIOR`` cases of it: (right + 2 × confidence) / (cases + 2). It only ever
    lifts (a record with corrections leaves the readers' confidence as it is), and never a value the template did not
    read, nor one to check: a first-time supplier's values keep the readers' own confidence. The status is unchanged
    (a verified value stays verified), so nothing goes touchless that would not have."""
    for name, result in fields.items():
        cases, right = _counts((record or {}).get(name))
        if not cases or result.status != VERIFIED or not template_vouched(result):
            continue
        lifted = round((right + LEARNED_PRIOR * result.confidence) / (cases + LEARNED_PRIOR), 4)
        if lifted > result.confidence:
            result.confidence = lifted
            result.reasons.append(f"read right on {right} of this supplier's last {cases} invoices")


def reading_confidence(fields: dict[str, FieldResult]) -> float:
    """How sure the reading of the invoice is: the mean confidence of the values found (D-89's Reading)."""
    found = [r.confidence for r in fields.values() if r.value not in (None, "")]
    return round(min(sum(found) / len(found), 1.0), 3) if found else 0.0


def _counts(seen: Any) -> tuple[int, int]:
    """(cases, right) from a store's count, made safe: whole numbers, 0 <= right <= cases."""
    try:
        n = max(0, int(seen[0]))
        return n, min(max(0, int(seen[1])), n)
    except (TypeError, ValueError, IndexError, KeyError):
        return 0, 0


# ---------------------------------------------------------------- fusion


def _key(field: str, value: Any) -> Any:
    """A value as readers are grouped by (amounts to the cent)."""
    key = normalize_value(field, value)
    return round(key, 2) if isinstance(key, float) else key


def _page_reader_differs(
    field: str, by_source: dict[str, dict[str, list[Reading]]]
) -> tuple[Reading, str, Reading] | None:
    """(the page reader's reading, the other reader, its reading) when the page reader's first choice for
    ``field`` and another page reader's first choice are different values."""
    vlm = (by_source.get("vlm") or {}).get(field) or []
    mine = _key(field, vlm[0].value) if vlm else None
    if mine is None:
        return None
    for source in PAGE_READERS:
        other = (by_source.get(source) or {}).get(field) or []
        theirs = _key(field, other[0].value) if other else None
        if theirs is not None and theirs != mine:
            return vlm[0], source, other[0]
    return None


def _unsettled(g: _Group | None) -> bool:
    """Read only by the template, at a spot it does not vouch for (``UNSETTLED``)."""
    return g is not None and all(UNSETTLED in r.method for _, r in g.readings)


def _vouched_first(groups: list[_Group]) -> None:
    """The template's readings it does not vouch for (``UNSETTLED``: its label not on this page, or a spot never
    settled) do not choose: the value first is the one the other readers make strongest, and those readings only add
    their agreement to it. A value only they read fills the field when nothing else is read, and otherwise stays as a
    rival: it still contests the value chosen (the layout may have changed under the template, or not). In place."""
    if not any(UNSETTLED in r.method for g in groups for _, r in g.readings):
        return
    if all(_unsettled(g) for g in groups):
        return
    groups.sort(key=lambda g: (-g.strength(vouched=True), -g.strength()))
    if len(groups) > 2:  # the strongest rival next, as the contest is measured against it
        rival = max(groups[1:], key=lambda g: g.strength())
        groups.remove(rival)
        groups.insert(1, rival)


def _whole_first_word(groups: list[_Group]) -> None:
    """One OCR engine missed the start of the name ("ipements Saint-Laurent" for "Équipements Saint-Laurent", an
    accented capital it could not see): the reading that has the whole first word goes first. The two still
    disagree, so the name stays for a person to check."""
    if len(groups) < 2:
        return
    top = " ".join(str(groups[0].value).split())
    for i, other in enumerate(groups[1:], 1):
        whole = " ".join(str(other.value).split())
        cut = whole[: len(whole) - len(top)]
        if len(whole) > len(top) and whole.endswith(top) and re.fullmatch(r"[^\W\d_]{1,4}", cut) and top[:1].islower():
            groups.insert(0, groups.pop(i))
            return


def fuse(by_source: dict[str, dict[str, list[Reading]]], line_items: list[LineReading],
         vendor: dict[str, Any] | None = None, fields: tuple[str, ...] = FIELDS,
         today: dt.date | None = None, layout_source: str = "text",
         local_evidence: dict[str, tuple[int, int]] | None = None,
         text: str = "") -> tuple[dict[str, FieldResult], list[dict[str, Any]]]:  # fmt: skip
    """``by_source``: reader name -> field -> readings (best first). Returns field results and checks.
    ``local_evidence``: {evidence: (cases, right)} from AP's approvals, for the calibration; ``text``: the page's
    text (a tip printed after the tax, ``run_checks``)."""
    chosen: dict[str, _Group | None] = {}
    contest: dict[str, float] = {}
    all_groups: dict[str, list[_Group]] = {}
    for field in fields:
        per_source = {s: cands.get(field, []) for s, cands in by_source.items() if cands.get(field)}
        groups = _groups(field, per_source)
        _vouched_first(groups)
        if field == "vendor_name":
            _whole_first_word(groups)
        all_groups[field] = groups
        chosen[field] = groups[0] if groups else None
        if len(groups) > 1:
            contest[field] = groups[1].strength() / max(groups[0].strength(), 1e-9)

    if _unsettled(chosen.get("tax_total")) and any(chosen.get(f) is not None for f in TAX_FIELDS):
        # Only the template read a total tax, where it cannot vouch for it; the taxes read on the page give it.
        chosen["tax_total"] = None
        contest.pop("tax_total", None)
    _one_tax_per_printed_amount(chosen, all_groups, contest)
    _totals_that_add_up(chosen, all_groups, contest)
    values = {f: (normalize_value(f, g.value) if f in AMOUNT_FIELDS else g.value) for f, g in chosen.items() if g}
    checks = run_checks(values, line_items, vendor, today, text)
    confirmed: dict[str, int] = {}
    failed: dict[str, list[str]] = {}
    soft: dict[str, list[str]] = {}
    look: dict[str, list[str]] = {}
    for c in checks:
        for f in c["fields"]:
            if not c["ok"] and c["code"] in SOFT_CHECKS:
                soft.setdefault(f, []).append(c["detail"])
            elif not c["ok"] and c["code"] in LOOK_CHECKS:
                look.setdefault(f, []).append(c["detail"])
            elif c["ok"]:
                if c["code"] not in WEAK_CHECKS:
                    confirmed[f] = confirmed.get(f, 0) + (
                        2 if c["code"] in ("TOTALS_ADD_UP", "VENDOR_GST_MATCH") else 1
                    )
            else:
                failed.setdefault(f, []).append(c["detail"])
    template_rival: dict[str, Any] = {}
    adds_up = {f for c in checks if c["ok"] and c["code"] == "TOTALS_ADD_UP" for f in c["fields"]}
    for f in AMOUNT_FIELDS:
        rival = all_groups[f][1] if f in contest and len(all_groups[f]) > 1 else None
        if rival and f in adds_up and f not in failed and all(s == "template" for s, _ in rival.readings):
            # The supplier's template read another amount (its learned spot, as the layout was): the totals add up with
            # this one, read on the page by another reader. The arithmetic settles it: no contest.
            template_rival[f] = rival.value
            del contest[f]

    results: dict[str, FieldResult] = {}
    for field in fields:
        g = chosen.get(field)
        if g is None:
            results[field] = FieldResult(field, None, 0.0, MISSING, [], {}, ["not found on the invoice"])
            continue
        raw = g.strength()
        sources = sorted({s for s, _ in g.readings})
        # The template agreeing at a spot it does not vouch for is not a reader of its own: the evidence is the others'.
        vouched = sorted({s for s, r in g.readings if UNSETTLED not in r.method}) or sources
        reasons = []
        if len(sources) > 1:
            reasons.append(f"{len(sources)} readers agree ({', '.join(sources)})")
        else:
            reasons.append(f"read by {sources[0]} ({g.best().method})")
        if field in contest:
            raw *= 1.0 - 0.8 * min(contest[field], 1.0)
            other = all_groups[field][1]
            reasons.append(f"another reading: {other.value}")
        elif field in template_rival:
            reasons.append(f"the supplier's template read {template_rival[field]}: the totals add up with this one")
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
        if field in look:
            raw = min(raw, LOOK_CAP)
            reasons += [f"check: {d}" for d in look[field]]
        best = g.best()
        if best.method.endswith(UNSETTLED) and vouched != sources:
            best = max((r for _, r in g.readings if UNSETTLED not in r.method), key=lambda r: (bool(r.boxes), r.score))
        # What the evidence patterns below share; they differ in the readers and the reading they describe.
        alike = {"layout_source": layout_source, "contested": contest.get(field, 0.0) > 0.25,
                 "confirmed": bool(n_conf) and field not in failed, "soft": field in soft}  # fmt: skip
        measured = _clamp(raw)
        evidence = evidence_key(field, vouched, best.method, **alike)
        conf = calibrate(measured, evidence, local_evidence)
        if "vlm" in vouched and len(vouched) > 1:
            # The page reader agreeing is one more reader on top of a pattern the benchmark may have measured
            # without it (patterns with it are not measured yet): it never makes the value less certain.
            without = evidence_key(field, [s for s in vouched if s != "vlm"], best.method, **alike)
            conf = max(conf, calibrate(measured, without, local_evidence))
        if "template" in sources and len(sources) > 1:
            # Likewise the supplier's template agreeing: the pattern without it may be measured (the rule reader's
            # name with a rival name under it), the one with it not yet. Agreeing never makes the value less certain
            # than the other readers alone would (a lone reader is capped below verified: so is this floor).
            others = [r for s, r in g.readings if s != "template"]
            method = max(others, key=lambda r: (bool(r.boxes), r.score)).method
            without = evidence_key(field, [s for s in sources if s != "template"], method, **alike)
            conf = max(conf, min(calibrate(measured, without, local_evidence), SINGLE_READER_CAP))
        if len(vouched) > 1:
            # Readers that agree each describe the same evidence their own way (the supplier template's "template",
            # the rule reader's "label-right+adds-up"); which reading ranks first is chance, so the description
            # with the better measured record counts.
            for method in {r.method for _, r in g.readings if UNSETTLED not in r.method} - {best.method}:
                conf = max(conf, calibrate(measured, evidence_key(field, vouched, method, **alike), local_evidence))
        if field in failed:
            # The evidence pattern does not record the failed check: its measured accuracy must not undo the mark-down.
            conf = min(conf, measured)
        # A lone reader is never verified on its own measured record (the benchmark is synthetic):
        # verified also needs a second reader or a check to agree. The second OCR read of a scan
        # shares the first one's blind spots (a word OCR never saw), so with the rule reader it
        # counts as one reader.
        independent = {"rules" if s == "ocr" else s for s in vouched}
        texts = {" ".join(str(r.value).split()).casefold() for _, r in g.readings}
        if field == "vendor_name" and len(texts) > 1:
            independent = {"one"}  # they agree on the supplier, not on how its name is printed
            reasons.append(
                "readers differ on the exact name: " + " / ".join(sorted({str(r.value) for _, r in g.readings}))
            )
        forms = {"".join(str(r.value).split()).upper() for s, r in g.readings if s != "ai"}  # how the page prints it
        speck = False
        if field in ("invoice_number", "po_number") and len(forms) > 1:
            # "BC-15.332" / "BC-15332": the same number to compare, but which is printed is not agreed. OCR adds
            # specks as dots and commas far more often than it drops printed ones: the plainer form is shown.
            independent = {"one"}
            plain = min(forms, key=lambda f: (sum(not ch.isalnum() for ch in f), f))
            g.value = next(
                r.value for s, r in g.readings if s != "ai" and "".join(str(r.value).split()).upper() == plain
            )
            values[field] = g.value
            reasons.append("readers write it differently: " + " / ".join(sorted(forms)))
            # "IN833.79" / "IN833.7.9" (printed IN83379): even the plainer form keeps a dot between digits that the
            # readers do not agree on, so it may be a speck too. A person looks.
            speck = bool(re.search(r"\d[.,]\d", plain))
        if field in ("invoice_number", "po_number") and layout_source != "text" and len(forms) == 1:
            # "No.4.6" (printed No. 46), read so by both OCR reads: on a scan, a number that reads like a decimal
            # may hold a speck the readers share. A person looks.
            speck = bool(re.fullmatch(r"\d{1,4}[.,]\d{1,2}", next(iter(forms))))
            if speck:
                reasons.append("a dot between its digits may be a speck on the scan")
        if len(independent) < 2 and not (n_conf and field not in failed):
            conf = min(conf, SINGLE_READER_CAP)
        if (
            field in AMOUNT_FIELDS
            and layout_source != "text"
            and not (n_conf and field not in failed)
            and conf > SINGLE_READER_CAP
        ):
            # On a scan or a photo, an amount no sum confirms (no total read, or nothing adds up with it): the two OCR
            # reads share the page's geometry, and a tilted row can put the next row's amount on its label.
            conf = SINGLE_READER_CAP
            reasons.append("no total or sum on the page confirms this amount")
        if (
            field in REGISTRATION_FIELDS
            and all(BILL_TO_READING in r.method for _, r in g.readings)
            and not any(c["ok"] and c["code"] == "VENDOR_GST_MATCH" and field in c["fields"] for c in checks)
        ):
            # Read only where the customer's details are printed: a valid check digit shows the number is well
            # formed, not that it is the supplier's. Only the vendor master can say it is.
            conf = min(conf, SINGLE_READER_CAP)
            reasons.append("read only in the bill-to block: it may be the customer's number")
        if any(EITHER_TAX in r.method for _, r in g.readings):
            # Printed under "PST/QST" with nothing on the page to say which the supplier charges: the amount adds up,
            # which tax it is does not follow from that. A person says.
            conf = min(conf, SINGLE_READER_CAP)
            reasons.append(
                "printed under a label naming two taxes (PST/QST, GST/HST): which of the two it is, the page does not say"
            )
        if any(RIVAL_NAME in r.method for _, r in g.readings) and "template" not in vouched and not n_conf:
            conf = min(conf, SINGLE_READER_CAP)
            reasons.append(
                f"another name is printed at the top as well: {all_groups[field][1].value}"
                if len(all_groups[field]) > 1
                else "another name is printed at the top as well"
            )
        if all(r.method.startswith(UNLABELLED_METHODS) for _, r in g.readings):
            # A receipt's date and time, or a date beside the title, with no label to say which date it is: however
            # many readers see it, a person checks it.
            conf = min(conf, UNLABELLED_CAP)
            reasons.append("read without a label: printed where an invoice's date usually is")
        elif all(r.method.startswith(WEAK_LABEL_METHOD) for _, r in g.readings):
            # "Receipt No.", "Numéro :", "Nr.": words that name other numbers and dates too. However many readers
            # agree on what is printed after one, which number it is the page does not say: a person checks it.
            conf = min(conf, UNLABELLED_CAP)
            reasons.append("read after a word that also names other numbers or dates (a receipt's, a customer's)")
        if _unsettled(g):
            # Read only by the supplier's template where the value used to be, without its label on this page or at a
            # spot another value was printed at too: what is printed there now may be something else. A person checks.
            conf = min(conf, UNLABELLED_CAP)
            reasons.append("read only where this supplier's value was before, and the template cannot vouch for it")
        if any(TAKEN_OFF in r.method for _, r in g.readings):
            conf = min(conf, _clamp(raw, UNMEASURED_CAP))
            reasons.append(
                "something is taken off before the amount to pay (a deposit, a commission): a person checks it"
            )
        if any(TWO_SUBTOTALS in r.method for _, r in g.readings):
            conf = min(conf, SINGLE_READER_CAP)
            reasons.append("the totals add up with another subtotal printed too (before or after the charges)")
        if any(PART_OF_TERMS in r.method for _, r in g.readings):
            conf = min(conf, SINGLE_READER_CAP)
            reasons.append("the terms ask for a deposit first: these days are only the balance's")
        if all(UNMEASURED in r.method for _, r in g.readings):
            # Wording the benchmark never prints: its reading's family has a measured record, this wording not. Two
            # OCR reads of a scan see the same pixels: however many agree, only a check on other fields lifts it.
            if n_conf and field not in failed:
                conf = min(conf, _clamp(raw, SINGLE_READER_CAP))
                reasons.append("read from wording the test invoices do not print, and confirmed by the other fields")
            else:
                conf = min(conf, _clamp(raw, UNMEASURED_CAP))
                reasons.append("read from wording the test invoices do not print: a person checks it")
        # 03/04/2026 with nothing on the page to settle it: however many readers chose the same order, the
        # page does not confirm it. Settled only by the day it arrived, it is likely at best, never verified.
        unsure = [_date_unsure(r) for _, r in g.readings] if field in DATE_FIELDS else []
        if unsure and all(unsure):
            if all(u == "ambiguous" for u in unsure):
                conf = min(conf, 0.6)
                reasons.append("the date reads both day/month and month/day")
            else:
                conf = min(conf, SINGLE_READER_CAP)
                reasons.append("the date reads both day/month and month/day: the day it arrived chose one")
        # The AI's coding is what an approval posts: a page value it does not share is not verified for it,
        # however well the page's own values add up.
        ai_top = (by_source.get("ai") or {}).get(field, [])[:1]
        coding_differs = bool(ai_top) and normalize_value(field, ai_top[0].value) is not None and "ai" not in g.top
        if coding_differs:
            reasons.append(f"the coding has {ai_top[0].value}, the page reads {g.value}")
        # Two independent readings of the page that disagree: a person looks, however the rest adds up.
        readers_differ = _page_reader_differs(field, by_source)
        if readers_differ:
            vlm_reading, other, other_reading = readers_differ
            reasons.append(f"the page reader read {vlm_reading.value}, {other} read {other_reading.value}")
        if field in failed or field in look or coding_differs or readers_differ or speck:
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


def _date_unsure(r: Reading) -> str:
    """Why a date reading may have the day and month the wrong way round: "ambiguous" (printed 03/04/2026 and
    read so), "received" (the arrival date chose the order), or "" (the page settles it). A value found on
    the page by locating it (the AI's, Document Intelligence's) is as ambiguous as the text it was found in."""
    if "ambiguous" in r.method:
        return "ambiguous"
    if "+received" in r.method:
        return "received"
    if "located" in r.method or r.method == "di":
        found = parse_dates(str(r.raw or ""))
        if found and found[0][3]:
            return "ambiguous"
    return ""


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


# The total tax stands in for the separate taxes when none of them is read ("VAT 88.92", a US sales tax).
_TOTAL_PARTS = ("subtotal", "other_charges", *TAX_FIELDS, "tax_total")


def _one_tax_per_printed_amount(
    chosen: dict[str, Any], all_groups: dict[str, list[Any]], contest: dict[str, float]
) -> None:
    """Two taxes of the same amount where one is printed (B-1025): a supplier's template that learned HST reads its
    spot on a page that prints "G.S.T. @ 5%" (or the sales tax total's line), and the label reads GST. Unless the
    amounts add up with both, read on the same words, or on the total tax's line, or adding up with one of them, it is
    one tax: the reading only the template made (else the one on the total's line, else the weaker) is dropped. In
    place."""

    def cents(g: Any) -> int | None:
        v = normalize_value("subtotal", g.value) if g is not None else None
        return round(float(v) * 100) if v is not None else None

    def boxes(g: Any) -> list[Any]:
        return [b for _, r in g.readings for b in r.boxes]

    def same_spot(a: Any, b: Any) -> bool:
        return any(x.page == y.page and x.x0 < y.x1 and y.x0 < x.x1 and x.y0 < y.y1 and y.y0 < x.y1
                   for x in boxes(a) for y in boxes(b))  # fmt: skip

    total = chosen.get("tax_total")
    for i, a in enumerate(TAX_FIELDS):
        for b in TAX_FIELDS[i + 1 :]:
            ga, gb = chosen.get(a), chosen.get(b)
            if ga is None or gb is None or not cents(ga) or cents(ga) != cents(gb):
                continue
            on_total = {f for f, g in ((a, ga), (b, gb)) if total is not None and same_spot(g, total)}
            parts = [cents(chosen.get(f)) for f in ("subtotal", "other_charges", *TAX_FIELDS)]
            grand = cents(chosen.get("grand_total"))
            once = grand is not None and sum(p or 0 for p in parts) - (cents(ga) or 0) == grand
            twice = grand is not None and sum(p or 0 for p in parts) == grand
            if twice or not (same_spot(ga, gb) or (on_total and cents(total) == cents(ga)) or once):
                continue  # both printed: the total has them both

            weight = {f: (not all(s == "template" for s, _ in g.readings), f not in on_total, g.strength())
                      for f, g in ((a, ga), (b, gb))}  # fmt: skip
            loser = a if weight[a] < weight[b] else b
            all_groups[loser] = [g for g in all_groups.get(loser) or [] if g is not chosen[loser]]
            chosen[loser] = None
            contest.pop(loser, None)


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

    def added(parts: Any) -> float:
        # The total tax counts only when no separate tax is in the sum (it is their total, not one more tax).
        taxes = any(g is not None for f, g in zip(_TOTAL_PARTS, parts, strict=True) if f in TAX_FIELDS)
        return sum(amount(g) for f, g in zip(_TOTAL_PARTS, parts, strict=True) if f != "tax_total" or not taxes)

    current = [chosen.get(f) for f in _TOTAL_PARTS]
    if abs(added(current) - amount(chosen.get("grand_total"))) <= 0.011:
        return
    options = [(all_groups.get(f) or [])[:2] + ([None] if f != "subtotal" else []) for f in _TOTAL_PARTS]
    best, best_strength = None, -1.0
    for total in total_groups[:2]:
        for parts in product(*options):
            if parts[-1] is not None and any(g is not None for g in parts[2:-1]):
                continue  # the total tax and a separate tax: one of them is in the sum, not both
            if abs(added(parts) - amount(total)) > 0.011:
                continue
            if any(
                g is None and chosen.get(f) is not None and f in TAX_FIELDS
                for f, g in zip(_TOTAL_PARTS, parts, strict=True)
            ):
                continue  # dropping a tax the readers agreed on is not "adding up"
            if any(
                g is not None and g is not chosen.get(f) and not g.top and len(getattr(chosen.get(f), "top", ())) >= 2
                for f, g in (("grand_total", total), *zip(_TOTAL_PARTS, parts, strict=True))
            ):
                continue  # what no reader chose first does not replace what two chose first (the "8,00" row below)
            if (
                parts[-1] is None
                and chosen.get("tax_total") is not None
                and not any(g is not None for g in parts[2:-1])
            ):
                continue  # nor is dropping the total tax ("Total 889.20" read for the total of 889.20 + VAT 88.92)
            picked = (("grand_total", total), *zip(_TOTAL_PARTS, parts, strict=True))
            if any(_unsettled(g) for _, g in picked) and any(
                g is not chosen.get(f) for f, g in picked if not _unsettled(g)
            ):
                continue  # a template reading without its label fills a field; it does not re-decide the others
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
