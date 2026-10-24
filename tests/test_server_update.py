"""Updating AP Coder on the company server (D-100): ``python -m ap_coder server-update <bundle.zip>`` and ``--back``.

The Windows parts (the service, the new folder's setup by its launcher, its self-check) are stood in for by a fake
server: what is checked here is the order of the steps, the database backup and its restore, the folder switch and
the record kept in server.json.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from ap_coder.core import paths
from ap_coder.core.store import Store
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
        self.calls.append("migrate")
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
    db = data / "ap_coder.db"
    Store(db).set_setting("marker", "as before the update")
    return {"data": data, "current": current, "db": db, "tmp": tmp_path}


def _marker(db: Path) -> str:
    return Store(db).get_setting("marker")


def test_dry_run_prints_the_steps_and_changes_nothing(server, capsys):
    bundle = _bundle(server["tmp"])
    code = server_update.update(bundle, server["data"], server["db"], server=FakeServer(), dry_run=True)
    out = capsys.readouterr().out
    assert code == 0
    for step in ("Check the bundle", "Stop the service", "Back up the database", "Unzip", "Update the database",
                 "Self-check", "Start the service"):  # fmt: skip
        assert step in out, step
    assert "before-update-0.4.0" in out and "APProcessor-0.4.0" in out and "Nothing was changed (dry run)" in out
    assert not (server["current"].parent / "APProcessor-0.4.0").exists()
    assert not (server["data"] / "backups").exists()


def test_an_update_backs_up_switches_folders_and_keeps_the_previous_one(server, capsys):
    fake = FakeServer()
    assert server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 0
    new = server["current"].parent / "APProcessor-0.4.0"
    assert fake.calls == ["stop", "set up APProcessor-0.4.0", "migrate", "self-check", "start", "up APProcessor-0.4.0"]
    assert (new / "README.md").read_text() == "AP Coder" and server["current"].is_dir()
    saved = service.read_config(server["data"])
    assert saved["folder"] == str(new) and saved["previous_folder"] == str(server["current"])
    backup = Path(saved["pre_update_backup"])
    assert backup.is_file() and backup.name.endswith("-before-update-0.4.0.db")
    assert saved["history"][-1]["result"] == "updated" and saved["history"][-1]["to"] == "0.4.0"
    assert str(new) in service.runner_path(server["data"]).read_text()
    assert paths.current_install()["folder"] == str(new)  # recorded as the laptop updater does
    assert "server-update --back" in capsys.readouterr().out


def test_a_failed_self_check_restores_the_backup_and_starts_the_old_version(server, capsys):
    fake = FakeServer(check_ok=False)
    assert server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls[-2:] == ["start", "up APProcessor"]  # the old version, started again
    assert "start" not in fake.calls[:-2]
    assert _marker(server["db"]) == "as before the update"  # the migrated database put back
    saved = service.read_config(server["data"])
    assert saved["folder"] == str(server["current"]) and "previous_folder" not in saved
    assert saved["history"][-1]["result"].startswith("rolled back")
    assert str(server["current"]) in service.runner_path(server["data"]).read_text()
    out = capsys.readouterr().out
    assert "FAIL sample.pdf" in out and "went back to 0.3.0" in out


def test_a_new_version_that_does_not_answer_is_rolled_back_too(server):
    fake = FakeServer(up_ok=False)
    assert server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls[-4:] == ["up APProcessor-0.4.0", "stop", "start", "up APProcessor"]
    assert _marker(server["db"]) == "as before the update"
    assert service.read_config(server["data"])["folder"] == str(server["current"])


def test_back_restores_the_pre_update_backup_and_the_previous_folder(server, capsys):
    assert server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
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


def test_back_dry_run_changes_nothing(server, capsys):
    assert server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
    fake = FakeServer()
    assert server_update.go_back(server["data"], server["db"], server=fake, dry_run=True) == 0
    assert fake.calls == [] and "Nothing was changed (dry run)" in capsys.readouterr().out
    assert _marker(server["db"]) == "changed by the new version"


@pytest.mark.parametrize("version", ["0.3.0", "0.2.0"])
def test_the_same_or_an_older_version_is_refused_before_anything_stops(server, version, capsys):
    fake = FakeServer()
    assert server_update.update(_bundle(server["tmp"], version), server["data"], server["db"], server=fake) == 1
    assert fake.calls == [] and "--back" in capsys.readouterr().err


def test_a_bundle_with_a_path_out_of_its_folder_is_refused(server, capsys):
    bundle = _bundle(server["tmp"], extra={"../evil.txt": "x"})
    assert server_update.update(bundle, server["data"], server["db"], server=FakeServer()) == 1
    assert "not an AP Coder offline bundle" in capsys.readouterr().err
    assert not (server["tmp"] / "apps" / "evil.txt").exists()


def test_a_file_that_is_not_a_zip_is_refused(server, capsys):
    bogus = server["tmp"] / "bundle.zip"
    bogus.write_text("not a zip")
    assert server_update.update(bogus, server["data"], server["db"], server=FakeServer()) == 1
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


# --- The security review (D-101) --------------------------------------------------------------------------------------


def _accounts(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT username, active, pw_hash FROM users ORDER BY username").fetchall()


def test_back_keeps_the_accounts_and_the_lockouts_and_ends_every_session(server, capsys):
    store = Store(server["db"])
    boss = store.add_user("boss", GOOD, role="admin")
    fired = store.add_user("fired", GOOD)
    assert server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer()) == 0
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
    assert server_update.update(_bundle(server["tmp"]), server["data"], server["db"],
                                server=FakeServer(check_ok=False)) == 1  # fmt: skip
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
    assert server_update.update(bundle, server["data"], server["db"], server=FakeServer()) == 0
    assert checked == unzipped and checked[0] != bundle
    assert not (server["current"].parent / "APProcessor-0.4.0" / "evil.txt").exists()
    assert not checked[0].exists()  # the private copy is removed afterwards
