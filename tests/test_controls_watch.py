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


def test_controls_setup_section_counts_every_setup_change(tmp_path):
    """Vendors added or deleted, the ERP register cleared, an invoice deleted, history imported and a lesson retired
    or restored are all changes to setup, vendors or learning that the controls report lists."""
    store = Store(tmp_path / "a.db")
    store.add_vendor_here("Acme", gst="123456789RT0001", actor="Jane")
    store.delete_vendors(["acme"], actor="Jane")
    store.clear_erp_register(actor="Jane")
    store.delete_invoice(store.add_invoice(tmp_path / "a.pdf", _gt("A"), {}), actor="Sam")
    store.import_history([{"vendor_name": "Acme", "description": "Paper", "gl_code": "6000"}], actor="Sam")
    with store._conn() as conn:
        conn.execute("INSERT INTO lessons (situation, vendor_key, vendor_name, field, created_at, updated_at) "
                     "VALUES ('acme|invoice|gl|', 'acme', 'Acme', 'gl', '2026-10-01', '2026-10-01')")  # fmt: skip
        lesson = conn.execute("SELECT id FROM lessons").fetchone()[0]
    store.retire_lesson(lesson, actor="Sam")
    store.retire_lesson(lesson, actor="Sam", retired=False)
    today = dt.date.today()
    setup = {e["action"] for e in controls.build(store, today - dt.timedelta(days=1), today)["setup"]}
    assert setup >= {"vendor_added", "vendors_deleted", "erp_register_imported", "deleted", "history_imported",
                     "lesson_retired", "lesson_restored"}  # fmt: skip


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


def test_a_file_stamped_in_the_future_is_picked_up(tmp_path):
    """A ZIP made in a time zone ahead, or a scanner with a fast clock: its time is ahead of this computer's, which
    is not "still copying" (it was skipped until the clock caught up)."""
    store = Store(tmp_path / "a.db")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    ahead = inbox / "ahead.pdf"
    shutil.copy(SAMPLES / f"{SAMPLE_STEM}.pdf", ahead)
    os.utime(ahead, (time.time() + 3600, time.time() + 3600))
    assert new_files(inbox, store) == [ahead]


def _old_email(path, *attachments):
    from email.message import EmailMessage

    m = EmailMessage()
    m["Subject"], m["From"] = "Invoice", "ar@vendor.example"
    m.set_content("attached")
    for name, content in attachments:
        m.add_attachment(content, maintype="application", subtype="pdf", filename=name)
    path.write_bytes(m.as_bytes())
    os.utime(path, (1_700_000_000, 1_700_000_000))


def test_watch_once_processes_the_invoices_it_just_took_out_of_emails(tmp_path, monkeypatch):
    """The attachments were written a moment ago, so the "still copying" check skipped them until the next run (a
    day later from Task Scheduler)."""
    from types import SimpleNamespace

    from ap_coder import cli

    inbox = tmp_path / "in"
    inbox.mkdir()
    _old_email(inbox / "mail.eml", ("inv.pdf", b"%PDF-1.4 scan"))
    processed = []

    class Pipeline:
        def __init__(self, *args, **kwargs):
            pass

        def process(self, path):
            processed.append(path.name)
            return SimpleNamespace(ok=True, error=None, output={"vendor_name": "V"}, report=None)

    monkeypatch.setattr(cli, "InvoicePipeline", Pipeline)
    assert cli.main(_watch_args(tmp_path, "--once")) == 0
    assert processed == ["mail - inv.pdf"]


def test_watch_once_fails_when_every_email_could_not_be_read(tmp_path):
    from ap_coder import cli

    inbox = tmp_path / "in"
    inbox.mkdir()
    (inbox / "bad.msg").write_bytes(b"not an outlook email" * 40)
    os.utime(inbox / "bad.msg", (1_700_000_000, 1_700_000_000))
    assert cli.main(_watch_args(tmp_path, "--once")) == 1
