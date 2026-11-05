"""Review probes (not to commit): phase 2 part 2's kept answers."""

from __future__ import annotations

import sqlite3

from ap_coder.core.store import Store
from ap_coder.core.store import vendors as vendors_module


def test_probe_a_vendor_over_the_row_cap_is_read_twice_by_one_check(tmp_path, monkeypatch):
    """Before D-160 the two calls of one check (vendor_findings, expected_tax_numbers) read the vendor's invoices once
    inside the page's session (``_session_memo``). Over VENDOR_ROWS_KEPT nothing is kept: read twice."""
    from ap_coder.coding.vendors import expected_tax_numbers, vendor_findings
    from ap_coder.core.schema import InvoiceCoding
    from tests.webclient import add_clean, gt

    store = Store(tmp_path / "ap.db")
    ids = [add_clean(store, invoice_number=f"N-{i}") for i in range(3)]
    key = store.get_invoice(ids[0])["vendor_key"]
    monkeypatch.setattr(vendors_module, "VENDOR_ROWS_KEPT", 2)  # the vendor has 3: a "big supplier" here
    reads = []
    real = Store._vendor_invoices
    monkeypatch.setattr(Store, "_vendor_invoices", lambda self, k: reads.append(k) or real(self, k))
    coding = InvoiceCoding.model_validate({**gt(), "invoice_number": "N-9"})
    with store.session():  # one page
        vendor_findings(coding, store)
        expected_tax_numbers(coding, store)
    print("reads in one check:", len(reads))
    assert len(reads) == 1, f"read {len(reads)} times in one check"


def test_probe_a_big_vendor_kept_flushes_every_other_vendor(tmp_path, monkeypatch):
    """A vendor whose rows nearly fill VENDOR_ROWS_KEPT, once kept, drops every other vendor kept; the next vendor
    asked drops it again."""
    from tests.webclient import add_clean

    store = Store(tmp_path / "ap.db")
    monkeypatch.setattr(vendors_module, "VENDOR_ROWS_KEPT", 5)
    vendors_module._VENDOR_ROWS.clear()
    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS_HELD", 0)
    big = [add_clean(store, invoice_number=f"B-{i}") for i in range(5)]
    big_key = store.get_invoice(big[0])["vendor_key"]
    small = []
    for n in range(3):
        with sqlite3.connect(store.path) as conn:
            conn.execute("INSERT INTO invoices (source_path, file_name, status, created_at, vendor_key, vendor_name) "
                         "VALUES ('x', 'x', 'review', '2026', ?, ?)", (f"small{n}", f"Small {n}"))
        small.append(f"small{n}")
    for k in small:
        store.vendor_invoices(k)
    kept_before = [k for (_, k) in vendors_module._VENDOR_ROWS]
    store.vendor_invoices(big_key)
    kept_after = [k for (_, k) in vendors_module._VENDOR_ROWS]
    store.vendor_invoices(small[0])
    kept_last = [k for (_, k) in vendors_module._VENDOR_ROWS]
    print("before:", kept_before, "after the big one:", kept_after, "after a small one:", kept_last)
