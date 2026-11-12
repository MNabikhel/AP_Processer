"""The steps of a login before its session (D-146): a login half way, its password right and its code still to come;
and the first sign-in (D-243), admin / admin typed on the computer running AP Coder, the first Admin's own password
still to choose. Each is kept by its token's SHA-256 in the short-lived table for ``PENDING_SECONDS``, never a
session (``web/pages/login.py``). The store's clock is ``users._clock`` (the tests move it by hand)."""

from __future__ import annotations

import contextlib
import hmac
import os
import secrets
from typing import Any

from . import users
from .users import (
    _USER_COLUMNS,
    ADMIN,
    FIRST_ACTOR,
    FIRST_KIND,
    FIRST_USERNAME,
    PENDING_KIND,
    PENDING_SECONDS,
    SETUP_CODE_FILE,
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
        """The server's setup code for the first sign-in (``users.SETUP_CODE_FILE``): while there is no account, the
        one in the data folder, made now when there is none (readable by this account only; two web workers making it
        at once keep the first). Once there is an account, "" and the file removed."""
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

    def setup_code_matches(self, typed: str) -> bool:
        """Whether ``typed`` is the server's setup code (case, spaces and dashes aside: as read off a screen)."""
        kept = self.first_setup_code()

        def plain(text: str) -> bytes:
            return "".join(c for c in str(text or "").lower() if c.isalnum()).encode("ascii", "replace")

        return bool(kept) and hmac.compare_digest(plain(typed), plain(kept))

    def end_first_sign_in(self, token: str | None) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE kind = ? AND token = ?", (FIRST_KIND, token_hash(token or "")))
