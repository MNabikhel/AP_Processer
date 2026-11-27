"""The time each test file takes, for CI's split into parts (``--shard``, tests/conftest.py ``_shard``).

``python -m pytest -q -m "" --junitxml=times.xml`` (the whole suite, about half an hour), then
``python -m tests.times times.xml`` writes tests/test_times.json: for each file, the seconds of all its tests and of
its tests that are not slow (the fast tier's). Refresh it when the parts of a CI run grow uneven (D-233). Several
reports (a folder at a time, a CI part each) add up: the files they measured get these times, the others keep theirs
(a file that is gone leaves). ``--slow-over 2``: each test of the default run that took over 2 seconds goes on
tests/slow_tests.txt first, as its rule says. A test marked slow in its own file counts as slow (``marked_slow``).

CI measures them every night (D-339): ``python -m tests.times ci FOLDER --out DIR`` works out, from the reports of a
run's tests parts (their artifacts, ``junit-<OS>-python-<version>-<whole or fast>-<n>/junit.xml``), each file's time
on Ubuntu (tests/test_times.json) and on Windows (tests/test_times_windows.json, which ``_shard`` reads there): the
mean of the Pythons that ran the whole suite. The nightly puts them on the data branch ``test-times``, with the test
map (``python -m tests.test_map merge``), and ``python -m tests.times pull`` brings them into a checkout before a pull
request (only with ``AP_ALLOW_INTERNET=1``: it asks raw.githubusercontent.com; ``--from`` another address, or a
folder's ``file://`` one).
"""

from __future__ import annotations

import ast
import datetime as dt
import gzip
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMES_FILE = Path(__file__).parent / "test_times.json"
WINDOWS_FILE = Path(__file__).parent / "test_times_windows.json"  # Windows' own (``_shard`` there, D-339)
SLOW_FILE = Path(__file__).parent / "slow_tests.txt"
# The data branch the nightly puts them on (D-339), of the repository the releases come from (scripts/releases.py)
DATA_BRANCH = "test-times"
RAW = f"https://raw.githubusercontent.com/MNabikhel/AP_Processer/{DATA_BRANCH}/"
PULLED = ("test_times.json", "test_times_windows.json", "test_map.json.gz")  # into tests/, as named
MADE = "made.json"  # where they came from: {"commit", "run", "date"}


def _cases(junit: Path, root: Path = ROOT) -> list[tuple[str, str, str, float]]:
    """(test file, test id without parameters, name, seconds) of each test in pytest's ``--junitxml`` report."""
    found = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        parts = (case.get("classname") or "").split(".")
        inside: list[str] = []
        while parts and not (root / Path(*parts)).with_suffix(".py").is_file():
            inside.insert(0, parts.pop())  # (a test class: part of the id, not of the file)
        if not parts:
            continue
        path, name = "/".join(parts) + ".py", case.get("name") or ""
        found.append((path, "::".join([path, *inside, name.split("[", 1)[0]]), name, float(case.get("time") or 0)))
    return found


def times(junit: Path, slow: set[str], root: Path = ROOT) -> dict[str, list[float]]:
    """{test file: [seconds of all its tests, seconds of those not slow]} from pytest's ``--junitxml`` report.
    ``slow``: the slow tests' ids without parameters (tests/slow_tests.txt); a browser test is slow too."""
    found: dict[str, list[float]] = {}
    for path, base, name, seconds in _cases(junit, root):
        both = found.setdefault(path, [0.0, 0.0])
        both[0] += seconds
        if base not in slow and "in_a_browser" not in name:
            both[1] += seconds
    return {path: [round(whole, 1), round(fast, 1)] for path, (whole, fast) in sorted(found.items())}


def slower(junit: Path, slow: set[str], seconds: float, root: Path = ROOT) -> set[str]:
    """The tests of the default run (not slow, no browser) that took over ``seconds``, by id without parameters."""
    return {base for _, base, name, took in _cases(junit, root)
            if took > seconds and base not in slow and "in_a_browser" not in name}  # fmt: skip


def put_on_the_slow_list(tests: set[str], listed: Path = SLOW_FILE) -> None:
    """``tests`` added to tests/slow_tests.txt, its comment kept at the top and its tests in order."""
    lines = listed.read_text(encoding="utf-8").splitlines()
    said = [line for line in lines if line.startswith("#")]
    kept = {line.strip() for line in lines if line.strip() and not line.startswith("#")}
    listed.write_text("\n".join([*said, *sorted(kept | tests)]) + "\n", encoding="utf-8")


def _slow_mark(node: ast.expr) -> bool:
    """``pytest.mark.slow`` (or a list holding it)."""
    if isinstance(node, (ast.List, ast.Tuple)):
        return any(_slow_mark(item) for item in node.elts)
    return ast.unparse(node) == "pytest.mark.slow"


