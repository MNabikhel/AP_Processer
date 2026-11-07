"""What each process keeps between requests stays within its bound (D-195): ``core/caches.py``'s ``size_of`` and
``Bounded``, every cache filled with entries the size of 300,000 invoices' within the bytes ``CACHES`` declares, and
what a web worker keeps in all within ``WORKER_CACHES_MOST``."""

from __future__ import annotations

import gc
import importlib
import random
import sys
import time
import tracemalloc
from types import MappingProxyType

import pytest

from ap_coder.ai import line_match, suggest
from ap_coder.bench.coding import ITEM_GL
from ap_coder.capture import supplier
from ap_coder.capture.reader import _common
from ap_coder.coding import po, terms
from ap_coder.core import caches, memory
from ap_coder.core.caches import CACHES, MB, WORKER_CACHES_MOST, Bounded, size_of
from ap_coder.core.store import Store, _base, reports
from ap_coder.core.store import summary as summary_module

RNG = random.Random(195)
ITEMS = sorted(ITEM_GL)


def _row(i: int) -> dict:
    """An invoice summary's row as a big company's are: about 1 KB."""
    return {"id": 300_000 - i, "status": "review", "vendor_key": f"vendor {i % 3000}", "vendor_name": f"Vendor {i}",
            "invoice_number": f"INV-{i:07d}", "invoice_date": "2026-10-01", "due_date": "2026-10-31", "currency": "CAD",
            "grand_total": 1741.1 + i, "export_batch": None, "created_at": "2026-10-01T10:11:12", "reviewer": "ann",
            "gst_hst_number": "123456789RT0001", "bank_account": "", "original_invoice_number": ""}  # fmt: skip


def _line(i: int) -> str:
    """A line description as invoices print them: an item, its code and a size, every one its own."""
    code = f"{RNG.choice('ABKMPX')}{RNG.choice('ABKMPX')}{10_000 + i}"
    return f"{RNG.choice(ITEMS)} - {code} {RNG.choice(['', 'LG', 'case'])}"


def _exact(value) -> int:
    """Every object ``value`` holds, each once: ``sys.getsizeof`` over what the gc says it refers to (a dict's keys
    too), every one of them."""
    seen, todo, total = set(), [value], 0
    while todo:
        obj = todo.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        total += sys.getsizeof(obj)
        if isinstance(obj, dict):
            todo += obj.keys()
        todo += gc.get_referents(obj)
    return total


def test_size_of_counts_what_a_value_holds_by_a_sample_of_its_items():
    rows = [_row(i) for i in range(20_000)]
    started = time.perf_counter()
    estimate = size_of(rows)
    took = time.perf_counter() - started
    assert abs(estimate - _exact(rows)) < 0.05 * _exact(rows) and took < 0.05  # (the whole: 0.3 s)
    shared = [MappingProxyType(r) for r in rows[:2000]]  # read-only rows: the dict behind each proxy counted
    assert abs(size_of(shared) - _exact(shared)) < 0.05 * _exact(shared) and size_of(shared) > 2000 * sys.getsizeof({})
    one = {"name": "x" * 1000}
    assert size_of([one] * 500) == sys.getsizeof([one] * 500) + size_of(one)  # the same object 500 times: once
    assert size_of([len, Store, caches]) == sys.getsizeof([len, Store, caches])  # code, classes, modules: not held


