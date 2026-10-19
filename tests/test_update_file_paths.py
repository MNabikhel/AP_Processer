"""An update never leaves an invoice without its file.

Invoices record their file as an absolute path. An AP Coder 0.1 install's demo invoices point into its own
``samples/``, and the invoices of one that kept its data inside it point into its ``private/``. The update moves
``private/`` to the data folder (or aside, to ``backups/from-<folder>-<date>/``) and deletes the old folder after the
self-check: every such invoice used to be left pointing at a file that was gone (the review screen's page picture a
404, no stamped PDF, no page reader, no Azure check). Now the update points each one at its file's new place first
(``scripts/upgrade.py``: ``repoint_files``), never deletes a folder holding a file the database still needs, and
the dashboard finds a file that moved anyway (``Store.invoice_file``) or says plainly that it is gone.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from ap_coder.core import paths
from ap_coder.core.store import Store

from .conftest import SAMPLES
from .test_update import make_install
from .webclient import client_for

ROOT = Path(__file__).resolve().parent.parent
DEMO = "northwind_ON_HST_NW-2026-0912.pdf"  # the same bytes in the old and the new version
CHANGED = "pacific_BC_GST_PST_PO-77120.pdf"  # a sample the new version changed
OWN = "harbourview_NS_HST_HPS-2026-0347.pdf"  # an invoice of the person's, kept in private/invoices
SCAN = "redriver_MB_GST_RST_RRO-55821.pdf"  # a file elsewhere in the old folder (docs/)


@pytest.fixture
def upgrade():
    spec = importlib.util.spec_from_file_location("ap_script_upgrade_file_paths", ROOT / "scripts" / "upgrade.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gt(name: str) -> dict:
    return json.loads((SAMPLES / "ground_truth" / f"{Path(name).stem}.json").read_text())


def old_install(tmp_path: Path) -> tuple[Path, Path, dict[str, int]]:
    """AP Coder 0.1 in ``<tmp>/AP Coder 0.1`` with its data in ``private/`` (as it kept it), and the new version in
    ``<tmp>/AP Coder 0.2``. Its invoices: two demo invoices (samples/, one the new version changed), one of the
    person's own (private/invoices, made from a photo kept in originals/) and one from elsewhere in the folder."""
    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0", "2026-09-01T10:00:00")
    new = make_install(tmp_path / "AP Coder 0.2", "0.2.0", "2026-10-11T09:00:00")
    for folder in (old, new):
        (folder / "samples").mkdir()
        shutil.copy(SAMPLES / DEMO, folder / "samples" / DEMO)
    shutil.copy(SAMPLES / CHANGED, old / "samples" / CHANGED)
    (new / "samples" / CHANGED).write_bytes((SAMPLES / CHANGED).read_bytes() + b"\n% changed in 0.2\n")
    private = old / "private"
    (private / "invoices" / "originals").mkdir(parents=True)
    shutil.copy(SAMPLES / OWN, private / "invoices" / OWN)
    (private / "invoices" / "originals" / "photo.jpg").write_bytes(b"the photo the PDF was made from")
    (old / "docs").mkdir()
    shutil.copy(SAMPLES / SCAN, old / "docs" / SCAN)
    store = Store(private / "ap_coder.db")
    ids = {}
    for name, path in ((DEMO, old / "samples" / DEMO), (CHANGED, old / "samples" / CHANGED)):
        ids[name] = store.add_invoice(
            path, gt(name), {"requires_review": True}, meta={"demo": True, "source": str(path)}
        )
    kept = private / "invoices" / "originals" / "photo.jpg"
    photo = {"file": "photo.jpg", "kept": str(kept), "sha256": sha(kept)}
    path = private / "invoices" / OWN
    ids[OWN] = store.add_invoice(
        path, gt(OWN), {"requires_review": True}, meta={"source": str(path), "original": photo}
    )
    path = old / "docs" / SCAN
    ids[SCAN] = store.add_invoice(path, gt(SCAN), {"requires_review": True}, meta={"source": str(path)})
    paths.write_user_settings(data_dir=str(private))  # AP Coder 0.1 recorded its private folder
    paths.record_install(paths.install_identity(old))
    paths.record_install(paths.install_identity(new))
    return old, new, ids


