"""Settings → JD Edwards E1: company, batch, amount and account formats, the GL → BU.Object.Subsidiary
mapping, the province → tax area table, PO-matched vouchers and supplier address numbers."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from ap_coder import jde, ui
from ap_coder.memory import vendor_key
from ap_coder.store import Store
from ap_coder.tax import OUTSIDE_CANADA, PROVINCES, province_label
from ap_coder.webapp.accounts import _read_upload
from ap_coder.webapp.common import card, notify, reviewer

CHECKLIST = """\
Confirm with your JDE team before the first live load:

- **Company numbers** (VLCO / VNCO / VLPKCO) and the business units the vouchers and lines go to.
- **Document type** (PV, or a custom one for imported vouchers) and the **P0400047 version** R04110ZA uses
  (its processing options decide defaults, tax recalculation and edits).
- **Tax areas and explanation codes** per province (the names here are placeholders): V, C, B, E and how the
  tax AAIs (PT / GT) post GST, HST, QST and PST.
- **Account mapping**: AP Coder GL code (+ cost center) → BU.Object.Subsidiary, and subledgers if used.
- **Batch numbering** (VLEDBT): the prefix and the user ID R04110ZA is run for.
- **Amounts**: implied decimals or a decimal point, gross only or taxable / tax amounts.
- **Line numbering** (VNEDLN): sequential, or matching the pay item line (VLEDLN).
- **Duplicate-invoice edit** in the processing options (H = hard error, I = warning): AP Coder checks
  supplier + invoice number too, but E1 should be the last word.
- **The PO match path**: whether PO-matched vouchers come in through the Z-tables at all, and the columns of
  the match file (marked *unconfirmed*).
