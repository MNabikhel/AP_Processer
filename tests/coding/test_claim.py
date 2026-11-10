"""The receipts of one expense claim made one voucher (D-216, ``coding/claim.py``): its lines, each line's tax, its
totals, and what is said of a receipt that adds nothing."""

from __future__ import annotations

from types import SimpleNamespace

from ap_coder.coding.claim import NO_REFERENCE, NO_SUPPLIER, NOT_ADDED, Receipt, claim_coding, claim_findings
from ap_coder.coding.taxmath import invoice_taxes
from ap_coder.coding.validation import ERROR, WARNING, Issue
from ap_coder.core.schema import InvoiceCoding, LineItem, TaxLine


def _line(n: int, text: str, amount: float, taxes: tuple[str, ...] = ("GST",), gl: str = "6100") -> LineItem:
    return LineItem(line_number=n, description=text, quantity=1, unit_price=amount, amount=amount,
                    predicted_gl_code=gl, taxes_applied=list(taxes))  # fmt: skip


def _receipt(merchant: str, date: str, lines: list[LineItem], tax: tuple[str, str, float, float], total: float,
             currency: str = "CAD", subtotal: float | None = None) -> InvoiceCoding:  # fmt: skip
    kind, province, rate, amount = tax
    before = sum(li.amount for li in lines) if subtotal is None else subtotal
    taxed = sum(li.amount for li in lines if kind in li.taxes_applied)
    return InvoiceCoding(
        vendor_name=merchant, invoice_number="R-1", invoice_date=date, currency=currency, subtotal=before,
        tax_lines=[TaxLine(tax_type=kind, province=province, rate=rate, taxable_amount=taxed, tax_amount=amount)],
        tax_total=amount, grand_total=total, confidence_score=0.9, line_items=lines,
    )  # fmt: skip


FUEL = _receipt("Prairie Fuel Stop", "2026-05-04", [_line(1, "Regular fuel", 60.0)], ("GST", "", 0.05, 3.0), 63.0)
LUNCH = _receipt("Riverbend Diner", "2026-05-05",
                 [_line(1, "Lunch special", 24.0), _line(2, "Coffee", 4.0), _line(3, "Tip", 5.0, ())],
                 ("GST", "", 0.05, 1.4), 34.4)  # fmt: skip
ROOM = _receipt("Lakeshore Inn", "2026-05-06", [_line(1, "Room, 1 night", 180.0, ("HST",), "6200")],
                ("HST", "ON", 0.13, 23.4), 203.4)  # fmt: skip


def test_three_receipts_are_one_voucher_of_their_lines_each_with_its_own_receipts_tax(reference):
    setup = reference.tax
    claim, notes = claim_coding([Receipt("a.jpg", FUEL), Receipt("b.jpg", LUNCH), Receipt("c.jpg", ROOM)], setup)
    assert notes == ["", "", ""]
    assert [(li.line_number, li.description, li.amount, li.taxes_read) for li in claim.line_items] == [
        (1, "Prairie Fuel Stop: Regular fuel", 60.0, {"GST": 3.0}),
        (2, "Riverbend Diner: Lunch special", 24.0, {"GST": 1.2}),
        (3, "Riverbend Diner: Coffee", 4.0, {"GST": 0.2}),
        (4, "Riverbend Diner: Tip", 5.0, None),  # a tip takes none of its receipt's tax
        (5, "Lakeshore Inn: Room, 1 night", 180.0, {"HST": 23.4}),
    ]
    assert [li.predicted_gl_code for li in claim.line_items] == ["6100", "6100", "6100", "6100", "6200"]  # as coded
    assert (claim.subtotal, claim.tax_total, claim.grand_total) == (273.0, 27.8, 300.8)
    assert [(t.tax_type, t.province, t.taxable_amount, t.tax_amount) for t in claim.tax_lines] == [
        ("GST", "", 88.0, 4.4), ("HST", "ON", 180.0, 23.4)]  # fmt: skip
    # The supplier is the employee and a claim has no number: neither is guessed from a merchant.
    assert (claim.vendor_name, claim.invoice_number) == ("", "")
    assert (claim.invoice_date, claim.currency) == ("2026-05-06", "CAD")
    # The tax on every line is its receipt's: each tax checks, and no line's read tax differs from the one worked out.
    taxes = invoice_taxes(claim, setup)
    assert all(check.ok for check in taxes.check) and not any(line.notes for line in taxes.lines if not line.tip)


