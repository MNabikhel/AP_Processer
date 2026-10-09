"""Sales tax: the GST/HST and QST to claim back on the return for a period, and the claims to check first."""

from __future__ import annotations

import datetime as dt
from typing import Any

import streamlit as st

from ap_coder import taxreturn, ui
from ap_coder.webapp.common import card, esc, get_store, money, page_head, show_toast

ISSUE_TONES = {
    taxreturn.NO_GST_NUMBER: "err",
    taxreturn.NO_QST_NUMBER: "err",
    taxreturn.FOREIGN: "warn",
    taxreturn.NOT_EXPORTED: "info",
}


def _amounts(by_currency: dict[str, float], rates: dict[str, float]) -> tuple[str, str]:
    """(the CAD total, the other currencies as a hint, with a CAD estimate when Settings has the rate)."""
    others = {c: t for c, t in sorted(by_currency.items()) if c != "CAD"}
    if not others:
        return f"{money(by_currency.get('CAD', 0))} CAD", ""
    text = " · ".join(f"{money(t)} {c}" for c, t in others.items())
    if all(c in rates for c in others):
        estimate = sum(t * rates[c] for c, t in others.items())
        return f"{money(by_currency.get('CAD', 0))} CAD", f"plus {text} (≈ {money(estimate)} CAD)"
    return f"{money(by_currency.get('CAD', 0))} CAD", f"plus {text} to convert"


def page_sales_tax() -> None:
    store = get_store()
    show_toast()
    page_head(
        "sales_tax",
        "Sales tax",
        "The GST/HST and QST you paid to vendors and can claim back on your return, from approved invoices.",
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
    rates = store.fx_rates()
    itc, itc_hint = _amounts(totals[taxreturn.ITC], rates)
    itr, itr_hint = _amounts(totals[taxreturn.ITR], rates)
    invoices = len({c.invoice_id for c in report.claims})
    risky = len({c.invoice_id for c in at_risk})
    st.html(
        ui.tiles(
            [
                ui.tile(
                    "GST/HST to claim",
                    itc,
                    "account_balance",
                    "blue",
                    "ITCs · " + (itc_hint or f"{invoices} invoice(s)"),
                ),
                ui.tile(
                    "QST to claim", itr, "account_balance", "violet", "ITRs" + (f" · {itr_hint}" if itr_hint else "")
                ),
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
    else:
        _claims(report, start, end)
    _self_assessment(store, start, end)


def _self_assessment(store: Any, start: dt.date, end: dt.date) -> None:
    items = taxreturn.self_assessment(store, start, end)
    if not items:
        with card("tax_self_none"):
            st.markdown("#### :material/assignment_return: PST / QST possibly to self-assess")
            st.caption(
                "None in this period: no approved invoice for a supply in BC, SK, MB or Quebec came without the "
                "provincial tax."
            )
        return
    with card("tax_self"):
        head, button = st.columns([3, 1.4], vertical_alignment="center")
        head.markdown("#### :material/assignment_return: PST / QST possibly to self-assess")
        button.download_button(
            "Download (CSV)", taxreturn.self_assessment_csv(items, start, end),
            file_name=f"self_assessment_{start}_{end}.csv", mime="text/csv", icon=":material/download:",
            width="stretch", key="tax_self_download",
        )  # fmt: skip
        totals: dict[tuple[str, str, str], float] = {}
        for s in items:
            k = (s.province, s.tax_type, s.currency)
            totals[k] = totals.get(k, 0.0) + s.estimate
        st.html(
            ui.tiles(
                [
                    ui.tile(
                        f"{tax} {prov}",
                        f"{money(total)} {cur}",
                        "assignment_return",
                        "amber",
                        f"{sum(1 for s in items if (s.province, s.tax_type, s.currency) == (prov, tax, cur))} "
                        "invoice(s), estimate",
                    )
                    for (prov, tax, cur), total in sorted(totals.items())
                ]  # fmt: skip
            )
        )
        rows = [
            [
                f"<b>{esc(s.vendor)}</b><div class='apc-muted'>{esc(s.invoice_number)} · #{s.invoice_id}</div>",
                esc(s.invoice_date),
                f"{esc(s.tax_type)} {esc(s.province)} {s.rate * 100:g}%",
                f"{money(s.base)}",
                f"{money(s.estimate)} <span class='apc-muted'>{esc(s.currency)}</span>",
            ]
            for s in items
        ]
        st.html(ui.table(["Vendor", "Date", "Tax", "Subtotal", "Estimate"], rows, right=[3, 4], wrap=[0]))
        st.caption(
            "These approved invoices are for a supply in a province with PST or QST, and the vendor charged none "
            "(often an out-of-province vendor). Your company may have to self-assess it on its provincial return. "
            "The estimate applies the official rate to the whole subtotal: exempt goods and services owe nothing, "
            "so check each one."
        )


def _claims(report: taxreturn.Report, start: dt.date, end: dt.date) -> None:
    at_risk = report.at_risk()

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
        risky = len({c.invoice_id for c in at_risk})
        only_risky = st.toggle(f"Only the invoices to check ({risky})", value=bool(at_risk), key="tax_risky")
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
