"""The review queue's checks: read from the invoices' summary, and checked again in the background after a change of
settings, in batches, one process at a time (``coding/bulk.py``, D-155)."""

from __future__ import annotations

import sqlite3
import time

import pytest

from ap_coder.coding import bulk
from ap_coder.core.config import Settings
from ap_coder.core.memory import vendor_key
from ap_coder.core.store import APPROVED, REVIEW
from ap_coder.core.store.summary import summary_facts
from tests.conftest import copy_database

QUEUE = ("q_audit", "q_stamp", "q_found", "r_stamp", "r_found", "q_pick", "q_pick_cc", "q_pay")


@pytest.fixture
def store(tmp_path, setup_demo_template):
    return copy_database(setup_demo_template, tmp_path / "private" / "ap_coder.db")


def _queue(store) -> list[int]:
    return [i["id"] for i in store.list_invoices(REVIEW)]


def _held(store) -> tuple[int, str]:
    """An invoice of the queue, and its vendor put on hold: a change the queue's checks read (B-842)."""
    inv = next(i for i in store.list_invoices(REVIEW) if i["vendor_name"])
    store.save_vendor(vendor_key(inv["vendor_name"]), inv["vendor_name"], "on_hold")
    return inv["id"], inv["vendor_name"]


def _codes(checks, invoice_id) -> set[str]:
    return {i["code"] for i in checks[invoice_id]["found"]}


def _as_read(store, ids) -> dict[int, list[tuple]]:
    """Each invoice's findings by today's checks as the queue worked them out before D-155: from the invoice itself
    (``issues_today``, else its stored checks)."""
    today = bulk.issues_today(store, ids)
    rows = store.invoice_columns(("id", "validation"), ids=ids)
    stored = {r["id"]: (r["validation"] or {}).get("issues") or [] for r in rows}
    return {i: [(f.get("severity"), f.get("code")) for f in today.get(i, stored[i])] for i in ids}


def test_the_queue_reads_its_checks_from_the_summary_as_the_invoices_have_them(store, monkeypatch):
    """B-1630: each load of the queue read the coding and checks of every invoice waiting (their JSON text, twice:
    about 86 MB a load with 2,722 waiting). What it reads of them is kept in the invoices' summary, and is what reading
    the invoices gives, before and after a change of settings."""
    ids = _queue(store)

    def found(checks):
        return {i: [(f["severity"], f["code"]) for f in checks[i]["found"]] for i in ids}

    def no_reading(*_a, **_k):
        raise AssertionError("read the invoice")

    with monkeypatch.context() as m:
        for name in ("invoice_columns", "get_invoice", "get_invoices"):
            m.setattr(store, name, no_reading)
        checks, updating = bulk.queue_checks(store, ids, recheck=False)
    assert updating == 0 and found(checks) == _as_read(store, ids)
    _held(store)
    checks, updating = bulk.queue_checks(store, ids, recheck=True)  # the laptop: checked again at once, as before
    assert updating == 0 and found(checks) == _as_read(store, ids)


def test_every_write_of_an_invoice_keeps_what_the_queue_reads_of_it(store):
    """B-1630: the queue's facts are worked out wherever an invoice is written (the summary's triggers, D-110): by the
    store's own writes, the queue's re-check, and a connection that is not the store's."""
    ids = _queue(store)
    first, second, third = ids[:3]
    _held(store)
    bulk.recheck_queue(store)
    store.refresh_confidence(first, 0.5, True)
    store.park_invoice(second, "jane", "waiting for the PO")
    store.unpark_invoice(second, "jane")
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET validation = json_set(validation, '$.issues', json('[]')) WHERE id = ?",
                     (third,))  # fmt: skip
    approved = store.list_invoices(APPROVED)[0]["id"]
    store.reopen(approved, "jane")
    for invoice_id in _queue(store):
        kept = store.summary_rows("id = ?", (invoice_id,), ", ".join(QUEUE))[0]
        assert kept == {k: summary_facts(store, invoice_id, "queue")[k] for k in QUEUE}, invoice_id
    assert approved in _queue(store)
    assert store.summary_rows("id = ?", (third,), "q_found")[0]["q_found"] == "[]"


