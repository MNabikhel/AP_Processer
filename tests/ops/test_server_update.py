"""Updating AP Coder on the company server (D-100): ``python -m ap_coder server-update <bundle.zip>`` and ``--back``.

The Windows parts (the service, the new folder's setup by its launcher, its self-check) are stood in for by a fake
server: what is checked here is the order of the steps, the database backup and its restore, the folder switch and
the record kept in server.json.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import zipfile
from pathlib import Path

import pytest

from ap_coder.core import paths
from ap_coder.core.store import Store, backup_files
from ap_coder.ops import cli, server_update, service

GOOD = "correct horse battery"


def _folder(path: Path, version: str) -> Path:
    (path / "ap_coder").mkdir(parents=True)
    (path / "ap_coder" / "__init__.py").write_text("")
    (path / "scripts").mkdir()
    (path / "scripts" / "launch.py").write_text("")
    (path / "pyproject.toml").write_text(f'[project]\nname = "ap-coder"\nversion = "{version}"\n')
    return path


def _bundle(tmp_path: Path, version: str = "0.4.0", extra: dict | None = None) -> Path:
    dest = tmp_path / f"APProcessor-offline-{version}.zip"
    files = {"ap_coder/__init__.py": "", "scripts/launch.py": "", "README.md": "AP Coder",
             "pyproject.toml": f'[project]\nname = "ap-coder"\nversion = "{version}"\n'}  # fmt: skip
    manifest = {"version": version, "built": "2026-11-01T10:00:00", "files": dict.fromkeys(files, "x")}
    with zipfile.ZipFile(dest, "w") as zf:
        for name, text in {**files, "bundle_manifest.json": json.dumps(manifest), **(extra or {})}.items():
            zf.writestr(name if name.startswith("../") else f"APProcessor/{name}", text)
    return dest


def _install(bundle: Path, data: Path, db: Path, **kwargs) -> int:
    """``server-update`` with the bundle's SHA-256, as IT is given it apart from the ZIP (D-109)."""
    return server_update.update(bundle, data, db, sha256=server_update.file_sha256(bundle), **kwargs)


class FakeServer(server_update.Server):
    """The Windows service, the launcher's setup and the self-check, recorded."""

    def __init__(self, check_ok: bool = True, up_ok: bool = True) -> None:
        self.calls: list[str] = []
        self.check_ok, self.up_ok = check_ok, up_ok

    def installed(self) -> bool:
        return True

    def stop(self) -> bool:
        self.calls.append("stop")
        return True

    def start(self) -> bool:
        self.calls.append("start")
        return True

    def up(self, folder: Path) -> bool:
        self.calls.append(f"up {folder.name}")
        return self.up_ok

    def set_up(self, folder: Path) -> bool:
        self.calls.append(f"set up {folder.name}")
        return True

    def migrate(self, folder: Path, db: Path) -> bool:
        self.calls.append("migrate copy" if "update-check" in db.parts else "migrate")
        Store(db).set_setting("marker", "changed by the new version")
        return True

    def self_check(self, folder: Path) -> tuple[bool, str]:
        self.calls.append("self-check")
        return self.check_ok, "Self-check OK" if self.check_ok else "Self-check FAILED: FAIL sample.pdf"


@pytest.fixture
def server(tmp_path, monkeypatch):
    data = tmp_path / "APCoderData"
    data.mkdir()
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    current = _folder(tmp_path / "apps" / "APProcessor", "0.3.0")
    service.write_config(data, folder=str(current), insecure_http=True)
    service.write_runner(data, current)
    # The administrator runs server-update from the folder the service runs (D-109).
    monkeypatch.setattr(server_update, "running_folder", lambda: service.settings(data).code_folder)
    db = data / "ap_coder.db"
    Store(db).set_setting("marker", "as before the update")
    return {"data": data, "current": current, "db": db, "tmp": tmp_path}


def _marker(db: Path) -> str:
    return Store(db).get_setting("marker")


def test_dry_run_prints_the_steps_and_changes_nothing(server, capsys):
    bundle = _bundle(server["tmp"])
    code = _install(bundle, server["data"], server["db"], server=FakeServer(), dry_run=True)
    out = capsys.readouterr().out
    assert code == 0
    for step in ("Check the bundle", "Stop the service", "Back up the database", "Unzip", "Update the database",
                 "Self-check", "Copy the database", "Start the service"):  # fmt: skip
        assert step in out, step
    assert "before-update-0.4.0" in out and "APProcessor-0.4.0" in out and "Nothing was changed (dry run)" in out
    assert not (server["current"].parent / "APProcessor-0.4.0").exists()
    assert not (server["data"] / "backups").exists()


