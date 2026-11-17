"""D-245: with Azure Document Intelligence on, a scan or a photo is still read by this computer's own OCR first; Azure's
words stand in for a page only when OCR isn't installed or can't read it. Its fields are then compared with what OCR
read (capture/__init__.py ``_di_sources``), so the two work together rather than Azure replacing OCR."""

import io

import pymupdf
import pytest
from PIL import Image

from ap_coder.capture import layout
from ap_coder.capture.types import PageLayout, Word


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (612, 792), "white").save(out, "PNG")
    return out.getvalue()


def _scan(path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=_png())
    doc.save(path)
    return path


def _photo(path):
    path.write_bytes(_png())
    return path


AZURE = {"pages": [{"pageNumber": 1, "width": 8.5, "height": 11, "words": [
    {"content": "AZURE", "polygon": [1, 1, 2, 1, 2, 1.2, 1, 1.2], "confidence": 0.99}]}]}  # fmt: skip


def _local_ocr(monkeypatch, *, fails=False):
    monkeypatch.setattr(layout, "ocr_available", lambda: True)

    def ocr(image, number, engine=None):
        if fails:
            raise RuntimeError("OCR can't read this page")
        box = layout.Box(number, 0.1, 0.1, 0.2, 0.12)
        word = Word("LOCAL", box, 0.95, "ocr")
        return PageLayout(number, 612, 792, [word], layout.group_lines([word], number))

    monkeypatch.setattr(layout, "ocr_image", ocr)


@pytest.mark.parametrize("make", [_scan, _photo], ids=["scanned pdf", "photo"])
def test_with_azure_on_a_scan_is_still_read_by_this_computers_ocr(make, tmp_path, monkeypatch):
    _local_ocr(monkeypatch)
    name = "scan.pdf" if make is _scan else "photo.png"
    read = layout.build_layout(make(tmp_path / name), di_raw=AZURE)
    assert [w.text for w in read.pages[0].words] == ["LOCAL"] and read.source == "ocr"


@pytest.mark.parametrize("make", [_scan, _photo], ids=["scanned pdf", "photo"])
def test_azures_words_stand_in_for_a_page_ocr_cant_read(make, tmp_path, monkeypatch):
    _local_ocr(monkeypatch, fails=True)
    name = "scan.pdf" if make is _scan else "photo.png"
    read = layout.build_layout(make(tmp_path / name), di_raw=AZURE)
    assert [w.text for w in read.pages[0].words] == ["AZURE"] and read.source == "di"


@pytest.mark.parametrize("make", [_scan, _photo], ids=["scanned pdf", "photo"])
def test_without_ocr_installed_azures_words_are_read(make, tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "ocr_available", lambda: False)
    name = "scan.pdf" if make is _scan else "photo.png"
    read = layout.build_layout(make(tmp_path / name), di_raw=AZURE)
    assert [w.text for w in read.pages[0].words] == ["AZURE"] and read.source == "di"


# Parts of Azure's words of another shape: (how, whether the page's other words are still read).
ODD_WORDS = {
    "a word that is no object": (lambda p: p["words"].insert(0, None), True),
    "a polygon of text": (lambda p: p["words"].insert(0, {"content": "X", "polygon": ["a"] * 8}), True),
    "a word's content a number": (lambda p: p["words"].insert(0, {"content": 5, "polygon": [1] * 8}), True),
    "the confidence as text": (lambda p: p["words"][0].update(confidence="high"), True),
    "the angle as text": (lambda p: p.update(angle="level"), True),
    "the page number as text": (lambda p: p.update(pageNumber="one"), True),
    "the width as text": (lambda p: p.update(width="8.5in"), False),
}  # fmt: skip


@pytest.mark.parametrize("name", list(ODD_WORDS))
def test_azures_words_of_another_shape_are_left_out_never_a_failure(name, tmp_path, monkeypatch):
    """An answer kept (``azure_check.readable``) whose words are of another shape: the invoice is still read, a
    digital PDF by its text, a scan's page OCR can't read by Azure's words that do read (D-245)."""
    import copy
    from pathlib import Path

    from ap_coder.capture import analyze

    raw = copy.deepcopy(AZURE)
    raw["pages"][0]["words"].append({"content": "AZURE2", "polygon": [3, 1, 4, 1, 4, 1.2, 3, 1.2]})
    how, words_read = ODD_WORDS[name]
    how(raw["pages"][0])
    sample = Path(__file__).resolve().parents[2] / "samples" / "northwind_ON_HST_NW-2026-0912.pdf"
    assert analyze(sample, di_raw=raw, tables=False).fields["invoice_number"].value == "NW-2026-0912"
    _local_ocr(monkeypatch, fails=True)
    read = layout.build_layout(_scan(tmp_path / "scan.pdf"), di_raw=raw)
    assert ("AZURE2" in [w.text for w in read.pages[0].words]) is words_read
