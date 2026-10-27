"""Telling builds apart, and what the launcher says while it works (scripts/launch.py, ap_coder/core/paths.py): GitHub's
ZIP carries a build stamp (ap_coder/BUILD, filled in by git archive), so a newer ZIP unzipped over an older one of the
same version is an update; the first setup says what it is doing during its silent minutes; a window whose dashboard a
newer AP Coder stopped says so."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ap_coder.core import paths
from tests.scripts import load_script

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:  # (running.py, fetch_models.py: imported the way the launcher does)
    sys.path.insert(0, str(ROOT / "scripts"))
STAMP_A = "1a2b3c4 2026-10-20T16:14:00-04:00\n"  # as git archive writes it (%h %cI)
STAMP_B = "5e6f7a8 2026-10-21T09:30:00-04:00\n"


def _code(folder: Path, version: str = "0.3.0", stamp: str | None = STAMP_A) -> Path:
    (folder / "ap_coder").mkdir(parents=True, exist_ok=True)
    (folder / "scripts").mkdir(exist_ok=True)
    (folder / "scripts" / "launch.py").write_text("")
    (folder / "pyproject.toml").write_text(f'[project]\nname = "ap-coder"\nversion = "{version}"\n')
    if stamp is not None:
        (folder / "ap_coder" / "BUILD").write_text(stamp)
    return folder


@pytest.fixture
def launch(tmp_path, monkeypatch):
    module = load_script("launch", "ap_script_launch_build")
    here = _code(tmp_path / "AP Coder")
    (here / "APProcessor.bat").write_text("")
    (here / "APProcessor.command").write_text("")
    venv = here / ".venv"
    venv.mkdir()
    monkeypatch.setattr(module, "ROOT", here)
    monkeypatch.setattr(module, "VENV", venv)
    monkeypatch.setattr(module, "VENV_PY", venv / "bin" / "python")
    monkeypatch.setattr(module, "STATE", venv / "ap_coder_launcher.json")
    monkeypatch.setattr(module, "WINDOWS", False)
    monkeypatch.setattr(module, "MAC", False)
    monkeypatch.setattr(module, "ensure_second_ocr", lambda state: "")
    return module


# --- The build stamp --------------------------------------------------------------------------------------------------


def test_the_build_stamp_is_filled_in_by_git_archive():
    """GitHub's Download ZIP is a git archive: .gitattributes marks ap_coder/BUILD export-subst, and the file holds the
    placeholder git fills in (the commit and its time)."""
    assert (ROOT / "ap_coder" / "BUILD").read_text(encoding="utf-8").strip() == "$Format:%h %cI$"
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "ap_coder/BUILD export-subst" in attributes
    package_data = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"BUILD"' in package_data  # installed with the package too (pip install, not only the code folder)


def test_a_stamped_zip_says_its_commit_and_time(tmp_path):
    folder = _code(tmp_path / "zip")
    stamp = paths.build_stamp(folder)
    assert stamp == {"commit": "1a2b3c4", "time": "2026-10-20T20:14:00", "source": "stamp"}  # (in UTC)
    ident = paths.install_identity(folder)
    assert ident["commit"] == "1a2b3c4" and ident["build"] == "2026-10-20T20:14:00"
    assert paths.describe(ident) == "0.3.0 (build 1a2b3c4, 2026-10-20)"
    assert paths.build_label(ident) == "build 1a2b3c4, 2026-10-20 20:14"


def test_the_placeholder_or_no_stamp_falls_back_to_a_hash_of_the_code(tmp_path):
    """A folder copied by hand (no stamp filled in, no git): its identity still changes whenever its code does."""
    folder = _code(tmp_path / "copy", stamp="$Format:%h %cI$\n")
    (folder / "ap_coder" / "x.py").write_text("one = 1\n")
    first = paths.install_identity(folder)
    assert paths.build_stamp(folder)["source"] == "code" and first["commit"] == "" and first["build"] == ""
    (folder / "ap_coder" / "x.py").write_text("one = 2\n")
    second = paths.install_identity(folder)
    assert second["fingerprint"] != first["fingerprint"] and second["code"] != first["code"]
    assert paths.describe(second) == "0.3.0"  # (a hash of the code is not shown as a build)


def test_a_checkout_says_its_head():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout (an offline bundle or GitHub's ZIP)")
    stamp = paths.build_stamp(ROOT)
    head = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%h"], capture_output=True, text=True)
    assert stamp["source"] == "git" and stamp["commit"] == head.stdout.strip()
    assert paths.install_identity(ROOT)["build"] == ""  # a checkout is never older than an install


def test_two_zips_of_one_version_are_two_builds(tmp_path):
    """H1: every version was 0.2.0 and a ZIP carried no build stamp: a newer ZIP looked the same as the older one."""
    old, new = _code(tmp_path / "old"), _code(tmp_path / "new", stamp=STAMP_B)
    a, b = paths.install_identity(old), paths.install_identity(new)
    assert a["version"] == b["version"] and a["fingerprint"] != b["fingerprint"]
    assert paths.is_older(a, b) and not paths.is_older(b, a)


def test_the_bundle_builder_stamps_its_copy(tmp_path, monkeypatch):
    """The offline bundle copies the code file by file (no git archive): it writes the stamp itself."""
    builder = ROOT / "scripts" / "build_offline_bundle.py"
    spec = importlib.util.spec_from_file_location("ap_script_bundle_build", builder)
    bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundle)
    real = paths.build_stamp
    checkout = {"commit": "9f8e7d6", "time": "2026-10-21T13:30:00", "source": "git"}
    monkeypatch.setattr(paths, "build_stamp", lambda folder: checkout)
    (tmp_path / "ap_coder").mkdir()
    assert bundle.stamp_build(tmp_path) == "9f8e7d6 2026-10-21T13:30:00Z"
    assert real(tmp_path) == {"commit": "9f8e7d6", "time": "2026-10-21T13:30:00", "source": "stamp"}


# --- An update of the same version is said, and checked -------------------------------------------------------------


def _quiet(launch, monkeypatch):
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("clear", None))
    monkeypatch.setattr(launch, "ensure_packages", lambda state: ("ok", "Packages: all 15 in place", False))
    monkeypatch.setattr(launch, "ensure_models", lambda first, state: None)
    monkeypatch.setattr(launch, "ensure_shortcut", lambda: "")
    monkeypatch.setattr(launch, "readiness", lambda packages, state=None, force=False: [packages])
    started: list[list[str]] = []
    monkeypatch.setattr(launch, "start_dashboard", lambda rest, state: started.append(rest) or 0)
    checks: list[int] = []
    monkeypatch.setattr(launch, "self_check", lambda: checks.append(1) or (True, "Self-check OK: 10 of 10"))
    return checks


def test_a_newer_zip_unzipped_over_the_older_one_is_an_update(launch, monkeypatch, capsys):
    """H1: unzipped over the old folder, the newer ZIP said nothing ("Self-check OK at setup", from the older one) and
    ran no self-check: both were 0.2.0 with the same pyproject.toml."""
    checks = _quiet(launch, monkeypatch)
    assert launch.run(*launch.parse(["--venv=reused"])) == 0 and checks == [1]  # the first start
    capsys.readouterr()
    assert launch.run(*launch.parse(["--venv=reused"])) == 0 and checks == [1]  # a later start: nothing new
    assert "Updated" not in capsys.readouterr().out
    (launch.ROOT / "ap_coder" / "BUILD").write_text(STAMP_B)  # the newer ZIP, unzipped over it
    assert launch.run(*launch.parse(["--venv=reused"])) == 0
    out = capsys.readouterr().out
    assert "Updated AP Coder from 0.3.0 (build 1a2b3c4, 2026-10-20) to 0.3.0 (build 5e6f7a8, 2026-10-21)." in out
    assert checks == [1, 1]  # and the self-check ran again
    assert launch.load_state()["fingerprint"] == launch.fingerprint()


def test_a_dashboard_of_this_folder_from_before_its_code_changed_is_older(tmp_path):
    """Unzipped over while it ran: the dashboard runs the older build (it says which on /healthz), so it is stopped
    and the newer one starts, instead of the newer start just opening the older one."""
    import running

    folder = _code(tmp_path / "AP Coder")
    server = running.Server(8501, "0.3.0", str(folder), build="1a2b3c4")
    assert running.judge(server, folder, "0.3.0", "1a2b3c4") == "same"
    assert running.judge(server, folder, "0.3.0", "5e6f7a8") == "older"
    assert running.judge(running.Server(8501, "0.3.0", str(folder)), folder, "0.3.0", "5e6f7a8") == "same"  # (0.2)
    assert "(build 1a2b3c4)" in server.name()


def test_the_dashboard_says_its_build(tmp_path):
    from fastapi.testclient import TestClient

    from ap_coder.core.store import Store
    from ap_coder.web.app import CODE_FOLDER, create_app

    with TestClient(create_app(store=Store(tmp_path / "ap.db")), base_url="http://localhost") as client:
        found = client.get("/healthz").json()
    assert found["build"] == paths.build_stamp(CODE_FOLDER)["commit"] and found["build"]


def test_settings_about_and_the_doctor_say_the_build(monkeypatch):
    from ap_coder.ops import doctor
    from ap_coder.web.pages import settings

    monkeypatch.setattr(paths, "install_identity", lambda folder=None: {"version": "0.3.0", "commit": "1a2b3c4",
                                                                        "build": "2026-10-20T20:14:00"})  # fmt: skip
    assert settings.build_version() == "build 1a2b3c4, 2026-10-20 20:14"
    assert doctor.ap_coder_build().endswith(", build 1a2b3c4, 2026-10-20 20:14")


# --- A window whose dashboard a newer AP Coder stopped ----------------------------------------------------------------


def test_the_old_window_says_a_newer_ap_coder_took_over(launch, monkeypatch, capsys):
    """L5: when a newer version took over, the old window just ended (or said AP Coder stopped on its own)."""
    script = "import time; time.sleep(1.0)"
    monkeypatch.setattr(launch, "server_command", lambda port, rest: [sys.executable, "-c", script])
    monkeypatch.setattr(launch.running, "health", lambda port, timeout=1.0: None)
    monkeypatch.setattr(launch, "open_browser", lambda url: None)

    def stopped_by_the_newer_one(*args):
        launch.running.note_takeover(8597, Path("elsewhere"))  # what take_over writes just before it stops it
        return real_poll(*args)

    real_poll = subprocess.Popen.poll
    monkeypatch.setattr(subprocess.Popen, "poll", stopped_by_the_newer_one)
    assert launch.start_dashboard(["--port", "8597"], {}) == launch.TOOK_OVER_CODE
    assert "A newer AP Coder took over; you can close this window." in capsys.readouterr().out
    monkeypatch.setattr(subprocess.Popen, "poll", real_poll)
    assert launch.start_dashboard(["--port", "8597"], {}) == 0  # its own start cleared the note: a plain stop
    assert "took over" not in capsys.readouterr().out


def test_an_old_note_or_another_port_is_not_this_window(monkeypatch):
    import running

    running.note_takeover(8501, Path("x"))
    assert running.taken_over(8501) and not running.taken_over(8502)
    settings = paths.read_user_settings()
    settings["taken_over"]["at"] = time.time() - 3600  # an hour ago
    paths.write_user_settings(**settings)
    assert not running.taken_over(8501)


def test_the_window_frees_the_lock_before_it_waits(launch, monkeypatch):
    """The first phase holds the lock: it is freed before the window waits for Enter (the newer start waits for it)."""
    freed: list[int] = []
    monkeypatch.setattr(launch, "_main", lambda argv: launch.TOOK_OVER_CODE)
    monkeypatch.setattr(launch, "release_lock", lambda: freed.append(1))
    monkeypatch.setenv(launch.LOCK_HELD_ENV, str(launch.os.getpid()))
    monkeypatch.setenv("AP_NO_PAUSE", "1")
    assert launch.main([]) == 0 and freed == [1]
    monkeypatch.setenv(launch.LOCK_HELD_ENV, "1")  # the second phase: its parent holds the lock
    assert launch.main([]) == launch.TOOK_OVER_CODE


# --- The silent minutes of a first setup ------------------------------------------------------------------------------


PIP_LINES = """Obtaining file:///C:/Users/pat/Downloads/AP_Processer-main
Collecting openpyxl>=3.1 (from ap-coder==0.3.0)
  Downloading openpyxl-3.1.5-py2.py3-none-any.whl.metadata (2.5 kB)
