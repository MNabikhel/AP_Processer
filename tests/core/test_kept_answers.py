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
    held = summary_module._COMPUTING[(str(store.path), "names", hash(repr(None)))]
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


def test_pages_asking_one_name_for_different_things_work_them_out_at_once(tmp_path, monkeypatch):
    """Month-end for three period ends asked together (one name, three ``asked``) was worked out one after the other:
    each page waited for another's work, whose answer it could not use. Only the same ask waits; the locks of asks
    no page is working out are let go past ``COMPUTING_KEPT``."""
    monkeypatch.setattr(summary_module, "_COMPUTING", {})
    store = Store(tmp_path / "ap.db")
    started = {n: threading.Event() for n in (1, 2, 3)}
    go = threading.Event()

    def month_end(n: int) -> int:
        started[n].set()
        go.wait(5)
        return n

    def ask(n: int) -> None:
        store.summary_memo("received_not_invoiced", lambda: month_end(n), changes="purchase_orders", asked=n)

    threads = [threading.Thread(target=ask, args=(n,)) for n in started]
    for thread in threads:
        thread.start()
    together = all(event.wait(3) for event in started.values())
    go.set()
    for thread in threads:
        thread.join(5)
    assert together
    for n in range(2 * summary_module.COMPUTING_KEPT):
        store.summary_memo("received_not_invoiced", lambda: n, changes="purchase_orders", asked=n)
    assert len(summary_module._COMPUTING) <= summary_module.COMPUTING_KEPT + 1


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


# --- Each vendor's invoices (``Store.vendor_invoices``, D-160) --------------------------------------------------------


def _vendor_reads(store: Store, monkeypatch) -> list[str]:
    """Each vendor whose invoices are read from the database from now on."""
    read: list[str] = []
    real = type(store)._vendor_invoices
    monkeypatch.setattr(type(store), "_vendor_invoices", lambda self, key: read.append(key) or real(self, key))
    return read


def test_each_vendors_invoices_are_kept_until_its_count_moves_whoever_changed_it(tmp_path, ground_truth, monkeypatch):
    """Every check read all of its vendor's invoices twice (220 ms for a vendor of 7,800 on the 100k database): they
    are kept per vendor while ``summary_id`` and its ``changes/vendor/<key>`` hold, a change by another worker or plain
    SQL included; another vendor's change, or a check's confidence, reads nothing again."""
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    acme = _invoice(store, tmp_path, ground_truth, "V-1", "Acme Supply")
    beta = _invoice(store, tmp_path, ground_truth, "V-2", "Beta Ltd")
    key = store.get_invoice(acme)["vendor_key"]
    read = _vendor_reads(store, monkeypatch)
    first = store.vendor_invoices(key)
    assert [r["id"] for r in first] == [acme] and read == [key]
    first[0]["status"] = "changed by its caller"  # each caller's own rows
    assert store.vendor_invoices(key)[0]["status"] == "review" and read == [key]
    assert "adjusted_confidence" not in first[0] and "requires_review" not in first[0]  # (read by nothing)
    store.refresh_confidence(acme, 0.12, True)
    other.park_invoice(beta, "Ann", "waiting")  # another vendor's
    assert other.vendor_invoices(key)[0]["status"] == "review" and read == [key]  # (one process: one copy kept)
    other.park_invoice(acme, "Ann", "waiting for the PO")  # another worker's change
    assert store.vendor_invoices(key)[0]["status"] == "parked" and read == [key, key]
    with sqlite3.connect(store.path) as conn:  # plain SQL
        conn.execute("UPDATE invoices SET invoice_date = '2026-02-02' WHERE id = ?", (acme,))
    assert store.vendor_invoices(key)[0]["invoice_date"] == "2026-02-02" and len(read) == 3
    backup = store.backup_now("manual")
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET invoice_date = '2026-03-03' WHERE id = ?", (acme,))
    assert store.vendor_invoices(key)[0]["invoice_date"] == "2026-03-03" and len(read) == 4
    store.restore_from(backup)  # the backup's count, under a new summary_id
    assert store.vendor_invoices(key)[0]["invoice_date"] == "2026-02-02" and len(read) == 5


