"""Vendor statement reconciliation."""

import csv
import json

import pytest

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.core.config import Settings
from ap_coder.core.memory import vendor_key
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import load_sample_setup
from ap_coder.erp import statements as stm
from tests.conftest import DATA, SAMPLES, copy_database


def _inv(i, number, total, date="2026-09-10", status="approved"):
    return {"id": i, "invoice_number": number, "grand_total": total, "invoice_date": date, "status": status}


def test_columns_are_recognised():
    cols = stm.map_columns(["Doc Date", "Invoice No.", "Amount", "Open Balance", "Type"])
    assert cols == {"number": "Invoice No.", "date": "Doc Date", "amount": "Amount", "balance": "Open Balance",
                    "type": "Type"}  # fmt: skip


def test_reconcile_finds_matches_differences_and_gaps():
    statement = [
        {"No": "INV-00101", "Date": "2026-09-01", "Amt": "1,000.00"},
        {"No": "102", "Date": "2026-09-05", "Amt": "250.00"},
        {"No": "103", "Date": "2026-09-20", "Amt": "75.00"},
        {"No": "", "Date": "2026-09-25", "Amt": "(500.00)", "Kind": "Payment"},
        {"No": "", "Date": "", "Amt": ""},  # blank row
    ]
    invoices = [
        _inv(1, "101", 1000.0),
        _inv(2, "INV-102", 245.0),
        _inv(3, "104", 60.0, "2026-09-12"),
        _inv(4, "105", 80.0, "2026-12-01"),  # outside the statement period
        _inv(5, "103", 75.0, status="rejected"),  # a rejected invoice does not count
    ]
    cols = {"number": "No", "date": "Date", "amount": "Amt", "type": "Kind"}
    rec = stm.reconcile(statement, cols, invoices)
    got = {(li.status, li.number) for li in rec.lines}
    assert got == {
        (stm.MATCHED, "INV-00101"), (stm.DIFFERS, "102"), (stm.NOT_RECEIVED, "103"), (stm.PAYMENT, ""),
        (stm.NOT_ON_STATEMENT, "104"),
    }  # fmt: skip
    differs = next(li for li in rec.lines if li.status == stm.DIFFERS)
    assert differs.difference == 5.0 and differs.invoice_id == 2
    assert rec.lines[0].status == stm.DIFFERS  # problems first
    assert "Amount differs" in stm.to_csv(rec).decode("utf-8-sig")


def test_negative_amount_formats():
    assert stm.parse_amount("(1,234.50)") == -1234.5
    assert stm.parse_amount("1234.50-") == -1234.5
    assert stm.parse_amount("99.00 CR") == -99.0
    assert stm.parse_amount("$1,000") == 1000.0
    assert stm.parse_amount("n/a") is None and stm.parse_amount(float("nan")) is None


def test_a_payment_word_in_a_description_does_not_turn_an_invoice_into_a_payment():
    statement = [
        {"Date": "2026-09-14", "Invoice": "E-5521", "Description": "14/2 NMD90 wire, 75 m", "Amount": "412.50"},
        {"Date": "2026-09-16", "Invoice": "E-5530", "Description": "Brass check valves", "Amount": "96.00"},
        {"Date": "2026-09-25", "Invoice": "88120", "Description": "Payment - thank you", "Amount": "-300.00"},
        {"Date": "2026-09-26", "Invoice": "", "Description": "EFT received", "Amount": "-200.00"},
    ]
    cols = stm.map_columns(list(statement[0]))
    rec = stm.reconcile(statement, cols, [_inv(11, "E-5521", 412.5), _inv(12, "E-5530", 96.0)])
    assert sorted((li.status, li.number) for li in rec.lines) == [
        (stm.MATCHED, "E-5521"), (stm.MATCHED, "E-5530"), (stm.PAYMENT, ""), (stm.PAYMENT, "88120"),
    ]  # fmt: skip
    # A real Type column still says what a line is, even with an invoice's number.
    typed = [{"No": "E-5521", "Type": "Payment", "Amt": "-412.50"}]
    (line,) = stm.reconcile(typed, {"number": "No", "type": "Type", "amount": "Amt"}, [_inv(11, "E-5521", 412.5)]).lines
    assert line.status == stm.PAYMENT


