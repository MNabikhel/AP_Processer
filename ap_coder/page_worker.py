"""The page reader's queue: invoices waiting for OvisOCR2 to read their pages.

Reading a page with a vision model takes minutes on a laptop without a graphics card, so it never holds up
*Process invoices*: every invoice (digital PDFs, scans and photos alike) is read and checked at once with the PDF's
text or OCR, queued, and the page reader reads it in the background (a thread of the dashboard, or
``python -m ap_coder read-pages``). Its reading is then folded into the invoice as one more independent reader, while
AP hasn't touched the invoice yet; once someone has edited or decided it, nothing in it is changed. Until it has read
an invoice, that invoice is never approved without a person.

Before OvisOCR2 reads any invoice it passes a self-test on an invoice whose answers are known (``self_test_if_due``),
run on its own the first time a model is found, and again a day after a failure (or when a person asks).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .extraction import TEXT_EXTENSIONS

log = logging.getLogger(__name__)

IDLE_SECONDS = 30.0  # nothing waiting: look again after this long
OFF_SECONDS = 60.0  # page reader not ready: look again after this long
# The self-tests of the page reader, one per model, as JSON: {"models": {model: test}}. An older AP Coder kept only
# the last test there, as the test itself: read as that model's.
TEST_KEY = "page_reader_test"
RETEST_HOURS = 24.0  # a model that failed its self-test is tested again on its own after this long
RETEST_UNREAD_HOURS = 1.0  # ... or after this long when the test read nothing (LM Studio stopped, not enough memory)
TEST_PAGES = 2  # the test invoice's pages
TESTING_KEY = "page_reader_testing"  # the test under way, as JSON: model, started, pid, pages done (else "")
FIGURES_KEY = "page_reader_figures"  # running totals of figures compared: {"digital": [same, figures], "scans": ...}
MAX_TRIES = 3  # reads cut off by the model server before an invoice leaves the queue as failed
BLANK_WORDS = 3  # a page OCR finds fewer words on than this is blank: the page reader isn't shown it
# This process, as it marks the invoice it reads (``store.next_page_read``): its id, and a token of its own, so a
# process id used again by another program later is not taken for it.
READER_ID = f"{os.getpid()}:{secrets.token_hex(4)}"


def saved_tests(store: Any) -> dict[str, dict[str, Any]]:
    """Every model's last test of the page reader on the invoice whose answers are known: {model: test}."""
    try:
        raw = store.get_setting(TEST_KEY)
        saved = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        return {}
    if not isinstance(saved, dict):
        return {}
    if isinstance(saved.get("models"), dict):
        return {str(m): t for m, t in saved["models"].items() if isinstance(t, dict)}
    return {str(saved["model"]): saved} if saved.get("model") else {}  # the single test of an older AP Coder


def saved_test(store: Any, model: str | None = None) -> dict[str, Any] | None:
    """``model``'s last test, or None when it was never tested. Without a model: the latest test of any model."""
    tests = saved_tests(store)
    if model is not None:
        return tests.get(model)
    return max(tests.values(), key=lambda t: str(t.get("when") or ""), default=None)


def save_test(store: Any, record: dict[str, Any]) -> None:
    """Keep a test's result as its model's (the other models' tests are kept: testing one never unlinks another)."""
    tests = saved_tests(store)
    tests[str(record.get("model") or "")] = record
    store.set_setting(TEST_KEY, json.dumps({"models": tests}))


def confirmed(store: Any, model: str) -> bool:
    """Whether ``model`` is trusted to read invoices: it passed its self-test. A model that was never tested (or
    failed its last test) reads nothing; its readings are folded into invoices only once it passed."""
    test = saved_test(store, model) if model else None
    return bool(test and test.get("ok"))


def _test_age_hours(test: dict[str, Any], now: dt.datetime | None = None) -> float | None:
    """How long ago the test was run, in hours (None when it doesn't say)."""
    try:
        when = dt.datetime.fromisoformat(str(test.get("when") or ""))
    except ValueError:
        return None
    return ((now or dt.datetime.now()) - when).total_seconds() / 3600


