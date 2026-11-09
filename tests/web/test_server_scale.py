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
from tests.conftest import until


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


def _workers(store, n: int = 2):
    """``n`` web workers' apps on one database, as the server's processes are (one key, no background work)."""
    import secrets

    from tests.web.test_server_security import app_for

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
    assert until(lambda: second.get("process").running)
    other = second.get("process")
    assert other.running and second.known("process")
    assert not other.start(work)  # one import at a time, whichever worker is asked
    assert until(lambda: other.snapshot()["note"] == "Reading a.pdf", 5)  # (it says how far it got every second)
    go.set()
    first.get("process").wait(10)
    assert until(lambda: not other.running, 5)
    assert other.snapshot()["result"]["messages"][0]["text"] == "3 invoices read."
    assert not other.running and other.start(lambda job: {}) and other.wait(10)


def test_a_job_whose_worker_stopped_does_not_block_the_next(store, monkeypatch):
    from types import SimpleNamespace

    from ap_coder.core.store import ephemeral
    from ap_coder.web import jobs

    clock = [time.time()]  # the leases' clock, moved on by hand (a slow runner took over 0.2 s between two lines)
    monkeypatch.setattr(ephemeral, "time", SimpleNamespace(time=lambda: clock[0]))
    assert store.claim_job("process", "999999:gone", b'{"state": "running"}', 0.2)  # a worker that then stopped
    assert jobs.Jobs(store).get("process").running
    clock[0] += 0.3
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

    from tests.web.test_server_security import account, client_of, log_in
    from tests.webclient import PAGE

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
    from tests.webclient import add_clean

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
    root = Path(__file__).resolve().parents[2]
    # The server's window to a file, not a pipe: a worker left running (the server ended before it) holds a pipe
    # open, and reading it to its end waited for good (Ubuntu CI: the job ended at its 45 minutes, no failure shown).
    # Its own group of processes, so a failure ends the workers too.
    window = tmp_path / "server.log"
    out = window.open("w", encoding="utf-8")
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(command, cwd=root, env=env, stdout=out, stderr=subprocess.STDOUT, text=True, **group)
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
        # Each worker opens its access log once it serves, after building its memories (D-204): one may serve while
        # the other still builds, so both are waited for.
        logs = [data / "logs" / "access-1.log", data / "logs" / "access-2.log"]
        while not all(log.exists() for log in logs) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert all(log.exists() for log in logs)
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
        assert took < 30 and proc.returncode == 0, f"{took:.1f} s, exit {proc.returncode}: {window.read_text()}"
        assert not (data / ".ap_coder_server.json").exists()
        held = try_lock(data / ".ap_coder_background.lock")
        assert held is not None  # the background process ended with it
        held.close()
    except BaseException:
        print("--- the server's window ---", window.read_text(encoding="utf-8", errors="replace"), sep="\n")
        raise
    finally:
        if proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            else:
                import signal

                os.killpg(proc.pid, signal.SIGKILL)  # (the server and every process it started)
        proc.wait(30)
        out.close()


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


def test_workers_that_saw_the_stop_file_first_are_no_startup_failure(tmp_path, monkeypatch, caplog):
    """B-1493: the workers read the stop file /shutdown wrote and ended (on Windows with exit code 3, uvicorn's
    STARTUP_FAILURE) before the supervisor's own check of it, every half second, had run: its keep-alive took them
    for workers that failed to start, and the server ended with exit code 1 after a clean stop. The keep-alive reads
    the stop file first; a worker that fails at start with no stop asked for still stops the server as a failure."""
    import json
    import logging
    import threading
    from types import SimpleNamespace

    from uvicorn.config import STARTUP_FAILURE

    class Ended:
        pid, exitcode = 4608, STARTUP_FAILURE

        def is_alive(self, timeout=None):
            return False

        def kill(self):
            pass

        def join(self, timeout=None):
            pass

    def supervisor():
        made = server._supervisor_class().__new__(server._supervisor_class())
        made.should_exit, made.processes = threading.Event(), [Ended()]
        made.config = SimpleNamespace(timeout_worker_healthcheck=0.01)
        return made

    monkeypatch.setenv(server.WORKER_ENV, json.dumps({"data": str(tmp_path), "token": "secret"}))
    caplog.set_level(logging.INFO)
    server.stop_file(tmp_path).write_text("secret", encoding="utf-8")  # /shutdown, answered by a worker
    stopping = supervisor()
    stopping.keep_subprocess_alive()
    assert stopping.should_exit.is_set() and stopping.asked_to_stop
    assert "failed to start" not in caplog.text
    server.stop_file(tmp_path).unlink()  # no stop asked for: a worker that ends at once did fail to start
    failing = supervisor()
    failing.keep_subprocess_alive()
    assert failing.should_exit.is_set() and not failing.asked_to_stop
    assert "failed to start" in caplog.text


