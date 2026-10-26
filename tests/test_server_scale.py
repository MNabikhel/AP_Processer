"""The company server under load (D-111): several web worker processes, the background work in one process of its
own, the state they share kept in the database, LLM calls in parallel, and the GPU time cut."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest

from ap_coder.ops import offline
from ap_coder.web import server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- B-1271: a port nothing listens on is free, whatever answers a connection to it --------------------------------


def test_a_free_port_is_not_taken_for_one_in_use_when_something_answers_every_connection(monkeypatch):
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    # An office proxy or an endpoint agent that intercepts connections answers on any port, so asking "does something
    # answer there?" says yes for a port nothing on this computer listens on.
    monkeypatch.setattr(offline, "listening", lambda port, host="127.0.0.1": True)
    port = _free_port()
    assert server.port_refusal("127.0.0.1", port) == ""
    assert server.port_refusal("0.0.0.0", port) == ""
    with socket.socket() as taken:  # a program listening there is still found
        taken.bind(("0.0.0.0", 0))
        taken.listen()
        assert "in use" in server.port_refusal("0.0.0.0", taken.getsockname()[1])


def test_the_port_check_does_not_fail_on_a_computer_without_ipv6(monkeypatch):
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    real = socket.socket

    def no_ipv6(family=socket.AF_INET, *args, **kwargs):
        if family == socket.AF_INET6:
            raise OSError(97, "Address family not supported by protocol")
        return real(family, *args, **kwargs)

    monkeypatch.setattr(socket, "socket", no_ipv6)
    assert server.port_refusal("::", _free_port()) == ""  # (uvicorn then says what is wrong itself)


# --- B-1272: a stop ends the server within 30 seconds, however long its requests take ------------------------------


def test_a_stop_ends_the_server_within_its_grace_even_with_a_long_request_under_way(monkeypatch):
    import httpx
    import uvicorn

    monkeypatch.setattr(server, "GRACEFUL_SECONDS", 1)
    assert server.server_config(object(), "127.0.0.1", 1, None, None, None).timeout_graceful_shutdown == 1
    started = threading.Event()

    async def slow_report(scope, receive, send):  # a report that takes minutes
        if scope["type"] != "http":
            return
        started.set()
        await asyncio.sleep(120)

    port = _free_port()
    config = server.server_config(slow_report, "127.0.0.1", port, None, None, None)
    config.lifespan = "off"
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    while not running.started:
        time.sleep(0.05)
    asking = threading.Thread(target=lambda: _ask(httpx, port), daemon=True)
    asking.start()
    assert started.wait(10)
    t0 = time.monotonic()
    running.should_exit = True  # what SIGTERM, Ctrl+C and POST /shutdown do
    thread.join(20)
    assert not thread.is_alive() and time.monotonic() - t0 < 10


def _ask(httpx, port: int) -> None:
    try:
        httpx.get(f"http://127.0.0.1:{port}/", timeout=60, trust_env=False)
    except httpx.HTTPError:
        pass


def test_the_server_grace_is_at_most_30_seconds():
    config = server.server_config(object(), "127.0.0.1", 8501, None, None, None)
    assert config.timeout_graceful_shutdown is not None and config.timeout_graceful_shutdown <= 30
    assert config.access_log is False and config.server_header is False


# --- Several web workers share what they know through the database ----------------------------------------------


@pytest.fixture
def store(tmp_path):
    from ap_coder.core.store import Store

    return Store(tmp_path / "private" / "ap_coder.db")


def _workers(store, n: int = 2):
    """``n`` web workers' apps on one database, as the server's processes are (one key, no background work)."""
    import secrets

    from .test_server_security import app_for

    key = secrets.token_bytes(32)
    return [app_for(store, background=False, processes=n, secret=key) for _ in range(n)]


def test_a_job_one_worker_runs_is_seen_and_not_started_again_by_another(store):
    from ap_coder.web.jobs import Jobs

    first, second = Jobs(store), Jobs(store)
    go = threading.Event()

    def work(job):
        job.step(1, 3, "Reading a.pdf")
        go.wait(10)
        return {"messages": [{"text": "3 invoices read.", "tone": "ok"}]}

    assert first.get("process").start(work, total=3)
    time.sleep(0.2)
    other = second.get("process")
    assert other.running and second.known("process")
    assert not other.start(work)  # one import at a time, whichever worker is asked
    deadline = time.monotonic() + 5  # (the worker running it says how far it got every second)
    while other.snapshot()["note"] != "Reading a.pdf" and time.monotonic() < deadline:
        time.sleep(0.1)
    assert other.snapshot()["note"] == "Reading a.pdf"
    go.set()
    first.get("process").wait(10)
    deadline = time.monotonic() + 5
    while other.running and time.monotonic() < deadline:
        time.sleep(0.05)
    assert other.snapshot()["result"]["messages"][0]["text"] == "3 invoices read."
    assert not other.running and other.start(lambda job: {}) and other.wait(10)


def test_a_job_whose_worker_stopped_does_not_block_the_next(store, monkeypatch):
    from ap_coder.web import jobs

    assert store.claim_job("process", "999999:gone", b'{"state": "running"}', 0.2)  # a worker that then stopped
    assert jobs.Jobs(store).get("process").running
    time.sleep(0.3)
    assert not jobs.Jobs(store).get("process").running
    assert jobs.Jobs(store).get("process").start(lambda job: {})


def test_the_hourly_upload_allowance_is_counted_once_for_every_worker(store, monkeypatch):
    from ap_coder.web.pages import process

    monkeypatch.setattr(process, "MAX_BYTES_PER_WINDOW", 1000)
    assert process.upload_refusal(7, 1, 600, store=store) == ("", 0)
    monkeypatch.setattr(process, "_uploads", __import__("collections").defaultdict(__import__("collections").deque))
    text, status = process.upload_refusal(7, 1, 600, store=store)  # another worker, its memory empty
    assert status == 429 and "last hour" in text
    assert process.upload_refusal(8, 1, 600, store=store) == ("", 0)


def test_a_temporary_password_made_by_one_worker_is_shown_once_by_another(store):
    import re

    from .test_server_security import account, client_of, log_in
    from .webclient import PAGE

    account(store, "boss", "admin")
    one, two = (client_of(app) for app in _workers(store))
    log_in(one, "boss")
    two.cookies.update(one.cookies)
    answer = one.post("/admin/users/add", data={"username": "ann", "name": "Ann", "role": "clerk"}, headers=PAGE)
    from ap_coder.web.pages import users

    users._waiting.clear()  # (the other worker is another process: nothing of this one's memory)
    shown = two.get(answer.json()["redirect"]).text
    password = re.search(r'<code class="big">([^<]+)</code>', shown).group(1)
    assert password not in one.get(answer.json()["redirect"]).text  # once, whichever worker shows it
    with store._conn() as conn:
        kept = b"".join(bytes(r[0]) for r in conn.execute("SELECT value FROM ephemeral"))
    assert password.encode() not in kept  # (nothing readable in the database meanwhile)


def test_a_draft_one_worker_kept_is_seen_and_dropped_by_another(store):
    import types

    from ap_coder.web.pages import invoice

    from .webclient import add_clean

    iid = add_clean(store)
    apps = _workers(store)

    def asking(app):  # a request to that worker
        return types.SimpleNamespace(app=app, state=types.SimpleNamespace())

    assert invoice.unsaved_edits(asking(apps[0])) == set()
    store.save_invoice_draft(iid, '["user", "7"]', {"header": {}, "lines": [], "taxes": []})  # kept by worker two
    assert invoice.unsaved_edits(asking(apps[0])) == {iid}
    invoice.forget_everyones_draft(asking(apps[1]), iid)  # approved on worker two
    assert invoice.unsaved_edits(asking(apps[0])) == set()


def test_a_restore_in_one_process_pauses_the_background_work_of_another(store):
    import os

    marker = store.path.with_name(store.path.name + ".restoring")
    assert not store.restoring()
    marker.write_text(str(os.getppid()), encoding="utf-8")  # another process, running, is restoring
    assert store.restoring()
    marker.write_text("999999", encoding="utf-8")  # one that is gone left it behind
    assert not store.restoring()


def test_the_server_runs_several_workers_and_one_background_process_and_stops_them_all(tmp_path):
    import json
    import os
    import subprocess
    import sys
    import urllib.request
    from pathlib import Path

    from ap_coder.core.paths import try_lock

    data, port = tmp_path / "data", _free_port()
    data.mkdir()
    (tmp_path / "empty.env").write_text("", encoding="utf-8")
    env = {**os.environ, "AP_PRIVATE_DIR": str(data), "BROWSER": "none", "AP_ALLOWED_HOSTS": "127.0.0.1"}
    command = [sys.executable, "-m", "ap_coder", "--env-file", str(tmp_path / "empty.env"), "--db",
               str(data / "ap_coder.db"), "serve", "--server", "--insecure-http", "--host", "127.0.0.1", "--port",
               str(port), "--workers", "2"]  # fmt: skip
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 90
        while True:
            try:
                with direct.open(f"http://127.0.0.1:{port}/healthz", timeout=2) as answer:
                    worker = json.loads(answer.read())["pid"]
                    assert worker != proc.pid  # a worker answers, not the server itself
                    break
            except OSError:
                assert time.monotonic() < deadline and proc.poll() is None
                time.sleep(0.3)
        while not (data / "logs" / "access-2.log").exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert (data / "logs" / "access-1.log").exists() and (data / "logs" / "access-2.log").exists()
        while try_lock(data / ".ap_coder_background.lock") is not None:  # the background process holds it
            assert time.monotonic() < deadline
            time.sleep(0.3)
        record = json.loads((data / ".ap_coder_server.json").read_text(encoding="utf-8"))
        # The server's own process: Popen's, or (a venv's python.exe on Windows is a launcher that starts the real
        # interpreter as its child) that child; never a worker.
        assert record["pid"] != worker and (record["pid"] == proc.pid or os.name == "nt")
        stop = urllib.request.Request(f"http://127.0.0.1:{port}/shutdown", data=b"", method="POST",
                                      headers={"X-AP-Coder-Token": record["token"]})  # fmt: skip
        with direct.open(stop, timeout=10) as answer:
            assert json.loads(answer.read())["stopping"]
        t0 = time.monotonic()
        proc.wait(40)
        took = time.monotonic() - t0
        assert took < 30 and proc.returncode == 0, f"{took:.1f} s, exit {proc.returncode}: {proc.stdout.read()}"
        assert not (data / ".ap_coder_server.json").exists()
        held = try_lock(data / ".ap_coder_background.lock")
        assert held is not None  # the background process ended with it
        held.close()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate()


def test_a_worker_asked_to_stop_is_no_startup_failure_even_with_windows_exit_code(monkeypatch):
    """B-1292: on Windows a worker stopped through the stop file re-raises SIGTERM after its graceful stop (uvicorn),
    which ends it with exit code 3, uvicorn's STARTUP_FAILURE: the server then said it failed (exit code 1). A
    worker that ends once the server was asked to stop is no startup failure; one that fails at start still is."""
    from uvicorn.config import STARTUP_FAILURE

    class Done:
        def __init__(self, code):
            self.exitcode = code
            self.process = self

        def join(self, _timeout=None):
            pass

        def is_alive(self):
            return False

    supervisor = server._supervisor_class().__new__(server._supervisor_class())
    supervisor.processes = [Done(STARTUP_FAILURE), Done(0)]
    supervisor.asked_to_stop = True
    supervisor.join_all()
    assert not supervisor.failed
    supervisor.asked_to_stop = False
    supervisor.join_all()
    assert supervisor.failed


def test_the_service_starts_one_worker_per_core_at_most_8_unless_server_json_says(tmp_path, monkeypatch):
    from ap_coder.ops import service

    for var in ("AP_SERVER_WORKERS", "AP_LLM_PARALLEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(server.os, "cpu_count", lambda: 32)
    cmd = service.serve_command(service.settings(tmp_path), "python")
    assert cmd[cmd.index("--workers") + 1] == "8"
    assert service.serve_env(service.settings(tmp_path), tmp_path)["AP_LLM_PARALLEL"] == "1"
    service.write_config(tmp_path, workers=3, llm_parallel=4)
    s = service.settings(tmp_path)
    assert service.serve_command(s, "python")[-1] == "3" and service.serve_env(s, tmp_path)["AP_LLM_PARALLEL"] == "4"
    monkeypatch.setenv("AP_SERVER_WORKERS", "1")  # everything in one process, as before
    assert service.serve_command(service.settings(tmp_path), "python")[-1] == "1"
    with pytest.raises(service.SettingsError):
        service.settings(tmp_path, workers="many")


# --- llm_parallel: requests to the model server, N at once -------------------------------------------------------


def _most_at_once(slots_list, limit: int, threads: int = 4) -> int:
    lock, now, most = threading.Lock(), [0], [0]

    def ask(slots):
        with slots.held(limit):
            with lock:
                now[0] += 1
                most[0] = max(most[0], now[0])
            time.sleep(0.15)
            with lock:
                now[0] -= 1

    running = [threading.Thread(target=ask, args=(slots_list[n % len(slots_list)],)) for n in range(threads)]
    for t in running:
        t.start()
    for t in running:
        t.join(10)
    return most[0]


def test_requests_to_the_model_server_go_one_at_a_time_unless_llm_parallel_says_more(monkeypatch):
    from ap_coder.ai import local_llm

    monkeypatch.delenv(local_llm.SLOTS_ENV, raising=False)
    assert _most_at_once([local_llm.Slots("t")], 1) == 1
    assert _most_at_once([local_llm.Slots("t")], 2) == 2


def test_the_limit_holds_across_the_servers_processes(tmp_path, monkeypatch):
    from ap_coder.ai import local_llm

    monkeypatch.setenv(local_llm.SLOTS_ENV, str(tmp_path / "llm"))
    two_processes = [local_llm.Slots("chat"), local_llm.Slots("chat")]  # each its own, as in two processes
    assert _most_at_once(two_processes, 1) == 1
    assert _most_at_once(two_processes, 2) == 2


def test_the_setting_is_read_from_the_environment(monkeypatch):
    from ap_coder.core.config import Settings

    monkeypatch.delenv("AP_LLM_PARALLEL", raising=False)
    assert Settings.from_env("/nonexistent.env").llm.parallel == 1
    monkeypatch.setenv("AP_LLM_PARALLEL", "4")
    assert Settings.from_env("/nonexistent.env").llm.parallel == 4
    monkeypatch.setenv("AP_LLM_PARALLEL", "lots")
    assert Settings.from_env("/nonexistent.env").llm.parallel == 1


def test_the_background_reader_reads_llm_parallel_invoices_at_once(store, monkeypatch):
    import dataclasses

    from ap_coder.ai import page_worker
    from ap_coder.core.config import Settings

    settings = Settings()
    settings = dataclasses.replace(settings, llm=dataclasses.replace(settings.llm, parallel=3))
    lock, marks, now, most = threading.Lock(), set(), [0], [0]

    def read_one(settings, store, **kw):
        mark = kw.get("reader") or page_worker.READER_ID
        with page_worker.reading_as(mark):
            assert page_worker.reader_alive(mark)  # another thread putting reads back in line leaves it alone
            with lock:
                marks.add(mark)
                now[0] += 1
                most[0] = max(most[0], now[0])
            time.sleep(0.3)
            with lock:
                now[0] -= 1
        return page_worker.ReadOutcome(1, "done")

    monkeypatch.setattr(page_worker, "read_one", read_one)
    monkeypatch.setattr(page_worker, "release_interrupted", lambda store: 0)
    monkeypatch.setattr(page_worker, "test_under_way", lambda store: None)
    monkeypatch.setattr(page_worker, "self_test_if_due", lambda settings, store: None)
    reader = page_worker.BackgroundReader(lambda: settings, lambda: store)
    reader.start()
    time.sleep(1.0)
    reader.stop()
    reader.join(5)
    assert most[0] == 3 and len(marks) == 3 and page_worker.READER_ID in marks
    assert not page_worker.reader_alive(page_worker.reader_mark(1))  # a mark of this process left over: back in line


# --- Less GPU time: clean digital PDFs, and the AI reviewer for touchless candidates only ----------------------------


def _clean_capture(**changes):
    from ap_coder.capture.types import CaptureResult, FieldResult

    fields = {name: FieldResult(name, "x", 0.99, "verified") for name in
              ("vendor_name", "invoice_number", "invoice_date", "grand_total")}  # fmt: skip
    capture = CaptureResult(fields=fields, checks=[{"code": "TOTALS_ADD_UP", "ok": True}], layout_source="text",
                            page_count=2)  # fmt: skip
    for name, value in changes.items():
        setattr(capture, name, value)
    return capture


def _skipping(on: bool = True, sample: float = 0.0):
    import dataclasses

    from ap_coder.core.config import Settings

    s = Settings()
    return dataclasses.replace(s, page_reader=dataclasses.replace(s.page_reader, skip_clean=on, skip_sample=sample))


def test_a_clean_digital_pdf_that_goes_to_a_person_anyway_is_not_read_by_the_page_reader(tmp_path):
    from ap_coder.ai import page_worker
    from ap_coder.capture.types import FieldResult

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 clean")
    assert page_worker.skip_reason(_skipping(), _clean_capture(), {"state": "supervised"}, pdf)
    assert not page_worker.skip_reason(_skipping(on=False), _clean_capture(), {}, pdf)  # (the laptop's default)
    assert not page_worker.skip_reason(_skipping(sample=1.0), _clean_capture(), {}, pdf)  # the sample is read
    assert not page_worker.skip_reason(_skipping(), _clean_capture(), {"state": "autonomous"}, pdf)  # touchless
    assert not page_worker.skip_reason(_skipping(), _clean_capture(layout_source="mixed"), {}, pdf)  # a scan in it
    assert not page_worker.skip_reason(_skipping(), _clean_capture(pages_left_out=1), {}, pdf)
    assert not page_worker.skip_reason(_skipping(), _clean_capture(checks=[{"code": "TAX_RATE", "ok": True}]), {}, pdf)
    unsure = _clean_capture()
    unsure.fields["invoice_number"] = FieldResult("invoice_number", "x", 0.7, "likely")
    assert not page_worker.skip_reason(_skipping(), unsure, {}, pdf)


def test_a_skipped_invoice_says_why_and_is_counted(monkeypatch, tmp_path):
    import dataclasses

    from ap_coder.ai import page_worker
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.core.store import Store, load_sample_setup

    from .test_uniform_reading import PDF

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    settings = _skipping()
    settings = dataclasses.replace(settings, llm=dataclasses.replace(settings.llm, provider="off"))
    monkeypatch.setattr(page_worker, "skip_reason", lambda *a: page_worker.SKIP_TEXT)  # (the sample, made clean)
    result = InvoicePipeline(settings, store.reference_data(), store=store).process(PDF)
    read = store.page_read(result.invoice_id)
    assert read["status"] == "skipped" and "rule reader read in full" in read["error"]
    assert page_worker.skipped_totals(store) == {"invoices": 1, "pages": result.capture.page_count}
    assert store.page_reads_waiting() == 0
    assert store.queue_page_read(result.invoice_id, "asked", requested_by="Ann")  # a person can still ask for it


def test_the_page_reader_skip_is_on_by_default_on_the_server_only(monkeypatch):
    from ap_coder.core.config import Settings

    monkeypatch.delenv("AP_PAGE_READER_SKIP_CLEAN", raising=False)
    monkeypatch.setenv(offline.SERVER_ENV, "0")
    assert not Settings.from_env("/nonexistent.env").page_reader.skip_clean
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    assert Settings.from_env("/nonexistent.env").page_reader.skip_clean
    monkeypatch.setenv("AP_PAGE_READER_SKIP_CLEAN", "0")
    assert not Settings.from_env("/nonexistent.env").page_reader.skip_clean


def test_on_the_server_the_ai_reviewer_reviews_only_what_could_be_approved_without_a_person(store, monkeypatch):
    from ap_coder.ai import verifier

    monkeypatch.setenv(offline.SERVER_ENV, "0")
    assert verifier.load_settings(store).scope == "all" and verifier.prepare(store, 1, {}).queue  # the laptop
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    assert verifier.load_settings(store).scope == "touchless"
    assert not verifier.prepare(store, 1, {"auto": False, "reason": "supplier is learning"}).queue
    plan = verifier.prepare(store, 1, {"auto": True})
    assert plan.queue and plan.hold
    assert verifier.hold({"auto": True}, plan)["verifier_hold"]  # still held until it agrees
    assert verifier.prepare(store, 1, {"auto": False, "azure_hold": True}).queue
    verifier.save_settings(store, on=True, rounds=3, minutes=5, scope="all")  # an Admin's choice
    assert verifier.prepare(store, 1, {}).queue


def test_an_admin_chooses_what_the_ai_reviewer_reviews_on_settings_reading(store, monkeypatch):
    from ap_coder.ai import verifier

    from .webclient import client_for, post

    monkeypatch.setenv(offline.SERVER_ENV, "0")
    client = client_for(store)
    assert 'name="scope"' in client.get("/settings/reading").text
    post(client, "/settings/reading/reviewer", {"on": "1", "rounds": "3", "minutes": "5", "scope": "touchless"})
    assert verifier.load_settings(store).scope == "touchless"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])


def test_a_busy_database_does_not_fail_a_request_over_the_sessions_last_use(store, monkeypatch):
    """B-1293: under load a write lock held past the busy timeout made the session's last-use update raise "database
    is locked", and the person got a 500 for any page; the last use is best effort (a minute late at most)."""
    import sqlite3

    from ap_coder.core.store import users

    uid = store.add_user("ann", "a long enough password 1", users.CLERK)
    token = store.start_session(uid)
    later = users._clock() + users.SEEN_EVERY + 1
    monkeypatch.setattr(users, "_clock", lambda: later)
    real_open = store._open

    class Locked:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args):
            if sql.startswith("UPDATE sessions SET last_seen"):
                raise sqlite3.OperationalError("database is locked")
            return self.conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.conn, name)

    monkeypatch.setattr(store, "_open", lambda: Locked(real_open()))
    assert store.session_user(token)["username"] == "ann"