Collecting pandas>=2.1 (from ap-coder==0.3.0)
  Downloading pandas-3.0.6-cp312-cp312-win_amd64.whl.metadata (79 kB)
Collecting python-dotenv>=1.0 (from ap-coder==0.3.0)
  Using cached python_dotenv-1.2.4-py3-none-any.whl.metadata (29 kB)
Downloading openpyxl-3.1.5-py2.py3-none-any.whl (250 kB)
Downloading pandas-3.0.6-cp312-cp312-win_amd64.whl (11.2 MB)""".splitlines()


def test_pip_progress_counts_what_it_found_fetched_and_installed(launch, tmp_path):
    """H2: pip installed 56 packages in silence for over two minutes; its lines (one a package) stay in the log, and
    the window says how far it is."""
    site = tmp_path / "site-packages"
    site.mkdir()
    (site / "pip-25.0.dist-info").mkdir()
    progress = launch.PipProgress(site)
    assert progress.status() == "    Starting pip..."
    for text in PIP_LINES[:6]:
        progress.feed(text)
    assert progress.status() == "    Working out which packages are needed: 3 so far (python-dotenv)..."
    for text in PIP_LINES[6:]:
        progress.feed(text)
    assert progress.status() == "    Getting packages: 2 of 3 (pandas)..."
    progress.feed("Installing collected packages: python-dotenv, openpyxl, pandas, ap-coder")
    assert progress.status() == "    Installing packages: 0 of 4 (python-dotenv)..."
    (site / "python_dotenv-1.2.4.dist-info").mkdir()
    (site / "openpyxl-3.1.5.dist-info").mkdir()
    assert progress.status() == "    Installing packages: 2 of 4 (pandas)..."


def test_pip_progress_from_the_wheelhouse(launch):
    progress = launch.PipProgress(None)
    progress.feed("Processing ./wheelhouse/pandas-3.0.6-cp312-cp312-win_amd64.whl")
    progress.feed("Processing c:\\apcoder\\wheelhouse\\numpy-2.3.0-cp312-cp312-win_amd64.whl")
    assert progress.status() == "    Getting packages: 2 of 2 (numpy)..."


def test_pip_says_how_far_it_is_while_it_is_quiet(launch, monkeypatch, capsys):
    """A pip that prints only progress lines (kept for the log) gets a line in the window every HEARTBEAT seconds."""
    monkeypatch.setattr(launch, "HEARTBEAT", 0.3)
    monkeypatch.setattr(launch, "pip_source", lambda: [])
    lines = "\n".join(PIP_LINES)
    script = f"import sys, time\nfor line in {lines!r}.splitlines():\n    print(line, flush=True)\n    time.sleep(0.2)"
    real_popen = subprocess.Popen
    monkeypatch.setattr(launch.subprocess, "Popen", lambda cmd, **kw: real_popen([sys.executable, "-c", script], **kw))
    assert launch.pip_install(["x"])
    out = capsys.readouterr().out
    assert "Getting packages:" in out or "Working out which packages are needed:" in out
    assert "Downloading pandas" not in out  # pip's own lines: in the log only
    assert "Downloading pandas" in launch.log_path().read_text(encoding="utf-8")


def test_making_the_venv_is_said(launch, monkeypatch, capsys):
    """H2: making .venv took 30 s with nothing on screen."""
    monkeypatch.setattr(launch, "venv_works", lambda: False)
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    calls = iter([False, True])
    monkeypatch.setattr(launch, "venv_works", lambda: next(calls))
    launch.VENV.rmdir()
    assert launch.ensure_venv("python3") == "created"
    assert "Making AP Coder's own Python environment" in capsys.readouterr().out


# --- Wording --------------------------------------------------------------------------------------------------------


def test_lm_studio_not_running_says_how_to_start_it_and_to_check_again(launch):
    """The owner runs LM Studio for the pilot: the dashboard picks it up with Settings → Reading → Check again, so the
    line no longer says to start AP Coder again."""
    from types import SimpleNamespace

    level, text = launch.lm_studio_line(SimpleNamespace(reachable=False))
    assert level == "warn" and text.startswith("LM Studio isn't running")  # (CI looks for this start)
    assert "Developer tab → Start server" in text and "Check again" in text and "Settings → Reading" in text
    assert "start AP Coder again" not in text


def test_the_ocr_line_says_where_the_models_come_from(launch, monkeypatch, tmp_path):
    import fetch_models

    monkeypatch.setattr(fetch_models, "BUNDLE_MODELS", tmp_path / "models")
    monkeypatch.setattr(launch, "setup_online", lambda: True)  # GitHub's ZIP
    assert "couldn't be downloaded yet" in launch.one_engine(True) and "bundle" not in launch.one_engine(True)
    (tmp_path / "models").mkdir()  # an offline bundle
    assert "offline bundle's models folder" in launch.one_engine(True)


# --- Python 3.11 ----------------------------------------------------------------------------------------------------


def test_one_python_version_everywhere():
    """L6: pyproject said 3.10 while the launcher, the doctor and the guides said 3.11, and CI tested 3.10."""
    import tomllib

    import yaml

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.11"
    assert project["tool"]["ruff"]["target-version"] == "py311"
    ci = ROOT / ".github" / "workflows" / "ci.yml"
    if ci.is_file():
        tested = [
            cell["python"]
            for cell in yaml.safe_load(ci.read_text(encoding="utf-8"))["jobs"]["tests"]["strategy"]["matrix"]["include"]
        ]
        assert min(tested, key=lambda v: tuple(map(int, v.split(".")))) == "3.11"
    spec = importlib.util.spec_from_file_location("ap_script_launch_py", ROOT / "scripts" / "launch.py")
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)
    assert launch.MINIMUM == (3, 11)


def test_a_venv_made_with_an_older_python_is_made_again(launch, monkeypatch, capsys):
    """A .venv from AP Coder 0.1 (Python 3.10 was allowed then) can't install 0.3: it is made again with this Python."""
    (launch.VENV / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.10.12\n")
    launch.VENV_PY.parent.mkdir(parents=True)
    launch.VENV_PY.write_text("")
    monkeypatch.setattr(launch, "fitting_pythons", lambda: [])
    made: list[bool] = []
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("clear", None))
    monkeypatch.setattr(launch, "ensure_venv", lambda rebuild=False: made.append(rebuild) or "recreated")
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd: 0)
    assert launch.bootstrap([]) == 0 and made == [True]
    assert ".venv was made with Python 3.10, older than AP Coder needs" in capsys.readouterr().out


