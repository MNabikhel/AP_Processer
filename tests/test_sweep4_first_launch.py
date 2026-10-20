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
from types import SimpleNamespace

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


# --- What the launcher's window says, read by someone who is not a developer -------------------------------------


def _readiness(launch, monkeypatch, reachable: bool):
    status = SimpleNamespace(reachable=reachable, active=False, server="LM Studio")
    monkeypatch.setattr(launch, "chat_status", lambda: status)
    monkeypatch.setattr(launch, "page_reader_line", lambda: ("warn", "Page reader: OvisOCR2 isn't running"))
    monkeypatch.setattr(launch, "setup_check_lines", lambda: [("ok", "Setup checks: -")])
    return launch.readiness(("ok", "Packages: all 15 in place"), None)


def test_the_readiness_summary_has_no_developer_words_and_one_lm_studio_line_with_what_to_do(launch, monkeypatch):
    """It said "Python 3.12.3 (.venv)", then "· LM Studio: not running (optional …)" right above "! Page reader:
    OvisOCR2 isn't running in LM Studio …": optional or not, two lines on one thing, and neither said how to start
    it."""
    lines = _readiness(launch, monkeypatch, reachable=False)
    texts = [t for _, t in lines]
    assert texts[0] == f"Python {sys.version.split()[0]}" and not any(".venv" in t for t in texts)
    studio = [(level, t) for level, t in lines if "LM Studio" in t]
    assert len(studio) == 1, studio
    level, text = studio[0]
    assert level == "warn" and "isn't running" in text and "Developer tab → Start server" in text
    assert "optional" not in text

    texts = [t for _, t in _readiness(launch, monkeypatch, reachable=True)]  # LM Studio runs: the reader's own line
    assert "Page reader: OvisOCR2 isn't running" in texts


def test_a_later_start_names_no_command_line_option(launch, monkeypatch, capsys):
    """It said "Self-check OK at setup (2026-10-20 03:29); --check runs it again": an option only a command line
    can give."""
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("clear", None))
    monkeypatch.setattr(launch, "cannot_set_up", lambda: None)
    monkeypatch.setattr(launch, "ensure_packages", lambda state: ("ok", "Packages: all 15 in place", False))
    monkeypatch.setattr(launch, "ensure_data_folder", lambda: launch.VENV.parent / "data")
    monkeypatch.setattr(launch, "ensure_models", lambda first, state: None)
    monkeypatch.setattr(launch, "ensure_shortcut", lambda: "")
    monkeypatch.setattr(launch, "readiness", lambda packages, state, force=False: [packages])
    monkeypatch.setattr(launch, "start_dashboard", lambda rest, state: 0)
    monkeypatch.setattr(launch, "self_check", lambda: (True, "Self-check OK: 10 of 10"))
    assert launch.run(*launch.parse(["--venv=created"])) == 0
    capsys.readouterr()
    assert launch.run(*launch.parse([])) == 0
    out = capsys.readouterr().out
    assert "Self-check OK at setup" in out and "--check" not in out and "reusing .venv" not in out


def test_a_failed_self_check_keeps_its_details_in_the_log_instead_of_asking_for_a_command(launch, monkeypatch,
                                                                                           capsys):  # fmt: skip
    """It said "For the details run  python scripts/pilot_check.py  in scripts\\terminal.bat, and send its output to
    whoever set up the pilot": a command line for the tester, though the self-check had just printed those details."""
    from ap_coder.core import paths

    bad = "FAIL a.pdf\n       grand_total: 1 (expected 2)\n\n9 of 10 sample invoices read right.\n"
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=bad, stderr=""))
    assert launch.self_check() == (False, "Self-check FAILED: FAIL a.pdf")
    logged = (paths.user_settings_path().parent / "logs" / "launch.log").read_text(encoding="utf-8")
    assert "grand_total: 1 (expected 2)" in logged  # the whole output, for IT

    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("clear", None))
    monkeypatch.setattr(launch, "cannot_set_up", lambda: None)
    monkeypatch.setattr(launch, "ensure_packages", lambda state: ("ok", "Packages: all 15 in place", False))
    monkeypatch.setattr(launch, "ensure_data_folder", lambda: launch.VENV.parent / "data")
    monkeypatch.setattr(launch, "ensure_models", lambda first, state: None)
    monkeypatch.setattr(launch, "ensure_shortcut", lambda: "")
    monkeypatch.setattr(launch, "readiness", lambda packages, state, force=False: [packages])
    monkeypatch.setattr(launch, "start_dashboard", lambda rest, state: 0)
    capsys.readouterr()
    assert launch.run(*launch.parse(["--venv=created"])) == 0
    out = capsys.readouterr().out
    assert "AP Coder still starts" in out and "python " not in out and "terminal.bat" not in out
    assert "Report a problem" in out and "launch.log" in out


