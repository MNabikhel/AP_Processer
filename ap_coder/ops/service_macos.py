"""The company server's service on a Mac (D-186): a LaunchDaemon, and the launchctl commands of ``sudo .venv/bin/python
-m ap_coder service install|remove|start|stop|status``.

How it runs, with the standard library only (``plistlib``): a **LaunchDaemon**, ``/Library/LaunchDaemons/<label>.plist``
(``com.apcoder.server``; another ``--task-name`` gives another label), which launchd starts at boot under the
service's own account (``_apcoder``; never root unless ``--allow-root``), whether anyone is logged in or not. It runs
``<the AP Coder folder's parent>/current/.venv/bin/python -m ap_coder service run``: the *service host* of
``service.py``, which starts the dashboard and starts it again when it stops, as on Windows. ``current`` is a link to
the AP Coder folder that runs; ``server-update`` points it at the new folder in one step (a new link renamed over the
old one) while the service is stopped, so the plist never changes with a version.

launchd's ``KeepAlive`` is ``{SuccessfulExit: false}``: a host that crashed or was killed is started again (at most
every ``ThrottleInterval``, 10 s), one that ended with exit code 0 is not. The host ends with 0 only when the service
was stopped on purpose: the ``stopped`` file, or SIGTERM (``launchctl bootout``), on which it stops its dashboard first.
``service stop`` leaves the ``stopped`` file, asks the dashboard to stop and boots the daemon out: it stays stopped,
also after a restart of the Mac (launchd loads the plist at boot, and the host ends at once). ``service start`` removes
the file and loads (or restarts) the daemon. What Python says before the host's own log is open (a Python that won't
start) is in ``<data folder>/logs/launchd.out.log`` and ``launchd.err.log``.

The plist is root's (in /Library/LaunchDaemons, which only an administrator can change), so the other service commands
find the data folder in it when ``AP_PRIVATE_DIR`` is not given (sudo leaves the environment out). What the platforms
share is ``service.py``'s, always reached as ``service.<name>`` here.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import plistlib
import re
import shlex
import stat
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core import paths
from . import service

if TYPE_CHECKING:
    from .service import Say, ServerSettings

NAME = "macos"
DAEMONS = Path("/Library/LaunchDaemons")
DEFAULT_LABEL = "com.apcoder.server"  # the default task name's ("AP Coder")
DEFAULT_ACCOUNT = "_apcoder"  # a hidden account made for AP Coder only (docs/SERVER.md, the Mac)
CURRENT = "current"  # the link beside the AP Coder folders to the one that runs
THROTTLE = 10  # seconds launchd waits at least between two starts
EXIT_TIMEOUT = 60  # seconds launchd gives the host after SIGTERM before SIGKILL: more than service.STOP_WAIT
OPEN_FILES = 8192  # launchd's own default, 256, is too few for the web workers' connections
AGAIN = "sudo .venv/bin/python -m ap_coder service start"
SUDO = f"sudo AP_PRIVATE_DIR=<the data folder> .venv/bin/python -m ap_coder service install --account {DEFAULT_ACCOUNT}"


# --- The daemon's names and its plist --------------------------------------------------------------------------------


def label(task_name: str) -> str:
    """The daemon's label: ``com.apcoder.server`` for the default task name; a name like a label (``com.example.ap``)
    as it is; any other made into one (``AP Coder test``: ``com.apcoder.ap-coder-test``). Letters, digits, dots and
    hyphens only: it names the plist's file."""
    if task_name.strip() == service.TASK_NAME:
        return DEFAULT_LABEL
    name = re.sub(r"\.{2,}", ".", re.sub(r"[^A-Za-z0-9.-]+", "-", task_name.strip())).strip(".-")
    if not name:
        return DEFAULT_LABEL
    return name if "." in name else f"com.apcoder.{name.lower()}"


def plist_path(task_name: str) -> Path:
    return DAEMONS / f"{label(task_name)}.plist"


