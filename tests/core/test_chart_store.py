"""The chart of accounts and the business units kept by the store (D-209): the exact rows, the GL accounts and cost
centers worked out from them, an import again, a smaller chart, and the pairs built once. The chart is made up
(``tests/chart_files.py``)."""

from __future__ import annotations

import dataclasses

import pytest

from ap_coder.core import chart, schema
from ap_coder.core.chart import BU_INACTIVE, OK, RESERVED, ChartSettings
from ap_coder.core.store import load_sample_setup
from ap_coder.core.store.chart import NO_ACCOUNT
from ap_coder.web import invoice_grid
from tests import chart_files as cf
from tests.chart_files import CLOSED, HOME, NOT_USED, OTHER, UNLISTED


def _chart(rows=cf.CHART):
    return chart.chart_rows(dict(zip(cf.CHART_HEADERS, row, strict=True)) for row in cf.chart_cells(rows)).rows


def _units(rows=cf.UNITS):
    return chart.bu_rows(dict(zip(cf.BU_HEADERS, row, strict=True)) for row in cf.bu_cells(rows)).rows


def _loaded(store):
    store.import_business_units(_units(), actor="pat")
    return store.import_chart(_chart(), actor="pat")


def _gl(store):
    return {r["code"]: (r["description"], r["category"]) for r in store.list_accounts("gl_accounts")}


def _cc(store):
    return {r["code"]: r["description"] for r in store.list_accounts("cost_centers")}


def test_a_database_with_no_chart_has_the_tables_empty_and_no_pairs(store):
    assert store.chart_accounts() == [] and store.business_units() == []
    assert store.account_pairs() is None and store.chart_version() == 0
    assert store.chart_settings() == ChartSettings()
    load_sample_setup(store)
    assert store.reference_data().pairs is None and store.account_pairs() is None


def test_the_import_keeps_the_exact_rows_and_works_out_the_two_lists(store):
    units = store.import_business_units(_units(), actor="pat")
    nothing = {"left": {"gl_accounts": [], "cost_centers": []}, "unmapped": [],
               "set_aside": {"meals": [], "categories": [], "managers": []},
               "brought_back": {"meals": [], "categories": [], "managers": []}}  # fmt: skip
    assert units == {"added": 9, "updated": 0, "removed": 0, "rows": 9, **nothing,
                     "gl_accounts": {"now": 0, "added": 0, "updated": 0, "removed": 0},
                     "cost_centers": {"now": 0, "added": 0, "updated": 0, "removed": 0}}  # fmt: skip
    assert store.account_pairs() is None  # the business units alone are no chart
    result = store.import_chart(_chart(), actor="pat")
    assert result == {"added": 41, "updated": 0, "removed": 0, "rows": 41, **nothing,
                      "gl_accounts": {"now": 13, "added": 13, "updated": 0, "removed": 0},
                      "cost_centers": {"now": 8, "added": 8, "updated": 0, "removed": 0}}  # fmt: skip
    # Both tables hold every row as exported: the headers, the titles and the accounts not for posting too.
    assert store.chart_accounts() == sorted(_chart(), key=lambda r: (r.bu, r.object, r.subsidiary))
    assert len(store.chart_accounts()) == len(cf.CHART)
    assert [(u.code, u.name, u.company, u.type, u.status) for u in store.business_units()] == sorted(cf.UNITS)
    assert _gl(store) == cf.GL_ACCOUNTS and _cc(store) == cf.COST_CENTERS
    assert not any(r["meals"] for r in store.list_accounts("gl_accounts"))
    assert store.chart_version() == 2
    events = store.events(actions=["accounts_imported"])
    assert [e["detail"]["table"] for e in events] == ["chart_accounts", "business_units"]
    assert {e["actor"] for e in events} == {"pat"}
    assert events[0]["detail"]["added"] == 41 and events[0]["detail"]["gl_accounts"]["now"] == 13


