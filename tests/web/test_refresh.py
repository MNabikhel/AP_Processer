"""The settled answers the company server's background process works out again (D-198, ``web/refresh.py``): a request
whose answer no longer holds takes the one kept and leaves a want; the refresher works it out and every process takes
it. Two processes, the laptop as before, a restore, an older database or backup, the background process down or
behind, a want that fails, the hold on an answer (B-1807, B-1914), the refresher's start, every answer's round trip,
and the pages the same byte for byte. The answers themselves: ``tests/core/test_kept_answers.py``."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from ap_coder.core.store import Store, reports, summary
from ap_coder.web import refresh
from tests.conftest import ROOT
from tests.core.test_kept_answers import Shared, _approved, _invoice, _plain, _Processes, _vendor_rows

ADD = "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES (?, 'A vendor', '2026-01-05T09:00:00')"


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    """The answers' clock (``reports.wall``), moved by the test."""
    now = [1000.0]
    monkeypatch.setattr(reports, "wall", lambda: now[0])
    monkeypatch.setattr(reports, "_ASKED", {})
    return now


def _server(store: Store, monkeypatch) -> Store:
    """``store`` as a web worker of the company server has it (``app._set_state``)."""
    monkeypatch.setattr(store, "background_answers", True)
    return store


def _rows_answer(store: Store, **stamp) -> Shared:
    """A settled answer of the vendors' rows (30 s), counting its work, that the refresher works out again as
    ``rows``."""
    return Shared(store, "rows", lambda: _vendor_rows(store), version="t-1", topic="", changes=("vendors",),
                  settle=30, again=[], **stamp)  # fmt: skip


def _db(store: Store) -> str:
    """The database's ``summary_id``, as a want names it."""
    return str(store._answer_stamp("", ())[0][0] or "")


def _wants(store: Store) -> list[dict]:
    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM answer_wants ORDER BY name, asked")]


def test_a_settled_answer_no_longer_holding_is_shown_as_kept_and_worked_out_again_by_the_background_process(
    tmp_path, clock, monkeypatch
):
    """D-198: on the company server a web worker whose settled answer no longer holds (changed, past its settle) takes
    the one kept at once, from its own memory or the database's, and leaves one want however many ask; the background
    process works it out (``refresh``) and every worker takes it. Nothing is worked out in a web worker after the
    first."""
    process = _Processes(monkeypatch)
    worker, background = _server(Store(tmp_path / "ap.db"), monkeypatch), Store(tmp_path / "ap.db")
    asked, again = _rows_answer(worker), _rows_answer(background)
    monkeypatch.setitem(refresh.ANSWERS, "rows", lambda store, args: again())
    process("worker 1")
    assert asked() == [] and asked.worked == 1  # (none kept: worked out in the request, as before)
    _plain(worker, ADD, "acme")
    clock[0] += 31
    assert background.take_lease(reports.REFRESHER, "the background process", 600)
    at = time.monotonic()
    for name in ("worker 1", "worker 2", "worker 1"):  # (its own kept, then the database's: one want)
        process(name)
        assert asked() == [] and asked.worked == 1, name
    assert time.monotonic() - at < 1
    (want,) = [w for w in _wants(worker) if w["wanted_at"] is not None]
    assert (want["name"], want["args"], want["wanted_at"]) == ("rows", "[]", clock[0])
    process("background")
    assert refresh.refresh(background, background.next_want()) and again.worked == 1
    assert background.next_want() is None and _wants(worker)[0]["wanted_at"] is None  # (its row kept: asked lately)
    for name in ("worker 1", "worker 2"):
        process(name)
        assert asked() == _vendor_rows(worker) != [] and asked.worked == 1, name


def test_on_a_laptop_a_settled_answer_is_worked_out_where_it_is_asked_as_before(tmp_path, clock, monkeypatch, store):
    """A laptop has no background process (``background_answers`` off, the dashboard's ``create_app`` on a laptop):
    an answer no longer holding is worked out in the request, whatever holds the refresher's lease, and nothing is
    noted for a refresher."""
    from tests.webclient import client_for

    client_for(store)
    assert store.background_answers is False
    asked = _rows_answer(store)
    assert store.take_lease(reports.REFRESHER, "another", 600)
    assert asked() == [] and asked.worked == 1
    _plain(store, ADD, "acme")
    clock[0] += 31
    assert asked() == _vendor_rows(store) and asked.worked == 2
    assert _wants(store) == []


def test_two_processes_one_works_the_answers_out_again_and_the_other_waits_for_one_it_has_none_of(tmp_path):
    """Two processes on one database, the refresher in one (a process of its own, ``Refresher``): the web worker's
    answer that no longer holds is taken as kept and worked out there; one it has nothing to show of while the
    refresher works it out is waited for while its hold is renewed (B-1807, B-1914), not worked out twice."""
    store = Store(tmp_path / "ap.db")
    store.background_answers = True
    asked = Shared(store, "rows", lambda: _vendor_rows(store), version="t-1", topic="", changes=("vendors",),
                   settle=0.5, again=[])  # fmt: skip
    assert asked() == [] and asked.worked == 1
    background = subprocess.Popen([sys.executable, "-c", _BACKGROUND, str(store.path)], cwd=ROOT,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)  # fmt: skip
    try:
        ends = time.monotonic() + 30
        while not store._lease_held(reports.REFRESHER) and time.monotonic() < ends:
            time.sleep(0.05)
        assert store._lease_held(reports.REFRESHER)
        time.sleep(2 * refresh.POLL + 0.2)  # (its start: the answer asked lately, held still, taken)
        while store.next_want() is not None and time.monotonic() < ends:
            time.sleep(0.05)
        _plain(store, ADD, "acme")
        time.sleep(0.6)  # (past its settle)
        assert asked() == [] and asked.worked == 1  # (the one kept, at once)
        while asked() == [] and time.monotonic() < ends:
            time.sleep(0.05)
        assert asked() == _vendor_rows(store) != [] and asked.worked == 1  # (the background process's)

        # With nothing to show (another question), the background's work under way is waited for.
        slow = Shared(store, "slow", lambda: ["here"], version="t-1", topic="", changes=("vendors",), settle=0.5,
                      again=[])  # fmt: skip
        store.want_answer("slow", reports.asked_key(None), [], _db(store))
        lease = f"answer/slow/{reports.asked_key(None)}"
        while not store._lease_held(lease) and time.monotonic() < ends:
            time.sleep(0.05)
        assert store._lease_held(lease)  # (it works it out: 3 s, its hold renewed)
        assert slow() == ["there"] and slow.worked == 0
    finally:
        background.stdin.close()
        background.wait(30)
    assert not store._lease_held(reports.REFRESHER)  # (let go of as it stopped)