def test_the_dry_run_names_every_address_the_build_downloads_from(capsys, monkeypatch):
    """For IT's firewall: pip's hosts and each model's address, on stderr (stdout stays the JSON plan)."""
    import build_offline_bundle

    monkeypatch.setenv("AP_ALLOW_INTERNET", "0")  # (the builder opts in for itself: put back after the test)
    assert build_offline_bundle.main(["--dry-run", "--python", "3.12"]) == 0
    out = capsys.readouterr()
    plan = json.loads(out.out)
    assert plan["download_hosts"][:2] == ["pypi.org", "files.pythonhosted.org"]
    assert "www.modelscope.cn" in plan["download_hosts"]
    assert all(m["url"].startswith("https://") for m in plan["models"])
    assert "pypi.org, files.pythonhosted.org, www.modelscope.cn" in out.err and "LM Studio" in out.err


def test_a_quiet_step_after_pip_says_it_is_still_working(launch, capsys):
    """H2: the steps after pip (the models, the checks, the self-check) can each be quiet for a while too."""
    step = launch.Step("getting the OCR models")
    with launch.heartbeat(step, every=0.2):
        time.sleep(0.7)
        launch.say("Downloading ch_PP-OCRv5_mobile_det.onnx ...")
    out = capsys.readouterr().out
    assert "  Still working: getting the OCR models..." in out
    assert launch.Step().status() == ""  # nothing named: nothing said
    with launch.heartbeat(step, every=0.2):  # the same line again and again: less and less often
        time.sleep(1.7)
    assert 2 <= capsys.readouterr().out.count("Still working") <= 4  # (every 0.2 s: 8)


