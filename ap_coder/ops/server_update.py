"""Updating AP Coder on the company server (D-100): ``python -m ap_coder server-update <bundle.zip>``, and ``--back``.

The steps, in order (``--dry-run`` prints them with the folders and files, and changes nothing). The first ones
prepare the new version while the old one keeps serving (D-188):

1. **Check the bundle**, from a copy in a private temporary folder (the file given may be changed once checked; the
   copy is what is unzipped): a ZIP holding one AP Coder folder (no path out of it), every file the manifest lists, CRCs
   that match, and a newer AP Coder than the one the service runs (``paths.compare``, as the laptop updater judges).
2. **Unzip** it next to the current folder, as ``APProcessor-<version>``.
3. **Set up** that folder with its own launcher (``scripts/launch.py --no-start --yes``: its ``.venv`` from its
   ``wheelhouse/``, offline), in server mode, so the launcher keeps the previous folder.
4. **Update a copy of the database** with the new version (``service/update-check``, removed after): the migration is
   proven, and timed, before anything stops.
5. **Self-check**: the new folder's ``scripts/pilot_check.py`` (the sample invoices read right).
6. **Copy the database ahead** for the safety copy (``copy_ahead``, D-196): the file held still (what is written
   meanwhile goes to its write-ahead log) and copied as it is, the old version still serving.

Then the switch, the service stopped (the downtime, printed and recorded):

7. **Stop the service** (``service stop``).
8. **Back up the database** and the files: ``backups/ap_coder-<time>-before-update-<version>.db`` and its list of
   files (``safety_copy``, D-143): the copy made ahead, brought up to date with what was written since (the log), so
   about as quick whatever the database's size. Its SHA-256 is read into the list once the service answers.
9. **Update the database**: the new version opens it once (``Store``: its migrations).
10. **Start the service** from the new folder (``run.cmd`` rewritten), and wait for the dashboard to answer. The
    backup is then copied to the second backup folder, when one is set (by the new version).

When 2 to 6 fail, nothing was stopped or changed: the old version kept serving. When 7 to 10 fail, the backup is put
back, the service switched back to the old folder and started again. The previous folder is kept: ``server-update
--back`` goes back to it and puts back the pre-update backup (an older version refuses a database a newer one has
changed). Putting a backup back keeps the user accounts and paused logins as they are, and logs everyone out
(``put_back``, D-101); it puts back the invoice files as they were then, the ones added since moved aside (D-143).
What it does is recorded in server.json (``folder``, ``previous_folder``, ``pre_update_backup``, ``history`` with
the times) and in the record of installs (``~/.ap_coder/settings.json``), as the laptop updater does. Where it is
is in ``service/update.json`` at each step: an update cut short (a power cut) is dealt with at the next
``server-update`` or the service's next start (``update_recovery``).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ..core import paths
from ..core.paths import PROJECT_DIR, private_dir
from ..core.store.backups import CopyAhead, accounts_in, carry_accounts_into, copy_database, one_file, setting_in
from . import service
from . import update_recovery as recovery
from .offline import sha256 as file_sha256

if TYPE_CHECKING:
    from ..core.store.backup_files import FilesRestored

Say = Callable[[str], None]
REQUIRED = ("bundle_manifest.json", "pyproject.toml", "ap_coder/__init__.py", "scripts/launch.py")
HISTORY_KEPT = 20
# Seconds the new dashboard has to answer: each web worker builds its memories before it serves (D-204: 25 s at most),
# and a database that never had the reports has them worked out first (200 s at most, ``refresh.PREPARE_MOST``)
UP_WAIT = 300.0
CHECK_TIMEOUT = 900  # the self-check: about 10 s, much longer on a busy server's first OCR


# What the new folder's Pythons, run as the administrator, keep of the administrator's environment: what Windows and
# Python need to start, nothing that decides which code runs (no PYTHON*, PIP_*), and never anything the data folder's
# .env said (D-109). They also run with -E -s: no PYTHON* variable, no user site-packages.
KEPT_ENV = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE",
            "HOMEPATH", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
            "PROGRAMW6432", "COMMONPROGRAMFILES", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
            "COMPUTERNAME", "USERNAME", "USERDOMAIN", "LANG", "LC_ALL", "TERM", "AP_PRIVATE_DIR")  # fmt: skip
SAFE_PYTHON = ("-E", "-s")
# The new version's migration (``Server.migrate``): its Store, without its own copy before it migrates (D-188).
MIGRATE = """import sys
from ap_coder.core.store import Store
try:
    from ap_coder.core.store import backup_files
    backup_files.before_update = lambda store, found: ""
except ImportError:
    pass
Store(sys.argv[1])
"""


def child_env(data: Path) -> dict[str, str]:
    """The environment of the Pythons ``server-update`` starts: :data:`KEPT_ENV` and server mode's own."""
    kept = {k: v for k, v in os.environ.items() if k.upper() in KEPT_ENV}
    return {**kept, "AP_PRIVATE_DIR": str(data), "AP_SERVER_MODE": "1", "AP_NO_PAUSE": "1"}


class BundleError(ValueError):
    """The ZIP is not an AP Coder offline bundle that can be installed: said in plain words."""


def _upgrade() -> Any:
    """scripts/upgrade.py, the laptop updater (standard library only): its safe removal and Python lookups."""
    name = "ap_coder_scripts_upgrade"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PROJECT_DIR / "scripts" / "upgrade.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --- 1. The bundle ----------------------------------------------------------------------------------------------------


