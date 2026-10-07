import pytest
from pydantic import ValidationError

from ap_coder.reference_data import UNASSIGNED, ReferenceData
from ap_coder.schema import InvoiceCoding, build_json_schema, line_gl_codes, response_format

TARGET_HEADER = [
    "vendor_name", "invoice_number", "invoice_date", "po_number", "currency", "supplier_province",
    "ship_to_province", "gst_hst_registration_number", "qst_registration_number", "subtotal", "tax_lines", "tax_total",
    "grand_total", "confidence_score", "line_items",
]  # fmt: skip
TARGET_LINE = [
    "line_number", "description", "quantity", "unit_price", "amount",
    "predicted_gl_code", "predicted_cost_center", "taxes_applied", "reasoning_justification",
]  # fmt: skip
ORIGINAL_SPEC_HEADER = [
    "vendor_name", "invoice_number", "invoice_date", "currency", "subtotal",
    "tax_total", "grand_total", "confidence_score", "line_items",
]  # fmt: skip


def _assert_strict(node):
    """Structured Outputs strict mode: every object closed and all properties required."""
    if node.get("type") == "object":
        assert node["additionalProperties"] is False
        assert sorted(node["required"]) == sorted(node["properties"])
        for child in node["properties"].values():
            _assert_strict(child)
    if node.get("type") == "array":
        _assert_strict(node["items"])


def test_schema_structure_is_strict_and_keeps_original_fields():
    schema = build_json_schema()
    assert list(schema["properties"]) == TARGET_HEADER
    assert set(ORIGINAL_SPEC_HEADER) <= set(schema["properties"])
    assert list(schema["properties"]["line_items"]["items"]["properties"]) == TARGET_LINE
    tax_line = schema["properties"]["tax_lines"]["items"]["properties"]
    assert tax_line["tax_type"]["enum"] == ["GST", "HST", "PST", "QST", "OTHER"]
    _assert_strict(schema)


def test_codes_constrained_to_reference_and_tax_accounts_excluded(reference):
    schema = build_json_schema(reference)
    line = schema["properties"]["line_items"]["items"]["properties"]
    assert line["predicted_gl_code"]["enum"] == [*line_gl_codes(reference), UNASSIGNED]
    assert "2310" not in line["predicted_gl_code"]["enum"]  # GST/HST recoverable is a tax account
    assert "6010" in line["predicted_gl_code"]["enum"]
    assert line["predicted_cost_center"]["enum"] == [*reference.cost_centers.codes, UNASSIGNED]
    unconstrained = build_json_schema(reference, constrain_codes=False)
    assert "enum" not in unconstrained["properties"]["line_items"]["items"]["properties"]["predicted_gl_code"]


def test_cost_center_fixed_empty_when_not_configured(reference):
    no_cc = ReferenceData(reference.chart_of_accounts, None, reference.tax, reference.notes)
    line = build_json_schema(no_cc)["properties"]["line_items"]["items"]["properties"]
    assert line["predicted_cost_center"]["enum"] == [""]


def test_response_format_wrapper():
    fmt = response_format(build_json_schema())
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True


def test_ground_truth_round_trips(ground_truth):
    out = InvoiceCoding.model_validate(ground_truth).to_output()
    assert list(out) == TARGET_HEADER
    assert list(out["line_items"][0]) == TARGET_LINE
    assert out == ground_truth


@pytest.mark.parametrize("bad_date", ["14/09/2026", "2026-9-14", "2026-02-30"])
def test_invalid_dates_rejected(ground_truth, bad_date):
    ground_truth["invoice_date"] = bad_date
    with pytest.raises(ValidationError):
        InvoiceCoding.model_validate(ground_truth)


@pytest.mark.parametrize(
    ("path", "value"),
    [(("supplier_province",), "Ontario"), (("tax_lines", 0, "tax_type"), "VAT"), (("tax_lines", 0, "rate"), 13)],
)
def test_invalid_tax_values_rejected(ground_truth, path, value):
    node = ground_truth
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValidationError):
        InvoiceCoding.model_validate(ground_truth)


def test_extra_fields_rejected(ground_truth):
    ground_truth["notes"] = "x"
    with pytest.raises(ValidationError):
        InvoiceCoding.model_validate(ground_truth)
