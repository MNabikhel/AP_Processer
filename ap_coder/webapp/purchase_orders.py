"""Purchase orders: import open POs from the ERP; invoices quoting a PO are matched against it."""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

from ap_coder import po as po_mod
from ap_coder import ui
from ap_coder.safe import md
from ap_coder.store import Store, load_sample_purchase_orders
from ap_coder.webapp.accounts import _read_upload
from ap_coder.webapp.common import card, esc, get_store, money, notify, page_head, reviewer, show_toast

FIELD_LABELS = {
    "po_number": "PO number",
    "vendor_name": "Vendor",
    "line_number": "Line number",
    "description": "Description",
    "quantity": "Quantity ordered",
    "unit_price": "Unit price",
    "amount": "Line amount",
    "received_quantity": "Quantity received",
    "gl_code": "GL account",
    "cost_center": "Cost center",
    "status": "Status (open / closed)",
}
STATUS_FILTERS = ("Open", "Closed", "All")


def _bar(fraction: float) -> str:
    width = max(0.0, min(fraction, 1.0))
    color = ui.ERR if fraction > 1.02 else (ui.OK if fraction >= 0.98 else ui.BRAND)
    return (
        "<div style='min-width:90px;height:6px;background:#eef1f6;border-radius:4px'>"
        f"<div style='height:6px;width:{width:.0%};background:{color};border-radius:4px'></div></div>"
    )


def _qty(value: float | None) -> str:
    return "—" if value is None else po_mod._qty(value)


def _status_pill(po: dict[str, Any]) -> str:
    if po["status"] == po_mod.CLOSED:
        return ui.pill("Closed", "gray", "lock")
    if po["total"] and po["billed"] > po["total"] * (1 + po_mod.TOTAL_TOLERANCE) + 0.01:
        return ui.pill("Over-billed", "err", "error")
    if po["total"] and po["billed"] >= po["total"] - 0.01:
        return ui.pill("Fully billed", "ok", "check")
    if po["billed"]:
        return ui.pill("Partly billed", "info", "hourglass_bottom")
    return ui.pill("Open", "violet", "shopping_cart")


def _importer(store: Store, has_pos: bool) -> None:
    with st.expander("Import purchase orders from CSV or Excel", expanded=not has_pos, icon=":material/upload:"):
        st.caption(
            "One row per PO line, as exported from your ERP. Re-importing a PO replaces its lines, so a fresh "
            "export also updates the quantities received. Columns are recognised automatically; check them below."
        )
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        upload = c1.file_uploader("PO lines file", type=["csv", "xlsx"], key="po_upload")
        c2.download_button(
            "Template", po_mod.template_csv(), file_name="purchase_orders_template.csv", mime="text/csv",
            icon=":material/download:", width="stretch",
        )  # fmt: skip
        df = _read_upload(upload, "po") if upload is not None else None
        if df is not None and df.empty:
            st.warning("This file has no rows under its header line.")
        elif df is not None:
            st.caption(f"{len(df)} rows found. First rows:")
            st.dataframe(df.head(5), hide_index=True)
            columns = list(df.columns)
            guessed = po_mod.map_columns(columns)
            options = ["(none)", *columns]
            chosen: dict[str, str] = {}
            cols = st.columns(4)
            for i, (field, label) in enumerate(FIELD_LABELS.items()):
                current = guessed.get(field)
                pick = cols[i % 4].selectbox(
                    label + (" *" if field in po_mod.REQUIRED_COLUMNS else ""), options,
                    index=options.index(current) if current in options else 0, key=f"po_col_{field}",
                )  # fmt: skip
                if pick != "(none)":
                    chosen[field] = pick
            missing = [FIELD_LABELS[f] for f in po_mod.REQUIRED_COLUMNS if f not in chosen]
            if "quantity" not in chosen and "unit_price" not in chosen and "amount" not in chosen:
                missing.append("a quantity, unit price or amount")
            replace = st.checkbox("Replace all my purchase orders with this file", key="po_replace")
            if missing:
                st.warning("Pick the column for: " + ", ".join(missing))
            if st.button("Import purchase orders", type="primary", icon=":material/upload:", disabled=bool(missing),
                         key="po_import"):  # fmt: skip
                rows, skipped = po_mod.rows_from_records(df.to_dict("records"), chosen)
                result = store.import_purchase_orders(rows, replace_all=replace, actor=reviewer())
                notify(
                    f"Imported {result['orders']} PO(s) with {result['lines']} line(s): {result['added']} new, "
                    f"{result['updated']} updated" + (f", {skipped} row(s) skipped (no PO number or description)."
                                                       if skipped else "."),
                    ":material/shopping_cart:",
                )  # fmt: skip
                st.rerun()
        if not has_pos:
            st.divider()
            st.caption("Just trying it out? Load the sample POs that go with the sample invoices.")
            if st.button("Load sample purchase orders", icon=":material/science:", key="po_samples"):
                keys = load_sample_purchase_orders(store)
                notify(f"Loaded {len(keys)} sample purchase orders.", ":material/shopping_cart:")
                st.rerun()


