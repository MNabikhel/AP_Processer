"""``scripts/same_behaviour.py`` (D-124): its probes give the same output every run (else every comparison would
differ), and a difference is shown where it is."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tool():
    spec = importlib.util.spec_from_file_location("same_behaviour", ROOT / "scripts" / "same_behaviour.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_difference_is_shown_by_its_line_and_key_path():
    tool = _tool()
    before = b'{"a": 1, "b": {"c": [1, 2]}}\n{"x": 0}\n'
    after = b'{"a": 1, "b": {"c": [1, 3]}}\n{"x": 0}\n'
    assert tool.first_difference(before, after) == "line 1: /b/c[1]: '2' before, '3' after"
    assert tool.first_difference(b"1\n", b"1\n2\n") == "1 lines before, 2 after"
    assert set(tool.DEFAULT) <= set(tool.DEFAULT_N) == {p.stem for p in (ROOT / "scripts" / "probes").glob("*.py")}


def test_the_tax_probe_gives_the_same_output_each_run(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": "0"}
    runs = [subprocess.run([sys.executable, str(ROOT / "scripts" / "probes" / "tax.py"), "300", str(tmp_path / str(i))],
                           env=env, capture_output=True, check=True).stdout for i in range(2)]  # fmt: skip
    assert runs[0] == runs[1] and runs[0].count(b"\n") == 300
