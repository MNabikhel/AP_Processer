"""Bulk approval as a job (D-185): the web worker asked holds no thread for it; it is approved in the background (the
company server's background process, a thread of the laptop's own) as the person who asked, while their page says how
far it got; safe to leave and come back, one at a time a person, each invoice approved once even across a restart."""

from __future__ import annotations

import re
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from ap_coder.coding import bulk
from ap_coder.core.store import APPROVED, REVIEW, Store, bulk_jobs, load_sample_setup
from ap_coder.web import jobs
from ap_coder.web.app import create_app
from ap_coder.web.pages import review
from tests.webclient import PAGE, add_clean, client_for, enrol, sign_in, web_settings

GOOD = "correct horse battery"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


def _clean(store: Store, n: int) -> list[int]:
    store.import_vendor_master([{"vendor_name": f"Bulk Supplier {i}", "erp_id": f"B{i}"} for i in range(n)])
    return [add_clean(store, vendor_name=f"Bulk Supplier {i}", invoice_number=f"B-{i}") for i in range(n)]


def _asked(store: Store, ids: list[int]) -> list[list]:
    """``ids`` as a job keeps them: each with its row identity and stamp as they are now."""
    stamps = store.bulk_stamps(ids)
    return [[i, list(store.row_identity(i)), stamps[i]] for i in ids]


class Gate:
    """Each invoice's check of a bulk approval held until ``open`` (``hold``: from the n-th on), then slowed by
    ``pause`` seconds: a long approval, seen while it goes on."""

    def __init__(self, monkeypatch, hold: int = 0, pause: float = 0.0) -> None:
        self.go, self.seen, self.hold, self.pause = threading.Event(), [], hold, pause
        real = bulk._still_clean

        def checked(store, reference, settings, invoice_id, stamp, server):
            self.seen.append(invoice_id)
            if len(self.seen) > self.hold:
                assert self.go.wait(30)
            time.sleep(self.pause)
            return real(store, reference, settings, invoice_id, stamp, server)

        monkeypatch.setattr(bulk, "_still_clean", checked)

    def reached(self, n: int) -> None:
        ends = time.monotonic() + 20
        while len(self.seen) < n and time.monotonic() < ends:
            time.sleep(0.01)
        assert len(self.seen) >= n, self.seen


def _ended(store: Store, job: int | None = None, seconds: float = 30) -> dict:
    ends = time.monotonic() + seconds
    while True:
        found = store.bulk_job(job) if job else store.latest_bulk_job("")
        if found and found["state"] in (bulk_jobs.DONE, bulk_jobs.STOPPED) or time.monotonic() > ends:
            return found
        time.sleep(0.02)


def _approvals(store: Store) -> list[int]:
    with sqlite3.connect(store.path) as conn:
        return [r[0] for r in conn.execute("SELECT invoice_id FROM events WHERE action = 'approved' ORDER BY id")]


def test_a_long_bulk_approval_answers_at_once_and_its_page_says_how_far_it_got(store, monkeypatch):
    """Approving 200 invoices held a web worker's thread for the whole run, with no progress. The
    POST answers within ``BULK_WAIT`` with the job's page, which says "N of M approved, x need a look" while it goes
    on, and the queue says it too (safe to leave and come back); once done, what was done, said once."""
    monkeypatch.setattr(review, "BULK_WAIT", 0.2)
    ids = _clean(store, 12)
    gate = Gate(monkeypatch, hold=3)
    client = client_for(store)
    began = time.monotonic()
    answer = client.post("/review/bulk-approve", data={"ids": [str(i) for i in ids]}, headers=PAGE)
    assert answer.status_code == 200 and answer.json()["redirect"] == "/review/bulk"
    assert time.monotonic() - began < 2  # (not the whole run)
    gate.reached(4)
    page = client.get("/review/bulk").text
    assert "Approving: 3 of 12 approved, 0 need a look" in page and 'data-job-status="/review/bulk/status?job=' in page
    queue = client.get("/review").text
    assert "Your bulk approval is under way: 3 of 12 approved, 0 need a look." in queue
    status = client.get(f"/review/bulk/status?job={store.latest_bulk_job('')['id']}").json()
    assert status["state"] == "running" and status["note"] == "3 of 12 approved, 0 need a look"
    gate.go.set()
    job = _ended(store)
    assert job["state"] == bulk_jobs.DONE and job["approved"] == ids
    assert all(store.get_invoice(i)["status"] == APPROVED for i in ids) and sorted(_approvals(store)) == ids
    assert "Your bulk approval is done: 12 of 12 approved, 0 need a look." in client.get("/review").text
    done = client.get("/review/bulk").text
    assert "Done: 12 of 12 approved, 0 need a look." in done and "data-job-status" not in done
    assert "Your bulk approval" not in client.get("/review").text  # (said once it was seen)