def self_test_due(store: Any, model: str, now: dt.datetime | None = None) -> bool:
    """Whether ``model``'s self-test should run on its own now: it was never tested, or it failed its last test a day
    ago or more (an hour, when that test read nothing: LM Studio was stopped or short of memory). A model that passed
    is not tested again; a failure is not retried in a loop. A person can run it again at any time (``start_test``)."""
    if not model:
        return False
    test = saved_test(store, model)
    if test is None:
        return True
    if test.get("ok"):
        return False
    age = _test_age_hours(test, now)
    if age is None:
        return True
    unread = not test.get("fields_total")
    return age >= (RETEST_UNREAD_HOURS if unread else RETEST_HOURS)


def self_test(store: Any, model: str, now: dt.datetime | None = None) -> tuple[str, str]:
    """(state, plain-English detail) of ``model``'s self-test: "passed", "failed", "running", "pending" (it runs on
    its own before the first invoice is read), or "no model" (no ``model``)."""
    from .page_reader import reader_name

    if not model:
        return "no model", "OvisOCR2 isn't running in LM Studio, so there is nothing to test."
    name = reader_name(model)
    try:
        testing = test_under_way(store)
        test = saved_test(store, model)
    except Exception as exc:  # noqa: BLE001 - a locked database: said so, never raised
        return "pending", f"Not checked ({type(exc).__name__})."
    if testing and testing.get("model") == model:
        done, pages = int(testing.get("done") or 0), int(testing.get("pages") or 0)
        where = f" (page {done + 1} of {pages})" if pages else ""
        return "running", f"{name} is reading the test invoice now{where}; invoices are read once it passes."
    if test is None:
        return "pending", (f"{name} reads a test invoice whose answers are known before it reads any invoice: the "
                           "test starts on its own.")  # fmt: skip
    right, total = int(test.get("fields_right") or 0), int(test.get("fields_total") or 0)
    when = str(test.get("when") or "")[:16].replace("T", " ")
    score = f"{right} of {total} fields right" if total else "nothing read"
    if test.get("ok"):
        return "passed", f"{name} passed its self-test on {when or 'an earlier day'} ({score})."
    problem = str(test.get("problem") or "").strip()
    age = _test_age_hours(test, now)
    wait = RETEST_UNREAD_HOURS if not total else RETEST_HOURS
    again = "now" if age is None or age >= wait else f"in about {max(1, round(wait - age))} hour(s)"
    detail = f"{name} failed its self-test on {when or 'an earlier day'} ({score})"
    detail += f": {problem.rstrip('.')}." if problem else "."
    return "failed", f"{detail} It is tested again on its own {again}, or when a person runs it again."


def _dashboard_store(db_path: Path | None = None) -> Any:
    """The database the dashboard uses, opened only when it exists (None otherwise)."""
    from . import paths
    from .store import Store

    db = Path(db_path or os.environ.get("AP_DB_PATH") or paths.default_db_path())
    return Store(db) if db.exists() else None


def linked(model: str, db_path: Path | None = None) -> bool:
    """``confirmed`` in the database the dashboard uses, opened only when it exists: for the doctor and the launcher,
    which have no store at hand."""
    try:
        store = _dashboard_store(db_path)
        return store is not None and confirmed(store, model)
    except Exception:  # a locked or damaged database: say "not linked", the dashboard tells the rest
        return False


def self_test_detail(model: str, db_path: Path | None = None) -> tuple[str, str]:
    """``self_test`` in the database the dashboard uses (for the doctor and the launcher): "pending" when there is
    no database yet (the test runs on its own once AP Coder is open). Never raises."""
    try:
        store = _dashboard_store(db_path)
        if store is None:
            return ("pending", "it runs on its own once AP Coder is open") if model else self_test(None, "")
        return self_test(store, model)
    except Exception as exc:  # noqa: BLE001 - a locked or damaged database
        return "pending", f"not checked ({type(exc).__name__})"


