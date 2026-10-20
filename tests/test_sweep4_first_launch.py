"""Sweep 4, first launch: what a pilot tester meets on a fresh laptop in the first hour (the black window, a page
that fails, a second copy started by mistake, the Quick start, reporting a problem, the next step on the welcome and
Import pages, and the dashboard's words)."""

from __future__ import annotations

import io
import logging

from ap_coder.ops import cli

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
