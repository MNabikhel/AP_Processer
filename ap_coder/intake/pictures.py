"""Every invoice is a PDF: a photo or a scan saved as a picture is turned into one when it comes in.

AP processes PDFs. A JPEG, PNG, iPhone photo (HEIC/HEIF), TIFF (every page of a multi-page TIFF), BMP, WEBP or GIF
(its first frame) that arrives by upload, in the invoices folder or attached to an email is made into a PDF before
it is read (``InvoicePipeline.process`` calls :func:`to_pdf` first, so every way in is covered): one page a
picture, turned upright as the photo's EXIF says, at the resolution it was taken or scanned at. The invoice is then
stored, read, shown and stamped as that PDF.

Nothing is recompressed that need not be: a JPEG goes into the PDF as it is (a photo taken sideways is turned
by the page's rotation, not by re-encoding it), a PNG, TIFF, BMP, WEBP or GIF losslessly. Only an iPhone photo, which a
PDF can't hold as it is, is re-encoded, as a high-quality JPEG; and a phone photo of a page lying on a desk,
which is flattened as a scanner app would (the page cut out of the desk and squared up: ``flatten_photo``), so
it is read, shown and highlighted as the page it is.

The picture itself is kept for the audit trail, in an ``originals`` folder next to the PDF (moved there, so
the invoices folder doesn't offer it again). The PDF records the picture's name, SHA-256 and where it was kept
(:func:`original_of`), so a photo sent twice is found as the same invoice (``Store.find_by_hash``) and the
review screen can point to it.

The OCR of a converted page reads the picture's own pixels (``capture/layout.py``, :func:`page_picture`), the
same as before the conversion, never a re-rendering of the page.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..capture.layout import HEIF_EXTENSIONS, IMAGE_EXTENSIONS, ONE_FRAME_EXTENSIONS, open_image

log = logging.getLogger(__name__)

ORIGINALS_FOLDER = "originals"
CREATOR = "AP Coder image to PDF"  # in the PDF's metadata: a page that is a picture AP Coder put there
TURNED = "AP Coder turned upright"  # a scanned PDF from elsewhere that AP Coder showed upright (its pages as they were)
MAX_FRAMES = 200  # pages taken from one multi-page TIFF
PAGE_LONG_SIDE = 11 * 72  # a picture without a believable resolution becomes a letter-sized page (points)
MIN_DPI, MAX_DPI = 50, 1200  # a resolution outside these is not believed
MAX_PAGE_INCHES = 17  # nor one that makes a page longer than this (a phone's "72 dpi")
HEIF_QUALITY = 95  # an iPhone photo has to be re-encoded: as a JPEG this good
FLAT_QUALITY = 95  # a photo's page, flattened: a JPEG this good (a PNG when the picture was lossless)
_ROTATION = {3: 180, 6: 90, 8: 270}  # EXIF orientation -> clockwise turn of the page


@dataclass(frozen=True)
class Converted:
    """An image made into a PDF: the PDF, where the image was kept, and the image's SHA-256."""

    pdf: Path
    original: Path
    sha256: str
    pages: int


def is_image(path: str | Path) -> bool:
    """A picture AP Coder turns into a PDF (JPEG, PNG, HEIC/HEIF, TIFF, BMP, WEBP, GIF)."""
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


_PICTURE_MAGIC = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"II*\x00", ".tif"), (b"MM\x00*", ".tif"),
                  (b"BM", ".bmp"), (b"GIF87a", ".gif"), (b"GIF89a", ".gif"))  # fmt: skip