def test_a_bounded_cache_lets_go_of_the_ones_used_longest_ago_past_its_bytes():
    kept = Bounded("test", 10_000, size=len)
    for key in "abcd":
        kept[key] = "x" * 3000
    assert list(kept) == ["b", "c", "d"] and kept.held == 9000  # a went: 12,000 is over
    assert kept.get("b") and list(kept) == ["c", "d", "b"]  # read: used now, the last to go
    kept["e"] = "y" * 3000
    assert list(kept) == ["d", "b", "e"]
    kept["b"] = "w" * 100  # kept again: its new size, the last
    assert list(kept) == ["d", "e", "b"] and kept.held == 6100
    kept["big"] = "z" * 10_001  # bigger than the whole: kept alone (worked out once, not on every ask)
    assert list(kept) == ["big"] and kept.held == 10_001
    kept["d"], kept["e"], kept["b"] = "x" * 3000, "y" * 3000, "w" * 100
    assert list(kept) == ["d", "e", "b"] and kept.held == 6100
    assert kept.pop("d") and kept.popitem(last=False) == ("e", "y" * 3000) and kept.held == 100
    del kept["b"]
    assert kept.held == 0 and not kept.sizes
    kept.update({"f": "1" * 10, "g": "2" * 20})
    assert kept.held == 30 and kept.setdefault("f", "") == "1" * 10
    kept.move_to_end("f")
    assert list(kept) == ["g", "f"] and kept.held == 30
    kept.clear()
    assert kept.held == 0 and not kept.sizes


def _bounded() -> dict[str, Bounded]:
    found = {}
    for name in CACHES:
        module, _, attr = name.partition(":")
        value = getattr(importlib.import_module(module), attr)
        if isinstance(value, Bounded):
            found[name] = value
    return found


@pytest.mark.parametrize("name", sorted(_bounded()))
def test_each_bounded_cache_filled_as_at_300000_invoices_stays_within_its_bytes(name):
    """Each one filled with answers of 300,000 invoices' rows (a vendor's 8,000 invoices, a report of 8,000 rows: 6 MB
    each), offered twice its bytes: what it keeps stays within them, the latest kept."""
    cache = _bounded()[name]
    fresh = Bounded(cache.name, cache.most, cache.size)  # (the process's own left as it is)
    rows = [_row(i) for i in range(8000)]
    one = size_of(rows)
    assert 4 * MB < one < 8 * MB and abs(one - _exact(rows)) < 0.1 * one
    for n in range(2 * cache.most // one + 1):
        fresh[("db", f"answer {n}")] = ((1, n), rows, 0.0)  # (stamp, value, when), as each keeps them
        assert fresh.held <= cache.most and ("db", f"answer {n}") in fresh
    assert len(fresh) == cache.most // max(fresh.sizes.values())  # (as many as fit)
    assert fresh.held == sum(fresh.sizes.values()) <= CACHES[name].most


def test_the_pages_answers_kept_by_summary_memo_stay_within_their_bytes(tmp_path, monkeypatch):
    """Through ``summary_memo`` itself: 30 answers of 10 MB (a big company's lists) keep 112 MB at most, the ones asked
    longest ago going first; an answer asked again is kept the longest."""
    monkeypatch.setattr(summary_module, "_MEMO", Bounded("summary_memo", summary_module._MEMO.most,
                                                         summary_module._MEMO.size))  # fmt: skip
    store = Store(tmp_path / "ap.db")
    worked = []

    def answer(n: int):
        worked.append(n)
        return [f"{n:08d}" + "x" * 10_000 for _ in range(1000)]  # 10 MB

    for n in range(30):
        store.summary_memo(f"big/{n}", lambda n=n: answer(n), topic="")
        if n >= 1:
            store.summary_memo("big/0", lambda: answer(0), topic="")  # asked on every page
        assert summary_module._MEMO.held <= summary_module._MEMO.most
    assert worked == list(range(30))  # big/0 never worked out again: used, it stayed
    kept = {key[1] for key in summary_module._MEMO}
    assert "big/0" in kept and "big/29" in kept and "big/1" not in kept and len(kept) < 12


def test_the_reports_answers_kept_in_a_process_stay_within_their_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "_KEPT", Bounded("shared answers", reports._KEPT.most, reports._KEPT.size))
    store = Store(tmp_path / "ap.db")
    for n in range(12):
        store.shared_answer(f"report/{n}", lambda n=n: [f"{n:08d}" + "y" * 10_000 for _ in range(500)],
                            version="test-1", topic="")  # fmt: skip
        assert reports._KEPT.held <= reports._KEPT.most
    assert len(reports._KEPT) < 12 and any(key[1] == "report/11" for key in reports._KEPT)


