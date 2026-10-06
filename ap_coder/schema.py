"""Target output schema.

Two representations are kept side by side:

* ``build_json_schema`` produces the JSON Schema sent to Azure OpenAI with
  ``strict: true`` so the service itself guarantees the response shape.
* ``InvoiceCoding`` is the Pydantic mirror used to re-validate the response
  locally (dates, ranges) before anything downstream consumes it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .reference_data import UNASSIGNED, ReferenceData

SCHEMA_NAME = "ap_invoice_coding"

# Structured Outputs limits for enums (documented by OpenAI/Azure OpenAI). We
# stay well under them and fall back to free-text codes + local validation.
_MAX_ENUM_VALUES_PER_FIELD = 500
_MAX_ENUM_CHARS_PER_FIELD = 7_500


def _code_property(description: str, codes: list[str] | None) -> dict[str, Any]:
    prop: dict[str, Any] = {"type": "string", "description": description}
    if codes is not None:
        prop["enum"] = codes
    return prop


def enum_values_or_none(codes: list[str]) -> list[str] | None:
    values = [*codes, UNASSIGNED]
    if len(values) > _MAX_ENUM_VALUES_PER_FIELD or sum(len(v) for v in values) > _MAX_ENUM_CHARS_PER_FIELD:
        return None
    return values


def build_json_schema(
    reference: ReferenceData | None = None,
    *,
    constrain_codes: bool = True,
    include_tax_rate: bool = False,
) -> dict[str, Any]:
    """Build the strict JSON Schema for the target output.

    When ``constrain_codes`` is set and reference data is supplied, the GL code
    and cost center fields become enums of the valid codes (plus
    ``UNASSIGNED``), so the model physically cannot invent an account.
    """
    gl_codes = cc_codes = None
    if reference is not None and constrain_codes:
        gl_codes = enum_values_or_none(reference.chart_of_accounts.codes)
        cc_codes = enum_values_or_none(reference.cost_centers.codes)

    line_properties: dict[str, Any] = {
        "line_number": {"type": "integer", "description": "1-based position of the line on the invoice."},
        "description": {"type": "string", "description": "Line description as printed on the invoice."},
        "quantity": {"type": "number", "description": "Billed quantity. Use 1 if the invoice states none."},
        "unit_price": {"type": "number", "description": "Price per unit, excluding tax."},
        "amount": {"type": "number", "description": "Line net amount, excluding tax."},
        "predicted_gl_code": _code_property(
            f"GL account code from the Chart of Accounts, or {UNASSIGNED} if none fits.", gl_codes
        ),
        "predicted_cost_center": _code_property(
            f"Cost center code from the Cost Center list, or {UNASSIGNED} if none fits.", cc_codes
        ),
    }
    if include_tax_rate:
        line_properties["predicted_tax_rate"] = {
            "type": "number",
            "description": "Applicable tax rate as a decimal fraction (0.2 = 20%). 0 if exempt.",
        }
    line_properties["reasoning_justification"] = {
        "type": "string",
        "description": "One or two sentences explaining the GL, cost center and tax determination.",
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "vendor_name",
            "invoice_number",
            "invoice_date",
            "currency",
            "subtotal",
            "tax_total",
            "grand_total",
            "confidence_score",
            "line_items",
        ],
        "properties": {
            "vendor_name": {"type": "string", "description": "Legal or trading name of the supplier."},
            "invoice_number": {"type": "string", "description": "Supplier's invoice identifier."},
            "invoice_date": {"type": "string", "description": "Invoice issue date formatted YYYY-MM-DD."},
            "currency": {"type": "string", "description": "ISO 4217 currency code, e.g. USD, EUR, GBP."},
            "subtotal": {"type": "number", "description": "Total before tax."},
            "tax_total": {"type": "number", "description": "Total tax charged."},
            "grand_total": {"type": "number", "description": "Amount due including tax."},
            "confidence_score": {
                "type": "number",
                "description": "Overall confidence 0.0-1.0 in extraction accuracy and GL coding.",
            },
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(line_properties.keys()),
                    "properties": line_properties,
                },
            },
        },
    }


def response_format(schema: dict[str, Any]) -> dict[str, Any]:
    """Wrap a schema into the Chat Completions ``response_format`` payload."""
    return {
        "type": "json_schema",
        "json_schema": {"name": SCHEMA_NAME, "strict": True, "schema": schema},
    }


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_number: int = Field(ge=1)
    description: str
    quantity: float
    unit_price: float
    amount: float
    predicted_gl_code: str
    predicted_cost_center: str
    predicted_tax_rate: float | None = Field(default=None, ge=0, le=1)
    reasoning_justification: str


class InvoiceCoding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_name: str
    invoice_number: str
    invoice_date: str
    currency: str
    subtotal: float
    tax_total: float
    grand_total: float
    confidence_score: float = Field(ge=0, le=1)
    line_items: list[LineItem]

    @field_validator("invoice_date")
    @classmethod
    def _iso_date(cls, value: str) -> str:
        try:
            dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invoice_date must be YYYY-MM-DD, got {value!r}") from exc
        if len(value) != 10:
            raise ValueError(f"invoice_date must be YYYY-MM-DD, got {value!r}")
        return value

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        return value.strip().upper()

    def to_output(self) -> dict[str, Any]:
        """Serialise to the target schema (drops optional fields that were not requested)."""
        data = self.model_dump()
        for line in data["line_items"]:
            if line.get("predicted_tax_rate") is None:
                line.pop("predicted_tax_rate", None)
        return data
