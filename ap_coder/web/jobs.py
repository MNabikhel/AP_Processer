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

A bulk approval (D-185) is a job of the database instead, done by the company server's background process or a thread
of the laptop's own, as the person who asked: ``BulkRunner``, ``approve_here`` and ``run_bulk`` below.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import json
import logging
import os
import secrets
import threading
from collections.abc import Callable
from typing import Any

from ap_coder.core.store import reports
from ap_coder.web import identity

IDLE, RUNNING, DONE, ERROR = "idle", "running", "done", "error"
MAX_LINES = 500  # log lines kept per job (a page shows the newest)
# Several web workers (the company server, D-111): a job runs in the worker that started it, and every worker sees
# it through the database. Its row is renewed every RENEW seconds; one not renewed for STALE seconds is a worker that
# stopped. A finished job's last snapshot is kept for KEEP seconds.
RENEW, STALE, KEEP = 1.0, 30.0, 86400.0
OWNER = f"{os.getpid()}:{secrets.token_hex(4)}"  # this process, as its job rows name it


class Job:
    """One kind of background work (e.g. "process"): its state, progress, log lines and result."""

    def __init__(self, name: str, shared: Any = None) -> None:
        self.name = name
        self.shared = shared  # the Store every web worker sees the job in (D-111); None: this process only
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
        """Running here, or (several web workers) in another worker."""
        if self.state == RUNNING:
            return True
        found = self._elsewhere()
        return found is not None and found.get("state") == RUNNING

    def _elsewhere(self) -> dict[str, Any] | None:
        """The job as the database has it (another worker's run, or the last one finished), when shared."""
        if self.shared is None:
            return None
        try:
            row = self.shared.job_row(self.name)
            return None if row is None else json.loads(row[1])
        except Exception:  # noqa: BLE001 - a busy database: as this process sees it
            return None

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
            claimed = self.shared is None or self.shared.claim_job(self.name, OWNER, self._encoded(False), STALE)
            if not claimed:
                self.state = IDLE  # another web worker runs it
                return False
            self._finished.clear()
        # The thread sees what the request saw: who started the job (web/identity.py) is who its events name; but not
        # a page's brief wait for a report's answer: a job waits for its answers as anywhere (B-2063)
        context = reports.anywhere_in(contextvars.copy_context())
        threading.Thread(target=context.run, args=(self._run, work, then), name=f"ap-coder-job-{self.name}",
                         daemon=True).start()  # fmt: skip
        if self.shared is not None:
            threading.Thread(target=self._renew, name=f"ap-coder-job-{self.name}-renew", daemon=True).start()
        return True

    def _renew(self) -> None:
        while not self._finished.wait(RENEW):
            try:
                self.shared.renew_job(self.name, OWNER, self._encoded(), STALE)
            except Exception:  # noqa: BLE001 - a busy database: renewed next time
                pass

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
        """Everything a page needs to draw the job, as JSON-ready data: with several web workers, the run of
        whichever worker ran it last."""
        if self.state != RUNNING:
            found = self._elsewhere()
            if found is not None:
                return found
        return self._own()

    def _encoded(self, locked: bool = True) -> bytes:
        return json.dumps(self._own(locked=locked), default=str).encode("utf-8")

    def _own(self, locked: bool = True) -> dict[str, Any]:
        with self._lock if locked else contextlib.nullcontext():
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
        if self.shared is not None:
            try:
                self.shared.finish_job(self.name, OWNER, self._encoded(), KEEP)
            except Exception:  # noqa: BLE001 - its row then ends as a stopped worker's would
                logging.getLogger(__name__).warning("the %s job's end was not recorded", self.name, exc_info=True)
        self._finished.set()
        if then is not None:
            try:
                then()
            except Exception:  # noqa: BLE001 - what comes next never turns a finished job into an error
                logging.getLogger(__name__).exception("After the %s job", self.name)


class Jobs:
    """The app's jobs by name (``request.app.state.jobs``), made on first use. ``shared``: the Store through which
    several web workers see each other's jobs (D-111)."""

    def __init__(self, shared: Any = None) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self.shared = shared

    def get(self, name: str) -> Job:
        with self._lock:
            if name not in self._jobs:
                self._jobs[name] = Job(name, self.shared)
            return self._jobs[name]

    def known(self, name: str) -> bool:
        with self._lock:
            if name in self._jobs:
                return True
        try:
            return self.shared is not None and self.shared.job_row(name) is not None
        except Exception:  # noqa: BLE001 - a busy database
            return False

    def running_here(self) -> bool:
        """Whether a job runs in this process (it would end with the process: a web worker started afresh waits)."""
        with self._lock:
            return any(job.state == RUNNING for job in self._jobs.values())


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# --- Bulk approvals, approved in the background (D-185) ------------------------------------------------------------
# A person's bulk approval is a job of the database (``Store.start_bulk_job``), not of a process: the company server's
# background process approves it (``BulkRunner``, started by ``app.start_background_work``); a laptop, or a server of
# one process without its background work (a first test), on a thread of the process asked (``approve_here``). Each
# is done as the person who asked (``identity.acting_as``), their session ended or not.

