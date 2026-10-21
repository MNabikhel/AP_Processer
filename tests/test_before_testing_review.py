"""The independent review of "Before testing" (docs/BUGS.md, Sweep 4, B-526 to B-534): JD Edwards users keep their
tab, the developer switch stores only what was ticked, the demo carries no cost centers, the welcome page leads with
LM Studio in the state it is in, the Quick start follows the screens, and the coding memory is one number."""

# ruff: noqa: F811  (the fixtures are tests/test_before_testing.py's, imported)
from __future__ import annotations

import csv
import io
import json
import re
from types import SimpleNamespace

import pytest

from ap_coder.core.store import REVIEW
from ap_coder.web import core
from ap_coder.web.pages import exports as exports_page
from ap_coder.web.pages import process

from .test_before_testing import PAGE, _demo_client, client, demo_db, demo_store, store  # noqa: F401

# --- B-526: JD Edwards users keep their tab and format -----------------------------------------------------------


def test_a_jd_edwards_user_keeps_the_tab_and_the_format_after_the_update(store):
    from ap_coder.erp import jde

    store.set_setting(jde.SETTING_KEY, json.dumps({"company": "00001"}))
    store.set_setting(exports_page.FORMAT_SETTING, jde.FORMAT)
    assert core.jde_shown(store) and store.get_setting(core.SHOW_JDE) == "1"  # turned on, once, and stored
    assert exports_page.last_format(store) == jde.FORMAT
    store.set_setting(core.SHOW_JDE, "0")  # switched off by hand: stays off
    assert not core.jde_shown(store)


def test_a_new_install_has_jd_edwards_hidden_and_nothing_stored(store):
    assert not core.jde_shown(store) and store.get_setting(core.SHOW_JDE, "unset") == "unset"


# --- B-527: the developer switch stores what was ticked ---------------------------------------------------------


@pytest.mark.parametrize("value,on", [("1", True), ("true", True), ("yes", True), ("0", False), ("false", False),
                                      ("no", False), ("", False)])  # fmt: skip
def test_the_developer_env_var_is_read_one_way(monkeypatch, store, value, on):
    monkeypatch.setenv(core.DEVELOPER_ENV, value)
    assert core.developer_env_on() is on and core.developer_tools(store) is on


def test_the_env_var_is_shown_as_on_and_never_stored(client, store, monkeypatch):
    monkeypatch.setenv(core.DEVELOPER_ENV, "1")
    page = client.get("/settings/about").text
    assert "on (set by AP_DEVELOPER_TOOLS)" in page and 'name="developer" value="1"' not in page.split("hidden")[0]
    assert 'type="hidden" name="developer"' not in page
    client.post("/settings/about/show", data={"show_jde": ""}, headers=PAGE)
    assert store.get_setting(core.DEVELOPER_TOOLS) in ("", "0")
    monkeypatch.delenv(core.DEVELOPER_ENV)
    assert not core.developer_tools(store)


def test_save_shown_logs_only_what_changed(client, store):
    client.post("/settings/about/show", data={"show_jde": "1"}, headers=PAGE)
    (event,) = store.events(actions=["settings_changed"])
    assert event["detail"]["keys"] == [core.SHOW_JDE]


# --- B-528: the demo has no cost centers anywhere ---------------------------------------------------------------


def _all_text(store) -> str:
    parts = []
    for row in store.list_invoices():
        inv = store.get_invoice(row["id"])
        parts.append(json.dumps([inv.get("ai_output"), inv.get("final_output")]))
    parts.append(json.dumps(store.feedback_rows(), default=str))
    return " ".join(parts)


def test_the_demo_has_no_cost_center_anywhere(demo_store):
    text = _all_text(demo_store)
    assert not re.search(r'"predicted_cost_center": "CC\d+', text) and '"cost_center": "CC' not in text
    assert not re.search(r'"final_cc": "CC\d+', text)
    client = _demo_client(demo_store)
    ids = [str(r["id"]) for r in demo_store.unexported_approved()]
    answer = client.post("/exports/create", data={"fmt": "csv", "ids": ids}, headers=PAGE).json()
    batch = re.search(r"batch=(\d+)", answer["redirect"]).group(1)
    exported = client.get(f"/exports/batches/{batch}/download?fmt=csv").content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(exported)))
    assert rows and not any(v.startswith("CC") for r in rows for k, v in r.items() if "cost" in k.lower())


