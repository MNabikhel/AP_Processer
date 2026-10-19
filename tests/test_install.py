"""The installer can be run again and again: it updates settings in place and never duplicates anything."""

import importlib.util
import json
from pathlib import Path

import pytest

from ap_coder.core import paths
from ap_coder.core.config import Settings

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ap_install", ROOT / "scripts" / "install.py")
install = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install)


def test_env_values_are_updated_in_place(tmp_path):
    env = tmp_path / ".env"
    env.write_text((ROOT / ".env.example").read_text())
    before = env.read_text().count("\n")
    for _ in range(3):  # running the installer three times
        install.write_env(env, {"AZURE_OPENAI_ENDPOINT": "https://x.openai.azure.com/", "AP_REVIEWER": "Jane Doe"})
    text = env.read_text()
    assert text.count("AZURE_OPENAI_ENDPOINT=") == 1 and text.count("AP_REVIEWER=") == 1
    assert env.read_text().count("\n") == before  # the commented "# AP_REVIEWER=" line was reused
    values = install.read_env(env)
    assert values["AP_REVIEWER"] == "Jane Doe" and values["AZURE_OPENAI_ENDPOINT"] == "https://x.openai.azure.com/"


def test_placeholders_count_as_not_filled_in():
    values = install.read_env(ROOT / ".env.example")
    assert "AZURE_OPENAI_ENDPOINT" not in values
    assert not [k for k in values if k.startswith("AZURE_")]  # developer only: commented out, ignored offline


def test_the_example_settings_offer_no_reading_choices():
    """Every invoice is read the same way: the example .env names no mode, scope, provider or vision switch."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("AP_PAGE_READER", "AP_LLM_PROVIDER", "AP_VISION", "AP_LLM_VISION"):
        assert f"{key}=" not in text and f"{key}_" not in text, key
    azure = text[text.index("Developer only: cloud services") :]
    assert "AP_ALLOW_INTERNET=1" in azure and "IGNORED by the offline build" in azure
    assert all(line.startswith("#") for line in text.splitlines() if line.startswith("AZURE") or "AZURE_" in line)


def _forget_env_after_test(monkeypatch, text):
    """load_dotenv writes os.environ: every key in ``text`` is put back as it was after the test."""
    for line in text.splitlines():
        key = line.lstrip("# ").partition("=")[0].strip()
        if key.isupper() and "=" in line:
            monkeypatch.setenv(key, "")
            monkeypatch.delenv(key)


def test_installer_env_has_no_example_endpoints(tmp_path, monkeypatch):
    """The .env the installer writes never points the app at an example Azure endpoint: it stays offline."""
    from ap_coder.ai.local_llm import resolve_provider

    env = tmp_path / ".env"
    install.write_example_env(env)
    _forget_env_after_test(monkeypatch, env.read_text())
    live = [line for line in env.read_text().splitlines() if not line.startswith("#")]
    assert not [line for line in live if "<your-resource>" in line]
    settings = Settings.from_env(env)
    assert settings.openai.endpoint is None and settings.document_intelligence.endpoint is None
    assert resolve_provider(settings, probe=False) == "local"  # not "azure"


def test_example_endpoints_left_in_an_older_env_count_as_unset(tmp_path, monkeypatch):
    """A .env written by an older installer still has the example endpoints in effect: the app ignores them."""
    from ap_coder.ai.local_llm import resolve_provider

    env = tmp_path / ".env"
    env.write_text(
        "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com/\n"
        "AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/\n"
        "AZURE_OPENAI_DEPLOYMENT=gpt-4o\n"
    )
    _forget_env_after_test(monkeypatch, env.read_text() + "AP_LLM_PROVIDER=\n")
    settings = Settings.from_env(env)
    assert settings.openai.endpoint is None and settings.document_intelligence.endpoint is None
    assert resolve_provider(settings, probe=False) == "local"


def test_terminal_works_after_the_folder_is_moved():
    """activate.bat has the folder the .venv was made in written into it: scripts\\terminal.bat puts .venv\\Scripts on
    PATH, from the app folder (the one above scripts\\), wherever that folder is now."""
    raw = (ROOT / "scripts" / "terminal.bat").read_bytes()
    text = raw.decode()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")  # Windows line endings throughout
    assert 'pushd "%~dp0.."' in text
    assert text.index('pushd "%~dp0.."') < text.index('if not exist ".venv\\Scripts\\python.exe"')
    assert 'set "PATH=%CD%\\.venv\\Scripts;%PATH%"' in text
    assert "Scripts\\activate" not in text
    assert "APProcessor.bat first" in text


def test_endpoints_are_tidied():
    assert install.clean_url(" myres.openai.azure.com ") == "https://myres.openai.azure.com/"
    assert (
        install.clean_url("https://myres.cognitiveservices.azure.com//") == "https://myres.cognitiveservices.azure.com/"
    )
    assert install.clean_url("") == ""


def test_fresh_start_keeps_settings_and_backs_up_data(tmp_path):
    (tmp_path / ".env").write_text("AP_REVIEWER=x\n")
    (tmp_path / "ap_coder.db").write_text("db")
    (tmp_path / "invoices").mkdir()
    (tmp_path / "invoices" / "a.pdf").write_text("pdf")
    install.fresh_start(_yes_console(), tmp_path)
    backups = list(tmp_path.glob("backup-*"))
    assert len(backups) == 1 and (backups[0] / "ap_coder.db").exists() and (backups[0] / "invoices" / "a.pdf").exists()
    assert (tmp_path / ".env").exists() and list((tmp_path / "invoices").iterdir()) == []


def test_fresh_start_keeps_the_models_ap_coder_reads(tmp_path, monkeypatch):
    """B-173: --fresh-start moved the data folder's models/ (the table finder IT put there, or the one the setup
    downloaded from the code alone) into the backup folder: line items were read without it from then on."""
    from ap_coder.ops import offline

    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path))
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / offline.TABLE_FINDER_NAME).write_bytes(b"onnx")
    (tmp_path / "ap_coder.db").write_text("db")
    assert tmp_path / "models" in offline.table_finder_dirs()  # (where AP Coder reads it from)
    install.fresh_start(_yes_console(), tmp_path)
    assert (tmp_path / "models" / offline.TABLE_FINDER_NAME).read_bytes() == b"onnx"
    assert not (tmp_path / "ap_coder.db").exists()


def _yes_console():
    c = install.Console(assume_yes=False)
    c.yes = lambda question, default=True: True
    return c


def test_app_uses_the_data_folder_recorded_by_the_installer(tmp_path, monkeypatch):
    monkeypatch.delenv("AP_PRIVATE_DIR")
    data = tmp_path / "APCoder"
    data.mkdir()
    (data / ".env").write_text("AZURE_OPENAI_DEPLOYMENT=from-data-folder\n")
    paths.write_user_settings(data_dir=str(data))
    assert json.loads(paths.user_settings_path().read_text())["data_dir"] == str(data)
    assert paths.private_dir() == data and paths.default_db_path() == data / "ap_coder.db"
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "")  # restored after the test (load_dotenv writes it)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT")
    monkeypatch.chdir(tmp_path)
    assert paths.env_file() == data / ".env"
    assert Settings.from_env().openai.deployment == "from-data-folder"


def test_offline_installer_never_runs_git(tmp_path, monkeypatch):
    """The offline default: no ``git fetch`` / ``git pull`` from GitHub, even in a git checkout."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(install, "ROOT", tmp_path)
    monkeypatch.setattr(install.subprocess, "run", lambda cmd, **kwargs: pytest.fail(f"ran {cmd}"))
    args = install.argparse.Namespace(no_update=False)
    assert install.update_code(install.Console(assume_yes=True), args) is None


