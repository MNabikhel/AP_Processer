"""The JD Edwards supplier and PO sync and the live duplicate check (ap_coder/erp/jde_sync.py, D-104 / D-105): a hold
set in AP Coder never lifted by JDE, every voucher of a big supplier checked, no mass closing of POs on a wrong
answer, and the bank account's transit compared too."""

from __future__ import annotations

import pytest

from ap_coder.coding.vendors import vendor_findings
from ap_coder.core.store import Store
from ap_coder.erp import jde_sync

from .fake_ais import FakeAis
from .test_jde_sync import _coding, connect, quick  # noqa: F401  (the fixture that pauses nothing)


@pytest.fixture
def ais():
    with FakeAis() as fake:
        yield fake


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ap_coder.db")


def _acme(store):
    name = "Acme Office Supplies Ltd."
    return next(v for v in store.master_vendors() if v["display_name"] == name)


def test_the_nightly_sync_never_lifts_a_hold_set_in_ap_coder(store, ais):
    connect(store, ais)
    jde_sync.sync(store, parts=("suppliers",))
    key = _acme(store)["vendor_key"]
    store.save_vendor(key, "Acme Office Supplies Ltd.", status="on_hold", notes="suspected fraud")  # AP's own hold
    jde_sync.sync(store, parts=("suppliers",))
    assert store.get_vendor(key)["status"] == "on_hold"
    assert "VENDOR_ON_HOLD" in [c for _, c, _ in vendor_findings(_coding(invoice_number="Z-1"), store)]


def test_a_hold_in_jde_holds_the_vendor_and_its_release_frees_only_that(store, ais):
    connect(store, ais)
    jde_sync.sync(store, parts=("suppliers",))
    courier = store.get_vendor("northern courier")
    assert courier["erp_hold"] == "H" and courier["status"] == "active"  # JDE's hold, kept apart
    found = [
        m
        for _, c, m in vendor_findings(_coding(vendor_name="Northern Courier Inc.", invoice_number="Q-1"), store)
        if c == "VENDOR_ON_HOLD"
    ]
    assert found and "in JD Edwards" in found[0]
    ais.data["suppliers"][1]["hold_code"] = ""
    jde_sync.sync(store, parts=("suppliers",))
    assert store.get_vendor("northern courier")["erp_hold"] == ""


def test_the_live_check_reads_every_page_of_a_big_supplier(store, ais):
    connect(store, ais)
    jde_sync.sync(store, parts=("suppliers",))
    base = next(r for r in ais.data["vouchers"] if r["invoice_number"] == "INV-7001")
    ais.data["vouchers"].remove(base)
    for i in range(600):  # more than a page (500) of older vouchers
        ais.data["vouchers"].append({**base, "key": f"00001-PV-{i:05d}-001", "document": str(80000 + i),
                                     "invoice_number": f"OLD-{i}"})  # fmt: skip
    ais.data["vouchers"].append({**base, "key": "00001-PV-99999-001"})  # the duplicate, on the second page
    found = jde_sync.live_findings(store, _coding())
    assert [c for _, c, _ in found] == ["DUPLICATE_IN_ERP"]


def _synced_pos(store, ais, count):
    lines = []
    for n in range(count):
        lines.append({**ais.data["purchase_orders"][0], "key": f"{5000 + n}-OP-00001-1", "order_number": str(5000 + n)})
    ais.data["purchase_orders"] = lines
    jde_sync.sync(store, parts=("purchase_orders",))
    assert all(store.purchase_order(str(5000 + n))["status"] == "open" for n in range(count))


def test_an_answer_that_would_close_every_open_po_is_refused(store, ais):
    connect(store, ais)
    _synced_pos(store, ais, 4)
    ais.data["purchase_orders"] = []  # a wrong filter, a lost page...
    report = jde_sync.sync(store, parts=("purchase_orders",))
    part = report["parts"]["purchase_orders"]
    assert part["closed"] == [] and "4 of the 4" in part["refused_close"]
    assert all(store.purchase_order(str(5000 + n))["status"] == "open" for n in range(4))
    assert any("--allow-mass-close" in line for line in jde_sync.report_lines(report))
    report = jde_sync.sync(store, parts=("purchase_orders",), allow_mass_close=True)  # the next sync still knows them
    assert len(report["parts"]["purchase_orders"]["closed"]) == 4


def test_more_than_half_closed_at_once_is_refused_a_few_are_not(store, ais):
    connect(store, ais)
    _synced_pos(store, ais, 4)
    ais.data["purchase_orders"] = ais.data["purchase_orders"][:1]  # 3 of 4 gone
    part = jde_sync.sync(store, parts=("purchase_orders",))["parts"]["purchase_orders"]
    assert part["closed"] == [] and part["refused_close"]
    _synced_pos(store, ais, 4)
    ais.data["purchase_orders"] = ais.data["purchase_orders"][:2]  # 2 of 4: half, closed
    part = jde_sync.sync(store, parts=("purchase_orders",))["parts"]["purchase_orders"]
    assert sorted(part["closed"]) == ["5002", "5003"] and not part["refused_close"]


@pytest.mark.parametrize(
    "printed, flagged",
    [
        ("Transit 00012, Institution 003, Account 9994567", False),
        ("Transit 00099, Institution 001, Account 9994567", True),  # the same last 4, another branch
        ("Account 9994567", False),  # no transit printed: the last 4 alone
        ("Transit 00012, Institution 003, Account 7654321", True),
    ],
)
def test_the_bank_account_check_compares_the_transit_too(store, ais, printed, flagged):
    connect(store, ais)
    jde_sync.sync(store, parts=("suppliers",))  # JDE pays Acme on 00012-003 …4567
    codes = [c for _, c, _ in vendor_findings(_coding(invoice_number="X-9", remit_bank_account=printed), store)]
    assert ("VENDOR_BANK_NOT_JDE" in codes) is flagged
