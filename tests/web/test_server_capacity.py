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


