"""The JD Edwards API on the dashboard (D-104): Settings → JD Edwards E1 → API connection (save, the password never
shown or stored in the database, Test connection, Sync now), and Exports (the dry run, the go-ahead, the voucher number
on the batch, the queue, no undo once posted, the ZIP without the posted vouchers), against the stand-in AIS server."""

from __future__ import annotations

import io
import zipfile

import pytest

from ap_coder.erp import jde_ais, jde_post, jde_sync
from tests.erp.test_jde_post import batch_of
from tests.fake_ais import ENVIRONMENT, PASSWORD, USER, FakeAis
from tests.webclient import client_for, follow, post


@pytest.fixture(autouse=True)
def quick(monkeypatch, tmp_path):
    monkeypatch.setattr(jde_ais, "RETRY_PAUSE", 0)
    monkeypatch.setattr(jde_ais, "env_path", lambda: tmp_path / ".env")
    monkeypatch.delenv(jde_ais.PASSWORD_ENV, raising=False)
    jde_sync.forget_cache()


@pytest.fixture
def ais():
    with FakeAis() as fake:
        yield fake


def _wait(client, name):
    assert client.app.state.jobs.get(name).wait(30)
    return client.app.state.jobs.get(name).result


def test_settings_save_test_and_sync(tmp_path, ais, monkeypatch):
    from ap_coder.core.store import Store

    store = Store(tmp_path / "ap_coder.db")
    client = client_for(store)
    page = client.get("/settings/jde").text
    assert 'id="api"' in page and "Off: AP Coder connects to nothing" in page
    form = {"base_url": ais.url, "environment": ENVIRONMENT, "role": "*ALL", "username": USER, "step1": "1",
            "step2": "1", "auth_mode": "token", "timeout": "5"}  # fmt: skip
    answer = post(client, "/settings/jde/api", form).json()
    assert not answer["ok"] and "the password" in answer["error"]
    page = follow(client, post(client, "/settings/jde/api", {**form, "password": PASSWORD}))
    assert "API connection saved" in page and PASSWORD not in page and "(saved)" in page
    assert PASSWORD not in store.get_setting(jde_ais.SETTING_KEY)
    page = follow(client, post(client, "/settings/jde/api/test"))
    assert "Connected to 127.0.0.1 (AIS 9.2.8.3)" in page
    answer = post(client, "/settings/jde/api/sync").json()
    assert answer["job"] == "jde-sync"
    result = _wait(client, "jde-sync")
    assert result["messages"][0]["text"] == "Synced from JD Edwards."
    assert any(m["text"].startswith("Suppliers: 2 added") for m in result["messages"])
    assert store.get_vendor("acme office supplies")["erp_id"] == "4001"
    assert "Suppliers: 2 added" in client.get("/settings/jde").text


def test_test_and_sync_refuse_while_off(tmp_path, ais):
    from ap_coder.core.store import Store

    client = client_for(Store(tmp_path / "ap_coder.db"))
    assert "Switch step 1 or step 2 on" in post(client, "/settings/jde/api/test").json()["error"]
    assert "Step 1" in post(client, "/settings/jde/api/sync").json()["error"]
    assert ais.calls == []


def test_exports_dry_run_post_and_the_voucher_number(tmp_path, ais, monkeypatch):
    store, batch = batch_of(tmp_path, ais)
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)
    client = client_for(store)
    page = client.get(f"/exports?batch={batch}").text
    assert 'id="jde-api"' in page and "Dry run through the API" in page and "to JD Edwards</button>" not in page
    post(client, f"/exports/batches/{batch}/jde-api/dry-run")
    assert "1 of 1 voucher ready to post" in _wait(client, "jde-api")["messages"][0]["text"]
    page = client.get(f"/exports?batch={batch}").text
    assert "Dry run passed" in page and "Post 1 voucher to JD Edwards" in page
    post(client, f"/exports/batches/{batch}/jde-api/post")
    assert _wait(client, "jde-api")["messages"][0]["text"] == "JD Edwards: 1 posted."
    page = client.get(f"/exports?batch={batch}").text
    assert "10001 PV 00001" in page and "Posted" in page and "Dry run through the API" not in page
    answer = post(client, f"/exports/batches/{batch}/undo").json()
    assert not answer["ok"] and "can't be undone" in answer["error"]
    data = client.get(f"/exports/batches/{batch}/download").content
    readme = zipfile.ZipFile(io.BytesIO(data)).read("README.txt").decode()
    assert "already posted to JD Edwards through the API" in readme
    header = zipfile.ZipFile(io.BytesIO(data)).read("F0411Z1.csv").decode().splitlines()
    assert len(header) == 1  # only the column names: the voucher is in JDE already


def test_exports_the_queue_and_jde_down(tmp_path, ais, monkeypatch):
    store, batch = batch_of(tmp_path, ais)
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)
    client = client_for(store)
    post(client, f"/exports/batches/{batch}/jde-api/dry-run")
    _wait(client, "jde-api")
    ais.script("voucher_create", 503, 503, 503)
    post(client, f"/exports/batches/{batch}/jde-api/post")
    messages = _wait(client, "jde-api")["messages"]
    assert messages[0]["text"] == "JD Edwards: 1 waiting in the queue."
    page = client.get(f"/exports?batch={batch}").text
    assert "1 voucher waiting for JD Edwards" in page and "Waiting: JD Edwards not reached" in page
    post(client, "/exports/jde-api/retry")
    assert _wait(client, "jde-api")["messages"][0]["text"] == "JD Edwards: 1 posted."
    assert jde_post.postings(store, batch)[0]["document"] == "10001"
    assert post(client, "/exports/jde-api/retry").json()["toast"]["text"] == "Nothing is waiting for JD Edwards."


def test_the_go_ahead_needs_a_dry_run_and_step_2(tmp_path, ais, monkeypatch):
    store, batch = batch_of(tmp_path, ais)
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)
    client = client_for(store)
    assert "Run the dry run first" in post(client, f"/exports/batches/{batch}/jde-api/post").json()["error"]
    store.set_setting(jde_ais.SETTING_KEY, ais.settings(step2=False).to_json())
    assert "Step 2" in post(client, f"/exports/batches/{batch}/jde-api/post").json()["error"]
    assert 'id="jde-api"' not in client.get(f"/exports?batch={batch}").text  # off and nothing sent: not shown


def test_a_dry_run_without_jde_s_proof_says_so_and_never_ready_in_green(tmp_path, ais, monkeypatch):
    """B-1209: a dry run whose JD Edwards proof call timed out said "1 of 1 voucher ready to post" in green (and the
    reason in a second line): it says it was checked here only, JD Edwards' proof not received, as a warning first;
    posting stays allowed."""
    from tests.erp.test_jde_post import connect

    store, batch = batch_of(tmp_path, ais)
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)
    connect(store, ais, timeout=0.15)
    ais.script("voucher_create", "slow")
    client = client_for(store)
    post(client, f"/exports/batches/{batch}/jde-api/dry-run")
    first = _wait(client, "jde-api")["messages"][0]
    assert first["tone"] == "warn" and "ready to post" not in first["text"], first
    assert "checked here only" in first["text"] and "proof wasn't received" in first["text"], first
    assert "Post 1 voucher to JD Edwards" in client.get(f"/exports?batch={batch}").text  # posting stays allowed