def test_a_person_has_one_bulk_approval_at_a_time_and_its_page_is_theirs(store, monkeypatch):
    monkeypatch.setattr(review, "BULK_WAIT", 0.1)
    ids = _clean(store, 4)
    gate = Gate(monkeypatch)
    client = client_for(store)
    client.post("/review/bulk-approve", data={"ids": [str(i) for i in ids[:2]]}, headers=PAGE)
    again = client.post("/review/bulk-approve", data={"ids": [str(i) for i in ids[2:]]}, headers=PAGE).json()
    assert again["redirect"].startswith("/review/bulk?flash=")
    assert "still under way" in client.get(again["redirect"]).text
    gate.go.set()
    job = _ended(store)
    assert job["approved"] == ids[:2] and [store.get_invoice(i)["status"] for i in ids[2:]] == [REVIEW, REVIEW]
    assert client.get("/review/bulk/status?job=999").status_code == 404


def test_an_invoice_edited_or_acted_on_while_the_job_ran_is_left_for_a_person(store, monkeypatch):
    """The job checks each invoice as it comes to it: one someone has edits of on its review screen is left for that
    screen, one approved meanwhile by someone else is left (never approved twice), one no longer the invoice the
    queue showed (another under its number) is left too; the rest are approved."""
    monkeypatch.setattr(review, "BULK_WAIT", 0.1)
    ids = _clean(store, 5)
    gate = Gate(monkeypatch, hold=1)
    client = client_for(store)
    client.post("/review/bulk-approve", data={"ids": [str(i) for i in ids]}, headers=PAGE)
    gate.reached(2)
    store.save_invoice_draft(ids[2], "", {"header": {}, "lines": [], "taxes": []})  # a review screen's edits
    store.approve_invoice(ids[3], store.get_invoice(ids[3])["ai_output"], "Bob")  # someone else's approval
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET file_sha256 = 'another' WHERE id = ?", (ids[4],))
    gate.go.set()
    job = _ended(store)
    assert job["approved"] == ids[:2]
    assert job["skipped"] == [[ids[2], bulk_jobs.EDITED], [ids[3], "no longer in the queue"], [ids[4], bulk.CHANGED]]
    assert store.get_invoice(ids[3])["reviewer"] == "Bob" and _approvals(store).count(ids[3]) == 1
    page = client.get("/review/bulk").text
    assert "Done: 2 of 5 approved, 3 need a look." in page and "it has edits open on its review screen" in page
    assert f'href="/review/{ids[2]}"' in page and f'href="/review/{ids[3]}"' not in page  # (only those still waiting)


def _bulk_form(client: TestClient) -> dict[str, list[str]]:
    """The queue's bulk approval form, as the page drew it: each invoice and its stamp."""
    page = client.get("/review").text
    form = page[page.index('action="/review/bulk-approve"') :]
    form = form[: form.index("</form>")]
    return {name: re.findall(f'name="{name}" value="([^"]*)"', form) for name in ("ids", "revs")}


