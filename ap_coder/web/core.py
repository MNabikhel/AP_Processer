"""What every page of the dashboard shares: templates, the data, the rail, messages after an action.

A page module uses these::

    store, settings = get_store(request), get_settings(request)
    return render(request, "vendors.html", "vendors", vendors=store.vendor_summaries())

    # after a POST from the page (static/js/app.js sends it and follows the answer):
    return done(request, "/vendors", "Vendor saved.")             # go there, with a message
    return message("Copied.")                                     # stay, show a message
    return refuse("That file is not a CSV.")                      # stay, show the problem

``can(request, path)`` says whether the person asking may use a route (always on a laptop; their role's say on the
server), ``reviewer()`` / ``login()`` who they are, ``current_store()`` the database of the request (where messages
and uploads wait, D-99).

Templates are escaped automatically (Jinja autoescape). The ``ui`` global gives the HTML helpers of
``ap_coder/core/ui.py`` (they escape what they are given), e.g. ``{{ ui.pill("Ready", "ok") }}``. Never mark text from
an invoice or a file as safe yourself.
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import datetime as dt
import email.utils
import functools
import getpass
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import anyio
import anyio.to_thread
import jinja2
from anyio.lowlevel import RunVar
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

from ap_coder.core import paths, ui
from ap_coder.core.config import Settings
from ap_coder.core.reference_data import UNASSIGNED, ReferenceData
from ap_coder.core.store import REVIEW, Store, reports
from ap_coder.ops import offline
from ap_coder.web import identity
from ap_coder.web.access import is_admin
from ap_coder.web.jobs import Jobs
from ap_coder.web.pages import BANNER_PAGES, BY_KEY, PAGES, PINNED, SIMPLE_KEYS, SIMPLE_MAIN, SIMPLE_SETUP

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
FONTS_DIR = WEB_DIR.parent / "static"  # Inter and the Material Symbols icons
ASSETS_DIR = WEB_DIR.parent / "assets"  # logo and icon

TARGET_ACCURACY = 0.90
EARLY_LINES = 50  # reviewed lines before the coding accuracy is held against the target


def accuracy_note(settings: Settings, lines_reviewed: int) -> tuple[str, bool]:
    """(note, compared): what to say under the coding accuracy, and whether to show its distance to the 90% target.
    Over the first lines (``EARLY_LINES``) one correction moves the figure by points, and without the chat model every
    line starts with no account, which counts as not right (D-39): a plain note then, not a red distance (B-442).
    After them the distance shows, with the note's first half while there is no chat model."""
    from ap_coder.ai import local_llm

    try:
        ready = local_llm.provider_status(settings).ready
    except Exception:  # noqa: BLE001 - a status never stops a page
        ready = False
    if lines_reviewed >= EARLY_LINES:
        return ("" if ready else "GL suggestions improve as you approve"), True
    return "GL suggestions improve as you approve" + (
        "" if ready else "; with the AI model on they start sooner"
    ), False


# --- Static files (D-161) ----------------------------------------------------------------------------------------
# The styles, scripts, fonts and logo, read once when AP Coder starts: ``app.StaticFromMemory`` answers them from
# memory on the event loop, with the headers Starlette's StaticFiles gives them (a file edited while AP Coder runs is
# answered as it was at the start). The pages link each at its kept address (``static_url``): /static/<build>/app.css,
# the build a hash of the files' contents, kept by the browser a year; an update that changes a file changes them all.
STATIC_FOLDERS = (("/static/", STATIC_DIR), ("/fonts/", FONTS_DIR), ("/assets/", ASSETS_DIR))
CHECKED = "no-cache"  # the browser checks its copy again on every use (an unchanged file answers 304)
KEPT = "public, max-age=31536000, immutable"  # a kept address: what it answers never changes
BUILT_IN = re.compile(r"(/static/|/fonts/|/assets/)([0-9a-f]{10})/(.+)")  # a kept address, this build's or another's
_LINKED = re.compile(rb"""url\((["']?)(/static/|/fonts/|/assets/)""")  # a stylesheet's link to a font or a picture


@dataclasses.dataclass(frozen=True)
class StaticFile:
    """One file's answer: its body, its headers (a 200 or a HEAD) and those of a 304, as StaticFiles has them."""

    body: bytes
    headers: tuple[tuple[bytes, bytes], ...]
    same: tuple[tuple[bytes, bytes], ...]  # a 304's: its ETag and Cache-Control
    etag: str
    modified: str  # Last-Modified


@dataclasses.dataclass(frozen=True)
class Static:
    """The static files as AP Coder read them at its start (``read_static``)."""

    build: str  # a hash of the files' contents
    plain: dict[str, StaticFile]  # by address (/static/app.css): as StaticFiles answered it
    kept: dict[str, StaticFile]  # by plain address: its answer at /static/<build>/app.css (a stylesheet names the
    # fonts at theirs)
    other: dict[str, StaticFile]  # by plain address: at another build's (a page drawn before an update), as it is now

    def find(self, path: str) -> tuple[str, StaticFile] | None:
        """(the plain address, the answer) of the file at ``path``; None when it is none of them."""
        if (found := self.plain.get(path)) is not None:
            return path, found
        if (built := BUILT_IN.fullmatch(path)) is None:
            return None
        plain = built[1] + built[3]
        found = (self.kept if built[2] == self.build else self.other).get(plain)
        return None if found is None else (plain, found)


