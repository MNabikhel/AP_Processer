"""A withdrawn approval takes back what it taught the supplier's template (D-71): the template as it was before is
kept (``template_snapshots``) and put back when the approval is reopened, rejected or sent back, unless a later
approval changed the template since (that one is kept, and the Activity log says so). The Learning page can reset a
supplier's layout, with a confirm step and an audit event."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.web.app import create_app

PAGE = {"X-AP-Coder": "1"}
KEY = "acme"


def _approved(store: Store, n: int) -> int:
    invoice_id = store.add_invoice(f"INV-{n}.pdf", {"vendor_name": "Acme", "line_items": []}, None)
    store.approve_invoice(invoice_id, {"vendor_name": "Acme", "line_items": [], "grand_total": 0.0}, "Ann")
    return invoice_id


def _template(store: Store) -> dict | None:
    return (store.get_supplier_profile(KEY) or {}).get("template")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "s.db")


def test_reopening_an_approval_puts_the_template_back_as_it_was(store):
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}})
    wrong = _approved(store, 1)
    store.save_supplier_profile(KEY, template={"fields": {"a": 2}}, invoice_id=wrong)
    assert _template(store) == {"fields": {"a": 2}}
    store.reopen(wrong, "Ann", "the wrong words were taught")
    assert _template(store) == {"fields": {"a": 1}}
    detail = store.events(actions=["reopened"])[0]["detail"]
    assert "layout" in str(detail) and "put back" in str(detail)


def test_the_first_approval_withdrawn_leaves_no_layout(store):
    first = _approved(store, 1)
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}}, invoice_id=first)
    store.reopen(first, "Ann")
    assert _template(store) is None


def test_a_later_approval_that_changed_the_template_is_kept_and_said(store):
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}})
    wrong = _approved(store, 1)
    store.save_supplier_profile(KEY, template={"fields": {"a": 2}}, invoice_id=wrong)
    later = _approved(store, 2)
    store.save_supplier_profile(KEY, template={"fields": {"a": 3}}, invoice_id=later)
    store.reopen(wrong, "Ann")
    assert _template(store) == {"fields": {"a": 3}}
    detail = str(store.events(actions=["reopened"])[0]["detail"])
    assert "later approval" in detail and "kept" in detail


def test_rejecting_and_sending_back_withdraw_the_layout_too(store):
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}})
    wrong = _approved(store, 1)
    store.save_supplier_profile(KEY, template={"fields": {"a": 2}}, invoice_id=wrong)
    store.reopen(wrong, "Ann")
    # Not approved any more: what learning would teach from it now is refused (B-782), and rejecting it is safe.
    assert store.save_supplier_profile(KEY, template={"fields": {"a": 5}}, invoice_id=wrong) is False
    store.reject_invoice(wrong, "Ann", "duplicate")
    assert _template(store) == {"fields": {"a": 1}}


def test_only_the_last_few_snapshots_of_a_supplier_are_kept(store):
    from ap_coder.core.store._base import KEEP_SNAPSHOTS

    for n in range(KEEP_SNAPSHOTS + 3):
        store.save_supplier_profile(KEY, display_name="Acme", template={"n": n}, invoice_id=_approved(store, n))
    with store._conn() as conn:
        kept = conn.execute("SELECT COUNT(*) FROM template_snapshots WHERE supplier_key = ?", (KEY,)).fetchone()[0]
    assert kept == KEEP_SNAPSHOTS


def test_reset_layout_on_the_learning_page_asks_first_and_is_logged(store):
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}}, invoice_id=_approved(store, 1))
    s = Settings()
    client = TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), store), base_url="http://localhost")
    page = client.get("/learning?tab=suppliers").text
    assert "/learning/suppliers/reset-layout" in page and "data-confirm" in page and "Reset layout" in page
    response = client.post("/learning/suppliers/reset-layout", data={"key": KEY}, headers=PAGE)
    assert response.status_code == 200 and response.json()["ok"]
    assert _template(store) is None
    event = store.events(actions=["layout_reset"])[0]
    assert event["actor"] and "Acme" in str(event["detail"])
    assert client.post("/learning/suppliers/reset-layout", data={"key": "nobody"}, headers=PAGE).status_code == 404
    assert client.post("/learning/suppliers/reset-layout", data={"key": KEY}).status_code == 403


def test_the_learning_bench_measures_a_wrong_approval_and_its_withdrawal(tmp_path):
    from ap_coder.bench.learning import run_learning

    report = run_learning(2, 5, seed=1, out=tmp_path / "learn", workers=1, wrong_at=3)
    wrong = report["summary"]["wrong_approval"]
    assert wrong["at"] == 3 and report["config"]["wrong_approval_at"] == 3
    nxt = wrong["the wrong approval's next invoice"]
    assert nxt["invoices"] == 2 and nxt["invoice_number_wrong"] == 0  # withdrawn: the template is as it was
    assert "## A wrong approval, then its withdrawal" in (tmp_path / "learn" / "report.md").read_text(encoding="utf-8")


def test_a_bulk_approval_in_one_second_keeps_the_newest_snapshot(store, monkeypatch):
    """Snapshots were pruned by time to the second, then by invoice number: more than KEEP_SNAPSHOTS approvals of one
    vendor in one second, out of number order (bulk approval in queue order), pruned the latest one, and withdrawing
    it put nothing back (B-780). They now go by the order of approval, and the one just written is never pruned."""
    from ap_coder.core.store import capture as store_capture
    from ap_coder.core.store._base import KEEP_SNAPSHOTS

    ids = [_approved(store, n) for n in range(KEEP_SNAPSHOTS + 1)][::-1]  # approved newest invoice first
    monkeypatch.setattr(store_capture, "_now", lambda: "2026-10-23T10:00:00")
    store.save_supplier_profile(KEY, display_name="Acme", template={"n": "start"})
    for n, invoice_id in enumerate(ids):
        store.save_supplier_profile(KEY, template={"n": n}, invoice_id=invoice_id)
    with store._conn() as conn:
        kept = {r[0] for r in conn.execute("SELECT invoice_id FROM template_snapshots")}
    assert kept == set(ids[1:])  # the oldest approval's snapshot went, the newest stayed
    store.reopen(ids[-1], "Ann", "wrong words taught")
    assert _template(store) == {"n": KEEP_SNAPSHOTS - 1}


def test_a_snapshot_table_made_before_the_approval_order_is_moved_over(tmp_path):
    """A database made by this version before snapshots kept the order of approval (no ``seq``): the table is
    rebuilt with it, its snapshots kept in the order they were taken (B-780)."""
    import sqlite3

    path = tmp_path / "s.db"
    store = Store(path)
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}})
    wrong = _approved(store, 1)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE template_snapshots")
        conn.execute("CREATE TABLE template_snapshots (invoice_id INTEGER PRIMARY KEY, supplier_key TEXT NOT NULL, "
                     "before_json TEXT, after_json TEXT, at TEXT NOT NULL)")  # fmt: skip
        conn.execute("UPDATE supplier_profiles SET template_json = ? WHERE key = ?", ('{"fields": {"a": 2}}', KEY))
        conn.execute("INSERT INTO template_snapshots VALUES (?, ?, ?, ?, ?)",
                     (wrong, KEY, '{"fields": {"a": 1}}', '{"fields": {"a": 2}}', "2026-10-23T10:00:00"))  # fmt: skip
    store = Store(path)
    with store._conn() as conn:
        assert "seq" in {r["name"] for r in conn.execute("PRAGMA table_info(template_snapshots)")}
    store.reopen(wrong, "Ann")
    assert _template(store) == {"fields": {"a": 1}}


def test_a_withdrawal_after_the_vendor_matching_rules_changed_still_puts_the_layout_back(tmp_path):
    """The supplier keys of the profiles were recomputed under new vendor-matching rules, not their snapshots': a
    withdrawal then found nothing to put back (B-781)."""
    import sqlite3

    path = tmp_path / "s.db"
    store = Store(path)
    old = "name:acme ltee"
    store.save_supplier_profile(old, display_name="Acme Ltée", template={"fields": {"a": 1}})
    wrong = _approved(store, 1)
    store.save_supplier_profile(old, template={"fields": {"a": 2}}, invoice_id=wrong)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'vendor_key_rules'")  # as an older version left it
    store = Store(path)
    assert store.get_supplier_profile(old) is None and store.get_supplier_profile("name:acme")
    store.reopen(wrong, "Ann")
    assert store.get_supplier_profile("name:acme")["template"] == {"fields": {"a": 1}}


def test_two_approvals_withdrawn_oldest_first_leave_neither_taught(store):
    """A (T0 -> T1), then B (T1 -> T2). Withdrawing A keeps T2 (B changed it since); withdrawing B then put back T1,
    which still held what A taught (B-781). B now starts from what was there before A: T0."""
    store.save_supplier_profile(KEY, display_name="Acme", template={"t": 0})
    a, b = _approved(store, 1), _approved(store, 2)
    store.save_supplier_profile(KEY, template={"t": 1}, invoice_id=a)
    store.save_supplier_profile(KEY, template={"t": 2}, invoice_id=b)
    store.reopen(a, "Ann")
    assert _template(store) == {"t": 2}
    store.reopen(b, "Ann")
    assert _template(store) == {"t": 0}


def test_an_approval_withdrawn_before_its_template_was_saved_teaches_nothing(store):
    """Learning runs after the approval is committed: an approval reopened in between (another reviewer) still
    taught the template and left a snapshot no withdrawal would take back (B-782). The status is checked again in
    the same transaction as the save."""
    store.save_supplier_profile(KEY, display_name="Acme", template={"fields": {"a": 1}})
    wrong = _approved(store, 1)
    store.reopen(wrong, "Bob", "not this one")
    assert store.save_supplier_profile(KEY, template={"fields": {"a": 2}}, invoice_id=wrong) is False
    assert _template(store) == {"fields": {"a": 1}}
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM template_snapshots").fetchone()[0] == 0
