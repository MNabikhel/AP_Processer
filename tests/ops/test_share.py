"""``python -m ap_coder share`` (D-218): one laptop's AP Coder served to a few colleagues with sign-in. It says what
is missing with the next step, makes what it can without administrator rights, and never starts without sign-in."""

from __future__ import annotations

import json
import shutil
import ssl
import types
from pathlib import Path

import pytest

from ap_coder.core.store import Store
from ap_coder.ops import cli, offline, safety, service, share
from tests.ops.test_safety import team_server_stand_in

GOOD = "correct horse battery staple 42"
SERVE = share.serve  # (the ``laptop`` fixture puts a refusal in its place)


@pytest.fixture
def laptop(tmp_path, monkeypatch):
    """A laptop's data folder with its database, as one person used it (laptop mode). No test starts a server."""
    monkeypatch.setattr(share, "serve", lambda *a, **k: pytest.fail("a test started a real server"))
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path))
    monkeypatch.setenv(offline.SERVER_ENV, "0")
    monkeypatch.setenv("AP_USER_SETTINGS", str(tmp_path / "user-settings.json"))
    for name in ("AP_TLS_CERT", "AP_TLS_KEY", "AP_ALLOWED_HOSTS", "AP_SERVER_PORT", "AP_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(share, "names", lambda: ["AP-LAPTOP"])
    Store(tmp_path / "ap_coder.db")
    return tmp_path, tmp_path / "ap_coder.db"


class Said(list):
    def __call__(self, text: str) -> None:
        self.append(text)

    @property
    def all(self) -> str:
        return "\n".join(self)


def openssl_stand_in(calls: list):
    """An openssl that writes the two files it was asked for."""

    def run(cmd, **kwargs):
        calls.append(cmd)
        for flag in ("-keyout", "-out"):
            Path(cmd[cmd.index(flag) + 1]).write_text("PEM", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    return run


def answers(*given: str):
    left = list(given)
    return lambda prompt: left.pop(0)


def test_with_nothing_there_it_says_each_missing_thing_and_starts_nothing(laptop):
    data, db = laptop
    said = Said()
    assert share.prepare(data, db, said, openssl="") is None
    assert "no openssl" in said.all and "Git for Windows" in said.all and "administrator rights" in said.all
    assert "openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj /CN=AP-LAPTOP" in said.all
    assert "subjectAltName=DNS:AP-LAPTOP" in said.all and str(data / "tls") in said.all
    assert not service.config_path(data).exists() and safety.team_server(data, db) is None  # still one person's


def test_it_makes_the_certificate_the_first_admin_and_the_settings_then_serves_with_sign_in(laptop):
    data, db = laptop
    (data / "user-settings.json").write_text(json.dumps({"reviewer": "Pat Lee"}), encoding="utf-8")
    said, calls = Said(), []
    s = share.prepare(data, db, said, openssl="openssl", run=openssl_stand_in(calls),
                      ask=answers("", ""), password=lambda username, name: GOOD)  # fmt: skip
    assert s is not None and s.https and not s.insecure_http and s.workers == share.WORKERS
    assert calls[0][:2] == ["openssl", "req"] and str(data / "tls" / "apcoder.key.pem") in calls[0]
    assert "Made a certificate for this computer's name (AP-LAPTOP)" in said.all
    # The owner's account: the name used in laptop mode (their approvals stay theirs), an Admin.
    (user,) = Store(db).list_users()
    assert (user["username"], user["display_name"], user["role"]) == ("pat.lee", "Pat Lee", "admin")
    assert "Added pat.lee (Admin)" in said.all and "authenticator app" in said.all
    kept = json.loads(service.config_path(data).read_text(encoding="utf-8"))
    assert kept["certfile"] == str(data / "tls" / "apcoder.pem") and kept["port"] == 8501
    command = service.serve_command(s, "python")
    assert "--server" in command and "--certfile" in command and "--insecure-http" not in command
    # A second time nothing is asked and nothing is made again.
    again = Said()
    assert share.prepare(data, db, again, openssl="", ask=answers(), run=openssl_stand_in(calls)) is not None
    assert len(calls) == 1 and "Added" not in again.all and len(Store(db).list_users()) == 1


def test_after_share_a_plain_start_still_refuses_this_data_without_sign_in(laptop, monkeypatch, capsys):
    """D-211: the double-click start (``serve`` in laptop mode) never opens a team's data folder without sign-in.
    After ``share`` the folder is one, its server running or stopped."""
    from ap_coder.web import server

    data, db = laptop
    started = []
    monkeypatch.setattr(server, "serve", lambda *a, **k: started.append(k) or 0)
    assert share.prepare(data, db, Said(), openssl="openssl", run=openssl_stand_in([]),
                         ask=answers("Pat Lee", "pat"), password=lambda username, name: GOOD) is not None  # fmt: skip
    team = safety.team_server(data, db)
    assert team is not None and team.accounts == 1 and team.settings
    assert cli.main(["--db", str(db), "serve", "--port", "8599", "--no-browser"]) == 2 and started == []
    assert "was not started without sign-in" in capsys.readouterr().err


@pytest.mark.parametrize("installed", [False, True])
def test_after_share_the_refusal_names_share_where_no_service_is_installed(laptop, monkeypatch, installed):
    """B-2352: after ``share``, the shortcut's start was refused with "python -m ap_coder service start", and a
    shared laptop has no service. Where none is installed, it names ``share``, typed in scripts\\terminal.bat."""
    data, db = laptop
    monkeypatch.setattr(service, "installed", lambda s: installed)
    assert share.prepare(data, db, Said(), openssl="openssl", run=openssl_stand_in([]),
                         ask=answers("Pat Lee", "pat"), password=lambda username, name: GOOD) is not None  # fmt: skip
    _why, to_do = safety.team_server(data, db).refusal()
    assert ("service start" in to_do) is installed
    assert ("Start it again with python -m ap_coder share, typed in scripts\\terminal.bat" in to_do) is not installed
    assert "--without-sign-in" in to_do


def test_with_no_console_to_ask_on_the_first_admin_is_said_with_its_command(laptop, monkeypatch):
    data, db = laptop
    monkeypatch.setattr(share.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    said = Said()
    assert share.prepare(data, db, said, openssl="openssl", run=openssl_stand_in([])) is None
    assert 'python -m ap_coder users add --admin <username> --name "<Your Name>"' in said.all
    assert Store(db).list_users() == [] and not service.config_path(data).exists()


def test_the_first_admin_share_made_still_signs_in_after_3_days_while_the_only_admin(laptop, monkeypatch):
    """B-2533: the owner's account ``share`` made has a temporary password, which lasted 3 days: an owner who first
    signed in later was refused, and there was no other Admin to give a new one. While it is the only active Admin
    and has never signed in, it does not expire; a temporary password given later (a colleague's, a reset once it
    signed in, or once there is another Admin) lasts 3 days as before."""
    import time

    from ap_coder.core.store import users

    data, db = laptop
    assert share.prepare(data, db, Said(), openssl="openssl", run=openssl_stand_in([]),
                         ask=answers("Pat Lee", "pat"), password=lambda username, name: GOOD) is not None  # fmt: skip
    store = Store(db)
    store.add_user("ann", "a long clerk password 1", role="clerk", by_user=store.find_user("pat")["id"])
    later = time.time() + users.TEMPORARY_SECONDS + 60
    monkeypatch.setattr(users.time, "time", lambda: later)
    assert store.authenticate("pat", GOOD)["must_change"]  # the first sign-in, 3 days on: let in
    with pytest.raises(users.LoginRefused, match=users.TEMPORARY_EXPIRED[:30]):
        store.authenticate("ann", "a long clerk password 1")  # given by an Admin: 3 days
    with store._conn() as conn:
        conn.execute("UPDATE users SET last_login = '2026-11-01T09:00:00' WHERE username = 'pat'")
    with pytest.raises(users.LoginRefused, match=users.TEMPORARY_EXPIRED[:30]):
        store.authenticate("pat", GOOD)  # once it signed in, a temporary password is one again
    with store._conn() as conn:
        conn.execute("UPDATE users SET last_login = NULL WHERE username = 'pat'")
    store.add_user("kim", "a long admin password 1", role="admin", must_change=False)
    with pytest.raises(users.LoginRefused, match=users.TEMPORARY_EXPIRED[:30]):
        store.authenticate("pat", GOOD)  # another Admin can give a new one


def test_a_first_tests_plain_http_is_never_what_share_starts(laptop):
    data, db = laptop
    Store(db).add_user("pat", GOOD, role="admin")
    service.write_config(data, insecure_http=True)
    said = Said()
    assert share.prepare(data, db, said, openssl="") is None and "no openssl" in said.all  # HTTPS all the same
    s = share.prepare(data, db, Said(), openssl="openssl", run=openssl_stand_in([]))
    assert s is not None and s.https and "--insecure-http" not in service.serve_command(s, "python")


@pytest.mark.skipif(not shutil.which("openssl"), reason="this computer has no openssl")
def test_the_certificate_this_computers_openssl_makes_is_one_the_server_can_use(laptop):
    data, db = laptop
    said = Said()
    files = share.certificate(data, service.settings(data), said)
    assert files == (str(data / "tls" / "apcoder.pem"), str(data / "tls" / "apcoder.key.pem")), said.all
    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(*files)
    names = ssl._ssl._test_decode_cert(files[0])["subjectAltName"]
    assert names == (("DNS", "AP-LAPTOP"),)


def test_the_second_backup_folder_chosen_in_laptop_mode_stays_usable(laptop, tmp_path):
    data, db = laptop
    copies = tmp_path / "second copies"
    copies.mkdir()
    Store(db).set_setting("backup_copy_dir", str(copies))
    said = Said()
    assert share.prepare(data, db, said, openssl="openssl", run=openssl_stand_in([]),
                         ask=answers("Pat Lee", "pat"), password=lambda username, name: GOOD) is not None  # fmt: skip
    assert service.folder_refusal(str(copies), data, server=True) == ""
    assert f"{copies} may be used for the backups' second copy" in said.all
    # Later, a folder an Admin wants is added from the console (never from the web: D-109).
    other = tmp_path / "other"
    said = Said()
    Store(db).set_setting("backup_copy_dir", str(other))
    assert share.prepare(data, db, said, openssl="") is not None
    assert "is not one the server may write to" in said.all and service.folder_refusal(str(other), data, server=True)
    assert share.prepare(data, db, Said(), openssl="", allow=str(other)) is not None
    assert service.folder_refusal(str(other), data, server=True) == ""


def test_a_laptop_whose_admin_signs_in_already_is_shared_with_that_account(laptop, tmp_path):
    """D-243: the laptop's own Admin (its sign-in on) is the shared laptop's first Admin: nothing asked, and the backup
    folder chosen on the laptop is allowed as for a first share."""
    data, db = laptop
    store = Store(db)
    store.set_setting("sign_in", "laptop")
    store.add_first_admin(GOOD, "Pat Lee")
    copies = tmp_path / "second copies"
    copies.mkdir()
    store.set_setting("backup_copy_dir", str(copies))
    said = Said()
    assert share.prepare(data, db, said, openssl="openssl", run=openssl_stand_in([]), ask=answers()) is not None
    assert "Added" not in said.all and [u["username"] for u in Store(db).list_users()] == ["admin"]
    assert service.folder_refusal(str(copies), data, server=True) == ""
    assert safety.team_server(data, db) is not None  # its server.json: a team's from now on (D-211)


def test_the_firewall_rule_is_said_with_its_command_never_made(laptop):
    ran = []

    def netsh(code):
        return lambda cmd, **kwargs: ran.append(cmd) or types.SimpleNamespace(returncode=code, stdout="", stderr="")

    note = share.firewall_note(8501, run=netsh(1), windows=True)
    assert "needs administrator rights" in note and "Run as administrator" in note
    assert 'netsh advfirewall firewall add rule name="AP Coder" dir=in action=allow protocol=TCP localport=8501' in note
    assert share.firewall_note(8501, run=netsh(0), windows=True) == ""  # the rule is there
    assert all(cmd[:4] == ["netsh", "advfirewall", "firewall", "show"] for cmd in ran) and len(ran) == 2


def test_share_serves_in_its_window_and_tells_colleagues_the_address(laptop, monkeypatch, capsys):
    data, db = laptop
    Store(db).add_user("pat", GOOD, role="admin")
    (data / "tls").mkdir()
    for name in (share.CERT_NAME, share.KEY_NAME):
        (data / "tls" / name).write_text("PEM", encoding="utf-8")
    monkeypatch.setattr(share, "firewall_note", lambda port: "")
    started = []

    class Child:
        def __init__(self, cmd, **kwargs):
            started.append((cmd, kwargs["env"]))

        def wait(self):
            return 0

    monkeypatch.setattr(share, "serve", lambda data, s, say: SERVE(data, s, Child, say))  # (no server really starts)
    monkeypatch.setattr(service, "WINDOWS", False)
    assert cli.main(["share"]) == 0
    (cmd, env), said = started[0], capsys.readouterr().out
    assert cmd[1:5] == ["-m", "ap_coder", "serve", "--server"] and "--insecure-http" not in cmd
    assert env["AP_SERVER_MODE"] == "1" and env["AP_PRIVATE_DIR"] == str(data)
    assert "https://AP-LAPTOP:8501" in said and "share --stop" in said and "closing the window" in said


def test_share_starts_nothing_beside_an_ap_coder_already_running_on_this_data(laptop, monkeypatch, capsys):
    data, db = laptop
    httpd, _ = team_server_stand_in(data, https=False, server_mode=False)  # the owner's own window, laptop mode
    try:
        assert cli.main(["share"]) == 1
        assert "Close it first" in capsys.readouterr().out
        assert cli.main(["share", "--stop"]) == 0  # (not shared: nothing of the owner's window is stopped)
        assert "nothing to stop" in capsys.readouterr().out
    finally:
        httpd.shutdown()
    httpd, _ = team_server_stand_in(data)  # shared already
    try:
        assert cli.main(["share"]) == 0
        assert "shared from this computer already" in capsys.readouterr().out
    finally:
        httpd.shutdown()
    (data / ".ap_coder_server.json").unlink()
    assert cli.main(["share", "--stop"]) == 0 and "nothing to stop" in capsys.readouterr().out


def test_share_stop_asks_the_running_server_to_stop(laptop, monkeypatch, capsys):
    data, db = laptop
    asked = []
    monkeypatch.setattr(service, "dashboard_stopped", lambda data, s: asked.append(data) or True)
    httpd, _ = team_server_stand_in(data)
    try:
        assert cli.main(["share", "--stop"]) == 0
    finally:
        httpd.shutdown()
    assert asked == [data] and "Stopped: nobody can sign in" in capsys.readouterr().out


def test_allow_folder_allows_the_folder_while_ap_coder_is_shared_and_says_so(laptop, capsys, tmp_path_factory):
    """B-2297: `share --allow-folder "<folder>"`, the command `share` itself prints, did nothing while AP Coder was
    being shared (the usual moment the owner finds the folder refused on Settings), said nothing of it and ended 0."""
    data, db = laptop
    Store(db).add_user("pat", GOOD, role="admin")
    target = str(tmp_path_factory.mktemp("second copies"))
    assert service.folder_refusal(target, data, server=True)
    httpd, _ = team_server_stand_in(data)  # shared, its window open
    try:
        assert cli.main(["share", "--allow-folder", target]) == 0
        said = capsys.readouterr().out
        assert "shared from this computer already" in said
        assert f"{target} may be used for the backups' second copy" in said and "with no restart" in said
        assert service.folder_refusal(target, data, server=True) == ""  # the running server reads it at each check
        assert service.read_config(data)["allowed_folders"] == [target]
        assert cli.main(["share", "--allow-folder", target]) == 0  # again: said, nothing written twice
        assert f"{target} is allowed already" in capsys.readouterr().out
        assert service.read_config(data)["allowed_folders"] == [target]
    finally:
        httpd.shutdown()
    # Beside the owner's own window (laptop mode) nothing is started or allowed: said, and it does not end 0.
    other = str(tmp_path_factory.mktemp("other"))
    httpd, _ = team_server_stand_in(data, https=False, server_mode=False)
    try:
        assert cli.main(["share", "--allow-folder", other]) == 1
        said = capsys.readouterr().out
        assert "Close it first" in said and f"{other} is not allowed yet" in said
        assert service.folder_refusal(other, data, server=True)
    finally:
        httpd.shutdown()


def test_a_scripted_set_up_gives_the_first_admin_on_standard_input(laptop, monkeypatch, capsys):
    """``share --admin-from-stdin`` (a scripted set-up, CI's Windows job): the first Admin's name, username and
    password are read from standard input, one a line, where the console's questions would need a person (Windows'
    password prompt reads the keyboard, never a pipe)."""
    import io

    data, db = laptop
    (data / "tls").mkdir()
    for name in (share.CERT_NAME, share.KEY_NAME):
        (data / "tls" / name).write_text("PEM", encoding="utf-8")
    monkeypatch.setattr(share, "firewall_note", lambda port: "")
    monkeypatch.setattr(share, "serve", lambda data, s, **kwargs: 0)
    monkeypatch.setattr(share.sys, "stdin", io.StringIO(f"Pat Lee\n\n{GOOD}\n"))
    assert cli.main(["share", "--admin-from-stdin"]) == 0
    (user,) = Store(db).list_users()
    assert (user["username"], user["display_name"], user["role"]) == ("pat.lee", "Pat Lee", "admin")
    assert "Added pat.lee (Admin)" in capsys.readouterr().out


def test_on_the_owners_laptop_share_starts_two_capture_processes_unless_told_otherwise(laptop, monkeypatch):
    """B-2312: ``share`` serves from a laptop its owner works on all day, but its capture processes were sized as the
    company server's (the cores less two: 18 on a 20-core laptop, about 700 MB each). It starts two, as it starts two
    web workers; server.json's ``capture_processes`` or AP_CAPTURE_PROCESSES still decide when they say."""
    data, db = laptop
    monkeypatch.delenv("AP_CAPTURE_PROCESSES", raising=False)
    Store(db).add_user("pat", GOOD, role="admin")

    def shared():
        s = share.prepare(data, db, Said(), openssl="openssl", run=openssl_stand_in([]))
        return s.capture_processes, service.serve_env(s, data).get("AP_CAPTURE_PROCESSES")

    assert shared() == (share.CAPTURE_PROCESSES, "2")
    service.write_config(data, capture_processes=5)
    assert shared() == (5, "5")
    monkeypatch.setenv("AP_CAPTURE_PROCESSES", "3")
    assert shared() == (3, "3")


def test_after_the_laptop_is_renamed_deleting_shares_certificate_makes_one_for_the_new_name(laptop, monkeypatch):
    """B-2313: the certificate names the laptop as it was called (an address is never in it, so a new IP changes
    nothing). Renamed, colleagues are told the new name, which that certificate does not cover. The way to a new one
    is to delete the two files ``share`` made and run it again, but server.json named them by then, and share refused
    ("server.json names files that are not there"), leaving the owner to edit server.json. It makes new ones for the
    new name; certificate files of IT's own, named in server.json and missing, are still refused."""
    data, db = laptop
    calls = []
    assert share.prepare(data, db, Said(), openssl="openssl", run=openssl_stand_in(calls),
                         ask=answers("Pat Lee", "pat"), password=lambda username, name: GOOD) is not None  # fmt: skip
    cert = share.certificate_command("openssl", data / "tls")
    assert "subjectAltName=DNS:AP-LAPTOP" in cert and not any("IP:" in part for part in cert)
    monkeypatch.setattr(share, "names", lambda: ["AP-LAPTOP-2"])
    for name in (share.CERT_NAME, share.KEY_NAME):
        (data / "tls" / name).unlink()
    said = Said()
    s = share.prepare(data, db, said, openssl="openssl", run=openssl_stand_in(calls))
    assert s is not None and s.certfile == str(data / "tls" / share.CERT_NAME) and len(calls) == 2, said.all
    assert "subjectAltName=DNS:AP-LAPTOP-2" in calls[1] and "Made a certificate for this computer's name" in said.all
    assert share.address(s) == "https://AP-LAPTOP-2:8501"
    service.write_config(data, certfile=str(data / "it.pem"), keyfile=str(data / "it.key.pem"))
    said = Said()
    assert share.prepare(data, db, said, openssl="openssl", run=openssl_stand_in(calls)) is None
    assert "server.json names files that are not there" in said.all and len(calls) == 2


@pytest.mark.parametrize("before", ["/Users/pat/APCoder/tls/", "C:\\Users\\pat\\APCoder\\tls\\"])
def test_after_the_data_folder_moved_share_uses_its_own_certificate_where_the_folder_is_now(laptop, before):
    """server.json keeps the whole paths of share's own two files. The data folder moved (a new laptop, another user
    name, off OneDrive), they named the old place, and share said "server.json names files that are not there... take
    certfile and keyfile out of server.json": a hand edit of JSON (as B-2313). Its own files are this folder's, made
    again when they did not come with it, and server.json names them where they are; IT's own are still refused."""
    data, db = laptop
    calls = []
    assert share.prepare(data, db, Said(), openssl="openssl", run=openssl_stand_in(calls),
                         ask=answers("Pat Lee", "pat"), password=lambda username, name: GOOD) is not None  # fmt: skip
    here = (str(data / "tls" / share.CERT_NAME), str(data / "tls" / share.KEY_NAME))
    there = {"certfile": before + share.CERT_NAME, "keyfile": before + share.KEY_NAME}
    service.write_config(data, **there)  # (as the folder came: written where it was before)
    said = Said()
    s = share.prepare(data, db, said, openssl="openssl", run=openssl_stand_in(calls))
    assert s is not None and (s.certfile, s.keyfile) == here and len(calls) == 1, said.all
    kept = service.read_config(data)
    assert (kept["certfile"], kept["keyfile"]) == here
    service.write_config(data, **there)
    shutil.rmtree(data / "tls")  # (copied without them)
    s = share.prepare(data, db, said, openssl="openssl", run=openssl_stand_in(calls))
    assert s is not None and (s.certfile, s.keyfile) == here and len(calls) == 2, said.all
    service.write_config(data, certfile=there["certfile"], keyfile=before + "it.key.pem")
    said = Said()
    assert share.prepare(data, db, said, openssl="openssl", run=openssl_stand_in(calls)) is None
    assert "server.json names files that are not there" in said.all and len(calls) == 2
