import json

import pytest
from pydantic import ValidationError

from ap_coder.core.reference_data import UNASSIGNED, ReferenceData
from ap_coder.core.schema import InvoiceCoding, build_json_schema, line_gl_codes, response_format

TARGET_HEADER = [
    "vendor_name", "invoice_number", "invoice_date", "po_number", "payment_terms", "due_date", "currency",
    "supplier_province", "ship_to_province", "gst_hst_registration_number", "qst_registration_number",
    "pst_registration_number", "original_invoice_number", "remit_bank_account",
    "subtotal", "tax_lines", "tax_total", "grand_total", "confidence_score", "line_items",
]  # fmt: skip
TARGET_LINE = [
    "line_number", "description", "quantity", "unit_price", "amount",
    "predicted_gl_code", "predicted_cost_center", "taxes_applied", "tax_exempt", "tax_status",
    "reasoning_justification",
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
    [(("supplier_province",), "Narnia"), (("tax_lines", 0, "tax_type"), "VAT"), (("tax_lines", 0, "rate"), 13)],
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


def test_a_tax_amount_read_on_a_line_is_kept_and_an_invoice_without_one_is_stored_as_before(ground_truth):
    """``LineItem.taxes_read``: the tax printed on a line, tax by tax. None (most invoices print one total) is left
    out of what is stored and exported, byte for byte, and the model is never asked for it."""
    assert "taxes_read" not in build_json_schema()["properties"]["line_items"]["items"]["properties"]
    coding = InvoiceCoding.model_validate(ground_truth)
    assert coding.line_items[0].taxes_read is None
    stored = json.dumps(coding.to_output())
    assert all(list(line) == TARGET_LINE for line in json.loads(stored)["line_items"])
    assert json.dumps(InvoiceCoding.model_validate(json.loads(stored)).to_output()) == stored
    assert json.loads(coding.model_dump_json()) == {**json.loads(stored), "pay_from_account": ""}
    assert "taxes_read" not in coding.line_items[0].model_dump()
    assert "taxes_read" not in coding.model_dump_json()

    ground_truth["line_items"][0]["taxes_read"] = {" hst ": 26, "GST": 0.0}
    out = InvoiceCoding.model_validate(ground_truth).to_output()
    assert out["line_items"][0]["taxes_read"] == {"GST": 0.0, "HST": 26.0}
    assert list(out["line_items"][0]) == [*TARGET_LINE[:-1], "taxes_read", TARGET_LINE[-1]]
    assert InvoiceCoding.model_validate(out).to_output() == out
    assert all("taxes_read" not in line for line in out["line_items"][1:])


def test_a_line_stores_each_of_its_optional_keys_only_when_it_says_something(ground_truth):
    """``taxes_read``, ``candidates`` and ``decided_by`` are each left out of a stored line that has none, by one
    serializer: a model keeps only the last one defined, and with two (one for the tax read, one for the other two)
    every line stored was given ``"candidates": []`` and ``"decided_by": {}`` (B-2183)."""
    line = InvoiceCoding.model_validate(ground_truth).line_items[0]
    assert list(line.model_dump()) == TARGET_LINE
    assert list(InvoiceCoding.model_validate(ground_truth).model_dump()["line_items"][0]) == TARGET_LINE
    for name, value in (("taxes_read", {"GST": 1.0}), ("candidates", [{"account": "100.7340.3307"}]),
                        ("decided_by", {"source": "person"})):  # fmt: skip
        kept = line.model_copy(update={name: value}).model_dump()
        assert set(kept) - set(TARGET_LINE) == {name} and kept[name] == value, name


@pytest.mark.parametrize("bad", [{"VAT": 1.0}, {"GST": "a lot"}, ["GST"]])
def test_a_tax_amount_read_that_is_no_tax_or_no_amount_is_rejected(ground_truth, bad):
    ground_truth["line_items"][0]["taxes_read"] = bad
    with pytest.raises(ValidationError):
        InvoiceCoding.model_validate(ground_truth)


def test_a_line_keeps_its_remark_and_one_without_is_stored_byte_for_byte_as_before(ground_truth):
    """``LineItem.remark`` and ``remark_from`` (D-213): the model is never asked for them, and a line with no remark
    is stored as it always was. So is one with no candidates and no decision (B-2187: the line had two serializers,
    of which pydantic keeps the last, so every line was stored with ``"candidates": [], "decided_by": {}``)."""
    asked = build_json_schema()["properties"]["line_items"]["items"]["properties"]
    assert not {"remark", "remark_from", "candidates", "decided_by"} & set(asked)
    old = json.dumps(InvoiceCoding.model_validate(ground_truth).to_output())  # an invoice stored before
    assert all(list(line) == TARGET_LINE for line in json.loads(old)["line_items"])
    assert json.dumps(InvoiceCoding.model_validate(json.loads(old)).to_output()) == old

    ground_truth["line_items"][0].update(remark="May janitorial 410 Elm", remark_from="supplier")
    out = InvoiceCoding.model_validate(ground_truth).to_output()
    first = out["line_items"][0]
    assert list(first) == [*TARGET_LINE, "remark", "remark_from"]
    assert (first["remark"], first["remark_from"]) == ("May janitorial 410 Elm", "supplier")
    assert all(list(line) == TARGET_LINE for line in out["line_items"][1:])
    assert InvoiceCoding.model_validate(out).to_output() == out
    ground_truth["line_items"][0]["remark_from"] = "somebody"
    with pytest.raises(ValidationError):
        InvoiceCoding.model_validate(ground_truth)
