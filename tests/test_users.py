"""Accounts, passwords, lockout and sessions for server mode (core/store/users.py, D-97)."""

from __future__ import annotations

import sqlite3

import pytest

from ap_coder.core.store import Store, users
from ap_coder.core.store.users import LOCKED, WRONG, LoginRefused

GOOD = "correct horse battery"  # 21 characters


@pytest.fixture
def clock(monkeypatch):
    """The store's clock, moved by hand: ``clock.now += 60``."""

    class Clock:
        now = 1_800_000_000.0

        def __call__(self) -> float:
            return self.now

    found = Clock()
    monkeypatch.setattr(users, "_clock", found)
    return found


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ap_coder.db")


# --- Password hashing and policy -----------------------------------------------------------------------------------


def test_a_password_hash_is_scrypt_with_its_settings_and_salt_and_round_trips():
    stored = users.hash_password(GOOD)
    kind, n, r, p, salt, digest = stored.split("$")
    assert (kind, n, r, p) == ("scrypt", "16384", "8", "1")
    import base64

    assert len(base64.b64decode(salt)) == 16 and len(base64.b64decode(digest)) == 64
    assert users.verify_password(GOOD, stored)
    assert not users.verify_password(GOOD + "!", stored)
    assert not users.verify_password("", stored)
    assert users.hash_password(GOOD) != stored  # a new salt each time


def test_a_damaged_or_foreign_hash_never_verifies():
    for stored in ("", "plain", "scrypt$1$8$1$AA==$AA==", "bcrypt$x", "scrypt$99999999$8$1$AA==$AA=="):
        assert not users.verify_password(GOOD, stored)


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("short", "at least 12 characters"),
        ("eleven char", "at least 12 characters"),
        ("pat.tester01", "not your username or your name"),
        ("PAT.TESTER01", "not your username or your name"),
        ("Pat Tester Smith", "not your username or your name"),
        (GOOD, ""),
    ],
)
def test_the_password_policy(password, problem):
    found = users.password_problem(password, "pat.tester01", "Pat Tester Smith")
    assert (problem in found) if problem else found == ""


def test_a_new_user_is_case_folded_must_change_their_password_and_is_never_stored_in_clear(store):
    uid = store.add_user("Pat.Tester", GOOD, role="clerk", display_name="Pat Tester", actor="console")
    found = store.user(uid)
    assert found["username"] == "pat.tester" and found["display_name"] == "Pat Tester"
    assert found["role"] == "clerk" and found["active"] and found["must_change"]
    assert "pw_hash" not in found
    with sqlite3.connect(store.path) as conn:
        assert GOOD not in str(conn.execute("SELECT * FROM users").fetchall())
    with pytest.raises(ValueError, match="already"):
        store.add_user("PAT.TESTER", GOOD + "x")
    with pytest.raises(ValueError, match="12 characters"):
        store.add_user("sam", "short")
    with pytest.raises(ValueError, match="role"):
        store.add_user("sam", GOOD, role="manager")
    assert store.find_user("PAT.tester")["id"] == uid
    assert store.events(actions=["user_added"])[0]["detail"]["username"] == "pat.tester"


# --- Logging in and lockout ----------------------------------------------------------------------------------------


def test_login_with_the_right_password_and_refused_with_a_wrong_one_or_an_unknown_name_alike(store, clock):
    uid = store.add_user("ann", GOOD, role="approver", display_name="Ann Lee")
    assert store.authenticate("ANN", GOOD, "10.0.0.5")["id"] == uid
    assert store.user(uid)["last_login"]
    for name in ("ann", "nobody"):
        with pytest.raises(LoginRefused) as refused:
            store.authenticate(name, "wrong password!", "10.0.0.5")
        assert str(refused.value) == WRONG
    actions = [e["action"] for e in store.events()]
    assert actions.count("login_failed") == 2 and "login" in actions


def test_five_failures_lock_the_username_from_that_address_for_15_minutes(store, clock):
    store.add_user("ann", GOOD)
    for _ in range(4):
        with pytest.raises(LoginRefused, match="match"):
            store.authenticate("ann", "wrong password!", "10.0.0.9")
    with pytest.raises(LoginRefused) as refused:
        store.authenticate("ann", "wrong password!", "10.0.0.9")
    assert str(refused.value) == LOCKED
    clock.now += 60
    with pytest.raises(LoginRefused) as refused:  # the right password too, from there, while locked
        store.authenticate("ann", GOOD, "10.0.0.9")
    assert str(refused.value) == LOCKED
    assert store.authenticate("ann", GOOD, "10.0.0.77")["username"] == "ann"  # from elsewhere: fine (D-101)
    clock.now += 15 * 60
    assert store.authenticate("ann", GOOD, "10.0.0.9")["username"] == "ann"
    assert store.events(actions=["login_locked"])[0]["detail"]["username"] == "ann"


def test_an_unknown_username_locks_the_same_way_so_the_message_says_nothing_about_who_exists(store, clock):
    store.add_user("ann", GOOD)
    said = {}
    for name in ("ann", "ghost"):
        for _ in range(5):
            with pytest.raises(LoginRefused) as refused:
                store.authenticate(name, "wrong password!", "10.1.0.1")
        said[name] = str(refused.value)
    assert said["ann"] == said["ghost"] == LOCKED


