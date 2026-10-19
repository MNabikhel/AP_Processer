"""AP Coder's one-button setup and start: what APProcessor.bat (Windows) and APProcessor.command (Mac) run.

The double-click finds a Python 3.11 or newer (3.12 first, then the other versions the offline bundle has
packages for) and runs this file with it. It then:

1. hands over to the newest AP Coder when this folder holds an older one than the current install (an old
   shortcut or folder started by mistake: the data is never opened by older code; ``scripts/upgrade.py``);
2. reuses ``.venv`` (it creates it only the first time, and again only when it is broken, or made with a Python
   the offline bundle has no packages for while one it has is installed);
3. looks for an AP Coder already running: the same folder and version is only opened in the browser; an older
   version, or one from another folder, is stopped first (``scripts/running.py``): never two on one database;
4. says "Updated AP Coder from X to Y" when this folder's version changed, and removes the files an older
   version unzipped here left behind (the bundle's manifest lists its own);
5. installs only the packages that are missing or too old (``scripts/check_deps.py``), from the offline
   bundle's ``wheelhouse/`` folder (``pip --no-index``: never from the internet), after checking that the
   wheelhouse was built for this kind of computer and Python (one plain message when it was not);
6. settles the data folder outside the code (data an older version kept in its code folder moves there),
   comments out settings that are no longer used, refuses a database made by a newer AP Coder, and copies
   the OCR models from the bundle's ``models/`` folder, only when they are not there yet;
7. records this folder as the current install and points the desktop shortcut at it (Windows: "AP Coder";
   Mac: an "AP Coder.command" launcher), rewritten whenever the current install changes;
8. prints a short readiness summary with the self-check (``scripts/pilot_check.py``) the first time and after
   every update; once it passed, the older installs' ``.venv``, models and folders are cleaned up;
9. starts the server (``python -m ap_coder serve``) and opens it in the browser once it is up.

Offline: this is an enterprise build. AP Coder itself (the dashboard and everything it runs) never connects to
the internet. Its setup gets the packages and OCR models from one of two places:

* the offline bundle (``wheelhouse/`` and ``models/`` next to this folder, ``bundle_manifest.json``): only from
  there, never the internet. A package that is not in the wheelhouse is said plainly to be missing; AP Coder still
  starts when its core packages are there.
* the code alone (GitHub's ZIP or a git checkout: no manifest, no wheelhouse): like CloseDesk, the setup downloads
  them once (packages from PyPI, the OCR models from www.modelscope.cn; APProcessor.bat offers Python 3.12 with
  winget), for its own steps only (:func:`plain_download`). ``AP_ALLOW_INTERNET=0`` forbids that.

``AP_ALLOW_INTERNET=1`` (developers, the bundle builder) also allows PyPI next to a wheelhouse built for another
computer, and ``install.py``'s git update.

It never installs Python itself (APProcessor.bat offers winget's from GitHub's ZIP or with the opt-in) and never
installs LM Studio: it only says whether a model is loaded. Safe to run again at any time, and from two windows at
once: an exclusive lock file (``.ap_coder_launch.lock``) is held while one sets up and runs AP Coder, and a
second double-click waits for its dashboard and opens it in the browser.

Everything a start says (pip's lines and the dashboard's output too) is also written to ``~/.ap_coder/logs/
launch.log`` (kept once as ``launch.previous.log`` past 1 MB), for IT after the window is closed. A step that fails
says what to do; an error no step expected is said in one line, its traceback in that log.

``--installer`` runs the installer's extra steps instead (``scripts/install.py``: data folder and Azure
questions, self-test): ``APProcessor.bat --installer`` / ``./APProcessor.command --installer``. Other options:
``--check`` (run the self-check again), ``--no-start``, ``--yes``. Anything else (e.g. ``--port 8502``) goes to the
dashboard.

Standard library only until the packages are installed: the first run starts with no packages at all.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
for _folder in (ROOT, SCRIPTS):  # ap_coder/ops/offline.py is standard-library only, usable before installing
    if str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))

import check_deps  # noqa: E402  (standard library plus pip's packaging)
import fetch_models  # noqa: E402  (standard library only)
import running  # noqa: E402  (standard library only)
import upgrade  # noqa: E402  (standard library only)

from ap_coder.core import envfile, paths  # noqa: E402  (standard library only)
from ap_coder.ops.offline import INTERNET_ENV, browser_wanted, internet_allowed  # noqa: E402

WINDOWS = os.name == "nt"
MAC = sys.platform == "darwin"
# The installer's steps and questions: the one launcher with --installer (typed in a command prompt or Terminal).
INSTALLER = "APProcessor.bat --installer" if WINDOWS else "./APProcessor.command --installer"
# Where AP Coder's own commands are typed: scripts\terminal.bat (a command prompt with .venv first on PATH), or
# Terminal in AP Coder's folder with .venv's Python.
IN_TERMINAL = "in scripts\\terminal.bat" if WINDOWS else "in Terminal, in AP Coder's folder, with .venv/bin/python"
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")
STATE = VENV / "ap_coder_launcher.json"  # what this .venv has been through (gone with it when it is recreated)
LOCK = ROOT / ".ap_coder_launch.lock"  # held while one start sets up and runs AP Coder (not in .venv: remade)
LOCK_HELD_ENV = "AP_LAUNCH_LOCK_HELD"  # set for the second phase, whose parent holds the lock
WAIT_FOR_OTHER = 1800  # seconds a second double-click waits for the first one's dashboard (a first setup)
MINIMUM = (3, 11)  # the oldest Python AP Coder runs on; any newer one is used (no upper bound)
DEFAULT_PORT = 8501
SHORTCUT = "AP Coder.lnk"  # on the Windows desktop
MAC_LAUNCHER = "AP Coder.command"  # on a Mac's desktop: a two-line script that opens the current install
LAUNCHER_MARK = "# AP Coder: opens the current version"  # the first comment line of that script (ours to rewrite)
READY = "AP Coder is running at"  # what `python -m ap_coder serve` prints once it answers
SELF_CHECK_TIMEOUT = 600
QUIET_PIP_LINES = (
    "Requirement already satisfied",
    "Using cached",
    "Checking if build backend",
    "Getting requirements",
    "Preparing ",
    "Installing build dependencies",
    "Building wheels",
    "Created wheel",
    "Stored in",
    " ",
)
OCR_PACKAGES = {"rapidocr", "onnxruntime", "rapidocr-onnxruntime", "pillow-heif"}  # scans and photos


class SetupError(Exception):
    """A step that stops the start: ``hint`` is the one line on what to do."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


# --- Console ----------------------------------------------------------------------------------------------------


def fancy_marks() -> bool:
    """✓ only where it shows: a UTF-8 console that is not the classic Windows console (its fonts lack it)."""
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "")
    if encoding not in ("utf8", "utf8sig"):
        return False
    if WINDOWS:
        return bool(os.environ.get("WT_SESSION")) or os.environ.get("TERM_PROGRAM") == "vscode"
    return True


MARKS = {
    True: {"ok": "✓", "warn": "!", "info": "·", "fail": "✗"},
    False: {"ok": "[OK]", "warn": "[!!]", "info": "[--]", "fail": "[FAIL]"},
}


def mark(level: str) -> str:
    return MARKS[fancy_marks()][level]


def say(message: str = "") -> None:
    print(message, flush=True)
    log(message)


# --- The log: what each start said, for IT after the window is closed ---------------------------------------------

LOG_NAME = "launch.log"
LOG_LIMIT = 1_000_000  # bytes: a longer log is kept once as launch.previous.log when a start begins
_log: dict[str, Any] = {"path": None, "handle": None}
_STARTS_HERE = not os.environ.get(LOCK_HELD_ENV)  # the first process of a start (not .venv's phase, nor the installer)


def log_path() -> Path:
    """``~/.ap_coder/logs/launch.log``: beside the record of installs, so it is there before the data folder is settled
    and survives an update (the code folder is replaced, the .venv remade)."""
    return paths.user_settings_path().parent / "logs" / LOG_NAME


def _open_log(path: Path) -> Any:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if _STARTS_HERE and path.is_file() and path.stat().st_size > LOG_LIMIT:
            try:  # (never under a phase of this start that writes to it; Windows refuses while another start does)
                os.replace(path, path.with_name("launch.previous.log"))
            except OSError:
                pass
        gap = "\n" if path.is_file() and path.stat().st_size else ""  # a blank line between starts
        handle = open(path, "a", encoding="utf-8", errors="replace")  # open for the whole start
        options = " ".join(getattr(sys, "argv", [])[1:])
        python = f"{str(getattr(sys, 'version', '?')).split()[0]} ({getattr(sys, 'executable', '?')})"
        handle.write(f"{gap}=== {time.strftime('%Y-%m-%d %H:%M:%S')} launch.py {options} | Python {python} | pid "
                     f"{os.getpid()} | {ROOT} ===\n")  # fmt: skip
        return handle
    except OSError:
        return None


def log(text: str) -> bool:
    """Add ``text`` to the log; True when it was written. Never a reason to stop: a log that can't be written is
    skipped (the console still shows everything)."""
    path = log_path()
    if _log["path"] != path:
        close_log()
        _log.update(path=path, handle=_open_log(path))
    handle = _log["handle"]
    if handle is None:
        return False
    try:
        handle.write(text if text.endswith("\n") else text + "\n")
        handle.flush()
    except (OSError, ValueError):
        return False
    return True


def close_log() -> None:
    handle = _log["handle"]
    _log.update(path=None, handle=None)
    if handle is not None:
        try:
            handle.close()
        except OSError:
            pass


def line(level: str, message: str) -> None:
    say(f"  {mark(level)} {message}")


def child_env() -> dict[str, str]:
    """The environment of the Python processes whose output the launcher reads (pip, the self-check, the server): they
    write UTF-8. Without it a Windows Python writing to a pipe uses the ANSI code page and stops with
    UnicodeEncodeError on "Łukasz"; read back with ``encoding="utf-8"``."""
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace", line_buffering=True)


# --- State ------------------------------------------------------------------------------------------------------


def load_state() -> dict:
    """What this .venv has been through ({} when the file is missing or damaged: everything is checked again)."""
    try:
        found = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def save_state(state: dict) -> None:
    try:
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # only a record: the next start checks again


def fingerprint() -> str:
    return identity()["fingerprint"]


def identity() -> dict:
    """This folder's AP Coder: version, build (the offline bundle's build time) and fingerprint."""
    return paths.install_identity(ROOT)


# --- Phase 1: the virtual environment (any Python) ------------------------------------------------------------


def in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


def supported(version: tuple[int, ...]) -> bool:
    """3.11 or newer: APProcessor.bat / .command accept any of them (3.12 first)."""
    return tuple(version[:2]) >= MINIMUM


