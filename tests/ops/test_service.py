"""AP Coder as a Windows service on the company server (D-100): ``python -m ap_coder service ...``.

On Linux (and macOS) only what would be run is checked: the scheduled task's XML, the schtasks commands, the runner
file and the server settings, as text. The service host (``service run``) is plain Python and runs everywhere. On
GitHub's Windows runner (an administrator) the task is really installed and removed.
"""

from __future__ import annotations

import ctypes
import functools
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath

import pytest

from ap_coder.core import paths
from ap_coder.ops import cli, service

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
REAL_DASHBOARD_STOPPED = service.dashboard_stopped  # (the `commands` fixture replaces it)


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "APCoderData"
    folder.mkdir()
    monkeypatch.setenv("AP_PRIVATE_DIR", str(folder))
    monkeypatch.setattr(service, "LINUX", False)  # Windows' task, shown anywhere (Linux's unit: test_service_linux.py)
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
    task = ET.fromstring(service.task_xml(s, Path("D:/APCoderData/service/run.cmd")))
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


def test_the_task_starts_in_run_cmds_folder_never_in_a_version_folder(data):
    """The task started in the AP Coder folder it was installed from, which no update changes: once server-update's
    "two versions back" was deleted, Task Scheduler could not start it (error 267: the folder is gone) (B-1961). It
    starts in run.cmd's own folder (the data folder's), and run.cmd goes to the version's folder (cd /d)."""
    s = service.settings(data, insecure_http=True)
    runner = service.runner_path(data)
    task = ET.fromstring(service.task_xml(s, runner))
    assert task.find("t:Actions/t:Exec/t:WorkingDirectory", NS).text == str(runner.parent)
    assert f'cd /d "{service.PROJECT_DIR}"' in service.runner_text(service.PROJECT_DIR, data)


@pytest.mark.parametrize(("account", "sid"), [("SYSTEM", "S-1-5-18"), ("NT AUTHORITY\\LOCAL SERVICE", "S-1-5-19"),
                                              ("network service", "S-1-5-20")])  # fmt: skip
def test_a_built_in_account_needs_no_password(data, account, sid):
    s = service.settings(data, account=account, insecure_http=True)
    task = ET.fromstring(service.task_xml(s, Path("run.cmd")))
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


def as_cmd_reads_it(raw: bytes, code_page: str = "cp850") -> str:
    """The text cmd.exe works with: the file's bytes in the console code page, unless a `chcp 65001` line comes
    before the line (then UTF-8)."""
    out, page = [], code_page
    for line in raw.split(b"\r\n"):
        text = line.decode(page, errors="replace")
        out.append(text)
        if text.strip().lower().startswith("chcp 65001"):
            page = "utf-8"
    return "\r\n".join(out)


def test_run_cmd_keeps_an_accented_data_folder_and_code_folder():
    """B-2570: run.cmd is written as UTF-8, but cmd.exe reads a batch file in the console's OEM code page (437 or 850
    on an English or French Canadian Windows, under Task Scheduler too): "D:\\Données AP" reached `set` and `cd /d`
    as "D:\\Donn├®es AP". Its second line is `chcp 65001 >nul`, everything before it ASCII."""
    data, folder = PureWindowsPath(r"D:\Données AP"), PureWindowsPath(r"C:\APCoder\Comptabilité\APProcessor")
    raw = service.runner_text(folder, data).encode("utf-8")  # what write_runner writes
    seen = as_cmd_reads_it(raw)
    assert 'set "AP_PRIVATE_DIR=D:\\Données AP"' in seen, seen
    assert 'cd /d "C:\\APCoder\\Comptabilité\\APProcessor"' in seen, seen
    assert raw.split(b"\r\n")[:2] == [b"@echo off", b"chcp 65001 >nul"]


class PowerCut(OSError):
    """The computer stopped here."""


