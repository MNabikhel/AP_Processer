"""Process invoices in the web dashboard: upload, read and code in the background, the folder pick-up, duplicates,
the setup card and the demo invoices."""

from __future__ import annotations

import hashlib
import os
import re
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
from tests.stores import sample_setup_store  # noqa: F401  (the tests' store)
from tests.webclient import PAGE

STEM = "harbourview_NS_HST_HPS-2026-0347"
OTHER = "northwind_ON_HST_NW-2026-0912"


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
    assert "demo invoices" not in text.lower()  # offered in Settings → Data & backups only (D-221)
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


def test_upload_refuses_other_files_and_joins_the_files_already_in_line(app, client, store, tmp_path):
    response = client.post("/process/upload", files=[("files", ("notes.exe", b"MZ", "application/octet-stream"))],
                           headers=PAGE)  # fmt: skip
    snap = finished(app, client)
    assert response.status_code == 200
    assert any("notes.exe was not read" in m["text"] for m in snap["result"]["messages"])
    # A colleague's file in line already (D-168): the upload goes in line beside it, said, and both are read.
    from ap_coder.core.store.folder import read_file

    other = invoice_dir(store) / f"{OTHER}.pdf"
    other.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLES / other.name, other)
    store.queue_imports([read_file(other)], source="upload", uploader="colleague", person=True)
    response = upload(client, f"{STEM}.pdf")
    assert response.status_code == 200 and response.json()["queued"] == 1
    assert "in line with 1 file that came in before" in response.json()["toast"]["text"]
    snap = finished(app, client)
    assert snap["result"]["ok"] == 2 and len(store.list_invoices(REVIEW)) == 2


def test_the_same_file_uploaded_twice_at_once_is_stored_once(app, client, store, monkeypatch):
    """Two people upload the same invoice at the same moment (D-168): both uploads are saved and put in line, the
    file is read once, and the second upload's copy is skipped as already in AP Coder."""
    from ap_coder.ai.pipeline import InvoicePipeline

    both, reads, queued, in_line = threading.Barrier(2), [], [], threading.Event()
    real_queue, real_process = Store.queue_imports, InvoicePipeline.process

    def queue_imports(self, *args, **kwargs):  # the two uploads reach the queue together
        both.wait(30)
        try:
            return real_queue(self, *args, **kwargs)
        finally:
            queued.append(1)
            if len(queued) == 2:
                in_line.set()

    def process(self, path, *args, **kwargs):  # read once both are in line: a run takes the batches put in line
        in_line.wait(30)  # while its files are, and an upload after the read was done would start a run of its own
        reads.append(path)
        return real_process(self, path, *args, **kwargs)

    monkeypatch.setattr(Store, "queue_imports", queue_imports)
    monkeypatch.setattr(InvoicePipeline, "process", process)
    answers = []
    uploads = [threading.Thread(target=lambda: answers.append(upload(client, f"{STEM}.pdf"))) for _ in range(2)]
    for thread in uploads:
        thread.start()
    for thread in uploads:
        thread.join(60)
    assert [a.status_code for a in answers] == [200, 200]
    snap = finished(app, client)
    assert len(reads) == 1 and len(store.list_invoices()) == 1
    assert snap["result"]["ok"] == 1 and snap["result"]["label"] == "Processed 1 of 2: 1 already in AP Coder"


def test_files_left_in_line_by_a_restart_are_read_when_ap_coder_starts(store):
    """A laptop closed while files waited in line (D-168): they are read once AP Coder starts again, without a
    press of the Process button (before, they sat in the folder as new until someone pressed it)."""
    from ap_coder.core.store.folder import read_file

    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    for name in (f"{STEM}.pdf", f"{OTHER}.pdf"):
        shutil.copy(SAMPLES / name, folder / name)
    store.queue_imports([read_file(folder / f"{STEM}.pdf"), read_file(folder / f"{OTHER}.pdf")], source="upload")
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store)
    with TestClient(app, base_url="http://localhost") as client:  # (its start: the lifespan)
        snap = finished(app, client)
    assert snap["result"]["ok"] == 2 and len(store.list_invoices(REVIEW)) == 2


