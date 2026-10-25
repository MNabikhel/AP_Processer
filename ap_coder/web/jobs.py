"""Long work in the background: the page asks for it, then polls how far it got.

Reading and coding a pile of invoices takes minutes. It runs in a thread of its own so the dashboard keeps answering
(other tabs, other pages), and the page polls ``GET /jobs/<name>`` (or a page's own status URL) for the progress, as
CloseDesk's ``/process/status`` does. One job of each name runs at a time: asking again while it runs says so.

A job's work is a function taking the ``Job``; it reports with ``job.step`` and ``job.log`` and returns a dict (the
result the page shows when it is done). An exception becomes the job's error, shown instead of a dead spinner::

    def work(job: Job) -> dict:
        for n, path in enumerate(paths, start=1):
            job.step(n - 1, len(paths), f"Reading {path.name}")
            ...
            job.log(f"{path.name}: ready", "ok", "check_circle")
        return {"messages": [{"text": "3 invoices read and coded.", "tone": "ok"}]}

    started = jobs(request).get("process").start(work)
"""

from __future__ import annotations

import contextvars
import datetime as dt
import logging
import threading
from collections.abc import Callable
from typing import Any

IDLE, RUNNING, DONE, ERROR = "idle", "running", "done", "error"
MAX_LINES = 500  # log lines kept per job (a page shows the newest)


class Job:
    """One kind of background work (e.g. "process"): its state, progress, log lines and result."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._finished = threading.Event()
        self._finished.set()
        self.state = IDLE
        self.stage = ""
        self.done = 0
        self.total = 0
        self.note = ""
        self.lines: list[dict[str, str]] = []
        self.result: dict[str, Any] | None = None
        self.error = ""
        self.started_at = ""
        self.finished_at = ""
        self.about: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return self.state == RUNNING

    def start(self, work: Callable[[Job], dict[str, Any] | None], *, total: int = 0, note: str = "",
              about: dict[str, Any] | None = None, then: Callable[[], Any] | None = None) -> bool:  # fmt: skip
        """Run ``work(job)`` in a new thread; False (nothing started) while this job is already running. ``then``:
        called on that thread once the job has finished (it may start the job again, e.g. the next import queued)."""
        with self._lock:
            if self.state == RUNNING:
                return False
            self.state, self.stage, self.done, self.total, self.note = RUNNING, "starting", 0, total, note
            self.lines, self.result, self.error, self.about = [], None, "", dict(about or {})
            self.started_at, self.finished_at = _now(), ""
            self._finished.clear()
        # The thread sees what the request saw: who started the job (web/identity.py) is who its events name.
        context = contextvars.copy_context()
        threading.Thread(target=context.run, args=(self._run, work, then), name=f"ap-coder-job-{self.name}",
                         daemon=True).start()  # fmt: skip
        return True

    def step(self, done: int, total: int | None = None, note: str = "", stage: str = "") -> None:
        """How far the work got: ``done`` of ``total``, and what it is doing now."""
        with self._lock:
            self.done = done
            if total is not None:
                self.total = total
            self.note = note
            self.stage = stage or self.stage

    def log(self, text: str, tone: str = "", icon: str = "") -> None:
        """One line for the page's progress list (plain text; the page escapes it). ``tone``: ok, warn, err, info."""
        with self._lock:
            self.lines.append({"text": str(text), "tone": tone, "icon": icon})
            del self.lines[:-MAX_LINES]

    def snapshot(self) -> dict[str, Any]:
        """Everything a page needs to draw the job, as JSON-ready data."""
        with self._lock:
            return {
                "name": self.name,
                "state": self.state,
                "stage": self.stage,
                "done": self.done,
                "total": self.total,
                "note": self.note,
                "lines": list(self.lines),
                "result": self.result,
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "about": dict(self.about),
            }

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the job is not running (tests, the command line); False when ``timeout`` ran out first."""
        return self._finished.wait(timeout)

    def _run(self, work: Callable[[Job], dict[str, Any] | None], then: Callable[[], Any] | None = None) -> None:
        try:
            result, state, error = work(self) or {}, DONE, ""
        except Exception as exc:  # noqa: BLE001 - the page shows the error instead of a spinner that never stops
            result, state, error = None, ERROR, str(exc) or type(exc).__name__
        with self._lock:  # all at once: a page that sees the job done also sees its result
            self.state, self.result, self.error, self.finished_at = state, result, error, _now()
            if state == DONE and self.total:
                self.done = self.total
        self._finished.set()
        if then is not None:
            try:
                then()
            except Exception:  # noqa: BLE001 - what comes next never turns a finished job into an error
                logging.getLogger(__name__).exception("After the %s job", self.name)


class Jobs:
    """The app's jobs by name (``request.app.state.jobs``), made on first use."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def get(self, name: str) -> Job:
        with self._lock:
            if name not in self._jobs:
                self._jobs[name] = Job(name)
            return self._jobs[name]

    def known(self, name: str) -> bool:
        with self._lock:
            return name in self._jobs


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")
