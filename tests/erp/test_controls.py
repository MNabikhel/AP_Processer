"""The controls report (``erp/controls.py``): the exceptions of a period, from the audit trail."""

from __future__ import annotations

import datetime as dt
import json

from ap_coder.core.store import Store
from ap_coder.erp import controls


def test_an_older_period_keeps_its_events_however_many_came_after(tmp_path, monkeypatch):
    """B-1598: the report read the newest 1,000,000 events and kept those of its period, so a period older than those
    came out empty, saying "None" for its setup changes and overrides (B-1251's kind). Here every read of the newest
    events is cut to 50, as a year of events is cut to 1,000,000: the period's own events are still all found."""
    store = Store(tmp_path / "private" / "ap_coder.db")
    with store._conn() as conn:
        conn.execute("INSERT INTO events (action, actor, detail, created_at) VALUES ('tax_setup_changed', 'Ann', ?, ?)",
                     (json.dumps({"tax_type": "GST", "treatment": "recoverable"}), "2025-03-10T09:00:00"))  # fmt: skip
        conn.executemany("INSERT INTO events (action, actor, detail, created_at) VALUES ('note', 'Bob', '{}', ?)",
                         [("2026-01-05T09:00:00",)] * 60)  # fmt: skip
    newest = Store.events
    monkeypatch.setattr(Store, "events", lambda self, *a, limit=1000, **k: newest(self, *a, limit=min(limit, 50), **k))
    report = controls.build(store, dt.date(2025, 3, 1), dt.date(2025, 3, 31))
    assert [(e["action"], e["actor"]) for e in report["setup"]] == [("tax_setup_changed", "Ann")]
    assert report["events"] == 1
    assert controls.build(store, dt.date(2026, 1, 5), dt.date(2026, 1, 5))["events"] == 60


def test_the_report_reads_the_currency_rates_once_not_once_an_invoice(tmp_path, monkeypatch):
    """B-1598: each approved invoice checked against the approval limit read the currency rates from the database
    again: 89,000 statements a view at the target volume. They are read once a report."""
    store = Store(tmp_path / "private" / "ap_coder.db")
    for n in range(4):
        pdf = tmp_path / f"{n}.pdf"
        pdf.write_bytes(b"%PDF-1.4 " + str(n).encode())
        coding = {"vendor_name": "Acme", "invoice_number": f"A-{n}", "grand_total": 5000 + n, "currency": "USD"}
        store.approve_invoice(store.add_invoice(pdf, coding, {}), coding, "Jane")
    store.set_setting("approval_limit", "1000")
    store.set_setting("fx_rates", "USD=1.37")
    read: list[int] = []
    rates = Store.fx_rates
    monkeypatch.setattr(Store, "fx_rates", lambda self: read.append(1) or rates(self))
    today = dt.date.today()
    report = controls.build(store, today, today)
    assert len(report["one_person"]) == 4 and len(read) == 1
