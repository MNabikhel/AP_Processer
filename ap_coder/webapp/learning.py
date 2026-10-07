"""Learning & accuracy: accuracy vs target, trend, vendors, corrections and the memory."""

from __future__ import annotations

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
    import altair as alt

    store = get_store()
    show_toast()
    st.html(
        ui.page_header("Insights", "Learning & accuracy", "How often the AI gets it right, and what it has learned.")
    )
    _history_card(store)
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
