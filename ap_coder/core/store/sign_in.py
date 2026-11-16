"""The steps of a login before its session (D-146): a login half way, its password right and its code still to come;
and the first sign-in (D-243), admin / admin typed on the computer running AP Coder, the first Admin's own password
still to choose. Each is kept by its token's SHA-256 in the short-lived table for ``PENDING_SECONDS``, never a
session (``web/pages/login.py``). The store's clock is ``users._clock`` (the tests move it by hand). And what the
Admin panel shows of the sessions and the sign-ins, and its Sign out everywhere (D-249)."""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import secrets
from typing import Any

from . import users
from .dialect import begin_write, json_get
from .users import (
    _USER_COLUMNS,
    ADMIN,
    FIRST_ACTOR,
    FIRST_KIND,
    FIRST_USERNAME,
    PENDING_KIND,
    PENDING_SECONDS,
    SETUP_CODE_FILE,
    SETUP_CODE_UNREADABLE,
    TOKEN_BYTES,
    _clean_display,
    _public,
    token_hash,
)

# Sign-in on the laptop (D-243): the database's setting ``SIGN_IN``. ``LAPTOP``: its Admin signs in on this computer,
# set when a laptop's data folder first starts with no sign-in decided (ops/safety.py ``laptop_sign_in``); ``OFF``:
# turned off at the console (``users sign-in off``); ``TEAM``: the folder serves a team (server mode started on it).
# None: an older folder not started since, or a team's (D-211 tells it by its accounts). ops/safety.py reads it
# without a Store, by these same words.
SIGN_IN, LAPTOP, OFF, TEAM = "sign_in", "laptop", "off", "team"
LAPTOPS = (LAPTOP, OFF)  # the accounts are the laptop's own: kept across a restore, as the server's (D-101)
LAPTOP_IDLE_SECONDS = 8 * 3600  # a laptop's session unused this long ends (its owner's working day; the server: 30 min)


