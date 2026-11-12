"""Accounts, passwords, lockout and sessions for server mode (core/store/users.py, D-97)."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from ap_coder.core import authenticator
from ap_coder.core.store import Store, users
from ap_coder.core.store.users import LOCKED, WRONG, WRONG_CODE, LoginRefused
from tests.webclient import enrol

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


def with_app(store: Store, username: str = "ann") -> int:
    """An account whose authenticator app is set up (D-146): a session is only such an account's."""
    uid = store.add_user(username, GOOD)
    enrol(store, uid)
    return uid


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
        store.add_user("sam", GOOD, role="owner")  # (not "manager": a role since D-138)
    assert store.find_user("PAT.tester")["id"] == uid
    assert store.events(actions=["user_added"])[0]["detail"]["username"] == "pat.tester"


# --- Logging in and lockout ----------------------------------------------------------------------------------------


def test_login_with_the_right_password_and_refused_with_a_wrong_one_or_an_unknown_name_alike(store, clock):
    uid = store.add_user("ann", GOOD, role="approver", display_name="Ann Lee")
    assert store.authenticate("ANN", GOOD, "10.0.0.5")["id"] == uid
    store.check_code(uid, authenticator.code(store.authenticator_secret(uid), clock.now), "10.0.0.5")  # (D-146)
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
    uid = with_app(store)
    token = store.start_session(uid, "10.0.0.5", "Firefox")
    assert len(token) >= 43  # 32 random bytes
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute("SELECT token_hash, ip, user_agent FROM sessions").fetchall()
    import hashlib

    assert rows == [(hashlib.sha256(token.encode()).hexdigest(), "10.0.0.5", "Firefox")]
    assert store.session_user(token)["id"] == uid
    assert store.session_user("made-up") is None and store.session_user("") is None


def test_a_session_ends_after_30_idle_minutes(store, clock):
    uid = with_app(store)
    token = store.start_session(uid)
    clock.now += 25 * 60
    assert store.session_user(token)  # used: the idle time starts again
    clock.now += 25 * 60
    assert store.session_user(token)
    clock.now += 30 * 60 + 1
    assert store.session_user(token) is None


def test_a_session_ends_after_12_hours_however_busy(store, clock):
    uid = with_app(store)
    token = store.start_session(uid)
    for _ in range(35):  # used every 20 minutes: 11 hours 40 minutes
        clock.now += 20 * 60
        assert store.session_user(token)
    clock.now += 20 * 60  # 12 hours
    assert store.session_user(token) is None


def test_logout_ends_the_session_and_a_password_change_ends_the_others(store, clock):
    uid = with_app(store)
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
    assert [e["action"] for e in store.events()][:3] == ["password_changed", "login_failed", "logout"]


def test_sessions_survive_a_restart(tmp_path, clock):
    first = Store(tmp_path / "ap_coder.db")
    token = first.start_session(with_app(first))
    assert Store(tmp_path / "ap_coder.db").session_user(token)["username"] == "ann"


def test_a_deactivated_users_sessions_end(store, clock):
    store.add_user("boss", GOOD, role="admin")
    uid = store.add_user("ann", GOOD)
    token = store.start_session(uid)
    store.deactivate_user(uid, actor="boss")
    assert store.session_user(token) is None
    store.activate_user(uid, actor="boss")
    assert store.user(uid)["active"]


def _last_seen(store, token):
    with sqlite3.connect(store.path) as conn:
        return conn.execute(
            "SELECT last_seen FROM sessions WHERE token_hash = ?", (users.token_hash(token),)
        ).fetchone()[0]


def test_a_session_lookup_never_waits_behind_another_writer_to_note_its_use(store, clock):
    uid = with_app(store)
    token = store.start_session(uid)
    clock.now += users.SEEN_EVERY + 1  # its last use is due to be written down
    writer = sqlite3.connect(store.path, isolation_level=None, check_same_thread=False)
    writer.execute("BEGIN IMMEDIATE")  # another request's write, a long one (an import, a month end)
    done = threading.Timer(2, writer.rollback)
    done.start()
    try:
        started = time.monotonic()
        assert store.session_user(token)["id"] == uid  # the page goes on at once
        waited = time.monotonic() - started
    finally:
        done.join()
        writer.close()
    assert waited < 1
    assert _last_seen(store, token) == clock.now - users.SEEN_EVERY - 1  # not written this time...
    clock.now += 60
    assert store.session_user(token) and _last_seen(store, token) == clock.now  # ...but by the next request


