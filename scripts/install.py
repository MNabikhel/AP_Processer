"""AP Coder installer and updater: the setup of APProcessor.bat plus a few questions.

Most people just double-click ``APProcessor.bat`` (Windows) or ``APProcessor.command`` (Mac): that sets
up whatever is missing and starts AP Coder. This installer is the same setup (``scripts/launch.py``: one
``.venv``, only missing packages installed, OCR models copied once, one desktop shortcut) with the
questions on top. ``install.bat`` / ``install.sh`` run it (through APProcessor, so Python is found the
same way). It is safe to run again at any time: each run updates what changed and keeps everything else.

* offline by default (an enterprise build): packages only from the offline bundle's ``wheelhouse/``, the
  OCR models only from its ``models/``, and no git update. Developers opt in to the internet (PyPI, the
  model download, ``git fetch`` / ``git pull`` of a checkout) with the environment variable ``AP_ALLOW_INTERNET=1``
* with that opt-in, a git checkout is updated in place — never a second copy
* one virtual environment (``.venv``), packages installed only when missing or too old
* one data folder outside the code (default ``~/APCoder``) for the database, invoices,
  outputs and the ``.env`` with your Azure settings, so updates never lose or duplicate them
* the desktop shortcut is overwritten, not added again

Options: ``--yes`` (no questions, keep current answers), ``--no-update``, ``--skip-tests``,
``--data-dir PATH``, ``--reinstall``, ``--no-shortcut``, ``--fresh-start``, ``--no-start``.

Standard library only at import (it is loaded before the packages are installed).
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # ap_coder/envfile.py is standard-library only, usable before installing
sys.path.insert(0, str(ROOT / "scripts"))

import launch  # noqa: E402  (the one setup: .venv, packages, models, shortcut, readiness)

from ap_coder.envfile import clean_url, read_env, write_env  # noqa: E402
from ap_coder.offline import internet_allowed  # noqa: E402

WINDOWS = os.name == "nt"
USER_SETTINGS = Path(os.environ.get("AP_USER_SETTINGS") or Path.home() / ".ap_coder" / "settings.json")
DEFAULT_DATA_DIR = Path.home() / "APCoder"
STEPS = 8
EXTRAS = ("ocr", "dev")  # the installer also installs the self-test's tools (pytest, ruff)

# (variable, question, kind) — kind: url | secret | text
AZURE_FIELDS = [
    ("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
     "Document Intelligence endpoint\n  (Azure portal > Document Intelligence resource > Keys and Endpoint)", "url"),
    ("AZURE_DOCUMENT_INTELLIGENCE_KEY",
     "Document Intelligence key (KEY 1). Leave empty to sign in with 'az login' instead", "secret"),
    ("AZURE_OPENAI_ENDPOINT", "Azure OpenAI endpoint (same page on your Azure OpenAI resource)", "url"),
    ("AZURE_OPENAI_API_KEY", "Azure OpenAI key. Leave empty to sign in with 'az login' instead", "secret"),
    ("AZURE_OPENAI_DEPLOYMENT", "Azure OpenAI deployment name (Azure AI Foundry > Deployments)", "text"),
    ("AZURE_OPENAI_MODEL_NAME", "Model behind that deployment, e.g. gpt-4o or gpt-4o-mini", "text"),
    ("AP_REVIEWER", "Your name, shown on the invoices you approve", "text"),
]  # fmt: skip
REQUIRED = {"AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"}


# --- Console ---------------------------------------------------------------------------------------------------


class Console:
    def __init__(self, assume_yes: bool) -> None:
        self.assume_yes = assume_yes
        self.n = 0

    def step(self, title: str) -> None:
        self.n += 1
        print(f"\n[{self.n}/{STEPS}] {title}")

    @staticmethod
    def ok(message: str) -> None:
        print(f"    OK  {message}")

    @staticmethod
    def info(message: str) -> None:
        print(f"        {message}")

    @staticmethod
    def warn(message: str) -> None:
        print(f"    !!  {message}")

    def ask(self, question: str, default: str = "", secret: bool = False) -> str:
        if self.assume_yes:
            return default
        shown = f" [{mask(default) if secret else default}]" if default else ""
        prompt = f"    ?   {question}{shown}: "
        try:
            answer = getpass.getpass(prompt) if secret and sys.stdin.isatty() else input(prompt)
        except EOFError:
            answer = ""
        answer = answer.strip().strip('"').strip("'").strip()
        return answer or default

    def yes(self, question: str, default: bool = True) -> bool:
        if self.assume_yes:
            return default
        answer = self.ask(f"{question} ({'Y/n' if default else 'y/N'})").lower()
        return default if not answer else answer.startswith("y")


def mask(value: str) -> str:
    return "****" + value[-4:] if len(value) > 8 else "****"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, text=True, **kwargs)


def quiet(cmd: list[str]) -> subprocess.CompletedProcess:
    return run(cmd, capture_output=True)


# --- Steps -------------------------------------------------------------------------------------------------------


def check_python(c: Console) -> None:
    c.step("Python")
    c.ok(f"Python {sys.version.split()[0]} ({sys.executable})")
    if "onedrive" in str(ROOT).lower():
        c.warn("This folder is inside OneDrive. That's fine for the code, but keep the data folder outside it.")


def update_code(c: Console, args: argparse.Namespace) -> int | None:
    """Update a git checkout in place. Returns an exit code if a newer installer took over."""
    c.step("Code")
    if args.no_update:
        c.ok("not checking for updates (--no-update)")
        return None
    if not internet_allowed():
        c.ok(f"{ROOT} (offline build: no update check; developers: AP_ALLOW_INTERNET=1)")
        c.info("To update, unzip the new offline bundle over this folder and run its installer; your data is kept.")
        return None
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        c.ok(f"{ROOT}")
        c.info("Downloaded without git: to update later, download the new version and run its installer.")
        c.info("Your data and settings are kept either way.")
        return None
    branch = quiet(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    before = quiet(["git", "rev-parse", "HEAD"]).stdout.strip()
    if quiet(["git", "fetch", "--quiet", "origin", branch]).returncode != 0:
        c.warn("Could not reach GitHub to check for updates; continuing with the code you have.")
        return None
    behind = quiet(["git", "rev-list", "--count", f"HEAD..origin/{branch}"]).stdout.strip() or "0"
    if behind == "0":
        c.ok(f"up to date (branch {branch}, {before[:7]})")
        return None
    if quiet(["git", "status", "--porcelain", "--untracked-files=no"]).stdout.strip():
        c.warn(f"{behind} update(s) available, but files in this folder were edited, so nothing was changed.")
        c.info("To get the update, undo those edits (git stash) and run the installer again.")
        return None
    if not c.yes(f"{behind} update(s) available on {branch}. Update now?"):
        return None
    if run(["git", "pull", "--ff-only", "--quiet", "origin", branch]).returncode != 0:
        c.warn("The update could not be applied automatically; continuing with the code you have.")
        return None
    after = quiet(["git", "rev-parse", "HEAD"]).stdout.strip()
    c.ok(f"updated {before[:7]} -> {after[:7]}")
    changed = quiet(["git", "diff", "--name-only", before, after]).stdout.split()
    if "scripts/install.py" in changed:
        c.info("The installer itself was updated: continuing with the new version.")
        return run([sys.executable, str(Path(__file__)), *sys.argv[1:], "--no-update"]).returncode
    return None


def ensure_venv_and_packages(c: Console, args: argparse.Namespace, venv: str | None) -> None:
    c.step("Virtual environment and packages")
    if venv in ("created", "recreated"):
        c.ok(f"{venv} .venv (Python {sys.version.split()[0]})")
    else:
        c.ok(f"reusing .venv (Python {sys.version.split()[0]})")
    state = launch.load_state()
    if args.reinstall and launch.pip_source() is None:
        c.warn("Not reinstalled (--reinstall): this offline build installs only from the bundle's wheelhouse folder.")
    elif args.reinstall:
        c.info("Reinstalling AP Coder itself (--reinstall)...")
        if not launch.pip_install(["--force-reinstall", "--no-deps", *launch.editable(())]):
            c.warn("Reinstalling failed (see the messages above).")
    try:
        level, summary, _ = launch.ensure_packages(state, extras=EXTRAS)
    except launch.SetupError as exc:
        c.warn(str(exc))
        c.info(exc.hint)
        sys.exit(1)
    launch.save_state(state)
    (c.ok if level == "ok" else c.warn)(summary.removeprefix("Packages: "))


def read_user_settings() -> dict:
    try:  # -sig: a settings file saved by Notepad starts with a byte-order mark
        return json.loads(USER_SETTINGS.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def write_user_settings(**values: object) -> None:
    USER_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    USER_SETTINGS.write_text(json.dumps({**read_user_settings(), **values}, indent=2), encoding="utf-8")


def has_data(folder: Path) -> bool:
    return folder.is_dir() and any(p.name != "README.md" for p in folder.iterdir())


def choose_data_dir(c: Console, args: argparse.Namespace) -> Path:
    c.step("Data folder (database, invoices, outputs, Azure settings)")
    current = read_user_settings().get("data_dir")
    if args.data_dir:
        data = Path(args.data_dir).expanduser().resolve()
        if current and Path(current) != data and has_data(Path(current)):
            c.warn(f"Switching data folder. The old one stays where it is: {current}")
    elif current:
        data = Path(current)
    else:
        c.info("AP Coder keeps your invoices, its database and your Azure keys in one folder, outside the")
        c.info("code, so new versions of the code use the same data. Do NOT use a OneDrive-synced folder.")
        data = Path(c.ask("Data folder", str(DEFAULT_DATA_DIR))).expanduser().resolve()
    if "onedrive" in str(data).lower():
        c.warn("That folder is synced by OneDrive, which can corrupt the database while it is open.")
        if not c.yes("Use it anyway?", default=False):
            data = DEFAULT_DATA_DIR
            c.info(f"Using {data} instead.")
    data.mkdir(parents=True, exist_ok=True)
    (data / "invoices").mkdir(exist_ok=True)
    write_user_settings(data_dir=str(data))

    legacy = ROOT / "private"
    if legacy.resolve() != data.resolve() and has_data(legacy) and not has_data_besides_env(data):
        for item in legacy.iterdir():
            if item.name == "README.md":
                continue
            target = data / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            elif not target.exists():
                shutil.copy2(item, target)
        c.ok(f"copied your existing data from {legacy} (the originals are left in place)")

    if args.fresh_start:
        fresh_start(c, data)
    c.ok(f"{data}")
    return data


def has_data_besides_env(folder: Path) -> bool:
    return any(p.name not in (".env", "invoices") or (p.is_dir() and any(p.iterdir())) for p in folder.iterdir())


def fresh_start(c: Console, data: Path) -> None:
    """Move the database, invoices and outputs into a dated backup folder (settings and cache are kept)."""
    keep = {".env", ".cache", "backups"}  # settings, the scanned-text cache (re-reading costs) and backups
    movable = [p for p in data.iterdir() if p.name not in keep and not p.name.startswith("backup-")]
    if not movable:
        c.ok("nothing to clear: the data folder is already empty")
        return
    question = "Move the database, invoices and outputs to a backup folder and start fresh?"
    if not c.assume_yes and not c.yes(question, default=False):  # --fresh-start --yes: already asked for
        return
    backup = data / f"backup-{dt.datetime.now():%Y%m%d-%H%M%S}"
    backup.mkdir()
    for p in movable:
        shutil.move(str(p), str(backup / p.name))
    (data / "invoices").mkdir(exist_ok=True)
    c.ok(f"started fresh; the previous data is in {backup}")


# --- .env ----------------------------------------------------------------------------------------------------------


def display_name() -> str:
    """The person's full name when the system knows it (Windows / macOS), else their login name."""
    try:
        if WINDOWS:
            import ctypes

            size = ctypes.c_ulong(256)
            buffer = ctypes.create_unicode_buffer(size.value)
            if ctypes.windll.secur32.GetUserNameExW(3, buffer, ctypes.byref(size)) and buffer.value.strip():
                return buffer.value.strip()  # 3 = NameDisplay, e.g. "Jane Doe"
        elif sys.platform == "darwin":
            name = quiet(["id", "-F"]).stdout.strip()
            if name:
                return name
    except Exception:  # noqa: BLE001 - any failure just means "use the login name"
        pass
    return getpass.getuser()


