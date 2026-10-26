"""The second security review of server mode (D-109): what an administrator's commands trust, the update bundle's
SHA-256, request sizes, uploads, lockouts, separation of duties, folders, the audit, TLS and sessions."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from ap_coder.core import config
from ap_coder.core.store import Store
from ap_coder.ops import cli, server_update, service

from .test_server_update import FakeServer, _bundle, _folder

ROOT = Path(__file__).resolve().parents[1]
GOOD = "correct horse battery"


# --- 1. The service's account can't reach the administrator's commands through the data folder ------------------------


def test_a_env_file_never_sets_what_decides_which_code_runs(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("PYTHONPATH=C:\\evil\nPIP_INDEX_URL=http://evil\nPYTHONSTARTUP=x.py\nAP_LLM_MODEL=gemma\n")
    for name in ("PYTHONPATH", "PIP_INDEX_URL", "PYTHONSTARTUP", "AP_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    config.Settings.from_env(env)
    assert os.environ.get("AP_LLM_MODEL") == "gemma"  # the settings themselves still load
    assert "PYTHONPATH" not in os.environ and "PIP_INDEX_URL" not in os.environ and "PYTHONSTARTUP" not in os.environ


@pytest.mark.parametrize("argv", [["server-update", "--back", "--yes"], ["service", "status"]])
def test_an_administrators_command_reads_no_env_file(tmp_path, monkeypatch, argv):
    data = tmp_path / "data"
    data.mkdir()
    (data / ".env").write_text("AP_TLS_CERT=C:\\planted.pem\nAZURE_OPENAI_ENDPOINT=https://x\n")
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    for name in ("AP_TLS_CERT", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    seen = {}
    monkeypatch.setattr(server_update, "main", lambda args: seen.update(os.environ) or 0)
    monkeypatch.setattr(service, "main", lambda args: seen.update(os.environ) or 0)
    assert cli.main(argv) == 0
    assert "AP_TLS_CERT" not in seen and "AZURE_OPENAI_ENDPOINT" not in seen


def test_the_new_folders_pythons_get_a_clean_explicit_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "evil"))
    monkeypatch.setenv("PIP_FIND_LINKS", str(tmp_path / "evil"))
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret")
    runs = []

    def run(cmd, **kwargs):
        runs.append((cmd, kwargs["env"]))
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(server_update.subprocess, "run", run)
    folder = _folder(tmp_path / "APProcessor-0.4.0", "0.4.0")
    srv = server_update.Server(tmp_path / "data", service.ServerSettings(), say=lambda m: None)
    srv.set_up(folder)
    srv.migrate(folder, tmp_path / "data" / "ap_coder.db")
    srv.self_check(folder)
    assert len(runs) == 3
    for cmd, env in runs:
        assert cmd[1:3] == ["-E", "-s"], cmd  # no PYTHON* variable, no user site-packages
        assert not [k for k in env if k.upper().startswith(("PYTHON", "PIP_"))], env
        assert "AZURE_OPENAI_API_KEY" not in env
        assert env["AP_PRIVATE_DIR"] == str(tmp_path / "data") and env["AP_SERVER_MODE"] == "1"


@pytest.fixture
def server(tmp_path, monkeypatch):
    data = tmp_path / "APCoderData"
    data.mkdir()
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    current = _folder(tmp_path / "apps" / "APProcessor", "0.3.0")
    service.write_config(data, folder=str(current), insecure_http=True)
    service.write_runner(data, current)
    monkeypatch.setattr(server_update, "running_folder", lambda: current)
    db = data / "ap_coder.db"
    Store(db)
    return {"data": data, "current": current, "db": db, "tmp": tmp_path}


def test_server_update_refuses_a_folder_in_server_json_that_is_not_the_one_it_runs_from(server, capsys):
    planted = _folder(server["tmp"] / "Users" / "Public" / "APProcessor", "0.3.0")  # a folder the service could write
    service.write_config(server["data"], folder=str(planted))
    bundle = _bundle(server["tmp"])
    fake = FakeServer()
    sha = server_update.file_sha256(bundle)
    assert server_update.update(bundle, server["data"], server["db"], server=fake, sha256=sha) == 1
    assert fake.calls == [] and not (planted.parent / "APProcessor-0.4.0").exists()
    assert "this command runs from" in capsys.readouterr().err


def test_going_back_refuses_a_previous_folder_that_is_not_beside_this_one(server, capsys):
    planted = _folder(server["tmp"] / "Users" / "Public" / "APProcessor-0.2.0", "0.2.0")
    backup = Store(server["db"]).backup_now("before-update-0.3.0")
    service.write_config(server["data"], previous_folder=str(planted), pre_update_backup=str(backup))
    fake = FakeServer()
    assert server_update.go_back(server["data"], server["db"], server=fake, yes=True) == 1
    assert fake.calls == []
    assert "not beside this AP Coder folder" in capsys.readouterr().err


# --- 2. The update bundle's SHA-256, given apart from it --------------------------------------------------------------


def test_server_update_refuses_a_bundle_without_its_sha256(server, capsys):
    fake = FakeServer()
    assert server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls == [] and "--sha256" in capsys.readouterr().err
    assert not (server["current"].parent / "APProcessor-0.4.0").exists()


def test_server_update_refuses_a_bundle_whose_sha256_is_not_the_one_given(server, capsys):
    fake = FakeServer()
    code = server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=fake, sha256="ab" * 32)
    assert code == 1 and fake.calls == [] and "not the bundle that was built" in capsys.readouterr().err


def test_no_hash_check_warns_and_is_recorded(server, capsys):
    fake = FakeServer()
    code = server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=fake, hash_check=False)
    assert code == 0
    assert "WARNING" in capsys.readouterr().err
    assert service.read_config(server["data"])["history"][-1]["bundle_hash"].startswith("not checked")


def test_the_command_line_passes_the_sha256(server, monkeypatch):
    seen = {}
    monkeypatch.setattr(server_update, "update", lambda bundle, data, db, **k: seen.update(k) or 0)
    assert cli.main(["server-update", "b.zip", "--sha256", "ab" * 32]) == 0
    assert seen["sha256"] == "ab" * 32 and seen["hash_check"] is True
    assert cli.main(["server-update", "b.zip", "--no-hash-check"]) == 0
    assert seen["hash_check"] is False


def test_the_bundle_builder_writes_the_zips_sha256_beside_it(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_offline_bundle

    zip_path = _bundle(tmp_path)
    digest, path = build_offline_bundle.write_checksum(zip_path)
    assert path == tmp_path / f"{zip_path.name}.sha256"
    assert path.read_text(encoding="utf-8") == f"{digest}  {zip_path.name}\n"
    assert digest == server_update.file_sha256(zip_path)
