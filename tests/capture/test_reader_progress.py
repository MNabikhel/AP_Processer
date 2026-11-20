"""A progress billing read off its page (``capture.reader.progress``, D-273): the work this period, before and to date,
the holdback and its percent, the taxes and the amount due, by their labels on each row of the page; a holdback
release; and an invoice that is none."""

import pytest

from ap_coder.capture import build_layout
from ap_coder.capture.reader.progress import layout_rows, read_progress
from tests.pdfs import text_pdf

HEAD = [(50, 60, "Westmeadow Building Services Inc."), (400, 60, "INVOICE"), (400, 76, "Invoice No: WBS-P-0102"),
        (400, 92, "Date: 2026-10-31"), (400, 108, "PO Number: 41201")]  # fmt: skip


def _rows(tmp_path, rows, name="p.pdf"):
    """``rows`` ((label, amount), or a line of text) printed under the head, each label at the left and its amount
    far to its right, as the page's rows read them."""
    items = list(HEAD)
    for n, row in enumerate(rows):
        y = 200 + 16 * n
        items += [(50, y, row[0]), (480, y, row[1])] if isinstance(row, tuple) else [(50, y, row)]
    return layout_rows(build_layout(text_pdf(tmp_path, items, name=name)))


APPLICATION = [("1. Original contract sum", "250,000.00"), ("2. Net change by change orders", "0.00"),
               ("3. Contract sum to date", "250,000.00"), ("4. Total completed to date", "120,000.00"),
               ("5. Less previous applications", "45,000.00"), ("6. Work completed this period", "75,000.00"),
               ("7. Less holdback 10%", "(7,500.00)"), ("8. Net amount this period", "67,500.00"),
               ("9. GST 5% on 75,000.00", "3,750.00"), ("10. Current payment due", "71,250.00")]  # fmt: skip


def test_an_application_for_payment_is_read_line_by_line(tmp_path):
    """A G702-style application: its numbered lines; the contract sum to date is no work to date, the net amount this
    period no work this period. It adds up: 75,000.00 - 7,500.00 + 3,750.00 = 71,250.00, and 120,000.00 - 45,000.00
    is the work this period."""
    read = read_progress(_rows(tmp_path, ["Application for Payment No. 2", *APPLICATION]))
    assert read == {"this_period": 75000.0, "previous": 45000.0, "to_date": 120000.0, "holdback": 7500.0,
                    "holdback_rate": 10.0, "taxes": {"GST": 3750.0}, "amount_due": 71250.0, "release": False,
                    "adds_up": True}  # fmt: skip


def test_a_progress_invoice_with_less_holdback_under_its_subtotal_is_read(tmp_path):
    """The simple Canadian progress invoice: a line table, then "Subtotal", "Less holdback 10%", the HST on the work
    and the amount due; what was billed before and to date printed above the table."""
    rows = [
        ("Billed to date", "200,000.00"),
        ("Previously billed", "120,000.00"),
        "Description",
        ("Mechanical rough-in - work this period", "80,000.00"),
        ("Subtotal", "80,000.00"),
        ("Less holdback 10%", "(8,000.00)"),
        ("HST 13% on 80,000.00", "10,400.00"),
        ("Amount due", "82,400.00"),
    ]
    read = read_progress(_rows(tmp_path, rows))
    assert (read["this_period"], read["previous"], read["to_date"], read["holdback"], read["holdback_rate"],
            read["taxes"], read["amount_due"], read["adds_up"]) == (80000.0, 120000.0, 200000.0, 8000.0, 10.0,
                                                                    {"HST": 10400.0}, 82400.0, True)  # fmt: skip


@pytest.mark.parametrize("label", ["Less holdback 10%", "Holdback (10%)", "Less: Holdback (10%)", "Less 10% holdback",
                                   "Retainage (10% of completed work)", "Less retainage 10%", "Retention 10%",
                                   "Moins retenue 10 %", "Retenue de garantie 10 %"])  # fmt: skip
def test_a_holdback_is_read_by_any_of_its_labels(label):
    read = read_progress(["Subtotal   1,000.00", f"{label}   (100.00)", "GST 5%   50.00", "Amount due   950.00"])
    assert (read["holdback"], read["holdback_rate"], read["adds_up"]) == (100.0, 10.0, True), label


def test_the_gst_on_the_work_less_the_holdback_adds_up_too():
    """The tax is the invoice's, whatever base it is charged on: 60,000.00 - 6,000.00 + 2,700.00 = 56,700.00."""
    read = read_progress(["Work completed this period   60,000.00", "Less holdback 10%   (6,000.00)",
                          "Net amount this period   54,000.00", "GST 5% on 54,000.00   2,700.00",
                          "Current payment due   56,700.00"])  # fmt: skip
    assert (read["this_period"], read["taxes"], read["amount_due"], read["adds_up"]) == (
        60000.0, {"GST": 2700.0}, 56700.0, True)  # fmt: skip


def test_a_billing_whose_amounts_do_not_add_up_is_read_but_said_so():
    """The work to date less the work before is not the work this period, or the amount due is not the work less the
    holdback plus the tax: read, never used (``adds_up`` False)."""
    wrong_to_date = ["Total completed to date   130,000.00", "Less previous applications   45,000.00",
                     "Work completed this period   75,000.00", "Less holdback 10%   (7,500.00)",
                     "GST 5%   3,750.00", "Current payment due   71,250.00"]  # fmt: skip
    assert read_progress(wrong_to_date)["adds_up"] is False
    assert read_progress(["Subtotal   1,000.00", "Less holdback 10%   (100.00)", "GST 5%   50.00",
                          "Amount due   1,050.00"])["adds_up"] is False  # fmt: skip
    no_due = read_progress(["Subtotal   1,000.00", "Less holdback 10%   (100.00)", "GST 5%   50.00"])
    assert no_due["holdback"] == 100.0 and no_due["adds_up"] is False


