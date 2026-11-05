"""The invoice's pages on the review screen (ap_coder/web/document.py): their printed size, the PNG of each page, the
words to teach a field from, and the field list and boxes the page viewer is given."""

from __future__ import annotations

import datetime as dt
import io
import os
import re
import shutil
from pathlib import Path

import pytest
from PIL import Image

from ap_coder.capture.confidence import LABELS
from ap_coder.capture.types import FIELDS, Box, FieldResult, LineReading
from ap_coder.web import document
from ap_coder.web.core import STATIC_DIR
from ap_coder.web.document import FIELD_ORDER, page_png, page_sizes, page_words, shape_fields

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "northwind_ON_HST_NW-2026-0912.pdf"


def png_size(data: bytes) -> tuple[int, int]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(data)) as img:
        return img.size


@pytest.fixture(autouse=True)
def fresh_cache():
    for cached in (document._png_cached, document._sizes_cached):
        cached.cache_clear()
    yield
    for cached in (document._png_cached, document._sizes_cached):
        cached.cache_clear()


# --- Pages: printed size and picture ---------------------------------------------------------------------------------


def test_pdf_pages_sizes_in_points_and_pixels():
    assert page_sizes(SAMPLE) == [(612, 792), (612, 792)]  # US letter in points, whatever the resolution
    for number in (1, 2):
        assert png_size(page_png(SAMPLE, number)) == (1020, 1320)  # 8.5 x 11 in at 120 dpi
    with pytest.raises(IndexError):
        page_png(SAMPLE, 3)
    with pytest.raises(IndexError):
        page_png(SAMPLE, 0)


def test_a_page_is_never_drawn_bigger_than_the_cap(monkeypatch):
    monkeypatch.setattr(document, "DPI", 1200)
    assert max(png_size(page_png(SAMPLE, 1))) <= document.MAX_SIDE
    assert page_sizes(SAMPLE)[0] == (612, 792)  # the printed size does not change with the cap


def test_pictures_are_cached_until_the_file_changes(tmp_path):
    pdf = tmp_path / "inv.pdf"
    shutil.copy(SAMPLE, pdf)
    first = page_png(pdf, 1)
    assert page_png(pdf, 1) == first
    info = document._png_cached.cache_info()
    assert (info.hits, info.misses) == (1, 1)
    version = document.version(pdf)
    stat = pdf.stat()
    os.utime(pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))  # changed on disk: drawn again
    page_png(pdf, 1)
    assert document._png_cached.cache_info().misses == 2
    assert document.version(pdf) != version  # and the picture's address changes, so no browser shows the old one
    assert document.version(tmp_path / "gone.pdf") == "0"


