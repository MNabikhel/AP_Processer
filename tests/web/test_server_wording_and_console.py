"""The company server, walked end to end (D-102): its wording, its audit, its console and its docs, as staff and IT
meet them. Each test names the bug it fixed (docs/archive/bugs-2026-10.md, B-1100 on)."""

from __future__ import annotations

import socket
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import PENDING, Store, load_sample_setup
from ap_coder.ops import cli, doctor, offline, server_update, service
from ap_coder.web import core
from ap_coder.web.app import create_app
from ap_coder.web.pages import process
from tests.conftest import SAMPLES
from tests.webclient import PAGE, add_clean, web_settings

ROOT = Path(__file__).resolve().parents[2]
GOOD = "correct horse battery"
STEM = "harbourview_NS_HST_HPS-2026-0347"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


def client_as(store: Store, role: str = "", name: str = "") -> TestClient:
    """The dashboard as an account with ``role`` sees it on the server (logged in); no role: the laptop."""
    app = create_app(web_settings(), store, start_reader=False, server=bool(role))
    client = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    if role:
        username = f"{role}-{len(store.list_users())}"
        uid = store.add_user(username, GOOD, role=role, display_name=name or username.title())
        with store._conn() as conn:
            conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
        answer = client.post("/login", data={"username": username, "password": GOOD}, headers=PAGE)
        assert answer.status_code in (200, 303), answer.text
    return client


def approved_first(store: Store) -> tuple[TestClient, int]:
    """An Approver's client, and an invoice over the approval limit they gave the first approval."""
    store.set_setting("approval_limit", "100")
    invoice_id = add_clean(store)
    approver = client_as(store, "approver", "Ann Approver")
    uid = next(u["id"] for u in store.list_users() if u["display_name"] == "Ann Approver")
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Ann Approver", login=f"user:{uid}")
    assert store.get_invoice(invoice_id)["status"] == PENDING
    return approver, invoice_id


# --- Help, the welcome page, Import invoices, Settings: as the server is (B-1103, B-1105, B-1108 to B-1111) -----------


def test_help_shows_the_servers_folders_to_an_admin_only(store):
    """B-1103: Help → "For IT: the folders" showed the server's paths to every role."""
    folders = "For IT: the folders"
    assert folders in client_as(store, "admin").get("/help").text
    for role in ("readonly", "clerk", "approver"):
        page = client_as(store, role).get("/help").text
        assert folders not in page and str(store.path.parent) not in page, role
        assert "kept on the server" in page and "kept on this computer" not in page
    assert folders in client_as(store).get("/help").text  # the laptop: as before
    assert "kept on this computer" in client_as(store).get("/help").text


def test_on_the_server_lm_studio_is_its_or_an_admins(store, monkeypatch):
    """B-1105: the welcome page, Import invoices, Help and Settings → Reading told staff to start LM Studio, which runs
    on the server."""
    from ap_coder.ai import reading

    monkeypatch.setattr(
        reading,
        "reading_status",
        lambda *a, **k: reading.ReadingStatus(
            lm_studio=False, banner="LM Studio isn't running: invoices are read by OCR only and wait for a person."
        ),
    )
    said = "LM Studio runs on the server; ask IT or an Admin"
    empty = Store(store.path.parent.parent / "empty" / "ap_coder.db")
    for role in ("clerk", "readonly"):
        welcome = client_as(empty, role).get("/review").text
        assert said in welcome and "Developer tab → Start server" not in welcome and "Start LM Studio" not in welcome
    imports = client_as(empty, "clerk").get("/process").text
    assert said in imports and "Developer tab → Start server" not in imports
    help_page = client_as(store, "clerk").get("/help").text
    assert said in help_page and "Open LM Studio, Developer tab" not in help_page
    assert "(start it, then its server)" not in help_page
    reading_page = client_as(store, "admin").get("/settings/reading").text
    assert said in reading_page and "To start LM Studio on this computer" not in reading_page
    laptop = client_as(empty).get("/review").text  # as before
    assert said not in laptop and "Developer tab → Start server" in laptop