# The background process of the test above: the refresher, its two answers as the web worker asks them.
_BACKGROUND = """
import sys, time
from ap_coder.core.store import Store
from ap_coder.web import refresh

path = sys.argv[1]

def rows(store, args):
    def work():
        with store._conn() as conn:
            return [list(r) for r in conn.execute("SELECT * FROM vendors ORDER BY rowid")]
    return store.shared_answer("rows", work, version="t-1", topic="", changes=("vendors",), settle=0.5, again=[])

def slow(store, args):
    return store.shared_answer("slow", lambda: time.sleep(3) or ["there"], version="t-1", topic="",
                               changes=("vendors",), settle=0.5, again=[])

refresh.ANSWERS.update(rows=rows, slow=slow)
refresher = refresh.Refresher(lambda: Store(path)).start()
sys.stdin.read()
refresher.stop()
refresher.thread.join(30)
"""


def test_with_the_background_process_down_or_behind_past_the_cap_the_request_works_it_out(tmp_path, clock, monkeypatch):
    """The kept answer is shown only while the refresher runs (its hold renewed: a background process stopped lets it
    go, or it runs out) and only up to ``STALE_MOST`` old: a background process down, or so far behind, has the
    request work it out, as before."""
    store = _server(Store(tmp_path / "ap.db"), monkeypatch)
    asked = _rows_answer(store)
    assert asked() == [] and asked.worked == 1
    _plain(store, ADD, "a")
    clock[0] += 31
    assert asked() == _vendor_rows(store) and asked.worked == 2  # (no refresher: down)
    assert store.take_lease(reports.REFRESHER, "the background process", 1)
    _plain(store, ADD, "b")
    clock[0] += 31
    shown = asked()
    assert shown != _vendor_rows(store) and asked.worked == 2  # (taken as kept: the refresher runs)
    clock[0] += reports.STALE_MOST - 31 - 1
    assert asked() == shown and asked.worked == 2
    clock[0] += 1  # (its work began STALE_MOST ago: the refresher is too far behind)
    assert asked() == _vendor_rows(store) and asked.worked == 3
    _plain(store, ADD, "c")
    clock[0] += 31
    time.sleep(1.1)  # (the refresher's hold ran out: it stopped)
    assert asked() == _vendor_rows(store) and asked.worked == 4


def test_after_a_restore_nothing_kept_before_is_shown_and_the_old_databases_wants_are_dropped(
    tmp_path, clock, monkeypatch
):
    """A restore gives the database a new ``summary_id``: an answer kept before it is never shown while the refresher
    works one out (B-1838's rule), so it is worked out in the request, and the wants of the database before (in the
    backup too) are never worked out and are forgotten at the refresher's next start."""
    store = _server(Store(tmp_path / "ap.db"), monkeypatch)
    asked = _rows_answer(store)
    asked()
    _plain(store, ADD, "a")
    clock[0] += 31
    assert store.take_lease(reports.REFRESHER, "the background process", 600)
    assert asked() == [] and store.next_want() is not None
    store.restore_from(store.backup_now("before"))
    assert store.next_want() is None  # (the want, restored with the backup, is of the database before)
    assert store.take_lease(reports.REFRESHER, "the background process", 600)
    assert asked() == _vendor_rows(store) and asked.worked == 2  # (worked out: nothing of before shown)
    assert store.want_again(0) == 0 and _wants(store) == []


def test_an_older_database_and_a_backup_from_before_d198_get_the_wants_table_before_a_page_reads_it(
    tmp_path, clock, monkeypatch
):
    """An older AP Coder's database gets ``answer_wants`` as it opens; a restore of an older backup gets it in its
    copy, before that goes in: a page asked while the data folder's files are copied (minutes on a big one), the
    refresher running, reads and keeps its want (as B-2045 for ``report_answers.lasted``)."""
    from ap_coder.core.store import backup_files
    from ap_coder.erp import recurring

    path = tmp_path / "ap_coder.db"
    with sqlite3.connect(Store(path).path) as conn:
        conn.execute("DROP TABLE answer_wants")
    store = _server(Store(path), monkeypatch)
    assert _wants(store) == []
    recurring.detected(store)
    assert store.take_lease(reports.REFRESHER, "the background process", 600)
    made = store.backup_now("before-d198")
    with sqlite3.connect(made) as conn:  # (as a version before D-198 made it)
        conn.execute("DROP TABLE answer_wants")
    seen = []
    real = backup_files.restore_files

    def files_meanwhile(*a, **k):
        try:
            seen.append(len(recurring.detected(store)))
        except sqlite3.Error as exc:
            seen.append(str(exc))
        return real(*a, **k)

    monkeypatch.setattr(backup_files, "restore_files", files_meanwhile)
    store.restore_from(made)
    assert seen == [0]


