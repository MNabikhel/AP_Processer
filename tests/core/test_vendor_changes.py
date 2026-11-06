"""The vendors' change log (``vendor_changes``, D-174): each process keeps its vendors' invoices
(``Store.vendor_invoices``) and brings them up to date from the invoices the log says changed since. Whatever wrote
(another worker, plain SQL, a write undone, a restore), in whatever order, what a process answers is what a fresh read
gives."""

from __future__ import annotations

import json
import random
import re
import sqlite3
from collections import OrderedDict
from contextlib import contextmanager

import pytest

from ap_coder.core.store import Store
from ap_coder.core.store import vendors as vendors_module
from ap_coder.core.store._base import VENDOR_CHANGES_KEPT
from ap_coder.core.store.summary import SUMMARY_TRIGGERS

NOW = "2026-01-05T09:00:00"
VENDORS = ("Acme Supply", "Beta Ltd", "Gamma Inc.")
_LOGGING = [name for name in SUMMARY_TRIGGERS if name.startswith("changes_vendor_invoice_")]


class Worker:
    """A web worker: a Store of its own and the vendors' invoices it keeps (``vendors._VENDOR_ROWS``), its own."""

    def __init__(self, path) -> None:
        self.store, self.kept, self.held = Store(path), OrderedDict(), 0

    @contextmanager
    def running(self):
        """This worker's turn (inside another's, that one's again after it)."""
        before = vendors_module._VENDOR_ROWS, vendors_module._VENDOR_ROWS_HELD
        vendors_module._VENDOR_ROWS, vendors_module._VENDOR_ROWS_HELD = self.kept, self.held
        try:
            yield self.store
        finally:
            self.held = vendors_module._VENDOR_ROWS_HELD
            vendors_module._VENDOR_ROWS, vendors_module._VENDOR_ROWS_HELD = before


@pytest.fixture
def workers(tmp_path, monkeypatch):
    """Two web workers on one database (their keeping restored after the test)."""
    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS", vendors_module._VENDOR_ROWS)
    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS_HELD", vendors_module._VENDOR_ROWS_HELD)
    path = tmp_path / "ap.db"
    return Worker(path), Worker(path)


def _short_log(path, keep: int) -> None:
    """The log's triggers made again keeping ``keep`` changes, not ``VENDOR_CHANGES_KEPT``: pruned at once."""
    with sqlite3.connect(path) as conn:
        for name in _LOGGING:
            conn.execute(f"DROP TRIGGER {name}")
            conn.execute(SUMMARY_TRIGGERS[name].replace(f"- {VENDOR_CHANGES_KEPT}", f"- {keep}"))


_READ = Store._vendor_invoices  # (as it is: a test may watch the store's own reads)


def _fresh(store: Store, key: str) -> list[dict]:
    """The vendor's invoices read afresh from the database."""
    return _READ(store, key)


def _keys(path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT vendor_key FROM invoices WHERE vendor_key IS NOT NULL")]


def _add(store: Store, tmp_path, ground_truth, rng: random.Random, n: int) -> int:
    path = tmp_path / f"in-{n}.pdf"
    path.write_bytes(f"invoice {n}".encode())
    doc = {**ground_truth, "invoice_number": f"N-{n}", "vendor_name": rng.choice(VENDORS),
           "grand_total": rng.choice((100.0, 250.5, -40.0)),
           "remit_bank_account": rng.choice(("", "004-1-22"))}  # fmt: skip
    return store.add_invoice(path, doc, {"requires_review": True})


def _ids(path, *statuses: str) -> list[int]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT id FROM invoices WHERE status IN (SELECT value FROM json_each(?)) ORDER BY id",
            (json.dumps(statuses),),
        )
        return [r[0] for r in rows]


def _plain(path, sql: str, *args) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(sql, args)


_ACTIONS = ("add", "park", "unpark", "approve", "reject", "reopen", "delete", "export", "undo export",
            "sql column", "sql coding", "sql move", "sql merge", "sql insert", "sql delete", "undone", "confidence",
            "restore")  # fmt: skip