def _sessions(store) -> int:
    with sqlite3.connect(store.path) as conn:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


def test_the_first_request_after_a_session_expired_never_waits_behind_another_writer(store, clock):
    uid = with_app(store)
    token = store.start_session(uid)
    clock.now += 30 * 60 + 1  # unused for its idle minutes: the next request finds it ended
    writer = sqlite3.connect(store.path, isolation_level=None, check_same_thread=False)
    writer.execute("BEGIN IMMEDIATE")  # another request's write, a long one (an import, a month end)
    done = threading.Timer(2, writer.rollback)
    done.start()
    try:
        started = time.monotonic()
        assert store.session_user(token) is None  # off to the login page at once
        waited = time.monotonic() - started
    finally:
        done.join()
        writer.close()
    assert waited < 1
    assert _sessions(store) == 1  # not deleted this time...
    assert store.session_user(token) is None and _sessions(store) == 0  # ...but by the next look at it


def test_a_poll_reads_the_session_without_renewing_it(store, clock):
    uid = with_app(store)
    token = store.start_session(uid)
    for _ in range(3):  # a page's automatic polls: still logged in, but no one using it
        clock.now += 10 * 60 - 1
        assert store.session_user(token, renew=False)["id"] == uid
    clock.now += 4
    assert store.session_user(token, renew=False) is None and store.session_user(token) is None


# --- The second step: a code from an authenticator app (D-146) -------------------------------------------------------


def _code(secret: str, clock, steps: int = 0) -> str:
    return authenticator.code(secret, clock.now + 30 * steps)


def _set_up(store: Store, clock, username: str = "ann") -> tuple[int, str]:
    """An account that logged in once and set up its authenticator app; the clock then a minute on."""
    uid = store.add_user(username, GOOD)
    secret = store.authenticator_secret(uid)
    store.check_code(uid, _code(secret, clock), "10.0.0.5")
    clock.now += 60
    return uid, secret


def test_a_password_alone_is_not_a_login_its_code_is_then_the_app_is_set_up(store, clock):
    uid = store.add_user("ann", GOOD, display_name="Ann Lee")
    found = store.authenticate("ANN", GOOD, "10.0.0.5")
    assert found["id"] == uid and not found["totp_on"]
    assert store.user(uid)["last_login"] is None and not store.events(actions=["login"])
    secret = store.authenticator_secret(uid)
    assert store.authenticator_secret(uid) == secret  # the same until it is confirmed (the page loaded again)
    assert store.check_code(uid, _code(secret, clock), "10.0.0.5")["totp_on"]
    assert store.user(uid)["last_login"] and store.user(uid)["totp_on"]
    assert [e["action"] for e in store.events()][:2] == ["login", "authenticator_enrolled"]
    with pytest.raises(ValueError, match="set up"):  # its secret is never shown again
        store.authenticator_secret(uid)


def test_the_secret_is_kept_sealed_by_the_key_file_beside_the_database_not_in_it(store, clock):
    uid = store.add_user("ann", GOOD)
    secret = store.authenticator_secret(uid)
    key = (store.path.parent / authenticator.KEY_FILE).read_text(encoding="utf-8").strip()
    with sqlite3.connect(store.path) as conn:
        dumped = "\n".join(conn.iterdump())
    assert secret not in dumped and key not in dumped