def test_a_worker_stuck_waiting_for_a_connection_is_woken_to_stop(monkeypatch):
    """B-1441: on Windows a web worker's loop could sit in a blocking ``accept()`` on the socket the workers share
    (both woke for one connection, the other took it): it never saw the stop, and was ended at the deadline, 30 s on
    (CI's faulthandler dump). While a worker is still running after the stop, the server connects to its own port every
    half second: the worker waiting takes the connection and its loop sees the stop."""
    import socket
    import threading
    from types import SimpleNamespace

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(True)  # as the shared socket can be in a worker on Windows
    accepted = []
    waiting = threading.Thread(target=lambda: accepted.append(listener.accept()), daemon=True)
    waiting.start()
    server._wake_waiting_workers(SimpleNamespace(host="0.0.0.0", port=listener.getsockname()[1]))
    waiting.join(5)
    listener.close()
    assert accepted and not waiting.is_alive()

    class Stopping:  # a worker that ends on the third look
        def __init__(self):
            self.looks, self.exitcode = 0, 0
            self.process = self

        def join(self, _timeout=None):
            self.looks += 1

        def is_alive(self):
            return self.looks < 3

        def kill(self):
            raise AssertionError("ended gracefully, never killed")

    woken = []
    monkeypatch.setattr(server, "_wake_waiting_workers", lambda config: woken.append(config))
    supervisor = server._supervisor_class().__new__(server._supervisor_class())
    supervisor.config, supervisor.asked_to_stop = SimpleNamespace(host="127.0.0.1", port=1), True
    supervisor.processes = [Stopping()]
    supervisor.join_all()
    assert len(woken) == 2  # while it ran, and no more once it ended


