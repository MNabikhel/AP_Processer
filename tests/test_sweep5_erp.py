"""Sweep 5 of coding/ and erp/: the bugs found reading the GL coding, tax, JD Edwards, exports and reports code
(B-1370 and on), each with the test that keeps it fixed."""

from __future__ import annotations

import json

from ap_coder.core.store import Store


def test_insights_count_every_rejected_duplicate(tmp_path):
    """B-1370: two duplicates rejected and the business case failed (sqlite rows can't be sorted)."""
    from ap_coder.erp.insights import compute

    store = Store(tmp_path / "ap.db")
    flagged = {"issues": [{"severity": "error", "code": "DUPLICATE_INVOICE", "message": "x"}]}
    for n, total in (("A-1", 10.0), ("A-2", 25.5)):
        doc = {"vendor_name": "Acme", "invoice_number": n, "grand_total": total, "currency": "CAD"}
        store.reject_invoice(store.add_invoice(tmp_path / f"{n}.pdf", doc, flagged), "Jane", "duplicate")
    s = compute(store)
    assert s["duplicates_stopped"] == 2 and s["duplicates_stopped_total"] == {"CAD": 35.5}


def test_a_statement_amount_a_cent_off_matches_whatever_its_size():
    """B-1371: a statement line a cent off an invoice was matched at 10.01 but "amount differs" at 100.01 (a float
    a hair over the cent)."""
    from ap_coder.erp import statements as stm

    cols = {"number": "No", "amount": "Amt"}
    for ours, theirs in ((10.00, 10.01), (100.00, 100.01), (20.00, 20.01), (1000.00, 999.99)):
        invoices = [{"id": 1, "invoice_number": "77", "invoice_date": "2026-09-01", "grand_total": ours,
                     "status": "approved"}]  # fmt: skip
        rec = stm.reconcile([{"No": "77", "Amt": str(theirs)}], cols, invoices)
        assert [li.status for li in rec.lines] == [stm.MATCHED], (ours, theirs)
    invoices = [{"id": 1, "invoice_number": "77", "invoice_date": "", "grand_total": 100.00, "status": "approved"}]
    assert stm.reconcile([{"No": "77", "Amt": "100.02"}], cols, invoices).lines[0].status == stm.DIFFERS
