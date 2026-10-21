import json
import os

from ap_coder.ai.imaging import render_page_images
from ap_coder.core.config import Settings
from ap_coder.ops import cli

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

    from ap_coder.core.store import Store

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


def test_a_command_that_could_not_read_something_does_not_exit_0(tmp_path, monkeypatch, capsys):
    """An email that could not be read, or a page read that failed, is said on screen: the exit code says it too, so
    Task Scheduler or a script sees it."""
    from types import SimpleNamespace

    from ap_coder.ai import page_worker

    for key in [k for k in os.environ if k.startswith("AZURE_")]:
        monkeypatch.delenv(key)
    (tmp_path / "bad.msg").write_bytes(b"not an outlook email" * 40)
    good = tmp_path / "good.pdf"
    good.write_bytes(b"%PDF-1.4 good")

    class Pipeline:
        def __init__(self, *args, **kwargs):
            pass

        def process_many(self, inputs, workers=1, on_result=None):
            return [SimpleNamespace(ok=True, summary=lambda p=p: {"status": "", "file": p.name}) for p in inputs]

    monkeypatch.setattr(cli, "InvoicePipeline", Pipeline)
    out = ["-o", str(tmp_path / "out"), "--cache-dir", ""]
    assert _cli(tmp_path, "process", str(tmp_path / "bad.msg"), str(good), "--no-db", *out) == 1
    assert "could not be read" in capsys.readouterr().err
    (tmp_path / "bad2.msg").write_bytes(b"not an outlook email" * 40)
    assert _cli(tmp_path, "extract", str(tmp_path / "bad2.msg"), *out) == 2  # nothing left to extract

    monkeypatch.setattr(page_worker, "self_test_if_due", lambda *a, **k: None)
    monkeypatch.setattr(page_worker, "ready", lambda *a, **k: ("ovis", ""))
    monkeypatch.setattr(page_worker, "run_queue", lambda *a, **k: [page_worker.ReadOutcome(1, "failed")])
    assert _cli(tmp_path, "read-pages", "--cache-dir", "") == 1


def test_watch_uses_the_dashboard_invoices_folder(tmp_path, monkeypatch):
    """With AP_DB_PATH (or --db) the dashboard picks up files from the invoices folder next to the database: so
    does ``watch``, as its help says."""
    seen = []
    monkeypatch.setattr(cli, "watch_once", lambda args, settings, store, folder: seen.append(folder) or (0, 0))
    monkeypatch.setenv("AP_DB_PATH", str(tmp_path / "srv" / "ap_coder.db"))
    assert cli.main(["--env-file", str(tmp_path / "none.env"), "watch", "--once"]) == 0
    assert cli.main(["--env-file", str(tmp_path / "none.env"), "--db", str(tmp_path / "b" / "x.db"), "watch",
                     "--once"]) == 0  # fmt: skip
    assert seen == [tmp_path / "srv" / "invoices", tmp_path / "b" / "invoices"]


def test_watch_and_read_pages_say_in_the_data_folder_that_they_run(tmp_path, monkeypatch):
    """B-167: an update stopped the older version's server but not its ``watch`` (nor ``read-pages``): each now
    leaves a note in the data folder while it runs (pid, folder, version), so the launcher finds and stops it."""
    import ap_coder
    from ap_coder.ai import page_worker
    from ap_coder.core import paths

    data = paths.private_dir()
    seen = []

    def note(command):
        found = [json.loads(p.read_text(encoding="utf-8")) for p in data.glob(".ap_coder_worker-*.json")]
        seen.append(found)
        assert found == [{"pid": os.getpid(), "command": command, "folder": str(paths.PROJECT_DIR),
                          "version": ap_coder.__version__, "started": found[0]["started"]}]  # fmt: skip

    monkeypatch.setattr(cli, "watch_once", lambda *args: note("watch") or (0, 0))
    assert cli.main(["--env-file", str(tmp_path / "none.env"), "--db", str(tmp_path / "a.db"), "watch", "--once"]) == 0
    monkeypatch.setattr(page_worker, "self_test_if_due", lambda *a, **k: note("read-pages"))
    monkeypatch.setattr(page_worker, "ready", lambda *a, **k: ("", "no model"))
    assert _cli(tmp_path, "read-pages", "--cache-dir", "") == 1
    assert len(seen) == 2 and list(data.glob(".ap_coder_worker-*")) == []  # removed when each stopped


