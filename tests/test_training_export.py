"""Training data from AP's approvals (a local ZIP of page images and approved values), the export-training
command, and the Learning page's Readers tab (the scorecard, field by field, the export card)."""

import copy
import html
import io
import json
import re
import shutil
import sqlite3
import zipfile

import pymupdf
import pytest
from PIL import Image

from ap_coder.ai.training_export import INSTRUCTION, approved_values, export_training_set, training_invoices
from ap_coder.capture.confidence import MIN_EVIDENCE
from ap_coder.capture.types import Box, CaptureResult, FieldResult
from ap_coder.capture.workflow import AUTONOMOUS_REVIEWER, capture_invoice, learn_from_approval
from ap_coder.core.store import Store
from ap_coder.ops import cli

from .conftest import SAMPLE_STEM, SAMPLES

PDF = SAMPLES / f"{SAMPLE_STEM}.pdf"
PNG = b"\x89PNG\r\n\x1a\n"
README_TEXTS = (
    "OvisOCR2", "Qwen 3.5", "it leaves the computer only if someone copies it", "fields.jsonl", "chat.jsonl",
    "1 because their file is no longer on the computer", "1 because they are demo invoices",
    "1 because they were approved without a person", "1 because they were bulk-approved unchanged",
    "including bank account and remittance details",
)  # fmt: skip


def _approve(store, path, final, reviewer="Ann", meta=None, capture=None, bulk=False):
    invoice_id = store.add_invoice(path, final, {"requires_review": False}, meta=meta)
    if capture is not None:
        store.save_capture(invoice_id, capture)
    store.approve_invoice(invoice_id, final, reviewer, bulk=bulk)
    if capture is not None:
        learn_from_approval(store, invoice_id, final, actor=reviewer)
    return invoice_id


def _photo(path, size=(3000, 2000)):
    """A phone photo taken sideways: stored landscape, its EXIF says turn it a quarter (orientation 6)."""
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", size, "white").save(path, format="JPEG", exif=exif.tobytes())
    return path


@pytest.fixture
def approved(tmp_path, ground_truth):
    """Approved invoices of every kind, and one still in review."""
    store = Store(tmp_path / "ap.db")
    capture, _, _ = capture_invoice(PDF, ground_truth, store=store)
    photo_truth = copy.deepcopy(ground_truth) | {"invoice_number": "NW-2026-0999", "currency": "CAD"}
    ids = {
        "pdf": _approve(store, PDF, ground_truth, capture=capture.to_dict()),
        "photo": _approve(store, _photo(tmp_path / "photo.jpg"), photo_truth),
        "demo": _approve(store, PDF, ground_truth, meta={"demo": True}),
        "autonomous": _approve(store, PDF, ground_truth, reviewer=AUTONOMOUS_REVIEWER),
        "bulk": _approve(store, PDF, ground_truth, bulk=True),  # approved unchanged, never opened
        "gone": _approve(store, shutil.copy(PDF, tmp_path / "gone.pdf"), ground_truth),
        "text": _approve(store, SAMPLES / f"{SAMPLE_STEM}.md", ground_truth),
        "review": store.add_invoice(PDF, ground_truth, {"requires_review": True}),
    }
    (tmp_path / "gone.pdf").unlink()  # the file was moved or deleted since
    with sqlite3.connect(store.path) as conn:  # the photo was approved in January
        conn.execute("UPDATE invoices SET reviewed_at = '2026-01-05T10:00:00' WHERE id = ?", (ids["photo"],))
    return store, ids


