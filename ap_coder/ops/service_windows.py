"""The company server's service on Windows (D-100): a scheduled task, run.cmd, and the schtasks commands of
``python -m ap_coder service install|remove|start|stop|status``.

How it runs, with nothing to download and no pywin32: a **Windows scheduled task** (Task Scheduler is part of every
Windows Server) that starts at boot under a dedicated local account, whether anyone is logged on or not. The task runs
``<data folder>\\service\\run.cmd``, which runs ``python -m ap_coder service run``: the *service host* (``service.py``
``run_host``). The task also comes back every 5 minutes (a repeating trigger that does nothing while the host runs), so
even a host that was ended is back within minutes. A plain ``sc.exe create`` of Python is not a real service (the
Service Control Manager ends it after 30 s), and a ctypes service host does not work from a virtual environment's
``python.exe`` (it starts the real Python as a second process, which cannot answer the Service Control Manager).

The ``run.cmd`` file is the one thing the task points at: ``server-update`` rewrites it for a new folder, so the task
(and the account's password Windows keeps for it) never has to be registered again.

``service stop`` leaves a ``stopped`` file beside ``run.cmd`` (the repeating trigger then starts nothing), asks the
dashboard to stop (``POST /shutdown`` with the token of ``.ap_coder_server.json``, to this computer only) and ends the
task. ``service start`` removes it and runs the task. What the platforms share (the settings, the host, the dashboard's
own stop) is ``service.py``'s, always reached as ``service.<name>`` here. Standard library only.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape, unescape

from . import service

if TYPE_CHECKING:
    from .service import Say, ServerSettings

NAME = "windows"
DEFAULT_ACCOUNT = service.DEFAULT_ACCOUNT  # a local account made for AP Coder only (docs/SERVER.md)
AGAIN = "python -m ap_coder service start"
# Windows' own accounts: no password; the task names them by their SID.
BUILT_IN = {"system": "S-1-5-18", "localsystem": "S-1-5-18", "nt authority\\system": "S-1-5-18",
            "local service": "S-1-5-19", "localservice": "S-1-5-19", "nt authority\\local service": "S-1-5-19",
            "nt authority\\localservice": "S-1-5-19", "network service": "S-1-5-20", "networkservice": "S-1-5-20",
            "nt authority\\network service": "S-1-5-20", "nt authority\\networkservice": "S-1-5-20"}  # fmt: skip


# --- What the task runs -----------------------------------------------------------------------------------------------


def runner_path(data: Path) -> Path:
    return service.service_dir(data) / "run.cmd"


def task_file(data: Path) -> Path:
    return service.service_dir(data) / "ap-coder-task.xml"


def runner_text(folder: Path, data: Path) -> str:
    """``run.cmd``: the data folder, server mode, and the service host of ``folder``. A % is doubled (cmd.exe)."""

    def cmd(value: str | Path) -> str:
        return str(value).replace("%", "%%")

    python = f"{folder}\\.venv\\Scripts\\python.exe"  # (a .cmd file: always Windows)
    return (
        "@echo off\r\n"
        'rem AP Coder\'s service host, written by "python -m ap_coder service install" and "server-update".\r\n'
        "rem Change the settings in server.json (next to this folder), not here.\r\n"
        f'set "AP_PRIVATE_DIR={cmd(data)}"\r\n'
        'set "AP_SERVER_MODE=1"\r\n'
        f'cd /d "{cmd(folder)}"\r\n'
        f'"{cmd(python)}" -m ap_coder service run\r\n'
    )


def write_runner(data: Path, folder: Path) -> Path:
    """run.cmd written whole or not at all (``service.write_whole``): cut short by a power cut, the task would start
    nothing, and no host would start to deal with the update cut short with it."""
    return service.write_whole(runner_path(data), runner_text(folder, data).encode("utf-8"))


def account_sid(account: str) -> str:
    """Windows' own account's SID ("" for any other account)."""
    return BUILT_IN.get(account.strip().lower(), "")


def task_xml(s: ServerSettings, runner: Path, now: dt.datetime | None = None) -> str:
    """The scheduled task: at boot (a minute after), and every 5 minutes (nothing happens while the host runs: one
    instance only), for ever (no 3-day limit), at normal priority, on battery too, started again after a failure. It
    starts in run.cmd's folder, which no update changes (run.cmd goes to the version's folder itself): a version
    folder deleted once no longer needed would leave Task Scheduler unable to start it (error 267, B-1961)."""
    start = (now or dt.datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")
    sid = account_sid(s.account)
    user = f"<UserId>{sid}</UserId>" if sid else f"<UserId>{escape(s.account)}</UserId><LogonType>Password</LogonType>"
    start_in = escape(str(runner.parent))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>AP Coder for the office: python -m ap_coder service (docs/SERVER.md).</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger><Enabled>true</Enabled><Delay>PT1M</Delay></BootTrigger>
    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>
      <Repetition><Interval>PT5M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">{user}<RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(str(runner))}</Command><WorkingDirectory>{start_in}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def task_folder(data: Path) -> str:
    """The folder the task ``service install`` registered starts in, as the copy it saved says ("" when none is
    saved: a Mac, or not installed). One an older AP Coder registered starts in the folder it was installed from."""
    try:
        text = task_file(data).read_text(encoding="utf-16")
    except (OSError, UnicodeError):
        return ""
    found = re.search(r"<WorkingDirectory>(.*?)</WorkingDirectory>", text)
    return unescape(found.group(1)) if found else ""


def create_command(s: ServerSettings, xml: Path) -> list[str]:
    """``schtasks /create``. Any account but Windows' own: ``/ru`` without ``/rp``, so schtasks asks for its password
    itself (it is never on a command line, and Windows keeps it for the task)."""
    cmd = ["schtasks", "/create", "/tn", s.task_name, "/xml", str(xml)]
    if not account_sid(s.account):
        cmd += ["/ru", s.account]
    return [*cmd, "/f"]


def data_folder(data: Path, task_name: str) -> Path:
    """The data folder the commands use: this computer's (``setx /M AP_PRIVATE_DIR``, docs/SERVER.md step 2)."""
    return data


