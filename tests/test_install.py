"""The installer can be run again and again: it updates settings in place and never duplicates anything."""

import importlib.util
import json
from pathlib import Path

import pytest

from ap_coder import paths
from ap_coder.config import Settings

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
    assert "AZURE_OPENAI_ENDPOINT" not in values and values["AZURE_OPENAI_DEPLOYMENT"] == "gpt-4o"


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
    monkeypatch.setattr(install.launch, "ROOT", tmp_path)  # no wheelhouse here
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
    monkeypatch.setattr(install, "USER_SETTINGS", settings)
    assert install.read_user_settings()["data_dir"] == str(data)
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
