"""What one web worker of the company server runs at once (D-153): its thread pool capped, the long routes in a lane
of their own, a worker started afresh past its memory or requests, how many workers the server runs, and what the
server and each worker do before they answer (D-204)."""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import anyio
import anyio.to_thread
import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from ap_coder.core.store import Store, load_sample_setup
from ap_coder.web import app as web_app
from ap_coder.web import core, roles, security, server
from ap_coder.web.app import create_app
from ap_coder.web.jobs import Jobs
from tests.webclient import PAGE, enrol, sign_in, web_settings

GOOD = "correct horse battery"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    uid = store.add_user("boss", GOOD, role="admin", display_name="Boss")
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    enrol(store, uid)
    return store


def _tokens(client: TestClient) -> float:
    return client.portal.call(lambda: anyio.to_thread.current_default_thread_limiter().total_tokens)


# --- The thread pool ---------------------------------------------------------------------------------------------


def test_a_server_worker_runs_a_few_requests_at_once_and_a_laptop_keeps_anyios_default(store, monkeypatch):
    """16 people at once took a worker to 1.9-2.15 GB with anyio's 40 threads, 1.33 GB with 4, the same work done
    (one process works on one request at a time: the GIL). Server mode caps the pool; the laptop is as before."""
    monkeypatch.delenv(web_app.THREADS_ENV, raising=False)
    with TestClient(create_app(web_settings(), store, start_reader=False, server=True)) as client:
        assert _tokens(client) == web_app.WORKER_THREADS == 4
    monkeypatch.setenv(web_app.THREADS_ENV, "6")  # server.json's worker_threads, through the service
    with TestClient(create_app(web_settings(), store, start_reader=False, server=True)) as client:
        assert _tokens(client) == 6
    monkeypatch.setenv(web_app.THREADS_ENV, "many")  # a typo never stops the server
    assert web_app.worker_threads() == web_app.WORKER_THREADS
    monkeypatch.setenv(web_app.THREADS_ENV, "6")
    with TestClient(create_app(web_settings(), store, start_reader=False)) as client:
        assert _tokens(client) == 40  # anyio's default


def test_the_servers_processes_keep_two_malloc_arenas_on_linux(monkeypatch):
    """Each thread of a web worker could make a malloc arena of its own, which kept what a request's peak freed there:
    on 60,000 invoices a worker held 714 MB, 536 MB with two (D-195). The server limits this process (``mallopt``) and
    those it starts (``MALLOC_ARENA_MAX``); one set already is left as it is, and other systems are not touched:
    glibc's arenas only (Windows and macOS have their own allocators, musl no ``mallopt``)."""
    import ctypes

    asked = []
    monkeypatch.setattr(ctypes, "CDLL", lambda name: SimpleNamespace(mallopt=lambda *a: asked.append(a)))
    monkeypatch.setenv(server.ARENAS_ENV, "")  # (as if not set; put back as it was after the test)
    monkeypatch.setattr(server.sys, "platform", "linux")
    server.fewer_malloc_arenas()
    assert os.environ[server.ARENAS_ENV] == "2" and asked == [(-8, 2)]  # M_ARENA_MAX
    monkeypatch.setenv(server.ARENAS_ENV, "4")  # IT's own
    server.fewer_malloc_arenas()
    assert os.environ[server.ARENAS_ENV] == "4" and len(asked) == 1
    monkeypatch.delenv(server.ARENAS_ENV)
    for system in ("darwin", "win32"):
        monkeypatch.setattr(server.sys, "platform", system)
        server.fewer_malloc_arenas()
        assert server.ARENAS_ENV not in os.environ and len(asked) == 1
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", lambda name: SimpleNamespace())  # a libc without mallopt (musl)
    server.fewer_malloc_arenas()
    assert os.environ[server.ARENAS_ENV] == "2" and len(asked) == 1


def test_server_json_gives_the_workers_their_threads(tmp_path, monkeypatch):
    from ap_coder.ops import service

    monkeypatch.delenv("AP_WORKER_THREADS", raising=False)
    assert "AP_WORKER_THREADS" not in service.serve_env(service.settings(tmp_path), tmp_path)  # the default
    service.write_config(tmp_path, worker_threads=6)
    assert service.serve_env(service.settings(tmp_path), tmp_path)["AP_WORKER_THREADS"] == "6"
    service.write_config(tmp_path, worker_threads=400)
    assert service.settings(tmp_path).worker_threads == 40


def test_server_json_says_how_many_capture_processes_read_the_imports(tmp_path, monkeypatch):
    """``capture_processes`` in server.json reaches the dashboard as AP_CAPTURE_PROCESSES (the .env alone set it), and
    the server starts that many; 0 or none: one per core but two, as before."""
    from ap_coder.ops import service
    from ap_coder.web import server

    monkeypatch.delenv(server.CAPTURE_ENV, raising=False)
    assert server.CAPTURE_ENV not in service.serve_env(service.settings(tmp_path), tmp_path)  # the default
    service.write_config(tmp_path, capture_processes=3)
    env = service.serve_env(service.settings(tmp_path), tmp_path)
    assert env[server.CAPTURE_ENV] == "3"
    monkeypatch.setenv(server.CAPTURE_ENV, env[server.CAPTURE_ENV])
    assert server.capture_processes(cores=32, memory_mb=64_000) == 3
    monkeypatch.delenv(server.CAPTURE_ENV)
    service.write_config(tmp_path, capture_processes=500)
    assert service.settings(tmp_path).capture_processes == 64
    monkeypatch.setenv(server.CAPTURE_ENV, "2")  # the .env's still wins over server.json, as for the other keys
    assert service.settings(tmp_path).capture_processes == 2


# --- The long routes' lane ---------------------------------------------------------------------------------------


def test_the_long_routes_are_routes_of_the_app_and_run_in_their_lane(store):
    app = create_app(web_settings(), store, start_reader=False, server=True)
    found = {}
    for route in roles.leaf_routes(app.routes):
        if isinstance(route, APIRoute):
            for method in route.methods:
                found[(method, route.path)] = getattr(route.endpoint, "heavy", False)
    assert sorted(core.HEAVY_ROUTES - set(found)) == [], "a long route that is gone (web/core.py HEAVY_ROUTES)"
    assert sorted(key for key, heavy in found.items() if heavy) == sorted(core.HEAVY_ROUTES)


def _request(server_mode: bool = True):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(server_mode=server_mode)))


def test_at_most_two_long_requests_run_at_once_never_on_the_pools_threads_and_a_long_wait_is_busy(monkeypatch):
    monkeypatch.setattr(core, "HEAVY_WAIT", 0.5)
    release = threading.Event()
    running, most = [0], [0]
    lock = threading.Lock()

    def report() -> str:
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
        release.wait(5)
        with lock:
            running[0] -= 1
        return "done"

    async def main() -> tuple[list, float]:
        pool = anyio.to_thread.current_default_thread_limiter()
        pool.total_tokens = 4
        answers: list = []

        async def one() -> None:
            try:
                answers.append(await core.run_heavy(_request(), report))
            except HTTPException as exc:
                answers.append(exc.status_code)

        async with anyio.create_task_group() as tg:
            for _ in range(3):
                tg.start_soon(one)
            await anyio.sleep(0.2)
            borrowed = pool.borrowed_tokens  # the pool's four are all free for the other pages
            await anyio.sleep(0.6)  # the third has waited its HEAVY_WAIT by now
            release.set()
        return answers, borrowed

    answers, borrowed = asyncio.run(main())
    assert most[0] == core.HEAVY_AT_ONCE == 2 and borrowed == 0
    assert sorted(answers, key=str) == [503, "done", "done"]


def test_on_a_laptop_a_long_route_runs_as_any_page_does(monkeypatch):
    async def main() -> tuple[str, float]:
        pool = anyio.to_thread.current_default_thread_limiter()
        seen = []
        answer = await core.run_heavy(_request(server_mode=False), lambda: seen.append(pool.borrowed_tokens) or "ok")
        return answer, seen[0]

    assert asyncio.run(main()) == ("ok", 1)  # on the pool's own thread, as FastAPI runs a plain handler


