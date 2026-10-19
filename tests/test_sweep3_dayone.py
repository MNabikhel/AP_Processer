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


def test_the_queue_decodes_each_invoices_coding_once_not_on_every_load(store, monkeypatch):
    """B-210: /review decoded the coding and checks of every waiting invoice on each load (0.23 s became 0.57 s at
    5,000 waiting). What the lines need is remembered by the stored text, so a changed invoice is asked again."""
    from ap_coder.web.core import reference_or_none
    from ap_coder.web.pages.review import attention

    reference, pending = reference_or_none(store), store.list_invoices(REVIEW)
    first = attention(store, reference, pending, 0.85)

    def no_decoding(*_a, **_k):
        raise AssertionError("decoded again")

    with monkeypatch.context() as m:
        m.setattr(json, "loads", no_decoding)
        assert attention(store, reference, pending, 0.85) == first  # nothing changed: nothing decoded
    invoice_id = without_cost_centers(store)  # its stored coding changed: asked again
    assert (
        invoice_id not in first and "Pick a cost center for" in attention(store, reference, pending, 0.85)[invoice_id]
    )


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


# --- 5. JD Edwards: a missing supplier number links to where it is set ---------------------------------------------


def test_an_export_held_back_for_a_supplier_number_links_to_the_address_numbers(client, store):
    cascade = next(i for i in store.list_invoices(REVIEW) if "Cascade" in (i["vendor_name"] or ""))  # no ERP ID
    store.approve_invoice(cascade["id"], store.get_invoice(cascade["id"])["ai_output"], "Jordan Lee")
    page = client.get("/exports?fmt=jde").text
    assert "no JDE supplier number (AN8)" in page
    held = page[page.index("cannot go to JD Edwards yet") :]
    assert 'href="/settings/jde#an8"' in held and "Supplier address numbers" in held
    assert 'id="an8"' in client.get("/settings/jde").text


# --- 6. Settings → Reading: no cloud card offline, optional parts in grey ---------------------------------------


def test_the_reading_tab_shows_the_azure_check_only_where_the_internet_is_allowed(client, store, monkeypatch):
    from ap_coder.ai import azure_check

    assert 'id="azure-check"' not in client.get("/settings/reading").text  # the offline build: no cloud card
    store.set_setting(azure_check.ENABLED_KEY, "on")  # switched on before: shown, so it can be switched off
    assert 'id="azure-check"' in client.get("/settings/reading").text
    store.set_setting(azure_check.ENABLED_KEY, "off")
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")  # a developer's computer
    assert 'id="azure-check"' in client.get("/settings/reading").text


def test_optional_readers_not_set_up_are_grey_not_red(client, monkeypatch):
    from ap_coder.ai import reading
    from ap_coder.ops import offline
    from ap_coder.web.pages.settings_reading import reader_rows

    monkeypatch.setattr(offline, "onnxruntime_installed", lambda: True)
    monkeypatch.setattr(offline, "table_finder_path", lambda: None)
    monkeypatch.setattr(offline, "from_bundle", lambda: False)  # set up from the code alone, no models folder
    assert reading._table_finder().state == reading.OPTIONAL
    monkeypatch.setattr(offline, "from_bundle", lambda: True)  # the bundle should have brought it: a real problem
    assert reading._table_finder().state == reading.WARN

    status = reading.reading_status(Settings(), None)  # no chat model running in LM Studio
    gl = status.reader("GL coding model")
    assert gl.state == reading.OPTIONAL
    row = next(r for r in reader_rows(status) if r["name"] == "GL coding model")
    assert (row["label"], row["tone"]) == ("Not set up (optional)", "gray")


# --- 7. One "approved as coded" --------------------------------------------------------------------------------------


def test_a_change_is_a_value_the_reviewer_changed_or_a_line_added_not_a_blank_filled_in():
    from ap_coder.erp.insights import corrected

    filled = {"detail": {"changes": [{"what": "line 1 cost center", "before": "UNASSIGNED", "after": "100"},
                                     {"what": "PO number", "before": "", "after": "PO-1"}]}}  # fmt: skip
    added = {"detail": {"changes": [{"what": "line 4 added", "before": "", "after": "Freight · 12.00"}]}}
    fixed = {"detail": {"changes": [{"what": "line 1 GL account", "before": "6010", "after": "1510"}]}}
    assert not corrected(filled) and corrected(added) and corrected(fixed)