def static_file(body: bytes, path: Path, mtime: float, cache: str = "", tag: str = "") -> StaticFile:
    """The answer StaticFiles gives the file at ``path``: its type (text with ``charset=utf-8``), ``accept-ranges``,
    length, Last-Modified and ETag (an MD5 of its time and size, then ``tag``); ``cache``, its Cache-Control."""
    kind = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    kind += "; charset=utf-8" if kind.startswith("text/") else ""
    etag = f'"{hashlib.md5(f"{mtime}-{len(body)}{tag}".encode(), usedforsecurity=False).hexdigest()}"'
    modified = email.utils.formatdate(mtime, usegmt=True)
    cached = ((b"cache-control", cache.encode()),) if cache else ()
    headers = ((b"content-type", kind.encode()), (b"accept-ranges", b"bytes"),
               (b"content-length", str(len(body)).encode()), (b"last-modified", modified.encode()),
               (b"etag", etag.encode()), *cached)  # fmt: skip
    return StaticFile(body, headers, ((b"etag", etag.encode()), *cached), etag, modified)


@functools.cache
def read_static(folders: tuple[tuple[str, Path], ...] = STATIC_FOLDERS) -> Static:
    """Every file of the static folders, read once. A file whose real path is outside its folder (a link out of it)
    is left out: StaticFiles refuses it too."""
    found: list[tuple[str, Path, bytes, float]] = []
    for prefix, folder in folders:
        inside = folder.resolve()
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.resolve().is_relative_to(inside):
                found.append((prefix + path.relative_to(folder).as_posix(), path, path.read_bytes(),
                              path.stat().st_mtime))  # fmt: skip
    digest = hashlib.sha256()
    for address, _, body, _ in found:
        digest.update(f"{address}\0{len(body)}\0".encode() + body)
    build = digest.hexdigest()[:10]
    static = Static(build, {}, {}, {})
    for address, path, body, mtime in found:
        static.plain[address] = static_file(body, path, mtime, CHECKED if address.startswith("/static/") else "")
        # A stylesheet names the fonts at their kept addresses: its body changes with the build, and its ETag too.
        kept = _LINKED.sub(rb"url(\g<1>\g<2>" + build.encode() + b"/", body) if path.suffix == ".css" else body
        tag = f"-{build}" if kept != body else ""
        static.kept[address] = static_file(kept, path, mtime, KEPT, tag)
        static.other[address] = static_file(kept, path, mtime, CHECKED, tag)
    return static


def static_url(address: str) -> str:
    """A static file's kept address, for a page: ``/static/app.css`` → ``/static/<build>/app.css``."""
    for prefix, _ in STATIC_FOLDERS:
        if address.startswith(prefix):
            return f"{prefix}{read_static().build}/{address.removeprefix(prefix)}"
    raise ValueError(f"Not a static file's address: {address}")


# --- Templates ---------------------------------------------------------------------------------------------------

# The HTML builders of ap_coder/core/ui.py that a template may call (each escapes the text it is given). The text
# helpers (money, plural, time_ago, initials) are filters instead, so their output is escaped like any text.
_UI_HTML = {
    "avatar", "breadcrumbs", "check", "empty_note", "empty_state", "kbd", "pill", "recent_row",
    "ring", "section_title", "status", "step", "subhead", "tax_chip", "tile", "tiles", "timeline",
}  # fmt: skip


class _UiHelpers:
    """``{{ ui.pill(...) }}`` in a template: the ui.py builder, its HTML marked safe (it escaped its input)."""

    def __getattr__(self, name: str) -> Callable[..., Markup]:
        if name not in _UI_HTML:
            raise AttributeError(f"ui.{name} is not available in templates")
        builder = getattr(ui, name)
        return lambda *args, **kwargs: Markup(builder(*args, **kwargs))  # noqa: S704 - ui.py escapes its input


def money(value: Any, currency: str = "") -> str:
    """1,234.50 (a credit as (1,234.50)), "—" for no amount; ``currency`` after it."""
    text = ui.money(value)
    return f"{text} {currency}" if currency and text != "—" else text


# Autoescape on for every template: text from invoices and files is always escaped.
templates = Jinja2Templates(env=jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES_DIR), autoescape=True))
templates.env.globals.update(
    ui=_UiHelpers(), static_url=static_url, unassigned=UNASSIGNED
)  # (a line without a GL account, as the pages say it)
templates.env.filters.update(
    money=money,
    plural=ui.plural,
    time_ago=ui.time_ago,
    initials=ui.initials,
    num=lambda n: f"{int(n or 0):,}",
    pct=lambda v: "—" if v is None else f"{float(v):.0%}",
)


# --- The data ----------------------------------------------------------------------------------------------------


def get_store(request: Request) -> Store:
    """The database this dashboard was started with."""
    return request.app.state.store


def one_connection(handler: Callable[..., Any]) -> Callable[..., Any]:
    """A page handler (a plain ``def``: FastAPI runs it on a threadpool thread) whose store calls all share one
    database connection (``Store.session``, held by that thread for the request): a heavy page makes dozens of
    store calls, and a connection opened and closed for each costs more than the queries. A GET's report answer that
    another works out with none to show is not waited for past its brief wait (``reports.asked_by_a_page``, D-201)."""

    @functools.wraps(handler)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        request = kwargs.get("request")
        if request is None:
            request = next(a for a in args if isinstance(a, Request))
        page = reports.asked_by_a_page() if request.method == "GET" else contextlib.nullcontext()
        with get_store(request).session(), page:
            return handler(*args, **kwargs)

    wrapped.one_connection = True  # type: ignore[attr-defined]
    return wrapped


