"""The ERP register's store part (ap_coder/core/store/register.py, D-181): JD Edwards' vouchers kept by their key under
their bills, made in a database an older AP Coder left, and moved with their bill when the vendor keys are worked
out again."""

from __future__ import annotations

import sqlite3

from ap_coder.core.store import Store


def _voucher(key: str, name: str = "Acme Ltd", number: str = "A-1", total: float = 10.0, day: str = "2026-10-01",
             an8: str = "4001") -> dict:  # fmt: skip
    return {"key": key, "vendor_name": name, "invoice_number": number, "total": total, "invoice_date": day, "an8": an8}


def test_a_database_an_older_version_left_gets_the_vouchers_and_keeps_its_register(tmp_path):
    path = tmp_path / "ap.db"
    Store(path).import_erp_register([{"vendor_name": "Acme Ltd", "invoice_number": "A-1", "total": 10.0}])
    with sqlite3.connect(path) as conn:  # as an older AP Coder left it
        conn.execute("DROP TABLE erp_vouchers")
        conn.execute("ALTER TABLE erp_invoices DROP COLUMN an8")
    store = Store(path)
    assert [r["invoice_number"] for r in store.erp_register()] == ["A-1"]
    assert store.import_jde_vouchers([_voucher("1-PV-1-001", number="B-1")], seen=1) == 1
    assert store.in_erp("Acme Ltd", "B-1", 10.0) and store.erp_register_count() == 2


def test_a_bill_counts_its_vouchers_dates_and_goes_with_its_last(tmp_path):
    store = Store(tmp_path / "ap.db")
    store.import_jde_vouchers([_voucher("1"), _voucher("2", day="2026-10-05")], seen=1)
    assert store.erp_register_repeats() == 1  # entered twice in JDE
    assert store.jde_vouchers_unseen("2026-01-01", 2) == (["1", "2"], 2)
    store.import_jde_vouchers([_voucher("1")], seen=2)
    gone, _ = store.jde_vouchers_unseen("2026-01-01", 2)
    assert gone == ["2"] and store.remove_jde_vouchers(gone, 2) == 0 and store.erp_register_repeats() == 0
    store.import_jde_vouchers([_voucher("1", name="Acme Renamed Inc.")], seen=3)  # its supplier renamed in JDE
    assert [r["vendor_name"] for r in store.erp_register()] == ["Acme Renamed Inc."]
    assert store.in_erp("Acme Ltd", "A-1", 10.0, an8="4001") and not store.in_erp("Acme Ltd", "A-1", 10.0, an8="")
    assert store.remove_jde_vouchers(["1"], 4) == 1 and store.erp_register_count() == 0


def test_a_voucher_keeps_the_newest_read_that_listed_it(tmp_path):
    """B-1898: a voucher took the stamp of the last read that wrote it, so an older read (two syncs at once, the lease
    lost) writing after a newer one put its older stamp back, and the newer read's sweep removed a voucher it had
    listed: still in JDE, out of the register."""
    store = Store(tmp_path / "ap.db")
    store.import_jde_vouchers([_voucher("1")], seen=5)  # listed by a read begun later (Read everything again)
    store.import_jde_vouchers([_voucher("1")], seen=3)  # then by an older, slower one
    assert store.jde_vouchers_unseen("2026-01-01", 5) == ([], 1)


def test_the_vouchers_go_with_their_bill_when_the_vendor_keys_are_worked_out_again(tmp_path):
    path = tmp_path / "ap.db"
    store = Store(path)
    store.import_jde_vouchers([_voucher("1", name="Énergir, s.e.c.")], seen=1)
    with sqlite3.connect(path) as conn:  # as an older version keyed it
        for table in ("erp_invoices", "erp_vouchers"):
            conn.execute(f"UPDATE {table} SET vendor_key = 'energir sec'")
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'vendor_key_rules'")
    store = Store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT vendor_key FROM erp_vouchers").fetchall() == [("energir",)]
    assert store.remove_jde_vouchers(["1"], 2) == 1 and store.erp_register_count() == 0  # voided: the bill goes
