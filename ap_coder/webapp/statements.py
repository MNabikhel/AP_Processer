"""Vendor statements: reconcile a vendor's statement of account with the invoices AP Coder holds."""

from __future__ import annotations

from typing import Any

import streamlit as st

from ap_coder import statements as stm
from ap_coder import ui
from ap_coder.safe import md
from ap_coder.webapp.accounts import _read_upload
from ap_coder.webapp.common import card, esc, get_store, money, show_toast

TONES = {
    stm.MATCHED: ("ok", "check"),
    stm.DIFFERS: ("err", "error"),
    stm.NOT_RECEIVED: ("warn", "mail"),
    stm.NOT_ON_STATEMENT: ("info", "help"),
    stm.PAYMENT: ("gray", "payments"),
}
FIELD_LABELS = {"number": "Invoice number *", "date": "Date", "amount": "Amount (or Debit)",
                "credit": "Credit (if separate)", "balance": "Open balance per document", "type": "Type"}  # fmt: skip


def _ap_status(line: stm.Line, invoices: dict[int, dict[str, Any]]) -> str:
    if not line.invoice_id:
        return ""
    inv = invoices.get(line.invoice_id) or {}
    if inv.get("export_batch"):
        return f"Exported (batch {inv['export_batch']})"
    return {"approved": "Approved", "review": "In review", "pending_approval": "Second approval"}.get(
        line.ap_status, line.ap_status
    )


def page_statements() -> None:
    store = get_store()
    show_toast()
    st.html(
        ui.page_header(
            "Reconcile",
            "Vendor statements",
            "Compare a vendor's statement of account with the invoices you hold: find missing invoices and "
            "amount differences before month-end.",
        )
    )
    vendors = store.vendor_summaries()
    if not vendors:
        with card("stm_empty"):
            st.html(ui.empty_state("No vendors yet", "Process a few invoices first; then reconcile their statements."))
        return

    with card("stm_input"):
        names = {v["vendor_key"]: v["vendor_name"] for v in sorted(vendors, key=lambda v: v["vendor_name"] or "")}
        c1, c2 = st.columns([2, 3], vertical_alignment="bottom")
        vendor = c1.selectbox("Vendor", list(names), format_func=names.get, key="stm_vendor")
        upload = c2.file_uploader("Statement (CSV or Excel)", type=["csv", "xlsx"], key="stm_upload")
        st.caption("One row per invoice, credit or payment, as the vendor sent it. Nothing is stored.")
        df = _read_upload(upload, "stm") if upload is not None else None
        if df is None or df.empty:
            if df is not None:
                st.warning("This file has no rows under its header line.")
            return
        columns = list(df.columns)
        guessed = stm.map_columns(columns)
        options = ["(none)", *columns]
        cols = st.columns(len(FIELD_LABELS))
        chosen: dict[str, str] = {}
        for col, (field, label) in zip(cols, FIELD_LABELS.items(), strict=True):
            pick = col.selectbox(label, options, index=options.index(guessed[field]) if field in guessed else 0,
                                 key=f"stm_col_{field}")  # fmt: skip
            if pick != "(none)":
                chosen[field] = pick
        if "number" not in chosen or not ({"amount", "credit", "balance"} & set(chosen)):
            st.warning("Pick the invoice number column and an amount or balance column.")
            return

    invoices = store.vendor_invoices(vendor)
    rec = stm.reconcile(df.to_dict("records"), chosen, invoices)
    by_id = {i["id"]: i for i in invoices}
    st.html(
        ui.tiles(
            [
                ui.tile("Matched", rec.count(stm.MATCHED), "check_circle", "green", money(rec.total(stm.MATCHED))),
                ui.tile(
                    "Amount differs",
                    rec.count(stm.DIFFERS),
                    "error",
                    "amber" if rec.count(stm.DIFFERS) else "green",
                    "same number, different amount",
                ),  # fmt: skip
                ui.tile(
                    "Not received",
                    rec.count(stm.NOT_RECEIVED),
                    "mail",
                    "violet",
                    f"{money(rec.total(stm.NOT_RECEIVED))} to request",
                ),  # fmt: skip
                ui.tile(
                    "Not on statement", rec.count(stm.NOT_ON_STATEMENT), "help", "blue", "in AP Coder for the period"
                ),  # fmt: skip
            ]
        )
    )
    with card("stm_result"):
        head, button = st.columns([3, 1], vertical_alignment="center")
        head.markdown(f"#### :material/fact_check: {md(names[vendor])}")
        button.download_button("Download (CSV)", stm.to_csv(rec), file_name="statement_reconciliation.csv",
                               mime="text/csv", icon=":material/download:", width="stretch")  # fmt: skip
        rows = []
        for li in rec.lines:
            tone, icon = TONES[li.status]
            diff = li.difference
            rows.append(
                [
                    ui.pill(stm.LABELS[li.status], tone, icon),
                    esc(li.number or "—"),
                    esc(li.date),
                    "" if li.statement_amount is None else money(li.statement_amount),
                    "" if li.ap_amount is None else money(li.ap_amount),
                    "" if not diff else f"<b style='color:{ui.ERR}'>{money(diff)}</b>",
                    esc(_ap_status(li, by_id) or li.note),
                ]
            )
        st.html(ui.table(["", "Invoice #", "Date", "Statement", "AP Coder", "Difference", "In AP Coder"], rows,
                         right=[3, 4, 5]))  # fmt: skip
        st.caption(
            "Not received: ask the vendor for a copy (or check it was not sent to someone else). Amount differs: "
            "compare the two documents; a credit note may be missing. Not on statement: the vendor may have "
            "missed it, or it was already paid."
        )
