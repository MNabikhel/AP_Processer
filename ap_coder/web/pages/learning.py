"""Learning & accuracy, in four tabs (``?tab=coding|suppliers|lessons|readers``; Lessons: corrections per situation):

* Coding accuracy: first the corrections AP made and whether AP Coder had each right the next time; then accuracy
  against the 90% target, by week (an SVG line), recent lessons, accuracy by vendor, the
  most common corrections, the memory (every lesson, with the option to forget some) and teaching from past coding
  (an ERP export of AP lines: upload, match the columns, teach; JD Edwards' Transaction Journal is recognised and
  goes its own way: its summary first, then "Load this journal", each loaded journal listed with "Forget").
* Supplier learning: what each vendor's reviewed invoices taught, the fields the clerk corrected, how far it is from
  going touchless, and an AP lead's "Keep supervised" / "Allow again" (recorded in the Activity log with their name).
* Readers (a developer tool, shown with Settings → About's "Show developer tools" or AP_DEVELOPER_TOOLS=1, B-511):
  each capture reader against what AP approved (scorecard, field by field) and the training data export (a ZIP of
  page images and approved values, a GET download made on this computer).

Every change is a POST answered with where to go next; the reader picked, the memory search and the uploaded history
waiting for its columns are in the address.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import re
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pandas as pd
from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from markupsafe import Markup

from ap_coder.ai import history
from ap_coder.core import ui
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.store import Store, reports
from ap_coder.erp import insights
from ap_coder.erp.import_specs import PAST_CODING
from ap_coder.erp.journal import looks_like_journal
from ap_coder.ops import journal_cmd
from ap_coder.web.charts import SERIES, Series, line_chart, percent
from ap_coder.web.core import (
    TARGET_ACCURACY,
    accuracy_note,
    current_store,
    developer_tools,
    done,
    get_settings,
    get_store,
    one_connection,
    page_router,
    posted_form,
    reference_or_none,
    refuse,
    render,
    reviewer,
    shown_count,
)
from ap_coder.web.files import (
    GONE,
    PREVIEW_ROWS,
    TOO_BIG,
    SheetError,
    Upload,
    cannot_read,
    pick_refusal,
    picked_columns,
    read_capped,
    read_csv,
    read_excel,
    spec_sheet,
)
from ap_coder.web.invoice_grid import gl_name

router = page_router()  # each plain handler: one connection a request

TABS = {"coding": "Coding accuracy", "suppliers": "Supplier learning", "lessons": "Lessons", "readers": "Readers"}
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
# Seconds the Coding accuracy tab's figures and corrections may be behind the stream of approvals (D-164, D-179): read
# from every lesson (1.1 million at 300,000 invoices), worked out once for every process (``_settled``). The lessons
# forgotten or taught on this page show at once; the memory list is never settled.
SETTLE_SECONDS = 60.0
SETTLED_FORMAT = "learning-1"  # what is kept for every process; another when that changes
SETTLES_AFTER = ("lessons_forgotten", "history_imported")  # the page's own changes: each another question
OUTCOMES = {
    "accepted": "✓ confirmed",
    "corrected": "✎ corrected",
    "coded": "✎ coded by AP (the AI only guessed)",
    "coded_blank": "✎ coded by AP (it had no account)",
    "bulk": "✓ approved in bulk, as proposed",
    "history": "⟲ ERP history",
}


# --- Small helpers --------------------------------------------------------------------------------------------------


def outcome_of(row: dict[str, Any]) -> str:
    """A memory row's outcome as Learning words it: AP's own coding of a line the AI left without an account is
    "coded_blank", not a kept guess (B-1412)."""
    if row["outcome"] == "coded" and (row.get("suggested_gl") or "") in ("", UNASSIGNED):
        return "coded_blank"
    return row["outcome"]


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
        raise ValueError(cannot_read(data)) from exc
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


# --- JD Edwards' Transaction Journal, uploaded and waiting to be loaded ------------------------------------------
# The file is kept as it came under a one-off token, as a table's upload is: its summary is worked out from the
# bytes at each view (with the objects in the address, when the boxes were changed), and nothing is written until
# "Load this journal".

JOURNAL_KIND = "ap-journal"
JOURNAL_LABELS = {"trade": "Trade payable objects", "intercompany": "Intercompany payable objects",
                  "tax": "Tax objects"}  # fmt: skip
JOURNAL_NOTES = {journal_cmd.SAME_FILE: "Loaded already", journal_cmd.DIFFERS: "The control total differs",
                 journal_cmd.REFUSED: "This file cannot be loaded as it is"}  # fmt: skip


def keep_journal(name: str, data: bytes) -> str:
    kept = name.encode("utf-8") + b"\0" + data
    return current_store().keep_ephemeral(JOURNAL_KIND, kept, _UPLOAD_TTL, keep=_MAX_UPLOADS)


def get_journal(token: str | None) -> tuple[str, bytes] | None:
    kept = current_store().ephemeral(JOURNAL_KIND, token)
    if kept is None:
        return None
    name, _, data = kept.partition(b"\0")
    return name.decode("utf-8"), data


def _outline(said: tuple[str, ...]) -> list[dict[str, Any]]:
    """The summary's lines as a list with the indented ones under the line before them (two levels)."""
    found: list[dict[str, Any]] = []
    for line in said:
        depth = (len(line) - len(line.lstrip())) // 2
        item = {"text": line.strip(), "sub": []}
        if depth == 0 or not found:
            found.append(item)
        elif depth == 1 or not found[-1]["sub"]:
            found[-1]["sub"].append(item)
        else:
            found[-1]["sub"][-1]["sub"].append(item)
    return found


