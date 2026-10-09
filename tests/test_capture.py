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


# ---------------------------------------------------------------- reader rules found by the benchmark


def _pdf(tmp_path, lines: list[tuple[float, float, str]], name: str = "inv.pdf") -> Path:
    """A one-page PDF with text at (x, y) points."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text in lines:
        page.insert_text((x, y), text, fontsize=10)
    path = tmp_path / name
    doc.save(path)
    return path


def _read(path: Path) -> dict:
    return {f: (c[0].value if c else None) for f, c in read_fields(build_layout(path, ocr=False)).items()}


def test_dotted_and_combined_tax_labels(tmp_path):
    lines = [(60, 60, "Acme Supply Ltd."), (380, 500, "Subtotal"), (500, 500, "1,000.00"), (380, 515, "G.S.T. 5%"),
             (500, 515, "50.00"), (380, 530, "Total Due"), (500, 530, "1,050.00")]  # fmt: skip
    got = _read(_pdf(tmp_path, lines))
    assert got["gst_amount"] == 50.0 and got["grand_total"] == 1050.0
    assert not got.get("hst_amount")
    # "GST/HST" with no rate printed: the amount's rate on the subtotal says it is GST (5%), not HST.
    lines = [(60, 60, "Acme Supply Ltd."), (380, 500, "Subtotal"), (500, 500, "200.00"), (380, 515, "GST/HST"),
             (500, 515, "10.00"), (380, 530, "Total"), (500, 530, "210.00")]  # fmt: skip
    got = _read(_pdf(tmp_path, lines, "b.pdf"))
    assert got["gst_amount"] == 10.0
    assert not got.get("hst_amount")


def test_registration_line_is_not_a_tax_amount_and_customer_numbers_are_ignored(tmp_path):
    got = _read(_pdf(tmp_path, [(60, 60, "Acme Supply Ltd."), (60, 80, "GST/HST Registration 12345 6782 RT 0001"),
                                (60, 160, "Customer GST #: 862371887 RT0001")]))  # fmt: skip
    assert got["gst_hst_registration_number"] == "123456782RT0001"
    assert not got.get("hst_amount") and not got.get("gst_amount")
    assert find_gst_numbers("BN 12345 6782 RT 0001")[0][0] == "123456782RT0001"


def test_document_date_order():
    from ap_coder.capture.normalize import infer_day_first

    assert infer_day_first("Date: 25/03/2026  Due: 04/04/2026") is True
    assert infer_day_first("Date: 03/25/2026  Due: 04/04/2026") is False
    assert infer_day_first("Date: 03/04/2026") is None


def test_ambiguous_dates_are_resolved_by_the_terms(tmp_path):
    got = _read(_pdf(tmp_path, [(60, 60, "Acme Supply Ltd."), (60, 120, "Invoice Date: 11/07/2025"),
                                (60, 135, "Due Date: 12/07/2025"), (60, 150, "Terms: Net 30")]))  # fmt: skip
    assert got["invoice_date"] == "2025-11-07" and got["due_date"] == "2025-12-07"


def test_logo_initials_and_customer_block_do_not_make_the_vendor(tmp_path):
    got = _read(_pdf(tmp_path, [(60, 60, "BT Bluewater Telecom"), (60, 75, "12 Main St, Toronto, ON M5V 1A1"),
                                (60, 120, "To:"), (100, 120, "Hartwell Manufacturing Inc.")]))  # fmt: skip
    assert got["vendor_name"] == "Bluewater Telecom"


def test_po_keeps_its_prefix_and_ignores_po_dates(tmp_path):
    got = _read(_pdf(tmp_path, [(60, 60, "Acme Supply Ltd."), (300, 120, "Cust. P.O.:"), (400, 120, "PO-45059"),
                                (300, 140, "PO Date:"), (400, 140, "Aug 27, 2025")]))  # fmt: skip
    assert got["po_number"] == "PO-45059"


def test_measured_calibration_uses_the_lower_bound():
    from ap_coder.capture import confidence

    key = confidence.evidence_key("invoice_number", ["rules"], "label-right", layout_source="text")
    assert key == "invoice_number|rules|label-right|text|"
    confidence.calibration_table.cache_clear()
    old = confidence.calibration_table
    try:
        confidence.calibration_table = lambda: {key: (500, 500)}
        assert 0.99 < confidence.calibrate(0.5, key) < 1.0
        confidence.calibration_table = lambda: {key: (10, 10)}  # too few cases: the formula stands
        assert confidence.calibrate(0.5, key) == 0.5
    finally:
        confidence.calibration_table = old
    assert confidence.wilson_lower(0, 0) == 0.0


def test_passing_weak_checks_neither_confirm_nor_fail():
    from ap_coder.capture.confidence import fuse
    from ap_coder.capture.types import Reading

    box = [Box(1, 0.1, 0.1, 0.2, 0.12)]
    reading = Reading("invoice_date", "2026-09-01", "Sep 1, 2026", box, 1.0, "label-right")
    by_source = {"rules": {"invoice_date": [reading]}}
    results, _ = fuse(by_source, [], fields=("invoice_date",), today=TODAY)
    fr = results["invoice_date"]
    assert not any(r.startswith("check failed") for r in fr.reasons)
    assert not any("confirmed" in r for r in fr.reasons)


def test_ocr_thousands_separator():
    from ap_coder.capture.normalize import find_amounts

    assert parse_amount("1;864.71") == 1864.71
    assert parse_amount("$1:664.92") == 1664.92
    assert [a[0] for a in find_amounts("Total 1;864.71")] == [1864.71]


def test_fusion_takes_the_runner_up_amounts_that_add_up():
    from ap_coder.capture.confidence import fuse
    from ap_coder.capture.types import Reading

    def r(field, value, score, method="label-right"):
        return Reading(field, value, f"{value:.2f}", [Box(1, 0.7, 0.5, 0.8, 0.52)], score, method)

    by_source = {
        "rules": {"subtotal": [r("subtotal", 100.0, 0.8)], "gst_amount": [r("gst_amount", 5.0, 0.9)],
                  "grand_total": [r("grand_total", 105.0, 0.9)]},
        "template": {"subtotal": [r("subtotal", 150.0, 0.99, "template")]},
    }  # fmt: skip
    results, checks = fuse(by_source, [], fields=("subtotal", "gst_amount", "grand_total"), today=TODAY)
    assert results["subtotal"].value == 100.0
    assert any(c["code"] == "TOTALS_ADD_UP" and c["ok"] for c in checks)


def test_confirmed_supplier_takes_the_vendor_master_name():
    from ap_coder.capture.confidence import fuse
    from ap_coder.capture.types import Reading

    box = [Box(1, 0.1, 0.05, 0.4, 0.08)]
    by_source = {"rules": {
        "vendor_name": [Reading("vendor_name", "Harbourfront Water&Wastewater", "x", box, 0.9, "top-of-page")],
        "gst_hst_registration_number": [Reading("gst_hst_registration_number", "123456782RT0001", "x", box, 0.95,
                                                "pattern")],
    }}  # fmt: skip
    vendor = {"name": "Harbourfront Water & Wastewater", "gst_number": "123456782RT0001"}
    fields = ("vendor_name", "gst_hst_registration_number")
    results, _ = fuse(by_source, [], vendor=vendor, fields=fields, today=TODAY)
    assert results["vendor_name"].value == "Harbourfront Water & Wastewater"
    assert any("vendor master" in r for r in results["vendor_name"].reasons)


def test_customer_number_glued_by_ocr_is_ignored():
    from ap_coder.capture.reader import _CUSTOMER_WORD, plain

    assert _CUSTOMER_WORD.search(plain("YourGSTNo.:"))
    assert _CUSTOMER_WORD.search(plain("C1ientBN:"))
