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