def _card(client, until, seconds=10.0):
    """The Process page's card once ``until(card)`` (or as it is after ``seconds``)."""
    ends = time.monotonic() + seconds
    while True:
        snap = client.get("/process/status").json()
        if until(snap) or time.monotonic() > ends:
            return snap
        time.sleep(0.05)


def test_a_file_that_cannot_be_read_now_says_why_and_is_read_once_it_can(app, client, store, monkeypatch):
    """The GL accounts open in Excel: the file goes back in line. The laptop's job ended in error, and the card said
    "1 file in line to be read" for good: the error never shown, nothing read until a restart or another import
    (B-1764). The card says why now, and the job looks again every few seconds, as the server's capture processes
    do: once the file is closed, it is read."""
    from ap_coder.web.pages import process

    monkeypatch.setattr(process, "LOOK_AGAIN_SECONDS", 0.1)
    real, locked = store.reference_data, [True]

    def reference_data(*args, **kwargs):
        if locked[0]:
            raise PermissionError(13, "Permission denied: 'gl_accounts.xlsx'")
        return real(*args, **kwargs)

    monkeypatch.setattr(store, "reference_data", reference_data)
    upload(client, f"{STEM}.pdf")
    snap = _card(client, lambda snap: "Permission denied" in snap["note"])
    assert snap["state"] == "running" and snap["note"].startswith("1 file in line to be read"), snap
    assert "Permission denied: 'gl_accounts.xlsx'" in snap["note"] and "tries again" in snap["note"]
    locked[0] = False
    assert finished(app, client)["result"]["ok"] == 1


def test_once_there_is_room_again_the_files_in_line_are_read(app, client, store, monkeypatch):
    """The data folder's disk nearly full: nothing is read, and the card says why. Once there was room again,
    nothing read the file until another import or a restart, and "Process N files" was off, a file in line not
    being new (B-1764): the job looks again every few seconds now (a restore's pause likewise)."""
    from ap_coder.intake import imports
    from ap_coder.web.pages import process

    monkeypatch.setattr(process, "LOOK_AGAIN_SECONDS", 0.1)
    room = [False]
    for module in (imports, process):
        monkeypatch.setattr(module, "has_room", lambda folder: room[0])
    upload(client, f"{STEM}.pdf")
    assert _card(client, lambda snap: snap["note"] == imports.NO_ROOM)["note"] == imports.NO_ROOM
    time.sleep(0.3)
    assert store.imports_open() == {"waiting": 1, "reading": 0} and store.list_invoices() == []
    room[0] = True
    assert finished(app, client)["result"]["ok"] == 1


def test_stopping_the_dashboard_puts_the_file_under_way_back_in_line(store, monkeypatch):
    """The dashboard stopped (its window closed, Ctrl+C) while its import reads a file: the file goes back in line,
    its try not counted, and nothing more is taken, as a capture process's stop does. It stayed "reading" as the
    dashboard's: after a quick restart the card said "Reading and coding" it for up to 2 minutes while nothing read
    it, and a second stop failed it as stopped twice (B-1766)."""
    from types import SimpleNamespace

    from ap_coder.ai import pipeline

    started, go = threading.Event(), threading.Event()

    def slow(self, path, *args, **kwargs):  # (a long scan)
        started.set()
        go.wait(30)
        return SimpleNamespace(ok=True, error="", output={"vendor_name": "V"}, report=None, invoice_id=None)

    def files():
        with store._conn() as conn:
            return [tuple(r) for r in conn.execute("SELECT status, owner, tries FROM import_files ORDER BY id")]

    monkeypatch.setattr(pipeline.InvoicePipeline, "process", slow)
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store)
    with TestClient(app, base_url="http://localhost") as client:
        upload(client, f"{STEM}.pdf", f"{OTHER}.pdf")
        assert started.wait(30)
    assert files() == [("waiting", "", 0), ("waiting", "", 0)]
    go.set()  # (its read ends after the stop: nothing of it is stored, nothing more taken)
    assert app.state.jobs.get("process").wait(30)
    assert files() == [("waiting", "", 0), ("waiting", "", 0)] and store.list_invoices() == []


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
    old = sorted(p for p in folder.iterdir()
                 if p.is_file() and p.suffix.lower() in EMAIL_EXTENSIONS and not p.name.startswith("."))  # fmt: skip
    # Windows drops the trailing dot of "dot.eml." (so it is an email there) and sorts its paths without case. A hidden
    # file is never a saved email (B-1758).
    expected = {"MAIL2.MSG", "mail.eml"} | ({"dot.eml"} if os.name == "nt" else set())
    assert emails_in(folder) == old and {p.name for p in old} == expected


