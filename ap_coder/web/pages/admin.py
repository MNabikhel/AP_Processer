"""The Admin panel (/admin, D-249): one place for what an Admin looks after where people sign in (the company server,
a laptop shared with ``share``, and a laptop's own sign-in, D-243): the people and their roles, the activity log, the
sign-in pauses and the computers signed in now, and links to the connections and the backups (which stay on their
Settings tabs). A page per person (/admin/users/<id>): their account, where they are signed in, their latest sign-ins
and tries that didn't match, and what they did, with the Users page's actions.

On a laptop's own sign-in only its Admin signs in: the panel shows their own account, the activity and the sign-in;
the people and the roles are the server's (the Users page answers 404 there, as before).
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ap_coder.core.audit import ACTIONS, describe
from ap_coder.core.store.users import ROLE_LABELS, ROLES
from ap_coder.web import identity
from ap_coder.web.core import can, done, get_store, page_router, refuse, render, reviewer, server_mode, signs_in
from ap_coder.web.pages import activity, users

router = page_router()  # each plain handler: one connection a request

PATH = "/admin"
SIGN_IN_PATH = "/admin/sign-in"
EVENTS_SHOWN = 50  # a person's events a page of theirs shows; the older ones are on the next
SIGN_INS_SHOWN = 20
# The Settings tabs an Admin looks after, linked from the panel (not moved: D-249).
SETTINGS_LINKS = (
    ("/settings/reading#azure-check", "cloud", "Azure Document Intelligence", "The optional second reader of scans."),
    ("/settings/jde#api", "lan", "JD Edwards AIS Server", "Posting vouchers to JD Edwards through its API."),
    ("/settings/data#backups", "backup", "Data & backups", "The backups, their time, and restoring one."),
)


def _signs_in_only(request: Request) -> None:
    if not signs_in(request):
        raise HTTPException(404, "The Admin panel is for AP Coder with sign-in: a company server, a laptop shared "
                                 "with its colleagues, or a laptop whose Admin signs in.")  # fmt: skip


def _clock_text(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def browser_name(agent: str) -> str:
    """The browser and system a session came from, in words ("Edge on Windows"), from its user agent; "An unknown
    browser" when it says nothing we know."""
    text = str(agent or "")
    browser = next((name for mark, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                            ("Chrome/", "Chrome"), ("CriOS/", "Chrome"), ("Safari/", "Safari"))
                    if mark in text), "")  # fmt: skip
    system = next((name for mark, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                           ("Windows", "Windows"), ("Macintosh", "Mac"), ("CrOS", "ChromeOS"),
                                           ("Linux", "Linux")) if mark in text), "")  # fmt: skip
    if browser and system:
        return f"{browser} on {system}"
    return browser or (f"A browser on {system}" if system else "An unknown browser")


def _sessions(store: Any, user_id: int | None = None) -> list[dict[str, Any]]:
    me = identity.signed_in()
    return [{**s, "browser_name": browser_name(s["browser"]), "since_text": _clock_text(s["since"]),
             "seen_text": _clock_text(s["last_seen"]), "address": s["address"] or "unknown",
             "mine": me is not None and s["user_id"] == me.user_id} for s in store.live_sessions(user_id)]  # fmt: skip


def _event_row(event: dict[str, Any]) -> dict[str, Any]:
    icon, tone, label = ACTIONS.get(event["action"], ("circle", "gray", event["action"]))
    return {
        "id": event["id"],
        "when": str(event["created_at"] or "").replace("T", " ")[:16],
        "icon": icon,
        "tone": tone,
        "what": label,
        "details": describe(event),
        "invoice_id": event.get("invoice_id"),
        "invoice": activity.invoice_label(event),
        "address": (event.get("detail") or {}).get("address") or "",
        "who": event.get("actor") or "",
    }


def common(request: Request) -> dict[str, Any]:
    """What every page of the panel shows: its tabs, those this person may open (on a laptop's own sign-in, not the
    people and the roles: the server's)."""
    tabs = [(PATH, "Overview"), (users.PATH, "Users"), (SIGN_IN_PATH, "Sign-in"),
            ("/activity", "Activity")]  # fmt: skip
    server = server_mode(request)
    shown = [(path, label) for path, label in tabs if can(request, path, "GET")
             and (server or path not in (users.PATH, "/admin/roles"))]  # fmt: skip
    return {"admin_tabs": shown, "server_mode": server}


