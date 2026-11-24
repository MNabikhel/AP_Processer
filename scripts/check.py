"""The quick check before a commit: ruff on the changed Python files, then the tests of what changed.

    python scripts/check.py              the files changed since the last commit (and new ones)
    python scripts/check.py FILE...      these files instead
    --walk / --no-walk                   run the first-day walk whatever changed / not at all
    --fast                               the default run's tests of the files picked only (no slow ones)
    --serial                             on one core (else a long run takes AP_CHECK_WORKERS, or up to 4)
    --rules                              the rules that check the whole tree too, whatever changed (combine.py)

The tests picked for a changed module are its own (``tests/<folder>/test_<module>*.py``, the folders mirror
ap_coder/, or those named in ``OWN``), the test files that import it by name (a script: those that load it; a module
more than ``WIDE`` of them import: the quickest of them, up to ``BUDGET`` seconds) and those that name a function
the diff changed (with a test map, tests/test_map.json.gz: those that ran it, up to ``BUDGET`` seconds, D-333); a
changed test file runs itself; a changed template, page script or stylesheet runs the tests named after it, in a
browser too; a changed doc runs the rules every doc is held to and the tests that open it; a file every test uses
(conftest.py, pyproject.toml, the shared helpers...) runs the whole default run. Any change to the app, the scripts
or the tests also runs the rules that check the whole tree (``RULES``, D-329, their slow tests too). A changed
module of the app no test is picked for fails the check (``NO TESTS RAN FOR``), and a changed function near the
length limit gets a warning. The files picked run with their slow tests but no browser (``--fast``: without them; a
file's that take over ``SLOW_SECONDS`` together are CI's), on several cores when they take long (pytest-xdist: ``pip
install -e ".[parallel]"``); the rest is CI's. A change to what the pilot tester's first day walks through (a page,
its words, the guide, the launcher: ``WALK``) runs that walk too, ``scripts/ci/first_day.py`` (D-225, about two
minutes), in a temporary folder and on a free port, never the data folder. Exit code 0 when everything passes, 2
outside a git checkout without files named.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
WIDE = 12  # a module imported by more test files than this (the store, the schema) brings only the quickest of them,
BUDGET = 20.0  # up to these seconds of the default run (tests/test_times.json), its own tests aside
UNTIMED = 2.0  # the seconds taken for a test file tests/test_times.json has no time for yet
NEAR = 110  # a changed function this long (tests/meta/test_function_length.py: at most 120 lines) and
NEAR_PART = 950  # a part of the store this long (tests/core/test_store_package.py: under 1,000) get a warning
PARALLEL_SECONDS = 15  # a run longer than this (tests/test_times.json) goes on several cores (pytest-xdist)
SLOW_SECONDS = 15  # a test file whose slow tests take longer together (tests/test_times.json) leaves them to CI
OLD_WINDOWS_CLOCK = False  # True: the tests on old Windows' clock (tests/conftest.py, AP_TEST_OLD_WINDOWS_CLOCK)


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
# The rules every doc is held to (docs/, CLAUDE.md, README.md, .claude/agents/, and .ignore): its lines, its links, the
# log entries' numbers (the last one only once it is there): a changed doc runs them, and the tests that open it by its
# path.
DOC_RULES = ("tests/meta/test_agent_friendly.py", "tests/meta/test_doc_links.py", "tests/meta/test_log_entries.py")
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
    """Test files that load ``scripts/<stem>.py``: by its path, by import, or by its name in quotes in a file that
    names the scripts folder (``_script("upgrade")``; not the word in ``["coverage", "combine"]``)."""
    pattern = re.compile(rf"scripts[/\\.]{stem}\b|[\"']{stem}\.py[\"']|import\s+{stem}\b")
    named, folder = re.compile(rf"[\"']{stem}[\"']"), re.compile(r"[\"']scripts[\"'/\\]|scripts[/\\]|_script\(")
    return [path for path, text in test_texts.items()
            if pattern.search(text) or (named.search(text) and folder.search(text))]  # fmt: skip


def test_files(pattern: str = "test_*.py") -> set[Path]:
    """The test files of a name, in tests/' folders: a file directly in tests/ is none (tests/test_map.py, a module:
    tests/conftest.py's ``collect_ignore_glob``)."""
    return {path for path in TESTS.rglob(pattern) if path.parent != TESTS}


