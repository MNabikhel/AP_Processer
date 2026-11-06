"""AP Coder run as the company server's service (D-100, D-186): ``python -m ap_coder service
install|remove|start|stop|status``, on Windows a scheduled task (``service_windows.py``), on a Mac a LaunchDaemon
(``service_macos.py``).

What both share is here: the settings, the *service host* (``python -m ap_coder service run``, what the task or the
daemon runs), the dashboard's own stop, ``status`` and the doctor's lines. The host starts the dashboard (``python -m
ap_coder serve --server ...``) with the server settings, writes what it says to the rotating log ``<data
folder>/logs/service.log``, and starts it again when it stops (after 5 s, then 10 s, ... up to 5 minutes), until the
service is stopped: the ``stopped`` file ``service stop`` leaves, or, off Windows, SIGTERM or SIGINT (launchd's
bootout, Ctrl+C), on which it stops the dashboard and ends with exit code 0 (launchd then leaves it stopped).

Settings: ``<data folder>/server.json`` (written by ``service install``; ``server-update`` keeps its record there
too), each overridden by an environment variable (:data:`ENV_SETTINGS`; the data folder's .env, which ``service run``
reads into its environment, too, but for the backup model servers: :data:`OVER_ENV_FILE`). Which folder the service
runs is one thing ``server-update`` changes (``switch_folder``): Windows' ``run.cmd``, the Mac's ``current`` link, so
the task or the daemon never has to be registered again. Standard library only.
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
import signal
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import __version__
from ..core.paths import PROJECT_DIR, private_dir, same_folder, try_lock

WINDOWS = os.name == "nt"
MACOS = sys.platform == "darwin"
CONFIG_FILE = "server.json"  # in the data folder
SERVICE_DIR = "service"  # in the data folder: host.json, host.lock, stopped (and Windows' run.cmd, the task's XML)
LOG_DIR = "logs"
LOG_FILE = "service.log"
TASK_NAME = "AP Coder"
DEFAULT_ACCOUNT = "APCoderService"  # a local account made for AP Coder only (docs/SERVER.md)
DEFAULT_PORT = 8501
DEFAULT_HOST = "0.0.0.0"  # every network card of the server: staff open it from their browsers
SERVER_FILE = ".ap_coder_server.json"  # written by the running dashboard (web/server.py)
# The environment variables that override server.json (the TLS and host names are those `serve --server` reads too).
ENV_SETTINGS = {"port": "AP_SERVER_PORT", "host": "AP_DASHBOARD_ADDRESS", "certfile": "AP_TLS_CERT",
                "keyfile": "AP_TLS_KEY", "allowed_hosts": "AP_ALLOWED_HOSTS", "proxy": "AP_SERVER_PROXY",
                "workers": "AP_SERVER_WORKERS", "llm_parallel": "AP_LLM_PARALLEL",
                "worker_threads": "AP_WORKER_THREADS", "capture_processes": "AP_CAPTURE_PROCESSES",
                "llm_backup_url": "AP_LLM_BACKUP_URL",
                "page_reader_backup_url": "AP_PAGE_READER_BACKUP_URL"}  # fmt: skip
# The settings server.json gives that win over the data folder's .env, which ``service run`` reads into its own
# environment as every AP Coder process does: the backup model servers, given to the dashboard and left by its form
# (D-180). The machine's own environment still wins.
OVER_ENV_FILE = ("llm_backup_url", "page_reader_backup_url")
RESTART_FIRST, RESTART_MOST, STEADY = 5.0, 300.0, 600.0  # seconds: first wait, longest wait, a run that counts as fine
STOP_WAIT = 45.0  # seconds the dashboard has to stop once the host is asked to (its workers give requests 25: D-111)
# The host's exit code off Windows for "start me again" (EX_TEMPFAIL; ``again``): launchd's KeepAlive starts it again
# 10 s later, where a 0 would leave it stopped. Another host runs, or the folder to run changed (an update's recovery).
HOST_AGAIN = 75
# The Windows task's names, found here as before they moved to service_windows.py (``__getattr__``).
WINDOWS_NAMES = ("BUILT_IN", "runner_path", "task_file", "runner_text", "write_runner", "account_sid", "task_xml",
                 "create_command", "tie_to_host")  # fmt: skip

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
    allowed_folders: tuple[str, ...] = ()  # the folders an Admin may name on the Settings pages (D-109)
    workers: int = 0  # web worker processes (D-111); 0: for the cores and the memory (``server.default_workers``)
    llm_parallel: int = 1  # requests sent to LM Studio (or another model server) at once (D-111)
    worker_threads: int = 0  # requests each web worker runs at once (D-153); 0: ``app.WORKER_THREADS``
    capture_processes: int = 0  # processes reading the import queue (D-168); 0: ``server.capture_processes``' own
    llm_backup_url: str = ""  # the chat model's backup model server (D-180); "": none
    page_reader_backup_url: str = ""  # the page reader's, when it has a server of its own (D-180)

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
    """Set these values in server.json, keeping every other one (``write_whole``)."""
    merged = {**read_config(data), **values}
    return write_whole(config_path(data), json.dumps(merged, indent=2).encode("utf-8"))


def write_whole(path: Path, content: bytes, mode: int | None = None,
                prepare: Callable[[Path], None] | None = None) -> Path:  # fmt: skip
    """``path`` written whole or not at all, a power cut included: a temporary file beside it, flushed to the disk
    (fsync), then renamed over it (``os.replace``), the folder flushed too (``sync_folder``). What the service starts
    from (server.json, Windows' run.cmd, the Mac's LaunchDaemon) is never left empty or cut short. ``mode`` and
    ``prepare`` (its owner) are set on the temporary file, before it takes the name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f".{path.name}.{os.getpid()}.part")  # (a dot first, and not .plist: launchd reads none)
    try:
        with open(part, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(part, mode)
        if prepare is not None:
            prepare(part)
        os.replace(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    sync_folder(path.parent)
    return path


def sync_folder(folder: Path) -> None:
    """A rename in ``folder`` flushed to the disk (off Windows: Windows opens no folder, and NTFS logs a rename)."""
    if WINDOWS:
        return
    with contextlib.suppress(OSError):
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _hosts(value: Any) -> tuple[str, ...]:
    items = value.split(",") if isinstance(value, str) else list(value or [])
    return tuple(str(h).strip() for h in items if str(h).strip())


def _folders(value: Any) -> tuple[str, ...]:
    items = value.split(";") if isinstance(value, str) else list(value or [])
    return tuple(str(f).strip().strip('"') for f in items if str(f).strip().strip('"'))


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.strip().strip('"')))