def test_settings_review_on_the_server_speaks_of_accounts_and_the_company(store):
    """B-1108: Settings → Review said "one laptop with one Windows login" and put the company's province under "You"
    as the person's own."""
    page = client_as(store, "admin").get("/settings/review").text
    assert "one Windows login" not in page and "another account" in page
    assert "The company's province" in page and "Your province" not in page
    laptop = client_as(store).get("/settings/review").text
    assert "one Windows login" in laptop and "Your province" in laptop


def test_the_second_approval_tooltip_says_the_same_account(store):
    """B-1109: "You approved it first (same name or computer login)" on the server, where it is the same account."""
    approver, invoice_id = approved_first(store)
    for url in (f"/review/{invoice_id}", "/review?tab=second"):
        page = approver.get(url).text
        assert "You approved it first (the same account)" in page and "computer login)" not in page, url


def test_settings_data_and_help_say_on_the_server(store):
    """B-1110: Settings → Data, Reading, About, Azure and Help said "on this computer" of the server."""
    admin = client_as(store, "admin")
    for url in ("/settings/data", "/settings/reading", "/help"):
        page = admin.get(url).text
        assert "on this computer" not in page and "on the server" in page, url
    assert "stays on this computer" in client_as(store).get("/settings/data").text  # the laptop: as before


def test_the_quick_start_on_the_server_leaves_out_the_laptop(store):
    """B-1111: Help → Quick start showed staff the laptop's install, its 127.0.0.1 address and its folders."""
    page = client_as(store, "clerk").get("/help/quick-start").text
    assert "On the company server" in page and "Your password" in page
    assert "127.0.0.1" not in page and "APProcessor.bat" not in page and "C:\\Users" not in page
    laptop = client_as(store).get("/help/quick-start").text
    assert "127.0.0.1" in laptop and "APProcessor.bat" in laptop and "On the company server" in laptop


# --- Setup messages: who to ask (B-1112) ------------------------------------------------------------------------------


def test_on_the_server_the_setup_findings_say_an_admin(store, monkeypatch):
    """B-1112: the JD Edwards vendor error said "ask the pilot tester" on the server, where an Admin sets it up."""
    from ap_coder.erp import jde

    output = {"vendor_name": "Nobody Ltd.", "invoice_number": "1", "currency": "CAD", "grand_total": 10}
    settings = jde.JdeSettings()
    monkeypatch.delenv(offline.SERVER_ENV, raising=False)
    laptop = [f[2] for f in jde.voucher_findings(output, settings, "")]  # no settings yet, no AN8
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    server = [f[2] for f in jde.voucher_findings(output, settings, "")]
    assert any("an Admin" in t for t in server) and not any("pilot tester" in t for t in server)
    assert any("Settings → JD Edwards E1 → Supplier address numbers" in t for t in laptop)  # where, since B-1297
    assert offline.setup_contact("Ask the pilot tester to map it") == "Ask an Admin to map it"
    help_page = client_as(store, "clerk").get("/help").text
    assert "ask the pilot tester" not in help_page.lower() and "ask an admin" in help_page.lower()


# --- The second approval by the same account (B-1113) -----------------------------------------------------------------


def test_a_second_approval_by_the_same_account_is_refused_with_409(store):
    """B-1113: the final approval by the account that gave the first answered {"ok": true} with only a message."""
    approver, invoice_id = approved_first(store)
    answer = approver.post(f"/review/{invoice_id}/final-approve", headers=PAGE)
    assert answer.status_code == 409
    assert answer.json()["ok"] is False and "another account" in answer.json()["error"]
    assert store.get_invoice(invoice_id)["status"] == PENDING


# --- The audit: who uploaded, loaded the demo, imported the setup (B-1106) --------------------------------------------


