"""What a process keeps between requests (D-159, ``Store.summary_memo``): a kept answer holds only while a stamp read
from the database before the work says nothing it depends on changed, whichever connection changed it (another
Store, plain SQL); the counts of changes the triggers keep (``changes/<kind>``); an answer worked out inside a write
that is undone; a restore; the answers kept on them (D-162)."""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading
import time

import pytest

from ap_coder.core.store import Store
from ap_coder.core.store import summary as summary_module

NOW = "2026-01-05T09:00:00"


def _rows(store: Store, table: str) -> list[tuple]:
    """What a kept answer is worked out from here: every row of ``table``, read afresh."""
    with sqlite3.connect(store.path) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


class Kept:
    """``store.summary_memo(name, ...)`` of ``compute``, counting how many times it is worked out."""

    def __init__(self, store: Store, name: str, compute, **stamp) -> None:
        self.store, self.name, self.compute, self.stamp, self.worked = store, name, compute, stamp, 0

    def __call__(self):
        def work():
            self.worked += 1
            return self.compute()

        return self.store.summary_memo(self.name, work, **self.stamp)


def _counts(store: Store) -> dict[str, str]:
    with sqlite3.connect(store.path) as conn:
        return dict(conn.execute("SELECT key, value FROM settings WHERE key LIKE 'changes/%'").fetchall())


# What each kind counts, and a row added, changed and deleted there (``{n}``: a number of the test's own).
KINDS = [
    ("vendors", "vendors", "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('v{n}', 'V', '{now}')",
     "UPDATE vendors SET notes = 'a note {n}'", "DELETE FROM vendors WHERE vendor_key = 'v{n}'"),
    ("suppliers", "supplier_outcomes", "INSERT INTO supplier_outcomes (supplier_key, field, correct, at) VALUES "
     "('name:acme', 'f{n}', 1, '{now}')", "UPDATE supplier_outcomes SET correct = 0",
     "DELETE FROM supplier_outcomes WHERE field = 'f{n}'"),
    ("readers", "reader_outcomes", "INSERT INTO reader_outcomes (reader, field, correct, at) VALUES ('rules', "
     "'f{n}', 1, '{now}')", "UPDATE reader_outcomes SET correct = 0",
     "DELETE FROM reader_outcomes WHERE field = 'f{n}'"),
    ("purchase_orders", "purchase_orders", "INSERT INTO purchase_orders (po_key, po_number, updated_at) VALUES "
     "('po{n}', 'PO{n}', '{now}')", "UPDATE purchase_orders SET status = 'closed'",
     "DELETE FROM purchase_orders WHERE po_key = 'po{n}'"),
    ("purchase_orders", "po_lines", "INSERT INTO po_lines (po_key, line_number, description, quantity, unit_price, "
     "amount) VALUES ('po1', {n}, 'Bolts', 1, 2, 2)", "UPDATE po_lines SET received_quantity = 1",
     "DELETE FROM po_lines WHERE line_number = {n}"),
    ("register", "erp_invoices", "INSERT INTO erp_invoices (vendor_key, number_key, total, vendor_name, "
     "invoice_number, imported_at) VALUES ('acme', 'n{n}', 10, 'Acme', 'N{n}', '{now}')",
     "UPDATE erp_invoices SET copies = 2", "DELETE FROM erp_invoices WHERE number_key = 'n{n}'"),
    ("lessons", "feedback", "INSERT INTO feedback (vendor_key, vendor_name, description, final_gl, outcome, "
     "created_at) VALUES ('acme', 'Acme', 'line {n}', '6100', 'accepted', '{now}')",
     "UPDATE feedback SET final_gl = '6200'", "DELETE FROM feedback WHERE description = 'line {n}'"),
    ("situations", "lessons", "INSERT INTO lessons (situation, vendor_key, field, created_at, updated_at) VALUES "
     "('s{n}', 'acme', 'gl', '{now}', '{now}')", "UPDATE lessons SET seen = seen + 1",
     "DELETE FROM lessons WHERE situation = 's{n}'"),
    ("situations", "lesson_approvals", "INSERT INTO lesson_approvals (invoice_id, situation, at) VALUES ({n}, 's1', "
     "'{now}')", "UPDATE lesson_approvals SET confirmed = 1", "DELETE FROM lesson_approvals WHERE invoice_id = {n}"),
]  # fmt: skip


