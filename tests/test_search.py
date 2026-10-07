"""Find an invoice."""

import json

from ap_coder import search
from ap_coder.demo import load_demo
from ap_coder.store import Store

from .conftest import SAMPLES


def test_find_by_vendor_number_po_and_amount(tmp_path):
    store = Store(tmp_path / "s.db")
    load_demo(store)
    assert [h.row["invoice_number"] for h in search.find(store, "northwind 0912")] == ["NW-2026-0912"]
    assert search.find(store, "NW 2026 0912")[0].row["invoice_number"] == "NW-2026-0912"
    assert search.find(store, "18,017.85")[0].row["invoice_number"] == "NW-2026-0912"
    assert {h.row["invoice_number"] for h in search.find(store, "PO-88213")} >= {"NW-2026-0912"}
    assert search.find(store, "red river")[0].row["vendor_name"].startswith("Red River")
    assert search.find(store, "northwind 99999") == []  # every part must match
    assert search.find(store, "  ") == []


def test_where_each_invoice_stands(tmp_path):
    store = Store(tmp_path / "s.db")
    doc = json.loads((SAMPLES / "ground_truth" / "chinook_AB_GST_CCO-26-10418.json").read_text())
    a = store.add_invoice(tmp_path / "a.pdf", doc, {})
    assert search.where(search.find(store, "chinook")[0].row).startswith("In the review queue")
    store.park_invoice(a, "Jane", "buyer to confirm", "2026-10-20")
    assert "buyer to confirm; follow up 2026-10-20" in search.where(search.find(store, "chinook")[0].row)
    store.unpark_invoice(a, "Jane")
    store.approve_invoice(a, doc, "Jane")
    assert "not exported" in search.where(search.find(store, "chinook")[0].row)
    store.create_export_batch([a], "csv")
    text = search.where(search.find(store, "chinook")[0].row)
    assert "exported in batch 1 on 20" in text and "approved by Jane" in text
    b = store.add_invoice(tmp_path / "b.pdf", {**doc, "invoice_number": "X-9"}, {})
    store.reject_invoice(b, "Sam", "duplicate")
    assert search.where(search.find(store, "X-9")[0].row).endswith(": duplicate")


def test_the_erp_register_is_searched_too(tmp_path):
    store = Store(tmp_path / "s.db")
    store.import_erp_register(
        [{"vendor_name": "Acme Ltd", "invoice_number": "A-77", "invoice_date": "2026-01-05", "total": 12.5}]
    )
    (hit,) = search.find(store, "acme 12.50")
    assert hit.source == "ERP" and hit.row["invoice_number"] == "A-77"