BULK_RUNNERS = 2  # bulk approvals the background process does at once, each on a thread of its own
BULK_POLL = 0.5  # seconds between two looks for a bulk approval waiting (the background process)
BULK_STALE = 30.0  # seconds without an invoice done: the process approving them stopped, the next one goes on
NOT_DONE = "AP Coder stopped approving these invoices ({why}). The ones still in the queue need a person."
NO_LONGER = ("AP Coder stopped approving these invoices: your account can no longer approve invoices (an Admin "
             "changed it). The ones still in the queue need a person.")  # fmt: skip
_ENDED: dict[tuple[str, int], threading.Event] = {}  # bulk approvals a request of this process waits for (``ended``)
_ENDED_LOCK = threading.Lock()


def ended(store: Any, job_id: int) -> threading.Event:
    """Set once bulk approval ``job_id`` ends in this process (a request waits for it holding no thread, then
    ``forget_ended``)."""
    with _ENDED_LOCK:
        return _ENDED.setdefault((str(store.path), job_id), threading.Event())


def forget_ended(event: threading.Event) -> None:
    """No request waits for the bulk approval ``event`` (``ended``) is of any more."""
    with _ENDED_LOCK:
        for key in [k for k, e in _ENDED.items() if e is event]:
            del _ENDED[key]


def _may_not(store: Any, kept: dict[str, Any]) -> str:
    """Why the person who asked may no longer approve: on the server, their account turned off or its role lowered
    since (an Admin's change applies to the invoices not approved yet); "" when they still may."""
    from ap_coder.web import roles

    if not kept.get("user_id"):
        return ""
    user = store.user(kept["user_id"])
    allowed = user is not None and user.get("active") and roles.allowed(user["role"], roles.CLERK)
    return "" if allowed else NO_LONGER


def run_bulk(store: Any, settings: Any, job: dict[str, Any], runner: str, halt: threading.Event | None = None) -> None:
    """Approve bulk approval ``job`` (claimed by ``runner``) to its end, as the person who asked, and end it: done, or
    stopped with why. Put back in line when ``halt`` is set meanwhile (AP Coder stopping): the next start goes on.
    Raises nothing: a database error noting the end (or the putting back) is logged, the job then taken again once it
    is stale (``BULK_STALE``), and the thread goes on to the next."""
    from ap_coder.coding import bulk

    halt = halt or threading.Event()
    kept = job["person"].get("as") or {}
    try:
        with identity.acting_as(kept), store.session():
            why = bulk.approve_in_job(store, settings, job, runner, stopping=halt.is_set,
                                      refused=lambda: _may_not(store, kept))  # fmt: skip
    except bulk.Stopping:
        if halt.is_set():
            try:
                store.release_bulk_job(job["id"], runner)
            except Exception:  # noqa: BLE001 - taken again once it is stale
                logging.getLogger(__name__).warning("bulk approval %s not put back in line", job["id"], exc_info=True)
        return
    except Exception as exc:  # noqa: BLE001 - the person is told, never left waiting
        logging.getLogger(__name__).exception("bulk approval %s stopped", job["id"])
        why = NOT_DONE.format(why=str(exc) or type(exc).__name__)
    try:
        store.end_bulk_job(job["id"], runner, why)
    except Exception:  # noqa: BLE001 - locked past the wait, a restore's moment, a full disk: ended once taken again
        logging.getLogger(__name__).warning("the end of bulk approval %s was not noted", job["id"], exc_info=True)
    finally:
        with _ENDED_LOCK:
            waiting = _ENDED.get((str(store.path), job["id"]))
        if waiting is not None:
            waiting.set()


