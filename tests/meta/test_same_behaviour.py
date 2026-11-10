"""``scripts/same_behaviour.py`` (D-124): its probes give the same output every run (else every comparison would
differ), and a difference is shown where it is."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_with_no_probes_named_the_default_ones_run():
    tool = _tool()
    assert tool.parse([]).probes == list(tool.DEFAULT)
    assert tool.parse(["pipeline", "--n", "3"]).probes == ["pipeline"]
    with pytest.raises(SystemExit):
        tool.parse(["taxes"])


def test_the_code_compared_with_sees_the_models_this_checkout_has(tmp_path):
    """The optional models a checkout holds (``models/``, git-ignored: the table finder) change what is read; the
    checkout of the ref has none, so a comparison showed a difference no code made (B-1699)."""
    tool = _tool()
    root, before = tmp_path / "root", tmp_path / "before"
    (root / "models").mkdir(parents=True)
    (root / "models" / "finder.onnx").write_bytes(b"model")
    before.mkdir()
    tool.share_models(root, before)
    assert (before / "models" / "finder.onnx").read_bytes() == b"model"
    bare = tmp_path / "bare"
    bare.mkdir()
    tool.share_models(tmp_path / "none", bare)
    assert not (bare / "models").exists()


def test_the_migrate_probe_leaves_out_the_clock_time_a_database_is_brought_up_to_date(tmp_path, monkeypatch):
    """B-2306: bringing a database up to date writes when it began to keep its coding rules' use
    (``rule_use_since``, D-217): the clock's time, which the probe printed with the settings. The two trees run side
    by side a second apart, so ``migrate`` said DIFFERENT for a change that made none. What the clock writes is left
    out, as the id made anew each time is: two databases brought up to date at different times read the same."""
    from ap_coder.core.store import Store, migrations

    spec = importlib.util.spec_from_file_location("migrate_probe", ROOT / "scripts" / "probes" / "migrate.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    read = []
    for n, at in enumerate(("2026-11-10T19:20:19", "2026-11-10T19:20:20")):
        monkeypatch.setattr(migrations, "_now", lambda at=at: at)
        Store(tmp_path / f"{n}.db")
        read.append(probe.contents(tmp_path / f"{n}.db"))
    assert dict(read[0]["tables"]["settings"])["rule_use_since"] == "(new each time)"
    assert read[0] == read[1]
