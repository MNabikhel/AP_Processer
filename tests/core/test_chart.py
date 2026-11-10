"""One JD Edwards account string joined and split in one place, the chart's rows made clean, and which pairs exist and
may be posted to (D-209). The chart is made up (``tests/chart_files.py``)."""

from __future__ import annotations

import pytest

from ap_coder.core import chart
from ap_coder.core.chart import BU_INACTIVE, NOT_IN_CHART, NOT_POSTABLE, OK, RESERVED, AccountPairs, ChartSettings
from tests import chart_files as cf
from tests.chart_files import CLOSED, HOME, NOT_USED, OTHER, UNLISTED


@pytest.mark.parametrize(
    ("business_unit", "code", "account"),
    [
        ("0880133142", "7340.2210", "0880133142.7340.2210"),
        ("0880133142", "2110", "0880133142.2110"),
        ("05504218", "5710.3120", "05504218.5710.3120"),
        ("0880133142", "2180.QR", "0880133142.2180.QR"),
        ("05504218", "2165.60017734", "05504218.2165.60017734"),
        ("08801", "0042", "08801.0042"),
    ],
)
def test_an_account_joins_and_splits_back(business_unit, code, account):
    assert chart.join_account(business_unit, code) == account
    assert chart.split_account(account) == (business_unit, code)


def test_an_exports_padding_and_blank_cells_are_dropped():
    assert chart.split_account("  0880133142.7340.2210") == ("0880133142", "7340.2210")
    assert chart.split_account("    05504218.5710.3120 ") == ("05504218", "5710.3120")
    assert chart.join_account("       08801", " ") == "08801"
    assert chart.code_of("7340", " ") == "7340"
    assert chart.code_of(" 7340 ", "2210") == "7340.2210"
    assert chart.clean(None) == chart.clean(" ") == chart.clean("nan") == ""


def test_leading_zeros_and_letters_are_kept_as_text():
    assert chart.split_code("0042") == ("0042", "")
    assert chart.split_code("2190.7QXR3") == ("2190", "7QXR3")
    assert chart.join_account("0550421", "0042") == "0550421.0042"


def test_a_text_with_no_dot_is_a_business_unit_and_a_code_alone_joins_to_itself():
    assert chart.split_account("0880133142") == ("0880133142", "")
    assert chart.join_account("", "7340.2210") == "7340.2210"
    assert chart.join_account("0880133142", "") == "0880133142"
    assert chart.split_account("") == ("", "")


def test_a_dot_left_at_the_end_is_not_an_empty_subsidiary():
    assert chart.split_account("0880133142.2110.") == ("0880133142", "2110")
    assert chart.code_of("2110", "") == "2110"


# --- The exports' rows -------------------------------------------------------------------------------------------


def _records(headers, cells):
    return [dict(zip(headers, row, strict=True)) for row in cells]


def _chart(rows=cf.CHART):
    return chart.chart_rows(_records(cf.CHART_HEADERS, cf.chart_cells(rows)))


def _units(rows=cf.UNITS):
    return chart.bu_rows(_records(cf.BU_HEADERS, cf.bu_cells(rows)))


def _pairs(**settings):
    return AccountPairs(_chart().rows, _units().rows, ChartSettings(**settings))


def test_the_charts_rows_come_clean_in_the_files_order():
    read = _chart()
    assert read.skipped == {}
    # Padded business units stripped, a lone space blank, zeros and letters kept, the indentation gone, the cut kept.
    assert [(r.bu, r.object, r.subsidiary, r.description, r.lod, r.posting_edit) for r in read.rows] == [
        (bu, obj, sub, cf.indented(text, lod).strip(), lod, edit) for bu, obj, sub, text, lod, edit in cf.CHART
    ]
    by_account = {(r.bu, r.code): r for r in read.rows}
    assert by_account["07310", "0042"].object == "0042"  # an object below 1000 keeps its zeros
    assert by_account["07310", "2140.QC"].subsidiary == "QC"
    assert by_account["07310", "2165.60017734"].subsidiary == "60017734"
    assert by_account["05504218", "5710.3120"].description == "Accounting Package Lice"  # cut at 30 with its indent


