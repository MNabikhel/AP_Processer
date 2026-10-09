"""Learning & accuracy: coding accuracy vs target, trend, vendors, corrections and the memory; and supplier
learning (header-field accuracy per supplier and its path to touchless processing)."""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from ap_coder import history, ui
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
    reference_or_none,
    reviewer,
    show_toast,
)

HISTORY_LABELS = {"vendor_name": "Vendor *", "description": "Line description *", "gl_code": "GL account *",
                  "cost_center": "Cost center", "amount": "Amount", "date": "Date"}  # fmt: skip


def _history_card(store) -> None:
    count = store.history_count()
    title = "Teach from past coding" + (f" ({count:,} past line(s) taught)" if count else "")
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
            elif st.button(f"Teach {len(df):,} line(s)", type="primary", icon=":material/school:", key="hist_go"):
                rows, skipped = history.rows_from_records(df.to_dict("records"), chosen)
                result = store.import_history(rows, actor=reviewer())
                notify(
                    f"Taught {result['added']:,} past line(s)"
                    + (f"; {result['skipped'] + skipped:,} skipped (incomplete or already taught)"
                       if result["skipped"] + skipped else "")
                    + (f"; {result['unknown_gl']:,} with a GL account that is not in your list" if result["unknown_gl"]
                       else "") + ".",
                    ":material/school:",
                )  # fmt: skip
                st.rerun()
        if count and st.button("Forget all past coding", icon=":material/delete_sweep:", key="hist_forget"):
            n = store.forget_history(actor=reviewer())
            notify(f"Forgot {n:,} past line(s).", ":material/delete_sweep:")
            st.rerun()


# --- Learning & accuracy ----------------------------------------------------------------------------------------


def page_learning() -> None:
    store = get_store()
    show_toast()
    st.html(
        ui.page_header("Insights", "Learning & accuracy", "How often the AI gets it right, and what it has learned.")
    )
    coding_tab, supplier_tab = st.tabs(
        [":material/auto_awesome: Coding accuracy", ":material/storefront: Supplier learning"]
    )
    with coding_tab:
        _history_card(store)
        _coding_accuracy(store)
    with supplier_tab:
        _supplier_learning(store)


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
                    st.caption(f"Over the last {stats.window_invoices} reviewed invoice(s).")
            can_turn_on = state == READY or (state == SUSPENDED and meets_policy(stats, policy))
            if can_turn_on:
                with actions.popover(
                    "Turn on autonomy…", icon=":material/bolt:", width="stretch", key=f"sup_menu_{slug}"
                ):
                    st.markdown(
                        f"Process **{name}** invoices without a person when every header field is verified and "
                        f"every check passes. The policy: {policy.describe()}."
                    )
                    if st.button("Yes, turn on autonomy", type="primary", key=f"sup_on_{slug}"):
                        try:
                            store.set_supplier_state(p["key"], AUTONOMOUS, reviewer())
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            notify(f"Autonomy is on for {name}.", ":material/bolt:")
                            st.rerun()
            if p["state"] in (AUTONOMOUS, SUSPENDED) and actions.button(
                "Turn off", icon=":material/pan_tool:", key=f"sup_off_{slug}", width="stretch"
            ):
                store.set_supplier_state(p["key"], SUPERVISED, reviewer(), reason="turned off on the Learning page")
                notify(f"{name} is back to supervised: every invoice is reviewed.", ":material/pan_tool:")
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
                    "AI coding accuracy",
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
        st.markdown("#### :material/show_chart: Accuracy by week")
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
        st.markdown("#### :material/history_edu: Recent lessons")
        items = []

        def short(text: str, n: int = 42) -> str:
            return text if len(text) <= n else text[: n - 1].rstrip() + "…"

        for r in reviewed[:6]:
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
        st.markdown("#### :material/storefront: Accuracy by vendor")
        st.html(
            "".join(
                ui.vendor_row(v["vendor_name"], v["lines"], v["accepted"] / v["lines"], v["corrected"])
                for v in m["by_vendor"][:12]
            )
        )
    with right, card("corrections"):
        st.markdown("#### :material/swap_horiz: Most common corrections")
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
        st.expander("The AI's memory: every lesson, with the option to forget", icon=":material/psychology:"),
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
        if selected and st.button(f"Forget {len(selected)} lesson(s)", icon=":material/delete_sweep:"):
            store.delete_feedback([int(i) for i in selected], actor=reviewer())
            notify(f"Forgot {len(selected)} lesson(s).", ":material/delete_sweep:")
            st.rerun()
