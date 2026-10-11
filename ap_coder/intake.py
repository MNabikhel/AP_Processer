"""Every invoice is a PDF: a photo or a scan saved as a picture is turned into one when it comes in.

AP processes PDFs. A JPEG, PNG, iPhone photo (HEIC/HEIF), TIFF (every page of a multi-page TIFF) or BMP that
arrives by upload, in the invoices folder or attached to an email is made into a PDF before it is read
(``InvoicePipeline.process`` calls :func:`to_pdf` first, so every way in is covered): one page a picture, turned
upright as the photo's EXIF says, at the resolution it was taken or scanned at. The invoice is then stored,
read, shown and stamped as that PDF.

Nothing is recompressed that need not be: a JPEG goes into the PDF as it is (a photo taken sideways is turned
by the page's rotation, not by re-encoding it), a PNG, TIFF or BMP losslessly. Only an iPhone photo, which a
PDF can't hold as it is, is re-encoded, as a high-quality JPEG.

The picture itself is kept for the audit trail, in an ``originals`` folder next to the PDF (moved there, so
the invoices folder doesn't offer it again). The PDF records the picture's name, SHA-256 and where it was kept
(:func:`original_of`), so a photo sent twice is found as the same invoice (``Store.find_by_hash``) and the
review screen can point to it.

The OCR of a converted page reads the picture's own pixels (``capture/layout.py``, :func:`page_picture`), the
same as before the conversion, never a re-rendering of the page.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capture.layout import HEIF_EXTENSIONS, IMAGE_EXTENSIONS, open_image

log = logging.getLogger(__name__)

ORIGINALS_FOLDER = "originals"
CREATOR = "AP Coder image to PDF"  # in the PDF's metadata: a page that is a picture AP Coder put there
MAX_FRAMES = 200  # pages taken from one multi-page TIFF
PAGE_LONG_SIDE = 11 * 72  # a picture without a believable resolution becomes a letter-sized page (points)
MIN_DPI, MAX_DPI = 50, 1200  # a resolution outside these is not believed
MAX_PAGE_INCHES = 17  # nor one that makes a page longer than this (a phone's "72 dpi")
HEIF_QUALITY = 95  # an iPhone photo has to be re-encoded: as a JPEG this good
_ROTATION = {3: 180, 6: 90, 8: 270}  # EXIF orientation -> clockwise turn of the page


@dataclass(frozen=True)
class Converted:
    """An image made into a PDF: the PDF, where the image was kept, and the image's SHA-256."""

    pdf: Path
    original: Path
    sha256: str
    pages: int


def is_image(path: str | Path) -> bool:
    """A picture AP Coder turns into a PDF (JPEG, PNG, HEIC/HEIF, TIFF, BMP)."""
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _free_name(folder: Path, name: str, content: bytes | None = None) -> Path:
    """``folder/name``, or ``stem_1``, ``stem_2``... when that name is taken (by other content, when given)."""
    target = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while target.exists() and (content is None or target.read_bytes() != content):
        target = folder / f"{stem}_{n}{suffix}"
        n += 1
    return target


def _page_size(frame: Any, width: int, height: int) -> tuple[float, float]:
    """The page's size in points: from the picture's resolution when it is believable (a scan at 300 dpi becomes
    the letter page it was), else a letter-sized page."""
    dpi = frame.info.get("dpi")
    try:
        dx, dy = float(dpi[0]), float(dpi[1])
    except (TypeError, ValueError, IndexError):
        dx = dy = 0.0
    if MIN_DPI <= dx <= MAX_DPI and MIN_DPI <= dy <= MAX_DPI and max(width / dx, height / dy) <= MAX_PAGE_INCHES:
        return width * 72 / dx, height * 72 / dy
    scale = PAGE_LONG_SIDE / max(width, height, 1)
    return width * scale, height * scale


def _orientation(frame: Any) -> int:
    try:
        return int(frame.getexif().get(0x0112) or 1)
    except Exception:  # noqa: BLE001 - unreadable EXIF: as it is
        return 1


def _flat(picture: Any) -> Any:
    """The picture without transparency (on white paper, as printed); black-and-white and grey pictures stay so."""
    from .capture.layout import _on_paper

    if picture.mode in ("1", "L", "RGB"):
        return picture
    if picture.mode in ("I;16", "I", "F"):  # 16-bit or float greys (some scanners): 8-bit grey
        return picture.convert("L")
    return _on_paper(picture)


def _encoded(frame: Any, source: Path, raw: bytes, single: bool) -> tuple[bytes, int]:
    """(the image data for the page, the page's clockwise turn). A JPEG is kept as it is, turned by the page; a
    mirrored photo, a picture with transparency or any other kind is upright and lossless (PNG); an iPhone photo
    a high-quality JPEG."""
    from PIL import ImageOps

    orientation = _orientation(frame)
    jpeg = frame.format == "JPEG" or (single and raw[:3] == b"\xff\xd8\xff")
    if single and jpeg and frame.mode in ("RGB", "L") and orientation in (1, *_ROTATION):
        return raw, _ROTATION.get(orientation, 0)
    picture = _flat(ImageOps.exif_transpose(frame.copy()) if orientation != 1 else frame.copy())
    buf = io.BytesIO()
    if source.suffix.lower() in HEIF_EXTENSIONS:
        picture.convert("RGB").save(buf, "JPEG", quality=HEIF_QUALITY, subsampling=0)
    else:
        picture.save(buf, "PNG")
    return buf.getvalue(), 0


