"""JD Edwards API step 1 (ap_coder/erp/jde_sync.py, D-104) against the stand-in AIS server: suppliers, GL accounts,
business units, open POs and vouchers synced through the CSV imports' code, the report of what was added and changed,
the nightly timing, and the live "already in JD Edwards?" check with its cache and its fallback when JDE is down."""

from __future__ import annotations

import datetime as dt

import pytest

from ap_coder.coding.vendors import vendor_findings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.erp import jde, jde_ais, jde_sync

from .fake_ais import PASSWORD, FakeAis


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(jde_ais, "RETRY_PAUSE", 0)
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)
    jde_sync.forget_cache()
    yield
    jde_sync.forget_cache()


@pytest.fixture
def ais():
    with FakeAis() as fake:
        yield fake


def connect(store: Store, fake: FakeAis, **changes) -> None:
    store.set_setting(jde_ais.SETTING_KEY, fake.settings(**changes).to_json())


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ap_coder.db")


def test_a_first_sync_fills_every_table_through_the_imports(store, ais):
    connect(store, ais)
    report = jde_sync.sync(store, actor="jane")
    assert report["ok"], report["errors"]
    acme = store.get_vendor("acme office supplies")
    assert acme["erp_id"] == "4001" and acme["expected_gst"] == "123456789RT0001" and acme["terms"] == "Net 30"
    assert acme["status"] == "active" and acme["province"] == "ON" and acme["erp_bank"] == "00012-003 …4567"
    courier = store.get_vendor("northern courier")
    assert courier["status"] == "on_hold" and courier["qst_number"] == "1234567890TQ0001"  # the hold code: on hold
    codes = {r["code"]: r["description"] for r in store.list_accounts("gl_accounts")}
    assert codes == {"6100": "Office supplies", "6200": "Courier and freight", "6200.01": "Courier - same day",
                     "1310": "GST/HST receivable"}  # the inactive 6999 left out  # fmt: skip
    mapped = jde.load(store).gl_map
    assert [(m.gl_code, m.obj, m.sub) for m in mapped] == [("6200.01", "6200", "01")]
    assert {r["code"] for r in store.list_accounts("cost_centers")} == {"CC100", "CC200"}
    po = store.purchase_order("4512")
    assert po["vendor_name"] == "Acme Office Supplies Ltd." and len(po["lines"]) == 2
    assert [li["received_quantity"] for li in po["lines"]] == [10, 0] and po["lines"][0]["gl_code"] == "6100"
    assert store.in_erp("Acme Office Supplies Ltd.", "INV-7001", 113.0)
    parts = report["parts"]
    assert parts["suppliers"]["added"] == ["Acme Office Supplies Ltd.", "Northern Courier Inc."]
    assert parts["gl_accounts"]["not_postable"] == 1 and parts["purchase_orders"]["added"] == ["4512"]
    lines = jde_sync.report_lines(report)
    assert lines[0].startswith("Suppliers: 2 added, 0 changed") and any("1 not postable" in x for x in lines)
    assert store.events(actions=["jde_synced"])[0]["actor"] == "jane"


def test_a_second_sync_reports_what_changed_and_closes_pos_no_longer_open(store, ais):
    connect(store, ais)
    store.import_accounts("gl_accounts", [{"code": "6100", "category": "Office"}], "code", None, "category")
    jde_sync.sync(store)
    ais.data["suppliers"][1]["hold_code"] = ""  # released in JDE
    ais.data["suppliers"][0]["bank_last4"] = "9999"
    ais.data["gl_accounts"][0]["description"] = "Office supplies and paper"
    ais.data["purchase_orders"][1]["received"] = 2
    report = jde_sync.sync(store)
    parts = report["parts"]
    assert parts["suppliers"]["added"] == []
    assert parts["suppliers"]["changed"] == ["Acme Office Supplies Ltd. (bank account)", "Northern Courier Inc. (hold)"]
    assert parts["gl_accounts"]["changed"] == ["6100 (description)"]
    assert parts["purchase_orders"]["changed"] == ["4512"]
    assert store.get_vendor("northern courier")["status"] == "active"
    assert next(r for r in store.list_accounts("gl_accounts") if r["code"] == "6100")["category"] == "Office"  # kept
    ais.data["purchase_orders"] = []
    report = jde_sync.sync(store)
    assert (
        report["parts"]["purchase_orders"]["closed"] == ["4512"] and store.purchase_order("4512")["status"] == "closed"
    )


