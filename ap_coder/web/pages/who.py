"""The "Who are you?" page (/who): a colleague using the dashboard from another computer of the office network gives
their name once; it is kept in a cookie the server signs (web/identity.py), and every approval and event of theirs
names them. On this computer the page only says who is approving here (Settings → Review changes that name)."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ap_coder.web import identity
from ap_coder.web.core import done, local_url, refuse, render, reviewer

router = APIRouter()

COOKIE_DAYS = 365  # kept by the browser; a restart of AP Coder asks again (the signing secret is new)


@router.get(identity.WHO_PATH, response_class=HTMLResponse)
def who(request: Request, next: str = "/review") -> HTMLResponse:  # noqa: A002 - the address's own word
    found = identity.person()
    here = request.client is None or identity.is_this_computer(request.client.host)
    name = reviewer() if (found or here) else "Not given yet"  # the rail shows no one's name before it is given
    return render(request, "who.html", "", title="Who are you?", next=local_url(next, "/review"), here=here,
                  current=found.name if found else "", reviewer=name)  # fmt: skip


@router.post(identity.WHO_PATH)
def who_save(request: Request, name: str = Form(""), next: str = Form("/review")) -> JSONResponse:  # noqa: A002
    if request.client is None or identity.is_this_computer(request.client.host):
        return refuse("On this computer, your name is set in Settings → Review.")
    cleaned = identity.clean_name(name)
    if not cleaned:
        return refuse("Type your name: it goes on every invoice you approve.")
    answer = done(request, next or "/review", f"Thanks, {cleaned}. Your approvals are now in your name.")
    answer.set_cookie(identity.COOKIE, identity.cookie_value(request.app.state.identity_secret, cleaned),
                      max_age=COOKIE_DAYS * 86400, httponly=True, samesite="strict", path="/")  # fmt: skip
    return answer