# --- Review: never clean up a folder that may be newer; Python 3.10; wording ------------------------------------------


def _bundle(folder: Path, stamp: str | None, built: str) -> Path:
    _code(folder, stamp=stamp)
    (folder / "bundle_manifest.json").write_text(json.dumps({"version": "0.3.0", "built": built}))
    return folder


def test_a_newer_bundle_is_never_taken_for_an_older_one_across_time_zones(tmp_path):
    """B-551: a bundle's `built` is IT's local time with no zone, a ZIP's build its commit's time in UTC; compared as
    text, a bundle built in Canada after the ZIP looked older, and the ZIP's clean-up could delete it."""
    zip_x = _code(tmp_path / "zip X", stamp="1a2b3c4 2026-10-21T20:00:00+00:00\n")  # 16:00 in Toronto
    bundle_y = _bundle(tmp_path / "bundle Y", "5e6f7a8 2026-10-21T21:00:00Z\n", "2026-10-21T17:30:00")  # 17:30 there
    x, y = paths.install_identity(zip_x), paths.install_identity(bundle_y)
    assert paths.compare(x, y) == "older" and paths.compare(y, x) == "newer"
    assert paths.is_older(x, y) and not paths.is_older(y, x)


def test_a_build_that_cant_be_ordered_is_never_cleaned_up(launch, tmp_path):
    """Never delete when in doubt: a same-version folder whose build can't be ordered with this one (a bundle from
    before the build stamp, a hand-copied folder) is neither handed over to nor cleaned up."""
    old_bundle = _bundle(tmp_path / "0.3 bundle without a stamp", None, "2026-10-21T09:00:00")
    copied = _code(tmp_path / "copied by hand", stamp="$Format:%h %cI$\n")
    older = _code(tmp_path / "older ZIP", stamp="0a0a0a0 2026-10-01T10:00:00+00:00\n")
    mine = launch.identity()
    for folder in (old_bundle, copied):
        assert paths.compare(paths.install_identity(folder), mine) == "unknown"
        paths.record_install(paths.install_identity(folder), current=False)
    paths.record_install(paths.install_identity(older), current=False)
    assert launch.older_installs(mine, []) == [older]
    data = tmp_path / "data"
    data.mkdir()
    launch.upgrade.clean_up(launch.ROOT, data, [old_bundle, copied], print)  # (even when handed them)
    assert (old_bundle / "ap_coder").is_dir() and (copied / "ap_coder").is_dir()


