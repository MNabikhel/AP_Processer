"""The one-button launcher (scripts/launch.py, behind APProcessor.bat / APProcessor.command): it sets up only
what is missing, says what is ready, and never starts a second copy of a dashboard that is already running."""

from __future__ import annotations

import functools
import http.server
import importlib.util
import io
import json
import os
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
    module.real_ensure_second_ocr = module.ensure_second_ocr
    monkeypatch.setattr(module, "ensure_second_ocr", lambda state: "")  # its own tests below, on any Python
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
    calls = _fake_pip(launch, monkeypatch, ["fastapi>=0.115,<1", "rapidocr>=3.4,<3.5"], "Missing: ap_coder")
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


def _second_ocr(launch, monkeypatch, *, python=(3, 14), has=(), ok=True):
    """``ensure_second_ocr`` on ``python`` with the modules in ``has`` importable; pip succeeds when ``ok``."""
    monkeypatch.setattr(launch.sys, "version_info", (*python, 0, "final", 0))
    monkeypatch.setattr(launch.importlib.util, "find_spec", lambda name: object() if name in has else None)
    monkeypatch.setattr(launch, "pip_source", lambda: WHEELHOUSE)
    calls: list[list[str]] = []
    monkeypatch.setattr(launch, "pip_install", lambda args: calls.append(list(args)) or ok)
    return calls


def test_the_second_ocr_engine_is_installed_on_python_3_14(launch, monkeypatch):
    """rapidocr-onnxruntime says "below 3.13" in its metadata but works there: installed without that check."""
    calls = _second_ocr(launch, monkeypatch, has={"rapidocr"})
    assert launch.real_ensure_second_ocr({}) == ""
    assert calls == [["--ignore-requires-python", launch.SECOND_OCR]]


def test_the_second_ocr_engine_is_left_alone_where_pip_installs_it(launch, monkeypatch):
    calls = _second_ocr(launch, monkeypatch, python=(3, 12), has={"rapidocr"})  # pip installs it with the rest
    assert launch.real_ensure_second_ocr({}) == "" and calls == []
    calls = _second_ocr(launch, monkeypatch, has={"rapidocr", "rapidocr_onnxruntime"})  # already there
    assert launch.real_ensure_second_ocr({}) == "" and calls == []
    calls = _second_ocr(launch, monkeypatch, has=set())  # no OCR add-on at all: said elsewhere
    assert launch.real_ensure_second_ocr({}) == "" and calls == []


def test_a_second_ocr_engine_that_cannot_be_installed_is_said_once(launch, monkeypatch):
    state: dict = {}
    calls = _second_ocr(launch, monkeypatch, has={"rapidocr"}, ok=False)
    assert "one OCR engine" in launch.real_ensure_second_ocr(state)
    assert state["ocr2_failed"] == launch.fingerprint() and len(calls) == 1
    assert launch.real_ensure_second_ocr(state) == "" and len(calls) == 1  # not tried again on every start


def test_a_failed_install_stops_with_what_to_do(launch, monkeypatch):
    # (no bundle's manifest: run in an offline bundle's folder, the test read that bundle's and failed there)
    monkeypatch.setattr(launch, "bundle_pythons", lambda here_only=True: [])
    _fake_pip(launch, monkeypatch, ["fastapi>=0.115,<1"], fails={"fastapi>=0.115,<1"})
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert "wheelhouse folder has no fastapi for Python" in exc.value.hint and "3.12" in exc.value.hint
    _fake_pip(launch, monkeypatch, ["fastapi>=0.115,<1"], fails={"fastapi>=0.115,<1"}, source=[])  # opted in: PyPI
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert "HTTPS_PROXY" in exc.value.hint


