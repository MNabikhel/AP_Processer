"""What a process keeps between requests (D-159, ``Store.summary_memo``): a kept answer holds only while a stamp read
from the database before the work says nothing it depends on changed, whichever connection changed it (another
Store, plain SQL); the counts of changes the triggers keep (``changes/<kind>``); an answer worked out inside a write
that is undone; a restore; the answers kept on them (D-162)."""

from __future__ import annotations

import datetime as dt
import json
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
        store.summary_memo("received_not_invoiced", lambda n=n: n, changes="purchase_orders", asked=n)
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


def test_an_answer_shown_only_settles_for_a_few_seconds_after_a_change_but_never_past_a_restore_or_another_ask(
    tmp_path, monkeypatch
):
    """``settle`` (D-164): under a steady stream of changes (100 people approving) an answer kept for a page that
    only shows it (a count, a report) is worked out again at most every ``settle`` seconds, not at each change: it is
    at most that old. Another question, a restore (a new ``summary_id``) or a write under way is never settled."""
    clock = [1000.0]
    monkeypatch.setattr(summary_module, "monotonic", lambda: clock[0])
    store = Store(tmp_path / "ap.db")
    kept = Kept(store, "vendors", lambda: _rows(store, "vendors"), topic="", changes="vendors", settle=5)

    def add(n: int) -> None:
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES (?, 'V', ?)", (f"v{n}", NOW)
            )

    assert kept() == [] and kept.worked == 1
    add(1)
    clock[0] += 4.9
    assert kept() == [] and kept.worked == 1  # changed, but worked out under 5 s ago: settled
    clock[0] += 0.2
    assert len(kept()) == 1 and kept.worked == 2  # 5 s on: worked out again
    add(2)
    assert len(kept()) == 1 and kept.worked == 2
    assert len(store.summary_memo("vendors", lambda: _rows(store, "vendors"), topic="", changes="vendors",
                                  settle=5, asked="other")) == 2  # another question: afresh  # fmt: skip
    plain = Kept(store, "plain", lambda: _rows(store, "vendors"), topic="", changes="vendors")
    assert len(plain()) == 2
    add(3)
    assert len(plain()) == 3  # without ``settle``: every change, as before

    def keys():
        with store._conn() as conn:  # (the session's connection, inside a write)
            return [r[0] for r in conn.execute("SELECT vendor_key FROM vendors ORDER BY rowid")]

    settled = Kept(store, "restored", keys, topic="", changes="vendors", settle=60)
    assert len(settled()) == 3
    backup = store.backup_now("manual")
    add(4)
    store.restore_from(backup)
    assert len(settled()) == 3 and settled.worked == 2  # the restore's new summary_id: never settled
    with pytest.raises(RuntimeError), store.session(), store._conn() as conn:  # a write under way: afresh
        conn.execute("INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('v9', 'V', ?)", (NOW,))
        assert len(settled()) == 4
        raise RuntimeError("undone")
    assert len(settled()) == 3 and settled.worked == 3


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
    """Each vendor whose invoices are read from the database from now on: (its key, the invoices of it read again, or
    None for all of them)."""
    read: list[tuple[str, list[int] | None]] = []
    real = type(store)._vendor_invoices

    def reading(self, key, ids=None, **kw):
        read.append((key, ids))
        return real(self, key, ids, **kw)

    monkeypatch.setattr(type(store), "_vendor_invoices", reading)
    return read


def test_each_vendors_invoices_are_kept_and_only_those_changed_read_again_whoever_changed_them(
    tmp_path, ground_truth, monkeypatch
):
    """Every check read all of its vendor's invoices twice (220 ms for a vendor of 7,800 on the 100k database): they
    are kept per vendor on the database (``summary_id``), and a change by another worker or plain SQL reads again only
    the invoice it changed (the vendors' change log, D-174); another vendor's change, or a check's confidence, reads
    nothing again; a restore reads them all."""
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    acme = _invoice(store, tmp_path, ground_truth, "V-1", "Acme Supply")
    beta = _invoice(store, tmp_path, ground_truth, "V-2", "Beta Ltd")
    key = store.get_invoice(acme)["vendor_key"]
    read = _vendor_reads(store, monkeypatch)
    first = store.vendor_invoices(key)
    assert [r["id"] for r in first] == [acme] and read == [(key, None)]
    first[0]["status"] = "changed by its caller"  # each caller's own rows
    assert store.vendor_invoices(key)[0]["status"] == "review" and read == [(key, None)]
    assert "adjusted_confidence" not in first[0] and "requires_review" not in first[0]  # (read by nothing)
    store.refresh_confidence(acme, 0.12, True)
    other.park_invoice(beta, "Ann", "waiting")  # another vendor's
    assert other.vendor_invoices(key)[0]["status"] == "review" and read == [(key, None)]  # (one process: one copy)
    other.park_invoice(acme, "Ann", "waiting for the PO")  # another worker's change
    assert store.vendor_invoices(key)[0]["status"] == "parked" and read == [(key, None), (key, [acme])]
    with sqlite3.connect(store.path) as conn:  # plain SQL
        conn.execute("UPDATE invoices SET invoice_date = '2026-02-02' WHERE id = ?", (acme,))
    assert store.vendor_invoices(key)[0]["invoice_date"] == "2026-02-02" and read[2:] == [(key, [acme])]
    backup = store.backup_now("manual")
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET invoice_date = '2026-03-03' WHERE id = ?", (acme,))
    assert store.vendor_invoices(key)[0]["invoice_date"] == "2026-03-03" and read[3:] == [(key, [acme])]
    store.restore_from(backup)  # the backup's log, under a new summary_id: all read again
    assert store.vendor_invoices(key)[0]["invoice_date"] == "2026-02-02" and read[4:] == [(key, None)]


def test_each_vendors_invoices_read_in_a_write_that_is_undone_are_not_kept(tmp_path, ground_truth, monkeypatch):
    """Inside a write under way the vendors' change log holds that write, and the rows read show it: kept, they would
    be served once a later change took the undone write's place in the log, though the write was undone."""
    store = Store(tmp_path / "ap.db")
    acme = _invoice(store, tmp_path, ground_truth, "V-1", "Acme Supply")
    key = store.get_invoice(acme)["vendor_key"]
    read = _vendor_reads(store, monkeypatch)
    with pytest.raises(RuntimeError), store.session(), store._conn() as conn:
        conn.execute("UPDATE invoices SET status = 'rejected' WHERE id = ?", (acme,))
        assert store.vendor_invoices(key)[0]["status"] == "rejected"
        assert store.vendor_invoices(key)[0]["status"] == "rejected" and len(read) == 2  # afresh each time
        raise RuntimeError("undone")
    with sqlite3.connect(store.path) as conn:  # another worker's change, in the undone write's place in the log
        conn.execute("UPDATE invoices SET invoice_date = '2026-02-02' WHERE id = ?", (acme,))
    found = store.vendor_invoices(key)[0]
    assert (found["status"], found["invoice_date"]) == ("review", "2026-02-02") and len(read) == 3
    assert store.vendor_invoices(key)[0]["status"] == "review" and len(read) == 3  # kept now


def test_vendors_asked_with_no_invoices_are_not_kept_for_ever(tmp_path, ground_truth, monkeypatch):
    """A name typed on the review screen is asked for, most with no invoices: at most ``VENDOR_KEYS_KEPT`` vendors are
    kept (B-1724's idea: 20,000 names kept 27.6 MB, and each new one summed them all), and ``VENDOR_ROWS_KEPT`` rows in
    all, counted as they are kept; one vendor of more rows than half that is not kept."""
    from ap_coder.core.store import vendors as vendors_module

    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS", vendors_module.Bounded("rows", vendors_module.VENDOR_ROWS_MOST))
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

    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS", vendors_module.Bounded("rows", vendors_module.VENDOR_ROWS_MOST))
    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS_HELD", 0)
    monkeypatch.setattr(vendors_module, "VENDOR_ROWS_KEPT", 10)
    store = Store(tmp_path / "ap.db")
    with sqlite3.connect(store.path) as conn:
        for key, count in (("small 1", 1), ("small 2", 2), ("half", 5), ("big", 6)):
            conn.executemany("INSERT INTO invoices (source_path, file_name, status, created_at, vendor_key, "
                             "vendor_name) VALUES ('x', 'x', 'review', ?, ?, ?)",
                             [(NOW, key, key.title())] * count)  # fmt: skip
    for key in ("small 1", "small 2", "big"):
        store.vendor_invoices(key)
    kept = vendors_module._VENDOR_ROWS
    assert [key for _, key in kept] == ["small 1", "small 2"] and vendors_module._VENDOR_ROWS_HELD == 3
    assert len(store.vendor_invoices("half")) == 5 and len(store.vendor_invoices("big")) == 6
    assert [key for _, key in kept] == ["small 1", "small 2", "half"] and vendors_module._VENDOR_ROWS_HELD == 8


def test_a_vendor_grown_past_half_the_rows_kept_is_no_longer_kept(tmp_path, monkeypatch):
    """A vendor kept at half ``VENDOR_ROWS_KEPT`` that grew past it kept its last rows (up to 25,000: 25 MB in each
    worker) until other vendors pushed them out, counted in the cap and never answered from again (B-1790). They go
    when it is next read."""
    from ap_coder.core.store import vendors as vendors_module

    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS", vendors_module.Bounded("rows", vendors_module.VENDOR_ROWS_MOST))
    monkeypatch.setattr(vendors_module, "_VENDOR_ROWS_HELD", 0)
    monkeypatch.setattr(vendors_module, "VENDOR_ROWS_KEPT", 10)
    store = Store(tmp_path / "ap.db")

    def add(count: int) -> None:
        with sqlite3.connect(store.path) as conn:
            conn.executemany("INSERT INTO invoices (source_path, file_name, status, created_at, vendor_key, "
                             "vendor_name) VALUES ('x', 'x', 'review', ?, 'half', 'Half')",
                             [(NOW,)] * count)  # fmt: skip

    add(5)
    assert len(store.vendor_invoices("half")) == 5 and vendors_module._VENDOR_ROWS_HELD == 5
    add(1)
    assert len(store.vendor_invoices("half")) == 6
    assert (str(store.path), "half") not in vendors_module._VENDOR_ROWS and vendors_module._VENDOR_ROWS_HELD == 0


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
        assert read == [(key, None)]
        store.vendor_invoices(key)[0]["status"] = "changed by its caller"
        assert store.vendor_invoices(key)[0]["status"] == "review" and read == [(key, None)]
        store.park_invoice(ids[2], "Ann", "waiting for the PO")  # a change: read again
        assert store.vendor_invoices(key)[0]["status"] == "parked" and read == [(key, None), (key, None)]
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


