"""Unusual invoice files (sweep 4, real-world testing): a file named as another kind, a damaged or empty one, an odd
picture. Each is read, or refused in plain words, never with a traceback, and the file is never lost. Every fixture
is made here."""

from __future__ import annotations

import dataclasses as dc
import io

import pymupdf
import pytest
from PIL import Image, ImageDraw

from ap_coder.capture import layout
from ap_coder.core.store import FAILED, Store, load_sample_setup
from ap_coder.intake import pictures as intake
from tests.conftest import SAMPLES

SAMPLE = SAMPLES / "northwind_ON_HST_NW-2026-0912.pdf"


def _words_page(number):
    """What the OCR stub reads on a page: a small invoice."""
    words = []
    lines = ["ACME SUPPLY LTD", "INVOICE", "Invoice No: A-1001", "Date: 2026-10-01", "Subtotal: 100.00",
             "GST 5%: 5.00", "Total: 105.00"]  # fmt: skip
    for i, text in enumerate(lines):
        words += layout._split_ocr_line(text, 100, 100 + 40 * i, 100 + 14 * len(text), 128 + 40 * i, 0.99, 1000, 1300,
                                        number)  # fmt: skip
    return layout.PageLayout(number, 1000, 1300, words, layout.group_lines(words, number))


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """A pipeline reading on this computer with the sample setup, OCR stubbed (a page of a picture reads as a few
    words), and its invoices folder."""
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.core.config import Settings

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "ocr_image", lambda png, number, engine=None: _words_page(number))
    settings = Settings()
    settings = dc.replace(settings, llm=dc.replace(settings.llm, provider="off"))
    folder = tmp_path / "invoices"
    folder.mkdir()
    return store, InvoicePipeline(settings, store.reference_data(), store=store), folder


