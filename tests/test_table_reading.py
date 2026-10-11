"""Line items read with the table finder and the PDF's own table, in tandem with OvisOCR2: the table found on the page
(a layout model shipped in the offline bundle, never downloaded), only its words read as rows, a tilted scan's rows
straightened, a digital PDF's columns from its ruling lines and gaps, and two independent readings compared row by
row: agreed lines are marked so, lines read differently keep the invoice from going touchless."""

import datetime as dt
import hashlib
from pathlib import Path

import numpy as np
import pymupdf
import pytest

from ap_coder import offline
from ap_coder.capture import analyze, build_layout, layout, table_finder
from ap_coder.capture.bridge import review_issues
from ap_coder.capture.line_items import (
    CHECK_CODE,
    agree,
    agreement_check,
    description_similarity,
    mark_agreed,
    same_row,
    sources_line,
)
from ap_coder.capture.reader import inside_tables, read_line_items, straightened
from ap_coder.capture.supplier import AUTONOMOUS, HEADER_PROVEN_SOFT, should_auto_approve
from ap_coder.capture.tables import text_table_items, vertical_rules
from ap_coder.capture.types import VERIFIED, Box, CaptureResult, DocLayout, FieldResult, LineReading, PageLayout, Word
from ap_coder.help import help_for

from .conftest import SAMPLES

STEM = "harbourview_NS_HST_HPS-2026-0347"
PDF = SAMPLES / f"{STEM}.pdf"
TODAY = dt.date(2026, 10, 11)


def _words(*cells: tuple[str, float, float, float]) -> list[Word]:
    """Words from (text, x0, x1, y middle), one text line high."""
    return [Word(t, Box(1, x0, y - 0.006, x1, y + 0.006), 1.0, "text") for t, x0, x1, y in cells]


def _layout(words: list[Word], skew: float = 0.0, source: str = "text") -> DocLayout:
    page = PageLayout(1, 612, 792, words, layout.group_lines(words, 1))
    page.skew = skew
    return DocLayout([page], source)


TABLE = [
    ("Description", 0.10, 0.22, 0.30), ("Qty", 0.45, 0.49, 0.30), ("Amount", 0.60, 0.68, 0.30),
    ("Widget", 0.10, 0.16, 0.34), ("A", 0.165, 0.18, 0.34), ("2", 0.47, 0.48, 0.34), ("100.00", 0.62, 0.68, 0.34),
    ("Gadget", 0.10, 0.16, 0.38), ("B", 0.165, 0.18, 0.38), ("1", 0.47, 0.48, 0.38), ("50.00", 0.63, 0.68, 0.38),
]  # fmt: skip
# A remittance note printed beside the table, on the same rows.
SIDEBAR = [("Pay", 0.80, 0.83, 0.34), ("by", 0.835, 0.85, 0.34), ("EFT", 0.855, 0.89, 0.34),
           ("Ref", 0.80, 0.83, 0.38), ("ACME", 0.835, 0.89, 0.38)]  # fmt: skip


# --- only the words inside the table found ------------------------------------------------------------------------


def test_rows_are_read_from_the_words_inside_the_table_only():
    doc = _layout(_words(*TABLE, *SIDEBAR))
    before = read_line_items(doc)
    assert [li.description for li in before] == ["Widget A Pay by EFT", "Gadget B Ref ACME"]  # merged with the note
    tables = {1: [Box(1, 0.08, 0.28, 0.70, 0.40)]}
    inside = read_line_items(doc, tables)
    assert [(li.description, li.quantity, li.amount) for li in inside] == [("Widget A", 2.0, 100.0),
                                                                           ("Gadget B", 1.0, 50.0)]  # fmt: skip
    kept = inside_tables(doc, tables).pages[0]
    assert {w.text for w in kept.words} == {t for t, *_ in TABLE}
    assert inside_tables(doc, {2: [Box(2, 0, 0, 1, 1)]}).pages[0] is doc.pages[0]  # no table on it: whole page


