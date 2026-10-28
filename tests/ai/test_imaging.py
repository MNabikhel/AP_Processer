"""Page images for a chat model that can see: small enough for Gemma 4 to take in one batch (B-1414)."""

import io

import pymupdf
from PIL import Image

from ap_coder.ai.imaging import MAX_PIXELS, render_page_images
from tests.conftest import SAMPLES

LETTER = SAMPLES / "harbourview_NS_HST_HPS-2026-0347.pdf"


def _size(page):
    with Image.open(io.BytesIO(page.data)) as img:
        return img.size


def test_a_letter_page_fits_gemma_4s_batch():
    """Gemma 4 makes a token of each 48 x 48 pixels and must take a whole picture in one batch, 512 tokens in LM Studio
    unless set otherwise: a letter page at 150 dpi (1275 x 1650, about 925 tokens) stopped its model server, and at
    918 x 1188 (about 480) it read the same header right, sooner (B-1414)."""
    width, height = _size(render_page_images(LETTER, 1)[0])
    assert 0.99 * MAX_PIXELS < width * height <= MAX_PIXELS
    assert abs(width / height - 8.5 / 11) < 0.005
    assert MAX_PIXELS / 48**2 + (width + height) / 48 < 500  # its tokens, the edges rounded up: under 512


def test_a_small_page_keeps_150_dpi(tmp_path):
    path = tmp_path / "receipt.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=3 * 72, height=5 * 72)
        doc.save(path)
    assert _size(render_page_images(path, 1)[0]) == (450, 750)


def test_a_large_photo_is_sent_smaller_the_right_way_up(tmp_path):
    photo = Image.new("RGB", (4032, 3024), "white")  # a phone photo taken upright: stored on its side
    exif = photo.getexif()
    exif[0x0112] = 6  # EXIF orientation: turn it 90 degrees to show it
    path = tmp_path / "photo.jpg"
    photo.save(path, quality=90, exif=exif)
    page = render_page_images(path)[0]
    width, height = _size(page)
    assert page.mime_type == "image/jpeg"
    assert width * height <= MAX_PIXELS and width < height and abs(width / height - 0.75) < 0.005


def test_a_small_picture_is_sent_as_it_is(tmp_path):
    path = tmp_path / "small.png"
    Image.new("RGB", (800, 1000), "white").save(path)
    assert render_page_images(path)[0].data == path.read_bytes()
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8 not a picture")
    assert render_page_images(broken)[0].data == broken.read_bytes()  # the model server says what it makes of it


def test_tiff_frames_are_sent_smaller(tmp_path):
    path = tmp_path / "fax.tif"
    frames = [Image.new("L", (1728, 2200), 255) for _ in range(2)]  # a fax page at 200 dpi: 3.8 million pixels
    frames[0].save(path, save_all=True, append_images=frames[1:])
    pages = render_page_images(path)
    assert len(pages) == 2 and all(w * h <= MAX_PIXELS for w, h in map(_size, pages))


def test_without_a_limit_pages_are_rendered_at_150_dpi():
    """The training export's pages (it sizes them itself)."""
    assert _size(render_page_images(LETTER, 1, max_pixels=None)[0]) == (1275, 1650)
