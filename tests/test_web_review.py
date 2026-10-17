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


# --- The first session: the quickest start first, no dead end, the self-test said calmly --------------------------


def test_an_empty_queue_puts_the_demo_invoices_first(client, store, tmp_path):
    text = client.get("/review").text
    assert text.index("Load demo invoices") < text.index("Getting started")  # nothing yet: the quickest start
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    store.add_invoice(pdf, {"vendor_name": "Acme", "grand_total": 5}, {"issues": []})
    text = client.get("/review").text  # invoices waiting for GL accounts: that comes first, the demo last
    assert text.index("read and waiting") < text.index("Getting started") < text.index("Load demo invoices")


def test_process_offers_the_sample_gl_accounts_when_there_are_none(client, store):
    text = client.get("/process").text
    assert "Finish the setup first" in text and 'action="/review/sample-gl"' in text
    response = client.post("/review/sample-gl", data={"back": "/process"}, headers=PAGE)
    assert response.status_code == 200 and response.json()["redirect"].startswith("/process")
    assert store.has_reference()
    text = client.get("/process").text
    assert 'action="/review/sample-gl"' not in text and "Finish the setup first" not in text


def test_the_sample_gl_accounts_never_send_the_browser_elsewhere(client):
    response = client.post("/review/sample-gl", data={"back": "https://elsewhere.example/"}, headers=PAGE)
    assert response.status_code == 200 and response.json()["redirect"].startswith("/review")


@pytest.mark.parametrize(
    ("self_test", "tone", "icon"),
    [("pending", "info", "hourglass_top"), ("running", "info", "hourglass_top"), ("failed", "warn", "visibility_off"),
     ("no model", "warn", "visibility_off")],
)  # fmt: skip
def test_the_reading_banner_is_calm_while_ovisocr2_tests_itself(client, monkeypatch, self_test, tone, icon):
    from types import SimpleNamespace

    from ap_coder.web import core

    status = SimpleNamespace(banner="The page reader's state.", self_test=self_test)
    monkeypatch.setattr(core, "reading_status_or_none", lambda settings, store, **k: status)
    text = client.get("/review").text
    banner = text.split('role="status">', 1)[0].rsplit("<div", 1)[1]
    assert f'class="banner {tone}"' in banner
    assert icon in text.split('role="status">', 1)[1].split("</span>", 1)[0]


def test_the_setup_step_names_the_self_tests_state(store, monkeypatch):
    from types import SimpleNamespace

    from ap_coder.web.pages import process

    def step(self_test, ready=False):
        status = SimpleNamespace(page_reader_ready=ready, self_test=self_test)
        monkeypatch.setattr(process, "reading_status_or_none", lambda settings, store, **k: status)
        return process.setup_steps(store, Settings())[4]

    assert step("passed", ready=True)[0] == "ok"
    assert step("pending")[0] == "wait" and "testing itself" in step("pending")[2]  # nothing wrong: calm
    assert step("running")[0] == "wait" and "use AP Coder meanwhile" in step("running")[2]
    assert step("failed") == ("bad", "OvisOCR2", "failed its self-test (Settings → Reading)")
    assert step("no model")[0] == "todo" and "not running in LM Studio" in step("no model")[2]


def test_the_rail_calls_the_manual_import_import_invoices(client):
    """The review queue is the work list; the page that brings invoices in by hand is "Import invoices" (same URL)."""
    text = client.get("/review").text
    assert 'href="/process"' in text and "Import invoices" in text and "Process invoices" not in text


def test_the_review_and_export_pages_do_not_ask_the_database_once_per_invoice(tmp_path, monkeypatch):
    """Sweep: the queue read each row's vendor terms, the second-approval tab each invoice's events and the JD Edwards
    check each ready invoice, every one with its own database connection: 5,000 rows meant 5,000 connections."""
    import sqlite3

    from ap_coder import store as store_mod
    from ap_coder.jde import FORMAT as JDE
    from ap_coder.store import load_sample_setup

    from .webclient import add_clean, client_for

    monkeypatch.setenv("AP_REVIEWER", "Sam")
    db = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(db)
    db.set_setting("approval_limit", "10000")
    client = client_for(db)
    opened: list[str] = []
    real = sqlite3.connect

    def counting(*args, **kwargs):
        opened.append(str(args[0]))
        return real(*args, **kwargs)

    def connections(url: str) -> int:
        monkeypatch.setattr(store_mod.sqlite3, "connect", counting)
        opened.clear()
        assert client.get(url).status_code == 200
        monkeypatch.setattr(store_mod.sqlite3, "connect", real)
        return len(opened)

    def add(n: int) -> None:
        for _ in range(n):
            add_clean(
                db, vendor_name=f"Vendor {len(db.list_invoices())}", invoice_number=f"N-{len(db.list_invoices())}"
            )
            approved = add_clean(db, vendor_name=f"Vendor {len(db.list_invoices())}",
                                 invoice_number=f"N-{len(db.list_invoices())}")  # fmt: skip
            db.approve_invoice(approved, db.get_invoice(approved)["ai_output"], "Jane")  # over the limit: waits
            ready = add_clean(db, vendor_name=f"Vendor {len(db.list_invoices())}",
                              invoice_number=f"N-{len(db.list_invoices())}", grand_total=100.0)  # fmt: skip
            db.approve_invoice(ready, db.get_invoice(ready)["ai_output"], "Jane")

    pages = ("/review", "/review?tab=second", f"/exports?fmt={JDE}")
    add(2)
    few = [connections(url) for url in pages]
    add(10)
    many = [connections(url) for url in pages]
    assert many == few
