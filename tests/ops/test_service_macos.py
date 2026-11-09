"""AP Coder as a Mac's LaunchDaemon on the company server (D-186): ``sudo .venv/bin/python -m ap_coder service ...``.

Off a Mac only what would be done is checked: the plist, the launchctl commands (answered by a stand-in for the Mac's
launchd), the ``current`` link and the files handed to the service's account. The service host's own behaviour under
launchd (SIGTERM, its exit codes) is checked for real with processes, as launchd would see it. GitHub's macOS runner
installs, kills, stops, starts and removes the daemon for real (``scripts/ci/macos_service.py``, CI job macos-service).
"""

from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ap_coder.core import paths
from ap_coder.ops import cli, server_update, service, service_macos
from tests.conftest import ROOT

pytestmark = pytest.mark.skipif(os.name == "nt", reason="a Mac's links and signals (Windows: test_service.py)")
ACCOUNTS = {"_apcoder": (450, 450), "root": (0, 0), "runner": (501, 20)}
PRINT = """system/{label} = {{
\tactive count = 1
\tpath = /Library/LaunchDaemons/{label}.plist
\ttype = LaunchDaemon
\tstate = running

\tprogram = /Library/APCoder/current/.venv/bin/python
\targuments = {{
\t\t/Library/APCoder/current/.venv/bin/python
\t\t-m
\t\tap_coder
\t}}

\tevent triggers = {{
\t\tcom.apple.example => {{
\t\t\tstate = 0
\t\t\tpid = 1
\t\t}}
\t}}

\truns = 1
\tpid = {pid}
\tlast exit code = (never exited)
}}
"""