"""


def _cell(value: Any) -> str:
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value).strip()


def _general(store: Store, js: jde.JdeSettings) -> None:
    with card("jde_general"), st.form("jde_form", border=False):
        st.markdown("#### JD Edwards E1 vouchers")
        st.caption(
            "Exports → *JD Edwards E1* writes F0411Z1 (pay items) and F0911Z1 (G/L lines) files for the Voucher Batch "
            "Processor R04110ZA. See `docs/JDE_E1.md`."
        )
        st.html(ui.subhead("Voucher header", first=True))
        c1, c2, c3 = st.columns(3)
        company = c1.text_input("Company (VLCO)", js.company, max_chars=5, help="Up to 5 digits; zero-padded.")
        user = c2.text_input("User ID (VLEDUS)", js.user, max_chars=10, help="The user R04110ZA is run for.")
        prefix = c3.text_input(
            "Batch prefix (VLEDBT)", js.batch_prefix, max_chars=9, help="Batch number = prefix + AP Coder batch."
        )
        doc_type = c1.text_input("Document type (VLDCT)", js.document_type, max_chars=2, help="PV, or blank.")
        domestic = c2.text_input("Domestic currency", js.domestic_currency, max_chars=3)
        currencies = c3.text_input(
            "Currencies set up in E1", ", ".join(js.currencies), help="Invoices in other currencies are not exported."
        )
        st.html(ui.subhead("Formats"))
        r1, r2 = st.columns(2)
        amount_mode = r1.radio(
            "Amounts", list(jde.AMOUNT_MODES), format_func=jde.AMOUNT_MODES.get,
            index=list(jde.AMOUNT_MODES).index(js.amount_mode), help="Never both: E1 rejects a voucher with both.",
        )  # fmt: skip
        decimals = r2.radio(
            "Amount format", list(jde.DECIMAL_MODES), format_func=jde.DECIMAL_MODES.get,
            index=list(jde.DECIMAL_MODES).index(js.decimals),
        )  # fmt: skip
        account_mode = r1.radio(
            "Account", list(jde.ACCOUNT_MODES), format_func=jde.ACCOUNT_MODES.get,
            index=list(jde.ACCOUNT_MODES).index(js.account_mode),
        )  # fmt: skip
        numbering = r2.radio(
            "Line numbering (VNEDLN) · unconfirmed", list(jde.LINE_NUMBERING), format_func=jde.LINE_NUMBERING.get,
            index=list(jde.LINE_NUMBERING).index(js.line_numbering),
            help="Oracle's notes say VNEDLN must match VLEDLN; most loads number the lines 1, 2, 3 under pay item 1.",
        )  # fmt: skip
        po_matched = st.toggle(
            "PO-matched vouchers: invoices with a PO number get no G/L lines, to be matched to receipts in E1",
            value=js.po_matched,
        )
        st.html(ui.subhead("Default account rule", "when the GL code is not in the mapping below"))
        d1, d2, d3 = st.columns(3, vertical_alignment="bottom")
        use_default = d1.toggle("Use the default rule", value=js.use_default_rule,
                                help="Object = the GL code, Subsidiary blank.")  # fmt: skip
        bu_from_cc = d2.toggle("BU = the line's cost center", value=js.bu_from_cost_center)
        default_bu = d3.text_input("Default BU", js.default_bu, max_chars=12, help="When a line has no cost center.")
        with st.expander("More fields"):
            m1, m2, m3 = st.columns(3)
            pay_status = m1.text_input("Pay status (VLPST)", js.pay_status, max_chars=1)
            terms = m2.text_input("Payment terms code (VLPTC)", js.payment_terms_code, max_chars=3,
                                  help="Blank: E1 takes the supplier's terms.")  # fmt: skip
            header_bu = m3.text_input("Header business unit (VLMCU)", js.header_bu, max_chars=12)
            credit_type = m1.text_input("Credit note document type", js.credit_document_type, max_chars=2,
                                        help="Blank: the document type above, with negative amounts.")  # fmt: skip
            po_type = m2.text_input("PO document type (VLPDCT)", js.po_document_type, max_chars=2)
            po_company = m3.text_input("PO company (VLPKCO)", js.po_company, max_chars=5, help="Blank: the company.")
            self_code = m1.text_input("Code for self-assessed PST", js.self_assessed_code, max_chars=2)
            exempt_code = m2.text_input("Code when no tax is charged", js.exempt_code, max_chars=2)
            gl_date = m3.selectbox("G/L date (VLDGJ)", list(jde.GL_DATES), format_func=jde.GL_DATES.get,
                                   index=list(jde.GL_DATES).index(js.gl_date))  # fmt: skip
            due = st.toggle("Send the due date (VLDDJ)", value=js.send_due_date)
            po_ref = st.toggle("Send the PO number as a reference on other vouchers (VLPO)", value=js.send_po_reference)
            dist_tax = st.toggle("Repeat the tax area and code on each G/L line (VNTXA1, VNEXR1)",
                                 value=js.dist_tax_columns)  # fmt: skip
            extras = st.text_input(
                "Extra columns on PO-matched vouchers · unconfirmed",
                ", ".join(f"{k}={v}" for k, v in js.po_extra_columns.items()),
                help="COLUMN=value pairs added to F0411Z1 in PO-matched mode (e.g. the automation flags). Ask your "
                "JDE team which columns their match process needs.",
            )  # fmt: skip
        if st.form_submit_button("Save JD Edwards settings", type="primary", icon=":material/save:"):
            pairs = {}
            for part in extras.split(","):
                name, _, value = part.partition("=")
                if name.strip():
                    pairs[name.strip().upper()] = value.strip()
            new = jde.JdeSettings.from_json(js.to_json())
            new.company, new.user, new.batch_prefix = company.strip(), user.strip(), prefix.strip()
            new.document_type, new.domestic_currency = doc_type.strip().upper(), domestic.strip().upper()
            new.currencies = [c.strip().upper() for c in currencies.replace(";", ",").split(",") if c.strip()]
            new.amount_mode, new.decimals, new.account_mode = amount_mode, decimals, account_mode
            new.line_numbering, new.po_matched = numbering, po_matched
            new.use_default_rule, new.bu_from_cost_center, new.default_bu = use_default, bu_from_cc, default_bu.strip()
            new.pay_status, new.payment_terms_code, new.header_bu = pay_status.strip(), terms.strip(), header_bu.strip()
            new.credit_document_type, new.po_document_type = credit_type.strip().upper(), po_type.strip().upper()
            new.po_company, new.gl_date = po_company.strip(), gl_date
            new.self_assessed_code, new.exempt_code = self_code.strip().upper(), exempt_code.strip().upper()
            new.send_due_date, new.send_po_reference, new.dist_tax_columns = due, po_ref, dist_tax
            new.po_extra_columns = pairs
            problems = jde.settings_problems(new)
            if problems:
                for p in problems:
                    st.error(p)
            else:
                changed = jde.save(store, new, actor=reviewer())
                notify("JD Edwards settings saved." if changed else "Nothing changed.", ":material/save:")
                st.rerun()


def _gl_map(store: Store, js: jde.JdeSettings) -> None:
    with card("jde_gl_map"):
        st.markdown("#### GL accounts → BU.Object.Subsidiary")
        st.caption(
            "One row per AP Coder GL code (and cost center, blank = any). A blank BU or object is filled by the "
            "default rule above. Lines without a mapping (and no default rule) are left out of the export."
        )
        columns = ["gl_code", "cost_center", "bu", "obj", "sub", "sbl", "sblt"]
        df = pd.DataFrame([vars(m) for m in js.gl_map], columns=columns)
        edited = st.data_editor(
            df,
            column_config={
                "gl_code": st.column_config.TextColumn("GL code", required=True),
                "cost_center": st.column_config.TextColumn("Cost center"),
                "bu": st.column_config.TextColumn("BU", max_chars=12),
                "obj": st.column_config.TextColumn("Object", max_chars=6),
                "sub": st.column_config.TextColumn("Subsidiary", max_chars=8),
                "sbl": st.column_config.TextColumn("Subledger", max_chars=8),
                "sblt": st.column_config.TextColumn("Subledger type", max_chars=1),
            },
            num_rows="dynamic",
            hide_index=True,
            key="jde_gl_editor",
        )
        if st.button("Save the mapping", type="primary", icon=":material/save:", key="jde_gl_save"):
            rows = [jde.GlMap(**{c: _cell(r.get(c)) for c in columns}) for r in edited.to_dict("records")]
            new = jde.JdeSettings.from_json(js.to_json())
            new.gl_map = [r for r in rows if r.gl_code]
            changed = jde.save(store, new, actor=reviewer())
            notify(
                f"{ui.plural(len(new.gl_map), 'mapping row')} saved." if changed else "Nothing changed.",
                ":material/save:",
            )
            st.rerun()
        upload = st.file_uploader(
            "Import a mapping (CSV or Excel: GL code, Cost center, BU, Object, Subsidiary)", type=["csv", "xlsx"],
            key="jde_gl_upload",
        )  # fmt: skip
        df_up = _read_upload(upload, "jde_gl") if upload is not None else None
        if df_up is not None and not df_up.empty:
            rows = jde.gl_map_from_records(df_up.to_dict("records"))
            st.caption(f"{ui.plural(len(rows), 'row')} with a GL code found.")
            replace = st.checkbox("Replace the current mapping", key="jde_gl_replace")
            if rows and st.button("Import", icon=":material/upload:", key="jde_gl_import"):
                new = jde.JdeSettings.from_json(js.to_json())
                kept = [] if replace else new.gl_map
                keys = {(r.gl_code.upper(), r.cost_center.upper()) for r in rows}
                new.gl_map = [m for m in kept if (m.gl_code.upper(), m.cost_center.upper()) not in keys] + rows
                jde.save(store, new, actor=reviewer())
                st.session_state.pop("jde_gl_editor", None)
                notify(f"{ui.plural(len(rows), 'mapping row')} imported.", ":material/upload:")
                st.rerun()


def _tax_map(store: Store, js: jde.JdeSettings) -> None:
    with card("jde_tax_map"):
        st.markdown("#### Province → tax area and explanation code")
        st.caption(
            "The tax area names are placeholders: use your E1 tax areas (F4008). Codes: V = GST/HST/QST recoverable, "
            "C = GST + seller-charged PST (PST in the distribution), B = self-assessed PST, E = exempt. Invoices with "
            "no tax charged use the exempt code; PST provinces without PST charged use the self-assessed code."
        )
        order = [*PROVINCES, OUTSIDE_CANADA]
        df = pd.DataFrame(
            [{"province": p, "name": province_label(p), "tax_area": js.tax_map[p].tax_area, "code": js.tax_map[p].code}
             for p in order if p in js.tax_map]
        )  # fmt: skip
        edited = st.data_editor(
            df,
            column_config={
                "province": st.column_config.TextColumn("Province", width="small"),
                "name": st.column_config.TextColumn("Name"),
                "tax_area": st.column_config.TextColumn("Tax area (VLTXA1)", max_chars=10),
                "code": st.column_config.SelectboxColumn("Code (VLEXR1)", options=["V", "C", "B", "E", ""]),
            },
            disabled=["province", "name"],
            hide_index=True,
            key="jde_tax_editor",
        )
        if st.button("Save the tax areas", type="primary", icon=":material/save:", key="jde_tax_save"):
            new = jde.JdeSettings.from_json(js.to_json())
            for r in edited.to_dict("records"):
                new.tax_map[r["province"]] = jde.TaxArea(_cell(r.get("tax_area")).upper(), _cell(r.get("code")).upper())
            changed = jde.save(store, new, actor=reviewer())
            notify("Tax areas saved." if changed else "Nothing changed.", ":material/save:")
            st.rerun()


def _an8(store: Store, js: jde.JdeSettings) -> None:
    with card("jde_an8"):
        st.markdown("#### Supplier address numbers (AN8)")
        st.caption(
            "Taken from the vendor master's ERP ID (Vendors page). Add a vendor here only when its ERP ID is not its "
            "JDE address number."
        )
        names = {v["vendor_key"]: v["display_name"] for v in store.master_vendors()}
        df = pd.DataFrame(
            [{"vendor": names.get(k, k), "an8": a} for k, a in js.an8_overrides.items()], columns=["vendor", "an8"]
        )
        edited = st.data_editor(
            df,
            column_config={
                "vendor": st.column_config.TextColumn("Vendor name", required=True),
                "an8": st.column_config.TextColumn("Address number", max_chars=8),
            },
            num_rows="dynamic",
            hide_index=True,
            key="jde_an8_editor",
        )
        if st.button("Save the address numbers", type="primary", icon=":material/save:", key="jde_an8_save"):
            new = jde.JdeSettings.from_json(js.to_json())
            new.an8_overrides = {
                vendor_key(_cell(r.get("vendor"))): _cell(r.get("an8"))
                for r in edited.to_dict("records")
                if vendor_key(_cell(r.get("vendor"))) and _cell(r.get("an8"))
            }
            changed = jde.save(store, new, actor=reviewer())
            notify("Address numbers saved." if changed else "Nothing changed.", ":material/save:")
            st.rerun()


def jde_tab(store: Store) -> None:
    js = jde.load(store)
    _general(store, js)
    _gl_map(store, js)
    _tax_map(store, js)
    _an8(store, js)
    with card("jde_checklist"):
        st.markdown("#### Before the first live load")
        st.markdown(CHECKLIST)
