"""Settings: how invoices are read, touchless processing, review behaviour, JD Edwards E1, the finance hub feed, the
data folder and its backups, and about this installation.

Each tab is a page of its own (``/settings/review``, ``/settings/reading``...), so a tab can be linked to and
Back works; ``/settings`` is the Review tab, what a pilot tester changes (B-506). The JD Edwards E1 tab shows only
once Settings → About's "Show JD Edwards settings" is on (``/settings/jde`` still opens, and says so). The Reading,
Automation, JD Edwards and Finance hub tabs are in their own modules (``settings_reading.py``,
``settings_automation.py``, ``settings_jde.py``, ``settings_hub.py``), included here.

Values that belong to the computer (review threshold, Azure endpoints...) are written to the ``.env`` with the same
functions as before (``ap_coder.core.envfile``) and applied to the running dashboard at once; values that belong to the
data (approval limit, exchange rates, backups...) are kept in the database's settings. Every change is logged.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import platform
import re
from pathlib import Path
from typing import Any

import fastapi
from fastapi import Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ap_coder import __version__
from ap_coder.ai import lessons
from ap_coder.coding.tax import PROVINCE_NAMES
from ap_coder.core import paths, ui
from ap_coder.core.envfile import one_line, read_env, write_env
from ap_coder.core.store import APPROVAL_CONTROLS, Store, tiny_fx_rates, unread_fx_text
from ap_coder.web import identity
from ap_coder.web.core import (
    DEVELOPER_TOOLS,
    SHOW_JDE,
    STALE_PAGE,
    data_dir,
    developer_env_on,
    developer_tools,
    done,
    get_settings,
    get_store,
    invoice_dir,
    jde_shown,
    open_folder_here,
    page_router,
    page_version,
    posted_form,
    refuse,
    render,
    reviewer,
    server_mode,
)

router = page_router()  # each plain handler: one connection a request

# (key, label) in the order of the tab bar; /settings/<key> is each tab.
TABS: list[tuple[str, str]] = [
    ("review", "Review"),
    ("reading", "Reading"),
    ("automation", "Automation"),
    ("jde", "JD Edwards E1"),
    ("data", "Data & backups"),
    ("about", "About"),
    ("hub", "Finance hub"),  # the feed for the finance hub on Microsoft Fabric, an Admin's (D-103)
]
TAB_KEYS = [key for key, _ in TABS]
RESTORE_WORD = "RESTORE"


def tab_url(tab: str) -> str:
    return f"/settings/{tab}"


def shown_tabs(store: Store) -> list[tuple[str, str]]:
    """The tab bar: every tab but JD Edwards E1 while it is hidden (the pilot exports to Excel and CSV only)."""
    return [(key, label) for key, label in TABS if key != "jde" or jde_shown(store)]


def render_tab(request: Request, tab: str, **context: Any) -> HTMLResponse:
    """The Settings page showing ``tab`` (its template is ``settings_<tab>.html``)."""
    return render(request, "settings.html", "settings", tab=tab, tabs=shown_tabs(get_store(request)), **context)


# --- The .env ------------------------------------------------------------------------------------------------------


def env_path() -> Path:
    """The .env being used, or where a new one goes (the data folder)."""
    return paths.env_file() or paths.private_dir() / ".env"


def save_env(store: Store, updates: dict[str, str]) -> list[str]:
    """Write changed values to the .env and apply them to this running dashboard (it reads its settings from the
    environment). Logged as ``settings_changed``; a reviewer name change records both names. Returns changed keys."""
    current = read_env(env_path())
    updates = {k: one_line(v) for k, v in updates.items()}
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
    return render_tab(request, "review", **review_context(request))


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

        return render_tab(request, tab, jde_hidden=not jde_shown(get_store(request)), **jde_context(get_store(request)))
    if tab == "review":
        return render_tab(request, tab, **review_context(request))
    if tab == "hub":
        from ap_coder.web.pages.settings_hub import hub_context

        return render_tab(request, tab, **hub_context(request))
    if tab == "data":
        return render_tab(request, tab, **data_context(get_store(request)))
    return render_tab(request, tab, **about_context(get_store(request)))


# --- Review --------------------------------------------------------------------------------------------------------


def review_version(request: Request) -> str:
    """The stamp of the values the Review tab shows (B-655)."""
    shown = review_context(request, stamped=False)
    return page_version({k: v for k, v in shown.items() if k not in ("provinces", "uses_cost_centers")})


def review_context(request: Request, stamped: bool = True) -> dict[str, Any]:
    settings, store = get_settings(request), get_store(request)
    env = read_env(env_path())
    name = paths.read_user_settings().get("reviewer") or env.get("AP_REVIEWER") or os.environ.get("AP_REVIEWER", "")
    colleague = identity.person()  # on the office network: their own name, changed on the Who are you? page
    return {
        "colleague": colleague is not None,
        "reviewer_name": colleague.name if colleague else str(name or ""),
        "threshold": round(float(settings.engine.review_threshold) * 100),
        "constrain": bool(settings.engine.constrain_codes),
        "approval_limit": store.approval_limit(),
        "poster_not_approver": store.poster_not_approver(),
        "terms_days": store.default_terms_days(),
        "fx_rates": store.get_setting("fx_rates"),
        "our_province": store.our_province(),
        "why_after": lessons.why_after(store),
        "site_provinces": store.get_setting("site_provinces"),
        "uses_cost_centers": bool(store.list_accounts("cost_centers")),
        "provinces": PROVINCE_NAMES,
        **({"version": review_version(request)} if stamped else {}),
    }


@router.post("/settings/review")
async def save_review(request: Request) -> JSONResponse:
    """The Review tab's one form, one Save (B-507): your name, approval and payment, where invoices are delivered to
    you, and (under Advanced) the AI's threshold, the GL list rule and when to ask why. A field the form didn't post
    is left as it is (read from the raw form: a field posted blank, a name cleared, is not one left out); the message
    says what was saved."""
    form = await posted_form(request)
    if form.get("version") is not None and str(form.get("version")) != review_version(request):
        return refuse(STALE_PAGE, 409)  # an old tab would save its old values over newer ones (B-655)

    def field(name: str) -> str | None:
        value = form.get(name)
        return None if value is None else str(value)

    reviewer_name, threshold, constrain = field("reviewer_name"), field("threshold"), field("constrain") or ""
    approval_limit, terms_days, fx_rates = field("approval_limit"), field("terms_days"), field("fx_rates")
    why_after, our_province, site_provinces = field("why_after"), field("our_province"), field("site_provinces")
    name_part = _review_part(request, reviewer_name, threshold, constrain)
    if isinstance(name_part, JSONResponse):
        return name_part
    approval_part = _approval_part(request, approval_limit, terms_days, fx_rates, why_after)
    if isinstance(approval_part, JSONResponse):
        return approval_part
    supply_part = _supply_part(request, our_province, site_provinces)
    if isinstance(supply_part, JSONResponse):
        return supply_part
    saved = name_part() + approval_part() + supply_part() + _duties_part(request, form)
    if saved:
        from ap_coder.web.pages.help import forget_setup_report

        forget_setup_report()
    return done(request, tab_url("review"), saved_text(saved))


# What each saved setting is called in the message after Save, and whether it applies to invoices read from now on.
SAVED_WORDS = {
    "reviewer": ("your name", False),
    "AP_REVIEW_THRESHOLD": ("the confidence threshold", True),
    "AP_CONSTRAIN_CODES": ("the GL account list rule", True),
    "approval_limit": ("the second-approval limit", False),
    "poster_not_approver": ("who may post to JD Edwards", False),
    "default_terms_days": ("the days to pay", False),
    "fx_rates": ("the exchange rates", False),
    lessons.SETTING: ("when to ask why", False),
    "our_province": ("your province", True),
    "site_provinces": ("the sites in other provinces", True),
}


# Not a setting: the approval limit was raised, said after what was saved (B-1187).
LIMIT_RAISED = "approval_limit_raised"
LIMIT_RAISED_TEXT = ("Invoices already in AP Coder keep the strictest limit in force since they arrived: the higher "
                     "limit applies to invoices that come in from now on.")  # fmt: skip


def saved_text(changed: list[str]) -> str:
    """What was saved, in words (B-507): "Saved your name." / "Saved your province: invoices read from now on use
    it." / "Saved your name and your province; your province applies to invoices read from now on." / "Nothing
    changed." A raised approval limit (``LIMIT_RAISED``) says the invoices already in keep the strictest (B-970)."""
    if LIMIT_RAISED in changed:
        return f"{saved_text([key for key in changed if key != LIMIT_RAISED])} {LIMIT_RAISED_TEXT}"
    if not changed:
        return "Nothing changed."
    words = [SAVED_WORDS.get(key, (key, False)) for key in changed]

    def listed(names: list[str]) -> str:
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

    later = [w for w, applies in words if applies]
    if not later:
        return f"Saved {listed([w for w, _ in words])}."
    if len(later) == len(words):
        return f"Saved {listed(later)}: invoices read from now on use {'it' if len(later) == 1 else 'them'}."
    apply = "applies" if len(later) == 1 else "apply"
    return f"Saved {listed([w for w, _ in words])}; {listed(later)} {apply} to invoices read from now on."


