"""AP Coder as a Windows service on the company server (D-100): ``python -m ap_coder service install|remove|start|
stop|status``.

How it runs, with nothing to download and no pywin32: a **Windows scheduled task** (Task Scheduler is part of every
Windows Server) that starts at boot under a dedicated local account, whether anyone is logged on or not. The task runs
``<data folder>\\service\\run.cmd``, which runs ``python -m ap_coder service run``: the *service host*. The host starts
the dashboard (``python -m ap_coder serve --server ...``) with the server settings, writes what it says to the rotating
log ``<data folder>\\logs\\service.log``, and starts it again when it stops (after 5 s, then 10 s, ... up to 5
minutes). The task also comes back every 5 minutes (a repeating trigger that does nothing while the host runs), so
even a host that was ended is back within minutes. A plain ``sc.exe create`` of Python is not a real service (the
Service Control Manager ends it after 30 s), and a ctypes service host does not work from a virtual environment's
``python.exe`` (it starts the real Python as a second process, which cannot answer the Service Control Manager).

Settings: ``<data folder>\\server.json`` (written by ``service install``; ``server-update`` keeps its record there
too), each overridden by an environment variable (:data:`ENV_SETTINGS`). The ``run.cmd`` file is the one thing the task
points at: ``server-update`` rewrites it for a new folder, so the task (and the account's password Windows keeps for
it) never has to be registered again.

``service stop`` leaves a ``stopped`` file beside ``run.cmd`` (the repeating trigger then starts nothing), asks the
dashboard to stop (``POST /shutdown`` with the token of ``.ap_coder_server.json``, to this computer only) and ends the
task. ``service start`` removes it and runs the task. Standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import json
import logging
import logging.handlers
import os
import ssl
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .. import __version__
from ..core.paths import PROJECT_DIR, private_dir

WINDOWS = os.name == "nt"
CONFIG_FILE = "server.json"  # in the data folder
SERVICE_DIR = "service"  # in the data folder: run.cmd, the task's XML, host.json, stopped
LOG_DIR = "logs"
LOG_FILE = "service.log"
TASK_NAME = "AP Coder"
DEFAULT_ACCOUNT = "APCoderService"  # a local account made for AP Coder only (docs/SERVER.md)
DEFAULT_PORT = 8501
DEFAULT_HOST = "0.0.0.0"  # every network card of the server: staff open it from their browsers
SERVER_FILE = ".ap_coder_server.json"  # written by the running dashboard (web/server.py)
# The environment variables that override server.json (the TLS and host names are those `serve --server` reads too).
ENV_SETTINGS = {"port": "AP_SERVER_PORT", "host": "AP_DASHBOARD_ADDRESS", "certfile": "AP_TLS_CERT",
                "keyfile": "AP_TLS_KEY", "allowed_hosts": "AP_ALLOWED_HOSTS", "proxy": "AP_SERVER_PROXY"}  # fmt: skip
# Windows' own accounts: no password; the task names them by their SID.
BUILT_IN = {"system": "S-1-5-18", "localsystem": "S-1-5-18", "nt authority\\system": "S-1-5-18",
            "local service": "S-1-5-19", "localservice": "S-1-5-19", "nt authority\\local service": "S-1-5-19",
            "nt authority\\localservice": "S-1-5-19", "network service": "S-1-5-20", "networkservice": "S-1-5-20",
            "nt authority\\network service": "S-1-5-20", "nt authority\\networkservice": "S-1-5-20"}  # fmt: skip
RESTART_FIRST, RESTART_MOST, STEADY = 5.0, 300.0, 600.0  # seconds: first wait, longest wait, a run that counts as fine

Say = Callable[[str], None]


class SettingsError(ValueError):
    """server.json can't be read, or the settings can't work: said in plain words."""


@dataclasses.dataclass(frozen=True)
class ServerSettings:
    port: int = DEFAULT_PORT
    host: str = DEFAULT_HOST
    certfile: str = ""
    keyfile: str = ""
    allowed_hosts: tuple[str, ...] = ()
    proxy: str = ""
    insecure_http: bool = False
    account: str = DEFAULT_ACCOUNT
    task_name: str = TASK_NAME
    folder: str = ""  # the AP Coder folder the service runs ("" = this one); server-update changes it

    @property
    def code_folder(self) -> Path:
        return Path(self.folder) if self.folder else PROJECT_DIR

    @property
    def https(self) -> bool:
        return bool(self.certfile) and not self.insecure_http


FIELDS = {f.name for f in dataclasses.fields(ServerSettings)}


# --- The settings -----------------------------------------------------------------------------------------------------


def config_path(data: Path) -> Path:
    return data / CONFIG_FILE


def read_config(data: Path) -> dict:
    """server.json as it is ({} when there is none). A damaged one raises SettingsError: never guessed around."""
    path = config_path(data)
    try:
        found = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SettingsError(f"{path} can't be read ({exc}). Fix it or delete it, then run the command again.") from None
    if not isinstance(found, dict):
        raise SettingsError(f"{path} is not a settings file (a JSON object). Fix it or delete it.")
    return found


def write_config(data: Path, **values: Any) -> Path:
    """Set these values in server.json, keeping every other one (written whole, then renamed)."""
    path = config_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**read_config(data), **values}
    part = path.with_name(f"{path.name}.{os.getpid()}.part")
    part.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    os.replace(part, path)
    return path


def _hosts(value: Any) -> tuple[str, ...]:
    items = value.split(",") if isinstance(value, str) else list(value or [])
    return tuple(str(h).strip() for h in items if str(h).strip())


def settings(data: Path, env: dict | None = None, **overrides: Any) -> ServerSettings:
    """The settings in effect: the defaults, then server.json, then the environment (:data:`ENV_SETTINGS`), then
    ``overrides`` (the command line; None = not given)."""
    env = os.environ if env is None else env
    values: dict[str, Any] = {k: v for k, v in read_config(data).items() if k in FIELDS}
    for name, var in ENV_SETTINGS.items():
        if (env.get(var) or "").strip():
            values[name] = env[var].strip()
    values.update({k: v for k, v in overrides.items() if v is not None})
    values["allowed_hosts"] = _hosts(values.get("allowed_hosts"))
    try:
        values["port"] = int(values.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        raise SettingsError(f"The port {values.get('port')!r} is not a number.") from None
    values["insecure_http"] = values.get("insecure_http") in (True, "1", "true", "yes", "on")
    for name in ("host", "certfile", "keyfile", "proxy", "account", "task_name", "folder"):
        values[name] = str(values.get(name) or getattr(ServerSettings, name)).strip()
    return ServerSettings(**values)


def problems(s: ServerSettings, check_files: bool = True) -> list[str]:
    """What stops these settings from working, in plain words ([] = fine)."""
    found = []
    if not 1 <= s.port <= 65535:
        found.append(f"The port {s.port} is not between 1 and 65535.")
    if not s.insecure_http and not s.certfile:
        found.append("HTTPS needs the server's certificate: give --certfile and --keyfile (PEM files; docs/SERVER.md "
                     "says how to make them from IT's .pfx). Only for a first test: --insecure-http.")  # fmt: skip
    if bool(s.certfile) != bool(s.keyfile) and not s.insecure_http:
        found.append("Give both --certfile and --keyfile: the certificate and its private key.")
    if check_files and s.https:
        found += [f"The file {f} is not there." for f in (s.certfile, s.keyfile) if f and not Path(f).is_file()]
    return found


def save_settings(data: Path, s: ServerSettings) -> Path:
    values = dataclasses.asdict(s)
    values["allowed_hosts"] = list(s.allowed_hosts)
    values["folder"] = str(s.code_folder)
    return write_config(data, **values)


# --- What the task runs -----------------------------------------------------------------------------------------------


def folder_python(folder: Path) -> Path:
    """The Python of an AP Coder folder: its .venv's (``Scripts\\python.exe`` on Windows)."""
    own = folder / ".venv" / ("Scripts" if WINDOWS else "bin") / ("python.exe" if WINDOWS else "python")
    if not own.exists() and folder == PROJECT_DIR:
        return Path(sys.executable)  # (a developer's checkout without a .venv)
    return own