def _traced(fill) -> int:
    """The bytes ``fill()`` leaves held."""
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    fill()
    gc.collect()
    held = tracemalloc.get_traced_memory()[0] - before
    tracemalloc.stop()
    return held


def _normalised(text: str, *_: object) -> None:
    memory._normalise_short(text)


def _tokened(a: str, b: str) -> None:
    po._tokens(a), po._tokens(b)


# Each pure function's cache: the arguments of one entry as a big company's invoices give them, and the caches it
# fills on the way (filled first: theirs, not its own).
LRU = {
    "ap_coder.ai.line_match:keys": (line_match.keys, lambda i: (_line(i),), _normalised),
    "ap_coder.ai.line_match:word_match": (line_match.word_match, lambda i: (
        f"{RNG.choice(ITEMS).split()[0].lower()}{i}", f"glove{i % 97}"), None),
    "ap_coder.core.memory:_token_set_short": (memory._token_set_short, lambda i: (_line(i),), _normalised),
    "ap_coder.core.memory:_normalise_short": (memory._normalise_short, lambda i: (_line(i),), None),
    "ap_coder.core.memory:vendor_key": (memory.vendor_key, lambda i: (f"Northern Courier Partners {i} Ltée",), None),
    "ap_coder.coding.po:similarity": (po.similarity, lambda i: (_line(i), _line(i + 1)), _tokened),
    "ap_coder.coding.po:_tokens": (po._tokens, lambda i: (_line(i),), None),
    "ap_coder.coding.terms:parse_terms": (terms.parse_terms, lambda i: (f"{i % 3 + 1}% {i % 25 + 5} net {i}",), None),
    "ap_coder.capture.reader._common:_plain": (_common._plain, lambda i: (f"Qty {i} {_line(i)}",), None),
    "ap_coder.capture.supplier:_printed_key": (supplier._printed_key, lambda i: ("invoice_number", f"INV-{i:07d}"),
                                               None),
    "ap_coder.capture.supplier:_printed_dates": (supplier._printed_dates, lambda i: (
        f"Invoice date {i % 28 + 1:02d}/10/2026, ref. {i}",), None),
    "ap_coder.capture.supplier:_printed_amount": (supplier._printed_amount, lambda i: (f"$ {i * 7.31:,.2f}",), None),
    "ap_coder.capture.supplier:clean_fields_needed": (supplier.clean_fields_needed, lambda i: (
        i % 400, i % 400 + i // 400 + 1, 0.95), None),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(LRU))
def test_each_pure_functions_cache_full_stays_within_its_bytes(name):
    """2,048 entries of a big company's (each its own: the worst), their key held by the cache alone, times the
    cache's maxsize: within what ``CACHES`` declares."""
    fn, args, fills = LRU[name]
    calls = [args(i) for i in range(2048)]
    for a in calls if fills else ():
        fills(*a)
    fn.cache_clear()
    held = _traced(lambda: [fn(*a) for a in calls]) + sum(sys.getsizeof(x) for a in calls for x in a)
    assert fn.cache_info().currsize == len(calls)  # (each its own)
    each = held / len(calls)
    assert each * fn.cache_info().maxsize <= CACHES[name].most, f"{each:.0f} B an entry"
    fn.cache_clear()


def test_the_memorys_index_and_patterns_kept_stay_within_their_bytes():
    """The GL suggestions' index and the examples' patterns of 10,000 lessons, each of a description of its own (a big
    company's memory: 50,000), within ``row_bytes`` a lesson; and the two kept together within ``most``: the latest,
    and another while both fit."""
    rows = [{"id": 10_000 - i, "vendor_key": f"vendor {i % 3000}", "vendor_name": f"Vendor {i % 3000}",
             "description": _line(i), "final_gl": str(5000 + i % 300), "final_cc": "CC1", "outcome": "approved",
             "amount": 10.0, "created_at": "2026-10-01T10:00:00", "invoice_id": i // 4, "line_number": i % 4}
            for i in range(10_000)]  # fmt: skip
    valid = frozenset(str(5000 + i) for i in range(300))
    for cache, build in ((suggest._MEMORY, lambda: suggest._build_memory(rows, valid)),
                         (memory._AGGREGATED, lambda: memory._aggregated(rows))):  # fmt: skip
        assert _traced(build) <= len(rows) * cache.row_bytes
        assert 50_000 * cache.row_bytes <= cache.most  # the whole memory, kept however big
    small = memory.RowsCache(slots=2, most=suggest._MEMORY.most, row_bytes=suggest._MEMORY.row_bytes)
    whole, learning = [{**r} for r in rows] * 5, rows[:2000]
    other = [{**r, "description": r["description"] + " (corrected)"} for r in rows] * 5
    small.get(whole, None, lambda: "whole")
    small.get(learning, None, lambda: "Learning's 2,000")
    assert [s[3] for s in small._slots] == ["Learning's 2,000", "whole"]  # both fit
    small.get(other, None, lambda: "after an approval")
    assert [s[3] for s in small._slots] == ["after an approval", "Learning's 2,000"]  # two of 50,000 don't


def test_a_web_workers_caches_full_stay_within_its_budget():
    """Every cache a web worker of the company server fills, full: within ``WORKER_CACHES_MOST`` (1,050 MB), under
    ``WORKER_MB`` with room for the requests under way."""
    from ap_coder.web.server import WORKER_MB

    held = sum(k.most for k in CACHES.values() if k.web)
    assert held <= WORKER_CACHES_MOST <= WORKER_MB * MB * 0.7


def test_the_invoices_whose_file_is_gone_are_remembered_within_a_bound(tmp_path, ground_truth, monkeypatch):
    """B-2026: an invoice whose file is no longer where it was recorded is looked for again 30 s later at the soonest
    (``invoice_file``), and each one looked for was remembered for the life of the process: after a move that lost the
    files of 300,000 invoices, every list and screen that showed one added an entry, never let go. They are let go
    past ``LOOKED_KEPT``, as the files found are."""
    monkeypatch.setattr(_base, "LOOKED_KEPT", 10)
    _base._NOT_FOUND.clear()
    store = Store(tmp_path / "ap.db")
    for n in range(30):
        path = tmp_path / f"gone-{n}.pdf"
        path.write_bytes(b"%PDF-1.4 an invoice")
        invoice_id = store.add_invoice(path, {**ground_truth, "invoice_number": f"G-{n}"}, {"requires_review": True})
        path.unlink()
        assert store.invoice_file(store.get_invoice(invoice_id)) is None
        assert len(_base._NOT_FOUND) <= 10
    assert store.invoice_file(store.get_invoice(invoice_id)) is None  # (looked for a moment ago: not again)


def test_a_sessions_memo_holds_what_was_read_at_the_databases_latest_moment_within_its_bytes(tmp_path):
    """B-2042: a session's memo (``Store._session_memo``: what a request reads twice, read once) kept every answer it
    was ever given until the session ended, those of moments the database had left too: the queue checked again after
    a change of settings (one session for the whole queue, a write after each batch) kept every vendor's invoices it
    met, and the server's background process grew to 4.7 GB at 300,000 invoices (a long download the same). It holds
    the answers of the database's latest moment only, within ``SESSION_MEMO_BYTES``."""
    from ap_coder.core.store import _base

    store = Store(tmp_path / "s.db")
    with store.session():
        held = _base._held_connections()[str(store.path)]
        for n in range(30):
            assert store._session_memo(("vendor", n), lambda n=n: [{"n": n, "rows": "x" * 200_000}])[0]["n"] == n
            store.set_setting("marker", str(n))  # (a write: what was read before is of another moment)
        assert len(held.memo) == 1
        for n in range(200):  # (the database unchanged: each kept, the oldest let go past the bytes)
            store._session_memo(("export", n), lambda: [{"rows": "y" * 1_000_000}])
        assert (
            len(held.memo) < 400 and sum(len(v[1][0]["rows"]) for v in held.memo.values()) <= _base.SESSION_MEMO_BYTES
        )
        again = store._session_memo(("export", 199), lambda: [{"rows": "read again"}])
        assert again[0]["rows"] == "y" * 1_000_000  # (the latest is kept: read once)
