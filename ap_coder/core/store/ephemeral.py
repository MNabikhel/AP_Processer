"""What the dashboard keeps for a short while: a message waiting for the page an action leads to, a spreadsheet
waiting for its columns to be picked (D-99). In the database, so a restart of the server (an update, the service
restarted) does not lose them; each has an expiry and is gone after it, or once it is used."""

from __future__ import annotations

import secrets
import time


class EphemeralMixin:
    # --- Kept for a short while ---------------------------------------------------------------------------------

    def keep_ephemeral(self, kind: str, value: bytes, ttl: float, keep: int | None = None) -> str:
        """Keep ``value`` under a new one-off token for ``ttl`` seconds; at most ``keep`` of this ``kind`` (the oldest
        go first). Expired ones of every kind are removed now."""
        token = secrets.token_urlsafe(12)
        now = time.time()
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE expires_at < ?", (now,))
            if keep is not None:
                conn.execute(
                    "DELETE FROM ephemeral WHERE kind = ? AND token NOT IN (SELECT token FROM ephemeral WHERE kind = ? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?)",
                    (kind, kind, max(keep - 1, 0)),
                )
            conn.execute("INSERT INTO ephemeral (kind, token, value, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                         (kind, token, value, now, now + ttl))  # fmt: skip
        return token

    def ephemeral(self, kind: str, token: str | None, *, take: bool = False) -> bytes | None:
        """What is kept under ``token`` (None when there is nothing, or it expired); ``take``: forgotten once read."""
        if not token:
            return None
        with self._conn() as conn:
            row = conn.execute("SELECT value, expires_at FROM ephemeral WHERE kind = ? AND token = ?",
                               (kind, token)).fetchone()  # fmt: skip
            if row is not None and (take or row["expires_at"] < time.time()):
                conn.execute("DELETE FROM ephemeral WHERE kind = ? AND token = ?", (kind, token))
        if row is None or row["expires_at"] < time.time():
            return None
        return bytes(row["value"])

    def drop_ephemeral(self, kind: str, token: str | None) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE kind = ? AND token = ?", (kind, token or ""))