def test_an_invoice_whose_coding_moved_after_the_queue_listed_it_is_left_for_a_person(store, monkeypatch):
    """The job compared only which invoice it was, not its coding: one approved by Bob with his correction and the
    approval undone (back in the queue, his coding kept), one parked and brought back, one read again by the page
    reader, all after the queue listed them, were approved as the person asking with coding they never saw."""
    monkeypatch.setattr(review, "BULK_WAIT", 5.0)
    ids = _clean(store, 4)
    client = client_for(store)
    form = _bulk_form(client)
    assert form["ids"] == [str(i) for i in ids] and len(form["revs"]) == len(ids)
    corrected = {**store.get_invoice(ids[0])["ai_output"], "invoice_number": "BOB-HALF-DONE"}
    store.approve_invoice(ids[0], corrected, "Bob")
    store.reopen(ids[0], "Bob", "Approval undone")
    store.park_invoice(ids[1], "Bob", "Waiting for the PO")
    store.unpark_invoice(ids[1], "Bob")
    inv = store.get_invoice(ids[2])
    read = {**inv["ai_output"], "invoice_number": "READ-AGAIN"}
    assert store.replace_proposal(ids[2], read, inv["validation"], None, expect=store.row_identity(ids[2]))
    client.post("/review/bulk-approve", data=form, headers=PAGE)
    job = _ended(store)
    assert job["approved"] == ids[3:] and job["skipped"] == [[i, bulk.CHANGED] for i in ids[:3]]
    assert [store.get_invoice(i)["status"] for i in ids[:3]] == [REVIEW] * 3
    assert store.get_invoice(ids[0])["final_output"]["invoice_number"] == "BOB-HALF-DONE"


def test_coding_stored_while_the_job_checks_an_invoice_is_never_written_over(store, monkeypatch):
    """Between the job's check of an invoice and its approval, Bob's correction (approved and undone) or the page
    reader's new reading landed: the job approved the coding it had checked, over theirs, and the approval recorded
    the difference as the asker's corrections."""
    ids = _clean(store, 2)
    real = bulk._still_clean

    def meanwhile(store_, reference, settings, invoice_id, stamp, server):
        out = real(store_, reference, settings, invoice_id, stamp, server)
        inv = store_.get_invoice(invoice_id)
        if invoice_id == ids[0]:
            store_.approve_invoice(invoice_id, {**inv["ai_output"], "invoice_number": "BOB-CORRECTED"}, "Bob")
            store_.reopen(invoice_id, "Bob", "Approval undone")
        else:
            assert store_.replace_proposal(invoice_id, {**inv["ai_output"], "invoice_number": "READ-AGAIN"},
                                           inv["validation"], None, expect=store_.row_identity(invoice_id))  # fmt: skip
        return out

    monkeypatch.setattr(bulk, "_still_clean", meanwhile)
    job, _ = store.start_bulk_job("", {"name": "Sam", "login": "sam", "server": False, "as": {}},
                                  _asked(store, ids),
                                  bulk.JOB_FORMAT)  # fmt: skip
    jobs.run_bulk(store, web_settings(), store.claim_bulk_job("r", jobs.BULK_STALE, bulk.JOB_FORMAT), "r")
    assert store.bulk_job(job)["skipped"] == [[i, bulk.CHANGED] for i in ids]
    first, second = store.get_invoice(ids[0]), store.get_invoice(ids[1])
    assert first["status"] == second["status"] == REVIEW and first["final_output"]["invoice_number"] == "BOB-CORRECTED"
    assert second["ai_output"]["invoice_number"] == "READ-AGAIN" and not second["final_output"]
    assert _approvals(store) == [ids[0]]  # (Bob's)