def quickest(users: list[Path], mirror: Path, times: dict[str, list[float]]) -> set[Path]:
    """Of the many test files that import a module, the quickest (tests/test_times.json, all their tests' seconds),
    those in its own tests folder first, up to ``BUDGET`` seconds: a few of them rather than none."""

    def seconds(path: Path) -> float:
        return times.get(path.relative_to(ROOT).as_posix(), [UNTIMED, UNTIMED])[0]

    taken: set[Path] = set()
    spent = 0.0
    for path in sorted(users, key=lambda p: (p.parent != mirror, seconds(p), p)):
        if taken and spent + seconds(path) > BUDGET:
            break
        taken.add(path)
        spent += seconds(path)
    return taken


def mirror(f: str) -> Path:
    """The tests folder of a module (ap_coder/coding/x.py: tests/coding; tests/ for one with none)."""
    parts = Path(f).parts
    return TESTS.joinpath(parts[1]) if len(parts) > 2 else TESTS


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


def own_tests(f: str, test_texts: dict[Path, str], times: dict[str, list[float]], said: list[str] | None = None
              ) -> set[Path]:  # fmt: skip
    """The test files of one changed file, the rules that check the whole tree aside; what the budget left out is added
    to ``said``."""
    path = Path(f)
    if is_doc(f):
        return doc_tests(f, test_texts)
    if f in OWN:
        return {p for t in OWN[f] for p in ROOT.glob(t)}
    if path.parts[:1] == ("tests",) and len(path.parts) > 2 and path.name.startswith("test_") and path.suffix == ".py":
        return {ROOT / path} if (ROOT / path).is_file() else set()
    stem = path.parent.name if path.name == "__init__.py" else path.stem.lstrip("_")
    own = test_files(f"test_{stem}*.py") | test_files(f"test_web_{stem}*.py")
    users: list[Path] = []
    if path.parts[:1] == ("ap_coder",) and path.suffix == ".py" and path.name != "__main__.py":
        parts = path.parent.parts if path.name == "__init__.py" else path.with_suffix("").parts
        users = importers(".".join(parts), test_texts)
        if not users and path.name != "__init__.py" and (ROOT / path.parent / "__init__.py").is_file():
            # A part of a package that re-exports it (core/store/invoices.py): tests import the package.
            users = importers(".".join(path.parent.parts), test_texts)
            own |= test_files(f"test_{path.parent.name}*.py")
    elif path.parts[:1] == ("scripts",) and path.suffix == ".py":
        users = script_users(path.stem, test_texts)
    if len(users) <= WIDE:
        return own | set(users)
    taken = quickest(users, mirror(f), times)
    left = sorted(p.relative_to(ROOT).as_posix() for p in set(users) - taken - own)
    if said is not None and left:
        said.append(f"{len(users)} test files use it: the quickest {len(taken)} run, {len(left)} left to CI (about "
                    f"{estimate(left, times):.0f} s more)")  # fmt: skip
    return own | taken


def checks_whole_tree(f: str) -> bool:
    """Whether a change to ``f`` can break the rules that check the whole tree (``RULES``): one in the app (its
    templates and data too), the scripts, or a Python file of the tests."""
    return f.startswith(("ap_coder/", "scripts/")) or (f.startswith("tests/") and f.endswith(".py"))


def read_tests() -> tuple[dict[Path, str], dict[str, list[float]]]:
    """Every test file's text, and their seconds (tests/test_times.json: all its tests, those of the default run)."""
    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in sorted(test_files())}
    times_file = TESTS / "test_times.json"
    return texts, json.loads(times_file.read_text(encoding="utf-8")) if times_file.is_file() else {}


def picks(files: list[str], notes: dict[str, list[str]] | None = None) -> dict[str, set[str]]:
    """Each changed file's own test files, the rules that check the whole tree aside, and those that name a function
    of it the diff changed or, by the test map, ran one. ``notes``: what to say of each file (the tests its functions
    brought, what a budget left out)."""
    test_texts, times = read_tests()
    mapped = load_map()
    found: dict[str, set[str]] = {}
    for f in files:
        said = [] if notes is None else notes.setdefault(f, [])
        found[f] = {p.relative_to(ROOT).as_posix() for p in own_tests(f, test_texts, times, said)}
        if f.startswith(("ap_coder/", "scripts/")) and f.endswith(".py"):
            for name, users in named_tests(changed_functions(f), test_texts).items():
                more = {p.relative_to(ROOT).as_posix() for p in users} - found[f]
                if more:
                    said.append(f"{name} (changed) is named in " + ", ".join(sorted(more)))
                found[f] |= more
        if mapped and f.startswith("ap_coder/") and f.endswith(".py"):
            module, test_map = mapped
            ran = [ROOT / t for t in module.tests_for(map_keys(f), test_map)[0] if t not in found[f]]
            taken = {p.relative_to(ROOT).as_posix() for p in quickest(ran, mirror(f), times)} if ran else set()
            left = sorted({p.relative_to(ROOT).as_posix() for p in ran} - taken)
            rest = f"; {len(left)} left to CI (about {estimate(left, times):.0f} s more)" if left else ""
            if taken:
                said.append(f"the test map's tests of its changed functions: {', '.join(sorted(taken))}{rest}")
            found[f] |= taken
    return found