def folder_refusal(folder: str, data: Path | None = None, server: bool | None = None) -> str:
    """Why a folder an Admin typed (the backups' second copy, the finance hub's folder) can't be used ("" = it can).
    On the server it must be one of server.json's ``allowed_folders``, set by IT (``service install
    --allowed-folders``), or inside one, compared as text before anything is asked of it: a network path from the web
    would make the server's account sign in there (its password hash sent), or copy the invoices out (D-109). Laptop:
    any folder, as before."""
    from .offline import server_mode

    if not (server_mode() if server is None else server) or not str(folder or "").strip():
        return ""
    try:
        allowed = _folders(read_config(data or private_dir()).get("allowed_folders"))
    except SettingsError:
        allowed = ()
    target = _norm(str(folder))
    for root in allowed:
        base = _norm(root).rstrip("\\/")
        if base and (target == base or target.startswith(base + os.sep)):
            return ""
    listed = "; ".join(allowed) if allowed else "none yet"
    return (f"On the server, AP Coder writes only to the folders IT allows ({listed}): {folder} is not one of them, "
            "nor inside one. Ask IT to add it (service install --allowed-folders, docs/SERVER.md section "
            "9).")  # fmt: skip


def session_limits(data: Path) -> tuple[float, float]:
    """(idle, absolute) seconds of a login session on the server: server.json's ``session_idle_minutes`` (default
    30, from 5 to 480) and ``session_hours`` (default 12, from 1 to 168), set by IT (D-109)."""
    try:
        found = read_config(data)
    except SettingsError:
        found = {}

    def number(key: str, default: float, low: float, high: float) -> float:
        try:
            value = float(found.get(key) or default)
        except (TypeError, ValueError):
            return default
        return min(max(value, low), high) if value == value else default

    return number("session_idle_minutes", 30, 5, 480) * 60, number("session_hours", 12, 1, 168) * 3600