def test_an_update_backs_up_switches_folders_and_keeps_the_previous_one(server, capsys):
    fake = FakeServer()
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 0
    new = server["current"].parent / "APProcessor-0.4.0"
    # Prepared while the old version serves (a copy of the database updated first), then stopped (D-188).
    assert fake.calls == ["set up APProcessor-0.4.0", "migrate copy", "self-check", "stop", "migrate", "start",
                          "up APProcessor-0.4.0"]  # fmt: skip
    assert (new / "README.md").read_text() == "AP Coder" and server["current"].is_dir()
    assert not (server["data"] / "service" / "update-check").exists()  # the copy, removed
    assert not (server["data"] / "service" / "update.json").exists()  # the marker, once recorded
    saved = service.read_config(server["data"])
    assert saved["folder"] == str(new) and saved["previous_folder"] == str(server["current"])
    backup = Path(saved["pre_update_backup"])
    assert backup.is_file() and backup.name.endswith("-before-update-0.4.0.db")
    assert saved["history"][-1]["result"] == "updated" and saved["history"][-1]["to"] == "0.4.0"
    times = saved["history"][-1]["times"]  # the downtime, from the stop to the answer, and its parts
    assert {"prepare_s", "check_migration_s", "self_check_s", "stop_s", "safety_copy_s", "migrate_s", "start_s",
            "downtime_s"} <= set(times)  # fmt: skip
    assert str(new) in service.runner_path(server["data"]).read_text()
    assert paths.current_install()["folder"] == str(new)  # recorded as the laptop updater does
    out = capsys.readouterr().out
    assert "server-update --back" in out and "Downtime:" in out and "kept serving" in out


def test_an_update_whose_record_cant_be_written_says_so_and_it_is_recorded_next_time(server, monkeypatch, capsys):
    """server.json held open by another process (Windows) as the update, done and answering, wrote its record: a
    traceback, the marker left, and no word of what had happened (B-1962). It says the new version runs and the record
    is to come; the marker is kept, and the next server-update records it (so --back works)."""
    from ap_coder.ops import update_recovery

    real = server_update._record
    held = PermissionError(13, "The process cannot access the file: another process uses it", "server.json")
    monkeypatch.setattr(server_update, "_record", lambda *a, **k: (_ for _ in ()).throw(held))
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 1
    out = capsys.readouterr().out
    assert "AP Coder 0.4.0 runs" in out and "server-update again" in out and "record" in out
    assert update_recovery.read(server["data"])["step"] == "start"
    monkeypatch.setattr(server_update, "_record", real)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
    saved = service.read_config(server["data"])
    assert saved["previous_folder"] == str(server["current"]) and Path(saved["pre_update_backup"]).is_file()
    assert not update_recovery.marker_path(server["data"]).exists()


@pytest.mark.parametrize("task", ["installed before", "installed now", "none (a Mac)"])
def test_the_folder_two_versions_back_is_offered_for_deletion_once_the_task_no_longer_starts_in_it(server, capsys,
                                                                                                    task):  # fmt: skip
    """A task an older ``service install`` registered starts in the folder it was installed from: deleted as the
    second update said, Task Scheduler could no longer start it (error 267) (B-1961). Then the message says to
    install the service again first; a task installed now starts in run.cmd's folder: delete it."""
    from xml.sax.saxutils import escape

    first = server["current"]
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
    xml = service.task_xml(service.settings(server["data"]), service.runner_path(server["data"]))
    if task == "installed before":
        xml = xml.replace(escape(str(service.runner_path(server["data"]).parent)), escape(str(first)))
    if task != "none (a Mac)":
        service.task_file(server["data"]).write_text(xml, encoding="utf-16")
    capsys.readouterr()
    assert _install(_bundle(server["tmp"], "0.5.0"), server["data"], server["db"], server=FakeServer()) == 0
    out = capsys.readouterr().out
    assert f"{first} (two versions back) is no longer needed" in out
    if task == "installed before":
        assert "starts in it" in out and "service install again" in out and "delete it to free" not in out
    else:
        assert "delete it to free its space" in out and "starts in it" not in out


