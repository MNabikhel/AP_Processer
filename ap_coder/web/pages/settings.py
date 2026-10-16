"""Settings: how invoices are read, touchless processing, review behaviour, JD Edwards E1, the data folder and its
backups, and about this installation.

Each tab is a page of its own (``/settings/reading``, ``/settings/automation``...), so a tab can be linked to and
Back works; ``/settings`` is the Reading tab. The Reading, Automation and JD Edwards tabs are in their own modules
(``settings_reading.py``, ``settings_automation.py``, ``settings_jde.py``), included here.

Values that belong to the computer (review threshold, Azure endpoints...) are written to the ``.env`` with the same
functions as before (``ap_coder.envfile``) and applied to the running dashboard at once; values that belong to the
data (approval limit, exchange rates, backups...) are kept in the database's settings. Every change is logged.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

import fastapi
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ap_coder import __version__, paths, ui
from ap_coder.envfile import read_env, write_env
from ap_coder.store import Store, unread_fx_text
from ap_coder.tax import PROVINCE_NAMES
from ap_coder.web.core import (
    data_dir,
    done,
    get_settings,
    get_store,
    invoice_dir,
    message,
    open_folder,
    refuse,
    render,
    reviewer,
)

router = APIRouter()

# (key, label) in the order of the tab bar; /settings/<key> is each tab.
TABS: list[tuple[str, str]] = [
    ("reading", "Reading"),
    ("automation", "Automation"),
    ("review", "Review"),
    ("jde", "JD Edwards E1"),
    ("data", "Data & backups"),
    ("about", "About"),
]
TAB_KEYS = [key for key, _ in TABS]
RESTORE_WORD = "RESTORE"


def tab_url(tab: str) -> str:
    return f"/settings/{tab}"


def render_tab(request: Request, tab: str, **context: Any) -> HTMLResponse:
    """The Settings page showing ``tab`` (its template is ``settings_<tab>.html``)."""
    return render(request, "settings.html", "settings", tab=tab, tabs=TABS, **context)


# --- The .env ------------------------------------------------------------------------------------------------------


def env_path() -> Path:
    """The .env being used, or where a new one goes (the data folder)."""
    return paths.env_file() or paths.private_dir() / ".env"


def save_env(store: Store, updates: dict[str, str]) -> list[str]:
    """Write changed values to the .env and apply them to this running dashboard (it reads its settings from the
    environment). Logged as ``settings_changed``; a reviewer name change records both names. Returns changed keys."""
    current = read_env(env_path())
    changed = {k: v for k, v in updates.items() if v != current.get(k, "")}
    if changed:
        detail: dict[str, object] = {"keys": sorted(changed)}
        if "AP_REVIEWER" in changed:  # a name change is recorded under the old name, with both names
            detail["reviewer"] = {"from": reviewer(), "to": changed["AP_REVIEWER"]}
        store.log_event("settings_changed", actor=reviewer(), detail=detail)
        write_env(env_path(), changed)
        os.environ.update(changed)
    return sorted(changed)


def saved_line(changed: list[str]) -> str:
    return f"Saved {ui.plural(len(changed), 'change')}." if changed else "Nothing changed."


# --- The tabs ------------------------------------------------------------------------------------------------------


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    from ap_coder.web.pages.settings_reading import reading_context

    return render_tab(request, "reading", **reading_context(request))


@router.get("/settings/{tab}", response_class=HTMLResponse)
def settings_tab(request: Request, tab: str) -> HTMLResponse:
    if tab not in TAB_KEYS:
        raise HTTPException(status_code=404, detail="Settings has no such tab.")
    if tab == "reading":
        from ap_coder.web.pages.settings_reading import reading_context

        return render_tab(request, tab, **reading_context(request))
    if tab == "automation":
        from ap_coder.web.pages.settings_automation import automation_context

        return render_tab(request, tab, **automation_context(get_store(request)))
    if tab == "jde":
        from ap_coder.web.pages.settings_jde import jde_context

        return render_tab(request, tab, **jde_context(get_store(request)))
    if tab == "review":
        return render_tab(request, tab, **review_context(request))
    if tab == "data":
        return render_tab(request, tab, **data_context(get_store(request)))
    return render_tab(request, tab, **about_context(get_store(request)))


# --- Review --------------------------------------------------------------------------------------------------------


def review_context(request: Request) -> dict[str, Any]:
    settings, store = get_settings(request), get_store(request)
    env = read_env(env_path())
    name = paths.read_user_settings().get("reviewer") or env.get("AP_REVIEWER") or os.environ.get("AP_REVIEWER", "")
    return {
        "reviewer_name": str(name or ""),
        "threshold": round(float(settings.engine.review_threshold) * 100),
        "constrain": bool(settings.engine.constrain_codes),
        "approval_limit": store.approval_limit(),
        "terms_days": store.default_terms_days(),
        "fx_rates": store.get_setting("fx_rates"),
        "our_province": store.our_province(),
        "site_provinces": store.get_setting("site_provinces"),
        "provinces": PROVINCE_NAMES,
    }


@router.post("/settings/review")
def save_review(
    request: Request, reviewer_name: str = Form(""), threshold: str = Form("80"), constrain: str = Form("")
) -> JSONResponse:
    settings, store = get_settings(request), get_store(request)
    try:
        percent = int(float(threshold))
    except (ValueError, OverflowError):  # int(float("1e309")) overflows
        return refuse("The confidence threshold must be a number from 50 to 99.")
    if not 50 <= percent <= 99:
        return refuse("The confidence threshold must be from 50% to 99%.")
    name = reviewer_name.strip()
    changed: list[str] = []
    if name and name != reviewer():  # logged under the old name, with both names
        rename = {"from": reviewer(), "to": name}
        store.log_event("settings_changed", actor=reviewer(), detail={"keys": ["reviewer"], "reviewer": rename})
        paths.write_user_settings(reviewer=name)
        changed.append("reviewer")
    elif not name and paths.read_user_settings().get("reviewer"):  # cleared: back to AP_REVIEWER or the login
        saved = reviewer()
        paths.write_user_settings(reviewer="")
        rename = {"from": saved, "to": reviewer()}
        store.log_event("settings_changed", actor=saved, detail={"keys": ["reviewer"], "reviewer": rename})
        changed.append("reviewer")
    # Only what differs from the values in effect: a default is not written to the .env as a "change".
    updates = {}
    new_threshold = percent / 100
    if round(new_threshold, 2) != round(float(settings.engine.review_threshold), 2):
        updates["AP_REVIEW_THRESHOLD"] = f"{new_threshold:.2f}"
    on = constrain in ("1", "on", "true")
    if on != settings.engine.constrain_codes:
        updates["AP_CONSTRAIN_CODES"] = "true" if on else "false"
    changed = save_env(store, updates) + changed
    return done(request, tab_url("review"), saved_line(changed))


@router.post("/settings/review/approval")
def save_approval(
    request: Request, approval_limit: str = Form("0"), terms_days: str = Form("30"), fx_rates: str = Form("")
) -> JSONResponse:
    store = get_store(request)
    try:
        limit = round(float(approval_limit or 0), 2)
        days = int(float(terms_days or 0))
        if not math.isfinite(limit):  # "1e309" or "nan" posted directly
            raise ValueError(approval_limit)
    except (ValueError, OverflowError):  # int(float("1e309")) overflows
        return refuse("The approval limit and the days to pay must be numbers.")
    if limit < 0:
        return refuse("The approval limit can't be below 0 (0 = never a second approval).")
    if not 0 <= days <= 180:
        return refuse("The days to pay must be from 0 to 180.")
    unread = unread_fx_text(fx_rates)
    if unread:  # saved as it was, a rate typed the wrong way round would be ignored without a word
        return refuse(f"Exchange rates not saved: “{unread}” can't be read. Write each one as a currency code, then "
                      "the CAD for one unit, e.g. USD=1.37, EUR=1.50.")  # fmt: skip
    changed = []
    if fx_rates.strip() != store.get_setting("fx_rates"):
        store.set_setting("fx_rates", fx_rates.strip(), actor=reviewer())
        changed.append("fx_rates")
    if days != store.default_terms_days():
        store.set_setting("default_terms_days", str(days), actor=reviewer())
        changed.append("default_terms_days")
    if limit != store.approval_limit():
        store.set_setting("approval_limit", f"{limit:.2f}", actor=reviewer())
        changed.append("approval_limit")
    if changed:
        store.log_event("settings_changed", actor=reviewer(), detail={"keys": changed})
    return done(request, tab_url("review"), "Saved." if changed else "Nothing changed.")


@router.post("/settings/review/supply")
def save_supply(request: Request, our_province: str = Form(""), site_provinces: str = Form("")) -> JSONResponse:
    """Where invoices are delivered to us: the place of supply of an invoice that prints no ship-to."""
    store = get_store(request)
    our_province = our_province.strip().upper()
    if our_province and our_province not in PROVINCE_NAMES:
        return refuse("Choose a province from the list.")
    lines = [line.strip() for line in re.split(r"[\n,;]+", site_provinces) if line.strip()]
    bad = [line for line in lines if line.partition("=")[2].strip().upper() not in PROVINCE_NAMES
           or not line.partition("=")[0].strip()]  # fmt: skip
    if bad:
        return refuse(f"Not saved: “{bad[0]}” can't be read. Write one cost center per line, then = and its province "
                      "code, e.g. CC400=BC.")  # fmt: skip
    sites = "\n".join(f"{line.partition('=')[0].strip()}={line.partition('=')[2].strip().upper()}" for line in lines)
    changed = []
    if our_province != store.our_province():
        store.set_setting("our_province", our_province, actor=reviewer())
        changed.append("our_province")
    if sites != store.get_setting("site_provinces"):
        store.set_setting("site_provinces", sites, actor=reviewer())
        changed.append("site_provinces")
    if changed:
        store.log_event("settings_changed", actor=reviewer(), detail={"keys": changed})
    saved = "Saved. Invoices read from now on use it." if changed else "Nothing changed."
    return done(request, tab_url("review"), saved)


# --- Data & backups ------------------------------------------------------------------------------------------------


def size_text(path: Path) -> str:
    size = path.stat().st_size if path.exists() else 0
    return f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"


def copy_status(store: Store) -> dict[str, str] | None:
    """The last copy to the second backup folder: {ok, when, what}, or None when there is none to show."""
    status = store.get_setting("backup_copy_status")
    if not (store.get_setting("backup_copy_dir") and status):
        return None
    state, _, rest = status.partition(" ")
    when, _, what = rest.partition(": ")
    return {"ok": "yes" if state == "ok" else "", "when": when.replace("T", " "), "what": what}


def data_context(store: Store) -> dict[str, Any]:
    folder = invoice_dir(store)
    backups = store.list_backups()
    return {
        "data_path": str(data_dir(store)),
        "db_size": size_text(store.path),
        "invoice_count": len(store.list_invoices()),
        "file_count": len(list(folder.glob("*"))) if folder.exists() else 0,
        "lesson_count": len(store.feedback_rows()),
        "backups": [
            {
                "name": b.name,
                "made": dt.datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "size": size_text(b),
            }
            for b in backups
        ],
        "copy_dir": store.get_setting("backup_copy_dir"),
        "copy_status": copy_status(store),
        "restore_word": RESTORE_WORD,
    }


def backup_path(store: Store, name: str) -> Path:
    """The backup called ``name`` in the backups folder (404 for any other name: never a path)."""
    for path in store.list_backups():
        if path.name == name:
            return path
    raise HTTPException(status_code=404, detail="No such backup.")


@router.post("/settings/data/open-folder")
def open_data_folder(request: Request) -> JSONResponse:
    folder = data_dir(get_store(request))
    if open_folder(folder):
        return message("Opened the data folder.", "info")
    return message(f"Open this folder yourself: {folder}", "info")


@router.post("/settings/data/backup")
def backup_now(request: Request) -> JSONResponse:
    store = get_store(request)
    try:
        made = store.backup_now("manual", actor=reviewer())
    except OSError as exc:
        return refuse(f"The backup could not be made: {exc}")
    return done(request, tab_url("data"), f"Backed up to {made.name}.")


@router.post("/settings/data/backup-copy")
def save_backup_copy(request: Request, folder: str = Form("")) -> JSONResponse:
    store = get_store(request)
    folder = folder.strip().strip('"')
    if folder == store.get_setting("backup_copy_dir"):
        return done(request, tab_url("data"), "Nothing changed.")
    try:  # a name the computer refuses (too long, a character it does not allow) is a folder that does not exist
        exists = Path(folder).expanduser().is_dir() if folder else True
    except (OSError, ValueError):
        exists = False
    if not exists:
        return refuse("That folder does not exist (or is not reachable from this computer).")
    if folder and Path(folder).expanduser().resolve() == store.backup_dir().resolve():
        return refuse("That is the local backups folder: choose a folder on OneDrive or a network drive.")
    store.set_setting("backup_copy_dir", folder, actor=reviewer())
    store.log_event("settings_changed", actor=reviewer(), detail={"keys": ["backup_copy_dir"]})
    if folder:
        store.backup_now()  # a first copy straight away (backup_now copies it)
    said = "Backups will be copied there too." if folder else "Backups are kept on this computer only."
    return done(request, tab_url("data"), said)


@router.get("/settings/data/backups/{name}")
def download_backup(request: Request, name: str) -> FileResponse:
    path = backup_path(get_store(request), name)
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@router.post("/settings/data/restore")
def restore_backup(request: Request, name: str = Form(""), confirm: str = Form("")) -> JSONResponse:
    store = get_store(request)
    if confirm.strip().upper() != RESTORE_WORD:
        return refuse(f"Type {RESTORE_WORD} to confirm: nothing was restored.")
    if not name:
        return refuse("Choose a backup to restore.")
    try:
        path = backup_path(store, name)
    except HTTPException:
        return refuse("That backup is not in the backups folder any more.", status=404)
    had_copy_dir = bool(store.get_setting("backup_copy_dir").strip())
    try:
        safety = store.restore_from(path)
    except ValueError as exc:  # not an AP Coder backup: nothing was changed
        return refuse(f"Not restored: {exc}.")
    from ap_coder.web.pages.invoice import forget_all_drafts

    forget_all_drafts(request)  # edits made on the replaced invoices would open on the restored ones
    cleared = " The second backup folder was cleared: set it again below." if had_copy_dir else ""
    said = f"Restored {name}. The previous database was saved as {safety.name}.{cleared}"
    return done(request, tab_url("data"), said)


# --- About ---------------------------------------------------------------------------------------------------------


def git_version() -> str:
    if not (paths.PROJECT_DIR / ".git").exists():
        return ""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h · %cd", "--date=format:%Y-%m-%d %H:%M"],  # noqa: S607
            cwd=paths.PROJECT_DIR, capture_output=True, text=True, timeout=5, check=False,
        )  # fmt: skip
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def about_context(store: Store) -> dict[str, Any]:
    git = git_version()
    return {
        "about": [
            ("Version", __version__ + (f" · {git}" if git else ""), False),
            ("Code folder", str(paths.PROJECT_DIR), True),
            ("Data folder", str(data_dir(store)), True),
            ("Settings file", str(env_path()), True),
            ("Python", platform.python_version(), False),
            ("FastAPI", fastapi.__version__, False),
            ("Computer", f"{platform.system()} {platform.release()}", False),
        ]
    }


# The other tabs' routes (their modules import nothing from this one at import time but the helpers above).
def _include_tabs() -> None:
    from ap_coder.web.pages import settings_automation, settings_jde, settings_reading

    for module in (settings_reading, settings_automation, settings_jde):
        router.include_router(module.router)


_include_tabs()
