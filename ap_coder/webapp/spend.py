"""Spend: where the money goes, from approved invoices; and every invoice's data for Excel or Power BI."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import streamlit as st

from ap_coder import spend, ui
from ap_coder.webapp.common import card, esc, get_store, money, reference_or_none, show_toast

TOP = 10


def _table(rows: list[tuple[str, float, int]], label: str, name: dict[str, str], total: float) -> str:
    body = [
        [
            f"<b>{esc(key or '—')}</b>" + (f"<div class='apc-muted'>{esc(name.get(key, ''))}</div>" if name else ""),
            str(n),
            money(amount),
            f"{amount / total:.0%}" if total else "",
        ]
        for key, amount, n in rows[:TOP]
    ]
    return ui.table([label, "Invoices", "Spend", "Share"], body, right=[1, 2, 3], wrap=[0])


def _data_download(store: Any, reference: Any) -> None:
    """Every invoice's data as Excel, built on request (a large database takes a few seconds)."""
    ready = st.session_state.get("spend_xlsx")
    if ready:
        data, made = ready
        st.download_button(
            f"Download data (Excel, {made:%H:%M})", data, file_name=f"ap_coder_data_{made:%Y-%m-%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:", width="stretch", key="spend_download", type="primary",
            on_click=lambda: st.session_state.pop("spend_xlsx", None),
        )  # fmt: skip
    elif st.button(
        "All invoice data (Excel)", icon=":material/table_view:", width="stretch", key="spend_prepare",
        help="Every invoice, its lines and its GL posting, as three Excel tables that join on AP Coder #: ready "
        "for pivot tables or Power BI.",
    ):  # fmt: skip
        with st.spinner("Preparing the workbook…"):
            st.session_state["spend_xlsx"] = (spend.workbook(store, reference), dt.datetime.now())
        st.rerun()


def page_spend() -> None:
    import altair as alt

    store = get_store()
    show_toast()
    st.html(
        ui.page_header(
            "Analyse",
            "Spend",
            "Where the money goes, by month, GL account, cost center and vendor, from approved invoices.",
        )
    )
    reference = reference_or_none(store)
    with card("spend_period"):
        default_start, default_end = spend.default_period()
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1.6], vertical_alignment="bottom")
        with c4:
            _data_download(store, reference)
        start = c1.date_input("From (invoice date)", default_start, key="spend_start")
        end = c2.date_input("To", default_end, key="spend_end")
        rows = spend.lines(store, start, end) if start <= end else []
        currencies = spend.currencies(rows) or ["CAD"]
        currency = c3.selectbox("Currency", currencies, key="spend_currency")
    if start > end:
        st.warning("The start date is after the end date.", icon=":material/event_busy:")
        return
    rows = [r for r in rows if r.currency == currency]
    if not rows:
        with card("spend_empty"):
            st.html(ui.empty_state("No spend yet", f"No approved invoice dated {start} to {end} in {currency}."))
        return

    total = round(sum(r.amount for r in rows), 2)
    invoices = {r.invoice_id for r in rows}
    vendors = spend.total_by(rows, "vendor", currency)
    months = sorted({r.month for r in rows})
    st.html(
        ui.tiles(
            [
                ui.tile("Spend", f"{total:,.0f} {currency}", "payments", "blue", "net of recoverable tax"),
                ui.tile("Invoices", len(invoices), "receipt_long", "violet", f"average {money(total / len(invoices))}"),
                ui.tile(
                    "Vendors",
                    len(vendors),
                    "storefront",
                    "green",
                    f"top one {vendors[0][1] / total:.0%} of spend" if total else "",
                ),
                ui.tile(
                    "Per month",
                    money(total / len(months)),
                    "calendar_month",
                    "amber",
                    f"over {len(months)} month(s) with invoices",
                ),
            ]  # fmt: skip
        )
    )

    accounts = spend.accounts_by_code(reference)
    names = {code: row.get("description", "") for code, row in accounts.items()}
    category = {code: row.get("category", "") for code, row in accounts.items()}
    with card("spend_months"):
        st.markdown("#### :material/bar_chart: By month")
        data = pd.DataFrame(spend.by_month_and_category(rows, currency, category))
        chart = (
            alt.Chart(data)
            .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, **({"size": 46} if len(months) <= 14 else {}))
            .encode(
                x=alt.X("month:N", title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("amount:Q", title=None, stack="zero"),
                color=alt.Color("category:N", title=None, legend=alt.Legend(orient="top")),
                tooltip=["month:N", "category:N", alt.Tooltip("amount:Q", format=",.2f")],
            )
            .properties(height=260, background="transparent")
            .configure_view(stroke=None)
        )
        st.altair_chart(chart, width="stretch")
        st.caption("Coloured by the GL account's category (GL accounts & tax page).")

    left, right = st.columns(2, gap="medium")
    with left, card("spend_gl"):
        st.markdown("#### :material/account_tree: Top GL accounts")
        st.html(_table(spend.total_by(rows, "gl_code", currency), "GL account", names, total))
    with right, card("spend_vendors"):
        st.markdown("#### :material/storefront: Top vendors")
        st.html(_table(vendors, "Vendor", {}, total))
    centers = spend.total_by(rows, "cost_center", currency)
    if any(key for key, _, _ in centers):
        with card("spend_cc"):
            st.markdown("#### :material/domain: By cost center")
            cc_names = {
                code: row.get("description", "") for code, row in spend.accounts_by_code(reference, True).items()
            }
            st.html(_table(centers, "Cost center", cc_names, total))
