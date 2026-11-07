"""An update of the company server cut short at each of its steps (a power cut), then dealt with at the next
``server-update`` and at the service's next start (``ops/update_recovery.py``, D-188): the database and the folder
the service runs end right, whichever step it was.

A power cut is a ``PowerCut`` raised as a step begins (once its marker is written) or inside it: no rollback code of
``server-update`` runs after it, as none would after the power went off.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ap_coder.core.store import Store
from ap_coder.ops import server_update, service, update_recovery
from tests.ops import test_server_update as base
from tests.ops.test_server_update import FakeServer, _bundle, _install, _marker

server = base.server  # the fixture: a data folder served by APProcessor (0.3.0), its database marked
NEW, OLD_DB, NEW_DB = "APProcessor-0.4.0", "as before the update", "changed by the new version"


class PowerCut(BaseException):
    """The power going off: nothing of server-update runs after it."""


class Service(FakeServer):
    """The fake service, which leaves the ``stopped`` file as ``service stop`` does and removes it at its start."""

    def __init__(self, data: Path, up_ok: bool = True, cut_in: str = "") -> None:
        super().__init__(up_ok=up_ok)
        self.data, self.cut_in = data, cut_in

    def stop(self) -> bool:
        service.stop_flag(self.data).parent.mkdir(parents=True, exist_ok=True)
        service.stop_flag(self.data).write_text("stopped")
        return super().stop()

    def start(self) -> bool:
        service.stop_flag(self.data).unlink(missing_ok=True)
        super().start()
        if self.cut_in == "start":  # the new version started, the power off before it answered
            raise PowerCut
        return True

    def migrate(self, folder: Path, db: Path) -> bool:
        super().migrate(folder, db)
        if self.cut_in == "migrate" and self.calls[-1] == "migrate":  # the real database, half updated
            raise PowerCut
        return True


def _cut_at(monkeypatch, step: str) -> None:
    """The power goes off as ``step`` begins (its marker written)."""
    real = update_recovery.write

    def write(data: Path, state: dict) -> None:
        real(data, state)
        if state["step"] == step:
            monkeypatch.setattr(update_recovery, "write", real)
            raise PowerCut

    monkeypatch.setattr(update_recovery, "write", write)


def _cut(server, monkeypatch, step: str = "", cut_in: str = "") -> dict:
    """An update cut short: the marker it left."""
    if step:
        _cut_at(monkeypatch, step)
    with pytest.raises(PowerCut):
        _install(_bundle(server["tmp"]), server["data"], server["db"], server=Service(server["data"], cut_in=cut_in))
    state = update_recovery.read(server["data"])
    assert state["step"] == (step or cut_in) and state["from"] == "0.3.0" and state["to"] == "0.4.0"
    return state


def _runs(server) -> str:
    """The folder the service runs (server.json and run.cmd agree)."""
    folder = Path(service.read_config(server["data"])["folder"])
    assert str(folder) in service.runner_path(server["data"]).read_text()
    return folder.name


# Each step, what is left (the database, the folder run) once dealt with.
STEPS = [("prepare", "", OLD_DB, "APProcessor"), ("stop", "", OLD_DB, "APProcessor"),
         ("safety-copy", "", OLD_DB, "APProcessor"), ("migrate", "", OLD_DB, "APProcessor"),
         ("", "migrate", OLD_DB, "APProcessor"), ("switch", "", NEW_DB, NEW), ("start", "", NEW_DB, NEW),
         ("", "start", NEW_DB, NEW), ("going-back", "", OLD_DB, "APProcessor")]  # fmt: skip


@pytest.mark.parametrize(("step", "cut_in", "db", "folder"), STEPS)
def test_an_update_cut_short_at_each_step_is_dealt_with_at_the_next_server_update(server, monkeypatch, capsys, step,
                                                                                 cut_in, db, folder):  # fmt: skip
    if step == "going-back":  # the new version did not answer, and the power went off as it went back
        monkeypatch.setattr(Service, "up", lambda self, f: self.calls.append(f"up {f.name}") or f.name != NEW)
    state = _cut(server, monkeypatch, step, cut_in)
    capsys.readouterr()
    fake = Service(server["data"])
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 0
    out = capsys.readouterr().out
    assert "was cut short" in out and "Done: AP Coder" in out
    assert _marker(server["db"]) == db and _runs(server) == folder
    assert not update_recovery.marker_path(server["data"]).exists()
    assert not update_recovery.check_folder(server["data"]).exists()
    assert not list((server["data"] / "backups").glob("*.part"))
    assert not service.stop_flag(server["data"]).exists()  # the service runs again
    saved = service.read_config(server["data"])
    if folder == NEW:  # finished: --back can still go back to the version before
        assert saved["previous_folder"] == state["from_folder"] and Path(saved["pre_update_backup"]).is_file()
        assert saved["history"][-1]["result"].startswith("updated: finished after it was cut short")
    else:
        assert "previous_folder" not in saved
        assert saved["history"][-1]["result"].startswith("not updated: cut short")
    if step == "prepare":  # the old version was never stopped: it is left serving
        assert fake.calls == []
    else:
        assert fake.calls == ["stop", "start", f"up {folder}"]
    # The next update goes through as any other.
    assert _install(_bundle(server["tmp"], "0.5.0"), server["data"], server["db"], server=Service(server["data"])) == 0


def test_a_finished_update_whose_new_version_does_not_answer_goes_back(server, monkeypatch, capsys):
    _cut(server, monkeypatch, "start")
    fake = Service(server["data"], up_ok=False)
    monkeypatch.setattr(Service, "up", lambda self, f: self.calls.append(f"up {f.name}") or f.name != NEW)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 0
    assert fake.calls == ["stop", "start", f"up {NEW}", "stop", "start", "up APProcessor"]
    assert _marker(server["db"]) == OLD_DB and _runs(server) == "APProcessor"
    assert "does not answer: going back" in capsys.readouterr().out


def test_the_dry_run_says_what_it_would_do_with_an_update_cut_short_and_does_nothing(server, monkeypatch, capsys):
    _cut(server, monkeypatch, "migrate")
    capsys.readouterr()
    fake = Service(server["data"])
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "while the new version updated the database" in out and "safety copy" in out and "dry run" in out
    assert fake.calls == [] and update_recovery.read(server["data"])["step"] == "migrate"


def test_back_deals_with_an_update_cut_short_first(server, monkeypatch, capsys):
    _cut(server, monkeypatch, "", cut_in="migrate")
    assert server_update.go_back(server["data"], server["db"], server=Service(server["data"]), yes=True) == 0
    assert _marker(server["db"]) == OLD_DB and _runs(server) == "APProcessor"
    assert "Run server-update again" in capsys.readouterr().out


def test_a_marker_naming_a_folder_elsewhere_is_never_acted_on_by_the_administrator(server, monkeypatch, capsys):
    """The marker is in the data folder, which the service's account can write (D-109): the administrator's
    server-update switches only to a folder beside its own."""
    state = _cut(server, monkeypatch, "switch")
    update_recovery.write(server["data"], {**state, "to_folder": str(server["tmp"] / "elsewhere" / "APProcessor")})
    fake = Service(server["data"])
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls == [] and update_recovery.read(server["data"])
    assert "not beside this AP Coder folder" in capsys.readouterr().err


def test_an_update_under_way_is_never_taken_for_one_cut_short(server, monkeypatch, capsys):
    """While server-update runs it holds update.lock: neither a second server-update nor the service's start (the
    task's trigger every 5 minutes, while it is stopped) acts on its marker."""
    state = _cut(server, monkeypatch, "", cut_in="migrate")
    with update_recovery.update_lock(server["data"]) as held:
        assert held is not None
        assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=Service(server["data"])) == 1
        assert update_recovery.recover_at_start(server["data"], server["db"], Path(state["from_folder"])) is None
    assert "Another server-update runs" in capsys.readouterr().err
    assert _marker(server["db"]) == NEW_DB and update_recovery.read(server["data"])  # untouched


