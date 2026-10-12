"""The one-button launcher when an older AP Coder is installed (scripts/launch.py): the newest version takes over
seamlessly. Its shortcut opens the newest, an older one still running is stopped (never opened), an update is said
and checked, the older install is cleaned up once the new one passed its self-check, a package folder that doesn't
fit this computer is said plainly, and the server is started with ``python -m ap_coder serve``."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ap_coder import paths
from ap_coder.store import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def launch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ap_script_launch_update", ROOT / "scripts" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    here = tmp_path / "AP Coder 0.2"
    (here / "ap_coder").mkdir(parents=True)
    (here / "scripts").mkdir()
    (here / "scripts" / "launch.py").write_text("")
    (here / "pyproject.toml").write_text('[project]\nname = "ap-coder"\nversion = "0.2.0"\n')
    (here / "bundle_manifest.json").write_text(json.dumps({"version": "0.2.0", "built": "2026-10-11T09:00:00"}))
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


# --- The shortcut always opens the current install --------------------------------------------------------------


def _windows_desktop(launch, monkeypatch, tmp_path, ok=True):
    desk = tmp_path / "Desktop"
    desk.mkdir(exist_ok=True)
    written: list[str] = []
    monkeypatch.setattr(launch, "WINDOWS", True)
    monkeypatch.setattr(launch, "desktop", lambda: desk)

    def write_shortcut(folder):
        written.append(str(launch.ROOT))
        if ok:
            (folder / "AP Coder.lnk").write_text(str(launch.ROOT / "APProcessor.bat"))
        return ok

    monkeypatch.setattr(launch, "write_shortcut", write_shortcut)
    return desk, written


def test_the_shortcut_is_moved_to_the_newer_install(launch, monkeypatch, tmp_path):
    """An older install made the shortcut: it kept opening that older folder after an update."""
    desk, written = _windows_desktop(launch, monkeypatch, tmp_path)
    old = tmp_path / "AP Coder 0.1"
    (desk / "AP Coder.lnk").write_text(str(old / "APProcessor.bat"))  # made by the older install
    paths.write_user_settings(desktop_shortcut=True, shortcut_target=str(old / "APProcessor.bat"))
    assert "now opens" in launch.ensure_shortcut() and written == [str(launch.ROOT)]
    assert paths.read_user_settings()["shortcut_target"] == str(launch.ROOT / "APProcessor.bat")
    assert launch.ensure_shortcut() == "" and len(written) == 1  # in place: nothing more to do


def test_an_ap_coder_0_1_shortcut_is_repointed_too(launch, monkeypatch, tmp_path):
    """AP Coder 0.1 recorded only ``desktop_shortcut: true``, not where the shortcut points."""
    desk, written = _windows_desktop(launch, monkeypatch, tmp_path)
    (desk / "AP Coder.lnk").write_text("C:/AP Coder 0.1/APProcessor.bat")
    paths.write_user_settings(desktop_shortcut=True)
    assert launch.ensure_shortcut() and written == [str(launch.ROOT)]


def test_a_shortcut_that_could_not_be_made_is_tried_again(launch, monkeypatch, tmp_path):
    desk, written = _windows_desktop(launch, monkeypatch, tmp_path, ok=False)
    assert launch.ensure_shortcut().startswith("Could not put a shortcut") and len(written) == 1
    assert launch.ensure_shortcut().startswith("Could not put a shortcut") and len(written) == 2  # retried
    desk, written = _windows_desktop(launch, monkeypatch, tmp_path, ok=True)
    assert launch.ensure_shortcut().startswith("Desktop shortcut") and len(written) == 1


def test_a_shortcut_the_person_deleted_or_declined_is_not_put_back(launch, monkeypatch, tmp_path):
    desk, written = _windows_desktop(launch, monkeypatch, tmp_path)
    launch.ensure_shortcut()
    (desk / "AP Coder.lnk").unlink()  # deleted by the person
    assert launch.ensure_shortcut() == "" and len(written) == 1
    paths.write_user_settings(desktop_shortcut=False, shortcut_target=None)  # --installer --no-shortcut
    assert launch.ensure_shortcut() == "" and len(written) == 1


def test_a_mac_gets_one_launcher_on_its_desktop_that_opens_the_current_install(launch, monkeypatch, tmp_path):
    desk = tmp_path / "Desktop"
    desk.mkdir()
    monkeypatch.setattr(launch, "MAC", True)
    monkeypatch.setattr(launch, "desktop", lambda: desk)
    assert launch.ensure_shortcut().startswith("Desktop shortcut")
    made = desk / "AP Coder.command"
    text = made.read_text()
    assert text.startswith("#!/bin/bash") and f"exec '{launch.ROOT / 'APProcessor.command'}'" in text
    assert os.name == "nt" or made.stat().st_mode & 0o111  # executable on the Mac (Windows has no such bit)
    newer = tmp_path / "AP Coder 0.3"
    monkeypatch.setattr(launch, "ROOT", newer)
    assert "now opens" in launch.ensure_shortcut() and str(newer / "APProcessor.command") in made.read_text()


def test_a_file_of_the_persons_own_on_the_mac_desktop_is_never_replaced(launch, monkeypatch, tmp_path):
    desk = tmp_path / "Desktop"
    desk.mkdir()
    (desk / "AP Coder.command").write_text("#!/bin/bash\necho mine\n")
    monkeypatch.setattr(launch, "MAC", True)
    monkeypatch.setattr(launch, "desktop", lambda: desk)
    assert "AP Coder.command" in launch.ensure_shortcut()  # says where to start AP Coder instead
    assert (desk / "AP Coder.command").read_text() == "#!/bin/bash\necho mine\n"


# --- A start while an AP Coder already runs ---------------------------------------------------------------------


def _quiet_setup(launch, monkeypatch, installed=False, self_check_ok=True):
    """Everything but the step under test stands still; returns (servers started, self-checks run, clean-ups)."""
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("clear", None))
    monkeypatch.setattr(launch, "ensure_packages", lambda state: ("ok", "Packages: all 13 in place", installed))
    monkeypatch.setattr(launch, "ensure_models", lambda first, state: None)
    monkeypatch.setattr(launch, "ensure_shortcut", lambda: "")
    monkeypatch.setattr(launch, "lm_studio_line", lambda: ("info", "LM Studio: not running"))
    monkeypatch.setattr(launch, "page_reader_line", lambda: ("info", "Page reader: -"))
    started: list[list[str]] = []
    monkeypatch.setattr(launch, "start_dashboard", lambda rest, state: started.append(rest) or 0)
    checks: list[int] = []
    result = (True, "Self-check OK: 10 of 10") if self_check_ok else (False, "Self-check FAILED: FAIL a.pdf")
    monkeypatch.setattr(launch, "self_check", lambda: checks.append(1) or result)
    cleaned: list[list[Path]] = []
    monkeypatch.setattr(launch.upgrade, "clean_up", lambda root, data, old, say: cleaned.append(old) or 12_000_000)
    return started, checks, cleaned


def test_an_older_dashboard_still_running_is_stopped_not_opened(launch, monkeypatch, capsys):
    """It was opened as if it were this version: the person saw the old version while this one was half set up."""
    started, _, _ = _quiet_setup(launch, monkeypatch)
    from_port = []
    monkeypatch.setattr(launch.running, "take_over", lambda root, version, ports, data, say: from_port.append(ports)
                        or ("clear", None))  # fmt: skip
    monkeypatch.setattr(launch, "open_browser", lambda url: pytest.fail("opened the old dashboard"))
    args, rest = launch.parse(["--port", "8597"])
    assert launch.run(args, rest) == 0 and started
    assert 8597 in from_port[0] and 8501 in from_port[0]


def test_an_older_dashboard_on_the_next_free_port_is_looked_for_too(launch):
    """AP Coder 0.1 started its dashboard on the first free port from 8501 (a second double-click of start.bat started
    a second one on 8502) and recorded none of them: only 8501 was looked at, so one could keep running on the data
    beside the new version."""
    assert set(range(8501, 8511)) <= set(launch.candidate_ports([], {}))
    assert launch.candidate_ports(["--port", "8597"], {"port": 8600})[:2] == [8597, 8600]


def test_the_same_version_running_is_only_opened(launch, monkeypatch):
    _quiet_setup(launch, monkeypatch)
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("open", 8597))
    monkeypatch.setattr(launch, "ensure_packages", lambda state: pytest.fail("set up again"))
    opened: list[str] = []
    monkeypatch.setattr(launch, "open_browser", opened.append)
    assert launch.run(*launch.parse([])) == 0 and opened == ["http://127.0.0.1:8597"]


def test_an_older_version_that_cannot_be_stopped_stops_this_start(launch, monkeypatch, capsys):
    started, _, _ = _quiet_setup(launch, monkeypatch)
    monkeypatch.setattr(launch.running, "take_over", lambda *a, **k: ("blocked", 8501))
    assert launch.run(*launch.parse([])) == 1 and not started
    assert "Close the old AP Coder window, then start again" in capsys.readouterr().out


# --- An update is said, checked, and cleaned up after --------------------------------------------------------------


def test_an_update_in_the_same_folder_is_said_and_checked(launch, monkeypatch, capsys):
    """The fingerprint was stored but never compared: an update skipped the self-check."""
    started, checks, _ = _quiet_setup(launch, monkeypatch)
    launch.save_state({"setup_done": True, "fingerprint": "0ld", "version": "0.1.0", "build": "2026-09-01T10:00:00",
                       "self_check": {"ok": True, "at": "2026-09-01 10:05"}})  # fmt: skip
    assert launch.run(*launch.parse([])) == 0
    out = capsys.readouterr().out
    assert "Updated AP Coder from 0.1.0 (2026-09-01) to 0.2.0 (2026-10-11)" in out and checks == [1]
    state = launch.load_state()
    assert state["version"] == "0.2.0" and state["fingerprint"] == launch.fingerprint()
    assert launch.run(*launch.parse([])) == 0 and checks == [1]  # the next start: nothing new


def test_an_update_unzipped_over_an_ap_coder_from_before_the_launcher_is_said(launch, monkeypatch, capsys):
    """An AP Coder from before scripts/launch.py (install.bat, or by hand) kept no launcher state: only its .venv says
    which version it was. Unzipped over it, the update was never said."""
    _quiet_setup(launch, monkeypatch)
    monkeypatch.setattr(launch, "registered_version", lambda: "0.1.0")
    assert launch.run(*launch.parse(["--venv=reused"])) == 0
    assert "Updated AP Coder from 0.1.0 to 0.2.0 (2026-10-11)" in capsys.readouterr().out
    monkeypatch.setattr(launch, "registered_version", lambda: "0.2.0")  # registered again: the next start
    assert launch.run(*launch.parse(["--venv=reused"])) == 0 and "Updated" not in capsys.readouterr().out


def test_an_update_from_another_folder_takes_over_and_cleans_up_after_its_self_check(launch, monkeypatch, tmp_path,
                                                                                      capsys):  # fmt: skip
    started, checks, cleaned = _quiet_setup(launch, monkeypatch)
    old = tmp_path / "AP Coder 0.1"
    (old / "ap_coder").mkdir(parents=True)
    (old / "scripts").mkdir()
    (old / "scripts" / "launch.py").write_text("")
    (old / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    paths.record_install(paths.install_identity(old))
    args, rest = launch.parse(["--venv=created"])
    assert launch.run(args, rest) == 0
    out = capsys.readouterr().out
    assert "Updated AP Coder from 0.1.0 to 0.2.0 (2026-10-11)" in out and checks == [1]
    assert paths.current_install()["folder"] == str(launch.ROOT)
    assert cleaned == [[old]] and "Cleaned up the older AP Coder: 12 MB freed" in out
    assert (launch.ROOT / ".ap_coder_install.json").exists()


def test_the_older_install_is_kept_while_the_self_check_fails(launch, monkeypatch, tmp_path, capsys):
    _, _, cleaned = _quiet_setup(launch, monkeypatch, self_check_ok=False)
    old = tmp_path / "AP Coder 0.1"
    (old / "ap_coder").mkdir(parents=True)
    (old / "scripts").mkdir()
    (old / "scripts" / "launch.py").write_text("")
    (old / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    paths.record_install(paths.install_identity(old))
    assert launch.run(*launch.parse(["--venv=created"])) == 0
    assert cleaned == [] and f"kept in {old} until the self-check passes" in capsys.readouterr().out


def test_an_ap_coder_0_1_folder_whose_data_moved_out_is_still_cleaned_up(launch, monkeypatch, tmp_path, capsys):
    """AP Coder 0.1 kept no record of itself: it was found only from its data folder (``<folder>/private``). The
    start moved that data out first, so the older folder (and its 1 GB .venv) was never cleaned up: not on this
    start, nor on a later one after a failed self-check."""
    monkeypatch.delenv("AP_PRIVATE_DIR")
    home = tmp_path / "APCoder"
    monkeypatch.setattr(launch, "ensure_data_folder",
                        lambda: launch.upgrade.settle_data_folder(launch.ROOT, launch.say, default=home))  # fmt: skip
    old = tmp_path / "AP Coder 0.1"
    (old / "ap_coder").mkdir(parents=True)
    (old / "scripts").mkdir()
    (old / "scripts" / "launch.py").write_text("")
    (old / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    (old / "private").mkdir()
    (old / "private" / "ap_coder.db").write_text("the data")
    paths.write_user_settings(data_dir=str(old / "private"))  # all AP Coder 0.1 recorded
    _, _, cleaned = _quiet_setup(launch, monkeypatch, self_check_ok=False)
    assert launch.run(*launch.parse(["--venv=created"])) == 0
    assert (home / "ap_coder.db").read_text() == "the data" and cleaned == []
    assert f"kept in {old} until the self-check passes" in capsys.readouterr().out
    _, _, cleaned = _quiet_setup(launch, monkeypatch)
    assert launch.run(*launch.parse(["--check"])) == 0 and cleaned == [[old]]


def test_an_older_ap_coder_run_by_hand_and_stopped_by_this_start_brings_its_data(launch, monkeypatch, tmp_path, capsys):
    """AP Coder before the launcher, run by hand (``python -m ap_coder dashboard``): its data in its own private/,
    nothing recorded. Its dashboard was stopped by this start, yet the new version started on an empty database and
    left the older folder, data and .venv as they were (found by updating over PR #1's main for real)."""
    monkeypatch.delenv("AP_PRIVATE_DIR")
    home = tmp_path / "APCoder"
    monkeypatch.setattr(launch, "ensure_data_folder",
                        lambda: launch.upgrade.settle_data_folder(launch.ROOT, launch.say, default=home))  # fmt: skip
    old = tmp_path / "AP_Processer-main"
    (old / "ap_coder").mkdir(parents=True)
    (old / "start.sh").write_text("")
    (old / "pyproject.toml").write_text('[project]\nname = "ap-coder"\nversion = "0.1.0"\n')
    (old / "private").mkdir()
    (old / "private" / "ap_coder.db").write_text("the data")
    _, _, cleaned = _quiet_setup(launch, monkeypatch, self_check_ok=False)
    monkeypatch.setattr(launch.running, "stopped", [str(old)])  # its dashboard, stopped by this start
    assert launch.run(*launch.parse(["--venv=created"])) == 0
    out = capsys.readouterr().out
    assert (home / "ap_coder.db").read_text() == "the data"  # before the dashboard starts, self-check or not
    assert "Updated AP Coder from 0.1.0" in out and cleaned == []
    _, _, cleaned = _quiet_setup(launch, monkeypatch)
    assert launch.run(*launch.parse(["--check"])) == 0 and cleaned == [[old]]


def test_an_older_ap_coder_closed_before_the_update_next_to_this_one_is_taken_over(launch, monkeypatch, tmp_path,
                                                                                   capsys):  # fmt: skip
    """Not running, not on record, no desktop shortcut (a Mac, Linux): the older folder next to this one kept its data
    (run by hand: in its private/) and its .venv, and could be started by mistake on the updated data."""
    monkeypatch.delenv("AP_PRIVATE_DIR")
    home = tmp_path / "APCoder"
    monkeypatch.setattr(launch, "ensure_data_folder",
                        lambda: launch.upgrade.settle_data_folder(launch.ROOT, launch.say, default=home))  # fmt: skip
    old = tmp_path / "APProcessor"
    (old / "ap_coder").mkdir(parents=True)
    (old / "APProcessor.command").write_text("")
    (old / "pyproject.toml").write_text('[project]\nname = "ap-coder"\nversion = "0.1.0"\n')
    (old / "private").mkdir()
    (old / "private" / "ap_coder.db").write_text("the data")
    _, _, cleaned = _quiet_setup(launch, monkeypatch)
    assert launch.run(*launch.parse(["--venv=created"])) == 0
    assert (home / "ap_coder.db").read_text() == "the data" and cleaned == [[old]]
    assert "Updated AP Coder from 0.1.0" in capsys.readouterr().out


def test_data_that_could_not_all_be_moved_stops_the_start_plainly(launch, monkeypatch, capsys):
    started, _, _ = _quiet_setup(launch, monkeypatch)

    def stuck():
        raise launch.upgrade.DataMoveError(
            "Your data could not all be moved from A to B (in use: A/ap_coder.db).",
            "Close the program that has that file open, then start AP Coder again.",
        )

    monkeypatch.setattr(launch, "ensure_data_folder", stuck)
    assert launch.run(*launch.parse([])) == 1 and not started
    out = capsys.readouterr().out
    assert "could not all be moved" in out and "What to do: Close the program" in out


def test_a_database_made_by_a_newer_ap_coder_stops_the_start_plainly(launch, monkeypatch, tmp_path, capsys):
    started, _, _ = _quiet_setup(launch, monkeypatch)
    import sqlite3

    data = tmp_path / "private"  # AP_PRIVATE_DIR (tests/conftest.py)
    data.mkdir()
    conn = sqlite3.connect(data / "ap_coder.db")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('schema_version', ?)", (str(SCHEMA_VERSION + 1),))
    conn.commit()
    conn.close()
    assert launch.run(*launch.parse([])) == 1 and not started
    out = capsys.readouterr().out
    assert "made by a newer AP Coder" in out and "nothing was changed" in out


def test_settings_no_longer_used_are_commented_out_and_a_code_folder_env_moves(launch, monkeypatch, tmp_path, capsys):
    _quiet_setup(launch, monkeypatch)
    data = tmp_path / "private"
    data.mkdir()
    (data / ".env").write_text("AP_REVIEWER=Ann\nAP_PAGE_READER=on\nAP_LLM_PROVIDER=local\n")
    (launch.ROOT / ".env").write_text("AP_REVIEWER=Old\n")
    assert launch.run(*launch.parse([])) == 0
    text = (data / ".env").read_text()
    assert "# no longer used (AP Coder 0.2): AP_PAGE_READER=on" in text and "\nAP_REVIEWER=Ann\n" in "\n" + text
    assert "# no longer used (AP Coder 0.2): AP_LLM_PROVIDER=local" in text
    assert not (launch.ROOT / ".env").exists() and list((data / "backups").glob("from-*/.env"))
    assert "no longer used" in capsys.readouterr().out


def test_a_settings_file_that_cannot_be_changed_never_stops_the_start(launch, monkeypatch, tmp_path, capsys):
    """A read-only (or open) .env with settings no longer used stopped every start with a traceback."""
    started, _, _ = _quiet_setup(launch, monkeypatch)
    data = tmp_path / "private"
    data.mkdir()
    (data / ".env").write_text("AP_PAGE_READER=on\n")
    (launch.ROOT / ".env").write_text("AP_REVIEWER=Old\n")

    def refused(*args, **kwargs):
        raise PermissionError(13, "Access is denied", str(data / ".env"))

    monkeypatch.setattr(launch.envfile, "retire", refused)
    monkeypatch.setattr(launch.upgrade.shutil, "move", refused)
    assert launch.run(*launch.parse([])) == 0 and started
    out = capsys.readouterr().out
    assert "can't be changed" in out and "Traceback" not in out


def test_leftovers_of_an_older_version_unzipped_over_are_removed(launch, monkeypatch, capsys):
    _quiet_setup(launch, monkeypatch)
    manifest = {
        "version": "0.2.0",
        "built": "2026-10-11T09:00:00",
        "files": {"ap_coder/x.py": "0" * 64, "scripts/launch.py": "0" * 64},
    }
    (launch.ROOT / "bundle_manifest.json").write_text(json.dumps(manifest))
    (launch.ROOT / "ap_coder" / "x.py").write_text("")
    (launch.ROOT / "ap_coder" / "retired.py").write_text("")
    assert launch.run(*launch.parse([])) == 0
    assert not (launch.ROOT / "ap_coder" / "retired.py").exists() and (launch.ROOT / "ap_coder" / "x.py").exists()
    assert "1 file the older version left" in capsys.readouterr().out


# --- An older folder started by mistake ---------------------------------------------------------------------------


def test_an_older_folder_started_by_mistake_opens_the_newest(launch, monkeypatch, tmp_path, capsys):
    newest = tmp_path / "AP Coder 0.3"
    (newest / "ap_coder").mkdir(parents=True)
    (newest / "scripts").mkdir()
    (newest / "scripts" / "launch.py").write_text("")
    (newest / "pyproject.toml").write_text('[project]\nversion = "0.3.0"\n')
    paths.record_install(paths.install_identity(newest))
    monkeypatch.setattr(launch, "take_lock", lambda: True)
    monkeypatch.setattr(launch, "bootstrap", lambda argv: pytest.fail("set up the older folder"))
    handed: list = []
    monkeypatch.setattr(launch.upgrade, "hand_over", lambda folder, argv, env: handed.append((folder, argv)) or 0)
    assert launch.main(["--port", "8502"]) == 0
    assert handed == [(newest, ["--port", "8502"])]
    assert f"A newer AP Coder (0.3.0) is in {newest}: opening that one" in capsys.readouterr().out
    # This older folder (an older bundle unzipped again by mistake) is on record: the newest cleans it up.
    assert paths.current_install()["folder"] == str(newest)
    assert any(paths.same_folder(e["folder"], launch.ROOT) for e in paths.installs())


# --- The server -----------------------------------------------------------------------------------------------------


def test_the_server_is_started_with_serve_and_opened_once_it_is_up(launch, monkeypatch, capsys):
    cmd = launch.server_command(8597, ["--address", "0.0.0.0"])
    assert cmd[1:] == ["-m", "ap_coder", "serve", "--port", "8597", "--no-browser", "--address", "0.0.0.0"]
    script = "import time; print('starting', flush=True); print('AP Coder is running at http://127.0.0.1:8597', " \
             "flush=True); time.sleep(0.5); print('bye', flush=True)"  # fmt: skip
    monkeypatch.setattr(launch, "server_command", lambda port, rest: [sys.executable, "-c", script])
    monkeypatch.setattr(launch.running, "health", lambda port, timeout=1.0: None)
    opened: list[str] = []
    monkeypatch.setattr(launch, "open_browser", opened.append)
    assert launch.start_dashboard(["--port", "8597"], {}) == 0
    assert opened == ["http://127.0.0.1:8597"]
    out = capsys.readouterr().out
    assert out.index("AP Coder is running at") < out.index("bye")  # its output is shown as it comes


def test_a_server_that_answers_its_health_check_is_opened_without_the_line(launch, monkeypatch):
    script = "import time; time.sleep(1.5)"
    monkeypatch.setattr(launch, "server_command", lambda port, rest: [sys.executable, "-c", script])
    monkeypatch.setattr(launch.running, "health", lambda port, timeout=1.0: {"app": "ap-coder"})
    opened: list[str] = []
    monkeypatch.setattr(launch, "open_browser", opened.append)
    assert launch.start_dashboard(["--port", "8597"], {}) == 0 and opened == ["http://127.0.0.1:8597"]


def test_a_server_that_stops_at_once_opens_nothing(launch, monkeypatch):
    monkeypatch.setattr(launch, "server_command", lambda port, rest: [sys.executable, "-c", "raise SystemExit(3)"])
    monkeypatch.setattr(launch.running, "health", lambda port, timeout=1.0: None)
    monkeypatch.setattr(launch, "open_browser", lambda url: pytest.fail("opened"))
    assert launch.start_dashboard(["--port", "8597"], {}) == 3


# --- A wheelhouse that doesn't fit this computer ----------------------------------------------------------------


def _wheelhouse(launch, *names: str) -> None:
    folder = launch.ROOT / "wheelhouse"
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"")


