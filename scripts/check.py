"""The quick check before a commit: ruff on the changed Python files, then the tests of what changed.

    python scripts/check.py              the files changed since the last commit (and new ones)
    python scripts/check.py FILE...      these files instead

The tests picked for a changed module are its own (``tests/test_<module>*.py``) and the test files that import it by
name (a script: those that load it); a changed test file runs itself; a changed template, page script or stylesheet
runs the tests named after it, in a browser too; a file every test uses (conftest.py, pyproject.toml...) runs the
whole default run. Other slow tests are left to CI and ``python -m pytest -m ""`` (pyproject.toml). Exit code 0 when
everything passes, 2 outside a git checkout without files named.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
WIDE = 12  # a module imported by more test files than this (the store, the schema) runs only its own tests


# Files every test leans on: a change to one runs the whole default run.
SHARED = {"tests/conftest.py", "tests/webclient.py", "tests/slow_tests.txt", "pyproject.toml"}
OWN = {"ap_coder/__init__.py": ["tests/test_moved_modules.py"]}  # the package itself: its own tests by name


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
    test_texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in TESTS.glob("test_*.py")}
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
        own = set(TESTS.glob(f"test_{stem}*.py")) | set(TESTS.glob(f"test_web_{stem}*.py"))
        users: list[Path] = []
        if path.parts[:1] == ("ap_coder",) and path.suffix == ".py" and path.name != "__main__.py":
            parts = path.parent.parts if path.name == "__init__.py" else path.with_suffix("").parts
            users = importers(".".join(parts), test_texts)
            if not users and path.name != "__init__.py" and (path.parent / "__init__.py").is_file():
                # A part of a package that re-exports it (core/store/invoices.py): tests import the package.
                users = importers(".".join(path.parent.parts), test_texts)
                own |= set(TESTS.glob(f"test_{path.parent.name}*.py"))
        elif path.parts[:1] == ("scripts",) and path.suffix == ".py":
            users = script_users(path.stem, test_texts)
        picked |= own | (set(users) if len(users) <= WIDE else set())
    return sorted(p.relative_to(ROOT).as_posix() for p in picked)  # tests/x.py on Windows too


def pytest(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=ROOT,
                          capture_output=True, text=True)  # fmt: skip


def main(argv: list[str]) -> int:
    files = argv or changed_files()
    if files is None:
        print("Not a git checkout: name the files to check, e.g. python scripts/check.py ap_coder/coding/tax.py")
        return 2
    if not files:
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
    tests = tests_for(files)
    if tests == []:
        print("No tests found for these files.")
    else:
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
    print("check: FAILED" if failed else "check: OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