def configure_azure(c: Console, data: Path) -> Path:
    c.step("Azure settings")
    env = data / ".env"
    project_env = ROOT / ".env"
    if not env.exists():
        if project_env.exists():
            shutil.copy2(project_env, env)
            project_env.rename(ROOT / ".env.moved-to-data-folder")
            c.ok(f"moved your existing .env to {env}")
        else:
            shutil.copy2(ROOT / ".env.example", env)
            c.ok(f"created {env}")
    elif project_env.exists():
        c.warn(f"There is also a .env in {ROOT}; AP Coder uses the one in the data folder: {env}")

    current = read_env(env)
    complete = all(current.get(k) for k in REQUIRED)
    if complete:
        c.ok(f"already filled in ({env})")
        if not c.yes("Change any Azure settings?", default=False):
            return env
    else:
        c.info("Answer each question (press Enter to keep the value in brackets). You can change these")
        c.info(f"later by running the installer again, or by editing {env}")
    defaults = {"AP_REVIEWER": current.get("AP_REVIEWER") or display_name()}
    answers: dict[str, str] = {}
    for key, question, kind in AZURE_FIELDS:
        default = current.get(key) or defaults.get(key, "")
        if key == "AZURE_OPENAI_MODEL_NAME" and not default:
            default = answers.get("AZURE_OPENAI_DEPLOYMENT", "")  # usually named after the model
        answer = c.ask(question, default, secret=kind == "secret")
        answers[key] = clean_url(answer) if kind == "url" else answer
    updates = {k: v for k, v in answers.items() if v != current.get(k, "")}
    if updates:
        write_env(env, updates)
        c.ok(f"saved {len(updates)} setting(s) to {env}")
    missing = [k for k in sorted(REQUIRED) if not answers.get(k)]
    if missing:
        c.warn("Still missing: " + ", ".join(missing) + ". You can try the sample data without them.")
    return env


