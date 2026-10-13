"""The database's session (one connection shared by every call inside, each still committed on its own) and the
learning memory kept between invoices until a lesson is added or removed."""

import sqlite3

import pytest

from ap_coder import store as store_mod
from ap_coder.store import Store


@pytest.fixture
def connects(monkeypatch):
    opened: list[str] = []
    real = sqlite3.connect

    def counting(*args, **kwargs):
        opened.append(str(args[0]))
        return real(*args, **kwargs)

    monkeypatch.setattr(store_mod.sqlite3, "connect", counting)
    return opened


def _lesson(n: int) -> dict:
    return {"vendor_name": "Northwind Supplies", "description": f"Copy paper case {n}", "gl_code": "6100",
            "cost_center": "", "amount": 10.0 + n, "date": "2026-01-02"}  # fmt: skip


def test_inside_a_session_every_call_shares_one_connection(tmp_path, connects):
    store = Store(tmp_path / "ap.db")
    store.set_setting("a", "1")
    outside = len(connects)
    with store.session():
        store.set_setting("b", "2")
        assert store.get_setting("a") == "1" and store.get_setting("b") == "2"
        store.log_event("looked")
        with store.session():  # a session inside a session is the same one
            store.set_setting("c", "3")
    assert len(connects) == outside + 1
    assert Store(tmp_path / "ap.db").get_setting("c") == "3"  # each call committed: another connection sees it


def test_a_call_inside_another_joins_its_transaction(tmp_path):
    store = Store(tmp_path / "ap.db")
    with store.session():
        with pytest.raises(RuntimeError), store._conn() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('outer', 'x')")
            store.set_setting("inner", "y")  # inside the outer block: not committed on its own
            raise RuntimeError("the outer block fails")
        assert store.get_setting("outer") == "" and store.get_setting("inner") == ""  # both rolled back
        store.set_setting("after", "z")
    assert Store(tmp_path / "ap.db").get_setting("after") == "z"


def test_outside_a_session_each_call_opens_its_own_connection(tmp_path, connects):
    store = Store(tmp_path / "ap.db")
    before = len(connects)
    store.set_setting("a", "1")
    store.get_setting("a")
    assert len(connects) == before + 2
    assert not store_mod._held_connections()


def test_a_database_deleted_during_a_session_is_made_again(tmp_path):
    store = Store(tmp_path / "ap.db")
    with store.session():
        store.set_setting("a", "1")
        for suffix in ("", "-wal", "-shm"):
            (tmp_path / f"ap.db{suffix}").unlink(missing_ok=True)
        store.set_setting("b", "2")  # a fresh database, not the deleted file still open
        assert store.get_setting("b") == "2"
    assert (tmp_path / "ap.db").exists() and Store(tmp_path / "ap.db").get_setting("b") == "2"


def test_the_learning_memory_is_read_again_only_when_it_changes(tmp_path, connects):
    store = Store(tmp_path / "ap.db")
    store.import_history([_lesson(n) for n in range(3)])
    first = store.feedback_rows()
    assert store.feedback_rows() is first  # unchanged: the rows already read
    store.import_history([_lesson(9)])
    added = store.feedback_rows()
    assert added is not first and len(added) == 4 and added[0]["description"] == "Copy paper case 9"
    store.forget_history()
    assert store.feedback_rows() == []
    assert store.feedback_rows(vendor_name="Northwind Supplies") == []