def test_people_pages_are_answered_while_two_reports_run_and_a_third_is_told_it_is_busy(store, monkeypatch):
    from ap_coder.web.pages import activity

    monkeypatch.setattr(core, "HEAVY_WAIT", 1.0)
    started, release = threading.Semaphore(0), threading.Event()
    hidden = activity._hidden

    def slow_hidden(request):  # the Activity page held up while it reads, as a year of events is on a big database
        started.release()
        release.wait(10)
        return hidden(request)

    monkeypatch.setattr(activity, "_hidden", slow_hidden)
    app = create_app(web_settings(), store, start_reader=False, server=True)
    with TestClient(app, base_url="https://localhost", client=("10.0.0.5", 5000), follow_redirects=False) as client:
        assert sign_in(client, "boss", GOOD).status_code == 200
        reports = [threading.Thread(target=lambda: answers.append(client.get("/activity").status_code))
                   for _ in range(2)]  # fmt: skip
        answers: list[int] = []
        for thread in reports:
            thread.start()
        assert started.acquire(timeout=5) and started.acquire(timeout=5)  # both reports under way
        t0 = time.monotonic()
        assert client.get("/review").status_code == 200  # the worker's own threads are free for everyone else
        assert time.monotonic() - t0 < 5
        busy = client.get("/activity", headers={"Accept": "text/html"})
        assert busy.status_code == 503 and "busy" in busy.text and "Report a problem" not in busy.text
        release.set()
        for thread in reports:
            thread.join(10)
        assert answers == [200, 200]


def test_an_export_batch_is_made_on_a_thread_never_on_the_workers_event_loop(store, monkeypatch):
    """POST /exports/create ran its work on the event loop itself: for a big JD Edwards batch, seconds in which the
    worker answered nobody."""
    from fastapi.responses import JSONResponse

    from ap_coder.web.pages import exports

    seen = []

    def made(request, form):
        seen.append(threading.current_thread().name)
        return JSONResponse({"ok": True})

    monkeypatch.setattr(exports, "_create_batch", made)
    app = create_app(web_settings(), store, start_reader=False, server=True)
    with TestClient(app, base_url="https://localhost", client=("10.0.0.5", 5000), follow_redirects=False) as client:
        assert sign_in(client, "boss", GOOD).status_code == 200
        assert client.post("/exports/create", data={"fmt": "csv", "ids": "1"}, headers=PAGE).status_code == 200
    assert seen and seen[0].startswith("AnyIO worker thread")


# --- A worker started afresh -------------------------------------------------------------------------------------


class _Server:
    should_exit = False

    def __init__(self) -> None:
        self.config = SimpleNamespace(timeout_graceful_shutdown=server.GRACEFUL_SECONDS)


def _recycle(tmp_path, monkeypatch, **limits):
    state = SimpleNamespace(jobs=Jobs(), daily_backup=SimpleNamespace(thread=None))

    async def page(scope, receive, send):
        pass

    worker = server.Recycle(page, state=state, data=tmp_path, jitter=SimpleNamespace(randint=lambda a, b: 0),
                            **{"max_mb": 0, "max_requests": 0, **limits})  # fmt: skip
    found = _Server()
    monkeypatch.setattr(server, "uvicorn_server", lambda: found)
    monkeypatch.setattr(server, "DRAIN_SECONDS", 0.0)
    return worker, state, found


def _serve(worker, n: int = 1) -> None:
    async def main() -> None:
        for _ in range(n):
            await worker({"type": "http"}, None, None)
        await asyncio.sleep(0.01)  # (its stop comes DRAIN_SECONDS later)

    asyncio.run(main())


def test_a_worker_ends_after_its_requests_once_nothing_of_its_own_would_end_with_it(tmp_path, monkeypatch):
    from ap_coder.web.jobs import RUNNING

    worker, state, found = _recycle(tmp_path, monkeypatch, max_requests=4)
    _serve(worker, 3)
    assert not found.should_exit
    state.jobs.get("process").state = RUNNING  # an import running in this worker: it would end with it
    _serve(worker, 3)
    assert not found.should_exit and worker.why == "4 requests"
    state.jobs.get("process").state = "done"
    _serve(worker)
    assert found.should_exit  # uvicorn finishes the requests under way, then the process ends; another replaces it
    found.should_exit = False
    _serve(worker)
    assert not found.should_exit  # asked once


def test_a_worker_ending_lets_its_requests_under_way_finish_however_long_they_take(tmp_path, monkeypatch):
    """An import run whole in its request (the ERP register, the vendor master, the ERP history...) was cut with a 500
    after uvicorn's grace (25 s) while its work went on, and a retry did it twice. A worker started afresh gives them
    the time they need (an hour at most); a stop of the service still ends it at its deadline (``join_all``)."""
    worker, _, found = _recycle(tmp_path, monkeypatch, max_requests=1)
    _serve(worker)
    assert found.should_exit and found.config.timeout_graceful_shutdown == server.RECYCLE_GRACE_SECONDS >= 3600


def test_a_worker_due_whose_uvicorn_server_is_not_found_says_so_once(tmp_path, monkeypatch, caplog):
    """Found through uvicorn's SIGTERM handler: a uvicorn that changes it, or a worker run another way, would never be
    started afresh and said nothing. It is said in the server's log, once."""
    import logging

    worker, _, _ = _recycle(tmp_path, monkeypatch, max_requests=1)
    monkeypatch.setattr(server, "uvicorn_server", lambda: None)
    caplog.set_level(logging.WARNING)
    _serve(worker, 3)
    said = [r.getMessage() for r in caplog.records if "not found" in r.getMessage()]
    assert len(said) == 1 and "is not started afresh" in said[0], caplog.text


def test_a_worker_ending_takes_no_new_connection_and_stops_a_moment_later(tmp_path, monkeypatch):
    """The other workers take the new connections; those it took already have DRAIN_SECONDS to ask before uvicorn
    stops it. The supervisor is told which worker is ending (``RECYCLE_PID``): it doesn't ask that one "alive?"."""
    worker, _, found = _recycle(tmp_path, monkeypatch, max_requests=1)
    monkeypatch.setattr(server, "DRAIN_SECONDS", 0.5)
    taken = []

    async def main() -> tuple[bool, bool]:
        listening = await asyncio.start_server(lambda r, w: taken.append(w), "127.0.0.1", 0)
        found.servers = [listening]
        port = listening.sockets[0].getsockname()[1]
        await worker({"type": "http"}, None, None)
        _, writer = await asyncio.open_connection("127.0.0.1", port)  # (the kernel's queue holds it for the others)
        await asyncio.sleep(0.2)
        early = found.should_exit
        await asyncio.sleep(0.5)
        writer.close()
        listening.close()
        return early, found.should_exit

    # (the event loop uvicorn gives each worker of several, on Windows too: Windows' default has no remove_reader)
    loop = asyncio.SelectorEventLoop()
    try:
        assert loop.run_until_complete(main()) == (False, True) and taken == []
    finally:
        loop.close()
    assert server._leaving(tmp_path) == os.getpid()


def test_a_worker_whose_event_loop_cant_stop_reading_a_socket_closes_it_and_still_stops(tmp_path, monkeypatch):
    """An event loop without remove_reader (Windows' Proactor loop: NotImplementedError) made the ending worker fail
    after its request, holding the restart lock: it now stops serving its listening sockets instead (the other
    workers keep their own) and stops as before (B-1710)."""
    worker, _, found = _recycle(tmp_path, monkeypatch, max_requests=1)

    async def main() -> tuple[bool, bool]:
        listening = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        found.servers = [listening]

        def unsupported(fd):
            raise NotImplementedError

        monkeypatch.setattr(asyncio.get_running_loop(), "remove_reader", unsupported)
        await worker({"type": "http"}, None, None)
        await asyncio.sleep(0.05)
        serving = listening.is_serving()
        listening.close()
        return serving, found.should_exit

    assert asyncio.run(main()) == (False, True)


def test_the_supervisor_does_not_ask_the_ending_worker_alive_and_replaces_it_once_it_has_ended(tmp_path, monkeypatch):
    """An ending worker can't answer "alive?" while its process closes, and uvicorn's supervisor holds the other end
    of the pipe too, so it waited its whole timeout_worker_healthcheck (30 s), replacing no worker meanwhile."""
    asked, started = [], []

    class Worker:
        def __init__(self, pid, alive=True):
            self.pid, self.alive, self.exitcode = pid, alive, 0
            self.process = SimpleNamespace(is_alive=lambda: self.alive)

        def is_alive(self, timeout=None):
            asked.append(self.pid)
            return self.alive

        def kill(self):
            pass

        def join(self, timeout=None):
            pass

    class Started:
        def __init__(self, config, sockets):
            self.pid = 99

        def start(self):
            started.append(self.pid)

    import uvicorn.supervisors.multiprocess as multiprocess

    monkeypatch.setattr(multiprocess, "Process", Started)
    monkeypatch.setenv(server.WORKER_ENV, json.dumps({"data": str(tmp_path), "token": "secret"}))
    supervisor = server._supervisor_class().__new__(server._supervisor_class())
    supervisor.should_exit, supervisor.sockets = threading.Event(), []
    supervisor.config = SimpleNamespace(timeout_worker_healthcheck=30)
    ending, other = Worker(11), Worker(12)
    supervisor.processes = [ending, other]
    (tmp_path / server.RECYCLE_PID).write_text("11", encoding="ascii")
    supervisor.keep_subprocess_alive()
    assert asked == [12] and started == [] and len(supervisor.processes) == 2  # the ending one left alone
    ending.alive = False  # it has ended
    supervisor.keep_subprocess_alive()
    assert started == [99] and sorted(p.pid for p in supervisor.processes) == [12, 99]


