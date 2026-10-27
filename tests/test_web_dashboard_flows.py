"""Clerk and AP lead flows through the dashboard's pages (ap_coder/web), each one a behaviour the earlier dashboard's
page tests covered: what is approved is what was on screen, the review screen's grid actions and messages, edits that
must not outlive their invoice, actions on an invoice someone else just changed, uploads and saved emails that must
not stop the Process page, and the pages that offer what an approval taught."""

from __future__ import annotations

import copy
import io
import os
import re
import zipfile
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from ap_coder.coding.rules import Rule
from ap_coder.core.store import APPROVED, PENDING, REJECTED, Store, load_sample_setup
from ap_coder.web.core import invoice_dir

from .conftest import SAMPLE_STEM, SAMPLES, fill_from
from .test_web_invoice import check, form_of
from .webclient import add_clean, follow, gt, known_vendors, post


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


def _flagged(store, doc=None, **changes):
    doc = {**(doc or gt()), **changes}
    return store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", doc, {"requires_review": True})


def _selected(lines_html: str) -> list[str]:
    """The GL account picked in each row of the line grid's HTML."""
    rows = lines_html.split('name="line_key"')[1:]
    out = []
    for row in rows:
        gl = row.split('name="line_gl"', 1)[1] if 'name="line_gl"' in row else row
        found = re.search(r'<option value="([^"]*)" selected>', gl)
        out.append(found.group(1) if found else "")
    return out


# --- The review screen ---------------------------------------------------------------------------------------------


def test_an_impossible_date_is_said_and_approval_waits_but_reject_stays(client, store):
    invoice_id = add_clean(store)
    data = form_of(store, invoice_id, invoice_date="2026-02-30")
    answer = check(client, invoice_id, data)
    # said at its field, the other checks kept, never approved (B-738)
    assert not answer["allow"] and "is not a date" in answer["header"]["flags"]["invoice_date"]
    assert post(client, f"/review/{invoice_id}/approve", data).status_code == 400
    page = client.get(f"/review/{invoice_id}").text
    assert f"/review/{invoice_id}/reject" in page and "Delete" in page  # reject / delete stay available
    follow(client, post(client, f"/review/{invoice_id}/reject", {"reason": "not our invoice"}))
    assert store.get_invoice(invoice_id)["status"] == REJECTED


def test_the_pos_coding_is_applied_on_the_review_screen(client, store, setup_demo_template):
    fill_from(store, setup_demo_template)
    redriver = next(i for i in store.list_invoices() if i["vendor_name"].startswith("Red River"))
    invoice_id = redriver["id"]
    assert f"/review/{invoice_id}/po-coding" in check(client, invoice_id)["sections"]["po"]
    answer = post(client, f"/review/{invoice_id}/po-coding", form_of(store, invoice_id)).json()
    assert answer["ok"] and _selected(answer["lines"])[0] == "1510"  # the PO's GL, not the AI's 6900
    assert "/po-coding" not in check(client, invoice_id)["sections"]["po"]  # nothing left to apply

    def po(rows):
        rows[0]["predicted_gl_code"] = "1510"
        return rows

    follow(client, post(client, f"/review/{invoice_id}/approve", form_of(store, invoice_id, po)))
    assert store.get_invoice(invoice_id)["final_output"]["line_items"][0]["predicted_gl_code"] == "1510"


def test_ask_the_vendor_on_the_review_screen(client, store):
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", {**gt(), "gst_hst_registration_number": ""}, {})
    page = client.get(f"/review/{invoice_id}").text
    bodies = re.findall(r"<pre>(Hello.*?)</pre>", page, re.S)
    assert bodies and "GST/HST registration number" in bodies[0]


def test_the_review_screen_shows_the_rules_applied(client, store):
    change = {"line_number": 5, "rule": "any vendor, line contains “delivery”", "gl_from": "6800", "gl_to": "6900",
              "cc_from": "CC400", "cc_to": "CC400"}  # fmt: skip
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", gt(), {}, meta={"rules_applied": [change]})
    assert "by the rule" in client.get(f"/review/{invoice_id}").text