def test_a_bulk_approval_left_by_a_process_that_stopped_goes_on_and_approves_each_invoice_once(store, monkeypatch):
    """A process stopped (or hung) while it approved: once its job is stale another takes it again and goes on from
    the next invoice; the first, coming back, finds it is no longer its own and approves nothing more."""
    ids = _clean(store, 6)
    job, _ = store.start_bulk_job("", {"name": "Sam", "login": "sam", "server": False, "as": {}},
                                  _asked(store, ids), bulk.JOB_FORMAT)  # fmt: skip
    gate = Gate(monkeypatch, hold=2)
    first = store.claim_bulk_job("process-a", jobs.BULK_STALE, bulk.JOB_FORMAT)
    stuck = threading.Thread(target=jobs.run_bulk, args=(store, web_settings(), first, "process-a"), daemon=True)
    stuck.start()
    gate.reached(3)  # two approved, the third held: process a "stopped" there
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE bulk_jobs SET renewed_at = ? WHERE id = ?", (time.time() - 2 * jobs.BULK_STALE, job))
    second = store.claim_bulk_job("process-b", jobs.BULK_STALE, bulk.JOB_FORMAT)
    assert second["approved"] == ids[:2]
    gate.hold = len(ids) * 2  # (process b's go through)
    jobs.run_bulk(store, web_settings(), second, "process-b")
    gate.go.set()
    stuck.join(20)
    ended = store.bulk_job(job)
    assert ended["state"] == bulk_jobs.DONE and ended["approved"] == ids and ended["skipped"] == []
    assert sorted(_approvals(store)) == ids  # each once
    assert {store.get_invoice(i)["reviewer"] for i in ids} == {"Sam"}


def test_a_laptop_started_again_goes_on_with_the_bulk_approval_its_last_run_left(store, monkeypatch):
    ids = _clean(store, 3)
    job, _ = store.start_bulk_job("", {"name": "Sam", "login": "sam", "server": False, "as": {}},
                                  _asked(store, ids), bulk.JOB_FORMAT)  # fmt: skip
    left = store.claim_bulk_job("the last run", jobs.BULK_STALE, bulk.JOB_FORMAT)
    store.bulk_step(job, "the last run", ids[0], "", lambda: store.approve_invoice(ids[0], left["ids"] and
                    store.get_invoice(ids[0])["ai_output"], "Sam", bulk=True, login="sam"))  # fmt: skip
    jobs.resume_bulk(store, lambda: web_settings())
    ended = _ended(store, job)
    assert ended["state"] == bulk_jobs.DONE and ended["approved"] == ids and sorted(_approvals(store)) == ids


def test_a_stop_puts_the_bulk_approval_back_in_line_and_the_next_start_goes_on(store, monkeypatch):
    """The background process stopping (an update, the service restarted): the invoice under way is done, the job put
    back in line, its try not counted; the next start's runner goes on with it."""
    ids = _clean(store, 4)
    gate = Gate(monkeypatch, hold=1)
    store.start_bulk_job("", {"name": "Sam", "login": "sam", "server": False, "as": {}},
                         _asked(store, ids), bulk.JOB_FORMAT)  # fmt: skip
    runner = jobs.BulkRunner(web_settings, lambda: store).start()
    gate.reached(2)
    runner.stop()
    gate.go.set()
    runner.thread.join(10)
    assert not runner.thread.is_alive()
    job = store.latest_bulk_job("")
    assert job["state"] == bulk_jobs.WAITING and job["approved"] == ids[:2] and job["tries"] == 0
    again = jobs.BulkRunner(web_settings, lambda: store).start()
    try:
        assert _ended(store, job["id"])["approved"] == ids and sorted(_approvals(store)) == ids
    finally:
        again.stop()
        again.thread.join(10)


