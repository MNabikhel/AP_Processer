"""AP Coder's one-button setup and start: what APProcessor.bat (Windows) and APProcessor.command (Mac) run.

The double-click finds a Python (3.11 to 3.13) and runs this file with it. It then:

1. reuses ``.venv`` (it creates it only the first time, and again only when it is broken);
2. installs only the packages that are missing or too old (``scripts/check_deps.py``), from the offline
   bundle's ``wheelhouse/`` folder when there is one;
3. picks the data folder and fetches the OCR models, only when they are not there yet;
4. puts one "AP Coder" shortcut on the Windows desktop, once;
5. prints a short readiness summary (and, the first time, the result of the self-check,
   ``scripts/pilot_check.py``);
6. opens the dashboard: the one already running if there is one, else it starts it.

It never installs Python itself (APProcessor.bat offers that when no Python is found) and never installs
LM Studio: it only says whether a model is loaded. Safe to run again at any time.

``--installer`` runs the installer's extra steps instead (``scripts/install.py``: update, data folder and
Azure questions, self-test); ``install.bat`` / ``install.sh`` pass it. Other options: ``--check`` (run the
self-check again), ``--no-start``, ``--yes``. Anything else (e.g. ``--port 8502``) goes to the dashboard.

Standard library only until the packages are installed: the first run starts with no packages at all.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_deps  # noqa: E402  (standard library plus pip's packaging)

WINDOWS = os.name == "nt"
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")
STATE = VENV / "ap_coder_launcher.json"  # what this .venv has been through (gone with it when it is recreated)
SUPPORTED = ((3, 11), (3, 13))  # the Python versions APProcessor looks for (every package has wheels for them)
DEFAULT_PORT = 8501
SIGNATURE = "/app/static/InterVariable.woff2"  # a file only AP Coder's dashboard serves
SHORTCUT = "AP Coder.lnk"
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
OCR_PACKAGES = {"rapidocr", "onnxruntime", "rapidocr-onnxruntime"}


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


def line(level: str, message: str) -> None:
    say(f"  {mark(level)} {message}")


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace", line_buffering=True)


# --- State ------------------------------------------------------------------------------------------------------


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # only a record: the next start checks again


def fingerprint() -> str:
    return hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()[:16]


# --- Phase 1: the virtual environment (any Python) ------------------------------------------------------------


def in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


def supported(version: tuple[int, int]) -> bool:
    return SUPPORTED[0] <= version <= SUPPORTED[1]


def venv_works() -> bool:
    """``.venv``'s Python starts and has pip: anything less is a broken environment."""
    if not VENV_PY.exists():
        return False
    try:
        probe = subprocess.run([str(VENV_PY), "-c", "import encodings, pip"], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def ensure_venv(python: str = sys.executable) -> str:
    """``reused``, ``created`` or ``recreated`` (only when the existing one is broken)."""
    if venv_works():
        return "reused"
    status = "recreated" if VENV.exists() else "created"
    if status == "recreated":
        say("The .venv folder is broken (its Python does not start): making a new one.")
        shutil.rmtree(VENV, ignore_errors=True)
        if VENV.exists():
            raise SetupError(
                "Could not remove the broken .venv folder.",
                "Close every AP Coder window, delete the .venv folder, then start AP Coder again.",
            )
    result = subprocess.run([python, "-m", "venv", str(VENV)])
    if result.returncode != 0 or not venv_works():
        raise SetupError(
            "Could not create the .venv folder (AP Coder's own Python environment).",
            "Reinstall Python 3.12 from https://www.python.org/downloads/, then start AP Coder again.",
        )
    return status


def bootstrap(argv: list[str]) -> int:
    """Run with a Python found on this computer: make ``.venv``, then carry on inside it."""
    version = sys.version_info[:2]
    if not supported(version) and not VENV_PY.exists():
        say(f"Note: Python {version[0]}.{version[1]} is not one AP Coder is tested with (3.11 to 3.13).")
    if not VENV_PY.exists():
        say("First run: setting up AP Coder. This takes a few minutes; later starts take seconds.")
    say(f"Python {sys.version.split()[0]} found on this computer: {sys.executable}")
    try:
        status = ensure_venv()
    except SetupError as exc:
        return failed(exc)
    try:
        return subprocess.call([str(VENV_PY), str(Path(__file__).resolve()), *argv, f"--venv={status}"])
    except KeyboardInterrupt:
        return 0


def failed(exc: SetupError) -> int:
    say()
    line("fail", str(exc))
    say(f"  What to do: {exc.hint}")
    return 1


# --- Phase 2: packages, data, checks (inside .venv) -------------------------------------------------------------


def pip_source() -> list[str]:
    """pip options that install from the offline bundle's wheelhouse/ folder, when there is one."""
    wheelhouse = ROOT / "wheelhouse"
    return ["--no-index", "--find-links", str(wheelhouse)] if wheelhouse.is_dir() else []


def pip_install(args: list[str]) -> bool:
    """``pip install`` with its progress lines (not the long "already satisfied" list)."""
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off",
           *pip_source(), *args]  # fmt: skip
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace")  # fmt: skip
    assert proc.stdout is not None
    for out in proc.stdout:
        text = out.rstrip()
        if text and not text.startswith(QUIET_PIP_LINES):
            say(f"    {text}")
    return proc.wait() == 0


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