def test_installer_checks_for_updates_only_with_the_opt_in(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(install, "ROOT", tmp_path)
    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    ran: list[list[str]] = []

    def run(cmd, **kwargs):
        ran.append(cmd)
        return install.subprocess.CompletedProcess(cmd, 1, "", "")  # GitHub not reachable

    monkeypatch.setattr(install.subprocess, "run", run)
    assert install.update_code(install.Console(assume_yes=True), install.argparse.Namespace(no_update=False)) is None
    assert any(cmd[:2] == ["git", "fetch"] for cmd in ran)


def test_offline_reinstall_runs_no_pip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install.launch, "ROOT", tmp_path)  # an offline bundle with no wheelhouse here
    (tmp_path / "bundle_manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(install.launch, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(install.launch.subprocess, "Popen", lambda *a, **k: pytest.fail("pip ran offline"))
    monkeypatch.setattr(install.launch, "ensure_packages", lambda state, extras: ("ok", "Packages: all in place", 0))
    args = install.argparse.Namespace(reinstall=True)
    install.ensure_venv_and_packages(install.Console(assume_yes=True), args, "reused")
    assert "Not reinstalled" in capsys.readouterr().out


def test_settings_saved_with_a_byte_order_mark_keep_the_data_folder(tmp_path, monkeypatch):
    """Notepad's "UTF-8 with BOM": the recorded data folder is read, not taken for an empty file and replaced."""
    first_run = _first_run()
    monkeypatch.delenv("AP_PRIVATE_DIR")
    data = tmp_path / "My Data"
    settings = paths.user_settings_path()
    settings.write_bytes(json.dumps({"data_dir": str(data)}).encode("utf-8-sig"))
    assert paths.read_user_settings() == {"data_dir": str(data)} and paths.private_dir() == data
    assert install.paths.read_user_settings()["data_dir"] == str(data)  # the installer reads it the same way
    monkeypatch.setattr(first_run, "DEFAULT_DATA_DIR", tmp_path / "APCoder")
    assert first_run.choose_data_dir() == data
    paths.write_user_settings(desktop_shortcut=True)
    assert json.loads(settings.read_text(encoding="utf-8"))["data_dir"] == str(data)  # not lost on the next write


def test_first_run_takes_the_data_folder_without_cmds_quotes(tmp_path, monkeypatch):
    """``set AP_PRIVATE_DIR="D:\\AP Data"`` in cmd keeps the quotes in the value: they are not part of the path."""
    monkeypatch.setenv("AP_PRIVATE_DIR", f'"{tmp_path / "AP Data"}"')
    assert _first_run().choose_data_dir() == tmp_path / "AP Data"


def _first_run():
    spec = importlib.util.spec_from_file_location("ap_first_run", ROOT / "scripts" / "first_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_installer_asks_no_cloud_questions(tmp_path, monkeypatch):
    """The offline build reads every invoice on this computer: the installer makes the settings file, asks nothing."""
    monkeypatch.delenv("AP_ALLOW_INTERNET", raising=False)
    monkeypatch.setattr(install, "ROOT", tmp_path)
    (tmp_path / ".env.example").write_text("AP_REVIEWER=\n", encoding="utf-8")
    c = install.Console(assume_yes=False)
    c.ask = lambda *a, **k: pytest.fail(f"asked {a[0]!r}")
    c.yes = lambda *a, **k: pytest.fail(f"asked {a[0]!r}")
    env = install.configure_settings(c, tmp_path / "data")
    assert env == tmp_path / "data" / ".env" and env.exists()


def test_the_installer_stops_an_older_ap_coder_first_and_never_installs_under_a_newer_one(monkeypatch):
    verdicts = iter([("clear", None), ("newer", 8501), ("blocked", 8501)])
    monkeypatch.setattr(install.launch.running, "take_over", lambda *a, **k: next(verdicts))
    assert install.make_way() == ""
    assert "A newer AP Coder is running" in install.make_way()
    assert "close the old AP Coder window" in install.make_way()


# --- Run over an older version, and run again: up to date every time ----------------------------------------------


def _folder(monkeypatch, tmp_path, state, ident, pruned):
    monkeypatch.setattr(install, "ROOT", tmp_path)
    monkeypatch.setattr(install.launch, "load_state", lambda: dict(state))
    monkeypatch.setattr(install.launch, "identity", lambda: dict(ident))
    monkeypatch.setattr(install.launch, "legacy_folders", lambda: [])
    monkeypatch.setattr(install.upgrade, "remember", lambda folders: None)
    monkeypatch.setattr(install.upgrade, "prune_stale", lambda root: (pruned, 2048 if pruned else 0))


def test_run_over_an_older_version_it_removes_what_that_version_left(tmp_path, monkeypatch, capsys):
    """The pilot tester unzipped the new bundle over PR 8's folder and ran the installer: PR 8's own tests (they import
    streamlit) stayed in tests/, so the installer's self-test stopped on 6 errors, and its Streamlit files stayed."""
    _folder(monkeypatch, tmp_path, {"fingerprint": "old", "version": "0.1.0"},
            {"version": "0.2.0", "build": "b", "fingerprint": "new"}, ["tests/test_dashboard_pages.py"])  # fmt: skip
    assert install.bring_up_to_date(install.Console(assume_yes=True))
    out = capsys.readouterr().out
    assert "updating AP Coder from 0.1.0 to 0.2.0" in out
    assert "removed 1 file the older version left in this folder" in out


def test_run_again_it_says_it_is_up_to_date_and_changes_nothing(tmp_path, monkeypatch, capsys):
    ident = {"version": "0.2.0", "build": "b", "fingerprint": "same"}
    _folder(monkeypatch, tmp_path, {**ident, "setup_done": True}, ident, [])
    assert not install.bring_up_to_date(install.Console(assume_yes=True))
    assert "up to date" in capsys.readouterr().out


def test_the_check_runs_when_something_changed_else_its_result_is_said(monkeypatch, capsys):
    """The installer ran the whole test suite on every run (18 minutes on a laptop, and again on a second run with
    nothing changed). It runs the launcher's self-check (the sample invoices, seconds) when something changed, or
    when none passed yet; else it says the last result."""
    state = {"self_check": {"ok": True, "at": "2026-10-12 09:00", "result": "Self-check OK: 10 of 10"}}
    monkeypatch.setattr(install.launch, "load_state", lambda: state)
    monkeypatch.setattr(install.launch, "save_state", lambda s: None)
    monkeypatch.setattr(install, "quiet", lambda cmd: pytest.fail(f"ran {cmd}"))
    monkeypatch.setattr(install, "run", lambda cmd, **kw: None)  # the doctor
    ran = []
    monkeypatch.setattr(install.launch, "self_check", lambda: ran.append(1) or (True, "Self-check OK: 10 of 10 (9 s)"))
    args = install.argparse.Namespace(skip_tests=False, full_tests=False)
    c = install.Console(assume_yes=True)
    install.run_checks(c, args, Path("nowhere/.env"), changed=False)
    assert not ran and "up to date" in capsys.readouterr().out
    install.run_checks(c, args, Path("nowhere/.env"), changed=True)
    assert ran == [1] and state["self_check"]["ok"]
    state["self_check"] = {"ok": False, "at": "x", "result": "FAILED"}
    install.run_checks(c, args, Path("nowhere/.env"), changed=False)
    assert ran == [1, 1]  # not passed yet: run again
