"""The second security review of server mode (D-109): what an administrator's commands trust, the update bundle's
SHA-256, request sizes, uploads, lockouts, separation of duties, folders, the audit, TLS and sessions."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from ap_coder.core import config
from ap_coder.core.store import Store
from ap_coder.ops import cli, server_update, service
from ap_coder.web.identity import SESSION_COOKIE
from tests.ops.test_server_update import FakeServer, _bundle, _folder

ROOT = Path(__file__).resolve().parents[2]
GOOD = "correct horse battery"


# --- 1. The service's account can't reach the administrator's commands through the data folder ------------------------


def test_a_env_file_never_sets_what_decides_which_code_runs(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("PYTHONPATH=C:\\evil\nPIP_INDEX_URL=http://evil\nPYTHONSTARTUP=x.py\nAP_LLM_MODEL=gemma\n")
    for name in ("PYTHONPATH", "PIP_INDEX_URL", "PYTHONSTARTUP", "AP_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    config.Settings.from_env(env)
    assert os.environ.get("AP_LLM_MODEL") == "gemma"  # the settings themselves still load
    assert "PYTHONPATH" not in os.environ and "PIP_INDEX_URL" not in os.environ and "PYTHONSTARTUP" not in os.environ


@pytest.mark.parametrize("argv", [["server-update", "--back", "--yes"], ["service", "status"]])
def test_an_administrators_command_reads_no_env_file(tmp_path, monkeypatch, argv):
    data = tmp_path / "data"
    data.mkdir()
    (data / ".env").write_text("AP_TLS_CERT=C:\\planted.pem\nAZURE_OPENAI_ENDPOINT=https://x\n")
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    for name in ("AP_TLS_CERT", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    seen = {}
    monkeypatch.setattr(server_update, "main", lambda args: seen.update(os.environ) or 0)
    monkeypatch.setattr(service, "main", lambda args: seen.update(os.environ) or 0)
    assert cli.main(argv) == 0
    assert "AP_TLS_CERT" not in seen and "AZURE_OPENAI_ENDPOINT" not in seen


def test_the_new_folders_pythons_get_a_clean_explicit_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "evil"))
    monkeypatch.setenv("PIP_FIND_LINKS", str(tmp_path / "evil"))
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret")
    runs = []

    def run(cmd, **kwargs):
        runs.append((cmd, kwargs["env"]))
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(server_update.subprocess, "run", run)
    folder = _folder(tmp_path / "APProcessor-0.4.0", "0.4.0")
    srv = server_update.Server(tmp_path / "data", service.ServerSettings(), say=lambda m: None)
    srv.set_up(folder)
    srv.migrate(folder, tmp_path / "data" / "ap_coder.db")
    srv.self_check(folder)
    assert len(runs) == 3
    for cmd, env in runs:
        assert cmd[1:3] == ["-E", "-s"], cmd  # no PYTHON* variable, no user site-packages
        assert not [k for k in env if k.upper().startswith(("PYTHON", "PIP_"))], env
        assert "AZURE_OPENAI_API_KEY" not in env
        assert env["AP_PRIVATE_DIR"] == str(tmp_path / "data") and env["AP_SERVER_MODE"] == "1"


@pytest.fixture
def server(tmp_path, monkeypatch):
    data = tmp_path / "APCoderData"
    data.mkdir()
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    current = _folder(tmp_path / "apps" / "APProcessor", "0.3.0")
    service.write_config(data, folder=str(current), insecure_http=True)
    service.write_runner(data, current)
    monkeypatch.setattr(server_update, "running_folder", lambda: current)
    db = data / "ap_coder.db"
    Store(db)
    return {"data": data, "current": current, "db": db, "tmp": tmp_path}


def test_server_update_refuses_a_folder_in_server_json_that_is_not_the_one_it_runs_from(server, capsys):
    planted = _folder(server["tmp"] / "Users" / "Public" / "APProcessor", "0.3.0")  # a folder the service could write
    service.write_config(server["data"], folder=str(planted))
    bundle = _bundle(server["tmp"])
    fake = FakeServer()
    sha = server_update.file_sha256(bundle)
    assert server_update.update(bundle, server["data"], server["db"], server=fake, sha256=sha) == 1
    assert fake.calls == [] and not (planted.parent / "APProcessor-0.4.0").exists()
    assert "this command runs from" in capsys.readouterr().err


def test_going_back_refuses_a_previous_folder_that_is_not_beside_this_one(server, capsys):
    planted = _folder(server["tmp"] / "Users" / "Public" / "APProcessor-0.2.0", "0.2.0")
    backup = Store(server["db"]).backup_now("before-update-0.3.0")
    service.write_config(server["data"], previous_folder=str(planted), pre_update_backup=str(backup))
    fake = FakeServer()
    assert server_update.go_back(server["data"], server["db"], server=fake, yes=True) == 1
    assert fake.calls == []
    assert "not beside this AP Coder folder" in capsys.readouterr().err


# --- 2. The update bundle's SHA-256, given apart from it --------------------------------------------------------------


def test_server_update_refuses_a_bundle_without_its_sha256(server, capsys):
    fake = FakeServer()
    assert server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=fake) == 1
    assert fake.calls == [] and "--sha256" in capsys.readouterr().err
    assert not (server["current"].parent / "APProcessor-0.4.0").exists()


def test_server_update_refuses_a_bundle_whose_sha256_is_not_the_one_given(server, capsys):
    fake = FakeServer()
    code = server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=fake, sha256="ab" * 32)
    assert code == 1 and fake.calls == [] and "not the bundle that was built" in capsys.readouterr().err


def test_no_hash_check_warns_and_is_recorded(server, capsys):
    fake = FakeServer()
    code = server_update.update(_bundle(server["tmp"]), server["data"], server["db"], server=fake, hash_check=False)
    assert code == 0
    assert "WARNING" in capsys.readouterr().err
    assert service.read_config(server["data"])["history"][-1]["bundle_hash"].startswith("not checked")


def test_the_command_line_passes_the_sha256(server, monkeypatch):
    seen = {}
    monkeypatch.setattr(server_update, "update", lambda bundle, data, db, **k: seen.update(k) or 0)
    assert cli.main(["server-update", "b.zip", "--sha256", "ab" * 32]) == 0
    assert seen["sha256"] == "ab" * 32 and seen["hash_check"] is True
    assert cli.main(["server-update", "b.zip", "--no-hash-check"]) == 0
    assert seen["hash_check"] is False


def test_the_bundle_builder_writes_the_zips_sha256_beside_it(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_offline_bundle

    zip_path = _bundle(tmp_path)
    digest, path = build_offline_bundle.write_checksum(zip_path)
    assert path == tmp_path / f"{zip_path.name}.sha256"
    assert path.read_text(encoding="utf-8") == f"{digest}  {zip_path.name}\n"
    assert digest == server_update.file_sha256(zip_path)


# --- 3. Pillow opens only the picture formats AP Coder takes in -------------------------------------------------------


def _picture(fmt: str) -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format=fmt)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["PCX", "ICO", "TGA"])
def test_a_picture_in_another_format_is_never_decoded(fmt):
    import io

    from PIL import UnidentifiedImageError

    from ap_coder.ai import page_reader
    from ap_coder.capture import layout

    data = _picture(fmt)
    with pytest.raises(UnidentifiedImageError):
        layout.open_image(io.BytesIO(data), "invoice.png")
    with pytest.raises(page_reader.PageReaderError):
        page_reader._open_picture(data)


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "TIFF", "WEBP", "GIF", "BMP"])
def test_the_picture_formats_taken_in_still_open(fmt):
    import io

    from ap_coder.ai import page_reader
    from ap_coder.capture import layout

    data = _picture(fmt)
    with layout.open_image(io.BytesIO(data), f"invoice.{fmt.lower()}") as img:
        assert img.size == (40, 30)
    assert page_reader._open_picture(data).size == (40, 30)


def test_the_package_minimums_leave_out_the_versions_with_known_vulnerabilities():
    import tomllib

    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert "pillow>=13" in deps and "certifi>=2026.10.19" in deps
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pip-audit" in workflow


# --- 4. and 5. Request sizes, and uploads on the server ---------------------------------------------------------------


@pytest.fixture
def web(tmp_path):
    from ap_coder.core.store import load_sample_setup
    from tests.web.test_server_security import account, app_for, client_of

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    account(store, "clerk1", "clerk")
    app = app_for(store)
    return {"store": store, "app": app, "client": lambda ip="10.0.0.5": client_of(app, ip)}


def _signed_in(web, username: str = "clerk1"):
    from tests.web.test_server_security import log_in

    client = web["client"]()
    assert log_in(client, username).status_code == 200
    return client


def test_a_large_login_request_is_refused_before_it_is_read(web):
    from tests.webclient import PAGE

    client = web["client"]()
    big = {"username": "x", "password": "y" * 100_000}
    assert client.post("/login", data=big, headers=PAGE).status_code == 413
    chunks = (b"username=x&password=" + b"y" * 50_000 for _ in range(3))  # no Content-Length: streamed
    answer = client.post(
        "/login", content=chunks, headers={**PAGE, "content-type": "application/x-www-form-urlencoded"}
    )
    assert answer.status_code == 413


def test_the_login_takes_no_multipart(web):
    from tests.webclient import PAGE

    client = web["client"]()
    answer = client.post("/login", files={"username": (None, "clerk1"), "password": (None, GOOD)}, headers=PAGE)
    assert answer.status_code == 415 and SESSION_COOKIE not in answer.cookies


def test_a_signed_in_request_has_a_generous_limit_too(web, monkeypatch):
    from ap_coder.web import security
    from tests.webclient import PAGE

    client = _signed_in(web)
    monkeypatch.setattr(security, "BODY_LIMIT", 200_000)
    files = [("files", ("a.pdf", b"%PDF-1.4 " + b"x" * 300_000, "application/pdf"))]
    assert client.post("/process/upload", files=files, headers=PAGE).status_code == 413


def test_the_login_page_sends_its_form_urlencoded():
    page = (ROOT / "ap_coder" / "web" / "templates" / "login.html").read_text(encoding="utf-8")
    script = (ROOT / "ap_coder" / "web" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "data-urlencoded" in page and "URLSearchParams(body)" in script


def test_an_upload_reads_no_file_before_its_checks_and_one_at_a_time(web, monkeypatch):
    from ap_coder.web.pages import process
    from tests.webclient import PAGE

    client = _signed_in(web)
    read = []
    monkeypatch.setattr(process, "ready_to_read", lambda steps: False)
    real = process._read_upload
    monkeypatch.setattr(process, "_read_upload", lambda upload: read.append(upload.filename) or real(upload))
    files = [("files", (f"{n}.pdf", b"%PDF-1.4 x", "application/pdf")) for n in range(3)]
    answer = client.post("/process/upload", files=files, headers=PAGE)
    assert answer.status_code == 400 and read == []  # the setup isn't done: no file read at all


def test_an_upload_of_too_many_files_is_refused_on_the_server(web, monkeypatch):
    from ap_coder.web.pages import process
    from tests.webclient import PAGE

    client = _signed_in(web)
    monkeypatch.setattr(process, "MAX_REQUEST_FILES", 2)
    files = [("files", (f"{n}.pdf", b"%PDF-1.4 x", "application/pdf")) for n in range(3)]
    answer = client.post("/process/upload", files=files, headers=PAGE)
    assert answer.status_code == 413 and "up to 2 per upload" in answer.json()["error"]


def test_each_account_has_an_hourly_upload_allowance(monkeypatch):
    from ap_coder.web.pages import process

    monkeypatch.setattr(process, "_uploads", __import__("collections").defaultdict(__import__("collections").deque))
    monkeypatch.setattr(process, "MAX_BYTES_PER_WINDOW", 1000)
    assert process.upload_refusal(7, 1, 600, now=10.0) == ("", 0)
    text, status = process.upload_refusal(7, 1, 600, now=20.0)
    assert status == 429 and "last hour" in text
    assert process.upload_refusal(8, 1, 600, now=20.0) == ("", 0)  # another account
    assert process.upload_refusal(7, 1, 600, now=10.0 + process.UPLOAD_WINDOW + 1) == ("", 0)  # an hour later


# --- 6. Lockouts: from anywhere, and a shared address that keeps its people ------------------------------------------


@pytest.fixture
def clock(monkeypatch):
    from ap_coder.core.store import users

    class Clock:
        now = 1_800_000_000.0

        def __call__(self) -> float:
            return self.now

    found = Clock()
    monkeypatch.setattr(users, "_clock", found)
    return found


def _fail(store, username, ip, times=1):
    from ap_coder.core.store.users import LoginRefused

    for _ in range(times):
        with pytest.raises(LoginRefused):
            store.authenticate(username, "wrong wrong wrong", ip)


def test_guesses_at_one_account_from_many_addresses_pause_it(tmp_path, clock):
    from ap_coder.core.store.users import LOCKED, LoginRefused

    store = Store(tmp_path / "ap_coder.db")
    store.add_user("boss", GOOD, role="admin", must_change=False)
    for n in range(10):  # 10 addresses, one guess each: no address is paused for it, the account is
        _fail(store, "boss", f"10.9.0.{n}")
    with pytest.raises(LoginRefused, match=LOCKED[:20]):
        store.authenticate("boss", GOOD, "10.9.1.1")
    assert any(e["action"] == "login_locked" and e["detail"].get("by") == "account" for e in store.events())
    clock.now += 61  # a minute, then twice as long after each further guess
    _fail(store, "boss", "10.9.1.2")
    clock.now += 61
    with pytest.raises(LoginRefused, match=LOCKED[:20]):
        store.authenticate("boss", GOOD, "10.9.1.3")
    clock.now += 60
    assert store.authenticate("boss", GOOD, "10.9.1.3")["username"] == "boss"


def test_an_account_paused_from_anywhere_still_logs_in_from_its_own_address(tmp_path, clock):
    store = Store(tmp_path / "ap_coder.db")
    store.add_user("ann", GOOD, must_change=False)
    assert store.authenticate("ann", GOOD, "10.8.8.8")  # her desk
    clock.now += 60
    for n in range(12):
        _fail(store, "ann", f"203.0.113.{n}")
    assert store.authenticate("ann", GOOD, "10.8.8.8")["username"] == "ann"
    names = store.paused_usernames()
    assert names == [] or all(p["address"] != "*" for p in names)  # her login ended the account-wide count


def test_a_shared_address_paused_by_guesses_keeps_the_people_who_log_in_there(tmp_path, clock):
    from ap_coder.core.store.users import LoginRefused

    store = Store(tmp_path / "ap_coder.db")
    store.add_user("ann", GOOD, must_change=False)
    store.add_user("cleo", GOOD, must_change=False)
    assert store.authenticate("ann", GOOD, "10.1.1.1")  # the office's NAT
    for n in range(30):
        _fail(store, f"spray{n}", "10.1.1.1")
    assert store.authenticate("ann", GOOD, "10.1.1.1")["username"] == "ann"
    with pytest.raises(LoginRefused):  # someone who never logged in from there waits, as before
        store.authenticate("cleo", GOOD, "10.1.1.1")


def test_an_admin_lifts_an_account_wide_pause(tmp_path, clock):
    from ap_coder.core.store.users import ANYWHERE

    store = Store(tmp_path / "ap_coder.db")
    store.add_user("ann", GOOD, must_change=False)
    for n in range(10):
        _fail(store, "ann", f"10.7.0.{n}")
    assert {"username": "ann", "address": ANYWHERE} in [{k: p[k] for k in ("username", "address")}
                                                         for p in store.paused_usernames()]  # fmt: skip
    assert store.unlock_username("ann", ANYWHERE, actor="boss")
    assert store.authenticate("ann", GOOD, "10.7.1.1")["username"] == "ann"


# --- 7. Separation of duties ------------------------------------------------------------------------------------------


def _waiting(store, approver_id: int, name: str = "Boss") -> int:
    from ap_coder.core.store import PENDING
    from tests.webclient import add_clean

    store.set_setting("approval_limit", "100")
    invoice_id = add_clean(store)
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], name, login=f"user:{approver_id}")
    assert store.get_invoice(invoice_id)["status"] == PENDING
    return invoice_id


def test_the_final_approval_always_checks_the_approval_shown(tmp_path):
    from ap_coder.core.store import ApprovedAgain

    store = Store(tmp_path / "ap_coder.db")
    invoice_id = _waiting(store, 1)
    with pytest.raises(ApprovedAgain):
        store.final_approve(invoice_id, "Ann", login="user:2", shown=None)
    with pytest.raises(TypeError):
        store.final_approve(invoice_id, "Ann", login="user:2")  # noqa  (shown must be given)
    assert store.get_invoice(invoice_id)["status"] == "pending_approval"


def test_the_final_approval_page_that_posts_no_approval_is_refused(web):
    from tests.web.test_server_security import account, log_in
    from tests.webclient import PAGE

    store = web["store"]
    first = account(store, "ann", "approver")
    second = account(store, "bob", "approver")
    assert first != second
    invoice_id = _waiting(store, first, "Ann")
    client = web["client"]()
    assert log_in(client, "bob").status_code == 200
    client.post(f"/review/{invoice_id}/final-approve", headers=PAGE)
    assert store.get_invoice(invoice_id)["status"] == "pending_approval"


def test_an_admin_cant_give_the_second_approval_through_an_account_they_made(web):
    from tests.web.test_server_security import account, log_in
    from tests.webclient import PAGE

    store = web["store"]
    boss = account(store, "boss", "admin")
    admin = web["client"]()
    assert log_in(admin, "boss").status_code == 200
    assert admin.post("/admin/users/add", data={"username": "puppet", "name": "Puppet", "role": "approver"},
                      headers=PAGE).json()["ok"]  # fmt: skip
    puppet = store.find_user("puppet")["id"]
    invoice_id = _waiting(store, boss)
    with pytest.raises(PermissionError, match="added or reset"):
        store.final_approve(invoice_id, "Puppet", login=f"user:{puppet}", shown=store.latest_approval_id(invoice_id))
    other = account(store, "mlee", "approver")
    admin.post(f"/admin/users/{other}/reset", headers=PAGE)  # a reset too
    with pytest.raises(PermissionError, match="added or reset"):
        store.final_approve(invoice_id, "M Lee", login=f"user:{other}", shown=store.latest_approval_id(invoice_id))
    with store._conn() as conn:  # 8 days later
        conn.execute("UPDATE events SET created_at = '2000-01-01T00:00:00' WHERE action IN ('user_added', "
                     "'password_reset')")  # fmt: skip
    store.final_approve(invoice_id, "Puppet", login=f"user:{puppet}", shown=store.latest_approval_id(invoice_id))
    assert store.get_invoice(invoice_id)["status"] == "approved"


def test_an_admin_cant_give_the_second_approval_through_a_chain_of_accounts_or_behind_1000_events(web):
    """B-1497: the vouch rule (D-109) saw only the accounts the first approver added or reset themselves, and looked
    for the first approval among the invoice's newest 1000 events. An Admin's second Admin account adding the second
    approver's account (or resetting a colleague's password), or 1000 notes on the invoice, let one Admin give both
    approvals; and after 1000 events the page posted no approval, so an honest second approver was refused."""
    from tests.web.test_server_security import account, log_in
    from tests.webclient import PAGE

    store = web["store"]
    boss = account(store, "boss", "admin")
    carol = store.add_user("carol", GOOD, role="admin", by_user=boss)  # boss's second Admin account
    puppet = store.add_user("puppet", GOOD, role="approver", by_user=carol)
    dave = account(store, "dave", "approver")  # a colleague's own account...
    store.reset_password(dave, by_user=carol)  # ...whose password carol reset
    direct = store.add_user("direct", GOOD, role="approver", by_user=boss)
    invoice_id = _waiting(store, boss)
    shown = store.latest_approval_id(invoice_id)
    for user_id, name in ((puppet, "Puppet"), (dave, "Dave")):
        with pytest.raises(PermissionError, match="added or reset"):
            store.final_approve(invoice_id, name, login=f"user:{user_id}", shown=shown)
    for n in range(1000):
        store.add_note(invoice_id, "Direct", f"note {n}")
    with pytest.raises(PermissionError, match="added or reset"):
        store.final_approve(invoice_id, "Direct", login=f"user:{direct}", shown=shown)
    account(store, "erin", "approver")
    erin = web["client"]("10.0.0.9")
    assert log_in(erin, "erin").status_code == 200
    assert f'name="approval" value="{shown}"' in erin.get(f"/review/{invoice_id}").text
    erin.post(f"/review/{invoice_id}/final-approve", data={"approval": shown}, headers=PAGE)
    assert store.get_invoice(invoice_id)["status"] == "approved"


def test_the_poster_can_be_kept_apart_from_the_approvers(tmp_path):
    store = Store(tmp_path / "ap_coder.db")
    invoice_id = _waiting(store, 1)
    store.final_approve(invoice_id, "Ann", login="user:2", shown=store.latest_approval_id(invoice_id))
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET export_batch = 7 WHERE id = ?", (invoice_id,))
    assert store.posting_refusal(7, 2) == ""  # off by default
    store.set_setting("poster_not_approver", "1")
    assert f"#{invoice_id}" in store.posting_refusal(7, 2) and f"#{invoice_id}" in store.posting_refusal(7, 1)
    assert store.posting_refusal(7, 3) == ""  # someone who approved none of them


def test_the_setting_is_an_admins_on_the_review_tab(web):
    from tests.web.test_server_security import account, log_in
    from tests.webclient import PAGE

    store = web["store"]
    account(store, "boss", "admin")
    admin = web["client"]()
    assert log_in(admin, "boss").status_code == 200
    page = admin.get("/settings/review").text
    assert 'name="poster_not_approver"' in page
    version = __import__("re").search(r'name="version" value="([^"]*)"', page)
    data = {"duties_shown": "1", "poster_not_approver": "1", **({"version": version.group(1)} if version else {})}
    assert admin.post("/settings/review", data=data, headers=PAGE).json()["ok"]
    assert store.poster_not_approver()


def test_the_controls_report_lists_one_person_at_several_steps_on_the_server(tmp_path):
    import datetime as dt

    from ap_coder.erp import controls

    store = Store(tmp_path / "ap_coder.db")
    invoice_id = _waiting(store, 1, "Ann")
    store.log_event("exported", invoice_id=invoice_id, actor="Ann", detail={"batch": 1})
    today = dt.date.today()
    report = controls.build(store, today, today, duties=True)
    assert [(s["invoice_id"], s["person"], s["steps"]) for s in report["same_person"]] == [
        (invoice_id, "Ann", ["approved", "exported"])
    ]
    assert "One person at several steps (1)" in controls.report_html(report)
    laptop = controls.build(store, today, today)
    assert laptop["same_person"] is None and "One person at several steps" not in controls.report_html(laptop)


# --- 8. The folders an Admin types: on the server, only those IT allows -----------------------------------------------


def test_on_the_server_a_typed_folder_must_be_one_it_allows(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    allowed = tmp_path / "share" / "APCoder"
    allowed.mkdir(parents=True)
    service.write_config(data, allowed_folders=[str(allowed)])
    refuse = lambda folder: service.folder_refusal(folder, data, server=True)  # noqa: E731
    assert refuse(str(allowed)) == "" and refuse(str(allowed / "backups")) == ""
    assert refuse("\\\\attacker\\share") and refuse(str(tmp_path / "share")) and refuse(str(allowed) + "-evil")
    assert refuse(str(allowed / ".." / "other"))
    assert service.folder_refusal("\\\\attacker\\share", data, server=False) == ""  # the laptop: as before
    service.write_config(data, allowed_folders=[])
    assert "none yet" in refuse(str(allowed))


def test_the_backup_copy_folder_is_refused_before_it_is_asked_anything(web, tmp_path, monkeypatch):
    from pathlib import Path as RealPath

    from tests.web.test_server_security import account, log_in
    from tests.webclient import PAGE

    data = tmp_path / "private"
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    store = web["store"]
    account(store, "boss", "admin")
    admin = web["client"]()
    assert log_in(admin, "boss").status_code == 200
    asked = []
    real = RealPath.is_dir
    monkeypatch.setattr(RealPath, "is_dir", lambda self: asked.append(str(self)) or real(self))
    answer = admin.post("/settings/data/backup-copy", data={"folder": "\\\\attacker\\share"}, headers=PAGE).json()
    assert not answer["ok"] and "IT allows" in answer["error"]
    assert not [a for a in asked if "attacker" in a] and store.get_setting("backup_copy_dir") == ""


def test_a_backup_copy_folder_saved_before_is_not_used_on_the_server(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_SERVER_MODE", "1")
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path))
    store = Store(tmp_path / "ap_coder.db")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    store.set_setting("backup_copy_dir", str(elsewhere))
    store.backup_now()
    assert list(elsewhere.iterdir()) == [] and "IT allows" in store.get_setting("backup_copy_status")


def test_service_install_keeps_the_allowed_folders(tmp_path):
    s = service.settings(tmp_path, allowed_folders="D:\\Backups;\\\\fileserver\\APCoder")
    assert s.allowed_folders == ("D:\\Backups", "\\\\fileserver\\APCoder")
    service.save_settings(tmp_path, s)
    assert service.read_config(tmp_path)["allowed_folders"] == ["D:\\Backups", "\\\\fileserver\\APCoder"]


def test_the_finance_hub_folder_is_refused_unless_it_allows_it(web, tmp_path, monkeypatch):
    from tests.web.test_server_security import account, log_in
    from tests.webclient import PAGE

    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path / "private"))
    store = web["store"]
    account(store, "boss", "admin")
    admin = web["client"]()
    assert log_in(admin, "boss").status_code == 200
    form = {"enabled": "1", "mode": "folder", "folder": "\\\\attacker\\share", "every_hours": "24", "format": "csv",
            "link_base": "https://apcoder.company.local"}  # fmt: skip
    answer = admin.post("/settings/hub", data=form, headers=PAGE).json()
    assert not answer["ok"] and "IT allows" in answer["error"]


# --- 9. The access log, and downloads in the audit trail --------------------------------------------------------------


def test_the_server_logs_each_request_and_records_each_download(web):
    from tests.web.test_server_security import account, log_in

    store = web["store"]
    account(store, "boss", "admin")
    admin = web["client"]()
    assert log_in(admin, "boss").status_code == 200
    backup = store.backup_now("manual")
    assert admin.get(f"/settings/data/backups/{backup.name}").status_code == 200
    assert admin.get("/activity/events.csv").status_code == 200
    log = (store.path.parent / "logs" / "access.log").read_text(encoding="utf-8")
    assert f"10.0.0.5 boss GET /settings/data/backups/{backup.name} 200" in log
    assert "10.0.0.5 - POST /login 200" in log  # before the login: no user yet
    downloads = store.events(actions=["downloaded"])
    assert {e["detail"]["file"] for e in downloads} >= {backup.name}
    assert all(e["actor"] == "Boss" for e in downloads)
    assert len(downloads) == 2


def test_a_newline_in_the_path_cant_forge_a_line_in_the_access_log(web):
    log = web["store"].path.parent / "logs" / "access.log"
    before = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    web["client"]().get("/review%0Afake%0Dline")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(before) + 1
    assert "/review%0Afake%0Dline" in lines[-1]


def test_the_laptop_keeps_no_access_log(tmp_path):
    from ap_coder.core.store import load_sample_setup
    from tests.webclient import client_for

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    assert client_for(store).get("/activity/events.csv").status_code == 200
    assert not (store.path.parent / "logs" / "access.log").exists()
    assert store.events(actions=["downloaded"]) == []


# --- 11. TLS ciphers and the server header ----------------------------------------------------------------------------


def test_the_https_server_takes_only_modern_ciphers_and_says_nothing_of_uvicorn(tmp_path):
    import ssl

    from tests.web.test_server_https import CERT, KEY, _get, _start, _stop

    proc, port = _start(tmp_path, "--server", "--certfile", str(CERT), "--keyfile", str(KEY))
    try:
        trusted = ssl.create_default_context(cafile=str(CERT))
        status, headers = _get(f"https://localhost:{port}/healthz", trusted)
        assert status == 200 and "server" not in headers
        old = ssl.create_default_context(cafile=str(CERT))
        old.maximum_version = ssl.TLSVersion.TLSv1_2
        old.set_ciphers("AES128-SHA")  # TLS 1.2 without forward secrecy, CBC with SHA-1
        with pytest.raises(OSError):
            _get(f"https://localhost:{port}/healthz", old, timeout=2)
        modern = ssl.create_default_context(cafile=str(CERT))
        modern.maximum_version = ssl.TLSVersion.TLSv1_2
        modern.set_ciphers("ECDHE+AESGCM")
        assert _get(f"https://localhost:{port}/healthz", modern)[0] == 200
    finally:
        _stop(proc)


# --- 12. OneLake: its own endpoints only ------------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["https://onelake.dfs.fabric.microsoft.com/ws/item/Files",
                                 "https://westus-onelake.dfs.fabric.microsoft.com/ws/item/Files"])  # fmt: skip
def test_onelake_and_its_regional_endpoints_are_taken(url):
    from ap_coder.erp.hub import onelake

    assert onelake._check_url(url, onelake.ONELAKE_HOSTS, "OneLake folder URL").hostname in url


@pytest.mark.parametrize("url", ["https://attacker.dfs.core.windows.net/x", "https://evil.dfs.fabric.microsoft.com/x",
                                 "https://onelake.dfs.fabric.microsoft.com.evil.net/x",
                                 "https://a.b-onelake.dfs.fabric.microsoft.com/x",
                                 "http://onelake.dfs.fabric.microsoft.com/x"])  # fmt: skip
def test_another_storage_account_is_refused_for_onelake(url):
    from ap_coder.erp.hub import onelake
    from ap_coder.erp.hub.landing import HubError

    with pytest.raises(HubError):
        onelake._check_url(url, onelake.ONELAKE_HOSTS, "OneLake folder URL")


# --- 13. Sessions and passwords --------------------------------------------------------------------------------------


def test_a_login_lasts_30_idle_minutes_and_12_hours_unless_it_says_otherwise(tmp_path, clock):
    store = Store(tmp_path / "ap_coder.db")
    uid = store.add_user("ann", GOOD, must_change=False)
    token = store.start_session(uid)
    clock.now += 31 * 60
    assert store.session_user(token) is None
    service.write_config(tmp_path, session_idle_minutes=60, session_hours=4)
    assert service.session_limits(tmp_path) == (3600.0, 4 * 3600.0)
    service.write_config(tmp_path, session_idle_minutes=1, session_hours=10_000)
    assert service.session_limits(tmp_path) == (5 * 60.0, 168 * 3600.0)  # kept within bounds


def test_the_session_cookie_ends_with_the_browser(web):
    from tests.web.test_server_security import log_in

    answer = log_in(web["client"](), "clerk1")
    cookie = answer.headers["set-cookie"].lower()
    assert "ap_session=" in cookie and "max-age" not in cookie and "expires" not in cookie


def test_a_temporary_password_expires_after_3_days(tmp_path):
    from ap_coder.core.store.users import TEMPORARY_EXPIRED, LoginRefused

    store = Store(tmp_path / "ap_coder.db")
    store.add_user("ann", GOOD)  # temporary: must change
    assert store.authenticate("ann", GOOD, "10.0.0.5")["must_change"]
    with store._conn() as conn:
        conn.execute("UPDATE events SET created_at = '2000-01-01T00:00:00' WHERE action = 'user_added'")
    with pytest.raises(LoginRefused, match=TEMPORARY_EXPIRED[:30]):
        store.authenticate("ann", GOOD, "10.0.0.5")
    store.reset_password(store.find_user("ann")["id"], actor="boss", password="another good secret")
    assert store.authenticate("ann", "another good secret", "10.0.0.5")


def test_wrong_current_passwords_on_the_password_page_log_the_person_out(web):
    from tests.web.test_server_security import log_in
    from tests.webclient import PAGE

    client = web["client"]()
    assert log_in(client, "clerk1").status_code == 200
    form = {"current": "not my password at all", "new": "a brand new secret", "again": "a brand new secret"}
    for _ in range(4):
        assert "doesn't match" in client.post("/account/password", data=form, headers=PAGE).json()["error"]
    assert "logged out" in client.post("/account/password", data=form, headers=PAGE).json()["error"]
    assert client.get("/review").status_code == 303  # the session ended
    assert "paused" in log_in(client, "clerk1").json()["error"]


def test_a_person_sees_and_ends_their_other_sessions(web):
    from tests.web.test_server_security import log_in
    from tests.webclient import PAGE

    here, there = web["client"](), web["client"]("10.0.0.9")
    assert log_in(here, "clerk1").status_code == 200 and log_in(there, "clerk1").status_code == 200
    assert "also logged in on 1 other" in here.get("/account/password").text
    assert here.post("/account/sessions/end-others", headers=PAGE).json()["ok"]
    assert there.get("/review").status_code == 303 and here.get("/review").status_code == 200


# --- 14. What each role sees: the setup report, jobs, saved layouts --------------------------------------------------


def _as(web, username, role):
    from tests.web.test_server_security import account, log_in

    account(web["store"], username, role)
    client = web["client"]()
    assert log_in(client, username).status_code == 200
    return client


def test_the_setup_report_is_an_admins_on_the_server(web):
    reader = _as(web, "reader", "readonly")
    assert reader.get("/help/report/setup").status_code == 403
    assert 'data-url="/help/report/setup"' not in reader.get("/help/report").text
    admin = _as(web, "boss", "admin")
    assert 'data-url="/help/report/setup"' in admin.get("/help/report").text


def test_where_the_servers_log_is_kept_is_shown_to_an_admin_only(web):
    """B-1407: Report a problem showed every role the path of the server's log on the server."""
    from ap_coder.ops import service

    name = service.log_path(web["store"].path.parent).name
    reader = _as(web, "reader", "readonly")
    assert name not in reader.get("/help/report").text
    admin = _as(web, "boss", "admin")
    assert name in admin.get("/help/report").text


