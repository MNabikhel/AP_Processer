"""Lessons: what AP corrects, remembered per situation, with AP's own reason, for the AI to read.

A situation is a vendor, a kind of document (an invoice or a credit note) and a field of it: "TELUS, invoice,
invoice number", or one of its lines' GL account ("TELUS, invoice, lines like 'Mobility services'"). Every approval
records, discreetly, for each field AP Coder proposed: seen, and corrected or not, with the value AP chose and what
was proposed. Nothing is asked.

When AP corrects the same situation again and again (``why_after`` times since the last reason, 3 by default,
Settings → Review), the next approval that corrects it asks one short question before saving: "Why is this the right
value?". The answer is kept on the lesson. It is asked again only if the corrections keep coming: the loop goes on
until the situation needs no correction.

A second question comes after an approval, never holding it (D-218): when the person changed an account AP Coder had
coded by itself, or had offered first from this supplier's own history, the next page asks once "Why 7340 and not
7420 for “Working lunch”?" (``surprise``: the most surprising line of the invoice, built from what the line kept:
``LineItem.decided_by`` and ``candidates``; no model is asked). The answer, typed or one click, is kept as that
lesson's reason; "one-off" forgets the line from the coding memory instead. Settings → Review turns both questions
off (``asks_why``).

The lessons go, as short plain sentences (``prompt_lines``), to the AI: the AI reviewer's prompt (verifier.py) and
the GL suggestions (inference.suggest_accounts); and to the reviewer, as "Learned" hints on the review screen. AP
edits or retires them on the Learning page.
"""

from __future__ import annotations

from typing import Any

from ..core.keys import LINE_CC, LINE_FIELDS, LINE_GL, line_pattern
from ..core.memory import pair_lines, vendor_key
from ..core.reference_data import UNASSIGNED
from ..core.ui import FIELD_WORDS as _WORDS

SETTING = "why_after"  # corrections of one situation since its last reason before the "why" question
DEFAULT_WHY_AFTER = 3
ASK = "why_surprise"  # Settings → Review, "Ask why after a surprising correction": "0", no question is ever asked
# The one-click answers to the question after a surprising correction (``surprise``'s "reasons").
KIND, UNIT, CHANGED, ONE_OFF = "kind", "unit", "changed", "one_off"
# A line's account came from this supplier's own history (``LineItem.decided_by``'s and a candidate's "source"): one
# offered first from it was nearly sure.
OWN_HISTORY = ("vendor", "usual", "supplier")
NEARLY = 0.5  # ...when it counts its lines: with at least this share of the supplier's (its lines' usual account)
# What may have changed, by where the account AP Coder chose came from.
_CHANGED = {"supplier": "Last year's account no longer applies", "vendor": "It changed: these go there from now on",
            "usual": "It changed: these go there from now on", "rule": "The rule does not fit this line",
            "others": "The words on the line mislead here", "words": "The words on the line mislead here"}  # fmt: skip
INVOICE, CREDIT_NOTE = "invoice", "credit note"
MAX_PROMPT_LINES = 8

FIELD_WORDS = {**_WORDS, LINE_GL: _WORDS["line_gl"], LINE_CC: "cost center"}  # one place for these words: core/ui.py


def kind_of(output: dict[str, Any]) -> str:
    try:
        return CREDIT_NOTE if float(output.get("grand_total") or 0) < 0 else INVOICE
    except (TypeError, ValueError):
        return INVOICE


def situation(vendor: str, kind: str, field: str, pattern: str = "", key: str | None = None) -> str:
    """A lesson's key: the vendor's (``key``, its own of two whose names read alike; else its name's), the kind of
    document, the field and the lines' pattern."""
    return "|".join((key or vendor_key(vendor), kind, field, pattern))


def vendor_of(store: Any, vendor: str, gst: Any = None) -> str:
    """The key a vendor's lessons are kept under: of two vendors whose names read alike, the one with this GST/HST
    number (``Store.vendor_key_for``, B-1021, B-1514); the name's key without a store."""
    if store is not None and hasattr(store, "vendor_key_for"):
        try:
            return store.vendor_key_for(vendor, gst)
        except Exception:  # noqa: BLE001 - an older store: the name's key
            pass
    return vendor_key(vendor)


def why_after(store: Any) -> int:
    try:
        return max(1, min(20, int(store.get_setting(SETTING) or DEFAULT_WHY_AFTER)))
    except (TypeError, ValueError):
        return DEFAULT_WHY_AFTER


