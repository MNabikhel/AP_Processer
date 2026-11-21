"""CLAUDE.md's map ("Where things live") and the packages' maps (each ``__init__.py`` docstring) say what is there
(D-332): every path in them exists, every name is defined where they say it is, and CLAUDE.md, read on every turn of
an agent's work, stays no bigger than it was when its map was first checked."""

from __future__ import annotations

import ast
import re
import sys
from functools import cache
from pathlib import Path

from tests.conftest import ROOT

CLAUDE = ROOT / "CLAUDE.md"
MOST_BYTES = 12_573  # CLAUDE.md when its map was first checked: a line added means one taken out
PATH = re.compile(r"[\w.<>*-]*/[\w./<>*-]*|[\w<>*-]+\.(?:py|md|html|js|css|json|txt|yml)")
NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\(\))?")
JOB = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")  # a CI job, as the workflows name it


def map_bullets() -> list[str]:
    """The bullets of CLAUDE.md's "Where things live", each with its lines joined (the lines above them as one)."""
    text = CLAUDE.read_text(encoding="utf-8")
    section = text.split("\n## Where things live\n", 1)[1].split("\n## ", 1)[0]
    return [" ".join(b.split()) for b in re.split(r"\n(?=- )", section.strip())]


def found(token: str, near: list[Path]) -> list[Path]:
    """The files or folders a path ``token`` is, from the folder of the path written last before it (or a folder
    above that one), of the one before, ..., then from ``ap_coder/`` and from the top of the repository;
    ``<subject>`` stands for any word, so ``web/pages/<page>.py`` is any page's module."""
    pattern = re.sub(r"<\w+>", "*", token.rstrip("/"))
    ups = [up for p in reversed(near) for up in [p if p.is_dir() else p.parent, *p.parents] if ROOT in up.parents]
    for base in dict.fromkeys([*ups, ROOT / "ap_coder", ROOT]):
        hits = sorted(base.glob(pattern)) if "*" in pattern else [base / pattern] * (base / pattern).exists()
        if hits:
            return hits
    return []


@cache
def defined(path: Path) -> frozenset[str]:
    """The names a Python file defines: its functions, classes and assignments, its classes' methods and fields, and
    the attributes its classes set on ``self``."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                for each in ast.walk(target):
                    if isinstance(each, ast.Name):
                        names.add(each.id)
                    elif isinstance(each, ast.Attribute) and isinstance(each.value, ast.Name):
                        names.add(each.attr)
    return frozenset(names)


def python_files(paths: list[Path]) -> list[Path]:
    return [f for p in paths for f in ([p] if p.suffix == ".py" else sorted(p.rglob("*.py")) if p.is_dir() else [])]


@cache
def store_methods() -> frozenset[str]:
    return frozenset().union(*(defined(p) for p in (ROOT / "ap_coder" / "core" / "store").glob("*.py")))


@cache
def fixtures() -> frozenset[str]:
    tree = ast.parse((ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))
    return frozenset(f.name for f in tree.body if isinstance(f, ast.FunctionDef)
                     and any("fixture" in ast.unparse(d) for d in f.decorator_list))  # fmt: skip


def is_job(token: str) -> bool:
    workflows = (ROOT / ".github" / "workflows").glob("*.yml")
    return any(re.search(rf"^  {re.escape(token)}:", p.read_text(encoding="utf-8"), re.M) for p in workflows)


def name_is_defined(token: str, files: list[Path]) -> bool:
    """``token`` (``name``, ``module.name``, ``Class.method``, ``name()``) is defined in one of ``files``, in a store
    part (a ``Store`` method), or is a fixture."""
    first, *rest = token.removesuffix("()").split(".")
    if first == "Store" and rest:
        return rest[-1] in store_methods()
    if rest:  # the module among the files (or beside them), or a class one of them defines
        modules = [f for f in python_files([*files, *(f.parent for f in files)]) if f.stem == first]
        return any(rest[-1] in defined(f) for f in modules + [f for f in files if first in defined(f)])
    return any(first in defined(f) for f in files) or first in store_methods() or first in fixtures()


def test_every_path_and_name_of_claudes_map_is_there():
    """Each backticked path of a bullet exists (from the top, ``ap_coder/`` or a path before it in the bullet); each
    name is defined in a file the bullet names (any store part for a Store method; a fixture in tests/conftest.py),
    is a word a path's ``<subject>`` stands for, or a CI job."""
    missing = []
    for bullet in map_bullets():
        tokens = [t for t in re.findall(r"`([^`]+)`", bullet) if " " not in t]  # (words or code: `x = f()`)
        paths: list[Path] = []
        for token in filter(PATH.fullmatch, tokens):
            hits = found(token, paths)
            paths += hits
            missing += [] if hits else [f"path {token}"]
        files = python_files(paths)
        open_paths = [t for t in tokens if PATH.fullmatch(t) and re.search(r"<\w+>", t)]
        for token in [t for t in tokens if not PATH.fullmatch(t)]:
            if NAME.fullmatch(token) and name_is_defined(token, files):
                continue
            if any(found(re.sub(r"<\w+>", token, p, count=1), paths) for p in open_paths):
                continue
            if JOB.fullmatch(token) and is_job(token):
                continue
            missing.append(f"{token} (in: {bullet[:50]}...)")
    assert not missing, missing


def test_claude_md_is_no_bigger_than_when_its_map_was_first_checked():
    """It is read on every turn: a line added takes one out (or a package's own map holds it). Counted with LF line
    ends, as Linux checks it out (a Windows checkout's CRLF is a byte more a line)."""
    size = len(CLAUDE.read_bytes().replace(b"\r\n", b"\n"))
    assert size <= MOST_BYTES, f"CLAUDE.md is {size:,} bytes, over {MOST_BYTES:,}: move a detail into the package's map"


