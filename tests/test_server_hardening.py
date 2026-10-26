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

from .test_server_update import FakeServer, _bundle, _folder

ROOT = Path(__file__).resolve().parents[1]
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

    from .test_server_security import account, app_for, client_of

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    account(store, "clerk1", "clerk")
    app = app_for(store)
    return {"store": store, "app": app, "client": lambda ip="10.0.0.5": client_of(app, ip)}


def _signed_in(web, username: str = "clerk1"):
    from .test_server_security import log_in

    client = web["client"]()
    assert log_in(client, username).status_code == 200
    return client


def test_a_large_login_request_is_refused_before_it_is_read(web):
    from .webclient import PAGE

    client = web["client"]()
    big = {"username": "x", "password": "y" * 100_000}
    assert client.post("/login", data=big, headers=PAGE).status_code == 413
    chunks = (b"username=x&password=" + b"y" * 50_000 for _ in range(3))  # no Content-Length: streamed
    answer = client.post(
        "/login", content=chunks, headers={**PAGE, "content-type": "application/x-www-form-urlencoded"}
    )
    assert answer.status_code == 413


def test_the_login_takes_no_multipart(web):
    from .webclient import PAGE

    client = web["client"]()
    answer = client.post("/login", files={"username": (None, "clerk1"), "password": (None, GOOD)}, headers=PAGE)
    assert answer.status_code == 415 and SESSION_COOKIE not in answer.cookies


def test_a_signed_in_request_has_a_generous_limit_too(web, monkeypatch):
    from ap_coder.web import security

    from .webclient import PAGE

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

    from .webclient import PAGE

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

    from .webclient import PAGE

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

    from .webclient import add_clean

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
    from .test_server_security import account, log_in
    from .webclient import PAGE

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
    from .test_server_security import account, log_in
    from .webclient import PAGE

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
    from .test_server_security import account, log_in
    from .webclient import PAGE

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
