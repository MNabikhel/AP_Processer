"""Bulk approvals as the database keeps them (D-185, ``core/store/bulk_jobs.py``): one open at a time a person, each
invoice's outcome kept with its approval while the job is still the runner's, taken again when its process stopped,
ended with why after too many stops or an update, and none of another database's."""

from __future__ import annotations

import sqlite3
import threading
import time

from ap_coder.core.store import APPROVED, REVIEW, Store, bulk_jobs

PERSON = {"name": "Ann", "login": "ann", "server": False, "as": {}}
CODED = {"vendor_name": "North Supply", "invoice_number": "N-1", "grand_total": 10.0, "line_items": []}


def _invoices(store: Store, tmp_path, n: int) -> list[list]:
    """``n`` invoices in the queue, as a job asks them: each [id, its row identity]."""
    out = []
    for i in range(n):
        path = tmp_path / f"in-{i}.pdf"
        path.write_bytes(f"%PDF-1.4 {i}".encode())
        iid = store.add_invoice(path, {**CODED, "invoice_number": f"N-{i}"}, None)
        out.append([iid, list(store.row_identity(iid))])
    return out


def _approve(store: Store, iid: int):
    return lambda: store.approve_invoice(iid, {**CODED, "invoice_number": f"N-{iid}"}, "Ann", bulk=True, login="ann")


