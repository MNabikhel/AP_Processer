"""The server's data folder (``ops/safety.py``, D-188): never on a network share (SQLite's write-ahead log is damaged
there), and never a new, empty database where there was one."""

from __future__ import annotations

import http.server
import json
import ssl
import threading
from pathlib import Path

import pytest

from ap_coder.core.store import Store
from ap_coder.ops import cli, offline, safety, service

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


def test_a_laptop_whose_database_is_gone_while_its_backups_remain_is_told_how_to_get_it_back(tmp_path, capsys):
    """It started as new with no word: the pilot tester saw an empty AP Coder, and nothing said the backups in the
    folder still held the invoices. It still starts (the server alone refuses, D-188), with a warning."""
    db = tmp_path / "ap_coder.db"
    assert safety.serve_refusal(db, server=False) == "" and "WARNING" not in capsys.readouterr().err  # a new laptop
    (tmp_path / "invoices").mkdir()
    (tmp_path / "invoices" / "a.pdf").write_bytes(b"%PDF")  # (dropped there before the first start: no sign here)
    assert safety.serve_refusal(db, server=False) == "" and "WARNING" not in capsys.readouterr().err
    Store(db).backup_now("manual")
    assert safety.serve_refusal(db, server=False) == "" and "WARNING" not in capsys.readouterr().err  # it is there
    for path in tmp_path.glob("ap_coder.db*"):
        path.unlink()
    assert safety.serve_refusal(db, server=False) == ""
    err = capsys.readouterr().err
    assert err.startswith("WARNING: ") and f"The database {db} was not there" in err
    assert f"1 backup in {tmp_path / 'backups'}" in err and "starts with a new, empty one" in err
    assert "Settings → Data & backups" in err and "Restore this state" in err and "manager" not in err.lower()
    assert not db.exists()  # (made by the start itself, as before)


def test_a_fresh_server_starts_with_its_first_database(tmp_path):
    service.write_config(tmp_path, folder="C:\\APCoder\\APProcessor", insecure_http=True)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "service.log").write_text("Service host started\n")  # (written before the dashboard starts)
    assert safety.serve_refusal(tmp_path / "ap_coder.db", server=True) == ""
    assert json.loads((tmp_path / "server.json").read_text())["folder"]


# --- A data folder that serves a team is never opened without sign-in by accident (D-211) -----------------------------

FIXTURES = Path(__file__).parents[1] / "fixtures"
CERT = FIXTURES / "TEST_ONLY_self_signed_localhost.cert.pem"  # a test-only certificate for localhost, never used else
KEY = FIXTURES / "TEST_ONLY_self_signed_localhost.key.pem"
GOOD = "correct horse battery staple 42"


