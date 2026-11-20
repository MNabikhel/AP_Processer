"""D-267: the chart asked in the database (``core/store/chart_index.IndexedPairs``) answers every question exactly as
the chart held in memory (``core/chart.AccountPairs``) does, never holding it whole; the Account box searches it."""

import sqlite3
import threading
from contextlib import contextmanager

import pytest

from ap_coder.core import chart
from ap_coder.core.chart import AccountPairs, ChartSettings
from ap_coder.core.store import Store
from ap_coder.core.store.chart_index import IndexedPairs
from tests import chart_files as cf
from tests.core.test_chart import _chart, _units
from tests.core.test_chart_store import _loaded

SETTINGS = (ChartSettings(), ChartSettings(never_offered=("*.7701", "0731066150.*.*"), postable_codes=("", "M")),
            ChartSettings(skip_words=("annex",), never_offered=("5410",)),
            # The companies in the pilot, and report 9 (D-272): the made-up chart's company 07310 in it, or not.
            ChartSettings(pilot=("07310",)), ChartSettings(pilot=("00999",)), ChartSettings(companies=("00999",)),
            ChartSettings(companies=("07310", "00999")))  # fmt: skip


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("index") / "ap.db")
    store.import_business_units(_units().rows)
    store.import_chart(_chart().rows)
    return store


@pytest.mark.parametrize("settings", SETTINGS)
def test_the_chart_asked_in_the_database_answers_as_the_chart_in_memory(loaded, settings):
    memory = AccountPairs(_chart().rows, _units().rows, settings)
    indexed = IndexedPairs(loaded, settings)
    units = sorted({r.bu for r in _chart().rows} | {u.code for u in _units().rows} | {"nowhere", " 0731066120"})
    codes = sorted({r.code for r in _chart().rows if r.object} | {"9999", "5410.9999", ""})
    for bu in units:
        for code in codes:
            for ask in ("status", "why_reserved", "offered", "description", "posting_edit"):
                assert getattr(indexed, ask)(bu, code) == getattr(memory, ask)(bu, code), (ask, bu, code)
        for ask in ("accounts_of", "has_business_unit", "has_accounts", "name_of", "company_of"):
            assert getattr(indexed, ask)(bu) == getattr(memory, ask)(bu), (ask, bu)
    for code in codes:
        assert indexed.bus_of(code) == memory.bus_of(code) and indexed.has_code(code) == memory.has_code(code), code
    assert indexed.offered_codes() == memory.offered_codes()
    assert indexed.business_units() == memory.business_units()
    assert (indexed.closed_units(), indexed.skipped_units()) == (memory.closed_units(), memory.skipped_units())
    assert indexed.unlisted_units(5) == memory.unlisted_units(5) == [cf.UNLISTED]
    assert (len(indexed), indexed.tally()) == (len(memory), memory.tally())
    assert indexed.units_of("07310") == memory.units_of("07310")


def test_the_store_asks_the_database_and_the_account_box_searches_it(loaded):
    pairs = loaded.account_pairs()
    assert isinstance(pairs, IndexedPairs)
    found = pairs.search("5410")
    assert found and all(row[0].split(".")[1] == "5410" for row in found)
    assert [row[0] for row in pairs.search("coffee")] == [f"{cf.HOME}.5410.2210"]
    assert pairs.search(f"{cf.HOME} stationery") == [[f"{cf.HOME}.5410", "Stationery and Paper",
                                                       "Head Office General"]]  # fmt: skip
    assert pairs.search("") == [] and pairs.search(f"{cf.CLOSED} 5410") == []  # (a closed unit's: never offered)
    assert pairs.search("5410", units=frozenset({cf.OTHER})) == [[f"{cf.OTHER}.5410", "Stationery and Paper",
                                                                   pairs.name_of(cf.OTHER)]]  # fmt: skip


@pytest.mark.parametrize("settings", SETTINGS)
def test_the_two_lists_are_worked_out_by_sqlite_as_from_the_rows(loaded, settings):
    from ap_coder.core import chart
    from ap_coder.core.store.chart import _lists

    with loaded._conn() as conn:
        assert _lists(conn, settings) == chart.account_lists(_chart().rows, _units().rows, settings)


