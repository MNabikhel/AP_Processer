"""Learning & accuracy: coding accuracy vs target, trend, vendors, corrections and the memory; supplier
learning (header-field accuracy per supplier and its path to touchless processing); and the readers (each
capture reader against what AP approved, and the training data export)."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ap_coder import history, ui
from ap_coder.safe import md
from ap_coder.store import APPROVED
from ap_coder.webapp.accounts import _read_upload
from ap_coder.webapp.common import (
    SERIES_BLUE,
    TARGET_ACCURACY,
    TARGET_GRAY,
    card,
    esc,
    get_store,
    gl_name,
    notify,
    page_head,
    reference_or_none,
    reviewer,
    show_toast,
)

HISTORY_LABELS = {"vendor_name": "Vendor *", "description": "Line description *", "gl_code": "GL account *",
                  "cost_center": "Cost center", "amount": "Amount", "date": "Date"}  # fmt: skip


def _history_card(store) -> None:
    count = store.history_count()
    title = "Teach from past coding" + (f" ({ui.plural(count, 'past line')} taught)" if count else "")
    with st.expander(title, icon=":material/history_edu:", expanded=False):
        st.caption(
            "Export last year's posted AP invoice lines from the ERP (vendor, line description, GL account, cost "
            "center) and import them here: the AI then sees how each vendor was coded before, from the first "
            "invoice. Past lines never count in the accuracy figures."
        )
        upload = st.file_uploader("AP line history (CSV or Excel)", type=["csv", "xlsx"], key="hist_upload")
        df = _read_upload(upload, "hist") if upload is not None else None
        if df is not None and not df.empty:
            columns = list(df.columns)
            guessed = history.map_columns(columns)
            options = ["(none)", *columns]
            cols = st.columns(3)
            chosen = {}
            for i, (field, label) in enumerate(HISTORY_LABELS.items()):
                pick = cols[i % 3].selectbox(
                    label, options, index=options.index(guessed[field]) if field in guessed else 0,
                    key=f"hist_col_{field}",
                )  # fmt: skip
                if pick != "(none)":
                    chosen[field] = pick
            missing = [HISTORY_LABELS[f].rstrip(" *") for f in history.REQUIRED if f not in chosen]
            if missing:
                st.warning("Pick the column for: " + ", ".join(missing))
            elif st.button(
                f"Teach {ui.plural(len(df), 'line')}", type="primary", icon=":material/school:", key="hist_go"
            ):
                rows, skipped = history.rows_from_records(df.to_dict("records"), chosen)
                result = store.import_history(rows, actor=reviewer())
                notify(
                    f"Taught {ui.plural(result['added'], 'past line')}"
                    + (f"; {result['skipped'] + skipped:,} skipped (incomplete or already taught)"
                       if result["skipped"] + skipped else "")
                    + (f"; {result['unknown_gl']:,} with a GL account that is not in your list" if result["unknown_gl"]
                       else "") + ".",
                    ":material/school:",
                )  # fmt: skip
                st.rerun()
        if count and st.button("Forget all past coding", icon=":material/delete_sweep:", key="hist_forget"):
            n = store.forget_history(actor=reviewer())
            notify(f"Forgot {ui.plural(n, 'past line')}.", ":material/delete_sweep:")
            st.rerun()


# --- Learning & accuracy ----------------------------------------------------------------------------------------


def page_learning() -> None:
    store = get_store()
    show_toast()
    page_head("learning", "Learning & accuracy", "How often the coding is right, and what AP Coder has learned.")
    coding_tab, supplier_tab, readers_tab = st.tabs(["Coding accuracy", "Supplier learning", "Readers"])
    with coding_tab:
        _history_card(store)
        _coding_accuracy(store)
    with supplier_tab:
        _supplier_learning(store)
    with readers_tab:
        _readers(store)


# --- Supplier learning ---------------------------------------------------------------------------------------

FIELD_LABELS = {
    "vendor_name": "Vendor", "invoice_number": "Invoice #", "invoice_date": "Invoice date", "due_date": "Due date",
    "po_number": "PO #", "currency": "Currency", "gst_hst_registration_number": "GST/HST #",
    "qst_registration_number": "QST #", "subtotal": "Subtotal", "gst_amount": "GST", "hst_amount": "HST",
    "pst_amount": "PST", "qst_amount": "QST", "tax_total": "Tax total", "grand_total": "Total",
    "payment_terms": "Terms",
}  # fmt: skip
_STATE_ORDER = {"ready": 0, "suspended": 1, "autonomous": 2, "supervised": 3, "learning": 4}


def _supplier_learning(store) -> None:
    from ap_coder.capture.supplier import AUTONOMOUS, READY, SUPERVISED, SUSPENDED, autonomy_status, meets_policy

    profiles = store.list_supplier_profiles()
    policy = store.autonomy_policy()
    if not profiles:
        with card("nosuppliers"):
            st.html(
                ui.empty_state(
                    "No supplier learned yet",
                    "Each approved invoice teaches AP Coder where that supplier prints its invoice number, dates "
                    "and totals, and records how many it read right. Once a supplier has enough clean invoices in a "
                    "row, a manager can turn on touchless processing, and every invoice still goes through all the "
                    "checks.",
                    ui.LEARNING_SVG,
                )
            )
        return
    rows = []
    for p in profiles:
        stats = store.supplier_stats(p["key"], policy.window)
        state, progress, why = autonomy_status(stats, policy, p["state"], p["autonomous_since"])
        rows.append((p, stats, state, progress, why))
    rows.sort(key=lambda r: (_STATE_ORDER.get(r[2], 9), -r[1].invoices, (r[0]["display_name"] or "").lower()))
    st.caption(f"Bar for touchless processing: {policy.describe()}.")
    for p, stats, state, progress, why in rows:
        name = p["display_name"] or p["key"]
        slug = hashlib.sha1(p["key"].encode()).hexdigest()[:10]
        accuracy = stats.accuracy
        sub = " · ".join(
            [
                f"{stats.invoices} invoice{'s' if stats.invoices != 1 else ''} reviewed",
                f"field accuracy {accuracy:.1%} (at least {stats.lower_bound(policy.z):.1%})"
                if accuracy is not None
                else "no fields checked yet",
                f"clean streak {stats.clean_streak}",
            ]
        )
        with card(f"supplier_{slug}"):
            st.html(ui.supplier_row(name, sub, state, progress, why))
            info, actions = st.columns([3, 2], vertical_alignment="center")
            with info.expander("Per-field accuracy", icon=":material/table_rows:"):
                if not stats.fields:
                    st.caption("No fields checked yet.")
                else:
                    table = []
                    for field, fs in sorted(stats.fields.items(), key=lambda kv: (kv[1].accuracy or 0, kv[0])):
                        tone = "ok" if fs.correct == fs.n else "warn"
                        table.append(
                            [esc(FIELD_LABELS.get(field, field)), f"{fs.n:,}", f"{fs.n - fs.correct:,}",
                             ui.pill(f"{fs.accuracy:.0%}", tone)]
                        )  # fmt: skip
                    st.html(ui.table(["Field", "Checked", "Corrected", "Accuracy"], table, right=[1, 2, 3]))
                    st.caption(f"Over the last {ui.plural(stats.window_invoices, 'reviewed invoice')}.")
            can_turn_on = state == READY or (state == SUSPENDED and meets_policy(stats, policy))
            if can_turn_on:
                with actions.popover(
                    "Turn on autonomy…", icon=":material/bolt:", width="stretch", key=f"sup_menu_{slug}"
                ):
                    st.markdown(
                        f"Process **{md(name)}** invoices without a person when every header field is verified and "
                        f"every check passes. The policy: {policy.describe()}."
                    )
                    if st.button("Yes, turn on autonomy", type="primary", key=f"sup_on_{slug}"):
                        try:
                            store.set_supplier_state(p["key"], AUTONOMOUS, reviewer())
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            notify(f"Autonomy is on for {md(name)}.", ":material/bolt:")
                            st.rerun()
            if p["state"] in (AUTONOMOUS, SUSPENDED) and actions.button(
                "Turn off", icon=":material/pan_tool:", key=f"sup_off_{slug}", width="stretch"
            ):
                store.set_supplier_state(p["key"], SUPERVISED, reviewer(), reason="turned off on the Learning page")
                notify(f"{md(name)} is back to supervised: every invoice is reviewed.", ":material/pan_tool:")
                st.rerun()


# --- Coding accuracy -----------------------------------------------------------------------------------------


def _coding_accuracy(store) -> None:
    import altair as alt

    m = store.metrics()
    if not m["lines_reviewed"]:
        with card("nolearning"):
            st.html(
                ui.empty_state(
                    "Nothing learned yet",
                    "Accuracy and the AI's memory appear here once invoices are approved in the review queue.",
                    ui.LEARNING_SVG,
                )
            )
        return

    accuracy = m["line_accuracy"] or 0.0
    approved = m["invoices_by_status"].get(APPROVED, 0)
    weekly = pd.DataFrame(m["weekly"])
    weekly["lines"] = weekly["accepted"] + weekly["corrected"]
    weekly["accuracy"] = weekly["accepted"] / weekly["lines"]
    st.html(
        ui.tiles(
            [
                ui.tile(
                    "Coding accuracy",
                    f"{accuracy:.1%}",
                    "auto_awesome",
                    "green" if accuracy >= TARGET_ACCURACY else "violet",
                    f"{(accuracy - TARGET_ACCURACY) * 100:+.1f} pts vs 90% target",
                    "up" if accuracy >= TARGET_ACCURACY else "down",
                    weekly["accuracy"].tolist() if len(weekly) > 1 else None,
                ),  # fmt: skip
                ui.tile(
                    "Lines reviewed",
                    f"{m['lines_reviewed']:,}",
                    "fact_check",
                    "blue",
                    f"{m['lines_accepted']:,} confirmed",
                ),  # fmt: skip
                ui.tile(
                    "Corrections taught", f"{m['lines_corrected']:,}", "school", "amber", "used on the next invoice"
                ),
                ui.tile(
                    "Approved without edits",
                    f"{m['invoices_approved_without_edits']}/{approved}",
                    "bolt",
                    "green",
                    "straight-through invoices",
                ),  # fmt: skip
            ]
        )
    )

    chart_col, feed_col = st.columns([3, 2], gap="medium")
    with chart_col, card("trend"):
        st.markdown("#### Accuracy by week")
        base = alt.Chart(weekly).encode(x=alt.X("week:N", title=None, axis=alt.Axis(labelAngle=0)))
        area = base.mark_area(color=SERIES_BLUE, opacity=0.08).encode(y=alt.Y("accuracy:Q"))
        line = base.mark_line(
            color=SERIES_BLUE, strokeWidth=2.5, point=alt.OverlayMarkDef(size=70, filled=True)
        ).encode(
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
        chart = (
            (area + line + target + label)
            .properties(height=260, background="transparent")
            .configure_view(strokeWidth=0)
        )
        st.altair_chart(chart, width="stretch")
        with st.expander("Show as table"):
            st.dataframe(weekly[["week", "accepted", "corrected", "accuracy"]], hide_index=True)

    rows = store.feedback_rows(limit=2000)
    reviewed = [r for r in rows if r["outcome"] != "history"]
    with feed_col, card("feed"):
        st.markdown("#### Recent lessons")
        items = []

        def short(text: str, n: int = 42) -> str:
            return text if len(text) <= n else text[: n - 1].rstrip() + "…"

        for r in reviewed[:4]:  # as tall as the chart beside it; every lesson is in the memory below
            if r["outcome"] == "corrected":
                was = (
                    f"<span class='apc-strike apc-mono'>{esc(r['suggested_gl'])}</span>"
                    if r["suggested_gl"]
                    else "<i>new line</i>"
                )
                text = (
                    f"<b>{esc(r['reviewer'])}</b> corrected <b>{esc(short(r['description']))}</b> "
                    f"{was}<span class='apc-arrow'>→</span><b class='apc-mono'>{esc(r['final_gl'])}</b>"
                )
            else:
                text = (
                    f"<b>{esc(r['reviewer'])}</b> confirmed <b>{esc(short(r['description']))}</b> "
                    f"<span class='apc-arrow'>→</span><b class='apc-mono'>{esc(r['final_gl'])}</b>"
                )
            items.append(ui.feed_item(r["reviewer"] or "?", f"{text}<br><span class='apc-muted'>"
                                      f"{esc(r['vendor_name'])}</span>", ui.time_ago(r["created_at"])))  # fmt: skip
        st.html(f"<div class='apc-feed'>{''.join(items)}</div>")

    left, right = st.columns(2, gap="medium")
    with left, card("vendors"):
        st.markdown("#### Accuracy by vendor")
        st.html(
            "".join(
                ui.vendor_row(v["vendor_name"], v["lines"], v["accepted"] / v["lines"], v["corrected"])
                for v in m["by_vendor"][:12]
            )
        )
    with right, card("corrections"):
        st.markdown("#### Most common corrections")
        if not m["top_corrections"]:
            st.caption("No corrections yet: the AI has matched every reviewer decision.")
        else:
            reference = reference_or_none(store)
            table_rows = []
            for c in m["top_corrections"]:
                before = c["suggested_gl"]
                before_name = gl_name(reference, before) if reference and before != "(new line)" else ""
                after_name = gl_name(reference, c["final_gl"]) if reference else ""
                table_rows.append(
                    [
                        f"<div class='gl'>{esc(before)}<small>{esc(before_name)}</small></div>",
                        "<span class='apc-arrow'>→</span>",
                        f"<div class='gl'>{esc(c['final_gl'])}<small>{esc(after_name)}</small></div>",
                        ui.pill(f"×{c['n']}", "violet"),
                    ]
                )
            st.html(ui.table(["AI suggested", "", "Reviewer chose", "Times"], table_rows, right=[3]))

    with (
        card("memory"),
        st.expander("Memory: every lesson, with the option to forget", icon=":material/psychology:"),
    ):
        st.caption(
            "Lessons from the same vendor are shown to the AI on the next invoice; corrections count most. "
            "Tick *Forget?* to remove a lesson a reviewer got wrong."
        )
        memory_version = rows[0]["id"] if rows else 0
        memory = pd.DataFrame(rows)[
            ["id", "created_at", "vendor_name", "description", "suggested_gl", "final_gl", "final_cc", "outcome",
             "reviewer"]
        ]  # fmt: skip
        memory.insert(0, "forget", False)
        memory["created_at"] = memory["created_at"].str[:10]
        memory["outcome"] = memory["outcome"].map(
            {"accepted": "✓ confirmed", "corrected": "✎ corrected", "history": "⟲ ERP history"}
        )
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
        if selected and st.button(f"Forget {ui.plural(len(selected), 'lesson')}", icon=":material/delete_sweep:"):
            store.delete_feedback([int(i) for i in selected], actor=reviewer())
            notify(f"Forgot {ui.plural(len(selected), 'lesson')}.", ":material/delete_sweep:")
            st.rerun()


# --- Readers ------------------------------------------------------------------------------------------------------

READER_LABELS = {
    "fused": "Combined (what AP saw)", "rules": "OCR + rules", "ocr2": "OCR (second engine)",
    "ocr": "OCR (second engine)", "vlm": "Page reader", "template": "Supplier template", "ai": "AI model",
    "di": "Azure Document Intelligence",
}  # fmt: skip
_READER_ORDER = list(dict.fromkeys(READER_LABELS.values()))  # labels in the order the tab lists them


def reader_label(reader: str) -> str:
    return READER_LABELS.get(reader, reader)


def _added(rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], str]) -> dict[str, dict[str, Any]]:
    """Score rows that share ``key`` added together: the two kinds of second OCR read are one reader here."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        m = out.setdefault(key(r), {"readers": [], "fields": 0, "agreed": 0, "invoices": 0, "last_read": None,
                                    "last_final": None})  # fmt: skip
        if r.get("reader"):
            m["readers"].append(r["reader"])
        for count in ("fields", "agreed", "invoices"):
            m[count] += r[count]
        if m["last_read"] is None and r.get("last_read") is not None:
            m["last_read"], m["last_final"] = r["last_read"], r.get("last_final")
    for m in out.values():
        m["rate"] = m["agreed"] / m["fields"] if m["fields"] else 0.0
    return out


