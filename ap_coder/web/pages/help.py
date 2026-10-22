"""Help: getting started (in the Quick start's order), how every invoice is read, the AP routine, what every check
means and what to do about it (``ap_coder.ops.help.CHECKS``, grouped by what the tester does and folded, searchable by
word and area), questions, shortcuts, and for IT where the data is. The search and the area are in the address
(``/help?q=duplicate&area=Sales+tax``), so a filtered list can be linked to; the page also filters as you type. The
checks only the optional Azure check raises are left out unless it is on (or the internet is allowed), and the
developer changelog shows with the developer tools (B-514).

Report a problem draws at once; its setup report (the doctor, a few seconds: it looks at OCR) is fetched by the page
afterwards from ``/help/report/setup`` and kept a few minutes (B-520).
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from ap_coder.core import paths
from ap_coder.ops.help import AREAS, CHECKS, CLOUD_CHECKS, FAQ, QUICK_START, ROUTINE, TO_DO, WHATS_NEW, CheckHelp
from ap_coder.ops.offline import internet_allowed
from ap_coder.web import minimd
from ap_coder.web.core import data_dir, developer_tools, get_store, render, short_path
from ap_coder.web.pages import BY_KEY
from ap_coder.web.pages.settings_reading import HOW_IT_READS, azure_check_on

router = APIRouter()

SETUP_KEEP_SECONDS = 300  # the setup report is kept this long once made
_setup_cache: dict[str, tuple[float, str]] = {}
_setup_lock = threading.Lock()
# Each row's keys: [Key] is a key, anything else words between them ("[Ctrl] + wheel", "[H] or hold [Space]").
SHORTCUT_ROWS = [
    ("[Ctrl] + [Enter]", "Approve & teach the open invoice"),
    ("[Alt] + [→]", "Next invoice"),
    ("[Alt] + [←]", "Previous invoice"),
    ("[Alt] + [↑]", "Back to the queue"),
    # The page viewer on the review screen, worded as its own Shortcuts list (templates/invoice.html)
    ("[+] [−] [0] [9]", "Zoom the page in, out, to its width, all of it"),
    ("[Ctrl] + wheel", "Zoom where the pointer is (or pinch)"),
    ("[H] or hold [Space]", "Drag to move the page"),
    ("[R]", "Turn the page (on screen only)"),
    ("[PgUp] [PgDn]", "Previous or next page"),
    # The review screen's menus and its last change (D-60), also in its own Shortcuts list
    ("[Shift] + [F10]", "More options for a field, a line or the page (or right-click)"),
    ("[Ctrl] + [Z]", "Undo the last change (a line deleted, a value cleared)"),
    ("[P]", "Park: opens Park, to say what it waits for"),
    ("[X]", "Reject: opens Reject, to say why (nothing is rejected until you press it)"),
]


def shortcut_keys(spec: str) -> list[dict[str, Any]]:
    """``"[Ctrl] + wheel"`` -> [{"key": True, "text": "Ctrl"}, {"key": False, "text": "+ wheel"}]."""
    parts = []
    for piece in re.split(r"(\[[^\]]+\])", spec):
        if piece.startswith("[") and piece.endswith("]"):
            parts.append({"key": True, "text": piece[1:-1]})
        elif piece.strip():
            parts.append({"key": False, "text": piece.strip()})
    return parts


SHORTCUTS = [(shortcut_keys(spec), what) for spec, what in SHORTCUT_ROWS]


def matching_checks(query: str = "", area: str | None = None, cloud: bool = True) -> list[tuple[str, CheckHelp]]:
    """The check catalog filtered by a search text (code, title, meaning or action) and an area; without ``cloud``,
    the checks only the Azure check raises are left out."""
    pattern = re.compile(re.escape(query.strip()), re.IGNORECASE) if query.strip() else None
    out = []
    for code, about in CHECKS.items():
        if area and about.area != area:
            continue
        if not cloud and code in CLOUD_CHECKS:
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


def quick_start_doc() -> Any:
    """docs/QUICK_START.md as HTML, without its own title ("" when the file isn't there)."""
    doc = paths.PROJECT_DIR / "docs" / "QUICK_START.md"
    if not doc.is_file():
        return ""
    text = doc.read_text(encoding="utf-8")
    text = text.split("\n", 1)[1] if text.startswith("# ") else text
    return minimd.render(text, top=1)  # "## Install" is a section of the page: h2


@router.get("/help/quick-start", response_class=HTMLResponse)
def quick_start_page(request: Request) -> HTMLResponse:
    """The pilot tester's one-page Quick start (docs/QUICK_START.md), to read or print."""
    return render(request, "help_quick_start.html", "help", doc=quick_start_doc())


def forget_setup_report() -> None:
    """The setup report is made again on its next use (after Check again or a setting saved)."""
    with _setup_lock:
        _setup_cache.clear()


def setup_report(store: Any) -> str:
    """The doctor's report (``python -m ap_coder doctor``, offline, without asking LM Studio): safe to paste. Kept
    ``SETUP_KEEP_SECONDS`` once made: it takes a few seconds (it looks at the OCR engines)."""
    key = str(store.path)
    with _setup_lock:
        kept = _setup_cache.get(key)
        if kept and time.monotonic() - kept[0] < SETUP_KEEP_SECONDS:
            return kept[1]
    report = _make_setup_report(store)
    with _setup_lock:
        _setup_cache[key] = (time.monotonic(), report)
    return report


def _make_setup_report(store: Any) -> str:
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


@router.get("/help/report/setup", response_class=PlainTextResponse)
def report_setup(request: Request) -> PlainTextResponse:
    """The setup report, fetched by Report a problem once the page is drawn (B-520)."""
    return PlainTextResponse(setup_report(get_store(request)), headers={"Cache-Control": "no-store"})


@router.get("/help/report", response_class=HTMLResponse)
def report_page(request: Request, codes: bool = False) -> HTMLResponse:
    """Report a problem: what to write, and the two reports to copy (no command line). The setup report comes after
    the page (``/help/report/setup``): the page opens at once."""
    store = get_store(request)
    logs = paths.user_settings_path().parent / "logs"
    key = str(store.path)
    with _setup_lock:
        kept = _setup_cache.get(key)
    ready = kept[1] if kept and time.monotonic() - kept[0] < SETUP_KEEP_SECONDS else ""
    return render(
        request,
        "help_report.html",
        "help",
        title="Report a problem",
        setup=ready,
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
    cloud = internet_allowed() or azure_check_on(store)
    checks = matching_checks(q, area or None, cloud)
    data = data_dir(store)
    groups = [
        {"area": a, "title": title, "checks": [(code, about) for code, about in checks if about.area == a]}
        for a, title in TO_DO.items()
    ]
    return render(
        request,
        "help.html",
        "help",
        news=whats_new() if developer_tools(store) else "",
        whats_new=WHATS_NEW,
        quick_start=QUICK_START,
        how_it_reads=HOW_IT_READS,
        routine=[(when, [(task, link(key)) for task, key in tasks]) for when, tasks in ROUTINE],
        areas=AREAS,
        area=area,
        q=q,
        checks=checks,
        groups=[g for g in groups if g["checks"]],
        open_groups=bool(q.strip() or area),
        check_total=len(matching_checks(cloud=cloud)),
        faq=FAQ,
        shortcuts=SHORTCUTS,
        where=[
            ("Database", short_path(store.path)),
            ("Backups", short_path(data / "backups")),
            ("Settings", "the Settings page (kept in the data folder)"),
            ("Logs, for IT", short_path(paths.user_settings_path().parent / "logs")),
        ],
    )
