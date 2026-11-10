"""What a person changes by hand in what JD Edwards' files loaded (core/store/by_hand.py, D-221): recorded with its
before and after, and kept when a newer file is loaded: on the row while the file has it, set aside while it does
not, let go when the file says the same or the person types the file's value back. On the made-up company's exports
(``bench.jde_exports``)."""

from __future__ import annotations

import dataclasses

import pytest

from ap_coder.bench import jde_exports as made
from ap_coder.core import audit
from ap_coder.core.store import Store, by_hand
from ap_coder.ops import chart_cmd


@pytest.fixture(scope="module")
def exports(tmp_path_factory):
    folder = tmp_path_factory.mktemp("jde")
    made.write_all(folder, size="small")
    return folder


@pytest.fixture
def store(tmp_path, exports) -> Store:
    store = Store(tmp_path / "private" / "ap_coder.db")
    chart_cmd.import_files(store, exports / made.CHART_FILE, exports / made.UNITS_FILE)
    return store


def _row(store: Store, bu: str, obj: str, subsidiary: str = ""):
    return next(r for r in store.chart_accounts() if (r.bu, r.object, r.subsidiary) == (bu, obj, subsidiary))


def _not_posted(store: Store):
    """An account of the chart the file says may not be posted to, on a business unit that is active, whose GL code
    no line is coded to."""
    codes = {row["code"] for row in store.list_accounts("gl_accounts")}
    closed = set(store.account_pairs().closed_units())
    return next(r for r in store.chart_accounts()
                if r.object and r.posting_edit == "I" and r.code not in codes and r.bu not in closed)  # fmt: skip


def _last(store: Store) -> dict:
    return store.events(actions=[by_hand.EDITED_ACTION])[0]


def test_a_chart_account_changed_by_hand_is_recorded_and_kept_when_the_chart_is_loaded_again(store, exports):
    row = _not_posted(store)
    key = (row.bu, row.object, row.subsidiary)
    changed = store.edit_chart_account(*key, description="Payroll clearing  (salaries)", postable=True, actor="Pat")
    assert changed == {"description": {"from": row.description, "to": "Payroll clearing (salaries)"},
                       "posting_edit": {"from": "I", "to": ""}}  # fmt: skip
    assert dataclasses.astuple(_row(store, *key))[3:6] == ("Payroll clearing (salaries)", row.lod, "")
    assert row.code in {r["code"] for r in store.list_accounts("gl_accounts")}  # the lists follow at once
    event = _last(store)
    assert event["actor"] == "Pat" and event["detail"]["changes"] == changed
    said = audit.describe(event)
    assert f"Chart of accounts {row.bu}.{row.code}" in said and f"description {row.description} → Payroll" in said
    assert "posting edit code I → (blank)" in said
    kept = store.hand_edits()["chart_accounts"][by_hand.chart_key(*key)]
    assert kept["posting_edit"]["file"] == "I" and kept["description"]["by"] == "Pat"

    # The same export loaded again: the row keeps the change, nothing is counted as changed, and the load says so.
    version = store.chart_version()
    said = chart_cmd.import_files(store, exports / made.CHART_FILE, exports / made.UNITS_FILE)
    assert "Chart of accounts: the same as already imported." in said and store.chart_version() == version
    assert any(line.startswith("  Kept as changed by hand here") and f"{row.bu}.{row.code}" in line for line in said)
    assert _row(store, *key).posting_edit == "" and _row(store, *key).description == "Payroll clearing (salaries)"
    assert store.edit_chart_account(*key, description="Payroll clearing (salaries)", postable=True) == {}  # no change


def test_a_change_is_set_aside_while_the_file_lacks_the_row_and_let_go_when_the_file_agrees(store):
    row = _not_posted(store)
    key = (row.bu, row.object, row.subsidiary)
    store.edit_chart_account(*key, description="By hand", postable=True)
    rows = store.chart_accounts()
    without = [r for r in rows if (r.bu, r.object, r.subsidiary) != key]
    assert "by_hand" not in store.import_chart(without)  # the file no longer has the row: set aside, not lost
    assert by_hand.chart_key(*key) in store.hand_edits()["chart_accounts"]
    # A newer file has the row again, its description now what the person typed, its posting edit still "I".
    newer = [*without, dataclasses.replace(row, description="By hand")]
    result = store.import_chart(newer)
    assert result["by_hand"] == [f"{row.bu}.{row.code}"]
    assert _row(store, *key).posting_edit == "" and _row(store, *key).description == "By hand"
    kept = store.hand_edits()["chart_accounts"][by_hand.chart_key(*key)]
    assert set(kept) == {"posting_edit"}  # the description is the file's own now: nothing to keep
    event = store.events(actions=["accounts_imported"])[0]
    assert event["detail"]["by_hand"] == [f"{row.bu}.{row.code}"] and "kept as changed by hand" in audit.describe(event)
    # Unticked again: the file's own code is back, and the row follows the file from now on.
    assert store.edit_chart_account(*key, postable=False) == {"posting_edit": {"from": "", "to": "I"}}
    assert store.hand_edits()["chart_accounts"] == {}