def test_quitting_the_laptop_while_it_approves_puts_the_job_back_without_using_a_try(store, monkeypatch):
    """Closing AP Coder on a laptop left its bulk approval "being approved": each start put it back without giving its
    try back, so the third time it ended, "AP Coder stopped 3 times", though the person only closed AP Coder (an
    orderly stop, not counted). Now each close puts it back after the invoice under way, as the server's does."""
    monkeypatch.setattr(review, "BULK_WAIT", 0.1)
    ids = _clean(store, 2 * bulk_jobs.TRIES + 1)
    gates: list[Gate] = []
    real_stop = jobs.stop_here

    def stop_here(store_):
        thread = real_stop(store_)
        gates[-1].go.set()  # (the invoice under way is done once AP Coder is told to stop)
        return thread

    monkeypatch.setattr(jobs, "stop_here", stop_here)
    for closed in range(1, bulk_jobs.TRIES + 1):
        gates.append(Gate(monkeypatch, hold=1))
        with client_for(store) as client:  # (started: the bulk approval its last run left goes on)
            if closed == 1:
                client.post("/review/bulk-approve", data={"ids": [str(i) for i in ids]}, headers=PAGE)
            gates[-1].reached(2)
        job = store.latest_bulk_job("")
        assert job["state"] == bulk_jobs.WAITING and job["tries"] == 0 and job["approved"] == ids[: 2 * closed]
    gates.append(Gate(monkeypatch))
    gates[-1].go.set()
    with client_for(store):
        ended = _ended(store, job["id"], seconds=10)
    assert ended["state"] == bulk_jobs.DONE and ended["approved"] == ids and not ended["error"]
    assert sorted(_approvals(store)) == ids


SAM = {"name": "Sam", "login": "sam", "server": False, "as": {}}


def test_a_slow_learning_never_lets_another_runner_take_the_job(store, monkeypatch):
    """``renew_bulk_job`` was never called: the job's progress was noted only at each invoice's approval, so one whose
    learning (or check) took over 30 s (its pages read again, a lock waited for) let the background process's other
    runner take the job meanwhile, and three takeovers ended it "AP Coder stopped 3 times". Each invoice's start
    notes it too."""
    ids = _clean(store, 3)
    job, _ = store.start_bulk_job("", SAM, _asked(store, ids), bulk.JOB_FORMAT)
    real_learn, real_check, taken = bulk.learn_from_approval, bulk._still_clean, []

    def slow_learning(*args, **kwargs):
        learned = real_learn(*args, **kwargs)
        with sqlite3.connect(store.path) as conn:  # (it took a minute)
            conn.execute("UPDATE bulk_jobs SET renewed_at = ? WHERE id = ?", (time.time() - 2 * jobs.BULK_STALE, job))
        return learned

    def checked(*args, **kwargs):
        taken.append(store.claim_bulk_job("another runner", jobs.BULK_STALE, bulk.JOB_FORMAT))
        return real_check(*args, **kwargs)

    monkeypatch.setattr(bulk, "learn_from_approval", slow_learning)
    monkeypatch.setattr(bulk, "_still_clean", checked)
    jobs.run_bulk(store, web_settings(), store.claim_bulk_job("r", jobs.BULK_STALE, bulk.JOB_FORMAT), "r")
    ended = store.bulk_job(job)
    assert taken == [None] * len(ids) and ended["state"] == bulk_jobs.DONE and ended["approved"] == ids
    assert ended["tries"] == 1


def _ends_failing(monkeypatch, times: int) -> list[int]:
    """The first ``times`` ends of a bulk approval meet a database error (locked past the 30 s wait, a restore's
    moment without the table, a full disk): the jobs whose end was tried."""
    real, tried = Store.end_bulk_job, []

    def end(self, job_id, *args, **kwargs):
        tried.append(job_id)
        if len(tried) <= times:
            raise sqlite3.OperationalError("database is locked")
        return real(self, job_id, *args, **kwargs)

    monkeypatch.setattr(Store, "end_bulk_job", end)
    return tried


def _until(seen, seconds: float = 10) -> None:
    ends = time.monotonic() + seconds
    while not seen() and time.monotonic() < ends:
        time.sleep(0.02)
    assert seen()


