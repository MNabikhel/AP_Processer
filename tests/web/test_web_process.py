"""Process invoices in the web dashboard: upload, read and code in the background, the folder pick-up, duplicates,
the setup card and the demo invoices."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core import ui
from ap_coder.core.config import Settings
from ap_coder.core.store import REVIEW, Store, load_sample_setup
from ap_coder.web.app import create_app
from ap_coder.web.core import invoice_dir
from ap_coder.web.pages.process import safe_file_name, save_upload
from tests.conftest import SAMPLES

PAGE = {"X-AP-Coder": "1"}
STEM = "harbourview_NS_HST_HPS-2026-0347"
OTHER = "northwind_ON_HST_NW-2026-0912"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


@pytest.fixture
def app(store):
    s = Settings()
    return create_app(replace(s, llm=replace(s.llm, provider="off")), store)


@pytest.fixture
def client(app):
    return TestClient(app, base_url="http://localhost")


def finished(app, client):
    job = app.state.jobs.get("process")
    assert job.wait(180), "reading took too long"
    snap = client.get("/process/status").json()
    assert snap["state"] == "done", snap
    return snap


def upload(client, *names):
    files = [("files", (name, (SAMPLES / name).read_bytes(), "application/pdf")) for name in names]
    return client.post("/process/upload", files=files, headers=PAGE)


def test_process_page_shows_setup_upload_and_folder(client):
    text = client.get("/process").text
    assert "Upload invoices" in text and "Invoices folder" in text and "Setup" in text
    assert "GL accounts" in text and "26 imported" in text
    assert "Ready to read invoices" in text
    assert "Load demo invoices" in text
    assert 'accept=".bmp,' in text  # the file picker offers what AP Coder reads


def test_upload_reads_and_codes_in_the_background_then_the_queue_lists_it(app, client, store):
    response = upload(client, f"{STEM}.pdf")
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "job": "process", "status": "/process/status"}
    snap = finished(app, client)
    assert snap["result"]["ok"] == 1 and snap["result"]["failed"] == 0
    assert snap["result"]["label"] == "Processed 1 of 1"
    assert any("Harbourview" in line["text"] for line in snap["lines"])
    assert any("read and coded" in m["text"] and "review queue" in m["text"] for m in snap["result"]["messages"])
    assert (invoice_dir(store) / f"{STEM}.pdf").exists()  # saved in the invoices folder
    invoices = store.list_invoices(REVIEW)
    assert len(invoices) == 1
    queue = client.get("/review").text
    assert "Harbourview Professional Services" in queue and f'href="/review/{invoices[0]["id"]}"' in queue
    assert "1 invoice waiting" in queue
    page = client.get("/process").text  # the job card stays, with the summary, until the next run
    assert "Processed 1 of 1" in page and "Recently processed" in page
    assert client.get(f"/review/{invoices[0]['id']}").status_code == 200


def test_a_file_already_in_ap_coder_is_skipped(app, client, store):
    upload(client, f"{STEM}.pdf")
    finished(app, client)
    assert upload(client, f"{STEM}.pdf", f"{STEM}.pdf").status_code == 200
    snap = finished(app, client)
    assert snap["result"]["processed"] == 0 and snap["result"]["label"] == "Nothing new to read"
    skipped = [m for m in snap["result"]["messages"] if m["text"].startswith("Skipped")]
    assert skipped and skipped[0]["details"] == [f"{STEM}.pdf", f"{STEM}.pdf"]
    assert len(store.list_invoices()) == 1


def test_upload_refuses_other_files_and_queues_behind_a_running_job(app, client):
    response = client.post("/process/upload", files=[("files", ("notes.exe", b"MZ", "application/octet-stream"))],
                           headers=PAGE)  # fmt: skip
    snap = finished(app, client)
    assert response.status_code == 200
    assert any("notes.exe was not read" in m["text"] for m in snap["result"]["messages"])
    job = app.state.jobs.get("process")
    job.state = "running"  # as if a run were under way: the upload is saved and waits behind it (D-99)
    try:
        response = upload(client, f"{STEM}.pdf")
        assert response.status_code == 200 and response.json()["queued"] == 1
        assert "queued behind the import in progress" in response.json()["toast"]["text"]
    finally:
        job.state = "done"
        app.state.import_queue.waiting.clear()


def test_upload_needs_gl_accounts_first(tmp_path):
    bare = Store(tmp_path / "private" / "ap_coder.db")
    s = Settings()
    client = TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), bare), base_url="http://localhost")
    assert "Setup not finished" in client.get("/process").text
    response = upload(client, f"{STEM}.pdf")
    assert response.status_code == 400 and "GL accounts" in response.json()["error"]
    assert bare.list_invoices() == []


def test_the_folder_pick_up_lists_and_processes_new_files(app, client, store):
    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLES / f"{OTHER}.pdf", folder / f"{OTHER}.pdf")
    text = client.get("/process").text
    assert f"{OTHER}.pdf" in text and "Process 1 file" in text
    response = client.post("/process/folder", headers=PAGE)
    assert response.json()["job"] == "process"
    snap = finished(app, client)
    assert snap["result"]["ok"] == 1
    assert "No new files" in client.get("/process").text


def test_the_saved_emails_are_found_by_their_names_first(tmp_path):
    """Whether a path is a file is asked of the saved emails only, not of every invoice in the folder (D-157): the
    same emails."""
    from ap_coder.intake.mailbox import EMAIL_EXTENSIONS, emails_in

    folder = tmp_path / "invoices"
    folder.mkdir()
    for name in ("a.pdf", "C.PDF", "note.docx", "mail.eml", "MAIL2.MSG", ".hidden.eml", "plain", "dot.eml."):
        (folder / name).write_bytes(name.encode())
    (folder / "folder.eml").mkdir()
    old = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EMAIL_EXTENSIONS)
    # Windows drops the trailing dot of "dot.eml." (so it is an email there) and sorts its paths without case.
    expected = {".hidden.eml", "MAIL2.MSG", "mail.eml"} | ({"dot.eml"} if os.name == "nt" else set())
    assert emails_in(folder) == old and {p.name for p in old} == expected


def test_the_files_not_processed_are_found_in_one_query(store, tmp_path):
    """Each file's SHA-256 against every invoice's in one query, not one query a file (D-157): the same files new
    (an invoice of a picture made into a PDF, and a failed one, count as processed) and the same ones unreadable."""
    from ap_coder.web.pages.process import not_processed

    folder = tmp_path / "in"
    folder.mkdir()
    files = []
    for n in range(6):
        files.append(folder / f"f{n}.pdf")
        files[-1].write_bytes(f"%PDF-1.4 {n}".encode())
    store.add_invoice(files[0], {"vendor_name": "A", "invoice_number": "1", "grand_total": 1}, {"issues": []})
    store.add_invoice(files[1], None, None, error="could not be read")
    store.add_invoice(tmp_path / "made.pdf", {"vendor_name": "B", "invoice_number": "2", "grand_total": 2},
                      {"issues": []}, original_sha256=hashlib.sha256(files[2].read_bytes()).hexdigest())  # fmt: skip
    shutil.copy(files[3], folder / "copy of f3.pdf")
    files += [folder / "copy of f3.pdf", folder / "gone.pdf"]
    store.add_invoice(files[3], {"vendor_name": "C", "invoice_number": "3", "grand_total": 3}, {"issues": []})
    old_new, old_unreadable = [], []
    for path in files:
        try:
            (old_new if store.find_by_hash(path, include_failed=True) is None else []).append(path)
        except OSError:
            old_unreadable.append(path)
    assert not_processed(store, files) == (old_new, old_unreadable) == (files[4:6], [files[7]])


def test_a_scheduled_watch_and_the_process_button_store_a_new_file_once(app, client, store, tmp_path, monkeypatch):
    """B-1587: a scheduled ``watch`` and the dashboard's Process button had no lock in common. Both listed a new file
    as not stored yet, both read it, and it was stored twice (two invoices of the same file). Here they really run
    at once, a watch in a thread and the button's import: neither reads the file until both have listed it."""
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.ops import cli

    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    new = folder / f"{OTHER}.pdf"
    shutil.copy(SAMPLES / f"{OTHER}.pdf", new)
    os.utime(new, (time.time() - 60, time.time() - 60))  # copied in a minute ago: the watch takes it
    listed_by_watch, listed_by_both = threading.Event(), threading.Event()
    real_new_files, real_process = cli.new_files, InvoicePipeline.process

    def new_files(*args, **kwargs):
        found = real_new_files(*args, **kwargs)
        listed_by_watch.set()
        return found

    def process(self, path, *args, **kwargs):  # as a slow reading: the other pick-up lists the file meanwhile
        listed_by_both.wait(30)
        return real_process(self, path, *args, **kwargs)

    monkeypatch.setattr(cli, "new_files", new_files)
    monkeypatch.setattr(InvoicePipeline, "process", process)
    argv = ["--env-file", str(tmp_path / "none.env"), "--db", str(store.path), "watch", "--once", "--cache-dir", ""]
    watch = threading.Thread(target=cli.main, args=(argv,))
    watch.start()
    try:
        assert listed_by_watch.wait(60)
        assert client.post("/process/folder", headers=PAGE).json()["job"] == "process"  # it lists the file too
    finally:
        listed_by_both.set()
        watch.join(180)
    snap = finished(app, client)
    sha = hashlib.sha256(new.read_bytes()).hexdigest()
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM invoices WHERE file_sha256 = ?", (sha,)).fetchone()[0] == 1
    # The watch held the pick-up lock: the import waited for it, then found the file stored and skipped it.
    assert snap["result"]["processed"] == 0
    assert any(m["text"].startswith("Skipped") and m["details"] == [new.name] for m in snap["result"]["messages"])