def test_the_files_already_in_ap_coder_are_found_in_one_query(store, tmp_path):
    """Each file's SHA-256 against every invoice's in one query, not one query a file (D-157): an invoice of a picture
    made into a PDF counts, and a failed one does for the folder's pick-ups (``include_failed``), as ``find_by_hash``
    says; a copy of one under another name is in AP Coder; the import queue leaves them out (D-168)."""
    from ap_coder.core.store.folder import read_file

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
    files.append(folder / "copy of f3.pdf")
    store.add_invoice(files[3], {"vendor_name": "C", "invoice_number": "3", "grand_total": 3}, {"issues": []})
    read = [read_file(path) for path in files]
    for include_failed in (True, False):
        old = [p for p in files if store.find_by_hash(p, include_failed=include_failed) is not None]
        known = store.known_files([r[3] for r in read], include_failed)
        assert [p for p, r in zip(files, read, strict=True) if r[3] in known] == old
    queued = store.queue_imports(read, source="folder", include_failed=True)
    assert queued["queued"] == 2 and queued["skipped"] == ["f0.pdf", "f1.pdf", "f2.pdf", "f3.pdf", "copy of f3.pdf"]


def test_a_scheduled_watch_and_the_process_button_store_a_new_file_once(app, client, store, tmp_path, monkeypatch):
    """B-1587, with the import queue (D-168): a scheduled ``watch`` and the dashboard's Process button pick up the
    folder at once. The file goes in line once (the second pick-up finds it in line), is read once, by whichever
    reader takes it first, and stored once; the other reader waits for it."""
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.ops import cli

    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    new = folder / f"{OTHER}.pdf"
    shutil.copy(SAMPLES / f"{OTHER}.pdf", new)
    os.utime(new, (time.time() - 60, time.time() - 60))  # copied in a minute ago: the watch takes it
    listed_by_both, read = threading.Event(), []
    real_process = InvoicePipeline.process

    def process(self, path, *args, **kwargs):  # as a slow reading: the other pick-up lists the folder meanwhile
        read.append(path)
        listed_by_both.wait(30)
        return real_process(self, path, *args, **kwargs)

    monkeypatch.setattr(InvoicePipeline, "process", process)
    argv = ["--env-file", str(tmp_path / "none.env"), "--db", str(store.path), "watch", "--once", "--cache-dir", ""]
    watch = threading.Thread(target=cli.main, args=(argv,))
    watch.start()
    try:
        deadline = time.monotonic() + 60
        while not read and time.monotonic() < deadline:  # the watch put it in line and reads it
            time.sleep(0.05)
        assert read
        assert client.post("/process/folder", headers=PAGE).json()["job"] == "process"  # it lists the folder too
    finally:
        listed_by_both.set()
        watch.join(180)
    snap = finished(app, client)
    sha = hashlib.sha256(new.read_bytes()).hexdigest()
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM invoices WHERE file_sha256 = ?", (sha,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM import_files WHERE sha256 = ?", (sha,)).fetchone()[0] == 1
    assert len(read) == 1 and snap["result"]["processed"] == 1  # the run the button joined: the watch's file


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