WINDOWS_ONLY = ("numpy-2.3.0-cp311-cp311-win_amd64.whl", "numpy-2.3.0-cp312-cp312-win_amd64.whl",
                "pandas-2.3.0-cp311-cp311-win_amd64.whl", "pandas-2.3.0-cp312-cp312-win_amd64.whl",
                "openpyxl-3.1.5-py2.py3-none-any.whl")  # fmt: skip


def _computer(launch, monkeypatch, system: str, version: tuple[int, int], tags: list[str]):
    monkeypatch.setattr(launch, "this_computer", lambda: system)
    monkeypatch.setattr(launch.sys, "version_info", (*version, 0, "final", 0))
    monkeypatch.setattr(launch, "here_tags", lambda: set(tags))


def test_a_windows_wheelhouse_on_a_mac_is_said_in_one_plain_message(launch, monkeypatch):
    """The tester: a Mac with Python 3.14 and the bundle of an older Windows build. pip failed with an unclear error."""
    _wheelhouse(launch, *WINDOWS_ONLY)
    _computer(launch, monkeypatch, "mac", (3, 14), ["cp314-cp314-macosx_14_0_arm64", "py3-none-any"])
    assert launch.wheelhouse_misfit() == (
        "This package folder (wheelhouse) was built for Windows, Python 3.11/3.12; this computer is a Mac with "
        "Python 3.14. Use the bundle built for Macs (build it with --mac)"
    )
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["pandas>=2.1"], "Missing: ap_coder"))
    monkeypatch.setattr(launch, "pip_install", lambda args: pytest.fail("pip ran"))
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert "built for Windows, Python 3.11/3.12" in str(exc.value) and "nothing was installed" in str(exc.value)