@pytest.mark.parametrize("what", ["job", "backup", "imports"])
def test_a_worker_ending_waits_for_its_own_work_started_after_it_looked(store, monkeypatch, what):
    """A request answered after ``Recycle`` looked (one on another thread, or in its DRAIN_SECONDS) may start a job or
    a backup: threads that end with the worker's process. Its lifespan waits for them once its connections are closed
    (a service stop still ends it at the stop's deadline, ``join_all``); a laptop's doesn't. Imports waiting their turn
    are in the database now (D-168): nothing of theirs ends with it, so it doesn't wait for them."""

    def ending(processes: int) -> bool:
        app = create_app(web_settings(), store, start_reader=False, background=processes == 1, processes=processes)
        monkeypatch.setattr(app.state.daily_backup, "start", lambda: None)  # (a laptop's own backup at the start)
        gate = threading.Event()
        if what == "job":
            threading.Timer(0.6, gate.set).start()
            assert app.state.jobs.get("process").start(lambda job: gate.wait(10) and {})
        elif what == "backup":
            threading.Timer(0.6, gate.set).start()
            app.state.daily_backup.thread = threading.Thread(target=gate.wait, args=(10,), daemon=True)
            app.state.daily_backup.thread.start()
        else:  # a file in line, read by the server's capture processes (a laptop's own job: monkeypatched away)
            from ap_coder.core.store.folder import read_file
            from ap_coder.web.pages import process

            (store.path.parent / "in.pdf").write_bytes(b"%PDF-1.4 in line")
            store.queue_imports([read_file(store.path.parent / "in.pdf")], source="upload")
            monkeypatch.setattr(process, "start_reading", lambda app: False)
            threading.Timer(0.6, gate.set).start()

        async def lifespan() -> None:
            async with app.router.lifespan_context(app):
                pass

        asyncio.run(lifespan())
        done = gate.is_set()
        gate.set()
        return done

    assert ending(processes=2) == (what != "imports")  # one of the server's web workers: it waited for its own
    assert not ending(processes=1)  # the laptop stops as before


def test_the_ending_workers_note_goes_once_it_ended_at_the_start_and_after_ten_minutes(tmp_path, monkeypatch):
    """``.ap_coder_worker_restart.pid`` names the worker being started afresh, which the supervisor doesn't ask
    "alive?". Left behind, it spared a later worker given the same process number (Windows gives them again) for
    good: it goes once that worker has ended, at the server's start, and is believed for ten minutes at most."""
    note = tmp_path / server.RECYCLE_PID

    class Worker:
        def __init__(self, pid):
            self.pid, self.alive, self.exitcode = pid, True, 0
            self.process = SimpleNamespace(is_alive=lambda: self.alive)

        def is_alive(self, timeout=None):
            return self.alive

        def kill(self):
            pass

        def join(self, timeout=None):
            pass

    import uvicorn.supervisors.multiprocess as multiprocess

    monkeypatch.setattr(multiprocess, "Process", lambda config, sockets: SimpleNamespace(pid=13, start=lambda: None))
    monkeypatch.setenv(server.WORKER_ENV, json.dumps({"data": str(tmp_path), "token": "secret"}))
    supervisor = server._supervisor_class().__new__(server._supervisor_class())
    supervisor.should_exit, supervisor.sockets = threading.Event(), []
    supervisor.config = SimpleNamespace(timeout_worker_healthcheck=30)
    ending, other = Worker(11), Worker(12)
    supervisor.processes = [ending, other]
    note.write_text("11", encoding="ascii")
    supervisor.keep_subprocess_alive()
    assert note.exists()  # still ending
    ending.alive = False  # it has ended (and is replaced)
    supervisor.keep_subprocess_alive()
    assert not note.exists() and sorted(p.pid for p in supervisor.processes) == [12, 13]
    note.write_text("12", encoding="ascii")
    assert server._leaving(tmp_path) == 12
    old = time.time() - 11 * 60
    os.utime(note, (old, old))
    assert server._leaving(tmp_path) is None  # asked "alive?" again (it answers while it waits for its own work)

    data = tmp_path / "data"
    store = Store(data / "ap_coder.db")
    (data / server.RECYCLE_PID).write_text(str(os.getpid()), encoding="ascii")  # left by the server's last run
    seen = []

    class Supervisor:
        failed = False

        def __init__(self, config, sockets):
            self.should_exit = threading.Event()

        def run(self):
            seen.append((data / server.RECYCLE_PID).exists())
            self.should_exit.set()

    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    monkeypatch.setattr(
        server,
        "server_config",
        lambda *a, **k: SimpleNamespace(bind_socket=lambda: SimpleNamespace(listen=lambda n: None), backlog=2048),
    )
    monkeypatch.setattr(server, "Supervisor", Supervisor)
    monkeypatch.setattr(server, "Together", lambda works: SimpleNamespace(start=lambda: None, stop=lambda: None))
    monkeypatch.setattr(server, "_announce_when_up", lambda *a: None)
    assert server._serve_workers("127.0.0.1", 8599, store, None, None, None, True, 2) == 0
    assert seen == [False]


def test_the_uvicorn_asked_for_is_one_whose_supervisor_the_server_starts():
    """The server builds uvicorn's supervisor without a target, as uvicorn 0.52 on does; pyproject and requirements.txt
    asked for 0.30 on, so an older one already installed passed the setup and the server with several workers failed
    as it started."""
    import inspect
    import re
    import tomllib

    from uvicorn.supervisors.multiprocess import Multiprocess

    assert "target" not in inspect.signature(Multiprocess.__init__).parameters
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    (asked,) = [d for d in project if d.startswith("uvicorn")]
    listed = [line.split("#")[0].strip() for line in (root / "requirements.txt").read_text("utf-8").splitlines()]
    assert asked in listed
    lowest = re.search(r">=(\d+)\.(\d+)", asked)
    assert lowest and (int(lowest[1]), int(lowest[2])) >= (0, 52), asked


def test_each_worker_ends_after_a_number_of_requests_of_its_own(tmp_path):
    import random

    limits = {server.Recycle(None, state=None, data=tmp_path, max_mb=0, max_requests=1000,
                             jitter=random.Random(seed)).limit for seed in range(20)}  # fmt: skip
    assert len(limits) > 10 and min(limits) >= 1000 and max(limits) <= 1250


def test_a_worker_past_its_memory_ends_and_one_worker_at_a_time(tmp_path, monkeypatch):
    from ap_coder.core.paths import try_lock

    worker, _, found = _recycle(tmp_path, monkeypatch, max_mb=2000)
    monkeypatch.setattr(server, "memory_mb", lambda: 1500.0)
    _serve(worker, server.MEMORY_EVERY)
    assert not found.should_exit
    monkeypatch.setattr(server, "memory_mb", lambda: 2500.0)
    other = try_lock(tmp_path / server.RECYCLE_LOCK)  # another worker is being started afresh
    _serve(worker, server.MEMORY_EVERY)
    assert not found.should_exit and "2,500 MB" in worker.why
    other.close()
    _serve(worker)
    assert found.should_exit


