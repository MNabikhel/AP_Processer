"""Vendor master and payment-fraud / duplicate signals."""

import copy

import pytest

from ap_coder.config import Settings
from ap_coder.pipeline import finalise_coding
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store
from ap_coder.vendors import norm_invoice_number


def _codes(store, doc, reference, exclude=None):
    _, report = finalise_coding(InvoiceCoding.model_validate(doc), reference, Settings(), store=store,
                                exclude_invoice_id=exclude)  # fmt: skip
    return {i.code: i.severity for i in report.issues}, report


def _approved(store, doc, tmp_path, name):
    invoice_id = store.add_invoice(tmp_path / name, doc, {})
    store.approve_invoice(invoice_id, doc, "jane")
    return invoice_id


def _variant(doc, number, total=None, date=None, gst=None):
    d = copy.deepcopy(doc)
    d["invoice_number"] = number
    if date:
        d["invoice_date"] = date
    if gst is not None:
        d["gst_hst_registration_number"] = gst
    if total is not None:
        factor = total / d["grand_total"]
        for li in d["line_items"]:
            li["amount"] = round(li["amount"] * factor, 2)
            li["unit_price"] = round(li["amount"] / li["quantity"], 2)
        d["subtotal"] = round(sum(li["amount"] for li in d["line_items"]), 2)
        d["tax_lines"][0]["taxable_amount"] = d["subtotal"]
        d["tax_lines"][0]["tax_amount"] = round(d["subtotal"] * d["tax_lines"][0]["rate"], 2)
        d["tax_total"] = d["tax_lines"][0]["tax_amount"]
        d["grand_total"] = round(d["subtotal"] + d["tax_total"], 2)
    return d


@pytest.mark.parametrize(("a", "b"), [("INV-00123", "123"), ("#123", "inv 123"), ("Facture no 0045", "45")])
def test_invoice_numbers_match_regardless_of_formatting(a, b):
    assert norm_invoice_number(a) == norm_invoice_number(b)


