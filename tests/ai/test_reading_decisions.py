"""Regression tests for bugs found by the reading-and-decisions bug sweep: each one reproduces a bug that was
real (a wrong value shown as right, a wrong message, an invoice approved without a person when it should not be),
so it is never made again."""

from dataclasses import replace
from pathlib import Path

import pymupdf

from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.core.config import Settings
from ap_coder.core.store import Store


def _pdf(path: Path, pages: list[list[str | tuple[float, str]]]) -> Path:
    """A digital PDF: each page's lines, one under the other (a (x, text) pair is set at that x)."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page(width=612, height=792)
        y = 50.0
        for line in lines:
            x, text = line if isinstance(line, tuple) else (50.0, line)
            if text:
                page.insert_text((x, y), text, fontsize=10)
            y += 14
    doc.save(path)
    return path


def _offline(tmp_path: Path, reference) -> tuple[InvoicePipeline, Store]:
    s = Settings()
    store = Store(tmp_path / "ap.db")
    return InvoicePipeline(replace(s, llm=replace(s.llm, provider="off")), reference, store=store), store


def test_a_currency_assumed_from_a_us_address_is_said_as_assumed_not_as_cad(tmp_path, reference):
    """A US supplier's invoice that prints no currency is coded in US dollars (``default_currency``), and the review
    screen said "no currency printed: taken as CAD" (info), the opposite of what an approval posts."""
    pdf = _pdf(tmp_path / "us.pdf", [[
        "Acme Widgets Inc.", "100 Main Street, Buffalo, NY 14201", "", "INVOICE", "Invoice No: AW-5521",
        "Invoice Date: 2026-09-15", "Bill To: Northern Foods Ltd, 12 King St W, Toronto, ON M5H 1A1", "",
        "Description            Qty   Unit Price    Amount", "Widgets                 10       25.00      250.00", "",
        (300, "Subtotal  250.00"), (300, "Total  250.00"),
    ]])  # fmt: skip
    pipe, _store = _offline(tmp_path, reference)
    result = pipe.process(pdf)
    assert result.ok, result.error
    assert result.output["currency"] == "USD"
    (issue,) = [i for i in result.report.issues if i.code == "CURRENCY_NOT_FOUND"]
    assert issue.severity == "warning" and "USD" in issue.message and "CAD" not in issue.message


def test_a_credit_lines_quantity_and_unit_price_are_read_with_the_credits_sign(tmp_path):
    """A credit note's row "Returned pallets 4 50.00 200.00 CR" was read as 4 at 4.00 (the quantity taken for the
    unit price too): quantity x price was only ever matched against a positive amount, so a negative line never
    found its price. The coded line then posted a unit price of 4.00 and the review warned LINE_MATH."""
    from ap_coder.capture import build_layout
    from ap_coder.capture.reader import read_line_items

    pdf = _pdf(tmp_path / "credit.pdf", [[
        "Harbour Supply Ltd.", "CREDIT NOTE", "", "Description            Qty   Unit Price    Amount",
        "Returned pallets          4       50.00      200.00 CR",
        "Returned crates           2       25.00      (50.00)",
        "Damaged drums             3      -10.00      -30.00", "", (300, "Subtotal  280.00 CR"),
    ]])  # fmt: skip
    items = [(li.description, li.quantity, li.unit_price, li.amount) for li in read_line_items(build_layout(pdf))]
    assert items == [("Returned pallets", 4.0, -50.0, -200.0), ("Returned crates", 2.0, -25.0, -50.0),
                     ("Damaged drums", 3.0, -10.0, -30.0)]  # fmt: skip


_FRENCH = ["FACTURE FL-2026-0042", "Fournitures Laval Inc.", "Papier A4 12 42,25 $ 507,00 $",
           "Toner 4 155,15 $ 620,60 $", "Bureau 2 1 234,56 $ 2 469,12 $", "Livraison 1 125,00 $ 125,00 $",
           "Sous-total 3 721,72 $", "TPS (5 %) 186,09 $", "TVQ (9,975 %) 371,24 $", "Total 4 279,05 $"]  # fmt: skip


def test_figures_are_read_in_french_and_european_formats():
    """ "1 234,56 $" was counted as the figure 234 (and "42,25" not at all): the figures of a French invoice were
    fragments, so the page reader's figure-by-figure comparison measured nothing on them."""
    from decimal import Decimal

    from ap_coder.ai.figures import compare_figures, figures

    found = figures("\n".join(_FRENCH) + "\nNetto 1.234,56 EUR\nTotal 1 050.00")
    for amount in ("42.25", "507.00", "1234.56", "2469.12", "3721.72", "186.09", "4279.05", "1050.00"):
        assert Decimal(amount) in found, amount
    assert Decimal("234") not in found and Decimal("279") not in found
    assert figures("Total 1,234.56 and 3,907.81") == {
        Decimal("1234.56"): ["1,234.56"],
        Decimal("3907.81"): ["3,907.81"],
    }
    misread = compare_figures("Total 4 279,05 $", "Total 4 279,65 $")
    assert misread.confirmed == 0 and misread.differ == [("4 279,65", "4 279,05")]