# --- At the service's next start (the host, as the scheduled task runs it after the power came back) -------------

HOST = [("prepare", "", "from", OLD_DB, "APProcessor", True), ("stop", "", "from", OLD_DB, "APProcessor", True),
        ("safety-copy", "", "from", OLD_DB, "APProcessor", True), ("", "migrate", "from", OLD_DB, "APProcessor", True),
        ("switch", "", "from", OLD_DB, "APProcessor", True), ("start", "", "to", NEW_DB, NEW, True),
        ("", "start", "to", NEW_DB, NEW, True), ("going-back", "", "to", OLD_DB, "APProcessor", False)]  # fmt: skip


@pytest.mark.parametrize(("step", "cut_in", "host", "db", "folder", "starts"), HOST)
def test_an_update_cut_short_at_each_step_is_dealt_with_at_the_services_next_start(server, monkeypatch, step, cut_in,
                                                                                  host, db, folder,
                                                                                  starts):  # fmt: skip
    """The host runs the folder run.cmd named when the power came back: the old one until the switch, the new one
    after. It acts on the marker before anything starts; a folder it must not run is left to the task's next start."""
    if step == "going-back":
        monkeypatch.setattr(Service, "up", lambda self, f: self.calls.append(f"up {f.name}") or f.name != NEW)
    state = _cut(server, monkeypatch, step, cut_in)
    here = Path(state[f"{host}_folder"])
    left = update_recovery.recover_at_start(server["data"], server["db"], here)
    assert left is None if starts else left == service.again()  # (None: the dashboard starts)
    assert _marker(server["db"]) == db and _runs(server) == folder
    assert not update_recovery.marker_path(server["data"]).exists()
    if step != "prepare":
        assert not service.stop_flag(server["data"]).exists()  # the update's own stop, undone
    log = service.log_path(server["data"]).read_text(encoding="utf-8")
    assert "was cut short" in log and "Done:" in log


