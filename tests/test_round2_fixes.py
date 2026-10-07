"""Fixes from the second browser bug hunt."""

import json

from ap_coder.store import Store

from .conftest import SAMPLES


def _doc(stem):
    return json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text())


def test_batch_totals_keep_currencies_apart(tmp_path):
    store = Store(tmp_path / "e.db")
    ids = []
    for stem in ("northwind_ON_HST_NW-2026-0912", "cascade_US_SalesTax_INV-30981", "chinook_AB_GST_CCO-26-10418"):
        doc = _doc(stem)
        invoice_id = store.add_invoice(tmp_path / f"{stem}.pdf", doc, {})
        store.approve_invoice(invoice_id, doc, "Jane")
        ids.append(invoice_id)
    batch = store.create_export_batch(ids, "csv")
    totals = store.batch_totals()[batch]
    assert set(totals) == {"CAD", "USD"}
    assert totals["USD"] == _doc("cascade_US_SalesTax_INV-30981")["grand_total"]
    store.undo_export_batch(batch)
    assert batch not in store.batch_totals()


def test_by_currency_text():
    from ap_coder.webapp.common import by_currency

    text = by_currency([{"currency": "USD", "grand_total": 10}, {"currency": "CAD", "grand_total": 5},
                        {"currency": "", "grand_total": 1}])  # fmt: skip
    assert text == "6.00 CAD · 10.00 USD"


def test_every_recorded_action_has_an_activity_filter():
    from ap_coder.audit import ACTIONS
    from ap_coder.webapp.activity import GROUPS

    assert set(ACTIONS) <= {a for actions in GROUPS.values() for a in actions}


def test_the_reviewers_currency_is_saved(tmp_path):
    import sqlite3

    store = Store(tmp_path / "c.db")
    doc = _doc("northwind_ON_HST_NW-2026-0912")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", doc, {})
    store.approve_invoice(invoice_id, {**doc, "currency": "usd"}, "Jane")
    assert store.list_invoices()[0]["currency"] == "USD"
    store.create_export_batch([invoice_id], "csv")
    assert store.batch_totals()[1] == {"USD": doc["grand_total"]}
    # A database approved by an older version is put right on upgrade.
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET currency = 'CAD'")
        conn.execute("UPDATE settings SET value = '11' WHERE key = 'schema_version'")
    assert Store(store.path).list_invoices()[0]["currency"] == "USD"


def test_backups_are_copied_to_a_second_folder(tmp_path):
    store = Store(tmp_path / "db" / "ap.db")
    second = tmp_path / "onedrive"
    assert store.copy_backup(store.backup_now()) is None  # not set up: nothing to do
    store.set_setting("backup_copy_dir", str(second))
    store.backup_now("manual")
    assert store.get_setting("backup_copy_status").startswith("failed")  # the folder is not there (yet)
    second.mkdir()
    made = store.backup_now("manual")
    assert (second / made.name).exists() and store.get_setting("backup_copy_status").startswith("ok")
    store.backup_now("before-restore")
    assert len(list(second.glob("*.db"))) == 1  # safety copies stay local
    for n in range(16):  # daily copies there are pruned to the newest 14
        (second / f"ap_coder-2020010{n % 10}-0000{n:02d}.db").write_bytes(b"x")
    store.copy_backup(store.backup_now())
    daily = [p for p in second.glob("ap_coder-*.db") if p.stem.count("-") == 2]
    assert len(daily) == 14