def _detail(store: Store, po: dict[str, Any]) -> None:
    full = store.purchase_order(po["po_key"])
    if full is None:
        return
    invoices = store.po_invoices(po["po_key"])
    billed = po_mod.billed_by_line(full, invoices)
    billed_amount = po_mod.billed_by_line(full, invoices, amounts=True)
    rows = []
    for li in full["lines"]:
        if li.get("amount_only"):  # services / lump sums: amounts, not quantities
            done_amount = billed_amount.get(li["line_number"], 0.0)
            over = done_amount > li["amount"] * (1 + po_mod.TOTAL_TOLERANCE) + 0.01
            flag = (
                ui.pill("Over-billed", "err") if over
                else ui.pill("Billed", "ok", "check") if done_amount >= li["amount"] - 0.01
                else ""
            )  # fmt: skip
            rows.append(
                [str(li["line_number"]), esc(li["description"]), "amount", "—", f"<b>{money(done_amount)}</b>", "—",
                 money(li["amount"]), esc(" · ".join(x for x in (li["gl_code"], li["cost_center"]) if x)), flag]
            )  # fmt: skip
            continue
        done = billed.get(li["line_number"], 0.0)
        over = done > li["quantity"] + 1e-6
        unreceived = li["received_quantity"] is not None and done > li["received_quantity"] + 1e-6
        flag = (
            ui.pill("Over-billed", "err") if over
            else ui.pill("Not received", "warn") if unreceived
            else ui.pill("Billed", "ok", "check") if done >= li["quantity"] - 1e-6
            else ""
        )  # fmt: skip
        rows.append(
            [
                str(li["line_number"]),
                esc(li["description"]),
                _qty(li["quantity"]),
                _qty(li["received_quantity"]),
                f"<b>{_qty(done)}</b>",
                money(li["unit_price"]),
                money(li["amount"]),
                esc(" · ".join(x for x in (li["gl_code"], li["cost_center"]) if x)),
                flag,
            ]
        )
    st.html(
        ui.table(
            ["#", "Description", "Ordered", "Received", "Billed", "Unit price", "Amount", "Coding", ""],
            rows,
            right=[2, 3, 4, 5, 6],
            wrap=[1],
            foot=["", "Total", "", "", "", "", money(full["total"]), "", ""],
        )  # fmt: skip
    )
    if invoices:
        st.markdown("**Invoices on this PO**")
        inv_rows = [
            [
                f"#{i['id']}",
                esc(i["invoice_number"] or ""),
                esc(i["invoice_date"] or ""),
                ui.pill("Approved", "ok", "check")
                if i["status"] == "approved"
                else ui.pill("Second approval", "violet")
                if i["status"] == "pending_approval"
                else ui.pill("In review", "warn"),
                money(i["coding"].get("subtotal")),
            ]
            for i in invoices
        ]
        st.html(ui.table(["", "Invoice #", "Date", "", "Subtotal"], inv_rows, right=[4]))
    else:
        st.caption("No invoices have quoted this PO yet.")

    c1, c2, _ = st.columns([1.3, 1.3, 3])
    closed = po["status"] == po_mod.CLOSED
    if c1.button("Reopen PO" if closed else "Close PO", icon=":material/lock_open:" if closed else ":material/lock:",
                 key=f"po_toggle_{po['po_key']}", width="stretch"):  # fmt: skip
        store.set_po_status(po["po_key"], po_mod.OPEN if closed else po_mod.CLOSED, actor=reviewer())
        st.session_state["po_status_filter"] = "All"  # keep this PO in view (not jump to another one)
        notify(
            f"{md(po_mod.po_label(po['po_number']))} {'reopened' if closed else 'closed'}.", ":material/shopping_cart:"
        )
        st.rerun()
    with c2.popover("Delete PO", icon=":material/delete:", width="stretch"):
        st.markdown(f"Delete **{md(po['po_number'])}** from the list? Invoices are not affected.")
        if st.button("Delete", type="primary", key=f"po_delete_{po['po_key']}"):
            store.delete_purchase_orders([po["po_key"]], actor=reviewer())
            st.session_state.pop("po_choice", None)
            notify(f"{md(po_mod.po_label(po['po_number']))} deleted.", ":material/delete:")
            st.rerun()


