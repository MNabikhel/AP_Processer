"""Vendor master and payment-fraud / duplicate signals."""

import copy

import pytest

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.coding.vendors import norm_invoice_number, vendor_findings
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from tests.webclient import gt


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
    assert codes.get("VENDOR_TAX_NUMBER_CHANGED") == "error"  # approved only with a reason (D-141)
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
    assert codes.get("VENDOR_TAX_NUMBER_CHANGED") == "error"


def test_another_gst_hst_account_of_the_same_business_is_a_warning(tmp_path, ground_truth, reference):
    """D-219, amended: the nine digits are the business; the four after RT are which of its GST/HST accounts (a
    branch or division can have its own). Another account of the same business is a warning, against the vendor's
    earlier invoices as against the vendor list; other nine digits stay the error."""
    store = Store(tmp_path / "ap.db")
    _approved(store, _variant(ground_truth, "A-1", date="2026-06-01"), tmp_path, "a.pdf")  # prints 123456782 RT0001
    codes, report = _codes(store, _variant(ground_truth, "A-2", gst="123456782 RT0002"), reference)
    assert codes.get("VENDOR_TAX_ACCOUNT_DIFFERS") == "warning" and "VENDOR_TAX_NUMBER_CHANGED" not in codes
    message = next(i.message for i in report.issues if i.code == "VENDOR_TAX_ACCOUNT_DIFFERS")
    assert "123456782 RT0002" in message and "123456782 RT0001" in message and "this vendor's usual" in message
    assert "same business number" in message and "CRA" in message and "confirm with the vendor" not in message
    store.save_vendor("northwind it solutions", "Northwind IT Solutions Inc.", expected_gst="123456782RT0003")
    codes, report = _codes(store, _variant(ground_truth, "A-3", gst="123456782 RT0002"), reference)
    assert codes.get("VENDOR_TAX_ACCOUNT_DIFFERS") == "warning" and "VENDOR_TAX_NUMBER_CHANGED" not in codes
    message = next(i.message for i in report.issues if i.code == "VENDOR_TAX_ACCOUNT_DIFFERS")
    assert "the vendor list's" in message and "123456782RT0003" in message
    for same in ("123456782", "123456782 RT0003"):  # the nine digits alone, and the list's own account: no finding
        codes, _ = _codes(store, _variant(ground_truth, "A-4", gst=same), reference)
        assert not {"VENDOR_TAX_ACCOUNT_DIFFERS", "VENDOR_TAX_NUMBER_CHANGED"} & set(codes), same
    codes, _ = _codes(store, _variant(ground_truth, "A-5", gst="987654321 RT0003"), reference)  # another business
    assert codes.get("VENDOR_TAX_NUMBER_CHANGED") == "error" and "VENDOR_TAX_ACCOUNT_DIFFERS" not in codes


@pytest.mark.parametrize(
    "a, b, other",
    [
        ("123456789RT0001", "123456789 RT 0002", True),  # two GST/HST accounts of one business
        ("123456789RT0002", "123456789rt0002", False),  # the same account
        ("123456789", "123456789RT0002", False),  # a side with only the nine digits: the same registration
        ("123456789RT0001", "987654321RT0002", False),  # another business
        ("123456789RC0001", "123456789RT0001", False),  # a payroll account is no GST/HST account
        ("", "123456789RT0001", False),
    ],
)
def test_two_gst_hst_accounts_of_one_business_are_told_from_another_business(a, b, other):
    from ap_coder.core.keys import other_tax_account

    assert other_tax_account(a, b) is other and other_tax_account(b, a) is other


def test_the_note_under_the_gst_hst_box_says_another_account_of_the_same_business(tmp_path, ground_truth):
    from ap_coder.coding.vendors import expected_tax_numbers

    store = Store(tmp_path / "ap.db")
    store.save_vendor("northwind it solutions", "Northwind IT Solutions Inc.", expected_gst="123456782RT0003")
    field = "gst_hst_registration_number"
    sister = InvoiceCoding.model_validate(_variant(ground_truth, "A-1", gst="123456782 RT0002"))
    assert expected_tax_numbers(sister, store)[field] == (
        "Vendor list: 123456782RT0003, another GST/HST account of the same business"
    )
    stranger = InvoiceCoding.model_validate(_variant(ground_truth, "A-2", gst="987654321 RT0002"))
    assert expected_tax_numbers(stranger, store)[field] == "Vendor list: 123456782RT0003"
    store.save_vendor("northwind it solutions", "Northwind IT Solutions Inc.", expected_gst="")
    _approved(store, _variant(ground_truth, "A-3", gst="123456782 RT0001"), tmp_path, "a.pdf")
    assert expected_tax_numbers(sister, store)[field] == (
        "Usual for this vendor: 123456782 RT0001, another GST/HST account of the same business"
    )


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
    from ap_coder.coding.vendors import same_tax_number

    assert same_tax_number(a, b) is same