# --- Shortcut, checks, start -------------------------------------------------------------------------------------


def create_shortcut(c: Console, args: argparse.Namespace) -> None:
    c.step("Shortcut")
    if not WINDOWS:
        c.ok(f"start AP Coder with: {ROOT / 'APProcessor.command'}")
        return
    wanted = read_user_settings().get("desktop_shortcut")
    if args.no_shortcut:
        wanted = False
    elif wanted is None:
        wanted = c.yes("Put an 'AP Coder' shortcut on your desktop?")
    write_user_settings(desktop_shortcut=bool(wanted))
    if not wanted:
        c.ok(f"no desktop shortcut; start AP Coder with {ROOT / 'APProcessor.bat'}")
        return
    folder = launch.desktop()
    if folder is not None and launch.write_shortcut(folder):
        c.ok("desktop shortcut 'AP Coder' (updated in place, never duplicated)")
    else:
        c.warn(f"Could not create the desktop shortcut; start AP Coder with {ROOT / 'APProcessor.bat'}")


def fetch_models(c: Console) -> None:
    c.step("OCR models (for scanned invoices)")
    state = launch.load_state()
    launch.ensure_models(first=True, state=state)
    launch.save_state(state)
    if state.get("models_failed"):
        c.warn("not all in place: scans are read with one OCR engine (the models come from the offline bundle)")
    else:
        c.ok("in place (copied from the offline bundle only when missing)")


