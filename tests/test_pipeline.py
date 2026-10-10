import json

from ap_coder.config import Settings
from ap_coder.evaluation import evaluate
from ap_coder.extraction import DocumentExtractor
from ap_coder.inference import InvoiceCoder
from ap_coder.pipeline import InvoicePipeline, discover_inputs, write_outputs

from .conftest import SAMPLE_STEM, SAMPLES, FakeDIClient, FakeOpenAI, make_completion


def _pipeline(settings, reference, *completions, di_raw=None):
    extractor = DocumentExtractor(settings.document_intelligence, client=FakeDIClient(di_raw or {}))
    coder = InvoiceCoder(settings, reference, client=FakeOpenAI(*completions))
    return InvoicePipeline(settings, reference, extractor=extractor, coder=coder)


def test_end_to_end_from_markdown(tmp_path, settings, reference, ground_truth, sample_markdown_path):
    pipe = _pipeline(settings, reference, make_completion(json.dumps(ground_truth)))
    result = pipe.process(sample_markdown_path)

    assert result.ok, result.error
    assert result.report.requires_review is False
    written = write_outputs(result, tmp_path)
    names = sorted(p.name for p in written)
    assert names == [f"{SAMPLE_STEM}.json", f"{SAMPLE_STEM}.validation.json"]

    out = json.loads((tmp_path / f"{SAMPLE_STEM}.json").read_text())
    distribution = out.pop("gl_distribution")
    assert out == ground_truth  # target schema fields exactly as coded
    assert round(sum(e["amount"] for e in distribution), 2) == ground_truth["grand_total"]
    assert {e["gl_code"] for e in distribution if e["kind"] == "tax"} == {"2310"}

    gt_dir = tmp_path / "gt"
    gt_dir.mkdir()
    (gt_dir / f"{SAMPLE_STEM}.json").write_text((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    report = evaluate(tmp_path, gt_dir)
    assert report.meets_target and report.gl_accuracy == 1.0


def test_end_to_end_from_pdf(tmp_path, settings, reference, ground_truth):
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"%PDF fake")
    raw = {"modelId": "prebuilt-layout", "content": "# Invoice", "pages": [{"words": []}]}
    pipe = _pipeline(settings, reference, make_completion(json.dumps(ground_truth)), di_raw=raw)
    result = pipe.process(pdf)
    assert result.ok
    written = {p.name for p in write_outputs(result, tmp_path / "out")}
    assert "inv.extraction.md" in written


def test_failure_is_captured_not_raised(tmp_path, settings, reference, sample_markdown_path):
    pipe = _pipeline(settings, reference, make_completion("", refusal="nope"))
    result = pipe.process(sample_markdown_path)
    assert not result.ok and "refused" in result.error
    write_outputs(result, tmp_path)
    meta = json.loads((tmp_path / f"{SAMPLE_STEM}.validation.json").read_text())
    assert meta["status"] == "failed"
    assert result.summary()["status"] == "failed"


def test_discover_inputs(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"")
    (tmp_path / "b.TIFF").write_bytes(b"")
    (tmp_path / "notes.docx").write_bytes(b"")
    assert [p.name for p in discover_inputs([tmp_path])] == ["a.pdf", "b.TIFF"]


def test_process_many_parallel(settings, reference, ground_truth, sample_markdown_path):
    body = json.dumps(ground_truth)
    pipe = _pipeline(Settings(), reference, make_completion(body), make_completion(body))
    results = pipe.process_many([sample_markdown_path, sample_markdown_path], workers=2)
    assert all(r.ok for r in results)


def test_the_page_readers_text_reaches_capture_with_azure_too(settings, reference, ground_truth):
    """Coded by Azure OpenAI (not the offline reader): the page reader's reading is still one more reader."""
    pdf = SAMPLES / f"{SAMPLE_STEM}.pdf"
    transcript = (SAMPLES / f"{SAMPLE_STEM}.md").read_text(encoding="utf-8")
    raw = {"modelId": "prebuilt-layout", "content": transcript, "pages": [{"words": []}]}
    pipe = _pipeline(settings, reference, make_completion(json.dumps(ground_truth)), di_raw=raw)
    result = pipe.process(pdf, page_text=[transcript])
    assert result.ok, result.error
    assert any("vlm" in (f.sources or {}) for f in result.capture.fields.values())


def test_local_model_suggestions_only_fill_uncoded_lines(monkeypatch):
    """Printed line numbers can repeat (a second page numbering from 1 again): the local model's account for the
    uncoded line must not overwrite the coded line with the same number."""
    from types import SimpleNamespace

    import ap_coder.inference as inference
    from ap_coder.schema import InvoiceCoding

    data = json.loads((SAMPLES / "ground_truth" / "pacific_BC_GST_PST_PO-77120.json").read_text())
    data["line_items"][1]["line_number"] = 1  # lines numbered 1, 1, 3
    data["line_items"][1]["predicted_gl_code"] = "UNASSIGNED"
    coding = InvoiceCoding.model_validate(data)
    coded = [li.predicted_gl_code for li in coding.line_items]
    asked = {}

    def suggest(coder, vendor, lines, history=""):
        asked["lines"] = lines
        return {n: ("6999", "", "a guess") for n, _, _ in lines}

    monkeypatch.setattr(inference, "suggest_accounts", suggest)
    result = SimpleNamespace(coding=coding, model="")
    InvoicePipeline._local_accounts(SimpleNamespace(coder=SimpleNamespace(_local_model="m")), result, "")

    assert len(asked["lines"]) == 1  # only the uncoded line is asked about
    assert [li.predicted_gl_code for li in coding.line_items] == [coded[0], "6999", *coded[2:]]
    assert "local model" not in coding.line_items[0].reasoning_justification
    assert result.model == "local reader + m"


def test_an_invoice_read_again_is_no_duplicate_of_itself(tmp_path, settings, reference, ground_truth,
                                                         sample_markdown_path):  # fmt: skip
    from ap_coder.store import Store

    store = Store(tmp_path / "ap.db")
    pipe = _pipeline(settings, reference, *[make_completion(json.dumps(ground_truth))] * 3)
    pipe.store = store
    first = pipe.process(sample_markdown_path)
    assert first.invoice_id and "DUPLICATE_INVOICE" not in {i.code for i in first.report.issues}

    again = pipe.process(sample_markdown_path, save=False, invoice_id=first.invoice_id)
    assert "DUPLICATE_INVOICE" not in {i.code for i in again.report.issues}
    without = pipe.process(sample_markdown_path, save=False)  # a new copy of it is still a duplicate
    assert "DUPLICATE_INVOICE" in {i.code for i in without.report.issues}
