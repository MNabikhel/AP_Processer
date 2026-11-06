"""The dashboard's web app: ``create_app`` builds it, ``ap_coder/web/server.py`` runs it (``python -m ap_coder serve``).

Every page is a module of ``ap_coder/web/pages`` listed in ``pages.PAGES`` (or ``pages.MORE_MODULES``). Besides the
pages:

* ``GET /healthz``: {"ok", "app", "version", "build", "folder", "pid", "port"} for the launcher (no secrets); to this
  computer, also "user" (the account running it); to another computer, or past the server's own console, only
  {"ok", "app"}.
* ``POST /shutdown``: stops the server cleanly; only from this computer, with the token ``serve`` wrote to the
  data folder's ``.ap_coder_server.json`` (header ``X-AP-Coder-Token``).
* ``GET /jobs/<name>``: how far a background job got (``ap_coder/web/jobs.py``).
* ``/static`` (this package's CSS and scripts), ``/fonts`` (Inter, the icons), ``/assets`` (logo): from memory
  (``StaticFromMemory``, D-161).
"""

from __future__ import annotations

import contextlib
import email.utils
import getpass
import hmac
import importlib
import io
import logging
import os
import secrets
import threading
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ap_coder import __version__
from ap_coder.core import envfile, paths
from ap_coder.core.config import Settings, process_var
from ap_coder.core.store import Store, default_db_path
from ap_coder.ops import offline
from ap_coder.web import core, identity, roles
from ap_coder.web.errors import FriendlyErrors
from ap_coder.web.identity import THIS_COMPUTER, Identity, from_this_computer, is_this_computer
from ap_coder.web.jobs import Jobs
from ap_coder.web.pages import MORE_MODULES, PAGES
from ap_coder.web.security import TOKEN_HEADER, AccessLog, LocalOnly, SecurityHeaders, allowed_hosts

CODE_FOLDER = core.WEB_DIR.parent.parent  # the folder holding ap_coder/ (the launcher checks it is its own)


def os_user() -> str:
    """The computer account running this server ("" when it can't be told): two Windows accounts may share one AP
    Coder folder and data folder, and the launcher opens only its own person's (scripts/running.py)."""
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name available (unusual service accounts)
        return ""


class CheckedStaticFiles(StaticFiles):
    """The dashboard's scripts and styles at their plain addresses (a page an older AP Coder drew, a bookmark), which
    the browser checks again on every use (``Cache-Control: no-cache``): left to guess, a browser keeps running its
    old copy for weeks after an update. An unchanged file answers 304 to its ETag, so the check costs nothing. The
    pages link them at their kept addresses (``core.static_url``), answered from memory (``StaticFromMemory``)."""

    async def get_response(self, path: str, scope: Any) -> Any:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = core.CHECKED
        return response


class StaticFromMemory:
    """ASGI middleware: a GET or HEAD of a static file answered from memory (``core.read_static``), on the event loop:
    no thread, no session (D-161). StaticFiles read each on one of a web worker's few threads, behind the pages. It
    sits after the Host check, with the security headers and the access log, before the session, the errors page and
    the backup check. At its plain address, the same status, headers and 304s as StaticFiles; at its kept address
    (``core.static_url``), kept a year; at another build's, as it is now, checked again on every use. What memory
    doesn't answer (a POST, a file that isn't there, a Range: the file at its plain address) goes on to the StaticFiles
    mounts."""

    def __init__(self, app: Any, static: core.Static | None = None) -> None:
        self.app, self.static = app, core.read_static() if static is None else static

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        found = self.static.find(scope["path"]) if scope["type"] == "http" else None
        if found is None or scope["method"] not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        plain, found = found
        asked: dict[bytes, bytes] = {}
        for name, value in scope["headers"]:
            asked.setdefault(name.lower(), value)  # (the first of a header given twice, as Starlette reads it)
        if b"range" in asked:
            await self.app({**scope, "path": plain}, receive, send)
            return
        if _not_modified(found, asked):
            await send({"type": "http.response.start", "status": 304, "headers": list(found.same)})
            await send({"type": "http.response.body", "body": b""})
            return
        await send({"type": "http.response.start", "status": 200, "headers": list(found.headers)})
        await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else found.body})


