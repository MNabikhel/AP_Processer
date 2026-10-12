"""Which Python the launchers use: any CPython 3.11 or newer (no upper bound), the offline bundle's versions
first, and a plain message when the only Python there is too old. APProcessor.command is run under bash with
stand-in Pythons on a made-up PATH; scripts/launch.py's checks and messages are tested directly."""

from __future__ import annotations

import glob
import importlib.util
import json
import re
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
        (self.code / "scripts").mkdir()
        (self.code / "scripts" / "launch.py").write_text("# the stand-in Pythons never read it\n")

    def script(self, name: str, body: str, folder: Path | None = None) -> Path:
        path = (folder or self.bin) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
        return path

    def python(self, name: str, version: str, folder: Path | None = None) -> Path:
        """A stand-in Python: ``-c`` prints its version (what the launcher asks), ``launch.py --venv-ok`` (the quick
        check of .venv) says yes, anything else is a run."""
        body = (f'if [ "$1" = -c ] || [ "$2" = --venv-ok ]; then echo {version}; exit 0; fi\n'
                f'echo "{name} $*" >> "{self.ran}"\necho "RAN {name}"')  # fmt: skip
        return self.script(name, body, folder)

    def run(self, *extra_path: Path, shell: tuple = ("/bin/bash",), **env: str) -> subprocess.CompletedProcess:
        path = ":".join(str(p) for p in (self.bin, *extra_path))
        return subprocess.run([*shell, str(self.code / "APProcessor.command"), "--no-start"],
                              capture_output=True, text=True, env={"PATH": path, "AP_NO_PAUSE": "1", **env},
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


@needs_bash
@pytest.mark.skipif(not Path("/bin/sh").exists() or Path("/bin/sh").resolve().name == "bash", reason="needs a sh")
def test_started_with_sh_it_runs_under_bash(mac):
    """``sh APProcessor.command`` (or zsh, the Mac's default shell): it needs bash, which every Mac has, and starts
    itself again with it instead of stopping on a syntax error."""
    mac.python("python3", "3.14")
    out = mac.run(shell=("/bin/sh",))
    assert out.returncode == 0, out.stdout + out.stderr
    assert mac.used() == ["python3 scripts/launch.py --no-start"]
    assert "Syntax error" not in out.stderr and "not found" not in out.stderr
    # The Mac's sh is bash 3.2 in POSIX mode (no "< <(...)" there): the same; never in a loop, even when the
    # environment asks every bash for POSIX mode.
    mac.ran.unlink()
    out = mac.run(shell=("/bin/bash", "--posix"), POSIXLY_CORRECT="1")
    assert out.returncode == 0, out.stdout + out.stderr
    assert mac.used() == ["python3 scripts/launch.py --no-start"]


@needs_bash
def test_a_folder_with_spaces_accents_and_symbols(mac, tmp_path):
    """Unzipped to "~/Téléchargements/AP Coder (copy) & co $HOME! 100%": the launcher still runs from it."""
    odd = tmp_path / "Téléchargements" / "AP Coder (copy) & co $HOME! 100%"
    odd.parent.mkdir()
    mac.code.rename(odd)
    mac.code = odd
    mac.python("python3", "3.14")
    out = mac.run()
    assert out.returncode == 0, out.stdout + out.stderr
    assert mac.used() == ["python3 scripts/launch.py --no-start"]


@needs_bash
def test_the_launcher_copied_out_of_its_folder_says_so(mac):
    """APProcessor.command copied on its own to the desktop (or a folder Terminal may not read): no Python error,
    one plain message, and nothing is run."""
    (mac.code / "scripts" / "launch.py").unlink()
    mac.python("python3", "3.14")
    out = mac.run()
    assert out.returncode == 1 and mac.used() == []
    assert "AP Coder can't find its files next to this launcher" in out.stdout
    assert "What to do: open APProcessor.command inside the unzipped AP Coder folder" in out.stdout


BASH4 = {
    "associative arrays": r"declare\s+-[a-zA-Z]*A",
    "mapfile/readarray": r"\b(mapfile|readarray)\b",
    "case conversion": r"\$\{\w+(,,|\^\^|,|\^)\}",
    "|& and &>>": r"\|&|&>>",
    "case fall-through": r";;&|;&",
    "coproc": r"\bcoproc\b",
    "globstar": r"globstar",
    "[[ -v ]]": r"\[\[\s+-v\b",
    "nameref": r"\b(local|declare)\s+-n\b",
    "wait -n": r"\bwait\s+-n\b",
    "negative substring": r"\$\{\w+:\s*-\d",
    "$EPOCHSECONDS": r"EPOCH(SECONDS|REALTIME)",
}


@pytest.mark.parametrize("name", ["APProcessor.command"])
def test_the_mac_launchers_need_only_bash_3_2(name):
    """macOS's own /bin/bash is 3.2 (2007): nothing from bash 4 or newer."""
    code = "\n".join(line.split(" #")[0] for line in (ROOT / name).read_text().splitlines()
                     if not line.lstrip().startswith("#"))  # fmt: skip
    found = [what for what, pattern in BASH4.items() if re.search(pattern, code)]
    assert not found, f"{name} uses bash 4 syntax: {found}"
    if sys.platform != "win32":
        assert subprocess.run(["bash", "-n", str(ROOT / name)]).returncode == 0


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


class MacPlaces(MacLauncher):
    """A Mac double-clicked from Finder: launchd's short PATH (only ``bin/`` and the folder of Apple's stand-in), and
    Homebrew's and python.org's fixed folders moved under ``tmp`` (the launcher's copy names them there)."""

    PLACES = ("/opt/homebrew/bin", "/usr/local/bin", "/Library/Frameworks/Python.framework")

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path, darwin=True)
        script = self.code / "APProcessor.command"
        text = script.read_text()
        for place in self.PLACES:
            assert place in text
            text = text.replace(place, str(tmp_path / place.lstrip("/")))
        script.write_text(text)

    def at(self, place: str, name: str, version: str) -> Path:
        """A stand-in Python in a fixed place ("/opt/homebrew/bin", ...): a run writes its full path."""
        folder = self.tmp / place.lstrip("/")
        body = (f'if [ "$1" = -c ] || [ "$2" = --venv-ok ]; then echo {version}; exit 0; fi\n'
                f'echo "$0 $*" >> "{self.ran}"')  # fmt: skip
        return self.script(name, body, folder)

    def finder(self) -> subprocess.CompletedProcess:
        return self.run(self.apple.parent)


