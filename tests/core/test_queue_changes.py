"""The review queue's change log (``queue_changes``, D-193): each process keeps the queue's lists and facts by invoice
(``list_invoices`` of the review, parked and pending lists; ``Store.queue_facts`` behind ``bulk.queue_checks``) and
brings them up to date from the invoices the log says changed since. Whatever wrote (another worker, plain SQL, a write
undone, a restore, an older AP Coder), in whatever order, what a process answers is what a fresh full computation
gives."""

from __future__ import annotations

import itertools
import json
import random
import sqlite3
from contextlib import contextmanager

import pytest

from ap_coder.coding import bulk
from ap_coder.core.store import PARKED, PENDING, REVIEW, Store, review_queue
from ap_coder.core.store import summary as summary_module
from ap_coder.web.pages import review

NOW = "2026-01-05T09:00:00"
VENDORS = ("Acme Supply", "Beta Ltd", "Gamma Inc.")
LISTS = [c for n in (1, 2, 3) for c in itertools.combinations((REVIEW, PARKED, PENDING), n)]
FINDINGS = ([], [{"severity": "error", "code": "TOTAL_MISMATCH", "message": "Totals differ"}],
            [{"severity": "warning", "code": "GL_UNASSIGNED", "message": "Pick one"},
             {"severity": "info", "code": "VENDOR_BANK_CHANGED", "message": "New bank"}],
            [{"severity": "info", "code": "NEW_VENDOR", "message": "First invoice"}])  # fmt: skip


class Worker:
    """A web worker: a Store of its own and the queue's rows it keeps (``review_queue._KEPT``), its own."""

    def __init__(self, path) -> None:
        self.store, self.kept = Store(path), {}

    @contextmanager
    def running(self):
        """This worker's turn (inside another's, that one's again after it)."""
        before = review_queue._KEPT
        review_queue._KEPT = self.kept
        try:
            yield self.store
        finally:
            review_queue._KEPT = before


@pytest.fixture
def workers(tmp_path, monkeypatch):
    """Two web workers on one database (their keeping restored after the test); every ask of the queue's checks takes
    the facts kept (``KEEP_FROM``)."""
    monkeypatch.setattr(review_queue, "_KEPT", review_queue._KEPT)
    monkeypatch.setattr(bulk, "KEEP_FROM", 1)
    path = tmp_path / "ap.db"
    return Worker(path), Worker(path)


def _fresh_list(store: Store, statuses: tuple[str, ...]) -> list[dict]:
    """The queue's list of ``statuses`` read afresh from the summary."""
    listed, marks = ", ".join(summary_module.LISTED_COLUMNS), ", ".join("?" * len(statuses))
    return store.summary_fetch(f"SELECT {listed} FROM invoice_summary WHERE status IN ({marks}) ORDER BY "
                               f"{summary_module.QUEUE_ORDER}", statuses)  # fmt: skip


