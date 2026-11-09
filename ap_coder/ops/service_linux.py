"""The company server's service on Linux (D-208): a systemd unit, and the systemctl commands of ``sudo .venv/bin/python
-m ap_coder service install|remove|start|stop|status``.

How it runs, with the standard library only: a **systemd unit**, ``/etc/systemd/system/<name>.service``
(``apcoder.service``; another ``--task-name`` gives another name, always starting with ``apcoder``), enabled for
``multi-user.target``, so systemd starts it at boot under the service's own account (``apcoder``, a system account
``service install`` makes when this computer has none: no password, no login shell; never root unless
``--allow-root``), whether anyone is logged in or not. It runs ``<the AP Coder folder's parent>/current/.venv/bin/python
-m ap_coder service run``: the *service host* of ``service.py``, which starts the dashboard and starts it again when it
stops, as on Windows and a Mac. ``current`` is the Mac's link (``service_macos.point_current``): ``server-update``
points it at the new folder in one step while the service is stopped, so the unit never changes with a version.

``Restart=on-failure`` is the Mac's ``KeepAlive {SuccessfulExit: false}``: a host that crashed or was killed (an exit
code but 0, a signal) is started again ``RestartSec`` (10 s) later, one that ended with exit code 0 is not, nor one
systemd stopped itself (``systemctl stop``). The host ends with 0 only when the service was stopped on purpose: the
``stopped`` file, or SIGTERM (systemd's stop), on which it stops its dashboard first. ``KillMode=mixed``: the SIGTERM
goes to the host alone, and what is left of the dashboard once the host has ended is killed (launchd's process group).
``service stop`` leaves the ``stopped`` file, asks the dashboard to stop and stops the unit: it stays stopped, also
after a restart of the computer (the unit stays enabled; the host systemd starts at boot ends at once, with 0).
``service start`` removes the file and restarts the unit. What Python says before the host's own log is open is in
systemd's journal: ``journalctl -u <name>.service``.

The unit is root's (in /etc/systemd/system, which only an administrator can change), so the other service commands
find the data folder in it when ``AP_PRIVATE_DIR`` is not given (sudo leaves the environment out). What a Mac and Linux
share (root, the accounts, the data folder handed to the service's account, the ``current`` link) is
``service_macos``'s; what every platform shares is ``service.py``'s.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import shlex
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core import paths
from . import service, service_macos

if TYPE_CHECKING:
    from .service import Say, ServerSettings

NAME = "linux"
UNITS = Path("/etc/systemd/system")  # the administrator's units (packages' are in /usr/lib/systemd/system)
RUNNING = Path("/run/systemd/system")  # there while systemd runs this computer (sd_booted)
DEFAULT_LABEL = "apcoder"  # the default task name's ("AP Coder"): apcoder.service
DEFAULT_ACCOUNT = "apcoder"  # a system account made for AP Coder only (docs/SERVER.md section 15)
NOLOGIN = "/usr/sbin/nologin"
THROTTLE = 10  # seconds systemd waits before it starts a crashed host again (RestartSec)
EXIT_TIMEOUT = 60  # seconds systemd gives the host after SIGTERM before SIGKILL: more than service.STOP_WAIT
OPEN_FILES = 8192  # systemd's soft limit, 1024, is too few for the web workers' connections
SHOWN = ("LoadState", "ActiveState", "SubState", "MainPID", "NRestarts", "ExecMainStatus", "Result", "UnitFileState")
AGAIN = "sudo .venv/bin/python -m ap_coder service start"
SUDO = "sudo AP_PRIVATE_DIR=<the data folder> .venv/bin/python -m ap_coder service install"


# --- The unit's name and its file -------------------------------------------------------------------------------------


def label(task_name: str) -> str:
    """The unit's name without ``.service``: ``apcoder`` for the default task name; any other made of its letters and
    digits in lower case joined by hyphens, starting with ``apcoder`` (``AP Coder test``: ``apcoder-ap-coder-test``;
    ``apcoder-ci`` as it is), so it never names a unit of the system's (``sshd``: ``apcoder-sshd``) nor leaves
    /etc/systemd/system."""
    name = re.sub(r"[^a-z0-9]+", "-", task_name.strip().lower()).strip("-")
    if task_name.strip() == service.TASK_NAME or not name:
        return DEFAULT_LABEL
    return name if name == DEFAULT_LABEL or name.startswith(f"{DEFAULT_LABEL}-") else f"{DEFAULT_LABEL}-{name}"


def unit_name(task_name: str) -> str:
    return f"{label(task_name)}.service"


def unit_path(task_name: str) -> Path:
    return UNITS / unit_name(task_name)


def quoted(value: str | Path, command: bool = False) -> str:
    """``value`` as one word of a unit file, read back by systemd as it is: in double quotes, its backslashes and quotes
    escaped, a % doubled (a specifier otherwise), and in ExecStart a $ too (a variable otherwise)."""
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return '"' + (text.replace("$", "$$") if command else text) + '"'


def unquoted(value: str) -> str:
    """A value ``quoted`` wrote (outside ExecStart), as it was."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = re.sub(r"\\(.)", r"\1", value[1:-1])
    return value.replace("%%", "%")