@pytest.mark.parametrize(("kind", "table", "add", "change", "delete"), KINDS, ids=[k[1] for k in KINDS])
def test_a_kept_answer_follows_each_change_of_its_kind_made_through_another_store_or_plain_sql(
    tmp_path, kind, table, add, change, delete
):
    """Each kind's count (``changes/<kind>``) moves at an insert, an update and a delete of its tables, however it is
    made: the answer kept on it is the one reading it afresh gives, and is reused while nothing changed."""
    store = Store(tmp_path / "ap.db")
    other = Store(store.path)  # e.g. another web worker, or the background process
    kept = Kept(store, f"rows/{table}", lambda: _rows(store, table), topic="", changes=kind)
    assert kept() == [] and kept() == [] and kept.worked == 1
    for n, (sql, through) in enumerate([(add, "store"), (add, "plain"), (change, "store"), (change, "plain"),
                                        (delete, "store"), (delete, "plain")], start=1):  # fmt: skip
        sql = sql.format(n=1 if through == "store" else 2, now=NOW)  # each way adds a row, then deletes it
        if through == "store":
            with other._conn() as conn:
                conn.execute(sql)
        else:
            with sqlite3.connect(store.path) as conn:
                conn.execute(sql)
        assert kept() == _rows(store, table), (sql, through)
        assert kept.worked == n + 1 and kept() == _rows(store, table) and kept.worked == n + 1
    assert _counts(store)[f"changes/{kind}"] == "8"  # (each change, of two rows)


def _invoice(store: Store, tmp_path, ground_truth, name: str, vendor: str = "") -> int:
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(name.encode())
    doc = {**ground_truth, "invoice_number": name, **({"vendor_name": vendor} if vendor else {})}
    return store.add_invoice(path, doc, {"requires_review": True})


def test_the_approved_count_moves_only_when_an_approved_invoice_does(tmp_path, ground_truth):
    """``changes/approved``: an invoice approved or no longer approved, or its vendor or coding changed while approved,
    added or deleted approved; not a review invoice's coding, nor an approved one's export or confidence."""
    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    first, second = (_invoice(store, tmp_path, ground_truth, n) for n in ("A-1", "A-2"))

    def approved():
        """The count, checked against an answer kept on it: what reading it afresh gives."""
        assert kept() == fresh()
        return store.summary_query("SELECT value FROM settings WHERE key = 'changes/approved'")[0][0]

    def fresh():
        with sqlite3.connect(store.path) as conn:
            return conn.execute("SELECT id, vendor_key, final_output, ai_output FROM invoices WHERE status = "
                                "'approved' ORDER BY id").fetchall()  # fmt: skip

    def plain(sql: str, *args) -> None:
        with sqlite3.connect(store.path) as conn:
            conn.execute(sql, args)

    kept = Kept(store, "approved", fresh, topic="", changes="approved")

    with sqlite3.connect(store.path) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('changes/approved', '0')")
    plain("UPDATE invoices SET final_output = ai_output, vendor_key = 'moved' WHERE id = ?", first)
    assert approved() == "0"  # (in review)
    other.approve_invoice(first, ground_truth, "Jordan Lee")
    assert approved() == "1"
    other.create_export_batch([first], "csv", actor="Jordan Lee")
    plain("UPDATE invoices SET adjusted_confidence = 0.5, reviewed_at = 'later', final_output = final_output, "
          "status = status WHERE id = ?", first)  # fmt: skip
    assert approved() == "1"
    for n, sql in enumerate(["UPDATE invoices SET vendor_key = 'moved' WHERE id = ?",
                             "UPDATE invoices SET final_output = '{}' WHERE id = ?",
                             "UPDATE invoices SET ai_output = '{}' WHERE id = ?",
                             "UPDATE invoices SET status = 'review' WHERE id = ?",
                             "UPDATE invoices SET status = 'approved' WHERE id = ?",
                             "DELETE FROM invoices WHERE id = ?"], start=2):  # fmt: skip
        plain(sql, first)
        assert approved() == str(n), sql
    plain("INSERT INTO invoices (source_path, file_name, status, created_at) VALUES ('x', 'x', 'approved', ?)", NOW)
    plain("DELETE FROM invoices WHERE id = ?", second)  # (in review)
    assert approved() == "8"