def test_a_row_with_no_object_is_its_business_units_header_not_an_account():
    read = _chart()
    headers = [r for r in read.rows if r.is_header]
    assert [(r.bu, r.description) for r in headers] == [(bu, text) for bu, obj, _, text, _, _ in cf.CHART if not obj]
    assert len(headers) == 9 and all(r.code == "" for r in headers)
    assert not any(r.is_header for r in read.rows if r.object)


def test_each_row_gets_the_title_account_above_it_as_its_category():
    category = {(r.bu, r.code): r.category for r in _chart().rows}
    assert category[HOME, "5410"] == category[HOME, "5410.2210"] == "Office and General Costs"
    assert category[HOME, "5710"] == category["05504218", "5710.3120"] == "Computer Software"
    assert category["07310", "2140"] == category["07310", "2140.QC"] == "Current Liabilities"  # not "Objects under…"
    assert category["07310K", "5410"] == category[UNLISTED, "5470"] == ""  # no title account above it
    assert category[HOME, "5400"] == category["07310", ""] == ""  # a business unit's name is no category


def test_rows_left_out_are_counted_with_the_reason():
    blank = [cf.BLANK] * len(cf.CHART_HEADERS)
    no_bu = [cf.BLANK, "5410", *[cf.BLANK] * (len(cf.CHART_HEADERS) - 2)]
    no_object = [cf.padded(HOME), cf.BLANK, "2210", *[cf.BLANK] * (len(cf.CHART_HEADERS) - 3)]
    cells = [*cf.chart_cells(), blank, no_bu, no_object, cf.chart_cells()[3], blank]
    read = chart.chart_rows(_records(cf.CHART_HEADERS, cells))
    assert len(read.rows) == len(cf.CHART)
    assert read.skipped == {"blank rows": 2, "rows with no business unit": 1,
                            "rows with a subsidiary but no object account": 1,
                            "rows repeating an account already read": 1}  # fmt: skip


def test_a_file_that_is_not_the_chart_says_which_columns_it_lacks():
    with pytest.raises(chart.ChartError) as refused:
        chart.chart_rows(_records(cf.BU_HEADERS, cf.bu_cells()))
    said = str(refused.value)
    assert "“Business Unit” or “Object Account”" in said and "Job Number, Job Name" in said and "was not read" in said
    with pytest.raises(chart.ChartError, match="“Job Number”"):
        chart.bu_rows([{"Account": "5410", "Name": "Office"}])
    assert chart.chart_rows([]).rows == []


def test_the_columns_are_found_by_other_usual_names_and_can_be_given():
    records = [{"BU": "  0731066120", "Object": "5410", "Sub": " ", "Description": "  Office Supplies", "LOD": 6,
                "Posting Edit": " "}]  # fmt: skip
    assert chart.chart_rows(records).rows == [chart.ChartRow(HOME, "5410", "", "Office Supplies", 6, "")]
    picked = {"business_unit": "Where", "object": "What"}
    assert chart.chart_rows([{"Where": HOME, "What": "5410"}], picked).rows == [chart.ChartRow(HOME, "5410")]


def test_the_business_units_rows_come_clean():
    read = _units(
        [*cf.UNITS, (HOME, "Again", "07310", "AD", "ACT"), ("0731066101", "Lower Case", "07310", "ad", "ina")]
    )
    assert read.skipped == {"rows repeating a business unit already read": 1}
    assert [(u.code, u.name, u.company, u.type, u.status) for u in read.rows[:-1]] == cf.UNITS
    assert read.rows[-1] == chart.BuRow("0731066101", "Lower Case", "07310", "AD", "INA")
    blank = [cf.BLANK] * len(cf.BU_HEADERS)
    assert chart.bu_rows(_records(cf.BU_HEADERS, [blank, *cf.bu_cells()[:2]])).skipped == {"blank rows": 1}


# --- The settings ------------------------------------------------------------------------------------------------


def test_the_settings_defaults():
    settings = ChartSettings.read({})
    assert settings == ChartSettings()
    assert settings.postable_codes == ("",) and settings.never_offered == ()
    assert settings.skip_words == ("DO NOT USE", "DNU") and settings.home_business_unit == ""
    assert settings.account_labels == ("Business unit", "Object", "Subsidiary")