def load_map(path: Path | None = None) -> tuple[ModuleType, Any] | None:
    """tests/test_map.py and its map of the test files that ran each function (tests/test_map.json.gz, D-333); None
    when there is no map."""
    path = path or TESTS / "test_map.json.gz"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("ap_check_test_map", TESTS / "test_map.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # (its dataclass looks itself up there)
    spec.loader.exec_module(module)
    test_map = module.load(path)
    return None if test_map is None else (module, test_map)


def map_keys(f: str) -> set[str]:
    """The map's keys of the functions of ``f`` the diff touches, their signatures and decorators too: in the file as
    it is and, for the lines taken out, as it was; all of them in a file git doesn't have yet."""
    path = ROOT / f
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    changed = changed_lines(f)
    now, was = (set(range(1, text.count("\n") + 2)), set()) if changed is None else changed
    keys = {f"{f}::{name}" for name, lines in map_spans(text).items() if lines & now}
    return keys | ({f"{f}::{name}" for name, lines in map_spans(committed(f)).items() if lines & was} if was else set())


def map_spans(text: str) -> dict[str, set[int]]:
    """The functions of a module as the test map names them (``func``, ``Class.func``; one inside a function is part
    of that one), each with its lines from its decorators on: the map is made of the lines coverage counts, the body's,
    but a changed signature or page address changes the function too."""
    found: dict[str, set[int]] = {}

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[prefix + node.name] = span(node)
            elif isinstance(node, ast.ClassDef):
                walk(node.body, f"{prefix}{node.name}.")

    try:
        walk(ast.parse(text).body, "")
    except SyntaxError:  # (ruff says so)
        return {}
    return found


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


def changed_lines(f: str) -> tuple[set[int], set[int]] | None:
    """The lines of ``f`` the diff since the last commit (``git diff -U0``) touches: of the file as it is, and those
    taken out of it as it was; None for a file git doesn't have yet (all of it new)."""
    try:  # (git writes UTF-8, whatever the code page Windows reads with by default before Python 3.15)
        known = subprocess.run(["git", "ls-files", "--", f], cwd=ROOT, capture_output=True, encoding="utf-8",
                               errors="replace")  # fmt: skip
        diff = subprocess.run(["git", "diff", "-U0", "HEAD", "--", f], cwd=ROOT, capture_output=True,
                              encoding="utf-8", errors="replace")  # fmt: skip
    except OSError:
        return None
    if known.returncode or diff.returncode or not known.stdout.strip():
        return None
    now: set[int] = set()
    was: set[int] = set()
    for old, old_count, new, new_count in re.findall(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", diff.stdout, re.M):
        was |= set(range(int(old), int(old) + int(old_count or 1)))  # (a count of 0: none)
        now |= set(range(int(new), int(new) + int(new_count or 1)))
    return now, was


def functions(text: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function and method of a module (none when it doesn't parse: ruff says so)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    return [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """A function's lines, its decorators' (a page's address) among them."""
    first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    return set(range(first, (node.end_lineno or node.lineno) + 1))


def changed_functions(f: str) -> set[str]:
    """The names of the functions (and methods) of ``f`` whose lines the diff since the last commit touches: in the
    file as it is and, for the lines taken out, as it was; every one of a file git doesn't have yet."""
    path = ROOT / f
    now = functions(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else []
    changed = changed_lines(f)
    if changed is None:
        return {node.name for node in now}
    lines, taken_out = changed
    names = {node.name for node in now if lines & span(node)}
    if taken_out:
        names |= {node.name for node in functions(committed(f)) if taken_out & span(node)}
    return names


def committed(f: str) -> str:
    """``f`` as the last commit has it ("" when it has none)."""
    was = subprocess.run(["git", "show", f"HEAD:{f}"], cwd=ROOT, capture_output=True, encoding="utf-8",
                         errors="replace")  # fmt: skip
    return was.stdout if was.returncode == 0 else ""


def named_tests(names: set[str], test_texts: dict[Path, str]) -> dict[str, set[Path]]:
    """The test files that name each function as a whole word; none for a name more than ``WIDE`` of them have (main,
    load, a method every class has)."""
    found = {}
    for name in sorted(n for n in names if not (n.startswith("__") and n.endswith("__"))):
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        users = {path for path, text in test_texts.items() if name in text and pattern.search(text)}
        if users and len(users) <= WIDE:
            found[name] = users
    return found


def estimate(tests: list[str], times: dict[str, list[float]], slow: bool = True, long: list[str] = ()) -> float:
    """The seconds these test files (or folders) take by tests/test_times.json, with their slow tests or not
    (tests/meta's always), but for the files ``long`` whose slow tests are left out; ``UNTIMED`` each for a file not
    timed yet."""
    total = 0.0
    for test in tests:
        whole = slow or test == "tests/meta" or test.startswith("tests/meta/")
        inside = [(path, both) for path, both in times.items() if path == test or path.startswith(test + "/")]
        total += sum(both[0 if whole and path not in long else 1] for path, both in inside) if inside else UNTIMED
    return total


def long_slow(tests: list[str], times: dict[str, list[float]]) -> list[str]:
    """The test files (of these, or in these folders) whose slow tests take over ``SLOW_SECONDS`` together by
    tests/test_times.json: those are left to CI."""
    return [path for path, (whole, fast) in sorted(times.items()) if whole - fast > SLOW_SECONDS
            and any(path == test or path.startswith(test + "/") for test in tests)]  # fmt: skip


def slow_tests(path: str) -> list[str]:
    """A test file's slow tests: those tests/slow_tests.txt lists, and those it marks slow itself
    (``@pytest.mark.slow``, or every one when its ``pytestmark`` is)."""
    listed = (TESTS / "slow_tests.txt").read_text(encoding="utf-8").splitlines()
    found = {line.strip() for line in listed if line.startswith(path + "::")}
    try:
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return sorted(found)
    every = any(isinstance(node, ast.Assign) and "pytestmark" in ast.unparse(node.targets[0])
                and "slow" in ast.unparse(node.value) for node in tree.body)  # fmt: skip

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                walk(node.body, f"{prefix}{node.name}::")
            elif (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test")
                and (every or any("mark.slow" in ast.unparse(d) for d in node.decorator_list))
            ):
                found.add(f"{path}::{prefix}{node.name}")

    walk(tree.body, "")
    return sorted(found)


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
        changed = changed_lines(f)
        for node in functions(text):
            lines = (node.end_lineno or node.lineno) - node.lineno + 1  # (as tests/meta/test_function_length.py counts)
            if lines >= NEAR and (changed is None or changed[0] & span(node)):
                found.append(f"{f}::{node.name}: {lines} lines (the limit is 120)")
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
    env = {**os.environ, "AP_TEST_OLD_WINDOWS_CLOCK": "1"} if OLD_WINDOWS_CLOCK else None
    # (pytest writes a pipe in the code page this reads it with, unlike git and ruff: no encoding; a byte replaced)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=ROOT,
                          capture_output=True, text=True, errors="replace", env=env)  # fmt: skip


def workers(serial: bool) -> int:
    """The cores a long run takes (pytest -n): ``AP_CHECK_WORKERS``, else one a core up to 4; one with ``--serial``
    or without pytest-xdist (``pip install -e ".[parallel]"``)."""
    if serial or importlib.util.find_spec("xdist") is None:
        return 1
    asked = os.environ.get("AP_CHECK_WORKERS", "").strip()
    return max(1, int(asked)) if asked.isdigit() else min(4, os.cpu_count() or 1)


def run_tests(tier: list[str], paths: list[str], seconds: float, serial: bool, retry: bool = True,
              left: list[str] = ()) -> bool:  # fmt: skip
    """One pytest run of these test files, on several cores when it takes long (``seconds``, its estimate), the tests
    ``left`` to CI left out: its failures and its last line printed; whether it failed, a run of no test among them.
    ``retry``: when it ran none (each a slow one, or left to CI), run them all rather than none."""
    cores = workers(serial) if seconds > PARALLEL_SECONDS else 1
    print(f"  about {seconds:.0f} s of tests (tests/test_times.json)" + (f", on {cores} cores" if cores > 1 else ""),
          flush=True)  # fmt: skip
    on = ["-n", str(cores)] if cores > 1 else []
    out = [a for test in left for a in ("--deselect", test)]
    done = pytest([*on, *tier, *out, *paths])
    if done.returncode == 5 and retry:
        done = pytest([*on, "-m", "", *paths])
    lines = done.stdout.strip().splitlines()
    failures = [line for line in lines if line.startswith(("FAILED", "ERROR"))]
    print("\n".join(failures[:20] + lines[-1:]))
    if done.returncode == 5:  # (pytest collected none: on several cores, also when a test named is not found)
        print("NO TESTS RAN of those picked: " + " ".join(paths))
    elif done.returncode and not failures:
        print(done.stderr.strip()[-2000:] or "\n".join(lines[-30:]))
    return done.returncode != 0


def rules_args(rules: list[str], times: dict[str, list[float]]) -> list[str]:
    """The rules' pytest arguments: their slow tests too, but no browser, none of the slow tests outside tests/meta
    (the offline guard's whole walks are CI's), and none of a file's in it that take long (``long_slow``: the probes
    run twice, D-331)."""
    outside = [rule for rule in rules if not rule.startswith("tests/meta") and "::" not in rule]
    inside = [path for path in long_slow(rules, times) if path.startswith("tests/meta/")]
    left = sorted({test for path in outside + inside for test in slow_tests(path)})
    return ["-m", "", "-k", "not in_a_browser", *(a for test in left for a in ("--deselect", test))]


def run_rules(rules: list[str], times: dict[str, list[float]], serial: bool = False) -> bool:
    """The rules that check the whole tree (or those of them a change brings), run as every check runs them: their
    slow tests too, no browser, none of CI's whole walks (``rules_args``), on several cores when long. Whether they
    passed."""
    print("Rules:", " ".join(rules), flush=True)
    gone = missing(rules)
    for rule in gone:
        print(f"RULE NOT FOUND: {rule} (renamed or gone? RULES in scripts/check.py names the rules)")
    rules = [rule for rule in rules if rule not in gone]  # (pytest would run none of them)
    long = long_slow([rule for rule in rules if rule.startswith("tests/meta")], times)
    for path in long:
        print(f"  {path}: its slow tests left to CI (about {times[path][0] - times[path][1]:.0f} s)")
    seconds = estimate(rules, times, slow=False, long=long)
    failed = run_tests(rules_args(rules, times), rules, seconds, serial, retry=False)  # (the others: run all the same)
    return not (failed or gone)


def missing(tests: list[str]) -> list[str]:
    """The test files, folders or tests (``path::name``) of these that the checkout doesn't have."""
    gone = []
    for test in tests:
        path, _, name = test.partition("::")
        file = ROOT / path
        if not file.exists() or (name and f"def {name.split('::')[-1].split('[')[0]}(" not in file.read_text("utf-8")):
            gone.append(test)
    return gone


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__.strip())
        return 0
    flags = ("--walk", "--no-walk", "--fast", "--serial", "--rules")
    force, skip, fast, serial, all_rules = (flag in argv for flag in flags)
    argv = [a for a in argv if a not in flags]
    files = [f.replace("\\", "/") for f in argv] or changed_files()  # (ap_coder\x.py as typed on Windows)
    if files is None:
        print("Not a git checkout: name the files to check, e.g. python scripts/check.py ap_coder/coding/tax.py")
        return 2
    if not files and not (force or all_rules):
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
    notes: dict[str, list[str]] = {}
    found = picks(files, notes)
    times = read_tests()[1]
    tests = tests_for(files, found) if files else []  # (--walk alone: the walk only)
    if all_rules and tests is not None:
        tests = sorted({t for t in tests if not is_rule(t)} | set(RULES))  # (tests/meta's among them)
    rules = list(RULES) if tests is None else [t for t in tests if is_rule(t)]
    if tests == [] and files:
        print("No tests found for these files.")
    for f, said in notes.items():
        print("".join(f"{f}: {line}\n" for line in said), end="")
    if rules:
        failed |= not run_rules(rules, times, serial)
    if tests is None or any(not is_rule(t) for t in tests):
        picked = None if tests is None else [t for t in tests if not is_rule(t)]
        # A page's script, template or stylesheet is tested in a browser: those tests are slow, so asked for here.
        browser = any(f.endswith((".js", ".html", ".css")) for f in files)
        tier = ["-m", ""] if browser else [] if fast or picked is None else ["-m", "", "-k", "not in_a_browser"]
        print("Tests:", " ".join(picked) if picked else "the whole default run (a file every test uses changed)",
              flush=True)  # fmt: skip
        long = [] if browser or not tier or picked is None else long_slow(picked, times)
        for path in long:
            print(f"{path}: its slow tests left to CI (about {times[path][0] - times[path][1]:.0f} s)")
        seconds = estimate(list(times) if picked is None else picked, times, slow=bool(tier), long=long)
        left = [test for path in long for test in slow_tests(path)]
        failed |= run_tests(tier, picked or [], seconds, serial, retry=not browser, left=left)
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
