"""Review queue: the invoice list and the focused review screen (approve teaches the AI)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ap_coder import ui
from ap_coder.bulk import bulk_approve, clean_candidates
from ap_coder.extraction import ExtractionResult
from ap_coder.help import help_for
from ap_coder.memory import ACCEPTED, pair_lines
from ap_coder.pipeline import finalise_coding
from ap_coder.po import match_invoice, po_label
from ap_coder.reference_data import UNASSIGNED, ReferenceData
from ap_coder.review import coding_from_inputs
from ap_coder.schema import PROVINCE_VALUES, InvoiceCoding
from ap_coder.store import APPROVED, FAILED, REJECTED, REVIEW, Store
from ap_coder.suggest import suggest_gl
from ap_coder.tax import PROVINCE_NAMES, TAX_TYPES, province_label
from ap_coder.terms import DUE_SOON_DAYS, payment
from ap_coder.terms import describe as terms_describe
from ap_coder.webapp.common import (
    INVOICE_DIR,
    PAGES,
    TARGET_ACCURACY,
    approved_today,
    card,
    cc_label_map,
    demo_card,
    esc,
    first_name,
    forget_drafts,
    get_settings,
    get_store,
    gl_label_map,
    gl_name,
    history_html,
    money,
    notify,
    persistent_editor,
    reference_or_none,
    render_pages,
    replace_editor,
    reviewer,
    show_toast,
    weekly_accuracy,
)
from ap_coder.webapp.process import run_pipeline, setup_steps

# --- Review queue --------------------------------------------------------------------------------------


QUEUE_PAGE = 50  # queue cards drawn at once; more on request
APPROVED_SHOWN = 100


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
            if view != "attention":
                _bulk_approve_bar(store, reference)
            if not shown:
                st.caption("No invoices match.")
            limit = st.session_state.get("queue_limit", QUEUE_PAGE)
            page_ids = [i["id"] for i in shown[:limit]]
            ai_outputs = {
                r["id"]: r["ai_output"] or {} for r in store.invoice_columns(("id", "ai_output"), ids=page_ids)
            }
            default_days = store.default_terms_days()
            for inv in shown[:limit]:
                _queue_card(inv, ai_outputs.get(inv["id"], {}), default_days)
            if len(shown) > limit:
                more = min(QUEUE_PAGE, len(shown) - limit)
                if st.button(f"Show {more} more · {len(shown) - limit} not shown", icon=":material/expand_more:",
                             key="queue_more", width="stretch"):  # fmt: skip
                    st.session_state["queue_limit"] = limit + QUEUE_PAGE
                    st.rerun()

    with tab_approved:
        if not approved:
            st.caption("No approved invoices yet.")
        else:
            waiting = len(store.unexported_approved())
            st.page_link(
                PAGES["exports"],
                label=f"Export to the ERP · {waiting} ready" if waiting else "Exports",
                icon=":material/ios_share:",
            )
            recent = sorted(approved, key=lambda i: i["reviewed_at"] or "", reverse=True)[:APPROVED_SHOWN]
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
                for i in recent
            ]
            with card("approved_list"):
                st.html(ui.table(["Vendor", "Invoice #", "Date", "Total", "Approved by", "When"], rows, right=[3]))
                if len(approved) > len(recent):
                    st.caption(f"The {len(recent)} most recent of {len(approved)}. Pick any invoice below.")
            labels = {
                i["id"]: f"#{i['id']} · {i['vendor_name']} · {i['invoice_number']}"
                for i in sorted(approved, key=lambda i: i["reviewed_at"] or "", reverse=True)
            }
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
                    else:  # the new attempt replaces the failed one
                        run_pipeline(store, [path])
                        st.rerun()
                if b2.button("Delete", key=f"del_{inv['id']}", icon=":material/delete:"):
                    delete_invoice(store, inv["id"])
                    notify("Deleted. The file moved to invoices/deleted.", ":material/delete:")
                    st.rerun()


def _bulk_approve_bar(store: Store, reference: ReferenceData) -> None:
    """One click for the invoices nobody needs to look at: clean when processed and still clean now."""
    result = st.session_state.pop("bulk_result", None)
    if result and result["skipped"]:
        with st.expander(f"{len(result['skipped'])} invoice(s) were not approved: they need a look", expanded=True,
                         icon=":material/info:"):  # fmt: skip
            st.html(ui.table(["#", "Why"], [[str(i), esc(why)] for i, why in result["skipped"]], wrap=[1]))
    candidates = clean_candidates(store)
    if len(candidates) < 2:
        return
    total = sum(i["grand_total"] or 0 for i in candidates)
    with card("bulk"):
        text, action = st.columns([3, 1.3], vertical_alignment="center")
        text.html(
            f"<div><b>{len(candidates)} invoices look clean</b> <span class='apc-muted'>· no errors or warnings, "
            f"confidence above the threshold · {money(total)} in total</span></div>"
        )
        with action.popover(f"Approve {len(candidates)} clean…", icon=":material/done_all:", width="stretch"):
            st.markdown(
                "These invoices are approved **exactly as the AI coded them**, and each one teaches the AI. "
                "Every invoice is re-checked first; any that is no longer clean is left for you."
            )
            st.html(
                ui.table(
                    ["Vendor", "Invoice", "Total"],
                    [[esc(i["vendor_name"]), esc(i["invoice_number"]), money(i["grand_total"])] for i in candidates],
                    right=[2],
                )
            )
            if st.button("Approve them", type="primary", icon=":material/done_all:", key="bulk_approve"):
                result = bulk_approve(store, reference, get_settings(), [i["id"] for i in candidates], reviewer())
                st.session_state["bulk_result"] = result
                if not store.list_invoices(REVIEW):
                    st.session_state["celebrate"] = True
                notify(f"Approved {len(result['approved'])} invoice(s).", ":material/done_all:")
                st.rerun()


def _due_pill(ai: dict[str, Any], default_days: int) -> str:
    p = payment(ai, default_days)
    if p.discount_open():
        return ui.pill(f"{p.terms.discount_pct:g}% off until {p.discount_by:%b} {p.discount_by.day}", "violet", "sell")
    left = p.days_left()
    if left is None or (ai.get("grand_total") or 0) <= 0:
        return ""
    if left < 0:
        return ui.pill(f"Overdue {-left}d", "err", "schedule")
    if left <= DUE_SOON_DAYS:
        return ui.pill("Due today" if left == 0 else f"Due in {left}d", "warn", "schedule")
    return ""


def _queue_card(inv: dict[str, Any], ai: dict[str, Any], default_days: int = 30) -> None:
    taxes = list(dict.fromkeys(t.get("tax_type", "") for t in ai.get("tax_lines", [])))
    prov = ai.get("ship_to_province") or ai.get("supplier_province") or ""
    with st.container(key=f"qcard_{inv['id']}"):
        st.html(ui.queue_card(inv, taxes, province_label(prov, ""), _due_pill(ai, default_days)))
        label = f"Review invoice from {inv['vendor_name'] or inv['file_name']}"
        if st.button(label, key=f"qopen_{inv['id']}"):
            st.session_state["open_invoice"] = inv["id"]
            st.rerun()


NO_PROVINCE = "—"  # shown instead of an empty province (e.g. GST, which is federal)
QUEUE_SORTS = ("Priority", "Due date: soonest", "Amount: high to low", "Newest invoice date", "Vendor A–Z")


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
    if order == "Due date: soonest":
        return sorted(rows, key=lambda r: (not r.get("due_date"), r.get("due_date") or ""))
    if order == "Amount: high to low":
        return sorted(rows, key=lambda r: -(r.get("grand_total") or 0))
    if order == "Newest invoice date":
        return sorted(rows, key=lambda r: r.get("invoice_date") or "", reverse=True)
    if order == "Vendor A–Z":
        return sorted(rows, key=lambda r: (r.get("vendor_name") or r.get("file_name") or "").lower())
    return rows  # store order: flagged first, then lowest confidence


def _getting_started(store: Store) -> None:
    settings = get_settings()
    steps = setup_steps(store, settings)
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
    demo_card(store, "welcome")


def _document_panel(inv: dict[str, Any], store: Store) -> None:
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
    events = store.events(inv["id"])
    if events:
        with st.expander(f"History · {len(events)}", icon=":material/history:"):
            st.html(history_html(events))


def _checks_html(report: Any) -> str:
    items = []
    for issue in report.issues:
        where = f"Line {issue.line_number}: " if issue.line_number else ""
        about = help_for(issue.code)
        hint = about.action if about and issue.severity != "info" else ""
        if issue.severity == "error":
            items.append(ui.check("error", "Must fix", where + issue.message, issue.code, hint))
        elif issue.severity == "info":
            items.append(ui.check("info", "Good to know", where + issue.message, issue.code))
        else:
            items.append(ui.check("warning", "Worth a look", where + issue.message, issue.code, hint))
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
    if not [i for i in report.issues if i.severity != "info"]:
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
        _document_panel(inv, store)

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
            c1, c2, c3 = st.columns(3)
            header["gst_hst_registration_number"] = text(c1, "Supplier GST/HST #", "gst_hst_registration_number")
            header["qst_registration_number"] = text(c2, "Supplier QST #", "qst_registration_number")
            header["po_number"] = text(c3, "PO #", "po_number", help="Purchase order the invoice quotes, if any")
            c1, c2 = st.columns([2, 1])
            header["payment_terms"] = text(c1, "Payment terms", "payment_terms", placeholder="e.g. Net 30, 2/10 Net 30")
            header["due_date"] = text(
                c2, "Due date", "due_date", placeholder="YYYY-MM-DD",
                help="Only if printed; otherwise it comes from the terms",
            )  # fmt: skip
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
    po_box = st.container()  # the purchase order match, once the edits are valid
    suggest_box = st.container()  # GL suggestions for lines without a usable GL account

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
        _invoice_summary(coding, report, errors, warnings, output, reference, store.default_terms_days())
    with checks_box:
        head, count = st.columns([3, 2], vertical_alignment="center")
        head.markdown("#### :material/fact_check: Checks")
        count.html(
            "<div style='text-align:right'>"
            + (ui.pill(f"{len(errors)} to fix", "err") + " " if errors else "")
            + (ui.pill(f"{len(warnings)} to look at", "warn") if warnings else "")
            + (ui.pill("All clear", "ok", "check") if not (errors or warnings) and not report.requires_review else "")
            + (
                ui.pill("Needs a look", "warn", "visibility")
                if not (errors or warnings) and report.requires_review
                else ""
            )
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
    uncoded_lines = [
        li
        for li in coding.line_items
        if li.predicted_gl_code == UNASSIGNED or reference.chart_of_accounts.get(li.predicted_gl_code) is None
    ]
    if uncoded_lines:
        with suggest_box:
            _suggestion_card(store, reference, coding, uncoded_lines, edited_lines, key)
    if coding.po_number.strip():
        with po_box:
            _po_card(store, coding, invoice_id, edited_lines, key)
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


def _po_card(store: Store, coding: InvoiceCoding, invoice_id: int, edited_lines: pd.DataFrame, key: str) -> None:
    """How the invoice lines compare with the purchase order it quotes (2- or 3-way match)."""
    if not store.has_purchase_orders():
        return
    match = match_invoice(coding, store, invoice_id)
    if match is None:
        return
    with card("pomatch"):
        label = po_label(match.po_number)
        if not match.found:
            st.markdown(f"#### :material/shopping_cart: {esc(label)}")
            st.caption("This PO is not in the purchase orders list. Check the number, or import the PO.")
            st.page_link(PAGES["purchase_orders"], label="Purchase orders", icon=":material/shopping_cart:")
            return
        po = match.po or {}
        problems = sum(1 for m in match.lines if set(m.problems) - {"coding"})
        head, badge = st.columns([3, 2], vertical_alignment="center")
        head.markdown(f"#### :material/shopping_cart: Matched to {esc(label)}")
        badge.html(
            "<div style='text-align:right'>"
            + (
                ui.pill(f"{problems} line(s) to check", "warn")
                if problems
                else ui.pill("Matches the PO", "ok", "check")
            )
            + "</div>"
        )
        received = any(m.received is not None for m in match.lines)
        rows = []
        for m in match.lines:
            if m.po_line is None:
                rows.append([str(m.invoice_line), esc(m.description), "<span class='apc-muted'>not on the PO</span>",
                             "", "", ui.pill("Not on PO", "warn")])  # fmt: skip
                continue
            billed = m.billed_before + m.quantity
            qty = f"{_fmt_qty(billed)} / {_fmt_qty(m.po_quantity)}"
            if received:
                qty += f" / {_fmt_qty(m.received)}"
            price = money(m.unit_price)
            if "price" in m.problems:
                price = (
                    f"<b style='color:{ui.WARN}'>{price}</b> <span class='apc-muted'>PO {money(m.po_unit_price)}</span>"
                )
            flags = {"price": "Price", "quantity": "Over ordered", "received": "Not received", "coding": "Coding"}
            pills = " ".join(
                ui.pill(flags[p], "info" if p == "coding" else "warn") for p in m.problems if p in flags
            ) or ui.pill("OK", "ok", "check")
            rows.append([str(m.invoice_line), esc(m.description), f"{m.po_line} · {esc(m.po_description)}", qty,
                         price, pills])  # fmt: skip
        qty_head = "Billed / ordered" + (" / received" if received else "")
        st.html(ui.table(["#", "Invoice line", "PO line", qty_head, "Unit price", ""], rows, right=[3, 4],
                         wrap=[1, 2]))  # fmt: skip
        notes = [f"PO total {money(po.get('total'))}"]
        if match.other_invoices:
            notes.append(
                f"{money(match.billed_before)} already invoiced on "
                + ", ".join(f"#{o['id']}" for o in match.other_invoices[:5])
            )
        notes.append(
            f"{'closed' if po.get('status') == 'closed' else 'open'}, for {(po.get('vendor_name') or '?').rstrip('.')}"
        )
        st.caption(" · ".join(notes) + ". Billed quantities include earlier invoices on this PO.")
        differs = match.coding_differs
        if differs and st.button(
            f"Use the PO's coding on {len(differs)} line(s)", icon=":material/auto_fix_high:", key=f"{key}_po_coding",
            help="Sets the GL account and cost center of these lines to the ones on the PO",
        ):  # fmt: skip
            updated = edited_lines.copy()
            for m in differs:
                rows_at = updated["line_number"] == m.invoice_line
                if m.po_gl:
                    updated.loc[rows_at, "predicted_gl_code"] = m.po_gl
                if m.po_cc:
                    updated.loc[rows_at, "predicted_cost_center"] = m.po_cc
            replace_editor(f"{key}_lines", updated)
            notify(f"Applied the PO's coding to {len(differs)} line(s).", ":material/auto_fix_high:")
            st.rerun()


def _suggestion_card(
    store: Store, reference: ReferenceData, coding: InvoiceCoding, lines: list[Any], edited_lines: pd.DataFrame,
    key: str,
) -> None:  # fmt: skip
    """One-click GL accounts for lines the AI could not code, from past decisions and the account list."""
    feedback = store.feedback_rows()
    found = [(li, suggest_gl(li.description, coding.vendor_name, feedback, reference)) for li in lines]
    found = [(li, s) for li, s in found if s]
    if not found:
        return
    with card("suggest"):
        st.markdown("#### :material/lightbulb: Suggested GL accounts")
        st.caption("From how similar lines were coded before and from your GL account descriptions.")
        for li, suggestions in found:
            st.html(f"<div style='margin:.3rem 0 .1rem'><b>Line {li.line_number}</b> "
                    f"<span class='apc-muted'>{esc(li.description)}</span></div>")  # fmt: skip
            row = st.container(horizontal=True, gap="small")
            for s in suggestions:
                label = f"{s.gl_code} · {gl_name(reference, s.gl_code) or s.gl_code}"
                if row.button(label, key=f"{key}_sugg_{li.line_number}_{s.gl_code}", icon=":material/add_task:",
                              help="; ".join(s.reasons).capitalize()):  # fmt: skip
                    updated = edited_lines.copy()
                    at = updated["line_number"] == li.line_number
                    updated.loc[at, "predicted_gl_code"] = s.gl_code
                    blank_cc = li.predicted_cost_center in ("", UNASSIGNED)
                    if s.cost_center and blank_cc and reference.cost_centers is not None:
                        updated.loc[at, "predicted_cost_center"] = s.cost_center
                    replace_editor(f"{key}_lines", updated)
                    notify(f"Line {li.line_number} coded to GL {s.gl_code}.", ":material/add_task:")
                    st.rerun()
            st.caption(" · ".join(f"{s.gl_code}: {s.reasons[0]}" for s in suggestions))


def _fmt_qty(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


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
    store.delete_invoice(invoice_id, actor=reviewer())
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


def due_text(coding: dict[str, Any], default_days: int) -> str:
    p = payment(coding, default_days)
    text = terms_describe(p)
    if p.discount_open():
        text += f" · {p.terms.discount_pct:g}% off until {p.discount_by:%b} {p.discount_by.day}"
    return text


def _invoice_summary(
    coding: InvoiceCoding, report: Any, errors: list[Any], warnings: list[Any], output: dict[str, Any],
    reference: ReferenceData, default_days: int = 30,
) -> None:  # fmt: skip
    supply = coding.ship_to_province or coding.supplier_province
    meta = [
        ("receipt_long", f"Invoice {coding.invoice_number}"),
        ("event", coding.invoice_date),
        ("location_on", province_label(supply)),
        ("verified", f"GST/HST {coding.gst_hst_registration_number}" if coding.gst_hst_registration_number else ""),
        ("schedule", due_text(coding.to_output(), default_days)),
    ]
    pills = []
    if errors:
        pills.append(ui.pill(f"{len(errors)} error{'s' if len(errors) > 1 else ''}", "err", "error"))
    if warnings:
        pills.append(ui.pill(f"{len(warnings)} to check", "warn", "warning"))
    if not (errors or warnings) and not report.requires_review:
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
    for e in output.get("gl_distribution") or []:
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
    total = round(sum(e["amount"] for e in output.get("gl_distribution") or []), 2)
    diff = round(total - (output.get("grand_total") or 0), 2)
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
    "vendor_name": "vendor", "invoice_number": "invoice #", "invoice_date": "date", "po_number": "PO #",
    "payment_terms": "terms", "due_date": "due date",
    "currency": "currency",
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
        events = store.events(invoice_id)
        if events:
            with st.expander("History", icon=":material/history:"):
                st.html(history_html(events))
