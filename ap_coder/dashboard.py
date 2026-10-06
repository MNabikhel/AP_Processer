"""AP Coder review dashboard (Streamlit). Runs locally: ``python -m ap_coder dashboard``.

Pages
-----
* Review queue: invoice image next to the AI's coding; edit anything, see the tax checks and
  GL distribution update live, then approve (which teaches the memory) or reject.
* Process invoices: upload files or pick up new files in private/invoices and run the engine.
* GL accounts: import / view / edit / delete GL accounts (cost codes) and cost centers with your
  own category column, map each sales tax to its GL, edit coding policy notes.
* Learning & accuracy: how often the AI is right, trend over time, per vendor, most common
  corrections, and the memory itself (delete a bad lesson).

All data stays in the local SQLite database (``private/ap_coder.db`` by default).
"""

from __future__ import annotations

import datetime as dt
import getpass
import io
import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ap_coder.config import Settings
from ap_coder.extraction import SUPPORTED_EXTENSIONS, ExtractionResult
from ap_coder.memory import ACCEPTED
from ap_coder.pipeline import InvoicePipeline, finalise_coding
from ap_coder.reference_data import UNASSIGNED, ReferenceData
from ap_coder.review import coding_from_inputs
from ap_coder.schema import PROVINCE_VALUES, InvoiceCoding
from ap_coder.store import APPROVED, DEFAULT_DB_PATH, FAILED, REJECTED, REVIEW, Store, load_sample_setup
from ap_coder.tax import PROVINCE_NAMES, TAX_TYPES, TREATMENTS, TaxRateTable

DB_PATH = Path(os.environ.get("AP_DB_PATH", DEFAULT_DB_PATH))
INVOICE_DIR = DB_PATH.parent / "invoices"
CACHE_DIR = DB_PATH.parent / ".cache" / "extraction"
TARGET_ACCURACY = 0.90
SERIES_BLUE = "#2a78d6"  # categorical slot 1 (dataviz reference palette)
TARGET_GRAY = "#8a8985"

st.set_page_config(page_title="AP Coder", page_icon="🧾", layout="wide")


# --- Shared helpers -----------------------------------------------------------------------------


@st.cache_resource
def get_store() -> Store:
    return Store(DB_PATH)


def get_settings() -> Settings:
    return Settings.from_env(os.environ.get("AP_ENV_FILE"))


def reviewer() -> str:
    return st.session_state.get("reviewer") or "reviewer"


def money(value: Any, currency: str = "") -> str:
    try:
        return f"{float(value):,.2f}{' ' + currency if currency else ''}"
    except (TypeError, ValueError):
        return "-"


def reference_or_none(store: Store) -> ReferenceData | None:
    try:
        return store.reference_data()
    except ValueError:
        return None


def gl_label_map(reference: ReferenceData | None) -> dict[str, str]:
    labels = {UNASSIGNED: f"{UNASSIGNED} · needs a code", "": "(none)"}
    if reference is not None:
        for row in reference.chart_of_accounts.rows:
            labels[row["gl_code"]] = f"{row['gl_code']} · {row.get('description', '')[:60]}"
    return labels


def cc_label_map(reference: ReferenceData | None) -> dict[str, str]:
    labels = {UNASSIGNED: f"{UNASSIGNED} · needs a cost center", "": "(none)"}
    if reference is not None and reference.cost_centers is not None:
        for row in reference.cost_centers.rows:
            labels[row["cost_center"]] = f"{row['cost_center']} · {row.get('description', '')[:50]}"
    return labels