def test_cli_labels_with_nothing_processed_writes_nothing(tmp_path, capsys):
    dest = tmp_path / "labels.xlsx"
    args = ["labels", "--predictions", str(tmp_path / "out"), "-o", str(dest)]
    assert _cli(tmp_path, *args) == 1 and not dest.exists()  # so the next run (after `process`) is not refused
    assert "Nothing to label" in capsys.readouterr().err


# --- Doctor: the checks an offline install needs ---------------------------------------------------------------


def test_doctor_python_range_matches_the_launcher():
    from ap_coder.ops.doctor import FAIL, PASS, _python_check

    assert _python_check((3, 12, 1))[1] == PASS and _python_check((3, 11, 0))[1] == PASS
    assert _python_check((3, 13, 2))[1] == PASS
    # No upper bound, like the launchers: a Mac with only Python 3.14 (or newer) is fine.
    assert _python_check((3, 14, 5))[1] == PASS and _python_check((3, 15, 0))[1] == PASS
    assert _python_check((3, 10, 4))[1] == FAIL  # requires-python is 3.11 (CI tests 3.11 to 3.14)
    assert _python_check((3, 9, 7))[1] == FAIL


def test_doctor_missing_packages_fail_with_the_offline_fix(reference, monkeypatch):
    """pymupdf reads every PDF, so it is a failure; the fix never needs the internet (pip install -e .)."""
    from ap_coder.core.config import LocalLLMSettings
    from ap_coder.ops import doctor

    gone = {"pymupdf", "fastapi", "uvicorn", "pandas", "python-dotenv"}
    real = doctor._version
    monkeypatch.setattr(doctor, "_version", lambda pkg: None if pkg in gone else real(pkg))
    checks = {c.area: c for c in doctor.run_checks(Settings(llm=LocalLLMSettings(provider="off")), lambda: reference)}
    for pkg in gone:
        check = checks[f"package {pkg}"]
        assert check.status == doctor.FAIL and "APProcessor.bat" in check.detail
        assert "pip install" not in check.detail
    assert "PDFs can't be read" in checks["package pymupdf"].detail


def test_doctor_fails_without_pillow(reference, monkeypatch):
    """Pillow turns every photo or scanned image into a PDF to read: not only for --vision."""
    from ap_coder.core.config import LocalLLMSettings
    from ap_coder.ops import doctor

    real = doctor._version
    monkeypatch.setattr(doctor, "_version", lambda pkg: None if pkg == "pillow" else real(pkg))
    checks = doctor.run_checks(Settings(llm=LocalLLMSettings(provider="off")), lambda: reference, llm_checks=False)
    (pillow,) = [c for c in checks if c.area == "package pillow"]
    assert pillow.status == doctor.FAIL and "images can't be read" in pillow.detail
    assert doctor.exit_code(checks) != 0


def test_doctor_skips_the_local_model_when_ai_coding_is_off(reference, monkeypatch):
    from ap_coder.core.config import LocalLLMSettings
    from ap_coder.ops import doctor

    def never(*args, **kwargs):
        raise AssertionError("asked the local server although AI coding is off")

    monkeypatch.setattr(doctor, "check_server", never)
    checks = {c.area: c for c in doctor.run_checks(Settings(llm=LocalLLMSettings(provider="off")), lambda: reference)}
    assert checks["local model"].status == doctor.SKIP and "turned off" in checks["local model"].detail


def test_commands_use_the_database_the_dashboard_uses(monkeypatch, tmp_path):
    """AP_DB_PATH picks the dashboard's database; read-pages and the other commands use the same one."""
    monkeypatch.setenv("AP_DB_PATH", str(tmp_path / "chosen.db"))
    assert cli.build_parser().parse_args(["read-pages"]).db == str(tmp_path / "chosen.db")
    monkeypatch.delenv("AP_DB_PATH")
    assert cli.build_parser().parse_args(["read-pages"]).db.endswith("ap_coder.db")
