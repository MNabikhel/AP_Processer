"""The ERP's AP invoice register: invoices entered in the ERP before (or outside) AP Coder.

AP Coder only knows the invoices it has processed. Importing the ERP's invoice list (vendor, invoice
number, date, total) lets the duplicate check also catch a bill that was already entered or paid in the
ERP: DUPLICATE_IN_ERP.

Entry points: ``map_columns`` and ``rows_from_records`` (the import; ``Store.import_erp_register`` keeps them); for
JD Edwards' voucher list, read as it comes, ``voucher_columns``, ``vouchers_from_pay_items`` and ``vouchers_said``
(``Store.import_erp_vouchers`` keeps them).
"""

from __future__ import annotations

from typing import Any

from ..core.headers import match_columns
from ..core.safe import parse_amount
from ..core.ui import plural
from .statements import day_first_order, read_date

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "vendor_name": ("vendorname", "vendor", "supplier", "suppliername", "name", "fournisseur"),
    # The supplier's own number first: a register lists the ERP's document number next to it.
    "invoice_number": ("supplierinvoice", "supplierinvoicenumber", "supplierinvoiceno", "vendorinvoice",
                       "vendorinvoicenumber", "vendorinvoiceno", "invoicenumber", "invoice", "invoiceno", "invno",
                       "facture", "documentnumber", "document", "docno", "reference"),
    "invoice_date": ("invoicedate", "date", "documentdate", "postingdate"),
    "total": ("total", "invoicetotal", "amount", "grossamount", "totalamount", "montant", "invoiceamount"),
}  # fmt: skip
REQUIRED = ("vendor_name", "invoice_number", "total")


def map_columns(headers: list[str]) -> dict[str, str]:
    return match_columns(headers, COLUMN_ALIASES)


def iso_date(text: str, day_first: bool | None = None) -> str:
    """YYYY-MM-DD when the date can be read ("2026-09-11", "2026-09-11 00:00:00", "25/09/2026", "1/5/2026 0:00"),
    else the text as it is. ``day_first``: the order the file's other dates show, for an ambiguous one."""
    return read_date(text, day_first)


def rows_from_records(records: list[dict[str, Any]], columns: dict[str, str]) -> tuple[list[dict[str, Any]], int]:
    rows, skipped = [], 0
    column = columns.get("invoice_date")
    order = day_first_order([str(rec.get(column) or "") for rec in records]) if column else None
    for rec in records:

        def get(target: str, rec: dict[str, Any] = rec) -> str:
            value = rec.get(columns[target]) if target in columns else None
            text = "" if value is None else str(value).strip()
            return "" if text.lower() in ("nan", "none") else text

        row = {"vendor_name": get("vendor_name"), "invoice_number": get("invoice_number"),
               "invoice_date": iso_date(get("invoice_date"), order), "total": parse_amount(get("total"))}  # fmt: skip
        if not row["vendor_name"] or not row["invoice_number"] or row["total"] is None:
            skipped += 1
            continue
        rows.append(row)
    return rows, skipped


# --- JD Edwards' voucher list: Supplier Ledger Inquiry's grid, one row per pay item (B-2084) -------------------------
# The grid has no vendor name column: the supplier's number, and its name cut at 30 characters. Each caption is one
# of the grid's own ("Doc Type" is the code, "Document Type" its words; "Doc Co" the voucher's company).
VOUCHER_ALIASES: dict[str, tuple[str, ...]] = {
    "document": ("documentnumber", "docnumber"),
    "doc_type": ("doctype", "docty"),
    "company": ("docco", "documentcompany"),
    "pay_item": ("payitem", "payitm"),
    "an8": ("suppliernumber", "supplierno"),
    "supplier_name": ("suppliernumberdesc", "suppliername"),
    "invoice_number": ("invoicenumber", "supplierinvoicenumber"),
    "invoice_date": ("invoicedate",),
    "gross": ("grossamount",),
    "foreign": ("foreignamount",),
    "currency": ("currcode", "currencycode", "currency"),
    "batch": ("batchnumber", "batchno"),
}
VOUCHER_REQUIRED = ("document", "pay_item", "an8", "invoice_number", "gross")
NO_INVOICE_NUMBER, NO_SUPPLIER, NO_AMOUNT, NOTHING = (
    "with no invoice number", "with no supplier", "with an amount that could not be read", "adding up to 0.00",
)  # fmt: skip


def voucher_columns(headers: list[str]) -> dict[str, str]:
    """{field: the file's column} when the file is JD Edwards' voucher list (it has a document number, a pay item, a
    supplier number, an invoice number and a gross amount); {} for any other list, whose columns are picked."""
    found = match_columns(headers, VOUCHER_ALIASES)
    return found if all(field in found for field in VOUCHER_REQUIRED) else {}


