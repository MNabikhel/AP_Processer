"""Every invoice is a PDF: a photo or a scanned picture is made into one when it comes in, the picture kept beside it
for the audit trail, OCR still reading the picture's own pixels, and the same photo sent twice found as one invoice."""

import dataclasses
import hashlib
import io

import pymupdf
import pytest
from PIL import Image, ImageDraw

from ap_coder.capture import layout
from ap_coder.capture.layout import _image_pngs, _scan_png
from ap_coder.core.store import Store
from ap_coder.intake import pictures as intake


def _picture(size=(600, 800), color="white", mode="RGB") -> Image.Image:
    img = Image.new(mode, size, color)
    ImageDraw.Draw(img).rectangle([50, 60, 250, 120], fill="black" if mode != "1" else 0)  # a mark at the top left
    return img


def _jpeg(path, orientation=None, dpi=(300, 300), size=(600, 800)):
    img = _picture(size)
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    img.save(path, "JPEG", quality=90, dpi=dpi, exif=exif.tobytes())
    return path


def _pixels(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def test_a_jpeg_goes_into_the_pdf_as_it_is(tmp_path):
    photo = _jpeg(tmp_path / "scan.jpg")
    raw = photo.read_bytes()
    converted = intake.to_pdf(photo)
    assert converted.pdf == tmp_path / "scan.pdf" and converted.pages == 1
    assert converted.original == tmp_path / "originals" / "scan.jpg" and converted.original.read_bytes() == raw
    assert not photo.exists()  # moved, so the invoices folder doesn't offer it again
    assert converted.sha256 == hashlib.sha256(raw).hexdigest()
    with pymupdf.open(converted.pdf) as doc:
        page = doc[0]
        ((xref, *_rest),) = page.get_images(full=True)
        assert doc.extract_image(xref)["image"] == raw  # not recompressed
        assert page.rect.width == pytest.approx(600 * 72 / 300) and page.rect.height == pytest.approx(800 * 72 / 300)
        assert intake.made_from_picture(doc)
    assert intake.original_of(converted.pdf) == {"original": "scan.jpg", "sha256": converted.sha256,
                                                 "kept": "originals/scan.jpg"}  # fmt: skip


@pytest.mark.parametrize("orientation, turn", [(6, 90), (3, 180), (8, 270)])
def test_a_photo_taken_sideways_is_turned_upright_by_the_page(tmp_path, orientation, turn):
    photo = _jpeg(tmp_path / "IMG_1.jpg", orientation=orientation, dpi=(72, 72), size=(800, 600))
    converted = intake.to_pdf(photo)
    with pymupdf.open(converted.pdf) as doc:
        page = doc[0]
        assert page.rotation == turn
        upright = intake.page_picture(doc, page)
        expected = Image.open(converted.original)
        from PIL import ImageOps

        expected = ImageOps.exif_transpose(expected)
        assert upright.size == expected.size  # as the phone shows it
        assert (page.rect.width > page.rect.height) == (expected.width > expected.height)
        # The PDF page is what OCR reads: the same picture as the photo turned upright (JPEG decoding aside).
        got = _pixels(_scan_png(doc, page))
        want = _pixels(_image_pngs(converted.original, 1)[0])
        assert got.size == want.size
        assert sum(abs(a - b) for a, b in zip(got.convert("L").tobytes(), want.convert("L").tobytes(),
                                                strict=True)) / (got.width * got.height) < 1.0  # fmt: skip


def test_a_mirrored_photo_is_made_upright_and_lossless(tmp_path):
    photo = _jpeg(tmp_path / "mirror.jpg", orientation=2)
    converted = intake.to_pdf(photo)
    with pymupdf.open(converted.pdf) as doc:
        page = doc[0]
        assert page.rotation == 0
        ((xref, *_rest),) = page.get_images(full=True)
        assert doc.extract_image(xref)["ext"] == "png"


def test_a_multi_page_tiff_is_one_pdf_page_a_frame(tmp_path):
    tiff = tmp_path / "statement.tif"
    frames = [_picture((850, 1100), mode="1"), _picture((850, 1100), mode="L"), _picture((1100, 850))]
    frames[0].save(tiff, save_all=True, append_images=frames[1:], dpi=(100, 100), compression="tiff_lzw")
    converted = intake.to_pdf(tiff)
    assert converted.pages == 3
    with pymupdf.open(converted.pdf) as doc:
        assert doc.page_count == 3
        assert doc[0].rect.width == pytest.approx(850 * 72 / 100)
        assert doc[2].rect.width > doc[2].rect.height  # the landscape page stays landscape
        for page, frame in zip(doc, frames, strict=True):
            assert intake.page_picture(doc, page).size == frame.size  # the resolution it was scanned at


def test_png_with_transparency_and_bmp_become_pages_on_white_paper(tmp_path):
    png = tmp_path / "logo-invoice.png"
    img = Image.new("RGBA", (400, 500), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([10, 10, 100, 40], fill=(0, 0, 0, 255))
    img.save(png)
    bmp = tmp_path / "old.bmp"
    _picture((300, 400)).save(bmp, dpi=(96, 96))
    for path, long_side in ((png, intake.PAGE_LONG_SIDE), (bmp, 400 * 72 / 96)):
        converted = intake.to_pdf(path)
        with pymupdf.open(converted.pdf) as doc:
            picture = intake.page_picture(doc, doc[0]).convert("RGB")
            assert picture.getpixel((250, 350)) == (255, 255, 255)  # white paper, not black
            # No resolution recorded (the PNG): a letter-sized page; else the size it was scanned at.
            assert max(doc[0].rect.width, doc[0].rect.height) == pytest.approx(long_side, rel=1e-3)


def test_an_iphone_photo_becomes_a_pdf_when_the_photo_add_on_is_installed(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    if not layout.register_heif():
        pytest.skip("pillow-heif can't be loaded here")
    heic = tmp_path / "IMG_0042.HEIC"
    try:
        pillow_heif.from_pillow(_picture((640, 480))).save(heic, quality=90)
    except Exception as exc:  # a pillow-heif build without an encoder
        pytest.skip(f"pillow-heif can't write HEIC here ({exc})")
    converted = intake.to_pdf(heic)
    with pymupdf.open(converted.pdf) as doc:
        assert doc.page_count == 1 and intake.page_picture(doc, doc[0]).size == (640, 480)
    assert converted.original.suffix == ".HEIC" and converted.original.exists()


def test_an_iphone_photo_without_the_photo_add_on_is_left_where_it_was(tmp_path, monkeypatch):
    heic = tmp_path / "IMG_0001.HEIC"
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic")
    monkeypatch.setattr(layout, "_heif_registered", False)
    with pytest.raises(layout.CannotRead, match="pillow-heif"):
        intake.to_pdf(heic)
    assert heic.exists() and not (tmp_path / "IMG_0001.pdf").exists() and not (tmp_path / "originals").exists()


def test_names_already_taken_and_a_picture_kept_once(tmp_path):
    (tmp_path / "scan.pdf").write_bytes(b"%PDF-1.4 another invoice")
    first = intake.to_pdf(_jpeg(tmp_path / "scan.jpg"))
    assert first.pdf.name == "scan_1.pdf" and (tmp_path / "scan.pdf").read_bytes() == b"%PDF-1.4 another invoice"
    again = intake.to_pdf(_jpeg(tmp_path / "scan.jpg"))  # the same picture once more
    assert again.original == first.original and len(list((tmp_path / "originals").iterdir())) == 1
    other = intake.to_pdf(_jpeg(tmp_path / "scan.jpg", dpi=(200, 200)))  # same name, another picture
    assert other.original.name == "scan_1.jpg"


def test_only_pictures_are_converted_and_other_pdfs_have_no_original(tmp_path):
    assert intake.is_image("a.JPEG") and intake.is_image("b.heic") and intake.is_image("c.tiff")
    assert not intake.is_image("d.pdf") and not intake.is_image("e.md")
    plain = tmp_path / "digital.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Invoice 1")
        doc.save(plain)
    assert intake.original_of(plain) is None and intake.original_of(tmp_path / "missing.pdf") is None


def test_ocr_reads_a_converted_photo_as_it_read_the_photo(tmp_path):
    """The capture of a converted picture: a page with no text, read by OCR from the picture's own pixels."""
    if not layout.ocr_available():
        pytest.skip("OCR isn't installed here")
    scan = tmp_path / "scan.png"
    img = Image.new("RGB", (1275, 1650), "white")
    draw = ImageDraw.Draw(img)
    for i, text in enumerate(["INVOICE", "Invoice No: INV-20931", "Date: 2026-10-01", "Total Due: 1,234.56"]):
        draw.text((100, 100 + 60 * i), text, fill="black", font_size=36)
    img.save(scan, dpi=(150, 150))
    before = layout.build_layout(scan)
    converted = intake.to_pdf(scan)
    after = layout.build_layout(converted.pdf)
    assert after.source == "ocr" and after.text() == before.text() and "INV-20931" in after.text()


# --- the pipeline and the store -------------------------------------------------------------------------------------


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
def offline_pipeline(tmp_path, monkeypatch):
    """A pipeline reading on this computer, OCR stubbed: a page of a picture reads as a few words."""
    import dataclasses as dc

    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.core.config import Settings
    from ap_coder.core.store import load_sample_setup

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "ocr_image", lambda png, number, engine=None: _words_page(number))
    settings = Settings()
    settings = dc.replace(settings, llm=dc.replace(settings.llm, provider="off"))
    return store, InvoicePipeline(settings, store.reference_data(), store=store)


def test_a_photo_is_stored_and_read_as_its_pdf_and_found_when_sent_again(tmp_path, offline_pipeline):
    store, pipeline = offline_pipeline
    inbox = tmp_path / "invoices"
    inbox.mkdir()
    photo = _jpeg(inbox / "IMG_2001.jpg")
    copy = tmp_path / "same photo again.jpg"
    copy.write_bytes(photo.read_bytes())
    result = pipeline.process(photo)
    assert result.source == inbox / "IMG_2001.pdf" and result.source.exists()
    assert result.original["file"] == "IMG_2001.jpg" and (inbox / "originals" / "IMG_2001.jpg").exists()
    row = store.get_invoice(result.invoice_id)
    assert row["source_path"].endswith("IMG_2001.pdf") and row["file_name"] == "IMG_2001.pdf"
    assert row["meta"]["original"]["sha256"] == hashlib.sha256(copy.read_bytes()).hexdigest()
    # The same photo uploaded or dropped in the folder again is the invoice already there, and so is its PDF.
    assert store.find_by_hash(copy, include_failed=True)["id"] == result.invoice_id
    assert store.find_by_hash(result.source)["id"] == result.invoice_id
    from ap_coder.ops.cli import new_files

    assert new_files(inbox, store, now=10**12) == []  # nothing in the folder is new


def test_a_pdf_made_from_a_photo_before_still_knows_its_photo(tmp_path, offline_pipeline):
    store, pipeline = offline_pipeline
    converted = intake.to_pdf(_jpeg(tmp_path / "scan.jpg"))
    result = pipeline.process(converted.pdf)  # e.g. read again after the first attempt stopped half-way
    assert result.original["sha256"] == converted.sha256
    assert store.find_by_hash(converted.original)["id"] == result.invoice_id


def test_a_photo_stored_by_an_older_version_is_read_again_where_it_is(tmp_path, offline_pipeline):
    """The page reader reads an invoice stored as a picture (before pictures became PDFs) again: the picture stays
    where the database has it."""
    store, pipeline = offline_pipeline
    photo = _jpeg(tmp_path / "old.jpg")
    result = pipeline.process(photo, page_text=["INVOICE"], save=False, invoice_id=7)
    assert result.source == photo and photo.exists() and not (tmp_path / "old.pdf").exists()


def test_a_failed_photo_is_replaced_when_it_is_read_after_all(tmp_path, offline_pipeline, monkeypatch):
    store, pipeline = offline_pipeline
    photo = _jpeg(tmp_path / "scan.jpg")
    keep = photo.read_bytes()
    monkeypatch.setattr(intake, "to_pdf", lambda path: (_ for _ in ()).throw(OSError("disk full")))
    failed = pipeline.process(photo)
    assert not failed.ok and store.find_by_hash(photo, include_failed=True)["status"] == "failed"
    monkeypatch.undo()
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "ocr_image", lambda png, number, engine=None: _words_page(number))
    photo.write_bytes(keep)
    done = pipeline.process(photo)
    assert done.ok
    rows = [r for r in store.list_invoices() if r["file_name"] in ("scan.jpg", "scan.pdf")]
    assert [r["file_name"] for r in rows] == ["scan.pdf"]  # the failed attempt is gone


