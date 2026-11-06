"""The JD Edwards sync at the company's size against the stand-in AIS server (D-181): 30,000 suppliers and 600,000
vouchers (730 days at 25,000 invoices a month, one supplier with 20,000 of them). The first sync, the next night's
(only what changed, and the last weeks' vouchers), a whole read interrupted and resumed, and the week's whole read:
each timed, the longest a tiny write elsewhere waited for the sync's write lock measured, and the register compared
with the stand-in's vouchers after each (the voided ones gone).

``python -m tests.erp.test_jde_sync_volume --suppliers 30000 --vouchers 600000`` prints the numbers; the test runs it
at that size (slow: a few minutes).
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from ap_coder.coding.vendors import norm_invoice_number
from ap_coder.core.memory import vendor_key
from ap_coder.core.store import Store
from ap_coder.erp import jde_ais, jde_sync
from tests.fake_ais import PASSWORD, FakeAis, Generated

LONG_AGO = "2020-01-01"
BIG = 20_000  # the vouchers of the biggest supplier (the first supplier's)


def _suppliers(count: int) -> Generated:
    def make(i: int) -> dict[str, Any]:
        return {"an8": str(1_000_000 + i), "name": f"Supplier {i:05d} Ltd.", "tax_id": f"{100_000_000 + i}RT0001",
                "tax_id_2": "", "terms_code": "N30", "terms": "Net 30", "hold_code": "", "bank_transit": "",
                "bank_last4": "", "province": "ON", "currency": "CAD", "_changed": LONG_AGO}  # fmt: skip

    return Generated(count, make, lambda i: str(1_000_000 + i))


def _vouchers(count: int, suppliers: int, today: dt.date) -> Generated:
    def make(i: int) -> dict[str, Any]:
        who = 0 if i < BIG else 1 + i % (suppliers - 1)
        return {"key": f"00001-PV-{i:08d}-001", "document": str(i), "doc_type": "PV", "company": "00001",
                "an8": str(1_000_000 + who), "supplier_name": f"Supplier {who:05d} Ltd.",
                "invoice_number": f"INV-{i:07d}", "invoice_date": (today - dt.timedelta(days=i % 700)).isoformat(),
                "gross": 100.25 + i % 997, "open_amount": 0.0, "pay_status": "P", "batch": "1",
                "_changed": LONG_AGO}  # fmt: skip

    return Generated(count, make, lambda i: f"00001-PV-{i:08d}-001")


def _bills(rows: Any) -> set[tuple[str, str, float]]:
    return {(vendor_key(r["supplier_name"]), norm_invoice_number(r["invoice_number"]), round(float(r["gross"]), 2))
            for r in rows}  # fmt: skip


def _register(path: Path) -> set[tuple[str, str, float]]:
    with sqlite3.connect(path) as conn:
        return {(r[0], r[1], r[2]) for r in conn.execute("SELECT vendor_key, number_key, total FROM erp_invoices")}


class Prober:
    """A tiny write every 20 ms from another connection (an approval's): how long each waited for the write lock (its
    own commit not counted), the longest the sync held it."""

    def __init__(self, path: Path) -> None:
        self.path, self.waits = path, [0.0]
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> Prober:
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stopping.set()
        self.thread.join()

    def _run(self) -> None:
        conn = sqlite3.connect(self.path, timeout=120, isolation_level=None)
        while not self.stopping.is_set():
            began = time.perf_counter()
            conn.execute("BEGIN IMMEDIATE")
            self.waits.append(time.perf_counter() - began)
            conn.execute("INSERT INTO settings (key, value) VALUES ('probe', '1') ON CONFLICT(key) DO UPDATE SET "
                         "value = CAST(value AS INTEGER) + 1")  # fmt: skip
            conn.execute("COMMIT")
            time.sleep(0.02)
        conn.close()

    @property
    def longest(self) -> float:
        return max(self.waits)


def _timed(store: Store, label: str, out: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    with Prober(store.path) as probe:
        began = time.perf_counter()
        report = jde_sync.sync(store, **kwargs)
        took = time.perf_counter() - began
    reads = {p: r.get("read", 0) for p, r in report["parts"].items()}
    out[label] = {"seconds": round(took, 1), "longest_write_wait": round(probe.longest, 3), "read": reads,
                  "ok": report["ok"], "errors": report["errors"]}  # fmt: skip
    return report


def volume(folder: Path, suppliers: int = 30_000, vouchers: int = 600_000) -> dict[str, Any]:
    """The four syncs at this size; what each took and read, and whether the register held the stand-in's vouchers."""
    import os

    os.environ[jde_ais.PASSWORD_ENV] = PASSWORD
    jde_ais.RETRY_PAUSE = 0
    today = dt.date.today()
    out: dict[str, Any] = {"suppliers": suppliers, "vouchers": vouchers}
    store = Store(folder / "ap_coder.db")
    with FakeAis() as ais:
        ais.data["suppliers"] = _suppliers(suppliers)
        ais.data["vouchers"] = made = _vouchers(vouchers, suppliers, today)
        store.set_setting(jde_ais.SETTING_KEY, ais.settings(timeout=60).to_json())
        report = _timed(store, "first", out)
        assert report["ok"], report["errors"]
        assert len(store.master_vendor_ids()) == suppliers
        expected = _bills(made)
        out["first"]["register_right"] = _register(store.path) == expected

        # The next night: 50 suppliers changed, 1,000 vouchers entered, 30 of this month's voided and 20 older ones.
        night = jde_sync._now() + dt.timedelta(days=1)
        jde_sync._now = lambda: night  # type: ignore[assignment]
        changed = (today + dt.timedelta(days=1)).isoformat()
        for i in range(0, 50 * 7, 7):
            ais.data["suppliers"].change(i, changed, hold_code="H")
        for n in range(vouchers, vouchers + 1_000):
            ais.data["vouchers"].add(changed, key=f"00001-PV-{n:08d}-001", document=str(n), invoice_number=f"NEW-{n}",
                                     invoice_date=changed)  # fmt: skip
        recent = [i for i in range(BIG, vouchers) if i % 700 < 20][:30]
        older = [i for i in range(BIG, vouchers) if i % 700 > 400][:20]
        old_bills = _bills(made.row(i) for i in older)
        made.gone |= set(recent) | set(older)
        report = _timed(store, "next_night", out)
        assert report["ok"], report["errors"]
        out["next_night"]["register_right"] = _register(store.path) == _bills(made) | old_bills  # older: weekly
        out["next_night"]["removed"] = report["parts"]["vouchers"]["removed"]

        # The week's whole read, stopped by AIS down half way through the vouchers (page 600 of 1,200), then resumed.
        half = vouchers // 1_000
        ais.script("vouchers", *([None] * half), 503, 503, 503)
        report = _timed(store, "full_interrupted", out, full=True)
        assert set(report["errors"]) == {"vouchers"}, report["errors"]
        asked = len(ais.orchestration_calls("vouchers"))
        report = _timed(store, "full_resumed", out)
        assert report["ok"] and report["parts"]["vouchers"]["resumed"], report
        out["full_resumed"]["voucher_pages"] = len(ais.orchestration_calls("vouchers")) - asked
        out["full_resumed"]["voucher_pages_before"] = half
        out["full_resumed"]["register_right"] = _register(store.path) == _bills(made)
        out["full_resumed"]["removed"] = report["parts"]["vouchers"]["removed"]
    return out


