"""AP Coder review dashboard (Streamlit). Runs locally: ``python -m ap_coder dashboard``.

Pages
-----
* Review queue: welcome banner, today's numbers and clickable invoice cards; opening one switches to
  a focused review (keyboard shortcuts, invoice summary, spend breakdown, live checks, editable lines
  and taxes, GL posting preview) with a sticky approve bar. Approving teaches the memory.
* Process invoices: setup checklist, upload or pick up new files in private/invoices.
* GL accounts & tax: import / view / edit / delete GL accounts (cost codes) and cost centers with
  your own category column, map each sales tax to its GL, edit coding policy notes.
* Learning & accuracy: accuracy vs target, trend, recent lessons, vendors, corrections and the memory.

Visual building blocks live in ``ui.py`` and ``assets/style.css``. All data stays in the local
SQLite database (``private/ap_coder.db`` by default).
"""

from __future__ import annotations

import datetime as dt
import getpass
import hashlib
import html
import io
import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ap_coder import ui
from ap_coder.config import Settings
from ap_coder.extraction import SUPPORTED_EXTENSIONS, ExtractionResult
from ap_coder.memory import ACCEPTED, pair_lines
from ap_coder.pipeline import InvoicePipeline, finalise_coding, invoice_files
from ap_coder.reference_data import UNASSIGNED, ReferenceData
from ap_coder.review import coding_from_inputs
from ap_coder.schema import PROVINCE_VALUES, InvoiceCoding
from ap_coder.store import APPROVED, FAILED, REJECTED, REVIEW, Store, default_db_path, load_sample_setup
from ap_coder.tax import PROVINCE_NAMES, TAX_TYPES, TREATMENTS, TaxRateTable

DB_PATH = Path(os.environ.get("AP_DB_PATH") or default_db_path())
INVOICE_DIR = DB_PATH.parent / "invoices"
CACHE_DIR = DB_PATH.parent / ".cache" / "extraction"
ASSETS = Path(__file__).resolve().parent / "assets"
TARGET_ACCURACY = 0.90
SERIES_BLUE = "#2a78d6"  # categorical slot 1 (dataviz reference palette)
TARGET_GRAY = "#8a8985"

st.set_page_config(page_title="AP Coder", page_icon=str(ASSETS / "icon.svg"), layout="wide")
st.html(f"<style>{(ASSETS / 'style.css').read_text(encoding='utf-8')}</style>")


# --- Shared helpers -----------------------------------------------------------------------------


@st.cache_resource
def get_store() -> Store:
    return Store(DB_PATH)


def get_settings() -> Settings:
    return Settings.from_env(os.environ.get("AP_ENV_FILE"))


def reviewer() -> str:
    for name in (st.session_state.get("reviewer"), os.environ.get("AP_REVIEWER"), getpass.getuser()):
        if name and str(name).strip():
            return str(name).strip()
    return "Reviewer"


def first_name() -> str:
    return reviewer().split()[0]


def money(value: Any, currency: str = "") -> str:
    try:
        return f"{float(value):,.2f}{' ' + currency if currency else ''}"
    except (TypeError, ValueError):
        return "-"


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


def short_path(path: Path) -> Path:
    """Show paths relative to the folder the dashboard was started from, when possible."""
    try:
        return path.resolve().relative_to(Path.cwd())
    except ValueError:
        return path


def reference_or_none(store: Store) -> ReferenceData | None:
    try:
        return store.reference_data()
    except ValueError:
        return None


def gl_label_map(reference: ReferenceData | None) -> dict[str, str]:
    labels = {UNASSIGNED: f"{UNASSIGNED} · needs a code", "": "(none)"}
    if reference is not None:
        for row in reference.chart_of_accounts.rows:
            name = row.get("description", "").split(" - ")[0]
            labels[row["gl_code"]] = f"{row['gl_code']} · {name[:40]}"
    return labels


def gl_name(reference: ReferenceData, code: str) -> str:
    row = reference.chart_of_accounts.get(code)
    return (row or {}).get("description", "").split(" - ")[0] if row else ""


def cc_label_map(reference: ReferenceData | None) -> dict[str, str]:
    labels = {UNASSIGNED: f"{UNASSIGNED} · needs a cost center", "": "(none)"}
    if reference is not None and reference.cost_centers is not None:
        for row in reference.cost_centers.rows:
            labels[row["cost_center"]] = f"{row['cost_center']} · {row.get('description', '')[:30]}"
    return labels


