"""A data folder's database read without a Store (``core/store/peek.py``, D-211, D-243): its sign-in setting and its
accounts, read only, wherever the data folder is."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from ap_coder.core.store import Store, peek, users
from ap_coder.core.store.sign_in import LAPTOP, SIGN_IN


@pytest.mark.parametrize(("path", "uri"), [
    (PureWindowsPath(r"\\fileserver\home$\pat\APCoder\ap_coder.db"),
     "file:////fileserver/home%24/pat/APCoder/ap_coder.db"),
    (PureWindowsPath(r"C:\Users\Pat Lee\APCoder\ap_coder.db"), "file:///C:/Users/Pat%20Lee/APCoder/ap_coder.db"),
    (PurePosixPath("/home/pat/APCoder/ap_coder.db"), "file:///home/pat/APCoder/ap_coder.db"),
])  # fmt: skip
def test_a_database_on_a_network_path_is_named_without_an_authority_sqlite_refuses(path, uri):
    """B-2569: a data folder on a network path, or on a mapped drive (``resolve`` makes it ``\\\\server\\share``), was
    named ``file://fileserver/...``: SQLite refuses any authority but localhost ("invalid uri authority"), so the
    sign-in setting and the accounts read as none, and a laptop signed into there was refused at its start, its
    database said to be missing or damaged. The path goes in the URI's path, its authority empty; ``mode=ro`` still
    applies."""
    assert peek.read_only_uri(path, "mode=ro") == f"{uri}?mode=ro"


def test_the_empty_authority_form_opens_read_only(tmp_path):
    """SQLite takes ``file:////...`` (an empty authority, the path from its second slash) and its ``mode=ro``."""
    store = Store(tmp_path / "ap_coder.db")
    store.set_setting(SIGN_IN, LAPTOP)
    conn = sqlite3.connect(f"file:///{tmp_path.as_posix()}/ap_coder.db?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT value FROM settings WHERE key = ?", (SIGN_IN,)).fetchone()[0] == LAPTOP
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM settings")
    finally:
        conn.close()
    assert peek.setting(store.path, SIGN_IN) == LAPTOP
    assert peek.accounts(store.path) == users.sign_in_accounts(store.path) == 0


@pytest.mark.skipif(os.name != "nt", reason="a Windows network path")
def test_on_windows_a_database_reached_by_a_network_path_is_read(tmp_path):
    """The database through this computer's own administrative share (``\\\\localhost\\C$\\...``), as a data folder on
    a file server or a mapped drive is reached: its setting and its accounts read as they are."""
    store = Store(tmp_path / "ap_coder.db")
    store.set_setting(SIGN_IN, LAPTOP)
    store.add_user("admin", "correct horse battery", role="admin", must_change=False)
    drive, rest = os.path.splitdrive(str(store.path))
    shared = Path(f"\\\\localhost\\{drive[0]}$" + rest)
    if not shared.is_file():
        pytest.skip("no administrative share on this computer")
    assert peek.setting(shared, SIGN_IN) == LAPTOP
    assert peek.accounts(shared) == users.sign_in_accounts(shared) == 1