def _review_part(request: Request, reviewer_name: str | None, threshold: str | None, constrain: str) -> Any:
    """Your name, and the AI's threshold and GL list rule: a refusal, or what saves them (it returns the keys saved)."""
    settings, store = get_settings(request), get_store(request)
    percent = None
    if threshold is not None:
        try:
            percent = int(float(threshold))
        except (ValueError, OverflowError):  # int(float("1e309")) overflows
            return refuse("The confidence threshold must be a number from 50 to 99.")
        if not 50 <= percent <= 99:
            return refuse("The confidence threshold must be from 50% to 99%.")
    name = None if reviewer_name is None else reviewer_name.strip()
    if name is not None and identity.person() is not None:  # a colleague's name is never the owner's saved one
        if name and name != reviewer():
            if identity.person().user_id:  # server mode (D-97)
                return refuse("Your name is your account's: it is the one on every invoice you approve.")
            return refuse("Your name is the one this browser gave AP Coder: change it on the Who are you? page (/who).")
        name = reviewer()

    def save() -> list[str]:
        changed: list[str] = []
        if name and name != reviewer():  # logged under the old name, with both names
            rename = {"from": reviewer(), "to": name}
            store.log_event("settings_changed", actor=reviewer(), detail={"keys": ["reviewer"], "reviewer": rename})
            paths.write_user_settings(reviewer=name)
            changed.append("reviewer")
        elif name == "" and paths.read_user_settings().get("reviewer"):  # cleared: back to AP_REVIEWER or the login
            saved = reviewer()
            paths.write_user_settings(reviewer="")
            rename = {"from": saved, "to": reviewer()}
            store.log_event("settings_changed", actor=saved, detail={"keys": ["reviewer"], "reviewer": rename})
            changed.append("reviewer")
        # Only what differs from the values in effect: a default is not written to the .env as a "change". The
        # GL list rule is a checkbox: with the threshold posted (the form), unticked means off.
        updates = {}
        if percent is not None:
            new_threshold = percent / 100
            if round(new_threshold, 2) != round(float(settings.engine.review_threshold), 2):
                updates["AP_REVIEW_THRESHOLD"] = f"{new_threshold:.2f}"
            on = constrain in ("1", "on", "true")
            if on != settings.engine.constrain_codes:
                updates["AP_CONSTRAIN_CODES"] = "true" if on else "false"
        return changed + save_env(store, updates)

    return save


