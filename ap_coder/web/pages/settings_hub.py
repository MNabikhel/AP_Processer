"""Settings → Finance hub: AP Coder's feed for the company's finance hub on Microsoft Fabric (D-103), an Admin's.

On or off (off by default), how the files travel ("Fabric pulls" from a shared folder, the default once on, with the
SQLite copy for the on-premises gateway; or "AP Coder pushes" to OneLake), the schedule, the last run and its status,
and "Run now". Every change is in the Activity log with the names of what changed (never the client secret, which is
kept in the data folder's ``.env``), and each run with the person who ran it.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ap_coder.erp.hub import landing, onelake
from ap_coder.erp.hub.run import (
    EVERY_HOURS,
    FOLDER,
    FORMATS,
    MODES,
    ONELAKE,
    HubConfig,
    client_secret,
    export,
    last_run,
    save_config,
)
from ap_coder.erp.hub.tables import HUB_SCHEMA_VERSION, TABLES
from ap_coder.web.core import done, get_store, posted_form, refuse, reviewer, server_mode

router = APIRouter()

URL = "/settings/hub"
EVERY_WORDS = {0: "Only when run by hand", 1: "Every hour", 4: "Every 4 hours", 12: "Every 12 hours", 24: "Once a day"}
FORMAT_WORDS = {"auto": "Automatic (Parquet when it can, else CSV)", "parquet": "Parquet", "csv": "CSV"}


def hub_context(request: Request) -> dict[str, Any]:
    store = get_store(request)
    config = HubConfig.load(store)
    secret = client_secret(store)
    return {
        "hub": config,
        "hub_last": last_run(store),
        "hub_secret_set": bool(secret),
        "hub_every": EVERY_WORDS,
        "hub_formats": FORMAT_WORDS,
        "hub_parquet": landing.parquet_available(),
        "hub_format_now": landing.choose_format("auto") if config.format == "auto" else config.format,
        "hub_push_blocked": onelake.push_allowed(True),
        "hub_tables": list(TABLES.values()),
        "hub_version": HUB_SCHEMA_VERSION,
        "hub_on_server": server_mode(request),
    }


def _on(form: Any, name: str) -> bool:
    return str(form.get(name) or "") in ("1", "on", "true")


def _web_address(text: str) -> bool:
    parts = urllib.parse.urlsplit(text)
    return parts.scheme in ("http", "https") and bool(parts.hostname)


@router.post("/settings/hub")
async def save_hub(request: Request) -> JSONResponse:
    """The whole tab's one form. Unticked boxes are off; a client secret left blank is kept."""
    store = get_store(request)
    form = await posted_form(request)
    before = HubConfig.load(store)
    mode, fmt = str(form.get("mode") or FOLDER), str(form.get("format") or "auto")
    try:
        every = int(str(form.get("every_hours") or "24"))
    except ValueError:
        every = -1
    if mode not in MODES or fmt not in FORMATS:
        return refuse("Choose how the files travel and their format from the lists.")
    if every not in EVERY_HOURS:
        return refuse("Choose how often from the list: every hour, 4 or 12 hours, once a day, or only by hand.")
    if fmt == landing.PARQUET and not landing.parquet_available():
        return refuse("Parquet needs the pyarrow package, which is not installed here: choose CSV, or Automatic.")
    config = HubConfig(
        enabled=_on(form, "enabled"), mode=mode, folder=str(form.get("folder") or "").strip(), every_hours=every,
        format=fmt, sqlite=_on(form, "sqlite"),
        link_base=str(form.get("link_base") or "").strip().rstrip("/") or before.link_base,
        onelake_url=str(form.get("onelake_url") or "").strip(), tenant_id=str(form.get("tenant_id") or "").strip(),
        client_id=str(form.get("client_id") or "").strip(), authority=str(form.get("authority") or "").strip(),
        push_opt_in=_on(form, "push_opt_in"),
    )  # fmt: skip
    secret = str(form.get("client_secret") or "").strip() or None
    if not _web_address(config.link_base):
        return refuse("The address people open AP Coder at must start with http:// or https://, e.g. "
                      "https://<the server's name>.")  # fmt: skip
    if config.enabled and mode == FOLDER and not config.folder:
        return refuse("Give the folder the finance hub reads from (a network share), or turn the feed off.")
    from ap_coder.ops.service import folder_refusal

    if mode == FOLDER and folder_refusal(config.folder, server=server_mode(request)):  # (D-109)
        return refuse(folder_refusal(config.folder, server=True))
    if mode == ONELAKE and (config.onelake_url or config.enabled):
        try:
            onelake._check_url(config.onelake_url, onelake.ONELAKE_HOSTS, "OneLake folder URL")
            if config.authority:
                onelake._check_url(config.authority, onelake.AUTHORITY_HOSTS, "sign-in address", exact=True)
        except landing.HubError as exc:
            return refuse(str(exc))
        if config.enabled and not (config.tenant_id and config.client_id and (secret or client_secret(store))):
            return refuse("The push to OneLake needs the tenant ID, the client ID and the client secret.")
    changed = save_config(store, config, actor=reviewer(), secret=secret)
    if not changed:
        return done(request, URL, "Nothing changed.", tone="info")
    state = "on" if config.enabled else "off"
    return done(request, URL, f"Saved. The finance hub feed is {state}.")


@router.post("/settings/hub/run")
async def run_hub(request: Request) -> JSONResponse:
    """Run now (a full rebuild when ticked), recorded with the person who ran it."""
    store = get_store(request)
    form = await posted_form(request)
    config = HubConfig.load(store)
    if not config.enabled:
        return refuse("The finance hub feed is off: turn it on and save first.")
    from starlette.concurrency import run_in_threadpool

    result = await run_in_threadpool(export, store, config, full=_on(form, "full"), actor=reviewer())
    return done(request, URL, result.summary(), tone="ok" if result.ok else "err")
