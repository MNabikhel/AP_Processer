"""Vendor master and payment-fraud / duplicate signals."""

import copy

import pytest

from ap_coder.config import Settings
from ap_coder.pipeline import finalise_coding
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store
from ap_coder.vendors import norm_invoice_number


def _codes(store, doc, reference, exclude=None):
    _, report = finalise_coding(InvoiceCoding.model_validate(doc), reference, Settings(), store=store,
                                exclude_invoice_id=exclude)  # fmt: skip
    return {i.code: i.severity for i in report.issues}, report


def _approved(store, doc, tmp_path, name):
    invoice_id = store.add_invoice(tmp_path / name, doc, {})
    store.approve_invoice(invoice_id, doc, "jane")
    return invoice_id


def _variant(doc, number, total=None, date=None, gst=None):
    d = copy.deepcopy(doc)
    d["invoice_number"] = number
    if date:
        d["invoice_date"] = date
    if gst is not None:
        d["gst_hst_registration_number"] = gst
    if total is not None:
        factor = total / d["grand_total"]
        for li in d["line_items"]:
            li["amount"] = round(li["amount"] * factor, 2)
            li["unit_price"] = round(li["amount"] / li["quantity"], 2)
        d["subtotal"] = round(sum(li["amount"] for li in d["line_items"]), 2)
        d["tax_lines"][0]["taxable_amount"] = d["subtotal"]
        d["tax_lines"][0]["tax_amount"] = round(d["subtotal"] * d["tax_lines"][0]["rate"], 2)
        d["tax_total"] = d["tax_lines"][0]["tax_amount"]
        d["grand_total"] = round(d["subtotal"] + d["tax_total"], 2)
    return d


@pytest.mark.parametrize(("a", "b"), [("INV-00123", "123"), ("#123", "inv 123"), ("Facture no 0045", "45")])
def test_invoice_numbers_match_regardless_of_formatting(a, b):
    assert norm_invoice_number(a) == norm_invoice_number(b)


def test_formatting_variants_count_as_duplicates(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    store.add_invoice(tmp_path / "a.pdf", _variant(ground_truth, "NW-2026-0912"), {})
    codes, _ = _codes(store, _variant(ground_truth, "NW 2026 0912"), reference)
    assert codes.get("DUPLICATE_INVOICE") == "error"


def test_first_invoice_from_a_vendor_is_noted_without_penalty(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    other = {**ground_truth, "vendor_name": "Some Other Vendor Ltd."}
    store.add_invoice(tmp_path / "o.pdf", other, {})
    codes, report = _codes(store, ground_truth, reference)
    assert codes.get("VENDOR_NEW") == "info"
    assert report.adjusted_confidence == report.model_confidence  # info costs nothing


def test_changed_gst_number_is_flagged(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    _approved(store, _variant(ground_truth, "A-1", date="2026-06-01"), tmp_path, "a.pdf")
    codes, _ = _codes(store, _variant(ground_truth, "A-2", gst="987654321 RT0001"), reference)
    assert codes.get("VENDOR_TAX_NUMBER_CHANGED") == "warning"
    same, _ = _codes(store, _variant(ground_truth, "A-3", gst="123456789RT0001"), reference)
    assert "VENDOR_TAX_NUMBER_CHANGED" not in same  # spacing differences don't count


def test_unusual_amount_and_same_amount_different_number(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    for i, date in enumerate(["2026-05-01", "2026-06-01", "2026-07-01"]):
        _approved(store, _variant(ground_truth, f"R-{i}", total=1000.0, date=date), tmp_path, f"r{i}.pdf")
    codes, _ = _codes(store, _variant(ground_truth, "BIG-1", total=9000.0, date="2026-08-01"), reference)
    assert codes.get("AMOUNT_UNUSUAL") == "warning"
    codes, _ = _codes(store, _variant(ground_truth, "NEW-9", total=1000.0, date="2026-07-15"), reference)
    assert codes.get("POSSIBLE_DUPLICATE_AMOUNT") == "warning"
    far, _ = _codes(store, _variant(ground_truth, "NEW-10", total=1000.0, date="2026-12-15"), reference)
    assert "POSSIBLE_DUPLICATE_AMOUNT" not in far  # monthly fees months apart are normal


def test_vendor_on_hold_blocks(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    _approved(store, _variant(ground_truth, "A-1"), tmp_path, "a.pdf")
    store.save_vendor("northwind it solutions", "Northwind IT Solutions Inc.", "on_hold", notes="bank change pending",
                      actor="jane")  # fmt: skip
    codes, report = _codes(store, _variant(ground_truth, "A-2"), reference)
    assert codes.get("VENDOR_ON_HOLD") == "error" and report.requires_review
    assert store.events(actions=["vendor_updated"])[0]["detail"]["status"] == "on_hold"
    summary = store.vendor_summaries()[0]
    assert summary["status"] == "on_hold" and summary["approved"] == 1 and summary["lessons"] == 5


def test_expected_gst_number_from_the_vendor_master_wins(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    _approved(store, _variant(ground_truth, "A-1"), tmp_path, "a.pdf")
    store.save_vendor("northwind it solutions", "Northwind IT Solutions Inc.", expected_gst="111111111RT0001")
    codes, _ = _codes(store, _variant(ground_truth, "A-2"), reference)
    assert codes.get("VENDOR_TAX_NUMBER_CHANGED") == "warning"
