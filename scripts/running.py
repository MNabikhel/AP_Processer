"""AP Coder servers already running on this computer: which version, from which folder, and stopping an older one.

Never two versions on one database: before a start sets anything up, the launcher (scripts/launch.py) looks for an
AP Coder that is already running. The same folder and version is simply opened in the browser (a second double-click);
an older version, or one from another folder, is stopped first; a newer one is opened, never stopped (this older
version would downgrade nothing, it just steps aside).

How a running AP Coder is found:

* AP Coder 0.2 and later (``python -m ap_coder serve``) write ``<data folder>/.ap_coder_server.json`` (pid, port,
  folder, version, token, started) and answer ``GET /healthz`` with ``{"ok", "app": "ap-coder", "version", "folder",
  "pid", "port"}``; ``POST /shutdown`` with the header ``X-AP-Coder-Token`` (the token from that file) stops one.
* AP Coder 0.1 (the Streamlit dashboard) only serves its font, ``/app/static/InterVariable.woff2``: it is stopped
  through its process, found as the owner of its port whose command line runs ``streamlit run`` on AP Coder's
  dashboard.

When none of that works (another user's process, no permission), the person is asked to close the old window, and
the start waits for it (up to a minute). Standard library only: it runs before any package is installed. Never
through a proxy: every request goes to 127.0.0.1.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ap_coder.offline import listening as offline_listening  # noqa: E402  (standard library only)
from ap_coder.paths import same_folder, version_key  # noqa: E402  (standard library only)

SERVER_FILE = ".ap_coder_server.json"  # in the data folder, written by `python -m ap_coder serve`
TOKEN_HEADER = "X-AP-Coder-Token"
APP = "ap-coder"
LEGACY_SIGNATURE = "/app/static/InterVariable.woff2"  # a file only AP Coder 0.1's Streamlit dashboard serves
WINDOWS = os.name == "nt"
STOP_WAIT = 20.0  # seconds a server is given to stop after it was asked to
PATIENCE = 60.0  # seconds the person is given to close an old window that could not be stopped
stopped: list[str] = []  # the folders of the older AP Coders this start stopped (to clean up after the update)


@dataclass
class Server:
    port: int
    version: str = ""
    folder: str = ""
    pid: int | None = None
    token: str = ""
    legacy: bool = False  # AP Coder 0.1's Streamlit dashboard: no /healthz, stopped through its process

    def name(self) -> str:
        version = "0.1 (the Streamlit dashboard)" if self.legacy else (self.version or "of an unknown version")
        where = f" from {self.folder}" if self.folder else ""
        return f"AP Coder {version}{where}"


# --- Finding one ------------------------------------------------------------------------------------------------


def listening(port: int) -> bool:
    """Something accepts connections on ``port`` here (``ap_coder/offline.py:listening``: 0.3 s at most)."""
    return offline_listening(port)


def _open(request: urllib.request.Request | str, timeout: float):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout)


def health(port: int, timeout: float = 3.0) -> dict | None:
    """What an AP Coder 0.2 server on ``port`` says about itself (``/healthz``), or None (nothing, or not ours)."""
    if not listening(port):
        return None
    try:
        with _open(f"http://127.0.0.1:{port}/healthz", timeout) as response:
            found = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) and found.get("app") == APP else None


def legacy_dashboard(port: int, timeout: float = 3.0) -> bool:
    """AP Coder 0.1's Streamlit dashboard answers on ``port``."""
    if not listening(port):
        return False
    try:
        with _open(f"http://127.0.0.1:{port}{LEGACY_SIGNATURE}", timeout) as response:
            return response.status == 200
    except OSError:
        return False


