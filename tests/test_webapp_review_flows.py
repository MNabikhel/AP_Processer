"""Review-screen flows driven through the real pages (Streamlit AppTest): what is approved is what was on screen."""

import json

import pandas as pd
from streamlit.testing.v1 import AppTest

from ap_coder.store import APPROVED, REVIEW, Store, load_sample_setup

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


def test_bulk_approve_does_not_discard_a_correction_made_on_screen(db):
    store = Store(db)
    load_sample_setup(store)
    edited = _clean(store)
    other = _clean(store, "laurentides_QC_TPS_TVQ_SIL-4471")
    third = _clean(store, "chinook_AB_GST_CCO-26-10418")
    at = _open(other)  # only looked at: still approved in bulk
    at.session_state["open_invoice"] = edited
    _ok(at.run())
    at.text_input(key=f"inv{edited}_invoice_number").input("NW-2026-0912-A")  # the reviewer's correction
    _ok(at.run())
    _ok(next(b for b in at.button if b.label == "Queue").click().run())
    assert at.button(key="bulk_approve")
    _ok(at.button(key="bulk_approve").click().run())
    assert store.get_invoice(other)["status"] == store.get_invoice(third)["status"] == APPROVED
    inv = store.get_invoice(edited)
    # Approved "exactly as coded" would silently drop the correction still on its review screen: left for the
    # reviewer instead (or approved with it), never approved without it.
    assert inv["status"] == REVIEW or inv["final_output"]["invoice_number"] == "NW-2026-0912-A"


def test_a_rejected_invoice_reopens_without_the_edits_dropped_at_rejection(db):
    store = Store(db)
    load_sample_setup(store)
    invoice_id = _clean(store)
    key = f"inv{invoice_id}"
    at = _open(invoice_id)
    at.text_input(key=f"{key}_invoice_number").input("TYPO-123")
    _ok(at.run())
    at.text_input(key=f"{key}_reason").input("not our invoice")
    _ok(at.button(key=f"{key}_reject").click().run())
    _ok(at.button(key=f"reopen_{invoice_id}").click().run())  # Failed / rejected tab: rejected by mistake
    _ok(at.button(key=f"qopen_{invoice_id}").click().run())
    shown = at.text_input(key=f"{key}_invoice_number").value
    assert shown == store.get_invoice(invoice_id)["invoice_number"] == "NW-2026-0912"


def test_restoring_a_backup_forgets_the_edits_of_invoices_it_removes(db):
    """After a restore, invoice numbers are used again: a new invoice must not open with an old one's edits."""
    store = Store(db)
    load_sample_setup(store)
    backup = store.backup_now("manual")  # before any invoice
    first = _clean(store)
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages
    at = AppTest.from_string(
        "import streamlit as st\nfrom ap_coder.webapp.review import page_review\n"
        "from ap_coder.webapp.settings import page_settings\n"
        "page_settings() if st.session_state.get('page') == 'settings' else page_review()",
        default_timeout=TIMEOUT,
    )
    at.session_state["open_invoice"] = first
    _ok(at.run())
    at.text_input(key=f"inv{first}_vendor_name").input("Northwind (edited)")
    _ok(at.run())
    at.session_state["page"] = "settings"
    _ok(at.run())
    at.selectbox(key="backup_choice").select(backup.name)
    at.text_input(key="restore_confirm").input("RESTORE")
    _ok(at.run())
    _ok(at.button(key="restore").click().run())
    second = _clean(store, "laurentides_QC_TPS_TVQ_SIL-4471")
    assert second == first  # the restored database numbers invoices from where the backup was
    at.session_state["page"] = "review"
    at.session_state["open_invoice"] = second
    _ok(at.run())
    assert at.text_input(key=f"inv{second}_vendor_name").value == store.get_invoice(second)["vendor_name"]


def test_a_suggested_gl_codes_a_line_just_added(db):
    """A line the reviewer adds has no number in the grid yet: the suggestion's button must still code it."""
    store = Store(db)
    load_sample_setup(store)
    invoice_id = _clean(store)
    lines = pd.DataFrame(_gt()["line_items"])
    added = {c: None for c in lines.columns} | {"description": "Printer toner and copy paper", "amount": 0.0}
    key = f"inv{invoice_id}"
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    at.session_state[f"_draft_{key}_lines"] = pd.concat([lines, pd.DataFrame([added])], ignore_index=True)
    _ok(at.run())
    number = len(lines) + 1  # the number the new line gets
    button = next(b for b in at.button if (b.key or "").startswith(f"{key}_sugg_{number}_"))
    gl = button.key.rsplit("_", 1)[1]
    _ok(button.click().run())
    assert not [b for b in at.button if (b.key or "").startswith(f"{key}_sugg_{number}_")]  # coded: no suggestion
    assert gl in set(at.session_state[f"_draft_{key}_lines"]["predicted_gl_code"])


def _meanwhile(monkeypatch, method, before):
    """Someone else acts on the invoice between this screen being drawn and the button's action."""
    original = getattr(Store, method)

    def patched(self, invoice_id, *args, **kwargs):
        monkeypatch.setattr(Store, method, original)
        before(self, invoice_id)
        return original(self, invoice_id, *args, **kwargs)

    monkeypatch.setattr(Store, method, patched)


def _approved_by_sam(store, invoice_id):
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Sam")


def _exported_by_sam(store, invoice_id):
    _approved_by_sam(store, invoice_id)
    store.create_export_batch([invoice_id], "csv", actor="Sam")


def test_actions_on_an_invoice_someone_else_just_changed_show_a_message_not_an_error(db, monkeypatch):
    store = Store(db)
    load_sample_setup(store)
    for method, before, button, fill in [
        ("approve_invoice", _approved_by_sam, "approve", None),
        ("reject_invoice", _exported_by_sam, "reject", "reason"),
        ("park_invoice", _approved_by_sam, "park", "park_reason"),
    ]:
        invoice_id = _clean(store, invoice_number=f"N-{method}")
        key = f"inv{invoice_id}"
        at = _open(invoice_id)
        if fill:
            at.text_input(key=f"{key}_{fill}").input("x")
            _ok(at.run())
        _meanwhile(monkeypatch, method, before)
        _ok(at.button(key=f"{key}_{button}").click().run())
        assert any("someone else" in str(t.proto.body) for t in at.get("toast")), method


def test_second_approval_on_an_invoice_someone_else_just_handled(db, monkeypatch):
    store = Store(db)
    load_sample_setup(store)
    store.set_setting("approval_limit", "100")
    invoice_id = _clean(store)
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Sam")
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    _meanwhile(monkeypatch, "final_approve", lambda s, i: s.final_approve(i, "Lee"))
    _ok(at.button(key=f"second_ok_{invoice_id}").click().run())
    assert any("someone else" in str(t.proto.body) for t in at.get("toast"))
