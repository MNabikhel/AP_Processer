"""AP Coder as a Linux systemd unit on the company server (D-208): ``sudo .venv/bin/python -m ap_coder service ...``.

Off a running systemd only what would be done is checked: the unit file, the systemctl and useradd commands (answered
by a stand-in for Ubuntu's systemd), the ``current`` link and the files handed to the service's account; the unit is
read by systemd's own ``systemd-analyze verify`` where it is installed. The service host's behaviour under SIGTERM and
its exit codes are checked for real with processes in tests/ops/test_service_macos.py (the same host, the same codes).
GitHub's Ubuntu runner installs, kills, stops, starts and removes the unit for real (``scripts/ci/linux_service.py``,
CI job linux-service).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ap_coder.core import paths
from ap_coder.ops import cli, server_update, service, service_linux, service_macos
from tests.conftest import ROOT

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux's links and accounts (Windows: test_service.py)")
SHOW = ("LoadState", "ActiveState", "SubState", "MainPID", "NRestarts", "ExecMainStatus", "Result", "UnitFileState")


class FakeSystemd:
    """systemctl and useradd as Ubuntu answers them, each command recorded: the units systemd has read (daemon-reload),
    those enabled (started at boot), those running and their main process; the accounts useradd makes."""

    def __init__(self, units: Path, accounts: dict[str, tuple[int, int]]) -> None:
        self.units, self.accounts = units, accounts
        self.ran: list[list[str]] = []
        self.loaded: set[str] = set()
        self.enabled: set[str] = set()
        self.active: dict[str, int] = {}  # unit: its main process

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.ran.append(cmd)
        if cmd[0] == "useradd":
            self.accounts[cmd[-1]] = (996, 995)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        verb, unit = cmd[1], cmd[2] if len(cmd) > 2 else ""
        if verb == "daemon-reload":
            self.loaded = {p.name for p in self.units.glob("*.service")}
        elif verb == "show":
            return subprocess.CompletedProcess(cmd, 0, self.show(unit), "")
        elif unit not in self.loaded:
            return subprocess.CompletedProcess(cmd, 5, "", f"Failed to {verb} {unit}: Unit {unit} not found.\n")
        elif verb == "enable":
            self.enabled.add(unit)
        elif verb == "disable":
            self.enabled.discard(unit)
        elif verb == "restart":
            self.active[unit] = self.active.get(unit, 4241) + 1
        elif verb == "stop":
            self.active.pop(unit, None)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def show(self, unit: str) -> str:
        found = unit in self.loaded
        pid = self.active.get(unit, 0)
        values = {"LoadState": "loaded" if found else "not-found", "ActiveState": "active" if pid else "inactive",
                  "SubState": "running" if pid else "dead", "MainPID": pid, "NRestarts": 0, "ExecMainStatus": 0,
                  "Result": "success", "UnitFileState": ("enabled" if unit in self.enabled else "disabled") if found
                  else ""}  # fmt: skip
        return "".join(f"{key}={values[key]}\n" for key in SHOW)

    def verbs(self) -> list[str]:
        return [" ".join(cmd[1:]) for cmd in self.ran if cmd[0] == "systemctl"]


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "apcoder-data"
    folder.mkdir()
    monkeypatch.setenv("AP_PRIVATE_DIR", str(folder))
    for name in (*service.ENV_SETTINGS.values(), "AP_DASHBOARD_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    return folder


@pytest.fixture
def linux(tmp_path, monkeypatch, data):
    """This computer as an Ubuntu with sudo and systemd: its systemd stood in for, /etc/systemd/system a folder of the
    test's, the accounts of ``accounts`` (useradd adds one), and what would be handed to an account recorded."""
    units = tmp_path / "systemd" / "system"
    units.mkdir(parents=True)
    running = tmp_path / "run-systemd-system"
    running.mkdir()
    folder = tmp_path / "opt" / "apcoder" / "APProcessor-1.4.0"
    (folder / ".venv" / "bin").mkdir(parents=True)
    (folder / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    accounts = {"apcoder": (996, 995), "root": (0, 0), "runner": (1001, 118)}
    systemd, owned = FakeSystemd(units, accounts), []
    monkeypatch.setattr(service, "WINDOWS", False)
    monkeypatch.setattr(service, "MACOS", False)
    monkeypatch.setattr(service, "LINUX", True)
    monkeypatch.setattr(service, "run_command", systemd)
    monkeypatch.setattr(service, "dashboard_stopped", lambda data, settings, wait=45.0: True)
    monkeypatch.setattr(service, "_local", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no dashboard")))
    monkeypatch.setattr(service_linux, "UNITS", units)
    monkeypatch.setattr(service_linux, "RUNNING", running)
    monkeypatch.setattr(service_macos, "is_root", lambda: True)
    monkeypatch.setattr(service_macos, "account_ids", accounts.get)
    monkeypatch.setattr(service_macos, "own", lambda path, uid, gid: owned.append((Path(path).name, uid, gid)))
    return SimpleNamespace(systemd=systemd, accounts=accounts, owned=owned, folder=folder, data=data, units=units,
                           running=running, unit=units / "apcoder.service")  # fmt: skip


def main(*argv: str) -> int:
    return cli.main(["service", *argv])


def install(m, *argv: str) -> int:
    return main("install", "--insecure-http", "--folder", str(m.folder), *argv)


def unit_lines(text: str) -> dict[str, list[str]]:
    """``[Section] Key`` and each of its values, as systemd reads a unit file (comments left out)."""
    found: dict[str, list[str]] = {}
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line
        elif line and not line.startswith(("#", ";")):
            key, _, value = line.partition("=")
            found.setdefault(f"{section} {key}", []).append(value)
    return found


# --- The unit: its name, its file -------------------------------------------------------------------------------------


def test_linux_has_a_backend_of_its_own():
    """The dispatch by platform: Windows' task, the Mac's LaunchDaemon, Linux's systemd unit (it refused before)."""
    with pytest.MonkeyPatch.context() as here:
        here.setattr(service, "WINDOWS", False)
        here.setattr(service, "MACOS", False)
        here.setattr(service, "LINUX", True)
        assert service.backend() is service_linux and service_linux.NAME == "linux"
        assert service_linux.DEFAULT_ACCOUNT == "apcoder"
        here.setattr(service, "LINUX", False)
        assert service.backend() is None  # (another system: install --dry-run shows Windows' task, the rest refuse)


def test_the_unit_name_is_made_from_the_task_name_and_never_leaves_the_units_folder():
    assert service_linux.unit_name("AP Coder") == "apcoder.service"
    assert service_linux.unit_name("AP Coder test") == "apcoder-ap-coder-test.service"
    assert service_linux.unit_name("apcoder-ci") == "apcoder-ci.service"  # one of AP Coder's names already: as it is
    assert service_linux.unit_name("sshd") == "apcoder-sshd.service"  # never a unit of the system's
    assert service_linux.unit_name("../../etc/sudoers") == "apcoder-etc-sudoers.service"
    assert service_linux.unit_name("..") == "apcoder.service"
    assert service_linux.unit_path("x/../../y").parent == service_linux.UNITS


def test_the_unit_runs_the_current_link_at_boot_as_the_account_and_comes_back_only_after_a_crash(data, tmp_path):
    folder = tmp_path / "opt" / "apcoder" / "APProcessor-1.4.0"
    s = service.settings(data, account="apcoder", folder=str(folder), insecure_http=True)
    unit = unit_lines(service_linux.unit_text(s, data))
    link = tmp_path / "opt" / "apcoder" / "current"
    assert unit["[Service] ExecStart"] == [f'"{link / ".venv" / "bin" / "python"}" -m ap_coder service run']
    assert unit["[Service] WorkingDirectory"] == [str(link)]  # never the version folder: server-update switches it
    assert unit["[Service] User"] == ["apcoder"] and unit["[Service] Type"] == ["simple"]
    assert unit["[Service] Environment"] == [f'"AP_PRIVATE_DIR={data}"', '"AP_SERVER_MODE=1"', '"PYTHONUNBUFFERED=1"']
    # Started again when it crashed or was killed (an exit code but 0, a signal), not when it ended on purpose (0):
    # service stop, systemctl stop. Never given up on (systemd's start limit off), 10 s between two starts.
    assert unit["[Service] Restart"] == ["on-failure"] and unit["[Service] RestartSec"] == ["10"]
    assert unit["[Unit] StartLimitIntervalSec"] == ["0"]
    assert "[Service] SuccessExitStatus" not in unit and "[Service] RestartPreventExitStatus" not in unit
    # SIGTERM to the host only, which ends its dashboard; what is left of it ended once the host has (as launchd).
    assert unit["[Service] KillMode"] == ["mixed"] and unit["[Service] KillSignal"] == ["SIGTERM"]
    assert int(unit["[Service] TimeoutStopSec"][0]) > service.STOP_WAIT  # SIGKILL only after the host's own stop
    assert int(unit["[Service] LimitNOFILE"][0]) >= 4096  # (systemd's soft limit is 1024)
    assert unit["[Install] WantedBy"] == ["multi-user.target"]  # started at boot, whether anyone logs in or not


def test_the_units_values_are_written_as_systemd_reads_them_back(data, tmp_path):
    """A folder with a space, a % (a specifier to systemd), a $ (a variable in ExecStart), a quote or a backslash is
    written so systemd reads the very folder; the other commands read the data folder back from the unit."""
    odd = tmp_path / 'opt 100% "$HOME" \\x'
    s = service.settings(data / 'a 50% "b" \\c', account="apcoder", folder=str(odd / "APProcessor"),
                         insecure_http=True)  # fmt: skip
    text = service_linux.unit_text(s, data / 'a 50% "b" \\c')
    unit = unit_lines(text)
    assert unit["[Service] WorkingDirectory"] == [str(odd / "current").replace("%", "%%")]
    assert unit["[Service] ExecStart"][0].startswith('"' + str(odd).replace("\\", "\\\\").replace('"', '\\"')
                                                     .replace("%", "%%").replace("$", "$$"))  # fmt: skip
    assert unit["[Service] Environment"][0] == '"AP_PRIVATE_DIR=' + str(data / 'a 50% "b" \\c').replace(
        "\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'  # fmt: skip


def test_the_data_folder_is_read_back_from_the_unit_as_it_was_written(linux, monkeypatch, tmp_path):
    odd = tmp_path / 'data 50% "b" \\c $x'
    s = service.settings(odd, account="apcoder", folder=str(linux.folder), insecure_http=True)
    service_linux.write_unit("AP Coder", service_linux.unit_text(s, odd))
    assert service_linux.daemon_data("AP Coder") == str(odd)
    assert service_linux.unit_account("AP Coder") == "apcoder"
    monkeypatch.delenv("AP_PRIVATE_DIR")
    assert service_linux.data_folder(linux.data, "AP Coder") == odd


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="systemd's own check is not on this computer")
def test_systemd_reads_the_unit_without_a_complaint(data, tmp_path):
    """``systemd-analyze verify``: systemd's own reading of the unit file (its keys, values and quoting), here with a
    folder holding a space and a %."""
    folder = tmp_path / "opt 100%" / "APProcessor-1.4.0"
    python = tmp_path / "opt 100%" / "current" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    shutil.copy(shutil.which("true"), python)  # (verify wants a program it can run there)
    s = service.settings(data, account="root", folder=str(folder), insecure_http=True)
    unit = tmp_path / "apcoder-test.service"
    unit.write_text(service_linux.unit_text(s, data), encoding="utf-8")
    done = subprocess.run(["systemd-analyze", "verify", str(unit)], capture_output=True, text=True, timeout=120,
                          check=False)  # fmt: skip
    said = [line for line in (done.stdout + done.stderr).splitlines() if unit.name in line]
    assert done.returncode == 0 and said == [], done.stdout + done.stderr


def test_install_dry_run_shows_the_unit_and_changes_nothing(linux, capsys):
    del linux.accounts["apcoder"]  # not made yet: the dry run says it would be
    assert install(linux, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Restart=on-failure" in out and "WantedBy=multi-user.target" in out and "User=apcoder" in out
    assert "useradd --system" in out and "--shell /usr/sbin/nologin" in out
    assert f"systemctl enable {linux.unit.name}" in out and "Nothing was changed (dry run)" in out
    assert '"account": "apcoder"' in out  # Linux's own default account
    assert not linux.unit.exists() and not (linux.data / "server.json").exists() and "apcoder" not in linux.accounts
    assert not (linux.folder.parent / "current").is_symlink() and linux.systemd.ran == []


# --- install, start, stop, remove (systemd stood in for) --------------------------------------------------------------


def test_install_writes_the_unit_points_current_hands_the_data_folder_over_and_starts_it(linux, capsys):
    assert install(linux, "--allowed-hosts", "apcoder.company.local") == 0
    s = service.settings(linux.data, folder=str(linux.folder), account="apcoder")
    assert linux.unit.read_text(encoding="utf-8") == service_linux.unit_text(s, linux.data)
    assert oct(linux.unit.stat().st_mode & 0o777) == "0o644"  # root's, read by systemd; nobody else changes it
    link = linux.folder.parent / "current"
    assert link.is_symlink() and os.readlink(link) == "APProcessor-1.4.0" and link.resolve() == linux.folder
    saved = json.loads((linux.data / "server.json").read_text(encoding="utf-8"))
    assert saved["account"] == "apcoder" and saved["folder"] == str(linux.folder)
    assert {("service", 996, 995), ("logs", 996, 995), ("host.lock", 996, 995), ("update.lock", 996, 995)} <= set(
        linux.owned)  # fmt: skip
    verbs = linux.systemd.verbs()
    assert verbs[:3] == ["daemon-reload", "enable apcoder.service", "enable apcoder.service"]
    assert verbs[3] == "restart apcoder.service" and "apcoder.service" in linux.systemd.active
    assert not [c for c in linux.systemd.ran if c[0] not in ("systemctl",)]  # (the account was there: no useradd)
    out = capsys.readouterr().out
    assert "Installed the systemd unit apcoder.service" in out and "Started. Staff open" in out
    assert "sudo ufw allow 8501/tcp" in out
    # Installed again (a setting changed): systemd reads the new unit, and the running service is restarted with it.
    linux.systemd.ran.clear()
    assert install(linux, "--port", "8443") == 0
    assert "Environment" in linux.unit.read_text(encoding="utf-8")
    assert linux.systemd.verbs().index("daemon-reload") < linux.systemd.verbs().index("restart apcoder.service")


def test_install_makes_the_service_account_when_this_computer_has_none(linux, tmp_path, monkeypatch, capsys):
    """``apcoder``, the default, is made by install: a system account with no password, no login shell and no home
    folder of its own. A data folder install makes is the account's (made before server.json is written in it)."""
    del linux.accounts["apcoder"]
    data = tmp_path / "var" / "lib" / "apcoder"
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    assert install(linux) == 0
    made = [c for c in linux.systemd.ran if c[0] == "useradd"]
    assert made == [["useradd", "--system", "--user-group", "--no-create-home", "--home-dir", "/nonexistent",
                     "--shell", "/usr/sbin/nologin", "--comment", "AP Coder service", "apcoder"]]  # fmt: skip
    assert linux.systemd.ran.index(made[0]) < linux.systemd.ran.index(["systemctl", "daemon-reload"])
    assert ("apcoder", 996, 995) in linux.owned  # the data folder, made now: the account's
    assert "Made the account apcoder" in capsys.readouterr().out
    assert unit_lines(linux.unit.read_text(encoding="utf-8"))["[Service] User"] == ["apcoder"]


def test_install_refuses_without_sudo_root_an_account_the_data_folder_or_a_set_up_folder(linux, monkeypatch, capsys):
    monkeypatch.setattr(service_macos, "is_root", lambda: False)
    assert install(linux) == 1 and "Run it with sudo" in capsys.readouterr().err
    assert main("status") == 1 and "sudo" in capsys.readouterr().err
    assert main("stop") == 1 and "sudo .venv/bin/python -m ap_coder service stop" in capsys.readouterr().err
    monkeypatch.setattr(service_macos, "is_root", lambda: True)
    assert install(linux, "--account", "nobody-here") == 1
    out = capsys.readouterr().out
    assert "no account nobody-here" in out and "useradd --system" in out and "nobody-here" not in linux.accounts
    assert install(linux, "--account", "root") == 1 and "--allow-root" in capsys.readouterr().out
    (linux.folder / ".venv" / "bin" / "python").unlink()
    assert install(linux) == 1 and "python is not there" in capsys.readouterr().out and not linux.unit.exists()
    (linux.folder / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    (linux.folder.parent / "current").mkdir()  # a folder of someone's own, never replaced
    assert install(linux) == 1 and "not AP Coder's link" in capsys.readouterr().out
    (linux.folder.parent / "current").rmdir()
    monkeypatch.delenv("AP_PRIVATE_DIR")  # sudo left it out, and no unit names one yet
    assert install(linux) == 1 and "AP_PRIVATE_DIR" in capsys.readouterr().out
    monkeypatch.setenv("AP_PRIVATE_DIR", str(linux.data))
    assert linux.systemd.ran == []  # nothing was asked of systemd, no account made
    assert install(linux, "--account", "root", "--allow-root") == 0  # asked for: a first test
    assert unit_lines(linux.unit.read_text(encoding="utf-8"))["[Service] User"] == ["root"]


def test_the_service_commands_say_plainly_when_this_linux_runs_no_systemd(linux, capsys):
    """A container, or WSL without systemd: nothing would start the unit. Said, not a systemctl error."""
    linux.running.rmdir()
    assert main("status") == 1 and "systemd" in capsys.readouterr().err
    assert install(linux) == 1 and "serve --server" in capsys.readouterr().err
    assert install(linux, "--dry-run") == 0  # (what it would do is shown anywhere)
    assert linux.systemd.ran == []


def test_stop_leaves_the_flag_stops_the_unit_and_keeps_it_enabled_and_start_restarts_it(linux, capsys):
    """``service stop`` stays stopped after a restart of the computer as on a Mac: the unit stays enabled, so systemd
    starts its host at boot, and the host, seeing the ``stopped`` file, ends at once with 0 (not started again)."""
    assert install(linux) == 0
    assert main("stop") == 0
    assert service.stop_flag(linux.data).exists() and linux.systemd.verbs()[-1] == "stop apcoder.service"
    assert "apcoder.service" not in linux.systemd.active and "apcoder.service" in linux.systemd.enabled
    assert "disable apcoder.service" not in linux.systemd.verbs()
    assert f"until: {service_linux.AGAIN}" in capsys.readouterr().out
    assert service.run_host(linux.data, popen=lambda *a, **k: pytest.fail("started")) == 0  # its start at boot
    assert main("start") == 0
    assert not service.stop_flag(linux.data).exists() and "apcoder.service" in linux.systemd.active
    assert linux.systemd.verbs()[-2:] == ["enable apcoder.service", "restart apcoder.service"]


def test_a_start_systemd_refuses_is_said_with_where_to_look(linux, monkeypatch, capsys):
    assert install(linux, "--no-start") == 0
    assert "apcoder.service" not in linux.systemd.active and "apcoder.service" in linux.systemd.enabled
    fake = linux.systemd

    def refuses(cmd, **kwargs):
        if cmd[:2] == ["systemctl", "restart"]:
            return subprocess.CompletedProcess(cmd, 1, "", "Job for apcoder.service failed.\n")
        return fake(cmd, **kwargs)

    monkeypatch.setattr(service, "run_command", refuses)
    assert main("start") == 1
    out = capsys.readouterr().out
    assert "could not be started: Job for apcoder.service failed." in out and "journalctl -u apcoder.service" in out


def test_remove_stops_disables_and_deletes_the_unit_keeping_the_data_and_the_link(linux, capsys):
    assert install(linux) == 0
    assert main("remove") == 0
    assert not linux.unit.exists() and not linux.systemd.active and not linux.systemd.enabled
    assert "apcoder.service" not in linux.systemd.loaded and not service.stop_flag(linux.data).exists()
    assert linux.systemd.verbs()[-2:] == ["disable apcoder.service", "daemon-reload"]
    assert (linux.data / "server.json").exists() and (linux.folder.parent / "current").is_symlink()
    assert "the account apcoder" in capsys.readouterr().out  # kept, said
    assert main("remove") == 1 and "not installed" in capsys.readouterr().out
    assert main("start") == 1


def test_the_other_commands_find_the_data_folder_in_the_unit_when_sudo_leaves_it_out(linux, monkeypatch, tmp_path):
    """sudo leaves AP_PRIVATE_DIR out (its env_reset): ``service stop`` would write its ``stopped`` file in another
    folder, and the host started at the next boot would not see it. The unit (root's) names the data folder."""
    assert install(linux) == 0
    monkeypatch.delenv("AP_PRIVATE_DIR")
    monkeypatch.setenv("AP_USER_SETTINGS", str(tmp_path / "root-settings.json"))
    assert paths.private_dir() != linux.data
    assert main("stop") == 0
    assert service.stop_flag(linux.data).exists()


def test_what_roots_sudo_made_in_the_data_folder_is_handed_back_at_start(linux, monkeypatch):
    """server-update's backups and restores, ``users add`` with sudo: root's files in the account's data folder are
    handed back at each ``service start`` (the account named in the unit), as on a Mac."""
    assert install(linux, "--no-start") == 0
    handed = []
    monkeypatch.setattr(service_macos, "hand_back", lambda data, uid, gid: handed.append((data, uid, gid)) or 0)
    uid = os.lstat(linux.data).st_uid
    if uid == 0:  # (the tests run as root: the data folder made another account's)
        os.chown(linux.data, 1234, 995)
        uid = 1234
    monkeypatch.setitem(linux.accounts, "apcoder", (uid, 995))  # the data folder: the account's
    assert main("start") == 0 and handed == [(linux.data, uid, 995)]
    handed.clear()
    monkeypatch.setitem(linux.accounts, "apcoder", (uid + 1, 995))  # not the account's: never walked
    assert main("start") == 0 and handed == []


def test_status_says_the_unit_its_host_and_the_dashboard_in_words_and_in_json(linux, monkeypatch, capsys):
    assert main("status") == 1 and "not installed" in capsys.readouterr().out
    assert main("status", "--json") == 1
    assert json.loads(capsys.readouterr().out)["installed"] is False
    assert install(linux) == 0
    health = {"ok": True, "app": "ap-coder", "version": "1.4.0", "folder": str(linux.folder), "port": 8501}
    monkeypatch.setattr(service, "dashboard_health", lambda data, s, timeout=3.0: health)
    (linux.data / ".ap_coder_server.json").write_text(json.dumps({"pid": 5150, "port": 8501}), encoding="utf-8")
    host = {"pid": 4242, "started": "2026-11-09T08:00:00", "folder": str(linux.folder), "version": "1.4.0"}
    service.host_record(linux.data).write_text(json.dumps(host), encoding="utf-8")
    held = paths.try_lock(service.host_lock(linux.data))
    try:
        capsys.readouterr()
        assert main("status") == 0
        out = capsys.readouterr().out
        assert "systemd unit apcoder.service: active (running, process 4242), runs as apcoder" in out
        assert "Service host: running since 2026-11-09T08:00:00 (process 4242" in out and "Dashboard: answering" in out
        assert main("status", "--json") == 0
        found = json.loads(capsys.readouterr().out)
    finally:
        held.close()
    assert found["platform"] == "linux" and found["installed"] is True and found["loaded"] is True
    assert found["running"] is True and found["pid"] == 4242 and found["systemd_pid"] == 4242
    assert found["state"] == "active" and found["sub_state"] == "running" and found["enabled"] == "enabled"
    assert found["restarts"] == 0 and found["last_exit_code"] == "0" and found["result"] == "success"
    assert found["unit"] == "apcoder.service" and found["unit_file"] == str(linux.unit)
    assert found["account"] == "apcoder" and found["answering"] is True and found["dashboard_pid"] == 5150
    assert found["stopped_on_purpose"] is False and found["log"] == str(linux.data / "logs" / "service.log")
    assert "line" not in found and "missing" not in found


def test_systemctl_show_is_read_as_its_lines_of_key_and_value():
    found = service_linux.parse_show("LoadState=loaded\nActiveState=activating\nSubState=auto-restart\nMainPID=0\n"
                                     "ExecMainStatus=75\nUnitFileState=enabled\nDescription=a=b\n")  # fmt: skip
    assert found["ActiveState"] == "activating" and found["SubState"] == "auto-restart"
    assert found["ExecMainStatus"] == "75" and found["Description"] == "a=b"


def test_the_doctor_says_the_unit(linux):
    checks = {area: (status, detail) for area, status, detail in service.doctor_checks(linux.data, env={},
                                                                                        linux=True)}  # fmt: skip
    assert checks["server service"][0] == "WARN" and "not installed" in checks["server service"][1]
    assert install(linux) == 0
    checks = {area: (status, detail) for area, status, detail in service.doctor_checks(linux.data, env={}, linux=True)}
    assert checks["server service"] == ("PASS", "systemd unit apcoder.service: active, runs as apcoder")
    assert main("stop") == 0
    checks = {area: (status, detail) for area, status, detail in service.doctor_checks(linux.data, env={}, linux=True)}
    assert checks["server service"][1].endswith("(stopped on purpose: service start)")


# --- server-update: the current link, switched in one step, and the host's "start me again" ---------------------------


def test_server_update_switches_the_current_link_and_sees_the_unit_installed(linux):
    assert install(linux, "--no-start") == 0
    new = linux.folder.parent / "APProcessor-1.5.0"
    new.mkdir()
    (new / "pyproject.toml").write_text('[project]\nname = "ap-coder"\nversion = "1.5.0"\n', encoding="utf-8")
    before = linux.unit.read_text(encoding="utf-8")
    server_update.switch_to(linux.data, new)
    link = linux.folder.parent / "current"
    assert os.readlink(link) == "APProcessor-1.5.0" and link.resolve() == new
    assert service.read_config(linux.data)["folder"] == str(new) and not (linux.data / "service" / "run.cmd").exists()
    assert linux.unit.read_text(encoding="utf-8") == before  # the unit is never rewritten for a version
    s = server_update.server_settings(linux.data)
    assert server_update.Server(linux.data, s).installed() is True
    linux.unit.unlink()
    assert server_update.Server(linux.data, s).installed() is False


def test_a_host_that_must_be_started_again_never_ends_with_0_on_linux(data, monkeypatch):
    """``service.again()`` (another host runs; an update's recovery switched the folder to run) is 75 off Windows:
    Restart=on-failure starts again a host that ended with a code but 0, and leaves one that ended with 0 stopped. No
    SuccessExitStatus may name 75, or systemd would leave it stopped."""
    monkeypatch.setattr(service, "WINDOWS", False)
    assert service.again() == service.HOST_AGAIN != 0
    s = service.settings(data, account="apcoder", insecure_http=True)
    unit = unit_lines(service_linux.unit_text(s, data))
    assert unit["[Service] Restart"] == ["on-failure"] and not any("ExitStatus" in key for key in unit)


def test_the_hosts_switch_is_asked_of_the_links_folder(tmp_path, monkeypatch):
    """The host, as the service's account, can't change /opt/apcoder (root's): it is asked first, so it never puts a
    database back and then fails at the link (B-1957, as on a Mac)."""
    asked = []
    monkeypatch.setattr(os, "access", lambda path, mode: asked.append((Path(path), mode)) or False)
    folder = tmp_path / "opt" / "apcoder" / "APProcessor-1.4.0"
    assert service_linux.can_switch(tmp_path / "data", folder) is False
    assert asked == [(folder.parent, os.W_OK)]


class PowerCut(OSError):
    """The computer stopped here."""


def test_the_unit_is_never_left_cut_short(linux, monkeypatch):
    """Cut before its rename, the unit is the old one, whole, and nothing of the new one is left; it is flushed to the
    disk before it takes the name (``service.write_whole``), under a name systemd never reads (not .service)."""
    assert install(linux, "--no-start") == 0
    before = linux.unit.read_bytes()
    done: list[str] = []

    def cut(src, dst):
        done.append(f"rename {Path(src).name}")
        raise PowerCut("the power went off")

    with monkeypatch.context() as power:
        power.setattr(os, "fsync", lambda fd: done.append("fsync"))
        power.setattr(os, "replace", cut)
        with pytest.raises(PowerCut):
            service_linux.write_unit("AP Coder", "[Unit]\n")
    assert done[0] == "fsync" and done[1].startswith("rename .apcoder.service.") and done[1].endswith(".part")
    assert linux.unit.read_bytes() == before and [p.name for p in linux.units.iterdir()] == ["apcoder.service"]


# --- CI's check on GitHub's Ubuntu runner: scripts/ci/linux_service.py ------------------------------------------------

CI_SCRIPT = ROOT / "scripts" / "ci" / "linux_service.py"
needs_ci_script = pytest.mark.skipif(not CI_SCRIPT.is_file(), reason="no scripts/ci in this copy (an offline bundle)")


def _ci():
    spec = importlib.util.spec_from_file_location("ap_ci_linux_service", CI_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeUbuntu:
    """The Ubuntu CI's script drives, as its commands find it: the unit installed and enabled, its host's and
    dashboard's processes and their account, systemd starting a killed host again, its start at boot, and ``status
    --json`` answering as AP Coder's does."""

    def __init__(self, unit: Path, misbehave: str = "") -> None:
        self.unit, self.misbehave, self.ran = unit, misbehave, []
        self.host = self.dashboard = 0
        self.stopped_at = 0.0
        self.flag = self.marker = False  # the ``stopped`` file; an update's marker
        self.asked: dict[str, int] = {}  # kill -0 of each process: how many times

    def __call__(self, cmd, timeout=300):
        self.ran.append(cmd)
        out, code = "", 0
        if "service" in cmd and "ap_coder" in cmd:
            out = self.service(cmd[cmd.index("service") + 1])
        elif cmd[:3] == ["sudo", "kill", "-9"]:
            if self.misbehave != "stays dead":
                self.host, self.dashboard = self.host + 1, self.dashboard + 1  # systemd: a new host, its dashboard
            else:
                self.host = self.dashboard = 0
        elif cmd[:3] == ["sudo", "kill", "-0"]:  # the old dashboard: gone (a moment later, or never, if so told)
            asked = self.asked[cmd[3]] = self.asked.get(cmd[3], 0) + 1
            late = self.misbehave == "the old dashboard ends a few seconds later" and asked <= 2
            code = 0 if late or self.misbehave == "the old dashboard never ends" else 1
        elif cmd[:3] == ["sudo", "systemctl", "stop"]:  # systemd's SIGTERM: the host ends its dashboard, then itself
            self.host = self.dashboard = 0
            self.stopped_at = time.monotonic()
        elif cmd[:3] == ["sudo", "systemctl", "start"]:  # as at boot: the host deals with an update, or ends at once
            self.boot()
        elif cmd[:3] == ["sudo", "systemd-analyze", "verify"]:
            code = 1 if self.misbehave == "a unit systemd does not read" else 0
        elif cmd[:2] == ["systemctl", "is-enabled"]:
            out = "disabled" if self.misbehave == "not enabled" else "enabled"
        elif cmd[:2] == ["systemctl", "show"]:
            out = "LoadState=" + ("loaded" if self.unit.exists() else "not-found")
        elif cmd[:2] == ["ps", "-o"]:
            out = "root" if self.misbehave == "runs as root" else "apcoder"
        elif cmd[:3] == ["sudo", "test", "-e"]:  # the update's marker: removed once dealt with
            code = 0 if self.marker else 1
        elif cmd[:2] == ["sudo", "grep"]:
            code = 1 if self.misbehave == "no SIGTERM in its log" else 0
        elif "update_recovery" in " ".join(cmd):
            self.marker = True
        return subprocess.CompletedProcess(cmd, code, out, "")

    def service(self, action: str) -> str:
        if action == "install":
            self.unit.write_text("[Unit]\n", encoding="utf-8")
            self.host, self.dashboard = 600, 601
        elif action in ("stop", "remove"):
            self.host = self.dashboard = 0
            self.flag, self.stopped_at = True, time.monotonic()
            if action == "remove":
                self.unit.unlink(missing_ok=True)
                self.flag = False
        elif action == "start" and self.host:  # running: systemctl restart, a new host (the old one asked to stop)
            kept = self.misbehave == "keeps the old dashboard"
            self.host, self.dashboard = self.host + 10, self.dashboard + (0 if kept else 10)
        elif action == "start":
            self.host, self.dashboard, self.flag = 700, 701, False
        elif action == "status":
            if self.misbehave == "comes back" and self.stopped_at and self.unit.exists():
                self.host, self.dashboard = 800, 801
            up = bool(self.host)
            return json.dumps({"installed": self.unit.exists(), "unit_file": str(self.unit), "running": up,
                               "pid": self.host or None, "answering": up, "dashboard_pid": self.dashboard or None,
                               "health": {"app": "ap-coder"} if up else None,
                               "state": "active" if up else "inactive", "last_exit_code": "0"})  # fmt: skip
        return ""

    def boot(self) -> None:
        if self.marker:  # an update cut short: the version it runs starts, the marker and the update's stop gone
            if self.misbehave != "stays stopped after an update cut short":
                self.host, self.dashboard, self.flag = 900, 901, False
                self.marker = self.misbehave == "keeps the marker of an update cut short"
        elif not self.flag or self.misbehave == "starts at boot after a stop":
            self.host, self.dashboard = 950, 951


def _ci_run(tmp_path, monkeypatch, misbehave: str) -> tuple[int, FakeUbuntu]:
    ci = _ci()
    ubuntu = FakeUbuntu(tmp_path / "apcoder-ci.service", misbehave)
    clock = [0.0]
    monkeypatch.setattr(ci, "run", ubuntu)
    monkeypatch.setattr(ci, "clock", lambda: clock[0])
    monkeypatch.setattr(ci, "pause", lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, 0.5)))
    return ci.main(["--data", str(tmp_path / "data"), "--port", "8597"]), ubuntu


@needs_ci_script
@pytest.mark.parametrize("misbehave", ["", "stays dead", "keeps the old dashboard", "no SIGTERM in its log",
                                       "comes back", "the old dashboard never ends", "starts at boot after a stop",
                                       "stays stopped after an update cut short",
                                       "keeps the marker of an update cut short", "a unit systemd does not read",
                                       "not enabled", "runs as root"])  # fmt: skip
def test_the_ci_script_passes_a_service_that_behaves_and_fails_one_that_does_not(tmp_path, monkeypatch, misbehave):
    code, ubuntu = _ci_run(tmp_path, monkeypatch, misbehave)
    assert code == (0 if not misbehave else 1)
    commands = [" ".join(c) for c in ubuntu.ran]
    assert any("service install --account apcoder --insecure-http --host 127.0.0.1 --port 8597" in c
               for c in commands)  # fmt: skip
    assert all("--task-name apcoder-ci" in c for c in commands if " ap_coder service " in c)  # never the real unit
    assert not ubuntu.unit.exists()  # removed at the end, failed or not
    if misbehave:
        assert any(c.startswith("sudo journalctl -u apcoder-ci.service") for c in commands)  # for the failure
    if not misbehave:
        # service stop, then systemd starting the unit as at boot: it stays stopped. Then an update cut short, as
        # root's sudo leaves it (its marker), and the unit's next start.
        stop = commands.index("sudo systemctl start apcoder-ci.service")
        assert any(" ap_coder service stop " in c for c in commands[:stop])
        assert any("update_recovery" in c and "safety-copy" in c for c in commands)
        assert len([c for c in commands if c == "sudo systemctl start apcoder-ci.service"]) == 2
        assert commands.index("sudo systemctl stop apcoder-ci.service") < stop


@needs_ci_script
def test_the_ci_script_gives_the_old_dashboard_a_while_to_end(tmp_path, monkeypatch):
    """systemd ends a killed host's processes before it starts the new one, but the check of the old dashboard may come
    a moment after the new one answers: it is asked again for 30 s, as on a Mac (B-1959)."""
    code, ubuntu = _ci_run(tmp_path, monkeypatch, "the old dashboard ends a few seconds later")
    assert code == 0 and max(ubuntu.asked.values()) == 3


@needs_ci_script
def test_the_ci_script_runs_its_commands_in_its_own_copy_of_ap_coder(monkeypatch):
    """CI runs the script of the copy in /opt/apcoder from the checkout's folder: ``python -m ap_coder`` takes the
    package from the folder it runs in, so each command runs in the script's own copy, whatever the caller's folder."""
    ci = _ci()
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ci.subprocess, "run", fake_run)
    ci.run(["true"])
    assert Path(seen.get("cwd", "")) == CI_SCRIPT.parents[2]