def check_bundle(bundle: Path) -> dict:
    """{"top", "identity"} of an offline bundle, or BundleError."""
    if not bundle.is_file() or not zipfile.is_zipfile(bundle):
        raise BundleError(f"{bundle} is not a ZIP file: give the offline bundle (APProcessor-offline-<version>.zip).")
    not_bundle = f"{bundle.name} is not an AP Coder offline bundle"
    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
        for name in names:
            parts = PurePosixPath(name).parts
            if name.startswith(("/", "\\")) or "\\" in name or ".." in parts or (parts and ":" in parts[0]):
                raise BundleError(f"{not_bundle}: it holds {name!r}, a path out of its folder.")
        tops = {PurePosixPath(n).parts[0] for n in names if n.strip("/")}
        if len(tops) != 1:
            raise BundleError(f"{not_bundle}: it should hold one folder (APProcessor), not {len(tops)}.")
        top = tops.pop()
        missing = [r for r in REQUIRED if f"{top}/{r}" not in names]
        if missing:
            raise BundleError(f"{not_bundle}: {', '.join(missing)} missing.")
        bad = zf.testzip()
        if bad:
            raise BundleError(f"{bundle.name} is damaged ({bad} does not read back right): copy it again.")
        with tempfile.TemporaryDirectory(prefix="ap_coder-bundle-") as folder:
            root = Path(folder)
            for rel in ("bundle_manifest.json", "pyproject.toml", "ap_coder/BUILD"):
                if f"{top}/{rel}" in names:
                    (root / rel).parent.mkdir(parents=True, exist_ok=True)
                    (root / rel).write_bytes(zf.read(f"{top}/{rel}"))
            manifest = paths.bundle_manifest(root)
            identity = paths.install_identity(root)
        listed = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        absent = [f for f in listed if f"{top}/{f}" not in names]
        if absent:
            some = f"{len(absent)} files its manifest lists are" if len(absent) > 1 else "1 file its manifest lists is"
            raise BundleError(f"{bundle.name} is incomplete: {some} missing ({absent[0]}, ...). Copy it "
                              "again.")  # fmt: skip
    if not identity.get("version"):
        raise BundleError(f"{not_bundle}: its pyproject.toml has no version.")
    return {"top": top, "identity": identity}


def target_folder(current: Path, version: str) -> Path:
    """``APProcessor-<version>`` beside the current folder (``-2``, ``-3``... when that name is taken)."""
    base = current.parent / f"APProcessor-{version}"
    folder, n = base, 2
    while folder.exists():
        folder, n = base.with_name(f"{base.name}-{n}"), n + 1
    return folder


