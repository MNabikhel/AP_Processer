"""The long downloads on the company server, made by its background process (D-169), not by the web worker asked.

Search to Excel took 9-12 s at 100,000 invoices, the Activity CSV 11-16 s, the duplicate audit's first 11-15 s (a
year's controls report 15 s at 300,000, D-206): each held its web worker's one running Python thread (the GIL) that
long, and the light pages beside it in that worker waited. On the server each of ``KINDS`` is a job instead
(``core/store/reports.py``):

* The page's link asks for it with the page's header (``X-AP-Coder``, ``app.js``): the web worker reads what the file
  is of (the page's filters, the person's own default layout) and answers at once with the job, its status and its
  file's address; the page says how far it got (``GET /downloads/<id>``) and downloads it once made, from the
  download's own address with ``job=<id>`` (so the access log and Activity's "Downloaded" name it as before). The
  same link asked again while it waits, from any web worker, is the same job.
* Asked for without that header (a bookmark, a script), the web worker waits for the job without holding a thread,
  then sends the file.
* The background process (``Runner``, started by ``app.start_background_work``) makes ``RUNNERS`` at a time, oldest
  first, a file of their own each, written beside and renamed once whole. One its process stopped making is made
  again by the next; a full disk ends the job with why, nothing half written left.
* A job and its file are its owner's alone (another person: "no such download"), kept ``KEEP`` seconds, the latest
  ``KEPT_EACH`` of each person; a person has at most ``AT_ONCE`` waiting or being made.

On a laptop each is made as it is asked for, as before.
"""

from __future__ import annotations

import contextlib
import errno
import importlib
import logging
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import anyio
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from ap_coder.core import ui
from ap_coder.core.store import reports
from ap_coder.web import identity
from ap_coder.web.core import data_dir, get_store, page_router, refuse, server_mode
from ap_coder.web.files import XLSX, attachment

router = page_router()  # each plain handler: one connection a request

Step = Callable[[int, int, str], None]  # how far a file got: (done, of how many: 0 unknown, what it is doing)


@dataclass(frozen=True)
class Kind:
    """A download made in the background: its page module, which has ``params(request)`` (what the file is of, as
    JSON-ready data, and its name) and ``write(store, params, out, step)`` (the file written to ``out``)."""

    module: str
    params: str
    write: str
    media: str
    suffix: str


# By the download's own address (its GET route, whose role rule says who may ask for it).
KINDS = {
    "/search/export.xlsx": Kind("search", "export_params", "write_xlsx", XLSX, ".xlsx"),
    "/search/export.csv": Kind("search", "export_params", "write_csv", "text/csv", ".csv"),
    "/activity/events.csv": Kind("activity", "csv_params", "write_csv", "text/csv; charset=utf-8", ".csv"),
    "/exports/duplicates.csv": Kind("exports", "duplicates_params", "write_duplicates", "text/csv", ".csv"),
    "/month-end/accruals.csv": Kind("month_end", "accruals_params", "write_accruals", "text/csv", ".csv"),
    "/activity/controls-report": Kind(
        "activity", "controls_params", "write_controls", "text/html; charset=utf-8", ".html"
    ),
}
FORMAT = "downloads-1"  # what a job's params are (another: a job an older AP Coder left fails, asked again)
AT_ONCE = 3  # a person's downloads waiting or being made at once
KEEP = 3600.0  # seconds a file made is kept
KEPT_EACH = 5  # files kept of each person, the latest
RUNNERS = 2  # downloads the background process makes at once
POLL = 0.5  # seconds between two looks for a job waiting (the background process)
LOOK = 0.25  # seconds at most between two looks at a job its web worker waits for (one indexed read)
RENEW = 1.0  # seconds between two notes of how far a job got
STALE = 60.0  # seconds without a note: the background process making it stopped, the next one makes it again
SWEEP = 60.0  # seconds between two looks for files no longer kept
WAIT_MOST = 1800.0  # seconds a download asked without the page's header is waited for
FULL = ("The server's disk is too full to make this file. Tell IT; nothing was changed. Ask for it again once there "
        "is room.")  # fmt: skip
NOT_MADE = "The file could not be made: {why}. Ask for it again; if it happens again, tell IT."
TOO_MANY = f"You have {AT_ONCE} files being made already. Wait for one of them to be ready, then ask again."


def folder(store: Any) -> Path:
    """Where the downloads made are kept: in the data folder, not backed up (a restore empties it)."""
    return data_dir(store) / ".cache" / "downloads" / "made"


