"""The quick check before a commit: ruff on the changed Python files, then the tests of what changed.

    python scripts/check.py              the files changed since the last commit (and new ones)
    python scripts/check.py FILE...      these files instead
    --walk / --no-walk                   run the first-day walk whatever changed / not at all

The tests picked for a changed module are its own (``tests/<folder>/test_<module>*.py``, the folders mirror
ap_coder/, or those named in ``OWN``) and the test files that import it by name (a script: those that load it; a
module more than ``WIDE`` of them import: the quickest of them, up to ``BUDGET`` seconds); a changed test file runs
itself; a changed template, page script or stylesheet runs the tests named after it, in a browser too; a changed doc
runs the rules every doc is held to and the tests that open it; a file every test uses (conftest.py, pyproject.toml,
the shared helpers...) runs the whole default run. Any change to the app, the scripts or the tests also runs the
rules that check the whole tree (``RULES``, D-329, their slow tests too). A changed module of the app no test is
picked for fails the check (``NO TESTS RAN FOR``), and a changed function near the length limit gets a warning.
Other slow tests are left to CI and ``python -m pytest -m ""`` (pyproject.toml). A change to what the pilot tester's
first day walks through (a page, its words, the guide, the launcher: ``WALK``) runs that walk too,
``scripts/ci/first_day.py`` (D-225, about two minutes), in a temporary folder and on a free port, never the data
folder. Exit code 0 when everything passes, 2 outside a git checkout without files named.
"""

from __future__ import annotations

import ast
import json
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
WIDE = 12  # a module imported by more test files than this (the store, the schema) brings only the quickest of them,
BUDGET = 20.0  # up to these seconds of the default run (tests/test_times.json), its own tests aside
UNTIMED = 2.0  # the seconds taken for a test file tests/test_times.json has no time for yet
NEAR = 110  # a changed function this long (tests/meta/test_function_length.py: at most 120 lines) and
NEAR_PART = 950  # a part of the store this long (tests/core/test_store_package.py: under 1,000) get a warning


# Files every test leans on: a change to one runs the whole default run.
SHARED = {"tests/conftest.py", "tests/webclient.py", "tests/pdfs.py", "tests/scripts.py", "tests/browser.py",
          "tests/slow_tests.txt", "tests/stores.py", "pyproject.toml"}  # fmt: skip
# The rules that check the whole tree (D-329): the code rules (all of tests/meta, its slow tests too, no browser),
# the offline guard's network touchpoints (its quick tests), the store's parts, a help entry for every check code, a
# rule for every route, the built-in roles' access and an old database for each schema since 27 (that one test: a
# schema raised in _base.py alone). A change far from them breaks them (a module moved, a script added), so any change
# to the app, the scripts or the tests runs them all: about 30 seconds.
RULES = ("tests/meta", "tests/offline/test_offline_guard.py", "tests/core/test_store_package.py",
         "tests/core/test_migrations.py::test_every_schema_since_pr_99s_has_an_old_database", "tests/ops/test_help.py",
         "tests/web/test_roles.py", "tests/web/test_role_permissions.py")  # fmt: skip