def ensure_packages(state: dict, extras: tuple[str, ...] = ("ocr",)) -> tuple[str, str, bool]:
    """Install only what is missing or too old: nothing when everything is in place.
    Returns (level, summary line, whether pip installed something)."""
    total = len(check_deps.requirements(extras=extras))
    missing, self_problem = missing_packages(extras)
    if not missing and not self_problem:
        return "ok", f"Packages: all {total} in place", False
    if not self_problem and all(is_ocr(r) for r in missing) and state.get("ocr_failed") == fingerprint():
        return "warn", "Packages: in place, without the OCR add-on (it could not be installed here)", False

    if pip_source():
        say("Installing from the wheelhouse folder (no internet needed).")
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
    if self_left or [r for r in left if not is_ocr(r)]:
        raise SetupError(
            "Installing the packages failed (the messages above say why).",
            "Check the internet connection (behind a company proxy: set HTTPS_PROXY=http://proxy:port in this "
            "window), close any other AP Coder window, then start AP Coder again.",
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
    """The data folder: the one already recorded, else ``~/APCoder`` (recorded once, never moved)."""
    import first_run

    data = first_run.choose_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / "invoices").mkdir(exist_ok=True)
    return data


def ensure_models(first: bool) -> None:
    """The PP-OCRv5 OCR models, fetched only when missing (checksums checked on the first run)."""
    from ap_coder import offline

    if offline.ppocrv5_installed() and offline.missing_models(verify=first):
        import fetch_models

        say("Fetching the OCR models (once).")
        if fetch_models.main([]) != 0:
            say("(Not needed to start: scans are read with the other OCR engine.)")


def desktop() -> Path | None:
    if not WINDOWS:
        return None
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                         capture_output=True, text=True)  # fmt: skip
    folder = out.stdout.strip()
    return Path(folder) if out.returncode == 0 and folder else None


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


def ensure_shortcut() -> str:
    """Windows: one "AP Coder" shortcut on the desktop, made the first time only. Decided once: a shortcut
    already there (an older installer's) is kept, never a second one, and one the person deleted is not put
    back. Returns a summary line ("" = nothing done)."""
    if not WINDOWS:
        return ""
    from ap_coder import paths

    if paths.read_user_settings().get("desktop_shortcut") is not None:
        return ""
    folder = desktop()
    paths.write_user_settings(desktop_shortcut=True)
    if folder is not None and (folder / SHORTCUT).exists():
        return ""
    if folder is None or not write_shortcut(folder):
        return f"Could not put a shortcut on the desktop; start AP Coder with {ROOT / 'APProcessor.bat'}"
    return "Desktop shortcut 'AP Coder' made (once)"


def lm_studio_line() -> tuple[str, str]:
    """LM Studio is optional: say whether a model is loaded, never install or start it."""
    try:
        from ap_coder import local_llm
        from ap_coder.config import Settings

        status = local_llm.check_server(Settings.from_env().llm, timeout=1.0, use_cache=False)
    except Exception as exc:  # noqa: BLE001 - the summary never stops the start
        return "info", f"LM Studio: not checked ({type(exc).__name__}) (optional)"
    if status.active:
        return "ok", f"{status.server}: model {status.model} loaded"
    if status.reachable:
        return "warn", f"{status.server}: running, but no model is loaded (optional: load one to use it)"
    return "info", "LM Studio: not running (optional: invoices are still read and coded without it)"


def readiness(packages: tuple[str, str]) -> list[tuple[str, str]]:
    """The lines of the readiness summary: (level, text)."""
    from ap_coder import offline, paths

    lines = [("ok", f"Python {sys.version.split()[0]} (.venv)"), packages]
    v4, v5 = offline.ppocrv4_installed(), offline.ppocrv5_installed()
    if v5 and not offline.missing_models():
        lines.append(("ok", "OCR for scanned invoices"))
    elif v4 or v5:
        lines.append(("ok", "OCR for scanned invoices (one engine; run scripts/fetch_models.py online for both)"))
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
    lines.append(lm_studio_line())
    return lines


def self_check() -> tuple[bool, str]:
    """``scripts/pilot_check.py`` (about 10 s): the sample invoices read right, with no AI model and no Azure."""
    say("Running the self-check (reads the sample invoices; about 10 seconds)...")
    started = time.monotonic()
    try:
        out = subprocess.run([sys.executable, str(SCRIPTS / "pilot_check.py")], cwd=ROOT, capture_output=True,
                             text=True, errors="replace", timeout=SELF_CHECK_TIMEOUT)  # fmt: skip
    except subprocess.TimeoutExpired:
        return False, "Self-check FAILED: it did not finish in 10 minutes"
    text = out.stdout.splitlines()
    seconds = round(time.monotonic() - started)
    summary = next((t for t in text if "sample invoices read right" in t), "")
    count = summary.split(" sample invoices")[0] if summary else "?"
    if out.returncode == 0:
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


