"""The people who log in when AP Coder runs on a company server (server mode, D-97), their sessions and the tries
that did not match.

* **Accounts** (``users``): a username (kept case-folded: "Ann" and "ann" are one account), the name shown, a role
  (``ROLES``: Read-only, Clerk, Approver, Admin, D-98), the password's scrypt hash, active or turned off, and whether
  the person must choose a new password at their next login (a new account, a reset).
* **Passwords**: scrypt (n=2^14, r=8, p=1, a 16-byte salt) from the standard library, stored as
  ``scrypt$n$r$p$<salt>$<hash>`` (base64) and compared in constant time. At least 12 characters, not the username
  or the name, nor easy to guess (``password_problem``, D-101).
* **Lockout** (D-101): 5 tries that don't match within 15 minutes for one username from one address pause logins
  for that username from that address for 15 minutes; 30 from one address, whatever the usernames, pause that
  address (a shared NAT, RDS or proxy address is not paused by five people mistyping). Someone elsewhere can't lock a
  person out; the server's own console is never paused by address. An unknown username is counted the same way (by
  a hash: the text typed is never kept), and every refusal says the same words, so nothing tells whether a username
  exists. A try is recorded before its password is checked, in one transaction with the check of the pause, so
  guesses sent in parallel get no more checks than guesses sent one by one.
* **Sessions** (``sessions``): a random 32-byte token in the browser's cookie; only its SHA-256 is stored, so a copy
  of the database logs nobody in. A session ends after 8 hours unused or 7 days in all, at logout, and when the
  person changes their password (their other sessions; the one they changed it from gets a new token) or an Admin
  resets it or turns the account off (all of them). They are kept in the database: a restart of AP Coder logs
  nobody out; a restore of a backup logs everybody out (``carry_accounts``).

Every login, refusal, lockout, logout, password change, account added, role change and account turned off or on is
an event of the audit trail (``core/audit.py`` says each in words).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import time
import unicodedata
from typing import Any

from ._base import _now

READONLY, CLERK, APPROVER, ADMIN = "readonly", "clerk", "approver", "admin"
ROLES = (READONLY, CLERK, APPROVER, ADMIN)  # each can do what the one before it can, and more (web/roles.py)
ROLE_LABELS = {READONLY: "Read-only", CLERK: "Clerk", APPROVER: "Approver", ADMIN: "Admin"}

MIN_PASSWORD = 12
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SALT_BYTES, HASH_BYTES = 16, 64
_SCRYPT_MAXMEM = 64 * 1024 * 1024
TOKEN_BYTES = 32
IDLE_SECONDS = 8 * 3600  # a session unused this long ends
ABSOLUTE_SECONDS = 7 * 86400  # and any session this old
SEEN_EVERY = 60  # seconds between two writes of a session's last use
MAX_FAILURES = 5  # tries that don't match within FAILURE_WINDOW for one username from one address...
ADDRESS_FAILURES = 30  # ...or from one address, whatever the usernames...
FAILURE_WINDOW = 15 * 60
LOCK_SECONDS = 15 * 60  # ...pause logins (that username there, or that address) for this long
UNKNOWN = "?"  # how login_attempts keeps a username no account has: "?" and a hash, never the text typed
USERNAME = re.compile(r"[a-z0-9][a-z0-9._@-]{0,63}")
MAX_DISPLAY_NAME = 60

WRONG = "That username and password don't match. Check them and try again."
LOCKED = (
    "Too many tries that didn't match: logging in from this computer is paused for 15 minutes. Wait, then try "
    "again, or ask an Admin to reset your password."
)
LAST_ADMIN = "AP Coder needs at least one active Admin: make another person an Admin first."
EASY_PASSWORD = (
    "That password is too easy to guess: choose one that is not your username or your name, not a common password, "
    "and not one character or a short pattern repeated. A few unrelated words make a good one."
)
# Common passwords of 12 characters or more (shorter ones are refused anyway), compared without case or spaces.
COMMON_PASSWORDS = frozenset(
    """