def test_packages_come_only_from_the_wheelhouse_unless_the_internet_is_allowed(launch, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    (tmp_path / "bundle_manifest.json").write_text("{}", encoding="utf-8")  # an offline bundle...
    assert launch.pip_source() is None  # ...without its wheelhouse: nowhere to install from, never the internet
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    assert launch.pip_source() == []  # a developer's opt-in: PyPI
    (tmp_path / "wheelhouse").mkdir()
    assert launch.pip_source() == ["--no-index", "--find-links", str(tmp_path / "wheelhouse")]
    monkeypatch.delenv("AP_ALLOW_INTERNET")
    assert launch.pip_source() == ["--no-index", "--find-links", str(tmp_path / "wheelhouse")]


def test_the_code_alone_downloads_its_packages_like_closedesk(launch, tmp_path, monkeypatch):
    """GitHub's ZIP (no manifest, wheelhouse or models): the setup installs from PyPI, as CloseDesk does, unless
    AP_ALLOW_INTERNET=0 forbids it; an offline bundle never does."""
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    monkeypatch.setattr(launch.fetch_models, "BUNDLE_MODELS", tmp_path / "models")
    monkeypatch.delenv("AP_ALLOW_INTERNET", raising=False)
    assert launch.plain_download() and launch.pip_source() == []
    for value in ("0", "false", "No", "off"):
        monkeypatch.setenv("AP_ALLOW_INTERNET", value)
        assert not launch.plain_download() and launch.pip_source() is None
    monkeypatch.delenv("AP_ALLOW_INTERNET")
    (tmp_path / "models").mkdir()  # the bundle's OCR models: an offline bundle (missing its wheelhouse)
    assert not launch.plain_download() and launch.pip_source() is None
    (tmp_path / "models").rmdir()
    (tmp_path / "bundle_manifest.json").write_text("{}", encoding="utf-8")
    assert not launch.plain_download() and launch.pip_source() is None


def test_the_opt_in_for_a_download_step_is_never_left_for_the_dashboard(launch, monkeypatch):
    monkeypatch.delenv("AP_ALLOW_INTERNET", raising=False)
    with launch.setup_downloads():
        assert launch.internet_allowed()
    assert "AP_ALLOW_INTERNET" not in launch.os.environ and "AP_ALLOW_INTERNET" not in launch.child_env()
    monkeypatch.setenv("AP_ALLOW_INTERNET", "0")
    with launch.setup_downloads():
        pass
    assert launch.os.environ["AP_ALLOW_INTERNET"] == "0"


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
    (tmp_path / "bundle_manifest.json").write_text("{}", encoding="utf-8")
    commands = _record_pip(launch, monkeypatch)
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["fastapi>=0.115,<1", "rapidocr>=3.4,<3.5"], "x"))
    with pytest.raises(launch.SetupError) as exc:  # no wheelhouse: nothing run, and it says what is missing
        launch.ensure_packages({})
    assert commands == [] and "fastapi" in str(exc.value) and "offline bundle" in str(exc.value)
    assert not launch.pip_install(["openpyxl"]) and commands == []
    (tmp_path / "wheelhouse").mkdir()
    with pytest.raises(launch.SetupError):
        launch.ensure_packages({})
    assert commands and all("--no-index" in cmd for cmd in commands)
    assert "no internet needed" in capsys.readouterr().out


