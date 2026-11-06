"""The import queue's two ends (D-168): the files that go in line (``core/store/imports.py``), and the reading of them
into the review queue.

* ``pick_up``: the invoices folder's files the pick-up hasn't read since they changed (``Store.unseen_files``: a look
  at each file's size and time), each read once for its SHA-256, which goes with it all the way to its invoice.
* ``Reader``: takes the queue's files one at a time a thread and reads each (``InvoicePipeline.process``). It is what
  each capture process of the company server runs (``web/server.py run_capture``), the dashboard's own import on a
  laptop, and the ``watch`` and ``process`` commands. While it reads, a heartbeat renews its files' leases; it takes
  nothing while the disk of the data folder has less than ``backups.BACKUP_RESERVE`` free (the file stays in the
  folder, in line), nor while a backup is being restored.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import secrets
import shutil
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.store.folder import read_file
from ..core.store.imports import GONE, IMPORT_DONE, IMPORT_FAILED, IMPORT_LEASE_SECONDS, IMPORT_SKIPPED
from .files import folder_names

log = logging.getLogger(__name__)

NO_ROOM = ("The disk of the data folder is nearly full: AP Coder reads no more invoices until there is room. The "
           "files wait in line in the invoices folder.")  # fmt: skip
RESTORING = "A backup is being restored: the invoices in line are read once it is done."
Read = tuple[str, int | None, int | None, str]  # (place, size, time in nanoseconds, SHA-256), as ``read_file``


def owner_id(kind: str) -> str:
    """What the queue's rows name a reader by: its kind, computer, process and a token (a process number given again
    after a restart is another reader)."""
    return f"{kind}:{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"


def noted(path: Path, content: bytes) -> Read:
    """``read_file`` of a file just written from ``content`` (an upload), without reading it again."""
    seen = path.stat()
    return str(path.parent.resolve() / path.name), seen.st_size, seen.st_mtime_ns, hashlib.sha256(content).hexdigest()


def pick_up(store: Any, folder: Path, settling: Callable[[Path], bool]) -> tuple[list[Read], list[Path], list[Path]]:
    """(what to put in line, the files that can't be read now, those still being written: ``settling``) of the
    invoices in ``folder``. Only a file the pick-up hasn't read since it changed, nor whose content is in AP Coder or
    in line, is read (the folder keeps every invoice ever processed: a year of them on the server)."""
    folder = folder.resolve()
    listed = []
    for name in folder_names(folder)[0]:
        place = os.path.join(folder, name)
        try:
            seen = os.stat(place)
        except OSError:  # gone meanwhile
            continue
        listed.append((place, seen.st_size, seen.st_mtime_ns))
    new = store.unseen_files(listed)
    read, unreadable, saving = [], [], []
    for place, _, _ in listed:
        if place not in new:
            continue
        path = Path(place)
        try:
            if settling(path):
                saving.append(path)
                continue
            read.append(read_file(path))
        except OSError:  # open in another program, a cloud placeholder not downloaded: the next pick-up
            unreadable.append(path)
    return read, unreadable, saving


def has_room(folder: Path) -> bool:
    """Whether the disk of ``folder`` has ``backups.BACKUP_RESERVE`` free (the database and its backups need it more
    than one more invoice does). True when it can't be told."""
    from ..core.store.backups import BACKUP_RESERVE

    try:
        return shutil.disk_usage(folder).free >= BACKUP_RESERVE
    except OSError:
        return True


def requires_review(store: Any, reference: Any, result: Any) -> bool:
    """The checks' own flag (what the command line says); the dashboard says what its review queue says."""
    return bool(result.report and result.report.requires_review)


class Reader:
    """Reads the import queue's files into the review queue as ``owner`` (``owner_id``). ``settings``: the settings,
    or what gives them afresh (the .env read again); ``attention(store, reference, result)``: whether the line said
    of a file read is "needs attention"; ``on_result(row, result)``: told of each file read (the command line's
    outputs); ``pipeline()``: what reads them (default: an ``InvoicePipeline`` of the store's GL accounts, made
    afresh at each ``read_all``); ``paused``: why it last stopped taking files ("" when the queue was empty)."""

    def __init__(self, store: Any, settings: Any, owner: str, *, attention: Callable[..., bool] = requires_review,
                 cache_dir: str | Path | None = None, lease: float = IMPORT_LEASE_SECONDS,
                 on_result: Callable[[dict[str, Any], Any], None] | None = None,
                 pipeline: Callable[[], Any] | None = None) -> None:  # fmt: skip
        self.store, self.owner, self.attention, self.cache_dir, self.lease = store, owner, attention, cache_dir, lease
        self._settings = settings
        self.on_result = on_result
        self._make = pipeline
        self.paused = ""
        self._pipeline: Any = None
        self._lock = threading.Lock()

    def settings(self) -> Any:
        return self._settings() if callable(self._settings) else self._settings

    def read_all(self, stop: threading.Event | None = None, *, batch: int | None = None, workers: int = 1) -> int:
        """Read the files in line until none waits for this reader (``stop`` set: once the files under way are read).
        ``batch``: that batch's only; ``workers``: files read at once (threads, one pipeline). How many it read."""
        stop = stop or threading.Event()
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(done,), name="ap-coder-import-heartbeat", daemon=True)
        beat.start()
        counts: list[int] = []
        try:
            if workers <= 1:
                return self._loop(stop, batch)
            threads = [threading.Thread(target=contextvars.copy_context().run, name=f"ap-coder-import-{n + 1}",
                                        args=(self._counted, stop, batch, counts), daemon=True)
                       for n in range(workers)]  # fmt: skip
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            return sum(counts)
        finally:
            done.set()
            self._pipeline = None  # the GL accounts and settings read again next time

    def _counted(self, stop: threading.Event, batch: int | None, counts: list[int]) -> None:
        counts.append(self._loop(stop, batch))

    def _heartbeat(self, done: threading.Event) -> None:
        """Every lease/6 seconds: the files it reads, and its ``reader:`` lease (a batch of its own stays its own)."""
        while True:
            try:
                self.store.take_lease(f"reader:{self.owner}", self.owner, self.lease)
                self.store.renew_imports(self.owner, self.lease)
            except Exception:  # noqa: BLE001 - a busy database: renewed next time, well within the lease
                log.warning("the import queue's leases of %s were not renewed just now", self.owner, exc_info=True)
            if done.wait(self.lease / 6):
                self.store.drop_lease(f"reader:{self.owner}", self.owner)
                return

    def _loop(self, stop: threading.Event, batch: int | None) -> int:
        read = 0
        self.pipeline()  # (one that can't be made, the GL accounts open in Excel say, takes no file)
        while not stop.is_set():
            self.paused = "" if has_room(self.store.path.parent) else NO_ROOM
            if not self.paused and self.store.restoring():
                self.paused = RESTORING
            if self.paused:
                return read
            row = self.store.claim_import(self.owner, self.lease, batch)
            if row is None:
                return read
            read += self._read(row)
        return read

    def pipeline(self) -> Any:
        with self._lock:
            if self._pipeline is None and self._make is not None:
                self._pipeline = self._make()
            elif self._pipeline is None:
                from ..ai.pipeline import InvoicePipeline

                self._pipeline = InvoicePipeline(self.settings(), self.store.reference_data(), cache_dir=self.cache_dir,
                                                 store=self.store)  # fmt: skip
            return self._pipeline

    def _read(self, row: dict[str, Any]) -> int:
        """Read one file taken from the queue; 1 when it was read (0: gone meanwhile)."""
        from ..core.store import EVENT_ACTOR, EVENT_ORIGIN

        path, name = Path(row["path"]), row["name"]
        try:
            seen = os.stat(path)
        except OSError:  # removed or renamed since it came in: nothing to read
            gone = {"text": f"{name}: no longer in the invoices folder, so not read", "tone": "warn", "icon": "help"}
            self.store.finish_import(row["id"], self.owner, IMPORT_SKIPPED, error=GONE, line=gone)
            return 0
        same = (seen.st_size, seen.st_mtime_ns) == (row["size"], row["mtime_ns"])  # else written over: read again
        try:
            pipeline = self.pipeline()
            # Who brought it is who its invoice's events name (the signed-in person on the server, B-1106).
            context = contextvars.copy_context()
            context.run(EVENT_ACTOR.set, row["account"] or None)
            context.run(EVENT_ORIGIN.set, row["origin"] or None)
            result = context.run(pipeline.process, path, sha256=row["sha256"] if same else None)
        except Exception as exc:  # noqa: BLE001 - the pipeline says what failed itself; never left in line to fail again
            log.exception("Could not read %s from the import queue", path)
            self.store.finish_import(row["id"], self.owner, IMPORT_FAILED, error=f"{type(exc).__name__}: {exc}",
                                     line={"text": f"{name}: {exc}", "tone": "err", "icon": "error"})  # fmt: skip
            return 1
        if result.ok:
            try:
                flagged = self.attention(self.store, getattr(pipeline, "reference", None), result)
            except Exception:  # noqa: BLE001 - only what the line says
                flagged = bool(result.report and result.report.requires_review)
            vendor = (result.output or {}).get("vendor_name") or name
            line = {"text": f"{vendor}: {'needs attention' if flagged else 'ready'}", "tone": "warn" if flagged
                    else "ok", "icon": "flag" if flagged else "check_circle"}  # fmt: skip
        else:
            line = {"text": f"{name}: {result.error}", "tone": "err", "icon": "error"}
        self.store.finish_import(row["id"], self.owner, IMPORT_DONE if result.ok else IMPORT_FAILED,
                                 invoice_id=getattr(result, "invoice_id", None), line=line,
                                 error=result.error or "")  # fmt: skip
        if self.on_result is not None:
            self.on_result(row, result)
        return 1
