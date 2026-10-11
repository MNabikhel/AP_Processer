"""The dashboard's web app: ``create_app`` builds it, ``ap_coder/web/server.py`` runs it (``python -m ap_coder serve``).

Every page is a module of ``ap_coder/web/pages`` listed in ``pages.PAGES``; pages not built yet answer with the
placeholder template. Besides the pages:

* ``GET /healthz``: {"ok", "app", "version", "folder", "pid", "port"} for the launcher (no secrets).
* ``POST /shutdown``: stops the server cleanly; only from this computer, with the token ``serve`` wrote to the
  data folder's ``.ap_coder_server.json`` (header ``X-AP-Coder-Token``).
* ``GET /jobs/<name>``: how far a background job got (``ap_coder/web/jobs.py``).
* ``/static`` (this package's CSS and scripts), ``/fonts`` (Inter, the icons), ``/assets`` (logo).
"""

from __future__ import annotations

import contextlib
import hmac
import importlib
import ipaddress
import os
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ap_coder import __version__, paths
from ap_coder.config import Settings
from ap_coder.store import Store, default_db_path
from ap_coder.web import core
from ap_coder.web.jobs import Jobs
from ap_coder.web.pages import MORE_MODULES, PAGES, Page
from ap_coder.web.security import TOKEN_HEADER, LocalOnly

CODE_FOLDER = core.WEB_DIR.parent.parent  # the folder holding ap_coder/ (the launcher checks it is its own)


def default_store() -> Store:
    """The database the dashboard uses: AP_DB_PATH, else the data folder's ap_coder.db."""
    return Store(paths.env_path("AP_DB_PATH") or default_db_path())


def create_app(settings: Settings | None = None, store: Store | None = None, *, address: str = "127.0.0.1",
               start_reader: bool = True) -> FastAPI:  # fmt: skip
    """The dashboard. ``settings``: fixed settings (tests); by default the .env is read again for every page, so a
    change on the Settings page applies at once. ``store``: the database (default: ``default_store``). ``address``:
    what the server listens on (the Host check allows it). ``start_reader``: start the page reader's background
    thread (OvisOCR2) when the app starts (tests replace ``page_worker.start_background`` with a no-op)."""
    store = store or default_store()
    settings_factory: Callable[[], Settings] = (
        (lambda: settings) if settings is not None else (lambda: Settings.from_env(os.environ.get("AP_ENV_FILE")))
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            store.auto_backup()  # once a day, when the dashboard starts; the newest 14 are kept
        except OSError:
            pass  # a backup problem must never stop the dashboard from opening
        if start_reader:
            from ap_coder import page_worker

            app.state.reader = page_worker.start_background(
                settings_factory, lambda: Store(store.path), cache_dir=core.cache_dir(store)
            )
            from ap_coder import azure_check

            # The optional Azure check (Settings → Reading): it does nothing while it is off, the default.
            app.state.azure_checker = azure_check.start_background(settings_factory, lambda: Store(store.path))
        yield
        for name in ("reader", "azure_checker"):
            worker = getattr(app.state, name, None)
            if worker is not None:
                worker.stop()

    app = FastAPI(title="AP Coder", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.settings_factory = settings_factory
    app.state.jobs = Jobs()
    app.state.reader = None
    app.state.azure_checker = None  # the Azure check's thread (ap_coder/azure_check.py), started with the reader
    app.state.port = None  # set by server.serve
    app.state.shutdown_token = None  # set by server.serve; without it /shutdown refuses
    app.state.server = None  # the uvicorn.Server, set by server.serve
    app.add_middleware(LocalOnly, address=address)
    app.mount("/static", StaticFiles(directory=str(core.STATIC_DIR)), name="static")
    app.mount("/fonts", StaticFiles(directory=str(core.FONTS_DIR)), name="fonts")
    app.mount("/assets", StaticFiles(directory=str(core.ASSETS_DIR)), name="assets")

    for page in PAGES:
        if page.module:
            app.include_router(importlib.import_module(f"ap_coder.web.pages.{page.module}").router)
        else:
            app.add_api_route(page.path, _placeholder(page), methods=["GET"], response_class=HTMLResponse)
    for module in MORE_MODULES:
        app.include_router(importlib.import_module(f"ap_coder.web.pages.{module}").router)

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse(PAGES[0].path, status_code=307)

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, Any]:
        server = request.scope.get("server") or (None, None)
        return {
            "ok": True,
            "app": "ap-coder",
            "version": __version__,
            "folder": str(CODE_FOLDER),
            "pid": os.getpid(),
            "port": request.app.state.port or server[1],
        }

    @app.post("/shutdown")
    def shutdown(request: Request) -> JSONResponse:
        token = request.app.state.shutdown_token
        given = request.headers.get(TOKEN_HEADER) or ""
        if not _loopback(request.client.host if request.client else "") or not token:
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

    return app


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _placeholder(page: Page) -> Callable[[Request], HTMLResponse]:
    def placeholder(request: Request) -> HTMLResponse:
        return core.render(request, "placeholder.html", page.key)

    placeholder.__name__ = f"placeholder_{page.key}"
    return placeholder