def venv_works() -> bool:
    """``.venv``'s Python starts and has pip: anything less is a broken environment."""
    if not VENV_PY.exists():
        return False
    try:
        probe = subprocess.run([str(VENV_PY), "-c", "import encodings, pip"], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def ensure_venv(python: str = sys.executable, rebuild: bool = False) -> str:
    """``reused``, ``created`` or ``recreated`` (only when the existing one is broken, or ``rebuild``: made with a
    Python the offline bundle has no packages for)."""
    if venv_works() and not rebuild:
        return "reused"
    status = "recreated" if VENV.exists() else "created"
    if status == "recreated":
        if not rebuild:
            say("The .venv folder is broken (its Python does not start): making a new one.")
        shutil.rmtree(VENV, ignore_errors=True)
        if VENV.exists():
            raise SetupError(
                "Could not remove the .venv folder to make it again.",
                "Close every AP Coder window, delete the .venv folder, then start AP Coder again.",
            )
    result = subprocess.run([python, "-m", "venv", str(VENV)])
    if result.returncode != 0 or not venv_works():
        hint = "Reinstall Python 3.12 from https://www.python.org/downloads/, then start AP Coder again."
        if sys.platform.startswith("linux") and not ensurepip_works(python):
            # Debian, Ubuntu: the distribution's Python makes a .venv only with its separate python3.X-venv package.
            version = f"{sys.version_info[0]}.{sys.version_info[1]}"
            hint = (f"This Linux's Python {version} can't make one without its venv package: ask IT to install it "
                    f"(Debian, Ubuntu: sudo apt install python{version}-venv), then start AP Coder again.")  # fmt: skip
        raise SetupError("Could not create the .venv folder (AP Coder's own Python environment).", hint)
    return status


def ensurepip_works(python: str) -> bool:
    """``python`` has what ``-m venv`` needs to put pip in a .venv (Debian and Ubuntu leave it out of their Python)."""
    try:
        probe = subprocess.run([python, "-m", "ensurepip", "--version"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def venv_version() -> str:
    """The Python version .venv was made with ("3.14"), from its pyvenv.cfg ("" when unknown)."""
    try:
        text = (VENV / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = re.search(r"(?m)^\s*version(?:_info)?\s*=\s*(\d+)\.(\d+)", text)
    return f"{found.group(1)}.{found.group(2)}" if found else ""


def python_candidates(version: str) -> list[list[str]]:
    """Where a Python ``version`` ("3.12") may be on this computer, as commands to run (never Apple's stand-in)."""
    if WINDOWS:
        short = version.replace(".", "")
        local, program = os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")
        return [["py", f"-{version}"], [f"{local}\\Programs\\Python\\Python{short}\\python.exe"],
                [f"{program}\\Python{short}\\python.exe"]]  # fmt: skip
    found = [shutil.which(f"python{version}") or ""]
    found += [f"{d}/python{version}" for d in ("/opt/homebrew/bin", "/usr/local/bin")]
    found.append(f"/Library/Frameworks/Python.framework/Versions/{version}/bin/python{version}")
    return [[c] for c in dict.fromkeys(found) if c]


def python_found(command: list[str], version: str) -> str:
    """The executable of ``command`` when it starts and is Python ``version`` ("" otherwise)."""
    if len(command) == 1 and not (command[0].startswith("py") or Path(command[0]).is_file()):
        return ""
    env = {**child_env(), "PYTHON_MANAGER_AUTOMATIC_INSTALL": "false"}  # py never downloads a missing version
    try:
        ask = "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)"
        out = subprocess.run([*command, "-c", ask], capture_output=True, encoding="utf-8", errors="replace",
                             timeout=30, env=env, stdin=subprocess.DEVNULL)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = out.stdout.split()
    if out.returncode != 0 or len(lines) < 2 or lines[0] != version:
        return ""
    executable = " ".join(lines[1:])
    try:
        inside = Path(executable).resolve().is_relative_to(VENV.resolve())
    except (OSError, ValueError):
        inside = False
    return "" if inside else executable


def find_python(versions: list[str]) -> tuple[str, str] | None:
    """A Python installed on this computer in one of ``versions`` (3.12 first): (its executable, its version)."""
    for version in sorted(versions, key=lambda v: v != "3.12"):  # 3.12 first, then in the order given
        for command in python_candidates(version):
            executable = python_found(command, version)
            if executable:
                return executable, version
    return None


def fitting_pythons() -> list[str]:
    """The Python versions the offline bundle's wheelhouse has every core package for, on this kind of computer
    (from its wheels; from the manifest when there are none to judge by). [] when unknown."""
    wheels = _core_wheels()
    if not wheels:
        return bundle_pythons()
    system, arch = _computer_kind(wheel_platform())
    per_package = []
    for tags in wheels.values():
        versions: set[str] = set()
        for interpreter, abi, plat in tags:
            kind = _computer_kind(plat)
            if plat != "any" and not (kind[0] == system and (kind[1] == arch or "universal2" in (kind[1], arch))):
                continue
            found = re.fullmatch(r"cp3(\d+)", interpreter)
            if found and abi == "abi3":  # one wheel for this and every later Python
                versions |= {f"3.{n}" for n in range(int(found.group(1)), 30)}
            elif found:
                versions.add(f"3.{found.group(1)}")
        per_package.append(versions)
    return sorted(set.intersection(*per_package), key=_version_key) if per_package else []


def venv_fits() -> bool:
    """The double-click's quick check, run with .venv's Python (``--venv-ok``): use .venv as it is? Yes, unless its
    Python has no packages in the offline bundle, AP Coder's packages are not all in place yet, and a Python the
    bundle has packages for is installed (then .venv is made again with that one)."""
    here = f"{sys.version_info[0]}.{sys.version_info[1]}"
    fitting = fitting_pythons()
    if not fitting or here in fitting:
        return True
    missing, _ = missing_packages(("ocr",))
    if not [r for r in missing if not is_ocr(r)]:
        return True
    return find_python(fitting) is None


def venv_fits_probe() -> bool:
    """:func:`venv_fits`, asked of .venv's own Python."""
    try:
        probe = subprocess.run([str(VENV_PY), str(Path(__file__).resolve()), "--venv-ok"], capture_output=True,
                               timeout=120)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def _version_key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)


_ARCH = re.compile(r"(amd64|x86_64|arm64|aarch64|universal2|win32|i686)$")
_SAME_ARCH = {"amd64": "x86_64", "aarch64": "arm64"}


def _computer_kind(platform_tag: str) -> tuple[str, str]:
    """("windows" / "mac" / "linux", processor) of a wheel platform ("win_amd64", "macosx_14_0_arm64",
    "manylinux2014_x86_64") or a sysconfig one ("win-amd64", "linux-x86_64"). The macOS version is not compared."""
    tag = re.sub(r"[-.]", "_", platform_tag.strip().lower())
    system = "windows" if tag.startswith("win") else "mac" if tag.startswith("macosx") else "linux" * ("linux" in tag)
    found = _ARCH.search(tag)
    arch = found.group(1) if found else ""
    return system, _SAME_ARCH.get(arch, "x86" if arch == "win32" else arch)


def _for_this_computer(target: str) -> bool:
    """A manifest target ("win_amd64, Python 3.12", "this computer (linux-x86_64, Python 3.12)") has packages for
    this kind of computer (the same system and processor; a universal2 Mac build counts for either Mac)."""
    tag = target.split(", Python ")[0].removeprefix("this computer (").strip()
    (system, arch), (here_system, here_arch) = _computer_kind(tag), _computer_kind(wheel_platform())
    return bool(system) and system == here_system and (arch == here_arch or "universal2" in (arch, here_arch))


def bundle_pythons(here_only: bool = True) -> list[str]:
    """The Python versions the offline bundle's wheelhouse was built for ("3.11", "3.12", ...; [] when unknown), on
    this kind of computer (``here_only``; a Windows-only bundle has none for a Mac) or on any."""
    try:
        targets = json.loads((ROOT / "bundle_manifest.json").read_text(encoding="utf-8")).get("targets", [])
    except (OSError, ValueError, AttributeError):
        return []
    found = {t.rsplit("Python ", 1)[-1].strip(")").strip() for t in targets
             if isinstance(t, str) and "Python " in t and (not here_only or _for_this_computer(t))}  # fmt: skip
    return sorted(found, key=_version_key)


def wheel_platform() -> str:
    """This computer as ``build_offline_bundle.py --platform`` names it (``win_amd64``, ``macosx_14_0_arm64``)."""
    machine = platform.machine().lower()
    if WINDOWS:
        return {"arm64": "win_arm64", "x86": "win32", "i386": "win32", "i686": "win32"}.get(machine, "win_amd64")
    arm = machine in ("arm64", "aarch64")
    if sys.platform == "darwin":
        release = [int(n) for n in (platform.mac_ver()[0] or "11.0").split(".")[:2] if n.isdigit()] or [11]
        major, minor = release[0], (release[1] if len(release) > 1 else 0)
        return f"macosx_{f'{major}_0' if major >= 11 else f'10_{minor}'}_{'arm64' if arm else 'x86_64'}"
    return f"manylinux2014_{'aarch64' if arm else 'x86_64'}"


def listed(versions: list[str]) -> str:
    """``3.11``, ``3.11 and 3.12``, ``3.11, 3.12 and 3.13``."""
    return versions[0] if len(versions) == 1 else f"{', '.join(versions[:-1])} and {versions[-1]}"


def bundle_advice(version: str, venv_made: bool = False) -> str:
    """What to do when the offline bundle has no packages for Python ``version`` ("3.14") on this computer.
    ``venv_made``: .venv exists with that Python, and the double-click keeps using it until it is deleted."""
    built_for = bundle_pythons()
    build = f"python scripts/build_offline_bundle.py --python {version} --platform {wheel_platform()}"
    if not built_for and bundle_pythons(here_only=False):
        return (f"This offline bundle has no packages for this kind of computer ({wheel_platform()}): IT can build "
                f"one for it ({build}).")  # fmt: skip
    best = "3.12" if "3.12" in built_for or not built_for else built_for[-1]
    made = f"This offline bundle was built for Python {listed(built_for)}. " if built_for else ""
    venv = f" and delete the .venv folder in {ROOT}" if venv_made else ""
    return f"{made}IT can build a bundle for this Python ({build}), or install Python {best} on this computer{venv}."


def bootstrap(argv: list[str]) -> int:
    """Run with a Python found on this computer: make ``.venv``, then carry on inside it. When the offline bundle has
    no packages for this Python but has them for another one installed here, that one is used instead; a .venv made
    with a Python the bundle has no packages for (and not set up yet) is made again."""
    version = sys.version_info[:2]
    shown = f"{version[0]}.{version[1]}"
    fitting = fitting_pythons()
    if fitting and shown not in fitting and (not VENV_PY.exists() or not venv_fits_probe()):
        better = find_python(fitting)
        if better:
            say(f"Python {shown} has no packages in this offline bundle: using Python {better[1]} ({better[0]}).")
            try:  # (this process holds the lock: LOCK_HELD_ENV says so to the one it starts)
                return subprocess.call([better[0], str(Path(__file__).resolve()), *argv])
            except KeyboardInterrupt:
                return 0
    made_with = venv_version()
    rebuild = bool(VENV_PY.exists() and fitting and made_with and made_with not in fitting and shown in fitting
                   and not venv_fits_probe())  # fmt: skip
    if rebuild:
        say(f".venv was made with Python {made_with}, which this offline bundle has no packages for: making it "
            f"again with Python {shown}.")  # fmt: skip
    if not supported(version) and not VENV_PY.exists():
        say(f"Note: Python {shown} is older than AP Coder needs (3.11 or newer; 3.12 is best).")
    built_for = bundle_pythons()
    # (with wheels to judge by, the second phase says what they fit, once, before installing anything)
    if bundle_pythons(here_only=False) and shown not in built_for and not VENV_PY.exists() and not _core_wheels():
        has = (f"packages for Python {listed(built_for)}, not for Python {shown} (the Python found on this computer)"
               if built_for else "no packages for this kind of computer")  # fmt: skip
        say(f"Note: this offline bundle has {has}, so the setup may stop. {bundle_advice(shown)}")
    if not VENV_PY.exists():
        say("First run: setting up AP Coder. This takes a few minutes; later starts take seconds.")
    say(f"Python {sys.version.split()[0]} found on this computer: {sys.executable}")
    try:
        status = ensure_venv(rebuild=True) if rebuild else ensure_venv()
    except SetupError as exc:
        return failed(exc)
    try:  # (this process holds the lock for both phases: main() says so to the second one, LOCK_HELD_ENV)
        return subprocess.call([str(VENV_PY), str(Path(__file__).resolve()), *argv, f"--venv={status}"])
    except KeyboardInterrupt:
        return 0


def failed(exc: SetupError) -> int:
    say()
    line("fail", str(exc))
    say(f"  What to do: {exc.hint}")
    if log(""):
        say(f"  (Everything this start said is also in {log_path()}.)")
    return 1


def unexpected(exc: BaseException) -> int:
    """An error no step expected: one plain line, and the details (the traceback) in the log for IT, instead of a
    traceback under "The message above says what to do". Without a log the traceback is shown after all."""
    import traceback

    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logged = log(details)
    say()
    line("fail", f"AP Coder stopped on an unexpected error ({type(exc).__name__}: {exc}).")
    if logged:
        say(f"  What to do: start AP Coder again. If it happens again, send IT the log {log_path()} (it has the "
            "details).")  # fmt: skip
    else:
        sys.stderr.write(details)
        say("  What to do: start AP Coder again. If it happens again, send IT the message above.")
    return 1


# --- Phase 2: packages, data, checks (inside .venv) -------------------------------------------------------------


def internet_forbidden() -> bool:
    """``AP_ALLOW_INTERNET=0`` (or false / no / off): the setup never downloads, even from the code alone."""
    return (os.environ.get(INTERNET_ENV) or "").strip().lower() in ("0", "false", "no", "off")


def plain_download() -> bool:
    """This folder is the code alone: GitHub's ZIP or a git checkout, with no offline bundle's manifest, wheelhouse or
    models folder. Its setup downloads the packages and OCR models once, as CloseDesk does (unless
    ``AP_ALLOW_INTERNET=0``); the dashboard it then starts stays offline. A bundle missing its wheelhouse is not one."""
    if internet_forbidden() or (ROOT / "bundle_manifest.json").exists() or fetch_models.BUNDLE_MODELS.is_dir():
        return False
    return not wheelhouse_dir().is_dir()


def setup_online() -> bool:
    """The setup may download: with the opt-in (AP_ALLOW_INTERNET=1), or from the code alone (plain_download)."""
    return internet_allowed() or plain_download()


class setup_downloads:  # noqa: N801 - used as ``with setup_downloads():``
    """The opt-in for one setup step that asks :func:`internet_allowed` itself (fetch_models), set only while it runs:
    never in the environment of the dashboard started afterwards."""

    def __enter__(self) -> None:
        self.before = os.environ.get(INTERNET_ENV)
        os.environ[INTERNET_ENV] = "1"

    def __exit__(self, *exc: object) -> None:
        if self.before is None:
            os.environ.pop(INTERNET_ENV, None)
        else:
            os.environ[INTERNET_ENV] = self.before


def pip_source() -> list[str] | None:
    """Where pip may install from: the offline bundle's wheelhouse/ folder, with ``--no-index`` (never the
    internet). Without one: PyPI ([]) when the setup may download (:func:`setup_online`: the code alone, or the
    opt-in), else None (nowhere). A wheelhouse built for another kind of computer or Python is passed over for PyPI
    only with the opt-in."""
    wheelhouse = wheelhouse_dir()
    if wheelhouse.is_dir() and not (internet_allowed() and wheelhouse_misfit()):
        return ["--no-index", "--find-links", str(wheelhouse)]
    return [] if setup_online() else None


# The packages with compiled code AP Coder can't run without: their wheels say which computers and Pythons the
# wheelhouse was built for (the pure-Python ones fit anywhere).
CORE_BINARY = ("numpy", "pandas", "pyarrow", "pymupdf", "pillow", "pydantic-core")
SYSTEM_NAMES = {"windows": "Windows", "mac": "Macs", "linux": "Linux"}
THIS_KIND = {"windows": "a Windows computer", "mac": "a Mac", "linux": "a Linux computer"}


def _holding_wheels(folder: Path) -> Path | None:
    """``folder`` when it holds wheels; else the one folder in it that does (a wheelhouse copied over another as a
    whole lands in wheelhouse/wheelhouse); else None."""
    try:
        if not folder.is_dir():
            return None
        if any(folder.glob("*.whl")):
            return folder
        inner = [d for d in folder.iterdir() if d.is_dir() and any(d.glob("*.whl"))]
    except OSError:
        return None
    return inner[0] if len(inner) == 1 else None


NEAR_MISS = re.compile(r"[._\s-]*wheel[\s_-]?house\b.*", re.IGNORECASE)  # .wheelhouse, wheelhouse (1), Wheelhouse-old


def wheelhouse_candidates() -> list[Path]:
    """Folders of wheels under a near-miss name, when wheelhouse/ itself has none: ``.wheelhouse``, ``wheelhouse
    (1)``, ``Wheelhouse-old``..., and a bundle unzipped into this folder (``<bundle>/wheelhouse``)."""
    try:
        children = [d for d in ROOT.iterdir() if d.is_dir() and d.name not in (".venv", "wheelhouse", "__pycache__")]
    except OSError:
        return []
    found = [_holding_wheels(d) for d in children if NEAR_MISS.fullmatch(d.name)]
    found += [_holding_wheels(d / "wheelhouse") for d in children if not NEAR_MISS.fullmatch(d.name)]
    return list(dict.fromkeys(f for f in found if f is not None))


def wheelhouse_dir() -> Path:
    """The offline bundle's wheelhouse/ folder (or the one folder of wheels in it); when it holds none, the one folder
    of wheels under a near-miss name (:func:`wheelhouse_candidates`; several: none is guessed)."""
    own = _holding_wheels(ROOT / "wheelhouse")
    if own is not None:
        return own
    found = wheelhouse_candidates()
    return found[0] if len(found) == 1 else ROOT / "wheelhouse"


def _core_wheels(folder: Path | None = None) -> dict[str, list[tuple[str, str, str]]]:
    """The (interpreter, abi, platform) tags of the core compiled packages' wheels in the wheelhouse, by package."""
    folder = folder or wheelhouse_dir()
    found: dict[str, list[tuple[str, str, str]]] = {}
    try:
        files = [f.name for f in folder.iterdir() if f.name.endswith(".whl")]
    except OSError:
        return {}
    for name in files:
        parts = name[:-4].split("-")
        package = re.sub(r"[-_.]+", "-", parts[0]).lower()
        if len(parts) < 5 or package not in CORE_BINARY:
            continue
        found.setdefault(package, []).extend(
            (i, a, p) for i in parts[-3].split(".") for a in parts[-2].split(".") for p in parts[-1].split(".")
        )
    return found


def here_tags() -> set[str]:
    """The wheel tags this Python accepts ("cp312-cp312-win_amd64", ...), as pip judges them."""
    try:
        from packaging.tags import sys_tags
    except ImportError:  # pip carries its own copy
        from pip._vendor.packaging.tags import sys_tags
    return {str(tag) for tag in sys_tags()}


def this_computer() -> str:
    return "windows" if WINDOWS else "mac" if sys.platform == "darwin" else "linux"


def _cpython(interpreter: str, abi: str) -> str:
    """ "3.12" for a wheel made for CPython 3.12 only ("" for one made for several, abi3)."""
    found = re.fullmatch(r"cp3(\d+)", interpreter)
    return f"3.{found.group(1)}" if found and abi != "abi3" else ""


def wheelhouse_misfit() -> str:
    """ "" when the wheelhouse fits this computer and Python (or can't be judged), else one plain sentence on what
    it was built for, what this computer is, and what to do."""
    wheels = _core_wheels()
    here = here_tags()
    if not wheels or all(any(f"{i}-{a}-{p}" in here for i, a, p in tags) for tags in wheels.values()):
        return ""
    tags = [tag for found in wheels.values() for tag in found]
    systems = [s for s in SYSTEM_NAMES if s in {_computer_kind(p)[0] for _i, _a, p in tags}]
    pythons = sorted({v for i, a, _p in tags if (v := _cpython(i, a))}, key=_version_key)
    built = " and ".join(SYSTEM_NAMES[s] for s in systems) or "another kind of computer"
    built += f", Python {'/'.join(pythons)}" if pythons else ""
    system, version = this_computer(), f"{sys.version_info[0]}.{sys.version_info[1]}"
    start = f"This package folder (wheelhouse) was built for {built}"
    if system not in systems:
        other = f"Use a bundle built for this computer (build it with --platform {wheel_platform()})"
        advice = {"mac": "Use the bundle built for Macs (build it with --mac)",
                  "windows": "Use the bundle built for Windows (the default build)"}.get(system, other)  # fmt: skip
        return f"{start}; this computer is {THIS_KIND[system]} with Python {version}. {advice}"
    if pythons and version not in pythons:
        best = "3.12" if "3.12" in pythons else pythons[-1]
        return (f"{start}; this computer has Python {version}: install Python {best} (then start AP Coder again), "
                f"or use a bundle built for Python {version} (build it with --python {version})")  # fmt: skip
    if system == "mac" and under_rosetta():
        return (f"{start}; this Python ({sys.executable}) is an Intel build running through Rosetta on an Apple "
                "silicon Mac, so it can't use the Apple silicon packages")  # fmt: skip
    return (f"{start}; this computer ({wheel_platform()}, Python {version}) can't use it. Use a bundle built for it "
            f"(build it with --platform {wheel_platform()} --python {version})")  # fmt: skip


ROSETTA_HINT = (
    "Install Python 3.12 from https://www.python.org/downloads/ (its macOS installer runs natively on Apple silicon), "
    "delete the .venv folder in {root}, then open APProcessor.command again. If Terminal is set to Open using "
    "Rosetta (Finder > Applications > Utilities > Terminal > Get Info), untick it."
)


def under_rosetta() -> bool:
    """An Intel (x86_64) Python on an Apple silicon Mac, through Rosetta: an Intel Homebrew in /usr/local, or Terminal
    set to open using Rosetta. Asked of the system (sysctl), never through a dialog."""
    if sys.platform != "darwin" or platform.machine() != "x86_64":
        return False
    try:
        out = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.stdout.strip() == "1"


def pip_env(source: list[str]) -> dict[str, str]:
    """pip's environment. From the wheelhouse (``--no-index``) pip reads no settings of this computer: a PIP_FIND_LINKS
    or a pip.conf ``find-links`` (an office's package server, say) would still be fetched next to the wheelhouse, so
    every PIP_* variable is left out and no configuration file is read (``PIP_CONFIG_FILE=os.devnull``)."""
    env = child_env()  # UTF-8 output, read back as UTF-8
    if "--no-index" in source:
        env = {k: v for k, v in env.items() if not k.upper().startswith("PIP_")}
        env["PIP_CONFIG_FILE"] = os.devnull
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def pip_install(args: list[str]) -> bool:
    """``pip install`` with its progress lines (not the long "already satisfied" list). Offline without a
    wheelhouse it runs nothing: there is nowhere to install from."""
    source = pip_source()
    if source is None:
        say("    Nothing installed: this offline build installs packages only from the bundle's wheelhouse folder.")
        return False
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off",
           *source, *args]  # fmt: skip
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8",
                            errors="replace", env=pip_env(source))  # fmt: skip
    assert proc.stdout is not None
    for out in proc.stdout:
        text = out.rstrip()
        if text and not text.startswith(QUIET_PIP_LINES):
            say(f"    {text}")
        if any(sign in text for sign in DISK_FULL):
            _disk_full.append(text)
        if any(sign in text for sign in PATH_TOO_LONG):
            _path_too_long.append(text)
        if any(sign in text for sign in FILE_IN_USE):
            _file_in_use.append(text)
    return proc.wait() == 0


# How pip says Windows refused a file it must write or replace: open in another program (an AP Coder window still
# running from this .venv) or held by the antivirus while it scans a new .pyd.
FILE_IN_USE = ("[WinError 5]", "[WinError 32]")
_file_in_use: list[str] = []


def file_in_use_error() -> SetupError:
    return SetupError(
        "Installing the packages stopped: Windows refused a file in .venv because another program has it in use (an "
        "AP Coder window that is still open, or the antivirus checking a new file).",
        "Close every AP Coder window, wait a minute for the antivirus to finish, then double-click APProcessor.bat "
        "again: it picks up where it stopped. If it happens again, ask IT to let the antivirus allow the "
        f"{VENV} folder.",
    )


# How pip says a file's path passed Windows' 260 characters (its own hint, or Windows' error 206).
PATH_TOO_LONG = ("Long Path support", "enable-long-paths", "[WinError 206]")
_path_too_long: list[str] = []


def path_too_long_error() -> SetupError:
    return SetupError(
        f"Installing the packages stopped: the AP Coder folder's path is too long for Windows ({len(str(ROOT))} "
        f"characters: {ROOT}), so a package file's full path passes the 260 characters Windows allows.",
        "Unzip the offline bundle to a short folder such as C:\\APCoder (or move this folder there), then "
        "double-click APProcessor.bat in it. (Or IT turns on Windows' long paths: LongPathsEnabled.)",
    )


# How pip says the disk is full (Linux and Mac: errno 28; Windows: error 112, or errno 28 from Python's own code).
DISK_FULL = ("No space left on device", "[Errno 28]", "[WinError 112]", "not enough space on the disk")
_disk_full: list[str] = []  # pip's lines saying so, on this start


def disk_full_error() -> SetupError:
    """The disk is full: said as that, with the space the older installs' .venv folders take (each can go)."""
    older = [Path(e["folder"]) / ".venv" for e in paths.installs() if not paths.same_folder(e["folder"], ROOT)]
    sizes = [(venv, upgrade.size_of(venv)) for venv in older if venv.is_dir()]
    free = ""
    try:
        free = f" ({upgrade.shown_size(shutil.disk_usage(ROOT).free)} free)"
    except OSError:
        pass
    hint = (f"Free up space on the disk of {ROOT}{free}: AP Coder's packages need about 1.5 GB. Then close any "
            "other AP Coder window and start AP Coder again.")  # fmt: skip
    if sizes:
        listed = ", ".join(f"{venv} ({upgrade.shown_size(size)})" for venv, size in sizes)
        hint += (f" The older AP Coder's .venv folder can be deleted to make room: {listed} (it holds only "
                 "packages, never your data; the older version is cleaned up once this one works).")  # fmt: skip
    return SetupError("Installing the packages stopped: the disk is full.", hint)


def missing_packages(extras: tuple[str, ...]) -> tuple[list[str], str]:
    """The requirements (core and ``extras``) not met in this environment, and why ap_coder itself is not
    installed from this folder ("" when it is)."""
    importlib.invalidate_caches()
    missing = [req for req in check_deps.requirements(extras=extras) if check_deps.problem(req)]
    return missing, check_deps.self_problem()


def is_ocr(requirement: str) -> bool:
    return check_deps.requirement_name(requirement) in OCR_PACKAGES


def editable(extras: tuple[str, ...]) -> list[str]:
    return ["-e", f".[{','.join(extras)}]" if extras else "."]


def names(requirements: list[str]) -> str:
    return ", ".join(check_deps.requirement_name(r) for r in requirements)


def install_hint(source: list[str], left: list[str]) -> str:
    """What to do when pip could not install ``left``: from the wheelhouse, or (opted in) from PyPI."""
    if not source:
        return ("Check the internet connection (behind a company proxy: set HTTPS_PROXY=http://proxy:port in this "
                "window), close any other AP Coder window, then start AP Coder again. A computer with no internet "
                "needs the offline bundle instead (docs/GETTING_STARTED.md).")  # fmt: skip
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    what = names(left) or "AP Coder itself"
    return (f"The offline bundle's wheelhouse folder has no {what} for Python {version} on this computer. "
            f"{bundle_advice(version, venv_made=True)} Then close any other AP Coder window and start AP Coder "
            "again.")  # fmt: skip


def without_wheelhouse(missing: list[str], total: int) -> tuple[str, str, bool]:
    """Offline with no wheelhouse/ folder: nothing can be installed, and nothing is downloaded. AP Coder starts
    when its core packages are there (running from this folder); what is missing is said plainly."""
    core = set(check_deps.requirements())
    missing_core = [r for r in missing if r in core]
    if missing_core:
        raise SetupError(
            f"Missing packages: {names(missing_core)}. This offline build never downloads packages: they come "
            "only from the offline bundle's wheelhouse folder, and there is none here.",
            "Unzip the full offline bundle (with its wheelhouse folder) over this folder, then start AP Coder "
            f"again. (Developers with internet: set {INTERNET_ENV}=1.)",
        )
    if missing:
        scans = " (scanned invoices can't be read without the OCR add-on)" if any(map(is_ocr, missing)) else ""
        return ("warn", f"Packages: core in place; missing {names(missing)}: it must come from the offline "
                        f"bundle's wheelhouse folder{scans}", False)  # fmt: skip
    return "ok", f"Packages: all {total} in place (AP Coder runs from this folder)", False


# The second OCR engine (PP-OCRv4, models inside the package) is pure Python but says "Python below 3.13" in its
# metadata, so pip leaves it out on 3.13 and newer (pyproject's marker). It works there: installed on its own,
# without that check, so a scan is read by two engines on every Python.
SECOND_OCR = "rapidocr-onnxruntime>=1.3"


def ensure_second_ocr(state: dict) -> str:
    """On Python 3.13 or newer, the second OCR engine from the wheelhouse when it is missing (once per version of
    AP Coder if it can't be installed). Returns a note for the readiness lines ("" when nothing to say)."""
    if sys.version_info[:2] < (3, 13) or importlib.util.find_spec("rapidocr_onnxruntime") is not None:
        return ""
    if importlib.util.find_spec("rapidocr") is None or state.get("ocr2_failed") == fingerprint():
        return ""  # no OCR add-on at all (said already), or tried for this version
    if pip_source() is None:
        return ""
    say("Installing the second OCR engine (it reads scans a second way; its metadata predates this Python).")
    if pip_install(["--ignore-requires-python", SECOND_OCR]):
        importlib.invalidate_caches()
        state.pop("ocr2_failed", None)
        return ""
    state["ocr2_failed"] = fingerprint()
    return "The second OCR engine could not be installed: scans are read by one OCR engine (and OvisOCR2)."


def ensure_packages(state: dict, extras: tuple[str, ...] = ("ocr",)) -> tuple[str, str, bool]:
    """Install only what is missing or too old: nothing when everything is in place. Offline (the default)
    only from the bundle's wheelhouse/ folder. Returns (level, summary line, whether pip installed something).
    On Python 3.13 or newer the second OCR engine is added too (``ensure_second_ocr``), and the optional Azure
    check's packages when the wheelhouse has them (``ensure_azure``: never a reason to stop)."""
    level, summary, installed = _ensure_packages(state, extras)
    refresh_registration()
    if "ocr" in extras and level != "fail":
        note = ensure_second_ocr(state)
        if note and level == "ok":
            level, summary = "warn", f"{summary}. {note}"
    ensure_azure(state)
    return level, summary, installed


def ensure_azure(state: dict) -> bool:
    """The packages of the optional Azure check (the ``azure`` extra), installed from the offline bundle's wheelhouse
    when it carries them, so switching the check on in the settings needs no download. Never from the internet,
    never a reason to stop (AP Coder runs without them), tried once per version when it fails. True when installed."""
    wheelhouse = wheelhouse_dir()
    core = set(check_deps.requirements())
    wanted = [r for r in check_deps.requirements(extras=("azure",)) if r not in core]
    try:
        missing = [r for r in wanted if check_deps.problem(r)]
        carried = wheelhouse.is_dir() and any(f.name.lower().startswith("azure_ai_documentintelligence")
                                              for f in wheelhouse.glob("*.whl"))  # fmt: skip
    except Exception:  # noqa: BLE001 - optional: never stops the start
        return False
    if not missing:
        state.pop("azure_failed", None)
        return False
    if not carried or state.get("azure_failed") == fingerprint() or not pip_source():
        return False  # (pip_source() [] would be the internet: never for an optional package)
    say("Installing the optional Azure check's packages from the wheelhouse (no internet needed; off until it is "
        "switched on in the settings).")  # fmt: skip
    if pip_install(missing):
        state.pop("azure_failed", None)
        return True
    state["azure_failed"] = fingerprint()
    say("(Not needed: AP Coder runs without them. Not tried again for this version.)")
    return False


def refresh_registration() -> None:
    """Unzipped over an older version, ap_coder is still registered in .venv with the older version's number (what
    the running server reports): registered again, without downloading anything. Never stops the start."""
    version = identity()["version"]
    registered = check_deps.registered_version(ROOT)
    if not registered or not version or registered == version or pip_source() is None:
        return
    say(f"Registering AP Coder {version} in .venv (it still said {registered}; nothing to download).")
    pip_install(["--no-deps", *editable(())])


def _ensure_packages(state: dict, extras: tuple[str, ...]) -> tuple[str, str, bool]:
    total = len(check_deps.requirements(extras=extras))
    missing, self_problem = missing_packages(extras)
    if not missing and not self_problem:
        return "ok", f"Packages: all {total} in place", False
    if not self_problem and all(is_ocr(r) for r in missing) and state.get("ocr_failed") == fingerprint():
        return "warn", "Packages: in place, without the OCR add-on (it could not be installed here)", False

    if _holding_wheels(ROOT / "wheelhouse") is None:  # a package folder under another name?
        found = wheelhouse_candidates()
        if len(found) > 1:
            shown = ", ".join(str(f.relative_to(ROOT)) for f in found)
            raise SetupError(f"There are several package folders here, none named wheelhouse: {shown}. Nothing was "
                             "installed.", "Keep the one from the newest offline bundle, rename it to wheelhouse (next "
                             "to APProcessor), delete the others, then start AP Coder again.")  # fmt: skip
        if found:
            say(f"Using the package folder {found[0].relative_to(ROOT)} (AP Coder looks for one named wheelhouse).")
    source = pip_source()
    if source is None:
        return without_wheelhouse(missing, total)
    misfit = wheelhouse_misfit() if wheelhouse_dir().is_dir() else ""
    if misfit and source:  # offline: pip would only fail with a long, unclear error
        python = re.search(r"this computer has Python [\d.]+: install Python ([\d.]+)", misfit)  # (only Python differs)
        if python:
            hint = (
                f"Install Python {python.group(1)} on this computer (from python.org, or ask IT), then start AP Coder "
                "again: it is found and used on its own. Or unzip a bundle built for this Python."
            )
        elif "through Rosetta" in misfit:
            hint = ROSETTA_HINT.format(root=ROOT)
        else:
            hint = ("Unzip the offline bundle built for this computer (into a new folder, or over this one), then "
                    "start AP Coder again.")  # fmt: skip
        raise SetupError(f"{misfit}: nothing was installed.", hint)
    if source:
        say("Installing from the wheelhouse folder (no internet needed).")
    elif misfit:
        say(f"{misfit}. Installing from the internet (PyPI) instead: {INTERNET_ENV}=1 allows it.")
    elif internet_allowed():
        say(f"Installing from the internet (PyPI): {INTERNET_ENV}=1 allows it.")
    else:
        say("This folder is AP Coder's code without the offline bundle's packages (GitHub's ZIP): downloading them "
            "from the internet once, for the setup only. AP Coder itself never goes online.")  # fmt: skip
    plain = tuple(e for e in extras if e != "ocr")
    if self_problem and missing:
        say(f"Installing AP Coder's packages ({len(missing)} missing). The first time takes a few minutes.")
        tries = [editable(extras)] + ([editable(plain)] if plain != extras else [])
    elif self_problem:
        say("Registering this AP Coder folder in .venv (nothing to download).")
        tries = [["--no-deps", *editable(())]]
    else:
        say(f"Installing only what this version needs: {', '.join(missing)}")
        core = [r for r in missing if not is_ocr(r)]
        tries = [missing] + ([core] if core and core != missing else [])
    for number, pip_args in enumerate(tries):
        if number:
            say("The OCR add-on (for scanned invoices) could not be installed here; trying without it.")
        if pip_install(pip_args):
            break
    left, self_left = missing_packages(extras)
    if (self_left or left) and _disk_full:
        raise disk_full_error()
    if (self_left or left) and _path_too_long:
        raise path_too_long_error()
    if (self_left or left) and _file_in_use:
        raise file_in_use_error()
    if self_left or [r for r in left if not is_ocr(r)]:
        raise SetupError(
            "Installing the packages failed (the messages above say why).",
            install_hint(source, [r for r in left if not is_ocr(r)]),
        )
    if left:  # only the OCR add-on: AP Coder runs without it, and doesn't try again until pyproject changes
        state["ocr_failed"] = fingerprint()
        return (
            "warn",
            f"Packages: installed what was missing except the OCR add-on ({total - len(left)} of {total})",
            True,
        )
    state.pop("ocr_failed", None)
    return "ok", f"Packages: installed what was missing; all {total} in place", True


def ensure_data_folder() -> Path:
    """The data folder: the one already recorded, else ``~/APCoder`` (recorded once). Data an older AP Coder kept
    inside a code folder (``private``) is moved there first (``scripts/upgrade.py``, settle_data_folder)."""
    import first_run

    data = first_run.choose_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / "invoices").mkdir(exist_ok=True)
    return data


