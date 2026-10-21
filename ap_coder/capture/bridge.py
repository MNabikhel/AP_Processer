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
            "pst_registration_number",
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
    from ..core.memory import vendor_key

    try:
        rec = store.get_vendor(vendor_key(vendor_name))
    except Exception:  # an older store without the vendor table
        return None
    if not rec or not rec.get("in_master"):
        return None
    return {"name": rec.get("display_name") or vendor_name, "gst_number": rec.get("expected_gst") or "",
            "erp_id": rec.get("erp_id") or ""}  # fmt: skip


def review_issues(capture: CaptureResult, assumed_currency: str | None = None, *,
                  assumed_date: str | None = None) -> list[tuple[str, str, str]]:  # fmt: skip
    """(severity, code, message) for the review screen: header fields the readers could not confirm, totals that
    don't add up as printed, a PDF whose hidden text is not what its page shows, a currency the page does not show
    (the coding's is then assumed: ``assumed_currency``, e.g. USD for a supplier with a US address), an invoice date
    no reader found (the coding's ``assumed_date``, the day it was read, is then put in: B-459; None once a person
    gave another), and pages the local readers did not read (past ``layout.READ_PAGES``, or OCR failed on them)."""
    from ..coding.validation import INFO, WARNING, currency_not_found
    from .confidence import LABELS
    from .layout import READ_PAGES

    out: list[tuple[str, str, str]] = []
    unsure = [f for f in capture.fields.values() if f.status == CHECK]
    failed = [c for c in capture.checks if not c["ok"] and c["code"] in ("TOTALS_ADD_UP", "TAXES_ADD_UP")]
    for c in failed:
        out.append((WARNING, "CAPTURE_TOTALS", f"as printed, {c['detail']}"))
    for c in capture.checks:  # the PDF's hidden text is not what its page shows (capture.text_layer_check)
        if c.get("code") == "TEXT_LAYER_MATCHES_PAGE" and not c.get("ok", True):
            out.append((WARNING, "TEXT_LAYER_MATCHES_PAGE", str(c.get("detail") or "")))
        if c.get("code") == "LINE_ITEMS_AGREE" and not c.get("ok", True):  # OvisOCR2 and the table read differently
            out.append((WARNING, "LINE_ITEMS_AGREE", f"line items read differently: {c.get('detail') or ''}"))
    if unsure:
        names = ", ".join(LABELS.get(f.field, f.field) for f in unsure)
        out.append((INFO, "CAPTURE_CHECK_FIELDS", f"check on the page: {names} (highlighted in amber)"))
    currency = capture.fields.get("currency")
    if currency is not None and not currency_read(currency):
        issue = currency_not_found(str(assumed_currency or currency.value or ""))
        out.append((issue.severity, issue.code, issue.message))
    dated = capture.fields.get("invoice_date")
    if assumed_date and dated is not None and (dated.status == MISSING or dated.value in (None, "")):
        out.append(
            (
                WARNING,
                "INVOICE_DATE_NOT_FOUND",
                f"no invoice date was found on the page: {assumed_date} is "
                "assumed (the day it was read); put in the invoice's date, it sets the due date and the period",
            )
        )
    if capture.pages_left_out > 0:  # nothing on those pages is in what the screen shows as read
        read, total = max(0, capture.page_count - capture.pages_left_out), capture.page_count
        out.append((WARNING, "PAGES_NOT_READ", f"only {read} of its {total} pages were read (a file's first "
                    f"{READ_PAGES} pages are read, and a page OCR can't read is left out): check the other pages "
                    "yourself before approving"))  # fmt: skip
    return out


def currency_read(field: FieldResult) -> bool:
    """A reader of the page found the currency: printed as a code or a sign, not only the AI coding's
    answer that the page does not show."""
    if field.status == MISSING or field.value in (None, ""):
        return False
    if "|us-supplier|" in (getattr(field, "evidence", "") or ""):
        return False  # US dollars implied by the supplier's US address (reader ``_us_supplier``): not printed
    readers = {s for s in field.sources if not s.startswith("other:")} - {"ai"}
    return bool(readers) or bool(field.boxes)
