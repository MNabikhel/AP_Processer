"""Run the dashboard: ``python -m ap_coder serve [--host 127.0.0.1] [--port 8501] [--no-browser]``; as the company's
server, ``serve --server --certfile cert.pem --keyfile key.pem [--proxy <ip>]`` (HTTPS, D-99).

The server runs in this process (uvicorn). Once it answers, it prints ``AP Coder is running at http://127.0.0.1:N``
(the launcher waits for that line, or for ``GET /healthz``) and opens the browser, unless ``--no-browser``,
``BROWSER=none`` or a Linux computer with no desktop (``offline.browser_wanted``).

While it runs, ``<data folder>/.ap_coder_server.json`` says which server this is: {"pid", "port", "folder",
"version", "token", "user" (the computer account running it), "started"}. The launcher stops it with
``POST /shutdown`` and the header ``X-AP-Coder-Token: <token>``; the file is removed when the server stops.

On the company server with ``workers`` > 1 (D-111): uvicorn's supervisor runs that many web workers
(``worker_app``), and ``BackgroundWork`` keeps one ``serve --background-work`` process (``run_background``) beside
them; a stop file asks them all to stop. A busy worker hands its connections back to the others (``HandBack``).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import errno
import json
import logging
import logging.handlers
import os
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio.to_thread

from ap_coder import __version__
from ap_coder.core import paths
from ap_coder.core.store import Store, dialect
from ap_coder.ops import health, system_log
from ap_coder.ops.offline import ADDRESS_ENV, browser_wanted, dashboard_address, server_mode
from ap_coder.web.app import CODE_FOLDER, create_app, os_user, own_work_running

SERVER_FILE = ".ap_coder_server.json"


def server_file() -> Path:
    """Where the running server describes itself: the data folder's ``.ap_coder_server.json``."""
    return paths.private_dir() / SERVER_FILE


def browser_url(host: str, port: int, https: bool = False) -> str:
    """The address to open: listening on every network still opens this computer's own."""
    shown = "127.0.0.1" if host in ("", "0.0.0.0", "::", "localhost") else host
    shown = f"[{shown}]" if ":" in shown else shown
    return f"{'https' if https else 'http'}://{shown}:{port}"


# --- The company server (D-99) ------------------------------------------------------------------------------------

CERT_ENV, KEY_ENV = "AP_TLS_CERT", "AP_TLS_KEY"  # the HTTPS certificate and its private key (PEM files)


def default_host() -> str:
    """Where the dashboard listens when ``--host`` is not given: ``AP_DASHBOARD_ADDRESS``, else this computer only;
    the company server, every network (staff open it from their own PCs)."""
    if (os.environ.get(ADDRESS_ENV) or "").strip() or not server_mode():
        return dashboard_address()
    return "0.0.0.0"


def opens_browser(asked: bool) -> bool:
    """Whether to open a browser once the dashboard is up: never on the server (no one sits at it)."""
    return asked and not server_mode()


def tls_refusal(server: bool, certfile: str | None, keyfile: str | None, insecure_http: bool) -> str:
    """Why the dashboard may not start as asked ("" when it may): the server only over HTTPS (both files, there),
    unless ``--insecure-http`` for a first test. A certificate given on the laptop is used too."""
    if bool(certfile) != bool(keyfile):
        return ("HTTPS needs both files: the certificate (--certfile, or AP_TLS_CERT) and its private key (--keyfile, "
                "or AP_TLS_KEY), each a PEM file.")  # fmt: skip
    for what, path in (("certificate", certfile), ("private key", keyfile)):
        if path and not Path(path).is_file():
            return f"The HTTPS {what} was not found: {path}. Check the path (a PEM file)."
    if server and not certfile and not insecure_http:
        return ("AP Coder runs as the company's server only over HTTPS, so nothing was started: give the certificate "
                "and its private key (--certfile and --keyfile, PEM files; or AP_TLS_CERT and AP_TLS_KEY). For a "
                "first test on a trusted network only, --insecure-http starts it on plain HTTP.")  # fmt: skip
    return ""


IN_USE = {errno.EADDRINUSE, 10048} | ({10013} if os.name == "nt" else set())  # 10013: held exclusively (Windows)


def port_taken(host: str, port: int) -> bool:
    """Whether another program listens on ``port`` at ``host``: a bind test, as uvicorn binds (SO_REUSEADDR off
    Windows, SO_EXCLUSIVEADDRUSE on it), never a connection, which an office proxy or an endpoint agent that
    intercepts connections answers on any port (B-1271). A network this computer lacks (no IPv6) or a port it
    may not use is not "in use": uvicorn says what is wrong then."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        probe = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        return False
    with probe:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        elif os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(({"": "0.0.0.0", "localhost": "127.0.0.1"}.get(host, host), port))
        except OSError as exc:
            return exc.errno in IN_USE or getattr(exc, "winerror", None) in IN_USE
    return False


def port_refusal(host: str, port: int, https: bool = False) -> str:
    """Why the dashboard can't listen on ``port`` ("" when nothing answers there): an AP Coder started earlier, or
    another program. Its address as staff would open it (https on the server). On the company server the port is the
    one IT gave staff, so it is never swapped for another (D-102)."""
    if not port_taken(host, port):
        return ""
    if server_mode():
        return (f"Port {port} is in use by another program (an AP Coder started earlier?): nothing was started. Staff "
                f"open AP Coder at the address IT gave them, with this port, so it is not changed: stop the other "
                f"program (netstat -ano | findstr :{port}), or give AP Coder another port with --port (and tell "
                "staff the new address).")  # fmt: skip
    return (f"Port {port} is in use by another program (an AP Coder started earlier?): nothing was started. Open "
            f"{browser_url(host, port, https)}, or start with another --port.")  # fmt: skip


# TLS 1.2's ciphers (TLS 1.3 has its own, all strong): forward secrecy and AEAD only, never AES-CBC with SHA-1
# (AES128-SHA, which uvicorn's default "TLSv1" allows). Python's own minimum is TLS 1.2 (D-109).
TLS_CIPHERS = "ECDHE+AESGCM:ECDHE+CHACHA20:!aNULL:!eNULL:!MD5:!DSS"


def uvicorn_options(certfile: str | None, keyfile: str | None, proxy: str | None) -> dict[str, Any]:
    """The server's HTTPS files and its proxy setting. The X-Forwarded-For and -Proto headers are believed only from
    the reverse proxy named with ``--proxy`` (its address): never otherwise, not even from this computer (uvicorn's
    own default trusts 127.0.0.1). No ``server: uvicorn`` header: nothing says what serves the pages (D-109)."""
    options: dict[str, Any] = {"proxy_headers": bool(proxy), "server_header": False}
    if proxy:
        options["forwarded_allow_ips"] = proxy
    if certfile and keyfile:
        options["ssl_certfile"], options["ssl_keyfile"] = certfile, keyfile
        options["ssl_ciphers"] = TLS_CIPHERS
    return options


def write_server_file(port: int, token: str) -> Path:
    path = server_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    info = {"pid": os.getpid(), "port": port, "folder": str(CODE_FOLDER), "version": __version__, "token": token,
            "user": os_user(), "started": dt.datetime.now().isoformat(timespec="seconds")}  # fmt: skip
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(info, indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)  # the token is for this Windows / Mac user only
    os.replace(tmp, path)
    return path


def remove_server_file(token: str) -> None:
    """Remove the server file, if it is still this server's (a newer server may have written its own)."""
    path = server_file()
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("token") == token:
            path.unlink()
    except (OSError, ValueError):
        pass