def settle_settings(data: Path, version: str) -> list[tuple[str, str]]:
    """The settings file: a ``.env`` (or data) left in this code folder moves to the data folder, and settings no
    longer used are commented out, with a note, instead of looking like they still do something."""
    notes = []
    if not (ROOT / ".git").exists():  # a developer's checkout keeps its own files
        try:
            if upgrade.adopt_legacy(ROOT, data, say):
                upgrade.repoint_files(ROOT, data, ROOT, say, copy=False)  # its invoices: where their files went
        except OSError as exc:  # in use or read-only: tried again on the next start, never a reason to stop
            notes.append(("warn", f"The settings or data in {ROOT} can't be changed ({exc.strerror or exc}): not "
                                  "moved to the data folder yet"))  # fmt: skip
    return notes + retire_settings(data, version)


def retire_settings(data: Path, version: str) -> list[tuple[str, str]]:
    from ap_coder.core.config import RETIRED_SETTINGS

    env = data / ".env"
    short = ".".join(version.split(".")[:2]) or version
    try:
        found = envfile.retire(env, RETIRED_SETTINGS, f"no longer used (AP Coder {short})")
    except OSError as exc:  # read-only or open: the settings no longer used do nothing either way
        return [("info", f"Settings file {env} can't be changed ({exc.strerror or exc}): settings no longer used "
                         "are left in it (they do nothing)")]  # fmt: skip
    if not found:
        return []
    return [("info", f"Settings file: {', '.join(found)} no longer used (commented out in {env})")]


