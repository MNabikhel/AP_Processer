"""``python -m ap_coder users``: the server console adds the first Admin and looks after the accounts (D-97)."""

from __future__ import annotations

import getpass

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
