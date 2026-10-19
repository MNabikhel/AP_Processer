"""An AP Coder already running when a start begins (scripts/running.py): the same version is opened, a newer one is
never stopped, and an older one (AP Coder 0.2's server, or 0.1's Streamlit dashboard) is stopped first, so two
versions never run on one database."""

from __future__ import annotations

import http.server
import importlib.util
import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def running():
    spec = importlib.util.spec_from_file_location("ap_script_running", ROOT / "scripts" / "running.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server(tmp_path):
    """A stand-in for ``python -m ap_coder serve``: /healthz says who it is, /shutdown (with the right token) stops it,
    and it writes its server file into the data folder ``tmp_path / "data"``."""
    data = tmp_path / "data"
    data.mkdir()
    state = {"shutdowns": [], "version": "0.2.0", "folder": str(tmp_path / "B")}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            if self.path != "/healthz":
                self.send_error(404)
                return
            body = json.dumps({"ok": True, "app": "ap-coder", "version": state["version"], "folder": state["folder"],
                               "pid": 4242, "port": port}).encode()  # fmt: skip
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            ok = self.path == "/shutdown" and self.headers.get("X-AP-Coder-Token") == "secret"
            state["shutdowns"].append(ok)
            self.send_response(200 if ok else 403)
            self.end_headers()
            if ok:  # as the real server: it stops and closes its port
                threading.Thread(target=lambda: (httpd.shutdown(), httpd.server_close()), daemon=True).start()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    record = {"pid": 4242, "port": port, "token": "secret", "folder": state["folder"], "version": "0.2.0"}
    (data / ".ap_coder_server.json").write_text(json.dumps(record))
    yield state, port, data
    httpd.shutdown()
    httpd.server_close()


def test_the_same_folder_and_version_is_opened(running, server, tmp_path):
    state, port, data = server
    verdict = running.take_over(tmp_path / "B", "0.2.0", [], data, say=lambda m: None)
    assert verdict == ("open", port) and state["shutdowns"] == []


def test_a_newer_version_is_opened_never_stopped(running, server, tmp_path):
    state, port, data = server
    state["version"] = "0.3.0"
    assert running.take_over(tmp_path / "A", "0.2.0", [], data, say=lambda m: None) == ("newer", port)
    assert state["shutdowns"] == []


@pytest.mark.parametrize("ours", ["0.2.1", "0.2.0"])
def test_an_older_version_or_another_folder_is_stopped_with_its_token(running, server, tmp_path, ours):
    state, port, data = server
    said: list[str] = []
    folder = tmp_path / ("B" if ours != "0.2.0" else "C")  # a newer version here, or the same one in another folder
    assert running.take_over(folder, ours, [], data, say=said.append) == ("clear", None)
    assert state["shutdowns"] == [True] and not running.listening(port)
    assert "stopping it" in said[0] and "Stopped it" in said[1]


def test_a_server_that_cannot_be_stopped_waits_for_the_person(running, server, tmp_path, monkeypatch):
    state, port, data = server
    monkeypatch.setattr(running, "stop", lambda server, wait=0: False)
    said: list[str] = []
    assert running.take_over(tmp_path / "C", "0.3.0", [], data, say=said.append, patience=0.5) == ("blocked", port)
    assert any("Close the old AP Coder window, then start again" in s for s in said)


def test_nothing_running_is_clear(running, tmp_path):
    assert running.take_over(tmp_path, "0.2.0", [9], tmp_path, say=lambda m: None) == ("clear", None)


FAKE_STREAMLIT = textwrap.dedent(
    """
    import http.server, sys
    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200 if self.path == "/app/static/InterVariable.woff2" else 404)
            self.end_headers()
    s = http.server.HTTPServer(("127.0.0.1", 0), H)
    print(s.server_address[1], flush=True)
    s.serve_forever()
    """
)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="finds the port's owner through /proc")
def test_an_ap_coder_0_1_dashboard_is_found_by_its_port_and_stopped(running, tmp_path):
    """The Streamlit-era dashboard has no /healthz and no token: its process is found by its port, checked to be
    ``streamlit run ...ap_coder/dashboard.py``, and ended."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "streamlit").write_text(FAKE_STREAMLIT)
    old = tmp_path / "AP Coder 0.1"
    proc = subprocess.Popen([sys.executable, str(bindir / "streamlit"), "run", str(old / "ap_coder" / "dashboard.py")],
                            stdout=subprocess.PIPE, text=True)  # fmt: skip
    try:
        port = int(proc.stdout.readline())
        found = running.find([port], None)
        assert [s.legacy for s in found] == [True]
        said: list[str] = []
        assert running.take_over(tmp_path / "B", "0.2.0", [port], None, say=said.append) == ("clear", None)
        proc.wait(timeout=10)
        assert "0.1 (the Streamlit dashboard)" in said[0] and str(old) in said[0]
    finally:
        proc.kill()


FAKE_WATCH = "import sys, time\nprint('watching', flush=True)\nwhile True:\n    time.sleep(0.1)\n"


def _fake_worker(tmp_path, command: str) -> subprocess.Popen:
    """A process whose command line is ``python -m ap_coder <command>`` (a stand-in package, so nothing is read)."""
    fake = tmp_path / f"fake-{command}"
    (fake / "ap_coder").mkdir(parents=True)
    (fake / "ap_coder" / "__init__.py").write_text("")
    (fake / "ap_coder" / "__main__.py").write_text(FAKE_WATCH)
    proc = subprocess.Popen([sys.executable, "-m", "ap_coder", command, "--every", "60"], cwd=fake,
                            stdout=subprocess.PIPE, text=True)  # fmt: skip
    assert proc.stdout.readline().strip() == "watching"
    return proc


def _worker_note(data: Path, proc: subprocess.Popen, command: str, folder: Path, version: str) -> None:
    note = {"pid": proc.pid, "command": command, "folder": str(folder), "version": version, "started": "x"}
    (data / f".ap_coder_worker-{proc.pid}.json").write_text(json.dumps(note))


@pytest.mark.skipif(sys.platform == "win32", reason="reads the process's command line through /proc or ps")
def test_an_older_versions_watch_is_stopped_before_the_update(running, tmp_path):
    """B-167: an update stopped the older version's server, but its ``python -m ap_coder watch`` kept running on the
    same database; once the clean-up deleted its .venv, it marked every new invoice failed (ModuleNotFoundError),
    and the new version never read them again. An older watch or read-pages is stopped like an older server; this
    version's own is left running."""
    data = tmp_path / "data"
    data.mkdir()
    old = _fake_worker(tmp_path, "watch")
    same = _fake_worker(tmp_path, "read-pages")
    try:
        _worker_note(data, old, "watch", tmp_path / "AP Coder 0.2.0", "0.2.0")
        _worker_note(data, same, "read-pages", tmp_path / "AP Coder 0.2.1", "0.2.1")
        said: list[str] = []
        assert running.take_over(tmp_path / "AP Coder 0.2.1", "0.2.1", [], data, say=said.append) == ("clear", None)
        assert old.wait(timeout=10) is not None and same.poll() is None
        assert "AP Coder 0.2.0" in said[0] and "watch" in said[0] and "Stopped it" in said[1]
        assert str(tmp_path / "AP Coder 0.2.0") in running.stopped  # (its folder is cleaned up after the update)
        assert not (data / f".ap_coder_worker-{old.pid}.json").exists()
    finally:
        for proc in (old, same):
            proc.kill()
            proc.wait()


