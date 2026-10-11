"""The invoice's pages for the review screen: their printed size, a PNG of each page, and the words on them.

The page viewer (``static/js/invoice_viewer.js``) draws ``GET /review/<id>/page/<n>.png`` and puts the field boxes
over it; boxes and words are fractions of the page (0..1, origin top-left), pages numbered from 1, as in
``ap_coder.capture.types``. A page's ``width`` / ``height`` are its printed size in points (1/72 inch), so the
viewer's 100% is the page's real size whatever resolution the picture was drawn at.

Pictures are cached on the file's path, size and time: a page is drawn once however often it is asked for.
"""

from __future__ import annotations

import functools
import io
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ap_coder.capture.types import MISSING, STATUSES, Box, FieldResult, LineReading

PDF = ".pdf"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic", ".heif"}
VIEWABLE = {PDF, *IMAGE_SUFFIXES}
MAX_PAGES = 20  # pages shown; a longer file still shows its first pages
DPI = 120  # sharp on a laptop screen at "Fit", still small
MAX_SIDE = 2400  # pixels: a page is never drawn bigger than this, whatever the dpi
SCREEN_DPI = 96

# The order of the field list: as an invoice reads, header first, then the money, then the terms.
FIELD_ORDER = (
    "vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "gst_hst_registration_number",
    "qst_registration_number", "subtotal", "gst_amount", "hst_amount", "pst_amount", "qst_amount", "tax_total",
    "grand_total", "currency", "payment_terms",
)  # fmt: skip


def _stamp(path: Path) -> tuple[str, int, int]:
    p = path.resolve()
    stat = p.stat()  # FileNotFoundError for a file that is not there
    return str(p), stat.st_mtime_ns, stat.st_size


def version(path: str | Path) -> str:
    """Changes when the file does (for the picture's address, so a browser never shows an old page)."""
    try:
        _, mtime, size = _stamp(Path(path))
    except OSError:
        return "0"
    return f"{mtime:x}{size:x}"


def page_sizes(path: str | Path) -> list[tuple[float, float]]:
    """The printed size of each page (points), up to ``MAX_PAGES``; [] for a file that can't be shown."""
    p = Path(path)
    if p.suffix.lower() not in VIEWABLE:
        return []
    return list(_sizes_cached(*_stamp(p)))


@functools.lru_cache(maxsize=64)
def _sizes_cached(path: str, _mtime: int, _size: int) -> tuple[tuple[float, float], ...]:
    if Path(path).suffix.lower() == PDF:
        import pymupdf

        with pymupdf.open(path) as doc:
            return tuple(
                (round(page.rect.width, 2), round(page.rect.height, 2))
                for index, page in enumerate(doc)
                if index < MAX_PAGES
            )
    return tuple((w, h) for w, h, _ in _image_pages(path, sizes_only=True))


def page_png(path: str | Path, number: int) -> bytes:
    """Page ``number`` (from 1) as PNG bytes. IndexError for a page the file doesn't have."""
    p = Path(path)
    if p.suffix.lower() not in VIEWABLE:
        raise IndexError(number)
    if not 1 <= number <= MAX_PAGES:
        raise IndexError(number)
    return _png_cached(*_stamp(p), int(number))


@functools.lru_cache(maxsize=32)
def _png_cached(path: str, _mtime: int, _size: int, number: int) -> bytes:
    if Path(path).suffix.lower() == PDF:
        import pymupdf

        with pymupdf.open(path) as doc:
            if number > doc.page_count:
                raise IndexError(number)
            page = doc[number - 1]
            rect = page.rect  # points, after the page's own rotation
            scale = min(DPI / 72, MAX_SIDE / max(rect.width, rect.height, 1))
            return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
    for index, (_, _, png) in enumerate(_image_pages(path), start=1):
        if index == number:
            return png
    raise IndexError(number)


def _image_pages(path: str, sizes_only: bool = False) -> list[tuple[float, float, bytes]]:
    """(width pt, height pt, PNG) per frame of a picture (a TIFF can hold several pages)."""
    from PIL import Image, ImageOps, ImageSequence

    from ap_coder.capture.layout import open_image

    out = []
    with open_image(path) as img:  # an iPhone's HEIC/HEIF photo too, with pillow-heif
        for index, frame in enumerate(ImageSequence.Iterator(img)):
            if index >= MAX_PAGES:
                break
            source_dpi = _image_dpi(frame)
            pic = ImageOps.exif_transpose(frame.copy())
            width_pt, height_pt = round(pic.width * 72 / source_dpi, 2), round(pic.height * 72 / source_dpi, 2)
            if sizes_only:
                out.append((width_pt, height_pt, b""))
                continue
            if pic.mode in ("RGBA", "LA", "P"):
                pic = pic.convert("RGBA")
                paper = Image.new("RGB", pic.size, "white")
                paper.paste(pic, mask=pic.getchannel("A"))
                pic = paper
            else:
                pic = pic.convert("RGB")
            scale = min(1.0, DPI / source_dpi, MAX_SIDE / max(pic.width, pic.height, 1))
            if scale < 1:
                pic = pic.resize((max(1, round(pic.width * scale)), max(1, round(pic.height * scale))),
                                 Image.Resampling.LANCZOS)  # fmt: skip
            buf = io.BytesIO()
            pic.save(buf, format="PNG", optimize=True)
            out.append((width_pt, height_pt, buf.getvalue()))
    return out


