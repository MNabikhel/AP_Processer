"""Bug lessons that came back: each rule here turns a lesson docs/BUGS.md logged more than once into a check of the
whole code, so the same mistake cannot come back in a new place."""

from __future__ import annotations

import ast
import importlib.util
import re
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


TEXT_MODE = re.compile(r"[rwaxt+]*")  # a mode of open() with no "b": text
# The keywords pathlib's own take besides the encoding: a call with another (writers=, color=) is not a path's.
PATH_KEYWORDS = {"read_text": {"errors", "newline"}, "write_text": {"errors", "newline"},
                 "open": {"mode", "buffering", "errors", "newline"}}  # fmt: skip
# The rule's names (PATH_KEYWORDS'), each a whole word: a file with none of them holds none of its calls, not parsed.
PATH_CALL_NAMES = re.compile(r"\b(?:" + "|".join(PATH_KEYWORDS) + r")\b")
# The way out for a call the rule takes for a path's but is no file's (a PDF page's text, say): on its line, the
# comment "# encoding: not text (why)", the why said.
NOT_TEXT = re.compile(r"#\s*encoding: not text \([^)]+\)")
# Calls the rule below finds, kept as they are: each file and how many, and why (none since B-3038).
NO_ENCODING_KEPT: dict[str, int] = {}


def _named(node: ast.AST, name: str) -> bool:
    """Whether ``node`` is a call of ``name`` (``TextWriter(...)``, ``pymupdf.TextWriter(...)``), or the name."""
    node = node.func if isinstance(node, ast.Call) else node
    return getattr(node, "attr", getattr(node, "id", None)) == name


def _text_without_encoding(source: str) -> list[int]:
    """The lines of a path's ``read_text()``, ``write_text(data)`` or ``open(<a text mode>)`` with no encoding, by
    pathlib's own signatures (an encoding given by position, ``**kwargs`` or a keyword pathlib's has not, a module's
    function of that name such as tarfile.open, PyMuPDF's TextWriter, ``open()`` with no mode, which a page or a PDF
    has too, and a call said to be no file's, ``NOT_TEXT``, aside)."""
    if not PATH_CALL_NAMES.search(source):
        return []
    tree = ast.parse(source)
    lines = source.splitlines()
    modules: set[str] = set()
    writers: set[str] = set()  # PyMuPDF's TextWriters: made, or a parameter's type
    calls: list[ast.Call] = []  # x.read_text(...), x.write_text(...), x.open(...)
    for node in ast.walk(tree):  # one walk for all three
        if isinstance(node, ast.Import):
            modules |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and _named(node.value, "TextWriter"):
            writers |= {ast.unparse(target) for target in node.targets}
        elif isinstance(node, ast.arg) and node.annotation is not None and "TextWriter" in ast.unparse(node.annotation):
            writers.add(node.arg)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in PATH_KEYWORDS:
            calls.append(node)
    found = []
    for node in calls:
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        held = node.func.value
        if (
            "encoding" in keywords
            or None in keywords
            or set(keywords) - PATH_KEYWORDS.get(node.func.attr, set())
            or (isinstance(held, ast.Name) and held.id in modules)
            or ast.unparse(held) in writers
            or _named(held, "TextWriter")
            or any(NOT_TEXT.search(line) for line in lines[node.lineno - 1 : node.end_lineno])
        ):
            continue
        name, given = node.func.attr, len(node.args)
        mode = keywords.get("mode", node.args[0] if node.args else None)
        text = isinstance(mode, ast.Constant) and isinstance(mode.value, str) and TEXT_MODE.fullmatch(mode.value)
        if (name, given) in {("read_text", 0), ("write_text", 1)} or (name == "open" and given <= 1 and text):
            found.append(node.lineno)
    return sorted(found)


def test_every_path_read_or_written_as_text_names_its_encoding():
    """The rule above where ruff can't tell a path: it knows ``Path(...)`` itself, not a fixture's ``tmp_path / "x"``
    or a variable, so ``(tmp_path / "a.md").write_text("·")`` went unseen and was written in cp1252 on Windows."""
    found: dict[str, list[int]] = {}
    for folder in CODE:
        for path in sorted((ROOT / folder).rglob("*.py")):
            if lines := _text_without_encoding(path.read_text(encoding="utf-8")):
                found[path.relative_to(ROOT).as_posix()] = lines
    new = {name: lines for name, lines in found.items() if len(lines) > NO_ENCODING_KEPT.get(name, 0)}
    assert not new, (f"name the encoding (usually encoding='utf-8'); a call that writes no file, such as a PDF page's "
                     f"text, ends its line with '# encoding: not text (why)': {new}")  # fmt: skip
    assert {name: len(lines) for name, lines in found.items()} == NO_ENCODING_KEPT, "a call kept no longer is: say so"


