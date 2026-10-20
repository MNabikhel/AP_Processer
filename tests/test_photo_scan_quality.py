"""Sweep 4 (real-world testing): reading phone photos and poor scans. Each picture here is made in the test, degraded
the way the failing pages were (no corpus file is kept in the repo)."""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw, ImageFilter

from ap_coder.capture import layout


def _page(size=(600, 800)) -> Image.Image:
    """A white page with a dark mark at its top left and another at its right edge."""
    page = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(page)
    draw.rectangle([50, 60, 250, 120], fill="black")
    draw.rectangle([size[0] - 120, 400, size[0] - 40, 440], fill="black")
    return page


def _on_desk(page: Image.Image, desk=(70, 60, 55), at=(150, 150), canvas=(900, 1100), tilt=4.0) -> Image.Image:
    photo = Image.new("RGB", canvas, desk)
    photo.paste(page, at)
    return photo.rotate(tilt, resample=Image.BICUBIC, fillcolor=desk)


def _shadowed(photo: Image.Image, box, dim=0.45, soft=12) -> Image.Image:
    """A shadow (a hand, a phone, a lamp's edge) falling over ``box``: everything under it dimmed, with a soft edge."""
    mask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(mask).rectangle(box, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(soft))
    dark = photo.point(lambda v: round(v * dim))
    return Image.composite(dark, photo, mask)


def test_a_shadow_across_the_page_does_not_cut_the_page_short():
    """Sweep 4: a shadow band over a third of a photographed page made that third look like desk; the page was cut at
    the shadow and its totals column was lost (h-0119, h-0110, h-0107: 26 desk photos, 9 cut short)."""
    pytest.importorskip("cv2")
    photo = _shadowed(_on_desk(_page()), (520, 0, 900, 1100))  # the right third of the page, and the desk beside it
    flat = layout.flatten_photo(photo)
    assert flat is not None
    assert abs(flat.width / flat.height - 600 / 800) < 0.04  # the whole page, not the part in the light
    right = flat.getpixel((round((600 - 80) / 600 * flat.width), round(420 / 800 * flat.height)))
    assert max(right) < 60  # the mark at the page's right edge, inside the shadow, is on the flattened page


_LINES = [
    "Kingsway Mechanical Limited",
    "Invoice date: April 23, 2026",
    "Bill to: Northline Energy Corp.",
    "Hydraulic hose assembly, half inch",
    "Monthly maintenance contract",
    "Shipping cartons, heavy duty",
    "Forklift rental, weekly",
    "Janitorial service, Unit 4",
    "Disinfectant, four litres",
    "Payment due within thirty days",
    "Thank you for your business",
    "Questions? Call the billing desk",
]


def _printed_page(upside_down: bool) -> tuple[bytes, list]:
    """A page of mixed-case print with no amount beside a label (nothing for the labels' side to go by), and the
    OCR's lines for it as the page lies: upside down, the lines' boxes are where the turned page has them."""
    import io

    from PIL import ImageFont

    w, h = 1000, 1300
    page = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=28)
    boxes = []
    for i, text in enumerate(_LINES):
        x, y = 80, 80 + 90 * i
        x0, y0, x1, y1 = draw.textbbox((x, y), text, font=font)
        draw.text((x, y), text, fill=0, font=font)
        boxes.append((text, x0 - 4, y0 - 6, x1 + 4, y1 + 6))
    if upside_down:
        page = page.rotate(180)
        boxes = [(t, w - x1, h - y1, w - x0, h - y0) for t, x0, y0, x1, y1 in boxes]
    result = [[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]], t, 0.95] for t, x0, y0, x1, y1 in boxes]
    png = io.BytesIO()
    page.save(png, "PNG")
    return png.getvalue(), result


@pytest.mark.parametrize("upside_down", [False, True])
def test_a_page_scanned_upside_down_is_known_by_its_letters(monkeypatch, upside_down):
    """Sweep 4: a page scanned upside down was turned only when its amounts stood left of their labels; a credit's
    "1,234.56 CR" amounts or a page of descriptions gave too few, so h-0064 was read upside down (nothing found).
    The letters say it: capitals and tall letters rise above the small letters' band, few letters hang below."""
    pytest.importorskip("cv2")
    png, result = _printed_page(upside_down)
    monkeypatch.setattr(layout, "_ocr_cached", lambda *a, **k: result)
    page = layout.ocr_image(png, 1)
    assert page.turn == (180 if upside_down else 0)
    if upside_down:
        first = min(page.lines, key=lambda ln: ln.box.y0)
        assert first.text == _LINES[0]  # read from the top of the upright page