def test_a_person_has_one_bulk_approval_open_at_a_time_however_many_web_workers_ask(store, tmp_path):
    """Four web workers asking at once for one person: one job, given to all four; another person's is their own."""
    ids = _invoices(store, tmp_path, 2)
    workers = [Store(store.path) for _ in range(4)]
    ready, found = threading.Barrier(len(workers)), []

    def ask(worker: Store) -> None:
        ready.wait()
        found.append(worker.start_bulk_job("user:1", PERSON, ids, "bulk-1"))

    threads = [threading.Thread(target=ask, args=(w,)) for w in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len({job for job, _ in found}) == 1 and sorted(new for _, new in found) == [False, False, False, True]
    other, new = store.start_bulk_job("user:2", PERSON, ids, "bulk-1")
    assert new and other != found[0][0]
    assert store.latest_bulk_job("user:1")["id"] == found[0][0] and store.latest_bulk_job("user:3") is None


def test_each_invoice_is_approved_with_its_outcome_and_only_by_the_runner_that_holds_the_job(store, tmp_path):
    """The approval and the job's record of it are one transaction: a runner that lost the job (another took it
    again) approves nothing; an invoice the job has already is never approved again; an approval that finds the
    invoice gone from the queue is kept as left, with why."""
    (one, _), (two, _), (three, _) = ids = _invoices(store, tmp_path, 3)
    job, _ = store.start_bulk_job("user:1", PERSON, ids, "bulk-1")
    assert store.claim_bulk_job("runner-a", 30, "bulk-1")["id"] == job
    assert store.bulk_step(job, "runner-a", one, "", _approve(store, one)) == "approved"
    assert store.bulk_step(job, "runner-a", one, "", _approve(store, one)) is None  # (it has it already)
    assert store.bulk_step(job, "runner-b", two, "", _approve(store, two)) is None  # not runner b's
    assert store.get_invoice(two)["status"] == REVIEW
    store.approve_invoice(three, CODED, "Bob")  # someone else, meanwhile
    assert store.bulk_step(job, "runner-a", three, "", _approve(store, three)) == "skipped"
    assert store.bulk_step(job, "runner-a", two, "it is a credit note") == "skipped"
    found = store.bulk_job(job)
    assert found["approved"] == [one] and found["skipped"] == [[three, bulk_jobs.GONE], [two, "it is a credit note"]]
    assert store.get_invoice(one)["status"] == APPROVED and store.get_invoice(three)["reviewer"] == "Bob"
    with sqlite3.connect(store.path) as conn:
        events = conn.execute("SELECT invoice_id FROM events WHERE action = 'approved' ORDER BY id").fetchall()
    assert [e[0] for e in events] == [one, three]  # each approved once
    store.end_bulk_job(job, "runner-a")
    assert store.bulk_job(job)["state"] == bulk_jobs.DONE


def test_a_job_its_process_left_is_taken_again_then_ends_with_why_after_too_many_stops(store, tmp_path):
    """A process that stopped while approving: its job is taken again once it is stale (or at once at the start of the
    one process that approves them), what it did kept; after ``TRIES`` stops it ends, saying so, nothing undone."""
    ids = _invoices(store, tmp_path, 3)
    job, _ = store.start_bulk_job("user:1", PERSON, ids, "bulk-1")
    first = store.claim_bulk_job("runner-a", 30, "bulk-1")
    assert store.bulk_step(job, "runner-a", ids[0][0], "", _approve(store, ids[0][0])) == "approved"
    assert store.claim_bulk_job("runner-b", 30, "bulk-1") is None  # (still renewed)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE bulk_jobs SET renewed_at = ? WHERE id = ?", (time.time() - 60, job))
    again = store.claim_bulk_job("runner-b", 30, "bulk-1")
    assert again["id"] == job and again["approved"] == [ids[0][0]] and again["tries"] == first["tries"] + 1
    assert store.release_left_bulk_jobs() == 1  # (the start of the process that approves them: b stopped)
    third = store.claim_bulk_job("runner-c", 30, "bulk-1")
    assert third["tries"] == bulk_jobs.TRIES
    store.release_left_bulk_jobs()
    assert store.claim_bulk_job("runner-d", 30, "bulk-1") is None  # stopped three times: it ends
    ended = store.bulk_job(job)
    assert ended["state"] == bulk_jobs.STOPPED and "stopped 3 times" in ended["error"]
    assert ended["approved"] == [ids[0][0]] and store.get_invoice(ids[1][0])["status"] == REVIEW


def test_a_job_put_back_by_its_process_stopping_is_not_counted_as_a_try(store, tmp_path):
    ids = _invoices(store, tmp_path, 1)
    job, _ = store.start_bulk_job("user:1", PERSON, ids, "bulk-1")
    for _ in range(bulk_jobs.TRIES + 2):
        assert store.claim_bulk_job("runner-a", 30, "bulk-1")["id"] == job
        store.release_bulk_job(job, "runner-a")
    assert store.bulk_job(job)["state"] == bulk_jobs.WAITING


def test_a_job_left_by_an_older_ap_coder_ends_with_why(store, tmp_path):
    job, _ = store.start_bulk_job("user:1", PERSON, _invoices(store, tmp_path, 1), "bulk-0")
    assert store.claim_bulk_job("runner-a", 30, "bulk-1") is None
    assert store.bulk_job(job)["state"] == bulk_jobs.STOPPED and store.bulk_job(job)["error"] == bulk_jobs.UPDATED


def test_a_job_of_the_database_before_a_restore_is_no_ones(store, tmp_path):
    """A restore gives the database another ``summary_id``: the jobs before it are not shown, taken nor kept."""
    job, _ = store.start_bulk_job("user:1", PERSON, _invoices(store, tmp_path, 1), "bulk-1")
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE settings SET value = 'another' WHERE key = 'summary_id'")
    assert store.bulk_job(job) is None and store.latest_bulk_job("user:1") is None
    assert store.claim_bulk_job("runner-a", 30, "bulk-1") is None
    store.start_bulk_job("user:1", PERSON, [], "bulk-1")
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bulk_jobs").fetchone()[0] == 1


def test_the_jobs_ended_are_kept_a_while_the_latest_few_of_each_person(store, tmp_path, monkeypatch):
    ids = _invoices(store, tmp_path, 1)
    for _ in range(bulk_jobs.KEPT_EACH + 2):
        job, _ = store.start_bulk_job("user:1", PERSON, ids, "bulk-1")
        store.claim_bulk_job("runner-a", 30, "bulk-1")
        store.end_bulk_job(job, "runner-a")
    other, _ = store.start_bulk_job("user:2", PERSON, ids, "bulk-1")
    with sqlite3.connect(store.path) as conn:
        kept = [r[0] for r in conn.execute("SELECT owner FROM bulk_jobs ORDER BY id")]
    assert kept.count("user:1") == bulk_jobs.KEPT_EACH and kept.count("user:2") == 1
    monkeypatch.setattr(bulk_jobs.time, "time", lambda: time.time_ns() / 1e9 + bulk_jobs.KEEP + 60)
    store.start_bulk_job("user:3", PERSON, ids, "bulk-1")
    with sqlite3.connect(store.path) as conn:
        kept = [r[0] for r in conn.execute("SELECT owner FROM bulk_jobs ORDER BY id")]
    assert kept == ["user:2", "user:3"]  # (user:2's is still open: kept however old)
    assert store.bulk_job(other)["state"] == bulk_jobs.WAITING


def test_a_job_of_another_database_never_has_the_runners_take_the_write_lock(store, tmp_path):
    """The look for a job waiting had no ``db = ?``: one of another database left in the table (a backup restored that
    was taken while a job waited) had each runner take the write lock at every look, about four times a second."""
    job, _ = store.start_bulk_job("user:1", PERSON, _invoices(store, tmp_path, 1), "bulk-1")
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE bulk_jobs SET db = 'the database before a restore' WHERE id = ?", (job,))
    statements: list[str] = []
    with store.session(), store._conn() as conn:
        conn.set_trace_callback(statements.append)
        assert store.claim_bulk_job("runner-a", 30, "bulk-1") is None
        conn.set_trace_callback(None)
    assert statements and not [s for s in statements if s.startswith("BEGIN")]