def test_importing_the_same_chart_again_changes_nothing(store):
    _loaded(store)
    before = (store.chart_version(), store.events_stamp(), store.checks_stamp(), store.list_accounts("gl_accounts"),
              store.list_accounts("cost_centers"), store.account_pairs())  # fmt: skip
    result = store.import_chart(_chart(), actor="pat")
    assert (result["added"], result["updated"], result["removed"], result["rows"]) == (0, 0, 0, 41)
    assert result["gl_accounts"] == {"now": 13, "added": 0, "updated": 0, "removed": 0}
    assert store.import_business_units(_units())["cost_centers"] == {"now": 8, "added": 0, "updated": 0, "removed": 0}
    after = (store.chart_version(), store.events_stamp(), store.checks_stamp(), store.list_accounts("gl_accounts"),
             store.list_accounts("cost_centers"), store.account_pairs())  # fmt: skip
    assert after[:5] == before[:5] and after[5] is before[5]


def test_a_smaller_chart_removes_what_is_gone_and_keeps_ticks_categories_and_managers(store):
    _loaded(store)
    store.import_accounts("gl_accounts", [{"code": "5410.2210", "meals": "yes"}], "code", None, meals_column="meals")
    store.import_accounts("gl_accounts", [{"code": "5410", "kind": "Typed by AP"}], "code", None, "kind")
    store.import_accounts("cost_centers", [{"code": HOME, "kind": "Overhead"}], "code", None, "kind")
    assert store.set_cost_center_manager([HOME, OTHER], "Dana Reyes", "dana@example.test", "pat") == 2
    smaller = [r for r in cf.CHART if r[0] not in (OTHER, "05504218") and (r[0], r[1]) != (HOME, "5710")]
    result = store.import_chart(_chart(smaller), actor="pat")
    assert (result["added"], result["updated"], result["removed"]) == (0, 0, len(cf.CHART) - len(smaller))
    assert result["gl_accounts"] == {"now": 10, "added": 0, "updated": 0, "removed": 3}
    assert result["cost_centers"] == {"now": 6, "added": 0, "updated": 0, "removed": 2}
    assert store.chart_accounts() == sorted(_chart(smaller), key=lambda r: (r.bu, r.object, r.subsidiary))
    gone = {"5450", "5710", "5710.3120"}
    assert set(_gl(store)) == set(cf.GL_ACCOUNTS) - gone
    assert set(_cc(store)) == set(cf.COST_CENTERS) - {OTHER, "05504218"}
    accounts = {r["code"]: r for r in store.list_accounts("gl_accounts")}
    assert accounts["5410.2210"]["meals"] is True and accounts["5410"]["category"] == "Typed by AP"
    assert accounts["5410.2210"]["category"] == "Office and General Costs"
    assert store.cost_center_managers() == {HOME: {"name": "Dana Reyes", "email": "dana@example.test"}}
    assert {r["code"]: r["category"] for r in store.list_accounts("cost_centers")}[HOME] == "Overhead"
    pairs = store.account_pairs()
    assert pairs.bus_of("5450") == () and pairs.accounts_of("05504218") == []
    assert pairs.status(OTHER, "5450") == chart.NOT_IN_CHART
    assert pairs.has_business_unit(OTHER)  # still in the business unit master, with no account


def test_a_category_that_came_from_the_chart_follows_the_chart_and_a_typed_one_stays(store):
    _loaded(store)
    store.import_accounts("gl_accounts", [{"code": "5410", "kind": "Typed by AP"}], "code", None, "kind")
    renamed = [(bu, obj, sub, "Office Costs" if obj == "5400" else "Coffee" if sub == "2210" else text, lod, edit)
               for bu, obj, sub, text, lod, edit in cf.CHART]  # fmt: skip
    result = store.import_chart(_chart(renamed))
    assert result["gl_accounts"] == {"now": 13, "added": 0, "updated": 3, "removed": 0}
    assert _gl(store)["5410.2210"] == ("Coffee", "Office Costs") and _gl(store)["5450"][1] == "Office Costs"
    assert _gl(store)["5410"] == ("Stationery and Paper", "Typed by AP")
    assert _gl(store)["5710"] == cf.GL_ACCOUNTS["5710"]


