"""Sweep 3, intake: emails whose attachments were left out, forwarded emails in base64, Outlook's Content-IDs, the
inbox lists, the demo's reference data and the command line (B-140 to B-149)."""

from __future__ import annotations

import base64
import io
import zipfile
from email.message import EmailMessage

import pymupdf
from PIL import Image, ImageDraw

from ap_coder.bench.msgwriter import Attachment, Message, build_msg
from ap_coder.intake import mailbox, triage
from ap_coder.intake.mailbox import PERSON_FOLDER, read_eml, read_msg_mail, unpack


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    out = doc.tobytes()
    doc.close()
    return out


def _page() -> Image.Image:
    img = Image.new("1", (1700, 2200), 1)
    draw = ImageDraw.Draw(img)
    for y in range(100, 2100, 40):
        draw.text((100, y), f"INVOICE INV-77  line {y} ...... 123.45", fill=0)
    return img


def _scan_tiff() -> bytes:
    """A 1-bit fax-style scan: Group 4 makes a full page of text only ~12 KB."""
    buf = io.BytesIO()
    _page().save(buf, "TIFF", compression="group4", dpi=(200, 200))
    return buf.getvalue()


def _scan_jpeg() -> bytes:
    """The same page scanned in grey: ~105 KB, under the size of the largest signature logo."""
    buf = io.BytesIO()
    _page().convert("L").save(buf, "JPEG", quality=60, dpi=(200, 200))
    return buf.getvalue()


# --- B-140: an attachment left out is never lost silently ------------------------------------------------------------


def _mixed_email() -> EmailMessage:
    m = EmailMessage()
    m["Subject"], m["From"] = "Invoices for October", "ar@vendor.example"
    m.set_content("Please find attached invoice INV-1 and the other invoices in the zip.")
    m.add_attachment(
        _pdf("INVOICE No: INV-1  Total 100.00"), maintype="application", subtype="pdf", filename="INV-1.pdf"
    )
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("INV-2.pdf", _pdf("INVOICE No: INV-2  Total 200.00"))
    m.add_attachment(z.getvalue(), maintype="application", subtype="zip", filename="more-invoices.zip")
    inner = EmailMessage()
    inner["Subject"] = "Invoice INV-3"
    inner.set_content("see attached")
    inner.add_attachment(_pdf("INVOICE No: INV-3  Total 300.00"), maintype="application", subtype="pdf",
                         filename="INV-3.pdf")  # fmt: skip
    m.add_attachment(inner.as_bytes(), maintype="application", subtype="octet-stream", filename="forwarded.eml")
    return m


def test_an_email_with_an_attachment_left_out_waits_for_a_person_with_the_reason(tmp_path):
    path = tmp_path / "mail.eml"
    path.write_bytes(_mixed_email().as_bytes())
    result = unpack(path)
    # The invoice inside the attached .eml file is read like a forwarded email's.
    assert [p.name for p in result.saved] == ["mail - INV-1.pdf", "mail - INV-3.pdf"]
    # The zip is not opened: the email waits for a person, who is told what was left out.
    assert result.triage.needs_person and result.triage.kind == triage.INVOICE
    assert any("more-invoices.zip" in r and "left out" in r for r in result.triage.reasons)
    assert (tmp_path / "emails" / PERSON_FOLDER / "mail.eml").exists()


def test_a_signed_email_or_a_small_logo_does_not_need_a_person(tmp_path):
    m = EmailMessage()
    m["Subject"], m["From"] = "Invoice INV-9", "ar@vendor.example"
    m.set_content("Invoice attached.")
    m.add_attachment(
        _pdf("INVOICE No: INV-9  Total 10.00"), maintype="application", subtype="pdf", filename="INV-9.pdf"
    )
    m.add_attachment(b"\x89PNG" + b"0" * 900, maintype="image", subtype="png", filename="image001.png")
    m.add_attachment(b"0\x82sig", maintype="application", subtype="pkcs7-signature", filename="smime.p7s")
    path = tmp_path / "signed.eml"
    path.write_bytes(m.as_bytes())
    result = unpack(path)
    assert [p.name for p in result.saved] == ["signed - INV-9.pdf"] and not result.triage.needs_person
    assert (tmp_path / "emails" / "signed.eml").exists()


