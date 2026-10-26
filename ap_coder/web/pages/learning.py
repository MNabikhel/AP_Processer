"""Learning & accuracy, in four tabs (``?tab=coding|suppliers|lessons|readers``; Lessons: corrections per situation):

* Coding accuracy: accuracy against the 90% target, by week (an SVG line), recent lessons, accuracy by vendor, the
  most common corrections, the memory (every lesson, with the option to forget some) and teaching from past coding
  (an ERP export of AP lines: upload, match the columns, teach).
* Supplier learning: what each vendor's reviewed invoices taught, the fields the clerk corrected, how far it is from
  going touchless, and an AP lead's "Keep supervised" / "Allow again" (recorded in the Activity log with their name).
* Readers (a developer tool, shown with Settings → About's "Show developer tools" or AP_DEVELOPER_TOOLS=1, B-511):
  each capture reader against what AP approved (scorecard, field by field) and the training data export (a ZIP of
  page images and approved values, a GET download made on this computer).

Every change is a POST answered with where to go next; the reader picked, the memory search and the uploaded history
waiting for its columns are in the address.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from markupsafe import Markup

from ap_coder.ai import history
from ap_coder.core import ui
from ap_coder.core.store import Store
from ap_coder.erp import insights
from ap_coder.web.charts import SERIES, Series, line_chart, percent
from ap_coder.web.core import (
    TARGET_ACCURACY,
    accuracy_note,
    current_store,
    developer_tools,
    done,
    get_settings,
    get_store,
    posted_form,
    reference_or_none,
    refuse,
    render,
    reviewer,
)
from ap_coder.web.files import TOO_BIG, SheetError, read_capped, read_csv, read_excel
from ap_coder.web.invoice_view import gl_name

router = APIRouter()

TABS = {"coding": "Coding accuracy", "suppliers": "Supplier learning", "lessons": "Lessons", "readers": "Readers"}
HISTORY_LABELS = {"vendor_name": "Vendor *", "description": "Line description *", "gl_code": "GL account *",
                  "cost_center": "Cost center", "amount": "Amount", "date": "Date"}  # fmt: skip
FIELD_LABELS = {
    "vendor_name": "Vendor", "invoice_number": "Invoice #", "invoice_date": "Invoice date", "due_date": "Due date",
    "po_number": "PO #", "currency": "Currency", "gst_hst_registration_number": "GST/HST #",
    "qst_registration_number": "QST #", "pst_registration_number": "PST #", "subtotal": "Subtotal",
    "gst_amount": "GST", "hst_amount": "HST",
    "pst_amount": "PST", "qst_amount": "QST", "tax_total": "Tax total", "grand_total": "Total",
    "payment_terms": "Terms",
}  # fmt: skip
READER_LABELS = {
    "fused": "Combined (what AP saw)", "rules": "OCR + rules", "ocr2": "OCR (second engine)",
    "ocr": "OCR (second engine)", "vlm": "Page reader", "template": "Supplier template", "ai": "AI model",
    "di": "Azure Document Intelligence",
}  # fmt: skip
_READER_ORDER = list(dict.fromkeys(READER_LABELS.values()))
_STATE_ORDER = {"suspended": 0, "ready": 1, "autonomous": 2, "supervised": 3, "learning": 4, "held": 5}
RECENT = 20  # "corrections in the last 20 invoices"
FEED = 4  # recent lessons beside the chart
VENDORS_SHOWN = 12
MEMORY_READ = 2000  # lessons the memory list works on (newest first)
MEMORY_SHOWN = 300
OUTCOMES = {
    "accepted": "✓ confirmed",
    "corrected": "✎ corrected",
    "coded": "✎ coded by AP (the AI only guessed)",
    "bulk": "✓ approved in bulk, as proposed",
    "history": "⟲ ERP history",
}


# --- Small helpers --------------------------------------------------------------------------------------------------


def built(html: str) -> Markup:
    """HTML from an ``ap_coder.core.ui`` builder (it escaped the text it was given), for a template."""
    return Markup(html)  # noqa: S704 - ui.py escapes its input


def reader_label(reader: str) -> str:
    return READER_LABELS.get(reader, reader)


def short(text: str, n: int = 42) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def rate_text(rate: float) -> str:
    return f"{rate:.1%}" if 0.995 <= rate < 1 else f"{rate:.0%}"


def added(rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], str]) -> dict[str, dict[str, Any]]:
    """Score rows that share ``key`` added together: the two kinds of second OCR read are one reader here."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        m = out.setdefault(key(r), {"readers": [], "fields": 0, "agreed": 0, "invoices": 0, "last_read": None,
                                    "last_final": None})  # fmt: skip
        if r.get("reader"):
            m["readers"].append(r["reader"])
        for count in ("fields", "agreed", "invoices"):
            m[count] += r[count]
        if m["last_read"] is None and r.get("last_read") is not None:
            m["last_read"], m["last_final"] = r["last_read"], r.get("last_final")
    for m in out.values():
        m["rate"] = m["agreed"] / m["fields"] if m["fields"] else 0.0
    return out


