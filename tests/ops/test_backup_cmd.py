"""``python -m ap_coder backup now|list|check|restore`` (D-143): IT's way to the backups, also on a new computer."""

from __future__ import annotations

import json
import os
import shutil

from ap_coder.core.store import Store
from ap_coder.ops import cli


def _data(tmp_path, ground_truth, share=None):
    data = tmp_path / "data"
    (data / "invoices").mkdir(parents=True)
    pdf = data / "invoices" / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 a")
    store = Store(data / "ap_coder.db")
    store.add_invoice(pdf, ground_truth, {})
    if share is not None:
        share.mkdir()
        store.set_setting("backup_copy_dir", str(share))
    return store, data


def test_now_and_list_say_what_each_backup_holds(tmp_path, ground_truth, capsys):
    share = tmp_path / "share"
    store, _ = _data(tmp_path, ground_truth, share)
    assert cli.main(["--db", str(store.path), "backup", "now"]) == 0
    out = capsys.readouterr().out
    (made,) = store.list_backups()
    assert made.name in out and "1 invoice" in out and "copied to" in out
    assert cli.main(["--db", str(store.path), "backup", "list"]) == 0
    out = capsys.readouterr().out
    assert made.name in out and "1 invoice, 1 file" in out and "complete" in out
    assert cli.main(["--db", str(store.path), "backup", "list", "--from", str(share)]) == 0
    assert made.name in capsys.readouterr().out


def test_restore_from_the_second_folder_onto_a_new_computer(tmp_path, ground_truth, capsys):
    share = tmp_path / "share"
    store, data = _data(tmp_path, ground_truth, share)
    made = store.backup_now()
    shutil.rmtree(data)  # the old computer is gone
    new = tmp_path / "new" / "ap_coder.db"
    code = cli.main(["--db", str(new), "backup", "restore", made.name, "--from", str(share), "--yes"])
    assert code == 0, capsys.readouterr()
    assert (tmp_path / "new" / "invoices" / "a.pdf").read_bytes() == b"%PDF-1.4 a"
    assert len(Store(new).list_invoices()) == 1
    assert "Restored" in capsys.readouterr().out


def test_restore_asks_first_and_refuses_while_ap_coder_runs(tmp_path, ground_truth, capsys, monkeypatch):
    store, data = _data(tmp_path, ground_truth)
    made = store.backup_now("manual")
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    assert cli.main(["--db", str(store.path), "backup", "restore", made.name]) == 1
    assert "Nothing was changed" in capsys.readouterr().out
    (data / ".ap_coder_server.json").write_text(json.dumps({"pid": os.getpid(), "port": 8501}))
    assert cli.main(["--db", str(store.path), "backup", "restore", made.name, "--yes"]) == 1
    assert "AP Coder is running" in capsys.readouterr().err
    assert cli.main(["--db", str(store.path), "backup", "restore", "../ap_coder.db", "--yes"]) == 1
    assert "No backup" in capsys.readouterr().err


def test_the_guides_new_server_steps_restore_while_the_service_is_stopped():
    """B-2112: docs/SERVER.md's "A new server" said to install the service, then restore. ``service install`` starts
    the service, and the console's restore refuses while AP Coder runs ("stop it first"): the step failed as written."""
    from tests.conftest import ROOT

    guide = (ROOT / "docs" / "SERVER.md").read_text(encoding="utf-8")
    steps = guide.split("- **A new server**", 1)[1].split("\n- **", 1)[0]
    assert "--no-start" in steps and "`service stop` first" in steps
    assert steps.index("--no-start") < steps.index("backup restore <name>") < steps.index("`service start`")


def test_restore_on_a_new_server_says_the_backups_accounts_come_back(tmp_path, ground_truth, capsys, monkeypatch):
    """B-2113: on a server with no account yet (a new server, restored before its first Admin) the restore said "The
    user accounts ... stay as they are now", where the backup's accounts are what comes back."""
    share = tmp_path / "share"
    store, _data_folder = _data(tmp_path, ground_truth, share)
    store.add_user("jsmith", "correct horse battery staple 42", role="admin")
    name = store.backup_now().name
    new = tmp_path / "new"
    new.mkdir()
    monkeypatch.setenv("AP_SERVER_MODE", "1")
    restore = ["--db", str(new / "ap_coder.db"), "backup", "restore", name, "--from", str(share), "--yes"]
    assert cli.main(restore) == 0
    out = capsys.readouterr().out
    assert "no user account yet" in out and "the backup's accounts" in out and "stay as they are now" not in out
    assert [u["username"] for u in Store(new / "ap_coder.db").list_users()] == ["jsmith"]
    assert cli.main(restore) == 0  # again, the accounts there now: kept
    assert "stay as they are now" in capsys.readouterr().out


def test_check_reads_every_file_and_says_what_is_missing_or_damaged(tmp_path, ground_truth, capsys):
    store, data = _data(tmp_path, ground_truth)
    made = store.backup_now("manual")
    assert cli.main(["--db", str(store.path), "backup", "check", made.name]) == 0
    assert "complete" in capsys.readouterr().out
    (kept,) = (store.backup_dir() / "files").glob("*/*")
    kept.write_bytes(b"%PDF-1.4 b")  # the same size, another content
    assert cli.main(["--db", str(store.path), "backup", "check"]) == 1
    assert "damaged: invoices/a.pdf" in capsys.readouterr().out


def test_help_names_a_backup_as_the_list_shows_it(tmp_path, ground_truth, capsys):
    """B-1577: ``backup --help`` gave a backup's name as ap_coder-<date>-<time>.db; a manual backup's name ends
    -manual.db, one before an update -before-update-…: the name to give is the one ``backup list`` shows."""
    store, _ = _data(tmp_path, ground_truth)
    made = store.backup_now("manual")
    try:
        cli.main(["backup", "--help"])
    except SystemExit:
        pass
    out = " ".join(capsys.readouterr().out.split())
    assert "as backup list shows it" in out
    assert "<date>-<time>.db" not in out and made.name.endswith("-manual.db")
