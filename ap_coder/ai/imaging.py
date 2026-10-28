"""Page images for a chat model that can see (``render_page_images``): the AI reviewer's pages, the chat model's own
reading of the header, Azure OpenAI's vision mode and the training export.

A PDF's pages are rendered at 150 dpi (PyMuPDF), a PNG or JPEG is sent as it is, a TIFF, BMP or iPhone photo
(HEIC/HEIF, with pillow-heif) frame by frame as PNG (Pillow); each at most ``MAX_PIXELS``, a larger one shrunk to it.
The page reader renders its own (``page_reader``).
"""

from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass
from pathlib import Path

_PASSTHROUGH = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_PDF_DPI = 150
# The most pixels a page image is sent with. Gemma 4 makes a token of each 48 x 48 pixels and must take a whole picture
# in one batch, 512 tokens in LM Studio unless set otherwise: a letter page at 150 dpi (about 925 tokens) stopped its
# model server (B-1414). A million pixels is about 440 tokens (a letter page at 118 dpi). Azure OpenAI shrinks a page
# to about this size itself; Qwen 3.5 reads it as about 1,300 tokens.
MAX_PIXELS = 1_000_000


@dataclass(frozen=True)
class PageImage:
    page_number: int
    mime_type: str
    data: bytes

    def to_data_url(self) -> str:
        return f"data:{self.mime_type};base64,{base64.b64encode(self.data).decode('ascii')}"


def render_page_images(path: str | Path, max_pages: int = 5, max_pixels: int | None = MAX_PIXELS) -> list[PageImage]:
    """The file's first ``max_pages`` pages, each at most ``max_pixels`` (None: as rendered, for the training export,
    which sizes them itself). [] for a file without pages (a text file)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in _PASSTHROUGH:
        return [_picture(path, _PASSTHROUGH[suffix], max_pixels)]
    if suffix == ".pdf":
        return _render_pdf(path, max_pages, max_pixels)
    if suffix in {".tif", ".tiff", ".bmp", ".heif", ".heic"}:
        return _render_with_pillow(path, max_pages, max_pixels)
    return []


def _shrunk(img, max_pixels: int | None):
    """``img`` at most ``max_pixels``, its shape kept (the image itself when it fits)."""
    from PIL import Image

    width, height = img.size
    if not max_pixels or width * height <= max_pixels:
        return img
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    scale = math.sqrt(max_pixels / (width * height))
    size = (max(1, math.floor(width * scale)), max(1, math.floor(height * scale)))
    return img.resize(size, Image.Resampling.LANCZOS)  # small print stays legible


def _picture(path: Path, mime_type: str, max_pixels: int | None) -> PageImage:
    """A PNG or JPEG as it is, or shrunk to ``max_pixels`` and turned the right way up (a phone photo's EXIF
    orientation). One Pillow can't open is sent as it is: the model server says what it makes of it."""
    data = path.read_bytes()
    if not max_pixels:
        return PageImage(1, mime_type, data)
    from PIL import ImageOps

    from ..capture.layout import open_image

    try:
        with open_image(path) as img:
            if img.width * img.height <= max_pixels:
                return PageImage(1, mime_type, data)
            small = _shrunk(ImageOps.exif_transpose(img), max_pixels)
            buf = io.BytesIO()
            small.save(buf, format="JPEG" if mime_type == "image/jpeg" else "PNG", quality=90)
    except Exception:  # noqa: BLE001 - not a picture Pillow reads: as it was before pictures were shrunk
        return PageImage(1, mime_type, data)
    return PageImage(1, mime_type, buf.getvalue())


def _pdf_zoom(width: float, height: float, max_pixels: int | None) -> float:
    """The zoom a ``width`` x ``height`` point page is rendered at: 150 dpi, or less for at most ``max_pixels``.
    PyMuPDF rounds each side up, so (width * zoom + 1) * (height * zoom + 1) <= max_pixels."""
    zoom = _PDF_DPI / 72
    if not max_pixels or (width * zoom + 1) * (height * zoom + 1) <= max_pixels:
        return zoom
    area, edges = width * height, width + height
    return (math.sqrt(edges * edges + 4 * area * (max_pixels - 1)) - edges) / (2 * area)


def _render_pdf(path: Path, max_pages: int, max_pixels: int | None) -> list[PageImage]:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError("Vision mode for PDFs requires PyMuPDF: pip install pymupdf") from exc

    images = []
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            if index >= max_pages:
                break
            zoom = _pdf_zoom(page.rect.width, page.rect.height, max_pixels)
            pix = (
                page.get_pixmap(dpi=_PDF_DPI)
                if zoom == _PDF_DPI / 72
                else page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            )
            images.append(PageImage(index + 1, "image/png", pix.tobytes("png")))
    return images


def _render_with_pillow(path: Path, max_pages: int, max_pixels: int | None) -> list[PageImage]:
    try:
        from PIL import ImageSequence
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError("Vision mode for TIFF/BMP requires Pillow: pip install pillow") from exc

    from ..capture.layout import open_image

    images = []
    with open_image(path) as img:  # HEIC/HEIF photos too, with pillow-heif
        for index, frame in enumerate(ImageSequence.Iterator(img)):
            if index >= max_pages:
                break
            buf = io.BytesIO()
            _shrunk(frame.convert("RGB"), max_pixels).save(buf, format="PNG")
            images.append(PageImage(index + 1, "image/png", buf.getvalue()))
    return images