def invoices(db: Path) -> dict[int, dict]:
    store = Store(db)
    with sqlite3.connect(db) as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM invoices")]
    return {i: store.get_invoice(i) for i in ids}


def stored(db: Path) -> dict[int, tuple[str, dict]]:
    """What the database records (read directly: no file is looked for)."""
    with sqlite3.connect(db) as conn:
        return {r[0]: (r[1], json.loads(r[2] or "{}")) for r in conn.execute("SELECT id, source_path, meta FROM "
                                                                              "invoices")}  # fmt: skip


def test_the_update_points_every_invoice_at_its_files_new_place(upgrade, tmp_path):
    old, new, ids = old_install(tmp_path)
    data = tmp_path / "APCoder"
    (data / "invoices").mkdir(parents=True)  # as the first start makes it: no data of its own yet
    said: list[str] = []
    upgrade.clean_up(new, data, [old], said.append)
    assert not old.exists(), said
    assert any("Removed the older AP Coder folder" in s for s in said)
    rows = stored(data / "ap_coder.db")
    assert rows[ids[DEMO]][0] == str((new / "samples" / DEMO).resolve())  # the new version's own, the same bytes
    copied = Path(rows[ids[CHANGED]][0])  # the new version's sample differs: the old one is kept in the data folder
    assert copied.parent == (data / "invoices").resolve() and sha(copied) == sha(SAMPLES / CHANGED)
    assert rows[ids[OWN]][0] == str((data / "invoices" / OWN).resolve())  # moved with private/: the same place
    assert rows[ids[OWN]][1]["original"]["kept"] == str((data / "invoices" / "originals" / "photo.jpg").resolve())
    scan = Path(rows[ids[SCAN]][0])
    assert scan == (data / "invoices" / "from-AP Coder 0.1" / "docs" / SCAN).resolve() and sha(scan) == sha(
        SAMPLES / SCAN)  # fmt: skip
    for path, meta in rows.values():
        assert Path(path).is_file(), path
        assert meta["source"] == path  # the meta's copy of the path follows
    store = Store(data / "ap_coder.db")
    for inv in invoices(data / "ap_coder.db").values():
        assert sha(Path(inv["source_path"])) == inv["file_sha256"]
    (event,) = store.events(actions=["files_moved"])
    assert event["detail"]["from"] == str(old) and event["detail"]["invoices"] == 4
    assert any("4 invoices" in s for s in said)
    assert [b for b in store.list_backups() if "before-update" in b.name]  # backed up before it was changed


def test_the_review_screen_shows_the_pages_after_the_update(upgrade, tmp_path):
    old, new, ids = old_install(tmp_path)
    data = tmp_path / "APCoder"
    (data / "invoices").mkdir(parents=True)
    upgrade.clean_up(new, data, [old], lambda m: None)
    assert not old.exists()
    client = client_for(Store(data / "ap_coder.db"))
    for invoice_id in ids.values():
        page = client.get(f"/review/{invoice_id}")
        assert page.status_code == 200 and "no longer at" not in page.text
        picture = client.get(f"/review/{invoice_id}/page/1.png")
        assert picture.status_code == 200 and picture.headers["content-type"] == "image/png"


