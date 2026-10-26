"""Run the dashboard: ``python -m ap_coder serve [--host 127.0.0.1] [--port 8501] [--no-browser]``; as the company's
server, ``serve --server --certfile cert.pem --keyfile key.pem [--proxy <ip>]`` (HTTPS, D-99).

The server runs in this process (uvicorn). Once it answers, it prints ``AP Coder is running at http://127.0.0.1:N``
(the launcher waits for that line, or for ``GET /healthz``) and opens the browser, unless ``--no-browser``,
``BROWSER=none`` or a Linux computer with no desktop (``offline.browser_wanted``).

While it runs, ``<data folder>/.ap_coder_server.json`` says which server this is: {"pid", "port", "folder",
"version", "token", "user" (the computer account running it), "started"}. The launcher stops it with
``POST /shutdown`` and the header ``X-AP-Coder-Token: <token>``; the file is removed when the server stops.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import json
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
from pathlib import Path
from typing import Any

from ap_coder import __version__
from ap_coder.core import paths
from ap_coder.core.store import Store
from ap_coder.ops.offline import ADDRESS_ENV, browser_wanted, dashboard_address, server_mode
from ap_coder.web.app import CODE_FOLDER, create_app, os_user

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
# was used again, a dropped request under load (B-1295).
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


def default_workers() -> int:
    """How many web workers the company server runs when nothing says (D-111): one per core, at most 8."""
    return max(1, min(os.cpu_count() or 1, 8))


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
    if server_mode() and workers > 1:
        return _serve_workers(host, port, store, certfile, keyfile, proxy, insecure_http, workers)
    app = create_app(store=store, address=host, insecure_http=insecure_http)
    app.state.proxy = proxy or None  # behind a proxy, a loopback address is not the console (pages/login.py)
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
# one background process beside them, also started again when it stops. What the workers share is in the database.


def stop_file(data: Path | None = None) -> Path:
    return (data or paths.private_dir()) / STOP_FILE


def _serve_workers(host: str, port: int, store: Store | None, certfile: str | None, keyfile: str | None,
                   proxy: str | None, insecure_http: bool, workers: int) -> int:  # fmt: skip
    store = store or Store(paths.env_path("AP_DB_PATH") or paths.default_db_path())  # (its upgrade runs here, once)
    token = secrets.token_urlsafe(24)
    data = paths.private_dir()
    stop_file(data).unlink(missing_ok=True)
    from ap_coder.ai.local_llm import SLOTS_ENV

    os.environ[SLOTS_ENV] = str(data / "llm")  # llm_parallel holds for all the server's processes together
    os.environ[WORKER_ENV] = json.dumps({"db": str(store.path), "address": host, "insecure_http": insecure_http,
                                         "proxy": proxy or "", "port": port, "token": token, "workers": workers,
                                         "secret": secrets.token_hex(32), "parent": os.getpid(),
                                         "data": str(data)})  # fmt: skip
    # A worker busy with a long report still answers the supervisor's "alive?" (a thread of its own), but give it
    # time: one taken for hung is ended and started again.
    config = server_config("ap_coder.web.server:worker_app", host, port, certfile, keyfile, proxy, factory=True,
                           workers=workers, timeout_worker_healthcheck=30)  # fmt: skip
    supervisor = Supervisor(config, sockets=[config.bind_socket()])
    write_server_file(port, token)
    background = BackgroundWork(store.path)
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


def _supervisor_class() -> Any:
    from uvicorn.config import STARTUP_FAILURE
    from uvicorn.supervisors.multiprocess import Multiprocess

    class _Supervisor(Multiprocess):
        """uvicorn's supervisor, made sure to stop: the stop file asks every worker to stop (Windows has no SIGTERM,
        and its CTRL_BREAK reaches a worker only in a console of its own), and a worker still running
        GRACEFUL_SECONDS + 5 seconds later is ended."""

        failed = False
        asked_to_stop = False  # /shutdown or the service: a worker ending now is no startup failure (B-1292)

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
                process.process.join(max(0.1, deadline - time.monotonic()))
                if process.process.is_alive():
                    process.kill()
                    process.process.join(5)
            # On Windows a worker stopped gracefully re-raises SIGTERM (uvicorn) and ends with exit code 3, the
            # same number as STARTUP_FAILURE: once asked to stop, that is no failure.
            self.failed = not self.asked_to_stop and any(p.exitcode == STARTUP_FAILURE for p in self.processes)

    return _Supervisor


def Supervisor(config: Any, sockets: list[Any]) -> Any:  # noqa: N802 - built when used: uvicorn is imported late
    return _supervisor_class()(config, sockets=sockets)


def worker_app() -> Any:
    """One web worker of the company server (uvicorn's factory, in a process of its own): the app on the database
    the server opened, without the background work, with the server's token and the key the workers share."""
    spec = json.loads(os.environ[WORKER_ENV])
    store = Store(Path(spec["db"]))
    app = create_app(store=store, address=spec["address"], insecure_http=bool(spec["insecure_http"]),
                     background=False, processes=int(spec["workers"]),
                     secret=bytes.fromhex(spec["secret"]))  # fmt: skip
    app.state.proxy = spec["proxy"] or None
    app.state.port, app.state.shutdown_token = int(spec["port"]), spec["token"]
    app.state.stop_file = stop_file(Path(spec["data"]))
    threading.Thread(target=_stop_worker_when_asked, args=(Path(spec["data"]), spec["token"], int(spec["parent"])),
                     name="ap-coder-worker-stop", daemon=True).start()  # fmt: skip
    return app


def _stop_worker_when_asked(data: Path, token: str, parent: int) -> None:
    """A worker stops as uvicorn's SIGTERM would stop it (gracefully) when the server asks (the stop file) or is gone
    (ended without stopping it: nothing is left running for nobody)."""
    import signal

    from ap_coder.ai.page_worker import _pid_alive

    while True:
        time.sleep(1.0)
        if _asked_to_stop(data, token) or not _pid_alive(parent):
            signal.raise_signal(signal.SIGTERM)
            return


class BackgroundWork:
    """The server's background process (``serve --background-work``), started again when it stops (5 s, doubling
    up to a minute). It stops when its input closes: this process stopping, or ending in any way."""

    def __init__(self, db: Path) -> None:
        self.command = [sys.executable, "-m", "ap_coder", "--db", str(db), "serve", "--background-work"]
        self.process: subprocess.Popen | None = None
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ap-coder-background", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        delay = 5.0
        while not self._halt.is_set():
            started = time.monotonic()
            try:
                self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE)
                code = self.process.wait()
            except OSError as exc:
                code = f"not started: {exc}"
            if self._halt.is_set():
                return
            delay = 5.0 if time.monotonic() - started > 600 else min(delay * 2, 60.0)
            print(f"AP Coder's background work stopped (exit code {code}): started again in {delay:.0f} s",
                  file=sys.stderr, flush=True)  # fmt: skip
            self._halt.wait(delay)

    def stop(self) -> None:
        self._halt.set()
        process = self.process
        if process is None or process.poll() is not None:
            return
        with contextlib.suppress(OSError):
            process.stdin.close()  # its signal to stop: it finishes the page under way, at most 15 seconds
        try:
            process.wait(15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)


def run_background(store: Store) -> int:
    """The server's background process (D-111): the page reader and the AI reviewer, the Azure check, JD Edwards,
    the daily backup and the finance hub's schedule (every minute, not at each page), until its input closes. One per
    data folder (an OS lock): a second one waits for the first to end."""
    from ap_coder.web.app import DailyBackup, EnvSettings, start_background_work

    halt = threading.Event()

    def until_input_closes() -> None:
        with contextlib.suppress(OSError, ValueError):
            sys.stdin.buffer.read()
        halt.set()

    threading.Thread(target=until_input_closes, name="ap-coder-background-input", daemon=True).start()
    lock = None
    while lock is None and not halt.is_set():
        lock = paths.try_lock(store.path.parent / ".ap_coder_background.lock")
        if lock is None:
            halt.wait(5.0)
    if lock is None:
        return 0
    from ap_coder.erp.hub.run import HubSchedule

    try:
        store.auto_backup()
    except OSError:
        pass
    workers = start_background_work(EnvSettings(), store)
    backup, hub = DailyBackup(store), HubSchedule(store)
    try:
        while not halt.wait(30.0):
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
