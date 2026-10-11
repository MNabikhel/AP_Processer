"""Which Python the launchers use: any CPython 3.11 or newer (no upper bound), the offline bundle's versions
first, and a plain message when the only Python there is too old. APProcessor.command is run under bash with
stand-in Pythons on a made-up PATH; scripts/launch.py's checks and messages are tested directly."""

from __future__ import annotations

import glob
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent

needs_bash = pytest.mark.skipif(sys.platform == "win32", reason="runs the bash launcher")


class MacLauncher:
    """A copy of APProcessor.command in its own folder, run with only ``bin/`` (stand-ins) on PATH."""

    def __init__(self, tmp_path: Path, darwin: bool = False) -> None:
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.code = tmp_path / "code"
        self.code.mkdir()
        self.ran = tmp_path / "ran"  # each stand-in Python run with arguments (not -c) writes its name here
        self.apple = tmp_path / "usr-bin" / "python3"  # stands in for Apple's /usr/bin/python3
        tool = subprocess.run(["bash", "-c", "command -v dirname"], capture_output=True, text=True).stdout.strip()
        (self.bin / "dirname").symlink_to(tool)
        self.script("uname", f"echo {'Darwin' if darwin else 'Linux'}")
        text = (ROOT / "APProcessor.command").read_text()
        assert "APPLE_STUB=/usr/bin/python3\n" in text
        (self.code / "APProcessor.command").write_text(text.replace("APPLE_STUB=/usr/bin/python3\n",
                                                                    f"APPLE_STUB={self.apple}\n"))  # fmt: skip

    def script(self, name: str, body: str, folder: Path | None = None) -> Path:
        path = (folder or self.bin) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
        return path

    def python(self, name: str, version: str, folder: Path | None = None) -> Path:
        """A stand-in Python: ``-c`` prints its version (what the launcher asks), anything else is a run."""
        body = (f'if [ "$1" = -c ]; then echo {version}; exit 0; fi\n'
                f'echo "{name} $*" >> "{self.ran}"\necho "RAN {name}"')  # fmt: skip
        return self.script(name, body, folder)

    def run(self, *extra_path: Path) -> subprocess.CompletedProcess:
        path = ":".join(str(p) for p in (self.bin, *extra_path))
        return subprocess.run(["/bin/bash", str(self.code / "APProcessor.command"), "--no-start"],
                              capture_output=True, text=True, env={"PATH": path, "AP_NO_PAUSE": "1"},
                              timeout=60)  # fmt: skip

    def used(self) -> list[str]:
        return self.ran.read_text().split("\n")[:-1] if self.ran.exists() else []


@pytest.fixture
def mac(tmp_path):
    return MacLauncher(tmp_path)


@needs_bash
def test_only_python3_314_is_found_and_used(mac):
    """The tester's Mac: Python 3.14 only, as python3. It is used, not called "not installed"."""
    mac.python("python3", "3.14")
    out = mac.run()
    assert out.returncode == 0, out.stdout
    assert mac.used() == ["python3 scripts/launch.py --no-start"]
    assert "not installed" not in out.stdout


@needs_bash
def test_only_python_314_is_used(mac):
    mac.python("python", "3.14")
    out = mac.run()
    assert out.returncode == 0, out.stdout
    assert mac.used() == ["python scripts/launch.py --no-start"]


@needs_bash
def test_a_newer_python_than_the_bundle_knows_is_used(mac):
    mac.python("python3.16", "3.16")
    assert mac.run().returncode == 0 and mac.used() == ["python3.16 scripts/launch.py --no-start"]


@needs_bash
def test_python3_comes_before_python(mac):
    mac.python("python", "3.12")
    mac.python("python3", "3.12")
    assert mac.run().returncode == 0 and mac.used() == ["python3 scripts/launch.py --no-start"]


@needs_bash
def test_the_bundles_versions_come_first(mac):
    """3.12 over 3.14 when both are there, whatever their names: the offline bundle has packages for 3.12."""
    mac.python("python3.14", "3.14")
    mac.python("python3", "3.14")
    mac.python("python3.12", "3.12")
    assert mac.run().returncode == 0 and mac.used() == ["python3.12 scripts/launch.py --no-start"]
    mac.ran.unlink()
    (mac.bin / "python3.12").unlink()
    mac.python("python3", "3.12")  # python3 is 3.12 here: still before python3.14
    assert mac.run().returncode == 0 and mac.used() == ["python3 scripts/launch.py --no-start"]
    mac.ran.unlink()
    (mac.bin / "python3").unlink()
    mac.python("python3.13", "3.13")
    mac.python("python3.11", "3.11")
    assert mac.run().returncode == 0 and mac.used() == ["python3.11 scripts/launch.py --no-start"]


