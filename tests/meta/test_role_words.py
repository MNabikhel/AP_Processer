"""What a person reads says who may do something by what they may do, never by a built-in role's name (D-250): with
roles made on the Admin panel, "ask an Admin" or "an Approver or an Admin" names roles a company may not use."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "ap_coder"
# "an Admin", "another Admin", "an Approver", "Approver or Admin": a built-in role named as who does something.
NAMED = re.compile(r"\b[Aa]n(?:other)? (?:Admin|Approver)\b|\bApprover or (?:an )?Admin\b")
COMMENT = re.compile(r"\{#.*?#\}", re.S)
PYTHON = ("web", "core/store", "ops/help.py", "ops/health.py", "ops/offline.py")


def _strings(path: Path) -> list[tuple[int, str]]:
    """The string literals of a module that a person may read: not its docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]  # fmt: skip


def test_no_page_or_message_names_a_built_in_role_as_who_does_something():
    """B-2588."""
    found = []
    for template in sorted((ROOT / "web" / "templates").glob("*.html")):
        text = COMMENT.sub("", template.read_text(encoding="utf-8"))
        found += [f"{template.name}: {m.group(0)}" for m in NAMED.finditer(text)]
    for part in PYTHON:
        for module in sorted((ROOT / part).rglob("*.py")) if (ROOT / part).is_dir() else [ROOT / part]:
            found += [f"{module.relative_to(ROOT)}:{line}: {NAMED.search(s).group(0)}" for line, s in _strings(module)
                      if NAMED.search(s)]  # fmt: skip
    assert found == []
