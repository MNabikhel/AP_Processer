from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from ap_coder.capture import analyze, build_layout, locate, read_fields
from ap_coder.capture.confidence import run_checks
from ap_coder.capture.normalize import (
    find_gst_numbers,
    gst_valid,
    luhn_ok,
    norm_gst,
    norm_id,
    norm_name,
    parse_amount,
    parse_date,
    parse_dates,
    qst_valid,
    same_value,
)
from ap_coder.capture.types import VERIFIED, Box, CaptureResult, FieldResult

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
TODAY = dt.date(2026, 10, 9)


# ---------------------------------------------------------------- normalize


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1,234.56", 1234.56),
        ("1 234,56 $", 1234.56),
        ("$1,234.56", 1234.56),
        ("CAD 1,234.56", 1234.56),
        ("(12.00)", -12.0),
        ("-12.00", -12.0),
        ("12.00 CR", -12.0),
        ("1.234,56", 1234.56),
        ("2 759,40 $", 2759.40),
        ("45", 45.0),
        ("no amount", None),
    ],
)
def test_parse_amount(text, value):
    assert parse_amount(text) == value


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("2026-10-07", "2026-10-07"),
        ("Oct 7, 2026", "2026-10-07"),
        ("October 7th, 2026", "2026-10-07"),
        ("7 octobre 2026", "2026-10-07"),
        ("1er octobre 2026", "2026-10-01"),
        ("22 septembre 2026", "2026-09-22"),
        ("28/09/2026", "2026-09-28"),
        ("09/28/2026", "2026-09-28"),
        ("07-Oct-2026", "2026-10-07"),
    ],
)
def test_parse_date(text, iso):
    assert parse_date(text) == iso


def test_ambiguous_numeric_date_is_flagged_not_guessed():
    (d, _, _, ambiguous), *_ = parse_dates("03/04/2026")
    assert ambiguous
    assert parse_date("03/04/2026") is None
    assert parse_date("03/04/2026", prefer_day_first=True) == "2026-04-03"
    assert parse_date("03/04/2026", prefer_day_first=False) == "2026-03-04"


def test_registration_numbers():
    assert luhn_ok("046454286")
    assert not luhn_ok("123456789")
    assert norm_gst("GST # 123 456 782 RT 0001") == "123456782RT0001"
    assert gst_valid("123456782RT0001")
    assert not gst_valid("123456789RT0001")  # bad check digit: misread or invented
    assert qst_valid("1234567890 TQ 0001")
    found = find_gst_numbers("TPS/GST : 987654321 RT0001 TVQ/QST : 1234567890 TQ0001")
    assert found[0][0] == "987654321RT0001"


def test_ids_and_names_compare_loosely():
    assert norm_id("INV-2026/0912") == norm_id("inv 2026 0912")
    assert norm_name("Services Informatiques Laurentides Inc.") == norm_name("SERVICES INFORMATIQUES LAURENTIDES")
    assert same_value("grand_total", "2 759,40 $", 2759.4)
    assert same_value("invoice_date", "Oct 1, 2026", "2026-10-01")
    assert not same_value("invoice_number", "NW-0912", "NW-0921")


# ---------------------------------------------------------------- reading the sample invoices


def _top(cands, field):
    return cands[field][0].value if cands.get(field) else None


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        (
            "laurentides_QC_TPS_TVQ_SIL-4471",
            {
                "invoice_number": "SIL-4471",
                "invoice_date": "2026-10-01",
                "subtotal": 2400.0,
                "gst_amount": 120.0,
                "qst_amount": 239.4,
                "grand_total": 2759.4,
                "gst_hst_registration_number": "987654321RT0001",
                "qst_registration_number": "1234567890TQ0001",
            },
        ),
        (
            "cascade_US_SalesTax_INV-30981",
            {"invoice_number": "INV-30981", "subtotal": 6860.0, "tax_total": 710.01, "grand_total": 7570.01},
        ),
        ("northwind_ON_HST_CN-2026-0047", {"invoice_date": "2026-09-28", "po_number": "PO-88213"}),
        ("harbourview_NS_HST_HPS-2026-0347", {"subtotal": 9614.5}),
    ],
)
def test_rule_reader_on_samples(stem, expected):
    cands = read_fields(build_layout(SAMPLES / f"{stem}.pdf", ocr=False))
    for field, value in expected.items():
        assert same_value(field, _top(cands, field), value), (field, _top(cands, field), value)


