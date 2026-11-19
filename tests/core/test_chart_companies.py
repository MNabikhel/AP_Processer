"""D-267: a chart of accounts split by company loads a file at a time. Each file replaces the accounts (and business
units) of the companies it has, the other companies' kept, so the files add up and one company's file again refreshes
that company alone; "start over" replaces it all. JD Edwards' L and U posting edit codes are postable by default."""

import sqlite3
from contextlib import closing

import pytest

from ap_coder.core import chart
from ap_coder.core.chart import BuRow, ChartRow
from ap_coder.core.store import Store


def _rows(company, unit, *objects, description="Cost"):
    return [ChartRow(unit, "", "", f"Job {unit}", 3, "N", "", company)] + [
        ChartRow(unit, obj, "", f"{description} {obj}", 7, "", "", company) for obj in objects
    ]


def _accounts(store):
    return {(r.company, r.bu, r.object): r.description for r in store.chart_accounts() if r.object}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ap.db")


def test_each_companys_file_adds_up_and_loaded_again_refreshes_only_that_company(store):
    store.import_business_units([BuRow("2100101", "Job A", "00210"), BuRow("3100101", "Job B", "00310")])
    store.import_chart(_rows("00210", "2100101", "5110", "5120"))
    result = store.import_chart(_rows("310", "3100101", "5110"))
    assert result["companies"] == ["00310"] and (result["added"], result["removed"]) == (2, 0)
    assert set(_accounts(store)) == {("00210", "2100101", "5110"), ("00210", "2100101", "5120"),
                                     ("00310", "3100101", "5110")}  # fmt: skip
    again = store.import_chart(_rows("00210", "2100101", "5110", description="Materials"))
    assert (again["updated"], again["removed"]) == (1, 1)
    assert _accounts(store) == {("00210", "2100101", "5110"): "Materials 5110",
                                ("00310", "3100101", "5110"): "Cost 5110"}  # fmt: skip
    assert {code for code in store.reference_data().line_accounts()} >= {"5110"}
    # starting over: the file's alone
    store.import_chart(_rows("00310", "3100101", "5120"), whole=True)
    assert set(_accounts(store)) == {("00310", "3100101", "5120")}


def test_accounts_kept_without_a_company_are_replaced_on_the_business_units_of_the_file(store):
    store.import_chart([ChartRow("2100101", "5110", "", "Old"), ChartRow("9900101", "5110", "", "Other")])
    store.import_chart(_rows("00210", "2100101", "5120"))
    assert set(_accounts(store)) == {("", "9900101", "5110"), ("00210", "2100101", "5120")}


def test_business_units_load_by_company_too(store):
    store.import_business_units([BuRow("2100101", "Job A", "00210"), BuRow("3100101", "Job B", "00310")])
    store.import_business_units([BuRow("2100102", "Job A2", "00210")])
    assert {u.code for u in store.business_units()} == {"2100102", "3100101"}
    store.import_business_units([BuRow("2100103", "Job A3", "00210")], whole=True)
    assert {u.code for u in store.business_units()} == {"2100103"}


def test_the_chart_reads_each_accounts_company_and_an_older_database_gets_the_column(tmp_path):
    read = chart.chart_rows([{"Business Unit": "  2100101", "Object Account": "5110", "Subsidiary": "",
                              "Account Description": "Cost", "Company": "210", "L D": "7", "P E": "L"}])  # fmt: skip
    assert read.rows[0].company == "00210"
    pairs = chart.AccountPairs(read.rows)
    assert pairs.status("2100101", "5110") == chart.OK  # L: a subledger is required, postable
    assert chart.ChartSettings().postable_codes == ("", "L", "U")
    db = Store(tmp_path / "old" / "ap.db").path
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("ALTER TABLE chart_accounts DROP COLUMN company")
        conn.execute("INSERT INTO chart_accounts (bu, object) VALUES ('2100101', '5110')")
    assert Store(db).chart_accounts()[0].company == ""


def test_a_chart_saved_as_csv_is_streamed_from_disk_a_company_at_a_time(store, tmp_path, capsys):
    from ap_coder.ops import chart_cmd

    header = "Company,Business Unit,Object Account,Subsidiary,Account Description,L D,P E\n"
    first = tmp_path / "chart_00210.csv"
    first.write_text(header + "".join(f"00210,  21001{n:02d},5110,,Cost,7,\n00210,  21001{n:02d},5110,,Cost,7,\n"
                                      for n in range(5)), encoding="utf-8")  # fmt: skip
    said = chart_cmd.import_files(store, first, every=4)
    assert "Read 10 rows of the chart: 10 accounts on 5 business units; 10 may be posted to." in said
    assert "  4 rows read..." in capsys.readouterr().out
    assert len(store.account_pairs()) == 5  # (each account given twice is kept once)
    second = tmp_path / "chart_00310.csv"
    second.write_text(header + "00310,3100101,5110,0100,Labour,8,U\n", encoding="utf-8")
    said = chart_cmd.import_files(store, second)
    assert "Chart of accounts (company 00310's; the other companies' kept): 1 rows added, 0 changed, 0 removed." in said
    assert store.account_pairs().status("3100101", "5110.0100") == chart.OK and len(store.account_pairs()) == 6


def test_an_account_not_in_the_chart_or_on_a_closed_unit_never_stops_a_load_and_is_said_on_the_invoice(store):
    store.import_business_units([BuRow("2100101", "Job A", "00210"), BuRow("2100199", "Old job", "00210", "", "CL")])
    store.import_chart(_rows("00210", "2100101", "5110"))
    line = {"po_number": "4512", "vendor_name": "Lakeside Steel Ltd", "line_number": 1, "description": "Rebar",
            "quantity": 1, "unit_price": 5, "amount": 5, "status": "open"}  # fmt: skip
    loaded = store.import_purchase_orders(
        [
            {**line, "cost_center": "2100199", "gl_code": "5110"},
            {**line, "line_number": 2, "cost_center": "9999999", "gl_code": "5110"},
        ]
    )
    assert loaded["lines"] == 2
    pairs = store.account_pairs()
    assert (
        pairs.status("2100199", "5110") == chart.BU_INACTIVE and pairs.status("9999999", "5110") == chart.NOT_IN_CHART
    )
