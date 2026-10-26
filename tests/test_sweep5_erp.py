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


def test_jde_api_checks_a_yen_vouchers_record_against_its_gross_in_yen():
    """B-1372: a yen voucher's gross was also expected at a hundredth of itself (its whole yen read as cents), so a
    JD Edwards record of 100.00 under its key passed for a voucher of 10,000 yen."""
    from ap_coder.erp import jde, jde_post

    doc = {"vendor_name": "Tokyo Parts", "invoice_number": "T-1", "invoice_date": "2026-09-01", "currency": "JPY",
           "subtotal": 10000, "tax_total": 0, "grand_total": 10000, "supplier_province": "OUTSIDE_CANADA",
           "line_items": [{"line_number": 1, "description": "Bolts", "quantity": 1, "unit_price": 10000,
                           "amount": 10000, "predicted_gl_code": "6010", "predicted_cost_center": "CC100"}],
           "tax_lines": [], "gl_distribution": [{"kind": "expense", "line_number": 1, "gl_code": "6010",
                                                 "cost_center": "CC100", "description": "Bolts", "net_amount": 10000,
                                                 "non_recoverable_tax": 0.0, "amount": 10000}]}  # fmt: skip
    inv = {"id": 5, "final_output": doc}
    settings = jde.JdeSettings(currencies=["CAD", "JPY"], default_bu="100")
    assert jde.validate([inv], settings, lambda final: "10023") == {}
    headers, _, _ = jde.voucher_rows(inv, "10023", settings, batch=1)
    expect = jde_post._expect(inv, headers, settings)
    assert expect["gross"] == [10000.0]
    row = {"expect": json.dumps(expect)}
    assert jde_post.mismatch({"an8": "10023", "invoice_number": "T-1", "gross": "10000"}, row) == ""
    assert "gross" in jde_post.mismatch({"an8": "10023", "invoice_number": "T-1", "gross": "100.00"}, row)