def test_every_reading_has_a_box_on_the_right_page():
    layout = build_layout(SAMPLES / "laurentides_QC_TPS_TVQ_SIL-4471.pdf", ocr=False)
    cands = read_fields(layout)
    for field in ("invoice_number", "grand_total", "subtotal"):
        (box,) = cands[field][0].boxes
        assert box.page == 1 and 0 <= box.x0 < box.x1 <= 1 and 0 <= box.y0 < box.y1 <= 1
    # the total's box holds the printed total
    total_box = cands["grand_total"][0].boxes[0]
    inside = [w.text for w in layout.pages[0].words if total_box.x0 - 0.001 <= w.box.cx <= total_box.x1 + 0.001
              and total_box.y0 - 0.001 <= w.box.cy <= total_box.y1 + 0.001]  # fmt: skip
    assert "759,40" in " ".join(inside)


def test_locate_finds_a_value_from_another_reader():
    layout = build_layout(SAMPLES / "northwind_ON_HST_NW-2026-0912.pdf", ocr=False)
    found = locate(layout, "invoice_number", "nw-2026-0912")
    assert found and found[0].boxes
    assert locate(layout, "grand_total", 999999.99) == []


# ---------------------------------------------------------------- fusion and checks


def test_totals_that_add_up_are_verified():
    result = analyze(SAMPLES / "laurentides_QC_TPS_TVQ_SIL-4471.pdf", ocr=False, today=TODAY)
    for field in ("subtotal", "gst_amount", "qst_amount", "grand_total"):
        assert result.fields[field].status == VERIFIED, (field, result.fields[field].reasons)
    assert any(c["code"] == "TOTALS_ADD_UP" and c["ok"] for c in result.checks)


def test_agreeing_ai_reading_lifts_a_field_and_disagreement_lowers_it():
    pdf = SAMPLES / "laurentides_QC_TPS_TVQ_SIL-4471.pdf"
    alone = analyze(pdf, ocr=False, today=TODAY).fields["invoice_number"]
    agree = analyze(pdf, ocr=False, ai_values={"invoice_number": "SIL-4471"}, today=TODAY).fields["invoice_number"]
    differ = analyze(pdf, ocr=False, ai_values={"invoice_number": "SIL-4417"}, today=TODAY).fields["invoice_number"]
    assert agree.confidence > alone.confidence > differ.confidence
    assert agree.status == VERIFIED
    assert differ.status != VERIFIED


def test_failed_totals_check_sends_fields_to_check():
    checks = run_checks({"subtotal": 100.0, "gst_amount": 5.0, "grand_total": 106.0}, [], today=TODAY)
    assert any(c["code"] == "TOTALS_ADD_UP" and not c["ok"] for c in checks)
    checks = run_checks({"subtotal": 100.0, "gst_amount": 5.0, "grand_total": 105.0}, [], today=TODAY)
    assert all(c["ok"] for c in checks if c["code"] in ("TOTALS_ADD_UP", "TAX_RATE"))


def test_vendor_master_mismatch_is_reported():
    checks = run_checks({"gst_hst_registration_number": "987654321RT0001", "vendor_name": "Acme"}, [],
                        vendor={"gst_number": "123456782RT0001", "name": "Acme Inc."}, today=TODAY)  # fmt: skip
    assert {c["code"]: c["ok"] for c in checks}["VENDOR_GST_MATCH"] is False
    assert {c["code"]: c["ok"] for c in checks}["VENDOR_NAME_MATCH"] is True


def test_capture_result_round_trips_json():
    result = analyze(SAMPLES / "pacific_BC_GST_PST_PO-77120.pdf", ocr=False, today=TODAY)
    again = CaptureResult.from_dict(result.to_dict())
    assert again.to_dict() == result.to_dict()
    assert sum(result.status_counts().values()) == len(result.fields)


def test_field_result_and_box_round_trip():
    fr = FieldResult("grand_total", 12.5, 0.991, VERIFIED, [Box(1, 0.1, 0.2, 0.3, 0.4)], {"rules": "12.50"}, ["x"])
    assert FieldResult.from_dict(fr.to_dict()) == fr


def test_rotated_page_boxes_line_up(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Invoice No: ROT-123", fontsize=12)
    page.insert_text((72, 140), "Total: 1,000.00", fontsize=12)
    page.set_rotation(90)
    path = tmp_path / "rotated.pdf"
    doc.save(path)
    layout = build_layout(path, ocr=False)
    word = next(w for w in layout.pages[0].words if "ROT-123" in w.text)
    # Displayed landscape: the text runs down the right side of the shown page.
    assert word.box.x0 > 0.5
    assert 0 <= word.box.y0 < word.box.y1 <= 1