def target(task_name: str) -> str:
    """The daemon as launchctl names it: ``system/<label>``."""
    return f"system/{label(task_name)}"


def current_link(folder: Path) -> Path:
    """The link the daemon runs: ``current``, beside the AP Coder folders (only the administrators can change them)."""
    return Path(folder).parent / CURRENT


def launchd_logs(data: Path) -> tuple[Path, Path]:
    logs = data / service.LOG_DIR
    return logs / "launchd.out.log", logs / "launchd.err.log"


def daemon(s: ServerSettings, data: Path) -> dict[str, Any]:
    """The plist: the service host of the ``current`` link, at boot, as the service's account, started again when it
    crashed or was killed but not when it ended on purpose (exit code 0)."""
    link = current_link(s.code_folder)
    out, err = launchd_logs(data)
    return {
        "Label": label(s.task_name),
        "ProgramArguments": [str(link / ".venv" / "bin" / "python"), "-m", "ap_coder", "service", "run"],
        "WorkingDirectory": str(link),
        "EnvironmentVariables": {"AP_PRIVATE_DIR": str(data), "AP_SERVER_MODE": "1", "PYTHONUNBUFFERED": "1"},
        "UserName": s.account,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": THROTTLE,
        "ExitTimeOut": EXIT_TIMEOUT,
        "SoftResourceLimits": {"NumberOfFiles": OPEN_FILES},
        "StandardOutPath": str(out),
        "StandardErrorPath": str(err),
    }


def read_plist(task_name: str) -> dict:
    """The installed plist ({} when there is none, or it can't be read)."""
    try:
        found = plistlib.loads(plist_path(task_name).read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}
    return found if isinstance(found, dict) else {}


def write_plist(task_name: str, content: dict) -> Path:
    """Written whole beside it, then renamed; root's and 0644, as launchd loads only such a file."""
    path = plist_path(task_name)
    part = path.with_name(f".{path.stem}.{os.getpid()}.part")  # (launchd reads only *.plist)
    part.write_bytes(plistlib.dumps(content))
    os.chmod(part, 0o644)
    if is_root():
        os.chown(part, 0, 0)
    os.replace(part, path)
    return path


def point_current(folder: Path) -> Path:
    """Make ``current`` name ``folder`` in one step: a new link under a temporary name, renamed over the old one
    (``os.replace``, one rename): whatever starts the service meanwhile finds the old folder or the new one, never
    none. The link names the folder by its name: they are side by side."""
    folder = Path(folder)
    link = current_link(folder)
    part = link.with_name(f".{CURRENT}.{os.getpid()}.part")
    part.unlink(missing_ok=True)
    os.symlink(folder.name, part)
    try:
        os.replace(part, link)
    except OSError:
        part.unlink(missing_ok=True)
        raise
    return link


def daemon_data(task_name: str) -> str:
    """The data folder the installed daemon runs with ("" when none is installed)."""
    return str((read_plist(task_name).get("EnvironmentVariables") or {}).get("AP_PRIVATE_DIR") or "").strip()


def data_folder(data: Path, task_name: str) -> Path:
    """The data folder the commands use: ``AP_PRIVATE_DIR`` when given, else the installed daemon's (root's plist),
    else ``data``."""
    if paths.env_path("AP_PRIVATE_DIR"):
        return data
    return Path(daemon_data(task_name) or data)


def command_refusal(args: argparse.Namespace) -> str:
    """Why this service command can't run ("" = it can): all but ``install --dry-run`` change or read root's launchd
    and the service account's files, so they run with sudo."""
    if is_root() or (args.action == "install" and args.dry_run):
        return ""
    return f"Run it with sudo: sudo .venv/bin/python -m ap_coder service {args.action} (docs/SERVER.md, the Mac)."