def test_a_wheelhouse_for_another_python_says_which_to_install(launch, monkeypatch):
    _wheelhouse(launch, *WINDOWS_ONLY)
    _computer(launch, monkeypatch, "windows", (3, 14), ["cp314-cp314-win_amd64", "py3-none-any"])
    message = launch.wheelhouse_misfit()
    assert "built for Windows, Python 3.11/3.12; this computer has Python 3.14" in message
    assert "install Python 3.12" in message and "--python 3.14" in message
    # The tester's case: what to do agrees with it (it said to unzip "the bundle built for this computer", which this
    # one is: only its Python differs).
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["pandas>=2.1"], "Missing: ap_coder"))
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert "Install Python 3.12" in exc.value.hint and "bundle built for this computer" not in exc.value.hint


def test_a_wheelhouse_that_fits_says_nothing(launch, monkeypatch):
    _wheelhouse(launch, *WINDOWS_ONLY)
    _computer(launch, monkeypatch, "windows", (3, 12), ["cp312-cp312-win_amd64", "py3-none-any"])
    assert launch.wheelhouse_misfit() == "" and launch.pip_source()[0] == "--no-index"
    (launch.ROOT / "wheelhouse").rename(launch.ROOT / "elsewhere")
    _wheelhouse(launch, "openpyxl-3.1.5-py2.py3-none-any.whl")  # nothing binary to judge by
    assert launch.wheelhouse_misfit() == ""


