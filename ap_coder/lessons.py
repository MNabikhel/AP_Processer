"""Lessons: what AP corrects, remembered per situation, with AP's own reason, for the AI to read.

A situation is a vendor, a kind of document (an invoice or a credit note) and a field of it: "TELUS, invoice,
invoice number", or one of its lines' GL account ("TELUS, invoice, lines like 'Mobility services'"). Every approval
records, discreetly, for each field AP Coder proposed: seen, and corrected or not, with the value AP chose and what
was proposed. Nothing is asked.

When AP corrects the same situation again and again (``why_after`` times since the last reason, 3 by default,
Settings → Review), the next approval that corrects it asks one short question before saving: "Why is this the right
value?". The answer is kept on the lesson. It is asked again only if the corrections keep coming: the loop goes on
until the situation needs no correction.

The lessons go, as short plain sentences (``prompt_lines``), to the AI: the AI reviewer's prompt (verifier.py) and
the GL suggestions (inference.suggest_accounts); and to the reviewer, as "Learned" hints on the review screen. AP
edits or retires them on the Learning page.
"""

from __future__ import annotations

import re
from typing import Any

from .memory import pair_lines, vendor_key

SETTING = "why_after"  # corrections of one situation since its last reason before the "why" question
DEFAULT_WHY_AFTER = 3
LINE_GL = "line_gl"  # the field of a line-coding lesson
INVOICE, CREDIT_NOTE = "invoice", "credit note"
MAX_PROMPT_LINES = 8

FIELD_WORDS = {
    "vendor_name": "vendor", "invoice_number": "invoice number", "invoice_date": "invoice date",
    "due_date": "due date", "po_number": "PO number", "currency": "currency",
    "gst_hst_registration_number": "GST/HST number", "qst_registration_number": "QST number",
    "pst_registration_number": "PST number", "subtotal": "subtotal", "gst_amount": "GST", "hst_amount": "HST",
    "pst_amount": "PST", "qst_amount": "QST", "tax_total": "total tax", "grand_total": "total",
    "payment_terms": "payment terms", LINE_GL: "GL account",
}  # fmt: skip


def kind_of(output: dict[str, Any]) -> str:
    try:
        return CREDIT_NOTE if float(output.get("grand_total") or 0) < 0 else INVOICE
    except (TypeError, ValueError):
        return INVOICE


def line_pattern(description: str) -> str:
    """The words that make a line "the same kind of line" from one invoice to the next: its first four words,
    without numbers, dates or punctuation ("Mobility services Oct 2026 - 555-0101" -> "mobility services")."""
    words = re.findall(r"[a-zà-ÿ]{2,}", str(description or "").lower())
    return " ".join(words[:4])


def situation(vendor: str, kind: str, field: str, pattern: str = "") -> str:
    return "|".join((vendor_key(vendor), kind, field, pattern))


def why_after(store: Any) -> int:
    try:
        return max(1, min(20, int(store.get_setting(SETTING) or DEFAULT_WHY_AFTER)))
    except (TypeError, ValueError):
        return DEFAULT_WHY_AFTER


