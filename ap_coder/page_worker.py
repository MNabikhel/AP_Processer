"""The page reader's queue: invoices waiting for the vision model to read their pages.

Reading a page with a vision model takes minutes on a laptop without a graphics card, so it never holds up
*Process invoices*: an invoice is read and checked at once with OCR, queued, and the page reader reads it in the
background (a thread of the dashboard, or ``python -m ap_coder read-pages``). Its reading is then folded into the
invoice as one more independent reader, while AP hasn't touched the invoice yet; once someone has edited or decided
it, nothing in it is changed.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .capture.layout import IMAGE_EXTENSIONS
from .config import Settings
from .extraction import TEXT_EXTENSIONS

log = logging.getLogger(__name__)

IDLE_SECONDS = 30.0  # nothing waiting: look again after this long
OFF_SECONDS = 60.0  # page reader off or not ready: look again after this long
SCANNED_SOURCES = {"ocr", "mixed", "di"}
TEST_KEY = "page_reader_test"  # the last test of the page reader (Settings → Page reader), as JSON
FIGURES_KEY = "page_reader_figures"  # running totals of figures compared: {"digital": [same, figures], "scans": ...}


def saved_test(store: Any) -> dict[str, Any] | None:
    """The last test of the page reader on the invoice whose answers are known, or None."""
    try:
        raw = store.get_setting(TEST_KEY)
        test = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        return None
    return test if isinstance(test, dict) else None


def confirmed(store: Any, model: str) -> bool:
    """Whether ``model`` is the page reader AP linked: it passed the test in Settings → Page reader. A model that
    was never tested (or another model than the one tested) reads nothing on its own."""
    test = saved_test(store)
    return bool(model and test and test.get("ok") and test.get("model") == model)


def linked(model: str, db_path: Path | None = None) -> bool:
    """``confirmed`` in the database the dashboard uses, opened only when it exists: for the doctor and the launcher,
    which have no store at hand."""
    import os

    from . import paths
    from .store import Store

    db = Path(db_path or os.environ.get("AP_DB_PATH") or paths.default_db_path())
    try:
        return db.exists() and confirmed(Store(db), model)
    except Exception:  # a locked or damaged database: say "not linked", the dashboard tells the rest
        return False


def wants_reading(settings: Settings, path: Path, layout_source: str = "") -> bool:
    """Whether a newly processed invoice goes to the page reader on its own: in the background mode, scans and
    photos (or every invoice, when the scope says so). Text files never."""
    reader = settings.page_reader
    suffix = path.suffix.lower()
    if reader.mode != "auto" or suffix in TEXT_EXTENSIONS:
        return False
    if reader.scope == "all":
        return True
    return suffix in IMAGE_EXTENSIONS or layout_source in SCANNED_SOURCES


def queue_new_invoice(store: Any, settings: Settings, invoice_id: int | None, path: Path, layout_source: str) -> bool:
    """Queue a just-processed invoice for the page reader when it wants reading. Never raises."""
    if not invoice_id or store is None or not wants_reading(settings, path, layout_source):
        return False
    try:
        store.queue_page_read(invoice_id, "new invoice")
        return True
    except Exception as exc:  # the queue is an extra: processing must not fail over it
        log.warning("invoice %s: not queued for the page reader (%s)", invoice_id, exc)
        return False


def agreement(capture: Any) -> dict[str, Any]:
    """How the page reader's reading compares with what the invoice shows, field by field: ``agree`` (the value
    shown is the page reader's too), ``differ`` (it read something else), ``only`` (only it found the value)."""
    out: dict[str, Any] = {"agree": [], "differ": [], "only": []}
    if capture is None:
        return out
    from .capture.normalize import normalize_value

    for name, result in (capture.fields or {}).items():
        sources = result.sources or {}
        if "vlm" not in sources or result.value in (None, ""):
            continue
        readers = {s for s in sources if not s.startswith("other:") and s != "computed"}
        if normalize_value(name, sources["vlm"]) == normalize_value(name, result.value):
            out["only" if readers == {"vlm"} else "agree"].append(name)
        else:
            out["differ"].append(name)
    return out


