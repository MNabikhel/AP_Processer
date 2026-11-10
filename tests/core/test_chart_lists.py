"""The GL accounts and cost centers are what the chart, the business units and the chart's settings give, whenever
one of the three changes (B-2091); what a person typed on a code that leaves them is set aside, never lost (B-2090);
the pairs kept are those of the chart in the database (B-2125). The chart is made up (``tests/chart_files.py``)."""

from __future__ import annotations

import dataclasses
import sqlite3
from contextlib import closing

import pytest

from ap_coder.core import audit, chart
from ap_coder.core.chart import NOT_IN_CHART, OK
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.core.store import chart as store_chart
from tests import chart_files as cf
from tests.chart_files import OTHER


def _chart(rows=cf.CHART):
    return chart.chart_rows(dict(zip(cf.CHART_HEADERS, row, strict=True)) for row in cf.chart_cells(rows)).rows


def _units(rows=cf.UNITS):
    return chart.bu_rows(dict(zip(cf.BU_HEADERS, row, strict=True)) for row in cf.bu_cells(rows)).rows


def _loaded(store):
    store.import_business_units(_units(), actor="pat")
    return store.import_chart(_chart(), actor="pat")


def _gl(store):
    return {r["code"]: r for r in store.list_accounts("gl_accounts")}


def _cc(store):
    return {r["code"]: r for r in store.list_accounts("cost_centers")}


WITHOUT_OTHER = [r for r in cf.CHART if r[0] != OTHER]


def _typed(store):
    """What a person set up by hand on the account and the business unit only ``OTHER`` has."""
    store.import_accounts("gl_accounts", [{"code": "5450", "meals": "yes", "kind": "People"}], "code", None, "kind",
                          meals_column="meals")  # fmt: skip
    store.import_accounts("cost_centers", [{"code": OTHER, "kind": "Overhead"}], "code", None, "kind")
    store.set_cost_center_manager([OTHER], "Dana Reyes", "dana@example.test", "pat")


def test_a_business_unit_missing_from_one_import_keeps_its_manager_and_meals_tick_when_it_comes_back(store):
    _loaded(store)
    _typed(store)
    smaller = store.import_chart(_chart(WITHOUT_OTHER), actor="pat")
    assert "5450" not in _gl(store) and OTHER not in _cc(store) and store.cost_center_managers() == {}
    assert smaller["left"] == {"gl_accounts": ["5450"], "cost_centers": [OTHER]}
    assert smaller["set_aside"] == {"meals": ["5450"], "categories": ["5450", OTHER], "managers": [OTHER]}
    assert store.chart_set_aside() == {
        "gl_accounts": {"5450": {"meals": 1, "category": "People"}},
        "cost_centers": {OTHER: {"category": "Overhead", "manager_name": "Dana Reyes",
                                 "manager_email": "dana@example.test"}},
    }  # fmt: skip
    event = store.events(actions=["accounts_imported"])[0]
    assert event["detail"]["left"] == smaller["left"] and event["detail"]["set_aside"] == smaller["set_aside"]
    assert audit.describe(event) == (
        "Chart of accounts: 37 rows (0 added, 0 changed, 4 removed); GL accounts: 1 left the list; cost centers: 1 "
        "left the list; set aside: 1 meals tick, 2 categories, 1 manager"
    )
    full = store.import_chart(_chart(), actor="pat")
    assert full["brought_back"] == smaller["set_aside"] and full["left"] == {"gl_accounts": [], "cost_centers": []}
    back = _gl(store)["5450"]
    assert (back["meals"], back["category"], back["description"]) == (True, "People", "Training and Seminars")
    assert _cc(store)[OTHER]["category"] == "Overhead"
    assert store.cost_center_managers() == {OTHER: {"name": "Dana Reyes", "email": "dana@example.test"}}
    assert store.chart_set_aside() == {"gl_accounts": {}, "cost_centers": {}}
    assert "put back: 1 meals tick, 2 categories, 1 manager" in audit.describe(
        store.events(actions=["accounts_imported"])[0]
    )


