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
    with pytest.raises(ValueError, match="more than your own role"):
        store.set_role(boss, "admin", by_user=boss)  # nor gives themselves more (B-2561)
    store.set_role(boss, "admin")  # the console
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


def test_nobody_gives_or_manages_more_than_their_own_role_may_do(people):
    """A role with Manage users and roles alone (an HR role) can't widen itself, make or give a role with more, nor
    touch an account that may do more than it (B-2561); the console, the server's owner, is not held back."""
    store, boss, ann = people
    hr = store.add_role("HR", ["users"], by_user=boss)
    store.set_role(ann, hr, by_user=boss)
    old = store.add_user("old.boss", GOOD, role="admin", must_change=False)
    store.deactivate_user(old, by_user=boss)
    more = "more than your own role may do"
    for refused in (
        lambda: store.edit_role(hr, "HR", ["users", "settings"], by_user=ann),
        lambda: store.add_role("Boss", ["users", "settings"], by_user=ann),
        lambda: store.set_role(ann, "admin", by_user=ann),
        lambda: store.add_user("cy", GOOD, role="clerk", by_user=ann),
    ):
        with pytest.raises(ValueError, match=more):
            refused()
    theirs = "may do more than yours"
    for refused in (
        lambda: store.set_role(boss, "readonly", by_user=ann),
        lambda: store.deactivate_user(boss, by_user=ann),
        lambda: store.activate_user(old, by_user=ann),
        lambda: store.reset_password(boss, by_user=ann),
        lambda: store.reset_authenticator(boss, by_user=ann),
        lambda: store.sign_out_everywhere(boss, by_user=ann),
    ):
        with pytest.raises(ValueError, match=theirs):
            refused()
    assert store.role_info(hr)["permissions"] == ["users"] and [r["name"] for r in store.custom_roles()] == ["HR"]
    assert store.user(ann)["role"] == hr and store.user(boss)["role"] == "admin" and store.find_user("cy") is None
    assert store.user(boss)["active"] and not store.user(old)["active"]
    # What is within their own: fine.
    helper = store.add_role("HR helper", ["users"], by_user=ann)
    cy = store.add_user("cy", GOOD, role=helper, by_user=ann)
    store.reset_password(cy, by_user=ann)
    store.deactivate_user(cy, by_user=ann)
    # An Admin manages a Manager (reviewing invoices opens every manager's request: more than confirming them).
    store.set_role(cy, "manager", by_user=boss)
    store.reset_password(cy, by_user=boss)
    store.add_role("Confirms", ["confirm"], by_user=boss)
    store.set_role(ann, "admin")  # the server console: as it always was


def test_a_role_name_is_never_a_built_in_ones_look_alike_nor_a_roles_key(people):
    store, _, _ = people
    for name in ("Ａｄｍｉｎ", "ＣＬＥＲＫ", "role-7", "ROLE-2", "Role-12"):
        with pytest.raises(ValueError):
            store.add_role(name, ["view"])
    assert store.custom_roles() == []


def test_a_deleted_roles_key_is_never_given_to_another(people):
    store, _, _ = people
    first = store.add_role("Temp", ["view"])
    store.delete_role(first)
    second = store.add_role("Other", ["view"])
    assert (first, second) == ("role-1", "role-2") and store.role_info(first) is None
    assert store.add_role("Temp", ["code"]) == "role-3"  # its name is free again, its key never


def test_a_role_either_reviews_invoices_or_confirms_as_a_manager(people):
    store, boss, _ = people
    with pytest.raises(ValueError, match="Confirm coding as a manager"):
        store.add_role("Both", ["view", "code", "confirm"])
    key = store.add_role("Coder", ["view", "code"])
    with pytest.raises(ValueError, match="Confirm coding as a manager"):
        store.edit_role(key, "Coder", ["view", "code", "confirm"], by_user=boss)


def test_an_account_added_with_a_role_made_here_is_named_by_that_roles_name_in_the_activity_log(people):
    """B-2576: "ann.lee (Ann Lee) can log in as role-1": the role's key, not its name."""
    store, boss, _ = people
    key = store.add_role("AP lead", LEAD, by_user=boss)
    store.add_user("cy", GOOD, role=key, display_name="Cy Tan", by_user=boss)
    store.add_user("di", GOOD, role="approver", by_user=boss)
    (cy,) = [e for e in store.events(actions=["user_added"]) if e["detail"]["username"] == "cy"]
    (di,) = [e for e in store.events(actions=["user_added"]) if e["detail"]["username"] == "di"]
    store.edit_role(key, "AP leads", LEAD, by_user=boss)  # the log reads as it was said then
    assert describe(cy) == "cy (Cy Tan) can log in as AP lead"
    assert describe(di) == "di (di) can log in as Approver"


def test_nobody_deletes_a_role_that_may_do_more_than_theirs_and_the_refusal_reads_well(people):
    """B-2577: a role with Manage users and roles alone could delete a role it could not change, and the refusal to
    change one said "The role AP lead role may do more than yours"."""
    store, boss, ann = people
    hr = store.add_role("HR", ["users"], by_user=boss)
    store.set_role(ann, hr, by_user=boss)
    lead = store.add_role("AP lead", LEAD, by_user=boss)
    with pytest.raises(ValueError, match=r"^The AP lead role may do more than yours \(See invoices"):
        store.edit_role(lead, "AP lead", ["users"], by_user=ann)
    with pytest.raises(ValueError, match=r"^The AP lead role may do more than yours"):
        store.delete_role(lead, by_user=ann)
    assert store.role_info(lead) is not None
    helper = store.add_role("HR helper", ["users"], by_user=ann)
    store.delete_role(helper, by_user=ann)  # one within their own: fine
    store.delete_role(lead, by_user=boss)
    assert store.role_info(helper) is None and store.role_info(lead) is None


def test_a_role_that_works_on_invoices_sees_them_too(people):
    """B-2585: a role made without See invoices and reports, but to bring invoices in, code, approve or read the
    activity log, was led by its pages to invoices and reports it could not open. Such a role sees them: given when it
    is saved, and on reading one saved before; a role that only manages people, changes settings or confirms as a
    manager is left as it is, and so are the built-in ones."""
    store, boss, ann = people
    importer = store.add_role("Importer", ["import"])
    assert store.role_info(importer)["permissions"] == ["view", "import"]
    for name, alone in (("People desk", ["users"]), ("Settings only", ["settings"]), ("Confirms", ["confirm"])):
        assert store.role_info(store.add_role(name, alone))["permissions"] == alone
    store.edit_role(importer, "Importer", ["activity"], by_user=boss)
    assert store.role_info(importer)["permissions"] == ["view", "activity"]
    with store._conn() as conn:  # saved before the rule
        conn.execute("UPDATE roles SET permissions = '[\"approve\"]' WHERE key = ?", (importer,))
    store.set_role(ann, importer, by_user=boss)
    assert store.role_info(importer)["permissions"] == store.user(ann)["permissions"] == ["view", "approve"]
    assert [r["permissions"] for r in store.all_roles() if r["built_in"]] == [
        ["view", "activity"], ["view", "activity", "import", "code", "approve", "export"],
        ["view", "activity", "import", "code", "approve", "final_approve", "export", "post", "controls"],
        ["view", "activity", "import", "code", "approve", "final_approve", "export", "post", "controls", "setup",
         "undo", "settings", "users"], ["confirm"]]  # fmt: skip
