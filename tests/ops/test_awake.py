"""A laptop reading invoices asks Windows not to go to sleep by itself (``ops/awake.py``)."""

from __future__ import annotations

import ctypes
import sys
from types import SimpleNamespace

import pytest

from ap_coder.ops import awake

CONTINUOUS, SYSTEM, DISPLAY = 0x80000000, 0x00000001, 0x00000002


@pytest.fixture
def windows(monkeypatch):
    """Windows as ``stay_awake`` sees it, its kernel32 a stand-in: every state asked of it, in order."""
    asked: list[int] = []

    def set_state(state):
        asked.append(int(getattr(state, "value", state)))
        return CONTINUOUS  # (the state before: not 0, which is a failure)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=set_state)),
                        raising=False)  # fmt: skip
    monkeypatch.delenv("AP_SERVER_MODE", raising=False)
    return asked


def test_a_laptop_on_windows_stays_awake_while_it_reads_and_gives_it_back_after(windows):
    """Windows put a laptop to sleep by its idle timer in the middle of a long reading (the page reader's first
    self-test takes 10 to 20 minutes): while a file is read it is asked to stay awake, and no longer once it is read."""
    with awake.stay_awake() as asked:
        assert asked and windows == [CONTINUOUS | SYSTEM]
    assert windows == [CONTINUOUS | SYSTEM, CONTINUOUS]
    assert not any(state & DISPLAY for state in windows)  # the display is never kept on


def test_it_is_given_back_when_the_reading_fails(windows):
    with pytest.raises(RuntimeError), awake.stay_awake():
        raise RuntimeError("the file could not be read")
    assert windows == [CONTINUOUS | SYSTEM, CONTINUOUS]


def test_the_company_server_and_other_systems_are_left_alone(windows, monkeypatch):
    """A server's sleep settings are IT's (docs/SERVER.md); a Mac or Linux has no such call."""
    monkeypatch.setenv("AP_SERVER_MODE", "1")
    with awake.stay_awake() as asked:
        assert not asked
    monkeypatch.delenv("AP_SERVER_MODE")
    monkeypatch.setattr(sys, "platform", "darwin")
    with awake.stay_awake() as asked:
        assert not asked
    assert windows == []


def test_a_windows_that_refuses_never_stops_the_reading(windows, monkeypatch):
    def refused(state):
        raise OSError("no such call here")

    monkeypatch.setattr(ctypes.windll.kernel32, "SetThreadExecutionState", refused)
    with awake.stay_awake() as asked:
        assert not asked


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own call")
def test_on_windows_itself_the_call_is_accepted(monkeypatch):
    monkeypatch.delenv("AP_SERVER_MODE", raising=False)
    with awake.stay_awake() as asked:
        assert asked


def test_a_file_read_from_the_import_queue_and_a_page_read_are_read_awake():
    """Where it is asked: around each file the import queue reads, each invoice the page reader reads and its
    self-test (the work that takes minutes), never while they wait for work."""
    import inspect

    from ap_coder.ai import page_worker
    from ap_coder.intake import imports

    assert "with stay_awake():" in inspect.getsource(imports.Reader._read)
    assert "with stay_awake()" in inspect.getsource(page_worker._read_one)
    assert "with stay_awake():" in inspect.getsource(page_worker._test)
    assert "stay_awake" not in inspect.getsource(page_worker.BackgroundReader.run)  # (asked while idle: never asleep)