@pytest.mark.parametrize("which", ["run.cmd", "server.json"])
def test_run_cmd_and_server_json_are_never_left_cut_short(data, tmp_path, monkeypatch, which):
    """B-1935: run.cmd was written in place: a power cut while server-update (or its recovery at the host's start)
    rewrote it could leave it empty or cut short, the task then started nothing, and no host started to deal with the
    update cut short. It, and server.json, are now written whole beside, flushed (fsync), then renamed over: cut
    before the rename, the old one is there, whole, and no part of the new one is left."""
    old, new = tmp_path / "APProcessor", tmp_path / "APProcessor-0.5.0"

    def write(folder):
        if which == "run.cmd":
            return service.write_runner(data, folder)
        return service.write_config(data, folder=str(folder))

    path = write(old)
    before = path.read_bytes()
    done: list[str] = []
    real_fsync = os.fsync

    def cut(src, dst):
        done.append("rename")
        raise PowerCut("the power went off")

    with monkeypatch.context() as power:
        power.setattr(os, "fsync", lambda fd: (done.append("fsync"), real_fsync(fd))[1])
        power.setattr(os, "replace", cut)
        with pytest.raises(PowerCut):
            write(new)
    assert path.read_bytes() == before  # the old one, whole: the task starts it, and its host recovers the update
    assert done[:2] == ["fsync", "rename"]  # the new one on the disk before it takes the name
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".part")] == []
    write(new)
    if which == "run.cmd":
        assert str(new) in path.read_text(encoding="utf-8")
    else:
        assert service.read_config(data)["folder"] == str(new)


@pytest.mark.skipif(os.name == "nt", reason="a link planted in a folder: POSIX")
def test_a_whole_write_never_follows_a_link_left_at_its_temporary_name(data, tmp_path):
    """B-1964: ``write_whole`` opened its temporary file with ``open(part, "wb")``, which follows a link already at
    that name. Run as root (a Mac's ``sudo service install``) beside files the service account may write, it would
    have written root's content through a link into any file. A file already at that name is removed and the new one
    made with O_EXCL (never through a link): the file the link named stays as it was."""
    other = tmp_path / "elsewhere.txt"
    other.write_text("as it was", encoding="utf-8")
    path = data / "service" / "run.cmd"
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f".{path.name}.{os.getpid()}.part")
    part.symlink_to(other)
    service.write_whole(path, b"the new runner")
    assert other.read_text(encoding="utf-8") == "as it was"
    assert path.read_bytes() == b"the new runner" and not path.is_symlink() and not part.exists()


def test_server_json_is_replaced_even_while_another_process_has_it_open(data, monkeypatch):
    """Windows refuses to rename over a file another process has open without delete sharing (as Python opens files;
    an antivirus; a monitor's /readyz reading server.json): server-update's record failed on that PermissionError
    (B-1962). The rename is tried again for a moment on Windows; held longer, it is said, and nothing is left
    beside it. Elsewhere such a rename never fails: no wait."""
    service.write_config(data, folder="before")
    real, tries, waited = os.replace, [], []

    def busy(src, dst):
        tries.append(dst)
        if len(tries) < 3:
            raise PermissionError(13, "The process cannot access the file: another process uses it", str(dst))
        return real(src, dst)

    monkeypatch.setattr(service, "WINDOWS", True)
    monkeypatch.setattr(service.time, "sleep", waited.append)
    monkeypatch.setattr(os, "replace", busy)
    service.write_config(data, folder="after")
    assert service.read_config(data)["folder"] == "after" and len(tries) == 3 and len(waited) == 2
    monkeypatch.setattr(os, "replace", lambda src, dst: (_ for _ in ()).throw(PermissionError(13, "in use", dst)))
    with pytest.raises(PermissionError):
        service.write_config(data, folder="never")
    assert service.read_config(data)["folder"] == "after" and sum(waited) <= 5
    assert [p.name for p in data.iterdir() if p.name.endswith(".part")] == []
    monkeypatch.setattr(service, "WINDOWS", False)
    waited.clear()
    with pytest.raises(PermissionError):
        service.write_config(data, folder="never")
    assert waited == []


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


