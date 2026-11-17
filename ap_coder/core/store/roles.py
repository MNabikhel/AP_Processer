"""The roles an Admin makes (D-250), beside the five built in (``core/permissions.py``), and a person's role.

* **A role made here** (``roles``): its key (``role-<n>``, kept in ``users.role``), its name (unique, not one of the
  built-in ones, case aside) and its permissions (a JSON list of ``permissions.PERMISSIONS``' keys). Made from scratch
  or from another role's permissions, changed, and deleted only while no account has it (its row kept, marked
  ``deleted_at``, so its key is never given to another role: old events and the finance hub feed name it). A
  built-in role is never stored: its permissions are fixed in the code.
* **Who manages the people** is any active account whose role has ``users`` (``_active_admins``): there is always one
  (``LAST_ADMIN``); an Admin never takes it from themselves (``OWN_USERS``), by their own role nor by changing the
  role they have.
* **Nobody gives, or manages, more than their own role may do** (``by_user``, the account acting on a page;
  ``permissions.beyond``): no role made, changed or given with a permission theirs lacks (``GIVES_MORE``), and no
  account changed, reset, turned off or on, or signed out everywhere that may do more than theirs (``MANAGES_MORE``),
  checked in the transaction that writes. The server's console (no ``by_user``) is the server's owner: not held back.

Every role made, changed or deleted, and every role given, is an event of the Activity log, with who and what changed
(the names kept in it, so the log reads the same once a role is renamed or deleted).
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from .. import permissions as perms
from ._base import _now
from .dialect import begin_write, in_list, nocase

CUSTOM = "role-"  # the key of a role made here: role-1, role-2...
MAX_ROLE_NAME = 40
OWN_USERS = ("You can't take away your own Manage users and roles: another Admin changes your role, so AP Coder "
             "always has someone who can.")  # fmt: skip
ROLE_IN_USE = "{name} is the role of {count}: give them another role first, then delete it."
GIVES_MORE = "You can't give more than your own role may do ({missing}): someone whose role has that does it."
MANAGES_MORE = ("{whose} role may do more than yours ({missing}): someone whose role has that changes "
                "{what}.")  # fmt: skip
BOTH = ("A role either reviews invoices or confirms coding as a manager: untick Review and code invoices or Confirm "
        "coding as a manager (a role that reviews invoices opens every manager's request already).")  # fmt: skip
LIVE = "deleted_at = ''"  # a role not deleted


def role_name_of(conn: Any, key: str) -> str:
    """What a role is called: a built-in one's label, a made one's name, else its key."""
    if key in perms.ROLE_LABELS:
        return perms.ROLE_LABELS[key]
    found = conn.execute("SELECT name FROM roles WHERE key = ?", (key,)).fetchone()
    return found[0] if found else key


def _said(keys: list[str]) -> str:
    return ", ".join(perms.PERMISSIONS[k][0] for k in keys)


def _clean_name(name: Any) -> str:
    """A role's name as kept: NFKC (a full-width "Ａｄｍｉｎ" is "Admin"), one space for a run of spaces or controls."""
    plain = unicodedata.normalize("NFKC", str(name or ""))
    kept = "".join(" " if unicodedata.category(c).startswith(("C", "Z")) else c for c in plain)
    return " ".join(kept.split())[:MAX_ROLE_NAME].strip()


def _clean_permissions(chosen: Any) -> list[str]:
    """The permissions ticked, in the order of ``PERMISSIONS``; ValueError for none, or one AP Coder doesn't have."""
    picked = {str(p) for p in chosen or ()}
    unknown = sorted(picked - set(perms.PERMISSIONS))
    if unknown:
        raise ValueError(f"There is no permission {unknown[0]!r}.")
    if not picked:
        raise ValueError("Tick at least one thing this role may do.")
    picked = perms.with_view(picked)  # whoever works on invoices sees them (B-2585)
    if {perms.CODE, perms.CONFIRM} <= picked:  # a manager's page shows their own requests only without code
        raise ValueError(BOTH)
    return [p for p in perms.PERMISSIONS if p in picked]


