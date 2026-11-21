"""Prove a move changed nothing: every function, class and method of the files a change touched is found again exactly
once, word for word as Python reads it (the same ``ast.dump``: what it does, never where its lines are), in the files
of this checkout. For a split or a move of code between modules; ``scripts/same_behaviour.py`` then proves the
behaviour, the tests the rest.

    python scripts/moved_same.py origin/main               the .py files changed since origin/main (and new ones)
    python scripts/moved_same.py origin/main --docstrings  the same, the docstrings left out (a change of words only)
    python scripts/moved_same.py origin/main ap_coder      only the files under ap_coder/

A definition is said "changed" (found by its name, its code not the same), "gone" (no longer found), "twice" (found
more often than before) or "new" (not there before). A method is found by its name in any class (the store's parts
are split so: methods moved into a new class); a class is compared without its methods. Said and passing: a method
"moved" to another class, a new class that is only the "home" of moved methods, and a class whose "bases" gained such
a home. Exit code 0 when each one is found once and the same and none is new, 1 otherwise, 2 when the ref can't be
read. Only git on this clone is run (``git diff``, ``git show``).
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


METHOD = (ast.FunctionDef, ast.AsyncFunctionDef)
Entry = tuple[str, str, ast.AST]  # (where it is: its file, and for a method its class too; its ast.dump; its node)
Found = dict[str, list[Entry]]  # {"def f" / "class C" / "method m": the definitions of that name}


def _shell(node: ast.ClassDef, bases: bool = True) -> str:
    """A class without its methods (compared one by one, wherever they went), and without its bases if asked."""
    shell = ast.ClassDef(name=node.name, bases=node.bases if bases else [], keywords=node.keywords,
                         body=[s for s in node.body if not isinstance(s, METHOD)], decorator_list=node.decorator_list,
                         type_params=getattr(node, "type_params", []))  # fmt: skip
    return ast.dump(shell)


def definitions(source: str, path: str, docstrings: bool) -> Found:
    """A file's top-level functions (``def f``, where: its file), its classes without their methods (``class C``)
    and their methods (``method m``, where: its file and class)."""
    found: Found = {}
    tree = ast.parse(source)
    if not docstrings:
        tree = _without_docstrings(tree)
    for node in tree.body:
        if isinstance(node, METHOD):
            found.setdefault(f"def {node.name}", []).append((path, ast.dump(node), node))
        elif isinstance(node, ast.ClassDef):
            found.setdefault(f"class {node.name}", []).append((path, _shell(node), node))
            for method in (m for m in node.body if isinstance(m, METHOD)):
                found.setdefault(f"method {method.name}", []).append((f"{path} {node.name}", ast.dump(method), method))
    return found


def _pair(was: list[Entry], now: list[Entry]) -> list[tuple[Entry, Entry]]:
    """Each one before taken with one after of the same code (the one in its own place first); both lists lose them."""
    pairs = []
    for old in list(was):
        same = [x for x in now if x[1] == old[1]]
        if same:
            new = next((x for x in same if x[0] == old[0]), same[0])
            was.remove(old)
            now.remove(new)
            pairs.append((old, new))
    return pairs


def _bases(node: ast.ClassDef) -> list[str]:
    return [ast.unparse(b) for b in node.bases]


def compare(before: Found, after: Found) -> list[str]:
    """What is not found once and the same, one line each, then what moved and passes. Of the definitions of a name,
    each one before takes one after with the same code; one left before is "changed" when one is left after (the one
    in its own file first), else "gone"; one left after is "twice" when its code is one before, else "new" (another
    file's ``main``). Passing: a method "moved" to another class; a new class that only holds methods moved in, their
    "home"; a class as before but for "bases" it gained, each such a home."""
    said, passing = [], []
    moved_in: set[str] = set()  # "file Class" a method moved into from another class
    left = {}
    for name in sorted(set(before) | set(after)):
        was, now = list(before.get(name, [])), list(after.get(name, []))
        for old, new in _pair(was, now):
            if name.startswith("method ") and old[0].split(" ")[1] != new[0].split(" ")[1]:
                passing.append(f"moved: {name} ({old[0]} -> {new[0]})")
                moved_in.add(new[0])
        left[name] = (was, now)
    homes = set()
    for name, (_, now) in left.items():
        for entry in [e for e in now if name.startswith("class ") and not before.get(name)]:
            where, _, node = entry
            methods = {f"{where} {node.name}" for m in node.body if isinstance(m, METHOD)}
            rest = [s for s in node.body if not isinstance(s, METHOD)]
            rest = rest[1:] if rest and _docstring(rest[0]) else rest  # (its docstring is no code)
            if methods and methods <= moved_in and not rest:
                now.remove(entry)
                homes.add(node.name)
                passing.append(f"home: {name} ({where}), only methods moved in")
    for name, (was, now) in left.items():
        for old in was:
            if not now:
                said.append(f"gone: {name} ({old[0]})")
                continue
            new = next((x for x in now if x[0].split(" ")[0] == old[0].split(" ")[0]), now[0])
            now.remove(new)
            gained = [b for b in _bases(new[2]) if b not in _bases(old[2])] if name.startswith("class ") else []
            if (gained and set(gained) <= homes and [b for b in _bases(new[2]) if b not in gained] == _bases(old[2])
                    and _shell(old[2], bases=False) == _shell(new[2], bases=False)):  # fmt: skip
                passing.append(f"bases: {name} ({new[0]}) gains {', '.join(gained)}")
            else:
                said.append(f"changed: {name} ({old[0]} -> {new[0]})")
        codes = {code for _, code, _ in before.get(name, [])}
        said += [f"{'twice' if code in codes else 'new'}: {name} ({where})" for where, code, _ in now]
    return said + passing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ref", help="the code moved from (a commit, a branch: origin/main)")
    parser.add_argument("--docstrings", action="store_true", help="leave the docstrings out of the comparison")
    parser.add_argument("under", nargs="*", help="only the files under these paths (default: all)")
    args = parser.parse_intermixed_args(argv)  # (the paths after --docstrings too: Python 3.11, B-3025)
    keep = not args.docstrings
    try:
        old_files, new_files = changed_files(args.ref, args.under)
        before: Found = {}
        for path in old_files:
            for name, found in definitions(git("show", f"{args.ref}:{path}"), path, keep).items():
                before.setdefault(name, []).extend(found)
    except ValueError as exc:
        print(f"Could not read {args.ref}: {exc}")
        return 2
    after: Found = {}
    for path in new_files:
        for name, found in definitions((ROOT / path).read_text(encoding="utf-8"), path, keep).items():
            after.setdefault(name, []).extend(found)
    said = compare(before, after)
    kinds = {k: sum(line.startswith(f"{k}:") for line in said) for k in ("changed", "gone", "twice", "new")}
    wrong = [line for line in said if line.split(":")[0] in kinds]
    total = sum(len(found) for found in before.values())
    same = total - kinds["changed"] - kinds["gone"] - kinds["twice"]
    what = " (docstrings left out)" if args.docstrings else ""
    print(f"{total} definitions in {len(old_files)} files at {args.ref}{what}: {same} found once and the same, "
          + ", ".join(f"{n} {k}" for k, n in kinds.items()))  # fmt: skip
    for line in said[:SHOWN]:
        print(f"  {line}")
    if len(said) > SHOWN:
        print(f"  ... and {len(said) - SHOWN} more")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