@pytest.mark.parametrize(
    "a, b, same",
    [
        ("1234567890", "1234567890 TQ 0001", True),  # the vendor list keeps only the 10 digits
        ("1234567890TQ0001", "1234-567-890", True),
        ("1234567890TQ0001", "1234567890TQ0002", False),  # two files of one registrant
        ("1234567890", "9876543210TQ0001", False),
        ("", "1234567890TQ0001", False),
    ],
)
def test_qst_numbers_compare_by_their_ten_digits(a, b, same):
    from types import SimpleNamespace

    from ap_coder.coding.vendors import tax_number_findings

    changed = {c for _, c, _ in tax_number_findings(SimpleNamespace(qst_registration_number=b), {"qst_number": a}, [])}
    assert ("VENDOR_QST_CHANGED" in changed) is (not same and bool(a))


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


def test_a_hold_on_the_name_survives_a_match_by_tax_number(tmp_path, ground_truth, reference):
    """S2-T2: a name AP put on hold (not in the ERP master) that prints a master vendor's GST/HST number still
    shows the hold, and its note."""
    store = Store(tmp_path / "ap.db")
    row = {"vendor_name": ground_truth["vendor_name"], "gst": ground_truth["gst_hst_registration_number"],
           "status": "active", "status_given": True, "erp_id": "10001", "terms": None, "default_gl": None}  # fmt: skip
    store.import_vendor_master([row])
    store.save_vendor("nw trading do not pay", "NW Trading (do not pay)", "on_hold", notes="fraud alert",
                      actor="jane")  # fmt: skip
    _, report = _codes(store, {**_variant(ground_truth, "A-1"), "vendor_name": "NW Trading (do not pay)"}, reference)
    codes = {i.code: i for i in report.issues}
    assert "VENDOR_MATCHED_BY_TAX_NUMBER" in codes
    assert codes["VENDOR_ON_HOLD"].severity == "error" and "fraud alert" in codes["VENDOR_ON_HOLD"].message
    # And a master vendor on hold, found by its number under another name, is on hold too.
    store.import_vendor_master([{**row, "status": "on_hold"}])
    codes, _ = _codes(store, {**_variant(ground_truth, "A-2"), "vendor_name": "NW IT Sol."}, reference)
    assert codes.get("VENDOR_ON_HOLD") == "error"


def test_the_duplicate_check_stays_right_and_quick_beside_a_supplier_with_20000_vouchers(tmp_path, ground_truth):
    """The scale goal's target 4: with one supplier's 20,000 vouchers in the ERP register (the JD Edwards sync's, or
    an imported one), a bill of it already in JD Edwards, and one of a small supplier beside it, are each flagged
    DUPLICATE_IN_ERP, a new bill of the big one is not, and each check reads only that supplier's number."""
    import time

    from ap_coder.coding.vendors import vendor_findings

    big = "Northwind IT Solutions Inc."
    rows = [{"vendor_name": big, "invoice_number": f"NW-{n:05d}", "total": 100 + n % 50, "invoice_date": "2026-03-01"}
            for n in range(20000)]  # fmt: skip
    rows.append({"vendor_name": "Small Supplies Co", "invoice_number": "S-7", "total": 42.0, "invoice_date": ""})
    store = Store(tmp_path / "ap.db")
    assert store.import_erp_register(rows) == 20001

    def codes(vendor: str, number: str, total: float) -> set[str]:
        coding = InvoiceCoding.model_validate({**ground_truth, "vendor_name": vendor, "invoice_number": number,
                                               "grand_total": total})  # fmt: skip
        return {code for _, code, _ in vendor_findings(coding, store)}

    codes(big, "warm", 1.0)
    started = time.perf_counter()
    assert "DUPLICATE_IN_ERP" in codes(big, "NW 12345", 145.0)  # written another way: the same bill
    assert "DUPLICATE_IN_ERP" in codes("Small Supplies Co", "S-7", 42.0)
    assert "DUPLICATE_IN_ERP" not in codes(big, "NW-20001", 100.0)  # its new bill
    assert "DUPLICATE_IN_ERP" not in codes(big, "NW-12345", -145.0)  # the credit note of that bill
    assert (time.perf_counter() - started) / 4 < 0.25


