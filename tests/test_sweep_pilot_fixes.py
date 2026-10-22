"""Problems found by the pilot sweeps (B-650 onwards): what counts as a correction, the place of supply with no
ship-to, amounts without a leading zero, stale pages, payment terms, French headers and more."""

import copy
import sqlite3

from ap_coder.core.memory import ACCEPTED, CODED, CORRECTED
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store

# --- B-650: filling in a blank GL account is the reviewer's coding, not a correction --------------------------------


def _blank_invoice():
    line = {"line_number": 1, "description": "Consulting", "quantity": 1, "unit_price": 100.0, "amount": 100.0,
            "predicted_gl_code": "UNASSIGNED", "predicted_cost_center": "", "taxes_applied": [],
            "reasoning_justification": "No earlier approval to learn from: pick the account."}  # fmt: skip
    return {"vendor_name": "Acme Ltd", "invoice_number": "1", "invoice_date": "2026-10-01", "currency": "CAD",
            "subtotal": 100.0, "tax_lines": [], "tax_total": 0.0, "grand_total": 100.0, "confidence_score": 0.9,
            "line_items": [line]}  # fmt: skip


def test_filling_in_a_blank_gl_account_is_coded_not_corrected(tmp_path):
    from ap_coder.ai import lessons
    from ap_coder.web.invoice_view import ai_changes, learn_text

    store = Store(tmp_path / "ap.db")
    read = _blank_invoice()
    iid = store.add_invoice(tmp_path / "x.pdf", read, {"requires_review": True})
    final = copy.deepcopy(read)
    final["line_items"][0]["predicted_gl_code"] = "6110"
    coding = InvoiceCoding.model_validate(final)
    # The review screen, before approving: nothing changed, one line the reviewer coded
    assert ai_changes(coding, read) == 0
    assert learn_text(coding, read) == "Teaches from 1 line: 1 you coded"
    # The lessons: a blank filled in is no correction (no "why" question for it)
    assert [r["corrected"] for r in lessons.outcomes(read, final) if r["field"] == lessons.LINE_GL] == [False]
    assert store.approve_invoice(iid, final, "pat") == {ACCEPTED: 0, CORRECTED: 0, CODED: 1}
    m = store.metrics()
    assert m["lines_corrected"] == 0 and m["lines_measured"] == 0 and m["by_vendor"] == []
    (event,) = [e for e in store.events() if e["action"] == "approved"]
    assert event["detail"]["corrected"] == 0 and event["detail"]["coded"] == 1
    inv = store.get_invoice(iid)
    assert "line_coding" not in inv["edits"]


def test_a_filled_in_blank_still_teaches_the_learning_memory(tmp_path):
    """The memory learns the account from a coded line: a recode made by filling in a blank line, then confirmed,
    still moves the memory away from the older account (as when the blank was recorded as a correction, B-605)."""
    from ap_coder.core.memory import select_examples, vendor_key

    def row(gl, outcome, suggested):
        return {"vendor_key": vendor_key("Northwind"), "vendor_name": "Northwind", "description": "Laptop",
                "final_gl": gl, "final_cc": "", "suggested_gl": suggested, "outcome": outcome,
                "created_at": "2026-10-01T10:00:00"}  # fmt: skip

    rows = [row("1500", ACCEPTED, "1500"), row("1500", CODED, "UNASSIGNED")]
    rows += [row("6010", ACCEPTED, "6010") for _ in range(12)]
    assert [(e.gl_code, e.decisions) for e in select_examples(rows, "Northwind invoice")] == [("1500", 2)]


def test_blanks_an_older_version_recorded_as_corrections_are_recounted(tmp_path):
    path = tmp_path / "ap.db"
    store = Store(path)
    read = _blank_invoice()
    iid = store.add_invoice(tmp_path / "x.pdf", read, {"requires_review": True})
    final = copy.deepcopy(read)
    final["line_items"][0]["predicted_gl_code"] = "6110"
    store.approve_invoice(iid, final, "pat")
    with sqlite3.connect(path) as conn:  # as an older version recorded it
        conn.execute("UPDATE feedback SET outcome = 'corrected'")
    assert Store(path).metrics()["lines_corrected"] == 0


# --- B-651: the vendor's province is never the ship-to (D-61) ---------------------------------------------------------

