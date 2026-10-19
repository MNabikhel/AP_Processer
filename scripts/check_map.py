"""The codebase map's checks: every folder explains itself in a CLAUDE.md that matches the code.

    python scripts/check_map.py                 # the whole map (CI): errors fail, warnings are printed
    python scripts/check_map.py --changed BASE  # also: folders whose code changed since BASE but whose map didn't
    python scripts/check_map.py --modules       # write ap_coder/MODULES.md (the core folders' module index) again
    python scripts/check_map.py --hook          # Claude Code hook (PostToolUse on Edit/Write): a reminder only

What is checked, for every folder git tracks or that holds new files not yet added (except the ones in SKIP):
1. it has a CLAUDE.md, of at most MAX_LINES lines (over WARN_LINES: a warning, the folder may do too much);
2. its "## Files" index names every file and sub-folder in it, and nothing that isn't there;
3. for a Python package: its "**Depends on:**" and "**Used by:**" lines name exactly the folders its modules import
   and the folders that import it (from the code, ``import`` and ``from ... import``, inside functions too). Only
   packages under ap_coder/ count: scripts/ and tests/ import the core but aren't named in its Used by (MODULES.md
   lists the scripts that import each module);
4. every name in backticks followed by ``()`` in the "## Interface" section exists: `name()` (x.py) in x.py,
   `name()` in the folder's code (or anywhere, for a folder with no code), `mod.name()` / `Class.name()` in that
   module or class;
5. every bug in docs/BUGS.md (B-NN) has its lesson in the "### Bug lessons" list of the folder its fix is in (the
   folder of the first path of its **Where:** line). That list is not counted in the size limit: one line per bug;
6. ap_coder/MODULES.md (the module index of ap_coder/ and its GROUPED folders, old-path aliases aside: each module's
   job and the modules that import it, made by ``--modules``) is up to date: module by module, what breaks where.
With --changed: a folder whose code changed but whose CLAUDE.md didn't is a warning (an interface change updates it);
a base git can't compare with (a shallow clone, a typo) is a warning too, never a silent pass.

Only the standard library: it runs before anything is installed, offline.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
MAP = "CLAUDE.md"
MAX_LINES, WARN_LINES = 80, 50
# Folders that hold data or fixtures, not code to explain: each is described by its parent's CLAUDE.md instead.
SKIP = {"samples", "samples/ground_truth", "docs/screenshots", "tests/fixtures", "tests/fixtures/page_reader",
        "tests/offline_guard_site",
        "ap_coder/assets", "ap_coder/static", "private"}  # fmt: skip
CODE = {".py", ".js", ".css", ".html", ".sh", ".bat", ".command", ".yml", ".toml"}
PACKAGE_ROOTS = ("ap_coder",)


def tracked() -> list[PurePosixPath]:
    """The files git tracks, plus new ones not yet added (a new module is checked before its commit), minus the
    ones deleted but not yet removed from git."""
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                         capture_output=True, check=True).stdout  # fmt: skip
    names = {p for p in out.decode("utf-8").split("\0") if p}
    return [PurePosixPath(p) for p in sorted(names) if (ROOT / p).is_file()]


def source(path: PurePosixPath) -> ast.Module:
    """The parsed module, read as Python reads it (from bytes, so a UTF-8 byte-order mark is allowed)."""
    return ast.parse((ROOT / path).read_bytes())


def folders(files: list[PurePosixPath]) -> dict[str, list[PurePosixPath]]:
    """Every tracked folder (its own path, "." for the root) -> the tracked files directly in it."""
    by: dict[str, list[PurePosixPath]] = defaultdict(list)
    for f in files:
        by[str(f.parent)].append(f)
        for parent in list(f.parents)[1:]:  # a folder that only holds folders is still a folder
            by.setdefault(str(parent), [])
    return {k: v for k, v in by.items() if not _skipped(k)}


def _skipped(folder: str) -> bool:
    return any(folder == s or folder.startswith(s + "/") for s in SKIP) or folder.startswith(".claude/worktrees")


def section(text: str, title: str) -> str:
    """The text of a "## title" section, up to the next "## "."""
    m = re.search(rf"(?ms)^##\s+{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)", text)
    return m.group(1) if m else ""


def listed_paths(line_text: str) -> set[str]:
    """The names in backticks, files and folders only (a `name()` or a `module.name()` is code, not a file)."""
    return {p.rstrip("/") for p in re.findall(r"`([^`]+)`", line_text) if "(" not in p and " " not in p}


def field(text: str, name: str) -> str | None:
    m = re.search(rf"(?m)^\*\*{re.escape(name)}:\*\*(.*)$", text)
    return m.group(1) if m else None


# --- Imports between folders ---------------------------------------------------------------------------------------


def module_of(path: PurePosixPath) -> str:
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports(path: PurePosixPath, modules: set[str]) -> set[str]:
    """The project modules ``path`` imports (resolved to modules that exist)."""
    tree = source(path)
    here = module_of(path)
    package = here if path.name == "__init__.py" else here.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            # ``from pkg import mod`` imports pkg.mod (a module), ``from mod import name`` imports mod
            names = [f"{target}.{a.name}" if f"{target}.{a.name}" in modules else target for a in node.names]
        else:
            continue
        for name in names:
            while name and name not in modules:
                name = name.rpartition(".")[0]
            if name:
                found.add(name)
    return found


def folder_graph(files: list[PurePosixPath]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(folder -> project folders it imports, folder -> project folders that import it), Python packages only."""
    py = [f for f in files if f.suffix == ".py" and f.parts[0] in PACKAGE_ROOTS]
    modules = {module_of(f): f for f in py}
    folder_of = {m: str(f.parent) for m, f in modules.items()}
    for m, f in modules.items():
        if f.name == "__init__.py":
            folder_of[m] = str(f.parent)
    uses: dict[str, set[str]] = defaultdict(set)
    used_by: dict[str, set[str]] = defaultdict(set)
    for f in py:
        src = str(f.parent)
        for m in imports(f, set(modules)):
            dst = folder_of[m]
            if dst != src:
                uses[src].add(dst)
                used_by[dst].add(src)
    return uses, used_by