def test_each_vendors_count_moves_with_what_its_invoices_list_shows(tmp_path, ground_truth):
    """``changes/vendor/<key>``: an invoice of the vendor's added or deleted, or a column ``vendor_invoices`` lists
    changed (a move to another vendor counts for both); a check's confidence is not one of them."""
    store = Store(tmp_path / "ap.db")
    acme = _invoice(store, tmp_path, ground_truth, "V-1", "Acme Supply")
    beta = store.get_invoice(_invoice(store, tmp_path, ground_truth, "V-2", "Beta Ltd"))["vendor_key"]
    key = store.get_invoice(acme)["vendor_key"]
    listed = summary_module._VENDOR_LISTED

    def fresh():
        with sqlite3.connect(store.path) as conn:
            return conn.execute(f"SELECT {listed} FROM invoices WHERE vendor_key = ?", (key,)).fetchall()

    kept = Kept(store, "acme", fresh, topic="", changes=f"vendor/{key}")
    assert kept() == fresh() and kept.worked == 1
    before = _counts(store)
    assert before[f"changes/vendor/{key}"] == before[f"changes/vendor/{beta}"] == "1"
    store.refresh_confidence(acme, 0.12, True)
    assert _counts(store) == before and kept() == fresh() and kept.worked == 1
    for column, value in (("invoice_date", "2026-02-02"), ("grand_total", 1.5), ("final_output", "{}"),
                          ("status", "parked"), ("export_batch", 7), ("reviewed_at", NOW)):  # fmt: skip
        with sqlite3.connect(store.path) as conn:
            conn.execute(f"UPDATE invoices SET {column} = ? WHERE id = ?", (value, acme))
        assert kept() == fresh(), column
    assert _counts(store)[f"changes/vendor/{key}"] == "7" and _counts(store)[f"changes/vendor/{beta}"] == "1"
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET vendor_key = ? WHERE id = ?", (beta, acme))
    assert _counts(store)[f"changes/vendor/{key}"] == "8" and _counts(store)[f"changes/vendor/{beta}"] == "2"
    assert kept() == fresh() == [] and kept.worked == 8
    store.delete_invoice(acme)
    assert _counts(store)[f"changes/vendor/{key}"] == "8" and _counts(store)[f"changes/vendor/{beta}"] == "3"


def test_an_answer_worked_out_in_a_write_that_is_undone_is_neither_kept_nor_reused_nor_waited_for(
    tmp_path, monkeypatch
):
    """Inside a write under way the stamp counts that write's own changes, and the answer reads them: kept, it would be
    served once a later change, by anyone, brought the count back to that number, though the write was undone. It is
    worked out afresh, not taken from what is kept either, and without waiting for the page working it out (whose
    work may need the write's lock)."""
    monkeypatch.setattr(summary_module, "MEMO_WAIT", 3.0)
    store = Store(tmp_path / "ap.db")
    with sqlite3.connect(store.path) as conn:
        conn.execute("INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('acme', 'Acme', ?)", (NOW,))

    def names():
        with store._conn() as conn:  # (the session's connection, inside the write)
            return dict(conn.execute("SELECT vendor_key, display_name FROM vendors").fetchall())

    kept = Kept(store, "names", names, topic="", changes="vendors")
    assert kept() == {"acme": "Acme"} and kept.worked == 1
    held = summary_module._COMPUTING[(str(store.path), "names")]
    held.acquire()  # another page working it out
    try:
        with pytest.raises(RuntimeError), store.session(), store._conn() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('unrelated', '1')")
            started = time.monotonic()
            assert kept() == {"acme": "Acme"} and kept.worked == 2  # worked out, not taken from what is kept
            conn.execute("UPDATE vendors SET display_name = 'UNDONE'")
            assert kept() == {"acme": "UNDONE"} and kept.worked == 3
            assert time.monotonic() - started < 1  # no wait for the lock
            raise RuntimeError("undone")
    finally:
        held.release()
    assert _rows(store, "vendors")[0][1] == "Acme"
    with sqlite3.connect(store.path) as conn:  # another worker's change: the count is again the undone write's
        conn.execute("UPDATE vendors SET notes = 'a note'")
    assert kept() == {"acme": "Acme"} and kept.worked == 4
    assert kept() == {"acme": "Acme"} and kept.worked == 4


def test_a_page_waits_for_another_working_the_same_answer_out(tmp_path):
    """One works it out; another asking meanwhile waits and takes it (not both reading everything at once)."""
    store = Store(tmp_path / "ap.db")
    started, go = threading.Event(), threading.Event()

    def slow():
        started.set()
        go.wait(5)
        return ["worked out"]

    kept = Kept(store, "slow", slow, topic="", changes="vendors")
    first = threading.Thread(target=kept)
    first.start()
    started.wait(5)
    second = threading.Thread(target=kept)
    second.start()
    time.sleep(0.2)
    go.set()
    first.join(5)
    second.join(5)
    assert kept.worked == 1 and kept() == ["worked out"] and kept.worked == 1


