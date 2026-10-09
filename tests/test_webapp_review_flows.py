"""Review-screen flows driven through the real pages (Streamlit AppTest): what is approved is what was on screen."""

import json

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
