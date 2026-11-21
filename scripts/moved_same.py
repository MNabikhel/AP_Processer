"""Prove a move changed nothing: every function, class and method of the files a change touched is found again exactly
once, word for word as Python reads it (the same ``ast.dump``: what it does, never where its lines are), in the files
of this checkout. For a split or a move of code between modules; ``scripts/same_behaviour.py`` then proves the
behaviour, the tests the rest.

    python scripts/moved_same.py origin/main               the .py files changed since origin/main (and new ones)
    python scripts/moved_same.py origin/main --docstrings  the same, the docstrings left out (a change of words only)
    python scripts/moved_same.py origin/main ap_coder      only the files under ap_coder/

A definition is said "changed" (found by its name, its code not the same), "gone" (no longer found), "twice" (found
more often than before) or "new" (not there before). Exit code 0 when each one is found once and the same and none is
new, 1 otherwise, 2 when the ref can't be read. Only git on this clone is run (``git diff``, ``git show``).
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOWN = 30  # the definitions listed at most


def git(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        raise ValueError(done.stderr.strip() or f"git {' '.join(args)} failed")
    return done.stdout


def changed_files(ref: str, under: list[str]) -> tuple[list[str], list[str]]:
    """(the .py files the change touched as they were at ``ref``, the same files as this checkout has them, new ones
    and those not yet added to git among them)."""
    before, after = [], []
    for line in git("diff", "--name-status", "-M", ref, "--", *under).splitlines():
        status, *paths = line.split("\t")
        old, new = paths[0], paths[-1]
        if status[0] in "MDR" and old.endswith(".py"):
            before.append(old)
        if status[0] in "MAR" and new.endswith(".py"):
            after.append(new)
    after += [
        p for p in git("ls-files", "--others", "--exclude-standard", "--", *under).splitlines() if p.endswith(".py")
    ]
    return before, after


def _docstring(node: ast.AST) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _without_docstrings(tree: ast.Module) -> ast.Module:
    for each in ast.walk(tree):  # (an empty module has no first statement)
        kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)
        if isinstance(each, kinds) and each.body and _docstring(each.body[0]):
            each.body = each.body[1:] or [ast.Pass()]
    return tree


def definitions(source: str, path: str, docstrings: bool) -> dict[str, list[tuple[str, str]]]:
    """{"def name" / "class Name" / "method Class.name": [(the file, its ``ast.dump``)]} of a file's top level."""
    found: dict[str, list[tuple[str, str]]] = {}
    tree = ast.parse(source)
    if not docstrings:
        tree = _without_docstrings(tree)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.setdefault(f"def {node.name}", []).append((path, ast.dump(node)))
        elif isinstance(node, ast.ClassDef):
            found.setdefault(f"class {node.name}", []).append((path, ast.dump(node)))
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.setdefault(f"method {node.name}.{method.name}", []).append((path, ast.dump(method)))
    return found


def compare(before: dict[str, list[tuple[str, str]]], after: dict[str, list[tuple[str, str]]]) -> list[str]:
    """What is not found once and the same, in words, one line each. Of the definitions of a name, each one before
    takes one after with the same code; one left before is "changed" when one is left after (the one in its own file
    first), else "gone"; one left after is "twice" when its code is one before, else "new" (another file's ``main``)."""
    said = []
    for name in sorted(set(before) | set(after)):
        was, now = list(before.get(name, [])), list(after.get(name, []))
        for path, code in list(was):
            same = next((x for x in now if x[1] == code), None)
            if same is not None:
                was.remove((path, code))
                now.remove(same)
        for path, _ in was:
            if now:
                other = next((x for x in now if x[0] == path), now[0])
                now.remove(other)
                said.append(f"changed: {name} ({path} -> {other[0]})")
            else:
                said.append(f"gone: {name} ({path})")
        codes = {code for _, code in before.get(name, [])}
        said += [f"{'twice' if code in codes else 'new'}: {name} ({path})" for path, code in now]
    return said


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ref", help="the code moved from (a commit, a branch: origin/main)")
    parser.add_argument("--docstrings", action="store_true", help="leave the docstrings out of the comparison")
    parser.add_argument("under", nargs="*", help="only the files under these paths (default: all)")
    args = parser.parse_args(argv)
    keep = not args.docstrings
    try:
        old_files, new_files = changed_files(args.ref, args.under)
        before: dict[str, list[tuple[str, str]]] = {}
        for path in old_files:
            for name, found in definitions(git("show", f"{args.ref}:{path}"), path, keep).items():
                before.setdefault(name, []).extend(found)
    except ValueError as exc:
        print(f"Could not read {args.ref}: {exc}")
        return 2
    after: dict[str, list[tuple[str, str]]] = {}
    for path in new_files:
        for name, found in definitions((ROOT / path).read_text(encoding="utf-8"), path, keep).items():
            after.setdefault(name, []).extend(found)
    said = compare(before, after)
    kinds = {k: sum(line.startswith(f"{k}:") for line in said) for k in ("changed", "gone", "twice", "new")}
    total = sum(len(found) for found in before.values())
    same = total - kinds["changed"] - kinds["gone"] - kinds["twice"]
    what = " (docstrings left out)" if args.docstrings else ""
    print(f"{total} definitions in {len(old_files)} files at {args.ref}{what}: {same} found once and the same, "
          + ", ".join(f"{n} {k}" for k, n in kinds.items()))  # fmt: skip
    for line in said[:SHOWN]:
        print(f"  {line}")
    if len(said) > SHOWN:
        print(f"  ... and {len(said) - SHOWN} more")
    return 1 if said else 0


if __name__ == "__main__":
    sys.exit(main())
