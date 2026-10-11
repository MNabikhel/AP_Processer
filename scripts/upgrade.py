"""Updating over an older AP Coder: one install, always the newest, nothing duplicated.

A new version is installed by unzipping its offline bundle anywhere (a new folder, or over the old one) and
double-clicking APProcessor. The launcher (scripts/launch.py) then uses what is here to take over from the old one:

* the record of installs (``~/.ap_coder/settings.json``, ``ap_coder/paths.py``): every AP Coder folder started on this
  computer and the current one. An older folder started by mistake hands over to the newest (:func:`newer_install`,
  :func:`hand_over`): the database is never opened by older code. An AP Coder 0.1 folder, which kept no record, is
  found from the data folder it used (``<folder>/private``) and the desktop shortcut (:func:`legacy_installs`).
* :func:`prune_stale`: unzipped over an older version, the files the older one had and this one does not (deleted
  modules and tests, old wheels) are removed: the bundle's manifest lists every file it holds.
* :func:`settle_data_folder`: data kept inside a code folder (``<folder>/private``, AP Coder 0.1) is moved to the data
  folder outside the code (``~/APCoder``), so replacing the code never touches it; a ``.env`` left in a code folder
  goes there too.
* :func:`clean_up`: once the new version is set up and its self-check passed, each older install's ``.venv`` (with
  its copy of the OCR models) and models are removed, and the whole folder when it is clearly an AP Coder folder
  holding nothing but what AP Coder put there. What was removed, and the space freed, is said.

The data folder's contents are never touched, except to move legacy data into it. Standard library only.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ap_coder import paths  # noqa: E402  (standard library only)

MARKER = ".ap_coder_install.json"  # written into a folder the launcher set up (not a git checkout)
LOCK_NAME = ".ap_coder_launch.lock"
PRUNED = ("ap_coder", "scripts", "tests", "wheelhouse", "models")  # what an unzip over an older version may leave
# Made by AP Coder (or Python) inside a code folder: never user data, removed with the folder.
GENERATED = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".cache", MARKER, LOCK_NAME,
             ".env.moved-to-data-folder", ".ap_coder_server.json", ".DS_Store", "launch.out", "launch.err"}  # fmt: skip
# What an AP Coder bundle folder holds (for a folder whose manifest has no file list: AP Coder 0.1).
BUNDLE_TOP = {"ap_coder", "scripts", "tests", "docs", "data", "samples", "static", "wheelhouse", "models", "private",
              ".streamlit", ".github", "APProcessor.bat", "APProcessor.command", "install.bat", "install.sh",
              "start.bat", "start.sh", "terminal.bat", "README.md", "pyproject.toml", "requirements.txt",
              "streamlit_app.py", "bundle_manifest.json", ".env.example", ".gitignore", ".gitattributes"}  # fmt: skip
DATA_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".xlsx", ".xlsm", ".xls")

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
    except OSError:
        return True
    finally:
        handle.close()
    return False


# --- One install, always the newest ---------------------------------------------------------------------------


def is_install(folder: Path) -> bool:
    """``folder`` holds an AP Coder that can be started (its launcher and its code)."""
    return (folder / "scripts" / "launch.py").is_file() and (folder / "ap_coder").is_dir()


def newer_install(root: Path = ROOT) -> dict | None:
    """The current install, when it is another folder holding a newer AP Coder than ``root`` (else None). Read from
    the folders themselves, not only the record: a folder that is gone, or no longer newer, is not handed over to."""
    current = paths.current_install()
    if current is None or paths.same_folder(current["folder"], root):
        return None
    folder = Path(current["folder"])
    if not is_install(folder):
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
    import json

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
    """Remove the files under ap_coder/, scripts/, tests/, wheelhouse/ and models/ that this version's bundle does not
    hold (an older version's, when the new bundle was unzipped over it): only when the manifest lists its files, and
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
        return target
    default = default or paths.default_data_dir()
    recorded = paths.user_path(str(settings.get("data_dir") or ""))
    legacy = root / "private"
    if recorded is None:
        recorded = legacy if has_data(legacy) else default
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


def removable(folder: Path, root: Path) -> str:
    """Why ``folder`` (an older install) must be kept whole ("" when it can go): it is this folder or holds it, a
    git checkout, not an AP Coder bundle folder, running, or holds files AP Coder did not put there."""
    if paths.same_folder(folder, root) or str(root.resolve()).startswith(str(folder.resolve()) + os.sep):
        return "it holds this AP Coder"
    if (folder / ".git").exists():
        return "it is a git checkout"
    launcher = (folder / "APProcessor.bat").is_file() or (folder / "APProcessor.command").is_file()
    marked = (folder / "bundle_manifest.json").is_file() or (folder / MARKER).is_file()
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
        try:
            adopt_legacy(folder, data, say)
        except OSError as exc:  # a file of its data in use or read-only: the folder stays, whole, for now
            say(f"  {folder}: its data could not be moved yet ({exc.strerror or exc}); it is cleaned up on a later "
                "start.")  # fmt: skip
            continue
        why = removable(folder, root)
        later = False  # the whole folder is to go, on a later start: kept on record
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