def test_formatting_variants_count_as_duplicates(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    store.add_invoice(tmp_path / "a.pdf", _variant(ground_truth, "NW-2026-0912"), {})
    codes, _ = _codes(store, _variant(ground_truth, "NW 2026 0912"), reference)
    assert codes.get("DUPLICATE_INVOICE") == "error"


def test_first_invoice_from_a_vendor_is_noted_without_penalty(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    other = {**ground_truth, "vendor_name": "Some Other Vendor Ltd.", "invoice_number": "OTHER-1"}
    store.add_invoice(tmp_path / "o.pdf", other, {})
    codes, report = _codes(store, ground_truth, reference)
    assert codes.get("VENDOR_NEW") == "info"
    assert report.adjusted_confidence == report.model_confidence  # info costs nothing


def test_same_bill_under_another_vendor_name(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    first = store.add_invoice(tmp_path / "o.pdf", {**ground_truth, "vendor_name": "Northwind IT Solns"}, {})
    codes, report = _codes(store, _variant(ground_truth, "NW 2026 0912"), reference)
    assert codes.get("DUPLICATE_OTHER_VENDOR") == "warning"
    assert f"#{first}" in next(i.message for i in report.issues if i.code == "DUPLICATE_OTHER_VENDOR")
    assert (
        "DUPLICATE_OTHER_VENDOR" not in _codes(store, _variant(ground_truth, "NW 2026 0912", total=99.0), reference)[0]
    )


def test_changed_gst_number_is_flagged(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    _approved(store, _variant(ground_truth, "A-1", date="2026-06-01"), tmp_path, "a.pdf")
    codes, _ = _codes(store, _variant(ground_truth, "A-2", gst="987654321 RT0001"), reference)
    assert codes.get("VENDOR_TAX_NUMBER_CHANGED") == "warning"
    same, _ = _codes(store, _variant(ground_truth, "A-3", gst="123456782RT0001"), reference)
    assert "VENDOR_TAX_NUMBER_CHANGED" not in same  # spacing differences don't count


def test_unusual_amount_and_same_amount_different_number(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    for i, date in enumerate(["2026-05-01", "2026-06-01", "2026-07-01"]):
        _approved(store, _variant(ground_truth, f"R-{i}", total=1000.0, date=date), tmp_path, f"r{i}.pdf")
    codes, _ = _codes(store, _variant(ground_truth, "BIG-1", total=9000.0, date="2026-08-01"), reference)
    assert codes.get("AMOUNT_UNUSUAL") == "warning"
    codes, _ = _codes(store, _variant(ground_truth, "NEW-9", total=1000.0, date="2026-07-15"), reference)
    assert codes.get("POSSIBLE_DUPLICATE_AMOUNT") == "warning"
    far, _ = _codes(store, _variant(ground_truth, "NEW-10", total=1000.0, date="2026-12-15"), reference)
    assert "POSSIBLE_DUPLICATE_AMOUNT" not in far  # monthly fees months apart are normal


def test_vendor_on_hold_blocks(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    _approved(store, _variant(ground_truth, "A-1"), tmp_path, "a.pdf")
    store.save_vendor("northwind it solutions", "Northwind IT Solutions Inc.", "on_hold", notes="bank change pending",
                      actor="jane")  # fmt: skip
    codes, report = _codes(store, _variant(ground_truth, "A-2"), reference)
    assert codes.get("VENDOR_ON_HOLD") == "error" and report.requires_review
    assert store.events(actions=["vendor_updated"])[0]["detail"]["status"] == "on_hold"
    summary = store.vendor_summaries()[0]
    assert summary["status"] == "on_hold" and summary["approved"] == 1 and summary["lessons"] == 5


def test_expected_gst_number_from_the_vendor_master_wins(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    _approved(store, _variant(ground_truth, "A-1"), tmp_path, "a.pdf")
    store.save_vendor("northwind it solutions", "Northwind IT Solutions Inc.", expected_gst="111111111RT0001")
    codes, _ = _codes(store, _variant(ground_truth, "A-2"), reference)
    assert codes.get("VENDOR_TAX_NUMBER_CHANGED") == "warning"


@pytest.mark.parametrize(
    "a, b, same",
    [
        ("123456789", "123456789 RT0001", True),  # the vendor master keeps only the Business Number
        ("123456789RT0001", "123 456 789", True),
        ("123456789 RT 0001", "123456789rt0001", True),
        ("123456789RT0001", "123456789RT0002", False),  # two program accounts of one business
        ("123456789", "987654321RT0001", False),
        ("", "123456789RT0001", False),
    ],
)
def test_tax_numbers_compare_by_business_number(a, b, same):
    from ap_coder.vendors import same_tax_number

    assert same_tax_number(a, b) is same


def test_a_vendor_master_with_only_the_business_number(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    _approved(store, _variant(ground_truth, "A-1"), tmp_path, "a.pdf")
    bn = ground_truth["gst_hst_registration_number"].replace(" ", "")[:9]
    row = {"vendor_name": ground_truth["vendor_name"], "gst": bn, "status": "active", "status_given": False,
           "erp_id": None, "terms": None, "default_gl": None}  # fmt: skip
    store.import_vendor_master([row])
    codes, _ = _codes(store, _variant(ground_truth, "A-2"), reference)
    assert "VENDOR_TAX_NUMBER_CHANGED" not in codes  # "123456789" and "123456789 RT0001" are the same registration
    renamed = {**_variant(ground_truth, "A-3"), "vendor_name": "NW IT Sol."}
    codes, _ = _codes(store, renamed, reference)
    assert "VENDOR_MATCHED_BY_TAX_NUMBER" in codes and "VENDOR_NOT_IN_MASTER" not in codes


@pytest.mark.parametrize(
    "a, b, same",
    [
        ("004-12345-1234567", "Inst 004 Transit 12345 Account 1234567", True),
        ("Transit 12345, Institution 004, Account 1234567", "004 12345 1234567", True),
        ("00412345 1234567", "004-12345-1234567", True),
        ("004-12345-1234567", "004-12345-7654321", False),
        ("", "004-12345-1234567", False),
    ],
)
def test_bank_accounts_compare_regardless_of_formatting(a, b, same):
    from ap_coder.vendors import same_bank_account

    assert same_bank_account(a, b) is same


def test_changed_bank_account_is_flagged(tmp_path, ground_truth, reference):
    from ap_coder.vendors import mask_account

    store = Store(tmp_path / "ap.db")
    first = {**_variant(ground_truth, "B-1", date="2026-06-01"), "remit_bank_account": "004-12345-1234567"}
    _approved(store, first, tmp_path, "a.pdf")
    codes, report = _codes(
        store, {**_variant(ground_truth, "B-2"), "remit_bank_account": "010 00999 99887766"}, reference
    )
    assert codes.get("VENDOR_BANK_CHANGED") == "warning"
    message = next(i.message for i in report.issues if i.code == "VENDOR_BANK_CHANGED")
    assert "…7766" in message and "…4567" in message and "99887766" not in message  # never the whole account
    reformatted = {**_variant(ground_truth, "B-3"), "remit_bank_account": "Inst 004 Transit 12345 Acct 1234567"}
    same, _ = _codes(store, reformatted, reference)
    assert "VENDOR_BANK_CHANGED" not in same
    none, _ = _codes(store, _variant(ground_truth, "B-4"), reference)
    assert "VENDOR_BANK_CHANGED" not in none  # no bank details printed: nothing to compare
    assert mask_account("") == "(none)"


def test_bank_account_without_approved_history_is_not_flagged(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    store.add_invoice(tmp_path / "a.pdf", {**ground_truth, "remit_bank_account": "004-12345-1234567"}, {})  # in review
    codes, _ = _codes(store, {**_variant(ground_truth, "C-2"), "remit_bank_account": "010-00999-99887766"}, reference)
    assert "VENDOR_BANK_CHANGED" not in codes


@pytest.mark.parametrize(
    "details, masked",
    [
        ("Inst 003 Transit 00012 Acct 1234", "…1234"),  # the account, not the longest number
        ("004-12345-1234567", "…4567"),
        ("DE89 3704 0044 0532 0130 00", "…3000"),
        ("compte 123-4567 transit 00012", "…4567"),
    ],
)
def test_masking_shows_the_account_number(details, masked):
    from ap_coder.vendors import mask_account

    assert mask_account(details) == masked


def test_bank_details_without_leading_zeros_are_the_same_account():
    from ap_coder.vendors import same_bank_account

    assert same_bank_account("transit 00012, inst 003, account 1234567", "transit 12, institution 3, acct 1234567")
    assert not same_bank_account("Inst 003 Transit 00012 Account 1234567", "Inst 003 Transit 00099 Account 1234567")


def test_same_account_number_at_another_branch_is_explained(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    first = {**_variant(ground_truth, "D-1", date="2026-06-01"),
             "remit_bank_account": "Institution 003 Transit 00012 Account 1234567"}  # fmt: skip
    _approved(store, first, tmp_path, "a.pdf")
    moved = {**_variant(ground_truth, "D-2"), "remit_bank_account": "Institution 003 Transit 00099 Account 1234567"}
    _, report = _codes(store, moved, reference)
    message = next(i.message for i in report.issues if i.code == "VENDOR_BANK_CHANGED")
    assert "different bank or branch" in message


def test_credit_note_finds_the_invoice_it_credits(tmp_path, reference):
    import json

    from .conftest import SAMPLES

    store = Store(tmp_path / "ap.db")
    invoice = json.loads((SAMPLES / "ground_truth" / "northwind_ON_HST_NW-2026-0912.json").read_text())
    credit = json.loads((SAMPLES / "ground_truth" / "northwind_ON_HST_CN-2026-0047.json").read_text())
    codes, report = _codes(store, credit, reference)
    assert codes.get("CREDIT_NOTE_ORIGINAL_UNKNOWN") == "info"
    original = _approved(store, invoice, tmp_path, "a.pdf")
    codes, report = _codes(store, credit, reference)
    message = next(i.message for i in report.issues if i.code == "CREDIT_NOTE_FOR")
    assert f"#{original}" in message and "NW-2026-0912" in message and "CREDIT_EXCEEDS_INVOICE" not in codes
    big = {**credit, "original_invoice_number": "nw 2026 0912"}
    for li in big["line_items"]:
        li["amount"] *= 10
        li["unit_price"] *= 10
    big["subtotal"] *= 10
    big["tax_total"] *= 10
    big["grand_total"] *= 10
    for t in big["tax_lines"]:
        t["taxable_amount"] *= 10
        t["tax_amount"] *= 10
    codes, _ = _codes(store, big, reference)
    assert codes.get("CREDIT_EXCEEDS_INVOICE") == "warning"
    codes, _ = _codes(store, {**invoice, "invoice_number": "NW-X"}, reference)
    assert not {c for c in codes if c.startswith("CREDIT_")}  # an invoice is not a credit note


def test_several_credit_notes_together_exceed_the_invoice(tmp_path, reference):
    import json

    from .conftest import SAMPLES

    store = Store(tmp_path / "ap.db")
    invoice = json.loads((SAMPLES / "ground_truth" / "northwind_ON_HST_NW-2026-0912.json").read_text())
    credit = json.loads((SAMPLES / "ground_truth" / "northwind_ON_HST_CN-2026-0047.json").read_text())
    _approved(store, invoice, tmp_path, "a.pdf")  # 18,017.85
    for n in range(7):  # 7 x 2,316.50 = 16,215.50: still under the invoice
        _approved(store, {**credit, "invoice_number": f"CN-{n}"}, tmp_path, f"c{n}.pdf")
    codes, _ = _codes(store, {**credit, "invoice_number": "CN-7"}, reference)  # 18,532.00 in all
    assert codes.get("CREDIT_EXCEEDS_INVOICE") == "warning"
    first_credit = next(i["id"] for i in store.list_invoices() if i["invoice_number"] == "CN-0")
    store.reopen(first_credit, "Jane", "duplicate")  # an approved credit is reopened, then rejected
    store.reject_invoice(first_credit, "Jane", "duplicate")  # a rejected credit does not count
    codes, _ = _codes(store, {**credit, "invoice_number": "CN-7"}, reference)
    assert "CREDIT_EXCEEDS_INVOICE" not in codes