def test_no_worker_is_started_afresh_while_the_one_started_before_it_builds_its_memories(store, tmp_path, monkeypatch):
    """B-2067: "one worker at a time" (``RECYCLE_LOCK``) lasted only until the worker started afresh had ended; its
    replacement then built its memories before it served (D-204, up to ``WARM_MOST``) while another worker due
    meanwhile ended too: no worker served (9.7 s in a probe with two). No other is started afresh until the
    replacement serves: from the end of the one before (its note, ``RECYCLE_PID``) through the replacement's start
    (``WARMING_NOTE``, the supervisor's as it starts it) to its memories built; a replacement that never gets there
    (it crashed) lets the others go after ``WARMING_SECONDS``."""
    from ap_coder.core.paths import try_lock

    ending = try_lock(tmp_path / server.RECYCLE_LOCK)  # (a worker started afresh, finishing its requests)
    (tmp_path / server.RECYCLE_PID).write_text("4242", encoding="ascii")
    worker, _, found = _recycle(tmp_path, monkeypatch, max_requests=1)
    _serve(worker)
    assert not found.should_exit
    ending.close()  # (it has ended; the supervisor has not started its replacement yet)
    _serve(worker)
    assert not found.should_exit and worker.stopping is None
    lock = try_lock(tmp_path / server.RECYCLE_LOCK)  # (let go of: another worker may look in its turn)
    assert lock is not None
    lock.close()
    server._left(tmp_path, 4242)  # (the supervisor starts its replacement)
    assert server._leaving(tmp_path) is None
    seen = []

    def steps(store, settings_factory):  # (the replacement building its memories: a slow step)
        def slow():
            _serve(worker)  # (the worker due asks again meanwhile)
            seen.append(found.should_exit)

        yield "a slow step", slow

    monkeypatch.setattr(server, "warm_steps", steps)
    _as_a_worker(store, tmp_path, monkeypatch)
    server.worker_app()
    assert seen == [False]
    _serve(worker)
    assert found.should_exit  # (the replacement serves: the next one goes)

    worker.stopping.close()  # (it has ended)
    server._left(tmp_path, os.getpid())  # (its replacement started, and never served: it crashed)
    old = time.time() - server.WARMING_SECONDS - 1
    os.utime(tmp_path / server.WARMING_NOTE, (old, old))
    late, _, found = _recycle(tmp_path, monkeypatch, max_requests=1)
    _serve(late)
    assert found.should_exit


def test_the_limits_come_from_the_memory_and_the_settings(monkeypatch):
    monkeypatch.delenv(server.MEMORY_ENV, raising=False)
    monkeypatch.delenv(server.REQUESTS_ENV, raising=False)
    assert server.recycle_limits(28, 64_000) == {"max_mb": 2000.0, "max_requests": 50_000}
    assert server.recycle_limits(4, 16_000)["max_mb"] == 3000.0  # its share of the 12 GB the workers may use
    monkeypatch.setenv(server.MEMORY_ENV, "0")  # never
    monkeypatch.setenv(server.REQUESTS_ENV, "300")
    assert server.recycle_limits(4, 16_000) == {"max_mb": 0.0, "max_requests": 300.0}


def test_this_processs_memory_and_the_computers_are_known():
    used, total = server.memory_mb(), server.memory_total_mb()
    assert used is not None and total is not None and 10 < used < total


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_worker_of_the_server_is_started_afresh_and_no_request_or_login_is_lost(tmp_path):
    """The real server, two workers, each ended after a few requests: the supervisor starts others in their place,
    every request is answered, and the login made before still works after."""
    from ap_coder.core import authenticator

    data, port = tmp_path / "data", _free_port()
    store = Store(data / "ap_coder.db")
    uid = store.add_user("boss", GOOD, role="admin", display_name="Boss")
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    secret = enrol(store, uid)
    (tmp_path / "empty.env").write_text("", encoding="utf-8")
    env = {**os.environ, "AP_PRIVATE_DIR": str(data), "BROWSER": "none", "AP_ALLOWED_HOSTS": "127.0.0.1",
           server.REQUESTS_ENV: "6"}  # fmt: skip
    command = [sys.executable, "-m", "ap_coder", "--env-file", str(tmp_path / "empty.env"), "--db",
               str(data / "ap_coder.db"), "serve", "--server", "--insecure-http", "--host", "127.0.0.1", "--port",
               str(port), "--workers", "2"]  # fmt: skip
    root = Path(__file__).resolve().parents[2]
    log = tmp_path / "server.log"  # a file, not a pipe: a worker left running can't hold the test up
    out = log.open("w")
    proc = subprocess.Popen(command, cwd=root, env=env, stdout=out, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    cookies: dict[str, str] = {}

    def ask(path: str, form: dict | None = None) -> tuple[int, str]:
        """One request on a connection of its own, with the cookies kept so far."""
        body = urllib.parse.urlencode(form).encode() if form is not None else None
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()), "Origin": base, **PAGE}
        try:
            with direct.open(urllib.request.Request(base + path, data=body, headers=headers), timeout=30) as answer:
                status, text, sent = answer.status, answer.read().decode(), answer.headers.get_all("Set-Cookie")
        except urllib.error.HTTPError as exc:
            status, text, sent = exc.code, exc.read().decode(), exc.headers.get_all("Set-Cookie")
        for line in sent or []:
            name, _, value = line.split(";", 1)[0].partition("=")
            cookies[name.strip()] = value.strip()
        return status, text

    try:
        deadline = time.monotonic() + 120
        while True:
            try:
                with direct.open(base + "/healthz", timeout=2):
                    break
            except OSError:
                assert time.monotonic() < deadline and proc.poll() is None
                time.sleep(0.3)
        assert ask("/login", {"username": "boss", "password": GOOD, "next": "/settings"})[0] == 200
        assert ask("/login/code", {"code": authenticator.code(secret, time.time()), "next": "/settings"})[0] == 200
        workers, answers, slowest = set(), [], 0.0
        while len(workers) < 5 and time.monotonic() < deadline:  # the first two, and three started in their place
            t0 = time.monotonic()
            status, text = ask("/healthz")
            answers.append(status)
            workers.add(json.loads(text)["pid"])
            answers.append(ask("/settings")[0])  # still logged in, whichever worker answers
            slowest = max(slowest, time.monotonic() - t0)
        assert len(workers) >= 5 and set(answers) == {200}, answers
        # Never the 30 s the supervisor waited for an "alive?" the ending worker could no longer answer; at most the
        # few seconds both workers of two take to start afresh one after the other.
        assert slowest < 15
        assert proc.poll() is None  # the server runs on
        token = json.loads((data / ".ap_coder_server.json").read_text(encoding="utf-8"))["token"]
        stop = urllib.request.Request(base + "/shutdown", data=b"", method="POST", headers={"X-AP-Coder-Token": token})
        direct.open(stop, timeout=10).close()
        proc.wait(40)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)
        out.close()
    output = log.read_text(encoding="utf-8")
    assert output.count("started afresh after") >= 3, output


# --- A busy worker hands its connections back (D-161) -------------------------------------------------------------


async def _answered(app) -> bytes | None:
    """The Connection header of ``app``'s answer to a GET (None: none)."""
    said = []

    async def send(message):
        if message["type"] == "http.response.start":
            said.append(dict(message["headers"]).get(b"connection"))

    await app({"type": "http", "method": "GET", "path": "/review", "headers": []}, None, send)
    return said[0]


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})
    await send({"type": "http.response.body", "body": b"ok"})


def test_a_worker_whose_requests_wait_for_its_threads_closes_the_connection_of_each_answer():
    """The workers share one socket, and the first free one takes a new connection; but a browser keeps its own open
    for minutes (the server 75 s), so people stayed on a busy worker while another sat idle (one of four did 54% of
    the work). While requests wait for one of its threads, its answers say Connection: close; every thread taken with
    nobody waiting is not busy yet."""
    worker = server.HandBack(_ok)

    async def main() -> list[bytes | None]:
        pool = anyio.to_thread.current_default_thread_limiter()
        pool.total_tokens = 2
        said = [await _answered(worker)]
        holders = [object(), object()]
        for holder in holders:
            await pool.acquire_on_behalf_of(holder)
        said.append(await _answered(worker))  # every thread taken, nobody waiting
        waiting = asyncio.ensure_future(anyio.to_thread.run_sync(lambda: None))
        await asyncio.sleep(0.05)
        said.append(await _answered(worker))
        for holder in holders:
            pool.release_on_behalf_of(holder)
        await waiting
        said.append(await _answered(worker))
        return said

    assert anyio.run(main) == [None, None, b"close", None]


def test_a_worker_whose_long_routes_lane_is_waited_for_closes_the_connection_of_each_answer():
    """Its reports run in a lane of their own (``core.run_heavy``), never on the pool's threads: a worker whose CPU
    two reports take, more waiting their turn, is busy too. A full lane with nobody waiting is not."""
    worker = server.HandBack(_ok)

    async def main() -> list[bytes | None]:
        lane = core.heavy_lane()
        holders = [object() for _ in range(core.HEAVY_AT_ONCE)]
        for holder in holders:
            await lane.acquire_on_behalf_of(holder)
        said = [await _answered(worker)]  # full, nobody waiting
        waiting = asyncio.ensure_future(lane.acquire_on_behalf_of("next"))
        await asyncio.sleep(0.05)
        said.append(await _answered(worker))
        lane.release_on_behalf_of(holders[0])
        await waiting
        said.append(await _answered(worker))
        return said

    assert anyio.run(main) == [None, b"close", None]