@pytest.mark.parametrize("by", ["server-update", "the host"])
def test_a_safety_copy_cut_short_leaves_none_of_its_files_once_dealt_with(server, monkeypatch, by):
    """B-2035: a power cut while the safety copy was written leaves SQLite's files beside its ``.part`` (its
    ``-journal``; a copy made ahead, D-196, its ``-wal`` and ``-shm`` too). Dealing with the cut removed the ``.part``
    only: the rest stayed in the backups folder for good."""
    state = _cut(server, monkeypatch, "safety-copy")
    part = server["data"] / "backups" / f"ap_coder-20261107-101500-{state['label']}.db.part"
    left = [part, *(part.with_name(part.name + s) for s in ("-journal", "-wal", "-shm"))]
    for path in left:  # (as the power cut left them)
        path.write_bytes(b"SQLite format 3\x00 cut short")
    if by == "server-update":
        assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=Service(server["data"])) == 0
    else:
        assert update_recovery.recover_at_start(server["data"], server["db"], Path(state["from_folder"])) is None
    assert [p.name for p in left if p.exists()] == []
    assert _marker(server["db"]) == OLD_DB and _runs(server) == "APProcessor"


def test_the_host_starts_the_dashboard_once_it_dealt_with_an_update_cut_short(server, monkeypatch):
    """``service run``: the update's own ``stopped`` file no longer keeps the service down after a power cut."""
    _cut(server, monkeypatch, "", cut_in="migrate")
    assert service.stop_flag(server["data"]).exists()
    monkeypatch.setattr(service, "PROJECT_DIR", server["current"])
    started = []

    class Child:
        stdout, pid = iter([]), 4242

        def wait(self, timeout=None):
            service.stop_flag(server["data"]).write_text("")
            return 0

    def popen(cmd, **kwargs):
        started.append(cmd)
        return Child()

    assert service.run_host(server["data"], popen=popen, sleep=lambda s: None) == 0
    assert len(started) == 1 and _marker(server["db"]) == OLD_DB