def asks_why(store: Any) -> bool:
    """Whether AP is ever asked why (Settings → Review, on until turned off)."""
    try:
        return str(store.get_setting(ASK) or "1").strip() != "0"
    except Exception:  # noqa: BLE001 - an older store: asked, as before
        return True


def _shown(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    return "" if value in (None, "") else str(value)


def outcomes(proposed: dict[str, Any], final: dict[str, Any], key: str | None = None) -> list[dict[str, Any]]:
    """What an approval says about each situation: {key, vendor, kind, field, pattern, proposed, value, corrected}
    for each header field AP Coder proposed or AP filled in, and for each line whose GL account or cost center AP
    changed or confirmed (``LINE_GL``, ``LINE_CC``)."""
    from ..capture.supplier import outcome_rows

    vendor = str(final.get("vendor_name") or "")
    if not vendor_key(vendor):
        return []
    key = key or vendor_key(vendor)  # the vendor's own (``vendor_of``)
    kind = kind_of(final)
    out = []
    for row in outcome_rows(proposed or {}, final):
        out.append({"key": situation(vendor, kind, row["field"], key=key), "vendor": vendor, "vendor_key": key,
                    "kind": kind,
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
            out.append({"key": situation(vendor, kind, field, pattern, key=key), "vendor": vendor, "vendor_key": key,
                        "kind": kind,
                        "field": field, "pattern": pattern, "proposed": was, "value": now,
                        "corrected": bool(before) and was not in ("", UNASSIGNED) and was != now})  # fmt: skip
    return out


def record(store: Any, invoice_id: int, proposed: dict[str, Any], final: dict[str, Any], actor: str = "",
           bulk: bool = False) -> int:  # fmt: skip
    """After an approval: each situation seen, and corrected or confirmed. Returns how many were corrected. Never
    raises (learning must never block an approval). ``bulk``: approved in bulk, without anyone opening it: only its
    corrections (an earlier approver's, on a reopened invoice) are recorded; what was kept was never looked at, so it
    neither confirms a lesson nor counts as an invoice the lesson was seen on (B-955)."""
    try:
        rows = outcomes(proposed, final, _key_of(store, final))
        if bulk:
            rows = [r for r in rows if r["corrected"]]
        for row in rows:
            store.record_lesson(row, invoice_id=invoice_id, actor=actor)
        return sum(1 for r in rows if r["corrected"])
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning("invoice %s: lessons not recorded", invoice_id, exc_info=True)
        return 0


def _key_of(store: Any, doc: dict[str, Any]) -> str:
    return vendor_of(store, str(doc.get("vendor_name") or ""), doc.get("gst_hst_registration_number"))


def due_for_why(store: Any, proposed: dict[str, Any], final: dict[str, Any]) -> list[dict[str, Any]]:
    """The corrections in this approval that AP has made ``why_after`` times (this one included) since the last
    reason: [{key, field, label, pattern, proposed, value, times, explanation}]. [] when nothing is asked."""
    if not asks_why(store):
        return []
    threshold = why_after(store)
    asked = []
    for row in outcomes(proposed, final, _key_of(store, final)):
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


def _code(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text == UNASSIGNED else text


def _expected(before: dict[str, Any]) -> tuple[str, str, str, float] | None:
    """What AP Coder expected for a line as proposed: (GL code, business unit or "", where it came from, how sure:
    from 1, an account offered first, to 4, a rule's). None when it expected nothing: a guess, or a line left for a
    person with no account offered first from this supplier's own history, or one with under ``NEARLY`` of the
    supplier's lines (they went to several accounts: it was not nearly sure)."""
    from .offline_coder import unsure

    gl, by = _code(before.get("predicted_gl_code")), before.get("decided_by") or {}
    if gl:  # coded with no one picking
        source = str(by.get("source") or "")
        if source in ("model", "person") or unsure(before.get("reasoning_justification")):
            return None
        sure = (3.0 if source == "rule" else 2.0) + float(by.get("share") or 0)
        return gl, _code(before.get("predicted_cost_center")), source, sure
    first = next(iter(before.get("candidates") or []), None)
    if not first or first.get("source") not in OWN_HISTORY or not _code(first.get("code")):
        return None
    if first.get("of") and float(first.get("share") or 0) < NEARLY:
        return None
    unit = "" if first.get("guess") else _code(first.get("business_unit"))
    return _code(first.get("code")), unit, str(first["source"]), 1.0 + float(first.get("share") or 0)


def surprise(store: Any, proposed: dict[str, Any], final: dict[str, Any],
             reference: Any = None) -> dict[str, Any] | None:  # fmt: skip
    """The account of this approval AP Coder least expected to be changed (D-218): {key (the lesson's situation),
    field, line, description, pattern, was, now, coded (AP Coder had coded it; False: offered first), question,
    reasons: [{code, label}]}. Of the lines whose account the person changed from the one AP Coder had coded by
    itself or offered first (``_expected``), the one it was surest of; None when there is none. As ``outcomes``,
    the first line of a kind speaks for it. A business unit changed alone counts where accounts are whole strings
    (``reference`` has a chart)."""
    from ..core import chart

    vendor = str(final.get("vendor_name") or "")
    if not vendor_key(vendor):
        return None
    units = reference is not None and getattr(reference, "pairs", None) is not None
    best: dict[str, Any] | None = None
    seen: set[str] = set()
    for before, after in pair_lines((proposed or {}).get("line_items") or [], final.get("line_items") or []):
        pattern = line_pattern(after.get("description") or "")
        if not pattern or pattern in seen or before is None:
            continue
        seen.add(pattern)
        expected = _expected(before)
        now_gl, now_cc = _code(after.get("predicted_gl_code")), _code(after.get("predicted_cost_center"))
        if expected is None or not now_gl:
            continue
        was_gl, was_cc, source, sure = expected
        moved = units and bool(was_cc and now_cc) and was_cc != now_cc  # to another business unit
        if (now_gl == was_gl and not moved) or (best is not None and sure <= best["sure"]):
            continue
        was, now = was_gl, now_gl
        if moved:
            was, now = chart.join_account(was_cc, was_gl), chart.join_account(now_cc, now_gl)
        description = " ".join(str(after.get("description") or "").split())[:80]
        reasons = [{"code": KIND, "label": "This supplier's lines like this go there"}]
        if moved and now_cc in _units_named(reference, description):
            reasons.append({"code": UNIT, "label": f"The unit named on the line ({reference.pairs.name_of(now_cc)})"})
        elif source in _CHANGED:
            reasons.append({"code": CHANGED, "label": _CHANGED[source]})
        reasons.append({"code": ONE_OFF, "label": "One-off: don't learn from it"})
        field = LINE_GL if now_gl != was_gl else LINE_CC
        best = {"key": situation(vendor, kind_of(final), field, pattern, key=_key_of(store, final)), "field": field,
                "line": after.get("line_number"), "description": description, "pattern": pattern, "was": was,
                "now": now, "coded": bool(_code(before.get("predicted_gl_code"))), "sure": sure,
                "question": f"Why {now} and not {was} for “{description}”?", "reasons": reasons}  # fmt: skip
    return best


def _units_named(reference: Any, description: str) -> dict[str, int]:
    from .supplier_prior import units_named

    return units_named(reference, description)


def reason_kept(found: dict[str, Any], code: str, typed: str) -> str:
    """The reason to keep for an answer to ``surprise``'s question: the one-click reason's words and what was typed;
    "" when neither. For an account only offered, the lesson shows no correction: the two accounts are said."""
    label = next((r["label"] for r in found["reasons"] if r["code"] == code and code != ONE_OFF), "")
    said = ": ".join(part for part in (label, " ".join(str(typed or "").split())[:300]) if part)
    if said and not found["coded"]:
        said = f"{found['now']}, not the {found['was']} offered first: {said}"
    return said


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


def prompt_lines(store: Any, vendor: str, kind: str | None = None, *, gst: Any = None, fields: str = "all",
                 gl_names: dict[str, str] | None = None, limit: int = MAX_PROMPT_LINES,
                 screen: bool = False) -> list[str]:  # fmt: skip
    """The lessons for a vendor (of a kind of document), newest first, as sentences: ``fields`` "all", "header" or
    "lines". For the AI, only lessons that reach it (``reaches_ai``); with ``screen``, the reviewer's hints
    (``on_screen``): a lesson from a single correction too, said as such (``SEEN_ONCE``). Never retired ones."""
    if store is None or not vendor_key(vendor or ""):
        return []
    try:
        rows = store.lessons_for(vendor_of(store, vendor, gst), kind)  # its own, of two alike (B-1514)
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