def test_data_moved_aside_is_pointed_at_where_it_went(upgrade, tmp_path):
    """The data folder already had data of its own: the old folder's private/ went, whole, to backups/from-...;
    its database (to restore from) points at the files there, and the live database's demo invoices too."""
    old, new, ids = old_install(tmp_path)
    data = tmp_path / "APCoder"
    data.mkdir()
    live = Store(data / "ap_coder.db")
    demo = live.add_invoice(old / "samples" / DEMO, gt(DEMO), {"requires_review": True}, meta={"demo": True})
    said: list[str] = []
    upgrade.clean_up(new, data, [old], said.append)
    assert not old.exists(), said
    assert stored(data / "ap_coder.db")[demo][0] == str((new / "samples" / DEMO).resolve())
    (aside,) = (data / "backups").glob("from-AP Coder 0.1-*")
    rows = stored(aside / "ap_coder.db")
    assert rows[ids[OWN]][0] == str((aside / "invoices" / OWN).resolve())
    assert all(Path(path).is_file() for path, _meta in rows.values())


def test_a_file_that_cannot_be_moved_keeps_the_old_folder(upgrade, tmp_path, monkeypatch):
    old, new, ids = old_install(tmp_path)
    data = tmp_path / "APCoder"
    (data / "invoices").mkdir(parents=True)
    real_copy = shutil.copy2

    def copy(src, dst, *args, **kwargs):
        if Path(src).name == SCAN:
            raise PermissionError(13, "Permission denied", str(src))
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", copy)
    said: list[str] = []
    upgrade.clean_up(new, data, [old], said.append)
    assert (old / "docs" / SCAN).is_file()  # the folder stays: the database still needs that file
    assert any("Kept the older AP Coder folder" in s and SCAN in s for s in said), said
    rows = stored(data / "ap_coder.db")
    assert rows[ids[SCAN]][0] == str((old / "docs" / SCAN).resolve())  # still where it is
    assert rows[ids[OWN]][0] == str((data / "invoices" / OWN).resolve())  # the others moved on
    assert any(paths.same_folder(e["folder"], old) for e in paths.installs())  # tried again on a later start
    monkeypatch.setattr(shutil, "copy2", real_copy)  # the next start
    upgrade.clean_up(new, data, [old], said.append)
    assert not old.exists() and Path(stored(data / "ap_coder.db")[ids[SCAN]][0]).is_file()


def test_moving_the_data_folder_out_of_a_code_folder_points_the_invoices_there(upgrade, tmp_path, monkeypatch):
    """Data kept in <old>/private (recorded as the data folder) moves to ~/APCoder at the first start of the new
    version, before the self-check: its invoices point there at once, whether or not the old folder goes later."""
    monkeypatch.delenv("AP_PRIVATE_DIR")
    old, new, ids = old_install(tmp_path)
    home = tmp_path / "APCoder"
    assert upgrade.settle_data_folder(new, lambda m: None, default=home) == home
    rows = stored(home / "ap_coder.db")
    assert rows[ids[OWN]][0] == str((home / "invoices" / OWN).resolve())
    assert rows[ids[DEMO]][0] == str((new / "samples" / DEMO).resolve())
    assert rows[ids[SCAN]][0] == str((old / "docs" / SCAN).resolve())  # still there: moved when the folder goes


def test_data_this_folder_kept_is_pointed_at_the_data_folder(upgrade, tmp_path):
    """A new version unzipped over the old one: this folder's own private/ moves to the data folder."""
    here, _new, ids = old_install(tmp_path)
    data = tmp_path / "APCoder"
    (data / "invoices").mkdir(parents=True)
    upgrade.adopt_legacy(here, data, lambda m: None)
    upgrade.repoint_files(here, data, here, lambda m: None, copy=False)
    rows = stored(data / "ap_coder.db")
    assert rows[ids[OWN]][0] == str((data / "invoices" / OWN).resolve())
    assert rows[ids[DEMO]][0] == str((here / "samples" / DEMO).resolve())  # still here: untouched
    assert rows[ids[SCAN]][0] == str((here / "docs" / SCAN).resolve())


def test_a_backup_made_by_the_update_is_reused(upgrade, tmp_path):
    old, new, _ids = old_install(tmp_path)
    data = tmp_path / "APCoder"
    (data / "invoices").mkdir(parents=True)
    upgrade.adopt_legacy(old, data, lambda m: None)
    Store(data / "ap_coder.db").backup_now("before-update-from-schema-14")  # the update's own, just made
    upgrade.repoint_files(old, data, new, lambda m: None)
    assert len(list((data / "backups").glob("ap_coder-*.db"))) == 1