def test_offline_without_wheelhouse_still_starts_when_the_core_is_there(launch, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    (tmp_path / "bundle_manifest.json").write_text("{}", encoding="utf-8")
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


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_a_python_without_venv_support_is_said_as_that_on_linux(launch, monkeypatch, platform):
    """Debian and Ubuntu ship their Python without what ``python -m venv`` needs (ensurepip) unless the
    python3.X-venv package is installed: reinstalling Python from python.org is not the fix there."""
    ran: list[list[str]] = []

    def run(cmd, **kwargs):
        ran.append(cmd)
        return SimpleNamespace(returncode=1, stdout="", stderr="")  # neither venv nor ensurepip works

    monkeypatch.setattr(launch.subprocess, "run", run)
    monkeypatch.setattr(launch.sys, "platform", platform)
    monkeypatch.setattr(launch, "venv_works", lambda: False)
    launch.VENV.rmdir()
    with pytest.raises(launch.SetupError) as failed:
        launch.ensure_venv("python3")
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    if platform == "linux":
        assert f"python{version}-venv" in failed.value.hint and "apt" in failed.value.hint
        assert ["python3", "-m", "ensurepip", "--version"] in ran
    else:
        assert "python.org" in failed.value.hint and "-venv" not in failed.value.hint


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
    """This folder's own AP Coder (its version, answering /healthz) is found; anything else on the port is not."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # never through a proxy
    site, port = web
    assert launch.running_dashboard(["--port", str(port)], {}) is None  # something else answers on that port
    version = launch.identity()["version"]
    health = {"ok": True, "app": "ap-coder", "version": version, "folder": str(launch.ROOT), "pid": 1, "port": port}
    (site / "healthz").write_text(json.dumps(health))
    assert launch.running_dashboard(["--port", str(port)], {}) == port
    assert launch.running_dashboard([], {"port": port}) == port  # the port the last start used
    (site / "healthz").write_text(json.dumps({**health, "folder": "/another/folder"}))
    assert launch.running_dashboard([], {"port": port}) is None  # another install's: not this one to open
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    assert launch.running_dashboard(["--port", str(closed)], {}) is None


def test_another_accounts_running_ap_coder_is_named_never_opened(launch, web, monkeypatch, capsys):
    """B-168: with two Windows accounts on one AP Coder folder and data folder, the second person's double-click opened
    the first person's dashboard (it approves under their name). It says whose AP Coder is open and starts nothing."""
    site, port = web
    health = {"ok": True, "app": "ap-coder", "version": launch.identity()["version"], "folder": str(launch.ROOT),
              "pid": 1, "port": port, "user": "someone-else"}  # fmt: skip
    (site / "healthz").write_text(json.dumps(health))
    monkeypatch.setattr(launch, "candidate_ports", lambda rest, state: [port])
    opened: list[str] = []
    monkeypatch.setattr(launch, "open_browser", opened.append)
    monkeypatch.setattr(launch, "ensure_packages", lambda state: pytest.fail("set up under another person's AP Coder"))
    assert launch.running_dashboard([], {}) is None
    assert launch.run(launch.parse([])[0], []) == 1
    monkeypatch.setattr(launch, "take_lock", lambda: False)  # (their start holds the folder's lock)
    assert launch.wait_for_other([], timeout=60, every=0) == 1
    out = capsys.readouterr().out
    assert opened == [] and out.count("open in someone-else's account on this computer") == 2
    assert "Ask someone-else to close" in out and "waiting for it" not in out


def test_second_start_while_running_only_opens_the_browser(launch, monkeypatch):
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("open", 8501))
    opened: list[str] = []
    monkeypatch.setattr(launch, "open_browser", opened.append)
    monkeypatch.setattr(launch, "ensure_packages", lambda state: pytest.fail("set up again"))
    monkeypatch.setattr(launch, "start_dashboard", lambda rest, state: pytest.fail("a second copy started"))
    assert launch.run(launch.parse([])[0], []) == 0
    assert opened == ["http://127.0.0.1:8501"]


@pytest.fixture
def lock(launch, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "LOCK", tmp_path / ".ap_coder_launch.lock")
    monkeypatch.setattr(launch, "_held", [])
    monkeypatch.setattr(launch, "real_stop_older", launch.stop_older, raising=False)
    monkeypatch.setattr(launch, "stop_older", lambda argv: None)  # (never a real server on this computer)
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
    assert opened == ["http://127.0.0.1:8597"]
    out = capsys.readouterr().out
    assert "being set up or started in another window" in out and "already running" in out
    # The first start ended without a dashboard (e.g. --no-start): this one takes over the setup.
    monkeypatch.setattr(launch, "running_dashboard", lambda argv, state: None)
    other.close()
    assert launch.wait_for_other([], every=0) is None and launch._held


def test_an_older_version_running_from_this_folder_is_taken_over_not_waited_for(lock, monkeypatch, capsys):
    """Unzipped over the folder of an AP Coder still running (0.1's dashboard): its start holds this folder's lock
    and its dashboard is never this version's, so the new start said "being set up in another window: waiting"
    for half an hour. The older one is stopped (its start then ends and frees the lock) and this one carries on."""
    launch = lock
    other = _other_start_holds_the_lock(launch)  # the old version's start, serving its dashboard
    old = launch.running.Server(8501, legacy=True)
    stops: list[int] = []

    def take_over(root, version, ports, data, say):
        stops.append(1)
        other.close()  # stopped: the old start ends
        return "clear", None

    monkeypatch.setattr(launch.running, "find", lambda ports, data: [] if stops else [old])
    monkeypatch.setattr(launch.running, "take_over", take_over)
    monkeypatch.setattr(launch, "running_dashboard", lambda argv, state: None)
    monkeypatch.setattr(launch, "stop_older", launch.real_stop_older)
    assert launch.wait_for_other([], timeout=1, every=0) is None and launch._held and stops == [1]
    assert "has not opened the dashboard yet" not in capsys.readouterr().out


