"""The files of a backup (D-143): what AP Coder needs besides the database to come back exactly as it was.

A backup is ``backups/ap_coder-<stamp>.db`` (the database, ``Store.backup_to``) and, beside it, its list of files
``ap_coder-<stamp>.files.json``: each file's place in the data folder, size, time and SHA-256, and the database's own
size and SHA-256. The files themselves are kept once per content, named by their SHA-256, in
``backups/files/<first 2 digits>/<sha256>``: a file that did not change since the last backup is not copied again, a
file changed or deleted since stays for every backup that lists it, and pruning (``prune``) drops only what no backup
kept lists. The second backup folder has the same layout (``copy_files``), so it restores on its own: a new computer.

In a backup (``AREAS``, ``SETTINGS_FILES``): the whole ``invoices/`` folder (the invoice PDFs and pictures,
``originals/``, ``deleted/``, ``statements/``, the saved emails, their attachments set aside and the triage log in
``emails/``), the data folder's ``emails/`` (managers' replies), ``reference/`` (the command line's reference files),
the rate table and JD Edwards mapping kept in the data folder, the JD Edwards postings ledger (merged on a restore,
never replaced: ``_merge_ledger``), the key that seals the authenticator apps' secrets (``authenticator.key``, D-146:
put back only where there is none, a new computer; never over the one here, which the accounts a restore keeps on the
server are sealed with, nor set aside), and an invoice's file kept anywhere else (``watch --folder``; put back in
``invoices/restored/``). Not in it: the ``.env`` (passwords; and a backup must not choose where AP Coder connects),
``server.json`` (this server's own install, made by ``service install``), the JD Edwards installation id (a restore
makes a new one, D-105), the caches, logs, exports and reports (made again), the OCR models (they come with AP Coder),
and the backups themselves.

A restore (``restore_files``) makes those places as they were: a file missing or different is put back (checked
against its SHA-256 as it is copied: a damaged or missing copy is said, never put in place), and a file that was not
there then (an invoice that came in after the backup, a reply changed since) is moved to
``backups/set-aside-<date>-<time>/``, never deleted.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ..authenticator import KEY_FILE
from ..paths import PROJECT_DIR, try_lock
from .postings import LEDGER_FILE

FORMAT = 1
OBJECTS = "files"  # beside the backups: files/<2 digits>/<sha256>
LIST_SUFFIX = ".files.json"
AREAS = ("invoices", "emails", "reference")  # folders of the data folder, whole
SETTINGS_FILES = ("canada_tax_rates.csv", "jde_orchestrations.json")  # files of the data folder kept as they are
RESTORED = "invoices/restored"  # where an invoice file kept outside the data folder is put back
STILL_WRITING = (".part", ".partial", ".tmp", ".crdownload", ".download", ".restoring")
GRACE = 86400.0  # a kept file no backup lists is dropped only after a day: a backup may be being made meanwhile
_SHA = re.compile(r"[0-9a-f]{64}")
_case = os.path.normcase  # how this computer compares names: Windows one file whatever its case (B-1563)


# --- One at a time (B-1559) -------------------------------------------------------------------------------------------

BUSY = "a backup is being made or restored just now: try again once it is done"
_HOLDING = threading.local()  # how deep the thread holding the lock is in it
_THIS_PROCESS = threading.Lock()


class Busy(OSError):
    """Another backup, a restore or an update's put-back is under way (``exclusive``)."""


def lock_path(data: Path) -> Path:
    return data / "backups" / ".lock"


@contextlib.contextmanager
def exclusive(data: Path) -> Iterator[None]:
    """One backup, restore or update's put-back at a time on the data folder ``data``: in this process and in any
    other (the server's web workers and its background process), by the operating system's own lock
    (``paths.try_lock``: released when a process ends, crashed or not). The thread holding it enters again freely (a
    restore's own safety backup). Raises ``Busy`` at once when another holds it: a daily backup waits for its next
    check, a person is told to try again."""
    if getattr(_HOLDING, "depth", 0):
        _HOLDING.depth += 1
        try:
            yield
        finally:
            _HOLDING.depth -= 1
        return
    if not _THIS_PROCESS.acquire(blocking=False):
        raise Busy(BUSY)
    try:
        handle = try_lock(lock_path(data))
        if handle is None:
            raise Busy(BUSY)
        _HOLDING.depth = 1
        try:
            yield
        finally:
            _HOLDING.depth = 0
            handle.close()
    finally:
        _THIS_PROCESS.release()