def unzip(bundle: Path, top: str, dest: Path) -> None:
    """The bundle's folder as ``dest``: unzipped beside it first, then renamed (never a half-unzipped ``dest``)."""
    work = dest.with_name(f".{dest.name}.unzipping-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        with zipfile.ZipFile(bundle) as zf:
            zf.extractall(work)
        (work / top).rename(dest)
    finally:
        if work.exists():
            _upgrade().rmtree(work)


# --- The database: copies that never migrate (no Store opened on them: core/store/backups.py, ``copy_database``) ------


def put_back(backup: Path, db: Path) -> FilesRestored:
    """``backup`` copied over the database (``copy_database``), keeping the user accounts, their passwords and roles,
    and the paused logins as they are now, and ending every session: a backup must not bring back an account turned
    off, a password changed since, or a session (D-101). A backup older than the accounts gets their tables, so the
    server is never left with no account. Then the files of the data folder as the backup lists them (D-143; what is
    there now and was not then is moved aside): what was done with them is returned. Never while a backup or a
    restore runs (``backup_files.exclusive``: raises ``backup_files.Busy`` with nothing changed, B-1559)."""
    from ..core.store import backup_files

    with backup_files.exclusive(db.parent):
        saved = accounts_in(db) if db.exists() else {}
        copy_database(backup, db)
        carry_accounts_into(db, saved)
        return backup_files.restore_files(backup, db.parent)


def private_copy(bundle: Path, folder: Path) -> Path:
    """The bundle copied into ``folder`` (a private temporary folder): what is checked is what is unzipped, even
    when the file given (on a share, in a folder others can write to) is changed meanwhile (D-101)."""
    copy = folder / bundle.name
    shutil.copyfile(bundle, copy)
    return copy


def _backup_name(db: Path, label: str) -> Path:
    return db.parent / "backups" / f"ap_coder-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{label}.db"


def copy_ahead(db: Path, label: str, say: Say) -> CopyAhead | None:
    """The safety copy begun while the old version serves (D-196): the database file held still and copied as it is,
    and the data folder's files listed and kept (so that once stopped only what changed since is read), the backups'
    lock held until ``safety_copy`` finishes it. None when the file can't be held still (said): it is copied whole
    once the service is stopped, as before."""
    from ..core.store import backup_files

    name = _backup_name(db, label)
    ahead = CopyAhead(db, name.with_name(name.name + ".part"))
    why = ahead.start()
    if why:
        say(f"The database could not be copied ahead ({why}): it is copied whole once the service is stopped.")
        return None
    try:
        backup_files.snapshot(ahead.part, db.parent, hashed=False, database=db)
    except BaseException:
        ahead.close()
        raise
    return ahead


def safety_copy(db: Path, label: str, ahead: CopyAhead | None = None, hashed: bool = True) -> Path:
    """A backup of the database and the data folder's files as they are now (D-143), made without AP Coder's own
    code opening the database (it may be a newer version's). Never while another backup or a restore runs (B-1559).
    ``ahead`` (``copy_ahead``): only what was written since it began is copied now. ``hashed`` False: the list of files
    is made without the database's SHA-256, read in later (``backup_files.hash_database``)."""
    from ..core.store import backup_files

    with contextlib.ExitStack() as lock:
        if ahead is None:
            lock.enter_context(backup_files.exclusive(db.parent))
        else:  # (the copy made ahead has held the backups' lock since it began: let go at the end)
            lock.callback(ahead.close)
        dest = _backup_name(db, label)
        part = ahead.part if ahead is not None else dest.with_name(dest.name + ".part")  # (no backup yet: B-1955)
        try:
            if ahead is None or not ahead.finish():
                copy_database(db, part)
                one_file(part)
            os.replace(part, dest)
            _flush_folder(dest.parent)
        except BaseException:
            for leftover in (part, part.with_name(part.name + "-journal")):
                with contextlib.suppress(OSError):
                    leftover.unlink(missing_ok=True)
            raise
        backup_files.snapshot(dest, db.parent, hashed=hashed)
    return dest


def _flush_folder(folder: Path) -> None:
    """The rename in ``folder`` on the disk before the database is migrated (Windows journals it)."""
    if os.name != "nt":
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


# --- The server: the service, the new folder's setup and checks (stood in for by the tests) ---------------------------


def server_settings(data: Path, task_name: str | None = None) -> service.ServerSettings:
    """The service's settings, with the task named on the command line, else "AP Coder", as ``service install|start|
    stop`` name it: never server.json's, which the service's account can write (B-1500). Otherwise the
    administrator's schtasks would end and run whatever task it named (B-1586). Its folder must be the one this
    runs from (``wrong_folder``)."""
    return service.settings(data, task_name=task_name or service.TASK_NAME)


class Server:
    """This computer's service (Windows' task, the Mac's LaunchDaemon) and the steps run in the new folder, for real."""

    def __init__(self, data: Path | None = None, settings: service.ServerSettings | None = None,
                 say: Say = print) -> None:  # fmt: skip
        self.data = data or private_dir()
        self.settings = settings or server_settings(self.data)
        self.say = say

    def installed(self) -> bool:
        return service.installed(self.settings)

    def answers(self) -> bool:
        """A dashboard answers on this computer (its /healthz), whatever runs it."""
        return service.dashboard_health(self.data, self.settings) is not None

    def stop(self) -> bool:
        return service.stop(self.data, self.settings, self.say) == 0

    def start(self) -> bool:
        return service.start(self.data, self.settings, self.say) == 0

    def up(self, folder: Path) -> bool:
        """The dashboard answers, from ``folder``."""
        deadline = time.monotonic() + UP_WAIT
        while time.monotonic() < deadline:
            health = service.dashboard_health(self.data, self.settings)
            if health and paths.same_folder(health.get("folder"), folder):
                return True
            time.sleep(2)
        return False

    def _env(self) -> dict[str, str]:
        return child_env(self.data)

    def set_up(self, folder: Path) -> bool:
        """The new folder's own launcher: its .venv and packages from its wheelhouse (offline), the OCR models."""
        cmd = [_upgrade().base_python(), *SAFE_PYTHON, str(folder / "scripts" / "launch.py"), "--no-start", "--yes"]
        done = subprocess.run(cmd, cwd=folder, env=self._env(), check=False)  # noqa: S603  (its window shows it)
        return done.returncode == 0 and service.folder_python(folder).exists()

    def migrate(self, folder: Path, db: Path) -> bool:
        """``db`` updated by the new version (its ``Store``), without the copy a Store makes before it migrates: the
        copy server-update updates first is thrown away, and the real database has its safety copy, brought up to date
        a moment before with the service stopped (D-188, D-196). A second copy would add its minutes to the downtime."""
        done = subprocess.run(
            [str(service.folder_python(folder)), *SAFE_PYTHON, "-c", MIGRATE, str(db)],
            cwd=folder,  # noqa: S603
            env=self._env(),
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        if done.returncode:
            self.say((done.stderr or done.stdout).strip()[-2000:])
        return done.returncode == 0

    def second_copy(self, folder: Path, db: Path, backup: Path) -> str:
        """The pre-update backup copied to the second backup folder, when one is set, by the new version (the old one
        can't open the updated database), once the service answers again: what was done ("" when none is set)."""
        from ..core.store import backup_files

        if not str(setting_in(db, "backup_copy_dir") or "").strip():
            return ""
        code = ("import sys; from pathlib import Path; from ap_coder.core.store import Store; "
                "Store(sys.argv[1]).copy_backup(Path(sys.argv[2]))")  # fmt: skip
        subprocess.run([str(service.folder_python(folder)), *SAFE_PYTHON, "-c", code, str(db), str(backup)],  # noqa: S603
                       cwd=folder, env=self._env(), capture_output=True, check=False)  # fmt: skip
        copied = backup_files.check(backup).copy
        if not copied:
            return "The backup was not copied to the second backup folder: Settings → Data & backups says why."
        return f"Also copied to {copied['what']}." if copied["ok"] else (
            f"Not copied to the second backup folder: {copied['what']}.")  # fmt: skip

    def self_check(self, folder: Path) -> tuple[bool, str]:
        cmd = [str(service.folder_python(folder)), *SAFE_PYTHON, str(folder / "scripts" / "pilot_check.py")]
        try:
            done = subprocess.run(cmd, cwd=folder, env=self._env(), capture_output=True, text=True,  # noqa: S603
                                  errors="replace", timeout=CHECK_TIMEOUT, check=False)  # fmt: skip
        except subprocess.TimeoutExpired:
            return False, f"Self-check FAILED: it did not finish in {CHECK_TIMEOUT // 60} minutes"
        lines = (done.stdout + done.stderr).strip().splitlines()
        if done.returncode == 0:
            return True, "Self-check OK: " + (lines[-1] if lines else "the sample invoices read right")
        wrong = [t.strip() for t in lines if t.startswith("FAIL")] or lines[-3:] or ["?"]
        return False, "Self-check FAILED: " + "; ".join(wrong[:5])


# --- Which folders it may trust, and the bundle's SHA-256 (D-109) ----------------------------------------------------


def running_folder() -> Path:
    """The AP Coder folder this command runs from (the administrator's ``cd``)."""
    return PROJECT_DIR


def wrong_folder(s: service.ServerSettings) -> str:
    """Why server.json's ``folder`` can't be trusted ("" = it can): it must be the folder this command runs from.
    server.json is in the data folder, which the service's account can write; the code folder only the
    administrators can (docs/SERVER.md step 5). Trusting another folder would run what is beside it as the
    administrator."""
    here = running_folder()
    if paths.same_folder(s.code_folder, here):
        return ""
    run = "cd /d into it, then .venv\\Scripts\\python.exe -m ap_coder server-update ..."
    if not service.WINDOWS:  # a Mac, Linux
        run = "cd into it, then sudo AP_PRIVATE_DIR=<the data folder> .venv/bin/python -m ap_coder server-update ..."
    return (f"server.json says the service runs {s.code_folder}, but this command runs from {here}. Run "
            f"server-update from the folder the service runs ({run}). If nobody moved it, server.json was changed by "
            "someone: check it, then install the service again from the right folder (service install --folder). "
            "Nothing was changed.")  # fmt: skip


def outside_install(folder: Path) -> str:
    """Why ``folder`` (server.json's ``previous_folder``) can't be trusted ("" = it can): it must be beside the
    folder this command runs from, inside the code folder only the administrators can change."""
    here = running_folder()
    if paths.same_folder(Path(folder).resolve().parent, here.resolve().parent):
        return ""
    return (f"server.json names {folder} as the previous version, which is not beside this AP Coder folder "
            f"({here.parent}): AP Coder won't run it. If nobody moved it, server.json was changed by someone: "
            "check it. Nothing was changed.")  # fmt: skip


def another_task(server: Server, s: service.ServerSettings, saved: dict) -> str:
    """Why the service may run under another task than ``s.task_name`` ("" = no sign of it), once no task of that name
    is installed: server.json names another (read only to refuse, never run: B-1586), or a dashboard answers.
    Going on would update a live server's database and switch its folder under it, the service never stopped."""
    named = str(saved.get("task_name") or "").strip()
    given = "pass --task-name <name>, the name service install was given"
    if named and named != s.task_name:
        why = f'server.json says the service was installed as "{named}": {given}'
    elif server.answers():
        why = (f"AP Coder's dashboard answers on this computer: if the service was installed under another name, "
               f"{given}; else stop the dashboard first")  # fmt: skip
    else:
        return ""
    return f'No scheduled task "{s.task_name}" is installed, but {why}. Nothing was changed.'


def hash_refusal(copy: Path, given: str | None, check: bool, say: Say) -> str:
    """Why the bundle can't be installed ("" = it can): the SHA-256 IT was given with it, by another way than the
    ZIP (the builder's ``.sha256`` file, sent apart), must be the copy's. Without one, only ``--no-hash-check``."""
    if not check:
        print("WARNING: --no-hash-check: the bundle's SHA-256 is NOT checked. Anyone who could change the ZIP on its "
              "way here can run their code on this server as the administrator. Give --sha256 instead.",
              file=sys.stderr)  # fmt: skip
        say("The bundle's SHA-256 was not checked (--no-hash-check): the update's record in server.json says so.")
        return ""
    want = (given or "").strip().lower()
    if not want:
        return ("Give the bundle's SHA-256: server-update <bundle.zip> --sha256 <64 hexadecimal digits>. The person "
                "who built the bundle has it (the .sha256 file made beside the ZIP): get it from them by another way "
                "than the ZIP (e-mail, chat, phone). Nothing was changed.")  # fmt: skip
    if len(want) != 64 or any(c not in "0123456789abcdef" for c in want):
        return f"--sha256 {given} is not a SHA-256 (64 hexadecimal digits). Nothing was changed."
    found = file_sha256(copy)
    if found != want:
        return (f"{copy.name}'s SHA-256 is {found}, not the {want} given: it is not the bundle that was built (copied "
                "wrong, or changed on its way). Nothing was changed. Copy it again, or ask for the right "
                "one.")  # fmt: skip
    say(f"Bundle SHA-256 checked: {found}")
    return ""


# --- The record in server.json ----------------------------------------------------------------------------------------


def _record(data: Path, entry: dict, **values: Any) -> None:
    history = service.read_config(data).get("history")
    history = [h for h in history if isinstance(h, dict)] if isinstance(history, list) else []
    entry = {"at": dt.datetime.now().isoformat(timespec="seconds"), **entry}
    service.write_config(data, history=[*history, entry][-HISTORY_KEPT:], **values)


def switch_to(data: Path, folder: Path) -> None:
    """The service runs ``folder`` from its next start: run.cmd (the Mac's ``current`` link) and server.json say so,
    and the record of installs."""
    service.switch_folder(data, folder)
    service.write_config(data, folder=str(folder))
    paths.record_install(paths.install_identity(folder))


def _version(folder: Path) -> str:
    return paths.project_version(folder) or "the previous version"


# --- server-update <bundle.zip> ---------------------------------------------------------------------------------------


def update(bundle: Path, data: Path, db: Path, *, server: Server | None = None, dry_run: bool = False,
           force: bool = False, sha256: str | None = None, hash_check: bool = True, task_name: str | None = None,
           say: Say = print) -> int:  # fmt: skip
    """Install ``bundle``: checked (its SHA-256 against ``sha256``, given apart) and unzipped from a copy in a private
    temporary folder, removed at the end."""
    bundle = Path(bundle)
    if not bundle.is_file():
        print(f"{bundle} is not a ZIP file: give the offline bundle (APProcessor-offline-<version>.zip).",
              file=sys.stderr)  # fmt: skip
        return 1
    with tempfile.TemporaryDirectory(prefix="ap_coder-update-") as folder:  # only this account may open it
        try:
            copy = private_copy(bundle, Path(folder))
        except OSError as exc:
            print(f"{bundle} could not be copied to check it ({exc}): nothing was changed.", file=sys.stderr)
            return 1
        refused = hash_refusal(copy, sha256, hash_check, say)
        if refused:
            print(refused, file=sys.stderr)
            return 1
        return _update(copy, data, db, server=server, dry_run=dry_run, force=force, say=say, task_name=task_name,
                       checked="sha256" if hash_check else "not checked (--no-hash-check)")  # fmt: skip


def _update(bundle: Path, data: Path, db: Path, *, server: Server | None, dry_run: bool, force: bool,
            say: Say, checked: str = "sha256", task_name: str | None = None) -> int:  # fmt: skip
    def refuse(message: str) -> int:
        print(message, file=sys.stderr)
        return 1

    try:
        s, saved = server_settings(data, task_name), service.read_config(data)
    except service.SettingsError as exc:
        return refuse(str(exc))
    server = server or Server(data, s, say)
    with recovery.update_lock(data) as lock:
        if lock is None:
            return refuse(f"Another server-update runs on this data folder now ({recovery.service_folder(data)}): "
                          "wait for it to end. Nothing was changed.")  # fmt: skip
        cut = recovery.read(data)
        if cut:  # left by an update cut short (a power cut): dealt with first, and only that this time
            return recovery.recover(data, db, server, cut, dry_run=dry_run, say=say)
        try:
            info = check_bundle(Path(bundle))
        except BundleError as exc:
            return refuse(str(exc))
        if wrong_folder(s):
            return refuse(wrong_folder(s))
        current = s.code_folder
        theirs, mine = info["identity"], paths.install_identity(current)
        new_version, old_version = theirs["version"], mine.get("version") or "?"
        verdict = paths.compare(theirs, mine)
        if not force and verdict != "newer":
            said = {"same": f"{bundle.name} is the AP Coder the service runs already ({paths.describe(mine)}).",
                    "older": f"{bundle.name} holds an older AP Coder ({paths.describe(theirs)}) than the one the "
                             f"service runs ({paths.describe(mine)}).",
                    "unknown": f"{bundle.name} is AP Coder {new_version}, like the one the service runs, and which "
                               "build is newer can't be told: --force installs it anyway."}[verdict]  # fmt: skip
            return refuse(f"{said} Nothing was changed. To go back to the previous version: server-update --back")
        new = target_folder(current, new_version)
        installed = server.installed()
        elsewhere = "" if installed else another_task(server, s, saved)
        if elsewhere:
            return refuse(elsewhere)
        plan = {"from": old_version, "to": new_version, "from_folder": str(current), "to_folder": str(new),
                "label": f"before-update-{new_version}", "database": str(db), "started": recovery.started_now(),
                "pid": os.getpid(), "safety_copy": "", "bundle_hash": checked}  # fmt: skip
        if dry_run:
            _dry_run(bundle, db, s, plan, installed, paths.describe(mine), paths.describe(theirs), say)
            return 0
        say(f"Updating AP Coder from {paths.describe(mine)} to {paths.describe(theirs)}.")
        return _install(Path(bundle), info["top"], data, db, server, installed, plan, say)


def _dry_run(bundle: Path, db: Path, s: service.ServerSettings, plan: dict, installed: bool, mine: str, theirs: str,
             say: Say) -> None:  # fmt: skip
    new, current = Path(plan["to_folder"]), Path(plan["from_folder"])
    say(f"Updating AP Coder on this server from {mine} to {theirs}:")
    say(f"While AP Coder {plan['from']} keeps serving:")
    steps = [
        f"Check the bundle: {bundle} (one AP Coder folder, every file it lists, no damage): done, it is fine",
        f"Unzip it next to the current folder: {new}",
        f"Set up the new folder from its wheelhouse (offline): {new / 'scripts' / 'launch.py'} --no-start --yes",
        f"Update a copy of the database with the new version, and time it ({recovery.check_folder(db.parent)}, "
        "removed after)",
        f"Self-check: {new / 'scripts' / 'pilot_check.py'} (the sample invoices read right)",
        f"Copy the database {db} ahead for the backup, as it is (what is written meanwhile goes to its log)",
    ]
    switch = [
        f'Stop the service: the task "{s.task_name}" (python -m ap_coder service stop)'
        if installed
        else "The service is not installed on this computer: nothing to stop",
        f"Back up the database and the invoice files to backups\\ap_coder-<date>-{plan['label']}.db (the copy made "
        "ahead, brought up to date with what was written since)",
        "Update the database (the new version opens it once: its migrations)",
        f"Start the service from {new} and wait for the dashboard to answer (the downtime: stop to answer)"
        if installed
        else f"Switch to {new}; no service to start (python -m ap_coder service install, from the new folder)",
    ]
    for n, step in enumerate(steps, 1):
        say(f"  {n}. {step}")
    say("Then, the service stopped:")
    for n, step in enumerate(switch, len(steps) + 1):
        say(f"  {n}. {step}")
    again = "the service started again from" if installed else "the service switched back to"
    say(f"If 2 to 6 fail, nothing was stopped or changed. If 7 to 10 fail: the backup is put back and {again} "
        f"{current}.")  # fmt: skip
    say(f"The current folder {current} is kept: server-update --back goes back to it.")
    say("Nothing was changed (dry run).")


def _seconds(start: float) -> float:
    return round(time.monotonic() - start, 1)


def _install(bundle: Path, top: str, data: Path, db: Path, server: Server, installed: bool, plan: dict,
             say: Say) -> int:  # fmt: skip
    """The update itself: prepared while the old version serves, then switched over with the service stopped. Each
    step is in the marker first (``update_recovery``), so a power cut at any point is dealt with at the next start."""
    new, old_version, new_version = Path(plan["to_folder"]), plan["from"], plan["to"]
    times: dict[str, float] = {}
    began = time.monotonic()

    def step(name: str) -> None:
        plan["step"] = name
        recovery.write(data, plan)

    def not_updated(reason: str) -> int:
        """A step before the stop failed: the old version kept serving, its database untouched."""
        say(f"\n{reason}\nNothing was stopped or changed: AP Coder {old_version} kept serving.")
        recovery.remove_check_copy(data)
        _record(data, {"from": old_version, "to": new_version, "result": f"not updated: {reason}", "backup": "",
                       "bundle_hash": plan["bundle_hash"], "times": times})  # fmt: skip
        recovery.clear(data)
        if new.exists():
            say(f"The new folder {new} is kept to look at; delete it when done.")
        return 1

    # --- Prepared while the old version serves ----------------------------------------------------------------------
    try:
        step("prepare")
        if db.exists():
            from ..core.store.backups import _room_refusal

            short = _room_refusal(db, data / "backups")
            if short:
                return not_updated(f"The update needs room for a copy of the database: {short}.")
        say(f"Unzipping to {new}...")
        try:
            unzip(bundle, top, new)
        except (OSError, zipfile.BadZipFile) as exc:
            return not_updated(f"The bundle could not be unzipped ({exc}).")
        say("Setting up the new folder (its packages come from its wheelhouse; a few minutes)...")
        if not server.set_up(new):
            return not_updated("The new folder could not be set up: its launcher's window says why (and "
                               "~/.ap_coder/logs).")  # fmt: skip
        if db.exists():
            say("Updating a copy of the database with the new version (the database itself is not touched)...")
            started = time.monotonic()
            copy = copy_database(db, recovery.check_folder(data) / db.name)
            ok = server.migrate(new, copy)
            times["check_migration_s"] = _seconds(started)
            recovery.remove_check_copy(data)
            if not ok:
                return not_updated("The new version could not update a copy of the database: the database itself "
                                   "was not touched.")  # fmt: skip
            say(f"The new version updated the copy in {times['check_migration_s']:g} s.")
        started = time.monotonic()
        ok, result = server.self_check(new)
        times["self_check_s"] = _seconds(started)
        say(result)
        if not ok:
            return not_updated(f"The self-check failed: {result}")
        ahead = None
        if db.exists():
            say("Copying the database ahead for the backup (it is brought up to date once the service is stopped)...")
            started = time.monotonic()
            ahead = copy_ahead(db, plan["label"], say)
            times["copy_ahead_s"] = _seconds(started)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - nothing stopped yet: said, the old one serves on
        why = "interrupted (Ctrl+C)" if isinstance(exc, KeyboardInterrupt) else f"stopped on an error: {exc}"
        return not_updated(f"The update was {why}.")
    times["prepare_s"] = _seconds(began)
    say(f"Prepared in {times['prepare_s']:g} s while AP Coder {old_version} kept serving.")
    try:
        return _switch_over(data, db, server, installed, plan, times, step, say, ahead)
    finally:
        if ahead is not None:
            ahead.close()  # (once the safety copy is made, or the update went back before it)


def _switch_over(data: Path, db: Path, server: Server, installed: bool, plan: dict, times: dict[str, float],
                 step: Callable[[str], None], say: Say, ahead: CopyAhead | None = None) -> int:  # fmt: skip
    """Stop, safety copy (``ahead``, made while the old version served, brought up to date), the database updated,
    switched and started: the downtime, from the stop to the answer."""
    current, new = Path(plan["from_folder"]), Path(plan["to_folder"])
    old_version, new_version, label = plan["from"], plan["to"], plan["label"]
    backup: Path | None = None
    started = False
    down = time.monotonic()

    def back(reason: str) -> int:
        with contextlib.suppress(OSError):  # (a full disk: the going back goes on all the same)
            step("going-back")
        say(f"\n{reason}\nGoing back to AP Coder {old_version}...")
        if ahead is not None:
            ahead.close()  # (the database let go before the old version starts on it)
        if started:
            server.stop()
        if backup is not None:
            try:  # whatever stops it, the old version is started again below: never a server left stopped (B-1565)
                files = put_back(backup, db)
                say(f"The database is back as it was before the update ({backup.name}); the user accounts are kept "
                    f"as they are, and everyone logs in again. The files: {files.words()}.")  # fmt: skip
            except Exception as exc:  # noqa: BLE001 - see above
                say(f"The backup made before the update ({backup.name}) could not all be put back ({exc}): once the "
                    f"old version runs, restore it from Settings → Data & backups.")  # fmt: skip
        switch_to(data, current)
        _record(data, {"from": old_version, "to": new_version, "result": f"rolled back: {reason}",
                       "backup": str(backup or ""), "bundle_hash": plan["bundle_hash"], "times": times})  # fmt: skip
        recovery.clear(data)
        if installed:
            server.start()
            if not server.up(current):
                say("The old version does not answer either: see the log, python -m ap_coder service status.")
        if backup is not None:  # (B-2037)
            _read_in_sha(backup)
        kept = f" The new folder {new} is kept to look at; delete it when done." if new.exists() else ""
        say(f"AP Coder went back to {old_version}.{kept}")
        return 1

    # Whatever stops it once the service is stopped (the disk full for the backup, the new folder's Python blocked,
    # Ctrl+C) puts the old version back and starts it again: never a server left stopped.
    try:
        step("stop")
        if installed:
            say("Stopping the service...")
            if not server.stop():
                return back("The service did not stop.")
        else:
            say("The service is not installed on this computer: nothing to stop.")
        times["stop_s"] = _seconds(down)
        if db.exists():
            step("safety-copy")
            mark = time.monotonic()
            backup = safety_copy(db, label, ahead, hashed=False)  # (its SHA-256 read once it serves: below)
            plan["safety_copy"] = str(backup)
            times["safety_copy_s"] = _seconds(mark)
            if ahead is not None and not ahead.caught_up:
                say("The copy of the database made ahead could not be brought up to date (its file was written "
                    "meanwhile): it was copied whole instead.")  # fmt: skip
            say(f"Database backed up: {backup}")
        step("migrate")
        say("Updating the database...")
        mark = time.monotonic()
        if not server.migrate(new, db):
            return back("The new version could not update the database.")
        times["migrate_s"] = _seconds(mark)
        previous = service.read_config(data).get("previous_folder") or ""
        step("switch")
        switch_to(data, new)
        step("start")
        if installed:
            say("Starting the service...")
            mark = time.monotonic()
            started = server.start()
            if not started or not server.up(new):
                return back("The new version does not answer (python -m ap_coder service status; the log).")
            times["start_s"] = _seconds(mark)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - see above
        if isinstance(exc, KeyboardInterrupt):
            return back("The update was interrupted (Ctrl+C).")
        return back(f"The update stopped on an error: {exc or type(exc).__name__}.")
    times["downtime_s"] = _seconds(down)
    entry = {"from": old_version, "to": new_version, "result": "updated", "backup": str(backup or ""),
             "bundle_hash": plan["bundle_hash"], "times": times}  # fmt: skip
    try:
        _record(data, entry, previous_folder=str(current), previous_version=old_version,
                pre_update_backup=str(backup or ""))  # fmt: skip
    except OSError as exc:  # the marker is kept (step start): the next server-update or service start records it
        say(f"\nAP Coder {new_version} runs from {new}, but the update's record in {service.config_path(data)} could "
            f"not be written ({exc}). server-update --back needs it: run server-update again (it records it, and "
            "restarts the service once), or the service's next start records it.")  # fmt: skip
        return 1
    recovery.clear(data)
    parts = [("stopping", "stop_s"), ("safety copy", "safety_copy_s"), ("database update", "migrate_s"),
             ("starting", "start_s")]  # fmt: skip
    said = ", ".join(f"{what} {times[key]:g} s" for what, key in parts if key in times)
    stopped = "Downtime" if installed else "The switch took"
    say(f"\nUpdated AP Coder from {old_version} to {new_version}: {new}.")
    say(f"{stopped}: {times['downtime_s']:g} s ({said}); prepared beforehand in {times['prepare_s']:g} s.")
    if backup is not None:
        _read_in_sha(backup)
        copied = server.second_copy(new, db, backup)
        if copied:
            say(copied)
    if not installed:
        say("Start it: python -m ap_coder service install (or start), from the new folder.")
    say(f"The previous version stays in {current}: python -m ap_coder server-update --back goes back to it.")
    if previous and not paths.same_folder(previous, current) and Path(previous).exists():
        from .service_windows import task_folder

        if paths.same_folder(task_folder(data), previous):  # (a task an older AP Coder registered, B-1961)
            say(f"{previous} (two versions back) is no longer needed, but the service's scheduled task starts in it "
                f"(an older AP Coder installed it so): Task Scheduler could not start it once it is gone. First run "
                f"service install again from {new}, with the --account, --task-name and --folder it was given "
                "(Windows asks for the account's password), then delete it.")  # fmt: skip
        else:
            say(f"{previous} (two versions back) is no longer needed: delete it to free its space.")
    return 0


# --- server-update --back ---------------------------------------------------------------------------------------------


def _read_in_sha(backup: Path) -> None:
    """The safety copy's SHA-256 read into its list once a version serves again (``safety_copy(hashed=False)``): else
    the list has the size only, which ``check`` compares, and an older version's check calls it damaged (B-2037)."""
    from ..core.store import backup_files

    with contextlib.suppress(OSError):
        backup_files.hash_database(backup)


def go_back(data: Path, db: Path, *, server: Server | None = None, dry_run: bool = False, yes: bool = False,
            task_name: str | None = None, say: Say = print) -> int:  # fmt: skip
    """``_go_back``, never while an update runs; an update cut short is dealt with first, and only that this time."""
    with recovery.update_lock(data) as lock:
        if lock is None:
            print("A server-update runs on this data folder now: wait for it to end. Nothing was changed.",
                  file=sys.stderr)  # fmt: skip
            return 1
        cut = recovery.read(data)
        if not cut:
            return _go_back(data, db, server=server, dry_run=dry_run, yes=yes, task_name=task_name, say=say)
        try:
            server = server or Server(data, server_settings(data, task_name), say)
        except service.SettingsError as exc:
            print(exc, file=sys.stderr)
            return 1
        return recovery.recover(data, db, server, cut, dry_run=dry_run, say=say)


def _go_back(data: Path, db: Path, *, server: Server | None, dry_run: bool, yes: bool, task_name: str | None,
             say: Say) -> int:  # fmt: skip
    try:
        saved, s = service.read_config(data), server_settings(data, task_name)
    except service.SettingsError as exc:
        print(exc, file=sys.stderr)
        return 1
    previous, backup = Path(saved.get("previous_folder") or "."), Path(saved.get("pre_update_backup") or ".")
    if not saved.get("previous_folder") or not saved.get("pre_update_backup"):
        print("Nothing to go back to: no update was made with server-update since the last --back.", file=sys.stderr)
        return 1
    if wrong_folder(s) or outside_install(previous):
        print(wrong_folder(s) or outside_install(previous), file=sys.stderr)
        return 1
    if not _upgrade().is_install(previous):
        print(f"The previous version's folder {previous} is gone (or not an AP Coder folder): AP Coder can't go back "
              "to it. Install that version's offline bundle instead.", file=sys.stderr)  # fmt: skip
        return 1
    if not backup.is_file():
        print(f"The backup made before the update, {backup}, is gone: AP Coder can't go back without it (the older "
              "version can't open the updated database).", file=sys.stderr)  # fmt: skip
        return 1
    server = server or Server(data, s, say)
    installed = server.installed()
    elsewhere = "" if installed else another_task(server, s, saved)
    if elsewhere:
        print(elsewhere, file=sys.stderr)
        return 1
    current = s.code_folder
    # The update the backup was made for: the last one that went through, not a later attempt rolled back.
    updates = [h for h in saved.get("history") or [] if isinstance(h, dict)
               and str(h.get("result") or "").startswith("updated")]  # fmt: skip
    since = updates[-1].get("at", "the update") if updates else "the update"
    say(f"Going back from AP Coder {_version(current)} ({current}) to {_version(previous)} ({previous}).")
    say(f"The database goes back to the backup made before the update ({backup.name}): what was done in AP Coder "
        f"since {since} is not in it. A backup of the database and the files as they are now is kept in backups. "
        "The invoice files go back as they were then; the ones added since are moved aside. The user accounts, "
        "their passwords and roles, and paused logins stay as they are now; everyone is logged out and logs in "
        "again.")  # fmt: skip
    if dry_run:
        say(f'  1. Stop the service "{s.task_name}"' if installed
            else "  1. The service is not installed on this computer: nothing to stop")  # fmt: skip
        say("  2. Back up the database and the files as they are now: backups\\ap_coder-<date>-before-going-back.db")
        say(f"  3. Put back {backup} and its files (the user accounts kept as they are; everyone logged out; files "
            "added since moved to backups\\set-aside-<date>)")  # fmt: skip
        say(f"  4. Start the service from {previous} and wait for the dashboard to answer" if installed
            else f"  4. Switch to {previous}; no service to start")  # fmt: skip
        say("Nothing was changed (dry run).")
        return 0
    if not yes:
        try:
            answer = input("Type yes to go back: ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            say("Nothing was changed.")
            return 1
    if installed and not server.stop():
        print("The service did not stop: nothing was changed.", file=sys.stderr)
        return 1

    def went_back() -> None:
        switch_to(data, previous)
        _record(
            data,
            {"from": _version(current), "to": _version(previous), "result": "went back", "backup": str(backup)},
            previous_folder="",
            pre_update_backup="",
            went_back_from=str(current),
        )

    # Whatever stops it once the service is stopped (the disk full for the safety copy, a backup that can't be read,
    # Ctrl+C) still ends with a version running, as B-1474 does for an update: the one that ran, on the database as
    # it was, until the backup is put back; the previous one after.
    kept, restored, stopped = None, False, ""
    try:
        if db.exists():
            kept = safety_copy(db, "before-going-back")
            say(f"The database and the files as they were are kept in {kept}")
        files = put_back(backup, db)
        restored = True
        say(f"The files: {files.words()}.")
        went_back()
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - see above
        stopped = "was interrupted (Ctrl+C)" if isinstance(exc, KeyboardInterrupt) else (
            f"stopped on an error: {exc or type(exc).__name__}")  # fmt: skip
        say(f"\nGoing back {stopped}.")
        if restored:  # the database is the pre-update backup: the previous version is the one that opens it
            went_back()
        elif kept is not None:
            copy_database(kept, db)  # the database as it was, whatever put_back had done of it
            from ..core.store import backup_files

            backup_files.restore_files(kept, db.parent)  # and the files
    running = previous if restored else current
    if installed:
        server.start()
        if not server.up(running):
            say(f"AP Coder {_version(running)} does not answer: python -m ap_coder service status, and the log.")
            return 1
    if not restored:
        on = f"runs {_version(current)} again" if installed else f"stays on {_version(current)}"
        say(f"AP Coder did not go back: it {on} ({current}), on the database as it was. Nothing was changed: fix "
            "what stopped it, then server-update --back again.")  # fmt: skip
        return 1
    say(f"Back on AP Coder {_version(previous)}. The newer folder {current} is kept; delete it when you no longer "
        "need it (or update to a newer bundle).")  # fmt: skip
    return 1 if stopped else 0


# --- The command line -------------------------------------------------------------------------------------------------


def add_parser(sub: Any) -> None:
    p = sub.add_parser("server-update", help="The company server: install a new offline bundle, or go back (--back)")
    p.add_argument("bundle", nargs="?", default=None, help="The offline bundle (APProcessor-offline-<version>.zip)")
    p.add_argument("--back", action="store_true", help="Go back to the previous version and its pre-update backup")
    p.add_argument("--dry-run", action="store_true", help="Show the steps, change nothing")
    p.add_argument("--yes", action="store_true", help="--back: don't ask first")
    p.add_argument("--force", action="store_true", help="Install even when the bundle is not certainly newer")
    p.add_argument("--sha256", default=None, help="The bundle's SHA-256, from its builder by another way than the ZIP")
    p.add_argument("--no-hash-check", action="store_true", help="Don't check the SHA-256 (unsafe; warned and recorded)")
    p.add_argument("--task-name", default=None, help=f'The service\'s scheduled task, as service install was given '
                   f'it (default "{service.TASK_NAME}"; never server.json\'s)')  # fmt: skip


def main(args: argparse.Namespace) -> int:
    data, db = private_dir(), Path(args.db)
    if args.back:
        return go_back(data, db, dry_run=args.dry_run, yes=args.yes, task_name=args.task_name)
    if not args.bundle:
        print("Give the offline bundle (server-update APProcessor-offline-<version>.zip), or --back.", file=sys.stderr)
        return 2
    return update(
        Path(args.bundle),
        data,
        db,
        dry_run=args.dry_run,
        force=args.force,
        sha256=args.sha256,
        hash_check=not args.no_hash_check,
        task_name=args.task_name,
    )
