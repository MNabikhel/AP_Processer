"""Fixes from the store bug hunt: exports that race, stale Reject and Approve clicks, audit outcomes kept after
a reopen, lessons from approvals nobody checked, and batch totals added across currencies."""

import sqlite3
import threading
import time

import pytest
from streamlit.testing.v1 import AppTest

import ap_coder.store as store_module
from ap_coder.capture.workflow import AUTONOMOUS_REVIEWER
from ap_coder.store import APPROVED, PARKED, PENDING, REJECTED, Store

from .test_dashboard_pages import TIMEOUT, _ok, db  # noqa: F401  (db is a fixture)

LINES = [{"line_number": 1, "description": "Widgets", "amount": 100.0, "predicted_gl_code": "6000",
          "predicted_cost_center": ""}]  # fmt: skip


def _invoice(tmp_path, store, number="1", total=100.0, currency="CAD", meta=None):
    path = tmp_path / f"inv-{number}.txt"
    path.write_text(number)
    output = {"vendor_name": "Acme Ltd", "invoice_number": number, "invoice_date": "2026-01-01",
              "grand_total": total, "currency": currency, "line_items": LINES}  # fmt: skip
    return store.add_invoice(path, output, {"requires_review": True}, meta=meta), output


def test_two_people_exporting_at_once_never_export_an_invoice_twice(tmp_path, monkeypatch):
    # Ann's export has read the invoices to export when Bob, on another computer, exports the same one.
    ann, bob = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    invoice_id, output = _invoice(tmp_path, ann)
    ann.approve_invoice(invoice_id, output, "Ann")
    real_now, bob_saw = store_module._now, []

    def bob_exports():
        try:
            bob_saw.append(bob.create_export_batch([invoice_id], "csv", actor="Bob"))
        except ValueError as exc:
            bob_saw.append(exc)

    thread = threading.Thread(target=bob_exports)

    def now_while_ann_exports():
        if not thread.is_alive() and not bob_saw:
            thread.start()
            time.sleep(0.5)  # Bob's export runs now, if nothing holds it back
        return real_now()

    monkeypatch.setattr(store_module, "_now", now_while_ann_exports)
    batch = ann.create_export_batch([invoice_id], "csv", actor="Ann")
    thread.join(10)
    monkeypatch.setattr(store_module, "_now", real_now)
    assert len(bob_saw) == 1 and isinstance(bob_saw[0], ValueError)  # Bob waited, then found nothing to export
    assert [(b["id"], b["invoices"]) for b in ann.export_batches()] == [(batch, 1)]
    assert ann.get_invoice(invoice_id)["export_batch"] == batch
    assert [e["actor"] for e in ann.events(invoice_id, ["exported"])] == ["Ann"]


def test_an_export_takes_only_invoices_still_approved(tmp_path):
    store = Store(tmp_path / "ap.db")
    kept, output = _invoice(tmp_path, store, "1", 100.0)
    store.approve_invoice(kept, output, "Ann")
    gone, output2 = _invoice(tmp_path, store, "2", 50.0)
    store.approve_invoice(gone, output2, "Ann")
    store.reopen(gone, "Bob", "wrong supplier")  # reopened and rejected after the export screen listed it
    store.reject_invoice(gone, "Bob", "not ours")
    batch = store.create_export_batch([kept, gone], "csv", actor="Ann")
    (row,) = store.export_batches()
    assert (row["invoices"], row["total"]) == (1, 100.0)
    assert store.batch_invoice_ids(batch) == [kept] and store.get_invoice(gone)["export_batch"] is None


def test_a_stale_reject_keeps_a_finished_second_approval(tmp_path):
    store = Store(tmp_path / "ap.db")
    store.set_setting("approval_limit", "50")
    invoice_id, output = _invoice(tmp_path, store)
    store.approve_invoice(invoice_id, output, "Ann")
    assert store.get_invoice(invoice_id)["status"] == PENDING
    with pytest.raises(ValueError):  # waiting for the second approval: not from the review queue either
        store.reject_invoice(invoice_id, "Carol", "dup?")
    store.final_approve(invoice_id, "Bob")
    lessons = len(store.feedback_rows())
    with pytest.raises(ValueError):  # Carol's screen was opened before Ann approved it
        store.reject_invoice(invoice_id, "Carol", "dup?")
    inv = store.get_invoice(invoice_id)
    assert (inv["status"], inv["reviewer"], inv["second_reviewer"]) == (APPROVED, "Ann", "Bob")
    assert lessons and len(store.feedback_rows()) == lessons