class FakeLaunchd:
    """launchctl as a Mac's answers it, each command recorded: what is loaded, and its process."""

    def __init__(self) -> None:
        self.ran: list[list[str]] = []
        self.loaded: dict[str, int] = {}  # system/<label>: its pid

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.ran.append(cmd)
        verb, where = cmd[1], cmd[-1]
        if verb == "print" and where in self.loaded:
            return subprocess.CompletedProcess(cmd, 0, PRINT.format(label=where[7:], pid=self.loaded[where]), "")
        if verb == "bootstrap":
            self.loaded[f"system/{plistlib.loads(Path(where).read_bytes())['Label']}"] = 4242
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if verb == "bootout" and where in self.loaded:
            del self.loaded[where]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if verb in ("enable",) or (verb == "kickstart" and where in self.loaded):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 113, "", f'Could not find service "{where}" in domain for system\n')

    def verbs(self) -> list[str]:
        return [" ".join(cmd[1:]) for cmd in self.ran]


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "APCoderData"
    folder.mkdir()
    monkeypatch.setenv("AP_PRIVATE_DIR", str(folder))
    for name in (*service.ENV_SETTINGS.values(), "AP_DASHBOARD_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    return folder


@pytest.fixture
def mac(tmp_path, monkeypatch, data):
    """This computer as a Mac with sudo: its launchd stood in for, /Library/LaunchDaemons a folder of the test's, the
    accounts of :data:`ACCOUNTS`, and what would be handed to an account recorded."""
    daemons = tmp_path / "LaunchDaemons"
    daemons.mkdir()
    folder = tmp_path / "Library" / "APCoder" / "APProcessor-1.4.0"
    (folder / ".venv" / "bin").mkdir(parents=True)
    (folder / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    launchd, owned = FakeLaunchd(), []
    monkeypatch.setattr(service, "WINDOWS", False)
    monkeypatch.setattr(service, "MACOS", True)
    monkeypatch.setattr(service, "run_command", launchd)
    monkeypatch.setattr(service, "dashboard_stopped", lambda data, settings, wait=45.0: True)
    monkeypatch.setattr(service, "_local", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no dashboard")))
    monkeypatch.setattr(service_macos, "DAEMONS", daemons)
    monkeypatch.setattr(service_macos, "is_root", lambda: True)
    monkeypatch.setattr(service_macos, "account_ids", ACCOUNTS.get)
    monkeypatch.setattr(service_macos, "own", lambda path, uid, gid: owned.append((Path(path).name, uid, gid)))
    return SimpleNamespace(launchd=launchd, owned=owned, folder=folder, data=data,
                           plist=daemons / "com.apcoder.server.plist")  # fmt: skip


def main(*argv: str) -> int:
    return cli.main(["service", *argv])


def install(m, *argv: str) -> int:
    return main("install", "--insecure-http", "--folder", str(m.folder), *argv)


# --- The daemon: its label, its plist ---------------------------------------------------------------------------------


def test_the_label_is_made_from_the_task_name_and_never_leaves_launchdaemons():
    assert service_macos.label("AP Coder") == "com.apcoder.server"
    assert service_macos.label("AP Coder test") == "com.apcoder.ap-coder-test"
    assert service_macos.label("com.example.apcoder") == "com.example.apcoder"  # a label already: as it is
    assert service_macos.label("../../etc/sudoers") == "com.apcoder.etc-sudoers"
    assert service_macos.label("..") == "com.apcoder.server"
    assert service_macos.plist_path("x/../../y").parent == service_macos.DAEMONS


def test_the_plist_runs_the_current_link_at_boot_as_the_account_and_comes_back_only_after_a_crash(data, tmp_path):
    folder = tmp_path / "Library" / "APCoder" / "APProcessor-1.4.0"
    s = service.settings(data, account="_apcoder", folder=str(folder), insecure_http=True)
    plist = plistlib.loads(plistlib.dumps(service_macos.daemon(s, data)))  # (what launchd reads back)
    link = tmp_path / "Library" / "APCoder" / "current"
    assert plist["Label"] == "com.apcoder.server"
    assert plist["ProgramArguments"] == [str(link / ".venv" / "bin" / "python"), "-m", "ap_coder", "service", "run"]
    assert plist["WorkingDirectory"] == str(link)  # never the version folder: server-update switches the link
    assert plist["EnvironmentVariables"]["AP_PRIVATE_DIR"] == str(data)
    assert plist["EnvironmentVariables"]["AP_SERVER_MODE"] == "1"
    assert plist["UserName"] == "_apcoder" and plist["RunAtLoad"] is True
    # Started again when it crashed or was killed (an exit code but 0, a signal), not when it ended on purpose (0).
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["AbandonProcessGroup"] is False  # a host killed: launchd ends its dashboard with it
    assert plist["ThrottleInterval"] == 10
    assert plist["ExitTimeOut"] > service.STOP_WAIT  # launchd's SIGKILL only after the host has ended its dashboard
    assert plist["SoftResourceLimits"]["NumberOfFiles"] >= 4096  # (launchd's default is 256)
    for key in ("StandardOutPath", "StandardErrorPath"):
        assert Path(plist[key]).parent == data / "logs"


def test_install_dry_run_shows_the_plist_and_changes_nothing(mac, capsys):
    assert install(mac, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "<key>KeepAlive</key>" in out and "<key>SuccessfulExit</key>" in out and "<false/>" in out
    assert f"launchctl bootstrap system {mac.plist}" in out and "Nothing was changed (dry run)" in out
    assert '"account": "_apcoder"' in out  # the Mac's own default account
    assert not mac.plist.exists() and not (mac.data / "server.json").exists()
    assert not (mac.folder.parent / "current").is_symlink() and mac.launchd.ran == []


# --- install, start, stop, remove (launchd stood in for) --------------------------------------------------------------


def test_install_writes_the_plist_points_current_hands_the_data_folder_over_and_loads_it(mac, capsys):
    assert install(mac, "--allowed-hosts", "apcoder.company.local") == 0
    plist = plistlib.loads(mac.plist.read_bytes())
    assert plist == service_macos.daemon(service.settings(mac.data, folder=str(mac.folder), account="_apcoder"),
                                         mac.data)  # fmt: skip
    assert oct(mac.plist.stat().st_mode & 0o777) == "0o644"  # launchd loads no file others can change
    link = mac.folder.parent / "current"
    assert link.is_symlink() and os.readlink(link) == "APProcessor-1.4.0" and link.resolve() == mac.folder
    saved = json.loads((mac.data / "server.json").read_text(encoding="utf-8"))
    assert saved["account"] == "_apcoder" and saved["folder"] == str(mac.folder)
    # The parts the host writes are the account's (root made them); the plist is root's.
    assert {("service", 450, 450), ("logs", 450, 450), ("host.lock", 450, 450)} <= set(mac.owned)
    assert ("update.lock", 450, 450) in mac.owned  # (made by sudo server-update, its host could not open it: B-1956)
    assert any(name.endswith(".part") and (uid, gid) == (0, 0) for name, uid, gid in mac.owned)
    assert "bootstrap system " + str(mac.plist) in mac.launchd.verbs()
    assert "system/com.apcoder.server" in mac.launchd.loaded
    out = capsys.readouterr().out
    assert "Installed the LaunchDaemon com.apcoder.server" in out and "Started. Staff open" in out
    # Installed again (a setting changed) while loaded: booted out first, so launchd loads the new plist.
    mac.launchd.ran.clear()
    assert install(mac, "--port", "8443") == 0
    assert mac.launchd.verbs().index("bootout system/com.apcoder.server") < mac.launchd.verbs().index(
        "bootstrap system " + str(mac.plist))  # fmt: skip


def test_a_data_folder_install_makes_is_the_accounts(mac, tmp_path, monkeypatch):
    """A data folder ``service install`` makes (not there yet) is handed to the account, as on Linux: server.json was
    written in it first, so it was there already when it was handed over, and it stayed root's."""
    data = tmp_path / "Library" / "APCoderNew"
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    assert install(mac, "--no-start") == 0
    assert ("APCoderNew", 450, 450) in mac.owned and (data / "server.json").is_file()


def test_install_refuses_without_sudo_root_an_account_the_data_folder_or_a_set_up_folder(mac, monkeypatch, capsys):
    monkeypatch.setattr(service_macos, "is_root", lambda: False)
    assert install(mac) == 1 and "sudo" in capsys.readouterr().err
    assert main("status") == 1 and "sudo" in capsys.readouterr().err
    monkeypatch.setattr(service_macos, "is_root", lambda: True)
    assert install(mac, "--account", "nobody-here") == 1 and "no account nobody-here" in capsys.readouterr().out
    assert install(mac, "--account", "root") == 1 and "--allow-root" in capsys.readouterr().out
    (mac.folder / ".venv" / "bin" / "python").unlink()
    assert install(mac) == 1 and "python is not there" in capsys.readouterr().out and not mac.plist.exists()
    (mac.folder / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    (mac.folder.parent / "current").mkdir()  # a folder of someone's own, never replaced
    assert install(mac) == 1 and "not AP Coder's link" in capsys.readouterr().out
    (mac.folder.parent / "current").rmdir()
    monkeypatch.delenv("AP_PRIVATE_DIR")  # sudo left it out, and no daemon names one yet
    assert install(mac) == 1 and "AP_PRIVATE_DIR" in capsys.readouterr().out
    monkeypatch.setenv("AP_PRIVATE_DIR", str(mac.data))
    assert install(mac, "--account", "root", "--allow-root") == 0  # asked for: a first test
    assert plistlib.loads(mac.plist.read_bytes())["UserName"] == "root"
    assert mac.launchd.ran and not [c for c in mac.launchd.ran if c[0] != "launchctl"]


def test_stop_leaves_the_flag_and_boots_it_out_and_start_loads_it_again(mac, capsys):
    assert install(mac) == 0
    assert main("stop") == 0
    assert service.stop_flag(mac.data).exists() and "bootout system/com.apcoder.server" in mac.launchd.verbs()
    assert not mac.launchd.loaded and f"until: {service_macos.AGAIN}" in capsys.readouterr().out
    assert main("start") == 0
    assert not service.stop_flag(mac.data).exists() and "system/com.apcoder.server" in mac.launchd.loaded
    assert mac.launchd.verbs()[-1] == "bootstrap system " + str(mac.plist)
    assert main("start") == 0  # loaded: started again
    assert mac.launchd.verbs()[-1] == "kickstart -k system/com.apcoder.server"


def test_remove_boots_it_out_and_deletes_the_plist_keeping_the_data_and_the_link(mac, capsys):
    assert install(mac) == 0
    assert main("remove") == 0
    assert not mac.plist.exists() and not mac.launchd.loaded and not service.stop_flag(mac.data).exists()
    assert (mac.data / "server.json").exists() and (mac.folder.parent / "current").is_symlink()
    assert main("remove") == 1 and "not installed" in capsys.readouterr().out
    assert main("start") == 1


def test_what_roots_sudo_made_in_the_data_folder_is_handed_back_never_what_a_link_points_at(tmp_path, monkeypatch):
    """An administrator's sudo (server-update's backup and restore, users add before the first start) leaves files
    and folders that are root's: the service's account could not write them (a new backup in a backups folder root
    made, the database root made). ``hand_back`` gives the account root's own folders and files with one name only,
    through the open file: a link, a hard link to a file of the system, or a folder linked in are left alone."""
    data, system = tmp_path / "APCoderData", tmp_path / "etc"
    (data / "backups").mkdir(parents=True)
    (data / "backups" / "ap_coder-before-update.db").write_text("db", encoding="utf-8")
    (data / "authenticator.key").write_text("key", encoding="utf-8")
    system.mkdir()
    (system / "sudoers").write_text("root ALL", encoding="utf-8")
    (system / "inside").write_text("x", encoding="utf-8")
    (data / "a link").symlink_to(system / "inside")  # (one name: only O_NOFOLLOW keeps it)
    (data / "a linked folder").symlink_to(system)
    os.link(system / "sudoers", data / "a hard link")
    monkeypatch.setattr(service_macos, "ROOT_UID", os.lstat(data).st_uid)  # the test's own files stand for root's
    handed: list[tuple[int, int, int]] = []
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: handed.append((os.fstat(fd).st_ino, uid, gid)))
    assert service_macos.hand_back(data, 450, 450) == 3
    names = {p.lstat().st_ino: p.name for p in (data / "backups", data / "backups" / "ap_coder-before-update.db",
                                                data / "authenticator.key")}  # fmt: skip
    assert {names.get(inode, inode) for inode, _, _ in handed} == set(names.values())
    assert {(uid, gid) for _, uid, gid in handed} == {(450, 450)}
    # A data folder that is not the daemon's account's (a system folder named by mistake) is never walked.
    monkeypatch.setattr(service_macos, "is_root", lambda: True)
    monkeypatch.setattr(service_macos, "read_plist", lambda task: {"UserName": "_apcoder"})
    monkeypatch.setattr(service_macos, "account_ids", lambda name: (os.lstat(data).st_uid + 1, 450))
    handed.clear()
    service_macos.hand_back_to_daemon(data, "AP Coder")
    assert handed == []


def test_the_other_commands_find_the_data_folder_in_the_daemon_when_sudo_leaves_it_out(mac, monkeypatch, tmp_path):
    """sudo leaves AP_PRIVATE_DIR out: ``service stop`` then wrote its ``stopped`` file in another folder (the code
    folder's private/), and the host started at the Mac's next start did not see it. The daemon's plist (root's) names
    the data folder: the commands use it."""
    assert install(mac) == 0
    monkeypatch.delenv("AP_PRIVATE_DIR")
    monkeypatch.setenv("AP_USER_SETTINGS", str(tmp_path / "root-settings.json"))
    assert paths.private_dir() != mac.data
    assert main("stop") == 0
    assert service.stop_flag(mac.data).exists()


def test_status_says_the_daemon_its_host_and_the_dashboard_in_words_and_in_json(mac, monkeypatch, capsys):
    assert main("status") == 1 and "not installed" in capsys.readouterr().out
    assert main("status", "--json") == 1
    assert json.loads(capsys.readouterr().out)["installed"] is False
    assert install(mac) == 0
    health = {"ok": True, "app": "ap-coder", "version": "1.4.0", "folder": str(mac.folder), "port": 8501, "pid": 5151}
    monkeypatch.setattr(service, "dashboard_health", lambda data, s, timeout=3.0: health)
    (mac.data / ".ap_coder_server.json").write_text(json.dumps({"pid": 5150, "port": 8501}), encoding="utf-8")
    service.host_record(mac.data).write_text(json.dumps({"pid": 4242, "started": "2026-11-06T08:00:00",
                                                         "folder": str(mac.folder), "version": "1.4.0"}))  # fmt: skip
    held = paths.try_lock(service.host_lock(mac.data))
    try:
        capsys.readouterr()
        assert main("status") == 0
        out = capsys.readouterr().out
        assert "LaunchDaemon com.apcoder.server: running (process 4242), runs as _apcoder" in out
        assert "Service host: running since 2026-11-06T08:00:00 (process 4242" in out and "Dashboard: answering" in out
        assert main("status", "--json") == 0
        found = json.loads(capsys.readouterr().out)
    finally:
        held.close()
    assert found["platform"] == "macos" and found["installed"] is True and found["loaded"] is True
    assert found["running"] is True and found["pid"] == 4242 and found["launchd_pid"] == 4242
    assert (found["version"], found["folder"]) == ("1.4.0", str(mac.folder))
    assert found["address"] == "http://<this server's name>:8501" and found["answering"] is True
    assert found["health"] == health and found["dashboard_pid"] == 5150 and found["state"] == "running"
    assert found["label"] == "com.apcoder.server" and found["plist"] == str(mac.plist)
    assert found["stopped_on_purpose"] is False and found["log"] == str(mac.data / "logs" / "service.log")
    assert "line" not in found and "missing" not in found


def test_launchctl_print_is_read_at_its_top_level_only():
    found = service_macos.parse_print(PRINT.format(label="com.apcoder.server", pid=812))
    assert found["state"] == "running" and found["pid"] == "812"  # not the event trigger's "pid = 1"
    assert found["last exit code"] == "(never exited)" and "arguments" in found


def test_the_doctor_says_the_daemon(mac):
    checks = dict((area, (status, detail)) for area, status, detail in service.doctor_checks(mac.data, env={},
                                                                                              macos=True))  # fmt: skip
    assert checks["server service"][0] == "WARN" and "not installed" in checks["server service"][1]
    assert install(mac) == 0
    checks = {area: (status, detail) for area, status, detail in service.doctor_checks(mac.data, env={}, macos=True)}
    assert checks["server service"] == ("PASS", "LaunchDaemon com.apcoder.server: running, runs as _apcoder")


# --- server-update: the current link, switched in one step ------------------------------------------------------------


def test_server_update_switches_the_current_link_not_a_run_cmd_and_sees_the_daemon_installed(mac):
    assert install(mac, "--no-start") == 0
    new = mac.folder.parent / "APProcessor-1.5.0"
    new.mkdir()
    (new / "pyproject.toml").write_text('[project]\nname = "ap-coder"\nversion = "1.5.0"\n', encoding="utf-8")
    server_update.switch_to(mac.data, new)
    link = mac.folder.parent / "current"
    assert os.readlink(link) == "APProcessor-1.5.0" and link.resolve() == new
    assert service.read_config(mac.data)["folder"] == str(new) and not (mac.data / "service" / "run.cmd").exists()
    assert plistlib.loads(mac.plist.read_bytes())["WorkingDirectory"] == str(link)  # the plist is never rewritten
    s = server_update.server_settings(mac.data)
    assert server_update.Server(mac.data, s).installed() is True
    mac.plist.unlink()
    assert server_update.Server(mac.data, s).installed() is False


def test_the_current_link_always_names_a_folder_while_it_is_switched(tmp_path):
    """The link is replaced by one rename (a new link renamed over it): a start of the service meanwhile finds the old
    folder or the new one, never no link."""
    old, new = tmp_path / "APProcessor-1.4.0", tmp_path / "APProcessor-1.5.0"
    old.mkdir()
    new.mkdir()
    link = service_macos.point_current(old)
    seen, missing, done = set(), [], threading.Event()

    def read() -> None:
        while not done.is_set():
            try:
                seen.add(os.readlink(link))
            except FileNotFoundError:
                missing.append(1)

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for n in range(400):
            service_macos.point_current(new if n % 2 == 0 else old)
    finally:
        done.set()
        reader.join()
    assert missing == [] and seen <= {old.name, new.name} and os.readlink(link) == old.name
    assert sorted(p.name for p in tmp_path.iterdir()) == [old.name, new.name, "current"]  # no temporary link left


# --- The service host as launchd sees it: SIGTERM, and its exit codes -------------------------------------------------

HOST = """
import sys
from pathlib import Path
from ap_coder.ops import service
service.STOP_WAIT = 1.0
service.serve_command = lambda s, python: [python, sys.argv[2], sys.argv[3]]
sys.exit(service.run_host(Path(sys.argv[1])))
"""
DASHBOARD = """
import os, signal, sys, time
from pathlib import Path
way = sys.argv[1]
if way == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
elif way == "kind":
    signal.signal(signal.SIGTERM, lambda *a: (print("stopping", flush=True), sys.exit(0)))
signal.signal(signal.SIGINT, signal.SIG_IGN)  # (as the dashboard's own Ctrl+C would be: the host decides)
Path(__file__).with_suffix(".pid").write_text(str(os.getpid()))
print("dashboard up", flush=True)
if way == "crashes":
    sys.exit(3)
while True:
    time.sleep(0.05)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize(("sent", "way"), [(signal.SIGTERM, "kind"), (signal.SIGTERM, "stubborn"),
                                           (signal.SIGINT, "kind"), (signal.SIGTERM, "crashes")])  # fmt: skip
def test_sigterm_or_sigint_ends_the_dashboard_then_the_host_with_exit_code_0(data, tmp_path, sent, way):
    """launchd's bootout sends the host SIGTERM (Ctrl+C, SIGINT): the host asks its dashboard to stop, ends it
    STOP_WAIT later when it won't (stubborn), and ends with 0, which KeepAlive leaves stopped. Between two starts of a
    dashboard that crashed, it ends at once."""
    (data / "server.json").write_text(json.dumps({"insecure_http": True}), encoding="utf-8")
    script = tmp_path / "dashboard.py"
    script.write_text(DASHBOARD, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    host = subprocess.Popen([sys.executable, "-c", HOST, str(data), str(script), way], cwd=ROOT, env=env)  # noqa: S603
    try:
        pid_file = script.with_suffix(".pid")
        deadline = time.monotonic() + 30
        while not pid_file.exists() and time.monotonic() < deadline and host.poll() is None:
            time.sleep(0.05)
        assert pid_file.exists(), "the dashboard did not start"
        dashboard = int(pid_file.read_text())
        log = data / "logs" / "service.log"
        while "dashboard: dashboard up" not in (log.read_text(encoding="utf-8") if log.exists() else ""):
            assert time.monotonic() < deadline, "the host did not read the dashboard's output"
            time.sleep(0.05)
        if way == "crashes":
            while "starting it again" not in log.read_text(encoding="utf-8"):
                assert time.monotonic() < deadline
                time.sleep(0.05)
        sent_at = time.monotonic()
        host.send_signal(sent)
        assert host.wait(timeout=30) == 0
        took = time.monotonic() - sent_at
    finally:
        if host.poll() is None:
            host.kill()
    assert not _alive(dashboard)
    text = (data / "logs" / "service.log").read_text(encoding="utf-8")
    if way == "crashes":
        assert "The service was stopped" in text and took < 4  # not after the 5 s wait
    elif way == "kind":  # it stopped when asked (SIGTERM), on its own
        assert "dashboard: stopping" in text
        assert f"(exit code 0): the host was asked to stop ({signal.Signals(sent).name})" in text
    else:  # it ignored SIGTERM: ended (SIGKILL) STOP_WAIT, 1 s here, after
        assert f"(exit code -9): the host was asked to stop ({signal.Signals(sent).name})" in text and took >= 1.0
    assert not service.host_running(data) and not service.host_record(data).exists()


def test_the_host_ends_with_0_only_when_stopped_on_purpose(data):
    """KeepAlive (SuccessfulExit false) goes by the host's exit code: 0 for a stop on purpose (the ``stopped`` file,
    SIGTERM above), HOST_AGAIN while another host runs (launchd tries again), and a crash is no 0."""
    (data / "server.json").write_text(json.dumps({"insecure_http": True}), encoding="utf-8")
    service.write_stop_flag(data)
    assert service.run_host(data, popen=lambda *a, **k: pytest.fail("started"), sleep=lambda s: None) == 0
    service.stop_flag(data).unlink()
    held = paths.try_lock(service.host_lock(data))
    try:
        assert service.run_host(data, popen=lambda *a, **k: pytest.fail("started")) == service.HOST_AGAIN
    finally:
        held.close()
    crash = subprocess.run([sys.executable, "-c", "from ap_coder.ops import service; service.host_logger = None; "
                            "import sys, pathlib; sys.exit(service.run_host(pathlib.Path(sys.argv[1])))", str(data)],
                           cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True,
                           timeout=60)  # fmt: skip
    assert crash.returncode not in (0, service.HOST_AGAIN)  # a host that broke: launchd starts it again


def test_a_host_that_must_be_started_again_never_ends_with_0_on_a_mac(data, monkeypatch):
    """``service.again()`` is what the host ends with to be started again soon: another host runs, or an update's
    recovery at its start (D-188) has switched the folder to run, so the host the ``current`` link now names must
    start. On a Mac never 0: launchd's KeepAlive (SuccessfulExit false) leaves a host that ended with 0 stopped. On
    Windows 0, as before: its task comes back every 5 minutes, through run.cmd."""
    monkeypatch.setattr(service, "WINDOWS", False)
    assert service.again() == service.HOST_AGAIN != 0
    s = service.settings(data, account="_apcoder", insecure_http=True)
    assert service_macos.daemon(s, data)["KeepAlive"] == {"SuccessfulExit": False}  # (an exit but 0: started again)
    monkeypatch.setattr(service, "WINDOWS", True)
    assert service.again() == 0


class PowerCut(OSError):
    """The Mac stopped here."""


def test_the_plist_and_the_current_link_are_never_left_cut_short(mac, monkeypatch):
    """Cut before its rename, the plist and the ``current`` link are the old ones, whole, and nothing of the new ones
    is left; the plist is flushed to the disk before it takes the name, the link's folder once it is renamed."""
    assert install(mac, "--no-start") == 0
    before, link = mac.plist.read_bytes(), mac.folder.parent / "current"
    new = mac.folder.parent / "APProcessor-1.5.0"
    new.mkdir()
    done: list[str] = []

    def cut(src, dst):
        done.append("rename")
        raise PowerCut("the power went off")

    with monkeypatch.context() as power:
        power.setattr(os, "fsync", lambda fd: done.append("fsync"))
        power.setattr(os, "replace", cut)
        with pytest.raises(PowerCut):
            service_macos.write_plist("AP Coder", {"Label": "com.apcoder.server"})
        assert done[:2] == ["fsync", "rename"]
        with pytest.raises(PowerCut):
            service_macos.point_current(new)
    assert mac.plist.read_bytes() == before and os.readlink(link) == mac.folder.name
    left = [p.name for p in (*mac.plist.parent.iterdir(), *link.parent.iterdir()) if p.name.endswith(".part")]
    assert left == []
    synced: list[Path] = []
    monkeypatch.setattr(service, "sync_folder", synced.append)
    service_macos.point_current(new)
    assert os.readlink(link) == new.name and synced == [link.parent]


# --- CI's check on GitHub's macOS runner: scripts/ci/macos_service.py -------------------------------------------------

CI_SCRIPT = ROOT / "scripts" / "ci" / "macos_service.py"
needs_ci_script = pytest.mark.skipif(not CI_SCRIPT.is_file(), reason="no scripts/ci in this copy (an offline bundle)")


def _ci():
    spec = importlib.util.spec_from_file_location("ap_ci_macos_service", CI_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeMac:
    """The Mac CI's script drives, as its commands find it: the daemon installed, its host's and dashboard's
    processes, launchd starting a killed host again, and ``status --json`` answering as AP Coder's does."""

    def __init__(self, plist: Path, misbehave: str = "") -> None:
        self.plist, self.misbehave, self.ran = plist, misbehave, []
        self.host = self.dashboard = 0
        self.stopped_at = 0.0
        self.asked: dict[str, int] = {}  # kill -0 of each process: how many times

    def __call__(self, cmd, timeout=300):
        self.ran.append(cmd)
        out, code = "", 0
        if "service" in cmd:
            action = cmd[cmd.index("service") + 1]
            if action == "install":
                self.plist.write_bytes(plistlib.dumps({"Label": "com.apcoder.ci"}))
                self.host, self.dashboard = 600, 601
            elif action in ("stop", "remove"):
                self.host = self.dashboard = 0
                self.stopped_at = time.monotonic()
                if action == "remove":
                    self.plist.unlink(missing_ok=True)
            elif action == "start" and self.host:  # running: kickstart -k, a new host (the old one asked to stop)
                kept = self.misbehave == "keeps the old dashboard"
                self.host, self.dashboard = self.host + 10, self.dashboard + (0 if kept else 10)
            elif action == "start":
                self.host, self.dashboard = 700, 701
            elif action == "status":
                if self.misbehave == "comes back" and self.stopped_at and self.plist.exists():
                    self.host, self.dashboard = 800, 801
                up = bool(self.host)
                out = json.dumps({"installed": self.plist.exists(), "plist": str(self.plist), "running": up,
                                  "pid": self.host or None, "answering": up, "dashboard_pid": self.dashboard or None,
                                  "health": {"app": "ap-coder"} if up else None,
                                  "state": "running" if up else "not loaded"})  # fmt: skip
        elif cmd[:3] == ["sudo", "kill", "-9"]:
            if self.misbehave != "stays dead":
                self.host, self.dashboard = self.host + 1, self.dashboard + 1  # launchd: a new host, its dashboard
            else:
                self.host = self.dashboard = 0
        elif cmd[:3] == ["sudo", "kill", "-0"]:  # the old dashboard: gone (a moment later, or never, if so told)
            asked = self.asked[cmd[3]] = self.asked.get(cmd[3], 0) + 1
            late = self.misbehave == "the old dashboard ends a few seconds later" and asked <= 2
            code = 0 if late or self.misbehave == "the old dashboard never ends" else 1
        elif cmd[:3] == ["sudo", "launchctl", "bootout"]:  # launchd's SIGTERM: the host ends its dashboard, then itself
            self.host = self.dashboard = 0
            self.stopped_at = time.monotonic()
        elif cmd[:4] == ["sudo", "launchctl", "bootstrap", "system"]:  # the Mac's next start: the host deals with it
            if self.misbehave != "stays stopped after an update cut short":
                self.host, self.dashboard = 900, 901
        elif cmd[:3] == ["sudo", "test", "-e"]:  # the update's marker: removed once dealt with
            code = 0 if self.misbehave == "keeps the marker of an update cut short" else 1
        elif cmd[0] == "grep":
            code = 1 if self.misbehave == "no SIGTERM in its log" else 0
        elif cmd[:2] == ["plutil", "-lint"]:
            code = 1 if self.misbehave == "a plist launchd does not read" else 0
        return subprocess.CompletedProcess(cmd, code, out, "")


def _ci_run(tmp_path, monkeypatch, misbehave: str) -> tuple[int, FakeMac]:
    ci = _ci()
    mac = FakeMac(tmp_path / "com.apcoder.ci.plist", misbehave)
    clock = [0.0]
    monkeypatch.setattr(ci, "run", mac)
    monkeypatch.setattr(ci, "clock", lambda: clock[0])
    monkeypatch.setattr(ci, "pause", lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, 0.5)))
    return ci.main(["--data", str(tmp_path / "data"), "--port", "8597", "--account", "runner"]), mac


@needs_ci_script
@pytest.mark.parametrize("misbehave", ["", "stays dead", "keeps the old dashboard", "no SIGTERM in its log",
                                       "comes back", "the old dashboard never ends",
                                       "stays stopped after an update cut short",
                                       "keeps the marker of an update cut short",
                                       "a plist launchd does not read"])  # fmt: skip
def test_the_ci_script_passes_a_service_that_behaves_and_fails_one_that_does_not(tmp_path, monkeypatch, misbehave):
    code, mac = _ci_run(tmp_path, monkeypatch, misbehave)
    assert code == (0 if not misbehave else 1)
    commands = [" ".join(c) for c in mac.ran]
    assert any("service install --account runner --insecure-http --host 127.0.0.1 --port 8597" in c
               for c in commands)  # fmt: skip
    assert all("--task-name com.apcoder.ci" in c for c in commands if " service " in c)  # never the real daemon
    assert not mac.plist.exists()  # removed at the end, failed or not
    if misbehave:
        assert any(c.startswith("sudo tail -n 80") for c in commands)  # the logs, for the failure
    if not misbehave:  # an update cut short, as root's sudo leaves it: its marker, then the Mac's next start
        assert any("update_recovery" in c and "safety-copy" in c for c in commands)
        assert commands.index(f"sudo launchctl bootstrap system {mac.plist}") > max(
            i for i, c in enumerate(commands) if "update_recovery" in c)  # fmt: skip


@needs_ci_script
def test_the_ci_script_gives_the_old_dashboard_a_while_to_end(tmp_path, monkeypatch):
    """launchd ends a killed host's processes as it starts the new one: the old dashboard can still be there a moment
    after the new one answers. It was looked at once, right then, and the job failed now and again (B-1959)."""
    code, mac = _ci_run(tmp_path, monkeypatch, "the old dashboard ends a few seconds later")
    assert code == 0 and max(mac.asked.values()) == 3


def test_the_hosts_switch_is_asked_of_the_links_folder(tmp_path, monkeypatch):
    """The Mac's host, as the service's account, can't change /Library/APCoder (root's): it is asked first, so the
    host never puts a database back and then fails at the link (B-1957)."""
    asked = []
    monkeypatch.setattr(os, "access", lambda path, mode: asked.append((Path(path), mode)) or False)
    folder = tmp_path / "Library" / "APCoder" / "APProcessor-1.4.0"
    assert service_macos.can_switch(tmp_path / "data", folder) is False
    assert asked == [(folder.parent, os.W_OK)]