def test_a_worker_note_whose_process_is_gone_is_passed_over(running, tmp_path):
    (tmp_path / ".ap_coder_worker-1.json").write_text("{not json")
    gone = {"pid": 2**22 + 12345, "command": "watch", "folder": "x", "version": "0.1.0"}
    (tmp_path / f".ap_coder_worker-{gone['pid']}.json").write_text(json.dumps(gone))
    assert running.take_over(tmp_path, "0.2.0", [], tmp_path, say=lambda m: None) == ("clear", None)
    assert running.workers(tmp_path) == []


def test_only_ap_coder_processes_are_ever_ended(running):
    assert running.is_ap_coder_process("python -m streamlit run C:\\AP Coder\\ap_coder\\dashboard.py", legacy=True)
    assert running.is_ap_coder_process('"C:\\x\\streamlit.exe" run "C:\\AP\\ap_coder\\dashboard.py"', legacy=True)
    assert not running.is_ap_coder_process("python -m streamlit run other_app.py", legacy=True)
    assert not running.is_ap_coder_process("python -m http.server 8501", legacy=True)
    assert running.is_ap_coder_process("C:\\AP\\.venv\\Scripts\\python.exe -m ap_coder serve --port 8501", legacy=False)
    assert not running.is_ap_coder_process("python -m ap_coder doctor", legacy=False)
    folder = running.folder_in_command("python -m streamlit run C:\\AP Coder 0.1\\ap_coder\\dashboard.py --x 1")
    assert folder == "C:\\AP Coder 0.1"


def test_the_listener_of_a_port_is_found(running):
    if not sys.platform.startswith("linux"):
        pytest.skip("through /proc")
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        deadline = time.monotonic() + 5
        owner = None
        while owner is None and time.monotonic() < deadline:
            owner = running.port_owner(port)
        import os

        assert owner == os.getpid()


def fake_windows_powershell(answer: str):
    """subprocess.run as Windows runs ``powershell -Command``: its output is in the console's code page (850) unless
    the script sets UTF-8, and Python reads it in the ANSI one (cp1252) unless told otherwise."""

    def run(cmd, capture_output=False, text=None, encoding=None, errors=None, timeout=None, **kwargs):
        script = cmd[-1]
        raw = answer.encode("utf-8" if "OutputEncoding" in script and "UTF8" in script else "cp850")
        out = raw.decode(encoding or "cp1252", errors="replace") if (text or encoding) else raw
        return subprocess.CompletedProcess(cmd, 0, out + "\r\n", "")

    return run


def test_a_command_line_with_accents_is_read_right_on_windows(running, monkeypatch):
    """A folder named "Données" came back as "Donn‚es": the old dashboard's folder wasn't found to be cleaned up."""
    monkeypatch.setattr(running, "WINDOWS", True)
    line = 'python -m streamlit run "C:\\Users\\José\\Données\\AP Coder 0.1\\ap_coder\\dashboard.py"'
    monkeypatch.setattr(running.subprocess, "run", fake_windows_powershell(line))
    assert running.command_line(1234) == line
    assert running.folder_in_command(running.command_line(1234)) == "C:\\Users\\José\\Données\\AP Coder 0.1"