def test_a_jobs_progress_is_shown_to_the_role_that_may_start_it(web):
    from ap_coder.web.roles import JOB_NEEDS

    web["app"].state.jobs.get("settings-doctor")  # an Admin's check, run once
    web["app"].state.jobs.get("process")
    clerk = web["client"]()
    from tests.web.test_server_security import log_in

    assert log_in(clerk, "clerk1").status_code == 200
    assert clerk.get("/jobs/settings-doctor").status_code == 404
    assert clerk.get("/jobs/process").status_code == 200
    assert JOB_NEEDS["settings-doctor"] == "admin"
    admin = _as(web, "boss", "admin")
    assert admin.get("/jobs/settings-doctor").status_code == 200


def test_a_clerks_saved_layouts_are_their_own_and_the_shared_default_an_admins(web):
    from ap_coder.erp import directory
    from tests.webclient import PAGE

    store = web["store"]
    admin = _as(web, "boss", "admin")
    assert admin.post("/search/presets", data={"name": "Team", "query": "col=vendor", "make_default": "1"},
                      headers=PAGE).json()["ok"]  # fmt: skip
    clerk = web["client"]()
    from tests.web.test_server_security import log_in

    assert log_in(clerk, "clerk1").status_code == 200
    assert clerk.post("/search/presets", data={"name": "Mine", "query": "col=total", "make_default": "1"},
                      headers=PAGE).json()["ok"]  # fmt: skip
    assert clerk.post("/search/presets/delete", data={"name": "Team"}, headers=PAGE).status_code == 403
    assert [p["name"] for p in directory.presets(store)] == ["Team"]  # the shared ones: unchanged
    assert directory.default_preset(store)["name"] == "Team"  # everyone else's default too
    assert directory.default_preset(store, f"user:{store.find_user('clerk1')['id']}")["name"] == "Mine"
    page = clerk.get("/search").text
    assert "Mine" in page and "Team" in page
