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


def test_the_reading_tab_does_not_say_ovisocr2_is_missing_while_lm_studio_is_down(db):
    """With LM Studio not running, Settings → Reading said "Not in LM Studio" and "No self-test: OvisOCR2 isn't in
    LM Studio", while the steps below it (rightly) said "start LM Studio first to check": nobody knows yet whether
    OvisOCR2 is there, and IT went looking for model files that were in place."""
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages
    at = _ok(AppTest.from_string("from ap_coder.webapp.settings import page_settings\npage_settings()",
                                 default_timeout=TIMEOUT).run())  # fmt: skip
    html = _html(at)
    assert "start LM Studio first to check" in html  # LM Studio is not running here
    assert "Not in LM Studio" not in html and "OvisOCR2 isn&#x27;t in LM Studio" not in html
    assert "No self-test: LM Studio isn&#x27;t running" in html
    assert at.button(key="rd_selftest").disabled


def test_the_vendor_master_duplicates_warning_is_about_the_last_import(db):
    """The "appear more than once in the file" warning stayed after the corrected file was imported, naming vendors
    the new file no longer has twice."""
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages
    at = _ok(AppTest.from_string("from ap_coder.webapp.vendors import page_vendors\npage_vendors()",
                                 default_timeout=TIMEOUT).run())  # fmt: skip
    head = "vendor_id,vendor_name,payment_terms\n"
    twice = head + "V1,Northwind IT Solutions Inc.,Net 30\nV2,Northwind IT Solutions Inc,Net 30\nV3,Chinook,Net 15\n"
    at.file_uploader(key="vm_upload").upload("vendors.csv", twice.encode(), "text/csv")
    _ok(at.run())
    _ok(at.button(key="vm_import").click().run())
    assert any(w.value.startswith("1 vendor appears more than once") for w in at.warning)
    once = head + "V1,Northwind IT Solutions Inc.,Net 30\nV3,Chinook,Net 15\n"
    at.file_uploader(key="vm_upload").upload("vendors_fixed.csv", once.encode(), "text/csv")
    _ok(at.run())
    _ok(at.button(key="vm_import").click().run())
    assert not any("more than once" in w.value for w in at.warning), [w.value for w in at.warning]


def test_tables_inside_a_wrapper_scroll_in_their_card_at_a_narrow_width(db):
    """At a phone's or a narrow window's width the GL posting preview (a table inside <div class='rvw-gl'>) spilled
    out of its card and the whole review screen scrolled sideways: style.css lets only a table that is the direct
    child of st.html scroll on its own."""
    import re

    from ap_coder.webapp.common import ASSETS

    store = Store(db)
    load_sample_setup(store)
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", json.loads(
        (SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text()), {"requires_review": True})  # fmt: skip
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    html = _html(_ok(at.run()))
    wrappers = set(re.findall(r"<div class='([\w-]+)'><table class='apc-table'", html))
    assert "rvw-gl" in wrappers
    css = (ASSETS / "style.css").read_text(encoding="utf-8") + (ASSETS / "review.css").read_text(encoding="utf-8")
    for name in wrappers:
        assert re.search(rf"\.{name}\s*\{{[^}}]*overflow-x:\s*auto", css), name


def test_insights_charge_no_cloud_cost_for_invoices_read_and_coded_on_this_computer(db, monkeypatch):
    """The offline build sends nothing to Azure, yet Insights priced the tokens LM Studio (or the demo) recorded at
    Azure's rates: "Cloud cost / invoice $0.040 · Azure, measured on 10 invoices", and a cloud cost taken off the
    business case's net benefit."""
    from ap_coder.demo import load_demo
    from ap_coder.insights import compute

    store = Store(db)
    load_demo(store)
    assert compute(store)["cost_per_invoice_cad"] is None
    assert compute(store)["projection"]["azure_cost"] == 0
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages
    at = _ok(AppTest.from_string("from ap_coder.webapp.insights import page_insights\npage_insights()",
                                 default_timeout=TIMEOUT).run())  # fmt: skip
    html = _html(at)
    assert "read and coded on this computer" in html and "Azure, measured" not in html
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")  # a developer build that may use Azure: its cost is measured
    assert compute(store)["cost_per_invoice_cad"] > 0


def _clean(store, stem=SAMPLE_STEM, **changes):
    doc = {**json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text()), **changes}
    return store.add_invoice(SAMPLES / f"{stem}.pdf", doc, {"requires_review": False, "adjusted_confidence": 0.97})


def test_counts_of_one_read_as_sentences(db):
    """Seen in the browser: "1 wait for a second approval" after a bulk approval, "Pick a cost center for line 1, 2,
    3, 4" on the approve bar, "1 invoice cannot go to JD Edwards yet and are left out" on Exports."""
    store = Store(db)
    load_sample_setup(store)
    store.set_setting("approval_limit", "5000.00")
    _clean(store)  # 18,017.85 CAD: over the limit
    _clean(store, "laurentides_QC_TPS_TVQ_SIL-4471")  # 2,759.40 CAD
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    _ok(at.button(key="bulk_approve").click().run())
    assert "Approved 2 invoices; 1 waits for a second approval." in [t.value for t in at.toast]

    doc = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    for li in doc["line_items"]:
        li["predicted_cost_center"] = "UNASSIGNED"
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", {**doc, "invoice_number": "NW-2"},
                                   {"requires_review": True})  # fmt: skip
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    numbers = ", ".join(str(li["line_number"]) for li in doc["line_items"])
    assert f"Pick a cost center for lines {numbers} to approve" in _html(_ok(at.run()))

    at = _ok(AppTest.from_string("from ap_coder.webapp.exports import page_exports\npage_exports()",
                                 default_timeout=TIMEOUT).run())  # fmt: skip
    _ok(at.radio(key="export_format").set_value("jde").run())
    warned = " ".join(w.value for w in at.warning)
    assert "1 invoice cannot go to JD Edwards yet and is left out of the batch" in warned, warned


def test_an_activity_search_that_finds_nothing_says_so(db):
    """Searching the Activity log for something it doesn't hold (or picking a filter with no events, e.g. Backups
    before any backup) crashed the page: "StreamlitMissingRequiredParameterError: The body parameter is required"
    (an empty timeline is no HTML). The search is kept for the session, so the page broke on every visit."""
    store = Store(db)
    load_sample_setup(store)
    _clean(store)
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages
    at = _ok(AppTest.from_string("from ap_coder.webapp.activity import page_activity\npage_activity()",
                                 default_timeout=TIMEOUT).run())  # fmt: skip
    at.text_input(key="activity_query").input("nothing like this")
    _ok(at.run())
    assert "No events match" in _html(at)
    at.text_input(key="activity_query").input("")
    _ok(at.run())
    _ok(at.button_group(key="activity_group").set_value("Backups").run())


def test_getting_started_counts_only_the_steps_that_are_needed(db, monkeypatch):
    """The welcome screen's ring counted the optional chat model, so it read 1/6 where the Process page said 1 of 4
    (plus processing the first invoices), and could never be complete without that optional model. OCR is taken as
    installed (CI's test jobs run without it)."""
    from ap_coder.webapp import process

    monkeypatch.setattr(process, "ocr_available", lambda: True)
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    html = _html(at)
    assert "Chat model for GL suggestions" in html and "optional" in html
    assert "aria-label='1/5'" in html, html  # reading invoices done; GL, tax mapping, OvisOCR2, first invoices to do
    store = Store(db)
    load_sample_setup(store)
    html = _html(_ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run()))
    assert "aria-label='3/5'" in html
