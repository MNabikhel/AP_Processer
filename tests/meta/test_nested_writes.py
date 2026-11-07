"""Store transactions nest (B-1995): a write takes the write lock with ``dialect.begin_write``, which joins a
transaction under way, so any store method can be called inside ``Store.transaction()`` or another write. No bare
``BEGIN IMMEDIATE`` outside ``core/store/dialect.py``. A write that must commit on its own takes its lock with
``StoreCore._begin_alone`` (``NestedWrite``, in words, inside another transaction) and is listed here with why. The
list only shrinks: a method that no longer needs its own transaction leaves it."""

from __future__ import annotations

import ast
import functools

from tests.conftest import ROOT

DIALECT = "ap_coder/core/store/dialect.py"
# (file, method): (its ``_begin_alone`` calls, why it commits on its own)
ALONE = {
    ("ap_coder/core/store/users.py", "authenticate"): (2, "the try is recorded, and committed, before its password "
        "is checked, and kept whatever comes next (D-101): undone with a caller's rollback, wrong passwords would "
        "never pause the logins; its outcome's events are kept when LoginRefused is raised"),
    ("ap_coder/core/store/users.py", "check_code"): (1, "a code's try counts toward the pause as a password's does "
        "(D-146), kept when LoginRefused is raised"),
    ("ap_coder/core/store/users.py", "change_password"): (1, "a current password that doesn't match is a try at "
        "logging in (D-109), kept when the ValueError is raised"),
    ("ap_coder/core/store/page_queue.py", "next_page_read"): (1, "a claim: it commits before the read starts, "
        "outside any transaction, so its heartbeat (another thread's connection) finds it"),
    ("ap_coder/core/store/lessons.py", "next_verification"): (1, "a claim: it commits before the AI reviewer's "
        "minutes of work, so its heartbeat (another thread's connection) finds it"),
    ("ap_coder/core/store/capture.py", "next_azure_check"): (1, "a claim: it commits before the Azure check's "
        "minutes, never the write lock held (every other writer waiting) all that time"),
    ("ap_coder/core/store/imports.py", "claim_import"): (1, "a claim and its lease: they commit before the read "
        "starts, so its heartbeat (another thread's connection) finds them"),
}  # fmt: skip


def _found(source: str) -> tuple[list[int], dict[str, list[str]]]:
    """The lines with a bare ``BEGIN IMMEDIATE`` (in a string that is not a docstring), and each method's
    ``_begin_alone(conn, what)`` calls (their ``what``)."""
    tree = ast.parse(source)
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}  # fmt: skip
    bare, alone = set(), {}

    def visit(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str) and id(child) not in docs:
                if "BEGIN IMMEDIATE" in child.value.upper():
                    bare.add(child.lineno)
            elif isinstance(child, ast.Call) and getattr(child.func, "attr", "") == "_begin_alone":
                what = child.args[1].value if len(child.args) > 1 and isinstance(child.args[1], ast.Constant) else ""
                alone.setdefault(func, []).append(what)
            visit(child, child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func)

    visit(tree, "")
    return sorted(bare), alone


@functools.cache
def _code() -> dict[str, tuple[list[int], dict[str, list[str]]]]:
    """What ``_found`` finds in each file of ``ap_coder/`` (read once for the tests)."""
    code = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8-sig")
            for p in sorted((ROOT / "ap_coder").rglob("*.py"))}  # fmt: skip
    # (a file that says neither has neither: only those that do are parsed)
    return {name: _found(text) for name, text in code.items() if "IMMEDIATE" in text.upper() or "_begin_alone" in text}


def test_no_bare_begin_immediate_outside_the_dialect():
    found = [f"{name}:{line}" for name, (bare, _) in _code().items() if name != DIALECT for line in bare]
    assert not found, ("take the write lock with dialect.begin_write (it joins a transaction under way), or "
                       "StoreCore._begin_alone where it must commit on its own (listed in ALONE): "
                       + ", ".join(found))  # fmt: skip


def test_only_the_listed_writes_commit_on_their_own():
    found = {(name, func): whats for name, (_, alone) in _code().items() for func, whats in alone.items()}
    assert {key: len(whats) for key, whats in found.items()} == {key: n for key, (n, _why) in ALONE.items()}, (
        "a write joins the caller's transaction (dialect.begin_write); one that must commit on its own is listed "
        "in ALONE with why, and leaves it once it doesn't (the list only shrinks)"
    )
    assert all(whats == [func] * len(whats) for (_name, func), whats in found.items())  # the error names the method
    assert all(why.strip() for _n, why in ALONE.values())


def test_the_rule_finds_what_it_should():
    source = '''
"""A module's docstring may say BEGIN IMMEDIATE."""


class Part:
    def take(self):
        """So may a method's: BEGIN IMMEDIATE."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")  # a bare one
            conn.execute("begin immediate transaction")

    def claim(self):
        with self._conn() as conn:
            self._begin_alone(conn, "claim")

    def write(self):
        with self._conn() as conn:
            begin_write(conn)  # (and BEGIN IMMEDIATE in a comment)
'''
    assert _found(source) == ([9, 10], {"claim": ["claim"]})