def server_record(data: Path | None) -> dict:
    """``<data folder>/.ap_coder_server.json``, written by the running server ({} when there is none)."""
    if data is None:
        return {}
    try:
        found = json.loads((data / SERVER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _port(value: object) -> int | None:
    try:
        port = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def find(ports: list[int], data: Path | None) -> list[Server]:
    """Every AP Coder running on one of ``ports`` (or on the port its server file names), newest kind first."""
    record = server_record(data)
    candidates = list(dict.fromkeys(p for p in [_port(record.get("port")), *ports] if p))
    if len(candidates) > 1:  # all at once: a closed port takes up to 0.3 s to tell on Windows
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(len(candidates), 16)) as pool:
            candidates = [p for p, up in zip(candidates, pool.map(listening, candidates), strict=True) if up]
    found = []
    for port in candidates:
        info = health(port)
        if info is not None:
            pid = _port(info.get("pid")) if str(info.get("pid", "")).isdigit() else None
            token = str(record.get("token") or "") if _port(record.get("port")) == port else ""
            found.append(Server(port, str(info.get("version") or ""), str(info.get("folder") or ""), pid, token))
        elif legacy_dashboard(port):
            pid = port_owner(port)
            folder = folder_in_command(command_line(pid)) if pid else ""
            found.append(Server(port, folder=folder, pid=pid, legacy=True))
    return found


def judge(server: Server, root: Path, version: str) -> str:
    """``same`` (this folder and version: open it), ``newer`` (open it, never stop it) or ``older`` (stop it: an
    older version, or the same one from another folder)."""
    if server.legacy:
        return "older"
    if version_key(server.version) > version_key(version):
        return "newer"
    if same_folder(server.folder, root) and server.version == version:
        return "same"
    return "older"


# --- Stopping one -----------------------------------------------------------------------------------------------


def ask_to_stop(server: Server, timeout: float = 5.0) -> bool:
    """``POST /shutdown`` with the server's token (from its server file). True when it said yes."""
    if not server.token:
        return False
    headers = {TOKEN_HEADER: server.token, "Content-Type": "application/json"}
    request = urllib.request.Request(f"http://127.0.0.1:{server.port}/shutdown", b"{}", headers, method="POST")
    try:
        with _open(request, timeout) as response:
            return 200 <= response.status < 300
    except OSError:
        return False


def gone(port: int, timeout: float, every: float = 0.5) -> bool:
    """``port`` stops answering within ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while listening(port):
        if time.monotonic() >= deadline:
            return False
        time.sleep(every)
    return True


def _run(cmd: list[str], timeout: float = 20) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout if out.returncode == 0 else ""


def powershell(script: str, timeout: float = 20) -> str:
    """What a Windows PowerShell ``script`` prints ("" when it fails), read as UTF-8: by default it writes in the
    console's code page and Python reads the ANSI one, so a folder named "Données" came back as "Donn‚es"."""
    utf8 = "try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}; "
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", utf8 + script], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=timeout)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip().lstrip("﻿") if out.returncode == 0 else ""


def _linux_port_owner(port: int) -> int | None:
    """The process listening on ``port``, from /proc (Linux): the socket's inode, then the process holding it."""
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for row in lines:
            fields = row.split()
            if len(fields) > 9 and fields[3] == "0A" and int(fields[1].rsplit(":", 1)[1], 16) == port:
                inodes.add(fields[9])
    if not inodes:
        return None
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc / "fd").iterdir():
                link = os.readlink(fd)
                if link.startswith("socket:[") and link[8:-1] in inodes:
                    return int(proc.name)
        except OSError:
            continue
    return None


def port_owner(port: int) -> int | None:
    """The process id listening on ``port`` on this computer, or None when it can't be told."""
    if WINDOWS:
        script = (f"(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | "
                  "Select-Object -First 1).OwningProcess")  # fmt: skip
        found = powershell(script)
        return int(found) if found.isdigit() else None
    if sys.platform.startswith("linux"):
        return _linux_port_owner(port)
    found = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]).split()
    return int(found[0]) if found and found[0].isdigit() else None


def command_line(pid: int) -> str:
    """The command line of process ``pid`` ("" when it can't be read)."""
    if WINDOWS:
        script = f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'
        return powershell(script)
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        return _run(["ps", "-o", "command=", "-p", str(pid)]).strip()


def is_ap_coder_process(cmd: str, legacy: bool) -> bool:
    """The command line of an AP Coder server: ``streamlit run ...ap_coder...dashboard.py`` (0.1), or
    ``-m ap_coder serve`` / ``-m ap_coder dashboard`` (0.2). Nothing else is ever stopped."""
    text = cmd.replace("\\", "/").lower()
    if legacy:
        return bool(re.search(r"streamlit(\.exe|\.py)?[\"']?\s+run\b", text)) and "ap_coder" in text
    return bool(re.search(r"-m\s+ap_coder\s+(serve|dashboard)\b", text))


def folder_in_command(cmd: str) -> str:
    """The AP Coder folder an AP Coder 0.1 dashboard runs from: ``...streamlit run <folder>/ap_coder/dashboard.py``."""
    found = re.search(r"\brun\s+[\"']?(.+?)[\\/]ap_coder[\\/]dashboard\.py", cmd)
    return found.group(1) if found else ""


def terminate(pid: int) -> None:
    """Ask process ``pid`` to end (SIGTERM; on Windows, the process and the ones it started)."""
    if WINDOWS:
        _run(["taskkill", "/PID", str(pid), "/T", "/F"])
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def stop(server: Server, wait: float = STOP_WAIT) -> bool:
    """Stop ``server``: asked through ``/shutdown`` when its token is known, else its process (found by its port and
    checked to be AP Coder's) is ended. True when its port is free."""
    if ask_to_stop(server) and gone(server.port, wait):
        return True
    pid = port_owner(server.port) or server.pid
    if pid and pid != os.getpid() and is_ap_coder_process(command_line(pid), server.legacy):
        terminate(pid)
        return gone(server.port, wait)
    return not listening(server.port)


def take_over(root: Path, version: str, ports: list[int], data: Path | None,
              say: Callable[[str], None] = print, patience: float = PATIENCE) -> tuple[str, int | None]:  # fmt: skip
    """Make way for AP Coder ``version`` from ``root``. Returns what to do:

    * ``("open", port)``: this same folder and version is running: open it (nothing to set up);
    * ``("newer", port)``: a newer version is running: open it (this older one never takes over from it);
    * ``("clear", None)``: nothing (left) running: set up and start;
    * ``("blocked", port)``: an older one is still running after ``patience`` seconds: stop."""
    for server in find(ports, data):
        verdict = judge(server, root, version)
        if verdict in ("same", "newer"):
            return ("open" if verdict == "same" else "newer"), server.port
        say(f"{server.name()} is still running (port {server.port}): stopping it, so this version takes over.")
        if stop(server):
            say("  Stopped it.")
            stopped.append(server.folder)
            continue
        say("  Close the old AP Coder window, then start again. (Waiting up to a minute for it to close...)")
        if not gone(server.port, patience, every=1.0):
            return "blocked", server.port
        say("  It closed.")
    return "clear", None