def test_uploads_demo_and_setup_imports_record_who_on_the_server(store, monkeypatch):
    """B-1106: an upload ("processed"), the demo and the setup imports were recorded with no person (actor NULL)."""
    admin = client_as(store, "admin", "Ada Admin")
    assert admin.post("/demo/load", data={"back": "/process"}, headers=PAGE).status_code == 200
    processed = store.events(actions=["processed"])
    assert processed and all(e["actor"] == "Ada Admin" for e in processed)
    csv = b"code,description\n6999,Test account\n"
    up = admin.post("/accounts/gl_accounts/upload", files={"file": ("gl.csv", csv, "text/csv")}, headers=PAGE)
    assert up.status_code == 200, up.text
    before = {e["id"] for e in store.events()}
    token = up.json()["redirect"].split("file=")[1].split("&")[0].split("#")[0]
    columns = {"token": token, "file": token, "col_code": "code", "col_description": "description"}
    done = admin.post("/accounts/gl_accounts/import", data=columns, headers=PAGE)
    assert done.status_code == 200, done.text
    new = [e for e in store.events() if e["id"] not in before]
    assert new and all(e["actor"] == "Ada Admin" for e in new), new

    # An upload: the import runs in its own thread, as the person who uploaded it.
    def fake_process_files(store_, settings_, paths, **kwargs):
        def work(job):
            for p in paths:
                store_.add_invoice(p, {"vendor_name": "X"}, {"requires_review": True})
            return {"label": "Processed", "messages": []}

        return work

    monkeypatch.setattr(process, "process_files", fake_process_files)
    clerk = client_as(store, "clerk", "Cleo Clerk")
    fresh = (SAMPLES / f"{STEM}.pdf").read_bytes() + b"\n% not one of the demo's\n"  # (the demo has each sample)
    sent = [("files", ("fresh.pdf", fresh, "application/pdf"))]
    assert clerk.post("/process/upload", files=sent, headers=PAGE).json()["ok"]
    job = clerk.app.state.jobs.get("process")
    for _ in range(100):
        job.wait(5)
        if not job.running:
            break
        threading.Event().wait(0.05)
    assert store.events(actions=["processed"])[0]["actor"] == "Cleo Clerk"


def test_an_event_without_an_actor_stays_so_on_a_laptop(store):
    from ap_coder.core.store import EVENT_ACTOR

    store.log_event("setup_changed", None, None, {})
    assert store.events(actions=["setup_changed"])[0]["actor"] is None
    token = EVENT_ACTOR.set("Ada Admin")
    try:
        store.log_event("setup_changed", None, None, {})
        store.log_event("setup_changed", None, "Someone Named", {})
    finally:
        EVENT_ACTOR.reset(token)
    assert [e["actor"] for e in store.events(actions=["setup_changed"])] == ["Someone Named", "Ada Admin", None]


# --- Small items (B-1114 to B-1119) -----------------------------------------------------------------------------------


def test_the_rail_logs_out_at_once(store):
    """B-1114: the rail's "Log out" opened a page asking to confirm; it is a POST form, sent with the page's header."""
    client = client_as(store, "clerk")
    page = client.get("/review").text
    assert 'action="/logout"' in page and 'href="/logout"' not in page
    answer = client.post("/logout", headers=PAGE)
    assert answer.status_code == 200 and answer.json()["redirect"].startswith("/login")
    assert client.get("/review").status_code == 303


def test_serve_server_stops_on_a_port_in_use_and_says_so_first(tmp_path, monkeypatch, capsys):
    """B-1115: ``serve --server`` printed "AP Coder server: https://…" before "Port in use…" (which said http), and
    off the server's port it would have moved to the next free one: staff were given a fixed address."""
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path))
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        code = cli.main(["--db", str(tmp_path / "ap.db"), "serve", "--server", "--insecure-http", "--host",
                         "127.0.0.1", "--port", str(port)])  # fmt: skip
    err = capsys.readouterr().err
    assert code == 1
    assert f"Port {port} is in use" in err and "AP Coder server:" not in err and "http://" not in err
    assert "--port" in err
    monkeypatch.setattr(offline, "free_port", lambda *a, **k: pytest.fail("the server never picks another port"))
    from ap_coder.web import server

    monkeypatch.setattr(server, "serve", lambda *a, **k: 0)
    monkeypatch.setattr(server, "port_refusal", lambda host, port, https=False: "")
    assert cli.main(["--db", str(tmp_path / "ap.db"), "serve", "--server", "--insecure-http"]) == 0
    assert ":8501" in capsys.readouterr().err