def record_figures(store: Any, layout_source: str, comparison: Any) -> None:
    """Add one reading's figures to the running totals. On digital PDFs ("text": their own text is exact) the share
    read the same is the page reader's accuracy, measured on every invoice it reads; on scans it is how often it
    and OCR agree."""
    total = max(comparison.figures, comparison.first_figures)
    if not total:
        return
    try:
        totals = json.loads(store.get_setting(FIGURES_KEY) or "{}")
    except (ValueError, TypeError):
        totals = {}
    kind = "digital" if layout_source == "text" else "scans"
    same, seen = totals.get(kind) or [0, 0]
    totals[kind] = [same + comparison.confirmed, seen + total]
    store.set_setting(FIGURES_KEY, json.dumps(totals))


def figure_totals(store: Any) -> dict[str, tuple[int, int]]:
    """{"digital" | "scans": (figures read the same, figures compared)} so far."""
    try:
        totals = json.loads(store.get_setting(FIGURES_KEY) or "{}")
        return {k: (int(v[0]), int(v[1])) for k, v in totals.items() if isinstance(v, list) and len(v) == 2}
    except (ValueError, TypeError):
        return {}


@dataclass
class ReadOutcome:
    invoice_id: int
    status: str  # done | failed | skipped | postponed
    model: str = ""
    pages: int = 0
    seconds: float = 0.0
    updated: bool = False  # the invoice's proposal now includes the page reader's reading
    auto_approved: bool = False
    message: str = ""
    summary: dict[str, Any] = field(default_factory=dict)


def ready(settings: Settings, store: Any) -> tuple[str, str]:
    """(model, why not): the model that reads pages now, or why nothing can be read yet."""
    from . import page_reader

    if settings.page_reader.mode == "off":
        return "", "the page reader is off"
    status = page_reader.reader_status(settings)
    if not status.model or status.state in ("missing", "off", "down"):
        return "", status.note or "no model can read pages"
    if not confirmed(store, status.model):
        return "", (f"{status.model} hasn't passed its test yet: Settings → Page reader → Test the page reader "
                    "links it")  # fmt: skip
    if not store.has_reference():
        return "", "no GL accounts imported yet"
    return status.model, ""


def decided(inv: dict[str, Any]) -> str:
    """Why the page reader leaves this invoice alone, or "": AP has already approved, rejected, parked or edited it,
    and nothing in such an invoice is changed, so reading its pages would only take minutes for nothing."""
    from .store import REVIEW

    if inv.get("status") != REVIEW:
        return f"not read: the invoice is already {inv.get('status') or 'dealt with'}, so nothing in it would change"
    if inv.get("final_output") or inv.get("reviewer") or inv.get("edits"):
        return "not read: the invoice was already edited or decided, so nothing in it would change"
    return ""


class _BetweenPages:
    """A ``should_stop`` for ``page_reader.read_document`` that is heeded between pages only: a page under way is
    finished. The reader asks once before it starts, then once as each page starts (right after ``on_page``) and
    again and again while the page is read; only the first question after ``on_page`` (or before the first page)
    is passed on."""

    def __init__(self, should_stop: Callable[[], bool]) -> None:
        self._should_stop = should_stop
        self._between = True

    def on_page(self, page: int, total: int) -> None:
        self._between = True

    def __call__(self) -> bool:
        if not self._between:
            return False  # a page is being read: it is finished first
        self._between = False
        return bool(self._should_stop())


