"""GL accounts & tax: import / edit GL accounts and cost centers, tax treatments, coding policy."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import streamlit as st

from ap_coder import ui
from ap_coder.rules import Rule
from ap_coder.rules import suggest as suggest_rules
from ap_coder.safe import csv_cell, md
from ap_coder.store import Store, load_sample_setup
from ap_coder.tax import DEFAULT_RATES_PATH, RATES_FILE, TAX_TYPES, TREATMENTS, TaxRateTable, rates_path
from ap_coder.webapp.common import (
    card,
    esc,
    get_store,
    notify,
    page_head,
    persistent_editor,
    replace_editor,
    reviewer,
    short_path,
    show_toast,
)
from ap_coder.webapp.process import tax_types_mapped

# --- GL accounts ------------------------------------------------------------------------------------------------

_CODE_HINTS = ("gl", "account", "code", "cost", "centre", "center", "saknr", "kostl")
_DESC_HINTS = ("desc", "name", "title", "text")
_CAT_HINTS = ("categ", "group", "type", "class")


def _guess(columns: list[str], hints: tuple[str, ...], default: int = 0) -> int:
    for i, col in enumerate(columns):
        if any(h in col.lower() for h in hints):
            return i
    return default


def _read_upload(upload: Any, table: str) -> pd.DataFrame | None:
    """The uploaded sheet as text cells, or None (with a message) if it can't be read."""
    try:
        if upload.name.lower().endswith((".xlsx", ".xls")):
            sheets = pd.read_excel(upload, sheet_name=None, dtype=str)
            names = list(sheets)
            name = st.selectbox("Sheet", names, key=f"{table}_sheet_{upload.name}") if len(names) > 1 else names[0]
            df = sheets[name]
        else:
            df = None
            for encoding in ("utf-8-sig", "cp1252"):  # Excel "CSV" exports are often Windows-1252
                try:
                    upload.seek(0)
                    df = pd.read_csv(  # sep=None: also semicolon CSVs from French-Canadian Excel
                        upload, dtype=str, keep_default_na=False, encoding=encoding, sep=None, engine="python"
                    )
                    break
                except UnicodeDecodeError:
                    continue
            if df is None:
                st.error("Could not read this file. In Excel, use *Save As → CSV UTF-8* and upload it again.")
                return None
    except Exception as exc:  # empty file, not really a spreadsheet, damaged workbook, ...
        st.error(
            "This file could not be read as a spreadsheet: it may be empty, damaged or another format. In Excel, "
            "save it as *CSV UTF-8* or *Excel Workbook (.xlsx)* and upload it again."
        )
        with st.expander("Technical details"):
            st.code(str(exc), language=None, wrap_lines=True)
        return None
    df.columns = [str(c) for c in df.columns]  # a header like 2024 arrives as a number
    return df.fillna("")