def _journal_context(store: Store, token: str | None, q: Any) -> dict[str, Any]:
    """The journals loaded, and the uploaded one's summary (``journal_cmd.look``: the same as the command's)."""
    loaded = [{**f, "when": f["loaded_at"].replace("T", " ")[:16]} for f in store.journal_files()]
    context: dict[str, Any] = {"journals": loaded, "journal": None}
    kept = get_journal(token)
    if token and kept is None:
        context["upload_gone"] = True
    if kept:
        name, data = kept
        seen = journal_cmd.look(store, data, name, **{key: q.get(key) for key in JOURNAL_LABELS})
        context["journal"] = {
            "token": token, "name": name, "said": _outline(seen.said), "state": seen.state, "problem": seen.problem,
            "note": JOURNAL_NOTES.get(seen.state, ""), "labels": JOURNAL_LABELS, "read": seen.journal is not None,
            "objects": {key: ", ".join(seen.objects[key]) for key in JOURNAL_LABELS}, "brief": seen.brief,
            "size": f"{ui.plural(seen.journal.totals.lines, 'line')} on "
                    f"{ui.plural(seen.journal.totals.vouchers, 'voucher')}" if seen.journal else "",
            "already": seen.state == journal_cmd.SAME_FILE,
        }  # fmt: skip
    return context


# --- Coding accuracy ----------------------------------------------------------------------------------------------


def _history_context(store: Store, token: str | None, sheet: str | None) -> dict[str, Any]:
    upload = get_upload(token)
    context: dict[str, Any] = {"history_count": store.history_count(), "upload": None, "history_spec": PAST_CODING}
    if token and upload is None:
        context["upload_gone"] = True
    if upload:
        name, sheets = upload
        sheet = sheet if sheet in sheets else next(iter(sheets))
        columns, rows = sheets[sheet]
        head = pd.DataFrame(rows[:PREVIEW_ROWS], columns=columns)  # (the choices and the first rows: spec_sheet)
        picked = spec_sheet(token, Upload(name, b""), head, list(sheets), sheet, PAST_CODING)
        picked["count"] = picked["look"]["count"] = len(rows)
        context["upload"] = {"token": token, "name": name, "sheets": list(sheets), "sheet": sheet, "rows": len(rows),
                             "s": picked, **_unknown_gl(store, rows, picked)}  # fmt: skip
    return context