def test_a_rule_added_after_processing_is_applied_and_taught_as_the_reviewers_choice(client, store):
    invoice_id = add_clean(store)
    store.save_coding_rules([Rule("", "delivery", "6900")])
    answer = post(client, f"/review/{invoice_id}/apply-rules", form_of(store, invoice_id)).json()
    numbers = [li["line_number"] for li in gt()["line_items"]]
    assert answer["ok"] and _selected(answer["lines"])[numbers.index(5)] == "6900"
    assert "Apply the coding rules" not in check(client, invoice_id)["sections"]["checks"]  # nothing left to apply

    def ruled(rows):
        next(r for r in rows if r["line_number"] == 5)["predicted_gl_code"] = "6900"
        return rows

    follow(client, post(client, f"/review/{invoice_id}/approve", form_of(store, invoice_id, ruled)))
    lines = store.get_invoice(invoice_id)["final_output"]["line_items"]
    assert next(li for li in lines if li["line_number"] == 5)["predicted_gl_code"] == "6900"
    line5 = next(r for r in store.feedback_rows() if r["line_number"] == 5)
    assert (line5["suggested_gl"], line5["outcome"]) == ("6800", "corrected")  # the AI's own answer was 6800


def test_a_credit_note_is_not_shown_as_due(client, store, setup_demo_template):
    fill_from(store, setup_demo_template)
    credit = next(i for i in store.list_invoices() if (i["grand_total"] or 0) < 0)
    summary = check(client, credit["id"])["sections"]["summary"]
    assert "Credit" in summary and "Due in" not in summary and "Overdue" not in summary


def test_approve_anyway_asks_again_when_a_new_error_appears(client, store):
    doc = gt()
    # one error: HST put in Alberta, where it is not levied (a cost center not in the list is never approved anyway)
    doc["tax_lines"][0]["province"] = "AB"
    invoice_id = _flagged(store, doc)
    data = form_of(store, invoice_id)
    bar = check(client, invoice_id, data)["sections"]["bar"]
    assert "Approve anyway, despite" in bar and "1 error" in bar
    key = re.search(r'name="override" value="([0-9a-f]+)"', bar).group(1)
    assert check(client, invoice_id, {**data, "override": key, "override_reason": "the vendor is correcting it"})[
        "allow"
    ]
    # a second error: the totals differ, which is never approved anyway (D-75)
    worse = {**data, "override": key, "override_reason": "the vendor is correcting it", "grand_total": "99999"}
    answer = check(client, invoice_id, worse)
    assert not answer["allow"] and "Not ready: Subtotal + tax" in answer["sections"]["bar"]
    assert post(client, f"/review/{invoice_id}/approve", worse).status_code == 400


def test_a_line_without_a_cost_center_is_not_approved(client, store):
    """Where cost centers are used, UNASSIGNED is never posted: like a line without a GL account."""
    doc = gt()
    doc["line_items"][0]["predicted_cost_center"] = "UNASSIGNED"
    invoice_id = _flagged(store, doc)
    answer = check(client, invoice_id, form_of(store, invoice_id))
    assert not answer["allow"] and "Pick a cost center for line 1 to approve" in answer["sections"]["bar"]
    assert 'name="override"' not in answer["sections"]["bar"]  # not something to approve anyway


def test_suggestions_for_two_lines_with_the_same_number(client, store):
    doc = gt()
    doc["line_items"][1]["line_number"] = 1  # as read from the invoice
    for li in doc["line_items"][:2]:
        li["predicted_gl_code"] = "UNASSIGNED"
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", doc, {})
    data = form_of(store, invoice_id)
    found = re.findall(
        r'data-position="(\d+)" data-gl="([^"]+)"', check(client, invoice_id, data)["sections"]["suggest"]
    )
    positions = {p for p, _ in found}
    assert {"0", "1"} <= positions
    gl = next(g for p, g in found if p == "1")
    answer = post(client, f"/review/{invoice_id}/suggestion", {**data, "position": "1", "gl": gl}).json()
    assert _selected(answer["lines"])[:2] == ["UNASSIGNED", gl]  # the line clicked, not both