@dataclass(frozen=True)
class Retention:
    """Which daily backups a folder keeps (D-187): the newest ``daily``, and of the complete ones the newest of each of
    the last ``weekly`` weeks and ``monthly`` months that have one (a server off for a month keeps its history)."""

    daily: int = 14
    weekly: int = 8
    monthly: int = 12


RETENTION = Retention()  # the company server's
LAPTOP_RETENTION = Retention(weekly=0, monthly=0)  # a laptop keeps its newest 14, as before
_MADE_ON = re.compile(r"ap_coder-(\d{8})-")


def retention(data: Path | None = None) -> Retention:
    """The daily backups kept here: on the company server 14 days, 8 weeks and 12 months; on a laptop 14 days. The
    server is told as the doctor tells it (``service.is_server_here``): server mode, or server.json in the data folder
    ``data`` (a ``backup now`` or a restore run from IT's console, without AP_SERVER_MODE: B-1960)."""
    from ...ops.offline import server_mode

    if server_mode():
        return RETENTION
    from ...ops.service import is_server_here

    return RETENTION if data is not None and is_server_here(data) else LAPTOP_RETENTION


def _made_on(backup: Path) -> dt.date | None:
    """The day ``backup`` was made, from its name (None for a name that isn't one)."""
    found = _MADE_ON.match(backup.name)
    try:
        return dt.datetime.strptime(found.group(1), "%Y%m%d").date() if found else None
    except ValueError:
        return None


def kept_dailies(dailies: list[Path], keep: Retention = RETENTION) -> set[Path]:
    """Of the daily backups ``dailies`` (one folder's, any order), those ``keep`` keeps: the newest ``daily``, and the
    newest ``daily`` complete ones (with their list of files), so a backup cut short or of an older AP Coder (the
    database alone) never pushes out a complete one (B-1558); and the newest complete one of each of the last
    ``weekly`` weeks (ISO weeks) and ``monthly`` months that have one. The newest complete one is always kept."""
    newest = sorted(dailies, key=lambda p: p.name, reverse=True)
    complete = [p for p in newest if list_path(p).is_file()]
    kept = set(newest[: keep.daily]) | set(complete[: keep.daily]) | set(complete[:1] or newest[:1])
    for count, period in ((keep.weekly, lambda d: d.isocalendar()[:2]), (keep.monthly, lambda d: (d.year, d.month))):
        seen: set[Any] = set()
        for backup in complete:  # newest first: the first of a period is its newest
            day = _made_on(backup)
            if day is None or period(day) in seen:
                continue
            if len(seen) == count:
                break
            seen.add(period(day))
            kept.add(backup)
    return kept


def drop_old(dailies: list[Path], keep: Retention | None = None, data: Path | None = None) -> None:
    """Retention of the daily backups ``dailies`` (``kept_dailies``; ``keep`` by default this mode's, ``retention``
    of the data folder ``data``): the others go, with their lists."""
    kept = kept_dailies(dailies, keep or retention(data))
    for old in dailies:
        if old not in kept:
            old.unlink(missing_ok=True)
            list_path(old).unlink(missing_ok=True)


def list_path(backup: Path) -> Path:
    """``ap_coder-<stamp>.db`` -> ``ap_coder-<stamp>.files.json``, beside it."""
    return backup.with_name(backup.stem + LIST_SUFFIX)


def object_path(folder: Path, sha: str) -> Path:
    return folder / OBJECTS / sha[:2] / sha