def vouchers_from_pay_items(
    records: list[dict[str, Any]], columns: dict[str, str], names: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """JD Edwards' voucher list (``voucher_columns``) as (the register's vouchers, what was read). The pay items of
    one voucher (the same document number, type and company) are one voucher: its gross is their sum (a negative
    pay item subtracts), or the sum of their foreign amounts when it has them (a USD voucher, with its currency when
    the list gives it). Its supplier is named from the vendor master by its number (``names``: {ERP ID: name}), else
    as the list names it (cut at 30 characters). The grid's totals row (only amounts) is left out, and so is a
    voucher with no invoice number, with no supplier, or adding up to nothing. Each voucher keeps JD Edwards'
    document number and batch (``Store.import_erp_vouchers``)."""
    column = columns.get("invoice_date")
    order = day_first_order([str(rec.get(column) or "") for rec in records]) if column else None
    read: dict[str, Any] = {"pay_items": 0, "vouchers": 0, "added_up": 0, "totals_rows": 0, "named": 0,
                            "foreign": {}, "skipped": {}}  # fmt: skip
    vouchers: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for rec in records:
        item = {field: _cell(rec.get(header)) for field, header in columns.items()}
        if not item["document"]:
            if not item["an8"] and not item["invoice_number"] and item["gross"]:
                read["totals_rows"] += 1
            continue
        read["pay_items"] += 1
        vouchers.setdefault((item.get("company", ""), item.get("doc_type", ""), item["document"]), []).append(item)
    known = {str(ident).strip().lstrip("0"): name for ident, name in names.items()}
    rows = []
    for key, items in vouchers.items():
        row = _voucher(key, items, known, order)
        if isinstance(row, str):
            counted = read["skipped"].setdefault(row, [0, 0])
            counted[0], counted[1] = counted[0] + 1, counted[1] + len(items)
            continue
        read["vouchers"] += 1
        read["added_up"] += len(items) > 1
        read["named"] += row.pop("named")
        if row.pop("in_foreign"):
            read["foreign"][row["currency"]] = read["foreign"].get(row["currency"], 0) + 1
        rows.append(row)
    return rows, read


def _cell(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def _voucher(key: tuple[str, str, str], items: list[dict[str, str]], known: dict[str, str],
             order: bool | None) -> dict[str, Any] | str:  # fmt: skip
    """One voucher of its pay items, or why it is left out (NO_INVOICE_NUMBER, NO_SUPPLIER, NO_AMOUNT, NOTHING)."""

    def first(field: str) -> str:
        return next((item[field] for item in items if item.get(field)), "")

    an8, number = first("an8"), first("invoice_number")
    master = known.get(an8.lstrip("0"), "")
    name = master or first("supplier_name")
    if not number:
        return NO_INVOICE_NUMBER
    if not name:
        return NO_SUPPLIER
    gross = [parse_amount(item["gross"]) for item in items]
    foreign = [parse_amount(item.get("foreign", "")) or 0.0 for item in items]
    if any(amount is None for amount in gross):
        return NO_AMOUNT
    in_foreign = any(foreign)
    total = round(sum(foreign if in_foreign else gross), 2)  # type: ignore[arg-type]
    if not total:
        return NOTHING
    return {"key": "-".join(key), "document": key[2], "batch": first("batch"), "currency": first("currency").upper(),
            "vendor_name": name, "an8": an8, "invoice_number": number, "total": total,
            "invoice_date": iso_date(first("invoice_date"), order), "named": bool(master),
            "in_foreign": in_foreign}  # fmt: skip


def vouchers_said(read: dict[str, Any]) -> str:
    """What ``vouchers_from_pay_items`` read, in plain words: the vouchers and their pay items, how their suppliers
    were named, the ones in another currency, and what was left out and why."""
    said = f"JD Edwards' voucher list: {plural(read['vouchers'], 'voucher')} read from "
    said += plural(read["pay_items"], "pay item")
    if read["added_up"]:
        said += f" ({plural(read['added_up'], 'voucher')} added up from more than one pay item)"
    named, listed = read["named"], read["vouchers"] - read["named"]
    by = [f"{named:,} named from the vendor master by their supplier number"] if named else []
    by += [f"{listed:,} by the name on the list (cut at 30 characters)"] if listed else []
    said += f". {', '.join(by)}." if by else "."
    for currency, count in read["foreign"].items():
        said += (f" {plural(count, 'voucher')} in {currency or 'another currency'}, by "
                 f"{'its' if count == 1 else 'their'} foreign amount.")  # fmt: skip
    out = [plural(read["totals_rows"], "totals row")] if read["totals_rows"] > 1 else []
    out = ["the totals row"] if read["totals_rows"] == 1 else out
    out += [f"{plural(vouchers, 'voucher')} {why} ({plural(items, 'pay item')})"
            for why, (vouchers, items) in read["skipped"].items()]  # fmt: skip
    return said + (f" Left out: {'; '.join(out)}." if out else "")