@pytest.fixture
def folder():
    made = Path(tempfile.mkdtemp(prefix="jde-volume-"))
    yield made
    shutil.rmtree(made, ignore_errors=True)


def test_the_sync_at_the_companys_size(folder, monkeypatch):
    monkeypatch.setattr(jde_sync, "_now", jde_sync._now)  # (put back after the harness moves to the next night)
    monkeypatch.setattr(jde_ais, "RETRY_PAUSE", jde_ais.RETRY_PAUSE)
    found = volume(folder)
    print(found)
    for run in ("first", "next_night", "full_resumed"):
        assert found[run]["register_right"], (run, found)
        assert found[run]["longest_write_wait"] < 0.5, (run, found)
    night = found["next_night"]
    assert night["read"]["suppliers"] == 50 and night["removed"] == 30
    assert night["read"]["vouchers"] < 40_000  # 1,000 new and the last 31 days' (~26,500), not 600,000
    assert found["full_resumed"]["removed"] == 20 and found["full_resumed"]["voucher_pages"] < 700


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--suppliers", type=int, default=30_000)
    p.add_argument("--vouchers", type=int, default=600_000)
    a = p.parse_args()
    where = Path(tempfile.mkdtemp(prefix="jde-volume-"))
    try:
        for key, value in volume(where, a.suppliers, a.vouchers).items():
            print(f"{key}: {value}")
    finally:
        shutil.rmtree(where, ignore_errors=True)
