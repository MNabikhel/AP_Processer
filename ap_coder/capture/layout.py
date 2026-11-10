"""Words and lines of an invoice, with their boxes on the page.

Sources, best first:

* the PDF's own text layer (exact characters, from PyMuPDF);
* Azure Document Intelligence words (polygons and confidence), when an analysis is at hand;
* OCR (RapidOCR, optional) for scanned pages and images, with its confidence per line.

Boxes are fractions of the page as displayed (rotation applied), so they line up with a rendered
page image. Lines are *segments*: words on one baseline that sit close together. Two columns on
the same row are two lines, which is what label/value matching needs.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import logging
import os
import re
import statistics
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .types import Box, DocLayout, Line, PageLayout, Word

log = logging.getLogger(__name__)

HEIF_EXTENSIONS = {".heic", ".heif"}  # an iPhone's photos
ONE_FRAME_EXTENSIONS = {".gif", ".webp"}  # a picture that may be animated: its first frame is the page
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"} | HEIF_EXTENSIONS | ONE_FRAME_EXTENSIONS
# The picture formats Pillow may open (``Image.open(formats=...)``): those AP Coder takes in (MPO is how Pillow names a
# camera's JPEG holding a second picture; HEIF is pillow-heif's). Every other decoder Pillow has (PSD, PCX, ICO, EPS,
# JPEG 2000, ...) stays shut: a file named .png is never handed to them (D-109).
PICTURE_FORMATS = ("JPEG", "MPO", "PNG", "TIFF", "WEBP", "GIF", "BMP", "HEIF")
MIN_TEXT_WORDS = 5  # fewer words than this on a page means it is a scan
# A page that is one picture (a scan) with fewer words of visible text than this carries a stamp, not its text: a
# scanner's header, a mailroom's "received" line (B-1512). It is read by OCR.
STAMP_WORDS = 25
OCR_DPI = 200
# A file's pages read on this computer (``build_layout``; AP_READ_PAGES sets another number, ``read_pages``): of a
# longer file the first pages and its last ``LAST_PAGES`` are read (the totals are at the end) and the pages between
# are said not read (B-267, B-2215).
READ_PAGES = 60
LAST_PAGES = 5
OCR_MAX_SIDE = 2400
NO_OCR = "OCR isn't installed on this computer, so a scan or a photo can't be read: run APProcessor.bat"
# The OCR model also reads Chinese, and now and then prints a percent sign as 号 ("GST@5号", "(9,975号)").
_OCR_PERCENT = re.compile(r"(?<=\d)号%?")
NO_HEIF = ("this iPhone photo (HEIC) can't be opened: the photo add-on (pillow-heif) isn't installed; run "
           "APProcessor.bat, or save the photo as a JPEG")  # fmt: skip


def read_pages() -> int:
    """How many pages of a file are read on this computer: AP_READ_PAGES, else ``READ_PAGES``. A value that isn't a
    positive number keeps the default, so a typo never stops the reading."""
    try:
        asked = int(os.environ.get("AP_READ_PAGES") or 0)
    except ValueError:
        asked = 0
    return asked if asked > 0 else READ_PAGES


def last_pages(max_pages: int) -> int:
    """Of the ``max_pages`` read of a longer file, how many are its last ones: ``LAST_PAGES``, at most half."""
    return min(LAST_PAGES, max_pages // 2)


def pages_read(count: int, max_pages: int) -> list[int]:
    """The pages (from 0) read of a file of ``count`` pages: all of them up to ``max_pages``; of a longer file the
    first pages and the last ones (``last_pages``), ``max_pages`` in all."""
    if count <= max_pages:
        return list(range(count))
    last = last_pages(max_pages)
    return [*range(max_pages - last), *range(count - last, count)]


class CannotRead(RuntimeError):
    """The file can't be read on this computer, for a reason AP can act on (said in plain words): OCR or the photo
    add-on isn't installed, a PDF with no pages, a web page saved as an invoice. Never an empty reading: an invoice
    with nothing read would look like a blank one."""


_heif_registered: bool | None = None


def register_heif() -> bool:
    """Let Pillow open HEIC/HEIF photos (pillow-heif), once per process. False when pillow-heif isn't installed."""
    global _heif_registered
    if _heif_registered is None:
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
            _heif_registered = True
        except Exception as exc:  # not installed (or a build that can't load here): HEIC photos can't be read
            log.info("pillow-heif not available (%s): HEIC/HEIF photos can't be opened", exc)
            _heif_registered = False
    return _heif_registered


# The most pixels a picture is decoded at (B-975): a 189 KB PNG of 13000 x 13000 pixels took 2 GB to draw. An invoice
# scanned at 600 dpi is 35 MP; a phone's 48 MP photo is a JPEG, read smaller (``Image.draft``).
MAX_PICTURE_PIXELS = 40_000_000


def too_large(width: int, height: int) -> str:
    return (f"this picture is too large to open safely ({width:,} x {height:,} pixels, over "
            f"{MAX_PICTURE_PIXELS // 1_000_000} megapixels): scan or save it at a lower resolution "
            "(300 dpi is plenty), or as a PDF")  # fmt: skip


def within_pixel_limit(frame: Any) -> None:
    """Raises ``CannotRead`` for a picture (a frame of a TIFF) over ``MAX_PICTURE_PIXELS``, before it is decoded."""
    width, height = frame.size
    if width * height > MAX_PICTURE_PIXELS:
        raise CannotRead(too_large(width, height))


def picture_formats() -> tuple[str, ...]:
    """:data:`PICTURE_FORMATS` that this Pillow has (HEIF once pillow-heif is registered): ``Image.open`` fails on a
    format it doesn't know."""
    from PIL import Image

    Image.init()
    return tuple(f for f in PICTURE_FORMATS if f in Image.OPEN)