@router.get(PATH, response_class=HTMLResponse)
def admin_page(request: Request) -> HTMLResponse:
    _signs_in_only(request)
    store = get_store(request)
    me = identity.signed_in()
    people = store.list_users() if server_mode(request) else []
    recent = list(activity.filter_events(store, activity.ACCOUNT_GROUP, "Everyone", "", limit=8))
    paused = len(store.paused_addresses()) + len(store.paused_usernames())
    links = [link for link in SETTINGS_LINKS if can(request, link[0], "GET")]
    return render(request, "admin.html", "admin", title="Admin", **common(request), people=people,
                  active=sum(1 for u in people if u["active"]), me=me.user_id if me else 0,
                  sessions=len(store.live_sessions()), paused=paused, recent=[_event_row(e) for e in recent],
                  links=links)  # fmt: skip


@router.get(SIGN_IN_PATH, response_class=HTMLResponse)
def sign_in_page(request: Request) -> HTMLResponse:
    """Sign-in: the addresses and usernames paused now (lifted here, as on Users) and every session within its
    time, whose and from where; an Admin signs someone else out everywhere."""
    _signs_in_only(request)
    store = get_store(request)
    return render(request, "admin_sign_in.html", "admin", title="Sign-in", **common(request),
                  sessions=_sessions(store), **users.paused_context(store))  # fmt: skip


@router.get(users.PATH + "/{user_id:int}", response_class=HTMLResponse)
def user_page(request: Request, user_id: int, before: int = 0, shown: str = "") -> HTMLResponse:
    """One person: their account, where they are signed in, their latest sign-ins and what they did (their events,
    by the name they are recorded under, newest first, a page at a time)."""
    _signs_in_only(request)
    store = get_store(request)
    found = store.user(user_id)
    if found is None:
        raise HTTPException(404, "There is no such account.")
    me = identity.signed_in()
    events = list(activity.filter_events(store, "All", found["name"], "", before=before or None,
                                         limit=EVENTS_SHOWN + 1))  # fmt: skip
    older = ""
    if len(events) > EVENTS_SHOWN:
        older = f"{users.PATH}/{user_id}?{urlencode({'before': events[EVENTS_SHOWN - 1]['id']})}#activity"
    return render(request, "admin_user.html", "admin", title=found["name"], **common(request), user=found,
                  own_account=me is not None and me.user_id == found["id"], roles=[(r, ROLE_LABELS[r]) for r in ROLES],
                  temporary=users.take_temporary(request, shown), sessions=_sessions(store, found["id"]),
                  sign_ins=[_event_row(e) for e in store.sign_in_events(found["username"], SIGN_INS_SHOWN)],
                  events=[_event_row(e) for e in events[:EVENTS_SHOWN]], older=older,
                  newest=f"{users.PATH}/{user_id}#activity" if before else "")  # fmt: skip


@router.post(users.PATH + "/{user_id:int}/sign-out")
def sign_out(request: Request, user_id: int, back: str = Form("")) -> JSONResponse:
    """Sign someone out on every computer and browser (a computer left signed in): their password stays. One's own
    sessions end from Your password (Log out everywhere else), so this browser is not signed out by surprise."""
    users.server_only(request)
    found = users.account(request, user_id)
    me = identity.signed_in()
    if me is not None and me.user_id == found["id"]:
        return refuse("This is your own account: use Log out everywhere else on the Your password page "
                      "(/account/password), which keeps this browser signed in.")  # fmt: skip
    ended = get_store(request).sign_out_everywhere(user_id, actor=reviewer())
    said = (
        f"{found['username']} is signed out on {ended} computer{'s' if ended != 1 else ''} or "
        f"browser{'s' if ended != 1 else ''}."
        if ended
        else f"{found['username']} was signed in nowhere."
    )
    return done(request, SIGN_IN_PATH if back == "sign-in" else f"{users.PATH}/{user_id}", said)
