"""The invoice page viewer for the review screen: the page with a box over every field read from it.

A bidirectional Streamlit component with no build step (``components/invoice_viewer/``): plain HTML, CSS and
JavaScript that speak Streamlit's component protocol themselves.

    pages = render_pages(path)
    event = invoice_viewer(pages, fields, words=page_words(path), key="viewer")
    if event and event["new"] and event["type"] == "assign":
        ...

Coordinates are fractions of the page (0..1, origin top-left), pages numbered from 1, as in
``ap_coder.capture.types``. ``pages[i]["width"]`` and ``["height"]`` are the page's printed size in points
(1/72 inch), whatever resolution the picture was drawn at, so the viewer's 100% is the page's real size.
"""

from __future__ import annotations

import base64
import functools
import io
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ap_coder.capture.types import MISSING, STATUSES, Box, FieldResult, LineReading

COMPONENT_DIR = Path(__file__).resolve().parent / "components" / "invoice_viewer"

# The order of the field panel: as an invoice reads, header first, then the money, then the terms.
FIELD_ORDER = (
    "vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "gst_hst_registration_number",
    "qst_registration_number", "subtotal", "gst_amount", "hst_amount", "pst_amount", "qst_amount", "tax_total",
    "grand_total", "currency", "payment_terms",
)  # fmt: skip
FIELD_LABELS = {
    "vendor_name": "Supplier", "invoice_number": "Invoice number", "invoice_date": "Invoice date",
    "due_date": "Due date", "po_number": "PO number", "currency": "Currency",
    "gst_hst_registration_number": "GST/HST number", "qst_registration_number": "QST number",
    "subtotal": "Subtotal", "gst_amount": "GST", "hst_amount": "HST", "pst_amount": "PST", "qst_amount": "QST",
    "tax_total": "Total tax", "grand_total": "Total", "payment_terms": "Terms",
}  # fmt: skip

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic", ".heif"}
MAX_SIDE = 2400  # pixels: a page is never drawn bigger than this, whatever the dpi asked for
SCREEN_DPI = 96


# --- Page pictures ----------------------------------------------------------------------------------------


def render_pages(path: str | Path, dpi: int = 110, max_pages: int = 10) -> list[dict[str, Any]]:
    """The first ``max_pages`` pages of a PDF or image as ``{"src": PNG data URI, "width", "height"}``.

    ``width``/``height`` are the printed page size in points. Cached on the file's path, size and time, so
    a page is drawn once however often the review screen reruns. Unknown file types give ``[]``.
    """
    p = Path(path).resolve()
    stat = p.stat()  # FileNotFoundError for a file that is not there
    pages = _render_cached(str(p), stat.st_mtime_ns, stat.st_size, int(dpi), int(max_pages))
    return [dict(page) for page in pages]


@functools.lru_cache(maxsize=24)
def _render_cached(path: str, _mtime: int, _size: int, dpi: int, max_pages: int) -> tuple[dict[str, Any], ...]:
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return tuple(_render_pdf(path, dpi, max_pages))
    if suffix in IMAGE_SUFFIXES:
        return tuple(_render_image(path, dpi, max_pages))
    return ()


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _render_pdf(path: str, dpi: int, max_pages: int) -> list[dict[str, Any]]:
    import pymupdf

    out = []
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            if index >= max_pages:
                break
            rect = page.rect  # points, after the page's own rotation
            scale = min(dpi / 72, MAX_SIDE / max(rect.width, rect.height, 1))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            out.append({"src": _data_uri(pix.tobytes("png")), "width": round(rect.width, 2),
                        "height": round(rect.height, 2)})  # fmt: skip
    return out