def test_a_tilted_scan_is_read_straightened():
    """At this tilt a row's description sits most of a row away from its amount: read as printed, the rows pair the
    wrong words; straightened (the OCR's own text lines give the tilt), each row is whole."""
    skew = 0.03
    tilted = [Word(w.text, Box(1, w.box.x0, w.box.y0 + skew * (w.box.cx - 0.5), w.box.x1,
                               w.box.y1 + skew * (w.box.cx - 0.5)), 1.0, "ocr") for w in _words(*TABLE)]  # fmt: skip
    as_printed = read_line_items(_layout(tilted, 0.0, "ocr"))
    assert [li.description for li in as_printed] != ["Widget A", "Gadget B"]
    read = read_line_items(_layout(tilted, skew, "ocr"))
    assert [(li.description, li.amount) for li in read] == [("Widget A", 100.0), ("Gadget B", 50.0)]
    row = read[0].boxes[0]  # on the page as printed (tilted), not the straightened one
    assert row.y0 <= tilted[3].box.y0 + 1e-6 and row.y1 >= tilted[6].box.y1 - 1e-6
    assert straightened(_layout(tilted, 0.0)).pages[0].words is not None


def test_the_tilt_comes_from_the_ocr_text_lines():
    lines = [([[0, 100 + 40 * i], [400, 112 + 40 * i], [400, 132 + 40 * i], [0, 120 + 40 * i]], "some text here", 0.9)
             for i in range(8)]  # fmt: skip
    assert layout._skew(lines, 1000, 1300) == pytest.approx(12 / 400 * 1000 / 1300)
    assert layout._skew(lines[:3], 1000, 1300) == 0.0  # too few lines to tell
    turned = [([[0, 0], [100, 50], [100, 70], [0, 20]], "sideways text", 0.9)] * 8
    assert layout._skew(turned, 1000, 1300) == 0.0  # far more than a tilt: not straightened


def test_a_table_whose_headings_cant_be_read_is_read_by_its_amounts():
    """OCR runs bilingual headings together ("Qty Shipped/QuRate/Prix unitaire Extended /Prix total"): no heading
    row to go by, but the table the table finder found is read by the column its amounts end in."""
    words = _words(
        ("Item", 0.10, 0.15, 0.30), ("Qty", 0.45, 0.48, 0.30), ("Shipped/QuRate/Prix", 0.49, 0.62, 0.30),
        ("total", 0.63, 0.68, 0.30),
        ("LED", 0.10, 0.13, 0.34), ("panel", 0.135, 0.18, 0.34), ("24", 0.47, 0.48, 0.34), ("100,69", 0.52, 0.56, 0.34),
        ("2416.56", 0.62, 0.68, 0.34),
        ("Disinfectant", 0.10, 0.18, 0.38), ("24", 0.47, 0.48, 0.38), ("19.71", 0.52, 0.56, 0.38),
        ("473,04", 0.63, 0.68, 0.38),
        ("Sub-total", 0.45, 0.55, 0.42), ("2889.60", 0.62, 0.68, 0.42),
        ("Bill", 0.10, 0.13, 0.10), ("No.", 0.135, 0.16, 0.10), ("S-40696", 0.30, 0.36, 0.10),
    )  # fmt: skip
    doc = _layout(words, source="ocr")
    assert read_line_items(doc) == []  # no column headings: nothing read, as before
    tables = {1: [Box(1, 0.08, 0.08, 0.40, 0.12), Box(1, 0.08, 0.28, 0.70, 0.44)]}  # a header box, the line table
    items = read_line_items(doc, tables)
    assert [(li.description, li.quantity, li.unit_price, li.amount) for li in items] == [
        ("LED panel", 24.0, 100.69, 2416.56), ("Disinfectant", 24.0, 19.71, 473.04)]  # fmt: skip


def test_a_total_row_read_without_its_label_is_not_a_line():
    words = _words(*TABLE, ("150.00", 0.62, 0.68, 0.42))  # "Total before tax" OCR'd into nothing it knows
    assert [li.amount for li in read_line_items(_layout(words))] == [100.0, 50.0]


# --- the table finder: the model on this computer only --------------------------------------------------------------