# --- The long routes' own lane in each web worker of the company server (D-153) ---------------------------------
# A worker runs a few requests at once (``app.worker_threads``). These routes can take seconds and read a lot (the
# reports, exports, the audit trail, Report a problem), or wait on something else (a backup's thread, LM Studio,
# JD Edwards): there they run on threads of their own, HEAVY_AT_ONCE at a time in each worker, so a few people's
# reports never hold every thread while the others' pages wait. One that waits HEAVY_WAIT seconds for its turn is
# told AP Coder is busy. On a laptop they run as any page does.
# The long downloads made by the server's background process are not among them: their web worker only waits
# (``pages/downloads.py``, D-169).
HEAVY_ROUTES = frozenset({
    ("GET", "/activity"),
    ("GET", "/spend"), ("GET", "/spend/data.xlsx"),
    ("GET", "/month-end"),
    ("GET", "/sales-tax"), ("GET", "/sales-tax/claims.csv"), ("GET", "/sales-tax/self-assessment.csv"),
    ("GET", "/insights"),
    ("GET", "/exports"), ("GET", "/exports/jde/dry-run.txt"),
    ("GET", "/exports/batches/{batch:int}/download"), ("POST", "/exports/batches/{batch:int}/download"),
    ("GET", "/exports/batches/{batch:int}/approved-pdfs.zip"),
    ("GET", "/settings/jde/worksheet.xlsx"), ("GET", "/learning/training.zip"),
    ("GET", "/help/report"), ("GET", "/help/report/setup"),
    ("POST", "/settings/data/backup"), ("POST", "/settings/data/backup-copy"), ("POST", "/settings/data/restore"),
    ("POST", "/settings/reading/check"), ("POST", "/settings/reading/load-model"), ("POST", "/settings/jde/api/test"),
})  # fmt: skip
HEAVY_AT_ONCE = 2
HEAVY_WAIT = 30.0  # seconds
BUSY = ("AP Coder is busy with other people's reports and exports just now. Try again in a minute: nothing was "
        "lost.")  # fmt: skip
_HEAVY_LANE: RunVar[anyio.CapacityLimiter] = RunVar("ap_coder_heavy_lane")  # one for each event loop (a worker's)


def heavy(handler: Callable[..., Any]) -> Callable[..., Any]:
    """A plain handler of HEAVY_ROUTES, run by ``run_heavy`` (``OneConnectionRoute`` applies it)."""

    @functools.wraps(handler)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        request = kwargs.get("request")
        if request is None:
            request = next(a for a in args if isinstance(a, Request))
        return await run_heavy(request, functools.partial(handler, *args, **kwargs))

    wrapped.heavy = True  # type: ignore[attr-defined]
    return wrapped


async def run_heavy(request: Request, call: Callable[..., Any], *args: Any) -> Any:
    """``call(*args)`` on a thread, as FastAPI runs a plain handler; on the company server, in the long routes' lane
    (HEAVY_AT_ONCE at a time, never on the worker's own threads). An ``async`` handler of a long route calls this
    itself; a 503 saying BUSY after HEAVY_WAIT seconds in line."""
    if not server_mode(request):
        return await run_in_threadpool(call, *args)
    lane = heavy_lane()
    turn = False
    with anyio.move_on_after(HEAVY_WAIT):
        await lane.acquire()
        turn = True
    if not turn:
        raise HTTPException(status_code=503, detail=BUSY)
    try:  # a limiter of its own: the pool's tokens are the other requests'
        return await anyio.to_thread.run_sync(functools.partial(call, *args), limiter=anyio.CapacityLimiter(1))
    finally:
        lane.release()


def heavy_lane() -> anyio.CapacityLimiter:
    """The long routes' lane of this event loop (made on first use)."""
    try:
        return _HEAVY_LANE.get()
    except LookupError:
        lane = anyio.CapacityLimiter(HEAVY_AT_ONCE)
        _HEAVY_LANE.set(lane)
        return lane


def page_router() -> Any:
    """The ``router`` of a page module (``ap_coder/web/pages/<page>.py``): each plain ``def`` handler declared on it
    that takes the request is made ``one_connection`` (``OneConnectionRoute``), so no page opens a connection per
    store call because its handler was not marked (7 of 179 were; the settings page opened 23 a request)."""
    from fastapi import APIRouter

    return APIRouter(route_class=_one_connection_route())


@functools.cache
def _one_connection_route() -> type:
    import inspect

    from fastapi.routing import APIRoute

    class OneConnectionRoute(APIRoute):
        """A route whose plain handler shares one database connection for the request. An ``async`` handler is left
        as it is: other requests run on its thread while it awaits, so a connection held across that would be
        theirs too. A plain handler of HEAVY_ROUTES runs in the long routes' lane (``heavy``)."""

        def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
            if (not inspect.iscoroutinefunction(endpoint) and not getattr(endpoint, "one_connection", False)
                    and "request" in inspect.signature(endpoint).parameters):  # fmt: skip
                endpoint = one_connection(endpoint)
            if (not inspect.iscoroutinefunction(endpoint)
                    and any((str(m).upper(), path) in HEAVY_ROUTES for m in kwargs.get("methods") or ())):  # fmt: skip
                endpoint = heavy(endpoint)
            super().__init__(path, endpoint, **kwargs)

    return OneConnectionRoute


def get_settings(request: Request) -> Settings:
    """The settings, read again from the .env each time (a change on the Settings page applies at once), or the
    fixed settings ``create_app`` was given (tests)."""
    return request.app.state.settings_factory()