def test_each_vendors_invoices_read_in_a_write_that_is_undone_are_not_kept(tmp_path, ground_truth, monkeypatch):
    """Inside a write under way the vendor's count includes that write, and the rows read show it: kept, they would be
    served once a later change brought the count back to that number, though the write was undone."""
    store = Store(tmp_path / "ap.db")
    acme = _invoice(store, tmp_path, ground_truth, "V-1", "Acme Supply")
    key = store.get_invoice(acme)["vendor_key"]
    read = _vendor_reads(store, monkeypatch)
    with pytest.raises(RuntimeError), store.session(), store._conn() as conn:
        conn.execute("UPDATE invoices SET status = 'rejected' WHERE id = ?", (acme,))
        assert store.vendor_invoices(key)[0]["status"] == "rejected"
        assert store.vendor_invoices(key)[0]["status"] == "rejected" and len(read) == 2  # afresh each time
        raise RuntimeError("undone")
    with sqlite3.connect(store.path) as conn:  # another worker's change: the count is again the undone write's
        conn.execute("UPDATE invoices SET invoice_date = '2026-02-02' WHERE id = ?", (acme,))
    found = store.vendor_invoices(key)[0]
    assert (found["status"], found["invoice_date"]) == ("review", "2026-02-02") and len(read) == 3
    assert store.vendor_invoices(key)[0]["status"] == "review" and len(read) == 3  # kept now


def test_vendors_asked_with_no_invoices_are_not_kept_for_ever(tmp_path, ground_truth, monkeypatch):
    """A name typed on the review screen is asked for, most with no invoices: at most ``VENDOR_KEYS_KEPT`` vendors are
    kept (B-1724's idea: 20,000 names kept 27.6 MB, and each new one summed them all), and ``VENDOR_ROWS_KEPT`` rows in
    all, counted as they are kept; one vendor of more rows than half that is not kept."""
    from ap_coder.core.store import vendors as vendors_module

    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS", type(vendors_module._VENDOR_ROWS)())
    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS_HELD", 0)
    monkeypatch.setattr(vendors_module, "VENDOR_KEYS_KEPT", 50)
    monkeypatch.setattr(vendors_module, "VENDOR_ROWS_KEPT", 4)
    store = Store(tmp_path / "ap.db")

    def vendor(name: str, *numbers: str) -> str:
        ids = [_invoice(store, tmp_path, ground_truth, number, name) for number in numbers]
        return store.get_invoice(ids[0])["vendor_key"]

    acme, beta, gamma = vendor("Acme Supply", "A-1", "A-2"), vendor("Beta Ltd", "B-1", "B-2"), vendor("Gamma", "C-1")
    for n in range(200):
        assert store.vendor_invoices(f"nobody {n}") == []
    kept = vendors_module._VENDOR_ROWS
    assert len(kept) == 50 and list(kept)[-1][1] == "nobody 199" and vendors_module._VENDOR_ROWS_HELD == 0
    assert len(store.vendor_invoices(acme)) == 2 and len(store.vendor_invoices(beta)) == 2
    assert vendors_module._VENDOR_ROWS_HELD == 4
    assert len(store.vendor_invoices(gamma)) == 1  # the rows over the cap: the vendor asked longest ago goes
    assert (str(store.path), acme) not in kept and vendors_module._VENDOR_ROWS_HELD == 3
    assert sum(len(rows) for _, rows in kept.values()) == vendors_module._VENDOR_ROWS_HELD and len(kept) <= 50
    _invoice(store, tmp_path, ground_truth, "A-3", "Acme Supply")
    assert len(store.vendor_invoices(acme)) == 3 and (str(store.path), acme) not in kept  # more than half the cap


def test_one_vendor_is_kept_up_to_half_the_rows_kept_so_a_big_one_never_empties_them(tmp_path, monkeypatch):
    """A vendor of nearly ``VENDOR_ROWS_KEPT`` rows, once kept, dropped every other vendor kept, and the next one asked
    dropped it again: each page of either vendor read its invoices afresh. One vendor is kept up to half the cap, the
    other half left to the others."""
    from ap_coder.core.store import vendors as vendors_module

    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS", type(vendors_module._VENDOR_ROWS)())
    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS_HELD", 0)
    monkeypatch.setattr(vendors_module, "VENDOR_ROWS_KEPT", 10)
    store = Store(tmp_path / "ap.db")
    with sqlite3.connect(store.path) as conn:
        for key, count in (("small 1", 1), ("small 2", 2), ("half", 5), ("big", 6)):
            conn.executemany("INSERT INTO invoices (source_path, file_name, status, created_at, vendor_key, "
                             "vendor_name) VALUES ('x', 'x', 'review', ?, ?, ?)", [(NOW, key, key.title())] * count)  # fmt: skip
    for key in ("small 1", "small 2", "big"):
        store.vendor_invoices(key)
    kept = vendors_module._VENDOR_ROWS
    assert [key for _, key in kept] == ["small 1", "small 2"] and vendors_module._VENDOR_ROWS_HELD == 3
    assert len(store.vendor_invoices("half")) == 5 and len(store.vendor_invoices("big")) == 6
    assert [key for _, key in kept] == ["small 1", "small 2", "half"] and vendors_module._VENDOR_ROWS_HELD == 8