def test_harbourviews_demo_mistake_is_a_gl_account(demo_store):
    harbour = next(r for r in demo_store.list_invoices(REVIEW) if "Harbourview" in (r["vendor_name"] or ""))
    lines = demo_store.get_invoice(harbour["id"])["ai_output"]["line_items"]
    assert lines[2]["predicted_gl_code"] != "6100"  # the mistake to correct, a GL account a tester can see


def test_no_hidden_cost_center_is_posted_without_a_list(demo_store):
    page = _demo_client(demo_store).get(f"/review/{demo_store.list_invoices(REVIEW)[0]['id']}").text
    assert 'name="line_cc"' not in page


# --- B-529: LM Studio in the state it is in, first on the welcome page -------------------------------------------


def _status(running=True, ready=True, self_test="passed"):
    return SimpleNamespace(lm_studio=running, page_reader_ready=ready, self_test=self_test)


@pytest.mark.parametrize("status,chat,state,words", [
    (_status(running=False, ready=False, self_test="no model"), False, "todo", "Start server"),
    (_status(ready=False, self_test="no model"), True, "todo", "OvisOCR2 isn't in it"),
    (_status(), False, "todo", "no chat model loaded"),
    (_status(), True, "ok", "OvisOCR2 ready"),
])  # fmt: skip
def test_the_lm_studio_step_needs_ovisocr2_and_the_chat_model(store, monkeypatch, status, chat, state, words):
    monkeypatch.setattr(process, "reading_status_or_none", lambda settings, st, **k: status)
    monkeypatch.setattr(process, "provider_status", lambda s: SimpleNamespace(ready=chat, label="LM Studio · gemma"))
    step = process.lm_studio_step(store, process.Settings())
    assert step[0] == state and words in step[2], step
    help_ = process.lm_studio_help(step)
    assert (help_ is None) == (state == "ok")


def test_the_next_step_is_lm_studio_with_check_again_on_a_laptop(client):
    page = client.get("/review").text
    card = page.split('class="card next-step"', 1)[1].split("</section>", 1)[0]
    assert "Start LM Studio" in card and 'action="/settings/reading/check"' in card and "Start server" in card
    assert page.index('class="card next-step"') < page.index("Getting started")


def test_the_running_but_empty_lm_studio_box_says_what_is_missing(client, monkeypatch):
    monkeypatch.setattr(process, "reading_status_or_none",
                        lambda settings, st, **k: _status(ready=False, self_test="no model"))  # fmt: skip
    page = client.get("/review").text
    card = page.split('class="card next-step"', 1)[1].split("</section>", 1)[0]
    assert "OvisOCR2" in card and "Start server" not in card


# --- B-530, B-531: one name and one numbering --------------------------------------------------------------------


def test_one_name_for_the_sales_tax_accounts_and_recorded_under_it(client):
    page = client.get("/review").text
    assert "Sales tax accounts" in page and "Sales tax GL mapping" not in page
    assert "recorded in it" not in page and "recorded under it" in page


def test_help_and_the_quick_start_number_the_same_steps(client):
    from pathlib import Path

    from ap_coder.ops.help import QUICK_START

    quick = (Path(__file__).resolve().parent.parent / "docs" / "QUICK_START.md").read_text(encoding="utf-8")
    numbered = re.findall(r"(?m)^(\d)\. ([^:]+):", quick)
    assert [n for n, _ in numbered] == [str(i) for i in range(7)]
    assert [title for title, _, _ in QUICK_START] == [t for _, t in numbered]
    assert "Start LM Studio" in quick.split("## Start (every day)", 1)[1].split("##", 1)[0]
    help_page = client.get("/help").text
    assert "Import GL accounts" in quick and 'same steps as the <a href="/help/quick-start">Quick start' in help_page


# --- B-532: the coding memory is one number ------------------------------------------------------------------------


def test_the_coding_memory_is_one_number_with_erp_history_apart(demo_store):
    demo_store.import_history([{"vendor_name": "Old Vendor", "description": "Paper", "gl_code": "6000",
                                "cost_center": "", "amount": 10.0, "date": "2025-01-02"}], actor="t")  # fmt: skip
    client = _demo_client(demo_store)
    reviewed = demo_store.metrics()["lines_reviewed"]
    queue = client.get("/review").text
    data = client.get("/settings/data").text
    tile = data.split("Coding memory", 1)[1][:400]
    assert f">{reviewed:,}<" in tile and "plus 1 line from ERP history" in tile
    assert "Coding memory" in queue and "lines AP confirmed or corrected" not in queue  # (short: fits at 1366)
