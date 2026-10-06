"""Page rendering for vision-capable models.

When vision mode is on, page images are sent alongside the Document
Intelligence Markdown so the model can resolve layout ambiguities (stamps,
handwritten approvals, rotated tables). Rendering relies on optional
dependencies: PyMuPDF for PDFs and Pillow for multi-frame TIFF/BMP/HEIF.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

_PASSTHROUGH = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_PDF_DPI = 150


@dataclass(frozen=True)
class PageImage:
    page_number: int
    mime_type: str
    data: bytes

    def to_data_url(self) -> str:
        return f"data:{self.mime_type};base64,{base64.b64encode(self.data).decode('ascii')}"


def render_page_images(path: str | Path, max_pages: int = 5) -> list[PageImage]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in _PASSTHROUGH:
        return [PageImage(1, _PASSTHROUGH[suffix], path.read_bytes())]
    if suffix == ".pdf":
        return _render_pdf(path, max_pages)
    if suffix in {".tif", ".tiff", ".bmp", ".heif", ".heic"}:
        return _render_with_pillow(path, max_pages)
    return []


def _render_pdf(path: Path, max_pages: int) -> list[PageImage]:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError("Vision mode for PDFs requires PyMuPDF: pip install pymupdf") from exc

    images = []
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            if index >= max_pages:
                break
            pix = page.get_pixmap(dpi=_PDF_DPI)
            images.append(PageImage(index + 1, "image/png", pix.tobytes("png")))
    return images


def _render_with_pillow(path: Path, max_pages: int) -> list[PageImage]:
    try:
        from PIL import Image, ImageSequence
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError("Vision mode for TIFF/BMP requires Pillow: pip install pillow") from exc

    images = []
    with Image.open(path) as img:
        for index, frame in enumerate(ImageSequence.Iterator(img)):
            if index >= max_pages:
                break
            buf = io.BytesIO()
            frame.convert("RGB").save(buf, format="PNG")
            images.append(PageImage(index + 1, "image/png", buf.getvalue()))
    return images