def jobs(request: Request) -> Jobs:
    """The app's background jobs (``ap_coder/web/jobs.py``)."""
    return request.app.state.jobs


# Starlette refuses a form of more than 1,000 fields by default; the review screen posts about ten per invoice line
# (an invoice of 100 lines is over), the Exports page one per invoice ticked (B-455).
MAX_FORM_FIELDS = 200_000


async def posted_form(request: Request) -> Any:
    """The form the page posted, however many fields it has (``MAX_FORM_FIELDS``)."""
    return await request.form(max_fields=MAX_FORM_FIELDS)


def data_dir(store: Store) -> Path:
    return store.path.parent


def invoice_dir(store: Store) -> Path:
    """The invoices folder next to the database: uploads are saved there, new files are picked up from it."""
    return data_dir(store) / "invoices"


def cache_dir(store: Store) -> Path:
    return data_dir(store) / ".cache" / "extraction"


def reference_or_none(store: Store) -> ReferenceData | None:
    """The GL accounts and tax setup, or None before any GL accounts are imported."""
    try:
        return store.reference_data()
    except ValueError:
        return None


def login() -> str:
    """The Windows / computer account running the dashboard (recorded with approvals); for a colleague on the
    office network, their computer's address (web/identity.py); in server mode, their account (``user:<id>``: the
    second approval needs another one, D-98)."""
    found = identity.person()
    if found is not None and found.user_id:
        return f"user:{found.user_id}"
    if found is not None:
        return identity.network_login(found.address)
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name available (unusual service accounts)
        return ""


def reviewer() -> str:
    """The person approving: the name this Windows user saved in Settings (kept per user, so two people sharing one
    AP Coder keep their own names), else AP_REVIEWER from the .env, else the computer login. A colleague on the
    office network is the name they gave on the "Who are you?" page (web/identity.py); in server mode, the name of
    the account logged in."""
    found = identity.person()
    if found is not None:
        return found.name
    saved = str(paths.read_user_settings().get("reviewer") or "")
    for name in (saved, os.environ.get("AP_REVIEWER"), login()):
        if name and str(name).strip():
            return str(name).strip()
    return "Reviewer"


def first_name() -> str:
    return reviewer().split()[0]


def approved_today(store: Store) -> int:
    """How many invoices this reviewer approved today (shown under their name: not the whole team's)."""
    return store.approved_since(dt.date.today().isoformat(), reviewer())


def by_currency(invoices: list[dict[str, Any]]) -> str:
    """ "27,864.06 CAD · 7,570.01 USD": totals per currency, never added together."""
    totals: dict[str, float] = {}
    for i in invoices:
        cur = i.get("currency") or "CAD"
        totals[cur] = totals.get(cur, 0.0) + float(i.get("grand_total") or 0)
    return " · ".join(f"{money(t)} {c}" for c, t in sorted(totals.items(), key=lambda x: (x[0] != "CAD", x[0])))


def short_path(path: Path) -> str:
    """A path as short as possible: relative to the working folder, else ``~`` for the home folder."""
    path = path.resolve()
    for base, prefix in ((Path.cwd(), ""), (Path.home(), "~")):
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        return str(Path(prefix) / rel) if prefix else str(rel)
    return str(path)


def open_folder(path: Path) -> bool:
    """Open a folder in Explorer / Finder. The dashboard runs on this computer, so the folder is the user's. Never on
    the company server (its screen is no one's): False there."""
    path.mkdir(parents=True, exist_ok=True)
    if offline.server_mode():
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603, S607
        return True
    except OSError:
        return False


def folder_on_the_server(request: Request, folder: Path) -> JSONResponse:
    """Open folder on the company server (D-99): nothing opens on the server's screen. An Admin is told where the
    folder is; everyone else that it is on the server. Both are pointed to the downloads."""
    downloads = ("use the downloads instead: each invoice's file on its review screen, the exports on the Exports "
                 "page and the backups under Settings, Data & backups")  # fmt: skip
    if is_admin(request):
        return message(f"This folder is on the server: {folder}. To get a file from it here, {downloads}.", "info")
    return message(f"This folder is on the server, so it does not open here. To get a file, {downloads}.", "info")


def open_folder_here(request: Request, folder: Path, what: str) -> JSONResponse:
    """An Open folder button (``what``: "invoices folder"): the folder in a window on this computer, never on the
    computer running AP Coder for someone else (the company server, or a colleague on the office network)."""
    if server_mode(request):
        return folder_on_the_server(request, folder)
    if identity.person() is not None:  # a colleague's click must not open a window on the computer running AP Coder
        return message(f"The {what} is on the computer running AP Coder: open it there.", "info")
    if open_folder(folder):
        return message(f"Opened the {what}.", "info")
    return message(f"Open this folder yourself: {folder}", "info")


_UNASKED: Any = object()  # ``render`` without the readers' state: it asks for it


def reading_status_or_none(settings: Settings, store: Store, *, use_cache: bool = True) -> Any:
    """``ap_coder.ai.reading.reading_status``, or None when it can't be worked out (never raises)."""
    try:
        from ap_coder.ai.reading import reading_status

        return reading_status(settings, store, use_cache=use_cache)
    except Exception:  # noqa: BLE001 - a page never breaks over a status line
        return None


# --- Messages after an action (flash) ----------------------------------------------------------------------------
# A POST answers with where to go next; the message waits under a one-off token in the address (?flash=...) and is
# shown once by the page it leads to. It waits in the dashboard's database (``Store.keep_ephemeral``), so a restart
# of the server between the action and its page does not lose it (D-99).

