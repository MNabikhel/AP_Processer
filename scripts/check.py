"""The quick check before a commit: ruff on the changed Python files, then the tests of what changed.

    python scripts/check.py              the files changed since the last commit (and new ones)
    python scripts/check.py FILE...      these files instead
    --walk / --no-walk                   run the first-day walk whatever changed / not at all

The tests picked for a changed module are its own (``tests/<folder>/test_<module>*.py``, the folders mirror
ap_coder/) and the test files that import it by name (a script: those that load it); a changed test file runs itself;
a changed template, page script or stylesheet runs the tests named after it, in a browser too; a file every test uses
(conftest.py, pyproject.toml, the shared helpers...) runs the whole default run. Other slow tests are left to CI and
``python -m pytest -m ""`` (pyproject.toml). A change to what the pilot tester's first day walks through (a page, its
words, the guide, the launcher: ``WALK``) runs that walk too, ``scripts/ci/first_day.py`` (D-225, about two minutes),
in a temporary folder and on a free port, never the data folder. Exit code 0 when everything passes, 2 outside a git
checkout without files named.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
WIDE = 12  # a module imported by more test files than this (the store, the schema) runs only its own tests


# Files every test leans on: a change to one runs the whole default run.
SHARED = {"tests/conftest.py", "tests/webclient.py", "tests/pdfs.py", "tests/scripts.py", "tests/browser.py",
          "tests/slow_tests.txt", "tests/stores.py", "pyproject.toml"}  # fmt: skip
OWN = {"ap_coder/__init__.py": ["tests/meta/test_moved_modules.py"]}  # the package itself: its own tests by name
# What the pilot tester's first day (scripts/ci/first_day.py) walks through: a change to one runs the walk (a folder
# ends in "/"). Its CI job is Windows only, so a page or wording change otherwise fails it only there.
WALK = ("ap_coder/web/templates/", "ap_coder/web/static/", "ap_coder/web/pages/", "ap_coder/ops/help.py",
        "docs/PROTOTYPE.md", "scripts/ci/first_day.py", "APProcessor.bat", "APProcessor.command", "scripts/launch.py",
        "scripts/running.py", "scripts/first_run.py", "scripts/check_deps.py", "scripts/terminal.bat")  # fmt: skip
WALK_SECONDS = 2400  # the walk's own waits end it well before; this only stops one that hangs


def changed_files() -> list[str] | None:
    """Changed, new and deleted files since the last commit; None outside a git checkout."""
    try:
        done = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=ROOT, capture_output=True, text=True)
    except OSError:
        return None
    if done.returncode:
        return None
    return [line[3:].split(" -> ")[-1].strip('"') for line in done.stdout.splitlines() if line.strip()]


def importers(module: str, test_texts: dict[Path, str]) -> list[Path]:
    """Test files that import ``ap_coder.<package>.<module>`` (or its old flat path) by name."""
    package, _, name = module.rpartition(".")
    pattern = re.compile(
        rf"(?:from|import)\s+{re.escape(module)}\b|(?:from|import)\s+ap_coder\.{name}\b"
        rf"|from\s+{re.escape(package)}\s+import\s+(?:\([^)]*|[^\n]*)\b{name}\b"
    )
    return [path for path, text in test_texts.items() if pattern.search(text)]


def script_users(stem: str, test_texts: dict[Path, str]) -> list[Path]:
    """Test files that load ``scripts/<stem>.py``: by its path, by name (``_script("upgrade")``) or by import."""
    pattern = re.compile(rf"scripts[/\\.]{stem}\b|[\"']{stem}(?:\.py)?[\"']|import\s+{stem}\b")
    return [path for path, text in test_texts.items() if pattern.search(text)]


def tests_for(files: list[str]) -> list[str] | None:
    """The test files to run for these changed files; None means the whole default run."""
    if any(f in SHARED for f in files):
        return None
    test_texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in TESTS.rglob("test_*.py")}
    picked: set[Path] = set()
    for f in files:
        path = Path(f)
        if f in OWN:
            picked |= {ROOT / t for t in OWN[f]}
            continue
        if path.parts[:1] == ("tests",) and path.name.startswith("test_") and path.suffix == ".py":
            if (ROOT / path).is_file():
                picked.add(ROOT / path)
            continue
        stem = path.parent.name if path.name == "__init__.py" else path.stem.lstrip("_")
        own = set(TESTS.rglob(f"test_{stem}*.py")) | set(TESTS.rglob(f"test_web_{stem}*.py"))
        users: list[Path] = []
        if path.parts[:1] == ("ap_coder",) and path.suffix == ".py" and path.name != "__main__.py":
            parts = path.parent.parts if path.name == "__init__.py" else path.with_suffix("").parts
            users = importers(".".join(parts), test_texts)
            if not users and path.name != "__init__.py" and (path.parent / "__init__.py").is_file():
                # A part of a package that re-exports it (core/store/invoices.py): tests import the package.
                users = importers(".".join(path.parent.parts), test_texts)
                own |= set(TESTS.rglob(f"test_{path.parent.name}*.py"))
        elif path.parts[:1] == ("scripts",) and path.suffix == ".py":
            users = script_users(path.stem, test_texts)
        picked |= own | (set(users) if len(users) <= WIDE else set())
    return sorted(p.relative_to(ROOT).as_posix() for p in picked)  # tests/x.py on Windows too


def walk_needed(files: list[str]) -> bool:
    """Whether a changed file is one the first-day walk goes through (``WALK``)."""
    paths = [f.replace("\\", "/") for f in files]
    return any(p == w or (w.endswith("/") and p.startswith(w)) for p in paths for w in WALK)


def walk() -> tuple[bool, str]:
    """The first day of docs/PROTOTYPE.md (``scripts/ci/first_day.py``, started with ``serve``) in a new temporary
    folder, on a free port, with none of this computer's AP Coder settings: whether it went as the guide says, and on
    a failure what the walk said."""
    work = Path(tempfile.mkdtemp(prefix="check-first-day-"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("AP_")}
    env.update(AP_PRIVATE_DIR=str(work / "unused"), AP_USER_SETTINGS=str(work / "user-settings.json"))
    cmd = [sys.executable, str(ROOT / "scripts" / "ci" / "first_day.py"), "--work", str(work / "walk"), "--start",
           "serve", "--port", str(port)]  # fmt: skip
    try:
        done = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=WALK_SECONDS)  # fmt: skip
    except subprocess.TimeoutExpired:
        return False, f"the walk was still going after {WALK_SECONDS // 60} minutes: stopped"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if done.returncode == 0:
        return True, ""
    said = [line for line in done.stderr.splitlines() if line.strip()]
    return False, "\n".join(done.stdout.strip().splitlines()[-40:] + said[-20:])


def pytest(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=ROOT,
                          capture_output=True, text=True)  # fmt: skip


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__.strip())
        return 0
    force, skip = "--walk" in argv, "--no-walk" in argv
    argv = [a for a in argv if a not in ("--walk", "--no-walk")]
    files = argv or changed_files()
    if files is None:
        print("Not a git checkout: name the files to check, e.g. python scripts/check.py ap_coder/coding/tax.py")
        return 2
    if not files and not force:
        print("Nothing changed since the last commit (name files to check them).")
        return 0
    failed = False
    python = [f for f in files if f.endswith(".py") and (ROOT / f).is_file()]
    if python:
        for cmd in (["ruff", "check", "-q", *python], ["ruff", "format", "--check", "-q", *python]):
            done = subprocess.run([sys.executable, "-m", *cmd], cwd=ROOT, capture_output=True, text=True)
            if done.returncode:
                failed = True
                print((done.stdout + done.stderr).strip())
    tests = tests_for(files) if files else []  # (--walk alone: the walk only)
    if tests == [] and files:
        print("No tests found for these files.")
    elif tests != []:
        # A page's script, template or stylesheet is tested in a browser: those tests are slow, so asked for here.
        every = ["-m", ""] if any(f.endswith((".js", ".html", ".css")) for f in files) else []
        print("Tests:", " ".join(tests) if tests else "the whole default run (a file every test uses changed)",
              flush=True)  # fmt: skip
        done = pytest([*every, *(tests or [])])
        if done.returncode == 5 and not every:  # every test picked is a slow one: run them
            done = pytest(["-m", "", *(tests or [])])
        lines = done.stdout.strip().splitlines()
        failures = [line for line in lines if line.startswith(("FAILED", "ERROR"))]
        print("\n".join(failures[:20] + lines[-1:]))
        if done.returncode not in (0, 5) and not failures:
            print(done.stderr.strip()[-2000:] or "\n".join(lines[-30:]))
        failed |= done.returncode not in (0, 5)
    if skip:
        print("first-day walk: skipped (--no-walk)")
    elif not (force or walk_needed(files)):
        print("first-day walk: not needed (no page, its words, the guide or the launcher changed)")
    else:
        print("first-day walk: docs/PROTOTYPE.md step by step (about two minutes)...", flush=True)
        ok, said = walk()
        print("first-day walk: OK" if ok else f"{said}\nfirst-day walk: FAILED")
        failed |= not ok
    print("check: FAILED" if failed else "check: OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
