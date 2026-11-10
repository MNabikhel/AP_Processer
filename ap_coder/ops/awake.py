"""A laptop reading invoices is not put to sleep by Windows meanwhile.

Windows puts an idle laptop to sleep after a few minutes, and a reading nobody touches the keyboard for looks idle to
it: the page reader's first self-test takes 10 to 20 minutes, an import of scans as long. While a file is read, the
thread reading it asks Windows to stay awake (``SetThreadExecutionState``), and gives that back as soon as it is read:
AP Coder waiting for work never keeps the laptop awake. The display may still turn off, and closing the lid or the
power button still puts it to sleep. Nothing is asked on a Mac or Linux, nor on the company server, whose sleep
settings are IT's (docs/SERVER.md).
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator

from .offline import server_mode

ES_CONTINUOUS = 0x80000000  # the state asked stays until this thread asks otherwise (or ends)
ES_SYSTEM_REQUIRED = 0x00000001  # the system stays in the working state (never ES_DISPLAY_REQUIRED: the screen may go off)


@contextlib.contextmanager
def stay_awake() -> Iterator[bool]:
    """While the block runs, a laptop on Windows is asked not to go to sleep by itself; given back as the block ends,
    by the thread that asked (the request is the thread's own). Yields whether it was asked: never on another system,
    nor on the company server, nor when Windows refuses (the reading goes on all the same)."""
    asked = sys.platform == "win32" and not server_mode() and _ask(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield asked
    finally:
        if asked:
            _ask(ES_CONTINUOUS)


def _ask(state: int) -> bool:
    """Windows' kernel32 asked for ``state`` for this thread; whether it took it (0, or no such call: it did not)."""
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(state)))
    except Exception:  # noqa: BLE001 - a courtesy to the person waiting: never the reading's undoing
        return False
