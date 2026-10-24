"""Who may see what, in the few places the server mode changes a page (D-99).

Two small answers the pages ask, kept here so the accounts and roles (``web/identity.py``, ``web/roles.py``) can
change them in one place:

* ``is_admin(request)``: whether the person asking is an Admin. On a laptop (no accounts) the person at the
  computer runs AP Coder, so yes. On the company server, only a user whose session says so: the login middleware
  sets ``request.state.user_role`` ("admin", "approver", "clerk", "readonly"); without it, no.
* ``person_key(request)``: whose edits a draft on the review screen is. None for this computer's own person; on the
  office network today (name, browser id). With accounts, the user id.
"""

from __future__ import annotations

from typing import Any

from ap_coder.ops.offline import server_mode
from ap_coder.web import identity

ADMIN = "admin"


def user_role(request: Any) -> str:
    """The role the login middleware gave this request ("" when there is none)."""
    state = getattr(request, "state", None)
    return str(getattr(state, "user_role", "") or "")


def is_admin(request: Any) -> bool:
    """Whether the person asking may see the server's own details (its folders): on a laptop always; on the server
    only an Admin's session."""
    if not server_mode():
        return True
    return user_role(request) == ADMIN


def person_key(request: Any) -> tuple[str, ...] | None:
    """Whose draft this is: None for this computer's person (one set of drafts), else a key of the person asking.
    With accounts (``request.state.user_id`` set by the login middleware), ("user", <id>); a colleague on the office
    network, (their name with case aside, their browser's id or else their address)."""
    state = getattr(request, "state", None)
    user_id = getattr(state, "user_id", None)
    if user_id not in (None, ""):
        return ("user", str(user_id))
    found = identity.person()
    if found is None:
        return None
    return found.name.casefold(), found.browser or found.address  # by address: a cookie made without a browser id
