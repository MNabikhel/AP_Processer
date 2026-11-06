"""The import queue (schema 26, D-168): every file to read into the review queue waits here, whatever brought it (an
upload, the Process button's pick-up of the invoices folder, a scheduled ``watch``, ``ap_coder process``, a Retry),
until a reader takes it: on the company server one of its capture processes, on a laptop the dashboard's own import or
the command that brought it (``intake/imports.py``).

* **Kept over a restart:** a file waiting is taken up again. One being read by a process that stopped is read again
  once its lease runs out (``IMPORT_LEASE_SECONDS`` without its reader's heartbeat); a file whose reader stopped twice
  is failed with the reason (``STOPPED_TWICE``), as an invoice in Failed / rejected that Retry reads again.
* **Taken one at a time** in one BEGIN IMMEDIATE transaction: two readers never take the same file. A lease's end is
  a time of the database's clock (``julianday('now')``), and it is compared there, never in a process.
* **Never read twice:** a file whose content an invoice was made from is skipped as already in AP Coder, when it comes
  in and again when it is taken (a failed read counts for the folder's pick-ups, as they list the folder), and a file
  whose content another reader is reading waits for it.
* **Fair:** a person waiting (an upload, the Process button, a Retry, the command line) goes before a scheduled watch;
  between the people waiting, the one served least recently goes next (``import_turns``): a colleague's 1,000 files
  don't hold up your three.
* **The Process page's card** is the latest run (``import_run``): the batches that came in while the queue had work,
  from the one that found it empty. Each batch is recorded in the audit trail once its last file is done.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

from ._base import _now

IMPORT_WAITING, IMPORT_READING, IMPORT_DONE, IMPORT_FAILED, IMPORT_SKIPPED = (
    "waiting", "reading", "done", "failed", "skipped")  # fmt: skip
IMPORT_OPEN = (IMPORT_WAITING, IMPORT_READING)
IMPORT_TRIES = 2  # reads started of one file: the second stopped part-way fails it
IMPORT_LEASE_SECONDS = 120.0  # a file read with no heartbeat this long: its reader stopped
IMPORT_KEEP_DAYS = 7  # the files done, failed or skipped are kept this long (the latest run whatever its age)
GONE = "gone"  # a skipped file's error when it was no longer there to read
STOPPED_TWICE = ("AP Coder stopped twice while reading {name} (the computer restarted or the reading ran out of "
                 "memory), so it is not read again on its own. Retry reads it again; if it stops again, save it again "
                 "as a PDF, or ask the vendor for a new copy.")  # fmt: skip
# Where an import run is recorded from in the audit trail (``record_import_run``), by what brought its files.
RUN_SOURCE = {"upload": "dashboard", "folder": "dashboard", "retry": "dashboard", "watch": "watch",
              "process": "process"}  # fmt: skip

# A batch of one reader's own (``only``: the command line's) is anyone's once that reader's ``reader:`` lease ran out
# (it stopped). Asked with (the reader, time.time()).
_TAKES = ("(b.only_owner IN ('', ?) OR NOT EXISTS (SELECT 1 FROM ephemeral e WHERE e.kind = 'lease' AND e.token = "
          "'reader:' || b.only_owner AND e.expires_at >= ?))")  # fmt: skip
# The next file a reader takes: a person's first, then the uploader served least recently, then the oldest; never one
# whose content another reader is reading. Asked with (the reader, time.time(), one batch only or None, the same).
_NEXT = (
    "SELECT f.id, f.batch, f.sha256, b.include_failed, b.force, b.uploader FROM import_files f JOIN import_batches b "
    f"ON b.id = f.batch WHERE f.status = 'waiting' AND {_TAKES} AND (? IS NULL OR f.batch = ?) AND NOT EXISTS (SELECT "
    "1 FROM import_files r WHERE r.status = 'reading' AND r.sha256 = f.sha256) ORDER BY b.person DESC, "
    "COALESCE((SELECT t.served FROM import_turns t WHERE t.uploader = b.uploader), 0), f.id LIMIT 1"
)
_STORED = ("SELECT id FROM invoices WHERE (file_sha256 = ? OR original_sha256 = ?) AND (? OR status != 'failed') "
           "ORDER BY id DESC LIMIT 1")  # fmt: skip
# The queue's file being read here, as (its id, the reader): the invoice made from it is stored only while the claim is
# still that reader's, checked in the same transaction (``add_invoice``, B-1762). Set by ``intake.imports.Reader``.
IMPORT_CLAIM: contextvars.ContextVar[tuple[int, str] | None] = contextvars.ContextVar("ap_coder_import_claim",
                                                                                      default=None)  # fmt: skip


class ClaimLost(Exception):
    """The file's claim is no longer the reader's (it stalled past its lease, and another took the file): nothing of
    its reading is stored."""


class ImportsMixin:
    # --- The import queue (D-168) --------------------------------------------------------------------------------

    def queue_imports(
        self, files: list[tuple[str, int | None, int | None, str]], *, source: str, uploader: str = "",
        actor: str = "", account: str = "", origin: str = "", person: bool = False, include_failed: bool = False,
        force: bool = False, only: str = "", notes: list[Any] = (), lines: list[Any] = (), skipped: list[str] = (),
        emails: int = 0, to_person: int = 0,
    ) -> dict[str, Any]:  # fmt: skip
        """Put ``files`` in line, as the pick-up read them ((place, size, time in nanoseconds, SHA-256), noted for the
        import page: ``note_files``): one batch, in one transaction. A file whose content an invoice was made from
        (failed reads too with ``include_failed``), or a repeat within ``files``, is not put in line but named among
        the batch's skipped (``force``: every file in line). ``uploader``: whose turn it takes; ``actor``: who the run
        is recorded under; ``account`` and ``origin``: who and where from the invoices' events say (the signed-in
        person on the server); ``person``: someone waits for it; ``only``: the one reader that takes it (the command
        line's own); ``notes``, ``lines``, ``skipped``, ``emails``, ``to_person``: what the batch says of itself
        (messages from saving it, its emails' lines, the files left out before, its emails and those sent to a
        person). Returns {"batch", "run", "queued", "skipped" (names), "ahead" (files of others in line before)}."""
        now = _now()
        files = [(str(place), size, mtime, sha) for place, size, mtime, sha in files]
        with self.session(), self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute("BEGIN IMMEDIATE")
            known = set() if force else self.known_files([f[3] for f in files], include_failed)
            todo, left_out, seen = [], list(skipped), set()
            for place, size, mtime, sha in files:
                if sha in known or (sha in seen and not force):
                    left_out.append(Path(place).name)
                else:
                    todo.append((place, size, mtime, sha))
                seen.add(sha)
            ahead, run = conn.execute(
                "SELECT COUNT(*), MAX(b.run) FROM import_files f JOIN import_batches b ON b.id = f.batch WHERE "
                "f.status IN ('waiting', 'reading')"
            ).fetchone()  # fmt: skip
            batch = conn.execute(
                "INSERT INTO import_batches (source, uploader, actor, account, origin, person, include_failed, force, "
                "only_owner, notes, lines, skipped, emails, to_person, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?)",
                (source, uploader or "", actor or "", account or "", origin or "", int(bool(person)),
                 int(bool(include_failed)), int(bool(force)), only or "", json.dumps(list(notes), default=str),
                 json.dumps(list(lines), default=str), json.dumps(left_out), int(emails), int(to_person), now),
            ).lastrowid  # fmt: skip
            run = run or batch
            conn.execute("UPDATE import_batches SET run = ? WHERE id = ?", (run, batch))
            conn.executemany(
                "INSERT INTO import_files (batch, path, name, size, mtime_ns, sha256, created_at) VALUES (?, ?, ?, ?, "
                "?, ?, ?)", [(batch, place, Path(place).name, size, mtime, sha, now)
                             for place, size, mtime, sha in todo],
            )  # fmt: skip
            self.note_files([f for f in files if f[1] is not None])  # type: ignore[attr-defined]
            self._close_batches(conn, {batch})
            self._prune_imports(conn, run)
        return {"batch": batch, "run": run, "queued": len(todo), "skipped": left_out, "ahead": int(ahead or 0)}

    def known_files(self, shas: list[str], include_failed: bool = False) -> set[str]:
        """Those of ``shas`` an invoice was made from (as its file, or the picture its PDF was made from), failed reads
        too with ``include_failed``: one query, whatever their number (D-157)."""
        if not shas:
            return set()
        with self._conn() as conn:  # type: ignore[attr-defined]
            return {r[0] for r in conn.execute(
                "SELECT value FROM json_each(?) WHERE EXISTS (SELECT 1 FROM invoices WHERE file_sha256 = value AND "
                "(? OR status != 'failed')) OR EXISTS (SELECT 1 FROM invoices WHERE original_sha256 = value AND "
                "(? OR status != 'failed'))", (json.dumps(sorted(set(shas))), include_failed, include_failed),
            )}  # fmt: skip

    def claim_import(self, owner: str, lease: float = IMPORT_LEASE_SECONDS,
                     batch: int | None = None) -> dict[str, Any] | None:  # fmt: skip
        """The next file to read, marked as read by ``owner`` for ``lease`` seconds (``renew_imports`` renews it), with
        its batch's actor, account, origin and source; None when none waits for this reader. On the way, in the same
        transaction: a file whose reader stopped (its lease ran out) is put back in line, or failed the second time;
        a file already in AP Coder is skipped. ``batch``: that batch's files only."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            if conn.execute("SELECT 1 FROM import_files WHERE status = 'waiting' OR (status = 'reading' AND "
                            "lease_until < julianday('now')) LIMIT 1").fetchone() is None:  # fmt: skip
                return None  # (an idle reader's look every second takes no write lock)
        with self.session(), self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute("BEGIN IMMEDIATE")  # the write lock from the choice to the mark
            closed = self._expire_imports(conn)
            taken = None
            while taken is None:
                row = conn.execute(_NEXT, (owner, time.time(), batch, batch)).fetchone()
                if row is None:
                    break
                found = None if row["force"] else conn.execute(
                    _STORED, (row["sha256"], row["sha256"], row["include_failed"])).fetchone()  # fmt: skip
                if found is not None:
                    self._close_file(conn, row["id"], IMPORT_SKIPPED, invoice_id=found[0])
                    closed.add(row["batch"])
                    continue
                conn.execute(
                    "UPDATE import_files SET status = 'reading', owner = ?, lease_until = julianday('now') + ? / "
                    "86400.0, tries = tries + 1, started_at = ? WHERE id = ?", (owner, float(lease), _now(), row["id"]),
                )  # fmt: skip
                conn.execute(
                    "INSERT INTO import_turns (uploader, served) VALUES (?, (SELECT COALESCE(MAX(served), 0) + 1 FROM "
                    "import_turns)) ON CONFLICT(uploader) DO UPDATE SET served = excluded.served", (row["uploader"],),
                )  # fmt: skip
                taken = dict(conn.execute(
                    "SELECT f.*, b.source, b.actor, b.account, b.origin, b.person FROM import_files f JOIN "
                    "import_batches b ON b.id = f.batch WHERE f.id = ?", (row["id"],),
                ).fetchone())  # fmt: skip
            self._close_batches(conn, closed)
        return taken

    def renew_imports(self, owner: str, lease: float = IMPORT_LEASE_SECONDS) -> int:
        """The heartbeat: ``lease`` seconds more for every file ``owner`` is reading. How many it holds."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            return conn.execute(
                "UPDATE import_files SET lease_until = julianday('now') + ? / 86400.0 WHERE owner = ? AND status = "
                "'reading'", (float(lease), owner),
            ).rowcount  # fmt: skip

    def finish_import(self, file_id: int, owner: str, status: str, *, invoice_id: int | None = None,
                      line: dict[str, Any] | None = None, error: str = "") -> bool:  # fmt: skip
        """How the read of file ``file_id`` by ``owner`` ended: ``done`` (an invoice made), ``failed`` (``error`` says
        why; ``invoice_id`` the failed invoice, when one was saved) or ``skipped`` (no longer there to read: ``GONE``).
        ``line``: what the Process page says of it. False when ``owner`` no longer holds it (its lease ran out)."""
        with self.session(), self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT batch FROM import_files WHERE id = ? AND owner = ? AND status = 'reading'",
                               (file_id, owner)).fetchone()  # fmt: skip
            if row is None:
                return False
            self._close_file(conn, file_id, status, invoice_id=invoice_id, line=line, error=error)
            self._close_batches(conn, {row[0]})
        return True

    def release_imports(self, owner: str, file_id: int | None = None) -> int:
        """Put the files ``owner`` is reading (``file_id``: that one) back in line, their try not counted: the reader
        is stopping (a restart of the server, Ctrl+C), not stopped by the file. How many."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            return conn.execute(
                "UPDATE import_files SET status = 'waiting', owner = '', lease_until = NULL, tries = MAX(tries - 1, 0) "
                "WHERE owner = ? AND status = 'reading' AND (? IS NULL OR id = ?)", (owner, file_id, file_id),
            ).rowcount  # fmt: skip

    def imports_open(self, owner: str | None = None) -> dict[str, int]:
        """The files in line ({"waiting", "reading"}), of every batch; ``owner``: those waiting that it may take
        (not another live reader's own)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            found = dict(conn.execute(
                "SELECT f.status, COUNT(*) FROM import_files f JOIN import_batches b ON b.id = f.batch WHERE f.status "
                f"= 'reading' OR (f.status = 'waiting' AND (? IS NULL OR {_TAKES})) GROUP BY f.status",
                (owner, owner, time.time()),
            ).fetchall())  # fmt: skip
        return {status: int(found.get(status, 0)) for status in IMPORT_OPEN}

    def import_run(self, lines: int = 500) -> dict[str, Any] | None:
        """The latest run as the Process page shows it (None before the first): its batches; its files counted by
        status; the names read now; the last ``lines`` lines said of its files, in the order they were done; the
        names skipped as already in AP Coder; the errors of those failed; and how many of its invoices went in
        line for the page reader."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            run = conn.execute("SELECT MAX(run) FROM import_batches").fetchone()[0]
            if run is None:
                return None
            batches = [dict(r) for r in conn.execute("SELECT * FROM import_batches WHERE run = ? ORDER BY id", (run,))]
            ids = json.dumps([b["id"] for b in batches])
            within = "f.batch IN (SELECT value FROM json_each(?))"
            counts = dict(conn.execute(f"SELECT f.status || CASE WHEN f.status = 'skipped' AND f.invoice_id IS NULL "
                                       f"THEN '-gone' ELSE '' END, COUNT(*) FROM import_files f WHERE {within} GROUP "
                                       f"BY 1", (ids,)).fetchall())  # fmt: skip
            reading = [r[0] for r in conn.execute(f"SELECT f.name FROM import_files f WHERE {within} AND f.status = "
                                                  f"'reading' ORDER BY f.started_at, f.id", (ids,))]  # fmt: skip
            said = [json.loads(r[0]) for r in conn.execute(
                f"SELECT f.line FROM import_files f WHERE {within} AND f.line IS NOT NULL ORDER BY f.finished_at "
                f"DESC, f.id DESC LIMIT ?", (ids, int(lines)),
            )][::-1]  # fmt: skip
            skipped = [r[0] for r in conn.execute(
                f"SELECT f.name FROM import_files f WHERE {within} AND f.status = 'skipped' AND f.invoice_id IS NOT "
                f"NULL ORDER BY f.id", (ids,))]  # fmt: skip
            errors = [f"{r[0]}: {r[1]}" for r in conn.execute(
                f"SELECT f.name, f.error FROM import_files f WHERE {within} AND f.status = 'failed' ORDER BY f.id",
                (ids,))]  # fmt: skip
            queued = conn.execute(
                f"SELECT COUNT(*) FROM page_reads p WHERE p.status != 'skipped' AND p.invoice_id IN (SELECT "
                f"f.invoice_id FROM import_files f WHERE {within} AND f.status = 'done')", (ids,),
            ).fetchone()[0]  # fmt: skip
            ended = conn.execute(f"SELECT MAX(f.finished_at) FROM import_files f WHERE {within}", (ids,)).fetchone()[0]
        return {"run": run, "batches": batches, "counts": {k: int(v) for k, v in counts.items()}, "reading": reading,
                "lines": said, "skipped": skipped, "errors": errors, "page_reads": int(queued or 0),
                "finished_at": ended}  # fmt: skip

    def capture_processes(self) -> int:
        """The company server's capture processes running now (each renews its ``capture:`` lease, D-168)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            return int(conn.execute("SELECT COUNT(*) FROM ephemeral WHERE kind = 'lease' AND token LIKE 'capture:%' "
                                    "AND expires_at >= ?", (time.time(),)).fetchone()[0])  # fmt: skip

    # --- Inside a transaction of the queue's

    @staticmethod
    def _claim_noted(conn: Any, invoice_id: int, name: str) -> int:
        """In ``add_invoice``'s transaction: an invoice read from the import queue (``IMPORT_CLAIM``) is noted on its
        file while its reader still holds it, else ``ClaimLost`` and nothing is stored (B-1762). ``invoice_id``."""
        claim = IMPORT_CLAIM.get()
        if (
            claim is not None
            and not conn.execute(
                "UPDATE import_files SET invoice_id = ? WHERE id = ? AND owner = ? AND status = 'reading'",
                (invoice_id, *claim),
            ).rowcount
        ):  # its reader stalled past its lease and another took the file: that one stores it
            raise ClaimLost(f"{name} is read by another reader now: this reading is not stored")
        return invoice_id

    def _expire_imports(self, conn: Any) -> set[int]:
        """The files whose reader stopped (lease run out): back in line, or failed (an invoice saying why) once this was
        their last try. The batches that ended so."""
        closed: set[int] = set()
        for row in conn.execute("SELECT id, batch, path, name, sha256, tries FROM import_files WHERE status = "
                                "'reading' AND lease_until < julianday('now')").fetchall():  # fmt: skip
            if row["tries"] < IMPORT_TRIES:
                conn.execute("UPDATE import_files SET status = 'waiting', owner = '', lease_until = NULL WHERE id = ?",
                             (row["id"],))  # fmt: skip
                continue
            error = STOPPED_TWICE.format(name=row["name"])
            invoice_id = self.add_invoice(row["path"], None, None, error=error,  # type: ignore[attr-defined]
                                          sha256=row["sha256"])  # fmt: skip
            line = {"text": f"{row['name']}: {error}", "tone": "err", "icon": "error"}
            self._close_file(conn, row["id"], IMPORT_FAILED, invoice_id=invoice_id, line=line, error=error)
            closed.add(row["batch"])
        return closed

    @staticmethod
    def _close_file(conn: Any, file_id: int, status: str, *, invoice_id: int | None = None,
                    line: dict[str, Any] | None = None, error: str = "") -> None:  # fmt: skip
        conn.execute(
            "UPDATE import_files SET status = ?, owner = '', lease_until = NULL, invoice_id = ?, line = ?, error = ?, "
            "finished_at = ? WHERE id = ?",
            (status, invoice_id, json.dumps(line) if line else None, error or "", _now(), file_id),
        )

    def _close_batches(self, conn: Any, batches: set[int]) -> None:
        """Each of ``batches`` with no file left in line is done: recorded in the audit trail once (the finance hub's
        workflow_runs, D-103), as the import that read it."""
        for batch in sorted(batches):
            done = conn.execute(
                "UPDATE import_batches SET finished_at = ? WHERE id = ? AND finished_at IS NULL AND NOT EXISTS (SELECT "
                "1 FROM import_files WHERE batch = ? AND status IN ('waiting', 'reading'))", (_now(), batch, batch),
            ).rowcount  # fmt: skip
            if not done:
                continue
            b = conn.execute("SELECT * FROM import_batches WHERE id = ?", (batch,)).fetchone()
            files = conn.execute("SELECT status, name, error, invoice_id FROM import_files WHERE batch = ?",
                                 (batch,)).fetchall()  # fmt: skip
            read = [f for f in files if f["status"] in (IMPORT_DONE, IMPORT_FAILED)]
            skipped = len(json.loads(b["skipped"] or "[]")) + sum(
                1 for f in files if f["status"] == IMPORT_SKIPPED and f["invoice_id"] is not None)  # fmt: skip
            if not read and not skipped:
                continue
            started = dt.datetime.fromisoformat(b["created_at"])
            self.record_import_run(  # type: ignore[attr-defined]
                RUN_SOURCE.get(b["source"], "dashboard"), files=len(read),
                ok=sum(1 for f in read if f["status"] == IMPORT_DONE),
                failed=sum(1 for f in read if f["status"] == IMPORT_FAILED), skipped=skipped,
                started_at=b["created_at"], seconds=(dt.datetime.now() - started).total_seconds(),
                errors=[f"{f['name']}: {f['error']}" for f in read if f["status"] == IMPORT_FAILED],
                actor=b["actor"] or None,
            )  # fmt: skip

    @staticmethod
    def _prune_imports(conn: Any, run: int) -> None:
        """The files and batches done more than IMPORT_KEEP_DAYS ago go, but the latest run's."""
        before = (dt.datetime.now() - dt.timedelta(days=IMPORT_KEEP_DAYS)).isoformat(timespec="seconds")
        old = "SELECT id FROM import_batches WHERE run != ? AND finished_at IS NOT NULL AND finished_at < ?"
        conn.execute(f"DELETE FROM import_files WHERE batch IN ({old}) AND status NOT IN ('waiting', 'reading')",
                     (run, before))  # fmt: skip
        conn.execute(f"DELETE FROM import_batches WHERE id IN ({old}) AND NOT EXISTS (SELECT 1 FROM import_files f "
                     "WHERE f.batch = import_batches.id)", (run, before))  # fmt: skip