def test_a_suggested_gl_codes_a_line_just_added(client, store):
    """A line the reviewer adds has no number in the grid yet: the suggestion must still code it."""
    invoice_id = add_clean(store)

    def added(rows):
        new = {k: None for k in rows[0]} | {
            "description": "Printer toner and copy paper",
            "amount": 0.0,
            "predicted_gl_code": "UNASSIGNED",
            "predicted_cost_center": "CC400",
        }
        return [*rows, new]  # fmt: skip

    data = form_of(store, invoice_id, added)
    position = str(len(gt()["line_items"]))  # the new line
    found = re.findall(
        r'data-position="(\d+)" data-gl="([^"]+)"', check(client, invoice_id, data)["sections"]["suggest"]
    )
    gl = next(g for p, g in found if p == position)
    answer = post(client, f"/review/{invoice_id}/suggestion", {**data, "position": position, "gl": gl}).json()
    assert answer["ok"] and _selected(answer["lines"])[-1] == gl
    coded = {**data, "line_gl": [*data["line_gl"][:-1], gl]}
    assert f'data-position="{position}"' not in check(client, invoice_id, coded)["sections"]["suggest"]


# --- Edits that must not outlive their invoice ----------------------------------------------------------------------


def test_bulk_approve_keeps_an_earlier_approvers_corrections(client, store):
    """A reopened invoice is shown with what its approver approved: bulk approval posts that, not the AI's."""
    known_vendors(store, SAMPLE_STEM, "chinook_AB_GST_CCO-26-10418", "laurentides_QC_TPS_TVQ_SIL-4471")
    reopened = add_clean(store)
    others = [add_clean(store, "chinook_AB_GST_CCO-26-10418"), add_clean(store, "laurentides_QC_TPS_TVQ_SIL-4471")]
    fixed = copy.deepcopy(store.get_invoice(reopened)["ai_output"])
    assert fixed["line_items"][0]["predicted_gl_code"] != "1510"
    fixed["line_items"][0]["predicted_gl_code"] = "1510"
    store.approve_invoice(reopened, fixed, "Jane")
    store.reopen(reopened, "Jane", "check the cost center")
    follow(client, post(client, "/review/bulk-approve", {"ids": [reopened, *others]}))
    inv = store.get_invoice(reopened)
    assert inv["status"] == APPROVED
    assert inv["final_output"]["line_items"][0]["predicted_gl_code"] == "1510"


def test_a_rejected_invoice_reopens_without_the_edits_dropped_at_rejection(client, store):
    invoice_id = add_clean(store)
    check(client, invoice_id, form_of(store, invoice_id, invoice_number="TYPO-123"))
    assert 'value="TYPO-123"' in client.get(f"/review/{invoice_id}").text  # kept while it is open
    follow(client, post(client, f"/review/{invoice_id}/reject", {"reason": "not our invoice"}))
    follow(client, post(client, f"/review/{invoice_id}/reopen"))  # Failed / rejected tab: rejected by mistake
    page = client.get(f"/review/{invoice_id}").text
    assert "TYPO-123" not in page and 'value="NW-2026-0912"' in page


def test_restoring_a_backup_forgets_the_edits_of_invoices_it_removes(client, store):
    """After a restore, invoice numbers are used again: a new invoice must not open with an old one's edits."""
    backup = store.backup_now("manual")  # before any invoice
    first = add_clean(store)
    check(client, first, form_of(store, first, vendor_name="Northwind (edited)"))
    follow(client, post(client, "/settings/data/restore", {"name": backup.name, "confirm": "RESTORE"}))
    second = add_clean(store, "laurentides_QC_TPS_TVQ_SIL-4471")
    assert second == first  # the restored database numbers invoices from where the backup was
    page = client.get(f"/review/{second}").text
    assert "Northwind (edited)" not in page and store.get_invoice(second)["vendor_name"] in page


def test_a_deleted_invoice_leaves_no_edits_behind(client, store):
    first = add_clean(store)
    check(client, first, form_of(store, first, vendor_name="Northwind (edited)"))
    follow(client, post(client, f"/review/{first}/delete", {"back": "/review"}))
    assert first not in client.app.state.invoice_drafts


# --- Someone else acted meanwhile ------------------------------------------------------------------------------------


