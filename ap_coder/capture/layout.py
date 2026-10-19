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
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .types import Box, DocLayout, Line, PageLayout, Word

log = logging.getLogger(__name__)

HEIF_EXTENSIONS = {".heic", ".heif"}  # an iPhone's photos
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"} | HEIF_EXTENSIONS
MIN_TEXT_WORDS = 5  # fewer words than this on a page means it is a scan
OCR_DPI = 200
OCR_MAX_SIDE = 2400
NO_OCR = "OCR isn't installed on this computer, so a scan or a photo can't be read: run APProcessor.bat"
# The OCR model also reads Chinese, and now and then prints a percent sign as 号 ("GST@5号", "(9,975号)").
_OCR_PERCENT = re.compile(r"(?<=\d)号%?")
NO_HEIF = ("this iPhone photo (HEIC) can't be opened: the photo add-on (pillow-heif) isn't installed; run "
           "APProcessor.bat, or save the photo as a JPEG")  # fmt: skip


class CannotRead(RuntimeError):
    """The file can't be read on this computer, for a reason AP can act on (said in plain words): OCR or the photo
    add-on isn't installed. Never an empty reading: an invoice with nothing read would look like a blank one."""


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


def open_image(source: Any, name: str = "") -> Any:
    """Pillow's ``Image.open`` for a picture (a path or a file object), HEIC/HEIF photos included. Raises
    ``CannotRead`` for a HEIC photo when pillow-heif isn't installed."""
    from PIL import Image

    heif = Path(name or str(source)).suffix.lower() in HEIF_EXTENSIONS
    if not register_heif() and heif:
        raise CannotRead(NO_HEIF)
    return Image.open(source)


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
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    _, paper = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    paper = cv2.morphologyEx(paper, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))  # the print inside is paper too
    contours, _ = cv2.findContours(paper, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    page = max(contours, key=cv2.contourArea)
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


def _text_layer_page(page: Any, number: int) -> PageLayout:
    rect = page.rect  # as displayed (rotation applied)
    width, height = rect.width or 1.0, rect.height or 1.0
    matrix = page.rotation_matrix
    words: list[Word] = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words", sort=True):
        # Composed accents: some producers store "é" as "e" + a combining mark, which the readers' character
        # offsets (labels to values) would count twice.
        text = unicodedata.normalize("NFC", text).strip()
        if not text:
            continue
        r = _rotated(page, matrix, x0, y0, x1, y1)
        words.append(Word(text, Box(number, r[0] / width, r[1] / height, r[2] / width, r[3] / height), 1.0, "text"))
    return PageLayout(number, width, height, words, group_lines(words, number), page.rotation)


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


def _ppocrv5_ready() -> bool:
    """RapidOCR 3 with its PP-OCRv5 models on disk. Without them it would download them on first use,
    which an offline computer cannot: the other engine reads alone (scripts/fetch_models.py fetches them)."""
    from ..offline import ppocrv5_ready

    return ppocrv5_ready()


def _engine_name() -> str:
    """The first-read OCR engine: "rapidocr" (rapidocr-onnxruntime, PP-OCRv4; the reader is tuned on
    it) when installed, else "ppocrv5" (RapidOCR 3, PP-OCRv5). ``AP_OCR_ENGINE`` forces one."""
    forced = os.environ.get("AP_OCR_ENGINE")
    if forced:
        return forced
    return "rapidocr" if _installed("rapidocr_onnxruntime") else "ppocrv5"


def second_engine_name() -> str:
    """The engine of the second read: the other model when both are installed (two models make
    independent mistakes), else the same one on a straightened, enlarged page."""
    first = _engine_name()
    other = "ppocrv5" if first == "rapidocr" else "rapidocr"
    return other if (_ppocrv5_ready() if other == "ppocrv5" else _installed("rapidocr_onnxruntime")) else first


def ocr_available() -> bool:
    return _installed("rapidocr_onnxruntime") or (_installed("rapidocr") and _ppocrv5_ready())


_ENGINES: dict[str, Any] = {}


def _engine(name: str | None = None) -> Any:
    """A callable: RGB image array -> [(quad, text, score), ...]."""
    name = name or _engine_name()
    if name not in _ENGINES:
        from ..offline import quiet_onnxruntime

        quiet_onnxruntime()  # both engines run on ONNX Runtime: its usage events off
        if name == "ppocrv5":
            from rapidocr import RapidOCR

            from ..offline import ppocrv5_engine_params

            eng = RapidOCR(params=ppocrv5_engine_params())  # the model files on disk: RapidOCR never downloads

            def run(img: Any) -> list:
                res = eng(img)
                if res.boxes is None:
                    return []
                return [(b.tolist(), t, float(c)) for b, t, c in zip(res.boxes, res.txts, res.scores, strict=False)]

            _ENGINES[name] = run
        else:
            from rapidocr_onnxruntime import RapidOCR

            eng4 = RapidOCR()
            _ENGINES[name] = lambda img: eng4(img)[0]
    return _ENGINES[name]


_ocr_cache_folder: contextvars.ContextVar[Path | None] = contextvars.ContextVar("ocr_cache_folder", default=None)
OCR_CACHE_MAX_FILES = 4000  # about 60 MB of page readings; the oldest go first
_pruned: dict[Path, float] = {}


@contextlib.contextmanager
def ocr_cache(folder: str | Path | None) -> Iterator[None]:
    """Keep each page's OCR result in ``folder`` while inside: the same page image is OCR'd once by each engine, however
    often the invoice is read (on arrival, again with its supplier's template, again once the page reader has read
    it). The pipeline turns it on in the data folder (``.cache/ocr``); None turns it off."""
    token = _ocr_cache_folder.set(Path(folder) if folder else None)
    try:
        yield
    finally:
        _ocr_cache_folder.reset(token)


@functools.cache
def _engine_signature(engine: str) -> str:
    """The engine and its package's version: a reading kept by another version of the OCR is not reused."""
    from importlib.metadata import PackageNotFoundError, version

    package = "rapidocr" if engine == "ppocrv5" else "rapidocr_onnxruntime"
    try:
        return f"{engine}-{version(package)}"
    except PackageNotFoundError:
        return engine


def _prune(folder: Path) -> None:
    """At most ``OCR_CACHE_MAX_FILES`` readings, the oldest removed first; looked at once an hour."""
    import time

    now = time.monotonic()
    if now - _pruned.get(folder, -1e9) < 3600:
        return
    _pruned[folder] = now
    try:
        files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for path in files[: max(0, len(files) - OCR_CACHE_MAX_FILES)]:
        path.unlink(missing_ok=True)


def _ocr_cached(png_bytes: bytes, run: Any, engine: str | None = None) -> Any:
    """The OCR engine's raw result, kept on disk by image hash: in ``AP_OCR_CACHE`` when it names a folder (for
    benchmark iterations: the reader changes, the scans do not), else in the folder ``ocr_cache`` set, if any."""
    env = os.environ.get("AP_OCR_CACHE")
    folder = Path(env) if env else _ocr_cache_folder.get()
    if folder is None:
        return run()
    import hashlib
    import json

    engine = engine or _engine_name()
    if env:  # the benchmark's own cache, as it always was
        tag = "" if engine == "rapidocr" else f"-{engine}"
    else:
        tag = f"-{_engine_signature(engine)}"
        _prune(folder)
    path = folder / f"{hashlib.sha256(png_bytes).hexdigest()}{tag}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    result = run()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_text(json.dumps([[[list(map(float, p)) for p in q], t, float(c)] for q, t, c in result or []]),
                        encoding="utf-8")  # fmt: skip
        part.replace(path)  # whole or not at all: a reading cut short is never read back
    except OSError:
        pass
    return result