def test_a_vendor_too_big_to_keep_is_read_once_by_a_check(tmp_path, ground_truth, monkeypatch):
    """A check asks for its vendor's invoices twice (``vendor_findings``, ``expected_tax_numbers``). A vendor of more
    rows than one is kept up to was read by both, 1.1 s each for one of 23,000 invoices: it is read once in a page's
    session (``_session_memo``), again after a change; each caller gets rows of its own."""
    from ap_coder.coding.vendors import expected_tax_numbers, vendor_findings
    from ap_coder.core.schema import InvoiceCoding
    from ap_coder.core.store import vendors as vendors_module

    monkeypatch.setattr(vendors_module, "VENDOR_ROWS_KEPT", 2)  # this vendor has 3
    store = Store(tmp_path / "ap.db")
    ids = [_invoice(store, tmp_path, ground_truth, f"N-{n}") for n in range(3)]
    key = store.get_invoice(ids[0])["vendor_key"]
    read = _vendor_reads(store, monkeypatch)
    coding = InvoiceCoding.model_validate({**ground_truth, "invoice_number": "N-9"})
    with store.session():  # one page
        vendor_findings(coding, store)
        expected_tax_numbers(coding, store)
        assert read == [key]
        store.vendor_invoices(key)[0]["status"] = "changed by its caller"
        assert store.vendor_invoices(key)[0]["status"] == "review" and read == [key]
        store.park_invoice(ids[2], "Ann", "waiting for the PO")  # a change: read again
        assert store.vendor_invoices(key)[0]["status"] == "parked" and read == [key, key]
    assert (str(store.path), key) not in vendors_module._VENDOR_ROWS
    assert len(store.vendor_invoices(key)) == 3 and len(read) == 3  # (outside a page's session: each time)


# --- The uses (D-159's "What is kept"): each filled, changed through another Store or plain SQL, asked again -------


def _master(conn, key: str, erp_id: str, gst: str, in_master: int = 1) -> None:
    conn.execute("INSERT INTO vendors (vendor_key, display_name, erp_id, expected_gst, in_master, updated_at) VALUES "
                 "(?, ?, ?, ?, ?, ?)", (key, key.title(), erp_id, gst, in_master, NOW))  # fmt: skip


def test_the_vendor_masters_ids_follow_a_change_made_through_another_store_or_plain_sql(tmp_path):
    """``vendor_ids``, kept on ``changes/vendors``: an ERP ID changed, a vendor added or deleted by another Store or
    plain SQL is in the next answer; while nothing changed it is not worked out again."""
    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    with sqlite3.connect(store.path) as conn:
        _master(conn, "acme", "S1", "123456789 RT0001")
    first = store.vendor_ids()
    assert first == {"acme": "S1", "gst:123456789rt0001": "S1", "bnx:123456789": "S1"}
    assert store.vendor_ids() is first  # kept
    with other._conn() as conn:
        conn.execute("UPDATE vendors SET erp_id = 'S2' WHERE vendor_key = 'acme'")
    assert store.vendor_ids()["acme"] == "S2"
    with sqlite3.connect(store.path) as conn:
        _master(conn, "beta", "B1", "987654321")
    assert store.vendor_ids() == store._vendor_ids() and store.vendor_ids()["bn:987654321"] == "B1"
    with sqlite3.connect(store.path) as conn:
        conn.execute("DELETE FROM vendors WHERE vendor_key = 'acme'")
    assert store.vendor_ids() == {"beta": "B1", "gst:987654321": "B1", "bn:987654321": "B1"}