def test_off_sends_nothing(store, ais):
    connect(store, ais, step1=False, step2=False)
    with pytest.raises(jde_ais.AisOff):
        jde_sync.sync(store)
    connect(store, ais, step1=False, step2=True)  # step 2 alone does not read
    with pytest.raises(jde_ais.AisOff):
        jde_sync.sync(store)
    assert ais.calls == []


def test_jde_down_stops_the_sync_and_says_so(store, ais):
    connect(store, ais)
    jde_sync.sync(store)
    synced = jde_sync.state(store)["at"]
    ais.script("gl_accounts", 503, 503, 503)
    report = jde_sync.sync(store, parts=("suppliers", "gl_accounts", "business_units"))
    assert not report["ok"] and set(report["errors"]) == {"gl_accounts", "business_units"}
    assert report["errors"]["business_units"].startswith("not tried")
    assert jde_sync.state(store)["at"] == synced  # the last good sync stays the last good one
    assert any("GL accounts: not synced" in x for x in jde_sync.report_lines(report))


def test_the_nightly_sync_is_due_once_a_day_after_its_hour(store, ais, monkeypatch):
    connect(store, ais, sync_hour=2)
    night = dt.datetime(2026, 10, 25, 1, 30)
    assert not jde_sync.due(store, night)  # before 2 am
    assert jde_sync.due(store, night.replace(hour=3))
    monkeypatch.setattr(jde_sync, "_now", lambda: night.replace(hour=3))
    assert jde_sync.tick(store, night.replace(hour=3)) == ["synced"]
    assert not jde_sync.due(store, night.replace(hour=23))  # done today
    assert jde_sync.due(store, night + dt.timedelta(days=1, hours=2))


def _coding(**changes) -> InvoiceCoding:
    data = {"vendor_name": "Acme Office Supplies Ltd.", "invoice_number": "INV-7001", "invoice_date": "2026-10-01",
            "subtotal": 100.0, "tax_total": 13.0, "grand_total": 113.0, "currency": "CAD", "confidence_score": 0.9,
            "line_items": [], **changes}  # fmt: skip
    return InvoiceCoding.model_validate(data)


def test_the_live_check_finds_a_voucher_already_in_jde(store, ais):
    connect(store, ais)
    jde_sync.sync(store, parts=("suppliers",))
    found = jde_sync.live_findings(store, _coding())
    assert len(found) == 1 and found[0][1] == "DUPLICATE_IN_ERP"
    assert "already in JD Edwards: voucher 90001 PV dated 2026-09-02, total 113.00, paid" in found[0][2]
    assert jde_sync.live_findings(store, _coding(invoice_number="inv 7001"))  # the number written another way
    asked = len(ais.orchestration_calls("vouchers"))
    jde_sync.live_findings(store, _coding())
    assert len(ais.orchestration_calls("vouchers")) == asked  # cached
    assert jde_sync.live_findings(store, _coding(invoice_number="INV-7002")) == []
    assert jde_sync.live_findings(store, _coding(grand_total=-113.0)) == []  # a credit note isn't a duplicate
    codes = [c for _, c, _ in vendor_findings(_coding(), store)]
    assert "DUPLICATE_IN_ERP" in codes  # through the review screen's vendor checks


def test_the_live_check_degrades_when_jde_is_down(store, ais):
    connect(store, ais)
    jde_sync.sync(store)  # the register holds INV-7001 from the sync
    ais.stop()
    found = jde_sync.live_findings(store, _coding(invoice_number="INV-9999"))
    assert [c for _, c, _ in found] == ["JDE_NOT_REACHED"] and "last sync" in found[0][2]
    codes = [c for _, c, _ in vendor_findings(_coding(), store)]
    assert "DUPLICATE_IN_ERP" in codes and "JDE_NOT_REACHED" in codes  # the last sync's copy still catches it


def test_the_live_check_is_off_with_step_1(store, ais):
    connect(store, ais, step1=False)
    assert jde_sync.live_findings(store, _coding()) == []
    vendor_findings(_coding(), store)
    assert ais.calls == []


def test_a_printed_bank_account_jde_does_not_pay_is_flagged(store, ais):
    connect(store, ais)
    jde_sync.sync(store, parts=("suppliers",))
    same = _coding(invoice_number="X-1", remit_bank_account="Transit 00012, Institution 003, Account 1234567")
    other = _coding(invoice_number="X-2", remit_bank_account="Transit 00099, Institution 001, Account 7654321")
    assert "VENDOR_BANK_NOT_JDE" not in [c for _, c, _ in vendor_findings(same, store)]
    found = [m for _, c, m in vendor_findings(other, store) if c == "VENDOR_BANK_NOT_JDE"]
    assert found and "…4321" in found[0] and "00012-003 …4567" in found[0]
