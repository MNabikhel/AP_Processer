"""The AIS client's network and secrets rules (ap_coder/erp/jde_ais.py, D-104 / D-105): the address checked is the one
connected to, the ranges that are not the company's network, the password never sent to a new host, and no secret,
however short, in a message."""

from __future__ import annotations

import socket

import pytest

from ap_coder.core.store import Store
from ap_coder.erp import jde_ais

from .fake_ais import PASSWORD, USER, FakeAis


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(jde_ais, "RETRY_PAUSE", 0)


@pytest.fixture
def ais():
    with FakeAis() as fake:
        yield fake


def test_the_name_is_resolved_once_and_that_address_is_connected_to(ais, monkeypatch):
    real = socket.getaddrinfo
    asked = []

    def rebinding(host, *args, **kwargs):
        if host == "localhost":  # first a private address, then one on the internet (DNS rebinding)
            asked.append(host)
            if len(asked) > 1:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
            return real("127.0.0.1", *args, **kwargs)
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", rebinding)
    port = ais.url.rsplit(":", 1)[1]
    with jde_ais.AisClient(ais.settings(base_url=f"http://localhost:{port}")) as c:
        assert c.address == "127.0.0.1"
        assert {r["an8"] for r in c.rows("suppliers")} == {"4001", "4002"}
    assert asked == ["localhost"]  # resolved once: every request went to the address checked


def test_6to4_and_documentation_addresses_are_not_internal():
    for address in ("2002:c0a8:101::1", "2002::1", "2001:db8::5", "::ffff:8.8.8.8"):
        assert "internet" in jde_ais.internal_problem(address), address
    assert "internet" in jde_ais.internal_problem("ais.corp.example.com", resolve=lambda h: ["10.0.0.5", "2001:db8::1"])
    assert jde_ais.internal_problem("fd00::5") == "" and jde_ais.internal_problem("::ffff:10.0.0.1") == ""


def test_a_shared_address_needs_the_admins_tick():
    problem = jde_ais.internal_problem("100.64.12.3")
    assert "internet" in problem and "on our network" in problem
    assert jde_ais.internal_problem("100.64.12.3", shared=True) == ""
    assert jde_ais.internal_problem("ais.vpn.example.com", resolve=lambda h: ["100.100.1.1"], shared=True) == ""
    assert "internet" in jde_ais.internal_problem("8.8.8.8", shared=True)
    cfg = jde_ais.AisSettings(base_url="https://100.64.12.3:9300", environment="E", username="U", step1=True)
    cfg.password = "x"
    with pytest.raises(jde_ais.AisError, match="internet"):
        jde_ais.AisClient(cfg)
    cfg.shared_network = True
    assert jde_ais.AisClient(cfg).address == "100.64.12.3"
    assert jde_ais.AisSettings.from_json(cfg.to_json()).shared_network


def test_a_new_ais_host_forgets_the_saved_password(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(jde_ais, "env_path", lambda: env)
    monkeypatch.delenv(jde_ais.PASSWORD_ENV, raising=False)
    store = Store(tmp_path / "ap_coder.db")
    jde_ais.save(store, base_url="ais.company.local:9300", environment="JPD920", role="", username=USER,
                 password=PASSWORD, step1=True)  # fmt: skip
    with pytest.raises(ValueError, match="type it again"):
        jde_ais.save(store, base_url="ais.other.local:9300", environment="JPD920", role="", username=USER, step1=True)
    assert jde_ais.load(store).password == PASSWORD  # refused: nothing changed
    changed = jde_ais.save(store, base_url="ais.other.local:9300", environment="JPD920", role="", username=USER)
    assert "password" in changed and jde_ais.load(store).password == "" and PASSWORD not in env.read_text()
    jde_ais.save(store, base_url="ais.other.local:9300", environment="JPD920", role="", username=USER,
                 password="New-Pass-1", step1=True)  # fmt: skip
    assert jde_ais.save(store, base_url="https://ais.other.local:9300", environment="JPD920", role="",
                        username=USER, step1=True) == []  # the same host: kept  # fmt: skip
    assert jde_ais.load(store).password == "New-Pass-1"


def test_a_secret_of_any_length_is_scrubbed():
    assert jde_ais.scrub("password abc refused", "abc") == "password •••• refused"
    assert jde_ais.scrub("x", "") == "x"


def test_the_sign_in_refusal_never_shows_the_password(ais, monkeypatch):
    cfg = ais.settings(password="pw1")
    client = jde_ais.AisClient(cfg)
    monkeypatch.setattr(client, "_request", lambda *a, **k: (403, {"message": "Authorization Failure for pw1"}))
    with pytest.raises(jde_ais.AisError) as caught:
        client.login()
    assert caught.value.kind == jde_ais.AUTH and "pw1" not in str(caught.value) and "••••" in str(caught.value)