def _owner() -> str:
    me = identity.person()
    return f"user:{me.user_id}" if me is not None else ""


def _module(kind: Kind) -> Any:
    return importlib.import_module(f"ap_coder.web.pages.{kind.module}")


# --- In a web worker ----------------------------------------------------------------------------------------------


async def answer(request: Request, address: str, inline: Callable[[Request], Response]) -> Response:
    """The download at ``address`` (``KINDS``): on a laptop ``inline(request)`` (made now, on a thread, as any page);
    on the company server by the background process: the job (to the page's own script, which follows it), or the
    file once made (waited for, holding no thread)."""
    if not server_mode(request):
        return await run_in_threadpool(_inline, inline, request)
    asked = request.query_params.get("job", "")
    if asked:  # the file of a job of the person's own (``_made`` checks it is): waited for while it is made
        if not asked.isdigit() or len(asked) > 18:
            raise HTTPException(status_code=404, detail="No such download.")
        started: int | Response = int(asked)
    else:
        started = await run_in_threadpool(_start, request, address)
    if isinstance(started, Response):
        return started
    if request.headers.get("x-ap-coder") and not asked:  # the page's script: it follows the job, then downloads
        query = "&".join(part for part in [request.url.query, f"job={started}"] if part)
        return JSONResponse({"ok": True, "job": started, "status": f"/downloads/{started}",
                             "file": f"{address}?{query}"}, headers={"Cache-Control": "no-store"})  # fmt: skip
    ends, wait = time.monotonic() + WAIT_MOST, 0.05
    while time.monotonic() < ends:
        found = await run_in_threadpool(_made, request, started)
        if isinstance(found, Response):
            return found
        if found is not None:
            return _file(*found)
        if await request.is_disconnected():
            break
        await anyio.sleep(wait)
        wait = min(LOOK, wait * 1.5)
    raise HTTPException(status_code=503, detail="The file is still being made. Ask for it again in a few minutes.")


def _inline(inline: Callable[[Request], Response], request: Request) -> Response:
    with get_store(request).session():  # (one connection for the request, as a plain handler's)
        return inline(request)


def _start(request: Request, address: str) -> int | Response:
    kind = KINDS[address]
    store = get_store(request)
    with store.session():
        params, name = getattr(_module(kind), kind.params)(request)
        job, _ = store.start_download(_owner(), address, params, name, AT_ONCE, FORMAT)
    if job is None:
        return refuse(TOO_MANY, status=429)
    if _alone(request):
        _make_here(store)
    return job


def _alone(request: Request) -> bool:
    """Whether this is a server of one process without its background work (a first test, a bench): it makes its
    downloads itself (``_make_here``)."""
    state = request.app.state
    return getattr(state, "processes", 1) == 1 and getattr(state, "downloads", None) is None


def _left(request: Request, job: dict[str, Any]) -> None:
    """``job`` made here, on a server of one process without its background work, when the process before this one
    left it waiting or stopped while making it (a restart): only a new file asked for started a maker (B-1777)."""
    if _alone(request) and (job["state"] == reports.WAITING or (
            job["state"] == reports.RUNNING and job["renewed_at"] < time.time() - STALE)):  # fmt: skip
        _make_here(get_store(request))


_HERE: dict[str, threading.Thread] = {}  # the one-process server's own maker of each database, while jobs wait
_HERE_LOCK = threading.Lock()


def _make_here(store: Any) -> None:
    """The jobs waiting made by a thread of this process, which ends once none is left: a server of one process
    whose background work was not started has no other to make them."""
    with _HERE_LOCK:
        if str(store.path) in _HERE:
            return  # (one is at it: it takes this job too before it ends)
        maker = _HERE[str(store.path)] = threading.Thread(target=_make_until_none, args=(store,),
                                                           name="ap-coder-downloads-here", daemon=True)  # fmt: skip
    maker.start()


def _make_until_none(store: Any) -> None:
    runner = f"{reports.OWNER}:here"
    while True:
        with _HERE_LOCK:  # (a job asked meanwhile is claimed here, or finds no maker and starts one)
            try:
                job = store.claim_download(runner, STALE, FORMAT)
            except Exception:  # noqa: BLE001 - a busy database: the next job asked starts another
                job = None
            if job is None:
                del _HERE[str(store.path)]
                break
        make(store, job, runner)
    with contextlib.suppress(Exception):
        sweep(store)


