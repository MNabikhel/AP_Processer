import json
import os

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


def test_watch_works_offline_without_azure_or_a_model(tmp_path, monkeypatch):
    """The folder watcher reads invoices on this computer like the dashboard does: no Azure and no local model is
    the offline pilot set-up, not "not set up yet"."""
    import os
    import shutil

    from ap_coder.store import Store

    for key in [k for k in os.environ if k.startswith(("AZURE_", "AP_LLM_"))]:
        monkeypatch.delenv(key)
    folder = tmp_path / "invoices"
    folder.mkdir()
    pdf = shutil.copy(SAMPLES / "northwind_ON_HST_NW-2026-0912.pdf", folder)
    os.utime(pdf, (1_700_000_000, 1_700_000_000))  # copied in long ago: settled
    db = tmp_path / "ap.db"
    env = tmp_path / "none.env"
    assert cli.main(["--env-file", str(env), "--db", str(db), "watch", str(folder), "--once", "--cache-dir", ""]) == 0
    assert [i["status"] for i in Store(db).list_invoices()] == ["review"]


def test_render_sample_pdf_pages():
    pdf = SAMPLES / "northwind_ON_HST_NW-2026-0912.pdf"
    images = render_page_images(pdf, max_pages=1)
    assert len(images) == 1 and images[0].data.startswith(b"\x89PNG")


def _cli(tmp_path, *args):
    return cli.main(["--env-file", str(tmp_path / "none.env"), "--db", str(tmp_path / "a.db"), *args])


def test_cli_mistakes_are_one_line_and_a_non_zero_exit(tmp_path, monkeypatch, capsys):
    """A typo, nothing processed yet, no Azure: one plain line each, never a traceback."""
    for key in [k for k in os.environ if k.startswith("AZURE_")]:
        monkeypatch.delenv(key)
    assert _cli(tmp_path, "process", str(tmp_path / "typo")) == 2
    assert "Not found:" in capsys.readouterr().err
    assert _cli(tmp_path, "share-report", "--predictions", str(tmp_path / "out"), "-o", str(tmp_path / "r.md")) == 1
    assert "No report written: No processed invoices" in capsys.readouterr().err
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert _cli(tmp_path, "extract", str(pdf), "-o", str(tmp_path / "x"), "--cache-dir", "") == 1
    assert "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT is not set" in capsys.readouterr().err
    assert _cli(tmp_path, "extract", str(tmp_path / "typo.pdf"), "-o", str(tmp_path / "x")) == 2


def test_cli_labels_with_nothing_processed_writes_nothing(tmp_path, capsys):
    dest = tmp_path / "labels.xlsx"
    args = ["labels", "--predictions", str(tmp_path / "out"), "-o", str(dest)]
    assert _cli(tmp_path, *args) == 1 and not dest.exists()  # so the next run (after `process`) is not refused
    assert "Nothing to label" in capsys.readouterr().err
