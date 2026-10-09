"""Insights: the business case. Volume, straight-through rate, time saved, Azure cost, projection."""

from __future__ import annotations

import datetime as dt
from dataclasses import fields

import pandas as pd
import streamlit as st

from ap_coder import ui
from ap_coder.insights import Assumptions, compute, operations, report_html
from ap_coder.webapp.common import card, esc, get_store, notify, page_head, reviewer, show_toast

OK_GREEN, WARN_AMBER = "#1baf7a", "#eda100"

LABELS = {
    "manual_minutes": ("Manual processing today (min / invoice)", 0.5),
    "clean_minutes": ("Review when the AI is right (min)", 0.5),
    "changed_minutes": ("Review when it needs corrections (min)", 0.5),
    "hourly_cost": ("Loaded AP hourly cost (CAD)", 1.0),
    "monthly_volume": ("Invoices per month", 50),
    "di_usd_per_1000_pages": ("Document Intelligence, USD per 1,000 pages", 0.5),
    "aoai_usd_per_m_input": ("Azure OpenAI input, USD per million tokens", 0.05),
    "aoai_usd_per_m_cached": ("Azure OpenAI cached input, USD per million", 0.05),
    "aoai_usd_per_m_output": ("Azure OpenAI output, USD per million tokens", 0.1),
    "usd_to_cad": ("USD → CAD", 0.01),
}


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0%}"


def _age_color(label: str) -> str:
    return WARN_AMBER if label.startswith(("8", "15")) else OK_GREEN


def _operations_card(store) -> None:
    ops = operations(store)
    with card("operations"):
        st.markdown("#### :material/speed: AP operations")
        median = ops["median_days_to_approve"]
        taken, taken_amount = ops["discounts_in_time"]
        missed, missed_amount = ops["discounts_missed"]
        st.html(
            ui.tiles(
                [
                    ui.tile(
                        "Waiting",
                        ops["waiting"],
                        "hourglass_top",
                        "blue",
                        f"oldest {ops['oldest_days']} day(s)" if ops["waiting"] else "queue is clear",
                    ),
                    ui.tile(
                        "Days to approve",
                        "—" if median is None else f"{median:g}",
                        "timer",
                        "violet",
                        "median, from received to approved",
                    ),
                    ui.tile(
                        "Approved after due",
                        ops["approved_after_due"],
                        "event_busy",
                        "amber" if ops["approved_after_due"] else "green",
                        f"of {ops['approved']} approved",
                    ),
                    ui.tile(
                        "Discounts in time",
                        f"{taken} / {taken + missed}",
                        "sell",
                        "green" if not missed else "amber",
                        f"${taken_amount:,.0f} taken · ${missed_amount:,.0f} missed"
                        if taken + missed
                        else "no discount terms yet",
                    ),
                ]
            )  # fmt: skip
        )
        if ops["waiting"]:
            biggest = max(ops["ageing"].values()) or 1
            st.html(
                "<div style='display:flex;gap:1rem;flex-wrap:wrap'>"
                + "".join(
                    f"<div style='flex:1;min-width:120px'><div class='apc-muted' style='font-size:.8rem'>{esc(label)}"
                    f"</div><div style='height:8px;background:#eef1f6;border-radius:4px;margin:.25rem 0'><div "
                    f"style='height:8px;width:{n / biggest:.0%};background:{_age_color(label)};"
                    f"border-radius:4px'></div></div><b>{n}</b></div>"
                    for label, n in ops["ageing"].items()
                )
                + "</div>"
            )
            st.caption("Waiting invoices by days since they were received (queue and parked).")


