"""The quick check before a commit: ruff on the changed Python files, then the tests of what changed.

    python scripts/check.py              the files changed since the last commit (and new ones)
    python scripts/check.py FILE...      these files instead

The tests picked for a changed module are its own (``tests/test_<module>*.py``) and the test files that import it by
name; a changed test file runs itself; a changed template, script or stylesheet runs the tests named after it, in a
browser too. Other slow tests are left to CI and ``python -m pytest -m ""`` (pyproject.toml). Exit code 0 when
everything passes.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
WIDE = 12  # a module imported by more test files than this (the store, the schema) runs only its own tests


def changed_files() -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=ROOT, capture_output=True, text=True).stdout
    files = []
    for line in out.splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        if (ROOT / path).is_file():
            files.append(path)
    return files


def importers(module: str, test_texts: dict[Path, str]) -> list[Path]:
    """Test files that import ``ap_coder.<package>.<module>`` (or its old flat path) by name."""
    package, _, name = module.rpartition(".")
    pattern = re.compile(
        rf"(?:from|import)\s+{re.escape(module)}\b|from\s+{re.escape(package)}\s+import\s+[^\n]*\b{name}\b"
        rf"|(?:from|import)\s+ap_coder\.{name}\b"
    )
    return [path for path, text in test_texts.items() if pattern.search(text)]


def tests_for(files: list[str]) -> list[str]:
    test_texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in TESTS.glob("test_*.py")}
    picked: set[Path] = set()
    for f in files:
        path = Path(f)
        if path.parts[:1] == ("tests",) and path.name.startswith("test_") and path.suffix == ".py":
            picked.add(ROOT / path)
            continue
        stem = path.stem.lstrip("_")
        own = set(TESTS.glob(f"test_{stem}*.py")) | set(TESTS.glob(f"test_web_{stem}*.py"))
        if path.parts[:1] == ("ap_coder",) and path.suffix == ".py" and stem not in ("__init__", "__main__"):
            module = ".".join(path.with_suffix("").parts)
            users = importers(module, test_texts)
            picked |= own | (set(users) if len(users) <= WIDE else set())
        else:
            picked |= own
    return sorted(str(p.relative_to(ROOT)) for p in picked)


def main(argv: list[str]) -> int:
    files = argv or changed_files()
    if not files:
        print("Nothing changed since the last commit.")
        return 0
    failed = False
    python = [f for f in files if f.endswith(".py")]
    if python:
        for cmd in (["ruff", "check", "-q", *python], ["ruff", "format", "--check", "-q", *python]):
            done = subprocess.run([sys.executable, "-m", *cmd], cwd=ROOT, capture_output=True, text=True)
            if done.returncode:
                failed = True
                print((done.stdout + done.stderr).strip())
    tests = tests_for(files)
    if tests:
        # A page's script, template or stylesheet is tested in a browser: those tests are slow, so asked for here.
        every = ["-m", ""] if any(f.endswith((".js", ".html", ".css")) for f in files) else []
        print("Tests:", " ".join(tests), flush=True)
        done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *every, *tests],
                              cwd=ROOT, capture_output=True, text=True)  # fmt: skip
        lines = done.stdout.strip().splitlines()
        failures = [line for line in lines if line.startswith(("FAILED", "ERROR"))]
        print("\n".join(failures[:20] + lines[-1:]))
        failed |= done.returncode not in (0, 5)
    else:
        print("No tests found for these files.")
    print("check: FAILED" if failed else "check: OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
