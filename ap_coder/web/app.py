"""The dashboard's web app: ``create_app`` builds it, ``ap_coder/web/server.py`` runs it (``python -m ap_coder serve``).

Every page is a module of ``ap_coder/web/pages`` listed in ``pages.PAGES``; pages not built yet answer with the
placeholder template. Besides the pages:

* ``GET /healthz``: {"ok", "app", "version", "folder", "pid", "port"} for the launcher (no secrets); to this
  computer, also "user" (the account running it).
* ``POST /shutdown``: stops the server cleanly; only from this computer, with the token ``serve`` wrote to the
  data folder's ``.ap_coder_server.json`` (header ``X-AP-Coder-Token``).
* ``GET /jobs/<name>``: how far a background job got (``ap_coder/web/jobs.py``).
* ``/static`` (this package's CSS and scripts), ``/fonts`` (Inter, the icons), ``/assets`` (logo).
"""

from __future__ import annotations

import contextlib
import getpass
import hmac
import importlib
import logging
import os
import secrets
import threading
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ap_coder import __version__
from ap_coder.core import paths
from ap_coder.core.config import Settings
from ap_coder.core.store import Store, default_db_path
from ap_coder.ops import offline
from ap_coder.web import core
from ap_coder.web.errors import FriendlyErrors
from ap_coder.web.identity import THIS_COMPUTER, Identity, from_this_computer, is_this_computer
from ap_coder.web.jobs import Jobs
from ap_coder.web.pages import MORE_MODULES, PAGES, Page
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
    """The dashboard's scripts and styles, which the browser checks again on every use (``Cache-Control: no-cache``).
    js/app.js and js/invoice_viewer.js are imported without ``?v=`` (one URL, one module): left to guess, a browser
    keeps running its old copy for weeks after an update. An unchanged file answers 304 to its ETag, so the check
    costs nothing."""

    async def get_response(self, path: str, scope: Any) -> Any:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class DailyBackup:
    """The automatic backup while the dashboard keeps running (B-660): the start makes the day's backup
    (``Store.auto_backup``, in the lifespan); after that, a request at least ``every`` seconds after the last check
    checks again on a thread of its own, so no page waits for it. ``auto_backup`` keeps its rules: once a day, the
    newest 14 kept."""

    def __init__(self, store: Store, every: float = 3600) -> None:
        self.store, self.every = store, every
        self.last = time.monotonic()  # the start's own backup is the lifespan's
        self.thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def check(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self.last < self.every or (self.thread is not None and self.thread.is_alive()):
                return
            self.last = now
            self.thread = threading.Thread(target=self._run, name="ap-coder-daily-backup", daemon=True)
            self.thread.start()

    def _run(self) -> None:
        try:
            self.store.auto_backup()
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


def default_store() -> Store:
    """The database the dashboard uses: AP_DB_PATH, else the data folder's ap_coder.db."""
    return Store(paths.env_path("AP_DB_PATH") or default_db_path())


class EnvSettings:
    """The settings from the .env and the environment, read again only when either changed (the .env's time and
    size, the environment's values): every request asks for them, and the Settings page saves to the .env."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kept: tuple[Any, Settings] | None = None

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

    def __call__(self) -> Settings:
        with self._lock:
            if self._kept is not None and self._kept[0] == self._stamp():
                return self._kept[1]
            read = Settings.from_env(os.environ.get("AP_ENV_FILE"))
            self._kept = (self._stamp(), read)  # after reading: the .env's values are in the environment now
            return read


def create_app(settings: Settings | None = None, store: Store | None = None, *, address: str = "127.0.0.1",
               start_reader: bool = True, server: bool | None = None,
               insecure_http: bool = False) -> FastAPI:  # fmt: skip
    """The dashboard. ``settings``: fixed settings (tests); by default the .env is read again for every page, so a
    change on the Settings page applies at once. ``store``: the database (default: ``default_store``). ``address``:
    what the server listens on (the Host check allows it). ``start_reader``: start the page reader's background
    thread (OvisOCR2) when the app starts (tests replace ``page_worker.start_background`` with a no-op).
    ``server``: server mode, people log in and their role says what they may do (D-97; default ``AP_SERVER_MODE``).
    ``insecure_http``: the server's ``--insecure-http`` (a first test): its session cookie is then not Secure and it
    sends no HSTS; otherwise both, whatever scheme a request reports (D-101)."""
    store = store or default_store()
    server = offline.server_mode() if server is None else bool(server)
    settings_factory: Callable[[], Settings] = (lambda: settings) if settings is not None else EnvSettings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            store.auto_backup()  # once a day, when the dashboard starts (then DailyBackup); the newest 14 are kept
        except OSError:
            pass  # a backup problem must never stop the dashboard from opening
        if start_reader:
            from ap_coder.ai import page_worker

            app.state.reader = page_worker.start_background(
                settings_factory, lambda: store, cache_dir=core.cache_dir(store)
            )
            from ap_coder.ai import azure_check

            # The optional Azure check (Settings → Reading): it does nothing while it is off, the default.
            app.state.azure_checker = azure_check.start_background(settings_factory, lambda: store)
            from ap_coder.erp import jde_sync

            # The JD Edwards API's nightly sync and voucher queue (D-104): nothing while both its steps are off.
            app.state.jde_worker = jde_sync.Background(lambda: store).start()
        yield
        for name in ("reader", "azure_checker", "jde_worker"):
            worker = getattr(app.state, name, None)
            if worker is not None:
                worker.stop()

    app = FastAPI(title="AP Coder", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.settings_factory = settings_factory
    app.state.jobs = Jobs()
    app.state.reader = None
    app.state.azure_checker = None  # the Azure check's thread (ap_coder/ai/azure_check.py), started with the reader
    app.state.jde_worker = None  # the JD Edwards API's nightly sync and queue (ap_coder/erp/jde_sync.py), likewise
    app.state.port = None  # set by server.serve
    app.state.shutdown_token = None  # set by server.serve; without it /shutdown refuses
    app.state.server = None  # the uvicorn.Server, set by server.serve
    app.state.proxy = None  # the reverse proxy's address (--proxy), set by server.serve
    app.state.insecure_http = bool(insecure_http)
    # A colleague on the office network says who they are once (web/identity.py); LocalOnly, added last, runs first.
    app.state.identity_secret = secrets.token_bytes(32)  # made at each start: a cookie from another start is refused
    app.state.daily_backup = DailyBackup(store)
    from ap_coder.erp.hub.run import HubSchedule

    app.state.hub_schedule = HubSchedule(store)  # the finance hub feed, off until an Admin turns it on (D-103)
    # After the security checks: only for pages served.
    app.add_middleware(BackupCheck, backup=app.state.daily_backup, also=(app.state.hub_schedule,))
    app.add_middleware(FriendlyErrors)  # an error no page expected is drawn as a page, with the menu
    app.state.server_mode = server  # accounts and roles (web/identity.py, web/roles.py)
    app.add_middleware(Identity, secret=app.state.identity_secret, address=address, server=server)
    app.add_middleware(core.StoreContext, store=store)  # messages and uploads kept in this database (D-99)
    app.add_middleware(
        LocalOnly, address=address, extra=allowed_hosts(), server=server
    )  # + the server's names (AP_ALLOWED_HOSTS)
    # Outermost: a refusal carries the headers too.
    app.add_middleware(SecurityHeaders, server=server, hsts=server and not insecure_http)
    if server:  # every request in logs/access.log, and each download in the audit trail (D-109)
        app.add_middleware(AccessLog, folder=store.path.parent / "logs", store=store)
    app.mount("/static", CheckedStaticFiles(directory=str(core.STATIC_DIR)), name="static")
    app.mount("/fonts", StaticFiles(directory=str(core.FONTS_DIR)), name="fonts")
    app.mount("/assets", StaticFiles(directory=str(core.ASSETS_DIR)), name="assets")

    # The extra modules first: their fixed paths (/settings/azure-check) are matched before a page's catch-all
    # (/settings/{tab}), which would otherwise answer them with "no such tab".
    for module in MORE_MODULES:
        app.include_router(importlib.import_module(f"ap_coder.web.pages.{module}").router)
    for page in PAGES:
        if page.module:
            app.include_router(importlib.import_module(f"ap_coder.web.pages.{page.module}").router)
        else:
            app.add_api_route(page.path, _placeholder(page), methods=["GET"], response_class=HTMLResponse)

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse(PAGES[0].path, status_code=307)

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
        return JSONResponse({"ok": True, "stopping": True})

    @app.get("/jobs/{name}")
    def job_status(name: str, request: Request) -> JSONResponse:
        registry = core.jobs(request)
        if not registry.known(name):
            raise HTTPException(status_code=404, detail="No such job.")
        return JSONResponse(registry.get(name).snapshot(), headers={"Cache-Control": "no-store"})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Any:
        wants_page = request.method == "GET" and "text/html" in request.headers.get("accept", "")
        if not wants_page:
            return JSONResponse({"ok": False, "error": str(exc.detail)}, status_code=exc.status_code)
        return core.render(request, "error.html", "", status_code=exc.status_code, title="Not found",
                           code=exc.status_code, detail=str(exc.detail))  # fmt: skip

    @app.exception_handler(OverflowError)
    async def too_large(request: Request, exc: OverflowError) -> Any:
        # A number from the address or a form beyond what SQLite keeps (2**63): no invoice or lesson has it.
        if "SQLite" not in str(exc):
            raise exc
        return await http_error(request, StarletteHTTPException(404, "Nothing in AP Coder has that number."))

    return app


def _placeholder(page: Page) -> Callable[[Request], HTMLResponse]:
    def placeholder(request: Request) -> HTMLResponse:
        return core.render(request, "placeholder.html", page.key)

    placeholder.__name__ = f"placeholder_{page.key}"
    return placeholder