def _url(**params: Any) -> str:
    keep = {k: v for k, v in params.items() if v not in ("", None)}
    return "/learning" + (f"?{urlencode(keep)}" if keep else "")


# --- Uploaded AP line history, waiting for its columns ------------------------------------------------------------
# The upload is read once and kept under a one-off token until the columns are matched, in the dashboard's database
# (a restart of the server keeps it, D-99); an hour later, or after teaching, it is forgotten.

_UPLOAD_TTL = 3600
_MAX_UPLOADS = 4
HISTORY_KIND = "ap-history"  # its kind in the database's short-lived table (``Store.keep_ephemeral``)


def read_sheets(name: str, data: bytes) -> dict[str, tuple[list[str], list[dict[str, Any]]]]:
    """The uploaded file's sheets as (columns, rows of text cells). Raises ValueError (in plain words) when it is not
    a spreadsheet AP Coder can read."""
    if not data:
        raise ValueError("The file is empty.")
    frames: dict[str, Any] = {}
    try:  # a CSV as every page reads one: Windows-1252 and semicolon files too (B-1400)
        frames = read_excel(data) if name.lower().endswith((".xlsx", ".xls")) else {"CSV": read_csv(data)}
    except SheetError:  # a workbook that unzips to too much, a CSV in no encoding it reads: its message says so
        raise
    except Exception as exc:  # noqa: BLE001 - empty, damaged or not a spreadsheet at all
        raise ValueError(
            "This file could not be read as a spreadsheet: it may be empty, damaged or another format. In Excel, save "
            "it as CSV UTF-8 or Excel Workbook (.xlsx) and upload it again."
        ) from exc
    if not frames:
        raise ValueError("Could not read this file. In Excel, use Save As → CSV UTF-8 and upload it again.")
    sheets = {}
    for sheet, df in frames.items():
        df = df.fillna("")
        df.columns = [str(c) for c in df.columns]  # a header like 2024 arrives as a number
        if len(df):
            sheets[str(sheet)] = (list(df.columns), df.to_dict("records"))
    if not sheets:
        raise ValueError("The file has no rows under its header.")
    return sheets


def keep_upload(name: str, sheets: dict[str, tuple[list[str], list[dict[str, Any]]]]) -> str:
    value = json.dumps({"name": name, "sheets": sheets}, default=str).encode("utf-8")
    return current_store().keep_ephemeral(HISTORY_KIND, value, _UPLOAD_TTL, keep=_MAX_UPLOADS)


def get_upload(token: str | None) -> tuple[str, dict[str, tuple[list[str], list[dict[str, Any]]]]] | None:
    kept = current_store().ephemeral(HISTORY_KIND, token)
    if kept is None:
        return None
    found = json.loads(kept)
    return found["name"], {sheet: (columns, rows) for sheet, (columns, rows) in found["sheets"].items()}


def drop_upload(token: str) -> None:
    current_store().drop_ephemeral(HISTORY_KIND, token)


# --- Coding accuracy ----------------------------------------------------------------------------------------------


def _history_context(store: Store, token: str | None, sheet: str | None) -> dict[str, Any]:
    upload = get_upload(token)
    context: dict[str, Any] = {"history_count": store.history_count(), "history_labels": HISTORY_LABELS,
                               "upload": None}  # fmt: skip
    if token and upload is None:
        context["upload_gone"] = True
    if upload:
        name, sheets = upload
        sheet = sheet if sheet in sheets else next(iter(sheets))
        columns, rows = sheets[sheet]
        context["upload"] = {"token": token, "name": name, "sheets": list(sheets), "sheet": sheet, "columns": columns,
                             "rows": len(rows), "guessed": history.map_columns(columns)}  # fmt: skip
    return context