def _file(path: Path, kind: Kind, name: str) -> FileResponse:
    return FileResponse(path, media_type=kind.media, headers={"Content-Disposition": attachment(name),
                                                              "Cache-Control": "no-store"})  # fmt: skip


def _made(request: Request, job_id: int) -> tuple[Path, Kind, str] | Response | None:
    """(the file, its kind, its name) once the job is made; the reason as an answer when it failed or is gone; None
    while it waits or is being made."""
    job = _own_job(request, job_id)
    if job["kind"] != request.url.path and request.url.path in KINDS:  # (another download's address)
        raise HTTPException(status_code=404, detail="No such download.")
    if job["state"] == reports.FAILED:
        return refuse(job["error"] or NOT_MADE.format(why="it stopped"), status=500)
    if job["state"] != reports.MADE:
        _left(request, job)
        return None
    path = folder(get_store(request)) / job["file"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="That file is no longer kept. Ask for it again.")
    return path, KINDS[job["kind"]], job["name"]


def _own_job(request: Request, job_id: int) -> dict[str, Any]:
    """Job ``job_id`` when it is the person's own; "no such download" otherwise (another person's is as none)."""
    job = get_store(request).download_job(job_id)
    if job is None or job["owner"] != _owner() or job["kind"] not in KINDS:
        raise HTTPException(status_code=404, detail="No such download.")
    return job


@router.get("/downloads/{job_id:int}")
def download_status(request: Request, job_id: int) -> JSONResponse:
    """How far the person's download got, as ``app.js`` follows a job: running (waiting counts), done or error."""
    job = _own_job(request, job_id)
    _left(request, job)
    state = {reports.MADE: "done", reports.FAILED: "error"}.get(job["state"], "running")
    note = job["note"]
    if job["state"] == reports.WAITING:
        note = f"Waiting for {ui.plural(job['ahead'], 'other file')} to be made first…" if job["ahead"] else note
    return JSONResponse({"state": state, "done": job["done"], "total": job["total"], "note": note,
                         "error": job["error"]}, headers={"Cache-Control": "no-store"})  # fmt: skip


# --- In the background process ------------------------------------------------------------------------------------


class Stopping(Exception):
    """The background process is stopping (the job is put back in line), or the job is no longer its own."""


class Runner:
    """The downloads waiting, made by the company server's background process (``app.start_background_work``):
    ``RUNNERS`` at a time, each on a thread of its own; files no longer kept are removed every ``SWEEP`` seconds."""

    def __init__(self, store_factory: Callable[[], Any]) -> None:
        self.store_factory = store_factory
        self._halt = threading.Event()
        self.thread = threading.Thread(target=self._run, name="ap-coder-downloads", daemon=True)

    def start(self) -> Runner:
        self.thread.start()
        return self

    def stop(self) -> None:
        """Stop: the jobs being made are put back in line for the next start (``thread`` ends once they are)."""
        self._halt.set()

    def _run(self) -> None:
        makers = [threading.Thread(target=self._make_jobs, args=(f"{reports.OWNER}:{n}",),
                                   name=f"ap-coder-download-{n}", daemon=True) for n in range(RUNNERS)]  # fmt: skip
        for maker in makers:
            maker.start()
        while not self._halt.wait(SWEEP):
            with contextlib.suppress(Exception):  # a busy database: next time
                sweep(self.store_factory())
        for maker in makers:
            maker.join()

    def _make_jobs(self, runner: str) -> None:
        while not self._halt.is_set():
            try:
                store = self.store_factory()
                job = store.claim_download(runner, STALE, FORMAT)
            except Exception:  # noqa: BLE001 - a busy database: looked at again
                logging.getLogger(__name__).debug("no download claimed", exc_info=True)
                job = None
            if job is None:
                self._halt.wait(POLL)
                continue
            make(store, job, runner, self._halt)