class BulkRunner:
    """The bulk approvals waiting, approved by the company server's background process (``app.start_background_work``):
    ``BULK_RUNNERS`` at a time, oldest first. At its start the ones left being approved are put back in line: the
    process that approved them stopped (one background process at a time, D-111)."""

    def __init__(self, settings_factory: Callable[[], Any], store_factory: Callable[[], Any]) -> None:
        self.settings_factory, self.store_factory = settings_factory, store_factory
        self._halt, self._wake = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, name="ap-coder-bulk", daemon=True)

    def start(self) -> BulkRunner:
        try:
            self.store_factory().release_left_bulk_jobs()
        except Exception:  # noqa: BLE001 - a busy database: taken again once BULK_STALE has passed
            logging.getLogger(__name__).warning("the bulk approvals left were not put back in line", exc_info=True)
        self.thread.start()
        return self

    def wake(self) -> None:
        """A bulk approval was asked for in this process: looked for now."""
        self._wake.set()

    def stop(self) -> None:
        """Stop: the approvals under way are put back in line for the next start, between two invoices."""
        self._halt.set()
        self._wake.set()

    def _run(self) -> None:
        makers = [threading.Thread(target=self._approve, args=(f"{OWNER}:bulk-{n}",), name=f"ap-coder-bulk-{n}",
                                   daemon=True) for n in range(BULK_RUNNERS)]  # fmt: skip
        for maker in makers:
            maker.start()
        for maker in makers:
            maker.join()

    def _approve(self, runner: str) -> None:
        from ap_coder.coding import bulk

        while not self._halt.is_set():
            try:
                store = self.store_factory()
                # (none taken from a database being replaced by a backup, as the other background workers)
                job = None if store.restoring() else store.claim_bulk_job(runner, BULK_STALE, bulk.JOB_FORMAT)
            except Exception:  # noqa: BLE001 - a busy database: looked at again
                logging.getLogger(__name__).debug("no bulk approval claimed", exc_info=True)
                job = None
            if job is None:
                self._wake.wait(BULK_POLL)
                self._wake.clear()
                continue
            try:
                run_bulk(store, self.settings_factory(), job, runner, self._halt)
            except Exception:  # noqa: BLE001 - never the end of this runner: the job is taken again once stale
                logging.getLogger(__name__).exception("bulk approval %s stopped", job["id"])


# The thread approving each database's bulk approvals here while some wait, and what tells it to stop (``stop_here``).
_HERE: dict[str, tuple[threading.Thread, threading.Event]] = {}
_HERE_LOCK = threading.Lock()


def approve_here(store: Any, settings_factory: Callable[[], Any]) -> None:
    """The bulk approvals waiting approved by a thread of this process, which ends once none is left: a laptop's, or
    a server of one process without its background work (no ``BulkRunner``)."""
    with _HERE_LOCK:
        if str(store.path) in _HERE:
            return  # (one is at it: it takes this one too before it ends)
        halt = threading.Event()
        here = threading.Thread(target=_approve_until_none, args=(store, settings_factory, halt),
                                name="ap-coder-bulk-here", daemon=True)  # fmt: skip
        _HERE[str(store.path)] = (here, halt)
    here.start()


def stop_here(store: Any) -> threading.Thread | None:
    """AP Coder stopping (the dashboard closed): the bulk approval under way here put back in line once the invoice
    under way is done, as the background process's (an orderly stop: its try not counted). The thread, to wait for;
    None when there is none."""
    with _HERE_LOCK:
        found = _HERE.get(str(store.path))
    if found is None:
        return None
    found[1].set()
    return found[0]


def _approve_until_none(store: Any, settings_factory: Callable[[], Any], halt: threading.Event) -> None:
    from ap_coder.coding import bulk

    runner, key, me = f"{OWNER}:bulk-here", str(store.path), threading.current_thread()
    try:
        while True:
            with _HERE_LOCK:  # (one asked meanwhile is claimed here, or finds no thread at it and starts one)
                try:  # (none taken from a database being replaced by a backup: its page's next look asks again)
                    taking = not halt.is_set() and not store.restoring()
                    job = store.claim_bulk_job(runner, BULK_STALE, bulk.JOB_FORMAT) if taking else None
                except Exception:  # noqa: BLE001 - a busy database: the next one asked, or looked at, starts another
                    job = None
                if job is None:
                    del _HERE[key]
                    return
            try:
                run_bulk(store, settings_factory(), job, runner, halt)
            except Exception:  # noqa: BLE001 - never the end of the database's bulk approvals here
                logging.getLogger(__name__).exception("bulk approval %s stopped", job["id"])
    finally:  # (whatever ends this thread: never the database left marked as approved by it)
        with _HERE_LOCK:
            if _HERE.get(key, (None,))[0] is me:
                del _HERE[key]


def resume_bulk(store: Any, settings_factory: Callable[[], Any]) -> None:
    """A laptop's dashboard starting: the bulk approvals its last run left (it stopped meanwhile) go on, here."""
    store.release_left_bulk_jobs()
    approve_here(store, settings_factory)  # (none waiting: its thread ends at once)
