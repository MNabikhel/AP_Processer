"""What the dashboard keeps for a short while: a message waiting for the page an action leads to, a spreadsheet
waiting for its columns to be picked (D-99). In the database, so a restart of the server (an update, the service
restarted) does not lose them; each has an expiry and is gone after it, or once it is used."""

from __future__ import annotations

import secrets
import time

_COLUMNS = "(kind, token, value, created_at, expires_at)"


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

    # --- A dashboard job seen by every web worker of the company server (D-111) -------------------------------------
    # One row per job name: "job-running:<owner>" while a worker runs it (renewed every second, gone after ``stale``
    # seconds without renewal: that worker stopped), then "job" with its last snapshot (kept ``keep`` seconds).
    # "job-waiting:<owner>" is how many files a worker holds queued behind it.

    def claim_job(self, name: str, owner: str, snapshot: bytes, stale: float) -> bool:
        """Mark job ``name`` as run by ``owner``; False (nothing changed) while another worker runs it."""
        now = time.time()
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE token = ? AND (kind = 'job' OR (kind LIKE 'job-running:%' AND "
                         "expires_at < ?))", (name, now))  # fmt: skip
            done = conn.execute(
                "INSERT INTO ephemeral (kind, token, value, created_at, expires_at) SELECT ?, ?, ?, ?, ? WHERE NOT "
                "EXISTS (SELECT 1 FROM ephemeral WHERE token = ? AND kind LIKE 'job-running:%')",
                (f"job-running:{owner}", name, snapshot, now, now + stale, name),
            )
            return done.rowcount == 1

    def renew_job(self, name: str, owner: str, snapshot: bytes, stale: float) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute("UPDATE ephemeral SET value = ?, expires_at = ? WHERE kind = ? AND token = ?",
                         (snapshot, now + stale, f"job-running:{owner}", name))  # fmt: skip

    def finish_job(self, name: str, owner: str, snapshot: bytes, keep: float) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE token = ? AND kind IN (?, 'job')", (name, f"job-running:{owner}"))
            conn.execute(f"INSERT INTO ephemeral {_COLUMNS} VALUES ('job', ?, ?, ?, ?)",
                         (name, snapshot, now, now + keep))  # fmt: skip

    def job_row(self, name: str) -> tuple[str, bytes] | None:
        """(owner, snapshot) of the worker running job ``name``, ("", snapshot) once it finished; None: never run."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT kind, value FROM ephemeral WHERE token = ? AND expires_at >= ? AND (kind = 'job' OR kind LIKE "
                "'job-running:%') ORDER BY kind DESC",
                (name, time.time()),
            ).fetchall()
        if not rows:
            return None
        kind, value = rows[0]["kind"], bytes(rows[0]["value"])
        return kind.partition(":")[2], value

    def set_job_waiting(self, name: str, owner: str, files: int, stale: float) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE kind = ? AND token = ?", (f"job-waiting:{owner}", name))
            if files:
                conn.execute(f"INSERT INTO ephemeral {_COLUMNS} VALUES (?, ?, ?, ?, ?)",
                             (f"job-waiting:{owner}", name, str(files).encode(), now, now + stale))  # fmt: skip

    def jobs_waiting(self, name: str) -> int:
        with self._conn() as conn:
            rows = conn.execute("SELECT value FROM ephemeral WHERE token = ? AND kind LIKE 'job-waiting:%' AND "
                                "expires_at >= ?", (name, time.time())).fetchall()  # fmt: skip
        return sum(int(bytes(r["value"]) or b"0") for r in rows)

    # --- What each account uploaded in the last hour, on the server (D-109, kept here since D-111) -------------------

    def take_upload_allowance(self, account: str, size: int, window: float, most: int, most_bytes: int) -> bool:
        """Count an upload of ``size`` bytes for ``account`` when it stays within ``most`` uploads and ``most_bytes``
        in ``window`` seconds (True), else nothing is counted (False). One transaction: every web worker counts the
        same uploads."""
        now = time.time()
        kind, token = f"upload-allowance:{account}", secrets.token_urlsafe(8)
        with self._conn() as conn:
            conn.execute("DELETE FROM ephemeral WHERE kind = ? AND expires_at < ?", (kind, now))
            conn.execute("INSERT INTO ephemeral (kind, token, value, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                         (kind, token, str(size).encode(), now, now + window))  # fmt: skip
            count, total = conn.execute("SELECT COUNT(*), SUM(CAST(CAST(value AS TEXT) AS INTEGER)) FROM ephemeral "
                                        "WHERE kind = ?", (kind,)).fetchone()  # fmt: skip
            if count > most or (total or 0) > most_bytes:
                conn.execute("DELETE FROM ephemeral WHERE kind = ? AND token = ?", (kind, token))
                return False
        return True
