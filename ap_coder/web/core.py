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
import datetime as dt
import functools
import getpass
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import jinja2
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ap_coder.core import paths, ui
from ap_coder.core.config import Settings
from ap_coder.core.reference_data import UNASSIGNED, ReferenceData
from ap_coder.core.store import REVIEW, Store
from ap_coder.ops import offline
from ap_coder.web import identity
from ap_coder.web.access import is_admin
from ap_coder.web.jobs import Jobs
from ap_coder.web.pages import BANNER_PAGES, BY_KEY, PAGES, PINNED

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


# --- Templates ---------------------------------------------------------------------------------------------------

# The HTML builders of ap_coder/core/ui.py that a template may call (each escapes the text it is given). The text
# helpers (money, plural, time_ago, initials) are filters instead, so their output is escaped like any text.
_UI_HTML = {
    "avatar", "breadcrumbs", "check", "empty_note", "empty_state", "icon", "kbd",
    "pill", "recent_row", "ring", "section_title", "sparkline", "status", "step",
    "subhead", "supplier_row", "tax_chip", "tile", "tiles", "timeline", "vendor_row",
}  # fmt: skip


class _UiHelpers:
    """``{{ ui.pill(...) }}`` in a template: the ui.py builder, its HTML marked safe (it escaped its input)."""

    def __getattr__(self, name: str) -> Callable[..., Markup]:
        if name not in _UI_HTML:
            raise AttributeError(f"ui.{name} is not available in templates")
        builder = getattr(ui, name)
        return lambda *args, **kwargs: Markup(builder(*args, **kwargs))  # noqa: S704 - ui.py escapes its input


def _static_version() -> str:
    """Changes whenever a stylesheet or script does, so a browser never keeps an old copy after an update."""
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def money(value: Any, currency: str = "") -> str:
    """1,234.50 (a credit as (1,234.50)), "—" for no amount; ``currency`` after it."""
    text = ui.money(value)
    return f"{text} {currency}" if currency and text != "—" else text


# Autoescape on for every template: text from invoices and files is always escaped.
templates = Jinja2Templates(env=jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES_DIR), autoescape=True))
templates.env.globals.update(
    ui=_UiHelpers(), static_version=_static_version(), unassigned=UNASSIGNED
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
    store calls, and a connection opened and closed for each costs more than the queries."""

    @functools.wraps(handler)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        request = kwargs.get("request")
        if request is None:
            request = next(a for a in args if isinstance(a, Request))
        with get_store(request).session():
            return handler(*args, **kwargs)

    wrapped.one_connection = True  # type: ignore[attr-defined]
    return wrapped


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
        theirs too."""

        def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
            if (not inspect.iscoroutinefunction(endpoint) and not getattr(endpoint, "one_connection", False)
                    and "request" in inspect.signature(endpoint).parameters):  # fmt: skip
                endpoint = one_connection(endpoint)
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


# --- What this pilot shows (Settings → About, D-52) -----------------------------------------------------------------
SHOW_JDE = "show_jde_settings"  # "1": Settings → JD Edwards E1 is a tab, and JD Edwards E1 an export format
DEVELOPER_TOOLS = "show_developer_tools"  # "1": Learning → Readers, the training data, What's new for developers
DEVELOPER_ENV = "AP_DEVELOPER_TOOLS"  # =1: the same, for a developer's computer


def _switch(store: Store, key: str) -> bool:
    try:
        return store.get_setting(key) == "1"
    except Exception:  # noqa: BLE001 - a setting that can't be read is off: the page still opens
        return False


def jde_shown(store: Store) -> bool:
    """Whether the JD Edwards E1 settings and export are shown: off for the laptop pilot (Excel and CSV exports only),
    on at first on the company server (D-102); a switch on Settings → About shows or hides them, nothing is removed.
    A database already set up for JD Edwards (its settings saved, or a JD Edwards batch made) has it turned on, once
    and stored, so an update takes nothing away (B-526)."""
    try:
        value = store.get_setting(SHOW_JDE, "unset")
        if value != "unset":
            return value == "1"
        from ap_coder.erp import jde

        used = bool(store.get_setting(jde.SETTING_KEY)) or any(
            b["format"] == jde.FORMAT for b in store.export_batches()
        )
        if used:
            store.set_setting(SHOW_JDE, "1")
        return used or offline.server_mode()  # the company server is the JD Edwards pilot: shown at first (D-102)
    except Exception:  # noqa: BLE001 - a setting that can't be read is off: the page still opens
        return False


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
    invoices not exported yet."""
    return {"review": store.status_counts().get(REVIEW, 0), "exports": store.ready_to_export_count()}


def rail(store: Store) -> dict[str, list[dict[str, Any]]]:
    """The rail's three groups, each page with its count: the everyday pages (``main``), the pages folded under More
    (``more``) and Settings and Help, pinned at its foot (``pinned``)."""
    counts = nav_counts(store)

    def items(pages: list[Any]) -> list[dict[str, Any]]:
        return [{"page": p, "count": counts.get(p.key, 0)} for p in pages]

    return {
        "main": items([p for p in PAGES if not p.more and p.key not in PINNED]),
        "more": items([p for p in PAGES if p.more]),
        "pinned": items([p for p in PAGES if p.key in PINNED]),
    }


def server_mode(request: Request) -> bool:
    """Whether this dashboard runs as the company's server: people log in, their role says what they may do (D-97)."""
    return bool(getattr(request.app.state, "server_mode", False))


def can(request: Request, path: str, method: str = "POST") -> bool:
    """Whether the person asking may use ``path`` (a page hides what they can't; the server refuses it anyway):
    always on a laptop, their role's say in server mode (web/roles.py)."""
    if not server_mode(request):
        return True
    from ap_coder.web import roles

    found = identity.person()
    path = path.split("#", 1)[0].split("?", 1)[0]  # a link's address: the route is the path's
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


def render(request: Request, template: str, page: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    """Draw ``template`` (which extends base.html) as page ``page`` (a key of ``pages.PAGES``): the rail, the
    reviewer, the messages left by the last action and, on Process and Review, OvisOCR2's warning."""
    store, settings = get_store(request), get_settings(request)
    meta = BY_KEY.get(page)
    banner, banner_tone = "", "warn"
    if page in BANNER_PAGES:
        status = reading_status_or_none(settings, store)
        banner = str(getattr(status, "banner", "") or "").strip()
        if getattr(status, "self_test", "") in ("pending", "running"):  # the first start's self-test: nothing wrong
            banner_tone = "info"
        elif banner and server_mode(request):  # staff can't start LM Studio: it runs on the server (D-102)
            from ap_coder.ops.help import SERVER_LM_STUDIO

            banner = f"{banner} {SERVER_LM_STUDIO}."
    me = reviewer()
    base = {
        "page": page,
        "meta": meta,
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
        "account": account(request),
        "can": functools.partial(can, request),
    }
    base.update(context)
    response = templates.TemplateResponse(request, template, base, status_code=status_code)
    # Never kept by the browser: Back after an action (park, approve...) shows where things stand now, not the page
    # as it was before.
    response.headers["Cache-Control"] = "no-store"
    return response