def test_a_file_another_pick_up_failed_to_read_meanwhile_is_not_read_again(app, client, store, monkeypatch):
    """B-1673: the folder pick-up lists the files never processed, a failed read counting as processed (it is read
    again only by Retry), but its check once it holds the pick-up lock left failed reads out: a file a scheduled watch
    had just failed to read was read again at once. A retry still reads a failed file again."""
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.web.pages import process

    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    new = folder / f"{OTHER}.pdf"
    shutil.copy(SAMPLES / f"{OTHER}.pdf", new)
    real_wait, real_process, read = process.wait_for_pickup, InvoicePipeline.process, []

    def watch_first(*args, **kwargs):  # a watch held the lock: it read the file, and failed
        store.add_invoice(new, None, None, error="The file could not be read")
        return real_wait(*args, **kwargs)

    def reading(self, path, *args, **kwargs):
        read.append(os.path.basename(path))
        return real_process(self, path, *args, **kwargs)

    monkeypatch.setattr(process, "wait_for_pickup", watch_first)
    monkeypatch.setattr(InvoicePipeline, "process", reading)
    assert client.post("/process/folder", headers=PAGE).json()["job"] == "process"
    assert finished(app, client)["result"]["processed"] == 0 and read == []
    monkeypatch.setattr(process, "wait_for_pickup", real_wait)
    failed = store.find_by_hash(new, include_failed=True)["id"]
    assert client.post(f"/review/{failed}/retry", headers=PAGE).status_code == 200
    assert finished(app, client)["result"]["processed"] == 1 and read == [new.name]