def test_insights_learning_activity_and_the_queue_count_approved_as_coded_alike(client, store):
    from ap_coder.erp.insights import approved_as_coded

    pending = [store.get_invoice(i["id"]) for i in store.list_invoices(REVIEW)[:4]]
    cc = store.reference_data().cost_centers.codes[0]
    for n, inv in enumerate(pending):
        output = copy.deepcopy(inv["ai_output"])
        for li in output["line_items"]:  # every one: a cost center filled in where the coder left none
            if li.get("predicted_cost_center") in ("", None, UNASSIGNED):
                li["predicted_cost_center"] = cc
        if n == 0:  # one: a GL account the coder chose, changed
            coded = next(li for li in output["line_items"] if li["predicted_gl_code"] not in ("", UNASSIGNED))
            coded["predicted_gl_code"] = next(c for c in ("6000", "6010") if c != coded["predicted_gl_code"])
        store.approve_invoice(inv["id"], output, "Jordan Lee")
    clean, total = approved_as_coded(store)
    assert total >= 4 and clean <= total - 1
    shown = f"{clean} of {total}"
    insights = client.get("/insights").text
    learning = client.get("/learning").text
    activity = client.get("/activity").text
    for page in (insights, learning):
        assert "Approved as coded" in page and shown in page
    assert "Approved without edits" not in learning and "with reviewer changes" not in activity
    events = store.events(actions=["approved"], limit=1000)
    assert f"{len(events) - (total - clean)} approved as coded, {total - clean} corrected" in activity

    accuracy = store.metrics()["line_accuracy"]  # the same figure, written the same way, on the queue and Learning
    assert f"{accuracy:.1%}" in client.get("/review").text and f"{accuracy:.1%}" in learning


# --- 9. First run: your name ----------------------------------------------------------------------------------------


def test_the_first_steps_ask_for_your_name_until_it_is_given(tmp_path, monkeypatch):
    from ap_coder.core import paths
    from ap_coder.web.pages.process import setup_steps

    monkeypatch.delenv("AP_REVIEWER", raising=False)
    empty = Store(tmp_path / "empty" / "ap_coder.db")
    fresh = TestClient(create_app(replace(Settings(), llm=replace(Settings().llm, provider="off")), empty,
                                  start_reader=False), base_url="http://localhost")  # fmt: skip
    steps = {label: (state, detail) for state, label, detail in setup_steps(empty, Settings())}
    assert steps["Your name"][0] == "todo" and "Settings → Review" in steps["Your name"][1]
    for page in (fresh.get("/review").text, fresh.get("/process").text):  # the welcome page and Import invoices
        assert "Your name" in page and 'href="/settings/review"' in page

    paths.write_user_settings(reviewer="Pat Clerk")
    steps = {label: (state, detail) for state, label, detail in setup_steps(empty, Settings())}
    assert steps["Your name"] == ("ok", "Pat Clerk")


# --- 10. Words: the guide's buttons, the first download --------------------------------------------------------------


def test_the_getting_started_guide_names_the_sample_button_as_the_app_does():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    guide = (root / "docs" / "GETTING_STARTED.md").read_text(encoding="utf-8")
    accounts = (root / "ap_coder" / "web" / "templates" / "accounts.html").read_text(encoding="utf-8")
    assert "Load sample setup" not in guide
    assert "*Use the sample GL accounts*" in guide and ">Use the sample GL accounts</button>" in accounts


def test_a_batch_just_made_offers_download_not_download_again(client, store):
    inv = store.list_invoices(REVIEW)[0]
    store.approve_invoice(inv["id"], store.get_invoice(inv["id"])["ai_output"], "Jordan Lee")
    answer = client.post("/exports/create", data={"fmt": "csv", "ids": [inv["id"]]}, headers=PAGE).json()
    page = client.get(answer["redirect"]).text
    assert "Download it below" in page and "Download again" not in page
    from urllib.parse import parse_qs, urlsplit

    batch = parse_qs(urlsplit(answer["redirect"]).query)["made"][0]
    assert "Download again" in client.get("/exports", params={"batch": batch}).text  # picked later, from the list
