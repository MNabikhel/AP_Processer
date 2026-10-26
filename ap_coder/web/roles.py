"""Who may do what in server mode (D-98): one table from each route to what it needs, read by the ``Identity``
middleware (web/identity.py) before any page runs.

The roles, each able to do everything the one before it can (``core/store/users.py`` ``ROLES``):

* **Read-only** (``READ``): looks at the review queue, the invoices and their pages, the reports, the list of
  exports, Help, Insights, Spend, the Activity log and their own account. Changes nothing.
* **Clerk** (``CLERK``): imports invoices, reviews and checks them, gives the first approval, parks, rejects,
  reopens, undoes their own approval, approves in bulk, makes export batches and downloads them, keeps vendors' hold
  and notes.
* **Approver** (``APPROVER``): also gives the second approval (over the approval limit) or sends the invoice back,
  and gives the go-ahead to post vouchers to JD Edwards through the API (D-104; a Clerk may run its dry run).
* **Admin** (``ADMIN``): everything else: Settings, the people who log in, JD Edwards setup (the API connection and
  its sync too), the account lists and tax setup, imports of the vendor master and the like, backups and restore,
  deleting, the Azure check, the learning memory's resets, the finance hub feed.

``OPEN`` routes answer anyone (the login page, ``/healthz``, the styles and scripts); ``SIGNED_IN`` ones anyone
logged in, also while they must still choose a new password (logging out, changing it). A route not in ``RULES``
needs an Admin: a route added without a rule is closed, and ``tests/test_roles.py`` fails until it gets one.
Hiding a button a role can't use is only for the eye; this table is what is enforced.
"""

from __future__ import annotations

from typing import Any

from starlette.routing import Match

from ap_coder.core.store.users import ADMIN as ADMIN_ROLE
from ap_coder.core.store.users import APPROVER as APPROVER_ROLE
from ap_coder.core.store.users import CLERK as CLERK_ROLE
from ap_coder.core.store.users import READONLY

OPEN, SIGNED_IN, READ, CLERK, APPROVER, ADMIN = "open", "signed in", "read", "clerk", "approver", "admin"
RANK = {READONLY: 1, CLERK_ROLE: 2, APPROVER_ROLE: 3, ADMIN_ROLE: 4}  # a role's rank
NEEDS = {SIGNED_IN: 0, READ: 1, CLERK: 2, APPROVER: 3, ADMIN: 4}  # the rank a need asks for
MOUNTS_OPEN = ("/static", "/fonts", "/assets")  # the styles, scripts, fonts and logo: anyone (the login page too)


def _rules(need: str, *routes: str) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for route in routes:
        method, _, path = route.partition(" ")
        found[(method, path)] = need
    return found


