"""An error no page expected (B-241): the dashboard's own page, with the menu, in plain words and with what to do,
instead of the bare text "Internal Server Error". The technical details are folded away on the page and written in
full to ``~/.ap_coder/logs/dashboard-errors.log`` (beside the launcher's ``launch.log``); the AP Coder window says one
line. A form sent from a page (``app.js`` ``post``) gets ``{"ok": false, "error": ...}``, shown as a message."""

from __future__ import annotations

import html
import logging
import time
import traceback
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ap_coder.core import paths

log = logging.getLogger(__name__)

LOG_NAME = "dashboard-errors.log"
LOG_LIMIT = 1_000_000  # bytes: a longer log is kept once as dashboard-errors.previous.log
WHAT_TO_DO = (
    "Go back and try again. If it happens again, open Help → Report a problem: it says what to send so it can be "
    "fixed. Your invoices and settings are safe."
)


def error_log_path() -> Path:
    return paths.user_settings_path().parent / "logs" / LOG_NAME


def record(request: Request, exc: BaseException) -> Path | None:
    """The full details (traceback) in the error log; one line in the AP Coder window. The log file's path, or None
    when it could not be written (the traceback then goes to the window)."""
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    path = error_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > LOG_LIMIT:
            path.replace(path.with_name("dashboard-errors.previous.log"))
        with open(path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} {request.method} {request.url.path}\n{details}\n")
    except OSError:
        log.error("A page failed (%s %s)", request.method, request.url.path, exc_info=exc)
        return None
    log.error("A page failed (%s %s): %s. The details are in %s", request.method, request.url.path, summary(exc), path)
    return path


def summary(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def response(request: Request, exc: BaseException) -> Response:
    """What the browser gets: the error page for a page asked for, a JSON message for a form sent from a page."""
    from ap_coder.web.identity import person, without_folders

    logged = record(request, exc)
    if person() is not None:  # a colleague on another computer: no folder of this one, no log path (B-979)
        logged = None
    wants_page = request.method == "GET" and "text/html" in request.headers.get("accept", "")
    if not wants_page:
        said = without_folders(summary(exc))
        return JSONResponse({"ok": False, "error": f"Something went wrong ({said}). {WHAT_TO_DO}"}, status_code=500)
    context: dict[str, Any] = {"code": 500, "title": "Something went wrong", "what_to_do": WHAT_TO_DO,
                               "error_text": without_folders(summary(exc)), "error_log": str(logged or "")}  # fmt: skip
    try:
        from ap_coder.web import core

        return core.render(request, "error.html", "", status_code=500,
                           detail="AP Coder couldn't show this page.", **context)  # fmt: skip
    except Exception:  # noqa: BLE001 - the page frame reads the database: when that is what failed, a plain page
        return HTMLResponse(plain_page(context), status_code=500)


def plain_page(context: dict[str, Any]) -> str:
    log_line = f"<p>The full details are in {html.escape(context['error_log'])}.</p>" if context["error_log"] else ""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Something went wrong · AP Coder</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem;color:#1f2937}"
        "a{color:#2563eb}</style></head><body><h1>Something went wrong</h1><p>AP Coder couldn't show this page.</p>"
        f"<p><b>What to do:</b> {html.escape(context['what_to_do'])}</p>"
        "<p><a href='/review'>Go to the review queue</a> · <a href='/help/report'>Report a problem</a></p>"
        f"<details><summary>Details for IT</summary><p><code>{html.escape(context['error_text'])}</code></p>"
        f"{log_line}</details></body></html>"
    )


class FriendlyErrors:
    """ASGI middleware: an exception from a page, before it answered, becomes :func:`response` (logged once, here)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def watch(message: Any) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, watch)
        except Exception as exc:
            if started:  # half an answer was sent: nothing better can be said now
                raise
            await response(Request(scope, receive), exc)(scope, receive, send)