def test_with_the_internet_allowed_a_misfit_wheelhouse_falls_back_to_pypi(launch, monkeypatch, capsys):
    _wheelhouse(launch, *WINDOWS_ONLY)
    _computer(launch, monkeypatch, "mac", (3, 14), ["cp314-cp314-macosx_14_0_arm64", "py3-none-any"])
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    assert launch.pip_source() == []
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["pandas>=2.1"], ""))
    calls: list[list[str]] = []
    monkeypatch.setattr(launch, "pip_install", lambda args: calls.append(args) or True)
    with pytest.raises(launch.SetupError):  # (pip "installed" nothing: still missing)
        launch.ensure_packages({})
    assert calls and "Installing from the internet (PyPI) instead" in capsys.readouterr().out


def test_a_wheelhouse_copied_into_the_wheelhouse_folder_is_used_and_judged(launch, monkeypatch):
    """An older bundle's wheelhouse copied over the folder lands in wheelhouse/wheelhouse: it was not looked at, so
    pip found nothing and the start listed every package as missing instead of saying what the wheels fit."""
    nested = launch.ROOT / "wheelhouse" / "wheelhouse"
    nested.mkdir(parents=True)
    for name in WINDOWS_ONLY:
        (nested / name).write_bytes(b"")
    _computer(launch, monkeypatch, "windows", (3, 14), ["cp314-cp314-win_amd64", "py3-none-any"])
    assert "built for Windows, Python 3.11/3.12; this computer has Python 3.14" in launch.wheelhouse_misfit()
    assert launch.pip_source() == ["--no-index", "--find-links", str(nested)]
    _computer(launch, monkeypatch, "windows", (3, 12), ["cp312-cp312-win_amd64", "py3-none-any"])
    assert launch.wheelhouse_misfit() == ""


