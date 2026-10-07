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

# Structured Outputs enum limits. Azure OpenAI documents at most 500 enum values across ALL enum
# properties of a schema (OpenAI's own limit is higher) and a cap on their total string length, so
# the budget is shared by every enum in the schema. Over budget, code lists fall back to free text
# plus local validation.
MAX_ENUM_VALUES_TOTAL = 500
MAX_ENUM_CHARS_TOTAL = 7_500

PROVINCE_VALUES = [*PROVINCES, OUTSIDE_CANADA, ""]
# Enums that are always present: supplier/ship-to/tax-line provinces and two tax-type fields.
FIXED_ENUM_VALUES = 3 * len(PROVINCE_VALUES) + 2 * len(TAX_TYPES)
FIXED_ENUM_CHARS = 3 * sum(len(p) for p in PROVINCE_VALUES) + 2 * sum(len(t) for t in TAX_TYPES)


def _code_property(description: str, codes: list[str] | None) -> dict[str, Any]:
    prop: dict[str, Any] = {"type": "string", "description": description}
    if codes is not None:
        prop["enum"] = codes
    return prop


def plan_code_enums(gl_codes: list[str], cc_codes: list[str] | None) -> tuple[list[str] | None, list[str] | None]:
    """Decide which code lists can be enforced as enums within the shared budget.

    GL accounts get priority (they matter most); cost centers are enforced only if both fit.
    Returns the enum value lists (with ``UNASSIGNED``) or ``None`` for free text.
    """
    budget_n = MAX_ENUM_VALUES_TOTAL - FIXED_ENUM_VALUES
    budget_c = MAX_ENUM_CHARS_TOTAL - FIXED_ENUM_CHARS
    gl = [*gl_codes, UNASSIGNED]
    cc = [*cc_codes, UNASSIGNED] if cc_codes is not None else None

    def size(values: list[str] | None) -> tuple[int, int]:
        return (len(values), sum(len(v) for v in values)) if values else (0, 0)

    (gn, gc), (cn, cchars) = size(gl), size(cc)
    if gn + cn <= budget_n and gc + cchars <= budget_c:
        return gl, cc
    if gn <= budget_n and gc <= budget_c:
        return gl, None
    if cc is not None and cn <= budget_n and cchars <= budget_c:
        return None, cc
    return None, None


def enum_values_or_none(codes: list[str]) -> list[str] | None:
    """A single code list on its own within the budget (kept for callers checking one list)."""
    return plan_code_enums(codes, None)[0]


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
            cost_centers = reference.cost_centers.codes if reference.cost_centers is not None else None
            gl_codes, cc_codes = plan_code_enums(line_gl_codes(reference), cost_centers)
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
        "tax_type": {
            "type": "string",
            "enum": list(TAX_TYPES),
            "description": "GST, HST, PST (incl. MB RST), QST, or OTHER for a non-Canadian tax.",
        },
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
        "po_number": {
            "type": "string",
            "description": "Buyer's purchase order number as printed (PO #, Customer PO, Order Ref), empty if none.",
        },
        "payment_terms": {
            "type": "string",
            "description": "Payment terms as printed (e.g. Net 30, 2/10 Net 30, Due on receipt), empty if none.",
        },
        "due_date": {"type": "string", "description": "Due date printed on the invoice, YYYY-MM-DD; empty if none."},
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
        "remit_bank_account": {
            "type": "string",
            "description": "Bank account the supplier asks to be paid into, as printed (institution, transit and "
            "account numbers, or SWIFT/IBAN and account), empty if none.",
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
    po_number: str = ""
    payment_terms: str = ""
    due_date: str = ""
    currency: str
    supplier_province: str = ""
    ship_to_province: str = ""
    gst_hst_registration_number: str = ""
    qst_registration_number: str = ""
    remit_bank_account: str = ""
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

    @field_validator("due_date")
    @classmethod
    def _due_date(cls, value: str) -> str:
        value = (value or "").strip()
        if value:
            try:
                dt.date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"due_date must be YYYY-MM-DD (or empty), got {value!r}") from exc
            if len(value) != 10:
                raise ValueError(f"due_date must be YYYY-MM-DD (or empty), got {value!r}")
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
