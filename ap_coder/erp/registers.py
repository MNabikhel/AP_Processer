"""The ERP's AP invoice register: invoices entered in the ERP before (or outside) AP Coder.

AP Coder only knows the invoices it has processed. Importing the ERP's invoice list (vendor, invoice
number, date, total) lets the duplicate check also catch a bill that was already entered or paid in the
ERP: DUPLICATE_IN_ERP.

Entry points: ``map_columns`` and ``rows_from_records`` (the import; ``Store.import_erp_register`` keeps them); for
JD Edwards' voucher list, read as it comes, ``voucher_columns``, ``vouchers_from_pay_items`` and ``vouchers_said``
(``Store.import_erp_vouchers`` keeps them).
"""

from __future__ import annotations

import re
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
    "gl_date": ("gldate",),
    "gross": ("grossamount",),
    "foreign": ("foreignamount",),
    "currency": ("currcode", "currencycode", "currency"),
    "batch": ("batchnumber", "batchno"),
}
VOUCHER_REQUIRED = ("document", "pay_item", "an8", "invoice_number", "gross")
NO_INVOICE_NUMBER, NO_SUPPLIER, NO_AMOUNT, NOTHING, NO_FOREIGN = (
    "with no invoice number", "with no supplier", "with an amount that could not be read", "adding up to 0.00",
    "with a pay item that has no foreign amount",
)  # fmt: skip
HOME_CURRENCY = "CAD"  # a voucher in another currency is compared by its foreign amount, when the list gives it
ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


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
    document number, batch and G/L date (``Store.import_erp_vouchers``).

    What was read also says what the list could not tell (B-2165): ``by_supplier``, the list has no document company
    or type column, so a voucher is told by its document number and its supplier; ``domestic``, the vouchers in
    another currency kept at their gross in Canadian dollars for want of a foreign amount. And what it covers
    (B-2164): ``voided``, the keys of the vouchers it shows adding up to nothing; ``complete``, its totals row is the
    sum of its rows (a file cut short has none, or one that is not); ``covers``, {"<company>-<type>-": (the first
    G/L date of its rows, the last)}: the period it covers, when the person says the grid was not filtered."""
    column = columns.get("invoice_date")
    order = day_first_order([str(rec.get(column) or "") for rec in records]) if column else None
    by_supplier = "company" not in columns or "doc_type" not in columns
    read: dict[str, Any] = {"pay_items": 0, "vouchers": 0, "added_up": 0, "totals_rows": 0, "named": 0,
                            "foreign": {}, "skipped": {}, "by_supplier": by_supplier, "domestic": {},
                            "complete": False, "covers": {}, "voided": []}  # fmt: skip
    vouchers: dict[tuple[str, ...], list[dict[str, str]]] = {}
    listed_total, gross_sum = None, 0.0
    for rec in records:
        item = {field: _cell(rec.get(header)) for field, header in columns.items()}
        if not item["document"]:
            if not item["an8"] and not item["invoice_number"] and item["gross"]:
                read["totals_rows"] += 1
                listed_total = parse_amount(item["gross"])
            continue
        read["pay_items"] += 1
        gross_sum += parse_amount(item["gross"]) or 0.0
        key = (item.get("company", ""), item.get("doc_type", ""), item["document"])
        vouchers.setdefault((*key, item["an8"]) if by_supplier else key, []).append(item)
        _covered(read["covers"], key, iso_date(item.get("gl_date", ""), order), by_supplier)
    read["complete"] = read["totals_rows"] == 1 and listed_total is not None and abs(listed_total - gross_sum) < 0.005
    known = {str(ident).strip().lstrip("0"): name for ident, name in names.items()}
    rows = []
    for key, items in vouchers.items():
        row = _voucher(key, items, known, order)
        if isinstance(row, str):
            counted = read["skipped"].setdefault(row, [0, 0])
            counted[0], counted[1] = counted[0] + 1, counted[1] + len(items)
            if row == NOTHING:  # voided in JD Edwards: out of the register if an earlier list gave it (B-2164)
                read["voided"].append("-".join(key))
            continue
        read["vouchers"] += 1
        read["added_up"] += len(items) > 1
        read["named"] += row.pop("named")
        if row.pop("in_foreign"):
            read["foreign"][row["currency"]] = read["foreign"].get(row["currency"], 0) + 1
        elif row["currency"] not in ("", HOME_CURRENCY):
            read["domestic"][row["currency"]] = read["domestic"].get(row["currency"], 0) + 1
        rows.append(row)
    return rows, read


def _cell(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def _covered(covers: dict[str, list[str]], key: tuple[str, str, str], day: str, by_supplier: bool) -> None:
    """``covers`` widened by a pay item's G/L date, under its company and document type; nothing without both of
    them or a date that reads as one (the list then says no period it covers)."""
    if by_supplier or not ISO_DAY.fullmatch(day):
        return
    span = covers.setdefault(f"{key[0]}-{key[1]}-", [day, day])
    span[0], span[1] = min(span[0], day), max(span[1], day)


def _voucher(key: tuple[str, ...], items: list[dict[str, str]], known: dict[str, str],
             order: bool | None) -> dict[str, Any] | str:  # fmt: skip
    """One voucher of its pay items, or why it is left out (NO_INVOICE_NUMBER, NO_SUPPLIER, NO_AMOUNT, NOTHING,
    NO_FOREIGN: some of its pay items have a foreign amount and one has none, so their sum would be short)."""

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
    if in_foreign and not all(amount or not own for amount, own in zip(foreign, gross, strict=True)):
        return NO_FOREIGN
    total = round(sum(foreign if in_foreign else gross), 2)  # type: ignore[arg-type]
    if not total:
        return NOTHING
    return {"key": "-".join(key), "document": key[2], "batch": first("batch"), "currency": first("currency").upper(),
            "vendor_name": name, "an8": an8, "invoice_number": number, "total": total,
            "invoice_date": iso_date(first("invoice_date"), order), "gl_date": iso_date(first("gl_date"), order),
            "named": bool(master), "in_foreign": in_foreign}  # fmt: skip


def vouchers_said(read: dict[str, Any]) -> str:
    """What ``vouchers_from_pay_items`` read, in plain words: the vouchers and their pay items, how their suppliers
    were named, the ones in another currency, what was left out and why, what the list could not tell, and the
    period it covers (or that it may be cut short)."""
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
    for currency, count in read.get("domestic", {}).items():
        its, it = ("its", "it") if count == 1 else ("their", "they")
        said += (f" {plural(count, 'voucher')} in {currency} kept at {its} gross amount in Canadian dollars (the list "
                 f"gives no foreign amount): {it} will not match an invoice's {currency} total.")  # fmt: skip
    if read.get("by_supplier"):
        said += (" The list has no document company or document type column: a voucher is told by its document "
                 "number and its supplier.")  # fmt: skip
    out = [plural(read["totals_rows"], "totals row")] if read["totals_rows"] > 1 else []
    out = ["the totals row"] if read["totals_rows"] == 1 else out
    out += [f"{plural(vouchers, 'voucher')} {why} ({plural(items, 'pay item')})"
            for why, (vouchers, items) in read["skipped"].items()]  # fmt: skip
    return said + (f" Left out: {'; '.join(out)}." if out else "") + _period_said(read)


def _period_said(read: dict[str, Any]) -> str:
    """The period a voucher list covers, by the G/L dates of its own rows, said when its totals row is the sum of
    its rows (so the file is whole). A voucher it shows at 0.00 always leaves the register; one of that period it no
    longer shows leaves only when the person says the grid was not filtered (by supplier, batch or pay status: the
    file cannot tell). A list that may be cut short takes nothing off by its absence (B-2164)."""
    covers = read.get("covers") or {}
    if not covers:
        return ""
    if not read.get("complete"):
        return (" Its totals row is missing or is not the sum of its rows, so the file may be cut short: a voucher it "
                "does not show is never taken off the list.")  # fmt: skip
    spans = [f"company {prefix.split('-')[0]} {prefix.split('-')[1]}, G/L dates {first} to {last}"
             for prefix, (first, last) in sorted(covers.items())]  # fmt: skip
    return f" It covers {'; '.join(spans)} (its own rows' dates; its totals row adds up)."