def test_the_held_vendors_approved_numbers_follow_an_approval_made_elsewhere_and_what_is_asked(
    tmp_path, ground_truth, monkeypatch
):
    """``approved_numbers``, kept on the vendors asked and their own counts (``changes/vendor/<key>``, D-174): an
    approval through another Store, an approved coding changed by plain SQL, are in the next answer; another vendor's
    approval (each one, under 100 people) works nothing out again, where every approval did; other vendors asked get
    their own; each pair of numbers once; each caller gets lists of its own."""
    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    invoice, again, beta = (_invoice(store, tmp_path, ground_truth, n, v) for n, v in
                            (("H-1", "Acme Supply"), ("H-2", "Acme Supply"), ("H-3", "Beta Ltd")))  # fmt: skip
    key = store.get_invoice(invoice)["vendor_key"]
    worked = []
    real = type(store)._approved_numbers
    monkeypatch.setattr(type(store), "_approved_numbers", lambda self, keys: worked.append(keys) or real(self, keys))
    assert store.approved_numbers([key]) == {} and len(worked) == 1
    coding = {**ground_truth, "vendor_name": "Acme Supply", "gst_hst_registration_number": "123456789RT0001",
              "remit_bank_account": "004-12345-1234567"}  # fmt: skip
    other.approve_invoice(invoice, coding, "Jordan Lee")
    other.approve_invoice(again, coding, "Jordan Lee")
    assert store.approved_numbers([key]) == {key: [("123456789RT0001", "004-12345-1234567")]} and len(worked) == 2
    other.approve_invoice(beta, {**coding, "vendor_name": "Beta Ltd"}, "Jordan Lee")  # another vendor's
    assert store.approved_numbers([key]) == {key: [("123456789RT0001", "004-12345-1234567")]} and len(worked) == 2
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET final_output = json_set(final_output, '$.remit_bank_account', '999') "
                     "WHERE id = ?", (invoice,))  # fmt: skip
    assert store.approved_numbers([key]) == {key: [("123456789RT0001", "004-12345-1234567"),
                                                   ("123456789RT0001", "999")]}  # fmt: skip
    beta_key = store.get_invoice(beta)["vendor_key"]
    assert store.approved_numbers(["gamma"]) == {} and set(store.approved_numbers([beta_key, key])) == {beta_key, key}
    mine = store.approved_numbers([key])
    mine[key].append(("changed", "by the caller"))
    assert store.approved_numbers([key])[key][-1] == ("123456789RT0001", "999")


def test_the_vendors_on_hold_are_kept_until_the_vendor_list_changes_and_are_the_callers_own(tmp_path, monkeypatch):
    """``held_vendors`` (every check asks it) read the 20,000 vendors of the vendor master each time: kept until the
    vendor list changes (``changes/vendors``), through another Store or plain SQL (D-174)."""
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    with sqlite3.connect(store.path) as conn:
        _master(conn, "acme", "100", "")
        _master(conn, "beta", "200", "")
    read = []
    real = type(store)._held_vendors
    monkeypatch.setattr(type(store), "_held_vendors", lambda self: read.append(1) or real(self))
    assert store.held_vendors() == [] and store.held_vendors() == [] and len(read) == 1
    other.save_vendor("acme", "Acme", status="on_hold", notes="fraud check")
    assert [v["vendor_key"] for v in store.held_vendors()] == ["acme"] and len(read) == 2
    with sqlite3.connect(store.path) as conn:  # JD Edwards' own hold, synced
        conn.execute("UPDATE vendors SET erp_hold = 'H' WHERE vendor_key = 'beta'")
    mine = store.held_vendors()
    assert sorted(v["vendor_key"] for v in mine) == ["acme", "beta"]
    mine[0]["status"] = "changed by its caller"
    assert {v["status"] for v in store.held_vendors()} == {"on_hold", "active"}


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
    """``bulk.queue_checks``: from ``KEEP_FROM`` invoices asked, their facts are taken from the whole queue's, kept in
    each process and followed by the summary's log of the queue's changes (D-193): a finding stored by plain SQL, an
    invoice parked through another Store are in the next answer, only that invoice's facts read again; a change of
    settings (their checks then made against others: counted), the laptop's re-check, the invoices asked in another
    order or others read none; fewer than ``KEEP_FROM`` invoices are read each time; each caller gets rows and lists
    of its own, the findings in them read-only."""
    from ap_coder.coding import bulk

    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    checked = {"requires_review": True, "checks_stamp": store.checks_stamp(), "issues": []}
    ids = []
    for name in ("Q-1", "Q-2", "Q-3"):
        (tmp_path / f"{name}.pdf").write_bytes(name.encode())
        ids.append(store.add_invoice(tmp_path / f"{name}.pdf", {**ground_truth, "invoice_number": name}, checked))
    read = []
    real = Store.queue_rows
    monkeypatch.setattr(Store, "queue_rows", lambda self, some: read.append(some) or real(self, some))
    monkeypatch.setattr(bulk, "KEEP_FROM", 3)

    def asked(which=tuple(ids), recheck=False):
        return bulk.queue_checks(store, list(which), recheck=recheck)

    first = asked()
    assert sorted(first[0]) == ids and first[1] == 0 and all(c["found"] == [] for c in first[0].values())
    assert asked() == first and read == [None]  # kept: the whole queue read once
    first[0][ids[0]]["found"].append({"severity": "error", "code": "CHANGED_BY_THE_CALLER"})
    first[0][ids[0]]["q_pick"] = "changed by the caller"
    assert asked()[0][ids[0]] == {**first[0][ids[0]], "found": [], "q_pick": ""} and read == [None]  # copies
    found = '[{"severity": "error", "code": "TOTAL_MISMATCH", "message": "Totals differ"}]'
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET validation = json_set(validation, '$.issues', json(?)) WHERE id = ?",
                     (found, ids[0]))  # fmt: skip
    mismatch = asked()[0][ids[0]]["found"]
    assert mismatch == [{"severity": "error", "code": "TOTAL_MISMATCH"}] and read == [None, [ids[0]]]
    with pytest.raises(TypeError):
        mismatch[0]["code"] = "CHANGED_BY_THE_CALLER"
    other.park_invoice(ids[1], "Jordan Lee", "waiting for the buyer")
    assert sorted(asked()[0]) == [ids[0], ids[2]] and read[2:] == [[ids[1]]]
    with sqlite3.connect(store.path) as conn:  # a change of settings: their checks were made against others
        conn.execute("INSERT INTO settings (key, value) VALUES ('default_terms_days', '45')")
    assert asked()[1] == 2
    assert asked(recheck=True)[1] == 2  # the laptop's (no GL accounts: nothing checked again)
    assert asked(recheck=True)[1] == 2 and asked()[1] == 2
    assert sorted(asked([ids[2], ids[0], ids[1]])[0]) == [ids[0], ids[2]]  # in another order
    assert sorted(asked([*ids, 999])[0]) == [ids[0], ids[2]] and len(read) == 3  # asked otherwise (B-1731)
    asked(ids[:2])
    asked(ids[:2])
    assert read[3:] == [ids[:2], ids[:2]]  # fewer than KEEP_FROM: read each time
    key = summary_module.TOPICS["queue"]  # one the summary could not work out: each change to it counts
    row = {"status": "review", "broken": summary_module.BIT["queue"], **dict.fromkeys(summary_module.QUEUE_READ[1:])}
    assert key(row) != key(row) and key({**row, "broken": 0}) == key({**row, "broken": 0})


def test_the_queues_checks_are_kept_when_a_check_only_moves_an_invoice_in_the_queues_order(
    tmp_path, ground_truth, monkeypatch
):
    """B-1731: each check of a review screen writes the confidence it worked out, which moves the invoice in the
    queue's order, and the queue asked its checks of the invoices in that order: kept for the invoices asked in their
    order, they were worked out again after nearly every check (93 ms at 100,000 invoices), though they are the same
    for the same invoices in any order. Asked in another order they are the same; after a check only its invoice's
    facts are read again (the queue's log, D-193), after a finding changed only that one's; another invoice asked
    reads none."""
    from ap_coder.coding import bulk
    from ap_coder.core.store import REVIEW

    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    checked = {"requires_review": False, "checks_stamp": store.checks_stamp(), "issues": []}
    for name in ("O-1", "O-2", "O-3"):
        (tmp_path / f"{name}.pdf").write_bytes(name.encode())
        added = store.add_invoice(tmp_path / f"{name}.pdf", {**ground_truth, "invoice_number": name}, checked)
        store.refresh_confidence(added, 0.9, False)
    read = []
    real = Store.queue_rows
    monkeypatch.setattr(Store, "queue_rows", lambda self, some: read.append(some) or real(self, some))
    monkeypatch.setattr(bulk, "KEEP_FROM", 3)

    def asked(ids=None):
        return bulk.queue_checks(store, ids or [i["id"] for i in store.list_invoices(REVIEW)], recheck=False)

    first = asked()
    order = [i["id"] for i in store.list_invoices(REVIEW)]
    other.refresh_confidence(order[-1], 0.12, False)  # a check on its screen: first in the queue now
    assert [i["id"] for i in store.list_invoices(REVIEW)] == [order[-1], *order[:-1]]
    assert asked() == first and asked(order[::-1]) == first and read == [None, [order[-1]]]
    found = '[{"severity": "error", "code": "TOTAL_MISMATCH", "message": "Totals differ"}]'
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET validation = json_set(validation, '$.issues', json(?)) WHERE id = ?",
                     (found, order[0]))  # fmt: skip
    assert asked()[0][order[0]]["found"] == [{"severity": "error", "code": "TOTAL_MISMATCH"}]
    assert sorted(asked([*order, 999])[0]) == sorted(order) and read[2:] == [[order[0]]]  # another invoice asked