@st.cache_data(show_spinner=False)
def render_pages(path: str, mtime: float) -> list[bytes]:
    """PNG bytes per page (PDF via PyMuPDF, images via Pillow)."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import pymupdf

        with pymupdf.open(path) as doc:
            return [page.get_pixmap(dpi=120).tobytes("png") for page in doc]
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        from PIL import Image, ImageSequence

        pages = []
        with Image.open(path) as img:
            for frame in ImageSequence.Iterator(img):
                buf = io.BytesIO()
                frame.convert("RGB").save(buf, format="PNG")
                pages.append(buf.getvalue())
        return pages
    return []


def notify(message: str, icon: str = ":material/check_circle:") -> None:
    """Show a toast after the next rerun."""
    st.session_state.setdefault("toasts", []).append((message, icon))


def scroll_to_top_if_asked() -> None:
    """After approve / reject / next, start the newly opened page at the top, not where the button was.

    The element is drawn on every run (only its content changes) so the page's layout stays the same;
    adding and removing it confused Streamlit's clean-up of the previous page.
    """
    nonce = st.session_state.get("_scroll_nonce", 0)
    script = ""
    if st.session_state.pop("scroll_top", False):
        nonce += 1
        st.session_state["_scroll_nonce"] = nonce
        script = (
            "<script>for (const el of window.parent.document.querySelectorAll("
            '\'[data-testid="stMain"], [data-testid="stAppViewContainer"], section.main\')) el.scrollTo(0, 0);'
            "window.scrollTo(0, 0);</script>"
        )
    st.html(f"<span data-scroll='{nonce}' hidden></span>{script}", unsafe_allow_javascript=True)


def show_toast() -> None:
    scroll_to_top_if_asked()
    for message, icon in st.session_state.pop("toasts", []):
        st.toast(message, icon=icon)


def persistent_editor(data: pd.DataFrame, key: str, **kwargs: Any) -> pd.DataFrame:
    """``st.data_editor`` whose edits survive moving to another invoice or page and back.

    Streamlit forgets a widget's edits once it is not drawn. The last edited table is kept
    as a draft and becomes the starting point the next time the editor is created.
    """
    base_key, draft_key = f"_base_{key}", f"_draft_{key}"
    if key not in st.session_state:
        st.session_state[base_key] = st.session_state.get(draft_key, data)
    edited = st.data_editor(st.session_state[base_key], key=key, **kwargs)
    st.session_state[draft_key] = edited
    return edited


def forget_drafts(prefix: str) -> None:
    """Drop the saved drafts of a finished invoice."""
    for k in [k for k in st.session_state if str(k).startswith((f"_base_{prefix}_", f"_draft_{prefix}_"))]:
        del st.session_state[k]


def card(name: str) -> Any:
    """A bordered white card (the key gives it a stable CSS class: st-key-card_<name>)."""
    return st.container(border=True, key=f"card_{name}")


def approved_today(store: Store) -> int:
    today = dt.date.today().isoformat()
    return sum(1 for i in store.list_invoices(APPROVED) if (i["reviewed_at"] or "").startswith(today))


def weekly_accuracy(metrics: dict[str, Any]) -> list[float]:
    return [w["accepted"] / (w["accepted"] + w["corrected"]) for w in metrics["weekly"]]


# --- Review queue --------------------------------------------------------------------------------------


def page_review() -> None:
    store = get_store()
    show_toast()
    reference = reference_or_none(store)
    invoices = store.list_invoices()
    if reference is None or not invoices:
        st.html(ui.page_header("Welcome", f"{ui.greeting()}, {first_name()}", "Four quick steps and you're reviewing."))
        _getting_started(store)
        return

    pending = [i for i in invoices if i["status"] == REVIEW]
    open_id = st.session_state.get("open_invoice")
    if open_id in [i["id"] for i in pending]:
        render_invoice(store, reference, open_id, navigation_list(pending, open_id))
        return

    approved = [i for i in invoices if i["status"] == APPROVED]
    flagged = [i for i in pending if i["requires_review"]]
    metrics = store.metrics()
    done_today = approved_today(store)
    minutes = len(flagged) * 2 + (len(pending) - len(flagged))
    total_today = done_today + len(pending)
    lead = (
        f"{len(pending)} invoice{'s' if len(pending) != 1 else ''} waiting · about {minutes} minute"
        f"{'s' if minutes != 1 else ''} of review"
        if pending
        else "Your queue is clear. Nice work!"
    )
    chips = [
        f"{ui.icon('flag')} {len(flagged)} need attention",
        f"{ui.icon('bolt')} {len(pending) - len(flagged)} ready to approve",
        f"{ui.icon('task_alt')} {done_today} approved today",
    ]
    st.html(
        ui.hero(
            f"{dt.date.today():%A, %B} {dt.date.today().day}",
            f"{ui.greeting()}, {first_name()}",
            lead,
            chips,
            done_today / total_today if total_today else 1.0,
            f"{done_today}/{total_today}" if total_today else "✓",
            "today's progress",
        )
    )

    accuracy = metrics["line_accuracy"]
    weekly = weekly_accuracy(metrics)
    st.html(
        ui.tiles(
            [
                ui.tile("Waiting for review", len(pending), "inbox", "blue", f"{len(flagged)} flagged by checks"),
                ui.tile("Approved", len(approved), "task_alt", "green", f"{done_today} today"),
                ui.tile(
                    "AI coding accuracy",
                    "—" if accuracy is None else f"{accuracy:.0%}",
                    "auto_awesome",
                    "violet",
                    "" if accuracy is None else f"{(accuracy - TARGET_ACCURACY) * 100:+.1f} pts vs 90% target",
                    "" if accuracy is None else ("up" if accuracy >= TARGET_ACCURACY else "down"),
                    weekly if len(weekly) > 1 else None,
                ),
                ui.tile(
                    "Lessons learned",
                    metrics["lines_reviewed"],
                    "psychology",
                    "amber",
                    f"{metrics['lines_corrected']} corrections taught",
                ),
            ]
        )
    )

    if st.session_state.pop("celebrate", False):
        st.balloons()

    tab_review, tab_approved, tab_other = st.tabs(
        [
            f":material/inbox: To review · {len(pending)}",
            f":material/task_alt: Approved · {len(approved)}",
            f":material/report: Failed / rejected · {len(invoices) - len(pending) - len(approved)}",
        ]
    )
    with tab_review:
        if not pending:
            with card("empty"):
                st.html(ui.empty_state("Inbox zero", "Every invoice has been reviewed. Time for a coffee."))
                st.page_link(PAGES["process"], label="Process new invoices", icon=":material/arrow_forward:")
        else:
            bar_filter, bar_search, bar_sort = st.columns([3.2, 2.4, 1.8], vertical_alignment="center")
            counts = {"all": len(pending), "attention": len(flagged), "ready": len(pending) - len(flagged)}
            names = {"all": "All", "attention": "Needs attention", "ready": "Ready"}
            view = (
                bar_filter.segmented_control(
                    "Show",
                    list(counts),  # stable values; the counts are only in the labels
                    format_func=lambda v: f"{names[v]} · {counts[v]}",
                    default="all",
                    label_visibility="collapsed",
                    key="queue_filter",
                    persist_state="session",
                )
                or "all"
            )
            query = bar_search.text_input(
                "Search", placeholder="Search vendor or invoice #", label_visibility="collapsed",
                icon=":material/search:", key="queue_search", persist_state="session",
            )  # fmt: skip
            order = bar_sort.selectbox(
                "Sort",
                list(QUEUE_SORTS),
                label_visibility="collapsed",
                key="queue_sort",
                persist_state="session",
            )
            shown = (
                flagged
                if view == "attention"
                else [i for i in pending if not i["requires_review"]]
                if view == "ready"
                else pending
            )
            shown = sort_queue(filter_queue(shown, query), order)
            # Previous / Next on the review screen follow exactly what is shown here.
            st.session_state["queue_order"] = [i["id"] for i in shown]
            if not shown:
                st.caption("No invoices match.")
            for inv in shown:
                _queue_card(store, inv)

    with tab_approved:
        if not approved:
            st.caption("No approved invoices yet.")
        else:
            export = _export_rows(store, approved)
            st.download_button(
                "Export GL distribution (CSV)",
                pd.DataFrame(export).to_csv(index=False).encode("utf-8-sig"),  # BOM: Excel shows accents
                file_name=f"approved_gl_distribution_{dt.date.today()}.csv",
                mime="text/csv",
                icon=":material/download:",
            )
            rows = [
                [
                    f"<div style='display:flex;gap:.6rem;align-items:center'>{ui.avatar(i['vendor_name'] or '', 'sm')}"
                    f"<b>{esc(i['vendor_name'])}</b></div>",
                    esc(i["invoice_number"]),
                    esc(i["invoice_date"]),
                    f"{money(i['grand_total'])} <span class='apc-muted'>{esc(i['currency'])}</span>",
                    esc(i["reviewer"]),
                    esc(ui.time_ago(i["reviewed_at"])),
                ]
                for i in approved
            ]
            with card("approved_list"):
                st.html(ui.table(["Vendor", "Invoice #", "Date", "Total", "Approved by", "When"], rows, right=[3]))
            labels = {i["id"]: f"#{i['id']} · {i['vendor_name']} · {i['invoice_number']}" for i in approved}
            chosen = st.selectbox("View approved invoice", list(labels), format_func=labels.get, key="view_approved")
            render_approved(store, reference, chosen)

    with tab_other:
        others = [i for i in invoices if i["status"] in (FAILED, REJECTED)]
        if not others:
            st.caption("Nothing here.")
        settings = get_settings()
        azure_ready = bool(settings.document_intelligence.endpoint and settings.openai.endpoint)
        for inv in others:
            with card(f"failed_{inv['id']}"):
                left, right = st.columns([5, 2], vertical_alignment="center")
                badge = ui.pill("Failed", "err", "error") if inv["status"] == FAILED else ui.pill("Rejected", "gray")
                error = (inv.get("error") or "No reason recorded.").strip()
                first, _, details = error.partition("\n")
                left.html(
                    f"<div style='font-weight:700'>{esc(inv['vendor_name'] or inv['file_name'])} {badge}</div>"
                    f"<div class='apc-muted'>{esc(first[:240])}{'…' if len(first) > 240 else ''}</div>"
                )
                if details.strip() or len(first) > 240:
                    with left.expander("Technical details"):
                        st.code(error, language=None, wrap_lines=True)
                b1, b2 = right.columns(2)
                if b1.button(
                    "Retry", key=f"retry_{inv['id']}", icon=":material/refresh:", disabled=not azure_ready,
                    help=None if azure_ready else "Set up Azure in .env first (see Process invoices)",
                ):  # fmt: skip
                    full = store.get_invoice(inv["id"])
                    path = Path(full["source_path"])
                    if not path.exists():
                        st.error(f"The original file is no longer at {path}.")
                    else:
                        store.delete_invoice(inv["id"])
                        run_pipeline(store, [path])
                        st.rerun()
                if b2.button("Delete", key=f"del_{inv['id']}", icon=":material/delete:"):
                    delete_invoice(store, inv["id"])
                    notify("Deleted. The file moved to invoices/deleted.", ":material/delete:")
                    st.rerun()


def _queue_card(store: Store, inv: dict[str, Any]) -> None:
    full = store.get_invoice(inv["id"]) or {}
    ai = full.get("ai_output") or {}
    taxes = list(dict.fromkeys(t.get("tax_type", "") for t in ai.get("tax_lines", [])))
    prov = ai.get("ship_to_province") or ai.get("supplier_province") or ""
    with st.container(key=f"qcard_{inv['id']}"):
        st.html(ui.queue_card(inv, taxes, PROVINCE_NAMES.get(prov, "")))
        label = f"Review invoice from {inv['vendor_name'] or inv['file_name']}"
        if st.button(label, key=f"qopen_{inv['id']}"):
            st.session_state["open_invoice"] = inv["id"]
            st.rerun()


NO_PROVINCE = "—"  # shown instead of an empty province (e.g. GST, which is federal)
QUEUE_SORTS = ("Priority", "Amount: high to low", "Newest invoice date", "Vendor A–Z")


def navigation_list(pending: list[dict[str, Any]], open_id: int) -> list[dict[str, Any]]:
    """The invoices Previous / Next step through: the queue as last shown (filter, search, sort),
    or the whole queue if the open invoice isn't part of that view."""
    by_id = {i["id"]: i for i in pending}
    shown = [by_id[i] for i in st.session_state.get("queue_order", []) if i in by_id]
    return shown if open_id in [i["id"] for i in shown] else pending