def command_refusal(args: argparse.Namespace) -> str:
    """Nothing refused beforehand: schtasks says so when the console is not an administrator's."""
    return ""


def switch_folder(data: Path, folder: Path) -> None:
    """The service runs ``folder`` from its next start: run.cmd rewritten (the task itself never changes)."""
    write_runner(data, folder)


def can_switch(data: Path, folder: Path) -> bool:
    """run.cmd's folder (the data folder's ``service``, the service account's) may be written by this process."""
    return os.access(runner_path(data).parent, os.W_OK)


# --- The host's dashboard, ended with it ------------------------------------------------------------------------------


_JOBS: list[Any] = []  # the job objects that end the dashboard with the host (kept open while the host runs)


def tie_to_host(child: Any) -> None:
    """Windows: put the dashboard in a job object that ends it when the host ends (the task ended, the host killed),
    so a dashboard is never left running without its host. Nothing elsewhere; best effort."""
    if not service.WINDOWS or not hasattr(child, "_handle"):
        return
    try:
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]  # fmt: skip

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]  # fmt: skip

        kernel = service.ctypes_kernel32()
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            return
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
        if kernel.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(child._handle))):
            _JOBS.append(job)
    except (OSError, AttributeError, ValueError):
        pass


# --- install / remove / start / stop / status -------------------------------------------------------------------------


def start(data: Path, s: ServerSettings, say: Say = print) -> int:
    service.stop_flag(data).unlink(missing_ok=True)
    done = service.run_command(["schtasks", "/run", "/tn", s.task_name], capture_output=True)
    if done.returncode:
        say(f'The task "{s.task_name}" could not be started: {(done.stderr or done.stdout).strip()}')
        return 1
    say(f"Started. Staff open {service.address(s)}  (log: {service.log_path(data)})")
    return 0


def stop(data: Path, s: ServerSettings, say: Say = print) -> int:
    """Stop the dashboard and its host, and keep the task from starting them again until ``service start``."""
    service.write_stop_flag(data)
    stopped = service.dashboard_stopped(data, s)
    service.run_command(["schtasks", "/end", "/tn", s.task_name], capture_output=True)
    # Only the running host: it holds the host lock and wrote host.json as soon as it held it, so the record is read
    # once the lock says a host runs. A record left by a host that was killed may name a number another process has
    # since, an AP Coder watch among them (B-1590, B-1677).
    host = int(service._read_json(service.host_record(data)).get("pid") or 0) if service.host_running(data) else 0
    if host and host != os.getpid() and service.runs_python_of(host, s.code_folder):
        service.terminate(host)
    if not stopped:
        say("The dashboard did not stop: end python.exe in Task Manager (Details), then run this again.")
        return 1
    say("Stopped. It stays stopped, also after a restart of the server, until: python -m ap_coder service start")
    return 0


