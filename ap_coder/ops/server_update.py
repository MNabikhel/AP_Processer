"""Updating AP Coder on the company server (D-100): ``python -m ap_coder server-update <bundle.zip>``, and ``--back``.

The steps, in order (``--dry-run`` prints them with the folders and files, and changes nothing):

1. **Check the bundle**, from a copy in a private temporary folder (the file given may be changed once checked; the
   copy is what is unzipped): a ZIP holding one AP Coder folder (no path out of it), every file the manifest lists, CRCs
   that match, and a newer AP Coder than the one the service runs (``paths.compare``, as the laptop updater judges).
2. **Stop the service** (``service stop``).
3. **Back up the database**: ``backups/ap_coder-<time>-before-update-<version>.db`` (``Store.backup_now``).
4. **Unzip** it next to the current folder, as ``APProcessor-<version>``.
5. **Set up** that folder with its own launcher (``scripts/launch.py --no-start --yes``: its ``.venv`` from its
   ``wheelhouse/``, offline), in server mode, so the launcher keeps the previous folder.
6. **Update the database**: the new version opens it once (``Store``: its migrations).
7. **Self-check**: the new folder's ``scripts/pilot_check.py`` (the sample invoices read right).
8. **Start the service** from the new folder (``run.cmd`` rewritten), and wait for the dashboard to answer.

When 5, 6, 7 or 8 fails, the backup is put back, the service switched back to the old folder and started again. The
previous folder is kept: ``server-update --back`` goes back to it and puts back the pre-update backup (an older
version refuses a database a newer one has changed). Putting a backup back keeps the user accounts and paused logins
as they are, and logs everyone out (``put_back``, D-101). What it does is recorded in server.json (``folder``,
``previous_folder``, ``pre_update_backup``, ``history``) and in the record of installs (``~/.ap_coder/settings.json``),
as the laptop updater does.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from ..core import paths
from ..core.paths import PROJECT_DIR, private_dir
from . import service
from .offline import sha256 as file_sha256

Say = Callable[[str], None]
REQUIRED = ("bundle_manifest.json", "pyproject.toml", "ap_coder/__init__.py", "scripts/launch.py")
HISTORY_KEPT = 20
UP_WAIT = 180.0  # seconds the new dashboard has to answer
CHECK_TIMEOUT = 900  # the self-check: about 10 s, much longer on a busy server's first OCR


# What the new folder's Pythons, run as the administrator, keep of the administrator's environment: what Windows and
# Python need to start, nothing that decides which code runs (no PYTHON*, PIP_*), and never anything the data folder's
# .env said (D-109). They also run with -E -s: no PYTHON* variable, no user site-packages.
KEPT_ENV = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE",
            "HOMEPATH", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
            "PROGRAMW6432", "COMMONPROGRAMFILES", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
            "COMPUTERNAME", "USERNAME", "USERDOMAIN", "LANG", "LC_ALL", "TERM", "AP_PRIVATE_DIR")  # fmt: skip
SAFE_PYTHON = ("-E", "-s")


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


# --- The database: copies that never migrate --------------------------------------------------------------------------


def copy_database(source: Path, dest: Path) -> Path:
    """A consistent copy of ``source`` into ``dest`` (a new file, or the database itself to put a backup back), with
    SQLite's backup: no AP Coder code opens it, so nothing is migrated."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dest, timeout=30)
        try:
            src.backup(target)
        finally:
            target.close()
    finally:
        src.close()
    return dest


def put_back(backup: Path, db: Path) -> Path:
    """``backup`` copied over the database (``copy_database``), keeping the user accounts, their passwords and roles,
    and the paused logins as they are now, and ending every session: a backup must not bring back an account turned
    off, a password changed since, or a session (D-101). A backup older than the accounts gets their tables, so the
    server is never left with no account."""
    from ..core.store.users import carry_accounts, saved_accounts

    saved: dict = {}
    if db.exists():
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        try:
            saved = saved_accounts(conn)
        finally:
            conn.close()
    copy_database(backup, db)
    conn = sqlite3.connect(db, timeout=30)
    try:
        with conn:
            carry_accounts(conn, saved)
    finally:
        conn.close()
    return db


