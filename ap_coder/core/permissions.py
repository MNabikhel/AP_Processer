"""What a person may do where people sign in (D-250): the permissions a role is made of, and the five built-in roles.

Each route of the dashboard needs one permission (``web/roles.py`` ``RULES``), or is open, or only needs a session. A
role is a set of permissions: the five built in here, fixed, giving exactly the access they gave when roles were a
ladder (``tests/web/test_role_permissions.py`` compares every route with the table kept from before,
``tests/fixtures/role_access_before.json``), and the roles an Admin makes from them (``core/store/roles.py``).

* **view**: the review queue, the invoices and their pages, the reports and their downloads, the exports list,
  Insights, Spend, Help.
* **activity**: the Activity log and the controls report (the logins and the accounts' changes in it need **users**).
* **import**: bring invoices in (upload, the folder, the emails), read a failed one again, compare vendor statements.
* **code**: review and code invoices: edit, check, park, reject, reopen, notes, send to a manager and record their
  answer, saved views; see the setup pages (vendors, accounts, JD Edwards files, purchase orders, learning); a
  vendor's hold and notes; the coding rules.
* **approve**: the first approval, taking one's own back, bulk approval.
* **final_approve**: the second approval of an invoice over the approval limit, or sending it back (never the person
  who gave the first: ``users.same_approver``, D-109).
* **export**: export batches and their downloads, the JD Edwards dry run, the Spend workbook made in the background.
* **post**: the go-ahead to post vouchers to JD Edwards through its API (D-104), and its queue.
* **controls**: take a vendor off hold, approve a held vendor's invoice coded to another vendor (D-142), and keep the
  company's bank accounts (D-220).
* **setup**: change the setup: the account lists and tax, the vendor master, JD Edwards' files, the purchase orders, the
  ERP's invoice list, the learning memory.
* **undo**: delete invoices, undo export batches and anyone's approval, release a voucher held in JD Edwards (D-107).
* **settings**: Settings (connections, backups and restore, the review, reading and automation settings), the demo,
  the export layout; see the server's own folders and versions (``access.is_admin``).
* **users**: the people who sign in and their roles, their sign-ins (the Admin panel, D-249).
* **confirm**: confirm, or send back, the coding of the invoices sent to them, as a manager (D-138): their own
  requests only, unless the role also has **code** (AP's view: every request).

No permission sets an approval limit: the code has one, the company's (Settings → Automation), over which an invoice
needs a second approval (**final_approve**); none per person.
"""

from __future__ import annotations

import json
from typing import Any

READONLY, CLERK, APPROVER, ADMIN = "readonly", "clerk", "approver", "admin"
MANAGER = "manager"  # confirms the cost allocation of the invoices sent to them, nothing else (D-138)
ROLES = (READONLY, CLERK, APPROVER, ADMIN, MANAGER)  # the built-in roles, in the order they are shown
ROLE_LABELS = {READONLY: "Read-only", CLERK: "Clerk", APPROVER: "Approver", ADMIN: "Admin", MANAGER: "Manager"}

VIEW, ACTIVITY, IMPORT, CODE, APPROVE, FINAL = "view", "activity", "import", "code", "approve", "final_approve"
EXPORT, POST, CONTROLS, SETUP, UNDO, SETTINGS = "export", "post", "controls", "setup", "undo", "settings"
USERS, CONFIRM = "users", "confirm"