def test_a_database_error_at_a_jobs_end_never_stops_the_laptops_later_bulk_approvals(store, monkeypatch):
    """The error went out of the laptop's thread, which died still marked as the one at the database's bulk
    approvals: every later one of the process waited for good, its page on "Approving…", a new one refused."""
    ids = _clean(store, 2)
    tried = _ends_failing(monkeypatch, 1)
    first, _ = store.start_bulk_job("ann", SAM, _asked(store, ids[:1]), bulk.JOB_FORMAT)
    jobs.approve_here(store, web_settings)
    _until(lambda: tried == [first] and str(store.path) not in jobs._HERE)
    second, _ = store.start_bulk_job("bo", SAM, _asked(store, ids[1:]), bulk.JOB_FORMAT)
    jobs.approve_here(store, web_settings)
    ended = _ended(store, second, seconds=10)
    assert ended["state"] == bulk_jobs.DONE and ended["approved"] == ids[1:]


def test_a_database_error_at_a_jobs_end_never_stops_the_background_processs_runners(store, monkeypatch):
    """Each of the background process's runners that met such an error died, the last one leaving none."""
    ids = _clean(store, jobs.BULK_RUNNERS + 1)
    tried = _ends_failing(monkeypatch, jobs.BULK_RUNNERS)
    for n, i in enumerate(ids[:-1]):
        store.start_bulk_job(f"p{n}", SAM, _asked(store, [i]), bulk.JOB_FORMAT)
    runner = jobs.BulkRunner(web_settings, lambda: store).start()
    try:
        _until(lambda: len(tried) == jobs.BULK_RUNNERS)
        last, _ = store.start_bulk_job("last", SAM, _asked(store, ids[-1:]), bulk.JOB_FORMAT)
        runner.wake()
        assert _ended(store, last, seconds=10)["state"] == bulk_jobs.DONE
    finally:
        runner.stop()
        runner.thread.join(10)


def test_no_bulk_approval_is_taken_while_a_backup_is_restored(store, monkeypatch):
    """The other background workers wait while a backup is restored (``Store.restoring``); the bulk approvals were
    taken from the database being replaced, by the laptop's thread and the background process alike."""
    ids = _clean(store, 2)
    restoring = {"now": True}
    monkeypatch.setattr(Store, "restoring", lambda self: restoring["now"])
    here, _ = store.start_bulk_job("ann", SAM, _asked(store, ids[:1]), bulk.JOB_FORMAT)
    jobs.approve_here(store, web_settings)
    _until(lambda: str(store.path) not in jobs._HERE)
    runner = jobs.BulkRunner(web_settings, lambda: store).start()
    try:
        runner.wake()
        time.sleep(3 * jobs.BULK_POLL)
        assert store.bulk_job(here)["state"] == bulk_jobs.WAITING and _approvals(store) == []
        restoring["now"] = False
        runner.wake()
        assert _ended(store, here, seconds=10)["state"] == bulk_jobs.DONE
    finally:
        runner.stop()
        runner.thread.join(10)


def _server(store: Store, processes: int = 1) -> TestClient:
    app = create_app(web_settings(), store, start_reader=False, server=True, processes=processes)
    return TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)


def _account(store: Store, username: str, role: str = "clerk") -> int:
    uid = store.add_user(username, GOOD, role=role, display_name=username.title())
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    enrol(store, uid)
    return uid


