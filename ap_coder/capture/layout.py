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

import logging
import os
import statistics
from pathlib import Path
from typing import Any

from .types import Box, DocLayout, Line, PageLayout, Word

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
MIN_TEXT_WORDS = 5  # fewer words than this on a page means it is a scan
OCR_DPI = 200
OCR_MAX_SIDE = 2400


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
        text = text.strip()
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

_OCR_ENGINE: Any = None


def ocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def _engine() -> Any:
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _ocr_cached(png_bytes: bytes, run: Any) -> Any:
    """The OCR engine's raw result, kept on disk by image hash when ``AP_OCR_CACHE`` names a folder
    (for benchmark iterations: the reader changes, the scans do not)."""
    folder = os.environ.get("AP_OCR_CACHE")
    if not folder:
        return run()
    import hashlib
    import json

    path = Path(folder) / f"{hashlib.sha256(png_bytes).hexdigest()}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    result = run()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([[[list(map(float, p)) for p in q], t, float(c)] for q, t, c in result or []]),
                        encoding="utf-8")  # fmt: skip
    except OSError:
        pass
    return result


def ocr_image(png_bytes: bytes, number: int) -> PageLayout:
    """OCR one page image. Each OCR line becomes words (split on spaces, boxes shared by length)."""
    import io

    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    width, height = img.size
    result = _ocr_cached(png_bytes, lambda: _engine()(np.asarray(img))[0])
    words: list[Word] = []
    for quad, text, score in result or []:
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        words.extend(_split_ocr_line(str(text), x0, y0, x1, y1, float(score), width, height, number))
    return PageLayout(number, float(width), float(height), words, group_lines(words, number))


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


def build_layout(path: str | Path, *, di_raw: dict[str, Any] | None = None, ocr: str | bool = "auto",
                 max_pages: int = 20) -> DocLayout:  # fmt: skip
    """The words of ``path``. ``ocr``: "auto" (scanned pages only, when RapidOCR is installed),
    True (force OCR on every page), False (never)."""
    path = Path(path)
    suffix = path.suffix.lower()
    use_ocr = ocr is True or (ocr == "auto" and ocr_available())
    if suffix in IMAGE_EXTENSIONS:
        if di_raw:
            di = layout_from_di(di_raw)
            if di:
                return di
        if not use_ocr:
            return DocLayout([], "none")
        import pymupdf

        with pymupdf.open(path) as img_doc:
            png = img_doc[0].get_pixmap().tobytes("png") if suffix not in (".png",) else path.read_bytes()
        return DocLayout([ocr_image(png, 1)], "ocr")

    import pymupdf

    pages: list[PageLayout] = []
    sources: set[str] = set()
    di = layout_from_di(di_raw) if di_raw else None
    with pymupdf.open(path) as doc:
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
                    pages.append(ocr_image(_page_png(page), number))
                    sources.add("ocr")
                except Exception as exc:  # a page OCR cannot read is reported as empty, never fatal
                    log.warning("%s page %d: OCR failed (%s)", path.name, number, exc)
                    pages.append(text_page)
            else:
                pages.append(text_page)
                sources.add("text")
    source = sources.pop() if len(sources) == 1 else ("mixed" if sources else "none")
    return DocLayout(pages, source)