def test_a_search_finds_the_accounts_offered_past_a_thousand_that_are_not(tmp_path):
    """B-2652: the Account box's search read the first 1,000 accounts matching the words (50 x 20) and kept those
    offered: a group's chart is half accounts AP does not post to (titles, budget, inactive), so a word they share
    found nothing offered past them. What may be posted to is asked of SQLite with the words."""
    from ap_coder.core.chart import ChartRow

    store = Store(tmp_path / "ap.db")
    titles = [ChartRow(f"1{n:05d}", "5110", "", "Widget costs", 5, "N") for n in range(1100)]
    store.import_chart([*titles, ChartRow("200000", "5110", "", "Widget costs", 6, "")])
    assert [row[0] for row in store.account_pairs().search("widget")] == ["200000.5110"]


HELD = {  # 1,100 jobs whose account 5110.9 is held back, each way (their units, the setting), then one open job
    "closed": ({"status": "N"}, {}),
    "named to be skipped": ({"name": "Job DO NOT USE"}, {"skip_words": ("do not use",)}),
    "never offered": ({}, {"never_offered": ("1*.5110.*",)}),
    "a control object": ({}, {"control": (("5110.9", "trade payable"),)}),
    "not in the pilot": ({"company": "00100"}, {"pilot": ("00200",)}),
    "not in report 9": ({"company": "00100"}, {"companies": ("00200",)}),
    "not in the business units report": ({"listed": False}, {}),
}


@pytest.mark.parametrize("held", HELD)
def test_a_search_finds_the_open_accounts_past_a_thousand_held_back(tmp_path, held):
    """B-2741: past B-2652, the search still read the first 1,000 accounts that may be posted to and dropped those of
    a closed business unit, one named to be skipped, one not in the business units report, a company held back or an
    account never offered: old jobs (low numbers) closed, a cost code or a word found nothing on the open jobs. Every
    rule is asked of SQLite with the words."""
    from ap_coder.core.chart import BuRow, ChartRow

    unit, settings = HELD[held]
    rows = []
    units = [BuRow("200000", "Job open Calgary", "00200", "JB", "")]
    for n in range(1100):
        bu = f"1{n:05d}"
        rows += [ChartRow(bu, "", "", unit.get("name", f"Job {n} Calgary"), 3, "", "", "00100"),
                 ChartRow(bu, "5110", "9", "Widget labour", 8, "", "", unit.get("company", "00200"))]  # fmt: skip
        if unit.get("listed", True):
            units.append(BuRow(bu, unit.get("name", f"Job {n} Calgary"), unit.get("company", "00200"), "JB",
                               unit.get("status", "")))  # fmt: skip
    rows += [ChartRow("200000", "", "", "Job open Calgary", 3, "", "", "00200"),
             ChartRow("200000", "5110", "", "Widget labour", 7, "", "", "00200")]  # fmt: skip
    store = Store(tmp_path / "ap.db")
    store.import_business_units(units)
    store.import_chart(rows)
    pairs = IndexedPairs(store, ChartSettings(**settings))
    assert pairs.status("200000", "5110") == chart.OK and pairs.status("100005", "5110.9") != chart.OK
    for typed in ("widget", "5110", "labour", "calgary", "5110 calgary"):
        assert [row[0] for row in pairs.search(typed)] == ["200000.5110"], typed
    assert [row[0] for row in pairs.search("5110", units=frozenset({"100003", "200000"}))] == ["200000.5110"]