def _step(rng: random.Random, store: Store, tmp_path, ground_truth, n: int, backup) -> str:
    """One write, picked at random among the ways an invoice changes: through a Store (a worker's) or plain SQL."""
    path = store.path
    review, approved = _ids(path, "review"), _ids(path, "approved")
    action = rng.choices(_ACTIONS, [0.1 if a == "restore" else 1 for a in _ACTIONS])[0]
    if action == "add" or not review:
        _add(store, tmp_path, ground_truth, rng, n)
    elif action == "park":
        store.park_invoice(rng.choice(review), "Ann", "waiting for the PO")
    elif action == "unpark" and _ids(path, "parked"):
        store.unpark_invoice(rng.choice(_ids(path, "parked")), "Ann")
    elif action == "approve":
        invoice = rng.choice(review)
        coding = {**ground_truth, "vendor_name": store.get_invoice(invoice)["vendor_name"],
                  "remit_bank_account": rng.choice(("004-12345-1234567", "010-9-8"))}  # fmt: skip
        store.approve_invoice(invoice, coding, "Jordan Lee")
    elif action == "reject":
        store.reject_invoice(rng.choice(review), "Ann", "a copy")
    elif action == "reopen" and _ids(path, "rejected"):
        store.reopen(rng.choice(_ids(path, "rejected")), "Ann", "again")
    elif action == "delete":
        store.delete_invoice(rng.choice(review), actor="Ann")
    elif action == "export" and approved:
        with sqlite3.connect(path) as conn:
            ready = [r[0] for r in conn.execute("SELECT id FROM invoices WHERE status = 'approved' AND export_batch "
                                                "IS NULL")]  # fmt: skip
        if ready:
            store.create_export_batch(rng.sample(ready, min(2, len(ready))), "csv", "Ann")
    elif action == "undo export":
        with sqlite3.connect(path) as conn:
            batches = [r[0] for r in conn.execute("SELECT DISTINCT export_batch FROM invoices WHERE export_batch > 0")]
        if batches:
            store.undo_export_batch(rng.choice(batches), "Ann")
    elif action == "sql column":
        column, value = rng.choice((("invoice_date", "2026-03-03"), ("grand_total", 99.5), ("currency", "USD"),
                                    ("invoice_number", f"X-{n}"), ("vendor_name", "ACME SUPPLY"),
                                    ("created_at", NOW), ("reviewed_at", NOW)))  # fmt: skip
        _plain(path, f"UPDATE invoices SET {column} = ? WHERE id = ?", value, rng.choice(review))
    elif action == "sql coding":
        _plain(path, "UPDATE invoices SET ai_output = json_set(ai_output, '$.gst_hst_registration_number', ?) "
                     "WHERE id = ?", f"12345678{n % 10}RT0001", rng.choice(review))  # fmt: skip
    elif action == "sql move":  # one invoice now another vendor's
        name = rng.choice(VENDORS)
        _plain(path, "UPDATE invoices SET vendor_name = ?, vendor_key = lower(?) WHERE id = ?", name, name,
               rng.choice(review))  # fmt: skip
    elif action == "sql merge":  # every invoice of one vendor now another's (a vendor renamed or merged)
        old, new = rng.sample(_keys(path), 2) if len(_keys(path)) > 1 else (_keys(path)[0], "zeta")
        _plain(path, "UPDATE invoices SET vendor_key = ? WHERE vendor_key = ?", new, old)
    elif action == "sql insert":
        _plain(path, "INSERT INTO invoices (source_path, file_name, status, created_at, vendor_key, vendor_name, "
                     "invoice_number, grand_total) VALUES ('x', 'x', 'review', ?, ?, 'Beta Ltd', ?, 5)",
               NOW, rng.choice(_keys(path)), f"P-{n}")  # fmt: skip
    elif action == "sql delete":
        _plain(path, "DELETE FROM invoices WHERE id = ?", rng.choice(review))
    elif action == "undone":  # a write under way, read inside it, then undone
        invoice = rng.choice(review)
        key = store.get_invoice(invoice)["vendor_key"]
        with pytest.raises(RuntimeError), store.session(), store._conn() as conn:
            conn.execute("UPDATE invoices SET status = 'rejected' WHERE id = ?", (invoice,))
            assert next(r for r in store.vendor_invoices(key) if r["id"] == invoice)["status"] == "rejected"
            raise RuntimeError("undone")
    elif action == "confidence":
        store.refresh_confidence(rng.choice(review), rng.random(), True)
    elif action == "restore":
        store.restore_from(backup)
    return action