class FakeSession:
    """Answers like the YOLOv8 layout model: (1, 4 + 6 labels, candidates)."""

    def __init__(self, candidates):
        self.candidates = candidates
        self.seen = None

    def get_inputs(self):
        return [type("Input", (), {"name": "images"})()]

    def run(self, _outputs, feed):
        self.seen = feed["images"]
        out = np.zeros((1, 4 + len(table_finder.LABELS), len(self.candidates)), dtype=np.float32)
        for i, (cx, cy, w, h, label, score) in enumerate(self.candidates):
            out[0, :4, i] = (cx, cy, w, h)
            out[0, 4 + table_finder.LABELS.index(label), i] = score
        return [out]


def _png(width=850, height=1100) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, "PNG")
    return buf.getvalue()


def test_the_table_finder_keeps_tables_merges_overlaps_and_drops_unsure_ones():
    session = FakeSession([
        (320, 320, 512, 192, "Table", 0.90),
        (322, 318, 500, 190, "Table", 0.60),  # the same table found twice: one
        (320, 100, 400, 40, "Text", 0.95),  # a paragraph: not a table
        (320, 560, 400, 60, "Table", 0.10),  # not sure enough
        (320, 500, 300, 40, "Table", 0.30),
    ])  # fmt: skip
    found = table_finder._find(session, _png(), 1)
    assert [(round(b.x0, 3), round(b.y0, 3), round(b.x1, 3), round(b.y1, 3)) for b, _ in found] == [
        (0.1, 0.35, 0.9, 0.65), (0.266, 0.75, 0.734, 0.812)]  # fmt: skip
    assert session.seen.shape == (1, 3, table_finder.SIDE, table_finder.SIDE)  # the page squeezed to 640 x 640


def test_without_the_model_line_items_are_read_as_before(monkeypatch, tmp_path):
    monkeypatch.setenv(offline.TABLE_FINDER_ENV, str(tmp_path / "missing.onnx"))
    monkeypatch.setattr(offline, "table_finder_dirs", lambda: [tmp_path])
    assert offline.table_finder_path() is None and not table_finder.available()
    assert table_finder.find_tables(PDF) is None and table_finder.find(_png()) is None
    result = analyze(PDF, today=TODAY)
    assert result.line_items and {li.source for li in result.line_items} <= {"words", "text table"}


def test_the_model_file_is_checked_never_downloaded(monkeypatch, tmp_path, no_network):
    fake = tmp_path / offline.TABLE_FINDER_NAME
    fake.write_bytes(b"not the model")
    monkeypatch.delenv(offline.TABLE_FINDER_ENV, raising=False)
    monkeypatch.setattr(offline, "table_finder_dirs", lambda: [tmp_path])
    assert offline.table_finder_path() is None  # its SHA-256 isn't the model's: as good as missing
    monkeypatch.setattr(offline, "TABLE_FINDER_SHA256", hashlib.sha256(b"not the model").hexdigest())
    assert offline.table_finder_path() == fake
    assert offline.table_finder_file() in offline.bundle_models()
    assert {m.name for m in offline.bundle_models()} >= {offline.TABLE_FINDER_NAME}


@pytest.fixture
def no_network(monkeypatch):
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("the table finder must never connect anywhere")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_the_reading_settings_show_the_table_finder(monkeypatch, tmp_path):
    from ap_coder.reading import OK, WARN, _table_finder

    monkeypatch.setattr(offline, "onnxruntime_installed", lambda: True)
    monkeypatch.setattr(offline, "table_finder_path", lambda: None)
    line = _table_finder()
    assert line.name == "Table finder" and line.state == WARN and "offline bundle" in line.detail
    monkeypatch.setattr(offline, "table_finder_path", lambda: tmp_path / "model.onnx")
    assert _table_finder().state == OK
    checks = {area: status for area, status, _ in offline.offline_checks()}
    assert checks["table finder"] == "PASS"


NO_MODEL = "the table finder's model isn't on this computer (it comes with the offline bundle)"


