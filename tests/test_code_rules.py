"""Bug lessons that came back: each rule here turns a lesson docs/BUGS.md logged more than once into a check of the
whole code, so the same mistake cannot come back in a new place."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys

import pytest

from .conftest import ROOT

CODE = ("ap_coder", "scripts", "tests")


def test_every_text_file_is_read_and_written_with_its_encoding():
    """B-78, B-104: a text file read without naming its encoding is read in the computer's code page (cp1252 on
    Windows), so a Notepad file with a byte order mark or a French accent comes in wrong. Ruff knows which open()
    and read_text() calls are text; its rule PLW1514 is still a preview rule, so it runs here on its own, not in the
    project's lint settings."""
    if importlib.util.find_spec("ruff") is None:
        pytest.skip("ruff isn't installed (pip install -e '.[dev]')")
    run = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--preview", "--select", "PLW1514", "--output-format", "concise",
         "--no-cache", *CODE],
        cwd=ROOT, capture_output=True, text=True,
    )  # fmt: skip
    assert run.returncode == 0, "name the encoding (usually encoding='utf-8'):\n" + run.stdout + run.stderr


def _is_mark(node: ast.AST) -> bool:
    """ "?", "?, " or ["?"]: one placeholder, to be repeated."""
    if isinstance(node, ast.List) and len(node.elts) == 1:
        node = node.elts[0]
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip(" ,") == "?"


def _placeholders_for_a_list_of_any_length(source: str) -> list[int]:
    """Lines building SQL placeholders as one "?" per item of something that is not a constant (an UPPER_CASE name),
    unless the line says why the list is short (a comment with "at most", e.g. chunks of 500)."""
    text = source.splitlines()
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult)):
            continue
        sides = (node.left, node.right)
        mark = next((s for s in sides if _is_mark(s)), None)
        if mark is None:
            continue
        counted = node.right if mark is node.left else node.left
        if isinstance(counted, ast.Call) and getattr(counted.func, "id", "") == "len" and counted.args:
            counted = counted.args[0]
        if not (isinstance(counted, ast.Name) and counted.id.isupper()) and "at most" not in text[node.lineno - 1]:
            lines.append(node.lineno)
    return sorted(lines)


def test_no_sql_placeholders_are_made_one_per_item_of_a_list():
    """B-10, B-29, B-30: "?" repeated once per id fails past SQLite's variable limit (999 on older builds) as soon as
    the list grows with the database. Id lists are bound as one JSON parameter instead (store.py, json_each)."""
    found = []
    for folder in ("ap_coder", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            source = path.read_text(encoding="utf-8-sig")
            found += [f"{path.relative_to(ROOT)}:{line}" for line in _placeholders_for_a_list_of_any_length(source)]
    assert not found, "bind the ids as one JSON parameter (json_each), see store.py: " + ", ".join(found)


def test_the_placeholder_rule_finds_what_it_should():
    """The rule above, on the patterns the three bugs had and on the one form that is safe."""
    bad = """
q = ",".join("?" * len(ids))
q = ", ".join(["?"] * len(ids))
q = "?, " * len(rows)
"""
    safe = """
q = ",".join("?" * len(ACTIVE_STATUSES))
q = ",".join("?" * len(chunk))  # at most 500 ids
"""
    assert _placeholders_for_a_list_of_any_length(bad) == [2, 3, 4]
    assert _placeholders_for_a_list_of_any_length(safe) == []