def test_the_settings_are_read_as_typed_and_saved_back():
    typed = {"postable_codes": "m", "never_offered": "*.7701\n2140; 0731066150.*.*", "skip_words": "DO NOT USE, closed",
             "home_business_unit": "  0731066120", "account_labels": "Job, Cost type, Cost code"}  # fmt: skip
    settings = ChartSettings.read(typed)
    assert settings.postable_codes == ("", "M")
    assert settings.never_offered == ("*.7701", "2140", "0731066150.*.*")
    assert settings.skip_words == ("DO NOT USE", "closed") and settings.home_business_unit == HOME
    assert settings.account_labels == ("Job", "Cost type", "Cost code")
    assert ChartSettings.read(settings.saved()) == settings
    assert set(settings.saved()) == set(chart.SETTING_KEYS)
    # Left blank, or with a part missing: the default.
    assert ChartSettings.read({"skip_words": " ", "account_labels": "Job, Cost code"}) == ChartSettings()


@pytest.mark.parametrize(
    ("name", "word"),
    [("****DO NOT USE**** Old Annex", "DO NOT USE"), ("old yard - do  not use", "DO NOT USE"), ("Yard DNU", "DNU"),
     ("DNU-Yard 4", "DNU"), ("Head Office General", ""), ("Adnure Supplies", ""), ("Do not disturb, use door", ""),
     ("", ""), (None, "")],
)  # fmt: skip
def test_a_name_that_carries_a_skip_word(name, word):
    assert chart.skip_word(name) == word


# --- Which pairs exist and may be posted to ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("business_unit", "code", "status"),
    [
        (HOME, "5410", OK),
        (HOME, "5410.2210", OK),
        ("07310", "2310", OK),  # an account with no subsidiary, on a 5-character business unit
        ("07310K", "5410", OK),  # 6 characters, a letter in it
        ("0550421", "5610", OK),  # 7
        ("05504218", "5710.3120", OK),  # 8
        ("07310", "2140.QC", OK),  # a letter subsidiary
        ("07310", "2190.7QXR3", OK),  # letters and digits
        ("07310", "2165.60017734", OK),  # an 8-digit subsidiary
        (UNLISTED, "5470", OK),  # a business unit the master does not list yet
        ("  0731066120", " 5410.2210 ", OK),  # the export's padding
        (HOME, "5450", NOT_IN_CHART),  # another business unit's account
        (OTHER, "5410.2210", NOT_IN_CHART),
        ("07310", "42", NOT_IN_CHART),  # 0042 without its zeros is another object
        ("0731066100", "5410", NOT_IN_CHART),  # no such business unit
        (HOME, "", NOT_IN_CHART),  # the business unit's header row is no account
        (HOME, "5400", NOT_POSTABLE),  # P E = N: a title
        ("07310", "0042", NOT_POSTABLE),  # an object below 1000
        (HOME, "5430", NOT_POSTABLE),  # I: inactive
        ("0550421", "5620", NOT_POSTABLE),  # M
        ("05504218", "5720", NOT_POSTABLE),  # B
        (HOME, "5440", NOT_POSTABLE),  # L
        (CLOSED, "5410", BU_INACTIVE),
        (CLOSED, "5460", BU_INACTIVE),
        (CLOSED, "9999", BU_INACTIVE),  # whatever the account: the business unit takes nothing
        (NOT_USED, "5410", RESERVED),  # active, but its name says DO NOT USE
        (HOME, "5410.7701", RESERVED),  # a reserved subsidiary (the setting names it)
    ],
)
def test_the_status_of_a_pair(business_unit, code, status):
    pairs = _pairs(never_offered=("*.7701",))
    assert pairs.status(business_unit, code) == status
    assert pairs.offered(business_unit, code) is (status == OK)