def test_an_image_uses_its_dpi_and_paper_white(tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGBA", (1700, 2200), (0, 0, 0, 0)).save(path, dpi=(200, 200))
    assert page_sizes(path) == [(612, 792)]
    png = page_png(path, 1)
    assert png_size(png) == (1020, 1320)  # brought down to 120 dpi
    with Image.open(io.BytesIO(png)) as img:
        assert img.mode == "RGB" and img.getpixel((5, 5)) == (255, 255, 255)  # transparent becomes white paper


def test_an_image_without_dpi_and_a_multi_frame_tiff(tmp_path):
    path = tmp_path / "scan.tif"
    frames = [Image.new("L", (850, 1100), 255) for _ in range(3)]
    frames[0].save(path, save_all=True, append_images=frames[1:])
    # No dpi recorded: taken as a letter-width page (850 px over 8.5 in is 100 dpi)...
    assert page_sizes(path) == [(612, 792)] * 3
    assert png_size(page_png(path, 3)) == (850, 1100)
    # ... but never below screen resolution, so a small picture is not blown up to a page.
    small = tmp_path / "small.png"
    Image.new("RGB", (480, 300), "white").save(small)
    assert page_sizes(small) == [(360, 225)]


def test_unknown_and_missing_files(tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("not an invoice")
    assert page_sizes(other) == []
    with pytest.raises(IndexError):
        page_png(other, 1)
    with pytest.raises(FileNotFoundError):
        page_sizes(tmp_path / "gone.pdf")


# --- Sharper pictures for a page zoomed in (B-580) ------------------------------------------------------------------


def test_a_pdf_page_is_drawn_two_and_three_times_as_sharp():
    assert png_size(page_png(SAMPLE, 1, scale=1)) == (1020, 1320)  # 120 dpi, as before
    assert png_size(page_png(SAMPLE, 1, scale=2)) == (2040, 2640)  # 240 dpi
    assert png_size(page_png(SAMPLE, 1, scale=3)) == (3060, 3960)  # 360 dpi
    assert page_png(SAMPLE, 1) == page_png(SAMPLE, 1, scale=1)


def test_each_scale_has_its_own_cap(monkeypatch):
    monkeypatch.setattr(document, "DPI", 1200)
    for scale, cap in ((1, 2400), (2, 4800), (3, 6000)):
        assert document.max_side(scale) == cap
        assert max(png_size(page_png(SAMPLE, 1, scale=scale))) == cap


@pytest.mark.parametrize("value", [0, 4, -1, "2.5", "x", None, "", 99])
def test_a_scale_that_is_not_1_2_or_3_is_1(value):
    assert document.clean_scale(value) == 1
    assert document.clean_scale("2") == 2 and document.clean_scale(3) == 3


def test_the_scale_is_part_of_the_cache_key():
    one = page_png(SAMPLE, 1, scale=1)
    two = page_png(SAMPLE, 1, scale=2)
    assert one != two
    assert page_png(SAMPLE, 1, scale=2) == two
    info = document._png_cached.cache_info()
    assert (info.hits, info.misses) == (1, 2)


def test_a_scan_is_never_enlarged_beyond_its_own_resolution(tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGB", (1700, 2200), "white").save(path, dpi=(200, 200))
    assert png_size(page_png(path, 1, scale=1)) == (1020, 1320)  # 120 dpi
    assert png_size(page_png(path, 1, scale=2)) == (1700, 2200)  # 240 dpi asked, the scan's own 200 dpi given
    assert png_size(page_png(path, 1, scale=3)) == (1700, 2200)


def _scanned_pdf(path: Path, dpi: int, inches: tuple[float, float] = (4, 5), logo: bool = False) -> Path:
    """A PDF whose page is one picture scanned at ``dpi`` covering the whole page (a scanner's PDF); ``logo``: a small
    picture on a page of text instead (not a scan)."""
    import pymupdf

    picture = Image.new("L", (round(inches[0] * dpi), round(inches[1] * dpi)), 235)
    buf = io.BytesIO()
    picture.save(buf, "JPEG", quality=80)
    with pymupdf.open() as doc:
        page = doc.new_page(width=inches[0] * 72, height=inches[1] * 72)
        if logo:
            page.insert_text((20, 200), "INVOICE 1001  Total 105.00", fontsize=11)
            page.insert_image(pymupdf.Rect(20, 20, 92, 56), stream=buf.getvalue())
        else:
            page.insert_image(page.rect, stream=buf.getvalue())
        doc.save(path)
    return path


def _near(size: tuple[int, int], expected: tuple[int, int]) -> bool:
    return all(abs(a - b) <= 1 for a, b in zip(size, expected, strict=True))


def test_a_scanned_pdf_page_is_never_drawn_sharper_than_it_was_scanned(tmp_path):
    """B-594: a scanned PDF's page was drawn at 360 dpi at scale 3 whatever it was scanned at: a 300 dpi scan made
    a picture bigger than the scan, for no more detail."""
    scan = _scanned_pdf(tmp_path / "scan300.pdf", 300)  # 4 x 5 in at 300 dpi: 1200 x 1500 px
    assert _near(png_size(page_png(scan, 1, scale=1)), (480, 600))  # 120 dpi, as before
    assert _near(png_size(page_png(scan, 1, scale=2)), (960, 1200))  # 240 dpi: less than the scan
    assert _near(png_size(page_png(scan, 1, scale=3)), (1200, 1500))  # 300 dpi, the scan's own, not 360
    low = _scanned_pdf(tmp_path / "scan200.pdf", 200)  # 800 x 1000 px
    assert _near(png_size(page_png(low, 1, scale=2)), (800, 1000))
    assert _near(png_size(page_png(low, 1, scale=3)), (800, 1000))
    poor = _scanned_pdf(tmp_path / "scan72.pdf", 72)  # a poor scan is still drawn at 120 dpi at scale 1
    assert _near(png_size(page_png(poor, 1, scale=1)), (480, 600))
    # A page of text with a small picture on it is no scan: drawn at 360 dpi.
    text = _scanned_pdf(tmp_path / "logo.pdf", 300, logo=True)
    assert _near(png_size(page_png(text, 1, scale=3)), (1440, 1800))


def test_the_pictures_kept_are_bounded_by_their_size_in_bytes(monkeypatch):
    """B-594: 32 pictures were kept whatever their size: 32 scans at scale 3 were about 380 MB."""
    assert document.PICTURE_CACHE_BYTES == 96 * 1024 * 1024
    one = page_png(SAMPLE, 1)
    two = page_png(SAMPLE, 2)
    info = document._png_cached.cache_info()
    assert info.bytes == len(one) + len(two) and info.currsize == 2
    monkeypatch.setattr(document._png_cached, "max_bytes", len(one) + len(two))
    page_png(SAMPLE, 1)  # used again: now the newest
    sharp = page_png(SAMPLE, 1, scale=2)  # the oldest (page 2) goes first
    assert len(sharp) > len(two)
    info = document._png_cached.cache_info()
    assert info.bytes <= info.max_bytes or info.currsize == 1, info
    misses = info.misses
    page_png(SAMPLE, 2)  # dropped: drawn again
    assert document._png_cached.cache_info().misses == misses + 1
    page_png(SAMPLE, 2)
    assert document._png_cached.cache_info().misses == misses + 1  # and kept
    # A picture bigger than the whole room is still given, and the newest is kept
    monkeypatch.setattr(document._png_cached, "max_bytes", 10)
    assert page_png(SAMPLE, 1, scale=3)
    assert document._png_cached.cache_info().currsize == 1


# --- The company server's pictures, on disk for all its web workers (D-160) -------------------------------------------


def _kept(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file())


def _drawn(monkeypatch) -> list[tuple]:
    """Each picture drawn from now on (its arguments)."""
    drawn: list[tuple] = []
    real = document._draw_png
    monkeypatch.setattr(document, "_draw_png", lambda *a: drawn.append(a) or real(*a))
    return drawn


def test_on_the_server_a_page_is_drawn_once_for_all_its_web_workers(tmp_path, monkeypatch):
    """Each web worker drew the pages it was asked for (40 ms, 115 ms at scale 2) into its own 96 MB. One draws it
    into the data folder's .cache/pages; any other worker reads it back, and none keeps it in memory. Named by the
    file's content: the same file elsewhere has the same picture, another file at the same path its own."""
    import subprocess
    import sys

    pdf, folder = tmp_path / "inv.pdf", tmp_path / ".cache" / "pages"
    shutil.copy(SAMPLE, pdf)
    worker = ("import sys; from pathlib import Path; from ap_coder.web import document; "
              "sys.stdout.buffer.write(document.page_png(sys.argv[1], 1, 2, Path(sys.argv[2])))")  # fmt: skip
    drawn_there = subprocess.run([sys.executable, "-c", worker, str(pdf), str(folder)], cwd=ROOT, capture_output=True,
                                 check=True).stdout  # fmt: skip
    assert png_size(drawn_there) == (2040, 2640)
    (kept,) = _kept(folder)
    assert kept.read_bytes() == drawn_there and kept.parent.name == kept.name[:2]
    assert kept.name.endswith(f"-p1-s2-{document.DPI}dpi-{document.max_side(2)}px-d{document.DRAWING}.png")
    drawn = _drawn(monkeypatch)
    assert page_png(pdf, 1, 2, kept_in=folder) == drawn_there and drawn == []  # this worker: read back
    elsewhere = tmp_path / "copy.pdf"
    shutil.copy(pdf, elsewhere)
    assert page_png(elsewhere, 1, 2, kept_in=folder) == drawn_there and drawn == []
    assert document._png_cached.cache_info().currsize == 0  # nothing kept in memory
    shutil.copy(ROOT / "samples" / "chinook_AB_GST_CCO-26-10418.pdf", pdf)  # another invoice at the same path
    other = page_png(pdf, 1, 2, kept_in=folder)
    assert other != drawn_there and len(drawn) == 1 and len(_kept(folder)) == 2
    assert page_png(pdf, 1, 2) == other  # as a laptop draws it


def test_a_picture_that_cant_be_kept_is_still_shown_and_nothing_half_written_is_left(tmp_path, monkeypatch):
    """A full disk, or a folder that can't be made: the page is drawn each time."""
    import errno

    drawn = _drawn(monkeypatch)
    blocked = tmp_path / "data"
    blocked.write_text("a file where the folder would go", encoding="utf-8")
    first = page_png(SAMPLE, 1, kept_in=blocked / ".cache" / "pages")
    assert png_size(first) == (1020, 1320) and len(drawn) == 1
    folder = tmp_path / "pages"
    real = Path.write_bytes

    def full(self, data):
        real(self, data[: len(data) // 2])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", full)
    assert page_png(SAMPLE, 1, kept_in=folder) == first and page_png(SAMPLE, 1, kept_in=folder) == first
    assert len(drawn) == 3 and _kept(folder) == []


def test_on_windows_a_picture_being_read_is_left_as_it_is_and_a_refused_rename_is_tried_again(tmp_path, monkeypatch):
    """Windows refuses to replace a file another process has open (B-1689): a picture another worker is reading."""
    folder = tmp_path / "pages"
    real = os.replace
    refused: list[str] = []

    def at_first_refused(src, dst):
        if len(refused) < 2:
            refused.append(str(dst))
            raise PermissionError(13, "Access is denied")
        real(src, dst)

    def refused_each_time(src, dst):
        refused.append(str(dst))
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "replace", at_first_refused)
    png = page_png(SAMPLE, 1, kept_in=folder)
    (kept,) = _kept(folder)
    assert len(refused) == 2 and kept.read_bytes() == png  # tried again, then put there
    monkeypatch.setattr(os, "replace", refused_each_time)
    kept.write_bytes(b"the same picture, put there by another worker")
    document._keep(folder, kept, png)  # refused: the same picture is there, being read
    assert len(refused) == 3 and kept.read_bytes() == b"the same picture, put there by another worker"
    assert _kept(folder) == [kept]  # its own copy removed
    document._keep(folder, kept.with_name(kept.name.replace("-p1-", "-p2-")), png)  # none there: tried, given up
    assert len(refused) == 3 + 5 and _kept(folder) == [kept]


def test_the_pictures_kept_are_pruned_oldest_first_to_three_quarters_never_the_newest(tmp_path, monkeypatch):
    """At most ``PAGES_KEPT_BYTES`` (1 GB), looked at as a worker writes its first picture and after each sixteenth of
    it; a picture half written by a worker that stopped goes once it is an hour old."""
    folder, keys = tmp_path / "pages", ((1, 1), (2, 1), (1, 2), (2, 2))
    sizes = [len(page_png(SAMPLE, n, s)) for n, s in keys]  # (as drawn: the same bytes)
    pruned: list[Path] = []
    real = document._prune
    monkeypatch.setattr(document, "_prune", lambda f, newest: pruned.append(newest) or real(f, newest))
    monkeypatch.setattr(document, "PAGES_KEPT_BYTES", 16 * (sizes[1] + sizes[2]))
    for n, s in keys[:3]:
        page_png(SAMPLE, n, s, kept_in=folder)
    files = {(n, s): next(folder.glob(f"*/*-p{n}-s{s}-*.png")) for n, s in keys[:3]}
    assert pruned == [files[(1, 1)], files[(1, 2)]]  # its first picture, then once a sixteenth was written
    page_png(SAMPLE, 2, 2, kept_in=folder)
    files[(2, 2)] = next(folder.glob("*/*-p2-s2-*.png"))
    for at, key in enumerate(keys):  # written in that order
        os.utime(files[key], (1_700_000_000 + at, 1_700_000_000 + at))
    stale, writing = folder / "ab" / "x.png.1-2.part", folder / "ab" / "y.png.3-4.part"
    stale.parent.mkdir(exist_ok=True)
    for part in (stale, writing):
        part.write_bytes(b"half")
    os.utime(stale, (dt.datetime.now().timestamp() - 7200,) * 2)  # a worker stopped while writing it, 2 hours ago
    monkeypatch.setattr(document, "PAGES_KEPT_BYTES", (sizes[0] + sizes[3]) * 4 // 3 + 1)  # over it, by far
    real(folder, files[(1, 1)])  # the oldest written is the one just written (the newest): kept whatever its age
    assert [key for key in keys if files[key].exists()] == [(1, 1), (2, 2)]  # the oldest others, down to 3/4
    assert not stale.exists() and writing.exists()
    monkeypatch.setattr(document, "PAGES_KEPT_BYTES", sizes[0] + sizes[3])
    real(folder, files[(2, 2)])  # not over it: nothing goes
    assert [key for key in keys if files[key].exists()] == [(1, 1), (2, 2)]


def test_a_picture_is_not_kept_where_it_would_leave_the_disk_short_of_room(tmp_path, monkeypatch):
    """The cap counts the pictures, not the disk: on a data disk with little room left they could fill it, and the
    database's writes then fail. A picture is kept only while the disk keeps the backups' reserve free. The free space
    is read every 5 seconds at most, counted down by what was written since."""
    import time
    import types

    from ap_coder.core.store.backups import BACKUP_RESERVE

    clock = types.SimpleNamespace(now=1000.0)
    monkeypatch.setattr(document, "time", types.SimpleNamespace(monotonic=lambda: clock.now, time=time.time,
                                                                 sleep=lambda s: None))  # fmt: skip
    one = page_png(SAMPLE, 1)
    reads: list[Path] = []
    free = {"bytes": BACKUP_RESERVE + len(one) + 1}  # room for that picture alone

    def disk_usage(path):
        reads.append(path)
        if free["bytes"] is None:
            raise OSError("not a disk this computer can read")
        return types.SimpleNamespace(total=10**12, used=0, free=free["bytes"])

    monkeypatch.setattr(shutil, "disk_usage", disk_usage)
    folder = tmp_path / "pages"
    drawn = _drawn(monkeypatch)
    assert page_png(SAMPLE, 1, kept_in=folder) == one and len(_kept(folder)) == 1 and len(reads) == 1
    two = page_png(SAMPLE, 2, kept_in=folder)  # the reserve would be cut into: shown, not kept
    assert png_size(two) == (1020, 1320) and len(_kept(folder)) == 1 and len(reads) == 1
    page_png(SAMPLE, 2, kept_in=folder)
    assert len(drawn) == 3 and len(reads) == 1  # drawn each time, the free space as counted down
    free["bytes"] += 10 * len(two)  # room again (a backup removed): seen once 5 seconds have gone by
    clock.now += 4.9
    page_png(SAMPLE, 2, kept_in=folder)
    assert len(_kept(folder)) == 1 and len(reads) == 1
    clock.now += 0.2
    page_png(SAMPLE, 2, kept_in=folder)
    assert len(_kept(folder)) == 2 and len(reads) == 2
    free["bytes"] = None  # a free space that can't be read: kept
    clock.now += 5
    page_png(SAMPLE, 1, 2, kept_in=folder)
    assert len(_kept(folder)) == 3 and len(reads) == 3


# --- Words ------------------------------------------------------------------------------------------------------------


def test_page_words_are_fractions_of_the_page():
    words = page_words(SAMPLE)
    assert len(words) == 2
    found = [w for w in words[0] if w["t"] == "NW-2026-0912"]
    assert len(found) == 1
    x0, y0, x1, y1 = found[0]["b"]
    assert 0 < x0 < x1 < 1 and 0 < y0 < y1 < 1
    assert x0 == pytest.approx(98 / 612, abs=0.01) and y0 == pytest.approx(122 / 792, abs=0.01)


def test_words_are_cleaned_for_the_viewer():
    words = document.shape_words([[{"t": "Total", "b": [0.5, 0.5, 0.6, 0.52]}, {"t": " ", "b": [0, 0, 0.1, 0.1]},
                                   {"t": "x"}, {"t": "Out", "b": [-0.2, 0.1, 1.4, 0.2]}]])  # fmt: skip
    assert words == [[{"t": "Total", "b": [0.5, 0.5, 0.6, 0.52]}, {"t": "Out", "b": [0.0, 0.1, 1.0, 0.2]}]]


# --- The field list and the boxes ------------------------------------------------------------------------------------


def test_field_order_covers_every_capture_field():
    assert sorted(FIELD_ORDER) == sorted(FIELDS)
    assert set(FIELDS) <= set(LABELS)


def test_shape_fields_orders_like_an_invoice_and_fills_labels():
    total = FieldResult("grand_total", 18017.85, 0.42, "check", [Box(2, 0.8, 0.22, 0.9, 0.24)],
                        {"text": 18017.85, "ai": 18071.85}, ["check failed: total does not add up"])  # fmt: skip
    rows = shape_fields([
        total,
        {"field": "custom_ref", "value": "X1", "status": "likely", "confidence": 0.8},
        FieldResult("vendor_name", "Northwind", 0.99, "verified", [Box(1, 0.1, 0.05, 0.4, 0.07)]),
        {"field": "invoice_number", "value": "NW-1", "status": "verified", "confidence": 0.999,
         "boxes": [[1, 0.16, 0.15, 0.26, 0.17]]},
    ], LABELS)  # fmt: skip
    assert [r["field"] for r in rows] == ["vendor_name", "invoice_number", "grand_total", "custom_ref"]
    by = {r["field"]: r for r in rows}
    assert by["vendor_name"]["label"] == "Supplier" and by["vendor_name"]["display"] == "Northwind"
    assert by["invoice_number"]["label"] == LABELS["invoice_number"]
    assert by["grand_total"]["display"] == "18,017.85"
    assert by["grand_total"]["failed"] is True and by["vendor_name"]["failed"] is False
    assert by["grand_total"]["boxes"] == [[2, 0.8, 0.22, 0.9, 0.24]]
    assert by["custom_ref"]["label"] == "Custom ref"


def test_shape_fields_cleans_boxes_status_and_sources():
    rows = shape_fields([
        {"field": "po_number", "value": "PO-1", "status": "check", "confidence": 1.7,
         "boxes": [[1, 0.5, 0.2, 0.4, 0.1], [0, 0.1, 0.1, 0.2, 0.2], [1, 0.3, 0.3, 0.3, 0.4], "bad",
                   [1, -0.2, 0.1, 1.4, 0.2]],
         "sources": {"rules": "PO-1", "di": {"value": "PO-1", "conf": float("nan")}, "ai": dt.date(2026, 9, 14)}},
        {"field": "qst_registration_number", "value": None, "status": "verified", "boxes": []},
        {"field": "due_date", "value": "2026-10-14", "status": "nonsense", "boxes": [[1, 0.1, 0.1, 0.2, 0.2]]},
    ], LABELS)  # fmt: skip
    by = {r["field"]: r for r in rows}
    po = by["po_number"]
    assert po["boxes"] == [[1, 0.4, 0.1, 0.5, 0.2], [1, 0.0, 0.1, 1.0, 0.2]]  # sorted corners, clamped, bad dropped
    assert po["confidence"] == 1.0
    assert po["sources"] == {"rules": "PO-1", "di": {"value": "PO-1", "conf": None}, "ai": "2026-09-14"}
    assert by["qst_registration_number"]["status"] == "missing"  # nothing read, nothing to verify
    assert by["qst_registration_number"]["display"] == ""
    assert by["due_date"]["status"] == "missing" and by["due_date"]["boxes"] == []


def test_line_items_keep_their_row_boxes():
    items = [LineReading("Server", 1, 8900.0, 8900.0, [Box(1, 0.08, 0.33, 0.9, 0.35), Box(0, 0.8, 0.33, 0.9, 0.35)]),
             LineReading("No box", 1, 1.0, 1.0, [])]  # fmt: skip
    assert document.shape_line_items(items) == [{"label": "Server", "boxes": [[1, 0.08, 0.33, 0.9, 0.35]], "n": 1,
                                                 "amount": 8900.0}]  # fmt: skip
    no_amount = LineReading("Fee", None, None, None, [Box(1, 0.1, 0.4, 0.9, 0.42)])
    assert document.shape_line_items([no_amount])[0]["amount"] is None


# --- The viewer's own files -------------------------------------------------------------------------------------------


def test_the_viewer_loads_nothing_from_the_internet():
    for path in [*STATIC_DIR.glob("*.css"), *STATIC_DIR.glob("js/*.js")]:
        text = path.read_text(encoding="utf-8")
        # The SVG namespace is a name, not a download.
        assert not re.search(r"""(src=|href=|url\(|import\s|fetch\()\s*["']?(https?:)?//""", text), path.name
    assert (STATIC_DIR / "js" / "invoice_viewer.js").is_file() and (STATIC_DIR / "invoice_viewer.css").is_file()


def test_readers_that_read_the_same_value_printed_another_way_agree():
    """A date printed "Oct 1, 2026" is 2026-10-01: the readers behind the value agree, whatever the print; only an
    "other:<value>" reading differs (the tooltip said "differs" for both before)."""
    rows = shape_fields([
        {"field": "invoice_date", "value": "2026-10-01", "status": "verified", "confidence": 0.99,
         "boxes": [[1, 0.1, 0.1, 0.3, 0.12]],
         "sources": {"ai": "Oct 1, 2026", "rules": "Oct 1, 2026", "other:2026-10-02": "vlm"}},
    ], {})  # fmt: skip
    (row,) = rows
    assert row["agreeing"] == ["ai", "rules"] and "other:2026-10-02" in row["sources"]
    assert shape_fields([{"field": "po_number", "value": "PO-1", "status": "likely"}], {})[0]["agreeing"] == []
