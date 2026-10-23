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

from ..core.memory import pair_lines, vendor_key
from ..core.reference_data import UNASSIGNED
from ..core.ui import FIELD_WORDS as _WORDS

SETTING = "why_after"  # corrections of one situation since its last reason before the "why" question
DEFAULT_WHY_AFTER = 3
LINE_GL = "line_gl"  # the field of a line-coding lesson (its GL account)
LINE_CC = "line_cc"  # the field of a line's cost center lesson
LINE_FIELDS = (LINE_GL, LINE_CC)
INVOICE, CREDIT_NOTE = "invoice", "credit note"
MAX_PROMPT_LINES = 8

FIELD_WORDS = {**_WORDS, LINE_GL: _WORDS["line_gl"], LINE_CC: "cost center"}  # one place for these words: core/ui.py


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
    for each header field AP Coder proposed or AP filled in, and for each line whose GL account or cost center AP
    changed or confirmed (``LINE_GL``, ``LINE_CC``)."""
    from ..capture.supplier import outcome_rows

    vendor = str(final.get("vendor_name") or "")
    if not vendor_key(vendor):
        return []
    kind = kind_of(final)
    out = []
    for row in outcome_rows(proposed or {}, final):
        out.append({"key": situation(vendor, kind, row["field"]), "vendor": vendor, "kind": kind,
                    "field": row["field"], "pattern": "", "proposed": _shown(row["ai_value"]),
                    "value": _shown(row["final_value"]), "corrected": not row["correct"]})  # fmt: skip
    from .offline_coder import guessed

    seen: set[str] = set()
    for before, after in pair_lines((proposed or {}).get("line_items") or [], final.get("line_items") or []):
        pattern = line_pattern(after.get("description") or "")
        if not pattern or pattern in seen:
            continue
        if before and guessed(before.get("reasoning_justification")):
            continue  # the AI only guessed: keeping it confirms nothing, changing it corrects nothing (B-475)
        seen.add(pattern)
        for field, column in ((LINE_GL, "predicted_gl_code"), (LINE_CC, "predicted_cost_center")):
            was = str((before or {}).get(column) or "")
            now = str(after.get(column) or "")
            if not now or (field == LINE_CC and now == UNASSIGNED):  # no cost center chosen: nothing to learn
                continue
            out.append({"key": situation(vendor, kind, field, pattern), "vendor": vendor, "kind": kind,
                        "field": field, "pattern": pattern, "proposed": was, "value": now,
                        "corrected": bool(before) and was not in ("", UNASSIGNED) and was != now})  # fmt: skip
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
                label = f"{'Cost center' if row['field'] == LINE_CC else 'GL account'} of lines like “{row['pattern']}”"
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
    elif lesson.get("field") == LINE_CC:
        text = f"{vendor}: lines like “{lesson.get('pattern')}” go to cost center {value}"
    else:
        word = FIELD_WORDS.get(lesson.get("field") or "", lesson.get("field") or "")
        text = f"{vendor} {kind}s: AP corrects the {word}"
        if lesson.get("proposed") and lesson["proposed"] != UNASSIGNED:
            text += f" (read as “{lesson['proposed']}”, AP chose “{value}”)"
        elif value:
            text += f" (AP chose “{value}”)"
    text += f", {lesson.get('corrected', 0)} time{'s' if lesson.get('corrected', 0) != 1 else ''}"
    if lesson.get("explanation"):
        text += f". AP's reason: {lesson['explanation']}"
    return text + "."


AGREEING = 2  # corrections to the same value before a lesson with no reason reaches the AI (D-72)


def reaches_ai(lesson: dict[str, Any]) -> bool:
    """Whether a lesson goes to the AI and to the review screen's "Learned" hints: not retired, and corrected
    ``AGREEING`` times to its value (``agreeing``, from the store), or corrected once with the clerk's reason. A single
    correction may be a slip: it is counted, and waits (D-72)."""
    if not lesson.get("corrected") or lesson.get("retired"):
        return False
    agreeing = lesson.get("agreeing", lesson.get("corrected")) or 0
    return agreeing >= AGREEING or bool(str(lesson.get("explanation") or "").strip())


def on_screen(lesson: dict[str, Any]) -> bool:
    """Whether a lesson is shown to the reviewer as a hint: corrected at least once, not retired. A person decides
    what to do with it, so it needs no second correction (``reaches_ai`` does: the AI acts on it, D-72)."""
    return bool(lesson.get("corrected")) and not lesson.get("retired")


SEEN_ONCE = "Corrected once before: "  # a hint from a single correction, not yet one the AI reads


def prompt_lines(store: Any, vendor: str, kind: str | None = None, *, fields: str = "all",
                 gl_names: dict[str, str] | None = None, limit: int = MAX_PROMPT_LINES,
                 screen: bool = False) -> list[str]:  # fmt: skip
    """The lessons for a vendor (of a kind of document), newest first, as sentences: ``fields`` "all", "header" or
    "lines". For the AI, only lessons that reach it (``reaches_ai``); with ``screen``, the reviewer's hints
    (``on_screen``): a lesson from a single correction too, said as such (``SEEN_ONCE``). Never retired ones."""
    if store is None or not vendor_key(vendor or ""):
        return []
    try:
        rows = store.lessons_for(vendor_key(vendor), kind)
    except Exception:  # noqa: BLE001 - an older store: no lessons
        return []
    shown = on_screen if screen else reaches_ai
    rows = [r for r in rows if shown(r)
            and (fields == "all" or (fields == "lines") == (r.get("field") in LINE_FIELDS))]  # fmt: skip
    return [("" if reaches_ai(r) else SEEN_ONCE) + sentence(r, gl_names) for r in rows[:limit]]


# --- Does a lesson help? (the Lessons tab, D-74) ---------------------------------------------------------------------

WORKING, NOT_WORKING, TOO_EARLY = "working", "not_working", "too_early"
MIN_AFTER = 3  # invoices of the vendor seen since a lesson reached the AI before saying whether it helps


def _rate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    corrected = sum(1 for r in rows if r.get("corrected"))
    return {"invoices": len(rows), "corrected": corrected,
            "rate": round(corrected / len(rows), 2) if rows else None}  # fmt: skip


def effect(history: list[dict[str, Any]], lesson: dict[str, Any] | None) -> dict[str, Any]:
    """Corrections per invoice of a lesson's vendor and field before the lesson reached the AI (``reaches_ai``: the
    second correction to its value, or the first with the clerk's reason) and after: {"reached", "since", "before",
    "after", "verdict"}. ``history``: the approvals that saw the situation, oldest first (``Store.lesson_history``). The
    verdict is ``WORKING`` (fewer corrections since), ``NOT_WORKING`` (as many or more, over ``MIN_AFTER`` invoices or
    more: to suggest retiring it, never done on its own) or ``TOO_EARLY``."""
    lesson = lesson or {}
    value, explained = str(lesson.get("value") or ""), str(lesson.get("explained_at") or "")
    reached, agreeing = None, 0  # the approval (its place in ``history``) with which the lesson reached the AI
    for n, r in enumerate(history):
        if not r.get("corrected"):
            continue
        agreeing += 1 if str(r.get("value") or "") == value else 0
        if agreeing >= AGREEING or (explained and str(r["at"]) >= explained):
            reached = n
            break
    if reached is None and explained and any(r.get("corrected") for r in history):
        # The reason was given after the corrections: from the last approval before it.
        reached = max((n for n, r in enumerate(history) if str(r["at"]) <= explained), default=len(history) - 1)
    since = str(history[reached]["at"]) if reached is not None else ""
    before = history[: reached + 1] if reached is not None else list(history)
    after = history[reached + 1 :] if reached is not None else []
    out = {
        "reached": reached is not None,
        "since": since,
        "before": _rate(before),
        "after": _rate(after),
        "verdict": TOO_EARLY,
    }
    if reached is not None and len(after) >= MIN_AFTER:
        better = out["after"]["rate"] < (out["before"]["rate"] or 0)
        out["verdict"] = WORKING if better else NOT_WORKING
    return out