def test_what_an_answer_is_for_is_in_its_stamp_and_only_the_latest_is_kept(tmp_path):
    """``asked``: another question works it out again, and replaces the one kept under that name."""
    store = Store(tmp_path / "ap.db")
    kept = Kept(store, "plain", lambda: object(), topic="")
    one = store.summary_memo("asked", lambda: ["one"], topic="", asked=(1,))
    assert store.summary_memo("asked", lambda: ["other"], topic="", asked=(1,)) is one
    two = store.summary_memo("asked", lambda: ["two"], topic="", asked=(2,))
    assert two == ["two"] and store.summary_memo("asked", lambda: ["again"], topic="", asked=(1,)) == ["again"]
    assert kept() is kept()  # (no topic, no count: only the database's id)


def test_a_restore_gives_the_database_a_new_summary_id_so_nothing_kept_from_before_is_taken(tmp_path):
    """After a restore the counts are the backup's: a later change brings one back to a number kept before the
    restore, of other rows. The new ``summary_id`` tells them apart."""
    store = Store(tmp_path / "ap.db")
    with sqlite3.connect(store.path) as conn:
        conn.execute("INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('acme', 'Acme', ?)", (NOW,))
    before = store.get_setting("summary_id")
    backup = store.backup_now("manual")
    kept = Kept(store, "vendors", lambda: _rows(store, "vendors"), topic="", changes="vendors")
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE vendors SET display_name = 'Acme after'")
    assert kept()[0][1] == "Acme after"
    store.restore_from(backup)
    assert store.get_setting("summary_id") not in ("", before)
    with sqlite3.connect(store.path) as conn:  # the count is again the one kept before the restore
        conn.execute("UPDATE vendors SET display_name = 'Acme restored'")
    assert kept()[0][1] == "Acme restored"


# --- The answers kept on these counts (D-162): each filled, its data changed through another Store or plain SQL, then
# the new answer ------------------------------------------------------------------------------------------------------


def _plain(store: Store, sql: str, *args) -> None:
    with sqlite3.connect(store.path) as conn:
        conn.execute(sql, args)


def test_every_vendors_supplier_record_and_the_drawn_vendors_field_figures_follow_their_outcomes(tmp_path):
    """Supplier learning and Settings → Automation: every vendor's record (``all_supplier_stats`` without fields) and
    the drawn vendors' field figures (``supplier_fields``), kept on ``changes/suppliers``, per window and vendors."""
    from dataclasses import replace

    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    rows = [
        {"field": "invoice_number", "ai_value": "1", "final_value": "1", "correct": True},
        {"field": "invoice_date", "ai_value": "2026-01-01", "final_value": "2026-01-02", "correct": False},
    ]
    other.record_outcomes("name:acme", 1, rows, display_name="Acme", at=NOW)
    keys = ["name:acme", "name:beta"]

    def kept(window: int = 200):
        return store.all_supplier_stats(keys, window, fields=False), store.supplier_fields(keys, window)

    def fresh(window: int = 200):
        each = {key: store.supplier_stats(key, window) for key in keys}
        return {key: replace(s, fields={}) for key, s in each.items()}, {key: s.fields for key, s in each.items()}

    assert kept() == fresh() and kept()[0]["name:beta"].invoices == 0
    assert kept()[0] is kept()[0] and kept()[1] is kept()[1]  # reused while nothing changed
    other.record_outcomes("name:beta", 2, rows, display_name="Beta", at=NOW)
    assert kept() == fresh() and kept()[0]["name:beta"].invoices == 1
    _plain(store, "UPDATE supplier_outcomes SET correct = 1 WHERE supplier_key = 'name:beta'")
    assert kept() == fresh() and kept()[1]["name:beta"]["invoice_date"].correct == 1
    _plain(store, "DELETE FROM supplier_outcomes WHERE supplier_key = 'name:acme'")
    assert kept() == fresh() and kept()[0]["name:acme"].invoices == 0
    assert kept(1) == fresh(1) and store.supplier_fields(keys[:1]) == {"name:acme": {}}


def test_the_readers_scorecard_follows_their_outcomes(tmp_path):
    """Settings → Reading and Learning → Readers: the readers' scorecard, kept on ``changes/readers``."""
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")

    def read(correct: bool) -> list[dict]:
        return [{"reader": "rules", "field": "invoice_number", "read_value": "1", "final_value": "1",
                 "correct": correct}]  # fmt: skip

    def fields() -> list[tuple]:
        card = store.reader_scorecard()
        assert card == store._reader_scorecard()
        return [(r["reader"], r["fields"], r["agreed"]) for r in card]

    other.record_reader_outcomes(1, read(True), at=NOW)
    assert fields() == [("rules", 1, 1)]
    other.record_reader_outcomes(2, read(False), at=NOW)
    assert fields() == [("rules", 2, 1)]
    _plain(store, "UPDATE reader_outcomes SET correct = 1")
    assert fields() == [("rules", 2, 2)]
    _plain(store, "DELETE FROM reader_outcomes WHERE invoice_id = 1")
    assert fields() == [("rules", 1, 1)]
    store.reader_scorecard()[0]["fields"] = 99  # (each caller has its own copy)
    assert fields() == [("rules", 1, 1)]


