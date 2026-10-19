"""Sweep 3, the ai folder: credit notes, learning past the history cap, banking lines, the page reader's test mark,
unlisted charges and the files a folder of invoices holds."""

import datetime as dt
import json
import subprocess
import sys
from dataclasses import replace

from ap_coder.ai import page_worker
from ap_coder.ai.offline_coder import bank_details, code_from_capture
from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.capture.types import VERIFIED, CaptureResult, FieldResult, LineReading
from ap_coder.coding.validation import validate_coding
from ap_coder.coding.vendors import same_bank_account
from ap_coder.core import store as store_mod
from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from tests.conftest import SAMPLE_STEM, SAMPLES

HEADER = {"vendor_name": "Northwind", "invoice_number": "CN-1", "invoice_date": "2026-09-12", "currency": "CAD"}


def _capture(lines=(), **values):
    return CaptureResult(
        fields={k: FieldResult(k, v, 0.95, VERIFIED) for k, v in {**HEADER, **values}.items()},
        line_items=[LineReading(d, q, u, a) for d, q, u, a in lines],
    )


def test_a_credit_note_with_positive_lines_and_negative_totals_codes_the_lines_as_credits(reference):
    # The lines print 1,450 and 600; the subtotal -2,050: the lines are the credit, not a -4,100 charge beside them.
    capture = _capture([("Returned laptop", 1, 1450.0, 1450.0), ("Consulting hours cancelled", 4, 150.0, 600.0)],
                       subtotal=-2050.0, hst_amount=-266.5, tax_total=-266.5, grand_total=-2316.5)  # fmt: skip
    text = "Northwind\nToronto ON M5H 1A1\nCREDIT NOTE\nBill to:\nToronto ON M5J 2J5\n"
    coding = code_from_capture(capture, reference, [], text=text).coding
    assert [(li.quantity, li.unit_price, li.amount) for li in coding.line_items] == [
        (1, -1450.0, -1450.0),
        (4, -150.0, -600.0),
    ]
    assert coding.subtotal == -2050.0
    assert [i.code for i in validate_coding(coding, reference).issues if i.severity == "error"] == []


def _offline_pipeline(reference, store):
    s = Settings()
    return InvoicePipeline(replace(s, llm=replace(s.llm, provider="off")), reference, store=store)


def test_a_vendor_imported_first_keeps_its_learning_past_the_memory_read_cap(tmp_path, reference, monkeypatch):
    # The learning memory is read newest first, up to a cap (50,000 rows): past it, the vendors whose history was
    # imported first fell out whole and their lines stayed UNASSIGNED. (The cap made small here.)
    store = Store(tmp_path / "ap.db")
    lines = {"Dell Latitude 7450 laptop, 32GB RAM": "6010", "Microsoft 365 E3 licences - monthly": "6020"}
    store.import_history([{"vendor_name": "Northwind IT Solutions Inc.", "description": d, "gl_code": gl,
                           "amount": 100.0, "date": f"2025-01-0{n}"}
                          for n in (1, 2) for d, gl in lines.items()])  # fmt: skip
    store.import_history([{"vendor_name": f"Zenith Freight {i}", "description": f"Freight charge lot {i}",
                           "gl_code": "5100", "amount": 10.0, "date": "2025-06-01"} for i in range(30)])  # fmt: skip
    monkeypatch.setattr(store_mod, "FEEDBACK_READ", 20)
    assert len(store.feedback_rows()) == 20  # the cap holds, and Northwind's rows are not in it
    result = _offline_pipeline(reference, store).process(SAMPLES / f"{SAMPLE_STEM}.md", save=False)
    coded = {li["description"]: li["predicted_gl_code"] for li in result.output["line_items"]}
    assert {d: coded[d] for d in lines} == lines


def test_a_banking_line_that_quotes_the_invoice_number_keeps_only_the_account():
    # Every invoice quotes its own number on the banking line: kept, it made every later invoice a changed account.
    first = bank_details("EFT payments: Transit 12345 Inst 001 Account 1234567, quoting invoice NW-2026-0912")
    later = bank_details("EFT payments: Transit 12345 Inst 001 Account 1234567, quoting invoice NW-2026-0987")
    assert first == later == "EFT payments: Transit 12345 Inst 001 Account 1234567"
    assert same_bank_account(first, later)
    assert bank_details("Wire transfer to RBC 003-00012-1234567, ref. invoice 88231") == (
        "Wire transfer to RBC 003-00012-1234567"
    )
    assert bank_details("EFT: Transit 12345 Inst 001 Account 1234567. Questions? Call 416-555-1212") == (
        "EFT: Transit 12345 Inst 001 Account 1234567"
    )
    # A word about the invoice before the account's digits is not where the details end.
    assert bank_details("EFT (quote the invoice number): Transit 12345 Inst 001 Account 1234567") == (
        "EFT (quote the invoice number): Transit 12345 Inst 001 Account 1234567"
    )
    assert bank_details("IBAN GB29 NWBK 6016 1331 9268 19, invoice 4471") == "IBAN GB29 NWBK 6016 1331 9268 19"


def test_a_self_test_mark_whose_process_id_another_program_now_has_is_not_under_way(tmp_path):
    # A dashboard killed during OvisOCR2's self-test left its mark; its process id now belongs to another program.
    store = Store(tmp_path / "ap.db")
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started = (dt.datetime.now() - dt.timedelta(days=3)).isoformat(timespec="seconds")
        store.set_setting(page_worker.TESTING_KEY, json.dumps({"model": "ovisocr2", "started": started,
                          "pid": other.pid, "reader": f"{other.pid}:deadbeef", "done": 0, "pages": 2}))  # fmt: skip
        assert page_worker.test_under_way(store) is None
        assert page_worker.self_test(store, "ovisocr2")[0] == "pending"
        # A test another process runs now, noted minutes ago, is under way.
        now = dt.datetime.now().isoformat(timespec="seconds")
        store.set_setting(page_worker.TESTING_KEY, json.dumps({"model": "ovisocr2", "started": started,
                          "updated": now, "reader": f"{other.pid}:cafe", "done": 1, "pages": 2}))  # fmt: skip
        assert page_worker.test_under_way(store)["done"] == 1
    finally:
        other.kill()
        other.wait()