def test_thirty_failures_from_one_address_lock_that_address_across_usernames(store, clock):
    store.add_user("ann", GOOD)
    for n in range(30):
        with pytest.raises(LoginRefused):
            store.authenticate(f"guess{n}", "wrong password!", "10.0.0.66")
    with pytest.raises(LoginRefused) as refused:
        store.authenticate("ann", GOOD, "10.0.0.66")
    assert str(refused.value) == LOCKED
    assert store.authenticate("ann", GOOD, "10.0.0.67")["username"] == "ann"  # another address: fine
    clock.now += 15 * 60 + 1
    assert store.authenticate("ann", GOOD, "10.0.0.66")["username"] == "ann"


def test_failures_spread_over_more_than_15_minutes_do_not_lock(store, clock):
    store.add_user("ann", GOOD)
    for _ in range(8):
        with pytest.raises(LoginRefused, match="match"):
            store.authenticate("ann", "wrong password!", "10.0.0.5")
        clock.now += 4 * 60
    assert store.authenticate("ann", GOOD, "10.0.0.5")


def test_a_reset_unlocks_the_user_and_a_deactivated_user_cannot_log_in(store, clock):
    uid = store.add_user("ann", GOOD)
    for _ in range(5):
        with pytest.raises(LoginRefused):
            store.authenticate("ann", "wrong password!", f"10.0.0.{_}")
    temporary = store.reset_password(uid, actor="Admin")
    assert len(temporary) >= 12 and store.user(uid)["must_change"]
    assert store.authenticate("ann", temporary, "10.0.0.50")
    store.deactivate_user(uid, actor="Admin")
    with pytest.raises(LoginRefused) as refused:
        store.authenticate("ann", temporary, "10.0.0.51")
    assert str(refused.value) == WRONG


# --- Roles and the last Admin --------------------------------------------------------------------------------------


def test_the_last_active_admin_cannot_be_demoted_or_deactivated(store):
    boss = store.add_user("boss", GOOD, role="admin")
    with pytest.raises(ValueError, match="Admin"):
        store.set_role(boss, "clerk", actor="console")
    with pytest.raises(ValueError, match="Admin"):
        store.deactivate_user(boss, actor="console")
    other = store.add_user("other", GOOD, role="admin")
    store.set_role(boss, "clerk", actor="console")
    assert store.user(boss)["role"] == "clerk"
    with pytest.raises(ValueError, match="Admin"):
        store.deactivate_user(other, actor="console")
    with pytest.raises(ValueError, match="role"):
        store.set_role(other, "superuser", actor="console")
    assert store.events(actions=["role_changed"])[0]["detail"] == {"username": "boss", "from": "admin", "to": "clerk"}


# --- Sessions ------------------------------------------------------------------------------------------------------


def test_only_the_tokens_sha256_is_stored(store, clock):
    uid = store.add_user("ann", GOOD)
    token = store.start_session(uid, "10.0.0.5", "Firefox")
    assert len(token) >= 43  # 32 random bytes
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute("SELECT token_hash, ip, user_agent FROM sessions").fetchall()
    import hashlib

    assert rows == [(hashlib.sha256(token.encode()).hexdigest(), "10.0.0.5", "Firefox")]
    assert store.session_user(token)["id"] == uid
    assert store.session_user("made-up") is None and store.session_user("") is None


def test_a_session_ends_after_8_idle_hours(store, clock):
    uid = store.add_user("ann", GOOD)
    token = store.start_session(uid)
    clock.now += 7 * 3600
    assert store.session_user(token)  # used: the idle time starts again
    clock.now += 7 * 3600
    assert store.session_user(token)
    clock.now += 8 * 3600 + 1
    assert store.session_user(token) is None


def test_a_session_ends_after_7_days_however_busy(store, clock):
    uid = store.add_user("ann", GOOD)
    token = store.start_session(uid)
    for _ in range(27):  # used every 6 hours: 6 days 18 hours
        clock.now += 6 * 3600
        assert store.session_user(token)
    clock.now += 6 * 3600  # 7 days
    assert store.session_user(token) is None


def test_logout_ends_the_session_and_a_password_change_ends_the_others(store, clock):
    uid = store.add_user("ann", GOOD)
    here, there, elsewhere = (store.start_session(uid) for _ in range(3))
    assert here != there != elsewhere  # a new token for every login
    store.end_session(elsewhere, actor="ann")
    assert store.session_user(elsewhere) is None
    with pytest.raises(ValueError, match="current password"):
        store.change_password(uid, "not it at all", "a brand new password", keep=here)
    with pytest.raises(ValueError, match="different"):
        store.change_password(uid, GOOD, GOOD, keep=here)
    fresh = store.change_password(uid, GOOD, "a brand new password", keep=here)
    assert store.session_user(here) is None and store.session_user(there) is None  # a new token for this one
    assert fresh and store.session_user(fresh)["id"] == uid
    assert not store.user(uid)["must_change"]
    assert [e["action"] for e in store.events()][:2] == ["password_changed", "logout"]


def test_sessions_survive_a_restart(tmp_path, clock):
    first = Store(tmp_path / "ap_coder.db")
    token = first.start_session(first.add_user("ann", GOOD))
    assert Store(tmp_path / "ap_coder.db").session_user(token)["username"] == "ann"


def test_a_deactivated_users_sessions_end(store, clock):
    store.add_user("boss", GOOD, role="admin")
    uid = store.add_user("ann", GOOD)
    token = store.start_session(uid)
    store.deactivate_user(uid, actor="boss")
    assert store.session_user(token) is None
    store.activate_user(uid, actor="boss")
    assert store.user(uid)["active"]
