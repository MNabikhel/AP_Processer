"""Updating over an older AP Coder (scripts/upgrade.py, the record in ap_coder/paths.py): one install, always the
newest; the older one's leftovers, .venv and folder are cleaned up; data kept in a code folder moves to the data
folder; nothing is duplicated and the data folder's contents are never touched."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from ap_coder import paths

ROOT = Path(__file__).resolve().parent.parent


def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"ap_script_{name}_update", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def upgrade():
    return _script("upgrade")


def make_install(folder: Path, version: str, built: str = "", files: bool = False, venv: bool = True,
                 extra: tuple[str, ...] = ()) -> Path:  # fmt: skip
    """An AP Coder folder as an offline bundle unzips it (``built``: its build time), set up (``venv``); ``extra``:
    more files of the bundle (e.g. its wheels)."""
    for rel in ("ap_coder/__init__.py", "scripts/launch.py", "tests/test_a.py", "APProcessor.bat",
                "APProcessor.command", "private/README.md", "README.md", *extra):  # fmt: skip
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(f"# {rel} {version}\n")
    (folder / "pyproject.toml").write_text(f'[project]\nname = "ap-coder"\nversion = "{version}"\n')
    (folder / "models").mkdir()
    (folder / "models" / "det.onnx").write_bytes(b"m" * 2000)
    manifest: dict = {"app": "AP Coder", "version": version}
    if built:
        manifest["built"] = built
    if files:
        manifest["files"] = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in folder.rglob("*") if p.is_file()}  # fmt: skip
    (folder / "bundle_manifest.json").write_text(json.dumps(manifest))
    if venv:
        (folder / ".venv" / "lib").mkdir(parents=True)
        (folder / ".venv" / "lib" / "big.bin").write_bytes(b"v" * 5000)
    return folder


# --- The record of installs ------------------------------------------------------------------------------------


def test_versions_and_builds_are_compared():
    older = {"version": "0.1.0", "build": "2026-09-01T10:00:00"}
    assert paths.is_older(older, {"version": "0.2.0", "build": ""})
    assert paths.is_older(
        {"version": "0.2.0", "build": "2026-10-01T09:00:00"}, {"version": "0.2.0", "build": "2026-10-02"}
    )
    assert not paths.is_older({"version": "0.2.0", "build": ""}, {"version": "0.2.0", "build": "2026-10-02"})
    assert not paths.is_older({"version": "0.10.0"}, {"version": "0.9.1"})  # numbers, not text
    assert paths.describe({"version": "0.2.0", "build": "2026-10-11T09:30:00"}) == "0.2.0 (2026-10-11)"
    assert paths.describe({"version": "", "build": ""}) == "an earlier version"
    rebuilt = {"version": "0.2.0", "build": "2026-10-11T09:30:00"}
    assert paths.describe(rebuilt, exact=True) == "0.2.0 (2026-10-11 09:30)"


def test_the_record_keeps_one_entry_per_folder_and_the_current_one(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    paths.write_user_settings(data_dir="D:/data", reviewer="Ann")
    assert paths.record_install({"folder": str(a), "version": "0.1.0"}) is None
    assert paths.record_install({"folder": str(a), "version": "0.1.0"}) is None  # started again: still one entry
    before = paths.record_install({"folder": str(b), "version": "0.2.0"})
    assert before["folder"] == str(a)
    assert [e["folder"] for e in paths.installs()] == [str(a), str(b)]
    assert paths.current_install()["folder"] == str(b)
    paths.forget_install(a)
    settings = paths.read_user_settings()
    assert [e["folder"] for e in paths.installs()] == [str(b)] and settings["current"] == str(b)
    assert settings["data_dir"] == "D:/data" and settings["reviewer"] == "Ann"  # the other settings are kept
    assert not list(paths.user_settings_path().parent.glob("*.part"))  # written whole, nothing left behind


def test_an_older_folder_started_by_mistake_hands_over_to_the_newest(upgrade, tmp_path, monkeypatch):
    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0", "2026-09-01T10:00:00")
    new = make_install(tmp_path / "AP Coder 0.2", "0.2.0", "2026-10-11T09:00:00")
    paths.record_install(paths.install_identity(new))
    assert upgrade.newer_install(old)["folder"] == str(new)
    assert upgrade.newer_install(new) is None  # the newest itself
    own = new / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    own.parent.mkdir(parents=True)
    own.write_text("")
    ran: list = []
    monkeypatch.setenv("AP_LAUNCH_LOCK_HELD", "123")
    monkeypatch.setattr(upgrade.subprocess, "call", lambda cmd, cwd, env: ran.append((cmd, cwd, env)) or 0)
    assert upgrade.hand_over(new, ["--port", "8502", "--venv=reused"], "AP_LAUNCH_LOCK_HELD") == 0
    ((cmd, cwd, env),) = ran
    assert cmd[1:] == [str(new / "scripts" / "launch.py"), "--port", "8502"] and cwd == new
    assert "AP_LAUNCH_LOCK_HELD" not in env  # the newest takes its own folder's lock
    assert cmd[0] == str(own)  # its own .venv's Python


def test_a_newer_install_that_is_gone_or_not_newer_is_not_handed_over_to(upgrade, tmp_path):
    here = make_install(tmp_path / "here", "0.2.0", "2026-10-11T09:00:00")
    same = make_install(tmp_path / "copy", "0.2.0", "2026-10-11T09:00:00")
    paths.record_install(paths.install_identity(same))
    assert upgrade.newer_install(here) is None  # the same version and build: this one starts
    paths.record_install({"folder": str(tmp_path / "deleted"), "version": "9.0.0"})
    assert upgrade.newer_install(here) is None


# --- Unzipped over an older version -----------------------------------------------------------------------------


def test_files_the_older_version_left_are_removed_when_unzipped_over_it(upgrade, tmp_path):
    folder = make_install(tmp_path / "AP Coder", "0.2.0", files=True,
                          extra=("wheelhouse/numpy-2.3.0-cp312-cp312-win_amd64.whl",))  # fmt: skip
    stale = ["ap_coder/retired_module.py", "tests/test_retired.py", "scripts/old_tool.py", "models/old.onnx",
             "wheelhouse/numpy-1.0-cp311-cp311-win_amd64.whl", "ap_coder/gone/__init__.py"]  # fmt: skip
    for rel in stale:
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text("left by the older version")
    (folder / "docs").mkdir()
    (folder / "docs" / "my notes.md").write_text("not ours to remove")
    (folder / "ap_coder" / "__pycache__").mkdir()
    (folder / "ap_coder" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"cache")
    removed, freed = upgrade.prune_stale(folder)
    assert sorted(removed) == sorted(stale) and freed > 0
    assert not (folder / "ap_coder" / "gone").exists()  # an emptied folder goes too
    assert (folder / "ap_coder" / "__init__.py").exists() and (folder / "tests" / "test_a.py").exists()
    assert (folder / "docs" / "my notes.md").exists() and (folder / ".venv" / "lib" / "big.bin").exists()
    assert (folder / "ap_coder" / "__pycache__" / "x.cpython-312.pyc").exists()
    assert upgrade.prune_stale(folder) == ([], 0)


def test_retired_samples_and_top_level_files_of_an_older_version_are_removed(upgrade, tmp_path):
    """Unzipped over an older version, a sample invoice the new one dropped stayed in samples/: the self-check read
    it with no answer to compare, failed, and so the older install was never cleaned up. Files AP Coder no longer
    ships at the top (its Streamlit dashboard's streamlit_app.py and .streamlit/) stayed for good too."""
    folder = make_install(tmp_path / "AP Coder", "0.2.0", files=True,
                          extra=("samples/a.pdf", "samples/ground_truth/a.json"))  # fmt: skip
    stale = ["samples/retired.pdf", "samples/ground_truth/retired.json", "streamlit_app.py", ".streamlit/config.toml"]
    for rel in stale:
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text("left by the older version")
    (folder / "my notes.txt").write_text("the person's own")
    (folder / "docs").mkdir()
    (folder / "docs" / "my notes.md").write_text("the person's own")
    removed, _ = upgrade.prune_stale(folder)
    assert sorted(removed) == sorted(stale) and not (folder / ".streamlit").exists()
    assert (folder / "samples" / "a.pdf").exists() and (folder / "samples" / "ground_truth" / "a.json").exists()
    assert (folder / "my notes.txt").exists() and (folder / "docs" / "my notes.md").exists()
    assert (folder / "private" / "README.md").exists() and (folder / ".venv").exists()


def test_nothing_is_pruned_without_a_file_list_or_in_a_checkout(upgrade, tmp_path):
    folder = make_install(tmp_path / "old bundle", "0.1.0")  # an AP Coder 0.1 manifest: no file list
    (folder / "ap_coder" / "extra.py").write_text("x")
    assert upgrade.prune_stale(folder) == ([], 0)
    checkout = make_install(tmp_path / "checkout", "0.2.0", files=True)
    (checkout / ".git").mkdir()
    (checkout / "ap_coder" / "new_module.py").write_text("work in progress")
    assert upgrade.prune_stale(checkout) == ([], 0) and (checkout / "ap_coder" / "new_module.py").exists()


def test_a_wheelhouse_this_bundle_did_not_unzip_here_is_never_pruned(upgrade, tmp_path):
    """The tester copied an older bundle's wheelhouse next to the new code: the start deleted every wheel in it as
    "left by the older version", so instead of the one plain message (built for Windows, Python 3.11/3.12) pip
    failed with a long list of missing packages. A code-only update unzipped over an older folder lost its only
    wheelhouse the same way."""
    folder = make_install(tmp_path / "AP Coder", "0.2.0", files=True)  # its manifest lists no wheelhouse or models
    copied = ["wheelhouse/numpy-2.1.3-cp312-cp312-win_amd64.whl", "wheelhouse/streamlit-1.40.1-py3-none-any.whl"]
    for rel in copied:
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text("a wheel")
    assert upgrade.prune_stale(folder) == ([], 0) and all((folder / rel).exists() for rel in copied)
    # Its own wheels listed but not there (the person replaced the folder): the copied one is kept too.
    listed = json.loads((folder / "bundle_manifest.json").read_text())
    listed["files"]["wheelhouse/numpy-2.3.0-cp312-cp312-manylinux2014_x86_64.whl"] = "0" * 64
    (folder / "bundle_manifest.json").write_text(json.dumps(listed))
    assert upgrade.prune_stale(folder) == ([], 0) and all((folder / rel).exists() for rel in copied)


# --- Data kept in a code folder ----------------------------------------------------------------------------------


def _with_data(folder: Path) -> Path:
    private = folder / "private"
    (private / "invoices").mkdir(parents=True, exist_ok=True)
    (private / "invoices" / "a.pdf").write_text("pdf")
    (private / "ap_coder.db").write_text("db")
    return private


def test_data_kept_in_a_code_folder_moves_to_the_data_folder(upgrade, tmp_path, monkeypatch):
    monkeypatch.delenv("AP_PRIVATE_DIR")
    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0")
    private = _with_data(old)
    paths.write_user_settings(data_dir=str(private))  # AP Coder 0.1 recorded its private folder
    home = tmp_path / "APCoder"
    said: list[str] = []
    assert upgrade.settle_data_folder(tmp_path / "new", said.append, default=home) == home
    assert (home / "ap_coder.db").read_text() == "db" and (home / "invoices" / "a.pdf").exists()
    assert paths.private_dir() == home and not (private / "ap_coder.db").exists()
    assert (private / "README.md").exists() and "Moved your data" in said[0]
    assert upgrade.settle_data_folder(tmp_path / "new", said.append, default=home) == home and len(said) == 1


def test_data_in_a_code_folder_stays_when_the_data_folder_has_other_data(upgrade, tmp_path, monkeypatch):
    monkeypatch.delenv("AP_PRIVATE_DIR")
    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0")
    private = _with_data(old)
    paths.write_user_settings(data_dir=str(private))
    home = tmp_path / "APCoder"
    home.mkdir()
    (home / "ap_coder.db").write_text("someone else's")
    said: list[str] = []
    assert upgrade.settle_data_folder(tmp_path / "new", said.append, default=home) == private
    assert (private / "ap_coder.db").read_text() == "db" and (home / "ap_coder.db").read_text() == "someone else's"
    assert "still in" in said[0]


def test_an_interrupted_data_move_is_finished_by_the_next_start(upgrade, tmp_path, monkeypatch):
    """A move stopped halfway (a file in use, the window closed, the computer off): the next start found data in
    both folders, said "your data is still in <old folder>" and used it, without what had moved already (an empty
    queue, or no database at all). Now nothing runs on half the data, and the next start finishes the move."""
    import shutil

    monkeypatch.delenv("AP_PRIVATE_DIR")
    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0")
    private = _with_data(old)
    (private / "outputs").mkdir()
    (private / "outputs" / "batch.xlsx").write_text("export")
    paths.write_user_settings(data_dir=str(private))
    home = tmp_path / "APCoder"
    real_move, moved = shutil.move, []

    def move(src, dst, *args, **kwargs):
        if moved:
            raise PermissionError(32, "The process cannot access the file because it is being used", src)
        moved.append(src)
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "move", move)
    with pytest.raises(upgrade.DataMoveError) as stopped:
        upgrade.settle_data_folder(tmp_path / "new", lambda m: None, default=home)
    assert f"still in {private}" in str(stopped.value) and "picks up where it stopped" in stopped.value.hint
    assert paths.read_user_settings()["data_dir"] == str(private) and len(moved) == 1
    monkeypatch.setattr(shutil, "move", real_move)  # the file was closed; the next start
    said: list[str] = []
    assert upgrade.settle_data_folder(tmp_path / "new", said.append, default=home) == home
    assert (home / "ap_coder.db").read_text() == "db" and (home / "invoices" / "a.pdf").read_text() == "pdf"
    assert (home / "outputs" / "batch.xlsx").read_text() == "export" and not upgrade.has_data(private)
    assert paths.private_dir() == home and "Finished moving your data" in said[0]
    assert upgrade.settle_data_folder(tmp_path / "new", said.append, default=home) == home and len(said) == 1


