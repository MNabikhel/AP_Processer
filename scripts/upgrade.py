"""Updating over an older AP Coder: one install, always the newest, nothing duplicated.

A new version is installed by unzipping its offline bundle anywhere (a new folder, or over the old one) and
double-clicking APProcessor. The launcher (scripts/launch.py) then uses what is here to take over from the old one:

* the record of installs (``~/.ap_coder/settings.json``, ``ap_coder/core/paths.py``): every AP Coder folder started on
  this computer and the current one. An older folder started by mistake hands over to the newest (:func:`newer_install`,
  :func:`hand_over`): the database is never opened by older code. An AP Coder 0.1 folder, which kept no record, is
  found from the data folder it used (``<folder>/private``) and the desktop shortcut (:func:`legacy_installs`).
* :func:`prune_stale`: unzipped over an older version, the files the older one had and this one does not (deleted
  modules and tests, old wheels) are removed: the bundle's manifest lists every file it holds.
* :func:`settle_data_folder`: data kept inside a code folder (``<folder>/private``, AP Coder 0.1) is moved to the data
  folder outside the code (``~/APCoder``), so replacing the code never touches it; a ``.env`` left in a code folder
  goes there too.
* :func:`repoint_files`: invoices record their file as an absolute path, so every path into an older folder (its
  demo invoices' samples, the invoices in its ``private/``) is pointed at the file's new place, or the file copied
  into the data folder, before the folder goes.
* :func:`clean_up`: once the new version is set up and its self-check passed, each older install's ``.venv`` (with
  its copy of the OCR models) and models are removed, and the whole folder when it is clearly an AP Coder folder
  holding nothing but what AP Coder put there (and nothing the database still needs). What was removed, and the
  space freed, is said.

The data folder's contents are never touched, except to move legacy data into it. Standard library only.
"""

from __future__ import annotations

import datetime as dt
import errno
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ap_coder.core import paths  # noqa: E402  (standard library only)

MARKER = ".ap_coder_install.json"  # written into a folder the launcher set up (not a git checkout)
LOCK_NAME = ".ap_coder_launch.lock"
# What an unzip over an older version may leave (samples/: a sample the self-check would read with no answer).
PRUNED = ("ap_coder", "scripts", "tests", "samples", "wheelhouse", "models")
# Made by AP Coder (or Python) inside a code folder: never user data, removed with the folder.
GENERATED = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".cache", MARKER, LOCK_NAME,
             ".env.moved-to-data-folder", ".ap_coder_server.json", ".DS_Store", "launch.out", "launch.err"}  # fmt: skip
# What an AP Coder bundle folder holds (for a folder whose manifest has no file list: AP Coder 0.1). The older
# versions' install.bat / install.sh / start.bat / start.sh / terminal.bat stay listed: the bundle no longer ships them,
# so unzipped over an older folder they are removed (prune_stale), and an older folder is still recognised. Every
# top-level name git tracks is here too: GitHub's ZIP holds them all (tests/test_update.py checks it).
BUNDLE_TOP = {"ap_coder", "scripts", "tests", "docs", "data", "samples", "static", "wheelhouse", "models", "private",
              ".streamlit", ".github", ".claude", "APProcessor.bat", "APProcessor.command", "install.bat",
              "install.sh", "start.bat", "start.sh", "terminal.bat", "README.md", "CLAUDE.md", "pyproject.toml",
              "requirements.txt", "streamlit_app.py", "bundle_manifest.json", ".env.example", ".gitignore",
              ".gitattributes"}  # fmt: skip
# Top-level names of a bundle folder never removed when a newer bundle no longer ships them: data, the person's own
# notes may be there, the manifest itself (not in its own file list), what starts AP Coder, and Claude Code's
# settings (the bundle leaves .claude/ out; a developer's own settings.local.json may be in it).
RETIRED_KEPT = {"private", "data", "docs", "bundle_manifest.json", "pyproject.toml", "APProcessor.bat",
                "APProcessor.command", "README.md", ".claude"}  # fmt: skip
DATA_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".xlsx", ".xlsm", ".xls")
# What starts an AP Coder folder: the double-click, and the installer and start scripts of the older versions.
LAUNCHERS = ("APProcessor.bat", "APProcessor.command", "start.bat", "start.sh", "install.bat", "install.sh")

Say = Callable[[str], None]


# --- Sizes and safe removal -------------------------------------------------------------------------------------


def size_of(path: Path) -> int:
    """Bytes in ``path`` (a file, or a folder and everything in it; links are not followed)."""
    if path.is_symlink() or path.is_file():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for folder, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return total


def shown_size(size: int) -> str:
    if size >= 1e9:
        return f"{size / 1e9:.1f} GB"
    return f"{size / 1e6:,.0f} MB" if size >= 1e6 else f"{size / 1e3:,.0f} KB"


