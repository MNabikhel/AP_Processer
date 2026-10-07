"""Sales tax: the GST/HST and QST to claim back on the return for a period, and the claims to check first."""

from __future__ import annotations

import streamlit as st

from ap_coder import taxreturn, ui
from ap_coder.webapp.common import card, esc, get_store, money, show_toast

ISSUE_TONES = {
    taxreturn.NO_GST_NUMBER: "red",
    taxreturn.NO_QST_NUMBER: "red",
    taxreturn.FOREIGN: "amber",
    taxreturn.NOT_EXPORTED: "blue",
}


def _amounts(by_currency: dict[str, float]) -> tuple[str, str]:
    """(the CAD total, the other currencies as a hint)."""
    others = " · ".join(f"{money(t)} {c}" for c, t in sorted(by_currency.items()) if c != "CAD")
    return f"{money(by_currency.get('CAD', 0))} CAD", (f"plus {others} to convert" if others else "")


def page_sales_tax() -> None:
    store = get_store()
    show_toast()
    st.html(
        ui.page_header(
            "Close",
            "Sales tax",
            "The GST/HST and QST you paid to vendors and can claim back on your return, from approved invoices.",
        )
    )
    with card("tax_period"):
        default_start, default_end = taxreturn.default_period()
        c1, c2, c3 = st.columns([1, 1, 2.4], vertical_alignment="bottom")
        start = c1.date_input("From (invoice date)", default_start, key="tax_start")
        end = c2.date_input("To", default_end, key="tax_end")
        c3.caption(
            "Claimed by invoice date. Only taxes set up as *recoverable* (GL accounts & tax page) count; credit "
            "notes reduce the claim."
        )
    if start > end:
        st.warning("The start date is after the end date.", icon=":material/event_busy:")
        return
    report = taxreturn.build(store, start, end)
    totals = report.totals()
    at_risk = report.at_risk()
    itc, itc_hint = _amounts(totals[taxreturn.ITC])
    itr, itr_hint = _amounts(totals[taxreturn.ITR])
    invoices = len({c.invoice_id for c in report.claims})
    risky = len({c.invoice_id for c in at_risk})
    st.html(
        ui.tiles(
            [
                ui.tile(
                    "GST/HST to claim (ITCs)", itc, "account_balance", "blue", itc_hint or f"{invoices} invoice(s)"
                ),
                ui.tile("QST to claim (ITRs)", itr, "account_balance", "violet", itr_hint),
                ui.tile(
                    "Claims to check",
                    risky,
                    "rule",
                    "amber" if risky else "green",
                    "invoice(s) with something to check" if risky else "nothing to check",
                ),
                ui.tile(
                    "Not approved yet",
                    report.not_approved,
                    "pending_actions",
                    "amber" if report.not_approved else "green",
                    "dated in the period, not claimed",
                ),
            ]  # fmt: skip
        )
    )
    if report.not_recoverable:
        st.caption(
            f":material/info: {', '.join(report.not_recoverable)} is set up as not recoverable (expensed), so it is "
            "not claimed here. Change it on the GL accounts & tax page if your company claims it."
        )
    if not report.claims:
        with card("tax_empty"):
            st.html(
                ui.empty_state(
                    "Nothing to claim",
                    f"No approved invoice dated {start} to {end} has recoverable GST/HST or QST.",
                )
            )
        return

    with card("tax_rates"):
        st.markdown("#### :material/percent: By tax and rate")
        rows = [
            [esc(t), esc(p or "—"), f"{r * 100:g}%", f"{money(total)} <span class='apc-muted'>{esc(cur)}</span>"]
            for t, p, r, cur, total in report.by_type()
        ]
        st.html(ui.table(["Tax", "Province", "Rate", "To claim"], rows, right=[2, 3]))
        st.caption(
            "Compare the ITC total with the balance of your GST/HST receivable account in the ERP for the same "
            "period; invoices approved but not exported yet explain part of any difference."
        )

    with card("tax_lines"):
        head, button = st.columns([3, 1.4], vertical_alignment="center")
        head.markdown("#### :material/list_alt: Invoices")
        button.download_button(
            "Download (CSV)", taxreturn.to_csv(report), file_name=f"sales_tax_{start}_{end}.csv", mime="text/csv",
            icon=":material/download:", width="stretch", key="tax_download",
        )  # fmt: skip
        only_risky = st.toggle(f"Only the claims to check ({len(at_risk)})", value=bool(at_risk), key="tax_risky")
        shown = at_risk if only_risky else report.claims
        rows = [
            [
                f"<b>{esc(c.vendor)}</b><div class='apc-muted'>{esc(c.invoice_number)} · #{c.invoice_id}</div>",
                esc(c.invoice_date),
                f"{esc(c.tax_type)} {esc(c.province)} {c.rate * 100:g}%"
                + f"<div class='apc-muted'>{esc(c.registration or 'no number')}</div>",
                f"{money(c.amount)} <span class='apc-muted'>{esc(c.currency)}</span>",
                " ".join(ui.pill(i, ISSUE_TONES.get(i, "gray")) for i in c.issues),
            ]
            for c in shown
        ]
        if rows:
            st.html(ui.table(["Vendor", "Date", "Tax", "Amount", "To check"], rows, right=[3], wrap=[0, 2, 4]))
        else:
            st.caption("Nothing to check.")
        st.caption(
            "An invoice of $30 or more needs the vendor's GST/HST registration number (QST number for QST) for "
            "the tax to be claimed: ask the vendor for a corrected invoice before filing."
        )
