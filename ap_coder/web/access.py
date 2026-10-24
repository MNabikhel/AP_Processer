"""Who may see what, in the few places the server mode changes a page (D-99).

Two small answers the pages ask, kept here so they change in one place:

* ``is_admin(request)``: whether the person asking is an Admin. On a laptop (no accounts) the person at the
  computer runs AP Coder, so yes. On the company server, only the account logged in with the Admin role
  (``identity.person()``, set by the login middleware, D-97).
* ``person_key(request)``: whose edits a draft on the review screen is. None for this computer's own person; a
  logged-in account's id on the server; a colleague's (name, browser id) on the office network.
"""

from __future__ import annotations

from typing import Any

from ap_coder.ops import offline
from ap_coder.web import identity

ADMIN = "admin"


def is_admin(request: Any = None) -> bool:
    """Whether the person asking may see the server's own details (its folders): on a laptop always; on the server
    only an Admin's account."""
    if not _server(request):
        return True
    found = identity.person()
    return found is not None and found.role == ADMIN


def _server(request: Any) -> bool:
    """Server mode: the app's own setting (``create_app(server=...)``), else ``AP_SERVER_MODE``."""
    state = getattr(getattr(request, "app", None), "state", None)
    if state is not None and hasattr(state, "server_mode"):
        return bool(state.server_mode)
    return offline.server_mode()


def key_of(found: identity.Person) -> tuple[str, str]:
    """The draft key of one person: their account on the server (whatever name it shows), else their name (case
    aside) and their browser's id, or their address for a cookie made without one."""
    if found.user_id:
        return "", f"user:{found.user_id}"
    return found.name.casefold(), found.browser or found.address


def person_key(request: Any = None) -> tuple[str, str] | None:
    """Whose drafts: None for this computer's own person (one set of drafts), else ``key_of`` the person asking."""
    found = identity.person()
    return None if found is None else key_of(found)