def database_too_new(data: Path, version: str) -> SetupError | None:
    """A database made by a newer AP Coder: this older version never opens it (nothing changed)."""
    from ap_coder.core.store import SCHEMA_VERSION, database_schema

    database = paths.env_path("AP_DB_PATH") or data / "ap_coder.db"
    found = database_schema(database)
    if not found or found <= SCHEMA_VERSION:
        return None
    newest = paths.current_install()
    where = (f"its folder is {newest['folder']}" if newest and not paths.same_folder(newest["folder"], ROOT)
             else "unzip the newest offline bundle and double-click its APProcessor")  # fmt: skip
    return SetupError(
        f"Your database {database} was made by a newer AP Coder (database version {found}; AP Coder {version} knows "
        f"up to version {SCHEMA_VERSION}): this older version does not open it, and nothing was changed.",
        f"Start the newer AP Coder instead ({where}).",
    )


def models_attempt() -> str:
    """What a copy of the OCR models depends on: this version, the opt-in, and the bundle's models/ folder.
    A copy that failed is tried again only when one of them changes (e.g. the full bundle is unzipped); a download
    that failed (the code alone) is tried again the next day."""
    import datetime

    folder = fetch_models.BUNDLE_MODELS
    try:
        files = sorted(f"{p.name}:{p.stat().st_size}" for p in folder.iterdir()) if folder.is_dir() else []
    except OSError:
        files = []
    online = setup_online() and not folder.is_dir()
    day = datetime.date.today().isoformat() if online else ""
    return f"{fingerprint()}|{online}|{folder.is_dir()}|{','.join(files)}|{day}"