def account_manager(store: Store, table: str, noun: str) -> None:
    rows = store.list_accounts(table)
    with st.expander(f"Import {noun}s from CSV or Excel", expanded=not rows, icon=":material/upload:"):
        upload = st.file_uploader("Choose a file exported from your ERP", type=["csv", "xlsx"], key=f"{table}_upload")
        df = _read_upload(upload, table) if upload is not None else None
        if df is not None and df.empty:
            st.warning("This file has no rows under its header line.")
        elif df is not None:
            st.caption(f"{len(df)} rows found. First rows:")
            st.dataframe(df.head(6), hide_index=True)
            columns = list(df.columns)
            optional = ["(none)", *columns]
            c1, c2, c3 = st.columns(3)
            code_col = c1.selectbox(f"Column with the {noun} code", columns, index=_guess(columns, _CODE_HINTS),
                                    key=f"{table}_codecol")  # fmt: skip
            desc_col = c2.selectbox("Description column", optional, index=_guess(optional, _DESC_HINTS, 0),
                                    key=f"{table}_desccol")  # fmt: skip
            cat_col = c3.selectbox("Category column (optional)", optional, index=_guess(optional, _CAT_HINTS, 0),
                                   key=f"{table}_catcol")  # fmt: skip
            replace = st.checkbox("Replace my current list (otherwise new codes are added and existing ones updated)",
                                  key=f"{table}_replace")  # fmt: skip
            if st.button(f"Import {noun}s", type="primary", key=f"{table}_import", icon=":material/upload:"):
                result = store.import_accounts(
                    table,
                    df.to_dict("records"),
                    code_col,
                    None if desc_col == "(none)" else desc_col,
                    None if cat_col == "(none)" else cat_col,
                    replace_all=replace,
                    actor=reviewer(),
                )
                notify(
                    f"Imported: {result['added']} added, {result['updated']} updated, "
                    f"{result['skipped']} skipped (blank or repeated codes)."
                )
                st.rerun()

    if not rows:
        st.caption(f"No {noun}s yet.")
        return

    counts = pd.Series([r["category"] or "Uncategorised" for r in rows]).value_counts()
    st.html(
        " ".join(ui.pill(f"{cat} · {n}", "info" if i == 0 else "gray") for i, (cat, n) in enumerate(counts.items()))
    )
    df = pd.DataFrame(rows)
    df.insert(0, "delete", False)
    edited = st.data_editor(
        df,
        column_config={
            "delete": st.column_config.CheckboxColumn("Delete?", width="small"),
            "code": st.column_config.TextColumn("Code", required=True),
            "description": st.column_config.TextColumn("Description", width="large"),
            "category": st.column_config.TextColumn(
                "Category", help="Group codes however you like, e.g. Opex, Capex, Sales Tax"
            ),
        },
        num_rows="dynamic",
        hide_index=True,
        key=f"{table}_editor_{abs(hash(tuple((r['code'], r['description'], r['category']) for r in rows)))}",
    )
    actions = st.container(horizontal=True)
    if actions.button("Save changes", type="primary", key=f"{table}_save", icon=":material/save:"):
        keep = edited[~edited["delete"].fillna(False).astype(bool)].copy()
        keep["code"] = keep["code"].fillna("").astype(str).str.strip()
        keep = keep[keep["code"] != ""]
        dupes = sorted(keep["code"][keep["code"].duplicated()].unique())
        if dupes:
            st.error(f"These codes appear more than once: {', '.join(dupes)}")
        else:
            removed = len(edited) - len(keep)
            store.save_accounts(table, keep.fillna("").to_dict("records"), actor=reviewer())
            notify(f"Saved {len(keep)} {noun}s" + (f", removed {removed}." if removed else "."))
            st.rerun()
    actions.download_button(
        f"Download {noun}s",
        df.drop(columns=["delete"]).map(csv_cell).to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{table}.csv",
        mime="text/csv",
        key=f"{table}_download",
        icon=":material/download:",
    )


def page_accounts() -> None:
    store = get_store()
    show_toast()
    page_head("accounts", "GL accounts & tax", "The codes the AI may use, how each sales tax posts, and your rules.")
    gl = store.list_accounts("gl_accounts")
    cc = store.list_accounts("cost_centers")
    mapped = tax_types_mapped(store)
    policy = [n for n in store.get_setting("policy_notes").splitlines() if n.strip() and not n.lstrip().startswith("#")]
    st.html(
        ui.tiles(
            [
                ui.tile(
                    "GL accounts",
                    len(gl),
                    "account_tree",
                    "blue",
                    f"{len({a['category'] for a in gl if a['category']})} categories",
                ),  # fmt: skip
                ui.tile("Cost centers", len(cc) or "—", "apartment", "violet", "in use" if cc else "optional"),
                ui.tile(
                    "Sales taxes mapped",
                    f"{mapped}/{len(TAX_TYPES)}",
                    "percent",
                    "green" if mapped == len(TAX_TYPES) else "amber",
                    "GST · HST · PST · QST · other",
                ),  # fmt: skip
                ui.tile(
                    "Coding policy",
                    len(policy),
                    "rule",
                    "amber",
                    f"plain-English lines · {ui.plural(len(store.coding_rules()), 'fixed rule')}",
                ),  # fmt: skip
            ]
        )
    )

    if not gl:
        with card("sample"):
            st.html(
                ui.empty_state("Just exploring?", "Load sample GL accounts, cost centers and tax setup to try the app.")
            )
            if st.button("Load sample setup", icon=":material/download:", type="primary"):
                load_sample_setup(store)
                notify("Sample setup loaded.")
                st.rerun()

    tab_gl, tab_cc, tab_tax, tab_policy, tab_rules = st.tabs(
        [
            ":material/account_tree: GL accounts",
            ":material/apartment: Cost centers",
            ":material/percent: Sales tax",
            ":material/rule: Coding policy",
            ":material/rule_settings: Fixed rules",
        ]
    )
    with tab_gl, card("gl"):
        st.caption(
            "The AI may only use the codes listed here. Descriptions matter: the AI matches invoice lines "
            "against them. Use the category column to group codes your own way."
        )
        account_manager(store, "gl_accounts", "GL account")
    with tab_cc, card("cc"):
        st.caption("Optional. Leave empty if you do not code invoices to cost centers.")
        account_manager(store, "cost_centers", "cost center")
    with tab_tax:
        tax_setup(store)
    with tab_policy, card("policy"):
        st.caption(
            "Plain-English rules the AI follows, one per line (e.g. 'Laptops under $2,500 go to 6010'). "
            "Lines starting with # are ignored."
        )
        notes = st.text_area("Coding policy", store.get_setting("policy_notes"), height=260)
        if st.button("Save policy", type="primary", icon=":material/save:"):
            store.set_setting("policy_notes", notes, actor=reviewer())
            notify("Coding policy saved.")
            st.rerun()
    with tab_rules:
        rules_editor(store, gl, cc)


