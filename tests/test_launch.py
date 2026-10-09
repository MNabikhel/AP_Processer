"""The one-button launcher (scripts/launch.py, behind APProcessor.bat / APProcessor.command): it sets up only
what is missing, says what is ready, and never starts a second copy of a dashboard that is already running."""

from __future__ import annotations

import functools
import http.server
import importlib.util
import io
import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def launch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ap_script_launch", ROOT / "scripts" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    venv = tmp_path / ".venv"
    venv.mkdir()
    monkeypatch.setattr(module, "VENV", venv)
    monkeypatch.setattr(module, "VENV_PY", venv / "bin" / "python")
    monkeypatch.setattr(module, "STATE", venv / "ap_coder_launcher.json")
    monkeypatch.setattr(module, "WINDOWS", False)
    return module


# --- Packages: only what is missing -----------------------------------------------------------------------------


def _fake_pip(launch, monkeypatch, missing: list[str], self_problem: str = "", fails: set[str] = frozenset()):
    """pip that "installs" what it is asked for (except ``fails``); records each call."""
    calls: list[list[str]] = []
    left = {"missing": list(missing), "self": self_problem}

    def pip_install(args):
        calls.append(list(args))
        if any(a in fails for a in args) or (args[-1].startswith(".[") and "ocr" in args[-1] and fails):
            return False
        if "-e" in args:  # the folder with its packages (without the OCR add-on when that fails)
            left["self"] = ""
            left["missing"] = [r for r in left["missing"] if fails and launch.is_ocr(r)]
        else:
            left["missing"] = [r for r in left["missing"] if r not in args]
        return True

    monkeypatch.setattr(launch, "pip_install", pip_install)
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (list(left["missing"]), left["self"]))
    return calls


def test_nothing_is_installed_when_everything_is_in_place(launch, monkeypatch):
    calls = _fake_pip(launch, monkeypatch, [])
    level, text, installed = launch.ensure_packages({})
    assert (level, installed, calls) == ("ok", False, []) and text.startswith("Packages: all ")


def test_only_the_missing_package_is_installed(launch, monkeypatch):
    calls = _fake_pip(launch, monkeypatch, ["openpyxl>=3.1"])
    level, text, installed = launch.ensure_packages({})
    assert calls == [["openpyxl>=3.1"]]  # not the whole list, not ap_coder again
    assert level == "ok" and installed and "installed what was missing" in text


def test_first_install_is_the_editable_folder_with_the_ocr_add_on(launch, monkeypatch):
    calls = _fake_pip(launch, monkeypatch, ["streamlit>=1.62", "rapidocr>=3.4,<3.5"], "Missing: ap_coder")
    launch.ensure_packages({})
    assert calls == [["-e", ".[ocr]"]]


def test_a_moved_folder_is_registered_without_downloading(launch, monkeypatch):
    calls = _fake_pip(launch, monkeypatch, [], "ap_coder is installed from C:/old, not from this folder")
    launch.ensure_packages({})
    assert calls == [["--no-deps", "-e", "."]]


def test_ocr_add_on_failing_is_not_retried_on_every_start(launch, monkeypatch):
    state: dict = {}
    calls = _fake_pip(launch, monkeypatch, ["rapidocr>=3.4,<3.5"], fails={"rapidocr>=3.4,<3.5"})
    level, text, installed = launch.ensure_packages(state)
    assert level == "warn" and "OCR" in text and state["ocr_failed"] == launch.fingerprint()
    assert len(calls) == 1
    level, text, installed = launch.ensure_packages(state)  # the next start
    assert len(calls) == 1 and not installed and level == "warn"


def test_a_failed_install_stops_with_what_to_do(launch, monkeypatch):
    _fake_pip(launch, monkeypatch, ["streamlit>=1.62"], fails={"streamlit>=1.62"})
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert "HTTPS_PROXY" in exc.value.hint


