"""Activity: the audit trail of everything that changed data, filterable and exportable for controls."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from ap_coder import controls, dupaudit, ui
from ap_coder.audit import ACTIONS, describe
from ap_coder.safe import csv_cell
from ap_coder.webapp.common import card, esc, get_store, history_html, show_toast

GROUPS = {
    "Invoices": ["processed", "failed", "approved", "final_approved", "sent_back", "reopened", "parked", "unparked",
                 "note", "rejected", "deleted", "exported"],
    "Setup": ["accounts_imported", "accounts_edited", "accounts_deleted", "tax_setup_changed", "policy_changed",
              "settings_changed", "rules_changed", "vendor_updated", "vendors_imported", "pos_imported", "po_status",
              "pos_deleted", "export_undone", "erp_register_imported"],
    "Learning": ["lessons_forgotten", "history_imported"],
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
                ui.tile(
                    "Invoices approved",
                    len({e["invoice_id"] for e in approvals}),
                    "task_alt",
                    "green",
                    f"{len(approvals)} approval(s), {changed} with reviewer changes",
                ),  # fmt: skip
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
    _duplicate_audit_card(store)

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


def _duplicate_audit_card(store) -> None:
    with card("dupaudit"):
        head, button = st.columns([3, 1.3], vertical_alignment="center")
        head.markdown("#### :material/content_copy: Duplicate payment audit")
        head.caption(
            "Bills that may have been approved, or posted in the ERP, twice: number typos, the same bill under two "
            "vendor names, the same amount days apart. Includes the ERP invoice register when imported (Exports)."
        )
        if button.button("Run the audit", icon=":material/search:", width="stretch", key="dupaudit_run"):
            st.session_state["dupaudit"] = dupaudit.find(store)
        pairs = st.session_state.get("dupaudit")
        if pairs is None:
            return
        if not pairs:
            st.html(ui.pill("No likely duplicates found", "ok", "check"))
            return
        rows = [
            [
                ui.pill(dupaudit.REASONS[p.reason], "err" if p.reason == dupaudit.EXACT else "warn"),
                f"<b>{esc(p.first.vendor_name)}</b><div class='apc-muted'>{esc(p.first.label)}</div>",
                f"<b>{esc(p.second.vendor_name)}</b><div class='apc-muted'>{esc(p.second.label)}</div>",
                f"{p.amount:,.2f} <span class='apc-muted'>{esc(p.first.currency or p.second.currency)}</span>",
            ]
            for p in pairs[:100]
        ]
        st.html(ui.table(["Why", "Bill", "Possible duplicate", "Amount"], rows, right=[3], wrap=[1, 2]))
        if len(pairs) > 100:
            st.caption(f"The first 100 of {len(pairs)}: download the CSV for all.")
        st.download_button(
            "Download (CSV)", dupaudit.to_csv(pairs), file_name=f"duplicate_audit_{dt.date.today()}.csv",
            mime="text/csv", icon=":material/download:", key="dupaudit_download",
        )  # fmt: skip
        st.caption(
            "Check each pair against the documents: a real duplicate that was paid is recovered from the vendor "
            "as a credit or refund."
        )


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
                        f"{len(r['sent_back'])} sent back or reopened · {len(r['waiting'])} waiting",
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