def test_a_want_that_fails_is_logged_tried_after_the_others_and_dropped_after_a_few_tries(
    tmp_path, clock, monkeypatch, caplog
):
    """A want whose work fails (its arguments can't be read back, its answer is no longer one AP Coder has, or its
    work raises) is logged, waits again after the others, and is dropped, row and all, after ``WANT_TRIES``; the
    others are worked out meanwhile."""
    store = Store(tmp_path / "ap.db")
    db = _db(store)
    store.want_answer("spend_figures", "unreadable", ["not a date"], db)
    clock[0] += 1
    store.want_answer("no such answer", "gone", [], db)
    clock[0] += 1
    store.want_answer("recurring", reports.asked_key(dt.date.today()), [dt.date.today().isoformat(), 60.0], db)
    done = []
    with caplog.at_level(logging.WARNING, logger="ap_coder.web.refresh"):
        while (want := store.next_want()) is not None:
            clock[0] += 10
            done.append((want["name"], refresh.refresh(store, want)))
    assert done == [("spend_figures", False), ("no such answer", False), ("recurring", True)] + [
        ("spend_figures", False), ("no such answer", False)] * (reports.WANT_TRIES - 1)  # fmt: skip
    assert [w["name"] for w in _wants(store)] == ["recurring"]
    assert sum("was not worked out again" in r.getMessage() for r in caplog.records) == 2 * reports.WANT_TRIES


def test_the_refresher_works_out_one_answer_at_a_time_and_at_its_start_those_asked_in_the_last_day(
    tmp_path, clock, monkeypatch
):
    """The refresher (a thread of the background process) works the wants out one at a time, oldest first, so the
    background process holds one answer's work at once (300,000 invoices: D-198); at its start, those asked in the last
    day wait for it (after a restart or an update, worked out before anyone asks), not older ones; it lets go of its
    hold as it stops."""
    store = Store(tmp_path / "ap.db")
    db = _db(store)
    started, finish, order = threading.Event(), threading.Event(), []

    def work(name):
        def run(store, args):
            order.append(name)
            if name == "latest":
                started.set()
                assert finish.wait(10)

        return run

    for name in ("old", "earlier", "latest"):
        monkeypatch.setitem(refresh.ANSWERS, name, work(name))
    store.want_answer("old", "k", [], db, wanted=False)
    clock[0] += refresh.ASKED_WITHIN + 1
    store.want_answer("earlier", "k", [], db, wanted=False)
    clock[0] += 1
    store.want_answer("latest", "k", [], db, wanted=False)
    refresher = refresh.Refresher(lambda: store).start()
    try:
        assert started.wait(10)  # (the latest asked first)
        time.sleep(0.3)
        assert order == ["latest"] and store._lease_held(reports.REFRESHER)  # (the other waits)
        finish.set()
        ends = time.monotonic() + 10
        while store.next_want() is not None and time.monotonic() < ends:
            time.sleep(0.05)
        assert order == ["latest", "earlier"]  # (the one asked over a day ago: not)
    finally:
        finish.set()
        refresher.stop()
        refresher.thread.join(10)
    assert not refresher.thread.is_alive() and not store._lease_held(reports.REFRESHER)