def test_the_queues_late_regular_invoices_follow_a_new_invoice_once_they_settled(tmp_path, ground_truth, monkeypatch):
    """B-1732: the queue's "regular invoices late" pill worked out every vendor's invoice dates again (98,720 rows,
    0.95 s at 100,000 invoices) after each upload or rejection, on each web worker. The page only shows it: it
    settles (D-164) for ``LATE_SETTLE`` seconds after a change, then follows it; never past a restore."""
    from ap_coder.core.store import reports
    from ap_coder.web.pages import review

    clock = [1000.0]
    monkeypatch.setattr(summary_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(reports, "wall", lambda: clock[0])  # (worked out once for every process, B-1798)
    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    today = dt.date.today()

    def bill(name: str, days_ago: int) -> int:
        (tmp_path / f"{name}.pdf").write_bytes(name.encode())
        doc = {**ground_truth, "vendor_name": "Acme Supply", "invoice_number": name, "grand_total": 100.0,
               "invoice_date": (today - dt.timedelta(days=days_ago)).isoformat()}  # fmt: skip
        return other.add_invoice(tmp_path / f"{name}.pdf", doc, {"requires_review": True})

    def late() -> list[str]:
        return [text for text, _ in review.today_pills(store, store.list_invoices()) if "late" in text]

    for n, days_ago in enumerate((120, 90, 60)):  # monthly: the next one was due a month ago
        bill(f"R-{n}", days_ago)
    assert late() == ["1 regular invoice late (Vendors page): Acme Supply"]
    backup = store.backup_now("manual")
    bill("R-3", 30)  # this month's
    clock[0] += review.LATE_SETTLE - 0.1
    assert late() == ["1 regular invoice late (Vendors page): Acme Supply"]  # settled
    clock[0] += 0.2
    assert late() == []  # followed
    store.restore_from(backup)  # (without this month's)
    assert late() == ["1 regular invoice late (Vendors page): Acme Supply"]  # at once


def test_the_queues_due_dates_follow_the_vendor_masters_terms_changed_elsewhere(tmp_path, ground_truth, monkeypatch):
    """B-1733: each visit of the queue read the vendor master's terms of every vendor (20,000 at 100,000 invoices: 27
    ms) to say when its 50 rows are due. They are kept until the vendor list changes (``changes/vendors``), through
    another Store or plain SQL."""
    from ap_coder.web.pages import review

    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    doc = {**ground_truth, "vendor_name": "Acme Supply", "invoice_date": dt.date.today().isoformat(),
           "due_date": None, "payment_terms": "", "grand_total": 100.0}  # fmt: skip
    (tmp_path / "T-1.pdf").write_bytes(b"T-1")
    inv = store.get_invoice(store.add_invoice(tmp_path / "T-1.pdf", doc, {"requires_review": True}))
    read = []
    real = Store.all_vendor_terms
    monkeypatch.setattr(Store, "all_vendor_terms", lambda self: read.append(1) or real(self))

    def due() -> str:
        return review.queue_rows(store, store.list_invoices(), 0.8)[0]["due"]

    with other._conn() as conn:  # as the vendor master's import writes them
        conn.execute("INSERT INTO vendors (vendor_key, display_name, terms, updated_at) VALUES (?, 'Acme Supply', "
                     "'Net 15', ?)", (inv["vendor_key"], NOW))  # fmt: skip
    first = due()
    assert due() == first and len(read) == 1  # kept
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE vendors SET terms = 'Net 60'")
    assert due() != first and len(read) == 2
    assert due() == review.due_note(inv["ai_output"], store.default_terms_days(), "Net 60")[0]


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


def test_previous_and_next_in_the_queues_own_order_read_its_ids_alone(tmp_path, ground_truth, monkeypatch):
    """B-1833: an invoice page's Previous and Next read the review queue's whole list again after every approval, park
    or new invoice (its rows kept until the list changes: at once under 100 people, 5.1% of the busy web workers' CPU
    at 300,000 invoices), only to step through its ids. In the queue's own order (no search, no other sort or view)
    they are read alone, off the queue's index: the same ids, the same steps."""
    from ap_coder.web.pages.invoice import navigation

    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    a, b, c, d = (_invoice(store, tmp_path, ground_truth, name) for name in ("N-1", "N-2", "N-3", "N-4"))
    for invoice_id, confidence in ((a, 0.5), (b, 0.6), (c, 0.2)):
        other.refresh_confidence(invoice_id, confidence, True)
    other.park_invoice(d, "Jordan Lee", "waiting for the buyer")
    seen: list[str] = []
    real = sqlite3.connect

    def tracing(*args, **kwargs):
        conn = real(*args, **kwargs)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracing)
    for query in ({}, {"sort": "priority"}, {"show": "all", "limit": "100"}):
        nav = navigation(store, a, query)
        assert nav["ids"] == [c, a, b] and nav["next"] == _with(f"/review/{b}", query) and nav["next_id"] == b
        assert navigation(store, d, query)["position"] is None  # (parked: not in the queue)
    assert not [s for s in seen if "file_name" in s], seen  # no row of the queue's list read
    assert [r["id"] for r in store.list_invoices("review")] == [c, a, b]
    assert navigation(store, a, {"sort": "amount"})["ids"] == [r["id"] for r in store.list_invoices("review")]


def _with(path: str, query: dict[str, str]) -> str:
    from urllib.parse import urlencode

    return path + (f"?{urlencode(query)}" if query else "")


def test_the_queues_lists_keep_their_rows_when_a_check_only_writes_a_confidence_in_the_same_order(
    tmp_path, ground_truth, monkeypatch
):
    """B-1737: each check writes the confidence it worked out, and the queue's lists were read again in full after
    every one (48 ms at 100,000 invoices, on ``/review`` and each invoice page's Previous and Next). The rows of the
    three lists are kept by invoice and followed by the summary's log of the queue's changes (D-193): a check reads its
    invoice's row again, nothing else; the lists, their order and confidences are what reading them afresh gives. They
    are read with the log and the queue's order in one moment: another worker's park meanwhile is in the next ask."""
    from ap_coder.core.store import PARKED, PENDING, REVIEW

    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    a, b, c, d = (_invoice(store, tmp_path, ground_truth, name) for name in ("K-1", "K-2", "K-3", "K-4"))
    other.park_invoice(d, "Jordan Lee", "waiting for the buyer")
    rows_read = []
    real = Store._listed
    monkeypatch.setattr(Store, "_listed", lambda self, ids: rows_read.append(ids and sorted(ids)) or real(self, ids))

    def fresh(*statuses):
        listed, marks = ", ".join(summary_module.LISTED_COLUMNS), ", ".join("?" * len(statuses))
        return store.summary_fetch(f"SELECT {listed} FROM invoice_summary WHERE status IN ({marks}) ORDER BY "
                                   "requires_review DESC, adjusted_confidence ASC, id ASC", statuses)  # fmt: skip

    def same(*statuses):
        listed = store.list_invoices(statuses=statuses)
        assert listed == fresh(*statuses) and [list(r) for r in listed] == [list(r) for r in fresh(*statuses)]
        return [r["id"] for r in listed]

    assert same(REVIEW, PARKED, PENDING) == [a, b, c, d] and same(REVIEW) == [a, b, c] and rows_read == [None]
    other.refresh_confidence(c, 0.12, False)  # a check on its screen
    with other._conn() as conn:
        conn.execute("UPDATE invoices SET adjusted_confidence = 0.5, requires_review = 0 WHERE id = ?", (a,))
    assert same(REVIEW, PARKED, PENDING) == [b, d, c, a] and same(REVIEW) == [b, c, a] and rows_read[1:] == [[a, c]]
    with sqlite3.connect(store.path) as conn:  # by plain SQL: worked into the summary by the next read
        conn.execute("UPDATE invoices SET adjusted_confidence = 0.3, requires_review = 0 WHERE id = ?", (b,))
    assert same(REVIEW) == [c, b, a] and same(REVIEW, PARKED, PENDING) == [d, c, b, a] and rows_read[2:] == [[b]]
    with other._conn() as conn:  # anything else listed changed: its row read again
        conn.execute("UPDATE invoices SET invoice_number = 'K-9' WHERE id = ?", (b,))
    rows_read.clear()
    assert same(REVIEW) == [c, b, a] and store.list_invoices(REVIEW)[1]["invoice_number"] == "K-9"
    assert rows_read == [[b]]

    def parked_meanwhile(self, ids):  # another worker parks one just after the log was read
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE invoices SET status = 'parked' WHERE id = ?", (a,))
        return real(self, ids)

    with other._conn() as conn:
        conn.execute("UPDATE invoices SET invoice_number = 'K-8' WHERE id = ?", (b,))
    monkeypatch.setattr(Store, "_listed", parked_meanwhile)
    assert [r["id"] for r in store.list_invoices(REVIEW)] == [c, b, a]  # (as of that moment)
    monkeypatch.undo()
    assert same(REVIEW) == [c, b] and same(PARKED) == [d, a]


def test_the_rails_counts_settle_after_a_change_of_status_and_what_decides_with_them_counts_exactly(
    tmp_path, ground_truth, monkeypatch
):
    """B-1738: every page's rail counted the invoices by status again after each approval, park or rejection (16 ms
    at 100,000 invoices, in each web worker). The rail and the queue's tabs only show them: they settle (D-164) for
    ``COUNTS_SETTLE`` seconds after a change, then follow it; never past a restore. What decides with them (the queue
    is clear after an approval, the demo may be loaded) counts exactly, and the settled count is then that one."""
    from ap_coder.web.core import nav_counts

    clock = [1000.0]
    monkeypatch.setattr(summary_module, "monotonic", lambda: clock[0])
    store = Store(tmp_path / "ap.db")
    other = Store(store.path)
    a, b, c = (_invoice(store, tmp_path, ground_truth, name) for name in ("C-1", "C-2", "C-3"))
    assert nav_counts(store)["review"] == 3 and store.status_counts(settled=True) == {"review": 3}
    backup = store.backup_now("manual")
    other.park_invoice(a, "Jordan Lee", "waiting for the buyer")
    clock[0] += summary_module.COUNTS_SETTLE - 0.1
    assert nav_counts(store)["review"] == 3  # settled
    assert store.status_counts() == {"review": 2, "parked": 1}  # exact
    assert nav_counts(store)["review"] == 2  # (that one, kept)
    other.reject_invoice(b, "Jordan Lee", "a copy")
    clock[0] += summary_module.COUNTS_SETTLE - 0.1
    assert store.status_counts(settled=True) == {"review": 2, "parked": 1}  # settled
    clock[0] += 0.2
    assert store.status_counts(settled=True) == {"review": 1, "parked": 1, "rejected": 1}  # followed
    store.restore_from(backup)
    assert nav_counts(store)["review"] == 3 and store.status_counts(settled=True) == {"review": 3}  # at once


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


