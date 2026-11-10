"""What each process keeps between requests, and how much (D-195): every per-process cache of ``ap_coder`` is listed
in ``CACHES`` with its bound, and those whose entries grow with the database are bounded by bytes (``Bounded``),
the ones used longest ago let go first. A cache only makes a page faster: what it gives is what working it out
again gives, so letting an entry go changes no answer. In all they stay within ``WORKER_CACHES_MOST`` a process, so
a web worker of the company server keeps well under ``web.server.WORKER_MB`` at 300,000 invoices.

``size_of`` is what a value holds, about: ``sys.getsizeof`` of it and of what it holds, each object once, a big
container by a sample of its items (so measuring 100,000 rows takes a millisecond, not a second)."""

from __future__ import annotations

import gc
import itertools
import sys
import threading
import types
from collections import deque
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any

MB = 1024 * 1024
SAMPLE = 32  # items of a bigger container measured, spread over it; the others counted at their average
DEPTH = 12  # levels measured inside a value; below that, an object counts its own size only
VISITS = 20_000  # objects measured for one value at most (then each counts its own size only)
_LEAVES = (str, bytes, bytearray, int, float, complex, bool, type(None), range, memoryview)
# What a cache may refer to but doesn't hold: code, classes and modules are the program's, not the cache's.
_NOT_HELD = (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.MethodType,
             types.CodeType, types.FrameType, type(threading.Lock()), threading.Thread)  # fmt: skip


def size_of(value: Any, sample: int = SAMPLE) -> int:
    """About the bytes ``value`` holds: its ``sys.getsizeof`` and that of everything it refers to, each object
    once. A dict, list, tuple, set or deque of more than ``sample`` items is measured by ``sample`` of them spread
    over it: the first (which also pays for what the items share, such as a row's column names) plus the average of
    the others for each item not measured. Classes, functions and modules it refers to are not counted."""
    seen: set[int] = set()
    budget = [VISITS]

    def measure(obj: Any, depth: int) -> int:
        if id(obj) in seen or isinstance(obj, _NOT_HELD):
            return 0
        seen.add(id(obj))
        own = sys.getsizeof(obj, 0)
        budget[0] -= 1
        if isinstance(obj, _LEAVES) or depth >= DEPTH or budget[0] <= 0:
            return own
        if isinstance(obj, dict):  # (its keys too: the gc doesn't visit a dict's str keys)
            return own + measure_items(obj.items(), len(obj), depth, pairs=True)
        if isinstance(obj, (list, tuple, set, frozenset, deque)):
            return own + measure_items(obj, len(obj), depth)
        held = gc.get_referents(obj)  # an object's attributes (its dict's values, its slots), a mapping proxy's dict
        return own + measure_items(held, len(held), depth)

    def measure_items(items: Any, n: int, depth: int, pairs: bool = False) -> int:
        if n == 0:
            return 0
        step = 1 if n <= sample else n // sample
        sizes = []
        for item in itertools.islice(items, 0, None, step):
            sizes.append(measure(item[0], depth + 1) + measure(item[1], depth + 1) if pairs else
                         measure(item, depth + 1))  # fmt: skip
            if len(sizes) == sample:
                break
        if len(sizes) == n:
            return sum(sizes)
        rest = sizes[1:] or sizes
        return sizes[0] + round((n - 1) * sum(rest) / len(rest))

    return measure(value, 0)