def _wheels_in(folder: Path, names=WINDOWS_ONLY) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"")
    return folder


@pytest.mark.parametrize("where", [".wheelhouse", "wheelhouse (1)", "Wheelhouse-old", "APProcessor/wheelhouse",
                                   ".wheelhouse/wheelhouse"])  # fmt: skip
def test_a_wheelhouse_under_a_near_miss_name_is_found_and_judged(launch, monkeypatch, where, capsys):
    """The tester had the older bundle's wheels in ".wheelhouse": not seen at all, so the start said "Missing
    packages: ..." (no wheelhouse here) instead of what the wheels were built for. One folder of wheels under a
    near-miss name (or one level deeper, after a double unzip) is used, and said once."""
    found = _wheels_in(launch.ROOT / where)
    _computer(launch, monkeypatch, "windows", (3, 14), ["cp314-cp314-win_amd64", "py3-none-any"])
    assert launch.wheelhouse_dir() == found and launch.pip_source() == ["--no-index", "--find-links", str(found)]
    assert "built for Windows, Python 3.11/3.12; this computer has Python 3.14" in launch.wheelhouse_misfit()
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["pandas>=2.1"], "Missing: ap_coder"))
    monkeypatch.setattr(launch, "pip_install", lambda args: pytest.fail("pip ran"))
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert "built for Windows" in str(exc.value) and "Missing packages" not in str(exc.value)
    assert f"Using the package folder {Path(where)}" in capsys.readouterr().out