def test_a_parked_or_failed_invoice_cannot_be_approved_but_a_parked_one_can_be_rejected(tmp_path):
    store = Store(tmp_path / "ap.db")
    parked, output = _invoice(tmp_path, store)
    store.park_invoice(parked, "Dan", "waiting for the buyer to confirm the price")
    with pytest.raises(ValueError, match="parked"):  # Ann's screen was opened before Dan parked it
        store.approve_invoice(parked, output, "Ann")
    assert store.get_invoice(parked)["status"] == PARKED and store.feedback_rows() == []
    store.reject_invoice(parked, "Dan", "the buyer never ordered it")
    inv = store.get_invoice(parked)
    assert (inv["status"], inv["parked_reason"], inv["follow_up"]) == (REJECTED, None, None)
    failed = store.add_invoice(tmp_path / "missing.pdf", None, None, error="could not be read")
    with pytest.raises(ValueError):
        store.approve_invoice(failed, output, "Ann")
    with pytest.raises(ValueError):
        store.reject_invoice(failed, "Ann")
    assert store.get_invoice(failed)["status"] == "failed"


def _outcome_sources(store, invoice_id):
    with sqlite3.connect(store.path) as conn:
        return sorted(r[0] for r in conn.execute("SELECT source FROM supplier_outcomes WHERE invoice_id = ?",
                                                 (invoice_id,)))  # fmt: skip


@pytest.mark.parametrize("undo", ["reopen", "send_back", "reject"])
def test_an_audit_samples_outcomes_go_when_its_approval_is_withdrawn(tmp_path, undo):
    store = Store(tmp_path / "ap.db")
    if undo == "send_back":
        store.set_setting("approval_limit", "50")
    invoice_id, output = _invoice(tmp_path, store, meta={"capture": {"audit": True}})
    rows = [{"field": "invoice_number", "ai_value": "1", "final_value": "1", "correct": True}]
    key = store.supplier_key_for("Acme Ltd")
    if undo == "reject":  # outcomes recorded while it was still in the queue (e.g. an earlier approval)
        store.record_outcomes(key, invoice_id, rows, source="audit")
        store.reject_invoice(invoice_id, "Bob", "not ours")
    else:
        store.approve_invoice(invoice_id, output, "Ann")
        store.record_outcomes(key, invoice_id, rows, source="audit")
        store.record_outcomes(key, invoice_id, rows, source="review")
        assert _outcome_sources(store, invoice_id) == ["audit", "review"]
        getattr(store, undo)(invoice_id, "Bob", "wrong supplier")
    assert _outcome_sources(store, invoice_id) == []  # neither counts towards the supplier's autonomy


def test_approvals_nobody_checked_teach_nothing_and_count_for_no_accuracy(tmp_path):
    store = Store(tmp_path / "ap.db")
    auto, output = _invoice(tmp_path, store, "1")
    store.approve_invoice(auto, output, AUTONOMOUS_REVIEWER, login="ap-coder")
    assert store.get_invoice(auto)["status"] == APPROVED
    assert store.feedback_rows() == []
    assert (store.metrics()["lines_reviewed"], store.metrics()["line_accuracy"]) == (0, None)
    person, output2 = _invoice(tmp_path, store, "2")
    store.approve_invoice(person, output2, "Ann")
    assert [(r["invoice_id"], r["reviewer"]) for r in store.feedback_rows()] == [(person, "Ann")]
    # A database from an older version kept lessons from AP Coder's own approvals: withdrawn on opening.
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, final_gl, "
            "outcome, reviewer, created_at) VALUES (?, 1, 'acme', 'Acme Ltd', 'Widgets', '6000', 'accepted', ?, ?)",
            (auto, AUTONOMOUS_REVIEWER, "2026-01-01T00:00:00"),
        )
    assert [r["reviewer"] for r in Store(store.path).feedback_rows()] == ["Ann"]


def test_an_undone_batch_shows_its_totals_per_currency(tmp_path):
    store = Store(tmp_path / "ap.db")
    ids = []
    for number, total, currency in (("1", 100.0, "CAD"), ("2", 40.0, "USD"), ("3", 10.5, "")):
        invoice_id, output = _invoice(tmp_path, store, number, total, currency)
        store.approve_invoice(invoice_id, output, "Ann")
        ids.append(invoice_id)
    mixed = store.create_export_batch(ids, "csv")
    assert store.export_batches()[0]["total"] is None  # dollars and euros do not add up
    store.undo_export_batch(mixed)
    assert mixed not in store.batch_totals()
    assert store.exported_totals()[mixed] == {"CAD": 110.5, "USD": 40.0}
    single = store.create_export_batch([ids[0]], "csv")
    assert store.export_batches()[0]["total"] == 100.0 and store.exported_totals()[single] == {"CAD": 100.0}


def test_the_exports_page_shows_an_undone_batch_per_currency(db):  # noqa: F811
    store = Store(db)
    ids = []
    for number, total, currency in (("1", 100.0, "CAD"), ("2", 40.0, "USD")):
        invoice_id, output = _invoice(db.parent, store, number, total, currency)
        store.approve_invoice(invoice_id, output, "Ann")
        ids.append(invoice_id)
    store.undo_export_batch(store.create_export_batch(ids, "csv"))
    at = _ok(AppTest.from_string(
        "from ap_coder.webapp.exports import page_exports\npage_exports()", default_timeout=TIMEOUT
    ).run())  # fmt: skip
    html = " ".join(h.proto.body for h in at.get("html"))
    assert "40.00 <span class='apc-muted'>USD</span>" in html and "140.00" not in html
