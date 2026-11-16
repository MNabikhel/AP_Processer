"""The roles an Admin makes (core/store/roles.py, D-250): made, changed and deleted with their rules, given to people,
each change in the Activity log; the last account that manages the people keeps that, and an Admin never takes it
from themselves; the roles go with the accounts across a restore and from an older database."""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from ap_coder.core.audit import describe
from ap_coder.core.store import SCHEMA_VERSION, Store
from ap_coder.core.store.roles import OWN_USERS
from ap_coder.core.store.users import LAST_ADMIN
from tests.core.test_migrations import OLD, _old_database, shape

GOOD = "correct horse battery"
LEAD = ["view", "activity", "import", "code", "approve", "final_approve", "export"]


@pytest.fixture
def people(store):
    boss = store.add_user("boss", GOOD, role="admin", display_name="Pat Boss", must_change=False)
    ann = store.add_user("ann", GOOD, role="clerk", display_name="Ann Lee", must_change=False)
    return store, boss, ann


def test_a_role_is_made_given_changed_and_deleted_each_in_the_activity_log(people):
    store, boss, ann = people
    key = store.add_role("  AP   lead ", reversed(LEAD), actor="Pat Boss")
    assert key == "role-1" and store.role_info(key)["name"] == "AP lead"
    assert store.role_info(key)["permissions"] == LEAD  # kept in the order of the list, whatever was ticked first
    store.set_role(ann, key, actor="Pat Boss", by_user=boss)
    found = store.user(ann)
    assert (found["role"], found["role_label"], found["permissions"]) == (key, "AP lead", LEAD)
    store.edit_role(key, "AP lead", [*LEAD, "post"], actor="Pat Boss", by_user=boss)
    store.edit_role(key, "AP leads", [p for p in LEAD if p != "export"] + ["post"], actor="Pat Boss", by_user=boss)
    assert store.user(ann)["permissions"] == ["view", "activity", "import", "code", "approve", "final_approve", "post"]
    with pytest.raises(ValueError, match="AP leads is the role of 1 account"):
        store.delete_role(key, actor="Pat Boss")
    store.set_role(ann, "clerk", actor="Pat Boss", by_user=boss)
    store.delete_role(key, actor="Pat Boss")
    assert store.role_info(key) is None and store.custom_roles() == []
    said = [(e["action"], describe(e), e["actor"]) for e in reversed(store.events(actions=[
        "role_created", "role_edited", "role_deleted", "role_changed"]))]  # fmt: skip
    assert said == [
        ("role_created", "AP lead: See invoices and reports, See the activity log, Bring invoices in, Review and code "
         "invoices, Approve invoices, Give the second approval, Make export batches", "Pat Boss"),
        ("role_changed", "ann: Clerk → AP lead", "Pat Boss"),
        ("role_edited", "AP lead: may now: Post to JD Edwards", "Pat Boss"),
        ("role_edited", "AP leads: renamed from AP lead; no longer: Make export batches", "Pat Boss"),
        ("role_changed", "ann: AP leads → Clerk", "Pat Boss"),
        ("role_deleted", "AP leads deleted", "Pat Boss"),
    ]  # fmt: skip
    # The log reads the same once the role is gone: the names were kept in it.
    assert store.events(actions=["role_changed"])[1]["detail"] == {
        "username": "ann", "from": "clerk", "to": key, "from_name": "Clerk", "to_name": "AP lead"}  # fmt: skip