_FLASH_TTL = 600  # seconds a message waits for its page

CURRENT_STORE: contextvars.ContextVar[Store | None] = contextvars.ContextVar("ap_coder_store", default=None)


def current_store() -> Store:
    """The database of the request being answered (set by ``StoreContext``; ``using_store`` outside a request), where
    messages and uploads wait. RuntimeError outside both: they wait nowhere else."""
    store = CURRENT_STORE.get()
    if store is None:
        raise RuntimeError("not answering a request to the dashboard: wrap the call in core.using_store(store)")
    return store


@contextlib.contextmanager
def using_store(store: Store) -> Iterator[None]:
    """Messages and uploads kept in ``store`` inside (as during a request to the dashboard on it)."""
    token = CURRENT_STORE.set(store)
    try:
        yield
    finally:
        CURRENT_STORE.reset(token)


class StoreContext:
    """ASGI middleware: each request knows its dashboard's database, where messages and uploads wait (D-99)."""

    def __init__(self, app: Any, store: Store) -> None:
        self.app, self.store = app, store

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        app = scope.get("app")
        token = CURRENT_STORE.set(getattr(getattr(app, "state", None), "store", None) or self.store)
        try:
            await self.app(scope, receive, send)
        finally:
            CURRENT_STORE.reset(token)


def flash_message(text: str, tone: str = "ok", details: list[str] | None = None, icon: str = "") -> dict[str, Any]:
    """One message: ``tone`` ok, info, warn or err; ``details`` (plain text lines) listed under it."""
    icon = icon or {"ok": "check_circle", "info": "info", "warn": "warning", "err": "error"}.get(tone, "info")
    return {"text": str(text), "tone": tone, "details": [str(d) for d in details or []], "icon": icon}


def keep_flash(messages: list[dict[str, Any]]) -> str:
    """Keep ``messages`` for the next page; returns their token."""
    return current_store().keep_ephemeral("flash", json.dumps(messages).encode("utf-8"), _FLASH_TTL)


def take_flash(token: str | None) -> list[dict[str, Any]]:
    """The messages kept under ``token`` (once: they are forgotten)."""
    if not token:
        return []
    try:
        kept = current_store().ephemeral("flash", token, take=True)
        return list(json.loads(kept)) if kept else []
    except ValueError:  # (never written so: a page shows without its message rather than an error)
        return []


def with_flash(url: str, messages: list[dict[str, Any]]) -> str:
    """``url`` with a token for ``messages`` added to its query (unchanged when there are none)."""
    if not messages:
        return url
    parts = urlsplit(url)
    query = "&".join(q for q in (parts.query, urlencode({"flash": keep_flash(messages)})) if q)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def local_url(url: str, fallback: str = "/") -> str:
    """``url`` when it is a path on this server ("/review?tab=parked"), else ``fallback``: a page never sends the
    browser somewhere else."""
    url = str(url or "")
    # Browsers drop tabs and line breaks from an address: "/<tab>/other.site" would be "//other.site" to them.
    if not url.startswith("/") or url.startswith("//") or "\\" in url or any(c < " " or c == "\x7f" for c in url):
        return fallback
    return url


def done(request: Request, url: str, *messages: str | dict[str, Any], tone: str = "ok") -> JSONResponse:
    """The answer to a page's POST: go to ``url`` (a path on this server), showing ``messages`` there (text, or
    ``flash_message`` dicts)."""
    items = [m if isinstance(m, dict) else flash_message(m, tone) for m in messages if m]
    return JSONResponse({"ok": True, "redirect": with_flash(local_url(url), items)})


def message(text: str, tone: str = "ok", **extra: Any) -> JSONResponse:
    """The answer to a page's POST that stays on the page: show ``text`` as a toast."""
    return JSONResponse({"ok": True, "toast": flash_message(text, tone), **extra})


def refuse(text: str, status: int = 400, **extra: Any) -> JSONResponse:
    """The answer to a page's POST that could not be done: the page shows ``text`` and stays as it is."""
    return JSONResponse({"ok": False, "error": str(text), **extra}, status_code=status)


# A date typed in an address ("/month-end?end=0001-01-01") is kept within these years: the reports count days and
# months from it, which would run past the calendar's first or last day (B-659).
OLDEST_DAY, LATEST_DAY = dt.date(1900, 1, 1), dt.date(2999, 12, 31)


def address_day(value: Any, default: dt.date) -> dt.date:
    """A YYYY-MM-DD from the address, kept within those years, or ``default`` when it is missing or not a date."""
    try:
        return min(max(dt.date.fromisoformat(str(value or "").strip()[:10]), OLDEST_DAY), LATEST_DAY)
    except ValueError:
        return default


