import json

from ap_coder import cli
from ap_coder.config import Settings
from ap_coder.imaging import render_page_images

from .conftest import SAMPLES


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "coder-prod")
    monkeypatch.setenv("AZURE_OPENAI_MODEL_NAME", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_TEMPERATURE", "none")
    monkeypatch.setenv("AZURE_OPENAI_SUPPORTS_VISION", "false")
    monkeypatch.setenv("AP_VISION", "true")
    monkeypatch.setenv("AP_REVIEW_THRESHOLD", "0.9")
    s = Settings.from_env(tmp_path / "missing.env")
    assert s.openai.deployment == "coder-prod"
    assert s.openai.model_name == "gpt-4o-mini"
    assert s.openai.temperature is None
    assert s.openai.supports_vision is False
    assert s.engine.vision is True
    assert s.engine.review_threshold == 0.9

    s2 = s.with_overrides(di_model="prebuilt-invoice", deployment="other", vision=False)
    assert s2.document_intelligence.model_id == "prebuilt-invoice"
    assert s2.openai.deployment == "other"
    assert s2.engine.vision is False


def test_cli_schema_command(capsys):
    assert cli.main(["--db", "missing.db", "schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert "taxes_applied" in schema["properties"]["line_items"]["items"]["properties"]


def test_cli_evaluate_command(capsys):
    gt = str(SAMPLES / "ground_truth")
    assert cli.main(["evaluate", "--predictions", gt, "--ground-truth", gt]) == 0
    assert json.loads(capsys.readouterr().out)["meets_target"] is True


def test_render_sample_pdf_pages():
    pdf = SAMPLES / "northwind_ON_HST_NW-2026-0912.pdf"
    images = render_page_images(pdf, max_pages=1)
    assert len(images) == 1 and images[0].data.startswith(b"\x89PNG")