def test_the_service_starts_workers_for_the_cores_and_memory_unless_server_json_says(tmp_path, monkeypatch):
    from ap_coder.ops import service

    for var in ("AP_SERVER_WORKERS", "AP_LLM_PARALLEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(server.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(server, "memory_total_mb", lambda: 64_000.0)  # the stage 2 server (D-153: no cap of 8)
    cmd = service.serve_command(service.settings(tmp_path), "python")
    assert cmd[cmd.index("--workers") + 1] == "28"
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
    lock, marks, now, most, began = threading.Lock(), set(), [0], [0], [0]

    def read_one(settings, store, **kw):
        mark = kw.get("reader") or page_worker.READER_ID
        with page_worker.reading_as(mark):
            assert page_worker.reader_alive(mark)  # another thread putting reads back in line leaves it alone
            with lock:
                marks.add(mark)
                began[0] += 1
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
    assert until(lambda: began[0] >= 6)  # two rounds of three
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
    from tests.ai.test_uniform_reading import PDF

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
    assert verifier.load_settings(store).scope == "all" and verifier.prepare(store, {}).queue  # the laptop
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    assert verifier.load_settings(store).scope == "touchless"
    assert not verifier.prepare(store, {"auto": False, "reason": "supplier is learning"}).queue
    plan = verifier.prepare(store, {"auto": True})
    assert plan.queue and plan.hold
    assert verifier.hold({"auto": True}, plan)["verifier_hold"]  # still held until it agrees
    assert verifier.prepare(store, {"auto": False, "azure_hold": True}).queue
    verifier.save_settings(store, on=True, rounds=3, minutes=5, scope="all")  # an Admin's choice
    assert verifier.prepare(store, {}).queue


def test_an_admin_chooses_what_the_ai_reviewer_reviews_on_settings_reading(store, monkeypatch):
    from ap_coder.ai import verifier
    from tests.webclient import client_for, post

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
    from tests.webclient import enrol

    uid = store.add_user("ann", "a long enough password 1", users.CLERK)
    enrol(store, uid)  # (D-146)
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


def test_the_background_process_writes_what_it_does_to_its_own_log_beside_the_database(tmp_path):
    """The page reader, the AI reviewer and JD Edwards log at INFO, and the background process had no handler, so
    nothing they did reached any file: a page reader that never ran (Windows CI) left no trace, for IT either."""
    import logging

    from ap_coder.web import server

    handler = server.background_log(tmp_path / "logs")
    try:
        logging.getLogger("ap_coder.ai.page_worker").info("page reader: invoice 7 read (2 page(s), 9 s)")
        logging.getLogger("other.library").info("not ours")
        handler.flush()
        text = (tmp_path / "logs" / server.BACKGROUND_LOG).read_text(encoding="utf-8")
        assert "page reader: invoice 7 read" in text and "INFO ap_coder.ai.page_worker" in text
        assert "not ours" not in text
    finally:
        logging.getLogger("ap_coder").removeHandler(handler)
        handler.close()


def test_a_background_process_that_seems_stuck_can_be_asked_to_write_every_thread_s_stack(tmp_path, monkeypatch):
    """Windows CI: the page reader's self-test waited 11 minutes on something no log line names, and no signal can
    ask a Windows process where it is. With AP_STACK_DUMP_SECONDS set (a bench, or IT on a developer's word), the
    background process writes every thread's stack to logs/background-stacks.log that often; unset, never. The
    dumps run in a process of their own here, as in the background process: Python's faulthandler reads the other
    threads' frames without stopping them, and in pytest's process (threads of other tests ending meanwhile) a dump
    ended the run with an access violation on Windows CI."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    from ap_coder.web import server

    monkeypatch.delenv(server.STACK_DUMP_ENV, raising=False)
    assert server.stack_dumps(tmp_path / "logs") is None and not (tmp_path / "logs").exists()
    script = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from ap_coder.web import server\n"
        "folder = Path(sys.argv[1])\n"
        "def waiting_for_its_own_stack():\n"
        "    handle = server.stack_dumps(folder)\n"
        "    path = folder / server.STACK_DUMP_LOG\n"
        "    deadline = time.monotonic() + 10\n"
        "    while ('waiting_for_its_own_stack' not in path.read_text(encoding='utf-8')\n"
        "           and time.monotonic() < deadline):\n"
        "        time.sleep(0.1)\n"
        "    import faulthandler\n"
        "    faulthandler.cancel_dump_traceback_later()\n"
        "    handle.close()\n"
        "waiting_for_its_own_stack()\n"
    )
    env = {**os.environ, server.STACK_DUMP_ENV: "0.2"}
    done = subprocess.run([sys.executable, "-c", script, str(tmp_path / "logs")],
                          cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True,
                          timeout=60)  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert "waiting_for_its_own_stack" in (tmp_path / "logs" / server.STACK_DUMP_LOG).read_text(encoding="utf-8")


def test_the_background_process_loads_a_library_while_it_waits_for_its_input_to_close(tmp_path):
    """Windows CI: the page reader's self-test sat 11 minutes in ``import numpy`` and went on 4 s after the server
    stopped. The thread waiting for its input to close (how the server stops it) kept a read pending on the pipe,
    and Windows blocks ``GetFileType`` on a pipe with a read pending, which every DLL that starts its own C runtime
    calls as it loads: on the company server, the self-test, each scan and OCR would wait until the server stopped.
    The library loads at once, and the process still stops when its input closes (on every system)."""
    import subprocess
    import sys
    import time

    script = (
        "import threading, time, sys\n"
        "from ap_coder.web import server\n"
        "closed = threading.Event()\n"
        "threading.Thread(target=server.until_input_closes, args=(closed.set,), daemon=True).start()\n"
        "time.sleep(1.0)\n"  # the wait for the input is under way before the library loads
        "assert 'numpy' not in sys.modules\n"
        "import numpy\n"
        "print('loaded', flush=True)\n"
        "assert closed.wait(30), 'the input closed and nobody noticed'\n"
        "print('stopped', flush=True)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)  # fmt: skip
    try:
        started = time.monotonic()
        assert proc.stdout.readline().strip() == "loaded"
        assert time.monotonic() - started < 30  # not waiting for the input to close
        proc.stdin.write("anything the server sends is read and dropped\n")
        proc.stdin.flush()
        time.sleep(0.5)
        assert proc.poll() is None  # input, but not closed: still running
        proc.stdin.close()
        assert proc.stdout.readline().strip() == "stopped"
        assert proc.wait(15) == 0
    finally:
        proc.kill()


# --- B-1660: a long import holds up no other request of its web worker -------------------------------------------


def test_a_long_import_does_not_hold_up_the_other_requests_of_its_web_worker(store, monkeypatch):
    """A web worker answers every ``async`` handler on its one event loop: the ERP register's import (600,000 rows at
    a large company) made its store calls there, so every other request to that worker (pages, polls, logins) waited
    until it was done. Its work now runs on a threadpool thread, as the review screen's Check does: a page asked for
    while the import is in its store call is answered before that call ends."""
    import httpx

    from ap_coder.core.store import Store
    from ap_coder.web.app import create_app
    from tests.webclient import PAGE, web_settings

    inside, ended = threading.Event(), threading.Event()
    real = Store.import_erp_register

    def import_slowly(self, *args, **kwargs):
        inside.set()
        time.sleep(1.5)  # the store call of a long import
        ended.set()
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Store, "import_erp_register", import_slowly)
    app = create_app(web_settings(), store, start_reader=False, background=False)
    sheet = b"Supplier,Doc No,Amount\nAcme Ltd,A-1,100.00\n"
    columns = {"col_vendor_name": "Supplier", "col_invoice_number": "Doc No", "col_total": "Amount"}

    async def both() -> tuple[bool, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            upload = await client.post("/exports/register/upload", files={"file": ("erp.csv", sheet, "text/csv")},
                                       headers=PAGE)  # fmt: skip
            token = httpx.URL(upload.json()["redirect"]).params["register"]
            importing = asyncio.create_task(
                client.post("/exports/register/import", data={"file": token, **columns}, headers=PAGE)
            )
            for _ in range(500):  # until the import is in its store call
                if inside.is_set():
                    break
                await asyncio.sleep(0.01)
            page = await client.get("/healthz")
            answered_meanwhile = not ended.is_set()
            return answered_meanwhile, page, await importing

    answered_meanwhile, page, imported = asyncio.run(both())
    assert page.status_code == 200 and imported.status_code == 200, imported.text
    assert store.erp_register_count() == 1
    assert answered_meanwhile, "the page waited for the import's store call to end"


def test_the_servers_socket_listens_before_any_worker_starts():
    """B-1711: each web worker listened on its own copy of the server's socket, the first one changing it from bound
    to listening while the others did the same; on Windows a worker's start once failed there with WinError 10022
    (an invalid argument), and the stop then waited for the worker started in its place. The server's socket now
    listens before any worker starts (a connection waits in its queue for the first one): a worker's listen changes
    nothing."""
    config = server.server_config("ap_coder.web.server:worker_app", "127.0.0.1", 0, None, None, None, factory=True,
                                  workers=2)  # fmt: skip
    sock = server.listening_socket(config)
    try:
        socket.create_connection(sock.getsockname()[:2], timeout=2).close()  # a socket only bound refuses it
    finally:
        sock.close()
