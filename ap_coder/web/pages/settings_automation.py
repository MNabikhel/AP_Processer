"""Settings → Automation: when an invoice may be approved without a person (touchless processing).

One switch for the company and one bar for every vendor: nothing here is set vendor by vendor. With the switch off
(the default) every invoice is approved by a person, and each approval keeps training its vendor. With it on, a vendor
whose reviewed invoices meet the bar goes touchless by itself, and one correction sends it back to review. Some
invoices always go to a person whatever the vendor's record. Turning the switch on or off needs "I understand" ticked
and is recorded in the Activity log with the reviewer's name (``Store.set_touchless``).
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from ap_coder.core import ui
from ap_coder.core.store import Store
from ap_coder.web.core import done, get_store, refuse, reviewer

router = APIRouter()

URL = "/settings/automation"
STATE_WORDS = {"autonomous": "Touchless", "ready": "Ready", "suspended": "Suspended", "supervised": "Learning",
               "learning": "Learning", "held": "Kept supervised"}  # fmt: skip


def supplier_counts(store: Store) -> dict[str, int]:
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


def typical_to_go(store: Store) -> str:
    """About how many invoices approved without a correction a new vendor needs: "20 to 30" with the standard bar
    (from 13 header fields an invoice down to 10); the same count each vendor's row shows on Supplier learning."""
    from ap_coder.capture.supplier import SupplierStats, clean_invoices_to_go

    policy = store.autonomy_policy()
    counts = []
    for fields in (13, 10):
        stats = SupplierStats(window_invoices=1, n=fields, correct=fields, invoices=1, clean_streak=1)
        to_go = clean_invoices_to_go(stats, policy)
        counts.append((to_go or 0) + 1)
    low, high = min(counts), max(counts)
    rounded = sorted({int(round(c, -1)) or c for c in (low, high)})
    return f"{rounded[0]} to {rounded[-1]}" if len(rounded) > 1 else f"{rounded[0]}"


def automation_context(store: Store) -> dict[str, Any]:
    from ap_coder.capture.supplier import ALWAYS_A_PERSON_RULES

    counts = supplier_counts(store)
    s = store.touchless_summary(30)
    errors = s["audit_errors"] + s["reopened"]
    limit = store.touchless_limit()
    limit_text = f"{ui.money(limit)} CAD"
    return {
        "touchless_on": store.touchless_enabled(),
        "touchless_limit": limit,
        "tiles": [
            ("Vendors skipping review", counts["Touchless"], "bolt", "green",
             f"{counts['Ready']} ready · {counts['Suspended']} suspended"),
            ("Vendors learning", counts["Learning"], "school", "blue", f"{counts['Kept supervised']} kept supervised"),
            ("Skipped review, last 30 days", f"{s['touchless_rate']:.0%}" if s["touchless_rate"] is not None else "—",
             "speed", "violet", f"{s['touchless']:,} of {ui.plural(s['processed'], 'invoice')} processed"),
            ("Errors found after skipping review", errors, "gpp_maybe", "amber" if errors else "green",
             f"{s['audit_errors']} of {ui.plural(s['audited'], 'audited invoice')}"
             + (f", {s['reopened']} reopened" if s["reopened"] else "")),
        ],
        "bar": store.autonomy_policy().plain_rules(),
        "typical": typical_to_go(store),
        "gates": [(a, b.replace("the largest amount approved without a person", limit_text))
                  for a, b in ALWAYS_A_PERSON_RULES],
    }  # fmt: skip


@router.post("/settings/automation/touchless")
def switch_touchless(request: Request, turn: str = Form(""), sure: str = Form("")) -> JSONResponse:
    """Turn touchless processing on or off: only with "I understand" ticked; logged with the reviewer's name."""
    store = get_store(request)
    if turn not in ("on", "off"):
        return refuse("Say whether to turn touchless processing on or off.")
    on = turn == "on"
    if on == store.touchless_enabled():
        return done(request, URL, f"Touchless processing is already {turn}.", tone="info")
    if sure not in ("1", "on", "true"):
        return refuse("Tick “I understand” first: nothing was changed.")
    changes = store.set_touchless(on, reviewer())
    if not on:
        return done(request, URL, "Touchless processing is off: every invoice is reviewed.")
    went = sum(1 for c in changes if c.get("to") == "autonomous")
    now = f"{ui.plural(went, 'vendor')} skipping review now" if went else "no vendor meets the bar yet"
    return done(request, URL, f"Touchless processing is on: {now}.")


@router.post("/settings/automation/limit")
def save_limit(request: Request, limit: str = Form("")) -> JSONResponse:
    """The largest invoice approved without a person, in CAD (logged by ``Store.set_touchless_limit``)."""
    try:
        amount = round(float(limit.replace(",", "").strip()), 2)
    except ValueError:
        return refuse("The limit must be an amount in CAD, e.g. 5000.")
    if not math.isfinite(amount):  # "1e309" or "nan" posted directly: no limit at all
        return refuse("The limit must be an amount in CAD, e.g. 5000.")
    if amount <= 0:
        return refuse("The limit must be above zero.")
    changed = get_store(request).set_touchless_limit(amount, reviewer())
    return done(request, URL, f"Saved: {ui.money(amount)} CAD." if changed else "Nothing changed.")