def _render_image(path: str, dpi: int, max_pages: int) -> list[dict[str, Any]]:
    from PIL import Image, ImageOps, ImageSequence

    from ap_coder.capture.layout import open_image

    out = []
    with open_image(path) as img:  # an iPhone's HEIC/HEIF photo too, with pillow-heif
        for index, frame in enumerate(ImageSequence.Iterator(img)):
            if index >= max_pages:
                break
            source_dpi = _image_dpi(frame)
            pic = ImageOps.exif_transpose(frame.copy())
            if pic.mode in ("RGBA", "LA", "P"):
                pic = pic.convert("RGBA")
                paper = Image.new("RGB", pic.size, "white")
                paper.paste(pic, mask=pic.getchannel("A"))
                pic = paper
            else:
                pic = pic.convert("RGB")
            width_pt, height_pt = pic.width * 72 / source_dpi, pic.height * 72 / source_dpi
            scale = min(1.0, dpi / source_dpi, MAX_SIDE / max(pic.width, pic.height, 1))
            if scale < 1:
                pic = pic.resize((max(1, round(pic.width * scale)), max(1, round(pic.height * scale))),
                                 Image.Resampling.LANCZOS)  # fmt: skip
            buf = io.BytesIO()
            pic.save(buf, format="PNG", optimize=True)
            out.append({"src": _data_uri(buf.getvalue()), "width": round(width_pt, 2), "height": round(height_pt, 2)})
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


def page_words(path: str | Path, max_pages: int = 10) -> list[list[dict[str, Any]]]:
    """Words on each page of a PDF's text layer, as the viewer's teach mode wants them: ``{"t", "b"}``."""
    p = Path(path)
    if p.suffix.lower() != ".pdf":
        return []
    import pymupdf

    out: list[list[dict[str, Any]]] = []
    with pymupdf.open(p) as doc:
        for index, page in enumerate(doc):
            if index >= max_pages:
                break
            w, h = page.rect.width or 1, page.rect.height or 1
            matrix = page.rotation_matrix
            words = []
            for x0, y0, x1, y1, text, *_ in page.get_text("words", sort=True):
                r = pymupdf.Rect(x0, y0, x1, y1) * matrix
                words.append({"t": text, "b": [round(r.x0 / w, 5), round(r.y0 / h, 5), round(r.x1 / w, 5),
                                               round(r.y1 / h, 5)]})  # fmt: skip
            out.append(words)
    return out


# --- Arguments --------------------------------------------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _box(value: Any) -> list[float] | None:
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


def _boxes(values: Any) -> list[list[float]]:
    return [b for b in (_box(v) for v in values or []) if b is not None]


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


def shape_field(item: FieldResult | Mapping[str, Any]) -> dict[str, Any]:
    """One row of the field panel, from ``FieldResult.to_dict()`` (+ ``label``/``display``) or a FieldResult."""
    d = item.to_dict() if isinstance(item, FieldResult) else dict(item)
    name = str(d.get("field") or "")
    status = d.get("status") if d.get("status") in STATUSES else MISSING
    boxes = _boxes(d.get("boxes"))
    if status != MISSING and d.get("value") is None and not boxes:
        status = MISSING
    display = d.get("display")
    return {
        "field": name,
        "label": str(d.get("label") or FIELD_LABELS.get(name) or name.replace("_", " ").capitalize()),
        "display": _display(d.get("value")) if display is None else str(display),
        "value": _plain(d.get("value")),
        "status": status,
        "confidence": round(min(1.0, max(0.0, _num(d.get("confidence")))), 4),
        "boxes": [] if status == MISSING else boxes,
        "sources": {str(k): _plain(v) for k, v in (d.get("sources") or {}).items()},
        "reasons": [str(r) for r in d.get("reasons") or []],
        "failed": bool(d.get("failed")),
    }


def shape_fields(fields: Iterable[FieldResult | Mapping[str, Any]] | Mapping[str, Any]) -> list[dict[str, Any]]:
    """Rows for the panel, in invoice order (unknown fields after, as given); one row per field name."""
    items = fields.values() if isinstance(fields, Mapping) else fields
    rows: dict[str, dict[str, Any]] = {}
    for item in items:
        row = shape_field(item)
        if row["field"]:
            rows[row["field"]] = row
    rank = {name: i for i, name in enumerate(FIELD_ORDER)}
    order = list(rows)
    return sorted(rows.values(), key=lambda r: (rank.get(r["field"], len(rank)), order.index(r["field"])))


