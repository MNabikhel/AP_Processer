"""With a chart of accounts imported, a line's account is checked on its business unit (D-209); with none, nothing
moves. The chart is made up (``tests/chart_files.py``)."""

from __future__ import annotations

import copy
import dataclasses
import json

import pytest

from ap_coder.coding import validation
from ap_coder.coding.validation import COUNTED_IN_CODING, ERROR, WARNING, Issue, coding_confidence, validate_coding
from ap_coder.core import chart
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import load_sample_setup
from ap_coder.web.invoice_view import is_hard
from tests import chart_files as cf
from tests.chart_files import CLOSED, HOME, NOT_USED, OTHER
from tests.conftest import SAMPLES


def _chart(rows=cf.CHART):
    return chart.chart_rows(dict(zip(cf.CHART_HEADERS, row, strict=True)) for row in cf.chart_cells(rows)).rows


def _units():
    return chart.bu_rows(dict(zip(cf.BU_HEADERS, row, strict=True)) for row in cf.bu_cells()).rows


@pytest.fixture
def reference(store):
    """The reference data of an install with the made-up chart imported, the reserved subsidiary named."""
    store.import_business_units(_units())
    store.import_chart(_chart())
    for tax in ("GST", "HST"):
        store.set_tax_treatment(tax, "recoverable", "2310")
    store.set_setting("never_offered", "*.7701")
    return store.reference_data()


def _coded(ground_truth, *pairs):
    """The sample invoice with its first lines coded to ``pairs`` (business unit, GL code), the others to a good
    pair."""
    coded = copy.deepcopy(ground_truth)
    for number, line in enumerate(coded["line_items"]):
        pair = pairs[number] if number < len(pairs) else (HOME, "5410")
        line["predicted_cost_center"], line["predicted_gl_code"] = pair
    return InvoiceCoding.model_validate(coded)


def _found(report):
    return [(i.line_number, i.severity, i.code) for i in report.issues]


def test_lines_coded_to_pairs_the_chart_has_pass(reference, ground_truth):
    coding = _coded(ground_truth, (HOME, "5410.2210"), (OTHER, "5450"), ("07310", "2165.60017734"), ("07310K", "5410"))
    report = validate_coding(coding, reference)
    assert reference.pairs is not None and report.issues == []
    assert report.coding_confidence == 1.0 and report.requires_review is False


def test_each_way_a_pair_is_wrong_is_a_finding_and_counts_nothing_in_the_coding(reference, ground_truth):
    """Before D-209: an account of another business unit, a closed business unit and a reserved subsidiary had no
    finding at all, and the Coding said 100%."""
    coding = _coded(ground_truth, (HOME, "5450"), (CLOSED, "5410"), (HOME, "5410.7701"), (HOME, "5400"))
    report = validate_coding(coding, reference)
    assert _found(report) == [(1, ERROR, "ACCOUNT_NOT_IN_CHART"), (2, ERROR, "BU_INACTIVE"),
                              (3, WARNING, "ACCOUNT_RESERVED"), (4, ERROR, "ACCOUNT_NOT_POSTABLE")]  # fmt: skip
    lines = len(coding.line_items)
    assert lines > 4 and report.coding_confidence == round((lines - 4) / lines, 4)
    assert coding_confidence(coding, reference) == report.coding_confidence
    assert report.reading_confidence == 1.0  # counted once, in the Coding: nothing more off the Reading
    assert report.requires_review is True
    said = {i.code: i.message for i in report.issues}
    assert said["ACCOUNT_NOT_IN_CHART"] == (
        f"account {HOME}.5450 is not in the chart of accounts: business unit {HOME} has no account 5450; it is on "
        f"{OTHER}"
    )
    assert said["BU_INACTIVE"].startswith(f"business unit {CLOSED} is not active in JD Edwards")
    assert f"account {HOME}.5400 cannot be posted to (its posting edit code is N)" in said["ACCOUNT_NOT_POSTABLE"]
    assert said["ACCOUNT_RESERVED"].startswith(
        f"account {HOME}.5410.7701 exists but is not offered for invoices: it is one of the accounts never offered "
        "(*.7701)"
    )


