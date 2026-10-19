"""Day one of a pilot: what a pilot tester meets first (the queue's Ready, the line grid, the vendor note, the
second approval, the export's supplier numbers, the Reading tab, one "approved as coded", the import list, the first
steps and the words of the docs)."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.store import REVIEW, Store
from ap_coder.ops.demo import load_demo
from ap_coder.web.app import create_app

PAGE = {"X-AP-Coder": "1"}


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    """The demo invoices, loaded once (copied for each test: loading reads ten PDFs)."""
    path = tmp_path_factory.mktemp("demo") / "private" / "ap_coder.db"
    load_demo(Store(path), Settings())
    return path


@pytest.fixture
def store(demo_db, tmp_path):
    import shutil

    target = tmp_path / "private" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    return Store(target)


@pytest.fixture
def client(store):
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def me(monkeypatch):
    """A fixed reviewer and login (not this computer's)."""
    for module in ("ap_coder.web.core", "ap_coder.web.pages.invoice", "ap_coder.web.pages.review"):
        monkeypatch.setattr(f"{module}.reviewer", lambda: "Jordan Lee", raising=False)
        monkeypatch.setattr(f"{module}.login", lambda: "jlee", raising=False)


def check(client: TestClient, invoice_id: int) -> dict:
    response = client.post(f"/review/{invoice_id}/check", data={}, headers=PAGE)
    assert response.status_code == 200, response.text
    return response.json()


def without_cost_centers(store: Store) -> int:
    """A clean demo invoice with every line's cost center left blank, as the coder leaves them on the sample setup:
    no check error, confidence above the threshold."""
    inv = store.get_invoice(next(i for i in store.list_invoices(REVIEW) if not i["requires_review"])["id"])
    output = copy.deepcopy(inv["ai_output"])
    for li in output["line_items"]:
        li["predicted_cost_center"] = UNASSIGNED
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", (json.dumps(output), inv["id"]))
    return inv["id"]


# --- 1. Ready means approvable ---------------------------------------------------------------------------------------


def test_the_queue_never_calls_ready_an_invoice_whose_review_screen_blocks_approval(client, store, tmp_path):
    invoice_id = without_cost_centers(store)
    queue = client.get("/review").text
    at = queue.index(f'href="/review/{invoice_id}"')
    row = queue[queue.rindex("<li", 0, at) : queue.index("</li>", at)]
    assert 'class="rq-row attn"' in row and "Ready" not in row
    assert "Pick a cost center for" in row
    assert f'href="/review/{invoice_id}"' not in client.get("/review?show=ready").text
    assert f'href="/review/{invoice_id}"' in client.get("/review?show=attention").text
    assert check(client, invoice_id)["allow"] is False  # the review screen: Approve disabled


def test_every_invoice_the_queue_calls_ready_can_be_approved_on_its_screen(client, store, tmp_path):
    without_cost_centers(store)
    ready = client.get("/review?show=ready").text
    for inv in store.list_invoices(REVIEW):
        if f'href="/review/{inv["id"]}"' in ready:
            assert check(client, inv["id"])["allow"], inv["vendor_name"]
