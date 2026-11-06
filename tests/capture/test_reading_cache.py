"""A scan's readings kept by what was asked (D-170): a page read again is neither drawn nor OCR'd, in any process
sharing the data folder; OCR reads the very pixels the PNG it was given before held, without the PNG; PyMuPDF one
thread at a time."""

import hashlib
import inspect
import io
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageSequence

from ap_coder.capture import layout, table_finder
from ap_coder.capture.types import Box
from ap_coder.intake import pictures as intake
from ap_coder.ops import offline

ROWS = ["Northwind Supplies Ltd.", "INVOICE NW-2026-0912", "Date 2026-10-01", "Bill to Lakeshore Plumbing",
        "Pallet wrap 18 in. 1500 ft", "Subtotal 1,000.00", "HST 13% 130.00", "Total due 1,130.00"]  # fmt: skip


def _lines(img) -> list:
    """What a fake OCR engine reads: eight long lines, the last one's text from the pixels' sum (so pixels that differ
    read differently)."""
    out = [[[[40, 40 + 30 * i], [400, 40 + 30 * i], [400, 60 + 30 * i], [40, 60 + 30 * i]], text, 0.9]
           for i, text in enumerate(ROWS)]  # fmt: skip
    out[-1][1] = f"Total due {int(np.asarray(img, dtype=np.int64).sum())}"
    return out


@pytest.fixture
def engine(monkeypatch):
    """A fake OCR engine: each run recorded with the engine's name."""
    runs: list[str] = []

    def make(name=None):
        def run(img):
            runs.append(name or layout._engine_name())
            return _lines(img)

        return run

    monkeypatch.setattr(layout, "_engine", make)
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.delenv("AP_OCR_CACHE", raising=False)
    return runs


def _picture(size=(850, 1100), mode="RGB", text="INVOICE 1001") -> Image.Image:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for i, row in enumerate([text, *ROWS]):
        draw.text((60, 60 + 40 * i), row, fill="black")
    return img.convert(mode)


def _scan(path, size=(612, 792), text="INVOICE 1001", pages=1):
    """A scanner's PDF: each page one JPEG picture, nothing else."""
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        buf = io.BytesIO()
        _picture((round(size[0] * 150 / 72), round(size[1] * 150 / 72)), text=f"{text} {n}").save(buf, "JPEG")
        page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)
    return path


def _same(a, b) -> None:
    assert a.source == b.source and len(a.pages) == len(b.pages)
    for x, y in zip(a.pages, b.pages, strict=True):
        assert (x.width, x.height, x.dpi, x.turn, x.skew) == (y.width, y.height, y.dpi, y.turn, y.skew)
        assert [(w.text, w.box, w.conf) for w in x.words] == [(w.text, w.box, w.conf) for w in y.words]


# --- OCR reads the pixels its PNG held ---------------------------------------------------------------------------


def _old_scan_png(doc, page) -> bytes:
    """How the picture OCR reads for a PDF page was made before D-170 (``_scan_png``): a PNG."""
    if intake.made_from_picture(doc):
        picture = intake.page_picture(doc, page)
        if picture is not None:
            return _old_picture_png(picture)
    zoom = layout.OCR_DPI / 72
    longest = max(page.rect.width, page.rect.height) * zoom
    if longest > layout.OCR_MAX_SIDE:
        zoom *= layout.OCR_MAX_SIDE / longest
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