MODULES = "ap_coder/MODULES.md"
GROUPED = ("core", "ai", "coding", "erp", "intake", "ops")  # the folders the flat core was grouped into (MODULES.md)
ALIAS = "Moved to ap_coder."  # the docstring of an old path kept as an alias for one release (not indexed)


def module_index(files: list[PurePosixPath]) -> str:
    """The core's module index: each ``ap_coder/*.py`` module (old-path aliases aside) and each module of the GROUPED
    folders, the first line of its docstring, and every project module that imports it (sub-packages and scripts/;
    tests aside)."""
    py = [f for f in files if f.suffix == ".py" and f.parts[0] in PACKAGE_ROOTS]
    modules = {module_of(f): f for f in py}
    importers: dict[str, set[str]] = defaultdict(set)
    for f in py + [f for f in files if f.suffix == ".py" and f.parts[0] == "scripts"]:
        for m in imports(f, set(modules)):
            if m != module_of(f):
                importers[m].add(module_of(f))
    rows = []
    grouped = [f for f in py if len(f.parts) == 3 and f.parts[1] in GROUPED and f.name != "__init__.py"]
    for f in sorted(f for f in py if len(f.parts) == 2) + sorted(grouped):
        mod = module_of(f)
        doc = ast.get_docstring(source(f)) or ""
        if len(f.parts) == 2 and doc.startswith(ALIAS):
            continue
        first = " ".join(doc.strip().split("\n\n")[0].split())
        first = first if len(first) <= 110 else first[:107].rsplit(" ", 1)[0] + "..."
        users = sorted(u.removeprefix("ap_coder.").replace(".", "/") for u in importers.get(mod, ()))
        name = "/".join(f.parts[1:])
        rows.append(f"| `{name}` | {first.replace('|', '/')} | {', '.join(users) or '(entry point)'} |")
    return (
        "# ap_coder: module index (generated)\n\n"
        "Made by `python scripts/check_map.py --modules`; CI fails when it is out of date. For each module of\n"
        f"ap_coder/ and of its folders {', '.join(GROUPED)} (the old paths' aliases aside): its job (its docstring's\n"
        "first lines) and every module that imports it, so a change can be followed to its callers.\n"
        "`web/pages/review` is `ap_coder/web/pages/review.py`.\n\n"
        "| Module | Job | Imported by |\n|---|---|---|\n" + "\n".join(rows) + "\n"
    )


def defined_names(files: list[PurePosixPath]) -> set[str]:
    return set().union(*(names_in(f) for f in files)) if files else set()


def names_in(f: PurePosixPath) -> set[str]:
    """The functions and classes (methods too) one .py or .js file defines."""
    if f.suffix == ".py":
        kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        return {n.name for n in ast.walk(source(f)) if isinstance(n, kinds)}
    text = (ROOT / f).read_text(encoding="utf-8-sig", errors="replace")
    return set(re.findall(r"(?:function\s+|const\s+|let\s+|class\s+)([A-Za-z_$][\w$]*)", text))