def test_the_masters_vendors_found_by_a_tax_number_are_the_whole_lists_and_follow_a_change(tmp_path):
    """``master_by_tax_number``: the master's vendors ``same_tax_number`` matches, in the list's order, as reading
    the whole master gives them (a Business Number for a full account and back; two full accounts only the same). Its
    numbers are kept on ``changes/vendors``: a number changed through another Store, a vendor put in the master by
    plain SQL, are found by what they are now."""
    from ap_coder.coding.vendors import norm_tax_number, same_tax_number

    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    with sqlite3.connect(store.path) as conn:
        for key, gst, in_master in (
            ("acme", "123456789 RT0001", 1),
            ("acme #2", "123456789", 1),
            ("beta", "123456789RT0002", 1),
            ("gamma", "987654321RT0001", 0),
            ("delta", "N/A", 1),
            ("epsilon", "", 1),
            ("zeta", "1234567890TQ0001", 1),
        ):
            _master(conn, key, key[:2].upper(), gst, in_master)

    def whole(number):
        return [v for v in store.master_vendors() if same_tax_number(v["expected_gst"], norm_tax_number(number))]

    asked = ("123456789RT0001", "123 456 789", "123456789 rt 0002", "123456789RT0003", "987654321RT0001", "n/a",
             "1234567890TQ0001", "555555555", "")  # fmt: skip
    for number in asked:
        assert store.master_by_tax_number(number) == whole(number), number
    assert [v["vendor_key"] for v in store.master_by_tax_number("123456789")] == ["acme", "acme #2", "beta"]
    with other._conn() as conn:
        conn.execute("UPDATE vendors SET expected_gst = '555555555 RT0001' WHERE vendor_key = 'beta'")
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE vendors SET in_master = 1 WHERE vendor_key = 'gamma'")
    for number in asked:
        assert store.master_by_tax_number(number) == whole(number), number
    assert [v["vendor_key"] for v in store.master_by_tax_number("555555555")] == ["beta"]
    assert [v["vendor_key"] for v in store.master_by_tax_number("987654321RT0001")] == ["gamma"]


def test_the_held_vendors_approved_numbers_follow_an_approval_made_elsewhere_and_what_is_asked(tmp_path, ground_truth):
    """``approved_numbers``, kept on ``changes/approved`` and the vendors asked: an approval through another Store,
    an approved coding changed by plain SQL, are in the next answer; other vendors asked get their own; each caller
    gets lists of its own."""
    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    invoice = _invoice(store, tmp_path, ground_truth, "H-1", "Acme Supply")
    key = store.get_invoice(invoice)["vendor_key"]
    assert store.approved_numbers([key]) == {}
    coding = {**ground_truth, "vendor_name": "Acme Supply", "gst_hst_registration_number": "123456789RT0001",
              "remit_bank_account": "004-12345-1234567"}  # fmt: skip
    other.approve_invoice(invoice, coding, "Jordan Lee")
    assert store.approved_numbers([key]) == {key: [("123456789RT0001", "004-12345-1234567")]}
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET final_output = json_set(final_output, '$.remit_bank_account', '999') "
                     "WHERE id = ?", (invoice,))  # fmt: skip
    assert store.approved_numbers([key]) == {key: [("123456789RT0001", "999")]}
    assert store.approved_numbers(["beta"]) == {} and store.approved_numbers(["beta", key])[key]
    mine = store.approved_numbers([key])
    mine[key].append(("changed", "by the caller"))
    assert store.approved_numbers([key]) == {key: [("123456789RT0001", "999")]}