def _read_answer(sock: socket.socket) -> tuple[bytes, bytes]:
    """(head, body) of one HTTP/1.1 answer read off ``sock`` (its Content-Length)."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        assert chunk, "the connection closed before the answer"
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lengths = [line.split(b":", 1)[1] for line in head.lower().split(b"\r\n") if line.startswith(b"content-length:")]
    while len(body) < int(lengths[0] if lengths else 0):
        body += sock.recv(65536)
    return head.lower(), body


def test_through_uvicorn_a_busy_workers_answer_closes_its_connection_and_a_free_ones_keeps_it(monkeypatch):
    """Over a raw socket to real uvicorn (h11, KEEP_ALIVE_SECONDS): a free worker answers on the same connection again;
    a busy one's answer arrives whole, says Connection: close once (once too when the browser asked to close), and
    the connection is closed after it, so the browser's next request opens a new one."""
    import uvicorn

    busy = {"now": False}
    monkeypatch.setattr(server, "worker_busy", lambda: busy["now"])
    port = _free_port()
    config = uvicorn.Config(server.HandBack(_ok), host="127.0.0.1", port=port, lifespan="off", log_level="warning",
                            timeout_keep_alive=server.KEEP_ALIVE_SECONDS)  # fmt: skip
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not running.started and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            for _ in range(2):  # free: kept
                sock.sendall(b"GET /review HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                head, body = _read_answer(sock)
                assert body == b"ok" and b"connection:" not in head
            busy["now"] = True
            sock.sendall(b"GET /review HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            head, body = _read_answer(sock)
            assert body == b"ok" and head.count(b"connection: close") == 1
            assert sock.recv(1) == b""  # closed after the answer
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(b"GET /review HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            head, body = _read_answer(sock)
            assert body == b"ok" and head.count(b"connection:") == 1
    finally:
        running.should_exit = True
        thread.join(5)


def test_each_web_worker_of_the_server_hands_its_connections_back_and_keeps_them_75_seconds(tmp_path, monkeypatch):
    """HandBack is one of a web worker's middlewares, and the keep-alive stays above browsers' and IIS ARR's idle
    limits: at 5 s, 12 requests got no answer in a saturated run (B-1295's race)."""
    store = Store(tmp_path / "ap_coder.db")
    spec = {"db": str(store.path), "address": "127.0.0.1", "insecure_http": True, "workers": 2, "secret": "00" * 32,
            "proxy": "", "port": 8501, "token": "t", "data": str(tmp_path), "parent": os.getpid()}  # fmt: skip
    monkeypatch.setenv(server.WORKER_ENV, json.dumps(spec))
    monkeypatch.setattr(server, "_stop_worker_when_asked", lambda *a: None)
    monkeypatch.setattr(server, "worker_log", lambda folder: None)  # (no log of its own in pytest's)
    app = server.worker_app()
    assert server.HandBack in [m.cls for m in app.user_middleware]
    assert server.KEEP_ALIVE_SECONDS == 75
    assert server.server_config(app, "127.0.0.1", 8501, None, None, None).timeout_keep_alive == 75


SAID = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} (INFO|WARNING) ap_coder\.[\w.]+: ")  # (a log's line)


def test_a_web_worker_of_the_server_says_what_it_does_in_its_own_log_and_its_warnings_with_their_time(tmp_path):
    """B-2065: a web worker of the company server is a process uvicorn spawns, which set up no logging of its own: what
    AP Coder said there at INFO went nowhere, and its warnings (a worker started afresh, D-153; the invoices folder
    not listed) reached the server's window bare, through ``logging.lastResort``, with no time. A worker made as
    uvicorn makes one (a fresh interpreter, uvicorn's own loggers set up, then the factory) logs to
    ``logs/web-<n>.log``, its access log's number, in the background process's format, and the window's line has its
    time."""
    store = Store(tmp_path / "ap_coder.db")
    spec = {"db": str(store.path), "address": "127.0.0.1", "insecure_http": True, "workers": 2, "secret": "00" * 32,
            "proxy": "", "port": 8501, "token": "t", "data": str(tmp_path), "parent": os.getpid()}  # fmt: skip
    script = ("import logging, sys\nimport uvicorn\nfrom ap_coder.web import security, server\n"
              "uvicorn.Config('ap_coder.web.server:worker_app', factory=True,"
              " log_level='warning').configure_logging()\n"
              "server.worker_app()\n"
              "logging.getLogger('ap_coder.web.server').info('said at INFO')\n"
              "logging.getLogger('ap_coder.web.server').warning('said at WARNING')\n"
              "access = security.access_logger(sys.argv[1], shared=True)\n"
              "print(access.handlers[0].baseFilename)\n")  # fmt: skip
    env = {**os.environ, server.WORKER_ENV: json.dumps(spec), "AP_PRIVATE_DIR": str(tmp_path)}
    done = subprocess.run([sys.executable, "-c", script, str(tmp_path / "logs")],
                          cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True,
                          timeout=120)  # fmt: skip
    assert done.returncode == 0, done.stderr
    own = tmp_path / "logs" / "web-1.log"
    assert own.is_file(), sorted(p.name for p in (tmp_path / "logs").glob("*"))
    said = [line for line in own.read_text(encoding="utf-8").splitlines() if "said at" in line]
    assert [line.split(" ", 2)[2] for line in said] == ["INFO ap_coder.web.server: said at INFO",
                                                        "WARNING ap_coder.web.server: said at WARNING"]  # fmt: skip
    assert all(SAID.match(line) for line in said), said
    window = [line for line in done.stderr.splitlines() if "said at" in line]
    assert len(window) == 1 and SAID.match(window[0]) and window[0].endswith("WARNING ap_coder.web.server: said at "
                                                                             "WARNING"), window  # fmt: skip
    assert done.stdout.strip().endswith("access-1.log"), done.stdout  # (the same number as its access log)


def test_a_web_workers_own_log_is_set_up_once_in_a_process(tmp_path, monkeypatch):
    """B-2065: asked again in the same process, ``worker_log`` adds no second handler (each line once in the log)."""
    monkeypatch.setattr(server.logging.getLogger("ap_coder"), "handlers", [])
    monkeypatch.setattr(server.logging.getLogger(), "handlers", [object()])  # (a window's handler there already)
    monkeypatch.setattr(security, "_SLOT", [])
    level = server.logging.getLogger("ap_coder").level
    first = server.worker_log(tmp_path / "logs")
    try:
        assert first is not None and server.worker_log(tmp_path / "logs") is None
        assert server.logging.getLogger("ap_coder").handlers == [first]
        assert first.baseFilename.endswith("web-1.log")
    finally:
        if first is not None:
            first.close()
        server.logging.getLogger("ap_coder").setLevel(level)  # (and the loggers' cache cleared)
        for _, lock in security._SLOT:
            lock.close()


# --- How many workers --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cores", "gb", "workers"),
    [(32, 64, 28), (4, 16, 4), (4, 8, 2), (8, 32, 7), (64, 64, 32), (2, 4, 1), (16, None, 14)],
)
def test_the_default_number_of_workers_follows_the_cores_and_the_memory(cores, gb, workers, monkeypatch):
    """The rollout's Phase 2 server (32 vCPU, 64 GB): 28 workers, about 1.5 GB each with their threads capped, a
    quarter of the memory and an eighth of the cores left to the background process, the database and the system."""
    if gb is None:
        monkeypatch.setattr(server, "memory_total_mb", lambda: None)  # memory unknown: by the cores
    assert server.default_workers(cores, gb * 1000 if gb else None) == workers


# --- What the company server does before it answers (D-204) -------------------------------------------------------


def test_the_server_works_its_reports_out_before_its_port_opens_and_only_with_several_workers(
    store, tmp_path, monkeypatch
):
    """D-204: the company server with several web workers works the reports' default views out (``prepare_reports``)
    before its port is bound, so a connection meanwhile is refused (nobody waits on a socket nothing answers yet); a
    laptop and the server in one process work none out at their start, as before."""
    done: list = []
    monkeypatch.setattr(server, "prepare_reports", lambda store: done.append(("prepared", store.path)))

    class Supervisor:
        failed = False

        def __init__(self, config, sockets):
            self.should_exit = threading.Event()

        def run(self):
            done.append("served")
            self.should_exit.set()

    data = tmp_path / "data"
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    socket_ = SimpleNamespace(listen=lambda n: None)
    monkeypatch.setattr(server, "server_config", lambda *a, **k: SimpleNamespace(
        bind_socket=lambda: done.append("bound") or socket_, backlog=2048))  # fmt: skip
    monkeypatch.setattr(server, "Supervisor", Supervisor)
    monkeypatch.setattr(server, "Together", lambda works: SimpleNamespace(start=lambda: None, stop=lambda: None))
    monkeypatch.setattr(server, "_announce_when_up", lambda *a: None)
    assert server._serve_workers("127.0.0.1", 8599, store, None, None, None, True, 2) == 0
    assert done == [("prepared", store.path), "bound", "served"]

    import uvicorn

    done.clear()
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: done.append("served"))
    monkeypatch.setattr(server, "fewer_malloc_arenas", lambda: None)
    for on_server in (False, True):
        if on_server:
            monkeypatch.setenv("AP_SERVER_MODE", "1")
        else:
            monkeypatch.delenv("AP_SERVER_MODE", raising=False)
        server.serve("127.0.0.1", _free_port(), open_browser=False, store=store, insecure_http=True, workers=1)
    assert done == ["served", "served"]


