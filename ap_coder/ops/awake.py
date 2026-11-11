"""A laptop reading invoices is not put to sleep by Windows meanwhile.

Windows puts an idle laptop to sleep after a few minutes, and a reading nobody touches the keyboard for looks idle to
it: the page reader's first self-test takes 10 to 20 minutes, an import of scans as long. While a file is read, the
thread reading it asks Windows to stay awake (``SetThreadExecutionState``), and gives that back as soon as it is read:
AP Coder waiting for work never keeps the laptop awake. The display may still turn off, and closing the lid or the
power button still puts it to sleep. Nothing is asked on a Mac or Linux, nor on the company server, whose sleep
settings are IT's (docs/SERVER.md), but by a laptop shared with colleagues (``share``): its window asks for as long as
it serves (``serving``).
"""

from __future__ import annotations

import contextlib
import sys
import threading
from collections.abc import Iterator

from .offline import server_mode

ES_CONTINUOUS = 0x80000000  # the state asked stays until this thread asks otherwise (or ends)
ES_SYSTEM_REQUIRED = 0x00000001  # the system stays working (never ES_DISPLAY_REQUIRED: the screen may go off)


_held = threading.local()  # ``blocks``: how many ``stay_awake`` blocks this thread is inside with its request standing


@contextlib.contextmanager
def stay_awake(serving: bool = False) -> Iterator[bool]:
    """While the block runs, a laptop on Windows is asked not to go to sleep by itself; given back as the block ends,
    by the thread that asked (the request is the thread's own). Yields whether it is asked: never on another system,
    nor on the company server (but ``serving``: a laptop that serves colleagues, ``share``), nor when Windows refuses
    (the work goes on all the same). A block inside another on the same thread asks nothing and gives nothing back:
    the outer block's request stands until that one ends. Two threads each ask and give back their own; Windows
    stays awake until both have."""
    inside = getattr(_held, "blocks", 0)
    asked = bool(inside) or (sys.platform == "win32" and (serving or not server_mode())
                             and _ask(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))  # fmt: skip
    _held.blocks = inside + asked
    try:
        yield asked
    finally:
        _held.blocks = inside
        if asked and not inside:
            _ask(ES_CONTINUOUS)


def _ask(state: int) -> bool:
    """Windows' kernel32 asked for ``state`` for this thread; whether it took it (0, or no such call: it did not).
    Called through a handle of AP Coder's own with the call's types set (a DWORD in, the state before out): the
    functions of the shared ``ctypes.windll.kernel32`` are every library's, and types another one set on this call
    would make it fail here, unseen."""
    try:
        import ctypes
        from ctypes import wintypes

        call = ctypes.WinDLL("kernel32", use_last_error=True).SetThreadExecutionState
        call.argtypes, call.restype = [wintypes.DWORD], wintypes.DWORD
        return bool(call(state))
    except Exception:  # noqa: BLE001 - a courtesy to the person waiting: never the reading's undoing
        return False
