"""AP Coder as a Windows service on the company server (D-100): ``python -m ap_coder service ...``.

On Linux (and macOS) only what would be run is checked: the scheduled task's XML, the schtasks commands, the runner
file and the server settings, as text. The service host (``service run``) is plain Python and runs everywhere. On
GitHub's Windows runner (an administrator) the task is really installed and removed.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ap_coder.ops import cli, service

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "APCoderData"
    folder.mkdir()
    monkeypatch.setenv("AP_PRIVATE_DIR", str(folder))
    for name in (*service.ENV_SETTINGS.values(), "AP_DASHBOARD_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    return folder


@pytest.fixture
def commands(monkeypatch):
    """What would be run on Windows, recorded instead (and the actions allowed on this computer)."""
    ran: list[list[str]] = []

    def run(cmd, **kwargs):
        ran.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0, "Status: Ready\n", "")

    monkeypatch.setattr(service, "WINDOWS", True)
    monkeypatch.setattr(service, "run_command", run)
    monkeypatch.setattr(service, "dashboard_stopped", lambda data, settings, wait=20.0: True)
    return ran


def main(*argv: str) -> int:
    return cli.main(["service", *argv])


# --- The settings: server.json, or environment variables ----------------------------------------------------------


def test_the_settings_come_from_server_json_and_the_environment_wins(data, monkeypatch):
    assert service.settings(data).port == 8501 and service.settings(data).account == "APCoderService"
    (data / "server.json").write_text(json.dumps({"port": 8443, "certfile": "C:\\certs\\ap.pem",
                                                  "allowed_hosts": ["apcoder.company.local"]}))  # fmt: skip
    found = service.settings(data)
    assert (found.port, found.certfile, found.allowed_hosts) == (8443, "C:\\certs\\ap.pem", ("apcoder.company.local",))
    monkeypatch.setenv("AP_SERVER_PORT", "9443")
    monkeypatch.setenv("AP_ALLOWED_HOSTS", "ap.company.local, apcoder")
    found = service.settings(data)
    assert found.port == 9443 and found.allowed_hosts == ("ap.company.local", "apcoder")


def test_a_damaged_server_json_is_said_plainly_not_a_crash(data, capsys):
    (data / "server.json").write_text("{not json")
    assert main("install", "--dry-run", "--insecure-http") == 1
    assert "server.json" in capsys.readouterr().err


def test_https_needs_a_certificate_and_its_key(data, capsys):
    assert main("install", "--dry-run") == 1
    assert "--certfile" in capsys.readouterr().err
    cert = data / "ap.pem"
    cert.write_text("cert")
    assert main("install", "--dry-run", "--certfile", str(cert)) == 1
    assert "--keyfile" in capsys.readouterr().err


# --- What install would do, as text ---------------------------------------------------------------------------------


def test_install_dry_run_shows_every_command_and_changes_nothing(data, capsys):
    assert main("install", "--dry-run", "--insecure-http", "--allowed-hosts", "apcoder.company.local") == 0
    out = capsys.readouterr().out
    assert 'schtasks /create /tn "AP Coder" /xml' in out and "/ru APCoderService" in out and "/f" in out
    assert "/rp" not in out  # schtasks asks for the account's password itself: never on a command line
    assert 'schtasks /run /tn "AP Coder"' in out
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in out  # the whole task, as it would be written
    assert "Nothing was changed (dry run)" in out
    assert not (data / "server.json").exists() and not (data / "service").exists()


def test_the_task_starts_at_boot_runs_for_ever_and_comes_back(data):
    s = service.settings(data, port=8443, certfile="c.pem", keyfile="k.pem")
    task = ET.fromstring(service.task_xml(s, Path("D:/APCoderData/service/run.cmd"), Path("C:/APCoder/app")))
    assert task.find("t:Triggers/t:BootTrigger/t:Enabled", NS).text == "true"
    repeat = task.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", NS).text
    assert repeat == "PT5M"  # started again within 5 minutes if the service host itself ended
    settings = task.find("t:Settings", NS)
    assert settings.find("t:ExecutionTimeLimit", NS).text == "PT0S"  # (the default is to stop it after 3 days)
    assert settings.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert settings.find("t:Priority", NS).text == "5"  # normal priority (a task's default, 7, is below normal)
    assert settings.find("t:DisallowStartIfOnBatteries", NS).text == "false"
    assert settings.find("t:RestartOnFailure/t:Count", NS).text == "999"
    principal = task.find("t:Principals/t:Principal", NS)
    assert principal.find("t:UserId", NS).text == "APCoderService"
    assert (
        principal.find("t:LogonType", NS).text == "Password"
        and principal.find("t:RunLevel", NS).text == "LeastPrivilege"
    )
    action = task.find("t:Actions/t:Exec", NS)
    assert action.find("t:Command", NS).text.endswith("run.cmd")


@pytest.mark.parametrize(("account", "sid"), [("SYSTEM", "S-1-5-18"), ("NT AUTHORITY\\LOCAL SERVICE", "S-1-5-19"),
                                              ("network service", "S-1-5-20")])  # fmt: skip
def test_a_built_in_account_needs_no_password(data, account, sid):
    s = service.settings(data, account=account, insecure_http=True)
    task = ET.fromstring(service.task_xml(s, Path("run.cmd"), Path("app")))
    assert task.find("t:Principals/t:Principal/t:UserId", NS).text == sid
    assert task.find("t:Principals/t:Principal/t:LogonType", NS) is None
    assert "/ru" not in service.create_command(s, Path("task.xml"))


def test_the_runner_points_at_the_folder_and_the_data_folder(data, tmp_path):
    folder = tmp_path / "APProcessor 100%"
    text = service.runner_text(folder, data)
    assert f'set "AP_PRIVATE_DIR={data}"' in text and 'set "AP_SERVER_MODE=1"' in text
    assert "APProcessor 100%%" in text  # a % in a path is doubled in a .cmd file
    python = "Scripts\\python.exe" if os.name == "nt" else "Scripts/python.exe"
    assert python.replace("\\", "/") in text.replace("\\", "/") and "-m ap_coder service run" in text


def test_the_dashboard_is_started_with_the_server_settings(data):
    s = service.settings(data, port=8443, certfile="c.pem", keyfile="k.pem", proxy="10.0.0.5",
                         allowed_hosts=("apcoder.company.local",))  # fmt: skip
    cmd = service.serve_command(s, "python")
    assert cmd[:4] == ["python", "-m", "ap_coder", "serve"] and "--server" in cmd and "--no-browser" in cmd
    assert cmd[cmd.index("--port") + 1] == "8443" and cmd[cmd.index("--host") + 1] == "0.0.0.0"
    assert cmd[cmd.index("--certfile") + 1] == "c.pem" and cmd[cmd.index("--keyfile") + 1] == "k.pem"
    assert cmd[cmd.index("--proxy") + 1] == "10.0.0.5" and "--insecure-http" not in cmd
    env = service.serve_env(s, data)
    assert env["AP_ALLOWED_HOSTS"] == "apcoder.company.local" and env["AP_SERVER_MODE"] == "1"
    assert env["AP_PRIVATE_DIR"] == str(data)
    assert "--insecure-http" in service.serve_command(service.settings(data, insecure_http=True), "python")


# --- install, start, stop, remove (with the commands recorded) -----------------------------------------------------


def test_install_writes_the_settings_the_runner_and_the_task_then_starts_it(data, commands, capsys):
    cert, key = data / "ap.pem", data / "ap.key"
    cert.write_text("c")
    key.write_text("k")
    assert main("install", "--certfile", str(cert), "--keyfile", str(key), "--port", "8443",
                "--allowed-hosts", "apcoder.company.local") == 0  # fmt: skip
    saved = json.loads((data / "server.json").read_text(encoding="utf-8"))
    assert saved["port"] == 8443 and saved["allowed_hosts"] == ["apcoder.company.local"]
    assert saved["folder"] == str(service.PROJECT_DIR) and saved["account"] == "APCoderService"
    assert (data / "service" / "run.cmd").is_file()
    xml = (data / "service" / "ap-coder-task.xml").read_bytes()
    assert xml.startswith(b"\xff\xfe")  # UTF-16 with its byte-order mark, as schtasks /xml reads it
    assert commands[0][:2] == ["schtasks", "/create"] and commands[-1][:2] == ["schtasks", "/run"]
    assert "Next steps" in capsys.readouterr().out
    # Installed again with nothing on the command line: the saved settings are kept.
    assert main("install", "--no-start") == 0
    assert json.loads((data / "server.json").read_text(encoding="utf-8"))["port"] == 8443


def test_stop_keeps_the_host_from_starting_it_again_and_start_lets_it(data, commands):
    (data / "server.json").write_text(json.dumps({"insecure_http": True}))
    assert main("stop") == 0
    assert (data / "service" / "stopped").exists() and ["schtasks", "/end", "/tn", "AP Coder"] in commands
    assert main("start") == 0
    assert not (data / "service" / "stopped").exists() and commands[-1] == ["schtasks", "/run", "/tn", "AP Coder"]


def test_remove_stops_and_deletes_the_task_and_keeps_the_settings_and_logs(data, commands):
    (data / "server.json").write_text(json.dumps({"insecure_http": True}))
    assert main("remove") == 0
    assert commands[-1] == ["schtasks", "/delete", "/tn", "AP Coder", "/f"]
    assert (data / "server.json").exists()


def test_off_windows_the_service_commands_say_so(data, monkeypatch, capsys):
    monkeypatch.setattr(service, "WINDOWS", False)
    assert main("status") == 2
    assert "Windows" in capsys.readouterr().err


# --- The service host: keeps the dashboard running, logs to the data folder -----------------------------------------


class FakeChild:
    def __init__(self, code: int, lines: list[str], on_exit=None) -> None:
        self.code, self.stdout, self.on_exit, self.pid = code, iter(lines), on_exit, 4242

    def wait(self, timeout=None):
        if self.on_exit:
            self.on_exit()
        return self.code


def test_the_host_starts_the_dashboard_again_when_it_stops_until_it_is_stopped(data):
    (data / "server.json").write_text(json.dumps({"insecure_http": True, "port": 8601}))
    started, slept = [], []

    def popen(cmd, **kwargs):
        started.append(cmd)
        stop = (lambda: service.stop_flag(data).write_text("")) if len(started) == 3 else None
        return FakeChild(1 if len(started) < 3 else 0, [f"line {len(started)}\n"], stop)

    assert service.run_host(data, popen=popen, sleep=slept.append, clock=lambda: 0.0) == 0
    assert len(started) == 3 and "--port" in started[0] and "8601" in started[0]
    assert sum(slept) >= 15  # 5 s, then 10 s: a dashboard that keeps failing is not restarted in a tight loop
    log = (data / "logs" / "service.log").read_text(encoding="utf-8")
    assert "dashboard: line 1" in log and "exit code 1" in log and "stopped" in log
    assert not service.host_record(data).exists()


def test_the_host_does_nothing_while_the_service_is_stopped(data):
    service.stop_flag(data).parent.mkdir(parents=True)
    service.stop_flag(data).write_text("")
    assert service.run_host(data, popen=lambda *a, **k: pytest.fail("started"), sleep=lambda s: None) == 0


def test_a_second_host_steps_aside_for_the_one_running(data, monkeypatch):
    service.host_record(data).parent.mkdir(parents=True)
    service.host_record(data).write_text(json.dumps({"pid": 999_999}))
    monkeypatch.setattr(service, "process_alive", lambda pid: pid == 999_999)
    assert service.run_host(data, popen=lambda *a, **k: pytest.fail("started"), sleep=lambda s: None) == 0


# --- On Windows (GitHub's runner: an administrator), the task really installed and removed --------------------------


def _admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


@pytest.mark.skipif(os.name != "nt" or not os.environ.get("GITHUB_ACTIONS") or not _admin(),
                    reason="installs a real scheduled task: Windows CI (an administrator) only")  # fmt: skip
def test_on_windows_the_task_is_installed_and_removed(data):
    name = f"AP Coder test {os.getpid()}"
    base = [sys.executable, "-m", "ap_coder", "service"]
    common = ["--task-name", name, "--account", "SYSTEM"]
    try:
        done = subprocess.run([*base, "install", *common, "--insecure-http", "--no-start"], capture_output=True,
                              text=True, timeout=120)  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        query = subprocess.run(["schtasks", "/query", "/tn", name, "/xml"], capture_output=True, text=True, timeout=60)
        assert query.returncode == 0 and "PT0S" in query.stdout and "S-1-5-18" in query.stdout
        status = subprocess.run([*base, "status", "--task-name", name], capture_output=True, text=True, timeout=60)
        assert status.returncode == 0, status.stdout + status.stderr
    finally:
        subprocess.run([*base, "remove", "--task-name", name], capture_output=True, text=True, timeout=120)
    gone = subprocess.run(["schtasks", "/query", "/tn", name], capture_output=True, text=True, timeout=60)
    assert gone.returncode != 0