def wants_reading(settings: Settings, path: Path, layout_source: str = "") -> bool:
    """Whether a newly processed invoice goes to the page reader: every invoice but a text file (.md, .txt), which
    has no page to look at. Digital PDFs too: the page as printed is checked against the text hidden in the PDF."""
    return Path(path).suffix.lower() not in TEXT_EXTENSIONS


def queue_new_invoice(store: Any, settings: Settings, invoice_id: int | None, path: Path, layout_source: str) -> bool:
    """Queue a just-processed invoice for the page reader (every invoice but a text file). Never raises."""
    if not invoice_id or store is None or not wants_reading(settings, path, layout_source):
        return False
    try:
        store.queue_page_read(invoice_id, "new invoice")
        return True
    except Exception as exc:  # the queue is an extra: processing must not fail over it
        log.warning("invoice %s: not queued for the page reader (%s)", invoice_id, exc)
        return False


def agreement(capture: Any) -> dict[str, Any]:
    """How the page reader's reading compares with what the invoice shows, field by field, as capture's fusion
    decided it: ``agree`` (the value shown is the page reader's too), ``differ`` (it read something else, or capture
    marked the field Check because it and another reader of the page read it differently), ``only`` (only it found
    the value).

    A field's ``sources`` name the readers whose readings make up the value shown (the page reader's as "vlm", as
    the text it was read from, "@ 13% 202.97" say, which is no value to compare) and the other values read
    ("other:<value>": the readers behind it). Capture's reasons say when the page reader and another reader of the
    page disagreed."""
    out: dict[str, Any] = {"agree": [], "differ": [], "only": []}
    if capture is None:
        return out
    for name, result in (capture.fields or {}).items():
        sources = result.sources or {}
        if result.value in (None, ""):
            continue
        others = [str(readers) for source, readers in sources.items() if source.startswith("other:")]
        read_otherwise = any("vlm" in {r.strip() for r in readers.split(",")} for readers in others)
        reasons = getattr(result, "reasons", None) or []
        disagreed = any(str(reason).startswith("the page reader read ") for reason in reasons)
        if "vlm" in sources and not disagreed:
            readers = {s for s in sources if not s.startswith("other:") and s != "computed"}
            out["only" if readers == {"vlm"} else "agree"].append(name)
        elif disagreed or read_otherwise:
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

    status = page_reader.reader_status(settings)
    if not status.usable:
        return "", status.note or "OvisOCR2 isn't running in LM Studio"
    if not confirmed(store, status.model):
        return "", self_test(store, status.model)[1].rstrip(".")
    if not store.has_reference():
        try:
            reference_data(store)
        except Exception as exc:  # noqa: BLE001 - no GL accounts anywhere: said plainly, the queue waits
            return "", f"no GL accounts to code with: import them on the GL Accounts page ({exc})"
    return status.model, ""


def reference_data(store: Any) -> Any:
    """The GL accounts, cost centers, tax set-up and policy the invoice is coded with again once its pages are read:
    from the same place ``python -m ap_coder process`` takes them, so an invoice processed from the command line is
    read the same way. The dashboard's database when GL accounts were imported there, else AP_REFERENCE_DIR, the
    data folder's reference folder, or the bundled sample data."""
    if store.has_reference():
        return store.reference_data()
    import argparse

    from .cli import _load_reference

    blank = argparse.Namespace(coa=None, cost_centers=None, tax_mapping=None, policy=None, db=str(store.path))
    return _load_reference(blank)


