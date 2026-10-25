"""The AIS client (ap_coder/erp/jde_ais.py, D-104) against the stand-in AIS server (tests/fake_ais.py): the token flow,
expiry, sign-in failures, timeouts, retries, JDE's error shape, HTTPS with a company CA, the address rules and the
settings, with the password never in the database or a message."""

from __future__ import annotations

import json
import logging

import pytest

from ap_coder.core.store import Store
from ap_coder.erp import jde_ais

from .fake_ais import CA_FILE, PASSWORD, USER, FakeAis


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(jde_ais, "RETRY_PAUSE", 0)


@pytest.fixture
def ais():
    with FakeAis() as fake:
        yield fake


def test_token_flow_orchestration_and_logout(ais):
    with jde_ais.AisClient(ais.settings()) as c:
        rows = c.rows("suppliers")
        assert {r["an8"] for r in rows} == {"4001", "4002"}
        assert rows[0]["name"] == "Acme Office Supplies Ltd."
        assert c.token
    paths = [p for p, _ in ais.calls]
    assert paths[0] == "/jderest/v2/tokenrequest" and paths[-1] == "/jderest/v2/tokenrequest/logout"
    assert ais.tokens == {}  # logged out
    token_request = ais.calls[0][1]
    assert token_request["environment"] == "JPD920" and token_request["deviceName"] == "APCoder"
    assert "password" not in token_request  # (the stand-in never keeps it)


def test_rows_are_read_page_by_page(ais):
    ais.mapping["ais"]["page_size"] = 1
    c = jde_ais.AisClient(ais.settings(), ais.mapping)
    assert [r["business_unit"] for r in c.rows("business_units")] == ["CC100", "CC200"]
    pages = ais.orchestration_calls("business_units")
    assert [p["AfterBusinessUnit"] for p in pages] == ["", "CC100", "CC200"] and pages[0]["MaxRows"] == 1


def test_an_expired_token_is_asked_again_once(ais):
    c = jde_ais.AisClient(ais.settings())
    c.call("suppliers", {})
    first = c.token
    ais.expire_tokens()
    c.call("suppliers", {})
    assert c.token != first
    assert sum(p == "/jderest/v2/tokenrequest" for p, _ in ais.calls) == 2


def test_a_wrong_password_is_said_plainly_and_never_repeated(ais):
    c = jde_ais.AisClient(ais.settings(password="Wrong-Password-1"))
    with pytest.raises(jde_ais.AisError) as err:
        c.call("suppliers", {})
    assert err.value.kind == jde_ais.AUTH
    assert "refused the JDE user APCODER" in str(err.value) and "Wrong-Password-1" not in str(err.value)


def test_basic_authentication(ais):
    c = jde_ais.AisClient(ais.settings(auth_mode=jde_ais.BASIC))
    assert c.call("suppliers", {})["Suppliers"]
    assert not any(p == "/jderest/v2/tokenrequest" for p, _ in ais.calls)


def test_no_permission_and_no_such_orchestration():
    with FakeAis(denied={"vouchers"}) as ais:
        c = jde_ais.AisClient(ais.settings())
        with pytest.raises(jde_ais.AisError) as err:
            c.call("vouchers", {})
        assert err.value.kind == jde_ais.DENIED and "not authorized to run APC_Vouchers_Read" in str(err.value)
        mapping = jde_ais.load_mapping()
        mapping["orchestrations"]["vouchers"]["name"] = "APC_Not_Built"
        with pytest.raises(jde_ais.AisError) as err:
            jde_ais.AisClient(ais.settings(), mapping).call("vouchers", {})
        assert err.value.kind == jde_ais.NOT_FOUND and "APC_Not_Built" in str(err.value)


def test_jde_errors_come_back_in_its_own_words(ais):
    c = jde_ais.AisClient(ais.settings())
    with pytest.raises(jde_ais.AisError) as err:
        c.call("voucher_create", {"key": "APC-1-1", "mode": "FINAL", "pay_items": [{"VLAN8": "9999"}]})
    assert err.value.kind == jde_ais.JDE and err.value.status == 500
    assert "Address Number 9999 Invalid (0002)" in str(err.value)