def coding_context(store: Store, mem_q: str, settings: Any = None) -> dict[str, Any]:
    m = store.metrics()
    if not m["lines_reviewed"]:
        return {"metrics": None}
    measured = m["line_accuracy"] is not None  # none: AP coded every line, the AI proposed none (no model, B-1298)
    accuracy = m["line_accuracy"] or 0.0
    clean, total = insights.approved_as_coded(store)  # as Insights counts them (D-39)
    weekly = []
    for w in m["weekly"]:
        lines = w["accepted"] + w["corrected"]
        weekly.append({**w, "lines": lines, "accuracy": w["accepted"] / lines if lines else None})
    good = accuracy >= TARGET_ACCURACY
    note, compared = accuracy_note(settings, m["lines_measured"]) if settings is not None else ("", True)
    distance = f"{(accuracy - TARGET_ACCURACY) * 100:+.1f} pts vs 90% target"  # early on: a plain note (B-442)
    tiles = [
        ui.tile("Coding accuracy", f"{accuracy:.1%}", "", "green" if good else "violet",
                (distance + (f" · {note}" if note else "")) if compared else note,
                ("up" if good else "down") if compared else "note",
                [w["accuracy"] or 0 for w in weekly] if len(weekly) > 1 else None)
        if measured else
        ui.tile("Coding accuracy", "—", "", "violet", "AP coded every line: the AI proposed none to measure yet",
                "note"),
        ui.tile("Lines reviewed", f"{m['lines_reviewed']:,}", "", "blue", f"{m['lines_accepted']:,} confirmed"),
        ui.tile("Corrections taught", f"{m['lines_corrected']:,}", "", "amber", "used on the next invoice"),
        ui.tile(insights.AS_CODED, "—" if not total else f"{clean / total:.0%}", "", "green",
                insights.as_coded_note(clean, insights.filled_in_count(store), total)),
    ]  # fmt: skip
    tiles = [built(tile) for tile in tiles]
    weeks = [w["week"] for w in weekly]
    chart = line_chart(
        weeks,
        Series("Accuracy", [w["accuracy"] for w in weekly], SERIES[0]),
        title="Coding accuracy by week",
        desc=f"Share of reviewed lines whose AI coding was confirmed, week by week, against the 90% target: "
        f"{accuracy:.1%} overall over {ui.plural(len(weeks), 'week')}.",
        y_domain=(0.0, 1.0),
        y_format=percent,
        target=(TARGET_ACCURACY, "90% target"),
        width=620,
        tooltip=lambda i, _s: (
            f"{weekly[i]['week']}: {weekly[i]['accuracy']:.0%} "
            f"({weekly[i]['accepted']} confirmed, {weekly[i]['corrected']} corrected)"
        ),
    )
    rows = store.feedback_rows(limit=MEMORY_READ)
    reviewed = [r for r in rows if r["outcome"] != "history"]
    feed = [
        {**r, "short": short(r["description"]), "when": ui.time_ago(r["created_at"])} for r in reviewed[:FEED]
    ]  # fmt: skip
    reference = reference_or_none(store)
    corrections = [
        {
            **c,
            "before_name": gl_name(reference, c["suggested_gl"]) if c["suggested_gl"] != "(new line)" else "",
            "after_name": gl_name(reference, c["final_gl"]),
        }
        for c in m["top_corrections"]
    ]
    query = (mem_q or "").strip().lower()
    matching = [
        r
        for r in rows
        if not query
        or query
        in " ".join(
            str(r.get(k) or "")
            for k in ("vendor_name", "description", "suggested_gl", "final_gl", "final_cc", "reviewer")
        ).lower()  # fmt: skip
    ]
    memory = [
        {**r, "date": str(r["created_at"] or "")[:10], "outcome_text": OUTCOMES.get(r["outcome"], r["outcome"])}
        for r in matching[:MEMORY_SHOWN]
    ]
    return {
        "metrics": m,
        "tiles": tiles,
        "chart": chart,
        "weekly": weekly,
        "feed": feed,
        "vendors": [
            built(ui.vendor_row(v["vendor_name"], v["lines"], v["accepted"] / v["lines"], v["corrected"]))
            for v in m["by_vendor"][:VENDORS_SHOWN]
            if v["lines"]
        ],
        "corrections": corrections,
        "ai_measured": measured,
        "memory": memory,
        "memory_total": len(rows),
        "memory_matching": len(matching),
        "mem_q": mem_q,
    }