@pytest.fixture
def laptop(tmp_path, monkeypatch):
    """A laptop's data folder (laptop mode: ``AP_SERVER_MODE`` off) and its database."""
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path))
    monkeypatch.setenv(offline.SERVER_ENV, "0")  # (restored afterwards: serve --server sets it)
    monkeypatch.setenv("BROWSER", "none")
    for name in ("AP_TLS_CERT", "AP_TLS_KEY", "AP_ALLOWED_HOSTS", "AP_SERVER_PORT"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path, tmp_path / "ap_coder.db"


def team_server_stand_in(data: Path, *, https: bool = True, server_mode: bool = True):
    """A stand-in for the server-mode dashboard of ``data``: /healthz on a free port (HTTPS with a self-signed
    certificate, or plain HTTP), and its server file in the data folder. Returns (the server, its port)."""
    answer = {"ok": True, "app": "ap-coder", "version": "0.3.0", "pid": 4242, "server_mode": server_mode}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            body = json.dumps({**answer, "port": port}).encode()
            self.send_response(200 if self.path == "/healthz" else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    if https:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(CERT), str(KEY))
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    (data / ".ap_coder_server.json").write_text(json.dumps({"pid": 4242, "port": port, "token": "secret"}))
    return httpd, port


@pytest.fixture
def started(monkeypatch):
    """The dashboards a test's ``serve`` commands started (none really starts): what each was given."""
    from ap_coder.web import server

    calls: list[dict] = []
    monkeypatch.setattr(server, "serve", lambda *a, **k: calls.append(k) or 0)
    return calls


def test_a_plain_laptop_data_folder_still_starts_as_before(laptop, started, capsys):
    data, db = laptop
    assert safety.team_server(data, db) is None  # no database yet: the first start
    assert cli.main(["--db", str(db), "serve", "--port", "8599", "--no-browser"]) == 0 and len(started) == 1
    Store(db)  # (its database, made by that first start in real life)
    assert safety.sign_in_accounts(db) == 0 and safety.team_server(data, db) is None
    assert cli.main(["--db", str(db), "serve", "--port", "8599", "--no-browser"]) == 0 and len(started) == 2
    assert "sign-in" not in capsys.readouterr().err


def test_a_stopped_servers_data_is_not_opened_without_sign_in(laptop, started, capsys):
    """B-2119: a data folder with sign-in accounts opened in laptop mode, with no sign-in, whenever its server was
    stopped (the night, a restart): ``serve`` refuses, whether or not anything answers on its port."""
    data, db = laptop
    Store(db).add_user("jsmith", GOOD, role="admin")
    team = safety.team_server(data, db)
    assert team is not None and not team.running and team.accounts == 1 and team.address == ""
    assert cli.main(["--db", str(db), "serve", "--port", "8599", "--no-browser"]) == 2 and started == []
    said = capsys.readouterr().err
    assert "set up to serve your team" in said and "1 sign-in account" in said
    assert "was not started without sign-in" in said and "--without-sign-in" in said
    assert sorted(p.name for p in data.glob("ap_coder.db*")) == ["ap_coder.db"]  # (only read: nothing left beside it)


def test_before_the_packages_are_installed_the_sign_in_codes_key_tells(laptop, monkeypatch):
    """The launcher's first phase in a new AP Coder folder runs on the standard library alone: the store, which reads
    the accounts, can't be loaded yet. The key made when the first person set up their authenticator app tells."""
    import sys

    from ap_coder.core import authenticator

    data, db = laptop
    Store(db).add_user("jsmith", GOOD, role="admin")
    monkeypatch.setitem(sys.modules, "ap_coder.core.store.users", None)  # (as with no packages: ImportError)
    assert safety.sign_in_accounts(db) == 0 and safety.team_server(data, db) is None
    assert authenticator.load_key(data, create=True)
    team = safety.team_server(data, db)
    assert team is not None and team.key and not team.running
    assert "the sign-in codes' key" in team.refusal()[0] and team.refusal(on_purpose=True) == ("", "")


def test_server_settings_alone_mark_a_team_servers_data_folder(laptop, started, capsys, monkeypatch):
    """Before its first account: server.json says this data folder is a server's, and where the team signs in."""
    data, db = laptop
    service.write_config(data, port=8443, certfile=str(CERT), keyfile=str(KEY), allowed_hosts=["apcoder.example"])
    team = safety.team_server(data, db)
    assert team is not None and not team.running and team.accounts == 0
    assert team.address == "https://apcoder.example:8443"
    (data / ".ap_coder_server.json").write_text(json.dumps({"pid": 4242, "port": 8502}))  # left by a start that
    assert safety.team_server(data, db).address == "https://apcoder.example:8443"  # was killed: not the team's port
    assert cli.main(["--db", str(db), "serve", "--port", "8599", "--no-browser"]) == 2 and started == []
    said = capsys.readouterr().err
    assert "server.json" in said and "https://apcoder.example:8443" in said
    service.write_config(data, allowed_hosts=[])  # no name given: this computer's own
    monkeypatch.setattr(safety.socket, "gethostname", lambda: "AP-LAPTOP")
    assert safety.team_server(data, db).address == "https://AP-LAPTOP:8443"


def test_laptop_mode_again_is_a_step_taken_on_purpose(laptop, started, capsys):
    """The way back to laptop mode on the same data: only with the option that says what it does."""
    data, db = laptop
    Store(db).add_user("jsmith", GOOD, role="admin")
    code = cli.main(["--db", str(db), "serve", "--port", "8599", "--no-browser", "--without-sign-in"])
    assert code == 0 and len(started) == 1
    said = capsys.readouterr().err
    assert "WARNING" in said and "no sign-in" in said and "1 sign-in account" in said
    assert Store(db).user_count() == 1  # the accounts stay, unused


@pytest.mark.parametrize("https", [True, False])
def test_a_team_server_running_here_is_never_joined_by_a_laptop_mode_start(laptop, started, capsys, https):
    """B-2119: beside the team's server (HTTPS with a self-signed certificate; plain HTTP for a first test), a
    laptop-mode ``serve`` took the next port and opened the same database with no sign-in. Told by what answers,
    before any account or server.json is there; and not even on purpose while it runs."""
    data, db = laptop
    httpd, port = team_server_stand_in(data, https=https)
    try:
        team = safety.team_server(data, db)
        assert team is not None and team.running
        assert team.address.startswith("https://" if https else "http://") and team.address.endswith(f":{port}")
        for more in ([], ["--without-sign-in"]):
            assert cli.main(["--db", str(db), "serve", "--port", "8599", "--no-browser", *more]) == 2
            said = capsys.readouterr().err
            assert f"AP Coder is serving your team from this computer (sign in at {team.address})" in said
            assert "it was not started a second time" in said
        assert started == []
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_laptop_mode_ap_coder_running_here_is_not_a_team_server(laptop):
    data, db = laptop
    httpd, _port = team_server_stand_in(data, https=False, server_mode=False)
    try:
        assert safety.team_server(data, db) is None
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_refusal_opens_the_teams_address_only_while_it_answers(laptop, started, monkeypatch):
    from ap_coder.web import server

    data, db = laptop
    opened: list[str] = []
    monkeypatch.setattr(server.webbrowser, "open", lambda url: opened.append(url) or True)
    monkeypatch.setattr(server, "browser_wanted", lambda: True)
    service.write_config(data, port=8443, certfile=str(CERT), keyfile=str(KEY))
    assert cli.main(["--db", str(db), "serve", "--port", "8599"]) == 2 and opened == []  # stopped: nothing to open
    httpd, _port = team_server_stand_in(data)
    try:
        address = safety.team_server(data, db).address
        assert cli.main(["--db", str(db), "serve", "--port", "8599"]) == 2 and opened == [address]
        assert cli.main(["--db", str(db), "serve", "--port", "8599", "--no-browser"]) == 2 and opened == [address]
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert started == []


def test_server_mode_itself_starts_on_its_own_data(laptop, started):
    data, db = laptop
    Store(db).add_user("jsmith", GOOD, role="admin")
    service.write_config(data, port=8599)
    assert cli.main(["--db", str(db), "serve", "--server", "--insecure-http", "--port", "8599"]) == 0
    assert len(started) == 1


def test_looking_for_a_team_server_on_a_new_data_folder_makes_no_database(tmp_path):
    """B-2317: the check before a laptop-mode start (D-211) opened the database to count its sign-in accounts, and
    SQLite made an empty file where there was none: the first start then took the new data folder for an old one
    and never gave it the Simple screens and the JD Edwards settings (web/core.first_run)."""
    from ap_coder.web.core import EVERYTHING, SCREENS, SIMPLE, first_run

    db = tmp_path / "ap_coder.db"
    assert safety.team_server(tmp_path, db) is None
    assert not db.exists()
    store = first_run(db, server=False)
    assert store.get_setting(SCREENS, EVERYTHING) == SIMPLE


# --- One laptop-mode AP Coder a data folder (B-2351) ------------------------------------------------------------------

LAPTOP_STAND_IN = """
import http.server, json, os
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = json.dumps({"ok": True, "app": "ap-coder", "pid": os.getpid(), "server_mode": False}).encode()
        self.send_response(200 if self.path == "/healthz" else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
s = http.server.HTTPServer(("127.0.0.1", 0), H)
print(s.server_address[1], flush=True)
s.serve_forever()
"""


def test_a_second_laptop_mode_serve_on_the_same_data_is_refused(laptop, started, capsys):
    """B-2351: ``python -m ap_coder serve`` typed in scripts\\terminal.bat while the shortcut's AP Coder ran started on
    the next port against the same database, overwrote the server file, then removed it as it stopped: the first one
    ran unseen, and ``backup restore`` went ahead under it. Refused, and said where it is open."""
    import subprocess
    import sys

    from ap_coder.ops import backup_cmd

    data, db = laptop
    first = subprocess.Popen([sys.executable, "-c", LAPTOP_STAND_IN], stdout=subprocess.PIPE, text=True)
    try:
        port = int(first.stdout.readline())
        record = json.dumps({"pid": first.pid, "port": port, "token": "the-first-ones"})
        (data / ".ap_coder_server.json").write_text(record)
        assert cli.main(["--db", str(db), "serve", "--port", "8599", "--no-browser"]) == 2 and started == []
        said = capsys.readouterr().err
        assert f"AP Coder is already open on this data folder ({data}) at http://127.0.0.1:{port}" in said
        assert (data / ".ap_coder_server.json").read_text() == record and backup_cmd._running(data)
    finally:
        first.kill()
        first.wait(10)
    # left by one that was killed: no longer refused
    assert cli.main(["--db", str(db), "serve", "--port", "8599", "--no-browser"]) == 0 and len(started) == 1