def test_never_offered_patterns_by_object_by_subsidiary_and_by_whole_string():
    assert _pairs().status(HOME, "5410.7701") == OK  # nothing is reserved until the setting names it
    by_object = _pairs(never_offered=("2140",))
    assert by_object.status("07310", "2140") == by_object.status("07310", "2140.QC") == RESERVED
    assert by_object.status("07310", "2165.60017734") == OK
    assert "2140" in by_object.why_reserved("07310", "2140.QC")
    by_subsidiary = _pairs(never_offered=("*.QC",))
    assert by_subsidiary.status("07310", "2140.QC") == RESERVED and by_subsidiary.status("07310", "2140") == OK
    whole = _pairs(never_offered=("07310K.*.*", "0731066120.5410.2*"))
    assert whole.status("07310K", "5410") == RESERVED and whole.status(OTHER, "5410") == OK
    assert whole.status(HOME, "5410.2210") == RESERVED and whole.status(HOME, "5410.7701") == OK
    assert ChartSettings.read({"never_offered": "*.qc"}).never_offered == ("*.QC",)  # letters compared in capitals
    assert whole.why_reserved(OTHER, "5410") == "" and "DO NOT USE" in whole.why_reserved(NOT_USED, "5410")


def test_the_settings_decide_what_is_postable_and_which_names_hold_a_business_unit_back():
    assert _pairs(postable_codes=("", "M")).status("0550421", "5620") == OK
    assert _pairs(skip_words=("annex",)).status("07310K", "5410") == RESERVED
    assert _pairs(skip_words=("annex",)).status(NOT_USED, "5410") == RESERVED  # "Old Annex"
    assert _pairs(skip_words=("closed",)).status(NOT_USED, "5410") == OK


def test_a_chart_with_no_business_unit_master_takes_every_business_unit_as_active():
    pairs = AccountPairs(_chart().rows)
    assert pairs.status(CLOSED, "5460") == OK and pairs.name_of(CLOSED) == "Closed Branch"
    assert pairs.status(NOT_USED, "5410") == OK  # its header row's name in the chart says nothing


def test_what_a_business_unit_has_and_where_an_account_is():
    pairs = _pairs(never_offered=("*.7701",))
    assert pairs.accounts_of(HOME) == ["5410", "5410.2210", "5710"]
    assert pairs.accounts_of(" " + OTHER) == ["5410", "5450"]
    assert pairs.accounts_of(CLOSED) == pairs.accounts_of(NOT_USED) == pairs.accounts_of("nowhere") == []
    assert pairs.bus_of("5410") == (HOME, OTHER, "07310K")
    assert pairs.bus_of("5450") == (OTHER,) and pairs.bus_of("5460") == pairs.bus_of("5410.7701") == ()
    assert "5410.7701" not in pairs.offered_codes() and "5460" not in pairs.offered_codes()
    assert {"5410", "2140.QC", "2165.60017734", "5470"} <= pairs.offered_codes()
    assert pairs.business_units() == ["0550421", "05504218", "07310", HOME, OTHER, UNLISTED, "07310K"]
    assert pairs.description("05504218", "5710.3120") == "Accounting Package Lice"
    assert pairs.posting_edit(HOME, "5430") == "I" and pairs.posting_edit(HOME, "5410") == ""
    assert pairs.name_of(NOT_USED) == "****DO NOT USE**** Old Annex" and pairs.name_of(UNLISTED) == "Facilities"
    assert pairs.has_business_unit(CLOSED) and pairs.has_business_unit("0731066188")
    assert not pairs.has_business_unit("0731066100")
    assert pairs.has_code("5460") and pairs.has_code("5400") and not pairs.has_code("9999")
    assert len(pairs) == 32
    assert pairs.tally() == {OK: 15, RESERVED: 2, NOT_POSTABLE: 12, BU_INACTIVE: 3}
    assert pairs.closed_units() == [CLOSED] and pairs.skipped_units() == {NOT_USED: "DO NOT USE"}


def test_the_two_lists_the_chart_gives():
    accounts, centers = chart.account_lists(_chart().rows, _units().rows)
    assert accounts == cf.GL_ACCOUNTS and list(accounts) == sorted(cf.GL_ACCOUNTS)
    assert centers == cf.COST_CENTERS
    # With the title accounts of another kind postable too, and no master: every business unit, more accounts.
    accounts, centers = chart.account_lists(_chart().rows, settings=ChartSettings(postable_codes=("", "I")))
    assert "5430" in accounts and "5460" in accounts and CLOSED in centers and centers[NOT_USED] == "Old Annex"