# The rules every doc is held to (docs/, CLAUDE.md, README.md, .claude/agents/, and .ignore): its lines, the log
# entries' numbers (the second one only once it is there): a changed doc runs them, and the tests that open it by its
# path.
DOC_RULES = ("tests/meta/test_agent_friendly.py", "tests/meta/test_log_entries.py")
# The tests of a file that its name and imports miss (named otherwise, reached through a page's address or a command,
# a doc the Help page shows): these, in place of those its name finds (a doc's: besides the rules every doc is held to).
OWN = {
    "ap_coder/__init__.py": ["tests/meta/test_moved_modules.py"],  # the package itself
    "ap_coder/__main__.py": ["tests/ops/test_config_and_cli.py"],  # python -m ap_coder: the command line
    "ap_coder/ai/offline_coder.py": ["tests/ai/test_offline_*.py"],
    "ap_coder/bench/__main__.py": ["tests/bench/test_bench.py"],
    "ap_coder/erp/jde_retainage.py": ["tests/bench/test_jde_po_vouchers.py", "tests/erp/test_jde_keying.py"],
    "ap_coder/ops/jde_cmd.py": ["tests/ops/test_config_and_cli.py"],
    "ap_coder/ops/service_windows.py": ["tests/ops/test_service.py"],
    "ap_coder/web/pages/invoice_approve.py": ["tests/web/test_web_invoice*.py"],
    "ap_coder/web/pages/invoice_document.py": ["tests/web/test_web_invoice.py", "tests/web/test_web_viewer.py"],
    "ap_coder/web/pages/invoice_lines.py": ["tests/web/test_web_dashboard_flows.py", "tests/web/test_web_invoice.py"],
    "ap_coder/web/pages/invoice_router.py": ["tests/web/test_web_invoice*.py"],
    "ap_coder/web/pages/month_end.py": ["tests/erp/test_accruals.py", "tests/web/test_web_finance.py"],
    "ap_coder/web/pages/settings_hub.py": ["tests/erp/test_finance_hub.py"],
    "ap_coder/web/pages/settings_jde.py": ["tests/erp/test_jde_api_web.py", "tests/web/test_web_settings.py"],
    "ap_coder/web/pages/who.py": ["tests/web/test_laptop_sign_in.py"],
    "docs/QUICK_START.md": ["tests/web/test_web_help.py"],  # shown on the Help page
    "docs/WHATS_NEW.md": ["tests/web/test_web_help.py"],
    "scripts/ci/fake_lm_studio.py": ["tests/bench/test_fake_model.py"],
}
# What the pilot tester's first day (scripts/ci/first_day.py) walks through: a change to one runs the walk (a folder
# ends in "/"). Its CI job is Windows only, so a page or wording change otherwise fails it only there.
WALK = ("ap_coder/web/templates/", "ap_coder/web/static/", "ap_coder/web/pages/", "ap_coder/ops/help.py",
        "docs/PROTOTYPE.md", "scripts/ci/first_day.py", "APProcessor.bat", "APProcessor.command", "scripts/launch.py",
        "scripts/running.py", "scripts/first_run.py", "scripts/check_deps.py", "scripts/terminal.bat")  # fmt: skip
WALK_SECONDS = 2400  # the walk's own waits end it well before; this only stops one that hangs


def changed_files() -> list[str] | None:
    """Changed, new and deleted files since the last commit; None outside a git checkout."""
    try:
        done = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=ROOT, capture_output=True,
                              encoding="utf-8", errors="replace")  # fmt: skip
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
    return [path for path, text in test_texts.items() if name in text and pattern.search(text)]  # (quick: name first)


def script_users(stem: str, test_texts: dict[Path, str]) -> list[Path]:
    """Test files that load ``scripts/<stem>.py``: by its path, by name (``_script("upgrade")``) or by import."""
    pattern = re.compile(rf"scripts[/\\.]{stem}\b|[\"']{stem}(?:\.py)?[\"']|import\s+{stem}\b")
    return [path for path, text in test_texts.items() if pattern.search(text)]


def quickest(users: list[Path], mirror: Path, times: dict[str, list[float]]) -> set[Path]:
    """Of the many test files that import a module, the quickest (tests/test_times.json, the default run's seconds),
    those in its own tests folder first, up to ``BUDGET`` seconds: a few of them rather than none."""

    def seconds(path: Path) -> float:
        return times.get(path.relative_to(ROOT).as_posix(), [UNTIMED, UNTIMED])[1]

    taken: set[Path] = set()
    spent = 0.0
    for path in sorted(users, key=lambda p: (p.parent != mirror, seconds(p), p)):
        if taken and spent + seconds(path) > BUDGET:
            break
        taken.add(path)
        spent += seconds(path)
    return taken


def is_doc(f: str) -> bool:
    """A doc, or .ignore (which of the logs a search skips)."""
    md = f.endswith(".md") and (f.startswith(("docs/", ".claude/agents/")) or f in ("CLAUDE.md", "README.md"))
    return md or f == ".ignore"


def doc_tests(f: str, test_texts: dict[Path, str]) -> set[Path]:
    """A changed doc's tests: the rules every doc is held to (``DOC_RULES``), those of ``OWN`` and the test files that
    open it by its path (``ROOT / "docs" / "X.md"``)."""
    joined = r"/\s*[\"']" + r"[\"']\s*/\s*[\"']".join(map(re.escape, Path(f).parts)) + r"[\"']"
    readers = {path for path, text in test_texts.items() if re.search(joined, text)}
    rules = {ROOT / t for t in DOC_RULES if (ROOT / t).is_file()}
    return rules | {p for t in OWN.get(f, []) for p in ROOT.glob(t)} | readers