def run_checks(c: Console, args: argparse.Namespace, env: Path) -> None:
    c.step("Checks")
    if args.skip_tests:
        c.ok("tests skipped (--skip-tests)")
    else:
        result = quiet([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
        summary = (result.stdout.strip().splitlines() or ["(no output)"])[-1]
        if result.returncode == 0:
            c.ok(f"self-test: {summary}")
        else:
            c.warn(f"self-test: {summary}")
            c.info("Please paste the full output of 'python -m pytest -q' (in terminal.bat) into the chat.")
    print()
    run([sys.executable, "-m", "ap_coder", "doctor"])
    values = read_env(env)
    if all(values.get(k) for k in REQUIRED) and c.yes(
        "Test the connection to Azure now? (one tiny request to each service, a fraction of a cent)", default=False
    ):
        print()
        run([sys.executable, "-m", "ap_coder", "doctor", "--online"])


def main(argv: list[str] | None = None, venv: str | None = None) -> int:
    """The installer's steps, inside ``.venv`` (``launch.py --installer`` makes it first; ``venv`` says how)."""
    parser = argparse.ArgumentParser(description="Install or update AP Coder (safe to run again).")
    parser.add_argument("--yes", action="store_true", help="no questions: keep current answers / defaults")
    parser.add_argument("--no-update", action="store_true", help="don't check GitHub for a newer version")
    parser.add_argument("--skip-tests", action="store_true", help="skip the self-test")
    parser.add_argument("--data-dir", help=f"data folder (default {DEFAULT_DATA_DIR})")
    parser.add_argument("--reinstall", action="store_true", help="reinstall packages even if unchanged")
    parser.add_argument("--no-shortcut", action="store_true", help="no desktop shortcut")
    parser.add_argument(
        "--fresh-start", action="store_true",
        help="move the database, invoices and outputs to a backup folder (Azure settings are kept)",
    )  # fmt: skip
    parser.add_argument("--no-start", action="store_true", help="don't offer to start the dashboard at the end")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    c = Console(args.yes)
    print("AP Coder installer: safe to run again. It updates what changed and keeps your data and settings.")
    try:
        check_python(c)
        handed_over = update_code(c, args)
        if handed_over is not None:
            return handed_over
        ensure_venv_and_packages(c, args, venv)
        data = choose_data_dir(c, args)
        env = configure_azure(c, data)
        create_shortcut(c, args)
        fetch_models(c)
        run_checks(c, args, env)
    except KeyboardInterrupt:
        print("\nStopped. Run the installer again any time; it picks up where it left off.")
        return 130

    start = "the 'AP Coder' desktop shortcut" if WINDOWS and read_user_settings().get("desktop_shortcut") else (
        "APProcessor.bat in this folder" if WINDOWS else "APProcessor.command in this folder"
    )  # fmt: skip
    state = launch.load_state()
    packages = launch.ensure_packages(state, extras=EXTRAS)[:2]  # all in place by now: nothing installed
    state.update(setup_done=True, fingerprint=launch.fingerprint())
    launch.save_state(state)
    launch.print_summary(launch.readiness(packages))
    print("\nAll set.")
    print(f"  Start AP Coder:   {start}")
    print(f"  Your data:        {data}")
    print(f"  Azure settings:   {env}")
    print("  Update later:     run the installer again (nothing is duplicated)")
    interactive = sys.stdin.isatty()  # never start a server when no one is there to stop it
    if not args.no_start and not args.yes and interactive and c.yes("Start AP Coder now?"):
        return launch.start_dashboard([], state)
    return 0


if __name__ == "__main__":  # the same as install.bat / install.sh: .venv first, then the steps above
    sys.exit(launch.main(["--installer", *sys.argv[1:]]))