@pytest.mark.skipif(offline.table_finder_path() is None or not offline.onnxruntime_installed(), reason=NO_MODEL)
def test_the_real_model_finds_the_line_table_of_a_sample_invoice():
    tables = table_finder.find_tables(PDF)
    assert tables and 1 in tables
    items = read_line_items(build_layout(PDF), tables)
    assert len(items) == 4 and all(any(b.y0 - 0.03 <= li.boxes[0].cy <= b.y1 for b in tables[1]) for li in items)
    result = analyze(PDF, today=TODAY, page_text=[(SAMPLES / f"{STEM}.md").read_text(encoding="utf-8")])
    assert {li.source for li in result.line_items} == {"table"} and all("vlm" in li.agreed for li in result.line_items)


# --- a digital PDF's own table --------------------------------------------------------------------------------------


def _ruled_invoice(path: Path, ruled: bool = True) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((60, 60), "INVOICE", fontsize=16)
    cols = [60, 330, 390, 470, 552]
    y = 200
    for x, text in zip(cols, ["Description", "Qty", "Unit Price", "Amount"], strict=False):
        page.insert_text((x + 4, y), text, fontsize=10)
    rows = [("Consulting services, September", "10", "150.00", "1,500.00"),
            ("Travel to the Halifax site", "1", "320.40", "320.40"),
            ("(airfare and two nights)", "", "", ""),
            ("Software licence", "3", "99.00", "297.00")]  # fmt: skip
    for desc, qty, price, amount in rows:
        y += 18
        page.insert_text((cols[0] + 4, y), desc, fontsize=10)
        for x, text in ((cols[1], qty), (cols[2], price), (cols[3], amount)):
            if text:
                page.insert_text((x + 4, y), text, fontsize=10)
    if ruled:
        for x in cols:
            page.draw_line((x, 188), (x, y + 6))
    page.insert_text((390, y + 30), "Subtotal 2,117.40", fontsize=10)
    doc.save(path)
    return path


@pytest.mark.parametrize("ruled", [True, False])
def test_the_pdfs_own_table_reads_columns_and_wrapped_descriptions(tmp_path, ruled):
    pdf = _ruled_invoice(tmp_path / "ruled.pdf", ruled)
    doc = build_layout(pdf)
    with pymupdf.open(pdf) as opened:
        assert bool(vertical_rules(opened[0])) == ruled
    items = text_table_items(pdf, doc)
    assert [(li.description, li.quantity, li.unit_price, li.amount) for li in items] == [
        ("Consulting services, September", 10.0, 150.0, 1500.0),
        ("Travel to the Halifax site (airfare and two nights)", 1.0, 320.4, 320.4),  # the wrapped line joined
        ("Software licence", 3.0, 99.0, 297.0),
    ]
    assert {li.source for li in items} == {"text table"}
    assert text_table_items(pdf, DocLayout(doc.pages, "ocr")) == []  # a scan has no text layer to read


# --- two readers compared row by row --------------------------------------------------------------------------------


def _line(desc, amount, qty=None, source="table"):
    return LineReading(desc, qty, None, amount, [], 0.8, source)


def test_descriptions_read_the_same_despite_ocr_and_codes():
    assert description_similarity("Calibrationofpressuregauges", "Calibration of pressure gauges") > 0.9
    assert description_similarity("EE-23652 Calibration of pressure gauges", "Calibration of pressure gauges") == 1.0
    assert description_similarity("Disinfectant 4 L", "Toner cartridge HP 58X") < 0.5
    assert description_similarity("", "") == 1.0 and description_similarity("ea", "Disinfectant 4 L ea") < 0.6


def test_rows_agree_on_amount_quantity_and_description():
    a = _line("Hex bolt 3/8-16 x 2 in., zinc", 2.37, 3)
    assert same_row(a, _line("Hexbolt3/8-16x2in.,zinc", 2.37, 3, "vlm"))
    assert not same_row(a, _line("Hex bolt 3/8-16 x 2 in., zinc", 2.73, 3, "vlm"))  # amount
    assert not same_row(a, _line("Hex bolt 3/8-16 x 2 in., zinc", 2.37, 4, "vlm"))  # quantity
    assert same_row(a, _line("Hex bolt 3/8-16 x 2 in., zinc", 2.37, None, "vlm"))  # one read no quantity
    assert not same_row(a, _line("Shipping cartons", 2.37, 3, "vlm"))  # description
    assert agree([a], [a]) and not agree([a], [a, a]) and not agree([], [])