def own_tests(f: str, test_texts: dict[Path, str], times: dict[str, list[float]]) -> set[Path]:
    """The test files of one changed file, the rules that check the whole tree aside."""
    path = Path(f)
    if is_doc(f):
        return doc_tests(f, test_texts)
    if f in OWN:
        return {p for t in OWN[f] for p in ROOT.glob(t)}
    if path.parts[:1] == ("tests",) and path.name.startswith("test_") and path.suffix == ".py":
        return {ROOT / path} if (ROOT / path).is_file() else set()
    stem = path.parent.name if path.name == "__init__.py" else path.stem.lstrip("_")
    own = set(TESTS.rglob(f"test_{stem}*.py")) | set(TESTS.rglob(f"test_web_{stem}*.py"))
    users: list[Path] = []
    if path.parts[:1] == ("ap_coder",) and path.suffix == ".py" and path.name != "__main__.py":
        parts = path.parent.parts if path.name == "__init__.py" else path.with_suffix("").parts
        users = importers(".".join(parts), test_texts)
        if not users and path.name != "__init__.py" and (ROOT / path.parent / "__init__.py").is_file():
            # A part of a package that re-exports it (core/store/invoices.py): tests import the package.
            users = importers(".".join(path.parent.parts), test_texts)
            own |= set(TESTS.rglob(f"test_{path.parent.name}*.py"))
    elif path.parts[:1] == ("scripts",) and path.suffix == ".py":
        users = script_users(path.stem, test_texts)
    mirror = TESTS.joinpath(*path.parts[1:2]) if len(path.parts) > 2 else TESTS  # ap_coder/coding/x.py: tests/coding
    return own | (set(users) if len(users) <= WIDE else quickest(users, mirror, times))


def checks_whole_tree(f: str) -> bool:
    """Whether a change to ``f`` can break the rules that check the whole tree (``RULES``): one in the app (its
    templates and data too), the scripts, or a Python file of the tests."""
    return f.startswith(("ap_coder/", "scripts/")) or (f.startswith("tests/") and f.endswith(".py"))


def read_tests() -> tuple[dict[Path, str], dict[str, list[float]]]:
    """Every test file's text, and their seconds (tests/test_times.json: all its tests, those of the default run)."""
    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in TESTS.rglob("test_*.py")}
    times_file = TESTS / "test_times.json"
    return texts, json.loads(times_file.read_text(encoding="utf-8")) if times_file.is_file() else {}


def picks(files: list[str]) -> dict[str, set[str]]:
    """Each changed file's own test files, the rules that check the whole tree aside."""
    test_texts, times = read_tests()
    return {f: {p.relative_to(ROOT).as_posix() for p in own_tests(f, test_texts, times)} for f in files}


def tests_for(files: list[str], found: dict[str, set[str]] | None = None) -> list[str] | None:
    """The test files to run for these changed files (the rules that check the whole tree among them); None means the
    whole default run. ``found``: their :func:`picks`, when already worked out."""
    if any(f in SHARED for f in files):
        return None
    picked = set().union(*(picks(files) if found is None else found).values())
    if any(checks_whole_tree(f) for f in files):
        picked = {t for t in picked if not t.startswith(tuple(r + "/" for r in RULES))} | set(RULES)
    return sorted(picked)  # tests/x.py on Windows too


def untested(found: dict[str, set[str]]) -> list[str]:
    """The changed modules of the app (of :func:`picks`) no test file is picked for, the rules aside."""
    return [f for f, tests in found.items() if f.startswith("ap_coder/") and f.endswith(".py") and not tests
            and (ROOT / f).is_file()]  # fmt: skip


def is_rule(test: str) -> bool:
    """A test file run with its slow tests (no browser): a rule that checks the whole tree, or any of tests/meta."""
    return test in RULES or test.startswith("tests/meta/")


def changed_lines(f: str) -> set[int] | None:
    """The lines of ``f`` changed since the last commit (``git diff -U0``); None for a file git doesn't have yet."""
    try:  # (git writes UTF-8, whatever the code page Windows reads with by default before Python 3.15)
        known = subprocess.run(["git", "ls-files", "--", f], cwd=ROOT, capture_output=True, encoding="utf-8",
                               errors="replace")  # fmt: skip
        diff = subprocess.run(["git", "diff", "-U0", "HEAD", "--", f], cwd=ROOT, capture_output=True,
                              encoding="utf-8", errors="replace")  # fmt: skip
    except OSError:
        return None
    if known.returncode or diff.returncode or not known.stdout.strip():
        return None
    lines: set[int] = set()
    for start, count in re.findall(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", diff.stdout, re.M):
        lines |= set(range(int(start), int(start) + max(int(count or 1), 1)))  # (0: lines removed after start)
    return lines


def near_limits(files: list[str]) -> list[str]:
    """Changed functions of the app, and parts of the store, close to the length the meta tests allow: split them
    before adding to them (D-191)."""
    found = []
    for f in files:
        if not (f.startswith("ap_coder/") and f.endswith(".py") and (ROOT / f).is_file()):
            continue
        text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        if f.startswith("ap_coder/core/store/") and len(text.splitlines()) >= NEAR_PART:
            found.append(f"{f}: {len(text.splitlines())} lines (a part of the store stays under 1,000)")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue  # (ruff has said so)
        changed = changed_lines(f)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = node.end_lineno or node.lineno
                if end - node.lineno + 1 >= NEAR and (changed is None or changed & set(range(node.lineno, end + 1))):
                    found.append(f"{f}::{node.name}: {end - node.lineno + 1} lines (the limit is 120)")
    return found


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
    # (pytest writes a pipe in the code page this reads it with, unlike git and ruff: no encoding; a byte replaced)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=ROOT,
                          capture_output=True, text=True, errors="replace")  # fmt: skip


