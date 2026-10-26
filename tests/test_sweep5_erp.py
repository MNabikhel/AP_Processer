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