def test_a_french_pdf_whose_hidden_text_is_not_the_page_fails_the_check(tmp_path):
    """The edited-PDF check (``TEXT_LAYER_MATCHES_PAGE``) passed a French invoice whose hidden subtotal and total
    differ from the page ("the page as printed shows 13 of the 13 figures"): its figures were never compared."""
    import datetime as dt

    from ap_coder.capture import analyze

    pdf = _pdf(tmp_path / "fr.pdf", [list(_FRENCH)])
    today = dt.date(2026, 10, 1)
    same = analyze(pdf, ocr=False, page_text=["\n".join(_FRENCH)], today=today)
    check = next(c for c in same.checks if c["code"] == "TEXT_LAYER_MATCHES_PAGE")
    assert check["ok"] and check["confirmed"] == check["figures"] >= 8, check
    printed = "\n".join(_FRENCH).replace("3 721,72", "2 721,72").replace("4 279,05", "3 279,05")
    tampered = analyze(pdf, ocr=False, page_text=[printed], today=today)
    check = next(c for c in tampered.checks if c["code"] == "TEXT_LAYER_MATCHES_PAGE")
    assert not check["ok"] and set(check["fields"]) >= {"subtotal", "grand_total"}, check


def test_a_page_the_page_reader_wrote_nothing_real_for_is_not_counted_as_read(tmp_path, monkeypatch):
    """A page OCR finds words on, for which OvisOCR2 wrote only a page number or its pangram, is read as blank: the
    page reader did not read it. It was still counted as read, so a two-page invoice whose second page it never
    read passed the "every page read by the page reader" gate and could be approved without a person."""
    import json

    from ap_coder.ai import page_reader, page_worker
    from ap_coder.ai.figures import PAGE_BREAK
    from ap_coder.ai.pipeline import autonomy_decision
    from ap_coder.core.config import PageReaderSettings
    from ap_coder.core.store import load_sample_setup
    from tests.conftest import SAMPLES

    model, stem = "ath-maas_ovisocr2", "northwind_ON_HST_NW-2026-0912"  # two pages
    s = Settings()
    settings = replace(s, llm=replace(s.llm, provider="off"), page_reader=PageReaderSettings())
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    store.set_setting(page_worker.TEST_KEY, json.dumps({"model": model, "ok": True, "fields_right": 9,
                                                        "fields_total": 9}))  # fmt: skip
    status = page_reader.ReaderStatus(reachable=True, lm_studio=True, model=model, document_reader=True,
                                      state="downloaded", candidates=[model], note="")  # fmt: skip
    first_page = (SAMPLES / f"{stem}.md").read_text(encoding="utf-8").split(PAGE_BREAK)[0]
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: status)
    monkeypatch.setattr(page_reader, "load_reader", lambda settings, model=None: "")
    monkeypatch.setattr(page_reader, "read_document", lambda *a, **k: page_reader.PageReading(
        model=model, pages=[first_page, "The quick brown fox jumps over the lazy dog."], seconds=[90.0, 80.0],
        page_count=2))  # fmt: skip
    monkeypatch.setattr(page_worker, "blank_pages", lambda settings, path: (set(), 2))  # OCR found words on both
    decided = []

    def spy(*args, **kwargs):
        decided.append(kwargs.get("pages_read"))
        return autonomy_decision(*args, **kwargs)

    monkeypatch.setattr("ap_coder.ai.pipeline.autonomy_decision", spy)
    result = InvoicePipeline(settings, store.reference_data(), store=store).process(SAMPLES / f"{stem}.pdf")
    assert result.ok and result.capture.page_count == 2
    outcome = page_worker.read_one(settings, store)
    assert outcome.status == "done", outcome
    assert decided[-1] == 1  # one of its two pages read