def _held(rel: str) -> bool:
    """``rel`` (a path in a list of files) is one a backup holds: in ``AREAS`` or one of the files, never out of the
    data folder (a list planted on a share must not write the .env, or anywhere else)."""
    parts = rel.split("/")
    if (not rel or "\\" in rel or PureWindowsPath(rel).drive or PurePosixPath(rel).is_absolute()
            or any(p in ("", ".", "..") for p in parts)):  # fmt: skip
        return False
    return (parts[0] in AREAS and len(parts) > 1) or rel in SETTINGS_FILES or rel in (LEDGER_FILE, KEY_FILE)


def in_data_folder(stored: str, data: Path) -> Path | None:
    """The file a stored path names, in the data folder ``data`` as it is now (B-1552): kept relative to it
    (``emails/manager/x.eml``), or whole as an older AP Coder kept it (``D:\\APCoderData\\emails\\manager\\x.eml``,
    maybe on another computer: found at the same place under one of ``AREAS`` here). None when it is not there, or the
    path leads out of those places."""
    stored = str(stored or "").strip()
    windows = "\\" in stored or bool(PureWindowsPath(stored).drive)
    pure = PureWindowsPath(stored) if windows else PurePosixPath(stored)
    if not stored:
        return None
    if not (pure.is_absolute() or pure.drive):
        rel = "/".join(pure.parts)
        return data / rel if _held(rel) and (data / rel).is_file() else None
    whole = Path(stored)
    if whole.is_file() and any(_inside(whole, data / area) for area in AREAS):
        return whole
    parts = pure.parts
    for i, part in enumerate(parts):
        rel = "/".join(parts[i:])
        if part in AREAS and _held(rel) and (data / rel).is_file():
            return data / rel
    return None


def repoint_invoices(conn: sqlite3.Connection, data: Path) -> int:
    """After a restore onto a data folder at another path (a new computer): each invoice whose file was kept in the
    old one points at the same file here, the same SHA-256 (B-1552), so what reads the rows (not ``invoice_file``)
    finds it too. How many were changed."""
    changed = 0
    for row in conn.execute("SELECT id, source_path, file_sha256, meta FROM invoices").fetchall():
        stored = str(row[1] or "")
        found = in_data_folder(stored, data) if stored and row[2] else None
        try:
            if found is None or found == Path(stored) or _sha_size(found)[0] != row[2]:
                continue
        except OSError:  # (held by another program: found when the invoice is opened, ``invoice_file``)
            continue
        try:
            meta = json.loads(row[3]) if row[3] else None
        except ValueError:
            meta = None
        if isinstance(meta, dict) and meta.get("source") == stored:
            meta["source"] = str(found.resolve())
        conn.execute("UPDATE invoices SET source_path = ?, meta = ? WHERE id = ?",
                     (str(found.resolve()), json.dumps(meta, default=str) if isinstance(meta, dict) else row[3],
                      row[0]))  # fmt: skip
        changed += 1
    return changed


def _skipped(found: dict[str, Any]) -> list[str]:
    """The places of the files a backup could not read when it was made (``{"path", "why"}``; a list of the first
    version wrote ``"<path>: <why>"``)."""
    out = []
    for s in found.get("skipped") or []:
        rel = s.get("path") if isinstance(s, dict) else str(s).partition(": ")[0]
        if isinstance(rel, str) and _held(rel):
            out.append(rel)
    return out


def _entries(found: dict[str, Any]) -> list[dict[str, Any]]:
    """The list's files that can be restored (a sound path and SHA-256)."""
    out = []
    for e in found.get("files") or []:
        if (isinstance(e, dict) and isinstance(e.get("path"), str) and _held(e["path"])
                and isinstance(e.get("sha256"), str) and _SHA.fullmatch(e["sha256"])
                and isinstance(e.get("size"), int)):  # fmt: skip
            out.append(e)
    return out


def _load(listed: Path) -> dict[str, Any] | None:
    try:
        found = json.loads(listed.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) and isinstance(found.get("files"), list) else None


