"""A withdrawn approval takes back what it taught the supplier's template (D-71): the template as it was before is
kept (``template_snapshots``) and put back when the approval is reopened, rejected or sent back, unless a later
approval changed the template since (that one is kept, and the Activity log says so). The Learning page can reset a
supplier's layout, with a confirm step and an audit event."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.web.app import create_app

PAGE = {"X-AP-Coder": "1"}
KEY = "acme"


def _approved(store: Store, n: int) -> int:
    invoice_id = store.add_invoice(f"INV-{n}.pdf", {"vendor_name": "Acme", "line_items": []}, None)
    store.approve_invoice(invoice_id, {"vendor_name": "Acme", "line_items": [], "grand_total": 0.0}, "Ann")
    return invoice_id


def _template(store: Store) -> dict | None:
    return (store.get_supplier_profile(KEY) or {}).get("template")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "s.db")


def test_reopening_an_approval_puts_the_template_back_as_it_was(store):
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}})
    wrong = _approved(store, 1)
    store.save_supplier_profile(KEY, template={"fields": {"a": 2}}, invoice_id=wrong)
    assert _template(store) == {"fields": {"a": 2}}
    store.reopen(wrong, "Ann", "the wrong words were taught")
    assert _template(store) == {"fields": {"a": 1}}
    detail = store.events(actions=["reopened"])[0]["detail"]
    assert "layout" in str(detail) and "put back" in str(detail)


def test_the_first_approval_withdrawn_leaves_no_layout(store):
    first = _approved(store, 1)
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}}, invoice_id=first)
    store.reopen(first, "Ann")
    assert _template(store) is None


def test_a_later_approval_that_changed_the_template_is_kept_and_said(store):
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}})
    wrong = _approved(store, 1)
    store.save_supplier_profile(KEY, template={"fields": {"a": 2}}, invoice_id=wrong)
    later = _approved(store, 2)
    store.save_supplier_profile(KEY, template={"fields": {"a": 3}}, invoice_id=later)
    store.reopen(wrong, "Ann")
    assert _template(store) == {"fields": {"a": 3}}
    detail = str(store.events(actions=["reopened"])[0]["detail"])
    assert "later approval" in detail and "kept" in detail


def test_rejecting_and_sending_back_withdraw_the_layout_too(store):
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}})
    wrong = _approved(store, 1)
    store.save_supplier_profile(KEY, template={"fields": {"a": 2}}, invoice_id=wrong)
    store.reopen(wrong, "Ann")
    store.save_supplier_profile(KEY, template={"fields": {"a": 5}}, invoice_id=wrong)  # approved again
    store.reject_invoice(wrong, "Ann", "duplicate")
    assert _template(store) == {"fields": {"a": 1}}


def test_only_the_last_few_snapshots_of_a_supplier_are_kept(store):
    from ap_coder.core.store._base import KEEP_SNAPSHOTS

    for n in range(KEEP_SNAPSHOTS + 3):
        store.save_supplier_profile(KEY, display_name="Acme", template={"n": n}, invoice_id=_approved(store, n))
    with store._conn() as conn:
        kept = conn.execute("SELECT COUNT(*) FROM template_snapshots WHERE supplier_key = ?", (KEY,)).fetchone()[0]
    assert kept == KEEP_SNAPSHOTS


def test_reset_layout_on_the_learning_page_asks_first_and_is_logged(store):
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}}, invoice_id=_approved(store, 1))
    s = Settings()
    client = TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), store), base_url="http://localhost")
    page = client.get("/learning?tab=suppliers").text
    assert "/learning/suppliers/reset-layout" in page and "data-confirm" in page and "Reset layout" in page
    response = client.post("/learning/suppliers/reset-layout", data={"key": KEY}, headers=PAGE)
    assert response.status_code == 200 and response.json()["ok"]
    assert _template(store) is None
    event = store.events(actions=["layout_reset"])[0]
    assert event["actor"] and "Acme" in str(event["detail"])
    assert client.post("/learning/suppliers/reset-layout", data={"key": "nobody"}, headers=PAGE).status_code == 404
    assert client.post("/learning/suppliers/reset-layout", data={"key": KEY}).status_code == 403


def test_the_learning_bench_measures_a_wrong_approval_and_its_withdrawal(tmp_path):
    from ap_coder.bench.learning import run_learning

    report = run_learning(2, 5, seed=1, out=tmp_path / "learn", workers=1, wrong_at=3)
    wrong = report["summary"]["wrong_approval"]
    assert wrong["at"] == 3 and report["config"]["wrong_approval_at"] == 3
    nxt = wrong["the wrong approval's next invoice"]
    assert nxt["invoices"] == 2 and nxt["invoice_number_wrong"] == 0  # withdrawn: the template is as it was
    assert "## A wrong approval, then its withdrawal" in (tmp_path / "learn" / "report.md").read_text(encoding="utf-8")