def blank_pages(settings: Settings, path: Path) -> tuple[set[int], int]:
    """(the pages, from 1, that OCR or the PDF's text finds (almost) no words on, the pages looked at). A blank page
    isn't shown to the page reader, which makes something up on one. None looked at when OCR isn't installed (a
    scan can't be told from a blank page then) or the file can't be read here. OCR takes seconds a page, the page
    reader minutes."""
    from .capture import layout as capture_layout

    if not capture_layout.ocr_available():
        return set(), 0
    try:
        doc = capture_layout.build_layout(path, max_pages=settings.page_reader.max_pages)
    except Exception as exc:  # noqa: BLE001 - the page reader reads every page then, as before
        log.info("%s: pages not checked for blanks (%s)", path.name, exc)
        return set(), 0
    if doc.source == "none":
        return set(), 0
    return {page.number for page in doc.pages if len(page.words) < BLANK_WORDS}, len(doc.pages)


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
    page under way is finished first.

    A reading cut off by the model server (LM Studio stopped, the model unloaded, no answer in time) is the server's
    trouble, not the invoice's: it waits in line again ("postponed"), and only after MAX_TRIES such reads does it
    leave the queue as failed. Interrupted (Ctrl+C, the app closing), the invoice goes back in line at once."""
    model, why = ready(settings, store)
    if not model:
        log.debug("page reader idle: %s", why)
        return None
    row = store.next_page_read(invoice_id, reader=READER_ID)
    if not row:
        return None
    iid = int(row["invoice_id"])
    try:
        return _read_taken(settings, store, row, model, cache_dir, should_stop, finish_page)
    except Exception as exc:  # noqa: BLE001 - a bug here must not leave the invoice marked as being read for hours
        log.exception("page reader: invoice %s could not be read", iid)
        message = f"{type(exc).__name__}: {exc}"
        store.finish_page_read(iid, "failed", model, 0, 0.0, message, only_if_reading=True)
        return ReadOutcome(iid, "failed", model, message=message)
    except BaseException:  # Ctrl+C, the app closing: back in line, to be read again
        store.finish_page_read(iid, "waiting", model, 0, 0.0, "interrupted before it was read: it is read again",
                               only_if_reading=True)  # fmt: skip
        raise


def _postpone_or_fail(store: Any, row: dict[str, Any], model: str, pages: int, seconds: float,
                      problem: str) -> ReadOutcome:  # fmt: skip
    """A reading the model server cut off: back in line, or failed once it has been cut off MAX_TRIES times."""
    from . import page_reader

    page_reader.forget_status()  # the next round asks the server again (it may well be stopped)
    iid = int(row["invoice_id"])
    tries = int(row.get("tries") or 0) + 1
    if tries >= MAX_TRIES:
        message = (f"the model server cut the reading off {tries} times ({problem}); read it again from the invoice "
                   "(Read with the page reader) once LM Studio is running with the model loaded")  # fmt: skip
        store.finish_page_read(iid, "failed", model, pages, seconds, message, tries=tries)
        return ReadOutcome(iid, "failed", model, pages, seconds, message=message)
    message = f"{problem}: read again later (try {tries} of {MAX_TRIES})"
    store.finish_page_read(iid, "waiting", model, pages, seconds, message, tries=tries)
    return ReadOutcome(iid, "postponed", model, pages, seconds, message=message)


def _read_taken(settings: Settings, store: Any, row: dict[str, Any], model: str, cache_dir: Path | None,
                should_stop: Callable[[], bool] | None, finish_page: bool) -> ReadOutcome:  # fmt: skip
    """``read_one`` on the invoice it took from the queue (marked as being read)."""
    from . import page_reader
    from .capture.transcript import made_up
    from .capture.workflow import AUTONOMOUS_REVIEWER
    from .figures import compare_figures, first_pages
    from .pipeline import InvoicePipeline
    from .pipeline import _meta as pipeline_meta

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
    blank, checked = blank_pages(settings, path)
    if checked and len(blank) >= checked:
        message = "not read: OCR found no words on its pages, so there is nothing for the page reader to read"
        store.finish_page_read(iid, "skipped", model, 0, 0.0, message)
        return ReadOutcome(iid, "skipped", model, message=message)
    t0 = time.perf_counter()
    stop, on_page = should_stop, None
    if should_stop is not None and finish_page:
        stop = _BetweenPages(should_stop)
        on_page = stop.on_page
    extra = {"blank_pages": blank} if blank else {}
    try:
        reading = page_reader.read_document(settings, path, model=model, on_page=on_page, should_stop=stop, **extra)
    except page_reader.PageReaderError as exc:  # the server failed, the page was blank, the model looped twice
        message = f"{type(exc).__name__}: {exc}"
        if exc.temporary:
            return _postpone_or_fail(store, row, model, 0, time.perf_counter() - t0, str(exc))
        store.finish_page_read(iid, "failed", model, 0, time.perf_counter() - t0, message)
        return ReadOutcome(iid, "failed", model, message=message)
    seconds = sum(reading.seconds) if reading.seconds else time.perf_counter() - t0
    if reading.stopped:  # asked to stop (time is up, the app is closing): it keeps its place in line
        message = f"stopped after {len(reading.pages)} of {reading.page_count or '?'} page(s): read again later"
        store.finish_page_read(iid, "waiting", model, len(reading.pages), seconds, message)
        return ReadOutcome(iid, "postponed", model, len(reading.pages), seconds, message=message)
    # A page OCR found blank, or one the model wrote only a page number or a pangram for, is read as blank: nothing
    # on it is taken from the page reader alone.
    reading.pages = ["" if number in blank or made_up(text) else text
                     for number, text in enumerate(reading.pages, start=1)]  # fmt: skip
    if reading.error and reading.temporary:  # LM Studio stopped, the model unloaded: read again later
        return _postpone_or_fail(store, row, model, len(reading.pages), seconds, reading.error)
    if not reading.complete or not any(p.strip() for p in reading.pages):  # only a reading of every page is used
        message = reading.error or (
            "the page reader found nothing to read"
            if len(reading.pages) >= reading.page_count
            else f"the page reader read {len(reading.pages)} of {reading.page_count} page(s)"
        )
        store.finish_page_read(iid, "failed", model, len(reading.pages), seconds, message)
        return ReadOutcome(iid, "failed", model, len(reading.pages), seconds, message=message)

    pipeline = InvoicePipeline(settings, reference_data(store), cache_dir=cache_dir, store=store)
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
    release_interrupted(store)
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


# --- Who is reading: an invoice whose reader is gone goes back in line -------------------------------------------


def _pid_alive(pid: int) -> bool:
    """Whether a process with this id runs on this computer (Windows: asked without touching it; ``os.kill`` with
    signal 0 would end it there)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() == 5  # access denied: it runs, as another user
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def reader_alive(reader: str) -> bool:
    """Whether the process that marked an invoice as being read (``READER_ID``) is still reading it. This process
    calls it between readings only, so a mark of its own is left over (a reading cut short); another process's mark
    stands while that process runs (``read-pages`` beside the dashboard)."""
    pid, _, _token = (reader or "").partition(":")
    try:
        number = int(pid)
    except ValueError:
        return False  # marked by an older AP Coder, or not at all
    return number != os.getpid() and _pid_alive(number)