def test_a_first_start_in_a_folder_with_data_moves_it_out(upgrade, tmp_path, monkeypatch):
    monkeypatch.delenv("AP_PRIVATE_DIR")
    here = make_install(tmp_path / "AP Coder", "0.2.0")
    _with_data(here)
    home = tmp_path / "APCoder"
    assert upgrade.settle_data_folder(here, lambda m: None, default=home) == home
    assert (home / "ap_coder.db").exists() and paths.read_user_settings()["data_dir"] == str(home)


def test_an_older_folders_data_and_settings_file_are_adopted(upgrade, tmp_path):
    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0")
    _with_data(old)
    (old / ".env").write_text("AP_REVIEWER=Ann\n")
    data = tmp_path / "APCoder"
    (data / "invoices").mkdir(parents=True)  # an empty data folder (as the first start makes it)
    assert upgrade.adopt_legacy(old, data, lambda m: None)
    assert (data / "ap_coder.db").read_text() == "db" and (data / ".env").read_text() == "AP_REVIEWER=Ann\n"
    assert (data / "invoices" / "a.pdf").exists() and not (old / ".env").exists()
    # A second older folder whose data would collide: kept apart, whole, never merged over the data folder's own.
    other = make_install(tmp_path / "AP Coder copy", "0.1.0")
    _with_data(other)
    (other / "private" / "ap_coder.db").write_text("other db")
    (other / ".env").write_text("AP_REVIEWER=Bob\n")
    said: list[str] = []
    assert upgrade.adopt_legacy(other, data, said.append)
    (aside,) = (data / "backups").iterdir()
    assert aside.name.startswith("from-AP Coder copy-") and (aside / "ap_coder.db").read_text() == "other db"
    assert (aside / ".env").read_text() == "AP_REVIEWER=Bob\n" and (data / "ap_coder.db").read_text() == "db"
    assert (data / ".env").read_text() == "AP_REVIEWER=Ann\n" and "nothing was merged" in said[0]