def run_tests(args: list[str], slow_too: bool = True) -> bool:
    """One pytest run, its failures and its last line printed: whether it failed. ``slow_too``: when every test
    picked is a slow one, run them."""
    done = pytest(args)
    if done.returncode == 5 and slow_too:
        done = pytest(["-m", "", *args])
    lines = done.stdout.strip().splitlines()
    failures = [line for line in lines if line.startswith(("FAILED", "ERROR"))]
    print("\n".join(failures[:20] + lines[-1:]))
    if done.returncode not in (0, 5) and not failures:
        print(done.stderr.strip()[-2000:] or "\n".join(lines[-30:]))
    return done.returncode not in (0, 5)


def rules_args(rules: list[str]) -> list[str]:
    """The rules' pytest arguments: their slow tests too, but no browser, and none of tests/slow_tests.txt outside
    tests/meta (the offline guard's whole walks are CI's)."""
    slow = (TESTS / "slow_tests.txt").read_text(encoding="utf-8").splitlines()
    left_out = [line.strip() for line in slow if line.split("::")[0] in rules and not line.startswith("tests/meta/")]
    return ["-m", "", "-k", "not in_a_browser", *(a for t in left_out for a in ("--deselect", t)), *rules]


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__.strip())
        return 0
    force, skip = "--walk" in argv, "--no-walk" in argv
    argv = [a for a in argv if a not in ("--walk", "--no-walk")]
    files = [f.replace("\\", "/") for f in argv] or changed_files()  # (ap_coder\x.py as typed on Windows)
    if files is None:
        print("Not a git checkout: name the files to check, e.g. python scripts/check.py ap_coder/coding/tax.py")
        return 2
    if not files and not force:
        print("Nothing changed since the last commit (name files to check them).")
        return 0
    failed = False
    python = [f for f in files if f.endswith(".py") and (ROOT / f).is_file()]
    if python:
        for cmd in (["ruff", "check", "-q", *python], ["ruff", "format", "--check", "-q", *python]):  # (in UTF-8)
            done = subprocess.run([sys.executable, "-m", *cmd], cwd=ROOT, capture_output=True, encoding="utf-8",
                                  errors="replace")  # fmt: skip
            if done.returncode:
                failed = True
                print((done.stdout + done.stderr).strip())
    found = picks(files)
    tests = tests_for(files, found) if files else []  # (--walk alone: the walk only)
    rules = list(RULES) if tests is None else [t for t in tests if is_rule(t)]
    if tests == [] and files:
        print("No tests found for these files.")
    if rules:
        print("Rules:", " ".join(rules), flush=True)
        failed |= run_tests(rules_args(rules), slow_too=False)
    if tests is None or any(not is_rule(t) for t in tests):
        picked = None if tests is None else [t for t in tests if not is_rule(t)]
        # A page's script, template or stylesheet is tested in a browser: those tests are slow, so asked for here.
        every = ["-m", ""] if any(f.endswith((".js", ".html", ".css")) for f in files) else []
        print("Tests:", " ".join(picked) if picked else "the whole default run (a file every test uses changed)",
              flush=True)  # fmt: skip
        failed |= run_tests([*every, *(picked or [])], slow_too=not every)
    for warning in near_limits(files):
        print(f"near the limit: split before adding: {warning}")
    for f in untested(found):
        print(f"NO TESTS RAN FOR {f}: name its tests in OWN (scripts/check.py), or write them")
        failed = True
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
    sys.stdout.reconfigure(errors="replace")  # (a Windows pipe's code page has no → for ruff's words)
    sys.exit(main(sys.argv[1:]))