def test_a_damaged_marker_is_not_guessed_around(server):
    path = update_recovery.marker_path(server["data"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"step": "migr', encoding="utf-8")  # (written whole and renamed: only by hand)
    assert update_recovery.read(server["data"]) == {}
    assert update_recovery.recover_at_start(server["data"], server["db"], server["current"]) is None
    assert not path.exists() and _marker(server["db"]) == OLD_DB


def test_the_marker_is_written_whole_at_each_step(server, monkeypatch):
    seen = []
    real = update_recovery.write
    monkeypatch.setattr(update_recovery, "write", lambda data, state: seen.append(dict(state)) or real(data, state))
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=Service(server["data"])) == 0
    assert [s["step"] for s in seen] == ["prepare", "stop", "safety-copy", "migrate", "switch", "start"]
    assert seen[0]["safety_copy"] == "" and Path(seen[-1]["safety_copy"]).is_file()
    assert {"from", "to", "from_folder", "to_folder", "database", "started", "label"} <= set(seen[-1])
    assert not list(update_recovery.service_folder(server["data"]).glob("*.part"))
    json.dumps(seen)  # (plain JSON)
    assert Store(server["db"]).get_setting("marker") == NEW_DB


# --- On a Mac: the host runs as the service's account; the `current` link and root's sudo files are not its ---------


def _host_cannot_switch(monkeypatch) -> list:
    """The Mac's host, as _apcoder: the `current` link's folder (/Library/APCoder) is root's. Each switch tried is
    recorded, and refused as os.symlink there would be."""
    tried = []

    def refused(data: Path, folder: Path) -> None:
        tried.append(Path(folder).name)
        raise PermissionError(13, "Permission denied", str(Path(folder).parent / ".current.123.part"))

    monkeypatch.setattr(service, "switch_folder", refused)
    monkeypatch.setattr(service, "can_switch", lambda data, folder: False)
    return tried


def _run_host(server, monkeypatch, here: Path) -> tuple[int, list]:
    """``service run`` of ``here`` (the folder the link named as launchd started it): its exit code, and the
    dashboards it started."""
    monkeypatch.setattr(service, "PROJECT_DIR", here)
    started = []

    class Child:
        stdout, pid = iter([]), 4242

        def wait(self, timeout=None):
            service.stop_flag(server["data"]).write_text("")
            return 0

    def popen(cmd, **kwargs):
        started.append(cmd)
        return Child()

    return service.run_host(server["data"], popen=popen, sleep=lambda s: None), started


def test_mac_cut_at_the_safety_copy_the_old_version_starts_without_a_switch(server, monkeypatch):
    """Cut while the safety copy was made (the update's `stopped` file there): the old version starts. The host ran
    the old folder already, yet it switched to it and was refused (B-1957): it kept the `stopped` file and ended
    with 0, so launchd left the service stopped for good."""
    state = _cut(server, monkeypatch, "safety-copy")
    service.stop_flag(server["data"]).write_text("stopped")  # (service stop's, made just before this step)
    tried = _host_cannot_switch(monkeypatch)
    code, started = _run_host(server, monkeypatch, Path(state["from_folder"]))
    assert started, f"the dashboard never started (exit code {code}); launchd leaves a 0 stopped"
    assert tried == [] and not update_recovery.marker_path(server["data"]).exists()
    assert _marker(server["db"]) == OLD_DB and _runs(server) == "APProcessor"


def test_mac_cut_at_migrate_is_put_back_once_and_the_old_version_starts(server, monkeypatch):
    """Cut during the migration: the safety copy is put back once and the old version starts. Each start put the
    whole database back, failed at the link, ended with 75, and launchd started it again 10 s later: for ever."""
    state = _cut(server, monkeypatch, "", cut_in="migrate")
    _host_cannot_switch(monkeypatch)
    calls = []
    real = server_update.put_back
    monkeypatch.setattr(server_update, "put_back", lambda b, d: calls.append(b.name) or real(b, d))
    for _ in range(3):  # three starts by launchd's KeepAlive
        code, started = _run_host(server, monkeypatch, Path(state["from_folder"]))
        if started:
            break
    assert _marker(server["db"]) == OLD_DB
    assert started and len(calls) == 1, f"put back {len(calls)} times, dashboard started: {bool(started)}"


def test_mac_root_update_lock_never_crashes_the_host(server, monkeypatch):
    """update.lock made by root's `sudo server-update --dry-run` (no service start since to hand it back): the host
    as _apcoder can't open it. It raised PermissionError, launchd started it again every 10 s, and the dashboard never
    started, even for a cut while preparing (the old version untouched) (B-1956). Which can't be told now: nothing is
    changed, the log says to run server-update, and the host goes on as it would without a marker."""
    state = _cut(server, monkeypatch, "prepare")

    def root_owned(path: Path):
        if path.name == update_recovery.LOCK:
            raise PermissionError(13, "Permission denied", str(path))
        return base.paths.try_lock(path)

    monkeypatch.setattr(update_recovery, "try_lock", root_owned)
    code, started = _run_host(server, monkeypatch, Path(state["from_folder"]))
    assert started
    assert update_recovery.read(server["data"])["step"] == "prepare"  # kept: server-update deals with it
    log = service.log_path(server["data"]).read_text(encoding="utf-8")
    assert "can't be told" in log and "server-update" in log


def test_mac_root_update_lock_after_the_stop_leaves_it_stopped_for_server_update(server, monkeypatch):
    """The same after the stop: the database may be half updated, so nothing is touched and the update's own
    `stopped` file keeps the service stopped (exit 0: launchd leaves it) until the administrator's server-update."""
    state = _cut(server, monkeypatch, "", cut_in="migrate")
    monkeypatch.setattr(update_recovery, "try_lock", lambda path: (_ for _ in ()).throw(PermissionError(13, "no")))
    code, started = _run_host(server, monkeypatch, Path(state["from_folder"]))
    assert (code, started) == (0, []) and service.stop_flag(server["data"]).exists()
    assert _marker(server["db"]) == NEW_DB and update_recovery.read(server["data"])["step"] == "migrate"


def test_mac_a_put_back_the_account_may_not_make_leaves_it_stopped_for_server_update(server, monkeypatch):
    """The backups folder made by root's sudo (a new server's first backup was the update's own): the host, as the
    service's account, can't take its lock to put the safety copy back. It ended with 75, and launchd started it
    again every 10 s for ever; now it stays stopped (0), and the log says to run server-update."""
    state = _cut(server, monkeypatch, "", cut_in="migrate")
    refused = PermissionError(13, "Permission denied", str(server["data"] / "backups" / ".lock"))
    monkeypatch.setattr(server_update, "put_back", lambda b, d: (_ for _ in ()).throw(refused))
    code, started = _run_host(server, monkeypatch, Path(state["from_folder"]))
    assert (code, started) == (0, []) and service.stop_flag(server["data"]).exists()
    assert update_recovery.read(server["data"])["step"] == "migrate" and _marker(server["db"]) == NEW_DB
    assert "server-update again" in service.log_path(server["data"]).read_text(encoding="utf-8")


def test_mac_cut_while_it_went_back_is_left_to_server_update_changing_nothing(server, monkeypatch):
    """Cut while it went back, the link still on the new folder: the host must run the old one, which it can't
    switch to. It changes nothing (no database put back under the new version), keeps the marker, says to run
    server-update and ends with 0 (launchd leaves it stopped); the administrator's server-update then goes back."""
    monkeypatch.setattr(Service, "up", lambda self, f: self.calls.append(f"up {f.name}") or f.name != NEW)
    state = _cut(server, monkeypatch, "going-back")
    real_switch, real_can = service.switch_folder, service.can_switch
    tried = _host_cannot_switch(monkeypatch)
    code, started = _run_host(server, monkeypatch, Path(state["to_folder"]))
    assert (code, started, tried) == (0, [], [])
    assert _marker(server["db"]) == NEW_DB and update_recovery.read(server["data"])["step"] == "going-back"
    log = service.log_path(server["data"]).read_text(encoding="utf-8")
    assert "nothing was changed" in log and "server-update" in log
    monkeypatch.setattr(service, "switch_folder", real_switch)  # the administrator's sudo: it can switch
    monkeypatch.setattr(service, "can_switch", real_can)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=Service(server["data"])) == 0
    assert _marker(server["db"]) == OLD_DB and _runs(server) == "APProcessor"