def test_a_wrong_code_is_refused_and_counts_toward_the_lockout_the_password_restarts_nothing(store, clock):
    uid, secret = _set_up(store, clock)
    wrong = "000000" if _code(secret, clock) != "000000" else "111111"
    for _ in range(4):
        assert store.authenticate("ann", GOOD, "10.0.0.9")  # the right password: the count goes on
        with pytest.raises(LoginRefused) as refused:
            store.check_code(uid, wrong, "10.0.0.9")
        assert str(refused.value) == WRONG_CODE
    store.authenticate("ann", GOOD, "10.0.0.9")
    with pytest.raises(LoginRefused) as refused:
        store.check_code(uid, wrong, "10.0.0.9")
    assert str(refused.value) == LOCKED
    with pytest.raises(LoginRefused, match="paused"):  # its right code too, from there, while paused
        store.check_code(uid, _code(secret, clock), "10.0.0.9")
    with pytest.raises(LoginRefused, match="paused"):
        store.authenticate("ann", GOOD, "10.0.0.9")
    failed = store.events(actions=["login_failed"])
    assert len(failed) == 5 and {e["detail"]["why"] for e in failed} == {users.CODE_WHY}
    assert store.events(actions=["login_locked"])[0]["detail"]["username"] == "ann"
    assert store.check_code(uid, _code(secret, clock), "10.0.0.77")["username"] == "ann"  # elsewhere: fine


def test_the_refusal_of_a_code_says_it_may_have_been_used_already(store, clock):
    """B-2117: someone who logged out and in again within 30 seconds typed the code their app still showed and was
    told "That code doesn't match": it had matched, and was used. The one answer for both (nothing tells which) says
    so, and that the app shows a new one every 30 seconds."""
    uid, secret = _set_up(store, clock)
    typed = _code(secret, clock)
    assert store.check_code(uid, typed, "10.0.0.5")
    with pytest.raises(LoginRefused) as used:
        store.check_code(uid, typed, "10.0.0.5")
    with pytest.raises(LoginRefused) as wrong:
        store.check_code(uid, "000000" if typed != "000000" else "111111", "10.0.0.5")
    assert str(used.value) == str(wrong.value) == WRONG_CODE
    assert "or it was used already" in WRONG_CODE and "every 30 seconds" in WRONG_CODE


def test_a_code_already_used_is_refused(store, clock):
    uid, secret = _set_up(store, clock)
    typed = _code(secret, clock)
    assert store.check_code(uid, typed, "10.0.0.5")
    with pytest.raises(LoginRefused) as refused:
        store.check_code(uid, typed, "10.0.0.5")  # seen by someone looking over a shoulder, say
    assert str(refused.value) == WRONG_CODE
    with pytest.raises(LoginRefused):
        store.check_code(uid, _code(secret, clock, -1), "10.0.0.5")  # the one before it: older still
    clock.now += 30
    assert store.check_code(uid, _code(secret, clock), "10.0.0.5")  # the next one


@pytest.mark.parametrize("steps, accepted", [(-2, False), (-1, True), (1, True), (2, False)])
def test_a_phone_a_step_ahead_or_behind_is_accepted_two_steps_are_not(store, clock, steps, accepted):
    uid, secret = _set_up(store, clock)
    clock.now += 120  # past every step the setting up could have used
    if accepted:
        assert store.check_code(uid, _code(secret, clock, steps), "10.0.0.5")["username"] == "ann"
    else:
        with pytest.raises(LoginRefused):
            store.check_code(uid, _code(secret, clock, steps), "10.0.0.5")


def test_a_wrong_code_while_setting_up_is_refused_and_leaves_it_not_set_up(store, clock):
    uid = store.add_user("ann", GOOD)
    secret = store.authenticator_secret(uid)
    with pytest.raises(LoginRefused):
        store.check_code(uid, _code(secret, clock, 3), "10.0.0.5")
    assert not store.user(uid)["totp_on"] and store.authenticator_secret(uid) == secret


def test_an_admins_reset_of_the_authenticator_means_setting_it_up_again(store, clock):
    uid, old = _set_up(store, clock)
    token = store.start_session(uid)
    store.authenticate("ann", GOOD, "10.0.0.9")
    with pytest.raises(LoginRefused):
        store.check_code(uid, "000000" if _code(old, clock) != "000000" else "111111", "10.0.0.9")
    temporary = store.reset_authenticator(uid, actor="Boss", by_user=7)
    assert not store.user(uid)["totp_on"] and store.session_user(token) is None  # logged out everywhere
    with store._conn() as conn:  # their tries that didn't match are forgotten, as at a password reset
        assert not conn.execute("SELECT COUNT(*) FROM login_attempts WHERE ok = 0").fetchone()[0]
    # The password too (a phone lost or stolen may go with it): a temporary one, to change at the next login.
    assert len(temporary) >= 12 and store.user(uid)["must_change"]
    with pytest.raises(LoginRefused):
        store.authenticate("ann", GOOD, "10.0.0.5")
    assert store.authenticate("ann", temporary, "10.0.0.5")["must_change"]
    (given,) = store.events(actions=["password_reset"])
    assert given["actor"] == "Boss" and given["detail"]["by_user"] == 7  # (D-109: not that Admin's second approver)
    new = store.authenticator_secret(uid)
    assert new != old
    with pytest.raises(LoginRefused):
        store.check_code(uid, _code(old, clock), "10.0.0.5")  # the old phone's codes are worth nothing
    clock.now += 30
    assert store.check_code(uid, _code(new, clock), "10.0.0.5")["totp_on"]
    (reset,) = store.events(actions=["authenticator_reset"])
    assert reset["actor"] == "Boss" and reset["detail"]["username"] == "ann"