def test_a_database_too_busy_to_drop_a_want_stops_nothing_the_want_is_taken_again(tmp_path, clock, monkeypatch):
    """The database busy just as a want worked out is dropped (another writer, longer than the wait): the refresher
    goes on, and takes the want again at its next look (its answer held then: nothing worked out twice)."""
    store = Store(tmp_path / "ap.db")
    worked, real = [], store.want_done
    monkeypatch.setitem(refresh.ANSWERS, "rows", lambda store, args: worked.append(1))
    busy = [True]

    def want_done(*args, **kwargs):
        if busy[0]:
            busy[0] = False
            raise sqlite3.OperationalError("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(store, "want_done", want_done)
    store.want_answer("rows", "k", [], _db(store))
    refresher = refresh.Refresher(lambda: store).start()
    try:
        ends = time.monotonic() + 10
        while store.next_want() is not None and time.monotonic() < ends:
            time.sleep(0.05)
        assert store.next_want() is None and worked == [1, 1] and refresher.thread.is_alive()
    finally:
        refresher.stop()
        refresher.thread.join(10)


def _ask_every_answer(store: Store, today: dt.date) -> None:
    """Each answer ``refresh.ANSWERS`` works out again, asked as its page asks it."""
    from ap_coder.erp import accruals, insights, recurring, spend
    from ap_coder.web.pages import learning, sales_tax

    start, end = today.replace(day=1), today
    spend.spend_currencies(store, start, end)
    spend.figures(store, start, end, "CAD", {"5000": "Supplies"}, None)
    spend.figures(store, start, end, "CAD", {}, {"USD": 1.35})
    insights.shown(store)
    insights.shown_operations(store, today)
    insights.vendor_workload(store)
    sales_tax.shown(store, start, end, "", 50, 50)
    for work in learning.SETTLED.values():
        work(store)
    recurring.detected(store, today, 60.0)
    accruals.received_not_invoiced(store, end, accruals.SETTLE_SECONDS)
    accruals.not_in_erp(store, end, accruals.SETTLE_SECONDS)
    store.vendor_summaries(settled=True)


def test_every_answer_asked_by_its_page_is_worked_out_again_by_the_refresher_as_the_same_question(
    tmp_path, ground_truth, clock, monkeypatch
):
    """Each settled answer (``refresh.ANSWERS``: Spend's, Insights', the Vendors page's, Sales tax, Learning's, the
    recurring vendors, month-end's) asked by its page is noted with what it is worked out from; the refresher, in
    another process with nothing kept, works each out again from that alone under the same name and question (the
    answer kept for every process where the page will look), with its caller's own format, stamp and settle."""
    process = _Processes(monkeypatch)
    store = _server(Store(tmp_path / "ap.db"), monkeypatch)
    _approved(store, tmp_path, ground_truth, "A-1")
    _invoice(store, tmp_path, ground_truth, "A-2")
    today = dt.date.today()
    process("worker")
    _ask_every_answer(store, today)
    wants = _wants(store)
    assert {w["name"] for w in wants} == set(refresh.ANSWERS)
    with sqlite3.connect(store.path) as conn:
        kept = conn.execute("SELECT name, asked, version, stamp, value FROM report_answers ORDER BY 1, 2").fetchall()
        conn.execute("DELETE FROM report_answers")
    process("background")
    assert store.want_again(0) == len(wants)
    while (want := store.next_want()) is not None:
        assert refresh.refresh(store, want), want["name"]
    with sqlite3.connect(store.path) as conn:
        again = conn.execute("SELECT name, asked, version, stamp, value FROM report_answers ORDER BY 1, 2").fetchall()
    assert again == kept  # (the same questions, worked out the same)
    assert {(w["name"], w["asked"]) for w in wants} <= {(name, asked) for name, asked, *_ in again}


def test_the_reports_pages_are_the_same_byte_for_byte_and_none_works_its_answer_out_while_it_has_one_to_show(
    tmp_path, ground_truth, clock, monkeypatch
):
    """A page that shows settled answers (Spend, Vendors, Month-end, Insights, Sales tax, Learning) is the same byte
    for byte on the company server as before when nothing changed, in a web worker that takes the answers from the
    database; after a change, past its settle, while the refresher runs, the page's request shows the answers kept
    and works none out; once the refresher worked them out, the page shows them."""
    from tests.webclient import client_for

    store = Store(tmp_path / "ap.db")
    for n in range(3):
        _approved(store, tmp_path, ground_truth, f"A-{n}")
    client = client_for(store)
    pages = ["/spend", "/vendors", "/month-end", "/insights", "/sales-tax", "/learning"]

    def shown(page: str) -> str:  # (a chart's id is numbered in its process: web/charts.py)
        return re.sub(r"apc-chart-[\w-]+", "apc-chart", client.get(page).text)

    laptop = {page: shown(page) for page in pages}
    assert all(shown(page) == laptop[page] for page in pages)  # (a page is the same asked again)
    monkeypatch.setattr(store, "background_answers", True)
    assert store.take_lease(reports.REFRESHER, "the background process", 600)
    process = _Processes(monkeypatch)
    process("another web worker")
    assert {page: shown(page) for page in pages} == laptop

    _approved(store, tmp_path, ground_truth, "B-1", vendor_name="Brand New Vendor Ltd")
    clock[0] += reports.SETTLE_MOST  # (past every settle)
    worked = []
    real = Store.shared_answer

    def counted(self, name, compute, **kwargs):
        def work():
            worked.append(name)
            return compute()

        return real(self, name, work, **kwargs)

    monkeypatch.setattr(Store, "shared_answer", counted)
    vendors = client.get("/vendors").text
    assert "Brand New Vendor Ltd" not in vendors  # (the totals kept)
    for page in pages:
        client.get(page)
    settled = set(refresh.ANSWERS)
    assert not set(worked) & settled, worked
    process("background")
    while (want := store.next_want()) is not None:
        assert refresh.refresh(store, want), want["name"]
    process("another web worker")
    worked.clear()
    assert "Brand New Vendor Ltd" in client.get("/vendors").text and not set(worked) & settled


def test_wants_for_the_same_answer_and_question_are_one_and_keep_their_place_in_line(tmp_path, clock):
    """However many ask an answer while it waits, it is one want, its place in line the first's; the latest
    ``ANSWERS_KEPT`` asked of a name are kept."""
    store = Store(tmp_path / "ap.db")
    db = _db(store)
    store.want_answer("rows", "a", [], db)
    first = clock[0]
    clock[0] += 5
    store.want_answer("rows", "b", [], db)
    clock[0] += 5
    store.want_answer("rows", "a", [], db)
    found = [(w["asked"], w["wanted_at"], w["asked_at"]) for w in _wants(store)]
    assert found == [("a", first, clock[0]), ("b", first + 5, first + 5)]
    assert store.next_want()["asked"] == "a"
    for n in range(reports.ANSWERS_KEPT):
        clock[0] += 1
        store.want_answer("rows", f"later {n}", [], db, wanted=False)
    assert {w["asked"] for w in _wants(store)} == {f"later {n}" for n in range(reports.ANSWERS_KEPT)}


def test_an_answer_asked_is_noted_at_most_every_hour_a_process_for_the_refreshers_start(tmp_path, clock, monkeypatch):
    """What the refresher's start works out again is what was asked lately: an answer held is noted asked at most every
    ``ASKED_NOTE`` in a process (not a write at every page), never by the refresher's own asks."""
    store = _server(Store(tmp_path / "ap.db"), monkeypatch)
    asked = _rows_answer(store)
    writes = []
    real = store.want_answer
    monkeypatch.setattr(store, "want_answer", lambda *a, **k: writes.append(a[0]) or real(*a, **k))
    asked()
    clock[0] += reports.ASKED_NOTE - 1
    asked()
    assert writes == ["rows"] and _wants(store)[0]["asked_at"] == 1000.0
    clock[0] += 1
    asked()
    assert writes == ["rows", "rows"] and _wants(store)[0]["asked_at"] == clock[0]
    clock[0] += reports.ASKED_NOTE
    with reports.refreshing():
        asked()
    assert writes == ["rows", "rows"]
    assert json.loads(_wants(store)[0]["args"]) == []


def test_an_answer_that_could_not_be_kept_for_every_process_is_worked_out_where_it_is_asked(
    tmp_path, clock, monkeypatch
):
    """B-2046: an answer too big to keep for every process (``ANSWER_MOST_BYTES``; or holding NaN) was shown from the
    worker's own memory for up to ``STALE_MOST`` while the refresher worked it out again and again in vain (it could
    not keep it either). A worker shows its own only when it is the one the database keeps; else it works it out, as
    before D-198."""
    monkeypatch.setattr(reports, "ANSWER_MOST_BYTES", 10)
    process = _Processes(monkeypatch)
    worker, background = _server(Store(tmp_path / "ap.db"), monkeypatch), Store(tmp_path / "ap.db")
    asked = _rows_answer(worker)
    _plain(worker, ADD, "acme")
    process("worker 1")
    assert len(asked()) == 1 and asked.worked == 1  # (more than 10 bytes: not kept for every process)
    assert background.take_lease(reports.REFRESHER, "the background process", 600)
    _plain(worker, ADD, "zeta")
    clock[0] += 31
    assert asked() == _vendor_rows(worker) and len(_vendor_rows(worker)) == 2 and asked.worked == 2  # (not its own)
    assert not [w for w in _wants(worker) if w["wanted_at"] is not None]


def test_an_answers_arguments_that_are_not_json_never_fail_the_page(tmp_path, clock, monkeypatch, caplog):
    """B-2047: on the company server a want's arguments were written with ``json.dumps`` where only a database error
    was caught: an answer asked with arguments JSON can't hold (a date) failed its page with a 500, and only on the
    server (a laptop writes no want). Such an answer is worked out where it is asked, as before, and said in the
    log."""
    process = _Processes(monkeypatch)
    worker, background = _server(Store(tmp_path / "ap.db"), monkeypatch), Store(tmp_path / "ap.db")
    asked = Shared(worker, "dated", lambda: _vendor_rows(worker), version="t-1", topic="", changes=("vendors",),
                   settle=30, again=[dt.date(2026, 11, 7)])  # fmt: skip
    process("worker 1")
    assert asked() == []
    assert background.take_lease(reports.REFRESHER, "the background process", 600)
    _plain(worker, ADD, "acme")
    clock[0] += 31
    with caplog.at_level(logging.WARNING):
        assert asked() == _vendor_rows(worker) != [] and asked.worked == 2
    assert "dated" in caplog.text and not _wants(worker)


def test_on_a_laptop_the_vendors_page_writes_nothing_to_show_its_totals(
    tmp_path, clock, store, ground_truth, monkeypatch
):
    """B-2048: D-198 put the Vendors page's settled totals on ``shared_answer`` on a laptop too, so after each change
    the page (which only reads) wrote the totals to the database and took and dropped a hold: three writes, a third
    slower. On a laptop they are kept in the process as before (``summary_memo``); the company server shares them.
    The process's kept answers settle by their own clock (ten times their work, B-2044): moved with the test's, so a
    slow first count (a busy machine) can't keep the page within its settle."""
    monkeypatch.setattr(summary, "monotonic", lambda: clock[0])
    _invoice(store, tmp_path, ground_truth, "A-1", "Acme Supply")
    store.vendor_summaries(settled=True)
    _invoice(store, tmp_path, ground_truth, "A-2", "Acme Supply")
    clock[0] += 31
    assert [v["invoices"] for v in store.vendor_summaries(settled=True)] == [2]
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM report_answers WHERE name = 'vendor_totals'").fetchone()[0] == 0


def _background_work(store: Store, monkeypatch, server: bool = True) -> tuple:
    """``app.start_background_work`` on the company server for ``store`` (the background process's own, as
    ``ap_coder background`` makes it; ``server`` False: a laptop's dashboard), its other work stubbed: (the last
    worker it started, everything it started)."""
    from types import SimpleNamespace

    from ap_coder.ai import azure_check, page_worker
    from ap_coder.coding import bulk
    from ap_coder.erp import jde_sync
    from ap_coder.web import app as web_app
    from tests.webclient import web_settings

    stub = SimpleNamespace(stop=lambda: None)
    stub.start = lambda: stub
    for module, name in ((page_worker, "start_background"), (azure_check, "start_background"),
                         (jde_sync, "Background"), (bulk, "QueueRecheck")):  # fmt: skip
        monkeypatch.setattr(module, name, lambda *args, **kwargs: stub)
    if server:
        monkeypatch.setenv("AP_SERVER_MODE", "1")
    else:
        monkeypatch.delenv("AP_SERVER_MODE", raising=False)
    workers = web_app.start_background_work(web_settings, store)
    return workers[-1], workers


def _stopped(workers: tuple) -> None:
    for worker in workers:
        worker.stop()
    for worker in workers:
        thread = getattr(worker, "thread", None)
        if thread is not None:
            thread.join(10)


def test_the_background_process_keeps_the_vendors_totals_it_works_out_again_for_every_process(
    tmp_path, ground_truth, clock, monkeypatch
):
    """B-2050: the company server's background process (``ap_coder background``: a Store of its own, which no
    ``create_app`` sets) worked the Vendors page's totals out again in its own memory only (``summary_memo``, a
    laptop's way since B-2048): the web workers went on showing the totals kept before, up to ``STALE_MOST`` old,
    then worked them out in a request (7 s at 300,000 invoices). What its refresher works out is kept for every
    process."""
    process = _Processes(monkeypatch)
    worker = _server(Store(tmp_path / "ap.db"), monkeypatch)
    _invoice(worker, tmp_path, ground_truth, "A-1", "Acme Supply")
    process("worker 1")
    assert [v["invoices"] for v in worker.vendor_summaries(settled=True)] == [1]
    _invoice(worker, tmp_path, ground_truth, "A-2", "Acme Supply")
    clock[0] += reports.SETTLE_MOST  # (past its settle)
    process("background")
    background = Store(worker.path)
    refresher, workers = _background_work(background, monkeypatch)
    try:
        ends = time.monotonic() + 20
        while (not worker._lease_held(reports.REFRESHER) or worker.next_want() is not None) and time.monotonic() < ends:
            time.sleep(0.05)
        time.sleep(2 * refresh.POLL)  # (its start: the totals asked lately, worked out again)
        assert worker.next_want() is None
        process("worker 2")
        worked = []
        real = worker._vendor_totals
        monkeypatch.setattr(worker, "_vendor_totals", lambda: worked.append(1) or real())
        assert [v["invoices"] for v in worker.vendor_summaries(settled=True)] == [2] and worked == []
    finally:
        _stopped(workers)


def _first_start(store: Store, caplog, ends: float = 30) -> refresh.Refresher:
    """The refresher started on ``store`` (the background process's), once it said its first start's default views
    worked out (D-200)."""
    refresher = refresh.Refresher(lambda: store).start()
    ends = time.monotonic() + ends
    while "default views worked out at a first start" not in caplog.text and time.monotonic() < ends:
        time.sleep(0.05)
    return refresher


def _halted(refresher: refresh.Refresher) -> None:
    refresher.stop()
    refresher.thread.join(30)
    assert not refresher.thread.is_alive()


def test_every_answer_the_refresher_works_out_has_a_default_view_or_says_why_not():
    """D-200: each settled answer (``refresh.ANSWERS``) is worked out at a first start as its page asks it opened with
    nothing in its address (``DEFAULTS``), or is listed in ``NO_DEFAULT`` with why not (one only a person's question
    asks): a new answer can't be left out unseen. Month-end's come first: the biggest."""
    named, without = [name for name, _ in refresh.DEFAULTS], dict(refresh.NO_DEFAULT)
    assert set(named) | set(without) == set(refresh.ANSWERS) and len(without) == len(refresh.NO_DEFAULT)
    assert not set(named) & set(without) and all(why.strip() for why in without.values())
    assert named[:3] == ["received_not_invoiced", "not_in_erp", "recurring"]


def test_at_a_first_start_each_pages_default_view_is_worked_out_before_anyone_asks_it(
    tmp_path, ground_truth, clock, monkeypatch, caplog
):
    """D-200: on a database never served (nothing asked in the last day), the refresher's start works out each page's
    default view (``DEFAULTS``), month-end's first, kept for every process; then each page opened with nothing in its
    address (the queue, Month-end, Spend, Sales tax, Insights, Vendors, Learning), by a web worker with nothing in its
    memory, takes the kept answers and works none out. What the pages ask (noted for the refresher's next start) is
    what was worked out: the same answers, questions and arguments."""
    from ap_coder.web.pages import insights as insights_page
    from tests.webclient import client_for

    monkeypatch.setattr(insights_page, "MIN_APPROVALS", 1)  # (Insights' operations shown too)
    store = Store(tmp_path / "ap.db")
    for n in range(3):
        _approved(store, tmp_path, ground_truth, f"A-{n}")
    _invoice(store, tmp_path, ground_truth, "Q-1")
    client = client_for(store)
    worked, real = [], Store.shared_answer

    def counted(self, name, compute, **kwargs):
        def work():
            worked.append(name)
            return compute()

        return real(self, name, work, **kwargs)

    monkeypatch.setattr(Store, "shared_answer", counted)
    process = _Processes(monkeypatch)
    process("background")
    background = _server(Store(store.path), monkeypatch)
    with caplog.at_level(logging.INFO, logger="ap_coder.web.refresh"):
        refresher = _first_start(background, caplog)
        _halted(refresher)
    assert f"the reports' default views worked out at a first start: {len(refresh.DEFAULTS)}" in caplog.text
    assert worked[0] == "received_not_invoiced" and set(worked) >= set(refresh.ANSWERS)
    with sqlite3.connect(store.path) as conn:
        kept = {(name, asked) for name, asked in conn.execute("SELECT name, asked FROM report_answers")
                if name in refresh.ANSWERS}  # fmt: skip
    assert _wants(store) == []  # (its own asks noted nowhere: nobody asked)

    process("web worker")
    monkeypatch.setattr(store, "background_answers", True)
    clock[0] += 2 * reports.STALE_MOST + 1  # (past every settle and the cap: each taken only as its stamp holds)
    worked.clear()
    for page in ("/review", "/month-end", "/spend", "/sales-tax", "/insights", "/vendors", "/learning"):
        assert client.get(page).status_code == 200, page
    assert not set(worked) & set(refresh.ANSWERS), worked
    noted = _wants(store)
    assert {(w["name"], w["asked"]) for w in noted} == kept
    with reports.refreshing():  # (its arguments as the warm-up gives them: worked out nowhere, all kept)
        defaults = {(name, json.dumps(default(store), separators=(",", ":"))) for name, default in refresh.DEFAULTS}
    assert {(w["name"], w["args"]) for w in noted} <= defaults
    assert {name for name, _ in defaults} == {w["name"] for w in noted} == set(refresh.ANSWERS)


def test_the_first_start_only_where_nothing_was_asked_lately_one_at_a_time_a_persons_want_first(
    tmp_path, clock, monkeypatch, caplog
):
    """D-200: the default views are worked out only when the refresher's start finds nothing asked within
    ``ASKED_WITHIN`` (after a restart or an update in use, what was asked is worked out again instead, D-198); one at
    a time, in their order, a want waiting taken before the next; one whose work (or its arguments') fails is logged
    and the others go on; the refresher stopped stops them."""
    store = Store(tmp_path / "ap.db")
    db, order, slow, go = _db(store), [], threading.Event(), threading.Event()

    def work(name):
        def run(store, args):
            order.append((name, args))
            if name == "slow":
                slow.set()
                assert go.wait(10)
            if name == "fails":
                raise ValueError("no such page")

        return run

    for name in ("asked", "slow", "fails", "later", "wanted"):
        monkeypatch.setitem(refresh.ANSWERS, name, work(name))

    def unreadable(store):
        raise ValueError("no default")

    monkeypatch.setattr(refresh, "DEFAULTS", (("slow", lambda store: [1]), ("fails", lambda store: [2]),
                                              ("later", unreadable), ("later", lambda store: [dt.date(2026, 11, 7)]),
                                              ("later", lambda store: [3])))  # fmt: skip
    store.want_answer("asked", "k", ["lately"], db, wanted=False)
    with caplog.at_level(logging.INFO, logger="ap_coder.web.refresh"):
        refresher = refresh.Refresher(lambda: store).start()
        try:
            ends = time.monotonic() + 10
            while not order and time.monotonic() < ends:
                time.sleep(0.05)
            time.sleep(3 * refresh.POLL)
        finally:
            _halted(refresher)
        assert order == [("asked", ["lately"])] and "default views" not in caplog.text  # (asked lately: not a first)

        order.clear()
        clock[0] += refresh.ASKED_WITHIN + 1  # (nothing asked in the last day: a first start)
        refresher = refresh.Refresher(lambda: store).start()
        try:
            assert slow.wait(10)
            store.want_answer("wanted", "w", ["a person's"], db)
            go.set()
            ends = time.monotonic() + 10
            while "default views worked out" not in caplog.text and time.monotonic() < ends:
                time.sleep(0.05)
        finally:
            _halted(refresher)
    assert order == [("slow", [1]), ("wanted", ["a person's"]), ("fails", [2]), ("later", [3])]
    found = [r.getMessage() for r in caplog.records if "default view" in r.getMessage()]
    assert found == ["the fails answer's default view was not worked out at a first start",
                     "the later answer's default view was not worked out at a first start",
                     "the later answer's default view was not worked out at a first start",
                     "the reports' default views worked out at a first start: 2"]  # fmt: skip

    # Stopped meanwhile: the default under way finished, none after it.
    order.clear()
    slow.clear()
    go.clear()
    with sqlite3.connect(store.path) as conn:
        conn.execute("DELETE FROM answer_wants")
    refresher = refresh.Refresher(lambda: store).start()
    try:
        assert slow.wait(10)
        refresher.stop()
    finally:
        go.set()
        _halted(refresher)
    assert order == [("slow", [1])]


def test_a_laptop_works_no_default_view_out(tmp_path, ground_truth, clock, monkeypatch):
    """D-200: a laptop has no background process, so no refresher and no first start's default views: each report is
    worked out where it is asked, as before."""
    store = Store(tmp_path / "ap.db")
    _approved(store, tmp_path, ground_truth, "A-1")
    _, workers = _background_work(store, monkeypatch, server=False)
    try:
        assert not any(isinstance(w, refresh.Refresher) for w in workers) and store.background_answers is False
        time.sleep(3 * refresh.POLL)
        assert not any(t.name == "ap-coder-answers" for t in threading.enumerate())
        with sqlite3.connect(store.path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM report_answers").fetchone()[0] == 0
    finally:
        _stopped(workers)


def _kept_warm(caplog, more: int = 2, ends: float = 10) -> None:
    """Once the refresher looked ``more`` more times at the default views nobody asked yet (B-2051): the second after
    the call looks once the first one's are asked."""

    def passes() -> int:
        return sum("nobody asked yet:" in r.getMessage() for r in caplog.records)

    until, ends = passes() + more, time.monotonic() + ends
    while passes() < until and time.monotonic() < ends:
        time.sleep(0.02)


def test_a_default_view_nobody_asked_is_kept_warm_until_it_is_asked_and_a_day_at_most(
    tmp_path, clock, monkeypatch, caplog
):
    """B-2051: after a first start's one pass nothing worked a default view out again until someone asked it, so the
    first person more than ``STALE_MOST`` after it, anything changed meanwhile (the night's intake, JD Edwards' sync),
    worked it out in their web worker, as before D-200. One nobody asked since the start is asked again, while no want
    waits, once its answer is ``WARM_AGAIN`` old (looked at every ``WARM_LOOK``): a cheap take while it holds, worked
    out again once something changed, so one younger than ``STALE_MOST`` is always there to show; once a page asked it
    it is left to D-198 (worked out again as it is asked); a day after the start, no more."""
    monkeypatch.setattr(refresh, "WARM_LOOK", 0.02, raising=False)
    for name in ("a.db", "b.db"):
        store = _server(Store(tmp_path / name), monkeypatch)
        asked, again = _rows_answer(store), _rows_answer(store)
        monkeypatch.setitem(refresh.ANSWERS, "rows", lambda store, args, again=again: again())
        monkeypatch.setattr(refresh, "DEFAULTS", (("rows", lambda store: []),))
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="ap_coder.web.refresh"):
            refresher = _first_start(store, caplog)
            try:
                assert again.worked == 1
                if name == "b.db":  # (a day after the start: no more)
                    clock[0] += refresh.ASKED_WITHIN + 1
                    _plain(store, ADD, "a")
                    time.sleep(0.3)
                    assert again.worked == 1
                    break
                clock[0] += refresh.WARM_AGAIN  # (nothing changed: a cheap take at each look)
                _kept_warm(caplog)
                assert again.worked == 1
                _plain(store, ADD, "a")  # (a change: worked out again at the next look)
                _kept_warm(caplog)
                assert again.worked == 2
                _plain(store, ADD, "b")  # (another, its answer younger than WARM_AGAIN: not yet)
                clock[0] += 31
                time.sleep(0.3)
                assert again.worked == 2
                clock[0] += refresh.WARM_AGAIN
                _kept_warm(caplog)
                assert again.worked == 3 and _vendor_rows(store) == asked() and asked.worked == 0
                _plain(store, ADD, "c")  # (asked by a page since the start: left to D-198)
                clock[0] += refresh.WARM_AGAIN
                time.sleep(0.3)
                assert again.worked == 3
            finally:
                _halted(refresher)


def test_a_first_start_overnight_the_morning_first_person_works_no_report_out(
    tmp_path, ground_truth, clock, monkeypatch, caplog
):
    """B-2051, as it was found: the background process starts at night after a quiet day (an update, a reboot) and
    works the default views out; an invoice comes in an hour later; the first person opens each page six hours later.
    Their web worker works none of the reports' answers out."""
    from ap_coder.web.pages import insights as insights_page
    from tests.webclient import client_for

    monkeypatch.setattr(insights_page, "MIN_APPROVALS", 1)
    monkeypatch.setattr(refresh, "WARM_LOOK", 0.02, raising=False)
    store = Store(tmp_path / "ap.db")
    for n in range(3):
        _approved(store, tmp_path, ground_truth, f"A-{n}")
    client = client_for(store)
    worked, real = [], Store.shared_answer

    def counted(self, name, compute, **kwargs):
        def work():
            worked.append(name)
            return compute()

        return real(self, name, work, **kwargs)

    monkeypatch.setattr(Store, "shared_answer", counted)
    process = _Processes(monkeypatch)
    process("background")
    background = _server(Store(store.path), monkeypatch)
    with caplog.at_level(logging.DEBUG, logger="ap_coder.web.refresh"):
        refresher = _first_start(background, caplog)
        try:
            clock[0] += 3600
            _invoice(background, tmp_path, ground_truth, "NIGHT-1")  # (one invoice in the night, nobody asking)
            _kept_warm(caplog)
            clock[0] += 5 * 3600
            _kept_warm(caplog)
        finally:
            _halted(refresher)
    process("web worker")
    monkeypatch.setattr(store, "background_answers", True)
    worked.clear()
    for page in ("/review", "/month-end", "/spend", "/sales-tax", "/insights", "/vendors", "/learning"):
        assert client.get(page).status_code == 200, page
    assert not set(worked) & set(refresh.ANSWERS), worked


def test_after_a_restore_on_the_running_server_its_start_is_done_again_for_the_restored_database(
    tmp_path, clock, monkeypatch, caplog
):
    """B-2052: the refresher did its start (the answers asked lately, or a first start's default views) once in its
    process's life, so a database restored while it ran (Settings → Data & backups → Restore) got neither until the
    background process started again. Its start is done again for each database it finds: after a restore, another
    ``summary_id``."""
    store = _server(Store(tmp_path / "ap.db"), monkeypatch)
    worked = []
    monkeypatch.setitem(refresh.ANSWERS, "rows", lambda store, args: worked.append(_db(store)))
    monkeypatch.setattr(refresh, "DEFAULTS", (("rows", lambda store: []),))
    with caplog.at_level(logging.INFO, logger="ap_coder.web.refresh"):
        refresher = _first_start(store, caplog)
        try:
            first = _db(store)
            assert worked == [first]
            caplog.clear()
            store.restore_from(store.backup_now("before"))
            ends = time.monotonic() + 20
            while "worked out at a first start" not in caplog.text and time.monotonic() < ends:
                time.sleep(0.05)
        finally:
            _halted(refresher)
    assert worked == [first, _db(store)] and _db(store) != first
    assert "the reports' default views worked out at a first start: 1" in caplog.text


def test_a_want_left_waiting_long_ago_does_not_stop_a_first_start(tmp_path, clock, monkeypatch, caplog):
    """B-2053: the refresher's start took any want still waiting for an answer asked lately (``want_again`` counted
    every one), so one left waiting days ago (the server stopped before it was worked out) skipped a first start's
    default views. A first start is decided by what was asked within ``ASKED_WITHIN``; the want left is worked out
    as before, first."""
    store = Store(tmp_path / "ap.db")
    order = []
    for name in ("left", "default"):
        monkeypatch.setitem(refresh.ANSWERS, name, lambda store, args, name=name: order.append(name))
    monkeypatch.setattr(refresh, "DEFAULTS", (("default", lambda store: []),))
    store.want_answer("left", "k", [], _db(store))  # (waiting since three days ago)
    clock[0] += 3 * 24 * 3600
    assert store.want_again(clock[0] - refresh.ASKED_WITHIN) == 0  # (none asked lately)
    with caplog.at_level(logging.INFO, logger="ap_coder.web.refresh"):
        refresher = _first_start(store, caplog, ends=10)
        _halted(refresher)
    assert order == ["left", "default"]
    assert "the reports' default views worked out at a first start: 1" in caplog.text
