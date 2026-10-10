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


WHEELHOUSE = ["--no-index", "--find-links", "wheelhouse"]


def _fake_pip(launch, monkeypatch, missing: list[str], self_problem: str = "", fails: set[str] = frozenset(),
              source: list[str] | None = WHEELHOUSE):  # fmt: skip
    """pip that "installs" what it is asked for (except ``fails``) from ``source`` (the bundle's wheelhouse by
    default); records each call."""
    monkeypatch.setattr(launch, "pip_source", lambda: source)
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
    assert "wheelhouse folder has no streamlit for Python" in exc.value.hint and "3.12" in exc.value.hint
    _fake_pip(launch, monkeypatch, ["streamlit>=1.62"], fails={"streamlit>=1.62"}, source=[])  # opted in: PyPI
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert "HTTPS_PROXY" in exc.value.hint


def test_packages_come_only_from_the_wheelhouse_unless_the_internet_is_allowed(launch, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    assert launch.pip_source() is None  # offline (the default) and no wheelhouse: nowhere to install from
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    assert launch.pip_source() == []  # a developer's opt-in: PyPI
    (tmp_path / "wheelhouse").mkdir()
    assert launch.pip_source() == ["--no-index", "--find-links", str(tmp_path / "wheelhouse")]
    monkeypatch.delenv("AP_ALLOW_INTERNET")
    assert launch.pip_source() == ["--no-index", "--find-links", str(tmp_path / "wheelhouse")]


def _record_pip(launch, monkeypatch) -> list[list[str]]:
    """The real pip_install, with pip itself replaced: every command line it would run."""
    commands: list[list[str]] = []

    class Proc:
        def __init__(self, cmd, **kwargs):
            commands.append(list(cmd))
            self.stdout = iter(())

        def wait(self):
            return 1  # nothing installed

    monkeypatch.setattr(launch.subprocess, "Popen", Proc)
    return commands


def test_offline_pip_never_runs_without_no_index(launch, tmp_path, monkeypatch, capsys):
    """The offline default: pip runs only against the bundle's wheelhouse (``--no-index``), never PyPI."""
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    commands = _record_pip(launch, monkeypatch)
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["streamlit>=1.62", "rapidocr>=3.4,<3.5"], "x"))
    with pytest.raises(launch.SetupError) as exc:  # no wheelhouse: nothing run, and it says what is missing
        launch.ensure_packages({})
    assert commands == [] and "streamlit" in str(exc.value) and "offline bundle" in str(exc.value)
    assert not launch.pip_install(["openpyxl"]) and commands == []
    (tmp_path / "wheelhouse").mkdir()
    with pytest.raises(launch.SetupError):
        launch.ensure_packages({})
    assert commands and all("--no-index" in cmd for cmd in commands)
    assert "no internet needed" in capsys.readouterr().out


def test_offline_without_wheelhouse_still_starts_when_the_core_is_there(launch, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    commands = _record_pip(launch, monkeypatch)
    ocr = [r for r in launch.check_deps.requirements(extras=("ocr",)) if launch.is_ocr(r)]
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (ocr, "Missing: ap_coder (pip install -e .)"))
    level, text, installed = launch.ensure_packages({})
    assert (level, installed, commands) == ("warn", False, [])
    assert "missing rapidocr" in text and "offline bundle" in text and "scanned invoices can't be read" in text
    monkeypatch.setattr(launch, "missing_packages", lambda extras: ([], "Missing: ap_coder (pip install -e .)"))
    level, text, _ = launch.ensure_packages({})
    assert level == "ok" and "runs from this folder" in text and commands == []


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


@pytest.fixture
def lock(launch, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "LOCK", tmp_path / ".ap_coder_launch.lock")
    monkeypatch.setattr(launch, "_held", [])
    yield launch
    launch.release_lock()


def _other_start_holds_the_lock(launch):
    """The lock taken as another start would take it (a second open file: the same as another process)."""
    assert launch.take_lock()
    return launch._held.pop()


