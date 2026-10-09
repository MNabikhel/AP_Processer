"""Exports: send approved invoices to the ERP in batches; each invoice is exported once."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from ap_coder import export_layout, exports, registers, stamp, ui
from ap_coder.reference_data import short_name
from ap_coder.store import Store
from ap_coder.webapp.accounts import _read_upload
from ap_coder.webapp.common import (
    by_currency,
    card,
    esc,
    get_store,
    money,
    notify,
    page_head,
    reference_or_none,
    reviewer,
    show_toast,
)


def _gl_names(store: Store) -> dict[str, str]:
    reference = reference_or_none(store)
    if reference is None:
        return {}
    return {row["gl_code"]: short_name(row.get("description") or "") for row in reference.chart_of_accounts.rows}


LAYOUT_SETTING = "export_layout"


def _layout(store: Store) -> export_layout.Layout:
    return export_layout.Layout.from_json(store.get_setting(LAYOUT_SETTING))


def _batch_file(store: Store, batch: int, fmt: str) -> tuple[bytes, str, str]:
    invoices = [store.get_invoice(i) for i in store.batch_invoice_ids(batch)]
    return exports.build(
        fmt, [i for i in invoices if i], _gl_names(store), batch, store.vendor_ids(), layout=_layout(store)
    )


REGISTER_LABELS = {"vendor_name": "Vendor *", "invoice_number": "Invoice number *", "invoice_date": "Date",
                   "total": "Total *"}  # fmt: skip


def _register_importer(store: Store) -> None:
    count = store.erp_register_count()
    title = "Invoices already in the ERP" + (f" ({count:,} known)" if count else "")
    with st.expander(title, icon=":material/receipt_long:"):
        st.caption(
            "Import the ERP's AP invoice list (vendor, invoice number, date, total), e.g. the last 18 months. "
            "Invoices processed in AP Coder are then also checked against bills already entered or paid in the ERP."
        )
        upload = st.file_uploader("ERP invoice list", type=["csv", "xlsx"], key="reg_upload")
        df = _read_upload(upload, "reg") if upload is not None else None
        if df is not None and not df.empty:
            columns = list(df.columns)
            guessed = registers.map_columns(columns)
            options = ["(none)", *columns]
            cols = st.columns(4)
            chosen = {}
            for col, (field, label) in zip(cols, REGISTER_LABELS.items(), strict=True):
                pick = col.selectbox(label, options, index=options.index(guessed[field]) if field in guessed else 0,
                                     key=f"reg_col_{field}")  # fmt: skip
                if pick != "(none)":
                    chosen[field] = pick
            replace = st.checkbox("Replace the invoices already imported", key="reg_replace")
            missing = [REGISTER_LABELS[f].rstrip(" *") for f in registers.REQUIRED if f not in chosen]
            if missing:
                st.warning("Pick the column for: " + ", ".join(missing))
            elif st.button("Import", type="primary", icon=":material/upload:", key="reg_import"):
                rows, skipped = registers.rows_from_records(df.to_dict("records"), chosen)
                total = store.import_erp_register(rows, replace_all=replace, actor=reviewer())
                notify(f"{total:,} ERP invoice(s) known" + (f"; {skipped} row(s) skipped." if skipped else "."),
                       ":material/receipt_long:")  # fmt: skip
                st.rerun()
        if count and st.button("Clear the list", icon=":material/delete_sweep:", key="reg_clear"):
            store.clear_erp_register(actor=reviewer())
            st.rerun()


def _layout_editor(store: Store) -> None:
    layout = _layout(store)
    with st.expander("Custom layout for your ERP", icon=":material/view_column:"):
        st.caption(
            "One row per GL posting line. Pick the columns your ERP's import expects, in order, with its headers; "
            "a column can also be fixed text (a company code, a journal name). Then export with *Custom CSV*."
        )
        labels = {**{k: v for k, v in export_layout.FIELDS.items()}, export_layout.FIXED: "Fixed text…"}
        df = pd.DataFrame([{"header": c.header, "field": c.field, "text": c.text} for c in layout.columns])
        edited = st.data_editor(
            df,
            column_config={
                "header": st.column_config.TextColumn("Header", required=True),
                "field": st.column_config.SelectboxColumn(
                    "Value", options=list(labels), format_func=labels.get, required=True, width="medium"
                ),  # fmt: skip
                "text": st.column_config.TextColumn("Fixed text", help="Used when the value is 'Fixed text…'"),
            },
            num_rows="dynamic",
            hide_index=True,
            key="layout_columns",
        )
        c1, c2, c3, c4 = st.columns(4)
        dates, separators = list(export_layout.DATE_FORMATS), list(export_layout.DELIMITERS)
        date_format = c1.selectbox("Dates", dates, index=dates.index(layout.date_format), key="layout_date")
        delimiter = c2.selectbox(
            "Separator", separators, format_func=export_layout.DELIMITERS.get,
            index=separators.index(layout.delimiter), key="layout_sep",
        )  # fmt: skip
        decimal_comma = c3.toggle("Decimal comma (12,50)", value=layout.decimal_comma, key="layout_comma")
        header_row = c4.toggle("Header row", value=layout.header_row, key="layout_header")

        def cell(value: object) -> str:
            return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value).strip()

        columns, incomplete = [], []
        for n, r in enumerate(edited.to_dict("records"), 1):
            header, field = cell(r.get("header")), cell(r.get("field"))
            if header and field:
                columns.append(export_layout.Column(header, field, cell(r.get("text"))))
            elif header or field:
                incomplete.append(f"row {n} ({header or labels.get(field, field)})")
        if incomplete:
            st.warning(
                f"Not in the layout until it has both a Header and a Value: {', '.join(incomplete)}.",
                icon=":material/warning:",
            )
        new = export_layout.Layout(columns, date_format, delimiter, decimal_comma, header_row)
        if decimal_comma and delimiter == ",":
            st.warning("With a decimal comma, use a semicolon or tab as the separator.")
        sample = store.unexported_approved()[:3] or [{"id": i} for i in store.batch_invoice_ids(1)[:3]]
        invoices = [store.get_invoice(i["id"]) for i in sample]
        rows = exports.custom_rows([i for i in invoices if i], _gl_names(store), "", store.vendor_ids())
        if rows and columns:
            preview = export_layout.build(rows[:6], new).decode("utf-8-sig")
            st.caption("Preview")
            st.code(preview, language=None)
        if st.button("Save layout", type="primary", icon=":material/save:", key="layout_save", disabled=not columns):
            store.set_setting(LAYOUT_SETTING, new.to_json(), actor=reviewer())
            store.log_event("settings_changed", actor=reviewer(), detail={"keys": ["export_layout"]})
            notify("Export layout saved. Choose Custom CSV when exporting.", ":material/view_column:")
            st.rerun()


def page_exports() -> None:
    store = get_store()
    show_toast()
    page_head(
        "exports",
        "Exports",
        "Hand approved invoices to the ERP in batches. Each invoice goes out once; any batch can be downloaded again.",
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
                    by_currency(ready) + " total" if ready else "nothing waiting",
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

    _layout_editor(store)
    _register_importer(store)

    with card("export_batches"):
        st.markdown("#### :material/inventory_2: Past batches")
        if not batches:
            st.caption("No exports yet.")
            return
        totals = store.batch_totals()

        def batch_total(b: dict[str, Any]) -> str:
            amounts = totals.get(b["id"])
            if not amounts:  # undone: its invoices are back in the ready list
                return money(b["total"])
            return " · ".join(f"{money(t)} <span class='apc-muted'>{esc(c)}</span>" for c, t in sorted(amounts.items()))

        rows = [
            [
                f"<b>{b['id']}</b>",
                esc(b["created_at"].replace("T", " ")[:16]),
                esc(b["actor"] or ""),
                str(b["invoices"]),
                batch_total(b),
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
        fmt = c2.selectbox(
            "Format",
            list(exports.FORMATS),
            format_func=lambda f: "Custom CSV" if f == "custom" else f.upper(),
            key="export_again_fmt",
        )
        data, name, mime = _batch_file(store, chosen_batch, fmt)
        c3.download_button("Download again", data, file_name=name, mime=mime, icon=":material/download:",
                           width="stretch", key="export_download_again")  # fmt: skip
        pdfs, undo = st.columns([1, 1], vertical_alignment="center")
        ready_zip = st.session_state.get("export_pdfs")
        if ready_zip and ready_zip[0] == chosen_batch:
            pdfs.download_button(
                f"Download approved PDFs (batch {chosen_batch})", ready_zip[1],
                file_name=f"ap_coder_batch_{chosen_batch}_approved_pdfs.zip", mime="application/zip",
                icon=":material/download:", key="export_pdfs_download", type="primary",
                on_click=lambda: st.session_state.pop("export_pdfs", None),
            )  # fmt: skip
        elif pdfs.button(
            "Approved PDFs (ZIP)", icon=":material/approval:", key="export_pdfs_make",
            help="Each invoice of the batch with an APPROVED stamp and its coding page: to attach in the ERP.",
        ):  # fmt: skip
            invoices = [store.get_invoice(i) for i in store.batch_invoice_ids(chosen_batch)]
            with st.spinner("Stamping the invoices…"):
                data = stamp.batch_zip([i for i in invoices if i], _gl_names(store))
            st.session_state["export_pdfs"] = (chosen_batch, data)
            st.rerun()
        with undo.popover("Undo this batch…", icon=":material/undo:"):
            st.markdown(
                f"Put the invoices of batch {chosen_batch} back in *Ready to export* (e.g. the ERP import failed). "
                "The batch stays in this list, marked as undone."
            )
            if st.button("Undo batch", key="export_undo", type="primary"):
                count = store.undo_export_batch(chosen_batch, actor=reviewer())
                st.session_state.pop("just_exported", None)
                notify(f"Batch {chosen_batch} undone: {count} invoice(s) are ready to export again.", ":material/undo:")
                st.rerun()