# --- Supplier learning ------------------------------------------------------------------------------------------


def suppliers_context(store: Store) -> dict[str, Any]:
    from ap_coder.capture.supplier import (
        HELD,
        SUPERVISED,
        autonomy_status,
        clean_invoices_to_go,
        judged_stats,
        to_go_text,
    )

    profiles = store.list_supplier_profiles()
    policy = store.autonomy_policy()
    on = store.touchless_enabled()
    out = []
    for p in profiles:
        stats = store.supplier_stats(p["key"], policy.window)
        stored = p["state"] or SUPERVISED
        state, progress, why = autonomy_status(stats, policy, stored, p["autonomous_since"], touchless_on=on,
                                               suspended_at=p.get("suspended_at"))  # fmt: skip
        name = p["display_name"] or p["key"]
        recent = stats.history[:RECENT]
        corrected = sum(1 for h in recent if h["corrections"])
        judged = judged_stats(stats, stored, p.get("suspended_at"))
        to_go = clean_invoices_to_go(judged, policy)
        parts = [
            ui.plural(stats.invoices, "invoice") + " reviewed",
            f"{ui.plural(corrected, 'correction')} in the last {len(recent)}" if recent else "no corrections yet",
        ]
        if state in ("learning", "supervised"):  # one number, said once (B-508): not the bar's parts again
            said = to_go_text(to_go, done=judged.clean_streak)
            why = said[:1].upper() + said[1:] + "."
        why = re.sub(r", accuracy at least [\d.]+%", "", why)  # a statistic the tester can't act on (B-508)
        fields = [
            {"label": FIELD_LABELS.get(f, f), "n": fs.n, "corrected": fs.n - fs.correct,
             "accuracy": f"{fs.accuracy:.0%}", "tone": "ok" if fs.correct == fs.n else "warn"}
            for f, fs in sorted(stats.fields.items(), key=lambda kv: (kv[1].accuracy or 0, kv[0]))
        ]  # fmt: skip
        latest = []
        if stats.fields:
            latest = [
                {"label": FIELD_LABELS.get(r["field"], r["field"]),
                 "read": r["ai_value"] if r["ai_value"] is not None else "(blank)",
                 "final": r["final_value"] if r["final_value"] is not None else "(blank)", "at": str(r["at"])[:10]}
                for r in [r for r in store.supplier_outcomes(p["key"], limit=500) if not r["correct"]][:5]
            ]  # fmt: skip
        out.append({
            "key": p["key"], "name": name, "state": state, "held": p["state"] == HELD, "invoices": stats.invoices,
            "row": built(ui.supplier_row(name, " · ".join(parts), state, progress, why)), "fields": fields,
            "window": ui.plural(stats.window_invoices, "reviewed invoice"), "latest": latest,
            "has_layout": bool(p.get("template")),
        })  # fmt: skip
    out.sort(key=lambda r: (_STATE_ORDER.get(r["state"], 9), -r["invoices"], r["name"].lower()))
    from ap_coder.web.pages.settings_automation import typical_to_go

    return {"suppliers": out, "touchless_on": on, "policy": policy, "typical": typical_to_go(store)}


# --- Readers ------------------------------------------------------------------------------------------------------


