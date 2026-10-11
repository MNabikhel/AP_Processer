"""Dashboard fixes driven through the real pages (Streamlit AppTest): bulk approval of a reopened invoice, the
"approve anyway" tick, lines without a cost center, the training-data ZIP, the fixed-rules grid after a restore,
vendor and suggestion widget keys, and vendor names shown as Markdown."""

import copy
import io
import json
import zipfile

import pandas as pd
from streamlit.testing.v1 import AppTest

from ap_coder.rules import Rule
from ap_coder.store import APPROVED, Store, load_sample_setup

from .conftest import SAMPLE_STEM, SAMPLES
from .test_dashboard_pages import APP, TIMEOUT, _ok
from .test_dashboard_pages import db as db  # the fixture (a fresh database per test)


def _gt(stem=SAMPLE_STEM):
    return json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text())


def _clean(store, stem=SAMPLE_STEM, **changes):
    doc = {**_gt(stem), **changes}
    return store.add_invoice(SAMPLES / f"{stem}.pdf", doc, {"requires_review": False, "adjusted_confidence": 0.97})


def _open(invoice_id):
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    return _ok(at.run())


def _page(module, function):
    return AppTest.from_string(
        f"from ap_coder.webapp.{module} import {function}\n{function}()", default_timeout=TIMEOUT
    )


def _html(at):
    return " ".join(str(h.proto.body) for h in at.get("html"))


def test_bulk_approve_keeps_an_earlier_approvers_corrections(db):
    """A reopened invoice is shown with what its approver approved: bulk approval posts that, not the AI's."""
    store = Store(db)
    load_sample_setup(store)
    reopened = _clean(store)
    _clean(store, "chinook_AB_GST_CCO-26-10418")
    _clean(store, "laurentides_QC_TPS_TVQ_SIL-4471")
    fixed = copy.deepcopy(store.get_invoice(reopened)["ai_output"])
    assert fixed["line_items"][0]["predicted_gl_code"] != "1510"
    fixed["line_items"][0]["predicted_gl_code"] = "1510"
    store.approve_invoice(reopened, fixed, "Jane")
    store.reopen(reopened, "Jane", "check the cost center")
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    _ok(at.button(key="bulk_approve").click().run())
    inv = store.get_invoice(reopened)
    assert inv["status"] == APPROVED
    assert inv["final_output"]["line_items"][0]["predicted_gl_code"] == "1510"


def test_approve_anyway_asks_again_when_a_new_error_appears(db):
    store = Store(db)
    load_sample_setup(store)
    doc = _gt()
    doc["line_items"][0]["predicted_cost_center"] = "CC999"  # one error: a cost center not in the list
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", doc, {"requires_review": True})
    key = f"inv{invoice_id}"

    def override(at):
        return next(c for c in at.checkbox if (c.key or "").startswith(f"{key}_override"))

    at = _open(invoice_id)
    assert "1 error" in override(at).label and at.button(key=f"{key}_approve").disabled
    _ok(override(at).check().run())
    assert not at.button(key=f"{key}_approve").disabled
    _ok(at.number_input(key=f"{key}_grand_total").set_value(99999.0).run())  # a second error: totals differ
    assert "1 error" not in override(at).label and not override(at).value
    assert at.button(key=f"{key}_approve").disabled


def test_a_line_without_a_cost_center_is_not_approved(db):
    """Where cost centers are used, UNASSIGNED is never posted: like a line without a GL account."""
    store = Store(db)
    load_sample_setup(store)
    doc = _gt()
    doc["line_items"][0]["predicted_cost_center"] = "UNASSIGNED"
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", doc, {"requires_review": True})
    at = _open(invoice_id)
    assert at.button(key=f"inv{invoice_id}_approve").disabled
    assert "Pick a cost center for line 1 to approve" in _html(at)
    assert not [c for c in at.checkbox if "override" in (c.key or "")]  # not something to approve anyway


def test_training_zip_is_made_again_after_a_reopened_invoice_is_corrected(db):
    store = Store(db)
    load_sample_setup(store)
    invoice_id = _clean(store)
    store.approve_invoice(invoice_id, dict(_gt(), invoice_number="WRONG-1"), "Jane")
    at = _ok(_page("learning", "page_learning").run())
    _ok(at.button(key="training_zip_make").click().run())
    assert any("training_zip_download" in (b.proto.id or "") for b in at.get("download_button"))
    store.reopen(invoice_id, "Jane", "typo")  # corrected and approved again: as many approved invoices as before
    store.approve_invoice(invoice_id, dict(_gt(), invoice_number="RIGHT-1"), "Jane")
    _ok(at.run())
    assert not any("training_zip_download" in (b.proto.id or "") for b in at.get("download_button"))
    _ok(at.button(key="training_zip_make").click().run())
    with zipfile.ZipFile(io.BytesIO(at.session_state["training_zip"][1])) as z:
        text = " ".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist() if not n.endswith(".png"))
    assert "RIGHT-1" in text and "WRONG-1" not in text