def test_one_start_at_a_time(lock):
    launch = lock
    other = _other_start_holds_the_lock(launch)
    assert not launch.take_lock()  # a second double-click during the first setup does not set up again
    other.close()  # the first start ended
    assert launch.take_lock() and launch.take_lock()  # (taken once; asking again in the same start is fine)


def test_a_second_start_waits_for_the_first_ones_dashboard(lock, monkeypatch, capsys):
    launch = lock
    other = _other_start_holds_the_lock(launch)
    answers = iter([None, None, 8597])  # the first start is still installing, then its dashboard is up
    monkeypatch.setattr(launch, "running_dashboard", lambda argv, state: next(answers))
    opened: list[str] = []
    monkeypatch.setattr(launch, "open_browser", opened.append)
    assert launch.wait_for_other(["--port", "8597"], every=0) == 0
    assert opened == ["http://localhost:8597"]
    out = capsys.readouterr().out
    assert "being set up or started in another window" in out and "already running" in out
    # The first start ended without a dashboard (e.g. --no-start): this one takes over the setup.
    monkeypatch.setattr(launch, "running_dashboard", lambda argv, state: None)
    other.close()
    assert launch.wait_for_other([], every=0) is None and launch._held


def test_main_waits_instead_of_setting_up_twice(lock, monkeypatch):
    launch = lock
    other = _other_start_holds_the_lock(launch)
    monkeypatch.setattr(launch, "wait_for_other", lambda argv: 0)
    monkeypatch.setattr(launch, "bootstrap", lambda argv: pytest.fail("a second .venv setup started"))
    monkeypatch.setattr(launch, "run", lambda args, rest: pytest.fail("a second setup started"))
    assert launch.main([]) == 0
    other.close()


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
    monkeypatch.setattr(launch, "ensure_models", lambda first, state: None)
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


@pytest.fixture
def no_models(launch, tmp_path, monkeypatch):
    """RapidOCR 3 installed with an empty models folder; every download attempt recorded (and refused)."""
    import fetch_models  # scripts/ is on sys.path: the launcher imports it the same way

    from ap_coder import offline

    empty = tmp_path / "rapidocr_models"
    empty.mkdir()
    monkeypatch.setattr(offline, "ppocrv5_installed", lambda: True)
    monkeypatch.setattr(offline, "model_dir", lambda: empty)
    monkeypatch.setattr(fetch_models, "BUNDLE_MODELS", tmp_path / "models")  # no bundle models/ folder
    downloads: list[str] = []

    def urlopen(url, timeout=None):
        downloads.append(url)
        raise OSError("network blocked")

    monkeypatch.setattr(fetch_models.urllib.request, "urlopen", urlopen)
    return fetch_models, downloads


def test_offline_models_are_never_downloaded_and_a_failure_is_said_once(launch, no_models, monkeypatch, capsys):
    fetch_models, downloads = no_models
    ran: list[list[str]] = []
    real_main = fetch_models.main
    monkeypatch.setattr(fetch_models, "main", lambda argv: ran.append(argv) or real_main(argv))
    state: dict = {}
    for _ in range(3):  # three starts
        launch.ensure_models(first=False, state=state)
    out = capsys.readouterr().out
    assert downloads == [] and ran == []  # offline, and no bundle models folder: nothing tried at all
    assert out.count("never downloads them") == 1 and state["models_failed"]

    fetch_models.BUNDLE_MODELS.mkdir()  # the full bundle unzipped: tried again, once, from its folder only
    for _ in range(2):
        launch.ensure_models(first=False, state=state)
    out = capsys.readouterr().out
    assert downloads == [] and len(ran) == 1 and "Copying the OCR models from the offline bundle" in out
    assert out.count("Not tried again") == 1


def test_models_are_downloaded_only_with_the_opt_in(launch, no_models, monkeypatch, capsys):
    fetch_models, downloads = no_models
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    state: dict = {}
    launch.ensure_models(first=False, state=state)
    launch.ensure_models(first=False, state=state)  # the failed download is not retried on every start
    assert len(downloads) == 3 and "Downloading the OCR models" in capsys.readouterr().out


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