def _announce_when_up(url: str, open_browser: bool, timeout: float = 60.0) -> None:
    """Wait until the server answers /healthz, then say where it is and open the browser. Asked directly, never
    through an office proxy (HTTP_PROXY, or Windows' own setting): a proxy can't reach this computer's 127.0.0.1.
    Over HTTPS the certificate is not checked for this one question: it is this process's own server, asked at
    127.0.0.1, a name the company's certificate does not carry."""
    deadline = time.monotonic() + timeout
    own = ssl.create_default_context()
    own.check_hostname, own.verify_mode = False, ssl.CERT_NONE
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=own))
    while time.monotonic() < deadline:
        try:
            with direct.open(url + "/healthz", timeout=2) as response:  # our own server
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.2)
    else:
        return
    print(f"AP Coder is running at {url}", flush=True)
    if open_browser and browser_wanted():  # (not BROWSER=none, nor a Linux computer with no desktop)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - no browser: the address is printed above
            pass


GRACEFUL_SECONDS = 25  # a stop waits this long for the requests under way, then ends them (B-1272)
WORKERS_ENV = "AP_SERVER_WORKERS"  # how many web workers (server.json's ``workers``; ``serve --workers``)
WORKER_ENV = "AP_SERVE_WORKER"  # what the server gives each of its web workers (JSON: worker_app)
STOP_FILE = ".ap_coder_server.stop"  # with several web workers: /shutdown writes the token here, and all stop


# Above browsers' and IIS ARR's idle limits: uvicorn's 5 s, the same as many clients', closed a connection just as it
# was used again, a dropped request under load (B-1295). A busy web worker hands its connections back (HandBack).
KEEP_ALIVE_SECONDS = 75


def server_config(app: Any, host: str, port: int, certfile: str | None, keyfile: str | None, proxy: str | None,
                  **more: Any) -> Any:  # fmt: skip
    """uvicorn's settings for the dashboard: a stop (Ctrl+C, the service's, ``POST /shutdown``) waits at most
    GRACEFUL_SECONDS for the requests under way (a long report), so the service stops within 30 seconds."""
    import uvicorn

    return uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False, lifespan="on",
                          timeout_graceful_shutdown=GRACEFUL_SECONDS, timeout_keep_alive=KEEP_ALIVE_SECONDS,
                          **uvicorn_options(certfile, keyfile, proxy),
                          **more)  # fmt: skip


WORKER_MB = 1500  # what a web worker uses on a 300,000-invoice database, its threads capped (D-153)
MALLOC_ARENAS, ARENAS_ENV = 2, "MALLOC_ARENA_MAX"  # glibc's malloc arenas in a process of the server (D-195)


def fewer_malloc_arenas() -> None:
    """On Linux, at most ``MALLOC_ARENAS`` malloc arenas in this process (``mallopt``, the arenas made from now on) and
    in those it starts (glibc's ``MALLOC_ARENA_MAX``: the web workers, the background and capture processes). By
    default each thread may make one of its own (8 a core), and each keeps the memory a request's peak freed there: a
    web worker on 60,000 invoices held 714 MB, 536 MB with two, its pages as fast (D-195). Not where it is set
    already, nor elsewhere (macOS and Windows have their own allocators)."""
    if not sys.platform.startswith("linux") or os.environ.get(ARENAS_ENV):
        return
    os.environ[ARENAS_ENV] = str(MALLOC_ARENAS)
    try:
        import ctypes

        ctypes.CDLL(None).mallopt(-8, MALLOC_ARENAS)  # M_ARENA_MAX
    except (OSError, AttributeError):  # (a libc without mallopt: musl)
        pass