def test_an_account_the_file_lets_be_posted_to_is_closed_by_hand_with_jd_edwards_inactive_code(store):
    row = next(r for r in store.chart_accounts() if r.object and r.posting_edit == "")
    key = (row.bu, row.object, row.subsidiary)
    assert store.edit_chart_account(*key, postable=False)["posting_edit"] == {"from": "", "to": by_hand.NOT_POSTED}
    assert store.account_pairs().status(row.bu, row.code) == "not_postable"
    header = next(r for r in store.chart_accounts() if r.is_header)
    assert store.edit_chart_account(header.bu, "", "", description="Head office (renamed)", postable=False,
                                    meals=True) == {"description": {"from": header.description,
                                                                    "to": "Head office (renamed)"}}  # fmt: skip
    with pytest.raises(ValueError, match="not in the chart of accounts any more"):
        store.edit_chart_account("NOPE", "1", "", description="x")


def test_the_meals_tick_is_the_gl_accounts_and_only_where_lines_are_coded(store, exports):
    listed = {r["code"] for r in store.list_accounts("gl_accounts")}
    row = next(r for r in store.chart_accounts() if r.code in listed and r.posting_edit == "")
    key = (row.bu, row.object, row.subsidiary)
    assert store.edit_chart_account(*key, meals=True, actor="Pat") == {"meals": {"from": False, "to": True}}
    assert next(r for r in store.list_accounts("gl_accounts") if r["code"] == row.code)["meals"]
    assert "meals account no → yes" in audit.describe(_last(store))
    chart_cmd.import_files(store, exports / made.CHART_FILE)  # a load keeps it, as before (B-2090)
    assert next(r for r in store.list_accounts("gl_accounts") if r["code"] == row.code)["meals"]
    assert store.edit_chart_account(*key, meals=False) == {"meals": {"from": True, "to": False}}
    other = _not_posted(store)
    before = store.chart_accounts()
    with pytest.raises(ValueError, match="meals tick is kept on the GL accounts"):
        store.edit_chart_account(other.bu, other.object, other.subsidiary, description="Changed", meals=True)
    assert store.chart_accounts() == before  # nothing changed, the description neither


def test_a_business_unit_changed_by_hand_follows_into_the_cost_centers_and_is_kept(store, exports):
    units = store.business_units()
    centers = {r["code"] for r in store.list_accounts("cost_centers")}
    unit = next(u for u in units if u.code in centers)
    changed = store.edit_business_unit(unit.code, name="Renamed by hand", status="ina", actor="Pat")
    assert changed == {"name": {"from": unit.name, "to": "Renamed by hand"}, "status": {"from": "ACT", "to": "INA"}}
    assert unit.code not in {r["code"] for r in store.list_accounts("cost_centers")}  # closed: no cost center
    assert f"Business units {unit.code}: name {unit.name} → Renamed by hand; status ACT → INA" == audit.describe(
        _last(store))  # fmt: skip
    said = chart_cmd.import_files(store, units_source=exports / made.UNITS_FILE)
    assert "Business units: the same as already imported." in said
    assert any("Kept as changed by hand here" in line and unit.code in line for line in said)
    kept = next(u for u in store.business_units() if u.code == unit.code)
    assert (kept.name, kept.status) == ("Renamed by hand", "INA")
    store.edit_business_unit(unit.code, name=unit.name, status="ACT")  # the file's values typed back: let go
    assert store.hand_edits()["business_units"] == {}
    assert unit.code in {r["code"] for r in store.list_accounts("cost_centers")}
    with pytest.raises(ValueError, match="not in the business unit master"):
        store.edit_business_unit("NOPE", name="x")


def test_a_change_that_would_leave_no_account_to_code_to_is_refused_whole(store):
    before = store.business_units()
    with pytest.raises(ValueError):
        for unit in before:
            store.edit_business_unit(unit.code, status="INA")
    assert sum(1 for unit in store.business_units() if unit.status == "ACT") == 1  # the last one stays open
    assert store.list_accounts("gl_accounts")


SUPPLIERS = [
    {"vendor_name": "LAKESIDE CATERING LTD", "erp_id": "0410002", "gst": "123456789RT0001", "terms": "N30"},
    {"vendor_name": "NORTHERN PAPER CO", "erp_id": "0410003", "gst": "", "terms": "N45"},
]


