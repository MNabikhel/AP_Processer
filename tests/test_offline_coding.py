"""No Azure and no AI model: invoices are read on this computer and coded from what AP approved before."""

import json
from dataclasses import replace

from ap_coder.config import Settings
from ap_coder.pipeline import InvoicePipeline
from ap_coder.reference_data import UNASSIGNED
from ap_coder.store import Store

from .conftest import SAMPLES

STEM = "harbourview_NS_HST_HPS-2026-0347"


def test_a_tax_above_the_provinces_rate_is_not_a_partial_base():
    """14% HST on $1,000 while the province was read as Ontario (13%) is Nova Scotia's rate on the whole
    subtotal, not 13% on a $1,076.92 base: the rate the amounts show is kept, with the province it names (the
    tax check then flags the place of supply that differs). Less than the rate is still a partial base."""
    import datetime as dt

    from ap_coder.offline_coder import _tax_lines

    on = dt.date(2026, 4, 3)
    (hst,) = _tax_lines({"hst_amount": 140.0}, 1000.0, "ON", on)
    assert (hst.rate, hst.province, hst.taxable_amount, hst.tax_amount) == (0.14, "NS", 1000.0, 140.0)
    (rst,) = _tax_lines({"pst_amount": 63.0}, 1000.0, "MB", on)  # delivery exempt from Manitoba RST
    assert (rst.rate, rst.province, rst.taxable_amount) == (0.07, "MB", 900.0)


def _offline_settings():
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


def test_a_pdf_is_read_and_coded_with_no_azure_and_no_model(tmp_path, reference):
    store = Store(tmp_path / "ap.db")
    result = InvoicePipeline(_offline_settings(), reference, store=store).process(SAMPLES / f"{STEM}.pdf")

    assert result.ok, result.error
    truth = json.loads((SAMPLES / "ground_truth" / f"{STEM}.json").read_text(encoding="utf-8"))
    coding = result.coding.coding
    assert result.coding.model.startswith("local reader")
    assert result.extraction.model_id.startswith("local-")
    assert coding.invoice_number == truth["invoice_number"]
    assert abs(coding.grand_total - truth["grand_total"]) < 0.01
    assert result.capture is not None and result.invoice_id
    assert len(coding.line_items) == len(truth["line_items"])
    # Nothing approved yet: an account comes only from the chart's own names, with its reason, else none.
    codes = set(reference.chart_of_accounts.codes)
    assert all(li.predicted_gl_code in codes | {UNASSIGNED} and li.reasoning_justification for li in coding.line_items)
    assert result.report.requires_review


def test_approved_coding_is_reused_for_the_next_invoice_from_that_vendor(tmp_path, reference):
    store = Store(tmp_path / "ap.db")
    pipe = InvoicePipeline(_offline_settings(), reference, store=store)
    first = pipe.process(SAMPLES / f"{STEM}.pdf")
    truth = json.loads((SAMPLES / "ground_truth" / f"{STEM}.json").read_text(encoding="utf-8"))
    final = dict(first.output)
    final["line_items"] = [
        {**li, "predicted_gl_code": t["predicted_gl_code"]}
        for li, t in zip(first.output["line_items"], truth["line_items"] * 10, strict=False)
    ]
    store.approve_invoice(first.invoice_id, final, "Pat")

    again = pipe.process(SAMPLES / f"{STEM}.pdf")
    assert again.ok, again.error
    assert [li.predicted_gl_code for li in again.coding.coding.line_items] == [
        li["predicted_gl_code"] for li in final["line_items"]
    ]


def test_every_sample_reads_right_offline(tmp_path, reference):
    """All ten samples, no Azure and no model: header, lines, provinces and tax lines as the ground truth;
    nothing is wrong, only accounts left to pick."""
    pipe = InvoicePipeline(_offline_settings(), reference, store=Store(tmp_path / "ap.db"))
    for pdf in sorted(SAMPLES.glob("*.pdf")):
        truth = json.loads((SAMPLES / "ground_truth" / f"{pdf.stem}.json").read_text(encoding="utf-8"))
        result = pipe.process(pdf)
        assert result.ok, (pdf.name, result.error)
        got = result.output
        for field in ("vendor_name", "invoice_number", "invoice_date", "supplier_province", "ship_to_province"):
            assert got[field] == truth[field], (pdf.name, field)
        for field in ("subtotal", "tax_total", "grand_total"):
            assert abs(got[field] - truth[field]) < 0.011, (pdf.name, field)
        assert [round(li["amount"], 2) for li in got["line_items"]] == [li["amount"] for li in truth["line_items"]]
        taxes = [(t["tax_type"], t["province"], t["rate"]) for t in got["tax_lines"]]
        assert taxes == [(t["tax_type"], t["province"], t["rate"]) for t in truth["tax_lines"]], pdf.name
        assert not [i for i in result.report.issues if i.severity == "error"], pdf.name


def test_queue_card_confidence_follows_the_review_screen(tmp_path, reference):
    from ap_coder.store import REVIEW

    store = Store(tmp_path / "ap.db")
    result = InvoicePipeline(_offline_settings(), reference, store=store).process(SAMPLES / f"{STEM}.pdf")
    assert store.get_invoice(result.invoice_id)["status"] == REVIEW
    assert store.refresh_confidence(result.invoice_id, 0.42, True)
    assert store.get_invoice(result.invoice_id)["adjusted_confidence"] == 0.42
    assert not store.refresh_confidence(result.invoice_id, 0.42, True)  # unchanged: nothing written
