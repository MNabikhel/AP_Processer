"""Find an invoice: where an invoice stands, for a vendor on the phone."""

from __future__ import annotations

import streamlit as st

from ap_coder import search, stamp, ui
from ap_coder.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW
from ap_coder.webapp.common import (
    PAGES,
    card,
    esc,
    get_store,
    money,
    page_head,
    reference_or_none,
    reviewer,
    show_toast,
)

STATUS = {
    REVIEW: ("To review", "info", "inbox"),
    PARKED: ("Parked", "warn", "pause_circle"),
    PENDING: ("Second approval", "violet", "how_to_reg"),
    APPROVED: ("Approved", "ok", "task_alt"),
    REJECTED: ("Rejected", "err", "block"),
    FAILED: ("Unreadable", "err", "error"),
}


def page_search() -> None:
    store = get_store()
    show_toast()
    page_head(
        "search",
        "Find an invoice",
        "Vendor on the phone? Search by vendor, invoice number, PO or amount: see where it is and when it is due.",
    )
    with card("search_box"):
        query = st.text_input(
            "Search", placeholder="e.g. northwind 0912 · 18,017.85 · PO-88213", key="search_query",
            label_visibility="collapsed", icon=":material/search:",
        )  # fmt: skip
        st.caption("Words match the vendor name; numbers match the invoice number (however written), the PO or the "
                   "amount (dates are not searched). Use both to narrow it down.")  # fmt: skip
    if not query.strip():
        return
    hits = search.find(store, query)
    if not hits:
        with card("search_none"):
            st.html(
                ui.empty_state(
                    "Not found",
                    "Not in AP Coder or the ERP register. Ask the vendor to send it (or check it was not sent to "
                    "someone else).",
                )
            )
        return
    for n, hit in enumerate(hits):
        r = hit.row
        with card(f"search_hit_{n}"):
            left, right = st.columns([4, 1.2], vertical_alignment="center")
            if hit.source == "ERP":
                pill = ui.pill("In the ERP register", "gray", "database")
                where = "Found in the ERP's invoice register (imported on the Exports page), not in AP Coder."
            else:
                label, tone, icon = STATUS.get(r["status"], (r["status"], "gray", "help"))
                pill = ui.pill(label, tone, icon)
                where = search.where(r)
            due = (
                f" · due {esc(r['due_date'])}"
                if r.get("due_date") and r.get("status") not in (REJECTED, FAILED) and (r.get("grand_total") or 0) > 0
                else ""
            )
            left.html(
                f"<div style='display:flex;gap:.5rem;align-items:center;flex-wrap:wrap'><b>{esc(r['vendor_name'])}"
                f"</b>{pill}</div><div class='apc-muted'>Invoice {esc(r.get('invoice_number') or '—')} · "
                f"{esc(r.get('invoice_date') or 'no date')}{due} · "
                f"{money(r.get('grand_total') or 0)} {esc(r.get('currency') or '')}</div>"
                f"<div style='margin-top:.3rem'>{esc(where)}</div>"
            )
            if hit.source == "AP Coder" and r["status"] == REVIEW and "review" in PAGES:
                if right.button("Open", icon=":material/open_in_new:", key=f"search_open_{r['id']}", width="stretch"):
                    st.session_state["open_invoice"] = r["id"]
                    st.switch_page(PAGES["review"])
            elif hit.source == "AP Coder" and r["status"] in (APPROVED, PENDING):
                # Made again if the invoice changed since (reopened, approved again, exported).
                version = (r.get("reviewed_at"), r.get("second_reviewed_at"), r.get("export_batch"))
                ready = st.session_state.get(f"search_pdf_{r['id']}")
                if ready and ready[2] == version:
                    right.download_button(
                        "Download PDF", ready[1], file_name=ready[0], mime="application/pdf",
                        icon=":material/download:", key=f"search_pdf_dl_{r['id']}", width="stretch",
                    )  # fmt: skip
                elif right.button(
                    "Approved PDF", icon=":material/approval:", key=f"search_pdf_make_{r['id']}", width="stretch",
                    help="The invoice with its APPROVED stamp and coding page",
                ):  # fmt: skip
                    inv = store.get_invoice(r["id"])
                    reference = reference_or_none(store)
                    names = {row["gl_code"]: row.get("description", "") for row in reference.chart_of_accounts.rows} \
                        if reference else {}  # fmt: skip
                    pdf = stamp.stamped_pdf(inv, names)
                    st.session_state[f"search_pdf_{r['id']}"] = (stamp.file_name(inv), pdf, version)
                    st.rerun()
            elif hit.source == "AP Coder" and r["status"] == PARKED and "review" in PAGES:
                if right.button("Bring back", icon=":material/play_circle:", key=f"search_unpark_{r['id']}",
                                width="stretch", help="Back to the review queue, and open it"):  # fmt: skip
                    store.unpark_invoice(r["id"], reviewer())
                    st.session_state["open_invoice"] = r["id"]
                    st.switch_page(PAGES["review"])
    if len(hits) == search.LIMIT:
        st.caption(f"The best {search.LIMIT} matches: add words or numbers to narrow it down.")
