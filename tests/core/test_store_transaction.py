"""Store transactions that nest (B-1995): each write of the store called inside ``Store.transaction()``, or inside
another write, joins it, kept with its commit and undone with its rollback; the few that must commit on their own (a
queue's claim, a try at logging in) refuse in words (``NestedWrite``), not with SQLite's "cannot start a transaction
within a transaction"; and a ``Store()`` built inside a transaction leaves it open (B-1996)."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from ap_coder.core.store import APPROVED, PENDING, NestedWrite, Store
from ap_coder.core.store.folder import read_file
from ap_coder.core.store.managers import CONFIRMED
from ap_coder.core.store.users import ADMIN, CLERK, LoginRefused

PASSWORD, NEW_PASSWORD = "plum-orchard-ledger-42", "maple-harbour-invoice-77"


class Undo(Exception):
    """Raised to roll the outer transaction back."""


def _file(store: Store, name: str = "a.pdf", text: bytes = b"%PDF-1.4 invoice") -> tuple:
    path = store.path.parent / name
    path.write_bytes(text)
    return read_file(path)


def _invoice(store: Store, gt: dict, number: str = "NW-1", validation: dict | None = None) -> int:
    return store.add_invoice(store.path.parent / f"{number}.pdf", {**gt, "invoice_number": number}, validation)


def _approved(store: Store, gt: dict) -> int:
    iid = _invoice(store, gt)
    store.approve_invoice(iid, store.get_invoice(iid)["ai_output"], "Ann")
    return iid


def _pw_matches(store: Store, uid: int, password: str) -> bool:
    from ap_coder.core.store.users import verify_password

    with store._conn() as conn:
        return verify_password(password, conn.execute("SELECT pw_hash FROM users WHERE id = ?", (uid,)).fetchone()[0])


def _paused(store: Store, column: str, ip: str) -> None:
    with store._conn() as conn:
        conn.execute(f"INSERT INTO login_attempts (username, ip, at, ok, {column}) VALUES ('ann', ?, ?, 0, ?)",
                     (ip, time.time(), time.time() + 900))  # fmt: skip


# --- The writes that join the caller's transaction: (what is set up first, the call, whether it is done) ----------


def _add_user(store, gt):
    return lambda: store.add_user("ann", PASSWORD, CLERK), lambda: store.find_user("ann") is not None


def _set_role(store, gt):
    uid = store.add_user("ann", PASSWORD, CLERK)
    return lambda: store.set_role(uid, ADMIN), lambda: store.user(uid)["role"] == ADMIN


def _deactivate_user(store, gt):
    uid = store.add_user("ann", PASSWORD, CLERK)
    return lambda: store.deactivate_user(uid), lambda: not store.user(uid)["active"]


def _activate_user(store, gt):
    uid = store.add_user("ann", PASSWORD, CLERK)
    store.deactivate_user(uid)
    return lambda: store.activate_user(uid), lambda: store.user(uid)["active"]


def _change_password(store, gt):
    uid = store.add_user("ann", PASSWORD, CLERK)
    return lambda: store.change_password(uid, PASSWORD, NEW_PASSWORD), lambda: _pw_matches(store, uid, NEW_PASSWORD)


def _reset_password(store, gt):
    uid = store.add_user("ann", PASSWORD, CLERK, must_change=False)
    return lambda: store.reset_password(uid, password=NEW_PASSWORD), lambda: _pw_matches(store, uid, NEW_PASSWORD)


def _unlock_username(store, gt):
    _paused(store, "user_locked_until", "10.0.0.5")
    return lambda: store.unlock_username("ann", "10.0.0.5"), lambda: not store.paused_usernames()


def _unlock_address(store, gt):
    _paused(store, "ip_locked_until", "10.0.0.6")
    return lambda: store.unlock_address("10.0.0.6"), lambda: not store.paused_addresses()


def _authenticator_secret(store, gt):
    uid = store.add_user("ann", PASSWORD, CLERK)

    def done():
        with store._conn() as conn:
            return bool(conn.execute("SELECT totp_secret FROM users WHERE id = ?", (uid,)).fetchone()[0])

    return lambda: store.authenticator_secret(uid), done


def _update_after_check(store, gt):
    iid = _invoice(store, gt)
    return (lambda: store.update_after_check(iid, None, None, (), {"checked": "yes"}),
            lambda: store.get_invoice(iid)["meta"].get("checked") == "yes")  # fmt: skip


def _recheck_duplicates(store, gt):
    first = _invoice(store, gt)
    flagged = {"issues": [{"severity": "error", "code": "DUPLICATE_INVOICE", "message": "a duplicate"}],
               "requires_review": True}  # fmt: skip
    second = _invoice(store, gt, validation=flagged)

    def done():
        return "DUPLICATE_INVOICE" not in str(store.get_invoice(second)["validation"])

    return lambda: store.reject_invoice(first, "Ann", "the duplicate"), done


def _replace_proposal(store, gt):
    iid = _invoice(store, gt)
    validation = store.get_invoice(iid)["validation"]
    return (lambda: store.replace_proposal(iid, {**gt, "invoice_number": "READ-AGAIN"}, validation, None),
            lambda: store.get_invoice(iid)["invoice_number"] == "READ-AGAIN")  # fmt: skip


def _create_export_batch(store, gt):
    iid = _approved(store, gt)
    return lambda: store.create_export_batch([iid], "csv", "Ann"), lambda: not store.unexported_approved()


def _add_rules_applied(store, gt):
    iid = _invoice(store, gt)
    change = {"line_number": 1, "gl_from": "", "cc_from": "", "gl_code": "6100", "cost_center": ""}
    return (lambda: store.add_rules_applied(iid, [change]),
            lambda: bool(store.get_invoice(iid)["meta"].get("rules_applied")))  # fmt: skip


def _answer_manager_request(store, gt):
    iid = _invoice(store, gt)
    rid = store.send_to_manager(iid, "Ann", manager_name="Bob", lines=gt["line_items"])
    return (lambda: store.answer_manager_request(rid, CONFIRMED, by="Bob", actor="Bob"),
            lambda: store.manager_request(rid)["status"] == CONFIRMED)  # fmt: skip


def _delete_invoice(store, gt):
    iid = _invoice(store, gt)
    return lambda: store.delete_invoice(iid, actor="Ann", keep_approved=True), lambda: store.get_invoice(iid) is None


def _final_approve(store, gt):
    store.set_setting("approval_limit", "10")
    iid = _approved(store, gt)
    assert store.get_invoice(iid)["status"] == PENDING
    return (lambda: store.final_approve(iid, "Sam", shown=store.latest_approval_id(iid)),
            lambda: store.get_invoice(iid)["status"] == APPROVED)  # fmt: skip


def _save_supplier_profile(store, gt):
    iid = _approved(store, gt)
    return (lambda: store.save_supplier_profile("acme", template={"fields": {"a": 1}}, invoice_id=iid),
            lambda: (store.get_supplier_profile("acme") or {}).get("template") == {"fields": {"a": 1}})  # fmt: skip


def _forget_if_withdrawn(store, gt):
    iid = _approved(store, gt)
    assert store.feedback_rows()
    before = store.approval_generation(iid)  # learning began before an approval that has been made again since
    return lambda: store.forget_if_withdrawn(iid, before - 1), lambda: not store.feedback_rows()


def _queue_imports(store, gt):
    file = _file(store)
    return lambda: store.queue_imports([file], source="upload"), lambda: store.imports_open()["waiting"] == 1


def _finish_import(store, gt):
    store.queue_imports([_file(store)], source="upload")
    taken = store.claim_import("a reader")
    return (lambda: store.finish_import(taken["id"], "a reader", "failed", error="unreadable"),
            lambda: store.imports_open()["reading"] == 0)  # fmt: skip


JOINED: dict[str, Callable] = {f.__name__[1:]: f for f in (
    _add_user, _set_role, _deactivate_user, _activate_user, _change_password, _reset_password, _unlock_username,
    _unlock_address, _authenticator_secret, _update_after_check, _recheck_duplicates, _replace_proposal,
    _create_export_batch, _add_rules_applied, _answer_manager_request, _delete_invoice, _final_approve,
    _save_supplier_profile, _forget_if_withdrawn, _queue_imports, _finish_import,
)}  # fmt: skip


@pytest.mark.parametrize("name", sorted(JOINED))
def test_a_write_inside_an_open_transaction_joins_it(store, ground_truth, name):
    call, done = JOINED[name](store, ground_truth)
    assert not done()
    with pytest.raises(Undo), store.transaction():
        store.set_setting("outer", "1")  # the transaction has written: it holds the write lock
        call()  # raised "cannot start a transaction within a transaction" before
        assert done()
        raise Undo
    assert store.get_setting("outer") == "" and not done()  # undone with the transaction it joined
    with store.transaction():
        store.set_setting("outer", "1")
        call()
    assert store.get_setting("outer") == "1" and done()  # and kept with its commit
    assert done() and Store(store.path).get_setting("outer") == "1"


# --- The writes that commit on their own -----------------------------------------------------------------------------


def _next_page_read(store, gt):
    store.queue_page_read(_invoice(store, gt), "scanned")
    return lambda: store.next_page_read(reader="a reader")


def _next_verification(store, gt):
    store.queue_verification(_invoice(store, gt), "asked")
    return lambda: store.next_verification(after_page_reader=False, reader="a reviewer")


def _next_azure_check(store, gt):
    store.queue_azure_check(_invoice(store, gt), "low-confidence")
    return lambda: store.next_azure_check()


def _claim_import(store, gt):
    store.queue_imports([_file(store)], source="upload")
    return lambda: store.claim_import("a reader")


def _authenticate(store, gt):
    store.add_user("ann", PASSWORD, CLERK)
    return lambda: store.authenticate("ann", "not the password at all", "10.0.0.7")


def _check_code(store, gt):
    uid = store.add_user("ann", PASSWORD, CLERK)
    store.authenticator_secret(uid)
    return lambda: store.check_code(uid, "000000", "10.0.0.7")


def _change_password_wrong(store, gt):
    uid = store.add_user("ann", PASSWORD, CLERK)
    return lambda: store.change_password(uid, "not the password at all", NEW_PASSWORD, ip="10.0.0.7")


ALONE: dict[str, Callable] = {f.__name__[1:]: f for f in (
    _next_page_read, _next_verification, _next_azure_check, _claim_import, _authenticate, _check_code,
    _change_password_wrong,
)}  # fmt: skip


@pytest.mark.parametrize("name", sorted(ALONE))
def test_a_write_that_commits_on_its_own_refuses_in_words_inside_a_transaction(store, ground_truth, name):
    call = ALONE[name](store, ground_truth)
    events = len(store.events())
    with store.transaction():
        store.set_setting("outer", "1")
        with pytest.raises(NestedWrite, match="commits on its own"):
            call()
        store.set_setting("after", "2")  # nothing of it done: the transaction goes on
    assert store.get_setting("outer") == "1" and store.get_setting("after") == "2"
    assert len(store.events()) == events
    with pytest.raises(NestedWrite), store.transaction(), store._conn():  # inside a write that has written nothing
        call()
    try:  # and outside any transaction, as before
        call()
    except (LoginRefused, ValueError):
        pass  # a wrong password or code: refused, and the try recorded
    assert len(store.events()) > events or name.startswith("next_") or name == "claim_import"


def test_a_try_at_logging_in_is_still_recorded_when_it_is_refused(store):
    store.add_user("ann", PASSWORD, CLERK)
    with pytest.raises(LoginRefused):
        store.authenticate("ann", "not the password at all", "10.0.0.7")
    assert [e["action"] for e in store.events(actions=["login_failed"])] == ["login_failed"]


# --- A Store built inside a transaction -----------------------------------------------------------------------------


def test_a_store_built_inside_a_transaction_leaves_it_open(store):
    with pytest.raises(Undo), store.transaction():
        store.set_setting("outer", "1")
        Store(store.path).set_setting("inner", "2")  # e.g. a helper that opens a Store of its own
        assert store.get_setting("outer") == "1" and store.get_setting("inner") == "2"
        raise Undo
    assert store.get_setting("outer") == "" and store.get_setting("inner") == ""  # the start-up committed it before


def test_a_store_built_inside_a_transaction_does_not_wait_for_its_lock(store):
    started = time.monotonic()
    with store.transaction():
        store.set_setting("outer", "1")  # the write lock is held from here to the commit
        Store(store.path)
    assert time.monotonic() - started < 5  # (the busy timeout is 30 s)


def test_a_store_built_inside_a_write_with_summary_work_left_never_waits_for_its_own_lock(tmp_path):
    """B-1965: B-1996's fix made a Store() built inside this thread's write start up on a connection of its own,
    "nothing due: no lock"; but with summary rows left to refresh (another connection's plain writes: the triggers
    mark them, the bench's seed does it) its start-up waited 30 s for the very write lock this thread held, then
    failed, undoing the caller's transaction. Inside a write, a database already up to date is not started up again."""
    import sqlite3
    import time

    store = Store(tmp_path / "private" / "ap_coder.db")
    iid = store.add_invoice(tmp_path / "a.pdf", {"vendor_name": "Acme", "invoice_number": "1", "grand_total": 10,
                                                 "line_items": []}, {})  # fmt: skip
    plain = sqlite3.connect(store.path)
    plain.execute("UPDATE invoices SET vendor_name = 'Acme Two' WHERE id = ?", (iid,))  # the triggers mark it
    plain.commit()
    plain.close()
    started = time.monotonic()
    with store.transaction():
        store.set_setting("outer", "1")  # the write lock held from here to the commit
        Store(store.path)
    assert time.monotonic() - started < 5 and store.get_setting("outer") == "1"


class _Undo(Exception):
    pass


@pytest.mark.parametrize("snapshot", ["summary_snapshot", "billing_moment"])
def test_a_transaction_inside_a_read_snapshot_is_undone_by_an_exception(tmp_path, snapshot):
    """B-1966: the read snapshots (``summary_snapshot``, ``learning.one_snapshot`` under ``billing_moment``) ended
    with ``commit`` whatever happened: a ``Store.transaction()`` or a joined write (D-192) inside one kept its
    writes when an exception left it, though a transaction promises all of it undone. An exception rolls back."""
    store = Store(tmp_path / "private" / "ap_coder.db")
    with pytest.raises(_Undo), getattr(store, snapshot)(), store.transaction():
        store.set_setting("written", "1")
        raise _Undo
    assert store.get_setting("written") == ""
    with pytest.raises(_Undo), store.summary_snapshot():
        store.add_user("probe", "correct horse battery staple", "clerk")  # begin_write: joins the read
        raise _Undo
    assert not any(u["username"] == "probe" for u in store.list_users())