def switch_folder(data: Path, folder: Path) -> None:
    """The service runs ``folder`` from its next start: the ``current`` link points at it (the plist never changes)."""
    point_current(folder)


# --- This Mac: root, the service's account, its part of the data folder ----------------------------------------------


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def account_ids(account: str) -> tuple[int, int] | None:
    """(uid, gid) of the account (None when this Mac has none of that name)."""
    import pwd

    try:
        found = pwd.getpwnam(account)
    except KeyError:
        return None
    return found.pw_uid, found.pw_gid


def own(path: Path, uid: int, gid: int) -> None:
    """``path`` made the account's, never what a link there points at."""
    os.chown(path, uid, gid, follow_symlinks=False)


def hand_over(data: Path, account: str, uid: int, gid: int) -> str:
    """The parts of the data folder the host writes (``service/``, its ``host.lock``, ``logs/``; the data folder too
    when made now) made the service account's: as root, ``service install`` would otherwise leave them root's, and
    the host could not start. A link there is refused, and a file with other names (a hard link) left as it is: the
    account can write here, and root must not hand it what they point at. Returns a warning ("" = none)."""
    made = not data.exists()
    data.mkdir(parents=True, exist_ok=True)
    folders = [service.service_dir(data), data / service.LOG_DIR]
    for folder in folders:
        if folder.is_symlink():
            raise OSError(f"{folder} is a link: AP Coder won't hand it to {account}. Remove it, then run this again.")
        folder.mkdir(exist_ok=True)
    for folder in [data, *folders] if made else folders:
        own(folder, uid, gid)
    lock = service.host_lock(data)
    if not lock.exists():
        lock.touch()
    found = lock.lstat()
    if stat.S_ISREG(found.st_mode) and found.st_nlink == 1:
        own(lock, uid, gid)
    if not made and data.stat().st_uid != uid:
        return (f"The data folder {data} is not {account}'s: the service must be able to write in it (the database, "
                f"the invoices). If it can't: sudo chown -R {account} {shlex.quote(str(data))}")  # fmt: skip
    return ""


# --- launchctl --------------------------------------------------------------------------------------------------------


def launchctl(*args: str) -> Any:
    return service.run_command(["launchctl", *args], capture_output=True)


def parse_print(text: str) -> dict[str, str]:
    """The top level of ``launchctl print system/<label>``'s answer ({"state": "running", "pid": "412", ...}), not
    what its blocks hold (``arguments = {...}``)."""
    found: dict[str, str] = {}
    depth = 0
    for line in (text or "").splitlines():
        item = line.strip()
        if item == "}":
            depth -= 1
            continue
        if depth == 1 and " = " in item:
            key, _, value = item.partition(" = ")
            found.setdefault(key.strip(), value.rstrip("{").strip())
        if item.endswith("{"):
            depth += 1
    return found


def loaded(task_name: str) -> dict[str, str] | None:
    """What launchd says of the daemon (None when it has not loaded it)."""
    done = launchctl("print", target(task_name))
    return parse_print(done.stdout) if done.returncode == 0 else None


def _said(done: Any) -> str:
    return (done.stderr or done.stdout or f"exit code {done.returncode}").strip()


# --- install / remove / start / stop / status -------------------------------------------------------------------------


def start(data: Path, s: ServerSettings, say: Say = print) -> int:
    plist = plist_path(s.task_name)
    if not plist.is_file():
        say(f"The LaunchDaemon {label(s.task_name)} is not installed: {SUDO}")
        return 1
    service.stop_flag(data).unlink(missing_ok=True)
    launchctl("enable", target(s.task_name))  # (one turned off with launchctl disable would not load)
    if loaded(s.task_name) is None:
        done = launchctl("bootstrap", "system", str(plist))  # RunAtLoad: it starts now
    else:
        done = launchctl("kickstart", "-k", target(s.task_name))  # started again (ended first when it runs)
    if done.returncode:
        say(f"The LaunchDaemon {label(s.task_name)} could not be started: {_said(done)}")
        return 1
    say(f"Started. Staff open {service.address(s)}  (log: {service.log_path(data)})")
    return 0