def test_several_near_miss_wheelhouses_are_named_not_guessed(launch, monkeypatch):
    _wheels_in(launch.ROOT / ".wheelhouse")
    _wheels_in(launch.ROOT / "wheelhouse (1)")
    (launch.ROOT / "wheelhouse").mkdir()  # empty: the bundle's own was deleted
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["pandas>=2.1"], ""))
    monkeypatch.setattr(launch, "pip_install", lambda args: pytest.fail("pip ran"))
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert ".wheelhouse" in str(exc.value) and "wheelhouse (1)" in str(exc.value)
    assert "rename it to wheelhouse" in exc.value.hint
    _wheels_in(launch.ROOT / "wheelhouse")  # the bundle's own is there: it is the one used, nothing to ask
    assert launch.wheelhouse_dir() == launch.ROOT / "wheelhouse"


def test_a_wheelhouse_that_does_not_fit_is_said_once_not_twice(launch, monkeypatch, capsys):
    """The first phase said what the manifest claims ("built for Python 3.12"), then the second what the wheels say
    ("built for Windows, Python 3.11/3.12"): two different messages. The wheels are judged once, in the second."""
    _wheelhouse(launch, *WINDOWS_ONLY)
    manifest = {"version": "0.2.0", "built": "2026-10-11T09:00:00", "targets": ["this computer (linux-x86_64, "
                                                                                 "Python 3.12)"]}  # fmt: skip
    (launch.ROOT / "bundle_manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(launch, "sys", SimpleNamespace(version_info=(3, 14, 0), version="3.14.0 (main)",
                                                       executable="/usr/bin/python3.14", platform="linux"))  # fmt: skip
    monkeypatch.setattr(launch.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(launch, "ensure_venv", lambda python="", rebuild=False: "created")
    monkeypatch.setattr(launch, "find_python", lambda versions: None)  # only Python 3.14 on this computer
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd: 1)
    assert launch.bootstrap([]) == 1
    assert "Note: this offline bundle" not in capsys.readouterr().out
    (launch.ROOT / "wheelhouse").rename(launch.ROOT / "elsewhere")  # no wheels to judge by: the manifest's word
    assert launch.bootstrap([]) == 1
    assert "Note: this offline bundle has packages for Python 3.12, not for Python 3.14" in capsys.readouterr().out