def _rate_pill(rate: float) -> str:
    return ui.pill(f"{rate:.1%}" if 0.995 <= rate < 1 else f"{rate:.0%}", "ok" if rate >= 0.95 else "warn")


def _readers(store) -> None:
    from ap_coder.capture.confidence import MIN_EVIDENCE

    st.caption(
        "Each approval compares what every reader found on the page with what AP approved. The combined value is "
        "what the review screen showed; a reader that keeps agreeing with AP earns its trust."
    )
    scores = _added(store.reader_scorecard(), lambda r: reader_label(r["reader"]))
    if not scores:
        with card("noreaders"):
            st.html(
                ui.empty_state(
                    "No reader compared with AP yet",
                    "Once invoices are approved on the review screen, this shows how often each reader (OCR and "
                    "the rules, the page reader, the supplier's template, the AI model) read what AP approved.",
                    ui.LEARNING_SVG,
                )
            )
    else:
        labels = sorted(scores, key=lambda label: (_READER_ORDER.index(label) if label in _READER_ORDER else 99, label))
        combined = scores.get(READER_LABELS["fused"])
        verified = next((s for s in store.fused_status_scorecard() if s["status"] == "verified"), None)
        tiles = []
        if combined:
            tiles.append(ui.tile("Combined value agreed with AP", f"{combined['rate']:.1%}", tone="green",
                                 hint=f"{ui.plural(combined['fields'], 'field')} on "
                                 f"{ui.plural(combined['invoices'], 'invoice')}"))  # fmt: skip
        tiles.append(
            ui.tile("Verified values right", f"{verified['rate']:.1%}", tone="green",
                    hint=f"{ui.plural(verified['fields'], 'value')} shown as verified")
            if verified
            else ui.tile("Verified values right", "—", hint="no value shown as verified yet")
        )  # fmt: skip
        st.html(ui.tiles(tiles))
        with card("readers_scorecard"):
            st.markdown("#### Scorecard")
            st.html(
                ui.table(
                    ["Reader", "Fields compared", "Agreed with AP", "% agreed", "Invoices"],
                    [[f"<b>{esc(label)}</b>" if label == READER_LABELS["fused"] else esc(label),
                      f"{scores[label]['fields']:,}", f"{scores[label]['agreed']:,}", _rate_pill(scores[label]["rate"]),
                      f"{scores[label]['invoices']:,}"] for label in labels],
                    right=[1, 2, 3, 4],
                )
            )  # fmt: skip
            st.caption(
                "OCR + rules: AP Coder's rules reading the page's text (from the PDF, or OCR for a scan). A field "
                "counts for a reader when it read a value there."
            )
        with card("readers_fields"):
            st.markdown("#### Field by field")
            pick = st.selectbox("Reader", labels, key="readers_pick")
            fields = _added(
                [row for reader in scores[pick]["readers"] for row in store.reader_field_scorecard(reader)],
                lambda r: r["field"],
            )
            rows = []
            for field, f in sorted(fields.items(), key=lambda kv: (kv[1]["rate"], kv[0])):
                latest = (
                    f"<span class='apc-muted'>read {esc(f['last_read'])} · AP approved {esc(f['last_final'])}</span>"
                    if f["last_read"] is not None
                    else ""
                )
                rows.append([esc(FIELD_LABELS.get(field, field)), f"{f['fields']:,}", f"{f['agreed']:,}",
                             _rate_pill(f["rate"]), latest])  # fmt: skip
            st.html(ui.table(["Field", "Compared", "Agreed", "% agreed", "Latest disagreement"], rows, right=[1, 2, 3],
                             wrap=[4]))  # fmt: skip
        patterns = sum(1 for n, _ in store.evidence_counts().values() if n >= MIN_EVIDENCE)
        st.caption(
            ":material/tune: "
            + (
                f"{ui.plural(patterns, 'evidence pattern')} now {'has' if patterns == 1 else 'have'} enough approvals "
                f"to set {'its' if patterns == 1 else 'their'} own confidence"
                if patterns
                else "No evidence pattern has enough approvals yet to set its own confidence"
            )
            + f" (each needs {MIN_EVIDENCE} approved values; until then the benchmark's measure is used)."
        )
    _training_card(store)