def make(store: Any, job: dict[str, Any], runner: str, halt: threading.Event | None = None) -> None:
    """Make ``job`` (claimed by ``runner``): its file written beside the others under a name of its own, renamed
    once whole, and the job ended made or failed with why; put back in line when ``halt`` is set meanwhile."""
    log = logging.getLogger(__name__)
    kind = KINDS.get(job["kind"])
    halt = halt or threading.Event()
    if kind is None:
        store.fail_download(job["id"], runner, NOT_MADE.format(why="this kind of file is no longer made"))
        return
    place = folder(store)
    token = secrets.token_hex(8)
    part, made = place / f"{job['id']}.{token}.part", place / f"{job['id']}.{token}{kind.suffix}"
    progress = {"done": 0, "total": 0, "note": "Starting…"}
    lost, finished = threading.Event(), threading.Event()
    kept = False  # whether the file made is the job's, recorded so (otherwise it is removed)

    def renew() -> None:
        while not finished.wait(RENEW):
            with contextlib.suppress(OSError):  # (its time kept new: the sweep leaves a part being written, B-1773)
                now = time.time()
                os.utime(part, (now, now))
            try:
                if not store.renew_download(job["id"], runner, progress["done"], progress["total"], progress["note"]):
                    lost.set()  # taken again by another (it seemed stopped), or gone
            except Exception:  # noqa: BLE001 - a busy database: renewed next time
                pass

    def step(done: int, total: int = 0, note: str = "") -> None:
        if halt.is_set() or lost.is_set():
            raise Stopping
        progress.update(done=done, total=total, note=note or progress["note"])

    threading.Thread(target=renew, name=f"ap-coder-download-{job['id']}-renew", daemon=True).start()
    try:
        place.mkdir(parents=True, exist_ok=True)
        if not _room(place):
            store.fail_download(job["id"], runner, FULL)
            return
        writer = getattr(_module(kind), kind.write)
        with open(part, "wb") as out, store.session():
            writer(store, job["params"], out, step)
        part.replace(made)
        kept = store.finish_download(job["id"], runner, made.name, made.stat().st_size)
    except Stopping:
        if halt.is_set():
            store.release_download(job["id"], runner)  # made again by the next start
    except OSError as exc:
        why = FULL if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)) else (
            NOT_MADE.format(why=f"it could not be written ({exc.strerror or exc})"))  # fmt: skip
        log.warning("download %s (%s) not made: %s", job["id"], job["kind"], exc)
        _failed(store, job, runner, why)
    except Exception as exc:  # noqa: BLE001 - the person is told, never left waiting
        log.exception("download %s (%s) not made", job["id"], job["kind"])
        _failed(store, job, runner, FULL if "disk is full" in str(exc) else NOT_MADE.format(why=str(exc) or "error"))
    finally:
        finished.set()
        part.unlink(missing_ok=True)
        if not kept:  # (no longer this runner's job, or its end not recorded: no job names it, B-1774)
            made.unlink(missing_ok=True)


def _failed(store: Any, job: dict[str, Any], runner: str, why: str) -> None:
    try:
        store.fail_download(job["id"], runner, why)
    except Exception:  # noqa: BLE001 - its row then ends as a stopped process's would
        logging.getLogger(__name__).warning("download %s: its failure was not recorded", job["id"], exc_info=True)


def _room(place: Path) -> bool:
    """Whether the disk keeps ``backups.BACKUP_RESERVE`` free for the database and its backups (D-160's rule)."""
    from ap_coder.core.store.backups import BACKUP_RESERVE

    try:
        return shutil.disk_usage(place).free >= BACKUP_RESERVE
    except OSError:
        return True  # unknown: tried, and a full disk then says so


def sweep(store: Any) -> None:
    """Remove the files of the jobs no longer kept (``KEEP``, ``KEPT_EACH``) and of a database restored since, the
    files left by a process that stopped while writing (``STALE``: a job being made keeps its file's time new), and,
    once ``KEEP`` old, any file no job names (its process stopped before the job was recorded made; one Windows
    refused to remove while it was sent)."""
    place = folder(store)
    gone = store.sweep_downloads(KEEP, KEPT_EACH)
    for name in gone:
        with contextlib.suppress(OSError):  # one being downloaded on Windows: no job names it, removed later
            (place / name).unlink(missing_ok=True)
    named, now = store.download_files(), time.time()
    with contextlib.suppress(OSError):  # (no folder yet)
        for path in list(place.iterdir()):
            writing = path.suffix == ".part"
            with contextlib.suppress(OSError):  # (one open on Windows: the next time)
                if (writing or path.name not in named) and now - path.stat().st_mtime > (STALE if writing else KEEP):
                    path.unlink(missing_ok=True)


def written(write: Callable[[Any, dict[str, Any], BinaryIO, Step], None], store: Any, params: dict[str, Any]) -> bytes:
    """What ``write`` makes, as bytes: a laptop's download, made as it is asked for."""
    import io

    out = io.BytesIO()
    write(store, params, out, lambda done, total=0, note="": None)
    return out.getvalue()
