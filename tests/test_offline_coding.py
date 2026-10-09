"""No Azure and no AI model: invoices are read on this computer and coded from what AP approved before."""

import json
from dataclasses import replace

from ap_coder.config import Settings
from ap_coder.pipeline import InvoicePipeline
from ap_coder.reference_data import UNASSIGNED
from ap_coder.store import Store

from .conftest import SAMPLES

STEM = "harbourview_NS_HST_HPS-2026-0347"


def _offline_settings():
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


def test_a_pdf_is_read_and_coded_with_no_azure_and_no_model(tmp_path, reference):
    store = Store(tmp_path / "ap.db")
    result = InvoicePipeline(_offline_settings(), reference, store=store).process(SAMPLES / f"{STEM}.pdf")

    assert result.ok, result.error
    truth = json.loads((SAMPLES / "ground_truth" / f"{STEM}.json").read_text())
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
    truth = json.loads((SAMPLES / "ground_truth" / f"{STEM}.json").read_text())
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
        truth = json.loads((SAMPLES / "ground_truth" / f"{pdf.stem}.json").read_text())
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