def _training_card(store) -> None:
    from ap_coder.training_export import export_training_set, training_invoices

    with card("training_export"):
        st.markdown("#### Export training data")
        picked = training_invoices(store)
        invoices = picked["invoices"]
        left_out = []  # approved invoices a training set never includes, and why
        if picked["demo"] or picked["unreviewed"]:
            left_out.append("Demo invoices and invoices approved without a person are never included.")
        if picked["bulk"]:
            n = picked["bulk"]
            left_out.append(
                f"{ui.plural(n, 'bulk-approved invoice')} {'is' if n == 1 else 'are'} left out: nobody opened "
                f"{'it' if n == 1 else 'them'}."
            )
        if not invoices:
            st.caption(
                "Each invoice AP approves becomes an example to fine-tune a vision model on your own suppliers' "
                "invoices: its pages, with the values AP approved. "
                + (" ".join(["None to include yet."] + left_out) if left_out else "Nothing approved yet.")
            )
            return
        missing = sum(1 for i in invoices if not Path(i["source_path"] or "").is_file())
        notes = [
            f"{ui.plural(len(invoices), 'approved invoice')}, each with its pages as images and the values AP "
            "approved: ready to fine-tune a vision model such as OvisOCR2 or Qwen 3.5 on your own suppliers' invoices."
        ]
        if missing:
            notes.append(f"{ui.plural(missing, 'invoice')} whose file is no longer on this computer will be left out.")
        st.caption(" ".join(notes + left_out))
        # Prepared for exactly these approvals: an invoice reopened, corrected and approved again (the count
        # unchanged) makes a new ZIP, not the one with the old values.
        made_for = hashlib.sha1(
            json.dumps(
                [(i["id"], i["approved_at"], i["final_output"]) for i in invoices], sort_keys=True, default=str
            ).encode()
        ).hexdigest()
        ready = st.session_state.get("training_zip")
        if ready and ready[0] == made_for:
            _, data, counts = ready
            st.download_button(
                "Download training data (ZIP)", data, file_name=f"ap-coder-training-{dt.date.today().isoformat()}.zip",
                mime="application/zip", type="primary", icon=":material/download:", key="training_zip_download",
                on_click=lambda: st.session_state.pop("training_zip", None),
            )  # fmt: skip
            st.caption(f"{ui.plural(counts['invoices'], 'invoice')}, {ui.plural(counts['pages'], 'page image')}.")
        elif st.button("Prepare training data", icon=":material/folder_zip:", key="training_zip_make"):
            with st.spinner("Rendering the pages…"):
                buf = io.BytesIO()
                counts = export_training_set(store, buf)
            st.session_state["training_zip"] = (made_for, buf.getvalue(), counts)
            st.rerun()
        st.caption(
            "Made on this computer and kept here unless someone copies it: it holds your suppliers' invoices. "
            "For a large set, `python -m ap_coder export-training` writes it straight to a file."
        )
