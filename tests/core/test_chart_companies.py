"""D-267: a chart of accounts split by company loads a file at a time. Each file replaces the accounts (and business
units) of the companies it has, the other companies' kept, so the files add up and one company's file again refreshes
that company alone; "start over" replaces it all. JD Edwards' L and U posting edit codes are postable by default."""

import json
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
    assert "Read 10 rows of the chart: 5 accepted, 5 rejected." in said
    assert "  Rejected (rows repeating an account already read): 5 (rows 3, 5, 7, 9, 11)." in said
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


# --- A refresh read in full before it replaces anything (D-272) -----------------------------------------------------


def _group(store):
    """Two companies' charts loaded: 00210 with ten accounts, 00310 with four."""
    store.import_chart(_rows("00210", "2100101", *(f"51{n:02d}" for n in range(10))) +
                       _rows("00310", "3100101", "5110", "5120", "5130", "5140"))  # fmt: skip
    return _accounts(store), store.chart_version()


def test_the_rows_are_staged_without_the_write_lock_and_the_live_chart_is_untouched_until_the_swap(store):
    """While a chart of millions is read, the dashboard writes as usual and reads the chart as it was."""
    before, _ = _group(store)
    seen = {}

    def rows():
        yield from _rows("00210", "2100101", *(f"52{n:02d}" for n in range(10)))
        with closing(sqlite3.connect(store.path, timeout=0.2)) as other:  # (another process: the dashboard)
            other.execute("BEGIN IMMEDIATE")
            other.execute("INSERT INTO settings (key, value) VALUES ('meanwhile', 'written')")
            other.commit()
            seen["live"] = other.execute("SELECT COUNT(*) FROM chart_accounts WHERE object != ''").fetchone()[0]

    store.import_chart(rows())
    assert seen["live"] == len(before) and store.get_setting("meanwhile") == "written"
    assert {k for k in _accounts(store) if k[0] == "00210"} == {("00210", "2100101", f"52{n:02d}") for n in range(10)}


def test_a_load_that_fails_or_is_stopped_midway_leaves_the_chart_as_it_was(store):
    before, version = _group(store)

    def broken():
        yield from _rows("00210", "2100101", "6110", "6120")
        raise chart.ChartError("Row 4: the business unit “21.0” is not one JD Edwards holds.")

    with pytest.raises(chart.ChartError):
        store.import_chart(broken())

    def stopped():
        yield from _rows("00210", "2100101", "6110", "6120")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        store.import_chart(stopped())
    assert (_accounts(store), store.chart_version()) == (before, version)


def test_a_refresh_with_far_fewer_accounts_of_a_company_is_refused_unless_asked(store):
    """Less than half a company's accounts (a filtered or cut-short export) never replaces them; the whole chart the
    same; ``anyway`` loads it."""
    before, version = _group(store)
    with pytest.raises(ValueError, match=r"less than half the accounts loaded now of company 00210 \(4 accounts, 10 "
                                         r"now\).*--anyway"):  # fmt: skip
        store.import_chart(_rows("00210", "2100101", "5100", "5101", "5102", "5103"))
    with pytest.raises(ValueError, match="the file has 1 accounts, less than half the 14 loaded now"):
        store.import_chart(_rows("", "2100101", "5100"), whole=True)
    assert (_accounts(store), store.chart_version()) == (before, version)
    store.import_chart(_rows("00210", "2100101", "5100", "5101", "5102", "5103", "5104"))  # (half: loaded)
    store.import_chart(_rows("00310", "3100101", "5110"), anyway=True)
    assert sorted(k[2] for k in _accounts(store) if k[0] == "00310") == ["5110"]


def test_a_company_split_across_files_loads_with_all_of_them_and_never_from_part_of_them(store):
    """The files of one import are staged together and swapped in once: a company in two of them loaded whole. Its
    next refresh from fewer files than that is refused (it would drop the other file's accounts), unless asked."""
    first, second = _rows("00210", "2100101", "5110", "5120"), _rows("00210", "2100102", "5110", "5120")
    with store.session():
        staged = store.stage_chart([("a.txt", first), ("b.txt", second)])
        store.swap_chart(staged)
    assert len(_accounts(store)) == 4 and staged.company_parts == {"00210": {"a.txt", "b.txt"}}
    assert json.loads(store.get_setting("chart_parts")) == {"00210": ["a.txt", "b.txt"]}
    with pytest.raises(ValueError, match=r"company 00210 came from 2 files last time \(a\.txt, b\.txt\); this import "
                                         r"has it in 1 \(a\.txt\)"):  # fmt: skip
        with store.session():
            store.swap_chart(store.stage_chart([("a.txt", _rows("00210", "2100101", "5110", "5120", "5130"))]))
    assert len(_accounts(store)) == 4
    with store.session():  # both again: refreshed
        store.swap_chart(store.stage_chart([("a.txt", first), ("b.txt", _rows("00210", "2100102", "5110", "5130"))]))
    assert ("00210", "2100102", "5130") in _accounts(store)
    with store.session():  # now in one file, as the person says
        store.swap_chart(store.stage_chart([("all.txt", first + second)]), anyway=True)
    assert json.loads(store.get_setting("chart_parts")) == {"00210": ["all.txt"]}