def test_export_training_set(approved, tmp_path, ground_truth):
    store, ids = approved
    with pymupdf.open(PDF) as doc:
        pdf_pages = len(doc)
    out = tmp_path / "exports" / "training.zip"
    counts = export_training_set(store, out)
    assert counts == {"invoices": 2, "pages": pdf_pages + 1, "missing_files": 1, "no_pages": 1, "demo": 1,
                      "unreviewed": 1, "bulk": 1}  # fmt: skip
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        images = sorted(n for n in names if n.startswith("images/"))
        assert names == {*images, "fields.jsonl", "chat.jsonl", "README.txt"}
        assert images == sorted([*(f"images/{ids['pdf']}-p{n}.png" for n in range(1, pdf_pages + 1)),
                                 f"images/{ids['photo']}-p1.png"])  # fmt: skip
        assert all(zf.read(name).startswith(PNG) for name in images)
        with Image.open(io.BytesIO(zf.read(f"images/{ids['photo']}-p1.png"))) as photo:
            assert photo.size == (1365, 2048)  # turned the right way up, the long side at most 2048
        fields = [json.loads(line) for line in zf.read("fields.jsonl").decode("utf-8").splitlines()]
        chat = [json.loads(line) for line in zf.read("chat.jsonl").decode("utf-8").splitlines()]
        readme = zf.read("README.txt").decode("utf-8")
    assert [f["invoice_id"] for f in fields] == [ids["pdf"], ids["photo"]]
    row = fields[0]
    assert row["images"] == [f"images/{ids['pdf']}-p{n}.png" for n in range(1, pdf_pages + 1)]
    assert row["fields"]["vendor_name"] == ground_truth["vendor_name"] and row["fields"]["grand_total"] == 18017.85
    assert "remit_bank_account" not in row["fields"] and "supplier_province" not in row["fields"]
    assert row["tax_lines"] == [{"tax_type": "HST", "rate": 0.13, "tax_amount": 2072.85}]
    assert row["line_items"][0] == {"description": "Dell Latitude 7450 laptop, 32GB RAM", "quantity": 3,
                                    "unit_price": 1450.0, "amount": 4350.0}  # fmt: skip
    assert row["layout"] == "text" and row["file"] == PDF.name
    assert "rules" in row["readers"]["invoice_number"]["agreed"] and row["readers"]["invoice_number"]["status"]
    assert fields[1]["readers"] == {} and fields[1]["fields"]["invoice_number"] == "NW-2026-0999"
    user, assistant = chat[0]["messages"]
    assert user["role"] == "user" and assistant["role"] == "assistant"
    assert user["content"] == [*({"type": "image", "path": p} for p in row["images"]),
                               {"type": "text", "text": INSTRUCTION}]  # fmt: skip
    assert json.loads(assistant["content"]) == approved_values(ground_truth)
    assert {"vendor_name", "invoice_number", "tax_lines", "line_items"} <= set(json.loads(assistant["content"]))
    readme = " ".join(readme.split())
    for text in README_TEXTS:
        assert text in readme, text
    assert "Bank account numbers" not in readme  # the page images can show them: never promised away


def test_export_since_a_date_and_into_memory(approved):
    store, ids = approved
    buf = io.BytesIO()
    counts = export_training_set(store, buf, since="2026-02-01")
    assert counts["invoices"] == 1  # the photo was approved in January
    with zipfile.ZipFile(buf) as zf:
        assert [json.loads(line)["invoice_id"] for line in zf.read("fields.jsonl").splitlines()] == [ids["pdf"]]
        assert "approved since 2026-02-01" in zf.read("README.txt").decode("utf-8")
    picked = training_invoices(store)
    assert [i["id"] for i in picked["invoices"]] == [ids["pdf"], ids["photo"], ids["gone"], ids["text"]]
    assert (picked["demo"], picked["unreviewed"], picked["bulk"]) == (1, 1, 1)
    with pytest.raises(ValueError):
        training_invoices(store, "last week")


def test_a_bulk_approval_is_left_out_until_a_person_approves_it_again(approved, ground_truth):
    # Bulk approval takes the AI's values without anyone opening the invoice: no checked answers to train on.
    # Reopened and approved by a person from the review screen, its latest approval is a checked one.
    store, ids = approved
    assert ids["bulk"] not in [i["id"] for i in training_invoices(store)["invoices"]]
    store.reopen(ids["bulk"], "Ann", "check the PO")
    store.approve_invoice(ids["bulk"], ground_truth, "Ann")
    picked = training_invoices(store)
    assert ids["bulk"] in [i["id"] for i in picked["invoices"]] and picked["bulk"] == 0


def test_an_empty_export_still_explains_itself(tmp_path):
    out = tmp_path / "t.zip"
    assert export_training_set(Store(tmp_path / "ap.db"), out)["invoices"] == 0
    with zipfile.ZipFile(out) as zf:
        assert zf.read("fields.jsonl") == b"" and b"0 invoice(s)" in zf.read("README.txt")


def test_cli_export_training(tmp_path, ground_truth, capsys):
    db = tmp_path / "data" / "ap_coder.db"
    assert cli.main(["--db", str(db), "export-training"]) == 1  # nothing approved yet
    assert "No approved invoices to export yet" in capsys.readouterr().err
    _approve(Store(db), PDF, ground_truth)
    out = tmp_path / "t.zip"
    assert cli.main(["--db", str(db), "export-training", "--out", str(out)]) == 0
    with zipfile.ZipFile(out) as zf:
        assert len(zf.read("chat.jsonl").splitlines()) == 1
    assert "Wrote 1 invoice(s)" in capsys.readouterr().err
    assert cli.main(["--db", str(db), "export-training", "--since", "last week"]) == 2
    assert cli.main(["--db", str(db), "export-training", "--since", "2099-01-01"]) == 1
    assert cli.main(["--db", str(db), "export-training"]) == 0  # next to the database by default
    assert len(list((db.parent / "training").glob("ap-coder-training-*.zip"))) == 1