def test_the_agreement_check():
    page = [_line("Widget A", 100.0), _line("Gadget B", 50.0)]
    vlm = [_line("Widget A", 100.0, source="vlm"), _line("Gadget B", 50.0, source="vlm")]
    passed = agreement_check([page], vlm, "table")
    assert passed["code"] == CHECK_CODE and passed["ok"] and "the table finder and OvisOCR2" in passed["detail"]
    wrong = [_line("Widget A", 100.0, source="vlm"), _line("Gadget B", 55.0, source="vlm")]
    failed = agreement_check([page], wrong, None)
    assert not failed["ok"] and "line 2 reads 50.00" in failed["detail"] and "55.00" in failed["detail"]
    fewer = agreement_check([page], wrong[:1], None)
    assert not fewer["ok"] and "read 2 line items and OvisOCR2 1" in fewer["detail"]
    assert agreement_check([[]], vlm, None) is None and agreement_check([page], [], None) is None  # one reader only
    mark_agreed(page, "vlm")
    assert all(li.agreed == ["vlm"] for li in page)
    assert sources_line(page, [passed]) == "Line items: 2 of 2 read the same by OvisOCR2 and the table finder."
    assert sources_line(page, [failed]).startswith("Line items read differently by two readers")
    assert "one reader only" in sources_line([_line("Widget A", 1.0, source="words")])
    assert sources_line([]) == ""
    assert help_for(CHECK_CODE).area == "Reading the document"


def test_line_readings_keep_their_source_through_storage():
    li = LineReading("Widget", 1.0, 2.0, 2.0, [Box(1, 0.1, 0.2, 0.3, 0.4)], 0.8, "table", ["vlm"])
    back = LineReading.from_dict(li.to_dict())
    assert (back.source, back.agreed) == ("table", ["vlm"])
    old = LineReading.from_dict({"description": "x", "quantity": None, "unit_price": None, "amount": 1.0})
    assert (old.source, old.agreed) == ("", [])


def _transcript() -> str:
    return (SAMPLES / f"{STEM}.md").read_text(encoding="utf-8")


def test_ovisocr2_and_the_page_agree_on_a_sample_invoice():
    result = analyze(PDF, today=TODAY, page_text=[_transcript()])
    check = next(c for c in result.checks if c["code"] == CHECK_CODE)
    assert check["ok"] and len(result.line_items) == 4
    assert all("vlm" in li.agreed for li in result.line_items)
    assert not [i for i in review_issues(result) if i[1] == CHECK_CODE]


def test_line_items_read_differently_keep_the_invoice_from_going_touchless():
    text = _transcript()
    assert "4,620.00" in text
    result = analyze(PDF, today=TODAY, page_text=[text.replace("4,620.00", "4,260.00", 1)])
    check = next(c for c in result.checks if c["code"] == CHECK_CODE)
    assert not check["ok"] and "4,620.00" in check["detail"] and "4,260.00" in check["detail"]
    assert not any(li.agreed for li in result.line_items)
    assert any(i[1] == CHECK_CODE for i in review_issues(result))
    # Every header field verified and every other check passed: the line items alone send it to a person.
    for f in result.fields.values():
        f.status = VERIFIED
    result.checks = [{**c, "ok": True} if c["code"] != CHECK_CODE else c for c in result.checks]
    auto, why = should_auto_approve(AUTONOMOUS, result, True)
    assert not auto and CHECK_CODE in why and CHECK_CODE not in HEADER_PROVEN_SOFT
    result.checks = [{**c, "ok": True} for c in result.checks]
    assert should_auto_approve(AUTONOMOUS, result, True)[0]


def test_a_stored_capture_with_a_failed_line_check_is_not_approved_alone():
    capture = CaptureResult(fields={"grand_total": FieldResult("grand_total", 10.0, 0.999, VERIFIED)},
                            checks=[{"code": "TOTALS_ADD_UP", "ok": True},
                                    {"code": CHECK_CODE, "ok": False, "detail": "x"}])  # fmt: skip
    assert should_auto_approve(AUTONOMOUS, capture.to_dict(), True) == (False, f"check failed: {CHECK_CODE}")