def test_a_failed_self_check_stops_nothing_and_leaves_the_database_as_it_was(server, capsys):
    """D-188: the self-check runs before the stop, the migration on a copy: a failure leaves the old version
    serving on its own database, untouched."""
    fake = FakeServer(check_ok=False)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls == ["set up APProcessor-0.4.0", "migrate copy", "self-check"]  # never stopped
    assert _marker(server["db"]) == "as before the update"  # only the copy was migrated
    saved = service.read_config(server["data"])
    assert saved["folder"] == str(server["current"]) and "previous_folder" not in saved
    assert saved["history"][-1]["result"].startswith("not updated: The self-check failed")
    assert str(server["current"]) in service.runner_path(server["data"]).read_text()
    assert not (server["data"] / "service" / "update.json").exists()
    assert not list((server["data"] / "backups").glob("*before-update*"))  # no safety copy needed
    out = capsys.readouterr().out
    assert "FAIL sample.pdf" in out and "AP Coder 0.3.0 kept serving" in out


def test_a_new_version_that_does_not_answer_is_rolled_back_too(server):
    fake = FakeServer(up_ok=False)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls[-4:] == ["up APProcessor-0.4.0", "stop", "start", "up APProcessor"]
    assert _marker(server["db"]) == "as before the update"
    assert service.read_config(server["data"])["folder"] == str(server["current"])


def test_a_rollback_whose_files_cannot_be_put_back_still_starts_the_old_version(server, monkeypatch, capsys):
    """A locked file while the update's rollback put the files back: the error left the server stopped (B-1565)."""

    def locked(*args, **kwargs):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(backup_files, "restore_files", locked)
    fake = FakeServer(up_ok=False)  # (since D-188 a failed self-check stops nothing: the new version not answering)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls[-2:] == ["start", "up APProcessor"]  # the old version, started again
    assert _marker(server["db"]) == "as before the update"
    assert service.read_config(server["data"])["folder"] == str(server["current"])
    assert "being used by another process" in capsys.readouterr().out


def test_back_never_runs_while_a_backup_is_being_made(server, capsys):
    """B-1559: one backup or restore at a time, the update's put-back too."""
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
    Store(server["db"]).set_setting("marker", "work done on the new version")
    import threading

    held, done = threading.Event(), threading.Event()

    def backing_up():  # (another thread, as another process making a backup: the same lock)
        with backup_files.exclusive(server["data"]):
            held.set()
            done.wait(20)

    other = threading.Thread(target=backing_up)
    other.start()
    held.wait(10)
    try:
        fake = FakeServer()
        assert server_update.go_back(server["data"], server["db"], server=fake, yes=True) == 1
    finally:
        done.set()
        other.join(10)
    assert fake.calls == ["stop", "start", "up APProcessor-0.4.0"]  # the version that ran, started again
    assert _marker(server["db"]) == "work done on the new version"
    assert "being made or restored" in capsys.readouterr().out


def test_back_restores_the_pre_update_backup_and_the_previous_folder(server, capsys):
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
    Store(server["db"]).set_setting("marker", "work done on the new version")
    fake = FakeServer()
    assert server_update.go_back(server["data"], server["db"], server=fake, yes=True) == 0
    assert fake.calls == ["stop", "start", "up APProcessor"]
    assert _marker(server["db"]) == "as before the update"  # the old version refuses the newer database
    saved = service.read_config(server["data"])
    assert saved["folder"] == str(server["current"]) and not saved.get("previous_folder")
    assert str(server["current"]) in service.runner_path(server["data"]).read_text()
    safety = list((server["data"] / "backups").glob("ap_coder-*-before-going-back.db"))
    assert len(safety) == 1  # what was done since the update is kept in a copy
    assert safety[0].name in capsys.readouterr().out
    assert server_update.go_back(server["data"], server["db"], server=FakeServer(), yes=True) == 1
    assert "Nothing to go back to" in capsys.readouterr().err


