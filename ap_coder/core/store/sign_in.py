"""The steps of a login before its session (D-146): a login half way, its password right and its code still to come;
and the first sign-in (D-243), admin / admin typed on the computer running AP Coder, the first Admin's own password
still to choose. Each is kept by its token's SHA-256 in the short-lived table for ``PENDING_SECONDS``, never a
session (``web/pages/login.py``). The store's clock is ``users._clock`` (the tests move it by hand)."""

from __future__ import annotations

import secrets
from typing import Any

from . import users
from .users import _USER_COLUMNS, FIRST_KIND, PENDING_KIND, PENDING_SECONDS, TOKEN_BYTES, _public, token_hash


class SignInMixin:
    """A login half way and the first sign-in (D-146, D-243)."""

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

    def end_first_sign_in(self, token: str | None) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE kind = ? AND token = ?", (FIRST_KIND, token_hash(token or "")))
