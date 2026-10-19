"""Finding the tables on an invoice page from its picture, with a small layout model that runs on this computer.

The line items of an invoice are read from the words under its line table's column headings. Words printed beside
or between the table's rows (a remittance box, a note, a stamp) used to be read into the rows: a description with
the note's words in it, or an amount taken from beside the table. A layout model trained on document pages finds
the table by how it looks, so the line items are read from the words inside it only (``reader.read_line_items``
with ``tables``).

The model is RapidAI's YOLOv8n "general6" layout model (RapidLayout, Apache-2.0; labels Text, Title, Figure, Table,
Caption, Equation), as CloseDesk uses it: 12 MB, about a quarter of a second a page on a laptop's processor, run
with ONNX Runtime, which the OCR add-on already installs. On CloseDesk's 84 scanned test pages it put 99.9% and 98%
of the table cells inside a table it found, where boxes of OCR words lining up found 47% and 60%; on letters with
no table it found none.

AP Coder is offline: the model comes in the offline bundle's ``models/`` folder with its SHA-256 checked
(``offline.table_finder_path``) and is never downloaded. Without it (or without ONNX Runtime), line items are read
as before, from the whole page; Settings → Reading says so.
"""

from __future__ import annotations

import io
import logging
import threading
from pathlib import Path
from typing import Any

from .types import Box

log = logging.getLogger(__name__)

LABELS = ("Text", "Title", "Figure", "Table", "Caption", "Equation")
TABLE = LABELS.index("Table")
SIDE = 640  # the model looks at the page squeezed to 640 x 640
# A table is kept from this score on (CloseDesk: 0.5 missed a few plain invoices' tables; 0.25 found them and nothing
# more on letters); two found tables overlapping this much (intersection over union) are one.
SURE = 0.25
OVERLAP = 0.5
PICTURE_SIDE = 1280  # the page picture handed to the model (it squeezes it to 640 anyway)
MAX_PAGES = 20

_session: Any = None
_session_path: Path | None = None
_lock = threading.Lock()


def available() -> bool:
    """ONNX Runtime is installed and the model is on this computer, intact."""
    from ..ops.offline import onnxruntime_installed, table_finder_path

    return onnxruntime_installed() and table_finder_path() is not None


def _model() -> Any:
    from ..ops.offline import table_finder_path

    global _session, _session_path
    path = table_finder_path()
    if path is None:
        return None
    with _lock:
        if _session is None or _session_path != path:
            import onnxruntime

            from ..ops.offline import quiet_onnxruntime

            quiet_onnxruntime()
            options = onnxruntime.SessionOptions()
            options.log_severity_level = 3
            options.intra_op_num_threads = 1  # pages are read in parallel workers; one thread each is fastest
            _session = onnxruntime.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
            _session_path = path
        return _session


def find(png: bytes, page: int = 1) -> list[Box] | None:
    """The tables on one page picture (PNG or JPEG bytes), as boxes (fractions of the page), top to bottom; None
    when the model isn't here or couldn't look at the page."""
    try:
        session = _model()
        if session is None:
            return None
        return [box for box, _score in _find(session, png, page)]
    except Exception as exc:  # noqa: BLE001 - a page the model can't look at keeps the whole page
        log.warning("The table finder couldn't look at a page (%s)", exc)
        return None


def _find(session: Any, png: bytes, page: int) -> list[tuple[Box, float]]:
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        squeezed = image.convert("RGB").resize((SIDE, SIDE), Image.BILINEAR)
    pixels = np.asarray(squeezed, dtype=np.float32)[:, :, ::-1] / 255.0  # as the model was run in its project: BGR
    batch = np.ascontiguousarray(pixels.transpose(2, 0, 1)[np.newaxis, ...])
    output = session.run(None, {session.get_inputs()[0].name: batch})[0]
    predictions = np.squeeze(output).T  # one row per candidate: centre x, centre y, width, height, a score per label
    scores = predictions[:, 4:]
    keep = (scores.argmax(axis=1) == TABLE) & (scores[:, TABLE] >= SURE)
    if not keep.any():
        return []
    found = []
    for (cx, cy, w, h), score in zip(predictions[keep, :4] / SIDE, scores[keep, TABLE], strict=True):
        x0, y0 = max(float(cx - w / 2), 0.0), max(float(cy - h / 2), 0.0)
        x1, y1 = min(float(cx + w / 2), 1.0), min(float(cy + h / 2), 1.0)
        if x1 > x0 and y1 > y0:
            found.append((Box(page, x0, y0, x1, y1), float(score)))
    kept: list[tuple[Box, float]] = []
    for box, score in sorted(found, key=lambda item: -item[1]):
        if all(_iou(box, other) < OVERLAP for other, _ in kept):
            kept.append((box, score))
    return sorted(kept, key=lambda item: (item[0].y0, item[0].x0))