@pytest.mark.parametrize("seed, keep", [(1, 1000), (2, 1000), (3, 8), (4, 8)])
def test_each_workers_kept_invoices_equal_a_fresh_read_after_any_writes_in_any_order(
    workers, tmp_path, ground_truth, seed, keep
):
    """Random writes by two workers and plain SQL (every way an invoice changes: added, coded, approved, exported and
    its batch undone, moved to another vendor, merged, deleted, a write undone, a restore), each worker asking a few
    vendors between them, the changes of several writes read at once; with a log of ``keep`` changes (8: some are
    pruned before a worker asks). Every answer is what a fresh read gives, each worker's own and the shared read-only
    rows alike."""
    rng = random.Random(seed)
    first, second = workers
    for n in range(6):
        with first.running() as store:
            _add(store, tmp_path, ground_truth, rng, n)
    backup = first.store.backup_now("manual")
    _short_log(first.store.path, keep)
    done, patched = [], 0
    for n in range(6, 86):
        writer = rng.choice((first, second))
        with writer.running() as store:
            done.append(_step(rng, store, tmp_path, ground_truth, n, backup))
        if done[-1] == "restore":  # (the migration made the log's triggers as they are)
            _short_log(first.store.path, keep)
        for worker in (first, second):
            with worker.running() as store:
                keys = _keys(store.path)
                for key in rng.sample(keys, min(rng.randint(0, 3), len(keys))):
                    patched += (str(store.path), key) in worker.kept
                    fresh = _fresh(store, key)
                    assert store.vendor_invoices(key) == fresh, (seed, n, done[-3:], key)
                    assert [dict(r) for r in store.vendor_invoices(key, shared=True)] == fresh
    assert {"export", "undo export", "sql move", "sql merge", "undone"} <= set(done) and patched > 100


def test_a_change_reads_again_only_the_invoices_it_changed_and_the_log_keeps_the_latest(
    workers, tmp_path, ground_truth
):
    """A vendor kept is brought up to date by reading the invoices changed since (one, here); the log keeps its latest
    ``VENDOR_CHANGES_KEPT`` changes, so one kept before more changes than that is read whole again, never missing
    one."""
    first, second = workers
    rng = random.Random(7)
    with first.running() as store:
        ids = [_add(store, tmp_path, ground_truth, rng, n) for n in range(4)]
    key = first.store.get_invoice(ids[0])["vendor_key"]
    read: list = []
    real = Store._vendor_invoices

    def reading(self, vendor, some=None):
        read.append(some)
        return real(self, vendor, some)

    with first.running() as store, pytest.MonkeyPatch.context() as mp:
        mp.setattr(Store, "_vendor_invoices", reading)
        store.vendor_invoices(key)
        mine = [r["id"] for r in store.vendor_invoices(key)]
        with second.running() as other:
            other.park_invoice(mine[0], "Ann", "waiting")
        assert store.vendor_invoices(key) == _fresh(store, key) and read == [None, [mine[0]]]
        with sqlite3.connect(store.path) as conn:  # more changes than the log keeps, of another invoice
            conn.executemany(
                "UPDATE invoices SET invoice_date = ? WHERE id = ?",
                [(f"2026-01-{n % 28 + 1:02d}", mine[-1]) for n in range(VENDOR_CHANGES_KEPT + 1)],
            )
            first_seq, last_seq, logged = conn.execute(
                "SELECT MIN(seq), MAX(seq), COUNT(*) FROM vendor_changes").fetchone()  # fmt: skip
        assert logged == VENDOR_CHANGES_KEPT and first_seq == last_seq - VENDOR_CHANGES_KEPT + 1
        assert store.vendor_invoices(key) == _fresh(store, key) and read[2:] == [None]  # read whole
        with second.running() as other:
            other.unpark_invoice(mine[0], "Ann")
        assert store.vendor_invoices(key) == _fresh(store, key) and read[3:] == [[mine[0]]]