def test_a_server_working_its_reports_out_is_running_by_its_server_file(store, tmp_path, monkeypatch):
    """B-2069: the server file (``.ap_coder_server.json``) was written once the reports had been worked out (D-204), so
    for up to 215 s a server starting said nothing: the launcher's "never two versions on one database" and
    ``dashboard_stopped`` saw none running. It is written before them, and removed as before, a stop meanwhile too."""
    seen = []

    def prepared(store):
        seen.append(json.loads(server.server_file().read_text(encoding="utf-8"))["pid"])

    class Supervisor:
        failed = False

        def __init__(self, config, sockets):
            self.should_exit = threading.Event()

        def run(self):
            self.should_exit.set()

    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(server, "server_config", lambda *a, **k: SimpleNamespace(
        bind_socket=lambda: SimpleNamespace(listen=lambda n: None), backlog=2048))  # fmt: skip
    monkeypatch.setattr(server, "Supervisor", Supervisor)
    monkeypatch.setattr(server, "Together", lambda works: SimpleNamespace(start=lambda: None, stop=lambda: None))
    monkeypatch.setattr(server, "_announce_when_up", lambda *a: None)
    monkeypatch.setattr(server, "prepare_reports", prepared)
    assert server._serve_workers("127.0.0.1", 8599, store, None, None, None, True, 2) == 0
    assert seen == [os.getpid()] and not server.server_file().exists()

    def stopped(store):
        prepared(store)
        raise KeyboardInterrupt

    monkeypatch.setattr(server, "prepare_reports", stopped)
    assert server._serve_workers("127.0.0.1", 8599, store, None, None, None, True, 2) == 0
    assert seen == [os.getpid()] * 2 and not server.server_file().exists()


def test_the_servers_reports_are_worked_out_in_a_process_of_its_own_before_it_answers(store, monkeypatch, capfd):
    """D-204: the reports' default views worked out before the server answers peaked the memory of the server's own
    process (uvicorn's supervisor, which lives as long as the server: 114 MB with its imports, 235-241 MB after the
    work at 60,000 invoices), and its allocator kept it. They are worked out in a process of its own, started afresh
    ("spawn", as uvicorn's web workers): nothing of them in this one, each kept for every process, and the window
    says what was done, as before."""
    import multiprocessing

    from ap_coder.web import refresh
    from tests.webclient import add_clean

    add_clean(store)
    worked, real = [], Store.shared_answer
    monkeypatch.setattr(Store, "shared_answer", lambda self, name, *a, **k: worked.append(name) or real(
        self, name, *a, **k))  # fmt: skip
    server.prepare_reports(store)
    assert worked == [] and multiprocessing.active_children() == []
    with sqlite3.connect(store.path) as conn:
        kept = {name for (name,) in conn.execute("SELECT name FROM report_answers")}
    assert kept >= {name for name, _ in refresh.DEFAULTS}
    said = [line for line in capfd.readouterr().err.splitlines() if "default views" in line]
    every = len(refresh.DEFAULTS)
    assert len(said) == 2 and said[0].startswith("Working out the reports' default views before the dashboard answers")
    assert said[1].startswith(f"The reports' default views worked out before the dashboard answers: {every} of "
                              f"{every}, in "), said  # fmt: skip


def test_a_database_that_had_the_reports_is_served_at_once_with_none_worked_out_before(store, monkeypatch, capfd):
    """D-204: on a database that has had the reports' default views (kept by this version or an earlier one: a restart,
    an update), the server answers at once, as before D-204: no process is started for them, and the window says in
    one line why and that they are worked out while it serves. An update's downtime stays as it was (D-188)."""
    import multiprocessing

    from ap_coder.core.store import reports
    from ap_coder.web import refresh

    for name, _ in refresh.DEFAULTS:
        store.keep_answer(name, reports.asked_key(name), "t-1", [None], [name])
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE report_answers SET version = '0.1.0/t-1'")  # (only an earlier version's)

    def started(method):
        raise AssertionError("a process was started")

    monkeypatch.setattr(multiprocessing, "get_context", started)
    server.prepare_reports(store)
    said = [line for line in capfd.readouterr().err.splitlines() if line.strip()]
    assert said == ["The reports' default views are not worked out before the dashboard answers: this database has had "
                    "them (by this version or an earlier one), and the background process brings them up to date "
                    "while it serves."]  # fmt: skip


def test_the_server_answers_though_its_reports_process_failed_or_was_ended_past_its_bound(store, tmp_path, monkeypatch,
                                                                                          capfd):  # fmt: skip
    """D-204: the reports' process failing (its database unreadable) or still working past ``PREPARE_MOST`` and
    ``PREPARE_ENDS`` (ended then) is no reason not to serve: the window says so, the server goes on to answer, and the
    background process's refresher works out what is left, as before D-204. No process is left behind."""
    import multiprocessing

    from ap_coder.web import refresh

    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database " * 1000)
    server.prepare_reports(SimpleNamespace(path=broken, answered_before=lambda names: False))
    err = capfd.readouterr().err
    assert re.search(r"WARNING ap_coder\.web\.server: The reports' default views were not worked out", err), err
    assert ("The reports' default views were not all worked out before the dashboard answers: their process stopped "
            "(exit code 1). The background process works out the rest.") in err  # fmt: skip

    monkeypatch.setattr(refresh, "PREPARE_MOST", 0.0)
    monkeypatch.setattr(server, "PREPARE_ENDS", 0.05)  # (ended while it starts)
    began = time.monotonic()
    server.prepare_reports(store)
    assert time.monotonic() - began < 10 and multiprocessing.active_children() == []
    assert ("The reports' default views were not all worked out before the dashboard answers: their process was "
            "ended after 0.05 s. The background process works out the rest.") in capfd.readouterr().err  # fmt: skip


@pytest.mark.skipif(os.name == "nt", reason="Windows: the service's job object ends the reports' process with it")
def test_a_stop_of_the_server_while_it_works_its_reports_out_ends_their_process_too(store, monkeypatch):
    """B-2068: SIGTERM (the service's stop, launchd's) while the reports were worked out before the server answered,
    before uvicorn's supervisor takes that signal, ended the server's process at once and left the reports' process
    running, holding the window's pipe: the service host's read went on until it ended (on a Mac, ``service stop`` up
    to launchd's 60 s). The signal now ends that process, then the server's start, as Ctrl+C does, and the signal's
    handler is given back."""
    import multiprocessing
    import signal

    ended: list[str] = []

    class Process:  # (the reports' process: still working when the signal comes)
        exitcode, pid = None, 4242

        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def join(self, timeout=None):
            if not ended:
                time.sleep(timeout)

        def is_alive(self):
            return not ended

        def terminate(self):
            ended.append("ended")

        def kill(self):
            ended.append("killed")

    def untaken(signum, frame):
        raise RuntimeError("SIGTERM not taken while the reports are worked out")

    monkeypatch.setattr(multiprocessing, "get_context", lambda method: SimpleNamespace(Process=Process))
    before = signal.signal(signal.SIGTERM, untaken)
    try:
        threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM)).start()
        with pytest.raises(KeyboardInterrupt):
            server.prepare_reports(store)
        assert ended == ["ended"] and signal.getsignal(signal.SIGTERM) is untaken
    finally:
        signal.signal(signal.SIGTERM, before)


def test_the_reports_process_whose_server_is_gone_stops_before_its_next_view(monkeypatch):
    """B-2068: the reports' process, its server's process killed outright (no signal to take), went on with its views
    for minutes; it looks before each view whether the process that started it still runs (``_parent_gone``)."""
    import multiprocessing

    monkeypatch.setattr(multiprocessing, "parent_process", lambda: SimpleNamespace(is_alive=lambda: False))
    assert server._parent_gone()
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: SimpleNamespace(is_alive=lambda: True))
    assert not server._parent_gone()
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: None)  # (started by nobody: this test's process)
    assert not server._parent_gone()


