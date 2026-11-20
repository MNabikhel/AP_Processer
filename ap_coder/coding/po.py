"""Purchase orders and PO matching (2-way and, when receipts are known, 3-way).

AP imports open purchase orders from the ERP (one row per PO line, CSV or Excel). When an invoice
quotes a PO number, each invoice line is paired with the PO line it bills for, and the checks
compare what is billed with what was ordered (and received):

* PO_UNKNOWN (warning): the quoted PO is not in the list
* PO_CLOSED (warning): the PO is closed
* PO_VENDOR_MISMATCH (warning): the PO was raised for another vendor
* PO_COMPANY_DIFFERS (warning): the PO is an order of another JD Edwards company than the invoice's (D-267)
* PO_LINE_NOT_ON_PO (warning): an invoice line matches nothing on the PO
* PO_LINE_AMBIGUOUS (warning): an invoice line could bill several PO lines alike, none is guessed (D-282)
* PO_PRICE_OVER (warning): unit price above the PO price beyond the tolerance
* PO_QTY_OVER (warning): billed so far (this and earlier invoices) exceeds the quantity ordered
* PO_NOT_RECEIVED (warning): billed so far exceeds the quantity received (3-way match)
* PO_OVER_BILLED (warning): invoices on this PO add up to more than the PO total
* PO_CODING_DIFFERS (info): the PO codes a line to another GL account / cost center
* PO_NOT_QUOTED (info): no PO on the invoice although the vendor has open POs
* PO_MATCHED (info): everything is within tolerance

Matching is local and deterministic; nothing here calls Azure.

The match also says what is left to expense on each PO line after the invoice (``PoMatch.left``, ``LineLeft``): shown on
the review screen, never a check, so an invoice without a PO or over what is left is approved as before.

Entry points: ``po_findings`` (the checks of an invoice), ``match_invoice`` (the line by line match the review
screen shows), ``rows_from_records`` and ``map_columns`` (the import), ``billed_by_line``.
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, fields, replace
from difflib import SequenceMatcher
from functools import lru_cache
from typing import TYPE_CHECKING, Any, NamedTuple

from ..core.caches import MB, Bounded, size_of
from ..core.chart import code_of, join_account, split_account
from ..core.headers import jde_names, match_columns
from ..core.keys import company_code, master_vendor_id, order_key_of, po_key
from ..core.memory import vendor_key
from ..core.safe import parse_amount
from ..core.schema import VOUCHER_MATCH

if TYPE_CHECKING:
    from ..core.schema import InvoiceCoding
    from ..core.store import Store

ERROR, WARNING, INFO = "error", "warning", "info"
OPEN, CLOSED = "open", "closed"
PRICE_TOLERANCE = 0.02  # unit price may be 2% above the PO price...
PRICE_TOLERANCE_ABS = 0.50  # ...or 50 cents, whichever is larger...
PRICE_FLOOR_SHARE = 0.10  # ...but the 50 cents never more than 10% of the PO price (a $0.10 bolt: 1 cent, B-927)
AMOUNT_TOLERANCE_ABS = 1.00  # a line's amount may be 2% or a dollar above its quantity at the PO price
TOTAL_TOLERANCE = 0.02  # invoices on a PO may total 2% more than the PO (rounding, small freight)
MATCH_THRESHOLD = 0.45
SAME_PRICE = 0.2  # what a PO line's price, the same as an invoice line's, adds to how much the two resemble
# A PO quoted by PO_KEPT_FROM invoices or more has them kept in each process (``po_billing``, D-202): each invoice as
# ``Store.po_invoices`` reads it and what its lines bill on the PO's lines, brought up to date from the invoices changed
# since (``Store.po_invoices_since``), not read and matched again at each open and check of one of them (the biggest
# vendor's standing PO at 300,000 invoices: 3,382 invoices, 0.2-0.3 s). Only a PO whose read is slow: at 300,000, 35
# POs have 100 invoices or more, 63 MB kept, and the cheap ones' asks let go of the costly one (D-202's review); 1 has
# 1,000 or more. Within PO_KEPT_MOST bytes (D-195): that one holds 22.5 MB. Those read at each ask keep the matches of
# their lines (``_MATCHED``), within MATCHED_MOST bytes.
PO_KEPT_FROM = 1000
PO_KEPT_MOST = 24 * MB
MATCHED_MOST = 6 * MB

Finding = tuple[str, str, str, "int | None"]


def _pd(*items: str) -> tuple[str, ...]:
    return jde_names("F4311", "PD", *items)


# Column names people use in ERP exports, normalised (lower case, letters and digits only); then JD Edwards' fields
# of the order detail table (F4311) as a query tool titles them (ReportsNow, D-266: "DOCO", "PDDOCO", "F4311.PDDOCO").
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "po_number": ("ponumber", "po", "pono", "ponum", "purchaseorder", "purchaseordernumber", "ordernumber",
                  "order", "pobr", "bondecommande", *_pd("DOCO")),
    # Optional, JD Edwards' (D-264): the order type and company that, with the number, name the order on a voucher.
    "order_type": ("orty", "ordertype", *_pd("DCTO")),
    "order_company": ("orderco", "ordercompany", *_pd("KCOO")),
    "vendor_name": ("vendorname", "vendor", "supplier", "suppliername", "supplierdescription", "fournisseur",
                    *jde_names("F0101", "AB", "ALPH")[1:], "alphaname"),
    # Optional: the supplier's number in the ERP (JD Edwards' address number); a supplier column of digits is one too.
    "supplier_number": ("addressnumber", "suppliernumber", "supplierno", "vendornumber", "vendorno", *_pd("AN8")),
    # Not "Item" or "Item Number": in most exports that is the part number (SKU), not the PO line.
    "line_number": ("linenumber", "line", "lineno", "linenum", "poline", *_pd("LNID")),
    "description": ("description", "desc", "itemdescription", "linedescription", "longdescription", "product",
                    "service", *_pd("DSC1")),
    "quantity": ("quantity", "qty", "orderedqty", "qtyordered", "quantityordered", "ordered", *_pd("UORG")),
    "unit_price": ("unitprice", "price", "unitcost", "rate", "prixunitaire", *_pd("PRRC")),
    "amount": ("amount", "linetotal", "extendedamount", "extended", "extendedprice", "extendedcost", "total",
               "lineamount", "netamount", "originalorderedamount", *_pd("AEXP")),
    # Optional: what the ERP has left open on the line (JD Edwards' AOPN), for what is left to expense.
    "open_amount": ("openamount", "amountopen", "openamt", "amtopen", "openbalance", "amounttoreceive", *_pd("AOPN")),
    # Optional, JD Edwards' (UOPN, UOM): the quantity left open on the line and its unit, for the voucher match.
    "open_quantity": ("quantityopen", "openquantity", "qtyopen", "openqty", "quantitytoreceive", "unitsopen",
                      *_pd("UOPN")),
    "uom": ("um", "unitofmeasure", *_pd("UOM")),
    "received_quantity": ("receivedquantity", "received", "qtyreceived", "receivedqty", "quantityreceived",
                          "receipts", "recu", "unitsreceived", *_pd("UREC")),
    "gl_code": ("glcode", "gl", "glaccount", "account", "accountcode", "costcode", "expenseaccount", "objacct",
                "objectaccount", *_pd("OBJ")),
    # (JD Edwards' cost business unit of a line, OMCU; the order's own business unit, MCU)
    "cost_center": ("costcenter", "costcentre", "cc", "department", "dept", "businessunit", *_pd("OMCU", "MCU")),
    # Optional, JD Edwards' (SUB): the subsidiary of the account, with the cost center and object (``gl_code``).
    "subsidiary": ("subsidiary", "sub", *_pd("SUB")),
    # Optional: a JD Edwards account string (business unit.object.subsidiary), its parts the cost center and GL code.
    "account_number": ("accountnumber", "acctno", "accountno", *_pd("ANI")),
    # Optional, JD Edwards' (TX, EXR1, TXA1): the PO line's own tax, typed on its voucher match row.
    "tax_flag": ("taxyn", *_pd("TX")),
    "tax_code": ("taxexpl", "taxexplanation", "taxexplcode", "taxexplanationcode", *_pd("EXR1")),
    "tax_area": ("taxratearea", "taxarea", *_pd("TXA1")),
    # Optional, JD Edwards' (SBL, SBLT, D-268): the line's subledger and its type, which an account whose posting edit
    # code is L requires: the invoice line billing it takes them.
    "subledger": ("subledger", *_pd("SBL")),
    "subledger_type": ("subtype", "subledgertype", *_pd("SBLT")),
    # Optional, a subcontract's (D-273): the percent of each progress billing on the line held back until the work is
    # done, JD Edwards' Percent - Retainage (PCRT, of the order header F4301 too); never its Retainage Rule (RTNR, a
    # code). "Retainage %", "Retention %", "Holdback %".
    "retainage": ("retainage", "retainagepercent", "retainagepercentage", "retainagepct", "retainagerate",
                  "percentretainage", "retention", "retentionpercent", "retentionpercentage", "holdback",
                  "holdbackpercent", "holdbackpercentage", *_pd("PCRT"), *jde_names("F4301", "PH", "PCRT")),
    "status": ("status", "postatus", "state"),
    # Optional, JD Edwards' (NXTR, LTTR): a line whose next status is 999 is closed, one whose last is 980 cancelled.
    "next_status": ("nextstatus", "nextstat", *_pd("NXTR")),
    "last_status": ("laststatus", "laststat", *_pd("LTTR")),
    # Optional: the PO's currency (JD Edwards' CRCD); a file without one: taken as CAD (B-2612).
    "currency": ("currencycode", "currcode", "curcode", "curcod", "curr", "ccy", "devise", *_pd("CRCD")),
    # Optional, JD Edwards' (D-264): an order in another currency than the company's (its Base Curr) has its amounts
    # and unit cost in that currency in the foreign columns; the others are the company's.
    "base_currency": ("basecurr", "basecurrency", "basecurrencycode"),
    "foreign_open_amount": ("foreignamounttoreceive", "foreignopenamount", *_pd("FAP")),
    "foreign_unit_price": ("foreignunitcost", "foreignunitprice", "fprc", *_pd("FRRC")),
    "foreign_amount": ("originalorderedforeignamount", "foreignextendedprice", "foreignextendedcost", *_pd("FEA")),
}  # fmt: skip
CLOSED_STATUSES = {"closed", "close", "complete", "completed", "fermé", "ferme"}
# An account written in JD Edwards' other formats (its short ID after "*", its third number after "/", D-266): not
# business unit.object.subsidiary, so the line's cost center, object and subsidiary columns are read instead.
_OTHER_FORMATS = ("*", "/")
JDE_CLOSED, JDE_CANCELLED = "999", "980"  # a JD Edwards line's next status once closed, last status once cancelled
SUBCONTRACT = "OS"  # JD Edwards' order type of a subcontract (Subcontract Management, system 44; D-273)
REQUIRED_COLUMNS = ("po_number", "description")
TEMPLATE_COLUMNS = (
    "po_number", "vendor_name", "line_number", "description", "quantity", "unit_price", "amount", "open_amount",
    "received_quantity", "gl_code", "cost_center", "status", "currency",
)  # fmt: skip


def map_columns(headers: list[str]) -> dict[str, str]:
    """{canonical field: column in the file} for the columns that could be recognised."""
    return match_columns(headers, COLUMN_ALIASES)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or str(value).strip() == ""


def _text(value: Any) -> str:
    if _blank(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def rows_from_records(records: list[dict[str, Any]], columns: dict[str, str]) -> tuple[list[dict[str, Any]], int]:
    """Turn spreadsheet rows into PO line dicts using ``map_columns`` output. Returns (rows, skipped)."""
    rows: list[dict[str, Any]] = []
    skipped = 0
    for rec in records:

        def get(target: str, rec: dict[str, Any] = rec) -> Any:
            column = columns.get(target)
            return rec.get(column) if column else None

        number, description = _text(get("po_number")), _text(get("description"))
        if not number or not description:
            skipped += 1
            continue
        qty, price, amount = parse_amount(get("quantity")), parse_amount(get("unit_price")), parse_amount(get("amount"))
        opened = parse_amount(get("open_amount"))
        currency, base = _text(get("currency")).upper(), _text(get("base_currency")).upper()
        if currency and base and currency != base:  # an order in a foreign currency: its own amounts (D-264)
            price, amount, opened = (
                parse_amount(get(f)) if columns.get(f) else was
                for f, was in (
                    ("foreign_unit_price", price),
                    ("foreign_amount", amount),
                    ("foreign_open_amount", opened),
                )
            )
        if amount is None and not qty and not price and opened is not None:  # (JD Edwards keeps 0, not blank, B-2660)
            qty = price = None
        if qty is None and price is None and amount is None:  # (JD Edwards' order details: what is left to receive)
            amount = opened
        if amount:  # an ERP writes 0 rather than blank for the quantity and price of a lump-sum line
            qty, price = qty or None, price or None
        amount_only = qty is None and price is None and amount is not None  # e.g. "Consulting services: 5,000"
        if qty is None:
            qty = 1.0 if price is None or amount is None or not price else amount / price
        if price is None:
            price = (amount / qty) if amount is not None and qty else 0.0
        if amount is None:
            amount = round(qty * price, 2)
        line = parse_amount(get("line_number"))
        vendor, supplier = _text(get("vendor_name")), _text(get("supplier_number"))
        if vendor.isdigit() and not supplier:  # JD Edwards' "Supplier" column: its address number, not a name
            vendor, supplier = "", vendor
        gl_code, cost_center = _text(get("gl_code")), _text(get("cost_center"))
        if gl_code and (subsidiary := _text(get("subsidiary"))):  # (the object and its subsidiary, JD Edwards' SUB)
            gl_code = code_of(gl_code, subsidiary)
        account = _text(get("account_number"))
        if "." in account and not account.startswith(_OTHER_FORMATS):
            # "business unit.object.subsidiary": the account, whatever its parts' columns say
            cost_center, gl_code = split_account(account)
        elif account and not gl_code and not cost_center:  # no dot: a GL code
            gl_code = account
        company = _text(get("order_company"))
        rows.append(
            {
                "po_number": number,
                "vendor_name": vendor,
                "supplier_number": supplier,
                "order_type": _text(get("order_type")).upper(),
                "order_company": company.zfill(5) if company.isdigit() else company,
                "line_number": int(line) if line is not None else None,
                # JD Edwards' own line number as its screens show it ("12.000"; a split line "12.500"), for the
                # voucher match: AP Coder's ``line_number`` is a whole number. The sync gives it apart (``erp_line``:
                # its ``line_number`` is already AP Coder's).
                "erp_line": _erp_line(get("erp_line") if columns.get("erp_line") else get("line_number")),
                "description": description,
                "quantity": qty,
                "unit_price": price,
                "amount": amount,
                "amount_only": amount_only,
                "open_amount": opened,
                "open_quantity": parse_amount(get("open_quantity")),
                "uom": _text(get("uom")).upper(),
                "received_quantity": parse_amount(get("received_quantity")),
                "gl_code": gl_code,
                "cost_center": cost_center,
                "status": CLOSED if _closed(get) else OPEN,
                "tax_flag": _text(get("tax_flag")).upper()[:1],
                "tax_code": _text(get("tax_code")).upper(),
                "tax_area": _text(get("tax_area")).upper(),
                "subledger": _text(get("subledger")).upper()[:8],
                "subledger_type": _text(get("subledger_type")).upper()[:1],
                "retainage": retainage_percent(get("retainage")),
                "currency": currency,
            }
        )
    return rows, skipped


def retainage_percent(value: Any) -> float | None:
    """A PO line's retainage as a percent: 10 (JD Edwards' 10.000), "10%", or a fraction 0.1 written so; None for none,
    0, or what can't be one (over 100, below 0, words: a Retainage Rule's code)."""
    number = parse_amount(_text(value).replace("%", "").strip()) if not _blank(value) else None
    if number is None or not math.isfinite(number) or number <= 0 or number > 100:
        return None
    return round(number * 100 if number < 1 else number, 4)


def is_subcontract(po: dict[str, Any]) -> bool:
    """Whether a PO (``Store.purchase_order``'s) is a subcontract (D-273): JD Edwards' order type OS, or a line of it
    holds back a retainage percent of what it bills."""
    return str(po.get("order_type") or "").upper() == SUBCONTRACT or any(
        pl.get("retainage") for pl in po.get("lines") or []
    )


def open_lines(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each PO's open lines (``rows_from_records``' rows): a closed line (JD Edwards' 999) leaves while another line of
    its PO is open, so no invoice is matched to it; with none open, every line stays and the PO is closed."""
    open_pos = {order_key_of(r) for r in rows if r.get("status") != CLOSED}
    return [r for r in rows if r.get("status") != CLOSED or order_key_of(r) not in open_pos]


def of_supplier(order: dict[str, Any], vendor: str, vendor_id: Any = "") -> bool:
    """Whether a PO (``Store.orders_numbered``' row) is the supplier's: by its vendor key, or its supplier number."""
    supplier = str(vendor_id or "").strip().lstrip("0")
    return bool((vendor and order.get("vendor_key") == vendor)
                or (supplier and str(order.get("supplier_number") or "").strip().lstrip("0") == supplier))  # fmt: skip


def pick_order(
    orders: list[dict[str, Any]], vendor: str, vendor_id: Any = "", company: Any = ""
) -> dict[str, Any] | None:
    """The order an invoice quoting a PO number bills, of ``orders`` (the POs of that number: ``Store.orders_numbered``,
    D-267): the only one; else the only one of its supplier (``vendor``: its vendor key, ``vendor_id``: its supplier
    number); else, of the supplier's, the only one of the invoice's ``company``. None: no one answer, held for a person,
    never a guess."""
    if len(orders) <= 1:
        return orders[0] if orders else None
    mine = [o for o in orders if of_supplier(o, vendor, vendor_id)]
    if len(mine) > 1 and str(company or "").strip():
        mine = [o for o in mine if company_code(o.get("order_company")) == company_code(company)]
    return mine[0] if len(mine) == 1 else None


def _erp_line(value: Any) -> str:
    """A line number as JD Edwards shows it, three decimals ("12" and 12.0 are "12.000"); "" for none."""
    number = parse_amount(value)
    return f"{number:.3f}" if number is not None and math.isfinite(number) and number > 0 else ""


def _closed(get: Callable[[str], Any]) -> bool:
    """Whether a row's line is closed: its status says so, or JD Edwards' next status is 999 (closed) or its last
    status 980 (cancelled)."""
    if _text(get("status")).lower() in CLOSED_STATUSES:
        return True
    return _text(get("next_status")) == JDE_CLOSED or _text(get("last_status")) == JDE_CANCELLED


# --- Matching ------------------------------------------------------------------------------------------

_STOP = {"the", "and", "for", "of", "a", "an", "to", "in", "with", "on", "per", "de", "la", "le", "et", "du", "des"}
_PREFIXES = re.compile(r"^\s*(return|credit|returned|refund|retour|crédit|credit note)\s*[-:–]?\s*", re.IGNORECASE)


@lru_cache(maxsize=4096)
def _tokens(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", _PREFIXES.sub("", text or "").lower())
    return frozenset(w for w in words if w not in _STOP and len(w) > 1)


@lru_cache(maxsize=8192)  # (0.75 KB a pair, D-195; a big PO's invoices' lines matched once a process, D-202)
def similarity(a: str, b: str) -> float:
    """0..1: how likely two line descriptions are the same item (word overlap, then character similarity)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb) / min(len(ta), len(tb))
    ratio = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return max(overlap * 0.9, ratio)


# A charge rather than goods: freight and delivery. An invoice's charge line and a PO's charge line are the same line
# whatever their words ("Freight Amount", "SHIPPING & HANDLING"), as alike as CHARGE_ALIKE (D-264): two charge lines of
# the same words, or of the same price, are still more alike.
# The reader's line for a charge printed beside the subtotal under no label it found (``offline_coder.READ_CHARGE``,
# B-2639) is one too. Not "courier" or "transport": a courier's own service ("Same-day document courier") is no
# charge on goods, and its monthly invoice billed a PO's freight line.
CHARGE = re.compile(
    r"(?i)\b(?:freight|shipping|delivery|deliveries|cartage|livraison|fret|expedition|expédition)\b"
    r"|^charges not in the line table$"
)
CHARGE_ALIKE = 0.8


def is_charge(text: str) -> bool:
    """Whether a line's description is a charge (freight, delivery) rather than goods or a service."""
    return bool(CHARGE.search(text or ""))


# What else a supplier adds to an invoice beside what was ordered (D-265): a surcharge or a fee. Not a charge to pair
# with a PO's freight line (``is_charge``), but one that Settings → JD Edwards E1's "Charges not on the purchase
# order" may key on a G/L line of its own when the PO has no line for it.
EXTRA_CHARGE = re.compile(
    r"(?i)\b(?:surcharges?|eco[- ]?fees?|ehf|levy|levies|supplément|frais)\b"
    r"|\b(?:environmental|handling|small[- ]order|recycling|disposal|fuel)\b.{0,20}\b(?:fees?|charges?|levy)\b"
)


def is_extra_charge(text: str) -> bool:
    """Whether a line is a charge not for goods or a service (freight, a fuel surcharge, an environmental fee)."""
    return is_charge(text) or bool(EXTRA_CHARGE.search(text or ""))


def _alike(description: Any, po_description: Any) -> float:
    """How much an invoice line resembles a PO line (``similarity``), a charge line and a charge line at least
    CHARGE_ALIKE: what ``_po_line`` and ``_price_decides`` both match by."""
    score = similarity(description, po_description)
    if score < CHARGE_ALIKE and isinstance(description, str) and isinstance(po_description, str) \
            and is_charge(description) and is_charge(po_description):  # fmt: skip
        return CHARGE_ALIKE
    return score


def _price_band(b: float) -> tuple[float, float]:
    """(the size of the price ``b``, how far another may be from it and still be the same price)."""
    floor = min(PRICE_TOLERANCE_ABS, abs(b) * PRICE_FLOOR_SHARE)
    return abs(b), max(floor, abs(b) * PRICE_TOLERANCE)


def _same_price(a: float, b: float) -> bool:
    price, room = _price_band(b)
    return abs(abs(a) - price) <= room


def _amount_over(m: LineMatch) -> float | None:
    """How far a line's amount is above its quantity at the PO price, beyond 2% or a dollar (B-927); None when not."""
    if m.po_unit_price is None or m.quantity <= 0 or m.amount <= 0:
        return None
    at_po = round(m.quantity * abs(m.po_unit_price), 2)
    if m.amount > at_po * (1 + PRICE_TOLERANCE) + AMOUNT_TOLERANCE_ABS:
        return m.amount - at_po
    return None


def po_lines_billed(invoice_lines: list[dict[str, Any]], po_lines: list[dict[str, Any]],
                    matched: dict[Any, tuple[int, ...]] | None = None) -> list[int | None]:  # fmt: skip
    """The PO line_number each invoice line bills, by position (None: matches nothing). Each invoice line takes
    the PO line it most resembles (a matching unit price counts in favour), the first of lines alike; several invoice
    lines may bill the same PO line. By position, so an invoice that repeats a line number (page 2 starting again at
    1) still pairs every line. ``matched``: as ``po_line_choices``'. Month-end's accruals pair so (``billed_on``); an
    invoice's match and its PO's billing share lines alike out instead (``share_out``, D-282)."""
    return [found[0] if found else None for found in po_line_choices(invoice_lines, po_lines, matched)]


def po_line_choices(invoice_lines: list[dict[str, Any]], po_lines: list[dict[str, Any]],
                    matched: dict[Any, tuple[int, ...]] | None = None) -> list[tuple[int, ...]]:  # fmt: skip
    """The PO lines each invoice line most resembles, by position (``_po_candidates``: a matching unit price counts
    in favour): one; none; or several alike (the same description and price), which ``share_out`` tells apart.
    ``matched`` keeps each (description, unit price)'s across calls for the same ``po_lines``: the invoices billing a
    PO repeat its lines (a PO billed by thousands of invoices, D-199). A subcontract's progress billing, its lines named
    for the billing ("Progress billing #3") and not the work, bills the one line it can be by its amount
    (``_by_amount``, D-273)."""
    out = []
    for li in invoice_lines:
        description, price = li.get("description", ""), li.get("unit_price")
        key = (description, price) if matched is not None else None
        try:
            found = matched[key] if key is not None and key in matched else _UNKNOWN  # type: ignore[index,operator]
        except TypeError:  # (a description or price that can't be a key: worked out each time)
            key, found = None, _UNKNOWN
        if found is _UNKNOWN:
            found = _po_candidates(description, po_lines, lambda _, pl, price=price: _same_price(
                float(price or 0), float(pl.get("unit_price") or 0)))  # fmt: skip
            if key is not None:
                matched[key] = found  # type: ignore[index]
        out.append(found if found or not _retains(po_lines, matched) else _by_amount(li, po_lines))
    return out


_UNKNOWN: Any = object()  # (a line's PO line not worked out yet)
_RETAINS = ("retains",)  # ``matched``'s entry of whether the PO's lines hold back a percent (its other keys are pairs)


def _retains(po_lines: list[dict[str, Any]], matched: dict[Any, tuple[int, ...]] | None) -> bool:
    """Whether a line of the PO holds back a retainage percent (a subcontract's, D-273): asked of its lines once for
    all the invoices billing it (kept in ``matched``)."""
    if matched is not None and _RETAINS in matched:
        return bool(matched[_RETAINS])
    found = any(pl.get("retainage") for pl in po_lines)
    if matched is not None:
        matched[_RETAINS] = (1,) if found else ()
    return found


def _by_amount(li: dict[str, Any], po_lines: list[dict[str, Any]]) -> tuple[int, ...]:
    """The line of a subcontract (its lines holding back a retainage percent, D-273) that an invoice line resembling
    none of them bills by its amount: the subcontract's only line, else the only one open for as much (its open
    amount, else its amount, 2% over allowed); none for a charge or a credit, or none or several lines open for it
    (never guessed)."""
    amount = float(li.get("amount") or 0)
    if amount <= 0 or is_charge(str(li.get("description") or "")):
        return ()
    if len(po_lines) == 1:
        return (int(po_lines[0]["line_number"]),)

    def room(pl: dict[str, Any]) -> float:
        opened = pl.get("open_amount")
        return float(opened if opened is not None else pl.get("amount") or 0)

    fits = [pl for pl in po_lines if amount <= room(pl) * (1 + TOTAL_TOLERANCE) + 0.01]
    return (int(fits[0]["line_number"]),) if len(fits) == 1 else ()


class Alike(NamedTuple):
    """An invoice line that could bill any of several PO lines alike (the same description and price) and that nothing
    tells apart (``share_out``): their line_numbers; ``room``, whether each has room left for it (else none has)."""

    lines: tuple[int, ...]
    room: bool


def share_out(po_lines: list[dict[str, Any]], invoices: list[tuple[list[dict[str, Any]], bool]],
              matched: dict[Any, tuple[int, ...]] | None = None) -> list[list[int | Alike | None]]:  # fmt: skip
    """The PO line each line of ``invoices`` bills ([(its lines, whether it is exported)], in invoice order), by
    position: the one it most resembles (``po_line_choices``); of PO lines alike, the one its own coding or words point
    to (``_pointed``), else the one with room left for it (``_has_room``) after the invoices before it and its own lines
    before took theirs; ``Alike`` when that tells none (several could each take it, or none can): none is guessed
    (D-264, D-282), and it bills none of them. None: it matches nothing."""
    return _shared(po_lines, invoices, [po_line_choices(lines, po_lines, matched) for lines, _ in invoices])


def _shared(po_lines: list[dict[str, Any]], invoices: list[tuple[list[dict[str, Any]], bool]],
            choices: list[list[tuple[int, ...]]]) -> list[list[int | Alike | None]]:  # fmt: skip
    """``share_out`` of ``invoices`` whose lines' ``po_line_choices`` are ``choices``. What is taken of a line's room
    counts the invoices not exported yet where the ERP gave what it has open on the line (it has counted the exported
    ones, D-280), every invoice where it gave none."""
    if all(len(found) <= 1 for mine in choices for found in mine):  # (no lines alike: no room to count)
        return [[found[0] if found else None for found in mine] for mine in choices]
    by_number = {int(pl["line_number"]): pl for pl in po_lines}
    taken: dict[int, float] = {}
    out: list[list[int | Alike | None]] = []
    for (lines, exported), mine in zip(invoices, choices, strict=True):
        shared: list[int | Alike | None] = []
        for li, found in zip(lines, mine, strict=True):
            n = found[0] if len(found) == 1 else _told(li, found, by_number, taken) if found else None
            if isinstance(n, int) and not (exported and _by_open(by_number[n])):
                taken[n] = taken.get(n, 0.0) + _taking(li, by_number[n])
            shared.append(n)
        out.append(shared)
    return out


_WORD = re.compile(r"[a-z0-9]+")


def _told(li: dict[str, Any], alike: tuple[int, ...], by_number: dict[int, dict[str, Any]],
          taken: dict[int, float]) -> int | Alike:  # fmt: skip
    """Which of the PO lines ``alike`` an invoice line bills (``share_out``): the one it points to, else the one of
    those with room left for it; ``Alike`` when that tells none."""
    pointed = _pointed(li, alike, by_number)
    if len(pointed) == 1:
        return pointed[0]
    fits = tuple(n for n in pointed if _has_room(li, by_number[n], taken.get(n, 0.0)))
    return fits[0] if len(fits) == 1 else Alike(fits, True) if fits else Alike(pointed, False)


def _pointed(li: dict[str, Any], alike: tuple[int, ...], by_number: dict[int, dict[str, Any]]) -> tuple[int, ...]:
    """Of PO lines alike, those an invoice line points to most: its coding the PO line's (the business unit, the
    object, the subledger), the PO line's business unit or subledger (a job's number) among its words; all of them
    when nothing tells them apart (D-282)."""
    coded = [str(li.get(k) or "").strip().upper() for k in ("predicted_cost_center", "predicted_gl_code", "subledger")]
    words = set(_WORD.findall(str(li.get("description") or "").lower()))

    def told(pl: dict[str, Any]) -> int:
        own = [str(pl.get(k) or "").strip().upper() for k in ("cost_center", "gl_code", "subledger")]
        same = sum(1 for mine, its in zip(coded, own, strict=True) if mine and mine == its)
        return same + sum(1 for its in (own[0], own[2]) if len(its) >= 3 and its.lower() in words)

    scores = [told(by_number[n]) for n in alike]
    best = max(scores)
    return tuple(n for n, score in zip(alike, scores, strict=True) if score == best) if best else alike


def _by_open(pl: dict[str, Any]) -> bool:
    """Whether a PO line's room is what the ERP had open on it (its open amount or quantity), not what was ordered."""
    return pl.get("open_amount" if pl.get("amount_only") else "open_quantity") is not None


def _taking(li: dict[str, Any], pl: dict[str, Any]) -> float:
    """What an invoice line takes of a PO line's room: its amount on one billed by amount, else the quantity it
    matches (a line printed with none: ``unprinted_quantity``)."""
    if pl.get("amount_only"):
        return float(li.get("amount") or 0)
    if not billed_quantity(li) and not is_charge(str(li.get("description") or "")) and pl.get("unit_price"):
        return unprinted_quantity(float(li.get("amount") or 0), float(pl["unit_price"]))[0]
    return matched_quantity(li)


def _has_room(li: dict[str, Any], pl: dict[str, Any], taken: float) -> bool:
    """Whether a PO line, ``taken`` of it already, has room left for an invoice line: what was ordered (or what the
    ERP has open on it), 2% and a cent more on a line billed by amount; a credit, as much billed on it to give back."""
    need = _taking(li, pl)
    if need < 0:
        return taken + need >= -1e-6
    if pl.get("amount_only"):
        room = pl["amount"] if pl.get("open_amount") is None else pl["open_amount"]
        return taken + need <= room * (1 + TOTAL_TOLERANCE) + 0.01
    room = pl["quantity"] if pl.get("open_quantity") is None else pl["open_quantity"]
    return taken + need <= room + 1e-6


def _po_candidates(description: Any, po_lines: list[dict[str, Any]],
                   same_price: Callable[[int, dict], bool]) -> tuple[int, ...]:  # fmt: skip
    """The line_numbers of the PO lines a line of ``description`` most resembles, its price the same as a PO line's
    (``same_price(n, po_line)``, n its position) counting in its favour: one, or several resembling it as much (alike:
    the same description and price), in the PO's order; none: none resembles it enough."""
    best: list[dict[str, Any]] = []
    best_score = 0.0
    for n, pl in enumerate(po_lines):
        score = _alike(description, pl.get("description", ""))
        if same_price(n, pl):
            score += SAME_PRICE
        if score > best_score:
            best, best_score = [pl], score
        elif best and score == best_score:
            best.append(pl)
    return tuple(int(pl["line_number"]) for pl in best) if best_score >= MATCH_THRESHOLD else ()


def _po_line(description: Any, po_lines: list[dict[str, Any]], same_price: Callable[[int, dict], bool]) -> int | None:
    """The line_number of the first PO line a line of ``description`` most resembles (``_po_candidates``); None: none
    resembles it enough."""
    found = _po_candidates(description, po_lines, same_price)
    return found[0] if found else None


@dataclass
class LineMatch:
    invoice_line: int
    description: str
    quantity: float
    unit_price: float
    po_line: int | None = None
    po_description: str = ""
    po_quantity: float | None = None
    po_unit_price: float | None = None
    received: float | None = None
    billed_before: float = 0.0  # quantity billed on this PO line by other invoices
    amount: float = 0.0
    po_amount: float | None = None
    amount_only: bool = False  # the PO line has an amount but no quantity: compare amounts
    billed_before_amount: float = 0.0
    po_gl: str = ""
    po_cc: str = ""
    problems: list[str] = field(default_factory=list)
    # For JD Edwards' voucher match (D-264): the quantity this line matches (``matched_quantity``; on a PO line with no
    # quantity, the one the invoice bills, None when it bills none), and the PO line's own line number ("12.000"),
    # unit of measure, account string and quantity open as the PO file had them.
    matched_quantity: float | None = 0.0
    po_erp_line: str = ""
    po_uom: str = ""
    po_account: str = ""
    po_open_quantity: float | None = None
    po_tax: tuple[str, str, str] = ("", "", "")  # the PO line's own Tax Y/N, explanation code, rate/area ("": none)
    po_subledger: tuple[str, str] = ("", "")  # the PO line's subledger and its type ("": none, D-268)
    po_retainage: float | None = None  # a subcontract line's percent held back of what it bills (D-273)
    # What JD Edwards had open on the line at the PO import (None: not in the file). A line billed by amount with one
    # is checked against it as its left to expense is (D-280: ``billed_before_amount`` then the invoices not exported
    # yet); without one, against its amount, every invoice.
    po_open_amount: float | None = None
    # A goods line printed with no quantity on a PO line with a unit cost (D-283): "amount", its quantity matched is its
    # amount at that cost; "one", 1, as its amount is no whole number of it; "": the quantity printed (or a charge's 1).
    quantity_from: str = ""
    # The PO lines alike (the same description and price) it could bill, none told (D-282), as JD Edwards names them
    # ("1.000"): it bills none of them (``po_line`` None).
    alike: tuple[str, ...] = ()


@dataclass
class LineLeft:
    """What is left to expense on one PO line after this invoice, in net amounts (credits give back). Without an open
    amount: the PO line's amount less what the other live invoices bill on it (``billed_by_line``) and this invoice.
    With one (what the ERP had left on the line when the POs were imported): that less the other invoices not exported
    yet and this invoice, as the ERP has counted the exported ones (this one too, once exported)."""

    po_line: int
    amount: float  # the PO line's amount
    open_amount: float | None  # what the ERP had left on the line at the PO import; None: the import had no such column
    others: float  # the other live invoices on the line (with an open amount: those not exported yet)
    this_invoice: float

    @property
    def left(self) -> float:
        """Below zero: over by that much (said, never a check)."""
        start = self.amount if self.open_amount is None else self.open_amount
        return round(start - self.others - self.this_invoice, 2)


@dataclass
class PoMatch:
    po_number: str
    found: bool
    po: dict[str, Any] | None = None
    lines: list[LineMatch] = field(default_factory=list)
    other_invoices: list[dict[str, Any]] = field(default_factory=list)
    billed_before: float = 0.0  # subtotal of other invoices on this PO
    findings: list[Finding] = field(default_factory=list)
    left: dict[int, LineLeft] = field(default_factory=dict)  # every line of the PO, by its line number
    # Why the invoice's prices and amounts are not compared with the PO's ("": they are, both in one currency, B-2612);
    # then nothing is left to expense either (``left`` empty).
    currencies: str = ""
    orders: list[dict[str, Any]] = field(default_factory=list)  # the orders of its number, none the one (D-267)
    # What the other invoices not exported yet bill on each PO line (net amounts), with an open amount (D-273)
    waiting: dict[int, float] = field(default_factory=dict)

    @property
    def coding_differs(self) -> list[LineMatch]:
        return [m for m in self.lines if "coding" in m.problems]

    @property
    def total_left(self) -> float:
        """What is left to expense on the whole PO after this invoice: its lines' together."""
        return round(sum(line.left for line in self.left.values()), 2)


def orders_named(orders: list[dict[str, Any]]) -> str:
    """The orders of one number as a person tells them apart: "company 00100 OP, Lakeside Supply; company 00200 OP,
    Lakeside Supply"."""

    def one(o: dict[str, Any]) -> str:
        said = " ".join(p for p in (f"company {o['order_company']}" if o.get("order_company") else "",
                                    o.get("order_type") or "") if p)  # fmt: skip
        return ", ".join(p for p in (said, o.get("vendor_name") or "") if p) or str(o.get("po_number") or "")

    return "; ".join(one(o) for o in orders)


def po_label(number: str) -> str:
    """'PO-123' stays as is; '123' becomes 'PO 123'."""
    number = (number or "").strip()
    return number if re.match(r"(?i)^p\.?o\.?(?![a-z])", number) else f"PO {number}"


def _money(v: float) -> str:
    return f"${v:,.2f}"


def _qty(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def match_invoice(coding: InvoiceCoding, store: Store, exclude_invoice_id: int | None = None) -> PoMatch | None:
    """The PO match for one invoice, or None when the invoice quotes no PO (or no POs are loaded)."""
    number = (coding.po_number or "").strip()
    if not number or not store.has_purchase_orders():
        return None
    orders = store.orders_numbered(po_key(number))
    vendor = vendor_key(coding.vendor_name)
    supplier = master_vendor_id(store.vendor_ids(), coding.vendor_name, coding.gst_hst_registration_number)
    head = pick_order(orders, vendor, supplier, coding.company)
    key = head["po_key"] if head else ""
    po = store.purchase_order(key) if key else None
    result = PoMatch(po_number=number, found=po is not None, po=po)
    if po is None and len(orders) > 1:  # the number of several orders, none of them told by supplier or company
        result.orders = orders
        said = (
            f"{po_label(number)} is {len(orders)} purchase orders ({orders_named(orders)}): pick the invoice's company"
        )
        result.findings.append((WARNING, "PO_AMBIGUOUS", f"{said} to tell which it bills", None))
        return result
    if po is None:
        result.findings.append((WARNING, "PO_UNKNOWN", f"{po_label(number)} is not in the purchase orders list", None))
        return result

    result.findings.extend(_header_findings(coding, po))
    result.currencies = _currencies(coding, po, number)
    if result.currencies:
        result.findings.append(
            (WARNING, "PO_CURRENCY_DIFFERS", f"{result.currencies}: its prices and amounts are not compared", None)
        )
    # A PO's cost centers mean nothing without a cost center list (the pilot's, D-53): never compared, never said,
    # never copied to a line (B-534).
    cost_centers = bool(store.list_accounts("cost_centers"))
    po_lines = po["lines"]
    by_line = {pl["line_number"]: pl for pl in po_lines}
    subcontract = is_subcontract(po)
    live = po_billing(store, key, po)
    others = [o for o in live.rows if o["id"] != exclude_invoice_id]
    result.other_invoices = others
    result.billed_before = sum(float(o["coding"].get("subtotal") or 0) for o in others)
    matched: dict[Any, tuple[int, ...]] = {}
    billed_before = live.billed(po, others, matched=matched)
    billed_before_amount = live.billed(po, others, amounts=True, matched=matched)
    # What the ERP has open has counted the invoices exported to it (D-139): a line billed by amount is checked
    # against it with the others not exported yet, and this one unless it is exported (D-280)
    waiting = [o for o in others if o["export_batch"] is None]
    opened = any(pl["open_amount"] is not None for pl in po_lines)
    not_exported = result.waiting = live.billed(po, waiting, amounts=True, matched=matched) if opened else {}
    exported = any(o["id"] == exclude_invoice_id and o["export_batch"] is not None for o in live.rows)
    received = _receipts_checked(store)  # (2-way, D-281: what was received not checked)

    items = [li.model_dump() for li in coding.line_items]
    billed_now: dict[int, float] = {}
    billed_now_amount: dict[int, float] = {}
    this_invoice: dict[int, float] = {}  # this invoice's net amount on each PO line
    for li, po_line in zip(items, live.share_with(po, items, exclude_invoice_id, exported, matched), strict=True):
        m = LineMatch(li["line_number"], li["description"], billed_quantity(li), float(li["unit_price"]))
        m.amount = float(li["amount"] or 0)
        m.matched_quantity = matched_quantity(li)
        result.lines.append(m)
        if not isinstance(po_line, int):
            _off_po(m, po_line, by_line, result)
            continue
        pl = by_line[po_line]
        _from_po_line(m, li, pl, cost_centers, received, subcontract)
        m.billed_before = billed_before.get(pl["line_number"], 0.0)
        m.billed_before_amount = billed_before_amount.get(pl["line_number"], 0.0)
        if m.amount_only and m.po_open_amount is not None:  # against what the ERP has open (D-280)
            m.billed_before_amount = not_exported.get(m.po_line, 0.0)
        this_invoice[pl["line_number"]] = this_invoice.get(pl["line_number"], 0.0) + m.amount
        if m.amount_only:
            _amount_findings(m, li, result, billed_now_amount, counted=not (exported and m.po_open_amount is not None))
        else:
            _line_findings(m, li, result, billed_now)
    if not result.currencies:
        _left_to_expense(result, po, billed_before_amount, not_exported, this_invoice, exported)

    subtotal = float(coding.subtotal or 0)
    total = float(po.get("total") or 0)
    before, said, limit = result.billed_before, "on earlier invoices", f"the PO total {_money(total)}"
    if po_lines and all(pl.get("amount_only") and pl["open_amount"] is not None for pl in po_lines):  # (D-280)
        total = round(sum(pl["open_amount"] for pl in po_lines), 2)
        before, said, limit = (sum(float(o["coding"].get("subtotal") or 0) for o in waiting),
                               "on invoices not exported yet", f"the {_money(total)} open on the PO")  # fmt: skip
        subtotal = 0.0 if exported else subtotal
    if subtotal > 0 and total > 0 and not result.currencies:
        billed = before + subtotal
        if billed > total * (1 + TOTAL_TOLERANCE) + 0.01:
            earlier = f" ({_money(before)} {said})" if before else ""
            result.findings.append(
                (WARNING, "PO_OVER_BILLED",
                 f"invoices on {po_label(number)} add up to {_money(billed)}{earlier}, more than {limit}", None)
            )  # fmt: skip
    if not any(f[0] in (ERROR, WARNING) for f in result.findings):
        matched = sum(1 for m in result.lines if m.po_line is not None)
        three_way = any(m.received is not None for m in result.lines)
        result.findings.append(
            (INFO, "PO_MATCHED",
             f"matches {po_label(number)}: {matched} line(s) within price and quantity"
             + (" ordered and received" if three_way else " ordered"), None)
        )  # fmt: skip
    return result


def _off_po(m: LineMatch, alike: Alike | None, by_line: dict[int, dict[str, Any]], result: PoMatch) -> None:
    """An invoice line billing no PO line: it matches none (PO_LINE_NOT_ON_PO), or could bill several alike and none is
    told (``alike``: PO_LINE_AMBIGUOUS, the lines named, D-282)."""
    label = po_label(result.po_number)
    if alike is None:
        m.problems.append("not_on_po")
        result.findings.append(
            (WARNING, "PO_LINE_NOT_ON_PO", f"no line on {label} matches “{_short(m.description)}”", m.invoice_line)
        )
        return
    lines = [by_line[n] for n in alike.lines]
    m.problems.append("alike")
    m.alike = tuple(pl.get("erp_line") or str(pl["line_number"]) for pl in lines)
    named = [f"line {name}" + (f" ({account})" if (account := join_account(pl.get("cost_center"), pl.get("gl_code")))
                               else "") for name, pl in zip(m.alike, lines, strict=True)]  # fmt: skip
    room = "each has room for it" if alike.room else "none has room left for it"
    by_hand = ", or match it in JD Edwards by hand" if (result.po or {}).get("order_type") else ""
    result.findings.append(
        (WARNING, "PO_LINE_AMBIGUOUS",
         f"line {m.invoice_line} “{_short(m.description)}” could bill {label} {', '.join(named[:-1])} or {named[-1]}: "
         f"they have the same description and price, and {room}, so none is guessed. Code the line to the business "
         f"unit or account of the one it bills{by_hand}", m.invoice_line)
    )  # fmt: skip


def _from_po_line(m: LineMatch, li: dict[str, Any], pl: dict[str, Any], cost_centers: bool, received: bool,
                  subcontract: bool) -> None:  # fmt: skip
    """``m``, the match of invoice line ``li``, filled from the PO line it bills: what was ordered (and, 3-way,
    ``received``), its coding (its cost center only with a cost center list, B-534), and what JD Edwards' voucher match
    names of it (D-264; a ``subcontract``'s retainage, D-273); the quantity it matches."""
    m.po_line, m.po_description = pl["line_number"], pl["description"]
    m.po_quantity, m.po_unit_price = pl["quantity"], pl["unit_price"]
    m.received = pl["received_quantity"] if received else None
    m.po_amount, m.amount_only = pl["amount"], bool(pl.get("amount_only"))
    m.po_gl, m.po_cc = pl.get("gl_code") or "", (pl.get("cost_center") or "") if cost_centers else ""
    m.po_erp_line, m.po_uom, m.po_open_quantity = pl.get("erp_line") or "", pl.get("uom") or "", pl.get("open_quantity")
    m.po_account = join_account(pl.get("cost_center"), pl.get("gl_code"))
    m.po_tax = (pl.get("tax_flag") or "", pl.get("tax_code") or "", pl.get("tax_area") or "")
    m.po_subledger = (pl.get("subledger") or "", pl.get("subledger_type") or "")
    m.po_retainage, m.po_open_amount = pl.get("retainage"), pl.get("open_amount")
    if m.amount_only:  # a PO line with no quantity (JD Edwards' order details give amounts only): matched by amount
        # (a subcontract's lump sum by its amount alone, D-273)
        m.matched_quantity = None if subcontract else billed_quantity(li) or None
    elif not billed_quantity(li) and not is_charge(m.description) and pl["unit_price"]:  # (none printed, D-283)
        m.matched_quantity, m.quantity_from = unprinted_quantity(m.amount, pl["unit_price"])


def _receipts_checked(store: Store) -> bool:
    """Whether what a PO line received is checked (3-way): ``jde_settings.receipts_checked`` (D-281)."""
    from ..erp.jde_settings import receipts_checked

    return receipts_checked(store)


def _left_to_expense(
    result: PoMatch,
    po: dict[str, Any],
    billed_before: dict[int, float],
    not_exported: dict[int, float],
    this_invoice: dict[int, float],
    exported: bool,
) -> None:
    """``result.left``: each PO line's ``LineLeft``, from what the other invoices bill on it (``billed_before``, in
    amounts) and this one; with an open amount, the invoices exported to the ERP don't count (``not_exported``: what
    the others not exported yet bill on it; ``exported``: this one is)."""
    for pl in po["lines"]:
        n, opened = pl["line_number"], pl["open_amount"]
        if opened is None:
            result.left[n] = LineLeft(n, pl["amount"], None, billed_before.get(n, 0.0), this_invoice.get(n, 0.0))
        else:
            now = 0.0 if exported else this_invoice.get(n, 0.0)
            result.left[n] = LineLeft(n, pl["amount"], opened, not_exported.get(n, 0.0), now)


def _currencies(coding: InvoiceCoding, po: dict[str, Any], number: str) -> str:
    """Why the invoice's amounts can't be compared with the PO's: the two in other currencies (a PO whose file gave
    none is taken as CAD, and said so); "" when they are in one."""
    invoice = (coding.currency or "").strip().upper() or "CAD"
    ordered = po.get("currency") or ""
    if (ordered or "CAD") == invoice:
        return ""
    if ordered:
        return f"{po_label(number)} is in {ordered}, the invoice in {invoice}"
    return (f"{po_label(number)} has no currency in the purchase-order file, so it is taken as CAD; the invoice is in "
            f"{invoice}")  # fmt: skip


def _short(text: str, n: int = 50) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _header_findings(coding: InvoiceCoding, po: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    number = po["po_number"]
    if po.get("status") == CLOSED:
        findings.append(
            (WARNING, "PO_CLOSED", f"{po_label(number)} is closed: confirm it should still be billed", None)
        )
    po_vendor = po.get("vendor_key") or ""
    if po_vendor and vendor_key(coding.vendor_name) != po_vendor:
        if similarity(coding.vendor_name, po.get("vendor_name") or "") < 0.6:
            findings.append(
                (WARNING, "PO_VENDOR_MISMATCH",
                 f"{po_label(number)} was raised for {po.get('vendor_name')}, not {coding.vendor_name}", None)
            )  # fmt: skip
    # The invoice's company picked, or decided before the POs were loaded, another than its order's (B-2686)
    theirs, mine = company_code(po.get("order_company")), company_code(coding.company)
    if theirs and mine and theirs != mine:
        findings.append((WARNING, "PO_COMPANY_DIFFERS", f"{po_label(number)} is an order of company {theirs}, not "
                         f"of {mine}, the company this invoice is for: check the company, or the PO number",
                         None))  # fmt: skip
    return findings


def _amount_findings(m: LineMatch, li: dict[str, Any], result: PoMatch, billed_now: dict[int, float],
                     counted: bool = True) -> None:  # fmt: skip
    """A PO line with only an amount (services, a lump sum): what has been billed against the amount, or against what
    the ERP has open on it (``LineMatch.po_open_amount``, D-280); ``counted``: this invoice's amount counts (not when
    the ERP's open amount has it, exported)."""
    assert m.po_line is not None and m.po_amount is not None
    billed_now[m.po_line] = billed_now.get(m.po_line, 0.0) + (m.amount if counted else 0.0)
    billed = m.billed_before_amount + billed_now[m.po_line]
    limit, held, said = (m.po_amount, "ordered", "on earlier invoices") if m.po_open_amount is None else (
        m.po_open_amount, "open", "on invoices not exported yet")  # fmt: skip
    if m.amount > 0 and not result.currencies and billed > limit * (1 + TOTAL_TOLERANCE) + 0.01:
        earlier = f", {_money(m.billed_before_amount)} {said}" if m.billed_before_amount else ""
        m.problems.append("quantity")
        result.findings.append(
            (WARNING, "PO_QTY_OVER",
             f"{_money(billed)} billed on {po_label(result.po_number)} line {m.po_line}, more than the "
             f"{_money(limit)} {held}{earlier}", m.invoice_line)
        )  # fmt: skip
    _coding_findings(m, li, result)


def _line_findings(m: LineMatch, li: dict[str, Any], result: PoMatch, billed_now: dict[int, float]) -> None:
    number = result.po_number
    assert m.po_line is not None and m.po_unit_price is not None and m.po_quantity is not None
    priced = not result.currencies  # prices compared only in one currency (B-2612)
    if (priced and m.quantity > 0 and abs(m.unit_price) > abs(m.po_unit_price)
            and not _same_price(m.unit_price, m.po_unit_price)):  # fmt: skip
        over = (abs(m.unit_price) / abs(m.po_unit_price) - 1) if m.po_unit_price else 1.0
        m.problems.append("price")
        result.findings.append(
            (WARNING, "PO_PRICE_OVER",
             f"unit price {_money(m.unit_price)} is above the PO price {_money(m.po_unit_price)} (+{over:.1%})",
             m.invoice_line)
        )  # fmt: skip
    elif priced and (extra := _amount_over(m)) is not None:  # the unit price within, the line's amount not (B-927)
        m.problems.append("price")
        at_po = round(m.quantity * abs(m.po_unit_price), 2)
        result.findings.append(
            (WARNING, "PO_PRICE_OVER",
             f"line amount {_money(m.amount)} is {_money(extra)} above {_qty(m.quantity)} at the PO price "
             f"{_money(m.po_unit_price)} ({_money(at_po)})", m.invoice_line)
        )  # fmt: skip
    billed_now[m.po_line] = billed_now.get(m.po_line, 0.0) + m.quantity
    billed = m.billed_before + billed_now[m.po_line]
    if m.quantity > 0 and billed > m.po_quantity + 1e-6:
        earlier = f", {_qty(m.billed_before)} on earlier invoices" if m.billed_before else ""
        m.problems.append("quantity")
        result.findings.append(
            (WARNING, "PO_QTY_OVER",
             f"{_qty(billed)} billed on {po_label(number)} line {m.po_line} but {_qty(m.po_quantity)} ordered{earlier}",
             m.invoice_line)
        )  # fmt: skip
    elif m.quantity > 0 and m.received is not None and billed > m.received + 1e-6:
        m.problems.append("received")
        result.findings.append(
            (WARNING, "PO_NOT_RECEIVED",
             f"{_qty(billed)} billed but only {_qty(m.received)} received on {po_label(number)} line {m.po_line}",
             m.invoice_line)
        )  # fmt: skip
    _coding_findings(m, li, result)


def _coding_findings(m: LineMatch, li: dict[str, Any], result: PoMatch) -> None:
    number = result.po_number
    gl, cc = li.get("predicted_gl_code") or "", li.get("predicted_cost_center") or ""
    if (m.po_gl and m.po_gl != gl) or (m.po_cc and m.po_cc != cc):
        m.problems.append("coding")
        target = " · ".join(x for x in (f"GL {m.po_gl}" if m.po_gl else "", m.po_cc) if x)
        result.findings.append(
            (INFO, "PO_CODING_DIFFERS", f"{po_label(number)} line {m.po_line} is coded to {target}", m.invoice_line)
        )


def po_findings(coding: InvoiceCoding, store: Store, exclude_invoice_id: int | None = None,
                match: PoMatch | None = None) -> list[Finding]:  # fmt: skip
    """The checks for ``validate_coding`` (empty when no purchase orders are loaded); ``match``: the invoice's
    ``match_invoice``, made already."""
    if not store.has_purchase_orders():
        return []
    if not (coding.po_number or "").strip():
        open_pos = store.open_pos_for_vendor(vendor_key(coding.vendor_name))
        if open_pos:
            listed = ", ".join(po_label(n) for n in open_pos[:3]) + ("…" if len(open_pos) > 3 else "")
            return [(INFO, "PO_NOT_QUOTED", f"no PO number on the invoice; this vendor has open {listed}", None)]
        return []
    result = match if match is not None else match_invoice(coding, store, exclude_invoice_id)
    return result.findings if result else []


def billed_quantity(li: dict[str, Any]) -> float:
    """The quantity a line bills: negative for a credit, also when the credit note prints a positive
    quantity with a negative price or amount."""
    quantity, amount = float(li.get("quantity") or 0), float(li.get("amount") or 0)
    return -abs(quantity) if amount < 0 else quantity


def matched_quantity(li: dict[str, Any]) -> float:
    """The quantity a line matches on its PO line (JD Edwards' Quantity Matched): the quantity billed; a charge
    (freight) or a line with no quantity 1, -1 on a credit (a goods line with none, on a PO line with a unit cost: its
    amount at that cost, ``unprinted_quantity``, D-283)."""
    quantity = billed_quantity(li)
    if quantity and not is_charge(str(li.get("description") or "")):
        return quantity
    return -1.0 if float(li.get("amount") or 0) < 0 else 1.0


def unprinted_quantity(amount: float, unit_cost: float) -> tuple[float, str]:
    """(the quantity a goods line printed with none matches on a PO line of ``unit_cost``, how: ``LineMatch.
    quantity_from``): its amount at that cost when it comes out a whole number, the cost within its tolerance
    (``_same_price``; not rounded to the cent first: a cost of a fraction of a cent, 0.045, has less tolerance than
    that, B-2910); else 1 (-1 on a credit), as before (D-283)."""
    sign = -1.0 if amount < 0 else 1.0
    count = math.floor(abs(amount / unit_cost) + 0.5) if unit_cost else 0
    if count >= 1 and _same_price(abs(amount) / count, unit_cost):
        return sign * count, "amount"
    return sign, "one"


@dataclass(frozen=True)
class MatchRow:
    """One row of JD Edwards' voucher match (P0411, Voucher Match): an invoice line and the open PO line it bills."""

    invoice_line: int
    description: str  # the invoice line's
    order_number: str
    order_type: str  # "": the PO file gave none
    order_company: str  # "": the PO file gave none
    line: str  # the PO line's, as JD Edwards shows it: "12.000"
    quantity: float | None  # quantity matched; None: the PO line has none and the invoice bills none
    uom: str
    amount: float  # amount matched: the line's, before tax
    account: str  # the PO line's: business unit.object.subsidiary ("" when the PO file had none)
    open_quantity: float | None  # what JD Edwards had open on the line at the PO import (None: not in the file)
    no_quantity: bool = False  # the PO line has no quantity in the PO file: matched by amount, its quantity to check
    tax: tuple[str, str, str] = ("", "", "")  # the PO line's own Tax Y/N, explanation code, rate/area ("": none)
    on_po: bool = True  # False: a charge the PO has no line for, keyed as a G/L line of its own on ``account`` (D-265)
    subledger: tuple[str, str] = ("", "")  # the PO line's subledger and its type ("": none, D-268)
    retainage: float | None = None  # a subcontract line's percent held back of what it bills (D-273)
    # The work billed on the line before (D-273): what JD Edwards had vouchered of it at the PO import (its amount
    # less its open amount) and AP Coder's other invoices not in JD Edwards yet; without an open amount, AP Coder's
    # other invoices on it.
    billed_before: float = 0.0
    quantity_from: str = ""  # a line printed with no quantity: how its quantity matched came (``LineMatch``, D-283)
    unit_cost: float | None = None  # the PO line's


@dataclass
class VoucherMatch:
    """An invoice keyed as a voucher match (D-264): a row for each of its lines, in their order; ``held``: why it can't
    be keyed so, in plain words (empty when it can). No PO line is ever guessed: a line that matches none holds it."""

    rows: list[MatchRow] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    # Of ``held``, what the PO match's own checks say already (a PO not in the list or of several orders, in another
    # currency, a line on no PO line): not said again before approval (``jde_review.match_findings``, B-2667)
    said: list[str] = field(default_factory=list)


def voucher_match(match: PoMatch | None, number: str, charge_account: str = "") -> VoucherMatch:
    """The voucher match of an invoice quoting PO ``number`` from its PO match (``match_invoice``; None: no purchase
    orders loaded). ``charge_account``: the account a charge the PO has no line for (freight, a fuel surcharge, an
    environmental fee: ``is_extra_charge``) is keyed to on a G/L line of its own (Settings → JD Edwards E1, D-265);
    "": such a charge holds the voucher, as any line on no PO line."""
    out = VoucherMatch()
    label = po_label(number)
    if match is not None and match.orders:  # its number several orders', none the invoice's (B-2664)
        out.held.append(f"{label} is {len(match.orders)} purchase orders ({orders_named(match.orders)}), none of them "
                        "told by the invoice's supplier and company: pick the invoice's company, or match it in JD "
                        "Edwards by hand")  # fmt: skip
        out.said = list(out.held)  # (PO_AMBIGUOUS)
        return out
    if match is None or not match.found or match.po is None:
        out.held.append(f"{label} is not in the purchase orders loaded: load its open lines from JD Edwards "
                        "(Purchase orders), then make the keying sheet again")  # fmt: skip
        out.said = list(out.held) if match is not None else []  # (PO_UNKNOWN; with no POs loaded, no PO check)
        return out
    if match.currencies:
        out.held.append(f"{match.currencies}: a voucher match is keyed in the order's currency, so match it in "
                        "JD Edwards by hand")  # fmt: skip
        out.said.append(out.held[-1])  # (PO_CURRENCY_DIFFERS)
    po = match.po
    for m in match.lines:
        if m.alike:  # (several PO lines alike, none told: D-282)
            out.held.append(f"line {m.invoice_line} “{_short(m.description)}” could bill {label} line "
                            f"{' or line '.join(m.alike)} (none is guessed): code the line to the business unit or "
                            "account of the one it bills, or match it in JD Edwards by hand")  # fmt: skip
            out.said.append(out.held[-1])  # (PO_LINE_AMBIGUOUS)
        elif m.po_line is None and charge_account and is_extra_charge(m.description):
            out.rows.append(MatchRow(m.invoice_line, m.description, "", "", "", "", None, "", m.amount, charge_account,
                                     None, on_po=False))  # fmt: skip
        elif m.po_line is None:
            out.held.append(
                f"line {m.invoice_line} “{_short(m.description)}” matches no line of {label} (none is "
                "guessed): correct the line or the PO number, or match it in JD Edwards by hand"
            )
            out.said.append(out.held[-1])  # (PO_LINE_NOT_ON_PO)
        elif not m.po_erp_line:
            out.held.append(f"{label} line {m.po_line} has no JD Edwards line number in the purchase orders loaded: "
                            "load the PO's open lines from JD Edwards again")  # fmt: skip
        else:
            before = (
                m.po_amount - m.po_open_amount + match.waiting.get(m.po_line, 0.0)
                if m.po_amount is not None and m.po_open_amount is not None
                else m.billed_before_amount
            )
            out.rows.append(MatchRow(
                m.invoice_line, m.description, str(po.get("po_number") or number), po.get("order_type") or "",
                po.get("order_company") or "", m.po_erp_line, m.matched_quantity, m.po_uom, m.amount, m.po_account,
                m.po_open_quantity, m.amount_only, m.po_tax, subledger=m.po_subledger, retainage=m.po_retainage,
                billed_before=round(before, 2), quantity_from=m.quantity_from, unit_cost=m.po_unit_price))  # fmt: skip
    return out


def kept_match(matched: VoucherMatch) -> dict[str, Any] | None:
    """What is kept with an invoice of its voucher match when it is approved (``schema.VOUCHER_MATCH``, D-284): its
    rows, when it has rows and nothing holds it; None otherwise (made again from the purchase orders loaded)."""
    if not matched.rows or matched.held:
        return None
    return {"rows": [asdict(row) for row in matched.rows]}


def kept_voucher_match(final: dict[str, Any]) -> VoucherMatch | None:
    """The voucher match kept with an approved invoice (``kept_match``; ``final``: its approved output), None when none
    is (approved before they were kept, or when it could not be made)."""
    kept = final.get(VOUCHER_MATCH)
    if not isinstance(kept, dict) or not isinstance(kept.get("rows"), list) or not kept["rows"]:
        return None
    names = {f.name for f in fields(MatchRow)}
    try:
        rows = [MatchRow(**{**{k: v for k, v in row.items() if k in names},
                            "tax": tuple(row.get("tax") or ("", "", "")),
                            "subledger": tuple(row.get("subledger") or ("", ""))}) for row in kept["rows"]]  # fmt: skip
    except (AttributeError, TypeError):  # (not rows as kept: made again)
        return None
    return VoucherMatch(rows=rows)


def billed_by_line(
    po: dict[str, Any], invoices: list[dict[str, Any]], positive_only: bool = False, amounts: bool = False,
    matched: dict[Any, tuple[int, ...]] | None = None,
) -> dict[int, float]:  # fmt: skip
    """Quantity (or with ``amounts``, net amount) billed so far on each PO line by ``invoices``
    (``Store.po_invoices`` rows), each line on the PO line ``po_lines_billed`` pairs it with; credits subtract unless
    ``positive_only``. ``matched``: each line's PO lines kept (``po_line_choices``), to give again to another call for
    the same PO. With lines alike on a PO its billing shares them out instead (``PoBilling.shares``, D-282)."""
    billed: dict[int, float] = {}
    matched = {} if matched is None else matched
    for inv in invoices:
        lines = inv["coding"].get("line_items") or []
        if positive_only:
            lines = [li for li in lines if billed_quantity(li) > 0]
        for li, po_line in zip(lines, po_lines_billed(lines, po["lines"], matched), strict=True):
            if po_line is not None:
                value = float(li.get("amount") or 0) if amounts else billed_quantity(li)
                billed[po_line] = billed.get(po_line, 0.0) + value
    return billed


class PoBilling:
    """A PO's invoices in review or approved (``rows``: ``Store.po_invoices``' rows, in order) and, once kept
    (``po_billing``), what each bills on the PO's lines (``_billed``): kept rows are every caller's, read them, don't
    change them. A PO read at each ask has instead its lines' matches kept (``_matched``: (where it is kept, the PO's
    lines as matched, this ask's own copy of them)). ``alike``: whether a line of theirs could bill several PO lines
    alike (kept: as worked out then; else not known yet), then shared out among them all in order (``shares``)."""

    def __init__(self, rows: list[dict[str, Any]], billed: dict[int, Any] | None = None,
                 matched: tuple[Any, Any, dict[Any, tuple[int, ...]]] | None = None,
                 alike: bool = True) -> None:  # fmt: skip
        self.rows, self._billed, self._matched, self._alike = rows, billed, matched, alike
        self._shares: dict[int, list[int | Alike | None]] | None = None

    def billed(self, po: dict[str, Any], invoices: list[dict[str, Any]], amounts: bool = False,
               matched: dict[Any, tuple[int, ...]] | None = None) -> dict[int, float]:  # fmt: skip
        """``billed_by_line(po, invoices, amounts=amounts, matched=matched)`` for ``invoices``, some of ``rows``: from
        what each bills as kept, added up in the same order (the same sums to the last digit); as before when not kept,
        or when one's lines could not be worked out so (it fails where it did). With lines alike on the PO, each line
        where it was shared out among all of ``rows`` (``shares``), whichever of them are added up."""
        shares = self.shares(po, matched)
        if shares:
            return _added(invoices, shares, amounts)
        kept = self._billed
        if self._matched is not None:  # (each line matched at most once in the process while the PO's lines stay)
            known = self._matched[2]
            before = len(known)
            try:
                return billed_by_line(po, invoices, amounts=amounts, matched=known)
            finally:
                self._kept_again(before)
        if kept is None or any(kept[o["id"]] is None for o in invoices):
            return billed_by_line(po, invoices, amounts=amounts, matched=matched)
        billed: dict[int, float] = {}
        get = billed.get
        for o in invoices:
            for po_line, quantity, amount in kept[o["id"]]:
                po_line = po_line[0] if type(po_line) is tuple else po_line  # (the first alike, as billed_by_line)
                billed[po_line] = get(po_line, 0.0) + (amount if amounts else quantity)
        return billed

    def _kept_again(self, before: int) -> None:
        """This ask's own copy of the PO's lines' matches (``_matched``), kept again and measured once it grew from
        ``before``."""
        if self._matched is not None and len(self._matched[2]) != before:
            where, lines, known = self._matched
            with _KEPT_LOCK:
                _MATCHED[where] = (lines, known)

    def shares(self, po: dict[str, Any], matched: dict[Any, tuple[int, ...]] | None = None,
               ) -> dict[int, list[int | Alike | None]]:  # fmt: skip
        """Each of ``rows``' lines' PO line by invoice id, shared out among them all in order (``share_out``), when a
        line of theirs could bill several PO lines alike (D-282); empty when none could (each bills the one it most
        resembles: ``billed`` adds them up as before), or when one's lines can't be read so (worked out as before,
        failing where it did)."""
        if self._shares is None:
            self._shares = {}
            if self._alike:
                known = self._matched[2] if self._matched is not None else {} if matched is None else matched
                before = len(known)
                try:
                    lines_of = [(row["coding"].get("line_items") or [], row["export_batch"] is not None)
                                for row in self.rows]  # fmt: skip
                    choices = [po_line_choices(lines, po["lines"], known) for lines, _ in lines_of]
                except (AttributeError, TypeError, ValueError):
                    return self._shares
                finally:
                    self._kept_again(before)
                if any(len(found) > 1 for mine in choices for found in mine):
                    shared = _shared(po["lines"], lines_of, choices)
                    self._shares = {row["id"]: mine for row, mine in zip(self.rows, shared, strict=True)}
        return self._shares

    def share_with(self, po: dict[str, Any], lines: list[dict[str, Any]], invoice_id: int | None, exported: bool,
                   matched: dict[Any, tuple[int, ...]] | None = None) -> list[int | Alike | None]:  # fmt: skip
        """Each of ``lines``' PO line (an invoice's: ``invoice_id``, None when not stored yet; ``exported``), shared out
        after the PO's invoices before it in order (``share_out``): all of ``rows`` but itself when it is none of them
        yet."""
        mine = po_line_choices(lines, po["lines"], matched)
        if all(len(found) <= 1 for found in mine):
            return [found[0] if found else None for found in mine]
        known = self._matched[2] if self._matched is not None else {} if matched is None else matched
        before = [(row["coding"].get("line_items") or [], row["export_batch"] is not None) for row in self.rows
                  if invoice_id is None or row["id"] < invoice_id]  # fmt: skip
        choices = [po_line_choices(earlier, po["lines"], known) for earlier, _ in before]
        return _shared(po["lines"], [*before, (lines, exported)], [*choices, mine])[-1]


def _added(invoices: list[dict[str, Any]], shares: dict[int, list[int | Alike | None]],
           amounts: bool) -> dict[int, float]:  # fmt: skip
    """What ``invoices`` bill on each PO line, each of their lines where it was shared out (``PoBilling.shares``)."""
    billed: dict[int, float] = {}
    for o in invoices:
        for li, po_line in zip(o["coding"].get("line_items") or [], shares[o["id"]], strict=True):
            if isinstance(po_line, int):
                billed[po_line] = billed.get(po_line, 0.0) + (float(li.get("amount") or 0) if amounts
                                                              else billed_quantity(li))  # fmt: skip
    return billed


def _billed(row: dict[str, Any], po_lines: list[dict[str, Any]], matched: dict[Any, tuple[int, ...]]) -> Any:
    """What an invoice (``Store.po_invoices``' row) bills on the PO lines its lines match, its lines in order: (the PO
    line, the quantity, the amount) of each, as ``billed_by_line`` adds them up, the PO line the lines alike (a tuple)
    of one that could bill several (``PoBilling.shares`` then shares them out); None when they can't be worked out so
    (a coding not an object, lines not lines, a quantity not a number): ``PoBilling.billed`` then works out the
    invoices with it as before, failing where it did."""
    try:
        lines = row["coding"].get("line_items") or []
        return tuple((found[0] if len(found) == 1 else found, billed_quantity(li), float(li.get("amount") or 0))
                     for li, found in zip(lines, po_line_choices(lines, po_lines, matched), strict=True)
                     if found)  # fmt: skip
    except Exception:  # (whatever it was, it is met again where it was met: in billed_by_line)
        return None


@dataclass(frozen=True)
class _KeptPo:
    """A PO's invoices kept in this process (``po_billing``): the stamp they were read with
    (``Store.po_invoices_since``), the PO's lines they were matched to (``_matched_to``), every invoice quoting the
    PO in order with its vendor as read (``vendors``), those in review or approved (``rows``: ``Store.po_invoices``'
    rows, in order), what each of them bills by id (``_billed``), the texts of their lines (``_share_texts``), and the
    bytes it holds."""

    stamp: tuple[Any, int, dict[str, int]]
    lines: tuple[tuple[Any, Any, Any], ...]
    vendors: dict[int, Any]
    rows: list[dict[str, Any]]
    billed: dict[int, Any]
    texts: dict[str, str] = field(default_factory=dict)
    size: int = 0
    alike: bool = False  # a line of theirs could bill several PO lines alike (``_billed``'s tuple): shared out


def _kept_size(kept: Any) -> int:
    """A kept PO's bytes, measured once its invoices changed (not again when only its stamp moved, at nearly every
    ask under load)."""
    return kept.size if isinstance(kept, _KeptPo) else size_of(kept)


# (database, PO key) -> _KeptPo, within PO_KEPT_MOST bytes (D-195), the ones asked longest ago let go first.
_KEPT = Bounded("POs' invoices", PO_KEPT_MOST, size=_kept_size)
# (database, PO key) -> (the PO's lines as matched, ``po_lines_billed``'s ``matched``) of a PO read at each ask (fewer
# than PO_KEPT_FROM invoices), within MATCHED_MOST bytes: its invoices' lines are matched once in the process while
# its lines stay, not at each ask, however their descriptions vary (the pairs' cache, ``similarity``, keeps 8,192).
_MATCHED = Bounded("POs' matched lines", MATCHED_MOST)
_KEPT_LOCK = threading.Lock()


def _share_texts(rows: Iterable[dict[str, Any] | None], texts: dict[str, str]) -> None:
    """The texts of these rows' lines (``Store.po_invoices``' rows, just read, kept by no one yet), their keys and
    values, made one object for each text, those of ``texts`` (the kept rows' texts, added to): a standing PO's lines
    repeat their descriptions, codes and taxes (3,414 invoices of 8 lines: 21 MB, 31 MB without), also once its
    invoices are read again one at a time as they change. The same texts: what reads them is the same."""

    def shared(value: Any) -> Any:
        if type(value) is str:
            return texts.setdefault(value, value)
        return [shared(x) if type(x) is str else x for x in value] if type(value) is list else value

    for row in rows:
        coding = row["coding"] if row is not None else None
        lines = coding.get("line_items") if isinstance(coding, dict) else None
        for n, li in enumerate(lines if isinstance(lines, list) else ()):
            if type(li) is not dict:
                continue
            if all(texts.setdefault(k, k) is k for k in li):  # (its keys those kept: read with them, in one decode)
                for k, v in li.items():
                    if type(v) is str or type(v) is list:
                        li[k] = shared(v)
            else:
                lines[n] = {shared(k): shared(v) for k, v in li.items()}  # type: ignore[index]


def _matched_to(po: dict[str, Any]) -> tuple[tuple[Any, Any, Any, Any], ...]:
    """What matching an invoice's lines to ``po``'s reads of each of its lines (``_po_line``; ``_retains``), in
    order."""
    return tuple((pl.get("description", ""), pl.get("unit_price"), pl.get("line_number"), pl.get("retainage"))
                 for pl in po["lines"])  # fmt: skip


def po_billing(store: Store, key: str, po: dict[str, Any]) -> PoBilling:
    """The invoices quoting the PO ``key`` (``po``: its ``Store.purchase_order``) in review or approved, as
    ``Store.po_invoices`` reads them, and what they bill on its lines (``PoBilling``). Kept in this process for a PO of
    PO_KEPT_FROM invoices or more (D-202), by database and PO, and brought up to date from the invoices changed since,
    whoever changed them (``Store.po_invoices_since``): only those are read and matched again, and all of them are
    matched again once the PO's lines are not the ones they were matched to. Inside a write under way they are read
    afresh and nothing is kept or taken (it may yet be undone, D-159); a PO of fewer invoices is read at each ask."""
    lines = _matched_to(po)
    where = (str(store.path), key)
    with _KEPT_LOCK:
        kept = _KEPT.get(where)
    changes = store.po_invoices_since(key, kept.stamp if kept else None, kept.vendors if kept else None)
    if changes is None:
        return PoBilling(store.po_invoices(key))
    read = changes.read
    if kept is not None and not read and lines == kept.lines and changes.quoting == list(kept.vendors):
        if changes.stamp != kept.stamp:  # (only other invoices changed since: these kept as of now)
            with _KEPT_LOCK:
                _KEPT[where] = replace(kept, stamp=changes.stamp)
        return PoBilling(kept.rows, kept.billed, alike=kept.alike)
    rows_of = {row["id"]: row for row in kept.rows} if kept is not None else {}
    vendors: dict[int, Any] = {}
    rows: list[dict[str, Any]] = []
    for n in changes.quoting:
        vendors[n], row = read[n] if n in read else (kept.vendors[n], rows_of.get(n))  # type: ignore[union-attr]
        if row is not None:
            rows.append(row)
    if len(changes.quoting) < PO_KEPT_FROM:
        with _KEPT_LOCK:
            _KEPT.pop(where, None)
            known = _MATCHED.get(where)
        # (a copy of its own: a kept one is never changed, another thread may be copying or measuring it)
        return PoBilling(rows, matched=(where, lines, dict(known[1]) if known and known[0] == lines else {}))
    # (a copy: a kept PO is never changed, another thread may be reading or measuring it; every invoice read: anew)
    texts = dict(kept.texts) if kept is not None and len(read) < len(changes.quoting) else {}
    _share_texts((row for _, row in read.values()), texts)
    same = kept is not None and lines == kept.lines
    matched: dict[Any, tuple[int, ...]] = {}
    billed = {row["id"]: kept.billed[row["id"]] if same and row["id"] not in read  # type: ignore[union-attr]
              else _billed(row, po["lines"], matched) for row in rows}  # fmt: skip
    alike = any(type(e[0]) is tuple for kept_lines in billed.values() if kept_lines for e in kept_lines)
    entry = _KeptPo(changes.stamp, lines, vendors, rows, billed, texts, alike=alike)
    entry = replace(entry, size=size_of(entry))
    with _KEPT_LOCK:
        _KEPT[where] = entry
    return PoBilling(rows, billed, alike=alike)


def positive_lines(invoices: Iterable[dict[str, Any]]) -> list[tuple[Any, float, float]]:
    """(description, unit price, quantity) of each line of ``invoices`` (``Store.po_invoices`` rows) that bills a
    positive quantity, in order: what ``billed_by_line(po, invoices, positive_only=True)`` matches to a PO's lines,
    read once for all the POs of a vendor (``billed_on``)."""
    out = []
    for inv in invoices:
        for li in inv["coding"].get("line_items") or []:
            quantity = billed_quantity(li)
            if quantity > 0:
                out.append((li.get("description", ""), float(li.get("unit_price") or 0), quantity))
    return out


def billed_on(po: dict[str, Any], lines: Iterable[tuple[Any, float, float]]) -> dict[int, float]:
    """``billed_by_line(po, invoices, positive_only=True)`` from the ``positive_lines`` of ``invoices``: each
    description and price matched to the PO's lines once, not once a line (month-end matches every invoice of a
    vendor to each of its POs): what decides is the description and which of the PO lines whose price may change what
    it matches (``_price_decides``: most often none) have the same price."""
    billed: dict[int, float] = {}
    bands = [_price_band(float(pl.get("unit_price") or 0)) for pl in po["lines"]]
    matched: dict[tuple[Any, tuple[int, ...]], int | None] = {}
    deciding: dict[Any, tuple[tuple[int | None] | None, tuple[int, ...]]] = {}  # by description: ``_price_decides``
    for description, price, quantity in lines:
        if description not in deciding:
            deciding[description] = _price_decides(description, po["lines"])
        found, by_price = deciding[description]
        if found is not None:
            po_line = found[0]
        else:
            size = abs(price)
            same = tuple(n for n in by_price if abs(size - bands[n][0]) <= bands[n][1])
            key = (description, same)
            if key not in matched:
                matched[key] = _po_line(description, po["lines"], lambda n, _, same=same: n in same)
            po_line = matched[key]
        if po_line is not None:
            billed[po_line] = billed.get(po_line, 0.0) + quantity
    return billed


def _price_decides(
    description: Any, po_lines: list[dict[str, Any]]
) -> tuple[tuple[int | None] | None, tuple[int, ...]]:
    """((what ``_po_line`` gives a line of ``description`` whatever its price,) or None; the positions of the PO lines
    whose price, when it is the line's, may change that): those that resemble it, at the same price, at least as much
    as the most alike does at any price, and enough to match. No other line's price can: that line is never the most
    alike, or never enough. With none of them it matches nothing; with only the most alike, itself enough, that one."""
    alike = [_alike(description, pl.get("description", "")) for pl in po_lines]
    at_same = [score + SAME_PRICE for score in alike]  # (as ``_po_line`` adds it)
    floor = max([MATCH_THRESHOLD, *alike])
    by_price = tuple(n for n, score in enumerate(at_same) if score >= floor)
    if not by_price:
        return (None,), ()
    if len(by_price) == 1 and alike[by_price[0]] >= MATCH_THRESHOLD:
        return (int(po_lines[by_price[0]]["line_number"]),), ()
    return None, by_price


def template_csv() -> bytes:
    example = [
        "PO-1001", "Example Supplier Ltd.", "1", "Example item or service", "10", "25.00", "250.00", "", "10",
        "6000", "", "open", "CAD",
    ]  # fmt: skip
    return ("\ufeff" + ",".join(TEMPLATE_COLUMNS) + "\r\n" + ",".join(example) + "\r\n").encode("utf-8")
