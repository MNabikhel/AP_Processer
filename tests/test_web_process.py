"""Process invoices in the web dashboard: upload, read and code in the background, the folder pick-up, duplicates,
the setup card and the demo invoices."""

from __future__ import annotations

import shutil
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core import ui
from ap_coder.core.config import Settings
from ap_coder.core.store import REVIEW, Store, load_sample_setup
from ap_coder.web.app import create_app
from ap_coder.web.core import invoice_dir
from ap_coder.web.pages.process import safe_file_name, save_upload

from .conftest import SAMPLES

PAGE = {"X-AP-Coder": "1"}
STEM = "harbourview_NS_HST_HPS-2026-0347"
OTHER = "northwind_ON_HST_NW-2026-0912"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


@pytest.fixture
def app(store):
    s = Settings()
    return create_app(replace(s, llm=replace(s.llm, provider="off")), store)


@pytest.fixture
def client(app):
    return TestClient(app, base_url="http://localhost")


def finished(app, client):
    job = app.state.jobs.get("process")
    assert job.wait(180), "reading took too long"
    snap = client.get("/process/status").json()
    assert snap["state"] == "done", snap
    return snap


def upload(client, *names):
    files = [("files", (name, (SAMPLES / name).read_bytes(), "application/pdf")) for name in names]
    return client.post("/process/upload", files=files, headers=PAGE)


def test_process_page_shows_setup_upload_and_folder(client):
    text = client.get("/process").text
    assert "Upload invoices" in text and "Invoices folder" in text and "Setup" in text
    assert "GL accounts" in text and "26 imported" in text
    assert "Ready to read invoices" in text
    assert "Load demo invoices" in text
    assert 'accept=".bmp,' in text  # the file picker offers what AP Coder reads


def test_upload_reads_and_codes_in_the_background_then_the_queue_lists_it(app, client, store):
    response = upload(client, f"{STEM}.pdf")
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "job": "process", "status": "/process/status"}
    snap = finished(app, client)
    assert snap["result"]["ok"] == 1 and snap["result"]["failed"] == 0
    assert snap["result"]["label"] == "Processed 1 of 1"
    assert any("Harbourview" in line["text"] for line in snap["lines"])
    assert any("read and coded" in m["text"] and "review queue" in m["text"] for m in snap["result"]["messages"])
    assert (invoice_dir(store) / f"{STEM}.pdf").exists()  # saved in the invoices folder
    invoices = store.list_invoices(REVIEW)
    assert len(invoices) == 1
    queue = client.get("/review").text
    assert "Harbourview Professional Services" in queue and f'href="/review/{invoices[0]["id"]}"' in queue
    assert "1 invoice waiting" in queue
    page = client.get("/process").text  # the job card stays, with the summary, until the next run
    assert "Processed 1 of 1" in page and "Recently processed" in page
    assert client.get(f"/review/{invoices[0]['id']}").status_code == 200


def test_a_file_already_in_ap_coder_is_skipped(app, client, store):
    upload(client, f"{STEM}.pdf")
    finished(app, client)
    assert upload(client, f"{STEM}.pdf", f"{STEM}.pdf").status_code == 200
    snap = finished(app, client)
    assert snap["result"]["processed"] == 0 and snap["result"]["label"] == "Nothing new to read"
    skipped = [m for m in snap["result"]["messages"] if m["text"].startswith("Skipped")]
    assert skipped and skipped[0]["details"] == [f"{STEM}.pdf", f"{STEM}.pdf"]
    assert len(store.list_invoices()) == 1


def test_upload_refuses_other_files_and_waits_for_a_running_job(app, client):
    response = client.post("/process/upload", files=[("files", ("notes.exe", b"MZ", "application/octet-stream"))],
                           headers=PAGE)  # fmt: skip
    snap = finished(app, client)
    assert response.status_code == 200
    assert any("notes.exe was not read" in m["text"] for m in snap["result"]["messages"])
    job = app.state.jobs.get("process")
    job.state = "running"  # as if a run were under way
    try:
        response = upload(client, f"{STEM}.pdf")
        assert response.status_code == 409 and "already reading" in response.json()["error"]
    finally:
        job.state = "done"


def test_upload_needs_gl_accounts_first(tmp_path):
    bare = Store(tmp_path / "private" / "ap_coder.db")
    s = Settings()
    client = TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), bare), base_url="http://localhost")
    assert "Setup not finished" in client.get("/process").text
    response = upload(client, f"{STEM}.pdf")
    assert response.status_code == 400 and "GL accounts" in response.json()["error"]
    assert bare.list_invoices() == []


