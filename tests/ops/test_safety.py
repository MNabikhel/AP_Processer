"""The server's data folder (``ops/safety.py``, D-188): never on a network share (SQLite's write-ahead log is damaged
there), and never a new, empty database where there was one."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ap_coder.core.store import Store
from ap_coder.ops import cli, safety, service

REMOTE = {"Z:\\": safety.DRIVE_REMOTE, "D:\\": 3}  # GetDriveTypeW: a mapped share, a fixed disk


def windows(path: str) -> str:
    return safety.network_share(path, windows=True, drive_type=lambda root: REMOTE.get(root, 3))


@pytest.mark.parametrize("path", ["\\\\fileserver\\APCoder\\data", "//fileserver/APCoder/data", "\\\\?\\UNC\\nas\\ap",
                                  "\\\\10.0.0.5\\d$\\APCoderData", "Z:\\APCoderData", "z:/APCoderData"])  # fmt: skip
def test_a_share_on_windows_is_told_by_its_path_or_its_drive(path):
    assert windows(path)


@pytest.mark.parametrize("path", ["D:\\APCoderData", "C:/APCoder/data", "\\\\?\\D:\\APCoderData",
                                  "\\\\.\\PhysicalDrive0"])  # fmt: skip
def test_a_folder_on_the_servers_own_disk_is_not(path):
    assert windows(path) == ""


LINUX = """sysfs /sys sysfs rw 0 0
/dev/sda1 / ext4 rw,relatime 0 0
//nas/ap\\040share /mnt/ap\\040share cifs rw,vers=3.0 0 0
nas:/export /srv/nfs nfs4 rw 0 0
/dev/sdb1 /srv/nfs/local ext4 rw 0 0
"""
MAC = """/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
//ann@nas._smb._tcp.local/AP on /Volumes/AP (smbfs, nodev, nosuid, mounted by ann)
afp_0TQP://nas/Old on /Volumes/Old Share (afpfs, nodev, nosuid)
"""


@pytest.mark.parametrize(("folder", "kind"), [("/mnt/ap share/data", "cifs"), ("/srv/nfs/APCoder", "nfs4"),
                                              ("/srv/nfs/local/APCoder", ""), ("/home/ann/APCoder", "")])  # fmt: skip
def test_a_share_mounted_on_linux_is_told_by_proc_mounts(folder, kind, monkeypatch):
    monkeypatch.setattr(safety.os.path, "realpath", lambda p: p)
    found = safety.network_share(folder, windows=False, mounts=safety.linux_mounts(LINUX))
    assert (kind in found) if kind else found == ""


@pytest.mark.parametrize(("folder", "kind"), [("/Volumes/AP/APCoder", "smbfs"), ("/Volumes/Old Share/x", "afpfs"),
                                              ("/Users/ann/APCoder", "")])  # fmt: skip
def test_a_share_mounted_on_a_mac_is_told_by_mount(folder, kind, monkeypatch):
    monkeypatch.setattr(safety.os.path, "realpath", lambda p: p)
    found = safety.network_share(folder, windows=False, mounts=safety.mac_mounts(MAC))
    assert (kind in found) if kind else found == ""


WSL = """/dev/sdc / ext4 rw,relatime 0 0
C:\\134 /mnt/c 9p rw,noatime,dirsync,aname=drvfs;path=C:\\;uid=1000;gid=1000;mmap,access=client,trans=fd 0 0
"""


def test_a_folder_a_virtual_machines_host_shares_is_a_warning_never_a_refusal(monkeypatch):
    """9p: WSL2's /mnt/c (the Windows disk of the same computer) or a virtual machine's folder from its host. The
    server refused to start there, as on a network share (B-1963). It is one computer, all of AP Coder's processes in
    the one machine: a warning on the server and on a laptop, never a refusal. Real shares are still refused."""
    monkeypatch.setattr(safety.os.path, "realpath", lambda p: p)
    mounts = safety.linux_mounts(WSL)
    assert safety.network_share("/mnt/c/APCoderData", windows=False, mounts=mounts) == ""
    for server in (True, False):
        status, words = safety.share_problem(Path("/mnt/c/APCoderData"), server, windows=False, mounts=mounts)
        assert status == "WARN" and "9p" in words and "/mnt/c" in words
    assert safety.share_problem(Path("/home/ann/APCoderData"), True, windows=False, mounts=mounts) == ("", "")
    nfs = safety.share_problem(Path("/srv/nfs/APCoder"), True, windows=False, mounts=safety.linux_mounts(LINUX))
    assert nfs[0] == "FAIL"


def test_this_computers_own_folder_is_not_a_share(tmp_path):
    assert safety.network_share(tmp_path) == ""  # (this test computer's real mounts)


def test_the_server_refuses_a_share_and_a_laptop_is_warned(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(safety, "network_share", lambda folder, **k: "a network share (cifs, mounted at /mnt/ap)")
    db = tmp_path / "ap_coder.db"
    refused = safety.serve_refusal(db, server=True)
    assert "network share" in refused and "own disks" in refused and "can be damaged" in refused
    assert safety.serve_refusal(db, server=False) == ""
    assert "WARNING" in capsys.readouterr().err
    assert ("data folder place", "FAIL") == safety.doctor_checks(tmp_path, db, server=True)[0][:2]
    assert ("data folder place", "WARN") == safety.doctor_checks(tmp_path, db, server=False)[0][:2]


def test_serve_on_a_share_never_opens_the_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path))
    monkeypatch.setattr(safety, "network_share", lambda folder, **k: "a network path (\\\\nas\\ap)")
    assert cli.main(["--db", str(tmp_path / "ap_coder.db"), "serve", "--server", "--insecure-http"]) == 2
    assert not (tmp_path / "ap_coder.db").exists() and "network path" in capsys.readouterr().err


# --- No silent new database -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("sign", ["backup", "history", "key", "access log", "invoice", "wal"])
def test_a_missing_database_where_there_was_one_is_never_made_again_empty(tmp_path, sign):
    data, db = tmp_path, tmp_path / "ap_coder.db"
    service.write_config(data, folder="C:\\APCoder\\APProcessor")  # (service install: no sign of a database)
    assert safety.missing_database(data, db) == ""
    if sign == "backup":
        (data / "backups").mkdir()
        (data / "backups" / "ap_coder-20261105-020000.db").write_bytes(b"x")
    elif sign == "history":
        service.write_config(data, history=[{"result": "updated"}])
    elif sign == "key":
        (data / "authenticator.key").write_text("k")
    elif sign == "access log":
        (data / "logs").mkdir()
        (data / "logs" / "access.log").write_text("")
    elif sign == "invoice":
        (data / "invoices" / "2026").mkdir(parents=True)
        (data / "invoices" / "2026" / "a.pdf").write_bytes(b"%PDF")
    else:
        (data / "ap_coder.db-wal").write_bytes(b"")
    said = safety.missing_database(data, db)
    assert "is missing, but this data folder had one" in said and "backup restore" in said
    assert "--new-database" in said


def test_the_server_refuses_to_start_on_a_missing_database_unless_asked(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path))
    db = tmp_path / "ap_coder.db"
    Store(db).backup_now("manual")
    db.unlink()
    for extra in ("-wal", "-shm"):
        db.with_name(db.name + extra).unlink(missing_ok=True)
    assert cli.main(["--db", str(db), "serve", "--server", "--insecure-http"]) == 2
    assert not db.exists() and "is missing" in capsys.readouterr().err
    assert ("server database", "FAIL") == safety.doctor_checks(tmp_path, db, server=True)[1][:2]
    # On purpose: made at once, even when this start stops after (no HTTPS on the console).
    assert cli.main(["--db", str(db), "serve", "--server", "--new-database"]) == 2
    assert db.exists() and "Made a new, empty database" in capsys.readouterr().err
    assert safety.missing_database(tmp_path, db) == ""


def test_a_laptop_still_makes_its_database_at_its_first_start(tmp_path, capsys):
    db = tmp_path / "ap_coder.db"
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "ap_coder-20261105-020000.db").write_bytes(b"x")
    assert safety.serve_refusal(db, server=False) == ""
    assert safety.doctor_checks(tmp_path, db, server=False) == []  # nothing to say on a laptop's own disk


def test_a_fresh_server_starts_with_its_first_database(tmp_path):
    service.write_config(tmp_path, folder="C:\\APCoder\\APProcessor", insecure_http=True)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "service.log").write_text("Service host started\n")  # (written before the dashboard starts)
    assert safety.serve_refusal(tmp_path / "ap_coder.db", server=True) == ""
    assert json.loads((tmp_path / "server.json").read_text())["folder"]
