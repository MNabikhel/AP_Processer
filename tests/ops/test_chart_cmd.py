"""``python -m ap_coder chart import --chart <file> --business-units <file>`` (D-209): the two exports loaded from the
console, what was read and left out said in plain words. The chart is made up (``tests/chart_files.py``)."""

from __future__ import annotations

from ap_coder.core.store import Store
from ap_coder.ops import chart_cmd, cli
from tests import chart_files as cf


def _run(tmp_path, capsys, *args):
    code = cli.main(["--db", str(tmp_path / "ap_coder.db"), "chart", "import", *args])
    said = capsys.readouterr()
    return code, said.out, said.err


def test_both_files_are_imported_and_what_was_read_is_said(tmp_path, capsys):
    chart, units = cf.write_chart(tmp_path / "chart.xlsx"), cf.write_business_units(tmp_path / "bu.xlsx")
    code, out, err = _run(tmp_path, capsys, "--chart", str(chart), "--business-units", str(units))
    assert code == 0 and err == ""
    assert "Read 9 business units: 8 active, 1 not active." in out
    assert "Business units: 9 added, 0 changed, 0 removed." in out
    assert "Read 41 rows of the chart: 32 accounts on 9 business units; 19 may be posted to." in out
    assert "Not for posting, by posting edit code: 1 with B, 1 with I, 1 with L, 1 with M, 9 with N." in out
    assert "Chart of accounts: 41 added, 0 changed, 0 removed." in out
    assert "GL accounts: 13 now (13 added, 0 changed, 0 removed)." in out
    assert "Cost centers: 8 now (8 added, 0 changed, 0 removed)." in out
    assert "Accounts a line may be coded to: 16" in out
    assert f"Not used: 3 accounts on a business unit that is not active ({cf.CLOSED})." in out
    assert f"Never offered: business unit {cf.NOT_USED}, whose name says DO NOT USE." in out
    assert "In all, 1 account may be posted to but is never offered" in out
    store = Store(tmp_path / "ap_coder.db")
    assert len(store.chart_accounts()) == 41 and len(store.business_units()) == 9
    assert {e["actor"] for e in store.events(actions=["accounts_imported"])} == {chart_cmd.ACTOR}


def test_the_same_files_again_change_nothing_and_say_so(tmp_path, capsys):
    chart, units = cf.write_chart(tmp_path / "chart.xlsx"), cf.write_business_units(tmp_path / "bu.xlsx")
    assert _run(tmp_path, capsys, "--chart", str(chart), "--business-units", str(units))[0] == 0
    store = Store(tmp_path / "ap_coder.db")
    before = (store.chart_version(), store.events_stamp(), store.list_accounts("gl_accounts"))
    code, out, _ = _run(tmp_path, capsys, "--chart", str(chart), "--business-units", str(units))
    assert code == 0
    assert "Business units: the same as already imported, nothing changed." in out
    assert "Chart of accounts: the same as already imported, nothing changed." in out
    assert "GL accounts: 13 now (0 added, 0 changed, 0 removed)." in out
    assert (store.chart_version(), store.events_stamp(), store.list_accounts("gl_accounts")) == before


def test_rows_left_out_and_never_offered_accounts_are_said_with_the_reason(tmp_path, capsys):
    blank = ("", "", "", "", 0, "")
    chart = cf.write_chart(tmp_path / "chart.xlsx", [*cf.CHART, blank, cf.CHART[3]])
    Store(tmp_path / "ap_coder.db").set_setting("never_offered", "*.7701")
    code, out, _ = _run(tmp_path, capsys, "--chart", str(chart))
    assert code == 0
    assert "  Left out (blank rows): 1." in out and "  Left out (rows repeating an account already read): 1." in out
    assert "  Never offered: the accounts on the never-offered list (*.7701)." in out
    assert "In all, 1 account may be posted to but is never offered" in out
    assert "Not used" not in out and "whose name says" not in out  # no business unit master: none closed or named


def test_a_wrong_file_changes_nothing_of_either(tmp_path, capsys):
    chart, units = cf.write_chart(tmp_path / "chart.xlsx"), cf.write_business_units(tmp_path / "bu.xlsx")
    code, out, err = _run(tmp_path, capsys, "--chart", str(units), "--business-units", str(units))
    assert code == 1 and "Not imported: This file has no column named “Business Unit” or “Object Account”" in err
    code, _, err = _run(tmp_path, capsys, "--business-units", str(chart))
    assert code == 1 and "it is the chart of accounts, not the business unit master" in err
    code, _, err = _run(tmp_path, capsys, "--chart", str(tmp_path / "missing.xlsx"))
    assert code == 1 and "There is no file" in err and "Nothing was changed." in err
    code, _, err = _run(tmp_path, capsys)
    assert code == 2 and "--chart <file>" in err and "--business-units <file>" in err
    # A chart no account of which may be posted to: the business units given with it are not kept either.
    titles = cf.write_chart(tmp_path / "titles.xlsx", [r for r in cf.CHART if r[5] == "N"])
    code, _, err = _run(tmp_path, capsys, "--chart", str(titles), "--business-units", str(units))
    assert code == 1 and "nothing was changed" in err
    store = Store(tmp_path / "ap_coder.db")
    assert store.business_units() == [] and store.chart_accounts() == [] and store.chart_version() == 0
    assert store.events(actions=["accounts_imported"]) == []