def test_main_waits_instead_of_setting_up_twice(lock, monkeypatch):
    launch = lock
    other = _other_start_holds_the_lock(launch)
    monkeypatch.setattr(launch, "wait_for_other", lambda argv: 0)
    monkeypatch.setattr(launch, "bootstrap", lambda argv: pytest.fail("a second .venv setup started"))
    monkeypatch.setattr(launch, "run", lambda args, rest: pytest.fail("a second setup started"))
    assert launch.main([]) == 0
    other.close()


def test_the_installer_refuses_while_ap_coder_is_running(lock, monkeypatch, capsys):
    """APProcessor --installer while AP Coder runs (its start holds the lock) says to close it first and fails: it does
    not open the browser and report success with the installer's steps skipped, nor reinstall under the running app."""
    import install

    launch = lock
    other = _other_start_holds_the_lock(launch)
    monkeypatch.delenv(launch.LOCK_HELD_ENV, raising=False)
    monkeypatch.setattr(launch, "running_dashboard", lambda rest, state: 8501)
    monkeypatch.setattr(launch, "open_browser", lambda url: pytest.fail("opened the running dashboard"))
    monkeypatch.setattr(launch, "in_venv", lambda: True)
    monkeypatch.setattr(install, "main", lambda argv, venv=None: pytest.fail("installed under the running app"))
    assert launch.main(["--installer", "--yes"]) != 0
    assert "close AP Coder first" in capsys.readouterr().out
    other.close()


def test_ports(launch, monkeypatch):
    # this computer's own rules for binding: on Windows SO_REUSEADDR would let a port in use be bound again (the
    # fixture's WINDOWS=False made the Windows runner test the Mac/Linux path), so the Windows path is the one tested
    monkeypatch.setattr(launch, "WINDOWS", os.name == "nt")
    assert launch.requested_port(["--port", "8597"]) == 8597
    assert launch.requested_port(["--port=8502"]) == 8502
    assert launch.requested_port(["--address", "0.0.0.0"]) is None
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        taken = s.getsockname()[1]
        found = launch.free_port(taken)
        assert found != taken, f"listening({taken}) said {launch.running.listening(taken)}"


def test_a_free_port_is_found_when_the_next_fifty_are_refused(launch, monkeypatch):
    """Windows reserves blocks of ports (Hyper-V, WSL): when none of the 50 after the one asked for can be bound,
    the port asked for (in use) was given back; now one the system says is free."""
    import socket as real

    refused = set(range(40000, 40050))

    class Probe(real.socket):
        def bind(self, address):
            if address[1] in refused:
                raise OSError(10013, "forbidden by its access permissions")
            return super().bind(address)

    monkeypatch.setattr(real, "socket", Probe)
    port = launch.free_port(40000)
    assert port not in refused and port > 0


def test_browser_none_opens_nothing(launch, monkeypatch):
    import webbrowser

    monkeypatch.setenv("BROWSER", "none")
    monkeypatch.setattr(webbrowser, "open", lambda url: pytest.fail("opened"))
    launch.open_browser("http://localhost:8501")


# --- Readiness and the self-check ------------------------------------------------------------------------------


def _quiet_setup(launch, monkeypatch, installed=False):
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("clear", None))
    monkeypatch.setattr(launch, "ensure_packages", lambda state: ("ok", "Packages: all 13 in place", installed))
    monkeypatch.setattr(launch, "ensure_data_folder", lambda: launch.VENV.parent / "data")
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
    assert "reusing .venv" not in out and "Self-check OK at setup" in out  # a later start: quiet

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
    skipped = "OK   a.pdf\n\n10 of 10 sample invoices read right.\nSKIP read as a scan with local OCR: not installed\n"
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=skipped,
                                                                                 stderr=""))  # fmt: skip
    ok, text = launch.self_check()
    assert ok and "no scan tried (no OCR)" in text and "a scan with local OCR" not in text


