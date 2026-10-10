"""Insights: straight-through rate, time saved and Azure cost from AP Coder's own records."""

import copy

from ap_coder.demo import load_demo
from ap_coder.insights import Assumptions, compute, invoice_cost_usd, report_html
from ap_coder.store import Store


def test_cost_of_one_invoice():
    a = Assumptions()
    usage = {"prompt_tokens": 4000, "cached_prompt_tokens": 2000, "completion_tokens": 1000}
    meta = {"extraction": {"page_count": 2}, "inference": {"usage": usage}}
    cost, recorded = invoice_cost_usd(meta, a)
    expected = 2 * 10 / 1000 + 2000 * 2.5 / 1e6 + 2000 * 1.25 / 1e6 + 1000 * 10 / 1e6
    assert recorded and abs(cost - expected) < 1e-9
    assert invoice_cost_usd({}, a) == (0.01, False)  # one page, no usage recorded


def test_straight_through_and_time_saved(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    a = store.add_invoice(tmp_path / "a.pdf", ground_truth, {"requires_review": False})
    b = store.add_invoice(tmp_path / "b.pdf", {**ground_truth, "invoice_number": "X-2"}, {"requires_review": True})
    store.approve_invoice(a, ground_truth, "jane")
    changed = copy.deepcopy({**ground_truth, "invoice_number": "X-2"})
    changed["line_items"][0]["predicted_gl_code"] = "6000"
    store.approve_invoice(b, changed, "jane")
    s = compute(store, Assumptions(manual_minutes=6, clean_minutes=1, changed_minutes=3, hourly_cost=60))
    assert s["approved"] == 2 and s["straight_through"] == 0.5
    assert s["hours_saved"] == (5 + 3) / 60
    assert s["value_saved"] == 8.0
    assert s["needs_attention_rate"] == 0.5


def test_assumptions_are_saved_and_the_report_has_no_invoice_details(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_demo(store)
    Assumptions(monthly_volume=1200, hourly_cost=50).save(store, actor="jane")
    loaded = Assumptions.load(store)
    assert loaded.monthly_volume == 1200 and loaded.hourly_cost == 50
    s = compute(store)
    assert s["cost_per_invoice_cad"] and s["projection"]["hours_saved"] > 0
    page = report_html(s)
    assert "At 1,200 invoices a month" in page
    for detail in ("Northwind", "Pacific", "NW-2026-0912", ".pdf"):
        assert detail not in page


def test_a_damaged_stored_check_does_not_break_insights_or_vendors(tmp_path):
    """A hand-edited or damaged database with a check stored as plain text: the pages still draw."""
    from ap_coder import insights
    from ap_coder.store import Store

    store = Store(tmp_path / "ap.db")
    iid = store.add_invoice(tmp_path / "x.pdf", {"vendor_name": "Acme", "grand_total": 10.0, "currency": "CAD"},
                            {"issues": ["not a check", {"code": "TOTAL_MISMATCH", "severity": "error"}]})  # fmt: skip
    assert iid
    insights.compute(store)  # raised AttributeError ('str' object has no attribute 'get') before
    insights.vendor_workload(store, min_invoices=1)
