"""Sweep 4, platform and security (B-970 to B-989): the second-approval controls, Settings from this computer only on
the office network, the offline guarantee through redirects, image and .msg bombs, the Azure host, local paths on
error pages, Windows reserved file names, upload sizes and the .xlsx export's formula-like text."""

from __future__ import annotations

import shutil
import time

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import APPROVED, PENDING, Store
from ap_coder.ops.demo import load_demo
from ap_coder.web.app import create_app

from .webclient import PAGE, add_clean, web_settings


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "private" / "ap_coder.db"
    load_demo(Store(path), Settings())
    return path


@pytest.fixture
def store(demo_db, tmp_path):
    target = tmp_path / "private" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    return Store(target)


def local(app) -> TestClient:
    return TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000), follow_redirects=False)


def colleague(app, address: str = "10.0.0.5", name: str = "Ana") -> TestClient:
    client = TestClient(app, base_url="http://localhost", client=(address, 50000), follow_redirects=False)
    assert client.post("/who", data={"name": name}, headers=PAGE).json()["ok"]
    return client


def _usd(store: Store, total: float = 7570.0) -> int:
    return add_clean(store, currency="USD", grand_total=total)


# --- 1. The second approval can't be skipped by changing the limit or the rates -----------------------------------


def test_lowering_the_limit_just_before_approving_does_not_skip_a_second_approval_that_was_due(store):
    store.set_setting("approval_limit", "1.00", actor="Pilot")
    invoice_id = add_clean(store)
    store.set_setting("approval_limit", "0.00", actor="Clerk")  # the clerk turns the limit off, approves alone
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Clerk", login="clerk")
    assert store.get_invoice(invoice_id)["status"] == PENDING
    approved = next(e for e in store.events(invoice_id) if e["action"] == "approved")
    assert approved["detail"]["needs_second"] and approved["detail"]["limit"] == 1.0  # the limit in force, kept


def test_a_lower_exchange_rate_set_after_the_invoice_came_in_does_not_skip_its_second_approval(store):
    store.set_setting("approval_limit", "1000.00")
    store.set_setting("fx_rates", "USD=1.37")
    time.sleep(1.1)  # the events' times are to the second: a change in the invoice's own second counts
    invoice_id = _usd(store)
    store.set_setting("fx_rates", "USD=0.05")
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Clerk", login="clerk")
    assert store.get_invoice(invoice_id)["status"] == PENDING
    approved = next(e for e in store.events(invoice_id) if e["action"] == "approved")
    assert approved["detail"]["rate"] == 1.37


def test_an_invoice_that_came_in_after_the_change_follows_the_new_limit(store):
    store.set_setting("approval_limit", "1.00")
    store.set_setting("approval_limit", "0.00")
    time.sleep(1.1)  # the events' times are to the second
    invoice_id = add_clean(store)
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Clerk", login="clerk")
    assert store.get_invoice(invoice_id)["status"] == APPROVED


def test_a_change_of_the_limit_or_the_rates_is_in_the_audit_trail_with_its_old_and_new_values(store):
    app = create_app(web_settings(), store, start_reader=False)
    client = local(app)
    assert client.post("/settings/review", data={"approval_limit": "500"}, headers=PAGE).json()["ok"]
    assert client.post("/settings/review", data={"fx_rates": "USD=1.37"}, headers=PAGE).json()["ok"]
    changes = [e["detail"] for e in store.events(actions=["settings_changed"]) if e["detail"].get("changes")]
    found = {k: v for d in changes for k, v in d["changes"].items()}
    assert found["approval_limit"] == {"from": "", "to": "500.00"}
    assert found["fx_rates"] == {"from": "", "to": "USD=1.37"}
    from ap_coder.core import audit

    event = next(e for e in store.events(actions=["settings_changed"]) if "approval_limit" in e["detail"]["keys"])
    assert "500.00" in audit.describe(event)


@pytest.mark.parametrize("rates", ["USD=0", "USD=0.0001", "EUR 0.004"])
def test_an_exchange_rate_of_zero_or_absurdly_small_is_refused(store, rates):
    app = create_app(web_settings(), store, start_reader=False)
    answer = local(app).post("/settings/review", data={"fx_rates": rates}, headers=PAGE).json()
    assert not answer["ok"] and "rate" in answer["error"]
    assert store.get_setting("fx_rates") == ""


def test_a_small_rate_is_kept_for_a_currency_whose_unit_is_worth_less_than_a_cent(store):
    app = create_app(web_settings(), store, start_reader=False)
    assert local(app).post("/settings/review", data={"fx_rates": "JPY=0.0093"}, headers=PAGE).json()["ok"]
    assert store.fx_rates()["JPY"] == 0.0093


def test_a_tiny_rate_already_saved_is_no_rate(store):
    store.set_setting("fx_rates", "USD=0.0001")
    assert "USD" not in store.fx_rates()


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/settings"),
        ("get", "/settings/review"),
        ("get", "/settings/data"),
        ("post", "/settings/review"),
        ("post", "/settings/data/backup"),
        ("post", "/settings/data/backup-copy"),
        ("post", "/settings/data/restore"),
        ("get", "/settings/data/backups/x.db"),
        ("get", "/settings/azure-check"),
        ("post", "/settings/azure-check"),
    ],
)
def test_on_the_office_network_settings_data_and_azure_are_only_from_this_computer(store, method, path):
    app = create_app(web_settings(), store, address="0.0.0.0", start_reader=False)
    answer = getattr(colleague(app), method)(path, headers=PAGE)
    assert answer.status_code == 403
    assert "computer running AP Coder" in answer.text
    if method == "get":
        assert local(app).get(path).status_code in (200, 303, 307, 404)