def _duties_part(request: Request, form: Any) -> list[str]:
    """The server's separation of duties (D-109): "the person who posts to JD Edwards can't be one who approved",
    saved when the form shows it (a checkbox not ticked posts nothing)."""
    store = get_store(request)
    if not server_mode(request) or form.get("duties_shown") is None:
        return []
    on = str(form.get("poster_not_approver") or "") == "1"
    if on == store.poster_not_approver():
        return []
    store.set_setting("poster_not_approver", "1" if on else "0", actor=reviewer())
    store.log_event("settings_changed", actor=reviewer(), detail={"keys": ["poster_not_approver"]})
    return ["poster_not_approver"]


def _approval_part(
    request: Request, approval_limit: str | None, terms_days: str | None, fx_rates: str | None, why_after: str | None
) -> Any:
    """Approval and payment: a refusal, or what saves them (it returns the keys saved)."""
    store = get_store(request)
    try:
        limit = None if approval_limit is None else round(float(approval_limit or 0), 2)
        days = None if terms_days is None else int(float(terms_days or 0))
        why = int(float(why_after)) if why_after and why_after.strip() else None
        if limit is not None and not math.isfinite(limit):  # "1e309" or "nan" posted directly
            raise ValueError(approval_limit)
    except (ValueError, OverflowError):  # int(float("1e309")) overflows
        return refuse("The approval limit and the days to pay must be numbers.")
    if limit is not None and limit < 0:
        return refuse("The approval limit can't be below 0 (0 = never a second approval).")
    if days is not None and not 0 <= days <= 180:
        return refuse("The days to pay must be from 0 to 180.")
    if why is not None and not 1 <= why <= 20:
        return refuse("Ask why after 1 to 20 corrections of the same thing.")
    tiny = tiny_fx_rates(fx_rates or "")
    if tiny:  # "USD=0" was saved and ignored; "USD=0.0001" let every USD invoice skip the second approval (B-971)
        return refuse(
            f"Exchange rates not saved: {', '.join(tiny)} is not a believable rate (the CAD for one unit "
            "can't be 0 or a tiny fraction of a cent). Check it and type it again, e.g. USD=1.37."
        )
    unread = unread_fx_text(fx_rates or "")
    if unread:  # saved as it was, a rate typed the wrong way round would be ignored without a word
        return refuse(f"Exchange rates not saved: “{unread}” can't be read. Write each one as a currency code, then "
                      "the CAD for one unit, e.g. USD=1.37, EUR=1.50.")  # fmt: skip

    def save() -> list[str]:
        changed = []
        if fx_rates is not None and fx_rates.strip() != store.get_setting("fx_rates"):
            store.set_setting("fx_rates", fx_rates.strip(), actor=reviewer())
            changed.append("fx_rates")
        if days is not None and days != store.default_terms_days():
            store.set_setting("default_terms_days", str(days), actor=reviewer())
            changed.append("default_terms_days")
        if why is not None and why != lessons.why_after(store):
            store.set_setting(lessons.SETTING, str(why), actor=reviewer())
            changed.append(lessons.SETTING)
        before = store.approval_limit()
        if limit is not None and limit != before:
            store.set_setting("approval_limit", f"{limit:.2f}", actor=reviewer())
            changed.append("approval_limit")
        others = [key for key in changed if key not in APPROVAL_CONTROLS]  # those the store logs with old and new
        if others:
            store.log_event("settings_changed", actor=reviewer(), detail={"keys": others})
        if "approval_limit" in changed and before and (not limit or limit > before):
            changed.append(LIMIT_RAISED)
        return changed

    return save


