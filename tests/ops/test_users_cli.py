"""``python -m ap_coder users``: the server console adds the first Admin and looks after the accounts (D-97)."""

from __future__ import annotations

import getpass
import re

import pytest

from ap_coder.core.store import Store
from ap_coder.ops import cli

GOOD = "correct horse battery"


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "ap_coder.db")


def typed(monkeypatch, *answers):
    given = iter(answers)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(given))


def test_the_first_admin_is_added_at_the_console_with_the_password_typed_twice(db, monkeypatch, capsys):
    typed(monkeypatch, GOOD, GOOD)
    assert cli.main(["--db", db, "users", "add", "Pat", "--role", "admin", "--name", "Pat Tester"]) == 0
    user = Store(db).find_user("pat")
    assert user["role"] == "admin" and user["display_name"] == "Pat Tester" and user["must_change"]
    assert Store(db).authenticate("pat", GOOD, "127.0.0.1")
    assert GOOD not in capsys.readouterr().out
    assert Store(db).events(actions=["user_added"])[0]["actor"] == "the server console"
    typed(monkeypatch, GOOD, GOOD)
    assert cli.main(["--db", db, "users", "add", "sam", "--admin"]) == 0
    assert Store(db).find_user("sam")["role"] == "admin"


def test_two_different_passwords_or_a_short_one_add_nobody(db, monkeypatch, capsys):
    typed(monkeypatch, GOOD, GOOD + "!")
    assert cli.main(["--db", db, "users", "add", "pat"]) == 1
    assert "not the same" in capsys.readouterr().err
    typed(monkeypatch, "short")
    assert cli.main(["--db", db, "users", "add", "pat"]) == 1
    assert "12 characters" in capsys.readouterr().err
    assert Store(db).user_count() == 0


def test_list_reset_role_and_deactivate(db, monkeypatch, capsys):
    store = Store(db)
    boss = store.add_user("boss", GOOD, role="admin")
    ann = store.add_user("ann", GOOD, role="clerk", display_name="Ann Lee")
    assert cli.main(["--db", db, "users", "list"]) == 0
    out = capsys.readouterr().out
    assert "boss" in out and "Admin" in out and "Ann Lee" in out and "Clerk" in out
    typed(monkeypatch, "a whole new password", "a whole new password")
    assert cli.main(["--db", db, "users", "reset", "ANN"]) == 0
    assert store.user(ann)["must_change"] and store.authenticate("ann", "a whole new password", "127.0.0.1")
    assert cli.main(["--db", db, "users", "role", "ann", "approver"]) == 0
    assert store.user(ann)["role"] == "approver"
    assert cli.main(["--db", db, "users", "deactivate", "ann"]) == 0
    assert not store.user(ann)["active"]
    assert cli.main(["--db", db, "users", "role", "boss", "clerk"]) == 1  # the last Admin
    assert "at least one active Admin" in capsys.readouterr().err
    assert store.user(boss)["role"] == "admin"
    assert cli.main(["--db", db, "users", "deactivate", "nobody"]) == 1


def test_reset_2fa_at_the_console_lets_a_last_admin_who_lost_their_phone_set_it_up_again(db, capsys):
    """The last Admin, their phone lost: no one can reset it on the Users page, so the server's console does (D-146)."""
    from tests.webclient import enrol

    store = Store(db)
    boss = store.add_user("boss", GOOD, role="admin")
    enrol(store, boss)
    token = store.start_session(boss)
    assert cli.main(["--db", db, "users", "list"]) == 0
    assert "codes set up" in capsys.readouterr().out
    assert cli.main(["--db", db, "users", "reset-2fa", "BOSS"]) == 0
    out = capsys.readouterr().out
    assert "set up their authenticator app again" in out and "logged out" in out
    temporary = re.search(r"temporary password: (\S+)", out).group(1)  # printed this once
    assert not store.user(boss)["totp_on"] and store.session_user(token) is None
    assert store.user(boss)["must_change"] and store.authenticate("boss", temporary, "127.0.0.1")
    assert store.events(actions=["authenticator_reset"])[0]["actor"] == "the server console"
    assert cli.main(["--db", db, "users", "list"]) == 0
    assert "codes not set up" in capsys.readouterr().out
    assert cli.main(["--db", db, "users", "reset-2fa", "nobody"]) == 1


def test_sign_in_on_a_laptop_is_turned_off_and_on_at_the_console(db, capsys):
    """D-243: the way out for a laptop's owner who can't sign in (no phone at hand), and back."""
    from ap_coder.core.store.sign_in import LAPTOP, OFF, SIGN_IN

    Store(db).set_setting(SIGN_IN, LAPTOP)
    assert cli.main(["--db", db, "users", "sign-in", "off"]) == 0
    assert Store(db).get_setting(SIGN_IN) == OFF and "without signing in" in capsys.readouterr().out
    assert Store(db).events(actions=["settings_changed"])[0]["actor"] == "the server console"
    assert cli.main(["--db", db, "users", "sign-in", "on"]) == 0
    assert Store(db).get_setting(SIGN_IN) == LAPTOP and "admin" in capsys.readouterr().out


def test_a_teams_sign_in_is_never_turned_off(db, capsys):
    from ap_coder.core.store.sign_in import SIGN_IN, TEAM

    Store(db).add_user("boss", GOOD, role="admin")  # an older team's folder: accounts, nothing decided
    assert cli.main(["--db", db, "users", "sign-in", "off"]) == 1
    assert Store(db).get_setting(SIGN_IN) == "" and "serves a team" in capsys.readouterr().err
    Store(db).set_setting(SIGN_IN, TEAM)
    assert cli.main(["--db", db, "users", "sign-in", "off"]) == 1 and Store(db).get_setting(SIGN_IN) == TEAM