def test_the_path_encoding_rule_finds_what_it_should():
    """Not PyMuPDF's write_text, to which an encoding is a TypeError, nor a call said to be no file's (B-3012)."""
    source = """import tarfile
(tmp_path / "a.csv").write_text("x")
path.read_text()
log.open("w")
path.open(mode="a")
path.write_text("x", encoding="utf-8")
path.read_text("utf-8")
path.write_text("x", "utf-8")
path.open("rb")
path.open("w", **options)
envfile.read_text(name)
tarfile.open("old.tar", "r")
pymupdf.open()
page.open()
archive.open("word/document.xml")
writer = pymupdf.TextWriter(page.rect)
writer.write_text(page)
def stamp(page, writer: pymupdf.TextWriter):
    writer.write_text(page)
pymupdf.TextWriter(page.rect).write_text(page)
page.write_text(page.rect, writers=writer)
self.writer = pymupdf.TextWriter(page.rect)
self.writer.write_text(page)
held.write_text(page)  # encoding: not text (a PDF page's text, PyMuPDF's)
held.write_text(page)  # encoding: not text
held.write_text(page)
"""
    assert _text_without_encoding(source) == [2, 3, 4, 5, 25, 26]  # (25: the way out says why, or it isn't one)


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
# answer came in time (B-1926), each by an abort or a timeout of its own, near it. ``navigator.sendBeacon``, an
# ``EventSource`` and a ``WebSocket`` have no deadline the page sets and see no login: none is used (B-1942).
JS = ROOT / "ap_coder" / "web" / "static" / "js"
NO_DEADLINE = "(an XMLHttpRequest with no abort or timeout of its own)"
_ASKS = (
    r"\bfetch\s*(?:\(|\.\s*(?:call|apply|bind)\b)"  # fetch(…), window.fetch(…), fetch.call(…)
    r"|\[\s*([\"'`])fetch\1\s*\]"  # window["fetch"]
    r"|=\s*(?:(?:window|globalThis|self)\s*\.\s*)?fetch\b"  # one under another name: const get = window.fetch
    r"|\bsendBeacon\b|\bnew\s+(?:EventSource|WebSocket)\b"
)
_NEAR = 40  # the lines after an XMLHttpRequest where its own abort or timeout is looked for


def _bare_requests(name: str, source: str) -> list[str]:
    """Each request of a page's script outside app.js's ``fetchWithin`` (``fetch(``, the other ways a page asks), and
    an XMLHttpRequest without an abort or a timeout of its own near it."""
    import re

    code = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), source, flags=re.S)  # (comments: not code)
    code = re.sub(r"(^|[^:\\])//[^\n]*", r"\1", code)
    if name == "app.js" and "export function fetchWithin(" in code:
        start = code.index("export function fetchWithin(")
        code = code[:start] + code[code.index("\n}\n", start) :]
    found = [(code[: m.start()].count("\n") + 1, "") for m in re.finditer(_ASKS, code)]
    lines = code.split("\n")
    for m in re.finditer(r"\bnew\s+XMLHttpRequest\b", code):
        at = code[: m.start()].count("\n")
        named = re.search(r"\b(\w+)\s*=\s*$", code[code.rfind("\n", 0, m.start()) + 1 : m.start()])  # xhr = new …
        near = "\n".join(lines[at : at + _NEAR])
        if not named or not re.search(rf"\b{named[1]}\s*\.\s*(?:abort\s*\(|timeout\s*=(?!=))", near):
            found.append((at + 1, f" {NO_DEADLINE}"))
    return [f"{name}: {line}{what}" for line, what in sorted(found)]


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
    assert _bare_requests("x.js", bad) == ["x.js: 2", "x.js: 3", f"x.js: 4 {NO_DEADLINE}"]
    assert _bare_requests("x.js", safe) == []
    assert _bare_requests("app.js", helper) == [] and len(_bare_requests("other.js", helper)) == 2


def test_the_deadline_rule_finds_the_other_ways_a_page_asks():
    """B-1942: the rule saw only ``fetch(``, and one ``.abort()`` anywhere in a file: ``navigator.sendBeacon``, an
    ``EventSource``, a ``WebSocket``, ``window["fetch"]`` and every XMLHttpRequest of a file where another was given
    up passed. Each is found; an XMLHttpRequest needs an abort or a timeout of its own, near it."""
    bad = """
navigator.sendBeacon("/review/1/check", body);
const events = new EventSource("/jobs/status");
const socket = new WebSocket("ws://127.0.0.1:8501/x");
window["fetch"]("/review/1/words");
const get = window.fetch;
const first = new XMLHttpRequest();
setTimeout(() => first.abort(), LOOK_MS);
const second = new XMLHttpRequest();
second.send(body);
new XMLHttpRequest().send(body);
"""
    safe = """
const xhr = new XMLHttpRequest();
xhr.timeout = LOOK_MS;
const other = new XMLHttpRequest();
other.open("POST", url);
const quiet = setTimeout(() => other.abort(), LOOK_MS);
"""
    assert _bare_requests("x.js", bad) == ["x.js: 2", "x.js: 3", "x.js: 4", "x.js: 5", "x.js: 6",
                                           f"x.js: 9 {NO_DEADLINE}", f"x.js: 11 {NO_DEADLINE}"]  # fmt: skip
    assert _bare_requests("x.js", safe) == []