def _unknown_gl(store: Store, records: list[dict[str, Any]], picked: dict[str, Any]) -> dict[str, Any]:
    """How many of the lines, read with the columns as picked, have a GL account not in the GL accounts listed
    (``unknown_gl``; those are not taught, ``Store.import_history``), and the column it is read from (``gl_column``)."""
    chosen = {f["key"]: f["value"] for f in picked["fields"] if f["value"]}
    known = {a["code"] for a in store.list_accounts("gl_accounts")}
    if not known or "gl_code" not in chosen:
        return {"unknown_gl": 0}
    rows, _skipped = history.rows_from_records(records, chosen)
    return {"unknown_gl": sum(1 for row in rows if row["gl_code"] not in known), "gl_column": chosen["gl_code"]}


CORRECTIONS_SHOWN = 50
NEXT_TIME = {
    "right": ("Right the next time", "ok"),
    "again": ("Corrected again", "warn"),
    "": ("Not seen since", "gray"),
}


def _settled(store: Store, name: str, compute: Callable[[], Any], decode: Callable[[Any], Any] = lambda v: v, *,
             also: Any = None) -> Any:  # fmt: skip
    """``compute()`` as the Coding accuracy tab shows it: worked out once for every process (D-169) on any change of
    the invoices or the lessons, and under a stream of approvals at most every ``SETTLE_SECONDS`` (D-164); a lesson
    forgotten or taught here (``SETTLES_AFTER``) is another question, worked out at once, as is one of ``also`` (what
    else it is worked out from that no count of changes follows); on the company server by its background process
    (``SETTLED``, D-198). Shared: read it, don't change it."""
    asked = (store.count_events(actions=SETTLES_AFTER), also) if also is not None else store.count_events(
        actions=SETTLES_AFTER)  # fmt: skip
    return store.shared_answer(name, compute, version=SETTLED_FORMAT, decode=decode, changes=("lessons", "situations"),
                               asked=asked, settle=SETTLE_SECONDS, again=[])  # fmt: skip


def _corrections(store: Store) -> list[dict[str, Any]]:
    corrected = store.line_texts_stamp()  # (the line descriptions corrected: no count follows them, B-1839)
    return _settled(store, "learning_corrections", lambda: store.corrections(limit=CORRECTIONS_SHOWN), also=corrected)


def _metrics(store: Store) -> dict[str, Any]:
    return _settled(store, "learning_metrics", store.metrics)


def _approvals(store: Store) -> tuple[int, int, int]:
    return _settled(store, "learning_approvals", lambda: insights.approval_counts(store), tuple)


# The tab's settled answers by name, as the background process works them out again (web/refresh.py, D-198).
SETTLED = {"learning_corrections": _corrections, "learning_metrics": _metrics, "learning_approvals": _approvals}


def corrections_context(store: Store) -> dict[str, Any]:
    """Corrections, the first thing on the Coding accuracy tab: each correction AP made (an invoice's field, a line's
    GL account, cost center, tax code or description), what AP Coder proposed and what AP approved, and whether AP
    Coder had it right the next time (``Store.corrections``, settled: ``_settled``)."""
    from ap_coder.ai import lessons

    reference = reference_or_none(store)
    rows = []
    for c in _corrections(store):
        field, pattern = c["field"], c["pattern"]
        if field == lessons.LINE_GL:
            what = f"GL account of lines like “{pattern}”"
        elif field == lessons.LINE_CC:
            what = f"Cost center of lines like “{pattern}”"
        elif field == "tax_code":
            what = f"Tax code of “{short(pattern, 60)}”"
        elif field == "line_description":
            what = "Description of a line, as read"
        else:
            what = lessons.FIELD_WORDS.get(field, field)
        names = {"proposed": "", "value": "", "next_value": ""}
        if field == lessons.LINE_GL:
            names = {k: gl_name(reference, c[k]) if c[k] else "" for k in names}
        text, tone = NEXT_TIME[c["next"]]
        rows.append({**c, "what": what[:1].upper() + what[1:], "names": names, "when": str(c["at"] or "")[:10],
                     "next_text": text, "next_tone": tone, "proposed_text": "" if c["proposed"] in ("", UNASSIGNED)
                     else c["proposed"], "next_when": str(c["next_at"] or "")[:10]})  # fmt: skip
    seen = [r for r in rows if r["next"]]
    right = sum(r["next"] == "right" for r in seen)
    if not rows:
        summary = ""
    elif not seen:
        summary = "None of them has come up again yet."
    else:
        summary = (f"Of the {ui.plural(len(seen), 'correction')} that came up again, AP Coder had "
                   f"{right} right the next time.")  # fmt: skip
    return {"fixes": rows, "fixes_summary": summary, "fixes_shown": CORRECTIONS_SHOWN}


