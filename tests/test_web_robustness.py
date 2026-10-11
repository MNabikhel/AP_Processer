"""The web dashboard used as a clerk would: stale pages, double actions, odd input, and the guards in front of it."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.config import Settings
from ap_coder.store import Store
from ap_coder.web.app import create_app

PAGE = {"X-AP-Coder": "1"}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture
def client(store):
    s = Settings()
    return TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), store), base_url="http://localhost")


def test_the_welcome_pages_sample_accounts_never_go_into_an_imported_chart(client, store):
    """A welcome page left open while the clerk imported their own chart of accounts: its "Use the sample GL
    accounts" button must not add the sample codes to it, nor replace their coding policy and tax setup."""
    store.import_accounts("gl_accounts", [{"code": "1000", "d": "Our cash"}], "code", "d")
    store.set_setting("policy_notes", "Our own policy.")
    response = client.post("/review/sample-gl", headers=PAGE)
    assert response.status_code == 400 and "already have GL accounts" in response.json()["error"]
    assert [a["code"] for a in store.list_accounts("gl_accounts")] == ["1000"]
    assert store.get_setting("policy_notes") == "Our own policy."