def stop(data: Path, s: ServerSettings, say: Say = print) -> int:
    """Stop the dashboard and its host, and keep launchd from starting them again until ``service start``: the
    ``stopped`` file (the host started at boot ends at once), the dashboard asked to stop, the daemon booted out
    (launchd's SIGTERM: the host ends its dashboard, then itself, with exit code 0)."""
    service.write_stop_flag(data, AGAIN)
    stopped = service.dashboard_stopped(data, s)
    launchctl("bootout", target(s.task_name))  # (an error when it was not loaded: nothing to stop)
    deadline = time.monotonic() + EXIT_TIMEOUT
    while service.host_running(data) and time.monotonic() < deadline:
        time.sleep(0.5)
    if service.host_running(data) or not (stopped or service.dashboard_health(data, s, timeout=2.0) is None):
        say(f"The service did not stop: see {service.log_path(data)}, then run this again (or restart the Mac).")
        return 1
    say(f"Stopped. It stays stopped, also after a restart of the Mac, until: {AGAIN}")
    return 0


def refusal(data: Path, s: ServerSettings, args: argparse.Namespace) -> str:
    """Why the daemon can't be installed so ("" = it can)."""
    if not is_root():
        return ("Run it with sudo: the LaunchDaemon is written in /Library/LaunchDaemons and loaded into the Mac's own "
                f"launchd. {SUDO}")  # fmt: skip
    if not paths.env_path("AP_PRIVATE_DIR") and not daemon_data(s.task_name):
        return (f"Give the data folder (outside the AP Coder folder, which an update replaces): {SUDO}. It is kept in "
                "the LaunchDaemon: the other service commands find it there.")  # fmt: skip
    ids = account_ids(s.account)
    if ids is None:
        return (f"This Mac has no account {s.account}: make it first (docs/SERVER.md, the Mac), or name another "
                "with --account.")  # fmt: skip
    if ids[0] == 0 and not getattr(args, "allow_root", False):
        return (f"AP Coder would run as root ({s.account}): every invoice and email it reads would be handled with "
                "the Mac's every right. Make its own account (docs/SERVER.md, the Mac); for a first test only, add "
                "--allow-root.")  # fmt: skip
    python = s.code_folder / ".venv" / "bin" / "python"
    if not python.exists():
        return f"{python} is not there: set {s.code_folder} up first (docs/SERVER.md, the Mac)."
    link = current_link(s.code_folder)
    if link.exists() and not link.is_symlink():
        return f"{link} is a folder or a file of its own, not AP Coder's link: move it away, then run this again."
    return ""


