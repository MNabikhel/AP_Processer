"""Run the dashboard: ``python -m ap_coder serve [--host 127.0.0.1] [--port 8501] [--no-browser]``.

The server runs in this process (uvicorn). Once it answers, it prints ``AP Coder is running at http://127.0.0.1:N``
(the launcher waits for that line, or for ``GET /healthz``) and opens the browser, unless ``--no-browser`` or
``BROWSER=none``.

While it runs, ``<data folder>/.ap_coder_server.json`` says which server this is: {"pid", "port", "folder",
"version", "token", "started"}. The launcher stops it with ``POST /shutdown`` and the header
``X-AP-Coder-Token: <token>``; the file is removed when the server stops.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from ap_coder import __version__, paths
from ap_coder.store import Store
from ap_coder.web.app import CODE_FOLDER, create_app

SERVER_FILE = ".ap_coder_server.json"


def server_file() -> Path:
    """Where the running server describes itself: the data folder's ``.ap_coder_server.json``."""
    return paths.private_dir() / SERVER_FILE


def browser_url(host: str, port: int) -> str:
    """The address to open: listening on every network still opens this computer's own."""
    shown = "127.0.0.1" if host in ("", "0.0.0.0", "::", "localhost") else host
    shown = f"[{shown}]" if ":" in shown else shown
    return f"http://{shown}:{port}"


def write_server_file(port: int, token: str) -> Path:
    path = server_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    info = {"pid": os.getpid(), "port": port, "folder": str(CODE_FOLDER), "version": __version__, "token": token,
            "started": dt.datetime.now().isoformat(timespec="seconds")}  # fmt: skip
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
    """Wait until the server answers /healthz, then say where it is and open the browser. Asked directly, never through
    a proxy: an office proxy (HTTP_PROXY) would be sent the address, and could not reach this computer's server."""
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(url + "/healthz", timeout=2) as response:  # our own server, on this computer
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.2)
    else:
        return
    print(f"AP Coder is running at {url}", flush=True)
    if open_browser and (os.environ.get("BROWSER") or "").strip().lower() != "none":
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - no browser: the address is printed above
            pass


def serve(host: str = "127.0.0.1", port: int = 8501, open_browser: bool = True, store: Store | None = None) -> int:
    """Run the dashboard until Ctrl+C or ``POST /shutdown``."""
    import uvicorn

    app = create_app(store=store, address=host)
    token = secrets.token_urlsafe(24)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False, lifespan="on")
    server = uvicorn.Server(config)
    app.state.port, app.state.shutdown_token, app.state.server = port, token, server
    write_server_file(port, token)
    url = browser_url(host, port)
    threading.Thread(target=_announce_when_up, args=(url, open_browser), daemon=True).start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        remove_server_file(token)
    return 0 if server.started else 1
