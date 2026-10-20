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