def read_list(backup: Path) -> dict[str, Any] | None:
    """The backup's list of files; None when it has none (a backup of an older AP Coder: the database only) or it
    can't be read."""
    return _load(list_path(backup))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    part = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        part.write_text(json.dumps(value, indent=1), encoding="utf-8")
        os.replace(part, path)
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink()
        raise


def data_files(data: Path) -> list[str]:
    """The data folder's files a backup holds, as ``invoices/a.pdf`` (one being written, ``.part``..., is not)."""
    found = []
    for area in AREAS:
        for folder, dirs, files in os.walk(data / area):
            dirs.sort()
            for name in sorted(files):
                rel = (Path(folder) / name).relative_to(data).as_posix()
                if not name.lower().endswith(STILL_WRITING) and _held(rel):
                    found.append(rel)
    return found + [name for name in (*SETTINGS_FILES, LEDGER_FILE, KEY_FILE) if (data / name).is_file()]


def _copy_hashed(source: Path, out: Path) -> tuple[str, int]:
    """Copy ``source`` into the new file ``out``: its SHA-256 and size."""
    digest, size = hashlib.sha256(), 0
    with open(source, "rb") as src, open(out, "xb") as dest:
        for block in iter(lambda: src.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
            dest.write(block)
    return digest.hexdigest(), size


def _incoming(folder: Path) -> Path:
    root = folder / OBJECTS
    root.mkdir(parents=True, exist_ok=True)
    return root / f".incoming-{uuid.uuid4().hex}"


def _fresh(path: Path) -> None:
    """A kept file used by the backup being made is not dropped by a pruning meanwhile (``GRACE``)."""
    with contextlib.suppress(OSError):
        if time.time() - path.stat().st_mtime > GRACE / 2:
            os.utime(path)


def _keep(source: Path, folder: Path) -> tuple[str, int]:
    """``source`` kept in ``folder``'s files under its SHA-256 (once: a content kept already is not kept twice)."""
    part = _incoming(folder)
    try:
        sha, size = _copy_hashed(source, part)
        final = object_path(folder, sha)
        if final.is_file() and final.stat().st_size == size:
            part.unlink()
            _fresh(final)
        else:
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(part, final)
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink()
        raise
    return sha, size


def _readable(source: Path) -> str:
    """ "" when ``source`` can be read, else why not (a scanner or Excel holding it): the file is left out and said."""
    try:
        with open(source, "rb"):
            return ""
    except OSError as exc:
        return exc.strerror or type(exc).__name__


def _from_database(backup: Path) -> tuple[int | None, list[str]]:
    """(the invoices the backup's database holds, the files they were read from)."""
    try:
        conn = sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True)
        try:
            count = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
            sources = [r[0] for r in conn.execute("SELECT DISTINCT source_path FROM invoices WHERE source_path != ''")]
        finally:
            conn.close()
    except sqlite3.Error:
        return None, []
    return int(count), [str(s) for s in sources if s]


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except (OSError, ValueError):
        return False
    return True


def _elsewhere(sources: Iterable[str], data: Path) -> list[Path]:
    """The invoices' files outside the folders a backup holds (a watched folder elsewhere, data an update kept aside
    in ``backups/from-...``, also inside the install folder: B-1562), there now; never the samples that come with
    AP Coder."""
    out = []
    for stored in sources:
        path = Path(stored)
        if any(_inside(path, data / area) for area in AREAS) or _inside(path, PROJECT_DIR / "samples"):
            continue
        if path.is_file():
            out.append(path)
    return out


