"""Sweep 4, the pilot path (backend): import, review, approval, learning, exports and the store, as the pilot tester
uses them with real English vendor PDFs (docs/BUGS.md, "Pilot path (backend)")."""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.web.app import create_app


def _web(store: Store) -> TestClient:
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)
    return TestClient(app, base_url="http://localhost")


@pytest.mark.skipif(sys.platform == "win32", reason="the file size limit (a full disk) is set with resource")
def test_a_backup_that_cannot_be_written_never_stops_the_dashboard_nor_counts_as_made(tmp_path):
    import resource

    store = Store(tmp_path / "private" / "ap_coder.db")
    store.set_setting("filler", "x" * 400_000)  # a database bigger than the disk has room for
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE, (100_000, hard))  # as a full disk: no file grows past 100 kB
    try:
        with _web(store) as client:  # the daily backup runs as the dashboard starts
            started = client.get("/review").status_code
        with pytest.raises(OSError):
            store.backup_now("manual")  # the Settings page says it could not be made (it catches OSError)
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
    assert started == 200
    assert store.list_backups() == []  # no half-written copy offered to restore, nor taken for today's backup
    assert store.auto_backup() is not None  # with room again, today's backup is made
