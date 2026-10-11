"""Glue between capture and the rest of AP Coder: the AI's coding as a reader, the vendor master as
a check, and capture results as review issues."""

from __future__ import annotations

from typing import Any

from .types import CHECK, MISSING, CaptureResult, FieldResult

TAX_FIELD = {"GST": "gst_amount", "HST": "hst_amount", "PST": "pst_amount", "QST": "qst_amount"}


def ai_values(output: dict[str, Any] | None) -> dict[str, Any]:
    """The AI coder's header values (an InvoiceCoding output dict) as capture fields."""
    if not output:
        return {}
    values: dict[str, Any] = {
        k: output.get(k)
        for k in (
            "vendor_name",
            "invoice_number",
            "invoice_date",
            "due_date",
            "po_number",
            "currency",
            "gst_hst_registration_number",
            "qst_registration_number",
            "subtotal",
            "tax_total",
            "grand_total",
            "payment_terms",
        )
        if output.get(k) not in (None, "")
    }
    taxes: dict[str, float] = {}
    for line in output.get("tax_lines") or []:
        field = TAX_FIELD.get(str(line.get("tax_type") or "").upper())
        if field and line.get("tax_amount") is not None:
            taxes[field] = round(taxes.get(field, 0.0) + float(line["tax_amount"]), 2)
    values.update(taxes)
    if taxes:
        values.pop("tax_total", None)  # the AI adds up the tax lines; whether a total is printed is the page's call
    return values


def vendor_record(store: Any, vendor_name: str | None) -> dict[str, Any] | None:
    """{name, gst_number, erp_id} from the vendor master, if this vendor is there."""
    if store is None or not vendor_name:
        return None
    from ..memory import vendor_key

    try:
        rec = store.get_vendor(vendor_key(vendor_name))
    except Exception:  # an older store without the vendor table
        return None
    if not rec or not rec.get("in_master"):
        return None
    return {"name": rec.get("display_name") or vendor_name, "gst_number": rec.get("expected_gst") or "",
            "erp_id": rec.get("erp_id") or ""}  # fmt: skip


def review_issues(capture: CaptureResult) -> list[tuple[str, str, str]]:
    """(severity, code, message) for the review screen: header fields the readers could not confirm, totals that
    don't add up as printed, a PDF whose hidden text is not what its page shows, and a currency the page does not
    show (the coding's is then assumed)."""
    from ..validation import INFO, WARNING, currency_not_found
    from .confidence import LABELS

    out: list[tuple[str, str, str]] = []
    unsure = [f for f in capture.fields.values() if f.status == CHECK]
    failed = [c for c in capture.checks if not c["ok"] and c["code"] in ("TOTALS_ADD_UP", "TAXES_ADD_UP")]
    for c in failed:
        out.append((WARNING, "CAPTURE_TOTALS", f"as printed, {c['detail']}"))
    for c in capture.checks:  # the PDF's hidden text is not what its page shows (capture.text_layer_check)
        if c.get("code") == "TEXT_LAYER_MATCHES_PAGE" and not c.get("ok", True):
            out.append((WARNING, "TEXT_LAYER_MATCHES_PAGE", str(c.get("detail") or "")))
    if unsure:
        names = ", ".join(LABELS.get(f.field, f.field) for f in unsure)
        out.append((INFO, "CAPTURE_CHECK_FIELDS", f"check on the page: {names} (highlighted in amber)"))
    currency = capture.fields.get("currency")
    if currency is not None and not currency_read(currency):
        issue = currency_not_found(str(currency.value or ""))
        out.append((issue.severity, issue.code, issue.message))
    return out


def currency_read(field: FieldResult) -> bool:
    """A reader of the page found the currency: printed as a code or a sign, not only the AI coding's
    answer that the page does not show."""
    if field.status == MISSING or field.value in (None, ""):
        return False
    readers = {s for s in field.sources if not s.startswith("other:")} - {"ai"}
    return bool(readers) or bool(field.boxes)