def test_a_role_needs_a_name_of_its_own_and_something_to_do(people):
    store, _, _ = people
    for name, chosen, said in (("", ["view"], "Give the role a name"), ("admin", ["view"], "built-in role"),
                               ("read-ONLY", ["view"], "built-in role"), ("Lead", [], "Tick at least one"),
                               ("Lead", ["view", "fly"], "no permission 'fly'")):  # fmt: skip
        with pytest.raises(ValueError, match=said):
            store.add_role(name, chosen)
    store.add_role("Lead", ["view"])
    with pytest.raises(ValueError, match="already a role Lead"):
        store.add_role("lead", ["code"])
    with pytest.raises(ValueError, match="built-in role: it can't be changed"):
        store.edit_role("clerk", "Clerk", ["view"])
    with pytest.raises(ValueError, match="built-in role: it can't be deleted"):
        store.delete_role("approver")
    with pytest.raises(ValueError, match="no role 'role-9'"):
        store.set_role(1, "role-9")
    with pytest.raises(ValueError, match="no role 'boss-of-all'"):
        store.add_user("cy", GOOD, role="boss-of-all")


def test_an_admin_never_takes_manage_users_from_themselves(people):
    store, boss, ann = people
    keeper = store.add_role("People keeper", ["view", "users"])
    store.set_role(ann, keeper, by_user=boss)  # two can manage the people now
    with pytest.raises(ValueError, match=OWN_USERS):
        store.set_role(boss, "approver", by_user=boss)
    store.set_role(boss, keeper, by_user=boss)  # to another role that has it: fine
    with pytest.raises(ValueError, match=OWN_USERS):
        store.edit_role(keeper, "People keeper", ["view"], by_user=boss)  # their own role, through the role
    assert store.role_info(keeper)["permissions"] == ["view", "users"]
    store.set_role(boss, "admin", by_user=boss)
    store.set_role(ann, "clerk", by_user=boss)  # someone else's: fine while another manages the people


def test_the_last_account_that_manages_the_people_keeps_it(people):
    store, boss, ann = people
    keeper = store.add_role("People keeper", ["view", "users"])
    store.set_role(ann, keeper)
    store.set_role(boss, "clerk")  # by the console: Ann still manages the people
    with pytest.raises(ValueError, match=LAST_ADMIN):
        store.set_role(ann, "clerk")
    with pytest.raises(ValueError, match=LAST_ADMIN):
        store.deactivate_user(ann)
    with pytest.raises(ValueError, match=LAST_ADMIN):
        store.edit_role(keeper, "People keeper", ["view"])  # the role of the last ones
    store.set_role(boss, "admin")
    store.edit_role(keeper, "People keeper", ["view"])  # Pat manages them again: fine
    assert store.user(ann)["permissions"] == ["view"]


def test_a_role_made_here_goes_with_the_accounts_across_a_restore(people, tmp_path):
    store, boss, ann = people
    backup = store.backup_now()
    key = store.add_role("AP lead", LEAD)
    store.set_role(ann, key)
    store.restore_state(backup, keep_accounts=True)  # the backup knew neither the role nor Ann's having it
    assert store.user(ann)["role_label"] == "AP lead" and store.role_info(key)["permissions"] == LEAD


def test_a_database_of_schema_27_gets_the_roles_and_keeps_everyones_role(tmp_path):
    db = _old_database(OLD / "schema-27-1696f20.sql", tmp_path / "old")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO users (username, display_name, role, pw_hash, created_at) VALUES ('ann', 'Ann', "
                     "'approver', 'x', '2026-11-01')")  # fmt: skip
    store = Store(db)
    assert store.get_setting("schema_version") == str(SCHEMA_VERSION) == "29"
    assert store.find_user("ann")["permissions"] == store.role_info("approver")["permissions"]
    assert store.custom_roles() == [] and "table roles" in shape(db)


def test_a_database_of_schema_28_without_the_roles_is_brought_up_to_date(tmp_path):
    db = Store(tmp_path / "old" / "ap_coder.db").path
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DROP TABLE roles")
        conn.execute("UPDATE settings SET value = '28' WHERE key = 'schema_version'")
    store = Store(db)
    assert store.add_role("AP lead", ["view"]) == "role-1"
    assert shape(db) == shape(Store(tmp_path / "new" / "ap_coder.db").path)
    assert any(p.name.endswith("before-update-from-schema-28.db") for p in (db.parent / "backups").iterdir())