_HEIF_BRANDS = (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1")
_SAME_KIND = {".jpeg": ".jpg", ".tiff": ".tif", ".heif": ".heic"}
WEB_PAGE = ("{name} is a web page saved with a {ext} name, not the invoice itself: open the vendor's email or portal "
            "and download the invoice again (as a PDF).")  # fmt: skip


def content_kind(path: str | Path) -> str:
    """What a file is by its first bytes, whatever its name says: ".pdf", a picture's extension (".png", ".jpg",
    ".tif", ".bmp", ".gif", ".webp", ".heic"), ".html" for a web page, "" when none of these."""
    with open(path, "rb") as fh:
        head = fh.read(1024)
    if b"%PDF-" in head:  # a few stray bytes may come before it (readers accept that)
        return ".pdf"
    for magic, kind in _PICTURE_MAGIC:
        if head.startswith(magic):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS:
        return ".heic"
    if head.lstrip(b"\xef\xbb\xbf \t\r\n").lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return ".html"
    return ""


def looks_incomplete(path: str | Path) -> bool:
    """A file whose end is missing: empty, a PDF without its closing "%%EOF", a JPEG without its end marker or a PNG
    without its end chunk (still being written by a scanner or a download, or cut off). Other kinds: False."""
    path = Path(path)
    size = path.stat().st_size
    if not size:
        return True
    kind = content_kind(path)
    with open(path, "rb") as fh:
        fh.seek(max(0, size - 1024))
        tail = fh.read()
    end = {".pdf": b"%%EOF", ".jpg": b"\xff\xd9", ".png": b"IEND"}.get(kind)
    return end is not None and end not in tail


def as_its_kind(path: str | Path) -> Path:
    """``path`` renamed to the extension its content has when it is a PDF or a picture named as another (a PDF saved
    as ".jpg", a phone's PNG as ".pdf"), so it is read, kept and stamped as what it is. As it was when its name is
    right, its content unknown (said by the reader that fails on it), or it can't be renamed. Raises ``CannotRead``
    for a web page saved with an invoice's name (a vendor portal's sign-in page instead of the PDF)."""
    from ..capture.layout import CannotRead

    path = Path(path)
    suffix = path.suffix.lower()
    kind = content_kind(path)
    if kind == ".html":
        raise CannotRead(WEB_PAGE.format(name=path.name, ext=suffix or "file"))
    if not kind or kind == _SAME_KIND.get(suffix, suffix) or (kind != ".pdf" and kind not in IMAGE_EXTENSIONS):
        return path
    target = _free_name(path.parent, path.stem + kind)
    try:
        os.replace(path, target)
    except OSError as exc:  # locked or read-only: read under its own name (the reader for that name says what fails)
        log.warning("%s is a %s file but could not be renamed (%s)", path.name, kind, exc)
        return path
    log.info("%s is a %s file: renamed %s", path.name, kind.lstrip(".").upper(), target.name)
    return target


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
    from ..capture.layout import _on_paper

    if picture.mode in ("1", "L", "RGB"):
        return picture
    if picture.mode in ("I;16", "I", "F"):  # 16-bit or float greys (some scanners): 8-bit grey
        return picture.convert("L")
    return _on_paper(picture)


def _encoded(frame: Any, source: Path, raw: bytes, single: bool) -> tuple[bytes, int, Any]:
    """(the image data for the page, the page's clockwise turn, the picture whose size and resolution the page
    takes). A photo of a page on a desk is flattened; else a JPEG is kept as it is, turned by the page; a mirrored
    photo, a picture with transparency or any other kind is upright and lossless (PNG); an iPhone photo a
    high-quality JPEG."""
    from PIL import ImageOps

    from ..capture.layout import flatten_photo

    orientation = _orientation(frame)
    jpeg = frame.format == "JPEG" or (single and raw[:3] == b"\xff\xd8\xff")
    lossy = jpeg or source.suffix.lower() in HEIF_EXTENSIONS
    flat = flatten_photo(ImageOps.exif_transpose(frame.copy()) if orientation != 1 else frame) if single else None
    if flat is not None:
        if frame.info.get("dpi"):
            flat.info["dpi"] = frame.info["dpi"]  # a scanner's dark bed: still the resolution it was scanned at
        buf = io.BytesIO()
        if lossy:
            flat.save(buf, "JPEG", quality=FLAT_QUALITY, subsampling=0)
        else:
            flat.save(buf, "PNG")
        return buf.getvalue(), 0, flat  # a phone's photo states no believable resolution: a letter-sized page
    if single and jpeg and frame.mode in ("RGB", "L") and orientation in (1, *_ROTATION):
        return raw, _ROTATION.get(orientation, 0), frame
    picture = _flat(ImageOps.exif_transpose(frame.copy()) if orientation != 1 else frame.copy())
    buf = io.BytesIO()
    if source.suffix.lower() in HEIF_EXTENSIONS:
        picture.convert("RGB").save(buf, "JPEG", quality=HEIF_QUALITY, subsampling=0)
    else:
        picture.save(buf, "PNG")
    return buf.getvalue(), 0, frame


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
            one = source.suffix.lower() in ONE_FRAME_EXTENSIONS  # an animated GIF or WEBP: the page it shows first
            frames = 1 if one else getattr(img, "n_frames", 1)
            for i, frame in enumerate(ImageSequence.Iterator(img)):
                if i >= (1 if one else MAX_FRAMES):
                    break
                if frame.mode not in ("P", "PA"):  # counting the pages read the last one: its palette is not this
                    frame.palette = None  # page's (Pillow keeps it, and a black-and-white page then can't load)
                frame.load()
                data, turn, shown = _encoded(frame, source, raw, frames == 1)
                width, height = shown.size
                w, h = _page_size(shown, width, height)
                page = doc.new_page(width=w, height=h)
                page.insert_image(page.rect, stream=data, keep_proportion=False)  # a fax's 204 x 98 dpi: page-sized
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


def turn_upright(pdf: str | Path, turns: dict[int, int]) -> Converted | None:
    """Show upright the pages OCR found scanned turned (``turns``: page number -> the clockwise quarter turn that
    sets it upright), so the review screen highlights each value where the page shows it. Only the pages' rotation
    changes, never what is printed on them. A PDF AP Coder made (from a picture, or turned before) is its own and is
    turned where it is (None); any other is kept in ``originals/`` as it came and the turned copy takes its place, its
    note naming the original as for a picture (returned, as :func:`to_pdf` returns a picture's). Nothing is left
    behind when the file can't be written: the PDF stays as it was."""
    import pymupdf

    pdf = Path(pdf)
    raw = pdf.read_bytes()
    part = pdf.with_name(pdf.name + ".part")
    kept: Path | None = None
    made_copy = made_folder = False
    try:
        with pymupdf.open(stream=raw, filetype="pdf") as doc:
            ours = (doc.metadata or {}).get("creator") in (CREATOR, TURNED)
            for number, turn in turns.items():
                if 1 <= number <= doc.page_count and turn % 360:
                    page = doc[number - 1]
                    page.set_rotation((page.rotation + turn) % 360)
            note = None
            if not ours:
                sha = hashlib.sha256(raw).hexdigest()
                kept = _free_name(pdf.parent / ORIGINALS_FOLDER, pdf.name, raw)
                note = {"original": pdf.name, "sha256": sha, "kept": f"{ORIGINALS_FOLDER}/{kept.name}"}
                meta = doc.metadata or {}
                doc.set_metadata({**{k: v for k, v in meta.items() if v and k != "format" and k != "encryption"},
                                  "creator": TURNED, "keywords": json.dumps(note, ensure_ascii=True)})  # fmt: skip
            doc.save(part, garbage=3, deflate=True)
            pages = doc.page_count
        if kept is not None and not kept.exists():
            made_folder = not kept.parent.exists()
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_bytes(raw)
            made_copy = True
        os.replace(part, pdf)
    except BaseException:
        part.unlink(missing_ok=True)
        if made_copy and kept is not None:
            kept.unlink(missing_ok=True)
            if made_folder:
                with contextlib.suppress(OSError):
                    kept.parent.rmdir()
        raise
    if note is None or kept is None:
        return None
    return Converted(pdf, kept, note["sha256"], pages)


def original_of(pdf: str | Path) -> dict[str, Any] | None:
    """For a PDF AP Coder made from a picture, or a scan it turned upright: {"original": its name, "sha256", "kept":
    where it is, relative to the PDF's folder}; None for any other PDF (or a file that isn't one)."""
    try:
        import pymupdf

        with pymupdf.open(pdf) as doc:
            meta = doc.metadata or {}
            if meta.get("creator") not in (CREATOR, TURNED):
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


def _stored_pixels(doc: Any, xref: int) -> Any | None:
    """A picture stored losslessly (as ``image_to_pdf`` stores a PNG: grey or colour, no transparency), straight from
    its pixels: the same image ``extract_image`` gives, without making a PNG of it and reading that back (about a
    second a page). None for any other kind (a JPEG is extracted as it is stored)."""
    import pymupdf
    from PIL import Image

    if doc.xref_get_key(xref, "Filter") != ("name", "/FlateDecode") or doc.xref_get_key(xref, "SMask")[0] != "null":
        return None
    pix = pymupdf.Pixmap(doc, xref)
    if pix.alpha or pix.n not in (1, 3) or pix.colorspace is None or pix.colorspace.n != pix.n:
        return None
    return Image.frombytes("L" if pix.n == 1 else "RGB", (pix.width, pix.height), pix.samples)


def page_picture(doc: Any, page: Any) -> Any | None:
    """The picture on a page of a PDF made from a picture, as a Pillow image the way the page shows it (its
    rotation applied); None when the page isn't one picture."""
    from PIL import Image

    images = page.get_images(full=True)
    if len(images) != 1:
        return None
    xref = images[0][0]
    try:
        picture = _stored_pixels(doc, xref)
        if picture is None:
            picture = Image.open(io.BytesIO(doc.extract_image(xref)["image"]))
            picture.load()
    except Exception:  # noqa: BLE001 - an image PyMuPDF stores another way: rendered instead
        return None
    # A picture with taller pixels than wide (a standard fax: 204 dpi across, 98 down) is shown at the page's
    # shape: its rows stretched, as a fax viewer shows it, or OCR would read squashed letters.
    across, down = page.cropbox.width, page.cropbox.height
    if across > 0 and down > 0 and picture.width and picture.height:
        stretch = (down / across) / (picture.height / picture.width)
        if stretch > 1.25:
            picture = picture.resize((picture.width, round(picture.height * stretch)))
        elif stretch < 0.8:
            picture = picture.resize((round(picture.width / stretch), picture.height))
    turn = page.rotation % 360
    if turn:
        picture = picture.rotate(-turn, expand=True)  # Pillow turns counter-clockwise; the page turns clockwise
    return picture