def image_to_pdf(source: str | Path, target: str | Path, *, note: dict[str, Any] | None = None) -> int:
    """Write ``source`` (a picture) as a PDF at ``target``: one page a picture (every frame of a multi-page TIFF),
    upright, at its resolution. ``note`` is recorded in the PDF (:func:`original_of`). Returns the page count.
    Raises ``CannotRead`` for an iPhone photo without the photo add-on, and Pillow's error for a damaged file."""
    import pymupdf
    from PIL import ImageSequence

    source, target = Path(source), Path(target)
    raw = source.read_bytes()
    doc = pymupdf.open()
    try:
        with open_image(io.BytesIO(raw), source.name) as img:
            frames = getattr(img, "n_frames", 1)
            for i, frame in enumerate(ImageSequence.Iterator(img)):
                if i >= MAX_FRAMES:
                    break
                frame.load()
                data, turn = _encoded(frame, source, raw, frames == 1)
                width, height = frame.size
                w, h = _page_size(frame, width, height)
                page = doc.new_page(width=w, height=h)
                page.insert_image(page.rect, stream=data)
                if turn:
                    page.set_rotation(turn)
        if not doc.page_count:
            raise ValueError("the picture has no page")
        doc.set_metadata({"creator": CREATOR, "producer": "AP Coder", "title": source.name,
                          "keywords": json.dumps(note or {}, ensure_ascii=True)})  # fmt: skip
        part = target.with_name(target.name + ".part")
        doc.save(part, garbage=3, deflate=True)
        pages = doc.page_count
    finally:
        doc.close()
    os.replace(part, target)
    return pages


def to_pdf(path: str | Path) -> Converted:
    """Turn the picture at ``path`` into ``<name>.pdf`` beside it (``_1``, ``_2``... when the name is taken) and move
    the picture into ``originals/`` there (a copy already kept there is not kept twice). Raises ``CannotRead`` /
    Pillow's error when the picture can't be opened; the picture is then left where it was."""
    path = Path(path)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    keep_dir = path.parent / ORIGINALS_FOLDER
    kept = _free_name(keep_dir, path.name, raw)
    pdf = _free_name(path.parent, f"{path.stem}.pdf")
    note = {"original": path.name, "sha256": sha, "kept": f"{ORIGINALS_FOLDER}/{kept.name}"}
    pages = image_to_pdf(path, pdf, note=note)
    keep_dir.mkdir(parents=True, exist_ok=True)
    try:
        if kept.exists():  # the same picture came in before: kept once
            path.unlink()
        else:
            os.replace(path, kept)
    except OSError as exc:  # locked by a scanner or another program: left in place, the PDF is read all the same
        log.warning("%s: could not move the picture to %s (%s)", path.name, keep_dir, exc)
        kept = path
    return Converted(pdf, kept, sha, pages)


def original_of(pdf: str | Path) -> dict[str, Any] | None:
    """For a PDF AP Coder made from a picture: {"original": its name, "sha256", "kept": where it is, relative to the
    PDF's folder}; None for any other PDF (or a file that isn't one)."""
    try:
        import pymupdf

        with pymupdf.open(pdf) as doc:
            meta = doc.metadata or {}
            if meta.get("creator") != CREATOR:
                return None
            note = json.loads(meta.get("keywords") or "{}")
    except Exception:  # noqa: BLE001 - not a PDF, damaged, or no note: not one of ours
        return None
    return note if isinstance(note, dict) else None


def made_from_picture(doc: Any) -> bool:
    """The open PDF is one AP Coder made from a picture (each page is that picture, nothing else)."""
    try:
        return (doc.metadata or {}).get("creator") == CREATOR
    except Exception:  # noqa: BLE001
        return False


def page_picture(doc: Any, page: Any) -> Any | None:
    """The picture on a page of a PDF made from a picture, as a Pillow image the way the page shows it (its
    rotation applied); None when the page isn't one picture."""
    from PIL import Image

    images = page.get_images(full=True)
    if len(images) != 1:
        return None
    xref = images[0][0]
    try:
        extracted = doc.extract_image(xref)
        picture = Image.open(io.BytesIO(extracted["image"]))
        picture.load()
    except Exception:  # noqa: BLE001 - an image PyMuPDF stores another way: rendered instead
        return None
    turn = page.rotation % 360
    if turn:
        picture = picture.rotate(-turn, expand=True)  # Pillow turns counter-clockwise; the page turns clockwise
    return picture