def read_one(settings: Settings, store: Any, *, cache_dir: Path | None = None, invoice_id: int | None = None,
             should_stop: Callable[[], bool] | None = None,
             finish_page: bool = False) -> ReadOutcome | None:  # fmt: skip
    """Read the next waiting invoice (or ``invoice_id``) with the page reader and fold the reading in. None when
    nothing is waiting or nothing can be read yet (the queue is left as it is).

    ``should_stop``: when it says stop, the reading stops and the invoice keeps its place in line ("postponed"; the
    pages already read are kept on disk and not read again). ``finish_page``: it is asked between pages only, so a
    page under way is finished first."""
    from . import page_reader
    from .capture.workflow import AUTONOMOUS_REVIEWER
    from .figures import compare_figures, first_pages
    from .pipeline import InvoicePipeline
    from .pipeline import _meta as pipeline_meta

    model, why = ready(settings, store)
    if not model:
        log.debug("page reader idle: %s", why)
        return None
    row = store.next_page_read(invoice_id) if invoice_id is not None else store.next_page_read()
    if not row:
        return None
    iid = int(row["invoice_id"])
    inv = store.get_invoice(iid)
    path = Path((inv or {}).get("source_path") or "")
    if inv is None or not path.exists():
        store.finish_page_read(iid, "skipped", model, 0, 0.0, "the invoice or its file is gone")
        return ReadOutcome(iid, "skipped", model, message="the invoice or its file is gone")
    why_not = decided(inv)
    if why_not:
        store.finish_page_read(iid, "skipped", model, 0, 0.0, why_not)
        return ReadOutcome(iid, "skipped", model, message=why_not)
    problem = page_reader.load_reader(settings, model)
    if problem:  # not enough memory, most often: it stays in the queue, asked again later
        store.finish_page_read(iid, "waiting", model, 0, 0.0, problem)
        return ReadOutcome(iid, "postponed", model, message=problem)
    t0 = time.perf_counter()
    stop, on_page = should_stop, None
    if should_stop is not None and finish_page:
        stop = _BetweenPages(should_stop)
        on_page = stop.on_page
    try:
        reading = page_reader.read_document(settings, path, model=model, on_page=on_page, should_stop=stop)
    except Exception as exc:  # the server failed, the page was blank, the model looped twice
        message = f"{type(exc).__name__}: {exc}"
        store.finish_page_read(iid, "failed", model, 0, time.perf_counter() - t0, message)
        return ReadOutcome(iid, "failed", model, message=message)
    seconds = sum(reading.seconds) if reading.seconds else time.perf_counter() - t0
    if reading.stopped:  # asked to stop (time is up, the app is closing): it keeps its place in line
        message = f"stopped after {len(reading.pages)} of {reading.page_count or '?'} page(s): read again later"
        store.finish_page_read(iid, "waiting", model, len(reading.pages), seconds, message)
        return ReadOutcome(iid, "postponed", model, len(reading.pages), seconds, message=message)
    if not reading.complete or not any(p.strip() for p in reading.pages):  # only a reading of every page is used
        message = reading.error or (
            "the page reader found nothing to read"
            if len(reading.pages) >= reading.page_count
            else f"the page reader read {len(reading.pages)} of {reading.page_count} page(s)"
        )
        store.finish_page_read(iid, "failed", model, len(reading.pages), seconds, message)
        return ReadOutcome(iid, "failed", model, len(reading.pages), seconds, message=message)

    pipeline = InvoicePipeline(settings, store.reference_data(), cache_dir=cache_dir, store=store)
    # The invoice is in the store already: its own row isn't a duplicate of it, nor counted in its vendor's history.
    result = pipeline.process(path, page_text=reading.pages, save=False, invoice_id=iid)
    if result.error or result.output is None or result.report is None:
        message = result.error or "the invoice could not be read again"
        store.finish_page_read(iid, "failed", model, len(reading.pages), seconds, message)
        return ReadOutcome(iid, "failed", model, len(reading.pages), seconds, message=message)
    summary = agreement(result.capture)
    # Only the pages the page reader read (at most AP_PAGE_READER_MAX_PAGES) are compared with OCR's.
    first = first_pages(result.extraction.content if result.extraction else "", len(reading.pages))
    figures = compare_figures(first, "\n\n".join(reading.pages))
    layout_source = result.capture.layout_source if result.capture is not None else ""
    if not reading.cached:  # a page read again from the cache is not counted twice
        record_figures(store, layout_source, figures)
    meta = pipeline_meta(result)
    meta["page_reader"] = {"model": model, "pages": len(reading.pages), "seconds": round(seconds, 1),
                           "cached": reading.cached, "at": dt.datetime.now().isoformat(timespec="seconds"),
                           "figures": figures.to_dict(), **{k: sorted(v) for k, v in summary.items()}}  # fmt: skip
    capture = result.capture.to_dict() if result.capture is not None else None
    updated = store.replace_proposal(iid, result.output, result.report.to_dict(), capture, meta)
    outcome = ReadOutcome(iid, "done", model, len(reading.pages), seconds, updated, summary=summary)
    if updated and result.autonomy.get("auto"):
        try:
            store.approve_invoice(iid, result.output, AUTONOMOUS_REVIEWER, login="ap-coder")
            outcome.auto_approved = True
        except (KeyError, ValueError) as exc:  # approved or rejected meanwhile
            log.info("invoice %s: not approved automatically (%s)", iid, exc)
    outcome.message = (
        "the proposal now includes the page reader's reading"
        if updated
        else "not applied: the invoice was already edited or decided, so nothing in it changed"
    )
    store.finish_page_read(iid, "done", model, len(reading.pages), seconds, "" if updated else outcome.message)
    return outcome