def test_a_business_unit_closed_and_opened_again_keeps_its_manager(store):
    _loaded(store)
    _typed(store)
    closed = [(code, name, co, kind, "INA" if code == OTHER else status) for code, name, co, kind, status in cf.UNITS]
    result = store.import_business_units(_units(closed))
    assert result["set_aside"]["managers"] == [OTHER] and store.cost_center_managers() == {}
    assert store.import_business_units(_units())["brought_back"]["managers"] == [OTHER]
    assert store.cost_center_managers() == {OTHER: {"name": "Dana Reyes", "email": "dana@example.test"}}


def test_the_first_import_over_a_list_made_by_hand_sets_its_ticks_aside_and_says_what_the_tax_setup_lost(store):
    load_sample_setup(store)
    ticked = sorted(code for code, r in _gl(store).items() if r["meals"])
    assert ticked and store.tax_treatments()["QST"].gl_code == "2320"
    result = _loaded(store)
    assert result["set_aside"]["meals"] == ticked and "2320" in result["left"]["gl_accounts"]
    assert result["unmapped"] == ["the tax set-up posts QST to GL account 2320"]
    aside = store.chart_set_aside()["gl_accounts"]
    assert set(aside) >= set(ticked) and aside["2320"] == {"category": "Sales Tax"}  # (its category, typed then)


def test_the_same_chart_again_puts_back_a_gl_account_deleted_by_hand(store):
    """ "Import the chart of accounts again" is what the checks' help says to do: it must repair the lists."""
    _loaded(store)
    store.save_accounts("gl_accounts", [r for r in store.list_accounts("gl_accounts") if r["code"] != "5450"])
    store.import_accounts("cost_centers", [{"code": "0999999999", "description": "Typed by hand"}], "code",
                          "description")  # fmt: skip
    assert "5450" not in _gl(store) and store.account_pairs().status(OTHER, "5450") == OK
    version = store.chart_version()
    result = store.import_chart(_chart(), actor="pat")
    assert (result["added"], result["updated"], result["removed"]) == (0, 0, 0)
    assert result["gl_accounts"] == {"now": 13, "added": 1, "updated": 0, "removed": 0}
    assert result["cost_centers"] == {"now": 8, "added": 0, "updated": 0, "removed": 1}
    assert "5450" in _gl(store) and "0999999999" not in _cc(store) and store.chart_version() == version + 1
    assert "worked out again" in audit.describe(store.events(actions=["accounts_imported"])[0])
    # And when nothing is out of step, nothing is written or recorded.
    stamp = (store.chart_version(), store.events_stamp())
    assert store.import_chart(_chart())["gl_accounts"]["added"] == 0
    assert (store.chart_version(), store.events_stamp()) == stamp


def test_the_lists_are_rebuilt_from_the_chart_on_demand(store):
    with pytest.raises(ValueError, match="No chart of accounts is imported"):
        store.rebuild_account_lists()
    _loaded(store)
    assert store.rebuild_account_lists()["gl_accounts"] == {"now": 13, "added": 0, "updated": 0, "removed": 0}
    store.save_accounts("gl_accounts", [r for r in store.list_accounts("gl_accounts") if r["code"] != "5450"])
    result = store.rebuild_account_lists(actor="pat")
    assert result["gl_accounts"]["added"] == 1 and "5450" in _gl(store)
    assert store.events(actions=["accounts_imported"])[0]["actor"] == "pat"


def test_a_posting_edit_code_made_postable_lists_its_accounts_at_once(store):
    _loaded(store)
    assert "5620" not in _gl(store)
    store.set_setting("postable_codes", "M")  # a plain setting, as a page saves it
    assert "5620" in _gl(store) and store.account_pairs().status("0550421", "5620") == OK
    assert "5620" in store.reference_data().line_accounts()
    store.save_chart_settings(dataclasses.replace(store.chart_settings(), postable_codes=("",)), actor="pat")
    assert "5620" not in _gl(store) and "5620" not in store.reference_data().line_accounts()
    # A setting that would leave no account to code to is refused, nothing changed.
    blank_only = [r for r in cf.CHART if r[5] in ("N", "M")]
    other = Store(store.path.parent / "other.db")
    other.set_setting("postable_codes", "M")
    other.import_chart(_chart(blank_only))
    with pytest.raises(ValueError, match="nothing was changed"):
        other.set_setting("postable_codes", "")
    assert other.chart_settings().postable_codes == ("", "M") and "5620" in _gl(other)