def test_a_full_disk_is_said_plainly_not_as_missing_packages(launch, monkeypatch, tmp_path):
    """pip stopped on a full disk (the new .venv is made while the older one still takes its 1 GB): the start said
    the wheelhouse "has no openai, pandas, ... for Python 3.12" and to install Python 3.12, which it was."""
    _wheelhouse(launch, "openpyxl-3.1.5-py2.py3-none-any.whl")
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["pandas>=2.1", "openai>=1.68"], ""))
    lines = ["Processing ./wheelhouse/pandas-3.0.6-cp312-cp312-manylinux_2_28_x86_64.whl\n",
             "ERROR: Could not install packages due to an OSError: [Errno 28] No space left on device\n"]  # fmt: skip

    class Pip:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(lines)

        def wait(self):
            return 1

    monkeypatch.setattr(launch.subprocess, "Popen", Pip)
    old = tmp_path / "AP Coder 0.1"
    (old / ".venv" / "lib").mkdir(parents=True)
    (old / ".venv" / "lib" / "big.bin").write_bytes(b"v" * 3_000_000)
    paths.record_install({"folder": str(old), "version": "0.1.0"}, current=False)
    with pytest.raises(launch.SetupError) as exc:
        launch.ensure_packages({})
    assert "disk is full" in str(exc.value) and "has no" not in exc.value.hint
    assert f"{old / '.venv'} (3 MB)" in exc.value.hint


# --- A .venv on a Python the bundle has no packages for ----------------------------------------------------------


def _venv_made_with(launch, version: str) -> None:
    (launch.VENV / "pyvenv.cfg").write_text(f"home = /usr/bin\nversion = {version}.1\n")
    launch.VENV_PY.parent.mkdir(parents=True, exist_ok=True)
    launch.VENV_PY.write_text("")


