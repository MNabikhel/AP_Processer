"""Approval-stamped PDFs."""

import io
import json
import zipfile

import pymupdf
from PIL import Image

from ap_coder import stamp
from ap_coder.store import Store

from .conftest import SAMPLE_STEM, SAMPLES


def _approved(tmp_path, source):
    store = Store(tmp_path / "s.db")
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    gt["gl_distribution"] = [{"kind": "expense", "gl_code": "6010", "cost_center": "CC400", "description": "Laptops",
                              "amount": 4350.0}]  # fmt: skip
    invoice_id = store.add_invoice(source, gt, {})
    store.approve_invoice(invoice_id, gt, "Jordan Lée")
    return store, store.get_invoice(invoice_id)


def _text(pdf: bytes) -> list[str]:
    with pymupdf.open("pdf", pdf) as doc:
        return [page.get_text() for page in doc]


def test_pdf_keeps_its_pages_and_gets_a_stamp_and_a_coding_page(tmp_path):
    source = SAMPLES / f"{SAMPLE_STEM}.pdf"
    with pymupdf.open(source) as original:
        pages = original.page_count
    _, inv = _approved(tmp_path, source)
    text = _text(stamp.stamped_pdf(inv, {"6010": "IT Hardware"}))
    assert len(text) == pages + 1
    assert "APPROVED" in text[0] and "Jordan Lée" in text[0] and f"AP Coder #{inv['id']}" in text[0]
    assert "6010 IT Hardware" in text[-1] and "4,350.00" in text[-1] and "Coding" in text[-1]


def test_image_invoice_and_missing_or_damaged_files(tmp_path):
    image = tmp_path / "scan.png"
    Image.new("RGB", (400, 500), "white").save(image)
    _, inv = _approved(tmp_path, image)
    assert len(_text(stamp.stamped_pdf(inv))) == 2  # the image as a page, then the coding page
    inv["source_path"] = str(tmp_path / "gone.pdf")
    assert len(_text(stamp.stamped_pdf(inv))) == 1  # the coding page alone
    damaged = tmp_path / "damaged.pdf"
    damaged.write_bytes(b"%PDF-1.4 not really")
    inv["source_path"] = str(damaged)
    assert len(_text(stamp.stamped_pdf(inv))) >= 1


def test_batch_zip_names_are_unique(tmp_path):
    _, inv = _approved(tmp_path, SAMPLES / f"{SAMPLE_STEM}.pdf")
    twin = {**inv, "id": inv["id"] + 1}
    with zipfile.ZipFile(io.BytesIO(stamp.batch_zip([inv, twin]))) as z:
        names = z.namelist()
    assert len(set(names)) == 2 and all(n.endswith("approved.pdf") for n in names)
    assert stamp.file_name({"id": 3, "final_output": {"vendor_name": "A/B: C", "invoice_number": "1"}}) == (
        "A_B_ C 1 - approved.pdf"
    )


def test_batch_zip_names_differ_on_windows_too(tmp_path):
    """Windows file names ignore case: "ACME ... INV-1" and "Acme ... inv-1" would unzip over each other."""
    _, inv = _approved(tmp_path, tmp_path / "gone.pdf")
    final = inv["final_output"]
    first = {**inv, "final_output": {**final, "vendor_name": "ACME Ltd", "invoice_number": "INV-1"}}
    twin = {**final, "vendor_name": "Acme Ltd", "invoice_number": "inv-1"}
    second = {**inv, "id": inv["id"] + 1, "final_output": twin}
    with zipfile.ZipFile(io.BytesIO(stamp.batch_zip([first, second]))) as z:
        names = z.namelist()
    assert len({n.casefold() for n in names}) == 2


def test_rotated_pages_and_password_protected_files(tmp_path):
    rotated = tmp_path / "rotated.pdf"
    with pymupdf.open(SAMPLES / f"{SAMPLE_STEM}.pdf") as doc:
        doc[0].set_rotation(90)
        doc.save(rotated)
    _, inv = _approved(tmp_path, rotated)
    with pymupdf.open("pdf", stamp.stamped_pdf(inv)) as out:
        page = out[0]
        (hit,) = page.search_for("APPROVED")
        assert page.rect.contains(hit * page.rotation_matrix)  # on the page as it is seen
    locked = tmp_path / "locked.pdf"
    with pymupdf.open(SAMPLES / f"{SAMPLE_STEM}.pdf") as doc:
        doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    inv["source_path"] = str(locked)
    assert len(_text(stamp.stamped_pdf(inv))) == 1  # cannot be opened: the coding page alone


def test_long_and_non_latin_text_fits(tmp_path):
    _, inv = _approved(tmp_path, tmp_path / "gone.pdf")
    inv["final_output"]["vendor_name"] = "北京公司 Ωmega — Ltd"
    inv["final_output"]["gl_distribution"] = [{"kind": "expense", "gl_code": "6010", "cost_center": "CC-" + "X" * 30,
                                               "description": "W" * 80, "amount": 1.0}]  # fmt: skip
    pdf = stamp.stamped_pdf(inv)
    (text,) = _text(pdf)
    assert "北京公司 Ωmega — Ltd" in text and "W" * 80 not in text and "..." in text
    assert len(pdf) < 200_000  # the fallback font is embedded as a subset only