def test_a_small_black_and_white_tiff_scan_is_read_not_dropped_as_a_logo():
    tif = _scan_tiff()
    assert len(tif) < mailbox.MIN_IMAGE_BYTES  # the size alone made it a "logo"
    m = EmailMessage()
    m["Subject"] = "Invoice INV-77"
    m.set_content("Invoice attached.")
    m.add_attachment(tif, maintype="image", subtype="tiff", filename="scan.tif")
    assert [n for n, _ in read_eml(m.as_bytes()).found] == ["scan.tif"]


def test_an_outlook_email_attached_to_an_eml_is_opened():
    inner = build_msg(Message(subject="Invoice INV-5", body="see attached",
                              attachments=[Attachment("INV-5.pdf", _pdf("INVOICE INV-5"))]))  # fmt: skip
    m = EmailMessage()
    m["Subject"] = "Fwd: invoice"
    m.set_content("forwarding")
    m.add_attachment(inner, maintype="application", subtype="vnd.ms-outlook", filename="Invoice INV-5.msg")
    mail = read_eml(m.as_bytes())
    assert [n for n, _ in mail.found] == ["INV-5.pdf"] and "see attached" in mail.body


def test_an_eml_file_attached_to_an_outlook_email_is_opened():
    inner = EmailMessage()
    inner["Subject"] = "Invoice INV-6"
    inner.set_content("see attached")
    inner.add_attachment(_pdf("INVOICE INV-6"), maintype="application", subtype="pdf", filename="INV-6.pdf")
    raw = build_msg(Message(subject="Fwd", attachments=[Attachment("fwd.eml", inner.as_bytes(), "message/rfc822")]))
    assert [n for n, _ in read_msg_mail(raw).found] == ["INV-6.pdf"]


def test_emails_attached_as_files_inside_each_other_are_opened_only_so_deep():
    raw = EmailMessage()
    raw["Subject"] = "deepest"
    raw.set_content("x")
    raw.add_attachment(b"%PDF-1.4 x", maintype="application", subtype="pdf", filename="inv.pdf")
    data = raw.as_bytes()
    for n in range(mailbox.MAX_FORWARD_DEPTH + 2):
        m = EmailMessage()
        m["Subject"] = f"Fwd {n}"
        m.set_content("x")
        m.add_attachment(data, maintype="application", subtype="octet-stream", filename=f"fwd{n}.eml")
        data = m.as_bytes()
    mail = read_eml(data)
    assert mail.found == [] and any("levels deep" in s for s in mail.skipped)


# --- B-206: the parts that come with ordinary invoice emails are not "left out" ----------------------------------


def _invoice_with(*extra: tuple[str, str, str, bytes]) -> EmailMessage:
    m = EmailMessage()
    m["Subject"], m["From"] = "Invoice INV-12", "ar@vendor.example"
    m.set_content("Please find attached invoice INV-12.")
    m.add_attachment(
        _pdf("INVOICE No: INV-12  Total 50.00"), maintype="application", subtype="pdf", filename="INV-12.pdf"
    )
    for name, maintype, subtype, data in extra:
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return m


def test_exchange_footers_outlook_formatting_and_invites_do_not_make_an_invoice_email_wait(tmp_path):
    """Exchange adds ATT00001.htm / ATT00001.txt (a footer), Outlook winmail.dat (its formatting), a vendor a calendar
    invite: none holds an invoice, so the email is filed like any invoice email."""
    for n, part in enumerate([
        ("ATT00001.txt", "text", "plain", b"Thanks"),
        ("ATT00001.htm", "text", "html", b"<p>Thanks</p>"),
        ("winmail.dat", "application", "ms-tnef", b"\x78\x9f\x3e\x22" + b"x" * 100),
        ("invite.ics", "text", "calendar", b"BEGIN:VCALENDAR"),
    ]):  # fmt: skip
        path = tmp_path / f"m{n}.eml"
        path.write_bytes(_invoice_with(part).as_bytes())
        result = unpack(path)
        assert [p.name for p in result.saved] == [f"m{n} - INV-12.pdf"], part[0]
        assert not result.triage.needs_person and not result.triage.left_out, (part[0], result.triage.reasons)
        assert (tmp_path / "emails" / f"m{n}.eml").exists()