def test_safe_upload_names(tmp_path):
    assert safe_file_name("../../etc/passwd") == "passwd"
    assert safe_file_name("C:\\Users\\me\\inv.pdf") == "inv.pdf"
    assert safe_file_name(".hidden") == "hidden" and safe_file_name("") == "invoice"
    a = save_upload(tmp_path, "a.pdf", b"one")
    b = save_upload(tmp_path, "a.pdf", b"two")
    assert a.name == "a.pdf" and b.name == "a_1.pdf" and save_upload(tmp_path, "a.pdf", b"one") == a


def test_demo_invoices_load_and_remove(client, store):
    response = client.post("/demo/load", data={"back": "/process"}, headers=PAGE)
    assert response.json()["redirect"].startswith("/process?flash=")
    assert store.demo_count() == 10
    assert "Remove demo invoices" in client.get("/process").text
    assert client.post("/demo/remove", data={"back": "/process"}, headers=PAGE).status_code == 400  # not ticked
    assert store.demo_count() == 10
    response = client.post("/demo/remove", data={"back": "/process", "sure": "1"}, headers=PAGE)
    assert "Removed 10 demo invoices" in client.get(response.json()["redirect"]).text
    assert store.demo_count() == 0


def test_the_reading_banner_shows_on_process_and_review_only(client):
    banner = '<div class="banner warn"'  # the FAQ on Help says "Open Settings → Reading" too
    assert banner in client.get("/process").text  # no LM Studio in tests: OvisOCR2 isn't running
    assert banner in client.get("/review").text
    assert banner not in client.get("/help").text