def test_a_put_back_is_never_made_twice_when_what_follows_it_fails(server, monkeypatch):
    """Going back (run.cmd still names the new folder): the safety copy is put back, then the switch fails (a full
    disk). The marker says it was put back, so the next start only switches (B-1957)."""
    monkeypatch.setattr(Service, "up", lambda self, f: self.calls.append(f"up {f.name}") or f.name != NEW)
    state = _cut(server, monkeypatch, "going-back")
    calls = []
    real_put_back, real_switch = server_update.put_back, service.switch_folder
    monkeypatch.setattr(server_update, "put_back", lambda b, d: calls.append(b.name) or real_put_back(b, d))
    monkeypatch.setattr(service, "switch_folder", lambda d, f: (_ for _ in ()).throw(OSError(28, "No space left")))
    here = Path(state["to_folder"])
    assert update_recovery.recover_at_start(server["data"], server["db"], here) == service.again()
    assert update_recovery.read(server["data"])["put_back"] and _marker(server["db"]) == OLD_DB
    Store(server["db"]).set_setting("marker", "the old version's: never put back again")
    monkeypatch.setattr(service, "switch_folder", real_switch)
    assert update_recovery.recover_at_start(server["data"], server["db"], here) == service.again()
    assert len(calls) == 1 and _runs(server) == "APProcessor"
    assert _marker(server["db"]) == "the old version's: never put back again"
    assert not update_recovery.marker_path(server["data"]).exists()