def test_a_receipt_whose_lines_do_not_add_up_is_one_line_for_its_amount_before_tax(reference):
    odd = _receipt("Corner Hardware", "2026-05-07", [_line(1, "Screws", 8.0), _line(2, "Glue", 3.0)],
                   ("GST", "", 0.05, 1.0), 21.0, subtotal=20.0)  # fmt: skip
    claim, notes = claim_coding([Receipt("a.jpg", FUEL), Receipt("d.jpg", odd)], reference.tax)
    assert notes == ["", ""]
    line = claim.line_items[1]
    assert (line.description, line.amount, line.taxes_applied, line.taxes_read) == (
        "Corner Hardware 2026-05-07", 20.0, ["GST"], {"GST": 1.0})  # fmt: skip
    assert line.predicted_gl_code == "6100"  # both of its lines were coded there
    assert (claim.subtotal, claim.tax_total, claim.grand_total) == (80.0, 4.0, 84.0)


def test_a_receipt_in_another_currency_or_with_no_total_adds_nothing_and_is_named(reference):
    usd = _receipt("Airport Taxi", "2026-05-08", [_line(1, "Fare", 45.0, ())], ("GST", "", 0.05, 0.0), 45.0, "USD")
    blank = _receipt("Smudged", "2026-05-09", [_line(1, "Something", 0.0, ())], ("GST", "", 0.05, 0.0), 0.0)
    claim, notes = claim_coding(
        [Receipt("a.jpg", FUEL), Receipt("usd.jpg", usd), Receipt("blank.jpg", blank),
         Receipt("torn.jpg", None, "torn.jpg can't be opened as a picture"), Receipt("c.jpg", ROOM)],
        reference.tax,
    )  # fmt: skip
    assert notes == ["", "it is in USD (45.00) and the claim is in CAD", "its total could not be read",
                     "torn.jpg can't be opened as a picture", ""]  # fmt: skip
    assert [(li.description, li.amount) for li in claim.line_items[1:4]] == [
        ("usd.jpg: Airport Taxi 2026-05-08, 45.00 USD", 0.0), ("blank.jpg: not read", 0.0),
        ("torn.jpg: not read", 0.0)]  # fmt: skip
    assert (claim.currency, claim.subtotal, claim.tax_total, claim.grand_total) == ("CAD", 240.0, 26.4, 266.4)
    assert claim.confidence_score == 0.0  # not every receipt was read: a person looks


def test_a_claims_findings_say_whose_the_supplier_is_and_name_the_receipt_left_out():
    def report():
        return SimpleNamespace(issues=[Issue(ERROR, "MISSING_VENDOR", "No vendor name: type it from the page"),
                                       Issue(ERROR, "MISSING_INVOICE_NUMBER", "No invoice number: type it"),
                                       Issue(WARNING, "GL_UNASSIGNED", "no GL account yet: pick one", 1)])  # fmt: skip

    files = [{"kind": "claim", "name": "a.jpg", "first_page": 1, "pages": 1, "note": ""},
             {"kind": "claim", "name": "usd.pdf", "first_page": 2, "pages": 2, "note": "it is in USD (45.00) and the "
              "claim is in CAD"}]  # fmt: skip
    found = report()
    claim_findings(found, files)
    assert [i.message for i in found.issues[:3]] == [NO_SUPPLIER, NO_REFERENCE, "no GL account yet: pick one"]
    assert "employee being repaid" in NO_SUPPLIER and "reference" in NO_REFERENCE
    assert (found.issues[3].severity, found.issues[3].code) == (WARNING, NOT_ADDED)
    assert found.issues[3].message.startswith("usd.pdf (pages 2 to 3) is not in the claim's total: it is in USD")
    # The pages of one invoice, and an invoice of one file, are checked as any invoice is.
    for other in ([{**f, "kind": "pages"} for f in files], []):
        plain = report()
        claim_findings(plain, other)
        assert [i.message for i in plain.issues] == [i.message for i in report().issues]
