"""The Azure check's card (Settings → Reading): Azure AI Document Intelligence as an optional second opinion.

The card is ``templates/partials/azure_check.html``; Settings → Reading includes it, and ``/settings/azure-check``
shows it on its own. It draws itself: the template global ``azure_check_card(request)`` gives it what it shows
(``azure_check.overview``), so the page that includes it needs to pass nothing. Off by default; AP Coder works fully
offline without it (see ``ap_coder/ai/azure_check.py``).

* ``GET /settings/azure-check``: the card on a page of its own.
* ``POST /settings/azure-check``: save the settings (the key is never sent back to the page, only a hint of it).
* ``POST /settings/azure-check/test``: "Test connection", a background job (``/jobs/azure-test``) that reads the
  made-up sample invoice with Azure and compares its fields with AP Coder's own reading; the page reloads when done.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ap_coder.ai import azure_check
from ap_coder.web.core import done, get_settings, get_store, jobs, local_url, refuse, render, reviewer, templates

router = APIRouter()

JOB = "azure-test"
HOME = "/settings/azure-check"


def card(request: Request) -> dict[str, Any]:
    """What the card shows (``azure_check.overview``); never raises, so it can't break the page that includes it."""
    try:
        view = azure_check.overview(get_store(request), get_settings(request))
        return {**view, "testing": jobs(request).get(JOB).running}
    except Exception as exc:  # noqa: BLE001 - the Settings page still opens
        return {"broken": f"The Azure check's state can't be read ({type(exc).__name__})."}


templates.env.globals["azure_check_card"] = card


def shown(request: Request) -> bool:
    """Whether the Azure check's page shows its card: where the internet is allowed, while the check is on, or with the
    developer tools (IT setting it up). The offline pilot build otherwise says it isn't part of it (B-516)."""
    from ap_coder.ops.offline import internet_allowed
    from ap_coder.web.core import developer_tools
    from ap_coder.web.pages.settings_reading import azure_check_on

    store = get_store(request)
    return internet_allowed() or azure_check_on(store) or developer_tools(store)


@router.get(HOME, response_class=HTMLResponse)
def azure_check_page(request: Request) -> HTMLResponse:
    return render(request, "azure_check_page.html", "settings", shown=shown(request))


def _number(text: str, what: str) -> float:
    try:
        number = float(str(text or "0").replace(",", ".").replace("%", "").strip() or 0)
    except ValueError:
        number = math.nan
    if not math.isfinite(number):  # ("1e309", "inf" or "nan" posted directly: int() of it would raise)
        raise ValueError(f"The {what} must be a number.")
    return number


@router.post(HOME)
def save_azure_check(
    request: Request, enabled: str = Form(""), endpoint: str = Form(""), api_key: str = Form(""),
    forget_key: str = Form(""), model: str = Form(azure_check.DEFAULT_MODEL), spot_percent: str = Form("5"),
    daily_pages: str = Form(str(azure_check.DEFAULT_CAP)), back: str = Form(HOME),
) -> JSONResponse:  # fmt: skip
    store = get_store(request)
    try:
        changed = azure_check.save_settings(
            store, enabled=bool(enabled), endpoint=endpoint, api_key=api_key, forget_key=bool(forget_key),
            model=model, spot_percent=_number(spot_percent, "spot-check share"),
            daily_pages=int(_number(daily_pages, "daily cap")), actor=reviewer(), settings=get_settings(request),
        )  # fmt: skip
    except ValueError as exc:
        return refuse(str(exc))
    cfg = azure_check.load_settings(store, get_settings(request))
    _, note = azure_check.status(cfg)
    text = f"Azure check saved ({', '.join(changed)}). {note}" if changed else "Nothing changed."
    tone = "ok" if not cfg.enabled or azure_check.usable(cfg) else "warn"
    return done(request, local_url(back, HOME), text, tone=tone)


@router.post(f"{HOME}/test")
def test_azure_check(request: Request) -> JSONResponse:
    store, settings = get_store(request), get_settings(request)
    cfg = azure_check.load_settings(store, settings)
    state, why = azure_check.status(cfg)
    if state != azure_check.READY:
        return refuse(why)

    def work(job: Any) -> dict[str, Any]:
        job.step(0, 1, f"Reading the sample invoice with {azure_check.NAME}…")
        record = azure_check.connection_test(store, settings)
        tone = "ok" if record.get("ok") else "err"
        return {"messages": [{"text": record.get("summary") or record.get("error") or "", "tone": tone}]}

    if not jobs(request).get(JOB).start(work, total=1, note="Testing the connection"):
        return refuse("The connection is being tested already.", status=409)
    return JSONResponse({"ok": True, "job": JOB, "status": f"/jobs/{JOB}"})