def test_the_process_button_waits_a_few_seconds_at_most_for_another_pick_up(app, client, store):
    """A scheduled watch or ``ap_coder process`` holds the pick-up lock while it lists the folder, unpacks the emails
    and hashes the files (a minute for a big folder): the Process button's request waited for it as long, a thread
    of the web worker held, the button busy and nothing said; before D-168 the job said "Waiting for another pick-up".
    It waits a few seconds at most now, then says why (B-1771)."""
    from ap_coder.intake.mailbox import pickup_lock

    held, answers = pickup_lock(store.path.parent), []
    pressed = threading.Thread(target=lambda: answers.append(client.post("/process/folder", headers=PAGE)))
    started = time.monotonic()
    try:
        pressed.start()
        pressed.join(15)
        took = time.monotonic() - started
    finally:
        held.close()
    pressed.join(30)
    assert took < 10 and answers[0].status_code == 409, took
    assert "Another pick-up of invoices is under way" in answers[0].json()["error"]
    assert client.post("/process/folder", headers=PAGE).json()["job"] == "process"  # once it is done


def test_safe_upload_names(tmp_path):
    assert safe_file_name("../../etc/passwd") == "passwd"
    assert safe_file_name("C:\\Users\\me\\inv.pdf") == "inv.pdf"
    assert safe_file_name(".hidden") == "hidden" and safe_file_name("") == "invoice"
    a = save_upload(tmp_path, "a.pdf", b"one")
    b = save_upload(tmp_path, "a.pdf", b"two")
    assert a.name == "a.pdf" and b.name == "a_1.pdf" and save_upload(tmp_path, "a.pdf", b"one") == a


def _demo_section(client) -> str:
    """Settings → Data & backups' Demo invoices section ("" when the page has none)."""
    page = client.get("/settings/data").text
    return page.split('<section class="card" id="demo">', 1)[1].split("</section>", 1)[0] if 'id="demo"' in page else ""


def test_demo_invoices_load_and_remove(client, store):
    """D-221: the demo invoices are loaded and removed in Settings → Data & backups, which says how many are loaded;
    a first start, the queue and Import invoices say nothing of them while none is loaded."""
    for page in ("/review", "/process"):
        assert "demo invoices" not in client.get(page).text.lower(), page
    section = _demo_section(client)
    assert "None is loaded" in section and 'action="/demo/load"' in section and "/demo/remove" not in section
    back = re.search(r'name="back" value="([^"]+)"', section).group(1)
    response = client.post("/demo/load", data={"back": back}, headers=PAGE)
    assert response.json()["redirect"].startswith("/review?flash=")
    assert store.demo_count() == 10
    section = _demo_section(client)
    assert "<b>10 demo invoices loaded.</b>" in section and "Yes, remove the demo invoices" in section
    assert 'action="/demo/remove"' in section and "/demo/load" not in section
    for page in ("/review", "/process"):  # the pages keep to their work: no card to load or remove them
        text = client.get(page).text
        assert "/demo/remove" not in text and "/demo/load" not in text, page
    assert 'href="/settings/data#demo"' in client.get("/process").text  # (its notice says where they can be removed)
    back = re.search(r'name="back" value="([^"]+)"', section).group(1)
    assert client.post("/demo/remove", data={"back": back}, headers=PAGE).status_code == 400  # not ticked
    assert store.demo_count() == 10
    response = client.post("/demo/remove", data={"back": back, "sure": "1"}, headers=PAGE)
    assert response.json()["redirect"].startswith("/settings/data?flash=")
    assert "Removed 10 demo invoices" in client.get(response.json()["redirect"]).text
    assert store.demo_count() == 0 and "None is loaded" in _demo_section(client)


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
        said = [t for t in lines if t.startswith(f"{inv['vendor_name']}, ")  # ("invoice …", "credit note …": B-2100)
                and t.split(":")[0].endswith(f" {inv['invoice_number']}")]  # fmt: skip
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
    demo already loaded can still be removed. (In Settings → Data & backups, the one place they are offered: D-221.)"""
    assert "Load demo invoices" in _demo_section(client)
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    store.add_invoice(pdf, {"vendor_name": "Acme", "grand_total": 5}, {"issues": []})
    section = _demo_section(client)
    assert "Load demo invoices" not in section and "no invoices of your own" in section


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


def test_the_invoice_files_the_saved_emails_and_the_others_are_one_rule(tmp_path):
    """D-166 listed the folder once by name for the import page (``folder_names``), with the same rules repeated
    from ``invoice_files``, ``emails_in`` and ``other_files``, kept alike by a test. They are one rule now
    (``intake.files.folder_names``, D-168): each of the three is a part of it, the import page's too."""
    from ap_coder.ai.pipeline import invoice_files, other_files
    from ap_coder.intake import imports
    from ap_coder.intake.files import folder_names
    from ap_coder.intake.mailbox import emails_in
    from ap_coder.web.pages import process

    names = ["b.pdf", "A.PDF", "a.txt", "b.md", "c.markdown", "alone.txt", "scan.JPG", "photo.heic", "Two.dots.png",
             "README.pdf", "readme.txt", ".hidden.pdf", "._b.pdf", "~$lock.docx", "mail.eml", "Note.MSG", ".x.eml",
             "..eml", ".eml", "letter.docx", "sheet.xlsx", "desktop.ini", "Thumbs.db", "big.pdf.part",
             "file.crdownload", "trailing.", "no_suffix", "z.TMP", "zz.Eml"]  # fmt: skip
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    for name in ("folder.pdf", "box.eml", "dir.docx"):  # folders named like files: never listed
        (tmp_path / name).mkdir()
    documents, emails, others = folder_names(tmp_path)
    assert imports.folder_names is folder_names and process.list_folder is imports.list_folder
    assert ([p.name for p in invoice_files(tmp_path)], [p.name for p in emails_in(tmp_path)],
            [p.name for p in other_files(tmp_path)]) == (documents, emails, others)  # fmt: skip
    assert set(documents) == {"A.PDF", "Two.dots.png", "alone.txt", "b.pdf", "c.markdown", "photo.heic", "scan.JPG"}
    assert set(emails) == {"mail.eml", "Note.MSG", "zz.Eml"}  # hidden ones never (B-1758)
    # (Windows drops the trailing dot of "trailing.")
    assert {name.rstrip(".") for name in others} == {"letter.docx", "sheet.xlsx", "trailing", "no_suffix"}
    assert documents == sorted(documents, key=os.path.normcase) and others == sorted(others, key=os.path.normcase)


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


