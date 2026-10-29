"""The demo run sheet (docs/DEMO_RUN_SHEET.md), step by step, as the dashboard does it: the sample GL accounts, the ten
sample invoices imported, read and coded; Pacific Office Supply's PO-77120 corrected and approved; its next invoice
(samples/demo_day/) coded from that approval; the same invoice sent again stopped as a duplicate; the Excel export.
CI runs it on Linux and Windows, and in the Windows offline bundle job on the unzipped bundle. With AP_DEMO_DAY_URL
it runs against an AP Coder already running there (the demo launcher, with LM Studio and its models: the release
workflow's pilot laptop, D-129), and checks that the real models did the reading and the coding."""

from __future__ import annotations

import io
import os
import re
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import APPROVED, REVIEW, Store
from ap_coder.web.app import create_app
from tests.conftest import SAMPLES
from tests.web.test_web_invoice import form_of

PAGE = {"X-AP-Coder": "1"}
DEMO_DAY = SAMPLES / "demo_day"
CORRECTED = {"HP 26X toner": "6000", "Dell 27": "6010", "Copy paper": "6000"}  # what AP codes on PO-77120
LIVE = os.environ.get("AP_DEMO_DAY_URL", "")  # a running AP Coder; its database is AP_DEMO_DAY_DB
READER = os.environ.get("AP_DEMO_DAY_READER", "ath-maas_ovisocr2")  # LM Studio's OvisOCR2, as AP Coder names it
WAIT = float(os.environ.get("AP_DEMO_DAY_WAIT", "300"))  # seconds a step may take: on a CPU, minutes an invoice


@pytest.fixture
def demo(tmp_path):
    if LIVE:
        yield from _running()
        return
    store = Store(tmp_path / "private" / "ap_coder.db")
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store)  # no LM Studio: learning needs none
    yield app, TestClient(app, base_url="http://localhost"), store


def _running():
    """The AP Coder running at LIVE, once LM Studio's OvisOCR2 passed its self-test (on demo day it passed the
    evening before, at the rehearsal): no app object, the client talks to it over HTTP."""
    try:  # in every install (openai's HTTP client); httpx comes only with the dev extra the launcher leaves out
        import httpx2 as httpx
    except ModuleNotFoundError:
        import httpx

    from ap_coder.ai import page_worker

    store = Store(Path(os.environ["AP_DEMO_DAY_DB"]))
    with httpx.Client(base_url=LIVE, timeout=WAIT, trust_env=False) as client:  # this computer: never a proxy
        deadline = time.monotonic() + WAIT
        while not page_worker.confirmed(store, READER):
            tested = page_worker.saved_test(store, READER)
            if tested and client.get("/settings/reading/self-test").json()["state"] == "idle":
                break  # tested, and it failed: the assert below says how
            assert time.monotonic() < deadline, f"{READER}'s self-test did not end in {WAIT:.0f} s"
            time.sleep(10)
        assert page_worker.confirmed(store, READER), page_worker.saved_test(store, READER)
        yield None, client, store


def _import(app, client, *paths):
    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in paths]
    assert client.post("/process/upload", files=files, headers=PAGE).json()["ok"]
    if app is not None:
        assert app.state.jobs.get("process").wait(WAIT), "reading took too long"
    deadline = time.monotonic() + WAIT
    while (snap := client.get("/process/status").json())["state"] == "running":
        assert time.monotonic() < deadline, "reading took too long"
        time.sleep(5)
    assert snap["state"] == "done", snap
    return snap["result"]


def _invoice(store, number, status=REVIEW):
    return next(i for i in store.list_invoices(status) if i["invoice_number"] == number)


def _gls(store, invoice_id):
    return [li["predicted_gl_code"] for li in store.get_invoice(invoice_id)["ai_output"]["line_items"]]


def test_the_demo_run_sheet_works_from_import_to_the_excel_export(demo):
    openpyxl = pytest.importorskip("openpyxl")
    app, client, store = demo

    # Before the meeting: the sample GL accounts, then the ten sample invoices imported
    assert client.post("/review/sample-gl", data={"back": "/process"}, headers=PAGE).json()["ok"]
    result = _import(app, client, *sorted(SAMPLES.glob("*.pdf")))
    assert result["ok"] == 10 and result["failed"] == 0
    assert len(store.list_invoices(REVIEW)) == 10
    if LIVE:  # LM Studio's chat model coded each of them, not OvisOCR2 (meta.inference: the model the pipeline asked)
        coded_by = {i["invoice_number"]: (store.get_invoice(i["id"])["meta"].get("inference") or {}).get("model")
                    for i in store.list_invoices(REVIEW)}  # fmt: skip
        assert all(model and READER not in model for model in coded_by.values()), coded_by

    # 1-2. Pacific Office Supply's PO-77120: the tax check adds up; AP codes its three lines and approves
    pacific = _invoice(store, "PO-77120")["id"]
    page = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", client.get(f"/review/{pacific}").text))
    assert "All adds up" in page or "ALL ADDS UP" in page.upper()

    def corrected(rows):
        for row in rows:
            row["predicted_gl_code"] = next(gl for words, gl in CORRECTED.items() if words in row["description"])
        return rows

    approved = client.post(f"/review/{pacific}/approve", data=form_of(store, pacific, corrected), headers=PAGE)
    assert approved.json()["ok"], approved.text
    assert _invoice(store, "PO-77120", APPROVED)["id"] == pacific

    # 3. Its next invoice arrives coded from that approval: nothing to correct, ready to approve
    _import(app, client, DEMO_DAY / "pacific_BC_GST_PST_PO-77311.pdf")
    second = _invoice(store, "PO-77311")["id"]
    assert _gls(store, second) == ["6000", "6000", "6010"]
    row = next(r for r in client.get("/review").text.split("<tr") if "PO-77311" in r)
    assert "Ready" in row
    assert client.post(f"/review/{second}/approve", data=form_of(store, second), headers=PAGE).json()["ok"]

    # 4. PO-77120 sent again (another file): a possible duplicate payment, which can't be approved
    _import(app, client, DEMO_DAY / "pacific_BC_GST_PST_PO-77120_second_notice.pdf")
    again = _invoice(store, "PO-77120")["id"]
    assert again != pacific
    assert "possible duplicate payment" in client.get(f"/review/{again}").text
    refused = client.post(f"/review/{again}/approve", data=form_of(store, again), headers=PAGE)
    assert not refused.json().get("ok") and store.get_invoice(again)["status"] == REVIEW

    # 5. The Excel export: the two approved invoices, their GL lines and the GL summary
    made = client.post("/exports/create", data={"fmt": "xlsx", "ids": [str(pacific), str(second)]}, headers=PAGE)
    batch = re.search(r"batch=(\d+)", made.json()["redirect"]).group(1)
    workbook = openpyxl.load_workbook(io.BytesIO(client.get(f"/exports/batches/{batch}/download?fmt=xlsx").content))
    invoices = [r for r in workbook["Invoices"].iter_rows(min_row=2, values_only=True) if r[0]]
    assert sorted((r[6], r[14]) for r in invoices) == [("PO-77120", 3053.12), ("PO-77311", 2499.84)]
    summary = {r[0]: r[3] for r in workbook["By GL account"].iter_rows(min_row=2, values_only=True) if r[0]}
    assert summary == {"2310": 247.9, "6000": 3192.88, "6010": 2112.18}  # PST expensed with each line (BC)
    assert round(sum(summary.values()), 2) == 5552.96
