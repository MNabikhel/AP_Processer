"""Invoices attached to saved emails (.eml)."""

import time
from email.message import EmailMessage
from unittest.mock import patch

from ap_coder.mailbox import MAX_FORWARD_DEPTH, attachments, emails_in, safe_name, unpack, unpack_folder


def _email(subject="Invoice INV-1"):
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = "billing@vendor.example"
    m.set_content("Please find our invoice attached.")
    m.add_alternative("<p>Please find our invoice attached. <img src='cid:logo'></p>", subtype="html")
    m.get_payload()[1].add_related(b"\x89PNG" + b"0" * 40_000, maintype="image", subtype="png", cid="<logo>")
    m.add_attachment(b"%PDF-1.4 invoice", maintype="application", subtype="pdf", filename="INV-1.pdf")
    m.add_attachment(b"tiny", maintype="image", subtype="png", filename="icon.png")
    m.add_attachment(b"x" * 30_000, maintype="image", subtype="jpeg", filename="photo of receipt.jpg")
    m.add_attachment(b"doc", maintype="application", subtype="msword", filename="terms.docx")
    return m


def test_invoice_attachments_only():
    m = _email()
    inner = EmailMessage()
    inner["Subject"] = "Fwd"
    inner.set_content("forwarded")
    inner.add_attachment(b"%PDF-1.4 forwarded", maintype="application", subtype="pdf", filename="INV-0.pdf")
    m.add_attachment(inner)  # an email forwarded as an attachment
    subject, found, skipped = attachments(m.as_bytes())
    assert subject == "Invoice INV-1"
    assert [name for name, _ in found] == ["INV-1.pdf", "photo of receipt.jpg", "INV-0.pdf"]
    assert dict(found)["INV-1.pdf"] == b"%PDF-1.4 invoice"
    assert any("icon.png" in s for s in skipped) and any("terms.docx" in s for s in skipped)
    assert not any("logo" in name for name, _ in found)  # the signature picture


def test_unpack_saves_attachments_and_files_the_email(tmp_path):
    (tmp_path / "Invoice from Northwind.eml").write_bytes(_email().as_bytes())
    (tmp_path / "notes.txt").write_text("x")
    assert [p.name for p in emails_in(tmp_path)] == ["Invoice from Northwind.eml"]
    assert unpack_folder(tmp_path) == []  # just written: it may still be being copied
    (result,) = unpack_folder(tmp_path, now=time.time() + 60)
    assert [p.name for p in result.saved] == [
        "Invoice from Northwind - INV-1.pdf", "Invoice from Northwind - photo of receipt.jpg",
    ]  # fmt: skip
    assert (tmp_path / "Invoice from Northwind - INV-1.pdf").read_bytes() == b"%PDF-1.4 invoice"
    assert emails_in(tmp_path) == [] and (tmp_path / "emails" / "Invoice from Northwind.eml").exists()
    # The same email again: the same files, nothing new written, the email kept under another name.
    (tmp_path / "Invoice from Northwind.eml").write_bytes(_email().as_bytes())
    (again,) = unpack_folder(tmp_path, now=time.time() + 60)
    assert [p.name for p in again.saved] == [p.name for p in result.saved]
    assert (tmp_path / "emails" / "Invoice from Northwind_1.eml").exists()


def test_unsafe_attachment_names_stay_in_the_folder(tmp_path):
    m = EmailMessage()
    m["Subject"] = "x"
    m.set_content("x")
    m.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="..\\..\\evil:name.pdf")
    path = tmp_path / "a.eml"
    path.write_bytes(m.as_bytes())
    result = unpack(path)
    (saved,) = result.saved
    assert saved.parent == tmp_path and saved.name == "a - evil_name.pdf"


def test_an_email_without_invoices(tmp_path):
    m = EmailMessage()
    m["Subject"] = "Hello"
    m.set_content("no attachment")
    path = tmp_path / "hello.eml"
    path.write_bytes(m.as_bytes())
    result = unpack(path)
    assert result.saved == [] and (tmp_path / "emails" / "hello.eml").exists()