def _rules_frame(rules: list[Rule]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"vendor": r.vendor, "contains": r.contains, "gl_code": r.gl_code, "cost_center": r.cost_center}
            for r in rules
        ],
        columns=["vendor", "contains", "gl_code", "cost_center"],
    )


def rules_editor(store: Store, gl: list[dict[str, Any]], cc: list[dict[str, Any]]) -> None:
    """Fixed coding rules: always this GL account (and cost center) for a vendor and/or words in a line."""
    current = store.coding_rules()
    with card("rules"):
        st.caption(
            "Lines that always go to the same account, whatever the AI thinks: a vendor (e.g. *Purolator*), words "
            "a line contains (e.g. *freight*), or both. Applied to invoices processed from now on (for one already "
            "in the queue, open it and use *Apply the coding rules*); the review screen says which lines a rule "
            "changed. The most specific rule wins."
        )
        if not gl:
            st.info("Import your GL accounts first.", icon=":material/account_tree:")
            return
        gl_codes = [a["code"] for a in gl]
        names = {a["code"]: f"{a['code']} · {a['description']}" for a in gl}
        cc_codes = ["", *(a["code"] for a in cc)]
        df = _rules_frame(current)
        edited = persistent_editor(
            df, "rules_grid",
            column_config={
                "vendor": st.column_config.TextColumn("Vendor (optional)", help="Part of the name is enough"),
                "contains": st.column_config.TextColumn("Line contains (optional)", help="Not case-sensitive"),
                "gl_code": st.column_config.SelectboxColumn(
                    "GL account", options=gl_codes, format_func=names.get, required=True, width="large"
                ),
                "cost_center": st.column_config.SelectboxColumn("Cost center", options=cc_codes)
                if cc
                else st.column_config.TextColumn("Cost center", disabled=True),
            },
            num_rows="dynamic", hide_index=True,
        )  # fmt: skip

        def cell(value: Any) -> str:
            return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value).strip()

        new = [
            Rule(cell(r.get("vendor")), cell(r.get("contains")), cell(r.get("gl_code")), cell(r.get("cost_center")))
            for r in edited.to_dict("records")
        ]
        incomplete = [n for n, r in enumerate(new, 1) if not (r.gl_code and (r.vendor or r.contains))]
        if incomplete:
            st.warning(
                f"Row {', '.join(map(str, incomplete))}: a rule needs a GL account and a vendor or words, or both. "
                "It is left out when you save.",
                icon=":material/warning:",
            )
        unknown = sorted({r.gl_code for r in new if r.gl_code and r.gl_code not in gl_codes})
        if unknown:
            st.warning(
                f"GL account {', '.join(unknown)} is not in your GL list any more: change these rules.",
                icon=":material/warning:",
            )
        if st.button("Save rules", type="primary", icon=":material/save:", key="rules_save"):
            saved = store.save_coding_rules(new, actor=reviewer())
            replace_editor("rules_grid", _rules_frame(store.coding_rules()))
            left_out = (
                f"; {ui.plural(len(incomplete), 'incomplete row')} left out (no GL account, or no vendor or words)"
            )
            notify(
                f"{ui.plural(saved, 'coding rule')} saved{left_out if incomplete else ''}.", ":material/rule_settings:"
            )
            st.rerun()

    suggestions = [(r, n) for r, n in suggest_rules(store.feedback_rows(), current) if r.gl_code in gl_codes]
    if suggestions:
        with card("rules_suggested"):
            st.markdown("#### :material/lightbulb: Suggested from past coding")
            st.caption("These vendors were always coded to one GL account. Add a rule to make it certain.")
            for n, (rule, lines) in enumerate(suggestions[:8]):
                text, button = st.columns([4, 1], vertical_alignment="center")
                target = names.get(rule.gl_code, rule.gl_code) + (f" · {rule.cost_center}" if rule.cost_center else "")
                text.markdown(
                    f"**{md(rule.vendor)}** → {md(target)}  \n:gray[{ui.plural(lines, 'line')}, all coded the same]"
                )
                if button.button("Add rule", key=f"rule_add_{n}", icon=":material/add:", width="stretch"):
                    store.save_coding_rules([*current, rule], actor=reviewer())
                    replace_editor("rules_grid", _rules_frame(store.coding_rules()))
                    notify(f"Rule added: {rule.vendor} → {rule.gl_code}.", ":material/rule_settings:")
                    st.rerun()