def test_the_import_list_says_ready_only_for_an_invoice_the_queue_calls_ready(store):
    """With the sample setup (no cost centers picked by the coder) and LM Studio not answering, a sample whose checks
    found no error is still one whose lines need a cost center: not "ready". (Weak matches were coded then, so some
    invoices passed the checks; since B-463 they are left blank, and the import list must still say what the queue
    says.)"""
    from ap_coder.web.pages.review import attention

    s = Settings()
    llm = replace(s.llm, provider="local", base_url="http://127.0.0.1:9/v1")  # nothing answers there
    app = create_app(replace(s, llm=llm), store, start_reader=False)
    client = TestClient(app, base_url="http://localhost")
    upload(client, *sorted(p.name for p in SAMPLES.glob("*.pdf")))
    snap = finished(app, client)
    pending = store.list_invoices(REVIEW)
    reasons = attention(store, store.reference_data(), pending)
    assert reasons  # lines to code: the queue flags them
    lines = [line["text"] for line in snap["lines"]]
    for inv in pending:
        said = [t for t in lines if t.startswith(f"{inv['vendor_name']}:")]
        assert said and all(t.endswith("needs attention" if inv["id"] in reasons else "ready") for t in said), (
            said,
            reasons.get(inv["id"]),
        )
    from ap_coder.web.pages import insights as insights_page

    insights_page.MIN_APPROVALS, kept = 0, insights_page.MIN_APPROVALS  # (its figures show from 20 approvals: B-515)
    try:
        insights = client.get("/insights").text  # its share is the checks' flag, said so: not the queue's "Ready"
    finally:
        insights_page.MIN_APPROVALS = kept
    assert "Flagged by the checks when processed" in insights and "Sent for a closer look" not in insights
    rows = client.get("/process").text.split("<div class='apc-rrow'>")[1:]  # Recently processed: the same pill
    for inv in pending:
        row = next((r for r in rows if ui.esc(inv["vendor_name"]) in r and inv["invoice_number"] in r), None)
        assert row is None or ("Needs attention" in row) == (inv["id"] in reasons), (inv["vendor_name"], row)


def test_the_demo_is_not_offered_once_there_are_invoices_of_your_own(client, store, tmp_path):
    """B-1504, D-137: after your own invoices are in, 'Load demo invoices' would add the ten samples again (a second
    Pacific PO-77120 shows as a duplicate payment) and mix made-up invoices into your numbers. Not offered then; a
    demo already loaded can still be removed."""
    assert "Load demo invoices" in client.get("/process").text
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    store.add_invoice(pdf, {"vendor_name": "Acme", "grand_total": 5}, {"issues": []})
    assert "Load demo invoices" not in client.get("/process").text
    assert "Load demo invoices" not in client.get("/review").text


def test_a_stale_page_cannot_load_the_demo_beside_your_own_invoices(client, store, tmp_path):
    """B-1529: the demo button is hidden once your own invoices are in (D-137), but a page drawn before they were
    (another tab) still loaded the ten samples beside them: a second Pacific PO-77120 read as a duplicate payment."""
    from tests.webclient import PAGE

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    store.add_invoice(pdf, {"vendor_name": "Acme", "grand_total": 5}, {"issues": []})
    answer = client.post("/demo/load", headers=PAGE)
    assert not answer.json().get("ok", True) and "your own invoices" in answer.text
    assert store.demo_count() == 0 and sum(store.status_counts().values()) == 1