password1234 password12345 password123456 password1234! password123! passw0rd1234 p@ssw0rd1234 p@ssword1234
qwertyuiop12 qwertyuiop123 qwerty123456 qwertyuiop1! 1qaz2wsx3edc 1q2w3e4r5t6y q1w2e3r4t5y6 asdfghjkl123
zxcvbnm12345 welcome12345 welcome123456 welcome2024! welcome2025! welcome2026! changeme1234 changeme123!
letmein12345 iloveyou1234 trustno11234 monkey123456 dragon123456 football1234 baseball1234 sunshine1234
princess1234 superman1234 starwars1234 master123456 123456789012 1234567890ab 111111111111 123123123123
abc123456789 abcdefghijkl abcd12345678 administrator admin1234567 adminadmin12 summer202612 winter202612
spring202612 autumn202612 canada123456 toronto12345
""".split()
)

_clock = time.time  # tests move it by hand


class LoginRefused(Exception):
    """A login that did not go through; its message is what the login page says (``WRONG`` or ``LOCKED``)."""


def fold_username(username: Any) -> str:
    return unicodedata.normalize("NFKC", str(username or "")).strip().casefold()


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    """``scrypt$16384$8$1$<salt>$<hash>``, the salt new each time."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(str(password).encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            maxmem=_SCRYPT_MAXMEM, dklen=HASH_BYTES)  # fmt: skip
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Whether ``password`` is the one ``stored`` was made from (compared in constant time). A hash that is not ours,
    damaged, or asks for more work than ours ever does never matches."""
    try:
        kind, n, r, p, salt, digest = str(stored or "").split("$")
        n, r, p = int(n), int(r), int(p)
        salt_bytes, expected = base64.b64decode(salt, validate=True), base64.b64decode(digest, validate=True)
    except (ValueError, binascii.Error):
        return False
    if kind != "scrypt" or not (2 <= n <= 2**17 and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 4) or not expected:
        return False
    try:
        found = hashlib.scrypt(str(password).encode("utf-8"), salt=salt_bytes, n=n, r=r, p=p, maxmem=_SCRYPT_MAXMEM,
                               dklen=len(expected))  # fmt: skip
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(found, expected)


_DUMMY: list[str] = []  # a hash checked for an unknown username, so its refusal takes as long as a wrong password


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_hex(16)))
    return _DUMMY[0]


_REPEATED = re.compile(r"(.{1,6}?)\1+|(.+?)\2{2,}", re.DOTALL)  # a short pattern again and again, or anything 3 times


def password_problem(password: str, username: str = "", display_name: str = "") -> str:
    """What is wrong with a new password, in words ("" when it may be used): fewer than 12 characters, or easy to
    guess (``EASY_PASSWORD``, one message): only spaces, one character or a short pattern repeated, the username or
    the name (or holding the username), or a common password."""
    text = str(password or "")
    if len(text) < MIN_PASSWORD:
        return f"A password needs at least {MIN_PASSWORD} characters (a few words make a good one)."
    plain = "".join(fold_username(text).split())
    user = "".join(fold_username(username).split())
    name = "".join(fold_username(display_name).split())
    if (
        not plain
        or _REPEATED.fullmatch(plain)
        or (user and (plain == user or (len(user) >= 4 and user in plain)))
        or (name and plain == name)
        or plain in COMMON_PASSWORDS
    ):
        return EASY_PASSWORD
    return ""


def temporary_password() -> str:
    """A password for a new account or a reset, read out or copied once: four groups of four letters and digits
    (no 0/O or 1/l to mix up)."""
    letters = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(letters) for _ in range(4)) for _ in range(4))


def _clean_display(name: Any) -> str:
    kept = "".join(" " if unicodedata.category(c).startswith(("C", "Z")) else c for c in str(name or ""))
    return " ".join(kept.split())[:MAX_DISPLAY_NAME].strip()


_USER_COLUMNS = "id, username, display_name, role, active, must_change, created_at, last_login"


def _public(row: Any) -> dict[str, Any] | None:
    """A user as the rest of AP Coder sees it: never their password's hash."""
    if row is None:
        return None
    found = {k: row[k] for k in row.keys() if k != "pw_hash"}
    found["active"], found["must_change"] = bool(found.get("active")), bool(found.get("must_change"))
    found["name"] = found.get("display_name") or found.get("username") or ""
    found["role_label"] = ROLE_LABELS.get(found.get("role") or "", found.get("role") or "")
    return found


ACCOUNT_LOGIN = "user:"  # the login of a person's account in server mode (web/core.py ``login``, D-98)