def page_insights() -> None:
    import altair as alt

    store = get_store()
    show_toast()
    page_head(
        "insights",
        "Insights",
        "The business case: how much goes straight through, the time it saves and what Azure costs.",
    )
    s = compute(store)
    a: Assumptions = s["assumptions"]
    if not s["processed"]:
        with card("insights_empty"):
            st.html(ui.empty_state("Nothing to measure yet", "Process and approve a few invoices (or load the demo)."))
        return

    cost = s["cost_per_invoice_cad"]
    st.html(
        ui.tiles(
            [
                ui.tile(
                    "Invoices processed",
                    s["processed"],
                    "receipt_long",
                    "blue",
                    f"{s['approved']} approved · {s['failed']} unreadable",
                ),  # fmt: skip
                ui.tile(
                    "Approved as coded",
                    _pct(s["straight_through"]),
                    "bolt",
                    "green",
                    f"{s['approved_clean']} of {s['approved']} needed no change",
                ),  # fmt: skip
                ui.tile(
                    "Hours saved",
                    f"{s['hours_saved']:,.1f}",
                    "schedule",
                    "violet",
                    f"≈ ${s['value_saved']:,.0f} of AP time",
                ),  # fmt: skip
                ui.tile(
                    "Azure cost / invoice",
                    "—" if cost is None else f"${cost:,.3f}",
                    "payments",
                    "amber",
                    f"measured on {s['cost_measured_invoices']} invoice(s)"
                    if cost is not None
                    else "no token usage recorded yet",
                ),  # fmt: skip
            ]
        )
    )

    _operations_card(store)

    p = s["projection"]
    left, right = st.columns([3, 2], gap="medium")
    with left, card("projection"):
        st.markdown(f"#### :material/trending_up: At {a.monthly_volume:,} invoices a month")
        if p:
            net = p["value_saved"] - p["azure_cost"]
            rows = [
                ["AP time saved", f"<b>{p['hours_saved']:,.0f} h</b> / month"],
                [f"Value of that time (${a.hourly_cost:,.0f}/h)", f"<b>${p['value_saved']:,.0f}</b> / month"],
                ["Azure cost", f"${p['azure_cost']:,.0f} / month"],
            ]
            st.html(ui.table(["", ""], rows, right=[1], foot=["Net benefit", f"${net:,.0f} / month"]))
            st.caption(
                "Based on the share approved as coded so far and the assumptions below. It firms up as more "
                "invoices are reviewed."
            )
        else:
            st.caption("Approve a few invoices to see the projection.")
        st.download_button(
            "Download the business case (one page)",
            report_html(s).encode("utf-8"),
            file_name=f"ap_coder_business_case_{dt.date.today()}.html",
            mime="text/html",
            icon=":material/download:",
            help="Totals only: no vendor names, amounts of individual invoices or file names.",
        )
    with right, card("top_issues"):
        st.markdown("#### :material/report: What the checks catch")
        if s["top_issues"]:
            biggest = s["top_issues"][0][1]
            st.html(
                "".join(
                    f"<div style='margin:.35rem 0'><div style='display:flex;justify-content:space-between;"
                    f"font-size:.85rem'><span><code>{esc(code)}</code></span><b>{n}</b></div>"
                    f"<div style='height:6px;background:#eef1f6;border-radius:4px'><div style='height:6px;"
                    f"width:{n / biggest:.0%};background:{WARN_AMBER};border-radius:4px'></div></div></div>"
                    for code, n in s["top_issues"]
                )
            )
            st.caption(f"Sent for a closer look: {_pct(s['needs_attention_rate'])} of processed invoices.")
            if s["duplicates_stopped"]:
                totals = " · ".join(f"{t:,.2f} {c}" for c, t in s["duplicates_stopped_total"].items())
                st.html(ui.pill(f"{s['duplicates_stopped']} duplicate invoice(s) stopped · {totals}", "ok", "shield"))
        else:
            st.caption("No errors or warnings so far.")

    if s["weekly"]:
        with card("weekly"):
            st.markdown("#### :material/bar_chart: Approvals by week")
            data = pd.DataFrame(s["weekly"]).melt(
                id_vars="week", value_vars=["clean", "changed"], var_name="kind", value_name="invoices"
            )
            data["kind"] = data["kind"].map({"clean": "Approved as coded", "changed": "Corrected"})
            chart = (
                alt.Chart(data)
                .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, size=46)
                .encode(
                    x=alt.X("week:N", title=None, axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("invoices:Q", title=None, stack="zero"),
                    color=alt.Color(
                        "kind:N",
                        title=None,
                        legend=alt.Legend(orient="top"),
                        scale=alt.Scale(domain=["Approved as coded", "Corrected"], range=[OK_GREEN, WARN_AMBER]),
                    ),  # fmt: skip
                    tooltip=["week:N", "kind:N", "invoices:Q"],
                )
                .properties(height=240, background="transparent")
                .configure_view(stroke=None)
            )
            st.altair_chart(chart, width="stretch")

    with st.expander("Assumptions (edit to match your team)", icon=":material/tune:"):
        with st.form("assumptions", border=False):
            cols = st.columns(2)
            values = {}
            for i, f in enumerate(fields(Assumptions)):
                label, step = LABELS[f.name]
                current = getattr(a, f.name)
                if f.name == "monthly_volume":
                    values[f.name] = cols[i % 2].number_input(label, min_value=0, value=int(current), step=int(step))
                else:
                    values[f.name] = cols[i % 2].number_input(
                        label, min_value=0.0, value=float(current), step=float(step), format="%.2f"
                    )
            if st.form_submit_button("Save assumptions", type="primary", icon=":material/save:"):
                Assumptions(**values).save(store, actor=reviewer())
                notify("Assumptions saved.", ":material/tune:")
                st.rerun()
        st.caption(
            "Azure prices change by region and agreement; check your Azure invoice. Defaults are list prices "
            "for prebuilt-layout and gpt-4o."
        )