def test_parts_that_may_hold_an_invoice_still_make_the_email_wait(tmp_path):
    """A Word file, an XML (UBL) invoice, an HTML file named as an invoice, an RTF: a person must look."""
    for n, part in enumerate([
        ("terms.docx", "application", "vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK doc"),
        ("INV-13.xml", "application", "xml", b"<Invoice/>"),
        ("Invoice INV-14.html", "text", "html", b"<h1>Invoice INV-14</h1>"),
        ("INV-15.rtf", "application", "rtf", b"{\\rtf1 Invoice}"),
    ]):  # fmt: skip
        path = tmp_path / f"w{n}.eml"
        path.write_bytes(_invoice_with(part).as_bytes())
        result = unpack(path)
        assert result.triage.needs_person and any(part[0] in s for s in result.triage.left_out), part[0]


def test_an_outlook_winmail_dat_alone_is_left_out_since_it_may_hold_the_attachments():
    m = EmailMessage()
    m["Subject"] = "Invoice INV-16"
    m.set_content("Invoice attached.")
    m.add_attachment(
        b"\x78\x9f\x3e\x22" + b"x" * 100, maintype="application", subtype="ms-tnef", filename="winmail.dat"
    )
    assert any("winmail.dat" in s for s in read_eml(m.as_bytes()).left_out)


# --- B-207: a small fax scan named .tiff ----------------------------------------------------------------------------


def test_a_small_tiff_scan_named_tiff_is_read_not_dropped_as_a_logo():
    tif = _scan_tiff()
    for name in ("fax.tiff", "scan.TIFF"):
        m = EmailMessage()
        m["Subject"] = "Invoice INV-78"
        m.set_content("Invoice attached.")
        m.add_attachment(tif, maintype="image", subtype="tiff", filename=name)
        assert [n for n, _ in read_eml(m.as_bytes()).found] == [name], name


# --- B-141: a forwarded email sent in base64 -------------------------------------------------------------------------


def test_a_forwarded_email_sent_in_base64_is_opened():
    inner = EmailMessage()
    inner["Subject"], inner["From"] = "Invoice INV-3", "x@y.example"
    inner.set_content("see attached")
    inner.add_attachment(b"%PDF-1.4\n" + b"0" * 3000, maintype="application", subtype="pdf", filename="INV-3.pdf")
    enc = base64.encodebytes(inner.as_bytes()).decode()
    raw = ("From: fwd@b.example\r\nSubject: Fwd: invoice\r\nMIME-Version: 1.0\r\n"
           "Content-Type: multipart/mixed; boundary=XX\r\n\r\n--XX\r\nContent-Type: text/plain\r\n\r\nForwarding\r\n"
           "--XX\r\nContent-Type: message/rfc822\r\nContent-Disposition: attachment; filename=\"Invoice INV-3.eml\"\r\n"
           f"Content-Transfer-Encoding: base64\r\n\r\n{enc}\r\n--XX--\r\n").encode()  # fmt: skip
    mail = read_eml(raw)
    assert [n for n, _ in mail.found] == ["INV-3.pdf"] and mail.skipped == []


# --- B-142: Outlook keeps a Content-ID on every attachment from Gmail ----------------------------------------------


def test_a_scanned_invoice_with_a_content_id_in_an_outlook_email_is_kept():
    jpg = _scan_jpeg()
    assert mailbox.MIN_IMAGE_BYTES < len(jpg) < mailbox.MAX_LOGO_BYTES
    msg = Message(subject="Invoice INV-77", body="Invoice attached.",
                  attachments=[Attachment("scan.jpg", jpg, "image/jpeg", content_id="f_lx3abc0")])  # fmt: skip
    assert [n for n, _ in read_msg_mail(build_msg(msg)).found] == ["scan.jpg"]


def test_a_picture_the_outlook_email_shows_in_its_text_is_a_logo():
    jpg = _scan_jpeg()
    html = '<p>Thanks</p><img src="cid:image001.jpg@01DA">'
    picture = Attachment("image001.jpg", jpg, "image/jpeg", content_id="image001.jpg@01DA")
    msg = Message(subject="Invoice INV-78", body="Invoice attached.", html=html, attachments=[picture])
    mail = read_msg_mail(build_msg(msg))
    assert mail.found == [] and any("logo" in s for s in mail.skipped)