def test_jde_messages_reads_the_shapes_ais_answers_with():
    assert jde_ais.jde_messages({"userDefinedErrorText": "Supplier on hold", "message": "x"}) == ["Supplier on hold",
                                                                                                    "x"]  # fmt: skip
    nested = {"message": {"Form": {"errors": [{"CODE": "4363", "TITLE": "Duplicate Invoice Number"}]}}}
    assert jde_ais.jde_messages(nested) == ["Duplicate Invoice Number (4363)"]
    assert jde_ais.jde_messages({"exception": "java.lang.NullPointerException"}) == ["java.lang.NullPointerException"]


def test_busy_is_retried_a_limited_number_of_times(ais):
    c = jde_ais.AisClient(ais.settings())
    ais.script("suppliers", 503, 503)
    assert c.call("suppliers", {})["Suppliers"]
    ais.script("suppliers", 503, 503, 503)
    with pytest.raises(jde_ais.AisError) as err:
        c.call("suppliers", {})
    assert err.value.kind == jde_ais.UNREACHABLE and "busy" in str(err.value)


def test_a_read_that_times_out_is_retried_a_voucher_never():
    with FakeAis(slow_seconds=0.3) as ais:
        c = jde_ais.AisClient(ais.settings(timeout=0.15))
        ais.script("suppliers", "slow")
        assert c.call("suppliers", {})["Suppliers"]  # the second try answered
        ais.script("voucher_create", "slow")
        with pytest.raises(jde_ais.AisError) as err:
            c.call("voucher_create", {"key": "k"}, idempotent=False)
        assert err.value.kind == jde_ais.TIMEOUT and err.value.sent
        assert len(ais.orchestration_calls("voucher_create")) == 1


def test_nobody_listening_is_unreachable_and_not_sent():
    with FakeAis() as ais:
        cfg = ais.settings()
    with pytest.raises(jde_ais.AisError) as err:
        jde_ais.AisClient(cfg).call("voucher_create", {}, idempotent=False)
    assert err.value.kind == jde_ais.UNREACHABLE and not err.value.sent
    assert "can't be reached" in str(err.value)