def test_the_data_folder_never_picks_the_task_its_account_its_folder_or_a_process_to_end(
    data, commands, monkeypatch, capsys
):
    """B-1500: server.json, host.json and .ap_coder_server.json are in the data folder, which the service's account
    can write. The administrator's install, stop and remove took the task's name, account and folder from server.json
    (installed again, the task ran another folder's python.exe as SYSTEM) and ended the processes those files named."""
    alive = {4242, 4343}
    monkeypatch.setattr(service, "process_alive", lambda pid: pid in alive)
    monkeypatch.setattr(service, "process_image", lambda pid: "C:\\Program Files\\Antivirus\\agent.exe", raising=False)
    monkeypatch.setattr(service, "_local", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no dashboard")))
    monkeypatch.setattr(service, "dashboard_stopped", functools.partial(REAL_DASHBOARD_STOPPED, wait=0))
    elsewhere = data / "theirs"
    task = "\\Microsoft\\Windows\\Defrag\\ScheduledDefrag"
    saved = {"insecure_http": True, "account": "SYSTEM", "folder": str(elsewhere), "task_name": task}
    (data / "server.json").write_text(json.dumps(saved))
    (data / "service").mkdir()
    (data / "service" / "host.json").write_text(json.dumps({"pid": 4242}))
    (data / ".ap_coder_server.json").write_text(json.dumps({"pid": 4343, "port": 8501, "token": "t"}))
    main("install", "--no-start")
    xml = (data / "service" / "ap-coder-task.xml").read_text(encoding="utf-16")
    assert "<UserId>APCoderService</UserId>" in xml and "S-1-5-18" not in xml
    runner = (data / "service" / "run.cmd").read_text(encoding="utf-8")
    assert str(elsewhere) not in runner and str(service.PROJECT_DIR) in runner
    assert ["schtasks", "/create", "/tn", "AP Coder"] == commands[-1][:4] and "/ru" in commands[-1]
    main("stop")
    main("remove")
    assert ["schtasks", "/delete", "/tn", "AP Coder", "/f"] in commands
    assert not [cmd for cmd in commands if cmd[0] == "taskkill" or "Defrag" in " ".join(cmd)]
    # The service's own Python (its .venv's, or the Python that .venv was made with) is ended when it won't stop; the
    # host only while it runs, holding the host lock (B-1590).
    monkeypatch.setattr(service, "process_image", lambda pid: str(service.folder_python(service.PROJECT_DIR)))
    held = paths.try_lock(service.host_lock(data))
    try:
        main("stop")
    finally:
        held.close()
    assert {"4242", "4343"} <= {cmd[2] for cmd in commands if cmd[0] == "taskkill"}
    base = data / "Python312"
    (elsewhere / ".venv").mkdir(parents=True)
    (elsewhere / ".venv" / "pyvenv.cfg").write_text(f"home = {base}\nversion = 3.12.7\n", encoding="utf-8")
    monkeypatch.setattr(service, "process_image", lambda pid: str(base / "python.exe"))
    assert service.runs_python_of(4242, elsewhere) and not service.runs_python_of(4242, data)
    # A built-in account, another task or folder: only when named on the command line.
    capsys.readouterr()
    assert main("install", "--dry-run", "--account", "SYSTEM", "--task-name", "AP Coder test") == 0
    assert "<UserId>S-1-5-18</UserId>" in capsys.readouterr().out


def test_off_windows_the_service_commands_say_so(data, monkeypatch, capsys):
    monkeypatch.setattr(service, "WINDOWS", False)
    monkeypatch.setattr(service, "MACOS", False)  # (a Mac and Linux have their own: test_service_macos.py, _linux.py)
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
    """The running host holds the operating system's lock on service/host.lock (B-1585): it, not host.json, says a
    host runs. On Windows the second one ends with 0, as before (the task comes back every 5 minutes); on a Mac with
    HOST_AGAIN, not 0, so launchd's KeepAlive starts it again once the other has ended (D-186)."""
    held = paths.try_lock(service.host_lock(data))
    assert held is not None
    try:
        for windows, code in ((True, 0), (False, service.HOST_AGAIN)):
            monkeypatch.setattr(service, "WINDOWS", windows)
            assert service.run_host(data, popen=lambda *a, **k: pytest.fail("started"), sleep=lambda s: None) == code
    finally:
        held.close()
    assert service.HOST_AGAIN != 0


def test_a_stale_host_record_never_keeps_the_service_down(data):
    """B-1585: after a reboot or a kill, host.json names a process number another program may have since (likely on
    a Mac: numbers start again low at boot). The host took that for a host running and exited, starting nothing, at
    every retry of the task: the service stayed down. Here host.json names a live process that is no host."""
    (data / "server.json").write_text(json.dumps({"insecure_http": True}))
    service.host_record(data).parent.mkdir(parents=True)
    service.host_record(data).write_text(json.dumps({"pid": os.getppid(), "started": "2026-11-01T08:00:00"}))
    started = []

    def popen(cmd, **kwargs):
        started.append(cmd)
        return FakeChild(0, [], lambda: service.stop_flag(data).write_text(""))

    assert service.run_host(data, popen=popen, sleep=lambda s: None) == 0
    assert len(started) == 1
    again = paths.try_lock(service.host_lock(data))  # released once the host ends: the next one can start
    assert again is not None
    again.close()


def test_status_and_install_tell_a_running_host_by_its_lock_not_host_json(data, commands, monkeypatch, capsys):
    """B-1588: status said "Service host: running" when host.json named a live process (a number reused after a
    reboot), and install then stopped the service first: with --no-start the service stayed stopped. The lock the
    running host holds (B-1585) says it."""
    (data / "server.json").write_text(json.dumps({"insecure_http": True}))
    service.host_record(data).parent.mkdir(parents=True)
    service.host_record(data).write_text(json.dumps({"pid": 4242, "started": "2026-11-01T08:00:00"}))
    monkeypatch.setattr(service, "process_alive", lambda pid: pid == 4242)  # the number is another program's now
    monkeypatch.setattr(service, "_local", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no dashboard")))
    assert main("status") == 0
    assert "Service host: not running" in capsys.readouterr().out
    assert main("install", "--no-start") == 0
    assert not service.stop_flag(data).exists() and ["schtasks", "/end", "/tn", "AP Coder"] not in commands
    held = paths.try_lock(service.host_lock(data))  # a host running
    try:
        assert main("status") == 0
        assert "Service host: running since 2026-11-01T08:00:00 (process 4242" in capsys.readouterr().out
        assert main("install", "--no-start") == 0  # installed again while it runs: stopped, to apply the settings
        assert service.stop_flag(data).exists() and ["schtasks", "/end", "/tn", "AP Coder"] in commands
    finally:
        held.close()


def test_status_json_says_the_task_the_host_and_the_dashboard(data, commands, monkeypatch, capsys):
    """``service status --json`` (D-186), for a monitoring script: whether it is installed and runs, the host's
    process, version and folder, the address, and the dashboard's /healthz answer; the plain words stay the default."""
    (data / "server.json").write_text(json.dumps({"insecure_http": True, "allowed_hosts": ["apcoder.company.local"]}))
    health = {"ok": True, "app": "ap-coder", "version": "1.4.0", "folder": "C:\\APCoder\\APProcessor", "port": 8501}
    monkeypatch.setattr(service, "dashboard_health", lambda data, s, timeout=3.0: health)
    service.host_record(data).parent.mkdir(parents=True)
    service.host_record(data).write_text(json.dumps({"pid": 4242, "started": "2026-11-06T08:00:00", "version": "1.4.0",
                                                     "folder": "C:\\APCoder\\APProcessor"}))  # fmt: skip
    held = paths.try_lock(service.host_lock(data))
    try:
        assert main("status", "--json") == 0
    finally:
        held.close()
    found = json.loads(capsys.readouterr().out)
    assert found["platform"] == "windows" and found["installed"] is True and found["state"] == "Ready"
    assert found["task"] == "AP Coder" and found["running"] is True and found["pid"] == 4242
    assert (found["version"], found["folder"]) == ("1.4.0", "C:\\APCoder\\APProcessor")
    assert found["address"] == "http://apcoder.company.local:8501" and found["answering"] is True
    assert found["health"] == health and found["stopped_on_purpose"] is False
    assert commands[-1] == ["schtasks", "/query", "/tn", "AP Coder", "/v", "/fo", "LIST"]
    monkeypatch.setattr(service, "run_command", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, "", "no"))
    monkeypatch.setattr(service, "dashboard_health", lambda data, s, timeout=3.0: None)
    assert main("status", "--json") == 1
    found = json.loads(capsys.readouterr().out)
    assert found["installed"] is False and found["running"] is False and found["health"] is None
    assert main("status") == 1 and "is not installed" in capsys.readouterr().out  # the words, as before


def test_stop_ends_only_the_running_host_never_a_process_a_stale_record_names(data, commands, monkeypatch):
    """B-1590: stop ended the process host.json named whenever it ran the AP Coder folder's Python. A record left by
    a host that was killed may name a number an AP Coder watch has since: stop ended that watch mid-invoice. The host
    is ended only while it holds the host lock (it writes host.json as soon as it holds it)."""
    (data / "server.json").write_text(json.dumps({"insecure_http": True}))
    service.host_record(data).parent.mkdir(parents=True)
    service.host_record(data).write_text(json.dumps({"pid": 4242}))
    monkeypatch.setattr(service, "process_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(service, "process_image", lambda pid: str(service.folder_python(service.PROJECT_DIR)))
    assert main("stop") == 0  # no host runs: 4242 is the watch's now
    assert not [cmd for cmd in commands if cmd[0] == "taskkill"]
    assert service.stop_flag(data).exists() and ["schtasks", "/end", "/tn", "AP Coder"] in commands
    held = paths.try_lock(service.host_lock(data))  # the host runs, and won't stop by itself
    try:
        assert main("stop") == 0
    finally:
        held.close()
    assert ["taskkill", "/PID", "4242", "/T", "/F"] in commands


def test_stop_reads_the_host_record_once_the_lock_says_a_host_runs(data, commands, monkeypatch):
    """B-1677: stop read host.json before it asked whether a host holds the lock. A record left by a host that was
    killed (its number an AP Coder watch's since) was read, then a new host took the lock and wrote its own: stop
    ended the watch. The record is read once the lock says a host runs: the one that holds it wrote it."""
    (data / "server.json").write_text(json.dumps({"insecure_http": True}))
    record = service.host_record(data)
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"pid": 4242}))  # the killed host's; 4242 is a watch's now

    def a_host_starts_meanwhile(folder):  # it takes the lock and writes host.json at once
        record.write_text(json.dumps({"pid": 5151}))
        return True

    monkeypatch.setattr(service, "host_running", a_host_starts_meanwhile)
    monkeypatch.setattr(service, "process_alive", lambda pid: pid in (4242, 5151))
    monkeypatch.setattr(service, "process_image", lambda pid: str(service.folder_python(service.PROJECT_DIR)))
    assert main("stop") == 0
    assert [cmd for cmd in commands if cmd[0] == "taskkill"] == [["taskkill", "/PID", "5151", "/T", "/F"]]


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