def rmtree(path: Path) -> None:
    """Delete ``path`` and everything in it as far as possible, read-only files too (Windows refuses to delete one
    until it is made writable). What is in use is left, for a later start."""

    def writable_again(func: Callable, name: str, _exc: object) -> None:
        try:
            for target in (os.path.dirname(name), name):
                os.chmod(target, os.stat(target).st_mode | stat.S_IWRITE | stat.S_IREAD)
            func(name)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable_again)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=writable_again)


def remove_folder(folder: Path) -> bool:
    """Remove ``folder`` whole, or not at all as far as possible: it is first renamed (which fails at once while a
    program in it runs, on Windows), then deleted. True when it is gone; a part that could not be deleted is left as
    ``<name>.removing-<date>`` beside it (:func:`removing`), for a later start to finish."""
    if not folder.exists():
        return True
    aside = folder.with_name(f"{folder.name}.removing-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        folder.rename(aside)
    except OSError:
        return False
    rmtree(aside)
    return not aside.exists()


def removing(folder: Path) -> list[Path]:
    """What an earlier removal of ``folder`` (or of a folder in it) left behind: ``<name>.removing-<date>``."""
    found = []
    for parent, prefix in ((folder.parent, f"{folder.name}.removing-"), (folder, "")):
        try:
            found += [p for p in parent.iterdir() if p.name.startswith(prefix) and ".removing-" in p.name]
        except OSError:
            continue
    return found


# How the operating system says another process holds a lock (flock: EWOULDBLOCK; msvcrt: EACCES or EDEADLK). Any
# other error (ENOLCK, EOPNOTSUPP...) means the file system has no locks at all: a NAS or network volume.
LOCK_BUSY = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK, None}


def lock_busy(exc: OSError) -> bool:
    """Taking a lock failed with ``exc`` because another process holds it (not because locks can't be had here)."""
    return exc.errno in LOCK_BUSY


def lock_held(folder: Path) -> bool:
    """Another start holds ``folder``'s launch lock: AP Coder runs (or is being set up) from it right now."""
    lock = folder / LOCK_NAME
    if not lock.exists():
        return False
    try:
        handle = open(lock, "a+b")
    except OSError:
        return True
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        return lock_busy(exc)
    finally:
        handle.close()
    return False


# --- One install, always the newest ---------------------------------------------------------------------------


def is_install(folder: Path) -> bool:
    """``folder`` holds AP Coder's code: its ``ap_coder`` package and its launcher (``scripts/launch.py``), or, for
    the versions before the launcher (install.bat / start.bat, or by hand: ``python -m ap_coder dashboard``), a
    pyproject.toml naming the project ``ap-coder``."""
    if not (folder / "ap_coder").is_dir():
        return False
    return (folder / "scripts" / "launch.py").is_file() or project_name(folder) == "ap-coder"


