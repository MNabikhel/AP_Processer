"""The downloads the company server's background process makes, as the database keeps them (D-169,
``core/store/reports.py``): one job for the same person's same file however many web workers ask, a few at once a
person, taken again when its process stopped, gone with an update or a restore, the files made kept a while. (The
answers kept for every process: ``tests/core/test_kept_answers.py``.)"""

from __future__ import annotations

import sqlite3
import threading

from ap_coder.core.store import Store, reports

PARAMS = {"query": "status=approved"}


def _start(store: Store, owner: str = "user:1", params: dict | None = None, most: int = 3) -> tuple[int | None, bool]:
    return store.start_download(owner, "/search/export.csv", params or PARAMS, "invoice_directory.csv", most, "f-1")


def test_the_same_persons_file_asked_from_several_web_workers_at_once_is_one_job(tmp_path):
    """Four web workers (four Stores, four threads) asking at once for one person's same file: one job, the others
    given it; another person's same file is a job of its own."""
    Store(tmp_path / "ap.db")
    workers = [Store(tmp_path / "ap.db") for _ in range(4)]
    ready, found = threading.Barrier(len(workers)), []

    def ask(store: Store) -> None:
        ready.wait()
        found.append(_start(store))

    threads = [threading.Thread(target=ask, args=(store,)) for store in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len({job for job, _ in found}) == 1 and sorted(new for _, new in found) == [False, False, False, True]
    assert _start(workers[0], owner="user:2")[0] != found[0][0]
    with sqlite3.connect(tmp_path / "ap.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM report_jobs").fetchone()[0] == 2


def test_a_person_has_at_most_a_few_files_waiting_and_each_job_is_theirs(tmp_path):
    store = Store(tmp_path / "ap.db")
    jobs = [_start(store, params={"query": f"q={n}"})[0] for n in range(3)]
    assert None not in jobs and _start(store, params={"query": "q=3"}) == (None, False)  # (a fourth: refused)
    assert _start(store, owner="user:2", params={"query": "q=3"})[0] is not None  # (another person's own count)
    job = store.download_job(jobs[0])
    assert job["owner"] == "user:1" and job["params"] == {"query": "q=0"} and job["state"] == reports.WAITING
    assert store.download_job(jobs[2])["ahead"] == 2  # (two waiting before it)
    claimed = store.claim_download("runner", 60, "f-1")
    assert claimed["id"] == jobs[0] and store.finish_download(jobs[0], "runner", "1.x.csv", 10)
    assert _start(store, params={"query": "q=3"})[0] is not None  # (one made: room for another)


def test_a_job_whose_background_process_stopped_is_made_again_then_fails_after_two_tries(tmp_path, monkeypatch):
    """A job being made whose process stopped renewing it (killed, the server restarted) is taken again by the next;
    the first one's late finish is refused (its file is not the job's); stopped twice, it fails with why."""
    clock = [1000.0]
    monkeypatch.setattr(reports.time, "time", lambda: clock[0])
    store = Store(tmp_path / "ap.db")
    job, _ = _start(store)
    assert store.claim_download("first", 60, "f-1")["tries"] == 1
    assert store.claim_download("second", 60, "f-1") is None  # (renewed lately: still its own)
    clock[0] += 30
    assert store.renew_download(job, "first", 10, 100, "10 of 100")
    clock[0] += 61  # (no note for over a minute: that process stopped)
    again = store.claim_download("second", 60, "f-1")
    assert again["id"] == job and again["tries"] == 2 and again["state"] == reports.RUNNING
    assert not store.renew_download(job, "first", 20, 100, "") and not store.finish_download(job, "first", "a", 1)
    clock[0] += 61
    assert store.claim_download("third", 60, "f-1") is None
    failed = store.download_job(job)
    assert failed["state"] == reports.FAILED and failed["error"] == reports.STOPPED_TWICE


def test_a_job_put_back_by_a_process_stopping_is_made_by_the_next_as_it_was(tmp_path):
    store = Store(tmp_path / "ap.db")
    job, _ = _start(store)
    store.claim_download("stopping", 60, "f-1")
    store.release_download(job, "stopping")
    assert store.download_job(job)["state"] == reports.WAITING
    again = store.claim_download("next", 60, "f-1")
    assert again["id"] == job and again["tries"] == 1  # (not counted against it)


def test_a_job_an_older_ap_coder_left_fails_and_one_of_the_database_before_a_restore_is_no_ones(tmp_path):
    """After an update, a job the older version asked for fails with why (its params may mean something else now);
    after a restore, the jobs (and kept answers) of the database before are gone: none is made, none is shown."""
    store = Store(tmp_path / "ap.db")
    old, _ = _start(store)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE report_jobs SET version = '0.0.1/f-1' WHERE id = ?", (old,))
    assert store.claim_download("runner", 60, "f-1") is None
    assert store.download_job(old)["error"] == reports.UPDATED
    store.keep_answer("pairs", reports.asked_key(None), "p-1", store._answer_stamp("", ())[0], [1])
    waiting, _ = _start(store, params={"query": "q=1"})
    backup = store.backup_now("manual")
    store.restore_from(backup)
    assert store.download_job(waiting) is None and store.claim_download("runner", 60, "f-1") is None
    store.sweep_downloads(3600, 5)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM report_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM report_answers").fetchone()[0] == 0


def test_the_files_made_are_kept_a_while_and_the_latest_few_of_each_person(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(reports.time, "time", lambda: clock[0])
    store = Store(tmp_path / "ap.db")

    def made(owner: str, n: int) -> int:
        job, _ = _start(store, owner=owner, params={"query": f"q={n}"})
        store.claim_download("runner", 60, "f-1")
        assert store.finish_download(job, "runner", f"{job}.csv", 1)
        clock[0] += 1
        return job

    ann = [made("user:1", n) for n in range(4)]
    bob = made("user:2", 0)
    assert sorted(store.sweep_downloads(3600, 3)) == [f"{ann[0]}.csv"]  # (Ann's oldest: three kept each)
    clock[0] += 3600
    assert sorted(store.sweep_downloads(3600, 3)) == sorted(f"{j}.csv" for j in [*ann[1:], bob])  # (an hour on)