def _supply_part(request: Request, our_province: str | None, site_provinces: str | None) -> Any:
    """Where invoices are delivered to us (the place of supply of an invoice that prints no ship-to): a refusal, or
    what saves it (it returns the keys saved)."""
    store = get_store(request)
    province = None if our_province is None else our_province.strip().upper()
    if province and province not in PROVINCE_NAMES:
        return refuse("Choose a province from the list.")
    sites = None
    if site_provinces is not None:
        lines = [line.strip() for line in re.split(r"[\n,;]+", site_provinces) if line.strip()]
        bad = [line for line in lines if line.partition("=")[2].strip().upper() not in PROVINCE_NAMES
               or not line.partition("=")[0].strip()]  # fmt: skip
        if bad:
            return refuse(f"Not saved: “{bad[0]}” can't be read. Write one cost center per line, then = and its "
                          "province code, e.g. CC400=BC.")  # fmt: skip
        pairs = [(line.partition("=")[0].strip(), line.partition("=")[2].strip().upper()) for line in lines]
        sites = "\n".join(f"{code}={where}" for code, where in pairs)

    def save() -> list[str]:
        changed = []
        if province is not None and province != store.our_province():
            store.set_setting("our_province", province, actor=reviewer())
            changed.append("our_province")
        if sites is not None and sites != store.get_setting("site_provinces"):
            store.set_setting("site_provinces", sites, actor=reviewer())
            changed.append("site_provinces")
        if changed:
            store.log_event("settings_changed", actor=reviewer(), detail={"keys": changed})
        return changed

    return save


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
        "invoice_count": sum(store.status_counts().values()),
        "file_count": len(list(folder.glob("*"))) if folder.exists() else 0,
        "lesson_count": f"{store.metrics()['lines_reviewed']:,}",  # one definition: AP's own reviewed lines (B-532)
        "history_count": store.history_count(),  # past coding imported from the ERP, counted apart
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
    return open_folder_here(request, data_dir(get_store(request)), "data folder")


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
    from ap_coder.ops.service import folder_refusal

    refused = folder_refusal(folder, server=server_mode(request))  # before the folder is asked anything (D-109)
    if refused:
        return refuse(refused)
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
        safety = store.restore_from(path, keep_accounts=server_mode(request))  # (D-101)
    except ValueError as exc:  # not an AP Coder backup: nothing was changed
        return refuse(f"Not restored: {exc}.")
    except OSError as exc:  # the safety copy of the current database could not be made (a full disk): nothing changed
        return refuse(f"Not restored: {exc}.")
    from ap_coder.web.pages.invoice import forget_all_drafts

    forget_all_drafts(request)  # edits made on the replaced invoices would open on the restored ones
    cleared = " The second backup folder was cleared: set it again below." if had_copy_dir else ""
    said = f"Restored {name}. The previous database was saved as {safety.name}.{cleared}"
    if server_mode(request):  # every session ended, this one too: the accounts stayed as they were
        said += " The user accounts are as they were; everyone is logged out: log in again."
        return done(request, f"/login?next={tab_url('data')}", said)
    return done(request, tab_url("data"), said)