# --- The Readers tab of the Learning page -----------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture
def client(store):
    from .webclient import client_for

    return client_for(store)


def _readers(client, **params) -> str:
    """The Readers tab as its words read."""
    page = client.get("/learning", params={"tab": "readers", **params}).text
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def test_readers_tab_before_any_approval(client):
    text = _readers(client)
    for tab in ("Coding accuracy", "Supplier learning", "Readers"):
        assert tab in text
    assert "No reader compared with AP yet" in text and "Nothing approved yet" in text
    assert "Download training data (ZIP)" not in text


def test_training_card_when_every_approval_was_a_bulk_approval(client, store, ground_truth):
    """Bulk approvals are approvals: the card says why they are left out instead of "Nothing approved yet"."""
    _approve(store, PDF, ground_truth, bulk=True)
    _approve(store, PDF, ground_truth | {"invoice_number": "NW-2026-0913"}, bulk=True)
    text = _readers(client)
    assert "Nothing approved yet" not in text
    assert "2 bulk-approved invoices are left out: nobody opened them." in text
    assert "Download training data (ZIP)" not in text
    _approve(store, PDF, ground_truth | {"invoice_number": "NW-2026-0914"})  # one a person approved
    text = _readers(client)
    assert "1 approved invoice" in text and "2 bulk-approved invoices are left out" in text


def test_readers_tab_with_approvals(client, store, ground_truth):
    box = [Box(1, 0.6, 0.1, 0.8, 0.12)]
    capture = CaptureResult({
        "invoice_number": FieldResult("invoice_number", "NW-2026-0912", 0.99, "verified", box,
                                      {"rules": "NW-2026-0912", "ocr2": "NW-2026-0912", "other:NW-2026-0812": "vlm"},
                                      evidence="invoice_number|ocr2+rules|label-right|scan|"),
        "grand_total": FieldResult("grand_total", 18017.85, 1.0, "verified", box,
                                   {"rules": "$18,017.85", "vlm": "18,017.85", "template": "18017.85"},
                                   evidence="grand_total|rules+template+vlm|label-right|scan|adds-up,confirmed"),
    }, layout_source="ocr")  # fmt: skip
    _approve(store, PDF, ground_truth, capture=capture.to_dict())
    other = _approve(store, PDF, ground_truth)  # a scan read twice by the same OCR engine ("ocr")
    store.record_reader_outcomes(other, [
        {"reader": "ocr", "field": "subtotal", "read_value": "15,945.00", "final_value": 15945.0, "correct": True},
        {"reader": "fused", "field": "subtotal", "read_value": 15945.0, "final_value": 15945.0, "correct": True,
         "evidence": "subtotal|ocr+rules|label-right|scan|", "status": "likely"},
    ])  # fmt: skip
    for n in range(MIN_EVIDENCE):  # one pattern seen often enough to set its own confidence
        store.record_reader_outcomes(1000 + n, [{"reader": "fused", "field": "grand_total", "correct": True,
                                                 "evidence": "grand_total|rules|label-right|scan|"}])  # fmt: skip
    page = client.get("/learning", params={"tab": "readers"}).text
    text = _readers(client)
    for label in ("Combined (what AP saw)", "OCR + rules", "OCR (second engine)", "Page reader", "Supplier template"):
        assert label in text, label
    scorecard = text.split("Scorecard", 1)[1].split("Field by field", 1)[0]
    assert scorecard.count("OCR (second engine)") == 1  # the ocr and ocr2 reads are one reader here
    assert "Verified values right" in text and "2 values shown as verified" in text
    assert "1 evidence pattern now has enough approvals to set its own confidence" in text
    options = re.findall(r"<option[^>]*>([^<]+)</option>", page.split('name="reader"', 1)[1].split("</select>", 1)[0])
    assert html.unescape(options[0]) == "Combined (what AP saw)" and "Page reader" in options
    assert "read NW-2026-0812 · AP approved NW-2026-0912" in _readers(client, reader="Page reader")  # its latest
    text = _readers(client, reader="OCR (second engine)")
    assert "Invoice #" in text and "Subtotal" in text  # both kinds of second read, field by field

    assert "2 approved invoices" in text
    response = client.get("/learning/training.zip")
    assert response.status_code == 200 and response.content.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        fields = [json.loads(line) for line in z.read("fields.jsonl").decode().splitlines() if line.strip()]
    assert len({f["invoice_id"] for f in fields}) == 2  # one row per approved invoice