def marked_slow(root: Path = ROOT) -> set[str]:
    """The tests marked slow in their own file (``@pytest.mark.slow``, or ``pytestmark`` for the whole module), by id:
    slow as those of tests/slow_tests.txt, though a ``--junitxml`` report doesn't say so."""
    found = set()
    for path in sorted((root / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        name = path.relative_to(root).as_posix()
        whole = any(isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in node.targets)
                    and _slow_mark(node.value) for node in tree.body)  # fmt: skip
        tests = [([], node) for node in tree.body]
        tests += [([node.name], item) for node in tree.body if isinstance(node, ast.ClassDef) for item in node.body]
        for inside, node in tests:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                if whole or any(_slow_mark(d) for d in node.decorator_list):
                    found.add("::".join([name, *inside, node.name]))
    return found


def refresh(
    reports: list[Path],
    slow: set[str],
    slow_over: float | None = None,
    root: Path = ROOT,
    times_file: Path = TIMES_FILE,
    slow_file: Path = SLOW_FILE,
) -> tuple[set[str], dict[str, list[float]]]:
    """The tests put on the slow list (``slow_over``: those of the default run over it), and the times written: the
    files the reports measured get theirs, the others keep theirs, a file that is gone leaves."""
    new = (
        set().union(*(slower(report, slow, slow_over, root) for report in reports)) if slow_over is not None else set()
    )
    if new:
        put_on_the_slow_list(new, slow_file)
    measured: dict[str, list[float]] = {}
    for report in reports:
        measured.update(times(report, slow | new, root))
    kept = _merged(_read(times_file), measured, root)
    _write(times_file, kept)
    return new, kept


def _read(path: Path) -> dict[str, list[float]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _write(path: Path, kept: dict[str, list[float]]) -> None:
    path.write_text(json.dumps(kept, indent=1) + "\n", encoding="utf-8")


def _merged(kept: dict[str, list[float]], measured: dict[str, list[float]], root: Path = ROOT) -> dict:
    """``kept`` with the files ``measured`` has at its times, a file that is gone from ``root`` left out, in order."""
    both = {**kept, **measured}
    return {path: both[path] for path in sorted(both) if (root / path).is_file()}


def listed_slow(listed: Path = SLOW_FILE) -> set[str]:
    """The tests on tests/slow_tests.txt, as tests/conftest.py reads them (``_SLOW``), with no pytest to import."""
    lines = listed.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


# --- CI's reports, every night (D-339) ---------------------------------------------------------------------------


def cells(folder: Path) -> dict[str, list[Path]]:
    """The reports of CI's tests parts in ``folder``, one artifact folder each (``junit-<cell>-<n>/junit.xml``), by
    cell: ``<OS>-python-<version>-<whole or fast>``, each with its parts' reports."""
    found: dict[str, list[Path]] = {}
    for report in sorted(folder.glob("junit-*/junit.xml")):
        cell = report.parent.name.removeprefix("junit-").rpartition("-")[0]
        found.setdefault(cell, []).append(report)
    return found


def mean_times(by_cell: list[list[Path]], slow: set[str], root: Path = ROOT) -> dict[str, list[float]]:
    """Each file's time in each cell (its parts' reports together), then their mean over the cells that ran it."""
    pairs: dict[str, list[list[float]]] = {}
    for reports in by_cell:
        cell: dict[str, list[float]] = {}
        for report in reports:
            cell.update(times(report, slow, root))
        for path, pair in cell.items():
            pairs.setdefault(path, []).append(pair)
    return {path: [round(sum(p[0] for p in got) / len(got), 1), round(sum(p[1] for p in got) / len(got), 1)]
            for path, got in sorted(pairs.items())}  # fmt: skip


# Which cells measure each file: those that ran the whole suite (the fast tier's cells have no slow test's time)
FROM_CELLS = {
    "test_times.json": ("ubuntu-latest-", TIMES_FILE),
    "test_times_windows.json": ("windows-latest-", WINDOWS_FILE),
}


def from_ci(folder: Path, out: Path, slow: set[str], root: Path = ROOT, made: dict | None = None,
            kept: dict[str, Path] | None = None) -> dict[str, dict[str, list[float]]]:  # fmt: skip
    """tests/test_times.json and tests/test_times_windows.json as the reports in ``folder`` measured them, written in
    ``out``: each file at the mean of the whole-suite cells of its OS that ran it, the others as the checkout has them
    (``kept``), a file that is gone left out; and ``made`` (where they came from) as made.json. No report: an error."""
    found = cells(folder)
    if not found:
        raise SystemExit(f"no --junitxml report in {folder} (junit-*/junit.xml): nothing measured, nothing written")
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, (os_name, default) in FROM_CELLS.items():
        whole = [reports for cell, reports in found.items() if cell.startswith(os_name) and cell.endswith("-whole")]
        written[name] = _merged(_read((kept or {}).get(name, default)), mean_times(whole, slow, root), root)
        _write(out / name, written[name])
    if made is not None:
        (out / MADE).write_text(json.dumps(made, indent=1) + "\n", encoding="utf-8")
    return written


def _fetch(url: str) -> bytes | None:
    """The file at ``url``; None when there is none (404, or no such file)."""
    try:
        with urllib.request.urlopen(url, timeout=60) as answer:  # noqa: S310 - a developer's command, its own address
            return answer.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise SystemExit(f"{url}: {exc}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, FileNotFoundError):
            return None
        raise SystemExit(f"{url}: {exc.reason}") from exc


def _good_times(data: bytes) -> dict[str, list[float]] | None:
    try:
        got = json.loads(data)
    except ValueError:
        return None
    ok = isinstance(got, dict) and all(isinstance(path, str) and isinstance(pair, list) and len(pair) == 2
                                       and all(isinstance(t, (int, float)) for t in pair) and pair[0] >= pair[1] >= 0
                                       for path, pair in got.items())  # fmt: skip
    return got if ok else None


def _good_map(data: bytes) -> bool:
    try:
        made = json.loads(gzip.decompress(data))
    except (OSError, ValueError):
        return False
    return isinstance(made, dict) and made.get("format") == 1 and "tests" in made and "map" in made


def pull(source: str = RAW, into: Path = TIMES_FILE.parent, root: Path = ROOT) -> list[str]:
    """The nightly's files from the data branch (``source``: its raw address, or a folder's ``file://`` one) into
    ``into`` (tests/): the times of the files it measured over this checkout's (one added since keeps its own; one
    gone leaves), the test map as it is. A file it doesn't have, or not in its format, is left as it was. Asking an
    address on the network needs ``AP_ALLOW_INTERNET=1`` (a developer's command: the app and its tests never do)."""
    from ap_coder.ops.offline import INTERNET_ENV, internet_allowed

    if not source.startswith("file:") and not internet_allowed():
        raise SystemExit(f"pull asks {source}: only with {INTERNET_ENV}=1 (the app and its tests never reach the "
                         "internet); --from file:///<folder>/ takes them from a folder")  # fmt: skip
    source = source if source.endswith("/") else source + "/"
    said = []
    made = _fetch(source + MADE)
    if made is not None:
        try:
            came = json.loads(made)
            said.append(
                f"from main at {str(came.get('commit', ''))[:7]}, {came.get('date', '')} ({came.get('run', '')})"
            )
        except ValueError:
            pass
    for name in PULLED:
        data = _fetch(source + name)
        if data is None:
            said.append(f"{name}: not there, left as it was")
        elif name.endswith(".json"):
            got = _good_times(data)
            if got is None:
                said.append(f"{name}: not times in their format, left as it was")
                continue
            kept = _merged(_read(into / name), got, root)
            _write(into / name, kept)
            said.append(f"{name}: {len(kept)} files, {sum(t[0] for t in kept.values()) / 60:.1f} minutes "
                        f"({sum(t[1] for t in kept.values()) / 60:.1f} without the slow tests)")  # fmt: skip
        elif not _good_map(data):
            said.append(f"{name}: not a test map in its format, left as it was")
        else:
            (into / name).write_bytes(data)
            said.append(f"{name}: {len(data):,} bytes")
    return said


def main(argv: list[str] | None = None) -> int:
    import argparse

    argv = sys.argv[1:] if argv is None else argv
    prog = "python -m tests.times"
    if argv[:1] == ["pull"]:
        parser = argparse.ArgumentParser(prog=f"{prog} pull", description=pull.__doc__.split("\n\n")[0])
        parser.add_argument("--from", dest="source", default=RAW, help=f"where from (default {RAW})")
        args = parser.parse_args(argv[1:])
        for line in pull(args.source):
            print(line)
        print("Commit them with your pull request: CI splits its parts by them, check.py picks tests by them.")
        return 0
    if argv[:1] == ["ci"]:
        parser = argparse.ArgumentParser(prog=f"{prog} ci", description=from_ci.__doc__.split("\n\n")[0])
        parser.add_argument("folder", type=Path, help="the run's junit-* artifacts, one folder each")
        parser.add_argument("--out", type=Path, required=True, help="the folder to write them in")
        parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""), help="the commit the run tested")
        parser.add_argument("--run", default="", help="the run's address")
        args = parser.parse_args(argv[1:])
        made = {"commit": args.commit, "run": args.run, "date": dt.date.today().isoformat()}
        written = from_ci(args.folder, args.out, listed_slow() | marked_slow(), made=made)
        for name, kept in written.items():
            print(f"{args.out / name}: {len(kept)} files, {sum(t[0] for t in kept.values()) / 60:.1f} minutes")
        return 0
    parser = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0])
    parser.add_argument("reports", nargs="+", type=Path, help="pytest --junitxml reports")
    parser.add_argument(
        "--slow-over", type=float, metavar="SECONDS", help="put default-run tests over it on the slow list"
    )
    args = parser.parse_args(argv)
    new, measured = refresh(args.reports, listed_slow() | marked_slow(), args.slow_over)
    print(f"{len(new)} tests put on {SLOW_FILE.name}" + "".join(f"\n  {test}" for test in sorted(new)))
    print(f"{len(measured)} files, {sum(t[0] for t in measured.values()) / 60:.1f} minutes "
          f"({sum(t[1] for t in measured.values()) / 60:.1f} without the slow tests): {TIMES_FILE}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