def test_the_import_takes_the_write_lock_before_it_reads_what_it_compares_with(store, monkeypatch):
    _loaded(store)
    read_in_a_transaction = []
    rows = store_chart._chart

    def reading(conn):
        read_in_a_transaction.append(conn.in_transaction)
        return rows(conn)

    monkeypatch.setattr(store_chart, "_chart", reading)
    store.import_chart(_chart(WITHOUT_OTHER))
    store.import_business_units(_units(cf.UNITS[:-1]))
    assert read_in_a_transaction and all(read_in_a_transaction)


def test_two_charts_with_the_same_lists_never_share_kept_pairs(tmp_path):
    """A backup restored, then another export imported: the count of imports and both lists are the same, the pairs
    are not. The pairs kept must be those of the chart in the database."""
    first, second = Store(tmp_path / "a" / "ap_coder.db"), Store(tmp_path / "b" / "ap_coder.db")
    first.import_chart(_chart())
    second.import_chart(_chart([r for r in cf.CHART if (r[0], r[1]) != (OTHER, "5410")]))
    assert first.chart_version() == second.chart_version() == 1
    assert first.list_accounts("gl_accounts") == second.list_accounts("gl_accounts")
    assert first.reference_data().pairs.status(OTHER, "5410") == OK  # kept by this process
    with closing(sqlite3.connect(second.path)) as source, closing(sqlite3.connect(first.path)) as target:
        source.backup(target)  # the second database put in place of the first, as a restore does
    assert first.chart_version() == 1 and len(first.chart_accounts()) == len(cf.CHART) - 1
    assert first.reference_data().pairs.status(OTHER, "5410") == NOT_IN_CHART
    assert first.account_pairs().status(OTHER, "5410") == NOT_IN_CHART


def test_a_business_unit_and_a_supplier_are_retired_by_the_same_words_kept_in_one_place(store):
    """One default, one setting and one way to find the word in a name, for the chart and the supplier master."""
    from ap_coder.core.store import vendors as store_vendors

    assert (
        store_vendors.SKIP_WORDS_SETTING is chart.SKIP_WORDS_SETTING and chart.SKIP_WORDS_SETTING in chart.SETTING_KEYS
    )
    assert store_vendors._markers is chart.markers
    assert (
        store.skip_words() == list(store.chart_settings().skip_words) == list(chart.SKIP_WORDS) == ["DO NOT USE", "DNU"]
    )
    store.set_setting(chart.SKIP_WORDS_SETTING, "retired, DNU")
    assert store.skip_words() == list(store.chart_settings().skip_words) == ["retired", "DNU"]
    supplier = store_vendors.name_marker("Old Print Shop - RETIRED", store.skip_words())
    assert supplier == ("Old Print Shop -", "RETIRED")
    assert chart.skip_word("Old Annex (retired)", store.skip_words()) == "RETIRED"
    for name in ("DO-NOT-USE yard", "****DO NOT USE**** Old Annex", "do  not\tuse", "Yard (DNU)"):  # the one rule
        word = chart.skip_word(name)
        assert word and store_vendors.name_marker(name, list(chart.SKIP_WORDS))[1] == word, name
    for name in ("Adnure Supplies", "Do not disturb, use door", "DNUGGET"):
        assert chart.skip_word(name) == "" == store_vendors.name_marker(name, list(chart.SKIP_WORDS))[1], name
    _loaded(store)
    assert store.account_pairs().skipped_units() == {}  # "Old Annex" is not retired by these words
    store.set_setting(chart.SKIP_WORDS_SETTING, "")  # no word at all: no name retires a supplier or a business unit
    assert store.skip_words() == [] and store.chart_settings().skip_words == ()
