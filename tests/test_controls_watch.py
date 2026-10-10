"""Controls report from the audit trail, and the folder watcher's choice of files."""

import datetime as dt
import json
import os
import shutil
import time

from ap_coder import controls
from ap_coder.audit import describe
from ap_coder.cli import new_files
from ap_coder.store import Store

from .conftest import SAMPLE_STEM, SAMPLES


def _gt(number="A"):
    return {**json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text()), "invoice_number": number}


def test_controls_report_lists_the_exceptions(tmp_path):
    store = Store(tmp_path / "a.db")
    store.set_setting("approval_limit", "10000")
    a = store.add_invoice(tmp_path / "a.pdf", _gt("A"), {})
    store.approve_invoice(a, _gt("A"), "Jane", open_issues=[{"code": "TOTAL_MISMATCH", "severity": "error"}])
    b = store.add_invoice(tmp_path / "b.pdf", _gt("B"), {})
    store.approve_invoice(
        b, _gt("B"), "Jane", open_issues=[{"code": "VENDOR_TAX_NUMBER_CHANGED", "severity": "warning"}]
    )
    store.final_approve(a, "Sam")
    store.send_back(b, "Sam", "check the GST number")
    store.set_setting("approval_limit", "5000", actor="Sam")  # not logged by itself...
    store.log_event("settings_changed", actor="Sam", detail={"keys": ["approval_limit"]})  # ...the page logs it

    today = dt.date.today()
    r = controls.build(store, today - dt.timedelta(days=1), today)
    assert [e["invoice_id"] for e in r["overrides"]] == [a]
    assert [e["invoice_id"] for e in r["signals"]] == [b]
    assert len(r["finals"]) == 1 and len(r["sent_back"]) == 1 and r["waiting"] == []
    assert any(e["action"] == "settings_changed" for e in r["setup"])
    assert r["by_person"] == [("Jane", 2, 0)] and controls.exceptions(r) == 2
    page = controls.report_html(r)
    assert "Approved despite an error (1)" in page and "TOTAL_MISMATCH" in page and "check the GST number" in page
    overridden = next(e for e in store.events(a) if e["action"] == "approved")
    assert describe(overridden).startswith("approved despite TOTAL_MISMATCH; over the approval limit")

    earlier = controls.build(store, today - dt.timedelta(days=30), today - dt.timedelta(days=10))
    assert earlier["approvals"] == 0 and "None: no error was overridden." in controls.report_html(earlier)


def test_watch_picks_new_settled_files_only(tmp_path):
    store = Store(tmp_path / "a.db")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    old = inbox / "old.pdf"
    shutil.copy(SAMPLES / f"{SAMPLE_STEM}.pdf", old)
    os.utime(old, (time.time() - 60, time.time() - 60))
    copying = inbox / "copying.pdf"
    shutil.copy(SAMPLES / "pacific_BC_GST_PST_PO-77120.pdf", copying)  # modified just now
    (inbox / "notes.txt.bak").write_text("not an invoice")
    assert new_files(inbox, store) == [old]
    assert set(new_files(inbox, store, now=time.time() + 60)) == {copying, old}
    store.add_invoice(old, None, None, error="Azure said no")  # a failed attempt is not retried in a loop
    assert old not in new_files(inbox, store, now=time.time() + 60)
    assert new_files(tmp_path / "missing", store) == []


def test_identical_files_dropped_together_are_one_invoice(tmp_path):
    store = Store(tmp_path / "a.db")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for name in ("scan.pdf", "scan (1).pdf", "scan - Copy.pdf"):
        shutil.copy(SAMPLES / f"{SAMPLE_STEM}.pdf", inbox / name)
    shutil.copy(SAMPLES / "pacific_BC_GST_PST_PO-77120.pdf", inbox / "other.pdf")
    found = new_files(inbox, store, now=time.time() + 60)
    assert len(found) == 2 and inbox / "other.pdf" in found  # one of the three copies, and the other invoice


def _watch_args(tmp_path, *extra):
    return ["--env-file", str(tmp_path / "none.env"), "--db", str(tmp_path / "a.db"), "watch", str(tmp_path / "in"),
            "--cache-dir", "", *extra]  # fmt: skip


def test_an_error_outside_one_invoice_does_not_stop_the_watcher(tmp_path, monkeypatch, capsys):
    """E.g. the GL accounts CSV open in Excel: that check is skipped and said, the next one runs."""
    from ap_coder import cli

    checks: list[int] = []

    def unpack_folder(folder):
        checks.append(1)
        raise PermissionError("chart_of_accounts.csv is open in Excel")

    def sleep(seconds):
        if len(checks) >= 2:
            raise KeyboardInterrupt  # Ctrl+C after the second check

    monkeypatch.setattr(cli, "unpack_folder", unpack_folder)
    monkeypatch.setattr(cli.time, "sleep", sleep)
    assert cli.main(_watch_args(tmp_path)) == 0
    assert len(checks) == 2
    assert capsys.readouterr().err.count("this check stopped: PermissionError") == 2
    assert cli.main(_watch_args(tmp_path, "--once")) == 1  # Task Scheduler sees the failed run


def test_watch_once_fails_when_every_invoice_failed(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from ap_coder import cli

    inbox = tmp_path / "in"
    inbox.mkdir()
    (inbox / "a.pdf").write_bytes(b"%PDF-1.4 a")
    os.utime(inbox / "a.pdf", (1_700_000_000, 1_700_000_000))

    class Pipeline:
        def __init__(self, *args, **kwargs):
            pass

        def process(self, path):
            return SimpleNamespace(ok=self.ok, error="unreadable", output={"vendor_name": "V"}, report=None)

    monkeypatch.setattr(cli, "InvoicePipeline", Pipeline)
    Pipeline.ok = False
    assert cli.main(_watch_args(tmp_path, "--once")) == 1
    Pipeline.ok = True
    (inbox / "b.pdf").write_bytes(b"%PDF-1.4 b")
    os.utime(inbox / "b.pdf", (1_700_000_000, 1_700_000_000))
    assert cli.main(_watch_args(tmp_path, "--once")) == 0


def test_a_crafted_email_does_not_stop_the_watcher(tmp_path, monkeypatch, capsys):
    """An email the watcher cannot read (1000 forwarded emails inside each other) stopped every check, so no
    invoice was processed any more: it is filed away, said, and the invoices next to it are processed."""
    from types import SimpleNamespace

    from ap_coder import cli

    from .test_mailbox import nested_email

    inbox = tmp_path / "in"
    inbox.mkdir()
    (inbox / "deep.eml").write_bytes(nested_email(1000))
    (inbox / "a.pdf").write_bytes(b"%PDF-1.4 a")
    for f in inbox.iterdir():
        os.utime(f, (1_700_000_000, 1_700_000_000))
    processed = []

    class Pipeline:
        def __init__(self, *args, **kwargs):
            pass

        def process(self, path):
            processed.append(path.name)
            return SimpleNamespace(ok=True, error=None, output={"vendor_name": "V"}, report=None)

    monkeypatch.setattr(cli, "InvoicePipeline", Pipeline)
    assert cli.main(_watch_args(tmp_path, "--once")) == 0
    assert processed == ["a.pdf"]
    assert (inbox / "emails" / "could not read" / "deep.eml").exists()
    assert "deep.eml: could not be read" in capsys.readouterr().err