def readers_context(store: Store, pick: str | None) -> dict[str, Any]:
    from ap_coder.capture.confidence import MIN_EVIDENCE

    scores = added(store.reader_scorecard(), lambda r: reader_label(r["reader"]))
    context: dict[str, Any] = {"scores": None, "training": training_context(store)}
    if not scores:
        return context
    labels = sorted(scores, key=lambda label: (_READER_ORDER.index(label) if label in _READER_ORDER else 99, label))
    combined = scores.get(READER_LABELS["fused"])
    verified = next((s for s in store.fused_status_scorecard() if s["status"] == "verified"), None)
    tiles = []
    if combined:
        tiles.append(ui.tile("Combined value agreed with AP", f"{combined['rate']:.1%}", tone="green",
                             hint=f"{ui.plural(combined['fields'], 'field')} on "
                             f"{ui.plural(combined['invoices'], 'invoice')}"))  # fmt: skip
    tiles.append(
        ui.tile("Verified values right", f"{verified['rate']:.1%}", tone="green",
                hint=f"{ui.plural(verified['fields'], 'value')} shown as verified")
        if verified
        else ui.tile("Verified values right", "—", hint="no value shown as verified yet")
    )  # fmt: skip
    # Vendor PDFs (their text inside) and scans or photos (read with OCR) apart: a phone photo now and then should
    # not hide how well the vendor PDFs, most of what comes in, are read.
    kinds = store.fused_kind_scorecard()
    for key, title in (("text", "Vendor PDFs read right"), ("scan", "Scans and photos read right")):
        k = kinds.get(key)
        tiles.append(
            ui.tile(title, f"{k['rate']:.1%}", tone="green",
                    hint=f"{ui.plural(k['fields'], 'field')} on {ui.plural(k['invoices'], 'invoice')}")
            if k and k["rate"] is not None
            else ui.tile(title, "—", hint="none approved yet")
        )  # fmt: skip
    pick = pick if pick in scores else labels[0]
    fields = added([row for reader in scores[pick]["readers"] for row in store.reader_field_scorecard(reader)],
                   lambda r: r["field"])  # fmt: skip
    patterns = sum(1 for n, _ in store.evidence_counts().values() if n >= MIN_EVIDENCE)
    context.update(
        scores=[
            {
                "label": label,
                "combined": label == READER_LABELS["fused"],
                **scores[label],
                "rate_text": rate_text(scores[label]["rate"]),
            }
            for label in labels
        ],  # fmt: skip
        tiles=[built(tile) for tile in tiles],
        labels=labels,
        pick=pick,
        fields=[
            {"label": FIELD_LABELS.get(field, field), **f, "rate_text": rate_text(f["rate"])}
            for field, f in sorted(fields.items(), key=lambda kv: (kv[1]["rate"], kv[0]))
        ],
        patterns=patterns,
        min_evidence=MIN_EVIDENCE,
    )
    return context


def training_context(store: Store) -> dict[str, Any]:
    from ap_coder.ai.training_export import training_invoices

    picked = training_invoices(store)
    invoices = picked["invoices"]
    left_out = []  # approved invoices a training set never includes, and why
    if picked["demo"] or picked["unreviewed"]:
        left_out.append("Demo invoices and invoices approved without a person are never included.")
    if picked["bulk"]:
        n = picked["bulk"]
        left_out.append(f"{ui.plural(n, 'bulk-approved invoice')} {'is' if n == 1 else 'are'} left out: nobody opened "
                        f"{'it' if n == 1 else 'them'}.")  # fmt: skip
    if not invoices:
        note = " ".join(["None to include yet.", *left_out]) if left_out else "Nothing approved yet."
        return {"count": 0, "note": note}
    missing = sum(1 for i in invoices if not Path(i["source_path"] or "").is_file())
    notes = [
        f"{ui.plural(len(invoices), 'approved invoice')}, each with its pages as images and the values AP approved: "
        "ready to fine-tune a vision model such as OvisOCR2 or Qwen 3.5 on your own suppliers' invoices."
    ]
    if missing:
        notes.append(f"{ui.plural(missing, 'invoice')} whose file is no longer on this computer will be left out.")
    return {"count": len(invoices), "note": " ".join(notes + left_out)}


# --- The page -----------------------------------------------------------------------------------------------------