def test_the_size_is_the_same_whatever_the_line_ends_a_checkout_has(tmp_path, monkeypatch):
    """B-3020: a Windows checkout ends each line in CRLF, so a CLAUDE.md within the limit on Linux was over it there
    (a byte more a line). Its size is counted with LF line ends."""
    text = CLAUDE.read_text(encoding="utf-8")
    near = text + "x" * (MOST_BYTES - len(text.encode("utf-8")) - 1) + "\n"  # at the limit, with LF
    windows = tmp_path / "CLAUDE.md"
    windows.write_bytes(near.encode("utf-8").replace(b"\n", b"\r\n"))
    monkeypatch.setattr(sys.modules[__name__], "CLAUDE", windows)
    test_claude_md_is_no_bigger_than_when_its_map_was_first_checked()


def package_maps() -> list[tuple[Path, str]]:
    return [(p, ast.get_docstring(ast.parse(p.read_text(encoding="utf-8"))) or "")
            for p in sorted((ROOT / "ap_coder").rglob("__init__.py")) if "__pycache__" not in p.parts]  # fmt: skip


@cache
def app_names() -> frozenset[str]:
    """Every module and package of ``ap_coder``, and every name one of them defines."""
    files = [p for p in (ROOT / "ap_coder").rglob("*.py") if "__pycache__" not in p.parts]
    return frozenset().union(*(defined(p) | {p.stem, p.parent.name} for p in files))


@cache
def app_code() -> str:
    """The app's code and pages, the packages' maps left out: where an import, an address, a setting or a page
    script's function is written."""
    texts = []
    for p in (q for kind in ("*.py", "*.js", "*.html") for q in (ROOT / "ap_coder").rglob(kind)):
        lines = p.read_text(encoding="utf-8").splitlines()
        if p.name == "__init__.py" and (doc := _docstring_node(p)) is not None:
            lines[doc.lineno - 1 : doc.end_lineno] = []
        texts.append("\n".join(lines))
    return "\n".join(texts)


def _docstring_node(path: Path) -> ast.Expr | None:
    body = ast.parse(path.read_text(encoding="utf-8")).body
    first = body[0] if body else None
    is_doc = (
        isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
    )
    return first if is_doc else None


def test_every_name_of_a_packages_map_is_there():
    """A package's map is its ``__init__.py`` docstring. Each name in it (in double backticks) is a module or a
    name of the app (each part of ``module.name``), or a word the app's code or pages write outside the packages'
    maps (an import, a page script's function); each path is found from the package, ``ap_coder/``, the top or a path
    before it, or is a file the code writes. Commands, examples and markup (``python -m ap_coder serve``,
    ``more=False``, ``|safe``) are left be."""
    missing = []
    for init, doc in package_maps():
        near = [init.parent]
        elsewhere = app_code()
        for token in (t.split("(", 1)[0] for t in re.findall(r"``([^`]+)``", doc)):
            if PATH.fullmatch(token):  # a file there, or one the code writes (``_metadata.json``)
                hits = found(token, near)
                near += hits
                missing += [] if hits or token in elsewhere else [f"{init.relative_to(ROOT)}: {token}"]
            elif NAME.fullmatch(token) and not (
                all(part in app_names() for part in token.split("."))
                or re.search(rf"(?<![\w.]){re.escape(token)}(?!\w)", elsewhere)
            ):
                missing.append(f"{init.relative_to(ROOT)}: {token}")
    assert not missing, missing


def _named_in_maps() -> set[Path]:
    """The files and folders of the app the maps name: CLAUDE.md's by path, a package's by path or by module name
    (``jde_ais``, ``jde_settings.load``), a path's ``<word>`` standing for any (``pages/<name>.py``)."""
    named: set[Path] = {
        ROOT / "ap_coder" / t
        for t in re.findall(r"`(\w+)`", CLAUDE.read_text(encoding="utf-8"))
        if (ROOT / "ap_coder" / t / "__init__.py").is_file()
    }  # fmt: skip  (the packages: "Code:")
    for bullet in map_bullets():
        paths: list[Path] = []
        for token in (t for t in re.findall(r"`([^`]+)`", bullet) if " " not in t and PATH.fullmatch(t)):
            paths += found(token, paths)
        named.update(paths)
    for init, doc in package_maps():
        near = [init.parent]
        for token in (t.split("(", 1)[0] for t in re.findall(r"``([^`]+)``", doc)):
            if PATH.fullmatch(token):
                near += found(token, near)
            elif NAME.fullmatch(token):
                first = token.split(".")[0]
                named.update(p for p in (init.parent / f"{first}.py", init.parent / first) if p.exists())
        named.update(near[1:])
    return named | {p.parent for p in named if p.name == "__init__.py"}


def test_every_module_of_the_app_is_in_a_map():
    """B-3022: details CLAUDE.md's map dropped were to go into the packages' maps (D-332), yet some went into none
    (coding's ``manager_mail``, bench's ``jde_group`` and ``companies_speed``): found only by searching. Each module
    and package of the app is named in its package's map or in CLAUDE.md's (a ``__main__.py`` is its package's
    ``python -m``)."""
    named = _named_in_maps()
    unnamed = []
    for init, _ in package_maps():
        for module in sorted(init.parent.iterdir()):
            is_module = module.suffix == ".py" and module.name not in ("__init__.py", "__main__.py")
            if (is_module or (module / "__init__.py").is_file()) and module not in named:
                unnamed.append(module.relative_to(ROOT).as_posix())
    assert not unnamed, f"name each in its package's __init__.py docstring: {unnamed}"