def test_an_approval_learns_the_suppliers_template_from_the_ocr_kept_when_it_was_read(store, monkeypatch, tmp_path):
    """B-1294: approving a scanned invoice OCR'd its pages again inside the web request (learn_from_approval's
    build_layout, without the data folder's OCR cache): 4-5 s each, 50-150 s under load. It reads them from the cache
    the pipeline filled when the invoice was read."""
    from ap_coder.capture import layout, workflow

    seen = []

    def fake_build_layout(path, **_kw):
        seen.append(layout._ocr_cache_folder.get())
        raise layout.CannotRead("stop here")

    monkeypatch.setattr(workflow, "build_layout", fake_build_layout)
    scan = tmp_path / "scan.png"
    scan.write_bytes(b"not really a picture")
    invoice = {"id": 1, "source_path": str(scan), "ai_output": {}, "meta": {}, "file_sha256": "x", "status": "approved"}
    monkeypatch.setattr(store, "get_invoice", lambda _id: invoice)
    workflow.learn_from_approval(store, 1, {"vendor_name": "Acme"}, actor="ann")
    assert seen and seen[0] is not None and seen[0].name == "ocr"


def test_a_connection_is_kept_open_longer_than_a_browsers_or_a_proxys_idle_time():
    """B-1295: uvicorn closed an idle connection after 5 s, the same as many clients, so under load a request sent
    on it as it closed was dropped (ReadError; a 502 behind IIS): kept 75 s."""
    config = server.server_config(object(), "127.0.0.1", 8501, None, None, None)
    assert config.timeout_keep_alive >= 60


def test_the_background_work_stops_while_the_workers_do_not_after_them():
    """B-1296: a stop ended the web workers first (up to 30 s with requests under way) and only then asked the
    background process to stop (up to 15 s more), so a stop could take 45 s: over the 30 s a stop is given on a slow
    Windows runner. The background process is asked as soon as the server is asked to stop."""
    stopped = threading.Event()

    class Background:
        def stop(self):
            stopped.set()

    asked = threading.Event()
    threading.Thread(target=server._stop_background_when_asked, args=(asked, Background()), daemon=True).start()
    assert not stopped.wait(0.2)
    asked.set()
    assert stopped.wait(2)