def release_interrupted(store: Any) -> int:
    """Put back in line the invoices marked as being read by a process that was closed or killed part way (the
    dashboard stopped, Ctrl+C on read-pages, the computer restarted): they are read again at once, not after
    hours. Called between readings only. Never raises."""
    try:
        released = store.release_page_reads(reader_alive)
    except Exception as exc:  # noqa: BLE001 - a locked database: the stale-read rule still frees them later
        log.info("page reader: interrupted reads not checked (%s)", exc)
        return 0
    if released:
        log.info("page reader: %d interrupted read(s) put back in line", released)
    return released


class BackgroundReader(threading.Thread):
    """Reads waiting invoices one at a time while the dashboard runs. Settings are read again each round, so a
    change in LM Studio (OvisOCR2 downloaded, loaded) is found without a restart. Before the first invoice it runs
    OvisOCR2's self-test on its own when that model has none that passed (``self_test_if_due``). While the page
    reader is tested it waits: two readings at once would each take twice as long."""

    def __init__(self, settings_factory: Callable[[], Settings], store_factory: Callable[[], Any],
                 cache_dir: Path | None = None) -> None:  # fmt: skip
        super().__init__(name="ap-coder-page-reader", daemon=True)
        self._settings_factory = settings_factory
        self._store_factory = store_factory
        self._cache_dir = cache_dir
        self._halt = threading.Event()
        self.current: int | None = None  # the invoice being read now
        self.last: ReadOutcome | None = None
        self.last_test: dict[str, Any] | None = None  # the self-test it ran on its own, when it ran one

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        while not self._halt.is_set():
            pause = IDLE_SECONDS
            try:
                settings = self._settings_factory()
                store = self._store_factory()
                release_interrupted(store)  # between readings: a mark of this process's is left over too
                if test_under_way(store):
                    pause = IDLE_SECONDS
                else:
                    tested = self_test_if_due(settings, store)
                    if tested is not None:  # the test took the round; reading starts next round when it passed
                        self.last_test = tested
                        pause = 0.0 if tested.get("ok") else OFF_SECONDS
                    else:
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


