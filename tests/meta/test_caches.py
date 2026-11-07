"""Every per-process cache declares its bound (D-195): what a module of ``ap_coder`` keeps at its top level (an empty
dict, set or list it fills, an ``OrderedDict``, a ``Bounded``, a ``RowsCache``, an ``lru_cache``) is either a cache
listed in ``core/caches.py``'s ``CACHES``, with the bytes it holds at most, or state listed here (``STATE``) with
what bounds it. A cache whose entries grow with the database is a ``Bounded``; together a web worker's stay within
``WORKER_CACHES_MOST``. Both lists only name what is still there."""

from __future__ import annotations

import ast
import functools
import importlib

from ap_coder.core import caches
from ap_coder.core.caches import CACHES, MB, WORKER_CACHES_MOST, Bounded
from ap_coder.core.memory import RowsCache
from tests.conftest import ROOT

# Module state that is not a cache of answers ("module:name"): what bounds it.
STATE = {
    "ap_coder.ai.local_llm:_FAILOVERS": "one breaker a set of model servers set up",
    "ap_coder.ai.local_llm:_UNLISTED": "one status a model server",
    "ap_coder.ai.local_llm:_HTTP_CLIENTS": "one client a model server and way to it",
    "ap_coder.ai.local_llm:_mode_by_model": "one flag a model",
    "ap_coder.ai.local_llm:_thinking_off_by_model": "one flag a model",
    "ap_coder.ai.local_llm:_thinking_on_by_model": "one flag a model",
    "ap_coder.ai.page_reader:_load_failures": "one failure a model, let go when it loads",
    "ap_coder.ai.page_worker:_READING": "the invoices being read now",
    "ap_coder.capture.layout:_ENGINES": "the OCR engines, one each, made once where scans are read",
    "ap_coder.capture.layout:_pruned": "one time a folder",
    "ap_coder.capture.layout:_written": "one count a folder",
    "ap_coder.core.config:_FROM_ENV_FILE": "the .env's settings",
    "ap_coder.core.paths:_kept_pruned": "one time a folder",
    "ap_coder.core.store._base:_RESTORING": "the databases being restored now",
    "ap_coder.core.store.learning:_READING": "a lock a memory read (one a database and cap)",
    "ap_coder.core.store.reports:_WORKING": "a lock an answer, let go past 4 x KEPT_HERE",
    "ap_coder.core.store.reports:_ASKED": "one time an answer was noted asked, 4 x KEPT_HERE at most (D-198)",
    "ap_coder.core.store.summary:_COMPUTING": "a lock an answer, let go past COMPUTING_KEPT",
    "ap_coder.core.store.summary:_ONE_GO": "one flag",
    "ap_coder.core.store.users:_DUMMY": "one hash",
    "ap_coder.erp.jde_sync:_DOWN": "one failure a database",
    "ap_coder.erp.jde_sync:_BIG": "the suppliers with too many vouchers to read whole (a pair of names each)",
    "ap_coder.erp.jde_sync:_CLIENTS": "one client a database",
    "ap_coder.ops.health:_LOOKING": "one thread a look under way",
    "ap_coder.ops.offline:_warned": "one name a model",
    "ap_coder.ops.service_windows:_JOBS": "the service host's job objects",
    "ap_coder.ops.system_log:_told": "what was told in the last hour (B-2027)",
    "ap_coder.web.document:_WRITTEN": "one count a folder",
    "ap_coder.web.document:_ROOM": "one free space a folder",
    "ap_coder.web.jobs:_ENDED": "the bulk approvals a request waits for, let go as they end",
    "ap_coder.web.jobs:_HERE": "one thread a database",
    "ap_coder.web.pages.downloads:_HERE": "one thread a database",
    "ap_coder.web.pages.process:_uploads": "a laptop's uploads of the last hour, by person",
    "ap_coder.web.pages.process:_LISTING": "the folders being listed now",
    "ap_coder.web.pages.users:_waiting": "passwords shown once, let go after SHOWN_FOR",
    "ap_coder.web.security:_SLOT": "this worker's access log",
}  # fmt: skip

# A container made empty (``dict()``; ``dict(TABLE)`` is a constant), and what makes a cache.
EMPTY_CALLS = ("dict", "OrderedDict", "set", "deque")
CACHE_CALLS = ("defaultdict", "Bounded", "RowsCache", "lru_cache", "_PictureCache", "WeakValueDictionary",
               "WeakKeyDictionary")  # fmt: skip