# Each permission, in the order the Roles page lists them: (what it is called, what it opens).
PERMISSIONS: dict[str, tuple[str, str]] = {
    VIEW: (
        "See invoices and reports",
        "The review queue, the invoices and their pages, the reports, the exports list, Insights, Spend and Help.",
    ),
    ACTIVITY: (
        "See the activity log",
        "Who did what, and the controls report. Logins and account changes need Manage users and roles.",
    ),
    IMPORT: (
        "Bring invoices in",
        "Upload invoices and emails, pick up the invoices folder, read a failed one again, compare vendor statements.",
    ),
    CODE: (
        "Review and code invoices",
        "Edit, check, park, reject and reopen invoices, send one to a manager, the "
        "coding rules, a vendor's hold and notes; see the setup pages.",
    ),
    APPROVE: ("Approve invoices", "The first approval (one at a time or in bulk), and taking one's own back."),
    FINAL: (
        "Give the second approval",
        "Approve an invoice over the approval limit after someone else did, or send it back.",
    ),
    EXPORT: (
        "Make export batches",
        "Export approved invoices and download the batches, the JD Edwards dry run, the Spend workbook.",
    ),
    POST: ("Post to JD Edwards", "Give the go-ahead to post vouchers to JD Edwards through its API."),
    CONTROLS: (
        "Lift holds and keep bank accounts",
        "Take a vendor off hold, and add, change or remove the company's bank accounts.",
    ),
    SETUP: (
        "Change the setup",
        "Import or edit the account lists, tax setup, vendor master, JD Edwards files, "
        "purchase orders and the ERP's invoice list; reset what was learned.",
    ),
    UNDO: (
        "Delete and undo",
        "Delete invoices, undo export batches and anyone's approval, release a voucher held in JD Edwards.",
    ),
    SETTINGS: (
        "Change settings",
        "Settings: connections, backups and restore, review, reading and automation; the demo; the export layout.",
    ),
    USERS: (
        "Manage users and roles",
        "Add people, change their role, reset passwords, turn accounts off; make and edit roles; see sign-ins.",
    ),
    CONFIRM: (
        "Confirm coding as a manager",
        "Confirm or send back the coding of the invoices AP sends them, and "
        "nothing else unless the role also reviews invoices.",
    ),
}

_READ = frozenset({VIEW, ACTIVITY})
_CLERK = _READ | {IMPORT, CODE, APPROVE, EXPORT}
_APPROVER = _CLERK | {FINAL, POST, CONTROLS}
BUILT_IN: dict[str, frozenset[str]] = {
    READONLY: _READ,
    CLERK: _CLERK,
    APPROVER: _APPROVER,
    ADMIN: _APPROVER | {SETUP, UNDO, SETTINGS, USERS},  # everything but a manager's own confirmations
    MANAGER: frozenset({CONFIRM}),
}
ROLE_NOTES = {  # what each built-in role is for, in a line
    READONLY: "Looks at the review queue, invoices, reports, exports, Insights, Spend, Activity and Help. Changes "
    "nothing.",
    CLERK: "Also imports invoices, reviews, checks and approves them (the first approval), parks, rejects and reopens "
    "them, approves in bulk and makes export batches.",
    APPROVER: "Also gives the second approval to invoices over the approval limit. It must be another person than the "
    "first approver.",
    ADMIN: "Everything, including Settings, users, the GL accounts and tax setup, imports, JD Edwards, backups and "
    "restore, and deleting.",
    MANAGER: "Outside the others. Only confirms, or sends back, the coding of the invoices AP sends them (Manager "
    "confirmations), for the cost centers set on their page; sees those invoices and nothing else.",
}


def built_in(role: str | None) -> frozenset[str]:
    """The permissions of a built-in role (none for any other key)."""
    return BUILT_IN.get(str(role or ""), frozenset())


def permissions_of(role: str | None, stored: str | None = None) -> list[str]:
    """A role's permissions in the order of ``PERMISSIONS``: a built-in role's, else those of ``stored`` (a role made
    by an Admin: its JSON list; a key AP Coder doesn't know is left out, and so is a list that can't be read)."""
    if role in BUILT_IN:
        found: Any = BUILT_IN[str(role)]
    else:
        try:
            found = json.loads(stored or "[]")
        except ValueError:
            found = []
        found = {str(p) for p in found} if isinstance(found, list) else set()
    return [p for p in PERMISSIONS if p in found]
