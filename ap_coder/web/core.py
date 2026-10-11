"""What every page of the dashboard shares: templates, the data, the rail, messages after an action.

A page module uses these and nothing of Streamlit::

    store, settings = get_store(request), get_settings(request)
    return render(request, "vendors.html", "vendors", vendors=store.vendor_summaries())

    # after a POST from the page (static/js/app.js sends it and follows the answer):
    return done(request, "/vendors", "Vendor saved.")             # go there, with a message
    return message("Copied.")                                     # stay, show a message
    return refuse("That file is not a CSV.")                      # stay, show the problem

Templates are escaped automatically (Jinja autoescape). The ``ui`` global gives the HTML helpers of
``ap_coder/ui.py`` (they escape what they are given), e.g. ``{{ ui.pill("Ready", "ok") }}``. Never mark text from an
invoice or a file as safe yourself.
"""

from __future__ import annotations

import datetime as dt
import getpass
import hashlib
import os
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import jinja2
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ap_coder import __version__, paths, ui
from ap_coder.config import Settings
from ap_coder.reference_data import ReferenceData
from ap_coder.store import REVIEW, Store
from ap_coder.web.jobs import Jobs
from ap_coder.web.pages import BANNER_PAGES, BY_KEY, PAGES, SECTIONS

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
FONTS_DIR = WEB_DIR.parent / "static"  # Inter and the Material Symbols icons (shared with the Streamlit dashboard)
ASSETS_DIR = WEB_DIR.parent / "assets"  # logo and icon

TARGET_ACCURACY = 0.90


# --- Templates ---------------------------------------------------------------------------------------------------

# The HTML builders of ap_coder/ui.py that a template may call (each escapes the text it is given). The text helpers
# (money, plural, time_ago, initials) are filters instead, so their output is escaped like any text.
_UI_HTML = {
    "avatar", "breadcrumbs", "check", "empty_note", "empty_state", "icon", "kbd", "kv", "meter", "page_header",
    "pill", "progress", "recent_row", "ring", "section_title", "sparkline", "split_bar", "status", "step",
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
templates.env.globals.update(ui=_UiHelpers(), static_version=_static_version(), version=__version__)
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


def get_settings(request: Request) -> Settings:
    """The settings, read again from the .env each time (a change on the Settings page applies at once), or the
    fixed settings ``create_app`` was given (tests)."""
    return request.app.state.settings_factory()


def jobs(request: Request) -> Jobs:
    """The app's background jobs (``ap_coder/web/jobs.py``)."""
    return request.app.state.jobs


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
    """The Windows / computer account running the dashboard (recorded with approvals)."""
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name available (unusual service accounts)
        return ""


def reviewer() -> str:
    """The person approving: the name this Windows user saved in Settings (kept per user, so two people sharing one
    AP Coder keep their own names), else AP_REVIEWER from the .env, else the computer login."""
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
    """Open a folder in Explorer / Finder. The dashboard runs on this computer, so the folder is the user's."""
    path.mkdir(parents=True, exist_ok=True)
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


def reading_status_or_none(settings: Settings, store: Store, *, use_cache: bool = True) -> Any:
    """``ap_coder.reading.reading_status``, or None when it can't be worked out (never raises)."""
    try:
        from ap_coder.reading import reading_status

        return reading_status(settings, store, use_cache=use_cache)
    except Exception:  # noqa: BLE001 - a page never breaks over a status line
        return None


# --- Messages after an action (flash) ----------------------------------------------------------------------------
# A POST answers with where to go next; the message waits under a one-off token in the address (?flash=...) and is
# shown once by the page it leads to. Nothing about it is kept on disk.

_FLASH_TTL = 600  # seconds a message waits for its page
_flashes: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_flash_lock = threading.Lock()


def flash_message(text: str, tone: str = "ok", details: list[str] | None = None, icon: str = "") -> dict[str, Any]:
    """One message: ``tone`` ok, info, warn or err; ``details`` (plain text lines) listed under it."""
    icon = icon or {"ok": "check_circle", "info": "info", "warn": "warning", "err": "error"}.get(tone, "info")
    return {"text": str(text), "tone": tone, "details": [str(d) for d in details or []], "icon": icon}


def keep_flash(messages: list[dict[str, Any]]) -> str:
    """Keep ``messages`` for the next page; returns their token."""
    token = secrets.token_urlsafe(8)
    now = time.monotonic()
    with _flash_lock:
        for key in [k for k, (at, _) in _flashes.items() if now - at > _FLASH_TTL]:
            del _flashes[key]
        _flashes[token] = (now, messages)
    return token


def take_flash(token: str | None) -> list[dict[str, Any]]:
    """The messages kept under ``token`` (once: they are forgotten)."""
    if not token:
        return []
    with _flash_lock:
        _, messages = _flashes.pop(token, (0.0, []))
    return messages


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


# --- Drawing a page ----------------------------------------------------------------------------------------------


def nav_counts(store: Store) -> dict[str, int]:
    """The count badges of the rail: invoices waiting for review (the one queue to work through) and approved
    invoices not exported yet."""
    return {"review": len(store.list_invoices(REVIEW)), "exports": len(store.unexported_approved())}


def rail(store: Store) -> list[dict[str, Any]]:
    """The rail's sections, each with its pages and counts."""
    counts = nav_counts(store)
    return [
        {"title": section, "pages": [{"page": p, "count": counts.get(p.key, 0)} for p in PAGES if p.section == section]}
        for section in SECTIONS
    ]


def render(request: Request, template: str, page: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    """Draw ``template`` (which extends base.html) as page ``page`` (a key of ``pages.PAGES``): the rail, the
    reviewer, the messages left by the last action and, on Process and Review, OvisOCR2's warning."""
    store, settings = get_store(request), get_settings(request)
    meta = BY_KEY.get(page)
    banner = ""
    if page in BANNER_PAGES:
        status = reading_status_or_none(settings, store)
        banner = str(getattr(status, "banner", "") or "").strip()
    me = reviewer()
    base = {
        "page": page,
        "meta": meta,
        "title": meta.title if meta else context.get("title", "AP Coder"),
        "section": meta.section if meta else "",
        "rail": rail(store),
        "reviewer": me,
        "approved_today": approved_today(store),
        "demo_count": store.demo_count(),
        "data_folder": short_path(data_dir(store)),
        "flashes": take_flash(request.query_params.get("flash")),
        "banner": banner,
        "settings": settings,
    }
    base.update(context)
    response = templates.TemplateResponse(request, template, base, status_code=status_code)
    # Never kept by the browser: Back after an action (park, approve...) shows where things stand now, not the page
    # as it was before.
    response.headers["Cache-Control"] = "no-store"
    return response