RULES: dict[tuple[str, str], str] = {
    **_rules(
        OPEN,
        "GET /login", "HEAD /login", "POST /login", "GET /healthz",
        "GET /who", "POST /who",  # in server mode: /who sends to /login, and its POST is refused (pages/who.py)
        "POST /shutdown",  # checked by the route itself: from the server's own console, with the launcher's token
    ),
    **_rules(
        SIGNED_IN,
        "GET /logout", "POST /logout", "GET /account/password", "POST /account/password",
        "POST /account/sessions/end-others", "GET /",
    ),
    **_rules(
        READ,
        # The review queue and the invoices, their pages and approved copies.
        "GET /review", "GET /review/{invoice_id:int}", "GET /review/{invoice_id:int}/page/{number:int}.png",
        "GET /review/{invoice_id:int}/words", "GET /review/{invoice_id:int}/reader",
        "GET /review/{invoice_id:int}/approved.pdf",
        "GET /search", "GET /search/export.csv", "GET /search/export.xlsx", "GET /search/{invoice_id:int}/approved.pdf",
        # The exports list, the reports and their downloads.
        "GET /exports", "GET /exports/duplicates.csv",
        "GET /month-end", "GET /month-end/accruals.csv",
        "GET /sales-tax", "GET /sales-tax/claims.csv", "GET /sales-tax/self-assessment.csv",
        "GET /statements", "GET /statements/reconciliation.csv",
        "GET /spend", "GET /spend/data.xlsx",
        "GET /insights", "GET /insights/business-case",
        "GET /activity", "GET /activity/events.csv", "GET /activity/controls-report", "GET /activity/duplicates.csv",
        "GET /help", "GET /help/quick-start", "GET /help/report",
        "GET /jobs/{name}",  # how far a job got: the job's own role besides (JOB_NEEDS)
    ),
    **_rules(
        CLERK,
        # Bringing invoices in.
        "GET /process", "POST /process/upload", "POST /process/folder", "GET /process/status",
        "GET /process/inbox/{entry_id}/file/{n}", "POST /process/inbox/{entry_id}/to-queue/{n}",
        "POST /process/inbox/{entry_id}/done", "POST /review/{invoice_id}/retry",
        # The review screen: edits, checks, the first approval, park, reject, reopen, undo (their own approval).
        "POST /review/{invoice_id:int}/vendor", "POST /review/{invoice_id:int}/check",
        "POST /review/{invoice_id:int}/approve", "POST /review/{invoice_id:int}/undo-approve",
        "POST /review/{invoice_id:int}/park", "POST /review/{invoice_id:int}/reject",
        "POST /review/{invoice_id:int}/note", "POST /review/{invoice_id:int}/teach",
        "POST /review/{invoice_id:int}/split", "POST /review/{invoice_id:int}/apply-rules",
        "POST /review/{invoice_id:int}/po-coding", "POST /review/{invoice_id:int}/suggestion",
        "POST /review/{invoice_id:int}/fill-lines", "POST /review/{invoice_id:int}/read-pages",
        "POST /review/{invoice_id:int}/review-again", "POST /review/{invoice_id:int}/start-over",
        "POST /review/{invoice_id:int}/resume", "POST /review/{invoice_id:int}/restore",
        "POST /review/{invoice_id:int}/correct",
        # The queue: bulk approval, reopen, back from parked.
        "POST /review/bulk-approve", "POST /review/{invoice_id}/reopen", "POST /review/{invoice_id}/unpark",
        "POST /search/{invoice_id:int}/unpark",
        "POST /search/presets", "POST /search/presets/default", "POST /search/presets/delete",  # their saved views
        # Export batches and their downloads.
        "POST /exports/create", "GET /exports/batches/{batch:int}/download",
        "GET /exports/batches/{batch:int}/approved-pdfs.zip", "GET /exports/jde/dry-run.txt",
        "POST /exports/batches/{batch:int}/jde-api/dry-run",  # the JDE API's dry run: nothing posted (D-104)
        # Statements compared, vendors' hold and notes, looking at the setup and what was learned.
        "POST /statements/upload", "POST /statements/emailed/{entry_id}/{n}",
        "GET /vendors", "POST /vendors/save",
        "GET /accounts", "GET /accounts/{table}.csv", "GET /accounts/bank/template.csv",
        "GET /purchase-orders", "GET /purchase-orders/template.csv",
        "GET /learning", "POST /learning/lessons/explain",
    ),
    **_rules(
        APPROVER,
        "POST /review/{invoice_id}/final-approve", "POST /review/{invoice_id}/send-back",
        # The go-ahead to post vouchers to JD Edwards through the API, and its queue (D-104).
        "POST /exports/batches/{batch:int}/jde-api/post", "POST /exports/jde-api/retry",
    ),
    **_rules(
        ADMIN,
        # Written out, though a route without a rule needs an Admin anyway: so the test sees each was decided.
        "POST /review/{invoice_id}/delete", "POST /review/sample-gl", "POST /process/open-folder",
        "POST /demo/load", "POST /demo/remove",
        "POST /exports/batches/{batch:int}/undo", "POST /exports/layout", "POST /exports/layout/preview",
        "POST /exports/batches/{batch:int}/jde-api/release",  # a voucher checked in JD Edwards, released (D-107)
        "POST /exports/register/upload", "POST /exports/register/import", "POST /exports/register/clear",
        "POST /accounts/sample", "POST /accounts/bank/upload", "POST /accounts/bank/import", "POST /accounts/bank/save",
        "POST /accounts/{table}/upload", "POST /accounts/{table}/import", "POST /accounts/{table}/save",
        "POST /accounts/tax", "POST /accounts/policy", "POST /accounts/rules", "POST /accounts/rules/add",
        "POST /vendors/master/upload", "POST /vendors/master/import",
        "POST /purchase-orders/upload", "POST /purchase-orders/import", "POST /purchase-orders/sample",
        "POST /purchase-orders/status", "POST /purchase-orders/delete",
        "POST /learning/lessons/retire", "POST /learning/suppliers/hold", "POST /learning/suppliers/allow",
        "POST /learning/suppliers/reset-layout", "POST /learning/memory/forget", "POST /learning/history/upload",
        "POST /learning/history/teach", "POST /learning/history/forget", "GET /learning/training.zip",
        "POST /insights/assumptions",
        "GET /settings", "GET /settings/{tab}", "GET /settings/azure-check", "POST /settings/azure-check",
        "POST /settings/azure-check/test", "POST /settings/review", "POST /settings/review/approval",
        "POST /settings/review/supply", "POST /settings/data/open-folder", "POST /settings/data/backup",
        "POST /settings/data/backup-copy", "GET /settings/data/backups/{name}", "POST /settings/data/restore",
        "POST /settings/about/show", "POST /settings/reading/reviewer", "POST /settings/reading/check",
        "POST /settings/reading/self-test", "GET /settings/reading/self-test", "POST /settings/reading/load-model",
        "POST /settings/reading/azure", "POST /settings/reading/azure-test", "POST /settings/automation/touchless",
        "POST /settings/automation/limit", "POST /settings/jde", "POST /settings/jde/gl-map",
        "POST /settings/jde/gl-map/import", "POST /settings/jde/tax-map", "POST /settings/jde/terms",
        "POST /settings/jde/an8", "GET /settings/jde/worksheet.xlsx", "POST /settings/jde/worksheet",
        "POST /settings/jde/api", "POST /settings/jde/api/test", "POST /settings/jde/api/sync",  # D-104
        "POST /settings/hub", "POST /settings/hub/run",  # the finance hub feed (D-103)
        "GET /admin/users", "POST /admin/users/add", "POST /admin/users/{user_id:int}/reset",
        "POST /admin/users/{user_id:int}/role", "POST /admin/users/{user_id:int}/deactivate",
        "POST /admin/users/{user_id:int}/activate", "POST /admin/users/addresses/unlock",
        "POST /admin/users/usernames/unlock",
        "GET /help/report/setup",  # the server's versions, settings and names (D-109)
    ),
}  # fmt: skip