def project_name(folder: Path) -> str:
    """The project name in ``folder``'s pyproject.toml ("" when none)."""
    try:
        text = (folder / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return ""
    found = re.search(r'(?m)^name\s*=\s*"([^"]+)"', text)
    return found.group(1) if found else ""


def newer_install(root: Path = ROOT) -> dict | None:
    """The current install, when it is another folder holding a newer AP Coder than ``root`` (else None). Read from
    the folders themselves, not only the record: a folder that is gone, or no longer newer, is not handed over to."""
    current = paths.current_install()
    if current is None or paths.same_folder(current["folder"], root):
        return None
    folder = Path(current["folder"])
    if not is_install(folder) or not (folder / "scripts" / "launch.py").is_file():  # (it is started through it)
        return None
    theirs = paths.install_identity(folder)
    return theirs if paths.is_older(paths.install_identity(root), theirs) else None


def venv_python(folder: Path) -> Path:
    return folder / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def base_python() -> str:
    """The Python this one was made from (outside any .venv): another folder's launcher runs with it."""
    return getattr(sys, "_base_executable", "") or sys.executable


def hand_over(folder: Path, argv: list[str], held_env: str) -> int:
    """Start AP Coder from ``folder`` (the newest install) with the same options, and wait for it."""
    own = venv_python(folder)
    python = str(own) if own.exists() else base_python()
    env = {k: v for k, v in os.environ.items() if k != held_env}  # that folder takes its own lock
    args = [a for a in argv if not a.startswith("--venv")]
    try:
        return subprocess.call([python, str(folder / "scripts" / "launch.py"), *args], cwd=folder, env=env)
    except KeyboardInterrupt:
        return 0


def write_marker(root: Path, identity: dict) -> None:
    """Mark ``root`` as a folder the launcher set up (so a later version may remove it once it is replaced). Never in
    a git checkout."""
    if (root / ".git").exists():
        return
    try:
        (root / MARKER).write_text(json.dumps({"app": "AP Coder", **identity}, indent=2), encoding="utf-8")
    except OSError:
        pass


def legacy_installs(root: Path, extra: list[str | Path] = ()) -> list[Path]:
    """AP Coder folders that kept no record of themselves (AP Coder 0.1): the folder whose ``private`` folder is the
    recorded data folder, and ``extra`` (the desktop shortcut's folder, a running old dashboard's folder)."""
    found: list[Path] = []
    data = paths.user_path(str(paths.read_user_settings().get("data_dir") or ""))
    candidates = [data.parent] if data and data.name == "private" else []
    for folder in [*candidates, *(Path(e) for e in extra if e)]:
        if is_install(folder) and not paths.same_folder(folder, root) and not any(paths.same_folder(folder, f)
                                                                                    for f in found):  # fmt: skip
            found.append(folder)
    return found


def nearby_installs(root: Path) -> list[Path]:
    """Older AP Coder folders next to ``root``, found though nothing points to them (closed before the update, no
    desktop shortcut on a Mac or Linux, AP Coder 0.1 kept no record): the folders beside it (a Mac unzips the new
    bundle as "APProcessor 2" beside "APProcessor"), and the bundle folder in each folder beside its parent
    (Windows' Extract All makes "APProcessor-offline-<version>-<date>\\APProcessor"). Only an older version than
    ``root``'s, never a git checkout."""
    mine = paths.install_identity(root)
    places: list[Path] = []
    for parent, depth in ((root.parent, 0), (root.parent.parent, 1)):
        try:
            children = sorted(p for p in parent.iterdir() if p.is_dir())
        except OSError:
            continue
        places += children if depth == 0 else [c / root.name for c in children]
    found: list[Path] = []
    for folder in places:
        if paths.same_folder(folder, root) or (folder / ".git").exists() or not is_install(folder):
            continue
        if not left_to_clean(folder) and removable(folder, root):
            continue  # cleaned up by an earlier start and kept (files AP Coder didn't put there): nothing to do
        known = any(paths.same_folder(folder, f) for f in found)
        if not known and paths.is_older(paths.install_identity(folder), mine):
            found.append(folder)
    return found


def left_to_clean(folder: Path) -> bool:
    """An older folder still holds something the clean-up takes: its .venv, models, settings or data (``private/``),
    or the rest of a removal."""
    try:
        data = (
            [p for p in (folder / "private").iterdir() if p.name != "README.md"]
            if (folder / "private").is_dir()
            else []
        )
    except OSError:
        data = [folder]
    return bool(data or removing(folder)) or any((folder / name).exists() for name in (".venv", "models", ".env"))


def remember(folders: list[Path]) -> None:
    """Put older AP Coder folders that kept no record of themselves (AP Coder 0.1) on the record, not as the current
    one. What found them (the data folder they used, the desktop shortcut) is changed by this start, and they are to
    be cleaned up once the new version passed its self-check: on this start, or a later one."""
    known = paths.installs()
    for folder in folders:
        if is_install(folder) and not any(paths.same_folder(folder, e["folder"]) for e in known):
            paths.record_install(paths.install_identity(folder), current=False)


def previous_versions(root: Path, extra: list[str | Path] = ()) -> list[dict]:
    """The identities of every other install on record (and the unrecorded AP Coder 0.1 ones), oldest first."""
    folders = [Path(e["folder"]) for e in paths.installs() if not paths.same_folder(e["folder"], root)]
    folders += [f for f in legacy_installs(root, extra) if not any(paths.same_folder(f, g) for g in folders)]
    found = [paths.install_identity(f) for f in folders if is_install(f)]
    return sorted(found, key=lambda i: (paths.version_key(i["version"]), i["build"]))


# --- Unzipped over an older version: its leftover files ---------------------------------------------------------


def prune_stale(root: Path = ROOT) -> tuple[list[str], int]:
    """Remove the files under ap_coder/, scripts/, tests/, samples/, wheelhouse/ and models/ that this version's bundle
    does not hold (an older version's, when the new bundle was unzipped over it), and AP Coder's own top-level files it
    no longer ships (e.g. streamlit_app.py): only when the manifest lists its files, and
    never in a git checkout. A folder is pruned only when this bundle's own files for it are there (it was unzipped
    here): a wheelhouse copied in from another bundle, or the only one when a bundle without one was unzipped over an
    older folder, is kept (its wheels say what they fit). Python's own caches are left alone. Returns (the files
    removed, bytes freed)."""
    listed = paths.bundle_manifest(root).get("files")
    if not isinstance(listed, dict) or not listed or (root / ".git").exists():
        return [], 0
    keep = {name.replace("\\", "/") for name in listed}
    removed: list[str] = []
    freed = 0
    for top in PRUNED:
        base = root / top
        if not base.is_dir() or not any(n.startswith(f"{top}/") and (root / n).is_file() for n in keep):
            continue
        for folder, dirs, files in os.walk(base, topdown=True):
            dirs[:] = [d for d in dirs if d != "__pycache__" and not d.endswith(".egg-info")]
            for name in files:
                path = Path(folder) / name
                rel = path.relative_to(root).as_posix()
                if rel in keep or name.endswith((".pyc", ".pyo")):
                    continue
                try:
                    size = path.lstat().st_size
                    path.unlink()
                except OSError:
                    continue
                removed.append(rel)
                freed += size
        for folder, _dirs, _files in sorted(os.walk(base), key=lambda w: -len(w[0])):  # deepest first
            current = Path(folder)
            try:
                left = [p for p in current.iterdir() if p.name != "__pycache__"]
                if not left and current != base:
                    shutil.rmtree(current, ignore_errors=True)
            except OSError:
                continue
    for name in sorted(BUNDLE_TOP - RETIRED_KEPT - set(PRUNED)):  # AP Coder's own top-level files it no longer ships
        path = root / name
        if not (path.is_file() or path.is_dir()) or any(n == name or n.startswith(f"{name}/") for n in keep):
            continue
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        size = sum(size_of(p) for p in files)
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                continue
        else:
            rmtree(path)
        if not path.exists():
            removed += [p.relative_to(root).as_posix() for p in files]
            freed += size
    return removed, freed


# --- Data kept in a code folder (AP Coder 0.1) ------------------------------------------------------------------


def has_data(folder: Path) -> bool:
    """``folder`` holds data: anything but a README, a settings file, the server's note and empty folders."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return False
    for entry in entries:
        if entry.name in ("README.md", ".env", ".ap_coder_server.json", ".write-test", ".ap_coder_write_test"):
            continue
        if entry.is_dir() and not any(p.is_file() for p in entry.rglob("*")):
            continue
        return True
    return False


class DataMoveError(Exception):
    """The data could not all be moved to the data folder: ``hint`` is the one line on what to do."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


def _move_into(items: list[Path], target: Path, replace: bool = False) -> None:
    """Move ``items`` into ``target``; a folder already there is merged (its files are never replaced, unless
    ``replace``: finishing a move that was interrupted, where what is still in the source is the whole copy)."""
    target.mkdir(parents=True, exist_ok=True)
    for item in items:
        dest = target / item.name
        if dest.is_dir() and item.is_dir():
            _move_into(list(item.iterdir()), dest, replace)
            shutil.rmtree(item, ignore_errors=True)
        elif not dest.exists() or (replace and dest.is_file() and item.is_file()):
            shutil.move(str(item), str(dest))
        else:
            aside = target / f"{item.stem}.from-old-install{item.suffix}"
            n = 2
            while aside.exists():
                aside = target / f"{item.stem}.from-old-install-{n}{item.suffix}"
                n += 1
            shutil.move(str(item), str(aside))


def adopt_legacy(folder: Path, data: Path, say: Say = print) -> bool:
    """Move the data an older AP Coder kept in code ``folder`` into the data folder ``data``: its ``private`` folder
    (when it is not ``data`` itself) and its ``.env``. When ``data`` already holds data of its own, nothing is
    merged: the old data goes, whole, to ``data/backups/from-<folder>-<date>/`` (and that is said). True when
    something moved."""
    moved = False
    legacy = folder / "private"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    aside = data / "backups" / f"from-{folder.name}-{stamp}"
    if legacy.is_dir() and not paths.same_folder(legacy, data) and has_data(legacy):
        items = [p for p in legacy.iterdir() if p.name not in ("README.md", ".ap_coder_server.json")]
        if has_data(data):
            _move_into(items, aside)
            say(f"  Moved the data the old AP Coder kept in {legacy} to {aside} (your data folder {data} already "
                "has its own; nothing was merged).")  # fmt: skip
        else:
            _move_into(items, data)
            say(f"  Moved your data from {legacy} into the data folder {data}.")
        moved = True
    env = folder / ".env"
    if env.is_file():
        target = data / ".env"
        if not target.exists():
            data.mkdir(parents=True, exist_ok=True)
            shutil.move(str(env), str(target))
            say(f"  Moved the settings file {env} into the data folder: {target}")
        elif target.read_bytes() == env.read_bytes():
            env.unlink()
        else:
            aside.mkdir(parents=True, exist_ok=True)
            shutil.move(str(env), str(aside / ".env"))
            say(f"  Kept the old settings file {env} as {aside / '.env'} (the data folder has its own: {target}).")
        moved = True
    return moved


def settle_data_folder(root: Path = ROOT, say: Say = print, default: Path | None = None) -> Path:
    """The data folder, outside every code folder. Data kept in a code folder's ``private`` folder (AP Coder 0.1:
    recorded as the data folder, or found in this folder with none recorded) is moved to ``~/APCoder`` and recorded
    there, unless ``~/APCoder`` already holds data of its own (then it stays where it is, and that is said). An
    ``AP_PRIVATE_DIR`` set by hand is used as it is. A move that was interrupted is finished first; one that can't
    be finished stops the start (:class:`DataMoveError`): AP Coder never runs on half the data."""
    if chosen := paths.env_path("AP_PRIVATE_DIR"):
        return chosen
    settings = paths.read_user_settings()
    pending = settings.get("data_move")
    if isinstance(pending, dict) and pending.get("from") and pending.get("to"):
        source, target = Path(pending["from"]), Path(pending["to"])
        move_data(source, target, resume=True)
        say(f"Finished moving your data from {source} to {target}: new versions use it from there.")
        if source.name == "private":
            repoint_files(source.parent, target, root, say, copy=False)
        return target
    default = default or paths.default_data_dir()
    recorded = paths.user_path(str(settings.get("data_dir") or ""))
    legacy = root / "private"
    if recorded is None:
        # Nothing recorded: an AP Coder run by hand kept its data in its own private/ folder (this one's, unzipped
        # over it, or an older install's found by its running dashboard or its shortcut: on the record by now).
        others = [Path(e["folder"]) / "private" for e in reversed(paths.installs())
                  if not paths.same_folder(e["folder"], root)]  # fmt: skip
        recorded = next((p for p in [legacy, *others] if has_data(p) and is_install(p.parent)), default)
        paths.write_user_settings(data_dir=str(recorded))
    if recorded.name == "private" and is_install(recorded.parent) and has_data(recorded):
        if has_data(default) and not paths.same_folder(default, recorded):
            say(f"Note: your data is still in {recorded}, inside an AP Coder code folder, because {default} already "
                f"holds other data. Keep that folder until you move one of them.")  # fmt: skip
            return recorded
        if not paths.same_folder(recorded.parent, root):
            remember([recorded.parent])  # (found from this data folder only: on the record before it moves)
        move_data(recorded, default)
        say(f"Moved your data from {recorded} (inside the code folder) to {default}: new versions use it from there.")
        # Its invoices point at their files' new place at once (the folder's other files: when it is cleaned up).
        repoint_files(recorded.parent, default, root, say, copy=False)
        return default
    return recorded


def move_data(source: Path, target: Path, resume: bool = False) -> None:
    """Move the data folder ``source`` into ``target`` and record ``target`` as the data folder once all of it is
    there. The move is on record while it runs (``data_move``), so one that is interrupted is finished by the next
    start (``resume``); one that fails raises :class:`DataMoveError` (what is not moved yet stays in ``source``)."""
    paths.write_user_settings(data_move={"from": str(source), "to": str(target)})
    try:
        items = [p for p in source.iterdir() if p.name not in ("README.md", ".ap_coder_server.json")]
        _move_into(items, target, replace=resume)
    except OSError as exc:
        if not source.is_dir():  # gone (moved whole, or deleted by hand): nothing is left to move
            paths.write_user_settings(data_dir=str(target), data_move=None)
            return
        what = f"{exc.strerror or exc}" + (f": {exc.filename}" if exc.filename else "")
        raise DataMoveError(
            f"Your data could not all be moved from {source} to {target} ({what}). Nothing was lost: what is not "
            f"moved yet is still in {source}.",
            "Close the program that has that file open (an old AP Coder window, Excel), then start AP Coder again: "
            "the move picks up where it stopped.",
        ) from exc
    paths.write_user_settings(data_dir=str(target), data_move=None)


# --- The files the database points to ---------------------------------------------------------------------------
#
# An invoice records its file as an absolute path. AP Coder 0.1's demo invoices point into its own samples/, and the
# invoices of one that kept its data inside it into its private/: moving private/ and deleting the old folder would
# leave each of them without its file. Before an older folder is deleted, every path into it is pointed at the file's
# new place (copied out first when it has none), in one transaction after a backup.

UPDATE_ACTOR = "AP Coder update"


def _relative(path: str | Path, folder: Path) -> Path | None:
    """``path`` relative to ``folder`` (as written, or with links resolved); None when it is not in it."""
    try:
        written, real = Path(path), Path(path).resolve()
        bases = {folder.absolute(), folder.resolve()}
    except (OSError, ValueError):
        return None
    for candidate in (written, real):
        for base in bases:
            key, base_key = os.path.normcase(str(candidate)), os.path.normcase(str(base))
            if key.startswith(base_key.rstrip(os.sep) + os.sep):
                return Path(str(candidate)[len(str(base).rstrip(os.sep)) + 1 :])
    return None


def live_database(data: Path) -> Path:
    """The database the dashboard uses (``AP_DB_PATH``, else the data folder's)."""
    return paths.env_path("AP_DB_PATH") or data / "ap_coder.db"


def _databases(folder: Path, data: Path) -> list[tuple[Path, list[Path]]]:
    """Each database that may point into ``folder``, with where that folder's ``private/`` went for it: the live
    one, and those of ``folder``'s data kept aside (``backups/from-<folder>-<date>/``) when the data folder had its
    own."""
    try:  # (not a glob: a folder name may hold "[")
        kept = [p for p in (data / "backups").iterdir() if p.name.startswith(f"from-{folder.name}-") and p.is_dir()]
    except OSError:
        kept = []
    asides = sorted(kept, reverse=True)
    found = [(live_database(data), [data, *asides])]
    found += [(aside / "ap_coder.db", [aside, data]) for aside in asides]
    return [(db, bases) for db, bases in found if is_database(db)]


def is_database(path: Path) -> bool:
    """``path`` is an SQLite database file (whatever else is there is no database to update)."""
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


class _Mover:
    """Where each file of ``folder`` the database points to is now (or is copied to, when ``copy``)."""

    def __init__(self, folder: Path, data: Path, root: Path, bases: list[Path], copy: bool) -> None:
        self.folder, self.data, self.root, self.bases, self.copy = folder, data, root, bases, copy
        self.done: dict[str, str | None] = {}
        self.copied: list[Path] = []
        self.left: list[str] = []

    def place(self, stored: str, sha: str | None) -> str | None:
        """The new path for ``stored`` (None: it is not in the folder, or stays where it is)."""
        rel = _relative(stored, self.folder) if stored else None
        if rel is None or not rel.parts:
            return None
        key = os.path.normcase(stored)
        if key not in self.done:
            new = self._find(rel, sha)
            self.done[key] = str(new.resolve()) if new is not None else None
        found = self.done[key]
        return found if found and os.path.normcase(found) != key else None

    def _find(self, rel: Path, sha: str | None) -> Path | None:
        old = self.folder / rel
        sha = sha or (paths.file_sha256(old) if old.is_file() else None)
        top = rel.parts[0].lower()
        if top == "private" and len(rel.parts) > 1:  # moved with the data: the same place in the data folder
            rest = Path(*rel.parts[1:])
            for base in self.bases:
                candidate = base / rest
                if paths.same_folder(candidate, old):
                    return None  # the data folder is this private/ itself: nothing moved
                if candidate.is_file() and (not sha or paths.file_sha256(candidate) == sha):
                    return candidate
            renamed = paths.find_moved_file(old, sha, [b / rest.parent for b in self.bases])  # a.from-old-install.pdf
            if renamed is not None:
                return renamed
        elif top == "samples":  # this version's own sample, when it has the same bytes
            candidate = self.root / rel
            if sha and candidate.is_file() and paths.file_sha256(candidate) == sha:
                return candidate
        if old.is_file():
            return self._copy_out(old, rel, sha) if self.copy else None
        return paths.find_moved_file(old, sha, [self.data / "invoices", self.root / "samples"], data=self.data)

    def _copy_out(self, old: Path, rel: Path, sha: str | None) -> Path | None:
        """Copy ``old`` into the data folder: a sample into ``invoices/``, any other file into
        ``invoices/from-<folder>/`` (its place in the folder kept). None (and noted) when it can't be copied."""
        if rel.parts[0].lower() == "samples":
            target = self.data / "invoices" / Path(*rel.parts[1:]).parent
        else:
            target = self.data / "invoices" / f"from-{self.folder.name}" / rel.parent
        dest, n = target / old.name, 2
        while dest.exists():
            if dest.is_file() and paths.file_sha256(dest) == sha:
                return dest  # copied before (an earlier start, or another invoice of the same file)
            dest, n = target / f"{old.stem}_{n}{old.suffix}", n + 1
        try:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, dest)
        except OSError:
            self.left.append(str(old))
            try:
                dest.unlink()
            except OSError:
                pass
            return None
        self.copied.append(dest)
        return dest


