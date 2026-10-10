"""GL suggestions for uncoded lines, and recurring-vendor detection."""

import datetime as dt
import sys

import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder import recurring
from ap_coder.demo import load_demo
from ap_coder.reference_data import UNASSIGNED
from ap_coder.store import Store
from ap_coder.suggest import suggest_gl

from .conftest import ROOT


def _row(vendor, final_gl, description, outcome="accepted", cc="CC400"):
    return {"vendor_key": vendor.lower(), "vendor_name": vendor, "description": description, "final_gl": final_gl,
            "final_cc": cc, "outcome": outcome, "created_at": "2026-09-01"}  # fmt: skip


def test_vendor_history_comes_first(reference):
    feedback = [_row("acme", "6010", "Dell monitor 27 inch"), _row("acme", "6010", "Dell monitor 24 inch")]
    out = suggest_gl("Dell monitor 32 inch", "ACME", feedback, reference)
    assert out[0].gl_code == "6010" and out[0].cost_center == "CC400"
    assert "2 times for similar lines from this vendor" in out[0].reasons[0]


def test_other_vendors_and_account_descriptions(reference):
    feedback = [_row("other co", "6320", "Catering for client lunch meeting")]
    out = suggest_gl("Catering client lunch", "Someone new", feedback, reference)
    assert out[0].gl_code == "6320" and "1 other vendor" in out[0].reasons[0]
    keyword = suggest_gl("Copy paper and toner cartridges", "Nobody", [], reference)
    assert keyword[0].gl_code == "6000" and "account description mentions" in keyword[0].reasons[0]
    plural = suggest_gl("27 inch monitor", "Nobody", [], reference)  # "monitors" in the account description
    assert plural and plural[0].gl_code == "6010"


def test_never_suggests_tax_accounts_or_nonsense(reference):
    tax_gls = reference.tax.tax_gl_codes()
    feedback = [_row("acme", next(iter(tax_gls)), "GST on widgets"), _row("acme", UNASSIGNED, "GST on widgets")]
    assert all(s.gl_code not in tax_gls for s in suggest_gl("GST on widgets", "acme", feedback, reference))
    assert suggest_gl("", "acme", feedback, reference) == []
    assert suggest_gl("zzqx wvvy", "acme", [], reference) == []


def _invoices(vendor, start, every, n, total=100.0):
    day = dt.date.fromisoformat(start)
    return [
        {"vendor_key": vendor, "vendor_name": vendor.title(), "currency": "CAD", "grand_total": total + i,
         "invoice_date": (day + dt.timedelta(days=every * i + (i % 2))).isoformat()}
        for i in range(n)
    ]  # fmt: skip


def test_monthly_vendor_is_late_after_the_grace_period():
    rows = _invoices("hydro", "2026-03-01", 30, 6)  # last around 2026-07-29
    on_time = recurring.detect(rows, today=dt.date(2026, 8, 10))
    assert on_time[0].cadence == "Monthly" and on_time[0].status == recurring.ON_TRACK
    assert recurring.detect(rows, today=dt.date(2026, 8, 26))[0].status == recurring.DUE_SOON
    late = recurring.detect(rows, today=dt.date(2026, 9, 20))[0]
    assert late.status == recurring.LATE and late.days_late > 7 and late.typical_total == 102.5


def test_irregular_rare_and_credit_notes_are_not_recurring():
    irregular = [{"vendor_key": "x", "vendor_name": "X", "currency": "CAD", "grand_total": 50, "invoice_date": d}
                 for d in ("2026-01-01", "2026-01-04", "2026-03-30", "2026-04-02", "2026-08-15")]  # fmt: skip
    assert recurring.detect(irregular) == []
    assert recurring.detect(_invoices("y", "2026-01-01", 30, 2)) == []
    credits = _invoices("z", "2026-01-01", 30, 5, total=-100.0)
    assert recurring.detect(credits) == []
    weekly = recurring.detect(_invoices("w", "2026-01-01", 7, 8), today=dt.date(2026, 2, 26))
    assert weekly[0].cadence in ("Weekly", "Every 8 days")


def test_late_vendors_are_listed_first():
    rows = _invoices("late", "2026-01-01", 30, 5) + _invoices("fine", "2026-05-01", 30, 5)
    found = recurring.detect(rows, today=dt.date(2026, 9, 25))
    assert [r.vendor_key for r in found] == ["late", "fine"]


def _fresh_app(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    for name in [m for m in sys.modules if m.startswith(("ap_coder.webapp", "ap_coder.dashboard"))]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()
    st.cache_data.clear()
    return Store(path)


def test_suggestion_button_codes_the_line(tmp_path, monkeypatch):
    store = _fresh_app(tmp_path, monkeypatch)
    load_demo(store)
    harbour = next(i for i in store.list_invoices() if i["vendor_name"].startswith("Harbourview"))
    key = f"inv{harbour['id']}"
    at = AppTest.from_file(str(ROOT / "ap_coder" / "dashboard.py"), default_timeout=90)
    at.session_state["open_invoice"] = harbour["id"]
    at.run()
    assert not at.exception
    # The key is <invoice>_sugg_<line>_<position>_<GL>: line 4, account 6800.
    next(b for b in at.button if (b.key or "").startswith(f"{key}_sugg_4_") and b.key.endswith("_6800")).click().run()
    at.run()
    assert not at.exception
    assert not [b for b in at.button if (b.key or "").startswith(f"{key}_sugg_")]  # line 4 is coded now
    at.button(key=f"{key}_approve").click().run()
    assert store.get_invoice(harbour["id"])["final_output"]["line_items"][3]["predicted_gl_code"] == "6800"


def test_vendors_page_shows_recurring_vendors(tmp_path, monkeypatch):
    store = _fresh_app(tmp_path, monkeypatch)
    import json

    from .conftest import SAMPLE_STEM, SAMPLES

    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    for i, day in enumerate(("2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01")):
        store.add_invoice(tmp_path / f"{i}.pdf", {**gt, "invoice_number": f"R-{i}", "invoice_date": day}, {})
    at = AppTest.from_string("from ap_coder.webapp.vendors import page_vendors\npage_vendors()", default_timeout=90)
    at.run()
    assert not at.exception
    assert "Monthly" in "".join(h.proto.body for h in at.get("html"))