def ensure_models(first: bool, state: dict) -> None:
    """The PP-OCRv5 OCR models, put in place only when missing (checksums checked on the first run): copied from
    the offline bundle's models/ folder; downloaded only when the setup may (:func:`setup_online`: the code alone,
    or AP_ALLOW_INTERNET=1), with the table finder's model then too (into the data folder's models/ folder, so the
    code folder never looks like a bundle). When that fails it is said once and recorded, not tried again on every
    start (a download: not before the next day)."""
    from ap_coder.ops import offline

    online = not fetch_models.BUNDLE_MODELS.is_dir() and setup_online()
    finder = online and offline.onnxruntime_installed() and offline.table_finder_path() is None
    if (not offline.ppocrv5_installed() or not offline.missing_models(verify=first)) and not finder:
        state.pop("models_failed", None)
        return
    attempt = models_attempt()
    if state.get("models_failed") == attempt:
        return  # said when it failed; the readiness summary still says scans are read with one engine
    if not online and not fetch_models.BUNDLE_MODELS.is_dir():
        say("The PP-OCRv5 OCR models are not here, and this offline build never downloads them: they come from the "
            "offline bundle's models folder. Scans are read with the other OCR engine.")  # fmt: skip
        state["models_failed"] = attempt
        return
    say("Downloading the OCR models (once)." if online else "Copying the OCR models from the offline bundle (once).")
    if online:
        with setup_downloads():
            code = fetch_models.main(["--table-finder"] if finder else [])
        if finder and offline.table_finder_path() is None:
            code = code or 1
    else:
        code = fetch_models.main([])
    if code != 0:
        later = "Tried again tomorrow." if online else "Not tried again until the models folder changes."
        say(f"(Not needed to start: scans are read with the other OCR engine. {later})")
        state["models_failed"] = attempt
    else:
        state.pop("models_failed", None)


def desktop() -> Path | None:
    if MAC:
        folder = Path.home() / "Desktop"
        return folder if folder.is_dir() else None
    if not WINDOWS:
        return None
    folder = running.powershell("[Environment]::GetFolderPath('Desktop')", timeout=60)  # (read as UTF-8)
    return Path(folder) if folder else None


def write_shortcut(folder: Path) -> bool:
    """One 'AP Coder' shortcut to APProcessor.bat: written over in place, never a second copy."""
    target = str(ROOT / "APProcessor.bat").replace("'", "''")
    workdir = str(ROOT).replace("'", "''")
    link = str(folder / SHORTCUT).replace("'", "''")
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        f"$s.TargetPath='{target}';$s.WorkingDirectory='{workdir}';"
        "$s.Description='AP Coder: invoice review dashboard';$s.Save()"
    )
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    return subprocess.run(cmd, capture_output=True).returncode == 0


def write_mac_launcher(link: Path) -> bool:
    """A Mac's "AP Coder.command" on the desktop: two lines that open the current install's APProcessor.command
    (a link would not do: APProcessor.command starts in its own folder). Only ever written over one of ours."""
    if link.exists() and LAUNCHER_MARK not in link.read_text(encoding="utf-8", errors="replace"):
        return False  # the person's own file: never replaced
    target = shlex.quote(str(ROOT / "APProcessor.command"))
    try:
        link.write_text(f"#!/bin/bash\n{LAUNCHER_MARK} (rewritten when a newer one takes over).\n"
                        f'exec {target} "$@"\n', encoding="utf-8")  # fmt: skip
        link.chmod(0o755)
    except OSError:
        return False
    return True


