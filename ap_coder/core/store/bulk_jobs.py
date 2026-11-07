"""Bulk approvals as jobs (D-185): the clean invoices a person asked to approve in one go, approved one at a time by a
background thread (the company server's background process, the laptop's own: ``web/jobs.py`` ``BulkRunner``) while
the person's page shows how far it got.

* A person has one open (waiting or being approved) at a time: asked again meanwhile, it is the same one.
* Each invoice's outcome is kept in the same transaction as its approval (``bulk_step``), while the job is still
  that runner's: after a restart the job goes on from the next invoice, and one approved before is never approved
  again nor counted twice.
* A job left by a process that stopped is taken again, ``TRIES`` times in all, then ends with why and what was done;
  one of another database (before a restore) is no one's, and goes at the next job asked.
* A job ended is kept ``KEEP`` seconds, the latest ``KEPT_EACH`` of each person, so its page shows what was done.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from ._base import _in_list
from .invoices import REVISION_ACTIONS

WAITING, RUNNING, DONE, STOPPED = "waiting", "running", "done", "stopped"
_OPEN = f"('{WAITING}', '{RUNNING}')"
TRIES = 3  # a job whose process stopped while approving is taken again, this many times in all
KEEP = 86400.0  # seconds a job ended is kept
KEPT_EACH = 5  # jobs ended kept of each person, the latest
GONE = "no longer in the queue (someone else acted on it meanwhile)"
EDITED = "it has edits open on its review screen"
CHANGED = "changed meanwhile: it is no longer the invoice the queue showed"
# What moves an invoice's stamp (``bulk_stamps``): its revision (``REVISION_ACTIONS``, D-184), and a new reading
# of it (``replace_proposal``). One whose stamp moved since the queue listed it is no longer the invoice asked.
STAMP_ACTIONS = (*REVISION_ACTIONS, "proposal_updated")
_STAMP_IN = "(" + ", ".join("?" * len(STAMP_ACTIONS)) + ")"


def stopped_twice(tries: int) -> str:
    return (f"AP Coder stopped {tries} times while approving these invoices, so it stopped trying. The ones still in "
            "the queue need a person: approve them from the queue again.")  # fmt: skip


UPDATED = ("AP Coder was updated while these invoices waited to be approved. The ones still in the queue need a "
           "person: approve them from the queue again.")  # fmt: skip


def _decoded(row: Any) -> dict[str, Any]:
    job = dict(row)
    for key in ("person", "ids", "approved", "skipped"):
        job[key] = json.loads(job[key])
    return job


class BulkJobsMixin:
    def start_bulk_job(self, owner: str, person: dict[str, Any], ids: list[list[Any]], version: str
                       ) -> tuple[int, bool]:  # fmt: skip
        """(the job, whether it is new) approving ``ids`` (each ``[id, its row_identity, its stamp]``: ``bulk_stamps``)
        for ``owner`` as ``person`` (who each approval names): theirs already open when they have one (asked again, or
        from another web worker at once), else a new one waiting. The jobs ended past ``KEEP`` and ``KEPT_EACH``, and
        those of another database, go now."""
        now = time.time()
        with self._conn() as conn:  # type: ignore[attr-defined]
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")  # one asker at a time: never two jobs of the same person
            db = self._database_id(conn)  # type: ignore[attr-defined]
            row = conn.execute(f"SELECT id FROM bulk_jobs WHERE owner = ? AND db = ? AND state IN {_OPEN}",
                               (owner, db)).fetchone()  # fmt: skip
            if row is not None:
                return int(row["id"]), False
            conn.execute(
                f"DELETE FROM bulk_jobs WHERE db != ? OR (state NOT IN {_OPEN} AND (finished_at < ? OR (owner = ? AND "
                f"id NOT IN (SELECT id FROM bulk_jobs WHERE owner = ? AND state NOT IN {_OPEN} ORDER BY id DESC "
                "LIMIT ?))))",
                (db, now - KEEP, owner, owner, KEPT_EACH - 1),
            )
            made = conn.execute(
                "INSERT INTO bulk_jobs (owner, person, ids, state, db, version, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?)",
                (owner, json.dumps(person), json.dumps(ids), WAITING, db, version, now),
            )
            return int(made.lastrowid), True

    def bulk_job(self, job_id: int) -> dict[str, Any] | None:
        """Job ``job_id`` (its lists decoded); None when there is none on this database."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT * FROM bulk_jobs WHERE id = ? AND db = ?",
                               (job_id, self._database_id(conn))).fetchone()  # type: ignore[attr-defined]  # fmt: skip
        return None if row is None else _decoded(row)

    def latest_bulk_job(self, owner: str) -> dict[str, Any] | None:
        """``owner``'s job open, else their latest ended; None when they have none on this database."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                f"SELECT * FROM bulk_jobs WHERE owner = ? AND db = ? ORDER BY state IN {_OPEN} DESC, id DESC LIMIT 1",
                (owner, self._database_id(conn)),  # type: ignore[attr-defined]
            ).fetchone()
        return None if row is None else _decoded(row)

    def claim_bulk_job(self, runner: str, stale: float, version: str) -> dict[str, Any] | None:
        """The oldest job waiting, or being approved by a process that stopped noting its progress ``stale`` seconds
        ago (taken again, ``TRIES`` times in all), now ``runner``'s; None when there is none. One of another format
        (``version``: an update meanwhile), or tried ``TRIES`` times, ends with why, what was done kept."""
        now = time.time()
        with self._conn() as conn:  # type: ignore[attr-defined]
            db = self._database_id(conn)  # type: ignore[attr-defined]
            # (looked at twice a second while none waits: never the write lock for nothing, nor for another database's)
            if conn.execute("SELECT 1 FROM bulk_jobs WHERE db = ? AND (state = ? OR (state = ? AND renewed_at < ?)) "
                            "LIMIT 1", (db, WAITING, RUNNING, now - stale)).fetchone() is None:  # fmt: skip
                return None
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            while True:
                row = conn.execute(
                    "SELECT * FROM bulk_jobs WHERE db = ? AND (state = ? OR (state = ? AND renewed_at < ?)) ORDER BY "
                    "id LIMIT 1",
                    (db, WAITING, RUNNING, now - stale),
                ).fetchone()
                if row is None:
                    return None
                why = UPDATED if row["version"] != version else stopped_twice(row["tries"]) if row["tries"] >= TRIES \
                    else ""  # fmt: skip
                if why:
                    conn.execute("UPDATE bulk_jobs SET state = ?, error = ?, finished_at = ? WHERE id = ?",
                                 (STOPPED, why, now, row["id"]))  # fmt: skip
                    continue
                conn.execute("UPDATE bulk_jobs SET state = ?, runner = ?, tries = tries + 1, renewed_at = ? WHERE "
                             "id = ?", (RUNNING, runner, now, row["id"]))  # fmt: skip
                return _decoded(row) | {"state": RUNNING, "runner": runner, "tries": row["tries"] + 1}

    def renew_bulk_job(self, job_id: int, runner: str) -> bool:
        """That ``runner`` is still at the job; False when it is no longer its own (taken again, or gone)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            changed = conn.execute("UPDATE bulk_jobs SET renewed_at = ? WHERE id = ? AND runner = ? AND state = ?",
                                   (time.time(), job_id, runner, RUNNING))  # fmt: skip
            return changed.rowcount == 1

    def bulk_stamps(self, invoice_ids: list[int]) -> dict[int, int]:
        """{invoice: its stamp} for each of ``invoice_ids``, in one query: the id of its last event that moved it in or
        out of the queue, stored its coding or read it again (``STAMP_ACTIONS``; 0 when none)."""
        ids = list(dict.fromkeys(int(i) for i in invoice_ids))
        with self._conn() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                f"SELECT invoice_id, MAX(id) FROM events WHERE {_in_list('invoice_id')} AND action IN {_STAMP_IN} "
                "GROUP BY invoice_id",
                (json.dumps(ids), *STAMP_ACTIONS),
            ).fetchall()
        return dict.fromkeys(ids, 0) | {int(r[0]): int(r[1]) for r in rows}

    def bulk_step(self, job_id: int, runner: str, invoice_id: int, why: str, approve: Callable[[], Any] | None = None,
                  expect: tuple[Any, int] | None = None) -> str | None:  # fmt: skip
        """One invoice of the job done: ``approve()`` (the approval, in this same transaction) when given, and its
        outcome kept with it: "approved", or "skipped" with ``why`` (``EDITED`` when someone has edits of it kept on
        its review screen: they are approved there; ``CHANGED`` when it is no longer ``expect``, its row identity and
        stamp as the queue listed it; ``GONE`` when the approval found it no longer in the queue). None, and nothing
        done, when the job is no longer ``runner``'s, or has this invoice already."""
        with self.session(), self._conn() as conn:  # type: ignore[attr-defined]
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")  # the job's own, and the approval with it: one at a time
            row = conn.execute("SELECT approved, skipped FROM bulk_jobs WHERE id = ? AND runner = ? AND state = ?",
                               (job_id, runner, RUNNING)).fetchone()  # fmt: skip
            approved, skipped = (json.loads(row["approved"]), json.loads(row["skipped"])) if row else ([], [])
            if row is None or invoice_id in approved or invoice_id in [s[0] for s in skipped]:
                return None
            outcome = "skipped"
            if conn.execute("SELECT 1 FROM invoice_drafts d JOIN invoices i ON i.id = d.invoice_id WHERE "
                            "d.invoice_id = ? AND i.status = 'review' LIMIT 1", (invoice_id,)).fetchone():  # fmt: skip
                approve, why = None, EDITED  # (a draft kept before this moment; one after finds it approved)
            if approve is not None and expect is not None and self._bulk_moved(conn, invoice_id, expect):
                approve, why = None, CHANGED  # (its coding checked before this moment is not the one stored now)
            if approve is not None:
                conn.execute("SAVEPOINT bulk_one")
                try:
                    approve()
                except (KeyError, ValueError):  # deleted, or approved, parked or rejected meanwhile
                    conn.execute("ROLLBACK TO bulk_one")
                    why = GONE
                else:
                    outcome = "approved"
                conn.execute("RELEASE bulk_one")
            if outcome == "approved":
                approved.append(invoice_id)
            else:
                skipped.append([invoice_id, why])
            conn.execute("UPDATE bulk_jobs SET approved = ?, skipped = ?, renewed_at = ? WHERE id = ?",
                         (json.dumps(approved), json.dumps(skipped), time.time(), job_id))  # fmt: skip
            return outcome

    @staticmethod
    def _bulk_moved(conn: Any, invoice_id: int, expect: tuple[Any, int]) -> bool:
        """Invoice ``invoice_id`` (still there) is no longer ``expect``: (its row identity, its stamp)."""
        row = conn.execute("SELECT file_sha256, created_at FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        stamp = conn.execute(f"SELECT MAX(id) FROM events WHERE invoice_id = ? AND action IN {_STAMP_IN}",
                             (invoice_id, *STAMP_ACTIONS)).fetchone()[0]  # fmt: skip
        return row is not None and ([row[0], row[1]] != list(expect[0]) or int(stamp or 0) != expect[1])

    def end_bulk_job(self, job_id: int, runner: str, error: str = "") -> None:
        """The job ended: done, or stopped with ``error`` (why), while it is ``runner``'s."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute(
                "UPDATE bulk_jobs SET state = ?, error = ?, finished_at = ? WHERE id = ? AND runner = ? AND state = ?",
                (STOPPED if error else DONE, error, time.time(), job_id, runner, RUNNING),
            )

    def release_bulk_job(self, job_id: int, runner: str) -> None:
        """The job put back in line as it is (its process stopping): the next start goes on with it."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute("UPDATE bulk_jobs SET state = ?, runner = '', tries = MAX(tries - 1, 0) WHERE id = ? AND "
                         "runner = ? AND state = ?", (WAITING, job_id, runner, RUNNING))  # fmt: skip

    def release_left_bulk_jobs(self) -> int:
        """Every job being approved put back in line, at the start of the one process that approves them (the laptop,
        the server's background process: one at a time, D-111): its process stopped. How many."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            return conn.execute("UPDATE bulk_jobs SET state = ?, runner = '' WHERE state = ?",
                                (WAITING, RUNNING)).rowcount  # fmt: skip

    def saw_bulk_job(self, job_id: int) -> None:
        """The job's end was shown to its owner: the queue no longer says it."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute(f"UPDATE bulk_jobs SET seen = 1 WHERE id = ? AND state NOT IN {_OPEN}", (job_id,))
