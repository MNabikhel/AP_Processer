import copy

from ap_coder.extraction import result_from_raw
from ap_coder.reference_data import UNASSIGNED
from ap_coder.schema import InvoiceCoding
from ap_coder.validation import validate_coding


def _codes(report):
    return {i.code for i in report.issues}


def test_clean_invoice_passes(reference, ground_truth):
    report = validate_coding(InvoiceCoding.model_validate(ground_truth), reference)
    assert report.issues == []
    assert report.requires_review is False
    assert report.adjusted_confidence == 1.0


def test_unknown_and_unassigned_codes(reference, ground_truth):
    gt = copy.deepcopy(ground_truth)
    gt["line_items"][0]["predicted_gl_code"] = "9999"
    gt["line_items"][1]["predicted_cost_center"] = UNASSIGNED
    report = validate_coding(InvoiceCoding.model_validate(gt), reference)
    assert {"GL_UNKNOWN", "CC_UNASSIGNED"} <= _codes(report)
    assert report.requires_review is True


def test_inactive_account_is_unknown(reference, ground_truth):
    gt = copy.deepcopy(ground_truth)
    gt["line_items"][0]["predicted_gl_code"] = "6999"
    assert "GL_UNKNOWN" in _codes(validate_coding(InvoiceCoding.model_validate(gt), reference))


def test_totals_reconciliation(reference, ground_truth):
    gt = copy.deepcopy(ground_truth)
    gt["line_items"].pop()  # drop the freight line -> subtotal no longer matches
    gt["grand_total"] = 1.0
    report = validate_coding(InvoiceCoding.model_validate(gt), reference)
    assert {"SUBTOTAL_MISMATCH", "TOTAL_MISMATCH"} <= _codes(report)
    assert report.adjusted_confidence < 0.5


def test_line_math_and_numbering(reference, ground_truth):
    gt = copy.deepcopy(ground_truth)
    gt["line_items"][2]["quantity"] = 2
    gt["line_items"][3]["line_number"] = 9
    report = validate_coding(InvoiceCoding.model_validate(gt), reference)
    assert {"LINE_MATH", "LINE_NUMBERING"} <= _codes(report)


def test_low_model_confidence_triggers_review(reference, ground_truth):
    gt = dict(ground_truth, confidence_score=0.7)
    report = validate_coding(InvoiceCoding.model_validate(gt), reference, review_threshold=0.85)
    assert report.issues == [] and report.requires_review is True


def test_cross_check_against_prebuilt_invoice(reference, ground_truth):
    raw = {
        "modelId": "prebuilt-invoice",
        "content": "",
        "pages": [{"words": [{"confidence": 0.6}]}],
        "documents": [
            {
                "fields": {
                    "InvoiceId": {"valueString": "NW 2026 0912", "confidence": 0.9},
                    "InvoiceDate": {"valueDate": "2026-09-15", "confidence": 0.9},
                    "InvoiceTotal": {"valueCurrency": {"amount": 18000.0}, "confidence": 0.9},
                }
            }
        ],
    }
    report = validate_coding(InvoiceCoding.model_validate(ground_truth), reference, result_from_raw("x.pdf", raw))
    codes = _codes(report)
    assert "DI_INVOICE_ID_DIFFERS" not in codes  # punctuation-insensitive match
    assert {"DI_DATE_DIFFERS", "DI_AMOUNT_DIFFERS", "LOW_OCR_CONFIDENCE"} <= codes