def filter_queue(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    query = (query or "").strip().lower()
    if not query:
        return rows
    fields = ("vendor_name", "invoice_number", "file_name")
    return [r for r in rows if any(query in str(r.get(f) or "").lower() for f in fields)]


def sort_queue(rows: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    if order == "Amount: high to low":
        return sorted(rows, key=lambda r: -(r.get("grand_total") or 0))
    if order == "Newest invoice date":
        return sorted(rows, key=lambda r: r.get("invoice_date") or "", reverse=True)
    if order == "Vendor A–Z":
        return sorted(rows, key=lambda r: (r.get("vendor_name") or r.get("file_name") or "").lower())
    return rows  # store order: flagged first, then lowest confidence


def _getting_started(store: Store) -> None:
    settings = get_settings()
    steps = _setup_steps(store, settings)
    has_invoices = bool(store.list_invoices())
    steps.append(("ok" if has_invoices else "todo", "Process your first invoices", "done" if has_invoices else "to do"))
    done = sum(1 for s, _, _ in steps if s == "ok")
    with card("onboarding"):
        head, gauge = st.columns([5, 1], vertical_alignment="center")
        head.markdown("#### :material/rocket_launch: Getting started")
        head.caption("Finish these steps and invoices will start arriving in your review queue.")
        ring = ui.ring(done / len(steps), size=60, stroke=6, label=f"{done}/{len(steps)}")
        gauge.html(f"<div style='text-align:right'>{ring}</div>")
        st.html("".join(ui.step(s, label, state) for s, label, state in steps))
        links = st.container(horizontal=True)
        if steps[0][0] != "ok" or steps[1][0] != "ok":
            links.caption(
                ":material/info: Azure settings live in the `.env` file next to the app (see GETTING_STARTED.md)."
            )
        links.page_link(PAGES["accounts"], label="GL accounts & tax", icon=":material/account_tree:")
        links.page_link(PAGES["process"], label="Process invoices", icon=":material/upload_file:")


def _export_rows(store: Store, approved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for inv in approved:
        full = store.get_invoice(inv["id"])
        final = full["final_output"] or {}
        for e in final.get("gl_distribution", []):
            rows.append(
                {
                    "invoice_id": inv["id"],
                    "vendor_name": final.get("vendor_name"),
                    "invoice_number": final.get("invoice_number"),
                    "invoice_date": final.get("invoice_date"),
                    "currency": final.get("currency"),
                    "kind": e["kind"],
                    "gl_code": e["gl_code"],
                    "cost_center": e["cost_center"],
                    "description": e["description"],
                    "net_amount": e["net_amount"],
                    "non_recoverable_tax": e["non_recoverable_tax"],
                    "amount": e["amount"],
                }
            )
    return rows


def _document_panel(inv: dict[str, Any]) -> None:
    path = Path(inv["source_path"])
    pages = render_pages(str(path), path.stat().st_mtime) if path.exists() else []
    head, pager = st.columns([3, 2], vertical_alignment="center")
    head.markdown("#### :material/description: Document")
    page_no = 1
    if len(pages) > 1:
        page_no = pager.segmented_control(
            "Page", list(range(1, len(pages) + 1)), default=1, key=f"page_{inv['id']}",
            format_func=lambda n: f"Page {n}", label_visibility="collapsed",
        ) or 1  # fmt: skip
    st.html(f"<div class='apc-muted'>{ui.icon('attach_file', '1em')} {esc(inv['file_name'])}</div>")
    if pages:
        st.image(pages[page_no - 1], width="stretch")
    elif not path.exists():
        st.warning(f"Original file not found at {path}", icon=":material/warning:")
    with st.expander("Extracted text (what the AI read)", expanded=not pages, icon=":material/text_snippet:"):
        st.html(f"<div style='font-size:0.85rem'>{ui.document_text(inv.get('extraction_md') or '')}</div>")


def _checks_html(report: Any) -> str:
    items = []
    for issue in report.issues:
        where = f"Line {issue.line_number}: " if issue.line_number else ""
        if issue.severity == "error":
            items.append(ui.check("error", "Must fix", where + issue.message, issue.code))
        else:
            items.append(ui.check("warning", "Worth a look", where + issue.message, issue.code))
    errors = [i for i in report.issues if i.severity == "error"]
    if report.requires_review and not errors:
        items.append(
            ui.check(
                "warning",
                "Low confidence",
                f"AI confidence {report.adjusted_confidence:.0%} is below the {report.review_threshold:.0%} "
                "threshold, so a person should look it over.",
            )
        )
    history = report.checks.get("history") or []
    matches = [h for h in history if h["status"] == "match"]
    if matches:
        lines = ", ".join(str(h["line_number"]) for h in matches)
        items.append(ui.check("info", "Learned", f"Line {lines} matches how reviewers coded this vendor before."))
    if not report.issues:
        title = "Numbers check out" if report.requires_review else "All clear"
        items.append(ui.check("ok", title, "Totals reconcile, taxes verified, all codes valid."))
    return "".join(items)


def _reasons_html(coding: InvoiceCoding, ai: dict[str, Any], report: Any, reference: ReferenceData) -> str:
    history = {h["line_number"]: h for h in report.checks.get("history") or []}
    rows = []
    for original, li in pair_lines(ai.get("line_items", []), list(coding.line_items)):
        before = original or {}
        badges = []
        if not before:
            badges.append(ui.pill("Added by you", "info", "add"))
        elif before.get("predicted_gl_code") != li.predicted_gl_code:
            badges.append(ui.pill(f"Changed from {before.get('predicted_gl_code')}", "info", "edit"))
        h = history.get(li.line_number)
        if h and h["status"] == "match":
            badges.append(ui.pill(f"Matches {h['decisions']} past decisions", "violet", "psychology"))
        elif h:
            badges.append(ui.pill(f"Reviewers used {h['history_gl']} before", "warn", "history"))
        rows.append(
            ui.reason_row(
                li.line_number,
                li.description,
                li.predicted_gl_code,
                gl_name(reference, li.predicted_gl_code),
                before.get("reasoning_justification") or "Line added by the reviewer.",
                badges,
            )  # fmt: skip
        )
    return f"<div class='apc-reasons'>{''.join(rows)}</div>"


def _ai_changes(coding: InvoiceCoding, ai: dict[str, Any]) -> int:
    changed = 0
    for before, li in pair_lines(ai.get("line_items", []), list(coding.line_items)):
        if (
            before is None
            or before.get("predicted_gl_code") != li.predicted_gl_code
            or (before.get("predicted_cost_center") or "") != (li.predicted_cost_center or "")
        ):
            changed += 1
    return changed


def render_invoice(store: Store, reference: ReferenceData, invoice_id: int, pending: list[dict[str, Any]]) -> None:
    inv = store.get_invoice(invoice_id)
    ai = inv["ai_output"] or {}
    settings = get_settings()
    key = f"inv{invoice_id}"
    meta = inv.get("meta") or {}
    ids = [i["id"] for i in pending]
    position = ids.index(invoice_id)

    # --- Navigation (with keyboard shortcuts) -------------------------------------------------------
    nav = st.container(horizontal=True, vertical_alignment="center")
    if nav.button("Queue", icon=":material/arrow_back:", type="tertiary", shortcut="Alt+Up"):
        st.session_state.pop("open_invoice", None)
        st.rerun()
    nav.html(f"<span class='apc-muted'>Reviewing <b style='color:#142033'>{position + 1}</b> of {len(ids)}</span>")
    nav.space("stretch")
    with nav.popover("Shortcuts", icon=":material/keyboard:", type="tertiary"):
        st.html(
            "<table class='apc-table'><tbody>"
            f"<tr><td>{ui.kbd('Ctrl')} + {ui.kbd('Enter')}</td><td>Approve &amp; teach</td></tr>"
            f"<tr><td>{ui.kbd('Alt')} + {ui.kbd('→')}</td><td>Next invoice</td></tr>"
            f"<tr><td>{ui.kbd('Alt')} + {ui.kbd('←')}</td><td>Previous invoice</td></tr>"
            f"<tr><td>{ui.kbd('Alt')} + {ui.kbd('↑')}</td><td>Back to the queue</td></tr>"
            "</tbody></table>"
        )
    # Always enabled (wrapping around), so Alt+Left/Right never fall through to the browser's Back/Forward.
    if nav.button("Previous", icon=":material/chevron_left:", shortcut="Alt+Left"):
        st.session_state["open_invoice"] = ids[(position - 1) % len(ids)]
        st.session_state["scroll_top"] = True
        st.rerun()
    if nav.button("Next", icon=":material/chevron_right:", icon_position="right", shortcut="Alt+Right"):
        st.session_state["open_invoice"] = ids[(position + 1) % len(ids)]
        st.session_state["scroll_top"] = True
        st.rerun()
    st.html(ui.progress(position + 1, len(ids)))

    summary = st.container()  # the summary card is drawn here once the edits are valid

    left, right = st.columns([5, 7], gap="medium")
    with left, card("document"):
        _document_panel(inv)

    with right:
        checks_box = card("checks")
        with card("details"):
            st.markdown("#### :material/badge: Invoice details")
            keep = {"persist_state": "session"}  # edits survive moving to another invoice and back

            def text(col: Any, label: str, field: str, default: str = "", **kw: Any) -> str:
                return col.text_input(label, ai.get(field, default), key=f"{key}_{field}", **keep, **kw)

            def amount(col: Any, label: str, field: str) -> float:
                value = float(ai.get(field) or 0)
                return col.number_input(label, value=value, format="%.2f", key=f"{key}_{field}", **keep)

            def province(col: Any, label: str, field: str, **kw: Any) -> str:
                current = ai.get(field) or ""
                return col.selectbox(
                    label, PROVINCE_VALUES, index=PROVINCE_VALUES.index(current if current in PROVINCE_VALUES else ""),
                    format_func=prov_label.get, key=f"{key}_{field}", **keep, **kw,
                )  # fmt: skip

            prov_label = {
                p: f"{p} · {PROVINCE_NAMES.get(p, 'Outside Canada' if p else 'Unknown')}" for p in PROVINCE_VALUES
            }
            c1, c2, c3 = st.columns([2, 1.2, 1.2])
            header: dict[str, Any] = {
                "vendor_name": text(c1, "Vendor", "vendor_name"),
                "invoice_number": text(c2, "Invoice #", "invoice_number"),
                "invoice_date": text(
                    c3, "Invoice date", "invoice_date", placeholder="YYYY-MM-DD", help="Format YYYY-MM-DD"
                ),
            }
            c1, c2, c3 = st.columns([1, 2, 2])
            header["currency"] = text(c1, "Currency", "currency", "CAD")
            header["supplier_province"] = province(c2, "Supplier province", "supplier_province")
            header["ship_to_province"] = province(
                c3, "Place of supply", "ship_to_province", help="Where goods are delivered / services performed"
            )
            c1, c2 = st.columns(2)
            header["gst_hst_registration_number"] = text(c1, "Supplier GST/HST #", "gst_hst_registration_number")
            header["qst_registration_number"] = text(c2, "Supplier QST #", "qst_registration_number")
            c1, c2, c3 = st.columns(3)
            header["subtotal"] = amount(c1, "Subtotal", "subtotal")
            header["tax_total"] = amount(c2, "Tax total", "tax_total")
            header["grand_total"] = amount(c3, "Grand total", "grand_total")

    # --- Line items ---------------------------------------------------------------------------------------
    with card("lines"):
        st.markdown("#### :material/list_alt: Line items")
        st.caption("Click a GL account or cost center cell to change it. Add or delete rows at the bottom.")
        gl_labels = gl_label_map(reference)
        tax_gls = reference.tax.tax_gl_codes()
        gl_options = [UNASSIGNED] + [c for c in reference.chart_of_accounts.codes if c not in tax_gls]
        lines_df = pd.DataFrame(ai.get("line_items", []))
        if lines_df.empty:
            lines_df = pd.DataFrame(columns=["line_number", "description", "quantity", "unit_price", "amount",
                                             "predicted_gl_code", "predicted_cost_center", "taxes_applied",
                                             "reasoning_justification"])  # fmt: skip
        for code in lines_df.get("predicted_gl_code", []):
            if code not in gl_options:
                gl_options.append(code)  # keep unknown codes visible so they can be fixed
        has_cc = reference.cost_centers is not None
        column_config: dict[str, Any] = {
            # pixel widths: the coding columns stay on screen on a 1366px laptop
            "line_number": st.column_config.NumberColumn("#", width=48, step=1),
            "description": st.column_config.TextColumn("Description", width=250 if has_cc else 380),
            "quantity": st.column_config.NumberColumn("Qty", format="%.2f"),
            "unit_price": st.column_config.NumberColumn("Unit price", format="%.2f"),
            "amount": st.column_config.NumberColumn("Amount", format="%.2f", width=95),
            "predicted_gl_code": st.column_config.SelectboxColumn(
                "GL account",
                options=gl_options,
                format_func=lambda c: gl_labels.get(c, f"{c} · unknown code"),
                width=200,
                # not required: Streamlit would silently drop a new row whose GL is still blank;
                # a blank GL becomes UNASSIGNED, which blocks approval until a code is picked
            ),  # fmt: skip
            "taxes_applied": st.column_config.MultiselectColumn("Taxes", options=list(TAX_TYPES), width=120),
            "reasoning_justification": st.column_config.TextColumn("AI reasoning", disabled=True, width="large"),
        }
        column_order = ["line_number", "description", "amount", "predicted_gl_code"]
        if reference.cost_centers is not None:
            cc_labels = cc_label_map(reference)
            column_config["predicted_cost_center"] = st.column_config.SelectboxColumn(
                "Cost center", options=[UNASSIGNED, *reference.cost_centers.codes],
                format_func=lambda c: cc_labels.get(c, c), width=150,
            )  # fmt: skip
            column_order.append("predicted_cost_center")
        column_order += ["taxes_applied", "quantity", "unit_price"]
        edited_lines = persistent_editor(
            lines_df, column_config=column_config, column_order=column_order, num_rows="dynamic",
            hide_index=True, key=f"{key}_lines",
        )  # fmt: skip
        reasons_box = st.container()

    tax_col, tax_check_col = st.container(), st.container()  # full width: every column readable at 1366px
    with tax_col, card("tax"):
        st.markdown("#### :material/percent: Sales tax")
        tax_df = pd.DataFrame(
            ai.get("tax_lines", []), columns=["tax_type", "province", "rate", "taxable_amount", "tax_amount"]
        )
        tax_df["province"] = tax_df["province"].fillna("").replace("", NO_PROVINCE)
        tax_df.insert(2, "rate_pct", (pd.to_numeric(tax_df.pop("rate"), errors="coerce") * 100).round(4))
        edited_tax = persistent_editor(
            tax_df,
            column_config={
                "tax_type": st.column_config.SelectboxColumn("Tax", options=list(TAX_TYPES), required=True),
                "province": st.column_config.SelectboxColumn(
                    "Province", options=[p or NO_PROVINCE for p in PROVINCE_VALUES]
                ),
                "rate_pct": st.column_config.NumberColumn("Rate", format="%.3f%%", help="Percent: 13% HST = 13"),
                "taxable_amount": st.column_config.NumberColumn("Taxable", format="%.2f"),
                "tax_amount": st.column_config.NumberColumn("Amount", format="%.2f"),
            },
            num_rows="dynamic",
            hide_index=True,
            key=f"{key}_taxlines",
        )

    coding, problems = coding_from_inputs(
        header, edited_lines, edited_tax, ai, UNASSIGNED if reference.cost_centers is not None else ""
    )
    if coding is None:
        with checks_box:
            st.markdown("#### :material/fact_check: Checks")
            st.html("".join(ui.check("error", "Fix this field", p) for p in problems))
        with card("actionbar"):
            left, right = st.columns([3, 1], vertical_alignment="center")
            left.html("<div class='apc-muted'>Fix the highlighted field to see checks and approve.</div>")
            _more_menu(right.container(horizontal=True, horizontal_alignment="right"), store, invoice_id, ids,
                       position, key)  # fmt: skip
        return

    extraction = ExtractionResult(
        source=inv["source_path"],
        model_id=(meta.get("extraction") or {}).get("model_id", ""),
        content="",
        mean_word_confidence=(meta.get("extraction") or {}).get("mean_word_confidence"),
        invoice_fields=(meta.get("extraction") or {}).get("invoice_fields") or {},
    )
    output, report = finalise_coding(
        coding, reference, settings, extraction, store=store, exclude_invoice_id=invoice_id
    )
    errors = [i for i in report.issues if i.severity == "error"]
    warnings = [i for i in report.issues if i.severity == "warning"]

    with summary, card("summary"):
        _invoice_summary(coding, report, errors, warnings, output, reference)
    with checks_box:
        head, count = st.columns([3, 2], vertical_alignment="center")
        head.markdown("#### :material/fact_check: Checks")
        count.html(
            "<div style='text-align:right'>"
            + (ui.pill(f"{len(errors)} to fix", "err") + " " if errors else "")
            + (ui.pill(f"{len(warnings)} to look at", "warn") if warnings else "")
            + (ui.pill("All clear", "ok", "check") if not report.issues and not report.requires_review else "")
            + (ui.pill("Needs a look", "warn", "visibility") if not report.issues and report.requires_review else "")
            + "</div>"
        )
        st.html(_checks_html(report))
    with (
        reasons_box,
        st.expander(
            "Why the AI chose these codes", icon=":material/psychology_alt:", expanded=len(coding.line_items) <= 6
        ),
    ):
        st.html(_reasons_html(coding, ai, report, reference))
    with tax_check_col, card("taxchecks"):
        st.markdown("#### :material/calculate: Tax checks")
        _tax_check_table(coding, reference)

    with card("posting"):
        st.markdown("#### :material/account_balance: GL posting preview")
        _distribution_table(output, reference, coding.currency)

    # --- Sticky action bar --------------------------------------------------------------------------------
    changed = _ai_changes(coding, ai)
    with card("actionbar"):
        left, right = st.columns([1, 1], vertical_alignment="center")
        with left:
            learn = f"you changed <b>{changed}</b> of its suggestions" if changed else "all as the AI suggested"
            st.html(
                f"<div style='display:flex;gap:.8rem;align-items:center'>{ui.avatar(reviewer(), 'sm')}"
                f"<div><div style='font-weight:700;color:#142033'>"
                f"Post {money(coding.grand_total)} {esc(coding.currency)}"
                f"</div><div class='apc-muted'>Approving teaches the AI from {len(coding.line_items)} line(s): "
                f"{learn}.</div></div></div>"
            )
            allow = True
            uncoded = [str(li.line_number) for li in coding.line_items if li.predicted_gl_code == UNASSIGNED]
            if uncoded:
                allow = False  # never post to UNASSIGNED
                st.html(ui.pill(f"Pick a GL account for line {', '.join(uncoded)} to approve", "warn", "edit_note"))
            elif errors:
                allow = st.checkbox(f"Approve anyway, despite {len(errors)} error(s)", key=f"{key}_override")
        with right:
            buttons = st.container(
                horizontal=True, horizontal_alignment="right", vertical_alignment="center", wrap=False
            )
            _more_menu(buttons, store, invoice_id, ids, position, key)
            if buttons.button(
                "Approve & teach", type="primary", icon=":material/check:", disabled=not allow,
                key=f"{key}_approve", shortcut="Ctrl+Enter",
            ):  # fmt: skip
                counts = store.approve_invoice(invoice_id, output, reviewer())
                total = sum(counts.values())
                forget_drafts(key)
                _advance(ids, position)
                if not store.list_invoices(REVIEW):  # the whole queue is done, not just the current view
                    st.session_state["celebrate"] = True
                notify(
                    f"Approved {coding.vendor_name.rstrip('.')}. Learned from {total} line(s): "
                    f"{counts[ACCEPTED]} confirmed, {total - counts[ACCEPTED]} corrected.",
                    ":material/school:",
                )
                st.rerun()


def _more_menu(parent: Any, store: Store, invoice_id: int, ids: list[int], position: int, key: str) -> None:
    """Reject / delete, kept in a menu so the main action stays obvious."""
    with parent.popover("More", icon=":material/more_horiz:"):
        st.markdown("**Reject this invoice**")
        reason = st.text_input("Reason", key=f"{key}_reason", placeholder="e.g. not our invoice")
        if st.button("Reject", key=f"{key}_reject", icon=":material/block:", width="stretch"):
            store.reject_invoice(invoice_id, reviewer(), reason)
            forget_drafts(key)
            _advance(ids, position)
            notify(f"Invoice #{invoice_id} rejected.", ":material/block:")
            st.rerun()
        st.divider()
        st.markdown("**Remove from the queue**")
        st.caption("Nothing is learned from a deleted invoice. Its file moves to `invoices/deleted`.")
        sure = st.checkbox("Yes, delete this invoice", key=f"{key}_sure")
        if st.button(
            "Delete invoice", key=f"{key}_delete", icon=":material/delete:", width="stretch", disabled=not sure
        ):
            delete_invoice(store, invoice_id)
            forget_drafts(key)
            _advance(ids, position)
            notify(f"Invoice #{invoice_id} deleted.", ":material/delete:")
            st.rerun()


def delete_invoice(store: Store, invoice_id: int) -> None:
    """Delete an invoice; if its file is in the invoices folder, move it to ``invoices/deleted`` so the
    folder pick-up doesn't offer it again."""
    inv = store.get_invoice(invoice_id) or {}
    store.delete_invoice(invoice_id)
    path = Path(inv.get("source_path") or "")
    try:
        if path.is_file() and path.resolve().parent == INVOICE_DIR.resolve():
            target = INVOICE_DIR / "deleted" / path.name
            target.parent.mkdir(exist_ok=True)
            n = 1
            while target.exists():
                target = target.with_name(f"{path.stem}_{n}{path.suffix}")
                n += 1
            path.rename(target)
    except OSError:
        pass  # the invoice is gone from the queue either way; the file just stays where it is


def _advance(ids: list[int], position: int) -> None:
    """After an action, open the next invoice in the queue (or return to the list)."""
    remaining = ids[:position] + ids[position + 1 :]
    st.session_state["scroll_top"] = True
    if remaining:
        st.session_state["open_invoice"] = remaining[min(position, len(remaining) - 1)]
    else:
        st.session_state.pop("open_invoice", None)


def _invoice_summary(
    coding: InvoiceCoding, report: Any, errors: list[Any], warnings: list[Any], output: dict[str, Any],
    reference: ReferenceData,
) -> None:  # fmt: skip
    supply = coding.ship_to_province or coding.supplier_province
    meta = [
        ("receipt_long", f"Invoice {coding.invoice_number}"),
        ("event", coding.invoice_date),
        ("location_on", PROVINCE_NAMES.get(supply, "Province unknown")),
        ("verified", f"GST/HST {coding.gst_hst_registration_number}" if coding.gst_hst_registration_number else ""),
    ]
    pills = []
    if errors:
        pills.append(ui.pill(f"{len(errors)} error{'s' if len(errors) > 1 else ''}", "err", "error"))
    if warnings:
        pills.append(ui.pill(f"{len(warnings)} to check", "warn", "warning"))
    if not report.issues and not report.requires_review:
        pills.append(ui.pill("All checks passed", "ok", "check_circle"))
    if report.requires_review and not errors:
        pills.append(ui.pill("Needs a look", "warn", "visibility"))
    if any(h["status"] == "match" for h in report.checks.get("history") or []):
        pills.append(ui.pill("Learned pattern", "violet", "psychology"))
    pills += [ui.tax_chip(t) for t in dict.fromkeys(t.tax_type for t in coding.tax_lines)]
    st.html(
        ui.invoice_hero(
            coding.vendor_name,
            meta,
            pills,
            report.adjusted_confidence,
            coding.grand_total,
            coding.currency,
            report.review_threshold,
        )  # fmt: skip
    )
    # Where the money goes: expense GLs (incl. non-recoverable tax) and recoverable tax accounts.
    by_gl: dict[str, float] = {}
    for e in output["gl_distribution"]:
        name = gl_name(reference, e["gl_code"]) or e["gl_code"] or "Unmapped"
        label = f"{e['gl_code']} {name}" if e["kind"] == "expense" else f"{name} (tax)"
        by_gl[label] = by_gl.get(label, 0.0) + e["amount"]
    st.html(ui.split_bar(sorted(by_gl.items(), key=lambda kv: -kv[1])))


def _tax_check_table(coding: InvoiceCoding, reference: ReferenceData) -> None:
    if not coding.tax_lines:
        st.caption("No sales tax on this invoice.")
        return
    try:
        on = dt.date.fromisoformat(coding.invoice_date)
    except ValueError:
        on = dt.date.today()
    rows = []
    for t in coding.tax_lines:
        expected = round(t.taxable_amount * t.rate, 2)
        prov = t.province or coding.ship_to_province or coding.supplier_province
        official = reference.tax.rates.rate_for(t.tax_type, prov if prov in PROVINCE_NAMES else "", on)
        treat = reference.tax.treatment(t.tax_type)
        gl = treat.gl_code if treat.needs_gl else "expense lines"
        math_ok = abs(expected - t.tax_amount) <= 0.01 * max(1, len(coding.line_items)) + 0.01
        rate_ok = official is not None and abs(official - t.rate) < 1e-6
        mark = lambda ok: "<span class='ok'>✓</span>" if ok else "<span class='bad'>✕</span>"  # noqa: E731
        rows.append(
            [
                f"{ui.tax_chip(t.tax_type)} {esc(t.province)}",
                f"{mark(rate_ok)} {t.rate * 100:.3f}%",
                "not levied" if official is None else f"{official * 100:.3f}%",
                f"{mark(math_ok)} {money(t.tax_amount)}",
                esc(gl or "⚠ not mapped"),
            ]
        )
    st.html(ui.table(["Tax", "Rate", "Official", "Charged", "Posts to"], rows, right=[3], wrap=[4]))


def _distribution_table(output: dict[str, Any], reference: ReferenceData, currency: str) -> None:
    rows = []
    for e in output["gl_distribution"]:
        kind = ui.pill("Tax", "info") if e["kind"] == "tax" else ui.pill(f"Line {e['line_number']}", "gray")
        name = gl_name(reference, e["gl_code"])
        gl = f"<div class='gl'>{esc(e['gl_code'] or '⚠ not mapped')}<small>{esc(name)}</small></div>"
        rows.append(
            [
                kind,
                gl,
                esc(e["cost_center"]),
                esc(e["description"]),
                money(e["net_amount"]) if e["kind"] == "expense" else "",
                money(e["non_recoverable_tax"]) if e["non_recoverable_tax"] else "",
                f"<b>{money(e['amount'])}</b>",
            ]
        )
    total = round(sum(e["amount"] for e in output["gl_distribution"]), 2)
    diff = round(total - output["grand_total"], 2)
    balance = (
        ui.pill("Balanced", "ok", "balance") if abs(diff) <= 0.01 else ui.pill(f"Off by {money(diff)}", "err", "error")
    )
    foot = ["", balance, "", "Total", "", "", f"{money(total)} {esc(currency)}"]
    st.html(
        ui.table(
            ["", "GL account", "Cost center", "Description", "Net", "Non-rec. tax", "Amount"],
            rows,
            right=[4, 5, 6],
            foot=foot,
            wrap=[3],
        )  # fmt: skip
    )


EDIT_LABELS = {
    "line_coding": "GL coding", "line_count": "lines added or removed", "tax_lines": "sales tax",
    "vendor_name": "vendor", "invoice_number": "invoice #", "invoice_date": "date", "currency": "currency",
    "supplier_province": "supplier province", "ship_to_province": "place of supply",
    "gst_hst_registration_number": "GST/HST #", "qst_registration_number": "QST #", "subtotal": "subtotal",
    "tax_total": "tax total", "grand_total": "total",
}  # fmt: skip


def render_approved(store: Store, reference: ReferenceData, invoice_id: int) -> None:
    inv = store.get_invoice(invoice_id)
    final = inv["final_output"] or {}
    edits = inv.get("edits") or []
    with card(f"approved_{invoice_id}"):
        st.html(
            f"<div style='display:flex;gap:.8rem;align-items:center;margin-bottom:.6rem'>"
            f"{ui.avatar(final.get('vendor_name', ''))}<div><div style='font-weight:700;font-size:1.05rem'>"
            f"{esc(final.get('vendor_name'))}</div><div class='apc-muted'>Invoice {esc(final.get('invoice_number'))}"
            f" · approved by {esc(inv['reviewer'])} {esc(ui.time_ago(inv['reviewed_at']))} · "
            + (
                "no changes to the AI's coding"
                if not edits
                else "changed: " + esc(", ".join(EDIT_LABELS.get(e, e) for e in edits))
            )
            + "</div></div></div>"
        )
        _distribution_table(final, reference, final.get("currency", ""))


# --- Process invoices --------------------------------------------------------------------------------------


def run_pipeline(store: Store, paths: list[Path]) -> None:
    reference = store.reference_data()
    settings = get_settings()
    pipeline = InvoicePipeline(settings, reference, cache_dir=CACHE_DIR, store=store)
    ok = 0
    with st.status(f"Processing {len(paths)} invoice(s)…", expanded=True) as status:
        for n, path in enumerate(paths, start=1):
            st.write(f":material/document_scanner: Reading and coding **{path.name}** ({n}/{len(paths)})")
            result = pipeline.process(path)
            if result.ok:
                ok += 1
                flag = "needs attention" if result.report and result.report.requires_review else "ready"
                st.write(f":material/check_circle: {result.output.get('vendor_name', path.name)}: {flag}")
            else:
                st.error(f"{path.name}: {result.error}", icon=":material/error:")
        status.update(label=f"Processed {ok} of {len(paths)}", state="complete" if ok == len(paths) else "error")
    failed = len(paths) - ok
    if ok:
        notify(f"{ok} invoice(s) read and coded. They're waiting in the review queue.", ":material/inbox:")
    if failed:
        notify(f"{failed} file(s) could not be processed. See Review queue → Failed / rejected.", ":material/error:")


def tax_types_mapped(store: Store) -> int:
    """Tax types ready to post: added to expense lines, or mapped to a GL account that still exists."""
    codes = {a["code"] for a in store.list_accounts("gl_accounts")}
    return sum(1 for t in store.tax_treatments().values() if not t.needs_gl or t.gl_code in codes)


def _setup_steps(store: Store, settings: Settings) -> list[tuple[str, str, str]]:
    mapped = tax_types_mapped(store)
    gl_count = len(store.list_accounts("gl_accounts"))
    return [
        (
            "ok" if settings.document_intelligence.endpoint else "bad",
            "Azure Document Intelligence",
            "connected" if settings.document_intelligence.endpoint else "set the endpoint in .env",
        ),
        (
            "ok" if settings.openai.endpoint else "bad",
            "Azure OpenAI",
            f"{settings.openai.deployment}" if settings.openai.endpoint else "set the endpoint in .env",
        ),
        ("ok" if gl_count else "todo", "GL accounts", f"{gl_count} imported" if gl_count else "import them"),
        ("ok" if mapped == len(TAX_TYPES) else "todo", "Sales tax GL mapping", f"{mapped} of {len(TAX_TYPES)} set"),
    ]


def page_process() -> None:
    store = get_store()
    show_toast()
    st.html(
        ui.page_header("Inbox", "Process invoices", "Read new invoices with Azure and send them to the review queue.")
    )
    settings = get_settings()
    steps = _setup_steps(store, settings)
    ready = all(s == "ok" for s, _, _ in steps[:3])

    left, right = st.columns([3, 2], gap="medium")
    with right, card("setup_steps"):
        done = sum(1 for s, _, _ in steps if s == "ok")
        head, gauge = st.columns([3, 1], vertical_alignment="center")
        head.markdown("#### :material/checklist: Setup")
        head.caption("Everything the engine needs before it can read invoices.")
        gauge.html(ui.ring(done / len(steps), size=56, stroke=6, label=f"{done}/{len(steps)}"))
        st.html("".join(ui.step(s, label, state) for s, label, state in steps))
        if any(s != "ok" for s, _, _ in steps[2:]):
            st.page_link(PAGES["accounts"], label="Finish setup", icon=":material/arrow_forward:")

    recent = sorted(store.list_invoices(), key=lambda i: (i["created_at"] or "", i["id"]), reverse=True)[:6]
    if recent:
        with right, card("recent"):
            st.markdown("#### :material/history: Recently processed")
            st.html("".join(ui.recent_row(i) for i in recent))
            if any(i["status"] == REVIEW for i in recent):
                st.page_link(PAGES["review"], label="Go to the review queue", icon=":material/arrow_forward:")

    with left:
        with card("upload"):
            st.markdown("#### :material/upload_file: Upload invoices")
            uploaded = st.file_uploader(
                "Drop PDFs, TIFFs, PNGs or JPGs here. They are saved to your private invoices folder on this computer.",
                type=sorted(e.lstrip(".") for e in SUPPORTED_EXTENSIONS),
                accept_multiple_files=True,
                key=f"upload_{st.session_state.get('upload_round', 0)}",  # new key = empty uploader after a run
            )
            if uploaded and st.button(
                f"Process {len(uploaded)} uploaded invoice(s)", type="primary", icon=":material/play_arrow:",
                disabled=not ready,
            ):  # fmt: skip
                INVOICE_DIR.mkdir(parents=True, exist_ok=True)
                paths = []
                for f in uploaded:
                    target = INVOICE_DIR / f.name
                    stem, n = target.stem, 1
                    while target.exists() and target.read_bytes() != f.getvalue():
                        target = INVOICE_DIR / f"{stem}_{n}{target.suffix}"
                        n += 1
                    target.write_bytes(f.getvalue())
                    paths.append(target)
                already, todo, hashes = [], [], set()
                for p in paths:  # skip files seen before, and repeats within this upload
                    digest = hashlib.sha256(p.read_bytes()).hexdigest()
                    if digest in hashes or store.find_by_hash(p) is not None:
                        already.append(p)
                    else:
                        todo.append(p)
                    hashes.add(digest)
                if todo:
                    run_pipeline(store, todo)
                if already:
                    notify(
                        f"Skipped {len(already)} file(s) already in AP Coder: {', '.join(p.name for p in already)}",
                        ":material/content_copy:",
                    )
                st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1
                st.rerun()

        with card("folder"):
            st.markdown("#### :material/folder_open: Invoices folder")
            st.caption(f"Copy files into `{short_path(INVOICE_DIR)}` and they appear here.")
            files = invoice_files(INVOICE_DIR) if INVOICE_DIR.exists() else []
            new_files = [p for p in files if store.find_by_hash(p, include_failed=True) is None]
            if not new_files:
                st.html(ui.pill("No new files", "gray", "done_all"))
            else:
                rows = [
                    [f"{ui.icon('picture_as_pdf' if p.suffix.lower() == '.pdf' else 'image', '1.1em', '#c53030')} "
                     f"{esc(p.name)}", f"{p.stat().st_size / 1024:,.0f} KB"]
                    for p in new_files
                ]  # fmt: skip
                st.html(ui.table(["File", "Size"], rows, right=[1]))
                if st.button(
                    f"Process {len(new_files)} file(s)", type="primary", icon=":material/play_arrow:",
                    disabled=not ready,
                ):  # fmt: skip
                    run_pipeline(store, new_files)
                    st.rerun()


# --- GL accounts ------------------------------------------------------------------------------------------------

_CODE_HINTS = ("gl", "account", "code", "cost", "centre", "center", "saknr", "kostl")
_DESC_HINTS = ("desc", "name", "title", "text")
_CAT_HINTS = ("categ", "group", "type", "class")


def _guess(columns: list[str], hints: tuple[str, ...], default: int = 0) -> int:
    for i, col in enumerate(columns):
        if any(h in col.lower() for h in hints):
            return i
    return default


def _read_upload(upload: Any, table: str) -> pd.DataFrame | None:
    """The uploaded sheet as text cells, or None (with a message) if it can't be read."""
    try:
        if upload.name.lower().endswith((".xlsx", ".xls")):
            sheets = pd.read_excel(upload, sheet_name=None, dtype=str)
            names = list(sheets)
            name = st.selectbox("Sheet", names, key=f"{table}_sheet_{upload.name}") if len(names) > 1 else names[0]
            df = sheets[name]
        else:
            df = None
            for encoding in ("utf-8-sig", "cp1252"):  # Excel "CSV" exports are often Windows-1252
                try:
                    upload.seek(0)
                    df = pd.read_csv(  # sep=None: also semicolon CSVs from French-Canadian Excel
                        upload, dtype=str, keep_default_na=False, encoding=encoding, sep=None, engine="python"
                    )
                    break
                except UnicodeDecodeError:
                    continue
            if df is None:
                st.error("Could not read this file. In Excel, use *Save As → CSV UTF-8* and upload it again.")
                return None
    except Exception as exc:  # empty file, not really a spreadsheet, damaged workbook, ...
        st.error(f"Could not read this file: {exc}")
        return None
    df.columns = [str(c) for c in df.columns]  # a header like 2024 arrives as a number
    return df.fillna("")


def account_manager(store: Store, table: str, noun: str) -> None:
    rows = store.list_accounts(table)
    with st.expander(f"Import {noun}s from CSV or Excel", expanded=not rows, icon=":material/upload:"):
        upload = st.file_uploader("Choose a file exported from your ERP", type=["csv", "xlsx"], key=f"{table}_upload")
        df = _read_upload(upload, table) if upload is not None else None
        if df is not None and df.empty:
            st.warning("This file has no rows under its header line.")
        elif df is not None:
            st.caption(f"{len(df)} rows found. First rows:")
            st.dataframe(df.head(6), hide_index=True)
            columns = list(df.columns)
            optional = ["(none)", *columns]
            c1, c2, c3 = st.columns(3)
            code_col = c1.selectbox(f"Column with the {noun} code", columns, index=_guess(columns, _CODE_HINTS),
                                    key=f"{table}_codecol")  # fmt: skip
            desc_col = c2.selectbox("Description column", optional, index=_guess(optional, _DESC_HINTS, 0),
                                    key=f"{table}_desccol")  # fmt: skip
            cat_col = c3.selectbox("Category column (optional)", optional, index=_guess(optional, _CAT_HINTS, 0),
                                   key=f"{table}_catcol")  # fmt: skip
            replace = st.checkbox("Replace my current list (otherwise new codes are added and existing ones updated)",
                                  key=f"{table}_replace")  # fmt: skip
            if st.button(f"Import {noun}s", type="primary", key=f"{table}_import", icon=":material/upload:"):
                result = store.import_accounts(
                    table,
                    df.to_dict("records"),
                    code_col,
                    None if desc_col == "(none)" else desc_col,
                    None if cat_col == "(none)" else cat_col,
                    replace_all=replace,
                )
                notify(
                    f"Imported: {result['added']} added, {result['updated']} updated, "
                    f"{result['skipped']} skipped (blank or repeated codes)."
                )
                st.rerun()

    if not rows:
        st.caption(f"No {noun}s yet.")
        return

    counts = pd.Series([r["category"] or "Uncategorised" for r in rows]).value_counts()
    st.html(
        " ".join(ui.pill(f"{cat} · {n}", "info" if i == 0 else "gray") for i, (cat, n) in enumerate(counts.items()))
    )
    df = pd.DataFrame(rows)
    df.insert(0, "delete", False)
    edited = st.data_editor(
        df,
        column_config={
            "delete": st.column_config.CheckboxColumn("Delete?", width="small"),
            "code": st.column_config.TextColumn("Code", required=True),
            "description": st.column_config.TextColumn("Description", width="large"),
            "category": st.column_config.TextColumn(
                "Category", help="Group codes however you like, e.g. Opex, Capex, Sales Tax"
            ),
        },
        num_rows="dynamic",
        hide_index=True,
        key=f"{table}_editor_{abs(hash(tuple((r['code'], r['description'], r['category']) for r in rows)))}",
    )
    actions = st.container(horizontal=True)
    if actions.button("Save changes", type="primary", key=f"{table}_save", icon=":material/save:"):
        keep = edited[~edited["delete"].fillna(False).astype(bool)].copy()
        keep["code"] = keep["code"].fillna("").astype(str).str.strip()
        keep = keep[keep["code"] != ""]
        dupes = sorted(keep["code"][keep["code"].duplicated()].unique())
        if dupes:
            st.error(f"These codes appear more than once: {', '.join(dupes)}")
        else:
            removed = len(edited) - len(keep)
            store.save_accounts(table, keep.fillna("").to_dict("records"))
            notify(f"Saved {len(keep)} {noun}s" + (f", removed {removed}." if removed else "."))
            st.rerun()
    actions.download_button(
        f"Download {noun}s",
        df.drop(columns=["delete"]).to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{table}.csv",
        mime="text/csv",
        key=f"{table}_download",
        icon=":material/download:",
    )


def page_accounts() -> None:
    store = get_store()
    show_toast()
    st.html(
        ui.page_header(
            "Setup", "GL accounts & tax", "The codes the AI may use, how each sales tax posts, and your rules."
        )
    )
    gl = store.list_accounts("gl_accounts")
    cc = store.list_accounts("cost_centers")
    mapped = tax_types_mapped(store)
    policy = [n for n in store.get_setting("policy_notes").splitlines() if n.strip() and not n.lstrip().startswith("#")]
    st.html(
        ui.tiles(
            [
                ui.tile(
                    "GL accounts",
                    len(gl),
                    "account_tree",
                    "blue",
                    f"{len({a['category'] for a in gl if a['category']})} categories",
                ),  # fmt: skip
                ui.tile("Cost centers", len(cc) or "—", "apartment", "violet", "" if cc else "optional"),
                ui.tile(
                    "Sales taxes mapped",
                    f"{mapped}/{len(TAX_TYPES)}",
                    "percent",
                    "green" if mapped == len(TAX_TYPES) else "amber",
                    "GST · HST · PST · QST · other",
                ),  # fmt: skip
                ui.tile("Coding rules", len(policy), "rule", "amber", "plain-English policy"),
            ]
        )
    )

    if not gl:
        with card("sample"):
            st.html(
                ui.empty_state("Just exploring?", "Load sample GL accounts, cost centers and tax setup to try the app.")
            )
            if st.button("Load sample setup", icon=":material/download:", type="primary"):
                load_sample_setup(store)
                notify("Sample setup loaded.")
                st.rerun()

    tab_gl, tab_cc, tab_tax, tab_policy = st.tabs(
        [
            ":material/account_tree: GL accounts",
            ":material/apartment: Cost centers",
            ":material/percent: Sales tax",
            ":material/rule: Coding policy",
        ]
    )
    with tab_gl, card("gl"):
        st.caption(
            "The AI may only use the codes listed here. Descriptions matter: the AI matches invoice lines "
            "against them. Use the category column to group codes your own way."
        )
        account_manager(store, "gl_accounts", "GL account")
    with tab_cc, card("cc"):
        st.caption("Optional. Leave empty if you do not code invoices to cost centers.")
        account_manager(store, "cost_centers", "cost center")
    with tab_tax:
        tax_setup(store)
    with tab_policy, card("policy"):
        st.caption(
            "Plain-English rules the AI follows, one per line (e.g. 'Laptops under $2,500 go to 6010'). "
            "Lines starting with # are ignored."
        )
        notes = st.text_area("Coding policy", store.get_setting("policy_notes"), height=260)
        if st.button("Save policy", type="primary", icon=":material/save:"):
            store.set_setting("policy_notes", notes)
            notify("Coding policy saved.")
            st.rerun()


def tax_setup(store: Store) -> None:
    st.caption(
        "Choose how each sales tax is posted. Recoverable taxes (input tax credits / refunds) go to their own "
        "GL account. Non-recoverable PST is normally added to the cost of the expense lines it applies to."
    )
    accounts = store.list_accounts("gl_accounts")
    labels = {"": "(choose an account)"} | {a["code"]: f"{a['code']} · {a['description'][:50]}" for a in accounts}
    options = ["", *(a["code"] for a in accounts)]
    treatments = store.tax_treatments()
    # Widget keys follow the stored data, so the form refreshes after an import or a save.
    version = abs(
        hash((tuple(options), tuple(sorted((t.tax_type, t.treatment, t.gl_code) for t in treatments.values()))))
    )
    where = {"GST": "Federal · all provinces", "HST": "ON · NB · NL · NS · PE", "PST": "BC · SK · MB (RST)",
             "QST": "Quebec (TVQ)", "OTHER": "Outside Canada (US sales tax, VAT)"}  # fmt: skip
    chosen = {}
    with card("taxsetup"):
        for tax_type in TAX_TYPES:
            current = treatments[tax_type]
            c1, c2, c3 = st.columns([2, 4, 4], vertical_alignment="center")
            c1.html(
                f"<div>{ui.tax_chip(tax_type)}</div><div class='apc-muted' style='margin-top:.3rem'>"
                f"{esc(where[tax_type])}</div>"
            )
            treatment = c2.selectbox(
                "Treatment", list(TREATMENTS), index=list(TREATMENTS).index(current.treatment),
                format_func=TREATMENTS.get, key=f"treat_{tax_type}_{version}", label_visibility="collapsed",
            )  # fmt: skip
            gl = ""
            if treatment == "expense_to_line":
                c3.html(ui.pill("Posted with each expense line's GL", "gray", "call_split"))
            else:
                gl = c3.selectbox(
                    "GL account", options, index=options.index(current.gl_code) if current.gl_code in options else 0,
                    format_func=lambda c: labels.get(c, c), key=f"taxgl_{tax_type}_{version}",
                    label_visibility="collapsed",
                )  # fmt: skip
            chosen[tax_type] = (treatment, gl)
        if st.button("Save tax setup", type="primary", icon=":material/save:"):
            for tax_type, (treatment, gl) in chosen.items():
                store.set_tax_treatment(tax_type, treatment, gl)
            notify("Tax setup saved.")
            st.rerun()

    with card("rates"):
        st.markdown("#### :material/calendar_month: Rates in force today")
        st.caption("From `data/canada_tax_rates.csv`. Edit that file when a rate changes.")
        today = dt.date.today()
        rows = [
            [ui.tax_chip(r.tax_type), esc(r.province or "All provinces"), f"<b>{r.rate * 100:.3f}%</b>",
             esc(r.effective_from.isoformat())]
            for r in TaxRateTable.load().rates
            if r.effective_from <= today and (r.effective_to is None or today <= r.effective_to)
        ]  # fmt: skip
        st.html(ui.table(["Tax", "Province", "Rate", "Since"], rows, right=[2]))


# --- Learning & accuracy ----------------------------------------------------------------------------------------


def page_learning() -> None:
    import altair as alt

    store = get_store()
    show_toast()
    st.html(
        ui.page_header("Insights", "Learning & accuracy", "How often the AI gets it right, and what it has learned.")
    )
    m = store.metrics()
    if not m["lines_reviewed"]:
        with card("nolearning"):
            st.html(
                ui.empty_state(
                    "Nothing learned yet",
                    "Accuracy and the AI's memory appear here once invoices are approved in the review queue.",
                    ui.LEARNING_SVG,
                )
            )
        return

    accuracy = m["line_accuracy"] or 0.0
    approved = m["invoices_by_status"].get(APPROVED, 0)
    weekly = pd.DataFrame(m["weekly"])
    weekly["lines"] = weekly["accepted"] + weekly["corrected"]
    weekly["accuracy"] = weekly["accepted"] / weekly["lines"]
    st.html(
        ui.tiles(
            [
                ui.tile(
                    "AI coding accuracy",
                    f"{accuracy:.1%}",
                    "auto_awesome",
                    "green" if accuracy >= TARGET_ACCURACY else "violet",
                    f"{(accuracy - TARGET_ACCURACY) * 100:+.1f} pts vs 90% target",
                    "up" if accuracy >= TARGET_ACCURACY else "down",
                    weekly["accuracy"].tolist() if len(weekly) > 1 else None,
                ),  # fmt: skip
                ui.tile(
                    "Lines reviewed",
                    f"{m['lines_reviewed']:,}",
                    "fact_check",
                    "blue",
                    f"{m['lines_accepted']:,} confirmed",
                ),  # fmt: skip
                ui.tile(
                    "Corrections taught", f"{m['lines_corrected']:,}", "school", "amber", "used on the next invoice"
                ),
                ui.tile(
                    "Approved without edits",
                    f"{m['invoices_approved_without_edits']}/{approved}",
                    "bolt",
                    "green",
                    "straight-through invoices",
                ),  # fmt: skip
            ]
        )
    )

    chart_col, feed_col = st.columns([3, 2], gap="medium")
    with chart_col, card("trend"):
        st.markdown("#### :material/show_chart: Accuracy by week")
        base = alt.Chart(weekly).encode(x=alt.X("week:N", title=None, axis=alt.Axis(labelAngle=0)))
        area = base.mark_area(color=SERIES_BLUE, opacity=0.08).encode(y=alt.Y("accuracy:Q"))
        line = base.mark_line(
            color=SERIES_BLUE, strokeWidth=2.5, point=alt.OverlayMarkDef(size=70, filled=True)
        ).encode(
            y=alt.Y("accuracy:Q", title=None, scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", grid=True)),
            tooltip=[
                alt.Tooltip("week:N", title="Week"),
                alt.Tooltip("accuracy:Q", title="Accuracy", format=".0%"),
                alt.Tooltip("accepted:Q", title="Confirmed"),
                alt.Tooltip("corrected:Q", title="Corrected"),
            ],
        )
        target = (
            alt.Chart(pd.DataFrame({"y": [TARGET_ACCURACY]}))
            .mark_rule(color=TARGET_GRAY, strokeDash=[4, 4])
            .encode(y="y:Q")
        )
        label = (
            alt.Chart(pd.DataFrame({"y": [TARGET_ACCURACY], "text": ["90% target"]}))
            .mark_text(align="left", dx=4, dy=-6, color=TARGET_GRAY)
            .encode(y="y:Q", text="text:N", x=alt.value(0))
        )
        chart = (
            (area + line + target + label)
            .properties(height=260, background="transparent")
            .configure_view(strokeWidth=0)
        )
        st.altair_chart(chart, width="stretch")
        with st.expander("Show as table"):
            st.dataframe(weekly[["week", "accepted", "corrected", "accuracy"]], hide_index=True)

    rows = store.feedback_rows(limit=2000)
    with feed_col, card("feed"):
        st.markdown("#### :material/history_edu: Recent lessons")
        items = []

        def short(text: str, n: int = 42) -> str:
            return text if len(text) <= n else text[: n - 1].rstrip() + "…"

        for r in rows[:6]:
            if r["outcome"] == "corrected":
                was = (
                    f"<span class='apc-strike apc-mono'>{esc(r['suggested_gl'])}</span>"
                    if r["suggested_gl"]
                    else "<i>new line</i>"
                )
                text = (
                    f"<b>{esc(r['reviewer'])}</b> corrected <b>{esc(short(r['description']))}</b> "
                    f"{was}<span class='apc-arrow'>→</span><b class='apc-mono'>{esc(r['final_gl'])}</b>"
                )
            else:
                text = (
                    f"<b>{esc(r['reviewer'])}</b> confirmed <b>{esc(short(r['description']))}</b> "
                    f"<span class='apc-arrow'>→</span><b class='apc-mono'>{esc(r['final_gl'])}</b>"
                )
            items.append(ui.feed_item(r["reviewer"] or "?", f"{text}<br><span class='apc-muted'>"
                                      f"{esc(r['vendor_name'])}</span>", ui.time_ago(r["created_at"])))  # fmt: skip
        st.html(f"<div class='apc-feed'>{''.join(items)}</div>")

    left, right = st.columns(2, gap="medium")
    with left, card("vendors"):
        st.markdown("#### :material/storefront: Accuracy by vendor")
        st.html(
            "".join(
                ui.vendor_row(v["vendor_name"], v["lines"], v["accepted"] / v["lines"], v["corrected"])
                for v in m["by_vendor"][:12]
            )
        )
    with right, card("corrections"):
        st.markdown("#### :material/swap_horiz: Most common corrections")
        if not m["top_corrections"]:
            st.caption("No corrections yet: the AI has matched every reviewer decision.")
        else:
            reference = reference_or_none(store)
            table_rows = []
            for c in m["top_corrections"]:
                before = c["suggested_gl"]
                before_name = gl_name(reference, before) if reference and before != "(new line)" else ""
                after_name = gl_name(reference, c["final_gl"]) if reference else ""
                table_rows.append(
                    [
                        f"<div class='gl'>{esc(before)}<small>{esc(before_name)}</small></div>",
                        "<span class='apc-arrow'>→</span>",
                        f"<div class='gl'>{esc(c['final_gl'])}<small>{esc(after_name)}</small></div>",
                        ui.pill(f"×{c['n']}", "violet"),
                    ]
                )
            st.html(ui.table(["AI suggested", "", "Reviewer chose", "Times"], table_rows, right=[3]))

    with (
        card("memory"),
        st.expander("The AI's memory: every lesson, with the option to forget", icon=":material/psychology:"),
    ):
        st.caption(
            "Lessons from the same vendor are shown to the AI on the next invoice; corrections count most. "
            "Tick *Forget?* to remove a lesson a reviewer got wrong."
        )
        memory_version = rows[0]["id"] if rows else 0
        memory = pd.DataFrame(rows)[
            ["id", "created_at", "vendor_name", "description", "suggested_gl", "final_gl", "final_cc", "outcome",
             "reviewer"]
        ]  # fmt: skip
        memory.insert(0, "forget", False)
        memory["created_at"] = memory["created_at"].str[:10]
        memory["outcome"] = memory["outcome"].map({"accepted": "✓ confirmed", "corrected": "✎ corrected"})
        edited = st.data_editor(
            memory,
            hide_index=True,
            disabled=[c for c in memory.columns if c != "forget"],
            column_config={
                "forget": st.column_config.CheckboxColumn("Forget?", width="small"),
                "id": None,
                "created_at": "Date",
                "vendor_name": "Vendor",
                "description": st.column_config.TextColumn("Line", width="large"),
                "suggested_gl": "AI suggested",
                "final_gl": "Final GL",
                "final_cc": "Cost center",
                "outcome": "Outcome",
                "reviewer": "Reviewer",
            },
            key=f"memory_editor_{memory_version}_{len(rows)}",
        )
        selected = edited.loc[edited["forget"], "id"].tolist()
        if selected and st.button(f"Forget {len(selected)} lesson(s)", icon=":material/delete_sweep:"):
            store.delete_feedback([int(i) for i in selected])
            notify(f"Forgot {len(selected)} lesson(s).", ":material/delete_sweep:")
            st.rerun()


# --- App shell ------------------------------------------------------------------------------------------------

PAGES = {
    "review": st.Page(page_review, title="Review queue", icon=":material/inbox:", default=True),
    "process": st.Page(page_process, title="Process invoices", icon=":material/upload_file:"),
    "accounts": st.Page(page_accounts, title="GL accounts & tax", icon=":material/account_tree:"),
    "learning": st.Page(page_learning, title="Learning & accuracy", icon=":material/insights:"),
}

st.logo(str(ASSETS / "logo.svg"), size="large", icon_image=str(ASSETS / "icon.svg"))
with st.sidebar:
    _store = get_store()
    st.html(ui.sidebar_profile(reviewer(), approved_today(_store), len(_store.list_invoices(REVIEW))))
    with st.expander("Settings", icon=":material/settings:"):
        st.text_input(
            "Your name (recorded on approvals)",
            value=os.environ.get("AP_REVIEWER") or getpass.getuser(),
            key="reviewer",
        )
    st.caption(f":material/lock: Runs on this computer only · `{short_path(DB_PATH)}`")

st.navigation(list(PAGES.values())).run()