def test_head_login_answers_200(store):
    """B-1116: HEAD /login (a monitor's check) answered 405."""
    client = client_as(store, "clerk")
    client.cookies.clear()
    assert client.head("/login").status_code == 200


def test_a_bundle_missing_one_file_says_1_file(tmp_path):
    """B-1117: "1 files its manifest lists are missing"."""
    import json
    import zipfile

    from tests.ops.test_server_update import _bundle

    made = _bundle(tmp_path, "0.9.0")
    with zipfile.ZipFile(made) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    manifest = json.loads(members["APProcessor/bundle_manifest.json"])
    manifest["files"]["ap_coder/gone.py"] = "x"  # listed, not in the ZIP
    members["APProcessor/bundle_manifest.json"] = json.dumps(manifest).encode()
    bundle = tmp_path / "APProcessor-offline-0.9.0-b.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    with pytest.raises(server_update.BundleError) as caught:
        server_update.check_bundle(bundle)
    assert "1 file its manifest lists is missing" in str(caught.value)


def test_the_host_refusal_names_service_install_allowed_hosts_as_the_guide_does(store):
    """B-1118: the refusal of an unknown name said only AP_ALLOWED_HOSTS; SERVER.md says service install
    --allowed-hosts. Both say both."""
    app = create_app(web_settings(), store, start_reader=False, server=True)
    refused = TestClient(app, base_url="https://not-the-server.example").get("/login")
    assert refused.status_code == 400
    assert "service install --allowed-hosts" in refused.text and "AP_ALLOWED_HOSTS" in refused.text
    guide = (ROOT / "docs" / "SERVER.md").read_text(encoding="utf-8")
    row = next(line for line in guide.splitlines() if "not one of the server's names" in line)
    assert "service install --allowed-hosts" in row and "AP_ALLOWED_HOSTS" in row


class NoService(server_update.Server):
    def __init__(self) -> None:
        pass

    def installed(self) -> bool:
        return False


def test_a_dry_run_without_the_service_says_there_is_nothing_to_stop(tmp_path, monkeypatch, capsys):
    """B-1119: ``server-update --dry-run`` said "Stop the service" on a server where none is installed."""
    from tests.ops.test_server_update import _bundle, _folder

    data = tmp_path / "APCoderData"
    data.mkdir()
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    current = _folder(tmp_path / "apps" / "APProcessor", "0.3.0")
    service.write_config(data, folder=str(current), insecure_http=True)
    db = data / "ap_coder.db"
    Store(db)
    monkeypatch.setattr(server_update, "running_folder", lambda: current)
    bundle = _bundle(tmp_path)
    sha = server_update.file_sha256(bundle)
    assert server_update.update(bundle, data, db, server=NoService(), dry_run=True, sha256=sha) == 0
    out = capsys.readouterr().out
    assert "Stop the service" not in out and "nothing to stop" in out and "Start the service" not in out


# --- The console: users activate (B-1120) -----------------------------------------------------------------------------


def test_users_activate_turns_an_account_on_again(tmp_path, capsys):
    """B-1120: the console could turn an account off but not on again (only the Users page could)."""
    db = str(tmp_path / "ap_coder.db")
    store = Store(db)
    store.add_user("boss", GOOD, role="admin")
    ann = store.add_user("ann", GOOD, role="clerk")
    assert cli.main(["--db", db, "users", "deactivate", "ann"]) == 0
    assert not store.user(ann)["active"]
    assert cli.main(["--db", db, "users", "activate", "ANN"]) == 0
    assert store.user(ann)["active"] and "can log in again" in capsys.readouterr().out
    assert store.events(actions=["user_activated"])[0]["actor"] == "the server console"
    assert cli.main(["--db", db, "users", "activate", "nobody"]) == 1


# --- The doctor on the server (B-1107) --------------------------------------------------------------------------------