# --- Testing the page reader, in the background -----------------------------------------------------------------
# The test reads the two pages of a sample invoice: 4 to 20 minutes on a laptop CPU. It runs in a thread of its own,
# so closing the browser or leaving the page loses nothing: its result is kept as its model's when it finishes. One
# test at a time, on this computer: the one under way is noted in the database (TESTING_KEY) with its process, so
# a second one (another browser tab, read-pages --test) waits, and the page shows its progress.

_test_lock = threading.Lock()
_testing_here = threading.Event()  # this process is testing the page reader now


def test_under_way(store: Any) -> dict[str, Any] | None:
    """The test of the page reader under way on this computer: {model, started, pid, page, pages, done}, or None.
    A test whose process is gone (the dashboard was closed during it) is not under way."""
    try:
        raw = store.get_setting(TESTING_KEY)
        testing = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        return None
    if not isinstance(testing, dict):
        return None
    if testing.get("reader") == READER_ID:  # this process's: under way until its test ends
        return testing if _testing_here.is_set() else None
    return testing if reader_alive(str(testing.get("reader") or "")) else None


test_under_way.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def _test_record(result: Any, model: str) -> dict[str, Any]:
    record = dataclasses.asdict(result)
    record["model"] = record.get("model") or model
    record["when"] = record.get("when") or dt.datetime.now().isoformat(timespec="minutes")
    return record


def run_test(settings: Settings, store: Any, model: str,
             on_page: Callable[[int, int], None] | None = None) -> dict[str, Any] | None:  # fmt: skip
    """Test ``model`` on the test invoice here and now (``read-pages --test``), noted as under way meanwhile, and
    keep the result as its model's. None when another test is under way."""
    with _test_lock:
        if test_under_way(store):
            return None
        _testing_here.set()
        started = _mark_test(store, model, 0, 0)
    return _test(settings, store, model, started, on_page)


run_test.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def start_test(settings: Settings, store: Any, model: str) -> bool:
    """Test ``model`` on the test invoice in a thread of its own; the result is kept as its model's when it
    finishes, whatever the browser does meanwhile. False when a test is already under way (only one at a time)."""
    with _test_lock:
        if test_under_way(store):
            return False
        _testing_here.set()
        started = _mark_test(store, model, 0, 0)
        threading.Thread(target=_test, args=(settings, store, model, started, None), daemon=True,
                         name="ap-coder-page-reader-test").start()  # fmt: skip
    return True