def test_build_times_need_no_python_3_11(monkeypatch):
    """B-552: datetime.UTC is new in 3.11: `python scripts/launch.py` under 3.10 stopped with AttributeError before it
    could say that Python is too old."""
    import datetime

    monkeypatch.delattr(datetime, "UTC", raising=False)
    assert paths._utc("2026-10-21T16:14:00-04:00") == "2026-10-21T20:14:00"


def test_an_older_build_unzipped_over_a_newer_one_is_said_as_such(launch, monkeypatch, capsys):
    """B-553: "Updated AP Coder from <newer> to <older>"; and "Updated AP Coder from 0.3.0 to 0.3.0" for a folder whose
    files changed without a build stamp."""
    _quiet(launch, monkeypatch)
    (launch.ROOT / "ap_coder" / "BUILD").write_text(STAMP_B)
    assert launch.run(*launch.parse(["--venv=reused"])) == 0
    capsys.readouterr()
    (launch.ROOT / "ap_coder" / "BUILD").write_text(STAMP_A)  # the older ZIP, over the newer one
    assert launch.run(*launch.parse(["--venv=reused"])) == 0
    out = capsys.readouterr().out
    assert ("Replaced AP Coder 0.3.0 (build 5e6f7a8, 2026-10-21) with an older build, 0.3.0 (build 1a2b3c4, "
            "2026-10-20)") in out  # fmt: skip
    assert "Updated AP Coder" not in out
    (launch.ROOT / "ap_coder" / "BUILD").write_text("$Format:%h %cI$\n")  # copied by hand: no build to name
    assert launch.run(*launch.parse(["--venv=reused"])) == 0
    capsys.readouterr()
    (launch.ROOT / "ap_coder" / "x.py").write_text("changed = True\n")
    assert launch.run(*launch.parse(["--venv=reused"])) == 0
    out = capsys.readouterr().out
    assert "AP Coder's files in this folder changed since it was last set up" in out and "Updated" not in out


