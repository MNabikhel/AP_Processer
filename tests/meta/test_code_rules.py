"""Bug lessons that came back: each rule here turns a lesson docs/BUGS.md logged more than once into a check of the
whole code, so the same mistake cannot come back in a new place."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys

import pytest

from tests.conftest import ROOT

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


# A web worker runs every ``async def`` on its one event loop: while one waits on the database or a file, every other
# request to that worker waits too (B-1660). Only the awaits (the form, an upload's body) stay in an async handler; the
# rest runs on a threadpool thread: ``return await run_in_threadpool(_work, request, form)``, ``_work`` a plain
# function marked ``@one_connection`` (web/core.py), as the review screen's Check does.
_STORE_CALLS = {"get_store", "current_store"}
_FILE_CALLS = {"open", "read_bytes", "write_bytes", "read_text", "write_text"}
_PAGE_CALL = "render"  # a page drawn (web/core.py): its frame reads the database
_FLASH_CALLS = {"done", "keep_flash", "with_flash"}  # a message kept for the next page (web/core.py): written
# Async functions whose waits are known and left so (none: the review screen's grid actions and the upload check were
# the last, converted at phase 2's integration).
_STILL_ON_THE_LOOP: dict[str, set[str]] = {}


def _waits_on_the_loop(source: str) -> list[tuple[str, int]]:
    """(the async function, the line) of each call in an ``async def`` that waits on the database or a file there:
    get_store(), a store method, a function given the store, a page drawn, open() or a path read or written. A call
    awaited is not (an awaitable does not hold the loop), nor what ``run_in_threadpool`` is given to run."""
    found: set[tuple[str, int]] = set()

    def store(node: ast.AST) -> bool:
        if isinstance(node, ast.Call):
            return getattr(node.func, "id", "") in _STORE_CALLS
        return getattr(node, "id", getattr(node, "attr", "")) == "store"  # store, self.store, app.state.store

    def waits(call: ast.Call) -> bool:
        name = getattr(call.func, "id", "") or getattr(call.func, "attr", "")
        if name == _PAGE_CALL or (isinstance(call.func, ast.Name) and name in {*_STORE_CALLS, "open"}):
            return True
        if name in _FLASH_CALLS and (name != "done" or len(call.args) > 2 or call.keywords):
            return True  # (``done(request, url)`` alone keeps no message)
        if isinstance(call.func, ast.Attribute) and (store(call.func.value) or name in _FILE_CALLS):
            return True
        return any(store(a) for a in [*call.args, *(k.value for k in call.keywords)])

    def visit(node: ast.AST, function: ast.AsyncFunctionDef, awaited: bool = False) -> None:
        if isinstance(node, ast.AsyncFunctionDef) and node is not function:
            return  # checked on its own
        children = list(ast.iter_child_nodes(node))
        if isinstance(node, ast.Call):
            if (getattr(node.func, "id", "") or getattr(node.func, "attr", "")) == "run_in_threadpool":
                children = [c for c in children if c is node.func or not isinstance(c, ast.Lambda)]
            elif not awaited and waits(node):
                found.add((function.name, node.lineno))
        for child in children:
            visit(child, function, awaited=isinstance(node, ast.Await) and child is node.value)

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name != "lifespan":  # the start and stop: no request waits
            for statement in node.body:
                visit(statement, node)
    return sorted(found, key=lambda f: f[1])


def test_no_async_handler_waits_on_the_database_or_a_file_on_the_event_loop():
    """B-1660: the ERP register's import, the vendor master's, teaching from the ERP history and every save of a
    setup page made their store calls on the event loop, holding up every page, poll and login of their web worker
    meanwhile; the pages' handlers and the middlewares are checked (ap_coder/web)."""
    found = []
    for path in sorted((ROOT / "ap_coder" / "web").rglob("*.py")):
        name = path.relative_to(ROOT).as_posix()
        still = _STILL_ON_THE_LOOP.get(name, set())
        for function, line in _waits_on_the_loop(path.read_text(encoding="utf-8")):
            if function not in still:
                found.append(f"{name}:{line} ({function})")
    assert not found, "run it with run_in_threadpool on a @one_connection function (web/core.py): " + ", ".join(found)


