"""The launcher's log (as CloseDesk keeps one for each run): every start writes what it said, pip's lines and the
dashboard's output to ``~/.ap_coder/logs/launch.log``, so IT can read what happened after the window was closed. An
unexpected error is said in one plain line that names the log, which holds the details (not a raw traceback)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from ap_coder.core import paths

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def launch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ap_script_launch_log", ROOT / "scripts" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    venv = tmp_path / "code" / ".venv"
    venv.mkdir(parents=True)
    monkeypatch.setattr(module, "ROOT", tmp_path / "code")
    monkeypatch.setattr(module, "VENV", venv)
    monkeypatch.setattr(module, "VENV_PY", venv / "bin" / "python")
    monkeypatch.setattr(module, "STATE", venv / "ap_coder_launcher.json")
    monkeypatch.setattr(module, "LOCK", tmp_path / "code" / ".ap_coder_launch.lock")
    monkeypatch.setattr(module, "WINDOWS", False)
    yield module
    module.close_log()


def log_text() -> str:
    return (paths.user_settings_path().parent / "logs" / "launch.log").read_text(encoding="utf-8")


def test_what_the_launcher_says_is_logged_beside_the_install_record(launch, capsys):
    assert launch.log_path() == paths.user_settings_path().parent / "logs" / "launch.log"
    launch.say("Python 3.12.10: reusing .venv")
    launch.line("ok", "Data folder: C:\\Users\\José\\APCoder")
    assert "reusing .venv" in capsys.readouterr().out
    text = log_text()
    assert "=== " in text.splitlines()[0] and "launch.py" in text.splitlines()[0]  # when, which Python, which options
    assert "Python 3.12.10: reusing .venv" in text and "Data folder: C:\\Users\\José\\APCoder" in text


def test_a_long_log_is_kept_once_as_the_previous_one(launch):
    log = launch.log_path()
    log.parent.mkdir(parents=True)
    log.write_text("x" * (launch.LOG_LIMIT + 1), encoding="utf-8")
    launch.say("a new start")
    assert "a new start" in log_text() and "xxx" not in log_text()
    assert (log.parent / "launch.previous.log").stat().st_size == launch.LOG_LIMIT + 1


def test_the_log_never_stops_a_start(launch, monkeypatch, tmp_path, capsys):
    blocked = tmp_path / "a file, not a folder"
    blocked.write_text("")
    monkeypatch.setenv("AP_USER_SETTINGS", str(blocked / "settings.json"))
    launch.say("still said")
    assert "still said" in capsys.readouterr().out


def test_a_failure_names_the_log(launch, capsys):
    assert launch.failed(launch.SetupError("Installing the packages failed.", "Do this.")) == 1
    out = capsys.readouterr().out
    assert "What to do: Do this." in out and str(launch.log_path()) in out


def test_an_unexpected_error_is_said_plainly_and_its_details_are_in_the_log(launch, monkeypatch, capsys):
    monkeypatch.setattr(launch, "in_venv", lambda: True)

    def broken(args, rest):
        raise KeyError("fingerprint")

    monkeypatch.setattr(launch, "run", broken)
    assert launch.main(["--venv=reused"]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
    assert "AP Coder stopped on an unexpected error (KeyError: 'fingerprint')" in captured.out
    assert str(launch.log_path()) in captured.out
    text = log_text()
    assert "Traceback (most recent call last)" in text and "raise KeyError" in text


def test_the_dashboards_output_is_logged_too(launch, monkeypatch):
    script = "print('AP Coder is running at http://127.0.0.1:8597', flush=True); print('a warning', flush=True)"
    monkeypatch.setattr(launch, "server_command", lambda port, rest: [sys.executable, "-c", script])
    monkeypatch.setattr(launch.running, "health", lambda port, timeout=1.0: None)
    monkeypatch.setattr(launch, "open_browser", lambda url: None)
    assert launch.start_dashboard(["--port", "8597"], {}) == 0
    assert "AP Coder is running at" in log_text() and "a warning" in log_text()