def test_no_answer_while_connecting_is_unreachable_and_not_sent(monkeypatch):
    """A connection that never opens (Windows waits about 2 seconds on a closed port before refusing it) sent
    nothing: it is "can't be reached", not a timeout of a request JD Edwards may have taken (B-1179)."""
    with FakeAis() as ais:
        cfg = ais.settings()

    def no_answer(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(jde_ais.socket, "create_connection", no_answer)
    with pytest.raises(jde_ais.AisError) as err:
        jde_ais.AisClient(cfg).call("voucher_create", {}, idempotent=False)
    assert err.value.kind == jde_ais.UNREACHABLE and not err.value.sent
    assert "can't be reached" in str(err.value)


def test_https_with_the_company_ca_and_without_it():
    with FakeAis(https=True) as ais:
        assert jde_ais.AisClient(ais.settings()).call("suppliers", {})["Suppliers"]
        with pytest.raises(jde_ais.AisError) as err:
            jde_ais.AisClient(ais.settings(ca_file="")).call("suppliers", {})
        assert err.value.kind == jde_ais.CERTIFICATE and "isn't trusted" in str(err.value)


def test_the_address_rules(monkeypatch):
    monkeypatch.delenv("AP_ALLOW_INTERNET", raising=False)
    assert jde_ais.address_problem("") and jde_ais.address_problem("http://10.1.2.3:9300")
    assert "user name" in jde_ais.address_problem("https://me:pw@ais.local")
    assert jde_ais.address_problem("https://ais.company.local:9300") == ""
    assert jde_ais.address_problem("http://127.0.0.1:9300") == ""  # plain http only to this computer
    assert jde_ais.internal_problem("10.20.30.40") == ""
    assert "internet" in jde_ais.internal_problem("8.8.8.8")
    assert "internet" in jde_ais.internal_problem("ais.example.com", resolve=lambda h: ["93.184.216.34"])
    assert jde_ais.internal_problem("ais.corp.example.com", resolve=lambda h: ["10.0.0.5"]) == ""
    assert "can't be found" in jde_ais.internal_problem("nowhere.invalid", resolve=lambda h: [])
    cfg = jde_ais.AisSettings(base_url="https://8.8.8.8", environment="JPD920", username=USER, step1=True)
    cfg.password = PASSWORD
    with pytest.raises(jde_ais.AisError) as err:
        jde_ais.AisClient(cfg)
    assert err.value.kind == jde_ais.ADDRESS


def test_off_sends_nothing(ais):
    cfg = ais.settings(step1=False, step2=False)
    with pytest.raises(jde_ais.AisOff):
        jde_ais.AisClient(cfg)
    assert ais.calls == []


def test_settings_keep_the_password_in_the_env_not_the_database(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(jde_ais, "env_path", lambda: env)
    monkeypatch.delenv(jde_ais.PASSWORD_ENV, raising=False)
    store = Store(tmp_path / "ap_coder.db")
    with pytest.raises(ValueError, match="the password"):
        jde_ais.save(store, base_url="ais.company.local:9300", environment="JPD920", role="", username=USER,
                     step1=True)  # fmt: skip
    changed = jde_ais.save(store, base_url="ais.company.local:9300", environment="JPD920", role="",
                           username=USER, password=PASSWORD, step1=True)  # fmt: skip
    assert "password" in changed and "step1" in changed
    saved = store.get_setting(jde_ais.SETTING_KEY)
    assert PASSWORD not in saved and json.loads(saved)["base_url"] == "https://ais.company.local:9300"
    assert PASSWORD in env.read_text()
    cfg = jde_ais.load(store)
    assert cfg.password == PASSWORD and cfg.step1 and not cfg.step2 and PASSWORD not in repr(cfg)
    assert jde_ais.save(store, base_url="https://ais.company.local:9300", environment="JPD920", role="*ALL",
                        username=USER, step1=True) == []  # the password kept  # fmt: skip
    events = json.dumps(store.events(limit=50))
    assert PASSWORD not in events
    with pytest.raises(ValueError, match="https"):
        jde_ais.save(store, base_url="http://ais.company.local", environment="JPD920", role="", username=USER)
    with pytest.raises(ValueError, match="CA certificate"):
        jde_ais.save(store, base_url="ais.local", environment="E", role="", username="U", ca_file=str(tmp_path / "x"))
    assert jde_ais.save(store, base_url="ais.local", environment="E", role="", username="U", ca_file=str(CA_FILE))


def test_connection_test_says_what_it_found(ais, tmp_path, monkeypatch, caplog):
    store = Store(tmp_path / "ap_coder.db")
    cfg = ais.settings()
    store.set_setting(jde_ais.SETTING_KEY, cfg.to_json())
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)
    with caplog.at_level(logging.DEBUG):
        found = jde_ais.connection_test(store)
    assert found["ok"] and "AIS 9.2.8.3" in found["summary"] and "JPD920" in found["summary"]
    assert PASSWORD not in caplog.text and not any(t in caplog.text for t in ais.tokens)
    assert jde_ais.last_test(store)["ok"]
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, "Nope-Nope-1")
    found = jde_ais.connection_test(store)
    assert not found["ok"] and found["kind"] == jde_ais.AUTH and "Nope-Nope-1" not in found["error"]


def test_the_mapping_can_be_renamed_from_the_data_folder(tmp_path, monkeypatch):
    from ap_coder.core import paths

    monkeypatch.setattr(paths, "private_dir", lambda: tmp_path)
    (tmp_path / jde_ais.MAPPING_FILE).write_text(json.dumps(
        {"orchestrations": {"suppliers": {"name": "ACME_Suppliers", "fields": {"an8": "AN8"}}}}))  # fmt: skip
    mapping = jde_ais.load_mapping()
    assert mapping["orchestrations"]["suppliers"]["name"] == "ACME_Suppliers"
    assert mapping["orchestrations"]["suppliers"]["fields"]["an8"] == "AN8"
    assert mapping["orchestrations"]["suppliers"]["fields"]["name"] == "AlphaName"  # the rest kept
    assert mapping["ais"]["token_path"] == "/jderest/v2/tokenrequest"
    (tmp_path / jde_ais.MAPPING_FILE).write_text("{not json")
    with pytest.raises(ValueError, match="can't be read"):
        jde_ais.load_mapping()