def test_a_holdback_under_a_printed_total_is_a_holdback_billing_that_adds_up():
    """B-2492's invoice: "Total 621.50 | Less 10% holdback (100.00) | Balance due 521.50"."""
    read = read_progress(["Subtotal   550.00", "HST 13%   71.50", "Total   621.50", "Less 10% holdback   (100.00)",
                          "Balance Due   521.50"])  # fmt: skip
    assert (read["this_period"], read["holdback"], read["amount_due"], read["adds_up"]) == (550.0, 100.0, 521.5, True)


@pytest.mark.parametrize(
    "line",
    [
        "Holdback release - Phase 1   25,000.00",
        "Release of holdback   25,000.00",
        "10% retainage release   25,000.00",
        "Libération de la retenue   25 000,00",
    ],
)
def test_a_line_billing_a_holdback_held_before_is_a_release(line):
    read = read_progress(["Description   Amount", line, "Subtotal   25,000.00", "GST 5%   1,250.00",
                          "Amount due   26,250.00"])  # fmt: skip
    assert read["release"] is True and read["holdback"] == 0.0, line


def test_an_invoice_that_holds_nothing_back_is_no_progress_billing():
    """A plain invoice; a line item named for a retainage ("Retainage billing - Phase 2", B-2492: no holdback taken
    off); a holdback of nothing; a sentence about the holdback with no amount; and a release spoken of in a sentence,
    with no amount billed for it."""
    plain = ["Consulting services   550.00", "Subtotal   550.00", "HST 13%   71.50", "Total Due   621.50"]
    assert read_progress(plain) is None
    assert read_progress(["Retainage billing - Phase 2   550.00", *plain[1:]]) is None
    assert read_progress([*plain, "Holdback   0.00"]) is None
    assert read_progress([*plain, "Holdback of 10% is payable within 60 days of completion."]) is None
    assert read_progress([*plain, "The holdback release will be invoiced at substantial performance."]) is None


def test_a_scans_tax_rows_with_the_spaces_lost_are_read(tmp_path):
    """B-2890: a scan's OCR runs a tax's name into its rate ("9.GST5%on45,000.00", "HST13%on80,000.00"): each is still
    the billing's tax, so it adds up and its amounts are used (it read no tax, and the billing was coded at nothing)."""
    application = ["4.Totalcompleted todate   45,000.00", "5.Less previous applications   0.00",
                   "6.Workcompleted thisperiod   45,000.00", "7.Lessholdback10%   (4,500.00)",
                   "8.Net amount thisperiod   40,500.00", "9.GST5%on45,000.00   2,250.00",
                   "10.Current payment due   42,750.00"]  # fmt: skip
    read = read_progress(application)
    assert (read["taxes"], read["adds_up"]) == ({"GST": 2250.0}, True)
    invoice = ["Mechanical rough-in-work thisperiod   80,000.00", "Subtotal   80,000.00",
               "Lessholdback 10%   (8,000.00)", "HST13%on80,000.00   10,400.00", "Amountdue   82,400.00"]  # fmt: skip
    read = read_progress(invoice)
    assert (read["taxes"], read["amount_due"], read["adds_up"]) == ({"HST": 10400.0}, 82400.0, True)


def test_the_total_of_a_billing_holding_back_is_held_for_a_person_to_check(tmp_path):
    """B-2946: an application for payment prints no total before its holdback (its work this period, the holdback,
    the GST, the payment due), so the coding's total, the one before the holdback (D-274), was no value read: nothing
    held it for a person, though B-2492 holds the total of any invoice taking something off before the amount to pay
    (D-47). The capture's total is the one coded, to check, saying why; a total read above a holdback keeps its
    reading, one that holds nothing back stays as it is."""
    from ap_coder.capture import analyze

    taken_off = "something is taken off before the amount to pay (a deposit, a holdback, a commission)"
    items = list(HEAD)
    for n, (label, amount) in enumerate(APPLICATION):
        items += [(50, 200 + 16 * n, label), (480, 200 + 16 * n, amount)]
    total = analyze(text_pdf(tmp_path, items, name="application.pdf"), tables=False).fields["grand_total"]
    assert (total.value, total.status) == (78750.0, "check")
    assert any(r.startswith(taken_off) for r in total.reasons), total.reasons
    printed = [("Subtotal", "550.00"), ("HST 13%", "71.50"), ("Total", "621.50"), ("Less 10% holdback", "(100.00)"),
               ("Balance Due", "521.50")]  # fmt: skip
    items = list(HEAD)
    for n, (label, amount) in enumerate(printed):
        items += [(330, 300 + 16 * n, label), (480, 300 + 16 * n, amount)]
    total = analyze(text_pdf(tmp_path, items, name="printed.pdf"), tables=False).fields["grand_total"]
    assert (total.value, total.status, sum(r.startswith(taken_off) for r in total.reasons)) == (621.5, "check", 1)
    assert total.boxes  # (as read)
    nothing = [("Billed to date", "200,000.00"), ("Previously billed", "120,000.00"), ("Subtotal", "80,000.00"),
               ("HST 13% on 80,000.00", "10,400.00"), ("Amount due", "90,400.00")]  # fmt: skip
    items = list(HEAD)
    for n, (label, amount) in enumerate(nothing):
        items += [(330, 300 + 16 * n, label), (480, 300 + 16 * n, amount)]
    total = analyze(text_pdf(tmp_path, items, name="nothing.pdf"), tables=False).fields["grand_total"]
    assert (total.value, total.status) == (90400.0, "verified") and not any(r.startswith(taken_off)
                                                                           for r in total.reasons)  # fmt: skip