def coding_context(store: Store, mem_q: str, settings: Any = None) -> dict[str, Any]:
    """The Coding accuracy tab but its corrections: the tiles, the chart by week, the recent lessons, the vendors, the
    most common corrections and the memory list (``mem_q``: its search). Its figures settle (``_settled``)."""
    m = _metrics(store)
    if not m["lines_reviewed"]:
        return {"metrics": None}
    measured = m["line_accuracy"] is not None  # none: AP coded every line, the AI proposed none (no model, B-1298)
    accuracy = m["line_accuracy"] or 0.0
    clean, total, filled = _approvals(store)  # as Insights counts them (D-39), by SQLite (D-156)
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
                insights.as_coded_note(clean, filled, total)),
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
        {**r, "short": short(r["description"]), "when": ui.time_ago(r["created_at"]),
         "blank": outcome_of(r) == "coded_blank"} for r in reviewed[:FEED]
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
        {**r, "date": str(r["created_at"] or "")[:10], "outcome_text": OUTCOMES.get(outcome_of(r), r["outcome"])}
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


SUPPLIERS_SHOWN = 50  # the vendors Supplier learning lists at first, and how many more each time ("Show more")


def suppliers_context(store: Store, shown: int = SUPPLIERS_SHOWN) -> dict[str, Any]:
    """Every vendor's state, in the order the page lists them (to act on first, then the most invoices), every
    vendor's record worked out in a few queries; the first ``shown`` drawn (D-157)."""
    from ap_coder.capture.supplier import (
        HELD,
        SUPERVISED,
        autonomy_status,
        clean_invoices_to_go,
        judged_stats,
        to_go_text,
    )

    profiles = store.supplier_profiles_brief()
    policy = store.autonomy_policy()
    on = store.touchless_enabled()
    every = store.all_supplier_stats([p["key"] for p in profiles], policy.window, fields=False)
    listed = []
    for p in profiles:
        stats = every[p["key"]]
        state, progress, why = autonomy_status(stats, policy, p["state"] or SUPERVISED, p["autonomous_since"],
                                               touchless_on=on, suspended_at=p.get("suspended_at"))  # fmt: skip
        listed.append((p, stats, state, progress, why, p["display_name"] or p["key"]))
    listed.sort(key=lambda s: (_STATE_ORDER.get(s[2], 9), -s[1].invoices, s[5].lower()))
    each = store.supplier_fields([s[0]["key"] for s in listed[:shown]], policy.window)  # the vendors drawn's
    corrections = store.latest_corrections([key for key, fields in each.items() if fields])
    out = []
    for p, stats, state, progress, why, name in listed[:shown]:
        stats = replace(stats, fields=each[p["key"]])
        stored = p["state"] or SUPERVISED
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
        latest = [
            {"label": FIELD_LABELS.get(r["field"], r["field"]),
             "read": r["ai_value"] if r["ai_value"] is not None else "(blank)",
             "final": r["final_value"] if r["final_value"] is not None else "(blank)", "at": str(r["at"])[:10]}
            for r in corrections.get(p["key"], [])
        ]  # fmt: skip
        out.append({
            "key": p["key"], "name": name, "state": state, "held": p["state"] == HELD, "invoices": stats.invoices,
            "row": built(ui.supplier_row(name, " · ".join(parts), state, progress, why)), "fields": fields,
            "window": ui.plural(stats.window_invoices, "reviewed invoice"), "latest": latest,
            "has_layout": bool(p["has_layout"]),
        })  # fmt: skip
    from ap_coder.web.pages.settings_automation import typical_to_go

    return {"suppliers": out, "suppliers_total": len(listed), "suppliers_step": SUPPLIERS_SHOWN, "shown": shown,
            "touchless_on": on, "policy": policy, "typical": typical_to_go(store)}  # fmt: skip


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
        # Teach from past coding is never replaced by "being worked out": the accuracy beside it waited for as
        # anywhere (B-2060); the tab alone may say so (D-201)
        waited = context["teach"] or q.get("history") or q.get("journal")
        with reports.as_anywhere() if waited else contextlib.nullcontext():
            context.update(corrections_context(store))
            context.update(_history_context(store, q.get("history"), q.get("sheet")))
            context.update(_journal_context(store, q.get("journal"), q))
            context.update(coding_context(store, q.get("mem_q") or "", get_settings(request)))
    elif tab == "suppliers":
        context.update(suppliers_context(store, shown_count(q.get("shown"), SUPPLIERS_SHOWN)))
    elif tab == "lessons":
        context.update(lessons_context(store))
    elif developer:
        context.update(readers_context(store, q.get("reader")))
    return render(request, "learning.html", "learning", **context)