def test_a_search_by_a_business_units_name_finds_one_companys_past_500_of_the_group(tmp_path):
    """B-2742: a word of a business unit's name was looked for in the first 500 business units of the whole group
    named so, before one company's or the pilot's were kept: a common word ("yard") found none of a company whose
    business units sort after the others'."""
    from ap_coder.core.chart import BuRow, ChartRow

    units = [BuRow(f"{100000 + n}", f"Calgary yard {n}", "00100", "JB", "") for n in range(600)]
    units += [BuRow(f"{200000 + n}", f"Calgary yard {n}", "00200", "JB", "") for n in range(30)]
    store = Store(tmp_path / "ap.db")
    store.import_business_units(units)
    store.import_chart([ChartRow(u.code, "5100", "", "Fuel", 7, "", "", u.company) for u in units])
    pairs = store.account_pairs()
    for typed in ("calgary", "yard", "calgary 5100"):
        assert len(pairs.search(typed, pairs.units_of("00200"))) == 30, typed
        assert len(IndexedPairs(store, ChartSettings(pilot=("00200",))).search(typed)) == 30, typed


def _opened(monkeypatch) -> list[sqlite3.Connection]:
    """Every connection a Store opens from now on."""
    made: list[sqlite3.Connection] = []
    real = Store._open

    def recorded(self, *args, **kwargs):
        made.append(real(self, *args, **kwargs))
        return made[-1]

    monkeypatch.setattr(Store, "_open", recorded)
    return made


def _closed(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError as exc:
        return "closed" in str(exc)  # (not "created in a thread": one of another thread's, still open)
    return False


@contextmanager
def _asked_on_another_thread(pairs):
    """``pairs`` asked on a thread of its own, which stays alive (as a web worker's does) until the block ends."""
    asked, done = threading.Event(), threading.Event()

    def ask() -> None:
        len(pairs)  # (asked of the database every time)
        asked.set()
        done.wait(10)

    worker = threading.Thread(target=ask)
    worker.start()
    assert asked.wait(10)
    try:
        yield
    finally:
        done.set()
        worker.join()


def test_a_closed_chart_holds_no_connection_open_on_any_thread(loaded, monkeypatch):
    """Windows refuses to remove or replace a database file a connection holds (the benchmark's temporary folder:
    WinError 32): ``close`` closes the connection each thread opened, one asking now once its question is answered,
    and a question asked after opens one again."""
    made = _opened(monkeypatch)
    pairs = IndexedPairs(loaded)
    assert pairs.status(cf.HOME, "5410") == chart.OK
    with _asked_on_another_thread(pairs):
        assert len(made) == 2 and not any(_closed(c) for c in made)
        pairs.close()
        assert all(_closed(c) for c in made)
    assert len(pairs) > 0 and len(made) == 3  # (opened again)
    with pairs._reader() as conn:
        pairs.close()
        assert conn.execute("SELECT 1").fetchone()[0] == 1  # (the question under way is answered)
    assert _closed(conn) and all(_closed(c) for c in made)


def test_reference_data_built_again_closes_the_old_charts_connections(store, monkeypatch):
    _loaded(store)
    made = _opened(monkeypatch)
    pairs = store.reference_data().pairs
    assert pairs.status(cf.HOME, "5410.7701") == chart.OK
    with _asked_on_another_thread(pairs):
        asked = [c for c in made if not _closed(c)]
        assert len(asked) == 2
        store.set_setting("never_offered", "*.7701")
        again = store.reference_data().pairs
        assert again is not pairs and again.status(cf.HOME, "5410.7701") == chart.RESERVED
        assert all(_closed(c) for c in asked)


def test_a_restore_closes_the_charts_connections_asked_on_two_threads(store, monkeypatch):
    _loaded(store)
    before = store.account_pairs().status(cf.UNLISTED, "5470")
    backup = store.backup_now("manual")
    store.import_chart(_chart([r for r in cf.CHART if r[0] != cf.UNLISTED]).rows)
    made = _opened(monkeypatch)
    pairs = store.account_pairs()
    assert pairs.status(cf.UNLISTED, "5470") == chart.NOT_IN_CHART
    with _asked_on_another_thread(pairs):
        asked = [c for c in made if not _closed(c)]
        assert len(asked) == 2
        store.restore_from(backup)
        assert all(_closed(c) for c in asked)
    assert store.account_pairs().status(cf.UNLISTED, "5470") == before != chart.NOT_IN_CHART