def _same(store: Store, rng: random.Random, where: tuple) -> None:
    """What this worker answers, kept, is what working it out afresh gives: some of the lists, the queue's checks of
    all its invoices, of some, and of every invoice (one no longer in review is in none), and why each needs
    attention."""
    for statuses in rng.sample(LISTS, 3):
        assert store.list_invoices(statuses=statuses) == _fresh_list(store, statuses), (where, statuses)
    pending = _fresh_list(store, (REVIEW,))
    ids = [i["id"] for i in pending]
    for asked in (ids, rng.sample(ids, len(ids) // 2) + [10_000], list(range(1, 120))):
        assert bulk.queue_checks(store, asked, recheck=False) == bulk._queue_checks(store, asked, None, False), where
    fresh = review.attention(store, None, pending, 0.8, bulk._queue_checks(store, ids, None, False)[0])
    assert review.attention(store, None, store.list_invoices(REVIEW), 0.8, recheck=False) == fresh, where


def _ids(path, *statuses: str) -> list[int]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT id FROM invoices WHERE status IN (SELECT value FROM json_each(?)) ORDER BY id",
                            (json.dumps(statuses),))  # fmt: skip
        return [r[0] for r in rows]


def _plain(path, sql: str, *args) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(sql, args)


def _add(store: Store, tmp_path, ground_truth, rng: random.Random, n: int) -> int:
    path = tmp_path / f"in-{n}.pdf"
    path.write_bytes(f"invoice {n}".encode())
    doc = {**ground_truth, "invoice_number": f"N-{n}", "vendor_name": rng.choice(VENDORS),
           "grand_total": rng.choice((100.0, 250.5, -40.0)),
           "pay_from_account": rng.choice(("", "CAD-1", "OLD"))}  # fmt: skip
    checked = {"requires_review": rng.random() < 0.5, "checks_stamp": store.checks_stamp(),
               "issues": rng.choice(FINDINGS)}  # fmt: skip
    invoice = store.add_invoice(path, doc, checked)
    store.refresh_confidence(invoice, rng.choice((0.3, 0.6, 0.9)), checked["requires_review"])
    return invoice


_ACTIONS = ("add", "park", "unpark", "approve", "reject", "reopen", "delete", "export", "confidence", "settings",
            "vendor master", "bank account", "sql column", "sql status", "sql findings", "sql coding", "sql move",
            "sql merge", "sql insert", "sql delete", "undone", "older", "restore")  # fmt: skip


def _step(rng: random.Random, store: Store, tmp_path, ground_truth, n: int, backup) -> str:
    """One write, picked at random among the ways an invoice of the queue changes: through a Store (a worker's), plain
    SQL, or an older AP Coder's."""
    path = store.path
    review_ids, approved = _ids(path, "review"), _ids(path, "approved")
    action = rng.choices(_ACTIONS, [0.5 if a == "restore" else 1 for a in _ACTIONS])[0]
    if action == "add" or not review_ids:
        _add(store, tmp_path, ground_truth, rng, n)
    elif action == "park":
        store.park_invoice(rng.choice(review_ids), "Ann", "waiting for the PO")
    elif action == "unpark" and _ids(path, "parked"):
        store.unpark_invoice(rng.choice(_ids(path, "parked")), "Ann")
    elif action == "approve":
        invoice = rng.choice(review_ids)
        store.approve_invoice(invoice, {**ground_truth, "vendor_name": store.get_invoice(invoice)["vendor_name"],
                                        "pay_from_account": rng.choice(("CAD-1", ""))}, "Jordan Lee")  # fmt: skip
    elif action == "reject":
        store.reject_invoice(rng.choice(review_ids), "Ann", "a copy")
    elif action == "reopen" and _ids(path, "rejected"):
        store.reopen(rng.choice(_ids(path, "rejected")), "Ann", "again")
    elif action == "delete":
        store.delete_invoice(rng.choice(review_ids), actor="Ann")
    elif action == "export" and approved:
        with sqlite3.connect(path) as conn:
            ready = [r[0] for r in conn.execute("SELECT id FROM invoices WHERE status = 'approved' AND export_batch "
                                                "IS NULL")]  # fmt: skip
        if ready:
            store.create_export_batch(rng.sample(ready, 1), "csv", "Ann")
    elif action == "confidence":  # what a check of a review screen writes
        store.refresh_confidence(rng.choice(review_ids), rng.random(), rng.random() < 0.5)
    elif action == "settings":  # the checks then made against others
        _plain(path, "INSERT INTO settings (key, value) VALUES ('default_terms_days', ?) ON CONFLICT(key) DO UPDATE "
                     "SET value = excluded.value", str(rng.randint(10, 90)))  # fmt: skip
    elif action == "vendor master":
        store.save_vendor(f"v{n}", rng.choice(VENDORS), status=rng.choice(("active", "on_hold")), actor="Ann")
    elif action == "bank account":  # the queue's Pay from: an account in use, or none
        store.save_bank_accounts([{"code": "CAD-1", "name": "Operating", "currency": "CAD", "institution": "001",
                                   "transit": "00011", "account_number": "1234567", "gl_account": "1010",
                                   "is_default": rng.random() < 0.5, "active": rng.random() < 0.7}])  # fmt: skip
    elif action == "sql column":
        column, value = rng.choice((("invoice_number", f"X-{n}"), ("grand_total", 99.5), ("currency", "USD"),
                                    ("due_date", "2026-02-01"), ("file_name", f"f{n}.pdf"), ("error", "late"),
                                    ("adjusted_confidence", 0.05), ("requires_review", 1)))  # fmt: skip
        _plain(path, f"UPDATE invoices SET {column} = ? WHERE id = ?", value, rng.choice(review_ids))
    elif action == "sql status":
        _plain(path, "UPDATE invoices SET status = ? WHERE id = ?",
               rng.choice(("parked", "pending_approval", "approved", "review", "failed")),
               rng.choice(_ids(path, "review", "parked", "pending_approval", "approved")))  # fmt: skip
    elif action == "sql findings":
        _plain(path, "UPDATE invoices SET validation = json_set(COALESCE(validation, '{}'), '$.issues', json(?)) "
                     "WHERE id = ?", json.dumps(rng.choice(FINDINGS)), rng.choice(review_ids))  # fmt: skip
    elif action == "sql coding":
        what = rng.choice(("$.pay_from_account", "$.line_items[0].predicted_gl_code"))
        _plain(path, f"UPDATE invoices SET ai_output = json_set(ai_output, '{what}', ?) WHERE id = ?",
               rng.choice(("", "CAD-1", "6100")), rng.choice(review_ids))  # fmt: skip
    elif action == "sql move":  # one invoice now another vendor's
        name = rng.choice(VENDORS)
        _plain(path, "UPDATE invoices SET vendor_name = ?, vendor_key = lower(?) WHERE id = ?", name, name,
               rng.choice(review_ids))  # fmt: skip
    elif action == "sql merge":  # every invoice of one vendor now another's (a vendor renamed or merged)
        _plain(path, "UPDATE invoices SET vendor_key = 'zeta', vendor_name = 'Zeta' WHERE vendor_key = lower(?)",
               rng.choice(VENDORS))  # fmt: skip
    elif action == "sql insert":
        _plain(path, "INSERT INTO invoices (source_path, file_name, status, created_at, vendor_name, invoice_number, "
                     "grand_total, adjusted_confidence) VALUES ('x', 'x', ?, ?, 'Beta Ltd', ?, 5, 0.4)",
               rng.choice(("review", "parked")), NOW, f"P-{n}")  # fmt: skip
    elif action == "sql delete":
        _plain(path, "DELETE FROM invoices WHERE id = ?", rng.choice(review_ids))
    elif action == "undone":  # a write under way, read inside it (afresh, as it stands), then undone
        invoice = rng.choice(review_ids)
        with pytest.raises(RuntimeError), store.session(), store._conn() as conn:
            conn.execute("UPDATE invoices SET status = 'parked', adjusted_confidence = 0.01 WHERE id = ?", (invoice,))
            assert invoice not in [r["id"] for r in store.list_invoices(REVIEW)]
            assert store.list_invoices(statuses=(PARKED,)) == _fresh_list(store, (PARKED,))
            assert invoice not in bulk.queue_checks(store, review_ids, recheck=False)[0]
            raise RuntimeError("undone")
    elif action == "older":  # an older AP Coder on the same database: the summary worked out, no log kept
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(summary_module, "queue_logged", lambda r: None)
            mp.setattr(summary_module, "LOGGED", "summary_logged_by_none")
            store.park_invoice(rng.choice(review_ids), "Ann", "an older AP Coder")
    elif action == "restore":
        store.restore_from(backup)
    return action


@pytest.mark.parametrize("seed, keep", [(1, 20_000), (2, 20_000), (3, 4), (4, 4)])
def test_each_workers_kept_queue_equals_a_fresh_computation_after_any_writes_in_any_order(
    workers, tmp_path, ground_truth, monkeypatch, seed, keep
):
    """Random writes by two workers, plain SQL and an older AP Coder (every way an invoice of the queue changes: added,
    checked, parked, approved, rejected, reopened, deleted, its findings, coding, vendor or status changed, the
    settings its checks are made against, the vendor master and the bank accounts, a write undone, a restore), each
    worker asking between them, the changes of several writes read at once; with a log of ``keep`` changes (4: some
    are pruned before a worker asks). Every list, the queue's checks and why each invoice needs attention are what
    working them out afresh gives, and most asks read only the invoices changed."""
    monkeypatch.setattr(summary_module, "QUEUE_CHANGES_KEPT", keep)
    rng = random.Random(seed)
    first, second = workers
    for n in range(8):
        with first.running() as store:
            _add(store, tmp_path, ground_truth, rng, n)
    backup = first.store.backup_now("manual")
    partly = []  # each read of the lists kept: of the invoices changed only, or whole
    real = review_queue.ReviewQueueMixin._listed
    monkeypatch.setattr(Store, "_listed", lambda self, ids: partly.append(ids is not None) or real(self, ids))
    done = []
    for n in range(8, 90):
        writer = rng.choice((first, second))
        with writer.running() as store:
            done.append(_step(rng, store, tmp_path, ground_truth, n, backup))
        for worker in rng.sample((first, second), rng.randint(1, 2)):
            with worker.running() as store:
                _same(store, rng, (seed, n, done[-3:]))
    assert {"undone", "older", "restore", "sql status", "confidence"} <= set(done)
    assert sum(partly) > (60 if keep > 100 else 20), (sum(partly), len(partly))


def test_a_change_reads_again_only_the_invoices_it_changed_and_the_log_keeps_the_latest(
    workers, tmp_path, ground_truth, monkeypatch
):
    """A check and a park each read again the one invoice they changed; nothing changed reads nothing; the log keeps its
    latest ``QUEUE_CHANGES_KEPT`` changes (50 here), so a worker that kept the queue before more changes than that
    reads it whole again, never missing one."""
    monkeypatch.setattr(summary_module, "QUEUE_CHANGES_KEPT", 50)
    first, second = workers
    rng = random.Random(7)
    with first.running() as store:
        ids = [_add(store, tmp_path, ground_truth, rng, n) for n in range(4)]
    read: list = []
    real = review_queue.ReviewQueueMixin._listed
    monkeypatch.setattr(Store, "_listed", lambda self, some: read.append(some) or real(self, some))
    with first.running() as store:
        assert store.list_invoices(REVIEW) == _fresh_list(store, (REVIEW,)) and read == [None]
        assert store.list_invoices(REVIEW) == _fresh_list(store, (REVIEW,)) and read == [None]  # nothing changed
        with second.running() as other:
            other.refresh_confidence(ids[0], 0.11, True)
            other.park_invoice(ids[1], "Ann", "waiting")
        assert store.list_invoices(statuses=(REVIEW, PARKED)) == _fresh_list(store, (REVIEW, PARKED))
        assert sorted(read[1]) == ids[:2] and len(read) == 2
        for n in range(51):  # more changes than the log keeps, of another invoice
            _plain(store.path, "UPDATE invoices SET invoice_number = ? WHERE id = ?", f"R-{n}", ids[3])
            store.summary_fetch("SELECT 1")  # (worked into the summary, and logged, change by change)
        with sqlite3.connect(store.path) as conn:
            first_seq, last_seq, logged = conn.execute("SELECT MIN(seq), MAX(seq), COUNT(*) FROM queue_changes"
                                                       ).fetchone()  # fmt: skip
        assert logged == 50 and first_seq == last_seq - logged + 1
        assert store.list_invoices(REVIEW) == _fresh_list(store, (REVIEW,)) and read[2:] == [None]  # read whole


def test_a_database_made_before_the_log_gets_it_at_start_and_a_backups_copy_while_restored(
    workers, tmp_path, ground_truth, monkeypatch
):
    """No schema version of its own: the log is made at every start, and in the copy of a backup made before it while
    the copy is restored (another worker's change meanwhile is written and logged, not refused for a table missing)."""
    from ap_coder.core.store import backup_files

    first, second = workers
    with first.running() as store:
        ids = [_add(store, tmp_path, ground_truth, random.Random(1), n) for n in range(3)]
        backup = store.backup_now("manual")
    for path in (first.store.path, backup):
        _plain(path, "DROP TABLE queue_changes")
    Store(first.store.path)  # the next start
    _plain(first.store.path, "UPDATE invoices SET invoice_number = 'S-1' WHERE id = ?", ids[0])
    with second.running() as worker:
        assert worker.list_invoices(REVIEW) == _fresh_list(worker, (REVIEW,))
    seen: dict = {}
    files = backup_files.restore_files

    def copying(*args, **kwargs):
        with second.running() as worker:
            seen["before"] = worker.list_invoices(REVIEW)
            with first.running() as other:
                other.park_invoice(ids[0], "Ann", "waiting for the PO")
            seen["after"], seen["fresh"] = worker.list_invoices(REVIEW), _fresh_list(worker, (REVIEW,))
        return files(*args, **kwargs)

    monkeypatch.setattr(backup_files, "restore_files", copying)
    with first.running() as store:
        store.restore_from(backup)
    assert sorted(r["id"] for r in seen["before"]) == ids
    assert seen["after"] == seen["fresh"] and ids[0] not in [r["id"] for r in seen["after"]]
    with second.running() as worker:
        assert worker.list_invoices(REVIEW) == _fresh_list(worker, (REVIEW,))


def test_after_a_restore_the_queue_is_read_whole_even_once_its_log_is_past_where_a_worker_kept_it(
    workers, tmp_path, ground_truth
):
    """A restored database is another (its ``summary_id``): a worker that kept the queue before the restore reads it
    whole, even when the restored log has since come back past the change it kept (the backup's log is older)."""
    first, second = workers
    with first.running() as store:
        ids = [_add(store, tmp_path, ground_truth, random.Random(2), n) for n in range(3)]
        backup = store.backup_now("manual")
        for n in range(3):
            store.refresh_confidence(ids[n], 0.2 + n / 10, True)
    with second.running() as worker:
        assert worker.list_invoices(REVIEW) == _fresh_list(worker, (REVIEW,))  # kept, as of the last confidence
    with first.running() as store:
        store.restore_from(backup)
        for n in range(8):  # the restored log past it, by other changes
            store.refresh_confidence(ids[2], 0.5 + n / 100, True)
    with second.running() as worker:
        assert worker.list_invoices(REVIEW) == _fresh_list(worker, (REVIEW,))
