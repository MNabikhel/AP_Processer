"""``python -m ap_coder chart import --chart <file> --business-units <file>`` (D-209): the two exports loaded from the
console, what was read and left out said in plain words. The chart is made up (``tests/chart_files.py``)."""

from __future__ import annotations

from ap_coder.core.store import Store
from ap_coder.ops import chart_cmd, cli
from tests import chart_files as cf


def _run(tmp_path, capsys, *args, action="import"):
    code = cli.main(["--db", str(tmp_path / "ap_coder.db"), "chart", action, *args])
    said = capsys.readouterr()
    return code, said.out, said.err


def test_an_import_says_what_left_the_lists_and_what_was_set_aside(tmp_path, capsys):
    """A smaller export: the manager and the meals tick of what left are kept aside, and the summary says so."""
    full = cf.write_chart(tmp_path / "chart.xlsx")
    assert _run(tmp_path, capsys, "--chart", str(full), "--business-units", str(cf.write_business_units(
        tmp_path / "bu.xlsx")))[0] == 0  # fmt: skip
    store = Store(tmp_path / "ap_coder.db")
    store.import_accounts("gl_accounts", [{"code": "5450", "meals": "yes"}], "code", None, meals_column="meals")
    store.set_cost_center_manager([cf.OTHER], "Dana Reyes", "dana@example.test", "pat")
    store.set_tax_treatment("QST", "recoverable", "5450")
    smaller = cf.write_chart(tmp_path / "smaller.xlsx", [r for r in cf.CHART if r[0] != cf.OTHER])
    code, out, _ = _run(tmp_path, capsys, "--chart", str(smaller))
    assert code == 0
    assert "GL accounts: 12 now (0 added, 0 changed, 1 removed)." in out
    assert "  Left the GL accounts: 5450." in out and f"  Left the cost centers: {cf.OTHER}." in out
    assert ("  Set aside, and put back when the chart has them again: the meals tick of 5450; the manager of "
            f"{cf.OTHER}.") in out  # fmt: skip
    assert "  To set right: the tax set-up posts QST to GL account 5450, which is no longer in the list." in out
    code, out, _ = _run(tmp_path, capsys, "--chart", str(full))
    assert f"  Put back: the meals tick of 5450; the manager of {cf.OTHER}." in out and "To set right" not in out
    assert store.cost_center_managers() == {cf.OTHER: {"name": "Dana Reyes", "email": "dana@example.test"}}


def test_the_lists_are_worked_out_again_on_demand(tmp_path, capsys):
    code, _, err = _run(tmp_path, capsys, action="rebuild")
    assert code == 1 and "No chart of accounts is imported yet" in err
    assert _run(tmp_path, capsys, "--chart", str(cf.write_chart(tmp_path / "chart.xlsx")))[0] == 0
    store = Store(tmp_path / "ap_coder.db")
    store.save_accounts("gl_accounts", [r for r in store.list_accounts("gl_accounts") if r["code"] != "5450"])
    code, out, _ = _run(tmp_path, capsys, action="rebuild")
    assert code == 0 and "GL accounts: 14 now (1 added, 0 changed, 0 removed)." in out
    code, out, _ = _run(tmp_path, capsys, action="rebuild")
    assert "The GL accounts and cost centers already are what the chart gives: nothing changed." in out


def test_the_charts_settings_are_set_and_shown_from_the_console(tmp_path, capsys):
    code, out, _ = _run(tmp_path, capsys, action="settings")
    assert code == 0 and "Posting edit codes AP may post to: blank only." in out
    assert (
        "Never offered: none." in out
        and "Skip words: DO NOT USE, DNU." in out
        and "Home business unit: not set." in out
    )
    files = ("--chart", str(cf.write_chart(tmp_path / "chart.xlsx")), "--business-units",
             str(cf.write_business_units(tmp_path / "bu.xlsx")))  # fmt: skip
    assert _run(tmp_path, capsys, *files)[0] == 0
    code, out, _ = _run(tmp_path, capsys, "--never-offered", "*.7701, 2140", "--home-business-unit", cf.HOME,
                        "--postable", "m", action="settings")  # fmt: skip
    assert code == 0
    assert "Posting edit codes AP may post to: blank and M." in out and "Never offered: *.7701, 2140." in out
    assert f"Home business unit: {cf.HOME} (Head Office General)." in out
    assert "GL accounts: 14 now (1 added, 0 changed, 0 removed)." in out  # the account with code M is listed at once
    store = Store(tmp_path / "ap_coder.db")
    assert (
        store.chart_settings().never_offered == ("*.7701", "2140") and "5620" in store.reference_data().line_accounts()
    )
    assert store.events(actions=["settings_changed"])[0]["actor"] == chart_cmd.ACTOR
    code, out, _ = _run(tmp_path, capsys, action="show")
    assert code == 0 and "Chart of accounts: 32 accounts on 9 business units; 9 business units in the master." in out
    assert "Never offered: *.7701, 2140." in out and "Accounts a line may be coded to: 14" in out
    assert "In all, 4 accounts may be posted to but are never offered" in out
    # A setting is cleared with an empty value; a home business unit the chart does not have is refused.
    code, out, _ = _run(tmp_path, capsys, "--never-offered", "", "--skip-words", "closed", action="settings")
    assert "Never offered: none." in out and "Skip words: closed." in out
    code, _, err = _run(tmp_path, capsys, "--home-business-unit", "0999999999", action="settings")
    assert code == 1 and "There is no business unit 0999999999" in err and "Nothing was changed." in err
    assert store.chart_settings().home_business_unit == cf.HOME


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
    assert "Business units: the same as already imported." in out
    assert "Chart of accounts: the same as already imported." in out
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
    assert code == 1 and "Not imported: bu.xlsx has no columns named “Business Unit” and “Object Account”" in err
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


def test_a_chart_without_its_posting_edit_column_is_imported_only_when_asked_and_says_so_each_time(tmp_path, capsys):
    """JD Edwards exports the grid's visible columns only: without "P E" every account was postable, with no word
    (B-2271)."""
    grid = cf.write_chart(tmp_path / "grid.xlsx", without=("P E",))
    code, _, err = _run(tmp_path, capsys, "--chart", str(grid))
    assert code == 1 and "Not imported: grid.xlsx has no posting edit column (“P E”): without it AP Coder" in err
    assert "To import it anyway, add --anyway: every account will be offered." in err
    store = Store(tmp_path / "ap_coder.db")
    assert store.chart_accounts() == [] and store.chart_without() == ()
    code, out, _ = _run(tmp_path, capsys, "--chart", str(grid), "--anyway")
    said = "Loaded without the posting edit column (“P E”): every account is offered, titles and accounts closed"
    assert code == 0 and said in out and "32 accounts on 9 business units; 32 may be posted to." in out
    assert store.chart_without() == ("posting_edit",)
    assert store.events(actions=["accounts_imported"])[0]["detail"]["without"] == ["posting_edit"]
    assert said in _run(tmp_path, capsys, action="show")[1]  # every time the chart is shown
    code, out, _ = _run(tmp_path, capsys, "--chart", str(cf.write_chart(tmp_path / "chart.xlsx")), "--anyway")
    assert code == 0 and said not in out and store.chart_without() == ()
    assert said not in _run(tmp_path, capsys, action="show")[1]