def test_back_puts_back_the_invoice_files_of_the_pre_update_backup(server, capsys):
    """The pre-update backup held the database only: going back brought back no invoice file lost since (D-143)."""
    invoices = server["data"] / "invoices"
    invoices.mkdir()
    (invoices / "a.pdf").write_bytes(b"%PDF-1.4 a")
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
    (invoices / "a.pdf").unlink()  # lost on the new version
    (invoices / "new.pdf").write_bytes(b"%PDF-1.4 came in on the new version")
    assert server_update.go_back(server["data"], server["db"], server=FakeServer(), yes=True) == 0
    assert (invoices / "a.pdf").read_bytes() == b"%PDF-1.4 a" and not (invoices / "new.pdf").exists()
    (aside,) = (server["data"] / "backups").glob("set-aside-*")
    assert (aside / "invoices" / "new.pdf").read_bytes() == b"%PDF-1.4 came in on the new version"
    (kept,) = (server["data"] / "backups").glob("ap_coder-*-before-going-back.db")
    assert "invoices/new.pdf" in [e["path"] for e in backup_files.read_list(kept)["files"]]  # it can be undone too
    assert "1 file added or changed since moved to backups" in capsys.readouterr().out


def test_back_dry_run_changes_nothing(server, capsys):
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
    fake = FakeServer()
    assert server_update.go_back(server["data"], server["db"], server=fake, dry_run=True) == 0
    assert fake.calls == [] and "Nothing was changed (dry run)" in capsys.readouterr().out
    assert _marker(server["db"]) == "changed by the new version"


@pytest.mark.parametrize("version", ["0.3.0", "0.2.0"])
def test_the_same_or_an_older_version_is_refused_before_anything_stops(server, version, capsys):
    fake = FakeServer()
    assert _install(_bundle(server["tmp"], version), server["data"], server["db"], server=fake) == 1
    assert fake.calls == [] and "--back" in capsys.readouterr().err


def test_a_bundle_with_a_path_out_of_its_folder_is_refused(server, capsys):
    bundle = _bundle(server["tmp"], extra={"../evil.txt": "x"})
    assert _install(bundle, server["data"], server["db"], server=FakeServer()) == 1
    assert "not an AP Coder offline bundle" in capsys.readouterr().err
    assert not (server["tmp"] / "apps" / "evil.txt").exists()


def test_a_file_that_is_not_a_zip_is_refused(server, capsys):
    bogus = server["tmp"] / "bundle.zip"
    bogus.write_text("not a zip")
    assert _install(bogus, server["data"], server["db"], server=FakeServer()) == 1
    assert "bundle.zip" in capsys.readouterr().err


def test_the_command_line(server, monkeypatch):
    seen = []
    monkeypatch.setattr(server_update, "update", lambda bundle, data, db, **k: seen.append((bundle, k)) or 0)
    monkeypatch.setattr(server_update, "go_back", lambda data, db, **k: seen.append(("back", k)) or 0)
    assert cli.main(["server-update", "C:\\b.zip", "--dry-run"]) == 0
    assert cli.main(["server-update", "--back", "--yes"]) == 0
    assert seen[0][0] == Path("C:\\b.zip") and seen[0][1]["dry_run"] is True
    assert seen[1][0] == "back" and seen[1][1]["yes"] is True
    assert cli.main(["server-update"]) == 2  # neither a bundle nor --back


def test_the_task_server_update_stops_and_starts_is_never_server_jsons(server, monkeypatch):
    """B-1586: server.json is in the data folder, which the service's account can write (B-1500). server-update took
    the task's name from it, so the administrator's schtasks queried, ended and ran whatever task it named (Windows'
    Defrag task). It names the task as ``service install|start|stop`` do: the command line's, else "AP Coder"."""
    service.write_config(server["data"], task_name="\\Microsoft\\Windows\\Defrag\\ScheduledDefrag")
    ran: list[list[str]] = []

    def run(cmd, **kwargs):
        ran.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0, "Status: Ready\n", "")

    monkeypatch.setattr(service, "WINDOWS", True)
    monkeypatch.setattr(service, "run_command", run)
    monkeypatch.setattr(service, "dashboard_stopped", lambda data, settings, wait=45.0: True)
    # The service's own Server (no fake one given), with the new folder's setup, its checks and the dashboard
    # answering stood in for.
    monkeypatch.setattr(server_update.Server, "set_up", lambda self, folder: True)
    monkeypatch.setattr(server_update.Server, "migrate", lambda self, folder, db: True)
    monkeypatch.setattr(server_update.Server, "self_check", lambda self, folder: (True, "Self-check OK"))
    monkeypatch.setattr(server_update.Server, "up", lambda self, folder: True)
    bundle = _bundle(server["tmp"])
    assert _install(bundle, server["data"], server["db"]) == 0
    assert server_update.go_back(server["data"], server["db"], yes=True) == 0
    assert {cmd[1] for cmd in ran} == {"/query", "/end", "/run"}
    assert {cmd[cmd.index("/tn") + 1] for cmd in ran} == {"AP Coder"}
    ran.clear()  # another task only when the command line names it
    sha = server_update.file_sha256(bundle)
    assert cli.main(["server-update", str(bundle), "--sha256", sha, "--dry-run", "--task-name", "AP Coder test"]) == 0
    assert ran == [["schtasks", "/query", "/tn", "AP Coder test"]]


