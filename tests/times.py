"""The time each test file takes, for CI's split into parts (``--shard``, tests/conftest.py ``_shard``).

``python -m pytest -q -m "" --junitxml=times.xml`` (the whole suite, about half an hour), then
``python -m tests.times times.xml`` writes tests/test_times.json: for each file, the seconds of all its tests and of
its tests that are not slow (the fast tier's). Refresh it when the parts of a CI run grow uneven (D-233).
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMES_FILE = Path(__file__).parent / "test_times.json"


def times(junit: Path, slow: set[str], root: Path = ROOT) -> dict[str, list[float]]:
    """{test file: [seconds of all its tests, seconds of those not slow]} from pytest's ``--junitxml`` report.
    ``slow``: the slow tests' ids without parameters (tests/slow_tests.txt); a browser test is slow too."""
    found: dict[str, list[float]] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        parts = (case.get("classname") or "").split(".")
        inside: list[str] = []
        while parts and not (root / Path(*parts)).with_suffix(".py").is_file():
            inside.insert(0, parts.pop())  # (a test class: part of the id, not of the file)
        if not parts:
            continue
        path, name = "/".join(parts) + ".py", case.get("name") or ""
        base = "::".join([path, *inside, name.split("[", 1)[0]])
        seconds = float(case.get("time") or 0)
        both = found.setdefault(path, [0.0, 0.0])
        both[0] += seconds
        if base not in slow and "in_a_browser" not in name:
            both[1] += seconds
    return {path: [round(whole, 1), round(fast, 1)] for path, (whole, fast) in sorted(found.items())}


if __name__ == "__main__":
    from tests.conftest import _SLOW

    measured = times(Path(sys.argv[1]), _SLOW)
    TIMES_FILE.write_text(json.dumps(measured, indent=1) + "\n", encoding="utf-8")
    print(f"{len(measured)} files, {sum(t[0] for t in measured.values()) / 60:.1f} minutes "
          f"({sum(t[1] for t in measured.values()) / 60:.1f} without the slow tests): {TIMES_FILE}")  # fmt: skip