# --- Cleaning up after the update --------------------------------------------------------------------------------


def test_the_older_install_is_removed_whole_and_forgotten(upgrade, tmp_path):
    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0", "2026-09-01T10:00:00")
    new = make_install(tmp_path / "AP Coder 0.2", "0.2.0", "2026-10-11T09:00:00")
    data = tmp_path / "APCoder"
    data.mkdir()
    (data / "ap_coder.db").write_text("the data")
    _with_data(old)
    paths.record_install(paths.install_identity(old))
    paths.record_install(paths.install_identity(new))
    said: list[str] = []
    freed = upgrade.clean_up(new, data, [old], said.append)
    assert not old.exists() and freed > 7000
    assert [e["folder"] for e in paths.installs()] == [str(new)]
    assert (data / "ap_coder.db").read_text() == "the data"  # the data folder's own data: untouched
    assert list((data / "backups").glob("from-AP Coder 0.1-*/ap_coder.db"))  # the old folder's data: kept aside
    assert any("Removed the older AP Coder folder" in s and "KB" in s for s in said)
    assert new.exists() and (new / ".venv").exists()
    assert not list(tmp_path.glob("*.removing-*"))


def test_an_older_folder_with_files_of_its_own_keeps_them_but_not_its_venv(upgrade, tmp_path):
    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0")
    (old / "my export.xlsx").write_text("the person's own file")
    said: list[str] = []
    freed = upgrade.clean_up(tmp_path / "new", tmp_path / "data", [old], said.append)
    assert (old / "my export.xlsx").exists() and (old / "ap_coder").exists()
    assert not (old / ".venv").exists() and not (old / "models").exists() and freed >= 7000
    assert "my export.xlsx" in said[0] and "removed its .venv and models" in said[0]