def settings(data: Path, env: dict | None = None, **overrides: Any) -> ServerSettings:
    """The settings in effect: the defaults, then server.json, then the environment (:data:`ENV_SETTINGS`: the data
    folder's .env too, once read into it, but for :data:`OVER_ENV_FILE`), then ``overrides`` (the command line; None =
    not given)."""
    from ..core.config import from_env_file

    env = os.environ if env is None else env
    values: dict[str, Any] = {k: v for k, v in read_config(data).items() if k in FIELDS}
    for name, var in ENV_SETTINGS.items():
        given = env.get(var) or ""
        if given.strip() and not (name in OVER_ENV_FILE and values.get(name) and from_env_file(var, given)):
            values[name] = given.strip()
    values.update({k: v for k, v in overrides.items() if v is not None})
    values["allowed_hosts"] = _hosts(values.get("allowed_hosts"))
    values["allowed_folders"] = _folders(values.get("allowed_folders"))
    try:
        values["port"] = int(values.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        raise SettingsError(f"The port {values.get('port')!r} is not a number.") from None
    values["insecure_http"] = values.get("insecure_http") in (True, "1", "true", "yes", "on")
    for name, low, high in (("workers", 0, 64), ("llm_parallel", 1, 32), ("worker_threads", 0, 40),
                            ("capture_processes", 0, 64)):  # fmt: skip
        try:
            values[name] = min(high, max(low, int(values.get(name, getattr(ServerSettings, name)))))
        except (TypeError, ValueError):
            raise SettingsError(f"{name} {values.get(name)!r} is not a number.") from None
    for name in ("host", "certfile", "keyfile", "proxy", "account", "task_name", "folder", "llm_backup_url",
                 "page_reader_backup_url"):  # fmt: skip
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
    values["allowed_folders"] = list(s.allowed_folders)
    values["folder"] = str(s.code_folder)
    return write_config(data, **values)


# --- What the service runs --------------------------------------------------------------------------------------------


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
    from ap_coder.web.server import default_workers

    return [*cmd, "--workers", str(s.workers or default_workers())]


def serve_env(s: ServerSettings, data: Path) -> dict[str, str]:
    """What the dashboard's environment gets on top of the host's."""
    env = {"AP_PRIVATE_DIR": str(data), "AP_SERVER_MODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    if s.allowed_hosts:
        env["AP_ALLOWED_HOSTS"] = ",".join(s.allowed_hosts)
    env["AP_LLM_PARALLEL"] = str(s.llm_parallel)
    if s.worker_threads:
        env["AP_WORKER_THREADS"] = str(s.worker_threads)
    if s.capture_processes:
        env["AP_CAPTURE_PROCESSES"] = str(s.capture_processes)
    for name, var in (("llm_backup_url", "AP_LLM_BACKUP_URL"), ("page_reader_backup_url", "AP_PAGE_READER_BACKUP_URL")):
        if getattr(s, name):
            env[var] = getattr(s, name)
    return env


def service_dir(data: Path) -> Path:
    return data / SERVICE_DIR


def stop_flag(data: Path) -> Path:
    return service_dir(data) / "stopped"


def write_stop_flag(data: Path, again: str = "python -m ap_coder service start") -> Path:
    """The ``stopped`` file: the host started meanwhile (the task's next run, the Mac's next start) ends at once."""
    flag = stop_flag(data)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(f"stopped {dt.datetime.now():%Y-%m-%d %H:%M} ({again})\n", encoding="utf-8")
    return flag


def host_record(data: Path) -> Path:
    return service_dir(data) / "host.json"


def host_lock(data: Path) -> Path:
    """Held by the running host (``try_lock``): the operating system releases it when the host ends, killed or not."""
    return service_dir(data) / "host.lock"


def host_running(data: Path) -> bool:
    """A service host runs: it holds ``host_lock``. Not host.json's process number, which after a reboot or a kill
    may be another program's (B-1588). Told by taking the lock, released at once when it was free; never made here
    (no host ran yet): made by an administrator's sudo, it would be root's, and the Mac's host could not take it."""
    if not host_lock(data).exists():
        return False
    lock = try_lock(host_lock(data))
    if lock is None:
        return True
    lock.close()
    return False


def log_path(data: Path) -> Path:
    return data / LOG_DIR / LOG_FILE


def run_command(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Run a command of the service (schtasks, taskkill, launchctl). The one place they run: tests record them."""
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


def process_image(pid: int) -> str:
    """The program file a Windows process runs, its full path ("" when it can't be told, and off Windows)."""
    if not WINDOWS or pid <= 0:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes_kernel32()
        kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                      ctypes.POINTER(wintypes.DWORD))  # fmt: skip
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            name = ctypes.create_unicode_buffer(size.value)
            return name.value if kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)) else ""
        finally:
            kernel.CloseHandle(handle)
    except (OSError, AttributeError):
        return ""


def runs_python_of(pid: int, folder: Path) -> bool:
    """The process ``pid`` runs a Python of the AP Coder folder ``folder``: its .venv's, or the Python that .venv was
    made with (a .venv's python.exe starts that one, which is the process that writes its number). host.json and
    .ap_coder_server.json are in the data folder, which the service's account can write (B-1500): a process they name
    is ended only when it is one of these."""
    image = process_image(pid)
    if not image:
        return False
    python = folder_python(folder)
    pythons = [python]
    with contextlib.suppress(OSError, UnicodeDecodeError):
        for line in (folder / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home" and value.strip():
                pythons.append(Path(value.strip()) / python.name)
    return any(same_folder(image, p) for p in pythons)


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


def dashboard_stopped(data: Path, s: ServerSettings, wait: float = 45.0) -> bool:
    """Ask the running dashboard to stop (``POST /shutdown`` with its token) and wait for its process to end; ended
    when it won't (its web workers give the requests under way 25 seconds, its background process the page under
    way 10 more: D-111). True when no dashboard of this data folder runs any more."""
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
    if runs_python_of(pid, s.code_folder):
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


class StopSignals:
    """SIGTERM and SIGINT, off Windows (launchd's bootout, Ctrl+C): the host asks its dashboard to stop (SIGTERM), ends
    it STOP_WAIT seconds later if it hasn't, then ends itself with exit code 0. Taken from ``install`` (in the main
    thread) to ``restore``; Windows' host is ended with its task, its job object ending the dashboard (``tie_to_host``).
    """

    def __init__(self) -> None:
        self.got = 0  # the signal received (0: none yet)
        self.child: Any = None  # the dashboard, while it runs
        self._before: dict[int, Any] = {}

    def install(self) -> StopSignals:
        if not WINDOWS and threading.current_thread() is threading.main_thread():
            for number in (signal.SIGTERM, signal.SIGINT):
                self._before[number] = signal.signal(number, self._received)
        return self

    def restore(self) -> None:
        for number, handler in self._before.items():
            signal.signal(number, handler)

    def _received(self, number: int, frame: Any) -> None:
        self.got = number
        self.end_child()

    def end_child(self) -> None:
        """Ask the dashboard to stop, and end it once STOP_WAIT has gone by (a thread: the host reads its output)."""
        child = self.child
        if child is None:
            return
        with contextlib.suppress(OSError):
            child.terminate()
        timer = threading.Timer(STOP_WAIT, self._end, args=(child,))
        timer.daemon = True
        timer.start()

    @staticmethod
    def _end(child: Any) -> None:
        if child.poll() is None:
            with contextlib.suppress(OSError):
                child.kill()


def _wait(seconds: float, ended: Callable[[], bool], sleep: Callable[[float], None]) -> None:
    """Wait ``seconds``, a second at a time, ending early once the service is stopped."""
    left = seconds
    while left > 0 and not ended():
        step = min(1.0, left)
        sleep(step)
        left -= step


def again() -> int:
    """The host's exit code when it must not run now but be started again soon (another host runs; the folder to
    run changed, so the one it names starts next): 0 on Windows, as before, its task comes back every 5 minutes by
    itself; elsewhere :data:`HOST_AGAIN`, as launchd's KeepAlive starts again only a host that ended with another
    code than 0 (a 0 is a stop on purpose, left stopped)."""
    return 0 if WINDOWS else HOST_AGAIN


def run_host(
    data: Path,
    popen: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Keep the dashboard running until the service is stopped: the ``stopped`` file, or (off Windows) SIGTERM or
    SIGINT (``StopSignals``). Settings are read again at each start, so a change in server.json applies at the next
    restart. The exit code is what launchd's KeepAlive (SuccessfulExit false) goes by: 0 when stopped on purpose (left
    stopped); ``again()`` to be started again soon; a crash or a kill, started again."""
    from ..core.paths import env_path
    from .update_recovery import recover_at_start

    # An update cut short (a power cut): acted on first, its own stop too (D-188). False: the folder to run is another
    # now (run.cmd, or the Mac's ``current`` link), which the service starts next: ``again()``.
    if not recover_at_start(data, env_path("AP_DB_PATH") or data / "ap_coder.db", PROJECT_DIR):
        return again()
    if stop_flag(data).exists():
        return 0  # stopped on purpose: the repeating trigger (the Mac's next start) starts nothing
    # A host is running already when it holds the lock. Not when host.json names a live process: after a reboot or a
    # kill that number may be another program's, and the service would stay down (B-1585).
    lock = try_lock(host_lock(data))
    if lock is None:
        return again()  # (Windows' task comes back in 5 minutes; launchd within 10 s)
    record = host_record(data)  # at once: while the lock is held, host.json is this host's (``stop`` relies on it)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"pid": os.getpid(), "started": dt.datetime.now().isoformat(timespec="seconds"),
                                  "folder": str(PROJECT_DIR), "version": __version__}), encoding="utf-8")  # fmt: skip
    log = host_logger(data)
    log.info("Service host started (AP Coder %s, %s, process %s)", __version__, PROJECT_DIR, os.getpid())
    delay = RESTART_FIRST
    signals = StopSignals().install()

    def ended() -> bool:
        return bool(signals.got) or stop_flag(data).exists()

    try:
        while not signals.got:
            try:
                s = settings(data)
            except SettingsError as exc:
                log.error("%s", exc)
                _wait(RESTART_MOST, ended, sleep)
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
                if WINDOWS:
                    _backend().tie_to_host(child)
                signals.child = child
                if signals.got:  # (it came while the dashboard was being started)
                    signals.end_child()
                for line in child.stdout:
                    log.info("dashboard: %s", line.rstrip())
                code = child.wait()
                signals.child = None
            if signals.got:
                log.info("The dashboard stopped (exit code %s): the host was asked to stop (%s)", code,
                         signal.Signals(signals.got).name)  # fmt: skip
                break
            if stop_flag(data).exists():
                log.info("The dashboard stopped (exit code %s): the service was stopped", code)
                break
            if clock() - started >= STEADY:
                delay = RESTART_FIRST
            log.warning("The dashboard stopped (exit code %s): starting it again in %d s", code, delay)
            _wait(delay, ended, sleep)
            if ended():
                log.info("The service was stopped")
                break
            delay = min(delay * 2, RESTART_MOST)
    finally:
        signals.restore()
        with contextlib.suppress(OSError):
            if int(_read_json(record).get("pid") or 0) == os.getpid():
                record.unlink()
        lock.close()
    return 0


# --- install / remove / start / stop / status: this computer's service ------------------------------------------------


def backend() -> Any:
    """This computer's service: Windows' scheduled task (``service_windows``), the Mac's LaunchDaemon
    (``service_macos``); None elsewhere."""
    if WINDOWS:
        from . import service_windows

        return service_windows
    if MACOS:
        from . import service_macos

        return service_macos
    return None


def _backend() -> Any:
    """``backend()``, or Windows' where there is none (``install --dry-run`` shows its task anywhere)."""
    from . import service_windows

    return backend() or service_windows


def __getattr__(name: str) -> Any:
    """The Windows task's names (:data:`WINDOWS_NAMES`), found here as before they moved to service_windows.py."""
    if name in WINDOWS_NAMES:
        from . import service_windows

        return getattr(service_windows, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def start(data: Path, s: ServerSettings, say: Say = print) -> int:
    return _backend().start(data, s, say)


def stop(data: Path, s: ServerSettings, say: Say = print) -> int:
    """Stop the dashboard and its host, and keep them from starting again (at boot too) until ``service start``."""
    return _backend().stop(data, s, say)


def install(data: Path, s: ServerSettings, args: argparse.Namespace, say: Say = print) -> int:
    return _backend().install(data, s, args, say)


def remove(data: Path, s: ServerSettings, say: Say = print) -> int:
    return _backend().remove(data, s, say)


def installed(s: ServerSettings) -> bool:
    """This computer's service is installed (False where there is none: ``server-update`` then stops nothing)."""
    found = backend()
    return bool(found and found.installed(s))


def switch_folder(data: Path, folder: Path) -> None:
    """The service runs ``folder`` from its next start (``server-update``): the Mac's ``current`` link pointed at it,
    elsewhere Windows' run.cmd rewritten."""
    _backend().switch_folder(data, folder)


def address(s: ServerSettings) -> str:
    name = s.allowed_hosts[0] if s.allowed_hosts else "<this server's name>"
    scheme = "https" if s.https else "http"
    return f"{scheme}://{name}:{s.port}" if s.port != (443 if s.https else 80) else f"{scheme}://{name}"


def status_info(data: Path, s: ServerSettings, found: dict) -> dict:
    """What ``service status --json`` says: the service (``found``, its backend's ``state``), the host, the dashboard's
    ``/healthz`` answer (None when none answers), where its settings and log are."""
    running = host_running(data)
    host = _read_json(host_record(data)) if running else {}
    health = dashboard_health(data, s)
    dashboard = int(_read_json(data / SERVER_FILE).get("pid") or 0) if health else 0
    return {"platform": _backend().NAME, **{k: v for k, v in found.items() if k not in ("line", "missing")},
            "running": running, "pid": host.get("pid"), "started": host.get("started"),
            "version": host.get("version") or (health or {}).get("version"),
            "folder": host.get("folder") or (health or {}).get("folder"), "address": address(s),
            "answering": health is not None, "health": health, "dashboard_pid": dashboard or None,
            "stopped_on_purpose": stop_flag(data).exists(), "data": str(data), "settings": str(config_path(data)),
            "log": str(log_path(data))}  # fmt: skip


def status(data: Path, s: ServerSettings, say: Say = print, as_json: bool = False) -> int:
    """The service, its host and the dashboard, in words (or JSON); 1 when the service is not installed."""
    way = _backend()
    found = way.state(data, s)
    if as_json:
        say(json.dumps(status_info(data, s, found), indent=2))
        return 0 if found["installed"] else 1
    if not found["installed"]:
        say(found["missing"])
        return 1
    say(found["line"])
    host = _read_json(host_record(data))
    if host_running(data):
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
        say(f"Stopped on purpose: {way.AGAIN} starts it again.")
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


def doctor_checks(data: Path, env: dict | None = None, *, windows: bool | None = None,
                  macos: bool | None = None) -> list[tuple[str, str, str]]:  # fmt: skip
    """(area, status, detail) of the server's own setup, as the doctor shows them: what it listens on, its HTTPS
    files, the names staff type and the service: Windows' scheduled task, the Mac's LaunchDaemon (this computer's,
    unless ``windows`` or ``macos`` is given). Reads files only (and asks schtasks or launchctl)."""
    if windows is None and macos is None:
        windows, macos = WINDOWS, MACOS
    try:
        s = settings(data, env)
    except SettingsError as exc:
        return [("server settings", "FAIL", str(exc))]
    out = [("dashboard network", "PASS", f"the company server: listens on {s.host}, port {s.port}; staff open "
            f"{address(s)}")]  # fmt: skip
    if s.insecure_http:
        out.append(("server HTTPS", "WARN", "off (--insecure-http): passwords cross the network unencrypted; for a "
                    "first test only"))  # fmt: skip
    elif not s.certfile or not s.keyfile:  # a server started with them on its command line has them (B-1196)
        out.append(("server HTTPS", "WARN", "the certificate and its key are not in server.json nor AP_TLS_CERT and "
                    "AP_TLS_KEY: the service doesn't start without them (service install --certfile --keyfile). The "
                    "doctor can't see a running server's command line: one started with serve --certfile --keyfile "
                    "has them"))  # fmt: skip
    else:
        for what, path in (("certificate", s.certfile), ("private key", s.keyfile)):
            why = _readable(path)
            out.append((f"server HTTPS {what}", "FAIL" if why else "PASS", f"{path}: {why}" if why else path))
    if s.allowed_hosts:
        out.append(("server names", "PASS", ", ".join(s.allowed_hosts)))
    else:
        out.append(("server names", "WARN", "AP_ALLOWED_HOSTS is not set: only this computer's own names open AP "
                    "Coder; add the name staff type (service install --allowed-hosts)"))  # fmt: skip
    if windows:
        from . import service_windows

        return [*out, service_windows.doctor_check(data, s)]
    if macos:
        from . import service_macos

        return [*out, service_macos.doctor_check(data, s)]
    out.append(("server service", "SKIP", "the service is a scheduled task on Windows, a LaunchDaemon on a Mac"))
    return out


def add_parser(sub: Any) -> None:
    p = sub.add_parser("service", help="The company server: run AP Coder as a Windows scheduled task or a Mac's "
                       "LaunchDaemon (docs/SERVER.md)")  # fmt: skip
    p.add_argument("action", choices=["install", "remove", "start", "stop", "status", "run"],
                   help="run: what the scheduled task (the LaunchDaemon) runs: the service host")  # fmt: skip
    p.add_argument("--account", default=None, help=f"The account it runs as (default {DEFAULT_ACCOUNT} on Windows, "
                   "_apcoder on a Mac)")  # fmt: skip
    p.add_argument("--task-name", default=None, help=f'The scheduled task\'s name (default "{TASK_NAME}"); on a Mac '
                   "the LaunchDaemon's label is made from it (default com.apcoder.server)")  # fmt: skip
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
    p.add_argument("--allowed-folders", default=None,
                   help="The folders Admins may name for backup copies and the finance hub, ;-separated")  # fmt: skip
    p.add_argument("--workers", type=int, default=None,
                   help="Web worker processes (default: for the cores and memory; 1: all in one process)")  # fmt: skip
    p.add_argument("--llm-parallel", type=int, default=None,
                   help="Requests sent to LM Studio at once (default 1; docs/SERVER.md, step 8)")  # fmt: skip
    p.add_argument("--no-start", action="store_true", help="install: don't start it yet")
    p.add_argument("--dry-run", action="store_true", help="install: show what it would do, change nothing")
    p.add_argument("--allow-root", action="store_true", help="install on a Mac: run it as root (a first test only)")
    p.add_argument("--json", action="store_true", help="status: in JSON (for a monitoring script)")


def main(args: argparse.Namespace) -> int:
    data = private_dir()
    if args.action == "run":
        return run_host(data)
    if backend() is None and not (args.action == "install" and args.dry_run):
        print("The service is for Windows (Task Scheduler) and macOS (launchd). Elsewhere, run: python -m ap_coder "
              "serve --server", file=sys.stderr)  # fmt: skip
        return 2
    way = _backend()
    refused = way.command_refusal(args)
    if refused:
        print(refused, file=sys.stderr)
        return 1
    data = way.data_folder(data, args.task_name or TASK_NAME)  # (the Mac: its LaunchDaemon's when sudo left it out)
    try:
        given = {
            name: getattr(args, name)
            for name in (
                "port",
                "host",
                "certfile",
                "keyfile",
                "allowed_hosts",
                "proxy",
                "insecure_http",
                "allowed_folders",
                "workers",
                "llm_parallel",
            )
        }
        # Never server.json's (B-1500): the service's account can write it, and these say what the task runs, as
        # whom, and which task the administrator's schtasks (launchctl) changes. A built-in account (root) only when
        # named here.
        given["account"] = args.account or way.DEFAULT_ACCOUNT
        given["task_name"] = args.task_name or TASK_NAME
        given["folder"] = str(Path(args.folder).resolve()) if args.folder else str(PROJECT_DIR)
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
        "status": lambda: status(data, s, as_json=args.json),
    }
    return actions[args.action]()