def test_a_picture_outlook_marks_hidden_is_a_logo():
    jpg = _scan_jpeg()
    msg = Message(subject="Invoice INV-79", body="Invoice attached.",
                  attachments=[Attachment("image002.jpg", jpg, "image/jpeg", "image002.jpg@01DA", hidden=True),
                               Attachment("scan.jpg", jpg, "image/jpeg", "f_lx3abc1")])  # fmt: skip
    mail = read_msg_mail(build_msg(msg))
    assert [n for n, _ in mail.found] == ["scan.jpg"] and any("image002.jpg" in s for s in mail.skipped)


# --- B-143: the doctor on a damaged database -------------------------------------------------------------------------


def test_the_doctor_reports_a_damaged_database_instead_of_crashing(tmp_path, capsys):
    from ap_coder.ops import cli

    db = tmp_path / "ap_coder.db"
    db.write_bytes(b"this is not a database " * 200)
    assert cli.main(["--db", str(db), "doctor"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] database" in out and "ap_coder.db" in out and "failure(s)" in out


def test_the_doctor_warns_when_iphone_photos_cannot_be_read(monkeypatch):
    from ap_coder.core.config import Settings
    from ap_coder.ops import doctor

    real = doctor._version
    monkeypatch.setattr(doctor, "_version", lambda pkg: None if pkg == "pillow-heif" else real(pkg))
    checks = doctor.run_checks(Settings(), lambda: (_ for _ in ()).throw(ValueError("no data")), llm_checks=False)
    heif = next(c for c in checks if c.area == "package pillow-heif")
    assert heif.status == doctor.WARN and "HEIC" in heif.detail


# --- B-144: an emailed statement stays on the Statements page ---------------------------------------------------


class _NoEvents:
    def event_values(self, action, field):
        return set()

    def log_event(self, *args, **kwargs):
        pass


def test_an_emailed_statement_stays_listed_however_many_emails_were_sorted_after_it(tmp_path):
    import json

    from ap_coder.intake import inbox

    (tmp_path / "statements").mkdir()
    (tmp_path / "statements" / "s - stmt.pdf").write_bytes(b"%PDF-1.4")
    stmt = {"id": "stmt01", "kind": "statement", "needs_person": False,
            "documents": [{"name": "stmt.pdf", "kind": "statement", "file": "statements/s - stmt.pdf"}]}  # fmt: skip
    newer = [{"id": f"inv{n:04d}", "kind": "invoice", "needs_person": False, "documents": []} for n in range(600)]
    (tmp_path / "emails").mkdir()
    (tmp_path / "emails" / "triage.jsonl").write_text("".join(json.dumps(e) + "\n" for e in [stmt, *newer]))
    assert [s["id"] for s in inbox.statements(_NoEvents(), tmp_path)] == ["stmt01"]


# --- B-145: a spreadsheet is never sent to the invoices folder ---------------------------------------------------


def test_a_spreadsheet_set_aside_cannot_be_sent_to_the_review_queue(tmp_path):
    import pytest

    from ap_coder.intake import inbox

    m = EmailMessage()
    m["Subject"], m["From"] = "October billing", "ar@vendor.example"
    m.set_content("Attached is our October billing.")
    m.add_attachment(b"Item,Qty,Price\nWidgets,10,12.50\n", maintype="text", subtype="csv", filename="october.csv")
    (tmp_path / "m.eml").write_bytes(m.as_bytes())
    unpack(tmp_path / "m.eml")
    (row,) = inbox.needs_person(_NoEvents(), tmp_path)
    (doc,) = row["docs"]
    assert doc["sheet"]  # the page offers no "Read as invoice" for it
    with pytest.raises(ValueError, match="spreadsheet"):
        inbox.to_queue(_NoEvents(), tmp_path, row["id"], doc["n"])
    assert inbox.needs_person(_NoEvents(), tmp_path)[0]["docs"][0]["there"]  # still where it was


def test_the_process_page_offers_no_read_as_invoice_for_a_spreadsheet(tmp_path):
    from ap_coder.core.store import Store, load_sample_setup
    from ap_coder.web.core import invoice_dir

    from .webclient import client_for, post

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    client = client_for(store)
    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    m = EmailMessage()
    m["Subject"], m["From"] = "October billing", "ar@vendor.example"
    m.set_content("Attached is our October billing.")
    m.add_attachment(b"Item,Qty,Price\nWidgets,10,12.50\n", maintype="text", subtype="csv", filename="october.csv")
    (folder / "m.eml").write_bytes(m.as_bytes())
    unpack(folder / "m.eml", folder)
    page = client.get("/process").text
    assert "october.csv" in page and "/to-queue/" not in page
    from ap_coder.intake import inbox

    (row,) = inbox.needs_person(store, folder)
    answer = post(client, f"/process/inbox/{row['id']}/to-queue/0")
    assert answer.status_code >= 400 and "spreadsheet" in answer.text


# --- B-146: removing the demo removes the sample setup it loaded ------------------------------------------------


def test_removing_the_demo_removes_the_sample_setup_it_loaded(tmp_path):
    from ap_coder.coding.tax import DEFAULT_TREATMENTS
    from ap_coder.core.store import Store
    from ap_coder.ops.demo import load_demo, remove_demo

    store = Store(tmp_path / "ap.db")
    load_demo(store)
    assert store.has_reference()
    remove_demo(store)
    assert store.list_accounts("gl_accounts") == [] and store.list_accounts("cost_centers") == []
    assert store.get_setting("policy_notes") == "" and not store.has_reference()
    assert {t: x.treatment for t, x in store.tax_treatments().items()} == DEFAULT_TREATMENTS
    assert all(not x.gl_code for x in store.tax_treatments().values())


def test_removing_the_demo_keeps_the_setup_once_the_user_changed_it(tmp_path):
    from ap_coder.core.store import Store
    from ap_coder.ops.demo import load_demo, remove_demo

    store = Store(tmp_path / "ap.db")
    load_demo(store)
    rows = store.list_accounts("gl_accounts")
    rows[0]["description"] = "Our own name for it"
    store.save_accounts("gl_accounts", rows)
    store.set_setting("policy_notes", "Coffee goes to 6100.")
    remove_demo(store)
    assert store.list_accounts("gl_accounts") == rows  # changed: kept
    assert store.get_setting("policy_notes") == "Coffee goes to 6100."
    assert store.list_accounts("cost_centers") == []  # not changed: removed with the demo


# --- B-147: `process` on a folder of emails only ------------------------------------------------------------------


def test_process_logs_the_emails_it_sorted_and_does_not_fail_when_there_was_nothing_else(tmp_path):
    import os
    import time

    from ap_coder.core.store import Store
    from ap_coder.intake import inbox
    from ap_coder.ops import cli

    folder = tmp_path / "invoices"
    folder.mkdir()
    m = EmailMessage()
    m["Subject"], m["From"] = "Please note our new bank details", "ar@vendor.example"
    m.set_content("Our banking details have changed. Please update your records before the next payment.")
    (folder / "bank.eml").write_bytes(m.as_bytes())
    os.utime(folder / "bank.eml", (time.time() - 60, time.time() - 60))
    db = tmp_path / "ap_coder.db"
    assert cli.main(["--db", str(db), "process", str(folder)]) == 0
    events = Store(db).events(actions=[inbox.FRAUD_ACTION], limit=10)
    assert len(events) == 1 and events[0]["detail"]["email"] == "bank.eml"


# --- B-148: a picture too large to open -----------------------------------------------------------------------------


def test_a_picture_too_large_to_open_is_said_to_be_too_large_not_damaged(tmp_path, monkeypatch):
    import pytest
    from PIL import Image

    from ap_coder.ai.pipeline import plain_error
    from ap_coder.intake.pictures import to_pdf

    photo = tmp_path / "IMG_2026.jpg"
    Image.new("L", (400, 300), 200).save(photo, "JPEG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)  # 120,000 pixels: over twice the limit, as 200 MP is
    with pytest.raises(Image.DecompressionBombError) as caught:
        to_pdf(photo)
    said = plain_error(caught.value, photo)
    assert "too large" in said and "megapixels" in said and "damaged" not in said


# --- B-149: `evaluate` without a labels file ---------------------------------------------------------------------


def test_evaluate_without_a_labels_file_says_so_in_one_line(tmp_path, capsys):
    from ap_coder.ops import cli

    missing = tmp_path / "labels.xlsx"
    assert cli.main(["evaluate", "--predictions", str(tmp_path), "--ground-truth", str(missing)]) == 2
    err = capsys.readouterr().err
    assert "labels.xlsx" in err and "Traceback" not in err and len(err.strip().splitlines()) == 1