def test_a_running_or_checked_out_older_install_is_left_alone(upgrade, tmp_path):
    busy = make_install(tmp_path / "busy", "0.1.0")
    handle = open(busy / ".ap_coder_launch.lock", "a+b")
    import os

    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    checkout = make_install(tmp_path / "checkout", "0.1.0")
    (checkout / ".git").mkdir()
    said: list[str] = []
    try:
        assert upgrade.clean_up(tmp_path / "new", tmp_path / "data", [busy, checkout], said.append) == 0
    finally:
        handle.close()
    assert (busy / ".venv").exists() and (checkout / ".venv").exists()
    assert "running from it" in said[0] and len(said) == 1


def test_only_an_ap_coder_folder_is_ever_removed(upgrade, tmp_path):
    stranger = tmp_path / "Projects"
    (stranger / "ap_coder").mkdir(parents=True)
    (stranger / "notes.txt").write_text("x")
    assert "doesn't look like" in upgrade.removable(stranger, tmp_path / "new")
    here = make_install(tmp_path / "here", "0.2.0")
    assert upgrade.removable(here, here) == "it holds this AP Coder"
    assert upgrade.removable(tmp_path, here) == "it holds this AP Coder"


def test_an_unfinished_removal_is_finished_by_a_later_start(upgrade, tmp_path):
    old = tmp_path / "AP Coder 0.1"
    rest = tmp_path / "AP Coder 0.1.removing-20261011-090000"
    (rest / "sub").mkdir(parents=True)
    (rest / "sub" / "locked.dll").write_bytes(b"x" * 3000)
    paths.record_install({"folder": str(old), "version": "0.1.0"}, current=False)
    assert upgrade.clean_up(tmp_path / "new", tmp_path / "data", [old], lambda m: None) == 3000
    assert not rest.exists() and paths.installs() == []