def test_the_handlers_left_on_the_loop_are_still_async_functions_of_their_files():
    """The list above names functions that exist: one converted to a plain function, or renamed, comes off it."""
    for name, functions in _STILL_ON_THE_LOOP.items():
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
        gone = ", ".join(sorted(functions - defined))
        assert not gone, f"{name} has no async {gone} any more: take it off the list"


def test_the_event_loop_rule_finds_what_it_should():
    """The rule above, on the calls that hold the loop and on the forms that don't."""
    bad = """
async def a(request):
    store = get_store(request)
async def b(request, store):
    form = await posted_form(request)
    store.save(form)
async def c(request, store):
    return done(request, jde.load(store))
async def d(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()
async def e(store):
    await run_in_threadpool(work, store.everything())
async def f(self, folder):
    (folder / "x").write_bytes(b"")
    self.store.log_event("x")
async def g(request):
    return core.render(request, "error.html", "")
async def h(request):
    return done(request, "/settings", "Saved.")
"""
    safe = """
async def a(request):
    form = await posted_form(request)
    return await run_in_threadpool(_save, request, form)
async def b(store, token):
    user = await run_in_threadpool(store.session_user, token)
    await run_in_threadpool(lambda: store.log_event("x"))
    await run_in_threadpool(export, store, full=True)
async def c(store, request):
    await run_in_threadpool(core.render, request, "error.html", "")
    return await store.fetch()
async def d(request):
    return done(request, "/settings")
"""
    assert _waits_on_the_loop(bad) == [("a", 3), ("b", 6), ("c", 8), ("d", 10), ("e", 13), ("f", 15), ("f", 16),
                                       ("g", 18), ("h", 20)]  # fmt: skip
    assert _waits_on_the_loop(safe) == []


# Every request of the pages' scripts has a deadline (D-161) and sees the login's answer (D-185): ``fetchWithin``
# (app.js) is their one fetch. A bare ``fetch(`` waited for ever on a server that never answered, and an answer that
# was the login's went unseen; an XMLHttpRequest (the upload, for its progress) is given up (``abort()``) once no
# answer came in time (B-1926).
JS = ROOT / "ap_coder" / "web" / "static" / "js"


def _bare_requests(name: str, source: str) -> list[str]:
    """Each ``fetch(`` of a page's script outside app.js's ``fetchWithin``, and an XMLHttpRequest never aborted."""
    import re

    code = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), source, flags=re.S)  # (comments: not code)
    code = re.sub(r"(^|[^:\\])//[^\n]*", r"\1", code)
    if name == "app.js" and "export function fetchWithin(" in code:
        start = code.index("export function fetchWithin(")
        code = code[:start] + code[code.index("\n}\n", start) :]
    found = [f"{name}: {code[: m.start()].count(chr(10)) + 1}" for m in re.finditer(r"\bfetch\(", code)]
    if "XMLHttpRequest" in code and ".abort()" not in code:
        found.append(f"{name}: an XMLHttpRequest with no deadline")
    return found


def test_every_request_of_the_pages_scripts_has_a_deadline_and_sees_the_login():
    found = [hit for path in sorted(JS.glob("*.js")) for hit in _bare_requests(path.name, path.read_text("utf-8"))]
    said = "send it with fetchWithin (app.js), which has a deadline and says when the session ended: "
    assert not found, said + ", ".join(found)


def test_the_deadline_rule_finds_what_it_should():
    bad = """
const a = await fetch("/review/1/words");
window.fetch(url, { keepalive: true });
const xhr = new XMLHttpRequest();
"""
    safe = """
/* fetch( in a comment */
// fetch("/x") either
const a = await fetchWithin("/review/1/words", {}, ACTION_MS);
const url = "https://example.invalid/x"; // fetch(
const xhr = new XMLHttpRequest();
setTimeout(() => xhr.abort(), LOOK_MS);
"""
    helper = """
export function fetchWithin(url, options = {}, ms = LOOK_MS) {
  if (!ms) return fetch(url, options);
  return fetch(url, { ...options, signal });
}
export const after = 1;
"""
    assert _bare_requests("x.js", bad) == ["x.js: 2", "x.js: 3", "x.js: an XMLHttpRequest with no deadline"]
    assert _bare_requests("x.js", safe) == []
    assert _bare_requests("app.js", helper) == [] and len(_bare_requests("other.js", helper)) == 2
