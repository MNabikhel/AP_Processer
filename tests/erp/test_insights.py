"""Insights: straight-through rate, time saved and Azure cost from AP Coder's own records."""

import copy

from ap_coder.core.store import Store
from ap_coder.erp.insights import Assumptions, compute, invoice_cost_usd, report_html
from ap_coder.ops.demo import load_demo


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


def test_assumptions_are_saved_and_the_report_has_no_invoice_details(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")  # a build that may use Azure: the report has its cost too
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
    from ap_coder.core.store import Store
    from ap_coder.erp import insights

    store = Store(tmp_path / "ap.db")
    iid = store.add_invoice(tmp_path / "x.pdf", {"vendor_name": "Acme", "grand_total": 10.0, "currency": "CAD"},
                            {"issues": ["not a check", {"code": "TOTAL_MISMATCH", "severity": "error"}]})  # fmt: skip
    assert iid
    insights.compute(store)  # raised AttributeError ('str' object has no attribute 'get') before
    insights.vendor_workload(store, min_invoices=1)


def test_filling_in_a_blank_is_not_a_correction():
    """The audit trail keeps every change; Insights counts as corrected only what changed what was proposed."""
    from ap_coder.erp.insights import corrected

    filled = {"detail": {"changes": [{"what": "line 1 cost center", "before": "UNASSIGNED", "after": "100"},
                                     {"what": "line 2 cost center", "before": "", "after": "200"}]}}  # fmt: skip
    fixed = {"detail": {"changes": [{"what": "line 1 GL account", "before": "6010", "after": "1510"}]}}
    assert not corrected(filled) and corrected(fixed) and not corrected({"detail": {}})


def test_the_insights_pages_figures_follow_their_data_and_settle_for_a_minute_under_changes(
    tmp_path, ground_truth, monkeypatch
):
    """The Insights page's figures (``shown``, ``shown_operations``) are kept until the invoices, the vendors' payment
    terms, the default terms or the assumptions change, whoever changed them (another Store, plain SQL); once the
    invoices or the vendors changed they are worked out again at most every ``insights.SETTLE_SECONDS`` (D-166); a
    change of the assumptions or of the default terms is another question, worked out at once."""
    from ap_coder.core.store import reports, summary
    from ap_coder.erp import insights
    from tests.core.test_kept_answers import NOW, _invoice, _plain

    clock = [1000.0]
    monkeypatch.setattr(summary, "monotonic", lambda: clock[0])
    monkeypatch.setattr(reports, "wall", lambda: clock[0])  # (worked out once for every process, B-1799)
    worked = {"compute": 0, "operations": 0}
    for name, real in (("compute", insights.compute), ("operations", insights.operations)):
        monkeypatch.setattr(insights, name, lambda *a, real=real, name=name: worked.update({name: worked[name] + 1})
                            or real(*a))  # fmt: skip
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    first = _invoice(other, tmp_path, ground_truth, "I-1")

    def seen() -> tuple:
        return insights.shown(store)["processed"], insights.shown_operations(store)["waiting"], dict(worked)

    assert seen() == (1, 1, {"compute": 1, "operations": 1}) == seen()
    _invoice(other, tmp_path, ground_truth, "I-2")
    clock[0] += insights.SETTLE_SECONDS - 1
    assert seen() == (1, 1, {"compute": 1, "operations": 1})  # changed, worked out under a minute ago: settled
    clock[0] += 1
    assert seen() == (2, 2, {"compute": 2, "operations": 2})
    _plain(store, "DELETE FROM invoices WHERE id = ?", first)
    clock[0] += insights.SETTLE_SECONDS
    assert seen()[:2] == (1, 1)  # (the page that brings the summary up to date works it out, the next keeps it)
    counts = seen()[2]
    assert seen()[2] == counts
    _plain(store, "INSERT INTO vendors (vendor_key, display_name, updated_at, terms) VALUES ('x', 'X', ?, 'Net 10')",
           NOW)  # fmt: skip
    assert seen()[2] == counts
    clock[0] += insights.SETTLE_SECONDS
    assert seen()[2] == {**counts, "operations": counts["operations"] + 1}  # (its terms: the operations only)
    other.set_setting("default_terms_days", "45")  # (each invoice's due date worked out again: the summary too)
    assert seen()[2]["operations"] == counts["operations"] + 2
    before = worked["compute"]
    insights.Assumptions(monthly_volume=1200).save(other)
    assert insights.shown(store)["assumptions"].monthly_volume == 1200 and worked["compute"] == before + 1