def snapshot(backup: Path, data: Path) -> dict[str, Any]:
    """The list of files of ``backup`` (the database copied already), the files that are new kept beside it. A file
    unchanged since the last backup (its size and time as listed then) is not read again. Raises OSError when the
    files can't be kept (a full disk): the database copy is there, its list is not."""
    folder = backup.parent
    before: dict[str, dict[str, Any]] = {}  # the newest list: its files' sizes and times, by place
    for listed in sorted(folder.glob(f"ap_coder-*{LIST_SUFFIX}"), key=_mtime, reverse=True):
        if listed != list_path(backup) and (found := _load(listed)) is not None:
            before = {e.get("from") or e["path"]: e for e in _entries(found)}
            break
    invoices, sources = _from_database(backup)
    entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    taken: set[str] = set()

    def add(source: Path, rel: str, key: str) -> dict[str, Any] | None:
        try:
            st = source.stat()
        except OSError:
            return None  # gone meanwhile
        old = before.get(key)
        if old and old["size"] == st.st_size and old.get("mtime_ns") == st.st_mtime_ns:
            kept = object_path(folder, old["sha256"])
            with contextlib.suppress(OSError):
                if kept.stat().st_size == st.st_size:
                    return {"path": rel, "sha256": old["sha256"], "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        why = _readable(source)
        if why:
            skipped.append({"path": rel, "why": why})
            return None
        sha, size = _keep(source, folder)
        return {"path": rel, "sha256": sha, "size": size, "mtime_ns": st.st_mtime_ns}

    for rel in data_files(data):
        if entry := add(data / rel, rel, rel):
            entries.append(entry)
            taken.add(rel)
    for source in _elsewhere(sources, data):
        entry = add(source, f"{RESTORED}/{source.name}", str(source))
        if entry is None:
            continue
        if entry["path"] in taken:  # two files of one name: each in a folder of its own
            entry["path"] = f"{RESTORED}/{entry['sha256'][:12]}/{source.name}"
        if entry["path"] not in taken:
            entries.append({**entry, "from": str(source)})
            taken.add(entry["path"])
    sha, size = _sha_size(backup)
    found = {"format": FORMAT, "database": backup.name, "database_size": size, "database_sha256": sha,
             "made": dt.datetime.now().isoformat(timespec="seconds"), "data_folder": str(data),
             "invoices": invoices, "files": entries, "skipped": skipped}  # fmt: skip
    _write_json(list_path(backup), found)
    return found


def before_update(store: Any, found: int) -> str:
    """The backup a ``Store`` makes before it brings a database of schema ``found`` up to date
    (``backups/ap_coder-<date>-before-update-from-schema-<n>.db``): the database copy, which is what undoes the update,
    then the files. Files that can't be saved (a nearly full disk) never stop AP Coder opening (B-1561): what to say
    then is returned, as ``backup_status`` has it ("" when all is saved)."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    made = store.backup_to(store.backup_dir() / f"ap_coder-{stamp}-before-update-from-schema-{found}.db")
    try:
        snapshot(made, store.path.parent)
    except OSError as exc:
        return (
            f"failed {dt.datetime.now().isoformat(timespec='minutes')}: the database was backed up before the "
            f"update ({made.name}), but not the invoice files ({exc}): free some space, then press Back up now"
        )
    return ""


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _sha_size(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as src:
        for block in iter(lambda: src.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


# --- What a backup holds, and whether all of it is there ----------------------------------------------------------


@dataclass
class Held:
    """What a backup holds (``check``): ``listed`` False for a backup of the database only."""

    listed: bool = False
    invoices: int | None = None
    files: int = 0
    size: int = 0  # the files' own, the database apart
    missing: list[str] = field(default_factory=list)
    damaged: list[str] = field(default_factory=list)  # (deep: the content is not the one listed; quick: the size)
    database_damaged: bool = False
    skipped: list[str] = field(default_factory=list)  # left out when it was made: could not be read
    copy: dict[str, Any] | None = None  # the copy to the second folder: {"ok", "when", "what"}

    @property
    def complete(self) -> bool:
        return self.listed and not (self.missing or self.damaged or self.database_damaged)


def check(backup: Path, deep: bool = False, seen: dict[str, int | None] | None = None) -> Held:
    """What ``backup`` holds and whether each of its files is there: of the size listed, or (``deep``) with the
    SHA-256 listed, the database too. ``seen``: the sizes found already (the page checks many backups that share
    their files)."""
    found = read_list(backup)
    if found is None:
        return Held()
    entries = _entries(found)
    held = Held(listed=True, invoices=found.get("invoices") if isinstance(found.get("invoices"), int) else None,
                files=len(entries), size=sum(e["size"] for e in entries),
                skipped=[f"{s['path']}: {s['why']}" if isinstance(s, dict) else str(s)
                         for s in found.get("skipped") or []],
                copy=found.get("copy") if isinstance(found.get("copy"), dict) else None)  # fmt: skip
    seen = {} if seen is None else seen
    for e in entries:
        kept = object_path(backup.parent, e["sha256"])
        if deep:
            try:
                sha, size = _sha_size(kept)
            except OSError:
                held.missing.append(e["path"])
                continue
            if (sha, size) != (e["sha256"], e["size"]):
                held.damaged.append(e["path"])
            continue
        if e["sha256"] not in seen:
            try:
                seen[e["sha256"]] = kept.stat().st_size
            except OSError:
                seen[e["sha256"]] = None
        if seen[e["sha256"]] is None:
            held.missing.append(e["path"])
        elif seen[e["sha256"]] != e["size"]:
            held.damaged.append(e["path"])
    try:
        if deep:
            held.database_damaged = _sha_size(backup) != (found.get("database_sha256"), found.get("database_size"))
        else:
            held.database_damaged = backup.stat().st_size != found.get("database_size")
    except OSError:
        held.database_damaged = True
    return held


def size_words(size: int) -> str:
    for unit, step in (("GB", 1 << 30), ("MB", 1 << 20)):
        if size >= step:
            return f"{size / step:.1f} {unit}"
    return f"{size / 1024:.0f} KB"


def holds_words(backup: Path, held: Held) -> str:
    """``1,234 invoices, 2,000 files (12.0 MB + 1.4 GB)``: what a backup holds, its database and its files."""
    db = size_words(backup.stat().st_size)
    if not held.listed:
        return f"the database ({db})"
    invoices = "" if held.invoices is None else f"{held.invoices:,} invoice{'' if held.invoices == 1 else 's'}, "
    return f"{invoices}{files(held.files)} ({db} + {size_words(held.size)})"


def state_words(held: Held) -> str:
    """Whether everything a backup lists is there: ``complete``, ``1 file missing``..."""
    if not held.listed:
        return "database only (no list of its files: made by an older AP Coder, or cut short while it was made)"
    if held.database_damaged:
        return "the database copy is damaged"
    bad = [f"{files(len(held.missing))} missing" if held.missing else "",
           f"{files(len(held.damaged))} damaged" if held.damaged else ""]  # fmt: skip
    return ", ".join(b for b in bad if b) or "complete"


def record_copy(backup: Path, ok: bool, when: str, what: str) -> None:
    """How the copy of ``backup`` to the second folder went, kept in its list (the page shows it per backup)."""
    found = read_list(backup)
    if found is not None:
        found["copy"] = {"ok": ok, "when": when, "what": what}
        with contextlib.suppress(OSError):
            _write_json(list_path(backup), found)


# --- The second backup folder ----------------------------------------------------------------------------------------


def copy_files(backup: Path, target: Path) -> None:
    """The list of ``backup`` and the files it needs that ``target`` lacks, copied there (the database is copied by
    the caller). The list goes last, so a list in ``target`` always has its files; meanwhile a ``.part`` list keeps a
    pruning from dropping them. Raises OSError when a file can't be copied, or is missing or damaged here."""
    found = read_list(backup)
    if found is None:
        return
    listed = list_path(target / backup.name)
    pending = listed.with_name(listed.name + ".part")
    _write_json(pending, found)
    bad = []
    try:
        for e in _entries(found):
            dest = object_path(target, e["sha256"])
            with contextlib.suppress(OSError):
                if dest.stat().st_size == e["size"]:
                    continue
            part = _incoming(target)
            try:
                sha, _size = _copy_hashed(object_path(backup.parent, e["sha256"]), part)
                if sha != e["sha256"]:
                    raise ValueError
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(part, dest)
            except (FileNotFoundError, ValueError):
                bad.append(e["path"])
            finally:
                with contextlib.suppress(OSError):
                    part.unlink()
        _write_json(listed, found)
    finally:
        with contextlib.suppress(OSError):
            pending.unlink()
    if bad:
        raise OSError(f"{len(bad)} of the backup's files are missing or damaged on this computer ({bad[0]}...)")


# --- Pruning -------------------------------------------------------------------------------------------------------


def prune(folder: Path) -> None:
    """Drop what no backup in ``folder`` needs: a list whose database was deleted, and each kept file no list names
    (not one kept in the last ``GRACE`` seconds: a backup may be being made). A list that can't be read keeps every
    file."""
    needed: set[str] = set()
    now = time.time()
    for listed in [*folder.glob(f"ap_coder-*{LIST_SUFFIX}"), *folder.glob(f"ap_coder-*{LIST_SUFFIX}.part")]:
        if listed.name.endswith(".part"):
            if now - _mtime(listed) > GRACE:
                with contextlib.suppress(OSError):
                    listed.unlink()
                continue
        elif not listed.with_name(listed.name[: -len(LIST_SUFFIX)] + ".db").exists():
            with contextlib.suppress(OSError):
                listed.unlink()
            continue
        found = _load(listed)
        if found is None:
            return
        needed |= {e["sha256"] for e in _entries(found)}
    root = folder / OBJECTS
    if not root.is_dir():
        return
    for kept in [*root.glob("*/*"), *root.glob(".incoming-*")]:
        if kept.name not in needed and now - _mtime(kept) > GRACE:
            with contextlib.suppress(OSError):
                kept.unlink()


def kept_size(folder: Path) -> int:
    """The bytes the kept files in ``folder`` take, each once."""
    total = 0
    for kept in (folder / OBJECTS).glob("*/*"):
        with contextlib.suppress(OSError):
            total += kept.stat().st_size
    return total


# --- A restore -----------------------------------------------------------------------------------------------------


@dataclass
class FilesRestored:
    """What ``restore_files`` did."""

    database_only: bool = False  # the backup has no list of files: they were left as they are
    put_back: int = 0
    unchanged: int = 0
    set_aside: list[str] = field(default_factory=list)
    aside: Path | None = None
    missing: list[str] = field(default_factory=list)
    damaged: list[str] = field(default_factory=list)
    error: str = ""  # the files could not all be put back (a full disk...): the database is restored all the same

    def words(self) -> str:
        """In plain words, for the page, the console and the Activity log."""
        if self.error:
            return f"they could not all be put back ({self.error}): fix that, then restore this backup again"
        if self.database_only:
            return ("the backup holds the database only (no list of its files: made by an older AP Coder, or cut "
                    "short): the files were left as they are")  # fmt: skip
        said = f"{files(self.put_back + self.unchanged)} as they were ({self.put_back:,} put back)"
        if self.set_aside:
            said += f"; {files(len(self.set_aside))} added or changed since moved to backups/{self.aside.name}"
        bad = self.missing + self.damaged
        if bad:
            said += f"; {len(bad):,} could not be restored, missing or damaged in the backup: {', '.join(bad[:3])}"
            said += "..." if len(bad) > 3 else ""
        return said


def files(n: int) -> str:
    return f"{n:,} file{'' if n == 1 else 's'}"


def _same(path: Path, entry: dict[str, Any]) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_size != entry["size"]:
        return False
    return st.st_mtime_ns == entry.get("mtime_ns") or _sha_size(path)[0] == entry["sha256"]


NO_ROOM = (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC))