def class_members(files: list[PurePosixPath]) -> dict[str, set[str]]:
    """Every Python class -> the names defined in its body (methods, nested classes)."""
    out: dict[str, set[str]] = defaultdict(set)
    for f in files:
        if f.suffix == ".py":
            for node in ast.walk(source(f)):
                if isinstance(node, ast.ClassDef):
                    out[node.name] |= {n.name for n in ast.walk(node) if n is not node and hasattr(n, "name")}
    return out


def qualified_ok(qualifier: str, called: str, code: list[PurePosixPath], members: dict[str, set[str]]) -> bool:
    """`mod.name()` / `Class.name()`: the qualifier is a module (file or package) that defines name, or a class
    that has it."""
    last = qualifier.rpartition(".")[2]
    if called in members.get(last, set()):
        return True
    for f in code:
        stem = f.parent.name if f.stem == "__init__" else f.stem
        if stem == last and called in names_in(f):
            return True
    return False


def bug_folders() -> dict[str, set[str]]:
    """docs/BUGS.md: folder -> the bugs fixed in it (the folder of the first path in each entry's **Where:** line)."""
    path = ROOT / "docs" / "BUGS.md"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    out: dict[str, set[str]] = defaultdict(set)
    for m in re.finditer(r"(?ms)^#### (B-\d+)\b(.*?)(?=^#{2,4} |\Z)", text):
        where = re.search(r"\*\*Where:\*\*(.*)", m.group(2))
        paths = re.findall(r"`([\w./-]+\.\w+)(?::[\d-]+)?", where.group(1)) if where else []
        if not paths:
            continue
        roots = ("ap_coder/", "scripts/", "tests/", "data/", "docs/", ".github/")
        first = next((p for p in paths if p.startswith(roots)), paths[0])
        if not first.startswith(roots):
            first = "ap_coder/" + first
        out[str(PurePosixPath(first).parent)].add(m.group(1))
    return out


# --- The checks ------------------------------------------------------------------------------------------------------


def check(changed_since: str | None = None) -> tuple[list[str], list[str]]:
    files = tracked()
    by_folder = folders(files)
    uses, used_by = folder_graph(files)
    subfolders: dict[str, set[str]] = defaultdict(set)  # every sub-folder, skipped ones too (the parent names them)
    for f in files:
        for parent in list(f.parents)[:-1]:
            subfolders[str(parent.parent)].add(parent.name)
    bugs = bug_folders()
    code = [f for f in files if f.suffix in (".py", ".js")]
    everywhere = defined_names(code)
    members = class_members(code)
    errors: list[str] = []
    warnings: list[str] = []
    for folder, here in sorted(by_folder.items()):
        doc_path = PurePosixPath(folder) / MAP if folder != "." else PurePosixPath(MAP)
        where = str(doc_path)
        if not (ROOT / doc_path).is_file():
            errors.append(f"{folder}: no {MAP}")
            continue
        text = (ROOT / doc_path).read_text(encoding="utf-8")
        lessons = re.search(r"(?ms)^###\s+Bug lessons\s*$(.*?)(?=^##|\Z)", text)
        lines = text.count("\n") + 1 - (lessons.group(0).count("\n") if lessons else 0)
        if lines > MAX_LINES:
            errors.append(f"{where}: {lines} lines (at most {MAX_LINES}): the folder does too much, split it")
        elif lines > WARN_LINES:
            warnings.append(f"{where}: {lines} lines: keep it short (about 50)")
        # 2. The index: every file and sub-folder, nothing else.
        index = listed_paths(section(text, "Files"))
        children = {f.name for f in here if f.name != MAP}
        children |= subfolders.get(folder, set())
        for name in sorted(children - index):
            errors.append(f"{where}: `{name}` is not in its ## Files index")
        for name in sorted(index - children):
            errors.append(f"{where}: ## Files lists `{name}`, which is not in {folder}/")
        # 3. Depends on / Used by, from the imports.
        if folder.split("/")[0] in PACKAGE_ROOTS and any(f.suffix == ".py" for f in here):
            for name, real in (("Depends on", uses.get(folder, set())), ("Used by", used_by.get(folder, set()))):
                line = field(text, name)
                if line is None:
                    errors.append(f"{where}: no **{name}:** line")
                    continue
                said = listed_paths(line)
                for missing in sorted(real - said):
                    errors.append(f"{where}: **{name}:** misses `{missing}` (the imports say so)")
                for extra in sorted(said - real):
                    errors.append(f"{where}: **{name}:** names `{extra}`, which the imports don't show")
        # 5. Every bug fixed in this folder has its lesson here.
        listed = set(re.findall(r"\bB-\d+\b", lessons.group(1))) if lessons else set()
        for bug in sorted(bugs.get(folder, ())):
            if bug not in listed:
                errors.append(f"{where}: ### Bug lessons misses {bug} (docs/BUGS.md: fixed in {folder}/)")
        for bug in sorted(listed - set(bugs.get(folder, ()))):
            errors.append(f"{where}: ### Bug lessons lists {bug}, which docs/BUGS.md doesn't place in {folder}/")
        # 4. Interface names that exist.
        code_here = [f for f in here if f.suffix in (".py", ".js")]
        names = defined_names(code_here)
        for m in re.finditer(r"`(?:([\w.]+)\.)?(\w+)\(\)`(?:\s*\((\w+\.(?:py|js))\b)?", section(text, "Interface")):
            qualifier, called, in_file = m.groups()
            if qualifier:  # `mailbox.unpack_folder()`, `InvoicePipeline.process()`: a module or a class anywhere
                if not qualified_ok(qualifier, called, code, members):
                    errors.append(f"{where}: ## Interface names `{qualifier}.{called}()`, which no module or class "
                                  "of that name defines")  # fmt: skip
                continue
            named = [f for f in code_here if f.name == in_file]
            if named and called not in names_in(named[0]):
                errors.append(f"{where}: ## Interface names `{called}()` ({in_file}), not defined in {in_file}")
            elif not code_here and called not in everywhere:
                errors.append(f"{where}: ## Interface names `{called}()`, defined nowhere in the project")
            elif code_here and called not in names:
                errors.append(f"{where}: ## Interface names `{called}()`, not defined in {folder}/")
    if (ROOT / MODULES).is_file() and (ROOT / MODULES).read_text(encoding="utf-8") != module_index(files):
        errors.append(f"{MODULES} is out of date: run python scripts/check_map.py --modules")
    elif not (ROOT / MODULES).is_file() and (ROOT / "ap_coder").is_dir():
        errors.append(f"{MODULES} is missing: run python scripts/check_map.py --modules")
    if changed_since:
        warnings += stale(changed_since, by_folder)
    return errors, warnings