def test_an_older_folder_a_window_still_has_open_loses_its_venv_and_is_removed_later(upgrade, tmp_path, monkeypatch):
    """Windows: the old AP Coder window (stopped by the new one, waiting at "Press any key") has its folder as its
    current folder, so the folder can't be renamed or removed. Nothing was removed, yet the start said "most of it
    was removed" and counted the whole folder as freed."""
    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0", "2026-09-01T10:00:00")
    paths.record_install(paths.install_identity(old), current=False)
    real_rename = Path.rename

    def rename(self, target):
        if self == old:
            raise PermissionError(32, "The process cannot access the file because it is being used", str(self))
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    said: list[str] = []
    freed = upgrade.clean_up(tmp_path / "new", tmp_path / "data", [old], said.append)
    assert old.exists() and not (old / ".venv").exists() and not (old / "models").exists()
    assert freed == 7000 and not list(tmp_path.glob("*.removing-*"))
    assert "removed its .venv and models" in said[0] and "most of it was removed" not in said[0]
    assert [e["folder"] for e in paths.installs()] == [str(old)]  # still on record: removed whole later
    monkeypatch.setattr(Path, "rename", real_rename)  # the window was closed
    upgrade.clean_up(tmp_path / "new", tmp_path / "data", [old], said.append)
    assert not old.exists() and paths.installs() == []


def test_read_only_files_in_an_older_folder_are_removed_too(upgrade, tmp_path, monkeypatch):
    """Windows refuses to delete a read-only file: the folder was left half-removed (``.removing-...``) on every
    start, and the older install never left the record."""
    import os
    import stat

    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0", "2026-09-01T10:00:00")
    (old / "docs").mkdir()
    (old / "docs" / "guide.pdf").write_text("read-only")
    (old / "docs" / "guide.pdf").chmod(stat.S_IREAD)
    (old / ".venv" / "lib" / "big.bin").chmod(stat.S_IREAD)
    paths.record_install(paths.install_identity(old), current=False)
    real_unlink = os.unlink

    def unlink(path, *, dir_fd=None):  # as Windows: a read-only file is never deleted
        if not os.stat(path, dir_fd=dir_fd, follow_symlinks=False).st_mode & stat.S_IWUSR:
            raise PermissionError(13, "Access is denied", path)
        return real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(os, "remove", unlink)
    said: list[str] = []
    upgrade.clean_up(tmp_path / "new", tmp_path / "data", [old], said.append)
    assert not old.exists() and not list(tmp_path.glob("*.removing-*")) and paths.installs() == []
    assert "Removed the older AP Coder folder" in said[-1]