# What seeing a job's progress needs (GET /jobs/{name}): the role that may start it (D-109). Any other job: an Admin.
JOB_NEEDS = {"process": READ, "jde-api": CLERK, "jde-sync": ADMIN, "settings-doctor": ADMIN, "azure-test": ADMIN}


def allowed(role: str | None, need: str) -> bool:
    """Whether someone with ``role`` may use a route that needs ``need``."""
    if need == OPEN:
        return True
    if not role or role not in RANK:
        return False
    return RANK[role] >= NEEDS.get(need, NEEDS[ADMIN])


def leaf_routes(routes: Any) -> list[Any]:
    """Every route of an app or router, routers included in it opened up, in the order requests are matched."""
    found: list[Any] = []
    for route in routes:
        inner = getattr(route, "original_router", None)  # FastAPI's include_router
        found.extend(leaf_routes(inner.routes) if inner is not None else [route])
    return found


def route_key(route: Any, method: str) -> tuple[str, str]:
    return (method, getattr(route, "path", ""))


def need_for(app: Any, scope: dict[str, Any]) -> str:
    """What the request needs: the rule of the route that answers it (a HEAD as its GET), ``OPEN`` for the styles
    and scripts, ``ADMIN`` for anything without a rule or no route at all."""
    method = str(scope.get("method") or "GET").upper()
    method = "GET" if method == "HEAD" else method
    probe = {**scope, "method": method}
    routes = getattr(app.state, "_leaf_routes", None) if app is not None else None
    if routes is None:
        routes = leaf_routes(getattr(app, "routes", []) or [])
        if app is not None:
            app.state._leaf_routes = routes
    for route in routes:
        try:
            match, _ = route.matches(probe)
        except Exception:  # noqa: BLE001 - a route that can't say: not this one
            continue
        if match == Match.FULL:
            path = getattr(route, "path", "")
            if not hasattr(route, "methods") and path in MOUNTS_OPEN:  # a Mount
                return OPEN
            return RULES.get(route_key(route, method), ADMIN)
    return ADMIN


def can(app: Any, role: str | None, method: str, path: str) -> bool:
    """Whether ``role`` may send ``method`` to ``path`` (for the pages, to hide what can't be used)."""
    scope = {"type": "http", "method": method.upper(), "path": path, "root_path": "", "query_string": b"",
             "headers": []}  # fmt: skip
    return allowed(role, need_for(app, scope))