def _row(row: Any, people: int = 0) -> dict[str, Any]:
    return {"key": row["key"], "name": row["name"], "permissions": perms.permissions_of(row["key"], row["permissions"]),
            "built_in": False, "created_at": row["created_at"], "updated_at": row["updated_at"],
            "people": people}  # fmt: skip


class RolesMixin:
    """The roles an Admin makes, and a person's role (D-250)."""

    # --- Roles ---------------------------------------------------------------------------------------------------

    def custom_roles(self) -> list[dict[str, Any]]:
        """The roles made here, by name, each with how many accounts have it (turned off ones too)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT r.*, (SELECT COUNT(*) FROM users u WHERE u.role = r.key) AS people FROM "
                                f"roles r WHERE {LIVE} ORDER BY {nocase('r.name')}").fetchall()  # fmt: skip
        return [_row(r, int(r["people"])) for r in rows]

    def all_roles(self) -> list[dict[str, Any]]:
        """Every role, the built-in ones first (in ``ROLES``' order), then the ones made here by name."""
        with self._conn() as conn:
            counts = dict(conn.execute("SELECT role, COUNT(*) FROM users GROUP BY role").fetchall())
        built = [{"key": k, "name": perms.ROLE_LABELS[k], "permissions": sorted(perms.BUILT_IN[k],
                  key=list(perms.PERMISSIONS).index), "built_in": True, "note": perms.ROLE_NOTES[k],
                  "people": int(counts.get(k, 0))} for k in perms.ROLES]  # fmt: skip
        return built + self.custom_roles()

    def role_info(self, key: str) -> dict[str, Any] | None:
        """One role (built in or made here) as ``all_roles`` gives it; None when there is no such role."""
        return next((r for r in self.all_roles() if r["key"] == key), None)

    def _check_name(self, conn: Any, name: Any, key: str = "") -> str:
        shown = _clean_name(name)
        if not shown:
            raise ValueError("Give the role a name (up to 40 characters), e.g. AP lead.")
        folded = shown.casefold()
        if folded in {n.casefold() for n in (*perms.ROLE_LABELS.values(), *perms.ROLE_LABELS)}:
            raise ValueError(f"{shown} is a built-in role: give yours another name.")
        if re.fullmatch(re.escape(CUSTOM) + r"\s*\d+", folded):  # a role's key (the console takes a key or a name)
            raise ValueError(f"{shown} is how AP Coder names a role inside: give yours another name.")
        taken = conn.execute(f"SELECT name FROM roles WHERE {nocase('name')} = ? AND key != ? AND {LIVE}",
                             (shown, key))  # fmt: skip
        if (found := taken.fetchone()) is not None:
            raise ValueError(f"There is already a role {found[0]}.")
        return shown

    def add_role(self, name: str, chosen: Any, *, actor: str = "", by_user: int | None = None) -> str:
        """A new role of ``name`` with the permissions ``chosen``; its key, never one a deleted role had. ValueError
        (in words) for a name taken or empty, no permission, or more than ``by_user``'s role may do."""
        kept = _clean_permissions(chosen)
        with self._conn() as conn:
            begin_write(conn)
            self._may_give(conn, by_user, kept)
            shown = self._check_name(conn, name)
            last = conn.execute(f"SELECT COALESCE(MAX(CAST(substr(key, {len(CUSTOM) + 1}) AS INTEGER)), 0) FROM "
                                "roles").fetchone()[0]  # fmt: skip  # (the deleted ones too)
            key = f"{CUSTOM}{int(last) + 1}"
            conn.execute("INSERT INTO roles (key, name, permissions, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                         (key, shown, json.dumps(kept), _now(), _now()))  # fmt: skip
            self._log(conn, "role_created", None, actor or None, {"role": key, "name": shown, "permissions": kept})
        return key

    def edit_role(self, key: str, name: str, chosen: Any, *, actor: str = "", by_user: int | None = None) -> None:
        """A role made here renamed, or given other permissions. Its ``users`` permission stays while it is the role
        of ``by_user`` (the Admin changing it: ``OWN_USERS``), or of the last active accounts that manage the people
        (``LAST_ADMIN``). ``by_user`` changes only a role within their own, to one within it (``MANAGES_MORE``,
        ``GIVES_MORE``). ValueError for a built-in role (fixed), or as ``add_role``; KeyError for no such role."""
        from .users import LAST_ADMIN

        if key in perms.BUILT_IN:
            raise ValueError(f"{perms.ROLE_LABELS[key]} is a built-in role: it can't be changed. Make a copy of it.")
        kept = _clean_permissions(chosen)
        with self._conn() as conn:
            begin_write(conn)
            row = conn.execute(f"SELECT * FROM roles WHERE key = ? AND {LIVE}", (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            before = perms.permissions_of(key, row["permissions"])
            self._may_manage(conn, by_user, before, f"The {row['name']}", "it")
            self._may_give(conn, by_user, kept)
            shown = self._check_name(conn, name, key)
            if perms.USERS in before and perms.USERS not in kept:
                mine = by_user is not None and conn.execute("SELECT 1 FROM users WHERE id = ? AND role = ?",
                                                            (int(by_user), key)).fetchone()  # fmt: skip
                if mine:
                    raise ValueError(OWN_USERS)
                holders = conn.execute("SELECT COUNT(*) FROM users WHERE role = ? AND active = 1", (key,)).fetchone()[0]
                if holders and self._active_admins(conn) <= holders:
                    raise ValueError(LAST_ADMIN)
            if shown == row["name"] and kept == list(before):
                return
            conn.execute("UPDATE roles SET name = ?, permissions = ?, updated_at = ? WHERE key = ?",
                         (shown, json.dumps(kept), _now(), key))  # fmt: skip
            change = {"role": key, "name": shown, "added": [p for p in kept if p not in before],
                      "removed": [p for p in before if p not in kept]}  # fmt: skip
            if shown != row["name"]:
                change["was"] = row["name"]
            self._log(conn, "role_edited", None, actor or None, change)

    def delete_role(self, key: str, *, actor: str = "", by_user: int | None = None) -> None:
        """A role made here deleted, only while no account has it (turned off ones too: ``ROLE_IN_USE``), and never by
        ``by_user`` when it may do more than theirs (``MANAGES_MORE``), as for changing it."""
        if key in perms.BUILT_IN:
            raise ValueError(f"{perms.ROLE_LABELS[key]} is a built-in role: it can't be deleted.")
        with self._conn() as conn:
            begin_write(conn)
            row = conn.execute(f"SELECT * FROM roles WHERE key = ? AND {LIVE}", (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            self._may_manage(conn, by_user, perms.permissions_of(key, row["permissions"]), f"The {row['name']}", "it")
            count = conn.execute("SELECT COUNT(*) FROM users WHERE role = ?", (key,)).fetchone()[0]
            if count:
                people = f"{count} account{'s' if count != 1 else ''}"
                raise ValueError(ROLE_IN_USE.format(name=row["name"], count=people))
            conn.execute("UPDATE roles SET deleted_at = ?, updated_at = ? WHERE key = ?", (_now(), _now(), key))
            self._log(conn, "role_deleted", None, actor or None, {"role": key, "name": row["name"]})

    # --- A person's role -----------------------------------------------------------------------------------------

    @staticmethod
    def _active_admins(conn: Any) -> int:
        """The active accounts that manage the people: an Admin's, or a role made here with ``users``."""
        keys = [r["key"] for r in conn.execute("SELECT key, permissions FROM roles").fetchall()
                if perms.USERS in json.loads(r["permissions"] or "[]")]  # fmt: skip
        sql = f"SELECT COUNT(*) FROM users WHERE active = 1 AND (role = ? OR {in_list('role')})"
        return int(conn.execute(sql, (perms.ADMIN, json.dumps(keys))).fetchone()[0])

    @staticmethod
    def _role_permissions(conn: Any, role: str) -> list[str]:
        """What ``role`` (a built-in key or one made here) may do; none for no such role."""
        if role in perms.BUILT_IN:
            return perms.permissions_of(role)
        found = conn.execute(f"SELECT permissions FROM roles WHERE key = ? AND {LIVE}", (role,)).fetchone()
        return perms.permissions_of(role, found[0]) if found is not None else []

    def _manages(self, conn: Any, role: str) -> bool:
        """Whether ``role`` (a built-in key or one made here) has ``users``."""
        return perms.USERS in self._role_permissions(conn, role)

    @staticmethod
    def _role_known(conn: Any, role: str) -> bool:
        return (
            role in perms.BUILT_IN
            or conn.execute(f"SELECT 1 FROM roles WHERE key = ? AND {LIVE}", (role,)).fetchone() is not None
        )

    def _granted(self, conn: Any, by_user: int | None) -> frozenset[str] | None:
        """What account ``by_user`` may do now (nothing once turned off or gone); None for the console (no account)."""
        if by_user is None:
            return None
        row = conn.execute("SELECT role, active FROM users WHERE id = ?", (int(by_user),)).fetchone()
        if row is None or not row["active"]:
            return frozenset()
        return frozenset(self._role_permissions(conn, row["role"]))

    def _may_give(self, conn: Any, by_user: int | None, wanted: Any) -> None:
        """ValueError ``GIVES_MORE`` when ``wanted`` (permissions) is more than ``by_user``'s role may do."""
        have = self._granted(conn, by_user)
        missing = [] if have is None else perms.beyond(have, wanted)
        if missing:
            raise ValueError(GIVES_MORE.format(missing=_said(missing)))

    def _may_manage(self, conn: Any, by_user: int | None, theirs: Any, whose: str, what: str) -> None:
        """ValueError ``MANAGES_MORE`` when ``theirs`` (an account's or a role's permissions) is more than ``by_user``'s
        role may do."""
        have = self._granted(conn, by_user)
        missing = [] if have is None else perms.beyond(have, theirs)
        if missing:
            raise ValueError(MANAGES_MORE.format(whose=whose, missing=_said(missing), what=what))

    def _may_manage_account(self, conn: Any, by_user: int | None, row: Any) -> None:
        """``_may_manage`` of the account ``row`` (users)."""
        self._may_manage(conn, by_user, self._role_permissions(conn, row["role"]), f"{row['username']}'s",
                         "their account")  # fmt: skip

    def set_role(self, user_id: int, role: str, *, actor: str = "", by_user: int | None = None) -> None:
        """A new role (a built-in one or one made here). The last active account that manages the people keeps that
        (ValueError ``LAST_ADMIN``), and an Admin never takes it from themselves (``by_user``: ``OWN_USERS``), nor
        changes an account that may do more than they, or gives a role that does (``MANAGES_MORE``, ``GIVES_MORE``)."""
        from .users import LAST_ADMIN, unknown_role

        with self._conn() as conn:
            begin_write(conn)
            if not self._role_known(conn, role):
                raise ValueError(unknown_role(role))
            row = self._account(conn, user_id)
            if row["role"] == role:
                return
            self._may_manage_account(conn, by_user, row)
            self._may_give(conn, by_user, self._role_permissions(conn, role))
            losing = self._manages(conn, row["role"]) and not self._manages(conn, role)
            if losing and row["active"] and self._active_admins(conn) <= 1:
                raise ValueError(LAST_ADMIN)
            if losing and by_user is not None and int(by_user) == row["id"]:
                raise ValueError(OWN_USERS)
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, row["id"]))
            names = {} if role in perms.BUILT_IN and row["role"] in perms.BUILT_IN else {
                "from_name": role_name_of(conn, row["role"]), "to_name": role_name_of(conn, role)}  # fmt: skip
            self._log(conn, "role_changed", None, actor or None,
                      {"username": row["username"], "from": row["role"], "to": role, **names})  # fmt: skip
