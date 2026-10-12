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


# --- Windows' 260-character paths --------------------------------------------------------------------------------


def test_a_path_too_long_for_windows_is_said_plainly(launch, monkeypatch):
    """Unzipped deep (C:\\Users\\<long name>\\Downloads\\APProcessor-offline-...\\APProcessor\\), a package file's path
    passes 260 characters and pip stops: the start said the wheelhouse "has no pandas for Python 3.12"."""
    wheelhouse = launch.ROOT / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "openpyxl-3.1.5-py2.py3-none-any.whl").write_bytes(b"")
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["pandas>=2.1"], ""))
    lines = [
        "Processing c:\\users\\someone\\downloads\\approcessor\\wheelhouse\\pandas-3.0.6-cp312-cp312-win_amd64.whl\n",
        "ERROR: Could not install packages due to an OSError: [Errno 2] No such file or directory: 'C:\\\\Users"
        "\\\\someone\\\\...\\\\pandas\\\\tests\\\\io\\\\data\\\\a_very_long_name.xlsx'\n",
        "HINT: This error might have occurred since this system does not have Windows Long Path support enabled. "
        "You can find information on how to enable this at https://pip.pypa.io/warnings/enable-long-paths\n",
    ]

    class Pip:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(lines)

        def wait(self):
            return 1

    monkeypatch.setattr(launch.subprocess, "Popen", Pip)
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert "too long for Windows" in str(exc.value) and str(launch.ROOT) in str(exc.value)
    assert "C:\\APCoder" in exc.value.hint and "has no" not in exc.value.hint


@pytest.mark.parametrize(
    "error",
    [
        "[WinError 5] Access is denied: 'C:\\\\APCoder\\\\app\\\\.venv\\\\Lib\\\\site-packages\\\\numpy\\\\x.pyd'",
        "[WinError 32] The process cannot access the file because it is being used by another process: 'x.pyd'",
    ],
)
def test_a_package_file_in_use_is_said_plainly(launch, monkeypatch, error):
    """The antivirus scanning a new .pyd, or an AP Coder window still open, holds a file pip must replace: the start
    said the wheelhouse "has no numpy for Python 3.12"."""
    (launch.ROOT / "wheelhouse").mkdir()
    (launch.ROOT / "wheelhouse" / "openpyxl-3.1.5-py2.py3-none-any.whl").write_bytes(b"")
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["numpy>=1.26"], ""))
    lines = [f"ERROR: Could not install packages due to an OSError: {error}\n",
             "Consider using the `--user` option or check the permissions.\n"]  # fmt: skip

    class Pip:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(lines)

        def wait(self):
            return 1

    monkeypatch.setattr(launch.subprocess, "Popen", Pip)
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert "in use" in str(exc.value) and "has no" not in exc.value.hint and "--user" not in exc.value.hint
    assert "Close every AP Coder window" in exc.value.hint


# --- Linux: Debian and Ubuntu's Python without venv ----------------------------------------------------------------


def test_a_python_without_venv_support_says_which_package(launch, monkeypatch):
    """Debian and Ubuntu ship venv's pip (ensurepip) in a separate package, python3.X-venv: without it .venv can't be
    made, and the start said to reinstall Python from python.org."""
    import shutil

    shutil.rmtree(launch.VENV)
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: launch.subprocess.CompletedProcess(a, 1))
    real = launch.importlib.util.find_spec
    monkeypatch.setattr(launch.importlib.util, "find_spec", lambda name: None if name == "ensurepip" else real(name))
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_venv()
    assert f"sudo apt install python{version}-venv" in exc.value.hint
    assert "python.org" not in exc.value.hint
    monkeypatch.setattr(launch.importlib.util, "find_spec", real)  # venv itself is there: the usual advice
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_venv()
    assert "python.org" in exc.value.hint


# --- Downloaded from the internet: Mark of the Web, Gatekeeper's quarantine ------------------------------------------


def test_the_mark_of_the_web_is_taken_off_the_windows_launchers(launch, monkeypatch):
    """A ZIP from a browser, Teams or Outlook marks every file it unzips (a Zone.Identifier stream): Windows then asks
    "Do you want to run this file?" on every double-click of APProcessor.bat. Once started, AP Coder takes the mark
    off its own launchers, nothing else. (On Linux "name:stream" is an ordinary file name: it stands in for it.)"""
    monkeypatch.setattr(launch, "WINDOWS", True)
    for name in ("APProcessor.bat", "install.bat", "README.md"):
        (launch.ROOT / name).write_text("x")
        (launch.ROOT / f"{name}:Zone.Identifier").write_text("[ZoneTransfer]\nZoneId=3\n")
    launch.unblock_launchers()
    assert not (launch.ROOT / "APProcessor.bat:Zone.Identifier").exists()
    assert not (launch.ROOT / "install.bat:Zone.Identifier").exists()
    assert (launch.ROOT / "APProcessor.bat").exists()
    assert (launch.ROOT / "README.md:Zone.Identifier").exists()  # only the launchers
    launch.unblock_launchers()  # nothing left to take off: quietly nothing


