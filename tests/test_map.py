"""Which test files run each function of the app, for picking the tests of a change by the functions it touches
(D-333): ``tests/test_map.json.gz``, keyed ``ap_coder/<module path>::func`` or ``::Class.func``, each key the test
files that ran a line of that function. Made from a coverage database whose contexts are the test files.

    pip install -e ".[testmap]"                                      the recorder, coverage (not for running the app)
    python -m tests.test_map record -- -m "" -k "not in_a_browser"   the suite under coverage, then the map
    python -m tests.test_map record -- tests/coding tests/erp        a part of it (a map of that part only)
    python -m tests.test_map build DATA                              the map from a coverage database made before

``record`` runs pytest under coverage with this module as a plugin, which names each test's file as the context of what
it runs (its fixtures' setup and teardown too); any arguments after ``--`` go to pytest. A fixture shared by tests (of a
session, a module or a class: the demo, the made-up company) is made once, so its setup is recorded under its own
context, and counts for each test file whose tests used it (written beside the data, ``fixture_uses``). Contexts made by
pytest-cov's ``--cov-context=test`` read the same. What another process runs (a browser test's server, a command a test
starts) counts for no test, so the browser tests are best left out. Lines of a function nested in another count for the
outer one; lines outside any function (imports, constants, class bodies) run at import and count for none.

Read it with ``load()`` and ``tests_for(keys)``: the test files of the keys, and whether each key is in the map (a
function no test ran, or one added since, is not: pick its tests another way). ``keys_for_lines`` names the
functions some lines of a file are in, ``stale`` the keys whose function the code no longer has.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAP_FILE = Path(__file__).parent / "test_map.json.gz"
FORMAT = 1
USES_ENV = "AP_TEST_MAP_USES"  # the folder the plugin writes in which test files used each shared fixture
SHARED = "fixture:"  # the context of a shared fixture's setup: fixture:<where it is defined>::<its name>
DATA = Path(tempfile.gettempdir()) / "ap_coder_test_map" / ".coverage"  # outside the checkout: it can be large
RC = """[run]
source = ap_coder
relative_files = True
parallel = True
patch = subprocess
"""


# --- the plugin: each test's file is the context of what it runs -------------------------------------------------


def _switch(context: str) -> None:
    try:
        import coverage
    except ImportError:
        return
    current = coverage.Coverage.current()
    if current is not None:
        current.switch_context(context)


_FILE = [""]  # the running test's file
_INSIDE: list[str] = []  # the shared fixtures being set up, the innermost last
_USES: dict[str, set[str]] = {}  # {shared fixture's context: the test files whose tests used it}


def _shared(fixturedef: object) -> str:
    return f"{SHARED}{getattr(fixturedef, 'baseid', '')}::{getattr(fixturedef, 'argname', '')}"


def pytest_runtest_logstart(nodeid: str, location: tuple) -> None:
    _FILE[0] = nodeid.split("::", 1)[0]
    _switch(_FILE[0])


def pytest_runtest_logfinish(nodeid: str, location: tuple) -> None:
    _FILE[0] = ""
    _switch("")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    info = getattr(item, "_fixtureinfo", None)  # every fixture the test needs, those its fixtures need too
    for defs in info.name2fixturedefs.values() if info is not None else ():
        if defs and defs[-1].scope != "function":
            _USES.setdefault(_shared(defs[-1]), set()).add(item.nodeid.split("::", 1)[0])


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef: object, request: object):  # noqa: ANN201 - a hook wrapper
    shared = getattr(fixturedef, "scope", "function") != "function"
    if shared:
        _INSIDE.append(_shared(fixturedef))
        _switch(_INSIDE[-1])
    try:
        yield
    finally:
        if shared:
            _INSIDE.pop()
            _switch(_INSIDE[-1] if _INSIDE else _FILE[0])


def pytest_sessionfinish(session: pytest.Session) -> None:
    folder = os.environ.get(USES_ENV)
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        uses = {name: sorted(files) for name, files in sorted(_USES.items())}
        (Path(folder) / f"uses-{os.getpid()}.json").write_text(json.dumps(uses), encoding="utf-8")


def fixture_uses(folder: Path) -> dict[str, list[str]]:
    """{a shared fixture's context: the test files that used it}, as the plugin wrote them in ``folder`` (a file
    each process: pytest's own, or each worker's)."""
    merged: dict[str, set[str]] = {}
    for kept in sorted(Path(folder).glob("uses-*.json")):
        for name, files in json.loads(kept.read_text(encoding="utf-8")).items():
            merged.setdefault(name, set()).update(files)
    return {name: sorted(files) for name, files in sorted(merged.items())}


# --- functions and their lines ------------------------------------------------------------------------------------


def functions(source: str) -> dict[str, tuple[int, int]]:
    """{``func`` / ``Class.func``: (its body's first line, its last line)} of a module: each function at its top level
    and each method of its classes (``Outer.Inner.func`` for a class in a class). A function inside a function is
    part of the outer one."""
    found: dict[str, tuple[int, int]] = {}

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[prefix + node.name] = (node.body[0].lineno, node.end_lineno or node.lineno)
            elif isinstance(node, ast.ClassDef):
                walk(node.body, f"{prefix}{node.name}.")

    walk(ast.parse(source).body, "")
    return found


def keys_for_lines(path: str, lines: set[int] | list[int], source: str | None = None) -> set[str]:
    """The keys of the functions that hold any of ``lines`` of ``path`` (relative to the repository, as in a diff;
    ``source``: its text, else read from the checkout)."""
    text = source if source is not None else (ROOT / path).read_text(encoding="utf-8")
    spans = functions(text)
    return {f"{path}::{name}" for name, (first, last) in spans.items() if any(first <= n <= last for n in lines)}


# --- making the map --------------------------------------------------------------------------------------------------


def _test_file(context: str) -> str | None:
    """The test file of a context: ours (``tests/x/test_y.py``) or pytest-cov's (``tests/x/test_y.py::test_z|run``)."""
    file = context.split("::", 1)[0]
    return file if file.startswith("tests/") and file.endswith(".py") else None


def build(contexts: dict[str, dict[int, list[str]]], root: Path = ROOT,
          uses: dict[str, list[str]] | None = None) -> dict:  # fmt: skip
    """The map from ``{file: {line: [the contexts that ran it]}}`` (a coverage database's ``contexts_by_lineno``,
    files relative to ``root``): only the app's files and test contexts, each function's test files sorted. A shared
    fixture's context counts for the test files ``uses`` says used it."""
    by_key: dict[str, set[str]] = {}
    for file, lines in contexts.items():
        if not (file.startswith("ap_coder/") and file.endswith(".py")) or not (root / file).is_file():
            continue
        spans = functions((root / file).read_text(encoding="utf-8")).items()  # (they never overlap)
        for line, said in lines.items():
            tests = {t for t in map(_test_file, said) if t}
            tests.update(t for c in said if c.startswith(SHARED) for t in (uses or {}).get(c, ()))
            name = next((n for n, (first, last) in spans if first <= line <= last), None)
            if tests and name:
                by_key.setdefault(f"{file}::{name}", set()).update(tests)
    tests = sorted(set().union(*by_key.values())) if by_key else []
    index = {t: i for i, t in enumerate(tests)}
    return {"format": FORMAT, "tests": tests,
            "map": {key: sorted(index[t] for t in found) for key, found in sorted(by_key.items())}}  # fmt: skip


def write(made: dict, path: Path = MAP_FILE) -> int:
    """``made`` as gzipped JSON, the same bytes for the same map (no time in the gzip header); its size."""
    data = gzip.compress(json.dumps(made, separators=(",", ":"), sort_keys=True).encode("utf-8"), 9, mtime=0)
    path.write_bytes(data)
    return len(data)


def from_coverage(data_file: Path) -> dict[str, dict[int, list[str]]]:
    """``{file: {line: [contexts]}}`` of a coverage database (its files relative to the repository)."""
    import coverage

    data = coverage.CoverageData(basename=str(data_file))
    data.read()
    out = {}
    for measured in data.measured_files():
        path = Path(measured)
        rel = (path.resolve().relative_to(ROOT) if path.is_absolute() else path).as_posix()
        out[rel] = {line: list(said) for line, said in data.contexts_by_lineno(measured).items()}
    return out


def record(pytest_args: list[str], data_file: Path = DATA) -> int:
    """The tests run under coverage, this module their plugin (the exit code is pytest's)."""
    data_file.parent.mkdir(parents=True, exist_ok=True)
    for old in [*data_file.parent.glob(data_file.name + "*"), *(data_file.parent / "uses").glob("uses-*.json")]:
        old.unlink()
    rc = data_file.parent / "coveragerc"
    rc.write_text(RC, encoding="utf-8")
    env = {**os.environ, "COVERAGE_PROCESS_START": str(rc), USES_ENV: str(data_file.parent / "uses")}
    run = [sys.executable, "-m", "coverage", "run", f"--rcfile={rc}", f"--data-file={data_file}", "-m", "pytest",
           "-p", "tests.test_map", "-p", "no:cacheprovider", *pytest_args]  # fmt: skip
    done = subprocess.run(run, cwd=ROOT, env=env).returncode
    subprocess.run([sys.executable, "-m", "coverage", "combine", f"--rcfile={rc}", f"--data-file={data_file}"],
                   cwd=ROOT, env=env, check=True)  # fmt: skip
    return done


# --- reading it -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TestMap:
    """A map as read: the test files, and each key's indices into them."""

    __test__ = False  # (not a pytest test class)
    tests: tuple[str, ...]
    keys: dict[str, tuple[int, ...]]

    def files(self, key: str) -> list[str] | None:
        """The test files of ``key``; None when it is not in the map. ``Store.func`` of a store part is the class
        there that has it (the store is made of a class a part)."""
        found = self.keys.get(key)
        path, _, name = key.partition("::")
        if found is None and name.startswith("Store.") and path.startswith("ap_coder/core/store/"):
            method = name.removeprefix("Store.")
            alike = [k for k in self.keys if k.partition("::")[0] == path
                     and k.partition("::")[2].count(".") == 1 and k.endswith(f".{method}")]  # fmt: skip
            found = tuple(sorted({i for k in alike for i in self.keys[k]})) if alike else None
        return None if found is None else [self.tests[i] for i in found]


def load(path: Path = MAP_FILE) -> TestMap | None:
    """The map, None when there is none (or of another format)."""
    try:
        made = json.loads(gzip.decompress(path.read_bytes()))
    except OSError:
        return None
    if made.get("format") != FORMAT:
        return None
    return TestMap(tuple(made["tests"]), {key: tuple(found) for key, found in made["map"].items()})


def tests_for(keys: list[str] | set[str], test_map: TestMap | None = None) -> tuple[list[str], dict[str, bool]]:
    """(the test files of ``keys``, sorted, those since deleted left out; {key: whether it is in the map}). With no
    map, no files and no key in it."""
    test_map = test_map if test_map is not None else load()
    files: set[str] = set()
    known = {}
    for key in keys:
        found = test_map.files(key) if test_map is not None else None
        known[key] = found is not None
        files.update(found or [])
    return sorted(f for f in files if (ROOT / f).is_file()), known


def stale(test_map: TestMap | None = None, root: Path = ROOT) -> list[str]:
    """The map's keys whose function the code no longer has (renamed, moved or gone since it was made)."""
    test_map = test_map if test_map is not None else load()
    if test_map is None:
        return []
    have: dict[str, set[str]] = {}
    gone = []
    for key in test_map.keys:
        path, _, name = key.partition("::")
        if path not in have:
            file = root / path
            have[path] = set(functions(file.read_text(encoding="utf-8"))) if file.is_file() else set()
        if name not in have[path]:
            gone.append(key)
    return sorted(gone)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="run the tests under coverage, then write the map")
    rec.add_argument("pytest_args", nargs="*", help="after --: what pytest is given")
    rec.add_argument("--data", type=Path, default=DATA, help=f"the coverage database (default {DATA})")
    made = sub.add_parser("build", help="write the map from a coverage database")
    made.add_argument("data", type=Path, nargs="?", default=DATA)
    for each in (rec, made):
        each.add_argument("--out", type=Path, default=MAP_FILE, help=f"the map (default {MAP_FILE.name})")
    args = parser.parse_args(argv)
    data_file = args.data
    if args.command == "record":
        code = record(args.pytest_args, data_file)
        if code not in (0, 1):  # (1: some tests failed; the map is still made of what ran)
            return code
    result = build(from_coverage(data_file), uses=fixture_uses(data_file.parent / "uses"))
    size = write(result, args.out)
    print(f"{len(result['map'])} functions, {len(result['tests'])} test files: {args.out} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