PYTHON_ORG_314 = "/Library/Frameworks/Python.framework/Versions/3.14/bin"


@pytest.fixture
def finder(tmp_path):
    return MacPlaces(tmp_path)


@needs_bash
def test_the_pilot_testers_mac_python_org_314_from_a_finder_double_click(finder):
    """The pilot tester's Mac: only python.org's Python 3.14.5, no developer tools (Apple's /usr/bin/python3 only opens
    an install dialog), and Finder's short PATH, without /usr/local/bin: python.org's own folder is used."""
    finder.python("python3", "3.9", finder.apple.parent)  # would open a dialog: never run
    finder.script("xcode-select", "echo 'xcode-select: error: unable to get active developer directory' >&2; exit 2")
    python = finder.at(PYTHON_ORG_314, "python3.14", "3.14")
    finder.at(PYTHON_ORG_314, "python3", "3.14")
    out = finder.finder()
    assert out.returncode == 0, out.stdout
    assert finder.used() == [f"{python} scripts/launch.py --no-start"]
    # python.org's links in /usr/local/bin (its installer's default) are the same Python: used, not run twice.
    link = finder.tmp / "usr/local/bin/python3.14"
    link.parent.mkdir(parents=True)
    link.symlink_to(python)
    finder.ran.unlink()
    assert finder.finder().returncode == 0 and finder.used() == [f"{link} scripts/launch.py --no-start"]