def _png_bytes(size=(600, 800)) -> bytes:
    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).rectangle([50, 60, 250, 120], fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# --- B-260: a file named as another kind is read as what it is ------------------------------------------------------


def test_a_pdf_named_as_a_photo_is_read_as_the_pdf_it_is(pipeline):
    store, pipe, folder = pipeline
    misnamed = folder / "scan from vendor.jpg"
    misnamed.write_bytes(SAMPLE.read_bytes())
    result = pipe.process(misnamed)
    assert result.ok, result.error
    assert result.output["vendor_name"] == "Northwind IT Solutions Inc."
    assert result.source == folder / "scan from vendor.pdf" and result.source.read_bytes() == SAMPLE.read_bytes()
    assert not misnamed.exists()  # renamed, not copied: the folder does not offer it again
    assert store.get_invoice(result.invoice_id)["file_name"] == "scan from vendor.pdf"


def test_a_picture_named_as_a_pdf_is_made_into_a_pdf_and_kept(pipeline):
    store, pipe, folder = pipeline
    raw = _png_bytes()
    misnamed = folder / "invoice 77.pdf"
    misnamed.write_bytes(raw)
    result = pipe.process(misnamed)
    assert result.ok, result.error
    assert result.source.read_bytes()[:5] == b"%PDF-"  # a real PDF now: stamped and exported as one
    assert result.original["file"] == "invoice 77.png"
    assert (folder / "originals" / "invoice 77.png").read_bytes() == raw  # the picture as it came
    assert store.find_by_hash(folder / "originals" / "invoice 77.png")["id"] == result.invoice_id


def test_a_web_page_saved_as_a_pdf_is_refused_in_plain_words(pipeline):
    store, pipe, folder = pipeline
    page = folder / "invoice.pdf"
    page.write_bytes(b"\n<!DOCTYPE html><html><head><title>Sign in</title></head><body>Session expired</body></html>")
    result = pipe.process(page)
    assert not result.ok
    assert "invoice.pdf is a web page saved with a .pdf name" in result.error
    assert "download the invoice again" in result.error
    assert page.exists() and store.get_invoice(result.invoice_id)["status"] == FAILED


# --- B-261: a multi-page TIFF whose last page has a colour palette --------------------------------------------------


def test_a_multi_page_tiff_with_a_palette_page_is_made_into_a_pdf(tmp_path):
    from PIL import TiffImagePlugin

    scan = tmp_path / "fax and colour page.tif"
    pages = [Image.new("1", (400, 500), 1), Image.new("L", (400, 500), 255), Image.new("RGB", (400, 500), "white")]
    pages.append(pages[2].convert("P"))  # a scanner's 8-bit colour page last
    with TiffImagePlugin.AppendingTiffWriter(scan, new=True) as tf:
        for page, compression in zip(pages, ("group4", "tiff_lzw", "jpeg", "packbits"), strict=True):
            page.save(tf, "TIFF", compression=compression, dpi=(200, 200))
            tf.newFrame()
    converted = intake.to_pdf(scan)
    with pymupdf.open(converted.pdf) as doc:
        assert doc.page_count == 4
    assert (tmp_path / "originals" / scan.name).exists()


# --- B-262: a PDF with no pages -------------------------------------------------------------------------------------


def _pdf_without_pages() -> bytes:
    body, offsets = b"%PDF-1.4\n", []
    for n, obj in enumerate((b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [] /Count 0 >>"), 1):
        offsets.append(len(body))
        body += f"{n} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 3\n0000000000 65535 f \n" + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return body + f"trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def test_a_pdf_with_no_pages_is_refused_in_plain_words_not_queued_blank(pipeline):
    store, pipe, folder = pipeline
    empty = folder / "statement.pdf"
    empty.write_bytes(_pdf_without_pages())
    with pymupdf.open(empty) as doc:
        assert doc.page_count == 0  # a real PDF, as some programs save one, with no page in it
    result = pipe.process(empty)
    assert not result.ok
    assert result.error == ("statement.pdf has no pages in it: the file is empty or was not saved completely. Ask the "
                            "vendor for a new copy.")  # fmt: skip
    assert store.get_invoice(result.invoice_id)["status"] == FAILED and empty.exists()


def test_a_password_protected_pdf_says_so_and_one_with_only_an_owner_password_is_read(pipeline, tmp_path):
    """Not a bug: these already worked; kept so the page check above never words a password as "no pages"."""
    store, pipe, folder = pipeline
    with pymupdf.open(SAMPLE) as doc:
        doc.save(folder / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
        doc.save(folder / "print only.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_128, owner_pw="owner",
                 permissions=pymupdf.PDF_PERM_PRINT)  # fmt: skip
    locked = pipe.process(folder / "locked.pdf")
    assert locked.error == ("locked.pdf is protected by a password, so it can't be read. Ask the vendor for a copy "
                            "without one.")  # fmt: skip
    opened = pipe.process(folder / "print only.pdf")  # an empty user password: opened without asking
    assert opened.ok and opened.output["grand_total"] == 18017.85


# --- B-263: a text file saved as "Unicode" by Notepad, or one that is not text at all --------------------------------

TEXT_INVOICE = (
    "INVOICE\nAcme Supply Ltd\nInvoice No: A-1001\nDate: 2026-10-01\nSubtotal: 100.00\nGST 5%: 5.00\nTotal: 105.00\n"
)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-be"])
def test_a_text_invoice_saved_as_unicode_is_read_as_its_text(pipeline, encoding):
    store, pipe, folder = pipeline
    text = folder / "invoice.txt"
    text.write_bytes(
        TEXT_INVOICE.encode(encoding) if encoding == "utf-16" else b"\xfe\xff" + TEXT_INVOICE.encode(encoding)
    )
    result = pipe.process(text)
    assert result.ok, result.error
    assert result.extraction.content == TEXT_INVOICE
    assert "\x00" not in str(result.output["vendor_name"]) and result.output["grand_total"] == 105.0


def test_a_txt_file_that_is_not_text_is_refused_in_plain_words(pipeline):
    store, pipe, folder = pipeline
    junk = folder / "scan.txt"
    junk.write_bytes(bytes(range(256)) * 8)  # a program's binary file saved with a .txt name
    result = pipe.process(junk)
    assert not result.ok
    assert result.error == ("scan.txt isn't a text file: it holds something else under a .txt name. Ask the vendor for "
                            "the invoice as a PDF.")  # fmt: skip
    assert store.get_invoice(result.invoice_id)["status"] == FAILED and junk.exists()


# --- B-264: WEBP and GIF pictures ------------------------------------------------------------------------------------


def _picture() -> Image.Image:
    img = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(img).rectangle([50, 60, 250, 120], fill="black")
    return img


def test_a_webp_or_gif_picture_in_the_invoices_folder_is_read_as_a_pdf(pipeline):
    from ap_coder.ai.pipeline import invoice_files

    store, pipe, folder = pipeline
    _picture().save(folder / "whatsapp photo.webp", "WEBP", quality=90)
    first, second = _picture(), _picture().rotate(90)
    first.save(folder / "scan.gif", save_all=True, append_images=[second], duration=500)  # animated: two frames
    assert [p.name for p in invoice_files(folder)] == ["scan.gif", "whatsapp photo.webp"]  # no longer passed over
    for name in ("whatsapp photo.webp", "scan.gif"):
        result = pipe.process(folder / name)
        assert result.ok, result.error
        assert result.source.suffix == ".pdf" and (folder / "originals" / name).exists()
        with pymupdf.open(result.source) as doc:
            assert doc.page_count == 1  # an animated picture's first frame: the page it shows


def test_the_upload_takes_a_webp_photo(tmp_path, monkeypatch):
    from tests.webclient import PAGE, client_for

    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "ocr_image", lambda png, number, engine=None: _words_page(number))
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    client = client_for(store)
    buf = io.BytesIO()
    _picture().save(buf, "WEBP")
    response = client.post("/process/upload", files=[("files", ("photo.webp", buf.getvalue(), "image/webp"))],
                           headers=PAGE)  # fmt: skip
    assert response.status_code == 200 and response.json()["ok"], response.text
    job = client.app.state.jobs.get("process")
    assert job.wait(180)
    snap = client.get("/process/status").json()
    assert not any("was not read" in m["text"] for m in snap["result"]["messages"]), snap
    assert snap["result"]["processed"] == 1
    assert ".webp" in client.get("/process").text  # the file picker offers it


# --- B-265: the folder pick-up and a file still being written --------------------------------------------------------


def test_the_folder_pick_up_leaves_a_pdf_still_being_written_for_next_time(tmp_path):
    from ap_coder.web.core import invoice_dir
    from tests.webclient import PAGE, client_for

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    client = client_for(store)
    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    raw = (SAMPLES / "harbourview_NS_HST_HPS-2026-0347.pdf").read_bytes()
    growing = folder / "scanner output.pdf"
    growing.write_bytes(raw[: len(raw) // 2])  # the scanner has written half of it so far
    (folder / "done.pdf").write_bytes(SAMPLE.read_bytes())  # just copied in, complete: read now
    job = client.app.state.jobs.get("process")
    assert client.post("/process/folder", headers=PAGE).json()["ok"]
    assert job.wait(180)
    snap = client.get("/process/status").json()
    assert snap["result"]["processed"] == 1 and [i["file_name"] for i in store.list_invoices()] == ["done.pdf"]
    note = next(m for m in snap["result"]["messages"] if m["tone"] == "warn")
    assert "still being saved" in note["text"] and "picked up next time" in note["text"]
    assert note["details"] == ["scanner output.pdf"]
    growing.write_bytes(raw)  # finished
    assert client.post("/process/folder", headers=PAGE).json()["ok"]
    assert job.wait(180)
    assert sorted(i["file_name"] for i in store.list_invoices()) == ["done.pdf", "scanner output.pdf"]


# --- B-266: a PDF cut off (a download that stopped) ------------------------------------------------------------------


def test_a_pdf_cut_off_is_read_with_a_warning_and_never_approved_without_a_person(pipeline):
    from ap_coder.capture.supplier import touchless_gates

    store, pipe, folder = pipeline
    raw = SAMPLE.read_bytes()
    cut = folder / "download.pdf"
    cut.write_bytes(raw[: int(len(raw) * 0.6)])  # the browser stopped at 60%
    result = pipe.process(cut)
    assert result.ok, result.error  # what is there is still read and shown
    issue = next(i for i in result.report.issues if i.code == "FILE_CUT_OFF")
    assert issue.severity == "warning"
    assert issue.message == ("download.pdf looks cut off (its end is missing: it was still downloading or being saved "
                             "when it came in), so part of the invoice may be missing. Compare it with the vendor's "
                             "copy, or ask the vendor for a new one.")  # fmt: skip
    assert "the file looks cut off" in touchless_gates([{"code": "FILE_CUT_OFF"}])
    whole = pipe.process(SAMPLE)
    assert not any(i.code == "FILE_CUT_OFF" for i in whole.report.issues)


# --- B-267: a file longer than the pages read ------------------------------------------------------------------------


def test_a_300_page_pdf_says_on_the_review_screen_that_only_its_first_pages_were_read(pipeline):
    store, pipe, folder = pipeline
    long = folder / "statement and invoice.pdf"
    with pymupdf.open() as doc, pymupdf.open(SAMPLE) as sample:
        for _ in range(150):
            doc.insert_pdf(sample)  # 300 pages
        doc.save(long)
    result = pipe.process(long)
    assert result.ok, result.error
    issue = next(i for i in result.report.issues if i.code == "PAGES_NOT_READ")
    assert issue.severity == "warning"
    assert issue.message == ("only 20 of its 300 pages were read (a file's first 20 pages are read, and a page OCR "
                             "can't read is left out): check the other pages yourself before approving")  # fmt: skip
    assert not any(i.code == "PAGES_NOT_READ" for i in pipe.process(SAMPLE).report.issues)


# --- B-268: a file in the invoices folder of a kind AP Coder does not read ------------------------------------------


def test_the_invoices_folder_says_which_files_it_does_not_read(tmp_path):
    from ap_coder.web.core import invoice_dir
    from tests.webclient import PAGE, client_for

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    client = client_for(store)
    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "invoice.pdf").write_bytes(SAMPLE.read_bytes())
    (folder / "scan.jp2").write_bytes(b"\x00\x00\x00\x0cjP  \r\n\x87\n")
    (folder / "layout.psd").write_bytes(b"8BPS")
    (folder / ".DS_Store").write_bytes(b"\x00")  # the computer's own: never mentioned
    page = client.get("/process").text
    assert "Not read from this folder: layout.psd, scan.jp2." in page
    assert "Save them as PDF (or JPEG) to have them read." in page
    job = client.app.state.jobs.get("process")
    assert client.post("/process/folder", headers=PAGE).json()["ok"]
    assert job.wait(180)
    note = next(m for m in client.get("/process/status").json()["result"]["messages"] if "kind" in m["text"])
    assert note["text"] == ("2 files in the invoices folder are not a kind AP Coder reads (PDFs, scans, photos and "
                            "saved emails): save them as PDF (or JPEG) to have them read.")  # fmt: skip
    assert note["details"] == ["layout.psd", "scan.jp2"] and note["tone"] == "warn"


def test_process_on_a_folder_names_the_files_it_leaves_out(tmp_path, capsys):
    from ap_coder.ops import cli

    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "scan.jp2").write_bytes(b"\x00\x00\x00\x0cjP  \r\n\x87\n")
    code = cli.main(["--env-file", str(tmp_path / "none.env"), "--db", str(tmp_path / "a.db"), "process", str(folder),
                     "--out", str(tmp_path / "out")])  # fmt: skip
    err = capsys.readouterr().err
    assert code == 2
    assert "Left out, not a kind AP Coder reads (save as PDF or JPEG): scan.jp2" in err


def test_an_upload_of_a_picture_kind_not_read_says_which_kinds_are(tmp_path):
    from tests.webclient import PAGE, client_for

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    client = client_for(store)
    files = [("files", ("photo.avif", b"\x00\x00\x00\x1cftypavif", "image/avif"))]
    job = client.app.state.jobs.get("process")
    assert client.post("/process/upload", files=files, headers=PAGE).json()["ok"]
    assert job.wait(60)
    texts = [m["text"] for m in client.get("/process/status").json()["result"]["messages"]]
    assert ("photo.avif was not read: AP Coder reads PDFs, pictures (JPEG, PNG, HEIC, TIFF, BMP, WEBP, GIF), saved "
            "emails (.eml, .msg) and invoices saved as text (.txt). Save it as PDF or JPEG to have it "
            "read.") in texts  # fmt: skip