class SignInMixin:
    """A login half way and the first sign-in (D-146, D-243)."""

    # --- The first Admin, and the laptop's account's name (D-243) ---------------------------------------------------

    def add_first_admin(self, password: str, display_name: str) -> int:
        """The first account (D-243): ``FIRST_USERNAME``, an Admin, with the password its person chose at the first
        sign-in (their own: nothing to change at the next login; the authenticator app still to set up). ValueError
        when an account was made meanwhile (``FIRST_DONE``) or the policy refuses the password."""
        made = self.add_user(FIRST_USERNAME, password, role=ADMIN, display_name=display_name, actor=FIRST_ACTOR,
                             must_change=False, only_first=True)  # fmt: skip
        (self.path.parent / SETUP_CODE_FILE).unlink(missing_ok=True)  # the server's setup code: used
        return made

    def rename_user(self, user_id: int, display_name: str) -> None:
        """The name an account shows (a laptop's Admin renamed in Settings, D-243: the name on their approvals)."""
        shown = _clean_display(display_name)
        if shown:
            with self._conn() as conn:
                conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (shown, int(user_id)))

    # --- A login half way: the password right, the code still to come (D-146) ----------------------------------------

    def start_pending(self, user_id: int) -> str:
        """A login half way for ``user_id``: its token, which only the browser keeps (the database its SHA-256), for
        ``PENDING_SECONDS``. Not a session: it opens only the next steps (``web/pages/login.py``)."""
        token, now = secrets.token_urlsafe(TOKEN_BYTES), users._clock()
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE kind = ? AND expires_at < ?", (PENDING_KIND, now))
            conn.execute("INSERT INTO ephemeral (kind, token, value, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                         (PENDING_KIND, token_hash(token), str(int(user_id)).encode("ascii"), now,
                          now + PENDING_SECONDS))  # fmt: skip
        return token

    def pending_user(self, token: str | None) -> dict[str, Any] | None:
        """The active account a login half way stands for; None when there is none, or past its time."""
        if not token:
            return None
        with self._conn() as conn:
            row = conn.execute("SELECT value, expires_at FROM ephemeral WHERE kind = ? AND token = ?",
                               (PENDING_KIND, token_hash(token))).fetchone()  # fmt: skip
            if row is None or row["expires_at"] < users._clock():
                return None
            found = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = ? AND active = 1",
                                 (int(bytes(row["value"])),)).fetchone()  # fmt: skip
        return _public(found)

    def end_pending(self, token: str | None) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE kind = ? AND token = ?", (PENDING_KIND, token_hash(token or "")))

    @staticmethod
    def _end_pending(conn: Any, user_id: int) -> None:
        """Every login half way of ``user_id`` ends (its password reset, its authenticator reset)."""
        conn.execute("DELETE FROM ephemeral WHERE kind = ? AND CAST(value AS TEXT) = ?", (PENDING_KIND, str(user_id)))

    # --- The first sign-in (D-243): admin / admin typed, its own password still to choose ----------------------------

    def start_first_sign_in(self) -> str:
        """A first sign-in under way: its token, which only the browser keeps (the database its SHA-256), for
        ``PENDING_SECONDS``. It opens only the page where the first account's password is chosen."""
        token, now = secrets.token_urlsafe(TOKEN_BYTES), users._clock()
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE kind = ? AND expires_at < ?", (FIRST_KIND, now))
            conn.execute("INSERT INTO ephemeral (kind, token, value, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                         (FIRST_KIND, token_hash(token), b"1", now, now + PENDING_SECONDS))  # fmt: skip
        return token

    def first_sign_in_open(self, token: str | None) -> bool:
        """Whether ``token`` is a first sign-in under way, not past its time."""
        if not token:
            return False
        with self._conn() as conn:
            row = conn.execute("SELECT expires_at FROM ephemeral WHERE kind = ? AND token = ?",
                               (FIRST_KIND, token_hash(token))).fetchone()  # fmt: skip
        return row is not None and row["expires_at"] >= users._clock()

    def first_setup_code(self) -> str:
        """The server's setup code for the first sign-in (``users.SETUP_CODE_FILE``): a forwarder on the server
        (netsh portproxy, ssh -L, socat, a proxy that adds nothing) brings any browser in from 127.0.0.1, so no
        address proves who is at the server. While there is no account, the one in the data folder, made now when
        there is none (0600; on Windows the folder's permissions; two web workers making it at once keep the first),
        said in the server's window and by the doctor. Once there is an account, "" and the file removed."""
        path = self.path.parent / SETUP_CODE_FILE
        if self.user_count():
            path.unlink(missing_ok=True)
            return ""
        if not path.exists():
            part = path.with_name(f".{SETUP_CODE_FILE}.{secrets.token_hex(4)}.part")
            handle = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(handle, "w", encoding="ascii") as out:
                    out.write(users.temporary_password() + "\n")
                with contextlib.suppress(FileExistsError):
                    os.link(part, path)  # never over one there
            finally:
                part.unlink(missing_ok=True)
        try:
            return path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            return ""

    def setup_code_problem(self) -> str:
        """Why the server's setup code can't be used though there is no account ("" when it can): its file is there
        but this account can't read it (``users.SETUP_CODE_UNREADABLE``)."""
        path = self.path.parent / SETUP_CODE_FILE
        if self.user_count() or not path.exists():
            return ""
        try:
            path.read_text(encoding="ascii")
        except (OSError, UnicodeDecodeError):
            return SETUP_CODE_UNREADABLE.format(path=path)
        return ""

    def setup_code_matches(self, typed: str) -> bool:
        """Whether ``typed`` is the server's setup code (case, spaces and dashes aside: as read off a screen)."""
        kept = self.first_setup_code()

        def plain(text: str) -> bytes:
            return "".join(c for c in str(text or "").lower() if c.isalnum()).encode("ascii", "replace")

        return bool(kept) and hmac.compare_digest(plain(typed), plain(kept))

    def end_first_sign_in(self, token: str | None) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE kind = ? AND token = ?", (FIRST_KIND, token_hash(token or "")))

    # --- What the Admin panel shows (D-249) --------------------------------------------------------------------------

    def sign_out_everywhere(self, user_id: int, *, actor: str = "") -> int:
        """An Admin ends every session of someone else's account (a computer left signed in, D-249): how many ended,
        recorded as ``sessions_ended``. Their password and their authenticator app stay as they are."""
        with self._conn() as conn:
            begin_write(conn)
            row = self._account(conn, user_id)
            ended = conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],)).rowcount
            self._end_pending(conn, row["id"])
            self._log(conn, "sessions_ended", None, actor or None, {"username": row["username"], "count": ended})
        return int(ended)

    def live_sessions(self, user_id: int | None = None) -> list[dict[str, Any]]:
        """The sessions within their time (D-249), the most recently used first: whose (``user_id``, ``username``,
        ``name``), since and last used (epoch seconds), and the address and browser it came from. Never the token,
        nor its hash. ``user_id``: one account's only."""
        now = users._clock()
        where, args = "s.created_at > ? AND s.last_seen >= ?", [now - self.session_absolute, now - self.session_idle]
        if user_id is not None:
            where += " AND s.user_id = ?"
            args.append(int(user_id))
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT s.user_id, u.username, u.display_name, s.created_at, s.last_seen, s.ip, s.user_agent FROM "
                f"sessions s JOIN users u ON u.id = s.user_id WHERE {where} ORDER BY s.last_seen DESC, u.username",
                args,
            ).fetchall()
        return [{"user_id": int(r["user_id"]), "username": r["username"], "name": r["display_name"] or r["username"],
                 "since": float(r["created_at"]), "last_seen": float(r["last_seen"]), "address": r["ip"],
                 "browser": r["user_agent"]} for r in rows]  # fmt: skip

    def sign_in_events(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        """An account's latest logins, logouts, tries that didn't match and pauses (D-249), newest first, as
        ``events`` gives them: read off the audit trail, which keeps them after a reset clears ``login_attempts``."""
        actions = ("login", "login_failed", "login_locked", "logout", "password_changed", "password_reset",
                   "authenticator_enrolled", "authenticator_reset", "sessions_ended")  # fmt: skip
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(
                f"SELECT * FROM events WHERE action IN ({', '.join('?' for _ in actions)}) AND "
                f"{json_get('detail', '$.username')} = ? ORDER BY id DESC LIMIT ?",
                (*actions, users.fold_username(username), int(limit)))]  # fmt: skip
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else {}
        return rows