def account_of(login: str | None) -> int | None:
    """The account id of a server-mode login ("user:12" -> 12); None for any other login (a computer's)."""
    text = str(login or "")
    if not text.startswith(ACCOUNT_LOGIN) or not text[len(ACCOUNT_LOGIN) :].isdigit():
        return None
    return int(text[len(ACCOUNT_LOGIN) :])


def same_approver(first_name: str | None, first_login: str | None, name: str | None, login: str | None) -> bool:
    """Whether the person approving (``name``, ``login``) is the one who gave the first approval. Two accounts (server
    mode) are compared by their ids alone: two people may have the same name, one person can't have two names.
    Otherwise as on a laptop or the office network (D-34): the same name, or the same computer login."""
    first_account, account = account_of(first_login), account_of(login)
    if first_account is not None and account is not None:
        return first_account == account
    if (name or "").strip().casefold() == (first_name or "").strip().casefold():
        return True
    return bool(login) and bool(first_login) and str(login).casefold() == str(first_login).casefold()


# --- A restore keeps the accounts (D-101) ----------------------------------------------------------------------------

KEPT_TABLES = ("users", "login_attempts")  # kept as they are across a restore on the server
ACCOUNT_TABLES = (*KEPT_TABLES, "sessions")  # sessions: every one ends at a restore