def test_pilot_check_passes_without_the_ocr_add_on_and_says_the_scan_was_not_tried(monkeypatch, capsys):
    """Without the OCR add-on (it can't be installed on every computer), the self-check failed on the scan sample:
    the launcher already warns that OCR isn't installed, and a failed self-check keeps an older AP Coder's folder
    on disk for ever. The samples are still checked; the scan is reported as not tried."""
    from ap_coder.capture import layout

    spec = importlib.util.spec_from_file_location("ap_script_pilot_check", ROOT / "scripts" / "pilot_check.py")
    pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pilot)
    monkeypatch.setattr(layout, "ocr_available", lambda: False)
    monkeypatch.setattr(pilot, "check", lambda pdf, pipe: [])
    assert pilot.main() == 0
    out = capsys.readouterr().out
    assert "SKIP read as a scan with local OCR" in out and "FAIL" not in out
    monkeypatch.setattr(pilot, "check", lambda pdf, pipe: ["grand_total: 1"])
    assert pilot.main() == 1  # a sample read wrong still fails


def _lm_studio(local_llm, monkeypatch, **fields):
    status = local_llm.ModelStatus(base_url="http://127.0.0.1:1234/v1", **fields)
    monkeypatch.setattr(local_llm, "check_server", lambda llm, **k: status)
    return status


GEMMA = "lmstudio-community/gemma-4-12B-it-QAT-GGUF"


def test_lm_studio_is_only_reported(launch, monkeypatch):
    from ap_coder.ai import local_llm

    _lm_studio(local_llm, monkeypatch)  # nothing answers
    assert launch.lm_studio_line()[1].startswith("LM Studio: not running (optional")
    _lm_studio(local_llm, monkeypatch, reachable=True, lm_studio=True)
    assert launch.lm_studio_line()[0] == "warn" and "no chat model is loaded" in launch.lm_studio_line()[1]
    _lm_studio(local_llm, monkeypatch, reachable=True, lm_studio=True, model=GEMMA, vision=True,
               loaded=[GEMMA, "ath-maas_ovisocr2", "text-embedding-qwen3-embedding-0.6b"])  # fmt: skip
    assert launch.lm_studio_line() == ("ok", f"Chat model: {GEMMA} loaded in LM Studio (can see pages)")
    _lm_studio(local_llm, monkeypatch, reachable=True, lm_studio=True, model=GEMMA, loaded=[GEMMA, "qwen3.5-9b"])
    assert launch.lm_studio_line()[1].endswith(
        "; also loaded: qwen3.5-9b (keep one chat model loaded: unload the one you don't want)"
    )
    _lm_studio(local_llm, monkeypatch, reachable=True, lm_studio=True, model=GEMMA)  # downloaded, not loaded
    level, text = launch.lm_studio_line()
    assert level == "info" and "downloaded but not loaded" in text
    monkeypatch.setattr(local_llm, "check_server", lambda llm, **k: 1 / 0)
    assert launch.lm_studio_line() == ("info", "Chat model: not checked (optional)")


def test_the_double_click_asks_the_loaded_chat_model_once_per_model(launch, monkeypatch):
    """What doctor --online asked, asked by the start itself: only of a loaded model, then remembered."""
    from ap_coder.ai import local_llm

    asked: list[int] = []
    answers = [("ok", "Chat model test passed: accounts from the chart in 2s: line 1 -> 6100, line 2 -> 6200")]
    monkeypatch.setattr(launch, "_ask_chat_model", lambda: asked.append(1) or answers[0])
    state: dict = {}
    _lm_studio(local_llm, monkeypatch, reachable=True, lm_studio=True, model=GEMMA)  # not loaded: never asked
    assert launch.chat_test_line(state) is None and asked == []
    _lm_studio(local_llm, monkeypatch, reachable=True, lm_studio=True, model=GEMMA, loaded=[GEMMA])
    assert launch.chat_test_line(state) == answers[0] and len(asked) == 1 and state["chat_test"]["ok"]
    level, text = launch.chat_test_line(state)  # the next start: remembered, not asked again
    assert level == "ok" and text.startswith("Chat model test passed (") and len(asked) == 1
    assert launch.chat_test_line(state, force=True) == answers[0] and len(asked) == 2  # --check asks again
    _lm_studio(local_llm, monkeypatch, reachable=True, lm_studio=True, model="qwen3.5-9b", loaded=["qwen3.5-9b"])
    launch.chat_test_line(state)  # another model loaded: asked again
    assert len(asked) == 3
    answers[0] = ("warn", "Chat model test: the model answered in 3s but gave no account from the chart")
    state["chat_test"]["key"] = "changed"
    launch.chat_test_line(state)
    launch.chat_test_line(state)  # a failed test is asked again on every start
    assert len(asked) == 5 and not state["chat_test"]["ok"]