def test_on_the_server_the_job_goes_on_as_the_person_who_asked_after_their_session_ended(store, monkeypatch):
    """Each approval and event names the person who asked, their account as the approval's login, though their
    session ended while the job ran; their page asks them to log in again, and then shows the result."""
    monkeypatch.setattr(review, "BULK_WAIT", 0.1)
    ids = _clean(store, 4)
    clara = _account(store, "clara")
    client = _server(store)
    assert sign_in(client, "clara", GOOD).status_code == 200
    gate = Gate(monkeypatch, hold=1)
    answer = client.post("/review/bulk-approve", data={"ids": [str(i) for i in ids]}, headers=PAGE)
    assert answer.json()["redirect"] == "/review/bulk"
    gate.reached(2)
    store.end_sessions(clara)
    gate.go.set()
    job = _ended(store, store.latest_bulk_job(f'["", "user:{clara}"]')["id"])
    assert job["state"] == bulk_jobs.DONE and job["approved"] == ids
    assert {store.get_invoice(i)["reviewer"] for i in ids} == {"Clara"}
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute("SELECT actor, detail FROM events WHERE action = 'approved'").fetchall()
    assert len(rows) == 4 and all(a == "Clara" and f'"login": "user:{clara}"' in d for a, d in rows)
    assert client.get("/review/bulk").headers["location"] == "/login?next=%2Freview%2Fbulk"
    polled = client.get(f"/review/bulk/status?job={job['id']}")
    assert polled.status_code == 303  # (a poll: it doesn't keep a session going, D-109)
    assert sign_in(client, "clara", GOOD, next="/review/bulk").json()["redirect"] == "/review/bulk"
    assert "Done: 4 of 4 approved, 0 need a look." in client.get("/review/bulk").text


def test_on_the_server_a_bulk_approval_stops_once_the_account_may_no_longer_approve(store, monkeypatch):
    monkeypatch.setattr(review, "BULK_WAIT", 0.1)
    ids = _clean(store, 4)
    clara = _account(store, "clara")
    client = _server(store)
    sign_in(client, "clara", GOOD)
    gate = Gate(monkeypatch, hold=1)
    client.post("/review/bulk-approve", data={"ids": [str(i) for i in ids]}, headers=PAGE)
    gate.reached(2)
    store.set_role(clara, "readonly", actor="Boss")
    gate.go.set()
    job = _ended(store, store.latest_bulk_job(f'["", "user:{clara}"]')["id"])
    assert job["state"] == bulk_jobs.STOPPED and job["error"] == jobs.NO_LONGER
    assert job["approved"] == ids[:2] and [store.get_invoice(i)["status"] for i in ids[2:]] == [REVIEW, REVIEW]


def test_with_several_web_workers_the_background_process_approves_and_the_worker_only_waits(store, monkeypatch):
    """The company server with several web workers: none approves (its threads serve pages); the background process's
    runner does, and the worker asked waits for it holding no thread, then answers as before."""
    ids = _clean(store, 3)
    _account(store, "clara")
    client = _server(store, processes=2)
    sign_in(client, "clara", GOOD)
    asked: list[threading.Thread] = []
    monkeypatch.setattr(jobs, "approve_here", lambda *args: asked.append(args))
    runner = jobs.BulkRunner(web_settings, lambda: store).start()  # (the background process's)
    try:
        answer = client.post("/review/bulk-approve", data={"ids": [str(i) for i in ids]}, headers=PAGE).json()
    finally:
        runner.stop()
        runner.thread.join(10)
    assert asked == [] and answer["redirect"].startswith("/review?flash=")
    assert "Approved 3 invoices." in client.get(answer["redirect"]).text
    assert all(store.get_invoice(i)["status"] == APPROVED for i in ids)


def test_in_a_browser_the_bulk_approvals_page_says_how_far_it_got_then_what_was_done(chromium, store, monkeypatch):
    """The queue's Approve goes to the job's page once it takes longer than ``BULK_WAIT``: its line follows how far it
    got (app.js's data-job-status), and the page is drawn again with what was done once it is."""
    from tests.browser import browser_on

    monkeypatch.setattr(review, "BULK_WAIT", 0.2)
    _clean(store, 6)
    gate = Gate(monkeypatch, hold=2)
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/review")
        page.click("summary:has-text('Approve 6')")
        page.click("button:has-text('Approve them')")
        page.wait_for_url("**/review/bulk")
        assert page.inner_text("[data-job-status]") == "Approving: 2 of 6 approved, 0 need a look"
        gate.go.set()
        page.wait_for_selector("text=Done: 6 of 6 approved, 0 need a look.", timeout=15000)
        assert page.locator("[data-job-status]").count() == 0 and errors == []
