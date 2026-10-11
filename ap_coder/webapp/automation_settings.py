"""Settings → Automation: when an invoice may be approved without a person (touchless processing).

One switch for the company and one bar for every vendor: nothing here is set vendor by vendor. With the switch off
(the default) every invoice is approved by a person, and each approval keeps training its vendor. With it on, a vendor
whose reviewed invoices meet the bar goes touchless by itself, and one correction sends it back to review. Some
invoices always go to a person whatever the vendor's record (a changed bank account, a possible duplicate, a total
over the limit set here...). A manager can still keep one vendor supervised on Learning & accuracy.
"""

from __future__ import annotations

import streamlit as st

from ap_coder import ui
from ap_coder.webapp.common import PUBLIC_DEMO, card, esc, not_in_public_demo, notify, reviewer

STATE_WORDS = {"autonomous": "Touchless", "ready": "Ready", "suspended": "Suspended", "supervised": "Learning",
               "learning": "Learning", "held": "Kept supervised"}  # fmt: skip


def _supplier_counts(store) -> dict[str, int]:
    """How many vendors are in each state, as the Learning page shows them."""
    from ap_coder.capture.supplier import SUPERVISED, autonomy_status

    policy, on = store.autonomy_policy(), store.touchless_enabled()
    counts = {word: 0 for word in dict.fromkeys(STATE_WORDS.values())}
    for p in store.list_supplier_profiles():
        stats = store.supplier_stats(p["key"], policy.window)
        state, _, _ = autonomy_status(stats, policy, p["state"] or SUPERVISED, p["autonomous_since"],
                                      touchless_on=on, suspended_at=p.get("suspended_at"))  # fmt: skip
        counts[STATE_WORDS.get(state, "Learning")] += 1
    return counts


def _switch_card(store) -> None:
    on = store.touchless_enabled()
    with card("automation_switch"):
        st.markdown("#### Touchless processing")
        if on:
            st.html(ui.status("On: a vendor that meets the bar below is approved without a person", "ok"))
        else:
            st.html(ui.status("Off: every invoice is reviewed by a person", "gray"))
        st.caption(
            "The same rules for every vendor. Each invoice a clerk reviews trains its vendor; once a vendor meets the "
            "bar it goes touchless by itself, and one correction (or a touchless invoice reopened) sends it back to "
            "review until it has a fresh clean streak. A vendor can be kept supervised on Learning & accuracy."
        )
        if PUBLIC_DEMO:
            not_in_public_demo("Touchless processing")
            return
        label = "Turn off touchless processing…" if on else "Turn on touchless processing…"
        with st.popover(label, icon=":material/pan_tool:" if on else ":material/bolt:", key="touchless_menu"):
            if on:
                st.markdown("Every invoice will be reviewed by a person again. Vendors keep their record.")
            else:
                st.markdown(
                    "Invoices from vendors that meet the bar will be approved **without a person** when every field "
                    "is verified, every check passes and nothing below needs a person. Recorded in the Activity log "
                    "with your name."
                )
            sure = st.checkbox("I understand", key="touchless_sure")
            if st.button("Turn off" if on else "Turn on", type="primary", disabled=not sure, key="touchless_go"):
                changes = store.set_touchless(not on, reviewer())
                went = sum(1 for c in changes if c["to"] == "autonomous")
                if on:
                    notify("Touchless processing is off: every invoice is reviewed.", ":material/pan_tool:")
                else:
                    now = f"{ui.plural(went, 'vendor')} touchless now" if went else "no vendor meets the bar yet"
                    notify(f"Touchless processing is on: {now}.", ":material/bolt:")
                st.rerun()


def _limit_card(store) -> None:
    with card("automation_limit"), st.form("touchless_limit_form", border=False):
        st.markdown("#### Largest invoice approved without a person")
        c1, _ = st.columns(2)
        limit = c1.number_input(
            "Amount, in CAD", min_value=0.01, step=500.0, value=store.touchless_limit(), format="%.2f",
            help="A larger invoice always goes to a person. A foreign-currency total is converted to CAD at the "
            "exchange rates in Settings → Review; one in a currency with no rate set always goes to a person.",
        )  # fmt: skip
        if st.form_submit_button("Save the limit", icon=":material/save:", disabled=PUBLIC_DEMO):
            changed = store.set_touchless_limit(limit, reviewer())
            notify(f"Saved: {ui.money(limit)} CAD." if changed else "Nothing changed.", ":material/save:")
            st.rerun()


def _bar_card(store) -> None:
    from ap_coder.capture.supplier import ALWAYS_A_PERSON_RULES

    policy = store.autonomy_policy()
    left, right = st.columns(2, gap="medium")
    with left, card("automation_bar"):
        st.markdown("#### The bar every vendor must meet")
        st.html(ui.table(["Rule", "Standard"], [[esc(a), esc(b)] for a, b in policy.plain_rules()]))
        st.caption("Fixed standard values, the same for every vendor: they cannot be changed here.")
    with right, card("automation_gates"):
        st.markdown("#### Always a person")
        limit = f"{ui.money(store.touchless_limit())} CAD"
        rows = [[esc(a), esc(b.replace("the largest amount approved without a person", limit))]
                for a, b in ALWAYS_A_PERSON_RULES]  # fmt: skip
        st.html(ui.table(["Invoice", "Why"], rows))


def _summary_card(store) -> None:
    counts = _supplier_counts(store)
    s = store.touchless_summary(30)
    rate = f"{s['touchless_rate']:.0%}" if s["touchless_rate"] is not None else "—"
    errors = s["audit_errors"] + s["reopened"]
    with card("automation_summary"):
        st.markdown("#### Right now")
        st.html(
            ui.tiles(
                [
                    ui.tile("Vendors touchless", counts["Touchless"], "bolt", "green",
                            f"{counts['Ready']} ready · {counts['Suspended']} suspended"),
                    ui.tile("Vendors learning", counts["Learning"], "school", "blue",
                            f"{counts['Kept supervised']} kept supervised"),
                    ui.tile("Touchless, last 30 days", rate, "speed", "violet",
                            f"{s['touchless']:,} of {ui.plural(s['processed'], 'invoice')} processed"),
                    ui.tile("Errors found in touchless invoices", errors, "gpp_maybe", "amber" if errors else "green",
                            f"{s['audit_errors']} of {ui.plural(s['audited'], 'audited invoice')}"
                            + (f", {s['reopened']} reopened" if s["reopened"] else "")),
                ]
            )
        )  # fmt: skip
        st.caption(
            "Audited: a share of the invoices that would have gone touchless, checked by a person. Any error a person "
            "finds there, or in a touchless invoice they reopen, suspends that vendor."
        )


def automation_tab(store) -> None:
    """The touchless switch, the largest amount approved without a person, and the fixed bar each vendor must meet."""
    _switch_card(store)
    _summary_card(store)
    _limit_card(store)
    _bar_card(store)