class Bounded(dict):
    """A per-process cache's dict whose entries are kept within ``most`` bytes: each entry's size is worked out as it
    is kept (``size(value)``, by default ``size_of``), and the entries used longest ago (the first in the dict) are let
    go while they are over. An entry is used when it is kept or read (``get``). The latest kept always stays, alone
    when it is bigger than ``most`` (then worked out once, not on every ask: an answer is bounded by what makes it,
    the learning memory by FEEDBACK_READ lessons). The caller's own bounds (entries, rows) stay as they were, and so
    does its locking: the dict changes only where the caller changes it, under the caller's lock.
    ``popitem(last=False)`` and ``move_to_end`` as an ``OrderedDict``'s (the oldest first, put last)."""

    def __init__(self, name: str, most: int, size: Callable[[Any], int] | None = None) -> None:
        super().__init__()
        self.name, self.most, self.size = name, most, size or size_of
        self.sizes: dict[Hashable, int] = {}
        self.held = 0

    def __setitem__(self, key: Hashable, value: Any) -> None:
        size = max(0, int(self.size(value)))
        self.pop(key, None)
        while self and self.held + size > self.most:
            self.pop(next(iter(self)))
        dict.__setitem__(self, key, value)
        self.sizes[key] = size
        self.held += size

    def __delitem__(self, key: Hashable) -> None:
        dict.__delitem__(self, key)
        self.held -= self.sizes.pop(key, 0)

    def pop(self, key: Hashable, *default: Any) -> Any:
        if key in self:
            self.held -= self.sizes.pop(key, 0)
        return dict.pop(self, key, *default)

    def popitem(self, last: bool = True) -> tuple[Any, Any]:
        if not self:
            raise KeyError("popitem(): the cache is empty")
        key = next(reversed(self)) if last else next(iter(self))
        return key, self.pop(key)

    def clear(self) -> None:
        dict.clear(self)
        self.sizes.clear()
        self.held = 0

    def setdefault(self, key: Hashable, default: Any = None) -> Any:
        if key not in self:
            self[key] = default
        return self.get(key, default)

    def update(self, *args: Any, **kwargs: Any) -> None:
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def move_to_end(self, key: Hashable) -> None:
        """``key`` put last: the last to be let go (as ``OrderedDict.move_to_end``)."""
        dict.__setitem__(self, key, dict.pop(self, key))

    def get(self, key: Hashable, default: Any = None) -> Any:
        """The entry of ``key`` (``default`` when none), put last: used now, it is the last to be let go."""
        if key not in self:
            return default
        self.move_to_end(key)
        return dict.__getitem__(self, key)


# --- Every per-process cache, and its bound ------------------------------------------------------------------------

WORKER_CACHES_MOST = 1050 * MB  # what a web worker of the company server keeps in all its caches, at most


@dataclass(frozen=True)
class Kept:
    """A per-process cache's bound: ``most``, the bytes it holds in one process at most (a ``Bounded``'s own
    ``most``; else what its other bounds come to with entries the size of 300,000 invoices', measured in
    tests/core/test_caches.py), how it is bounded, and whether a web worker of the company server fills it
    (``web``: not a laptop's page pictures, kept on disk there, nor what only reading a scan draws)."""

    most: int
    bound: str
    web: bool = True