def test_nothing_to_point_anywhere_changes_nothing(upgrade, tmp_path):
    old = make_install(tmp_path / "AP Coder 0.1", "0.1.0")
    data = tmp_path / "APCoder"
    data.mkdir()
    Store(data / "ap_coder.db")
    assert upgrade.repoint_files(old, data, tmp_path / "new", lambda m: None) == []
    assert not (data / "backups").exists()
    (data / "ap_coder.db").write_text("not a database")  # whatever is there: never a reason to stop the update
    assert upgrade.repoint_files(old, data, tmp_path / "new", lambda m: None) == []


# --- When the dashboard opens an invoice's file ----------------------------------------------------------------


def test_a_file_that_moved_is_found_again_by_its_sha256(tmp_path):
    data = tmp_path / "data"
    (data / "invoices").mkdir(parents=True)
    path = data / "invoices" / OWN
    shutil.copy(SAMPLES / OWN, path)
    store = Store(data / "ap_coder.db")
    own = store.add_invoice(path, gt(OWN), {"requires_review": True}, meta={"source": str(path)})
    gone = tmp_path / "AP Coder 0.1" / "samples" / DEMO  # a demo invoice of a folder that was deleted
    demo = store.add_invoice(gone, gt(DEMO), {"requires_review": True}, meta={"demo": True})
    with sqlite3.connect(data / "ap_coder.db") as conn:
        conn.execute("UPDATE invoices SET file_sha256 = ? WHERE id = ?", (sha(SAMPLES / DEMO), demo))
    moved = data / "invoices" / "from-AP Coder 0.1" / f"{Path(OWN).stem}.from-old-install.pdf"
    moved.parent.mkdir()
    path.rename(moved)  # moved, and renamed beside a file of the same name
    assert store.get_invoice(own)["source_path"] == str(moved.resolve())
    assert store.get_invoice(demo)["source_path"] == str((ROOT / "samples" / DEMO).resolve())
    rows = stored(data / "ap_coder.db")
    assert rows[own] == (str(moved.resolve()), {"source": str(moved.resolve())})  # the row points there now
    assert {e["invoice_id"] for e in store.events(actions=["file_found"])} == {own, demo}


def test_a_file_with_other_bytes_is_not_taken_for_the_invoices(tmp_path):
    data = tmp_path / "data"
    (data / "invoices").mkdir(parents=True)
    path = data / "invoices" / "elsewhere" / "own invoice.pdf"
    path.parent.mkdir()
    path.write_bytes((SAMPLES / OWN).read_bytes() + b"\n% not a sample\n")
    store = Store(data / "ap_coder.db")
    own = store.add_invoice(path, gt(OWN), {"requires_review": True})
    path.unlink()
    (data / "invoices" / "own invoice.pdf").write_bytes(b"another invoice with the same name")
    assert store.invoice_file(store.get_invoice(own)) is None
    assert stored(data / "ap_coder.db")[own][0] == str(path.resolve())


def test_the_review_screen_says_plainly_when_the_file_is_gone(tmp_path):
    data = tmp_path / "data"
    (data / "invoices").mkdir(parents=True)
    path = data / "invoices" / "x" / "own invoice.pdf"
    path.parent.mkdir()
    path.write_bytes((SAMPLES / OWN).read_bytes() + b"\n% not a sample\n")
    store = Store(data / "ap_coder.db")
    own = store.add_invoice(path, gt(OWN), {"requires_review": True})
    path.unlink()
    client = client_for(store)
    page = client.get(f"/review/{own}")
    assert page.status_code == 200 and f"This invoice&#39;s file is no longer at {path.resolve()}" in page.text
    picture = client.get(f"/review/{own}/page/1.png")
    assert picture.status_code == 404 and "file is no longer at" in picture.json()["error"]