def ocr_image(png_bytes: bytes, number: int, engine: str | None = None) -> PageLayout:
    """OCR one page image. Each OCR line becomes words (split on spaces, boxes shared by length)."""
    import io

    import numpy as np
    from PIL import Image

    picture = Image.open(io.BytesIO(png_bytes))
    stated = _stated_dpi(picture)
    img = _on_paper(picture)
    width, height = img.size
    result = _ocr_cached(png_bytes, lambda: _engine(engine)(np.asarray(img)), engine)
    turn = _upright_turn(result or [], width, height)
    if turn:
        result = _turned(result, turn, width, height)
        if turn != 180:
            width, height = height, width
    words: list[Word] = []
    for quad, text, score in result or []:
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        text = _OCR_PERCENT.sub("%", str(text))
        words.extend(_split_ocr_line(text, x0, y0, x1, y1, float(score), width, height, number))
    page = PageLayout(number, float(width), float(height), words, group_lines(words, number))
    page.skew = _skew(result or [], width, height)
    page.turn = turn
    page.dpi = stated or round(max(width, height) / 11, 1)  # else a letter page's long side
    return page


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
        if min(wide, high) < 300 or across <= 0 or down <= 0 or across * down * 72 * 72 < 0.25 * area:
            continue  # a small picture (a stretched background), not the scan
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