def page_purchase_orders() -> None:
    store = get_store()
    show_toast()
    page_head(
        "purchase_orders",
        "Purchase orders",
        "Invoices that quote a PO are checked against it: price, quantity ordered and received, and coding.",
    )
    pos = store.purchase_orders()
    _importer(store, bool(pos))
    if not pos:
        with card("po_empty"):
            st.html(
                ui.empty_state(
                    "No purchase orders yet",
                    "Import your open POs to check invoices against what was ordered and received.",
                )
            )
        return

    open_pos = [p for p in pos if p["status"] == po_mod.OPEN]
    committed = sum(max(p["remaining"], 0) for p in open_pos)
    over = [p for p in pos if p["total"] and p["billed"] > p["total"] * (1 + po_mod.TOTAL_TOLERANCE) + 0.01]
    st.html(
        ui.tiles(
            [
                ui.tile("Open POs", len(open_pos), "shopping_cart", "blue", f"{len(pos)} in total"),
                ui.tile(
                    "Still to be invoiced", f"${committed:,.0f}", "pending_actions", "violet", "on open POs"
                ),  # fmt: skip
                ui.tile(
                    "Invoiced against POs",
                    f"${sum(p['billed'] for p in pos):,.0f}",
                    "receipt_long",
                    "green",
                    f"{sum(p['invoices'] for p in pos)} invoice(s)",
                ),
                ui.tile(
                    "Over-billed",
                    len(over),
                    "error",
                    "amber" if over else "green",
                    "invoices exceed the PO total" if over else "none",
                ),  # fmt: skip
            ]
        )
    )

    with card("po_list"):
        c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
        query = c1.text_input("Search", placeholder="PO number or vendor", key="po_search",
                              label_visibility="collapsed")  # fmt: skip
        status = c2.segmented_control("Status", STATUS_FILTERS, default="Open", key="po_status_filter",
                                      label_visibility="collapsed")  # fmt: skip
        shown = [
            p
            for p in pos
            if (status in (None, "All") or p["status"] == (status or "").lower())
            and (not query or re.search(re.escape(query), f"{p['po_number']} {p['vendor_name']}", re.IGNORECASE))
        ]
        rows = [
            [
                f"<b>{esc(p['po_number'])}</b>",
                esc(p["vendor_name"]),
                str(p["lines"]),
                money(p["total"]),
                money(p["billed"]),
                _bar(p["billed"] / p["total"]) if p["total"] else "",
                money(p["remaining"]),
                _status_pill(p),
            ]
            for p in shown
        ]
        if rows:
            st.html(ui.table(["PO", "Vendor", "Lines", "Total", "Invoiced", "", "Remaining", ""], rows,
                             right=[2, 3, 4, 6], wrap=[1]))  # fmt: skip
        else:
            st.caption("No purchase orders match.")
        if any(p["in_review"] for p in shown):
            st.caption("Invoiced amounts include invoices still in the review queue.")

    if shown:
        with card("po_detail"):
            labels = {p["po_key"]: f"{p['po_number']} · {p['vendor_name']}" for p in shown}
            choice = st.selectbox("Purchase order", list(labels), format_func=labels.get, key="po_choice")
            chosen = next((p for p in shown if p["po_key"] == choice), shown[0])
            st.html(
                f"<div style='display:flex;gap:.6rem;align-items:center;margin:.2rem 0 .6rem'>"
                f"{ui.avatar(chosen['vendor_name'] or chosen['po_number'])}<div><b>{esc(chosen['po_number'])}</b> "
                f"{_status_pill(chosen)}<div class='apc-muted'>{esc(chosen['vendor_name'])} · updated "
                f"{esc(ui.time_ago(chosen['updated_at']))}</div></div></div>"
            )
            _detail(store, chosen)