def test_rules_grid_shows_the_rules_restored_not_the_old_draft(db):
    store = Store(db)
    load_sample_setup(store)
    store.save_coding_rules([Rule("Purolator", "", "6900")])
    at = _ok(_page("accounts", "page_accounts").run())
    store.save_coding_rules([Rule("", "freight", "6800"), Rule("Bell", "", "6200")])  # as a restored backup does
    _ok(at.run())
    assert [r["gl_code"] for r in at.session_state["_base_rules_grid"].to_dict("records")] == ["6800", "6200"]
    _ok(at.button(key="rules_save").click().run())
    assert [(r.vendor, r.contains, r.gl_code) for r in store.coding_rules()] == [("", "freight", "6800"),
                                                                                  ("Bell", "", "6200")]  # fmt: skip


def test_restoring_a_backup_shows_its_rules_in_the_rules_grid(db):
    store = Store(db)
    load_sample_setup(store)
    store.save_coding_rules([Rule("Purolator", "", "6900")])
    backup = store.backup_now("manual")
    store.save_coding_rules([Rule("Bell", "", "6200")])
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages
    at = AppTest.from_string(
        "import streamlit as st\nfrom ap_coder.webapp.accounts import page_accounts\n"
        "from ap_coder.webapp.settings import page_settings\n"
        "page_settings() if st.session_state.get('page') == 'settings' else page_accounts()",
        default_timeout=TIMEOUT,
    )
    _ok(at.run())
    at.session_state["page"] = "settings"
    _ok(at.run())
    at.selectbox(key="backup_choice").select(backup.name)
    at.text_input(key="restore_confirm").input("RESTORE")
    _ok(at.run())
    _ok(at.button(key="restore").click().run())
    at.session_state["page"] = "accounts"
    _ok(at.run())
    assert [r["vendor"] for r in at.session_state["_base_rules_grid"].to_dict("records")] == ["Purolator"]
    _ok(at.button(key="rules_save").click().run())
    assert [(r.vendor, r.gl_code) for r in Store(db).coding_rules()] == [("Purolator", "6900")]


def test_vendors_keyed_list_or_pick_open(db):
    store = Store(db)
    load_sample_setup(store)
    for name in ("List Corp", "Pick Ltd."):
        store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", dict(_gt(), vendor_name=name, invoice_number=name), {})
    at = _ok(_page("vendors", "page_vendors").run())
    for key in ("list", "pick"):
        _ok(at.selectbox(key="vendor_open").select(key).run())


def test_suggestions_for_two_lines_with_the_same_number(db):
    store = Store(db)
    load_sample_setup(store)
    doc = _gt()
    doc["line_items"][1]["line_number"] = 1  # as read from the invoice
    for li in doc["line_items"][:2]:
        li["predicted_gl_code"] = "UNASSIGNED"
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", doc, {})
    key = f"inv{invoice_id}"
    at = _open(invoice_id)
    second = [b for b in at.button if (b.key or "").startswith(f"{key}_sugg_1_1_")]
    assert second and [b for b in at.button if (b.key or "").startswith(f"{key}_sugg_1_0_")]
    gl = second[0].key.rsplit("_", 1)[1]
    _ok(second[0].click().run())
    lines = pd.DataFrame(at.session_state[f"_draft_{key}_lines"])
    assert list(lines["predicted_gl_code"][:2]) == ["UNASSIGNED", gl]  # the line clicked, not both


def test_vendor_names_are_shown_as_text_not_markdown(db, monkeypatch):
    store = Store(db)
    load_sample_setup(store)
    name = "Price $5 & $10 *Wholesale*"
    same = {
        **_gt(),
        "vendor_name": name,
        "line_items": [{**li, "predicted_gl_code": "6010"} for li in _gt()["line_items"]],
    }
    store.approve_invoice(store.add_invoice(SAMPLES / "a.pdf", same, {}), same, "Jane")
    at = _ok(_page("accounts", "page_accounts").run())
    _ok(at.button(key="rule_add_0").click().run())
    assert [t.value for t in at.toast] == ["Rule added: Price \\$5 & \\$10 \\*Wholesale\\* → 6010."]

    ids = [store.add_invoice(SAMPLES / f"s{n}.pdf", _gt(), {"requires_review": False}) for n in range(30)]
    outcomes = [{"field": f"f{f}", "ai_value": "a", "final_value": "a", "correct": True} for f in range(13)]
    for n, invoice_id in enumerate(ids):
        store.record_outcomes("id:V1", invoice_id, outcomes, display_name=name, at=f"2026-01-01T00:00:{n:02d}")
    at = _ok(_page("learning", "page_learning").run())
    assert any("**Price \\$5 & \\$10 \\*Wholesale\\***" in m.value for m in at.markdown)
    _ok(at.button(key=next(b.key for b in at.button if (b.key or "").startswith("sup_hold_"))).click().run())
    assert [t.value for t in at.toast] == [
        "Price \\$5 & \\$10 \\*Wholesale\\* is kept supervised: every invoice is reviewed."
    ]
