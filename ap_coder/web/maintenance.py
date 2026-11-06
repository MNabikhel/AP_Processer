"""While a backup is being restored on the company server (Settings → Data & backups → Restore, or ``backup restore``
on the console), every web worker answers everyone else 503: a plain page "AP Coder is being restored from a backup;
back in a minute or two" (JSON for the page's own requests), never a page drawn from a database being replaced
(D-188). The workers are separate processes: what they look at is the restore's marker beside the database
(``backups.restore_marker``), not the database. What still answers: ``/healthz`` and ``/readyz`` (the service's
checks), the static files (no database), and the restore's own request, under way since before the marker was
written: the Admin's page waits for it, then logs in again.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import anyio.to_thread

from ap_coder.core.store.backups import restore_marker, restore_under_way

MESSAGE = "AP Coder is being restored from a backup; back in a minute or two."
STILL_ANSWERED = ("/healthz", "/readyz")
STATIC = ("/static/", "/fonts/", "/assets/")
RETRY_SECONDS = 60
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{retry}"><title>AP Coder: being restored</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 36rem; margin: 4rem auto; padding: 0 1rem; color: #222">
<h1 style="font-size: 1.4rem">AP Coder is being restored from a backup</h1>
<p>Back in a minute or two. This page tries again by itself; nothing you saved is lost.</p>
</body></html>
"""


class Maintenance:
    """ASGI middleware: the 503 while a restore runs into ``db``, in this web worker or another."""

    def __init__(self, app: Any, db: Path) -> None:
        self.app, self.marker, self.db = app, os.fspath(restore_marker(db)), db

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] in STILL_ANSWERED or scope["path"].startswith(STATIC):
            await self.app(scope, receive, send)
            return
        try:
            os.stat(self.marker)  # (one look at the folder: the marker is there only while a restore runs)
        except OSError:
            await self.app(scope, receive, send)
            return
        if await anyio.to_thread.run_sync(restore_under_way, self.db) is None:  # left by a restore that ended
            await self.app(scope, receive, send)
            return
        await _restoring(scope, send)


async def _restoring(scope: Any, send: Any) -> None:
    accept = next((value for name, value in scope["headers"] if name.lower() == b"accept"), b"").decode("latin-1")
    if scope["method"] == "GET" and "text/html" in accept:
        body, kind = PAGE.format(retry=RETRY_SECONDS).encode(), b"text/html; charset=utf-8"
    else:
        body, kind = json.dumps({"ok": False, "error": MESSAGE, "restoring": True}).encode(), b"application/json"
    headers = [(b"content-type", kind), (b"content-length", str(len(body)).encode()),
               (b"retry-after", str(RETRY_SECONDS).encode()), (b"cache-control", b"no-store")]  # fmt: skip
    await send({"type": "http.response.start", "status": 503, "headers": headers})
    await send({"type": "http.response.body", "body": body if scope["method"] != "HEAD" else b""})
