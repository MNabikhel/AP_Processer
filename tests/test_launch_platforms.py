"""What happens on real Windows laptops and Macs that a Linux test run does not see by itself: the launchers started
from the wrong place, file systems, consoles, browsers and ports. The operating system is stood in for (WINDOWS / MAC,
subprocess, sockets) as in test_launch.py; the batch file is checked as text (there is no cmd.exe here)."""

from __future__ import annotations

import errno
import importlib.util
import os
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


posix_only = pytest.mark.skipif(os.name == "nt", reason="stands in for the Mac's and Linux's flock")


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


# --- A folder on a network drive or NAS --------------------------------------------------------------------------


def _flock_fails(monkeypatch, code: int) -> None:
    import fcntl

    def flock(fd, operation):
        if operation & fcntl.LOCK_UN:
            return None
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(fcntl, "flock", flock)


@posix_only
@pytest.mark.parametrize("code", [errno.ENOLCK, errno.EOPNOTSUPP, errno.EINVAL])
def test_a_folder_without_file_locks_is_not_another_start(launch, monkeypatch, code):
    """AP Coder on a NAS or network volume that has no file locks (NFS without its lock service, some SMB servers):
    the lock can't be taken at all. That is not "another window is setting up": waiting for one would last half an
    hour. It carries on, as in a read-only folder."""
    _flock_fails(monkeypatch, code)
    assert launch.take_lock() is True
    launch.LOCK.touch()
    assert launch.upgrade.lock_held(launch.ROOT) is False  # an older install there is not "running" forever


@posix_only
def test_a_lock_another_start_holds_still_counts(launch, monkeypatch):
    _flock_fails(monkeypatch, errno.EWOULDBLOCK)
    assert launch.take_lock() is False
    launch.LOCK.touch()
    assert launch.upgrade.lock_held(launch.ROOT) is True


# --- The browser and the port ----------------------------------------------------------------------------------


def test_the_browser_opens_the_address_the_dashboard_listens_on(launch, monkeypatch):
    """The dashboard listens on 127.0.0.1 only (no firewall prompt). "localhost" is tried as ::1 first by Mac and
    Windows browsers, where another program may answer (a dev server on [::1]:8501): the browser opens 127.0.0.1."""
    monkeypatch.delenv("AP_DASHBOARD_ADDRESS", raising=False)
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("open", 8501))
    opened: list[str] = []
    monkeypatch.setattr(launch, "open_browser", opened.append)
    assert launch.run(launch.parse([])[0], []) == 0
    assert opened == ["http://127.0.0.1:8501"]
    assert launch.dashboard_url(8502) == "http://127.0.0.1:8502"
    monkeypatch.setenv("AP_DASHBOARD_ADDRESS", "0.0.0.0")  # every network: this computer's own address still works
    assert launch.dashboard_url(8502) == "http://127.0.0.1:8502"
    monkeypatch.setenv("AP_DASHBOARD_ADDRESS", "192.168.1.20")  # one address only: that one
    assert launch.dashboard_url(8502) == "http://192.168.1.20:8502"


@pytest.mark.parametrize("result", [False, OSError("no browser")])
def test_no_browser_to_open_says_where_to_go(launch, monkeypatch, capsys, result):
    """Linux without xdg-open, a locked-down laptop: the address is said, nothing stops."""
    import webbrowser

    monkeypatch.delenv("BROWSER", raising=False)

    def fake_open(url):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(webbrowser, "open", fake_open)
    launch.open_browser("http://127.0.0.1:8501")
    assert "open http://127.0.0.1:8501 in your browser" in capsys.readouterr().out


def test_a_port_another_program_answers_on_is_passed_over(launch, monkeypatch):
    """On a Mac a port another program listens on for every address can still be bound for 127.0.0.1 alone
    (SO_REUSEADDR): a port something already answers on is not taken."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        start = s.getsockname()[1]
    monkeypatch.setattr(launch.running, "listening", lambda port: port == start)
    assert launch.free_port(start) != start


def test_another_port_is_said_when_8501_is_taken(launch, monkeypatch, capsys):
    monkeypatch.setattr(launch, "free_port", lambda start=8501: 8502)
    monkeypatch.setattr(launch, "server_command", lambda port, rest: [sys.executable, "-c", "pass"])
    monkeypatch.setattr(launch.running, "health", lambda port, timeout=1.0: None)
    monkeypatch.setattr(launch, "open_browser", lambda url: None)
    assert launch.start_dashboard([], {}) == 0
    out = capsys.readouterr().out
    assert "Port 8501 is in use by another program: AP Coder uses port 8502 (http://127.0.0.1:8502)" in out


# --- Accents in what AP Coder's own processes print ------------------------------------------------------------


def test_the_self_check_and_the_server_print_accents_on_windows(launch, tmp_path, monkeypatch, capsys):
    """On Windows a Python whose output goes to a pipe writes in the ANSI code page (cp1252): "Łukasz" (a user name,
    a vendor) can't be written there and the self-check or the server stopped with UnicodeEncodeError. Their output
    is UTF-8 now, read as UTF-8. (PYTHONIOENCODING=cp1252 stands in for Windows' code page here.)"""
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "pilot_check.py").write_text("print('FAIL Łukasz – Société Générale')\nraise SystemExit(1)\n",
                                            encoding="utf-8")  # fmt: skip
    monkeypatch.setattr(launch, "SCRIPTS", scripts)
    ok, result = launch.self_check()
    assert not ok and result == "Self-check FAILED: FAIL Łukasz – Société Générale"
    script = "print('Data folder: C:/Users/Łukasz/APCoder', flush=True)"
    monkeypatch.setattr(launch, "server_command", lambda port, rest: [sys.executable, "-c", script])
    monkeypatch.setattr(launch.running, "health", lambda port, timeout=1.0: None)
    monkeypatch.setattr(launch, "open_browser", lambda url: None)
    assert launch.start_dashboard(["--port", "8597"], {}) == 0
    assert "Data folder: C:/Users/Łukasz/APCoder" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="makes a symbolic link to this Python")
def test_a_python_under_a_user_name_with_accents_is_found(launch, tmp_path, monkeypatch):
    """C:\\Users\\Łukasz\\AppData\\Local\\Programs\\Python\\Python312\\python.exe: asked where it is, that Python
    printed its path in the ANSI code page and stopped, so the Python the bundle has packages for was "not there"."""
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    home = tmp_path / "Users" / "Łukasz"
    home.mkdir(parents=True)
    (home / "python3").symlink_to(sys.executable)
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    assert "Łukasz" in launch.python_found([str(home / "python3")], version)
