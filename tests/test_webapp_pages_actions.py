"""Buttons on the dashboard pages, clicked through the real page (Streamlit AppTest)."""

from ap_coder.store import Store, load_sample_purchase_orders

from .test_dashboard_pages import _ok, _page
from .test_dashboard_pages import db as db  # the fixture (a fresh database per test)


def test_close_and_reopen_a_purchase_order(db):
    store = Store(db)
    load_sample_purchase_orders(store)
    at = _ok(_page("purchase_orders", "page_purchase_orders").run())
    po_key = at.selectbox(key="po_choice").value
    _ok(at.button(key=f"po_toggle_{po_key}").click().run())
    assert store.purchase_order(po_key)["status"] == "closed"
    assert at.selectbox(key="po_choice").value == po_key  # still in view, now under All
    _ok(at.button(key=f"po_toggle_{po_key}").click().run())
    assert store.purchase_order(po_key)["status"] == "open"
