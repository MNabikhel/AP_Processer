"""Sweep 4 (real-world testing): the findings Sweep 3 left, fixed."""

import datetime as dt
import io
import json
import subprocess
import sys

import pymupdf
from openpyxl import load_workbook

from ap_coder.ai import page_worker
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.erp import accruals, spend, stamp

from .conftest import SAMPLES

NORTHWIND = "northwind_ON_HST_NW-2026-0912"


def _approved_by_an_older_version(tmp_path):
    """An approved invoice whose final coding has no stored gl_distribution (approved by an older version)."""
    store = Store(tmp_path / "s.db")
    load_sample_setup(store)
    doc = json.loads((SAMPLES / "ground_truth" / f"{NORTHWIND}.json").read_text())
    assert "gl_distribution" not in doc
    invoice_id = store.add_invoice(SAMPLES / f"{NORTHWIND}.pdf", doc, {})
    store.approve_invoice(invoice_id, doc, "Jordan")
    assert not (store.get_invoice(invoice_id)["final_output"] or {}).get("gl_distribution")
    return store, invoice_id, doc


def test_an_invoice_approved_without_a_stored_distribution_is_in_the_spend_report(tmp_path):
    """B-217: spend.lines and the full data workbook build the posting lines again, as the exports do (B-151)."""
    store, invoice_id, doc = _approved_by_an_older_version(tmp_path)
    rows = spend.lines(store, dt.date(2020, 1, 1), dt.date(2030, 12, 31))
    assert {r.invoice_id for r in rows} == {invoice_id}
    assert round(sum(r.amount for r in rows), 2) == doc["subtotal"]  # the expense lines, net of the HST
    posting = load_workbook(io.BytesIO(spend.workbook(store, store.reference_data())))["Posting"]
    assert posting.max_row == len(doc["line_items"]) + 2  # the header, each line and the HST
    assert "2310" in {c.value for c in posting["F"]}  # the HST's own account (the store's tax setup)


def test_an_invoice_approved_without_a_stored_distribution_is_in_the_accrual_detail(tmp_path):
    """B-217: an approved invoice not yet exported is accrued by GL account even with no stored distribution."""
    store, _, doc = _approved_by_an_older_version(tmp_path)
    items = [a for a in accruals.build(store, dt.date(2026, 12, 31)) if a.source == accruals.NOT_IN_ERP]
    assert items and round(sum(a.amount for a in items), 2) == doc["subtotal"]
    assert all(a.gl_code for a in items)


def test_an_invoice_approved_without_a_stored_distribution_has_its_posting_on_the_stamp(tmp_path):
    """B-217: the stamped PDF's coding page lists the posting lines built again, with the tax setup's accounts."""
    store, invoice_id, doc = _approved_by_an_older_version(tmp_path)
    pdf = stamp.stamped_pdf(store.get_invoice(invoice_id), {}, store.tax_treatments())
    with pymupdf.open("pdf", pdf) as opened:
        coding = opened[opened.page_count - 1].get_text()
    assert f"{doc['grand_total']:,.2f}" in coding and "2310" in coding


def test_a_reading_mark_whose_process_id_another_program_now_has_goes_back_in_line(tmp_path):
    """B-218: a dashboard killed mid-read left its "being read" mark; its process id now belongs to another program.
    The mark notes when its process started, so the invoice is read again at once, not two hours later."""
    store = Store(tmp_path / "ap.db")
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started = page_worker._process_started(other.pid)
        assert started and page_worker._process_started(2**22 + 12345) == ""  # no such process
        readers = {31: f"{other.pid}:dead:{started}x", 32: f"{other.pid}:alive:{started}", 33: f"{other.pid}:old"}
        for invoice_id, reader in readers.items():
            store.queue_page_read(invoice_id, "new invoice")
            assert store.next_page_read(invoice_id, reader=reader)["reader"] == reader
        assert page_worker.release_interrupted(store) == 1
        # The program now under that id started at another time; a mark of its own and an older mark (no start
        # noted: by its process id, as before) stand.
        assert {i: store.page_read(i)["status"] for i in readers} == {31: "waiting", 32: "reading", 33: "reading"}
    finally:
        other.kill()
        other.wait()
    assert page_worker.READER_ID.count(":") == 2 and page_worker.reader_alive(page_worker.READER_ID) is False


def test_invoices_of_an_old_vendor_and_others_in_turn_share_the_suggestion_indexes(tmp_path, reference, monkeypatch):
    """B-219: past the memory read cap, an old vendor's invoice gets a list of its own (B-175); invoices of it and of
    other vendors in turn rebuilt the suggestion index each time (about 0.3 s at 50,000 lessons)."""
    from ap_coder.ai import suggest
    from ap_coder.core import store as store_mod

    store = Store(tmp_path / "ap.db")
    store.import_history([{"vendor_name": "Northwind IT Solutions Inc.", "description": "Dell Latitude laptop",
                           "gl_code": "6010", "amount": 100.0, "date": "2025-01-01"}])  # fmt: skip
    store.import_history([{"vendor_name": f"Zenith Freight {i}", "description": f"Freight charge lot {i}",
                           "gl_code": "5100", "amount": 10.0, "date": "2025-06-01"} for i in range(30)])  # fmt: skip
    monkeypatch.setattr(store_mod, "FEEDBACK_READ", 20)
    monkeypatch.setattr(suggest, "_MEMORY", suggest.RowsCache())
    builds = []
    build = suggest._build_memory
    monkeypatch.setattr(suggest, "_build_memory", lambda *a: builds.append(1) or build(*a))
    for vendor in ["Northwind IT Solutions Inc.", "Zenith Freight 29", "Northwind IT Solutions Inc.",
                   "Zenith Freight 28", "Northwind IT Solutions Inc."]:  # fmt: skip
        suggest.suggest_gl("Dell Latitude laptop", vendor, store.learning_rows(vendor), reference)
    assert len(builds) == 2  # Northwind's own list, and the shared one


def test_an_invoice_number_in_digit_groups_with_more_text_after_it_is_read_whole(tmp_path):
    """B-220: "No de facture 123 456 Page 1 de 1": the number is 123 456, not 123 (B-185 joined the groups only
    when nothing followed them on the line)."""
    from ap_coder.capture.reader import _id_from
    from tests.test_capture_edge_cases import _pdf
    from tests.test_sweep3_capture import _NO_NUMBER, _read

    assert _id_from(" 123 456 Date 2026-09-14", 0)[0] == "123 456"
    assert _id_from(": 12 345 678 Réf. client A-77", 0)[0] == "12 345 678"
    # Another figure after it, or a group that is not three digits: still the first number alone.
    assert [_id_from(t, 0)[0] for t in (" 123 456 2026-09-14", " 123 45 Date", " 123 4567 Date")] == ["123"] * 3
    lines = _NO_NUMBER + [(380, 120, "No de facture 123 456 Page 1 de 1")]
    fr = _read(_pdf(tmp_path, lines)).fields["invoice_number"]
    assert fr.value.replace(" ", "") == "123456", fr