def unblock_launchers() -> None:
    """A ZIP downloaded from the internet (a browser, Teams, Outlook) marks what it unzips: Windows asks "Do you want
    to run this file?" on every double-click of APProcessor.bat (and of the shortcut to it), and a Mac keeps the
    .command in Gatekeeper's quarantine. AP Coder was started from this folder on purpose: the mark is taken off its
    own launchers (nothing else). Never a reason to stop."""
    if WINDOWS:
        for launcher in [*ROOT.glob("*.bat"), *ROOT.glob("*.cmd"), *(ROOT / "scripts").glob("*.bat")]:
            try:
                os.remove(f"{launcher}:Zone.Identifier")  # the Mark of the Web: an NTFS stream of the file
            except OSError:
                pass
    elif MAC:
        found = [str(p) for p in [*ROOT.glob("*.command"), *ROOT.glob("*.sh")]]
        if found:
            try:
                subprocess.run(["xattr", "-d", "com.apple.quarantine", *found], capture_output=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                pass


def shortcut_target() -> Path:
    return ROOT / ("APProcessor.bat" if WINDOWS else "APProcessor.command")


def ensure_shortcut() -> str:
    """One "AP Coder" shortcut on the desktop (Windows; a Mac gets an "AP Coder.command" launcher) that always opens
    the current install: (re)written when this install became the current one, or when an earlier try failed (then
    it is tried again on the next start). One the person deleted, or declined (--installer --no-shortcut), is not
    put back while the current install stays the same. Returns a summary line ("" = nothing to say)."""
    if not (WINDOWS or MAC):
        return ""
    settings = paths.read_user_settings()
    if settings.get("desktop_shortcut") is False:
        return ""
    target = shortcut_target()
    recorded = settings.get("shortcut_target")
    if paths.same_folder(recorded, target):
        return ""  # made for this install already
    folder = desktop()
    made = folder is not None and (write_shortcut(folder) if WINDOWS else write_mac_launcher(folder / MAC_LAUNCHER))
    if not made:
        name = SHORTCUT.removesuffix(".lnk") if WINDOWS else MAC_LAUNCHER
        return f"Could not put a shortcut ({name}) on the desktop (tried again next time); start AP Coder with {target}"
    paths.write_user_settings(desktop_shortcut=True, shortcut_target=str(target))
    if recorded or settings.get("desktop_shortcut"):
        return f"Desktop shortcut 'AP Coder' now opens this version ({ROOT})"
    return "Desktop shortcut 'AP Coder' made"


def old_shortcut_folder() -> str:
    """The folder an AP Coder 0.1 desktop shortcut opens (it recorded only that there is one): an older install."""
    settings = paths.read_user_settings()
    if not WINDOWS or not settings.get("desktop_shortcut") or settings.get("shortcut_target"):
        return ""
    folder = desktop()
    if folder is None or not (folder / SHORTCUT).exists():
        return ""
    link = str(folder / SHORTCUT).replace("'", "''")
    target = running.powershell(f"(New-Object -ComObject WScript.Shell).CreateShortcut('{link}').TargetPath", 60)
    return str(Path(target).parent) if target else ""


CLOUD_NAMES = {"onedrive": "OneDrive", "dropbox": "Dropbox", "googledrive": "Google Drive", "box": "Box"}


def synced_by(folder: Path) -> str:
    """The service that syncs ``folder`` ("OneDrive", "iCloud Drive", ...; "" when none is known): its files can be
    kept online-only, and with no internet they can't be read."""

    def inside(parent: str | Path) -> bool:
        try:
            return Path(os.path.normcase(str(folder))).is_relative_to(os.path.normcase(str(parent)))
        except (OSError, ValueError):
            return False

    if WINDOWS:
        roots = [os.environ.get(v) for v in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive")]
        return "OneDrive" if any(root and inside(root) for root in roots) else ""
    if MAC:
        library = Path.home() / "Library"
        if inside(library / "Mobile Documents"):
            return "iCloud Drive"
        if inside(library / "CloudStorage"):
            provider = folder.relative_to(library / "CloudStorage").parts[0].split("-")[0]
            return CLOUD_NAMES.get(provider.lower(), provider)
    return ""


def sync_notes(data: Path) -> list[tuple[str, str]]:
    """A warning for AP Coder's folder, and for its data folder, when a cloud service syncs it."""
    notes = []
    place = "C:\\APCoder\\app" if WINDOWS else "a folder in your home folder (e.g. ~/APCoder/app)"
    service = synced_by(ROOT)
    if service:
        notes.append(("warn", f"AP Coder's folder is synced by {service} ({ROOT}): a file it keeps online-only can't "
                              f"be read without the internet. Unzip AP Coder to a folder that is not synced, e.g. "
                              f"{place}"))  # fmt: skip
    service = synced_by(data)
    if service:
        notes.append(("warn", f"The data folder is synced by {service} ({data}): the database can be damaged while "
                              "it is synced, and an online-only invoice can't be opened offline. Choose a data folder "
                              f"that is not synced ({INSTALLER} asks for one)"))  # fmt: skip
    return notes


def lm_studio_line(status: Any = None) -> tuple[str, str]:
    """The chat model AP Coder would use (it suggests GL accounts for lines nothing was learned for), as LM Studio
    reports it: loaded, or only downloaded (LM Studio then loads it on first use). Optional: never installs, starts or
    loads anything. ``status``: LM Studio's answer already asked for this start (else it is asked now)."""
    status = status if status is not None else chat_status()
    if status is None:
        return "info", "Chat model: not checked (optional)"
    if not status.reachable:
        return "info", ("LM Studio: not running (optional: invoices are still read and coded without it; start its "
                        "server before AP Coder to use it)")  # fmt: skip
    if not status.active:
        return "warn", f"{status.server}: running, but no chat model is loaded (optional: load Gemma 4 12B to use it)"
    sees = "can see pages" if status.vision else "reads text only"
    if status.lm_studio and status.model not in status.loaded:
        return "info", (f"Chat model: {status.model} is downloaded but not loaded: LM Studio loads it the first time "
                        "it is needed (load it now for a quick first invoice)")  # fmt: skip
    from ap_coder.ai.local_llm import is_chat_model

    also = [m for m in status.loaded if m != status.model and is_chat_model(m)]  # (not OvisOCR2, the page reader)
    keep = "keep one chat model loaded: unload the one you don't want"
    more = f"; also loaded: {', '.join(also)} ({keep})" if also else ""
    return "ok", f"Chat model: {status.model} loaded in {status.server} ({sees}){more}"


def chat_status() -> Any:
    """LM Studio's answer about the chat model (``local_llm.ModelStatus``), or None when it can't be asked."""
    try:
        from ap_coder.ai import local_llm
        from ap_coder.core.config import Settings

        return local_llm.check_server(Settings.from_env().llm, timeout=1.0, use_cache=False)
    except Exception:  # noqa: BLE001 - the summary never stops the start
        return None


# How long the start waits for the chat model's answer to its test question (a loaded 12B model answers in seconds; a
# model LM Studio is still loading can take longer: the start goes on and says so).
CHAT_TEST_TIMEOUT = 90.0


def chat_test_line(state: dict, force: bool = False, status: Any = None) -> tuple[str, str] | None:
    """The question ``doctor --online`` asks, asked by the double-click: the loaded chat model gets two made-up lines
    and must answer with accounts from the chart. Asked once per model and version of AP Coder (remembered in the
    launcher's state), again after it failed or with --check; never when no chat model is loaded (that would make LM
    Studio load one). None when there is nothing to ask. ``status`` as for ``lm_studio_line``."""
    status = status if status is not None else chat_status()
    if status is None or not status.active or (status.lm_studio and status.model not in status.loaded):
        return None
    key = f"{status.model}|{identity()['version']}"
    last = state.get("chat_test") or {}
    if not force and last.get("key") == key and last.get("ok"):
        return "ok", f"Chat model test passed ({last.get('at', '?')}): it picks accounts from your chart"
    say(f"Asking {status.model} a test question (two made-up invoice lines; a few seconds)...")
    level, text = _ask_chat_model()
    state["chat_test"] = {"key": key, "ok": level == "ok", "at": time.strftime("%Y-%m-%d %H:%M")}
    return level, text


def _ask_chat_model() -> tuple[str, str]:
    """The doctor's local dry run, with a time limit: (level, line)."""
    result: list[tuple[str, str, str]] = []

    def ask() -> None:
        try:
            from ap_coder.core.config import Settings
            from ap_coder.ops import doctor
            from ap_coder.ops.cli import _load_reference, build_parser

            reference = _load_reference(build_parser().parse_args(["doctor"]))
            result.append(doctor._local_dry_run(Settings.from_env(), reference))
        except Exception as exc:  # noqa: BLE001 - reported, never stops the start
            result.append(("local dry run", "WARN", f"not asked ({type(exc).__name__}: {exc})"))

    worker = threading.Thread(target=ask, name="chat-model-test", daemon=True)
    worker.start()
    worker.join(CHAT_TEST_TIMEOUT)
    if not result:
        return "warn", (f"Chat model test: no answer within {CHAT_TEST_TIMEOUT:.0f} s (is LM Studio still loading "
                        "it?). AP Coder starts anyway; the next start asks again")  # fmt: skip
    _, status, detail = result[0]
    if status == "PASS":
        return "ok", f"Chat model test passed: {detail}"
    return "warn", f"Chat model test: {detail}. AP Coder starts anyway; the next start asks again"


# The doctor's checks the summary already has a line of its own for (or that never need saying at a start).
COVERED_CHECKS = ("python", "package ", "OCR", "table finder", "data folder", "local model", "page reader",
                  "engine", "prompt size", "AI model")  # fmt: skip


def setup_check_lines() -> list[tuple[str, str]]:
    """The rest of ``python -m ap_coder doctor`` (offline): the GL accounts, cost centers, tax mapping, coding rules
    and settings. One line when all is well, else one line for each problem, with the doctor's own words."""
    try:
        from ap_coder.core.config import Settings
        from ap_coder.ops.cli import _load_reference, build_parser
        from ap_coder.ops.doctor import FAIL, WARN, run_checks

        args = build_parser().parse_args(["doctor"])
        checks = run_checks(Settings.from_env(), lambda: _load_reference(args), online=False, llm_checks=False)
    except Exception as exc:  # noqa: BLE001 - the summary never stops the start
        return [("warn", f"Setup checks: not run ({type(exc).__name__}: {exc})")]
    shown = [c for c in checks if not c.area.startswith(COVERED_CHECKS)]
    problems = [("fail" if c.status == FAIL else "warn", f"{c.area}: {c.detail}") for c in shown
                if c.status in (FAIL, WARN)]  # fmt: skip
    if problems:
        return problems
    passed = sum(1 for c in shown if c.status == "PASS")
    return [("ok", f"Setup checks: all {passed} passed (GL accounts, tax mapping, coding rules, settings)")]


def page_reader_line() -> tuple[str, str]:
    """The page reader (OvisOCR2 in LM Studio): found or not, and its self-test. Never downloads or loads a model."""
    try:
        from ap_coder.ai.page_reader import reader_status
        from ap_coder.ai.page_worker import self_test_detail
        from ap_coder.core.config import Settings

        status = reader_status(Settings.from_env(), use_cache=False)
        state, _detail = self_test_detail(status.model) if status.usable else ("no model", "")
    except Exception as exc:  # noqa: BLE001 - the summary never stops the start
        return "info", f"Page reader: not checked ({type(exc).__name__})"
    if not status.usable:
        return "warn", ("Page reader: OvisOCR2 isn't running in LM Studio: invoices are read by OCR only and wait "
                        "for a person")  # fmt: skip
    if state == "passed":
        return "ok", f"Page reader: {status.model} found, self-test passed (reads every page of every invoice)"
    if state == "failed":
        return "warn", f"Page reader: {status.model} found, but it failed its self-test (Settings > Reading)"
    return "info", f"Page reader: {status.model} found; its self-test runs on its own before it reads invoices"


def readiness(packages: tuple[str, str], state: dict | None = None, force: bool = False) -> list[tuple[str, str]]:
    """The lines of the readiness summary: (level, text). Everything ``doctor --online`` checks, on every start: the
    packages, OCR, data folder, the chat model (and, with ``state``, its test question) and the page reader in LM
    Studio, and the GL accounts, tax mapping and settings."""
    from ap_coder.core import paths
    from ap_coder.ops import offline

    lines = [("ok", f"Python {sys.version.split()[0]} (.venv)"), packages]
    v4, v5 = offline.ppocrv4_installed(), offline.ppocrv5_installed()
    if v5 and not offline.missing_models():
        lines.append(("ok", "OCR for scanned invoices"))
    elif v4 or v5:
        lines.append(("ok", "OCR for scanned invoices (one engine: the PP-OCRv5 models come from the offline "
                            "bundle's models folder)"))  # fmt: skip
    else:
        lines.append(("warn", "OCR not installed: scanned invoices can't be read (text PDFs are fine)"))
    data = paths.private_dir()
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".ap_coder_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        lines.append(("ok", f"Data folder: {data}"))
    except OSError as exc:
        lines.append(("fail", f"Data folder {data} can't be written ({exc.strerror or exc})"))
    chat = chat_status()  # LM Studio asked once for both chat-model lines
    lines.append(lm_studio_line(chat))
    if state is not None:
        test = chat_test_line(state, force, chat)
        if test:
            lines.append(test)
    lines.append(page_reader_line())
    lines += setup_check_lines()
    return lines


def self_check() -> tuple[bool, str]:
    """``scripts/pilot_check.py`` (about 10 s): the sample invoices read right, with no AI model and no Azure."""
    say("Running the self-check (reads the sample invoices; about 10 seconds)...")
    started = time.monotonic()
    try:
        out = subprocess.run([sys.executable, str(SCRIPTS / "pilot_check.py")], cwd=ROOT, capture_output=True,
                             encoding="utf-8", errors="replace", timeout=SELF_CHECK_TIMEOUT,
                             env=child_env())  # fmt: skip
    except subprocess.TimeoutExpired:
        return False, "Self-check FAILED: it did not finish in 10 minutes"
    text = out.stdout.splitlines()
    seconds = round(time.monotonic() - started)
    summary = next((t for t in text if "sample invoices read right" in t), "")
    count = summary.split(" sample invoices")[0] if summary else "?"
    if out.returncode == 0:
        if any(t.startswith("SKIP") and "scan" in t for t in text):  # no OCR add-on: said in the summary already
            return True, f"Self-check OK: {count} sample invoices read right; no scan tried (no OCR) ({seconds} s)"
        return True, f"Self-check OK: {count} sample invoices read right, and a scan with local OCR ({seconds} s)"
    wrong = [t.strip() for t in text if t.startswith("FAIL")] or (out.stderr.strip().splitlines() or ["?"])[-1:]
    return False, f"Self-check FAILED: {'; '.join(wrong[:3])}"


def print_summary(lines: list[tuple[str, str]]) -> None:
    say()
    say("AP Coder readiness:")
    for level, text in lines:
        line(level, text)
    say()


# --- Already running? -------------------------------------------------------------------------------------------


def requested_port(rest: list[str]) -> int | None:
    for i, arg in enumerate(rest):
        if arg == "--port" and i + 1 < len(rest) and rest[i + 1].isdigit():
            return int(rest[i + 1])
        if arg.startswith("--port=") and arg[7:].isdigit():
            return int(arg[7:])
    return None


def candidate_ports(rest: list[str], state: dict) -> list[int]:
    """Where an AP Coder may already run: the port asked for, the one this folder's last start used, the ones the
    other installs' last starts used, and the default and the next ones (the server file in the data folder names its
    own; AP Coder 0.1 recorded none, and took the first free port from 8501: a second window's was 8502)."""
    ports = [requested_port(rest), state.get("port")]
    for entry in paths.installs():
        try:
            other = json.loads((Path(entry["folder"]) / ".venv" / STATE.name).read_text(encoding="utf-8"))
            ports.append(other.get("port"))
        except (OSError, ValueError, AttributeError):
            continue
    defaults = range(DEFAULT_PORT, DEFAULT_PORT + 10)
    return list(dict.fromkeys(p for p in [*ports, *defaults] if isinstance(p, int) and p > 0))


def running_dashboard(rest: list[str], state: dict) -> int | None:
    """The port of this folder's own AP Coder (this version), already running here, else None."""
    version = identity()["version"]
    for server in running.find(candidate_ports(rest, state), paths.private_dir()):
        if running.judge(server, ROOT, version) == "same":
            return server.port
    return None


def dashboard_url(port: int) -> str:
    """The address the browser opens: the one the dashboard listens on (127.0.0.1 by default). Never "localhost": a
    Mac or Windows browser tries it as ::1 first, where another program may answer."""
    from ap_coder.ops.offline import dashboard_address

    address = dashboard_address()
    shown = "127.0.0.1" if address in ("", "0.0.0.0", "::", "localhost") else address
    return f"http://{f'[{shown}]' if ':' in shown else shown}:{port}"


def open_running(port: int) -> int:
    url = dashboard_url(port)
    say(f"AP Coder is already running: opening {url} in your browser.")
    say("Nothing new was started. This window can be closed; the AP Coder window already open runs it.")
    open_browser(url)
    return 0


# --- One start at a time ----------------------------------------------------------------------------------------

_held: list[Any] = []  # the open lock file: the lock lasts as long as this process


def take_lock() -> bool:
    """An exclusive lock on ``LOCK`` for this start (setup and the dashboard), released when this process ends.
    False when another start holds it. True also when no lock file can be made here (a read-only folder: then
    there is no .venv to set up either), or the folder's file system has no locks (a NAS or network volume)."""
    if _held:
        return True
    try:
        handle = open(LOCK, "a+b")  # kept open on purpose: the lock lasts as long as the file
    except OSError:
        return True
    try:
        if os.name == "nt":  # the operating system's own lock (not WINDOWS, which tests set to try either path)
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        return not upgrade.lock_busy(exc)
    _held.append(handle)
    return True


def release_lock() -> None:
    while _held:
        _held.pop().close()  # closing the file releases the lock (msvcrt and flock alike)


def wait_for_other(argv: list[str], timeout: float = WAIT_FOR_OTHER, every: float = 2.0) -> int | None:
    """Another start holds the lock (a first setup, or the running dashboard): wait for its dashboard and open
    it. None when that start ended without one: this start then holds the lock and carries on."""
    port = running_dashboard(argv, load_state())
    if port:
        return open_running(port)
    blocked = stop_older(argv)
    if blocked is not None:
        return blocked
    if take_lock():
        return None
    say("AP Coder is being set up or started in another window: waiting for it to open the dashboard...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(every)
        port = running_dashboard(argv, load_state())
        if port:
            return open_running(port)
        if take_lock():
            return None
    say("The other AP Coder window has not opened the dashboard yet: look at that window, or close it and start "
        "AP Coder again.")  # fmt: skip
    return 1


def stop_older(argv: list[str], wait: float = 30.0) -> int | None:
    """The start holding this folder's lock serves an older AP Coder (this folder unzipped over while it ran: its
    dashboard is never this version's, so waiting for one would last half an hour): it is stopped like any older one,
    and its start then ends, freeing the lock. Returns an exit code when this start must stop, else None."""
    version = identity()["version"]
    ports = candidate_ports(argv, load_state())
    if not any(running.judge(s, ROOT, version) == "older" for s in running.find(ports, paths.private_dir())):
        return None
    verdict, port = running.take_over(ROOT, version, ports, paths.private_dir(), say)
    if verdict == "blocked":
        return failed(SetupError(f"An older AP Coder is still running (port {port}): two versions never use the same "
                                 "data at once.", "Close the old AP Coder window, then start again."))  # fmt: skip
    if verdict in ("open", "newer"):
        return open_running(port)
    deadline = time.monotonic() + wait  # its start ends once its dashboard stopped
    while not take_lock() and time.monotonic() < deadline:
        time.sleep(0.5)
    return None


def free_port(start: int = DEFAULT_PORT) -> int:
    """The first port from ``start`` the dashboard can listen on and nothing answers on yet
    (``ap_coder/ops/offline.py:free_port``, which ``python -m ap_coder serve`` uses too)."""
    from ap_coder.ops.offline import dashboard_address
    from ap_coder.ops.offline import free_port as first_free

    return first_free(start, dashboard_address(), windows=WINDOWS, answers=running.listening)


def open_browser(url: str) -> None:
    """``url`` in the browser, unless BROWSER=none or there is no desktop here (``offline.browser_wanted``)."""
    if not browser_wanted():
        if (os.environ.get("BROWSER") or "").strip().lower() != "none":
            say(f"No desktop on this computer, so no browser was opened: open {url} in a browser here.")
        return
    import webbrowser

    try:
        opened = webbrowser.open(url)
    except Exception:  # noqa: BLE001 - no browser here: the address is said instead
        opened = False
    if not opened:
        say(f"Could not open a web browser here: open {url} in your browser yourself.")


# --- Start --------------------------------------------------------------------------------------------------------


def without_port(rest: list[str]) -> list[str]:
    out, skip = [], False
    for i, arg in enumerate(rest):
        if skip:
            skip = False
        elif arg == "--port" and i + 1 < len(rest):
            skip = True
        elif not arg.startswith("--port="):
            out.append(arg)
    return out


def server_command(port: int, rest: list[str]) -> list[str]:
    """AP Coder's server on ``port``, with the options given (e.g. ``--address``); the launcher opens the browser."""
    return [sys.executable, "-m", "ap_coder", "serve", "--port", str(port), "--no-browser", *without_port(rest)]


def start_dashboard(rest: list[str], state: dict) -> int:
    """Start the server on the port asked for, else the first free one from 8501 (recorded, so the next
    double-click finds this one running); its output is shown as it comes, and the browser opens once it is up
    (its "AP Coder is running at" line, or an answer on /healthz). Returns when it stops."""
    port = requested_port(rest) or free_port()
    if port != DEFAULT_PORT and not requested_port(rest):
        say(f"Port {DEFAULT_PORT} is in use by another program: AP Coder uses port {port} ({dashboard_url(port)}).")
    state["port"] = port
    save_state(state)
    say("Starting AP Coder: it opens in your browser. Keep this window open while you use it; close it to stop.")
    try:
        proc = subprocess.Popen(server_command(port, rest), cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding="utf-8", errors="replace", bufsize=1, env=child_env())  # fmt: skip
    except OSError as exc:
        return failed(SetupError(f"AP Coder could not start ({exc}).", "Start AP Coder again; if it happens again, "
                                 f"run  python -m ap_coder doctor  {IN_TERMINAL}."))  # fmt: skip
    ready = threading.Event()

    def show_output() -> None:
        assert proc.stdout is not None
        for text in proc.stdout:
            sys.stdout.write(text)
            sys.stdout.flush()
            log(text)
            if READY in text:
                ready.set()

    shown = threading.Thread(target=show_output, daemon=True)
    shown.start()
    opened = False
    try:
        while proc.poll() is None:
            if opened:
                proc.wait()
            elif ready.wait(0.5) or running.health(port, timeout=1.0):
                open_browser(dashboard_url(port))
                opened = True
        shown.join(timeout=5)
        return proc.returncode
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


def previous_install(state: dict, ident: dict, legacy: list[str]) -> dict | None:
    """The AP Coder this start updates (None when it is no update): this folder's own, when its version or packages
    changed since .venv was set up here (unzipped over it); else, in a new folder, the newest older install."""
    if state.get("fingerprint"):
        same = (state["fingerprint"] == ident["fingerprint"] and state.get("version", ident["version"]) ==
                ident["version"] and state.get("build", ident["build"]) == ident["build"])  # fmt: skip
        if same:
            return None
        return {"version": state.get("version") or registered_version(), "build": state.get("build", "")}
    older = [i for i in upgrade.previous_versions(ROOT, legacy) if paths.is_older(i, ident)]
    if older:
        return older[-1]
    # Unzipped over an AP Coder from before this launcher (its install.bat, or by hand): only its .venv says which.
    registered = registered_version()
    if registered and paths.version_key(registered) < paths.version_key(ident["version"]):
        return {"version": registered, "build": ""}
    return None


def registered_version() -> str:
    """The version .venv registered for ap_coder when it was installed (an AP Coder 0.1 launcher kept no other)."""
    try:
        return importlib.metadata.version("ap-coder")
    except Exception:  # noqa: BLE001 - only for the message
        return ""


def legacy_folders() -> list[str]:
    """Older installs that kept no record (AP Coder 0.1): its desktop shortcut's folder, a stopped old dashboard's, and
    the older ones next to this folder (closed before the update)."""
    return [str(f) for f in [old_shortcut_folder(), *running.stopped, *upgrade.nearby_installs(ROOT)] if f]


def older_installs(ident: dict, legacy: list[str]) -> list[Path]:
    """Every other install this one replaces (on record, or AP Coder 0.1's): never one that is newer."""
    folders = [Path(e["folder"]) for e in paths.installs() if not paths.same_folder(e["folder"], ROOT)]
    folders += upgrade.legacy_installs(ROOT, legacy)
    found: list[Path] = []
    for folder in folders:
        if any(paths.same_folder(folder, f) for f in found):
            continue
        if upgrade.is_install(folder) and paths.is_older(ident, paths.install_identity(folder)):
            continue
        found.append(folder)
    return found


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def run(args: argparse.Namespace, rest: list[str]) -> int:
    state = load_state()
    ident = identity()
    first = args.venv in ("created", "recreated") or not state.get("setup_done")
    if args.venv in (None, "reused") and (first or state.get("fingerprint") != ident["fingerprint"]):
        say(f"Python {sys.version.split()[0]}: reusing .venv")  # (a later start of the same version: said nothing)
    unblock_launchers()

    # An AP Coder already running: this one opened; an older one (or another folder's) stopped first.
    verdict, port = running.take_over(ROOT, ident["version"], candidate_ports(rest, state), paths.private_dir(), say)
    if verdict == "open":
        return open_running(port)
    if verdict == "newer":
        say("A newer AP Coder is already running: opening it (this older version starts nothing).")
        return open_running(port)
    if verdict == "blocked":
        return failed(SetupError(f"An older AP Coder is still running (port {port}): two versions never use the same "
                                 "data at once.", "Close the old AP Coder window, then start again."))  # fmt: skip

    legacy = legacy_folders()  # (before the shortcut is pointed here: an AP Coder 0.1 shortcut names its folder)
    previous = previous_install(state, ident, legacy)
    upgrade.remember(upgrade.legacy_installs(ROOT, legacy))  # (what found them changes below: kept on record)
    if previous:
        exact = paths.describe(previous) == paths.describe(ident)  # the same version, rebuilt the same day
        say(f"Updated AP Coder from {paths.describe(previous, exact)} to {paths.describe(ident, exact)}.")
    removed, freed = upgrade.prune_stale(ROOT)
    if removed:
        say(f"Removed {plural(len(removed), 'file')} the older version left in this folder "
            f"({upgrade.shown_size(freed)}).")  # fmt: skip
    try:
        level, packages, installed = ensure_packages(state)
    except SetupError as exc:
        save_state(state)
        return failed(exc)
    if str(ROOT) not in sys.path:  # ap_coder itself, even when it was only just installed
        sys.path.insert(0, str(ROOT))
    try:
        data = ensure_data_folder()
    except upgrade.DataMoveError as exc:  # never started on half the data: the next start finishes the move
        save_state(state)
        return failed(SetupError(str(exc), exc.hint))
    notes = settle_settings(data, ident["version"])
    too_new = database_too_new(data, ident["version"])
    if too_new:
        save_state(state)
        return failed(too_new)
    ensure_models(first, state)
    paths.record_install(ident)
    upgrade.write_marker(ROOT, ident)
    shortcut = ensure_shortcut()
    lines = readiness((level, packages), state, force=args.check) + notes + sync_notes(data)
    if shortcut:
        lines.append(("ok" if shortcut.startswith("Desktop") else "warn", shortcut))
    old = older_installs(ident, legacy)
    unchecked = bool(old) and not state.get("self_check")  # (set up by --installer: checked before the clean-up)
    if first or installed or args.check or previous or unchecked:
        ok, result = self_check()
        state["self_check"] = {"ok": ok, "at": time.strftime("%Y-%m-%d %H:%M"), "result": result}
        lines.append(("ok" if ok else "fail", result))
        if not ok:
            lines.append(("info", "AP Coder still starts. For the details run  python scripts/pilot_check.py  "
                                  f"{IN_TERMINAL}, and send its output to whoever set up the pilot."))  # fmt: skip
    elif state.get("self_check"):
        last = state["self_check"]
        word = "OK" if last.get("ok") else "FAILED"
        lines.append(("ok" if last.get("ok") else "warn",
                      f"Self-check {word} at setup ({last.get('at', '?')}); --check runs it again"))  # fmt: skip
    if old and (state.get("self_check") or {}).get("ok"):
        say("Cleaning up the older AP Coder:")
        freed = upgrade.clean_up(ROOT, data, old, say)
        if freed:
            lines.append(("ok", f"Cleaned up the older AP Coder: {upgrade.shown_size(freed)} freed"))
        lines += retire_settings(data, ident["version"])  # (a settings file it moved in)
    elif old:
        kept = ", ".join(str(f) for f in old)
        lines.append(("warn", f"The older AP Coder is kept in {kept} until the self-check passes"))
    state.update(setup_done=True, fingerprint=ident["fingerprint"], version=ident["version"], build=ident["build"])
    save_state(state)
    print_summary(lines)
    if args.no_start:
        return 0
    return start_dashboard(rest, state)


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="APProcessor.bat" if WINDOWS else "./APProcessor.command",
        description="Set up what is missing, check it, and start AP Coder.",
        epilog="The installer's own options (with --installer): --yes, --no-update, --skip-tests, --full-tests, "
        "--no-start, --fresh-start, --data-dir PATH, --no-shortcut, --reinstall.",
    )
    parser.add_argument("--installer", action="store_true", help="the installer's questions too")
    parser.add_argument("--yes", action="store_true", help="no questions (automated runs)")
    parser.add_argument("--check", action="store_true", help="run the self-check again")
    parser.add_argument("--no-start", action="store_true", help="set up and check, but don't start the dashboard")
    parser.add_argument("--venv", help=argparse.SUPPRESS)  # what the first phase did with .venv
    parser.add_argument("--venv-ok", action="store_true", help=argparse.SUPPRESS)  # the double-click's quick check
    return parser.parse_known_args(argv)


ZIP_VIEW = re.compile(r"Temp\d+_.+\.zip", re.IGNORECASE)  # where Explorer unpacks a ZIP opened without Extract All


def inside_zip_view() -> bool:
    """Started from Explorer's view of the ZIP: Windows unpacked it to a temporary folder (``%TEMP%\\Temp1_<name>.zip``)
    that it deletes later, with the .venv and the desktop shortcut pointing into it."""
    return WINDOWS and any(ZIP_VIEW.fullmatch(part) for part in ROOT.parts)


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - said plainly, the details in the log
        return unexpected(exc)
    finally:
        close_log()


def _main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    setup_console()
    args, rest = parse(argv)  # first: what to do when another start holds the lock depends on it
    if args.venv_ok:  # APProcessor.bat / .command: may .venv be used as it is?
        return 0 if importlib.util.find_spec("pip") is not None and venv_fits() else 1
    if inside_zip_view():
        return failed(SetupError(f"AP Coder was started from inside its ZIP: Windows unpacked it to a temporary folder "
                                 f"({ROOT}) that it deletes later.", "Close this window, right-click the ZIP, choose "
                                 "Extract All (e.g. to C:\\APCoder), then double-click APProcessor.bat in the "
                                 "extracted folder."))  # fmt: skip
    if not os.environ.get(LOCK_HELD_ENV):  # (set: a parent start holds it, e.g. the first phase for the second)
        if not take_lock():  # another start is setting up or running AP Coder
            if args.installer:
                # The installer may reinstall packages into the .venv the running AP Coder uses: never under it.
                line("fail", "AP Coder is open (or being set up) in another window: close AP Coder first, then run "
                             f"{INSTALLER} again. Nothing was installed.")  # fmt: skip
                return 1
            code = wait_for_other(argv)
            if code is not None:
                return code
        os.environ[LOCK_HELD_ENV] = str(os.getpid())  # for what this start runs: never waits for itself
    if args.venv is None:  # the first phase: an older folder started by mistake opens the newest instead
        newer = upgrade.newer_install(ROOT)
        if newer is not None:
            say(f"A newer AP Coder ({paths.describe(newer)}) is in {newer['folder']}: opening that one.")
            upgrade.remember([ROOT])  # (an older bundle unzipped again: on record, so the newest cleans it up)
            return upgrade.hand_over(Path(newer["folder"]), argv, LOCK_HELD_ENV)
    if not in_venv():
        return bootstrap(argv)
    if args.installer:
        import install

        forwarded = [a for a in argv if a != "--installer" and not a.startswith("--venv")]
        return install.main(forwarded, venv=args.venv)
    return run(args, rest)


if __name__ == "__main__":
    sys.exit(main())