@needs_bash
def test_homebrews_314_on_apple_silicon_and_the_xcode_python(finder):
    """Homebrew's python3.14 in /opt/homebrew/bin (not on Finder's PATH) is found; Apple's /usr/bin/python3 3.9 from
    the developer tools only makes the message say "too old" when nothing newer is there."""
    tools = finder.tmp / "CommandLineTools"
    finder.python("python3", "3.9", tools / "usr" / "bin")
    finder.script("xcode-select", f"echo {tools}")
    finder.python("python3", "3.9", finder.apple.parent)
    out = finder.finder()
    assert out.returncode == 1 and "Python 3.9 is installed, but AP Coder needs Python 3.11 or newer" in out.stdout
    brew = finder.at("/opt/homebrew/bin", "python3.14", "3.14")
    assert finder.finder().returncode == 0 and finder.used() == [f"{brew} scripts/launch.py --no-start"]
    # Intel Homebrew (/usr/local/bin) and python.org both 3.14: one is used, once.
    (finder.tmp / "opt").rename(finder.tmp / "opt-gone")
    finder.ran.unlink()
    intel = finder.at("/usr/local/bin", "python3.14", "3.14")
    finder.at(PYTHON_ORG_314, "python3.14", "3.14")
    assert finder.finder().returncode == 0 and finder.used() == [f"{intel} scripts/launch.py --no-start"]


def test_launch_py_looks_where_python_org_and_homebrew_put_314(tmp_path, monkeypatch):
    """bootstrap's search for a Python the bundle fits (not only what is on PATH)."""
    spec = importlib.util.spec_from_file_location("ap_script_launch_places", ROOT / "scripts" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "WINDOWS", False)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)  # Finder's PATH: no python3.14 on it
    found = [c[0] for c in module.python_candidates("3.14")]
    assert found == ["/opt/homebrew/bin/python3.14", "/usr/local/bin/python3.14", f"{PYTHON_ORG_314}/python3.14"]
    asked = []
    monkeypatch.setattr(module, "python_found", lambda command, version: asked.append(command[0]) or (
        command[0] if command[0].startswith("/Library") else ""))  # fmt: skip
    assert module.find_python(["3.14"]) == (f"{PYTHON_ORG_314}/python3.14", "3.14")
    assert "/usr/bin/python3" not in asked  # never Apple's stand-in


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


def _manifest(launch, *versions: str, current: str = "", platform: str = "win_amd64", here: bool = True) -> None:
    """A bundle for ``platform`` (and for the Linux computer that built it, ``current``). ``here``: the computer the
    launcher runs on is a ``platform`` one (else it is what launch.wheel_platform() works out)."""
    if here:
        launch.wheel_platform = lambda: platform
    targets = [f"{platform}, Python {v}" for v in versions]
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
    _manifest(launch, "3.11", "3.12", platform="macosx_14_0_arm64", here=False)  # this Mac: macOS 15.4, arm64
    monkeypatch.setattr(launch, "sys", _found((3, 14, 5), "/usr/local/bin/python3", "darwin"))
    monkeypatch.setattr(launch.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(launch.platform, "mac_ver", lambda: ("15.4", ("", "", ""), "arm64"))
    monkeypatch.setattr(launch, "ensure_venv", lambda: "created")
    monkeypatch.setattr(launch, "find_python", lambda versions: None)  # no Python the bundle has packages for here
    calls = []
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert launch.bootstrap([]) == 0 and calls
    out = capsys.readouterr().out
    assert "this offline bundle has packages for Python 3.11 and 3.12, not for Python 3.14" in out
    assert "python scripts/build_offline_bundle.py --python 3.14 --platform macosx_15_0_arm64" in out
    assert "install Python 3.12" in out
    assert "not installed" not in out and "tested with" not in out and "older than" not in out


def test_a_python_the_bundle_has_gets_no_note(launch, monkeypatch, capsys):
    _manifest(launch, "3.11", "3.12", "3.13", "3.14", platform="macosx_14_0_arm64")
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
    hint = launch.install_hint(["--no-index"], ["fastapi>=0.115,<1"])
    assert f"no fastapi for Python {version} on this computer" in hint
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
