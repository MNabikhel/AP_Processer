"""What the company server's processes share of the big reports and downloads (D-169).

* **An answer worked out once for every process** (``report_answers``): a report's big answer (the duplicate payment
  audit's pairs, month-end's received purchase orders, Spend's figures, the recurring vendors, Insights' figures:
  D-169, D-175) kept as JSON with the stamp it was worked out under, the stamp
  read from the database before the work, as ``summary_memo`` reads its own (D-159): the database's ``summary_id``
  (another after a restore), the summary's generation, the counts of changes the triggers keep, and what the answer is
  for (``asked``). A process that has nothing kept in its memory takes it from here instead of working it out again
  (the audit took 11 s at 100,000 invoices, month-end 6 s, in each web worker). Only JSON, never pickle (what is read
  back is data, never code); only an answer of this AP Coder's version and format (``version``), so one an older
  version wrote is worked out again; at most ``ANSWER_MOST_BYTES``; never one worked out in a write under way, which
  may yet be undone. What a process asks again it keeps in its memory, as ``summary_memo`` does.
* **Downloads made by the background process** (``report_jobs``, ``web/pages/downloads.py``): on the company server a
  long download (search to Excel, the Activity CSV, the duplicate audit) is not made by the web worker the person
  asked: it waits here, the background process makes it (``claim_download``, ``renew_download``), and the person's page
  follows it and downloads the file once made. Each job is its owner's alone, the same one asked again (from two
  workers at once) is one job, a person has at most a few waiting at once, and a file made is kept for a while.
  A job left by a background process that stopped is taken again; one of another database (a restore) is no one's.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from typing import Any

ANSWER_MOST_BYTES = 16 * 1024 * 1024  # an answer bigger than this as JSON is not kept for the other processes
ANSWERS_KEPT = 8  # of each name (a report asked for a few period ends), the latest made kept
ANSWER_LEASE = 10.0  # seconds a process working an answer out holds it for the others, renewed while it works: one
ANSWER_RENEW = 3.0  # that stopped (killed, B-1807) is passed over within ANSWER_LEASE; renewed every ANSWER_RENEW
ANSWER_WAIT = 30.0  # seconds another process asking the same waits for that answer before working it out itself
ANSWER_WAIT_MOST = 180.0  # ... and while the one working it out still renews its hold, at most this long (B-1914)
ANSWER_POLL = 0.25
OWNER = f"{os.getpid()}:{secrets.token_hex(4)}"  # this process, as the answers' leases name it

WAITING, RUNNING, MADE, FAILED = "waiting", "running", "done", "failed"
_OPEN = f"('{WAITING}', '{RUNNING}')"
TRIES = 2  # a job whose background process stopped while making it is taken again, this many times in all
STOPPED_TWICE = "AP Coder stopped twice while making this file. Ask for it again; if it happens again, tell IT."
UPDATED = "AP Coder was updated while this file waited to be made. Ask for it again."

_log = logging.getLogger(__name__)


def _version(version: str) -> str:
    """An answer's or a job's version: AP Coder's and the caller's own format of it."""
    from ... import __version__

    return f"{__version__}/{version}"


def asked_key(asked: Any) -> str:
    """What an answer is for, as its row is found by: a digest of its ``repr`` (the same in every process)."""
    return hashlib.blake2b(repr(asked).encode("utf-8"), digest_size=16).hexdigest()


def _later(kept: list[Any], seen: list[Any]) -> bool:
    """Whether stamp ``kept`` is ``seen`` or a later one of the same database: each count the same or more (they only
    go up; a restore gives another ``summary_id``)."""
    if len(kept) != len(seen) or kept[:1] != seen[:1]:
        return False
    try:
        return all(int(k or 0) >= int(s or 0) for k, s in zip(kept[1:], seen[1:], strict=True))
    except (TypeError, ValueError):
        return False


# What this process keeps of the shared answers, before reading them from the database again: (database, name, what
# for) -> (the stamp, the value, when its work began: ``time.time``), the latest used last, KEPT_HERE at most.
_KEPT: dict[tuple[str, str, str], tuple[list[Any], Any, float]] = {}
_KEPT_LOCK = threading.Lock()
wall = time.time  # an answer's age, the same clock in every process (tests set it)
_WORKING: dict[tuple[str, str, str], threading.Lock] = {}  # one thread of this process works an answer out at a time
KEPT_HERE = 32


class ReportsMixin:
    # --- An answer worked out once for every process ---------------------------------------------------------------

    def shared_answer(self, name: str, compute: Callable[[], Any], *, version: str,
                      encode: Callable[[Any], Any] = lambda v: v, decode: Callable[[Any], Any] = lambda v: v,
                      topic: str = "all", changes: tuple[str, ...] = (), asked: Any = None,
                      settle: float = 0.0) -> Any:  # fmt: skip
        """``compute()``, worked out once for every process of this database (D-169), and kept in this one: an answer
        holds while the stamp read now, before any work (the database's ``summary_id``; with ``topic``, the summary's
        generation of it, ``""``: none; the counts of ``changes``, ``changes/<kind>``), is the one it was worked out
        under, for the same ``asked`` (compared by ``asked_key``), and was written by this ``version`` (the caller's
        format: another, or another AP Coder's, is worked out again). One kept by another process under a later stamp
        holds too: its work began after this ask did (it was being worked out while this one waited, B-1802).
        ``settle`` (an answer a page only shows, D-164): once what it depends on changed, one whose work began less
        than that many seconds ago on the same database is taken still, in whichever process it was made, so it is
        never older than that but by the time another takes to work it out: while another thread or process works it
        out, the answer before (this database's latest) is taken meanwhile (B-1834); with no such answer, or without
        ``settle``, it is waited for (``ANSWER_WAIT``, and while its hold is renewed up to ``ANSWER_WAIT_MOST``, B-1914). ``encode``/``decode``: the value to JSON-ready data and back.
        In a write under way (or a read transaction) it is worked out afresh, neither read nor kept. The value is
        shared: read it, don't change it."""
        key, owner = asked_key(asked), OWNER  # (the process's own name for its holds, the same to the end)
        here = (str(self.path), name, key)  # type: ignore[attr-defined]
        began = wall()
        seen, writing = self._answer_stamp(topic, changes)
        if writing:
            return compute()

        def holds(found: tuple[Any, Any, float] | None, later: bool = False) -> bool:
            if found is None:
                return False
            kept, _, made = found
            if kept == seen or (later and _later(kept, seen)):
                return True
            return bool(settle and kept[:1] == seen[:1] and 0 <= began - made < settle)

        def before(found: tuple[Any, Any, float] | None) -> bool:
            """Whether ``found`` may be shown while another works the answer out again: settled, of this database, its
            work begun less than the settle and the longest wait ago (B-1838: never older than that)."""
            return bool(settle and found is not None and found[0][:1] == seen[:1]
                        and 0 <= began - found[2] < settle + ANSWER_WAIT)  # fmt: skip

        with _KEPT_LOCK:
            hit = _KEPT.get(here)
            working = _WORKING.setdefault(here, threading.Lock())
        if holds(hit):
            return hit[1]  # type: ignore[index]
        if not before(hit):
            got = working.acquire(timeout=ANSWER_WAIT_MOST)  # (a thread here works it out: it is alive, B-1914)
        elif not (got := working.acquire(blocking=False)):
            return hit[1]  # type: ignore[index]  (another thread here works it out again: the one before meanwhile)
        try:
            with _KEPT_LOCK:
                hit = _KEPT.get(here)
            if holds(hit, later=True):  # (another thread here worked it out meanwhile)
                return hit[1]  # type: ignore[index]
            found = self.kept_answer(name, key, version)
            lease, taken, took = f"answer/{name}/{key}", True, holds(found, later=True)
            if not took:
                taken = self._briefly_or(lambda: self.take_lease(lease, owner, ANSWER_LEASE), False)  # type: ignore[attr-defined]
                # Another process works it out (its hold: not just a database too busy to give one, B-1838)
                other = not taken and self._lease_held(lease)
                if other and before(hit) and not (before(found) and found[2] > hit[2]):  # type: ignore[index]
                    return hit[1]  # type: ignore[index]  (another process works it out again: the one before meanwhile)
                took = other and before(found)  # (or the database's, when it is the latest)
                # another process works it out, with no answer before to show: taken once written, waited for while
                # its hold is renewed (it is alive and working, B-1807), up to ANSWER_WAIT_MOST (B-1914)
                until = time.monotonic() + ANSWER_WAIT_MOST
                while not taken and not took and time.monotonic() < until:
                    time.sleep(ANSWER_POLL)
                    found = self.kept_answer(name, key, version)
                    took = holds(found, later=True)
                    if took or not self._lease_held(lease):  # (it stopped, or wrote one for an older stamp)
                        break
            unkept: Callable[[], Any] | None = None  # what the database was too busy to keep just now (B-1808)
            try:
                if took:
                    value, kept = decode(found[1]), found  # type: ignore[index]
                else:
                    stamp, start = seen, began
                    if not taken:  # (after waiting for another process: worked out from what the database holds now)
                        start, (stamp, _) = wall(), self._answer_stamp(topic, changes)
                    with self._holding(lease, owner) if taken else nullcontext():
                        value = compute()
                    kept = (stamp, value, start)
                    encoded = encode(value)
                    if self.keep_answer(name, key, version, stamp, encoded, start) is None:
                        unkept = lambda: self.keep_answer(name, key, version, stamp, encoded, start, wait=True)  # noqa: E731
                with _KEPT_LOCK:
                    _KEPT.pop(here, None)  # (the latest used last: the oldest goes first)
                    _KEPT[here] = (kept[0], value, kept[2])
                    while len(_KEPT) > KEPT_HERE:
                        _KEPT.pop(next(iter(_KEPT)))
                    if len(_WORKING) > 4 * KEPT_HERE:
                        for idle in [k for k, lock in _WORKING.items() if k != here and not lock.locked()]:
                            del _WORKING[idle]
                return value
            finally:
                if not took and taken:
                    self._let_go(lease, owner, unkept)
        finally:
            if got:
                working.release()

    def kept_answer(self, name: str, key: str, version: str) -> tuple[Any, Any, float] | None:
        """(the stamp, the value, when it was made: ``time.time``) of the answer kept under ``name`` and ``key``
        (``asked_key``) by this ``version``; None when there is none, or another version's (or one that can't be
        read as JSON) is."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT version, stamp, value, made_at FROM report_answers WHERE name = ? AND asked = ?",
                               (name, key)).fetchone()  # fmt: skip
        if row is None or row["version"] != _version(version):
            return None
        try:
            return json.loads(row["stamp"]), json.loads(row["value"]), float(row["made_at"])
        except (ValueError, TypeError):
            return None

    def keep_answer(self, name: str, key: str, version: str, stamp: Any, value: Any, made: float | None = None, *,
                    wait: bool = False) -> bool | None:  # fmt: skip
        """Keep ``value`` (JSON-ready) under ``name`` and ``key`` for the other processes, worked out under ``stamp``
        from ``made`` (``time.time``, default now); False when it is not kept: bigger than ``ANSWER_MOST_BYTES``, or
        not JSON (it is then worked out again, nothing else changes); None when the database was busy or full just
        now (waiting at most a moment for another writer; ``wait``: as long as any write does). The oldest past
        ``ANSWERS_KEPT`` of the name go."""
        try:
            text = json.dumps(value, separators=(",", ":"), allow_nan=False)
            stamped = json.dumps(stamp, separators=(",", ":"))
        except (TypeError, ValueError):
            _log.warning("the %s answer is not JSON: not kept for the other processes", name)
            return False
        if len(text) > ANSWER_MOST_BYTES:
            return False

        def write() -> bool:
            with self._conn() as conn:  # type: ignore[attr-defined]
                conn.execute(
                    "INSERT INTO report_answers (name, asked, version, stamp, value, made_at) VALUES "
                    "(?, ?, ?, ?, ?, ?) ON CONFLICT(name, asked) DO UPDATE SET version = excluded.version, "
                    "stamp = excluded.stamp, value = excluded.value, made_at = excluded.made_at",
                    (name, key, _version(version), stamped, text, wall() if made is None else made),
                )
                conn.execute("DELETE FROM report_answers WHERE name = ? AND asked NOT IN (SELECT asked FROM "
                             "report_answers WHERE name = ? ORDER BY made_at DESC LIMIT ?)",
                             (name, name, ANSWERS_KEPT))  # fmt: skip
            return True

        if not wait:
            return self._briefly_or(write, None)
        try:
            return write()
        except sqlite3.Error as exc:
            _log.info("not kept for the other processes (%s)", exc)
            return None

    def _answer_stamp(self, topic: str, changes: tuple[str, ...]) -> tuple[list[Any], bool]:
        """(the stamp an answer is worked out under, read now; whether this is inside a write or read under way)."""
        reads, args = ["(SELECT value FROM settings WHERE key = 'summary_id')"], []
        if topic:
            reads.append("(SELECT value FROM settings WHERE key = ?)")
            args.append(f"summary_generation/{topic}")
        for kind in changes:
            reads.append("(SELECT value FROM settings WHERE key = ?)")
            args.append(f"changes/{kind}")
        with self._conn() as conn:  # type: ignore[attr-defined]
            if topic:
                self._summary_fresh(conn)  # type: ignore[attr-defined]
            stamp = list(conn.execute(f"SELECT {', '.join(reads)}", args).fetchone())
            return stamp, conn.in_transaction

    @contextmanager
    def _holding(self, lease: str, owner: str) -> Iterator[None]:
        """While an answer is worked out: its hold renewed every ANSWER_RENEW on a thread of its own, so the others
        asking wait for it however long the work takes, and pass over a process that stopped within ANSWER_LEASE
        (B-1807). A renewal the database is too busy for is tried again at the next."""
        done = threading.Event()

        def renew() -> None:
            while not done.wait(ANSWER_RENEW):
                self._briefly_or(lambda: self.renew_lease(lease, owner, ANSWER_LEASE), False)  # type: ignore[attr-defined]

        beat = threading.Thread(target=renew, name="ap-coder-answer-hold", daemon=True)
        beat.start()
        try:
            yield
        finally:
            done.set()
            beat.join(1)

    def _let_go(self, lease: str, owner: str, unkept: Callable[[], Any] | None) -> None:
        """Let go of the hold on an answer worked out (``unkept``: first keep what the database was too busy to keep).
        What the database is too busy for just now is done on a thread of its own once it is free, so the others
        asking take the answer then, not ANSWER_WAIT later (B-1808); the answer is kept only while the hold is still
        this process's (another may have worked out a later one meanwhile)."""
        if unkept is None and self._briefly_or(lambda: self.drop_lease(lease, owner) or True, False):  # type: ignore[attr-defined]
            return

        def later() -> None:
            try:
                if unkept is not None and self.renew_lease(lease, owner, ANSWER_LEASE):  # type: ignore[attr-defined]
                    unkept()
                self.drop_lease(lease, owner)  # type: ignore[attr-defined]
            except sqlite3.Error as exc:  # the hold runs out by itself within ANSWER_LEASE
                _log.info("the hold on %s was not let go of (%s)", lease, exc)

        threading.Thread(target=later, name="ap-coder-answer-let-go", daemon=True).start()

    def _lease_held(self, name: str) -> bool:
        with self._conn() as conn:  # type: ignore[attr-defined]
            return conn.execute("SELECT 1 FROM ephemeral WHERE kind = 'lease' AND token = ? AND expires_at >= ?",
                                (name, time.time())).fetchone() is not None  # fmt: skip

    def _briefly_or(self, write: Callable[[], Any], otherwise: Any) -> Any:
        """``write()``, waiting at most a moment for another writer (``briefly``); ``otherwise`` when the database is
        busy or full: what a page keeps for later never holds it up or fails it."""
        try:
            with self.briefly():  # type: ignore[attr-defined]
                return write()
        except sqlite3.Error as exc:
            _log.info("not kept for the other processes (%s)", exc)
            return otherwise

    # --- Downloads made by the background process -----------------------------------------------------------------

    def start_download(self, owner: str, kind: str, params: dict[str, Any], name: str, most: int,
                       version: str) -> tuple[int | None, bool]:  # fmt: skip
        """(the job, whether it is new) making ``kind`` of ``params`` for ``owner``: theirs already waiting or being
        made (asked again, or from another web worker at once), else a new one waiting; (None, False) when they have
        ``most`` waiting or being made already. ``name``: the file's name as it is downloaded."""
        text = json.dumps(params, sort_keys=True, separators=(",", ":"))
        with self._conn() as conn:  # type: ignore[attr-defined]
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")  # one asker at a time: never two jobs of the same
            db = self._database_id(conn)
            row = conn.execute(f"SELECT id FROM report_jobs WHERE owner = ? AND kind = ? AND params = ? AND db = ? AND "
                               f"state IN {_OPEN}", (owner, kind, text, db)).fetchone()  # fmt: skip
            if row is not None:
                return int(row["id"]), False
            waiting = conn.execute(f"SELECT COUNT(*) FROM report_jobs WHERE owner = ? AND db = ? AND state IN {_OPEN}",
                                   (owner, db)).fetchone()[0]  # fmt: skip
            if waiting >= most:
                return None, False
            made = conn.execute(
                "INSERT INTO report_jobs (owner, kind, params, name, state, note, db, version, created_at) VALUES "
                "(?, ?, ?, ?, ?, 'Waiting its turn…', ?, ?, ?)",
                (owner, kind, text, name, WAITING, db, _version(version), time.time()),
            )
            return int(made.lastrowid), True

    def download_job(self, job_id: int) -> dict[str, Any] | None:
        """Job ``job_id`` as its row has it (``params`` decoded), with how many wait before it; None when there is
        none on this database (gone, or one of the database before a restore)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT * FROM report_jobs WHERE id = ? AND db = ?",
                               (job_id, self._database_id(conn))).fetchone()  # fmt: skip
            if row is None:
                return None
            job = dict(row)
            job["ahead"] = (conn.execute("SELECT COUNT(*) FROM report_jobs WHERE state = ? AND id < ? AND db = ?",
                                         (WAITING, job_id, job["db"])).fetchone()[0] if job["state"] == WAITING
                            else 0)  # fmt: skip
        job["params"] = json.loads(job["params"])
        return job

    def claim_download(self, runner: str, stale: float, version: str) -> dict[str, Any] | None:
        """The oldest job waiting, or being made by a background process that stopped renewing it ``stale`` seconds
        ago (taken again, ``TRIES`` times in all), now ``runner``'s (``params`` decoded); None when there is none.
        One left by another AP Coder version, or tried ``TRIES`` times, fails with why."""
        now = time.time()
        with self._conn() as conn:  # type: ignore[attr-defined]
            if conn.execute("SELECT 1 FROM report_jobs WHERE state = ? OR (state = ? AND renewed_at < ?) LIMIT 1",
                            (WAITING, RUNNING, now - stale)).fetchone() is None:  # fmt: skip
                return None  # (looked at twice a second while none waits: never the write lock for nothing)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            db = self._database_id(conn)
            while True:
                row = conn.execute(
                    "SELECT * FROM report_jobs WHERE db = ? AND (state = ? OR (state = ? AND renewed_at < ?)) "
                    "ORDER BY id LIMIT 1",
                    (db, WAITING, RUNNING, now - stale),
                ).fetchone()
                if row is None:
                    return None
                why = UPDATED if row["version"] != _version(version) else STOPPED_TWICE if row["tries"] >= TRIES else ""
                if why:
                    conn.execute("UPDATE report_jobs SET state = ?, error = ?, note = '', finished_at = ? WHERE id = ?",
                                 (FAILED, why, now, row["id"]))  # fmt: skip
                    continue
                conn.execute(
                    "UPDATE report_jobs SET state = ?, runner = ?, tries = tries + 1, renewed_at = ?, done = 0, "
                    "total = 0, note = 'Starting…' WHERE id = ?",
                    (RUNNING, runner, now, row["id"]),
                )
                job = dict(row) | {"state": RUNNING, "runner": runner, "tries": row["tries"] + 1}
                job["params"] = json.loads(job["params"])
                return job

    def renew_download(self, job_id: int, runner: str, done: int, total: int, note: str) -> bool:
        """How far ``runner`` got with the job (and that it is still at it); False when the job is no longer its own
        (taken again after it seemed stopped, or gone): it then stops."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            changed = conn.execute(
                "UPDATE report_jobs SET done = ?, total = ?, note = ?, renewed_at = ? WHERE id = ? AND runner = ? AND "
                "state = ?",
                (done, total, note, time.time(), job_id, runner, RUNNING),
            )
            return changed.rowcount == 1

    def finish_download(self, job_id: int, runner: str, file: str, size: int) -> bool:
        """The job made, its file ``file`` (in the downloads' folder) of ``size`` bytes; False when it is no longer
        ``runner``'s (the file is then not the job's: remove it)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            changed = conn.execute(
                "UPDATE report_jobs SET state = ?, file = ?, size = ?, note = '', done = total, finished_at = ? WHERE "
                "id = ? AND runner = ? AND state = ?",
                (MADE, file, size, time.time(), job_id, runner, RUNNING),
            )
            return changed.rowcount == 1

    def fail_download(self, job_id: int, runner: str, error: str) -> None:
        with self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute("UPDATE report_jobs SET state = ?, error = ?, note = '', finished_at = ? WHERE id = ? AND "
                         "runner = ? AND state = ?", (FAILED, error, time.time(), job_id, runner, RUNNING))  # fmt: skip

    def release_download(self, job_id: int, runner: str) -> None:
        """The job put back in line as it was (its background process stopping): the next one makes it."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute("UPDATE report_jobs SET state = ?, runner = '', tries = MAX(tries - 1, 0), done = 0, "
                         "note = 'Waiting its turn…' WHERE id = ? AND runner = ? AND state = ?",
                         (WAITING, job_id, runner, RUNNING))  # fmt: skip

    def sweep_downloads(self, keep: float, kept_each: int) -> list[str]:
        """Forget the jobs finished over ``keep`` seconds ago, those past each person's latest ``kept_each`` made,
        and every job and kept answer of another database (before a restore): the files of those that were made, to
        be removed."""
        now = time.time()
        with self._conn() as conn:  # type: ignore[attr-defined]
            db = self._database_id(conn)
            gone = conn.execute(
                f"SELECT id, file FROM report_jobs WHERE db != ? OR (state NOT IN {_OPEN} AND finished_at < ?) OR "
                "(state = ? AND id NOT IN (SELECT id FROM report_jobs r WHERE r.owner = report_jobs.owner AND "
                "r.state = ? AND r.db = ? ORDER BY r.id DESC LIMIT ?))",
                (db, now - keep, MADE, MADE, db, kept_each),
            ).fetchall()
            conn.execute("DELETE FROM report_jobs WHERE id IN (SELECT value FROM json_each(?))",
                         (json.dumps([r["id"] for r in gone]),))  # fmt: skip
            conn.execute("DELETE FROM report_answers WHERE json_extract(stamp, '$[0]') IS NOT ?", (db,))
        return [r["file"] for r in gone if r["file"]]

    def download_files(self) -> set[str]:
        """The files the jobs on record name: any other in the downloads' folder is no job's (``downloads.sweep``)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            return {row[0] for row in conn.execute("SELECT file FROM report_jobs WHERE file != ''")}

    def _database_id(self, conn: sqlite3.Connection) -> str:
        """The database's ``summary_id`` (another after a restore, D-159): which database a job was asked of."""
        row = conn.execute("SELECT value FROM settings WHERE key = 'summary_id'").fetchone()
        return str(row[0]) if row else ""