@router.get("/learning", response_class=HTMLResponse)
def learning_page(request: Request) -> HTMLResponse:
    store = get_store(request)
    q = request.query_params
    developer = developer_tools(store)
    tabs = {k: v for k, v in TABS.items() if k != "readers" or developer}
    tab = q.get("tab") if q.get("tab") in TABS else "coding"
    context: dict[str, Any] = {
        "tab": tab,
        "tabs": tabs,
        "url": _url,
        "developer": developer,
        "teach": bool(q.get("teach")),
    }  # from Getting started: Teach from past coding open
    if tab == "coding":
        context.update(_history_context(store, q.get("history"), q.get("sheet")))
        context.update(coding_context(store, q.get("mem_q") or "", get_settings(request)))
    elif tab == "suppliers":
        context.update(suppliers_context(store))
    elif tab == "lessons":
        context.update(lessons_context(store))
    elif developer:
        context.update(readers_context(store, q.get("reader")))
    return render(request, "learning.html", "learning", **context)


def lessons_context(store: Store) -> dict[str, Any]:
    from ap_coder.ai import lessons

    rows = []
    listed = store.list_lessons(include_retired=True)
    history = store.lesson_history([lesson["situation"] for lesson in listed])
    for lesson in listed:
        what = lessons.FIELD_WORDS.get(lesson["field"], lesson["field"])
        if lesson["field"] in lessons.LINE_FIELDS:
            noun = "Cost center" if lesson["field"] == lessons.LINE_CC else "GL account"
            what = f"{noun} of lines like “{lesson['pattern']}”"
        rows.append({**lesson, "what": what[:1].upper() + what[1:], "reaches": lessons.reaches_ai(lesson),
                     "effect": lessons.effect(history.get(lesson["situation"]) or [], lesson)})  # fmt: skip
    return {"lessons": rows, "why_after": lessons.why_after(store)}


@router.post("/learning/lessons/explain")
def lesson_explain(request: Request, situation: str = Form(...), explanation: str = Form("")) -> JSONResponse:
    store = get_store(request)
    if not store.lesson(situation):
        return refuse("That lesson is no longer there: reload the page.", 404)
    if not explanation.strip():
        return refuse("Write the reason first.")
    store.explain_lesson(situation, explanation, actor=reviewer())
    return done(request, "/learning?tab=lessons", "Reason kept: the AI reads it with this vendor's next invoices.")


@router.post("/learning/lessons/retire")
def lesson_retire(request: Request, lesson_id: int = Form(...), retired: str = Form("1")) -> JSONResponse:
    store = get_store(request)
    if not store.retire_lesson(lesson_id, actor=reviewer(), retired=retired == "1"):
        return refuse("That lesson is no longer there: reload the page.", 404)
    return done(request, "/learning?tab=lessons", "Retired: not shown to the AI again." if retired == "1"
                else "Restored: the AI reads it again.")  # fmt: skip


@router.post("/learning/suppliers/hold")
def supplier_hold(request: Request, key: str = Form(...), reason: str = Form("")) -> JSONResponse:
    """Keep a vendor supervised: every invoice goes to a person, whatever its record, until allowed again."""
    from ap_coder.capture.supplier import HELD

    store = get_store(request)
    profile = store.get_supplier_profile(key)
    if profile is None:
        return refuse("That vendor is no longer in AP Coder's supplier learning.", 404)
    name = profile["display_name"] or key
    store.set_supplier_state(key, HELD, reviewer(), reason=reason.strip() or "kept supervised on the Learning page")
    return done(request, "/learning?tab=suppliers", f"{name} is kept supervised: every invoice is reviewed.")


@router.post("/learning/suppliers/allow")
def supplier_allow(request: Request, key: str = Form(...)) -> JSONResponse:
    from ap_coder.capture.supplier import SUPERVISED

    store = get_store(request)
    profile = store.get_supplier_profile(key)
    if profile is None:
        return refuse("That vendor is no longer in AP Coder's supplier learning.", 404)
    name = profile["display_name"] or key
    store.set_supplier_state(key, SUPERVISED, reviewer(), reason="allowed again on the Learning page")
    return done(request, "/learning?tab=suppliers", f"{name} can skip review again once it meets the bar.")


@router.post("/learning/suppliers/reset-layout")
def supplier_reset_layout(request: Request, key: str = Form(...)) -> JSONResponse:
    """Forget a vendor's layout (what its template learned): its next invoices are read as a new vendor's (D-71)."""
    store = get_store(request)
    profile = store.get_supplier_profile(key)
    if profile is None or not store.reset_supplier_layout(key, reviewer()):
        return refuse("That vendor is no longer in AP Coder's supplier learning.", 404)
    name = profile["display_name"] or key
    return done(request, "/learning?tab=suppliers",
                f"{name}'s layout is reset: its next invoices are read as a new vendor's, and approving them teaches "
                "it again.")  # fmt: skip