class NotInstalled(FakeServer):
    """No scheduled task of the name server-update was given; whether a dashboard answers asked as the real one."""

    def __init__(self, data: Path) -> None:
        super().__init__()
        self.data, self.settings = data, server_update.server_settings(data)

    def installed(self) -> bool:
        return False


@pytest.mark.parametrize("sign", ["server.json names it", "the dashboard answers"])
def test_a_service_installed_under_another_name_is_not_updated_without_that_name(server, monkeypatch, capsys, sign):
    """B-1672: since B-1586 server-update names the task "AP Coder" unless --task-name names another. A service
    installed under another name was taken for none: the live server's database was updated and its folder switched
    under it, the service never stopped. With no task of that name installed while server.json names another or the
    dashboard answers, it refuses, says to pass --task-name, and changes nothing; --back too."""
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0  # one to go back
    if sign == "server.json names it":
        service.write_config(server["data"], task_name="AP Coder Prod")  # as service install --task-name wrote it
        monkeypatch.setattr(service, "dashboard_health", lambda *a, **k: None)
    else:
        monkeypatch.setattr(service, "dashboard_health", lambda *a, **k: {"app": "ap-coder", "folder": "x"})
    saved, marker = service.read_config(server["data"]), _marker(server["db"])
    capsys.readouterr()
    newer, fake = _bundle(server["tmp"], "0.5.0"), NotInstalled(server["data"])
    assert _install(newer, server["data"], server["db"], server=fake, dry_run=True) == 1
    assert _install(newer, server["data"], server["db"], server=fake) == 1
    assert server_update.go_back(server["data"], server["db"], server=fake, yes=True) == 1
    assert server_update.go_back(server["data"], server["db"], server=fake, dry_run=True) == 1
    assert fake.calls == [] and service.read_config(server["data"]) == saved and _marker(server["db"]) == marker
    assert not (server["current"].parent / "APProcessor-0.5.0").exists()
    assert capsys.readouterr().err.count("pass --task-name <name>") == 4


# --- The security review (D-101) --------------------------------------------------------------------------------------