def lessons_context(store: Store) -> dict[str, Any]:
    from ap_coder.ai import lessons

    # Kept until a lesson or an approval's word on one changes (``changes/situations``, D-162): read, not changed.
    rows = store.summary_memo("lessons_tab", lambda: lesson_rows(store), topic="", changes="situations")
    return {"lessons": rows, "why_after": lessons.why_after(store)}


def lesson_rows(store: Store) -> list[dict[str, Any]]:
    """The Lessons tab's rows (``Store.list_lessons``: 500 at most, the most corrected since its reason first): what
    each is about, whether it reaches the AI and how it did since (``lessons.effect``)."""
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
    return rows


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


def _suppliers_url(shown: str) -> str:
    """Supplier learning with as many vendors listed as when a vendor was acted on: one past the first stays in view."""
    listed = shown_count(shown, SUPPLIERS_SHOWN)
    return "/learning?tab=suppliers" + (f"&shown={listed}" if listed > SUPPLIERS_SHOWN else "")


@router.post("/learning/suppliers/hold")
def supplier_hold(request: Request, key: str = Form(...), reason: str = Form(""),
                  shown: str = Form("")) -> JSONResponse:  # fmt: skip
    """Keep a vendor supervised: every invoice goes to a person, whatever its record, until allowed again."""
    from ap_coder.capture.supplier import HELD

    store = get_store(request)
    profile = store.get_supplier_profile(key)
    if profile is None:
        return refuse("That vendor is no longer in AP Coder's supplier learning.", 404)
    name = profile["display_name"] or key
    store.set_supplier_state(key, HELD, reviewer(), reason=reason.strip() or "kept supervised on the Learning page")
    return done(request, _suppliers_url(shown), f"{name} is kept supervised: every invoice is reviewed.")


@router.post("/learning/suppliers/allow")
def supplier_allow(request: Request, key: str = Form(...), shown: str = Form("")) -> JSONResponse:
    from ap_coder.capture.supplier import SUPERVISED

    store = get_store(request)
    profile = store.get_supplier_profile(key)
    if profile is None:
        return refuse("That vendor is no longer in AP Coder's supplier learning.", 404)
    name = profile["display_name"] or key
    store.set_supplier_state(key, SUPERVISED, reviewer(), reason="allowed again on the Learning page")
    return done(request, _suppliers_url(shown), f"{name} can skip review again once it meets the bar.")