def test_a_venv_on_a_python_without_packages_is_rebuilt_with_one_that_has_them(launch, monkeypatch, capsys):
    _venv_made_with(launch, "3.14")
    monkeypatch.setattr(launch, "fitting_pythons", lambda: ["3.11", "3.12"])
    monkeypatch.setattr(launch, "venv_fits_probe", lambda: False)  # .venv's own answer: it can't be set up
    monkeypatch.setattr(launch, "sys", SimpleNamespace(version_info=(3, 12, 4), version="3.12.4 (main)",
                                                       executable="/usr/bin/python3.12", platform="linux"))  # fmt: skip
    made: list = []
    monkeypatch.setattr(launch, "ensure_venv", lambda python="", rebuild=False: made.append(rebuild) or "recreated")
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd: 0)
    assert launch.bootstrap([]) == 0 and made == [True]
    assert "made with Python 3.14" in capsys.readouterr().out


def test_a_python_without_packages_hands_over_to_one_that_has_them(launch, monkeypatch, capsys):
    monkeypatch.setattr(launch, "fitting_pythons", lambda: ["3.12"])
    monkeypatch.setattr(launch, "sys", SimpleNamespace(version_info=(3, 14, 0), version="3.14.0 (main)",
                                                       executable="/usr/bin/python3.14", platform="linux"))  # fmt: skip
    monkeypatch.setattr(launch, "find_python", lambda versions: ("/usr/bin/python3.12", "3.12"))
    monkeypatch.setattr(launch, "ensure_venv", lambda python="", rebuild=False: pytest.fail("made .venv with 3.14"))
    calls: list = []
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert launch.bootstrap(["--port", "8502"]) == 0
    assert calls[0][0] == "/usr/bin/python3.12" and calls[0][-2:] == ["--port", "8502"]
    assert "using Python 3.12" in capsys.readouterr().out


def test_the_double_clicks_quick_check_keeps_a_venv_that_works(launch, monkeypatch):
    _venv_made_with(launch, "3.14")
    monkeypatch.setattr(launch, "fitting_pythons", lambda: ["3.12"])
    monkeypatch.setattr(launch.sys, "version_info", (3, 14, 0, "final", 0))
    monkeypatch.setattr(launch, "find_python", lambda versions: ("/usr/bin/python3.12", "3.12"))
    monkeypatch.setattr(launch, "missing_packages", lambda extras: ([], ""))
    assert launch.venv_fits()  # everything is in place: kept, whatever its Python
    monkeypatch.setattr(launch, "missing_packages", lambda extras: (["pandas>=2.1"], ""))
    assert not launch.venv_fits()  # it can't be set up, and a Python that can is installed
    monkeypatch.setattr(launch, "find_python", lambda versions: None)
    assert launch.venv_fits()  # no better Python here: kept (the setup says what to do)
    monkeypatch.setattr(launch.sys, "version_info", (3, 12, 0, "final", 0))
    monkeypatch.setattr(launch, "find_python", lambda versions: pytest.fail("looked for another Python"))
    assert launch.venv_fits()


def test_the_double_click_asks_the_venv_before_using_it():
    bat = (ROOT / "APProcessor.bat").read_text(encoding="ascii")
    assert '".venv\\Scripts\\python.exe" scripts\\launch.py --venv-ok' in bat
    command = (ROOT / "APProcessor.command").read_text()
    assert ".venv/bin/python scripts/launch.py --venv-ok" in command


def test_an_install_set_up_by_install_bat_is_checked_before_the_older_one_is_cleaned_up(launch, monkeypatch, tmp_path):
    """The installer (install.bat in older versions, --installer now) records the setup but runs no self-check: the
    clean-up waits for one, run on the next start."""
    _, checks, cleaned = _quiet_setup(launch, monkeypatch)
    old = tmp_path / "AP Coder 0.1"
    (old / "ap_coder").mkdir(parents=True)
    (old / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    paths.record_install({"folder": str(old), "version": "0.1.0"}, current=False)
    ident = launch.identity()
    launch.save_state({"setup_done": True, "fingerprint": ident["fingerprint"], "version": "0.2.0",
                       "build": ident["build"]})  # fmt: skip
    assert launch.run(*launch.parse([])) == 0 and checks == [1] and cleaned == [[old]]


def test_a_desktop_and_shortcut_with_accents_are_read_right_on_windows(launch, monkeypatch):
    """A desktop in "C:\\Users\\José\\OneDrive - Société\\Bureau" came back mangled from PowerShell (the console's
    code page, read as ANSI): the shortcut was never made, and an AP Coder 0.1 shortcut's folder never found."""
    from tests.test_running import fake_windows_powershell

    monkeypatch.setattr(launch, "WINDOWS", True)
    desk = "C:\\Users\\José\\OneDrive - Société\\Bureau"
    monkeypatch.setattr(launch.subprocess, "run", fake_windows_powershell(desk))
    assert str(launch.desktop()) == desk
    target = "/Users/José/Données/AP Coder 0.1/APProcessor.bat"  # (a path this test's computer can take apart)
    monkeypatch.setattr(launch.subprocess, "run", fake_windows_powershell(target))
    paths.write_user_settings(desktop_shortcut=True)
    monkeypatch.setattr(launch, "desktop", lambda: launch.ROOT)
    (launch.ROOT / "AP Coder.lnk").write_text("")
    assert Path(launch.old_shortcut_folder()) == Path("/Users/José/Données/AP Coder 0.1")  # "\\" on Windows
