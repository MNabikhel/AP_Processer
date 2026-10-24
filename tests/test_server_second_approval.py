"""In server mode the second approval needs a second account (D-98): the accounts' ids are compared, not the names
they show, and each approval records the account that gave it."""

from __future__ import annotations

import pytest

from ap_coder.core.store import PENDING, Store
from ap_coder.core.store.users import account_of, same_approver

from .webclient import add_clean


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
        store.final_approve(invoice_id, "Somebody Else", login="user:1")
    assert store.get_invoice(invoice_id)["status"] == PENDING


def test_another_account_with_the_same_name_may_give_it(waiting):
    """Two people called Ann Lee: two accounts, two people."""
    store, invoice_id = waiting
    store.final_approve(invoice_id, "Ann Lee", login="user:2")
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