def test_one_finding_a_line_says_why_not_one_more_for_each_code(reference, ground_truth):
    # A closed business unit is in no list, a title account neither: the pair's finding is the only one.
    coding = _coded(ground_truth, (CLOSED, "5460"), (HOME, "5430"), ("0731066100", "5410"), (HOME, "9999"),
                    (CLOSED, "9999"))  # fmt: skip
    report = validate_coding(coding, reference)
    assert _found(report) == [(1, ERROR, "BU_INACTIVE"), (2, ERROR, "ACCOUNT_NOT_POSTABLE"),
                              (3, ERROR, "ACCOUNT_NOT_IN_CHART"), (4, ERROR, "ACCOUNT_NOT_IN_CHART"),
                              (5, ERROR, "BU_INACTIVE")]  # fmt: skip
    said = [i.message for i in report.issues]
    assert said[2].endswith("the chart has no business unit 0731066100 (pick one from the list)")
    assert said[3].endswith("no business unit has an account 9999: pick one from the list")
    assert "its posting edit code is I" in said[1]
    only_closed = validate_coding(_coded(ground_truth, (HOME, "5460")), reference).issues[0].message
    assert only_closed.endswith(f"business unit {HOME} has no account 5460, and no business unit has one that may be "
                                "posted to")  # fmt: skip


def test_the_finding_names_up_to_three_business_units_that_have_the_account_the_home_one_first(reference, ground_truth):
    units = [f"07310661{n}" for n in range(60, 66)]
    rows = [chart.ChartRow(bu, "5480", "", "Postage") for bu in units] + [chart.ChartRow(HOME, "5410")]
    pairs = chart.AccountPairs(rows, settings=chart.ChartSettings(home_business_unit="0731066163"))
    wide = dataclasses.replace(reference, pairs=pairs)
    (issue,) = [i for i in validate_coding(_coded(ground_truth, (HOME, "5480")), wide).issues if i.line_number == 1]
    assert issue.code == "ACCOUNT_NOT_IN_CHART"
    assert issue.message.endswith("it is on 0731066163, 0731066160, 0731066161 and 3 more")
    assert validation._named(("B", "A")) == "B and A" and validation._named(("A", "B", "C")) == "A, B and C"
    assert validation._named(("A",)) == "A" and validation._named(("A", "B", "C", "D"), "D") == "D, A, B and 1 more"


def test_a_line_not_coded_yet_or_coded_to_a_tax_account_is_said_as_before(reference, ground_truth):
    coding = _coded(ground_truth, (HOME, UNASSIGNED), (UNASSIGNED, "5410"), (HOME, "2310"), (UNASSIGNED, UNASSIGNED))
    report = validate_coding(coding, reference)
    assert _found(report) == [(1, WARNING, "GL_UNASSIGNED"), (2, WARNING, "CC_UNASSIGNED"),
                              (3, ERROR, "GL_IS_TAX_ACCOUNT"), (4, WARNING, "GL_UNASSIGNED"),
                              (4, WARNING, "CC_UNASSIGNED")]  # fmt: skip


def test_words_or_part_of_a_number_in_the_account_box_are_one_finding_in_the_charts_words(reference, ground_truth):
    """B-2262: words typed in the Account box are kept as typed in the line's business unit; the line said "no GL
    account yet" and "cost center off is not in your cost centers". It says one thing; a business unit alone, a GL
    code alone and an empty box are said as before."""
    coding = _coded(ground_truth, ("off", UNASSIGNED), ("073106", UNASSIGNED), (HOME, UNASSIGNED), (UNASSIGNED, "5410"),
                    (UNASSIGNED, UNASSIGNED))  # fmt: skip
    report = validate_coding(coding, reference)
    assert _found(report) == [(1, ERROR, "ACCOUNT_NOT_IN_CHART"), (2, ERROR, "ACCOUNT_NOT_IN_CHART"),
                              (3, WARNING, "GL_UNASSIGNED"), (4, WARNING, "CC_UNASSIGNED"),
                              (5, WARNING, "GL_UNASSIGNED"), (5, WARNING, "CC_UNASSIGNED")]  # fmt: skip
    assert [i.message for i in report.issues[:2]] == ["“off” is not an account: pick one from the list",
                                                      "“073106” is not an account: pick one from the list"]  # fmt: skip


