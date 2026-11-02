"""The ready-made email asking a manager to confirm an invoice's cost allocation (D-138)."""

from __future__ import annotations

from email import message_from_bytes
from email.policy import default

from ap_coder.coding.manager_mail import MAX_ATTACHMENT, request_email, subject

INV = {"id": 12, "vendor_name": "Brightline Janitorial Ltd.", "invoice_number": "90311", "invoice_date": "2026-10-20",
       "grand_total": 565.0, "currency": "CAD"}  # fmt: skip
REQ = {"id": 4, "manager_name": "Bob Manager", "manager_email": "bob@example.com", "note": "New vendor for the site",
       "cost_centers": ["CC-200"],
       "lines": [{"line": 1, "description": "Office cleaning <October>", "gl_code": "6100", "cost_center": "CC-200",
                  "amount": 400.0},
                 {"line": 2, "description": "Supplies", "gl_code": "6110", "cost_center": "CC-300",
                  "amount": 100.0}]}  # fmt: skip


def test_the_email_opens_as_a_draft_to_the_manager_with_the_invoice_and_the_coding(tmp_path):
    pdf = tmp_path / "brightline 90311.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    msg = message_from_bytes(request_email(INV, REQ, pdf, signature="Ann, Accounts payable"), policy=default)
    assert msg["To"] == "bob@example.com" and msg["X-Unsent"] == "1"
    assert msg["Subject"] == (
        "Please confirm the cost allocation: Brightline Janitorial Ltd. invoice 90311 (565.00 CAD) [AP-4]"
    )
    text = msg.get_body(("plain",)).get_content()
    assert "1 | Office cleaning <October> | 6100 | CC-200 | 400.00 CAD" in text and "reply CONFIRMED" in text
    assert "Note from AP: New vendor for the site" in text and "Ann, Accounts payable" in text
    page = msg.get_body(("html",)).get_content()
    assert "Office cleaning &lt;October&gt;" in page and "<October>" not in page  # escaped
    assert "#fff4e0" in page  # the manager's own cost center marked
    (attached,) = list(msg.iter_attachments())
    assert attached.get_filename() == "brightline 90311.pdf" and attached.get_content() == b"%PDF-1.4 test"


def test_a_file_too_large_to_send_is_left_out_and_said(tmp_path):
    big = tmp_path / "scan.pdf"
    big.write_bytes(b"0" * (MAX_ATTACHMENT + 1))
    msg = message_from_bytes(request_email(INV, REQ, big), policy=default)
    assert list(msg.iter_attachments()) == [] and "too large to attach" in msg.get_body(("plain",)).get_content()


def test_an_invoice_without_a_number_or_total_still_has_a_subject():
    assert subject({}, 9) == "Please confirm the cost allocation: an invoice invoice without a number [AP-9]"