def test_the_chat_model_test_never_holds_up_the_start(launch, monkeypatch):
    import threading

    from ap_coder.ops import doctor

    release = threading.Event()
    monkeypatch.setattr(doctor, "_local_dry_run", lambda settings, reference: release.wait(5) and None)
    monkeypatch.setattr(launch, "CHAT_TEST_TIMEOUT", 0.2)
    level, text = launch._ask_chat_model()
    release.set()
    assert level == "warn" and "no answer within 0 s" in text and "starts anyway" in text
    monkeypatch.setattr(doctor, "_local_dry_run", lambda settings, reference: ("local dry run", "PASS", "fine"))
    monkeypatch.setattr(launch, "CHAT_TEST_TIMEOUT", 30.0)
    assert launch._ask_chat_model() == ("ok", "Chat model test passed: fine")
    monkeypatch.setattr(doctor, "_local_dry_run", lambda settings, reference: 1 / 0)
    level, text = launch._ask_chat_model()
    assert level == "warn" and "ZeroDivisionError" in text


def test_a_start_asks_lm_studio_once_for_the_chat_model(launch, monkeypatch):
    """The chat line, its test and the setup checks share one question to LM Studio (the page reader asks its own)."""
    from ap_coder.ai import local_llm
    from ap_coder.ops import doctor

    asked: list[int] = []
    status = local_llm.ModelStatus(base_url="http://127.0.0.1:1234/v1", reachable=True, lm_studio=True, model=GEMMA,
                                   loaded=[GEMMA], vision=True)  # fmt: skip
    monkeypatch.setattr(local_llm, "check_server", lambda llm, **k: asked.append(1) or status)
    monkeypatch.setattr(launch, "page_reader_line", lambda: ("info", "Page reader: -"))
    monkeypatch.setattr(launch, "_ask_chat_model", lambda: ("ok", "Chat model test passed: fine"))
    lines = launch.readiness(("ok", "Packages: all in place"), {}, force=False)
    texts = [t for _, t in lines]
    assert len(asked) == 1, f"LM Studio asked {len(asked)} times"
    assert any(t.startswith(f"Chat model: {GEMMA} loaded") for t in texts) and "Chat model test passed: fine" in texts
    assert any(t.startswith("Setup checks: all ") for t in texts)
    areas = [c.area for c in doctor.run_checks(launch_settings(), lambda: 1 / 0, llm_checks=False)]
    assert "local model" not in areas and "page reader" not in areas
    areas = [c.area for c in doctor.run_checks(launch_settings(), lambda: 1 / 0)]  # the doctor itself: both kept
    assert "local model" in areas and "page reader" in areas


def launch_settings():
    from ap_coder.core.config import Settings

    return Settings()


def test_the_start_says_no_gl_accounts_were_imported_yet(launch, monkeypatch):
    """B-169: on a new data folder the GL accounts are the bundled sample's, and the summary said "Setup checks: all 9
    passed (GL accounts, ...)" as if the setup were done."""
    monkeypatch.delenv("AP_REFERENCE_DIR", raising=False)
    lines = launch.setup_check_lines()
    assert ("warn", "GL accounts: none imported yet, so the sample ones are used: import yours on GL accounts & "
                    "tax") in lines  # fmt: skip
    assert not any(text.startswith("Setup checks: all ") and "(GL accounts" in text for _level, text in lines)
    monkeypatch.setenv("AP_REFERENCE_DIR", str(ROOT / "data"))  # a reference folder of its own: nothing to say
    assert not any("none imported yet" in text for _level, text in launch.setup_check_lines())