def _touchless_supplier(tmp_path, ground_truth):
    """A store with touchless processing on, and the ground truth's supplier touchless: 30 clean reviewed invoices
    (one of them stored, approved)."""
    from ap_coder.capture.supplier import AUTONOMOUS

    store = Store(tmp_path / "ap.db")
    store.set_touchless(True, "Mia")
    store.set_touchless_limit(1_000_000, "Mia")
    old = {**ground_truth, "invoice_number": "OLD-1", "invoice_date": "2025-06-01", "po_number": ""}
    store.approve_invoice(store.add_invoice(tmp_path / "first.pdf", old, {}), old, "jane")
    key = store.supplier_key_for(ground_truth["vendor_name"], ground_truth["gst_hst_registration_number"])
    clean = [{"field": f"f{n}", "ai_value": "a", "final_value": "a", "correct": True} for n in range(13)]
    for i in range(30):
        store.record_outcomes(key, 1000 + i, clean, display_name=ground_truth["vendor_name"],
                              at=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}")  # fmt: skip
    store.sync_autonomy(key)
    assert store.get_supplier_profile(key)["state"] == AUTONOMOUS
    return store


def _decision(store, doc, reference, tmp_path):
    """The touchless decision on ``doc``, read with every header field verified and every check passed."""
    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.capture.types import CaptureResult, FieldResult
    from ap_coder.capture.workflow import audit_pick, autonomy_decision
    from ap_coder.coding.validation import ERROR
    from ap_coder.core.schema import InvoiceCoding

    _, report = finalise_coding(InvoiceCoding.model_validate(doc), reference, Settings(), store=store)
    report.issues = [i for i in report.issues if i.severity != ERROR]  # the sample's GL codes: not what is tested
    path = next(p for n in range(200) if not audit_pick(p := tmp_path / f"inv{n}.pdf", 0.05))
    fields = {name: FieldResult(name, doc[name], 0.99, "verified") for name in
              ("vendor_name", "invoice_number", "invoice_date", "grand_total", "subtotal")}  # fmt: skip
    capture = CaptureResult(fields, checks=[{"code": "TOTALS_ADD_UP", "ok": True, "detail": ""}], page_count=1)
    key = store.supplier_key_for(doc["vendor_name"], doc["gst_hst_registration_number"])
    return autonomy_decision(store, key, store.get_supplier_profile(key), capture, report, path, output=doc,
                             pages_read=1)  # fmt: skip


def test_another_vendor_name_on_a_touchless_suppliers_gst_number_needs_a_person(tmp_path, ground_truth, reference):
    """The supplier's record is kept by its GST/HST business number: an invoice under a vendor name never seen before
    that prints a touchless supplier's GST/HST number took that supplier's record and was approved without a person
    (VENDOR_NEW was only worth knowing). A name never approved before always goes to a person."""
    store = _touchless_supplier(tmp_path, ground_truth)
    own = _decision(store, {**ground_truth, "invoice_number": "NEW-2"}, reference, tmp_path)
    assert own["auto"], own  # its own name: touchless
    other = {**ground_truth, "invoice_number": "NEW-3", "vendor_name": "Northwind Payments Centre"}
    decision = _decision(store, other, reference, tmp_path)
    assert not decision["auto"], decision
    assert decision["reason"] == "always a person: no invoice under this vendor name was approved before"
    # With a vendor master: the name is not in it, only the GST/HST number is.
    row = {"vendor_name": ground_truth["vendor_name"], "gst": ground_truth["gst_hst_registration_number"],
           "status": "active", "status_given": False, "erp_id": "V100", "terms": None, "default_gl": None}  # fmt: skip
    store.import_vendor_master([row])
    key = store.supplier_key_for(ground_truth["vendor_name"], ground_truth["gst_hst_registration_number"])
    clean = [{"field": f"f{n}", "ai_value": "a", "final_value": "a", "correct": True} for n in range(13)]
    for i in range(30):  # the supplier's record under its vendor master id
        store.record_outcomes(key, 2000 + i, clean, display_name=ground_truth["vendor_name"],
                              at=f"2026-01-02T00:{i // 60:02d}:{i % 60:02d}")  # fmt: skip
    assert _decision(store, {**ground_truth, "invoice_number": "NEW-4"}, reference, tmp_path)["auto"]
    decision = _decision(store, {**other, "invoice_number": "NEW-5"}, reference, tmp_path)
    assert not decision["auto"] and "only its GST/HST number" in decision["reason"], decision