start_test.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def self_test_if_due(settings: Settings, store: Any,
                     on_page: Callable[[int, int], None] | None = None) -> dict[str, Any] | None:  # fmt: skip
    """Run OvisOCR2's self-test here and now when it is due (``self_test_due``: LM Studio has the model and it has no
    test that passed, never tested or failed a day ago), and keep the result as its model's. None when no test was
    run: no model, it passed already, a failure is too recent to try again, or another test is under way."""
    from . import page_reader

    status = page_reader.reader_status(settings)
    if not status.usable or not self_test_due(store, status.model):
        return None
    log.info("page reader: %s has no self-test that passed; testing it before reading invoices", status.model)
    return run_test(settings, store, status.model, on_page)


def page_seconds(settings: Settings, model: str = "") -> float | None:
    """How long OvisOCR2 takes a page on this computer (invoices and self-tests read so far), or None before the
    first page."""
    from . import page_reader

    return page_reader.page_seconds_estimate(settings, model=model or None)


def wait_seconds(settings: Settings, store: Any, model: str = "", invoice_id: int | None = None) -> float | None:
    """About how long until the page reader has read the whole queue (or, with ``invoice_id``, that invoice): the
    invoices to read (those ahead of it, and itself) times the pages an invoice has had so far, times the time a page
    takes on this computer, plus the self-test's pages while it hasn't passed. None before a page time is measured,
    or when ``invoice_id`` isn't waiting."""
    seconds = page_seconds(settings, model)
    if seconds is None:
        return None
    try:
        if invoice_id is not None:
            read = store.page_read(invoice_id) or {}
            if read.get("status") not in ("waiting", "reading"):
                return None
            invoices = store.page_reads_ahead(invoice_id) + 1
        else:
            invoices = store.page_reads_waiting()
        pages = store.page_read_pages_average() or 1.0
        test_pages = 0 if not model or confirmed(store, model) else TEST_PAGES
    except Exception as exc:  # noqa: BLE001 - an estimate is a courtesy
        log.debug("page reader: no estimate (%s)", exc)
        return None
    return round((invoices * pages + test_pages) * seconds, 1)


def _mark_test(store: Any, model: str, done: int, pages: int, started: str = "") -> str:
    started = started or dt.datetime.now().isoformat(timespec="seconds")
    store.set_setting(TESTING_KEY, json.dumps({"model": model, "started": started, "pid": os.getpid(),
                                               "reader": READER_ID, "done": done, "pages": pages}))  # fmt: skip
    return started


def _test(settings: Settings, store: Any, model: str, started: str,
          on_page: Callable[[int, int], None] | None) -> dict[str, Any]:  # fmt: skip
    """The test itself: read, compare, keep the result (as its model's) and clear the mark. Never raises for a
    reading problem; the mark is cleared whatever happens."""
    from . import page_reader

    def progress(number: int, total: int) -> None:
        try:
            _mark_test(store, model, number - 1, total, started)
        except Exception as exc:  # noqa: BLE001 - the progress shown is a courtesy
            log.debug("page reader test: progress not noted (%s)", exc)
        if on_page:
            on_page(number, total)

    try:
        try:
            result = page_reader.test_reader(settings, model=model, on_page=progress)
            record = _test_record(result, model)
        except Exception as exc:  # noqa: BLE001 - a bug must not lose the test silently
            log.exception("page reader test failed")
            record = {"model": model, "ok": False, "seconds": 0.0, "rows": [], "fields_right": 0, "fields_total": 0,
                      "when": dt.datetime.now().isoformat(timespec="minutes"),
                      "problem": f"The test stopped: {type(exc).__name__}: {exc}"}  # fmt: skip
        save_test(store, record)
        store.log_event("page_reader_tested", detail={k: record.get(k) for k in
                        ("model", "ok", "fields_right", "fields_total", "seconds")})  # fmt: skip
        return record
    finally:
        _testing_here.clear()
        try:
            store.set_setting(TESTING_KEY, "")
        except Exception as exc:  # noqa: BLE001 - a mark left behind is ignored once this process is gone
            log.warning("page reader test: couldn't clear the mark (%s)", exc)
