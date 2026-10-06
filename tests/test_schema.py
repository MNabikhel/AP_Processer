import pytest
from pydantic import ValidationError

from ap_coder.reference_data import UNASSIGNED
from ap_coder.schema import InvoiceCoding, build_json_schema, response_format

TARGET_HEADER = [
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "currency",
    "subtotal",
    "tax_total",
    "grand_total",
    "confidence_score",
    "line_items",
]
TARGET_LINE = [
    "line_number",
    "description",
    "quantity",
    "unit_price",
    "amount",
    "predicted_gl_code",
    "predicted_cost_center",
    "reasoning_justification",
]


def _assert_strict(node):
    """Structured Outputs strict mode: every object closed and all properties required."""
    if node.get("type") == "object":
        assert node["additionalProperties"] is False
        assert sorted(node["required"]) == sorted(node["properties"])
        for child in node["properties"].values():
            _assert_strict(child)
    if node.get("type") == "array":
        _assert_strict(node["items"])


def test_schema_matches_target_structure():
    schema = build_json_schema()
    assert list(schema["properties"]) == TARGET_HEADER
    assert list(schema["properties"]["line_items"]["items"]["properties"]) == TARGET_LINE
    _assert_strict(schema)


def test_schema_with_tax_rate_field_is_strict():
    schema = build_json_schema(include_tax_rate=True)
    line = schema["properties"]["line_items"]["items"]
    assert "predicted_tax_rate" in line["required"]
    _assert_strict(schema)


def test_codes_constrained_to_reference(reference):
    schema = build_json_schema(reference)
    line = schema["properties"]["line_items"]["items"]["properties"]
    assert line["predicted_gl_code"]["enum"] == [*reference.chart_of_accounts.codes, UNASSIGNED]
    assert line["predicted_cost_center"]["enum"] == [*reference.cost_centers.codes, UNASSIGNED]
    assert (
        "enum"
        not in build_json_schema(reference, constrain_codes=False)["properties"]["line_items"]["items"]["properties"][
            "predicted_gl_code"
        ]
    )


def test_response_format_wrapper():
    fmt = response_format(build_json_schema())
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True


def test_ground_truth_round_trips(ground_truth):
    coding = InvoiceCoding.model_validate(ground_truth)
    out = coding.to_output()
    assert list(out) == TARGET_HEADER
    assert list(out["line_items"][0]) == TARGET_LINE
    assert out == ground_truth


@pytest.mark.parametrize("bad_date", ["14/09/2026", "2026-9-14", "2026-02-30"])
def test_invalid_dates_rejected(ground_truth, bad_date):
    ground_truth["invoice_date"] = bad_date
    with pytest.raises(ValidationError):
        InvoiceCoding.model_validate(ground_truth)


def test_extra_fields_rejected(ground_truth):
    ground_truth["notes"] = "x"
    with pytest.raises(ValidationError):
        InvoiceCoding.model_validate(ground_truth)