def test_the_rows_kept_for_every_caller_are_read_only(workers, tmp_path, ground_truth):
    """The check reads the rows kept (``shared``), not a copy of each of a big vendor's 23,000 (twice a check): none
    can be changed, and each other caller's copy is its own."""
    first, _ = workers
    with first.running() as store:
        invoice = _add(store, tmp_path, ground_truth, random.Random(1), 1)
        key = store.get_invoice(invoice)["vendor_key"]
        (row,) = store.vendor_invoices(key, shared=True)
        with pytest.raises(TypeError):
            row["status"] = "changed by a caller"  # type: ignore[index]
        mine = store.vendor_invoices(key)
        mine[0]["status"] = "changed by its caller"
        assert store.vendor_invoices(key, shared=True)[0]["status"] == "review"


def _old_triggers(path) -> None:
    """The database as a version before D-174 left it: no change log, and the vendors' triggers counting only."""
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE vendor_changes")
        for name in _LOGGING:
            conn.execute(f"DROP TRIGGER {name}")
            conn.execute(re.sub(r"; INSERT INTO vendor_changes .*? - \d+", "", SUMMARY_TRIGGERS[name]))


def test_a_database_made_before_the_log_gets_it_and_its_triggers_at_start(tmp_path, ground_truth):
    """No schema version of its own: the log and its triggers are made at every start, as the counts' are (D-159)."""
    store = Store(tmp_path / "ap.db")
    invoice = _add(store, tmp_path, ground_truth, random.Random(1), 1)
    _old_triggers(store.path)
    with sqlite3.connect(store.path) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'vendor_changes'").fetchone()
        assert "vendor_changes" not in conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (_LOGGING[0],)
                                                    ).fetchone()[0]  # fmt: skip
    again = Store(store.path)  # the next start
    with sqlite3.connect(store.path) as conn:
        made = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall())
    assert all(made[name] == SUMMARY_TRIGGERS[name] for name in _LOGGING)
    key = again.get_invoice(invoice)["vendor_key"]
    again.park_invoice(invoice, "Ann", "waiting")
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT vendor_key, invoice_id FROM vendor_changes ORDER BY seq DESC").fetchone() == (
            key, invoice)  # fmt: skip


def test_while_a_backup_made_before_the_log_is_restored_another_workers_change_is_logged(
    workers, tmp_path, ground_truth, monkeypatch
):
    """The restore's copy gets the counts' triggers whose tables it has before it goes in (B-1720): those of a vendor's
    count now log into ``vendor_changes``, which a backup made before D-174 has not. The copy gets the log too: a
    change by another worker while the files are copied is written (not refused for a table missing) and logged, and
    the worker keeping that vendor's invoices answers with it."""
    from ap_coder.core.store import backup_files

    first, second = workers
    with first.running() as store:
        ids = [_add(store, tmp_path, ground_truth, random.Random(1), n) for n in range(3)]
        backup = store.backup_now("manual")
    _old_triggers(backup)
    key = first.store.get_invoice(ids[0])["vendor_key"]
    seen: dict = {}
    files = backup_files.restore_files

    def copying(*args, **kwargs):
        with second.running() as worker:
            seen["before"] = worker.vendor_invoices(key)
            with first.running() as other:
                other.park_invoice(ids[0], "Ann", "waiting for the PO")
            seen["after"], seen["fresh"] = worker.vendor_invoices(key), _fresh(worker, key)
        return files(*args, **kwargs)

    monkeypatch.setattr(backup_files, "restore_files", copying)
    with first.running() as store:
        store.restore_from(backup)
    assert [r["status"] for r in seen["before"]] == ["review"] * 3
    assert seen["after"] == seen["fresh"] and seen["after"][-1]["status"] == "parked"
    with second.running() as worker:
        assert worker.vendor_invoices(key) == _fresh(worker, key)