def test_the_first_import_replaces_a_list_made_by_hand(store):
    load_sample_setup(store)
    hand_made = {r["code"]: r for r in store.list_accounts("gl_accounts")}
    assert "6010" in hand_made and hand_made["2310"]["category"] == "Sales Tax"
    store.import_business_units(_units())
    assert {r["code"] for r in store.list_accounts("gl_accounts")} == set(hand_made)  # no chart yet: as they were
    result = store.import_chart(_chart())
    assert result["gl_accounts"] == {"now": 13, "added": 12, "updated": 1, "removed": len(hand_made) - 1}
    assert set(_gl(store)) == set(cf.GL_ACCOUNTS) and set(_cc(store)) == set(cf.COST_CENTERS)
    assert _gl(store)["2310"] == ("GST Paid on Purchases", "Sales Tax")  # the chart's name, the category typed in


def test_a_business_unit_closed_since_leaves_the_lists(store):
    _loaded(store)
    assert store.account_pairs().status(OTHER, "5450") == OK
    closed = [(code, name, co, kind, "INA" if code == OTHER else status) for code, name, co, kind, status in cf.UNITS]
    result = store.import_business_units(_units(closed), actor="pat")
    assert (result["added"], result["updated"], result["removed"]) == (0, 1, 0)
    assert result["gl_accounts"]["removed"] == 1 and result["cost_centers"]["removed"] == 1
    assert "5450" not in _gl(store) and OTHER not in _cc(store) and "5410" in _gl(store)
    pairs = store.account_pairs()
    assert pairs.status(OTHER, "5450") == pairs.status(CLOSED, "5410") == BU_INACTIVE
    assert pairs.status(UNLISTED, "5470") == OK and pairs.status(NOT_USED, "5410") == RESERVED


def test_a_file_that_would_leave_no_account_is_refused_and_nothing_changes(store):
    with pytest.raises(ValueError, match="No row of this file has an object account"):
        store.import_chart([chart.ChartRow(HOME, "", "", "Head Office General", 3, "N")])
    with pytest.raises(ValueError, match="no business unit"):
        store.import_business_units([])
    _loaded(store)
    before = (store.chart_accounts(), store.business_units(), _gl(store), _cc(store), store.chart_version())
    with pytest.raises(ValueError) as refused:
        store.import_chart([chart.ChartRow(HOME, "5400", "", "Office and General Costs", 5, "N")])
    assert str(refused.value) == NO_ACCOUNT and "nothing was changed" in NO_ACCOUNT
    every_one_closed = [(code, name, co, kind, "INA") for code, name, co, kind, _ in cf.UNITS]
    with pytest.raises(ValueError, match="nothing was changed"):
        store.import_business_units(_units([*every_one_closed, (UNLISTED, "Facilities", "07310", "AD", "INA")]))
    assert (store.chart_accounts(), store.business_units(), _gl(store), _cc(store), store.chart_version()) == before


