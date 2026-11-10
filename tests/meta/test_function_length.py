"""No function of the app grows past a length a reader can hold in mind (D-191): a longer one is split into parts that
each say what they do. The few longer today are listed with their length then, and may only shrink."""

from __future__ import annotations

import ast

from tests.conftest import ROOT

# Chosen from the measured lengths (Nov 2026: 5,144 functions after the longest were split; 38 over 100 lines, 19
# over 120, 6 over 150).
LIMIT = 120

# The functions over the limit when it was set, at their length then (lines from ``def`` to the last line). Each may
# shrink, never grow; one split under the limit (or gone) comes off the list.
LONG_FUNCTIONS = {
    "ap_coder/bench/__main__.py::main": 269,
    "ap_coder/bench/coding.py::simulate": 203,
    "ap_coder/bench/learning.py::_simulate": 167,
    "ap_coder/capture/reader/entry.py::_read_fields": 151,
    "ap_coder/erp/jde_zfiles.py::voucher_rows": 148,
    "ap_coder/bench/learning.py::render_markdown": 146,
    "ap_coder/core/schema.py::build_json_schema": 140,
    "ap_coder/bench/harness.py::render_markdown": 138,
    "ap_coder/bench/generator.py::make_invoice": 136,
    "ap_coder/bench/day.py::Run.report": 133,
    "ap_coder/bench/coding.py::render_markdown": 132,
    "ap_coder/coding/vendors.py::vendor_findings": 132,
    "ap_coder/erp/insights.py::compute": 127,
    "ap_coder/erp/exports.py::sheet_workbook": 125,
    "ap_coder/bench/day.py::markdown": 124,
}


def function_lengths(source: str, where: str) -> dict[str, int]:
    """Every function and method of ``source`` ("path::Class.method", "path::outer.inner") and its length in lines."""
    found: dict[str, int] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[f"{where}::{prefix}{child.name}"] = (child.end_lineno or child.lineno) - child.lineno + 1
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(ast.parse(source), "")
    return found


def _app_functions() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted((ROOT / "ap_coder").rglob("*.py")):
        found |= function_lengths(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
    return found


def _too_long(found: dict[str, int], listed: dict[str, int]) -> list[str]:
    """What breaks the rule: a function over the limit not listed, a listed one grown, one listed but now under the
    limit or gone (to take off the list)."""
    out = []
    for name, lines in sorted(found.items()):
        if lines > LIMIT and name not in listed:
            out.append(f"{name}: {lines} lines (the limit is {LIMIT}): split it into parts that say what they do")
        elif name in listed and lines > listed[name]:
            out.append(f"{name}: {lines} lines, grown from {listed[name]}: split it rather than add to it")
    for name in sorted(set(listed) - {n for n, lines in found.items() if lines > LIMIT}):
        out.append(f"{name}: no longer over {LIMIT} lines (or gone): take it off LONG_FUNCTIONS")
    return out


def test_no_function_grows_past_the_limit():
    assert _too_long(_app_functions(), LONG_FUNCTIONS) == []


def test_the_length_rule_finds_what_it_should():
    body = "\n".join(f"    x{i} = {i}" for i in range(LIMIT))
    method = "\n".join(f"        x{i} = {i}" for i in range(LIMIT))
    source = f"def short():\n    return 1\n\n\nclass A:\n    def long(self):\n{method}\n\n\ndef listed():\n{body}\n"
    found = function_lengths(source, "m.py")
    assert found == {"m.py::short": 2, "m.py::A.long": LIMIT + 1, "m.py::listed": LIMIT + 1}
    assert _too_long(found, {"m.py::listed": LIMIT + 1}) == [
        f"m.py::A.long: {LIMIT + 1} lines (the limit is {LIMIT}): split it into parts that say what they do"
    ]
    assert _too_long(found, {"m.py::listed": LIMIT + 1, "m.py::A.long": LIMIT + 5}) == []
    assert _too_long(found, {"m.py::listed": LIMIT, "m.py::A.long": LIMIT + 1, "m.py::short": 130}) == [
        f"m.py::listed: {LIMIT + 1} lines, grown from {LIMIT}: split it rather than add to it",
        f"m.py::short: no longer over {LIMIT} lines (or gone): take it off LONG_FUNCTIONS",
    ]