def tax_setup(store: Store) -> None:
    st.caption(
        "Choose how each sales tax is posted. Recoverable taxes (input tax credits / refunds) go to their own "
        "GL account. Non-recoverable PST is normally added to the cost of the expense lines it applies to."
    )
    accounts = store.list_accounts("gl_accounts")
    labels = {"": "(choose an account)"} | {a["code"]: f"{a['code']} · {a['description'][:50]}" for a in accounts}
    options = ["", *(a["code"] for a in accounts)]
    treatments = store.tax_treatments()
    # Widget keys follow the stored data, so the form refreshes after an import or a save.
    version = abs(
        hash((tuple(options), tuple(sorted((t.tax_type, t.treatment, t.gl_code) for t in treatments.values()))))
    )
    where = {"GST": "Federal · all provinces", "HST": "ON · NB · NL · NS · PE", "PST": "BC · SK · MB (RST)",
             "QST": "Quebec (TVQ)", "OTHER": "Outside Canada (US sales tax, VAT)"}  # fmt: skip
    chosen = {}
    with card("taxsetup"):
        for tax_type in TAX_TYPES:
            current = treatments[tax_type]
            c1, c2, c3 = st.columns([2, 4, 4], vertical_alignment="center")
            c1.html(
                f"<div>{ui.tax_chip(tax_type)}</div><div class='apc-muted' style='margin-top:.3rem'>"
                f"{esc(where[tax_type])}</div>"
            )
            treatment = c2.selectbox(
                "Treatment", list(TREATMENTS), index=list(TREATMENTS).index(current.treatment),
                format_func=TREATMENTS.get, key=f"treat_{tax_type}_{version}", label_visibility="collapsed",
            )  # fmt: skip
            gl = ""
            if treatment == "expense_to_line":
                c3.html(ui.pill("Posted with each expense line's GL", "gray", "call_split"))
            else:
                gl = c3.selectbox(
                    "GL account", options, index=options.index(current.gl_code) if current.gl_code in options else 0,
                    format_func=lambda c: labels.get(c, c), key=f"taxgl_{tax_type}_{version}",
                    label_visibility="collapsed",
                )  # fmt: skip
            chosen[tax_type] = (treatment, gl)
        if st.button("Save tax setup", type="primary", icon=":material/save:"):
            for tax_type, (treatment, gl) in chosen.items():
                store.set_tax_treatment(tax_type, treatment, gl, actor=reviewer())
            notify("Tax setup saved.")
            st.rerun()

    with card("rates"):
        st.markdown("#### :material/calendar_month: Rates in force today")
        source = (
            "your copy in the data folder" if rates_path() != DEFAULT_RATES_PATH
            else "the rate table that comes with AP Coder (updated with it)"
        )  # fmt: skip
        st.caption(
            f"From {source}"
            f": `{short_path(rates_path())}`. To change a rate before an update brings it, copy "
            f"`data/{RATES_FILE}` into your data folder and edit the copy."
        )
        today = dt.date.today()
        rows = [
            [ui.tax_chip(r.tax_type), esc(r.province or "All provinces"), f"<b>{r.rate * 100:.3f}%</b>",
             esc(r.effective_from.isoformat())]
            for r in TaxRateTable.load().rates
            if r.effective_from <= today and (r.effective_to is None or today <= r.effective_to)
        ]  # fmt: skip
        st.html(ui.table(["Tax", "Province", "Rate", "Since"], rows, right=[2]))
