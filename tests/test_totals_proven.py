"""The money is proven on every invoice that goes without a person: subtotal (or, when no subtotal is printed, the
lines) + charges + taxes = total. An invoice whose totals could not be checked always goes to a person."""

from ap_coder.capture.confidence import run_checks
from ap_coder.capture.supplier import AUTONOMOUS, should_auto_approve
from ap_coder.capture.types import CaptureResult, FieldResult, LineReading


def _lines(*amounts: float) -> list[LineReading]:
    return [LineReading(f"Item {n}", 1.0, a, a, [], 0.95, "words") for n, a in enumerate(amounts, 1)]


def _totals(checks: list[dict]) -> list[dict]:
    return [c for c in checks if c["code"] == "TOTALS_ADD_UP"]


def test_without_a_subtotal_the_lines_and_taxes_must_make_the_total():
    (ok,) = _totals(run_checks({"grand_total": 1130.0, "hst_amount": 130.0}, _lines(600.0, 400.0)))
    assert ok["ok"] and ok["detail"] == "lines 1,000.00 + tax 130.00 = total 1,130.00"
    (wrong,) = _totals(run_checks({"grand_total": 1130.0, "hst_amount": 130.0}, _lines(600.0, 300.0)))
    assert not wrong["ok"]


def test_with_neither_a_subtotal_nor_lines_the_totals_cannot_be_checked():
    assert _totals(run_checks({"grand_total": 1130.0, "hst_amount": 130.0}, [])) == []


def test_a_printed_subtotal_is_still_what_the_total_is_checked_against():
    (ok,) = _totals(run_checks({"subtotal": 1000.0, "grand_total": 1050.0, "gst_amount": 50.0}, _lines(999.0)))
    assert ok["ok"] and ok["detail"].startswith("subtotal 1,000.00")


def _verified(*names: str) -> dict[str, FieldResult]:
    return {name: FieldResult(name, "x", 0.99, "verified") for name in names}


def test_an_invoice_whose_totals_were_not_checked_is_never_approved_without_a_person():
    fields = _verified("vendor_name", "invoice_number", "invoice_date", "grand_total")
    ok, reason = should_auto_approve(AUTONOMOUS, CaptureResult(fields, checks=[]), checks_ok=True)
    assert not ok and "totals could not be checked" in reason
    proven = CaptureResult(fields, checks=[{"code": "TOTALS_ADD_UP", "ok": True, "detail": ""}])
    assert should_auto_approve(AUTONOMOUS, proven, checks_ok=True)[0]
