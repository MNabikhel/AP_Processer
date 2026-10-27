"""The clean-up of an older install never takes the person's data with it (scripts/upgrade.py: removable, clean_up):
a data folder inside the old folder, a database or an invoices folder anywhere in it, a recorded folder that is no
longer an AP Coder install, a link to a folder elsewhere."""

from __future__ import annotations

import os

import pytest

from ap_coder.core import paths
from tests.launcher.test_update import _script, make_install


@pytest.fixture
def upgrade():
    return _script("upgrade")


def _github_zip(folder, version="0.2.0"):
    """An older GitHub ZIP folder, set up: no manifest; its data/ and samples/ are the code's."""
    make_install(folder, version)
    (folder / "bundle_manifest.json").unlink()
    (folder / "data").mkdir()
    (folder / "data" / "chart_of_accounts.csv").write_text("code,name\n")
    (folder / "samples").mkdir()
    (folder / "samples" / "a.pdf").write_bytes(b"%PDF")
    return folder


def _data(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ap_coder.db").write_bytes(b"SQLite format 3\0")
    (folder / "invoices").mkdir()
    (folder / "invoices" / "acme.pdf").write_bytes(b"%PDF")
    return folder


@pytest.mark.parametrize("inside", ["data/mine", "data", "samples/mydata"])
def test_a_data_folder_inside_the_old_folder_is_never_deleted(upgrade, tmp_path, monkeypatch, inside):
    """B-558: with the data folder recorded inside an old GitHub ZIP folder's data/ or samples/ (or as its data/), the
    clean-up said "Removed the older AP Coder folder" and the database and invoices went with it: without a manifest
    those folders count as AP Coder's own, a .db there was exempt, and nothing asked where the data folder was."""
    old = _github_zip(tmp_path / "AP_Processer-main")
    data = _data(old / inside)
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    new = make_install(tmp_path / "AP Coder 0.3", "0.3.0")
    assert upgrade.removable(old, new) != ""
    said: list[str] = []
    upgrade.clean_up(new, data, [old], said.append)
    assert (data / "ap_coder.db").is_file() and (data / "invoices" / "acme.pdf").is_file()
    assert any("data folder" in line for line in said), said


def test_a_database_or_invoices_anywhere_in_the_old_folder_keep_it(upgrade, tmp_path):
    """Not the data folder on record, but a database or an invoices folder all the same: never deleted."""
    for where in ("samples/old_data/ap_coder.db", "data/backup.db", "notes/invoices/x.pdf"):
        old = _github_zip(tmp_path / where.replace("/", "_"))
        (old / where).parent.mkdir(parents=True, exist_ok=True)
        (old / where).write_bytes(b"x")
        new = make_install(tmp_path / f"new {where.replace('/', '_')}", "0.3.0")
        assert upgrade.removable(old, new) != "", where
        upgrade.clean_up(new, tmp_path / "data", [old], lambda line: None)
        assert (old / where).is_file(), where


def test_a_recorded_folder_that_is_no_longer_an_install_is_not_touched(upgrade, tmp_path):
    """B-559: a folder on record that holds no AP Coder now (its ap_coder/ gone) lost its .venv and models/."""
    gone = make_install(tmp_path / "was AP Coder", "0.1.0")
    import shutil

    shutil.rmtree(gone / "ap_coder")
    said: list[str] = []
    upgrade.clean_up(make_install(tmp_path / "new", "0.3.0"), tmp_path / "data", [gone], said.append)
    assert (gone / ".venv").is_dir() and (gone / "models" / "det.onnx").is_file()
    assert any("no longer an AP Coder folder" in line for line in said), said


def test_the_data_folders_models_are_kept_when_the_data_folder_is_the_old_folder(upgrade, tmp_path, monkeypatch):
    old = make_install(tmp_path / "old", "0.1.0")
    _data(old)
    monkeypatch.setenv("AP_PRIVATE_DIR", str(old))
    upgrade.clean_up(make_install(tmp_path / "new", "0.3.0"), old, [old], lambda line: None)
    assert (old / "models" / "det.onnx").is_file() and (old / "ap_coder.db").is_file()


@pytest.mark.skipif(os.name == "nt", reason="a symbolic link needs a privilege on Windows")
def test_a_link_to_an_old_folder_is_skipped_not_renamed(upgrade, tmp_path):
    """B-560: a symlinked old folder was renamed to <link>.removing-<date> and said to be "mostly removed", again on
    every start; nothing behind it was deleted. Links are skipped, said once, and taken off the record."""
    real = make_install(tmp_path / "real", "0.1.0")
    link = tmp_path / "AP Coder link"
    link.symlink_to(real, target_is_directory=True)
    paths.record_install({"folder": str(link), "version": "0.1.0"}, current=False)
    said: list[str] = []
    upgrade.clean_up(make_install(tmp_path / "new", "0.3.0"), tmp_path / "data", [link], said.append)
    assert link.is_symlink() and (real / ".venv").is_dir() and (real / "ap_coder").is_dir()
    assert not list(tmp_path.glob("*.removing-*"))
    assert any("a link" in line for line in said), said
    assert not any(paths.same_folder(e["folder"], link) or e["folder"] == str(link) for e in paths.installs())