def _upright_turn(result: Any, width: float, height: float) -> int:
    """The clockwise quarter turn that sets a page scanned turned upright: 90 or 270 when most of its long lines run
    down the page (whichever puts the amounts right of their labels), 180 when they run across but the amounts
    stand left of their labels; else 0. The OCR reads each line's words right (it turns the line to read it):
    only where the lines are on the page is turned."""
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
    return 180 if _label_side(result) <= -2 else 0  # an upright page never scores below 0 (1,300 benchmark scans)


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
    return slope * width / max(height, 1.0) if abs(slope) <= 0.07 else 0.0  # within 4 degrees: a tilt, not a turn


def _split_ocr_line(text: str, x0: float, y0: float, x1: float, y1: float, score: float, width: float,
                    height: float, number: int) -> list[Word]:  # fmt: skip
    parts = text.split()
    if not parts:
        return []
    total = sum(len(p) for p in parts) + (len(parts) - 1)
    out = []
    pos = 0
    for p in parts:
        a = x0 + (x1 - x0) * pos / total
        b = x0 + (x1 - x0) * (pos + len(p)) / total
        out.append(Word(p, Box(number, a / width, y0 / height, b / width, y1 / height), score, "ocr"))
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


def _image_pngs(path: Path, max_pages: int) -> list[bytes]:
    """Each page of an image file as PNG: a PNG as it is, every frame of a multi-page TIFF, at the
    resolution it was scanned at (at most ``OCR_MAX_SIDE``), turned upright as its EXIF says."""
    if path.suffix.lower() == ".png":
        return [path.read_bytes()]
    from PIL import ImageOps, ImageSequence

    out = []
    with open_image(path) as img:
        for i, frame in enumerate(ImageSequence.Iterator(img)):
            if i >= max_pages:
                break
            upright = ImageOps.exif_transpose(frame.copy())
            out.append(_picture_png(flatten_photo(upright) or upright))
    return out