def test_the_wheelhouse_is_used_when_there_is_one(launch, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    assert launch.pip_source() == []
    (tmp_path / "wheelhouse").mkdir()
    assert launch.pip_source() == ["--no-index", "--find-links", str(tmp_path / "wheelhouse")]


# --- .venv: made once, remade only when broken ---------------------------------------------------------------


def test_venv_is_made_once_and_remade_only_when_broken(launch, tmp_path, monkeypatch):
    works = {"value": False}
    made: list[list[str]] = []

    def run(cmd, **kwargs):
        made.append(cmd)
        works["value"] = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launch, "venv_works", lambda: works["value"])
    monkeypatch.setattr(launch.subprocess, "run", run)
    launch.VENV.rmdir()
    assert launch.ensure_venv("python") == "created" and len(made) == 1
    assert launch.ensure_venv("python") == "reused" and len(made) == 1  # an update does not remake it
    works["value"] = False
    launch.VENV.mkdir()
    assert launch.ensure_venv("python") == "recreated" and len(made) == 2


def test_a_venv_whose_python_does_not_start_is_broken(launch):
    assert not launch.venv_works()  # no python in it at all
    launch.VENV_PY = Path(sys.executable)
    assert launch.venv_works()


# --- Already running ------------------------------------------------------------------------------------------


@pytest.fixture
def web(tmp_path):
    """A local web server on a free port, serving ``tmp_path/site``."""
    site = tmp_path / "site"
    site.mkdir()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield site, server.server_address[1]
    server.shutdown()
    server.server_close()


def test_a_running_ap_coder_is_found_and_another_server_is_not(launch, web, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # never through a proxy
    site, port = web
    assert not launch.is_ap_coder(port)  # something else answers on that port
    (site / "app" / "static").mkdir(parents=True)
    (site / "app" / "static" / "InterVariable.woff2").write_bytes(b"wOF2")
    assert launch.is_ap_coder(port)
    assert launch.running_dashboard(["--port", str(port)], {}) == port
    assert launch.running_dashboard([], {"port": port}) == port  # the port the last start used
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    assert not launch.is_ap_coder(closed)


def test_second_start_while_running_only_opens_the_browser(launch, monkeypatch):
    monkeypatch.setattr(launch, "running_dashboard", lambda rest, state: 8501)
    opened: list[str] = []
    monkeypatch.setattr(launch, "open_browser", opened.append)
    monkeypatch.setattr(launch, "ensure_packages", lambda state: pytest.fail("set up again"))
    monkeypatch.setattr(launch, "start_dashboard", lambda rest, state: pytest.fail("a second copy started"))
    assert launch.run(launch.parse([])[0], []) == 0
    assert opened == ["http://localhost:8501"]


def test_ports(launch):
    assert launch.requested_port(["--port", "8597"]) == 8597
    assert launch.requested_port(["--port=8502"]) == 8502
    assert launch.requested_port(["--address", "0.0.0.0"]) is None
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        taken = s.getsockname()[1]
        assert launch.free_port(taken) != taken


def test_browser_none_opens_nothing(launch, monkeypatch):
    import webbrowser

    monkeypatch.setenv("BROWSER", "none")
    monkeypatch.setattr(webbrowser, "open", lambda url: pytest.fail("opened"))
    launch.open_browser("http://localhost:8501")


# --- Readiness and the self-check ------------------------------------------------------------------------------


def _quiet_setup(launch, monkeypatch, installed=False):
    monkeypatch.setattr(launch, "running_dashboard", lambda rest, state: None)
    monkeypatch.setattr(launch, "ensure_packages", lambda state: ("ok", "Packages: all 13 in place", installed))
    monkeypatch.setattr(launch, "ensure_data_folder", lambda: None)
    monkeypatch.setattr(launch, "ensure_models", lambda first: None)
    started: list[list[str]] = []
    monkeypatch.setattr(launch, "start_dashboard", lambda rest, state: started.append(rest) or 0)
    checks: list[int] = []
    monkeypatch.setattr(launch, "self_check", lambda: checks.append(1) or (True, "Self-check OK: 10 of 10"))
    return started, checks


def test_first_start_runs_the_self_check_later_starts_do_not(launch, monkeypatch, capsys):
    started, checks = _quiet_setup(launch, monkeypatch)
    args, rest = launch.parse(["--venv=created", "--port", "8597"])
    assert launch.run(args, rest) == 0
    out = capsys.readouterr().out
    assert checks == [1] and started == [["--port", "8597"]]
    assert "AP Coder readiness:" in out and "Self-check OK: 10 of 10" in out and "Packages: all 13" in out
    assert "LM Studio: not running (optional" in out and "Data folder" in out

    args, rest = launch.parse([])
    assert launch.run(args, rest) == 0
    out = capsys.readouterr().out
    assert checks == [1]  # not again
    assert "reusing .venv" in out and "Self-check OK at setup" in out

    args, rest = launch.parse(["--check", "--no-start"])
    launch.run(args, rest)
    assert checks == [1, 1] and len(started) == 2


def test_a_failed_self_check_is_said_plainly_and_ap_coder_still_starts(launch, monkeypatch, capsys):
    started, _ = _quiet_setup(launch, monkeypatch)
    monkeypatch.setattr(launch, "self_check", lambda: (False, "Self-check FAILED: FAIL acme.pdf"))
    args, rest = launch.parse(["--venv=created"])
    assert launch.run(args, rest) == 0
    out = capsys.readouterr().out
    assert "Self-check FAILED: FAIL acme.pdf" in out and "AP Coder still starts" in out and started


def test_self_check_reads_pilot_checks_verdict(launch, monkeypatch):
    out = "OK   a.pdf\n\n10 of 10 sample invoices read right on this computer.\nOK   read as a scan\n"
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=out, stderr=""))
    ok, text = launch.self_check()
    assert ok and text.startswith("Self-check OK: 10 of 10 sample invoices read right")
    bad = "FAIL a.pdf\n       grand_total: 1\n\n9 of 10 sample invoices read right.\n"
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=bad, stderr=""))
    ok, text = launch.self_check()
    assert not ok and text == "Self-check FAILED: FAIL a.pdf"