def test_the_reports_process_ends_quietly_on_ctrl_c(store, monkeypatch, capsys):
    """D-204's review: a Ctrl+C in the server's window reaches the reports' process too, which printed "Process
    ap-coder-reports:" and a traceback cut short there; it ends with exit code 1 and says nothing (its server says
    why it stops)."""
    from ap_coder.web import refresh

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(refresh, "prepare", interrupted)
    with pytest.raises(SystemExit) as ended:
        server._prepare_reports(str(store.path), refresh.PREPARE_MOST)
    assert ended.value.code == 1 and capsys.readouterr().err == ""


def test_the_server_answers_though_its_reports_could_not_be_looked_at_or_started(store, monkeypatch, capsys):
    """B-2071: an error looking whether the database had the reports (a database too busy) or starting their process
    (no process to be had) stopped the server's start, where its reports are no reason not to serve: it says so in
    one line of the window and the server answers, the refresher working them out while it serves."""
    import multiprocessing

    from ap_coder.web import refresh

    def locked(store):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(refresh, "served_before", locked)
    server.prepare_reports(store)
    assert capsys.readouterr().err.splitlines() == [
        "The reports' default views were not worked out before the dashboard answers (database is locked): the "
        "background process works them out while it serves."]  # fmt: skip

    def unstarted():
        raise OSError("no process to be had")

    monkeypatch.setattr(refresh, "served_before", lambda store: False)
    monkeypatch.setattr(multiprocessing, "get_context", lambda method: SimpleNamespace(
        Process=lambda **kwargs: SimpleNamespace(start=unstarted, pid=None)))  # fmt: skip
    server.prepare_reports(store)
    assert capsys.readouterr().err.splitlines() == [
        "The reports' default views were not worked out before the dashboard answers (no process to be had): the "
        "background process works them out while it serves."]  # fmt: skip


def _queue_with_lessons(store: Store) -> tuple[int, str]:
    """An invoice in the review queue, a line of it without an account (its screen suggests one from the learning
    memory), and lessons of its vendor: (the invoice, its vendor's key)."""
    from tests.webclient import add_clean, gt

    lines = gt()["line_items"]
    invoice = add_clean(store, line_items=[{**lines[0], "predicted_gl_code": "UNASSIGNED"}, *lines[1:]])
    found = store.get_invoice(invoice)
    with store._conn() as conn:
        conn.executemany("INSERT INTO feedback (vendor_key, vendor_name, description, final_gl, suggested_gl, outcome, "
                         "created_at) VALUES (?, ?, ?, '5000', '5000', 'accepted', '2026-01-02T03:04:05')",
                         [(found["vendor_key"], found["vendor_name"], f"Part {n}") for n in range(20)])  # fmt: skip
    return invoice, found["vendor_key"]


def _reads(monkeypatch) -> collections.Counter:
    """What is read from the database from now on, of what a web worker builds before it serves: the learning memory
    and Learning's latest lessons (by how many), the vendor list, each vendor's lessons and invoices (by its key; its
    invoices all of them, or those changed since)."""
    from ap_coder.core.store import learning as lessons
    from ap_coder.web.pages import invoice as invoice_page

    read: collections.Counter = collections.Counter()
    fetch, vendor_list, vendor_invoices = lessons.fetch_rows, invoice_page._vendor_list, Store._vendor_invoices

    def fetching(conn, sql, params=()):
        if sql.startswith("SELECT * FROM feedback ORDER BY"):
            read["the learning memory", params[0]] += 1
        elif sql.startswith(f"SELECT {lessons._VENDOR_COLUMNS} FROM feedback WHERE vendor_key"):
            read["the lessons of", params[0]] += 1
        return fetch(conn, sql, params)

    def listing(store):
        read["the vendor list"] += 1
        return vendor_list(store)

    def invoices(self, key, ids=None, **kw):
        read["the invoices of", key, ids is None] += 1
        return vendor_invoices(self, key, ids, **kw)

    monkeypatch.setattr(lessons, "fetch_rows", fetching)
    monkeypatch.setattr(invoice_page, "_vendor_list", listing)
    monkeypatch.setattr(Store, "_vendor_invoices", invoices)
    return read


def _as_a_worker(store: Store, tmp_path, monkeypatch) -> None:
    """``worker_app`` to be made as uvicorn makes one of the company server's web workers, on ``store``."""
    spec = {"db": str(store.path), "address": "127.0.0.1", "insecure_http": True, "workers": 2, "secret": "00" * 32,
            "proxy": "", "port": 8501, "token": "t", "data": str(tmp_path), "parent": os.getpid()}  # fmt: skip
    monkeypatch.setenv(server.WORKER_ENV, json.dumps(spec))
    monkeypatch.setattr(server, "_stop_worker_when_asked", lambda *a: None)
    monkeypatch.setattr(server, "worker_log", lambda folder: None)  # (no log of its own in pytest's)


def test_a_web_worker_of_the_server_builds_its_memories_before_it_serves(store, tmp_path, monkeypatch, caplog):
    """D-204: a web worker's first open of an invoice built the learning memory, the vendor list and its vendor's
    lessons and invoices in that request (3-6 s at 300,000 invoices), its first Learning visit its latest lessons (2.2
    s). Each web worker of the company server builds them in uvicorn's factory (``worker_app``), before it serves,
    each once through the page's own function, and the pages' templates and the GL suggestions' index of the
    learning memory too (what a first open built still: 0.43 and 0.32 s at 60,000 invoices): an invoice and the review
    queue opened right after read, compile and build none of them again."""
    from ap_coder.ai import suggest
    from ap_coder.core.store import FEEDBACK_READ
    from ap_coder.web import core
    from ap_coder.web.pages import learning
    from tests.webclient import client_for

    invoice, key = _queue_with_lessons(store)
    name = store.get_invoice(invoice)["vendor_name"]
    _as_a_worker(store, tmp_path, monkeypatch)
    read = _reads(monkeypatch)
    env, compile_, index = core.templates.env, core.templates.env.compile, suggest._build_memory
    monkeypatch.setattr(env, "cache", type(env.cache)(env.cache.capacity))  # (none compiled yet in this process)
    monkeypatch.setattr(env, "compile", lambda source, name=None, *a, **k: read.update([("compiled", name)])
                        or compile_(source, name, *a, **k))  # fmt: skip
    monkeypatch.setattr(suggest, "_build_memory", lambda *a: read.update(["the GL index"]) or index(*a))
    with caplog.at_level(logging.INFO, logger="ap_coder.web.server"):
        server.worker_app()
    compiled = {what[1] for what in read if what[0] == "compiled"}
    assert {"invoice.html", "review.html", "base.html"} <= compiled
    assert {k: n for k, n in read.items() if k[0] != "compiled"} == {
        ("the learning memory", FEEDBACK_READ): 1, ("the learning memory", learning.MEMORY_READ): 1, "the GL index": 1,
        "the vendor list": 1, ("the lessons of", key): 1, ("the invoices of", key, True): 1}  # fmt: skip
    said = [r.getMessage() for r in caplog.records if "before it served" in r.getMessage()]
    assert len(said) == 1 and re.fullmatch(
        rf"A web worker \(process {os.getpid()}\) built 10 of its memories before it served, in [\d.]+ s: the "
        rf"invoice's and the queue's templates, the learning memory, the GL suggestions' index, the vendor list, the "
        rf"vendors' ERP IDs, the vendors on hold's numbers, the reading status, Learning's latest lessons, the lessons "
        rf"of {re.escape(name)}, the invoices of {re.escape(name)}\.", said[0]), said  # fmt: skip
    read.clear()
    client = client_for(store)
    assert client.get(f"/review/{invoice}").status_code == 200 and client.get("/review").status_code == 200
    assert read == {}


def test_only_a_web_worker_of_the_company_server_builds_its_memories_before_it_serves(store, tmp_path, monkeypatch):
    """D-204: a laptop's dashboard and the server in one process build nothing ahead (with its background work, each
    page builds what it reads, as before); each web worker of the company server (``worker_app``) on its database."""
    built: list = []
    monkeypatch.setattr(server, "warm_worker", lambda store, settings_factory: built.append(store.path))
    for on_server in (False, True):
        with TestClient(create_app(web_settings(), store, start_reader=False, server=on_server),
                        base_url="http://127.0.0.1") as client:  # fmt: skip
            assert client.get("/healthz").status_code == 200
    assert built == []
    _as_a_worker(store, tmp_path, monkeypatch)
    server.worker_app()
    assert built == [store.path]