def test_a_lines_suggestions_follow_the_lessons_and_the_gl_accounts_changed_elsewhere_and_are_the_callers_own(
    tmp_path,
):
    """``suggest_gl``'s answers, kept with the memory and the reference data they came from (``_Memory.said``): a
    lesson added through another Store is a new memory, a GL account's description changed by plain SQL new
    reference data (the accounts named the same: the same memory); each caller gets copies."""
    from ap_coder.ai.suggest import suggest_gl

    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    accounts = [{"code": "6000", "description": "Office supplies", "category": "Opex"},
                {"code": "6230", "description": "Cleaning supplies", "category": "Opex"}]  # fmt: skip
    store.save_accounts("gl_accounts", accounts)
    lesson = {"vendor_name": "Acme", "description": "Nitrile gloves", "gl_code": "6000", "amount": 1.0,
              "date": "2025-01-01"}  # fmt: skip
    store.import_history([lesson])

    def asked(line: str) -> list[tuple[str, str]]:
        found = suggest_gl(line, "Acme", store.learning_rows("Acme"), store.reference_data())
        return [(s.gl_code, s.reasons[0]) for s in found]

    gloves = asked("Nitrile gloves")
    assert gloves == [("6000", "coded this way 1 time for similar lines from this vendor")]
    mine = suggest_gl("Nitrile gloves", "Acme", store.learning_rows("Acme"), store.reference_data())
    mine[0].reasons.insert(0, "changed by the caller")
    mine[0].gl_code = "9999"
    assert asked("Nitrile gloves") == gloves and asked("Disinfectant 4 L") == []
    other.import_history([{**lesson, "gl_code": "6230", "date": "2025-02-01"}])  # AP coded it otherwise since
    assert asked("Nitrile gloves")[0] == ("6230", "coded this way 1 time for similar lines from this vendor")
    assert asked("Disinfectant 4 L") == []  # (asked of this memory, before the account's description changes)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE gl_accounts SET description = 'Cleaning supplies, disinfectant' WHERE code = '6230'")
    assert asked("Disinfectant 4 L") == [("6230", "account description mentions “disinfectant”")]


def test_the_queues_checks_follow_a_change_made_elsewhere_the_checks_stamp_and_what_is_asked(
    tmp_path, ground_truth, monkeypatch
):
    """``bulk.queue_checks``, kept on the summary's ``queue`` topic, the checks stamp, laptop or server and the
    invoices asked: a finding stored by plain SQL, an invoice parked through another Store, a change of settings
    (their checks then made against others: counted) are in the next answer; fewer than ``KEEP_FROM`` invoices are
    worked out each time; each caller gets rows and lists of its own, the findings in them read-only."""
    from ap_coder.coding import bulk

    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    checked = {"requires_review": True, "checks_stamp": store.checks_stamp(), "issues": []}
    ids = []
    for name in ("Q-1", "Q-2", "Q-3"):
        (tmp_path / f"{name}.pdf").write_bytes(name.encode())
        ids.append(store.add_invoice(tmp_path / f"{name}.pdf", {**ground_truth, "invoice_number": name}, checked))
    worked = []
    real = bulk._queue_checks
    monkeypatch.setattr(bulk, "_queue_checks", lambda *a: worked.append(1) or real(*a))
    monkeypatch.setattr(bulk, "KEEP_FROM", 3)

    def asked(which=tuple(ids), recheck=False):
        return bulk.queue_checks(store, list(which), recheck=recheck)

    first = asked()
    assert sorted(first[0]) == ids and first[1] == 0 and all(c["found"] == [] for c in first[0].values())
    assert asked() == first and len(worked) == 1  # kept
    first[0][ids[0]]["found"].append({"severity": "error", "code": "CHANGED_BY_THE_CALLER"})
    first[0][ids[0]]["q_pick"] = "changed by the caller"
    assert asked()[0][ids[0]] == {**first[0][ids[0]], "found": [], "q_pick": ""} and len(worked) == 1  # copies
    found = '[{"severity": "error", "code": "TOTAL_MISMATCH", "message": "Totals differ"}]'
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET validation = json_set(validation, '$.issues', json(?)) WHERE id = ?",
                     (found, ids[0]))  # fmt: skip
    mismatch = asked()[0][ids[0]]["found"]
    assert mismatch == [{"severity": "error", "code": "TOTAL_MISMATCH"}]
    with pytest.raises(TypeError):
        mismatch[0]["code"] = "CHANGED_BY_THE_CALLER"
    other.park_invoice(ids[1], "Jordan Lee", "waiting for the buyer")
    assert sorted(asked()[0]) == [ids[0], ids[2]] and len(worked) == 3
    with sqlite3.connect(store.path) as conn:  # a change of settings: their checks were made against others
        conn.execute("INSERT INTO settings (key, value) VALUES ('default_terms_days', '45')")
    assert asked()[1] == 2 and len(worked) == 4
    assert asked(recheck=True)[1] == 2 and len(worked) == 5  # the laptop's (no GL accounts: nothing checked again)
    assert asked(recheck=True)[1] == 2 and asked()[1] == 2 and len(worked) == 6
    assert sorted(asked([ids[2], ids[0], ids[1]])[0]) == [ids[0], ids[2]] and len(worked) == 7  # asked otherwise
    asked(ids[:2])
    asked(ids[:2])
    assert len(worked) == 9  # fewer than KEEP_FROM: worked out each time
    key = summary_module.TOPICS["queue"]  # one the summary could not work out: each change to it counts
    row = {"status": "review", "broken": summary_module.BIT["queue"], **dict.fromkeys(summary_module.QUEUE_READ[1:])}
    assert key(row) != key(row) and key({**row, "broken": 0}) == key({**row, "broken": 0})