def test_the_pairs_are_built_once_for_a_chart_and_its_settings(store):
    _loaded(store)
    pairs = store.account_pairs()
    assert pairs is store.account_pairs() is store.reference_data().pairs
    assert len(pairs) == 32 and pairs.status(HOME, "5410.7701") == OK
    store.set_setting("never_offered", "*.7701")  # a plain setting: the pairs follow it
    assert store.chart_settings().never_offered == ("*.7701",)
    again = store.account_pairs()
    assert again is not pairs and again.status(HOME, "5410.7701") == RESERVED and again is store.account_pairs()
    stamp = store.checks_stamp()
    changed = store.save_chart_settings(dataclasses.replace(store.chart_settings(), home_business_unit=HOME,
                                                            postable_codes=("", "M")), actor="pat")  # fmt: skip
    assert changed == ["postable_codes", "home_business_unit"]
    assert store.events(actions=["settings_changed"])[0]["detail"] == {"keys": changed}
    assert store.save_chart_settings(store.chart_settings()) == []
    assert store.account_pairs().settings.home_business_unit == HOME
    assert store.account_pairs().status("0550421", "5620") == OK and store.checks_stamp() != stamp
    store.import_chart(_chart([r for r in cf.CHART if r[0] != UNLISTED]))
    assert store.account_pairs().status(UNLISTED, "5470") == chart.NOT_IN_CHART  # a new chart: built again


def test_the_accounts_a_line_may_be_offered_are_said_in_one_place(store, reference):
    # With no chart: every account but the tax accounts, as before.
    tax_gls = reference.tax.tax_gl_codes()
    assert tax_gls and reference.pairs is None
    assert reference.line_accounts() == [c for c in reference.chart_of_accounts.codes if c not in tax_gls]
    assert schema.line_gl_codes(reference) == reference.line_accounts()
    assert [c for c, _ in invoice_grid.gl_options(reference, [])][1:] == reference.line_accounts()
    # With one: nor the accounts never offered, on any business unit.
    _loaded(store)
    store.set_tax_treatment("GST", "recoverable", "2310")
    store.set_setting("never_offered", "*.7701, 2140")
    with_chart = store.reference_data()
    offered = sorted(set(cf.GL_ACCOUNTS) - {"2310", "5410.7701", "2140", "2140.QC"})
    assert with_chart.line_accounts() == schema.line_gl_codes(with_chart) == offered
    assert "5410.7701" in with_chart.chart_of_accounts.codes  # still an account: a line coded to it by hand is checked
    options = invoice_grid.gl_options(with_chart, [{"predicted_gl_code": "5410.7701"}])
    assert [c for c, _ in options][1:] == [*offered, "5410.7701"]  # a line that has it still shows it, by its name
    assert dict(options)["5410.7701"] == "5410.7701 · Recoveries"
    assert "5410.7701" not in with_chart.to_prompt_context() and "5410.2210" in with_chart.to_prompt_context()


def test_a_business_unit_named_do_not_use_is_not_in_the_lists_offered(store, reference):
    """B-2093: it was in the cost center drop-down and in the model's list of cost centers, though the chart's pairs
    never offer it. It stays a cost center (a line coded to it by hand is checked), like a never-offered account."""
    assert reference.line_cost_centers() == reference.cost_centers.codes  # no chart: every cost center, as before
    assert dataclasses.replace(reference, cost_centers=None).line_cost_centers() is None
    _loaded(store)
    with_chart = store.reference_data()
    offered = sorted(set(cf.COST_CENTERS) - {NOT_USED})
    assert NOT_USED in with_chart.cost_centers.codes and with_chart.line_cost_centers() == offered
    assert with_chart.line_cost_centers() == with_chart.pairs.business_units()
    line = schema.build_json_schema(with_chart)["properties"]["line_items"]["items"]["properties"]
    assert line["predicted_cost_center"]["enum"] == [*offered, "UNASSIGNED"]
    assert [c for c, _ in invoice_grid.cc_options(with_chart, [])][1:] == offered
    kept = invoice_grid.cc_options(with_chart, [{"predicted_cost_center": NOT_USED}])
    assert kept[-1] == (NOT_USED, f"{NOT_USED} · ****DO NOT USE**** Old Annex")  # a line that has it still shows it
    assert NOT_USED not in with_chart.to_prompt_context() and HOME in with_chart.to_prompt_context()
    # A business unit all of whose accounts are never offered is not offered either.
    store.set_setting("never_offered", "0731066177.*.*")
    assert UNLISTED not in store.reference_data().line_cost_centers()