def test_a_web_workers_memories_come_from_the_queues_biggest_vendors_first_within_their_bounds(store, monkeypatch):
    """D-204: people open the review queue's first invoices first: of their vendors (``WARM_FIRST`` invoices), the
    biggest first by their invoices, ``WARM_VENDORS`` at most, a vendor's invoices only while those built fit in half
    of what a worker keeps of the vendors' (``VENDOR_ROWS_KEPT``: the other half for the vendors people ask). A vendor
    with no invoice in the queue is not taken, however big."""
    from ap_coder.core.store import vendors as vendors_module
    from tests.webclient import add_clean

    for name, numbers in (("Acme Supply", 3), ("Beta Ltd", 1), ("Gamma Inc", 1), ("Delta Corp", 5)):
        for n in range(numbers):
            add_clean(store, vendor_name=name, invoice_number=f"{name[0]}-{n}")
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET status = 'approved' WHERE vendor_name = 'Delta Corp'")
    monkeypatch.setattr(vendors_module, "VENDOR_ROWS_KEPT", 6)
    monkeypatch.setattr(server, "WARM_VENDORS", 2)
    steps = [what for what, _ in server.warm_steps(store, web_settings)]
    assert steps[8:] == ["the lessons of Acme Supply", "the invoices of Acme Supply", "the lessons of Beta Ltd"]
    monkeypatch.setattr(server, "WARM_FIRST", 3)  # (the queue's first three: Acme's)
    assert [what for what, _ in server.warm_steps(store, web_settings)][8:] == [
        "the lessons of Acme Supply", "the invoices of Acme Supply"]  # fmt: skip


def test_a_web_workers_memories_stop_at_their_bound_or_its_stop_and_a_step_that_fails_is_logged(
    store, monkeypatch, caplog
):
    """D-204: a worker builds its memories within ``WARM_MOST`` (looked at before each step), well under the 30 s
    uvicorn gives a worker started afresh to serve; a worker asked to stop meanwhile stops; a step that fails is logged
    and the next goes on (the page builds it when asked), and steps that can't even be listed (a busy database) are
    logged too. Each time it says what it built."""
    done: list[str] = []

    def locked():
        raise sqlite3.OperationalError("database is locked")

    def steps(store, settings_factory):
        yield "the first", lambda: done.append("the first")
        yield "the failing", locked
        yield "the slow", lambda: (time.sleep(0.3), done.append("the slow"))
        yield "the late", lambda: done.append("the late")

    monkeypatch.setattr(server, "warm_steps", steps)
    with caplog.at_level(logging.INFO, logger="ap_coder.web.server"):
        assert server.warm_worker(store, web_settings, most=0.2) == ["the first", "the slow"] == done
    said = [r.getMessage() for r in caplog.records]
    assert said[0] == "A web worker did not build the failing before it served."
    assert re.fullmatch(r"A web worker \(process \d+\) built 2 of its memories before it served, in [\d.]+ s; stopped "
                        r"at 0.2 s: the first, the slow\.", said[1]), said  # fmt: skip

    caplog.clear()
    monkeypatch.setattr(server, "uvicorn_server", lambda: SimpleNamespace(should_exit=True))
    with caplog.at_level(logging.INFO, logger="ap_coder.web.server"):
        assert server.warm_worker(store, web_settings) == []
    assert "built 0 of its memories before it served, in" in caplog.text and "stopped: the worker stops: none" in (
        caplog.text)  # fmt: skip

    def unlisted(store, settings_factory):
        yield "the first", lambda: None
        locked()

    monkeypatch.setattr(server, "uvicorn_server", lambda: None)
    monkeypatch.setattr(server, "warm_steps", unlisted)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="ap_coder.web.server"):
        assert server.warm_worker(store, web_settings) == ["the first"]
    assert "A web worker stopped building its memories before it served." in caplog.text
    assert "built 1 of its memories before it served" in caplog.text and "stopped on an error: the first." in (
        caplog.text)  # fmt: skip


def test_the_real_server_works_its_reports_and_each_workers_memories_out_then_serves_and_again_works_none_out(
    tmp_path,
):
    """D-204: the real server, two web workers, on a database never served: its window says the reports' default
    views were worked out before it answered, each web worker's own log (``logs/web-<n>.log``, B-2065) that it built
    its memories before it served, and it serves. Started again, it answers at once, the reports worked out while it
    serves (this database has had them), each web worker's memories built again."""
    from ap_coder.web import refresh
    from tests.webclient import add_clean

    data, port = tmp_path / "data", _free_port()
    add_clean(Store(data / "ap_coder.db"))
    every = len(refresh.DEFAULTS)
    (tmp_path / "empty.env").write_text("", encoding="utf-8")
    env = {**os.environ, "AP_PRIVATE_DIR": str(data), "BROWSER": "none", "AP_ALLOWED_HOSTS": "127.0.0.1",
           server.CAPTURE_ENV: "1"}  # fmt: skip
    command = [sys.executable, "-m", "ap_coder", "--env-file", str(tmp_path / "empty.env"), "--db",
               str(data / "ap_coder.db"), "serve", "--server", "--insecure-http", "--host", "127.0.0.1", "--port",
               str(port), "--workers", "2"]  # fmt: skip
    root = Path(__file__).resolve().parents[2]
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{port}"

    def started(n: int) -> str:
        """The server started, until it answered and was stopped: its window."""
        window = tmp_path / f"server-{n}.log"  # a file, not a pipe: a worker left running can't hold the test up
        with window.open("w") as out:
            proc = subprocess.Popen(command, cwd=root, env=env, stdout=out, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 120
                while True:
                    try:
                        with direct.open(base + "/healthz", timeout=2) as answer:
                            assert answer.status == 200
                            break
                    except OSError:
                        assert time.monotonic() < deadline and proc.poll() is None, window.read_text()
                        time.sleep(0.3)
                token = json.loads((data / ".ap_coder_server.json").read_text(encoding="utf-8"))["token"]
                stop = urllib.request.Request(base + "/shutdown", data=b"", method="POST",
                                              headers={"X-AP-Coder-Token": token})  # fmt: skip
                direct.open(stop, timeout=10).close()
                proc.wait(40)
                assert proc.returncode == 0
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(10)
        return window.read_text(encoding="utf-8")

    first = started(1)
    assert f"worked out before the dashboard answers: {every} of {every}, in " in first, first
    for n in (1, 2):
        own = (data / "logs" / f"web-{n}.log").read_text(encoding="utf-8")
        assert re.search(r"INFO ap_coder\.web\.server: A web worker \(process \d+\) built [1-9]\d* of its memories "
                         r"before it served", own) and "did not build" not in own, own  # fmt: skip
    again = started(2)
    assert "The reports' default views are not worked out before the dashboard answers: this database has had them" in (
        again), again  # fmt: skip
    assert "Working out the reports' default views" not in again
    for n in (1, 2):
        own = (data / "logs" / f"web-{n}.log").read_text(encoding="utf-8")
        assert own.count("of its memories before it served") == 2, own


def test_every_wait_for_a_server_started_covers_what_it_does_before_it_answers():
    """D-204: a first start on a database that never had the reports works them out before the server answers, then
    at each start each web worker builds its memories: server-update's wait for the new version (before it goes back
    to the old one), the load and day benches' and CI's update job's waits for the server they start cover both bounds
    (the reports' process ended ``PREPARE_ENDS`` past its own), the step under way at the workers', and the processes'
    own start. An update of a database that had them works none out; one that never had them still stays within
    D-188's 5 minutes: those and server-update's own stop, copy and update (16-40 s measured)."""
    import inspect

    from ap_coder.bench import day, load
    from ap_coder.ops import server_update
    from ap_coder.web import refresh
    from tests.scripts import load_script

    first = refresh.PREPARE_MOST + server.PREPARE_ENDS + server.WARM_MOST + 30
    downtime = load_script("ci/server_update_downtime", "ap_script_server_update_downtime_waits")
    waits = {"server-update": server_update.UP_WAIT, "bench load": load.START_WAIT, "CI's update job": downtime.UP_WAIT,
             "bench day": inspect.signature(day.Server.start).parameters["timeout"].default}  # fmt: skip
    assert all(wait >= first for wait in waits.values()), (first, waits)
    assert refresh.PREPARE_MOST + server.PREPARE_ENDS + server.WARM_MOST + 15 + 40 <= downtime.LIMIT
    assert server.WARM_MOST < 30  # (uvicorn's restart_all waits 30 s for a worker started afresh: AP Coder's is none)
