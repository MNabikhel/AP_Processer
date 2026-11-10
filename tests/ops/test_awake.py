"""A laptop reading invoices asks Windows not to go to sleep by itself (``ops/awake.py``)."""

from __future__ import annotations

import ctypes
import sys
import threading
from types import SimpleNamespace

import pytest

from ap_coder.ops import awake

CONTINUOUS, SYSTEM, DISPLAY = 0x80000000, 0x00000001, 0x00000002


class _Asked(list):
    """Every state asked of the stand-in kernel32, in order; ``threads``: the thread that asked each; ``kernel32``:
    the stand-in itself; ``opened``: the libraries AP Coder opened for itself."""

    threads: list[int]
    kernel32: SimpleNamespace
    opened: list[str]


@pytest.fixture
def windows(monkeypatch):
    """Windows as ``stay_awake`` sees it, its kernel32 a stand-in: every state asked of it, in order, and by which
    thread."""
    asked = _Asked()
    asked.threads, asked.opened = [], []

    def set_state(state):
        asked.append(int(getattr(state, "value", state)))
        asked.threads.append(threading.get_ident())
        return CONTINUOUS  # (the state before: not 0, which is a failure)

    def library(name, **options):
        asked.opened.append(name)
        return asked.kernel32

    asked.kernel32 = SimpleNamespace(SetThreadExecutionState=set_state)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", library, raising=False)
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

    monkeypatch.setattr(windows.kernel32, "SetThreadExecutionState", refused)
    with awake.stay_awake() as asked:
        assert not asked


def test_a_block_inside_another_on_one_thread_keeps_the_outer_request(windows):
    """The inner block ending sent ES_CONTINUOUS, which clears the thread's request: the outer block then read on
    with nothing keeping the laptop awake (B-2205). Only the outermost block asks, and gives back."""
    with awake.stay_awake() as outer:
        with awake.stay_awake() as inner:
            assert outer and inner and windows == [CONTINUOUS | SYSTEM]
        assert windows == [CONTINUOUS | SYSTEM]  # nothing given back while the outer block still reads
        with pytest.raises(RuntimeError), awake.stay_awake():
            raise RuntimeError("a page could not be read")
        assert windows == [CONTINUOUS | SYSTEM]
    assert windows == [CONTINUOUS | SYSTEM, CONTINUOUS]
    with awake.stay_awake():  # and asked afresh by the next reading
        assert windows[2:] == [CONTINUOUS | SYSTEM]
    assert windows[2:] == [CONTINUOUS | SYSTEM, CONTINUOUS] and set(windows.threads) == {threading.get_ident()}


def test_two_threads_reading_at_once_each_ask_and_give_back_their_own(windows):
    """The request is the thread's own (Windows keeps the laptop awake until every thread has given its back): a
    thread inside a block never counts as another's, and each gives back on the thread that asked."""
    both_inside = threading.Barrier(2)
    seen: list[bool] = []

    def read():
        with awake.stay_awake() as asked:
            both_inside.wait(timeout=10)
            seen.append(asked)

    readers = [threading.Thread(target=read) for _ in range(2)]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join(timeout=10)
    by_thread: dict[int, list[int]] = {}
    for state, thread in zip(windows, windows.threads, strict=True):
        by_thread.setdefault(thread, []).append(state)
    assert seen == [True, True] and len(by_thread) == 2
    assert all(states == [CONTINUOUS | SYSTEM, CONTINUOUS] for states in by_thread.values())


def test_the_call_is_made_through_a_library_of_its_own_with_its_types(windows):
    """``ctypes.windll.kernel32`` and its function objects are shared by every library in the process: one that sets
    other argument types on ``SetThreadExecutionState`` made AP Coder's call fail, unseen. Its own handle, with the
    documented types (a DWORD in, the state before out)."""
    from ctypes import wintypes

    with awake.stay_awake() as asked:
        assert asked
    call = windows.kernel32.SetThreadExecutionState
    assert set(windows.opened) == {"kernel32"} and call.argtypes == [wintypes.DWORD] and call.restype is wintypes.DWORD


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
