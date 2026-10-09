"""Vendors: every supplier seen on an invoice, its spend and AI accuracy, and AP's controls (hold, GST #)."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import streamlit as st

from ap_coder import recurring, ui
from ap_coder.help import help_for
from ap_coder.insights import vendor_workload
from ap_coder.safe import md
from ap_coder.store import Store
from ap_coder.vendors import ACTIVE, ON_HOLD, master_columns, master_rows
from ap_coder.webapp.accounts import _read_upload
from ap_coder.webapp.common import (
    card,
    esc,
    get_store,
    gl_name,
    money,
    notify,
    page_head,
    reference_or_none,
    reviewer,
    show_toast,
)

SORTS = ("Spend: high to low", "Most invoices", "Newest first", "Lowest AI accuracy", "Name A–Z")


def _slug(key: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", key.lower()).strip("_") or "vendor"


def _accuracy_cell(value: float | None) -> str:
    if value is None:
        return "<span class='apc-muted'>—</span>"
    color = ui.confidence_color(value, 0.9)
    return f"<b style='color:{color}'>{value:.0%}</b>"


def _sorted(rows: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    if order == "Most invoices":
        return sorted(rows, key=lambda r: -r["invoices"])
    if order == "Newest first":
        return sorted(rows, key=lambda r: r["first_seen"] or "", reverse=True)
    if order == "Lowest AI accuracy":
        return sorted(rows, key=lambda r: (r["accuracy"] is None, r["accuracy"] or 0))
    if order == "Name A–Z":
        return sorted(rows, key=lambda r: (r["vendor_name"] or "").lower())
    return sorted(rows, key=lambda r: -(r["spend_cad"] or 0))


def page_vendors() -> None:
    store = get_store()
    show_toast()
    page_head(
        "vendors", "Vendors", "Everyone who has sent an invoice: spend, how well the AI codes them, and your controls."
    )
    _master_importer(store)
    vendors = store.vendor_summaries()
    if not vendors:
        with card("vendors_empty"):
            st.html(ui.empty_state("No vendors yet", "Vendors appear here once invoices have been processed."))
        return

    month_ago = (dt.datetime.now() - dt.timedelta(days=30)).isoformat()
    new = [v for v in vendors if (v["first_seen"] or "") >= month_ago]
    on_hold = [v for v in vendors if v["status"] == ON_HOLD]
    top = vendors[0]
    st.html(
        ui.tiles(
            [
                ui.tile(
                    "Vendors", len(vendors), "storefront", "blue", f"{sum(v['invoices'] for v in vendors)} invoices"
                ),
                ui.tile("New this month", len(new), "fiber_new", "green", "first invoice in 30 days"),
                ui.tile(
                    "On hold",
                    len(on_hold),
                    "front_hand",
                    "amber" if on_hold else "violet",
                    "invoices from them can't be approved without an override",
                ),  # fmt: skip
                ui.tile(
                    "Top spend",
                    money(top["spend_cad"]) if top["spend_cad"] else "—",
                    "payments",
                    "violet",
                    (top["vendor_name"] or "") if top["spend_cad"] else "no approved spend yet",
                ),
            ]
        )
    )

    with card("vendor_list"):
        c1, c2, c3 = st.columns([2.4, 2, 1.6], vertical_alignment="center")
        view = c1.segmented_control(
            "Show", ["All", "On hold", "New"], default="All", key="vendor_view", label_visibility="collapsed",
            persist_state="session",
        ) or "All"  # fmt: skip
        query = c2.text_input(
            "Search", placeholder="Search vendors", key="vendor_query", label_visibility="collapsed",
            icon=":material/search:", persist_state="session",
        )  # fmt: skip
        order = c3.selectbox("Sort", SORTS, key="vendor_sort", label_visibility="collapsed", persist_state="session")
        shown = on_hold if view == "On hold" else new if view == "New" else vendors
        if query.strip():
            shown = [v for v in shown if query.strip().lower() in (v["vendor_name"] or "").lower()]
        shown = _sorted(shown, order)
        has_master = store.has_vendor_master()
        rows = [
            [
                f"<div style='display:flex;gap:.6rem;align-items:center'>{ui.avatar(v['vendor_name'] or '', 'sm')}"
                f"<b>{esc(v['vendor_name'])}</b></div>",
                f"{v['approved']} / {v['invoices']}",
                money(v["spend_cad"]),
                esc(v["last_invoice"] or "—"),
                _accuracy_cell(v["accuracy"]),
                (ui.pill("On hold", "warn", "front_hand") if v["status"] == ON_HOLD else ui.pill("Active", "ok"))
                + (" " + ui.pill("Not in master", "warn", "gpp_maybe") if has_master and not v["in_master"] else ""),
            ]
            for v in shown
        ]
        if rows:
            st.html(
                ui.table(
                    ["Vendor", "Approved / all", "Spend (CAD)", "Last invoice", "AI accuracy", ""],
                    rows,
                    right=[1, 2, 4],
                    wrap=[0, 5],
                )  # fmt: skip
            )
        else:
            st.caption("No vendors match.")

    # Open one vendor right under the list; the recurring and workload overviews follow.
    names = {v["vendor_key"]: v["vendor_name"] for v in shown or vendors}
    with card("vendor_pick"):
        chosen = st.selectbox(
            "Open a vendor", list(names), format_func=lambda k: names[k], key="vendor_open", index=None,
            placeholder="Choose a vendor to see its invoices, GL accounts and controls",
        )  # fmt: skip
    if chosen:
        vendor_detail(store, next(v for v in vendors if v["vendor_key"] == chosen))

    _recurring_card(store)
    _workload_card(store)


MASTER_LABELS = {"vendor_name": "Vendor name *", "erp_id": "Vendor ID", "gst": "GST/HST number",
                 "terms": "Payment terms", "status": "Status", "default_gl": "Default GL account"}  # fmt: skip


def _master_importer(store: Store) -> None:
    has_master = store.has_vendor_master()
    title = "Vendor master from the ERP" + (" (imported)" if has_master else "")
    with st.expander(title, icon=":material/upload:"):
        st.caption(
            "Import the ERP's vendor list (CSV or Excel). Exports then carry each vendor's ERP ID, invoices from "
            "a vendor that is not in the list are flagged, the vendor's payment terms set the due date when the "
            "invoice shows none, and its default GL account is offered for uncoded lines. Import again any time: "
            "vendors are matched by name."
        )
        upload = st.file_uploader("Vendor list (CSV or Excel)", type=["csv", "xlsx"], key="vm_upload")
        df = _read_upload(upload, "vm") if upload is not None else None
        if df is None or df.empty:
            return
        st.dataframe(df.head(5), hide_index=True)
        columns = list(df.columns)
        guessed = master_columns(columns)
        options = ["(none)", *columns]
        cols = st.columns(3)
        chosen: dict[str, str] = {}
        for i, (field, label) in enumerate(MASTER_LABELS.items()):
            pick = cols[i % 3].selectbox(label, options, index=options.index(guessed[field]) if field in guessed else 0,
                                         key=f"vm_col_{field}")  # fmt: skip
            if pick != "(none)":
                chosen[field] = pick
        if "vendor_name" not in chosen:
            st.warning("Pick the column with the vendor name.")
            return
        if st.button("Import vendors", type="primary", icon=":material/upload:", key="vm_import"):
            rows = master_rows(df.to_dict("records"), chosen)
            result = store.import_vendor_master(rows, actor=reviewer())
            notify(f"Vendor master: {result['added']} added, {result['updated']} updated.", ":material/storefront:")
            if result["duplicates"]:
                st.session_state["vm_duplicates"] = result["duplicates"]
            st.rerun()
        dupes = st.session_state.get("vm_duplicates")
        if dupes:
            st.warning(
                f"{ui.plural(len(dupes), 'vendor')} appear more than once in the file under similar names (merged "
                "here; one on hold keeps the vendor on hold). Often the same supplier set up twice in the ERP: "
                + "; ".join(" / ".join(n) for n in dupes[:8])
            )


def _workload_card(store: Store) -> None:
    rows = vendor_workload(store)
    if not rows:
        return
    with card("workload"):
        st.markdown("#### Vendors that make work")
        st.caption(
            "How often a vendor's invoice arrived with something only the vendor can fix, and how often AP "
            "corrected the coding. Ask the worst ones for better invoices (*Ask the vendor* on an invoice drafts "
            "the email); a fixed coding rule can settle what AP keeps correcting."
        )
        table = []
        for v in rows[:10]:
            problems = ", ".join(esc(help_for(c).title if help_for(c) else c) for c in v["top_codes"]) or "—"
            table.append(
                [
                    f"<b>{esc(v['vendor_name'])}</b>",
                    str(v["invoices"]),
                    f"{v['with_problems']} <span class='apc-muted'>({v['problem_rate']:.0%})</span>",
                    problems,
                    f"{v['corrected']} of {v['approved']}" if v["approved"] else "—",
                ]
            )
        st.html(
            ui.table(
                ["Vendor", "Invoices", "With a problem", "Usual problems", "Coding corrected"],
                table,
                right=[1, 2, 4],
                wrap=[0, 3],
            )  # fmt: skip
        )


def _recurring_card(store: Store) -> None:
    found = recurring.detect(store.vendor_invoice_dates())
    with card("recurring"):
        st.markdown("#### Recurring invoices")
        if not found:
            st.html(
                ui.empty_note(
                    "No regular billers yet",
                    "Vendors who bill on a regular rhythm (weekly to quarterly, at least three times) appear here "
                    "with their next expected invoice, so a missing one is noticed before it is paid late.",
                    "event_repeat",
                )
            )
            return
        late = [r for r in found if r.status == recurring.LATE]
        if late:
            st.caption(
                f"{ui.plural(len(late), 'expected invoice')} not received, usually about "
                f"{money(sum(r.typical_total for r in late))} in total: chase the vendor, and consider accruing "
                "them at month-end."
            )
        pills = {
            recurring.LATE: lambda r: ui.pill(f"{r.days_late} days late", "warn", "schedule"),
            recurring.DUE_SOON: lambda r: ui.pill("Due soon", "info", "event"),
            recurring.ON_TRACK: lambda r: ui.pill("On track", "ok", "check"),
        }
        rows = [
            [
                f"<b>{esc(r.vendor_name)}</b>",
                esc(r.cadence),
                esc(r.last_date.isoformat()),
                esc(r.next_date.isoformat()),
                f"{money(r.typical_total)} <span class='apc-muted'>{esc(r.currency)}</span>",
                pills[r.status](r),
            ]
            for r in found
        ]
        st.html(ui.table(["Vendor", "Bills", "Last invoice", "Next expected", "Usual amount", ""], rows, right=[4]))


def _master_line(store: Store, v: dict[str, Any]) -> str:
    if v.get("in_master"):
        parts = [f"ERP ID <b>{esc(v['erp_id'] or '—')}</b>"]
        if v.get("terms"):
            parts.append(f"terms {esc(v['terms'])}")
        if v.get("default_gl"):
            parts.append(f"default GL {esc(v['default_gl'])}")
        return f"<div class='apc-muted'>{ui.pill('In the vendor master', 'ok', 'verified')} {' · '.join(parts)}</div>"
    if store.has_vendor_master():
        return f"<div style='margin-top:.2rem'>{ui.pill('Not in the vendor master', 'warn', 'gpp_maybe')}</div>"
    return ""


def vendor_detail(store: Store, v: dict[str, Any]) -> None:
    key, name = v["vendor_key"], v["vendor_name"] or ""
    invoices = store.vendor_invoices(key)
    reference = reference_or_none(store)
    with card(f"vendor_{_slug(key)}"):
        status = ui.pill("On hold", "warn", "front_hand") if v["status"] == ON_HOLD else ui.pill("Active", "ok")
        st.html(
            f"<div style='display:flex;gap:.9rem;align-items:center;margin-bottom:.4rem'>{ui.avatar(name)}"
            f"<div><div style='font-weight:750;font-size:1.15rem'>{esc(name)} {status}</div>"
            f"<div class='apc-muted'>First invoice {esc(v['first_invoice'] or '—')} · "
            f"last {esc(v['last_invoice'] or '—')}"
            f" · {ui.plural(v['lessons'], 'lesson')} learned</div>{_master_line(store, v)}</div></div>"
        )
        numbers = sorted({i["gst_hst_number"] for i in invoices if i["gst_hst_number"]})
        left, right = st.columns(2, gap="medium")
        with left:
            st.markdown("**GL accounts reviewers used**")
            usage = store.vendor_gl_usage(key)
            if usage:
                st.html(
                    ui.table(
                        ["GL", "Account", "Lines", "Corrected"],
                        [
                            [
                                f"<b>{esc(u['gl_code'])}</b>",
                                esc(gl_name(reference, u["gl_code"]) if reference else ""),
                                str(u["lines"]),
                                str(u["corrected"]),
                            ]  # fmt: skip
                            for u in usage[:8]
                        ],
                        right=[2, 3],
                    )
                )
            else:
                st.caption("Nothing approved yet for this vendor.")
            st.markdown("**GST/HST numbers seen**")
            if numbers:
                warn = len(numbers) > 1
                st.html(
                    " ".join(ui.pill(n, "warn" if warn else "gray", "verified") for n in numbers)
                    + (
                        "<div class='apc-muted' style='margin-top:.3rem'>More than one number: confirm which is "
                        "right and record it below.</div>"
                        if warn
                        else ""
                    )  # fmt: skip
                )
            else:
                st.caption("None on file.")
        with right:
            st.markdown("**Recent invoices**")
            st.html(
                ui.table(
                    ["#", "Invoice", "Date", "Total", "Status"],
                    [
                        [
                            str(i["id"]),
                            esc(i["invoice_number"]),
                            esc(i["invoice_date"]),
                            f"{money(i['grand_total'])} <span class='apc-muted'>{esc(i['currency'])}</span>",
                            ui.pill(*ui.STATUS_PILLS.get(i["status"], (i["status"], "gray", ""))),
                        ]  # fmt: skip
                        for i in invoices[:8]
                    ],
                    right=[3],
                )
            )

        st.markdown("**Controls**")
        with st.form(f"vendor_form_{_slug(key)}", border=False):
            c1, c2 = st.columns([1, 2])
            status_choice = c1.radio(
                "Status", [ACTIVE, ON_HOLD], index=1 if v["status"] == ON_HOLD else 0, horizontal=True,
                format_func=lambda s: "Active" if s == ACTIVE else "On hold",
                help="On hold: every new invoice from this vendor gets an error and needs an override to approve.",
            )  # fmt: skip
            expected = c2.text_input(
                "Expected GST/HST number", v["expected_gst"], placeholder="e.g. 123456789RT0001",
                help="Invoices showing a different number are flagged (a common sign of a fake invoice).",
            )  # fmt: skip
            notes = st.text_area(
                "Notes", v["notes"], placeholder="e.g. bank details changed 2026-09, verified by phone"
            )
            if st.form_submit_button("Save vendor", type="primary", icon=":material/save:"):
                store.save_vendor(key, name, status_choice, expected, notes, actor=reviewer())
                notify(f"Saved {md(name.rstrip('.'))}.", ":material/storefront:")
                st.rerun()
