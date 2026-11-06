"""JD Edwards API step 1 (ap_coder/erp/jde_sync.py, D-104) against the stand-in AIS server: suppliers, GL accounts,
business units, open POs and vouchers synced through the CSV imports' code, the report of what was added and changed,
the nightly timing, and the live "already in JD Edwards?" check with its cache and its fallback when JDE is down."""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pytest

from ap_coder.coding.vendors import vendor_findings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.erp import jde, jde_ais, jde_sync
from tests.fake_ais import PASSWORD, FakeAis


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
    assert courier["erp_hold"] == "H" and courier["qst_number"] == "1234567890TQ0001"  # the hold code: on hold in JDE
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
    assert store.get_vendor("northern courier")["erp_hold"] == ""
    assert next(r for r in store.list_accounts("gl_accounts") if r["code"] == "6100")["category"] == "Office"  # kept
    ais.data["purchase_orders"] = []
    # every open PO at once: only when asked (D-105), and only a whole read says what JDE no longer lists (D-181)
    report = jde_sync.sync(store, allow_mass_close=True, full=True)
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


def test_a_supplier_without_an_an8_is_asked_by_the_invoice_number_as_printed(store, ais):
    """B-1607: for a supplier with no AN8 the live check asked JD Edwards for the invoice number as AP Coder compares
    it ("7001" for "INV-7001"), which the orchestration matches exactly: the voucher already there was never found."""
    connect(store, ais)  # no supplier sync: no AN8 for Acme
    found = jde_sync.live_findings(store, _coding())
    assert [c for _, c, _ in found] == ["DUPLICATE_IN_ERP"] and "voucher 90001 PV" in found[0][2]
    assert ais.orchestration_calls("vouchers")[0]["InvoiceNumber"] == "INV-7001"


def test_a_voucher_the_sync_put_in_the_register_is_said_with_its_voucher_number_and_paid(store, ais):
    """B-1217: once the nightly sync had put the voucher in the register, the review screen said only "already in the
    ERP (dated ...), total ...": the live check's voucher number and paid status were left out. JD Edwards' own answer
    is said instead, once."""
    connect(store, ais)
    jde_sync.sync(store)  # the register holds INV-7001 from the sync
    found = [m for _, c, m in vendor_findings(_coding(), store) if c == "DUPLICATE_IN_ERP"]
    assert len(found) == 1 and "voucher 90001 PV" in found[0] and "paid" in found[0], found


def test_the_live_check_degrades_when_jde_is_down(store, ais):
    connect(store, ais)
    jde_sync.sync(store)  # the register holds INV-7001 from the sync
    ais.stop()
    found = jde_sync.live_findings(store, _coding(invoice_number="INV-9999"))
    assert [c for _, c, _ in found] == ["JDE_NOT_REACHED"] and "last sync" in found[0][2]
    codes = [c for _, c, _ in vendor_findings(_coding(), store)]
    assert "DUPLICATE_IN_ERP" in codes and "JDE_NOT_REACHED" in codes  # the last sync's copy still catches it


def test_jde_down_the_register_is_checked_by_the_suppliers_number_too(store, ais):
    """B-1602: with JDE not reached the check fell back on the register by the invoice's vendor name only, while the
    sync files the vouchers under JDE's supplier name: an invoice whose name differs (found by its GST/HST number, a
    supplier renamed) passed as no duplicate. The register is also looked up under the names of its AN8."""
    connect(store, ais)
    jde_sync.sync(store)  # the register holds INV-7001 from the sync, under "Acme Office Supplies Ltd."
    ais.stop()
    coding = _coding(vendor_name="Acme Supplies Toronto", gst_hst_registration_number="123456789RT0001")
    found = {c: m for _, c, m in vendor_findings(coding, store)}
    assert "JDE_NOT_REACHED" in found
    assert "invoice INV-7001 from this vendor is already in the ERP (dated 2026-09-02)" in found["DUPLICATE_IN_ERP"]