PRAIRIE = ("Prairie Print Ltd.\n100 Main St SW, Calgary AB T2P 1J9\nGST Reg. 123456782 RT0001\n\nINVOICE\n"
           "Invoice # PP-77\nInvoice Date: October 1, 2026\nTerms: Net 30\n\nDescription        Amount\n"
           "Business cards, 500    100.00\n\nSubtotal    100.00\nGST 5%    5.00\nTotal    105.00\n")  # fmt: skip


def test_the_vendors_province_is_never_the_ship_to():
    from types import SimpleNamespace

    from ap_coder.ai.offline_coder import addresses, provinces
    from ap_coder.coding.tax import supply_source

    assert provinces(PRAIRIE) == ("AB", "")
    coding = SimpleNamespace(ship_to_province=provinces(PRAIRIE)[1], supplier_province="AB", line_items=[])
    assert supply_source(coding, SimpleNamespace(site_provinces={}, our_province="ON")) == ("ON", "ours")
    # A bill-to is our address, as the customer: the place of supply when no ship-to is printed; a ship-to wins
    billed = "Acme Supply\n1 Main St, Toronto ON M5V 1A1\nBill to:\nHalifax NS B3H 1A1\n"
    assert provinces(billed) == ("ON", "NS") and addresses(billed) == ("ON", "", "NS")
    shipped = billed + "Ship to:\nCalgary AB T2P 1A1\n"
    assert provinces(shipped) == ("ON", "AB") and addresses(shipped) == ("ON", "AB", "NS")


def test_the_screen_never_calls_a_bill_to_the_ship_to():
    from types import SimpleNamespace

    from ap_coder.web.invoice_view import supply_line

    setup = SimpleNamespace(site_provinces={}, our_province="ON")
    why = supply_line({"ship_to_province": "NS", "supplier_province": "ON"}, [], setup)["why"]
    assert "ship-to on the invoice" not in why and "bill-to" in why
    assert supply_line({"ship_to_province": "", "supplier_province": "AB"}, [], setup)["why"] == (
        "your province (Settings): no ship-to or bill-to printed"
    )


def test_with_no_ship_to_printed_the_tax_check_says_the_province_is_ours():
    from ap_coder.coding.tax import TaxSetup, check_taxes
    from ap_coder.core.schema import TaxLine

    coding = InvoiceCoding.model_validate({**_blank_invoice(), "supplier_province": "AB", "subtotal": 100.0,
                                           "tax_total": 5.0, "grand_total": 105.0})  # fmt: skip
    coding.tax_lines = [TaxLine(tax_type="GST", province="", rate=0.05, taxable_amount=100.0, tax_amount=5.0)]
    setup = TaxSetup.default()
    setup.our_province = "ON"
    messages = [f.message for f in check_taxes(coding, setup) if f.code == "TAX_REGIME_MISMATCH"]
    assert messages and all("your province in Settings: no ship-to or bill-to printed" in m for m in messages), messages


# --- B-652: amounts printed without a leading zero -------------------------------------------------------------------


def test_amounts_without_a_leading_zero():
    from ap_coder.capture.normalize import find_amounts, parse_amount

    assert parse_amount("$.99") == 0.99 and parse_amount(".65") == 0.65 and parse_amount("-.50") == -0.50
    assert parse_amount("(.50)") == -0.50 and parse_amount("$.65 CR") == -0.65
    assert [v for v, _, _ in find_amounts("HST 13%                $.65")] == [13.0, 0.65]
    assert [v for v, _, _ in find_amounts("Total ........ 65.00")] == [65.0]
    assert [v for v, _, _ in find_amounts("Invoice No.12 Ver.25")] == [12.0, 25.0]  # a number after a word's point
    assert parse_amount("1,234.56") == 1234.56 and parse_amount("0.65") == 0.65  # unchanged


def test_a_receipt_with_amounts_without_a_leading_zero_reads_them(tmp_path):
    from ap_coder.capture import analyze

    text = ("Northern Pet Supply Ltd.\n12 King St W, Toronto ON M5H 1A1\nGST/HST 123456782 RT0001\nINVOICE\n"
            "Invoice # 55013\nDate: October 1, 2026\n\nDescription            Amount\nDog chew               $.99\n\n"
            "Subtotal               $.99\nHST 13%                $.13\nTotal                  $1.12\n")  # fmt: skip
    path = tmp_path / "leading_point.txt"
    path.write_text(text)
    r = analyze(str(path))
    assert r.fields["subtotal"].value == 0.99 and r.fields["hst_amount"].value == 0.13
    assert r.fields["grand_total"].value == 1.12 and r.fields["subtotal"].status == "verified"