def test_the_import_page_reads_no_file_of_the_invoices_folder(app, client, store, monkeypatch):
    """B-1633: each view of Import invoices read and hashed every file in the invoices folder to list the new ones,
    and the folder keeps the processed files (100,000 of them at 100,000 invoices: 7 s a view, 847 s at p95 under
    load). A view reads none: it lists the files the pick-up has not read since they changed, or read and found in no
    invoice; the pick-up still reads every one."""
    from pathlib import Path

    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLES / f"{OTHER}.pdf", folder / f"{OTHER}.pdf")
    client.post("/process/folder", headers=PAGE)
    assert finished(app, client)["result"]["ok"] == 1  # processed: it stays in the folder
    shutil.copy(SAMPLES / f"{STEM}.pdf", folder / "new.pdf")
    shutil.copy(folder / f"{OTHER}.pdf", folder / "again.pdf")  # the same invoice again, under another name
    read = []
    real = Path.read_bytes
    with monkeypatch.context() as m:
        m.setattr(Path, "read_bytes", lambda self: read.append(self) or real(self))
        text = client.get("/process").text
    assert not [p for p in read if folder in p.parents] and "Process 2 files" in text, read
    assert "new.pdf" in text and "again.pdf" in text  # (not read yet: new for now)
    client.post("/process/folder", headers=PAGE)
    assert finished(app, client)["result"]["ok"] == 1  # new.pdf read and coded; again.pdf found in AP Coder
    assert "No new files" in client.get("/process").text  # each as the pick-up read it


def test_a_data_folder_reached_through_a_link_lists_none_of_an_older_databases_files_as_new(tmp_path):
    """An older database's files count as read by the pick-up from their invoices' places, which are resolved (the
    link, the mapped drive followed), while the page listed the folder as named: through a link every old file showed
    as new. Files are listed and noted at the folder's resolved place."""
    real = tmp_path / "real"
    (real / "private").mkdir(parents=True)
    try:
        os.symlink(real, tmp_path / "link", target_is_directory=True)
    except OSError:  # (Windows without the right to make links)
        pytest.skip("no symbolic links here")
    store = Store(tmp_path / "link" / "private" / "ap_coder.db")  # the data folder as the dashboard is given it
    load_sample_setup(store)
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)
    client = TestClient(app, base_url="http://localhost")
    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLES / f"{OTHER}.pdf", folder / f"{OTHER}.pdf")
    client.post("/process/folder", headers=PAGE)
    assert finished(app, client)["result"]["ok"] == 1
    assert "No new files" in client.get("/process").text
    with store._conn() as conn:  # as an older AP Coder left it
        conn.execute("DELETE FROM seen_files")
        conn.execute("DELETE FROM settings WHERE key = 'seen_files'")
    Store(store.path)  # opened by this version
    assert "No new files" in client.get("/process").text


def test_a_scan_written_over_another_of_its_name_is_listed_as_new_within_a_minute(app, client, store, monkeypatch):
    """The list was kept until the folder, a pick-up's notes or the invoices changed: a scanner writing a new scan
    over an old one's name changes none of them (the folder's time is that of a name added, removed or renamed), and
    the new scan was never listed. The list is also worked out again after LISTED_SECONDS."""
    from ap_coder.web.pages import process

    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    scan = folder / "scan.pdf"
    shutil.copy(SAMPLES / f"{OTHER}.pdf", scan)
    client.post("/process/folder", headers=PAGE)
    assert finished(app, client)["result"]["ok"] == 1
    before = os.stat(folder).st_mtime_ns - 10_000_000_000  # (the folder last changed 10 s ago: its list is kept)
    os.utime(folder, ns=(before, before))
    assert "No new files" in client.get("/process").text
    scan.write_bytes((SAMPLES / f"{STEM}.pdf").read_bytes())  # the next scan, written over the last one
    os.utime(folder, ns=(before, before))
    assert "No new files" in client.get("/process").text  # (kept a while)
    monkeypatch.setattr(process, "LISTED_SECONDS", 0.0)  # a minute later
    assert "scan.pdf" in client.get("/process").text


