"""Sweep 4, first launch: what a pilot tester meets on a fresh laptop in the first hour (the black window, a page
that fails, a second copy started by mistake, the Quick start, reporting a problem, the next step on the welcome and
Import pages, and the dashboard's words)."""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.ops import cli
from ap_coder.web.app import create_app

from .conftest import ROOT

# --- B-240: the AP Coder window fills with technical lines once a scan is read -----------------------------------


def test_the_window_shows_warnings_only_even_after_the_ocr_engine_turns_the_root_logger_to_info():
    """Importing rapidocr (the OCR engine) sets the root logger to INFO: from then on the black window printed
    "2026-10-20 03:31:57,765 INFO ap_coder.ai.pipeline: local model could not suggest accounts ..." for every
    invoice, and "AI reviewer: invoice 1 postponed" every minute."""
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    root.handlers = []
    try:
        shown = io.StringIO()
        cli.configure_logging(verbose=False, stream=shown)
        root.setLevel(logging.INFO)  # what `import rapidocr` does
        logging.getLogger("ap_coder.ai.pipeline").info("local model could not suggest accounts")
        logging.getLogger("ap_coder.ai.page_worker").warning("page reader: invoice 3 could not be read")
        assert "could not suggest" not in shown.getvalue()
        assert "invoice 3 could not be read" in shown.getvalue()  # a real problem is still said

        root.handlers = []
        verbose = io.StringIO()
        cli.configure_logging(verbose=True, stream=verbose)  # -v: everything, as before
        logging.getLogger("ap_coder.ai.pipeline").info("local model could not suggest accounts")
        assert "could not suggest" in verbose.getvalue()
    finally:
        root.handlers, level = saved
        root.setLevel(level)


# --- B-241: a page that fails shows a bare "Internal Server Error" ------------------------------------------------


def _app_that_fails(tmp_path):
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), Store(tmp_path / "private" / "ap_coder.db"))

    @app.get("/boom")
    def boom():
        raise RuntimeError("database is locked")

    @app.post("/boom")
    def boom_post():
        raise RuntimeError("database is locked")

    return TestClient(app, base_url="http://localhost", raise_server_exceptions=False)


def test_a_page_that_fails_says_what_to_do_in_the_dashboard_with_the_details_folded_away(tmp_path, caplog):
    """An error no page expected answered with the bare text "Internal Server Error": no menu, no way back, no word
    on what to do."""
    from ap_coder.web.errors import error_log_path

    client = _app_that_fails(tmp_path)
    with caplog.at_level(logging.ERROR):
        page = client.get("/boom", headers={"accept": "text/html"})
    assert page.status_code == 500 and page.headers["content-type"].startswith("text/html")
    text = page.text
    assert "Something went wrong" in text and "What to do" in text
    assert 'href="/review"' in text and 'class="nav' in text  # the menu is there: the tester can carry on
    assert "Report a problem" in text
    assert "<details" in text and "RuntimeError: database is locked" in text  # for IT, folded away
    assert "Traceback" not in text
    window = [r.getMessage() for r in caplog.records]
    assert any("A page failed (GET /boom): RuntimeError: database is locked" in m for m in window)  # one line
    assert "Traceback" in error_log_path().read_text(encoding="utf-8")  # the full details, for IT
    assert str(error_log_path()) in text

    answer = client.post("/boom", headers={"X-AP-Coder": "1", "accept": "application/json"})
    assert answer.status_code == 500
    assert answer.json()["ok"] is False and "try again" in answer.json()["error"].lower()


def test_a_page_that_fails_still_answers_when_the_page_frame_itself_fails(tmp_path, monkeypatch):
    """The error page is drawn with the menu, which reads the database: when that is what failed, a plain page."""
    from ap_coder.web import core

    client = _app_that_fails(tmp_path)
    monkeypatch.setattr(core, "rail", lambda store: (_ for _ in ()).throw(RuntimeError("disk I/O error")))
    page = client.get("/boom", headers={"accept": "text/html"})
    assert page.status_code == 500 and "Something went wrong" in page.text and "href='/review'" in page.text
    assert "What to do:" in page.text and "Report a problem" in page.text


# --- The launcher (scripts/launch.py) ---------------------------------------------------------------------------


@pytest.fixture
def launch(tmp_path, monkeypatch):
    """scripts/launch.py, as an offline bundle's copy in its own folder (as tests/test_launch_update.py loads it)."""
    spec = importlib.util.spec_from_file_location("ap_script_launch_sweep4", ROOT / "scripts" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    here = tmp_path / "AP Coder 0.2"
    (here / "ap_coder").mkdir(parents=True)
    (here / "scripts").mkdir()
    (here / "pyproject.toml").write_text('[project]\nname = "ap-coder"\nversion = "0.2.0"\n')
    (here / "bundle_manifest.json").write_text(json.dumps({"version": "0.2.0", "built": "2026-10-11T09:00:00"}))
    venv = here / ".venv"
    venv.mkdir()
    monkeypatch.setattr(module, "ROOT", here)
    monkeypatch.setattr(module, "VENV", venv)
    monkeypatch.setattr(module, "VENV_PY", venv / "bin" / "python")
    monkeypatch.setattr(module, "STATE", venv / "ap_coder_launcher.json")
    monkeypatch.setattr(module, "WINDOWS", False)
    monkeypatch.setattr(module, "MAC", False)
    return module


# --- B-242: a second copy that can't set itself up stops the AP Coder that works --------------------------------


def test_a_copy_that_cannot_set_itself_up_leaves_the_running_ap_coder_alone(launch, monkeypatch, capsys):
    """A second folder (GitHub's ZIP, or a bundle unzipped without its wheelhouse) double-clicked while AP Coder ran:
    it stopped the running AP Coder ("stopping it, so this version takes over"), then said "Missing packages" and
    stopped too, leaving the tester with no AP Coder at all."""
    core = ["fastapi>=0.110", "pandas>=2.0"]
    monkeypatch.setattr(launch, "pip_source", lambda: None)  # no wheelhouse, and the internet is not allowed
    monkeypatch.setattr(launch.check_deps, "requirements", lambda extras=(): list(core))
    monkeypatch.setattr(launch.check_deps, "problem", lambda requirement: "not installed")
    stopped: list[int] = []
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: stopped.append(1) or ("clear", None))
    monkeypatch.setattr(launch.running, "find", lambda ports, data: ["the AP Coder on port 8501"])
    assert launch.run(*launch.parse([])) == 1
    out = capsys.readouterr().out
    assert not stopped, "the running AP Coder was stopped by a copy that could not start"
    assert "Missing packages: fastapi, pandas" in out and "What to do:" in out
    assert "The AP Coder already open was left running" in out

    monkeypatch.setattr(launch.running, "find", lambda ports, data: [])  # nothing open: nothing said about one
    assert launch.run(*launch.parse([])) == 1 and "left running" not in capsys.readouterr().out


def test_a_copy_that_can_set_itself_up_still_takes_over(launch, monkeypatch):
    monkeypatch.setattr(launch, "pip_source", lambda: ["--no-index", "--find-links", "wheelhouse"])
    monkeypatch.setattr(launch.check_deps, "problem", lambda requirement: "not installed")
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("blocked", 8501))  # (stops the start here)
    assert launch.cannot_set_up() is None
    assert launch.run(*launch.parse([])) == 1  # it went on to take over