def test_the_start_runs_the_doctors_setup_checks(launch, monkeypatch):
    """GL accounts, tax mapping and settings: one line when all is well, each problem in the doctor's words."""
    from ap_coder.ops import doctor

    monkeypatch.setenv("AP_REFERENCE_DIR", str(ROOT / "data"))  # GL accounts of its own, not the bundled sample
    lines = launch.setup_check_lines()
    assert len(lines) == 1 and lines[0][0] == "ok" and lines[0][1].startswith("Setup checks: all ")
    real = doctor.run_checks

    def with_problems(*args, **kwargs):
        return [*real(*args, **kwargs), doctor.Check("tax PST", doctor.WARN, "no GL account mapped yet"),
                doctor.Check("GL accounts", doctor.FAIL, "none imported"),
                doctor.Check("local model", doctor.WARN, "said by the chat model's own line"),
                doctor.Check("OCR", doctor.WARN, "said by the OCR line"),
                doctor.Check("package numpy", doctor.FAIL, "said by the packages line")]  # fmt: skip

    monkeypatch.setattr(doctor, "run_checks", with_problems)
    assert launch.setup_check_lines() == [("warn", "tax PST: no GL account mapped yet"),
                                          ("fail", "GL accounts: none imported")]  # fmt: skip
    monkeypatch.setattr(doctor, "run_checks", lambda *a, **k: 1 / 0)
    assert launch.setup_check_lines()[0][1].startswith("Setup checks: not run (ZeroDivisionError")


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

    from ap_coder.ops import offline

    empty = tmp_path / "rapidocr_models"
    empty.mkdir()
    monkeypatch.setattr(offline, "ppocrv5_installed", lambda: True)
    # The table finder's model missing too, with its engine installed (CI's test jobs have no onnxruntime).
    monkeypatch.setattr(offline, "onnxruntime_installed", lambda: True)
    monkeypatch.setattr(offline, "table_finder_path", lambda: None)
    monkeypatch.setattr(offline, "table_finder_dirs", lambda: [tmp_path / "code_models", tmp_path / "data_models"])
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
    monkeypatch.setattr(launch, "plain_download", lambda: False)  # an offline bundle without its models folder
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
    assert len(downloads) == 4 and "Downloading the OCR models" in capsys.readouterr().out  # (+ the table finder)


def test_the_code_alone_downloads_the_models_once_without_the_opt_in(launch, no_models, monkeypatch, capsys):
    fetch_models, downloads = no_models
    monkeypatch.delenv("AP_ALLOW_INTERNET", raising=False)
    monkeypatch.setattr(launch, "plain_download", lambda: True)  # GitHub's ZIP
    state: dict = {}
    launch.ensure_models(first=False, state=state)
    launch.ensure_models(first=False, state=state)  # the failed download is not retried on every start
    out = capsys.readouterr().out
    assert len(downloads) == 4 and "Downloading the OCR models" in out and "Tried again tomorrow" in out
    assert any("yolov8n_layout" in url for url in downloads)  # the table finder's model too
    assert "AP_ALLOW_INTERNET" not in launch.os.environ  # set for the download only
    state["models_failed"] = state["models_failed"].rsplit("|", 1)[0] + "|2000-01-01"  # failed on an earlier day
    launch.ensure_models(first=False, state=state)
    assert len(downloads) == 8  # tried again


def test_the_table_finder_is_downloaded_into_the_data_folder(no_models, tmp_path, monkeypatch, capsys):
    """Never into the code's models/ folder: that would make GitHub's ZIP look like an offline bundle."""
    fetch_models, downloads = no_models
    from ap_coder.ops import offline

    monkeypatch.setattr(offline, "table_finder_path", lambda: None)
    monkeypatch.setattr(
        offline, "table_finder_dirs", lambda: [tmp_path / "code" / "models", tmp_path / "data" / "models"]
    )
    got: list = []
    monkeypatch.setattr(fetch_models, "fetch", lambda folder, source, online, models: got.append(folder) or
                        SimpleNamespace(ok=True))  # fmt: skip
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    assert fetch_models.table_finder(SimpleNamespace(table_finder=True, offline=False, check=False))
    assert got == [tmp_path / "data" / "models"] and "downloaded into" in capsys.readouterr().out


def test_shortcut_is_made_once(launch, tmp_path, monkeypatch):
    from ap_coder.core import paths

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
    (code / "scripts").mkdir()
    (code / "scripts" / "launch.py").write_text("")
    out = subprocess.run(["/bin/bash", str(code / "APProcessor.command")], capture_output=True, text=True,
                         env={"PATH": str(bindir), "AP_NO_PAUSE": "1"}, timeout=60)  # fmt: skip
    assert out.returncode == 1
    assert "needs Python 3.11 or newer (3.12 is best), and it is not installed" in out.stdout
    assert "What to do:" in out.stdout
