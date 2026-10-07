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