@st.cache_data(show_spinner=False)
def render_pages(path: str, mtime: float) -> list[bytes]:
    """PNG bytes per page (PDF via PyMuPDF, images via Pillow)."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import pymupdf

        with pymupdf.open(path) as doc:
            return [page.get_pixmap(dpi=110).tobytes("png") for page in doc]
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        from PIL import Image, ImageSequence

        pages = []
        with Image.open(path) as img:
            for frame in ImageSequence.Iterator(img):
                buf = io.BytesIO()
                frame.convert("RGB").save(buf, format="PNG")
                pages.append(buf.getvalue())
        return pages
    return []


def flash() -> None:
    message = st.session_state.pop("flash", None)
    if message:
        st.success(message, icon="✅")


# --- Review queue --------------------------------------------------------------------------------------


def page_review() -> None:
    store = get_store()
    st.title("Review queue")
    flash()
    reference = reference_or_none(store)
    if reference is None:
        st.warning("No GL accounts yet. Import them on the **GL accounts** page first.", icon="⚠️")
        st.page_link(PAGES["accounts"], label="Go to GL accounts", icon="📒")
        return

    invoices = store.list_invoices()
    by_status = {s: [i for i in invoices if i["status"] == s] for s in (REVIEW, APPROVED, REJECTED, FAILED)}
    tab_review, tab_approved, tab_other = st.tabs(
        [
            f"To review ({len(by_status[REVIEW])})",
            f"Approved ({len(by_status[APPROVED])})",
            f"Failed / rejected ({len(by_status[FAILED]) + len(by_status[REJECTED])})",
        ]
    )

    with tab_review:
        pending = by_status[REVIEW]
        if not pending:
            st.info("Nothing waiting for review. Process new invoices on the **Process invoices** page.", icon="📭")
        else:
            st.dataframe(
                _queue_frame(pending),
                hide_index=True,
                column_config={"Confidence": st.column_config.NumberColumn("Confidence", format="percent")},
            )
            ids = [i["id"] for i in pending]
            current = st.session_state.get("open_invoice")
            index = ids.index(current) if current in ids else 0
            labels = {
                i["id"]: f"#{i['id']} · {i['vendor_name'] or i['file_name']} · {i['invoice_number'] or ''}"
                for i in pending
            }
            chosen = st.selectbox("Open invoice", ids, index=index, format_func=labels.get)
            st.session_state["open_invoice"] = chosen
            st.divider()
            render_invoice(store, reference, chosen)

    with tab_approved:
        approved = by_status[APPROVED]
        if not approved:
            st.caption("No approved invoices yet.")
        else:
            st.dataframe(_queue_frame(approved, approved=True), hide_index=True)
            export = _export_rows(store, approved)
            st.download_button(
                "Download GL distribution of approved invoices (CSV)",
                pd.DataFrame(export).to_csv(index=False).encode("utf-8"),
                file_name=f"approved_gl_distribution_{dt.date.today()}.csv",
                mime="text/csv",
            )
            labels = {i["id"]: f"#{i['id']} · {i['vendor_name']} · {i['invoice_number']}" for i in approved}
            chosen = st.selectbox("View approved invoice", list(labels), format_func=labels.get, key="view_approved")
            render_approved(store, reference, chosen)

    with tab_other:
        others = by_status[FAILED] + by_status[REJECTED]
        if not others:
            st.caption("Nothing here.")
        for inv in others:
            with st.expander(f"#{inv['id']} · {inv['file_name']} · {inv['status']}"):
                st.write(inv.get("error") or "No reason recorded.")
                c1, c2 = st.columns(2)
                if c1.button("Process again", key=f"retry_{inv['id']}"):
                    full = store.get_invoice(inv["id"])
                    path = Path(full["source_path"])
                    if not path.exists():
                        st.error(f"The original file is no longer at {path}.")
                    else:
                        store.delete_invoice(inv["id"])
                        run_pipeline(store, [path])
                        st.rerun()
                if c2.button("Delete", key=f"del_{inv['id']}"):
                    store.delete_invoice(inv["id"])
                    st.rerun()


def _queue_frame(rows: list[dict[str, Any]], approved: bool = False) -> pd.DataFrame:
    data = []
    for r in rows:
        item = {
            "ID": r["id"],
            "Vendor": r["vendor_name"] or "",
            "Invoice #": r["invoice_number"] or "",
            "Date": r["invoice_date"] or "",
            "Total": money(r["grand_total"], r["currency"] or ""),
            "File": r["file_name"],
        }
        if approved:
            item["Approved by"] = r["reviewer"] or ""
            item["Approved"] = (r["reviewed_at"] or "")[:16].replace("T", " ")
        else:
            item["Flag"] = "⚠️ Review" if r["requires_review"] else "✓ Ready"
            item["Confidence"] = r["adjusted_confidence"] or 0.0
        data.append(item)
    return pd.DataFrame(data)


def _export_rows(store: Store, approved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for inv in approved:
        full = store.get_invoice(inv["id"])
        final = full["final_output"] or {}
        for e in final.get("gl_distribution", []):
            rows.append(
                {
                    "invoice_id": inv["id"],
                    "vendor_name": final.get("vendor_name"),
                    "invoice_number": final.get("invoice_number"),
                    "invoice_date": final.get("invoice_date"),
                    "currency": final.get("currency"),
                    "kind": e["kind"],
                    "gl_code": e["gl_code"],
                    "cost_center": e["cost_center"],
                    "description": e["description"],
                    "net_amount": e["net_amount"],
                    "non_recoverable_tax": e["non_recoverable_tax"],
                    "amount": e["amount"],
                }
            )
    return rows


def _document_panel(inv: dict[str, Any]) -> None:
    path = Path(inv["source_path"])
    pages = render_pages(str(path), path.stat().st_mtime) if path.exists() else []
    if pages:
        page_no = 1
        if len(pages) > 1:
            page_no = st.radio("Page", list(range(1, len(pages) + 1)), horizontal=True, key=f"page_{inv['id']}")
        st.image(pages[page_no - 1], width="stretch")
    elif not path.exists():
        st.warning(f"Original file not found at {path}", icon="⚠️")
    with st.expander("Extracted text (what the AI read)", expanded=not pages):
        st.html(f"<div style='font-size:0.85rem'>{_md_to_html(inv.get('extraction_md') or '')}</div>")


def _md_to_html(md: str) -> str:
    """Document Intelligence Markdown already uses HTML tables; just keep line breaks readable."""
    import html as _html

    out = []
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("<!--"):
            if "PageBreak" in stripped:
                out.append("<hr>")
            continue
        if stripped.startswith(("<table", "</table", "<tr", "<th", "<td")):
            out.append(stripped)
        else:
            out.append(_html.escape(line) + "<br>")
    return "\n".join(out)


def _issue_list(report: Any) -> None:
    errors = [i for i in report.issues if i.severity == "error"]
    warnings = [i for i in report.issues if i.severity == "warning"]
    for issue in errors:
        where = f" (line {issue.line_number})" if issue.line_number else ""
        st.error(f"**{issue.code}**{where}: {issue.message}", icon="⛔")
    for issue in warnings:
        where = f" (line {issue.line_number})" if issue.line_number else ""
        st.warning(f"**{issue.code}**{where}: {issue.message}", icon="⚠️")
    if not report.issues:
        st.success("All checks passed: totals reconcile, taxes verified, codes valid.", icon="✅")
    if report.requires_review and not errors:
        st.warning(
            f"Needs a human look: confidence {report.adjusted_confidence:.0%} is below the "
            f"{report.review_threshold:.0%} review threshold.",
            icon="👀",
        )


def render_invoice(store: Store, reference: ReferenceData, invoice_id: int) -> None:
    inv = store.get_invoice(invoice_id)
    if inv is None:
        st.error("Invoice not found.")
        return
    ai = inv["ai_output"] or {}
    settings = get_settings()
    key = f"inv{invoice_id}"
    meta = inv.get("meta") or {}

    left, right = st.columns([5, 7], gap="large")
    with left:
        st.caption(f"📄 {inv['file_name']}")
        _document_panel(inv)

    with right:
        st.subheader("Invoice header")
        c1, c2, c3 = st.columns(3)
        header: dict[str, Any] = {
            "vendor_name": c1.text_input("Vendor", ai.get("vendor_name", ""), key=f"{key}_vendor"),
            "invoice_number": c2.text_input("Invoice #", ai.get("invoice_number", ""), key=f"{key}_number"),
            "invoice_date": c3.text_input("Date (YYYY-MM-DD)", ai.get("invoice_date", ""), key=f"{key}_date"),
        }
        c1, c2, c3 = st.columns(3)
        header["currency"] = c1.text_input("Currency", ai.get("currency", "CAD"), key=f"{key}_cur")
        prov_label = {
            p: f"{p} · {PROVINCE_NAMES.get(p, 'Outside Canada' if p else 'Unknown')}" for p in PROVINCE_VALUES
        }
        header["supplier_province"] = c2.selectbox(
            "Supplier province",
            PROVINCE_VALUES,
            index=PROVINCE_VALUES.index(ai.get("supplier_province", "")),
            format_func=prov_label.get,
            key=f"{key}_sprov",
        )
        header["ship_to_province"] = c3.selectbox(
            "Ship-to province (place of supply)",
            PROVINCE_VALUES,
            index=PROVINCE_VALUES.index(ai.get("ship_to_province", "")),
            format_func=prov_label.get,
            key=f"{key}_tprov",
        )
        c1, c2 = st.columns(2)
        header["gst_hst_registration_number"] = c1.text_input(
            "Supplier GST/HST #", ai.get("gst_hst_registration_number", ""), key=f"{key}_gst"
        )
        header["qst_registration_number"] = c2.text_input(
            "Supplier QST #", ai.get("qst_registration_number", ""), key=f"{key}_qst"
        )
        c1, c2, c3 = st.columns(3)
        header["subtotal"] = c1.number_input(
            "Subtotal", value=float(ai.get("subtotal", 0)), format="%.2f", key=f"{key}_sub"
        )
        header["tax_total"] = c2.number_input(
            "Tax total", value=float(ai.get("tax_total", 0)), format="%.2f", key=f"{key}_tax"
        )
        header["grand_total"] = c3.number_input(
            "Grand total", value=float(ai.get("grand_total", 0)), format="%.2f", key=f"{key}_total"
        )
        checks_box = st.container()

    # Full-width editors below the document so every column (GL account first) is visible.
    st.subheader("Line items")
    gl_labels = gl_label_map(reference)
    tax_gls = reference.tax.tax_gl_codes()
    gl_options = [UNASSIGNED] + [c for c in reference.chart_of_accounts.codes if c not in tax_gls]
    lines_df = pd.DataFrame(ai.get("line_items", []))
    if lines_df.empty:
        lines_df = pd.DataFrame(columns=["line_number", "description", "quantity", "unit_price", "amount",
                                         "predicted_gl_code", "predicted_cost_center", "taxes_applied",
                                         "reasoning_justification"])  # fmt: skip
    for code in lines_df.get("predicted_gl_code", []):
        if code not in gl_options:
            gl_options.append(code)  # keep unknown codes visible so they can be fixed
    column_config: dict[str, Any] = {
        "line_number": st.column_config.NumberColumn("#", width="small", step=1),
        "description": st.column_config.TextColumn("Description", width="medium"),
        "quantity": st.column_config.NumberColumn("Qty", format="%.2f"),
        "unit_price": st.column_config.NumberColumn("Unit price", format="%.2f"),
        "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
        "predicted_gl_code": st.column_config.SelectboxColumn(
            "GL account",
            options=gl_options,
            format_func=lambda c: gl_labels.get(c, f"{c} · unknown code"),
            width="medium",
            required=True,
        ),  # fmt: skip
        "taxes_applied": st.column_config.MultiselectColumn("Taxes", options=list(TAX_TYPES)),
        "reasoning_justification": st.column_config.TextColumn("AI reasoning", disabled=True, width="large"),
    }
    column_order = ["line_number", "description", "amount", "predicted_gl_code"]
    if reference.cost_centers is not None:
        cc_labels = cc_label_map(reference)
        cc_options = [UNASSIGNED, *reference.cost_centers.codes]
        column_config["predicted_cost_center"] = st.column_config.SelectboxColumn(
            "Cost center", options=cc_options, format_func=lambda c: cc_labels.get(c, c)
        )
        column_order.append("predicted_cost_center")
    column_order += ["taxes_applied", "quantity", "unit_price", "reasoning_justification"]
    edited_lines = st.data_editor(
        lines_df,
        column_config=column_config,
        column_order=column_order,
        num_rows="dynamic",
        hide_index=True,
        key=f"{key}_lines",
    )

    tax_col, tax_check_col = st.columns([5, 6], gap="large")
    tax_col.subheader("Sales tax")
    tax_df = pd.DataFrame(
        ai.get("tax_lines", []), columns=["tax_type", "province", "rate", "taxable_amount", "tax_amount"]
    )
    edited_tax = tax_col.data_editor(
        tax_df,
        column_config={
            "tax_type": st.column_config.SelectboxColumn("Tax", options=list(TAX_TYPES), required=True),
            "province": st.column_config.SelectboxColumn(
                "Province", options=PROVINCE_VALUES, format_func=lambda p: p or "—"
            ),
            "rate": st.column_config.NumberColumn("Rate", format="%.5f", help="Decimal: 13% = 0.13"),
            "taxable_amount": st.column_config.NumberColumn("Taxable amount", format="%.2f"),
            "tax_amount": st.column_config.NumberColumn("Tax amount", format="%.2f"),
        },
        num_rows="dynamic",
        hide_index=True,
        key=f"{key}_taxlines",
    )

    coding, problems = coding_from_inputs(header, edited_lines, edited_tax, ai)
    if coding is None:
        with checks_box:
            st.error("Fix these fields before the invoice can be checked:\n\n" + "\n".join(f"- {p}" for p in problems))
        return

    extraction = ExtractionResult(
        source=inv["source_path"],
        model_id=(meta.get("extraction") or {}).get("model_id", ""),
        content="",
        mean_word_confidence=(meta.get("extraction") or {}).get("mean_word_confidence"),
        invoice_fields=(meta.get("extraction") or {}).get("invoice_fields") or {},
    )
    output, report = finalise_coding(
        coding, reference, settings, extraction, store=store, exclude_invoice_id=invoice_id
    )
    with tax_check_col:
        st.subheader("Tax checks")
        _tax_check_table(coding, reference)

    with checks_box:
        st.subheader("Checks")
        history = report.checks.get("history") or []
        matches = [h for h in history if h["status"] == "match"]
        if matches:
            st.info(
                f"Reinforced by history: {len(matches)} line(s) match how reviewers coded this vendor before "
                f"(lines {', '.join(str(h['line_number']) for h in matches)}).",
                icon="🧠",
            )
        _issue_list(report)

    st.subheader("GL distribution (posting preview)")
    _distribution_table(output, reference, coding.currency)

    st.divider()
    errors = [i for i in report.issues if i.severity == "error"]
    allow = True
    if errors:
        allow = st.checkbox(f"Approve anyway ({len(errors)} error(s) above)", key=f"{key}_override")
    c1, c2, c3 = st.columns([2, 2, 1])
    if c1.button("✅ Approve & teach the AI", type="primary", disabled=not allow, key=f"{key}_approve"):
        counts = store.approve_invoice(invoice_id, output, reviewer())
        total = sum(counts.values())
        st.session_state["flash"] = (
            f"Invoice #{invoice_id} approved. Learned from {total} line(s): "
            f"{counts[ACCEPTED]} confirmed the AI, {total - counts[ACCEPTED]} corrected it."
        )
        st.session_state.pop("open_invoice", None)
        st.rerun()
    with c2.popover("Reject"):
        reason = st.text_input("Reason", key=f"{key}_reason")
        if st.button("Confirm reject", key=f"{key}_reject"):
            store.reject_invoice(invoice_id, reviewer(), reason)
            st.session_state["flash"] = f"Invoice #{invoice_id} rejected."
            st.session_state.pop("open_invoice", None)
            st.rerun()
    with c3.popover("Delete"):
        st.write("Remove this invoice from the queue? Nothing is learned from it.")
        if st.button("Delete invoice", key=f"{key}_delete"):
            store.delete_invoice(invoice_id)
            st.session_state.pop("open_invoice", None)
            st.rerun()


def _tax_check_table(coding: InvoiceCoding, reference: ReferenceData) -> None:
    if not coding.tax_lines:
        return
    try:
        on = dt.date.fromisoformat(coding.invoice_date)
    except ValueError:
        on = dt.date.today()
    rows = []
    for t in coding.tax_lines:
        expected = round(t.taxable_amount * t.rate, 2)
        prov = t.province or coding.ship_to_province or coding.supplier_province
        official = reference.tax.rates.rate_for(t.tax_type, prov if prov in PROVINCE_NAMES else "", on)
        treat = reference.tax.treatment(t.tax_type)
        gl = treat.gl_code if treat.needs_gl else "added to expense lines"
        rows.append(
            {
                "Tax": f"{t.tax_type} {t.province}".strip(),
                "Rate": f"{t.rate * 100:.3f}%",
                "Official rate": "not levied here" if official is None else f"{official * 100:.3f}%",
                "Taxable": money(t.taxable_amount),
                "Charged": money(t.tax_amount),
                "Base × rate": money(expected),
                "Math": "✓" if abs(expected - t.tax_amount) <= 0.01 * max(1, len(coding.line_items)) + 0.01 else "✗",
                "Posts to": gl or "⚠ not mapped",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def _distribution_table(output: dict[str, Any], reference: ReferenceData, currency: str) -> None:
    labels = gl_label_map(reference)
    rows = [
        {
            "Type": "Tax" if e["kind"] == "tax" else f"Line {e['line_number']}",
            "GL account": labels.get(e["gl_code"], e["gl_code"] or "⚠ not mapped"),
            "Cost center": e["cost_center"],
            "Description": e["description"],
            "Net": money(e["net_amount"]) if e["kind"] == "expense" else "",
            "Non-recoverable tax": money(e["non_recoverable_tax"]) if e["non_recoverable_tax"] else "",
            "Amount": money(e["amount"]),
        }
        for e in output["gl_distribution"]
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    total = round(sum(e["amount"] for e in output["gl_distribution"]), 2)
    diff = round(total - output["grand_total"], 2)
    if abs(diff) <= 0.01:
        st.caption(f"✓ Distribution totals {money(total, currency)} = grand total.")
    else:
        st.error(
            f"Distribution totals {money(total, currency)}, which is {money(diff)} off the grand total.", icon="⛔"
        )


def render_approved(store: Store, reference: ReferenceData, invoice_id: int) -> None:
    inv = store.get_invoice(invoice_id)
    final = inv["final_output"] or {}
    edits = inv.get("edits") or []
    st.caption(
        f"Approved by {inv['reviewer']} on {(inv['reviewed_at'] or '')[:16].replace('T', ' ')} · "
        + ("no changes to the AI's coding" if not edits else "reviewer changed: " + ", ".join(edits))
    )
    _distribution_table(final, reference, final.get("currency", ""))


# --- Process invoices --------------------------------------------------------------------------------------


def run_pipeline(store: Store, paths: list[Path]) -> None:
    reference = store.reference_data()
    settings = get_settings()
    pipeline = InvoicePipeline(settings, reference, cache_dir=CACHE_DIR, store=store)
    progress = st.progress(0.0, text="Starting…")
    ok = 0
    for n, path in enumerate(paths, start=1):
        progress.progress((n - 1) / len(paths), text=f"Reading and coding {path.name} ({n}/{len(paths)})…")
        result = pipeline.process(path)
        if result.ok:
            ok += 1
        else:
            st.error(f"{path.name}: {result.error}", icon="⛔")
    progress.progress(1.0, text="Done")
    st.session_state["flash"] = f"Processed {ok} of {len(paths)} invoice(s). They are waiting in the Review queue."


def page_process() -> None:
    store = get_store()
    st.title("Process invoices")
    flash()
    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", settings.document_intelligence.endpoint),
            ("AZURE_OPENAI_ENDPOINT", settings.openai.endpoint),
        )
        if not value
    ]
    if missing:
        st.error(
            "Azure is not configured yet: set " + ", ".join(missing) + " in your `.env` file, then restart the "
            "dashboard. (`.md`/`.txt` pre-extracted files only need Azure OpenAI.)",
            icon="⛔",
        )
    if not store.has_reference():
        st.warning("Import your GL accounts first, so the AI knows which codes it may use.", icon="⚠️")
        st.page_link(PAGES["accounts"], label="Go to GL accounts", icon="📒")
        return

    st.subheader("Upload invoices")
    uploaded = st.file_uploader(
        "PDF, TIFF, PNG or JPG. Files are saved to your private invoices folder on this computer.",
        type=sorted(e.lstrip(".") for e in SUPPORTED_EXTENSIONS),
        accept_multiple_files=True,
    )
    if uploaded and st.button(f"Process {len(uploaded)} uploaded invoice(s)", type="primary"):
        INVOICE_DIR.mkdir(parents=True, exist_ok=True)
        paths = []
        for f in uploaded:
            target = INVOICE_DIR / f.name
            stem, n = target.stem, 1
            while target.exists() and target.read_bytes() != f.getvalue():
                target = INVOICE_DIR / f"{stem}_{n}{target.suffix}"
                n += 1
            target.write_bytes(f.getvalue())
            paths.append(target)
        run_pipeline(store, paths)
        st.rerun()

    st.subheader(f"Files waiting in {INVOICE_DIR}")
    files = (
        sorted(p for p in INVOICE_DIR.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
        if INVOICE_DIR.exists()
        else []
    )
    new_files = [p for p in files if store.find_by_hash(p) is None]
    if not new_files:
        st.caption("No new files. Copy invoices into this folder, or upload them above.")
    else:
        st.write(f"{len(new_files)} file(s) not processed yet:")
        st.dataframe(pd.DataFrame({"File": [p.name for p in new_files]}), hide_index=True)
        if st.button(f"Process {len(new_files)} file(s)", type="primary"):
            run_pipeline(store, new_files)
            st.rerun()


# --- GL accounts ------------------------------------------------------------------------------------------------

_CODE_HINTS = ("gl", "account", "code", "cost", "centre", "center", "saknr", "kostl")
_DESC_HINTS = ("desc", "name", "title", "text")
_CAT_HINTS = ("categ", "group", "type", "class")


def _guess(columns: list[str], hints: tuple[str, ...], default: int = 0) -> int:
    for i, col in enumerate(columns):
        if any(h in col.lower() for h in hints):
            return i
    return default


def _read_upload(upload: Any) -> pd.DataFrame:
    if upload.name.lower().endswith((".xlsx", ".xls")):
        sheets = pd.read_excel(upload, sheet_name=None, dtype=str)
        name = (
            st.selectbox("Sheet", list(sheets), key=f"sheet_{upload.name}") if len(sheets) > 1 else next(iter(sheets))
        )
        df = sheets[name]
    else:
        df = pd.read_csv(upload, dtype=str, keep_default_na=False)
    return df.fillna("")


def account_manager(store: Store, table: str, noun: str) -> None:
    rows = store.list_accounts(table)
    with st.expander(f"Import {noun}s from CSV or Excel", expanded=not rows):
        upload = st.file_uploader("Choose a file exported from your ERP", type=["csv", "xlsx"], key=f"{table}_upload")
        if upload is not None:
            df = _read_upload(upload)
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
            if st.button(f"Import {noun}s", type="primary", key=f"{table}_import"):
                result = store.import_accounts(
                    table,
                    df.to_dict("records"),
                    code_col,
                    None if desc_col == "(none)" else desc_col,
                    None if cat_col == "(none)" else cat_col,
                    replace_all=replace,
                )
                st.session_state["flash"] = (
                    f"Imported: {result['added']} added, {result['updated']} updated, "
                    f"{result['skipped']} skipped (blank or repeated codes)."
                )
                st.rerun()

    if not rows:
        st.info(f"No {noun}s yet.", icon="📭")
        return

    counts = pd.Series([r["category"] or "Uncategorised" for r in rows]).value_counts()
    st.caption(f"{len(rows)} {noun}s · " + " · ".join(f"{cat}: {n}" for cat, n in counts.items()))
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
    c1, c2 = st.columns([1, 3])
    if c1.button("Save changes", type="primary", key=f"{table}_save"):
        keep = edited[~edited["delete"].fillna(False).astype(bool)].copy()
        keep["code"] = keep["code"].fillna("").astype(str).str.strip()
        keep = keep[keep["code"] != ""]
        dupes = sorted(keep["code"][keep["code"].duplicated()].unique())
        if dupes:
            st.error(f"These codes appear more than once: {', '.join(dupes)}")
        else:
            removed = len(edited) - len(keep)
            store.save_accounts(table, keep.fillna("").to_dict("records"))
            st.session_state["flash"] = f"Saved {len(keep)} {noun}s" + (f", removed {removed}." if removed else ".")
            st.rerun()
    c2.download_button(
        f"Download {noun}s (CSV)",
        df.drop(columns=["delete"]).to_csv(index=False).encode("utf-8"),
        file_name=f"{table}.csv",
        mime="text/csv",
        key=f"{table}_download",
    )


def page_accounts() -> None:
    store = get_store()
    st.title("GL accounts & tax setup")
    flash()
    if not store.has_reference():
        with st.container(border=True):
            st.write("**Just exploring?** Load the bundled sample GL accounts, cost centers and tax setup.")
            if st.button("Load sample setup"):
                load_sample_setup(store)
                st.session_state["flash"] = "Sample setup loaded."
                st.rerun()

    tab_gl, tab_cc, tab_tax, tab_policy = st.tabs(
        ["GL accounts (cost codes)", "Cost centers (optional)", "Sales tax setup", "Coding policy"]
    )
    with tab_gl:
        st.caption(
            "The AI may only use the codes listed here. Descriptions matter: the AI matches invoice lines "
            "against them. Use the category column to group codes your own way."
        )
        account_manager(store, "gl_accounts", "GL account")
    with tab_cc:
        st.caption("Optional. Leave empty if you do not code invoices to cost centers.")
        account_manager(store, "cost_centers", "cost center")
    with tab_tax:
        tax_setup(store)
    with tab_policy:
        st.caption(
            "Plain-English rules the AI follows, one per line (e.g. 'Laptops under $2,500 go to 6010'). "
            "Lines starting with # are ignored."
        )
        notes = st.text_area("Coding policy", store.get_setting("policy_notes"), height=260)
        if st.button("Save policy", type="primary"):
            store.set_setting("policy_notes", notes)
            st.session_state["flash"] = "Coding policy saved."
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
    names = {"GST": "GST (federal)", "HST": "HST (ON, NB, NL, NS, PE)", "PST": "PST / RST (BC, SK, MB)",
             "QST": "QST (Quebec)"}  # fmt: skip
    chosen = {}
    for tax_type in TAX_TYPES:
        current = treatments[tax_type]
        c1, c2, c3 = st.columns([2, 4, 4])
        c1.markdown(f"**{names[tax_type]}**")
        treatment = c2.selectbox(
            "Treatment", list(TREATMENTS), index=list(TREATMENTS).index(current.treatment),
            format_func=TREATMENTS.get, key=f"treat_{tax_type}_{version}", label_visibility="collapsed",
        )  # fmt: skip
        gl = ""
        if treatment == "expense_to_line":
            c3.caption("Posted with each expense line's GL")
        else:
            gl = c3.selectbox(
                "GL account", options, index=options.index(current.gl_code) if current.gl_code in options else 0,
                format_func=lambda c: labels.get(c, c), key=f"taxgl_{tax_type}_{version}", label_visibility="collapsed",
            )  # fmt: skip
        chosen[tax_type] = (treatment, gl)
    if st.button("Save tax setup", type="primary"):
        for tax_type, (treatment, gl) in chosen.items():
            store.set_tax_treatment(tax_type, treatment, gl)
        st.session_state["flash"] = "Tax setup saved."
        st.rerun()

    st.markdown("**Rates in force today** (from `data/canada_tax_rates.csv`; edit that file when a rate changes)")
    today = dt.date.today()
    rates = [
        {"Tax": r.tax_type, "Province": r.province or "All provinces", "Rate": f"{r.rate * 100:.3f}%",
         "Since": r.effective_from.isoformat()}
        for r in TaxRateTable.load().rates
        if r.effective_from <= today and (r.effective_to is None or today <= r.effective_to)
    ]  # fmt: skip
    st.dataframe(pd.DataFrame(rates), hide_index=True)


# --- Learning & accuracy ----------------------------------------------------------------------------------------


def page_learning() -> None:
    import altair as alt

    store = get_store()
    st.title("Learning & accuracy")
    flash()
    m = store.metrics()
    if not m["lines_reviewed"]:
        st.info("No reviews yet. Accuracy and the AI's memory appear here once invoices are approved.", icon="📭")
        return

    accuracy = m["line_accuracy"] or 0.0
    approved = m["invoices_by_status"].get(APPROVED, 0)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("AI coding accuracy", f"{accuracy:.1%}", f"{(accuracy - TARGET_ACCURACY) * 100:+.1f} pts vs 90% target")
    c2.metric("Lines reviewed", f"{m['lines_reviewed']:,}")
    c3.metric("Corrections taught", f"{m['lines_corrected']:,}")
    c4.metric("Invoices approved without edits", f"{m['invoices_approved_without_edits']} of {approved}")

    weekly = pd.DataFrame(m["weekly"])
    if not weekly.empty:
        weekly["lines"] = weekly["accepted"] + weekly["corrected"]
        weekly["accuracy"] = weekly["accepted"] / weekly["lines"]
        st.subheader("AI coding accuracy by week")
        base = alt.Chart(weekly).encode(x=alt.X("week:N", title=None, axis=alt.Axis(labelAngle=0)))
        line = base.mark_line(color=SERIES_BLUE, strokeWidth=2, point=alt.OverlayMarkDef(size=64, filled=True)).encode(
            y=alt.Y("accuracy:Q", title=None, scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", grid=True)),
            tooltip=[
                alt.Tooltip("week:N", title="Week"),
                alt.Tooltip("accuracy:Q", title="Accuracy", format=".0%"),
                alt.Tooltip("accepted:Q", title="Confirmed"),
                alt.Tooltip("corrected:Q", title="Corrected"),
            ],
        )
        target = (
            alt.Chart(pd.DataFrame({"y": [TARGET_ACCURACY]}))
            .mark_rule(color=TARGET_GRAY, strokeDash=[4, 4])
            .encode(y="y:Q")
        )
        label = (
            alt.Chart(pd.DataFrame({"y": [TARGET_ACCURACY], "text": ["90% target"]}))
            .mark_text(align="left", dx=4, dy=-6, color=TARGET_GRAY)
            .encode(y="y:Q", text="text:N", x=alt.value(0))
        )
        st.altair_chart((line + target + label).properties(height=260), width="stretch")
        with st.expander("Show as table"):
            st.dataframe(weekly[["week", "accepted", "corrected", "accuracy"]], hide_index=True)

    left, right = st.columns(2)
    with left:
        st.subheader("By vendor")
        vendors = pd.DataFrame(m["by_vendor"])
        vendors["accuracy"] = vendors["accepted"] / vendors["lines"]
        vendors["last_seen"] = vendors["last_seen"].str[:16].str.replace("T", " ")
        st.dataframe(
            vendors[["vendor_name", "lines", "accuracy", "corrected", "last_seen"]],
            hide_index=True,
            column_config={
                "vendor_name": "Vendor",
                "lines": "Lines",
                "accuracy": st.column_config.ProgressColumn("AI accuracy", min_value=0, max_value=1, format="percent"),
                "corrected": "Corrections",
                "last_seen": "Last reviewed",
            },
        )
    with right:
        st.subheader("Most common corrections")
        corrections = pd.DataFrame(m["top_corrections"])
        if corrections.empty:
            st.caption("No corrections yet: the AI has matched every reviewer decision.")
        else:
            labels = gl_label_map(reference_or_none(store))
            corrections["AI suggested"] = corrections["suggested_gl"].map(lambda c: labels.get(c, c))
            corrections["Reviewer chose"] = corrections["final_gl"].map(lambda c: labels.get(c, c))
            st.dataframe(corrections[["AI suggested", "Reviewer chose", "n"]].rename(columns={"n": "Times"}),
                         hide_index=True)  # fmt: skip

    st.subheader("The AI's memory")
    st.caption(
        "Every approved line is a lesson. Lessons from the same vendor are shown to the AI on the next invoice; "
        "corrections count most. Delete a lesson if a reviewer made a mistake."
    )
    rows = store.feedback_rows(limit=2000)
    memory_version = rows[0]["id"] if rows else 0
    memory = pd.DataFrame(rows)[
        [
            "id",
            "created_at",
            "vendor_name",
            "description",
            "suggested_gl",
            "final_gl",
            "final_cc",
            "outcome",
            "reviewer",
        ]
    ]
    memory.insert(0, "forget", False)
    memory["created_at"] = memory["created_at"].str[:10]
    edited = st.data_editor(
        memory,
        hide_index=True,
        disabled=[c for c in memory.columns if c != "forget"],
        column_config={
            "forget": st.column_config.CheckboxColumn("Forget?", width="small"),
            "id": None,
            "created_at": "Date",
            "vendor_name": "Vendor",
            "description": st.column_config.TextColumn("Line", width="large"),
            "suggested_gl": "AI suggested",
            "final_gl": "Final GL",
            "final_cc": "Cost center",
            "outcome": "Outcome",
            "reviewer": "Reviewer",
        },
        key=f"memory_editor_{memory_version}_{len(rows)}",
    )
    selected = edited.loc[edited["forget"], "id"].tolist()
    if selected and st.button(f"Forget {len(selected)} lesson(s)"):
        store.delete_feedback([int(i) for i in selected])
        st.session_state["flash"] = f"Forgot {len(selected)} lesson(s)."
        st.rerun()


# --- App shell ------------------------------------------------------------------------------------------------

PAGES = {
    "review": st.Page(page_review, title="Review queue", icon="📥", default=True),
    "process": st.Page(page_process, title="Process invoices", icon="⚙️"),
    "accounts": st.Page(page_accounts, title="GL accounts & tax", icon="📒"),
    "learning": st.Page(page_learning, title="Learning & accuracy", icon="🧠"),
}

with st.sidebar:
    st.markdown("### 🧾 AP Coder")
    st.text_input("Reviewer name", value=os.environ.get("AP_REVIEWER") or getpass.getuser(), key="reviewer")
    st.caption(f"Data stays on this computer:\n`{DB_PATH}`")

st.navigation(list(PAGES.values())).run()
