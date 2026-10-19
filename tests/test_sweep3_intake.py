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