def serve_command(s: ServerSettings, python: str | Path) -> list[str]:
    """The dashboard as the service host starts it (``serve --server``: logins, HTTPS)."""
    cmd = [str(python), "-m", "ap_coder", "serve", "--server", "--no-browser", "--host", s.host, "--port", str(s.port)]
    if s.certfile and s.keyfile:
        cmd += ["--certfile", s.certfile, "--keyfile", s.keyfile]
    if s.proxy:
        cmd += ["--proxy", s.proxy]
    if s.insecure_http:
        cmd.append("--insecure-http")
    return cmd


def serve_env(s: ServerSettings, data: Path) -> dict[str, str]:
    """What the dashboard's environment gets on top of the host's."""
    env = {"AP_PRIVATE_DIR": str(data), "AP_SERVER_MODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    if s.allowed_hosts:
        env["AP_ALLOWED_HOSTS"] = ",".join(s.allowed_hosts)
    return env


def service_dir(data: Path) -> Path:
    return data / SERVICE_DIR


def runner_path(data: Path) -> Path:
    return service_dir(data) / "run.cmd"


def task_file(data: Path) -> Path:
    return service_dir(data) / "ap-coder-task.xml"


def stop_flag(data: Path) -> Path:
    return service_dir(data) / "stopped"


def host_record(data: Path) -> Path:
    return service_dir(data) / "host.json"


def log_path(data: Path) -> Path:
    return data / LOG_DIR / LOG_FILE


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
    path = runner_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(runner_text(folder, data), encoding="utf-8", newline="")
    return path


def account_sid(account: str) -> str:
    """Windows' own account's SID ("" for any other account)."""
    return BUILT_IN.get(account.strip().lower(), "")


def task_xml(s: ServerSettings, runner: Path, folder: Path, now: dt.datetime | None = None) -> str:
    """The scheduled task: at boot (a minute after), and every 5 minutes (nothing happens while the host runs: one
    instance only), for ever (no 3-day limit), at normal priority, on battery too, started again after a failure."""
    start = (now or dt.datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")
    sid = account_sid(s.account)
    user = f"<UserId>{sid}</UserId>" if sid else f"<UserId>{escape(s.account)}</UserId><LogonType>Password</LogonType>"
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
    <Exec><Command>{escape(str(runner))}</Command><WorkingDirectory>{escape(str(folder))}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def create_command(s: ServerSettings, xml: Path) -> list[str]:
    """``schtasks /create``. Any account but Windows' own: ``/ru`` without ``/rp``, so schtasks asks for its password
    itself (it is never on a command line, and Windows keeps it for the task)."""
    cmd = ["schtasks", "/create", "/tn", s.task_name, "/xml", str(xml)]
    if not account_sid(s.account):
        cmd += ["/ru", s.account]
    return [*cmd, "/f"]


def run_command(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Run a Windows command (schtasks, taskkill). The one place commands run: tests record them instead."""
    kwargs.setdefault("text", True)
    kwargs.setdefault("errors", "replace")
    return subprocess.run(cmd, check=False, **kwargs)  # noqa: S603


def shown(cmd: list[str]) -> str:
    return subprocess.list2cmdline(cmd)


# --- Processes: the host's record, the dashboard's --------------------------------------------------------------------


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if WINDOWS:  # (os.kill(pid, 0) would end the process on Windows)
        try:
            kernel = ctypes_kernel32()
            handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                import ctypes

                code = ctypes.c_ulong()
                return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            finally:
                kernel.CloseHandle(handle)
        except (OSError, AttributeError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ctypes_kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    return kernel


def terminate(pid: int) -> None:
    """End a process and what it started (Windows: ``taskkill /T /F``)."""
    if not process_alive(pid):
        return
    if WINDOWS:
        run_command(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        with contextlib.suppress(OSError):
            os.kill(pid, 15)


def _read_json(path: Path) -> dict:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _local(url: str, data: bytes | None = None, headers: dict | None = None, timeout: float = 5.0) -> Any:
    """A request to this computer only, never through a proxy. Over HTTPS the certificate is not checked: it names the
    server's DNS name, not 127.0.0.1, and the request never leaves this computer."""
    request = urllib.request.Request(
        url, data=data, headers=headers or {}, method="POST" if data is not None else "GET"
    )
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
    with opener.open(request, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8") or "{}")


def _schemes(s: ServerSettings) -> tuple[str, ...]:
    return ("https", "http") if s.https else ("http", "https")


def dashboard_health(data: Path, s: ServerSettings, timeout: float = 3.0) -> dict | None:
    """The running dashboard's ``/healthz`` answer (None when none answers on this computer)."""
    record = _read_json(data / SERVER_FILE)
    port = int(record.get("port") or s.port)
    for scheme in _schemes(s):
        try:
            found = _local(f"{scheme}://127.0.0.1:{port}/healthz", timeout=timeout)
        except (OSError, ValueError):
            continue
        if isinstance(found, dict) and found.get("app") == "ap-coder":
            return found
    return None


def dashboard_stopped(data: Path, s: ServerSettings, wait: float = 20.0) -> bool:
    """Ask the running dashboard to stop (``POST /shutdown`` with its token) and wait for its process to end; ended
    when it won't. True when no dashboard of this data folder runs any more."""
    record = _read_json(data / SERVER_FILE)
    pid, token, port = int(record.get("pid") or 0), str(record.get("token") or ""), int(record.get("port") or s.port)
    if not process_alive(pid):
        return True
    for scheme in _schemes(s):
        try:
            _local(f"{scheme}://127.0.0.1:{port}/shutdown", data=b"", headers={"X-AP-Coder-Token": token})
            break
        except (OSError, ValueError):
            continue
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and process_alive(pid):
        time.sleep(0.5)
    terminate(pid)
    return not process_alive(pid)


# --- The service host (``service run``): what the task runs -----------------------------------------------------------


def host_logger(data: Path) -> logging.Logger:
    """``<data folder>/logs/service.log``: 5 MB, then 5 older ones kept (service.log.1 ... .5)."""
    log = logging.getLogger("ap_coder.service")
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()
    path = log_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


_JOBS: list[Any] = []  # the job objects that end the dashboard with the host (kept open while the host runs)


def tie_to_host(child: Any) -> None:
    """Windows: put the dashboard in a job object that ends it when the host ends (the task ended, the host killed),
    so a dashboard is never left running without its host. Nothing elsewhere; best effort."""
    if not WINDOWS or not hasattr(child, "_handle"):
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

        kernel = ctypes_kernel32()
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


def _wait(seconds: float, data: Path, sleep: Callable[[float], None]) -> None:
    """Wait ``seconds``, a second at a time, ending early once the service is stopped."""
    left = seconds
    while left > 0 and not stop_flag(data).exists():
        step = min(1.0, left)
        sleep(step)
        left -= step


def run_host(
    data: Path,
    popen: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Keep the dashboard running until the service is stopped (the ``stopped`` file). Settings are read again at each
    start, so a change in server.json applies at the next restart."""
    if stop_flag(data).exists():
        return 0  # stopped on purpose: the repeating trigger starts nothing
    record = host_record(data)
    other = int(_read_json(record).get("pid") or 0)
    if other and other != os.getpid() and process_alive(other):
        return 0  # a host is running already
    log = host_logger(data)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"pid": os.getpid(), "started": dt.datetime.now().isoformat(timespec="seconds"),
                                  "folder": str(PROJECT_DIR), "version": __version__}), encoding="utf-8")  # fmt: skip
    log.info("Service host started (AP Coder %s, %s, process %s)", __version__, PROJECT_DIR, os.getpid())
    delay = RESTART_FIRST
    try:
        while True:
            try:
                s = settings(data)
            except SettingsError as exc:
                log.error("%s", exc)
                _wait(RESTART_MOST, data, sleep)
                if stop_flag(data).exists():
                    break
                continue
            cmd = serve_command(s, sys.executable)
            log.info("Starting the dashboard: %s", shown(cmd))
            started = clock()
            env = {**os.environ, **serve_env(s, data)}
            try:
                child = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(PROJECT_DIR),
                              text=True, encoding="utf-8", errors="replace")  # fmt: skip
            except OSError as exc:
                log.error("The dashboard could not be started: %s", exc)
                code = None
            else:
                tie_to_host(child)
                for line in child.stdout:
                    log.info("dashboard: %s", line.rstrip())
                code = child.wait()
            if stop_flag(data).exists():
                log.info("The dashboard stopped (exit code %s): the service was stopped", code)
                break
            if clock() - started >= STEADY:
                delay = RESTART_FIRST
            log.warning("The dashboard stopped (exit code %s): starting it again in %d s", code, delay)
            _wait(delay, data, sleep)
            if stop_flag(data).exists():
                log.info("The service was stopped")
                break
            delay = min(delay * 2, RESTART_MOST)
    finally:
        with contextlib.suppress(OSError):
            if int(_read_json(record).get("pid") or 0) == os.getpid():
                record.unlink()
    return 0


# --- install / remove / start / stop / status -------------------------------------------------------------------------


def start(data: Path, s: ServerSettings, say: Say = print) -> int:
    stop_flag(data).unlink(missing_ok=True)
    done = run_command(["schtasks", "/run", "/tn", s.task_name], capture_output=True)
    if done.returncode:
        say(f'The task "{s.task_name}" could not be started: {(done.stderr or done.stdout).strip()}')
        return 1
    say(f"Started. Staff open {address(s)}  (log: {log_path(data)})")
    return 0


def stop(data: Path, s: ServerSettings, say: Say = print) -> int:
    """Stop the dashboard and its host, and keep the task from starting them again until ``service start``."""
    flag = stop_flag(data)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(
        f"stopped {dt.datetime.now():%Y-%m-%d %H:%M} (python -m ap_coder service start)\n", encoding="utf-8"
    )
    stopped = dashboard_stopped(data, s)
    run_command(["schtasks", "/end", "/tn", s.task_name], capture_output=True)
    host = int(_read_json(host_record(data)).get("pid") or 0)
    if host and host != os.getpid():
        terminate(host)
    if not stopped:
        say("The dashboard did not stop: end python.exe in Task Manager (Details), then run this again.")
        return 1
    say("Stopped. It stays stopped, also after a restart of the server, until: python -m ap_coder service start")
    return 0


def address(s: ServerSettings) -> str:
    name = s.allowed_hosts[0] if s.allowed_hosts else "<this server's name>"
    scheme = "https" if s.https else "http"
    return f"{scheme}://{name}:{s.port}" if s.port != (443 if s.https else 80) else f"{scheme}://{name}"


def install(data: Path, s: ServerSettings, args: argparse.Namespace, say: Say = print) -> int:
    folder = s.code_folder
    if args.dry_run:
        say(f"1. Save the settings in {config_path(data)}:")
        say(json.dumps({**dataclasses.asdict(s), "allowed_hosts": list(s.allowed_hosts), "folder": str(folder)},
                       indent=2))  # fmt: skip
        say(f"2. Write {runner_path(data)}:")
        say(runner_text(folder, data).replace("\r\n", "\n"))
        say(f"3. Write the task {task_file(data)}:")
        say(task_xml(s, runner_path(data), folder))
        say(f"4. Register it: {shown(create_command(s, task_file(data)))}")
        if not account_sid(s.account):
            say(f"   (schtasks asks for {s.account}'s password)")
        if not args.no_start:
            say(f"5. Start it: {shown(['schtasks', '/run', '/tn', s.task_name])}")
        say("Nothing was changed (dry run).")
        return 0
    host = int(_read_json(host_record(data)).get("pid") or 0)
    if host and process_alive(host):  # installed again while running: the new settings apply from the restart
        stop(data, s, say)
    save_settings(data, s)
    write_runner(data, folder)
    xml = task_file(data)
    xml.write_text(task_xml(s, runner_path(data), folder), encoding="utf-16")
    if not account_sid(s.account):
        say(f"Windows asks for the password of {s.account} (it keeps it for the task).")
    done = run_command(create_command(s, xml))
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
    say(f"  - check it: python -m ap_coder service status   (log: {log_path(data)})")
    return code


def remove(data: Path, s: ServerSettings, say: Say = print) -> int:
    stop(data, s, lambda m: None)
    done = run_command(["schtasks", "/delete", "/tn", s.task_name, "/f"], capture_output=True)
    if done.returncode:
        say(f'The task "{s.task_name}" could not be removed: {(done.stderr or done.stdout).strip()}')
        return 1
    stop_flag(data).unlink(missing_ok=True)
    say(f'Removed the task "{s.task_name}". The data folder, {config_path(data).name} and the logs are kept.')
    return 0


def status(data: Path, s: ServerSettings, say: Say = print) -> int:
    done = run_command(["schtasks", "/query", "/tn", s.task_name, "/v", "/fo", "LIST"], capture_output=True)
    if done.returncode:
        say(f'The task "{s.task_name}" is not installed: python -m ap_coder service install')
        return 1
    state = next((line.split(":", 1)[1].strip() for line in (done.stdout or "").splitlines()
                  if line.strip().lower().startswith("status:")), "?")  # fmt: skip
    say(f'Scheduled task "{s.task_name}": {state} (runs as {s.account})')
    host = _read_json(host_record(data))
    if host and process_alive(int(host.get("pid") or 0)):
        say(f"Service host: running since {host.get('started', '?')} (process {host.get('pid')}, AP Coder "
            f"{host.get('version', '?')}, {host.get('folder', '?')})")  # fmt: skip
    else:
        say("Service host: not running")
    health = dashboard_health(data, s)
    if health:
        say(f"Dashboard: answering on port {health.get('port', s.port)} (AP Coder {health.get('version', '?')}, "
            f"{health.get('folder', '?')}). Staff open {address(s)}")  # fmt: skip
    else:
        say("Dashboard: not answering")
    if stop_flag(data).exists():
        say("Stopped on purpose: python -m ap_coder service start starts it again.")
    say(f"Settings: {config_path(data)}   Log: {log_path(data)}")
    return 0


# --- What the doctor says about the server (D-102) --------------------------------------------------------------------


def is_server_here(data: Path, env: dict | None = None) -> bool:
    """Whether this AP Coder is the company server: server mode on (``AP_SERVER_MODE``), or its server.json there
    (the doctor is often run from IT's own console, where the service's environment isn't set)."""
    env = os.environ if env is None else env
    on = (env.get("AP_SERVER_MODE") or "").strip().lower() in ("1", "true", "yes", "on")
    return on or config_path(data).is_file()


def _readable(path: str) -> str:
    """ "" when the file is there and can be read, else why not."""
    try:
        with open(path, "rb") as f:
            f.read(1)
    except FileNotFoundError:
        return "not found"
    except OSError as exc:
        return f"can't be read ({exc.strerror or type(exc).__name__})"
    return ""


def doctor_checks(data: Path, env: dict | None = None, *, windows: bool = WINDOWS) -> list[tuple[str, str, str]]:
    """(area, status, detail) of the server's own setup, as the doctor shows them: what it listens on, its HTTPS
    files, the names staff type and, on Windows, the scheduled task. Reads files only (and asks schtasks)."""
    try:
        s = settings(data, env)
    except SettingsError as exc:
        return [("server settings", "FAIL", str(exc))]
    out = [("dashboard network", "PASS", f"the company server: listens on {s.host}, port {s.port}; staff open "
            f"{address(s)}")]  # fmt: skip
    if s.insecure_http:
        out.append(("server HTTPS", "WARN", "off (--insecure-http): passwords cross the network unencrypted; for a "
                    "first test only"))  # fmt: skip
    elif not s.certfile or not s.keyfile:
        out.append(("server HTTPS", "FAIL", "the certificate and its key are not set (AP_TLS_CERT and AP_TLS_KEY, or "
                    "service install --certfile --keyfile): the server doesn't start without them"))  # fmt: skip
    else:
        for what, path in (("certificate", s.certfile), ("private key", s.keyfile)):
            why = _readable(path)
            out.append((f"server HTTPS {what}", "FAIL" if why else "PASS", f"{path}: {why}" if why else path))
    if s.allowed_hosts:
        out.append(("server names", "PASS", ", ".join(s.allowed_hosts)))
    else:
        out.append(("server names", "WARN", "AP_ALLOWED_HOSTS is not set: only this computer's own names open AP "
                    "Coder; add the name staff type (service install --allowed-hosts)"))  # fmt: skip
    if not windows:
        out.append(("server service", "SKIP", "the scheduled task is on Windows only"))
        return out
    try:
        done = run_command(["schtasks", "/query", "/tn", s.task_name, "/v", "/fo", "LIST"], capture_output=True)
    except OSError as exc:
        return [*out, ("server service", "WARN", f"schtasks could not be asked ({exc})")]
    if done.returncode:
        return [*out, ("server service", "WARN", f'the task "{s.task_name}" is not installed: python -m ap_coder '
                       "service install")]  # fmt: skip
    state = next((line.split(":", 1)[1].strip() for line in (done.stdout or "").splitlines()
                  if line.strip().lower().startswith("status:")), "?")  # fmt: skip
    stopped = " (stopped on purpose: service start)" if stop_flag(data).exists() else ""
    return [*out, ("server service", "PASS", f'task "{s.task_name}": {state}, runs as {s.account}{stopped}')]


def add_parser(sub: Any) -> None:
    p = sub.add_parser("service", help="The company server: run AP Coder as a Windows service (docs/SERVER.md)")
    p.add_argument("action", choices=["install", "remove", "start", "stop", "status", "run"],
                   help="run: what the scheduled task runs (the service host)")  # fmt: skip
    p.add_argument("--account", default=None, help=f"The Windows account it runs as (default {DEFAULT_ACCOUNT})")
    p.add_argument("--task-name", default=None, help=f'The scheduled task\'s name (default "{TASK_NAME}")')
    p.add_argument("--port", type=int, default=None, help=f"Default {DEFAULT_PORT}")
    p.add_argument("--host", default=None, help=f"Address to listen on (default {DEFAULT_HOST}: every network)")
    p.add_argument("--certfile", default=None, help="The HTTPS certificate (PEM)")
    p.add_argument("--keyfile", default=None, help="Its private key (PEM)")
    p.add_argument(
        "--allowed-hosts", default=None, help="The names staff type, comma-separated (apcoder.company.local)"
    )
    p.add_argument("--proxy", default=None, help="A reverse proxy's IP address, when one is in front (IIS)")
    p.add_argument("--insecure-http", action="store_true", default=None, help="No HTTPS: for a first test only")
    p.add_argument("--folder", default=None, help="The AP Coder folder to run (default: this one)")
    p.add_argument("--no-start", action="store_true", help="install: don't start it yet")
    p.add_argument("--dry-run", action="store_true", help="install: show what it would do, change nothing")


def main(args: argparse.Namespace) -> int:
    data = private_dir()
    if args.action == "run":
        return run_host(data)
    if not WINDOWS and not (args.action == "install" and args.dry_run):
        print("The service is for Windows (Task Scheduler). Elsewhere, run: python -m ap_coder serve --server",
              file=sys.stderr)  # fmt: skip
        return 2
    try:
        given = {
            name: getattr(args, name)
            for name in (
                "account",
                "task_name",
                "port",
                "host",
                "certfile",
                "keyfile",
                "allowed_hosts",
                "proxy",
                "insecure_http",
            )
        }
        if args.folder:
            given["folder"] = str(Path(args.folder).resolve())
        s = settings(data, **given)
        if args.action == "install":
            found = problems(s, check_files=not args.dry_run)
            if found:
                raise SettingsError("\n".join(found))
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return 1
    actions = {
        "install": lambda: install(data, s, args),
        "remove": lambda: remove(data, s),
        "start": lambda: start(data, s),
        "stop": lambda: stop(data, s),
        "status": lambda: status(data, s),
    }
    return actions[args.action]()
