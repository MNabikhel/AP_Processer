"""JD Edwards' vendor information list (D-219): the second supplier export, one row for each supplier paid by any
company of the group (about 20,000), with what the supplier master's grid does not give: the tax ID, the currency,
the payment terms in words, the address and an e-mail address.

The file as it comes: the column names in row 1, 22 columns of which two are both named "Business Unit" (the
number, then its name: read by where they are), every cell text but the supplier number, and a last row of totals
that is no supplier. A name can start with a space or carry a retired supplier's marker; the import keeps the name
as the supplier master's import does (``Store.import_vendor_master``), so the two lists meet on the supplier number.

The tax ID is free text. It is kept as written, and in a compared form (``compared``: its letters and digits, none
for a word such as CHARITABLE or N/A, which is never matched as a number). Only one that reads as a Canadian business
number is the supplier's GST/HST number (``gst_number``), the one an invoice's GST/HST number is filled from and
compared with: with its RT account as written, the nine digits alone for another program account of the same
business (RC, RP), an older "R" number or nine digits of a supplier in Canada. An EIN, a personal number or another
country's are kept and compared with nothing.

Entry points: ``is_vendor_info`` (the column names), ``read`` (a workbook's rows and what was found), ``said``
(the summary shown before anything is kept), ``master_rows`` (the rows as the vendor list's import takes them).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from ..core import chart_file, ui
from ..core.chart import ChartError
from ..core.headers import header_key, jde_names, match_columns
from ..core.keys import norm_tax_number

# Each field's column name in the list, as ``header_key`` reads it; "business_unit_name" is the second column named
# as "business_unit" is.
COLUMNS = {
    "erp_id": "internalsuppliernumber", "vendor_name": "companyname", "address1": "addressline1",
    "address2": "addressline2", "address3": "addressline3", "address4": "addressline4", "city": "citycty1",
    "state": "state", "country": "country", "postal_code": "postalcode", "tax_id": "taxid", "currency": "curcod",
    "email": "jdeemailaddress", "terms": "paymenttermsap", "business_unit": "businessunit", "tax_area": "taxratearea",
}  # fmt: skip
SECOND = {"business_unit_name": "business_unit"}
NEEDS = ("erp_id", "vendor_name", "tax_id")  # the columns that make a file this list
LABELS = {
    "erp_id": "Supplier number", "vendor_name": "Name", "tax_id": "Tax ID", "currency": "Currency",
    "terms": "Payment terms", "address1": "Address", "city": "City", "state": "Province or state",
    "country": "Country", "postal_code": "Postal code", "email": "E-mail", "business_unit": "Business unit",
}  # fmt: skip
# The same fields in JD Edwards' supplier master queried with its address book in one report (F0101 and F0401, with
# F0116's address and F01151's e-mail: the build sheet's tab 3, D-266), beside the master's own supplier number and
# name: titled as this list titles them, as the build sheet does, or by their fields. ``tax_id2``: the additional tax
# ID, where a company may keep the GST/HST or QST number.
IN_MASTER = {
    "tax_id": ("taxid", *jde_names("F0101", "AB", "TAX")),
    "tax_id2": ("additionaltaxid", "additionalindividualtaxid", "taxidadditionalindividual",
                *jde_names("F0101", "AB", "TX2")),
    "currency": ("curcod", "currencycodeap", *jde_names("F0401", "A6", "CRRP")),
    "terms": ("paymenttermsap", *jde_names("F0401", "A6", "TRAP")),
    **{f"address{n}": (f"addressline{n}", *jde_names("F0116", "AL", f"ADD{n}")) for n in range(1, 5)},
    "city": ("citycty1", "city", *jde_names("F0116", "AL", "CTY1")),
    "state": ("state", *jde_names("F0116", "AL", "ADDS")),
    "country": ("country", *jde_names("F0116", "AL", "CTR")),
    "postal_code": ("postalcode", "zipcode", *jde_names("F0116", "AL", "ADDZ")),
    "email": ("jdeemailaddress", "electronicaddress", "emailaddress", *jde_names("F01151", "EA", "EMAL")),
    # the supplier's default tax area (D-267: a US company's voucher takes it)
    "tax_area": ("taxratearea", *jde_names("F0401", "A6", "TXA2")),
}  # fmt: skip
_OWN = ("tax_id", "tax_id2", "currency", "address1", "postal_code", "email")  # what only this list gives
CANADA = ("", "CA", "CAN", "CANADA")
_QST_NUMBER = re.compile(r"\d{10}tq\d{4}")  # a QST number, as ``norm_tax_number`` writes it
_GST_ACCOUNT = re.compile(r"\d{9}rt\d{0,4}")  # as ``norm_tax_number`` writes it
_OTHER_ACCOUNT = re.compile(r"(\d{9})r[a-z]\d{0,4}")  # the same business' payroll or income tax account
_OLD_NUMBER = re.compile(r"r(\d{9})")
_DASHED = re.compile(r"\d+-\d")  # an EIN (12-3456789) or a personal number (123-45-6789)


def positions(headers: Iterable[Any]) -> dict[str, int]:
    """{field: where its column is} among a file's column names, each compared as ``header_key``."""
    keys = [header_key(h) for h in headers]
    found = {field: keys.index(key) for field, key in COLUMNS.items() if key in keys}
    for field, first in SECOND.items():
        if first in found and COLUMNS[first] in keys[found[first] + 1 :]:
            found[field] = keys.index(COLUMNS[first], found[first] + 1)
    return found