def test_lm_studio_is_only_reported(launch, monkeypatch):
    from ap_coder import local_llm

    assert launch.lm_studio_line()[1].startswith("LM Studio: not running (optional")
    monkeypatch.setattr(local_llm, "check_server", lambda llm, **k: local_llm.ModelStatus(
        base_url="http://127.0.0.1:1234/v1", reachable=True, model="qwen3.5-9b", lm_studio=True))  # fmt: skip
    assert launch.lm_studio_line() == ("ok", "LM Studio: model qwen3.5-9b loaded")


def test_marks_fall_back_to_ascii(launch, monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    assert not launch.fancy_marks() and launch.mark("ok") == "[OK]"
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))
    assert launch.fancy_marks() and launch.mark("ok") == "✓"
    monkeypatch.setattr(launch, "WINDOWS", True)  # the classic Windows console's fonts lack ✓
    monkeypatch.delenv("WT_SESSION", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    assert launch.mark("ok") == "[OK]"


def test_shortcut_is_made_once(launch, tmp_path, monkeypatch):
    from ap_coder import paths

    desk = tmp_path / "Desktop"
    desk.mkdir()
    made: list[Path] = []
    monkeypatch.setattr(launch, "WINDOWS", True)
    monkeypatch.setattr(launch, "desktop", lambda: desk)
    monkeypatch.setattr(launch, "write_shortcut", lambda folder: made.append(folder) or True)
    assert launch.ensure_shortcut().startswith("Desktop shortcut")
    assert launch.ensure_shortcut() == "" and made == [desk]  # never a second one, even if deleted
    paths.write_user_settings(desktop_shortcut=None)
    (desk / "AP Coder.lnk").write_text("an older installer's")
    assert launch.ensure_shortcut() == "" and made == [desk]  # one already there: kept


FIXED_PLACES = ("/opt/homebrew/bin/python3.12", "/usr/local/bin/python3.12", "/Library/Frameworks/Python.framework")


@pytest.mark.skipif(sys.platform == "win32", reason="runs the bash launcher")
def test_mac_launcher_explains_a_missing_python(tmp_path):
    """No suitable Python: one plain line on what to do, and it stops (AP_NO_PAUSE: no waiting)."""
    if any(Path(p).exists() for p in FIXED_PLACES):
        pytest.skip("this computer has a Python where the launcher always looks")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("dirname", "uname"):
        real = subprocess.run(["bash", "-c", f"command -v {tool}"], capture_output=True, text=True).stdout.strip()
        (bindir / tool).symlink_to(real)
    code = tmp_path / "code"
    code.mkdir()
    (code / "APProcessor.command").write_bytes((ROOT / "APProcessor.command").read_bytes())
    out = subprocess.run(["/bin/bash", str(code / "APProcessor.command")], capture_output=True, text=True,
                         env={"PATH": str(bindir), "AP_NO_PAUSE": "1"}, timeout=60)  # fmt: skip
    assert out.returncode == 1
    assert "needs Python 3.11, 3.12 or 3.13" in out.stdout and "What to do:" in out.stdout