@needs_bash
def test_a_too_old_python_is_said_to_be_too_old_not_missing(mac):
    mac.python("python3", "3.10")
    mac.python("python", "2.7")
    out = mac.run()
    assert out.returncode == 1 and mac.used() == []
    assert "Python 3.10 is installed, but AP Coder needs Python 3.11 or newer" in out.stdout
    assert "not installed" not in out.stdout and "What to do:" in out.stdout


@needs_bash
def test_a_python_that_does_not_start_is_passed_over(mac):
    mac.script("python3.12", "exit 1")  # broken
    mac.python("python3", "3.13")
    assert mac.run().returncode == 0 and mac.used() == ["python3 scripts/launch.py --no-start"]


@needs_bash
def test_a_working_venv_is_used_whatever_python_made_it(mac):
    """.venv made with an older Python keeps working; a broken one is passed over (launch.py remakes it)."""
    venv_bin = mac.code / ".venv" / "bin"
    mac.python("python", "3.11", venv_bin)
    mac.python("python3", "3.14")
    assert mac.run().returncode == 0 and mac.used() == ["python scripts/launch.py --no-start"]
    mac.ran.unlink()
    mac.script("python", "exit 1", venv_bin)  # its Python is gone or broken
    assert mac.run().returncode == 0 and mac.used() == ["python3 scripts/launch.py --no-start"]


MAC_PLACES = ("/opt/homebrew/bin/python3*", "/usr/local/bin/python3*", "/Library/Frameworks/Python.framework")


@pytest.fixture
def darwin(tmp_path):
    if any(glob.glob(p) for p in MAC_PLACES):
        pytest.skip("this computer has a Python where the launcher looks on a Mac")
    return MacLauncher(tmp_path, darwin=True)


@needs_bash
def test_apples_stand_in_is_never_run_without_the_developer_tools(darwin):
    """Without Apple's developer tools /usr/bin/python3 only opens an install dialog: it is not even run."""
    darwin.python("python3", "3.9", darwin.apple.parent)
    darwin.script("xcode-select", "echo 'xcode-select: error: unable to get active developer directory' >&2; exit 2")
    out = darwin.run(darwin.apple.parent)
    assert out.returncode == 1 and darwin.used() == []
    assert "needs Python 3.11 or newer (3.12 is best), and it is not installed" in out.stdout
    # With a real Python elsewhere, that one is used.
    darwin.python("python3.14", "3.14")
    assert darwin.run(darwin.apple.parent).returncode == 0
    assert darwin.used() == ["python3.14 scripts/launch.py --no-start"]


@needs_bash
def test_apples_python_counts_when_the_developer_tools_are_there(darwin):
    tools = darwin.tmp / "CommandLineTools"
    darwin.python("python3", "3.9", tools / "usr" / "bin")
    darwin.script("xcode-select", f"echo {tools}")
    darwin.python("python3", "3.9", darwin.apple.parent)
    out = darwin.run(darwin.apple.parent)
    assert out.returncode == 1 and "Python 3.9 is installed, but AP Coder needs Python 3.11 or newer" in out.stdout
    darwin.python("python3", "3.12", darwin.apple.parent)  # a newer one from Apple's tools: used
    assert darwin.run(darwin.apple.parent).returncode == 0
    assert darwin.used() == ["python3 scripts/launch.py --no-start"]


# --- scripts/launch.py ------------------------------------------------------------------------------------------


@pytest.fixture
def launch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ap_script_launch_python", ROOT / "scripts" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "VENV", tmp_path / ".venv")
    monkeypatch.setattr(module, "VENV_PY", tmp_path / ".venv" / "bin" / "python")
    monkeypatch.setattr(module, "WINDOWS", False)
    return module


def _manifest(launch, *versions: str, current: str = "") -> None:
    targets = [f"win_amd64, Python {v}" for v in versions]
    if current:
        targets.insert(0, f"this computer (linux-x86_64, Python {current})")
    (launch.ROOT / "bundle_manifest.json").write_text(json.dumps({"targets": targets}), encoding="utf-8")


def _found(version: tuple[int, int, int], executable: str, platform: str) -> SimpleNamespace:
    """What launch.py sees of the Python it runs with (the one the double-click found)."""
    shown = ".".join(map(str, version))
    return SimpleNamespace(version_info=version, version=f"{shown} (main)", executable=executable, platform=platform)