def test_the_live_check_signs_in_once_for_many_checks_and_out_at_the_end(store, ais, monkeypatch):
    """B-1603: each live check signed in to AIS and out again (three requests where one does), hundreds of AIS
    sessions an hour from 300 people. One signed-in client per process for each database: its token is re-used (by
    several people at once too), asked again when it expired, and logged out when the settings change and at exit."""
    connect(store, ais)
    jde_sync.sync(store, parts=("suppliers",))
    monkeypatch.setattr(jde_sync, "CACHE_SECONDS", 0)  # every check asks JDE
    token, logout = ais.mapping["ais"]["token_path"], ais.mapping["ais"]["logout_path"]
    before = len(ais.calls)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: jde_sync.live_findings(store, _coding(invoice_number=f"Z-{i}")), range(100)))
    calls = [path for path, _ in ais.calls[before:]]
    assert (calls.count(token), calls.count(logout), len(calls)) == (1, 0, 101)
    ais.expire_tokens()
    assert jde_sync.live_findings(store, _coding())[0][1] == "DUPLICATE_IN_ERP"  # signed in again, once
    jde_sync.forget_cache()  # the settings saved, or the process stopping (atexit)
    calls = [path for path, _ in ais.calls[before:]]
    assert (calls.count(token), calls.count(logout)) == (2, 1) and not any(ais.tokens.values())


@pytest.mark.parametrize("kind", [jde_ais.UNREACHABLE, jde_ais.TIMEOUT, jde_ais.CERTIFICATE])
def test_jde_not_reached_the_live_check_looks_its_address_up_again(store, monkeypatch, kind):
    """B-1609: the live check's shared AIS client kept the address its name resolved to when it was made, for the life
    of the process: once JDE moved to another server (a failover, a new address in DNS) every check went on failing
    at the old one. A client that can't reach JDE is dropped: the next check looks the name up again."""
    answers = iter([["10.0.0.1"], ["10.0.0.2"]])  # the AIS name moved to another server after the first lookup
    monkeypatch.setattr(jde_ais, "_resolve", lambda host: next(answers))
    used = []

    def once(self, method, path, body, headers, timeout):
        used.append(self.address)
        raise jde_ais.AisError(kind, f"{self.address} can't be reached")

    monkeypatch.setattr(jde_ais.AisClient, "_once", once)
    cfg = jde_ais.AisSettings(base_url="https://ais.corp.example:9443", environment="JDV920", username="u", step1=True)
    store.set_setting(jde_ais.SETTING_KEY, cfg.to_json())
    for _ in range(2):
        assert [c for _, c, _ in jde_sync.live_findings(store, _coding())] == ["JDE_NOT_REACHED"]
        jde_sync._DOWN.clear()  # DOWN_SECONDS later: JDE is asked again
    assert used == ["10.0.0.1", "10.0.0.2"]


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
    # approved only with "Approve anyway" and a reason, kept in the audit trail (D-141)
    assert [s for s, c, _ in vendor_findings(other, store) if c == "VENDOR_BANK_NOT_JDE"] == ["error"]


def test_the_sync_report_says_how_many_gl_descriptions_were_taken_from_jd_edwards(store, ais):
    """B-1210: ``jde sync`` writes JD Edwards' GL account descriptions over AP Coder's (kept so: JDE is the books)
    without saying so: the report says "N descriptions taken from JD Edwards"."""
    connect(store, ais)
    ours = [{"code": "6100", "description": "Paper and toner"}, {"code": "6200", "description": "Couriers"}]
    store.import_accounts("gl_accounts", ours, "code", "description")
    report = jde_sync.sync(store)
    assert {r["code"]: r["description"] for r in store.list_accounts("gl_accounts")}["6100"] == "Office supplies"
    line = next(x for x in jde_sync.report_lines(report) if x.startswith("GL accounts"))
    assert "2 descriptions taken from JD Edwards" in line, line