def test_pip_progress_never_sticks_on_a_name_it_cant_match(launch, tmp_path):
    """B-555: a package whose .dist-info never matched its name in pip's list kept the line on that name."""
    site = tmp_path / "site"
    site.mkdir()
    progress = launch.PipProgress(site)
    progress.feed("Installing collected packages: alpha, Odd.Name, beta, gamma")
    (site / "alpha-1.0.dist-info").mkdir()
    (site / "beta-1.0.dist-info").mkdir()
    assert progress.status() == "    Installing packages: 2 of 4 (gamma)..."


def test_an_older_venv_is_made_again_only_after_old_dashboards_stopped(launch, monkeypatch):
    """B-556: the 3.10 .venv of a running old AP Coder window can't be deleted (Windows: files in use): the old
    dashboards are stopped first."""
    (launch.VENV / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.10.12\n")
    launch.VENV_PY.parent.mkdir(parents=True)
    launch.VENV_PY.write_text("")
    monkeypatch.setattr(launch, "fitting_pythons", lambda: [])
    order: list[str] = []
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: order.append("take_over") or ("clear", None))
    monkeypatch.setattr(launch, "ensure_venv", lambda rebuild=False: order.append("venv") or "recreated")
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd: 0)
    assert launch.bootstrap([]) == 0 and order == ["take_over", "venv"]