def test_a_backup_model_server_in_server_json_reaches_the_dashboard(data):
    """D-180: server.json's ``llm_backup_url`` and ``page_reader_backup_url`` are given to the dashboard (whose
    environment wins over the .env), as AP_LLM_BACKUP_URL and AP_PAGE_READER_BACKUP_URL."""
    service.write_config(data, llm_backup_url=" http://macmini.local:1234/v1 ")
    s = service.settings(data, env={})
    assert s.llm_backup_url == "http://macmini.local:1234/v1" and s.page_reader_backup_url == ""
    env = service.serve_env(s, data)
    assert env["AP_LLM_BACKUP_URL"] == "http://macmini.local:1234/v1" and "AP_PAGE_READER_BACKUP_URL" not in env
    given = service.settings(data, env={"AP_PAGE_READER_BACKUP_URL": "http://macmini.local:8081/v1"})
    assert service.serve_env(given, data)["AP_PAGE_READER_BACKUP_URL"] == "http://macmini.local:8081/v1"
    assert "AP_LLM_BACKUP_URL" not in service.serve_env(service.settings(data, env={}, llm_backup_url=""), data)


def test_server_jsons_backup_model_server_wins_over_the_data_folders_env(data):
    """B-1873: ``service run`` reads the data folder's .env into its environment first (as every AP Coder process
    does), and the environment wins over server.json: the dashboard was given the .env's backup address, not
    server.json's, and its form wasn't locked. For the backup model servers server.json wins over the .env; the
    machine's own environment still wins over both, and without one in server.json the .env's is used."""
    from ap_coder.core.config import Settings

    (data / ".env").write_text("AP_LLM_BACKUP_URL=http://10.0.0.9:1234/v1\n"
                               "AP_PAGE_READER_BACKUP_URL=http://10.0.0.9:8081/v1\n", encoding="utf-8")  # fmt: skip
    service.write_config(data, llm_backup_url="http://10.0.0.7:1234/v1")
    Settings.from_env(data / ".env")  # what ops/cli.py does first for `service run`
    env = service.serve_env(service.settings(data), data)
    assert env["AP_LLM_BACKUP_URL"] == "http://10.0.0.7:1234/v1"
    assert env["AP_PAGE_READER_BACKUP_URL"] == "http://10.0.0.9:8081/v1"  # none in server.json: the .env's
    os.environ["AP_LLM_BACKUP_URL"] = "http://10.0.0.8:1234/v1"  # the machine's own (setx /M; conftest clears it)
    assert service.serve_env(service.settings(data), data)["AP_LLM_BACKUP_URL"] == "http://10.0.0.8:1234/v1"
