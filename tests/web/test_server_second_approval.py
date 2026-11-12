"""In server mode the second approval needs a second account (D-98): the accounts' ids are compared, not the names
they show, and each approval records the account that gave it."""

from __future__ import annotations

import pytest

from ap_coder.core.store import PENDING, Store
from ap_coder.core.store.users import account_of, same_approver
from tests.webclient import add_clean


@pytest.fixture
def waiting(tmp_path):
    store = Store(tmp_path / "ap_coder.db")
    store.set_setting("approval_limit", "100")
    invoice_id = add_clean(store)
    inv = store.get_invoice(invoice_id)
    store.approve_invoice(invoice_id, inv["ai_output"], "Ann Lee", login="user:1")
    assert store.get_invoice(invoice_id)["status"] == PENDING
    return store, invoice_id


def test_the_approval_records_the_account_that_gave_it(waiting):
    store, invoice_id = waiting
    first = store.events(invoice_id, ["approved"])[0]["detail"]
    assert first["login"] == "user:1" and first["user_id"] == 1


def test_the_same_account_under_another_name_cannot_give_the_second_approval(waiting):
    store, invoice_id = waiting
    with pytest.raises(PermissionError, match="another account"):
        store.final_approve(invoice_id, "Somebody Else", login="user:1", shown=store.latest_approval_id(invoice_id))
    assert store.get_invoice(invoice_id)["status"] == PENDING


def test_another_account_with_the_same_name_may_give_it(waiting):
    """Two people called Ann Lee: two accounts, two people."""
    store, invoice_id = waiting
    store.final_approve(invoice_id, "Ann Lee", login="user:2", shown=store.latest_approval_id(invoice_id))
    final = store.events(invoice_id, ["final_approved"])[0]["detail"]
    assert final["user_id"] == 2 and final["first_approver"] == "Ann Lee"
    assert store.get_invoice(invoice_id)["status"] == "approved"


def test_without_accounts_the_names_and_logins_are_compared_as_before():
    assert account_of("user:12") == 12 and account_of("network 10.0.0.5") is None and account_of("") is None
    assert same_approver("Ann", "jsmith", "ann", "other")
    assert same_approver("Ann", "jsmith", "Bob", "JSMITH")
    assert not same_approver("Ann", "jsmith", "Bob", "bwong")
    assert not same_approver("Ann", "user:1", "Ann", "user:2")
    assert same_approver("Ann", "user:1", "Bob", "user:1")
    assert same_approver("Ann", "jsmith", "ann", "user:2")  # an approval from before the accounts: by name


def test_the_queue_says_waiting_for_your_second_approval_only_to_who_may_give_it(tmp_path):
    """B-2524: a Clerk was told "1 waiting for your second approval", though only an Approver can give it; and another
    account with the first approver's name was not told (the names were compared, not the accounts)."""
    from fastapi.testclient import TestClient

    from ap_coder.core.store import load_sample_setup
    from ap_coder.web.app import create_app
    from ap_coder.web.identity import SESSION_COOKIE
    from tests.webclient import enrol, web_settings

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    store.set_setting("approval_limit", "100")

    def session(username, role, shown):
        uid = store.add_user(username, "correct horse battery", role=role, display_name=shown, must_change=False)
        enrol(store, uid)
        return uid, store.start_session(uid)

    first_id, first = session("ann", "approver", "Ann")
    _, namesake = session("ann.b", "approver", "Ann")  # another person called Ann: another account
    _, clerk = session("carl", "clerk", "Carl")
    _, viewer = session("rita", "readonly", "Rita")
    iid = add_clean(store)
    add_clean(store, invoice_number="SECOND-ONE")  # one left to review, so the queue is not the welcome page
    store.approve_invoice(iid, store.get_invoice(iid)["ai_output"], "Ann", login=f"user:{first_id}")
    assert store.get_invoice(iid)["status"] == PENDING
    app = create_app(web_settings(), store, start_reader=False, server=True)
    client = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000))
    pill = "1 waiting for your second approval"

    def queue(token):
        return client.get("/review", headers={"Cookie": f"{SESSION_COOKIE}={token}"}).text

    assert pill in queue(namesake)
    assert pill not in queue(first)  # the first approval was theirs
    assert pill not in queue(clerk) and pill not in queue(viewer)