def in_master(headers: Iterable[Any]) -> dict[str, str]:
    """{field: header} of the vendor information a supplier master export carries beside its own columns (its tax
    ID, currency, address, e-mail: ``IN_MASTER``); {} when it carries none of them (the supplier master's grid)."""
    found = match_columns([str(h) for h in headers if h is not None], IN_MASTER)
    return found if set(found) & set(_OWN) else {}


def is_vendor_info(headers: Iterable[Any]) -> bool:
    """Whether a file with these column names is the vendor information list: it has the list's own supplier number,
    name and tax ID columns (the supplier master's grid names them otherwise)."""
    found = positions(headers)
    return all(field in found for field in NEEDS)


def compared(tax_id: Any) -> str:
    """A tax ID as it is compared: its letters and digits, lower case; "" for one with fewer than nine digits (a
    word, N/A), which is no number to match."""
    text = norm_tax_number(tax_id)
    return text if sum(c.isdigit() for c in text) >= 9 else ""


def gst_number(tax_id: Any, country: Any = "") -> str:
    """The GST/HST number a tax ID is, "" when it is none: a business number with its RT account as written; the nine
    digits of one written with another program account or as an older "R" number; nine digits alone, without a
    dash, of a supplier in Canada (or of no country)."""
    written = str(tax_id or "").strip()
    text = norm_tax_number(written)
    if _GST_ACCOUNT.fullmatch(text):
        return written.lstrip("#").strip()
    other = _OTHER_ACCOUNT.fullmatch(text) or _OLD_NUMBER.fullmatch(text)
    if other:
        return other.group(1)
    here = str(country or "").strip().upper() in CANADA
    return text if re.fullmatch(r"\d{9}", text) and here and not _DASHED.search(written) else ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def rows_of(records: Iterable[tuple[Any, ...]], where: dict[str, int]) -> tuple[list[dict[str, str]], Counter[str]]:
    """(the suppliers, what was left out) of the list's rows under its column names. Left out, counted: "totals", a
    row with neither number nor name (the last row, only its payment count filled); "no number", "no name"; an empty
    row is not counted. A supplier number listed twice is kept once, its last row ("twice")."""
    kept: dict[str, dict[str, str]] = {}
    left: Counter[str] = Counter()
    for record in records:
        if not any(cell not in (None, "") for cell in record):
            continue
        row = {field: _text(record[at]) if at < len(record) else "" for field, at in where.items()}
        if not row["erp_id"] or not row["vendor_name"]:
            left["no name" if row["erp_id"] else "no number" if row["vendor_name"] else "totals"] += 1
            continue
        row.setdefault("tax_id", "")
        second = row.pop("tax_id2", "")  # (the additional tax ID: the GST/HST number when the tax ID is none)
        row["gst"] = gst_number(row["tax_id"], row.get("country"))
        if not row["gst"] and gst_number(second, row.get("country")):
            row["gst"], row["tax_id"] = gst_number(second, row.get("country")), row["tax_id"] or second
        row["qst"] = second if _QST_NUMBER.fullmatch(norm_tax_number(second)) else ""
        row["tax_id_norm"] = compared(row["tax_id"])
        if row["erp_id"] in kept:
            left["twice"] += 1
        kept[row["erp_id"]] = row
    return list(kept.values()), left


def read(source: bytes, name: str, sheet: str = "") -> dict[str, Any] | None:
    """An uploaded workbook read as the vendor information list: {"name", "rows" (``rows_of``), "left" (what was
    left out), "found" and "missing" (the columns, by their labels)}; None when its column names are not this
    list's or it cannot be opened (another supplier file: the caller reads it its own way). ``ChartError``, in plain
    words, for the list when its rows cannot be read."""
    records = chart_file.read_rows(source, name, sheet)  # (``sheet``: the sheet of that name, else the first)
    try:
        for at in range(chart_file.HEADER_ROWS):  # (row 1, or under a grid's title rows: B-2273)
            try:
                headers = next(records, None)
            except ChartError:  # no workbook that can be read: the caller's own reader says why
                return None
            if headers is None:
                return None
            where, above = positions(headers), at
            if all(field in where for field in NEEDS):
                break
        else:
            return None
        rows, left = rows_of(records, where)
    finally:
        records.close()
    return {"name": name, "rows": _numbers_first(rows), "left": dict(left), "header_row": above,
            "found": [(label, _text(headers[where[field]])) for field, label in LABELS.items() if field in where],
            "missing": [label for field, label in LABELS.items() if field not in where]}  # fmt: skip


