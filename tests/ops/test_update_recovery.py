"""An update of the company server cut short at each of its steps (a power cut), then dealt with at the next
``server-update`` and at the service's next start (``ops/update_recovery.py``, D-188): the database and the folder
the service runs end right, whichever step it was.

A power cut is a ``PowerCut`` raised as a step begins (once its marker is written) or inside it: no rollback code of
``server-update`` runs after it, as none would after the power went off.
"""

from __future__ import annotations

import json
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
        assert update_recovery.recover_at_start(server["data"], server["db"], Path(state["from_folder"])) is True
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
    assert update_recovery.recover_at_start(server["data"], server["db"], here) is starts
    assert _marker(server["db"]) == db and _runs(server) == folder
    assert not update_recovery.marker_path(server["data"]).exists()
    if step != "prepare":
        assert not service.stop_flag(server["data"]).exists()  # the update's own stop, undone
    log = service.log_path(server["data"]).read_text(encoding="utf-8")
    assert "was cut short" in log and "Done:" in log


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
    assert update_recovery.recover_at_start(server["data"], server["db"], server["current"]) is True
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