def _shown(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    return "" if value in (None, "") else str(value)


def outcomes(proposed: dict[str, Any], final: dict[str, Any]) -> list[dict[str, Any]]:
    """What an approval says about each situation: {key, vendor, kind, field, pattern, proposed, value, corrected}
    for each header field AP Coder proposed or AP filled in, and for each line whose GL account AP changed or
    confirmed."""
    from .capture.supplier import outcome_rows

    vendor = str(final.get("vendor_name") or "")
    if not vendor_key(vendor):
        return []
    kind = kind_of(final)
    out = []
    for row in outcome_rows(proposed or {}, final):
        out.append({"key": situation(vendor, kind, row["field"]), "vendor": vendor, "kind": kind,
                    "field": row["field"], "pattern": "", "proposed": _shown(row["ai_value"]),
                    "value": _shown(row["final_value"]), "corrected": not row["correct"]})  # fmt: skip
    seen: set[str] = set()
    for before, after in pair_lines((proposed or {}).get("line_items") or [], final.get("line_items") or []):
        pattern = line_pattern(after.get("description") or "")
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        was = str((before or {}).get("predicted_gl_code") or "")
        now = str(after.get("predicted_gl_code") or "")
        if not now:
            continue
        out.append({"key": situation(vendor, kind, LINE_GL, pattern), "vendor": vendor, "kind": kind,
                    "field": LINE_GL, "pattern": pattern, "proposed": was, "value": now,
                    "corrected": bool(before) and was != now})  # fmt: skip
    return out


def record(store: Any, invoice_id: int, proposed: dict[str, Any], final: dict[str, Any], actor: str = "") -> int:
    """After an approval: each situation seen, and corrected or confirmed. Returns how many were corrected. Never
    raises (learning must never block an approval)."""
    try:
        rows = outcomes(proposed, final)
        for row in rows:
            store.record_lesson(row, invoice_id=invoice_id, actor=actor)
        return sum(1 for r in rows if r["corrected"])
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning("invoice %s: lessons not recorded", invoice_id, exc_info=True)
        return 0


def due_for_why(store: Any, proposed: dict[str, Any], final: dict[str, Any]) -> list[dict[str, Any]]:
    """The corrections in this approval that AP has made ``why_after`` times (this one included) since the last
    reason: [{key, field, label, pattern, proposed, value, times, explanation}]. [] when nothing is asked."""
    threshold = why_after(store)
    asked = []
    for row in outcomes(proposed, final):
        if not row["corrected"]:
            continue
        lesson = store.lesson(row["key"])
        times = (lesson or {}).get("since_why", 0) + 1
        if lesson and not lesson.get("retired") and times >= threshold:
            label = FIELD_WORDS.get(row["field"], row["field"])
            if row["pattern"]:
                label = f"GL account of lines like “{row['pattern']}”"
            asked.append({"key": row["key"], "field": row["field"], "label": label, "pattern": row["pattern"],
                          "proposed": row["proposed"], "value": row["value"], "times": times,
                          "explanation": lesson.get("explanation") or ""})  # fmt: skip
    return asked


def sentence(lesson: dict[str, Any], gl_names: dict[str, str] | None = None) -> str:
    """A lesson as one plain sentence, for the AI and the reviewer."""
    vendor, kind = lesson.get("vendor_name") or "this vendor", lesson.get("kind") or INVOICE
    value = str(lesson.get("value") or "")
    if lesson.get("field") == LINE_GL:
        name = (gl_names or {}).get(value, "")
        text = f"{vendor}: lines like “{lesson.get('pattern')}” are coded to GL {value}{' ' + name if name else ''}"
    else:
        word = FIELD_WORDS.get(lesson.get("field") or "", lesson.get("field") or "")
        text = f"{vendor} {kind}s: AP corrects the {word}"
        if lesson.get("proposed"):
            text += f" (read as “{lesson['proposed']}”, AP chose “{value}”)"
        elif value:
            text += f" (AP chose “{value}”)"
    text += f", {lesson.get('corrected', 0)} time{'s' if lesson.get('corrected', 0) != 1 else ''}"
    if lesson.get("explanation"):
        text += f". AP's reason: {lesson['explanation']}"
    return text + "."


def prompt_lines(store: Any, vendor: str, kind: str | None = None, *, fields: str = "all",
                 gl_names: dict[str, str] | None = None, limit: int = MAX_PROMPT_LINES) -> list[str]:  # fmt: skip
    """The lessons for a vendor (of a kind of document), newest first, as sentences: ``fields`` "all", "header" or
    "lines". Only lessons AP corrected at least once, never retired ones."""
    if store is None or not vendor_key(vendor or ""):
        return []
    try:
        rows = store.lessons_for(vendor_key(vendor), kind)
    except Exception:  # noqa: BLE001 - an older store: no lessons
        return []
    rows = [r for r in rows if r.get("corrected") and not r.get("retired")
            and (fields == "all" or (fields == "lines") == (r.get("field") == LINE_GL))]  # fmt: skip
    return [sentence(r, gl_names) for r in rows[:limit]]