def _iou(a: Box, b: Box) -> float:
    w = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
    h = max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))
    shared = w * h
    whole = a.width * a.height + b.width * b.height - shared
    return shared / whole if whole > 0 else 0.0


def page_pictures(path: str | Path, max_pages: int = MAX_PAGES) -> list[bytes]:
    """Each page of the invoice as a small picture for the model (long side ``PICTURE_SIDE``): a PDF's pages rendered
    as displayed (digital PDFs too: the model looks at the page, not its text), a picture's frames upright."""
    path = Path(path)
    out: list[bytes] = []
    from .layout import IMAGE_EXTENSIONS

    if path.suffix.lower() in IMAGE_EXTENSIONS:
        from PIL import Image, ImageOps, ImageSequence

        from .layout import _on_paper, flatten_photo, open_image

        with open_image(path) as img:
            for i, frame in enumerate(ImageSequence.Iterator(img)):
                if i >= max_pages:
                    break
                upright = ImageOps.exif_transpose(frame.copy())
                pic = _on_paper(flatten_photo(upright) or upright)  # the page as OCR read it
                pic.thumbnail((PICTURE_SIDE, PICTURE_SIDE), Image.BILINEAR)
                buf = io.BytesIO()
                pic.save(buf, "PNG")
                out.append(buf.getvalue())
        return out
    import pymupdf

    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            zoom = PICTURE_SIDE / max(page.rect.width, page.rect.height, 1)
            out.append(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png"))
    return out


_found: dict[tuple[str, int, str], dict[int, list[Box]]] = {}  # (file's SHA-256, pages, model): tables, latest few
_found_lock = threading.Lock()
FOUND_KEPT = 32


def find_tables(path: str | Path, max_pages: int = MAX_PAGES) -> dict[int, list[Box]] | None:
    """The tables on each page of the invoice at ``path``: {page number: boxes}, a page with none left out; None when
    the table finder isn't available or the file can't be looked at (line items are then read from whole pages).
    The same file is looked at once: an invoice read again (with its supplier's template, or once the page reader has
    read it) gets the tables found the first time."""
    if not available():
        return None
    import hashlib

    from ..ops.offline import table_finder_path

    try:
        key = (hashlib.sha256(Path(path).read_bytes()).hexdigest(), max_pages, str(table_finder_path()))
    except OSError:
        key = None
    if key is not None:
        with _found_lock:
            if key in _found:
                return {page: list(boxes) for page, boxes in _found[key].items()}
    out = _find_tables(path, max_pages)
    if key is not None and out is not None:
        with _found_lock:
            _found[key] = out
            while len(_found) > FOUND_KEPT:
                _found.pop(next(iter(_found)))
    return None if out is None else {page: list(boxes) for page, boxes in out.items()}


def _find_tables(path: str | Path, max_pages: int) -> dict[int, list[Box]] | None:
    try:
        pictures = page_pictures(path, max_pages)
    except Exception as exc:  # noqa: BLE001 - an extra: never fatal
        log.warning("%s: pages not rendered for the table finder (%s)", Path(path).name, exc)
        return None
    out: dict[int, list[Box]] = {}
    for number, png in enumerate(pictures, 1):
        boxes = find(png, number)
        if boxes is None:
            return None
        if boxes:
            out[number] = boxes
    return out