def test_the_import_page_lists_the_folder_once_by_name_as_the_pick_up_and_its_warning_do(tmp_path):
    """D-166: the import page lists the invoices folder once, by name (``folder_names``): the invoices, the saved
    emails and the files of a kind not read are what ``invoice_files``, ``emails_in`` and ``other_files`` give, in
    their order, each of which made a Path of each of 100,000 files (2 s together)."""
    from ap_coder.ai.pipeline import invoice_files, other_files
    from ap_coder.intake.mailbox import EMAIL_EXTENSIONS, emails_in
    from ap_coder.web.pages import process

    names = ["b.pdf", "A.PDF", "a.txt", "b.md", "c.markdown", "alone.txt", "scan.JPG", "photo.heic", "Two.dots.png",
             "README.pdf", "readme.txt", ".hidden.pdf", "._b.pdf", "~$lock.docx", "mail.eml", "Note.MSG", ".x.eml",
             "..eml", ".eml", "letter.docx", "sheet.xlsx", "desktop.ini", "Thumbs.db", "big.pdf.part",
             "file.crdownload", "trailing.", "no_suffix", "z.TMP", "zz.Eml"]  # fmt: skip
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    for name in ("folder.pdf", "box.eml", "dir.docx"):  # folders named like files: never listed
        (tmp_path / name).mkdir()
    assert process.folder_names(tmp_path) == (
        [p.name for p in invoice_files(tmp_path)],
        [p.name for p in emails_in(tmp_path)],
        [p.name for p in other_files(tmp_path, EMAIL_EXTENSIONS)],
    )
    documents, emails, others = process.folder_names(tmp_path)
    assert "a.txt" not in documents and "alone.txt" in documents and "..eml" in emails and "letter.docx" in others


def test_the_vendor_master_step_follows_the_vendor_list_and_the_numbers_set_whoever_changed_them(tmp_path):
    """D-166: the setup step's count of the vendor master, and of its vendors with an address number (AN8), is kept in
    each process until the vendor list changes (``changes/vendors``: another Store, plain SQL) or the numbers set in
    Settings → JD Edwards do (what it is asked): 20,000 vendors read at every view of Import invoices took 0.23 s."""
    from ap_coder.erp import jde
    from ap_coder.web.core import SHOW_JDE
    from ap_coder.web.pages.process import VENDOR_STEP, pilot_steps
    from tests.core.test_kept_answers import _plain

    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    other.set_setting(SHOW_JDE, "1")

    def step() -> str:
        return next(s for s in pilot_steps(store) if s[1] == VENDOR_STEP)[2]

    assert step().startswith("on Vendors: import")
    other.import_vendor_master([{"vendor_name": "Northwind Supply", "status": "active"}])
    assert step().startswith("1 vendor, but no address number")
    _plain(store, "UPDATE vendors SET erp_id = '1001' WHERE in_master = 1")
    assert step() == "1 vendor, 1 with an address number (AN8)"
    _plain(store, "UPDATE vendors SET erp_id = '' WHERE in_master = 1")
    assert step().startswith("1 vendor, but no address number")
    settings = jde.load(other)
    settings.an8_overrides = {store.master_vendor_ids()[0][0]: "2002"}
    jde.save(other, settings)
    assert step() == "1 vendor, 1 with an address number (AN8)"
    other.import_vendor_master([{"vendor_name": "Harbourview Paper", "status": "active"}])
    assert step() == "2 vendors, 1 with an address number (AN8)"


def test_an_older_database_lists_none_of_its_invoices_files_as_new(app, client, store):
    """B-1633: a database from before has no record of what the pick-up read: its invoices' own files count as read
    (once, when this version opens it), so none is listed as new."""
    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLES / f"{OTHER}.pdf", folder / f"{OTHER}.pdf")
    client.post("/process/folder", headers=PAGE)
    assert finished(app, client)["result"]["ok"] == 1
    with store._conn() as conn:  # as an older AP Coder left it
        conn.execute("DELETE FROM seen_files")
        conn.execute("DELETE FROM settings WHERE key = 'seen_files'")
    assert "Process 1 file" in client.get("/process").text
    Store(store.path)  # opened by this version
    assert "No new files" in client.get("/process").text