def test_an_older_folders_data_that_cannot_be_moved_keeps_the_folder(upgrade, tmp_path, monkeypatch):
    """A file of the older folder's data in use (or read-only): the clean-up stopped the start with a traceback."""
    import shutil

    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0")
    _with_data(old)

    def move(src, dst, *args, **kwargs):
        raise PermissionError(32, "The process cannot access the file because it is being used", src)

    monkeypatch.setattr(shutil, "move", move)
    said: list[str] = []
    assert upgrade.clean_up(tmp_path / "new", tmp_path / "data", [old], said.append) == 0
    assert (old / "private" / "ap_coder.db").read_text() == "db" and (old / ".venv").exists()
    assert "it is cleaned up on a later start" in said[0]


def test_legacy_installs_are_found_from_the_data_folder_they_used(upgrade, tmp_path):
    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0", venv=False)
    paths.write_user_settings(data_dir=str(old / "private"))
    shortcut = make_install(tmp_path / "Desktop target", "0.1.0", venv=False)
    found = upgrade.legacy_installs(tmp_path / "new", [shortcut, tmp_path / "nothing here"])
    assert found == [old, shortcut]
    assert [i["version"] for i in upgrade.previous_versions(tmp_path / "new", [shortcut])] == ["0.1.0", "0.1.0"]


# --- The bundle says what it holds; the version is real --------------------------------------------------------


def test_the_bundle_manifest_lists_every_file_with_its_sha256(tmp_path):
    bundle = _script("build_offline_bundle")
    folder = make_install(tmp_path / "APProcessor", "0.2.0", venv=False)
    manifest = json.loads(bundle.write_manifest(folder).read_text())
    assert manifest["version"] == "0.2.0" and manifest["built"][:4].isdigit()
    files = manifest["files"]
    assert "bundle_manifest.json" not in files and "models/det.onnx" in files
    assert files["scripts/launch.py"] == hashlib.sha256((folder / "scripts" / "launch.py").read_bytes()).hexdigest()
    assert paths.install_identity(folder)["build"] == manifest["built"]


def test_ap_coder_has_a_real_version():
    import ap_coder

    assert ap_coder.__version__ == paths.project_version() == "0.2.0"
    assert paths.version_key(ap_coder.__version__) > paths.version_key("0.1.0")


def test_a_version_registered_by_the_older_install_is_noticed(tmp_path, monkeypatch):
    """Unzipped over 0.1.0, .venv still says 0.1.0 for ap_coder (what the running server reports) until it is
    registered again."""
    check_deps = _script("check_deps")
    code = tmp_path / "AP Coder"
    code.mkdir()
    dist = tmp_path / "site" / "ap_coder-0.1.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: ap-coder\nVersion: 0.1.0\n")
    (dist / "direct_url.json").write_text(json.dumps({"url": code.as_uri(), "dir_info": {"editable": True}}))
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    assert check_deps.registered_version(code) == "0.1.0"
    assert check_deps.registered_version(tmp_path / "elsewhere") == ""


def test_settings_no_longer_used_are_commented_out_with_a_note(tmp_path):
    from ap_coder import envfile
    from ap_coder.config import RETIRED_SETTINGS

    env = tmp_path / ".env"
    env.write_text("# my settings\nAP_REVIEWER=Ann\nexport AP_PAGE_READER=on\nAP_PAGE_READER_SCOPE = all\n")
    found = envfile.retire(env, RETIRED_SETTINGS, "no longer used (AP Coder 0.2)")
    assert found == ["AP_PAGE_READER", "AP_PAGE_READER_SCOPE"]
    note = "# no longer used (AP Coder 0.2): "
    expected = f"# my settings\nAP_REVIEWER=Ann\n{note}export AP_PAGE_READER=on\n{note}AP_PAGE_READER_SCOPE = all\n"
    assert env.read_text() == expected
    assert envfile.retire(env, RETIRED_SETTINGS, "x") == [] and envfile.read_env(env) == {"AP_REVIEWER": "Ann"}
