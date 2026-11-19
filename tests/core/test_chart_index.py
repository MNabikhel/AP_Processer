"""D-267: the chart asked in the database (``core/store/chart_index.IndexedPairs``) answers every question exactly as
the chart held in memory (``core/chart.AccountPairs``) does, never holding it whole; the Account box searches it."""

import pytest

from ap_coder.core.chart import AccountPairs, ChartSettings
from ap_coder.core.store import Store
from ap_coder.core.store.chart_index import IndexedPairs
from tests import chart_files as cf
from tests.core.test_chart import _chart, _units

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