def test_an_invoice_that_does_not_match_its_purchase_order_needs_a_person(tmp_path, ground_truth, reference):
    """PO matching only warns (PO_UNKNOWN, PO_NOT_RECEIVED, PO_OVER_BILLED...), and touchless processing let
    warnings through: a touchless supplier's invoice quoting a PO that is not in the list, or billing goods not yet
    received, was approved without a person. A purchase-order exception always goes to a person."""
    store = _touchless_supplier(tmp_path, ground_truth)

    def po(number: str, received: bool) -> list[dict]:
        return [{"po_number": number, "vendor_name": ground_truth["vendor_name"], "line_number": li["line_number"],
                 "description": li["description"], "quantity": li["quantity"], "unit_price": li["unit_price"],
                 "amount": li["amount"], "received_quantity": li["quantity"] if received else 0, "gl_code": "",
                 "cost_center": "", "status": "open"} for li in ground_truth["line_items"]]  # fmt: skip

    store.import_purchase_orders(po("PO-70001", received=True) + po("PO-70002", received=False))
    matched = _decision(store, {**ground_truth, "invoice_number": "NEW-7", "po_number": "PO-70001"}, reference,
                        tmp_path)  # fmt: skip
    assert matched["auto"], matched  # matches its PO, ordered and received: touchless
    for number, po_number in (("NEW-8", "PO-55555"), ("NEW-9", "PO-70002")):  # not in the list; not received
        decision = _decision(store, {**ground_truth, "invoice_number": number, "po_number": po_number}, reference,
                             tmp_path)  # fmt: skip
        assert decision == {"state": "autonomous", "auto": False, "audit": False,
                            "reason": "always a person: it does not match its purchase order"}, decision  # fmt: skip


def _billed(account_lines: list[str], number: str) -> list[str | tuple[float, str]]:
    return ["Harbour Supply Ltd.", "77 Water St, Halifax, NS B3H 1A1", "HST No. 123456782 RT0001", "", "INVOICE",
            f"Invoice No: {number}", "Invoice Date: 2026-09-20", "Bill To: Northern Foods Ltd, 1 Main St, Halifax, NS",
            "", "Description            Qty   Unit Price    Amount",
            "Pallets                   4       50.00      200.00", "", (300, "Subtotal  200.00"),
            (300, "HST 14%    28.00"), (300, "Total     228.00"), "",
            "Remit by EFT within 30 days. Customer account No: 45521", *account_lines]  # fmt: skip


def test_a_changed_bank_account_is_found_without_an_ai_model(tmp_path, reference):
    """The bank account an invoice asks to be paid into was only ever read by the AI coder: offline (the local
    reader, no model) it was always blank, so VENDOR_BANK_CHANGED, a payment-fraud signal that always needs a person,
    could never be raised, and a touchless supplier's invoice with new banking details went through untouched."""
    pipe, store = _offline(tmp_path, reference)
    usual = ["Banking details for EFT:", "Transit 12345  Institution 004  Account 1234567"]
    first = pipe.process(_pdf(tmp_path / "a.pdf", [_billed(usual, "HS-1001")]))
    assert first.ok and "1234567" in first.output["remit_bank_account"]
    store.approve_invoice(first.invoice_id, first.output, "jane")
    same = pipe.process(_pdf(tmp_path / "b.pdf", [_billed(usual, "HS-1002")]))
    assert "VENDOR_BANK_CHANGED" not in {i.code for i in same.report.issues}
    changed = ["Banking details for EFT:", "Transit 99881  Institution 010  Account 7654321"]
    fraud = pipe.process(_pdf(tmp_path / "c.pdf", [_billed(changed, "HS-1003")]))
    assert "VENDOR_BANK_CHANGED" in {i.code for i in fraud.report.issues}


def test_bank_details_are_read_only_where_the_page_gives_them():
    from ap_coder.ai.offline_coder import bank_details

    assert (
        bank_details("Remit by EFT within 30 days.\nCustomer account No: 45521\n100 Bank Street, Ottawa K1P 5N2") == ""
    )
    assert bank_details("Deposit on order 1 500.00 1,500.00\nWire harness 12 45.00 540.00\nEFT fee 1 2.50 2.50") == ""
    assert bank_details("IBAN: GB29 NWBK 6016 1331 9268 19") == "IBAN: GB29 NWBK 6016 1331 9268 19"
    assert bank_details("Paiement par virement : transit 12345, institution 815, compte 1234567") == (
        "virement : transit 12345, institution 815, compte 1234567"
    )


def test_days_in_a_late_interest_clause_are_not_the_net_days():
    """ "Due on receipt. Interest of 2% per month on accounts over 30 days" (common on Canadian invoices) was read
    as net 30: the due date came out 30 days late. Days in a late-payment clause say when interest starts."""
    from ap_coder.coding.terms import parse_terms, payment

    for printed in ("Payable upon receipt, interest 2% per month after 30 days",
                    "Due on receipt; 2% per month charged on overdue accounts over 30 days"):  # fmt: skip
        terms = parse_terms(printed)
        assert terms.on_receipt and terms.net_days == 0, printed
        assert str(payment({"payment_terms": printed, "invoice_date": "2026-01-15"}).due) == "2026-01-15"
    assert parse_terms("Net 30 days, interest 1.5% per month after 60 days").net_days == 30
    assert parse_terms("Net 30 days upon receipt of invoice").net_days == 30
