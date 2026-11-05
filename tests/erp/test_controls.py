"""The controls report (``erp/controls.py``): the exceptions of a period, from the audit trail."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from ap_coder.core.store import Store
from ap_coder.core.store.fx import parse_fx_rates
from ap_coder.erp import controls
from tests import reports_reference as ref

MISSING = object()  # a key not in the coding at all


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


def test_one_person_finds_what_checking_every_approved_coding_found(tmp_path):
    """The invoices over the approval limit approved by one person: SQLite leaves out the ones it can tell are under
    the limit, the rest are checked as before. The same invoices, whatever the currency (none, blank, lower case,
    without a rate, not text), the total (none, text, NaN, just under or over the limit at the rate) or the coding
    (none, not an object), and whatever the period, second approver or limit."""
    store = Store(tmp_path / "private" / "ap_coder.db")
    currencies = [MISSING, None, "CAD", "USD", " usd ", "usd", "EUR", "", "   ", True, False, 0, 5, "U$D", "ÜSD",
                  {"code": "USD"}, "cad\t", "Usd"]  # fmt: skip
    totals = [MISSING, None, 0, 999.99, 1000, 1000.0000001, 1000.01, -2000, "2000", True, False, 1e400, 730,
              729.927, 729.9270072992701, 25000.0, 25000.01, -18248.175182481752, float("nan")]  # fmt: skip
    docs: list[str | None] = [None, "", "[]", "{}", '{"currency": "USD", "grand_total": NaN}']
    for currency in currencies:
        for total in totals:
            doc = {"vendor_name": "Acme", "invoice_number": "A-1"}
            doc |= {} if currency is MISSING else {"currency": currency}
            doc |= {} if total is MISSING else {"grand_total": total}
            docs.append(json.dumps(doc))
    when = ["2026-03-01T00:00:00", "2026-03-15T10:00:00", "2026-02-28T23:59:59", "2026-04-01", None]
    seconds = [None, "", "Sam"]
    with store._conn() as conn:
        for n, doc in enumerate(docs):
            conn.execute(
                "INSERT INTO invoices (source_path, file_name, status, created_at, final_output, reviewed_at, "
                "second_reviewer) VALUES ('x.pdf', 'x.pdf', ?, '2026-01-01', ?, ?, ?)",
                ("approved" if n % 7 else "pending_approval", doc, when[n % len(when)],
                 seconds[n % len(seconds)] if n % 2 else None),
            )  # fmt: skip
    periods = (("2026-03-01", "2026-04-01"), ("2026-01-01", "2027-01-01"), ("2026-05-01", "2026-06-01"))
    for text in ("USD=1.37", "", "USD=1.37, EUR=1.5"):
        rates = parse_fx_rates(text)
        for limit in (1000.0, 25000.0, 0.5):
            for first, last in periods:
                found = controls.one_person(store, first, last, limit, rates)
                assert found == ref.one_person(store, first, last, limit, rates), (text, limit, first)
    assert len(controls.one_person(store, "2026-01-01", "2027-01-01", 1000.0, parse_fx_rates("USD=1.37"))) > 50


def test_the_report_reads_only_the_details_it_lists_and_gives_what_it_gave(tmp_path, monkeypatch):
    """D-156: of the period's approvals SQLite reads out the detail of those that may have left an error or a fraud or
    duplicate signal open or were bulk (any detail it can't tell of too), and of the exports and postings none: the
    same report as decoding every event (``reports_reference``), whatever an approval's detail holds."""
    store = Store(tmp_path / "private" / "ap_coder.db")
    store.set_setting("approval_limit", "1000")
    issue = {"code": "TOTAL_MISMATCH", "severity": "error"}
    details = [
        None, "", "{}", "[]", "0", '{"bulk": true}', '{"bulk": false}', '{"bulk": ""}', '{"bulk": 0}', '{"bulk": "x"}',
        '{"bulk": null}', '{"total": NaN}', '{"open_issues": []}', '{"open_issues": null}', '{"open_issues": false}',
        '{"open_issues": 0}', '{"open_issues": {}}', json.dumps({"open_issues": [issue]}),
        json.dumps({"open_issues": [{"code": "TAX_RATE_NONSTANDARD", "severity": "warning"}]}),
        json.dumps({"open_issues": [{"code": "VENDOR_BANK_CHANGED", "severity": "warning"}], "bulk": True}),
        json.dumps({"open_issues": [{"code": "PO_UNKNOWN"}, {"code": "X", "severity": "info"}]}),
        json.dumps({"open_issues": [{"code": "X", "severity": "Error"}], "changes": [{"what": "x"}]}),
    ]  # fmt: skip
    with store._conn() as conn:
        for n, detail in enumerate(details * 3):
            day = f"2026-03-{1 + n % 28:02d}T10:00:00"
            actor = ["Jane", "Sam", "jane ", None, ""][n % 5]
            for action in ("approved", "exported", "jde_voucher_posted", "final_approved", "sent_back", "note"):
                if action == "approved" or n % 3 == 0:
                    conn.execute("INSERT INTO events (invoice_id, action, actor, detail, created_at) VALUES "
                                 "(?, ?, ?, ?, ?)", (n % 17 or None, action, actor, detail, day))  # fmt: skip
        conn.execute("INSERT INTO events (action, actor, detail, created_at) VALUES ('tax_setup_changed', 'Ann', "
                     "'{\"tax_type\": \"GST\"}', '2026-03-05')")  # fmt: skip
    read: list[int] = []
    loads = json.loads
    monkeypatch.setattr(json, "loads", lambda text, *a, **k: read.append(1) or loads(text, *a, **k))
    for start, end in ((dt.date(2026, 3, 1), dt.date(2026, 3, 31)), (dt.date(2026, 3, 10), dt.date(2026, 3, 12))):
        for duties in (False, True):
            read.clear()
            new = controls.build(store, start, end, duties=duties)
            decoded = len(read)
            assert new == ref.controls_build(store, start, end, duties=duties)
            assert decoded < len(read) / 2  # most details are left as they are
    month = controls.build(store, dt.date(2026, 3, 1), dt.date(2026, 3, 31), duties=True)
    assert month["overrides"] and month["signals"] and month["same_person"] and any(b for _, _, b in month["by_person"])
    with store._conn() as conn:  # an issue without its code: the report fails as it did
        conn.execute("INSERT INTO events (invoice_id, action, actor, detail, created_at) VALUES (1, 'approved', "
                     "'Jane', '{\"open_issues\": [{\"severity\": \"warning\"}]}', '2026-03-03')")  # fmt: skip
    for build in (controls.build, ref.controls_build):
        with pytest.raises(KeyError):
            build(store, dt.date(2026, 3, 1), dt.date(2026, 3, 31))