def test_a_held_vendors_gst_number_or_bank_account_on_an_invoice_coded_to_another_vendor_is_said(
    tmp_path, ground_truth
):
    """B-1549: under a near-identical new name, the GST/HST number or the bank account read off the invoice is still
    the held vendor's (D-142)."""
    from ap_coder.coding.vendors import held_vendor, moved_off_hold

    store = Store(tmp_path / "ap.db")
    held = {**ground_truth, "remit_bank_account": "004-12345-1234567"}
    first = store.add_invoice(tmp_path / "a.pdf", held, {})
    store.approve_invoice(first, held, "jane")
    store.save_vendor(store.vendor_key_for(held["vendor_name"]), held["vendor_name"], "on_hold")
    gst = ground_truth["gst_hst_registration_number"]
    assert held_vendor(store, "Northwnd IT Solutions", gst)[1] == "gst"
    assert held_vendor(store, "Northwnd IT Solutions", "", "Transit 12345 Inst 004 Account 1234567")[1] == "bank"
    assert held_vendor(store, "Someone Else Ltd", "", "") is None
    moved = InvoiceCoding.model_validate({**held, "vendor_name": "Northwnd IT Solutions", "invoice_number": "B-2",
                                          "gst_hst_registration_number": ""})  # fmt: skip
    read = {**held, "vendor_name": "Northwnd IT Solutions"}
    [(severity, code, message)] = moved_off_hold(store, read, moved)
    assert (severity, code) == ("error", "VENDOR_CHANGED_FROM_HELD") and "GST/HST number" in message
    elsewhere = {"gst_hst_registration_number": "", "remit_bank_account": ""}  # nothing of the held vendor's
    other = InvoiceCoding.model_validate({**moved.model_dump(), **elsewhere})
    assert moved_off_hold(store, {**read, **elsewhere}, other) == []


def test_with_thousands_of_vendors_on_hold_only_the_ones_an_invoice_could_be_are_compared(tmp_path, monkeypatch):
    """B-1863: the held vendors' check (``held_vendor``) compared the GST/HST number and the bank account of an
    invoice with each of every held vendor's approved invoices, at each check and twice a review screen: 46 ms with
    3,000 vendors on hold. Their numbers and accounts are kept by what makes them the same (the Business Number, the
    digits, the last 4 JD Edwards pays on) until a vendor or their invoices change: only those are compared."""
    from ap_coder.coding import vendors

    store = Store(tmp_path / "ap.db")
    store.import_vendor_master([{"vendor_name": f"Held Supplier {i} Ltd", "erp_id": str(50000 + i),
                                 "gst": f"{100000000 + i}RT0001"} for i in range(300)])  # fmt: skip
    with store._conn() as conn:
        conn.execute("UPDATE vendors SET erp_hold = 'H', erp_bank = '00012-003 …' || substr('000' || rowid, -4)")
    for i in range(0, 300, 3):
        doc = {
            "vendor_name": f"Held Supplier {i} Ltd",
            "invoice_number": f"I-{i}",
            "grand_total": 10,
            "gst_hst_registration_number": f"{100000000 + i}",
            "remit_bank_account": f"004-12345-77{i:05d}",
        }
        store.approve_invoice(store.add_invoice(tmp_path / f"{i}.pdf", doc, {}), doc, "x")
    compared = []
    real_tax, real_bank = vendors.same_tax_number, vendors.same_bank_account
    monkeypatch.setattr(vendors, "same_tax_number", lambda a, b: compared.append(b) or real_tax(a, b))
    monkeypatch.setattr(vendors, "same_bank_account", lambda a, b: compared.append(b) or real_bank(a, b))
    assert vendors.held_vendor(store, "Someone Else Inc", "999999999 RT0001", "Transit 99999 Account 1") is None
    vendor, tie = vendors.held_vendor(store, "Someone Else Inc", "100000150 RT0001")  # its Business Number
    assert (vendor["vendor_key"], tie) == ("held supplier 150", "gst")
    vendor, tie = vendors.held_vendor(store, "Someone Else Inc", "", "Transit 12345 Inst 004 Account 7700297")
    assert (vendor["vendor_key"], tie) == ("held supplier 297", "bank")
    vendor, tie = vendors.held_vendor(store, "Someone Else Inc", "", "Transit 00012 Inst 003 Account 9990012")
    assert tie == "bank"  # the account JD Edwards pays it on (…0012)
    assert len(compared) < 10, len(compared)  # (before: each of 400 numbers and 100 accounts, at each check)


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
    from ap_coder.coding.vendors import same_bank_account

    assert same_bank_account(a, b) is same