def test_server_update_going_back_after_a_recovery_keeps_a_marker_while_it_goes_back(server, monkeypatch):
    """server-update finishes an update cut short, the new version does not answer, and it goes back: the marker
    was removed before that, so a power cut while it went back left nothing saying so (B-1958)."""
    _cut(server, monkeypatch, "start")
    seen = []

    class Watched(Service):
        def stop(self) -> bool:
            seen.append(update_recovery.read(self.data).get("step"))
            return super().stop()

    monkeypatch.setattr(Watched, "up", lambda self, f: self.calls.append(f"up {f.name}") or f.name != NEW)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=Watched(server["data"])) == 0
    assert seen == ["start", "going-back"]  # the second stop: going back, said in the marker
    assert _marker(server["db"]) == OLD_DB and not update_recovery.marker_path(server["data"]).exists()


def test_roots_sudo_gives_the_marker_and_the_lock_to_the_service_folders_owner(server, monkeypatch):
    """On a Mac, `sudo server-update` made update.lock and update.json root's in the service's folder: the host, as
    the service's account, could not open the lock after a power cut (B-1956). Made by root, they are the folder
    owner's from the start (on the open file: never what a link there points at)."""
    from ap_coder.ops import service_macos

    folder = update_recovery.service_folder(server["data"])
    folder.mkdir(parents=True, exist_ok=True)
    account = folder.stat()
    monkeypatch.setattr(service_macos, "is_root", lambda: True)
    monkeypatch.setattr(service_macos, "ROOT_UID", account.st_uid + 1)  # the folder stands for the account's
    handed = []
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: handed.append((os.fstat(fd).st_ino, uid)), raising=False)
    with update_recovery.update_lock(server["data"]) as held:
        assert held is not None
        update_recovery.write(server["data"], {"step": "prepare"})
    lock, marker = folder / update_recovery.LOCK, update_recovery.marker_path(server["data"])
    assert {(lock.stat().st_ino, account.st_uid), (marker.stat().st_ino, account.st_uid)} <= set(handed)
    handed.clear()
    with update_recovery.update_lock(server["data"]):  # there already: left as it is
        pass
    assert handed == []
    monkeypatch.setattr(service_macos, "is_root", lambda: False)  # the host itself, or a laptop: nothing handed
    update_recovery.write(server["data"], {"step": "stop"})
    assert handed == []


