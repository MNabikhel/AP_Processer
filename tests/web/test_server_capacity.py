"""What one web worker of the company server runs at once (D-153): its thread pool capped, the long routes in a lane
of their own, a worker started afresh past its memory or requests, and how many workers the server runs."""

from __future__ import annotations

import asyncio
import json
import os
import socket
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
from ap_coder.web import core, roles, server
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


def test_server_json_gives_the_workers_their_threads(tmp_path, monkeypatch):
    from ap_coder.ops import service

    monkeypatch.delenv("AP_WORKER_THREADS", raising=False)
    assert "AP_WORKER_THREADS" not in service.serve_env(service.settings(tmp_path), tmp_path)  # the default
    service.write_config(tmp_path, worker_threads=6)
    assert service.serve_env(service.settings(tmp_path), tmp_path)["AP_WORKER_THREADS"] == "6"
    service.write_config(tmp_path, worker_threads=400)
    assert service.settings(tmp_path).worker_threads == 40


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
    state = SimpleNamespace(jobs=Jobs(), daily_backup=SimpleNamespace(thread=None), import_queue=None)

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
    """A request answered after ``Recycle`` looked (one on another thread, or in its DRAIN_SECONDS) may start a job, a
    backup or queue an import: threads that end with the worker's process. Its lifespan waits for them once its
    connections are closed (a service stop still ends it at the stop's deadline, ``join_all``); a laptop's doesn't."""
    from ap_coder.web.pages.process import ImportQueue

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
        else:
            app.state.import_queue = ImportQueue()
            app.state.import_queue.waiting.append((None, None, 1))

            def imported() -> None:  # one timer, the gate first: with two, the queue could empty before the gate was
                gate.set()  # set (both 0.6 s; under load either fires first), and the lifespan ended rightly with
                app.state.import_queue.waiting.clear()  # ``done`` still False

            threading.Timer(0.6, imported).start()

        async def lifespan() -> None:
            async with app.router.lifespan_context(app):
                pass

        asyncio.run(lifespan())
        done = gate.is_set()
        gate.set()
        return done

    assert ending(processes=2)  # one of the server's web workers: it waited for them
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
    monkeypatch.setattr(server, "BackgroundWork", lambda db: SimpleNamespace(start=lambda: None, stop=lambda: None))
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


# --- How many workers --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cores", "gb", "workers"),
    [(32, 64, 28), (4, 16, 4), (4, 8, 2), (8, 32, 7), (64, 64, 32), (2, 4, 1), (16, None, 14)],
)
def test_the_default_number_of_workers_follows_the_cores_and_the_memory(cores, gb, workers, monkeypatch):
    """The stage 2 server (32 vCPU, 64 GB): 28 workers, about 1.5 GB each with their threads capped, a quarter of
    the memory and an eighth of the cores left to the background process, the database and the system."""
    if gb is None:
        monkeypatch.setattr(server, "memory_total_mb", lambda: None)  # memory unknown: by the cores
    assert server.default_workers(cores, gb * 1000 if gb else None) == workers