# --- About ---------------------------------------------------------------------------------------------------------


def build_version() -> str:
    """``build 3dacb6f, 2026-10-21 20:14``: which build this is (GitHub's ZIP, the offline bundle or a checkout), so
    two copies of the same version can be told apart ("" when nothing is known)."""
    return paths.build_label(paths.install_identity(paths.PROJECT_DIR))


def about_context(store: Store) -> dict[str, Any]:
    build = build_version()
    return {
        "about": [
            ("Version", __version__ + (f" · {build}" if build else ""), False),
            ("Code folder", str(paths.PROJECT_DIR), True),
            ("Data folder", str(data_dir(store)), True),
            ("Settings file", str(env_path()), True),
            ("Python", platform.python_version(), False),
            ("FastAPI", fastapi.__version__, False),
            ("Computer", f"{platform.system()} {platform.release()}", False),
        ],
        "show_jde": jde_shown(store),
        "developer_tools": developer_tools(store),
        "developer_env": developer_env_on(),
    }


@router.post("/settings/about/show")
def save_shown(request: Request, show_jde: str = Form(""), developer: str = Form("")) -> JSONResponse:
    """What this pilot shows (D-52): the JD Edwards E1 settings and export, and the developer tools. Logged."""
    store = get_store(request)
    changed, keys = [], []
    switches = [(SHOW_JDE, show_jde, "JD Edwards settings", jde_shown(store))]
    if not developer_env_on():  # set by AP_DEVELOPER_TOOLS: shown as on, never stored (B-527)
        switches.append((DEVELOPER_TOOLS, developer, "developer tools", developer_tools(store)))
    for key, value, words, was in switches:
        on = value in ("1", "on", "true")
        if on != was:
            store.set_setting(key, "1" if on else "0", actor=reviewer())
            changed.append(f"{words} {'shown' if on else 'hidden'}")
            keys.append(key)
    if keys:  # only the switches that changed
        store.log_event("settings_changed", actor=reviewer(), detail={"keys": keys})
    return done(request, tab_url("about"), ("Saved: " + ", ".join(changed) + ".") if changed else "Nothing changed.")


# The other tabs' routes (their modules import nothing from this one at import time but the helpers above).
def _include_tabs() -> None:
    from ap_coder.web.pages import settings_automation, settings_hub, settings_jde, settings_reading

    for module in (settings_reading, settings_automation, settings_jde, settings_hub):
        router.include_router(module.router)


_include_tabs()