def _old_picture_png(picture) -> bytes:
    pic = layout._on_paper(picture)
    scale = layout.OCR_MAX_SIDE / max(pic.width, pic.height, 1)
    if scale < 1:
        pic = pic.resize((max(1, round(pic.width * scale)), max(1, round(pic.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    pic.save(buf, "PNG")
    return buf.getvalue()


def _old_prepared(png: bytes) -> bytes:
    img = Image.open(io.BytesIO(png)).convert("L")
    angle = layout._deskew_angle(img)
    if abs(angle) >= 0.2:
        img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=255, expand=False)
    img = img.resize((int(img.width * 1.5), int(img.height * 1.5)), Image.LANCZOS).filter(ImageFilter.SHARPEN)
    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG")
    return out.getvalue()


def _as_ocr_read_it(png: bytes):
    """(the pixels OCR was given, the resolution the page was said to be at) from a PNG, as ``ocr_image`` read one."""
    picture = Image.open(io.BytesIO(png))
    return np.asarray(layout._on_paper(picture)), layout._stated_dpi(picture)


def _as_ocr_reads_it(drawn):
    picture, stated = drawn
    return np.asarray(layout._on_paper(picture)), stated


def _check(old_png: bytes, drawn) -> None:
    (want, want_dpi), (got, got_dpi) = _as_ocr_read_it(old_png), _as_ocr_reads_it(drawn)
    assert got.shape == want.shape and np.array_equal(got, want) and got_dpi == want_dpi
    prepared = layout._prepared(drawn[0])  # the second read of a computer with one OCR engine
    assert np.array_equal(np.asarray(layout._on_paper(prepared)), _as_ocr_read_it(_old_prepared(old_png))[0])


@pytest.mark.parametrize("size", [(612, 792), (288, 432), (1224, 792)])  # letter, a 4 x 6 in. slip (96 dpi), tabloid
def test_a_scanned_pdf_page_is_read_from_the_pixels_its_png_held(tmp_path, size):
    path = _scan(tmp_path / "scan.pdf", size=size)
    with pymupdf.open(path) as doc:
        page = doc[0]
        _check(_old_scan_png(doc, page), layout._scan_picture(doc, page))
        if size == (288, 432):
            assert layout._scan_picture(doc, page)[1] == 96.0  # a page this small states its PNG's 96 dpi, as before


@pytest.mark.parametrize("kind", ["png", "jpeg", "grey", "palette"])
def test_a_pdf_made_from_a_picture_is_read_from_the_pixels_its_png_held(tmp_path, kind):
    mode = {"grey": "L", "palette": "P"}.get(kind, "RGB")
    source = tmp_path / ("photo.jpg" if kind == "jpeg" else "scan.png")
    _picture((3000, 3900) if kind == "png" else (900, 1200), mode=mode).save(source)  # (3000 x 3900: made smaller)
    pdf = tmp_path / "made.pdf"
    intake.image_to_pdf(source, pdf)
    with pymupdf.open(pdf) as doc:
        assert intake.made_from_picture(doc)
        _check(_old_scan_png(doc, doc[0]), layout._scan_picture(doc, doc[0]))


def test_a_picture_file_is_read_from_the_pixels_its_png_held(tmp_path):
    photo = tmp_path / "IMG_1.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # taken sideways
    _picture((1200, 900)).save(photo, "JPEG", quality=90, dpi=(72, 72), exif=exif.tobytes())
    tiff = tmp_path / "fax.tif"
    frames = [_picture((850, 1100), text=f"PAGE {n}") for n in range(3)]
    frames[0].save(tiff, save_all=True, append_images=frames[1:], dpi=(200, 200))
    for path in (photo, tiff):
        pages = layout._image_pages(path, 20)
        with Image.open(path) as img:
            old = []
            for frame in ImageSequence.Iterator(img):  # (each frame copied as it is reached)
                upright = ImageOps.exif_transpose(frame.copy())
                old.append(_old_picture_png(layout.flatten_photo(upright) or upright))
        assert len(pages) == len(old) == (1 if path == photo else 3)
        for page, png in zip(pages, old, strict=True):
            _check(png, page.picture())


@pytest.mark.parametrize("mode", ["RGBA", "P", "L"])
def test_a_png_file_is_read_as_it_is_and_prepared_from_it_as_it_is(tmp_path, mode):
    png = io.BytesIO()
    picture = _picture((900, 1200)).convert(mode)
    if mode == "RGBA":
        picture.putalpha(Image.new("L", picture.size, 0).point(lambda _: 0))  # see-through: white paper for OCR
    picture.save(png, "PNG")
    page = layout.png_page(png.getvalue())
    want, want_dpi = _as_ocr_read_it(png.getvalue())
    got, got_dpi = _as_ocr_reads_it(page.picture())
    assert np.array_equal(got, want) and got_dpi == want_dpi
    old = _as_ocr_read_it(_old_prepared(png.getvalue()))[0]  # prepared from the file's own mode, as before
    assert np.array_equal(np.asarray(layout._on_paper(layout._prepared(page.picture()[0]))), old)


def test_the_table_finder_looks_at_the_pixels_its_png_held(tmp_path):
    path = _scan(tmp_path / "scan.pdf", pages=2)
    pictures = table_finder.page_pictures(path)
    with pymupdf.open(path) as doc:
        for page, picture in zip(doc, pictures, strict=True):
            zoom = table_finder.PICTURE_SIDE / max(page.rect.width, page.rect.height, 1)
            png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
            want = Image.open(io.BytesIO(png)).convert("RGB").resize((table_finder.SIDE,) * 2, Image.BILINEAR)
            got = picture.convert("RGB").resize((table_finder.SIDE,) * 2, Image.BILINEAR)
            assert np.array_equal(np.asarray(got), np.asarray(want))


# --- A page read again is neither drawn nor OCR'd -----------------------------------------------------------------


def _read(path):
    return layout.build_layout(path), layout.second_read_layout(path)


def _forget_this_process() -> None:
    """As in another process: nothing in memory."""
    layout._PICTURES.clear()
    table_finder._found.clear()


def test_a_scan_read_again_in_another_process_is_neither_drawn_nor_ocrd(engine, tmp_path, monkeypatch):
    path = _scan(tmp_path / "scan.pdf", size=(288, 360), pages=2)
    looked: list[int] = []
    monkeypatch.setattr(table_finder, "available", lambda: True)
    monkeypatch.setattr(table_finder, "find", lambda picture, n: looked.append(n) or [Box(n, 0, 0, 1, 1)])
    with layout.ocr_cache(tmp_path / "ocr"):
        first, second = _read(path)
        tables = table_finder.find_tables(path)
    assert len(engine) == 4 and looked == [1, 2] and tables == {1: [Box(1, 0, 0, 1, 1)], 2: [Box(2, 0, 0, 1, 1)]}

    def never(*args, **kwargs):
        raise AssertionError("drawn again")

    _forget_this_process()
    monkeypatch.setattr(layout, "_scan_picture", never)
    monkeypatch.setattr(table_finder, "page_pictures", never)
    with layout.ocr_cache(tmp_path / "ocr"):
        again, second_again = _read(path)
        assert table_finder.find_tables(path) == tables
    assert len(engine) == 4 and looked == [1, 2]
    _same(again, first)
    _same(second_again, second)
    assert not list((tmp_path / "ocr").glob("*.part"))


def test_the_letters_that_turn_a_page_are_kept_with_its_reading(engine, tmp_path, monkeypatch):
    path = _scan(tmp_path / "scan.pdf", size=(288, 360))
    told: list[int] = []
    real = layout._letters_upright
    monkeypatch.setattr(layout, "_letters_upright", lambda picture, result: told.append(1) or real(picture, result))
    with layout.ocr_cache(tmp_path / "ocr"):
        first = layout.build_layout(path)
        _forget_this_process()
        again = layout.build_layout(path)
    assert told == [1]  # eight lines across the page: their letters say which way up it is, worked out once
    _same(again, first)


def test_whatever_changes_a_reading_changes_what_it_is_kept_under(engine, tmp_path, monkeypatch):
    path = _scan(tmp_path / "scan.pdf", size=(288, 360))
    with layout.ocr_cache(tmp_path / "ocr"):
        layout.build_layout(path)
        layout.build_layout(path)
        assert len(engine) == 1
        changes = [
            ("PAGE_DRAWING", layout.PAGE_DRAWING + 1),  # how a page is drawn
            ("_libraries", lambda: "pymupdf 9.9.9"),  # a library's version
            ("_engine_signature", lambda name: f"{name} other models"),  # the engine's settings or model files
        ]
        for attribute, value in changes:
            with monkeypatch.context() as m:
                m.setattr(layout, attribute, value)
                _forget_this_process()
                layout.build_layout(path)
        assert len(engine) == 4
        _scan(path, text="INVOICE 1002")  # the file itself
        layout.build_layout(path)
        assert len(engine) == 5
    asked = {layout._pdf_page(layout.file_sha256(path), None, None, i).asked for i in (0, 1)}
    assert len(asked) == 2  # the page


def test_outside_a_readings_folder_nothing_is_kept(engine, tmp_path):
    path = _scan(tmp_path / "scan.pdf", size=(288, 360))
    layout.build_layout(path)
    _forget_this_process()
    layout.build_layout(path)
    assert len(engine) == 2


def test_with_one_engine_each_page_is_straightened_once_for_its_second_read(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "second_engine_name", layout._engine_name)  # (a computer with one OCR engine)
    made: list[int] = []
    real = layout._prepared
    monkeypatch.setattr(layout, "_prepared", lambda picture: made.append(1) or real(picture))
    path = _scan(tmp_path / "scan.pdf", size=(288, 360), pages=2)
    with layout.ocr_cache(tmp_path / "ocr"):
        layout.build_layout(path)
        layout.second_read_layout(path)
        _forget_this_process()
        layout.second_read_layout(path)
    assert len(made) == 2 and len(engine) == 4  # a second read again: neither straightened nor OCR'd


def test_two_writers_at_once_leave_one_whole_reading(tmp_path, monkeypatch):
    data = {"lines": [[[[0.5, 1.5]] * 4, "x" * 200, 0.9]] * 400, "size": [1, 1], "dpi": 0}
    monkeypatch.setenv("AP_OCR_CACHE", str(tmp_path))  # (a folder every thread sees)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: layout.keep("same", data), range(64)))
    assert layout.read_kept("same") == data
    assert [p.name for p in tmp_path.iterdir()] == ["same.json"]  # each wrote under its own name, then renamed it


def test_a_damaged_or_foreign_reading_is_read_again(engine, tmp_path):
    path = _scan(tmp_path / "scan.pdf", size=(288, 360))
    with layout.ocr_cache(tmp_path):
        layout.build_layout(path)
        (kept,) = tmp_path.glob("*.json")
        for damaged in ("{not json", "[1, 2]", '{"lines": 1}'):
            kept.write_text(damaged, encoding="utf-8")
            _forget_this_process()
            assert layout.build_layout(path).pages[0].words
    assert len(engine) == 4


# --- What a reading is kept under stays true to how it was made ------------------------------------------------------

# Everything that draws a page for OCR, runs OCR on it, or says what is kept of it. A change to any of them changes the
# readings: add one to layout.PAGE_DRAWING (the readings kept before are then read again) and put the new digest here.
_KEPT_CODE = (
    layout._on_paper, layout._picture_for_ocr, layout._image_pages, layout._image_pictures, layout.png_page,
    layout._decoded, layout._pdf_page, layout._scan_picture, layout._page_picture, layout._png_dpi, layout._stated_dpi,
    layout._prepared, layout._deskew_angle, layout._second_page, layout.flatten_photo, layout._page_outline,
    layout.open_image, layout._engine, layout._run_rapidocr, layout._read_as_rapidocr_onnxruntime,
    layout._boxes_in_order, layout._decode_lines, layout._letters_upright, layout._reading, layout._engine_signature,
    offline.ppocrv4_params, offline.ppocrv5_params, intake.made_from_picture, intake.page_picture,
    intake._stored_pixels, intake._shown_size, intake._too_large, table_finder.page_pictures, table_finder._find,
    table_finder._iou, table_finder._find_tables, table_finder._kept_tables, table_finder.find_tables,
)  # fmt: skip
KEPT_CODE = (1, "b8223bdf2c2dc4dd2742279308fb256a3d147146088d014bdb8d147f676b82fb")


def test_how_a_page_is_drawn_and_read_is_in_what_its_reading_is_kept_under():
    digest = hashlib.sha256("\n".join(inspect.getsource(f) for f in _KEPT_CODE).encode()).hexdigest()
    assert (layout.PAGE_DRAWING, digest) == KEPT_CODE, (
        "How a page is drawn or read for OCR changed: add one to layout.PAGE_DRAWING, so the readings kept before "
        f"are read again, and set KEPT_CODE = ({layout.PAGE_DRAWING + 1}, {digest!r})"
    )


# --- PyMuPDF one thread at a time (N-14) --------------------------------------------------------------------------


def test_fifty_pdfs_read_in_eight_threads_read_as_in_one(engine, tmp_path, monkeypatch):
    """Scans and digital PDFs read, rendered for the table finder and the page viewer, and their words listed for
    teach mode, eight at a time: each the same as alone."""
    from ap_coder.web import document

    monkeypatch.setattr(layout, "OCR_DPI", 100)  # (small pictures: the test is about PyMuPDF, not OCR)
    paths = []
    for n in range(50):
        path = tmp_path / f"{n}.pdf"
        if n % 2:
            _scan(path, size=(288, 360), text=f"INVOICE {n}", pages=1 + n % 3)
        else:
            doc = pymupdf.open()
            for p in range(1 + n % 3):
                page = doc.new_page(width=288, height=360)
                for i, row in enumerate(ROWS):
                    page.insert_text((20, 30 + 14 * i), f"{row} {n}.{p}", fontsize=8)
            doc.save(path)
        paths.append(path)

    def everything(path):
        read = layout.build_layout(path), layout.second_read_layout(path)
        pictures = [np.asarray(p).tobytes() for p in table_finder.page_pictures(path)]
        drawn = document._draw_png(str(path), 0, 0, 1)
        return read, pictures, drawn, document.page_words(path), document._sizes_cached.__wrapped__(str(path), 0, 0)

    alone = [everything(path) for path in paths]
    started = threading.Barrier(8)
    with ThreadPoolExecutor(8, initializer=started.wait) as pool:
        together = list(pool.map(everything, paths))
    for (a, a_pictures, a_png, a_words, a_sizes), (b, b_pictures, b_png, b_words, b_sizes) in zip(
        alone, together, strict=True
    ):
        _same(a[0], b[0])
        assert (a[1] is None) == (b[1] is None)
        if a[1] is not None:
            _same(a[1], b[1])
        assert a_pictures == b_pictures and a_png == b_png and a_words == b_words and a_sizes == b_sizes


def test_pymupdf_outside_capture_holds_the_one_lock_too(tmp_path, monkeypatch, ground_truth):
    """Every PDF opened outside ``capture/`` holds ``layout.PDF_LOCK`` too: the page reader's pages (its own lock is
    gone), the AI reviewer's page images, the pipeline's look at a PDF, a picture made a PDF and a scan turned, an
    email's PDF text and the approved invoice's stamp."""
    from ap_coder.ai import imaging, page_reader, pipeline
    from ap_coder.core.store import APPROVED
    from ap_coder.erp import stamp
    from ap_coder.intake import triage
    from tests.conftest import SAMPLE_STEM, SAMPLES

    pdf = tmp_path / "in.pdf"
    pdf.write_bytes((SAMPLES / f"{SAMPLE_STEM}.pdf").read_bytes())
    picture = tmp_path / "photo.png"
    Image.new("RGB", (300, 400), "white").save(picture)
    held, real = [], pymupdf.open

    def opened(*args, **kwargs):
        held.append(layout.PDF_LOCK._is_owned())
        return real(*args, **kwargs)

    monkeypatch.setattr(pymupdf, "open", opened)
    data = pdf.read_bytes()
    assert page_reader._page_count(data, True) == 2 and page_reader._render(data, True, page_reader.OVIS, [1])
    assert imaging.render_page_images(pdf)
    pipeline._check_pdf(pdf)
    assert not pipeline._needs_password(pdf)
    assert intake.image_to_pdf(picture, tmp_path / "photo.pdf") == 1
    assert intake.original_of(tmp_path / "photo.pdf") is not None
    intake.turn_upright(pdf, {1: 90})
    assert triage.pdf_text(data)
    invoice = {"id": 1, "status": APPROVED, "source_path": str(pdf), "final_output": ground_truth, "reviewer": "Ann"}
    assert stamp.stamped_pdf(invoice).startswith(b"%PDF")
    assert len(held) >= 10 and all(held)