def _picture_png(picture: Any) -> bytes:
    """A page's picture (upright) as OCR reads it: on white paper, its long side at most ``OCR_MAX_SIDE``, PNG."""
    import io

    from PIL import Image

    pic = _on_paper(picture)
    scale = OCR_MAX_SIDE / max(pic.width, pic.height, 1)
    if scale < 1:
        pic = pic.resize((max(1, round(pic.width * scale)), max(1, round(pic.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    pic.save(buf, "PNG")
    return buf.getvalue()


_SCAN_PNGS: dict[tuple[Any, ...], bytes] = {}  # the last few pages' images: both OCR engines read the same one
_SCAN_PNGS_KEPT = 4
_SCAN_PNGS_LOCK = threading.Lock()  # the dashboard's page reader reads in a thread of its own


def _scan_png(doc: Any, page: Any) -> bytes:
    """The image OCR reads for a PDF page without text: for a PDF AP Coder made from a photo or a scan
    (``intake.py``), that picture's own pixels, as OCR read the picture before it was converted; any other page
    rendered at ``OCR_DPI``. Made once for both OCR engines (a page's PNG takes about a second)."""
    try:
        st = os.stat(doc.name)
        key: tuple[Any, ...] | None = (doc.name, st.st_mtime_ns, st.st_size, page.number, page.rotation)
    except (OSError, TypeError, AttributeError):  # a document only in memory: made each time
        key = None
    with _SCAN_PNGS_LOCK:
        png = _SCAN_PNGS.get(key) if key else None
    if png is None:
        png = _make_scan_png(doc, page)
        if key:
            with _SCAN_PNGS_LOCK:
                _SCAN_PNGS[key] = png
                while len(_SCAN_PNGS) > _SCAN_PNGS_KEPT:
                    _SCAN_PNGS.pop(next(iter(_SCAN_PNGS)))
    return png


def _make_scan_png(doc: Any, page: Any) -> bytes:
    from ..intake import made_from_picture, page_picture

    if made_from_picture(doc):
        picture = page_picture(doc, page)
        if picture is not None:
            return _picture_png(picture)
    return _page_png(page)


def _page_png(page: Any, dpi: int = OCR_DPI) -> bytes:
    zoom = dpi / 72
    longest = max(page.rect.width, page.rect.height) * zoom
    if longest > OCR_MAX_SIDE:
        zoom *= OCR_MAX_SIDE / longest
    import pymupdf

    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


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


def build_layout(path: str | Path, *, di_raw: dict[str, Any] | None = None, ocr: str | bool = "auto",
                 max_pages: int = 20) -> DocLayout:  # fmt: skip
    """The words of ``path``. ``ocr``: "auto" (scanned pages only), True (force OCR on every page), False (never).

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
        return DocLayout([ocr_image(png, i) for i, png in enumerate(_image_pngs(path, max_pages), 1)], "ocr")

    import pymupdf

    pages: list[PageLayout] = []
    sources: set[str] = set()
    failed = 0  # pages OCR could not read: kept empty, and counted as not read (``DocLayout.pages_unread_locally``)
    di = layout_from_di(di_raw) if di_raw else None
    with pymupdf.open(path) as doc:
        left_out = max(0, doc.page_count - max_pages)  # never read: said so (``DocLayout.pages_left_out``)
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            number = i + 1
            text_page = _text_layer_page(page, number)
            if len(text_page.words) >= MIN_TEXT_WORDS and ocr is not True:
                pages.append(text_page)
                sources.add("text")
                continue
            di_page = next((p for p in di.pages if p.number == number), None) if di else None
            if di_page and di_page.words:
                pages.append(di_page)
                sources.add("di")
            elif use_ocr:
                try:
                    scanned = ocr_image(_scan_png(doc, page), number)
                    scanned.dpi = _native_dpi(page) or scanned.dpi  # the page is rendered finer than it was scanned
                    pages.append(scanned)
                    sources.add("ocr")
                except Exception as exc:  # a page OCR cannot read is kept empty and counted unread, never fatal
                    log.warning("%s page %d: OCR failed (%s)", path.name, number, exc)
                    pages.append(text_page)
                    failed += 1
            elif ocr == "auto" and _looks_scanned(page):
                raise CannotRead(NO_OCR)
            else:
                pages.append(text_page)
                sources.add("text")
    source = sources.pop() if len(sources) == 1 else ("mixed" if sources else "none")
    return DocLayout(pages, source, pages_left_out=left_out, pages_unread_locally=failed)


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


def _prepared(png_bytes: bytes) -> bytes:
    """The page straightened and enlarged 1.5x: OCR reads small, tilted print better."""
    import io

    from PIL import Image, ImageFilter

    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    angle = _deskew_angle(img)
    if abs(angle) >= 0.2:
        img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=255, expand=False)
    img = img.resize((int(img.width * 1.5), int(img.height * 1.5)), Image.LANCZOS).filter(ImageFilter.SHARPEN)
    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG")
    return out.getvalue()


def _second_image(png_bytes: bytes) -> bytes:
    """The page as the second read sees it: as scanned for another model (its own view of the same
    pixels), straightened and enlarged when it is the same model again."""
    return png_bytes if second_engine_name() != _engine_name() else _prepared(png_bytes)


def second_read_layout(path: str | Path, max_pages: int = 20) -> DocLayout | None:
    """An independent second OCR of a scanned invoice, from a straightened, enlarged copy of each page.
    Its boxes are fractions of the page like the first read's (the straightening moves them by at most
    a few thousandths). None for a text PDF or when OCR is not installed."""
    path = Path(path)
    if not ocr_available():
        return None
    import pymupdf

    pages: list[PageLayout] = []
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        for i, png in enumerate(_image_pngs(path, max_pages), 1):
            pages.append(ocr_image(_second_image(png), i, second_engine_name()))
    else:
        with pymupdf.open(path) as doc:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                if len(_text_layer_page(page, i + 1).words) >= MIN_TEXT_WORDS:
                    return None  # a text PDF: the text layer is exact
                pages.append(ocr_image(_second_image(_scan_png(doc, page)), i + 1, second_engine_name()))
    return DocLayout(pages, "ocr")