def _not_modified(found: core.StaticFile, asked: dict[bytes, bytes]) -> bool:
    """As StaticFiles: If-None-Match names the ETag (or is *); without it, If-Modified-Since is not before the file's
    time."""
    if match := asked.get(b"if-none-match", b"").decode("latin-1"):
        return match.strip() == "*" or found.etag in [tag.strip().removeprefix("W/") for tag in match.split(",")]
    since = email.utils.parsedate(asked.get(b"if-modified-since", b"").decode("latin-1"))
    return since is not None and since >= email.utils.parsedate(found.modified)


class DailyBackup:
    """The automatic backup while the dashboard keeps running (B-660): the start makes the day's backup (``start``,
    in the lifespan); after that, a request at least ``every`` seconds after the last check checks again. Each runs on
    a thread of its own, so neither the start nor a page waits for it: the first one after an update copies every
    invoice file, minutes to a slow network share (B-1553). ``auto_backup`` keeps its rules: once a day, the newest
    14 kept. ``now``: one asked for on Settings, on the same thread (never two at once)."""

    def __init__(self, store: Store, every: float = 3600) -> None:
        self.store, self.every = store, every
        self.last = time.monotonic()  # the start's own backup is ``start``'s
        self.thread: threading.Thread | None = None
        self.outcome: Path | Exception | None = None  # what the last one asked for (``now``) gave
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            self._spawn(self._run)

    def check(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self.last < self.every or (self.thread is not None and self.thread.is_alive()):
                return
            self.last = now
            self._spawn(self._run)

    def now(self, label: str = "manual", actor: str | None = None) -> threading.Thread | None:
        """``Store.backup_now(label)`` on the backups' thread (its result in ``outcome``); None while one runs."""
        with self._lock:
            if self.thread is not None and self.thread.is_alive():
                return None

            def made() -> None:
                try:
                    self.outcome = self.store.backup_now(label, actor=actor)
                except Exception as exc:  # noqa: BLE001 - said on the page (a busy database too: B-1564), never a 500
                    self.outcome = exc if isinstance(exc, OSError) else OSError(str(exc))

            self.outcome = None
            return self._spawn(made)

    def _spawn(self, target: Callable[[], None]) -> threading.Thread:
        self.thread = threading.Thread(target=target, name="ap-coder-daily-backup", daemon=True)
        self.thread.start()
        return self.thread

    def _run(self) -> None:
        from ap_coder.core.store.backups import BACKUP_HOURS_ENV, backup_window

        try:
            self.store.auto_backup(window=backup_window(os.environ.get(BACKUP_HOURS_ENV)))
        except Exception:  # noqa: BLE001 - a backup problem must never reach a page
            logging.getLogger(__name__).warning("the daily backup was not made", exc_info=True)


class BackupCheck:
    """ASGI middleware: each HTTP request lets ``DailyBackup`` check whether a day has passed, and the finance hub's
    schedule (``erp/hub/run.py`` ``HubSchedule``) whether a run is due (each returns at once)."""

    def __init__(self, app: Any, backup: DailyBackup, also: tuple[Any, ...] = ()) -> None:
        self.app, self.backup, self.also = app, backup, also

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            self.backup.check()
            for scheduled in self.also:
                scheduled.check()
        await self.app(scope, receive, send)


# How many requests a web worker of the company server runs at once (D-153): each one under way holds what it read
# (about 110 MB on average), and one Python process works on one at a time anyway (the GIL), so anyio's 40 threads
# multiplied the memory, not the pages answered (16 people at once: 1.9-2.15 GB a worker with 40, 1.33 GB with 4, the
# same work done). The others wait their turn. A laptop keeps anyio's default.
THREADS_ENV = "AP_WORKER_THREADS"
WORKER_THREADS = 4


def worker_threads() -> int:
    """AP_WORKER_THREADS (server.json's ``worker_threads``), else WORKER_THREADS; from 1 to 40."""
    try:
        return min(40, max(1, int((os.environ.get(THREADS_ENV) or "").strip() or WORKER_THREADS)))
    except ValueError:
        return WORKER_THREADS  # a typo never stops the server


def default_store() -> Store:
    """The database the dashboard uses: AP_DB_PATH, else the data folder's ap_coder.db."""
    return Store(paths.env_path("AP_DB_PATH") or default_db_path())


class EnvSettings:
    """The settings from the .env and the environment, read again only when either changed (the .env's time and
    size, the environment's values): every request asks for them, and the Settings page saves to the .env.

    A value the environment took from the .env (when AP Coder started, or saved here since) gives way to the .env's
    new one: with several web workers (D-111) another worker saved it there, and only its own environment had it
    (B-1404). A value the environment was given otherwise (the service's own) still wins, as ``Settings.from_env``
    has it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kept: tuple[Any, Settings] | None = None
        self._file: tuple[tuple[Any, ...], dict[str, str]] | None = None  # the .env's stamp and values, last read

    @staticmethod
    def _stamp() -> tuple[Any, ...]:
        chosen = os.environ.get("AP_ENV_FILE")
        path = Path(chosen) if chosen else paths.env_file()
        try:
            seen = path.stat() if path is not None else None
        except OSError:
            seen = None
        file = (str(path), seen.st_mtime_ns, seen.st_size) if seen is not None else (str(path), None, None)
        return (*file, frozenset(os.environ.items()))

    def _follow_the_file(self) -> None:
        """Take out of the environment each value the .env gave it that the .env no longer has (B-1404)."""
        stamp = self._stamp()[:3]
        if self._file is not None and self._file[0] == stamp:
            return
        path = Path(stamp[0]) if stamp[1] is not None else None
        values = dotenv_values(stream=io.StringIO(envfile.read_text(path)), interpolate=False) if path else {}
        now = {name: value for name, value in values.items() if value is not None and not process_var(name)}
        for name, value in (self._file[1] if self._file is not None else {}).items():
            if os.environ.get(name) == value and now.get(name) != value:
                del os.environ[name]  # Settings.from_env puts the .env's new value in its place
        self._file = (stamp, now)

    def __call__(self) -> Settings:
        with self._lock:
            if self._kept is not None and self._kept[0] == self._stamp():
                return self._kept[1]
            self._follow_the_file()
            read = Settings.from_env(os.environ.get("AP_ENV_FILE"))
            self._kept = (self._stamp(), read)  # after reading: the .env's values are in the environment now
            return read


def own_work_running(state: Any) -> bool:
    """Whether something this process runs on a thread of its own would end with it: a job, a backup (a web worker
    of the server waits for them before it ends: ``server.Recycle``). Imports waiting their turn are in the database
    (D-168): nothing of theirs ends with it."""
    backup = getattr(state.daily_backup, "thread", None)
    return state.jobs.running_here() or (backup is not None and backup.is_alive())


def start_background_work(settings_factory: Callable[[], Settings], store: Store) -> tuple[Any, Any, Any, Any]:
    """The work the dashboard does on its own: the page reader and the AI reviewer (``page_worker``), the optional
    Azure check, the JD Edwards API's nightly sync and voucher queue, and the review queue checked again after a
    change of settings. In the dashboard's own process (the laptop), or, with several web workers, in the server's
    background process only (``server.run_background``, D-111)."""
    from ap_coder.ai import azure_check, page_worker
    from ap_coder.coding.bulk import RECHECK_LEASE, QueueRecheck
    from ap_coder.erp import jde_sync

    reader = page_worker.start_background(settings_factory, lambda: store, cache_dir=core.cache_dir(store))
    # The optional Azure check (Settings → Reading): it does nothing while it is off, the default.
    azure = azure_check.start_background(settings_factory, lambda: store)
    # The JD Edwards API's nightly sync and voucher queue (D-104): nothing while both its steps are off.
    jde = jde_sync.Background(lambda: store).start()
    # The queue's checks against today's settings (D-155): a few small queries every few seconds while none changed.
    # A laptop's own re-check lease left is its last start's, stopped as it checked: the queue would wait for it.
    if not offline.server_mode():
        store.drop_lease(RECHECK_LEASE, None)
    recheck = QueueRecheck(settings_factory, lambda: store).start()
    return reader, azure, jde, recheck


def create_app(settings: Settings | None = None, store: Store | None = None, *, address: str = "127.0.0.1",
               start_reader: bool = True, server: bool | None = None, insecure_http: bool = False,
               background: bool = True, processes: int = 1, secret: bytes | None = None) -> FastAPI:  # fmt: skip
    """The dashboard. ``settings``: fixed settings (tests); by default the .env is read again for every page, so a
    change on the Settings page applies at once. ``store``: the database (default: ``default_store``). ``address``:
    what the server listens on (the Host check allows it). ``start_reader``: start the page reader's background
    thread (OvisOCR2) when the app starts (tests replace ``page_worker.start_background`` with a no-op).
    ``server``: server mode, people log in and their role says what they may do (D-97; default ``AP_SERVER_MODE``).
    ``insecure_http``: the server's ``--insecure-http`` (a first test): its session cookie is then not Secure and it
    sends no HSTS; otherwise both, whatever scheme a request reports (D-101). ``background``: this process does the
    background work (the reader, the daily backup, the hub's schedule...); ``processes``: how many web workers serve
    this database (the company server, D-111), which then share their jobs and the rest through the database;
    ``secret``: the key those workers share (else one made at this start)."""
    store = store or default_store()
    server = offline.server_mode() if server is None else bool(server)
    if server:  # how long a login lasts: server.json's, or 30 minutes unused and 12 hours (D-109)
        from ap_coder.ops.service import session_limits

        store.session_idle, store.session_absolute = session_limits(paths.private_dir())
    settings_factory: Callable[[], Settings] = (lambda: settings) if settings is not None else EnvSettings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if server:  # this event loop's thread pool: the plain handlers, sessions looked up, files sent
            anyio.to_thread.current_default_thread_limiter().total_tokens = worker_threads()
        if background:  # once a day, when the dashboard starts, on its own thread: it never holds up the start
            app.state.daily_backup.start()
        if start_reader and background:
            (app.state.reader, app.state.azure_checker, app.state.jde_worker,
             app.state.queue_recheck) = start_background_work(settings_factory, store)  # fmt: skip
        if background and app.state.processes == 1:  # files left in the import queue by the last run (D-168)
            from ap_coder.web.pages import process

            process.resume(app)
        yield
        for name in ("reader", "azure_checker", "jde_worker", "queue_recheck"):
            worker = getattr(app.state, name, None)
            if worker is not None:
                worker.stop()
        if app.state.queue_recheck is not None:  # it ends between two batches, letting go of its lease
            from ap_coder.coding.bulk import RECHECK_STOP_SECONDS

            ends = time.monotonic() + RECHECK_STOP_SECONDS
            while app.state.queue_recheck.thread.is_alive() and time.monotonic() < ends:
                await anyio.sleep(0.05)
        # One of the server's web workers: its connections are closed now, so no request starts more. What one
        # started after Recycle looked (a JD Edwards send, a backup, imports) ends with the process: it is waited for.
        # A stop of the service still ends the worker at its deadline (server.py, join_all).
        while app.state.processes > 1 and own_work_running(app.state):
            await anyio.sleep(0.2)

    app = FastAPI(title="AP Coder", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.settings_factory = settings_factory
    app.state.processes = max(1, int(processes))  # web workers serving this database (D-111)
    app.state.jobs = Jobs(store if app.state.processes > 1 else None)
    app.state.reader = None
    app.state.azure_checker = None  # the Azure check's thread (ap_coder/ai/azure_check.py), started with the reader
    app.state.jde_worker = None  # the JD Edwards API's nightly sync and queue (ap_coder/erp/jde_sync.py), likewise
    app.state.queue_recheck = None  # the review queue checked again after a change of settings (coding/bulk.py)
    app.state.port = None  # set by server.serve
    app.state.shutdown_token = None  # set by server.serve; without it /shutdown refuses
    app.state.server = None  # the uvicorn.Server, set by server.serve
    app.state.proxy = None  # the reverse proxy's address (--proxy), set by server.serve
    app.state.insecure_http = bool(insecure_http)
    # A colleague on the office network says who they are once (web/identity.py); LocalOnly, added last, runs first.
    # Made at each start (a cookie from another start is refused); the same in every web worker of one start.
    app.state.identity_secret = secret or secrets.token_bytes(32)
    app.state.stop_file = None  # with several web workers: where /shutdown asks the server to stop (server.py)
    app.state.daily_backup = DailyBackup(store)
    from ap_coder.erp.hub.run import HubSchedule

    app.state.hub_schedule = HubSchedule(store)  # the finance hub feed, off until an Admin turns it on (D-103)
    if background:  # after the security checks: only for pages served (else the background process does it)
        app.add_middleware(BackupCheck, backup=app.state.daily_backup, also=(app.state.hub_schedule,))
    app.add_middleware(FriendlyErrors)  # an error no page expected is drawn as a page, with the menu
    app.state.server_mode = server  # accounts and roles (web/identity.py, web/roles.py)
    app.add_middleware(Identity, secret=app.state.identity_secret, address=address, server=server)
    app.add_middleware(core.StoreContext, store=store)  # messages and uploads kept in this database (D-99)
    app.add_middleware(StaticFromMemory)  # after the Host check, before the session (D-161)
    app.add_middleware(
        LocalOnly, address=address, extra=allowed_hosts(), server=server
    )  # + the server's names (AP_ALLOWED_HOSTS)
    # Outermost: a refusal carries the headers too.
    app.add_middleware(SecurityHeaders, server=server, hsts=server and not insecure_http)
    if server:  # every request in logs/access.log, and each download in the audit trail (D-109)
        app.add_middleware(AccessLog, folder=store.path.parent / "logs", store=store, shared=app.state.processes > 1)
    app.mount("/static", CheckedStaticFiles(directory=str(core.STATIC_DIR)), name="static")
    app.mount("/fonts", StaticFiles(directory=str(core.FONTS_DIR)), name="fonts")
    app.mount("/assets", StaticFiles(directory=str(core.ASSETS_DIR)), name="assets")

    # The extra modules first: their fixed paths (/settings/azure-check) are matched before a page's catch-all
    # (/settings/{tab}), which would otherwise answer them with "no such tab".
    for module in MORE_MODULES:
        app.include_router(importlib.import_module(f"ap_coder.web.pages.{module}").router)
    for page in PAGES:
        app.include_router(importlib.import_module(f"ap_coder.web.pages.{page.module}").router)

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        from ap_coder.core.store.users import MANAGER  # a Manager opens its own requests (D-138)

        me = identity.person()
        return RedirectResponse("/manager" if me and me.role == MANAGER else PAGES[0].path, status_code=307)

    build = paths.build_stamp(CODE_FOLDER)["commit"]  # the code this server runs (the launcher compares it)
    network = str(address or "").strip().strip("[]").lower() not in THIS_COMPUTER  # shared on the office network

    console_only = server  # the server: /healthz tells only its own console where it runs (D-101)

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, Any]:
        client = request.client.host if request.client else ""
        if console_only and not is_this_computer(client):
            # The server: by the client's own address (after --proxy), not the one listened on: behind IIS on the same
            # server AP Coder listens on 127.0.0.1, and everyone arrives through it (D-101).
            return {"ok": True, "app": "ap-coder"}
        if network and not from_this_computer(request.scope):
            return {"ok": True, "app": "ap-coder"}  # another computer: only that it is up (B-979)
        server = request.scope.get("server") or (None, None)
        found = {
            "ok": True,
            "app": "ap-coder",
            "version": __version__,
            "build": build,
            "folder": str(CODE_FOLDER),
            "pid": os.getpid(),
            "port": request.app.state.port or server[1],
        }
        if is_this_computer(request.client.host if request.client else ""):  # (never said to the network)
            found["user"] = os_user()  # another account's double-click must not open this one (B-168)
        return found

    @app.post("/shutdown")
    def shutdown(request: Request) -> JSONResponse:
        token = request.app.state.shutdown_token
        given = request.headers.get(TOKEN_HEADER) or ""
        if not is_this_computer(request.client.host if request.client else "") or not token:
            raise HTTPException(status_code=403, detail="AP Coder only stops from this computer.")
        if not hmac.compare_digest(given.encode(), str(token).encode()):
            raise HTTPException(status_code=403, detail="Wrong or missing X-AP-Coder-Token.")
        server = request.app.state.server
        if server is not None:
            server.should_exit = True  # uvicorn finishes this answer, runs the shutdown (the reader stops), exits
        elif request.app.state.stop_file is not None:  # one of several web workers: the server stops them all
            request.app.state.stop_file.write_text(str(token), encoding="utf-8")
        return JSONResponse({"ok": True, "stopping": True})

    @app.get("/jobs/{name}")
    def job_status(name: str, request: Request, only: str = "") -> JSONResponse:
        registry = core.jobs(request)
        me = identity.person()
        if server and me is not None and not roles.allowed(me.role, roles.JOB_NEEDS.get(name, roles.ADMIN)):
            raise HTTPException(status_code=404, detail="No such job.")  # (as if there were none: D-109)
        if name == "process":  # the imports' progress is the import queue's, the same in every web worker (D-168)
            from ap_coder.web.pages.process import import_status

            snapshot = import_status(core.get_store(request), core.get_settings(request))
        elif not registry.known(name):
            raise HTTPException(status_code=404, detail="No such job.")
        else:
            snapshot = registry.get(name).snapshot()
        if only:  # a page following the job asks for what it shows (app.js watchJob): its state and these
            snapshot = {key: value for key, value in snapshot.items() if key == "state" or key in only.split(",")}
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Any:
        wants_page = request.method == "GET" and "text/html" in request.headers.get("accept", "")
        if not wants_page:
            return JSONResponse({"ok": False, "error": str(exc.detail)}, status_code=exc.status_code)
        title = "Busy" if exc.status_code == 503 else "Not found"  # (503: the long routes' lane, core.run_heavy)
        # the page frame reads the database: on a threadpool thread, never holding up the other requests
        return await run_in_threadpool(core.render, request, "error.html", "", status_code=exc.status_code,
                                       title=title, code=exc.status_code, detail=str(exc.detail))  # fmt: skip

    @app.exception_handler(RequestValidationError)
    async def unreadable(request: Request, exc: RequestValidationError) -> Any:
        # An address or form with a value AP Coder can't read (a typed ?limit=x, a field left out): plain words, as
        # every other error, never FastAPI's raw JSON (B-1534).
        detail = "That address or form has a value AP Coder can't read. Go back and try again."
        wants_page = request.method == "GET" and "text/html" in request.headers.get("accept", "")
        if not wants_page:
            return JSONResponse({"ok": False, "error": detail}, status_code=422)
        return await run_in_threadpool(core.render, request, "error.html", "", status_code=422, title="Not understood",
                                       code=422, detail=detail)  # fmt: skip

    @app.exception_handler(OverflowError)
    async def too_large(request: Request, exc: OverflowError) -> Any:
        # A number from the address or a form beyond what SQLite keeps (2**63): no invoice or lesson has it.
        if "SQLite" not in str(exc):
            raise exc
        return await http_error(request, StarletteHTTPException(404, "Nothing in AP Coder has that number."))

    return app