def _supplier(store: Store, number: str) -> dict:
    return next(v for v in store.master_vendors() if v["erp_id"] == number)


def test_a_supplier_changed_by_hand_is_put_back_after_a_newer_list_is_loaded(store):
    store.import_vendor_master(SUPPLIERS)
    key = _supplier(store, "0410002")["vendor_key"]
    typed = {"display_name": "Lakeside Catering Ltd.", "expected_gst": "987654321RT0001", "status": "on_hold",
             "terms": "N15", "province": "on"}  # fmt: skip
    changed = store.edit_supplier(key, typed, actor="Pat")
    assert changed["expected_gst"] == {"from": "123456789RT0001", "to": "987654321RT0001"}
    assert changed["status"] == {"from": "active", "to": "on_hold"} and changed["province"]["to"] == "ON"
    said = audit.describe(_last(store))
    assert said.startswith("Suppliers 0410002 Lakeside Catering Ltd.: ") and "status not on hold → on hold" in said
    assert "GST/HST number 123456789RT0001 → 987654321RT0001" in said and _last(store)["actor"] == "Pat"
    # A newer list still says what JD Edwards says: the import replaces it, and the person's change is put back.
    result = store.import_vendor_master(SUPPLIERS, actor="Ann")
    assert result["by_hand"] == ["0410002 Lakeside Catering Ltd."] and result["updated"] == 2
    after = _supplier(store, "0410002")
    assert (after["display_name"], after["expected_gst"], after["status"], after["terms"]) == (
        "Lakeside Catering Ltd.", "987654321RT0001", "on_hold", "N15")  # fmt: skip
    kept = _last(store)
    assert kept["actor"] == "Ann" and kept["detail"]["kept"] == ["0410002 Lakeside Catering Ltd."]
    assert "kept as changed by hand where the file loaded says otherwise" in audit.describe(kept)
    assert store.hand_edits()["vendors"][key]["terms"]["file"] == "N30"
    assert _supplier(store, "0410003")["terms"] == "N45"  # (nobody changed it: the file's)


def test_a_supplier_changed_elsewhere_since_is_not_put_back_and_jd_edwards_own_fields_are_not_changed(store):
    store.import_vendor_master(SUPPLIERS)
    row = _supplier(store, "0410003")
    key = row["vendor_key"]
    store.edit_supplier(key, {"expected_gst": "111111111RT0001", "terms": "N10"})
    store.save_vendor(key, row["display_name"], expected_gst="222222222RT0001")  # the Vendors page, afterwards
    newer = [{**SUPPLIERS[1], "gst": "333333333RT0001"}]
    assert store.import_vendor_master(newer)["by_hand"] == ["0410003 NORTHERN PAPER CO"]  # (its terms)
    after = _supplier(store, "0410003")
    assert (after["expected_gst"], after["terms"]) == ("333333333RT0001", "N10")
    assert set(store.hand_edits()["vendors"][key]) == {"terms"}
    for column in ("erp_id", "erp_hold", "erp_bank", "vendor_key"):
        with pytest.raises(ValueError, match="cannot be changed here"):
            store.edit_supplier(key, {column: "X"})
    with pytest.raises(ValueError, match="needs a name"):
        store.edit_supplier(key, {"display_name": "  "})
    with pytest.raises(ValueError, match="not in the supplier list"):
        store.edit_supplier("no such supplier", {"terms": "N1"})


def test_the_suppliers_fields_are_whichever_columns_the_table_has_and_never_a_bank_account(store):
    assert "by_hand" not in store.import_vendor_master([])  # nothing changed by hand: the import as it was
    names = [field.name for field in store.supplier_master_fields()]
    assert names[:4] == ["erp_id", "display_name", "status", "erp_hold"] and "erp_bank" not in names
    assert not {"vendor_key", "updated_at", "in_master"} & set(names)
    with store._conn() as conn:  # a version that keeps more of the master row: shown and changed like the others
        conn.execute("ALTER TABLE vendors ADD COLUMN currency TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE vendors ADD COLUMN bank_account TEXT NOT NULL DEFAULT ''")
    fields = {field.name: field for field in store.supplier_master_fields()}
    assert fields["currency"].label == "Currency" and fields["currency"].editable and "bank_account" not in fields
    assert not fields["erp_id"].editable and not fields["erp_hold"].editable
    store.import_vendor_master(SUPPLIERS)
    key = _supplier(store, "0410002")["vendor_key"]
    assert store.edit_supplier(key, {"currency": "USD"}) == {"currency": {"from": "", "to": "USD"}}
    assert _supplier(store, "0410002")["currency"] == "USD"