def _kept_here(source: str) -> list[str]:
    """The names a module's top level keeps something in: an empty dict, set or list it fills (``{}``, ``dict()``...), a
    container or cache made by a call of ``CACHE_CALLS`` (``lru_cache(...)(f)`` too), and functions with an
    ``lru_cache`` or ``cache`` decorator."""
    found = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any("cache" in ast.unparse(d) for d in node.decorator_list):
                found.append(node.name)
            continue
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        empty = isinstance(value, (ast.Dict, ast.List, ast.Set)) and not (
            getattr(value, "keys", None) or getattr(value, "elts", None))  # fmt: skip
        call = value
        while isinstance(call, ast.Call) and isinstance(call.func, ast.Call):  # lru_cache(maxsize=...)(function)
            call = call.func
        called = ast.unparse(call.func).split(".")[-1] if isinstance(call, ast.Call) else ""
        made = called in CACHE_CALLS or (called in EMPTY_CALLS and not call.args and not call.keywords)
        if empty or made:
            found += [t.id for t in targets if isinstance(t, ast.Name)]
    return found


@functools.cache
def _found() -> frozenset[str]:
    """Every "module:name" a module of ``ap_coder`` (not the benchmark's) keeps something in."""
    found = set()
    for path in sorted((ROOT / "ap_coder").rglob("*.py")):
        parts = path.relative_to(ROOT).with_suffix("").parts
        if "bench" in parts:
            continue
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        found |= {f"{module}:{name}" for name in _kept_here(path.read_text(encoding="utf-8-sig"))}
    return frozenset(found)


def _resolve(name: str):
    module, _, attr = name.partition(":")
    return getattr(importlib.import_module(module), attr)


def test_every_per_process_cache_declares_its_bound():
    new = sorted(_found() - set(CACHES) - set(STATE))
    assert not new, ("a module keeps something for the life of the process: list it in core/caches.py's CACHES with "
                     "the bytes it holds at most (a Bounded when its entries grow with the database), or here in "
                     f"STATE with what bounds it (D-195): {new}")  # fmt: skip


def test_the_lists_name_only_what_is_still_there():
    stale = sorted((set(CACHES) | set(STATE)) - _found())
    assert not stale, f"no longer kept: take them off the lists (they only name what is there): {stale}"
    assert not set(CACHES) & set(STATE)
    assert all(why.strip() for why in STATE.values()) and all(k.bound.strip() for k in CACHES.values())


def test_a_bounded_cache_is_declared_with_its_own_bytes_and_an_lru_cache_has_a_size():
    for name, kept in CACHES.items():
        found = _resolve(name)
        if isinstance(found, Bounded):
            assert found.most == kept.most, f"{name}: Bounded to {found.most / MB:g} MB, declared {kept.most / MB:g}"
            assert kept.bound.startswith("Bounded"), name
        else:
            assert not kept.bound.startswith("Bounded"), f"{name} is declared Bounded but is not"
        if isinstance(found, RowsCache):
            assert found.most == kept.most and found.row_bytes, f"{name}: within {found.most / MB:g} MB"
        if isinstance(found, functools._lru_cache_wrapper) and found.cache_info().maxsize is None:  # (functools.cache)
            assert any(w in kept.bound for w in ("one", "fixed", "once")), f"{name}: an unbounded cache of what?"


def test_a_web_workers_caches_together_stay_within_their_budget():
    held = sum(k.most for k in CACHES.values() if k.web)
    assert held <= WORKER_CACHES_MOST, f"{held / MB:g} MB declared for a web worker, over {WORKER_CACHES_MOST / MB:g}"


def test_the_rule_finds_what_it_should():
    source = """
from functools import lru_cache
import functools
from collections import OrderedDict
_A = {}
_B: dict[str, int] = {}
_C = OrderedDict()
_D = Bounded("d", 1)
_E = lru_cache(maxsize=8)(len)
CONSTANT = {"a": 1}
NAMES = ("x", "y")
LETTERS = set("aeiou")
_F = set()
@lru_cache(maxsize=4)
def f(x): return x
@functools.cache
def g(): return 1
def h():
    local = {}
"""
    assert _kept_here(source) == ["_A", "_B", "_C", "_D", "_E", "_F", "f", "g"]
    assert caches.Kept(1, "x").web