def private_copy(bundle: Path, folder: Path) -> Path:
    """The bundle copied into ``folder`` (a private temporary folder): what is checked is what is unzipped, even
    when the file given (on a share, in a folder others can write to) is changed meanwhile (D-101)."""
    copy = folder / bundle.name
    shutil.copyfile(bundle, copy)
    return copy


def safety_copy(db: Path, label: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = db.parent / "backups" / f"ap_coder-{stamp}-{label}.db"
    copy_database(db, dest)
    conn = sqlite3.connect(dest)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")  # one self-contained file
    finally:
        conn.close()
    return dest


# --- The server: the service, the new folder's setup and checks (stood in for by the tests) ---------------------------


class Server:
    """The Windows service and the steps run in the new folder, for real."""

    def __init__(self, data: Path | None = None, settings: service.ServerSettings | None = None,
                 say: Say = print) -> None:  # fmt: skip
        self.data = data or private_dir()
        self.settings = settings or service.settings(self.data)
        self.say = say

    def installed(self) -> bool:
        if not service.WINDOWS:
            return False
        done = service.run_command(["schtasks", "/query", "/tn", self.settings.task_name], capture_output=True)
        return done.returncode == 0

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
        code = "import sys; from ap_coder.core.store import Store; Store(sys.argv[1])"
        done = subprocess.run(
            [str(service.folder_python(folder)), *SAFE_PYTHON, "-c", code, str(db)],
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
    return (f"server.json says the service runs {s.code_folder}, but this command runs from {here}. Run "
            "server-update from the folder the service runs (cd /d into it, then .venv\\Scripts\\python.exe -m "
            "ap_coder server-update ...). If nobody moved it, server.json was changed by someone: check it, then "
            "install the service again from the right folder (service install --folder). Nothing was "
            "changed.")  # fmt: skip


def outside_install(folder: Path) -> str:
    """Why ``folder`` (server.json's ``previous_folder``) can't be trusted ("" = it can): it must be beside the
    folder this command runs from, inside the code folder only the administrators can change."""
    here = running_folder()
    if paths.same_folder(Path(folder).resolve().parent, here.resolve().parent):
        return ""
    return (f"server.json names {folder} as the previous version, which is not beside this AP Coder folder "
            f"({here.parent}): AP Coder won't run it. If nobody moved it, server.json was changed by someone: "
            "check it. Nothing was changed.")  # fmt: skip


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
    """The service runs ``folder`` from its next start: run.cmd and server.json say so, and the record of installs."""
    service.write_runner(data, folder)
    service.write_config(data, folder=str(folder))
    paths.record_install(paths.install_identity(folder))


def _version(folder: Path) -> str:
    return paths.project_version(folder) or "the previous version"


# --- server-update <bundle.zip> ---------------------------------------------------------------------------------------


def update(bundle: Path, data: Path, db: Path, *, server: Server | None = None, dry_run: bool = False,
           force: bool = False, sha256: str | None = None, hash_check: bool = True,
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
        return _update(copy, data, db, server=server, dry_run=dry_run, force=force, say=say,
                       checked="sha256" if hash_check else "not checked (--no-hash-check)")  # fmt: skip


def _update(bundle: Path, data: Path, db: Path, *, server: Server | None, dry_run: bool, force: bool,
            say: Say, checked: str = "sha256") -> int:  # fmt: skip
    def refuse(message: str) -> int:
        print(message, file=sys.stderr)
        return 1

    try:
        s = service.settings(data)
        info = check_bundle(Path(bundle))
    except (service.SettingsError, BundleError) as exc:
        return refuse(str(exc))
    if wrong_folder(s):
        return refuse(wrong_folder(s))
    current = s.code_folder
    theirs, mine = info["identity"], paths.install_identity(current)
    new_version, old_version = theirs["version"], mine.get("version") or "?"
    verdict = paths.compare(theirs, mine)
    if not force and verdict != "newer":
        said = {"same": f"{bundle.name} is the AP Coder the service runs already ({paths.describe(mine)}).",
                "older": f"{bundle.name} holds an older AP Coder ({paths.describe(theirs)}) than the one the service "
                         f"runs ({paths.describe(mine)}).",
                "unknown": f"{bundle.name} is AP Coder {new_version}, like the one the service runs, and which build "
                           "is newer can't be told: --force installs it anyway."}[verdict]  # fmt: skip
        return refuse(f"{said} Nothing was changed. To go back to the previous version: server-update --back")
    new = target_folder(current, new_version)
    label = f"before-update-{new_version}"
    server = server or Server(data, s, say)
    installed = server.installed()
    if dry_run:
        say(f"Updating AP Coder on this server from {paths.describe(mine)} to {paths.describe(theirs)}:")
        steps = [
            f"Check the bundle: {bundle} (one AP Coder folder, every file it lists, no damage): done, it is fine",
            f'Stop the service: the task "{s.task_name}" (python -m ap_coder service stop)'
            if installed
            else "The service is not installed on this computer: nothing to stop",
            f"Back up the database {db} to backups\\ap_coder-<date>-{label}.db",
            f"Unzip it next to the current folder: {new}",
            f"Set up the new folder from its wheelhouse (offline): {new / 'scripts' / 'launch.py'} --no-start --yes",
            "Update the database (the new version opens it once: its migrations)",
            f"Self-check: {new / 'scripts' / 'pilot_check.py'} (the sample invoices read right)",
            f"Start the service from {new} and wait for the dashboard to answer"
            if installed
            else f"Switch to {new}; no service to start (python -m ap_coder service install, from the new folder)",
        ]
        for n, step in enumerate(steps, 1):
            say(f"  {n}. {step}")
        again = "the service started again from" if installed else "the service switched back to"
        say(f"If 5 to 8 fail: the backup is put back and {again} {current}.")
        say(f"The current folder {current} is kept: server-update --back goes back to it.")
        say("Nothing was changed (dry run).")
        return 0

    say(f"Updating AP Coder from {paths.describe(mine)} to {paths.describe(theirs)}.")
    if installed:
        say("Stopping the service...")
        if not server.stop():
            return refuse("The service did not stop: nothing was changed. Try again, or restart the server.")
    else:
        say("The service is not installed on this computer: nothing to stop.")
    backup = None
    started = False

    def back(reason: str) -> int:
        say(f"\n{reason}\nGoing back to AP Coder {old_version}...")
        if started:
            server.stop()
        if backup is not None:
            put_back(backup, db)
            say(f"The database is back as it was before the update ({backup.name}); the user accounts are kept as "
                "they are, and everyone logs in again.")  # fmt: skip
        switch_to(data, current)
        _record(data, {"from": old_version, "to": new_version, "result": f"rolled back: {reason}",
                       "backup": str(backup or ""), "bundle_hash": checked})  # fmt: skip
        if installed:
            server.start()
            if not server.up(current):
                say("The old version does not answer either: see the log, python -m ap_coder service status.")
        kept = f" The new folder {new} is kept to look at; delete it when done." if new.exists() else ""
        say(f"AP Coder went back to {old_version}.{kept}")
        return 1

    # Whatever stops it once the service is stopped (the disk full for the backup, the new folder's Python blocked,
    # Ctrl+C) puts the old version back and starts it again: never a server left stopped.
    try:
        if db.exists():
            from ..core.store import Store

            backup = Store(db).backup_now(label)
            say(f"Database backed up: {backup}")
        say(f"Unzipping to {new}...")
        try:
            unzip(Path(bundle), info["top"], new)
        except (OSError, zipfile.BadZipFile) as exc:
            return back(f"The bundle could not be unzipped ({exc}).")
        say("Setting up the new folder (its packages come from its wheelhouse; a few minutes)...")
        if not server.set_up(new):
            return back("The new folder could not be set up: its launcher's window says why (and ~/.ap_coder/logs).")
        say("Updating the database...")
        if not server.migrate(new, db):
            return back("The new version could not update the database.")
        ok, result = server.self_check(new)
        say(result)
        if not ok:
            return back(f"The self-check failed: {result}")
        previous = service.read_config(data).get("previous_folder") or ""
        switch_to(data, new)
        if installed:
            say("Starting the service...")
            started = server.start()
            if not started or not server.up(new):
                return back("The new version does not answer (python -m ap_coder service status; the log).")
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - see above
        if isinstance(exc, KeyboardInterrupt):
            return back("The update was interrupted (Ctrl+C).")
        return back(f"The update stopped on an error: {exc or type(exc).__name__}.")
    _record(
        data,
        {
            "from": old_version,
            "to": new_version,
            "result": "updated",
            "backup": str(backup or ""),
            "bundle_hash": checked,
        },
        previous_folder=str(current),
        previous_version=old_version,
        pre_update_backup=str(backup or ""),
    )
    say(f"\nUpdated AP Coder from {old_version} to {new_version}: {new}.")
    if not installed:
        say("Start it: python -m ap_coder service install (or start), from the new folder.")
    say(f"The previous version stays in {current}: python -m ap_coder server-update --back goes back to it.")
    if previous and not paths.same_folder(previous, current) and Path(previous).exists():
        say(f"{previous} (two versions back) is no longer needed: delete it to free its space.")
    return 0


# --- server-update --back ---------------------------------------------------------------------------------------------


def go_back(data: Path, db: Path, *, server: Server | None = None, dry_run: bool = False, yes: bool = False,
            say: Say = print) -> int:  # fmt: skip
    try:
        saved, s = service.read_config(data), service.settings(data)
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
    current = s.code_folder
    # The update the backup was made for: the last one that went through, not a later attempt rolled back.
    updates = [h for h in saved.get("history") or [] if isinstance(h, dict) and h.get("result") == "updated"]
    since = updates[-1].get("at", "the update") if updates else "the update"
    say(f"Going back from AP Coder {_version(current)} ({current}) to {_version(previous)} ({previous}).")
    say(f"The database goes back to the backup made before the update ({backup.name}): what was done in AP Coder "
        f"since {since} is not in it. A copy of the database as it is now is kept in backups. The user accounts, "
        "their passwords and roles, and paused logins stay as they are now; everyone is logged out and logs in "
        "again.")  # fmt: skip
    server = server or Server(data, s, say)
    installed = server.installed()
    if dry_run:
        say(f'  1. Stop the service "{s.task_name}"' if installed
            else "  1. The service is not installed on this computer: nothing to stop")  # fmt: skip
        say("  2. Copy the database as it is now to backups\\ap_coder-<date>-before-going-back.db")
        say(f"  3. Put back {backup} (the user accounts kept as they are; everyone logged out)")
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
            say(f"The database as it was is kept in {kept}")
        put_back(backup, db)
        restored = True
        went_back()
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - see above
        stopped = "was interrupted (Ctrl+C)" if isinstance(exc, KeyboardInterrupt) else (
            f"stopped on an error: {exc or type(exc).__name__}")  # fmt: skip
        say(f"\nGoing back {stopped}.")
        if restored:  # the database is the pre-update backup: the previous version is the one that opens it
            went_back()
        elif kept is not None:
            copy_database(kept, db)  # the database as it was, whatever put_back had done of it
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


def main(args: argparse.Namespace) -> int:
    data, db = private_dir(), Path(args.db)
    if args.back:
        return go_back(data, db, dry_run=args.dry_run, yes=args.yes)
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
    )
