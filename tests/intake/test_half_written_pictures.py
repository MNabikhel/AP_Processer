"""A picture still being written into the invoices folder (a scanner's multi-page TIFF), or a photo cut off in the
copy: left for the next pickup while it is new, and said plainly when it is damaged (B-1528)."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from ap_coder.ai.pipeline import plain_error
from ap_coder.intake.pictures import looks_incomplete


def _tiff(pages: int = 4) -> bytes:
    frames = [Image.new("L", (1200, 1600), 255 - 20 * n) for n in range(pages)]
    out = io.BytesIO()
    frames[0].save(out, "TIFF", save_all=True, append_images=frames[1:], compression="tiff_deflate")
    return out.getvalue()


def test_a_tiff_cut_part_way_looks_incomplete(tmp_path):
    whole = _tiff()
    for share in (0.3, 0.6, 0.9):
        cut = tmp_path / f"fax_{share}.tif"
        cut.write_bytes(whole[: int(len(whole) * share)])
        assert looks_incomplete(cut), share
    done = tmp_path / "fax.tif"
    done.write_bytes(whole)
    assert not looks_incomplete(done)


def _raised_in(module: str, exc: Exception) -> Exception:
    """``exc`` as it comes back once raised by code of ``module``."""
    try:
        exec("raise exc", {"__name__": module, "exc": exc})  # noqa: S102 - a frame of that module, for its traceback
    except Exception as raised:  # noqa: BLE001
        return raised
    return exc


def test_a_damaged_picture_is_said_plainly_whatever_pillow_raises():
    """Pillow raises OSError ("Truncated File Read"), TypeError ("Missing dimensions") or SyntaxError for a picture it
    can't decode: the Failed tab said the Python error."""
    for exc in (OSError("Truncated File Read"), TypeError("Missing dimensions"), SyntaxError("not a TIFF file")):
        said = plain_error(_raised_in("PIL.TiffImagePlugin", exc), Path("photo.JPG"))
        assert said.startswith("photo.JPG can't be opened as a picture"), said
        # The same kind of error raised by AP Coder's own code is a bug, said as the error it is (B-2204).
        ours = _raised_in("ap_coder.intake.pictures", type(exc)(*exc.args))
        assert plain_error(ours, Path("photo.JPG")) == f"{type(exc).__name__}: {exc}"
    disk = OSError(28, "No space left on device")  # a system error is said as it is
    assert "No space left" in plain_error(disk, Path("photo.jpg"))


def test_an_iphone_photo_cut_part_way_looks_incomplete_and_is_said_plainly(tmp_path):
    """B-2979: a HEIC photo still being copied in (a phone's sync) was read at once, as only a PDF, a JPEG and a PNG
    had an end to look for and only a TIFF, BMP, GIF or WEBP was decoded to tell: pillow-heif's ValueError ("Invalid
    input: Unexpected end of file ...") stayed in the Failed tab, and the whole photo came in later as a second row.
    Its boxes tell (each must end within the file, its meta and mdat there), and a damaged one is said plainly."""
    import pytest
    from PIL import ImageDraw

    from ap_coder.capture.layout import register_heif
    from ap_coder.intake.pictures import to_pdf

    if not register_heif():
        pytest.skip("pillow-heif is not installed")
    page = Image.new("RGB", (900, 1200), "white")
    for y in range(40, 1200, 40):
        ImageDraw.Draw(page).text((40, y), f"INVOICE INV-77  line {y} ...... 123.45", fill="black")
    out = io.BytesIO()
    page.save(out, "HEIF", quality=80)
    whole = out.getvalue()
    for share in (0.02, 0.1, 0.3, 0.6, 0.9, 0.99):
        cut = tmp_path / f"IMG_{share}.HEIC"
        cut.write_bytes(whole[: int(len(whole) * share)])
        assert looks_incomplete(cut), share
    done = tmp_path / "IMG_0412.HEIC"
    done.write_bytes(whole)
    assert not looks_incomplete(done)
    with pytest.raises(ValueError) as raised:  # cut off for good: its reading fails, said in plain words
        to_pdf(tmp_path / "IMG_0.6.HEIC")
    said = plain_error(raised.value, tmp_path / "IMG_0.6.HEIC")
    assert said.startswith("IMG_0.6.HEIC can't be opened as a picture: the file is damaged"), said