_UNIT = re.compile(r"[0-9][0-9A-Z]*")  # a business unit's number as JD Edwards writes it ("00210", "0031045990")


def _numbers_first(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """The rows with the business unit's number and name each in its own field: the list's two "Business Unit"
    columns told apart by what they hold, not by their order (put the other way round, the name was kept as the
    number)."""

    def numbers(field: str) -> int:
        return sum(1 for row in rows if _UNIT.fullmatch(row.get(field, "")))

    if not rows or "business_unit_name" not in rows[0]:
        return rows
    if numbers("business_unit_name") <= numbers("business_unit"):
        return rows
    return [{**row, "business_unit": row["business_unit_name"], "business_unit_name": row["business_unit"]}
            for row in rows]  # fmt: skip


def master_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """The list's suppliers as the vendor list's import takes them (``Store.import_vendor_master``): the name, the
    number, the GST/HST number its tax ID is, the payment terms and, for a supplier in Canada, its province. No hold
    code, status or default account: the vendor keeps what it has."""
    from .vendors import ACTIVE, _province_or_blank

    return [{"vendor_name": row["vendor_name"], "erp_id": row["erp_id"], "gst": row["gst"], "terms": row.get("terms"),
             "status": ACTIVE, "status_given": False, "qst": row.get("qst", ""), "pst": "", "default_gl": None,
             "hold": None,
             "province": _province_or_blank(row.get("state", ""))
             if str(row.get("country") or "").strip().upper() in CANADA else ""}
            for row in rows]  # fmt: skip


def with_master(rows: list[dict[str, Any]], info: list[dict[str, str]]) -> list[dict[str, Any]]:
    """The supplier master's rows (``vendors.master_rows``: each with its payment hold) of a report that carries the
    vendor information too (``in_master``), with what that says of each supplier (``info``: ``rows_of``): its GST/HST
    number only when its tax ID reads as one (an EIN or a word is none), its QST number, and for a supplier in
    Canada its province; the payment terms when the master's own column gives none."""
    by_number = {row["erp_id"]: row for row in master_rows(info)}
    out = []
    for row in rows:
        mine = by_number.get(str(row.get("erp_id") or "").strip())
        if mine is not None:
            row = {**row, "gst": mine["gst"], "qst": mine["qst"] or row.get("qst") or "",
                   "province": mine["province"] or row.get("province") or "",
                   "terms": row.get("terms") if row.get("terms") is not None else mine["terms"]}  # fmt: skip
        out.append(row)
    return out


def said(read: dict[str, Any], known: set[str], markers: int) -> list[str]:
    """What the list holds, a line each, before anything is kept. ``known``: the supplier numbers the vendor list has
    already; ``markers``: how many of the names carry a retired supplier's marker."""
    rows, left = read["rows"], read["left"]
    there = sum(1 for row in rows if row["erp_id"].lstrip("0") in known)
    lines = [f"{ui.plural(len(rows), 'supplier')} to load: {there:,} already in the supplier list by their number "
             f"(each keeps its hold and everything set on it), {len(rows) - there:,} new."]  # fmt: skip
    out = [f"{ui.plural(left[why], 'row')} {words}" for why, words in (
        ("totals", "of totals at the end"), ("no number", "without a supplier number"),
        ("no name", "without a name"), ("twice", "of a supplier number listed twice (its last row is kept)"),
    ) if left.get(why)]  # fmt: skip
    if out:
        lines.append(f"Left out: {'; '.join(out)}.")
    with_id = [row for row in rows if row["tax_id"]]
    numbers = Counter(row["gst"] for row in rows if row["gst"])
    lines.append(
        f"Tax ID: {len(with_id):,} have one. {sum(numbers.values()):,} read as a Canadian business number (the "
        f"GST/HST number an invoice's is filled from and compared with); "
        f"{sum(1 for row in with_id if row['tax_id_norm'] and not row['gst']):,} are another number (an EIN, a "
        f"personal or foreign one), and {sum(1 for row in with_id if not row['tax_id_norm']):,} are words such as "
        "CHARITABLE or N/A: both kept as written, compared with nothing.")  # fmt: skip
    shared = sum(1 for count in numbers.values() if count > 1)
    if shared:
        lines.append(f"{ui.plural(shared, 'business number')} {'is' if shared == 1 else 'are'} listed for more than "
                     "one supplier: a number like that picks no supplier on its own.")  # fmt: skip
    currencies = Counter(row.get("currency") or "none" for row in rows)
    lines.append("Currency: " + ", ".join(f"{code} {count:,}" for code, count in currencies.most_common(6)) + ".")
    if markers:
        lines.append(f"{ui.plural(markers, 'name')} {'carries' if markers == 1 else 'carry'} a retired supplier's "
                     "marker (DNU, DO NOT USE): kept on hold, as the supplier list keeps them.")  # fmt: skip
    return lines