def test_changed_bank_account_is_flagged(tmp_path, ground_truth, reference):
    from ap_coder.coding.vendors import mask_account

    store = Store(tmp_path / "ap.db")
    first = {**_variant(ground_truth, "B-1", date="2026-06-01"), "remit_bank_account": "004-12345-1234567"}
    _approved(store, first, tmp_path, "a.pdf")
    codes, report = _codes(
        store, {**_variant(ground_truth, "B-2"), "remit_bank_account": "010 00999 99887766"}, reference
    )
    assert codes.get("VENDOR_BANK_CHANGED") == "error"  # approved only with a reason (D-141)
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
    from ap_coder.coding.vendors import mask_account

    assert mask_account(details) == masked


def test_bank_details_without_leading_zeros_are_the_same_account():
    from ap_coder.coding.vendors import same_bank_account

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

    from tests.conftest import SAMPLES

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

    from tests.conftest import SAMPLES

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


def test_a_credit_note_never_credits_a_rejected_invoice(tmp_path, reference):
    """B-1483: a credit note took a rejected invoice of the number it names as its original ("credits invoice
    NW-2026-0912 (#2, rejected)"), even with the approved one there: rejected and failed invoices were skipped only
    in the sum of earlier credits."""
    import json

    from tests.conftest import SAMPLES

    store = Store(tmp_path / "ap.db")
    invoice = json.loads((SAMPLES / "ground_truth" / "northwind_ON_HST_NW-2026-0912.json").read_text())
    credit = json.loads((SAMPLES / "ground_truth" / "northwind_ON_HST_CN-2026-0047.json").read_text())
    rejected = store.add_invoice(tmp_path / "a.pdf", invoice, {})
    store.reject_invoice(rejected, "Jane", "not ours")
    codes, _ = _codes(store, credit, reference)
    assert codes.get("CREDIT_NOTE_ORIGINAL_UNKNOWN") == "info" and "CREDIT_NOTE_FOR" not in codes
    original = _approved(store, invoice, tmp_path, "b.pdf")
    copy_of_it = store.add_invoice(tmp_path / "c.pdf", invoice, {})  # the same bill sent again, rejected
    store.reject_invoice(copy_of_it, "Jane", "duplicate")
    _, report = _codes(store, credit, reference)
    message = next(i.message for i in report.issues if i.code == "CREDIT_NOTE_FOR")
    assert f"#{original}, approved" in message and "rejected" not in message


# --- QST and PST numbers against the vendor chosen --------------------------------------------------------------------


def test_the_vendor_lists_numbers_are_checked_on_the_first_invoice_too(tmp_path, ground_truth, reference):
    """The vendor list's GST/HST, QST and PST numbers are checked even before any invoice from it was approved."""
    store = Store(tmp_path / "ap.db")
    store.save_vendor("northwind it solutions", "Northwind IT Solutions Inc.", expected_gst="111111111RT0001",
                      qst_number="1234567890TQ0001", pst_number="PST-1234-5678", province="on")  # fmt: skip
    vendor = store.get_vendor("northwind it solutions")
    assert (vendor["qst_number"], vendor["pst_number"], vendor["province"]) == ("1234567890TQ0001", "PST-1234-5678",
                                                                                "ON")  # fmt: skip
    doc = {**ground_truth, "qst_registration_number": "9999999999 TQ 0001", "pst_registration_number": "PST 9999-0000"}
    codes, report = _codes(store, doc, reference)
    assert {codes.get(c) for c in ("VENDOR_TAX_NUMBER_CHANGED", "VENDOR_QST_CHANGED", "VENDOR_PST_CHANGED")} == {
        "error"
    }
    message = next(i.message for i in report.issues if i.code == "VENDOR_PST_CHANGED")
    assert "PST 9999-0000" in message and "vendor list" in message
    # The same numbers printed another way are the same numbers.
    same = {**ground_truth, "gst_hst_registration_number": "111111111", "qst_registration_number": "1234567890 TQ 0001",
            "pst_registration_number": "1234-5678"}  # fmt: skip
    codes, _ = _codes(store, same, reference)
    assert not {"VENDOR_TAX_NUMBER_CHANGED", "VENDOR_QST_CHANGED", "VENDOR_PST_CHANGED"} & set(codes)