def _po_line(n: int, received: float) -> dict:
    return {"po_number": "PO-1", "vendor_name": "Acme Supply", "line_number": n, "description": f"Bolts size {n}",
            "quantity": 10.0, "unit_price": 2.0, "amount": 20.0, "received_quantity": received, "gl_code": "6010",
            "cost_center": "", "status": "open", "amount_only": False, "open_amount": None}  # fmt: skip


def test_month_ends_received_not_invoiced_lines_follow_the_pos_and_the_invoices_billing_them(tmp_path):
    """Month-end's received, not invoiced PO lines, kept per period end on ``changes/purchase_orders`` and the
    summary's generation (an invoice billing them)."""
    from ap_coder.erp import accruals

    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    other.import_purchase_orders([_po_line(1, 4.0), _po_line(2, 1.0)])
    september, october = dt.date(2026, 9, 30), dt.date(2026, 10, 31)

    def received(end: dt.date = october) -> list[tuple]:
        found = [a for a in accruals.build(store, end) if a.source == accruals.RECEIVED]
        assert found == accruals._received_not_invoiced(store, end)
        return [(a.reference, a.amount) for a in found]

    assert received() == [("PO-1 line 1", 8.0), ("PO-1 line 2", 2.0)]
    _plain(store, "UPDATE po_lines SET received_quantity = 3 WHERE line_number = 2")
    assert received() == [("PO-1 line 1", 8.0), ("PO-1 line 2", 6.0)]
    line = {"line_number": 1, "description": "Bolts size 1", "quantity": 4, "unit_price": 2.0, "amount": 8.0}
    bill = {"vendor_name": "Acme Supply", "invoice_number": "B-1", "po_number": "PO-1", "invoice_date": "2026-10-03",
            "currency": "CAD", "line_items": [line], "subtotal": 8.0, "grand_total": 8.4}  # fmt: skip
    other.add_invoice(tmp_path / "b.pdf", bill, {})  # (the summary's generation)
    assert received() == [("PO-1 line 2", 6.0)]
    assert received(september) == [("PO-1 line 1", 8.0), ("PO-1 line 2", 6.0)]  # (billed after it)
    _plain(store, "DELETE FROM po_lines WHERE line_number = 2")
    assert received() == []


def test_the_duplicate_audits_pairs_follow_the_invoices_and_the_erp_register(tmp_path, ground_truth):
    """The duplicate payment audit's pairs (its page and its CSV), kept on the summary's generation (the approved
    invoices) and ``changes/register`` (the ERP's invoice register)."""
    from ap_coder.erp import dupaudit

    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    first, second = (_invoice(other, tmp_path, ground_truth, name) for name in ("A-100", "B-100"))
    other.approve_invoice(first, {**ground_truth, "invoice_number": "A-100"}, "Jordan Lee")

    def pairs() -> list[tuple]:
        found = dupaudit.find(store)
        assert found == dupaudit._find(store)
        return [(p.reason, p.first.source, p.second.source) for p in found]

    assert pairs() == []
    other.approve_invoice(second, {**ground_truth, "invoice_number": "A-100"}, "Jordan Lee")  # approved twice
    assert pairs() == [(dupaudit.EXACT, "AP Coder", "AP Coder")]
    _plain(store, "UPDATE invoices SET status = 'review' WHERE id = ?", second)
    assert pairs() == []
    row = {"vendor_name": ground_truth["vendor_name"], "invoice_number": "A-100",
           "invoice_date": ground_truth["invoice_date"], "total": ground_truth["grand_total"]}  # fmt: skip
    other.import_erp_register([row])
    assert [p[0] for p in pairs()] == [dupaudit.EXACT]  # (not exported: the ERP's row is another)
    _plain(store, "UPDATE erp_invoices SET copies = 2")
    assert [p[0] for p in pairs()] == [dupaudit.EXACT, dupaudit.EXACT]  # (and the ERP's twice)
    _plain(store, "DELETE FROM erp_invoices")
    assert pairs() == []