def open_image(source: Any, name: str = "") -> Any:
    """Pillow's ``Image.open`` for a picture (a path or a file object), HEIC/HEIF photos included. Raises
    ``CannotRead`` for a HEIC photo when pillow-heif isn't installed, and for a picture over ``MAX_PICTURE_PIXELS``
    (a JPEG is read at a half, a quarter or an eighth of its size instead, its resolution scaled to match: the page
    keeps its size). Each further frame of a TIFF is checked by the caller (``within_pixel_limit``)."""
    from PIL import Image

    heif = Path(name or str(source)).suffix.lower() in HEIF_EXTENSIONS
    if not register_heif() and heif:
        raise CannotRead(NO_HEIF)
    # Over twice Pillow's own limit, DecompressionBombError (said plainly: plain_error).
    img = Image.open(source, formats=picture_formats())
    width, height = img.size
    if width * height <= MAX_PICTURE_PIXELS:
        return img
    scale = next((f for f in (2, 4, 8) if width * height <= MAX_PICTURE_PIXELS * f * f), 0)
    if img.format == "JPEG" and scale and img.mode in ("RGB", "L", "CMYK"):
        img.draft(img.mode, (width // scale, height // scale))  # decoded at that size: never at full size
        dpi = img.info.get("dpi")
        try:
            if dpi:
                img.info["dpi"] = (float(dpi[0]) * img.width / width, float(dpi[1]) * img.height / height)
        except (TypeError, ValueError, IndexError):
            pass
        return img
    img.close()
    raise CannotRead(too_large(width, height))


# ---------------------------------------------------------------- photos

PHOTO_PAGE_SHARE = (0.2, 0.95)  # a photographed page covers this much of the photo; a scan fills its picture
PHOTO_DESK_CONTRAST = 40  # the paper is at least this much lighter than the desk around it (0..255)


def flatten_photo(picture: Any) -> Any | None:
    """A phone photo of a page, as a scanner app makes it: the page cut out of the desk around it and squared up
    (its tilt and the keystone of a photo taken at an angle undone). None when the picture isn't one: a scan fills
    its picture, a page on a white desk shows no edge, a crumpled page no four corners, a page that runs off the
    picture is not one lying on a desk (or OpenCV isn't installed: it comes with the OCR)."""
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except ImportError:
        return None
    rgb = np.asarray(picture.convert("RGB"))
    height, width = rgb.shape[:2]
    scale = min(1.0, 800 / max(height, width, 1))
    small = cv2.resize(rgb, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
    page = _page_outline(small)
    if page is None:
        return None
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    if not PHOTO_PAGE_SHARE[0] <= cv2.contourArea(page) / gray.size <= PHOTO_PAGE_SHARE[1]:
        return None
    hull = cv2.convexHull(page)
    quad = None
    for loose in (0.02, 0.03, 0.05):
        approx = cv2.approxPolyDP(hull, loose * cv2.arcLength(hull, True), True)
        if len(approx) == 4:
            quad = approx.reshape(4, 2).astype(np.float32)
            break
    if quad is None:
        return None
    sh, sw = gray.shape
    if any(x < 0.01 * sw or x > 0.99 * sw or y < 0.01 * sh or y > 0.99 * sh for x, y in quad):
        return None  # the "page" runs off the picture: a scan with a dark band (a header), not a page on a desk
    inside = np.zeros_like(gray)
    cv2.fillConvexPoly(inside, quad.astype(np.int32), 255)
    desk = gray[inside == 0]
    if desk.size < 0.03 * gray.size or np.median(gray[inside > 0]) - np.median(desk) < PHOTO_DESK_CONTRAST:
        return None
    by_sum, by_diff = quad.sum(axis=1), np.diff(quad, axis=1).ravel()
    corners = np.array([quad[by_sum.argmin()], quad[by_diff.argmin()], quad[by_sum.argmax()], quad[by_diff.argmax()]],
                       dtype=np.float32) / scale  # fmt: skip  # top left, top right, bottom right, bottom left
    across = round(max(np.linalg.norm(corners[1] - corners[0]), np.linalg.norm(corners[2] - corners[3])))
    down = round(max(np.linalg.norm(corners[3] - corners[0]), np.linalg.norm(corners[2] - corners[1])))
    if min(across, down) < 200:
        return None
    square = np.array([[0, 0], [across - 1, 0], [across - 1, down - 1], [0, down - 1]], dtype=np.float32)
    flat = cv2.warpPerspective(rgb, cv2.getPerspectiveTransform(corners, square), (across, down),
                               flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)  # fmt: skip
    out = Image.fromarray(flat)
    return out.convert("L") if picture.mode in ("1", "L") else out


def _page_outline(small: Any) -> Any | None:
    """The outline of the page in a (small) photo, or None. The paper's lit part (lighter than the desk) is grown out
    to the page's edge: the picture is flooded from that part and from the picture's border at once, and the two
    meet where the colour changes sharply, the page's edge. A shadow falling across the page (a hand, the phone, a
    lamp) darkens part of it as much as the desk, but its edge is soft: the page is not cut short there."""
    import cv2
    import numpy as np

    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    _, paper = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    paper = cv2.morphologyEx(paper, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))  # the print inside is paper too
    count, labels, stats, _ = cv2.connectedComponentsWithStats(paper)
    if count < 2:
        return None
    lit = labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))  # the paper in the light
    inset = max(3, round(0.02 * max(gray.shape)))
    seed = cv2.erode(lit.astype(np.uint8), np.ones((inset, inset), np.uint8))
    if not seed.any():
        return None
    markers = np.zeros(gray.shape, np.int32)
    markers[seed > 0] = 2
    markers[:2, :] = markers[-2:, :] = 1  # the picture's border is desk (or the page runs off it: not a desk photo)
    markers[:, :2] = markers[:, -2:] = 1
    cv2.watershed(cv2.GaussianBlur(small, (5, 5), 0), markers)
    region = cv2.morphologyEx((markers == 2).astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(contours, key=cv2.contourArea) if contours else None


# ---------------------------------------------------------------- lines


def group_lines(words: list[Word], page: int) -> list[Line]:
    """Words -> line segments: same baseline band, split at gaps over ~1.6 x the text height, or over
    0.9 x when that is at least twice the row's ordinary word space."""
    if not words:
        return []
    ws = sorted(words, key=lambda w: (w.box.cy, w.box.x0))
    rows: list[list[Word]] = []
    for w in ws:
        placed = False
        for row in reversed(rows[-6:]):  # rows are sorted by height; only nearby rows can match
            ref = row[0].box
            overlap = min(ref.y1, w.box.y1) - max(ref.y0, w.box.y0)
            if overlap > 0.5 * min(ref.height, w.box.height):
                row.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
    lines: list[Line] = []
    for row in rows:
        row.sort(key=lambda w: w.box.x0)
        h = statistics.median(w.box.height for w in row) or 0.01
        gaps = [b.box.x0 - a.box.x1 for a, b in zip(row, row[1:], strict=False) if b.box.x0 > a.box.x1]
        space = statistics.median(gaps) if gaps else 0.0
        seg = [row[0]]
        for w in row[1:]:
            gap = w.box.x0 - seg[-1].box.x1
            # A wide gap ends a segment; so does a gap well over the row's ordinary word space
            # (two column labels of a header grid set close together).
            if gap > 1.6 * h or (gap > 0.9 * h and gap > 2.0 * space):
                lines.append(_line(seg, page))
                seg = [w]
            else:
                seg.append(w)
        lines.append(_line(seg, page))
    lines.sort(key=lambda ln: (round(ln.box.cy, 3), ln.box.x0))
    return lines


def _line(words: list[Word], page: int) -> Line:
    box = words[0].box
    for w in words[1:]:
        box = box.union(w.box)
    return Line(words=words, box=Box(page, box.x0, box.y0, box.x1, box.y1), text=" ".join(w.text for w in words))


# ---------------------------------------------------------------- PDF text layer


def _text_turn(page: Any) -> int:
    """The clockwise quarter turn that shows a page's text upright, when the PDF draws most of it sideways or upside
    down on a page that is not turned itself (a landscape invoice set on a portrait page, its text running up the
    page): 90, 180 or 270; 0 for text that runs across the page."""
    if page.rotation:
        return 0
    chars: dict[tuple[int, int], int] = {}
    spread: dict[tuple[int, int], list[float]] = {}
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:  # an odd page: read as it is
        return 0
    for block in blocks:
        for line in block.get("lines", []):
            d = tuple(round(v) for v in line.get("dir", (1, 0)))
            n = sum(len(span.get("text", "").strip()) for span in line.get("spans", []))
            chars[d] = chars.get(d, 0) + n
            x0, y0, x1, y1 = line.get("bbox", (0, 0, 0, 0))
            # Across the lines' run: rows of a sideways page are spread across the page's width (or height).
            spread.setdefault(d, []).extend((x0, x1) if d[0] == 0 else (y0, y1))
    total = sum(chars.values())
    if not total:
        return 0
    turn = {(0, -1): 90, (0, 1): 270, (-1, 0): 180}
    best = max(chars, key=chars.get)
    if best not in turn or chars[best] < 0.8 * total:
        return 0  # most of the text upright, or a sideways strip in the margin (form IDs, terms): read as it is
    across = page.rect.width if best[0] == 0 else page.rect.height
    covered, end = 0.0, None
    for a, b in sorted(zip(spread[best][::2], spread[best][1::2], strict=True)):
        if end is not None and a <= end[1]:
            end[1] = max(end[1], b)
            continue
        covered += (end[1] - end[0]) if end else 0.0
        end = [a, b]
    covered += (end[1] - end[0]) if end else 0.0
    if best != (-1, 0) and covered < 0.12 * across:
        return 0  # the sideways lines sit in a strip or two (a margin's terms or form number), not over the page
    return turn[best]


def _turn_box(x0: float, y0: float, x1: float, y1: float, turn: int, width: float, height: float) -> tuple[float, ...]:
    """A box on the page as drawn, moved to the page turned clockwise by ``turn`` degrees."""
    if turn == 90:
        return (height - y1, x0, height - y0, x1)
    if turn == 270:
        return (y0, width - x1, y1, width - x0)
    if turn == 180:
        return (width - x1, height - y1, width - x0, height - y0)
    return (x0, y0, x1, y1)


def _text_layer_page(page: Any, number: int) -> PageLayout:
    rect = page.rect  # as displayed (rotation applied)
    width, height = rect.width or 1.0, rect.height or 1.0
    matrix = page.rotation_matrix
    # Text drawn sideways (sweep 4: Microsoft's landscape invoices on portrait pages) is read on the page turned so
    # that it runs across; the page's ``turn`` says so, and the pipeline shows the PDF turned the same way.
    turn = _text_turn(page)
    shown_w, shown_h = (height, width) if turn in (90, 270) else (width, height)
    words: list[Word] = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words", sort=True):
        # Composed accents: some producers store "é" as "e" + a combining mark, which the readers' character
        # offsets (labels to values) would count twice.
        text = unicodedata.normalize("NFC", text).strip()
        if not text:
            continue
        r = _turn_box(*_rotated(page, matrix, x0, y0, x1, y1), turn, width, height)
        box = Box(number, r[0] / shown_w, r[1] / shown_h, r[2] / shown_w, r[3] / shown_h)
        # Some PDF writers make bold by drawing the text twice a hair apart: the page shows the word once.
        if any(w.text == text and _overlap(w.box, box) for w in words[-4:]):
            continue
        words.append(Word(text, box, 1.0, "text"))
    if turn:
        words.sort(key=lambda w: (round(w.box.cy, 3), w.box.x0))
    return PageLayout(number, shown_w, shown_h, words, group_lines(words, number), page.rotation, turn=turn)


def _overlap(a: Box, b: Box) -> bool:
    """The two boxes cover mostly the same spot (at least half of the smaller one)."""
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    height = min(a.y1, b.y1) - max(a.y0, b.y0)
    smaller = min(a.width * a.height, b.width * b.height)
    return a.page == b.page and width > 0 and height > 0 and width * height >= 0.5 * smaller


def _rotated(page: Any, matrix: Any, x0: float, y0: float, x1: float, y1: float) -> tuple[float, ...]:
    if not page.rotation:
        return (x0, y0, x1, y1)
    import pymupdf

    r = pymupdf.Rect(x0, y0, x1, y1) * matrix
    r.normalize()
    return (r.x0, r.y0, r.x1, r.y1)


# ---------------------------------------------------------------- OCR


def _installed(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:
        return False


def _ready(engine: str) -> bool:
    """RapidOCR 3 with the engine's models on disk ("ppocrv4": inside its package; "ppocrv5": from the offline
    bundle). Without them it would download them on first use, which an offline computer cannot: the other engine
    reads alone (scripts/fetch_models.py puts them in place)."""
    from ..ops import offline

    return offline.ppocrv5_ready() if engine == "ppocrv5" else offline.ppocrv4_ready()


def _engine_name() -> str:
    """The first-read OCR engine: "ppocrv4" (RapidOCR 3 with PP-OCRv4, read as rapidocr-onnxruntime read it; the reader
    is tuned on it) when its models are there, else "ppocrv5" (PP-OCRv5). ``AP_OCR_ENGINE`` forces one."""
    forced = os.environ.get("AP_OCR_ENGINE")
    if forced:
        return forced
    return "ppocrv4" if _ready("ppocrv4") else "ppocrv5"


def second_engine_name() -> str:
    """The engine of the second read: the other model when both are installed (two models make
    independent mistakes), else the same one on a straightened, enlarged page."""
    first = _engine_name()
    other = "ppocrv5" if first == "ppocrv4" else "ppocrv4"
    return other if _ready(other) else first


def ocr_available() -> bool:
    return _installed("rapidocr") and (_ready("ppocrv4") or _ready("ppocrv5"))


_ENGINES: dict[str, Any] = {}


def _engine(name: str | None = None) -> Any:
    """A callable: RGB image array -> [(quad, text, score), ...]."""
    name = name or _engine_name()
    if name not in _ENGINES:
        from ..ops import offline

        offline.quiet_onnxruntime()  # both engines run on ONNX Runtime: its usage events off
        from rapidocr import RapidOCR

        # The model files on disk: RapidOCR never downloads.
        eng = RapidOCR(params=offline.ppocrv5_engine_params() if name == "ppocrv5" else offline.ppocrv4_engine_params())
        if name != "ppocrv5":
            _read_as_rapidocr_onnxruntime(eng)
        _ENGINES[name] = functools.partial(_run_rapidocr, eng)
    return _ENGINES[name]


def _run_rapidocr(eng: Any, img: Any) -> list:
    res = eng(img)
    if res.boxes is None:
        return []
    return [(b.tolist(), t, float(c)) for b, t, c in zip(res.boxes, res.txts, res.scores, strict=False)]


def _read_as_rapidocr_onnxruntime(eng: Any) -> None:
    """RapidOCR 3 running PP-OCRv4 reads exactly as rapidocr-onnxruntime 1.4.4 did, which the reader is tuned on
    (D-163): the same model files and settings, and two steps done as that package did them. The boxes found come in
    its order (top to bottom by their top left corner, then left to right among those less than 10 pixels apart):
    RapidOCR 3 groups them into lines first, which orders some differently and so which lines are recognised in one
    batch. A line's score is the plain mean of its characters' (RapidOCR 3 rounds each, and the mean, to 5 decimals)."""
    import types

    eng.text_det.sorted_boxes = _boxes_in_order
    eng.text_rec.postprocess_op.decode = types.MethodType(_decode_lines, eng.text_rec.postprocess_op)


def _boxes_in_order(boxes: Any) -> Any:
    """rapidocr-onnxruntime 1.4.4's order of the boxes found on a page (``RapidOCR.sorted_boxes``)."""
    import numpy as np

    out = sorted(boxes, key=lambda b: (b[0][1], b[0][0]))
    for i in range(len(out) - 1):
        for j in range(i, -1, -1):
            if abs(out[j + 1][0][1] - out[j][0][1]) < 10 and out[j + 1][0][0] < out[j][0][0]:
                out[j], out[j + 1] = out[j + 1], out[j]
            else:
                break
    return np.array(out)


def _decode_lines(decoder: Any, text_index: Any, text_prob: Any, _word_box: bool, *_: Any,
                  remove_duplicate: bool = False) -> tuple[list, list]:  # fmt: skip
    """RapidOCR 3.4's ``CTCLabelDecode.decode`` as rapidocr-onnxruntime 1.4.4's: each line's text, and its score the
    mean of its characters' probabilities, unrounded (no word boxes: AP Coder asks for none)."""
    import numpy as np

    lines = []
    for tokens, probs in zip(text_index, text_prob, strict=True):
        keep = np.ones(len(tokens), dtype=bool)
        if remove_duplicate:
            keep[1:] = tokens[1:] != tokens[:-1]
        for ignored in decoder.get_ignored_tokens():
            keep &= tokens != ignored
        text = "".join(decoder.character[i] for i in tokens[keep])
        lines.append((text, np.mean(probs[keep].tolist() or [0]).tolist()))
    return lines, []


# PyMuPDF isn't made for two threads at once (N-14): every call on a document, opening and closing it, is made holding
# this lock (``capture``, ``web/document.py``). PyMuPDF holds Python's own lock (the GIL) in its calls anyway, so no
# thread waits longer for it; OCR runs outside it.
PDF_LOCK = threading.RLock()


@contextlib.contextmanager
def opened_pdf(path: str | Path) -> Iterator[Any]:
    """The PDF at ``path``, opened and closed holding ``PDF_LOCK``; each call on it takes the lock too."""
    import pymupdf

    with PDF_LOCK:
        doc = pymupdf.open(path)
    try:
        yield doc
    finally:
        with PDF_LOCK:
            doc.close()


# ---------------------------------------------------------------- the readings kept

_ocr_cache_folder: contextvars.ContextVar[Path | None] = contextvars.ContextVar("ocr_cache_folder", default=None)
OCR_CACHE_MAX_BYTES = 512 * 1024 * 1024  # about 20 KB a page and engine: two weeks of a busy server's scans
PART_AGE_SECONDS = 3600  # a reading half written this long ago is a stopped process's: removed as the folder is pruned
# How a page is drawn for OCR and how OCR runs on it (the code ``tests/capture/test_reading_cache.py`` pins): one more
# whenever that changes, so the readings kept before are read again.
PAGE_DRAWING = 1
_pruned: dict[Path, float] = {}  # when this process last pruned each folder
_written: dict[Path, int] = {}  # what it wrote there since
_KEEPING = threading.Lock()


@contextlib.contextmanager
def ocr_cache(folder: str | Path | None) -> Iterator[None]:
    """Keep each page's OCR reading in ``folder`` while inside, by what was asked: the file's content, the page, how it
    is drawn, the engine (``kept_name``). A page is OCR'd once by each engine, and not even drawn again, however often
    the invoice is read (on arrival, again with its supplier's template, for the page reader's blank pages and its
    pass, on approval, for teach mode), by any process sharing the folder; the table finder's tables are kept there
    too. The pipeline turns it on in the data folder (``.cache/ocr``); None turns it off."""
    token = _ocr_cache_folder.set(Path(folder) if folder else None)
    try:
        yield
    finally:
        _ocr_cache_folder.reset(token)


def _cache_folder() -> Path | None:
    """Where readings are kept: ``AP_OCR_CACHE`` when it names a folder (the benchmark's iterations: the reader
    changes, the scans do not; never pruned), else the folder ``ocr_cache`` set, if any."""
    env = os.environ.get("AP_OCR_CACHE")
    return Path(env) if env else _ocr_cache_folder.get()


def file_sha256(path: str | Path) -> str | None:
    """The file's SHA-256, worked out once per version of it (its path, time and size); None when it can't be read."""
    try:
        resolved = Path(path).resolve()
        stat = resolved.stat()
        return _file_sha256(str(resolved), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


@functools.lru_cache(maxsize=256)
def _file_sha256(path: str, _mtime: int, _size: int) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@functools.cache
def _libraries() -> str:
    """The libraries that draw a page and read it, with their versions: a reading made with others is not reused."""
    from importlib.metadata import PackageNotFoundError, version

    names = ("rapidocr", "onnxruntime", "onnxruntime-gpu", "onnxruntime-directml", "onnxruntime-silicon", "pymupdf",
             "pillow", "pillow-heif", "numpy", "opencv-python", "opencv-python-headless", "opencv-contrib-python",
             "opencv-contrib-python-headless")  # fmt: skip
    found = []
    for name in names:
        try:
            found.append(f"{name} {version(name)}")
        except PackageNotFoundError:
            continue
    return ", ".join(found)


@functools.cache
def _engine_signature(engine: str) -> str:
    """The engine, its settings and its model files (by their SHA-256, each file checked before it is used:
    ``_ready``): a reading by another is not reused."""
    from ..ops import offline

    try:
        five = engine == "ppocrv5"
        params = offline.ppocrv5_params() if five else offline.ppocrv4_params()
        files = offline.ppocrv5_files() if five else offline.ppocrv4_files()
    except ImportError:  # no RapidOCR: nothing is read
        return engine
    return f"{engine} {sorted((k, str(v)) for k, v in params.items())} {[f.sha256 for f in files]}"


def kept_name(*asked: Any) -> str:
    """The name a result is kept under in the readings' folder: the hash of what was asked, with the libraries'
    versions and ``PAGE_DRAWING``, so nothing made another way is ever read back."""
    import hashlib

    whole = "\n".join([*map(str, asked), _libraries(), f"drawing {PAGE_DRAWING}"])
    return hashlib.sha256(whole.encode("utf-8")).hexdigest()


def read_kept(name: str) -> dict[str, Any] | None:
    """What is kept under ``name`` in the readings' folder; None when no folder is set, or nothing (whole) is there."""
    import json

    folder = _cache_folder()
    if folder is None:
        return None
    try:
        kept = json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return kept if isinstance(kept, dict) else None


def keep(name: str, data: dict[str, Any]) -> None:
    """``data`` kept under ``name`` in the readings' folder, if one is set: written under a name of this process and
    thread, then renamed, so no process sharing the folder reads half of it. A full disk or a folder that can't be
    written to leaves nothing behind. The data folder's is pruned (``_prune``) at this process's first reading there,
    an hour after its last pruning, and after each sixteenth of ``OCR_CACHE_MAX_BYTES`` it wrote."""
    import json
    import time

    folder = _cache_folder()
    if folder is None:
        return
    target = folder / f"{name}.json"
    part = folder / f"{name}.{os.getpid()}-{threading.get_ident()}.part"
    text = json.dumps(data)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        part.write_text(text, encoding="utf-8")
        _replace(part, target)
    except OSError:
        return
    finally:
        with contextlib.suppress(OSError):
            part.unlink(missing_ok=True)
    if os.environ.get("AP_OCR_CACHE"):
        return
    now = time.monotonic()
    with _KEEPING:
        written = _written.get(folder, 0) + len(text)
        due = folder not in _pruned or now - _pruned[folder] >= 3600 or written >= OCR_CACHE_MAX_BYTES // 16
        if due:
            _pruned[folder], written = now, 0
        _written[folder] = written
    if due:
        _prune(folder, target)


def _replace(part: Path, target: Path) -> None:
    """``os.replace``. On Windows a reading another process is reading can't be replaced: left as it is when it is
    there (it is the same reading), else tried again a few times."""
    import time

    for attempt in range(5):
        try:
            os.replace(part, target)
            return
        except PermissionError:
            if target.exists():
                return
            time.sleep(0.02 * (attempt + 1))


def _prune(folder: Path, newest: Path | None = None) -> None:
    """The readings in ``folder`` (those named as before too), once over ``OCR_CACHE_MAX_BYTES``, down to three
    quarters of it, the oldest written first, never ``newest``; a half-written one a stopped process left, once
    ``PART_AGE_SECONDS`` old."""
    import time

    kept, total, now = [], 0, time.time()
    try:
        paths = list(folder.iterdir())
    except OSError:
        return
    for path in paths:
        try:
            stat = path.stat()
        except OSError:  # removed meanwhile (another process pruning)
            continue
        if path.suffix == ".part":
            if now - stat.st_mtime > PART_AGE_SECONDS:
                with contextlib.suppress(OSError):
                    path.unlink()
        elif path.suffix == ".json":
            kept.append((stat.st_mtime_ns, str(path), stat.st_size))
            total += stat.st_size
    if total <= OCR_CACHE_MAX_BYTES:
        return
    for _, name, size in sorted(kept):
        if total <= OCR_CACHE_MAX_BYTES * 3 // 4:
            break
        if newest is not None and name == str(newest):
            continue
        with contextlib.suppress(OSError):
            Path(name).unlink(missing_ok=True)  # one gone already (another process pruning) is gone too (B-1779)
            total -= size


# ---------------------------------------------------------------- the page OCR reads

PICTURES_KEPT_BYTES = 32 * 1024 * 1024  # the last pages drawn, for the second engine (a page: 11 MB at 200 dpi)


class PageToRead:
    """A page for OCR, drawn only when its reading isn't kept. ``asked`` says what it is by its content (the file's
    SHA-256, the page, how it is drawn); None: never kept. ``draw`` gives (its picture, as a PNG of it reads back:
    pixels and mode; the resolution that PNG states, 0 for none). ``shared``: the picture is kept in memory a while,
    within ``PICTURES_KEPT_BYTES``, for the next reader of the same page (the second engine)."""

    def __init__(self, asked: str | None, draw: Callable[[], tuple[Any, float]], shared: bool = True) -> None:
        self.asked, self._draw, self.shared = asked, draw, shared

    def picture(self) -> tuple[Any, float]:
        if not (self.asked and self.shared):
            return self._draw()
        with _PICTURES_LOCK:
            found = _PICTURES.get(self.asked)
            if found is not None:
                _PICTURES.move_to_end(self.asked)
                return found
        found = self._draw()
        with _PICTURES_LOCK:
            _PICTURES[self.asked] = found
            while len(_PICTURES) > 1 and sum(_size(pic) for pic, _ in _PICTURES.values()) > PICTURES_KEPT_BYTES:
                _PICTURES.popitem(last=False)
        return found


_PICTURES: OrderedDict[str, tuple[Any, float]] = OrderedDict()
_PICTURES_LOCK = threading.Lock()  # the dashboard's page reader reads in a thread of its own


def _size(picture: Any) -> int:
    return picture.width * picture.height * len(picture.getbands())


def png_page(png: bytes) -> PageToRead:
    """A picture's bytes (a PNG) as a page for OCR, named by their SHA-256."""
    import hashlib

    return PageToRead(f"picture {hashlib.sha256(png).hexdigest()}", functools.partial(_decoded, png))


def _decoded(png: bytes) -> tuple[Any, float]:
    import io

    from PIL import Image

    picture = Image.open(io.BytesIO(png), formats=picture_formats())
    picture.load()  # decoded here: the picture may be shared by two threads (``PageToRead.shared``)
    return picture, _stated_dpi(picture)


def ocr_image(image: bytes | PageToRead, number: int, engine: str | None = None) -> PageLayout:
    """OCR one page: a picture's bytes (a PNG), or a page drawn only when its reading isn't kept (``PageToRead``).
    Each OCR line becomes words (split on spaces, boxes shared by length)."""
    page = image if isinstance(image, PageToRead) else png_page(image)
    reading, letters = _reading(page, engine or _engine_name())
    result, (width, height) = reading["lines"], reading["size"]
    turn = _upright_turn(result, width, height, letters)
    if turn:
        result = _turned(result, turn, width, height)
        if turn != 180:
            width, height = height, width
    skew = _skew(result, width, height)
    tilted = abs(skew * height / max(width, 1.0)) >= TIGHT_FROM
    words: list[Word] = []
    for quad, text, score in result:
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        text = _OCR_PERCENT.sub("%", str(text))
        edges = _edges(quad) if tilted else None
        words.extend(_split_ocr_line(text, x0, y0, x1, y1, float(score), width, height, number, edges))
    out = PageLayout(number, float(width), float(height), words, group_lines(words, number))
    out.skew = skew
    out.turn = turn
    out.dpi = reading["dpi"] or round(max(width, height) / 11, 1)  # else a letter page's long side
    return out


def _reading(page: PageToRead, engine: str) -> tuple[dict[str, Any], Callable[[], tuple[int, int]]]:
    """(the engine's reading of ``page``: {"lines": [[corners, text, score], ...], "size": [width, height] of its
    picture, "dpi": the resolution that states}, a function giving how many of its lines' letters stand upright and
    upside down, ``_letters_upright``). Both kept by what was asked when a folder is set: the page isn't drawn then."""
    name = kept_name(page.asked, _engine_signature(engine)) if page.asked and _cache_folder() is not None else None
    reading = read_kept(name) if name else None
    drawn: list[Any] = []

    def picture() -> Any:  # on white paper, as OCR read it
        if not drawn:
            drawn.append(_on_paper(page.picture()[0]))
        return drawn[0]

    if not (reading and isinstance(reading.get("lines"), list) and len(reading.get("size") or ()) == 2):
        import numpy as np

        found, stated = page.picture()  # (drawn once: a page not kept in memory is drawn at each ask)
        drawn.append(_on_paper(found))
        img = drawn[0]
        result = _engine(engine)(np.asarray(img))
        reading = {"lines": [[[list(map(float, p)) for p in q], t, float(c)] for q, t, c in result or []],
                   "size": list(img.size), "dpi": stated}  # fmt: skip
        if name:
            keep(name, reading)

    def letters() -> tuple[int, int]:
        if not isinstance(reading.get("letters"), list):
            reading["letters"] = list(_letters_upright(picture(), reading["lines"]))
            if name:
                keep(name, reading)  # with its reading: read again, the page isn't drawn for them
        upright, upside_down = reading["letters"]
        return upright, upside_down

    return reading, letters


def _stated_dpi(picture: Any) -> float:
    """The resolution a picture states (pixels per inch, the coarser way), when believable: 0 for none, or one that
    makes the page smaller than a business card or longer than 17 inches (a phone's "72 dpi")."""
    try:
        dx, dy = (float(v) for v in picture.info["dpi"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    if not (50 <= dx <= 1200 and 50 <= dy <= 1200):
        return 0.0
    inches = (picture.width / dx, picture.height / dy)
    return round(min(dx, dy), 1) if 2 <= max(inches) <= 17 else 0.0


def _native_dpi(page: Any) -> float:
    """The resolution of the picture filling a scanned PDF page (pixels per inch, the coarser way, whichever way the
    picture lies on the page); 0 when the page shows no picture that large."""
    try:
        infos = page.get_image_info()
    except Exception:  # an image PyMuPDF can't describe
        return 0.0
    area = page.rect.width * page.rect.height
    best = 0.0
    for info in infos:
        x0, y0, x1, y1 = info.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        across, down = abs(x1 - x0) / 72, abs(y1 - y0) / 72
        wide, high = float(info.get("width") or 0), float(info.get("height") or 0)
        if min(wide, high) < 100 or across <= 0 or down <= 0 or across * down * 72 * 72 < 0.25 * area:
            continue  # a logo, or a picture too few pixels across for a line of text (a stretched background), not
            # the scan: a 58 mm receipt at 72 dpi is 164 px across, a 4 x 6 in. slip at 72 dpi 288 (B-1778)
        a, b = (list(info.get("transform") or (1, 0)) + [0, 0])[:2]
        if abs(b) > abs(a):  # placed turned a quarter: its width runs down the page
            wide, high = high, wide
        best = max(best, min(wide / across, high / down))
    return round(best, 1)


# A label on an invoice and an amount: on a page read upright, the amount is to the label's right.
_ROW_LABEL = re.compile(r"(?i)\b(?:sub\s*-?\s*total|total|tax(?:es)?|gst|hst|pst|qst|tps|tvq|tvh|amount|montant|"
                        r"balance|due|freight|shipping|discount|net)\b")  # fmt: skip
_ROW_AMOUNT = re.compile(r"^[^\w]*[A-Z]{0,3}\s*[$€£]?\s*-?\(?\d[\d,. ]*[.,]\d{2}\)?\s*\$?\s*(?:CAD|USD)?$")


def _turned(result: Any, turn: int, width: float, height: float) -> list[Any]:
    """The OCR's lines as on the page turned clockwise by ``turn`` (90, 180, 270): each four-cornered box carried over,
    its corners again in order (top left, top right, bottom right, bottom left of the upright line)."""

    def move(x: float, y: float) -> tuple[float, float]:
        if turn == 90:
            return height - y, x
        if turn == 180:
            return width - x, height - y
        return y, width - x

    out = []
    for quad, text, score in result:
        pts = [move(float(p[0]), float(p[1])) for p in quad]
        # The corners by where they lie, as a slightly tilted line keeps them: top left has the smallest x + y,
        # bottom right the largest, top right the smallest y - x, bottom left the largest.
        corners = (min(pts, key=lambda p: p[0] + p[1]), min(pts, key=lambda p: p[1] - p[0]),
                   max(pts, key=lambda p: p[0] + p[1]), max(pts, key=lambda p: p[1] - p[0]))  # fmt: skip
        out.append([[list(c) for c in corners], text, score])
    return out


def _label_side(result: Any) -> int:
    """Rows where an amount stands right of a label ("Total ... 105.00") less those where it stands left of one."""
    lines = []
    for quad, text, _score in result:
        xs, ys = [float(p[0]) for p in quad], [float(p[1]) for p in quad]
        lines.append((str(text).strip(), min(xs), max(xs), min(ys), max(ys)))
    side = 0
    for text, x0, x1, y0, y1 in lines:
        if not _ROW_LABEL.search(text) or any(ch.isdigit() for ch in text[-4:]):
            continue
        for other, a0, a1, b0, b1 in lines:
            overlap = min(y1, b1) - max(y0, b0)
            if overlap > 0.5 * min(y1 - y0, b1 - b0) and _ROW_AMOUNT.match(other) and (a0 >= x1 or a1 <= x0):
                side += 1 if a0 >= x1 else -1
    return side


_HEAD_WORD = re.compile(r"(?i)\b(?:invoice|facture|bill\s*to|sold\s*to|factur[ée]\s*a|date)\b")
_TOTAL_WORD = re.compile(r"(?i)\b(?:sub\s*-?\s*total|total|balance\s*due|amount\s*due|montant)\b")


def _top_down(result: Any) -> float:
    """How far below the invoice's title and dates its totals stand (positive on a page read upright); 0 when the
    page has not both."""
    heads, totals = [], []
    for quad, text, _score in result:
        cy = sum(float(p[1]) for p in quad) / max(len(quad), 1)
        if _TOTAL_WORD.search(str(text)):
            totals.append(cy)
        elif _HEAD_WORD.search(str(text)):
            heads.append(cy)
    if not heads or not totals:
        return 0.0
    return statistics.mean(totals) - statistics.mean(heads)


def _upright_turn(result: Any, width: float, height: float, letters: Callable[[], tuple[int, int]] | None = None,
                  ) -> int:  # fmt: skip
    """The clockwise quarter turn that sets a page scanned turned upright: 90 or 270 when most of its long lines run
    down the page (whichever puts the amounts right of their labels), 180 when they run across but the letters
    stand on their heads (``letters``: the page's ``_letters_upright``) or, when the letters don't tell, the amounts
    stand left of their labels; else 0. The OCR reads each line's words right (it turns the line to read it): only
    where the lines are on the page is turned."""
    long = [q for q, t, _s in result if len(str(t).strip()) >= 4]
    if len(long) < 6:
        return 0
    tall = sum(
        (max(p[1] for p in q) - min(p[1] for p in q)) > 1.5 * (max(p[0] for p in q) - min(p[0] for p in q))
        for q in long
    )
    if tall > 0.6 * len(long):
        turned = {t: _turned(result, t, width, height) for t in (90, 270)}
        # The amounts beside their labels; when they say nothing (each label on one line with its amount), the
        # totals below the invoice's title and dates.
        return max(turned, key=lambda t: (_label_side(turned[t]), _top_down(turned[t])))
    upright, upside_down = letters() if letters is not None else (0, 0)
    if upside_down >= LETTERS_SURE and upside_down >= 3 * upright:
        return 180
    if upright >= LETTERS_SURE and upright >= 3 * upside_down:
        return 0
    return 180 if _label_side(result) <= -2 else 0  # an upright page never scores below 0 (1,300 benchmark scans)


LETTERS_SURE = 8  # text lines that must say which way up the page is, three times as many as say the other way


def _letters_upright(picture: Any, result: Any) -> tuple[int, int]:
    """(lines whose letters stand upright, lines whose letters stand on their heads), from the ink of each OCR line
    of five characters or more: in Latin print, capitals, figures and the tall letters (b, d, f, h, k, l, t) rise
    above the small letters' band far more than g, j, p, q, y hang below it. A line of capitals and figures alone has
    no such band and says nothing."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return 0, 0
    gray = cv2.cvtColor(np.asarray(picture.convert("RGB")), cv2.COLOR_RGB2GRAY)
    upright = upside_down = 0
    for quad, text, _score in result:
        if len(str(text).strip()) < 5:
            continue
        corners = np.array([[float(v) for v in p[:2]] for p in quad[:4]], dtype=np.float32)
        if corners.shape != (4, 2):
            continue
        across = int(max(np.linalg.norm(corners[1] - corners[0]), np.linalg.norm(corners[2] - corners[3])))
        down = int(max(np.linalg.norm(corners[3] - corners[0]), np.linalg.norm(corners[2] - corners[1])))
        if down < 8 or across < 3 * down:
            continue  # too small to tell, or a line running down the page
        square = np.array([[0, 0], [across, 0], [across, down], [0, down]], dtype=np.float32)
        line = cv2.warpPerspective(gray, cv2.getPerspectiveTransform(corners, square), (across, down),
                                   flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)  # fmt: skip
        _, ink = cv2.threshold(line, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        rows = ink.sum(axis=1).astype(float)
        if rows.max() <= 0:
            continue
        band = np.nonzero(rows >= 0.5 * rows.max())[0]  # the small letters' band
        above, below = rows[: band.min()].sum(), rows[band.max() + 1 :].sum()
        if above + below < 0.05 * rows.sum():
            continue
        lean = float((above - below) / (above + below))
        upright += lean > 0.15
        upside_down += lean < -0.15
    return upright, upside_down


MAX_TILT = 0.15  # about 8.5 degrees (down per across): a scan fed askew is straightened; more is a page turned


def _skew(result: Any, width: float, height: float) -> float:
    """The page's tilt from the OCR's text lines (each a four-cornered box along its text): the median slope of the
    long ones' top edges, as fractions of the page (down per across); 0 when there are too few to tell."""
    slopes = []
    for quad, text, _score in result:
        try:
            (ax, ay), (bx, by) = quad[0][:2], quad[1][:2]
        except (TypeError, ValueError, IndexError):
            continue
        run = float(bx) - float(ax)
        tall = max(float(p[1]) for p in quad) - min(float(p[1]) for p in quad)
        if run > 0 and len(str(text)) >= 6 and run >= 4 * max(tall, 1.0):
            slopes.append((float(by) - float(ay)) / run)
    if len(slopes) < 5:
        return 0.0
    slope = statistics.median(slopes)
    return slope * width / max(height, 1.0) if abs(slope) <= MAX_TILT else 0.0  # a tilt, not a turn


TIGHT_FROM = 0.03  # on a page tilted more than this (down per across, in pixels: about 1.7 degrees), words follow it


def _edges(quad: Any) -> Any:
    """For an OCR line's four corners (top left, top right, bottom right, bottom left), a function giving the line's top
    and bottom at a point across it; None when the corners are not in that order."""
    try:
        (ax, ay), (bx, by), (cx, cy), (dx, dy) = ((float(p[0]), float(p[1])) for p in quad[:4])
    except (TypeError, ValueError, IndexError):
        return None
    if bx - ax < 1 or cx - dx < 1 or dy <= ay or cy <= by:
        return None

    def at(x: float) -> tuple[float, float]:
        return ay + (x - ax) * (by - ay) / (bx - ax), dy + (x - dx) * (cy - dy) / (cx - dx)

    return at


def _split_ocr_line(text: str, x0: float, y0: float, x1: float, y1: float, score: float, width: float,
                    height: float, number: int, edges: Any = None) -> list[Word]:  # fmt: skip
    """An OCR line's words, the line's box shared among them by length. ``edges`` (on a tilted page): the line's top
    and bottom across it (``_edges``), so each word's box is as high as the print where it stands, not the whole tilted
    line's: on a page tilted 5 degrees, a long line's box reached down over the next line, and the two were read as
    one."""
    parts = text.split()
    if not parts:
        return []
    total = sum(len(p) for p in parts) + (len(parts) - 1)
    out = []
    pos = 0
    for p in parts:
        a = x0 + (x1 - x0) * pos / total
        b = x0 + (x1 - x0) * (pos + len(p)) / total
        top, bottom = y0, y1
        if edges is not None:
            (ta, ba), (tb, bb) = edges(a), edges(b)
            top, bottom = max(y0, min(ta, tb)), min(y1, max(ba, bb))
            if bottom <= top:
                top, bottom = y0, y1
        out.append(Word(p, Box(number, a / width, top / height, b / width, bottom / height), score, "ocr"))
        pos += len(p) + 1
    return out


def _on_paper(img: Any) -> Any:
    """The image in RGB, its transparent parts white paper (a plain conversion turns them black)."""
    if img.mode in ("RGBA", "LA", "PA", "P"):
        from PIL import Image

        img = img.convert("RGBA")
        paper = Image.new("RGB", img.size, "white")
        paper.paste(img, mask=img.getchannel("A"))
        return paper
    return img.convert("RGB")


def _image_pages(path: Path, max_pages: int) -> list[PageToRead]:
    """Each page of an image file for OCR: a PNG as it is, every frame of a multi-page TIFF, at the resolution it was
    scanned at (at most ``OCR_MAX_SIDE``), turned upright as its EXIF says. Only counted here: drawn, all of them at
    once, when a page's reading isn't kept."""
    if path.suffix.lower() == ".png":
        return [png_page(path.read_bytes())]
    from PIL import ImageSequence

    sha = file_sha256(path)  # (before it is opened: what is read is what the name says)
    with open_image(path) as img:
        count = len(list(zip(range(max_pages), ImageSequence.Iterator(img), strict=False)))
    drawn = functools.cache(lambda: _image_pictures(path, max_pages))
    return [PageToRead(f"picture {sha} frame {i}, at most {OCR_MAX_SIDE} px" if sha else None,
                       lambda i=i: drawn()[i]) for i in range(count)]  # fmt: skip


def _image_pictures(path: Path, max_pages: int) -> list[tuple[Any, float]]:
    from PIL import ImageOps, ImageSequence

    out = []
    with open_image(path) as img:
        for i, frame in enumerate(ImageSequence.Iterator(img)):
            if i >= max_pages:
                break
            upright = ImageOps.exif_transpose(frame.copy())
            out.append((_picture_for_ocr(flatten_photo(upright) or upright), 0.0))  # (its PNG stated no resolution)
    return out


def _picture_for_ocr(picture: Any) -> Any:
    """A page's picture (upright) as OCR reads it: on white paper, its long side at most ``OCR_MAX_SIDE``."""
    from PIL import Image

    pic = _on_paper(picture)
    scale = OCR_MAX_SIDE / max(pic.width, pic.height, 1)
    if scale < 1:
        pic = pic.resize((max(1, round(pic.width * scale)), max(1, round(pic.height * scale))), Image.LANCZOS)
    return pic


def _pdf_page(sha: str | None, doc: Any, page: Any, index: int) -> PageToRead:
    """A PDF page without text (page ``index``, from 0) for OCR, named by the file's content (``sha``: its SHA-256,
    worked out before the file was opened)."""
    asked = f"pdf {sha} page {index}, at {OCR_DPI} dpi, at most {OCR_MAX_SIDE} px" if sha else None
    return PageToRead(asked, functools.partial(_scan_picture, doc, page))


def _scan_picture(doc: Any, page: Any) -> tuple[Any, float]:
    """The picture OCR reads for a PDF page without text: for a PDF AP Coder made from a photo or a scan
    (``intake/pictures.py``), that picture's own pixels, as OCR read the picture before it was converted (its PNG
    stated no resolution); any other page rendered at ``OCR_DPI``. Its pixels straight from PyMuPDF and Pillow: no PNG
    made and read back (about half a second a page)."""
    from ..intake.pictures import made_from_picture, page_picture

    with PDF_LOCK:
        picture = page_picture(doc, page) if made_from_picture(doc) else None
    if picture is not None:
        return _picture_for_ocr(picture), 0.0
    return _page_picture(page)


def _page_picture(page: Any, dpi: int = OCR_DPI) -> tuple[Any, float]:
    """The page drawn at ``dpi`` (its long side at most ``OCR_MAX_SIDE``), and the resolution its PNG stated (PyMuPDF
    wrote the pixmap's own)."""
    import pymupdf
    from PIL import Image

    with PDF_LOCK:
        zoom = dpi / 72
        longest = max(page.rect.width, page.rect.height) * zoom
        if longest > OCR_MAX_SIDE:
            zoom *= OCR_MAX_SIDE / longest
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        picture = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        stated = _png_dpi(pix.xres, pix.yres)
    if stated:
        picture.info["dpi"] = stated
    return picture, _stated_dpi(picture)


@functools.lru_cache(maxsize=8)
def _png_dpi(xres: int, yres: int) -> tuple[float, float] | None:
    """The resolution the PNG PyMuPDF writes of a pixmap at ``xres`` x ``yres`` dpi states, as Pillow reads it back."""
    import io

    import pymupdf
    from PIL import Image

    with PDF_LOCK:
        tiny = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1, 1), False)
        tiny.set_dpi(xres, yres)
        png = tiny.tobytes("png")
    return Image.open(io.BytesIO(png)).info.get("dpi")


# ---------------------------------------------------------------- Document Intelligence


def layout_from_di(raw: dict[str, Any]) -> DocLayout | None:
    """Words from an ``AnalyzeResult.as_dict()``: polygons in the page's unit, with confidence."""
    pages = []
    for p in raw.get("pages") or []:
        width, height = float(p.get("width") or 0), float(p.get("height") or 0)
        number = int(p.get("pageNumber") or len(pages) + 1)
        if not width or not height:
            continue
        words = []
        for w in p.get("words") or []:
            poly = w.get("polygon") or []
            if len(poly) < 8 or not (w.get("content") or "").strip():
                continue
            xs, ys = poly[0::2], poly[1::2]
            box = Box(number, min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height)
            words.append(Word(w["content"].strip(), box, float(w.get("confidence") or 0.0), "di"))
        pages.append(PageLayout(number, width, height, words, group_lines(words, number), int(p.get("angle") or 0)))
    return DocLayout(pages, "di") if pages else None


# ---------------------------------------------------------------- entry point


def _looks_scanned(page: Any) -> bool:
    """A PDF page without a text layer that shows a picture over a good part of it: a scan, not a blank page."""
    area = max(page.rect.width * page.rect.height, 1.0)
    try:
        boxes = [info.get("bbox") for info in page.get_image_info()]
    except Exception:  # an image PyMuPDF can't describe: taken for a scan
        return True
    return any(b and (b[2] - b[0]) * (b[3] - b[1]) >= 0.25 * area for b in boxes)


def _one_picture(page: Any) -> bool:
    """The page is one picture over (almost) all of it: a scan, whatever text lies on it."""
    area = max(page.rect.width * page.rect.height, 1.0)
    try:
        boxes = [info.get("bbox") for info in page.get_image_info()]
    except Exception:  # noqa: BLE001 - an image PyMuPDF can't describe: not taken for a scan here
        return False
    return any(b and (b[2] - b[0]) * (b[3] - b[1]) >= 0.8 * area for b in boxes)


def text_is_the_page(page: Any, text_page: PageLayout) -> bool:
    """Whether a PDF page's text layer is what the page shows (a digital page, read exactly), rather than a scan
    with text on it: a scanner's "searchable PDF", whose own OCR is drawn invisibly under the picture, slips and all
    (B-1511), or a stamp of real text on the scan (B-1512). Those are read as the scans they are."""
    if len(text_page.words) < MIN_TEXT_WORDS:
        return False
    if not _one_picture(page):
        return True
    try:
        trace = page.get_texttrace()
    except Exception:  # noqa: BLE001 - the text can't be told apart: as before, the text layer
        return True
    shown = sum(len(span.get("chars") or ()) for span in trace if span.get("type") != 3)
    hidden = sum(len(span.get("chars") or ()) for span in trace if span.get("type") == 3)
    return hidden <= shown and len(text_page.words) >= STAMP_WORDS


def build_layout(path: str | Path, *, di_raw: dict[str, Any] | None = None, ocr: str | bool = "auto",
                 max_pages: int | None = None) -> DocLayout:  # fmt: skip
    """The words of ``path``. ``ocr``: "auto" (scanned pages only), True (force OCR on every page), False (never).

    A PDF longer than the pages read (``read_pages``) is read at its first and its last pages (``pages_read``); the
    pages between are kept empty, each in its place, and named in ``DocLayout.not_read``. ``max_pages``: only the
    file's first pages, that many (the page reader's), the rest counted in ``pages_left_out``.

    With "auto", a photo or a scanned page on a computer without OCR raises ``CannotRead`` ("OCR isn't installed"),
    and so does an iPhone photo (HEIC) without pillow-heif: nothing read is never passed off as a blank invoice."""
    path = Path(path)
    suffix = path.suffix.lower()
    use_ocr = ocr is True or (ocr == "auto" and ocr_available())
    if suffix in IMAGE_EXTENSIONS:
        if di_raw:
            di = layout_from_di(di_raw)
            if di:
                return di
        if suffix in HEIF_EXTENSIONS and not register_heif():
            raise CannotRead(NO_HEIF)
        if not use_ocr:
            if ocr == "auto":
                raise CannotRead(NO_OCR)
            return DocLayout([], "none")
        frames = _image_pages(path, max_pages or read_pages())
        return DocLayout([ocr_image(page, i) for i, page in enumerate(frames, 1)], "ocr")

    pages: list[PageLayout] = []
    sources: set[str] = set()
    # Pages not read, each kept empty in its place and counted (``DocLayout.pages_unread_locally``): those OCR could
    # not read, and a long file's pages between the first and the last ones read.
    not_read: list[int] = []
    di = layout_from_di(di_raw) if di_raw else None
    sha = file_sha256(path) if use_ocr else None  # (before it is opened: what is read is what the name says)
    with opened_pdf(path) as doc:  # PyMuPDF's calls holding its lock; OCR outside it
        with PDF_LOCK:
            count = doc.page_count
        left_out = max(0, count - max_pages) if max_pages else 0  # past the pages asked for: only counted
        reading = set(range(max_pages) if max_pages else pages_read(count, read_pages()))
        for i in range(count - left_out):
            number = i + 1
            with PDF_LOCK:
                page = doc[i]
                if i not in reading:
                    pages.append(PageLayout(number, page.rect.width or 1.0, page.rect.height or 1.0, [], []))
                    not_read.append(number)
                    continue
                text_page = _text_layer_page(page, number)
                # Its text, unless it is a scan with text on it and OCR can read the scan (B-1511, B-1512).
                texted = len(text_page.words) >= MIN_TEXT_WORDS
                as_text = texted and ocr is not True and (not use_ocr or text_is_the_page(page, text_page))
            if as_text:
                pages.append(text_page)
                sources.add("text")
                continue
            di_page = next((p for p in di.pages if p.number == number), None) if di else None
            if di_page and di_page.words:
                pages.append(di_page)
                sources.add("di")
            elif use_ocr:
                try:
                    scanned = ocr_image(_pdf_page(sha, doc, page, i), number)
                    with PDF_LOCK:  # rendered finer than it was scanned; one of its own shapes, never scanned (B-1761)
                        scanned.dpi = _native_dpi(page)
                    pages.append(scanned)
                    sources.add("ocr")
                except Exception as exc:  # a page OCR cannot read is kept empty and counted unread, never fatal
                    log.warning("%s page %d: OCR failed (%s)", path.name, number, exc)
                    pages.append(text_page)
                    if texted:  # a scan with a text layer: that text, the best there is
                        sources.add("text")
                    else:
                        not_read.append(number)
            else:
                with PDF_LOCK:
                    scanned_page = ocr == "auto" and _looks_scanned(page)
                if scanned_page:
                    raise CannotRead(NO_OCR)
                pages.append(text_page)
                sources.add("text")
    source = sources.pop() if len(sources) == 1 else ("mixed" if sources else "none")
    return DocLayout(pages, source, pages_left_out=left_out, pages_unread_locally=len(not_read), not_read=not_read)


# ---------------------------------------------------------------- second read of a scan


def _deskew_angle(gray: Any) -> float:
    """The page's tilt in degrees (-2..2): the rotation that makes the text rows sharpest
    (largest variance of the horizontal ink profile), searched on a small copy."""
    import numpy as np
    from PIL import Image

    small = gray.copy()
    small.thumbnail((900, 900))
    ink = np.asarray(small, dtype=np.float32) < 128
    best, best_score = 0.0, -1.0
    for tenth in range(-20, 21, 2):
        angle = tenth / 10
        img = Image.fromarray((ink * 255).astype(np.uint8)).rotate(angle, resample=Image.NEAREST, fillcolor=0)
        profile = (np.asarray(img) > 0).sum(axis=1).astype(np.float64)
        score = float(profile.var())
        if score > best_score:
            best, best_score = angle, score
    return best


def _prepared(picture: Any) -> Any:
    """The page straightened and enlarged 1.5x: OCR reads small, tilted print better."""
    from PIL import Image, ImageFilter

    img = picture.convert("L")
    angle = _deskew_angle(img)
    if abs(angle) >= 0.2:
        img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=255, expand=False)
    img = img.resize((int(img.width * 1.5), int(img.height * 1.5)), Image.LANCZOS).filter(ImageFilter.SHARPEN)
    return img.convert("RGB")


def _second_page(first: PageToRead) -> PageToRead:
    """The page as the second read sees it: as scanned for another model (its own view of the same pixels),
    straightened and enlarged when it is the same model again (made from the first read's picture, never kept in
    memory; its PNG stated no resolution)."""
    if second_engine_name() != _engine_name():
        return first
    asked = f"{first.asked}, straightened and enlarged" if first.asked else None
    return PageToRead(asked, lambda: (_prepared(first.picture()[0]), 0.0), shared=False)


def second_read_layout(path: str | Path) -> DocLayout | None:
    """An independent second OCR of a scanned invoice, from a straightened, enlarged copy of each page.
    Its boxes are fractions of the page like the first read's (the straightening moves them by at most
    a few thousandths). None for a text PDF or when OCR is not installed. Of a long PDF, the pages the first read
    reads (``pages_read``), the others kept empty in their place."""
    path = Path(path)
    if not ocr_available():
        return None
    pages: list[PageLayout] = []
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        for i, page in enumerate(_image_pages(path, read_pages()), 1):
            pages.append(ocr_image(_second_page(page), i, second_engine_name()))
    else:
        sha = file_sha256(path)  # (before it is opened: what is read is what the name says)
        with opened_pdf(path) as doc:
            with PDF_LOCK:
                count = doc.page_count
            reading = set(pages_read(count, read_pages()))
            for i in range(count):
                with PDF_LOCK:
                    page = doc[i]
                    if i not in reading:
                        pages.append(PageLayout(i + 1, page.rect.width or 1.0, page.rect.height or 1.0, [], []))
                        continue
                    text = text_is_the_page(page, _text_layer_page(page, i + 1))
                if text:
                    return None  # a text PDF: the text layer is exact
                pages.append(ocr_image(_second_page(_pdf_page(sha, doc, page, i)), i + 1, second_engine_name()))
    return DocLayout(pages, "ocr")