def _image_dpi(img: Any) -> float:
    """The resolution a scan says it was made at; else guess from a letter-width page (never below screen dpi)."""
    dpi = img.info.get("dpi")
    try:
        value = float(dpi[0]) if dpi else 0.0
    except (TypeError, ValueError, IndexError):
        value = 0.0
    if 30 <= value <= 2400 and math.isfinite(value):
        return value
    return max(SCREEN_DPI, img.width / 8.5)


def page_words(path: str | Path) -> list[list[dict[str, Any]]]:
    """The words on each page, for teaching a field by clicking them: ``[[{"t": text, "b": [x0, y0, x1, y1]}]]``.
    A PDF's text layer when it has one, else the words OCR reads (``capture.layout.build_layout``)."""
    p = Path(path)
    words: list[list[dict[str, Any]]] = []
    if p.suffix.lower() == PDF:
        import pymupdf

        with pymupdf.open(p) as doc:
            for index, page in enumerate(doc):
                if index >= MAX_PAGES:
                    break
                w, h = page.rect.width or 1, page.rect.height or 1
                matrix = page.rotation_matrix
                row = []
                for x0, y0, x1, y1, text, *_ in page.get_text("words", sort=True):
                    r = pymupdf.Rect(x0, y0, x1, y1) * matrix
                    row.append({"t": text, "b": [r.x0 / w, r.y0 / h, r.x1 / w, r.y1 / h]})
                words.append(row)
    if not any(words):
        from ap_coder.capture.layout import build_layout

        layout = build_layout(p)
        words = [
            [{"t": w.text, "b": [w.box.x0, w.box.y0, w.box.x1, w.box.y1]} for w in pg.words] for pg in layout.pages
        ]
    return shape_words(words)


# --- What the viewer is given (checked and put in order) ------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def clean_box(value: Any) -> list[float] | None:
    """``[page, x0, y0, x1, y1]`` from a Box or a list, clamped to the page; None when it is not a box."""
    if isinstance(value, Box):
        value = value.to_list()
    if not isinstance(value, list | tuple) or len(value) != 5:
        return None
    page = int(_num(value[0], 0))
    x0, y0, x1, y1 = (min(1.0, max(0.0, _num(v))) for v in value[1:])
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if page < 1 or x1 <= x0 or y1 <= y0:
        return None
    return [page, round(x0, 5), round(y0, 5), round(x1, 5), round(y1, 5)]


def clean_boxes(values: Any) -> list[list[float]]:
    return [b for b in (clean_box(v) for v in (values if isinstance(values, list | tuple) else [])) if b is not None]


def _plain(value: Any) -> Any:
    """JSON-safe copy of a reader's value (sources can hold numbers, strings, dates or small dicts)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_plain(v) for v in value]
    return str(value)


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def shape_field(item: FieldResult | Mapping[str, Any], labels: Mapping[str, str]) -> dict[str, Any]:
    """One row of the viewer's field list, from a ``FieldResult`` (or its ``to_dict()``)."""
    d = item.to_dict() if isinstance(item, FieldResult) else dict(item)
    name = str(d.get("field") or "")
    status = d.get("status") if d.get("status") in STATUSES else MISSING
    boxes = clean_boxes(d.get("boxes"))
    if status != MISSING and d.get("value") is None and not boxes:
        status = MISSING
    failed = any(str(r).startswith("check failed") for r in d.get("reasons") or [])
    return {
        "field": name,
        "label": str(labels.get(name) or name.replace("_", " ").capitalize()),
        "display": _display(d.get("value")),
        "value": _plain(d.get("value")),
        "status": status,
        "confidence": round(min(1.0, max(0.0, _num(d.get("confidence")))), 4),
        "boxes": [] if status == MISSING else boxes,
        "sources": {str(k): _plain(v) for k, v in (d.get("sources") or {}).items()},
        "reasons": [str(r) for r in d.get("reasons") or []],
        "failed": failed,
    }


def shape_fields(fields: Iterable[FieldResult], labels: Mapping[str, str]) -> list[dict[str, Any]]:
    """Rows for the field list, in invoice order (unknown fields after, as given); one row per field name."""
    rows: dict[str, dict[str, Any]] = {}
    for item in fields:
        row = shape_field(item, labels)
        if row["field"]:
            rows[row["field"]] = row
    rank = {name: i for i, name in enumerate(FIELD_ORDER)}
    order = list(rows)
    return sorted(rows.values(), key=lambda r: (rank.get(r["field"], len(rank)), order.index(r["field"])))


def shape_line_items(items: Iterable[LineReading]) -> list[dict[str, Any]]:
    out = []
    for i, item in enumerate(items, start=1):
        boxes = clean_boxes([b.to_list() if isinstance(b, Box) else b for b in item.boxes])
        if boxes:
            out.append({"label": str(item.description or f"Line {i}")[:40], "boxes": boxes})
    return out


def shape_words(words: Iterable[Iterable[Mapping[str, Any]]]) -> list[list[dict[str, Any]]]:
    out = []
    for page in words or []:
        row = []
        for w in page or []:
            b = w.get("b")
            box = clean_box([1, *b]) if isinstance(b, list | tuple) and len(b) == 4 else None
            if box and str(w.get("t", "")).strip():
                row.append({"t": str(w["t"]), "b": box[1:]})
        out.append(row)
    return out
