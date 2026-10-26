"""The invoice directory's quick search."""

import json

from ap_coder.core.store import Store
from ap_coder.erp import search

from .conftest import SAMPLES, copy_database


def test_find_by_vendor_number_po_and_amount(tmp_path, demo_template):
    store = copy_database(demo_template, tmp_path / "s.db")
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


def test_french_amounts_zeros_accents_and_punctuation(tmp_path, demo_template):
    store = copy_database(demo_template, tmp_path / "s.db")

    def numbers(query):
        return [h.row["invoice_number"] for h in search.find(store, query)]

    assert numbers("18 017,85") == ["NW-2026-0912"]  # thousands separated by a space, decimal comma
    assert numbers("0047") == ["CN-2026-0047"]  # leading zeros kept in a part of a number
    assert numbers("creative") == ["ACMR-2026-1187"]  # Créative
    assert len(numbers('"northwind"?')) == 2


def test_vendor_name_with_digits(tmp_path):
    store = Store(tmp_path / "s.db")
    doc = json.loads((SAMPLES / "ground_truth" / "chinook_AB_GST_CCO-26-10418.json").read_text())
    store.add_invoice(tmp_path / "a.pdf", {**doc, "vendor_name": "Acme 3M Supplies"}, {})
    assert [h.row["vendor_name"] for h in search.find(store, "acme 3m")] == ["Acme 3M Supplies"]


def test_an_exported_invoice_is_not_shown_again_from_the_erp_register(tmp_path):
    store = Store(tmp_path / "s.db")
    doc = json.loads((SAMPLES / "ground_truth" / "chinook_AB_GST_CCO-26-10418.json").read_text())
    a = store.add_invoice(tmp_path / "a.pdf", doc, {})
    store.approve_invoice(a, doc, "Jane")
    store.create_export_batch([a], "csv")
    store.import_erp_register([{"vendor_name": "CHINOOK COURIER", "invoice_number": doc["invoice_number"],
                                "invoice_date": doc["invoice_date"], "total": doc["grand_total"]}])  # fmt: skip
    assert [h.source for h in search.find(store, doc["invoice_number"])] == ["AP Coder"]


def test_another_vendors_erp_bill_with_the_same_number_and_amount_is_still_found(tmp_path):
    """Only the exported invoice's own ERP entry is hidden: another vendor's bill (or a credit) with the same
    number and amount is a different document."""
    store = Store(tmp_path / "s.db")
    doc = {**json.loads((SAMPLES / "ground_truth" / "chinook_AB_GST_CCO-26-10418.json").read_text()),
           "invoice_number": "1001", "grand_total": 500.0}  # fmt: skip
    a = store.add_invoice(tmp_path / "a.pdf", doc, {})
    store.approve_invoice(a, doc, "Jane")
    store.create_export_batch([a], "csv")
    store.import_erp_register([
        {"vendor_name": "Other Supplies Ltd", "invoice_number": "1001", "invoice_date": "2026-09-01", "total": 500.0},
        {"vendor_name": "Chinook Courier", "invoice_number": "1001", "invoice_date": "2026-09-05", "total": -500.0},
    ])  # fmt: skip
    found = sorted((h.source, h.row["vendor_name"], h.row["grand_total"]) for h in search.find(store, "1001"))
    assert found == [
        ("AP Coder", doc["vendor_name"], 500.0), ("ERP", "Chinook Courier", -500.0),
        ("ERP", "Other Supplies Ltd", 500.0),
    ]  # fmt: skip
    assert [h.source for h in search.find(store, "other supplies")] == ["ERP"]