def listening(port: int) -> bool:
    """Something accepts connections on ``port`` here. A short timeout: on Windows a refused connection to a
    closed port is retried for about a second, a listening one answers at once."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            return s.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            return False


def is_ap_coder(port: int, timeout: float = 3.0) -> bool:
    """AP Coder's dashboard answers on ``port`` (not just anything listening there). Never through a proxy."""
    if not listening(port):
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}{SIGNATURE}", timeout=timeout) as response:
            return response.status == 200
    except OSError:
        return False


def running_dashboard(rest: list[str], state: dict) -> int | None:
    """The port of an AP Coder dashboard already running here: the one asked for, else the one the last
    start used, else the default."""
    asked = requested_port(rest)
    ports = [asked] if asked else list(dict.fromkeys(p for p in (state.get("port"), DEFAULT_PORT) if p))
    return next((p for p in ports if is_ap_coder(p)), None)


def free_port(start: int = DEFAULT_PORT) -> int:
    """The first port from ``start`` this computer can listen on (a bind test: instant, unlike connecting)."""
    from ap_coder.offline import dashboard_address

    address = dashboard_address()
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM) as s:
            if not WINDOWS:  # like the dashboard's server: a port left in TIME_WAIT is free
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((address, port))
                return port
            except OSError:
                continue
    return start


def open_browser(url: str) -> None:
    if (os.environ.get("BROWSER") or "").strip().lower() == "none":
        return
    import webbrowser

    webbrowser.open(url)


# --- Start --------------------------------------------------------------------------------------------------------


def start_dashboard(rest: list[str], state: dict) -> int:
    """Start the dashboard on the port asked for, else the first free one from 8501 (recorded, so the next
    double-click finds this one running)."""
    if requested_port(rest) is None:
        rest = ["--port", str(free_port()), *rest]
    state["port"] = requested_port(rest)
    save_state(state)
    say("Starting AP Coder: it opens in your browser. Keep this window open while you use it; close it to stop.")
    try:
        return subprocess.call([sys.executable, "-m", "ap_coder", "dashboard", *rest], cwd=ROOT)
    except KeyboardInterrupt:
        return 0


def run(args: argparse.Namespace, rest: list[str]) -> int:
    state = load_state()
    first = args.venv in ("created", "recreated") or not state.get("setup_done")
    if args.venv in (None, "reused"):
        say(f"Python {sys.version.split()[0]}: reusing .venv")

    port = running_dashboard(rest, state)
    if port:
        url = f"http://localhost:{port}"
        say(f"AP Coder is already running: opening {url} in your browser.")
        say("Nothing new was started. This window can be closed; the AP Coder window already open runs it.")
        open_browser(url)
        return 0

    try:
        level, packages, installed = ensure_packages(state)
    except SetupError as exc:
        save_state(state)
        return failed(exc)
    if str(ROOT) not in sys.path:  # ap_coder itself, even when it was only just installed
        sys.path.insert(0, str(ROOT))
    ensure_data_folder()
    ensure_models(first)
    shortcut = ensure_shortcut()
    lines = readiness((level, packages))
    if shortcut:
        lines.append(("ok" if shortcut.startswith("Desktop") else "warn", shortcut))
    if first or installed or args.check:
        ok, result = self_check()
        state["self_check"] = {"ok": ok, "at": time.strftime("%Y-%m-%d %H:%M"), "result": result}
        lines.append(("ok" if ok else "fail", result))
        if not ok:
            lines.append(("info", "AP Coder still starts. For the details run  python scripts/pilot_check.py  "
                                  "in terminal.bat, and send its output to whoever set up the pilot."))  # fmt: skip
    elif state.get("self_check"):
        last = state["self_check"]
        verdict = "OK" if last.get("ok") else "FAILED"
        lines.append(("ok" if last.get("ok") else "warn",
                      f"Self-check {verdict} at setup ({last.get('at', '?')}); --check runs it again"))  # fmt: skip
    state.update(setup_done=True, fingerprint=fingerprint())
    save_state(state)
    print_summary(lines)
    if args.no_start:
        return 0
    return start_dashboard(rest, state)


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Set up what is missing, check it, and start AP Coder.")
    parser.add_argument("--installer", action="store_true", help="the installer's steps (install.bat)")
    parser.add_argument("--yes", action="store_true", help="no questions (automated runs)")
    parser.add_argument("--check", action="store_true", help="run the self-check again")
    parser.add_argument("--no-start", action="store_true", help="set up and check, but don't start the dashboard")
    parser.add_argument("--venv", help=argparse.SUPPRESS)  # what the first phase did with .venv
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    setup_console()
    if not in_venv():
        return bootstrap(argv)
    args, rest = parse(argv)
    if args.installer:
        import install

        forwarded = [a for a in argv if a != "--installer" and not a.startswith("--venv")]
        return install.main(forwarded, venv=args.venv)
    try:
        return run(args, rest)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
