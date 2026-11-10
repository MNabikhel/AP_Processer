"""A long invoice (B-2215): every page is read up to the pages setting (AP_READ_PAGES, 60), so the totals on the last
page are read; of a longer file the first and the last pages are read and the finding names the pages that were not."""

import pytest

from ap_coder.capture import analyze, layout
from ap_coder.capture.bridge import review_issues
from tests.pdfs import long_invoice


def _read(path):
    return analyze(path, ocr=False, tables=False, second_read=False)


def _pages_finding(capture) -> list[str]:
    return [words for _, code, words in review_issues(capture, "CAD") if code == "PAGES_NOT_READ"]


@pytest.mark.parametrize("pages", [21, 25, 60])
def test_a_long_invoice_keeps_the_totals_of_its_last_page(tmp_path, pages):
    path, subtotal, total = long_invoice(tmp_path, pages)
    read = _read(path)
    assert (read.page_count, read.pages_left_out) == (pages, 0)
    assert (read.fields["subtotal"].value, read.fields["grand_total"].value) == (subtotal, total)
    assert len(read.line_items) == pages * 20
    assert not _pages_finding(read)


def test_past_the_pages_setting_the_first_and_last_pages_are_read_and_the_rest_are_named(tmp_path):
    path, subtotal, total = long_invoice(tmp_path, 70)
    read = _read(path)
    assert (read.page_count, read.pages_left_out) == (70, 10)
    assert read.pages_not_read == list(range(56, 66))
    assert (read.fields["subtotal"].value, read.fields["grand_total"].value) == (subtotal, total)  # the last page
    assert read.fields["invoice_number"].value == "RW-88213"  # the first
    assert _pages_finding(read) == [
        "pages 56 to 65 of its 70 pages were not read (of a file longer than 60 pages the first 55 and the last 5 "
        "are read): nothing on them is in the lines or totals shown; check them yourself before approving"]  # fmt: skip
    assert CaptureResult_round_trip(read).pages_not_read == read.pages_not_read


def CaptureResult_round_trip(capture):
    return type(capture).from_dict(capture.to_dict())


def test_the_pages_read_are_a_setting(tmp_path, monkeypatch):
    path, _, total = long_invoice(tmp_path, 12, per_page=3)
    monkeypatch.setenv("AP_READ_PAGES", "8")
    read = _read(path)
    assert read.pages_not_read == [5, 6, 7, 8] and read.fields["grand_total"].value == total
    assert _pages_finding(read)[0].startswith("pages 5 to 8 of its 12 pages were not read (of a file longer than 8 "
                                              "pages the first 4 and the last 4 are read)")  # fmt: skip
    for typo in ("sixty", "0", "-3"):  # a typo never stops the reading: the default
        monkeypatch.setenv("AP_READ_PAGES", typo)
        assert layout.read_pages() == layout.READ_PAGES == 60
    assert _read(path).pages_not_read == []


def test_a_capture_with_every_page_read_is_stored_as_before(tmp_path):
    path, _, _ = long_invoice(tmp_path, 2, per_page=3)
    assert "pages_not_read" not in _read(path).to_dict()


def test_which_pages_are_read():
    assert layout.pages_read(3, 60) == [0, 1, 2]
    assert layout.pages_read(60, 60) == list(range(60))
    assert layout.pages_read(61, 60) == [*range(55), *range(56, 61)]
    assert layout.pages_read(300, 60) == [*range(55), *range(295, 300)]
    assert layout.pages_read(9, 4) == [0, 1, 7, 8]  # a small setting: half of it for the end
