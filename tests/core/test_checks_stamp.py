"""An invoice's stored checks are stamped with what they were checked AGAINST (B-815, B-2094): a reader keeps its
reference data for a whole run of files, so lists changed during the run (a chart of accounts imported, a GL account
removed) must leave its invoices to be checked again, not stamped as checked against the new lists."""

from __future__ import annotations

import copy

from ap_coder.coding.validation import validate_coding
from ap_coder.core import chart
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import load_sample_setup
from tests import chart_files as cf


def _saved(store, tmp_path, coding, reference, name="a.pdf"):
    """The invoice saved as the pipeline saves it: its checks made with the reference data the reader holds."""
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.4 " + name.encode())
    report = validate_coding(InvoiceCoding.model_validate(coding), reference)
    return store.add_invoice(pdf, coding, report.to_dict()), report


def test_lists_changed_while_a_reader_holds_its_reference_data_leave_its_invoices_to_check_again(
    store, tmp_path, ground_truth
):
    load_sample_setup(store)
    held = store.reference_data()  # read once for the run of files
    first, report = _saved(store, tmp_path, ground_truth, held)
    assert report.issues == [] and store.stale_checks(store.checks_stamp()) == []
    # Meanwhile a person takes the line's GL account off the list: today's checks would say GL_UNKNOWN.
    gone = ground_truth["line_items"][0]["predicted_gl_code"]
    store.save_accounts("gl_accounts", [r for r in store.list_accounts("gl_accounts") if r["code"] != gone])
    second, report = _saved(store, tmp_path, ground_truth | {"invoice_number": "NW-2"}, held, "b.pdf")
    assert report.issues == []  # checked against the lists the reader holds
    assert store.stale_checks(store.checks_stamp()) == [first, second]  # both to be checked again
    stored = store.get_invoice(second)["validation"]
    assert stored["checks_stamp"] != store.checks_stamp() and "reference_stamp" not in stored
    # Read with today's lists, an invoice is stamped as checked against them.
    third, report = _saved(store, tmp_path, ground_truth | {"invoice_number": "NW-3"}, store.reference_data(), "c.pdf")
    assert "GL_UNKNOWN" in {i.code for i in report.issues}
    assert third not in store.stale_checks(store.checks_stamp())


def test_a_chart_imported_during_a_run_leaves_the_runs_invoices_to_check_again(store, tmp_path, ground_truth):
    load_sample_setup(store)
    held = store.reference_data()
    store.import_chart(chart.chart_rows(dict(zip(cf.CHART_HEADERS, row, strict=True)) for row in cf.chart_cells()).rows)
    coded = copy.deepcopy(ground_truth)
    invoice, report = _saved(store, tmp_path, coded, held)
    assert report.issues == [] and held.pairs is None
    assert store.stale_checks(store.checks_stamp()) == [invoice]
    today = validate_coding(InvoiceCoding.model_validate(coded), store.reference_data())
    assert "ACCOUNT_NOT_IN_CHART" in {i.code for i in today.issues}


def test_a_page_read_again_with_reference_data_held_since_before_a_change_is_checked_again(
    store, tmp_path, ground_truth
):
    load_sample_setup(store)
    invoice, _ = _saved(store, tmp_path, ground_truth, store.reference_data())
    held = store.reference_data()
    store.set_setting("our_province", "BC")
    report = validate_coding(InvoiceCoding.model_validate(ground_truth), held)
    assert store.replace_proposal(invoice, ground_truth, report, None)
    assert store.stale_checks(store.checks_stamp()) == [invoice]
    assert "reference_stamp" not in store.get_invoice(invoice)["validation"]
    assert store.replace_proposal(invoice, ground_truth, validate_coding(
        InvoiceCoding.model_validate(ground_truth), store.reference_data()), None)  # fmt: skip
    assert store.stale_checks(store.checks_stamp()) == []


def test_reference_data_read_from_files_has_no_stamp_and_is_stamped_as_before(store, tmp_path, ground_truth, reference):
    load_sample_setup(store)
    assert reference.stamp == "" and store.reference_data().stamp == store.reference_stamp() != ""
    report = validate_coding(InvoiceCoding.model_validate(ground_truth), reference)
    assert "reference_stamp" not in report.to_dict()
    invoice, _ = _saved(store, tmp_path, ground_truth, reference)
    assert store.get_invoice(invoice)["validation"]["checks_stamp"] == store.checks_stamp()