def test_a_session_of_an_account_without_its_authenticator_set_up_is_no_login(store, clock):
    uid = store.add_user("ann", GOOD)
    token = store.start_session(uid)  # one made by an older AP Coder, with a password alone
    assert store.session_user(token) is None


def test_a_login_half_way_is_kept_by_a_hash_ends_in_15_minutes_and_at_a_reset(store, clock):
    uid = store.add_user("ann", GOOD)
    pending = store.start_pending(uid)
    assert store.pending_user(pending)["id"] == uid and store.session_user(pending) is None
    with sqlite3.connect(store.path) as conn:
        assert pending not in "\n".join(conn.iterdump())
    clock.now += users.PENDING_SECONDS + 1
    assert store.pending_user(pending) is None
    pending = store.start_pending(uid)
    store.reset_password(uid, actor="Boss")
    assert store.pending_user(pending) is None
    pending = store.start_pending(uid)
    store.reset_authenticator(uid, actor="Boss")
    assert store.pending_user(pending) is None
    pending = store.start_pending(uid)
    store.end_pending(pending)
    assert store.pending_user(pending) is None


def test_without_the_key_file_a_code_is_refused_in_words_that_say_what_to_do(store, clock):
    uid, secret = _set_up(store, clock)
    (store.path.parent / authenticator.KEY_FILE).unlink()
    with pytest.raises(LoginRefused, match="reset your authenticator"):
        store.check_code(uid, _code(secret, clock), "10.0.0.5")


# --- The first sign-in: admin / admin while there is no account (D-243) ----------------------------------------------


def test_the_first_sign_in_is_kept_by_a_hash_ends_in_15_minutes_and_is_no_login_half_way(store, clock):
    token = store.start_first_sign_in()
    assert store.first_sign_in_open(token) and store.pending_user(token) is None and store.session_user(token) is None
    with sqlite3.connect(store.path) as conn:
        assert token not in "\n".join(conn.iterdump())
    clock.now += users.PENDING_SECONDS + 1
    assert not store.first_sign_in_open(token)
    token = store.start_first_sign_in()
    store.end_first_sign_in(token)
    assert not any(store.first_sign_in_open(t) for t in (token, "", None))
    assert not store.first_sign_in_open(store.start_pending(store.add_user("ann", GOOD)))  # another kind: not this


def test_the_first_admin_is_made_only_while_there_is_no_account(store):
    uid = store.add_first_admin(GOOD, "Pat Lee")
    made = store.user(uid)
    assert (made["username"], made["role"], made["name"]) == (users.FIRST_USERNAME, users.ADMIN, "Pat Lee")
    assert not made["must_change"] and not made["totp_on"]  # the password is their own; the app still to set up
    assert store.authenticate("admin", GOOD)["id"] == uid
    assert store.events(actions=["user_added"])[0]["actor"] == users.FIRST_ACTOR
    with pytest.raises(ValueError, match="first sign-in was just finished"):
        store.add_first_admin("another good long one", "Someone Else")
    assert store.user_count() == 1


def test_the_first_admins_password_follows_the_policy_and_admin_is_never_it(store):
    for password in (users.FIRST_PASSWORD, "admin admin admin", "my admin password", "Pat Lee"):
        with pytest.raises(ValueError):
            store.add_first_admin(password, "Pat Lee")
    assert store.user_count() == 0