def test_saving_a_vendor_without_tax_numbers_keeps_them(tmp_path):
    store = Store(tmp_path / "ap.db")
    store.save_vendor("acme", "Acme", qst_number="1234567890TQ0001", pst_number="7654321", province="QC")
    store.save_vendor("acme", "Acme", "on_hold", notes="disputed")  # the Vendors page's hold: numbers untouched
    vendor = store.get_vendor("acme")
    assert (vendor["status"], vendor["qst_number"], vendor["pst_number"], vendor["province"]) == (
        "on_hold",
        "1234567890TQ0001",
        "7654321",
        "QC",
    )


def test_without_the_vendor_list_the_approved_invoices_decide(tmp_path, ground_truth, reference):
    store = Store(tmp_path / "ap.db")
    _approved(store, {**_variant(ground_truth, "A-1"), "pst_registration_number": "RST 160482-7"}, tmp_path, "a.pdf")
    codes, _ = _codes(store, {**_variant(ground_truth, "A-2"), "pst_registration_number": "160482-7"}, reference)
    assert "VENDOR_PST_CHANGED" not in codes
    codes, _ = _codes(store, {**_variant(ground_truth, "A-3"), "pst_registration_number": "555111-2"}, reference)
    assert codes.get("VENDOR_PST_CHANGED") == "error"


def test_an_old_vendor_list_gets_the_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "ap.db"
    Store(path)
    with sqlite3.connect(path) as conn:  # a vendor list made before the QST, PST and province columns
        conn.execute("CREATE TABLE v2 AS SELECT vendor_key, display_name, status, expected_gst, notes, updated_at, "
                     "erp_id, terms, default_gl, in_master FROM vendors")  # fmt: skip
        conn.execute("DROP TABLE vendors")
        conn.execute("ALTER TABLE v2 RENAME TO vendors")
        conn.execute("INSERT INTO vendors VALUES ('acme', 'Acme', 'active', '', '', '2026-01-01', '', '', '', 0)")
    vendor = Store(path).get_vendor("acme")
    assert (vendor["qst_number"], vendor["pst_number"], vendor["province"]) == ("", "", "")


def test_the_erp_vendor_list_brings_qst_pst_and_province(tmp_path):
    from ap_coder.coding.vendors import master_columns, master_rows

    records = [
        {
            "Vendor Name": "Acme Ltée",
            "Vendor ID": "V1",
            "QST Number": "1234567890TQ0001",
            "PST Number": "PST-1111-2222",
            "Province": "Québec",
        },
        {"Vendor Name": "Beta", "Vendor ID": "V2", "QST Number": "", "PST Number": "", "Province": "Texas"},
    ]
    rows = master_rows(records, master_columns(list(records[0])))
    assert (rows[0]["qst"], rows[0]["pst"], rows[0]["province"]) == ("1234567890TQ0001", "PST-1111-2222", "QC")
    assert rows[1]["province"] in ("", "OUTSIDE_CANADA")
    store = Store(tmp_path / "ap.db")
    store.import_vendor_master(rows)
    acme = next(v for v in store.master_vendors() if v["erp_id"] == "V1")
    assert (acme["qst_number"], acme["pst_number"], acme["province"]) == ("1234567890TQ0001", "PST-1111-2222", "QC")
    store.import_vendor_master([{**rows[0], "qst": "", "pst": "", "province": ""}])  # a file without them keeps them
    acme = next(v for v in store.master_vendors() if v["erp_id"] == "V1")
    assert acme["pst_number"] == "PST-1111-2222"


