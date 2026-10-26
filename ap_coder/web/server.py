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

import datetime as dt
import errno
import json
import os
import secrets
import socket
import ssl
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


def server_config(app: Any, host: str, port: int, certfile: str | None, keyfile: str | None, proxy: str | None,
                  **more: Any) -> Any:  # fmt: skip
    """uvicorn's settings for the dashboard: a stop (Ctrl+C, the service's, ``POST /shutdown``) waits at most
    GRACEFUL_SECONDS for the requests under way (a long report), so the service stops within 30 seconds."""
    import uvicorn

    return uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False, lifespan="on",
                          timeout_graceful_shutdown=GRACEFUL_SECONDS, **uvicorn_options(certfile, keyfile, proxy),
                          **more)  # fmt: skip


def serve(host: str = "127.0.0.1", port: int = 8501, open_browser: bool = True, store: Store | None = None, *,
          certfile: str | None = None, keyfile: str | None = None, proxy: str | None = None,
          insecure_http: bool = False) -> int:  # fmt: skip
    """Run the dashboard until Ctrl+C or ``POST /shutdown`` (on the server, until its service is stopped). 1 when it
    could not start (the port is in use), 2 when it may not start so (the server without HTTPS: ``tls_refusal``).
    ``certfile``/``keyfile``: HTTPS (PEM); ``proxy``: the reverse proxy's address, whose X-Forwarded-For is believed."""
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