def test_a_reserved_account_is_for_a_person_to_confirm_not_a_blocker(reference, ground_truth):
    report = validate_coding(_coded(ground_truth, (NOT_USED, "5410")), reference)
    assert _found(report) == [(1, WARNING, "ACCOUNT_RESERVED")] and report.errors == []
    assert f"the name of business unit {NOT_USED} says DO NOT USE" in report.issues[0].message
    assert report.requires_review is True and report.coding_confidence < 1.0


def test_approve_anyway_never_covers_an_account_jd_edwards_would_refuse():
    for code in ("ACCOUNT_NOT_IN_CHART", "ACCOUNT_NOT_POSTABLE", "BU_INACTIVE"):
        assert is_hard(Issue(ERROR, code, "x")), code
    assert not is_hard(Issue(WARNING, "ACCOUNT_RESERVED", "x"))
    assert {"ACCOUNT_NOT_IN_CHART", "ACCOUNT_NOT_POSTABLE", "BU_INACTIVE", "ACCOUNT_RESERVED"} <= COUNTED_IN_CODING


def test_the_help_of_each_check_says_it_applies_once_a_chart_is_loaded_and_how_to_load_one():
    """The Help page lists the four for every install, a chart loaded or not."""
    from ap_coder.ops.help import CHECKS

    for code in ("ACCOUNT_NOT_IN_CHART", "ACCOUNT_NOT_POSTABLE", "BU_INACTIVE", "ACCOUNT_RESERVED"):
        assert CHECKS[code].meaning.startswith("Once a chart of accounts is loaded from JD Edwards: "), code
    for code in ("ACCOUNT_NOT_IN_CHART", "BU_INACTIVE"):
        assert "JD Edwards files" in CHECKS[code].action, code


def test_a_list_changed_by_hand_since_the_import_is_still_checked(reference, ground_truth, store):
    """An account taken off the GL accounts by hand is unknown, as before, though the chart has the pair."""
    store.save_accounts("gl_accounts", [r for r in store.list_accounts("gl_accounts") if r["code"] != "5450"])
    report = validate_coding(_coded(ground_truth, (OTHER, "5450")), store.reference_data())
    assert _found(report) == [(1, ERROR, "GL_UNKNOWN")]


@pytest.mark.parametrize("sample", sorted(p.name for p in (SAMPLES / "ground_truth").glob("*.json")))
def test_an_install_with_no_chart_checks_the_sample_invoices_exactly_as_before(sample, store, monkeypatch):
    load_sample_setup(store)
    reference = store.reference_data()
    assert reference.pairs is None and store.account_pairs() is None
    coding = InvoiceCoding.model_validate(json.loads((SAMPLES / "ground_truth" / sample).read_text(encoding="utf-8")))
    coding.line_items[0].predicted_gl_code = "9999"  # and a line the lists do not know, said as before
    coding.line_items[-1].predicted_cost_center = "CC999"
    report = validate_coding(coding, reference)
    assert [(i.line_number, i.code) for i in report.issues if i.code.endswith("_UNKNOWN")] == [
        (1, "GL_UNKNOWN"), (len(coding.line_items), "CC_UNKNOWN")]  # fmt: skip
    assert not {i.code for i in report.issues} & {"ACCOUNT_NOT_IN_CHART", "ACCOUNT_NOT_POSTABLE", "BU_INACTIVE",
                                                  "ACCOUNT_RESERVED"}  # fmt: skip
    # The same report with the pair check taken out altogether: it does nothing here.
    monkeypatch.setattr(validation, "_pair_issue", lambda *args: None)
    assert validate_coding(coding, reference).to_dict() == report.to_dict()
