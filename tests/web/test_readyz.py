"""``GET /readyz`` (D-187): for IT's monitor or a load balancer, 200 when ready and 503 when not, with what was
checked; on the server only to its console and the monitors server.json names, on a laptop as /healthz is. And what
an Admin sees on the dashboard: the certificate near its end (the banner), the second backup folder on the
database's disk (Settings → Data & backups)."""

from __future__ import annotations

import base64
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from ap_coder.core import paths
from ap_coder.ops import health, service
from ap_coder.web import roles
from ap_coder.web.app import create_app
from tests.webclient import client_for, enrol, sign_in, web_settings

GOOD = "correct horse battery"
TOKEN = "monitor-" + "0123456789abcdef" * 2


def _pem_ending(when: dt.datetime) -> bytes:
    """A certificate's PEM, its DER as far as its validity (all /readyz and the banner read)."""

    def der(tag, *parts):
        body = b"".join(parts)
        return bytes([tag, len(body)]) + body

    validity = der(0x30, der(0x17, b"260101000000Z"), der(0x17, when.strftime("%y%m%d%H%M%SZ").encode()))
    tbs = der(0x30, der(0xA0, der(0x02, b"\x02")), der(0x02, b"\x01"), der(0x30), der(0x30), validity, der(0x30))
    body = base64.encodebytes(der(0x30, tbs, der(0x30), der(0x03, b"\x00"))).decode()
    return f"-----BEGIN CERTIFICATE-----\n{body}-----END CERTIFICATE-----\n".encode()


def _server(store, **kwargs):
    return create_app(web_settings(), store, start_reader=False, server=True, **kwargs)


def _client(app, address):
    return TestClient(app, base_url="https://localhost", client=(address, 50000), follow_redirects=False)


def test_readyz_needs_no_sign_in_and_checks_who_asks_itself():
    assert roles.RULES[("GET", "/readyz")] == roles.OPEN


def test_on_a_laptop_this_computer_is_told_everything_and_a_colleague_only_whether_it_is_ready(store):
    found = client_for(store).get("/readyz")
    assert found.status_code == 200 and found.headers["cache-control"] == "no-store"
    body = found.json()
    assert body["ready"] is True and body["app"] == "ap-coder" and body["server"] is False
    assert {"data_disk", "backup", "imports", "page_reader", "model_servers"} <= set(body)
    shared = create_app(web_settings(), store, start_reader=False, address="0.0.0.0")
    colleague = TestClient(shared, base_url="http://localhost", client=("192.168.1.30", 50000))
    assert colleague.get("/readyz").json() == {"ready": True, "app": "ap-coder"}  # (no name asked first, B-979)
    assert colleague.get("/healthz").json() == {"ok": True, "app": "ap-coder"}


