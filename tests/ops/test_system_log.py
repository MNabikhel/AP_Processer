"""Failures told to the computer's own log on the company server (D-187, ``ops/system_log.py``): at most once an hour
a message, never on a laptop, through ``eventcreate`` on Windows and syslog elsewhere; and each failure that tells it
(a backup failed or refused for a full disk, a copy to the second folder failed, the import queue paused on a full
disk, a background or capture process stopped, a model server's calls moved to its backup)."""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import time
from pathlib import Path

import pytest

from ap_coder.core.store import Store
from ap_coder.ops import offline, system_log


@pytest.fixture
def server_on(monkeypatch):
    monkeypatch.setenv(offline.SERVER_ENV, "1")


def test_a_message_is_told_once_an_hour_and_only_on_the_server(monkeypatch, no_system_log):
    assert system_log.report(system_log.BACKUP_FAILED, "the backup failed") is False  # a laptop: nothing
    assert no_system_log == []
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    clock = [1000.0]
    monkeypatch.setattr(system_log.time, "monotonic", lambda: clock[0])
    assert system_log.report(system_log.BACKUP_FAILED, "the   backup\nfailed") is True
    assert system_log.report(system_log.BACKUP_FAILED, "the backup failed") is False  # the same, within the hour
    assert system_log.report(system_log.DISK_FULL, "the backup failed") is True  # another kind
    assert system_log.report(system_log.BACKUP_FAILED, "another reason") is True
    assert system_log.report(system_log.BACKUP_FAILED, "a third", key="another reason") is False  # its key's
    clock[0] += system_log.EVERY
    assert system_log.report(system_log.BACKUP_FAILED, "the backup failed", warning=True) is True
    assert no_system_log == [(101, "the backup failed", False), (102, "the backup failed", False),
                             (101, "another reason", False), (101, "the backup failed", True)]  # fmt: skip


def test_a_system_log_that_refuses_never_stops_ap_coder(monkeypatch, server_on):
    def broken(kind, text, warning):
        raise OSError("the event log is full")

    monkeypatch.setattr(system_log, "_send", broken)
    assert system_log.report(system_log.CERTIFICATE, "x" * 5000) is True


def test_windows_is_told_through_eventcreate(monkeypatch):
    ran = []
    monkeypatch.setattr(system_log.subprocess, "run", lambda command, **kwargs: ran.append((command, kwargs)))
    system_log._event_log(system_log.MODEL_BACKUP, 'the "chat" model', True)
    command, kwargs = ran[0]
    assert command == ["eventcreate", "/L", "APPLICATION", "/T", "WARNING", "/SO", "AP Coder", "/ID", "105", "/D",
                       "the 'chat' model"]  # fmt: skip
    assert kwargs["timeout"] == 10 and kwargs["check"] is False and kwargs["capture_output"] is True
    system_log._event_log(system_log.BACKUP_FAILED, "failed", False)
    assert ran[1][0][4] == "ERROR"


@pytest.mark.skipif(sys.platform == "win32", reason="syslog's socket: macOS and Linux")
def test_syslog_is_told_through_its_socket_and_skipped_without_one(monkeypatch):
    folder = Path(tempfile.mkdtemp())  # (a socket's path is short: not the test's own folder)
    try:
        monkeypatch.setattr(system_log, "SOCKET", str(folder / "none"))
        system_log._syslog(system_log.DISK_FULL, "the disk is full", False)  # no socket: nothing, quietly
        listening = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        listening.bind(str(folder / "log"))
        listening.settimeout(5)
        monkeypatch.setattr(system_log, "SOCKET", str(folder / "log"))
        system_log._syslog(system_log.DISK_FULL, "the disk is full", False)
        said = listening.recv(4096).decode()
        listening.close()
        assert f"ap-coder[{os.getpid()}]: [102] the disk is full" in said
        assert said.startswith("<11>")  # the user facility (1), an error (3)
    finally:
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()


# --- What tells it ------------------------------------------------------------------------------------------------


def test_a_failed_backup_and_one_refused_for_a_full_disk_are_told(tmp_path, monkeypatch, server_on, no_system_log):
    from ap_coder.core.store import backups

    store = Store(tmp_path / "ap.db")
    with pytest.MonkeyPatch.context() as patch, pytest.raises(OSError):
        patch.setattr(store, "backup_to", lambda dest: (_ for _ in ()).throw(OSError("the disk is gone")))
        store.backup_now()
    assert no_system_log[0][0] == system_log.BACKUP_FAILED and "the disk is gone" in no_system_log[0][1]
    monkeypatch.setattr(backups, "BACKUP_RESERVE", 10**18)
    with pytest.raises(OSError, match="not enough free space"):
        store.backup_now()
    assert no_system_log[1][0] == system_log.DISK_FULL and "not enough free space" in no_system_log[1][1]
    with pytest.raises(OSError):
        store.backup_now()  # tried again within the hour: told once
    assert len(no_system_log) == 2


def test_a_copy_to_the_second_folder_that_fails_is_told_as_a_warning(tmp_path, server_on, no_system_log):
    store = Store(tmp_path / "ap.db")
    store.set_setting("backup_copy_dir", str(tmp_path / "share"))  # (not one IT allows: refused)
    store.backup_now()
    assert store.get_setting("backup_copy_status").startswith("failed")
    assert [(kind, warning) for kind, _, warning in no_system_log] == [(system_log.BACKUP_FAILED, True)]
    assert "writes only to the folders IT allows" in no_system_log[0][1]


def test_the_import_queue_paused_on_a_full_disk_is_told(tmp_path, monkeypatch, server_on, no_system_log):
    from ap_coder.intake import imports

    monkeypatch.setattr(imports, "has_room", lambda folder: False)
    reader = imports.Reader(Store(tmp_path / "ap.db"), None, "capture:test")
    assert reader._next(None) is None and reader.paused == imports.NO_ROOM
    assert no_system_log == [(system_log.DISK_FULL, f"AP Coder: {imports.NO_ROOM}", False)]


def test_a_background_process_that_stops_is_told(tmp_path, server_on, no_system_log):
    from ap_coder.web.server import BackgroundWork

    work = BackgroundWork(tmp_path / "ap.db")
    work.command = [sys.executable, "-c", "raise SystemExit(3)"]
    work.start()
    try:
        deadline = time.monotonic() + 30
        while not no_system_log and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        work.stop()
    assert no_system_log == [(system_log.PROCESS_STOPPED, "AP Coder's background work stopped (exit code 3): it is "
                              "started again", False)]  # fmt: skip


def test_a_model_servers_calls_moved_to_its_backup_are_told(server_on, no_system_log):
    from ap_coder.ai import local_llm

    breaker = local_llm.Failover(local_llm.CHAT, "http://10.0.0.8:1234/v1", "http://10.0.0.9:1234/v1")
    try:
        for _ in range(local_llm.FAILOVER_FAILURES):
            breaker.failed(breaker.primary, "connection refused")
    finally:
        breaker.forgotten = True  # (its probe of the primary stops)
    assert breaker.state()["on"] == local_llm.BACKUP
    assert len(no_system_log) == 1 and no_system_log[0][0] == system_log.MODEL_BACKUP and no_system_log[0][2]
    assert no_system_log[0][1].startswith("The chat model's server at http://10.0.0.8:1234/v1 didn't answer")