def install(data: Path, s: ServerSettings, args: argparse.Namespace, say: Say = print) -> int:
    folder, name, plist = s.code_folder, label(s.task_name), plist_path(s.task_name)
    content = daemon(s, data)
    if args.dry_run:
        say(f"1. Save the settings in {service.config_path(data)}:")
        say(json.dumps({**dataclasses.asdict(s), "allowed_hosts": list(s.allowed_hosts),
                        "allowed_folders": list(s.allowed_folders), "folder": str(folder)}, indent=2))  # fmt: skip
        say(f"2. Make {service.service_dir(data)} and {data / service.LOG_DIR} the account {s.account}'s")
        say(f"3. Point {current_link(folder)} at {folder}")
        say(f"4. Write the LaunchDaemon {plist}:")
        say(plistlib.dumps(content).decode("utf-8"))
        if not args.no_start:
            say(f"5. Start it: {shlex.join(['launchctl', 'bootstrap', 'system', str(plist)])}")
        say("Nothing was changed (dry run).")
        return 0
    refused = refusal(data, s, args)
    if refused:
        say(refused)
        return 1
    uid, gid = account_ids(s.account) or (0, 0)
    if service.host_running(data):  # installed again while running: the new settings apply from the restart
        stop(data, s, say)
    elif loaded(s.task_name) is not None:
        launchctl("bootout", target(s.task_name))  # launchd keeps the plist it loaded: the new one is loaded below
    service.save_settings(data, s)
    try:
        warning = hand_over(data, s.account, uid, gid)
    except OSError as exc:
        say(str(exc))
        return 1
    point_current(folder)
    write_plist(s.task_name, content)
    say(f"Installed the LaunchDaemon {name} ({plist}): it starts at boot as {s.account}, from {current_link(folder)}.")
    if warning:
        say(warning)
    code = 0 if args.no_start else start(data, s, say)
    say("\nNext steps (docs/SERVER.md, the Mac):")
    say(f"  - the firewall, when it is on: allow incoming connections to Python (port {s.port})")
    if not s.allowed_hosts:
        say("  - the name staff type: --allowed-hosts apcoder.company.local (and that DNS alias)")
    say("  - the first admin: sudo AP_PRIVATE_DIR=<the data folder> .venv/bin/python -m ap_coder users add --admin "
        "<username>")  # fmt: skip
    say(f"  - check it: sudo .venv/bin/python -m ap_coder service status   (log: {service.log_path(data)})")
    return code


def remove(data: Path, s: ServerSettings, say: Say = print) -> int:
    plist = plist_path(s.task_name)
    if not plist.is_file() and loaded(s.task_name) is None:
        say(f"The LaunchDaemon {label(s.task_name)} is not installed: nothing to remove.")
        return 1
    stop(data, s, lambda m: None)
    plist.unlink(missing_ok=True)
    service.stop_flag(data).unlink(missing_ok=True)
    say(f"Removed the LaunchDaemon {label(s.task_name)}. The data folder, {service.config_path(data).name}, the logs "
        f"and the {CURRENT} link are kept.")  # fmt: skip
    return 0


def installed(s: ServerSettings) -> bool:
    """Its plist is there (``server-update`` stops and starts it only then)."""
    return plist_path(s.task_name).is_file()


def state(data: Path, s: ServerSettings) -> dict:
    """The daemon as ``service status`` says it: ``line`` (installed) or ``missing`` (not), and what --json gives."""
    name, plist = label(s.task_name), plist_path(s.task_name)
    found = loaded(s.task_name)
    account = str(read_plist(s.task_name).get("UserName") or s.account)
    pid = (found or {}).get("pid", "")
    if found is None:
        said = "not loaded (it starts with the Mac, or now with service start)"
    else:
        said = found.get("state", "?") + (f" (process {pid})" if pid.isdigit() else "")
    return {"installed": plist.is_file() or found is not None, "label": name, "plist": str(plist),
            "loaded": found is not None, "state": found.get("state", "?") if found else "not loaded",
            "launchd_pid": int(pid) if pid.isdigit() else None, "last_exit_code": (found or {}).get("last exit code"),
            "account": account, "line": f"LaunchDaemon {name}: {said}, runs as {account}",
            "missing": f"The LaunchDaemon {name} is not installed: {SUDO}"}  # fmt: skip


def doctor_check(data: Path, s: ServerSettings) -> tuple[str, str, str]:
    """The doctor's line about the daemon (it reads the plist and asks launchctl, changing nothing)."""
    try:
        found = state(data, s)
    except OSError as exc:
        return ("server service", "WARN", f"launchctl could not be asked ({exc})")
    if not found["installed"]:
        return ("server service", "WARN", f"the LaunchDaemon {found['label']} is not installed: {SUDO}")
    stopped = " (stopped on purpose: service start)" if service.stop_flag(data).exists() else ""
    return ("server service", "PASS", f"LaunchDaemon {found['label']}: {found['state']}, runs as "
            f"{found['account']}{stopped}")  # fmt: skip
