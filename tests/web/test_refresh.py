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
from tests.core.test_kept_answers import Shared, _approved, _bill, _invoice, _plain, _Processes, _vendor_rows

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
    """The refresher started on ``store`` (the background process's), once it said its start's default views worked
    out (D-200)."""
    refresher = refresh.Refresher(lambda: store).start()
    ends = time.monotonic() + ends
    while "default views worked out at the refresher's start" not in caplog.text and time.monotonic() < ends:
        time.sleep(0.05)
    return refresher


def _halted(refresher: refresh.Refresher) -> None:
    refresher.stop()
    refresher.thread.join(30)
    assert not refresher.thread.is_alive()


def test_every_answer_the_refresher_works_out_has_a_default_view_or_says_why_not():
    """D-200: each settled answer (``refresh.ANSWERS``) is worked out at a start as its page asks it opened with
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
    assert f"the reports' default views worked out at the refresher's start: {len(refresh.DEFAULTS)}" in caplog.text
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


def test_the_start_works_out_the_answers_asked_lately_then_the_default_views_one_at_a_time_a_persons_want_first(
    tmp_path, clock, monkeypatch, caplog
):
    """D-200: at its start the refresher works out again the answers asked lately (D-198), then each default view, in
    their order, one at a time, a want waiting taken before the next (B-2057: a start that found asks worked none out);
    one whose work (or its arguments') fails is logged and the others go on, and is not asked again; the refresher
    stopped stops them."""
    monkeypatch.setattr(refresh, "WARM_LOOK", 0.02)
    monkeypatch.setattr(refresh, "POLL", 0.02)
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
    with caplog.at_level(logging.DEBUG, logger="ap_coder.web.refresh"):
        refresher = refresh.Refresher(lambda: store).start()
        try:
            assert slow.wait(10)
            store.want_answer("wanted", "w", ["a person's"], db)
            go.set()
            ends = time.monotonic() + 10
            while "default views worked out" not in caplog.text and time.monotonic() < ends:
                time.sleep(0.05)
            _kept_warm(caplog, more=3)
        finally:
            _halted(refresher)
    # (the latest asked of each asked again once kept warm: nothing kept of them, so no more)
    assert order == [("asked", ["lately"]), ("slow", [1]), ("wanted", ["a person's"]), ("fails", [2]), ("later", [3]),
                     ("asked", ["lately"]), ("wanted", ["a person's"])]  # fmt: skip
    found = [r.getMessage() for r in caplog.records if "default view" in r.getMessage()]
    assert found == ["the fails answer's default view was not worked out ahead of its page",
                     "the later answer's default view was not worked out ahead of its page",
                     "the later answer's default view was not worked out ahead of its page",
                     "the reports' default views worked out at the refresher's start: 2"]  # fmt: skip

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


def _kept_warm(caplog, more: int = 3, ends: float = 10) -> None:
    """Once the refresher looked ``more`` more times at the views it keeps warm (B-2052, B-2055). A look comes only
    once the last one's views are asked, but the first after the call may have looked just before the test moved the
    clock (it finds none due), and the second marks them due: the third comes after they are asked (Windows CI: the
    second's view not yet worked out)."""

    def passes() -> int:
        return sum("views kept warm:" in r.getMessage() for r in caplog.records)

    until, ends = passes() + more, time.monotonic() + ends
    while passes() < until and time.monotonic() < ends:
        time.sleep(0.02)


def test_a_default_view_is_kept_warm_while_the_database_is_used(tmp_path, clock, monkeypatch, caplog):
    """B-2052: after a first start's one pass nothing worked a default view out again until someone asked it, so the
    first person more than ``STALE_MOST`` after it, anything changed meanwhile (the night's intake, JD Edwards' sync),
    worked it out in their web worker, as before D-200. A default view is asked again, while no want waits, once its
    answer is ``WARM_AGAIN`` old (looked at every ``WARM_LOOK``): a cheap take while it holds, worked out again once
    something changed, so one younger than ``STALE_MOST`` is always there to show; asked by a page too, it is one view
    kept warm (B-2055); while anything was asked within ``ASKED_WITHIN``, or for that long after the start."""
    monkeypatch.setattr(refresh, "WARM_LOOK", 0.02)
    monkeypatch.setattr(refresh, "POLL", 0.02)
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
                _plain(store, ADD, "c")  # (asked by a page too: one view kept warm)
                clock[0] += refresh.WARM_AGAIN
                _kept_warm(caplog)
                assert again.worked == 4
                clock[0] += refresh.ASKED_WITHIN + 1  # (nothing asked for a day: no more)
                _plain(store, ADD, "d")
                time.sleep(0.3)
                assert again.worked == 4
            finally:
                _halted(refresher)


def test_a_first_start_overnight_the_morning_first_person_works_no_report_out(
    tmp_path, ground_truth, clock, monkeypatch, caplog
):
    """B-2052, as it was found: the background process starts at night after a quiet day (an update, a reboot) and
    works the default views out; an invoice comes in an hour later; the first person opens each page six hours later.
    Their web worker works none of the reports' answers out."""
    from ap_coder.web.pages import insights as insights_page
    from tests.webclient import client_for

    monkeypatch.setattr(insights_page, "MIN_APPROVALS", 1)
    monkeypatch.setattr(refresh, "WARM_LOOK", 0.02)
    monkeypatch.setattr(refresh, "POLL", 0.02)
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
    """B-2053: the refresher did its start (the answers asked lately, or a first start's default views) once in its
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
            while "worked out at the refresher's start" not in caplog.text and time.monotonic() < ends:
                time.sleep(0.05)
        finally:
            _halted(refresher)
    assert worked == [first, _db(store)] and _db(store) != first
    assert "the reports' default views worked out at the refresher's start: 1" in caplog.text


def test_a_want_left_waiting_long_ago_does_not_stop_a_first_start(tmp_path, clock, monkeypatch, caplog):
    """B-2054: the refresher's start took any want still waiting for an answer asked lately (``want_again`` counted
    every one), so one left waiting days ago (the server stopped before it was worked out) skipped a first start's
    default views. ``want_again`` counts what was asked within ``ASKED_WITHIN``; the want left is worked out as
    before, first, then the default views (worked out at every start since B-2057)."""
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
    assert "the reports' default views worked out at the refresher's start: 1" in caplog.text


def _where(store: Store, name: str, where: list, **stamp) -> Shared:
    """A settled answer ``name`` of the vendors' rows (30 s), noting the thread of each work in ``where``."""

    def rows():
        where.append(threading.current_thread().name)
        return _vendor_rows(store)

    return Shared(store, name, rows, version="t-1", topic="", changes=("vendors",), settle=30, **stamp)


def test_on_a_server_that_keeps_running_each_morning_the_reports_asked_and_the_default_views_are_warm(
    tmp_path, clock, monkeypatch, caplog
):
    """B-2055: only a first start's default views nobody asked were kept warm, so on a server that keeps running (or
    after a start that found asks) a report asked in the evening and changed in the night (JD Edwards' nightly sync,
    the intake) was over ``STALE_MOST`` old at 08:00 and the first person's web worker worked it out; and a default
    view asked with today's date (Spend, Insights' operations, the recurring vendors) is another question each day.
    On every start and from then on, the latest question asked of each settled answer within ``ASKED_WITHIN``
    (``answer_wants``, its arguments there) and each page's default view (asked anew: today's) are kept warm."""
    monkeypatch.setattr(refresh, "WARM_LOOK", 0.02)
    monkeypatch.setattr(refresh, "POLL", 0.02)
    path, where = tmp_path / "ap.db", []
    web, background = _server(Store(path), monkeypatch), _server(Store(path), monkeypatch)
    asked = _where(web, "asked", where, again=["Q1"])  # (a question of its own: no page's default view)

    def day(store) -> str:
        return str(int(clock[0] // 86400))

    monkeypatch.setitem(refresh.ANSWERS, "asked", lambda s, args: _where(s, "asked", where, again=args)())
    monkeypatch.setitem(refresh.ANSWERS, "dated", lambda s, args: _where(s, "dated", where, asked=args[0],
                                                                         again=args)())  # fmt: skip
    monkeypatch.setattr(refresh, "DEFAULTS", (("dated", lambda s: [day(s)]),))
    process = _Processes(monkeypatch)
    process("web worker")
    assert asked() == [] and where == ["MainThread"]  # (day 1, 09:00: asked by a page)
    clock[0] += 8 * 3600
    asked()  # (17:00, the last ask of the day)
    with caplog.at_level(logging.DEBUG, logger="ap_coder.web.refresh"):
        process("background")
        refresher = refresh.Refresher(lambda: background).start()  # (a start that finds asks: not a first start)
        try:
            _kept_warm(caplog)
            clock[0] += 9 * 3600  # (02:00: the nightly sync changes the vendors)
            _plain(web, ADD, "night")
            _kept_warm(caplog)
            clock[0] += 6 * 3600  # (08:00, day 2)
            _kept_warm(caplog)
        finally:
            _halted(refresher)
    process("web worker")
    where.clear()
    dated = _where(web, "dated", where, asked=day(web), again=[day(web)])
    assert asked() == _vendor_rows(web) == dated() and where == []  # (the first person: nothing worked out here)


def test_a_view_whose_answer_cant_be_kept_for_every_process_is_not_kept_warm(tmp_path, clock, monkeypatch, caplog):
    """B-2056: a view whose answer can't be kept for every process (NaN in it, or over ``ANSWER_MOST_BYTES``) was due
    at every look, nothing kept to age, and worked out again for nothing (31 times in half an hour of a change a
    minute). Once its work keeps nothing it is no longer kept warm, a default view or a question asked."""
    monkeypatch.setattr(refresh, "WARM_LOOK", 0.02)
    monkeypatch.setattr(refresh, "POLL", 0.02)
    store = _server(Store(tmp_path / "ap.db"), monkeypatch)
    worked = []

    def nan(name):
        def answer(s, args):
            def work():
                worked.append(name)
                return [*_vendor_rows(s), [float("nan")]]

            return s.shared_answer(name, work, version="t-1", topic="", changes=("vendors",), settle=30, again=[])

        return answer

    for name in ("view", "question"):
        monkeypatch.setitem(refresh.ANSWERS, name, nan(name))
    monkeypatch.setattr(refresh, "DEFAULTS", (("view", lambda s: []),))
    process = _Processes(monkeypatch)
    for asked in (False, True):  # (a first start's default view; then a question a page asked too)
        if asked:
            process("web worker")
            refresh.ANSWERS["question"](store, [])  # (noted asked, nothing kept for every process)
            clock[0] += 120
        process(f"background {asked}")
        worked.clear()
        with caplog.at_level(logging.DEBUG, logger="ap_coder.web.refresh"):
            refresher = refresh.Refresher(lambda: store).start()
            try:
                _kept_warm(caplog, more=3)
                settled = list(worked)
                for minute in range(10):  # (ten minutes of a change a minute)
                    clock[0] += 61
                    _plain(store, ADD, f"v{asked}{minute}")
                    _kept_warm(caplog)
            finally:
                _halted(refresher)
        assert worked == settled and worked.count("view") == 1 and worked.count("question") <= 2, worked


def test_a_page_asked_a_moment_before_the_refreshers_start_does_not_skip_the_default_views(
    tmp_path, clock, monkeypatch, caplog
):
    """B-2057: on a database never served, a web worker up a moment before the background process showed the queue
    (its late pill, noted asked): the refresher's start found one answer asked lately and worked none of the default
    views out. The default views are worked out at every start, after the answers asked lately."""
    web = _server(Store(tmp_path / "ap.db"), monkeypatch)
    order = []
    for name in ("queue_pill", "month_end", "spend"):
        monkeypatch.setitem(refresh.ANSWERS, name, lambda s, args, name=name: order.append(name))
    monkeypatch.setattr(refresh, "DEFAULTS", (("month_end", lambda s: []), ("spend", lambda s: []),
                                              ("queue_pill", lambda s: [])))  # fmt: skip
    pill = Shared(web, "queue_pill", lambda: _vendor_rows(web), version="t-1", topic="", changes=("vendors",),
                  settle=30, again=[])  # fmt: skip
    pill()  # (the first request after the start, noted asked: D-198's _asked)
    clock[0] += 1
    with caplog.at_level(logging.INFO, logger="ap_coder.web.refresh"):
        refresher = _first_start(Store(web.path), caplog, ends=10)
        _halted(refresher)
    assert order[:4] == ["queue_pill", "month_end", "spend", "queue_pill"]  # (the one asked lately first)


# --- A report being worked out, answered at once (D-201) -------------------------------------------------------------

HTML = {"accept": "text/html,application/xhtml+xml"}  # (a browser's page: a link, the address bar, a reload)
REFRESH = re.compile(r'<meta http-equiv="refresh" content="\d+">')
GOOD = "correct horse battery"
LATE_PILL = "regular invoice late"  # (the queue's pill of the recurring vendors: ``review.today_pills``)


def _on_the_server(store: Store, base_url: str = "https://localhost"):
    """A web worker of the company server on ``store``, an Admin logged in."""
    from fastapi.testclient import TestClient

    from ap_coder.web.app import create_app
    from tests.webclient import enrol, sign_in, web_settings

    client = TestClient(create_app(web_settings(), store, start_reader=False, server=True), base_url=base_url,
                        client=("10.0.0.5", 50000))  # fmt: skip
    uid = store.add_user("boss", GOOD, role="admin", display_name="Boss")
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    enrol(store, uid)
    assert sign_in(client, "boss", GOOD).status_code in (200, 303)
    return client


def _month_end_held(store: Store, client, monkeypatch, page: str = "/month-end") -> list[str]:
    """Month-end's answers (or ``page``'s) asked once, then none kept anywhere (the database's, this process's memory)
    and each held by another process, as the background process's warm-up holds them at a first start (D-200): their
    holds."""
    held, real = [], Store.take_lease

    def take(self, name, owner, seconds):
        if name.startswith("answer/") and name not in held:
            held.append(name)
        return real(self, name, owner, seconds)

    monkeypatch.setattr(Store, "take_lease", take)
    assert client.get(page, headers=HTML).status_code == 200
    monkeypatch.setattr(Store, "take_lease", real)
    with sqlite3.connect(store.path) as conn:
        conn.execute("DELETE FROM report_answers")
    _Processes(monkeypatch)("a web worker just started")
    for lease in held:
        assert store.take_lease(lease, "the background process", 600)
    return held


def _month_end_worked_out_ahead(store: Store, held: list[str]) -> None:
    """The warm-up's work on Month-end done (``refresh.DEFAULTS``' first three): kept for every process, let go of."""
    for lease in held:
        store.drop_lease(lease, "the background process")
    for name, args_of in refresh.DEFAULTS[:3]:
        assert refresh.ahead(store, name, args_of, "default view")[0], name


def test_on_the_server_a_report_another_process_works_out_with_nothing_kept_says_so_at_once(
    tmp_path, ground_truth, monkeypatch
):
    """D-201: at a first start (nothing kept yet) the background process's warm-up holds Month-end's work (12 to 50 s
    at 300,000 invoices); a web worker's request asking it waited for that work, up to ``ANSWER_WAIT_MOST``, holding
    a thread (four asks waited 10.7-49 s, the worker's other requests behind them). It waits ``ANSWER_BRIEF`` at most
    now: a browser's page is a page in the app's layout saying the report is being worked out, which asks again by
    itself (a meta refresh: no script, within the Content-Security-Policy), never kept by the browser; a script's ask
    (JSON, a file) a 503 with Retry-After. Two asks at once in one worker likewise. Once the warm-up kept the answers,
    the page shows them, working none out."""
    monkeypatch.setattr(reports, "ANSWER_WAIT_MOST", 8.0)  # (waited for this long before, then worked out here)
    monkeypatch.setattr(reports, "ANSWER_BRIEF", 0.5)
    store = Store(tmp_path / "ap.db")
    _approved(store, tmp_path, ground_truth, "A-1")
    client = _on_the_server(store)
    held = _month_end_held(store, client, monkeypatch)
    assert len(held) == 3  # (received, not invoiced; not in the ERP; the recurring vendors)
    worked, real = [], Store.shared_answer

    def counted(self, name, compute, **kwargs):
        return real(self, name, lambda: worked.append(name) or compute(), **kwargs)

    monkeypatch.setattr(Store, "shared_answer", counted)

    at = time.monotonic()
    page = client.get("/month-end", headers=HTML)
    assert time.monotonic() - at < reports.ANSWER_BRIEF + 1.5
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store", page.status_code
    assert page.headers["x-ap-coder-working"] == "1"  # (said apart from a page served: the access log, bench load)
    assert "This report is being worked out" in page.text and REFRESH.search(page.text)
    assert 'name="end"' not in page.text and 'class="rail"' in page.text  # (not the report: the app's layout)
    for asked in ({"accept": "application/json"}, {"accept": "*/*"}, {"accept": "text/csv"}):
        answer = client.get("/month-end", headers=asked)
        assert answer.status_code == 503 and int(answer.headers["retry-after"]) > 0, asked
        assert answer.headers["cache-control"] == "no-store" and answer.headers["x-ap-coder-working"] == "1"
        assert answer.json()["ok"] is False and "being worked out" in answer.json()["error"]

    answers, threads = [], []
    for _ in range(2):  # (two asks at once in this process: the second waits on the first's thread here)
        threads.append(threading.Thread(target=lambda: answers.append(client.get("/month-end", headers=HTML))))
    at = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert time.monotonic() - at < reports.ANSWER_BRIEF + 2.5
    assert [a.status_code for a in answers] == [200, 200] and all("being worked out" in a.text for a in answers)
    assert not set(worked) & set(refresh.ANSWERS), worked  # (nothing worked out here meanwhile)

    _month_end_worked_out_ahead(store, held)
    _Processes(monkeypatch)("the web worker")
    worked.clear()
    shown = client.get("/month-end", headers=HTML)
    assert shown.status_code == 200 and 'name="end"' in shown.text and "being worked out" not in shown.text
    assert not REFRESH.search(shown.text) and not set(worked) & set(refresh.ANSWERS), worked


def test_on_a_laptop_a_report_held_by_another_is_still_worked_out_in_the_request(tmp_path, ground_truth, monkeypatch):
    """D-201 changes nothing on a laptop (no background process): its page waits for an answer another works out, as
    before, then works it out itself, and shows the report."""
    from tests.webclient import client_for

    monkeypatch.setattr(reports, "ANSWER_WAIT_MOST", 0.6)
    monkeypatch.setattr(reports, "ANSWER_BRIEF", 0.1)
    store = Store(tmp_path / "ap.db")
    _approved(store, tmp_path, ground_truth, "A-1")
    client = client_for(store)
    _month_end_held(store, client, monkeypatch)
    at = time.monotonic()
    page = client.get("/month-end", headers=HTML)
    assert time.monotonic() - at >= reports.ANSWER_WAIT_MOST  # (waited for the other, as before)
    assert page.status_code == 200 and 'name="end"' in page.text and "being worked out" not in page.text


def test_only_a_pages_read_on_the_server_is_told_an_answer_is_being_worked_out(tmp_path, clock, monkeypatch):
    """D-201's answer at once is a page's GET on the company server only (``reports.asked_by_a_page``), of a settled
    answer the refresher works out (``again``): asked anywhere else (a background job, an action's POST), with no
    ``again``, or in a write under way, it is waited for or worked out as before."""
    monkeypatch.setattr(reports, "ANSWER_WAIT_MOST", 0.6)
    monkeypatch.setattr(reports, "ANSWER_BRIEF", 0.2)
    store = _server(Store(tmp_path / "ap.db"), monkeypatch)
    asked = _rows_answer(store)
    exact = Shared(store, "exact", lambda: _vendor_rows(store), version="t-1", topic="", changes=("vendors",))
    for name in ("rows", "exact"):
        assert store.take_lease(f"answer/{name}/{reports.asked_key(None)}", "the background process", 600)
    with reports.asked_by_a_page():
        at = time.monotonic()
        with pytest.raises(reports.Working):
            asked()
        assert time.monotonic() - at < reports.ANSWER_BRIEF + 0.5 and asked.worked == 0
        assert exact() == [] and exact.worked == 1  # (not settled, no refresher behind it: waited for, then here)
        with store.session(), store._conn() as conn:  # (a write under way: worked out afresh, never waited for)
            conn.execute(ADD, ("acme",))
            assert asked() == [] and asked.worked == 1  # (``_vendor_rows`` reads what is committed)
    at = time.monotonic()
    assert asked() == _vendor_rows(store) != [] and asked.worked == 2  # (outside a page's read: waited for, as before)
    assert time.monotonic() - at >= reports.ANSWER_WAIT_MOST


def test_on_the_server_the_queue_opens_without_its_late_pill_while_the_recurring_vendors_are_worked_out(
    tmp_path, ground_truth, monkeypatch
):
    """The queue (everyone's first page) shows the recurring vendors only as a pill: while another process works
    them out with nothing kept yet, it opens at once without the pill (not even ``ANSWER_BRIEF`` waited: at a first
    start everyone opens it, each wait a thread), never "being worked out"; once they are kept, the pill is back."""
    from ap_coder.core.store import load_sample_setup

    monkeypatch.setattr(reports, "ANSWER_WAIT_MOST", 8.0)
    monkeypatch.setattr(reports, "ANSWER_BRIEF", 4.0)
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)  # (the queue itself, not its first steps)
    for n, days_ago in enumerate((120, 90, 60)):  # (a monthly bill, a month late)
        _bill(store, tmp_path, ground_truth, f"R-{n}", days_ago)
    client = _on_the_server(store)
    _Processes(monkeypatch)("a web worker just started")
    lease = f"answer/recurring/{reports.asked_key(dt.date.today())}"
    assert store.take_lease(lease, "the background process", 600)
    at = time.monotonic()
    queue = client.get("/review", headers=HTML)
    assert time.monotonic() - at < reports.ANSWER_BRIEF - 1.5
    assert queue.status_code == 200 and "R-2" in queue.text and "being worked out" not in queue.text
    assert not REFRESH.search(queue.text) and LATE_PILL not in queue.text
    store.drop_lease(lease, "the background process")
    assert refresh.ahead(store, *refresh.DEFAULTS[3], "default view")[0]  # (the warm-up's: the queue's, today's)
    _Processes(monkeypatch)("the web worker")
    assert LATE_PILL in client.get("/review", headers=HTML).text


def test_in_a_browser_the_being_worked_out_page_draws_itself_again_then_shows_the_report(
    tmp_path, ground_truth, monkeypatch, chromium
):
    """D-201 in a browser, under the company server's Content-Security-Policy: Month-end opened while the warm-up
    holds its work shows that it is being worked out and asks again by itself, every few seconds, with no script and
    nothing the policy refuses; once the work is kept, the same page shows the report."""
    from ap_coder.web import app as web_app
    from ap_coder.web.identity import SESSION_COOKIE
    from tests.browser import browser_on, wait_until

    monkeypatch.setattr(reports, "ANSWER_BRIEF", 0.3)
    monkeypatch.setattr(web_app, "WORKING_AGAIN", 1)
    store = Store(tmp_path / "ap.db")
    _approved(store, tmp_path, ground_truth, "A-1")
    client = _on_the_server(store)  # (logged in over HTTPS: its Secure cookie kept and sent)
    held = _month_end_held(store, client, monkeypatch)
    assert len(held) == 3
    with browser_on(chromium, client.app) as (browser, base):
        context = browser.new_context()
        # As a header: the __Host- cookie is Secure, which Chromium doesn't take for this http:// address
        context.set_extra_http_headers({"Cookie": f"{SESSION_COOKIE}={client.cookies.get(SESSION_COOKIE)}"})
        page = context.new_page()
        problems, asks = [], []
        page.on("console", lambda m: problems.append(m.text) if "Content Security Policy" in m.text else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.on("request", lambda r: asks.append(r.url) if r.url.endswith("/month-end") else None)
        answer = page.goto(base + "/month-end")
        assert answer is not None and answer.status == 200 and "content-security-policy" in answer.headers
        page.wait_for_selector("text=This report is being worked out")
        wait_until(page, lambda: len(asks) >= 3, timeout=10)  # (it drew itself again, still being worked out)
        assert len(asks) >= 3 and page.locator("input[name=end]").count() == 0
        _month_end_worked_out_ahead(store, held)
        page.wait_for_selector("input[name=end]", timeout=15000)
        assert "being worked out" not in page.content()
    assert problems == [], problems


@pytest.mark.parametrize("most", [None, 0.0, 2.0])
def test_an_answer_another_kept_and_let_go_of_just_after_the_ask_looked_is_taken_by_a_page_too(
    tmp_path, clock, monkeypatch, most
):
    """B-2058: the other process kept its answer and let go of its hold just after this ask found none and could not
    take the hold (B-1919's moment). The wait's first look takes it (``most`` None: not a page); a page's ask whose
    wait was over by then (the queue's 0 s, or a page that spent its 2 s on a thread here) skipped that look and
    worked the answer out itself, without a hold. It looks once more before it says "being worked out" or works it
    out."""
    store = _server(Store(tmp_path / "ap.db"), monkeypatch)
    asked = _rows_answer(store)
    key = reports.asked_key(None)
    lease = f"answer/rows/{key}"
    assert store.take_lease(lease, "the background process", 600)
    real = Store.take_lease

    def take(self, name, owner, seconds):
        got = real(self, name, owner, seconds)
        if name == lease and not got:  # (the other process done just now: kept, then let go of)
            stamp, _ = store._answer_stamp("", ("vendors",))
            store.keep_answer("rows", key, "t-1", stamp, [["kept by the other"]], reports.wall(), 0.01)
            store.drop_lease(lease, "the background process")
        return got

    monkeypatch.setattr(Store, "take_lease", take)
    if most is None:
        assert asked() == [["kept by the other"]]
    else:
        with reports.asked_by_a_page(most):
            assert asked() == [["kept by the other"]]
    assert asked.worked == 0


def test_a_pages_ask_without_a_wait_takes_the_answer_kept_while_a_thread_here_only_reads_it(
    tmp_path, clock, monkeypatch
):
    """B-2059: the queue's late pill (an ask that waits 0 s) gave up at once on this process's lock of the answer,
    which a thread here holds a moment while it only reads and decodes the answer kept: it is taken from the
    database when that one holds, and "being worked out" is said only when none there does."""
    store = _server(Store(tmp_path / "ap.db"), monkeypatch)
    asked = _rows_answer(store)
    assert asked() == [] and asked.worked == 1  # (kept for every process)
    _Processes(monkeypatch)("another web worker")  # (nothing in its memory)
    with reports._WORKING.setdefault((str(store.path), "rows", reports.asked_key(None)), threading.Lock()):
        with reports.asked_by_a_page(0.0):  # (a thread here reading the kept answer meanwhile)
            assert asked() == [] and asked.worked == 1
            _plain(store, ADD, "acme")
            clock[0] += 61  # (the one kept no longer holds, nor is shown meanwhile: past its settle and the wait)
            with pytest.raises(reports.Working):
                asked()
    assert asked.worked == 1


def test_three_queues_opened_at_once_in_a_worker_with_nothing_in_memory_all_show_the_late_pill(
    tmp_path, ground_truth, monkeypatch
):
    """B-2059 on the page: a web worker just started (nothing in its memory) opens three queues at once while the
    recurring vendors kept for every process hold; the first reads them (0.3 s, a big answer), the others showed no
    late pill. Each shows it."""
    from ap_coder.core.store import load_sample_setup

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    for n, days_ago in enumerate((120, 90, 60)):
        _bill(store, tmp_path, ground_truth, f"R-{n}", days_ago)
    client = _on_the_server(store)
    assert LATE_PILL in client.get("/review", headers=HTML).text  # (worked out and kept for every process)
    _Processes(monkeypatch)("a web worker just started")
    real = Store.kept_answer

    def slow(self, name, key, version):  # (a big answer's read and decode: the recurring vendors at 300,000)
        if name == "recurring":
            time.sleep(0.3)
        return real(self, name, key, version)

    monkeypatch.setattr(Store, "kept_answer", slow)
    shown: list[bool] = []
    threads = [threading.Thread(target=lambda: shown.append(LATE_PILL in client.get("/review", headers=HTML).text))
               for _ in range(3)]  # fmt: skip
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert shown == [True, True, True]


def test_the_vendor_master_and_past_codings_views_are_never_replaced_by_being_worked_out(
    tmp_path, ground_truth, monkeypatch
):
    """B-2060: the Vendors page and Learning's coding tab were replaced whole by "being worked out" while one of
    their report answers was: a vendor's details (to put it on hold, correct its numbers), the vendor master's import
    (Getting started's step) and Teach from past coding. Those views wait for the answers as before; the report
    views themselves (the vendor list, the coding accuracy) may say so."""
    monkeypatch.setattr(reports, "ANSWER_WAIT_MOST", 0.3)
    monkeypatch.setattr(reports, "ANSWER_BRIEF", 0.1)
    store = Store(tmp_path / "ap.db")
    _approved(store, tmp_path, ground_truth, "A-1")
    client = _on_the_server(store)
    with sqlite3.connect(store.path) as conn:
        key = conn.execute("SELECT vendor_key FROM invoices LIMIT 1").fetchone()[0]
    views = {
        "/vendors": {f"/vendors?open={key}": 'id="vendor"', "/vendors?setup=1": 'id="import" open'},
        "/learning": {"/learning?teach=1": 'id="history" open', "/learning?history=gone": 'id="history" open'},
    }
    for report, shown in views.items():
        held = _month_end_held(store, client, monkeypatch, report)
        assert held and "being worked out" in client.get(report, headers=HTML).text, report
        for view, mark in shown.items():
            _Processes(monkeypatch)("a web worker just started")
            with sqlite3.connect(store.path) as conn:
                conn.execute("DELETE FROM report_answers")
            page = client.get(view, headers=HTML)
            assert page.status_code == 200 and mark in page.text and "being worked out" not in page.text, view
        for lease in held:
            store.drop_lease(lease, "the background process")


def test_a_report_asked_while_the_workers_long_routes_are_busy_draws_itself_again(tmp_path, monkeypatch):
    """B-2061: the "being worked out" page asks again by itself; one of its asks that waited too long for a place in
    its web worker's long routes (``core.run_heavy``) got the "Busy" page, which did not, so the person was left on
    "Busy" until they asked again. A browser's busy page draws itself again too; a script's 503 is as before."""
    import anyio

    from ap_coder.web import core

    class Full:  # (every place of the long routes taken)
        async def acquire(self) -> None:
            await anyio.sleep(30)

        def release(self) -> None:
            pass

    monkeypatch.setattr(core, "heavy_lane", Full)
    monkeypatch.setattr(core, "HEAVY_WAIT", 0.1)
    client = _on_the_server(Store(tmp_path / "ap.db"))
    page = client.get("/month-end", headers=HTML)
    assert page.status_code == 503 and "Busy" in page.text and REFRESH.search(page.text)
    answer = client.get("/month-end", headers={"accept": "application/json"})
    assert answer.status_code == 503 and answer.json()["error"] == core.BUSY


def test_a_job_started_by_a_pages_read_waits_for_its_answers_as_anywhere(tmp_path, clock, monkeypatch):
    """B-2063 (latent): a job's thread runs in a copy of the request's context (who started it is who its events
    name), so a job started by a page's GET took the page's brief wait along: a settled answer another process works
    out failed the job with "being worked out" instead of being waited for. A job (and each file a run reads on a
    thread of its own) waits as anywhere."""
    from ap_coder.web.jobs import DONE, Job

    monkeypatch.setattr(reports, "ANSWER_BRIEF", 0.1)
    monkeypatch.setattr(reports, "ANSWER_WAIT_MOST", 0.6)
    store = _server(Store(tmp_path / "ap.db"), monkeypatch)
    asked = _rows_answer(store)
    assert store.take_lease(f"answer/rows/{reports.asked_key(None)}", "the background process", 600)
    job = Job("a page's job")
    with reports.asked_by_a_page():
        assert job.start(lambda j: {"rows": asked()})
    assert job._finished.wait(10)
    assert job.state == DONE and job.result == {"rows": []} and asked.worked == 1, job.error


# --- The reports' default views worked out before the server answers (D-204) -----------------------------------------


def _counting(monkeypatch) -> list[str]:
    """The settled answers worked out from now on (their work run, in any process), by name, in turn."""
    worked, real = [], Store.shared_answer

    def counted(self, name, compute, **kwargs):
        def work():
            worked.append(name)
            return compute()

        return real(self, name, work, **kwargs)

    monkeypatch.setattr(Store, "shared_answer", counted)
    return worked


def test_the_servers_start_works_out_exactly_the_default_views_with_none_kept_before_it_answers(
    tmp_path, ground_truth, clock, monkeypatch, capsys
):
    """D-204: at 300,000 invoices the reports asked before the background process's warm-up reached them (D-200, about
    a minute) were worked out in their request or said "being worked out" (Month-end's first build 12 to 50 s). The
    company server's start works out each default view with no answer kept for its question before it answers, kept
    for every process (the Vendors page's totals too: B-2050), each once, and says so in its window; then a web worker
    opening each page works none out. Started again it works none out: one kept, of any age, is the refresher's to
    bring up to date; only one with none kept for its question (none at all, an older AP Coder's) is."""
    from ap_coder.web import server
    from ap_coder.web.pages import insights as insights_page
    from tests.webclient import client_for

    monkeypatch.setattr(insights_page, "MIN_APPROVALS", 1)  # (Insights' operations shown too)
    store = Store(tmp_path / "ap.db")
    for n in range(3):
        _approved(store, tmp_path, ground_truth, f"A-{n}")
    _invoice(store, tmp_path, ground_truth, "Q-1")
    worked, process = _counting(monkeypatch), _Processes(monkeypatch)
    process("the server, before it answers")
    server._prepare_reports(str(store.path), refresh.PREPARE_MOST)  # (its own process's work, here: its own Store)
    said = capsys.readouterr().err.splitlines()
    every = len(refresh.DEFAULTS)
    assert sorted(worked) == sorted(name for name, _ in refresh.DEFAULTS)  # (each default view once, nothing more)
    assert len(said) == 2 and said[0].startswith("Working out the reports' default views before the dashboard answers")
    assert re.fullmatch(rf"The reports' default views worked out before the dashboard answers: {every} of the {every} "
                        rf"with none kept \({every} in all\), in [\d.]+ s\.", said[1]), said  # fmt: skip
    assert _wants(store) == []  # (nobody asked)

    process("a web worker")
    monkeypatch.setattr(store, "background_answers", True)
    worked.clear()
    client = client_for(store)
    for page in ("/review", "/month-end", "/spend", "/sales-tax", "/insights", "/vendors", "/learning"):
        assert client.get(page).status_code == 200, page
    assert not set(worked) & set(refresh.ANSWERS), worked

    process("the server started again")
    clock[0] += 2 * reports.STALE_MOST  # (an hour later; nothing changed: each kept still holds)
    _approved(store, tmp_path, ground_truth, "A-9")  # (and now something changed: each kept is old, not missing)
    worked.clear()
    again = Store(store.path)
    again.background_answers = True
    assert refresh.prepare(again, said.append) == (0, 0, pytest.approx(0, abs=5)) and worked == []
    assert said[-1].startswith(f"The reports' default views worked out before the dashboard answers: 0 of the 0 with "
                               f"none kept ({every} in all), in ")  # fmt: skip

    with sqlite3.connect(store.path) as conn:  # (none kept for one; an older AP Coder's kept for another)
        conn.execute("DELETE FROM report_answers WHERE name = 'sales_tax'")
        conn.execute("UPDATE report_answers SET version = '0.1.0/' || substr(version, instr(version, '/') + 1) "
                     "WHERE name = 'learning_metrics'")  # fmt: skip
    process("the server updated")
    made, due, _ = refresh.prepare(again, said.append)
    assert (made, due) == (2, 2) and sorted(worked) == ["learning_metrics", "sales_tax"]


def test_the_servers_start_stops_working_the_views_out_at_its_bound_and_goes_on_past_a_failure(
    tmp_path, clock, monkeypatch, caplog
):
    """D-204: the start's work is bounded (``PREPARE_MOST``, looked at before each view): past it the server answers
    and the rest is left to the background process's refresher, said so. A view whose work fails, or whose question
    can't be found, is logged and the next goes on."""
    store = _server(Store(tmp_path / "ap.db"), monkeypatch)
    order = []

    def answer(name: str, seconds: float = 0.0, fails: bool = False):
        def run(store, args):
            def work():
                order.append(name)
                time.sleep(seconds)
                if fails:
                    raise ValueError("no such page")
                return [name]

            return store.shared_answer(name, work, version="t-1", settle=30, asked=args, again=args)

        return run

    def no_question(store):
        raise sqlite3.OperationalError("database is locked")

    for name, work in (
        ("fails", answer("fails", fails=True)),
        ("unasked", answer("unasked")),
        ("slow", answer("slow", 0.3)),
        ("next", answer("next", 0.3)),
        ("left", answer("left")),
    ):
        monkeypatch.setitem(refresh.ANSWERS, name, work)
    monkeypatch.setattr(refresh, "DEFAULTS", (("fails", lambda s: [1]), ("unasked", no_question),
                                              ("slow", lambda s: [2]), ("next", lambda s: [3]),
                                              ("left", lambda s: [4]), ("left", lambda s: [5])))  # fmt: skip
    said = []
    with caplog.at_level(logging.WARNING, logger="ap_coder.web.refresh"):
        made, due, seconds = refresh.prepare(store, said.append, most=0.4)
    assert order == ["fails", "slow", "next"] and (made, due) == (2, 3) and 0.6 <= seconds < 5
    warned = [r.getMessage() for r in caplog.records]
    assert warned == ["the fails answer's default view was not worked out ahead of its page",
                      "the unasked answer's default view: whether one is kept was not found"]  # fmt: skip
    assert said[-1].endswith("; past 0.4 s, 2 left to the background process.")
    assert store.answer_kept("slow", reports.asked_key([2]), "t-1") and not store.answer_kept(
        "left", reports.asked_key([4]), "t-1")  # fmt: skip
