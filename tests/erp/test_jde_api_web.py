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


def test_a_batch_downloaded_as_a_zip_is_posted_through_the_api_only_on_a_persons_word(tmp_path, ais, monkeypatch):
    """D-134: the batch's JD Edwards ZIP downloaded (who and when on the batch and in the audit trail), posting it
    through the API asks for a tick that the file was not loaded and what was checked; once posted, the ZIP's page
    says its vouchers are in JD Edwards already."""
    store, batch = batch_of(tmp_path, ais)
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)
    client = client_for(store)
    post(client, f"/exports/batches/{batch}/jde-api/dry-run")
    _wait(client, "jde-api")
    page = client.get(f"/exports?batch={batch}").text
    assert "was downloaded" not in page and 'name="not_loaded"' not in page  # sent one way only: as before
    assert client.get(f"/exports/batches/{batch}/download").status_code == 200
    (event,) = store.events(actions=["jde_file_downloaded"])
    assert event["detail"]["batch"] == batch
    page = client.get(f"/exports?batch={batch}").text
    assert "The JD Edwards file of this batch was downloaded" in page
    assert event["created_at"][:16].replace("T", " ") in page
    assert 'name="not_loaded"' in page and 'name="why"' in page
    answer = post(client, f"/exports/batches/{batch}/jde-api/post").json()
    assert not answer["ok"] and "was downloaded" in answer["error"]
    answer = post(client, f"/exports/batches/{batch}/jde-api/post", {"not_loaded": "1"}).json()
    assert not answer["ok"] and "what you checked" in answer["error"]
    why = "Checked in JD Edwards: no voucher of batch APC1 loaded"
    post(client, f"/exports/batches/{batch}/jde-api/post", {"not_loaded": "1", "why": why})
    assert _wait(client, "jde-api")["messages"][0]["text"] == "JD Edwards: 1 posted."
    assert store.events(actions=["jde_api_approved"])[0]["detail"]["not_loaded"] == why
    activity = client.get("/activity").text
    assert "JD Edwards file downloaded" in activity and why in activity
    page = client.get(f"/exports?batch={batch}").text
    assert "already in JD Edwards" in page  # the ZIP's page: a copy downloaded before holds them


def test_in_a_browser_posting_a_downloaded_batch_needs_the_tick_and_what_was_checked(chromium, tmp_path, ais,
                                                                                    monkeypatch):  # fmt: skip
    """D-134 on the page: the Post button of a batch whose ZIP was downloaded sends nothing until the box is ticked
    and what was checked is said; then the vouchers post, and the reason is kept with the go-ahead."""
    from ap_coder.web.app import create_app
    from tests.browser import browser_on, wait_until
    from tests.webclient import web_settings

    store, batch = batch_of(tmp_path, ais)
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)
    jde_post.dry_run(store, batch)
    jde_post.file_downloaded(store, batch, "Sam")
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())  # "Post 1 voucher of batch 1 to JD Edwards?"
        page.goto(f"{base}/exports?batch={batch}")
        button = page.get_by_role("button", name="Post 1 voucher to JD Edwards")
        button.click()
        page.wait_for_timeout(300)
        assert store.events(actions=["jde_api_approved"]) == []  # the box not ticked: nothing sent
        page.check("input[name=not_loaded]")
        page.fill("textarea[name=why]", "No batch of these vouchers in JD Edwards")
        button.click()
        wait_until(page, lambda: jde_post.postings(store, batch)[0]["status"] == jde_post.POSTED, 15)
        assert jde_post.postings(store, batch)[0]["status"] == jde_post.POSTED
        (approved,) = store.events(actions=["jde_api_approved"])
        assert approved["detail"]["not_loaded"] == "No batch of these vouchers in JD Edwards" and errors == []
