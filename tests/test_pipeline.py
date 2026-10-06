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
    assert out == ground_truth  # exact target schema, nothing extra

    report = evaluate(tmp_path, SAMPLES / "ground_truth")
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
