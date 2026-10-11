"""Bugs found driving the dashboard in a browser as a clerk and a manager would (third sweep), each reproduced here
through the page or the helper it came from, so they are not made again."""

import json
from types import SimpleNamespace

import pandas as pd
from streamlit.testing.v1 import AppTest

from ap_coder.store import Store, load_sample_setup

from .conftest import SAMPLE_STEM, SAMPLES
from .test_dashboard_pages import APP, TIMEOUT, _ok
from .test_dashboard_pages import db as db  # the fixture (a fresh database per test)


def _html(at):
    return " ".join(str(h.proto.body) for h in at.get("html"))


def test_the_queued_note_reads_as_a_sentence(db, monkeypatch):
    """The toast after processing said "2 invoices queued for OvisOCR2, which isn't running: them wait for it"."""
    from ap_coder.webapp import process

    monkeypatch.setattr(process, "reading_status_or_none", lambda: SimpleNamespace(page_reader_ready=False))
    assert process.queued_note(2) == (
        "2 invoices queued for OvisOCR2, which isn't running: they wait for it (Settings → Reading)."
    )
    assert process.queued_note(1) == (
        "1 invoice queued for OvisOCR2, which isn't running: it waits for it (Settings → Reading)."
    )
    assert process.queued_note(0) == ""


def test_the_split_menu_names_cost_centers_as_the_line_grid_does(db):
    """The "Split a line…" menu showed a line without a cost center as the stored code UNASSIGNED (and CC400 without
    its name), where the line grid beside it says "Needs a cost center"."""
    store = Store(db)
    load_sample_setup(store)
    doc = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    doc["line_items"][0]["predicted_cost_center"] = "UNASSIGNED"
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", doc, {"requires_review": True})
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    _ok(at.run())
    split = next(e for e in at.dataframe if "_split_parts_" in e.proto.id)
    options = json.loads(split.proto.columns)["cc"]["type_config"]["options"]
    # an option without a label shows the stored code itself
    labels = dict((o["value"], o["label"]) if isinstance(o, dict) else (o, o) for o in options)
    assert labels["UNASSIGNED"] == "Needs a cost center"
    assert labels["CC400"].startswith("CC400 · ")


def test_the_approve_bar_counts_changes_as_the_approval_does(db):
    """Picking the cost centers the AI left blank read "you changed 4 of its codes" on the approve bar, and the
    approval then said "3 of 4 lines coded as suggested": filling a blank corrects nothing."""
    store = Store(db)
    load_sample_setup(store)
    doc = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    for li in doc["line_items"]:
        li["predicted_cost_center"] = "UNASSIGNED"
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", doc, {"requires_review": True})
    key = f"inv{invoice_id}"
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    _ok(at.run())
    n = len(doc["line_items"])
    filled = [{**li, "predicted_cost_center": "CC400"} for li in doc["line_items"]]
    filled[0]["predicted_gl_code"] = "6000" if filled[0]["predicted_gl_code"] != "6000" else "6010"  # one change
    at.session_state[f"_draft_{key}_lines"] = pd.DataFrame(filled)
    at.session_state[f"_base_{key}_lines"] = pd.DataFrame(filled)
    _ok(at.run())
    bar = _html(at).split("Teaches from ")[-1].split("</div>")[0]
    assert bar == f"{n} lines · you changed 1 of its codes"
    _ok(at.button(key=f"{key}_approve").click().run())
    assert f"{n - 1} of {n} lines coded as suggested" in " ".join(t.value for t in at.toast)


def test_a_restore_asks_for_restore_again_before_the_next_one(db):
    """After a restore the menu stayed open with RESTORE still typed and the button enabled: one more click (or
    picking another backup, then one click) replaced the database again without a new confirmation."""
    store = Store(db)
    load_sample_setup(store)
    older = store.backup_now("manual")
    newer = store.backup_now("manual")
    assert older != newer
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages
    at = AppTest.from_string("from ap_coder.webapp.settings import page_settings\npage_settings()",
                             default_timeout=TIMEOUT)  # fmt: skip
    _ok(at.run())
    at.selectbox(key="backup_choice").select(older.name)
    at.text_input(key="restore_confirm").input("RESTORE")
    _ok(at.run())
    _ok(at.button(key="restore").click().run())
    assert "Restored" in " ".join(t.value for t in at.toast)
    confirm = next(t for t in at.text_input if (t.key or "").startswith("restore_confirm"))
    assert confirm.value == ""
    assert next(b for b in at.button if b.key == "restore").disabled


def test_getting_started_counts_only_the_steps_that_are_needed(db):
    """The welcome screen's ring counted the optional chat model, so it read 1/6 where the Process page said 1 of 4
    (plus processing the first invoices), and could never be complete without that optional model."""
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    html = _html(at)
    assert "Chat model for GL suggestions" in html and "optional" in html
    assert "aria-label='1/5'" in html, html  # reading invoices done; GL, tax mapping, OvisOCR2, first invoices to do
    store = Store(db)
    load_sample_setup(store)
    html = _html(_ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run()))
    assert "aria-label='3/5'" in html
