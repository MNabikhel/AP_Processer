"""Month-end: the accruals schedule (costs incurred by the period end that are not in the ERP yet)."""

from __future__ import annotations

import datetime as dt

import streamlit as st

from ap_coder import accruals, ui
from ap_coder.reference_data import short_name
from ap_coder.webapp.common import (
    NEEDS_GL,
    card,
    cc_display,
    esc,
    get_store,
    gl_display,
    money,
    page_head,
    reference_or_none,
    show_toast,
)

ICONS = {accruals.RECEIVED: "inventory", accruals.NOT_IN_ERP: "receipt_long", accruals.RECURRING: "event_repeat"}
TONES = {accruals.RECEIVED: "violet", accruals.NOT_IN_ERP: "blue", accruals.RECURRING: "amber"}
# Headings for each source (the source names themselves go into the CSV unchanged)
TITLES = {
    accruals.RECEIVED: "Received, not invoiced",
    accruals.NOT_IN_ERP: "Invoices not in the ERP yet",
    accruals.RECURRING: "Expected recurring invoices",
}


def page_month_end() -> None:
    store = get_store()
    show_toast()
    page_head(
        "month_end",
        "Month-end",
        "What to accrue: goods received but not invoiced, invoices not in the ERP yet, and regular bills not arrived.",
    )
    with card("me_period"):
        c1, c2 = st.columns([1, 3], vertical_alignment="bottom")
        end = c1.date_input("Period end", accruals.default_period_end(), format="YYYY-MM-DD", key="me_end")
        c2.caption(
            "Amounts are net of recoverable sales tax, in each invoice's currency. Expected recurring invoices are "
            "estimates at the usual amount."
        )
    items = accruals.build(store, end)
    totals = accruals.summary(items)

    rates = store.fx_rates()

    def tile(source: str, n: int, by_currency: dict[str, float]) -> str:
        main = "CAD" if "CAD" in by_currency or not by_currency else sorted(by_currency)[0]
        rest = {c: t for c, t in sorted(by_currency.items()) if c != main}
        others = " · ".join(f"{money(t)} {c}" for c, t in rest.items())
        if rest and main == "CAD" and all(c in rates for c in rest):
            others += f" (≈ {money(sum(t * rates[c] for c, t in rest.items()))} CAD)"
        hint = ui.plural(n, "line") + (f" · plus {others}" if others else "")
        return ui.tile(TITLES[source], f"{money(by_currency.get(main, 0))} {main}", ICONS[source], TONES[source], hint)

    st.html(ui.tiles([tile(source, n, by_currency) for source, (n, by_currency) in totals.items()]))
    if end < dt.date.today() - dt.timedelta(days=31):
        st.caption(
            ":material/info: Received quantities come from the latest purchase-order import (they have no receipt "
            "date), so *Received, not invoiced* reflects what has been received by today, not by the period end."
        )
    if not items:
        with card("me_empty"):
            st.html(
                ui.empty_state(
                    "Nothing to accrue", f"Everything incurred by {end} is already in the ERP.", ui.EMPTY_INBOX_SVG
                )
            )
        return

    reference = reference_or_none(store)

    def gl_text(code: str) -> str:
        row = reference.chart_of_accounts.get(code) if reference and code else None
        name = short_name((row or {}).get("description", ""))
        todo = " todo" if gl_display(code) == NEEDS_GL else ""  # still to code: in the warning colour
        return f"<div class='gl{todo}'>{esc(gl_display(code))}<small>{esc(name)}</small></div>"

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
                    + (f"<small class='apc-muted'>{esc(cc_display(a.cost_center))}</small>" if a.cost_center else ""),
                    f"{money(a.amount)} <span class='apc-muted'>{esc(a.currency)}</span>",
                ]
                for a in items
                if a.source == source
            ]
            if rows:
                st.markdown(f"**{TITLES[source]}**")
                st.html(ui.table(["Vendor", "What", "GL account", "Amount"], rows, right=[3], wrap=[0, 1, 2]))
