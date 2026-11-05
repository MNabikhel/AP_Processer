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