def test_on_the_server_the_console_and_the_monitors_it_names_are_answered(store):
    app = _server(store)
    assert _client(app, "127.0.0.1").get("/readyz").json()["server"] is True
    refused = _client(app, "10.0.0.5").get("/readyz")
    assert refused.status_code == 403 and "monitor_token" in refused.json()["error"]
    assert _client(app, "10.0.0.5").get("/readyz", headers={health.TOKEN_HEADER: TOKEN}).status_code == 403
    paths.private_dir().mkdir(parents=True, exist_ok=True)
    service.write_config(paths.private_dir(), monitor_token=TOKEN, monitor_addresses=["10.0.7.0/24"])
    answer = _client(app, "10.0.0.5").get("/readyz", headers={health.TOKEN_HEADER: TOKEN})
    assert answer.status_code == 200 and answer.json()["server"] is True
    assert _client(app, "10.0.0.5").get("/readyz", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert _client(app, "10.0.0.5").get("/readyz", headers={health.TOKEN_HEADER: "x" + TOKEN}).status_code == 403
    assert _client(app, "10.0.7.20").get("/readyz").status_code == 200  # a load balancer that sends no header
    app.state.proxy = "127.0.0.1"  # behind IIS on the server: a loopback address is everyone
    assert _client(app, "127.0.0.1").get("/readyz").status_code == 403
    assert TOKEN not in answer.text


def test_the_server_answers_503_until_its_background_and_capture_processes_are_alive(store, monkeypatch):
    monkeypatch.setenv("AP_CAPTURE_PROCESSES", "1")
    console = _client(_server(store, processes=3), "127.0.0.1")
    answer = console.get("/readyz")
    assert answer.status_code == 503 and answer.json()["ready"] is False
    assert {p["check"] for p in answer.json()["problems"] if p["status"] == "fail"} == {"background", "capture"}
    store.beat(health.HEARTBEAT)
    store.take_lease("capture:1", "1", 30)
    console = _client(_server(store, processes=3), "127.0.0.1")  # (a new app: the last answer is kept 5 s)
    answer = console.get("/readyz")
    assert answer.status_code == 200 and answer.json()["ready"] is True, answer.json()["problems"]


def test_the_answer_is_kept_a_few_seconds_however_many_monitors_ask(store, monkeypatch):
    made = []
    real = health.readiness
    monkeypatch.setattr(health, "readiness", lambda *a, **k: made.append(1) or real(*a, **k))
    client = client_for(store)
    for _ in range(5):
        assert client.get("/readyz").status_code == 200
    assert len(made) == 1


def test_nothing_secret_or_named_is_in_the_answer(store, ground_truth, tmp_path):
    store.add_invoice(tmp_path / "Northwind invoice.pdf", ground_truth, {})
    store.add_user("jsmith", GOOD, role="admin", display_name="Jane Smith")
    store.set_setting("backup_copy_dir", str(tmp_path))
    text = _client(_server(store), "127.0.0.1").get("/readyz").text
    for named in ("jsmith", "Jane", "Northwind", ground_truth["vendor_name"], str(tmp_path), str(store.path)):
        assert named not in text, named


def _admin_and_clerk(store, app):
    clients = {}
    for role in ("admin", "clerk"):
        uid = store.add_user(f"the.{role}", GOOD, role=role)
        with store._conn() as conn:
            conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
        enrol(store, uid)
        clients[role] = _client(app, "10.0.0.5")
        assert sign_in(clients[role], f"the.{role}", GOOD).status_code in (200, 303)
    return clients


def test_an_admin_is_told_on_every_page_when_the_certificate_nears_its_end(store, tmp_path):
    app = _server(store)
    cert = tmp_path / "cert.pem"
    cert.write_bytes(_pem_ending(dt.datetime.now(dt.UTC) + dt.timedelta(days=9)))
    app.state.certfile = str(cert)
    clients = _admin_and_clerk(store, app)
    page = clients["admin"].get("/review", headers={"Accept": "text/html"}).text
    assert "HTTPS certificate expires on" in page and 'class="banner warn" role="alert"' in page
    assert "HTTPS certificate" not in clients["clerk"].get("/review", headers={"Accept": "text/html"}).text
    cert.write_bytes(_pem_ending(dt.datetime(2026, 1, 2, tzinfo=dt.UTC)))
    page = clients["admin"].get("/review", headers={"Accept": "text/html"}).text
    assert "HTTPS certificate expired on 2026-01-02" in page and 'class="banner err"' in page


@pytest.mark.parametrize("server", [True, False])
def test_settings_says_a_second_backup_folder_on_the_databases_disk_on_the_server_only(store, tmp_path, server):
    share = tmp_path / "share"
    share.mkdir()
    store.set_setting("backup_copy_dir", str(share))
    if server:
        page = _admin_and_clerk(store, _server(store))["admin"].get("/settings/data").text
    else:
        page = client_for(store).get("/settings/data").text
    assert ("on the same disk as the database" in page) is server
    assert "the newest 14" in page and ("one a week for 8 weeks and one a month for a year" in page) is server


def test_the_answer_says_each_check_as_documented(store):
    """The shape docs/SERVER.md (section 12) gives IT."""
    body = _client(_server(store), "127.0.0.1").get("/readyz").json()
    for key in ("ready", "status", "app", "version", "server", "checked_at", "problems", "database", "data_disk",
                "backups_disk", "backup", "second_backup_folder", "time_zone", "background", "capture", "imports",
                "page_reader", "model_servers"):  # fmt: skip
        assert key in body, key
    assert set(body["data_disk"]) == {"free_bytes", "total_bytes", "reserve_bytes", "status"}
    assert set(body["backup"]) == {"newest", "age_hours", "last_failed", "being_made", "status"}
    assert set(body["imports"]) == {"waiting", "reading", "oldest_wait_seconds"}
    assert json.loads(json.dumps(body)) == body