def run_queue(settings: Settings, store: Any, *, minutes: float = 30.0, cache_dir: Path | None = None,
              on_result: Callable[[ReadOutcome], None] | None = None) -> list[ReadOutcome]:  # fmt: skip
    """Read waiting invoices until the queue is empty or ``minutes`` have passed (a page under way is finished; an
    invoice whose pages aren't all read by then keeps its place in line, its pages read so far kept). For
    ``python -m ap_coder read-pages`` and a scheduled overnight run."""
    deadline = time.monotonic() + minutes * 60
    done: list[ReadOutcome] = []
    while time.monotonic() < deadline:
        outcome = read_one(settings, store, cache_dir=cache_dir, should_stop=lambda: time.monotonic() > deadline,
                           finish_page=True)  # fmt: skip
        if outcome is None or outcome.status == "postponed":
            if outcome is not None and on_result:
                on_result(outcome)
            break
        done.append(outcome)
        if on_result:
            on_result(outcome)
    return done


class BackgroundReader(threading.Thread):
    """Reads waiting invoices one at a time while the dashboard runs. Settings are read again each round, so a
    change in Settings → Page reader takes effect without a restart."""

    def __init__(self, settings_factory: Callable[[], Settings], store_factory: Callable[[], Any],
                 cache_dir: Path | None = None) -> None:  # fmt: skip
        super().__init__(name="ap-coder-page-reader", daemon=True)
        self._settings_factory = settings_factory
        self._store_factory = store_factory
        self._cache_dir = cache_dir
        self._halt = threading.Event()
        self.current: int | None = None  # the invoice being read now
        self.last: ReadOutcome | None = None

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        while not self._halt.is_set():
            pause = IDLE_SECONDS
            try:
                settings = self._settings_factory()
                if settings.page_reader.mode == "off":  # "ask": reads what AP asked for, nothing else is queued
                    pause = OFF_SECONDS
                else:
                    store = self._store_factory()
                    outcome = read_one(settings, store, cache_dir=self._cache_dir, should_stop=self._halt.is_set)
                    if outcome is not None:
                        self.last = outcome
                        log.info("page reader: invoice %s %s (%s page(s), %.0f s)", outcome.invoice_id,
                                 outcome.status, outcome.pages, outcome.seconds)  # fmt: skip
                        pause = 0.0 if outcome.status != "postponed" else OFF_SECONDS
                    else:
                        pause = IDLE_SECONDS
            except Exception:  # never let the thread die: the next round tries again
                log.exception("page reader round failed")
                pause = OFF_SECONDS
            self._halt.wait(pause)


_background: BackgroundReader | None = None
_background_lock = threading.Lock()


def start_background(settings_factory: Callable[[], Settings], store_factory: Callable[[], Any],
                     cache_dir: Path | None = None) -> BackgroundReader:  # fmt: skip
    """The dashboard's page-reader thread, started once per process."""
    global _background
    with _background_lock:
        if _background is None or not _background.is_alive():
            _background = BackgroundReader(settings_factory, store_factory, cache_dir)
            _background.start()
        return _background


def background() -> BackgroundReader | None:
    return _background if _background is not None and _background.is_alive() else None
