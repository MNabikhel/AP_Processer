"""ERP export batches (each invoice once, re-downloadable, undoable) and bulk approval of clean invoices."""

import csv
import io

import pytest
from openpyxl import load_workbook

from ap_coder import exports
from ap_coder.bulk import bulk_approve, clean_candidates
from ap_coder.config import Settings
from ap_coder.demo import load_demo
from ap_coder.memory import vendor_key
from ap_coder.store import APPROVED, REVIEW, Store


@pytest.fixture
def demo_store(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_demo(store)
    return store


def test_export_batch_lifecycle(demo_store):
    store = demo_store
    ready = store.unexported_approved()
    assert len(ready) == 2  # the two demo invoices approved "last week"
    batch = store.create_export_batch([i["id"] for i in ready], "xlsx", actor="jane")
    assert store.unexported_approved() == []
    assert sorted(store.batch_invoice_ids(batch)) == sorted(i["id"] for i in ready)
    with pytest.raises(ValueError):
        store.create_export_batch([i["id"] for i in ready], "csv")  # never exported twice
    assert len(store.events(actions=["exported"])) == 2
    assert store.undo_export_batch(batch, actor="jane") == 2
    assert len(store.unexported_approved()) == 2
    assert store.export_batches()[0]["undone_by"] == "jane"


def test_export_files_balance_to_the_invoices(demo_store):
    store = demo_store
    invoices = [store.get_invoice(i["id"]) for i in store.unexported_approved()]
    data, name, _ = exports.build("xlsx", invoices, {"6010": "IT Hardware"}, batch=7)
    assert name == "ap_coder_export_7.xlsx"
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Invoices", "GL lines", "By GL account"]
    header = [c.value for c in wb["GL lines"][1]]
    amount_col = header.index("Amount")
    lines_total = round(sum(r[amount_col] for r in wb["GL lines"].iter_rows(min_row=2, values_only=True)), 2)
    assert lines_total == round(sum(i["final_output"]["grand_total"] for i in invoices), 2)
    csv_data, csv_name, _ = exports.build("csv", invoices, batch=7)
    rows = list(csv.DictReader(io.StringIO(csv_data.decode("utf-8-sig"))))
    assert csv_name.endswith(".csv") and len(rows) == wb["GL lines"].max_row - 1
    assert {r["Batch"] for r in rows} == {"7"}


def test_bulk_approve_only_takes_invoices_that_are_still_clean(demo_store):
    store = demo_store
    candidates = clean_candidates(store)
    assert candidates and all(not c["requires_review"] for c in candidates)
    # One of them gets a new problem after processing: its vendor goes on hold.
    victim = candidates[0]
    store.save_vendor(vendor_key(victim["vendor_name"]), victim["vendor_name"], "on_hold")
    result = bulk_approve(store, store.reference_data(), Settings(), [c["id"] for c in candidates], "jane")
    assert result["skipped"] and result["skipped"][0][0] == victim["id"] and "VENDOR_ON_HOLD" in result["skipped"][0][1]
    assert len(result["approved"]) == len(candidates) - 1
    for invoice_id in result["approved"]:
        assert store.get_invoice(invoice_id)["status"] == APPROVED
    assert store.get_invoice(victim["id"])["status"] == REVIEW
    approved_event = store.events(result["approved"][0], actions=["approved"])[0]
    assert approved_event["detail"]["bulk"] is True and approved_event["actor"] == "jane"


def test_bulk_approve_skips_an_invoice_a_colleague_parks_meanwhile(demo_store, monkeypatch):
    """One invoice parked (or approved) by someone else while the loop checks the others is skipped with the
    reason; the rest are still approved and the result is not lost."""
    from ap_coder import bulk

    store = demo_store
    ids = [c["id"] for c in clean_candidates(store)]
    assert len(ids) >= 2
    real = bulk.finalise_coding

    def finalise(coding, *args, **kwargs):
        out = real(coding, *args, **kwargs)
        if kwargs.get("exclude_invoice_id") == ids[1]:  # parked between its check and its approval
            store.park_invoice(ids[1], "bob", "waiting on buyer")
        return out

    monkeypatch.setattr(bulk, "finalise_coding", finalise)
    result = bulk_approve(store, store.reference_data(), Settings(), ids, "jane")
    assert [i for i, _ in result["skipped"]] == [ids[1]] and "no longer in the queue" in result["skipped"][0][1]
    assert sorted(result["approved"]) == sorted(i for i in ids if i != ids[1])
    assert store.get_invoice(ids[1])["status"] != APPROVED