def test_installing_shows_progress_in_plain_words_and_keeps_pips_lines_in_the_log(launch, monkeypatch, capsys):
    """The first start printed pip's own lines, one per package: "Looking in links: …", "Processing ./wheelhouse/
    pandas-3.0.6-cp312-…whl" and a "Successfully installed …" line of sixty names."""
    from ap_coder.core import paths

    (launch.ROOT / "wheelhouse").mkdir()
    (launch.ROOT / "wheelhouse" / "openpyxl-3.1.5-py2.py3-none-any.whl").write_bytes(b"")
    lines = ["Looking in links: wheelhouse\n",
             "Processing ./wheelhouse/pandas-3.0.6-cp312-cp312-manylinux_2_28_x86_64.whl\n",
             "Collecting openpyxl>=3.1\n",
             "Installing collected packages: et-xmlfile, openpyxl, pandas\n",
             "Successfully installed et-xmlfile-2.0.0 openpyxl-3.1.5 pandas-3.0.6\n",
             "WARNING: The script f2py is installed in '/x/.venv/bin' which is not on PATH.\n",
             "ERROR: Could not find a version that satisfies the requirement nope\n"]  # fmt: skip

    class Pip:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(lines)

        def wait(self):
            return 0

    monkeypatch.setattr(launch.subprocess, "Popen", Pip)
    assert launch.pip_install(["pandas"])
    out = capsys.readouterr().out
    assert "Processing" not in out and "Looking in links" not in out and "Collecting" not in out
    assert "et-xmlfile" not in out and "Installed 3 packages." in out
    assert "ERROR: Could not find a version" in out  # what went wrong is still said
    logged = (paths.user_settings_path().parent / "logs" / "launch.log").read_text(encoding="utf-8")
    assert "Processing ./wheelhouse/pandas" in logged and "Successfully installed et-xmlfile" in logged


def test_the_server_window_does_not_repeat_the_address_with_ctrl_c(launch, monkeypatch, capsys):
    """Under the launcher the window said "Starting AP Coder: … close it to stop.", then "Dashboard: http://127.0.0.1:
    8501  (Ctrl+C here to stop)", then "AP Coder is running at http://127.0.0.1:8501"."""
    output = [
        "Dashboard: http://127.0.0.1:8501  (Ctrl+C here to stop)\n",
        "AP Coder is running at http://127.0.0.1:8501\n",
    ]

    class Server:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(output)
            self.returncode = 0
            self._polls = 0

        def poll(self):
            self._polls += 1
            return None if self._polls < 3 else 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(launch.subprocess, "Popen", Server)
    monkeypatch.setattr(launch, "free_port", lambda: 8501)
    monkeypatch.setattr(launch, "open_browser", lambda url: None)
    monkeypatch.setattr(launch.running, "health", lambda port, timeout=1.0: False)
    launch.start_dashboard([], {})
    out = capsys.readouterr().out
    assert "Ctrl+C" not in out and "AP Coder is running at http://127.0.0.1:8501" in out


# --- The dashboard -------------------------------------------------------------------------------------------------


def _client(store):
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def fresh(tmp_path):
    """A new laptop: nothing imported, nothing processed."""
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture
def demo_db(tmp_path_factory):
    from ap_coder.ops.demo import load_demo

    path = tmp_path_factory.mktemp("demo") / "private" / "ap_coder.db"
    load_demo(Store(path), Settings())
    return path


# --- Reporting a problem, without a command line ---------------------------------------------------------------


def test_report_a_problem_gives_both_reports_to_copy_without_a_command_line(demo_db, tmp_path, monkeypatch):
    """Help (and the guides) asked the tester to open scripts\\terminal.bat and type `python -m ap_coder doctor` and
    `python -m ap_coder share-report`: the reports a problem report needs were behind a command line."""
    import shutil

    target = tmp_path / "private" / "ap_coder.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(demo_db, target)
    client = _client(Store(target))
    text = client.get("/help/report").text
    assert "Report a problem" in text and "Describe it in words" in text
    assert "AP Coder doctor report" in text  # the setup report, as `doctor` writes it
    assert "AP Coder run summary (redacted)" in text  # the share report, as `share-report` writes it
    assert "data-copy-from" in text and "python -m" not in text and "terminal.bat" not in text
    assert "dashboard-errors.log" in text and "launch.log" in text  # where the details of a failure are
    assert "-> " not in text.split("AP Coder run summary")[1].split("</textarea>")[0]  # no GL codes unless asked
    help_page = client.get("/help").text
    assert 'href="/help/report"' in help_page


def test_report_a_problem_on_a_new_laptop_says_there_is_nothing_to_summarise_yet(fresh):
    text = _client(fresh).get("/help/report").text
    assert "AP Coder doctor report" in text and "No invoices processed yet" in text