def test_without_a_vendor_master_the_vendor_list_keeps_the_numbers_ap_gave_a_vendor(tmp_path):
    """B-680: with no vendor master the review screen's vendor list is the vendors approved before; the GST/HST, QST
    and PST numbers AP gave one of them on the Vendors page were left out of it, so the vendor field neither showed
    them nor filled them in (D-65)."""
    from ap_coder.core.store import Store

    store = Store(tmp_path / "ap.db")
    doc = {
        "vendor_name": "Acme Ltd",
        "invoice_number": "1",
        "invoice_date": "2026-10-01",
        "currency": "CAD",
        "subtotal": 10.0,
        "tax_total": 0.0,
        "grand_total": 10.0,
        "confidence_score": 0.9,
        "line_items": [],
    }
    invoice_id = store.add_invoice(tmp_path / "a.pdf", doc, {})
    store.approve_invoice(invoice_id, doc, "Jane")
    store.save_vendor(
        "acme", "Acme Ltd", expected_gst="123456782 RT0001", qst_number="1234567890 TQ0001", pst_number="PST-1234-5678"
    )
    (acme,) = [v for v in store.vendor_choices() if v["vendor_key"] == "acme"]
    assert (acme["expected_gst"], acme["qst_number"], acme["pst_number"]) == (
        "123456782 RT0001",
        "1234567890 TQ0001",
        "PST-1234-5678",
    )


RENAMED_GST = "123456789RT0001"


def _renamed_doc(name, number, bank=""):
    return InvoiceCoding.model_validate(
        gt(vendor_name=name, invoice_number=number, gst_hst_registration_number=RENAMED_GST, remit_bank_account=bank)
    )


def test_a_renamed_supplier_still_has_its_bank_history(tmp_path):
    """B-2517: JD Edwards renames supplier 100 (same number, new legal name): the first invoice under the new name with
    other bank details is still compared with the bank details supplier 100's approved invoices carried."""
    old_bank = "Transit 12345 Institution 004 Account 1234567"
    new_bank = "Transit 67890 Institution 010 Account 7654321"
    store = Store(tmp_path / "s.db")
    store.import_vendor_master([{"vendor_name": "Acme Fasteners Ltd", "erp_id": "100", "gst": RENAMED_GST}],
                               by_erp_id=True)  # fmt: skip
    for n in range(3):
        doc = _renamed_doc("Acme Fasteners Ltd", f"A-{n}", old_bank).model_dump(mode="json")
        store.approve_invoice(store.add_invoice(tmp_path / f"a{n}.pdf", doc, None), doc, "pat")
    assert "VENDOR_BANK_CHANGED" in {c for _, c, _ in vendor_findings(_renamed_doc("Acme Fasteners Ltd", "A-9",
                                                                                   new_bank), store)}  # fmt: skip
    store.import_vendor_master([{"vendor_name": "Acme Industrial Holdings Inc", "erp_id": "100",
                                 "gst": RENAMED_GST}], by_erp_id=True)  # fmt: skip
    assert store.supplier_key_for("Acme Industrial Holdings Inc", RENAMED_GST) == store.supplier_key_for(
        "Acme Fasteners Ltd", RENAMED_GST
    )  # one supplier
    found = vendor_findings(_renamed_doc("Acme Industrial Holdings Inc", "A-10", new_bank), store)
    assert "VENDOR_BANK_CHANGED" in {c for _, c, _ in found}, "supplier 100's bank history is lost with its new name"
    same = vendor_findings(_renamed_doc("Acme Industrial Holdings Inc", "A-11", old_bank), store)
    assert "VENDOR_BANK_CHANGED" not in {c for _, c, _ in same}


def test_a_hold_put_on_a_supplier_holds_it_under_its_new_name(tmp_path):
    """B-2517: AP holds supplier 100 in AP Coder; JD Edwards then renames it (same number): still on hold."""
    store = Store(tmp_path / "s.db")
    store.import_vendor_master([{"vendor_name": "Acme Fasteners Ltd", "erp_id": "100", "gst": RENAMED_GST}],
                               by_erp_id=True)  # fmt: skip
    old = store.get_vendor("acme fasteners")
    store.save_vendor("acme fasteners", old["display_name"], status="on_hold", expected_gst=RENAMED_GST,
                      notes="bank letter not verified", actor="Mia")  # fmt: skip
    assert "VENDOR_ON_HOLD" in {c for _, c, _ in vendor_findings(_renamed_doc("Acme Fasteners Ltd", "A-1"), store)}
    store.import_vendor_master([{"vendor_name": "Acme Industrial Holdings Inc", "erp_id": "100",
                                 "gst": RENAMED_GST}], by_erp_id=True)  # fmt: skip
    found = vendor_findings(_renamed_doc("Acme Industrial Holdings Inc", "A-2"), store)
    assert ("error", "VENDOR_ON_HOLD", "this vendor is on hold in the vendor list: bank letter not verified") in found
