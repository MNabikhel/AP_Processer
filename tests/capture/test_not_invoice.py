"""A document that is not an invoice says so (B-2217): one warning, "this may not be an invoice: <why>", from what is
printed (its title, a table of invoices with balances, or nothing an invoice has); an invoice never gets it."""

import pytest

from ap_coder.bench import jde_company, jde_invoices
from ap_coder.capture import analyze
from ap_coder.capture.bridge import review_issues
from tests.pdfs import long_invoice, text_pdf

WHY = {
    "statement": 'its title is "Statement of account"',
    "remittance_advice": 'its title is "Remittance advice"',
    "staff_notice": 'its title is "Staff notice"',
    "quote": 'its title is "Quotation"',
    "packing_slip": 'its title is "Packing slip"',
    "order_confirmation": 'its title is "Order confirmation"',
}
SAID = ("this may not be an invoice: {why}. If it is not one, reject it (Reject, with the reason); if it is, check it "
        "as any other")  # fmt: skip


def _finding(path) -> list[tuple[str, str]]:
    capture = analyze(path, ocr=False, tables=False, second_read=False)
    return [(severity, words) for severity, code, words in review_issues(capture, "CAD") if code == "NOT_AN_INVOICE"]


@pytest.fixture(scope="module")
def documents():
    return jde_invoices.other_documents(jde_company.build())


@pytest.mark.parametrize("name", jde_invoices.OTHER_DOCUMENTS)
def test_a_document_that_is_not_an_invoice_says_so(tmp_path, documents, name):
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(documents[name])
    assert _finding(path) == [("warning", SAID.format(why=WHY[name]))]


def test_a_statement_that_lost_its_title_is_told_by_its_table(tmp_path):
    rows = [(50, 60, "Northgate Supply Ltd."), (50, 150, "Invoice"), (170, 150, "Date"), (450, 150, "Balance")]
    for n in range(3):
        rows += [(50, 168 + 16 * n, f"7120{n}"), (170, 168 + 16 * n, "2026-01-05"), (450, 168 + 16 * n, "412.35")]
    why = "it lists 3 invoices with their balances or payments, as a statement or a remittance advice does"
    assert _finding(text_pdf(tmp_path, rows)) == [("warning", SAID.format(why=why))]


def test_a_letter_with_no_invoice_number_and_no_amounts(tmp_path):
    letter = [
        (50, 60, "Northgate Supply Ltd."),
        (50, 120, "Dear customer,"),
        (50, 140, "Our office moves to 88 Harbour Street on the first of March."),
        (50, 180, "Regards,"),
    ]
    assert _finding(text_pdf(tmp_path, letter)) == [("warning", SAID.format(
        why="no invoice number and no amounts were found on it"))]  # fmt: skip


def test_an_invoice_is_not_told_it_is_something_else(tmp_path):
    plain, _, _ = long_invoice(tmp_path, 1, per_page=3)
    assert _finding(plain) == []
    head = [(50, 60, "Northgate Supply Ltd."), (400, 60, "INVOICE"), (400, 76, "Invoice No: NG-5521"),
            (400, 92, "Invoice Date: 2026-01-12"), (50, 200, "Description"), (500, 200, "Amount"),
            (50, 216, "Filter cartridge 10 in."), (500, 216, "100.00"), (400, 300, "Subtotal"), (500, 300, "100.00"),
            (400, 316, "GST 5%"), (500, 316, "5.00"), (400, 332, "Total"), (500, 332, "105.00")]  # fmt: skip
    # Its packing slip and quote named as references, a payment stub at its foot, its own number beside a balance.
    more = [(400, 108, "Packing slip: PS-7741"), (400, 124, "Quote"), (460, 124, "Q-20931"),
            (50, 640, "REMITTANCE ADVICE"), (50, 656, "Please return this part with your payment"),
            (50, 672, "Invoice"), (170, 672, "Date"), (450, 672, "Balance"), (50, 688, "NG-5521"),
            (170, 688, "2026-01-12"), (450, 688, "105.00")]  # fmt: skip
    assert _finding(text_pdf(tmp_path, head + more, name="references.pdf")) == []
    # A packing slip named under an invoice's own title is a remark, not what the document is.
    assert _finding(text_pdf(tmp_path, [*head, (400, 140, "Packing slip")], name="titled.pdf")) == []
    # A utility's bill is titled "Statement": an invoice all the same.
    bill = [(400, 60, "STATEMENT") if row[2] == "INVOICE" else row for row in head if "Invoice" not in row[2]]
    assert _finding(text_pdf(tmp_path, [*bill, (400, 76, "Bill Number: 005308")], name="utility.pdf")) == []


def test_a_document_that_is_not_an_invoice_is_kept_for_a_person_never_rejected(tmp_path, documents):
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.coding.bulk import AFTER_PROCESSING
    from ap_coder.core.store import REVIEW, Store, load_sample_setup
    from tests.ai.test_uniform_reading import _settings

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    path = tmp_path / "statement.pdf"
    path.write_bytes(documents["statement"])
    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(path)
    invoice = store.get_invoice(result.invoice_id)
    assert invoice["status"] == REVIEW and invoice["requires_review"]
    assert [i["severity"] for i in invoice["validation"]["issues"] if i["code"] == "NOT_AN_INVOICE"] == ["warning"]
    assert "NOT_AN_INVOICE" in AFTER_PROCESSING  # kept when today's checks are worked out again