def saved_accounts(conn: Any) -> dict[str, tuple[list[str], list[str], list[tuple]]]:
    """The accounts and the login tries (lockouts) of a database, to put back after a restore (``carry_accounts``):
    for each table, the statements that make it and its indexes, its columns and its rows (none for sessions)."""
    found = {}
    for table in ACCOUNT_TABLES:
        made = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name = ? AND sql IS NOT NULL "
                                           "ORDER BY type DESC", (table,))]  # fmt: skip
        if not made:
            continue
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        rows = [] if table not in KEPT_TABLES else [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")]
        found[table] = (made, columns, rows)
    return found


def carry_accounts(conn: Any, saved: dict[str, tuple[list[str], list[str], list[tuple]]]) -> None:
    """After a backup was restored into ``conn``'s database: the accounts and the lockouts as they were before
    (``saved``), and no session at all, so nobody is logged in by a session the backup knew and an account turned off
    or a password changed since that backup stay so. When there was no account before, the backup's accounts are kept:
    a restore never leaves the server with none. A backup older than the accounts gets their tables."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for table, (made, _columns, _rows) in saved.items():
        if table not in tables:
            for statement in made:
                conn.execute(statement)
    replace = KEPT_TABLES if saved.get("users", ([], [], []))[2] else ("login_attempts",)
    for table in replace:
        if table not in saved:
            continue
        _made, columns, rows = saved[table]
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        keep = [i for i, c in enumerate(columns) if c in have]
        conn.execute(f"DELETE FROM {table}")
        if rows and keep:
            names = ", ".join(columns[i] for i in keep)
            conn.executemany(f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' * len(keep))})",
                             [tuple(r[i] for i in keep) for r in rows])  # fmt: skip
    if "sessions" in tables or "sessions" in saved:
        conn.execute("DELETE FROM sessions")


class UsersMixin:
    """Accounts, sessions and login attempts (server mode, D-97)."""

    # --- Accounts ---------------------------------------------------------------------------------------------------

    def add_user(self, username: str, password: str, role: str = CLERK, display_name: str = "", *,
                 actor: str = "", must_change: bool = True) -> int:  # fmt: skip
        """A new account; ValueError (in words) for a username taken or not allowed, a role that is not one of
        ``ROLES`` or a password the policy refuses."""
        name = fold_username(username)
        if not USERNAME.fullmatch(name):
            raise ValueError(
                "A username is 1 to 64 letters, digits, dots, dashes, underscores or @, starting with a letter or "
                "digit (e.g. ann.lee)."
            )
        if role not in ROLES:
            raise ValueError(f"There is no role {role!r}: choose one of {', '.join(ROLE_LABELS.values())}.")
        shown = _clean_display(display_name) or name
        problem = password_problem(password, name, shown)
        if problem:
            raise ValueError(problem)
        hashed = hash_password(password)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
                raise ValueError(f"There is already an account {name}.")
            cur = conn.execute(
                "INSERT INTO users (username, display_name, role, pw_hash, active, must_change, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                (name, shown, role, hashed, int(must_change), _now()),
            )
            self._log(conn, "user_added", None, actor or None, {"username": name, "name": shown, "role": role})
            return int(cur.lastrowid)

    def user(self, user_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            return _public(conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (int(user_id),)).fetchone())

    def find_user(self, username: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE username = ?", (fold_username(username),))
            return _public(row.fetchone())

    def list_users(self) -> list[dict[str, Any]]:
        """Every account, Admins first, then by username."""
        order = " ".join(f"WHEN '{r}' THEN {n}" for n, r in enumerate(reversed(ROLES)))
        with self._conn() as conn:
            rows = conn.execute(f"SELECT {_USER_COLUMNS} FROM users ORDER BY CASE role {order} END, username")
            return [u for u in map(_public, rows.fetchall()) if u is not None]

    def user_count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    @staticmethod
    def _active_admins(conn: Any) -> int:
        return int(conn.execute("SELECT COUNT(*) FROM users WHERE role = ? AND active = 1", (ADMIN,)).fetchone()[0])

    def _account(self, conn: Any, user_id: int) -> Any:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if row is None:
            raise KeyError(user_id)
        return row

    def set_role(self, user_id: int, role: str, *, actor: str = "") -> None:
        """A new role; the last active Admin keeps theirs (ValueError ``LAST_ADMIN``)."""
        if role not in ROLES:
            raise ValueError(f"There is no role {role!r}: choose one of {', '.join(ROLE_LABELS.values())}.")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._account(conn, user_id)
            if row["role"] == role:
                return
            if row["role"] == ADMIN and row["active"] and self._active_admins(conn) <= 1:
                raise ValueError(LAST_ADMIN)
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, row["id"]))
            self._log(conn, "role_changed", None, actor or None,
                      {"username": row["username"], "from": row["role"], "to": role})  # fmt: skip

    def deactivate_user(self, user_id: int, *, actor: str = "") -> None:
        """The account can no longer log in, and its sessions end; the last active Admin stays (``LAST_ADMIN``)."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._account(conn, user_id)
            if not row["active"]:
                return
            if row["role"] == ADMIN and self._active_admins(conn) <= 1:
                raise ValueError(LAST_ADMIN)
            conn.execute("UPDATE users SET active = 0 WHERE id = ?", (row["id"],))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
            self._log(conn, "user_deactivated", None, actor or None, {"username": row["username"]})

    def activate_user(self, user_id: int, *, actor: str = "") -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._account(conn, user_id)
            if row["active"]:
                return
            conn.execute("UPDATE users SET active = 1 WHERE id = ?", (row["id"],))
            self._log(conn, "user_activated", None, actor or None, {"username": row["username"]})

    # --- Passwords --------------------------------------------------------------------------------------------------

    def change_password(self, user_id: int, current: str, new: str, *, keep: str | None = None) -> str:
        """The person's own change: ``current`` must match, ``new`` pass the policy and differ from it. Every session
        of theirs ends; the one they changed it from (``keep``, its token) goes on under a new token, returned ("" when
        none was kept), so a token seen before the change is worth nothing after it (D-101)."""
        with self._conn() as conn:
            row = self._account(conn, user_id)
        if not verify_password(current, row["pw_hash"]):
            raise ValueError("Your current password doesn't match. Type it again.")
        if new == current:
            raise ValueError("Choose a new password, different from the current one.")
        problem = password_problem(new, row["username"], row["display_name"])
        if problem:
            raise ValueError(problem)
        hashed = hash_password(new)
        fresh = ""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE users SET pw_hash = ?, must_change = 0 WHERE id = ?", (hashed, row["id"]))
            kept = conn.execute(
                "SELECT ip, user_agent FROM sessions WHERE user_id = ? AND token_hash = ?",
                (row["id"], token_hash(keep) if keep else ""),
            ).fetchone()
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
            if kept is not None:
                fresh, now = secrets.token_urlsafe(TOKEN_BYTES), _clock()
                conn.execute(
                    "INSERT INTO sessions (token_hash, user_id, created_at, last_seen, ip, user_agent) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (token_hash(fresh), row["id"], now, now, kept["ip"], kept["user_agent"]),
                )
            self._log(conn, "password_changed", None, row["display_name"] or row["username"],
                      {"username": row["username"]})  # fmt: skip
        return fresh

    def reset_password(self, user_id: int, *, actor: str = "", password: str | None = None) -> str:
        """A new password for someone who forgot theirs (``password``, else a temporary one made here, returned to
        be given to them once): they choose their own at the next login, every session of theirs ends, and what
        would refuse them is lifted: their username's pauses, and the pause of every address they logged in or tried
        from (a shared address paused by others' tries, D-101)."""
        with self._conn() as conn:
            row = self._account(conn, user_id)
        chosen = temporary_password() if password is None else password
        problem = password_problem(chosen, row["username"], row["display_name"])
        if problem:
            raise ValueError(problem)
        hashed = hash_password(chosen)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE users SET pw_hash = ?, must_change = 1 WHERE id = ?", (hashed, row["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
            theirs = [r[0] for r in conn.execute(
                "SELECT ip FROM login_attempts WHERE username = ? AND ip != '' UNION "
                "SELECT ip FROM sessions WHERE user_id = ? AND ip != ''", (row["username"], row["id"]))]  # fmt: skip
            for ip in theirs:
                self._lift_address(conn, ip)
            conn.execute("DELETE FROM login_attempts WHERE username = ? AND ok = 0", (row["username"],))
            self._log(conn, "password_reset", None, actor or None, {"username": row["username"]})
        return chosen

    # --- Paused addresses (D-101) ---------------------------------------------------------------------------------

    @staticmethod
    def _lift_address(conn: Any, ip: str) -> bool:
        """An address no longer paused, its count started again (its tries that didn't match forgotten: the events
        keep them). True when it was paused."""
        paused = conn.execute("SELECT MAX(ip_locked_until) FROM login_attempts WHERE ip = ?", (ip,)).fetchone()[0]
        conn.execute("DELETE FROM login_attempts WHERE ip = ? AND ok = 0", (ip,))
        return (paused or 0) > _clock()

    def paused_addresses(self) -> list[dict[str, Any]]:
        """The addresses logins are paused from now (30 tries that didn't match), with until when (epoch seconds)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ip, MAX(ip_locked_until) AS until FROM login_attempts WHERE ip_locked_until > ? GROUP BY ip "
                "ORDER BY ip",
                (_clock(),),
            ).fetchall()
        return [{"address": r["ip"], "until": float(r["until"])} for r in rows]

    def unlock_address(self, ip: str, *, actor: str = "") -> bool:
        """An Admin lifts the pause of an address (True when it was paused); recorded as ``address_unlocked``."""
        ip = str(ip or "").strip()[:64]
        if not ip:
            return False
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lifted = self._lift_address(conn, ip)
            if lifted:
                self._log(conn, "address_unlocked", None, actor or None, {"unpaused": ip})
        return lifted

    # --- Logging in -------------------------------------------------------------------------------------------------

    @staticmethod
    def _locked(conn: Any, key: str, ip: str, now: float, console: bool) -> bool:
        """Whether logins are paused for ``key`` (a username, or ``UNKNOWN`` and its hash) from ``ip``, or from
        ``ip`` whatever the username (never for the server's own console)."""
        found = conn.execute("SELECT MAX(user_locked_until) FROM login_attempts WHERE username = ? AND ip = ?",
                             (key, ip))  # fmt: skip
        if (found.fetchone()[0] or 0) > now:
            return True
        if ip and not console:
            found = conn.execute("SELECT MAX(ip_locked_until) FROM login_attempts WHERE ip = ?", (ip,))
            if (found.fetchone()[0] or 0) > now:
                return True
        return False

    def authenticate(self, username: str, password: str, ip: str = "", *, console: bool = False) -> dict[str, Any]:
        """The account whose username and password these are (its last login recorded); ``LoginRefused`` otherwise,
        with the same words whether the username exists or not (``WRONG``, or ``LOCKED`` while logins are paused).
        A try made while paused is not counted and its password not checked. ``console``: the server's own console
        (loopback, no proxy), never paused by address. The try is recorded, as one that did not match, before the
        password is checked, in the transaction that checks the pause: tries sent at once wait for each other there,
        so no more than ``MAX_FAILURES`` passwords are ever checked (D-101)."""
        name, ip, now = fold_username(username)[:200], str(ip or "")[:64], _clock()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
            key = name if row is not None else UNKNOWN + token_hash(name)[:32]  # the text typed is not kept
            if self._locked(conn, key, ip, now, console):
                raise LoginRefused(LOCKED)
            since = now - FAILURE_WINDOW
            last_ok = conn.execute("SELECT MAX(at) FROM login_attempts WHERE username = ? AND ip = ? AND ok = 1",
                                   (key, ip)).fetchone()[0]  # fmt: skip
            by_pair = (
                1
                + conn.execute(
                    "SELECT COUNT(*) FROM login_attempts WHERE username = ? AND ip = ? AND ok = 0 AND at > ?",
                    (key, ip, max(since, last_ok or 0)),
                ).fetchone()[0]
            )
            by_ip = (
                1
                + conn.execute(
                    "SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND ok = 0 AND at > ?", (ip, since)
                ).fetchone()[0]
            )
            lock_pair = by_pair >= MAX_FAILURES
            lock_ip = bool(ip) and not console and by_ip >= ADDRESS_FAILURES
            attempt = conn.execute(
                "INSERT INTO login_attempts (username, ip, at, ok, user_locked_until, ip_locked_until) "
                "VALUES (?, ?, ?, 0, ?, ?)",
                (key, ip, now, now + LOCK_SECONDS if lock_pair else None, now + LOCK_SECONDS if lock_ip else None),
            ).lastrowid
        matches = verify_password(password, row["pw_hash"] if row is not None else _dummy_hash())
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if row is not None and row["active"] and matches:
                conn.execute("UPDATE login_attempts SET ok = 1, user_locked_until = NULL, ip_locked_until = NULL "
                             "WHERE id = ?", (attempt,))  # fmt: skip
                conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), row["id"]))
                self._log(conn, "login", None, row["display_name"] or name, {"username": name})
                fresh = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (row["id"],)).fetchone()
                return _public(fresh) or {}
            who = {"username": name} if row is not None else {"username": "", "account": "unknown"}
            self._log(conn, "login_failed", None, None, who)
            if lock_pair or lock_ip:
                self._log(conn, "login_locked", None, None, {**who, "by": "username" if lock_pair else "address"})
        raise LoginRefused(LOCKED if lock_pair or lock_ip else WRONG)

    # --- Sessions ---------------------------------------------------------------------------------------------------

    def start_session(self, user_id: int, ip: str = "", user_agent: str = "") -> str:
        """A new session (a new token at every login, so one planted before it is worth nothing); the token, which
        only the browser keeps. Sessions past their time are cleared on the way."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = _clock()
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE created_at <= ? OR last_seen < ?",
                (now - ABSOLUTE_SECONDS, now - IDLE_SECONDS),
            )
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, last_seen, ip, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash(token), int(user_id), now, now, str(ip or "")[:64], str(user_agent or "")[:200]),
            )
        return token

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        """The active account a session token stands for, or None (no such session, unused for 8 hours, older than
        7 days, or its account turned off). Its last use is recorded (at most once a minute)."""
        if not token:
            return None
        digest, now = token_hash(token), _clock()
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT s.created_at AS session_created, s.last_seen, "
                f"{', '.join('u.' + c.strip() for c in _USER_COLUMNS.split(','))} "
                "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (digest,),
            ).fetchone()
            if row is None:
                return None
            if (
                not row["active"]
                or now - row["session_created"] >= ABSOLUTE_SECONDS
                or (now - row["last_seen"] > IDLE_SECONDS)
            ):
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
                return None
            if now - row["last_seen"] >= SEEN_EVERY:
                conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, digest))
        found = _public(row) or {}
        for key in ("session_created", "last_seen"):
            found.pop(key, None)
        return found

    def end_session(self, token: str | None, *, actor: str = "") -> bool:
        """Logout: the session is deleted (True when there was one); recorded as ``logout`` when ``actor`` is given."""
        if not token:
            return False
        with self._conn() as conn:
            row = conn.execute(
                "SELECT u.username FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (token_hash(token),),
            ).fetchone()
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
            if row is not None and actor:
                self._log(conn, "logout", None, actor, {"username": row["username"]})
        return row is not None

    def end_sessions(self, user_id: int, *, keep: str | None = None) -> int:
        """End every session of an account (``keep``: but this token's); how many ended."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (int(user_id), token_hash(keep) if keep else ""),
            )
            return cur.rowcount
