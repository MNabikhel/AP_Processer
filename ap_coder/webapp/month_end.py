"""Month-end: the accruals schedule (costs incurred by the period end that are not in the ERP yet)."""

from __future__ import annotations

import streamlit as st

from ap_coder import accruals, ui
from ap_coder.webapp.common import card, esc, get_store, money, reference_or_none, show_toast

ICONS = {accruals.RECEIVED: "inventory", accruals.NOT_IN_ERP: "receipt_long", accruals.RECURRING: "event_repeat"}
TONES = {accruals.RECEIVED: "violet", accruals.NOT_IN_ERP: "blue", accruals.RECURRING: "amber"}


def page_month_end() -> None:
    store = get_store()
    show_toast()
    st.html(
        ui.page_header(
            "Close",
            "Month-end",
            "What to accrue: goods received but not invoiced, invoices not in the ERP yet, and regular bills "
            "that have not arrived.",
        )
    )
    with card("me_period"):
        c1, c2 = st.columns([1, 3], vertical_alignment="bottom")
        end = c1.date_input("Period end", accruals.default_period_end(), key="me_end")
        c2.caption(
            "Amounts are net of recoverable sales tax, in each invoice's currency. Expected recurring invoices are "
            "estimates at the usual amount."
        )
    items = accruals.build(store, end)
    totals = accruals.summary(items)
    st.html(
        ui.tiles(
            [
                ui.tile(source, money(total), ICONS[source], TONES[source], f"{n} line(s)")
                for source, (n, total) in totals.items()
            ]
        )
    )
    if not items:
        with card("me_empty"):
            st.html(ui.empty_state("Nothing to accrue", f"Everything incurred by {end} is already in the ERP."))
        return

    reference = reference_or_none(store)

    def gl_text(code: str) -> str:
        row = reference.chart_of_accounts.get(code) if reference and code else None
        name = (row or {}).get("description", "").split(" - ")[0]
        return f"<div class='gl'>{esc(code or '—')}<small>{esc(name)}</small></div>"

    with card("me_gl"):
        st.markdown("#### :material/account_balance: By GL account")
        totals = accruals.by_gl(items)
        half = (len(totals) + 1) // 2
        cols = st.columns(2) if len(totals) > 4 else [st.container()]
        for col, chunk in zip(cols, (totals[:half], totals[half:]) if len(cols) == 2 else (totals,), strict=True):
            rows = [
                [gl_text(gl), f"{money(total)} <span class='apc-muted'>{esc(cur)}</span>"] for gl, cur, total in chunk
            ]
            col.html(ui.table(["GL account", "Accrue"], rows, right=[1]))
        st.caption("Reverse these entries on the first day of the next period, as usual.")

    with card("me_lines"):
        head, button = st.columns([3, 1.4], vertical_alignment="center")
        head.markdown("#### :material/list_alt: Accruals schedule")
        button.download_button(
            "Download (CSV)", accruals.to_csv(items, end), file_name=f"accruals_{end}.csv", mime="text/csv",
            icon=":material/download:", width="stretch", key="me_download",
        )  # fmt: skip
        for source in accruals.SOURCES:
            rows = [
                [
                    f"<b>{esc(a.vendor)}</b><div class='apc-muted'>{esc(a.reference)}</div>",
                    esc(a.description),
                    gl_text(a.gl_code)
                    + (f"<small class='apc-muted'>{esc(a.cost_center)}</small>" if a.cost_center else ""),
                    f"{money(a.amount)} <span class='apc-muted'>{esc(a.currency)}</span>",
                ]
                for a in items
                if a.source == source
            ]
            if rows:
                st.markdown(f"**{source}**")
                st.html(ui.table(["Vendor", "What", "GL account", "Amount"], rows, right=[3], wrap=[0, 1, 2]))
