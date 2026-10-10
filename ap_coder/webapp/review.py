"""Review queue: the invoice list and the focused review screen (approve teaches the AI)."""

from __future__ import annotations

import datetime as dt
import functools
import hashlib
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ap_coder import recurring, rules, stamp, ui, vendor_mail
from ap_coder.bulk import bulk_approve, clean_candidates
from ap_coder.capture.types import MISSING, CaptureResult
from ap_coder.capture.workflow import learn_from_approval, on_reopen
from ap_coder.extraction import ExtractionResult
from ap_coder.help import help_for
from ap_coder.memory import ACCEPTED, pair_lines, vendor_key
from ap_coder.pipeline import finalise_coding
from ap_coder.po import match_invoice, po_label
from ap_coder.reference_data import UNASSIGNED, ReferenceData
from ap_coder.review import coding_from_inputs, is_blank, split_line
from ap_coder.safe import md
from ap_coder.schema import PROVINCE_VALUES, InvoiceCoding
from ap_coder.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, Store, load_sample_setup
from ap_coder.suggest import suggest_gl
from ap_coder.tax import OUTSIDE_CANADA, PROVINCE_NAMES, TAX_TYPES, province_label
from ap_coder.terms import DUE_SOON_DAYS, payment
from ap_coder.terms import describe as terms_describe
from ap_coder.webapp.capture_panel import (
    VIEWABLE,
    capture_panel,
    document_head,
    follow_page_reader,
    taught_boxes,
)
from ap_coder.webapp.common import (
    ASSETS,
    INVOICE_DIR,
    PAGES,
    TARGET_ACCURACY,
    approved_today,
    by_currency,
    card,
    cc_display,
    cc_label_map,
    demo_card,
    esc,
    first_name,
    forget_drafts,
    get_settings,
    get_store,
    gl_display,
    gl_label_map,
    gl_name,
    history_html,
    login,
    money,
    notify,
    page_head,
    persistent_editor,
    reference_or_none,
    render_pages,
    replace_editor,
    reviewer,
    show_toast,
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
        st.html(
            ui.page_header("Welcome", f"{ui.greeting()}, {first_name()}", "A few quick steps and you're reviewing.")
        )
        _getting_started(store)
        return

    st.html(f"<style>{_review_css()}</style>")
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
    hello = f"{ui.greeting()}, {first_name()}"
    lead = (
        f"{hello} · {ui.plural(len(pending), 'invoice')} waiting, about {minutes} minute"
        f"{'s' if minutes != 1 else ''} of review"
        if pending
        else f"{hello} · your queue is clear."
    )
    action = page_head("review", "Review queue", lead, action=True)
    with action.container(horizontal=True, horizontal_alignment="right"):
        st.page_link(PAGES["process"], label="Process invoices", icon=":material/upload_file:")

    accuracy = metrics["line_accuracy"]
    corrections = metrics["lines_corrected"]
    st.html(
        "<div class='rq-kpis' role='group' aria-label='The queue at a glance'>"
        + _kpi("Waiting", f"{len(pending):,}", f"{len(flagged)} flagged by checks")
        + _kpi(
            "Approved today",
            f"{done_today:,}",
            f"of {total_today:,} today · {len(approved):,} approved in all",
            bar=done_today / total_today if total_today else 0.0,
        )
        + _kpi(
            "Coding accuracy",
            "—" if accuracy is None else f"{accuracy:.0%}",
            "after the first approvals" if accuracy is None else "vs 90% target",
            delta="" if accuracy is None else f"{(accuracy - TARGET_ACCURACY) * 100:+.1f} pts".replace("-", "−"),
            good=accuracy is not None and accuracy >= TARGET_ACCURACY,
        )
        + _kpi(
            "Lessons learned",
            f"{metrics['lines_reviewed']:,}",
            f"{corrections} correction{'s' if corrections != 1 else ''} taught",
        )
        + "</div>"
    )
    _today_strip(store, invoices)

    st.session_state.pop("celebrate", False)  # the approval toast says it; no confetti in an ERP

    awaiting = [i for i in invoices if i["status"] == PENDING]
    parked = [i for i in invoices if i["status"] == PARKED]
    labels = [f"To review · {len(pending)}"]
    if awaiting or store.approval_limit():
        labels.append(f"Second approval · {len(awaiting)}")
    if parked:
        labels.append(f"Parked · {len(parked)}")
    others_count = sum(1 for i in invoices if i["status"] in (FAILED, REJECTED))
    labels += [f"Approved · {len(approved)}", f"Failed / rejected · {others_count}"]
    tabs = st.tabs(labels)
    tab_review, tab_approved, tab_other = tabs[0], tabs[-2], tabs[-1]
    extra = list(tabs[1:-2])
    if awaiting or store.approval_limit():
        with extra.pop(0):
            _second_approval_tab(store, reference, awaiting)
    if parked:
        with extra.pop(0):
            _parked_tab(store)
    with tab_review:
        if not pending:
            with card("empty"):
                st.html(ui.empty_note("All caught up", " Every invoice has been reviewed.", "task_alt"))
                st.page_link(PAGES["process"], label="Process new invoices", icon=":material/arrow_forward:")
        else:
            _queue_list(store, reference, pending, flagged)

    with tab_approved:
        if not approved:
            with card("approved_empty"):
                st.html(ui.empty_note("No approved invoices yet", " Approved invoices are listed here.", "task_alt"))
        else:
            _approved_tab(store, reference, approved)

    with tab_other:
        others = [i for i in invoices if i["status"] in (FAILED, REJECTED)]
        if not others:
            with card("others_empty"):
                st.html(ui.empty_note("Nothing here", " Invoices that could not be read, or were rejected.",
                                      "check_circle"))  # fmt: skip
        else:
            with card("others"):
                for inv in others:
                    _other_row(store, inv)


def _approved_tab(store: Store, reference: ReferenceData, approved: list[dict[str, Any]]) -> None:
    waiting = len(store.unexported_approved())
    recent = sorted(approved, key=lambda i: i["reviewed_at"] or "", reverse=True)[:APPROVED_SHOWN]
    rows = [
        [
            f"<div class='rq-who'>{_avatar(i['vendor_name'] or '')}<b>{esc(i['vendor_name'])}</b></div>",
            esc(i["invoice_number"]),
            esc(i["invoice_date"]),
            f"{money(i['grand_total'])} <span class='rq-cur'>{esc(i['currency'])}</span>",
            esc(i["reviewer"]) + (f" → {esc(i['second_reviewer'])}" if i.get("second_reviewer") else ""),
            esc(ui.time_ago(i["reviewed_at"])),
        ]
        for i in recent
    ]
    with card("approved_list"):
        bar = st.container(horizontal=True, vertical_alignment="center", key="rq_approved_bar")
        bar.html(f"<div class='rq-card-title'>Recently approved <span>{len(recent)} of {len(approved)}</span></div>")
        bar.space("stretch")
        bar.page_link(
            PAGES["exports"],
            label=f"Export to the ERP · {waiting} ready" if waiting else "Exports",
            icon=":material/ios_share:",
        )
        st.html(ui.table(["Vendor", "Invoice #", "Date", "Total", "Approved by", "When"], rows, right=[3]))
        if len(approved) > len(recent):
            st.caption(f"The {len(recent)} most recent of {len(approved)}. Pick any invoice below.")
    labels = {
        i["id"]: f"{i['vendor_name']} · {i['invoice_number']} · {money(i['grand_total'])} {i['currency']}"
        for i in sorted(approved, key=lambda i: i["reviewed_at"] or "", reverse=True)
    }
    chosen = st.selectbox("View approved invoice", list(labels), format_func=labels.get, key="view_approved")
    render_approved(store, reference, chosen)


def _other_row(store: Store, inv: dict[str, Any]) -> None:
    """A failed or rejected invoice: what happened, and Retry / Reopen / Delete."""
    with st.container(key=f"rqi_failed_{inv['id']}"):
        left, right = st.columns([5, 2], vertical_alignment="center")
        badge = _pill("Failed", "err") if inv["status"] == FAILED else _pill("Rejected")
        error = (inv.get("error") or "No reason recorded.").strip()
        first, _, details = error.partition("\n")
        left.html(
            f"<div class='rq-item'>{_avatar(inv['vendor_name'] or inv['file_name'] or '')}<div class='rq-item-body'>"
            f"<div class='rq-item-title'><b>{esc(inv['vendor_name'] or inv['file_name'])}</b>{badge}</div>"
            f"<div class='rq-item-sub'>{esc(first[:240])}{'…' if len(first) > 240 else ''}</div></div></div>"
        )
        if details.strip() or len(first) > 240:
            with left.expander("Technical details"):
                st.code(error, language=None, wrap_lines=True)
        buttons = right.container(horizontal=True, horizontal_alignment="right")
        if inv["status"] == REJECTED:
            if buttons.button("Reopen", key=f"reopen_{inv['id']}", icon=":material/undo:",
                              help="Back to the review queue (e.g. rejected by mistake)"):  # fmt: skip
                try:
                    store.reopen(inv["id"], reviewer())
                except ValueError as exc:
                    changed_meanwhile(exc)
                notify("Back in the review queue.", ":material/undo:")
                st.rerun()
        elif buttons.button("Retry", key=f"retry_{inv['id']}", icon=":material/refresh:"):
            full = store.get_invoice(inv["id"])
            path = Path(full["source_path"])
            if not path.exists():
                st.error(f"The original file is no longer at {md(path)}.")
            else:  # the new attempt replaces the failed one
                run_pipeline(store, [path])
                st.rerun()
        if buttons.button("Delete", key=f"del_{inv['id']}", icon=":material/delete:"):
            delete_invoice(store, inv["id"])
            notify("Deleted. The file moved to invoices/deleted.", ":material/delete:")
            st.rerun()


def _kpi(label: str, value: str, sub: str, delta: str = "", good: bool = True, bar: float | None = None) -> str:
    """One figure of the KPI strip: label, value, an optional delta (coloured) and a muted line."""
    delta_html = f"<span class='d {'up' if good else 'down'}'>{esc(delta)}</span> " if delta else ""
    bar_html = (
        f"<span class='rq-kpi-bar' aria-hidden='true'><i style='width:{max(0.0, min(1.0, bar)) * 100:.0f}%'></i></span>"
        if bar is not None
        else ""
    )
    return (
        f"<div class='rq-kpi'><div class='l'>{esc(label)}</div><div class='v'>{esc(value)}</div>{bar_html}"
        f"<div class='s'>{delta_html}{esc(sub)}</div></div>"
    )


def _avatar(name: str) -> str:
    """Neutral initials (no colours: colour only means a status)."""
    return f"<span class='rq-av' aria-hidden='true'>{esc(ui.initials(name))}</span>"


def _pill(text: str, tone: str = "") -> str:
    """A status chip: ok, warn, err, accent, or neutral (no tone)."""
    return f"<span class='rq-pill {tone}'>{esc(text)}</span>"


def _tax_chip(tax_type: str) -> str:
    """A neutral tax chip (GST, HST, PST, QST, Other tax)."""
    return f"<span class='rq-tax'>{esc(ui.TAX_LABELS.get(tax_type, tax_type))}</span>"


def _confidence(value: float, threshold: float) -> str:
    """A thin bar with the figure beside it (floored: it never rounds up to certainty)."""
    value = max(0.0, min(1.0, value or 0.0))
    tone = "ok" if value >= threshold else "warn"
    return (
        f"<span class='rq-conf {tone}' title='Confidence; {threshold:.0%} or more needs no second look'>"
        f"<span class='bar' aria-hidden='true'><i style='width:{value * 100:.0f}%'></i></span>"
        f"<b>{math.floor(value * 100 + 1e-9)}%</b></span>"
    )


def _queue_list(
    store: Store, reference: ReferenceData, pending: list[dict[str, Any]], flagged: list[dict[str, Any]]
) -> None:
    """The invoices waiting for review: a toolbar, the clean-invoices bar and one dense list, in one card."""
    with card("queue"):
        with st.container(key="rq_toolbar"):
            bar_filter, bar_search, bar_sort = st.columns([3.2, 2.4, 1.6], vertical_alignment="center")
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
            st.html(ui.empty_note("No invoices match", " Try another search or filter.", "search_off"))
            return
        st.html(
            "<div class='rq-row rq-cols' aria-hidden='true'><span>Vendor</span><span class='c-num'>Invoice #</span>"
            "<span class='c-date'>Date</span><span class='c-prov'>Prov.</span><span class='c-tax'>Tax</span>"
            "<span class='c-status'>Status</span><span class='c-conf'>Confidence</span>"
            "<span class='c-amt'>Amount</span><span></span></div>"
        )
        limit = st.session_state.get("queue_limit", QUEUE_PAGE)
        page_ids = [i["id"] for i in shown[:limit]]
        ai_outputs = {  # a sent-back invoice shows the first approver's corrections, as its review screen does
            r["id"]: r["final_output"] or r["ai_output"] or {}
            for r in store.invoice_columns(("id", "ai_output", "final_output"), ids=page_ids)
        }
        default_days = store.default_terms_days()
        threshold = get_settings().engine.review_threshold
        for inv in shown[:limit]:
            ai = ai_outputs.get(inv["id"], {})
            terms = store.vendor_terms(vendor_key(ai.get("vendor_name") or ""))
            _queue_card(inv, ai, default_days, terms, threshold)
        if len(shown) > limit:
            more = min(QUEUE_PAGE, len(shown) - limit)
            if st.button(f"Show {more} more · {len(shown) - limit} not shown", icon=":material/expand_more:",
                         key="queue_more", width="stretch", type="tertiary"):  # fmt: skip
                st.session_state["queue_limit"] = limit + QUEUE_PAGE
                st.rerun()


def _parked_tab(store: Store) -> None:
    st.caption("Invoices waiting for information. They stay out of the queue until you bring them back.")
    today = dt.date.today().isoformat()
    with card("parked"):
        for inv in store.parked():
            with st.container(key=f"rqi_parked_{inv['id']}"):
                left, right = st.columns([4, 1.4], vertical_alignment="center")
                late = inv["follow_up"] and inv["follow_up"] <= today
                follow = _pill(f"Follow up {inv['follow_up']}", "err" if late else "") if inv["follow_up"] else ""
                left.html(
                    f"<div class='rq-item'>{_avatar(inv['vendor_name'] or '')}<div class='rq-item-body'>"
                    f"<div class='rq-item-title'><b>{esc(inv['vendor_name'])}</b><span class='rq-num'>"
                    f"{esc(inv['invoice_number'])} · {money(inv['grand_total'])} {esc(inv['currency'])}</span>{follow}"
                    f"</div><div class='rq-item-sub'>Waiting for: {esc(inv['parked_reason'])}</div></div></div>"
                )
                if right.button("Back to the queue", key=f"unpark_{inv['id']}", icon=":material/play_circle:",
                                width="stretch"):  # fmt: skip
                    try:
                        store.unpark_invoice(inv["id"], reviewer())
                    except ValueError as exc:
                        changed_meanwhile(exc)
                    notify(f"Invoice #{inv['id']} is back in the review queue.", ":material/play_circle:")
                    st.rerun()


def _notes_card(store: Store, invoice_id: int, key: str) -> None:
    notes = [e for e in store.events(invoice_id) if e["action"] in ("note", "parked", "sent_back")]
    label = f"Notes · {len(notes)}" if notes else "Notes"
    with card("notes"), st.expander(label, icon=":material/sticky_note_2:", expanded=bool(notes)):
        if notes:
            st.html(history_html(notes))
        with st.form(f"{key}_note_form", clear_on_submit=True, border=False):
            text = st.text_input(
                "Add a note for the team", key=f"{key}_note", placeholder="e.g. asked Sam about the PO"
            )
            if st.form_submit_button("Add note", icon=":material/add_comment:"):
                store.add_note(invoice_id, reviewer(), text)
                st.rerun()


def _second_approval_tab(store: Store, reference: ReferenceData, awaiting: list[dict[str, Any]]) -> None:
    limit = store.approval_limit()
    st.caption(
        (f"Invoices over {money(limit)} CAD need a second approver before they can be exported. " if limit else "")
        + "The second approver must be someone other than the first."
    )
    if not awaiting:
        with card("second_empty"):
            st.html(
                ui.empty_note(
                    "Nothing waiting", " Invoices over the approval limit appear here once approved.", "how_to_reg"
                )  # fmt: skip
            )
        return
    me, my_login = reviewer(), login()
    with card("second"):
        for inv in awaiting:
            _second_row(store, reference, inv, me, my_login)


def _second_row(store: Store, reference: ReferenceData, inv: dict[str, Any], me: str, my_login: str) -> None:
    full = store.get_invoice(inv["id"]) or {}
    final = full.get("final_output") or {}
    with st.container(key=f"rqi_second_{inv['id']}"):
        left, right = st.columns([3, 2], vertical_alignment="center")
        left.html(
            f"<div class='rq-item'>{_avatar(inv['vendor_name'] or '')}<div class='rq-item-body'>"
            f"<div class='rq-item-title'><b>{esc(inv['vendor_name'])}</b><span class='rq-num'>"
            f"{esc(inv['invoice_number'])}</span></div><div class='rq-item-sub'>"
            f"{money(inv['grand_total'])} {esc(inv['currency'])} · approved by {esc(inv['reviewer'])} "
            f"{esc(ui.time_ago(inv['reviewed_at']))}</div></div></div>"
        )
        first = next((e for e in store.events(inv["id"]) if e["action"] == "approved"), None)
        first_login = ((first or {}).get("detail") or {}).get("login") or ""
        same = (me or "").strip().casefold() == (inv["reviewer"] or "").strip().casefold() or (
            bool(my_login) and my_login.casefold() == first_login.casefold()
        )
        buttons = right.container(horizontal=True, horizontal_alignment="right")
        if buttons.button(
            "Final approval", type="primary", icon=":material/how_to_reg:", key=f"second_ok_{inv['id']}",
            disabled=same,
            help="You approved it first (same name or computer login): someone else gives the second approval"
            if same
            else None,
        ):  # fmt: skip
            try:
                store.final_approve(inv["id"], me, login=my_login)
            except (ValueError, PermissionError) as exc:
                changed_meanwhile(exc)
            notify(f"{md(inv['vendor_name'])} approved. It is ready to export.", ":material/how_to_reg:")
            st.rerun()
        with buttons.popover("Send back", icon=":material/undo:"):
            reason = st.text_input("Why", key=f"second_reason_{inv['id']}", placeholder="e.g. wrong cost center")
            if st.button("Send back to the queue", key=f"second_back_{inv['id']}"):
                try:
                    store.send_back(inv["id"], me, reason)
                except ValueError as exc:
                    changed_meanwhile(exc)
                notify("Sent back to the review queue.", ":material/undo:")
                st.rerun()
        with st.expander("GL posting and history", icon=":material/account_balance:"):
            _distribution_table(final, reference, final.get("currency", ""))
            st.html(history_html(store.events(inv["id"])))


def _bulk_approve_bar(store: Store, reference: ReferenceData) -> None:
    """One click for the invoices nobody needs to look at: clean when processed and still clean now."""
    result = st.session_state.pop("bulk_result", None)
    if result and result["skipped"]:
        skipped = len(result["skipped"])
        with st.expander(f"{ui.plural(skipped, 'invoice')} {'was' if skipped == 1 else 'were'} not approved: "
                         f"{'it needs' if skipped == 1 else 'they need'} a look", expanded=True,
                         icon=":material/info:"):  # fmt: skip
            rows = []
            for i, why in result["skipped"]:
                inv = store.get_invoice(i) or {}
                name = " · ".join(x for x in (inv.get("vendor_name"), inv.get("invoice_number")) if x) or f"#{i}"
                rows.append([esc(name), esc(why)])
            st.html(ui.table(["Invoice", "Why"], rows, wrap=[1]))
    # Not an invoice with edits on its review screen: bulk approval would approve it without them.
    unsaved = st.session_state.get("unsaved_edits") or set()
    candidates = [i for i in clean_candidates(store) if i["id"] not in unsaved]
    if len(candidates) < 2:
        return
    total = by_currency(candidates)  # per currency, never added together
    with st.container(key="rq_bulk", horizontal=True, vertical_alignment="center"):
        st.html(
            f"<div class='rq-bulk' title='No errors or warnings, confidence above the threshold'>"
            f"{ui.icon('done_all', '18px')}<span><b>{len(candidates)} invoices look clean</b>"
            f"<span class='rq-bulk-sub'> · no errors or warnings · {esc(total)}</span></span></div>"
        )
        with st.popover(f"Approve {len(candidates)}", icon=":material/done_all:"):
            st.markdown(
                "These invoices are approved **exactly as coded**, and each one teaches AP Coder. "
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
                result = bulk_approve(
                    store, reference, get_settings(), [i["id"] for i in candidates], reviewer(), login=login()
                )
                st.session_state["bulk_result"] = result
                if not store.list_invoices(REVIEW):
                    st.session_state["celebrate"] = True
                waiting = sum(1 for i in result["approved"] if (store.get_invoice(i) or {}).get("status") == PENDING)
                notify(
                    f"Approved {ui.plural(len(result['approved']), 'invoice')}"
                    + (f"; {waiting} wait for a second approval." if waiting else "."),
                    ":material/done_all:",
                )
                st.rerun()


def _due_note(ai: dict[str, Any], default_days: int, vendor_terms: str = "") -> tuple[str, str]:
    """(text, tone) of when the invoice is due, for the queue row: tone err (overdue), warn (soon), ok
    (an early-payment discount still open) or "" (plain)."""
    if (ai.get("grand_total") or 0) < 0:  # a credit note is not paid: no due date, no discount
        return "Credit note", ""
    if (ai.get("grand_total") or 0) == 0:
        return "", ""
    p = payment(ai, default_days, vendor_terms)
    if p.discount_open():
        return f"{p.terms.discount_pct:g}% off until {p.discount_by:%b} {p.discount_by.day}", "ok"
    left = p.days_left()
    if left is None:
        return "", ""
    if left < 0:
        return f"Overdue {-left} day{'s' if left != -1 else ''}", "err"
    if left <= DUE_SOON_DAYS:
        return ("Due today" if left == 0 else f"Due in {left} day{'s' if left != 1 else ''}"), "warn"
    return f"Due {p.due:%b} {p.due.day}", ""


def _today_strip(store: Store, invoices: list[dict[str, Any]]) -> None:
    """What needs doing today, beyond the queue itself: one quiet line (nothing when all is calm)."""
    today = dt.date.today().isoformat()
    soon = (dt.date.today() + dt.timedelta(days=DUE_SOON_DAYS)).isoformat()
    me = reviewer().strip().lower()
    # Credit notes are not paid, so they are never past due (as on the queue rows).
    active = [i for i in invoices if i["status"] in (REVIEW, PARKED, PENDING) and (i["grand_total"] or 0) > 0]
    overdue = sum(1 for i in active if i["due_date"] and i["due_date"] < today)
    due_soon = sum(1 for i in active if i["due_date"] and today <= i["due_date"] <= soon)
    follow_ups = sum(1 for i in store.parked() if i.get("follow_up") and i["follow_up"] <= today)
    second = sum(1 for i in invoices if i["status"] == PENDING and (i["reviewer"] or "").strip().lower() != me)
    to_export = len(store.unexported_approved())
    late = [r for r in recurring.detect(store.vendor_invoice_dates()) if r.status == recurring.LATE]
    pills = [
        _pill(f"{overdue} past due, not approved yet", "err") if overdue else "",
        _pill(f"{due_soon} due within {DUE_SOON_DAYS} days", "warn") if due_soon else "",
        _pill(f"{follow_ups} parked to follow up", "warn") if follow_ups else "",
        _pill(f"{second} waiting for your second approval", "accent") if second else "",
        _pill(f"{to_export} approved, ready to export") if to_export else "",
        _pill(
            f"{ui.plural(len(late), 'regular invoice')} late (Vendors page): "
            + ", ".join(r.vendor_name for r in late[:2])
            + ("…" if len(late) > 2 else "")
        )
        if late
        else "",  # fmt: skip
    ]
    if any(pills):
        st.html(f"<div class='rq-today'><b>Today</b>{''.join(p for p in pills if p)}</div>")


def _queue_card(
    inv: dict[str, Any], ai: dict[str, Any], default_days: int = 30, vendor_terms: str = "", threshold: float = 0.85
) -> None:
    """One invoice of the queue: a dense row (the whole row opens the invoice)."""
    taxes = list(dict.fromkeys(t.get("tax_type", "") for t in ai.get("tax_lines", [])))
    prov = ai.get("ship_to_province") or ai.get("supplier_province") or ""
    name = inv.get("vendor_name") or inv.get("file_name") or ""
    due, tone = _due_note(ai, default_days, vendor_terms)
    flagged = bool(inv.get("requires_review"))
    status = _pill("Needs attention", "warn") if flagged else _pill("Ready", "ok")
    prov_short = prov if prov in PROVINCE_NAMES else ("Int’l" if prov == OUTSIDE_CANADA else "—")
    with st.container(key=f"qcard_{inv['id']}"):
        st.html(
            f"<div class='rq-row {'attn' if flagged else 'ready'}'>"
            f"<div class='c-vendor'>{_avatar(name)}<div class='who'><div class='name' title='{esc(name)}'>"
            f"{esc(name)}</div>" + (f"<div class='due {tone}'>{esc(due)}</div>" if due else "") + "</div></div>"
            f"<div class='c-num' title='{esc(inv.get('invoice_number'))}'>{esc(inv.get('invoice_number') or '—')}</div>"
            f"<div class='c-date'>{esc(inv.get('invoice_date') or '—')}</div>"
            f"<div class='c-prov' title='{esc(province_label(prov, 'Province unknown'))}'>{esc(prov_short)}</div>"
            f"<div class='c-tax'>{''.join(_tax_chip(t) for t in taxes if t)}</div>"
            f"<div class='c-status'>{status}</div>"
            f"<div class='c-conf'>{_confidence(inv.get('adjusted_confidence') or 0.0, threshold)}</div>"
            f"<div class='c-amt'>{money(inv.get('grand_total'))}<small>{esc(inv.get('currency') or '')}</small></div>"
            f"<div class='c-chev'>{ui.icon('chevron_right', '18px')}</div></div>"
        )
        label = f"Review invoice from {md(name)}"  # the name is text from the invoice
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
    invoices = store.list_invoices()
    has_invoices = bool(invoices)
    steps.append(("ok" if has_invoices else "todo", "Process your first invoices", "done" if has_invoices else "to do"))
    done = sum(1 for s, _, _ in steps if s == "ok")
    waiting = sum(1 for i in invoices if i["status"] == REVIEW)
    if waiting and reference_or_none(store) is None:
        # Read and waiting: only the chart of accounts stands between AP and the queue.
        with card("waiting_for_gl"):
            st.markdown(f"#### :material/inbox: {waiting} invoice{'s' if waiting != 1 else ''} read and waiting")
            st.caption(
                "Import your GL accounts (the chart of accounts from JD Edwards) to open them, or start with the "
                "sample accounts and switch to yours later."
            )
            row = st.container(horizontal=True)
            row.page_link(PAGES["accounts"], label="Import GL accounts", icon=":material/account_tree:")
            if row.button("Use the sample GL accounts for now", icon=":material/science:", key="use_sample_gl"):
                load_sample_setup(store)
                notify("Sample GL accounts loaded. Replace them with yours in GL accounts & tax.")
                st.rerun()
    with card("onboarding"):
        head, gauge = st.columns([5, 1], vertical_alignment="center")
        head.markdown("#### :material/flag: Getting started")
        head.caption("Finish these steps and invoices will start arriving in your review queue.")
        ring = ui.ring(done / len(steps), size=60, stroke=6, label=f"{done}/{len(steps)}")
        gauge.html(f"<div style='text-align:right'>{ring}</div>")
        st.html("".join(ui.step(s, label, state) for s, label, state in steps))
        links = st.container(horizontal=True)
        if steps[1][0] != "ok":
            links.caption(":material/info: Optional: start LM Studio's server, then check Settings → AI model.")
        links.page_link(PAGES["accounts"], label="GL accounts & tax", icon=":material/account_tree:")
        links.page_link(PAGES["process"], label="Process invoices", icon=":material/upload_file:")
    demo_card(store, "welcome")


def _document_panel(inv: dict[str, Any], store: Store, key: str = "") -> None:
    path = Path(inv["source_path"])
    capture = store.get_capture(inv["id"])
    if capture and path.exists() and path.suffix.lower() in VIEWABLE:
        try:
            capture_panel(inv, capture, key or f"inv{inv['id']}")
        except Exception:  # the plain page below still shows the invoice
            st.caption("The highlighted view could not be shown; the plain page is below.")
        else:
            _history(inv, store)
            return
    try:
        pages = render_pages(str(path), path.stat().st_mtime) if path.exists() else []
    except Exception:  # a damaged file: the extracted text below still shows what was read
        pages = []
        st.warning("This file could not be shown (it may be damaged).", icon=":material/broken_image:")
    head, pager = st.columns([3, 2], vertical_alignment="center")
    head.html(document_head(inv["file_name"]))
    page_no = 1
    if len(pages) > 1:
        page_no = pager.segmented_control(
            "Page", list(range(1, len(pages) + 1)), default=1, key=f"page_{inv['id']}",
            format_func=lambda n: f"Page {n}", label_visibility="collapsed",
        ) or 1  # fmt: skip
    if pages:
        st.image(pages[page_no - 1], width="stretch")
    elif not path.exists():
        st.warning(f"Original file not found at {md(path)}", icon=":material/warning:")
    with st.expander("Extracted text", expanded=not pages, icon=":material/text_snippet:"):
        st.html(f"<div class='rvw-text'>{ui.document_text(inv.get('extraction_md') or '')}</div>")
    _history(inv, store)


def _history(inv: dict[str, Any], store: Store) -> None:
    events = store.events(inv["id"])
    if events:
        with st.expander(f"History · {len(events)}", icon=":material/history:"):
            st.html(history_html(events))


@functools.lru_cache(maxsize=1)
def _review_css() -> str:
    """Styles of the review workspace (assets/review.css), on top of the app's style.css."""
    return (ASSETS / "review.css").read_text(encoding="utf-8")


def _section_head(title: str, hint: str = "", aside: str = "") -> str:
    """A card's title (no icon); ``hint`` (plain text) is a tooltip on an info mark rather than a line of text,
    ``aside`` is HTML on the right (a status chip)."""
    tip = (
        f"<span class='rvw-hint' title='{esc(hint)}' role='note' aria-label='{esc(hint)}'>{ui.icon('info', '15px')}"
        "</span>"
        if hint
        else ""
    )
    side = f"<span class='rvw-sec-aside'>{aside}</span>" if aside else ""
    return f"<div class='rvw-sec-head'><span>{esc(title)}</span>{tip}{side}</div>"


def _group_head(title: str) -> str:
    return f"<div class='rvw-group'>{esc(title)}</div>"


# How sure the capture is of a field, beside its label in the form: a small coloured glyph and the figure.
_CAPTURE_BADGES = {"verified": ("green", "check_circle"), "likely": ("blue", "check"), "check": ("orange", "error"),
                   "failed": ("red", "cancel")}  # fmt: skip


def _capture_confidence(store: Store, invoice_id: int) -> dict[str, tuple[str, float]]:
    """{field: (status, confidence)} of what the capture read from the page (status "failed" when a check failed)."""
    raw = store.get_capture(invoice_id)
    if not raw:
        return {}
    try:
        capture = CaptureResult.from_dict(raw)
    except (KeyError, TypeError, ValueError):
        return {}
    out = {}
    for name, fr in capture.fields.items():
        failed = any(r.startswith("check failed") for r in fr.reasons)
        out[name] = ("failed" if failed and fr.status != MISSING else fr.status, fr.confidence)
    return out


def _with_confidence(label: str, field: str, confidence: dict[str, tuple[str, float]]) -> str:
    status, value = confidence.get(field, ("", 0.0))
    if status not in _CAPTURE_BADGES:
        return label
    color, icon_name = _CAPTURE_BADGES[status]
    words = "check" if status == "failed" else f"{math.floor(value * 100 + 1e-9)}%"  # never rounds up to certainty
    return f"{label} :{color}[:material/{icon_name}:] :gray[{words}]"


def _status_block(tone: str, icon_name: str, title: str, items: list[str], sub: str = "") -> str:
    body = f"<ul>{''.join(items)}</ul>" if items else (f"<div class='rvw-status-sub'>{esc(sub)}</div>" if sub else "")
    return (
        f"<div class='rvw-status {tone}' role='status'><div class='rvw-status-title'>{ui.icon(icon_name, '18px')}"
        f"<span>{esc(title)}</span></div>{body}</div>"
    )


def _grouped(issues: list[Any]) -> list[tuple[bool, str]]:
    """(is error, text) per distinct message: the same finding on several lines is one item ("Lines 1, 3, 4: …")."""
    order: dict[tuple[str, str, str], list[int]] = {}
    for issue in issues:
        order.setdefault((issue.severity, issue.code, issue.message), []).append(issue.line_number or 0)
    out = []
    for (severity, _, message), lines in order.items():
        numbers = sorted({n for n in lines if n})
        where = (
            (f"Line {numbers[0]}: " if len(numbers) == 1 else f"Lines {', '.join(map(str, numbers))}: ")
            if numbers
            else ""
        )
        out.append((severity == "error", where + readable(message, cap=not where)))
    return out


_PLAIN_AMOUNT = re.compile(r"(?<![\d.,])(-?\d{4,})\.(\d{2})(?![\d%])")


def readable(message: str, cap: bool = True) -> str:
    """A check message as a clerk reads it: a capital first letter, amounts with thousands separators."""
    message = _PLAIN_AMOUNT.sub(lambda m: f"{int(m.group(1)):,}.{m.group(2)}", message)
    return message[:1].upper() + message[1:] if cap else message


def _checks_summary(report: Any, errors: list[Any], warnings: list[Any]) -> str:
    """All the checks as one status: passed, or what needs attention (one line each; details in the expander)."""
    low = report.requires_review and not errors
    look = sum(1 for bad, _ in _grouped(warnings) if not bad) + (1 if low else 0)
    items = []
    for bad, text in _grouped(errors + warnings):
        items.append(
            f"<li class='{'err' if bad else 'warn'}' title='{esc(text)}'><span class='m' aria-hidden='true'>"
            f"{'✕' if bad else '!'}</span><span class='rvw-sr'>{'Must fix' if bad else 'Worth a look'}: </span>"
            f"<span class='t'>{esc(text)}</span></li>"
        )
    if low:
        items.append(
            f"<li class='warn'><span class='m' aria-hidden='true'>!</span><span class='rvw-sr'>Worth a look: </span>"
            f"<span class='t'>Confidence {report.adjusted_confidence:.0%} is below the "
            f"{report.review_threshold:.0%} threshold.</span></li>"
        )
    if errors:
        title = f"{len(_grouped(errors))} must be fixed" + (f" · {look} to look at" if look else "")
        return _status_block("err", "error", title, items)
    if look:
        return _status_block("warn", "visibility", f"{look} {'needs' if look == 1 else 'need'} attention", items)
    return _status_block(
        "ok", "check_circle", "All checks passed", [], "Totals reconcile, taxes verified, all codes valid."
    )


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
                f"Confidence {report.adjusted_confidence:.0%} is below the {report.review_threshold:.0%} "
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
            badges.append(_pill("Added by you", "accent"))
        elif before.get("predicted_gl_code") != li.predicted_gl_code:
            badges.append(_pill(f"Changed from {before.get('predicted_gl_code')}", "accent"))
        h = history.get(li.line_number)
        if h and h["status"] == "match":
            badges.append(_pill(f"Matches {h['decisions']} past decisions", "ok"))
        elif h:
            badges.append(_pill(f"Reviewers used {h['history_gl']} before", "warn"))
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


def _apply_rules_button(
    store: Store, coding: InvoiceCoding, edited_lines: pd.DataFrame, key: str, invoice_id: int, ai: dict[str, Any]
) -> None:
    """Lines a fixed coding rule would code differently (e.g. a rule added after the invoice was processed)."""
    _, changes = rules.apply(coding, store.coding_rules())
    if not changes:
        return
    numbers = ", ".join(str(c["line_number"]) for c in changes)
    if st.button(
        f"Apply the coding rules to line {numbers}", icon=":material/rule_settings:", key=f"{key}_apply_rules",
        help=md("; ".join(rules.describe(c) for c in changes).replace("the AI chose", "now")),
    ):  # fmt: skip
        updated = edited_lines.copy()
        done = []
        numbers_in_grid = pd.to_numeric(updated["line_number"], errors="coerce")
        for c in changes:
            rows_at = numbers_in_grid == c["line_number"]
            if not rows_at.any():  # a line just added (not numbered yet): left for the reviewer to code
                continue
            updated.loc[rows_at, "predicted_gl_code"] = c["gl_to"]
            updated.loc[rows_at, "predicted_cost_center"] = c["cc_to"]
            done.append(c)
        replace_editor(f"{key}_lines", updated)
        # Recorded only for the AI's own lines, so its accuracy is measured on its own answer there.
        ai_lines = {li.get("line_number"): li for li in ai.get("line_items") or []}
        store.add_rules_applied(invoice_id, [
            {**c, "gl_from": ai_lines[c["line_number"]].get("predicted_gl_code", ""),
             "cc_from": ai_lines[c["line_number"]].get("predicted_cost_center", "")}
            for c in done
            if c["line_number"] in ai_lines
        ])  # fmt: skip
        changes = done
        notify(f"Coding rules applied to {ui.plural(len(changes), 'line')}.", ":material/rule_settings:")
        st.rerun()


def _ask_vendor(coding: InvoiceCoding, report: Any, key: str) -> None:
    """A ready-to-send email asking the vendor for what the invoice is missing (never the fraud checks)."""
    doc = coding.model_dump()
    if not vendor_mail.points(doc, report.issues):
        return
    with st.expander("Ask the vendor", icon=":material/forward_to_inbox:"):
        language = st.segmented_control(
            "Language", list(vendor_mail.LANGUAGES), format_func=vendor_mail.LANGUAGES.get,
            default=vendor_mail.suggested_language(doc), key=f"{key}_mail_lang",
        ) or vendor_mail.ENGLISH  # fmt: skip
        mail = vendor_mail.draft(doc, report.issues, language, reviewer())
        st.caption("Subject")
        st.code(mail.subject, language=None)
        st.caption("Message (copy it with the icon at the top right, or open it in your email program)")
        st.code(mail.body, language=None, wrap_lines=True)
        st.link_button("Open in email", mail.mailto(), icon=":material/mail:")
        st.caption(
            "Only what the vendor can fix is asked. Internal checks (bank account or GST/HST number changed, "
            "vendor on hold, unusual amount) are never mentioned: confirm those by phone, on a number from your "
            "vendor file."
        )


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
    # A sent-back invoice reopens with the version its first approver approved (corrections kept); the
    # AI's own output stays the reference for what was changed and what is learned.
    start = inv.get("final_output") or ai
    settings = get_settings()
    key = f"inv{invoice_id}"
    meta = inv.get("meta") or {}
    follow_page_reader(invoice_id, key, meta)
    ids = [i["id"] for i in pending]
    position = ids.index(invoice_id)
    st.html(f"<style>{_review_css()}</style>")

    # --- Navigation (with keyboard shortcuts) -------------------------------------------------------
    nav = st.container(horizontal=True, vertical_alignment="center", key="rvw_nav")
    if nav.button("Queue", icon=":material/arrow_back:", type="tertiary", shortcut="Alt+Up"):
        st.session_state.pop("open_invoice", None)
        st.rerun()
    done = (position + 1) / len(ids) * 100
    nav.html(
        f"<span class='rvw-pos'>Reviewing <b>{position + 1}</b> of {len(ids)}<span class='rvw-pos-bar' "
        f"aria-hidden='true'><i style='width:{done:.0f}%'></i></span></span>"
    )
    nav.space("stretch")
    with nav.popover("Shortcuts", icon=":material/keyboard:", type="tertiary"):
        st.html(
            "<table class='apc-table'><tbody>"
            f"<tr><td>{ui.kbd('Ctrl')} + {ui.kbd('Enter')}</td><td>Approve &amp; teach</td></tr>"
            f"<tr><td>{ui.kbd('Alt')} + {ui.kbd('→')}</td><td>Next invoice</td></tr>"
            f"<tr><td>{ui.kbd('Alt')} + {ui.kbd('←')}</td><td>Previous invoice</td></tr>"
            f"<tr><td>{ui.kbd('Alt')} + {ui.kbd('↑')}</td><td>Back to the queue</td></tr>"
            f"<tr><td>{ui.kbd('N')}</td><td>Next field to check (on the page)</td></tr>"
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

    back = next(
        (e for e in store.events(invoice_id) if e["action"] in ("sent_back", "reopened", "approved", "rejected")), None
    )
    if back and back["action"] in ("sent_back", "reopened"):
        reason = (back["detail"] or {}).get("reason") or "no reason given"
        verb = "Sent back" if back["action"] == "sent_back" else "Reopened"
        st.html(
            _status_block("warn", "undo", f"{verb} by {back['actor'] or '?'}", [], f"{reason}. The approved coding "
                          "is kept below; fix what is needed and approve again.")
        )  # fmt: skip
    summary = st.container()  # the invoice header is drawn here once the edits are valid

    # The page on the left (it stays in view while scrolling), what was read and the checks on the right.
    with st.container(key="rvw_split"):
        left, right = st.columns([1.5, 1], gap="medium")
    with left, card("document"):
        _document_panel(inv, store, key)

    with right:
        checks_box = card("checks")
        with card("details"):
            st.html(_section_head("Invoice details"))
            keep = {"persist_state": "session"}  # edits survive moving to another invoice and back
            sure = _capture_confidence(store, invoice_id)  # how sure the capture is of each field, for the labels

            def text(col: Any, label: str, field: str, default: str = "", **kw: Any) -> str:
                label = _with_confidence(label, field, sure)
                return col.text_input(label, start.get(field, default), key=f"{key}_{field}", **keep, **kw)

            def amount(col: Any, label: str, field: str) -> float:
                value = float(start.get(field) or 0)
                label = _with_confidence(label, field, sure)
                return col.number_input(label, value=value, format="%.2f", key=f"{key}_{field}", **keep)

            def province(col: Any, label: str, field: str, **kw: Any) -> str:
                current = start.get(field) or ""
                return col.selectbox(
                    label, PROVINCE_VALUES, index=PROVINCE_VALUES.index(current if current in PROVINCE_VALUES else ""),
                    format_func=prov_label.get, key=f"{key}_{field}", **keep, **kw,
                )  # fmt: skip

            prov_label = {
                p: f"{p} · {PROVINCE_NAMES[p]}" if p in PROVINCE_NAMES else province_label(p, "Unknown")
                for p in PROVINCE_VALUES
            }
            st.html(_group_head("Supplier"))
            header: dict[str, Any] = {"vendor_name": text(st, "Vendor", "vendor_name")}
            c1, c2 = st.columns(2, vertical_alignment="bottom")
            header["gst_hst_registration_number"] = text(
                c1, "GST/HST #", "gst_hst_registration_number", help="The supplier's GST/HST registration number"
            )
            header["qst_registration_number"] = text(
                c2, "QST #", "qst_registration_number", help="The supplier's QST registration number (Québec)"
            )
            c1, c2 = st.columns(2, vertical_alignment="bottom")
            header["supplier_province"] = province(c1, "Supplier province", "supplier_province")
            header["remit_bank_account"] = text(
                c2, "Pay into (bank account)", "remit_bank_account",
                help="The bank details printed for payment, if any: compared with this vendor's earlier invoices",
            )  # fmt: skip

            st.html(_group_head("Invoice"))
            c1, c2 = st.columns(2, vertical_alignment="bottom")
            header["invoice_number"] = text(c1, "Invoice #", "invoice_number")
            header["invoice_date"] = text(
                c2, "Invoice date", "invoice_date", placeholder="YYYY-MM-DD", help="Format YYYY-MM-DD"
            )
            c1, c2 = st.columns(2, vertical_alignment="bottom")
            header["po_number"] = text(c1, "PO #", "po_number", help="Purchase order the invoice quotes, if any")
            header["payment_terms"] = text(c2, "Payment terms", "payment_terms", placeholder="e.g. Net 30, 2/10 Net 30")
            computed = (
                payment(
                    {**start, **header}, store.default_terms_days(),
                    store.vendor_terms(vendor_key(header.get("vendor_name") or "")),
                ).due
                if float(start.get("grand_total") or 0) > 0  # a credit note has no due date
                else None
            )  # fmt: skip
            c1, c2 = st.columns(2, vertical_alignment="bottom")
            header["due_date"] = text(
                c1, "Due date", "due_date",
                placeholder=f"{computed.isoformat()} (from terms)" if computed else "YYYY-MM-DD",
                help="Only if printed; otherwise it comes from the terms (shown greyed)",
            )  # fmt: skip
            header["currency"] = text(c2, "Currency", "currency", "CAD")
            c1, c2 = st.columns(2, vertical_alignment="bottom")
            header["ship_to_province"] = province(
                c1, "Place of supply", "ship_to_province", help="Where goods are delivered / services performed"
            )
            header["original_invoice_number"] = text(
                c2, "Credits invoice #", "original_invoice_number", help="On a credit note: the invoice it credits"
            )

            st.html(_group_head("Amounts"))
            c1, c2, c3 = st.columns(3, vertical_alignment="bottom")
            header["subtotal"] = amount(c1, "Subtotal", "subtotal")
            header["tax_total"] = amount(c2, "Tax total", "tax_total")
            header["grand_total"] = amount(c3, "Total", "grand_total")
            # The fields can't show thousands separators: the same amounts, readable, with whether they add up
            adds_up = abs(header["subtotal"] + header["tax_total"] - header["grand_total"]) < 0.015
            st.html(
                f"<div class='rvw-amounts'>{ui.money(header['subtotal'])} + {ui.money(header['tax_total'])} tax = "
                f"<b>{ui.money(header['grand_total'])}</b> {esc(header.get('currency') or '')}"
                + (
                    f" <span class='rvw-ok'>{ui.icon('check', '14px')}</span>"
                    if adds_up
                    else f" {_pill('does not add up', 'warn')}"
                )
                + "</div>"
            )
        _notes_card(store, invoice_id, key)

    # --- Line items ---------------------------------------------------------------------------------------
    with card("lines"):
        st.html(
            _section_head(
                "Line items", "Click a GL account or cost center cell to change it; add or delete rows at the bottom."
            )
        )  # fmt: skip
        gl_labels = gl_label_map(reference)
        tax_gls = reference.tax.tax_gl_codes()
        gl_options = [UNASSIGNED] + [c for c in reference.chart_of_accounts.codes if c not in tax_gls]
        lines_df = pd.DataFrame(start.get("line_items", []))
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
            "quantity": st.column_config.NumberColumn("Qty", format="localized"),
            "unit_price": st.column_config.NumberColumn("Unit price", format="accounting"),
            "amount": st.column_config.NumberColumn("Amount", format="accounting", width=95),
            "predicted_gl_code": st.column_config.SelectboxColumn(
                "GL account",
                options=gl_options,
                format_func=lambda c: gl_labels.get(c, f"{c} · unknown code"),
                width=200,
                # not required: Streamlit would silently drop a new row whose GL is still blank;
                # a blank GL becomes UNASSIGNED, which blocks approval until a code is picked
            ),  # fmt: skip
            "taxes_applied": st.column_config.MultiselectColumn("Taxes", options=list(TAX_TYPES), width=120),
            "reasoning_justification": st.column_config.TextColumn("Reasoning", disabled=True, width="large"),
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
        _split_popover(edited_lines, gl_options, gl_labels, reference, key)
    po_box = st.container()  # the purchase order match, once the edits are valid
    suggest_box = st.container()  # GL suggestions for lines without a usable GL account

    with card("tax"):  # full width: every column readable at 1366px
        st.html(_section_head("Sales tax"))
        tax_df = pd.DataFrame(
            start.get("tax_lines", []), columns=["tax_type", "province", "rate", "taxable_amount", "tax_amount"]
        )
        tax_df["province"] = tax_df["province"].fillna("").replace("", NO_PROVINCE)
        tax_df.insert(2, "rate_pct", (pd.to_numeric(tax_df.pop("rate"), errors="coerce") * 100).round(4))
        edited_tax = persistent_editor(
            tax_df,
            column_config={
                "tax_type": st.column_config.SelectboxColumn("Tax", options=list(TAX_TYPES), required=True),
                "province": st.column_config.SelectboxColumn(
                    "Province",
                    options=[p or NO_PROVINCE for p in PROVINCE_VALUES],
                    format_func=lambda p: province_label(p) if p == OUTSIDE_CANADA else p,
                ),
                "rate_pct": st.column_config.NumberColumn("Rate", format="%.3f%%", help="Percent: 13% HST = 13"),
                "taxable_amount": st.column_config.NumberColumn("Taxable", format="accounting"),
                "tax_amount": st.column_config.NumberColumn("Amount", format="accounting"),
            },
            num_rows="dynamic",
            hide_index=True,
            key=f"{key}_taxlines",
        )
        tax_check_col = st.container()  # the rate and amount checks, once the edits are valid

    coding, problems = coding_from_inputs(
        header, edited_lines, edited_tax, ai, UNASSIGNED if reference.cost_centers is not None else ""
    )
    if coding is None:
        with checks_box:
            st.html(_status_block("err", "error", "Fix this field to see the checks", []))
            st.html("".join(ui.check("error", "Fix this field", p) for p in problems))
        with card("actionbar"):
            left, right = st.columns([1, 1.5], vertical_alignment="center")
            left.html("<div class='rvw-post-sub'>Fix the highlighted field to see checks and approve.</div>")
            _more_menu(right.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"),
                       store, invoice_id, ids, position, key)  # fmt: skip
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
    # The queue card shows what this screen shows (edits and today's checks included).
    store.refresh_confidence(invoice_id, report.adjusted_confidence, report.requires_review)
    # Edits not approved yet stay on this screen: bulk approval (which takes the stored coding) leaves it alone.
    edited = st.session_state.setdefault("unsaved_edits", set())
    if _differs_from_stored(coding, start):
        edited.add(invoice_id)
    else:
        edited.discard(invoice_id)

    with summary, card("summary"):
        _invoice_summary(
            coding, report, errors, warnings, output, reference, store.default_terms_days(),
            store.vendor_terms(vendor_key(coding.vendor_name)),
        )  # fmt: skip
    with checks_box:
        st.html(_checks_summary(report, errors, warnings))
        applied = meta.get("rules_applied") or []
        with st.expander("Details and what to do", icon=":material/checklist:"):
            st.html(_checks_html(report))
            if applied:
                st.html("".join(ui.check("info", "Coding rule", rules.describe(c)) for c in applied))
        _apply_rules_button(store, coding, edited_lines, key, invoice_id, ai)
        _ask_vendor(coding, report, key)
    with (
        reasons_box,
        st.expander("Why these codes", icon=":material/psychology_alt:"),
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
    with tax_check_col:
        _tax_check_table(coding, reference)

    with card("posting"):
        st.html(_section_head("GL posting preview", "The journal lines approval sends to the ERP."))
        split = _spend_split(output, reference)
        if split:
            st.html(split)
        _distribution_table(output, reference, coding.currency)

    # --- Sticky action bar --------------------------------------------------------------------------------
    changed = _ai_changes(coding, ai)
    with card("actionbar"):
        left, right = st.columns([1, 1.5], vertical_alignment="center")
        with left:
            learn = f"you changed {changed} of its codes" if changed else "all as suggested"
            lines_n = len(coding.line_items)
            st.html(
                f"<div class='rvw-post'><div class='rvw-post-amount'>Post <b>{money(coding.grand_total)}</b> "
                f"{esc(coding.currency)}</div><div class='rvw-post-sub' title='Approving teaches AP Coder from "
                f"{ui.plural(lines_n, 'line')}: {esc(learn)}.'>{ui.icon('school', '15px')} Teaches from {lines_n} "
                f"line{'s' if lines_n != 1 else ''} · {learn}</div></div>"
            )
            allow = True
            uncoded = [str(li.line_number) for li in coding.line_items if li.predicted_gl_code == UNASSIGNED]
            # Where the organisation uses cost centers, a line without one is not posted either.
            no_cc = (
                [str(li.line_number) for li in coding.line_items if li.predicted_cost_center == UNASSIGNED]
                if reference.cost_centers is not None and reference.cost_centers.codes
                else []
            )
            if uncoded or no_cc:
                allow = False  # never post to UNASSIGNED
                pick = [f"a GL account for line {', '.join(uncoded)}"] if uncoded else []
                pick += [f"a cost center for line {', '.join(no_cc)}"] if no_cc else []
                st.html(_pill(f"Pick {' and '.join(pick)} to approve", "warn"))
            elif errors:
                # Keyed on the errors: a new error (an edit after the tick) asks again instead of riding along.
                seen = ",".join(sorted(f"{i.code}:{i.line_number}" for i in errors))
                override_key = f"{key}_override_{hashlib.sha1(seen.encode()).hexdigest()[:10]}"
                allow = st.checkbox(f"Approve anyway, despite {ui.plural(len(errors), 'error')}", key=override_key)
        with right:
            buttons = st.container(
                horizontal=True, horizontal_alignment="right", vertical_alignment="center", wrap=False
            )
            _more_menu(buttons, store, invoice_id, ids, position, key)
            if buttons.button(
                "Approve & teach", type="primary", icon=":material/check:", disabled=not allow,
                key=f"{key}_approve", shortcut="Ctrl+Enter",
            ):  # fmt: skip
                open_issues = [
                    {"code": i.code, "severity": i.severity, "line": i.line_number} for i in errors + warnings
                ]
                try:
                    counts = store.approve_invoice(
                        invoice_id, output, reviewer(), open_issues=open_issues, login=login()
                    )
                except ValueError as exc:
                    changed_meanwhile(exc)
                learn_from_approval(store, invoice_id, output, actor=reviewer(), taught=taught_boxes(key))
                if (store.get_invoice(invoice_id) or {}).get("status") == PENDING:
                    notify("Over the approval limit: it now waits for a second approver.", ":material/how_to_reg:")
                total = sum(counts.values())
                forget_drafts(key)
                _advance(ids, position)
                if not store.list_invoices(REVIEW):  # the whole queue is done, not just the current view
                    st.session_state["celebrate"] = True
                notify(
                    f"Approved {md(coding.vendor_name.rstrip('.'))}. Learned from {ui.plural(total, 'line')}: "
                    f"{counts[ACCEPTED]} confirmed, {total - counts[ACCEPTED]} corrected.",
                    ":material/school:",
                )
                st.rerun()


def _differs_from_stored(coding: InvoiceCoding, stored: dict[str, Any]) -> bool:
    """Whether the coding on screen is not the one stored for the invoice (the reviewer changed something)."""
    try:
        before = InvoiceCoding.model_validate({k: v for k, v in stored.items() if k != "gl_distribution"})
    except ValueError:
        return True
    ignore = {"confidence_score"}
    return {k: v for k, v in coding.to_output().items() if k not in ignore} != {
        k: v for k, v in before.to_output().items() if k not in ignore
    }


def numbered_lines(lines: pd.DataFrame) -> pd.DataFrame:
    """A copy of the line grid with the lines just added numbered as the checks number them (after the last
    line, in order), so an action on "line 6" finds the row the reviewer added."""
    out = lines.copy()
    numbers = pd.to_numeric(out["line_number"], errors="coerce")
    next_no = int(numbers.max()) + 1 if numbers.notna().any() else 1
    for index, row in out.iterrows():
        if pd.notna(numbers[index]) or all(is_blank(row.get(k)) for k in ("description", "amount")):
            continue
        out.at[index, "line_number"] = next_no
        next_no += 1
    out["line_number"] = pd.to_numeric(out["line_number"], errors="coerce")
    return out


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
            st.html(_section_head(label, aside=_pill("Not found", "warn")))
            st.caption("This PO is not in the purchase orders list. Check the number, or import the PO.")
            st.page_link(PAGES["purchase_orders"], label="Purchase orders", icon=":material/shopping_cart:")
            return
        po = match.po or {}
        problems = sum(1 for m in match.lines if set(m.problems) - {"coding"})
        status = _pill(f"{ui.plural(problems, 'line')} to check", "warn") if problems else _pill("Matches the PO", "ok")
        st.html(_section_head(f"Matched to {label}", aside=status))
        received = any(m.received is not None for m in match.lines)
        rows = []
        for m in match.lines:
            if m.po_line is None:
                rows.append([str(m.invoice_line), esc(m.description), "<span class='rvw-muted'>not on the PO</span>",
                             "", "", _pill("Not on PO", "warn")])  # fmt: skip
                continue
            if m.amount_only:  # a PO line with only an amount: compare amounts
                qty = f"{money(m.billed_before_amount + m.amount)} / {money(m.po_amount)}"
            else:
                qty = f"{_fmt_qty(m.billed_before + m.quantity)} / {_fmt_qty(m.po_quantity)}"
                if received:
                    qty += f" / {_fmt_qty(m.received)}"
            price = money(m.unit_price)
            if "price" in m.problems:
                price = f"<b class='rvw-warn'>{price}</b> <span class='rvw-muted'>PO {money(m.po_unit_price)}</span>"
            flags = {"price": "Price", "quantity": "Over ordered", "received": "Not received", "coding": "Coding"}
            pills = " ".join(
                _pill(flags[p], "accent" if p == "coding" else "warn") for p in m.problems if p in flags
            ) or _pill("OK", "ok")
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
        st.caption(md(" · ".join(notes)) + ". Billed quantities include earlier invoices on this PO.")
        differs = match.coding_differs
        if differs and st.button(
            f"Use the PO's coding on {ui.plural(len(differs), 'line')}", icon=":material/auto_fix_high:",
            key=f"{key}_po_coding",
            help="Sets the GL account and cost center of these lines to the ones on the PO",
        ):  # fmt: skip
            updated = numbered_lines(edited_lines)
            for m in differs:
                rows_at = updated["line_number"] == m.invoice_line
                if m.po_gl:
                    updated.loc[rows_at, "predicted_gl_code"] = m.po_gl
                if m.po_cc:
                    updated.loc[rows_at, "predicted_cost_center"] = m.po_cc
            replace_editor(f"{key}_lines", updated)
            notify(f"Applied the PO's coding to {ui.plural(len(differs), 'line')}.", ":material/auto_fix_high:")
            st.rerun()


def _suggestion_card(
    store: Store, reference: ReferenceData, coding: InvoiceCoding, lines: list[Any], edited_lines: pd.DataFrame,
    key: str,
) -> None:  # fmt: skip
    """One-click GL accounts for lines the AI could not code, from past decisions and the account list."""
    feedback = store.feedback_rows()
    default_gl = (store.get_vendor(vendor_key(coding.vendor_name)) or {}).get("default_gl") or ""
    found = [
        (li, suggest_gl(li.description, coding.vendor_name, feedback, reference, default_gl=default_gl)) for li in lines
    ]
    found = [(li, s) for li, s in found if s]
    if not found:
        return
    with card("suggest"):
        st.html(_section_head("Suggested GL accounts"))
        st.caption("From how similar lines were coded before and from your GL account descriptions.")
        for pos, (li, suggestions) in enumerate(found):
            st.html(f"<div class='rvw-sugg-line'><b>Line {li.line_number}</b> "
                    f"<span class='rvw-muted'>{esc(li.description)}</span></div>")  # fmt: skip
            row = st.container(horizontal=True, gap="small")
            for s in suggestions:
                label = md(f"{s.gl_code} · {gl_name(reference, s.gl_code) or s.gl_code}")
                # The position is in the key: two lines can share a number (as read from the invoice).
                if row.button(label, key=f"{key}_sugg_{li.line_number}_{pos}_{s.gl_code}",
                              icon=":material/add_task:", help=md("; ".join(s.reasons).capitalize())):  # fmt: skip
                    updated = numbered_lines(edited_lines)
                    at = _grid_row(updated, coding, li)
                    updated.loc[at, "predicted_gl_code"] = s.gl_code
                    blank_cc = li.predicted_cost_center in ("", UNASSIGNED)
                    if s.cost_center and blank_cc and reference.cost_centers is not None:
                        updated.loc[at, "predicted_cost_center"] = s.cost_center
                    replace_editor(f"{key}_lines", updated)
                    notify(f"Line {li.line_number} coded to GL {s.gl_code}.", ":material/add_task:")
                    st.rerun()
            st.caption(md(" · ".join(f"{s.gl_code}: {s.reasons[0]}" for s in suggestions)))


def _grid_row(lines: pd.DataFrame, coding: InvoiceCoding, li: Any) -> Any:
    """The grid row of a line of the coding: the first, second… row with its number, as it comes in the
    coding (lines that share a number are told apart by their order). Every row with the number if unsure."""
    same = lines["line_number"] == li.line_number
    position = next((n for n, x in enumerate(coding.line_items) if x is li), None)
    if position is None:
        return same
    nth = sum(1 for x in coding.line_items[:position] if x.line_number == li.line_number)
    rows = lines.index[same]
    return [rows[nth]] if nth < len(rows) else same


def _split_popover(
    lines: pd.DataFrame, gl_options: list[str], gl_labels: dict[str, str], reference: ReferenceData, key: str
) -> None:  # fmt: skip
    """Split one line across several GL accounts / cost centers by percentage (shared costs)."""
    descriptions: dict[int, str] = {}
    for rec in lines.to_dict("records"):
        n = pd.to_numeric(rec.get("line_number"), errors="coerce")
        if pd.notna(n):
            descriptions.setdefault(int(n), str(rec.get("description") or ""))
    if not descriptions:
        return
    with st.popover("Split a line…", icon=":material/call_split:", key=f"{key}_split_menu"):
        number = st.selectbox(
            "Line", list(descriptions), format_func=lambda n: f"{n} · {descriptions[n][:50]}", key=f"{key}_split_line"
        )
        row = lines[pd.to_numeric(lines["line_number"], errors="coerce") == number].iloc[0]
        gl, cc = row.get("predicted_gl_code") or UNASSIGNED, row.get("predicted_cost_center") or ""
        has_cc = reference.cost_centers is not None
        start = pd.DataFrame([{"gl": gl, "cc": cc, "pct": 50.0}, {"gl": gl, "cc": cc, "pct": 50.0}])
        config: dict[str, Any] = {
            "gl": st.column_config.SelectboxColumn(
                "GL account", options=gl_options, required=True, format_func=lambda c: gl_labels.get(c, c), width=220
            ),  # fmt: skip
            "pct": st.column_config.NumberColumn(
                "%", min_value=0.0, max_value=100.0, step=1.0, format="%.2f", required=True
            ),  # fmt: skip
        }
        if has_cc:
            config["cc"] = st.column_config.SelectboxColumn(
                "Cost center", options=[UNASSIGNED, *reference.cost_centers.codes], width=150
            )
        parts = st.data_editor(
            start, column_config=config, column_order=["gl", "cc", "pct"] if has_cc else ["gl", "pct"],
            num_rows="dynamic", hide_index=True, key=f"{key}_split_parts_{number}",
        )  # fmt: skip
        total = float(pd.to_numeric(parts["pct"], errors="coerce").fillna(0).sum())
        st.caption(f"Total {total:g}% (must be 100%). Each part keeps the description and taxes of the line.")
        if st.button("Split the line", type="primary", key=f"{key}_split_go", disabled=abs(total - 100) > 0.01):
            chosen = [
                (
                    str(r["gl"] or gl),
                    (str(r["cc"]) if has_cc and r.get("cc") else (cc if has_cc else "")),
                    float(r["pct"]),
                )
                for r in parts.to_dict("records")
                if pd.notna(r.get("pct")) and float(r["pct"]) > 0
            ]
            try:
                replace_editor(f"{key}_lines", split_line(lines, number, chosen))
            except ValueError as exc:
                st.error(str(exc))
            else:
                notify(f"Line {number} split in {len(chosen)}.", ":material/call_split:")
                st.rerun()


def _fmt_qty(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _more_menu(parent: Any, store: Store, invoice_id: int, ids: list[int], position: int, key: str) -> None:
    """Park and Reject beside Approve, Delete in a menu, so the main action stays obvious."""
    # Keyed per invoice, so the next invoice opens with the menus closed.
    with parent.popover("Park", icon=":material/pause_circle:", key=f"{key}_park_menu",
                        help="Set it aside while waiting for information"):  # fmt: skip
        st.caption("Set it aside while waiting for information.")
        why = st.text_input("Waiting for", key=f"{key}_park_reason", placeholder="e.g. buyer to confirm the price")
        follow = st.date_input(
            "Follow up on", value=None, format="YYYY-MM-DD", key=f"{key}_park_date", min_value=dt.date.today()
        )
        if st.button("Park", key=f"{key}_park", icon=":material/pause_circle:", width="stretch",
                     disabled=not why.strip()):  # fmt: skip
            try:
                store.park_invoice(invoice_id, reviewer(), why, follow.isoformat() if follow else None)
            except ValueError as exc:
                changed_meanwhile(exc)
            _advance(ids, position)
            notify(f"Invoice #{invoice_id} parked. It is in the Parked tab.", ":material/pause_circle:")
            st.rerun()
    with parent.popover("Reject", icon=":material/block:", key=f"{key}_reject_menu",
                        help="Not ours, or not to be paid"):  # fmt: skip
        st.caption("Not ours, or not to be paid. It can be reopened later.")
        reason = st.text_input("Reason", key=f"{key}_reason", placeholder="e.g. not our invoice")
        if st.button("Reject", key=f"{key}_reject", icon=":material/block:", width="stretch"):
            try:
                store.reject_invoice(invoice_id, reviewer(), reason)
            except ValueError as exc:
                changed_meanwhile(exc)
            forget_drafts(key)
            _advance(ids, position)
            notify(f"Invoice #{invoice_id} rejected.", ":material/block:")
            st.rerun()
    with parent.popover("", icon=":material/more_horiz:", key=f"{key}_more", help="More: delete the invoice"):
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


def changed_meanwhile(exc: Exception) -> None:
    """Someone else acted on the invoice after this screen was drawn: say so and show where it stands now."""
    notify(f"Not done: someone else changed this invoice meanwhile ({md(exc)}).", ":material/sync_problem:")
    st.rerun()


def due_text(coding: dict[str, Any], default_days: int, vendor_terms: str = "") -> str:
    p = payment(coding, default_days, vendor_terms)
    text = terms_describe(p) + (" (vendor master terms)" if p.source == "vendor" else "")
    if p.discount_open():
        text += f" · {p.terms.discount_pct:g}% off until {p.discount_by:%b} {p.discount_by.day}"
    return text


def _invoice_summary(
    coding: InvoiceCoding, report: Any, errors: list[Any], warnings: list[Any], output: dict[str, Any],
    reference: ReferenceData, default_days: int = 30, vendor_terms: str = "",
) -> None:  # fmt: skip
    """The invoice at a glance: supplier, number, dates, status, confidence and the amount, on one row."""
    supply = coding.ship_to_province or coding.supplier_province
    meta = [
        (f"Invoice {coding.invoice_number}", "Invoice number"),
        (f"Issued {coding.invoice_date}" if coding.invoice_date else "", "Invoice date"),
        (due_text(coding.to_output(), default_days, vendor_terms) if coding.grand_total > 0 else "", "Payment due"),
        (province_label(supply), "Place of supply"),
        (f"GST/HST {coding.gst_hst_registration_number}" if coding.gst_hst_registration_number else "",
         "Supplier's GST/HST number"),
    ]  # fmt: skip
    look = len(warnings) + (1 if report.requires_review and not errors else 0)
    if errors:
        status = _pill(f"{len(errors)} to fix", "err")
    elif look:
        status = _pill("Needs attention", "warn")
    else:
        status = _pill("Ready to approve", "ok")
    extras = [_tax_chip(t) for t in dict.fromkeys(t.tax_type for t in coding.tax_lines)]
    if any(h["status"] == "match" for h in report.checks.get("history") or []):
        extras.append(_pill("Learned pattern"))
    meta_html = "".join(f"<span class='m' title='{esc(about)}'>{esc(t)}</span>" for t, about in meta if t)
    st.html(
        f"<div class='rvw-head'><div class='rvw-head-main'>"
        f"<div class='rvw-head-title'><span class='rvw-vendor'>{esc(coding.vendor_name or 'Unknown vendor')}</span>"
        f"{status}{_confidence(report.adjusted_confidence, report.review_threshold)}</div>"
        f"<div class='rvw-head-meta'>{meta_html}<span class='chips'>{''.join(extras)}</span></div></div>"
        f"<div class='rvw-head-total'><span>{'Credit' if coding.grand_total < 0 else 'Total due'}</span>"
        f"<b>{money(coding.grand_total)}<small>{esc(coding.currency)}</small></b></div></div>"
    )


def _spend_split(output: dict[str, Any], reference: ReferenceData) -> str:
    """Where the money goes: expense GLs (incl. non-recoverable tax) and recoverable tax accounts, as one thin
    bar in shades of the accent (no rainbow: colour only means a status) and a legend."""
    by_gl: dict[str, float] = {}
    for e in output["gl_distribution"]:
        name = gl_name(reference, e["gl_code"])
        if e["kind"] != "expense":
            label = f"{name or e['gl_code'] or 'Unmapped'} (tax)"
        elif e["gl_code"] == UNASSIGNED or not e["gl_code"]:
            label = "No GL account yet"
        else:
            label = f"{e['gl_code']} {name}".strip()
        by_gl[label] = by_gl.get(label, 0.0) + e["amount"]
    segments = sorted(((k, v) for k, v in by_gl.items() if v > 0), key=lambda kv: -kv[1])
    if not segments:  # a credit note: nothing to draw
        return ""
    if len(segments) > 6:
        segments = [*segments[:5], ("Other", sum(v for _, v in segments[5:]))]
    total = sum(v for _, v in segments)
    bars, legend = [], []
    for i, (label, amount) in enumerate(segments):
        share = amount / total
        title = f"{label}: {money(amount)} ({share:.0%})"
        bars.append(f"<i class='s{i}' style='flex:{share:.4f}' title='{esc(title)}'></i>")
        legend.append(f"<span><i class='s{i}'></i>{esc(label)} <b>{money(amount)}</b> <em>{share:.0%}</em></span>")
    return (
        f"<div class='rvw-split' role='img' aria-label='Spend by account'><div class='bar'>{''.join(bars)}</div>"
        f"<div class='legend'>{''.join(legend)}</div></div>"
    )


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
                f"{_tax_chip(t.tax_type)} {esc(t.province)}",
                f"{mark(rate_ok)} {t.rate * 100:.3f}%",
                "not levied" if official is None else f"{official * 100:.3f}%",
                f"{mark(math_ok)} {money(t.tax_amount)}",
                esc(gl or "⚠ not mapped"),
            ]
        )
    st.html(
        "<div class='rvw-subhead'>Rate and amount check</div>"
        + ui.table(["Tax", "Rate", "Official", "Charged", "Posts to"], rows, right=[1, 2, 3], wrap=[4])
    )


def _distribution_table(output: dict[str, Any], reference: ReferenceData, currency: str) -> None:
    rows = []
    for e in output.get("gl_distribution") or []:
        kind = "<span class='rvw-kind'>Tax</span>" if e["kind"] == "tax" else (
            f"<span class='rvw-kind'>Line {e['line_number']}</span>"
        )  # fmt: skip
        name = gl_name(reference, e["gl_code"])
        code = gl_display(e["gl_code"]) if e["gl_code"] else "⚠ not mapped"
        todo = " todo" if e["gl_code"] in (UNASSIGNED, "") else ""  # still to pick: in the warning colour
        gl = f"<div class='gl{todo}'>{esc(code)}<small>{esc(name)}</small></div>"
        rows.append(
            [
                kind,
                gl,
                esc(cc_display(e["cost_center"])),
                esc(e["description"]),
                money(e["net_amount"]) if e["kind"] == "expense" else "",
                money(e["non_recoverable_tax"]) if e["non_recoverable_tax"] else "",
                f"<b>{money(e['amount'])}</b>",
            ]
        )
    total = round(sum(e["amount"] for e in output.get("gl_distribution") or []), 2)
    diff = round(total - (output.get("grand_total") or 0), 2)
    balance = _pill("Balanced", "ok") if abs(diff) < 0.005 else _pill(f"Off by {money(diff)}", "err")
    foot = ["", balance, "", "Total", "", "", f"{money(total)} <span class='rq-cur'>{esc(currency)}</span>"]
    st.html(
        "<div class='rvw-gl'>"
        + ui.table(
            ["", "GL account", "Cost center", "Description", "Net", "Non-rec. tax", "Amount"],
            rows,
            right=[4, 5, 6],
            foot=foot,
            wrap=[3],
        )
        + "</div>"
    )


EDIT_LABELS = {
    "line_coding": "GL coding", "line_count": "lines added or removed", "tax_lines": "sales tax",
    "vendor_name": "vendor", "invoice_number": "invoice #", "invoice_date": "date", "po_number": "PO #",
    "payment_terms": "terms", "due_date": "due date",
    "currency": "currency",
    "supplier_province": "supplier province", "ship_to_province": "place of supply",
    "gst_hst_registration_number": "GST/HST #", "qst_registration_number": "QST #",
    "original_invoice_number": "invoice credited", "remit_bank_account": "bank account", "subtotal": "subtotal",
    "tax_total": "tax total", "grand_total": "total",
}  # fmt: skip


def gl_label_names(reference: ReferenceData) -> dict[str, str]:
    return {row[reference.chart_of_accounts.key_column]: row.get("description", "") for row in
            reference.chart_of_accounts.rows}  # fmt: skip


def render_approved(store: Store, reference: ReferenceData, invoice_id: int) -> None:
    inv = store.get_invoice(invoice_id)
    final = inv["final_output"] or {}
    edits = inv.get("edits") or []
    with card(f"approved_{invoice_id}"):
        st.html(
            f"<div class='rq-item'>{_avatar(final.get('vendor_name', ''))}<div class='rq-item-body'>"
            f"<div class='rq-item-title'><b>{esc(final.get('vendor_name'))}</b>"
            f"<span class='rq-num'>{money(final.get('grand_total'))} {esc(final.get('currency'))}</span></div>"
            f"<div class='rq-item-sub'>Invoice {esc(final.get('invoice_number'))}"
            f" · approved by {esc(inv['reviewer'])} {esc(ui.time_ago(inv['reviewed_at']))} · "
            + (
                "no changes to the suggested coding"
                if not edits
                else "changed: " + esc(", ".join(EDIT_LABELS.get(e, e) for e in edits))
            )
            + "</div></div></div>"
        )
        _distribution_table(final, reference, final.get("currency", ""))
        pdf, fix = st.columns([1, 1], vertical_alignment="center")
        pdf.download_button(
            "Approved PDF", stamp.stamped_pdf(inv, gl_label_names(reference)), file_name=stamp.file_name(inv),
            mime="application/pdf", icon=":material/approval:", key=f"approved_pdf_{invoice_id}",
            help="The invoice with an APPROVED stamp and its coding page, to attach in the ERP or to file.",
        )  # fmt: skip
        if inv.get("export_batch"):
            fix.caption(
                f"Exported in batch {inv['export_batch']}: to correct it, undo the batch on the Exports page first."
            )
        else:
            with fix.popover("Reopen for correction…", icon=":material/undo:"):
                why = st.text_input("What needs correcting", key=f"reopen_reason_{invoice_id}")
                if st.button("Reopen", key=f"reopen_{invoice_id}", type="primary", disabled=not why.strip()):
                    on_reopen(store, invoice_id, reviewer(), why)
                    try:
                        store.reopen(invoice_id, reviewer(), why)
                    except ValueError as exc:
                        changed_meanwhile(exc)
                    st.session_state["open_invoice"] = invoice_id
                    notify("Reopened: correct it and approve it again.", ":material/undo:")
                    st.rerun()
        events = store.events(invoice_id)
        if events:
            with st.expander("History", icon=":material/history:"):
                st.html(history_html(events))