def default_workers(cores: int | None = None, memory_mb: float | None = None) -> int:
    """How many web workers the company server runs when nothing says (D-153; D-111 had one per core, at most 8):
    one per core but an eighth of them, left to the background process and the database, and only as many as fit
    at WORKER_MB each in the memory not kept for those and the system (``spare_mb``). 32 cores and 64 GB: 28."""
    cores = cores or os.cpu_count() or 1
    by_cores = cores - cores // 8
    total = memory_mb if memory_mb is not None else memory_total_mb()
    by_memory = int(spare_mb(total) // WORKER_MB) if total else by_cores
    return max(1, min(by_cores, by_memory, 64))


def spare_mb(total_mb: float) -> float:
    """What the web workers may use of ``total_mb``: a quarter (at least 4 GB) is kept for the system, the background
    process (OCR) and the database's own cache."""
    return total_mb - max(4000.0, total_mb / 4)


def memory_total_mb() -> float | None:
    """This computer's memory in MB (None where it can't be told)."""
    if os.name == "nt":
        import ctypes

        class Status(ctypes.Structure):  # MEMORYSTATUSEX
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), *(
                (name, ctypes.c_ulonglong) for name in ("total", "free", "pagefile", "pagefree", "virtual",
                                                        "virtualfree", "extended"))]  # fmt: skip

        status = Status()
        status.length = ctypes.sizeof(Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return None
        return status.total / 1e6
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e6
    except (OSError, ValueError, AttributeError):
        return None


def memory_mb() -> float | None:
    """This process's resident memory in MB (its working set on Windows; None where it can't be told)."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD), *(
                (name, ctypes.c_size_t) for name in ("peak", "working_set", "peak_paged", "paged", "peak_nonpaged",
                                                     "nonpaged", "pagefile", "peak_pagefile"))]  # fmt: skip

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return counters.working_set / 1e6
    try:
        with open("/proc/self/statm", "rb") as statm:
            return int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError, IndexError):
        return None


def serve(host: str = "127.0.0.1", port: int = 8501, open_browser: bool = True, store: Store | None = None, *,
          certfile: str | None = None, keyfile: str | None = None, proxy: str | None = None,
          insecure_http: bool = False, workers: int = 1) -> int:  # fmt: skip
    """Run the dashboard until Ctrl+C or ``POST /shutdown`` (on the server, until its service is stopped). 1 when it
    could not start (the port is in use), 2 when it may not start so (the server without HTTPS: ``tls_refusal``).
    ``certfile``/``keyfile``: HTTPS (PEM); ``proxy``: the reverse proxy's address, whose X-Forwarded-For is believed.
    ``workers``: on the company server, that many web worker processes and one background process (D-111); the
    laptop always runs everything in this one process."""
    import uvicorn

    refusal = tls_refusal(server_mode(), certfile, keyfile, insecure_http)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2

    # A port something answers on (an AP Coder started earlier): writing the server file would replace the running
    # one's (and its token, which the launcher stops it with), and "is it up?" would be answered by the other one.
    https = bool(certfile and keyfile)
    taken = port_refusal(host, port, https)
    if taken:
        print(taken, file=sys.stderr)
        return 1
    if server_mode() and not https:
        print("On plain HTTP (--insecure-http): passwords and invoices cross the network unencrypted. Only for a "
              "first test: give the certificate (--certfile and --keyfile) before AP staff use it.",
              file=sys.stderr)  # fmt: skip
    if server_mode():
        fewer_malloc_arenas()
    if server_mode() and workers > 1:
        return _serve_workers(host, port, store, certfile, keyfile, proxy, insecure_http, workers)
    app = create_app(store=store, address=host, insecure_http=insecure_http)
    app.state.proxy = proxy or None  # behind a proxy, a loopback address is not the console (pages/login.py)
    app.state.certfile = certfile if https else None
    health.watch_certificate(app.state.certfile)  # its end told to the system's log on the server (D-187)
    token = secrets.token_urlsafe(24)
    server = uvicorn.Server(server_config(app, host, port, certfile, keyfile, proxy))
    app.state.port, app.state.shutdown_token, app.state.server = port, token, server
    write_server_file(port, token)
    url = browser_url(host, port, https)
    threading.Thread(target=_announce_when_up, args=(url, opens_browser(open_browser)), daemon=True).start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        remove_server_file(token)
    return 0 if server.started else 1


# --- Several web workers and a background process: the company server (D-111) -------------------------------------
# One Python process answers about 1.6 requests a second whatever the cores (the GIL). On the server, ``workers``
# processes answer, all on the one port (uvicorn's own supervisor, which works on Windows: the port is opened here
# and handed to each worker, started afresh with "spawn"); a worker that stops is started again. The work the
# dashboard does on its own (page reader, AI reviewer, Azure check, JD Edwards, daily backup, hub schedule) runs in
# one background process beside them, also started again when it stops. The invoices brought in are read by capture
# processes of their own (D-168), never in a web worker. What the processes share is in the database.

CAPTURE_ENV = "AP_CAPTURE_PROCESSES"  # how many capture processes (default: one per core but two)
CAPTURE_MB = 700  # what a capture process uses reading scans (measured: 660-680 MB, 4 cores, the day bench)
OCR_THREADS_ENV = "AP_OCR_THREADS"  # ONNX Runtime's threads in each capture process (ops/offline.py)
CAPTURE_NUMBER_ENV = "AP_CAPTURE_NUMBER"  # which one a capture process is (its log)
CAPTURE_LEASE_SECONDS = 30.0  # a capture process renews its ``capture:`` lease: ``Store.capture_processes``
CAPTURE_IDLE_SECONDS = 1.0  # between two looks at an empty import queue
CAPTURE_STOP_SECONDS = 12.0  # a stop gives the file under way this long, then puts it back in line (within 15 s)


def capture_processes(cores: int | None = None, memory_mb: float | None = None) -> int:
    """How many capture processes the company server starts (D-168): AP_CAPTURE_PROCESSES, else one per core but two
    (the web workers, the background process and the database keep those), and no more than fit at CAPTURE_MB each in
    the memory the web workers leave (``spare_mb``), 2 GB of it kept for the system and the database; at least one.
    4 cores and 16 GB: 2; 32 cores and 64 GB: 20."""
    try:
        asked = int((os.environ.get(CAPTURE_ENV) or "").strip())
    except ValueError:
        asked = 0
    if asked > 0:
        return min(asked, 64)
    by_cores = (cores or os.cpu_count() or 1) - 2
    total = memory_mb if memory_mb is not None else memory_total_mb()
    by_memory = int((total - spare_mb(total) - 2000) // CAPTURE_MB) if total else by_cores
    return max(1, min(by_cores, by_memory))


def capture_env(number: int, count: int, cores: int | None = None) -> dict[str, str]:
    """A capture process's environment: which it is, and the threads its OCR and numerics use, so ``count`` of them
    share the cores they are given instead of each taking them all (ONNX Runtime's default: every core). A value the
    environment sets already is kept."""
    threads = str(max(1, ((cores or os.cpu_count() or 1) - 2) // max(1, count)))
    env = {**os.environ, CAPTURE_NUMBER_ENV: str(number)}
    for name in (OCR_THREADS_ENV, "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        env.setdefault(name, threads)
    return env


def stop_file(data: Path | None = None) -> Path:
    return (data or paths.private_dir()) / STOP_FILE


def _serve_workers(host: str, port: int, store: Store | None, certfile: str | None, keyfile: str | None,
                   proxy: str | None, insecure_http: bool, workers: int) -> int:  # fmt: skip
    store = store or Store(paths.env_path("AP_DB_PATH") or paths.default_db_path())  # (its upgrade runs here, once)
    try:
        prepare_reports(store)  # (the port still closed: nobody waits for them, D-204)
    except KeyboardInterrupt:
        return 0
    token = secrets.token_urlsafe(24)
    data = paths.private_dir()
    stop_file(data).unlink(missing_ok=True)
    (data / RECYCLE_PID).unlink(missing_ok=True)  # a worker of the last run ending: its number may be given again
    from ap_coder.ai.local_llm import SLOTS_ENV

    os.environ[SLOTS_ENV] = str(data / "llm")  # llm_parallel holds for all the server's processes together
    os.environ[WORKER_ENV] = json.dumps({"db": str(store.path), "address": host, "insecure_http": insecure_http,
                                         "proxy": proxy or "", "port": port, "token": token, "workers": workers,
                                         "secret": secrets.token_hex(32), "parent": os.getpid(),
                                         "data": str(data), "certfile": certfile or ""})  # fmt: skip
    health.watch_certificate(certfile if certfile and keyfile else None)  # (the system's log, D-187)
    # A worker busy with a long report still answers the supervisor's "alive?" (a thread of its own), but give it
    # time: one taken for hung is ended and started again.
    config = server_config("ap_coder.web.server:worker_app", host, port, certfile, keyfile, proxy, factory=True,
                           workers=workers, timeout_worker_healthcheck=30)  # fmt: skip
    supervisor = Supervisor(config, sockets=[listening_socket(config)])
    write_server_file(port, token)
    count = capture_processes()
    background = Together([BackgroundWork(store.path),
                           *(CaptureWork(store.path, n, count) for n in range(1, count + 1))])  # fmt: skip
    background.start()
    threading.Thread(target=_stop_when_asked, args=(supervisor, data, token), daemon=True).start()
    threading.Thread(target=_stop_background_when_asked, args=(supervisor.should_exit, background),
                     daemon=True).start()  # fmt: skip
    url = browser_url(host, port, bool(certfile and keyfile))
    threading.Thread(target=_announce_when_up, args=(url, False), daemon=True).start()
    try:
        supervisor.run()
    except KeyboardInterrupt:
        pass
    finally:
        background.stop()
        stop_file(data).unlink(missing_ok=True)
        remove_server_file(token)
        os.environ.pop(WORKER_ENV, None)
        os.environ.pop(SLOTS_ENV, None)
    return 1 if supervisor.failed else 0


def prepare_reports(store: Store) -> None:
    """The reports' default views with no answer kept worked out before the server answers (D-204,
    ``refresh.prepare``), each kept for every process (``background_answers``: B-2050): the background process's
    refresher worked them out after the start, while people already asked them. What was done is said in the server's
    window (and the service's log), which shows warnings and up only (B-240), as its other lines at a start are."""
    from ap_coder.web import refresh

    store.background_answers = True
    refresh.prepare(store, lambda text: print(text, file=sys.stderr, flush=True))


def _stop_background_when_asked(should_exit: threading.Event, background: Any) -> None:
    """The background process is asked to stop as soon as the server is, while the workers finish their requests,
    not after them: a stop takes the longer of the two, not both (B-1296)."""
    should_exit.wait()
    background.stop()


def _asked_to_stop(data: Path, token: str) -> bool:
    try:
        return stop_file(data).read_text(encoding="utf-8").strip() == token
    except OSError:
        return False


def _stop_when_asked(supervisor: Any, data: Path, token: str) -> None:
    """The server's side of ``POST /shutdown`` answered by a worker: the token in the stop file."""
    while not supervisor.should_exit.wait(0.5):
        if _asked_to_stop(data, token):
            supervisor.asked_to_stop = True
            supervisor.should_exit.set()


WAKE_EVERY = 0.5  # seconds between the connections that wake a worker waiting in accept() once asked to stop


def _wake_waiting_workers(config: Any) -> None:
    """Connect to the server's own port, and close at once: a web worker whose loop waits in a blocking ``accept()``
    on the socket the workers share (on Windows: two woke for one connection, the other took it) takes it, and then
    sees it was asked to stop; it would otherwise run until ended at the deadline (B-1441). A worker that is not
    waiting answers an empty connection, which is nothing."""
    host = str(getattr(config, "host", "") or "127.0.0.1")
    host = {"0.0.0.0": "127.0.0.1", "::": "::1", "": "127.0.0.1"}.get(host, host)
    with contextlib.suppress(OSError):
        socket.create_connection((host, int(config.port)), timeout=WAKE_EVERY).close()


def _supervisor_class() -> Any:
    from uvicorn.config import STARTUP_FAILURE
    from uvicorn.supervisors.multiprocess import Multiprocess

    class _Supervisor(Multiprocess):
        """uvicorn's supervisor, made sure to stop: the stop file asks every worker to stop (Windows has no SIGTERM,
        and its CTRL_BREAK reaches a worker only in a console of its own), and a worker still running
        GRACEFUL_SECONDS + 5 seconds later is ended."""

        failed = False
        asked_to_stop = False  # /shutdown or the service: a worker ending now is no startup failure (B-1292)

        def keep_subprocess_alive(self) -> None:
            """The stop file first: workers that read it may have ended (on Windows with STARTUP_FAILURE's exit
            code) before ``_stop_when_asked`` saw it, and are no workers that failed to start (B-1493). A worker
            being started afresh (``Recycle``) is not asked "alive?" while it ends: it can't answer as its process
            closes, and uvicorn then waited timeout_worker_healthcheck (30 s), replacing no worker meanwhile. It is
            replaced once it has ended."""
            spec = json.loads(os.environ.get(WORKER_ENV) or "{}")
            if spec.get("data") and _asked_to_stop(Path(spec["data"]), str(spec.get("token") or "")):
                self.asked_to_stop = True
                self.should_exit.set()
                return
            leaving = _leaving(Path(spec["data"])) if spec.get("data") else None
            ending = [p for p in self.processes if p.pid == leaving and p.process.is_alive()]
            if leaving is not None and not ending:
                _left(Path(spec["data"]), leaving)  # it has ended: its process number may be given to another
            self.processes = [p for p in self.processes if p not in ending]
            try:
                super().keep_subprocess_alive()
            finally:
                self.processes += ending

        def terminate_all(self) -> None:
            spec = json.loads(os.environ.get(WORKER_ENV) or "{}")
            if spec.get("data"):
                with contextlib.suppress(OSError):
                    stop_file(Path(spec["data"])).write_text(str(spec.get("token") or ""), encoding="utf-8")
            if os.name == "nt":
                return  # the stop file reaches them; CTRL_BREAK could reach the whole console instead
            for process in self.processes:
                with contextlib.suppress(OSError):
                    process.terminate()

        def join_all(self) -> None:
            deadline = time.monotonic() + GRACEFUL_SECONDS + 5
            for process in self.processes:
                process.process.join(min(WAKE_EVERY, max(0.1, deadline - time.monotonic())))
                while process.process.is_alive() and time.monotonic() < deadline:
                    _wake_waiting_workers(self.config)  # one stuck in accept() sees the stop (B-1441)
                    process.process.join(min(WAKE_EVERY, max(0.1, deadline - time.monotonic())))
                if process.process.is_alive():
                    process.kill()
                    process.process.join(5)
            # On Windows a worker stopped gracefully re-raises SIGTERM (uvicorn) and ends with exit code 3, the
            # same number as STARTUP_FAILURE: once asked to stop, that is no failure.
            self.failed = not self.asked_to_stop and any(p.exitcode == STARTUP_FAILURE for p in self.processes)

    return _Supervisor


def listening_socket(config: Any) -> socket.socket:
    """The server's socket, bound and listening before any web worker starts (B-1711). Each worker listens on its own
    copy (on Windows, ``socket.share``): when the first of them made it listen while another did the same, one
    worker's start failed with WinError 10022. A socket already listening is left as it is, its queue holding a
    connection until the first worker takes it."""
    sock = config.bind_socket()
    sock.listen(config.backlog)
    return sock


def Supervisor(config: Any, sockets: list[Any]) -> Any:  # noqa: N802 - built when used: uvicorn is imported late
    return _supervisor_class()(config, sockets=sockets)


def worker_app() -> Any:
    """One web worker of the company server (uvicorn's factory, in a process of its own): the app on the database
    the server opened, without the background work, with the server's token and the key the workers share."""
    spec = json.loads(os.environ[WORKER_ENV])
    store = Store(Path(spec["db"]))
    worker_log(store.path.parent / "logs")
    app = create_app(store=store, address=spec["address"], insecure_http=bool(spec["insecure_http"]),
                     background=False, processes=int(spec["workers"]),
                     secret=bytes.fromhex(spec["secret"]))  # fmt: skip
    app.state.proxy, app.state.certfile = spec["proxy"] or None, spec.get("certfile") or None
    app.state.port, app.state.shutdown_token = int(spec["port"]), spec["token"]
    app.state.stop_file = stop_file(Path(spec["data"]))
    threading.Thread(target=_stop_worker_when_asked, args=(Path(spec["data"]), spec["token"], int(spec["parent"])),
                     name="ap-coder-worker-stop", daemon=True).start()  # fmt: skip
    app.add_middleware(HandBack)  # a busy worker's connections go to the others (D-161)
    app.add_middleware(Recycle, state=app.state, data=Path(spec["data"]), **recycle_limits(int(spec["workers"])))
    warm_worker(store, app.state.settings_factory)  # (before it serves: D-204)
    return app


# --- A web worker's memories built before it serves (D-204) ------------------------------------------------------
# A web worker keeps what its pages read again and again (core/caches.py CACHES). One just started has none of it, so
# its first open of an invoice built the learning memory, the vendor list and the vendor's lessons and invoices in that
# request (3-6 s at 300,000 invoices), and its first Learning visit its latest lessons (2.2 s). Each web worker of the
# company server builds them in uvicorn's factory (``worker_app``), before it takes a connection: the others serve
# meanwhile, and the supervisor's "alive?" is answered on a thread of uvicorn's own. Each through the function the page
# calls, so the page takes what it built. D-203 built them on a thread once the worker served: not kept.
WARM_MOST = 15.0  # seconds, looked at before each step: well under the 30 s uvicorn gives a worker started afresh
WARM_FIRST = 50  # the review queue's first invoices (people open those at its top first): their vendors'
WARM_VENDORS = 8  # lessons and invoices of so many of those vendors, the biggest first (VENDOR_LESSONS_KEPT: 32)
# The vendors of those invoices, the biggest first, with how many invoices each has
_FIRST_VENDORS = ("SELECT MIN(q.vendor_name) AS vendor_name, MAX(q.gst_number) AS gst_number, (SELECT COUNT(*) FROM "
                  "invoice_summary s WHERE s.vendor_key = q.vendor_key) AS invoices FROM invoice_summary q WHERE "
                  f"{dialect.in_list('q.id')} AND q.vendor_name != '' GROUP BY q.vendor_key ORDER BY invoices DESC, "
                  "MIN(q.id) LIMIT ?")  # fmt: skip


def _held_numbers(store: Store) -> Any:
    """What the vendors on hold's approved invoices carried, as the check of a held vendor's invoice moved to another
    vendor reads it at every open (``coding.vendors.held_vendor``); None with no vendor on hold."""
    held = store.held_vendors()
    return store.approved_numbers([v["vendor_key"] for v in held]) if held else None


def warm_steps(store: Store, settings_factory: Callable[[], Any]) -> Any:
    """What a web worker builds before it serves, in this order: (what, how). The learning memory (``FEEDBACK_READ``
    lessons: every open and check of an invoice), the vendor list, the vendors' ERP IDs and the vendors on hold's
    numbers (every open), the reading status (every page), Learning's latest lessons, then the lessons and invoices of
    the biggest vendors (by their invoices) of the review queue's first ``WARM_FIRST`` invoices, ``WARM_VENDORS`` at
    most, their invoices within half of what a worker keeps of the vendors' (``VENDOR_ROWS_KEPT``): the other half for
    those people ask."""
    from ap_coder.core.store import REVIEW
    from ap_coder.core.store.vendors import VENDOR_ROWS_KEPT
    from ap_coder.web.core import reading_status_or_none
    from ap_coder.web.pages import invoice, learning

    yield "the learning memory", store.feedback_rows
    yield "the vendor list", lambda: invoice.vendor_list(store)
    yield "the vendors' ERP IDs", store.vendor_ids
    yield "the vendors on hold's numbers", lambda: _held_numbers(store)
    yield "the reading status", lambda: reading_status_or_none(settings_factory(), store)
    yield "Learning's latest lessons", lambda: store.feedback_rows(limit=learning.MEMORY_READ)
    vendors = store.summary_fetch(_FIRST_VENDORS, (json.dumps(store.listed_ids(REVIEW)[:WARM_FIRST]), WARM_VENDORS))
    rows = 0
    for v in vendors:
        name, key = v["vendor_name"], store.vendor_key_for(v["vendor_name"], v["gst_number"])
        yield f"the lessons of {name}", lambda name=name, key=key: store.vendor_lessons(name, key)
        if rows + v["invoices"] <= VENDOR_ROWS_KEPT // 2:
            rows += v["invoices"]
            yield f"the invoices of {name}", lambda key=key: store.vendor_invoices(key, shared=True)


def warm_worker(store: Store, settings_factory: Callable[[], Any], most: float = WARM_MOST) -> list[str]:
    """A web worker's memories built before it serves (``warm_steps``), one after the other, within ``most`` seconds
    (looked at before each step), until the worker is asked to stop. A step that fails is logged and the next goes
    on: nothing is lost, the page builds it when asked. Said in the worker's own log (``worker_log``). What was
    built, in order."""
    log, began, built, cut = logging.getLogger(__name__), time.monotonic(), [], ""
    try:
        for what, build in warm_steps(store, settings_factory):
            if time.monotonic() - began >= most:
                cut = f"; stopped at {most:g} s"
                break
            if (running := uvicorn_server()) is not None and running.should_exit:
                cut = "; stopped: the worker stops"
                break
            try:
                build()
                built.append(what)
            except Exception:  # noqa: BLE001 - any step's failure: logged, the next goes on
                log.warning("A web worker did not build %s before it served.", what, exc_info=True)
    except Exception:  # noqa: BLE001 - the steps themselves not found (a busy database): built where asked
        log.warning("A web worker stopped building its memories before it served.", exc_info=True)
        cut = "; stopped on an error"
    log.info("A web worker (process %s) built %d of its memories before it served, in %.1f s%s: %s.", os.getpid(),
             len(built), time.monotonic() - began, cut, ", ".join(built) or "none")  # fmt: skip
    return built


# --- A web worker started afresh now and then (D-153) ------------------------------------------------------------
# What a worker frees is mostly kept by Python's allocator: its memory stays near its highest. Past its share of the
# memory, or past RECYCLE_REQUESTS requests (each worker a number of its own, up to a quarter more, so they don't all
# go together), a worker takes no new requests, finishes those under way and ends; uvicorn's supervisor starts
# another in its place (on Windows too: it ends as any process does, not by a signal). One worker at a time (a lock
# in the data folder), and never while something of its own runs that would end with it: a job, a backup, imports
# waiting their turn, the long routes' lane (core.run_heavy). Logins are kept in the database: nobody is signed out.
MEMORY_ENV, REQUESTS_ENV = "AP_WORKER_MAX_MB", "AP_WORKER_MAX_REQUESTS"
RECYCLE_REQUESTS = 50_000  # about a working day for each of 16 workers answering 20-40 requests a second
RECYCLE_LOCK = ".ap_coder_worker_restart.lock"
RECYCLE_PID = ".ap_coder_worker_restart.pid"  # the worker being started afresh, for the supervisor
LEAVING_SECONDS = 600  # how long that note is believed (one still waiting for its own work answers "alive?")
MEMORY_EVERY = 10  # requests between two looks at the memory
DRAIN_SECONDS = 1.0
RECYCLE_GRACE_SECONDS = 3600  # what an ending worker gives the requests under way (a service stop: GRACEFUL_SECONDS)


def recycle_limits(workers: int, total_mb: float | None = None) -> dict[str, float]:
    """A worker's memory ceiling (AP_WORKER_MAX_MB, else its share of ``spare_mb``, at least 2 GB) and requests
    (AP_WORKER_MAX_REQUESTS, else RECYCLE_REQUESTS); 0: no limit."""
    total = total_mb if total_mb is not None else memory_total_mb()
    share = max(2000.0, spare_mb(total) / max(1, workers)) if total else 0.0

    def env(name: str, default: float) -> float:
        try:
            return max(0.0, float((os.environ.get(name) or "").strip() or default))
        except ValueError:
            return default  # a typo never stops the server

    return {"max_mb": env(MEMORY_ENV, share), "max_requests": env(REQUESTS_ENV, RECYCLE_REQUESTS)}


class Recycle:
    """ASGI middleware of one web worker of the company server: after each request, whether it is time for the
    worker to be started afresh (above), and if so, as soon as nothing of its own would end with it, its stop."""

    def __init__(self, app: Any, state: Any, data: Path, max_mb: float, max_requests: float,
                 jitter: Any = None) -> None:  # fmt: skip
        import random

        self.app, self.state, self.data, self.max_mb = app, state, data, max_mb
        rng = jitter or random.Random()
        self.limit = int(max_requests) + rng.randint(0, int(max_requests) // 4) if max_requests else 0
        self.served = 0
        self.why = ""  # what made it due
        self.stopping: Any = None  # the restart lock, held until this process ends
        self.unfound = False  # whether it said uvicorn's server was not found

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await self.app(scope, receive, send)
        finally:
            if scope["type"] == "http" and self.stopping is None:
                self.served += 1
                self.why = self.why or self._due()
                if self.why and not self._busy():
                    self._stop()

    def _due(self) -> str:
        if self.limit and self.served >= self.limit:
            return f"{self.served} requests"
        if self.max_mb and self.served % MEMORY_EVERY == 0:
            used = memory_mb()
            if used is not None and used > self.max_mb:
                return f"{used:,.0f} MB of memory (its ceiling: {self.max_mb:,.0f} MB)"
        return ""

    def _busy(self) -> bool:
        """Something of this worker's that would end with it: a job, a backup, imports waiting, a long route."""
        from ap_coder.web.core import heavy_lane

        lane = heavy_lane().statistics()
        if lane.borrowed_tokens or lane.tasks_waiting:
            return True
        return own_work_running(self.state)

    def _stop(self) -> None:
        from ap_coder.core.paths import try_lock

        found = uvicorn_server()
        if found is None:
            if not self.unfound:  # (said once: it is asked again after each request)
                self.unfound = True
                logging.getLogger(__name__).warning(
                    "A web worker (process %s) is not started afresh after %s: uvicorn's server was not found.",
                    os.getpid(), self.why)  # fmt: skip
            return
        self.stopping = try_lock(self.data / RECYCLE_LOCK)
        if self.stopping is None:
            return  # another worker is being started afresh: this one's turn comes after
        with contextlib.suppress(OSError):
            (self.data / RECYCLE_PID).write_text(str(os.getpid()), encoding="ascii")
        logging.getLogger(__name__).warning("A web worker (process %s) is started afresh after %s.", os.getpid(),
                                            self.why)  # fmt: skip
        # It takes no new connection first (the other workers do), and those it took already have DRAIN_SECONDS to
        # ask: uvicorn's own stop, alone, left one taken as it began unanswered until the end of its grace (25 s).
        loop = asyncio.get_running_loop()
        for listening in getattr(found, "servers", None) or []:
            try:
                for sock in listening.sockets:
                    loop.remove_reader(sock.fileno())
            except NotImplementedError:  # a loop without readers (Windows' Proactor): its own copy closed (B-1710)
                listening.close()
        # Those under way get the time they need: an import run whole in its request was cut with a 500 at the grace
        # while its work went on. An hour at most (an upload whose computer went to sleep never ends); a stop of the
        # service still ends this worker at its deadline (join_all).
        found.config.timeout_graceful_shutdown = RECYCLE_GRACE_SECONDS
        loop.call_later(DRAIN_SECONDS, setattr, found, "should_exit", True)  # then uvicorn finishes what is under way


def _leaving(data: Path) -> int | None:
    """The process of the web worker being started afresh (``Recycle``), as it wrote; None after LEAVING_SECONDS
    (it then is asked "alive?" again, as any worker is)."""
    note = data / RECYCLE_PID
    try:
        if time.time() - note.stat().st_mtime > LEAVING_SECONDS:
            return None
        return int(note.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _left(data: Path, pid: int) -> None:
    """The note of the worker ``pid`` started afresh dropped once it has ended (if no other wrote its own since)."""
    if _leaving(data) == pid:
        with contextlib.suppress(OSError):
            (data / RECYCLE_PID).unlink()


# --- A busy web worker hands its connections back (D-161) --------------------------------------------------------
# The workers listen on one socket, and the first event loop to accept a new connection takes it, on Linux (epoll)
# and on Windows (each worker's copy of the socket, B-1711, and its selector loop): a free worker's usually wins it.
# What kept people on a busy worker is the browser keeping its connections open (minutes) and the server
# KEEP_ALIVE_SECONDS: with browser-like people, one worker of four did 54% of the work.


def worker_busy() -> bool:
    """Whether requests wait for one of this worker's threads, or for the long routes' lane (``core.run_heavy``)."""
    from ap_coder.web.core import heavy_lane

    pool = anyio.to_thread.current_default_thread_limiter().statistics()
    return bool(pool.tasks_waiting or heavy_lane().statistics().tasks_waiting)


class HandBack:
    """ASGI middleware of one web worker of the company server: while it is busy (``worker_busy``), each answer says
    ``Connection: close``, so the browser's next request opens a new connection, which a free worker takes. Nothing is
    lost: the close comes in the answer, and the browser (or IIS ARR in front) opens another for what it sends next."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def answer(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start" and worker_busy():
                headers = list(message.get("headers") or [])
                if not any(name.lower() == b"connection" for name, _ in headers):
                    message = {**message, "headers": [*headers, (b"connection", b"close")]}
            await send(message)

        await self.app(scope, receive, answer)


def uvicorn_server() -> Any:
    """The uvicorn server running this process (it took SIGTERM to stop gracefully); None when there is none."""
    import signal

    import uvicorn

    found = getattr(signal.getsignal(signal.SIGTERM), "__self__", None)
    return found if isinstance(found, uvicorn.Server) else None


def _stop_worker_when_asked(data: Path, token: str, parent: int) -> None:
    """A worker stops as uvicorn's SIGTERM would stop it (gracefully) when the server asks (the stop file) or is gone
    (ended without stopping it: nothing is left running for nobody)."""
    import signal

    from ap_coder.ai.page_worker import _pid_alive

    while True:
        time.sleep(1.0)
        if _asked_to_stop(data, token) or not _pid_alive(parent):
            # Still running well into the time a stop is given (the server ends it at GRACEFUL_SECONDS + 5): what each
            # of its threads is doing goes to the server's log, so a slow stop says why.
            with contextlib.suppress(Exception):
                import faulthandler

                faulthandler.dump_traceback_later(GRACEFUL_SECONDS - 5, file=sys.stderr)
            signal.raise_signal(signal.SIGTERM)
            return


class BackgroundWork:
    """The server's background process (``serve --background-work``), started again when it stops (5 s, doubling
    up to a minute). It stops when its input closes: this process stopping, or ending in any way."""

    FLAG, NAME = "--background-work", "background work"

    def __init__(self, db: Path, env: dict[str, str] | None = None) -> None:
        self.command = [sys.executable, "-m", "ap_coder", "--db", str(db), "serve", self.FLAG]
        self.env = env
        self.process: subprocess.Popen | None = None
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"ap-coder-{self.FLAG[2:]}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        delay = 5.0
        while not self._halt.is_set():
            started = time.monotonic()
            try:
                self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, env=self.env)
                code = self.process.wait()
            except OSError as exc:
                code = f"not started: {exc}"
            if self._halt.is_set():
                return
            delay = 5.0 if time.monotonic() - started > 600 else min(delay * 2, 60.0)
            print(f"AP Coder's {self.NAME} stopped (exit code {code}): started again in {delay:.0f} s",
                  file=sys.stderr, flush=True)  # fmt: skip
            system_log.report(system_log.PROCESS_STOPPED, f"AP Coder's {self.NAME} stopped (exit code {code}): it is "
                              "started again", key=self.NAME)  # fmt: skip
            self._halt.wait(delay)

    def stop(self) -> None:
        self.ask_to_stop()
        self.wait_stopped(time.monotonic() + 15)

    def ask_to_stop(self) -> None:
        self._halt.set()
        process = self.process
        if process is not None and process.poll() is None:
            with contextlib.suppress(OSError):
                process.stdin.close()  # its signal to stop: it finishes the page under way, at most 15 seconds

    def wait_stopped(self, deadline: float) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            process.wait(max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)


class CaptureWork(BackgroundWork):
    """One of the server's capture processes (``serve --capture-work``, D-168): it reads the import queue's files,
    ``number`` of ``count``, its OCR's threads its share of the cores (``capture_env``). Started again when it
    stops, as the background process is."""

    FLAG = "--capture-work"

    def __init__(self, db: Path, number: int, count: int) -> None:
        self.NAME = f"capture process {number}"
        super().__init__(db, capture_env(number, count))


class Together:
    """The background process and the capture processes, started and stopped as one: a stop asks them all at once,
    then waits for them together (15 s at most, not 15 s each)."""

    def __init__(self, works: list[BackgroundWork]) -> None:
        self.works = works

    def start(self) -> None:
        for work in self.works:
            work.start()

    def stop(self) -> None:
        for work in self.works:
            work.ask_to_stop()
        deadline = time.monotonic() + 15
        for work in self.works:
            work.wait_stopped(deadline)


BACKGROUND_LOG = "background.log"
STACK_DUMP_LOG = "background-stacks.log"
STACK_DUMP_ENV = "AP_STACK_DUMP_SECONDS"  # a bench's look inside a background process that seems stuck


def background_log(folder: Path, name: str = BACKGROUND_LOG) -> logging.Handler:
    """The background process's own log, ``logs/background.log`` beside the database (5 MB, then up to 5 older
    files): the page reader, the AI reviewer, JD Edwards and the backup say there what they do (INFO and up), so IT
    can see the work nobody waits on, and a failure leaves a trace. Warnings still reach the server's window too.
    ``name``: a capture process's own (``capture-<n>.log``: one file a process, each rotated by its own)."""
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(folder / name, maxBytes=5_000_000, backupCount=5,
                                                   encoding="utf-8")  # fmt: skip
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    log = logging.getLogger("ap_coder")
    log.addHandler(handler)
    if log.level == logging.NOTSET or log.level > logging.INFO:
        log.setLevel(logging.INFO)
    return handler


def worker_log(folder: Path) -> logging.Handler | None:
    """A web worker's own log, ``logs/web-<n>.log`` beside its access log (``access-<n>.log``, the same number), as
    ``background_log`` writes the background process's: what ``ap_coder`` says there, INFO and up. A web worker is a
    process uvicorn spawns, which sets up no logging of its own: its warnings reached the server's window bare
    (``logging.lastResort``, no time) and the rest went nowhere (B-2065); with no handler of the window yet, one in the
    log's format, warnings and up. None when this process has that log already."""
    from ap_coder.web.security import worker_slot

    folder.mkdir(parents=True, exist_ok=True)
    name = f"web-{worker_slot(folder)}.log"
    path = os.path.abspath(folder / name)  # (as the file handler names its file)
    if any(getattr(h, "baseFilename", "") == path for h in logging.getLogger("ap_coder").handlers):
        return None
    handler = background_log(folder, name)
    root = logging.getLogger()
    if not root.handlers:
        window = logging.StreamHandler()  # (stderr: the server's window, or the service's log)
        window.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        window.setLevel(logging.WARNING)
        root.addHandler(window)
    return handler


def stack_dumps(folder: Path) -> Any:
    """Every thread's stack written to ``logs/background-stacks.log`` every AP_STACK_DUMP_SECONDS (unset: never), to
    see where a background process that seems stuck waits: Python's faulthandler, which works on Windows too,
    where no signal can ask a process. The open file, which must stay open while the dumps go on; None when off."""
    try:
        seconds = float(os.environ.get(STACK_DUMP_ENV) or 0)
    except ValueError:
        return None
    if seconds <= 0:
        return None
    import faulthandler

    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / STACK_DUMP_LOG, "w", encoding="utf-8")  # noqa: SIM115 - open while the dumps go on
    faulthandler.dump_traceback_later(seconds, repeat=True, file=handle)
    return handle


def until_input_closes(on_close: Callable[[], None], poll: float = 0.5) -> None:
    """Wait until this process's standard input closes (how ``serve --server`` stops its background process), then
    ``on_close()``. What the other end writes is read and dropped.

    On Windows a pipe is peeked every ``poll`` seconds, never read with a read left pending: Windows blocks
    ``GetFileType`` on a pipe while a read waits on it, and every DLL that starts its own C runtime calls it as it
    loads. numpy's did, so the page reader's self-test waited until the server stopped (B-1680); every scan and OCR
    would have too."""
    try:
        stream = sys.stdin.buffer
        if os.name == "nt" and _peek_until_closed(stream.fileno(), poll):
            return on_close()
        stream.read()
    except (OSError, ValueError, AttributeError):
        pass  # no input to wait for (closed, none at all): as closed
    on_close()


def _peek_until_closed(fd: int, poll: float) -> bool:
    """Windows: wait until the pipe behind ``fd`` is closed at the other end, peeking at it (PeekNamedPipe) and
    reading only what is already there, so no read is ever left waiting. False when ``fd`` is not a pipe (a
    console, a file): it is read as before."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    kernel32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]  # fmt: skip
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
    if kernel32.GetFileType(handle) != 3:  # FILE_TYPE_PIPE
        return False
    available = wintypes.DWORD()
    while kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
        if available.value:
            os.read(fd, available.value)  # only what is there: returns at once
        else:
            time.sleep(poll)
    return True  # the other end closed it (a broken pipe)


def run_background(store: Store) -> int:
    """The server's background process (D-111): the page reader and the AI reviewer, the Azure check, JD Edwards,
    the review queue checked again after a change of settings (D-155), the reports' settled answers worked out again
    (D-198), the daily backup and the finance hub's schedule (looked at every 30 seconds, not at each page), until its
    input closes. One per data folder (an OS lock): a second one waits for the first to end."""
    from ap_coder.web.app import DailyBackup, EnvSettings, start_background_work

    halt = threading.Event()
    threading.Thread(target=until_input_closes, args=(halt.set,), name="ap-coder-background-input",
                     daemon=True).start()  # fmt: skip
    background_log(store.path.parent / "logs")
    stacks = stack_dumps(store.path.parent / "logs")  # noqa: F841 - kept open for the process's life
    log = logging.getLogger("ap_coder.background")
    log.info("background process %s started for %s", os.getpid(), store.path)
    lock, waited = None, False
    while lock is None and not halt.is_set():
        lock = paths.try_lock(store.path.parent / ".ap_coder_background.lock")
        if lock is None:
            if not waited:
                log.info("waiting for another background process of this data folder to end (its lock is held)")
            waited = True
            halt.wait(5.0)
    if lock is None:
        return 0
    from ap_coder.erp.hub.run import HubSchedule

    backup, hub = DailyBackup(store), HubSchedule(store)
    health.beat(store)  # alive, said every 30 s in the database: /readyz reads it (D-187)
    backup.start()  # the day's backup, on its own thread: the background work starts meanwhile (B-1553)
    workers = start_background_work(EnvSettings(), store)
    log.info(
        "background work running: the page reader, the AI reviewer, the Azure check, JD Edwards, the review "
        "queue's checks after a change of settings and the reports' answers worked out again"
    )
    try:
        while not halt.wait(30.0):
            health.beat(store)
            backup.check()
            hub.check()
    except KeyboardInterrupt:
        pass
    finally:
        for worker in workers:
            worker.stop()
        deadline = time.monotonic() + 15  # the page under way is finished, or kept to be read again
        for worker in workers:
            thread = worker if isinstance(worker, threading.Thread) else getattr(worker, "thread", None)
            if isinstance(thread, threading.Thread):
                thread.join(max(0.1, deadline - time.monotonic()))
        lock.close()
    return 0


def run_capture(store: Store) -> int:
    """One of the company server's capture processes (D-168): it reads the import queue's files into the review queue,
    one at a time, until its input closes (the server stopping), as ``run_background`` runs. Its models are loaded once
    (the OCR engines and the table finder are kept for the process's life); its OCR's threads are pinned to its share
    of the cores (``capture_env``). A stop gives the file under way CAPTURE_STOP_SECONDS, then puts it back in line,
    its try not counted. Several of them read at once: the queue gives each file to one."""
    from ap_coder.intake.imports import Reader, owner_id
    from ap_coder.web.app import EnvSettings
    from ap_coder.web.core import cache_dir
    from ap_coder.web.pages.process import needs_attention

    halt = threading.Event()
    threading.Thread(target=until_input_closes, args=(halt.set,), name="ap-coder-capture-input",
                     daemon=True).start()  # fmt: skip
    number = os.environ.get(CAPTURE_NUMBER_ENV) or "1"
    background_log(store.path.parent / "logs", f"capture-{number}.log")
    _pin_threads()
    log = logging.getLogger("ap_coder.capture")
    owner = owner_id("capture")
    log.info("capture process %s (%s) started for %s", number, owner, store.path)
    reader = Reader(store, EnvSettings(), owner, attention=needs_attention, cache_dir=cache_dir(store))
    work = threading.Thread(target=_capture_loop, args=(store, reader, halt), name="ap-coder-capture", daemon=True)
    alive = threading.Thread(target=_say_alive, args=(store, owner, halt), name="ap-coder-capture-alive", daemon=True)
    work.start()
    alive.start()
    try:
        while not halt.wait(1.0):
            if not work.is_alive():
                return 1  # (started again by the server)
    except KeyboardInterrupt:
        halt.set()
    work.join(CAPTURE_STOP_SECONDS)
    with contextlib.suppress(Exception):
        if work.is_alive():  # the file under way goes back in line, for the next start
            log.info("capture process %s stopping: the file under way is put back in line", number)
            store.release_imports(owner)
        store.drop_lease(f"capture:{owner}", owner)
    return 0


def _capture_loop(store: Store, reader: Any, halt: threading.Event) -> None:
    log = logging.getLogger("ap_coder.capture")
    while not halt.is_set():
        try:
            read = reader.read_all(halt)
        except Exception:  # noqa: BLE001 - the GL accounts unreadable, a busy database: looked at again shortly
            log.exception("the import queue could not be read just now")
            halt.wait(5.0)
            continue
        if not read:
            halt.wait(5.0 if reader.paused else CAPTURE_IDLE_SECONDS)


def _say_alive(store: Store, owner: str, halt: threading.Event) -> None:
    """The ``capture:`` lease this process renews while it runs: ``watch`` reads nothing itself then (D-168)."""
    while True:
        with contextlib.suppress(Exception):
            store.take_lease(f"capture:{owner}", owner, CAPTURE_LEASE_SECONDS)
        if halt.wait(CAPTURE_LEASE_SECONDS / 3):
            return


def _pin_threads() -> None:
    """OpenCV's own threads, as many as ONNX Runtime's in this process (``capture_env``)."""
    try:
        threads = int(os.environ.get(OCR_THREADS_ENV) or 0)
    except ValueError:
        return
    if threads > 0:
        with contextlib.suppress(Exception):
            import cv2

            cv2.setNumThreads(threads)