def test_a_payment_line_with_the_invoices_number_is_a_payment():
    """Statements often print the invoice's number on its payment line: with a payment word and the opposite sign
    of that invoice (or the invoice already matched) it is a payment, not an invoice AP never received."""
    statement = [
        {"Date": "2026-09-01", "Invoice": "1001", "Description": "Brass check valves", "Amount": "500.00"},
        {"Date": "2026-09-20", "Invoice": "1001", "Description": "Payment - thank you", "Amount": "-500.00"},
        {"Date": "2026-09-21", "Invoice": "1002", "Description": "Payment received", "Amount": "-80.00"},
    ]
    cols = stm.map_columns(list(statement[0]))
    rec = stm.reconcile(statement, cols, [_inv(7, "1001", 500.0), _inv(8, "1002", 80.0)])
    assert sorted((li.status, li.number) for li in rec.lines) == [
        (stm.MATCHED, "1001"), (stm.NOT_ON_STATEMENT, "1002"), (stm.PAYMENT, "1001"), (stm.PAYMENT, "1002"),
    ]  # fmt: skip
    assert rec.count(stm.NOT_RECEIVED) == 0


def test_the_same_invoice_is_not_matched_twice():
    statement = [{"n": "7", "a": "10"}, {"n": "7", "a": "10"}]
    rec = stm.reconcile(statement, {"number": "n", "amount": "a"}, [_inv(1, "7", 10.0)])
    assert [li.status for li in rec.lines] == [stm.NOT_RECEIVED, stm.MATCHED]


def test_sample_statement_against_the_demo(tmp_path, demo_template):
    store = copy_database(demo_template, tmp_path / "a.db")
    with (DATA / "sample_statement_northwind.csv").open(encoding="utf-8") as fh:
        records = list(csv.DictReader(fh))
    invoices = store.vendor_invoices(vendor_key("Northwind IT Solutions Inc."))
    rec = stm.reconcile(records, stm.map_columns(list(records[0])), invoices)
    assert {li.number: li.status for li in rec.lines if li.number} == {
        "NW-2026-0912": stm.MATCHED, "CN-2026-0047": stm.MATCHED, "NW-2026-0938": stm.NOT_RECEIVED,
    }  # fmt: skip
    assert rec.count(stm.PAYMENT) == 1


def test_a_payment_line_carrying_the_invoice_number_never_takes_the_invoice():
    """A payment or credit applied to an invoice often carries its number, with its own Type (PMT, CHQ) or in a
    Credit column, before or after the invoice: its sign, opposite to the invoice's, makes it the payment."""
    invoices = [_inv(1, "1001", 500.0, "2026-09-02"), _inv(2, "1002", 250.0, "2026-09-09")]
    typed = [
        {"Date": "2026-09-20", "Type": "PMT", "Ref": "1001", "Amount": "-500.00"},  # newest first
        {"Date": "2026-09-02", "Type": "INV", "Ref": "1001", "Amount": "500.00"},
        {"Date": "2026-09-09", "Type": "INV", "Ref": "1002", "Amount": "250.00"},
        {"Date": "2026-09-25", "Type": "PMT", "Ref": "CHQ 88123", "Amount": "-250.00"},
    ]
    debit_credit = [
        {"Date": "2026-09-20", "Invoice": "1001", "Debit": "", "Credit": "500.00"},
        {"Date": "2026-09-02", "Invoice": "1001", "Debit": "500.00", "Credit": ""},
        {"Date": "2026-09-09", "Invoice": "1002", "Debit": "250.00", "Credit": ""},
    ]
    described = [
        {"Date": "2026-09-20", "Invoice": "1001", "Description": "Payment - thank you", "Amount": "-500.00"},
        {"Date": "2026-09-02", "Invoice": "1001", "Description": "Brass check valves", "Amount": "500.00"},
        {"Date": "2026-09-09", "Invoice": "1002", "Description": "Brass check valves", "Amount": "250.00"},
    ]
    for statement in (typed, debit_credit, described):
        rec = stm.reconcile(statement, stm.map_columns(list(statement[0])), invoices)
        assert {(li.status, li.invoice_id) for li in rec.lines if li.status != stm.PAYMENT} == {
            (stm.MATCHED, 1), (stm.MATCHED, 2),
        }, statement  # fmt: skip
        assert rec.count(stm.NOT_RECEIVED) == rec.count(stm.DIFFERS) == 0
    # A credit note AP holds under the same number still matches the credit line.
    rec = stm.reconcile(debit_credit, stm.map_columns(list(debit_credit[0])), [*invoices, _inv(3, "1001", -500.0)])
    assert {(li.status, li.invoice_id) for li in rec.lines} == {(stm.MATCHED, 1), (stm.MATCHED, 2), (stm.MATCHED, 3)}


def _coded(store, stem):
    doc = json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text())
    out, _ = finalise_coding(InvoiceCoding.model_validate(doc), store.reference_data(), Settings(), store=store)
    return out


