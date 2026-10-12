"""The review queue in the web dashboard: getting started, today's numbers, filters, bulk approval and the other
lists' actions."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.config import Settings
from ap_coder.demo import load_demo
from ap_coder.store import APPROVED, PARKED, PENDING, REJECTED, REVIEW, Store
from ap_coder.web.app import create_app
from ap_coder.web.pages.review import confidence_pct, filter_queue, sort_queue

PAGE = {"X-AP-Coder": "1"}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture
def client(store):
    s = Settings()
    return TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), store), base_url="http://localhost")


@pytest.fixture
def demo(store):
    load_demo(store, Settings())
    return store


def test_an_empty_queue_shows_getting_started(client, store):
    text = client.get("/review").text
    assert "Getting started" in text and "Load demo invoices" in text and "Process your first invoices" in text


def test_invoices_waiting_for_gl_accounts_offer_the_sample_accounts(client, store, tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    store.add_invoice(pdf, {"vendor_name": "Acme", "grand_total": 5}, {"issues": []})
    text = client.get("/review").text
    assert "1 invoice read and waiting" in text
    response = client.post("/review/sample-gl", headers=PAGE)
    assert response.status_code == 200 and store.has_reference()
    assert "Sample GL accounts loaded" in client.get(response.json()["redirect"]).text


def test_the_queue_lists_kpis_today_and_rows(client, demo):
    pending = demo.list_invoices(REVIEW)
    text = client.get("/review").text
    for label in ("Waiting", "Approved today", "Coding accuracy", "Lessons learned"):
        assert label in text
    assert f"To review · {len(pending)}" in text and "Approved · " in text and "Failed / rejected · 0" in text
    for inv in pending:
        assert f'href="/review/{inv["id"]}"' in text
    assert "Needs attention" in text and "Ready" in text
    assert "look clean" in text  # the bulk approval bar
    rail_badge = f'title="{len(pending)}">{len(pending)}</span>'
    assert rail_badge in text


def test_filters_search_and_sort_are_in_the_address(client, demo):
    pending = demo.list_invoices(REVIEW)
    flagged = [i for i in pending if i["requires_review"]]
    attention = client.get("/review?show=attention").text
    assert attention.count('class="rq-row attn"') == len(flagged) and 'class="rq-row ready"' not in attention
    ready = client.get("/review?show=ready").text
    assert ready.count('class="rq-row ready"') == len(pending) - len(flagged)
    one = pending[0]
    found = client.get("/review", params={"q": one["invoice_number"]}).text
    assert found.count("rq-row ") - 1 == 1 and f'href="/review/{one["id"]}"' in found  # minus the column heads
    assert "No invoices match" in client.get("/review?q=zzzz-nothing").text
    by_amount = client.get("/review?sort=amount").text
    amounts = sorted(pending, key=lambda r: -(r["grand_total"] or 0))
    assert by_amount.index(f'/review/{amounts[0]["id"]}"') < by_amount.index(f'/review/{amounts[-1]["id"]}"')
    assert client.get("/review?show=bogus&sort=bogus&tab=bogus").status_code == 200


def test_bulk_approve_approves_clean_invoices_and_says_which_were_left(client, demo):
    from ap_coder.bulk import clean_candidates

    candidates = clean_candidates(demo)
    assert len(candidates) >= 2
    flagged = next(i for i in demo.list_invoices(REVIEW) if i["requires_review"])
    ids = [i["id"] for i in candidates] + [flagged["id"]]
    response = client.post("/review/bulk-approve", data={"ids": ids}, headers=PAGE)
    assert response.status_code == 200
    page = client.get(response.json()["redirect"]).text
    assert "Approved " in page and "not approved" in page
    for i in candidates:
        assert demo.get_invoice(i["id"])["status"] in (APPROVED, PENDING)
    assert demo.get_invoice(flagged["id"])["status"] == REVIEW
    assert client.post("/review/bulk-approve", headers=PAGE).status_code == 400  # nothing chosen


def test_approved_and_failed_lists(client, demo):
    approved = demo.list_invoices(APPROVED)
    text = client.get("/review?tab=approved").text
    assert "Recently approved" in text and all(f'href="/review/{i["id"]}"' in text for i in approved)
    assert "Nothing here" in client.get("/review?tab=other").text


def test_a_stale_page_never_deletes_or_reopens_an_approved_invoice(client, demo):
    """Delete and Reopen on the Failed / rejected list (or on a review screen) were offered while the invoice was
    rejected or in review; someone approved it since. The click must not delete a finished approval (perhaps already
    exported), nor reopen it without a reason, withdrawing what it taught."""
    first, second = demo.list_invoices(REVIEW)[:2]
    # A review screen offers Delete; meanwhile a colleague approved the invoice and exported it.
    demo.approve_invoice(first["id"], demo.get_invoice(first["id"])["ai_output"], "Bob")
    batch = demo.create_export_batch([first["id"]], "csv", actor="Bob")
    response = client.post(f"/review/{first['id']}/delete", data={"back": "/review?tab=other"}, headers=PAGE)
    assert "Not done" in client.get(response.json()["redirect"]).text
    assert demo.get_invoice(first["id"])["export_batch"] == batch
    # The Failed / rejected list offers Reopen on a rejected invoice; meanwhile it was reopened and approved.
    demo.reject_invoice(second["id"], "Jane", "not ours")
    demo.reopen(second["id"], "Bob", "it is ours")
    demo.approve_invoice(second["id"], demo.get_invoice(second["id"])["ai_output"], "Bob")
    for url in (f"/review/{second['id']}/reopen", f"/review/{second['id']}/restore"):
        client.post(url, headers=PAGE)
        assert demo.get_invoice(second["id"])["status"] in (APPROVED, PENDING)
    response = client.post(f"/review/{second['id']}/delete", data={"back": "/review?tab=other"}, headers=PAGE)
    assert demo.get_invoice(second["id"])["status"] in (APPROVED, PENDING)


def test_reject_reopen_delete_and_unpark(client, demo):
    first, second = demo.list_invoices(REVIEW)[:2]
    demo.reject_invoice(first["id"], "Jane", "duplicate")
    text = client.get("/review?tab=other").text
    assert "Rejected" in text and f"/review/{first['id']}/reopen" in text
    response = client.post(f"/review/{first['id']}/reopen", headers=PAGE)
    assert response.status_code == 200 and demo.get_invoice(first["id"])["status"] == REVIEW
    again = client.post(f"/review/{first['id']}/reopen", headers=PAGE)  # already reopened: said, nothing breaks
    assert "Not done" in client.get(again.json()["redirect"]).text
    demo.reject_invoice(first["id"], "Jane", "duplicate")
    assert demo.get_invoice(first["id"])["status"] == REJECTED
    response = client.post(f"/review/{first['id']}/delete", data={"back": "/review?tab=other"}, headers=PAGE)
    assert response.status_code == 200 and demo.get_invoice(first["id"]) is None
    demo.park_invoice(second["id"], "Jane", "waiting for the PO", "")
    assert demo.get_invoice(second["id"])["status"] == PARKED
    assert "waiting for the PO" in client.get("/review?tab=parked").text
    client.post(f"/review/{second['id']}/unpark", headers=PAGE)
    assert demo.get_invoice(second["id"])["status"] == REVIEW


def test_queue_helpers():
    rows = [{"vendor_name": "B", "invoice_number": "2", "grand_total": 5, "due_date": "2026-01-02"},
            {"vendor_name": "a", "invoice_number": "1", "grand_total": 9, "due_date": None}]  # fmt: skip
    assert filter_queue(rows, " b ") == [rows[0]] and filter_queue(rows, "") == rows
    assert [r["vendor_name"] for r in sort_queue(rows, "vendor")] == ["a", "B"]
    assert [r["vendor_name"] for r in sort_queue(rows, "amount")] == ["a", "B"]
    assert [r["vendor_name"] for r in sort_queue(rows, "due")] == ["B", "a"]
    assert confidence_pct(0.849, 0.85) == "84.9%" and confidence_pct(0.9, 0.85) == "90%"
