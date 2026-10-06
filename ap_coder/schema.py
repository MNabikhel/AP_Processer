"""Target output schema.

Two representations are kept side by side:

* ``build_json_schema`` produces the JSON Schema sent to Azure OpenAI with
  ``strict: true`` so the service itself guarantees the response shape.
* ``InvoiceCoding`` is the Pydantic mirror used to re-validate the response
  locally (dates, ranges) before anything downstream consumes it.

The original target fields are unchanged; Canadian sales tax adds the
province fields, supplier registration numbers, ``tax_lines`` (one per tax
type charged) and ``taxes_applied`` on each line item.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .reference_data import UNASSIGNED, ReferenceData
from .tax import OUTSIDE_CANADA, PROVINCES, TAX_TYPES

SCHEMA_NAME = "ap_invoice_coding"

# Structured Outputs limits for enums (documented by OpenAI/Azure OpenAI). We
# stay well under them and fall back to free-text codes + local validation.
_MAX_ENUM_VALUES_PER_FIELD = 500
_MAX_ENUM_CHARS_PER_FIELD = 7_500

PROVINCE_VALUES = [*PROVINCES, OUTSIDE_CANADA, ""]


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


def line_gl_codes(reference: ReferenceData) -> list[str]:
    """GL codes an expense line may use: everything except accounts mapped to a tax type."""
    tax_gls = reference.tax.tax_gl_codes()
    return [c for c in reference.chart_of_accounts.codes if c not in tax_gls]


def build_json_schema(reference: ReferenceData | None = None, *, constrain_codes: bool = True) -> dict[str, Any]:
    """Build the strict JSON Schema for the target output.

    When ``constrain_codes`` is set and reference data is supplied, the GL code
    and cost center fields become enums of the valid codes (plus
    ``UNASSIGNED``), so the model physically cannot invent an account. When no
    cost centers are configured the field is fixed to an empty string.
    """
    gl_codes = cc_codes = None
    cc_description = f"Cost center code from the Cost Center list, or {UNASSIGNED} if none fits."
    if reference is not None:
        if constrain_codes:
            gl_codes = enum_values_or_none(line_gl_codes(reference))
            if reference.cost_centers is not None:
                cc_codes = enum_values_or_none(reference.cost_centers.codes)
        if reference.cost_centers is None:
            cc_codes, cc_description = [""], "Cost centers are not configured: always an empty string."

    line_properties: dict[str, Any] = {
        "line_number": {"type": "integer", "description": "1-based position of the line on the invoice."},
        "description": {"type": "string", "description": "Line description as printed on the invoice."},
        "quantity": {"type": "number", "description": "Billed quantity. Use 1 if the invoice states none."},
        "unit_price": {"type": "number", "description": "Price per unit, excluding tax."},
        "amount": {"type": "number", "description": "Line net amount, excluding tax."},
        "predicted_gl_code": _code_property(
            f"Expense/asset GL account from the GL accounts list, or {UNASSIGNED} if none fits.", gl_codes
        ),
        "predicted_cost_center": _code_property(cc_description, cc_codes),
        "taxes_applied": {
            "type": "array",
            "description": "Sales taxes charged on this line (empty if exempt / zero-rated).",
            "items": {"type": "string", "enum": list(TAX_TYPES)},
        },
        "reasoning_justification": {
            "type": "string",
            "description": "One or two sentences explaining the GL, cost center and tax determination.",
        },
    }
    tax_line_properties: dict[str, Any] = {
        "tax_type": {"type": "string", "enum": list(TAX_TYPES), "description": "GST, HST, PST (incl. MB RST) or QST."},
        "province": {
            "type": "string",
            "enum": PROVINCE_VALUES,
            "description": "Province the tax belongs to (empty for GST).",
        },
        "rate": {"type": "number", "description": "Rate as a decimal fraction (13% -> 0.13, 9.975% -> 0.09975)."},
        "taxable_amount": {"type": "number", "description": "Amount the tax was calculated on, excluding tax."},
        "tax_amount": {"type": "number", "description": "Tax charged, as printed."},
    }

    properties: dict[str, Any] = {
        "vendor_name": {"type": "string", "description": "Legal or trading name of the supplier."},
        "invoice_number": {"type": "string", "description": "Supplier's invoice identifier."},
        "invoice_date": {"type": "string", "description": "Invoice issue date formatted YYYY-MM-DD."},
        "currency": {"type": "string", "description": "ISO 4217 currency code, e.g. CAD, USD."},
        "supplier_province": {
            "type": "string",
            "enum": PROVINCE_VALUES,
            "description": "Province of the supplier's address; OUTSIDE_CANADA or empty if unknown.",
        },
        "ship_to_province": {
            "type": "string",
            "enum": PROVINCE_VALUES,
            "description": "Province where goods are delivered / services performed (bill-to if no ship-to).",
        },
        "gst_hst_registration_number": {
            "type": "string",
            "description": "Supplier GST/HST number as printed (e.g. 123456789 RT0001), empty if absent.",
        },
        "qst_registration_number": {
            "type": "string",
            "description": "Supplier QST number as printed (e.g. 1234567890 TQ0001), empty if absent.",
        },
        "subtotal": {"type": "number", "description": "Total before tax."},
        "tax_lines": {
            "type": "array",
            "description": "One entry per sales tax charged, as printed on the invoice.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(tax_line_properties),
                "properties": tax_line_properties,
            },
        },
        "tax_total": {"type": "number", "description": "Total tax charged (sum of tax_lines)."},
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
                "required": list(line_properties),
                "properties": line_properties,
            },
        },
    }
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def response_format(schema: dict[str, Any]) -> dict[str, Any]:
    """Wrap a schema into the Chat Completions ``response_format`` payload."""
    return {
        "type": "json_schema",
        "json_schema": {"name": SCHEMA_NAME, "strict": True, "schema": schema},
    }


class TaxLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tax_type: str
    province: str = ""
    rate: float = Field(ge=0, le=1)
    taxable_amount: float
    tax_amount: float

    @field_validator("tax_type")
    @classmethod
    def _tax_type(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in TAX_TYPES:
            raise ValueError(f"tax_type must be one of {TAX_TYPES}, got {value!r}")
        return value

    @field_validator("province")
    @classmethod
    def _province(cls, value: str) -> str:
        value = (value or "").strip().upper()
        if value not in PROVINCE_VALUES:
            raise ValueError(f"province must be a Canadian province code, got {value!r}")
        return value


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_number: int = Field(ge=1)
    description: str
    quantity: float
    unit_price: float
    amount: float
    predicted_gl_code: str
    predicted_cost_center: str = ""
    taxes_applied: list[str] = Field(default_factory=list)
    reasoning_justification: str = ""

    @field_validator("taxes_applied")
    @classmethod
    def _taxes(cls, value: list[str]) -> list[str]:
        out = []
        for v in value:
            v = v.strip().upper()
            if v not in TAX_TYPES:
                raise ValueError(f"taxes_applied entries must be one of {TAX_TYPES}, got {v!r}")
            if v not in out:
                out.append(v)
        return out


class InvoiceCoding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_name: str
    invoice_number: str
    invoice_date: str
    currency: str
    supplier_province: str = ""
    ship_to_province: str = ""
    gst_hst_registration_number: str = ""
    qst_registration_number: str = ""
    subtotal: float
    tax_lines: list[TaxLine] = Field(default_factory=list)
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

    @field_validator("supplier_province", "ship_to_province")
    @classmethod
    def _provinces(cls, value: str) -> str:
        value = (value or "").strip().upper()
        if value not in PROVINCE_VALUES:
            raise ValueError(f"province must be a Canadian province code, got {value!r}")
        return value

    def to_output(self) -> dict[str, Any]:
        return self.model_dump()