def _backed_up(db: Path) -> None:
    """A copy of ``db`` in its ``backups/`` folder before it is changed: the update's own (``...-before-update-...``)
    when it was made since ``db`` last changed, else a new one."""
    folder = db.parent / "backups"
    changed = max((p.stat().st_mtime for p in (db, db.with_name(db.name + "-wal")) if p.exists()), default=0)
    if any(p.stat().st_mtime >= changed for p in folder.glob("ap_coder-*before-update*.db")):
        return
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"ap_coder-{time.strftime('%Y%m%d-%H%M%S')}-before-update-file-paths.db"
    source, target = sqlite3.connect(db, timeout=30), sqlite3.connect(dest)
    try:
        source.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")
    finally:
        target.close()
        source.close()


def _repoint_database(db: Path, mover: _Mover, folder: Path) -> int:
    """Point ``db``'s invoices (their file, the meta's copy of it, the picture a PDF was made from) and the backup
    copy folder at their new place, in one transaction after a backup. Returns how many invoices changed."""
    conn = sqlite3.connect(db, timeout=30)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "invoices" not in tables:
            return 0
        rows = conn.execute("SELECT id, source_path, file_sha256, meta FROM invoices").fetchall()
        changes: list[tuple[str, str, int]] = []
        for invoice_id, stored, sha, meta_text in rows:
            try:
                meta = json.loads(meta_text) if meta_text else {}
            except ValueError:
                meta = {}
            meta = meta if isinstance(meta, dict) else {}
            new = mover.place(str(stored or ""), sha) or str(stored or "")
            edited = False
            source = meta.get("source")
            if isinstance(source, str) and source:
                moved = new if source == stored else (mover.place(source, None) or source)
                edited, meta["source"] = moved != source, moved
            original = meta.get("original")
            if isinstance(original, dict) and original.get("kept"):
                kept = str(original["kept"])
                moved = mover.place(kept, original.get("sha256")) or kept
                edited, original["kept"] = edited or moved != kept, moved
            if new != stored or edited:
                changes.append((new, json.dumps(meta, default=str) if edited else meta_text, invoice_id))
        setting = None
        if "settings" in tables:
            row = conn.execute("SELECT value FROM settings WHERE key = 'backup_copy_dir'").fetchone()
            rel = _relative(row[0], folder) if row and row[0] else None
            if rel is not None and rel.parts and rel.parts[0].lower() == "private":
                moved = next((b / Path(*rel.parts[1:]) for b in mover.bases if (b / Path(*rel.parts[1:])).is_dir()),
                             None)  # fmt: skip
                setting = str(moved) if moved is not None else None
    finally:
        conn.close()
    if not changes and setting is None:
        return 0
    _backed_up(db)
    conn = sqlite3.connect(db, timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany("UPDATE invoices SET source_path = ?, meta = ? WHERE id = ?", changes)
        if setting is not None:
            conn.execute("UPDATE settings SET value = ? WHERE key = 'backup_copy_dir'", (setting,))
        if "events" in tables:
            detail = {"from": str(folder), "invoices": len(changes), "copied": len(mover.copied),
                      "copied_to": sorted({str(p.parent) for p in mover.copied}), "not_moved": mover.left}  # fmt: skip
            when = dt.datetime.now().isoformat(timespec="seconds")
            conn.execute("INSERT INTO events (invoice_id, action, actor, detail, created_at) VALUES (NULL, ?, ?, ?, ?)",
                         ("files_moved", UPDATE_ACTOR, json.dumps(detail), when))  # fmt: skip
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(changes)


def repoint_files(folder: Path, data: Path, root: Path = ROOT, say: Say = print, copy: bool = True) -> list[str]:
    """Point every file path the databases record inside AP Coder folder ``folder`` at the file's new place, before
    ``folder`` goes: a file of its ``private/`` at the same place in the data folder ``data`` (or where that data
    was kept aside), a sample at this version's own (``root``) when the bytes are the same, else (``copy``) a copy in
    ``data/invoices/`` (``from-<folder>/`` for a file from elsewhere in it). Returns the files in ``folder`` the
    database still needs (it could not copy them): while there is one, ``folder`` is kept. A database that can't be
    read or changed is said, never a reason to stop the update (the dashboard looks for a file that moved, too)."""
    left: list[str] = []
    for db, bases in _databases(folder, data):
        mover = _Mover(folder, data, root, bases, copy)
        try:
            changed = _repoint_database(db, mover, folder)
        except (OSError, sqlite3.Error) as exc:
            say(f"  {db}: could not point its invoices at their files' new place yet ({exc}).")
            if copy:
                left.append(f"{db} could not be updated")
            continue
        left += mover.left
        if changed:
            copied = f"; {len(mover.copied)} copied into {data / 'invoices'}" if mover.copied else ""
            say(f"  {changed} invoice{'' if changed == 1 else 's'} now point to their files' new place{copied}.")
    return left


# --- Cleaning up after an update --------------------------------------------------------------------------------


def user_files(folder: Path, limit: int = 3) -> list[str]:
    """Files in code ``folder`` that AP Coder did not put there (up to ``limit``): with them, the folder is kept."""
    listed = paths.bundle_manifest(folder).get("files")
    keep = {n.replace("\\", "/") for n in listed} if isinstance(listed, dict) and listed else None
    found: list[str] = []
    for current, dirs, files in os.walk(folder):
        here = Path(current)
        rel_dir = here.relative_to(folder)
        dirs[:] = [d for d in dirs if d not in GENERATED and not d.endswith(".egg-info") and ".removing-" not in d]
        if rel_dir == Path("."):
            if keep is None:
                found += [d for d in dirs if d not in BUNDLE_TOP]
                dirs[:] = [d for d in dirs if d in BUNDLE_TOP]
        for name in files:
            if name in GENERATED or name.endswith((".pyc", ".pyo")):
                continue
            rel = (rel_dir / name).as_posix()
            if rel == "bundle_manifest.json" or rel == "private/README.md":
                continue
            if keep is not None:
                unlisted = rel not in keep
            else:
                top = rel.split("/", 1)[0]
                unlisted = top not in BUNDLE_TOP or rel.startswith("private/") or name == ".env" or (
                    name.lower().endswith(DATA_SUFFIXES) and top not in ("samples", "data"))  # fmt: skip
            if unlisted:
                found.append(rel)
            if len(found) >= limit:
                return found
    return found


def replaced_by(folder: Path, mine: dict) -> bool:
    """The install in ``folder`` is certainly older than ``mine`` (an install identity), or another copy of the same
    build: the only kind a clean-up may take. A folder whose build can't be ordered with this one (the same version
    from before the build stamp, copied by hand, a checkout) is never taken: never deleted when in doubt (B-551)."""
    return paths.compare(paths.install_identity(folder), mine) in ("older", "same")


def removable(folder: Path, root: Path) -> str:
    """Why ``folder`` (an older install) must be kept whole ("" when it can go): it is this folder or holds it, a
    git checkout, not an AP Coder bundle folder, running, or holds files AP Coder did not put there."""
    if paths.same_folder(folder, root) or str(root.resolve()).startswith(str(folder.resolve()) + os.sep):
        return "it holds this AP Coder"
    if (folder / ".git").exists():
        return "it is a git checkout"
    launcher = any((folder / name).is_file() for name in LAUNCHERS)
    # Unzipped from a bundle, set up by the launcher, or an AP Coder from before both (GitHub's ZIP, installed with
    # install.bat or by hand): its pyproject names the project.
    marked = (folder / "bundle_manifest.json").is_file() or (folder / MARKER).is_file() or project_name(folder) == (
        "ap-coder")  # fmt: skip
    if not (launcher and marked and (folder / "ap_coder").is_dir()):
        return "it doesn't look like a folder AP Coder was installed in"
    if lock_held(folder):
        return "AP Coder is running from it"
    extra = user_files(folder)
    if extra:
        return f"it has files AP Coder didn't put there ({', '.join(extra)})"
    return ""


def clean_up(root: Path, data: Path, old: list[Path], say: Say = print) -> int:
    """Remove what the older installs ``old`` take up: first their data moves to the data folder, then their
    ``.venv`` (and its copy of the OCR models) and models go, and each whole folder that :func:`removable` allows.
    A folder done is taken off the record; one in use is tried again on a later start. Returns the bytes freed (and
    says what was removed)."""
    freed = 0
    mine = paths.install_identity(root)  # (no version: not an install folder, e.g. a test's: nothing to order by)
    for folder in old:
        if paths.same_folder(folder, root) or (folder / ".git").exists():
            continue
        if not folder.exists():
            for rest in removing(folder):  # what an earlier removal left behind
                size = size_of(rest)
                rmtree(rest)
                freed += size - (size_of(rest) if rest.exists() else 0)
            if not removing(folder):
                paths.forget_install(folder)
            continue
        if lock_held(folder):
            say(f"  {folder}: AP Coder is running from it; it is cleaned up on a later start.")
            continue
        if mine["version"] and is_install(folder) and not replaced_by(folder, mine):  # (whoever passed it)
            say(f"  Kept {folder}: it can't be told whether it is older than this AP Coder, so nothing in it was "
                "touched.")  # fmt: skip
            continue
        try:
            adopt_legacy(folder, data, say)
        except OSError as exc:  # a file of its data in use or read-only: the folder stays, whole, for now
            say(f"  {folder}: its data could not be moved yet ({exc.strerror or exc}); it is cleaned up on a later "
                "start.")  # fmt: skip
            continue
        needed = repoint_files(folder, data, root, say)  # before anything in it goes
        why = removable(folder, root)
        later = False  # the whole folder is to go, on a later start: kept on record
        if not why and needed:
            shown = ", ".join(Path(n).name for n in needed[:3]) + (" …" if len(needed) > 3 else "")
            why, later = (f"the database still points to {len(needed)} file{'' if len(needed) == 1 else 's'} in it "
                          f"that could not be copied to the data folder ({shown}); it is tried again on a later "
                          "start", True)  # fmt: skip
        if not why:
            size = size_of(folder)
            if remove_folder(folder):
                freed += size
                paths.forget_install(folder)
                say(f"  Removed the older AP Coder folder {folder} ({shown_size(size)}).")
                continue
            if removing(folder):  # renamed, then partly deleted
                freed += size - sum(size_of(p) for p in removing(folder))
                say(f"  {folder}: most of it was removed; the rest is removed on a later start (a file in it is in "
                    "use).")  # fmt: skip
                continue
            # Not even renamed (Windows: an old AP Coder window still has it as its current folder): what can go now.
            why, later = "a program still has it open (an old AP Coder window?); it is removed on a later start", True
        parts = [p for p in (folder / ".venv", folder / "models") if p.exists()] + removing(folder)
        size = sum(size_of(p) for p in parts)
        left = [p for p in parts if not remove_folder(p)] + removing(folder)
        gone = size - sum(size_of(p) for p in left if p.exists())
        freed += gone
        if not left and not later:
            paths.forget_install(folder)
        what = f"removed its .venv and models ({shown_size(gone)}); " if gone else ""
        say(f"  Kept the older AP Coder folder {folder}: {what}the folder itself stays because {why}.")
    return freed
