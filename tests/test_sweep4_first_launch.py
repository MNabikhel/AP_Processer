"""Sweep 4, first launch: what a pilot tester meets on a fresh laptop in the first hour (the black window, a page
that fails, a second copy started by mistake, the Quick start, reporting a problem, the next step on the welcome and
Import pages, and the dashboard's words)."""

from __future__ import annotations

import io
import logging
from dataclasses import replace

from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.ops import cli
from ap_coder.web.app import create_app

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
