"""What happens on real Windows laptops and Macs that a Linux test run does not see by itself: the launchers started
from the wrong place, file systems, consoles, browsers and ports. The operating system is stood in for (WINDOWS / MAC,
subprocess, sockets) as in test_launch.py; the batch file is checked as text (there is no cmd.exe here)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def launch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ap_script_launch_platforms", ROOT / "scripts" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    venv = tmp_path / ".venv"
    venv.mkdir()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "VENV", venv)
    monkeypatch.setattr(module, "VENV_PY", venv / "bin" / "python")
    monkeypatch.setattr(module, "STATE", venv / "ap_coder_launcher.json")
    monkeypatch.setattr(module, "LOCK", tmp_path / ".ap_coder_launch.lock")
    monkeypatch.setattr(module, "WINDOWS", False)
    monkeypatch.setattr(module, "MAC", False)
    monkeypatch.delenv(module.LOCK_HELD_ENV, raising=False)  # (main() sets it: gone again after the test)
    yield module
    module.release_lock()


def bat() -> str:
    return (ROOT / "APProcessor.bat").read_bytes().decode("ascii").replace("\r\n", "\n")


# --- Started from the wrong place --------------------------------------------------------------------------------


def test_the_windows_launcher_outside_its_folder_says_so():
    """Opened inside the ZIP (Explorer unpacks only that file to a temporary folder) or copied to the desktop: no
    Python error about scripts\\launch.py, one message on what to do."""
    text = bat()
    check = 'if not exist "scripts\\launch.py" goto :notunzipped'
    assert check in text
    assert text.index('pushd "%~dp0"') < text.index(check) < text.index("call :venvpython")
    message = text[text.index(":notunzipped\n") :]
    assert "Extract All" in message and "goto :fail" in message
    # A folder name with "&" or ")" is printed inside quotes: unquoted, cmd would run the rest as a command.
    assert 'echo AP Coder could not open its own folder: "%~dp0"' in text
    assert '"%CD%"' in message


def test_started_inside_explorers_view_of_the_zip_stops_with_what_to_do(launch, tmp_path, monkeypatch, capsys):
    """Explorer unpacked the whole ZIP to %TEMP%\\Temp1_<name>.zip: nothing is set up there (Windows deletes it)."""
    monkeypatch.setattr(launch, "WINDOWS", True)
    monkeypatch.setattr(launch, "ROOT", tmp_path / "Temp1_APProcessor-offline-0.2.0-20261011.zip" / "APProcessor")
    monkeypatch.setattr(launch, "bootstrap", lambda argv: pytest.fail("set up inside the ZIP's temporary folder"))
    assert launch.main(["--no-start"]) == 1
    out = capsys.readouterr().out
    assert "started from inside its ZIP" in out and "Extract All" in out
    # An ordinary folder, or the same name on a Mac: carries on.
    monkeypatch.setattr(launch, "ROOT", tmp_path / "APCoder" / "app")
    monkeypatch.setattr(launch, "bootstrap", lambda argv: 0)
    monkeypatch.setattr(launch.upgrade, "newer_install", lambda root: None)
    assert launch.main(["--no-start"]) == 0
