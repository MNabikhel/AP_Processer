"""Who may see what, in the few places the server mode changes a page (D-99).

Two small answers the pages ask, kept here so they change in one place:

* ``has(request, permission)``: whether the person asking may do what ``permission`` allows (``core/permissions.py``,
  D-250). On a laptop (no accounts) the person at the computer runs AP Coder, so yes. On the company server, the
  account logged in (``identity.person()``, set by the login middleware, D-97), by its role's permissions.
* ``is_admin(request)``: whether the person asking may see the server's own details (its folders, its versions):
  ``settings``, an Admin's.
* ``may_lift_hold(request)``: whether the person asking may take a vendor off hold, or approve a held vendor's
  invoice coded to another vendor (D-142): on a laptop yes; on the server ``controls``: an Approver or an Admin, not a
  Clerk.
* ``confirms(user)``: whether an account is one AP may ask to confirm an invoice's coding (D-138): ``confirm``, a
  Manager's.
* ``person_key()``: whose edits a draft on the review screen is. None for this computer's own person; a
  logged-in account's id on the server; a colleague's (name, browser id) on the office network.
"""

from __future__ import annotations

from typing import Any

from ap_coder.core.permissions import CONFIRM, CONTROLS, SETTINGS
from ap_coder.ops import offline
from ap_coder.web import identity


def has(request: Any, permission: str) -> bool:
    """Whether the person asking may do what ``permission`` allows: on a laptop always; on the server by their role."""
    if not _server(request):
        return True
    found = identity.person()
    return found is not None and permission in found.granted


def is_admin(request: Any = None) -> bool:
    """Whether the person asking may see the server's own details (its folders): on a laptop always; on the server
    only an account whose role changes the settings (an Admin's)."""
    return has(request, SETTINGS)


def confirms(user: dict[str, Any]) -> bool:
    """Whether account ``user`` (as the store gives it) is one AP may ask to confirm an invoice's coding (D-138)."""
    return bool(user.get("active")) and CONFIRM in (user.get("permissions") or ())


def may_lift_hold(request: Any = None) -> bool:
    """Whether the person asking may take a vendor off hold, or approve a held vendor's invoice coded to another
    vendor: on a laptop always; on the server an Approver or an Admin, not a Clerk, who would then approve the
    vendor's invoices under the limit alone (D-142). Without ``request``, an account logged in is the server."""
    found = identity.person()
    if not (_server(request) or (found is not None and found.user_id)):
        return True
    return found is not None and CONTROLS in found.granted


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


def person_key() -> tuple[str, str] | None:
    """Whose drafts: None for this computer's own person (one set of drafts), else ``key_of`` the person asking."""
    found = identity.person()
    return None if found is None else key_of(found)
