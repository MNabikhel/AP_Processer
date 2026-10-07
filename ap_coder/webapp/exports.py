"""Exports: send approved invoices to the ERP in batches; each invoice is exported once."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ap_coder import exports, ui
from ap_coder.store import Store
from ap_coder.webapp.common import (
    card,
    esc,
    get_store,
    money,
    notify,
    reference_or_none,
    reviewer,
    show_toast,
)


def _gl_names(store: Store) -> dict[str, str]:
    reference = reference_or_none(store)
    if reference is None:
        return {}
    return {row["gl_code"]: (row.get("description") or "").split(" - ")[0] for row in reference.chart_of_accounts.rows}


def _batch_file(store: Store, batch: int, fmt: str) -> tuple[bytes, str, str]:
    invoices = [store.get_invoice(i) for i in store.batch_invoice_ids(batch)]
    return exports.build(fmt, [i for i in invoices if i], _gl_names(store), batch)


def page_exports() -> None:
    store = get_store()
    show_toast()
    st.html(
        ui.page_header(
            "ERP",
            "Exports",
            "Hand approved invoices to the ERP in batches. Each invoice goes out once; any batch can be "
            "downloaded again.",
        )
    )
    ready = store.unexported_approved()
    batches = store.export_batches()
    live = [b for b in batches if not b["undone_at"]]
    st.html(
        ui.tiles(
            [
                ui.tile(
                    "Ready to export",
                    len(ready),
                    "outbox",
                    "blue",
                    f"{money(sum(i['grand_total'] or 0 for i in ready))} total",
                ),  # fmt: skip
                ui.tile(
                    "Batches",
                    len(live),
                    "inventory_2",
                    "violet",
                    f"{sum(b['invoices'] for b in live)} invoices exported",
                ),  # fmt: skip
                ui.tile(
                    "Last export",
                    (live[0]["created_at"][:10] if live else "—"),
                    "event",
                    "green",
                    (f"batch {live[0]['id']} by {live[0]['actor'] or '?'}" if live else "nothing yet"),
                ),  # fmt: skip
            ]
        )
    )

    with card("export_ready"):
        st.markdown("#### :material/outbox: Ready to export")
        if not ready:
            st.html(ui.empty_state("Nothing waiting", "Approved invoices appear here until they are exported."))
        else:
            df = pd.DataFrame(ready)
            df.insert(0, "include", True)
            edited = st.data_editor(
                df[
                    [
                        "include",
                        "id",
                        "vendor_name",
                        "invoice_number",
                        "invoice_date",
                        "currency",
                        "grand_total",
                        "reviewer",
                    ]
                ],  # fmt: skip
                column_config={
                    "include": st.column_config.CheckboxColumn("Export", width="small"),
                    "id": st.column_config.NumberColumn("#", width="small"),
                    "vendor_name": st.column_config.TextColumn("Vendor", width="medium"),
                    "invoice_number": "Invoice #",
                    "invoice_date": "Date",
                    "currency": "Cur.",
                    "grand_total": st.column_config.NumberColumn("Total", format="%.2f"),
                    "reviewer": "Approved by",
                },
                disabled=["id", "vendor_name", "invoice_number", "invoice_date", "currency", "grand_total", "reviewer"],
                hide_index=True,
                key="export_selection",
            )
            chosen = [int(i) for i in edited.loc[edited["include"], "id"].tolist()]
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            fmt = c1.radio("Format", list(exports.FORMATS), format_func=exports.FORMATS.get, horizontal=True,
                           key="export_format")  # fmt: skip
            if c2.button(
                f"Export {len(chosen)} invoice(s)", type="primary", icon=":material/ios_share:", disabled=not chosen,
                width="stretch", key="export_create",
            ):  # fmt: skip
                batch = store.create_export_batch(chosen, fmt, actor=reviewer())
                st.session_state["just_exported"] = (batch, fmt)
                notify(
                    f"Batch {batch} created with {len(chosen)} invoice(s). Download it below.", ":material/ios_share:"
                )
                st.rerun()

    just = st.session_state.get("just_exported")
    if just and any(b["id"] == just[0] for b in live):
        batch, fmt = just
        data, name, mime = _batch_file(store, batch, fmt)
        with card("export_download"):
            st.markdown(f"#### :material/download: Batch {batch} is ready")
            st.caption("Import this file into your ERP. If the import fails, undo the batch below and export again.")
            st.download_button(f"Download {name}", data, file_name=name, mime=mime, type="primary",
                               icon=":material/download:", key="export_download_now")  # fmt: skip

    with card("export_batches"):
        st.markdown("#### :material/inventory_2: Past batches")
        if not batches:
            st.caption("No exports yet.")
            return
        rows = [
            [
                f"<b>{b['id']}</b>",
                esc(b["created_at"].replace("T", " ")[:16]),
                esc(b["actor"] or ""),
                str(b["invoices"]),
                money(b["total"]),
                esc(exports.FORMATS.get(b["format"], b["format"]).split(":")[0]),
                ui.pill("Undone", "gray", "undo") if b["undone_at"] else ui.pill("Exported", "ok", "check"),
            ]  # fmt: skip
            for b in batches
        ]
        st.html(ui.table(["Batch", "When", "By", "Invoices", "Total", "Format", ""], rows, right=[3, 4]))
        if not live:
            return
        labels = {b["id"]: f"Batch {b['id']} · {b['created_at'][:10]} · {b['invoices']} invoice(s)" for b in live}
        c1, c2, c3 = st.columns([2, 1.2, 1.2], vertical_alignment="bottom")
        chosen_batch = c1.selectbox("Batch", list(labels), format_func=labels.get, key="export_batch_choice")
        fmt = c2.selectbox("Format", list(exports.FORMATS), format_func=lambda f: f.upper(), key="export_again_fmt")
        data, name, mime = _batch_file(store, chosen_batch, fmt)
        c3.download_button("Download again", data, file_name=name, mime=mime, icon=":material/download:",
                           width="stretch", key="export_download_again")  # fmt: skip
        with st.popover("Undo this batch…", icon=":material/undo:"):
            st.markdown(
                f"Put the invoices of batch {chosen_batch} back in *Ready to export* (e.g. the ERP import failed). "
                "The batch stays in this list, marked as undone."
            )
            if st.button("Undo batch", key="export_undo", type="primary"):
                count = store.undo_export_batch(chosen_batch, actor=reviewer())
                st.session_state.pop("just_exported", None)
                notify(f"Batch {chosen_batch} undone: {count} invoice(s) are ready to export again.", ":material/undo:")
                st.rerun()