def changed_files(base: str) -> list[PurePosixPath] | None:
    """Files changed since ``base`` (committed, staged, not yet staged, or new); None when git can't compare."""
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], cwd=ROOT, capture_output=True, text=True)
    work = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    new = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True,
                         text=True)  # fmt: skip
    if out.returncode or work.returncode:
        return None
    return [PurePosixPath(p) for p in {*out.stdout.split(), *work.stdout.split(), *new.stdout.split()}]


def stale(base: str, by_folder: dict[str, list[PurePosixPath]]) -> list[str]:
    changed = changed_files(base)
    if changed is None:
        return [f"--changed {base}: git can't compare with {base} (unknown name, or a shallow clone: fetch the full "
                "history), so changed folders were not checked"]  # fmt: skip
    touched_maps = {str(p.parent) for p in changed if p.name == MAP}
    code = {str(p.parent) for p in changed if p.suffix in CODE and p.name != MAP and str(p.parent) in by_folder}
    note = (f"its code changed but its {MAP} didn't: if an interface, a dependency or a file changed, update it and "
            "check every folder in its **Used by:**")  # fmt: skip
    return [f"{folder}: {note}" for folder in sorted(code - touched_maps)]


def hook() -> int:
    """Claude Code PostToolUse hook: after an edit to a code file, remind of its folder's map (never blocks)."""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_input = event.get("tool_input") if isinstance(event, dict) else None
    path = Path(str((tool_input if isinstance(tool_input, dict) else {}).get("file_path") or ""))
    try:
        rel = PurePosixPath(path.resolve().relative_to(ROOT).as_posix())
    except ValueError:
        return 0
    if rel.parts[:2] == (".claude", "worktrees") and len(rel.parts) > 3:  # an agent's worktree: its own copy
        rel = PurePosixPath(*rel.parts[3:])
    if rel.suffix not in CODE or rel.name == MAP or _skipped(str(rel.parent)):
        return 0
    doc = rel.parent / MAP
    note = (f"You changed {rel}. If its interface, dependencies or files changed, update {doc} (Purpose, Interface, "
            f"Depends on / Used by, Files) and check each folder in its **Used by:** line; "
            f"then run python scripts/check_map.py.")  # fmt: skip
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}}))
    return 0


def main(argv: list[str]) -> int:
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    if "--hook" in argv:
        return hook()
    if "--modules" in argv:
        (ROOT / MODULES).write_text(module_index(tracked()), encoding="utf-8", newline="\n")
        print(f"wrote {MODULES}")
        return 0
    if "--changed" in argv and argv.index("--changed") + 1 >= len(argv):
        print("--changed needs a base to compare with, e.g. --changed origin/main")
        return 2
    base = argv[argv.index("--changed") + 1] if "--changed" in argv else None
    errors, warnings = check(base)
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print(f"map: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