def test_an_invoices_folder_that_cannot_be_listed_never_breaks_the_import_page(client, store, monkeypatch):
    """B-1804: the invoices folder there but not listable just then (a network share gone between two looks, its
    rights changed) failed the whole Import invoices page; the page says so instead, the rest of it as ever."""
    from ap_coder.intake import files

    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    real = files.os.scandir

    def refused(path):
        if not isinstance(path, int) and os.path.realpath(path) == os.path.realpath(folder):
            raise PermissionError(13, "Access is denied", str(path))
        return real(path)

    monkeypatch.setattr(files.os, "scandir", refused)
    answer = client.get("/process")
    assert answer.status_code == 200 and "Setup" in answer.text
    assert "The invoices folder could not be listed just now" in answer.text and "No new files" not in answer.text


def test_the_process_button_says_when_the_invoices_folder_cannot_be_listed(client, store, monkeypatch):
    """B-1806: the Process button on an invoices folder there but not listable just then answered "Something went
    wrong" (a server error, its traceback in the log); it says why now, and nothing is put in line."""
    from ap_coder.intake import files

    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    real = files.os.scandir

    def refused(path):
        if not isinstance(path, int) and os.path.realpath(path) == os.path.realpath(folder):
            raise PermissionError(13, "Access is denied", str(path))
        return real(path)

    monkeypatch.setattr(files.os, "scandir", refused)
    answer = client.post("/process/folder", headers=PAGE)
    assert answer.status_code == 409 and "could not be listed just now (Access is denied)" in answer.json()["error"]
    assert store.imports_open()["waiting"] == 0