def test_gatekeepers_quarantine_is_taken_off_the_mac_launchers(launch, monkeypatch):
    monkeypatch.setattr(launch, "MAC", True)
    for name in ("APProcessor.command", "install.sh", "README.md"):
        (launch.ROOT / name).write_text("x")
    calls = []
    monkeypatch.setattr(launch.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    launch.unblock_launchers()
    assert calls == [["xattr", "-d", "com.apple.quarantine", str(launch.ROOT / "APProcessor.command"),
                      str(launch.ROOT / "install.sh")]]  # fmt: skip

    def missing(cmd, **k):
        raise FileNotFoundError("xattr")

    monkeypatch.setattr(launch.subprocess, "run", missing)
    launch.unblock_launchers()  # no xattr: never a reason to stop


def test_the_unblocking_runs_on_every_start(launch, monkeypatch):
    calls = []
    monkeypatch.setattr(launch, "unblock_launchers", lambda: calls.append(1))
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("open", 8501))
    monkeypatch.setattr(launch, "open_browser", lambda url: None)
    assert launch.run(launch.parse([])[0], []) == 0 and calls == [1]


# --- A folder synced by OneDrive, iCloud Drive or Dropbox -------------------------------------------------------


def test_a_folder_onedrive_syncs_is_warned_about(launch, tmp_path, monkeypatch):
    """Unzipped in a OneDrive folder (often the Desktop or Documents, redirected), the wheelhouse, the models and the
    database can be "online-only": with no internet they can't be read, and a database being synced is at risk."""
    monkeypatch.setattr(launch, "WINDOWS", True)
    onedrive = tmp_path / "OneDrive - Contoso"
    monkeypatch.setenv("OneDriveCommercial", str(onedrive))
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.setattr(launch, "ROOT", onedrive / "Desktop" / "APProcessor")
    notes = launch.sync_notes(tmp_path / "APCoder")
    assert len(notes) == 1 and notes[0][0] == "warn"
    assert "synced by OneDrive" in notes[0][1] and "C:\\APCoder" in notes[0][1]
    notes = launch.sync_notes(onedrive / "Documents" / "APCoder")  # the data folder too
    assert len(notes) == 2 and "data folder" in notes[1][1]
    monkeypatch.setattr(launch, "ROOT", tmp_path / "APCoder" / "app")
    assert launch.sync_notes(tmp_path / "APCoder") == []


@pytest.mark.parametrize(
    ("folder", "service"),
    [
        ("Library/Mobile Documents/com~apple~CloudDocs/AP Coder", "iCloud Drive"),
        ("Library/CloudStorage/OneDrive-Contoso/AP Coder", "OneDrive"),
        ("Library/CloudStorage/Dropbox/AP Coder", "Dropbox"),
        ("Library/CloudStorage/GoogleDrive-me@example.com/My Drive/AP Coder", "Google Drive"),
    ],
)
def test_a_folder_a_mac_syncs_is_warned_about(launch, tmp_path, monkeypatch, folder, service):
    monkeypatch.setattr(launch, "MAC", True)
    monkeypatch.setattr(launch.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(launch, "ROOT", tmp_path / folder)
    notes = launch.sync_notes(tmp_path / "APCoder")
    assert len(notes) == 1 and f"synced by {service}" in notes[0][1]
    monkeypatch.setattr(launch, "ROOT", tmp_path / "APCoder" / "app")
    assert launch.sync_notes(tmp_path / "APCoder") == []


# --- Apple silicon: an Intel Python through Rosetta ------------------------------------------------------------


MAC_ARM = ("numpy-2.3.0-cp312-cp312-macosx_14_0_arm64.whl", "pandas-2.3.0-cp312-cp312-macosx_14_0_arm64.whl",
           "openpyxl-3.1.5-py2.py3-none-any.whl")  # fmt: skip


def test_an_intel_python_through_rosetta_is_told_to_use_a_native_one(launch, monkeypatch):
    """An Apple silicon Mac whose Python is an Intel build (Intel Homebrew in /usr/local, or Terminal set to open
    using Rosetta): the Apple silicon bundle doesn't fit it, and the message said to build a bundle for Intel Macs."""
    wheelhouse = launch.ROOT / "wheelhouse"
    wheelhouse.mkdir()
    for name in MAC_ARM:
        (wheelhouse / name).write_bytes(b"")
    monkeypatch.setattr(launch, "this_computer", lambda: "mac")
    monkeypatch.setattr(launch.sys, "version_info", (3, 12, 0, "final", 0))
    monkeypatch.setattr(launch, "here_tags", lambda: {"cp312-cp312-macosx_14_0_x86_64", "py3-none-any"})
    monkeypatch.setattr(launch, "wheel_platform", lambda: "macosx_14_0_x86_64")
    monkeypatch.setattr(launch, "under_rosetta", lambda: True)
    message = launch.wheelhouse_misfit()
    assert "Intel" in message and "Rosetta" in message
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["pandas>=2.1"], "Missing: ap_coder"))
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert "python.org" in exc.value.hint and "Open using Rosetta" in exc.value.hint and ".venv" in exc.value.hint


def test_rosetta_is_asked_of_the_mac_without_a_dialog(launch, monkeypatch):
    monkeypatch.setattr(launch.sys, "platform", "darwin")
    monkeypatch.setattr(launch.platform, "machine", lambda: "x86_64")
    seen = []

    def sysctl(cmd, **kwargs):
        seen.append(cmd)
        return launch.subprocess.CompletedProcess(cmd, 0, "1\n", "")

    monkeypatch.setattr(launch.subprocess, "run", sysctl)
    assert launch.under_rosetta() is True and seen == [["sysctl", "-n", "sysctl.proc_translated"]]
    monkeypatch.setattr(launch.platform, "machine", lambda: "arm64")
    assert launch.under_rosetta() is False