def test_any_python_311_or_newer_is_supported(launch):
    assert launch.supported((3, 11)) and launch.supported((3, 12, 4)) and launch.supported((3, 13))
    assert launch.supported((3, 14)) and launch.supported((3, 14, 5)) and launch.supported((3, 20))
    assert not launch.supported((3, 10)) and not launch.supported((2, 7))


def test_bundle_pythons_are_read_from_the_manifest_in_version_order(launch):
    assert launch.bundle_pythons() == []  # no bundle here
    _manifest(launch, "3.14", "3.11", "3.13", "3.12", current="3.12")
    assert launch.bundle_pythons() == ["3.11", "3.12", "3.13", "3.14"]
    _manifest(launch, "3.9", "3.10")
    assert launch.bundle_pythons() == ["3.9", "3.10"]  # 3.10 after 3.9, not before


def test_a_python_the_bundle_was_not_built_for_is_said_plainly(launch, monkeypatch, capsys):
    """Found: Python 3.14; the bundle has 3.11 and 3.12. It says which, and what IT can do; it still goes on."""
    _manifest(launch, "3.11", "3.12")
    monkeypatch.setattr(launch, "sys", _found((3, 14, 5), "/usr/local/bin/python3", "darwin"))
    monkeypatch.setattr(launch.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(launch.platform, "mac_ver", lambda: ("15.4", ("", "", ""), "arm64"))
    monkeypatch.setattr(launch, "ensure_venv", lambda: "created")
    calls = []
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert launch.bootstrap([]) == 0 and calls
    out = capsys.readouterr().out
    assert "this offline bundle has packages for Python 3.11 and 3.12, not for Python 3.14" in out
    assert "python scripts/build_offline_bundle.py --python 3.14 --platform macosx_15_0_arm64" in out
    assert "install Python 3.12" in out
    assert "not installed" not in out and "tested with" not in out and "older than" not in out


def test_a_python_the_bundle_has_gets_no_note(launch, monkeypatch, capsys):
    _manifest(launch, "3.11", "3.12", "3.13", "3.14")
    monkeypatch.setattr(launch, "sys", _found((3, 14, 5), "/usr/local/bin/python3", "darwin"))
    monkeypatch.setattr(launch, "ensure_venv", lambda: "created")
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd: 0)
    assert launch.bootstrap([]) == 0
    assert "Note:" not in capsys.readouterr().out


def test_a_too_old_python_run_directly_is_told_so(launch, monkeypatch, capsys):
    monkeypatch.setattr(launch, "sys", _found((3, 10, 4), "/usr/bin/python3", "linux"))
    monkeypatch.setattr(launch, "ensure_venv", lambda: "created")
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd: 0)
    launch.bootstrap([])
    assert "Python 3.10 is older than AP Coder needs (3.11 or newer" in capsys.readouterr().out


def test_install_hint_names_the_bundles_versions_and_the_build_command(launch, monkeypatch):
    _manifest(launch, "3.11", "3.12", "3.13", current="3.12")
    monkeypatch.setattr(launch, "wheel_platform", lambda: "win_amd64")
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    hint = launch.install_hint(["--no-index"], ["streamlit>=1.62"])
    assert f"no streamlit for Python {version} on this computer" in hint
    assert "built for Python 3.11, 3.12 and 3.13" in hint
    assert f"build_offline_bundle.py --python {version} --platform win_amd64" in hint
    assert "install Python 3.12" in hint and "start AP Coder again" in hint


@pytest.mark.parametrize(
    ("windows", "plat", "machine", "mac_release", "expected"),
    [
        (True, "win32", "AMD64", "", "win_amd64"),
        (True, "win32", "ARM64", "", "win_arm64"),
        (False, "darwin", "arm64", "14.5", "macosx_14_0_arm64"),
        (False, "darwin", "x86_64", "10.15.7", "macosx_10_15_x86_64"),
        (False, "linux", "x86_64", "", "manylinux2014_x86_64"),
    ],
)
def test_wheel_platform_names_this_computer(launch, monkeypatch, windows, plat, machine, mac_release, expected):
    monkeypatch.setattr(launch, "WINDOWS", windows)
    monkeypatch.setattr(launch, "sys", SimpleNamespace(platform=plat))
    monkeypatch.setattr(launch.platform, "machine", lambda: machine)
    monkeypatch.setattr(launch.platform, "mac_ver", lambda: (mac_release, ("", "", ""), machine))
    assert launch.wheel_platform() == expected