def _writing(exc: OSError, part: Path) -> bool:
    """``exc`` is about writing ``part`` here (a full disk...), not about reading the backup."""
    return exc.errno in (*NO_ROOM, errno.EROFS) or str(getattr(exc, "filename", "") or "") == str(part)


def _fetch(entry: dict[str, Any], folders: list[Path], part: Path) -> str:
    """The listed content copied to ``part``, from the first of ``folders`` that has it whole: "" when copied, else
    "missing" or "damaged". Raises OSError when ``part`` can't be written (a full disk: B-1560)."""
    why = "missing"
    for folder in folders:
        kept = object_path(folder, entry["sha256"])
        if not kept.is_file():
            continue
        part.unlink(missing_ok=True)
        try:
            if _copy_hashed(kept, part) == (entry["sha256"], entry["size"]):
                return ""
        except OSError as exc:
            if _writing(exc, part):
                part.unlink(missing_ok=True)
                raise
        why = "damaged"
    part.unlink(missing_ok=True)
    return why


def _merge_ledger(entry: dict[str, Any], folders: list[Path], path: Path) -> str:
    """The postings ledger: the backup's lines the one here lacks go before its own (it is append-only: what was
    written since the backup stays, D-105). On a new computer, the backup's."""
    part = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.restoring")
    why = _fetch(entry, folders, part)
    if why:
        return why
    try:
        theirs = part.read_text(encoding="utf-8").splitlines(keepends=True)
        try:
            mine = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except FileNotFoundError:
            mine = []
        have = {line.rstrip("\n") for line in mine}
        added = [line if line.endswith("\n") else line + "\n" for line in theirs if line.rstrip("\n") not in have]
        if added:
            part.write_text("".join(added + mine), encoding="utf-8")
            os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    return ""