def test_after_a_change_of_settings_the_server_queue_waits_for_the_background(store, monkeypatch):
    """B-1631: the first load of the queue after a change of settings (a vendor put on hold, a PO imported) checked
    every invoice waiting again, in the page, and wrote each: 25.7 s with 2,722 waiting, past 600 s on 100,000
    invoices, once in each web worker. On the company server the page shows the earlier checks meanwhile, and says
    how many; the background process checks them again."""
    ids = _queue(store)
    target, _ = _held(store)
    calls = []
    real = bulk.finalise_coding
    monkeypatch.setattr(bulk, "finalise_coding", lambda *a, **k: calls.append(1) or real(*a, **k))
    checks, updating = bulk.queue_checks(store, ids, recheck=False)
    assert calls == [] and updating == len(store.stale_checks(store.checks_stamp())) > 0
    assert "VENDOR_ON_HOLD" not in _codes(checks, target)
    assert bulk.recheck_queue(store) == updating == len(calls)
    checks, updating = bulk.queue_checks(store, ids, recheck=False)
    assert updating == 0 and "VENDOR_ON_HOLD" in _codes(checks, target)
    assert bulk.recheck_queue(store) == 0  # done once per change of settings


def test_one_process_at_a_time_checks_the_queue_again(store):
    """B-1631: each web worker that got there first checked the whole queue again. A lease in the database lets one
    process at a time do it; one left by a process that stopped runs out."""
    _held(store)
    assert store.take_lease(bulk.RECHECK_LEASE, "another:1", 60)
    assert bulk.recheck_queue(store) is None and store.stale_checks(store.checks_stamp())
    assert store.take_lease(bulk.RECHECK_LEASE, "another:1", -1)  # (as if that process stopped a while ago)
    assert bulk.recheck_queue(store) and not store.stale_checks(store.checks_stamp())
    assert store.take_lease(bulk.RECHECK_LEASE, "another:1", 60)  # it let go of it


def test_the_queue_is_checked_again_in_batches_each_kept_in_one_short_write(store, monkeypatch):
    _held(store)
    monkeypatch.setattr(bulk, "RECHECK_BATCH", 2)
    writes = []
    real = store.save_rechecks
    monkeypatch.setattr(store, "save_rechecks", lambda found: writes.append(len(found)) or real(found))
    done = bulk.recheck_queue(store)
    assert len(writes) > 1 and max(writes) <= 2 and sum(writes) == done > 2


def test_the_background_checks_the_queue_again_after_a_vendor_change(store):
    work = bulk.QueueRecheck(lambda: Settings(), lambda: store, every=0.05).start()
    try:
        target, _ = _held(store)
        deadline = time.monotonic() + 60
        while store.stale_checks(store.checks_stamp()) and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        work.stop()
        work.thread.join(30)
    assert not store.stale_checks(store.checks_stamp())
    assert "VENDOR_ON_HOLD" in _codes(bulk.queue_checks(store, [target], recheck=False)[0], target)


def test_a_vendor_note_or_one_more_jde_batch_changes_no_check(store):
    """B-1632: a note on a vendor, and each JD Edwards batch made, changed the checks stamp: the whole queue was
    checked again for a change no check reads. A note is in a check's words only for a vendor on hold."""
    inv = next(i for i in store.list_invoices(REVIEW) if i["vendor_name"])
    key, name = vendor_key(inv["vendor_name"]), inv["vendor_name"]
    store.save_vendor(key, name)
    first = store.checks_stamp()
    store.save_vendor(key, name, notes="Call Jane before paying")
    assert store.checks_stamp() == first
    store.save_vendor(key, name, "on_hold", notes="Call Jane before paying")
    held = store.checks_stamp()
    store.save_vendor(key, name, "on_hold", notes="Disputed: call Jane")  # said by VENDOR_ON_HOLD
    noted = store.checks_stamp()
    assert len({first, held, noted}) == 3
    approved = [i["id"] for i in store.list_invoices(APPROVED)]
    store.create_export_batch(approved[:1], "jde")
    one = store.checks_stamp()  # JD Edwards in use now: its checks apply
    store.create_export_batch(approved[1:2], "jde")
    assert one != noted and store.checks_stamp() == one