def test_a_statement_line_matches_the_approved_invoice_not_a_resent_copy_in_review(store, tmp_path):
    """The vendor sent the bill again: its copy in review (the newest) never takes the statement line from the
    approved, exported original, and is not listed "Not on statement" once its number matched."""
    load_sample_setup(store)
    out = _coded(store, "northwind_ON_HST_NW-2026-0912")
    original = store.add_invoice(tmp_path / "NW-2026-0912.pdf", out, {})
    store.approve_invoice(original, out, "Jane")
    store.create_export_batch([original], "csv")
    store.add_invoice(tmp_path / "copy.pdf", _coded(store, "northwind_ON_HST_NW-2026-0912"), {})
    records = [{"Invoice": "NW-2026-0912", "Date": "2026-09-12", "Amount": "18,017.85"}]
    key = store.get_invoice(original)["vendor_key"]
    rec = stm.reconcile(records, stm.map_columns(list(records[0])), store.vendor_invoices(key))
    assert [(li.status, li.invoice_id) for li in rec.lines] == [(stm.MATCHED, original)]


def test_a_statement_whose_dates_read_both_ways_still_lists_what_it_leaves_out():
    """Every day 12 or less: the matched invoices' own dates tell the order (month first here)."""
    invoices = [_inv(1, "1001", 100.0, "2026-09-02"), _inv(2, "1002", 200.0, "2026-09-05"),
                _inv(3, "1003", 300.0, "2026-09-08")]  # fmt: skip
    records = [{"Invoice": "1001", "Date": "09/02/2026", "Amount": "100.00"},
               {"Invoice": "1002", "Date": "09/05/2026", "Amount": "200.00"},
               {"Invoice": "1004", "Date": "09/10/2026", "Amount": "50.00"}]  # fmt: skip
    rec = stm.reconcile(records, stm.map_columns(list(records[0])), invoices)
    assert [li.invoice_id for li in rec.lines if li.status == stm.NOT_ON_STATEMENT] == [3]
    assert {li.date for li in rec.lines} == {"2026-09-02", "2026-09-05", "2026-09-08", "2026-09-10"}
    assert not rec.period_unknown
    # Nothing matched to tell by: the period is unknown, and the page says so rather than looking clean.
    rec = stm.reconcile(records, stm.map_columns(list(records[0])), [_inv(3, "1003", 300.0, "2026-09-08")])
    assert rec.period_unknown and rec.count(stm.NOT_ON_STATEMENT) == 0


def _doc(i, number, date, total, status="approved", batch=None):
    return {**_inv(i, number, total, date, status), "export_batch": batch}


@pytest.mark.parametrize("left_over", [(-100.0, None), (250.0, 4)], ids=["credit note", "number used again"])
def test_a_credit_note_or_a_reused_number_left_over_is_still_not_on_statement(left_over):
    """B-2510: only a copy of the invoice a line took (same number, same total) is left out of "Not on statement": a
    credit note numbered as its invoice, or another bill under a number the vendor used again, is still listed."""
    cols = {"number": "No", "date": "Date", "amount": "Amount"}
    records = [{"No": "1009", "Date": "2026-10-01", "Amount": "5.00"},
               {"No": "1010", "Date": "2026-10-31", "Amount": "6.00"},
               {"No": "1001", "Date": "2026-10-05", "Amount": "100.00"}]  # fmt: skip
    invoices = [_doc(22, "1001", "2026-10-20", left_over[0], batch=left_over[1]),
                _doc(21, "1001", "2026-10-05", 100.0, batch=3), _doc(20, "1010", "2026-10-31", 6.0, batch=3),
                _doc(19, "1009", "2026-10-01", 5.0, batch=3)]  # fmt: skip
    rec = stm.reconcile(records, cols, invoices)
    assert [li.invoice_id for li in rec.lines if li.status == stm.NOT_ON_STATEMENT] == [22]
    assert {li.invoice_id for li in rec.lines if li.status == stm.MATCHED} == {19, 20, 21}


@pytest.mark.parametrize("amount, older, status", [("60.00", 80.0, "review"), ("100.00", 100.0, "approved")],
                         ids=["a balance", "the same amount"])  # fmt: skip
def test_a_line_takes_the_invoice_of_its_own_date_before_an_older_one_exported(amount, older, status):
    """B-2510: #1001 of 2025 (exported) never takes the 2026-10-05 line from #1001 of 2026, whether the statement's
    amount is a balance (it differs from both) or both bills have the same amount (a monthly fee)."""
    cols = {"number": "No", "date": "Date", "amount": "Amount"}
    records = [{"No": "1001", "Date": "2026-10-05", "Amount": amount},
               {"No": "1002", "Date": "2026-10-30", "Amount": "5.00"}]  # fmt: skip
    invoices = [_doc(9, "1002", "2026-10-30", 5.0), _doc(5, "1001", "2026-10-05", 100.0, status),
                _doc(1, "1001", "2025-10-05", older, batch=1)]  # fmt: skip
    rec = stm.reconcile(records, cols, invoices)
    assert next(li.invoice_id for li in rec.lines if li.number == "1001") == 5