def install(data: Path, s: ServerSettings, args: argparse.Namespace, say: Say = print) -> int:
    folder = s.code_folder
    if args.dry_run:
        say(f"1. Save the settings in {service.config_path(data)}:")
        say(json.dumps({**dataclasses.asdict(s), "allowed_hosts": list(s.allowed_hosts),
                        "allowed_folders": list(s.allowed_folders), "folder": str(folder)}, indent=2))  # fmt: skip
        say(f"2. Write {runner_path(data)}:")
        say(runner_text(folder, data).replace("\r\n", "\n"))
        say(f"3. Write the task {task_file(data)}:")
        say(task_xml(s, runner_path(data)))
        say(f"4. Register it: {service.shown(create_command(s, task_file(data)))}")
        if not account_sid(s.account):
            say(f"   (schtasks asks for {s.account}'s password)")
        if not args.no_start:
            say(f"5. Start it: {service.shown(['schtasks', '/run', '/tn', s.task_name])}")
        say("Nothing was changed (dry run).")
        return 0
    if service.host_running(data):  # installed again while running: the new settings apply from the restart
        stop(data, s, say)
    service.save_settings(data, s)
    write_runner(data, folder)
    xml = task_file(data)
    xml.write_text(task_xml(s, runner_path(data)), encoding="utf-16")
    if not account_sid(s.account):
        say(f"Windows asks for the password of {s.account} (it keeps it for the task).")
    done = service.run_command(create_command(s, xml))
    if done.returncode:
        say(f"The task could not be registered (schtasks: exit code {done.returncode}). Run this as an "
            "administrator; check the account's name and password.")  # fmt: skip
        return 1
    say(f'Installed the scheduled task "{s.task_name}": it starts at boot as {s.account}.')
    code = 0 if args.no_start else start(data, s, say)
    say("\nNext steps (docs/SERVER.md):")
    say(f"  - the firewall: netsh advfirewall firewall add rule name=\"AP Coder\" dir=in action=allow protocol=TCP "
        f"localport={s.port}")  # fmt: skip
    if not s.allowed_hosts:
        say("  - the name staff type: --allowed-hosts apcoder.company.local (and that DNS alias)")
    say("  - the first admin: python -m ap_coder users add --admin <username>")
    say(f"  - check it: python -m ap_coder service status   (log: {service.log_path(data)})")
    return code


def remove(data: Path, s: ServerSettings, say: Say = print) -> int:
    stop(data, s, lambda m: None)
    done = service.run_command(["schtasks", "/delete", "/tn", s.task_name, "/f"], capture_output=True)
    if done.returncode:
        say(f'The task "{s.task_name}" could not be removed: {(done.stderr or done.stdout).strip()}')
        return 1
    service.stop_flag(data).unlink(missing_ok=True)
    say(f'Removed the task "{s.task_name}". The data folder, {service.config_path(data).name} and the logs are kept.')
    return 0


def installed(s: ServerSettings) -> bool:
    """The task is registered (``server-update`` stops and starts it only then)."""
    return service.run_command(["schtasks", "/query", "/tn", s.task_name], capture_output=True).returncode == 0


def _task_state(done: Any) -> str:
    """The task's state in ``schtasks /query /v /fo LIST``'s answer ("Ready", "Running"...; "?" when it says none)."""
    return next((line.split(":", 1)[1].strip() for line in (done.stdout or "").splitlines()
                 if line.strip().lower().startswith("status:")), "?")  # fmt: skip


def state(data: Path, s: ServerSettings) -> dict:
    """The task as ``service status`` says it: ``line`` (installed) or ``missing`` (not), and what --json gives."""
    done = service.run_command(["schtasks", "/query", "/tn", s.task_name, "/v", "/fo", "LIST"], capture_output=True)
    task = _task_state(done) if done.returncode == 0 else ""
    return {"installed": done.returncode == 0, "task": s.task_name, "state": task, "account": s.account,
            "line": f'Scheduled task "{s.task_name}": {task} (runs as {s.account})',
            "missing": f'The task "{s.task_name}" is not installed: python -m ap_coder service install'}  # fmt: skip


def doctor_check(data: Path, s: ServerSettings) -> tuple[str, str, str]:
    """The doctor's line about the task (it asks schtasks only)."""
    try:
        done = service.run_command(["schtasks", "/query", "/tn", s.task_name, "/v", "/fo", "LIST"],
                                   capture_output=True)  # fmt: skip
    except OSError as exc:
        return ("server service", "WARN", f"schtasks could not be asked ({exc})")
    if done.returncode:
        return ("server service", "WARN", f'the task "{s.task_name}" is not installed: python -m ap_coder service '
                "install")  # fmt: skip
    stopped = " (stopped on purpose: service start)" if service.stop_flag(data).exists() else ""
    return ("server service", "PASS", f'task "{s.task_name}": {_task_state(done)}, runs as {s.account}{stopped}')