def restore_files(
    backup: Path, data: Path, also: Iterable[Path] = (), present: set[str] | None = None
) -> FilesRestored:
    """Put the files of the data folder back as ``backup`` lists them (``also``: other backup folders to take a file
    from when it is missing or damaged in ``backup``'s own). What is here and was not then is moved aside, but never a
    file the backup could not read when it was made (B-1556), nor one written since the restore began (``present``:
    the files there when it began; an upload meanwhile, its row in the restored database: B-1559). Which files were
    there is a list, not a time: a file's time can read ahead of the clock on Windows (B-1566). A full disk stops it,
    said in ``error``."""
    present = set(data_files(data)) if present is None else present
    found = read_list(backup)
    if found is None:
        return FilesRestored(database_only=True)
    folders = [backup.parent, *also]
    wanted = {e["path"]: e for e in _entries(found)}
    left = {_case(rel) for rel in [*wanted, *_skipped(found)]}  # (one file on Windows, whatever its case: B-1563)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    done = FilesRestored(aside=data / "backups" / f"set-aside-{stamp}")
    n = 2
    while done.aside.exists():
        done.aside = data / "backups" / f"set-aside-{stamp}-{n}"
        n += 1

    def set_aside(path: Path, rel: str) -> None:
        dest = done.aside / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, dest)
        done.set_aside.append(rel)

    try:
        _put_back(wanted, folders, data, done, set_aside)
    except OSError as exc:  # writing here failed (a full disk): never said as a damaged backup (B-1560)
        room = exc.errno in NO_ROOM
        done.error = "there is not enough space on the disk" if room else str(exc)
        return done
    for rel in data_files(data):
        if _case(rel) not in left and rel not in (LEDGER_FILE, KEY_FILE) and rel in present:
            set_aside(data / rel, rel)
    return done


def _put_back(wanted: dict[str, dict[str, Any]], folders: list[Path], data: Path, done: FilesRestored,
              set_aside: Any) -> None:  # fmt: skip
    for rel, entry in wanted.items():
        target = data / rel
        if rel == LEDGER_FILE:
            why = _merge_ledger(entry, folders, target)
        elif _same(target, entry) or (rel == KEY_FILE and target.exists()):
            done.unchanged += 1
            continue
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.restoring")
            why = _fetch(entry, folders, part)
            if not why:
                if target.exists():
                    set_aside(target, rel)
                os.replace(part, target)
                with contextlib.suppress(OSError):
                    os.utime(target, ns=(entry.get("mtime_ns") or time.time_ns(),) * 2)
        if why:
            (done.missing if why == "missing" else done.damaged).append(rel)
        elif rel != LEDGER_FILE:
            done.put_back += 1
        else:
            done.unchanged += 1
