"""Glue between capture and the rest of AP Coder: the AI's coding as a reader, the vendor master as
a check, and capture results as review issues."""

from __future__ import annotations

from typing import Any

from .types import CHECK, MISSING, CaptureResult, FieldResult, LineReading

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


def vendor_record(store: Any, vendor_name: str | None, gst_number: Any = None) -> dict[str, Any] | None:
    """{name, gst_number, erp_id} from the vendor master, if this vendor is there: of two whose names read alike, the
    one with ``gst_number`` (B-1021)."""
    if store is None or not vendor_name:
        return None
    try:
        rec = store.find_vendor(vendor_name, gst_number)
    except Exception:  # an older store without the vendor table
        return None
    if not rec or not rec.get("in_master"):
        return None
    from ..core.memory import vendor_key

    name = rec.get("display_name") or vendor_name
    # Found by a name AP corrected to this vendor's (D-119): the name as printed is that vendor's too.
    aliases = [vendor_name] if vendor_key(vendor_name) != vendor_key(name) else []
    return {"name": name, "gst_number": rec.get("expected_gst") or "", "erp_id": rec.get("erp_id") or "",
            **({"aliases": aliases} if aliases else {})}  # fmt: skip


def said_numbers(numbers: list[int], one: str = "page", many: str = "pages") -> str:
    """Numbers in words: "page 3", "pages 1, 2 and 10", "pages 56 to 65", "lines 2, 7 and 56 to 65"."""
    runs: list[list[int]] = []
    for n in sorted(numbers):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    parts = [part for a, b in runs for part in ([str(a)] if a == b else [str(a), str(b)] if b == a + 1 else
                                                [f"{a} to {b}"])]  # fmt: skip
    listed = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    return f"{one if len(numbers) == 1 else many} {listed}"


said_pages = said_numbers


def review_issues(capture: CaptureResult, assumed_currency: str | None = None, *,
                  assumed_date: str | None = None) -> list[tuple[str, str, str]]:  # fmt: skip
    """(severity, code, message) for the review screen: header fields the readers could not confirm, totals that
    don't add up as printed, a PDF whose hidden text is not what its page shows, a currency the page does not show
    (the coding's is then assumed: ``assumed_currency``, e.g. USD for a supplier with a US address), an invoice date
    no reader found (the coding's ``assumed_date``, the day it was read, is then put in: B-459; None once a person
    gave another), and pages the local readers did not read (a file longer than ``layout.read_pages``, or OCR failed
    on them), by their numbers (B-2215), and a document that may not be an invoice (B-2217)."""
    from ..coding.validation import INFO, WARNING, currency_not_found
    from .confidence import LABELS
    from .layout import last_pages, read_pages

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
        if c.get("code") == "LINE_AMOUNTS" and not c.get("ok", True):  # the lines read were left not adding up
            out.append((WARNING, "LINE_AMOUNTS_TO_CHECK", str(c.get("detail") or "")))
        if c.get("code") == "ACCOUNT_ROWS" and c.get("detail"):  # rows of the supplier's account are no lines
            out.append((INFO, "ACCOUNT_ROWS_LEFT_OUT", str(c["detail"])))
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
        read, total, most = max(0, capture.page_count - capture.pages_left_out), capture.page_count, read_pages()
        one = len(capture.pages_not_read) == 1
        last = last_pages(most)
        long = f"of a file longer than {most} pages the first {most - last} and the last {last} are read"
        if not capture.pages_not_read:  # a reading that did not name them
            said = (f"only {read} of its {total} pages were read ({long}, and a page OCR can't read is left out): "
                    "check the other pages yourself before approving")  # fmt: skip
        else:
            why = long if total > most else f"OCR could not read {'it' if one else 'them'}"
            said = (f"{said_pages(capture.pages_not_read)} of its {total} pages {'was' if one else 'were'} not read "
                    f"({why}): nothing on {'it' if one else 'them'} is in the lines or totals shown; check "
                    f"{'it' if one else 'them'} yourself before approving")  # fmt: skip
        out.append((WARNING, "PAGES_NOT_READ", said))
    if capture.not_invoice:  # a person rejects it, or checks it as an invoice: never rejected without one (B-2217)
        out.append((WARNING, "NOT_AN_INVOICE", f"this may not be an invoice: {capture.not_invoice}. If it is not one, "
                    "reject it (Reject, with the reason); if it is, check it as any other"))  # fmt: skip
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


def printed_taxes(lines: list[LineReading], charged: dict[str, float]) -> list[tuple[str, dict[str, float] | None]]:
    """For each line read, (its tax code as its own tax cells print it, its ``LineItem.taxes_read``): what the line
    table's tax columns say (``LineReading.taxes_read``), read with the taxes the invoice charges (``charged``: {tax:
    its amount}).

    A column says only about the tax it names. "GST" on an invoice whose tax is the HST is that HST (and "HST" the
    GST); "Tax" (or "GST/HST") is the one tax charged that no other column names, and on an invoice that charges two
    of them it names neither: its amounts say a line is taxed, not with which, and are not kept. A cell not read
    (None) exempts nothing: it is zero only when the amounts read in its column add up to the invoice's own amount of
    that tax. The code is given only where every tax charged has its column and its cell on the line: the taxes whose
    cell prints an amount ("GST/PST"), "Exempt" when none does. "" otherwise: which lines carry a tax no column names
    is worked out from the amounts (``line_tax.prefill``), as on an invoice with no tax column."""
    names = list(dict.fromkeys(name for li in lines for name in li.taxes_read or {}))
    taxes = {name: name for name in names if name != "TAX"}  # {column: the tax it prints}
    for name, other in (("GST", "HST"), ("HST", "GST")):
        if name in taxes and name not in charged and other in charged and other not in taxes:
            taxes[name] = other
    unnamed = [tax for tax in charged if tax not in taxes.values()]
    if "TAX" in names and len(unnamed) == 1:
        taxes["TAX"] = unnamed[0]
    whole = set()  # the columns whose amounts read add up to the invoice's amount of their tax
    for name, tax in taxes.items():
        read = [li.taxes_read[name] for li in lines if li.taxes_read and li.taxes_read.get(name) is not None]
        if tax in charged and abs(round(sum(read), 2) - charged[tax]) <= 0.011:
            whole.add(name)
    out: list[tuple[str, dict[str, float] | None]] = []
    for li in lines:
        mine = li.taxes_read or {}
        kept: dict[str, float] = {}
        for name, tax in taxes.items():
            value = 0.0 if mine.get(name) is None and name in mine and name in whole else mine.get(name)
            if value is not None:
                kept[tax] = round(value, 2)
        said = ""
        if charged and all(tax in kept for tax in charged):
            said = "/".join(tax for tax in charged if abs(kept[tax]) >= 0.005) or "Exempt"
        out.append((said, kept or None))
    return out