def unit_text(s: ServerSettings, data: Path) -> str:
    """The unit: the service host of the ``current`` link, at boot, as the service's account, started again when it
    crashed or was killed but not when it ended on purpose (exit code 0) or was stopped by systemd."""
    link = service_macos.current_link(s.code_folder)
    env = {"AP_PRIVATE_DIR": str(data), "AP_SERVER_MODE": "1", "PYTHONUNBUFFERED": "1"}
    lines = [
        '# AP Coder\'s service, written by "sudo .venv/bin/python -m ap_coder service install" (docs/SERVER.md',
        "# section 15). Change the settings in server.json and run service install again, not here.",
        "[Unit]",
        "Description=AP Coder, the company server's dashboard",
        "After=network-online.target",
        "Wants=network-online.target",
        "StartLimitIntervalSec=0",  # never given up on: started again every RestartSec, as launchd does
        "",
        "[Service]",
        "Type=simple",
        f"User={s.account}",
        f"WorkingDirectory={str(link).replace('%', '%%')}",
        *(f"Environment={quoted(f'{name}={value}')}" for name, value in env.items()),
        f"ExecStart={quoted(link / '.venv' / 'bin' / 'python', command=True)} -m ap_coder service run",
        "Restart=on-failure",
        f"RestartSec={THROTTLE}",
        "KillMode=mixed",
        "KillSignal=SIGTERM",
        f"TimeoutStopSec={EXIT_TIMEOUT}",
        f"LimitNOFILE={OPEN_FILES}",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
    ]
    return "\n".join(lines) + "\n"


