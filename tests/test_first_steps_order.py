"""Getting started leads the pilot tester in the order the shadow pilot needs (D-92): GL accounts, the vendor
master (with each supplier's JD Edwards address number), the JD Edwards settings, the ERP's past coding (to
warm-start the coding), then the first invoices."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core import paths
from ap_coder.core.config import Settings
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.erp import jde
from ap_coder.web.app import create_app
from ap_coder.web.core import SHOW_JDE
from ap_coder.web.pages import process

WHY = "AP Coder learns your coding from what you posted before"


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "lm_studio_step", lambda store, settings: ("ok", process.LM_STUDIO, "ready"))
    monkeypatch.setattr(process, "ocr_available", lambda: True)
    paths.write_user_settings(reviewer="Pat Tester")
    store = Store(tmp_path / "fresh" / "ap_coder.db")
    store.set_setting("our_province", "ON")
    return store


def _client(store: Store) -> TestClient:
    settings = replace(Settings(), llm=replace(Settings().llm, provider="off"))
    return TestClient(create_app(settings, store, start_reader=False), base_url="http://localhost")


def _labels(store: Store) -> list[str]:
    return [label for _, label, _ in process.first_steps(store, Settings(), False)[0]]


def test_with_jd_edwards_the_steps_are_in_the_pilots_order(fresh):
    fresh.set_setting(SHOW_JDE, "1")
    labels = _labels(fresh)
    order = ["GL accounts", process.VENDOR_STEP, process.JDE_STEP, process.HISTORY_STEP, "Process your first invoices"]
    assert [label for label in labels if label in order] == order
    steps = {label: (state, detail) for state, label, detail in process.first_steps(fresh, Settings(), False)[0]}
    assert steps[process.HISTORY_STEP] == ("todo", WHY)
    assert "address number (AN8)" in steps[process.VENDOR_STEP][1]


def test_the_next_step_walks_through_them_one_by_one(fresh):
    fresh.set_setting(SHOW_JDE, "1")
    client = _client(fresh)

    def card() -> str:
        page = client.get("/review").text
        return page.split('class="card next-step"', 1)[1].split("</section>", 1)[0]

    load_sample_setup(fresh)
    assert "Import your vendor master" in card() and f'href="{process.STEP_LINKS[process.VENDOR_STEP]}"' in card()
    fresh.import_vendor_master([{"vendor_name": "Northwind Supply", "erp_id": "1001", "status": "active"}])
    assert "JD Edwards settings" in card() and 'href="/settings/jde"' in card()
    jde.save(fresh, jde.JdeSettings(company="00001", default_bu="100"))  # what the export needs (B-1300)
    assert "Teach AP Coder your past coding" in card() and WHY in card()
    assert f'href="{process.STEP_LINKS[process.HISTORY_STEP]}"' in card()
    fresh.import_history([{"vendor_name": "Northwind Supply", "description": "Copy paper", "gl_code": "6000",
                           "cost_center": "", "amount": 50.0, "date": "2026-01-05"}])  # fmt: skip
    assert "Import your first invoices" in card()
    # The checklist counts every row it shows, on both pages (B-503).
    welcome = re.search(r"aria-label='(\d+)/(\d+)'", client.get("/review").text).groups()
    assert welcome == re.search(r"(\d+) of (\d+) done", client.get("/process").text).groups()


def test_a_vendor_master_without_address_numbers_is_not_done_for_jd_edwards(fresh):
    fresh.set_setting(SHOW_JDE, "1")
    fresh.import_vendor_master([{"vendor_name": "Northwind Supply", "status": "active"}])
    state, _, detail = next(s for s in process.setup_steps(fresh, Settings()) if s[1] == process.VENDOR_STEP)
    assert state == "todo" and "no address number" in detail
    fresh.set_setting(SHOW_JDE, "0")
    state, _, detail = next(s for s in process.setup_steps(fresh, Settings()) if s[1] == process.VENDOR_STEP)
    assert state == "ok" and detail == "1 vendor"


def test_without_jd_edwards_the_rows_show_but_the_next_step_is_unchanged(fresh):
    labels = _labels(fresh)
    assert process.JDE_STEP not in labels and process.VENDOR_STEP in labels and process.HISTORY_STEP in labels
    load_sample_setup(fresh)
    page = _client(fresh).get("/review").text
    assert "Import your first invoices" in page.split('class="card next-step"', 1)[1] and WHY in page


def test_the_links_open_the_import_they_point_to(fresh):
    client = _client(fresh)
    learning = client.get(process.STEP_LINKS[process.HISTORY_STEP].split("#")[0]).text
    assert re.search(r'<details class="card an-expander" id="history"[^>]* open>', learning)
    vendors = client.get(process.STEP_LINKS[process.VENDOR_STEP].split("#")[0]).text
    assert re.search(r'<details class="card expander" id="import"[^>]* open>', vendors)
    assert not re.search(r'id="history"[^>]* open>', client.get("/learning").text)  # (folded otherwise)


def test_past_coding_codes_the_first_invoice_from_a_new_vendor(tmp_path, monkeypatch):
    """The history step's why, measured: through the app's pipeline with no AI model, the first invoice of a vendor
    AP Coder never saw gets no GL account; with the vendor's past coding imported, its lines are coded."""
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.bench.coding import COST_CENTER, ITEM_GL, sample_reference
    from ap_coder.bench.generator import make_stream_case

    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path / "data"))
    settings = replace(Settings(), llm=replace(Settings().llm, provider="off"))
    suppliers = (2, 3, 7)
    firsts = [make_stream_case(tmp_path / "cases", 5, s, 0) for s in suppliers]
    past = [make_stream_case(tmp_path / "cases", 5, s, i) for s in suppliers for i in (1, 2)]
    rows = [{"vendor_name": c.truth["fields"]["vendor_name"]["value"], "description": li["description"],
             "gl_code": ITEM_GL[li["description"]], "cost_center": COST_CENTER[ITEM_GL[li["description"]]],
             "amount": li["amount"], "date": c.truth["fields"]["invoice_date"]["value"]}
            for c in past for li in c.truth["line_items"] if li["description"] in ITEM_GL]  # fmt: skip

    def coded(history: bool) -> tuple[int, int]:
        store = Store(tmp_path / f"{history}.db")
        if history:
            store.import_history(rows)
        pipeline = InvoicePipeline(settings, sample_reference(), store=store)
        lines = suggested = 0
        for case in firsts:
            output = store.get_invoice(pipeline.process(case.path).invoice_id)["ai_output"]
            name = case.truth["fields"]["vendor_name"]["value"]
            known = {r["description"] for r in rows if r["vendor_name"] == name}  # this vendor coded them before
            truth = {round(li["amount"], 2): li["description"] for li in case.truth["line_items"]}
            for li in output["line_items"]:
                description = truth.get(round(li["amount"], 2))
                if description in known:
                    lines += 1
                    suggested += li["predicted_gl_code"] == ITEM_GL[description]
        return lines, suggested

    lines, before = coded(False)
    assert lines >= 3 and before == 0
    assert coded(True) == (lines, lines)
