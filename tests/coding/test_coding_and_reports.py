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


def test_the_hub_gives_an_old_lines_tax_code_as_the_review_screen_reads_it():
    """B-1373: a line stored before tax codes kept their exemptions was read in the hub against every tax line
    printed ("PST 0.00" too): "GST~PST" (PST exempt), where the review screen and the exports say "GST" (PST due,
    not charged)."""
    from ap_coder.coding.line_tax import legacy_basis, tax_code_of
    from ap_coder.erp.hub import tables

    lines = [{"line_number": 1, "description": "Toner", "amount": 100.0, "taxes_applied": ["GST"]},
             {"line_number": 2, "description": "Paper", "amount": 50.0, "taxes_applied": []}]  # fmt: skip
    taxes = [{"tax_type": "GST", "rate": 0.05, "taxable_amount": 100.0, "tax_amount": 5.0},
             {"tax_type": "PST", "province": "BC", "rate": 0.07, "taxable_amount": 0.0, "tax_amount": 0.0}]  # fmt: skip
    row = {"id": 1, "status": "approved", "final_output": {"line_items": lines, "tax_lines": taxes}}
    got = [r["tax_code"] for r in tables.invoice_lines(None, tables.Context(), rows=[row])]
    assert got == [tax_code_of(li, legacy_basis(lines, taxes)) for li in lines] == ["GST", "NONE~GST"]


def test_a_qst_number_kept_as_its_ten_digits_is_not_warned_about():
    """B-1374: the Vendors page warned a QST number kept as its 10 digits alone ("not in the format"), the number the
    comparison with an invoice takes as the vendor's, as it takes the GST/HST Business Number alone."""
    from ap_coder.coding.vendors import same_qst_number, vendor_number_problems

    assert same_qst_number("1234567890", "1234567890 TQ 0001")
    assert vendor_number_problems({"qst_number": "1234567890"}) == []
    assert vendor_number_problems({"qst_number": "12345TQ"}) == [
        "QST number 12345TQ is not in the format 1234567890 TQ0001"
    ]
