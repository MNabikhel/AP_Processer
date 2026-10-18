"""The codebase map's checks: every folder explains itself in a CLAUDE.md that matches the code.

    python scripts/check_map.py                 # the whole map (CI): errors fail, warnings are printed
    python scripts/check_map.py --changed BASE  # also: folders whose code changed since BASE but whose map didn't
    python scripts/check_map.py --hook          # Claude Code hook (PostToolUse on Edit/Write): a reminder only

What is checked, for every folder git tracks (except the ones in SKIP):
1. it has a CLAUDE.md, of at most MAX_LINES lines (over WARN_LINES: a warning, the folder may do too much);
2. its "## Files" index names every file and sub-folder in it, and nothing that isn't there;
3. for a Python package: its "**Depends on:**" and "**Used by:**" lines name exactly the folders its modules import
   and the folders that import it (from the code, ``import`` and ``from ... import``, inside functions too);
4. every name in backticks followed by ``()`` in the "## Interface" section is defined in the folder's code;
5. every bug in docs/BUGS.md (B-NN) has its lesson in the "### Bug lessons" list of the folder its fix is in (the
   folder of the first path of its **Where:** line). That list is not counted in the size limit: one line per bug.
With --changed: a folder whose code changed but whose CLAUDE.md didn't is a warning (an interface change updates it).

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
MAX_LINES, WARN_LINES = 80, 60
# Folders that hold data or fixtures, not code to explain: each is described by its parent's CLAUDE.md instead.
SKIP = {"samples", "samples/ground_truth", "docs/screenshots", "tests/fixtures", "tests/fixtures/page_reader",
        "tests/offline_guard_site",
        "ap_coder/assets", "ap_coder/static", "private"}  # fmt: skip
CODE = {".py", ".js", ".css", ".html", ".sh", ".bat", ".command", ".yml", ".toml"}
PACKAGE_ROOTS = ("ap_coder",)


def tracked() -> list[PurePosixPath]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [PurePosixPath(p) for p in out.decode("utf-8").split("\0") if p]


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
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
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
            names = [f"{target}.{a.name}" for a in node.names] + [target]
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


def defined_names(files: list[PurePosixPath]) -> set[str]:
    names: set[str] = set()
    for f in files:
        text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        if f.suffix == ".py":
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
        elif f.suffix == ".js":
            names |= set(re.findall(r"(?:function\s+|const\s+|let\s+|class\s+)([A-Za-z_$][\w$]*)", text))
    return names


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
    everywhere = defined_names([f for f in files if f.suffix in (".py", ".js") and not _skipped(str(f.parent))])
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
        names = defined_names(here)
        for qualifier, called in re.findall(r"`(?:([\w.]+)\.)?(\w+)\(\)`", section(text, "Interface")):
            if (
                qualifier and "." not in qualifier and called in everywhere
            ):  # e.g. `capture.luhn_ok()`: another folder's
                continue
            if names and called not in names and not (qualifier and called in everywhere):
                errors.append(f"{where}: ## Interface names `{called}()`, not defined in {folder}/")
    if changed_since:
        warnings += stale(changed_since, by_folder)
    return errors, warnings


def changed_files(base: str) -> list[PurePosixPath]:
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], cwd=ROOT, capture_output=True, text=True)
    work = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return [PurePosixPath(p) for p in {*out.stdout.split(), *work.stdout.split()}]


def stale(base: str, by_folder: dict[str, list[PurePosixPath]]) -> list[str]:
    changed = changed_files(base)
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
    path = Path(str((event.get("tool_input") or {}).get("file_path") or ""))
    try:
        rel = PurePosixPath(path.resolve().relative_to(ROOT).as_posix())
    except ValueError:
        return 0
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