def test_the_queues_lists_follow_a_change_made_elsewhere_and_are_the_callers_own(tmp_path, ground_truth):
    """``list_invoices`` of the queue's lists (review, parked, pending), kept on the summary's ``listed`` topic: a
    confidence worked out through another Store, an invoice number changed by plain SQL, a park, are in the next
    lists, as reading them afresh gives them; each caller gets rows of its own."""
    from ap_coder.core.store import PARKED, PENDING, REVIEW

    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    a, b, c = (_invoice(store, tmp_path, ground_truth, name) for name in ("L-1", "L-2", "L-3"))

    def fresh(*statuses):
        listed, marks = ", ".join(summary_module.LISTED_COLUMNS), ", ".join("?" * len(statuses))
        return store.summary_fetch(f"SELECT {listed} FROM invoice_summary WHERE status IN ({marks}) ORDER BY "
                                   "requires_review DESC, adjusted_confidence ASC, id ASC", statuses)  # fmt: skip

    def ids(*statuses):
        listed = store.list_invoices(statuses=statuses)
        assert listed == fresh(*statuses), statuses
        return [r["id"] for r in listed]

    first = store.list_invoices(REVIEW)
    assert [r["id"] for r in first] == [a, b, c] and first == fresh(REVIEW)
    first[0]["invoice_number"] = "changed by the caller"
    first.pop()
    assert store.list_invoices(REVIEW) == fresh(REVIEW)
    other.refresh_confidence(b, 0.12, True)
    assert ids(REVIEW) == [a, c, b]
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET invoice_number = 'L-9' WHERE id = ?", (c,))
    assert store.list_invoices(REVIEW)[1]["invoice_number"] == "L-9" and ids(REVIEW) == [a, c, b]
    other.park_invoice(a, "Jordan Lee", "waiting for the buyer")
    assert ids(REVIEW) == [c, b] and ids(PARKED) == [a] and ids(REVIEW, PARKED, PENDING) == [a, c, b]


def test_previous_and_next_step_through_the_kept_queue_as_another_store_or_plain_sql_changes_it(tmp_path, ground_truth):
    """The invoice page's Previous and Next (``navigation``) read the queue's kept list: an invoice parked through
    another Store, a confidence changed by plain SQL, move them at once."""
    from ap_coder.web.pages.invoice import navigation

    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    a, b, c = (_invoice(store, tmp_path, ground_truth, name) for name in ("N-1", "N-2", "N-3"))
    assert navigation(store, a, {})["ids"] == [a, b, c] and navigation(store, a, {})["next"] == f"/review/{b}"
    other.park_invoice(b, "Jordan Lee", "waiting for the buyer")
    assert navigation(store, a, {})["next"] == f"/review/{c}"
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET adjusted_confidence = 0.5 WHERE id = ?", (a,))
        conn.execute("UPDATE invoices SET adjusted_confidence = 0.05 WHERE id = ?", (c,))
    assert navigation(store, a, {})["ids"] == [c, a] and navigation(store, a, {})["prev"] == f"/review/{c}"


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