# --- A file that holds several invoices (D-261) ------------------------------------------------------------------

SEVERAL = ("this file seems to hold {n} invoices, each with its own number and total ({which}). Split it into one "
           "file per invoice, add each one on Add invoices, then reject this one (Reject, with the reason); if it is "
           "one invoice after all, check it as any other")  # fmt: skip


def _several(path) -> list[tuple[str, str]]:
    capture = analyze(path, ocr=False, tables=False, second_read=False)
    return [(severity, words) for severity, code, words in review_issues(capture, "CAD") if code == "SEVERAL_INVOICES"]


def _bill(number: str, page: int, amount: str, *, above: bool = False) -> list[tuple]:
    """A made-up invoice on page ``page``: its number beside its label, or under it (``above``), one line of
    ``amount``, its subtotal, GST and total."""
    cents = round(float(amount) * 100)
    gst = round(cents * 0.05)
    label = [(400, 76, "Invoice No.", 10, page), (400, 90, number, 10, page)] if above else [
        (400, 76, f"Invoice No: {number}", 10, page)]  # fmt: skip
    return [(50, 60, "Northgate Supply Ltd.", 10, page), (400, 60, "INVOICE", 10, page), *label,
            (400, 108, "Invoice Date: 2026-01-12", 10, page), (50, 200, "Description", 10, page),
            (500, 200, "Amount", 10, page), (50, 216, "Filter cartridge 10 in.", 10, page),
            (500, 216, amount, 10, page),
            (400, 300, "Subtotal", 10, page), (500, 300, amount, 10, page), (400, 316, "GST 5%", 10, page),
            (500, 316, f"{gst / 100:.2f}", 10, page), (400, 332, "Total Due", 10, page),
            (500, 332, f"{(cents + gst) / 100:.2f}", 10, page)]  # fmt: skip


def test_a_file_with_an_invoice_on_each_page_is_flagged_to_be_split(tmp_path):
    two = text_pdf(tmp_path, _bill("NG-5521", 1, "105.00") + _bill("NG-5534", 2, "88.20", above=True), pages=2)
    assert _several(two) == [("warning", SEVERAL.format(n=2, which="NG-5521 on page 1, NG-5534 on page 2"))]
    # The second invoice runs on to a third page, its total there.
    second = _bill("NG-5534", 2, "88.20")
    rows = _bill("NG-5521", 1, "105.00") + second[:8] + [(r[0], r[1] - 240, r[2], 10, 3) for r in second[8:]]
    three = text_pdf(tmp_path, rows, name="three.pdf", pages=3)
    assert _several(three) == [("warning", SEVERAL.format(n=2, which="NG-5521 on page 1, NG-5534 on pages 2 to 3"))]


def test_one_invoice_of_several_pages_is_not_flagged(tmp_path):
    # Its lines over three pages, its number only on the first.
    assert _several(long_invoice(tmp_path, 3, per_page=12)[0]) == []
    # Its number printed again at the top of each page, its total on the last.
    first = _bill("NG-5521", 1, "105.00")
    rows = first[:8] + [(400, 76, "Invoice No: NG-5521", 10, 2), (400, 92, "Page 2 of 2", 10, 2)]
    rows += [(r[0], r[1] - 160, r[2], 10, 2) for r in first[8:]]  # its totals on the second page
    assert _several(text_pdf(tmp_path, rows, name="repeated.pdf", pages=2)) == []
    # A second page of terms, then a packing slip naming the same invoice.
    terms = [(50, 60, "Terms and conditions", 10, 2), (50, 80, "Payment is due within 30 days.", 10, 2)]
    slip = [(50, 60, "PACKING SLIP", 10, 3), (400, 76, "Invoice No: NG-5521", 10, 3), (50, 200, "Filter", 10, 3)]
    assert _several(text_pdf(tmp_path, _bill("NG-5521", 1, "105.00") + terms + slip, name="terms.pdf", pages=3)) == []
    # A continuation page whose number reads differently, with no total of the first page's own: one invoice.
    rows = first[:8] + [(400, 76, "Invoice No: NG-5S21", 10, 2)] + [(r[0], r[1] - 160, r[2], 10, 2) for r in first[8:]]
    assert _several(text_pdf(tmp_path, rows, name="misread.pdf", pages=2)) == []


def test_a_file_of_several_invoices_is_kept_for_a_person(tmp_path):
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.coding.bulk import AFTER_PROCESSING
    from ap_coder.core.store import REVIEW, Store, load_sample_setup
    from ap_coder.web.pages.review import SAID_FIRST
    from tests.ai.test_uniform_reading import _settings

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    path = text_pdf(tmp_path, _bill("NG-5521", 1, "105.00") + _bill("NG-5534", 2, "88.20"), pages=2)
    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(path)
    invoice = store.get_invoice(result.invoice_id)
    assert invoice["status"] == REVIEW and invoice["requires_review"]
    assert [i["severity"] for i in invoice["validation"]["issues"] if i["code"] == "SEVERAL_INVOICES"] == ["warning"]
    assert "SEVERAL_INVOICES" in AFTER_PROCESSING and "SEVERAL_INVOICES" in SAID_FIRST
