"""The database does no more work than a page needs: one-off clean-ups run once, indexes are used, the background
workers reuse one store (the SQL statements and connections counted)."""

import sqlite3
import threading

from ap_coder.core import store as store_mod
from ap_coder.core.store import Store


def _statements(monkeypatch) -> list[str]:
    """Every SQL statement run from now on (each connection opened traces into the list)."""
    seen: list[str] = []
    real = sqlite3.connect

    def tracing(*args, **kwargs):
        conn = real(*args, **kwargs)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(store_mod.sqlite3, "connect", tracing)
    return seen


def test_opening_the_database_again_does_not_scan_the_learning_memory(tmp_path, monkeypatch):
    """Every Store() (each round of the page reader, the AI reviewer and the Azure check) withdrew the AP Coder's own
    approvals from the lessons and recounted blanks as coding: two scans of the whole feedback table, each time."""
    path = tmp_path / "ap.db"
    Store(path)
    seen = _statements(monkeypatch)
    Store(path)
    assert not [s for s in seen if "FROM feedback" in s or "UPDATE feedback" in s]
    with sqlite3.connect(path) as conn:  # as an older version left it: the clean-ups run once more
        conn.execute("DELETE FROM settings WHERE key = 'feedback_cleanup'")
    seen.clear()
    Store(path)
    assert [s for s in seen if s.startswith("DELETE FROM feedback")]
    seen.clear()
    Store(path)
    assert not [s for s in seen if "FROM feedback" in s or "UPDATE feedback" in s]


def test_the_background_workers_build_their_store_once(tmp_path, monkeypatch):
    from ap_coder.ai import azure_check, page_worker

    store = Store(tmp_path / "ap.db")
    built: list[int] = []

    def factory():
        built.append(1)
        return store

    monkeypatch.setattr(page_worker, "IDLE_SECONDS", 0.005)
    monkeypatch.setattr(page_worker, "OFF_SECONDS", 0.005)
    monkeypatch.setattr(page_worker, "read_one", lambda *a, **k: None)
    monkeypatch.setattr(page_worker, "self_test_if_due", lambda *a, **k: None)
    monkeypatch.setattr(page_worker.BackgroundReader, "_review_one", lambda self, s, st: 0.005)
    monkeypatch.setattr(azure_check, "OFF_SECONDS", 0.005)
    rounds: list[int] = []
    for worker in (
        page_worker.BackgroundReader(lambda: rounds.append(1) or object(), factory),
        azure_check.AzureChecker(lambda: rounds.append(1) or None, factory),
    ):
        built.clear()
        rounds.clear()
        worker.start()
        try:
            threading.Event().wait(0.2)
        finally:
            worker.stop()
            worker.join(5)
        assert len(rounds) > 3 and built == [1]


def test_the_dashboard_hands_its_own_store_to_the_background_workers(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from ap_coder.ai import azure_check, page_worker
    from ap_coder.web.app import create_app

    from .webclient import web_settings

    factories = []
    monkeypatch.setattr(page_worker, "start_background", lambda s, store_factory, **k: factories.append(store_factory))
    monkeypatch.setattr(azure_check, "start_background", lambda s, store_factory: factories.append(store_factory))
    store = Store(tmp_path / "private" / "ap_coder.db")
    with TestClient(create_app(web_settings(), store, start_reader=True), base_url="http://localhost"):
        pass
    assert len(factories) == 2 and all(f() is store for f in factories)
