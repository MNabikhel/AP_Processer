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
import os
import secrets
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ap_coder import __version__
from ap_coder.core import paths
from ap_coder.core.config import Settings
from ap_coder.core.store import Store, default_db_path
from ap_coder.web import core
from ap_coder.web.identity import Identity, is_this_computer
from ap_coder.web.jobs import Jobs
from ap_coder.web.pages import MORE_MODULES, PAGES, Page
from ap_coder.web.security import TOKEN_HEADER, LocalOnly

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
            from ap_coder.ai import page_worker

            app.state.reader = page_worker.start_background(
                settings_factory, lambda: Store(store.path), cache_dir=core.cache_dir(store)
            )
            from ap_coder.ai import azure_check

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
    app.state.azure_checker = None  # the Azure check's thread (ap_coder/ai/azure_check.py), started with the reader
    app.state.port = None  # set by server.serve
    app.state.shutdown_token = None  # set by server.serve; without it /shutdown refuses
    app.state.server = None  # the uvicorn.Server, set by server.serve
    # A colleague on the office network says who they are once (web/identity.py); LocalOnly, added last, runs first.
    app.state.identity_secret = secrets.token_bytes(32)  # made at each start: a cookie from another start is refused
    app.add_middleware(Identity, secret=app.state.identity_secret, address=address)
    app.add_middleware(LocalOnly, address=address)
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

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, Any]:
        server = request.scope.get("server") or (None, None)
        found = {
            "ok": True,
            "app": "ap-coder",
            "version": __version__,
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