def test_an_old_database_gets_the_column_for_the_photo(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    Store(path)
    with sqlite3.connect(path) as conn:  # as an older version made it: no original_sha256
        conn.execute("DROP INDEX invoices_original_sha")
        conn.execute("ALTER TABLE invoices DROP COLUMN original_sha256")
    Store(path)
    with sqlite3.connect(path) as conn:
        assert "original_sha256" in {r[1] for r in conn.execute("PRAGMA table_info(invoices)")}


def test_converted_is_a_plain_record():
    assert {f.name for f in dataclasses.fields(intake.Converted)} == {"pdf", "original", "sha256", "pages"}


@pytest.mark.parametrize("mode", ["RGB", "L", "1", "P"])
def test_a_lossless_picture_is_read_from_its_pixels_exactly_as_extracting_it_would(tmp_path, mode):
    # The fast path (no PNG made and read back, about a second a page) gives OCR the very same image.
    src = tmp_path / f"scan-{mode}.png"
    _picture((500, 700), mode=mode).save(src)
    pdf = tmp_path / "scan.pdf"
    intake.image_to_pdf(src, pdf)
    with pymupdf.open(pdf) as doc:
        xref = doc[0].get_images(full=True)[0][0]
        extracted = Image.open(io.BytesIO(doc.extract_image(xref)["image"]))
        assert intake._stored_pixels(doc, xref) is not None
        assert _scan_png(doc, doc[0]) == layout._picture_png(extracted)


def test_a_jpeg_is_extracted_as_it_is_stored(tmp_path):
    pdf = tmp_path / "photo.pdf"
    intake.image_to_pdf(_jpeg(tmp_path / "photo.jpg"), pdf)
    with pymupdf.open(pdf) as doc:
        assert intake._stored_pixels(doc, doc[0].get_images(full=True)[0][0]) is None
        assert intake.page_picture(doc, doc[0]).size == (600, 800)


# --- a phone photo of a page on a desk --------------------------------------------------------------------------------


def _desk_photo(path, desk=(70, 60, 55), tilt=5.0):
    """A white page (600 x 800, a dark mark at its top left) photographed on a desk: tilted, the top edge narrower
    than the bottom (taken at an angle)."""
    import numpy as np

    page = _picture((600, 800))
    canvas = Image.new("RGB", (900, 1100), desk)
    canvas.paste(page, (150, 150))
    src = [(150, 150), (750, 150), (750, 950), (150, 950)]
    dst = [(180, 165), (720, 135), (750, 950), (150, 950)]  # where each corner goes
    rows = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        rows += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
    coeffs = np.linalg.solve(np.array(rows, float), np.array([c for p in src for c in p], float)).tolist()
    photo = canvas.transform(canvas.size, Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=desk)
    photo.rotate(tilt, resample=Image.BICUBIC, fillcolor=desk).save(path, "JPEG", quality=90)
    return path


def test_a_photo_of_a_page_on_a_desk_is_flattened_and_a_scan_is_not():
    pytest.importorskip("cv2")
    flat = layout.flatten_photo(Image.open(_desk_photo(io.BytesIO())))
    assert flat is not None and abs(flat.width / flat.height - 600 / 800) < 0.04  # the page's own shape
    corners = [
        flat.getpixel(p) for p in ((3, 3), (flat.width - 4, 3), (3, flat.height - 4), (flat.width - 4, flat.height - 4))
    ]
    assert all(min(c) > 200 for c in corners)  # paper to its corners: no desk left
    mark = flat.getpixel((round(150 / 600 * flat.width), round(90 / 800 * flat.height)))
    assert max(mark) < 90  # the page's mark is where the page has it (upright, not turned or mirrored)
    assert layout.flatten_photo(_picture((600, 800))) is None  # a scan fills its picture
    # Nor is a scan with a dark band across its top (a header printed white on navy): its white part below the band is
    # not a page lying on a desk (sweep: its header was cut off for good).
    banded = Image.new("RGB", (1700, 2200), "white")
    ImageDraw.Draw(banded).rectangle([0, 0, 1700, 130], fill=(20, 30, 80))
    ImageDraw.Draw(banded).rectangle([100, 300, 900, 360], fill="black")
    assert layout.flatten_photo(banded) is None
    assert layout.flatten_photo(Image.open(_desk_photo(io.BytesIO(), desk=(235, 235, 235)))) is None  # a white desk


def test_a_scan_on_a_dark_bed_keeps_the_resolution_it_was_scanned_at(tmp_path):
    pytest.importorskip("cv2")
    scan = tmp_path / "a5.png"
    Image.open(_desk_photo(io.BytesIO(), tilt=0.0)).save(scan, dpi=(150, 150))  # the page on a scanner's dark bed
    with pymupdf.open(intake.to_pdf(scan).pdf) as doc:
        assert layout._native_dpi(doc[0]) == pytest.approx(150, abs=1)  # not taken for a coarse scan


def test_a_desk_photo_becomes_a_pdf_of_the_flattened_page_and_the_photo_is_kept_as_taken(tmp_path):
    pytest.importorskip("cv2")
    photo = _desk_photo(tmp_path / "IMG_3001.jpg")
    raw = photo.read_bytes()
    converted = intake.to_pdf(photo)
    assert converted.original.read_bytes() == raw  # the photo as taken, for the audit trail
    with pymupdf.open(converted.pdf) as doc:
        page = doc[0]
        picture = intake.page_picture(doc, page).convert("RGB")
        assert page.rect.height > page.rect.width and min(picture.getpixel((3, 3))) > 200  # the page, no desk
        # OCR reads the page as it reads the photo itself (both flattened).
        got = _pixels(_scan_png(doc, page))
        want = _pixels(_image_pngs(converted.original, 1)[0])
        assert got.size == want.size


def test_a_desk_photo_read_where_it_is_is_shown_as_it_is_read(tmp_path):
    """Sweep: a photo stored as a picture by an older version is read flattened but was shown as taken, so the
    highlights fell beside the words."""
    pytest.importorskip("cv2")
    from ap_coder.web import document

    photo = _desk_photo(tmp_path / "old.jpg")
    read = _pixels(_image_pngs(photo, 1)[0])
    ((w_pt, h_pt, png),) = document._image_pages(str(photo))
    shown = _pixels(png)
    assert abs(w_pt / h_pt - read.width / read.height) < 0.01
    assert abs(shown.width / shown.height - read.width / read.height) < 0.01