def test_attachment_types_by_content_not_only_by_name():
    m = EmailMessage()
    m["Subject"] = "x"
    m.set_content("x")
    m.add_attachment(b"%PDF-1.4 a", maintype="application", subtype="pdf", filename="Invoice No. 12345")
    m.add_attachment(b"%PDF-1.7 b", maintype="application", subtype="octet-stream", filename="scan")
    m.add_attachment(b"not a pdf at all", maintype="application", subtype="pdf", filename="fake.pdf")
    m.add_attachment(b"\x89PNG" + b"0" * 400_000, maintype="image", subtype="png", filename="photo.png",
                     cid="<photo1>", disposition="inline")  # fmt: skip
    _, found, skipped = attachments(m.as_bytes())
    assert [n for n, _ in found] == ["Invoice No. 12345.pdf", "scan.pdf", "photo.png"]  # a big inline photo is kept
    assert any("fake.pdf" in s and "not one" in s for s in skipped)


def nested_email(depth: int) -> bytes:
    """An invoice inside ``depth`` emails forwarded inside each other."""
    msg = 'Content-Type: application/pdf\nContent-Disposition: attachment; filename="inv.pdf"\n\n%PDF-1.4 x'
    for n in range(depth):
        msg = f"Content-Type: message/rfc822\n\nSubject: Fwd {n}\nMIME-Version: 1.0\n" + msg
    return ("From: a@b.example\nSubject: deep\nMIME-Version: 1.0\n" + msg).encode()


def test_forwarded_emails_are_opened_only_so_deep():
    assert attachments(nested_email(3))[1] == [("inv.pdf", b"%PDF-1.4 x")]
    _, found, skipped = attachments(nested_email(MAX_FORWARD_DEPTH + 1))
    assert found == [] and any("levels deep" in s for s in skipped)


def test_an_email_that_cannot_be_read_is_filed_away_and_the_others_unpacked(tmp_path):
    """~1000 emails forwarded inside each other raised RecursionError (only OSError was caught): the email stayed in
    the folder and stopped every later check, also for the good emails next to it."""
    (tmp_path / "a deep.eml").write_bytes(nested_email(1000))
    (tmp_path / "b good.eml").write_bytes(_email().as_bytes())
    (tmp_path / "c huge.eml").write_bytes(b"x" * 3_000_000)
    later = time.time() + 60
    with patch("ap_coder.mailbox.MAX_EMAIL_BYTES", 2_000_000):  # a smaller limit than the real one, for the test
        deep, good, huge = unpack_folder(tmp_path, now=later)
    assert deep.error.startswith("could not be read") and "forwarded inside each other" in deep.error
    assert "larger than" in huge.error and good.error == "" and len(good.saved) == 2
    assert {p.name for p in (tmp_path / "emails" / "could not read").iterdir()} == {"a deep.eml", "c huge.eml"}
    assert emails_in(tmp_path) == [] and unpack_folder(tmp_path, now=later) == []  # nothing left to fail again


def test_any_other_error_in_one_email_is_filed_away_too(tmp_path):
    (tmp_path / "odd.eml").write_bytes(_email().as_bytes())
    with patch("ap_coder.mailbox.attachments", side_effect=LookupError("unknown encoding")):
        (odd,) = unpack_folder(tmp_path, now=time.time() + 60)
    assert odd.error == "could not be read: it is damaged (LookupError)"
    assert (tmp_path / "emails" / "could not read" / "odd.eml").exists()


def test_long_attachment_names_are_shortened_keeping_the_extension():
    assert safe_name("a" * 300 + ".pdf") == "a" * 76 + ".pdf"
    cjk = safe_name("发票" * 45 + ".pdf")
    assert cjk.endswith(".pdf") and len(cjk.encode()) <= 200