def test_in_a_browser_a_running_imports_card_does_not_show_the_last_runs_time(chromium, app, store):
    """B-2099: the card of the run before said when it ended ("2 minutes ago"); an import started from that page drew
    "Processing 4 invoices…" beside that time, the last run's. While it runs the card says no time."""
    from ap_coder.core.store.folder import read_file
    from tests.browser import browser_on
    from tests.web.test_server_browser import _job_running

    before = store.path.parent / "before.pdf"
    before.write_bytes(b"%PDF-1.4 the run before")
    store.queue_imports([read_file(before)], source="upload", person=True)
    row = store.claim_import("a reader", 600)
    store.finish_import(row["id"], "a reader", "done")
    with browser_on(chromium, app) as (browser, base):
        page = browser.new_page()
        page.goto(base + "/process")
        assert page.locator("#job").get_attribute("data-state") == "done"
        assert page.locator("#job-when").inner_text().strip()  # ("just now": when the run before ended)
        release = _job_running(app)
        try:
            page.evaluate("document.dispatchEvent(new CustomEvent('apc:job', {detail: {status: '/process/status'}}))")
            page.wait_for_selector("#job[data-state=running]", timeout=5000)
            assert page.locator("#job-title").inner_text() == "Processing 1 invoice…"
            assert page.locator("#job-when").inner_text().strip() == ""
        finally:
            release.set()


def test_an_imports_lines_name_each_invoice_not_only_its_vendor(app, client, store):
    """B-2100: three invoices of one vendor gave three lines the same ("Northwind IT Solutions Inc.: needs
    attention"). Each line names the invoice number read, or the file when none was."""
    from ap_coder.intake.imports import result_line

    upload(client, f"{STEM}.pdf", f"{OTHER}.pdf")
    texts = [line["text"] for line in finished(app, client)["lines"]]
    by_file = {inv["file_name"]: inv for inv in store.list_invoices(REVIEW)}
    assert len(texts) == 2
    for name in (f"{STEM}.pdf", f"{OTHER}.pdf"):
        vendor, number = by_file[name]["vendor_name"], by_file[name]["invoice_number"]
        assert vendor and number and [t for t in texts if t.startswith(f"{vendor}, invoice {number}: ")], texts
    said = [result_line("scan 12.pdf", output, flagged)["text"] for output, flagged in (
        ({"vendor_name": "Acme Supply Ltd", "invoice_number": "A-1001", "grand_total": 105.0}, False),
        ({"vendor_name": "Acme Supply Ltd", "invoice_number": "", "grand_total": 105.0}, True),
        ({"vendor_name": "Acme Supply Ltd", "invoice_number": "CN-7", "grand_total": -20.0}, True),
        ({"vendor_name": "", "invoice_number": "A-1002"}, True),
        ({}, True),
    )]  # fmt: skip
    assert said == ["Acme Supply Ltd, invoice A-1001: ready", "Acme Supply Ltd (scan 12.pdf): needs attention",
                    "Acme Supply Ltd, credit note CN-7: needs attention",
                    "scan 12.pdf, invoice A-1002: needs attention", "scan 12.pdf: needs attention"]  # fmt: skip


def test_a_file_left_out_of_an_upload_is_counted_and_said_with_why(app, client, store):
    """B-2100: six files picked ("Process 6 uploaded invoices"), one of them a text file with no amounts, ended as
    "Processed 2 of 5": the file left out before the reading was in neither count. The run counts every file picked
    and says which was left out and why, in its lines as it runs and in its messages."""
    files = [("files", (f"{STEM}.pdf", (SAMPLES / f"{STEM}.pdf").read_bytes(), "application/pdf")),
             ("files", ("notes.txt", b"call the vendor back\n", "text/plain")),
             ("files", ("contract.docx", b"PK\x03\x04 a document", "application/octet-stream"))]  # fmt: skip
    assert client.post("/process/upload", files=files, headers=PAGE).status_code == 200
    snap = finished(app, client)
    assert snap["result"]["label"] == "Processed 1 of 3: 2 left out"
    texts = [line["text"] for line in snap["lines"]]
    assert "notes.txt: left out, a text file with no amounts in it" in texts
    assert "contract.docx: left out, not a kind of file AP Coder reads" in texts
    assert [m["text"].split(":")[0] for m in snap["result"]["messages"] if "was not read" in m["text"]] == [
        "notes.txt was not read", "contract.docx was not read"]  # fmt: skip
    assert "Processed 1 of 3: 2 left out" in client.get("/process").text
    only = client.post("/process/upload", files=files[1:2], headers=PAGE)
    assert only.status_code == 200
    assert finished(app, client)["result"]["label"] == "Nothing new to read: 1 left out"
