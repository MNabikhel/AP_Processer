"""Sweep 4 (real-world testing): reading phone photos and poor scans. Each picture here is made in the test, degraded
the way the failing pages were (no corpus file is kept in the repo)."""

from __future__ import annotations

import io

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


_TOTALS = [  # (label, amount) rows of a totals block, a row apart
    ("Net amount", "1,990.48"),
    ("GST 5%", "99.52"),
    ("PST 7%", "139.33"),
    ("Total (incl. tax)", "2,229.33"),
]


def _tilted_scan(degrees: float):
    """The totals block of a scan fed askew, as OCR reports it: each line where it lies on the tilted page (the labels
    at the middle of the page, their amounts at its right edge, a row's worth higher)."""
    import math

    from ap_coder.capture.types import DocLayout, PageLayout

    w, h = 2200, 1700  # a landscape page
    slope = math.tan(math.radians(degrees))
    words = []
    for i, (label, amount) in enumerate(_TOTALS):
        y = 900 + 34 * i
        for text, x0, x1 in ((label, 1350, 1350 + 18 * len(label)), (amount, 1900, 1900 + 18 * len(amount))):
            dy = -slope * ((x0 + x1) / 2 - w / 2)  # the page turned anticlockwise: the right side rises
            words += layout._split_ocr_line(text, x0, y + dy, x1, y + dy + 24, 0.98, w, h, 1)
    words += layout._split_ocr_line("Invoice No: A-1001", 100, 200, 420, 224, 0.98, w, h, 1)
    page = PageLayout(1, w, h, words, layout.group_lines(words, 1))
    page.skew = -slope * w / h  # down per across, as fractions of the page (as ``layout._skew`` measures it)
    page.dpi = 200
    return DocLayout([page], "ocr")


def test_a_scan_fed_askew_reads_each_label_with_its_own_amount(tmp_path):
    """Sweep 4: on a landscape scan tilted 4 degrees, an amount at the right edge stood level with the label of the row
    above: subtotal 99.52, GST 139.33, PST 2,229.33 (h-0085). The header fields are now read on the page straightened,
    as the line items were, with their boxes where the amounts are printed."""
    from ap_coder.capture import analyze

    path = tmp_path / "scan.png"
    Image.new("RGB", (2200, 1700), "white").save(path)
    fields = analyze(path, layout=_tilted_scan(4.2), second_read=False, tables=False).fields
    assert fields["subtotal"].value == 1990.48
    assert fields["gst_amount"].value == 99.52
    assert fields["grand_total"].value == 2229.33
    box = fields["subtotal"].boxes[0]
    assert box.x0 > 0.8 and box.y0 < 900 / 1700  # where it is printed: right, and higher than its label's row


def test_on_a_steeply_tilted_scan_two_lines_close_together_stay_apart(monkeypatch):
    """Sweep 4: on a page scanned at 5 degrees, the box around a long OCR line (the vendor's name in large print)
    reached down over the address under it, and the two were one line: "1880 Industrial Pkwy Kingsway Mechanical
    Limited" (h-0008, the vendor then taken from a salesperson's name). Each word's box now follows the tilted line."""
    quads = [  # as OCR gave them on that page: four corners, text (the page 1700 x 2200)
        ([[190, 54], [807, 105], [804, 153], [187, 102]], "Kingsway Mechanical Limited"),
        ([[185, 121], [415, 143], [412, 176], [182, 154]], "1880 Industrial Pkwy"),
        ([[179, 159], [415, 179], [412, 212], [176, 192]], "Surrey, BC V7B 1G6"),
        ([[1288, 152], [1673, 183], [1669, 234], [1284, 203]], "TAX INVOICE"),
        ([[177, 196], [528, 227], [525, 257], [174, 226]], "250-555-2973 F: 250-555-9318"),
        ([[1007, 212], [1207, 230], [1204, 263], [1004, 245]], "Invoice Number:"),
        ([[173, 235], [442, 258], [439, 287], [170, 264]], "BC PST# PST-3634-7542"),
    ]
    result = [[q, t, 0.97] for q, t in quads]
    monkeypatch.setattr(layout, "_ocr_cached", lambda *a, **k: result)
    png = io.BytesIO()
    Image.new("RGB", (1700, 2200), "white").save(png, "PNG")
    page = layout.ocr_image(png.getvalue(), 1)
    assert not any("Kingsway" in ln.text and "1880" in ln.text for ln in page.lines)
    name = next(w for w in page.words if w.text == "Kingsway")
    assert name.box.y1 < 125 / 2200  # its box ends where the print ends there, not at the tilted line's lowest corner
    from ap_coder.capture import _read_straight
    from ap_coder.capture.types import DocLayout

    vendor = _read_straight(DocLayout([page], "ocr"), None)["vendor_name"][0]
    assert vendor.value == "Kingsway Mechanical Limited"  # read straightened, the name's words in one line again