def test_doctor_says_what_it_sees_on_the_server(tmp_path, monkeypatch):
    """B-1107: on the server ``doctor`` said "dashboard network: this computer only" and checked nothing of the
    server's own: its HTTPS files, its names, its service."""
    data = tmp_path / "APCoderData"
    (data / "tls").mkdir(parents=True)
    cert, key = data / "tls" / "apcoder.pem", data / "tls" / "apcoder.key.pem"
    cert.write_text("cert")
    service.write_config(data, certfile=str(cert), keyfile=str(key), allowed_hosts=["apcoder.company.local"])
    checks = {area: (status, detail) for area, status, detail in service.doctor_checks(data, env={}, windows=False)}
    assert checks["dashboard network"][0] == "PASS" and "this computer only" not in checks["dashboard network"][1]
    assert "https://apcoder.company.local:8501" in checks["dashboard network"][1]
    assert checks["server HTTPS certificate"][0] == "PASS"
    assert checks["server HTTPS private key"] == ("FAIL", f"{key}: not found")
    assert checks["server names"] == ("PASS", "apcoder.company.local")
    assert checks["server service"][0] == "SKIP"
    service.write_config(data, allowed_hosts=[])
    key.write_text("key")

    def schtasks(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="Status: Ready\nRun As User: APCoderService\n")

    monkeypatch.setattr(service, "run_command", schtasks)
    checks = {area: (status, detail) for area, status, detail in service.doctor_checks(data, env={}, windows=True)}
    assert checks["server names"][0] == "WARN" and "--allowed-hosts" in checks["server names"][1]
    assert checks["server HTTPS private key"][0] == "PASS"
    assert checks["server service"] == ("PASS", 'task "AP Coder": Ready, runs as APCoderService')

    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))  # the doctor itself, as IT runs it on the server's console
    found = doctor.run_checks(web_settings(), lambda: (_ for _ in ()).throw(FileNotFoundError("no")), llm_checks=False)
    network = [c for c in found if c.area == "dashboard network"]
    assert len(network) == 1 and "this computer only" not in network[0].detail
    assert any(c.area == "server names" for c in found)


def test_the_doctor_on_a_laptop_is_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path))
    monkeypatch.delenv(offline.SERVER_ENV, raising=False)
    found = doctor.run_checks(web_settings(), lambda: (_ for _ in ()).throw(FileNotFoundError("no")), llm_checks=False)
    assert [c.detail for c in found if c.area == "dashboard network"][0].startswith("this computer only")
    assert not any(c.area.startswith("server") for c in found)


# --- JD Edwards on the server (B-1121) --------------------------------------------------------------------------------


def test_jd_edwards_is_shown_at_first_on_the_server(store, monkeypatch):
    """B-1121: on the server, the JD Edwards pilot, the JD Edwards settings were hidden until Settings → About →
    Show JD Edwards settings; they are shown at first there (and can be hidden), and on a laptop as before."""
    assert core.jde_shown(store) is False
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    assert core.jde_shown(store) is True
    store.set_setting(core.SHOW_JDE, "0")  # hidden on Settings → About
    assert core.jde_shown(store) is False
    guide = (ROOT / "docs" / "SERVER.md").read_text(encoding="utf-8")
    assert "shown from the start" in guide


def test_a_clerk_is_told_to_ask_an_admin_to_undo_a_batch(store):
    """B-1104: the batch just made said "undo the batch below" to a Clerk, who may not (an Admin's)."""
    invoice_id = add_clean(store)
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Cleo Clerk")
    clerk = client_as(store, "clerk")
    answer = clerk.post("/exports/create", data={"ids": [str(invoice_id)], "fmt": "csv"}, headers=PAGE)
    assert answer.status_code == 200 and answer.json().get("ok", True), answer.text
    page = clerk.get(answer.json()["redirect"]).text
    assert "ask an Admin to undo the batch below" in page and "/undo" not in page
    admin = client_as(store, "admin").get(answer.json()["redirect"]).text
    assert "fails, undo the batch below" in admin