def test_spends_figures_and_currencies_follow_the_approved_invoices_and_what_is_asked(tmp_path, ground_truth):
    """The Spend page's figures and currencies, kept on the summary's generation, per period, currency, GL categories
    and rates (copies: the caller's own changed after is another question)."""
    from ap_coder.erp import spend

    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    start, end = dt.date(2000, 1, 1), dt.date(2099, 12, 31)
    cad, usd = (_invoice(other, tmp_path, ground_truth, name) for name in ("S-1", "S-2"))
    other.approve_invoice(cad, {**ground_truth, "invoice_number": "S-1", "currency": "CAD"}, "Jordan Lee")

    def seen(currency: str = "CAD", category: dict | None = None, rates: dict | None = None, first=start) -> tuple:
        category = {} if category is None else category
        found = spend.figures(store, first, end, currency, category, rates)
        assert found == spend.read_figures(store, first, end, currency, category, rates)
        shown = spend.spend_currencies(store, first, end)
        assert shown == spend._spend_currencies(store, first, end)
        return found["invoices"], found["total"], sorted({m["category"] for m in found["by_month"]}), shown

    invoices, total, categories, shown = seen()
    assert invoices == 1 and total > 0 and categories == ["Uncategorised"] and shown == ["CAD"]
    assert seen("USD")[:2] == (0, 0.0)
    other.approve_invoice(usd, {**ground_truth, "invoice_number": "S-2", "currency": "USD"}, "Jordan Lee")
    assert seen("USD") == (1, total, ["Uncategorised"], ["CAD", "USD"])
    assert seen(first=end) == (0, 0.0, [], [])
    rates = {"CAD": 1.0, "USD": 1.5}
    assert seen(rates=rates)[:2] == (2, round(total * 2.5, 2))
    rates["USD"] = 2.0
    assert seen(rates=rates)[:2] == (2, round(total * 3, 2))
    _plain(store, "UPDATE invoices SET status = 'review' WHERE id = ?", usd)
    assert seen(rates=rates) == (1, total, ["Uncategorised"], ["CAD"])
    category = {gl: "Office" for gl, _, _ in spend.read_figures(store, start, end, "CAD", {})["by"]["gl_code"]}
    assert seen(category=category)[2] == ["Office"]
    category.update((gl, "Travel") for gl in list(category))
    assert seen(category=category)[2] == ["Travel"]


def test_each_vendors_lesson_counts_follow_the_coding_memory_and_its_vendor_list_row_is_read_afresh(
    tmp_path, ground_truth
):
    """Vendors, Statements and the JD Edwards worksheet (``vendor_summaries``): each vendor's lesson counts, kept on
    ``changes/lessons``; its row of the vendor list, read by its key each time."""
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    invoice = _invoice(other, tmp_path, ground_truth, "L-1")
    key = store.get_invoice(invoice)["vendor_key"]

    def row() -> tuple:
        (found,) = [r for r in store.vendor_summaries() if r["vendor_key"] == key]
        return found["lessons"], found["accuracy"], found["status"], found["notes"]

    assert row() == (0, None, "active", "")
    other.approve_invoice(invoice, {**ground_truth, "invoice_number": "L-1"}, "Jordan Lee")
    lines = len(ground_truth["line_items"])
    assert row() == (lines, 1.0, "active", "")
    _plain(store, "UPDATE feedback SET outcome = 'corrected' WHERE rowid = (SELECT MIN(rowid) FROM feedback)")
    assert row() == (lines, (lines - 1) / lines, "active", "")
    _plain(store, "DELETE FROM feedback")
    assert row() == (0, None, "active", "")
    other.save_vendor(key, ground_truth["vendor_name"], "on_hold", notes="Call first", actor="Jordan Lee")
    assert row() == (0, None, "on_hold", "Call first")


def test_the_lessons_tabs_rows_follow_the_lessons_and_each_approvals_word_on_them(tmp_path, ground_truth):
    """Learning → Lessons: its rows (each lesson, whether it reaches the AI, how it did since), kept on
    ``changes/situations`` (``lessons``, ``lesson_approvals``)."""
    from ap_coder.ai import lessons
    from ap_coder.web.pages import learning

    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    proposed = {**ground_truth, "invoice_number": "ACCT 1"}
    key = lessons.situation(ground_truth["vendor_name"], lessons.INVOICE, "invoice_number")

    def rows() -> list[tuple]:
        found = learning.lessons_context(store)["lessons"]
        assert found == learning.lesson_rows(store)
        return [(r["situation"], r["corrected"], r["reaches"], r["effect"]["before"]["corrected"]) for r in found]

    assert rows() == []
    lessons.record(other, 1, proposed, {**ground_truth, "invoice_number": "1-2026"})
    assert rows() == [(key, 1, False, 1)]  # (once: it waits)
    lessons.record(other, 2, proposed, {**ground_truth, "invoice_number": "1-2026"})
    assert rows() == [(key, 2, True, 2)]  # (twice to the same value)
    _plain(store, "UPDATE lesson_approvals SET corrected = 0 WHERE invoice_id = 2")
    assert rows() == [(key, 2, True, 1)]
    _plain(store, "UPDATE lessons SET retired = 1")
    assert rows() == [(key, 2, False, 1)]
    _plain(store, "DELETE FROM lessons")
    assert rows() == []