def read_unit(task_name: str) -> dict[str, list[str]]:
    """The installed unit's settings, each key's values as written ({} when there is none, or it can't be read)."""
    try:
        text = unit_path(task_name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, equals, value = line.strip().partition("=")
        if equals and not key.startswith(("#", ";", "[")):
            found.setdefault(key.strip(), []).append(value.strip())
    return found


def unit_account(task_name: str) -> str:
    """The account the installed unit runs as ("" when none is installed)."""
    return (read_unit(task_name).get("User") or [""])[-1]


def write_unit(task_name: str, text: str) -> Path:
    """Written whole or not at all (``service.write_whole``: a power cut never leaves systemd half a unit, and the
    temporary file's name, not ``.service``, is never read as one); 0644, root's as sudo makes it."""
    return service.write_whole(unit_path(task_name), text.encode("utf-8"), mode=0o644)


def daemon_data(task_name: str) -> str:
    """The data folder the installed unit runs with ("" when none is installed)."""
    for value in read_unit(task_name).get("Environment", []):
        name, _, given = unquoted(value).partition("=")
        if name == "AP_PRIVATE_DIR":
            return given.strip()
    return ""


def data_folder(data: Path, task_name: str) -> Path:
    """The data folder the commands use: ``AP_PRIVATE_DIR`` when given, else the installed unit's (root's), else
    ``data``."""
    if paths.env_path("AP_PRIVATE_DIR"):
        return data
    return Path(daemon_data(task_name) or data)


def command_refusal(args: argparse.Namespace) -> str:
    """Why this service command can't run ("" = it can): all but ``install --dry-run`` change or read root's systemd
    and the service account's files, so they run with sudo, on a Linux that systemd runs."""
    if args.action == "install" and args.dry_run:
        return ""
    if not RUNNING.is_dir():
        return ("This Linux is not run by systemd (a container, or WSL without it): nothing would start the service. "
                "Run AP Coder there with: python -m ap_coder serve --server")  # fmt: skip
    if not service_macos.is_root():
        return f"Run it with sudo: sudo .venv/bin/python -m ap_coder service {args.action} (docs/SERVER.md section 15)."
    return ""


def switch_folder(data: Path, folder: Path) -> None:
    """The service runs ``folder`` from its next start: the ``current`` link points at it (the unit never changes)."""
    service_macos.point_current(folder)


def can_switch(data: Path, folder: Path) -> bool:
    """The ``current`` link's folder may be written by this process: root's /opt/apcoder, never by the service's
    account (its host leaves the switch to sudo server-update)."""
    return service_macos.can_switch(data, folder)


def useradd(account: str) -> list[str]:
    """The service's own account: a system account with no password, no login shell and no home folder, its group of
    its own name."""
    return ["useradd", "--system", "--user-group", "--no-create-home", "--home-dir", "/nonexistent", "--shell", NOLOGIN,
            "--comment", "AP Coder service", account]  # fmt: skip


def hand_back_to_unit(data: Path, task_name: str) -> None:
    """``service_macos.hand_back`` to the installed unit's account, as root, when the data folder is that account's."""
    ids = service_macos.account_ids(unit_account(task_name)) if service_macos.is_root() else None
    if ids and ids[0] != 0 and data.is_dir() and data.stat().st_uid == ids[0]:
        service_macos.hand_back(data, *ids)


# --- systemctl --------------------------------------------------------------------------------------------------------


def systemctl(*args: str) -> Any:
    return service.run_command(["systemctl", *args], capture_output=True)


def parse_show(text: str) -> dict[str, str]:
    """``systemctl show``'s answer, a ``Key=value`` a line."""
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, equals, value = line.partition("=")
        if equals:
            found[key.strip()] = value.strip()
    return found


def loaded(task_name: str) -> dict[str, str] | None:
    """What systemd says of the unit (None when it has not read it: no such unit, or systemd not answering)."""
    done = systemctl("show", unit_name(task_name), "--property=" + ",".join(SHOWN))
    found = parse_show(done.stdout) if done.returncode == 0 else {}
    return found if found.get("LoadState") == "loaded" else None


def _said(done: Any) -> str:
    return (done.stderr or done.stdout or f"exit code {done.returncode}").strip()


# --- install / remove / start / stop / status -------------------------------------------------------------------------


def start(data: Path, s: ServerSettings, say: Say = print) -> int:
    name = unit_name(s.task_name)
    if not unit_path(s.task_name).is_file():
        say(f"The systemd unit {name} is not installed: {SUDO}")
        return 1
    service.stop_flag(data).unlink(missing_ok=True)
    hand_back_to_unit(data, s.task_name)  # (what server-update or another sudo made there meanwhile)
    systemctl("enable", name)  # (one turned off with systemctl disable would not start at boot)
    done = systemctl("restart", name)  # started, or ended first and started again when it runs
    if done.returncode:
        say(f"The systemd unit {name} could not be started: {_said(done)} (see journalctl -u {name})")
        return 1
    say(f"Started. Staff open {service.address(s)}  (log: {service.log_path(data)})")
    return 0


def stop(data: Path, s: ServerSettings, say: Say = print) -> int:
    """Stop the dashboard and its host, and keep systemd from starting them again until ``service start``: the
    ``stopped`` file (the host started at boot ends at once), the dashboard asked to stop, the unit stopped (systemd's
    SIGTERM: the host ends its dashboard, then itself, with exit code 0; systemctl waits for it)."""
    name = unit_name(s.task_name)
    service.write_stop_flag(data, AGAIN)
    stopped = service.dashboard_stopped(data, s)
    systemctl("stop", name)
    deadline = time.monotonic() + EXIT_TIMEOUT
    while service.host_running(data) and time.monotonic() < deadline:
        time.sleep(0.5)  # (a host systemd did not start, the ``stopped`` file ends it)
    if service.host_running(data) or not (stopped or service.dashboard_health(data, s, timeout=2.0) is None):
        say(f"The service did not stop: see {service.log_path(data)} and journalctl -u {name}, then run this again.")
        return 1
    say(f"Stopped. It stays stopped, also after a restart of this computer, until: {AGAIN}")
    return 0


def refusal(data: Path, s: ServerSettings, args: argparse.Namespace) -> str:
    """Why the unit can't be installed so ("" = it can)."""
    if not service_macos.is_root():
        return f"Run it with sudo: the unit is written in {UNITS} and started by systemd. {SUDO}"
    if not paths.env_path("AP_PRIVATE_DIR") and not daemon_data(s.task_name):
        return (f"Give the data folder (outside the AP Coder folder, which an update replaces): {SUDO}. It is kept in "
                "the unit: the other service commands find it there.")  # fmt: skip
    ids = service_macos.account_ids(s.account)
    if ids is None and s.account != DEFAULT_ACCOUNT:
        return (f"This computer has no account {s.account}: make it first ({shlex.join(useradd(s.account))}), or "
                f"leave --account out: service install makes {DEFAULT_ACCOUNT}.")  # fmt: skip
    if ids and ids[0] == 0 and not getattr(args, "allow_root", False):
        return (f"AP Coder would run as root ({s.account}): every invoice and email it reads would be handled with "
                "the computer's every right. Leave --account out (its own account, apcoder); for a first test only, "
                "add --allow-root.")  # fmt: skip
    python = s.code_folder / ".venv" / "bin" / "python"
    if not python.exists():
        return f"{python} is not there: set {s.code_folder} up first (docs/SERVER.md section 15)."
    link = service_macos.current_link(s.code_folder)
    if link.exists() and not link.is_symlink():
        return f"{link} is a folder or a file of its own, not AP Coder's link: move it away, then run this again."
    return ""


def dry_run(data: Path, s: ServerSettings, args: argparse.Namespace, say: Say) -> int:
    folder, name = s.code_folder, unit_name(s.task_name)
    say(f"1. Save the settings in {service.config_path(data)}:")
    say(json.dumps({**dataclasses.asdict(s), "allowed_hosts": list(s.allowed_hosts),
                    "allowed_folders": list(s.allowed_folders), "folder": str(folder)}, indent=2))  # fmt: skip
    if service_macos.account_ids(s.account) is None:
        say(f"   Make the account {s.account}: {shlex.join(useradd(s.account))}")
    say(f"2. Make {service.service_dir(data)} and {data / service.LOG_DIR} the account {s.account}'s")
    say(f"3. Point {service_macos.current_link(folder)} at {folder}")
    say(f"4. Write the systemd unit {unit_path(s.task_name)}:")
    say(unit_text(s, data))
    say(f"5. Read it and start it at boot: systemctl daemon-reload; systemctl enable {name}")
    if not args.no_start:
        say(f"6. Start it: systemctl restart {name}")
    say("Nothing was changed (dry run).")
    return 0


def install(data: Path, s: ServerSettings, args: argparse.Namespace, say: Say = print) -> int:
    folder, name, unit = s.code_folder, unit_name(s.task_name), unit_path(s.task_name)
    if args.dry_run:
        return dry_run(data, s, args, say)
    refused = refusal(data, s, args)
    if refused:
        say(refused)
        return 1
    if service_macos.account_ids(s.account) is None:  # (``refusal`` lets only the default through)
        done = service.run_command(useradd(s.account), capture_output=True)
        if done.returncode:
            say(f"The account {s.account} could not be made: {_said(done)}")
            return 1
        say(f"Made the account {s.account}: a system account, with no password and no login shell.")
    uid, gid = service_macos.account_ids(s.account) or (0, 0)
    if service.host_running(data):  # installed again while running: the new settings apply from the restart
        stop(data, s, say)
    try:  # (before server.json: a data folder made now is the account's)
        warning = service_macos.hand_over(data, s.account, uid, gid)
    except OSError as exc:
        say(str(exc))
        return 1
    service.save_settings(data, s)
    service_macos.point_current(folder)
    write_unit(s.task_name, unit_text(s, data))
    for step in (("daemon-reload",), ("enable", name)):
        done = systemctl(*step)
        if done.returncode:
            say(f"systemctl {' '.join(step)} failed: {_said(done)}")
            return 1
    say(f"Installed the systemd unit {name} ({unit}): it starts at boot as {s.account}, from "
        f"{service_macos.current_link(folder)}.")  # fmt: skip
    if warning:
        say(warning)
    code = 0 if args.no_start else start(data, s, say)
    say("\nNext steps (docs/SERVER.md section 15):")
    say(f"  - the firewall, when ufw is on: sudo ufw allow {s.port}/tcp")
    if not s.allowed_hosts:
        say("  - the name staff type: --allowed-hosts apcoder.company.local (and that DNS alias)")
    say(f"  - the first admin: sudo -u {s.account} AP_PRIVATE_DIR=<the data folder> AP_SERVER_MODE=1 .venv/bin/python "
        "-m ap_coder users add --admin <username>")  # fmt: skip
    say(f"  - check it: sudo .venv/bin/python -m ap_coder service status   (log: {service.log_path(data)}; "
        f"journalctl -u {name})")  # fmt: skip
    return code


def remove(data: Path, s: ServerSettings, say: Say = print) -> int:
    name, unit = unit_name(s.task_name), unit_path(s.task_name)
    if not unit.is_file() and loaded(s.task_name) is None:
        say(f"The systemd unit {name} is not installed: nothing to remove.")
        return 1
    stop(data, s, lambda m: None)
    systemctl("disable", name)
    unit.unlink(missing_ok=True)
    systemctl("daemon-reload")
    service.stop_flag(data).unlink(missing_ok=True)
    say(f"Removed the systemd unit {name}. The data folder, {service.config_path(data).name}, the logs, the "
        f"{service_macos.CURRENT} link and the account {s.account} are kept.")  # fmt: skip
    return 0


def installed(s: ServerSettings) -> bool:
    """Its unit file is there (``server-update`` stops and starts it only then)."""
    return unit_path(s.task_name).is_file()


def _number(value: Any) -> int | None:
    return int(value) if str(value or "").isdigit() else None


def state(data: Path, s: ServerSettings) -> dict:
    """The unit as ``service status`` says it: ``line`` (installed) or ``missing`` (not), and what --json gives."""
    name, unit = unit_name(s.task_name), unit_path(s.task_name)
    found = loaded(s.task_name)
    account = unit_account(s.task_name) or s.account
    pid = _number((found or {}).get("MainPID")) or None
    if found is None:
        said = "not loaded (sudo systemctl daemon-reload, then service start)"
    else:
        said = f"{found.get('ActiveState', '?')} ({found.get('SubState', '?')}" + (f", process {pid})" if pid else ")")
    return {"installed": unit.is_file() or found is not None, "unit": name, "unit_file": str(unit),
            "loaded": found is not None, "state": found.get("ActiveState", "?") if found else "not loaded",
            "sub_state": (found or {}).get("SubState"), "systemd_pid": pid,
            "restarts": _number((found or {}).get("NRestarts")), "last_exit_code": (found or {}).get("ExecMainStatus"),
            "result": (found or {}).get("Result"), "enabled": (found or {}).get("UnitFileState"), "account": account,
            "line": f"systemd unit {name}: {said}, runs as {account}",
            "missing": f"The systemd unit {name} is not installed: {SUDO}"}  # fmt: skip


def doctor_check(data: Path, s: ServerSettings) -> tuple[str, str, str]:
    """The doctor's line about the unit (it reads the unit file and asks systemctl, changing nothing)."""
    try:
        found = state(data, s)
    except OSError as exc:
        return ("server service", "WARN", f"systemctl could not be asked ({exc})")
    if not found["installed"]:
        return ("server service", "WARN", f"the systemd unit {found['unit']} is not installed: {SUDO}")
    stopped = " (stopped on purpose: service start)" if service.stop_flag(data).exists() else ""
    return ("server service", "PASS", f"systemd unit {found['unit']}: {found['state']}, runs as "
            f"{found['account']}{stopped}")  # fmt: skip