def test_spends_figures_and_currencies_follow_the_approved_invoices_and_what_is_asked(
    tmp_path, ground_truth, monkeypatch
):
    """The Spend page's figures and currencies, kept on the summary's generation, per period, currency, GL categories
    and rates (copies: the caller's own changed after is another question); once the invoices changed, worked out
    again at most every ``spend.SETTLE_SECONDS`` (D-166)."""
    from ap_coder.core.store import reports
    from ap_coder.erp import spend

    clock = [1000.0]
    monkeypatch.setattr(summary_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(reports, "wall", lambda: clock[0])  # (worked out once for every process, B-1797)
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
    clock[0] += spend.SETTLE_SECONDS - 1
    assert spend.figures(store, start, end, "USD", {})["invoices"] == 0  # worked out under 30 s ago: settled
    assert spend.spend_currencies(store, start, end) == ["CAD"]
    clock[0] += 1
    assert seen("USD") == (1, total, ["Uncategorised"], ["CAD", "USD"])
    assert seen(first=end) == (0, 0.0, [], [])
    rates = {"CAD": 1.0, "USD": 1.5}
    assert seen(rates=rates)[:2] == (2, round(total * 2.5, 2))
    rates["USD"] = 2.0
    assert seen(rates=rates)[:2] == (2, round(total * 3, 2))
    _plain(store, "UPDATE invoices SET status = 'review' WHERE id = ?", usd)
    clock[0] += spend.SETTLE_SECONDS
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


# --- Answers worked out once for every process of the company server (``Store.shared_answer``, D-169) ---------------


class _Processes:
    """The server's processes, in this one: each with its own memory of the shared answers, of what it keeps for itself
    (``summary_memo``) and of month-end's POs (what another web worker, or the background process, holds), the
    database the same."""

    def __init__(self, monkeypatch) -> None:
        self.monkeypatch, self.memory = monkeypatch, {}

    def __call__(self, name: str) -> None:
        from ap_coder.core.store import reports
        from ap_coder.erp import accruals

        kept, parts, own = self.memory.setdefault(name, ({}, {}, {}))
        self.monkeypatch.setattr(reports, "_KEPT", kept)
        self.monkeypatch.setattr(reports, "_WORKING", {})
        self.monkeypatch.setattr(accruals, "_PARTS", parts)
        self.monkeypatch.setattr(summary_module, "_MEMO", own)
        self.monkeypatch.setattr(summary_module, "_COMPUTING", {})


class Shared(Kept):
    """``store.shared_answer(name, ...)`` of ``compute``, counting how many times it is worked out (in any process)."""

    def __call__(self):
        def work():
            self.worked += 1
            return self.compute()

        return self.store.shared_answer(self.name, work, **self.stamp)


def _vendor_rows(store: Store) -> list[list]:
    return [list(r) for r in _rows(store, "vendors")]


def _waiting(store: Store, monkeypatch) -> threading.Event:
    """Set when a thread asking ``store`` for a shared answer finds another process holding its lease: it waits for
    that one's answer from then on."""
    waiting, held = threading.Event(), store._lease_held

    def lease_held(lease: str) -> bool:
        found = held(lease)
        if found:
            waiting.set()
        return found

    monkeypatch.setattr(store, "_lease_held", lease_held)
    return waiting


def test_a_shared_answer_is_worked_out_once_for_every_process_and_follows_each_change_made_elsewhere(
    tmp_path, monkeypatch
):
    """D-169: a process that has nothing kept takes the answer another one worked out (``report_answers``) while the
    stamp read before the work is the same; a change through another Store or plain SQL has it worked out again, once
    for all of them."""
    process = _Processes(monkeypatch)
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    kept = Shared(store, "rows/vendors", lambda: _vendor_rows(store), version="t-1", topic="", changes=("vendors",))
    process("worker 1")
    assert kept() == [] and kept.worked == 1
    process("worker 2")
    assert kept() == [] and kept() == [] and kept.worked == 1  # (taken from the database, then its own)
    with other._conn() as conn:
        conn.execute("INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('acme', 'Acme', ?)", (NOW,))
    assert kept() == _vendor_rows(store) != [] and kept.worked == 2
    process("worker 1")  # (it kept the answer before the change)
    assert kept() == _vendor_rows(store) and kept.worked == 2
    _plain(store, "UPDATE vendors SET notes = 'a note'")
    process("background")
    assert kept() == _vendor_rows(store) and kept.worked == 3
    for name in ("worker 1", "worker 2"):
        process(name)
        assert kept() == _vendor_rows(store) and kept.worked == 3


def test_a_shared_answer_worked_out_in_a_write_that_is_undone_is_neither_kept_nor_shared(tmp_path, monkeypatch):
    """Inside a write under way the answer reads that write's changes, which may yet be undone: worked out afresh,
    neither taken from the database nor written there, so no other process takes it once the count comes back."""
    from ap_coder.core.store.reports import asked_key

    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    _plain(store, "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('acme', 'Acme', ?)", NOW)

    def names():
        with store._conn() as conn:  # (the session's connection, inside the write)
            return dict(conn.execute("SELECT vendor_key, display_name FROM vendors").fetchall())

    kept = Shared(store, "names", names, version="t-1", topic="", changes=("vendors",))
    process("worker 1")
    assert kept() == {"acme": "Acme"} and kept.worked == 1
    with pytest.raises(RuntimeError), store.session(), store._conn() as conn:
        conn.execute("UPDATE vendors SET display_name = 'UNDONE'")
        assert kept() == {"acme": "UNDONE"} and kept.worked == 2
        raise RuntimeError("undone")
    assert store.kept_answer("names", asked_key(None), "t-1")[1] == {"acme": "Acme"}
    _plain(store, "UPDATE vendors SET notes = 'a note'")  # another worker's change: the count is the undone write's
    process("worker 2")
    assert kept() == {"acme": "Acme"} and kept.worked == 3


def test_a_restore_gives_a_new_summary_id_so_no_shared_answer_from_before_is_taken(tmp_path, monkeypatch):
    """A backup holds the answers kept when it was made, and after a restore the counts are the backup's: neither
    that answer nor any process's from before the restore is taken (another ``summary_id``)."""
    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    _plain(store, "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('acme', 'Acme', ?)", NOW)
    kept = Shared(store, "vendors", lambda: _vendor_rows(store), version="t-1", topic="", changes=("vendors",))
    process("worker 1")
    assert kept()[0][1] == "Acme" and kept.worked == 1
    backup = store.backup_now("manual")  # (with that answer in it)
    _plain(store, "UPDATE vendors SET display_name = 'Acme after'")
    assert kept()[0][1] == "Acme after" and kept.worked == 2
    store.restore_from(backup)
    process("worker 2")
    assert kept()[0][1] == "Acme" and kept.worked == 3  # (not the backup's answer: worked out again)
    process("worker 1")
    assert kept()[0][1] == "Acme" and kept.worked == 3  # (not its own from before the restore: worker 2's)
    _plain(store, "UPDATE vendors SET display_name = 'Acme restored'")  # the count again the one from before
    process("background")
    assert kept()[0][1] == "Acme restored"


def test_an_answer_another_ap_coder_version_or_format_kept_or_not_json_is_worked_out_again(tmp_path, monkeypatch):
    """A row an older AP Coder wrote (or another format of the answer, or one that is not JSON) is never taken: the
    answer is worked out again and kept as this version's."""
    import json

    from ap_coder.core.store import reports

    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    kept = Shared(store, "rows", lambda: [1, 2], version="t-2", topic="", changes=("vendors",))
    stamp = json.dumps(store._answer_stamp("", ("vendors",))[0])
    rows = [("0.0.1/t-2", "[9]"), (reports._version("t-1"), "[9]"), (reports._version("t-2"), "not JSON")]
    for n, (version, value) in enumerate(rows, start=1):
        process(f"worker {n}")
        _plain(store, "INSERT OR REPLACE INTO report_answers (name, asked, version, stamp, value, made_at) VALUES "
                      "('rows', ?, ?, ?, ?, ?)", reports.asked_key(None), version, stamp, value, 0.0)  # fmt: skip
        assert kept() == [1, 2] and kept.worked == n
    process("background")
    assert kept() == [1, 2] and kept.worked == 3  # (kept by this version now)


def test_an_answer_over_the_size_cap_is_not_kept_for_the_other_processes(tmp_path, monkeypatch):
    """An answer bigger than ``ANSWER_MOST_BYTES`` as JSON is kept in its process only: each other works it out."""
    from ap_coder.core.store import reports

    monkeypatch.setattr(reports, "ANSWER_MOST_BYTES", 100)
    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    kept = Shared(store, "big", lambda: ["x" * 200], version="t-1", topic="", changes=("vendors",))
    process("worker 1")
    assert kept() == ["x" * 200] and kept() == ["x" * 200] and kept.worked == 1
    assert store.kept_answer("big", reports.asked_key(None), "t-1") is None
    process("worker 2")
    assert kept() == ["x" * 200] and kept.worked == 2


def test_a_shared_answer_only_shown_settles_across_processes_but_never_past_its_age(tmp_path, monkeypatch):
    """With ``settle`` (D-164), once what it depends on changed, an answer whose work began less than that long ago, in
    any process, is taken still; past that it is worked out again; asked without it, never taken (exact)."""
    from ap_coder.core.store import reports

    clock = [1000.0]
    monkeypatch.setattr(reports, "wall", lambda: clock[0])
    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    settled = Shared(store, "rows", lambda: _vendor_rows(store), version="t-1", topic="", changes=("vendors",),
                     settle=30)  # fmt: skip
    exact = Shared(store, "rows", lambda: _vendor_rows(store), version="t-1", topic="", changes=("vendors",))
    process("worker 1")
    assert settled() == [] and settled.worked == 1
    _plain(store, "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('acme', 'Acme', ?)", NOW)
    clock[0] += 29
    process("worker 2")
    assert settled() == [] and settled.worked == 1  # (worker 1's, 29 s old)
    clock[0] += 1
    assert settled() == _vendor_rows(store) and settled.worked == 2  # (30 s: not even worker 2's own copy)
    _plain(store, "UPDATE vendors SET notes = 'a note'")
    process("worker 1")
    assert exact() == _vendor_rows(store) and exact.worked == 1  # (exact: worked out at once)


def test_a_process_waits_for_another_working_the_same_shared_answer_out(tmp_path, monkeypatch):
    """While another process holds the answer's lease (it is working it out), one asking the same waits and takes the
    answer it writes, instead of reading everything at once."""
    from ap_coder.core.store import reports

    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    kept = Shared(store, "slow", lambda: ["mine"], version="t-1", topic="", changes=("vendors",))
    assert store.take_lease(f"answer/slow/{reports.asked_key(None)}", "another process", 60)
    process("worker 1")
    found, waiting = [], _waiting(store, monkeypatch)
    asking = threading.Thread(target=lambda: found.append(kept()))
    asking.start()
    assert waiting.wait(10)
    stamp = store._answer_stamp("", ("vendors",))[0]
    store.keep_answer("slow", reports.asked_key(None), "t-1", stamp, ["theirs"])
    asking.join(10)
    assert found == [["theirs"]] and kept.worked == 0


def test_month_ends_received_pos_worked_out_in_one_process_are_taken_by_the_others_for_each_change(
    tmp_path, monkeypatch
):
    """D-169: each received PO's accruals, kept for every process by the digest of what they come from (D-166): a
    process with none kept decodes no billing invoice another one decoded, and after a change only the POs whose
    billing changed; each answer is what working every PO out afresh gives."""
    from ap_coder.erp import accruals

    process = _Processes(monkeypatch)
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    second = [{**_po_line(n, 4.0), "po_number": "PO-2", "vendor_name": "Bolt Works"} for n in (1, 2)]
    other.import_purchase_orders([_po_line(1, 4.0), _po_line(2, 1.0), *second])
    october = dt.date(2026, 10, 31)
    bills = {}
    for number, (po, vendor) in enumerate((("PO-1", "Acme Supply"), ("PO-2", "Bolt Works"))):
        line = {"line_number": 1, "description": "Bolts size 1", "quantity": 2, "unit_price": 2.0, "amount": 4.0}
        bills[po] = other.add_invoice(tmp_path / f"{po}.pdf", {
            "vendor_name": vendor, "invoice_number": f"B-{number}", "po_number": po, "invoice_date": "2026-10-03",
            "currency": "CAD", "line_items": [line], "subtotal": 4.0, "grand_total": 4.2}, {})  # fmt: skip
    decoded: list[int] = []
    real = Store.billing_codings
    monkeypatch.setattr(Store, "billing_codings", lambda self, ids: decoded.extend(ids) or real(self, ids))

    def received(name: str) -> list[tuple]:
        process(name)
        return [(a.reference, a.amount) for a in accruals._received_not_invoiced(store, october)]

    def afresh() -> list[tuple]:
        with sqlite3.connect(store.path) as conn:
            kept = conn.execute("SELECT * FROM report_answers").fetchall()
            conn.execute("DELETE FROM report_answers")
        found = received(f"afresh {len(decoded)}")  # (nothing kept anywhere: worked out whole)
        with sqlite3.connect(store.path) as conn:
            conn.execute("DELETE FROM report_answers")
            conn.executemany("INSERT INTO report_answers VALUES (?, ?, ?, ?, ?, ?, ?)", kept)
        return found

    first = received("worker 1")
    assert first == [("PO-1 line 1", 4.0), ("PO-1 line 2", 2.0), ("PO-2 line 1", 4.0), ("PO-2 line 2", 8.0)]
    assert sorted(decoded) == sorted(bills.values())
    decoded.clear()
    assert received("worker 2") == first and decoded == []  # (each PO's from worker 1: nothing decoded)
    _plain(store, "UPDATE invoices SET final_output = json_set(ai_output, '$.line_items[0].quantity', 4) "
                  "WHERE id = ?", bills["PO-2"])  # fmt: skip
    decoded.clear()
    changed = received("worker 3")
    assert decoded == [bills["PO-2"]]  # (only the changed PO's billing invoice)
    assert changed == [("PO-1 line 1", 4.0), ("PO-1 line 2", 2.0), ("PO-2 line 2", 8.0)] == afresh()
    decoded.clear()
    assert received("worker 2") == changed and decoded == []  # (worker 3's for that PO, its own for the other)


def test_an_answer_only_shown_asked_while_another_works_it_out_again_is_the_one_before_not_waited_for(
    tmp_path, monkeypatch
):
    """B-1834: once a settled answer (D-164) was past its settle, every page asking it while one worked it out again
    waited for that one (Spend's p95 4.7 s and month-end's 7.5 s under 100 people at 100,000 invoices): in another
    process (its lease held), or another thread of the same. They take the answer before instead, as the settle
    allows for what a page only shows; the one working it out still does, and once it is kept it is taken. Never one
    of another database (a restore), nor for an exact answer, nor when there is none before: those wait as before."""
    from ap_coder.core.store import reports

    clock = [1000.0]
    monkeypatch.setattr(reports, "wall", lambda: clock[0])
    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    rows = lambda: _vendor_rows(store)  # noqa: E731
    settled = Shared(store, "rows", rows, version="t-1", topic="", changes=("vendors",), settle=30)
    process("worker 1")
    before = settled()
    assert before == [] and settled.worked == 1
    _plain(store, "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('acme', 'Acme', ?)", NOW)
    clock[0] += 31
    lease = f"answer/rows/{reports.asked_key(None)}"
    assert store.take_lease(lease, "another process", 60)  # (it works the answer out again)
    for name in ("worker 1", "worker 2"):  # (its own kept, and one with none: the database's)
        process(name)
        at = time.monotonic()
        assert settled() == before and settled.worked == 1 and time.monotonic() - at < 1, name
    stamp = store._answer_stamp("", ("vendors",))[0]
    store.keep_answer("rows", reports.asked_key(None), "t-1", stamp, _vendor_rows(store), clock[0])
    store.drop_lease(lease, "another process")
    assert settled() == _vendor_rows(store) != before and settled.worked == 1  # (the other's, once kept)

    # In one process: a thread working it out, another asking meanwhile.
    previous = _vendor_rows(store)
    _plain(store, "UPDATE vendors SET notes = 'a note'")
    clock[0] += 31
    began, finish = threading.Event(), threading.Event()
    slow = Shared(store, "rows", lambda: began.set() or finish.wait(10) and _vendor_rows(store), version="t-1",
                  topic="", changes=("vendors",), settle=30)  # fmt: skip
    found = []
    working = threading.Thread(target=lambda: found.append(slow()))
    working.start()
    assert began.wait(10)
    at = time.monotonic()
    assert settled() == previous != _vendor_rows(store) and time.monotonic() - at < 1 and settled.worked == 1
    finish.set()
    working.join(10)
    assert found == [_vendor_rows(store)] and settled() == _vendor_rows(store) and settled.worked == 1

    # Exact, or nothing before, or only another database's before: waited for.
    exact = Shared(store, "rows", rows, version="t-1", topic="", changes=("vendors",))
    _plain(store, "UPDATE vendors SET notes = 'another note'")
    clock[0] += 31
    assert store.take_lease(lease, "another process", 60)
    for name, ask, restored in (("worker 4", exact, False), ("worker 5", settled, True)):
        process(name)
        if restored:  # (what is kept is of the database before a restore: another summary_id)
            _plain(store, "UPDATE settings SET value = 'restored' WHERE key = 'summary_id'")
        found.clear()
        waiting = _waiting(store, monkeypatch)
        asking = threading.Thread(target=lambda ask=ask: found.append(ask()))
        asking.start()
        assert waiting.wait(10), name
        assert not found, name  # (waiting)
        store.keep_answer("rows", reports.asked_key(None), "t-1", store._answer_stamp("", ("vendors",))[0],
                          ["theirs"], clock[0])  # fmt: skip
        asking.join(10)
        assert found == [["theirs"]], name


def test_a_process_waiting_takes_the_answer_another_began_after_it_asked_though_a_change_came_meanwhile(
    tmp_path, monkeypatch
):
    """B-1802: a process waiting for the answer another was working out took it only for the stamp read at each look:
    a change while the other worked (a stream of approvals) had it work the same answer out again itself once the
    other let go. An answer whose stamp is no older than the one read as it asked holds: it was worked out from what
    the database held then, or later."""
    from ap_coder.core.store import reports

    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    kept = Shared(store, "slow", lambda: ["mine"], version="t-1", topic="", changes=("vendors",))
    lease = f"answer/slow/{reports.asked_key(None)}"
    assert store.take_lease(lease, "another process", 60)
    process("worker 1")
    found, waiting = [], _waiting(store, monkeypatch)
    asking = threading.Thread(target=lambda: found.append(kept()))
    asking.start()
    assert waiting.wait(10)
    stamp = store._answer_stamp("", ("vendors",))[0]  # (what the other read before its work: the same)
    _plain(store, "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('acme', 'Acme', ?)", NOW)
    store.keep_answer("slow", reports.asked_key(None), "t-1", stamp, ["theirs"])
    store.drop_lease(lease, "another process")
    asking.join(10)
    assert found == [["theirs"]] and kept.worked == 0
    assert kept() == ["mine"] and kept.worked == 1  # (asked after the change: worked out again)


def _approved(store: Store, tmp_path, ground_truth, name: str, **doc) -> int:
    invoice = _invoice(store, tmp_path, ground_truth, name)
    store.approve_invoice(invoice, {**ground_truth, "invoice_number": name, **doc}, "Jordan Lee")
    return invoice


def test_spends_figures_and_currencies_are_worked_out_once_for_every_process_and_follow_another_ones_change(
    tmp_path, ground_truth, monkeypatch
):
    """B-1797: the Spend page's figures and currencies (1.9 s and 0.4 s at 100,000 invoices) were kept by each web
    worker for itself: under a stream of approvals each of them worked them out again every ``SETTLE_SECONDS`` (17%
    of the workers' CPU under 100 people at 300,000 invoices). They are worked out once for every process (D-169),
    taken as they were worked out (tuples and all), and settle as before, by when their work began in any process."""
    from ap_coder.core.store import reports
    from ap_coder.erp import spend

    clock = [1000.0]
    monkeypatch.setattr(reports, "wall", lambda: clock[0])
    monkeypatch.setattr(summary_module, "monotonic", lambda: clock[0])
    worked: list[str] = []
    for name in ("_figures", "_spend_currencies"):
        real = getattr(spend, name)
        monkeypatch.setattr(spend, name, lambda *a, real=real, name=name: worked.append(name) or real(*a))
    process = _Processes(monkeypatch)
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    start, end, category, rates = dt.date(2000, 1, 1), dt.date(2099, 12, 31), {"6100": "Office"}, {"CAD": 1.0}
    _approved(other, tmp_path, ground_truth, "S-1")

    def shown(name: str) -> tuple:
        process(name)
        return (spend.spend_currencies(store, start, end), spend.figures(store, start, end, "CAD", category),
                spend.figures(store, start, end, "CAD", category, rates))  # fmt: skip

    def afresh() -> tuple:
        return (spend._spend_currencies(store, start, end), spend.read_figures(store, start, end, "CAD", category),
                spend.read_figures(store, start, end, "CAD", category, rates))  # fmt: skip

    before = shown("worker 1")
    assert before[1]["invoices"] == 1 and len(worked) == 3
    assert shown("worker 2") == before == afresh() and len(worked) == 6  # (worker 1's; then each read afresh)
    _approved(other, tmp_path, ground_truth, "S-2")
    clock[0] += spend.SETTLE_SECONDS - 1
    assert shown("worker 3") == before and len(worked) == 6  # settled: worker 1's, begun 29 s ago
    clock[0] += 1
    worked.clear()
    after = shown("worker 2")
    assert after[1]["invoices"] == 2 and len(worked) == 3
    assert shown("worker 1") == after == afresh() and len(worked) == 6  # (worker 2's)


def _bill(store: Store, tmp_path, ground_truth, name: str, days_ago: int) -> int:
    (tmp_path / f"{name}.pdf").write_bytes(name.encode())
    doc = {**ground_truth, "vendor_name": "Acme Supply", "invoice_number": name, "grand_total": 100.0,
           "invoice_date": (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()}  # fmt: skip
    return store.add_invoice(tmp_path / f"{name}.pdf", doc, {"requires_review": True})


def test_the_recurring_vendors_are_worked_out_once_for_every_process_and_the_late_pill_is_never_older_than_they_settle(
    tmp_path, ground_truth, monkeypatch
):
    """B-1798: the recurring vendors (0.8 s at 100,000 invoices: the queue's late pill, Vendors, Month-end) were
    worked out by each web worker for itself, every ``SETTLE_SECONDS`` under a stream of changes; and the queue's pill
    kept its own answer, settled on top of theirs, so it could show what was true up to twice ``LATE_SETTLE`` ago.
    They are worked out once for every process, the pill reads them as they are, at most ``LATE_SETTLE`` old."""
    from ap_coder.core.store import reports
    from ap_coder.erp import recurring
    from ap_coder.web.pages import review

    clock = [1000.0]
    monkeypatch.setattr(reports, "wall", lambda: clock[0])
    monkeypatch.setattr(summary_module, "monotonic", lambda: clock[0])
    worked = []
    real = recurring.detect
    monkeypatch.setattr(recurring, "detect", lambda *a: worked.append(1) or real(*a))
    process = _Processes(monkeypatch)
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    for n, days_ago in enumerate((120, 90, 60)):  # monthly: the next one was due a month ago
        _bill(other, tmp_path, ground_truth, f"R-{n}", days_ago)
    pill = "1 regular invoice late (Vendors page): Acme Supply"

    def late(name: str) -> list[str]:
        process(name)
        return [text for text, _ in review.today_pills(store, store.list_invoices()) if "late" in text]

    def vendors(name: str) -> list[tuple]:
        process(name)
        found = recurring.detected(store)
        assert found == real(store.vendor_invoice_dates(), dt.date.today())  # (as worked out afresh)
        return [(r.vendor_name, r.status) for r in found]

    assert vendors("worker 1") == [("Acme Supply", recurring.LATE)] and len(worked) == 1
    assert late("worker 2") == [pill] and len(worked) == 1  # (worker 1's)
    _bill(other, tmp_path, ground_truth, "R-3", 30)  # this month's: the next one due today
    clock[0] += review.LATE_SETTLE - 1
    assert late("worker 3") == [pill] and len(worked) == 1  # settled: worker 1's, begun 59 s ago
    clock[0] += 2
    assert late("worker 3") == [] and len(worked) == 2  # never older than that (not its own of 2 s ago)
    assert vendors("worker 1") == [("Acme Supply", recurring.DUE_SOON)] and len(worked) == 2  # (worker 3's)


def test_insights_figures_are_worked_out_once_for_every_process_and_follow_another_ones_change(
    tmp_path, ground_truth, monkeypatch
):
    """B-1799: the Insights page's figures (``shown``, ``shown_operations``: 2 s at 100,000 invoices) were kept by each
    web worker for itself. They are worked out once for every process, taken as they were worked out (the
    assumptions, the tuples), and settle as before (``insights.SETTLE_SECONDS``)."""
    from ap_coder.core.store import reports
    from ap_coder.erp import insights

    clock = [1000.0]
    monkeypatch.setattr(reports, "wall", lambda: clock[0])
    monkeypatch.setattr(summary_module, "monotonic", lambda: clock[0])
    worked = {"compute": 0, "operations": 0}
    for name, real in (("compute", insights.compute), ("operations", insights.operations)):
        monkeypatch.setattr(insights, name, lambda *a, real=real, name=name: worked.update({name: worked[name] + 1})
                            or real(*a))  # fmt: skip
    process = _Processes(monkeypatch)
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    _approved(other, tmp_path, ground_truth, "I-1", payment_terms="2/10 Net 30")
    _invoice(other, tmp_path, ground_truth, "I-2")

    def shown(name: str) -> tuple:
        process(name)
        return insights.shown(store), insights.shown_operations(store)

    def afresh() -> tuple:
        return insights.compute(store), insights.operations(store)

    before = shown("worker 1")
    assert before[0]["processed"] == 2 and worked == {"compute": 1, "operations": 1}
    assert shown("worker 2") == before == afresh() and worked == {"compute": 2, "operations": 2}  # (worker 1's)
    _invoice(other, tmp_path, ground_truth, "I-3")
    clock[0] += insights.SETTLE_SECONDS - 1
    assert shown("worker 3") == before and worked == {"compute": 2, "operations": 2}  # settled
    clock[0] += 1
    after = shown("worker 2")
    assert after[0]["processed"] == 3 and worked == {"compute": 3, "operations": 3}
    assert shown("worker 1") == after == afresh() and worked == {"compute": 4, "operations": 4}  # (worker 2's)


def test_the_vendors_workload_is_worked_out_once_for_every_process_and_follows_another_ones_change(
    tmp_path, monkeypatch
):
    """D-194: the Vendors page's workload (``insights.vendor_workload``, worked out again after each change: 4% of the
    web workers' CPU at 300,000 invoices) was kept by each web worker for itself. It is worked out once for every
    process, what working it out afresh gives (its codes a list each), and again after any change to an invoice,
    whoever made it, once settled (D-197: only shown, as Insights); each caller gets its own copy to change."""
    from ap_coder.core.store import reports
    from ap_coder.erp import insights

    clock = [1000.0]
    monkeypatch.setattr(reports, "wall", lambda: clock[0])
    worked: list[int] = []
    real = insights._vendor_workload
    monkeypatch.setattr(insights, "_vendor_workload", lambda *a: worked.append(1) or real(*a))
    process = _Processes(monkeypatch)
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")

    def bill(name: str, vendor: str = "Acme Supply") -> None:
        other.add_invoice(tmp_path / f"{name}.pdf", {"vendor_name": vendor, "invoice_number": name, "grand_total": 10.0,
                                                     "currency": "CAD"},
                          {"issues": [{"code": "TOTAL_MISMATCH", "severity": "error"}]})  # fmt: skip

    def shown(name: str) -> list:
        process(name)
        return insights.vendor_workload(store)

    bill("A-1"), bill("A-2")
    before = shown("worker 1")
    assert [(v["vendor_name"], v["with_problems"], v["top_codes"]) for v in before] == [
        ("Acme Supply", 2, ["TOTAL_MISMATCH"])] and len(worked) == 1  # fmt: skip
    assert shown("worker 2") == before == real(store, 2) and len(worked) == 1  # (worker 1's)
    bill("Z-1", "Zeta Parts"), bill("Z-2", "Zeta Parts"), bill("Z-3", "Zeta Parts")
    clock[0] += insights.SETTLE_SECONDS - 1
    assert shown("worker 2") == before and len(worked) == 1  # (settled)
    clock[0] += 1
    after = shown("worker 2")
    assert [v["vendor_name"] for v in after] == ["Zeta Parts", "Acme Supply"] and len(worked) == 2
    after[0]["top_codes"].append("changed by its caller")
    after[1]["invoices"] = 0
    assert shown("worker 1") == shown("worker 2") == real(store, 2) != after and len(worked) == 2  # (worker 2's)


def test_the_recurring_vendors_work_keeps_no_copy_of_every_invoice_in_the_worker(tmp_path, ground_truth):
    """B-1803: the recurring vendors are one answer for every process (D-175), so the rows they are worked out from
    (vendor, date and total of every invoice in review or approved: about 140 MB at 300,000) are read for the work
    and not kept in the worker that did it."""
    from ap_coder.erp import recurring

    store = Store(tmp_path / "ap.db")
    for n, days_ago in enumerate((120, 90, 60)):
        _bill(store, tmp_path, ground_truth, f"R-{n}", days_ago)
    assert recurring.detected(store)
    assert store.vendor_invoice_dates() == store._vendor_invoice_dates()
    assert not [key for key in summary_module._MEMO if key[0] == str(store.path) and key[1] == "vendor_invoice_dates"]


def test_a_process_working_an_answer_out_renews_its_hold_so_the_others_wait_for_it_and_not_past_a_stopped_one(
    tmp_path, monkeypatch
):
    """B-1807: a process working a shared answer out held it for the others 300 s, never renewed: one killed while
    working (out of memory, a forced restart) left every other worker asking for it waiting the whole ANSWER_WAIT
    (30 s) on a queue visit, again after each change, for five minutes. The hold is short and renewed while the work
    goes on: one that stopped is passed over within ANSWER_LEASE, and a long piece of work keeps it."""
    from ap_coder.core.store import reports

    assert reports.ANSWER_LEASE <= 15
    monkeypatch.setattr(reports, "ANSWER_LEASE", 1.2)  # (room for a slow runner's renewals: ~150 ms a write)
    monkeypatch.setattr(reports, "ANSWER_RENEW", 0.3, raising=False)
    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    working = threading.Event()

    def slow() -> list[str]:
        working.set()
        time.sleep(3.0)  # longer than the hold
        return ["first"]

    first = Shared(store, "slow", slow, version="t-1", topic="", changes=("vendors",))
    second = Shared(store, "slow", lambda: ["second"], version="t-1", topic="", changes=("vendors",))
    monkeypatch.setattr(reports, "OWNER", "worker 1")
    process("worker 1")
    found = []
    one = threading.Thread(target=lambda: found.append(first()))
    one.start()
    assert working.wait(5)
    time.sleep(1.6)  # past the hold's first expiry
    monkeypatch.setattr(reports, "OWNER", "worker 2")
    process("worker 2")
    assert second() == ["first"] and second.worked == 0  # waited for the one working it out, then took its answer
    one.join(10)
    assert found == [["first"]] and first.worked == 1


def test_an_answer_the_database_was_too_busy_to_keep_is_kept_once_it_is_free_and_the_others_take_it(
    tmp_path, monkeypatch
):
    """B-1808: a process that worked a shared answer out while another writer held the database (a bulk approval, an
    import, a backup) could neither keep it nor let go of its hold, each waiting 200 ms at most: the others asking
    then waited the whole ANSWER_WAIT for an answer that never came, then worked it out themselves. What the
    database was too busy for is done as soon as it is free: the others take that answer."""
    import sqlite3

    from ap_coder.core.store import reports

    monkeypatch.setattr(reports, "ANSWER_WAIT", 3.0)
    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    working = threading.Event()

    def slow() -> list[str]:
        working.set()
        time.sleep(0.3)
        return ["first"]

    first = Shared(store, "slow", slow, version="t-1", topic="", changes=("vendors",))
    second = Shared(store, "slow", lambda: ["second"], version="t-1", topic="", changes=("vendors",))
    monkeypatch.setattr(reports, "OWNER", "worker 1")
    process("worker 1")
    found = []
    one = threading.Thread(target=lambda: found.append(first()))
    one.start()
    assert working.wait(5)
    writer = sqlite3.connect(store.path, timeout=30, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")  # another worker's long write, while worker 1 ends its work
    one.join(10)
    assert found == [["first"]]
    time.sleep(0.5)
    writer.execute("COMMIT")
    writer.close()
    monkeypatch.setattr(reports, "OWNER", "worker 2")
    process("worker 2")
    began = time.monotonic()
    assert second() == ["first"] and second.worked == 0
    assert time.monotonic() - began < 2


def _settled_rows(tmp_path, monkeypatch):
    """(the clock, the store, a fresh read of the vendors, the settled shared answer of them, plain SQL)."""
    from ap_coder.core.store import reports

    clock = [1000.0]
    monkeypatch.setattr(reports, "wall", lambda: clock[0])
    store = Store(tmp_path / "ap.db")

    def rows() -> list[str]:
        with sqlite3.connect(store.path) as conn:
            return [r[0] for r in conn.execute("SELECT vendor_key FROM vendors ORDER BY vendor_key")]

    def ask(compute=rows):
        return store.shared_answer("rows", compute, version="t-1", topic="", changes=("vendors",), settle=30)

    def plain(sql: str) -> None:
        with sqlite3.connect(store.path) as conn:
            conn.execute(sql)

    return clock, store, rows, ask, plain


def test_the_answer_before_shown_while_another_works_it_out_is_never_older_than_its_settle_and_the_wait(
    tmp_path, monkeypatch
):
    """B-1838: B-1834's answer before, shown while another thread or process works a settled answer out again, could
    be of any age: asked again an hour after a change, the second asker was shown the answer of before the change at
    once. It is shown only while it began less than its settle and ANSWER_WAIT ago; an older one is waited for."""
    clock, store, rows, ask, plain = _settled_rows(tmp_path, monkeypatch)
    assert ask() == []
    clock[0] = 1010
    plain("INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('acme', 'Acme', 'x')")
    clock[0] = 5000  # an hour later: nobody asked meanwhile
    began, finish = threading.Event(), threading.Event()
    worker = threading.Thread(target=lambda: ask(lambda: began.set() or finish.wait(10) and rows()))
    worker.start()
    assert began.wait(5)
    threading.Timer(0.5, finish.set).start()  # (the work under way takes half a second)
    try:
        assert ask() == ["acme"]
    finally:
        finish.set()
        worker.join()


def test_a_settled_answer_asked_while_the_database_is_busy_and_no_one_works_it_out_is_worked_out(tmp_path, monkeypatch):
    """B-1838: with the database held by a long write (a bulk approval, an import), the hold on an answer could not be
    taken, and B-1834 took that for another process working it out: the latest kept, of any age, was shown. Only a
    hold another process has stands for that; with none, the answer is worked out from what is committed."""
    from ap_coder.core.store import reports

    clock, store, rows, ask, plain = _settled_rows(tmp_path, monkeypatch)
    assert ask() == []
    plain("INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('acme', 'Acme', 'x')")
    clock[0] = 9000
    monkeypatch.setattr(reports, "_KEPT", {})  # (another process: nothing of its own)
    writer = sqlite3.connect(store.path, isolation_level=None, timeout=0)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO settings (key, value) VALUES ('busy', '1')")
    try:
        assert ask() == ["acme"]
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_a_process_waits_for_an_answer_another_is_still_working_out_rather_than_work_it_out_twice(
    tmp_path, monkeypatch
):
    """B-1914: a process asking for an answer another was working out waited ANSWER_WAIT (30 s), then worked it out
    itself: month-end's first, 9 s of CPU on its own, took more than that under 100 people, so each worker asking
    worked it out again, each slowing the others (55 s pages in a load run). While the other's hold is renewed (it is
    alive and working, B-1807) it is waited for, up to ANSWER_WAIT_MOST; one that stopped is still passed over."""
    from ap_coder.core.store import reports

    monkeypatch.setattr(reports, "ANSWER_WAIT", 0.3)
    monkeypatch.setattr(reports, "ANSWER_LEASE", 1.5)  # (room for a slow runner's renewals: ~150 ms a write)
    monkeypatch.setattr(reports, "ANSWER_RENEW", 0.3)
    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    working = threading.Event()

    def slow() -> list[str]:
        working.set()
        time.sleep(2.0)  # longer than the wait, and than one hold unrenewed
        return ["first"]

    first = Shared(store, "slow", slow, version="t-1", topic="", changes=("vendors",))
    second = Shared(store, "slow", lambda: ["second"], version="t-1", topic="", changes=("vendors",))
    monkeypatch.setattr(reports, "OWNER", "worker 1")
    process("worker 1")
    found = []
    one = threading.Thread(target=lambda: found.append(first()))
    one.start()
    assert working.wait(5)
    monkeypatch.setattr(reports, "OWNER", "worker 2")
    process("worker 2")
    assert second() == ["first"] and second.worked == 0
    one.join(10)
    assert found == [["first"]]


@pytest.mark.parametrize("when", ["before its hold is asked for", "while it waits"])
def test_an_answer_kept_just_as_its_maker_lets_go_is_taken_never_worked_out_again(tmp_path, monkeypatch, when):
    """B-1919: a process waiting for the answer another was working out read the answers kept (none yet), then looked
    whether the other still held the work: kept and let go of between those two looks, it worked the answer out
    again itself (Windows CI, B-1808's test). The same between its first look and its asking for the hold: free,
    the hold was its own, and it worked it out again. Whenever the hold is found gone, or taken, the answers kept
    are looked at once more first."""
    from ap_coder.core.store import reports

    monkeypatch.setattr(reports, "ANSWER_WAIT", 3.0)
    monkeypatch.setattr(reports, "ANSWER_POLL", 0.01)
    _Processes(monkeypatch)("worker 2")
    monkeypatch.setattr(reports, "OWNER", "worker 2")
    store = Store(tmp_path / "ap.db")
    second = Shared(store, "slow", lambda: ["second"], version="t-1", topic="", changes=("vendors",))
    lease = f"answer/slow/{reports.asked_key(None)}"
    assert store.take_lease(lease, "worker 1", reports.ANSWER_LEASE)  # worker 1 works it out
    real, looks = store.kept_answer, []

    def look(name, key, version):
        found = real(name, key, version)
        looks.append(found)
        if len(looks) == (1 if when.startswith("before") else 2):  # worker 1 keeps it and lets go just after this look
            stamp, _ = store._answer_stamp("", ("vendors",))
            assert store.keep_answer(name, key, version, stamp, ["first"], time.time())
            store.drop_lease(lease, "worker 1")
        return found

    monkeypatch.setattr(store, "kept_answer", look)
    assert second() == ["first"] and second.worked == 0


# --- A settle that grows with the answer's own work (D-197) ---------------------------------------------------------


def test_a_settle_grows_with_the_answers_work_up_to_its_cap_and_never_for_an_exact_answer():
    """D-197: an answer a page only shows is taken after a change for its settle, or ``SETTLE_SHARE`` times as long as
    its work took when that is longer, never past ``SETTLE_MOST`` for it (a work slow once, its cache cold); an exact
    answer is never settled; one whose work is not known (kept by an older AP Coder) or a clock set back meanwhile has
    the plain settle."""
    from ap_coder.core.store.reports import SETTLE_MOST, SETTLE_SHARE, settle_for

    assert (SETTLE_SHARE, SETTLE_MOST) == (10, 900.0)
    assert settle_for(30, 0.05) == settle_for(30, None) == settle_for(30, -40) == 30  # (a small database: as before)
    assert settle_for(30, 20) == 200 and settle_for(5, 2.5) == 25
    assert settle_for(30, 3600) == SETTLE_MOST and settle_for(1200, 3600) == 1200
    assert settle_for(0, 20) == settle_for(0.0, 3600) == settle_for(0, None) == 0


def test_a_shared_answer_whose_work_was_long_settles_for_ten_times_it_in_every_process_but_never_past_a_restore(
    tmp_path, monkeypatch
):
    """D-197: Spend's figures took 20 s at 300,000 invoices against their 30 s settle, so under a stream of changes some
    web worker worked them out almost all the time, and its other requests waited behind it. The work an answer took
    is kept with it (``report_answers``, for the other processes); after a change it is taken for ten times that, in
    the process that made it and in another one; past that it is worked out again; asked exactly, at once; after a
    restore (another ``summary_id``), never, however recent."""
    from ap_coder.core.store import reports

    clock, worked = [1000.0], []
    monkeypatch.setattr(reports, "wall", lambda: clock[0])
    process = _Processes(monkeypatch)
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")

    def slow() -> list[str]:
        worked.append(clock[0])
        clock[0] += 20  # (its work takes 20 s)
        return [r[1] for r in _vendor_rows(store)]

    def ask(where: Store = store, settle: float = 30):
        return where.shared_answer("rows", slow, version="t-1", topic="", changes=("vendors",), settle=settle)

    def add(name: str) -> None:
        _plain(store, "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES (?, ?, ?)", name, name, NOW)

    process("worker 1")
    assert ask() == [] and worked == [1000]
    assert other.kept_answer("rows", reports.asked_key(None), "t-1")[2:] == (1000.0, 20.0)  # (began, its work)
    add("acme")
    clock[0] = 1199
    assert ask() == [] and len(worked) == 1  # its own, 199 s after its work began: past the plain 30 s
    process("worker 2")
    assert ask(other) == [] and len(worked) == 1  # worker 1's, from the database
    clock[0] = 1200
    assert ask(other) == ["acme"] and worked == [1000, 1200]  # 200 s: worked out again
    process("worker 1")
    assert ask() == ["acme"] and len(worked) == 2  # (worker 2's: worker 1's own past its settle)
    add("beta")
    assert ask(settle=0) == ["acme", "beta"] and len(worked) == 3  # exact: at once
    backup = store.backup_now("manual")
    add("gamma")
    assert ask(settle=0) == ["acme", "beta", "gamma"] and len(worked) == 4
    store.restore_from(backup)
    clock[0] += 5
    for name in ("worker 2", "worker 1"):  # (each with an answer of before the restore, 5 s old)
        process(name)
        assert ask() == ["acme", "beta"] and len(worked) == 5, name


def test_an_answer_kept_without_its_work_by_an_older_ap_coder_settles_for_the_plain_settle(tmp_path, monkeypatch):
    """D-197: a row of ``report_answers`` written before the work was kept with it (``lasted`` empty: an AP Coder of
    the same version before this change) is taken, and settles for the caller's ``settle`` alone."""
    from ap_coder.core.store import reports

    clock = [1000.0]
    monkeypatch.setattr(reports, "wall", lambda: clock[0])
    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")
    settled = Shared(store, "rows", lambda: _vendor_rows(store), version="t-1", topic="", changes=("vendors",),
                     settle=30)  # fmt: skip
    stamp = json.dumps(store._answer_stamp("", ("vendors",))[0])
    _plain(store, "INSERT INTO report_answers (name, asked, version, stamp, value, made_at) VALUES ('rows', ?, ?, ?, "
                  "'[]', 990.0)", reports.asked_key(None), reports._version("t-1"), stamp)  # fmt: skip
    assert store.kept_answer("rows", reports.asked_key(None), "t-1")[2:] == (990.0, None)
    process("worker 1")
    assert settled() == [] and settled.worked == 0  # (taken as it is)
    _plain(store, "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('acme', 'Acme', ?)", NOW)
    clock[0] = 1019
    process("worker 2")
    assert settled() == [] and settled.worked == 0  # 29 s after its work began
    clock[0] = 1020
    process("worker 3")
    assert settled() == _vendor_rows(store) != [] and settled.worked == 1  # 30 s: worked out again


def test_the_answer_before_shown_while_another_works_it_out_again_is_bound_by_its_grown_settle_and_the_wait(
    tmp_path, monkeypatch
):
    """D-197 with B-1834 and B-1838: while another process works a settled answer out again, the one before is shown
    only while its work began less than its settle (grown with its work: 200 s for 20 s) and ANSWER_WAIT ago; an
    older one, or one of another database, is waited for."""
    from ap_coder.core.store import reports

    clock = [1000.0]
    monkeypatch.setattr(reports, "wall", lambda: clock[0])
    process = _Processes(monkeypatch)
    store = Store(tmp_path / "ap.db")

    def slow() -> list[list]:
        clock[0] += 20
        return _vendor_rows(store)

    settled = Shared(store, "rows", slow, version="t-1", topic="", changes=("vendors",), settle=30)
    process("worker 1")
    before = settled()
    assert before == [] and settled.worked == 1
    lease = f"answer/rows/{reports.asked_key(None)}"
    bound = 1000 + 200 + reports.ANSWER_WAIT  # (its work began at 1000 and took 20 s)
    for n, (at, shown, restored) in enumerate(((bound - 1, True, False), (bound, False, False), (1100, False, True))):
        process(f"worker {n + 2}")
        _plain(store, "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES (?, 'V', ?)", f"v{n}", NOW)
        if restored:  # (what is kept is of the database before a restore: another summary_id)
            _plain(store, "UPDATE settings SET value = 'restored' WHERE key = 'summary_id'")
        clock[0] = at
        assert store.take_lease(lease, "another process", 60)  # (it works the answer out again)
        found, waiting = [], _waiting(store, monkeypatch)
        asking = threading.Thread(target=lambda found=found: found.append(settled()))
        asking.start()
        if shown:
            asking.join(10)
            assert found == [before] and settled.worked == 1, at  # at once, not waited for
        else:
            assert waiting.wait(10), at
            assert not found, at  # (waiting)
            store.keep_answer("rows", reports.asked_key(None), "t-1", store._answer_stamp("", ("vendors",))[0],
                              ["theirs"], clock[0], 20.0)  # fmt: skip
            asking.join(10)
            assert found == [["theirs"]] and settled.worked == 1, at
        store.drop_lease(lease, "another process")
        before = found[0]
        _plain(store, "UPDATE report_answers SET made_at = 1000.0, lasted = 20.0")  # (each turn: the same age)


def test_an_answer_a_process_keeps_for_itself_settles_for_ten_times_its_work_capped_and_never_past_a_restore(
    tmp_path, monkeypatch
):
    """D-197 for ``summary_memo``: once what it depends on changed, a settled answer is taken for its settle or ten
    times as long as its work took, at most ``SETTLE_MOST`` (a work once slow does not hold a stale answer for
    hours); an exact one follows every change whatever its work; a restore's new ``summary_id`` is never settled."""
    from ap_coder.core.store import reports

    clock, work = [1000.0], [20.0]
    monkeypatch.setattr(summary_module, "monotonic", lambda: clock[0])
    store = Store(tmp_path / "ap.db")

    def keys() -> list[str]:
        clock[0] += work[0]
        with store._conn() as conn:
            return [r[0] for r in conn.execute("SELECT vendor_key FROM vendors ORDER BY rowid")]

    settled = Kept(store, "vendors", keys, topic="", changes="vendors", settle=5)
    exact = Kept(store, "exact", keys, topic="", changes="vendors")

    def add(n: int) -> None:
        _plain(store, "INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES (?, 'V', ?)", f"v{n}", NOW)

    assert settled() == [] and exact() == [] and settled.worked == exact.worked == 1  # (began at 1000)
    add(1)
    clock[0] = 1199
    assert settled() == [] and settled.worked == 1  # 199 s: settled for 200
    assert exact() == ["v1"] and exact.worked == 2  # (exact: at once)
    clock[0] = 1200
    work[0] = 100.0  # (a cold cache: 1,000 s, held for 900)
    assert settled() == ["v1"] and settled.worked == 2  # 200 s: worked out again, from 1200
    add(2)
    clock[0] = 1200 + reports.SETTLE_MOST - 1
    assert settled() == ["v1"] and settled.worked == 2
    clock[0] = 1200 + reports.SETTLE_MOST
    work[0] = 20.0
    assert settled() == ["v1", "v2"] and settled.worked == 3  # 15 minutes: worked out again, from 2100
    backup = store.backup_now("manual")
    add(3)
    store.restore_from(backup)
    clock[0] += 5
    assert settled() == ["v1", "v2"] and settled.worked == 4  # (another summary_id: not the one 5 s old)


def test_the_vendors_pages_totals_settle_but_every_other_reader_of_them_is_exact(tmp_path, ground_truth, monkeypatch):
    """D-197: the Vendors page's counts and spend by vendor (``_vendor_totals``, 20% of a web worker's CPU with
    Spend's at 300,000 invoices, worked out again at every change) only show: once an invoice changed, the page takes
    the totals kept for ``VENDORS_SETTLE`` (or ten times their work). The Statements page, the JD Edwards worksheet and
    a vendor's controls read them exactly, and a vendor opened that came in after the page's totals opens."""
    from ap_coder.core.store.vendors import VENDORS_SETTLE
    from tests.webclient import client_for

    clock = [1000.0]
    monkeypatch.setattr(summary_module, "monotonic", lambda: clock[0])
    store = Store(tmp_path / "ap.db")
    _invoice(store, tmp_path, ground_truth, "A-1", "Acme Supply")
    shown = store.vendor_summaries(settled=True)
    assert [v["invoices"] for v in shown] == [1]
    _invoice(store, tmp_path, ground_truth, "A-2", "Acme Supply")
    _invoice(store, tmp_path, ground_truth, "B-1", "Beta Parts")
    clock[0] = 1000 + VENDORS_SETTLE - 1
    assert store.vendor_summaries(settled=True) == shown  # (settled)
    client = client_for(store)
    page = client.get("/vendors").text
    assert "Acme Supply" in page and "Beta Parts" not in page
    with sqlite3.connect(store.path) as conn:
        (beta,) = conn.execute("SELECT vendor_key FROM invoices WHERE vendor_name = 'Beta Parts'").fetchone()
    opened = client.get("/vendors", params={"open": beta}).text
    assert "Beta Parts" in opened and 'id="vendor"' in opened
    exact = store.vendor_summaries()
    assert sorted(v["invoices"] for v in exact) == [1, 2]
    clock[0] = 1000 + VENDORS_SETTLE
    _invoice(store, tmp_path, ground_truth, "B-2", "Beta Parts")
    assert store.vendor_summaries(settled=True) == exact  # (the exact read is the one kept: settled from it)
    clock[0] += VENDORS_SETTLE
    assert store.vendor_summaries(settled=True) == store.vendor_summaries()