def test_the_folder_pick_up_lists_and_processes_new_files(app, client, store):
    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLES / f"{OTHER}.pdf", folder / f"{OTHER}.pdf")
    text = client.get("/process").text
    assert f"{OTHER}.pdf" in text and "Process 1 file" in text
    response = client.post("/process/folder", headers=PAGE)
    assert response.json()["job"] == "process"
    snap = finished(app, client)
    assert snap["result"]["ok"] == 1
    assert "No new files" in client.get("/process").text


def test_safe_upload_names(tmp_path):
    assert safe_file_name("../../etc/passwd") == "passwd"
    assert safe_file_name("C:\\Users\\me\\inv.pdf") == "inv.pdf"
    assert safe_file_name(".hidden") == "hidden" and safe_file_name("") == "invoice"
    a = save_upload(tmp_path, "a.pdf", b"one")
    b = save_upload(tmp_path, "a.pdf", b"two")
    assert a.name == "a.pdf" and b.name == "a_1.pdf" and save_upload(tmp_path, "a.pdf", b"one") == a


def test_demo_invoices_load_and_remove(client, store):
    response = client.post("/demo/load", data={"back": "/process"}, headers=PAGE)
    assert response.json()["redirect"].startswith("/process?flash=")
    assert store.demo_count() == 10
    assert "Remove demo invoices" in client.get("/process").text
    assert client.post("/demo/remove", data={"back": "/process"}, headers=PAGE).status_code == 400  # not ticked
    assert store.demo_count() == 10
    response = client.post("/demo/remove", data={"back": "/process", "sure": "1"}, headers=PAGE)
    assert "Removed 10 demo invoices" in client.get(response.json()["redirect"]).text
    assert store.demo_count() == 0


def test_the_reading_banner_shows_on_process_and_review_only(client):
    banner = '<div class="banner warn"'  # the FAQ on Help says "Open Settings → Reading" too
    assert banner in client.get("/process").text  # no LM Studio in tests: OvisOCR2 isn't running
    assert banner in client.get("/review").text
    assert banner not in client.get("/help").text


def test_the_import_list_says_ready_only_for_an_invoice_the_queue_calls_ready(store):
    """With the sample setup (no cost centers picked by the coder) and LM Studio not answering, a sample whose checks
    found no error is still one whose lines need a cost center: not "ready". (Weak matches were coded then, so some
    invoices passed the checks; since B-463 they are left blank, and the import list must still say what the queue
    says.)"""
    from ap_coder.web.pages.review import attention

    s = Settings()
    llm = replace(s.llm, provider="local", base_url="http://127.0.0.1:9/v1")  # nothing answers there
    app = create_app(replace(s, llm=llm), store, start_reader=False)
    client = TestClient(app, base_url="http://localhost")
    upload(client, *sorted(p.name for p in SAMPLES.glob("*.pdf")))
    snap = finished(app, client)
    pending = store.list_invoices(REVIEW)
    reasons = attention(store, store.reference_data(), pending)
    assert reasons  # lines to code: the queue flags them
    lines = [line["text"] for line in snap["lines"]]
    for inv in pending:
        said = [t for t in lines if t.startswith(f"{inv['vendor_name']}:")]
        assert said and all(t.endswith("needs attention" if inv["id"] in reasons else "ready") for t in said), (
            said,
            reasons.get(inv["id"]),
        )
    from ap_coder.web.pages import insights as insights_page

    insights_page.MIN_APPROVALS, kept = 0, insights_page.MIN_APPROVALS  # (its figures show from 20 approvals: B-515)
    try:
        insights = client.get("/insights").text  # its share is the checks' flag, said so: not the queue's "Ready"
    finally:
        insights_page.MIN_APPROVALS = kept
    assert "Flagged by the checks when processed" in insights and "Sent for a closer look" not in insights
    rows = client.get("/process").text.split("<div class='apc-rrow'>")[1:]  # Recently processed: the same pill
    for inv in pending:
        row = next((r for r in rows if ui.esc(inv["vendor_name"]) in r and inv["invoice_number"] in r), None)
        assert row is None or ("Needs attention" in row) == (inv["id"] in reasons), (inv["vendor_name"], row)
