"""Pages stay light with a big database: batched queries, a paged queue, a capped approved list."""

import json
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder.bulk import clean_candidates
from ap_coder.demo import load_demo
from ap_coder.store import APPROVED, REVIEW, Store, load_sample_setup

from .conftest import ROOT, SAMPLE_STEM, SAMPLES

APP = str(ROOT / "ap_coder" / "dashboard.py")


def _add(store, tmp_path, n, approve=0):
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    ids = []
    for i in range(n):
        doc = {**gt, "invoice_number": f"N-{i}", "vendor_name": f"Vendor {i % 7}"}
        clean = {"requires_review": i % 3 == 0, "adjusted_confidence": 0.9,
                 "issues": [{"severity": "warning", "code": "X", "message": "m"}] if i % 5 == 0 else []}  # fmt: skip
        ids.append(store.add_invoice(tmp_path / f"{i}.pdf", doc, clean, meta={"n": i}))
    for invoice_id in ids[:approve]:
        store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "jane")
    return ids


def test_invoice_columns_reads_only_what_is_asked(tmp_path):
    store = Store(tmp_path / "a.db")
    ids = _add(store, tmp_path, 4, approve=1)
    rows = store.invoice_columns(("id", "meta", "validation"))
    assert [r["id"] for r in rows] == ids and rows[2]["meta"] == {"n": 2}
    assert [r["id"] for r in store.invoice_columns(("id",), REVIEW)] == ids[1:]
    assert [r["id"] for r in store.invoice_columns(("id", "ai_output"), ids=[ids[3], ids[1]])] == [ids[1], ids[3]]
    assert store.invoice_columns(("id",), ids=[]) == []
    with pytest.raises(ValueError):
        store.invoice_columns(("id; DROP TABLE invoices",))


def test_counts_without_reading_every_invoice(tmp_path):
    store = Store(tmp_path / "a.db")
    _add(store, tmp_path, 3, approve=2)
    assert store.demo_count() == 0
    assert store.approved_since("2000-01-01") == 2 and store.approved_since("2999-01-01") == 0
    load_demo(store)
    assert store.demo_count() == 10


def test_clean_candidates_skip_flagged_and_warned(tmp_path):
    store = Store(tmp_path / "a.db")
    ids = _add(store, tmp_path, 12)
    expected = [i for n, i in enumerate(ids) if n % 3 and n % 5]
    assert sorted(c["id"] for c in clean_candidates(store)) == expected


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    for name in [m for m in sys.modules if m.startswith(("ap_coder.webapp", "ap_coder.dashboard"))]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()
    st.cache_data.clear()
    store = Store(path)
    load_sample_setup(store)
    return store


def test_queue_is_paged(app_db, tmp_path):
    ids = _add(app_db, tmp_path, 120, approve=0)
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    cards = [b.key for b in at.button if (b.key or "").startswith("qopen_")]
    assert len(cards) == 50
    at.button(key="queue_more").click().run()
    assert len([b for b in at.button if (b.key or "").startswith("qopen_")]) == 100
    assert len(at.session_state["queue_order"]) == len(ids)  # Previous / Next still cover the whole queue


def test_approved_list_is_capped_but_every_invoice_can_be_opened(app_db, tmp_path):
    _add(app_db, tmp_path, 105, approve=105)
    assert len(app_db.list_invoices(APPROVED)) == 105
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert len(at.selectbox(key="view_approved").options) == 105
    assert any("100 most recent of 105" in c.value for c in at.caption)
