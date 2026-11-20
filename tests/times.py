"""The time each test file takes, for CI's split into parts (``--shard``, tests/conftest.py ``_shard``).

``python -m pytest -q -m "" --junitxml=times.xml`` (the whole suite, about half an hour), then
``python -m tests.times times.xml`` writes tests/test_times.json: for each file, the seconds of all its tests and of
its tests that are not slow (the fast tier's). Refresh it when the parts of a CI run grow uneven (D-233). Several
reports (a folder at a time, a CI part each) add up: the files they measured get these times, the others keep theirs
(a file that is gone leaves). ``--slow-over 2``: each test of the default run that took over 2 seconds goes on
tests/slow_tests.txt first, as its rule says.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMES_FILE = Path(__file__).parent / "test_times.json"
SLOW_FILE = Path(__file__).parent / "slow_tests.txt"


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
    kept = json.loads(times_file.read_text(encoding="utf-8")) if times_file.is_file() else {}
    for report in reports:
        kept.update(times(report, slow | new, root))
    measured = {path: kept[path] for path in sorted(kept) if (root / path).is_file()}
    times_file.write_text(json.dumps(measured, indent=1) + "\n", encoding="utf-8")
    return new, measured


if __name__ == "__main__":
    import argparse

    from tests.conftest import _SLOW

    parser = argparse.ArgumentParser(prog="python -m tests.times", description=__doc__.split("\n\n")[0])
    parser.add_argument("reports", nargs="+", type=Path, help="pytest --junitxml reports")
    parser.add_argument(
        "--slow-over", type=float, metavar="SECONDS", help="put default-run tests over it on the slow list"
    )
    args = parser.parse_args()
    new, measured = refresh(args.reports, set(_SLOW), args.slow_over)
    print(f"{len(new)} tests put on {SLOW_FILE.name}" + "".join(f"\n  {test}" for test in sorted(new)))
    print(f"{len(measured)} files, {sum(t[0] for t in measured.values()) / 60:.1f} minutes "
          f"({sum(t[1] for t in measured.values()) / 60:.1f} without the slow tests): {TIMES_FILE}")  # fmt: skip