# --- The safety copy made ahead (D-196): the power off while it is made, brought up to date, or after it --------------

LATE = "written while it was copied ahead"


class LastWork(Service):
    """The old version serving on while the safety copy is made ahead, its last work done just before it stops."""

    def stop(self) -> bool:
        if "stop" not in self.calls:
            Store(self.data / "ap_coder.db").set_setting("marker", LATE)
        return super().stop()


def _cut_in_the_copy(server, monkeypatch, where: str) -> dict:
    """The power off inside the safety copy made ahead: ``copy`` (half the file copied, the old version writing on),
    ``catch-up`` (the log copied beside it once stopped), or after it (``migrate``). What a power cut leaves on the
    disk stays (nothing removes it); the hold on the database and the backups' lock go with the process."""
    from ap_coder.core.store import backups

    monkeypatch.setattr(backups.CopyAhead, "_remove_part", lambda self: None)
    if where == "copy":

        def half(source: Path, dest: Path, size: int, page: int) -> None:
            dest.write_bytes(source.read_bytes()[: size // 2])
            Store(server["db"]).set_setting("marker", LATE)  # (the old version, serving on)
            raise PowerCut

        monkeypatch.setattr(backups, "_copy_as_is", half)
    elif where == "catch-up":
        real = backups.CopyAhead._let_go

        def cut(self) -> None:
            real(self)
            if backups._beside(self.part, "-wal").exists():
                monkeypatch.setattr(backups.CopyAhead, "_let_go", real)
                raise PowerCut

        monkeypatch.setattr(backups.CopyAhead, "_let_go", cut)
    with pytest.raises(PowerCut):
        _install(_bundle(server["tmp"]), server["data"], server["db"],
                 server=LastWork(server["data"], cut_in="migrate" if where == "migrate" else ""))  # fmt: skip
    monkeypatch.setattr(backups.CopyAhead, "_remove_part", REAL["remove"])  # (as the computer started again runs it)
    monkeypatch.setattr(backups, "_copy_as_is", REAL["copy"])
    state = update_recovery.read(server["data"])
    assert state["step"] == {"copy": "prepare", "catch-up": "safety-copy", "migrate": "migrate"}[where]
    return state


def _real() -> dict:
    from ap_coder.core.store import backups

    return {"remove": backups.CopyAhead._remove_part, "copy": backups._copy_as_is}


REAL = _real()


@pytest.mark.parametrize("by", ["server-update", "the host"])
@pytest.mark.parametrize("where", ["copy", "catch-up", "migrate"])
def test_an_update_cut_short_in_its_safety_copy_made_ahead_loses_nothing(server, monkeypatch, where, by):
    """Cut while the copy was made ahead (the old version serving) or brought up to date (stopped): the old version
    starts on its own database, its last work there, and nothing of the copy is left. Cut while the new version
    migrated: the safety copy is put back, and it is the database as it was the moment the service stopped, the old
    version's last work in it (D-188's promise, kept by D-196)."""
    state = _cut_in_the_copy(server, monkeypatch, where)
    backups = server["data"] / "backups"
    if where != "migrate":  # (all a power cut may leave of the copy made ahead)
        name = backups / f"ap_coder-20261107-101500-{state['label']}.db"
        for path in [name.with_name(name.name + s) for s in (".part", ".part-wal", ".part-shm", ".files.json")]:
            path.write_bytes(b"left by the power cut")
    fake = Service(server["data"])
    if by == "server-update":
        assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 0
        assert fake.calls == ([] if where == "copy" else ["stop", "start", "up APProcessor"])
    else:
        assert update_recovery.recover_at_start(server["data"], server["db"], Path(state["from_folder"])) is None
        assert where == "copy" or not service.stop_flag(server["data"]).exists()
    assert _marker(server["db"]) == LATE and _runs(server) == "APProcessor"
    assert [p.name for p in backups.iterdir() if ".db." in p.name] == []
    assert not update_recovery.marker_path(server["data"]).exists()
    assert _install(_bundle(server["tmp"], "0.5.0"), server["data"], server["db"], server=Service(server["data"])) == 0
