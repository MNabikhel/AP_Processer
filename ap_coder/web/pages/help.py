"""Help: getting started, how every invoice is read, the AP routine, what every check means and what to do about it
(``ap_coder.ops.help.CHECKS``, searchable by word and area), questions, shortcuts and where the data is. The search and
the area are in the address (``/help?q=duplicate&area=Sales+tax``), so a filtered list can be linked to; the page also
filters as you type.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ap_coder.core import paths
from ap_coder.ops.help import AREAS, CHECKS, FAQ, QUICK_START, ROUTINE, CheckHelp
from ap_coder.web import minimd
from ap_coder.web.core import data_dir, get_store, render, short_path
from ap_coder.web.pages import BY_KEY
from ap_coder.web.pages.settings_reading import HOW_IT_READS

router = APIRouter()

STEP_PAGES = ["accounts", "purchase_orders", "process", "review", "exports"]
SHORTCUTS = [
    (("Ctrl", "Enter"), "Approve & teach the open invoice"),
    (("Alt", "→"), "Next invoice"),
    (("Alt", "←"), "Previous invoice"),
    (("Alt", "↑"), "Back to the queue"),
]


def matching_checks(query: str = "", area: str | None = None) -> list[tuple[str, CheckHelp]]:
    """The check catalog filtered by a search text (code, title, meaning or action) and an area."""
    pattern = re.compile(re.escape(query.strip()), re.IGNORECASE) if query.strip() else None
    out = []
    for code, about in CHECKS.items():
        if area and about.area != area:
            continue
        if pattern and not pattern.search(" ".join((code, about.title, about.meaning, about.action))):
            continue
        out.append((code, about))
    return out


def whats_new() -> Any:
    """docs/WHATS_NEW.md without its own title, as HTML ("" when the file isn't there)."""
    news = paths.PROJECT_DIR / "docs" / "WHATS_NEW.md"
    if not news.is_file():
        return ""
    text = news.read_text(encoding="utf-8")
    text = text.split("\n", 1)[1] if text.startswith("# ") else text
    return minimd.render(text, top=5)


def setup_report(store: Any) -> str:
    """The doctor's report (``python -m ap_coder doctor``, offline, without asking LM Studio): safe to paste."""
    from ap_coder.core.config import Settings
    from ap_coder.ops.cli import _load_reference, build_parser
    from ap_coder.ops.doctor import format_checks, run_checks

    args = build_parser().parse_args(["--db", str(store.path), "doctor"])
    try:
        checks = run_checks(Settings.from_env(), lambda: _load_reference(args), online=False, llm_checks=False)
    except Exception as exc:  # noqa: BLE001 - the page that reports problems must not be one
        return f"## AP Coder doctor report\n\nThe checks could not run: {type(exc).__name__}: {exc}"
    return format_checks(checks)


def activity_report(store: Any, include_codes: bool) -> str:
    """The share report (``python -m ap_coder share-report``): counts and accuracy, no vendor names, amounts or
    file names. Invoices are doc-01, doc-02...; which file each one is stays in the data folder's key file."""
    from ap_coder.ops.share_report import build_share_report

    data = data_dir(store)
    try:
        return build_share_report(data / "output", None, include_codes, key_file=data / "share_report_key.csv",
                                  db_path=store.path)  # fmt: skip
    except FileNotFoundError:
        return ""
    except Exception as exc:  # noqa: BLE001
        return f"## AP Coder run summary\n\nThe summary could not be made: {type(exc).__name__}: {exc}"


@router.get("/help/report", response_class=HTMLResponse)
def report_page(request: Request, codes: bool = False) -> HTMLResponse:
    """Report a problem: what to write, and the two reports to copy (no command line)."""
    store = get_store(request)
    logs = paths.user_settings_path().parent / "logs"
    return render(
        request,
        "help_report.html",
        "help",
        title="Report a problem",
        setup=setup_report(store),
        activity=activity_report(store, codes),
        codes=codes,
        key_file=short_path(data_dir(store) / "share_report_key.csv"),
        logs=[short_path(logs / "launch.log"), short_path(logs / "dashboard-errors.log")],
    )


def link(key: str) -> dict[str, str] | None:
    page = BY_KEY.get(key)
    return {"path": page.path, "title": page.title} if page else None


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request, q: str = "", area: str = "") -> HTMLResponse:
    store = get_store(request)
    area = area if area in AREAS else ""
    checks = matching_checks(q, area or None)
    data = data_dir(store)
    return render(
        request,
        "help.html",
        "help",
        news=whats_new(),
        quick_start=[(title, text, link(key)) for (title, text), key in zip(QUICK_START, STEP_PAGES, strict=True)],
        how_it_reads=HOW_IT_READS,
        routine=[(when, [(task, link(key)) for task, key in tasks]) for when, tasks in ROUTINE],
        areas=AREAS,
        area=area,
        q=q,
        checks=checks,
        check_total=len(CHECKS),
        faq=FAQ,
        shortcuts=SHORTCUTS,
        where=[
            ("Database", short_path(store.path)),
            ("Backups", short_path(data / "backups")),
            ("Settings file", ".env in the data folder"),
        ],
    )