@router.post("/learning/memory/forget")
async def memory_forget(request: Request) -> JSONResponse:
    try:  # one field per lesson ticked, however many (B-461)
        ids = [int(str(i)) for i in (await posted_form(request)).getlist("ids")]
    except ValueError:
        ids = []
    if not ids:
        return refuse("Tick the lines to forget first.")
    n = get_store(request).delete_feedback([int(i) for i in ids], actor=reviewer())
    return done(request, "/learning#memory", f"Forgot {ui.plural(n or len(ids), 'line')} from the coding memory.")


@router.post("/learning/history/upload")
async def history_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    name = Path(file.filename or "history.csv").name
    if not name.lower().endswith((".csv", ".xlsx", ".xls", ".txt")):
        return refuse("Upload the AP line history as a CSV or an Excel workbook (.xlsx).")
    content = await read_capped(file)  # never read whole, however large (B-1401)
    if content is None:
        return refuse(TOO_BIG)
    try:
        sheets = read_sheets(name, content)
    except ValueError as exc:
        return refuse(str(exc))
    token = keep_upload(name, sheets)
    return done(request, _url(history=token) + "#history", flash_message_for_columns(name, sheets))


def flash_message_for_columns(name: str, sheets: dict[str, tuple[list[str], list[dict[str, Any]]]]) -> str:
    rows = sum(len(r) for _, r in sheets.values())
    return f"Read {name}: {ui.plural(rows, 'line')}. Check the columns below, then teach."


@router.post("/learning/history/teach")
async def history_teach(request: Request) -> JSONResponse:
    form = await posted_form(request)
    token = str(form.get("token") or "")
    upload = get_upload(token)
    if upload is None:
        return refuse("That upload has expired. Upload the file again.")
    _, sheets = upload
    sheet = str(form.get("sheet") or "")
    columns, records = sheets[sheet] if sheet in sheets else next(iter(sheets.values()))
    chosen = {}
    for field in HISTORY_LABELS:
        pick = str(form.get(f"col_{field}") or "")
        if pick and pick in columns:
            chosen[field] = pick
    missing = [HISTORY_LABELS[f].rstrip(" *") for f in history.REQUIRED if f not in chosen]
    if missing:
        return refuse("Pick the column for: " + ", ".join(missing))
    store = get_store(request)
    rows, skipped = history.rows_from_records(records, chosen)
    result = store.import_history(rows, actor=reviewer())
    drop_upload(token)
    text = (
        f"Taught {ui.plural(result['added'], 'past line')}"
        + (f"; {result['skipped'] + skipped:,} skipped (incomplete or already taught)"
           if result["skipped"] + skipped else "")
        + (f"; {result['unknown_gl']:,} with a GL account that is not in your list" if result["unknown_gl"] else "")
        + "."
    )  # fmt: skip
    return done(request, "/learning#history", text)


@router.post("/learning/history/forget")
def history_forget(request: Request) -> JSONResponse:
    n = get_store(request).forget_history(actor=reviewer())
    return done(request, "/learning#history", f"Forgot {ui.plural(n, 'past line')}.")


@router.get("/learning/training.zip")
def training_zip(request: Request) -> Response:
    """The training set as a ZIP, made on this computer for the approvals as they are now (each approved invoice's
    pages as images, with the values AP approved). Spooled to a temporary file, so a large set is not held in
    memory."""
    from ap_coder.ai.training_export import export_training_set

    store = get_store(request)
    if not developer_tools(store):  # supplier invoices in a ZIP: a developer tool, not the pilot's (B-511)
        raise HTTPException(status_code=404, detail="The training data export is a developer tool.")
    spool = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)  # noqa: SIM115 - closed after streaming
    export_training_set(store, spool)
    size = spool.tell()
    spool.seek(0)

    def chunks() -> Any:
        try:
            while block := spool.read(1024 * 1024):
                yield block
        finally:
            spool.close()

    filename = f"ap-coder-training-{dt.date.today().isoformat()}.zip"
    return StreamingResponse(
        chunks(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Content-Length": str(size)},
    )