@router.post("/learning/suppliers/reset-layout")
def supplier_reset_layout(request: Request, key: str = Form(...), shown: str = Form("")) -> JSONResponse:
    """Forget a vendor's layout (what its template learned): its next invoices are read as a new vendor's (D-71)."""
    store = get_store(request)
    profile = store.get_supplier_profile(key)
    if profile is None or not store.reset_supplier_layout(key, reviewer()):
        return refuse("That vendor is no longer in AP Coder's supplier learning.", 404)
    name = profile["display_name"] or key
    return done(request, _suppliers_url(shown),
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
    return await run_in_threadpool(_memory_forget, request, ids)


@one_connection
def _memory_forget(request: Request, ids: list[int]) -> JSONResponse:
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
    return await run_in_threadpool(_history_upload, request, name, content)


@one_connection
def _history_upload(request: Request, name: str, content: bytes) -> JSONResponse:
    if not name.lower().endswith((".xlsx", ".xls")) and looks_like_journal(content):
        # JD Edwards' Transaction Journal is a printed report, not a table: its own summary, then its own load
        said = f"Read {name}: JD Edwards' Transaction Journal. Check the summary below, then load it."
        return done(request, _url(journal=keep_journal(name, content)) + "#history", said)
    return _table_upload(request, name, content)


def _table_upload(request: Request, name: str, content: bytes) -> JSONResponse:
    """A table of past lines read and kept, waiting for its columns: the page's verdict says what was found (B-2471)."""
    try:
        sheets = read_sheets(name, content)
    except ValueError as exc:
        return refuse(str(exc))
    token = keep_upload(name, sheets)
    return done(request, _url(history=token) + "#history")


@router.post("/learning/history/teach")
async def history_teach(request: Request) -> JSONResponse:
    form = await posted_form(request)
    return await run_in_threadpool(_history_teach, request, form)  # years of ERP lines at once


@one_connection
def _history_teach(request: Request, form: Any) -> JSONResponse:
    token = str(form.get("token") or "")
    upload = get_upload(token)
    if upload is None:
        return refuse(GONE)
    _, sheets = upload
    sheet = str(form.get("sheet") or "")
    columns, records = sheets[sheet] if sheet in sheets else next(iter(sheets.values()))
    chosen = picked_columns(form, history.COLUMN_ALIASES, columns, {})
    if refusal := pick_refusal(PAST_CODING, chosen):
        return refuse(refusal)
    store = get_store(request)
    rows, skipped = history.rows_from_records(records, chosen)
    result = store.import_history(rows, actor=reviewer())
    drop_upload(token)
    text = (
        f"Loaded: {ui.plural(result['added'], 'past line')} added"
        + (f"; {result['skipped'] + skipped:,} skipped (incomplete or already loaded)"
           if result["skipped"] + skipped else "")
        + (f"; {result['unknown_gl']:,} with a GL account that is not in your list" if result["unknown_gl"] else "")
        + (f"; {result['unknown_cc']:,} kept without their cost center, which is not in your list"
           if result.get("unknown_cc") else "")
        + "."
    )  # fmt: skip
    return done(request, "/learning#history", text)


@router.post("/learning/history/forget")
def history_forget(request: Request) -> JSONResponse:
    n = get_store(request).forget_history(actor=reviewer())
    return done(request, "/learning#history", f"Forgot {ui.plural(n, 'past line')}.")


@router.post("/learning/journal/load")
async def journal_load(request: Request) -> JSONResponse:
    form = await posted_form(request)  # (read whole: a box left empty says "none", one not sent "work it out")
    return await run_in_threadpool(_journal_load, request, form)


@one_connection
def _journal_load(request: Request, form: Any) -> JSONResponse:
    """Load the uploaded journal with the objects its summary was shown with (a year: a second or two)."""
    token = str(form.get("token") or "")
    kept = get_journal(token)
    if kept is None:
        return refuse(GONE)
    name, data = kept
    given = {key: form.get(key) for key in JOURNAL_LABELS}
    outcome, said = journal_cmd.load(get_store(request), data, name, **given, anyway=bool(form.get("anyway")),
                                     actor=reviewer())  # fmt: skip
    if outcome in (journal_cmd.REFUSED, journal_cmd.DIFFERS):
        return refuse(said[-1])
    current_store().drop_ephemeral(JOURNAL_KIND, token)
    return done(request, "/learning#history", said[-1], tone="ok" if outcome == journal_cmd.DONE else "warn")


@router.post("/learning/journal/as-table")
def journal_as_table(request: Request, token: str = Form("")) -> JSONResponse:
    """The uploaded file read as a table of past lines after all: a table whose header names an account number and
    a remark looks like the journal."""
    kept = get_journal(token)
    if kept is None:
        return refuse(GONE)
    answer = _table_upload(request, *kept)
    if answer.status_code == 200:
        current_store().drop_ephemeral(JOURNAL_KIND, token)
    return answer


@router.post("/learning/journal/forget")
def journal_forget(request: Request, file_id: str = Form(...)) -> JSONResponse:
    forgotten, said = journal_cmd.forget(get_store(request), file_id, actor=reviewer())
    if not forgotten:
        return refuse("That journal is not loaded any more.")
    return done(request, "/learning#history", said)


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