def test_a_line_whose_date_reads_both_ways_takes_the_invoice_of_either_reading_first():
    """B-2510: on a statement whose every date reads both ways ("05/10/2026"), last year's exported #1001 took the
    line of this year's #1001, which was then listed "Not on statement": a date not yet read fits an invoice of
    either reading."""
    cols = {"number": "No", "date": "Date", "amount": "Amount"}
    records = [{"No": "1001", "Date": "05/10/2026", "Amount": "60.00"},
               {"No": "1002", "Date": "09/10/2026", "Amount": "5.00"}]  # fmt: skip
    invoices = [_doc(9, "1002", "2026-10-09", 5.0), _doc(5, "1001", "2026-10-05", 100.0, "review"),
                _doc(1, "1001", "2025-10-05", 80.0, batch=1)]  # fmt: skip
    rec = stm.reconcile(records, cols, invoices)
    assert next(li.invoice_id for li in rec.lines if li.number == "1001") == 5


def test_an_open_items_statement_counts_the_invoices_exported_rather_than_listing_them():
    """D-242: a statement of open items leaves out what is paid. Ticked, the invoices AP Coder exported (an export
    batch: sent to the ERP or on a keying sheet) that it leaves out are counted, not listed "Not on statement"; the
    rest of the comparison is as without it."""
    cols = {"number": "No", "date": "Date", "amount": "Amount"}
    records = [{"No": "1001", "Date": "2026-10-01", "Amount": "100.00"},
               {"No": "1009", "Date": "2026-10-31", "Amount": "9.00"}]  # fmt: skip
    invoices = [_doc(4, "1004", "2026-10-20", 40.0, "review"), _doc(3, "1003", "2026-10-10", 30.0, batch=4),
                _doc(2, "1002", "2026-10-05", 20.0, batch=3), _doc(1, "1001", "2026-10-01", 100, batch=3)]  # fmt: skip

    def left_out(rec):
        return [li.invoice_id for li in rec.lines if li.status == stm.NOT_ON_STATEMENT]

    def the_rest(rec):
        return [(li.status, li.number, li.invoice_id) for li in rec.lines if li.status != stm.NOT_ON_STATEMENT]

    plain = stm.reconcile(records, cols, invoices)
    assert left_out(plain) == [2, 3, 4] and plain.exported_left_out == 0
    rec = stm.reconcile(records, cols, invoices, open_items=True)
    assert left_out(rec) == [4] and rec.exported_left_out == 2
    assert the_rest(rec) == the_rest(plain) == [(stm.NOT_RECEIVED, "1009", None), (stm.MATCHED, "1001", 1)]


def test_the_csv_of_an_open_items_statement_says_how_many_exported_invoices_it_leaves_out():
    """D-242: the CSV leaves the exported invoices out as the page does, and says so in a last row, worded as on the
    page; with none left out (or unticked), no such row."""
    cols = {"number": "No", "date": "Date", "amount": "Amount"}
    records = [{"No": "1001", "Date": "2026-10-01", "Amount": "100.00"},
               {"No": "1009", "Date": "2026-10-31", "Amount": "9.00"}]  # fmt: skip
    invoices = [_doc(3, "1003", "2026-10-10", 30.0, batch=4), _doc(1, "1001", "2026-10-01", 100.0, batch=3)]

    def rows(rec):
        return list(csv.reader(stm.to_csv(rec).decode("utf-8-sig").splitlines()))

    ticked = stm.reconcile(records, cols, invoices, open_items=True)
    assert ticked.exported_note == "1 invoice already exported is not expected on an open-items statement."
    assert rows(ticked)[-1] == [ticked.exported_note] + [""] * 8 and len(rows(ticked)) == 4  # header, 2 lines, note
    plain = stm.reconcile(records, cols, invoices)
    assert plain.exported_note == "" and [r[0] for r in rows(plain)[1:]] == ["Not received", "Not on statement",
                                                                              "Matched"]  # fmt: skip
    two = stm.reconcile(records, cols, [*invoices, _doc(2, "1002", "2026-10-05", 20.0, batch=3)], open_items=True)
    assert two.exported_note == "2 invoices already exported are not expected on an open-items statement."
