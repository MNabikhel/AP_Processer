"""Day one of a pilot: what a pilot tester meets first (the queue's Ready, the line grid, the vendor note, the
second approval, the export's supplier numbers, the Reading tab, one "approved as coded", the import list, the first
steps and the words of the docs)."""

from __future__ import annotations

import copy
import importlib
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.store import REVIEW, Store
from ap_coder.ops.demo import load_demo
from ap_coder.web.app import create_app
from ap_coder.web.pages import MORE_MODULES, PAGES

PAGE = {"X-AP-Coder": "1"}

# Every page module imported now, before `me` patches web/core.py: a module imported while it is patched would keep
# the patched `reviewer` it imports by name, for the tests of the files that run after this one.
for _module in [*MORE_MODULES, *(p.module for p in PAGES if p.module)]:
    importlib.import_module(f"ap_coder.web.pages.{_module}")


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


# --- 2. Apply a cost center to every line without one ----------------------------------------------------------------


def test_apply_to_all_lines_codes_only_the_lines_that_have_none(client, store):
    from .test_web_invoice import form_of

    invoice_id = without_cost_centers(store)
    reference = store.reference_data()
    cc, other = reference.cost_centers.codes[:2]
    form = form_of(store, invoice_id, lambda rows: [{**r, "predicted_cost_center": other} if i == 0 else r
                                                    for i, r in enumerate(rows)])  # fmt: skip
    answer = client.post(f"/review/{invoice_id}/fill-lines", data={**form, "fill_cc": cc}, headers=PAGE).json()
    assert answer["ok"], answer
    n = len(store.get_invoice(invoice_id)["ai_output"]["line_items"])
    assert f"Cost center {cc} set on {n - 1} line" in answer["toast"]["text"]
    lines = answer["lines"]
    assert lines.count(f'<option value="{cc}" selected') == n - 1
    assert lines.count(f'<option value="{other}" selected') == 1
    assert check(client, invoice_id)["allow"]  # every line has its cost center now: Approve is open


def test_apply_to_all_lines_refuses_a_code_not_in_the_lists(client, store):
    from .test_web_invoice import form_of

    invoice_id = without_cost_centers(store)
    form = form_of(store, invoice_id)
    refused = client.post(f"/review/{invoice_id}/fill-lines", data={**form, "fill_cc": "NOPE"}, headers=PAGE).json()
    assert not refused["ok"] and "not in your cost center list" in refused["error"]
    nothing = client.post(f"/review/{invoice_id}/fill-lines", data=form, headers=PAGE).json()
    assert not nothing["ok"] and "Pick a GL account or a cost center" in nothing["error"]


def test_a_vendors_one_usual_cost_center_is_offered_for_its_lines(client, store):
    from ap_coder.web import invoice_view

    reference = store.reference_data()
    cc, other = reference.cost_centers.codes[:2]
    first, second = (store.get_invoice(i["id"]) for i in store.list_invoices(REVIEW)[:2])
    vendor = first["ai_output"]["vendor_name"]
    approved = copy.deepcopy(first["ai_output"])
    for li in approved["line_items"]:
        li["predicted_cost_center"] = cc
    store.approve_invoice(first["id"], approved, "Jordan Lee")
    assert invoice_view.usual_cost_center(store, reference, vendor) == cc

    output = copy.deepcopy(second["ai_output"])  # another invoice of the same vendor, its cost centers left blank
    output["vendor_name"] = vendor
    for li in output["line_items"]:
        li["predicted_cost_center"] = UNASSIGNED
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", (json.dumps(output), second["id"]))
    page = client.get(f"/review/{second['id']}").text
    start = page.index("data-fill-cc")
    assert f'<option value="{cc}" selected' in page[start : page.index("</select>", start)]
    assert f"used cost center {cc}" in page

    # One past line on another cost center: there is no single usual one any more.
    line = {**output["line_items"][0], "predicted_cost_center": other}
    store.approve_invoice(second["id"], {**output, "line_items": [line]}, "Jordan Lee")
    assert invoice_view.usual_cost_center(store, reference, vendor) == ""


# --- 4. The second approval on one laptop -----------------------------------------------------------------------


def test_the_second_approval_says_how_to_give_it_when_one_login_cannot(client, store):
    from ap_coder.core.store import PENDING

    store.set_setting("approval_limit", "1")
    inv = store.list_invoices(REVIEW)[0]
    store.approve_invoice(inv["id"], store.get_invoice(inv["id"])["ai_output"], "Jordan Lee", login="jlee")
    assert store.get_invoice(inv["id"])["status"] == PENDING

    for page in (client.get(f"/review/{inv['id']}").text, client.get("/review?tab=second").text):
        assert "second Windows account" in page and "office network" in page and "Send back" in page
    settings = client.get("/settings/review").text
    assert "one Windows login" in settings and "second Windows account" in settings and "office network" in settings
    # The control itself is unchanged: the first approver still can't give it.
    answer = client.post(f"/review/{inv['id']}/final-approve", headers=PAGE).json()
    assert store.get_invoice(inv["id"])["status"] == PENDING and "Not done" in client.get(answer["redirect"]).text