def test_actions_on_an_invoice_someone_else_just_changed_show_a_message_not_an_error(client, store):
    def approved_by_sam(invoice_id):
        store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Sam")

    def exported_by_sam(invoice_id):
        approved_by_sam(invoice_id)
        store.create_export_batch([invoice_id], "csv", actor="Sam")

    for action, meanwhile, data in (("reject", exported_by_sam, {"reason": "x"}),
                                    ("park", approved_by_sam, {"reason": "x"})):  # fmt: skip
        invoice_id = add_clean(store, invoice_number=f"N-{action}")
        meanwhile(invoice_id)
        response = post(client, f"/review/{invoice_id}/{action}", data)
        assert (
            response.status_code == 200
            and "this invoice changed meanwhile, in another tab or by someone else"
            in client.get(response.json()["redirect"]).text
        ), action
        assert store.get_invoice(invoice_id)["status"] == APPROVED


def test_second_approval_on_an_invoice_someone_else_just_handled(client, store, monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Kim")
    store.set_setting("approval_limit", "100")
    invoice_id = add_clean(store)
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Sam")
    assert store.get_invoice(invoice_id)["status"] == PENDING
    assert f"/review/{invoice_id}/final-approve" in client.get("/review?tab=second").text
    store.final_approve(invoice_id, "Lee", shown=store.latest_approval_id(invoice_id))
    response = post(client, f"/review/{invoice_id}/final-approve")
    assert "someone else changed this invoice meanwhile" in client.get(response.json()["redirect"]).text
    assert store.get_invoice(invoice_id)["second_reviewer"] == "Lee"


# --- Process invoices: uploads and saved emails ----------------------------------------------------------------------


def _fake_pipeline(monkeypatch, vendor="Northwind"):
    from ap_coder.web.pages import process

    class Pipeline:
        def __init__(self, *args, **kwargs):
            pass

        def process(self, path):
            return SimpleNamespace(ok=True, output={"vendor_name": vendor}, report=None, error=None)

    monkeypatch.setattr(process, "InvoicePipeline", Pipeline)
    return process


def _finished(client):
    job = client.app.state.jobs.get("process")
    assert job.wait(60), "processing took too long"
    return client.get("/process/status").json()


def test_one_upload_that_cannot_be_saved_does_not_stop_the_others(client, store, monkeypatch):
    """A long name (300 characters, or 90 Chinese ones) is shortened; a file Windows still refuses is said and
    skipped, and the other files of the upload are processed."""
    process = _fake_pipeline(monkeypatch)
    real_save = process.save_upload

    def save(folder, name, content):
        if name.startswith("locked"):
            raise PermissionError(13, "Permission denied")
        return real_save(folder, name, content)

    monkeypatch.setattr(process, "save_upload", save)
    files = [("files", (name, content, "application/pdf")) for name, content in (
        ("a" * 300 + ".pdf", b"%PDF-1.4 long"), ("发票" * 45 + ".pdf", b"%PDF-1.4 cjk"),
        ("locked.pdf", b"%PDF-1.4 locked"))]  # fmt: skip
    assert client.post("/process/upload", files=files, headers={"X-AP-Coder": "1"}).status_code == 200
    snap = _finished(client)
    saved = sorted(p.name for p in invoice_dir(store).iterdir() if p.is_file())
    assert len(saved) == 2 and all(len(n) <= 80 and len(n.encode()) <= 200 and n.endswith(".pdf") for n in saved)
    texts = [m["text"] for m in snap["result"]["messages"]]
    assert any("locked.pdf could not be saved" in t for t in texts), texts
    assert any("2 invoices read and coded" in t for t in texts), texts


def test_safe_file_name_is_short_enough_for_windows():
    from ap_coder.web.pages.process import safe_file_name

    assert safe_file_name("C:\\Users\\x\\" + "b" * 300 + ".PDF") == "b" * 76 + ".PDF"
    assert safe_file_name("../.hidden.pdf") == "hidden.pdf"
    assert safe_file_name("") == "invoice"
    cjk = safe_file_name("发票" * 45 + ".pdf")
    assert cjk.endswith(".pdf") and len(cjk.encode()) <= 200


def test_saved_emails_in_the_invoices_folder_are_unpacked(client, store, monkeypatch):
    _fake_pipeline(monkeypatch)
    m = EmailMessage()
    m["Subject"] = "Invoice"
    m.set_content("attached")
    m.add_attachment(b"%PDF-1.4 x", maintype="application", subtype="pdf", filename="INV-9.pdf")
    folder = invoice_dir(store)
    folder.mkdir(parents=True)
    (folder / "vendor mail.eml").write_bytes(m.as_bytes())
    os.utime(folder / "vendor mail.eml", (1, 1))  # copied a while ago
    assert "vendor mail.eml" in client.get("/process").text  # offered with the new files
    post(client, "/process/folder")
    snap = _finished(client)
    assert (folder / "vendor mail - INV-9.pdf").exists() and (folder / "emails" / "vendor mail.eml").exists()
    assert any("Invoice; 1 invoice to the review queue" in line["text"] for line in snap["lines"])
    assert snap["result"]["ok"] == 1


def _nested_email(depth: int) -> bytes:
    msg = 'Content-Type: application/pdf\nContent-Disposition: attachment; filename="inv.pdf"\n\n%PDF-1.4 x'
    for n in range(depth):
        msg = f"Content-Type: message/rfc822\n\nSubject: Fwd {n}\nMIME-Version: 1.0\n" + msg
    return ("From: a@b.example\nSubject: deep\nMIME-Version: 1.0\n" + msg).encode()


def test_a_crafted_email_in_the_folder_does_not_break_the_process_page(client, store):
    """1000 emails forwarded inside each other made the page fail on every visit, the email never moved."""
    folder = invoice_dir(store)
    folder.mkdir(parents=True)
    (folder / "deep.eml").write_bytes(_nested_email(1000))
    os.utime(folder / "deep.eml", (1_700_000_000, 1_700_000_000))
    assert client.get("/process").status_code == 200
    post(client, "/process/folder")
    snap = _finished(client)
    assert any("deep.eml" in line["text"] and "could not be read" in line["text"] for line in snap["lines"]), snap
    assert not (folder / "deep.eml").exists()
    assert (folder / "emails" / "could not read" / "deep.eml").exists()
    assert client.get("/process").status_code == 200  # and the next visit is fine too


# --- Only this computer ----------------------------------------------------------------------------------------------


def test_host_allowed_only_for_this_computer():
    import socket

    from ap_coder.web.security import host_allowed

    this_computer = ("localhost:8501", "127.0.0.1:8501", "[::1]:8501", "LOCALHOST", "127.0.0.2:8501", "localhost.")
    for host in (*this_computer, "[::ffff:7f00:1]:8501"):
        assert host_allowed(host, "127.0.0.1"), host
    for host in ("evil.example:8501", "localhost.evil.example", "127.0.0.1.nip.io:8501", "", "[::ffff:7f00:1"):
        assert not host_allowed(host, "127.0.0.1"), host
    assert host_allowed(None, "127.0.0.1")  # no Host header: not a browser
    assert host_allowed("ap-laptop:8501", "127.0.0.1", ("ap-laptop",))  # the configured browser address
    # Shared on the office network (0.0.0.0): an IP address or this computer's name, never another site's name.
    assert host_allowed("192.168.1.20:8501", "0.0.0.0")
    assert host_allowed(f"{socket.gethostname()}:8501", "0.0.0.0")
    assert not host_allowed("evil.example:8501", "0.0.0.0")
    assert host_allowed("10.0.0.5:8501", "10.0.0.5") and not host_allowed("10.0.0.6:8501", "10.0.0.5")


# --- The other pages -------------------------------------------------------------------------------------------------


def test_the_queue_says_what_is_ready_to_export(client, store, setup_demo_template):
    fill_from(store, setup_demo_template)
    assert "ready to export" in client.get("/review").text  # the demo's approved invoices are not exported yet


def test_coding_rules_tab_suggests_and_adds_a_rule(client, store):
    doc = gt()
    same = {**doc, "line_items": [{**li, "predicted_gl_code": "6010"} for li in doc["line_items"]]}
    store.approve_invoice(store.add_invoice(store.path.parent / "a.pdf", same, {}), same, "Jane")
    page = client.get("/accounts?tab=rules").text
    assert "Suggested from past coding" in page and 'name="gl_code" value="6010"' in page
    follow(client, post(client, "/accounts/rules/add", {"vendor": doc["vendor_name"], "gl_code": "6010"}))
    (rule,) = store.coding_rules()
    assert (rule.vendor, rule.gl_code) == (doc["vendor_name"], "6010")
    assert "Suggested from past coding" not in client.get("/accounts?tab=rules").text  # covered now


def test_rule_and_vendor_names_are_shown_as_text(client, store):
    name = "Price $5 & $10 *Wholesale* <b>"
    page = follow(client, post(client, "/accounts/rules/add", {"vendor": name, "gl_code": "6010"}))
    assert "Rule added: Price $5 &amp; $10 *Wholesale* &lt;b&gt; → 6010." in page


def test_the_rules_tab_shows_the_rules_of_a_restored_backup(client, store):
    store.save_coding_rules([Rule("Purolator", "", "6900")])
    backup = store.backup_now("manual")
    store.save_coding_rules([Rule("Bell", "", "6200")])
    assert 'value="Bell"' in client.get("/accounts?tab=rules").text
    follow(client, post(client, "/settings/data/restore", {"name": backup.name, "confirm": "RESTORE"}))
    page = client.get("/accounts?tab=rules").text
    assert 'value="Purolator"' in page and 'value="Bell"' not in page


def test_vendors_named_like_the_pages_own_words_open(client, store):
    for name in ("List Corp", "Pick Ltd."):
        store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", {**gt(), "vendor_name": name, "invoice_number": name}, {})
    for key in ("list", "pick", "all", "hold"):
        assert client.get("/vendors", params={"open": key}).status_code == 200


def test_vendors_page_with_the_demo_shows_the_vendors_that_make_work(client, store, setup_demo_template):
    from ap_coder.erp.insights import vendor_workload

    fill_from(store, setup_demo_template)
    page = client.get("/vendors").text
    if vendor_workload(store):
        assert "Vendors that make work" in page


def test_spend_all_currencies_in_cad(client, store, setup_demo_template):
    fill_from(store, setup_demo_template)
    cascade = next(i for i in store.list_invoices() if i["currency"] == "USD")
    store.approve_invoice(cascade["id"], store.get_invoice(cascade["id"])["ai_output"], "Jane")
    store.set_setting("fx_rates", "USD=1.37")
    period = {"start": "2020-01-01", "end": "2030-12-31"}
    page = client.get("/spend", params=period).text
    assert '<option value="All, in CAD" selected>' in page
    assert client.get("/spend", params={**period, "currency": "All, in CAD"}).status_code == 200
    assert client.get("/sales-tax", params=period).status_code == 200


def test_the_training_zip_holds_the_corrected_approval(client, store, monkeypatch):
    monkeypatch.setenv("AP_DEVELOPER_TOOLS", "1")  # a developer tool (B-511)
    invoice_id = add_clean(store)
    store.approve_invoice(invoice_id, dict(gt(), invoice_number="WRONG-1"), "Jane")

    def zip_text():
        response = client.get("/learning/training.zip")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            return " ".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist() if not n.endswith(".png"))

    assert "WRONG-1" in zip_text()
    store.reopen(invoice_id, "Jane", "typo")  # corrected and approved again: as many approved invoices as before
    store.approve_invoice(invoice_id, dict(gt(), invoice_number="RIGHT-1"), "Jane")
    text = zip_text()
    assert "RIGHT-1" in text and "WRONG-1" not in text


def test_opening_the_dashboard_leaves_no_page_reader_thread_behind(store):
    """A page-reader thread left running by one test acts on the next test's fakes (CI on Windows, Python 3.13)."""
    import threading

    from fastapi.testclient import TestClient

    from ap_coder.web.app import create_app

    from .webclient import web_settings

    with TestClient(create_app(web_settings(), store), base_url="http://localhost") as client:
        assert client.get("/review").status_code == 200
    assert not [t for t in threading.enumerate() if t.name == "ap-coder-page-reader" and t.is_alive()]