def _accounts(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT username, active, pw_hash FROM users ORDER BY username").fetchall()


def test_back_keeps_the_accounts_and_the_lockouts_and_ends_every_session(server, capsys):
    store = Store(server["db"])
    boss = store.add_user("boss", GOOD, role="admin")
    fired = store.add_user("fired", GOOD)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
    token = store.start_session(fired)
    store.deactivate_user(fired, actor="boss")
    store.reset_password(boss, actor="boss", password="brand new secret 1")
    with sqlite3.connect(server["db"]) as conn:
        conn.execute("INSERT INTO login_attempts (username, ip, at, ok, user_locked_until) VALUES (?, ?, ?, 0, ?)",
                     ("boss", "10.3.3.3", 1.0, 9e12))  # fmt: skip
    kept = _accounts(server["db"])
    assert server_update.go_back(server["data"], server["db"], server=FakeServer(), yes=True) == 0
    assert _accounts(server["db"]) == kept and _marker(server["db"]) == "as before the update"
    with sqlite3.connect(server["db"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM login_attempts WHERE user_locked_until > 0").fetchone()[0] == 1
    assert Store(server["db"]).session_user(token) is None
    assert "accounts" in capsys.readouterr().out


def test_a_backup_without_the_accounts_tables_never_leaves_the_server_with_none(server):
    store = Store(server["db"])
    backup = store.backup_now("older")
    with sqlite3.connect(backup) as conn:  # a version before the accounts
        conn.executescript("DROP TABLE users; DROP TABLE sessions; DROP TABLE login_attempts;")
    store.add_user("boss", GOOD, role="admin")
    store.start_session(store.find_user("boss")["id"])
    server_update.put_back(backup, server["db"])
    assert [u[0] for u in _accounts(server["db"])] == ["boss"]
    with sqlite3.connect(server["db"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_a_failed_update_puts_the_backup_back_with_the_current_accounts(server):
    store = Store(server["db"])
    uid = store.add_user("boss", GOOD, role="admin")
    token = store.start_session(uid)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"],
                                server=FakeServer(up_ok=False)) == 1  # fmt: skip
    assert Store(server["db"]).session_user(token) is None and Store(server["db"]).find_user("boss")


def test_the_bundle_is_checked_and_unzipped_from_a_private_copy(server, monkeypatch):
    bundle = _bundle(server["tmp"])
    (server["tmp"] / "other").mkdir()
    swapped = _bundle(server["tmp"] / "other", extra={"evil.txt": "swapped after the check"})
    checked, unzipped = [], []
    real_check, real_unzip = server_update.check_bundle, server_update.unzip

    def check(path: Path) -> dict:
        checked.append(path)
        found = real_check(path)
        bundle.write_bytes(swapped.read_bytes())  # the file given is changed once it was checked
        return found

    def unzip(path: Path, top: str, dest: Path) -> None:
        unzipped.append(path)
        real_unzip(path, top, dest)

    monkeypatch.setattr(server_update, "check_bundle", check)
    monkeypatch.setattr(server_update, "unzip", unzip)
    assert _install(bundle, server["data"], server["db"], server=FakeServer()) == 0
    assert checked == unzipped and checked[0] != bundle
    assert not (server["current"].parent / "APProcessor-0.4.0" / "evil.txt").exists()
    assert not checked[0].exists()  # the private copy is removed afterwards


class RaisingServer(FakeServer):
    """A step that raises instead of answering: the new folder's Python blocked (antivirus, AppLocker), or Ctrl+C."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    def migrate(self, folder: Path, db: Path) -> bool:
        super().migrate(folder, db)
        if self.calls[-1] == ("migrate copy" if self.on_copy else "migrate"):
            raise self.error
        return True

    on_copy = False


@pytest.mark.parametrize("error", [PermissionError(13, "Access is denied"), KeyboardInterrupt()])
def test_a_step_that_raises_still_puts_the_old_version_back_and_starts_it(server, error, capsys):
    fake = RaisingServer(error)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls[-2:] == ["start", "up APProcessor"]  # the old version, started again
    assert _marker(server["db"]) == "as before the update"  # the half-migrated database put back
    saved = service.read_config(server["data"])
    assert saved["folder"] == str(server["current"]) and saved["history"][-1]["result"].startswith("rolled back")
    assert "went back to 0.3.0" in capsys.readouterr().out


@pytest.mark.parametrize("error", [PermissionError(13, "Access is denied"), KeyboardInterrupt()])
def test_a_step_that_raises_while_preparing_stops_nothing(server, error, capsys):
    fake = RaisingServer(error)
    fake.on_copy = True
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls == ["set up APProcessor-0.4.0", "migrate copy"]  # never stopped
    assert _marker(server["db"]) == "as before the update"
    assert not (server["data"] / "service" / "update-check").exists()  # the half-migrated copy removed
    saved = service.read_config(server["data"])
    assert saved["folder"] == str(server["current"]) and saved["history"][-1]["result"].startswith("not updated")
    assert "kept serving" in capsys.readouterr().out


def test_a_backup_that_fails_starts_the_service_again_and_changes_nothing(server, monkeypatch, capsys):
    def full(db, label, ahead=None, hashed=True):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(server_update, "safety_copy", full)
    fake = FakeServer()
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    # Prepared beforehand, then stopped: the backup failed, so the old version was started again.
    assert fake.calls == ["set up APProcessor-0.4.0", "migrate copy", "self-check", "stop", "start", "up APProcessor"]
    assert _marker(server["db"]) == "as before the update"
    assert service.read_config(server["data"])["folder"] == str(server["current"])
    assert "No space left on device" in capsys.readouterr().out


def test_no_room_for_a_copy_of_the_database_is_said_before_anything_is_done(server, monkeypatch, capsys):
    from ap_coder.core.store import backups

    monkeypatch.setattr(backups, "_room_refusal", lambda database, folder: "not enough free space for a backup")
    fake = FakeServer()
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls == [] and not (server["current"].parent / "APProcessor-0.4.0").exists()
    assert "not enough free space" in capsys.readouterr().out


def test_back_says_the_work_lost_since_the_update_it_undoes_not_since_a_later_failed_one(server, capsys):
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
    assert _install(_bundle(server["tmp"], "0.5.0"), server["data"], server["db"],
                    server=FakeServer(check_ok=False)) == 1  # fmt: skip
    history = service.read_config(server["data"])["history"]
    assert [h["result"][:7] for h in history] == ["updated", "not upd"]
    history[0]["at"], history[1]["at"] = "2026-11-02T09:00:00", "2026-11-20T16:00:00"
    service.write_config(server["data"], history=history)
    capsys.readouterr()
    assert server_update.go_back(server["data"], server["db"], server=FakeServer(), dry_run=True) == 0
    out = capsys.readouterr().out
    assert "since 2026-11-02T09:00:00" in out and "2026-11-20" not in out


def _updated(server) -> None:
    """Updated to 0.4.0, then work done on it: what --back puts back, or keeps when it can't."""
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
    Store(server["db"]).set_setting("marker", "work done on the new version")


@pytest.mark.parametrize("error", [PermissionError(13, "Access is denied"), KeyboardInterrupt()])
def test_back_that_raises_before_the_backup_is_put_back_starts_the_running_version_again(server, error, monkeypatch,
                                                                                         capsys):  # fmt: skip
    _updated(server)

    def half(backup, db):  # the backup copied in, then the accounts could not be carried over
        server_update.copy_database(backup, db)
        raise error

    monkeypatch.setattr(server_update, "put_back", half)
    fake = FakeServer()
    assert server_update.go_back(server["data"], server["db"], server=fake, yes=True) == 1
    assert fake.calls == ["stop", "start", "up APProcessor-0.4.0"]  # the version that ran, started again
    assert _marker(server["db"]) == "work done on the new version"  # the database as it was
    saved = service.read_config(server["data"])
    assert saved["folder"].endswith("APProcessor-0.4.0") and saved["previous_folder"] == str(server["current"])
    assert "APProcessor-0.4.0" in service.runner_path(server["data"]).read_text()
    out = capsys.readouterr().out
    assert "did not go back" in out and "runs 0.4.0 again" in out


def test_back_whose_safety_copy_fails_starts_the_running_version_again(server, monkeypatch, capsys):
    _updated(server)

    def full(db, label):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(server_update, "safety_copy", full)
    fake = FakeServer()
    assert server_update.go_back(server["data"], server["db"], server=fake, yes=True) == 1
    assert fake.calls == ["stop", "start", "up APProcessor-0.4.0"]
    assert _marker(server["db"]) == "work done on the new version"
    assert service.read_config(server["data"])["previous_folder"] == str(server["current"])  # --back can be tried again
    out = capsys.readouterr().out
    assert "No space left on device" in out and "runs 0.4.0 again" in out


def test_back_interrupted_once_the_backup_is_put_back_starts_the_restored_version(server, monkeypatch, capsys):
    _updated(server)
    record = server_update._record

    def interrupted(data, entry, **values):
        if entry.get("result") == "went back":
            monkeypatch.setattr(server_update, "_record", record)  # the next try goes through
            raise KeyboardInterrupt
        record(data, entry, **values)

    monkeypatch.setattr(server_update, "_record", interrupted)
    fake = FakeServer()
    assert server_update.go_back(server["data"], server["db"], server=fake, yes=True) == 1
    assert fake.calls == ["stop", "start", "up APProcessor"]  # the previous version, on its backup
    assert _marker(server["db"]) == "as before the update"
    saved = service.read_config(server["data"])
    assert saved["folder"] == str(server["current"]) and not saved.get("previous_folder")
    assert "Back on AP Coder 0.3.0" in capsys.readouterr().out


def test_a_safety_copy_cut_short_leaves_nothing_that_looks_like_a_backup(server, monkeypatch):
    """B-1955: ``safety_copy`` copied straight into ``backups/ap_coder-<time>-<label>.db``: a copy cut short (a full
    disk) left half a database there under a backup's name, listed and offered to restore. It is written under a
    temporary name and renamed once complete, as ``backup_to`` does (B-453)."""

    def full(source: Path, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"SQLite format 3\x00 half")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(server_update, "copy_database", full)
    with pytest.raises(OSError):
        server_update.safety_copy(server["db"], "before-going-back")
    assert list((server["data"] / "backups").glob("ap_coder-*")) == []


# --- The safety copy made ahead, while the old version serves (D-196) -------------------------------------------------


class LastWork(FakeServer):
    """The old version serving on while the safety copy is made ahead: people still work until it stops (a setting,
    a bulk of pages past the log's checkpoints, an invoice that came in)."""

    def __init__(self, db: Path, up_ok: bool = True) -> None:
        super().__init__(up_ok=up_ok)
        self.db = db

    def stop(self) -> bool:
        if "stop" not in self.calls:  # (the update's own stop)
            store = Store(self.db)
            store.set_setting("marker", "written while it was copied ahead")
            store.set_setting("bulk", "x" * 3_000_000)
            (self.db.parent / "invoices").mkdir(exist_ok=True)
            (self.db.parent / "invoices" / "late.pdf").write_bytes(b"%PDF-1.4 came in meanwhile")
        return super().stop()


@pytest.mark.parametrize("answers", [True, False])
def test_the_safety_copy_holds_what_was_done_while_it_was_made_ahead(server, answers, capsys):
    """D-196: the safety copy is begun while the old version serves, and brought up to date once it is stopped, so it
    is the database as it was the moment the service stopped (D-188's promise): an update goes back to that, and so
    does --back. Its list of files has the invoice that came in meanwhile, and the database's SHA-256 once it serves;
    nothing of the copy made ahead is left."""
    from ap_coder.core.store.backups import setting_in

    fake = LastWork(server["db"], up_ok=answers)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == (0 if answers else 1)
    assert fake.calls[:4] == ["set up APProcessor-0.4.0", "migrate copy", "self-check", "stop"]
    (backup,) = (server["data"] / "backups").glob("ap_coder-*-before-update-0.4.0.db")
    assert setting_in(backup, "marker") == "written while it was copied ahead"
    assert setting_in(backup, "bulk") == "x" * 3_000_000
    listed = backup_files.read_list(backup)
    assert "invoices/late.pdf" in [e["path"] for e in listed["files"]]
    assert sorted(p.name for p in (server["data"] / "backups").iterdir() if ".db." in p.name) == []  # (.part, list)
    times = service.read_config(server["data"])["history"][-1]["times"]
    assert times["copy_ahead_s"] >= 0 and times["safety_copy_s"] >= 0
    if answers:  # its SHA-256, read once the new version serves
        assert listed["database_sha256"] == backup_files._sha_size(backup)[0]
        assert _marker(server["db"]) == "changed by the new version"
        assert server_update.go_back(server["data"], server["db"], server=FakeServer(), yes=True) == 0
    assert _marker(server["db"]) == "written while it was copied ahead"  # as it was the moment it stopped
    assert "Copying the database ahead" in capsys.readouterr().out


def test_a_database_that_cant_be_held_still_is_copied_whole_once_stopped(server, monkeypatch, capsys):
    """A long report keeps part of the log from the database file for longer than the copy waits: the update goes
    through all the same, the safety copy made whole once the service is stopped, as before D-196 (said)."""
    from ap_coder.core.store import backups

    monkeypatch.setattr(backups.CopyAhead, "start", lambda self: "it was never still for a moment in 60 s")
    fake = LastWork(server["db"], up_ok=False)
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert _marker(server["db"]) == "written while it was copied ahead"  # rolled back to the database at the stop
    out = capsys.readouterr().out
    assert "could not be copied ahead (it was never still" in out and "copied whole once the service is stopped" in out


def test_a_copy_ahead_that_fails_stops_nothing_and_leaves_nothing(server, monkeypatch, capsys):
    from ap_coder.core.store import backups

    def full(source: Path, dest: Path, size: int, page: int) -> None:
        dest.write_bytes(b"SQLite format 3\x00 half")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backups, "_copy_as_is", full)
    fake = FakeServer()
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls == ["set up APProcessor-0.4.0", "migrate copy", "self-check"]  # never stopped
    assert _marker(server["db"]) == "as before the update"
    assert [p.name for p in (server["data"] / "backups").iterdir() if p.name != ".lock"] == []
    out = capsys.readouterr().out
    assert "No space left on device" in out and "AP Coder 0.3.0 kept serving" in out
    assert Store(server["db"]).backup_now("manual").exists()  # the backups' lock let go
