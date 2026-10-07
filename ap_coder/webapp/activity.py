"""Activity: the audit trail of everything that changed data, filterable and exportable for controls."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from ap_coder import controls, ui
from ap_coder.audit import ACTIONS, describe
from ap_coder.safe import csv_cell
from ap_coder.webapp.common import card, get_store, history_html, show_toast

GROUPS = {
    "Invoices": ["processed", "failed", "approved", "final_approved", "sent_back", "rejected", "deleted", "exported"],
    "Setup": ["accounts_imported", "accounts_edited", "accounts_deleted", "tax_setup_changed", "policy_changed",
              "settings_changed", "vendor_updated", "pos_imported", "po_status", "pos_deleted", "export_undone"],
    "Learning": ["lessons_forgotten"],
    "Backups": ["backup_made", "backup_restored"],
}  # fmt: skip


def page_activity() -> None:
    store = get_store()
    show_toast()
    st.html(
        ui.page_header(
            "Controls",
            "Activity",
            "Who processed, changed, approved, rejected, deleted or exported what, and when.",
        )
    )
    events = store.events(limit=5000)
    if not events:
        with card("activity_empty"):
            st.html(ui.empty_state("No activity yet", "Everything that changes data is recorded here."))
        return

    today = dt.date.today()
    week = sum(1 for e in events if e["created_at"][:10] >= (today - dt.timedelta(days=7)).isoformat())
    approvals = [e for e in events if e["action"] == "approved"]
    changed = sum(1 for e in approvals if (e["detail"] or {}).get("changes"))
    people = sorted({e["actor"] for e in events if e["actor"]})
    st.html(
        ui.tiles(
            [
                ui.tile("Events", len(events), "history", "blue", f"{week} in the last 7 days"),
                ui.tile("Approvals", len(approvals), "task_alt", "green", f"{changed} with reviewer changes"),
                ui.tile("People", len(people), "group", "violet", ", ".join(people[:3]) or "—"),
                ui.tile(
                    "Deletions",
                    sum(1 for e in events if e["action"] in ("deleted", "accounts_deleted", "lessons_forgotten")),
                    "delete",
                    "amber",
                    "invoices, accounts, lessons",
                ),
            ]
        )
    )

    _controls_card(store)

    with card("activity_filters"):
        c1, c2, c3 = st.columns([2.8, 1.2, 1.8])
        group = c1.segmented_control(
            "Show", ["All", *GROUPS], default="All", key="activity_group", persist_state="session"
        ) or "All"  # fmt: skip
        person = c2.selectbox("Person", ["Everyone", *people], key="activity_person", persist_state="session")
        query = c3.text_input(
            "Search", placeholder="vendor, invoice #, GL code…", key="activity_query", persist_state="session"
        )
    shown = [
        e
        for e in events
        if (group == "All" or e["action"] in GROUPS[group])
        and (person == "Everyone" or e["actor"] == person)
        and (not query.strip() or query.strip().lower() in _searchable(e))
    ]

    table = pd.DataFrame(
        [
            {
                "When": e["created_at"].replace("T", " ")[:19],
                "Who": e["actor"] or "",
                "What": ACTIONS.get(e["action"], ("", "", e["action"]))[2],
                "Invoice": e["invoice_id"] or "",
                "Details": describe(e),
            }
            for e in shown
        ]
    )
    head, download = st.columns([3, 1], vertical_alignment="center")
    head.caption(f"{len(shown)} of {len(events)} event(s)")
    download.download_button(
        "Download CSV", table.map(csv_cell).to_csv(index=False).encode("utf-8-sig"),
        file_name=f"ap_coder_activity_{today}.csv", mime="text/csv", icon=":material/download:", width="stretch",
    )  # fmt: skip
    timeline, grid = st.tabs([":material/timeline: Timeline", ":material/table: Table"])
    with timeline, card("activity_timeline"):
        st.html(history_html(shown[:200]))
        if len(shown) > 200:
            st.caption("Showing the newest 200; use the table or the CSV for everything.")
    with grid:
        st.dataframe(table, hide_index=True, width="stretch")


def _controls_card(store) -> None:
    with card("controls"):
        head, pick = st.columns([3, 2], vertical_alignment="center")
        head.markdown("#### :material/verified_user: Controls report")
        head.caption(
            "Exceptions for internal audit: overridden errors, risky approvals, second approvals, setup changes."
        )
        today = dt.date.today()
        period = pick.date_input(
            "Period", (today.replace(day=1), today), max_value=today, key="controls_period",
            label_visibility="collapsed",
        )  # fmt: skip
        if not isinstance(period, tuple) or len(period) != 2:
            st.caption("Pick the first and last day.")
            return
        r = controls.build(store, period[0], period[1])
        st.html(
            ui.tiles(
                [
                    ui.tile(
                        "Errors overridden",
                        len(r["overrides"]),
                        "gpp_maybe",
                        "amber" if r["overrides"] else "green",
                        "approved despite an error",
                    ),
                    ui.tile(
                        "Risk signals approved",
                        len(r["signals"]),
                        "report",
                        "amber" if r["signals"] else "green",
                        "duplicates, vendor, PO",
                    ),
                    ui.tile(
                        "Second approvals",
                        len(r["finals"]),
                        "how_to_reg",
                        "violet",
                        f"{len(r['sent_back'])} sent back · {len(r['waiting'])} waiting",
                    ),
                    ui.tile("Setup changes", len(r["setup"]), "tune", "blue", "accounts, tax, vendors, POs"),
                ]
            )  # fmt: skip
        )
        st.download_button(
            "Download the controls report", controls.report_html(r).encode("utf-8"),
            file_name=f"ap_coder_controls_{period[0]}_{period[1]}.html", mime="text/html",
            icon=":material/download:", key="controls_download",
        )  # fmt: skip


def _searchable(event: dict) -> str:
    detail = event["detail"] or {}
    return " ".join([describe(event), str(detail.get("vendor") or ""), str(detail.get("invoice_number") or ""),
                     str(event["invoice_id"] or ""), event["actor"] or ""]).lower()  # fmt: skip