def shown_count(value: Any, step: int) -> int:
    """How many rows of a long list to show ("Show more": ``shown`` in the address): ``step``, or more in whole steps
    of it, a million at most; ``step`` when it is missing, not a number or fewer (D-162)."""
    try:
        wanted = int(value or step)
    except ValueError:
        return step
    return min(max(step, -(-wanted // step) * step), 1_000_000)


# A form that saves a whole list or page posts a stamp of what it showed (``page_version``, in a hidden "version"
# field): changed since in another tab or by a colleague, the save is refused rather than overwriting it (B-655).
STALE_PAGE = (
    "This page was changed elsewhere since you opened it: reload to see the latest, then make your change again."
)


def page_version(shown: Any) -> str:
    """A short stamp of what a page shows (any JSON-like value: the same value, the same stamp)."""
    return hashlib.sha1(json.dumps(shown, sort_keys=True, default=repr).encode()).hexdigest()[:16]


def stale_page(form: Any, shown: Any) -> JSONResponse | None:
    """The refusal when ``form`` posted a stamp that is not that of ``shown`` now, else None (a form that posted no
    stamp, a script or an older page, is not checked)."""
    posted = form.get("version")
    if posted is None or str(posted) == page_version(shown):
        return None
    return refuse(STALE_PAGE, 409)


# --- What is shown (Settings → About, D-52) -----------------------------------------------------------------------
SHOW_JDE = "show_jde_settings"  # "1": Settings → JD Edwards E1 is a tab, and JD Edwards E1 an export format
DEVELOPER_TOOLS = "show_developer_tools"  # "1": Learning → Readers, the training data, What's new for developers
DEVELOPER_ENV = "AP_DEVELOPER_TOOLS"  # =1: the same, for a developer's computer


def _switch(store: Store, key: str) -> bool:
    try:
        return store.get_setting(key) == "1"
    except Exception:  # noqa: BLE001 - a setting that can't be read is off: the page still opens
        return False


def jde_shown(store: Store) -> bool:
    """Whether the JD Edwards E1 settings and export are shown: off on a laptop (Excel and CSV exports only),
    on at first on the company server (D-102); a switch on Settings → About shows or hides them, nothing is removed.
    A database already set up for JD Edwards (its settings saved, or a JD Edwards batch made) has it turned on, once
    and stored, so an update takes nothing away (B-526)."""
    try:
        value = store.get_setting(SHOW_JDE, "unset")
        if value != "unset":
            return value == "1"
        from ap_coder.erp import jde_settings

        used = bool(store.get_setting(jde_settings.SETTING_KEY)) or any(
            b["format"] == jde_settings.FORMAT for b in store.export_batches()
        )
        if used:
            store.set_setting(SHOW_JDE, "1")
        return used or offline.server_mode()  # the company server is the JD Edwards pilot: shown at first (D-102)
    except Exception:  # noqa: BLE001 - a setting that can't be read is off: the page still opens
        return False


SCREENS = "screens"  # "simple": the short menu (pages.SIMPLE_MAIN, SIMPLE_SETUP); "everything" or not set: every page
SIMPLE, EVERYTHING = "simple", "everything"
SIMPLE_HIDDEN_TABS = ("automation", "hub")  # the Settings tabs the Simple screens leave out


def simple_screens(store: Store) -> bool:
    """Whether the Simple screens are chosen (Settings → About → "What is shown"): the short menu of one person
    coding invoices to JD Edwards, the other pages and the Automation and Finance hub tabs left out, nothing
    removed. A database that never chose shows everything, as it always did; a laptop's new data folder starts
    Simple (``first_run``)."""
    try:
        return store.get_setting(SCREENS) == SIMPLE
    except Exception:  # noqa: BLE001 - a setting that can't be read is off: the page still opens
        return False


def hidden_in_simple(store: Store, page: str, tab: Any = None) -> bool:
    """Whether ``page`` (or Settings' ``tab``) is one the Simple screens leave out: opened by its address, it says
    so in one line."""
    if not simple_screens(store):
        return False
    return (page in BY_KEY and page not in SIMPLE_KEYS) or (page == "settings" and tab in SIMPLE_HIDDEN_TABS)


def first_run(db: Path, *, server: bool) -> Store:
    """The database the dashboard starts on. A laptop's new data folder (no database file there before this start)
    begins the department's way: the Simple screens, and its JD Edwards settings (``erp.jde_prototype.use_prototype``:
    the settings saved, JD Edwards shown, the QST added to the expense line). A database that exists already, and the
    company server's, are opened as they are."""
    new = not db.exists()
    store = Store(db)
    if new and not server:
        from ap_coder.erp import jde_prototype

        store.set_setting(SCREENS, SIMPLE)
        jde_prototype.use_prototype(store)
    return store


def keying_to_set_up(store: Store) -> bool:
    """Whether JD Edwards keying is still to be set up the department's way: no JD Edwards settings saved yet, or
    saved ones that are no setup (a supplier number alone: ``jde_settings.set_up``, B-2532)."""
    from ap_coder.erp import jde_settings

    saved = store.get_setting(jde_settings.SETTING_KEY)
    return not saved or not jde_settings.set_up(jde_settings.JdeSettings.from_json(saved))


def use_simple(store: Store, actor: str | None = None) -> bool:
    """The Simple screens chosen on an older data folder: the short menu, and the department's JD Edwards set-up
    (``use_prototype``, as a new folder's, keeping what was saved besides) when no JD Edwards setup was saved
    (``keying_to_set_up``); a setup someone made is never touched (B-2378). True when JD Edwards keying was set up.
    The screens are saved last: a set-up that fails leaves the choice unmade, so the card and the next start still
    offer it."""
    keyed = keying_to_set_up(store)
    if keyed:
        from ap_coder.erp import jde_prototype

        jde_prototype.use_prototype(store, actor)
    store.set_setting(SCREENS, SIMPLE, actor=actor)
    return keyed


PILOT_SETUP = "pilot_setup"  # an older laptop data folder set up at a start (D-232): "done", or "declined"
ASK_SIMPLE = ("Use the short menu and set up JD Edwards keying (the keying sheet)? Your invoices are kept; QST "
              "is then added to each expense line, as JD Edwards posts it. [Y/n] ")  # fmt: skip
# The question when JD Edwards was set up already (``keying_to_set_up`` False): only the screens change (B-2531).
ASK_SCREENS = "Use the short menu? Your invoices and your JD Edwards settings are kept as they are. [Y/n] "


def older_folder_start(db: Path, ask: Callable[[str], bool] | None = None) -> str:
    """A laptop's data folder an earlier AP Coder made, set up the department's way at a start, once (D-232; the
    launcher, before it serves; never the company server's). Holding no invoices but the demo's (or none): as a new
    folder, the demo invoices removed (``_demo_removed``), the Simple screens and JD Edwards keying (``use_simple``),
    no question. Holding invoices of its own, or, with JD Edwards keying to set up, GL accounts or a tax setup of its
    own (``setup_of_its_own``, B-2531): ``ask`` (the question ``ASK_SIMPLE``, or ``ASK_SCREENS`` when JD Edwards was
    set up already; True for yes) decides; yes does what the queue's card's "Use the simple screens" does, no is
    recorded (the card still asks); no ``ask`` (nobody at the window), nothing more is done or recorded. A folder
    whose screens were chosen already is left as it is, but one: Simple chosen with JD Edwards keying never set up
    (chosen before that set it up, B-2382) is set up as one whose screens were never chosen. Returns what to say ("" for
    nothing)."""
    if not db.is_file():
        return ""  # a new data folder: ``first_run`` sets it up
    store = Store(db)
    screens = store.get_setting(SCREENS)
    if store.get_setting(PILOT_SETUP) or (screens and (screens != SIMPLE or not keying_to_set_up(store))):
        return ""
    own = sum(store.status_counts().values()) > store.demo_count()  # invoices of its own
    removed = 0 if own else _demo_removed(store)
    keying = keying_to_set_up(store)
    if own or (keying and setup_of_its_own(store)):  # (the keying changes how its QST is coded: B-2531)
        if ask is None or not ask(ASK_SIMPLE if keying else ASK_SCREENS):
            if ask is not None:
                store.set_setting(PILOT_SETUP, "declined")
            return "The demo invoices were removed." if removed else ""
    keyed = use_simple(store)
    store.set_setting(PILOT_SETUP, "done")
    store.log_event("settings_changed", detail={"keys": [SCREENS, PILOT_SETUP]})
    said = "Set up for JD Edwards keying: the short menu, " + (
        "the keying sheet" if keyed else "your JD Edwards settings kept as they are"
    )
    return said + ("; the demo invoices removed." if removed else ".")


def _demo_removed(store: Store) -> int:
    """A folder with no invoices of its own begins as a new one: its demo invoices removed (``remove_demo``: what it
    loaded with them too), and a removed batch's format, which is no one's choice (B-2383). How many went."""
    from ap_coder.erp.jde_settings import EXPORT_FORMAT
    from ap_coder.ops.demo import remove_demo

    removed = remove_demo(store) if store.demo_count() else 0
    if store.get_setting(EXPORT_FORMAT) and not store.export_batches():
        store.set_setting(EXPORT_FORMAT, "")
    return removed


def setup_of_its_own(store: Store) -> bool:
    """Whether a folder holds a setup someone made, which the JD Edwards keying changes (the QST added to the expense
    line): GL accounts or cost centers, or a tax setup other than the default (B-2531). The demo's sample setup is
    not one once the demo is removed (``remove_demo`` puts back what was there before)."""
    from ap_coder.core.tax_setup import DEFAULT_TREATMENTS

    if store.list_accounts("gl_accounts") or store.list_accounts("cost_centers"):
        return True
    return any((t.treatment, t.gl_code) != (DEFAULT_TREATMENTS[k], "") for k, t in store.tax_treatments().items())


def developer_env_on() -> bool:
    """AP_DEVELOPER_TOOLS read one way everywhere: 1, true or yes is on; 0, false, no or nothing is off (B-527)."""
    return os.environ.get(DEVELOPER_ENV, "").strip().lower() in ("1", "true", "yes")


def developer_tools(store: Store) -> bool:
    """Whether the developer tools show (Learning → Readers, the training data export, the developer changelog):
    the switch on Settings → About, or AP_DEVELOPER_TOOLS=1."""
    return developer_env_on() or _switch(store, DEVELOPER_TOOLS)


# --- Drawing a page ----------------------------------------------------------------------------------------------


def nav_counts(store: Store) -> dict[str, int]:
    """The count badges of the rail: invoices waiting for review (the one queue to work through) and approved
    invoices not exported yet (the first a few seconds behind a change: ``status_counts(settled=True)``)."""
    return {"review": store.status_counts(settled=True).get(REVIEW, 0), "exports": store.ready_to_export_count()}


def rail(store: Store) -> dict[str, list[dict[str, Any]]]:
    """The rail's groups, each page with its count: the everyday pages (``main``), the pages folded under More
    (``more``) and Settings and Help, pinned at its foot (``pinned``). In Simple screens (``simple_screens``): the
    everyday pages under their names there, the pages under "Setup" (``setup``) and Help, with no More."""
    counts = nav_counts(store)

    def items(pages: list[Any]) -> list[dict[str, Any]]:
        return [{"page": p, "count": counts.get(p.key, 0)} for p in pages]

    if simple_screens(store):
        named = [dataclasses.replace(BY_KEY[key], title=title) for key, title in SIMPLE_MAIN.items()]
        return {"main": items(named), "more": [], "setup": items([BY_KEY[key] for key in SIMPLE_SETUP]),
                "pinned": items([BY_KEY["help"]])}  # fmt: skip
    return {
        "main": items([p for p in PAGES if not p.more and p.key not in PINNED]),
        "more": items([p for p in PAGES if p.more]),
        "setup": [],
        "pinned": items([p for p in PAGES if p.key in PINNED]),
    }


def server_mode(request: Request) -> bool:
    """Whether this dashboard runs as the company's server: people log in, their role says what they may do (D-97)."""
    return bool(getattr(request.app.state, "server_mode", False))


def screens_to_choose(request: Request, store: Store) -> bool:
    """Whether the queue page asks which screens to show (its card, ``_screens_card.html``, D-221): a laptop's
    database on which the Screens choice was never saved, so one an earlier AP Coder made, which goes on showing
    every page (``first_run``) while the person wonders whether the update worked. Either answer is saved (``POST
    /settings/about/screens``), so it is asked once; a new data folder starts Simple and is never asked, nor is
    the company server, whose screens are an Admin's to choose."""
    return not server_mode(request) and not store.get_setting(SCREENS)


def can(request: Request, path: str, method: str = "POST") -> bool:
    """Whether the person asking may use ``path`` (a page hides what they can't; the server refuses it anyway):
    on a laptop, always from its own browser, and a colleague on the office network all but what is this computer's
    (``identity.here_only``, B-2430); their role's say in server mode (web/roles.py)."""
    found = identity.person()
    path = path.split("#", 1)[0].split("?", 1)[0]  # a link's address: the route is the path's
    if not server_mode(request):
        return found is None or not identity.here_only(path, method.upper())
    from ap_coder.web import roles

    return found is not None and roles.can(request.app, found.role, method, path)


def _shown_rail(request: Request, store: Store) -> dict[str, list[dict[str, Any]]]:
    """The rail; in server mode only the pages the person's role may open."""
    found = rail(store)
    if not server_mode(request):
        return found
    return {group: [i for i in items if can(request, i["page"].path, "GET")] for group, items in found.items()}


def account(request: Request) -> dict[str, Any] | None:
    """The person logged in, for the rail's foot (server mode only): their name, username and role."""
    found = identity.person()
    if not server_mode(request) or found is None or not found.user_id:
        return None
    from ap_coder.core.store.users import ROLE_LABELS

    return {"name": found.name, "username": found.username, "role": found.role,
            "role_label": ROLE_LABELS.get(found.role, found.role)}  # fmt: skip


def admin_notice(request: Request) -> dict[str, str] | None:
    """The server's banner for an Admin: its HTTPS certificate within 30 days of its end, or past it (D-187)."""
    if not server_mode(request) or not is_admin(request):
        return None
    from ap_coder.ops.health import certificate_notice

    return certificate_notice(getattr(request.app.state, "certfile", None))


def render(request: Request, template: str, page: str, status_code: int = 200, *, reading_status: Any = _UNASKED,
           **context: Any) -> HTMLResponse:  # fmt: skip
    """Draw ``template`` (which extends base.html) as page ``page`` (a key of ``pages.PAGES``): the rail, the
    reviewer, the messages left by the last action and, on Process and Review, OvisOCR2's warning
    (``reading_status``: the readers' state when the page has it already, ``reading_status_or_none``)."""
    store, settings = get_store(request), get_settings(request)
    meta = BY_KEY.get(page)
    banner, banner_tone = "", "warn"
    if page in BANNER_PAGES:
        status = reading_status_or_none(settings, store) if reading_status is _UNASKED else reading_status
        banner = str(getattr(status, "banner", "") or "").strip()
        if getattr(status, "self_test", "") in ("pending", "running"):  # the first start's self-test: nothing wrong
            banner_tone = "info"
        elif banner and server_mode(request):  # staff can't start LM Studio: it runs on the server (D-102)
            from ap_coder.ops.help import SERVER_LM_STUDIO

            banner = f"{banner} {SERVER_LM_STUDIO}."
    me = reviewer()
    simple = simple_screens(store)
    if simple and meta and page in SIMPLE_MAIN:  # the page under the name the Simple menu gives it
        meta = dataclasses.replace(meta, title=SIMPLE_MAIN[page])
    base = {
        "page": page,
        "meta": meta,
        "simple_screens": simple,
        "hidden_in_simple": simple and hidden_in_simple(store, page, context.get("tab")),  # opened by its address
        "title": meta.title if meta else context.get("title", "AP Coder"),
        "section": meta.section if meta else "",
        "rail": _shown_rail(request, store),
        "reviewer": me,
        "approved_today": approved_today(store),
        "demo_count": store.demo_count(),
        "data_folder": short_path(data_dir(store)),
        "flashes": take_flash(request.query_params.get("flash")),
        "banner": banner,
        "banner_tone": banner_tone,
        "settings": settings,
        "server_mode": server_mode(request),  # the company's server (D-97): no folder windows, no "this computer"
        "is_admin": is_admin(request),  # may see the server's own details, such as its folders (web/access.py)
        "admin_notice": admin_notice(request),  # the server's HTTPS certificate near its end, or past it (D-187)
        "account": account(request),
        "can": functools.partial(can, request),
    }
    base.update(context)
    response = templates.TemplateResponse(request, template, base, status_code=status_code)
    # Never kept by the browser: Back after an action (park, approve...) shows where things stand now, not the page
    # as it was before.
    response.headers["Cache-Control"] = "no-store"
    return response