# Each cache, "module:name", with its bound (D-195). A new per-process cache is listed here (tests/meta/test_caches.py
# finds them): one whose entries grow with the database is a ``Bounded``, kept within its bytes.
CACHES: dict[str, Kept] = {
    # The ones that grow with the database: bounded by bytes, the ones used longest ago going first.
    "ap_coder.core.store.summary:_MEMO": Kept(112 * MB, "Bounded; 64 answers (the vendor list of 20,000 vendors: "
                                                        "15 MB)"),
    "ap_coder.core.store._base:_FEEDBACK": Kept(96 * MB, "Bounded; 8 memories of FEEDBACK_READ lessons (50,000: "
                                                         "64 MB)"),
    "ap_coder.core.store._base:_PAST_MEMORY": Kept(80 * MB, "Bounded; PAST_KEPT vendors, PAST_ROWS_KEPT lessons"),
    "ap_coder.core.store.vendors:_VENDOR_ROWS": Kept(64 * MB, "Bounded (VENDOR_ROWS_MOST); VENDOR_ROWS_KEPT rows, "
                                                              "VENDOR_KEYS_KEPT vendors"),
    "ap_coder.core.store.reports:_KEPT": Kept(32 * MB, "Bounded; KEPT_HERE answers"),
    "ap_coder.erp.jde_sync:_CACHE": Kept(32 * MB, "Bounded (CACHE_MOST); CACHE_SECONDS old at most (B-2025)"),
    "ap_coder.core.store._base:_VENDOR_LESSONS": Kept(8 * MB, "Bounded; VENDOR_LESSONS_KEPT vendors"),
    "ap_coder.coding.po:_KEPT": Kept(24 * MB, "Bounded (PO_KEPT_MOST); POs of PO_KEPT_FROM invoices or more (the "
                                              "300,000 seed's one, 3,382 invoices of 8 lines: 22.5 MB)"),
    "ap_coder.coding.po:_MATCHED": Kept(6 * MB, "Bounded (MATCHED_MOST); the matches of the POs read at each ask"),
    # Bounded by bytes their own way.
    "ap_coder.core.store._base:_PAST_LINES": Kept(64 * MB, "PAST_LINES_BYTES (PastLines.size), PAST_LINES_KEPT "
                                                           "vendors"),
    "ap_coder.ai.suggest:_PAST": Kept(48 * MB, "PAST_BYTES_KEPT of their own (_own_bytes: the copied containers' "
                                               "own size, about two thirds of what they hold), PAST_KEPT vendors"),
    "ap_coder.web.document:_png_cached": Kept(96 * MB, "PICTURE_CACHE_BYTES (a laptop's: the company server keeps "
                                                       "its pictures on disk)", web=False),
    "ap_coder.capture.layout:_PICTURES": Kept(32 * MB, "PICTURES_KEPT_BYTES (a scan's pages, for its second OCR "
                                                       "engine)", web=False),
    "ap_coder.ai.suggest:_MEMORY": Kept(104 * MB, "RowsCache: the GL suggestions' index of FEEDBACK_READ lessons "
                                                  "(95 MB when each has a description of its own), a second while "
                                                  "both fit"),
    "ap_coder.core.memory:_AGGREGATED": Kept(28 * MB, "RowsCache: the examples' patterns of FEEDBACK_READ lessons "
                                                      "(24 MB), a second while both fit"),
    # Bounded by their entries, each of a size that doesn't grow with the database.
    "ap_coder.ai.suggest:_OLDER": Kept(16 * MB, "PAST_KEPT vendors, PAST_ROWS_KEPT lines"),
    "ap_coder.erp.accruals:_PARTS": Kept(16 * MB, "PARTS_KEPT period ends of the purchase orders' accruals"),
    "ap_coder.core.store._base:_NOT_FOUND": Kept(24 * MB, "LOOKED_KEPT invoices (B-2026)"),
    "ap_coder.core.store._base:_FOUND": Kept(12 * MB, "LOOKED_KEPT files"),
    "ap_coder.core.store.accounts:_REFERENCE": Kept(8 * MB, "REFERENCE_KEPT databases' reference data (a chart of "
                                                            "1,500 accounts: 0.7 MB), WITH_PAIRS_KEPT while one "
                                                            "holds the pairs of a JD Edwards chart (14,700 "
                                                            "accounts, D-209: 3 MB more)"),
    "ap_coder.ai.suggest:_ACCOUNT_STEMS": Kept(8 * MB, "4 charts of accounts"),
    "ap_coder.web.pages.process:_LISTED": Kept(8 * MB, "8 folders' new files, LISTED_SECONDS (a laptop's: the "
                                                       "company server's are kept for every process)", web=False),
    "ap_coder.core.store.review_queue:_KEPT": Kept(16 * MB, "KEPT_MOST: the queue's lists and facts of 2 "
                                                          "databases, about 1.1 KB an invoice in the queue (5.4 MB "
                                                          "for 100,000 invoices' 3,036 and 2,122)"),
    "ap_coder.web.pages.review:_PAYS": Kept(6 * MB, "PAYS_KEPT Pay from texts decoded (360 B each), then all let "
                                                    "go"),
    "ap_coder.coding.bulk:_FOUND": Kept(4 * MB, "FOUND_KEPT findings texts"),
    "ap_coder.capture.table_finder:_found": Kept(4 * MB, "FOUND_KEPT documents' tables", web=False),
    "ap_coder.core.store._base:_METRICS": Kept(1 * MB, "one count a database"),
    "ap_coder.core.store.summary:_NAMES": Kept(1 * MB, "512 SQL texts' columns, then let go"),
    "ap_coder.core.store.summary:_FTS": Kept(1 * MB, "one flag a database"),
    "ap_coder.core.config:_RESOLVED": Kept(1 * MB, "the addresses of each host name set up"),
    "ap_coder.ai.local_llm:_cache": Kept(1 * MB, "one status a model server"),
    "ap_coder.ai.page_reader:_listings": Kept(1 * MB, "one list a model server"),
    "ap_coder.ops.health:_NOTICES": Kept(1 * MB, "one notice a certificate"),
    "ap_coder.ops.health:_LOOKED": Kept(1 * MB, "one look a database and part"),
    "ap_coder.ops.health:_monitors": Kept(1 * MB, "one a server.json"),
    "ap_coder.ops.offline:_checked": Kept(1 * MB, "one SHA-256 a model file's version"),
    "ap_coder.erp.jde:_RATES": Kept(1 * MB, "the tax rates' table"),
    "ap_coder.web.pages.help:_setup_cache": Kept(1 * MB, "one report a database"),
    # Pure functions' answers (lru_cache): their maxsize, at the size measured of an entry.
    "ap_coder.ai.line_match:word_match": Kept(34 * MB, "131,072 pairs of words, 0.25 KB each"),
    "ap_coder.coding.po:similarity": Kept(7 * MB, "8,192 pairs of descriptions, 0.75 KB each"),
    "ap_coder.core.memory:_token_set_short": Kept(48 * MB, "65,536 descriptions' words, 0.7 KB each"),
    "ap_coder.ai.line_match:keys": Kept(40 * MB, "65,536 descriptions' words, 0.6 KB each"),
    "ap_coder.core.memory:_normalise_short": Kept(36 * MB, "131,072 texts of 300 characters at most, 0.25 KB each"),
    "ap_coder.capture.supplier:clean_fields_needed": Kept(4 * MB, "16,384 counts"),
    "ap_coder.core.memory:vendor_key": Kept(12 * MB, "16,384 names"),
    "ap_coder.coding.po:_tokens": Kept(8 * MB, "4,096 descriptions' words"),
    "ap_coder.capture.reader._common:_plain": Kept(8 * MB, "16,384 printed words"),
    "ap_coder.capture.supplier:_printed_key": Kept(6 * MB, "16,384 printed spans"),
    "ap_coder.capture.supplier:_printed_dates": Kept(4 * MB, "16,384 printed spans"),
    "ap_coder.capture.supplier:_printed_amount": Kept(4 * MB, "16,384 printed spans"),
    "ap_coder.coding.terms:parse_terms": Kept(2 * MB, "4,096 terms"),
    "ap_coder.web.core:read_static": Kept(2 * MB, "the static files, read once"),
    "ap_coder.core.parsing:_one_for_one": Kept(1 * MB, "4,096 characters"),
    "ap_coder.capture.layout:_file_sha256": Kept(1 * MB, "256 files"),
    "ap_coder.web.document:_sha256": Kept(1 * MB, "256 files"),
    "ap_coder.web.document:_sizes_cached": Kept(1 * MB, "64 files' page sizes"),
    "ap_coder.capture.layout:_png_dpi": Kept(1 * MB, "8 pictures"),
    "ap_coder.ai.offline_coder:_rate_table_at": Kept(1 * MB, "4 tax tables"),
    "ap_coder.capture.confidence:calibration_table": Kept(1 * MB, "the one table"),
    "ap_coder.ops.offline:ppocrv4_files": Kept(1 * MB, "the one list"),
    "ap_coder.ops.offline:ppocrv5_files": Kept(1 * MB, "the one list"),
    "ap_coder.capture.reader.labels:_label_rx": Kept(1 * MB, "the labels' patterns, a fixed table"),
    "ap_coder.capture.layout:_libraries": Kept(1 * MB, "the one answer"),
    "ap_coder.capture.layout:_engine_signature": Kept(1 * MB, "one an engine"),
    "ap_coder.web.core:_one_connection_route": Kept(1 * MB, "one a page's route"),
    "ap_coder.web.pages.settings_reading:test_page_count": Kept(1 * MB, "the one answer"),
    "ap_coder.web.security:computer_names": Kept(1 * MB, "the one answer"),
}  # fmt: skip