def shape_words(words: Iterable[Iterable[Any]] | None) -> list[list[dict[str, Any]]]:
    """Per page ``[{"t", "b": [x0, y0, x1, y1]}]``; also takes ``capture.types.Word`` objects."""
    out = []
    for page in words or []:
        row = []
        for w in page or []:
            if hasattr(w, "box") and hasattr(w, "text"):
                text, b = w.text, [w.box.x0, w.box.y0, w.box.x1, w.box.y1]
            elif isinstance(w, Mapping):
                text, b = w.get("t", ""), w.get("b")
            else:
                continue
            box = _box([1, *b]) if isinstance(b, list | tuple) and len(b) == 4 else None
            if box and str(text).strip():
                row.append({"t": str(text), "b": box[1:]})
        out.append(row)
    return out


def shape_line_items(items: Iterable[LineReading | Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for i, item in enumerate(items or [], start=1):
        if isinstance(item, LineReading):
            label, boxes = item.description, item.boxes[:1]  # the row box comes first
        else:
            label, boxes = item.get("label") or item.get("description"), item.get("boxes")
        shaped = _boxes(boxes)
        if shaped:
            out.append({"label": str(label or f"Line {i}"), "boxes": shaped})
    return out


def shape_pages(pages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for page in pages:
        src = str(page.get("src") or "")
        if not src.startswith("data:image/"):
            raise ValueError("each page needs a data:image/... URI in 'src' (see render_pages)")
        out.append({"src": src, "width": max(1.0, _num(page.get("width"), 612)),
                    "height": max(1.0, _num(page.get("height"), 792))})  # fmt: skip
    return out


def viewer_args(pages, fields, *, words=None, line_items=None, selected=None, teach=None, height=None) -> dict:
    """The arguments sent to the page (all JSON), checked and put in order."""
    if height is not None and int(height) < 240:
        raise ValueError("height must be at least 240 pixels, or None to fit the page")
    return {
        "pages": shape_pages(pages),
        "fields": shape_fields(fields),
        "words": shape_words(words),
        "line_items": shape_line_items(line_items),
        "selected": selected or None,
        "teach": teach or None,
        "height": int(height) if height is not None else None,
    }


# --- The component ----------------------------------------------------------------------------------------


def _component():
    import streamlit.components.v1 as components

    # Declared on each call: Streamlit only registers a component during a script run, and an identical
    # declaration replaces the last one quietly.
    return components.declare_component("invoice_viewer", path=str(COMPONENT_DIR))


def invoice_viewer(
    pages: list[dict[str, Any]],
    fields: Iterable[FieldResult | Mapping[str, Any]] | Mapping[str, Any],
    *,
    words: list[list[dict[str, Any]]] | None = None,
    line_items: list[Mapping[str, Any]] | None = None,
    selected: str | None = None,
    teach: str | None = None,
    height: int | None = None,
    key: str | None = None,
) -> dict[str, Any] | None:
    """Show the invoice pages with the fields' boxes; return the viewer's last event, or None before the first.

    Events: ``{"type": "select", "field"}``, ``{"type": "assign", "field", "text", "boxes": [[page, x0, y0, x1,
    y1], ...]}`` (teach mode: the words picked, in reading order) and ``{"type": "teach_cancel", "field"}``.
    Each also has ``seq`` (counts up) and ``new``: True only on the first rerun that returns it, so an action
    is taken once. ``selected`` highlights a field when it changes; ``teach`` (a field name) starts teach mode.
    ``height`` fixes the viewer's height (the page scrolls inside); None grows it to fit the page.
    """
    import streamlit as st

    key = key or "invoice_viewer"
    args = viewer_args(pages, fields, words=words, line_items=line_items, selected=selected, teach=teach,
                       height=height)  # fmt: skip
    event = _component()(**args, key=key, default=None)
    if not isinstance(event, dict) or event.get("type") not in {"select", "assign", "teach_cancel"}:
        return None
    event = dict(event)
    seen_key = f"_invoice_viewer_seen_{key}"
    seq = event.get("seq")
    event["new"] = seq is not None and st.session_state.get(seen_key) != seq
    st.session_state[seen_key] = seq
    return event
